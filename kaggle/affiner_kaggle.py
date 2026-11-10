"""Affinage d'une voix cible sur son chant, sur le GPU T4 gratuit de Kaggle : un noyau court, qui rend un point de contrôle
compact de Seed-VC 44,1 kHz (chant) avec son rapport. Construit comme doubler_kaggle.py : même cadre (réglages en base64,
journal horodaté, outils montés, Seed-VC au commit figé), mais sans séparation ni conversion.

Pourquoi Seed-VC : son dépôt a un script d'affinage officiel (train.py, prévu pour le préréglage chant 44,1 kHz
config_dit_mel_seed_uvit_whisper_base_f0_44k.yml) ; YingMusic-SVC n'en publie pas (voir docs/affinage.md).

1. Prises de chant (jeu de données privé, dossier chant/) : lecture en mono 44,1 kHz, passe-haut à 70 Hz, niveau de chaque
   prise ramené à -23 dBFS actif (un gain par prise : les nuances restent), crête gardée sous -1 dBFS.
2. Découpage aux silences en morceaux de 3 à 12 s (Seed-VC ignore ce qui dure moins de 1 s ou plus de 30 s), contrôle de
   chaque morceau : saturation, bruit de fond, trop faible, trop de silence. Les morceaux refusés sont comptés dans le
   rapport, jamais utilisés.
3. Seed-VC (commit figé, depuis le montage d'outils s'il y est) : train.py, un seul GPU, sauvegardes toutes les
   sauvegarde_tous étapes (train.py en garde deux) dans /kaggle/working/runs/<voix>/, reprises seules après un plantage
   du processus, arrêt propre quand le budget de temps est atteint.
4. Sortie dans /kaggle/working/ :
     affinage/<voix>.pth   point de contrôle compact (poids en demi-précision) ; <voix>.yml : la config qui va avec
     rapport.json          réglages, données (prises, morceaux, refus), pertes, durées, reprise, tailles
     reprise/              dernier point de contrôle complet, seulement si l'affinage n'est pas fini (budget ou panne) :
                           affiner.py le renvoie dans le jeu de données (dossier reprise/) pour continuer
Reprise : un point de contrôle trouvé dans le jeu de données (reprise/DiT_epoch_*_step_*.pth) est remis dans le dossier
d'exécution ; train.py reprend alors seul à la dernière étape sauvegardée.

affiner.py remplace les deux marques du fichier (réglages en JSON base64, et la source, inutilisée ici), puis pousse ce noyau privé.
Réglages : voix, simulation (essai sans GPU ni modèle), budget_s (temps maximal de la session), max_steps, batch,
sauvegarde_tous, relances, niveau_cible_db, pas_min_s, pas_max_s, commande_train, seedvc, sortie, entrees, tmp (essais).
"""
import base64
import glob
import json
import math
import os
import re
import select
import shutil
import subprocess
import sys
import time
import wave

import numpy as np

REGLAGES = json.loads(base64.b64decode('__REGLAGES__'))
DEPOT = 'https://github.com/Plachtaa/seed-vc.git'
COMMIT = '51383efd921027683c89e5348211d93ff12ac2a8'   # le même que doubler_kaggle.py : un seul Seed-VC, un seul montage d'outils
SEEDVC = '/tmp/seed-vc'
SORTIE = '/kaggle/working'
TMP = '/tmp/affinage'
SR = 44100
PRESET = 'configs/presets/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml'   # chant 44,1 kHz, conditionné par la hauteur
DEBUT = float(os.environ.get('DOUBLER_DEBUT') or time.time())
SOURCE = '__SOURCE__'

DEFAUTS = {
    'budget_s': 2 * 3600,        # la session entière : de quoi installer, entraîner et rendre, sans manger le quota (30 h par semaine)
    'max_steps': 1500,           # étapes d'entraînement (batch 2) ; la README de Seed-VC en donne 1000 dans son exemple
    'batch': 2,
    'sauvegarde_tous': 250,      # étapes entre deux sauvegardes complètes
    'relances': 2,               # reprises de train.py après un plantage
    'marge_s': 420,              # temps gardé pour compacter, écrire le rapport et sortir
    'niveau_cible_db': -23.0,    # niveau actif de chaque prise
    'crete_max_db': -1.0,
    'gain_max_db': 24.0,
    'hp_hz': 70.0,
    'pas_min_s': 3.0,
    'pas_max_s': 12.0,
    'pause_s': 0.3,              # pause qui sépare deux phrases
    'marge_morceau_s': 0.15,     # silence gardé autour de chaque morceau
    'saturation_max': 0.002,     # part d'échantillons à pleine échelle tolérée dans un morceau
    'rsb_min_db': 15.0,          # chant au-dessus du fond
    'niveau_min_db': -45.0,      # niveau actif avant normalisation, en dessous : prise inutilisable
    'voise_min': 0.4,            # part du morceau qui doit chanter
    'utile_min_s': 120.0,        # en dessous : arrêt, pas assez de chant exploitable
    'utile_conseil_s': 300.0,    # en dessous : avertissement
}


def reglage(R, cle):
    return R.get(cle, DEFAUTS.get(cle))


def log(*a):
    print(f'[{time.time() - DEBUT:7.1f} s]', *a, flush=True)


def ecrire_json(chemin, d):
    os.makedirs(os.path.dirname(chemin) or '.', exist_ok=True)
    tmp = chemin + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f, ensure_ascii=False, indent=1, default=float)
    os.replace(tmp, chemin)


# --- Prises : lecture, niveau, découpage, contrôle -------------------------------------------------------------------

def lire_audio(chemin):
    """Mono 44,1 kHz flottant : par ffmpeg (réchantillonne et mélange), sinon soundfile si le fichier est déjà à 44,1 kHz."""
    if shutil.which('ffmpeg'):
        r = subprocess.run(['ffmpeg', '-v', 'error', '-i', chemin, '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-'],
                           capture_output=True)
        if r.returncode == 0 and r.stdout:
            return np.frombuffer(r.stdout, dtype='<f4').astype(np.float32)
    import soundfile as sf
    x, sr = sf.read(chemin, dtype='float32', always_2d=True)
    if sr != SR:
        raise RuntimeError(f'{chemin} : {sr} Hz, ffmpeg absent pour réchantillonner')
    return x.mean(axis=1)


def ecrire_wav(chemin, x):
    """WAV 16 bits mono 44,1 kHz (bibliothèque standard seulement)."""
    q = np.clip(np.round(np.asarray(x, np.float64) * 32767), -32768, 32767).astype('<i2')
    with wave.open(chemin, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(q.tobytes())


def passe_haut(x, hz, sr=SR):
    """Passe-haut sans déphasage (Butterworth d'ordre 4, par FFT) : retire le grondement et la composante continue."""
    n = len(x)
    if n == 0 or hz <= 0:
        return x
    f = np.fft.rfftfreq(n, 1 / sr)
    gain = 1 / np.sqrt(1 + (hz / np.maximum(f, 1e-3)) ** 8)
    gain[0] = 0
    return np.fft.irfft(np.fft.rfft(x.astype(np.float64)) * gain, n).astype(np.float32)


def enveloppe_db(x, pas=441, fenetre=4):
    """Niveau RMS en dBFS toutes les 10 ms, sur fenetre pas (40 ms)."""
    n = len(x) // pas
    if n == 0:
        return np.zeros(0)
    p = np.mean(np.asarray(x[:n * pas], np.float64).reshape(n, pas) ** 2, axis=1)
    k = np.ones(fenetre) / fenetre
    p = np.convolve(np.pad(p, (fenetre // 2, fenetre - 1 - fenetre // 2), mode='edge'), k, mode='valid')
    return 10 * np.log10(np.maximum(p, 1e-12))


def seuil_silence(env):
    """Seuil de silence : au-dessus du fond (10e centile) de 10 dB et sous le chant fort (90e centile) de 30 dB."""
    if len(env) == 0:
        return -60.0
    fond, fort = np.percentile(env, 10), np.percentile(env, 90)
    return float(min(max(fond + 10, fort - 30), -35.0))


def niveau_actif_db(x, env, seuil, pas=441):
    """Niveau RMS du seul chant (trames au-dessus du seuil), en dBFS ; None sans chant."""
    actif = env > seuil
    if not actif.any():
        return None
    n = len(env) * pas
    p = np.asarray(x[:n], np.float64).reshape(len(env), pas) ** 2
    return float(10 * np.log10(max(p[actif].mean(), 1e-12)))


def gain_prise(x, niveau_db, R):
    """Gain (dB) qui met la prise au niveau cible, borné, et réduit pour que la crête reste sous crete_max_db."""
    cible = reglage(R, 'niveau_cible_db')
    g = min(max(cible - niveau_db, -12.0), reglage(R, 'gain_max_db'))
    crete = float(np.abs(x).max()) if len(x) else 0.0
    if crete > 0:
        g = min(g, reglage(R, 'crete_max_db') - 20 * math.log10(crete))
    return g


def decouper(env, seuil, R):
    """Morceaux [(début_s, fin_s)] de pas_min_s à pas_max_s, coupés dans les pauses ; une phrase plus longue que pas_max_s
    est coupée à son point le plus bas. Les morceaux trop courts sont comptés à part (rendus avec les refus)."""
    pas = 0.01
    pmin, pmax = reglage(R, 'pas_min_s'), reglage(R, 'pas_max_s')
    marge = reglage(R, 'marge_morceau_s')
    pause = int(round(reglage(R, 'pause_s') / pas))
    actif = env > seuil
    # phrases : suites de trames actives, trous plus courts que la pause comblés
    phrases, debut, trou = [], None, 0
    for i, a in enumerate(actif):
        if a:
            if debut is None:
                debut = i
            trou = 0
        elif debut is not None:
            trou += 1
            if trou >= pause:
                phrases.append((debut, i - trou + 1))
                debut, trou = None, 0
    if debut is not None:
        phrases.append((debut, len(actif) - trou))
    # une phrase trop longue est coupée à la trame la plus basse de sa fenêtre [pmin, pmax]
    morceaux_p = []
    for a, b in phrases:
        while (b - a) * pas > pmax:
            lo, hi = a + int(pmin / pas), a + int(pmax / pas)
            c = lo + int(np.argmin(env[lo:hi]))
            morceaux_p.append((a, c))
            a = c
        morceaux_p.append((a, b))
    # regroupement glouton de phrases voisines, sans dépasser pmax
    groupes = []
    for a, b in morceaux_p:
        if groupes and (b - groupes[-1][0]) * pas <= pmax:
            groupes[-1] = (groupes[-1][0], b)
        else:
            groupes.append((a, b))
    sortie, courts = [], 0
    for k, (a, b) in enumerate(groupes):
        pa = groupes[k - 1][1] if k else 0
        sb = groupes[k + 1][0] if k + 1 < len(groupes) else len(env)
        d = max(a * pas - marge, (pa * pas + a * pas) / 2 if k else 0.0)
        f = min(b * pas + marge, (b * pas + sb * pas) / 2 if k + 1 < len(groupes) else len(env) * pas)
        if f - d < pmin:
            courts += 1
        else:
            sortie.append((round(d, 2), round(f, 2)))
    return sortie, courts


def controler_morceau(x, R, brut=None):
    """Contrôle d'un morceau (déjà au niveau cible) : None s'il est bon, sinon la raison du refus. brut : le même morceau
    avant tout traitement, où la saturation se mesure (la normalisation de la crête la cacherait)."""
    if len(x) == 0:
        return 'vide'
    sat = float(np.mean(np.abs(x if brut is None else brut) >= 0.99))
    if sat > reglage(R, 'saturation_max'):
        return 'saturé'
    env = enveloppe_db(x)
    if len(env) < 10:
        return 'trop court'
    fond, fort = float(np.percentile(env, 10)), float(np.percentile(env, 90))
    if fort - fond < reglage(R, 'rsb_min_db'):
        return 'bruité'
    voise = float(np.mean(env > seuil_silence(env)))
    if voise < reglage(R, 'voise_min'):
        return 'trop de silence'
    return None


def preparer_donnees(prises, dossier, R):
    """Prises (chemins) -> morceaux WAV dans dossier ; rapport des données. Un fichier illisible est compté, pas fatal."""
    shutil.rmtree(dossier, ignore_errors=True)
    os.makedirs(dossier)
    rapport = {'prises': [], 'morceaux': 0, 'utile_s': 0.0, 'refuses': {}, 'courts': 0, 'illisibles': []}
    for n, chemin in enumerate(sorted(prises), 1):
        nom = os.path.basename(chemin)
        try:
            x = lire_audio(chemin)
        except Exception as e:  # noqa: BLE001 — un fichier abîmé ne doit pas arrêter les autres
            rapport['illisibles'].append({'prise': nom, 'erreur': str(e)[:120]})
            continue
        brut = x
        x = passe_haut(x, reglage(R, 'hp_hz'))
        env = enveloppe_db(x)
        seuil = seuil_silence(env)
        niveau = niveau_actif_db(x, env, seuil)
        info = {'prise': nom, 'duree_s': round(len(x) / SR, 1), 'niveau_db': None if niveau is None else round(niveau, 1)}
        if niveau is None or niveau < reglage(R, 'niveau_min_db'):
            info['refus'] = 'trop faible'
            rapport['refuses']['trop faible'] = rapport['refuses'].get('trop faible', 0) + 1
            rapport['prises'].append(info)
            continue
        g = gain_prise(x, niveau, R)
        x = (x * 10 ** (g / 20)).astype(np.float32)
        info['gain_db'] = round(g, 1)
        morceaux, courts = decouper(enveloppe_db(x), seuil + g, R)
        rapport['courts'] += courts
        gardes = 0
        for k, (a, b) in enumerate(morceaux, 1):
            m = x[int(a * SR):int(b * SR)]
            raison = controler_morceau(m, R, brut[int(a * SR):int(b * SR)])
            if raison:
                rapport['refuses'][raison] = rapport['refuses'].get(raison, 0) + 1
                continue
            ecrire_wav(os.path.join(dossier, f'p{n:02d}_{k:03d}.wav'), m)
            gardes += 1
            rapport['utile_s'] += len(m) / SR
        info['morceaux'] = gardes
        rapport['morceaux'] += gardes
        rapport['prises'].append(info)
    rapport['utile_s'] = round(rapport['utile_s'], 1)
    return rapport


# --- Sorties de train.py ---------------------------------------------------------------------------------------------

MOTIF_PAS = re.compile(r'(?:steps?|iters?)\D{0,3}(\d+)', re.I)
MOTIF_PERTE = re.compile(r'([A-Za-z_/]*loss[A-Za-z_/]*)\s*[:=]?\s*(-?[0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)', re.I)


def lire_pertes(ligne):
    """{'pas': N, '<perte>': valeur, …} d'une ligne de journal de train.py qui contient une perte, sinon None."""
    pertes = {m.group(1): float(m.group(2)) for m in MOTIF_PERTE.finditer(ligne)}
    if not pertes:
        return None
    pas = MOTIF_PAS.search(ligne)
    return {'pas': int(pas.group(1)) if pas else None, **pertes}


def resume_pertes(points, max_points=120):
    """Courbe réduite à max_points, premières et dernières pertes, moyenne du dernier dixième."""
    if not points:
        return {'points': [], 'nombre': 0}
    cles = sorted({k for p in points for k in p if k != 'pas'})
    saut = max(1, math.ceil(len(points) / max_points))
    res = {'nombre': len(points), 'points': points[::saut], 'premiere': points[0], 'derniere': points[-1]}
    fin = points[-max(1, len(points) // 10):]
    res['moyenne_fin'] = {k: round(float(np.mean([p[k] for p in fin if k in p])), 5) for k in cles
                          if any(k in p for p in fin)}
    return res


MOTIF_POINT = re.compile(r'DiT_epoch_(\d+)_step_(\d+)\.pth$')


def dernier_point(dossier):
    """(pas, chemin) du point de contrôle complet le plus avancé de dossier, sinon (0, None)."""
    liste = []
    for f in glob.glob(os.path.join(dossier, 'DiT_epoch_*_step_*.pth')):
        m = MOTIF_POINT.search(f)
        if m:
            liste.append((int(m.group(2)), f))
    return max(liste) if liste else (0, None)


def lancer_train(commande, cwd, fin, pertes, env=None, silence_max_s=1800):
    """Lance train.py, lit sa sortie (lignes ou retours chariot de tqdm), garde les pertes, l'arrête à l'heure fin (epoch).
    Rend {'code', 'arret_budget', 'duree_s'}."""
    t0 = time.time()
    p = subprocess.Popen(commande, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    tampon, dernier_signe, arret = b'', time.time(), False
    try:
        while True:
            if time.time() > fin and not arret:
                log('budget de temps atteint : arrêt de train.py (dernier point sauvegardé gardé)')
                p.terminate()
                arret = True
            if arret and time.time() > fin + 60:
                p.kill()
            prets, _, _ = select.select([p.stdout], [], [], 1.0)
            if prets:
                morceau = os.read(p.stdout.fileno(), 65536)
                if not morceau:
                    break
                dernier_signe = time.time()
                tampon += morceau
                *lignes, tampon = re.split(rb'[\r\n]', tampon)
                for l in lignes:
                    texte = l.decode('utf-8', 'replace').strip()
                    if not texte:
                        continue
                    pt = lire_pertes(texte)
                    if pt:
                        pertes.append(pt)
                    if pt:
                        if len(pertes) % 25 == 1:   # une ligne de journal sur 25 : Kaggle garde tout, c'est inutile
                            log('train.py :', texte[:240])
                    elif re.search(r'error|traceback|exception|checkpoint|resum|save', texte, re.I):
                        log('train.py :', texte[:240])
            elif p.poll() is not None:
                break
            elif time.time() - dernier_signe > silence_max_s:
                log('train.py muet depuis trop longtemps : arrêt')
                p.kill()
                arret = True
                dernier_signe = time.time()
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
        p.stdout.close()
    return {'code': p.returncode, 'arret_budget': arret, 'duree_s': round(time.time() - t0, 1)}


def entrainer(commande, cwd, run_dir, fin, pertes, R, env=None):
    """train.py jusqu'au bout, relancé (il reprend seul à son dernier point) après un plantage, dans la limite de
    relances et du budget. Rend le journal des lancements et l'état."""
    lancements, termine = [], False
    for essai in range(reglage(R, 'relances') + 1):
        avant = dernier_point(run_dir)[0]
        r = lancer_train(commande, cwd, fin, pertes, env)
        r['reprend_a'] = avant
        r['pas_fin'] = dernier_point(run_dir)[0]
        lancements.append(r)
        if r['code'] == 0 and not r['arret_budget']:
            termine = True
            break
        if r['arret_budget'] or time.time() > fin:
            break
        log(f"train.py s'est arrêté (code {r['code']}) à l'étape {r['pas_fin']} : reprise ({essai + 1}/{reglage(R, 'relances')})")
    return {'lancements': lancements, 'termine': termine}


# --- Point de contrôle compact ---------------------------------------------------------------------------------------

def compacter(source, destination, simulation=False):
    """Poids du point de contrôle (clé « net » : cfm et length_regulator affinés) en demi-précision, sans optimiseur.
    Rend {'modules', 'parametres', 'taille_mo'}. Simulation : le fichier est copié tel quel (pas de torch)."""
    if simulation:
        shutil.copy2(source, destination)
        return {'modules': ['simulation'], 'parametres': 0, 'taille_mo': round(os.path.getsize(destination) / 1e6, 3)}
    import torch
    etat = torch.load(source, map_location='cpu', weights_only=False)
    net = etat['net'] if 'net' in etat else etat
    compact, n = {}, 0
    for module, poids in net.items():
        compact[module] = {k: (v.half() if hasattr(v, 'is_floating_point') and v.is_floating_point() else v)
                           for k, v in poids.items()}
        n += sum(v.numel() for v in poids.values() if hasattr(v, 'numel'))
    torch.save({'net': compact, 'pas': etat.get('iters') if isinstance(etat, dict) else None,
                'precision': 'fp16'}, destination)
    return {'modules': sorted(compact), 'parametres': n, 'taille_mo': round(os.path.getsize(destination) / 1e6, 1)}


# --- Installation de Seed-VC -----------------------------------------------------------------------------------------

def lancer(*commande, **options):
    subprocess.run(commande, check=True, **options)


def trouver_outils():
    """Le dossier du noyau « outils » monté par kernel_sources (repéré par outils.json), sinon None."""
    fiches = glob.glob('/kaggle/input/**/outils.json', recursive=True)
    return os.path.dirname(fiches[0]) if fiches else None


def lire_outils(outils):
    try:
        with open(os.path.join(outils, 'outils.json')) as f:
            return json.load(f)
    except (TypeError, OSError, ValueError):
        return {}


def corriger(chemin, remplacements):
    with open(chemin) as f:
        texte = f.read()
    neuf = texte
    for avant, apres in remplacements:
        neuf = neuf.replace(avant, apres)
    if neuf != texte:
        with open(chemin, 'w') as f:
            f.write(neuf)
        log('corrigé :', os.path.relpath(chemin, SEEDVC))


def installer_seedvc(outils, seedvc=SEEDVC):
    """Seed-VC au commit figé (archive du montage d'outils, sinon clonage), ses poids (checkpoints.tar du montage, sinon
    Hugging Face au premier appel de train.py), les mêmes retouches que doubler_kaggle.py, et quelques paquets."""
    fiche = lire_outils(outils)
    archive = os.path.join(outils, 'seed-vc.tar') if outils else None
    if archive and os.path.exists(archive) and fiche.get('commit') == COMMIT:
        lancer('tar', '-xf', archive, '-C', os.path.dirname(seedvc))
        log('outils : Seed-VC', COMMIT[:10], 'depuis le montage')
    else:
        os.makedirs(seedvc, exist_ok=True)
        lancer('git', 'init', '-q', seedvc)
        lancer('git', '-C', seedvc, 'remote', 'add', 'origin', DEPOT)
        lancer('git', '-C', seedvc, 'fetch', '-q', '--depth', '1', 'origin', COMMIT)
        lancer('git', '-C', seedvc, 'checkout', '-q', 'FETCH_HEAD')
        log('Seed-VC', COMMIT[:10], 'cloné')
    if outils and os.path.exists(os.path.join(outils, 'checkpoints.tar')):
        lancer('tar', '-xf', os.path.join(outils, 'checkpoints.tar'), '-C', seedvc)
        log('outils : poids Seed-VC, Whisper, BigVGAN, RMVPE, CAMPPlus depuis le montage')
    corriger(os.path.join(seedvc, 'modules/bigvgan/bigvgan.py'),
             [('proxies: Optional[Dict],', 'proxies: Optional[Dict] = None,'), ('resume_download: bool,', 'resume_download: bool = None,'),
              ('proxies=proxies,', ''), ('resume_download=resume_download,', '')])
    manquants = [p for module, p in (('librosa', 'librosa'), ('soundfile', 'soundfile'), ('einops', 'einops'),
                                     ('yaml', 'pyyaml'), ('scipy', 'scipy'), ('transformers', 'transformers'),
                                     ('huggingface_hub', 'huggingface_hub'), ('tqdm', 'tqdm'), ('munch', 'munch'),
                                     ('matplotlib', 'matplotlib'))
                 if __import__('importlib.util').util.find_spec(module) is None]
    if manquants:
        log('installation :', ' '.join(manquants))
        lancer(sys.executable, '-m', 'pip', 'install', '-q', *manquants)
    if not shutil.which('ffmpeg'):
        lancer(sys.executable, '-m', 'pip', 'install', '-q', 'imageio-ffmpeg')
        import imageio_ffmpeg
        os.symlink(imageio_ffmpeg.get_ffmpeg_exe(), '/usr/local/bin/ffmpeg')
    # length_regulator.py importe dac.nn.quantize sans s'en servir : une souche suffit (comme doubler_kaggle.py)
    souches = '/tmp/souches'
    if __import__('importlib.util').util.find_spec('dac') is None:
        os.makedirs(os.path.join(souches, 'dac', 'nn'), exist_ok=True)
        for f, texte in (('dac/__init__.py', ''), ('dac/nn/__init__.py', ''),
                         ('dac/nn/quantize.py', 'class VectorQuantize:\n    def __init__(self, *a, **k):\n'
                                                '        raise RuntimeError("souche dac : VectorQuantize non prévu")\n')):
            with open(os.path.join(souches, f), 'w') as fh:
                fh.write(texte)
        return {'PYTHONPATH': souches}
    return {}


def verifier_gpu():
    if not any(f.startswith('nvidia') for f in os.listdir('/dev')):
        log("ACCÈS REFUSÉ : GPU indisponible sur ce noyau. Vérifier le numéro de téléphone du compte "
            '(kaggle.com/settings, Phone verification), puis relancer.')
        sys.exit(3)


# --- Programme -------------------------------------------------------------------------------------------------------

def trouver_prises(R):
    """Prises du jeu de données (dossier chant/), ou du dossier « entrees » des réglages (essais)."""
    base = R.get('entrees')
    dossiers = [base] if base else sorted({os.path.dirname(f) for f in glob.glob('/kaggle/input/**/chant/*', recursive=True)})
    prises = []
    for d in dossiers:
        prises += [f for f in glob.glob(os.path.join(d, '*')) if re.search(r'\.(wav|flac|mp3|m4a|ogg|opus)$', f, re.I)]
    return sorted(prises)


def restaurer_reprise(R, run_dir):
    """Point de contrôle complet laissé dans le jeu de données (reprise/) par une session précédente : remis dans le dossier
    d'exécution. Rend le pas repris, ou 0."""
    base = R.get('entrees_reprise')
    trouves = glob.glob(os.path.join(base, 'DiT_epoch_*_step_*.pth')) if base else \
        glob.glob('/kaggle/input/**/reprise/DiT_epoch_*_step_*.pth', recursive=True)
    if not trouves:
        return 0
    pas, chemin = max((int(MOTIF_POINT.search(f).group(2)), f) for f in trouves if MOTIF_POINT.search(f))
    os.makedirs(run_dir, exist_ok=True)
    shutil.copy2(chemin, os.path.join(run_dir, os.path.basename(chemin)))
    log(f'reprise : point de contrôle de l\'étape {pas} remis dans {run_dir}')
    return pas


def commande_train(R, run, preset, donnees):
    prefixe = R.get('commande_train') or [sys.executable, 'train.py']
    return [*prefixe, '--config', preset, '--dataset-dir', donnees, '--run-name', run,
            '--batch-size', str(reglage(R, 'batch')), '--max-steps', str(reglage(R, 'max_steps')),
            '--max-epochs', '100000', '--save-every', str(reglage(R, 'sauvegarde_tous')), '--num-workers', '0', '--gpu', '0']


def main(R=None):
    R = REGLAGES if R is None else R
    simulation = bool(R.get('simulation'))
    voix = R.get('voix') or 'voix'
    sortie, tmp, seedvc = R.get('sortie') or SORTIE, R.get('tmp') or TMP, R.get('seedvc') or SEEDVC
    fin = DEBUT + reglage(R, 'budget_s') - reglage(R, 'marge_s')   # heure où train.py doit s'arrêter
    rapport = {'format': 1, 'voix': voix, 'simulation': simulation, 'reglages': {k: reglage(R, k) for k in DEFAUTS},
               'commit_seedvc': COMMIT, 'preset': PRESET, 'temps': {}, 'complet': False}
    chemin_rapport = os.path.join(sortie, 'rapport.json')

    def ecrire():
        rapport['temps']['total_s'] = round(time.time() - DEBUT, 1)
        ecrire_json(chemin_rapport, rapport)
    if not simulation:
        verifier_gpu()
    # 1. Données
    t = time.time()
    prises = trouver_prises(R)
    log(f'{len(prises)} prise(s) de chant')
    if not prises:
        rapport['erreur'] = 'aucune prise de chant dans le jeu de données (dossier chant/)'
        ecrire()
        log('FICHIER ABSENT :', rapport['erreur'])
        return 4
    donnees = os.path.join(tmp, 'donnees')
    rapport['donnees'] = preparer_donnees(prises, donnees, R)
    rapport['temps']['donnees_s'] = round(time.time() - t, 1)
    d = rapport['donnees']
    log(f"données : {d['morceaux']} morceau(x), {d['utile_s']:.0f} s de chant utile, refus {d['refuses']}")
    avert = []
    if d['utile_s'] < reglage(R, 'utile_conseil_s'):
        avert.append(f"seulement {d['utile_s']:.0f} s de chant exploitable (5 à 10 min conseillées)")
    rapport['avertissements'] = avert
    if d['utile_s'] < reglage(R, 'utile_min_s'):
        rapport['erreur'] = f"pas assez de chant exploitable ({d['utile_s']:.0f} s ; au moins {reglage(R, 'utile_min_s'):.0f} s)"
        ecrire()
        log('ARRÊT :', rapport['erreur'])
        return 5
    ecrire()
    # 2. Seed-VC
    t = time.time()
    env = dict(os.environ, TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD='1')
    if not simulation:
        outils = trouver_outils()
        rapport['outils'] = {'monte': bool(outils), 'date': lire_outils(outils).get('date')}
        extra = installer_seedvc(outils, seedvc)
        if extra.get('PYTHONPATH'):
            env['PYTHONPATH'] = extra['PYTHONPATH'] + os.pathsep + env.get('PYTHONPATH', '')
        import torch
        rapport['torch'], rapport['gpu'] = torch.__version__, torch.cuda.get_device_name(0)
    rapport['temps']['installation_s'] = round(time.time() - t, 1)
    # 3. Entraînement
    runs = os.path.join(sortie, 'runs')
    run_dir = os.path.join(runs, voix)
    os.makedirs(runs, exist_ok=True)
    lien = os.path.join(seedvc, 'runs')   # train.py écrit dans ./runs/<run-name> : le dossier des sorties du noyau
    if not os.path.lexists(lien):
        os.makedirs(seedvc, exist_ok=True)
        os.symlink(runs, lien)
    rapport['reprise'] = {'pas_repris': restaurer_reprise(R, run_dir)}
    t = time.time()
    pertes = []
    r = entrainer(commande_train(R, voix, PRESET, donnees), seedvc, run_dir, fin, pertes, R, env)
    rapport['entrainement'] = {**r, 'duree_s': round(time.time() - t, 1), 'pertes': resume_pertes(pertes)}
    rapport['temps']['entrainement_s'] = rapport['entrainement']['duree_s']
    ecrire()
    # 4. Point de contrôle compact
    final = os.path.join(run_dir, 'ft_model.pth')
    pas, source = dernier_point(run_dir)
    if not os.path.exists(final):
        final = source
    if not final:
        rapport['erreur'] = "train.py n'a laissé aucun point de contrôle"
        ecrire()
        log('ÉCHEC :', rapport['erreur'])
        return 6
    os.makedirs(os.path.join(sortie, 'affinage'), exist_ok=True)
    cfg = os.path.join(run_dir, os.path.basename(PRESET))
    if not os.path.exists(cfg) and os.path.exists(os.path.join(seedvc, PRESET)):
        cfg = os.path.join(seedvc, PRESET)
    t = time.time()
    rapport['point'] = {'source': os.path.basename(final), 'pas': reglage(R, 'max_steps') if r['termine'] else pas,
                        **compacter(final, os.path.join(sortie, 'affinage', f'{voix}.pth'), simulation)}
    if os.path.exists(cfg):
        shutil.copy2(cfg, os.path.join(sortie, 'affinage', f'{voix}.yml'))
    rapport['temps']['compactage_s'] = round(time.time() - t, 1)
    rapport['complet'] = bool(r['termine'])
    # Fini : plus de reprise à garder. Pas fini : le dernier point complet part avec les sorties (reprise/)
    if rapport['complet']:
        shutil.rmtree(run_dir, ignore_errors=True)
    else:
        os.makedirs(os.path.join(sortie, 'reprise'), exist_ok=True)
        if source:
            shutil.copy2(source, os.path.join(sortie, 'reprise', os.path.basename(source)))
        shutil.rmtree(run_dir, ignore_errors=True)
        rapport['avertissements'].append('affinage inachevé (budget de temps ou plantage) : relancer reprend au dernier point')
    shutil.rmtree(tmp, ignore_errors=True)
    ecrire()
    log('AFFINAGE ' + ('terminé' if rapport['complet'] else 'inachevé') + ' : ' + json.dumps(
        {k: rapport[k] for k in ('point', 'temps')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
