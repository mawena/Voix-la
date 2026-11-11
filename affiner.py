#!/usr/bin/python3
"""affiner (Voix-là) : affine une voix sur son chant (Seed-VC 44,1 kHz ; GPU gratuit de Kaggle), pour de meilleurs doublages.

  affiner [NOM] [--prises FICHIER…] [--pas 1500] [--budget-h 2] [--donnees DIR] [--evenements FICHIER]
          [--essai | --simulation] [--reprendre] [--etat]

Une voix = un dossier <données>/voix/<nom>/. Ses prises de chant (enregistrées ou importées dans l'interface, page Voix, ou
posées dans voix/<nom>/chant/ : m4a, aac, mp3, wav, ogg, opus, 3gp, amr, flac, convertis par ffmpeg) servent à un court entraînement sur le GPU de Kaggle, qui apprend son timbre chanté : de 5 à
10 min de chant a cappella, une heure de GPU environ sur les 30 h hebdomadaires. Sans NOM : la voix par défaut (clé « voix »
de la configuration de doubler), sinon la seule voix présente. --prises : ces fichiers audio au lieu du dossier chant/.
  1. sur le PC, chaque prise est contrôlée (durée, saturation, bruit de fond, silence, niveau) ; une prise saturée, bruitée
     ou trop faible n'est pas envoyée ; il faut au moins 2 min de chant exploitable (5 à 10 min conseillées) ;
  2. les prises retenues partent en FLAC 16 bits mono dans un jeu de données privé, avec un noyau privé
     (kaggle/affiner_kaggle.py), sur le compte et la machine de doubler (doubler --config kaggle.compte …). Les fonctions
     d'envoi, de suivi et de rapatriement sont celles de doubler.py ; un seul travail Kaggle à la fois sur le poste ;
  3. le noyau découpe, nettoie, contrôle, entraîne (train.py de Seed-VC, sauvegardes intermédiaires) et rend un point de
     contrôle compact et son rapport ; ils sont rangés dans <données>/voix/<nom>/affinage/ (affinage.pth, affinage.yml,
     affinage.json, rapport.json). Interrompu (budget de temps, panne) : le dernier point est gardé dans affinage/reprise/ ;
     --reprendre finit une session interrompue, ou en relance une depuis ce point.
--essai (ou --simulation) fait tout sauf l'envoi : contrôle des prises, jeu de données et noyau écrits dans
<état>/affinage/<nom>/essai/, plan et estimation de durée affichés ; rien ne part sur Kaggle et aucune session ne tourne.
--etat : ce qu'a rendu le dernier affinage de la voix. Données : --donnees, $VOIXLA_DONNEES, « donnees » de la configuration,
sinon ~/Music/Voix-là. Seed-VC : licence GPL-3.0 ; n'enregistrez que votre voix, ou celle d'une personne d'accord.
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # doubler.py est à côté (dépôt, ou /usr/lib/voix-la)
import doubler  # noqa: E402  (ses fonctions d'envoi, de suivi et de rapatriement ; rien n'est lancé à l'import)

VERSION = doubler.VERSION
DEPOT = doubler.DEPOT
SCRIPT_NOYAU = DEPOT / 'kaggle' / 'affiner_kaggle.py'
NOMS_KAGGLE = {'jeu': 'affinage-voix-entrees', 'noyau': 'affinage-voix-gpu'}   # clés « kaggle.jeu_affinage », « kaggle.noyau_affinage »
SR = 44100
UTILE_MIN_S, UTILE_CONSEIL_S, UTILE_MAX_S = 120.0, 300.0, 900.0
PAS_DEFAUT, BUDGET_H_DEFAUT = 1500, 2.0
# Seuils de contrôle : ceux du noyau (affiner_kaggle.DEFAUTS), pour que ce qui est envoyé soit ce que le noyau garde
SATURATION_MAX = 0.002    # part d'échantillons à pleine échelle
RSB_MIN_DB = 15.0         # chant au-dessus du fond
NIVEAU_MIN_DB = -45.0     # niveau actif
SILENCE_MAX_PCT = 40.0
# Estimation du temps de GPU (à mesurer au premier essai réel : voir docs/affinage.md) : installation et chargement
# des modèles, puis un pas d'entraînement (batch 2, modèle 44,1 kHz, T4) ; la fourchette dit l'incertitude
INSTALLATION_S = 420.0
SECONDES_PAR_PAS = (0.8, 2.0)
RENDU_S = 120.0
# Formats des prises : ceux que la séance d'enregistrement de l'interface range dans voix/<nom>/chant/ (fichiers de téléphone),
# plus FLAC ; tous sont décodés et rééchantillonnés à 44,1 kHz mono par ffmpeg, quelle que soit leur fréquence d'origine
EXTENSIONS = ('.wav', '.flac', '.m4a', '.aac', '.mp3', '.ogg', '.opus', '.3gp', '.amr')
BANDE_MIN_HZ = 16000      # en dessous (amr à 8 kHz, appel téléphonique…), le timbre aigu manque : alerte, prise gardée


def dire(*a):
    doubler.dire(*a)


# --- Chemins ---------------------------------------------------------------------------------------------------------

def dossier_voix(nom):
    return doubler.VOIX / nom


def dossier_affinage(nom):
    return dossier_voix(nom) / 'affinage'


def fiche_affinage(nom):
    """Le affinage.json de la voix s'il existe et que son point de contrôle est là, sinon None."""
    d = dossier_affinage(nom)
    try:
        f = json.loads((d / 'affinage.json').read_text())
    except (OSError, ValueError):
        return None
    return f if (d / f.get('point', 'affinage.pth')).is_file() else None


def session_en_cours():
    return doubler.ETAT / 'affinage-en-cours.json'


def reprise_locale(nom):
    """Dernier point de contrôle complet gardé par un affinage inachevé (affinage/reprise/), sinon None."""
    liste = sorted((dossier_affinage(nom) / 'reprise').glob('DiT_epoch_*_step_*.pth'),
                   key=lambda f: int(re.search(r'step_(\d+)', f.name).group(1)))
    return liste[-1] if liste else None


# --- Contrôle des prises ---------------------------------------------------------------------------------------------

def enveloppe_db(x, pas=441, fenetre=4):
    n = len(x) // pas
    if n == 0:
        import numpy as np
        return np.zeros(0)
    import numpy as np
    p = np.mean(x[:n * pas].reshape(n, pas) ** 2, axis=1)
    p = np.convolve(np.pad(p, (fenetre // 2, fenetre - 1 - fenetre // 2), mode='edge'), np.ones(fenetre) / fenetre, mode='valid')
    return 10 * np.log10(np.maximum(p, 1e-12))


def frequence_source(fichier):
    """Fréquence d'échantillonnage du fichier d'origine (Hz) d'après ffprobe, None si on ne la lit pas."""
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=sample_rate',
                            '-of', 'csv=p=0', str(fichier)], capture_output=True, text=True, timeout=30)
        return int(r.stdout.strip().split(',')[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def controler_prise(fichier):
    """Mesures et alertes d'une prise : durée, crête, saturation, bruit de fond, rapport chant/fond, silence, niveau actif.
    'retenue' est faux pour une prise que le noyau refuserait (saturée, bruitée, trop faible, illisible)."""
    import numpy as np
    res = {'fichier': str(fichier), 'nom': Path(fichier).name, 'alertes': [], 'retenue': True}
    try:
        x = doubler.lire_brut(fichier, sr=SR)
    except SystemExit:
        return {**res, 'retenue': False, 'duree_s': 0.0, 'utile_s': 0.0, 'alertes': ['fichier audio illisible']}
    res['duree_s'] = round(len(x) / SR, 1)
    if len(x) < 5 * SR:
        return {**res, 'retenue': False, 'utile_s': 0.0, 'alertes': [f"prise trop courte ({res['duree_s']} s)"]}
    crete = float(np.abs(x).max())
    res['crete_dbfs'] = round(20 * math.log10(max(crete, 1e-9)), 1)
    res['saturation_pct'] = round(100 * float(np.mean(np.abs(x) >= 0.99)), 3)
    env = enveloppe_db(x)
    fond, fort = float(np.percentile(env, 10)), float(np.percentile(env, 90))
    seuil = min(max(fond + 10, fort - 30), -35.0)
    actif = env > seuil
    res['bruit_dbfs'] = round(fond, 1)
    res['rsb_db'] = round(fort - fond, 1)
    res['silence_pct'] = round(100 * (1 - float(actif.mean())), 1)
    res['utile_s'] = round(float(actif.sum()) * 0.01, 1)
    p = x[:len(env) * 441].reshape(len(env), 441) ** 2
    res['niveau_actif_dbfs'] = round(float(10 * np.log10(max(p[actif].mean(), 1e-12))), 1) if actif.any() else None
    a = res['alertes']
    hz = frequence_source(fichier)
    if hz:
        res['frequence_hz'] = hz
        if hz < BANDE_MIN_HZ:
            a.append(f'enregistrée à {hz / 1000:g} kHz seulement : la voix manquera d\'aigus ; réglez le téléphone sur la '
                     'meilleure qualité (m4a ou wav)')
    if res['saturation_pct'] > 100 * SATURATION_MAX or res['crete_dbfs'] > -0.5:
        a.append(f"saturée (crête {res['crete_dbfs']} dBFS) : baissez le niveau d'entrée ou reculez du micro")
        res['retenue'] = False
    if res['rsb_db'] < RSB_MIN_DB:
        a.append(f"bruit de fond trop proche du chant (écart {res['rsb_db']} dB, au moins {RSB_MIN_DB:.0f}) : pièce plus calme, "
                 "casque pour la musique")
        res['retenue'] = False
    if res['niveau_actif_dbfs'] is None or res['niveau_actif_dbfs'] < NIVEAU_MIN_DB:
        a.append('niveau trop faible : rapprochez-vous du micro (15 à 20 cm) ou montez le niveau d\'entrée')
        res['retenue'] = False
    if res['silence_pct'] > SILENCE_MAX_PCT:
        a.append(f"beaucoup de silence ({res['silence_pct']:.0f} %) : enchaînez davantage les phrases")
    return res


def bilan(controles):
    """Durée de chant exploitable (prises retenues) et verdict : {'utile_s', 'retenues', 'ecartees', 'alertes', 'bloquant'}."""
    retenues = [c for c in controles if c['retenue']]
    utile = round(sum(c['utile_s'] for c in retenues), 1)
    alertes, bloquant = [], None
    if not controles:
        bloquant = 'aucune prise de chant'
    elif utile < UTILE_MIN_S:
        bloquant = (f"seulement {utile:.0f} s de chant exploitable ; il en faut au moins {UTILE_MIN_S:.0f} s "
                    f"({UTILE_CONSEIL_S / 60:.0f} à 10 min conseillées)")
    elif utile < UTILE_CONSEIL_S:
        alertes.append(f"{utile:.0f} s de chant exploitable : l'affinage marchera, mais 5 à 10 min le feront mieux")
    elif utile > UTILE_MAX_S:
        alertes.append(f"{utile / 60:.0f} min de chant : au-delà de 10 min, le gain est faible et le temps de GPU monte")
    return {'utile_s': utile, 'retenues': len(retenues), 'ecartees': len(controles) - len(retenues),
            'alertes': alertes, 'bloquant': bloquant}


def estimer_s(pas):
    """Durée probable (basse, haute) du noyau sur la T4, en secondes : installation, entraînement, rendu."""
    return tuple(round(INSTALLATION_S + pas * s + RENDU_S) for s in SECONDES_PAR_PAS)


def prises_de(nom, fichiers):
    """Prises à envoyer : les fichiers donnés, sinon les fichiers audio de voix/<nom>/chant/ (tous les formats de EXTENSIONS)."""
    if fichiers:
        manque = [f for f in fichiers if not Path(f).is_file()]
        if manque:
            raise SystemExit('fichier introuvable : ' + ', '.join(manque))
        return [Path(f) for f in fichiers]
    d = dossier_voix(nom) / 'chant'
    prises = sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in EXTENSIONS) if d.is_dir() else []
    if not prises:
        raise SystemExit(f'aucune prise de chant dans {d} : en enregistrer dans l\'interface (page Voix, « Chant pour '
                         "l'affinage »), ou donner des fichiers avec --prises")
    return prises


# --- Jeu de données et noyau -----------------------------------------------------------------------------------------

def encoder_prise(source, sortie):
    """FLAC 16 bits mono 44,1 kHz, sans changer le niveau (le noyau mesure la saturation et normalise lui-même)."""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-vn', '-ac', '1', '-ar', str(SR),
                        '-sample_fmt', 's16', '-c:a', 'flac', str(sortie)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f'ffmpeg : {r.stderr.strip()[-300:]}')


def preparer_jeu(retenues, dossier, reprise=None, compte_jeu='compte/jeu'):
    """Jeu de données privé : chant/prise-NN.flac, reprise/ (le point de contrôle d'une session inachevée) et ses métadonnées."""
    shutil.rmtree(dossier, ignore_errors=True)
    (dossier / 'chant').mkdir(parents=True)
    taille = 0
    for i, c in enumerate(retenues, 1):
        sortie = dossier / 'chant' / f'prise-{i:02d}.flac'
        encoder_prise(c['fichier'], sortie)
        taille += sortie.stat().st_size
    if reprise:
        (dossier / 'reprise').mkdir()
        shutil.copy2(reprise, dossier / 'reprise' / Path(reprise).name)
        taille += Path(reprise).stat().st_size
    with open(dossier / 'dataset-metadata.json', 'w') as f:
        json.dump({'title': compte_jeu.split('/')[1], 'id': compte_jeu, 'licenses': [{'name': 'unknown'}]}, f)
    return taille


def envoyer_jeu(jeu, dossier, nb_prises, reprise=False):
    """Envoie le jeu (kaggle datasets create/version, comme doubler.envoyer_jeu) et attend qu'il soit prêt."""
    existe = doubler.kaggle('datasets', 'status', jeu)[0] == 0
    code, texte = (doubler.kaggle('datasets', 'version', '-p', str(dossier), '-m', f'{nb_prises} prise(s) de chant'
                                  + (', reprise' if reprise else '')) if existe
                   else doubler.kaggle('datasets', 'create', '-p', str(dossier)))
    if code or re.search(r'error|fail|invalid', texte, re.I):
        raise RuntimeError('Kaggle : jeu de données refusé : ' + texte.strip()[-500:])
    debut = time.time()
    while True:
        _, fichiers = doubler.kaggle('datasets', 'files', jeu)
        _, etat = doubler.kaggle('datasets', 'status', jeu)
        if nb_prises and fichiers.count('prise-') >= nb_prises and re.search('ready', etat, re.I) \
                and (not reprise or 'DiT_epoch' in fichiers):
            break
        if time.time() - debut > 20 * 60:
            raise RuntimeError('Kaggle : jeu de données toujours en préparation après 20 min')
        time.sleep(10)
    time.sleep(30)   # le noyau monte la dernière version prête


def reglages_noyau(nom, a):
    return {'voix': nom, 'budget_s': int(a.budget_h * 3600), 'max_steps': int(a.pas), 'batch': 2}


def identifiants(conf, essai):
    """(jeu, noyau, outils) : <compte>/<nom>. Sans compte configuré, un essai prend un compte fictif (rien n'est envoyé)."""
    try:
        noms = doubler.configurer_kaggle(conf)
    except SystemExit:
        if not essai:
            raise
        noms = {n: f'compte-essai/{d}' for n, d in doubler.KAGGLE_DEFAUTS.items()}
        doubler.JEU, doubler.NOYAU, doubler.OUTILS = noms['jeu'], noms['noyau'], noms['outils']
    compte = noms['jeu'].split('/')[0]
    k = conf.get('kaggle') if isinstance(conf.get('kaggle'), dict) else {}
    return (f"{compte}/{k.get('jeu_affinage') or NOMS_KAGGLE['jeu']}", f"{compte}/{k.get('noyau_affinage') or NOMS_KAGGLE['noyau']}",
            noms['outils'])


# --- Rangement du résultat -------------------------------------------------------------------------------------------

def sha256(chemin):
    h = hashlib.sha256()
    with open(chemin, 'rb') as f:
        for bloc in iter(lambda: f.read(1 << 20), b''):
            h.update(bloc)
    return h.hexdigest()


def ranger_retour(nom, retour, bilan_donnees=None):
    """Range la sortie du noyau dans voix/<nom>/affinage/ : point compact, config, rapport, fiche ; point de reprise si
    l'affinage est inachevé. Rend la fiche écrite."""
    rapport = json.loads((retour / 'rapport.json').read_text())
    d = dossier_affinage(nom)
    d.mkdir(parents=True, exist_ok=True)
    point = retour / 'affinage' / f'{nom}.pth'
    if not point.exists():
        raise RuntimeError(f"le noyau n'a pas rendu de point de contrôle : {rapport.get('erreur') or 'voir rapport.json'}")
    for src, dst in ((point, 'affinage.pth'), (retour / 'affinage' / f'{nom}.yml', 'affinage.yml'),
                     (retour / 'rapport.json', 'rapport.json')):
        if src.exists():
            tmp = d / (dst + '.tmp')
            shutil.copy2(src, tmp)
            os.replace(tmp, d / dst)
    reprise = d / 'reprise'
    shutil.rmtree(reprise, ignore_errors=True)
    if not rapport.get('complet') and (retour / 'reprise').is_dir():
        shutil.copytree(retour / 'reprise', reprise)
    pertes = (rapport.get('entrainement') or {}).get('pertes') or {}
    fiche = {'format': 1, 'voix': nom, 'date': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
             'moteur': 'seedvc-44k', 'commit_seedvc': rapport.get('commit_seedvc'), 'preset': rapport.get('preset'),
             'point': 'affinage.pth', 'config': 'affinage.yml' if (d / 'affinage.yml').exists() else None,
             'sha256': sha256(d / 'affinage.pth'), 'taille_mo': round((d / 'affinage.pth').stat().st_size / 1e6, 1),
             'pas': (rapport.get('point') or {}).get('pas'), 'complet': bool(rapport.get('complet')),
             'utile_s': (rapport.get('donnees') or {}).get('utile_s'), 'morceaux': (rapport.get('donnees') or {}).get('morceaux'),
             'perte_fin': pertes.get('moyenne_fin'), 'duree_noyau_s': (rapport.get('temps') or {}).get('total_s'),
             'avertissements': rapport.get('avertissements') or []}
    tmp = d / 'affinage.json.tmp'
    tmp.write_text(json.dumps(fiche, ensure_ascii=False, indent=1))
    os.replace(tmp, d / 'affinage.json')
    return fiche


# --- Déroulement -----------------------------------------------------------------------------------------------------

def noter_session(s):
    doubler.ETAT.mkdir(parents=True, exist_ok=True)
    tmp = session_en_cours().with_name('affinage-en-cours.json.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    os.replace(tmp, session_en_cours())


def clore(etat):
    f = session_en_cours()
    if f.exists():
        try:
            s = json.loads(f.read_text())
            s.update({'etat': etat, 'close_le': datetime.datetime.now().astimezone().isoformat(timespec='seconds')})
            (doubler.ETAT / 'affinage-precedent.json').write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str))
        except (OSError, ValueError):
            pass
        f.unlink(missing_ok=True)


def finir_session(s, a, videos):
    """Suit le noyau noté dans la session, rapatrie sa sortie et la range. Rend la fiche."""
    nom, noyau = s['voix'], s['noyau']
    kdir = Path(s['dossier'])
    try:
        etat = doubler.suivre_noyau(noyau, s['pousse_epoch'], s.get('attente_maxi_s', 3 * 3600), videos,
                                    s.get('estimation_s'), origine_limite=time.time() if s.get('reprise') else None)
    except BaseException as e:
        doubler.journal_gpu_fin('kaggle', s['pousse_epoch'], doubler.statut_gpu(e))
        raise
    doubler.journal_gpu_fin('kaggle', s['pousse_epoch'], 'reussie' if etat == 'COMPLETE' else 'echouee')
    retour = kdir / 'retour'
    try:
        doubler.rapatrier_noyau(noyau, retour, etat, attendu='rapport.json', videos=videos)
    except Exception:
        clore('échec')
        raise
    fiche = ranger_retour(nom, retour)
    clore('terminée' if fiche['complet'] else 'inachevée')
    shutil.rmtree(kdir, ignore_errors=True)
    return fiche


def plan(nom, controles, b, a, jeu, noyau):
    dire(f'Affinage de la voix {nom} : {len(controles)} prise(s), {b["retenues"]} retenue(s), '
         f'{b["utile_s"] / 60:.1f} min de chant exploitable')
    for c in controles:
        etat = 'retenue' if c['retenue'] else 'ÉCARTÉE'
        dire(f"  {c['nom']} : {c.get('duree_s', 0):.0f} s, chant {c.get('utile_s', 0):.0f} s, {etat}"
             + (' — ' + ' ; '.join(c['alertes']) if c['alertes'] else ''))
    for al in b['alertes']:
        dire('Attention :', al)
    bas, haut = estimer_s(a.pas)
    dire(f'Estimation : {bas / 60:.0f} à {haut / 60:.0f} min de GPU Kaggle ({a.pas} pas ; quota hebdomadaire de 30 h), '
         f'noyau {noyau}, jeu {jeu}')


def affiner(a):
    conf = doubler.configurer(argparse.Namespace(donnees=a.donnees, sortie=None))
    nom = a.voix or doubler.exiger_voix_defaut()
    if not (dossier_voix(nom) / 'voix.json').exists():
        raise SystemExit(f"voix inconnue : {nom} (disponibles : {', '.join(doubler.voix_presentes()) or 'aucune'})")
    essai = a.essai
    jeu, noyau, outils = identifiants(conf, essai)
    videos = [f'affinage {nom}']
    kdir = doubler.ETAT / 'affinage' / nom / ('essai' if essai else '.kaggle')
    # --reprendre : une session notée, suivie jusqu'au bout ; sinon un point de contrôle gardé, relancé
    if a.reprendre and session_en_cours().exists():
        s = json.loads(session_en_cours().read_text())
        if s['voix'] != nom and not a.voix:
            nom, videos = s['voix'], [f"affinage {s['voix']}"]
        s['reprise'] = True
        with doubler.Verrou(Path(s['dossier']), videos):
            fiche = finir_session(s, a, videos)
        dire(f"Affinage {'terminé' if fiche['complet'] else 'inachevé'} : {dossier_affinage(nom) / 'affinage.pth'}")
        return 0
    point = reprise_locale(nom) if a.reprendre else None
    if a.reprendre and not point:
        raise SystemExit(f'rien à reprendre : ni session en cours ({session_en_cours()}), ni point de contrôle dans '
                         f'{dossier_affinage(nom) / "reprise"}')
    prises = prises_de(nom, a.prises)
    with doubler.Etape('controle', videos, f'{len(prises)} prise(s) de chant'):
        controles = [controler_prise(p) for p in prises]
    b = bilan(controles)
    plan(nom, controles, b, a, jeu, noyau)
    if b['bloquant']:
        raise SystemExit('Affinage impossible : ' + b['bloquant'])
    retenues = [c for c in controles if c['retenue']]
    kdir.mkdir(parents=True, exist_ok=True)
    taille = preparer_jeu(retenues, kdir / 'jeu', point, jeu)
    dire(f'Jeu de données prêt : {taille / 1e6:.0f} Mo ({len(retenues)} prise(s) en FLAC 16 bits mono'
         + (f", reprise à l'étape {re.search(r'step_(\d+)', point.name).group(1)}" if point else '') + ')')
    reglages = reglages_noyau(nom, a)
    estimation = estimer_s(a.pas)[1]
    doubler.SCRIPT_NOYAU = SCRIPT_NOYAU          # pousser_noyau envoie ce fichier-ci, avec ses réglages
    if essai:
        # tout sauf l'envoi : la commande kaggle est remplacée par un enregistreur ; le noyau est écrit tel qu'il partirait
        appels = []
        doubler.kaggle = lambda *args: (appels.append(args), (0, 'successfully pushed version 0.'))[1]
        doubler.pousser_noyau(reglages, kdir, 60, noyau, (jeu,), (outils,), videos)
        (kdir / 'plan.json').write_text(json.dumps({'voix': nom, 'jeu': jeu, 'noyau': noyau, 'reglages': reglages,
                                                    'prises': controles, 'bilan': b, 'estimation_s': estimation,
                                                    'appels_kaggle_remplaces': [list(c[:2]) for c in appels]},
                                                   ensure_ascii=False, indent=1, default=str))
        dire(f'Essai : rien envoyé. Jeu de données : {kdir / "jeu"} ; noyau : {kdir / ("envoi-" + noyau.split("/")[1])} ; '
             f'plan : {kdir / "plan.json"}')
        return 0
    with doubler.Verrou(kdir, videos):
        if session_en_cours().exists():
            raise SystemExit(f'un affinage est déjà en cours ({session_en_cours()}) : « affiner --reprendre » le finit')
        with doubler.Etape('envoi', videos, f'{len(retenues)} prise(s) de chant vers {jeu}'):
            envoyer_jeu(jeu, kdir / 'jeu', len(retenues), bool(point))
        shutil.rmtree(kdir / 'jeu', ignore_errors=True)
        attente_maxi = int(a.budget_h * 3600) + 3600
        pousse, version = doubler.pousser_noyau(reglages, kdir, attente_maxi, noyau, (jeu,), (outils,), videos)
        s = {'voix': nom, 'noyau': noyau, 'jeu': jeu, 'dossier': str(kdir), 'pousse_epoch': pousse, 'version_noyau': version,
             'poussee_le': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'estimation_s': estimation,
             'attente_maxi_s': attente_maxi, 'reglages': reglages, 'prises': len(retenues), 'utile_s': b['utile_s']}
        doubler.journal_gpu_debut('kaggle', noyau, pousse, 'affiner', machine=doubler.MACHINE, version=version,
                                  estimation_s=estimation)
        noter_session(s)
        fiche = finir_session(s, a, videos)
    dire(f"Affinage {'terminé' if fiche['complet'] else 'inachevé (« affiner --reprendre » continue)'} : "
         f"{fiche['pas']} pas, {fiche['taille_mo']} Mo, rangé dans {dossier_affinage(nom)}")
    return 0


def afficher_etat(a):
    doubler.configurer(argparse.Namespace(donnees=a.donnees, sortie=None))
    nom = a.voix or doubler.exiger_voix_defaut()
    f = fiche_affinage(nom)
    if not f:
        print(f'voix {nom} : pas d\'affinage' + (' (un point de reprise existe)' if reprise_locale(nom) else ''))
        return 1
    print(json.dumps(f, ensure_ascii=False, indent=1))
    return 0


def lire_arguments(argv=None):
    p = argparse.ArgumentParser(prog='affiner', description=__doc__.split('\n')[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split('\n', 1)[1])
    p.add_argument('voix', nargs='?', default=None, help='voix à affiner (dossier de <données>/voix ; défaut : la voix par défaut)')
    p.add_argument('--prises', nargs='+', default=None, metavar='FICHIER',
                   help='fichiers audio de chant, au lieu de voix/<nom>/chant/ (m4a, aac, mp3, wav, ogg, opus, 3gp, amr, flac)')
    p.add_argument('--pas', type=int, default=PAS_DEFAUT, help=f"étapes d'entraînement (défaut {PAS_DEFAUT})")
    p.add_argument('--budget-h', type=float, default=BUDGET_H_DEFAUT, metavar='H',
                   help=f'durée maximale de la session Kaggle, en heures (défaut {BUDGET_H_DEFAUT:g})')
    p.add_argument('--donnees', default=None, metavar='DIR', help='dossier des données (voir doubler)')
    p.add_argument('--evenements', default=None, metavar='FICHIER', help="événements JSON, un par ligne (suivi par l'interface)")
    p.add_argument('--essai', '--simulation', dest='essai', action='store_true',
                   help="tout sauf l'envoi : contrôle, jeu de données et noyau écrits localement, rien sur Kaggle")
    p.add_argument('--reprendre', action='store_true',
                   help='finit la session notée, ou relance un affinage inachevé depuis son dernier point')
    p.add_argument('--etat', action='store_true', help="affiche la fiche du dernier affinage de la voix")
    p.add_argument('--version', action='version', version=f'affiner {VERSION}')
    a = p.parse_args(argv)
    if a.pas < 100 or a.pas > 20000:
        p.error('--pas : entre 100 et 20000')
    if not 0.5 <= a.budget_h <= 9:
        p.error('--budget-h : entre 0,5 et 9 heures (une session Kaggle dure 9 h au plus)')
    return a


def main(argv=None):
    a = lire_arguments(argv)
    code = 1
    if a.evenements and not a.etat:
        doubler.EV.ouvrir(a.evenements)     # ouvert avant tout : même un refus précoce est suivi par l'interface
    try:
        code = afficher_etat(a) if a.etat else affiner(a)
        return code
    except RuntimeError as e:
        doubler.EV('erreur', None, message=str(e))
        print(f'affiner : {e}', file=sys.stderr)
        return 1
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if not isinstance(e.code, (int, type(None))):
            doubler.EV('erreur', None, message=str(e.code))
        raise
    except KeyboardInterrupt:
        doubler.EV('erreur', None, message='interrompu')
        print('affiner : interrompu', file=sys.stderr)
        return 130
    finally:
        if a.evenements and not a.etat:
            doubler.EV('fin', None, code=code)


if __name__ == '__main__':
    sys.exit(main())
