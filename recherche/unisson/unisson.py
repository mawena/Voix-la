#!/usr/bin/env python3
"""Recherche « unisson » : séparer deux chanteurs qui chantent ensemble, pour donner à chacun sa voix convertie.

  unisson.py preparer [--video FICHIER --debut S --duree S]
                     prises chantées libres (VocalSet, CC BY 4.0, lues par requêtes HTTP Range dans l'archive Zenodo,
                     sans la télécharger en entier), fenêtres de 10 s, plan des duos synthétiques ; vrai duo facultatif
                     (une partie de la vidéo, lue par ffmpeg seulement)
  unisson.py envoyer                jeu privé <compte>/voixla-recherche-unisson
  unisson.py lancer S [--limite MIN]
                     noyau GPU privé <compte>/voixla-unisson-gpu : verrou Kaggle de doubler, quota vérifié avant
                     (refus s'il resterait moins de 4 h), suivi, rapatriement dans resultats/S/
  unisson.py rapatrier S            rapatrie à nouveau la sortie du noyau
  unisson.py tableau S              tableau des mesures (Markdown)
  unisson.py ecoute S [--ecoute D]  extraits d'écoute à l'aveugle (format de interface/voixla/ecoute.py) :
                                    <D>/unisson/ (pistes séparées) et <D>/unisson-double/ (extrait doublé)

Dossier de travail (entrees/, jeu/, resultats/, travail/) : $UNISSON_DONNEES, sinon banc/travail/unisson (ignoré par
git). Compte Kaggle : « kaggle.compte » de ~/.config/voix-la/config.json, comme doubler et le banc. Aucune voix de
l'utilisateur ne sert ici : seulement VocalSet (Wilkins et al., ISMIR 2018, CC BY 4.0) et, pour l'écoute, un vrai duo
local qui ne quitte que pour le jeu privé. Rien n'est publié : jeu et noyau privés, sons hors du dépôt.
Python du PC : numpy et soundfile (par exemple l'environnement d'audio-separator), ffmpeg et ffprobe.
"""
import argparse
import base64
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

CODE = Path(__file__).resolve().parent
DEPOT = CODE.parent.parent
sys.path.insert(0, str(DEPOT / 'banc'))
import banc  # noqa: E402  (verrou Kaggle, appels kaggle, jeu privé, mix d'écoute : ceux du banc d'essai)

DONNEES = Path(os.environ['UNISSON_DONNEES']).expanduser() if os.environ.get('UNISSON_DONNEES') else DEPOT / 'banc' / 'travail' / 'unisson'
ENTREES, JEU, RESULTATS, TRAVAIL = (DONNEES / d for d in ('entrees', 'jeu', 'resultats', 'travail'))
SR = 44100
NOM_KAGGLE = 'voixla-recherche-unisson'    # jeu de données privé
NOM_NOYAU = 'voixla-unisson-gpu'            # noyau privé (Kaggle refuse un noyau du même nom que le jeu : 409)
QUOTA_MINI_H = 4.0   # jamais de session qui laisserait moins de 4 h de quota GPU
NOYAU_DEPOT = DEPOT / 'kaggle' / 'doubler_kaggle.py'

# VocalSet 1.1 (Zenodo 1442513, CC BY 4.0) : 20 chanteurs et chanteuses, mêmes airs (« caro », « dona », « row »)
VOCALSET = 'https://zenodo.org/api/records/1442513/files/VocalSet11.zip/content'
CHANTEURS = {f'm{i}': 'homme' for i in (1, 2, 3, 6, 9, 11)} | {f'f{i}': 'femme' for i in (1, 2, 3, 5, 8, 9)}
PAIRES = [('m1', 'f1'), ('m3', 'f3'), ('m6', 'f5'), ('f2', 'f8'), ('m2', 'm9')]   # trois duos mixtes, deux de même genre
CIBLES_DEMO = {'homme': 'm11', 'femme': 'f9'}   # voix cibles de l'extrait doublé (hors des duos mesurés)
FENETRE_S = 10.0
CIBLE_S = 12.0

CANDIDATS_HF = ['hf_sucial', 'hf_aufr33']
CANDIDATS_PAROLE = ['medleyvox_238', 'sepformer_whamr16k', 'mossformer2_ss16k', 'sepformer_wsj02mix']
# Paquets des candidats de parole, installés dans un environnement à part sur Kaggle (une commande pip par liste).
# ClearVoice sans ses dépendances : il demande numpy < 2 et des versions figées de librosa et soundfile, qui casseraient
# les paquets compilés pour numpy 2 ; on ajoute à la main ce qu'il importe.
PAQUETS_PAROLE = [['speechbrain', 'asteroid', 'pyloudnorm'],
                  ['yamlargparse', 'pydub', 'python_speech_features', 'rotary_embedding_torch==0.8.3', 'torchinfo', 'gdown',
                   'scenedetect', 'joblib'],
                  ['--no-deps', 'clearvoice']]
LIBELLES = {
    'melange': 'mélange (aucune séparation)',
    'karaoke_becruily': 'Roformer karaoké (becruily, celui de doubler)',
    'karaoke_bs_frazer': 'BS-Roformer karaoké (frazer-becruily)',
    'hf_aufr33': 'BS-Roformer homme/femme (aufr33)',
    'hf_sucial': 'BS-Roformer chœur homme/femme (Sucial)',
    'sepformer_wsj02mix': 'SepFormer WSJ0-2mix (8 kHz)',
    'sepformer_whamr16k': 'SepFormer WHAMR! (16 kHz)',
    'mossformer2_ss16k': 'MossFormer2 SS (16 kHz)',
    'medleyvox_238': 'MedleyVox (Conv-TasNet, Cyru5, 24 kHz)',
    'masque_oracle': 'masque harmonique, hauteurs oracle',
    'masque_hf_aufr33': 'masque harmonique, hauteurs du Roformer aufr33',
    'masque_hf_sucial': 'masque harmonique, hauteurs du Roformer Sucial',
}
CONDITIONS = ['alternance', 'superposition', 'meme_air', 'unisson', 'octave', 'tierce', 'unisson_conv', 'octave_conv',
              'tierce_conv']


def dire(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


# --- Préparation (PC, léger) ------------------------------------------------------------------------------------------

class FichierHTTP(io.RawIOBase):
    """Fichier distant lu par requêtes HTTP Range : zipfile n'en lit que le sommaire et les membres demandés."""

    def __init__(self, url):
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=60) as r:
            self.url, self.taille = r.geturl(), int(r.headers['Content-Length'])
        self.pos = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.taille + off
        return self.pos

    def readinto(self, b):
        if self.pos >= self.taille or not len(b):
            return 0
        fin = min(self.pos + len(b), self.taille) - 1
        with urllib.request.urlopen(urllib.request.Request(self.url, headers={'Range': f'bytes={self.pos}-{fin}'}),
                                    timeout=300) as r:
            d = r.read()
        b[:len(d)] = d
        self.pos += len(d)
        return len(d)


def telecharger_vocalset(noms):
    """Les prises « straight » des airs demandés, {(chanteur, air): chemin}, lues dans l'archive distante."""
    dest = ENTREES / 'vocalset'
    dest.mkdir(parents=True, exist_ok=True)
    voulus = {(c, air): dest / f'{c}_{air}_straight.wav' for c, air in noms}
    manquants = {k: v for k, v in voulus.items() if not v.exists()}
    if manquants:
        dire(f'VocalSet : {len(manquants)} prises lues dans {VOCALSET} (requêtes Range)')
        z = zipfile.ZipFile(io.BufferedReader(FichierHTTP(VOCALSET), buffer_size=1 << 20))
        membres = {Path(i.filename).name: i.filename for i in z.infolist() if not i.filename.startswith('__MACOSX')}
        for (c, air), chemin in manquants.items():
            with z.open(membres[chemin.name]) as src:
                chemin.write_bytes(src.read())
    return voulus


def lire_mono(chemin):
    import numpy as np
    import soundfile as sf
    x, sr = sf.read(str(chemin), dtype='float32', always_2d=True)
    assert sr == SR, (chemin, sr)
    return np.ascontiguousarray(x.mean(1))


def meilleure_fenetre(x, duree_s):
    """Début (échantillons) de la fenêtre de duree_s la plus chantée (trames de 10 ms à moins de 30 dB du haut)."""
    import numpy as np
    n = int(duree_s * SR)
    if len(x) <= n:
        return 0
    t = len(x) // 441
    db = 10 * np.log10((x[:t * 441].reshape(t, 441).astype(np.float64) ** 2).mean(1) + 1e-12)
    act = (db > np.percentile(db, 95) - 30).astype(np.float64)
    L = n // 441
    c = np.concatenate([[0.0], np.cumsum(act)])
    return int(np.argmax(c[L:] - c[:-L])) * 441


def preparer(a):
    import numpy as np
    import soundfile as sf
    shutil.rmtree(JEU, ignore_errors=True)
    JEU.mkdir(parents=True)
    noms = [(c, air) for c in CHANTEURS for air in ('caro', 'dona', 'row')]
    prises = telecharger_vocalset(noms)
    fenetres = {}
    for (c, air), chemin in prises.items():
        x = lire_mono(chemin)
        cible = c in CIBLES_DEMO.values()
        if cible and air != 'dona':
            continue
        d = CIBLE_S if cible else FENETRE_S
        i = meilleure_fenetre(x, d)
        y = x[i:i + int(d * SR)]
        nom = f'cible-{CHANTEURS[c]}.wav' if cible else f'{c}-{air}.wav'
        sf.write(str(JEU / nom), y, SR, subtype='PCM_16')
        fenetres[nom] = {'prise': chemin.name, 'debut_s': round(i / SR, 2), 'duree_s': round(len(y) / SR, 2)}
    plan = {'source': 'VocalSet 1.1 (Zenodo 1442513, CC BY 4.0), prises « straight »', 'fenetres': fenetres,
            'conversions': [], 'melanges': [],
            'references': {c: f'{c}-row.wav' for c in CHANTEURS if c not in CIBLES_DEMO.values()},
            'cibles_demo': {g: f'cible-{g}.wav' for g in CIBLES_DEMO}}

    def src(c, air=None, conv=None, garder=None):
        s = {'chanteur': c, 'genre': CHANTEURS[c]}
        if conv:
            s['conversion'] = conv
        else:
            s['fichier'] = f'{c}-{air}.wav'
        if garder:
            s['garder'] = garder
        return s

    def conversion(source, vers, decalage, ident):
        if not any(c['id'] == ident for c in plan['conversions']):
            plan['conversions'].append({'id': ident, 'source': f'{source}-caro.wav', 'ref': f'{vers}-dona.wav',
                                        'decalage': decalage})
        return ident

    for A, B in PAIRES:
        paire = f'{A}+{B}'
        mixte = CHANTEURS[A] != CHANTEURS[B]
        m = plan['melanges']
        m.append({'id': f'{paire}-alternance', 'paire': paire, 'condition': 'alternance',
                  'sources': [src(A, 'caro', garder=[0, FENETRE_S / 2]), src(B, 'caro', garder=[FENETRE_S / 2, FENETRE_S])]})
        m.append({'id': f'{paire}-superposition', 'paire': paire, 'condition': 'superposition',
                  'sources': [src(A, 'caro'), src(B, 'dona')]})
        m.append({'id': f'{paire}-meme_air', 'paire': paire, 'condition': 'meme_air',
                  'sources': [src(A, 'caro'), src(B, 'caro')]})
        m.append({'id': f'{paire}-unisson', 'paire': paire, 'condition': 'unisson',
                  'sources': [src(A, 'caro'), src(B, conv=conversion(A, B, 0, f'{paire}-unisson'))]})
        if mixte:   # A homme, B femme : l'octave est chantée par la femme, et l'homme une octave dessous
            m.append({'id': f'{paire}-octave', 'paire': paire, 'condition': 'octave',
                      'sources': [src(B, 'caro'), src(A, conv=conversion(B, A, -12, f'{paire}-octave'))]})
        m.append({'id': f'{paire}-tierce', 'paire': paire, 'condition': 'tierce',
                  'sources': [src(A, 'caro'), src(B, conv=conversion(A, B, 4, f'{paire}-tierce'))]})
        # Les mêmes, voix 1 passée elle aussi par YingMusic-SVC (vers son propre timbre) : les deux voix ont alors la
        # même acoustique (sèches, même vocodeur), et seul le timbre et la hauteur les distinguent. Sans cela, une voix
        # réelle (prise dans une pièce) et une voix convertie (sèche) se séparent sur cet indice-là.
        soi_a = conversion(A, A, 0, f'{A}-soi')
        m.append({'id': f'{paire}-unisson_conv', 'paire': paire, 'condition': 'unisson_conv',
                  'sources': [src(A, conv=soi_a), src(B, conv=f'{paire}-unisson')]})
        if mixte:
            m.append({'id': f'{paire}-octave_conv', 'paire': paire, 'condition': 'octave_conv',
                      'sources': [src(B, conv=conversion(B, B, 0, f'{B}-soi')), src(A, conv=f'{paire}-octave')]})
        m.append({'id': f'{paire}-tierce_conv', 'paire': paire, 'condition': 'tierce_conv',
                  'sources': [src(A, conv=soi_a), src(B, conv=f'{paire}-tierce')]})
    if a.reel_extrait:   # extrait d'écoute d'une session précédente (voix principale sèche), pour les modèles de parole
        r = RESULTATS / a.reel_extrait / 'reel'
        d = json.loads((r / 'reel.json').read_text())
        shutil.copy2(r / 'lead_sec.flac', JEU / 'reel-lead_sec.flac')
        plan['reel_extrait'] = {'fichier': 'reel-lead_sec.flac', 'gain': d['gains_flac']['lead_sec']}
    if a.video:
        video = Path(a.video).expanduser()
        duree_video = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
                                            str(video)], capture_output=True, text=True, check=True).stdout.strip())
        debut = a.debut if a.debut is not None else max(0.0, duree_video - a.duree)
        tmp = TRAVAIL / 'duo-reel.wav'
        tmp.parent.mkdir(parents=True, exist_ok=True)
        banc.ff('-ss', f'{debut:.3f}', '-i', video, '-t', f'{a.duree:.3f}', '-vn', '-ac', '2', '-ar', str(SR),
                '-c:a', 'pcm_f32le', tmp)
        x, _ = sf.read(str(tmp), dtype='float32', always_2d=True)
        g = 10 ** (-1 / 20) / max(float(np.abs(x).max()), 1e-9)
        sf.write(str(JEU / 'duo-reel.flac'), np.clip(x * g, -1, 1), SR, subtype='PCM_24', format='FLAC')
        plan['reel'] = {'fichier': 'duo-reel.flac', 'gain_entree': g, 'debut_s': round(debut, 3), 'duree_s': round(len(x) / SR, 2)}
        dire(f'vrai duo : {len(x) / SR:.0f} s à partir de {debut:.0f} s')
    (DONNEES / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    dire(f"{len(plan['melanges'])} mélanges, {len(plan['conversions'])} conversions, jeu dans {JEU}")


def envoyer(a):
    banc.configurer_kaggle()
    r = banc.publier_jeu(JEU, f'{banc.COMPTE}/{NOM_KAGGLE}', a.message or f'unisson {time.strftime("%Y-%m-%d %H:%M")}')
    (DONNEES / 'envoi.json').write_text(json.dumps({**r, 'date': time.strftime('%Y-%m-%d %H:%M')}, indent=1))


# --- Session GPU ------------------------------------------------------------------------------------------------------

def quota_restant_h():
    """Heures de GPU restantes (kaggle quota, qui n'affiche pas le jeton), ou None."""
    code, texte = banc.kaggle('quota', '--format', 'json')
    try:
        for r in json.loads(texte[texte.index('['):]):
            if r.get('resource') == 'GPU':
                return float(str(r['remaining']).rstrip('h'))
    except (ValueError, KeyError):
        pass
    return None


def phases(plan, avec_reel):
    """Session 1 (avec_reel) : duos synthétiques, vrai duo et extrait doublé. Sans le vrai duo (session 2) : les duos
    synthétiques seuls, les modèles de parole sur une carte pendant le masque harmonique sur l'autre."""
    if not avec_reel:
        return [[[{'etape': 'generer'}]],
                [[{'etape': 'melanger'}]],
                [[{'etape': 'separer_synth', 'nom': 'a', 'candidats': ['karaoke_becruily', 'hf_aufr33']}],
                 [{'etape': 'separer_synth', 'nom': 'b', 'candidats': ['hf_sucial', 'karaoke_bs_frazer']}]],
                [[{'etape': 'parole', 'candidats': CANDIDATS_PAROLE}], [{'etape': 'masque', 'depuis': ['hf_sucial']}]],
                [[{'etape': 'mesurer'}]]]
    reel = [{'etape': 'separer_reel', 'candidats': CANDIDATS_HF, 'duree_extrait': 45}]
    p = [[[{'etape': 'generer'}], reel],
         [[{'etape': 'melanger'}]],
         [[{'etape': 'separer_synth', 'nom': 'a', 'candidats': ['karaoke_becruily', 'hf_aufr33']}],
          [{'etape': 'separer_synth', 'nom': 'b', 'candidats': ['hf_sucial', 'karaoke_bs_frazer']},
           {'etape': 'parole', 'candidats': CANDIDATS_PAROLE}]],
         [[{'etape': 'masque', 'depuis': CANDIDATS_HF}], [{'etape': 'mesurer', 'exclure': ['masque_']}]]]
    p.append([[{'etape': 'demo', 'candidats': CANDIDATS_HF}], [{'etape': 'mesurer'}]])
    return p


def construire_noyau(reglages):
    """Fonctions de kaggle/doubler_kaggle.py (sans leur bloc final) + noyau_unisson.py, encodés dans un lanceur."""
    base = NOYAU_DEPOT.read_text()
    i = base.rfind("\nif __name__ == '__main__':\n")
    assert i > 0, 'fin de doubler_kaggle.py inattendue'
    base = base[:i].rstrip('\n').replace('__REGLAGES__', base64.b64encode(json.dumps(reglages).encode()).decode())
    complet = base + '\n\n\n' + (CODE / 'noyau_unisson.py').read_text()
    compile(complet, 'unisson_complet.py', 'exec')
    code = base64.b64encode(complet.encode()).decode()
    return ('"""Recherche unisson (recherche/unisson/unisson.py) : lanceur."""\n'
            'import base64, subprocess, sys\n'
            f"SRC = base64.b64decode('{code}').decode()\n"
            "open('/tmp/unisson_complet.py', 'w').write(SRC)\n"
            "r = subprocess.run([sys.executable, '/tmp/unisson_complet.py', 'orchestrer'])\n"
            "print('LANCEUR fin, code', r.returncode, flush=True)\n")


def lancer(a):
    banc.configurer_kaggle()
    noyau, jeu = f'{banc.COMPTE}/{NOM_NOYAU}', f'{banc.COMPTE}/{NOM_KAGGLE}'
    plan = json.loads((DONNEES / 'plan.json').read_text())
    limite_s = int(a.limite * 60)
    reglages = {'cfg': 0.7, 'fp16': True, 'pad_s': 0.25, 'fondu_s': 0.06, 'graine': 1234, 'recouvrement': 2,
                'recouvrement_voix': 2, 'pas_synth': 20, 'pas_demo': 30, 'limite_totale_s': limite_s - 90,
                'paquets_parole': PAQUETS_PAROLE, 'plan': plan,
                'phases': json.loads(a.phases) if a.phases else phases(plan, 'reel' in plan and not a.sans_reel)}
    if 'reel' in plan and not a.sans_reel:
        reglages['reel'] = plan['reel']
    envoi = DONNEES / '.kaggle' / 'envoi-noyau'
    shutil.rmtree(envoi, ignore_errors=True)
    envoi.mkdir(parents=True)
    (envoi / 'unisson_noyau.py').write_text(construire_noyau(reglages))
    (envoi / 'kernel-metadata.json').write_text(json.dumps({
        'id': noyau, 'title': NOM_NOYAU, 'code_file': 'unisson_noyau.py', 'language': 'python', 'kernel_type': 'script',
        'is_private': True, 'enable_gpu': True, 'enable_tpu': False, 'enable_internet': True, 'machine_shape': 'NvidiaTeslaT4',
        'dataset_sources': [jeu], 'competition_sources': [], 'kernel_sources': [banc.OUTILS], 'model_sources': []}, indent=1))
    if a.essai:
        dire(f'essai : noyau construit dans {envoi}, rien n\'est envoyé')
        return
    retour = RESULTATS / a.session
    retour.mkdir(parents=True, exist_ok=True)
    with banc.VerrouKaggle():   # une seule session GPU à la fois sur le compte (doubler, banc, recherche)
        debut_attente, dit = time.time(), False
        while True:
            actives = banc.sessions_gpu_actives()
            if not actives:
                break
            if not dit:
                dire(f'Kaggle : {len(actives)} session(s) active(s) ({", ".join(actives)}), attente de leur fin')
                dit = True
            if time.time() - debut_attente > 3 * 3600:
                raise SystemExit('Kaggle : le compte a une session active depuis 3 h')
            time.sleep(120)
        avant = quota_restant_h()
        if avant is None or avant - limite_s / 3600 < QUOTA_MINI_H:
            raise SystemExit(f'quota GPU : {avant} h restantes ; une session de {a.limite:.0f} min laisserait moins de '
                             f'{QUOTA_MINI_H:.0f} h : rien n\'est lancé')
        dire(f'quota GPU avant la session : {avant:.2f} h')
        code, texte = banc.kaggle_obstine('kernels', 'push', '-p', str(envoi), essais=3, pause=60,
                                          ok=lambda c, t: bool(re.search(r'successfully pushed', t, re.I)))
        if not re.search(r'successfully pushed', texte, re.I):
            raise SystemExit('Kaggle : noyau refusé : ' + texte.strip()[-500:])
        pousse = time.time()
        dire('Kaggle : noyau lancé — ' + texte.strip().splitlines()[-1])
        suivi = {'session': a.session, 'pousse': time.strftime('%Y-%m-%d %H:%M:%S'), 'quota_avant_h': avant,
                 'limite_min': a.limite}
        (retour / 'suivi.json').write_text(json.dumps(suivi, indent=1))
        etat, vu, pannes, dit = '', False, 0, ''
        while True:
            time.sleep(30)
            code, texte = banc.kaggle('kernels', 'status', noyau)
            if code:
                pannes += 1
                if pannes >= 60:
                    raise SystemExit(f'Kaggle : état indisponible depuis 30 min ; relancer « unisson.py rapatrier {a.session} »')
                continue
            pannes = 0
            m = re.search(r'status "?(?:\w+\.)?(\w+)"?', texte, re.I)
            etat = (m.group(1) if m else 'inconnu').upper()
            if etat != dit:
                dire(f'Kaggle : {etat.lower()} ({(time.time() - pousse) / 60:.1f} min)')
                dit = etat
            en_cours = bool(re.search(r'QUEUED|RUNNING|NEW|PENDING', etat))
            vu = vu or en_cours
            if not en_cours and (vu or time.time() - pousse > 300):
                break
        suivi.update({'fin': time.strftime('%Y-%m-%d %H:%M:%S'), 'noyau_depuis_envoi_s': round(time.time() - pousse, 1),
                      'etat': etat})
        rapatrier_session(a.session)
        apres = quota_restant_h()
        suivi.update({'quota_apres_h': apres, 'quota_consomme_h': round(avant - apres, 3) if apres is not None else None})
        (retour / 'suivi.json').write_text(json.dumps(suivi, indent=1))
        dire(f'quota GPU après la session : {apres} h')


def rapatrier_session(session):
    banc.configurer_kaggle()
    noyau = f'{banc.COMPTE}/{NOM_NOYAU}'
    retour = RESULTATS / session
    tmp = retour / '.telechargement'
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for _ in range(6):
        code, texte = banc.kaggle('kernels', 'output', noyau, '-p', str(tmp), '--force')
        if code == 0:
            break
        dire('Kaggle : rapatriement en échec, nouvel essai : ' + texte.strip()[-200:])
        time.sleep(30)
    for f in tmp.iterdir():
        dest = retour / f.name
        if dest.is_dir():
            shutil.rmtree(dest)
        shutil.move(str(f), str(dest))
    shutil.rmtree(tmp, ignore_errors=True)
    journal = []
    for f in retour.glob('*.log'):
        try:
            journal += [l.get('data', '') for l in json.loads(f.read_text())]
        except ValueError:
            pass
    lignes = [l.rstrip() for l in ''.join(journal).splitlines() if l.strip()]
    utiles = [l for l in lignes if re.search(r'ÉTAPE|ÉCHEC|Error|Traceback|MESURE|REEL|DEMO|PAROLE|LANCEUR|GENERER', l)]
    print('\n'.join(utiles[-80:]))
    dire(f'rapatrié dans {retour}')


def rapatrier(a):
    rapatrier_session(a.session)


# --- Tableaux ---------------------------------------------------------------------------------------------------------

def tableau(a):
    r = RESULTATS / a.session
    resume = json.loads((r / 'synth' / 'resume.json').read_text())
    ordre = [c for c in LIBELLES if c in resume] + sorted(c for c in resume if c not in LIBELLES)
    for titre, suffixe in (('Duos mixtes (homme + femme)', '_mixte'), ('Duos de même genre', '_meme_genre')):
        conds = [c for c in CONDITIONS if any(c + suffixe in resume[k] for k in resume)]
        print(f'\n**{titre}** : SI-SDR moyen des deux voix (dB, 44,1 kHz) ; entre parenthèses, part des mélanges où '
              "l'empreinte attribue chaque sortie au bon chanteur\n")
        print('| Candidat | ' + ' | '.join(conds) + ' |')
        print('|---|' + '---|' * len(conds))
        for c in ordre:
            cases = []
            for k in conds:
                v = resume[c].get(k + suffixe)
                if not v:
                    cases.append('–')
                    continue
                ident = '' if v.get('id_ok') is None else f" ({v['id_ok'] * 100:.0f} %)"
                cases.append(f"{v['si_sdr']:+.1f}{ident}")
            print(f'| {LIBELLES.get(c, c)} | ' + ' | '.join(cases) + ' |')
    print('\n**Amélioration sur le mélange (SI-SDRi, dB), duos mixtes, et étiquette homme/femme juste**\n')
    conds = [c for c in CONDITIONS if any(c + '_mixte' in resume[k] for k in resume)]
    print('| Candidat | ' + ' | '.join(conds) + ' | étiquette |')
    print('|---|' + '---|' * (len(conds) + 1))
    for c in ordre:
        if c == 'melange':
            continue
        cases = [f"{resume[c][k + '_mixte']['si_sdri']:+.1f}" if k + '_mixte' in resume[c] else '–' for k in conds]
        et = [resume[c][k + '_mixte'].get('etiquette_ok') for k in conds if k + '_mixte' in resume[c]]
        et = [e for e in et if e is not None]
        print(f'| {LIBELLES.get(c, c)} | ' + ' | '.join(cases) + f" | {f'{sum(et) / len(et) * 100:.0f} %' if et else '–'} |")
    reel = r / 'reel' / 'reel.json'
    if reel.exists():
        d = json.loads(reel.read_text())
        print(f"\n**Vrai duo** ({d['duree_s']:.0f} s analysées) : temps de chaque passe sur une T4 (rtf = temps / durée du son)\n")
        print('| Passe | Modèle | Séparation (s) | rtf | Chargement (s) |')
        print('|---|---|---|---|---|')
        for k, p in d['passes'].items():
            if 'erreur' in p:
                print(f'| {k} | erreur | – | – | – |')
                continue
            print(f"| {k} | {p['modele']} | {p['separation_s']:.1f} | {p['rtf']:.2f} | {p['chargement_s']:.0f} |")
        print('\n| Modèle, entrée | Chanté à deux (s) | Homme (s) | Femme (s) |')
        print('|---|---|---|---|')
        for k, v in d['a_deux'].items():
            print(f"| {k} | {v['secondes_a_deux']:.1f} | {v['secondes_homme']:.1f} | {v['secondes_femme']:.1f} |")
        print(f"\nExtrait d'écoute : {d['extrait']}")
    for f in ('generer.json', 'separer_a.json', 'separer_b.json', 'separer_parole.json', 'masque.json'):
        p = r / 'synth' / f
        if p.exists():
            print(f'\n{f} : ' + json.dumps(json.loads(p.read_text()), ensure_ascii=False)[:1500])
    s = r / 'session.json'
    if s.exists():
        d = json.loads(s.read_text())
        print(f"\nsession : installation {d.get('installation_s')} s, total {d.get('total_s')} s")
        for ph in d.get('phases', []):
            print('  ', ' ; '.join(f"{e['etape']} (carte {e.get('carte')}) {e.get('code')} {e.get('duree_s')} s" for e in ph))


# --- Écoute à l'aveugle -----------------------------------------------------------------------------------------------

def lire_piste(chemin, gain):
    import numpy as np
    import soundfile as sf
    x, sr = sf.read(str(chemin), dtype='float32', always_2d=True)
    assert sr == SR
    return x / np.float32(gain)


def ecoute(a):
    """Deux extraits : « unisson » (pistes séparées, chacune seule sur la musique) et « unisson-double » (la voix
    principale doublée d'un bloc, ou chaque chanteur dans sa voix). Même chaîne de mix que doubler et le banc."""
    import numpy as np
    import soundfile as sf
    r = RESULTATS / a.session
    reel = json.loads((r / 'reel' / 'reel.json').read_text())
    gains = reel['gains_flac']
    dossier = Path(a.ecoute).expanduser() if a.ecoute else DEPOT / 'banc' / 'ecoute'
    dossier.mkdir(parents=True, exist_ok=True)
    t = TRAVAIL / 'ecoute'
    shutil.rmtree(t, ignore_errors=True)
    t.mkdir(parents=True)
    sf.write(str(t / 'original.wav'), lire_piste(r / 'reel' / 'original.flac', gains['original']), SR, subtype='FLOAT')
    sf.write(str(t / 'instrumental.wav'), lire_piste(r / 'reel' / 'musique.flac', gains['musique']), SR, subtype='FLOAT')
    sf.write(str(t / 'voix_source.wav'), lire_piste(r / 'reel' / 'voix.flac', gains['voix']), SR, subtype='FLOAT')
    rp = RESULTATS / (a.parole or a.session)   # modèles de parole : d'une autre session, au besoin
    resume = json.loads((rp / 'synth' / 'resume.json').read_text()) if (rp / 'synth' / 'resume.json').exists() else {}

    def note(c):
        """Moyenne sur les duos mixtes chantés ensemble, deux voix converties (sans l'indice d'acoustique)."""
        v = [resume.get(c, {}).get(k + '_mixte', {}).get('si_sdr') for k in ('unisson_conv', 'octave_conv', 'tierce_conv', 'meme_air')]
        v = [x for x in v if x is not None]
        return sum(v) / len(v) if v else -99
    versions = {'voix principale sèche, non séparée (chaîne actuelle)': (r / 'reel' / 'lead_sec.flac', gains['lead_sec'])}
    for c in CANDIDATS_HF:
        for k, g in (('h', "voix d'homme"), ('f', 'voix de femme')):
            f = r / 'reel' / f'{c}_lead_sec_{k}.flac'
            if f.exists():
                versions[f'{LIBELLES[c]} : {g}'] = (f, gains[f'{c}_lead_sec_{k}'])
    parole = json.loads((rp / 'synth' / 'separer_parole.json').read_text()) if (rp / 'synth' / 'separer_parole.json').exists() else {}
    meilleure_parole = max((c for c in CANDIDATS_PAROLE if c in resume and (rp / 'reel' / f'{c}_o1.flac').exists()),
                           key=note, default=None)
    if meilleure_parole:
        for k in (1, 2):
            versions[f'{LIBELLES[meilleure_parole]} : voix {k}'] = (
                rp / 'reel' / f'{meilleure_parole}_o{k}.flac', parole[meilleure_parole]['gains_flac_reel'][f'{meilleure_parole}_o{k}'])
    extraits = {'unisson': versions}
    demo = r / 'demo' / 'demo.json'
    if demo.exists():
        d = json.loads(demo.read_text())
        g = d['gains_flac']
        extraits['unisson-double'] = {
            'voix principale doublée d\'un bloc dans la voix de femme (chaîne actuelle)': (r / 'demo' / 'bloc.flac', g['bloc']),
            f"chaque chanteur séparé ({LIBELLES[d['meilleur']]}) dans sa voix": (r / 'demo' / 'separe.flac', g['separe'])}
    titres = {'unisson': 'Unisson : duo réel', 'unisson-double': 'Unisson : duo réel, doublé'}
    correspondance = banc.lire_json_local(dossier / 'correspondance.json')
    alea = random.Random(a.graine)
    for ex, vs in extraits.items():
        d = dossier / ex
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        tv = t / ex
        tv.mkdir(parents=True)
        noms = sorted(vs)
        alea.shuffle(noms)
        lettres = {chr(65 + i): v for i, v in enumerate(noms)}
        gains_mix = {}
        for lettre, v in lettres.items():
            chemin, gain = vs[v]
            y = lire_piste(chemin, gain).mean(1)
            n = sf.info(str(t / 'instrumental.wav')).frames
            y = y[:n] if len(y) >= n else np.pad(y, (0, n - len(y)))
            src = tv / f'{lettre}.wav'
            sf.write(str(src), y, SR, subtype='FLOAT')
            g, _ = banc.mixer_doubler(t, tv, 'chant', 4.0, -13.0, src, tv / f'mix_{lettre}.wav')
            gains_mix[v] = {k: round(x, 2) for k, x in g.items()}
            banc.ff('-i', tv / f'mix_{lettre}.wav', '-c:a', 'libmp3lame', '-b:a', '320k', d / f'{lettre}.mp3')
            banc.ff('-i', tv / f'seche_{lettre}.wav', '-af',
                    f"volume={g['seche']:.2f}dB,pan=mono|c0=c0,alimiter=limit=0.95:level=disabled",
                    '-c:a', 'libmp3lame', '-b:a', '256k', d / f'{lettre}_voix.mp3')
        banc.ff('-i', t / 'original.wav', '-af', 'alimiter=limit=0.95:level=disabled', '-c:a', 'libmp3lame', '-b:a', '320k',
                d / 'original.mp3')
        correspondance[ex] = lettres
        correspondance.setdefault('titres', {})[ex] = titres[ex]
        correspondance.setdefault('gains_db', {})[ex] = gains_mix
        dire(f'{ex} : ' + ', '.join(f'{l} = {v}' for l, v in lettres.items()))
    tmp = dossier / 'correspondance.json.tmp'
    tmp.write_text(json.dumps(correspondance, ensure_ascii=False, indent=1))
    os.replace(tmp, dossier / 'correspondance.json')
    shutil.rmtree(t, ignore_errors=True)
    dire(f'écoute : {dossier}/unisson/ et {dossier}/unisson-double/')


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sp = p.add_subparsers(dest='cmd', required=True)
    q = sp.add_parser('preparer')
    q.add_argument('--video', help='vrai duo (vidéo locale, lue par ffmpeg seulement)')
    q.add_argument('--debut', type=float, default=None, help='début de la partie analysée (s) ; défaut : la fin')
    q.add_argument('--duree', type=float, default=110.0, help='durée de la partie analysée (s)')
    q.add_argument('--reel-extrait', help='session dont la voix principale sèche de l\'extrait d\'écoute part dans le jeu')
    q = sp.add_parser('envoyer')
    q.add_argument('--message')
    q = sp.add_parser('lancer')
    q.add_argument('session')
    q.add_argument('--limite', type=float, default=28.0, help='limite de la session (min) ; défaut 28')
    q.add_argument('--phases', help='phases (JSON) à la place du plan par défaut')
    q.add_argument('--essai', action='store_true', help='construit le noyau sans rien envoyer')
    q.add_argument('--sans-reel', action='store_true',
                   help='duos synthétiques seuls (le vrai duo et l\'extrait doublé viennent d\'une session précédente)')
    for nom in ('rapatrier', 'tableau'):
        q = sp.add_parser(nom)
        q.add_argument('session')
    q = sp.add_parser('ecoute')
    q.add_argument('session')
    q.add_argument('--ecoute', help='dossier de l\'écoute à l\'aveugle (défaut : banc/ecoute du dépôt)')
    q.add_argument('--graine', type=int, default=20261106)
    q.add_argument('--parole', help='session des modèles de parole (défaut : la même)')
    a = p.parse_args()
    {'preparer': preparer, 'envoyer': envoyer, 'lancer': lancer, 'rapatrier': rapatrier, 'tableau': tableau,
     'ecoute': ecoute}[a.cmd](a)


if __name__ == '__main__':
    main()
