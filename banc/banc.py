#!/usr/bin/env python3
"""Banc d'essai « qualité maximale » de la conversion de voix chantée.

  banc.py preparer                 extraits (~30 s) et prises de la voix cible, sur le PC (léger)
  banc.py envoyer [--stems S]      jeu de données privé <compte>/doublage-banc-entrees (une version par appel) ;
                                   --stems S ajoute les voix séparées rapatriées de la session S
  banc.py lancer S                 noyau GPU privé <compte>/doublage-banc, étapes de sessions/S.json, suivi et
                                   rapatriement dans resultats/S/ (les points de contrôle .pth restent sur Kaggle)
  banc.py rapatrier S              rapatrie à nouveau la sortie du noyau de la session S
  banc.py modele S FICHIER         rapatrie un point de contrôle de la session S et le range dans le jeu privé
                                   <compte>/doublage-voix-modeles
  banc.py mixer                    mix (chaîne de doubler, identique pour toutes les versions) et écoute à l'aveugle
                                   (ecoute/<extrait>/A.mp3…, A_voix.mp3…, ecoute/correspondance.json)
  banc.py tableau                  tableau des mesures (Markdown) de toutes les sessions

  banc.py rapport S                temps et écarts d'une session du banc vitesse (Markdown) : séparation, nettoyage,
                                   conversion, juge, et SDR des versions de même graine à la version de référence

Extraits et voix cible : banc/cas.json, local et jamais publié (modèle : banc/cas.exemple.json), ou --cas FICHIER.
Un extrait donne soit « original » et « voix » (voix déjà séparée), soit « video » (le son en est tiré par ffmpeg,
de debut à fin, et séparé sur le GPU par l'étape separer_voix). Chemins relatifs : par rapport au dossier des données
de doubler (voix/, sorties/). Compte Kaggle : « kaggle.compte » de ~/.config/voix-la/config.json, comme doubler
(doubler --config kaggle.compte <nom>) ; noms du jeu et du noyau : « kaggle » du fichier des cas, sinon ceux du banc.
Dossier de travail du banc (cas.json, sessions/, entrees/, travail/, resultats/, ecoute/) : $BANC_DONNEES, sinon banc/.
Les sessions GPU prennent le verrou Kaggle de doubler (~/.local/state/voix-la/verrou-kaggle) et attendent qu'aucune
autre session ne tourne sur le compte : une seule session GPU à la fois.
Session « base » : « depot » prend le noyau actuel du dépôt (kaggle/doubler_kaggle.py), sinon la copie figée du 03/11.

Rien d'autre que les extraits audio et les enregistrements de la voix cible ne part sur Kaggle. Le calcul lourd
(séparation, conversion, affinage, juge) tourne sur le GPU de Kaggle (banc/noyau_banc.py) ; sur le PC, seulement des
extraits, du numpy léger et des mix ffmpeg courts (nice).
"""
import argparse
import base64
import datetime
import fcntl
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

CODE = Path(__file__).resolve().parent   # scripts du banc (noyau_banc.py)
DEPOT = CODE.parent
BANC = Path(os.environ['BANC_DONNEES']).expanduser() if os.environ.get('BANC_DONNEES') else CODE   # dossier de travail
# Copie figée du noyau de doubler du 03/11/2026 (md5 e4e206f3…) : V0 = la chaîne actuelle, même si doubler évolue ;
# une session « base » : « depot » prend le noyau actuel du dépôt
BASE_NOYAU = BANC / 'base' / 'doubler_kaggle_2026-11-03.py'
NOYAU_DEPOT = DEPOT / 'kaggle' / 'doubler_kaggle.py'
VERROU_KAGGLE = Path.home() / '.local' / 'state' / 'voix-la' / 'verrou-kaggle'   # celui de doubler (une session à la fois)

KAGGLE = Path.home() / 'kaggle-venv/bin/kaggle'
CONFIG = Path.home() / '.config' / 'voix-la' / 'config.json'   # celui de doubler : « donnees », « kaggle.compte »
CAS_DEFAUT = BANC / 'cas.json'   # extraits et voix cible (local, jamais publié) ; modèle : cas.exemple.json
# Identifiants Kaggle <compte>/<nom> : le compte est lu dans CONFIG (jamais dans le code : le dépôt est public)
NOMS_KAGGLE = {'jeu': 'doublage-banc-entrees', 'noyau': 'doublage-banc', 'outils': 'doublage-outils',
               'jeu_modeles': 'doublage-voix-modeles',
               'noyau_doublage': 'doublage-voix-gpu'}   # celui de doubler : jamais touché, seulement compté
ENTREES = BANC / 'entrees'
TRAVAIL = BANC / 'travail'
RESULTATS = BANC / 'resultats'
SESSIONS = BANC / 'sessions'
ECOUTE = BANC / 'ecoute'
SR = 44100

# Fixés par configurer_kaggle() : <compte>/<nom>
COMPTE = JEU = NOYAU = OUTILS = JEU_MODELES = NOYAU_DOUBLAGE = None
# Fixés par configurer() d'après le fichier des cas (cas.json). Extraits : bornes aux silences de la voix (enveloppe
# vérifiée), échantillons calés sur les mêmes indices pour l'original, la voix séparée et l'instrumental. Voix cible :
# prises distinctes (pas de dérivés d'une même prise), la prise gardée de côté hors entraînement sert de voix cible au juge.
DONNEES = DEPOT
CIBLE = 'cible'   # identifiant de la voix cible : préfixe des fichiers du jeu, clé des mesures du juge
REFERENCE_A = None
REFERENCES_EN_PLUS = {}   # autres références de la voix cible : {lettre : chemin} -> <cible>-reference-<lettre>.wav
TITRES = {}               # titres des extraits dans l'Écoute à l'aveugle (facultatif)
PRISES = None     # dossier des enregistrements de la voix cible
PRISES_ENTRAINEMENT, PRISES_VIDES, PRISE_TENUE, DESCRIPTION_MODELE = [], [], None, None
EXTRAITS = {}


def lire_config():
    try:
        conf = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}
    return conf if isinstance(conf, dict) else {}


def dossier_donnees(reference):
    """Données de doubler (voix/, sorties/) : $VOIXLA_DONNEES, sinon « donnees » de CONFIG, sinon le dépôt (ancien
    emplacement), sinon ~/Music/Voix-là ; le premier qui contient la référence de la voix cible (chemin relatif)."""
    candidats = [os.environ.get('VOIXLA_DONNEES'), lire_config().get('donnees')]
    temoin = Path(reference) if not Path(reference).expanduser().is_absolute() else Path('voix')
    for c in [Path(x).expanduser() for x in candidats if x] + [DEPOT, Path.home() / 'Music' / 'Voix-là']:
        if (c / temoin).exists():
            return c
    return DEPOT


def chemin_donnees(x):
    """Chemin du fichier des cas : « ~ » développé, relatif = par rapport au dossier des données."""
    p = Path(x).expanduser()
    return p if p.is_absolute() else DONNEES / p


def configurer(fichier):
    """Lit le fichier des cas (voix cible, extraits) et fixe les globales correspondantes."""
    global DONNEES, CIBLE, REFERENCE_A, PRISES, PRISES_ENTRAINEMENT, PRISES_VIDES, PRISE_TENUE, DESCRIPTION_MODELE, EXTRAITS
    global REFERENCES_EN_PLUS, TITRES
    fichier = Path(fichier).expanduser()
    try:
        cas = json.loads(fichier.read_text())
    except FileNotFoundError:
        raise SystemExit(f'{fichier} absent : copier banc/cas.exemple.json en banc/cas.json et l\'adapter '
                         '(ou banc.py --cas FICHIER)')
    except ValueError as e:
        raise SystemExit(f'{fichier} illisible ({e})')
    try:
        v = cas['voix']
        CIBLE = str(v['nom'])
        DONNEES = dossier_donnees(v['reference'])
        REFERENCE_A = chemin_donnees(v['reference'])
        REFERENCES_EN_PLUS = {str(k): chemin_donnees(c) for k, c in (v.get('references_en_plus') or {}).items()}
        PRISES = chemin_donnees(v['dossier_prises'])
        PRISES_ENTRAINEMENT, PRISES_VIDES = list(v.get('prises_entrainement', [])), list(v.get('prises_vides', []))
        PRISE_TENUE = v['prise_tenue']
        DESCRIPTION_MODELE = v.get('description_modele', f'voix cible « {CIBLE} »')
        EXTRAITS = {ex: {'titre': e['titre'], 'debut': e['debut'], 'fin': e['fin'], 'decalage': e['decalage'],
                         'video': Path(e['video']).expanduser() if e.get('video') else None,
                         'original': chemin_donnees(e['original']) if e.get('original') else None,
                         'voix': chemin_donnees(e['voix']) if e.get('voix') else None,
                         'paroles': e.get('paroles')} for ex, e in cas['extraits'].items()}
        TITRES = {ex: e['titre_ecoute'] for ex, e in cas['extraits'].items() if e.get('titre_ecoute')}
        for ex, e in EXTRAITS.items():
            if not e['video'] and not (e['original'] and e['voix']):
                raise KeyError(f'extrait {ex} : « video », ou « original » et « voix »')
        for cle, nom in (cas.get('kaggle') or {}).items():   # autres noms du jeu et du noyau (sans le compte)
            if cle in NOMS_KAGGLE and re.fullmatch(r'[a-z0-9-]+', str(nom)):
                NOMS_KAGGLE[cle] = str(nom)
    except (KeyError, TypeError) as e:
        raise SystemExit(f'{fichier} : champ manquant ou mal formé ({e!r}) ; voir banc/cas.exemple.json')
    if not re.fullmatch(r'[\w.-]+', CIBLE):
        raise SystemExit(f'{fichier} : voix.nom « {CIBLE} » : lettres, chiffres, « . », « _ » ou « - » seulement')
    for ex in EXTRAITS:   # le noyau retrouve l'extrait dans les noms <session>-<extrait>-<fichier> : [a-z]+
        if not re.fullmatch('[a-z]+', ex):
            raise SystemExit(f'{fichier} : extrait « {ex} » : lettres minuscules sans accent seulement (a-z)')


def configurer_kaggle():
    """Identifiants <compte>/<nom> du banc, d'après « kaggle.compte » de CONFIG (le même compte que doubler)."""
    global COMPTE, JEU, NOYAU, OUTILS, JEU_MODELES, NOYAU_DOUBLAGE
    k = lire_config().get('kaggle')
    COMPTE = str((k if isinstance(k, dict) else {}).get('compte') or '').strip()
    if not COMPTE:
        raise SystemExit(f'compte Kaggle non configuré : « kaggle.compte » de {CONFIG} '
                         '(doubler --config kaggle.compte <nom_du_compte_kaggle>)')
    JEU, NOYAU, OUTILS, JEU_MODELES, NOYAU_DOUBLAGE = (f'{COMPTE}/{NOMS_KAGGLE[n]}' for n in
                                                        ('jeu', 'noyau', 'outils', 'jeu_modeles', 'noyau_doublage'))


def dire(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def kaggle(*args):
    """Comme doubler.kaggle() : une limite de temps sur chaque appel (2 min pour un état ou une liste, 30 min pour un
    envoi) ; l'expiration compte comme une panne et l'appelant réessaie. Le jeton n'est jamais lu ni affiché ici."""
    court = args[:2] in (('kernels', 'status'), ('datasets', 'status'), ('datasets', 'files'), ('kernels', 'list'),
                         ('datasets', 'list'))
    limite = 120 if court else 1800
    try:
        r = subprocess.run([str(KAGGLE), *args], capture_output=True, text=True, timeout=limite, cwd=str(BANC))
    except subprocess.TimeoutExpired:
        return 124, f'kaggle {" ".join(args[:2])} : pas de réponse en {limite} s (réseau ?)'
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def kaggle_obstine(*args, essais=6, pause=30, ok=lambda code, texte: code == 0):
    for i in range(essais):
        code, texte = kaggle(*args)
        if ok(code, texte):
            return code, texte
        dire(f'kaggle {" ".join(args[:2])} : échec ({texte.strip()[-200:]}), nouvel essai dans {pause} s')
        time.sleep(pause)
    return code, texte


# --- Préparation (PC, léger) -----------------------------------------------------------------------------------------

def lire_extrait(chemin, debut, fin):
    import soundfile as sf
    a, b = int(round(debut * SR)), int(round(fin * SR))
    x, sr = sf.read(str(chemin), start=a, stop=b, dtype='float32', always_2d=True)
    assert sr == SR, (chemin, sr)
    return x


def nettoyer_parole(y, sr, cible_db=-20.0, coupe_max_s=12.0):
    """Parole de la voix cible prête pour l'affinage : passe-haut 70 Hz, découpe aux pauses en morceaux de 3 à 12 s, pauses
    internes ramenées à 0,2 s au plus, bords à 0,1 s, niveau RMS de la parole active ramené à cible_db (crête 0,95)."""
    import numpy as np
    import scipy.ndimage
    import scipy.signal
    b, a = scipy.signal.butter(4, 70, 'highpass', fs=sr)
    y = scipy.signal.filtfilt(b, a, y).astype(np.float32)
    h = int(0.02 * sr)
    n = len(y) // h
    e = 10 * np.log10((y[:n * h].reshape(n, h) ** 2).mean(1) + 1e-12)
    seuil = max(float(np.percentile(e, 95)) - 35, float(np.percentile(e, 10)) + 6, -65.0)   # au-dessus du bruit de fond
    actif = e > seuil
    # bouche les trous de moins de 0,12 s (consonnes, micro-pauses) et efface les éclats de moins de 0,1 s
    actif = scipy.ndimage.binary_closing(actif, np.ones(6, bool))
    actif = scipy.ndimage.binary_opening(actif, np.ones(5, bool))
    zones, i = [], 0
    while i < n:
        if actif[i]:
            j = i
            while j < n and actif[j]:
                j += 1
            zones.append([i, j])
            i = j
        else:
            i += 1
    if not zones:
        return [], {}
    morceaux, cur = [], [zones[0]]
    for z in zones[1:]:
        long_cur = (cur[-1][1] - cur[0][0]) * 0.02
        pause = (z[0] - cur[-1][1]) * 0.02
        if long_cur >= 3 and (pause >= 0.35 or long_cur + (z[1] - z[0]) * 0.02 > coupe_max_s):
            morceaux.append(cur)
            cur = [z]
        else:
            cur.append(z)
    morceaux.append(cur)
    marge, pause_max = int(0.1 * sr), int(0.2 * sr)
    sorties = []
    for m in morceaux:
        bouts = []
        for k, (zi, zj) in enumerate(m):
            a0, b0 = max(0, zi * h - marge), min(len(y), zj * h + marge)
            if k and m[k - 1][1] * h + marge < a0:
                trou = y[m[k - 1][1] * h + marge:a0]
                bouts.append(trou[:pause_max] if len(trou) > pause_max else trou)
            elif k:
                a0 = max(a0, m[k - 1][1] * h + marge)
            bouts.append(y[a0:b0])
        z = np.concatenate(bouts)
        if len(z) < 1.0 * sr:
            continue
        # niveau : RMS sur les trames actives du morceau
        nz = len(z) // h
        ez = (z[:nz * h].reshape(nz, h) ** 2).mean(1)
        rms = float(np.sqrt(ez[10 * np.log10(ez + 1e-12) > seuil].mean() + 1e-12))
        g = 10 ** (cible_db / 20) / max(rms, 1e-6)
        g = min(g, 0.95 / max(float(np.abs(z).max()), 1e-6))
        sorties.append((z * g).astype(np.float32))
    info = {'seuil_db': round(seuil, 1), 'parole_s': round(float(actif.mean() * len(y) / sr), 1), 'duree_s': round(len(y) / sr, 1)}
    return sorties, info


def correlation(a, b):
    import numpy as np
    a, b = a.astype(np.float64).ravel(), b.astype(np.float64).ravel()
    return float(a @ b / np.sqrt((a @ a) * (b @ b) + 1e-20))


def extraire_video(ex, e, fiche=None):
    """Son d'un extrait tiré de sa vidéo par ffmpeg seul (la vidéo n'est jamais lue autrement) : 44,1 kHz stéréo
    flottant, de debut à fin (recherche précise sur le son). La voix sera séparée sur le GPU (étape separer_voix)."""
    t = TRAVAIL / ex
    t.mkdir(parents=True, exist_ok=True)
    sortie = t / 'original.wav'
    ff('-ss', f"{e['debut']:.3f}", '-t', f"{e['fin'] - e['debut']:.3f}", '-i', e['video'], '-map', '0:a:0', '-vn',
       '-ac', '2', '-ar', str(SR), '-c:a', 'pcm_f32le', sortie)
    if fiche is not None:
        fiche['extraits'][ex] = {'titre': e['titre'], 'debut_s': e['debut'], 'fin_s': e['fin'],
                                 'duree_s': round(e['fin'] - e['debut'], 2), 'decalage_dt': e['decalage'],
                                 'duree_extraite_s': round(duree_s(sortie), 3)}
    dire(f"extrait {ex} : {e['debut']:.2f}–{e['fin']:.2f} s ({e['fin'] - e['debut']:.1f} s) tiré de la vidéo, "
         f"décalage {e['decalage']:+d}")


def extraire(fiche=None):
    """Extraits sur le PC : original et voix séparée aux mêmes indices ; musique seule = original - voix, calculée en numpy.
    Jamais d'amix à poids négatif (ffmpeg 6.1.1 ignore le signe : « 1 -1 » additionne) ni de instrumental.wav lu tel quel
    (celui de doubler valait original + voix jusqu'au 04/11). Garde-fou : corrélation musique seule / voix < 0,3.
    Extrait tiré d'une vidéo : le son seulement (extraire_video)."""
    for ex, e in EXTRAITS.items():
        if e['video']:
            extraire_video(ex, e, fiche)
            continue
        t = TRAVAIL / ex
        t.mkdir(parents=True, exist_ok=True)
        import soundfile as sf
        o = lire_extrait(e['original'], e['debut'], e['fin'])
        v = lire_extrait(e['voix'], e['debut'], e['fin'])
        i = o - v
        c = correlation(i, v)
        if abs(c) >= 0.3:
            raise SystemExit(f'{ex} : musique seule corrélée à la voix ({c:.3f}) : soustraction fausse')
        for nom, x in (('original.wav', o), ('voix_source.wav', v), ('instrumental.wav', i)):
            sf.write(str(t / nom), x, SR, subtype='FLOAT')
        if fiche is not None:
            fiche['extraits'][ex] = {'titre': e['titre'], 'debut_s': e['debut'], 'fin_s': e['fin'], 'duree_s': round(e['fin'] - e['debut'], 2),
                                     'decalage_dt': e['decalage'], 'echantillons': len(o), 'voix': str(e['voix']),
                                     'correlation_musique_voix': round(c, 3)}
        dire(f"extrait {ex} : {e['debut']:.2f}–{e['fin']:.2f} s ({e['fin'] - e['debut']:.1f} s), décalage {e['decalage']:+d}, "
             f"corrélation musique seule / voix {c:.3f}")


def preparer(a):
    import numpy as np
    import soundfile as sf
    if a.extraits_seulement:
        return extraire()
    jeu = ENTREES / 'jeu'
    shutil.rmtree(jeu, ignore_errors=True)
    jeu.mkdir(parents=True)
    fiche = {'date': time.strftime('%Y-%m-%d %H:%M'), 'extraits': {}, CIBLE: {}}
    sources = all(e['video'].exists() if e['video'] else all(e[k].exists() for k in ('original', 'voix'))
                  for e in EXTRAITS.values())
    deja = all((TRAVAIL / ex / ('original.wav' if e['video'] else 'voix_source.wav')).exists() for ex, e in EXTRAITS.items())
    if sources:
        extraire(fiche)
    elif deja:   # p. ex. réaffinage plus tard, sorties de doubler déplacées : les extraits du banc ne changent pas
        dire('sources des extraits introuvables : extraits déjà faits dans banc/travail/ réutilisés')
    else:
        raise SystemExit('sources des extraits introuvables et banc/travail/ vide')
    for ex, e in EXTRAITS.items():
        if e['video']:   # son d'origine : la voix sera séparée sur le GPU
            shutil.copy2(TRAVAIL / ex / 'original.wav', jeu / f'extrait-{ex}-original.wav')
        else:
            shutil.copy2(TRAVAIL / ex / 'voix_source.wav', jeu / f'extrait-{ex}-voix.wav')
    shutil.copy2(REFERENCE_A, jeu / f'{CIBLE}-reference-A.wav')
    for lettre, chemin in REFERENCES_EN_PLUS.items():
        shutil.copy2(chemin, jeu / f'{CIBLE}-reference-{lettre}.wav')
    prises = [Path(p).expanduser() for p in a.prises] if a.prises else [PRISES / p for p in PRISES_ENTRAINEMENT]
    tenue_prise = Path(a.tenue).expanduser() if a.tenue else PRISES / PRISE_TENUE
    if tenue_prise in prises:
        raise SystemExit(f'{tenue_prise.name} est à la fois dans l\'entraînement et gardée pour la mesure')
    total, k = 0.0, 0
    for chemin in prises:
        prise = chemin.name
        y, sr = sf.read(str(chemin), dtype='float32', always_2d=True)
        if y.std() < 10 ** (-50 / 20) or float(np.abs(y).max()) < 10 ** (-40 / 20):
            dire(f'{prise} : prise vide ou presque (niveau {20 * np.log10(y.std() + 1e-12):.0f} dBFS), écartée')
            fiche[CIBLE][prise] = {'ecartee': 'vide'}
            continue
        morceaux, info = nettoyer_parole(y.mean(1), sr, coupe_max_s=a.morceau_max)
        for z in morceaux:
            k += 1
            sf.write(str(jeu / f'{CIBLE}-ft-{k:03d}.wav'), z, sr, subtype='PCM_16')
        d = sum(len(z) for z in morceaux) / sr
        total += d
        fiche[CIBLE][prise] = {**info, 'morceaux': len(morceaux), 'gardee_s': round(d, 1), 'frequence_hz': sr}
        dire(f'{prise} : {len(morceaux)} morceaux, {d:.1f} s gardées sur {info.get("duree_s")} s')
    y, sr = sf.read(str(tenue_prise), dtype='float32', always_2d=True)
    morceaux, info = nettoyer_parole(y.mean(1), sr)
    tenue = np.concatenate([np.concatenate([z, np.zeros(int(0.15 * sr), np.float32)]) for z in morceaux])
    nom_tenue = tenue_prise.stem + '.wav'
    sf.write(str(jeu / f'{CIBLE}-tenue-{nom_tenue}'), tenue, sr, subtype='PCM_16')
    fiche[CIBLE]['tenue'] = {'prise': tenue_prise.name, 'fichier_jeu': f'{CIBLE}-tenue-{nom_tenue}', **info,
                                 'gardee_s': round(len(tenue) / sr, 1)}
    fiche[CIBLE]['prises_vides'] = PRISES_VIDES
    fiche[CIBLE]['entrainement_s'] = round(total, 1)
    fiche[CIBLE]['morceaux'] = k
    dire(f'entraînement : {k} morceaux, {total / 60:.1f} min ; prise gardée de côté : {tenue_prise.name} ({len(tenue) / sr:.1f} s)')
    (ENTREES / 'preparation.json').write_text(json.dumps(fiche, ensure_ascii=False, indent=1))


# --- Kaggle : jeu de données, noyau, suivi ---------------------------------------------------------------------------

def attendre_jeu(slug, fichiers, maxi=25 * 60):
    debut = time.time()
    while True:
        _, liste = kaggle('datasets', 'files', slug, '--page-size', '200')
        _, etat = kaggle('datasets', 'status', slug)
        if all(f in liste for f in fichiers) and re.search('ready', etat, re.I):
            return
        if time.time() - debut > maxi:
            raise RuntimeError(f'Kaggle : jeu {slug} toujours en préparation après {maxi // 60} min')
        time.sleep(15)


def publier_jeu(dossier, slug, message):
    meta = dossier / 'dataset-metadata.json'
    meta.write_text(json.dumps({'title': slug.split('/')[1], 'id': slug, 'licenses': [{'name': 'unknown'}]}))
    fichiers = sorted(p.name for p in dossier.iterdir() if p.name != 'dataset-metadata.json')
    taille = sum((dossier / f).stat().st_size for f in fichiers)
    existe = kaggle('datasets', 'status', slug)[0] == 0
    dire(f'Kaggle : {"nouvelle version du" if existe else "création du"} jeu privé {slug} ({len(fichiers)} fichiers, {taille / 1e6:.0f} Mo)')
    t = time.time()
    code, texte = (kaggle('datasets', 'version', '-p', str(dossier), '-m', message) if existe
                   else kaggle('datasets', 'create', '-p', str(dossier)))   # privé par défaut (pas de --public)
    if code or re.search(r'error|fail|invalid', texte, re.I):
        raise RuntimeError('Kaggle : jeu de données refusé : ' + texte.strip()[-500:])
    envoi_s = time.time() - t
    dire(f'Kaggle : envoi fait en {envoi_s:.0f} s ({taille / 1e6 / max(envoi_s, 1):.1f} Mo/s), attente de la préparation')
    attendre_jeu(slug, fichiers)
    time.sleep(30)   # le noyau monte la dernière version prête
    return {'fichiers': len(fichiers), 'taille_mo': round(taille / 1e6, 1), 'envoi_s': round(envoi_s, 1)}


def envoyer(a):
    jeu = ENTREES / 'jeu'
    for s_prec in (a.sorties or []):
        for ex in EXTRAITS:
            for f in sorted((RESULTATS / s_prec / ex).glob('V*')):
                if f.suffix in ('.flac', '.json') and not f.stem.endswith('_lead_sec'):
                    shutil.copy2(f, jeu / f'{s_prec}-{ex}-{f.name}')
    if a.stems:
        src = RESULTATS / a.stems
        for ex in EXTRAITS:
            for piste in ('lead_sec', 'choeurs_sec'):   # les seules qui servent ensuite (conversion, juge)
                f = src / ex / f'{piste}.wav'
                if not f.exists():
                    raise SystemExit(f'absent : {f}')
                shutil.copy2(f, jeu / f'stem-{ex}-{piste}.wav')
    r = publier_jeu(jeu, JEU, a.message or f'banc {time.strftime("%Y-%m-%d %H:%M")}')
    (ENTREES / 'envoi.json').write_text(json.dumps({**r, 'date': time.strftime('%Y-%m-%d %H:%M')}, indent=1))


def sessions_gpu_actives():
    """Sessions en cours (RUNNING/QUEUED) parmi les noyaux du compte lancés récemment, celle de doubler comprise."""
    code, texte = kaggle_obstine('kernels', 'list', '--mine', '--page-size', '20', '--sort-by', 'dateRun', essais=4)
    refs = re.findall(rf'^({re.escape(COMPTE)}/[\w-]+)', texte, re.M)
    actives = []
    for ref in refs[:12]:
        _, etat = kaggle('kernels', 'status', ref)
        if re.search(r'RUNNING|QUEUED|PENDING|NEW\b', etat):
            actives.append(ref)
    return actives


def construire_noyau(reglages, base_noyau=None):
    """Code du noyau : les fonctions de kaggle/doubler_kaggle.py (copie figée base/doubler_kaggle_2026-11-03.py, ou le
    noyau actuel du dépôt : installation hors ligne, Seed-VC figé, découpage aux silences…), lues telles quelles, sans
    leur bloc final « if __name__ == '__main__' », suivies du code du banc (noyau_banc.py) ; le tout encodé dans un
    lanceur qui l'écrit dans /tmp et l'exécute, chaque étape dans son propre processus (le GPU est libéré entre deux
    étapes)."""
    base = Path(base_noyau or BASE_NOYAU).read_text()
    i = base.rfind("\nif __name__ == '__main__':\n")
    assert i > 0 and not re.search(r'^\S', base[i + 1:].split('\n', 1)[1], re.M), 'fin de doubler_kaggle.py inattendue'
    base = base[:i].rstrip('\n').replace('__REGLAGES__', base64.b64encode(json.dumps(reglages).encode()).decode())
    complet = base + '\n\n\n' + (CODE / 'noyau_banc.py').read_text()
    code = base64.b64encode(complet.encode()).decode()
    return ('"""Banc d\'essai de conversion de voix chantée (banc/banc.py) : lanceur."""\n'
            'import base64, subprocess, sys\n'
            f"SRC = base64.b64decode('{code}').decode()\n"
            "open('/tmp/banc_complet.py', 'w').write(SRC)\n"
            "r = subprocess.run([sys.executable, '/tmp/banc_complet.py', 'orchestrer'])\n"
            "print('LANCEUR fin, code', r.returncode, flush=True)\n")


def noyau_de(session):
    """Chaque session peut avoir son propre noyau (« noyau » dans sessions/<S>.json) : la sortie d'une session précédente,
    points de contrôle compris, reste alors disponible sur Kaggle."""
    try:
        nom = json.loads((SESSIONS / f'{session}.json').read_text()).get('noyau', NOYAU)
    except (OSError, ValueError):
        return NOYAU
    if '/' not in nom:   # nom seul : sur le compte de la configuration
        compte = COMPTE or str((lire_config().get('kaggle') or {}).get('compte') or '').strip()
        nom = f'{compte}/{nom}'
    return nom


class VerrouKaggle:
    """Le verrou Kaggle de doubler (même fichier, même flock) : une seule session GPU à la fois sur le compte, banc et
    doublages confondus. Attend qu'il se libère (un doublage en cours, une autre session du banc)."""

    def __enter__(self):
        VERROU_KAGGLE.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(VERROU_KAGGLE, 'w')
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            dire('Kaggle : un doublage ou une autre session tient le verrou, attente de son tour')
            fcntl.flock(self.f, fcntl.LOCK_EX)
            dire('Kaggle : verrou obtenu')
        return self

    def __exit__(self, *exc):
        self.f.close()
        return False


def lancer(a):
    with VerrouKaggle():   # tenu jusqu'au rapatriement : une seule session GPU à la fois sur le compte
        lancer_sous_verrou(a)


def lancer_sous_verrou(a):
    session = a.session
    etapes = json.loads((SESSIONS / f'{session}.json').read_text())
    noyau = noyau_de(session)
    reglages = {**etapes.get('reglages', {}), 'session': session, 'etapes': etapes['etapes'],
                'extraits': {ex: {'decalage': e['decalage'], 'paroles': e['paroles']} for ex, e in EXTRAITS.items()},
                'limite_totale_s': etapes.get('limite_totale_s', 3600), 'voix_cible': CIBLE}
    envoi = BANC / '.kaggle' / 'envoi-noyau'
    shutil.rmtree(envoi, ignore_errors=True)
    envoi.mkdir(parents=True)
    base = NOYAU_DEPOT if etapes.get('base') == 'depot' else BASE_NOYAU
    (envoi / 'banc_noyau.py').write_text(construire_noyau(reglages, base))
    dire(f'noyau du banc : {base.name} + noyau_banc.py')
    jeux = [JEU] + etapes.get('jeux_en_plus', [])
    (envoi / 'kernel-metadata.json').write_text(json.dumps({
        'id': noyau, 'title': noyau.split('/')[1], 'code_file': 'banc_noyau.py', 'language': 'python', 'kernel_type': 'script',
        'is_private': True, 'enable_gpu': True, 'enable_tpu': False, 'enable_internet': True, 'machine_shape': 'NvidiaTeslaT4',
        'dataset_sources': jeux, 'competition_sources': [], 'kernel_sources': [OUTILS], 'model_sources': []}, indent=1))
    # une seule session GPU à la fois sur le compte : le verrou écarte doubler et le banc ; on attend en plus qu'aucune
    # autre session (lancée à la main sur Kaggle) ne tourne
    debut_attente, dit = time.time(), False
    while True:
        actives = sessions_gpu_actives()
        if not actives:
            break
        if not dit:
            dire(f'Kaggle : {len(actives)} session(s) déjà active(s) ({", ".join(actives)}), attente de leur fin')
            dit = True
        if time.time() - debut_attente > 3 * 3600:
            raise SystemExit('Kaggle : le compte a une session active depuis 3 h')
        time.sleep(120)
    dire('Kaggle : aucune session active avant l\'envoi')
    retour = RESULTATS / session
    retour.mkdir(parents=True, exist_ok=True)
    code, texte = kaggle_obstine('kernels', 'push', '-p', str(envoi), essais=3, pause=60,
                                 ok=lambda c, t: bool(re.search(r'successfully pushed', t, re.I)))
    if not re.search(r'successfully pushed', texte, re.I):
        raise SystemExit('Kaggle : noyau refusé : ' + texte.strip()[-500:])
    pousse = time.time()
    dire('Kaggle : noyau lancé — ' + texte.strip().splitlines()[-1])
    suivi = {'session': session, 'pousse': time.strftime('%Y-%m-%d %H:%M:%S'), 'pousse_epoch': pousse}
    (retour / 'suivi.json').write_text(json.dumps(suivi, indent=1))
    attente_maxi = etapes.get('limite_totale_s', 3600) + 20 * 60
    etat, vu, pannes, dit = '', False, 0, ''
    while True:
        time.sleep(30)
        code, texte = kaggle('kernels', 'status', noyau)
        if code:
            pannes += 1
            if pannes % 5 == 0:
                dire(f'Kaggle : état indisponible ({pannes} fois de suite), on continue d\'essayer')
            if pannes >= 60:   # 30 min sans réponse : on abandonne le suivi (le noyau continue sur Kaggle)
                raise SystemExit('Kaggle : état indisponible depuis 30 min ; relancer « banc.py rapatrier ' + session + ' »')
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
        if time.time() - pousse > attente_maxi:
            dire(f'Kaggle : toujours en cours après {attente_maxi // 60} min ; le noyau s\'arrêtera de lui-même (limite interne)')
            attente_maxi += 15 * 60
    suivi.update({'fin': time.strftime('%Y-%m-%d %H:%M:%S'), 'noyau_depuis_envoi_s': round(time.time() - pousse, 1), 'etat': etat})
    (retour / 'suivi.json').write_text(json.dumps(suivi, indent=1))
    rapatrier_session(session)


def rapatrier_session(session, motif=r'^(?!.*\.pth$).*$'):
    retour = RESULTATS / session
    noyau = noyau_de(session)
    tmp = retour / '.telechargement'
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    t = time.time()
    for _ in range(6):
        code, texte = kaggle('kernels', 'output', noyau, '-p', str(tmp), '--force', '--file-pattern', motif)
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
    dire(f'rapatrié dans {retour} en {time.time() - t:.0f} s')
    journal = []
    for f in retour.glob('*.log'):
        try:
            journal += [l.get('data', '') for l in json.loads(f.read_text())]
        except ValueError:
            pass
    lignes = [l.rstrip() for l in ''.join(journal).splitlines() if l.strip()]
    utiles = [l for l in lignes if re.search(r'ÉTAPE|ECHEC|ÉCHEC|Error|Traceback|MESURE|JUGE|ENTRAINEMENT|LANCEUR', l)]
    print('\n'.join(utiles[-60:]))


def rapatrier(a):
    rapatrier_session(a.session)


def modele(a):
    """Point de contrôle affiné : rapatrié de la sortie du noyau, puis rangé dans le jeu privé JEU_MODELES."""
    dossier = Path(os.environ.get('BANC_MODELES', '/tmp')) / 'doublage-voix-modeles'
    shutil.rmtree(dossier, ignore_errors=True)
    dossier.mkdir(parents=True)
    nom = Path(a.fichier).name
    for _ in range(6):
        code, texte = kaggle('kernels', 'output', noyau_de(a.session), '-p', str(dossier), '--force', '--file-pattern', re.escape(nom) + '$')
        if code == 0 and list(dossier.rglob(nom)):
            break
        dire('Kaggle : rapatriement du modèle en échec, nouvel essai : ' + texte.strip()[-200:])
        time.sleep(30)
    trouve = list(dossier.rglob(nom))
    if not trouve:
        raise SystemExit(f'{nom} introuvable dans la sortie du noyau')
    if trouve[0].parent != dossier:
        shutil.move(str(trouve[0]), str(dossier / nom))
    for p in dossier.iterdir():   # kaggle rapatrie aussi le journal du noyau : seul le modèle part dans le jeu
        if p.name != nom:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    fiche = json.loads((RESULTATS / a.session / 'modeles' / 'modeles.json').read_text())
    info = next((m for m in fiche if Path(m['fichier']).name in (nom, nom.replace('_fp16', ''))), {})
    (dossier / 'LISEZMOI.json').write_text(json.dumps({
        'modele': nom, 'base': 'Seed-VC V1 (commit 51383ef), DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth',
        'config': 'config_dit_mel_seed_uvit_whisper_base_f0_44k.yml (fournie à côté)', 'voix': DESCRIPTION_MODELE,
        'precision': 'fp16 (poids en demi-précision ; inférence en fp16 de toute façon)' if '_fp16' in nom else 'fp32',
        'prise_hors_entrainement': PRISE_TENUE, 'session_banc': a.session, **info,
        'utilisation': "inference.load_models(Namespace(f0_condition=True, checkpoint=<ce fichier>, config=<le yml>, fp16=True))"},
        ensure_ascii=False, indent=1))
    cfg = RESULTATS / a.session / 'modeles' / 'config_dit_mel_seed_uvit_whisper_base_f0_44k.yml'
    shutil.copy2(cfg, dossier / cfg.name)
    r = publier_jeu(dossier, JEU_MODELES, a.message or f'modèle affiné {nom} (banc {a.session})')
    dire(f'modèle rangé dans le jeu privé {JEU_MODELES} : {nom} ({r["taille_mo"]} Mo)')
    shutil.rmtree(dossier, ignore_errors=True)


# --- Mix et écoute à l'aveugle (PC, ffmpeg court) --------------------------------------------------------------------

# Chaîne de mix validée (copie à l'identique de ff(), moyenne_db(), duree_s() et mixer() de doubler.py du 03/11/2026,
# md5 937bd4d1…, elle-même reprise d'un script de mix antérieur) : figée ici pour que toutes les versions
# passent par la même chaîne, même si doubler change de mix entre-temps.

def lancer_cmd(cmd, **k):
    r = subprocess.run(cmd, capture_output=True, text=True, **k)
    if r.returncode:
        raise RuntimeError(f"échec : {' '.join(map(str, cmd[:6]))}…\n{(r.stderr or r.stdout)[-1500:]}")
    return r.stdout


def ff(*args):
    lancer_cmd(['nice', '-n', '10', 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)])


def moyenne_db(fichier):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(fichier), '-af', 'volumedetect', '-f', 'null', '-'],
                       capture_output=True, text=True)
    m = re.search(r'mean_volume: ([-0-9.]+)', r.stderr)
    if not m:
        raise RuntimeError(f'niveau illisible : {fichier}')
    return float(m.group(1))


def duree_s(fichier):
    return float(lancer_cmd(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(fichier)]).strip())


def mixer_doubler(t, tv, mode, voix_db, reverb_db, voix_fichier, sortie_wav, gains=None):
    """Voix sèche (passe-haut, compression douce) au niveau de la voix d'origine + voix_db ; en chant, reverb synthétique
    reverb_db en dessous ; limiteur à 0,95."""
    duree = duree_s(t / 'instrumental.wav')
    hp = 90 if mode == 'chant' else 70
    seche = tv / f'seche_{voix_fichier.stem}.wav'
    ff('-i', voix_fichier, '-af',
       f'highpass=f={hp},acompressor=threshold=-20dB:ratio=2.5:attack=10:release=150,pan=stereo|c0=c0|c1=c0',
       '-c:a', 'pcm_f32le', seche)
    cible = moyenne_db(t / 'voix_source.wav')
    g = dict(gains or {})
    if 'seche' not in g:
        g['seche'] = cible - moyenne_db(seche) + voix_db
    entrees = ['-i', t / 'instrumental.wav', '-i', seche]
    graphe = f"[1:a]volume={g['seche']:.2f}dB[s];"
    if mode == 'chant':
        ir = tv / 'reverb_ir.wav'
        if not ir.exists():
            ff('-f', 'lavfi', '-i', 'anoisesrc=d=1.6:c=pink:r=44100:a=0.5:seed=11', '-f', 'lavfi',
               '-i', 'anoisesrc=d=1.6:c=pink:r=44100:a=0.5:seed=23', '-filter_complex',
               '[0][1]join=inputs=2:channel_layout=stereo,afade=t=out:st=0:d=1.6:curve=exp,highpass=f=250,lowpass=f=6500', ir)
        rev = tv / f'reverb_{voix_fichier.stem}.wav'
        ff('-i', seche, '-i', ir, '-filter_complex', '[0:a]adelay=25|25[d];[d][1:a]afir[w]',
           '-map', '[w]', '-t', f'{duree:.3f}', '-c:a', 'pcm_f32le', rev)
        if 'reverb' not in g:
            g['reverb'] = cible - moyenne_db(rev) + voix_db + reverb_db
        entrees += ['-i', rev]
        graphe += f"[2:a]volume={g['reverb']:.2f}dB[r];[0:a][s][r]amix=inputs=3:normalize=0:duration=first,"
    else:
        graphe += '[0:a][s]amix=inputs=2:normalize=0:duration=first,'
    graphe += 'alimiter=limit=0.95:level=disabled[m]'
    ff(*entrees, '-filter_complex', graphe, '-map', '[m]', '-c:a', 'pcm_s24le', sortie_wav)
    return g, cible


def versions_disponibles():
    """{extrait: {version: chemin de la voix convertie sèche}} pour toutes les sessions rapatriées."""
    v = {}
    for s in sorted(RESULTATS.glob('*/')):
        for ex in EXTRAITS:
            for f in sorted((s / ex).glob('*.json')):
                fiche = json.loads(f.read_text())
                if fiche.get('type') != 'version':
                    continue
                flac = s / ex / f'{f.stem}.flac'
                if flac.exists():
                    v.setdefault(ex, {})[f.stem] = flac
    return v


def preparer_musique_seule(ex):
    """Extrait tiré d'une vidéo : voix séparée sur le GPU (voix.flac de la variante de référence, rapatriée) ; musique
    seule = original - voix, en numpy, avec le même garde-fou de corrélation que extraire()."""
    import numpy as np
    import soundfile as sf
    t = TRAVAIL / ex
    if (t / 'instrumental.wav').exists() and (t / 'voix_source.wav').exists():
        return
    voix = sorted(RESULTATS.glob(f'*/{ex}/voix.flac'))
    if not voix or not (t / 'original.wav').exists():
        raise SystemExit(f'{ex} : voix séparée (resultats/*/{ex}/voix.flac) ou original absents')
    o = sf.read(str(t / 'original.wav'), dtype='float32', always_2d=True)[0]
    v = sf.read(str(voix[-1]), dtype='float32', always_2d=True)[0]
    n = min(len(o), len(v))
    o, v = o[:n], v[:n, :o.shape[1]]
    i = o - v
    c = correlation(i, v)
    if abs(c) >= 0.3:
        raise SystemExit(f'{ex} : musique seule corrélée à la voix ({c:.3f}) : soustraction fausse')
    sf.write(str(t / 'original.wav'), o, SR, subtype='FLOAT')
    sf.write(str(t / 'voix_source.wav'), v, SR, subtype='FLOAT')
    sf.write(str(t / 'instrumental.wav'), i, SR, subtype='FLOAT')
    dire(f'{ex} : musique seule = original - voix séparée ({voix[-1].parent.parent.name}), corrélation {c:.3f}')


def mixer(a):
    import numpy as np
    import soundfile as sf
    ecoute = Path(a.ecoute).expanduser() if a.ecoute else ECOUTE
    ecoute.mkdir(parents=True, exist_ok=True)
    disp = versions_disponibles()
    if a.versions:   # seulement ces versions (les plus prometteuses), dans l'ordre tiré au hasard
        disp = {ex: {v: f for v, f in vs.items() if v in a.versions} for ex, vs in disp.items()}
    if a.extraits:
        disp = {ex: vs for ex, vs in disp.items() if ex in a.extraits}
    # les extraits d'avant (autres bancs) gardent leur correspondance : seuls ceux d'ici sont refaits
    correspondance = lire_json_local(ecoute / 'correspondance.json')
    correspondance.update({'note': "Lettres tirées au hasard, indépendamment pour chaque extrait. Même chaîne de mix pour "
                                   "toutes les versions : celle de doubler (passe-haut 90 Hz, compression douce, voix au niveau "
                                   "moyen de la voix d'origine + 4 dB, reverb bruit rose 1,6 s à -13 dB, limiteur 0,95).",
                           'graine': a.graine})
    if TITRES:
        correspondance.setdefault('titres', {}).update({ex: TITRES[ex] for ex in disp if ex in TITRES})
    alea = random.Random(a.graine)
    for ex, versions in disp.items():
        if not versions:
            continue
        if EXTRAITS.get(ex, {}).get('video'):
            preparer_musique_seule(ex)
        t = TRAVAIL / ex
        tv = TRAVAIL / ex / 'mix'
        shutil.rmtree(tv, ignore_errors=True)
        tv.mkdir(parents=True)
        d = ecoute / ex
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        noms = sorted(versions)
        alea.shuffle(noms)
        lettres = {chr(65 + i): v for i, v in enumerate(noms)}
        correspondance[ex] = lettres
        gains = {}
        for lettre, v in lettres.items():
            src = tv / f'{v}.wav'
            y, sr = sf.read(str(versions[v]), dtype='float32')
            n_ref = sf.info(str(t / 'instrumental.wav')).frames
            y = y[:n_ref] if len(y) >= n_ref else np.pad(y, (0, n_ref - len(y)))
            sf.write(str(src), y, sr, subtype='FLOAT')
            sortie = tv / f'mix_{v}.wav'
            g, cible = mixer_doubler(t, tv, 'chant', 4.0, -13.0, src, sortie)
            gains[v] = g
            ff('-i', sortie, '-c:a', 'libmp3lame', '-b:a', '320k', d / f'{lettre}.mp3')
            # voix sèche seule, au même niveau que dans le mix (passe-haut et compression du mix, sans reverb ni musique)
            ff('-i', tv / f'seche_{v}.wav', '-af', f"volume={g['seche']:.2f}dB,pan=mono|c0=c0,alimiter=limit=0.95:level=disabled",
                       '-c:a', 'libmp3lame', '-b:a', '256k', d / f'{lettre}_voix.mp3')
        ff('-i', t / 'original.wav', '-af', 'alimiter=limit=0.95:level=disabled', '-c:a', 'libmp3lame', '-b:a', '320k',
                   d / 'original.mp3')
        correspondance.setdefault('gains_db', {})[ex] = {v: {k: round(x, 2) for k, x in g.items()} for v, g in gains.items()}
        dire(f'{ex} : ' + ', '.join(f'{l} = {v}' for l, v in lettres.items()))
        shutil.rmtree(tv, ignore_errors=True)
    tmp = ecoute / 'correspondance.json.tmp'
    tmp.write_text(json.dumps(correspondance, ensure_ascii=False, indent=1))
    os.replace(tmp, ecoute / 'correspondance.json')
    dire(f'écoute : {ecoute}/<extrait>/A.mp3… (correspondance dans {ecoute / "correspondance.json"})')


def lire_json_local(chemin):
    try:
        d = json.loads(Path(chemin).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


# --- Tableau des mesures ---------------------------------------------------------------------------------------------

def tableau(a):
    lignes, bases = [], {}
    for s in sorted(RESULTATS.glob('*/')):
        for f in sorted(s.glob('juge*.json')):
            j = json.loads(f.read_text())
            for ex, b in j.get('bases', {}).items():
                bases.setdefault(ex, b)
            for ex, vs in j.get('versions', {}).items():
                for v, m in vs.items():
                    conv = {}
                    for fiche in sorted(RESULTATS.glob(f'*/{ex}/{v}.json')):
                        conv = json.loads(fiche.read_text())
                    lignes = [l for l in lignes if (l[0], l[1]) != (ex, v)] + [(ex, v, m, conv, s.name)]
    print('| Extrait | Version | WavLM cible ↑ | WavLM source ↓ | ECAPA cible ↑ | ECAPA source ↓ | CAMPPlus cible ↑ | '
          'écart F0 médian (cents) ↓ | notes à ±50 c ↑ | accord voisé ↑ | WER/source ↓ | WER/paroles ↓ | aigus >11 kHz (dB) | GPU conv. (s) |')
    print('|' + '---|' * 14)
    fmt = lambda x, p=3: '—' if x is None else (f'{x:.{p}f}' if isinstance(x, float) else str(x))
    # mesures du juge : {<voix cible>: ressemblance à la voix cible, 'source': ressemblance à la voix d'origine}
    cible = lambda d: next((x for k, x in d.items() if k != 'source'), None)
    for ex, v, m, conv, s in sorted(lignes):
        sim = m.get('sim', {})
        f0 = m.get('f0', {})
        print(f"| {ex} | {v} | {fmt(cible(sim.get('wavlm', {})))} | {fmt(sim.get('wavlm', {}).get('source'))} | "
              f"{fmt(cible(sim.get('ecapa', {})))} | {fmt(sim.get('ecapa', {}).get('source'))} | "
              f"{fmt(cible(sim.get('campplus', {})))} | {fmt(f0.get('ecart_median_cents'), 1)} | {fmt(f0.get('part_50c'), 3)} | "
              f"{fmt(f0.get('accord_voise'), 3)} | {fmt(m.get('wer', {}).get('source'), 3)} | {fmt(m.get('wer', {}).get('paroles'), 3)} | "
              f"{fmt(m.get('aigus_db'), 1)} | {fmt(conv.get('conversion_s'), 0)} |")
    print()
    print(json.dumps(bases, ensure_ascii=False, indent=1))


def sdr_local(ref, est):
    import numpy as np
    n = min(len(ref), len(est))
    r, e = np.asarray(ref[:n], np.float64), np.asarray(est[:n], np.float64)
    den = float(((r - e) ** 2).sum())
    return 200.0 if den <= 0 else round(10 * np.log10(max(float((r ** 2).sum()), 1e-30) / den), 2)


def rapport(a):
    """Banc vitesse : tableaux Markdown d'une session (resultats/S) : séparation et nettoyage (temps, mémoire, SDR à la
    variante de référence), conversion (temps, mémoire, profil), juge, et SDR de chaque piste convertie à la même piste
    de la version de référence (a.ref) : même graine, seule la variante change."""
    import soundfile as sf
    r = RESULTATS / a.session
    fmt = lambda x, p=1: '—' if x is None else (f'{x:.{p}f}' if isinstance(x, float) else str(x))
    extraits = sorted(d.name for d in r.iterdir() if d.is_dir() and not d.name.startswith('.'))
    print(f'# Banc vitesse, session {a.session}\n')
    sj = lire_json_local(r / 'session.json')
    if sj:
        print('Étapes : ' + ' ; '.join(f"{e.get('nom')} {fmt(e.get('duree_s'), 0)} s (code {e.get('code')})"
                                       for e in sj.get('etapes', [])) + f" ; noyau {fmt(sj.get('total_s'), 0)} s\n")
    for ex in extraits:
        sep = lire_json_local(r / ex / 'separation_voix.json')
        if sep:
            print(f"## {ex} : séparation ({sep.get('modele')}, {sep.get('duree_s')} s de son, référence {sep.get('reference')})\n")
            print('| Variante | Précision appliquée | Recouvrement | Séparation (s) | Temps / durée | Mémoire GPU (Mo) | '
                  'SDR voix (dB) | SDR reste (dB) | Chargement (s) | Chauffe (s) |')
            print('|' + '---|' * 10)
            for v, m in sep['variantes'].items():
                prec = f"{m.get('precision_effective')}{' + compile' if m.get('compile_effectif') else ''}"
                print(f"| {v} | {prec if not m.get('erreur') else m['erreur'][:60]} | {m.get('recouvrement', '—')} | "
                      f"{fmt(m.get('separation_s'))} | {fmt(m.get('rtf'), 3)} | {fmt(m.get('memoire_gpu_max_mo'))} | "
                      f"{fmt(m.get('sdr_voix_db'))} | {fmt(m.get('sdr_reste_db'))} | {fmt(m.get('chargement_s'))} | "
                      f"{fmt(m.get('chauffe_s'))} |")
            print()
        net = lire_json_local(r / ex / 'nettoyage.json')
        if net:
            print(f"## {ex} : nettoyage ({net.get('duree_s')} s de son, référence {net.get('reference')})\n")
            print('| Variante | Précision appliquée | Recouvrement | Karaoké (s) | Dereverb principale (s) | Dereverb chœurs (s) | '
                  'Total (s) | Temps / durée | SDR principale | SDR principale sèche | SDR chœurs secs | Mémoire (Mo) |')
            print('|' + '---|' * 12)
            for v, m in net['variantes'].items():
                d, s = m.get('durees_s') or {}, m.get('sdr_db') or {}
                k = m.get('karaoke') or {}
                prec = f"{k.get('precision_effective')}{' + compile' if k.get('compile_effectif') else ''}"
                if m.get('karaoke', {}) and k.get('modele') and 'becruily' not in k.get('modele', ''):
                    prec += f" ({k['modele']})"
                print(f"| {v} | {prec if not m.get('erreur') else m['erreur'][:60]} | {m.get('recouvrement', '—')} | "
                      f"{fmt(d.get('karaoke'))} | {fmt(d.get('dereverb_lead'))} | {fmt(d.get('dereverb_choeurs'))} | "
                      f"{fmt(m.get('nettoyage_s'))} | {fmt(m.get('rtf'), 3)} | {fmt(s.get('lead'))} | {fmt(s.get('lead_sec'))} | "
                      f"{fmt(s.get('choeurs_sec'))} | {m.get('memoire_gpu_max_mo') or '—'} |")
            print()
        fiches = {f.stem: lire_json_local(f) for f in sorted((r / ex).glob('*.json'))}
        fiches = {v: f for v, f in fiches.items() if f.get('type') in ('version', 'mesure')}
        if fiches:
            print(f'## {ex} : conversion\n')
            print('| Version | Pas | Graine | Référence | Pistes | Conversion (s) | Temps / s converties | Mémoire (Mo) | '
                  f'SDR à {a.ref} (dB, par piste) | Profil (s) |')
            print('|' + '---|' * 10)
            for v, f in fiches.items():
                pistes = f.get('pistes') or {}
                rtf = [p.get('rtf') for p in pistes.values() if p.get('rtf')]
                mem = [p.get('memoire_gpu_max_mo') for p in pistes.values() if p.get('memoire_gpu_max_mo')]
                sdrs = []
                for piste in pistes:
                    base = piste.split('@')[0]
                    fv = r / ex / (f'{v}_{piste}.flac' if len(pistes) > 1 else f'{v}.flac')
                    fr = r / ex / f'{a.ref}_{base}.flac'
                    if v != a.ref and fv.exists() and fr.exists():
                        sdrs.append(f"{base} {sdr_local(sf.read(str(fr), dtype='float32')[0], sf.read(str(fv), dtype='float32')[0]):.1f}")
                profil = ', '.join(f'{k[:-2]} {x:.1f}' for k, x in (f.get('profil_s') or {}).items())
                print(f"| {v}{' (mesure)' if f.get('type') == 'mesure' else ''} | {f.get('pas')} | {f.get('graine', '—')} | "
                      f"{f.get('reference', '—')} | {', '.join(pistes)} | {fmt(f.get('conversion_s'))} | "
                      f"{fmt(max(rtf) if rtf else None, 3)} | {max(mem) if mem else '—'} | {'; '.join(sdrs) or '—'} | {profil or '—'} |")
            print()
    juges = {}
    for f in sorted(r.glob('juge*.json')):
        j = lire_json_local(f)
        for ex, vs in (j.get('versions') or {}).items():
            juges.setdefault(ex, {}).update(vs)
        for ex, b in (j.get('bases') or {}).items():
            juges.setdefault('_bases', {})[ex] = b
    cible = lambda d: next((x for k, x in (d or {}).items() if k != 'source'), None)
    for ex, vs in sorted(juges.items()):
        if ex == '_bases':
            continue
        print(f'## {ex} : juge\n')
        print('| Version | CAMPPlus cible ↑ | CAMPPlus source ↓ | WavLM cible ↑ | Écart F0 médian (cents) ↓ | Notes à ±50 c ↑ | '
              'Accord voisé ↑ | WER / source ↓ | Aigus > 11 kHz (dB) | Écart spectral (dB) ↓ |')
        print('|' + '---|' * 10)
        for v, m in sorted(vs.items()):
            sim, f0 = m.get('sim') or {}, m.get('f0') or {}
            print(f"| {v} | {fmt(cible(sim.get('campplus')), 3)} | {fmt((sim.get('campplus') or {}).get('source'), 3)} | "
                  f"{fmt(cible(sim.get('wavlm')), 3)} | {fmt(f0.get('ecart_median_cents'))} | {fmt(f0.get('part_50c'), 3)} | "
                  f"{fmt(f0.get('accord_voise'), 3)} | {fmt((m.get('wer') or {}).get('source'), 3)} | {fmt(m.get('aigus_db'))} | "
                  f"{fmt(m.get('ecart_spectral_db'), 2)} |")
        b = (juges.get('_bases') or {}).get(ex) or {}
        print(f"\nBase : aigus de la voix d'origine sèche {fmt(b.get('aigus_db_voix_seche'))} dB, transcription de la voix "
              f"principale sèche : « {b.get('transcription_source', '')[:160]} »\n")


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split('\n', 1)[1])
    p.add_argument('--cas', default=str(CAS_DEFAUT), help='fichier des extraits et de la voix cible (défaut : banc/cas.json ; '
                   'modèle : banc/cas.exemple.json)')
    sp = p.add_subparsers(dest='cmd', required=True)
    pr = sp.add_parser('preparer', help='extraits et prises de la voix cible (jeu de données à envoyer)')
    pr.add_argument('--extraits-seulement', action='store_true', help='refaire seulement banc/travail/ (original, voix, musique seule)')
    pr.add_argument('--prises', nargs='+', help="prises d'entraînement (parole et chant, n'importe quelle fréquence), p. ex. "
                    "prises/chant-*.wav prises/parole-*.wav ; défaut : voix.prises_entrainement du fichier des cas")
    pr.add_argument('--tenue', help='prise gardée hors entraînement pour la mesure (défaut : voix.prise_tenue du fichier des cas)')
    pr.add_argument('--morceau-max', type=float, default=12.0, help='longueur maxi des morceaux (s), 12 par défaut (chant : 15)')
    e = sp.add_parser('envoyer')
    e.add_argument('--stems', help='session dont les voix séparées partent dans le jeu (stem-<extrait>-<piste>.wav)')
    e.add_argument('--sorties', nargs='+', help='sessions dont les voix converties partent dans le jeu, pour être rejugées')
    e.add_argument('--message')
    l = sp.add_parser('lancer')
    l.add_argument('session')
    r = sp.add_parser('rapatrier')
    r.add_argument('session')
    m = sp.add_parser('modele')
    m.add_argument('session')
    m.add_argument('fichier')
    m.add_argument('--message')
    x = sp.add_parser('mixer')
    x.add_argument('--graine', type=int, default=20261104)
    x.add_argument('--versions', nargs='+', help='seulement ces versions (défaut : toutes)')
    x.add_argument('--extraits', nargs='+', help='seulement ces extraits (défaut : tous)')
    x.add_argument('--ecoute', help="dossier de l'Écoute à l'aveugle (défaut : ecoute/ du dossier de travail) ; les extraits "
                   "d'autres bancs qui y sont gardent leur correspondance")
    sp.add_parser('tableau')
    rp = sp.add_parser('rapport')
    rp.add_argument('session')
    rp.add_argument('--ref', default='Y30', help='version de référence des SDR de conversion (défaut : Y30)')
    a = p.parse_args()
    if a.cmd in ('preparer', 'envoyer', 'lancer', 'modele', 'mixer'):   # tableau et rapatrier : resultats/ et Kaggle seulement
        configurer(a.cas)
    if a.cmd in ('envoyer', 'lancer', 'rapatrier', 'modele'):
        configurer_kaggle()
    {'preparer': preparer, 'envoyer': envoyer, 'lancer': lancer, 'rapatrier': rapatrier, 'modele': modele, 'mixer': mixer,
     'tableau': tableau, 'rapport': rapport}[a.cmd](a)


if __name__ == '__main__':
    main()
