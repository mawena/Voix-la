#!/usr/bin/python3
"""doubler (Voix-là) : remplace la voix d'une vidéo par une voix clonée (YingMusic-SVC, Seed-VC ; GPU gratuit de Kaggle).

  doubler <vidéo> [<vidéo>…] [--voix NOM] [--mode auto|chant|parole] [--octave auto|0|-12|+12] [--pas 30]
          [--moteur yingmusic|seedvc] [--sans-nettoyage] [--sans-paroles] [--voix-db 4] [--reverb-db -13] [--mix-v1]
          [--debut S --duree S] [--remix] [--sortie DIR] [--comparer-pas 50] [--donnees DIR] [--evenements FICHIER]
  doubler --ajouter-voix <nom> <enregistrement>      ajoute une voix (n'importe quel audio ou vidéo)
  doubler --voix-liste [--json]                      voix disponibles
  doubler --config [CLE VALEUR]                      affiche la configuration, ou écrit une clé
  doubler --reprendre                                finit une session Kaggle interrompue (sans GPU)
  doubler "clip.mp4@60+40" "film.mkv@1:10+35"        un extrait propre à chaque vidéo d'un lot

Les vidéos données ensemble partent dans UNE seule session Kaggle (installation payée une fois) :
  1. sur le PC, le son de chaque vidéo est extrait (44,1 kHz stéréo flottant ; extrait calé sur une image clé avec
     --debut/--duree), puis envoyé en FLAC 24 bits (crête ramenée à -1 dBFS par un gain connu, retiré sur Kaggle) ;
  2. sur Kaggle (jeu de données et noyau privés), un seul noyau GPU sépare chaque voix, choisit le mode et la hauteur
     (en chant, un décalage d'octave par chanteur), et convertit (kaggle/doubler_kaggle.py) ; la voix isolée revient
     elle aussi en FLAC. En chant, la voix est d'abord nettoyée (voix principale et chœurs séparés, reverb d'origine
     retirée), puis convertie par YingMusic-SVC (--moteur seedvc : Seed-VC 44 kHz ; --sans-nettoyage : la voix isolée
     complète, comme avant, plus rapide) ; un passage perdu au nettoyage (voix de fond prise pour de la reverb) est
     repris de la voix complète ; en parole, Seed-VC 22 kHz sur la voix isolée complète. Paroles (sauf --sans-paroles
     ou doubler --config paroles non) : Whisper large-v3 sur la voix principale, horodatées par mot, et traduction
     française (NLLB-200) quand elles ne sont pas en français ;
  3. de retour sur le PC : mixage puis remise sur chaque vidéo, image et sous-titres d'origine copiés sans réencodage,
     paroles ajoutées en pistes de sous-titres. Mixage v2 (défaut) : la voix suit les nuances de la voix d'origine (gain
     par fenêtres de 10 ms, lissé sur 150 ms, borné à ±6 dB, seulement là où les deux voix chantent), « s » adoucis
     (dé-esseur), voix +4 dB et reverb -13 dB en chant ; parole sans reverb. --mix-v1 : l'ancien mixage.
Sorties : <sortie>/<titre>/<titre> - voix <Nom>.<conteneur d'origine> et .mp3, .<langue>.srt, .fr.srt (traduction) et
.lrc (paroles synchronisées, à côté du mp3) ; travail/ (son d'origine, voix isolée, reste) et travail/<voix>/ (voix
convertie, rapport.json avec les temps de chaque étape, paroles.json). --remix refait seulement le mixage et la remise
sur la vidéo, paroles comprises. Seuls le son des vidéos et la référence de la voix choisie partent sur Kaggle.
Données (voix/, sorties/) : --donnees, sinon $VOIXLA_DONNEES, sinon « donnees » de ~/.config/voix-la/config.json,
sinon ~/Music/Voix-là. Voix par défaut : « voix » du même fichier (doubler --config voix NOM), sinon la seule voix de
<données>/voix. Compte Kaggle : « kaggle.compte » (doubler --config kaggle.compte <nom>) ; moteur du chant par
défaut : « moteur » (doubler --config moteur seedvc), sinon yingmusic. Commande kaggle : $VOIXLA_KAGGLE, sinon
~/kaggle-venv/bin/kaggle. Ancres de genre (facultatives) : ~/.config/voix-la/ancres_genre.json, sinon kaggle/ du dépôt.
YingMusic-SVC : code MIT, poids CC BY-NC 4.0, donc usage personnel et non commercial seulement.
"""
import argparse
import base64
import datetime
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VERSION = '0.3.0'   # même version que le paquet Debian (debian/changelog) et l'interface (voixla.__version__)
# Dossier du code : le dépôt, ou /usr/lib/voix-la une fois le paquet installé (/usr/bin/doubler lance ce fichier)
DEPOT = Path(__file__).resolve().parent
SCRIPT_NOYAU = DEPOT / 'kaggle' / 'doubler_kaggle.py'   # le noyau envoyé à Kaggle
# Commande kaggle, absente du paquet : $VOIXLA_KAGGLE, sinon l'environnement Python dédié (README, Prérequis)
KAGGLE = (Path(os.environ['VOIXLA_KAGGLE']).expanduser() if os.environ.get('VOIXLA_KAGGLE')
          else Path.home() / 'kaggle-venv/bin/kaggle')
JETON = Path.home() / '.kaggle/access_token'
CONFIG = Path.home() / '.config' / 'voix-la' / 'config.json'
DONNEES_DEFAUT = Path.home() / 'Music' / 'Voix-là'
# Identifiants Kaggle : <compte>/<nom>, compte lu dans CONFIG (jamais dans le code : le dépôt est public)
KAGGLE_DEFAUTS = {'jeu': 'doublage-voix-entrees', 'noyau': 'doublage-voix-gpu',
                  'outils': 'doublage-outils'}   # outils : wheels, Seed-VC, poids des modèles (doubler --preparer-outils)
CLES_CONFIG = {'donnees': 'dossier des données (voix/, sorties/)', 'kaggle.compte': 'compte Kaggle (nom d\'utilisateur)',
               'kaggle.jeu': 'jeu de données privé des entrées', 'kaggle.noyau': 'noyau GPU privé du doublage',
               'kaggle.outils': 'noyau privé des outils préparés',
               'moteur': 'moteur de conversion du chant : yingmusic (défaut) ou seedvc',
               'paroles': 'paroles à chaque doublage (transcription, traduction française, sous-titres) : oui (défaut) '
                          'ou non',
               'voix': 'voix cible par défaut, un dossier de <données>/voix (sinon la seule voix présente)'}
OUI = {'oui', 'o', 'yes', 'y', 'true', 'vrai', '1', 'on'}
NON = {'non', 'n', 'no', 'false', 'faux', '0', 'off'}
NOM_VOIX = r'[a-z0-9][a-z0-9_-]{0,39}'   # nom d'une voix : celui de son dossier
MOTEURS = ('yingmusic', 'seedvc')   # chant ; la parole passe toujours par Seed-VC 22 kHz
MOTEUR_DEFAUT = 'yingmusic'         # gagnant du banc d'essai du 04/11/2026 (mesures et écoute à l'aveugle)
# Fixés par configurer() et configurer_kaggle() au lancement, d'après les options, l'environnement et CONFIG
DONNEES = DONNEES_DEFAUT
SORTIES = DONNEES_DEFAUT / 'sorties'
VOIX = DONNEES_DEFAUT / 'voix'   # une voix = un dossier (reference.wav, voix.json)
JEU = NOYAU = OUTILS = None
VOIX_DEFAUT = None   # voix_par_defaut() : clé « voix » de CONFIG, sinon la seule voix de VOIX
VOIX_DEFAUT_SOURCE = None
FACTEUR_CHANT = 2.0   # centre de chant d'une voix ≈ 2 x sa hauteur de parole (ex. 100 Hz -> 200 Hz ; frontière 0/-12 à 1,41 x ce centre)
# Réglages du noyau : ceux validés à l'écoute d'une chanson (modèle chant, cfg 0.7, référence à -26 LUFS) et ceux
# du choix du mode et de la hauteur (calibrés sur une chanteuse seule, un faux duo, et de la parole ; voir README)
NOYAU_REGLAGES = {
    'cfg': 0.7, 'fp16': True, 'pad_s': 0.25, 'fondu_s': 0.06, 'graine': 1234, 'modele_parole': 'parole22k',
    'seuil_tonalite': 0.35, 'seuil_reste_db': -12, 'seuil_tenue': 0.12, 'voix_dense': 0.6,
    'voise_min_s': 0.8, 'centre_homme_hz': 250, 'centre_femme_hz': 420,
    'sil_parole': 0.2,
    'nettoyage_recouvrement': 4,   # recouvrement des Roformer du nettoyage (banc : 8, deux fois plus lent)
}
# Facultatif, jamais publié : empreintes CAMPPlus moyennes (192 nombres) d'une voix d'homme et d'une voix de femme
# chantées, {"homme": […], "femme": […]} ; sans lui, la décision d'octave se passe d'empreinte (voir lire_ancres).
# Cherché d'abord à côté de la configuration (paquet installé), puis dans le dépôt (installation de développement)
ANCRES = (CONFIG.parent / 'ancres_genre.json', DEPOT / 'kaggle' / 'ancres_genre.json')
CONTENEURS = {   # extension -> (codec audio, options, sous-titres copiables)
    '.mp4': (['-c:a', 'aac', '-b:a', '256k'], ['-movflags', '+faststart'], {'mov_text'}),
    '.m4v': (['-c:a', 'aac', '-b:a', '256k'], ['-movflags', '+faststart'], {'mov_text'}),
    '.mov': (['-c:a', 'aac', '-b:a', '256k'], ['-movflags', '+faststart'], {'mov_text'}),
    '.mkv': (['-c:a', 'aac', '-b:a', '256k'], [], {'subrip', 'ass', 'ssa', 'webvtt', 'text', 'hdmv_pgs_subtitle',
                                                   'dvd_subtitle', 'dvb_subtitle'}),
    '.webm': (['-c:a', 'libopus', '-b:a', '192k'], [], {'webvtt'}),
}


def dire(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def abrege(chemin):
    """Chemin sous le dossier personnel écrit « ~/… » (aide de la commande)."""
    try:
        return '~/' + str(Path(chemin).relative_to(Path.home()))
    except ValueError:
        return str(chemin)


# --- Configuration et dossier des données -----------------------------------------------------------------------------

SECRETS = re.compile(r'jeton|token|secret|password|mot.?de.?passe|(^|[._])(cle|clé|key)($|[._])', re.I)


def lire_config():
    try:
        conf = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise SystemExit(f'{CONFIG} illisible ({e}) : corriger le JSON, ou l\'effacer et refaire doubler --config …')
    if not isinstance(conf, dict):
        raise SystemExit(f'{CONFIG} : un objet JSON est attendu ({{"kaggle": {{"compte": "…"}}}})')
    return conf


def valeur_config(conf, cle):
    for morceau in cle.split('.'):
        if not isinstance(conf, dict) or morceau not in conf:
            return None
        conf = conf[morceau]
    return conf


def ecrire_config(cle, valeur):
    """Écrit une clé connue (« kaggle.compte » -> {"kaggle": {"compte": …}}) ; écriture atomique, autres clés gardées."""
    if cle not in CLES_CONFIG:
        raise SystemExit(f'clé inconnue : {cle} (clés : {", ".join(CLES_CONFIG)})')
    if cle == 'donnees':
        valeur = str(Path(valeur).expanduser().resolve())
    if cle == 'moteur' and valeur not in MOTEURS:
        raise SystemExit(f'moteur inconnu : {valeur} (moteurs : {", ".join(MOTEURS)})')
    if cle == 'voix' and not re.fullmatch(NOM_VOIX, valeur):
        raise SystemExit(f'nom de voix invalide : {valeur} (minuscules, chiffres, - ou _ ; ex. marie, jean-b)')
    if cle == 'paroles':
        booleen = oui_non(valeur)
        if booleen is None:
            raise SystemExit(f'paroles : oui ou non attendu (reçu : {valeur})')
        valeur = booleen
    conf = lire_config()
    d = conf
    *chemin, fin = cle.split('.')
    for morceau in chemin:
        if not isinstance(d.get(morceau), dict):
            d[morceau] = {}
        d = d[morceau]
    d[fin] = valeur
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_name(CONFIG.name + '.tmp')
    tmp.write_text(json.dumps(conf, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, CONFIG)
    print(f'{cle} = {valeur}  ({CONFIG})')
    if cle == 'voix' and not (VOIX / valeur / 'voix.json').exists():
        print(f'attention : pas (encore) de voix {valeur} dans {VOIX} (ajouter : doubler --ajouter-voix {valeur} <enregistrement>)')


def oui_non(valeur):
    """oui / non (et true, false, 1, 0…) -> True / False ; un booléen JSON tel quel ; None si ce n'est ni l'un ni l'autre."""
    if isinstance(valeur, bool):
        return valeur
    v = str(valeur).strip().lower()
    return True if v in OUI else False if v in NON else None


def paroles_config(conf):
    """Clé « paroles » de la configuration (vrai par défaut ; une valeur illisible compte comme vrai, avec un avis)."""
    v = valeur_config(conf, 'paroles')
    if v is None:
        return True
    b = oui_non(v)
    if b is None:
        dire(f'attention : « paroles » de {CONFIG} illisible ({v!r}) : paroles gardées (doubler --config paroles oui|non)')
        return True
    return b


def masquer(conf):
    """Copie de la configuration sans aucune valeur secrète (le jeton Kaggle n'y est jamais écrit, mais par prudence)."""
    if isinstance(conf, dict):
        return {k: ('***' if SECRETS.search(str(k)) else masquer(v)) for k, v in conf.items()}
    return conf


def configurer(a):
    """Dossier des données : --donnees, sinon $VOIXLA_DONNEES, sinon « donnees » de CONFIG, sinon ~/Music/Voix-là.
    voix/ et sorties/ en dépendent ; $DOUBLER_VOIX et --sortie gardent la priorité (compatibilité). Puis la voix par
    défaut (voix_par_defaut)."""
    global DONNEES, SORTIES, VOIX, VOIX_DEFAUT, VOIX_DEFAUT_SOURCE
    conf = lire_config()
    for valeur, source in ((getattr(a, 'donnees', None), 'option --donnees'), (os.environ.get('VOIXLA_DONNEES'), 'VOIXLA_DONNEES'),
                           (valeur_config(conf, 'donnees'), str(CONFIG)), (str(DONNEES_DEFAUT), 'défaut')):
        if valeur:
            DONNEES = Path(str(valeur)).expanduser()
            a.source_donnees = source
            break
    VOIX = Path(os.environ['DOUBLER_VOIX']).expanduser() if os.environ.get('DOUBLER_VOIX') else DONNEES / 'voix'
    SORTIES = DONNEES / 'sorties'
    if not a.sortie:
        a.sortie = str(SORTIES)
    VOIX_DEFAUT, VOIX_DEFAUT_SOURCE = voix_par_defaut(conf)
    return conf


def voix_presentes():
    return sorted(p.parent.name for p in VOIX.glob('*/voix.json'))


def voix_par_defaut(conf):
    """Voix cible par défaut : clé « voix » de CONFIG (doubler --config voix NOM), sinon la seule voix de VOIX.
    Rend (nom, ou None s'il n'y en a pas ; d'où vient le nom, ou pourquoi il n'y en a pas)."""
    nom = valeur_config(conf, 'voix')
    if nom:
        return str(nom), str(CONFIG)
    presentes = voix_presentes()
    if len(presentes) == 1:
        return presentes[0], f'seule voix de {VOIX}'
    if not presentes:
        return None, f'aucune voix dans {VOIX}'
    return None, f'{len(presentes)} voix dans {VOIX} et aucune choisie (doubler --config voix NOM)'


def exiger_voix_defaut():
    """Sans --voix : la voix par défaut, ou un message qui dit comment la choisir."""
    presentes = voix_presentes()
    if VOIX_DEFAUT and VOIX_DEFAUT in presentes:
        return VOIX_DEFAUT
    if VOIX_DEFAUT:
        raise SystemExit(f"voix par défaut « {VOIX_DEFAUT} » (clé voix de {CONFIG}) absente de {VOIX} (disponibles : "
                         f"{', '.join(presentes) or 'aucune'}) : choisir avec --voix NOM, ou doubler --config voix NOM")
    if not presentes:
        raise SystemExit(f'aucune voix dans {VOIX} : en ajouter une (doubler --ajouter-voix <nom> <enregistrement>)')
    raise SystemExit(f"plusieurs voix dans {VOIX} ({', '.join(presentes)}) et aucune par défaut : choisir avec --voix NOM, "
                     'ou une fois pour toutes avec doubler --config voix NOM')


def configurer_kaggle(conf=None):
    """Identifiants <compte>/<nom> du jeu de données et des noyaux, d'après CONFIG ; le compte n'a pas de défaut."""
    global JEU, NOYAU, OUTILS
    conf = lire_config() if conf is None else conf
    k = conf.get('kaggle') if isinstance(conf.get('kaggle'), dict) else {}
    compte = str(k.get('compte') or '').strip()
    if not compte:
        raise SystemExit(f'compte Kaggle non configuré. Écrire :\n  doubler --config kaggle.compte <nom_du_compte_kaggle>\n'
                         f'ou dans {CONFIG} : {{"kaggle": {{"compte": "<nom_du_compte_kaggle>"}}}}')
    noms = {n: f"{compte}/{k.get(n) or d}" for n, d in KAGGLE_DEFAUTS.items()}
    JEU, NOYAU, OUTILS = noms['jeu'], noms['noyau'], noms['outils']
    return noms


def afficher_config(a, en_json=False):
    conf = lire_config()
    try:
        noms = configurer_kaggle(conf)
    except SystemExit:
        noms = {n: None for n in KAGGLE_DEFAUTS}
    moteur = valeur_config(conf, 'moteur') or MOTEUR_DEFAUT
    ancres = fichier_ancres()
    paroles = paroles_config(conf)
    effectif = {'config': str(CONFIG), 'donnees': str(DONNEES), 'source_donnees': a.source_donnees, 'voix': str(VOIX),
                'sorties': a.sortie, 'voix_defaut': VOIX_DEFAUT, 'source_voix_defaut': VOIX_DEFAUT_SOURCE,
                'moteur': moteur, 'paroles': paroles, 'kaggle': {'compte': valeur_config(conf, 'kaggle.compte'), **noms},
                'jeton_kaggle': 'présent' if JETON.exists() else 'absent', 'commande_kaggle': str(KAGGLE),
                'script_noyau': str(SCRIPT_NOYAU), 'ancres_genre': str(ancres) if ancres else None,
                'contenu': masquer(conf)}
    if en_json:
        print(json.dumps(effectif, ensure_ascii=False, indent=1))
        return
    print(f"configuration : {CONFIG}{'' if CONFIG.exists() else ' (absente)'}")
    print(json.dumps(masquer(conf), ensure_ascii=False, indent=1))
    print('valeurs utilisées :')
    print(f"  données        {DONNEES}  ({a.source_donnees})")
    print(f"  voix           {VOIX}{'  ($DOUBLER_VOIX)' if os.environ.get('DOUBLER_VOIX') else ''}")
    print(f"  sorties        {a.sortie}")
    print(f"  voix (défaut)  {VOIX_DEFAUT or '—'}  ({VOIX_DEFAUT_SOURCE})")
    print(f"  moteur (chant) {moteur}{'' if valeur_config(conf, 'moteur') else '  (défaut)'}")
    print(f"  paroles        {'oui' if paroles else 'non'}{'' if valeur_config(conf, 'paroles') is not None else '  (défaut)'}")
    for n in KAGGLE_DEFAUTS:
        print(f"  kaggle.{n:8s} {noms[n] or '— (kaggle.compte manque : doubler --config kaggle.compte <nom>)'}")
    print(f"  jeton Kaggle   {'présent' if JETON.exists() else 'absent'} ({JETON}, jamais affiché)")
    print(f"  outil kaggle   {KAGGLE}{'' if KAGGLE.exists() else '  (absent)'}"
          f"{'  ($VOIXLA_KAGGLE)' if os.environ.get('VOIXLA_KAGGLE') else ''}")
    print(f"  script noyau   {SCRIPT_NOYAU}{'' if SCRIPT_NOYAU.exists() else '  (ABSENT)'}")
    print(f"  ancres genre   {ancres or 'absentes (facultatives : ' + ' ou '.join(map(abrege, ANCRES)) + ')'}")
    print('clés : ' + ' ; '.join(f'{c} ({d})' for c, d in CLES_CONFIG.items()))


# --- Événements pour une interface (--evenements) ---------------------------------------------------------------------

class Evenements:
    """Un événement JSON par ligne, vidé à chaque écriture (fichier ouvert en ajout). Champs toujours présents : t (heure
    ISO locale), type, video (titre de la vidéo : nom de son dossier de sortie ; null pour la commande entière)."""

    def __init__(self):
        self.f = None

    def ouvrir(self, chemin):
        if chemin:
            Path(chemin).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self.f = open(Path(chemin).expanduser(), 'a', encoding='utf-8')

    def __call__(self, type_, video=None, **champs):
        if not self.f:
            return
        ev = {'t': datetime.datetime.now().astimezone().isoformat(timespec='milliseconds'), 'type': type_, 'video': video}
        ev.update({k: v for k, v in champs.items() if v is not None})
        try:
            self.f.write(json.dumps(ev, ensure_ascii=False, default=str) + '\n')
            self.f.flush()
        except OSError:
            pass   # l'interface a pu fermer le fichier : le doublage continue


EV = Evenements()


class Etape:
    """Événements « etape » debut, puis fin (avec duree_s) ou erreur, pour une ou plusieurs vidéos (étapes communes au
    lot : envoi, attente, kaggle, rapatriement)."""

    def __init__(self, etape, videos, message=None):
        self.etape, self.message = etape, message
        self.videos = [videos] if isinstance(videos, str) or videos is None else list(videos)

    def __enter__(self):
        self.t0, self.fini = time.time(), False
        for v in self.videos:
            EV('etape', v, etape=self.etape, etat='debut', message=self.message)
        return self

    def finir(self, erreur=None, message=None):
        if self.fini:
            return
        self.fini = True
        duree = round(time.time() - self.t0, 1)
        for v in self.videos:
            EV('etape', v, etape=self.etape, etat='erreur' if erreur else 'fin', duree_s=duree,
               message=str(erreur) if erreur else (message or self.message))

    def __exit__(self, genre, exc, tb):
        self.finir(exc)
        return False


def lancer(cmd, **k):
    r = subprocess.run(cmd, capture_output=True, text=True, **k)
    if r.returncode:
        raise RuntimeError(f"échec : {' '.join(map(str, cmd[:6]))}…\n{(r.stderr or r.stdout)[-1500:]}")
    return r.stdout


def ff(*args):
    lancer(['nice', '-n', '10', 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)])


def sonder(video):
    return json.loads(lancer(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(video)]))


def moyenne_db(fichier):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(fichier), '-af', 'volumedetect', '-f', 'null', '-'],
                       capture_output=True, text=True)
    m = re.search(r'mean_volume: ([-0-9.]+)', r.stderr)
    if not m:
        raise RuntimeError(f'niveau illisible : {fichier}')
    return float(m.group(1))


def duree_s(fichier):
    return float(lancer(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(fichier)]).strip())


def crete_dbfs(fichier):
    """Crête (dBFS) mesurée en flottant par astats : volumedetect passe par des entiers 16 bits et plafonne à 0 dBFS."""
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(fichier), '-af',
                        'astats=measure_perchannel=none:measure_overall=Peak_level', '-f', 'null', '-'],
                       capture_output=True, text=True)
    m = re.findall(r'Peak level dB: (-?inf|-?[0-9.]+)', r.stderr)
    if r.returncode or not m:
        raise RuntimeError(f'crête illisible : {fichier}')
    return float(m[-1])


CRETE_FLAC_DBFS = -1.0   # crête des FLAC échangés avec Kaggle


def encoder_flac(wav, flac):
    """WAV flottant -> FLAC 24 bits (sans perte au sens utile, environ deux fois plus léger). Le FLAC est en entiers et
    écrêterait au-delà de 0 dBFS, où l'original monte souvent : la crête est ramenée à -1 dBFS par un gain connu, rendu
    ici et transmis au noyau (réglage gain_entree), qui le retire à la lecture. Écart après l'aller-retour : environ
    -140 dBFS (mesuré sur une chanson dont la crête atteint +0,5 dBFS)."""
    crete = crete_dbfs(wav)
    gain = 10 ** ((CRETE_FLAC_DBFS - crete) / 20) if math.isfinite(crete) else 1.0
    ff('-i', wav, '-af', f'volume={gain:.17g}', '-c:a', 'flac', '-sample_fmt', 's32', '-bits_per_raw_sample', '24',
       '-compression_level', '8', '-metadata', f'comment=doubler : gain {gain:.17g} appliqué (crête à -1 dBFS), à retirer', flac)
    return gain


def decoder_flac(flac, wav):
    """Voix isolée revenue de Kaggle en FLAC 24 bits (crête à -1 dBFS) -> WAV flottant au niveau d'origine : le gain
    inscrit par le noyau dans <fichier>.flac.json est retiré. Rend la fiche du noyau."""
    fiche = json.loads(Path(str(flac) + '.json').read_text())
    gain = float(fiche['gain_lineaire'])
    if not gain > 0:
        raise RuntimeError(f'gain illisible dans {flac}.json : {gain}')
    ff('-i', flac, '-af', f'volume={1 / gain:.17g}', '-c:a', 'pcm_f32le', wav)
    return fiche


def kaggle(*args):
    """Commande kaggle avec une limite de temps : une coupure réseau ne doit jamais la bloquer (le 03/11, un « status »
    figé 4 h a gardé le verrou et bloqué les doublages suivants). L'expiration compte comme une panne, et l'appelant réessaie."""
    limite = 120 if args[:2] in (('kernels', 'status'), ('datasets', 'status'), ('datasets', 'files')) else 1800
    try:
        r = subprocess.run([str(KAGGLE), *args], capture_output=True, text=True, timeout=limite)
    except subprocess.TimeoutExpired:
        return 124, f'kaggle {" ".join(args[:2])} : pas de réponse en {limite} s (réseau ?)'
    return r.returncode, (r.stdout or '') + (r.stderr or '')


class Chrono:
    def __init__(self):
        self.t = {}

    def __call__(self, nom, debut):
        self.t[nom] = round(self.t.get(nom, 0) + time.time() - debut, 1)


# --- Voix ------------------------------------------------------------------------------------------------------------

def charger_voix(nom):
    d = VOIX / nom
    fiche = d / 'voix.json'
    if not fiche.exists():
        dispo = ', '.join(sorted(p.parent.name for p in VOIX.glob('*/voix.json'))) or 'aucune'
        raise SystemExit(f'voix inconnue : {nom} (disponibles : {dispo} ; ajouter : doubler --ajouter-voix {nom} <enregistrement>)')
    f = json.loads(fiche.read_text())
    ref = d / f.get('reference', 'reference.wav')
    if not ref.exists():
        raise SystemExit(f'référence absente : {ref}')
    f0 = f.get('hauteur_parole_mediane_hz')
    if not f0:
        raise SystemExit(f'{fiche} : hauteur_parole_mediane_hz manque')
    return {'id': nom, 'affichage': f.get('nom') or nom.capitalize(), 'reference': ref, 'f0': float(f0), 'fiche': f}


def liste_voix(en_json=False):
    fiches = sorted(VOIX.glob('*/voix.json'))
    if en_json:
        voix = []
        for p in fiches:
            f = json.loads(p.read_text())
            ref = p.parent / f.get('reference', 'reference.wav')
            f0 = f.get('hauteur_parole_mediane_hz')
            voix.append({'id': p.parent.name, **f, 'chemin_reference': str(ref), 'reference_presente': ref.exists(),
                         'dossier': str(p.parent), 'defaut': p.parent.name == VOIX_DEFAUT,
                         'centre_chant_hz': round(FACTEUR_CHANT * f0, 1) if f0 else None})
        print(json.dumps(voix, ensure_ascii=False, indent=1))
        return
    if not fiches:
        print(f'aucune voix dans {VOIX} (ajouter : doubler --ajouter-voix <nom> <enregistrement>)')
        return
    print(f"{'voix':14s} {'nom affiché':16s} {'parole':>8s} {'chant':>8s} {'durée':>7s}  date        origine")
    for p in fiches:
        f = json.loads(p.read_text())
        f0 = f.get('hauteur_parole_mediane_hz') or 0
        defaut = ' (défaut)' if p.parent.name == VOIX_DEFAUT else ''
        print(f"{p.parent.name:14s} {(f.get('nom') or '') + defaut:16s} {f0:6.1f} Hz {FACTEUR_CHANT * f0:5.0f} Hz "
              f"{f.get('duree_s', 0):5.1f} s  {f.get('date', ''):10s}  {f.get('origine', '')[:70]}")
    if not VOIX_DEFAUT:
        print('aucune voix par défaut : --voix NOM à chaque doublage, ou une fois pour toutes : doubler --config voix NOM')
    elif VOIX_DEFAUT not in [p.parent.name for p in fiches]:
        print(f'voix par défaut « {VOIX_DEFAUT} » ({VOIX_DEFAUT_SOURCE}) absente de {VOIX} : doubler --config voix NOM')


NOTES = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B']


def note(f):
    import numpy as np
    m = int(round(69 + 12 * np.log2(f / 440)))
    return f'{NOTES[m % 12]}{m // 12 - 1}'


def lire_brut(fichier, filtre=None, sr=44100):
    import numpy as np
    cmd = ['ffmpeg', '-v', 'error', '-i', str(fichier), '-vn', '-ac', '1', '-ar', str(sr)]
    if filtre:
        cmd += ['-af', filtre]
    r = subprocess.run(cmd + ['-f', 'f32le', '-'], capture_output=True)
    if r.returncode or not r.stdout:
        raise SystemExit(f'son illisible : {fichier}\n{r.stderr.decode(errors="replace")[-500:]}')
    return np.frombuffer(r.stdout, np.float32).astype(np.float64)


def yin(x, sr=16000, W=640, H=160, fmin=60, fmax=500, seuil=0.15):
    """Hauteur (Hz, 0 = non voisé) et niveau (dBFS) toutes les 10 ms : YIN vectorisé, léger."""
    import numpy as np
    tmin, tmax = int(sr / fmax), int(sr / fmin)
    n = max(0, (len(x) - W - tmax) // H)
    f0, rms = np.zeros(n), np.full(n, -120.0)
    for d in range(0, n, 2000):
        idx = np.arange(d, min(n, d + 2000))[:, None] * H + np.arange(W + tmax)[None, :]
        fr = x[idx]
        a = fr[:, :W]
        rms[d:d + len(fr)] = 10 * np.log10((a ** 2).mean(1) + 1e-12)
        N = 2048
        r = np.fft.irfft(np.conj(np.fft.rfft(a, N)) * np.fft.rfft(fr, N), N)[:, :tmax + 1]
        c = np.cumsum(np.pad(fr ** 2, ((0, 0), (1, 0))), axis=1)
        dt = c[:, W][:, None] + (c[:, W:W + tmax + 1] - c[:, :tmax + 1]) - 2 * r
        cm = np.ones_like(dt)
        cm[:, 1:] = dt[:, 1:] * np.arange(1, tmax + 1) / (np.cumsum(dt[:, 1:], axis=1) + 1e-12)
        for i in range(len(fr)):
            row = cm[i]
            cand = np.where(row[tmin:] < seuil)[0]
            if not len(cand):
                continue
            t = tmin + cand[0]
            while t + 1 <= tmax and row[t + 1] < row[t]:
                t += 1
            if 1 <= t < tmax:
                y0, y1, y2 = row[t - 1], row[t], row[t + 1]
                den = y0 - 2 * y1 + y2
                t = t + (0.5 * (y0 - y2) / den if den != 0 else 0)
            f0[d + i] = sr / t
    return f0, rms


def enveloppe(x, pas=441, fen=1764):
    import numpy as np
    n = len(x) // pas + 1
    p = np.pad(x ** 2, (fen // 2, fen // 2 + pas))
    c = np.concatenate([[0.0], np.cumsum(p)])
    i = np.arange(n) * pas
    e = 10 * np.log10((c[i + fen] - c[i]) / fen + 1e-12)
    lin = 10 ** (e / 10)
    return e, 10 * np.log10(np.convolve(lin, np.ones(15) / 15, mode='same') + 1e-12)


def hauteur_parole(fichier):
    """Médiane et 10e-90e centiles de la hauteur de parole : YIN, puis les sauts d'octave (fréquents sur les voix graves)
    ramenés autour d'une première médiane. Vérifié contre RMVPE sur deux enregistrements de parole."""
    import numpy as np
    f0, rms = yin(lire_brut(fichier, sr=16000), seuil=0.1)
    v = f0[(f0 > 0) & (rms > -50)]
    if len(v) < 100:
        raise SystemExit('hauteur introuvable : la référence ne contient pas assez de voix')
    m = np.median(v)
    v = np.where(v > 1.6 * m, v / 2, np.where(v < 0.6 * m, v * 2, v))
    return tuple(float(np.percentile(v, q)) for q in (50, 10, 90))


def ajouter_voix(nom, enregistrement):
    """Nettoie un enregistrement de parole et en fait une voix : mono, passe-haut 70 Hz, silences du début et de la fin
    coupés, pauses raccourcies, meilleur extrait continu de 20-25 s, -26 LUFS, hauteur de parole mesurée."""
    import numpy as np
    if not re.fullmatch(NOM_VOIX, nom):
        raise SystemExit('nom de voix : minuscules, chiffres, - ou _ (ex. marie, jean-b)')
    src = Path(enregistrement).expanduser().resolve()
    if not src.is_file():
        raise SystemExit(f'fichier introuvable : {src}')
    d = VOIX / nom
    if (d / 'voix.json').exists():
        raise SystemExit(f'la voix {nom} existe déjà ({d}) : choisir un autre nom ou effacer ce dossier')
    sr = 44100
    brut = lire_brut(src)
    x = lire_brut(src, 'highpass=f=70')
    alertes = []
    satures = int((np.abs(brut) >= 0.999).sum())
    if satures > max(10, 1e-5 * len(brut)):
        alertes.append(f'saturé : {satures} échantillons à 0 dBFS (enregistrer moins fort)')
    env, lisse = enveloppe(x)
    seuil = max(float(np.percentile(lisse, 95)) - 35, -60.0)
    actif = lisse > seuil
    bruit, parole = float(np.percentile(env, 10)), float(np.percentile(env, 90))
    if parole - bruit < 25:
        alertes.append(f'bruité : la parole n\'est que {parole - bruit:.0f} dB au-dessus du fond (au moins 25 conseillés)')
    if not actif.any():
        raise SystemExit('aucune parole détectée')
    # silences du début et de la fin coupés, pauses de plus de 0,35 s ramenées à 0,25 s
    k0, k1 = max(0, int(np.argmax(actif)) - 10), min(len(actif) - 1, len(actif) - int(np.argmax(actif[::-1])) + 10)
    garde, k = [], k0
    while k < k1:
        if actif[k]:
            j = k
            while j < k1 and actif[j]:
                j += 1
            garde.append((k, j))
            k = j
        else:
            j = k
            while j < k1 and not actif[j]:
                j += 1
            pause = j - k
            garde.append((k, j) if pause <= 35 else (k, k + 12))
            if pause > 35:
                garde.append((j - 13, j))
            k = j
    morceaux, bornes, actifs = [], [], []
    for a, b in garde:
        y = x[a * 441:b * 441].copy()
        if len(y) > 882:
            f = np.linspace(0, 1, 441)
            y[:441] *= f
            y[-441:] *= f[::-1]
        if not actif[a] and len(morceaux):
            bornes.append(sum(len(m) for m in morceaux) + len(y) // 2)
        morceaux.append(y)
        actifs.append(np.repeat(actif[a:b], 441)[:len(y)])
    y = np.concatenate(morceaux)
    act = np.concatenate(actifs)
    parole_s = float(act.mean() * len(y) / sr)
    if parole_s < 15:
        alertes.append(f'trop court : {parole_s:.0f} s de parole (au moins 15 ; 30 à 60 conseillées)')
    # meilleur extrait de 20 à 25 s, borné par des pauses : le plus de parole, le moins de saturation
    if len(y) <= 25 * sr:
        a, b = 0, len(y)
    else:
        bornes = [0] + bornes + [len(y)]
        cands = [(i, j) for i in bornes for j in bornes if 20 * sr <= j - i <= 25 * sr]
        if not cands:
            centre = int(np.argmax(np.convolve(act, np.ones(sr), 'same')))
            cands = [(max(0, centre - int(11.25 * sr)), min(len(y), centre + int(11.25 * sr)))]
        a, b = max(cands, key=lambda c: (act[c[0]:c[1]].mean(), c[1] - c[0]))
    extrait = y[a:b]
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / '.brut.wav'
    import wave
    with wave.open(str(tmp), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(extrait / (np.abs(extrait).max() + 1e-9) * 0.5, -1, 1) * 32767).astype('<i2').tobytes())
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(tmp), '-af', 'ebur128=peak=sample', '-f', 'null', '-'],
                       capture_output=True, text=True).stderr
    lufs = float(re.findall(r'I:\s+(-?[0-9.]+) LUFS', r)[-1])
    crete = float(re.findall(r'Peak:\s+(-?[0-9.inf]+) dBFS', r)[-1])
    gain = -26 - lufs
    if crete + gain > -1:
        alertes.append(f'crêtes fortes : sonie ramenée à {-1 - crete + lufs:.1f} LUFS au lieu de -26 pour ne pas saturer')
        gain = -1 - crete
    ff('-i', tmp, '-af', f'volume={gain:.2f}dB', '-c:a', 'pcm_s16le', d / 'reference.wav')
    tmp.unlink()
    med, p10, p90 = hauteur_parole(d / 'reference.wav')
    fiche = {'nom': nom.replace('-', ' ').replace('_', ' ').title(), 'reference': 'reference.wav',
             'duree_s': round(len(extrait) / sr, 2), 'frequence_hz': sr, 'canaux': 1, 'sonie_lufs': round(lufs + gain, 1),
             'hauteur_parole_mediane_hz': round(med, 1), 'hauteur_parole_10_90_hz': [round(p10, 1), round(p90, 1)],
             'note_mediane': note(med), 'origine': f'{src} (doubler --ajouter-voix : passe-haut 70 Hz, pauses raccourcies, '
             f'extrait {a / sr:.1f}–{b / sr:.1f} s de la version nettoyée, normalisé)',
             'consentement': 'à confirmer : uniquement la voix d\'une personne d\'accord pour être clonée',
             'parole_s': round(parole_s, 1), 'rapport_signal_bruit_db': round(parole - bruit, 1), 'alertes': alertes,
             'date': datetime.date.today().isoformat()}
    (d / 'voix.json').write_text(json.dumps(fiche, ensure_ascii=False, indent=2))
    print(f"voix {nom} ajoutée ({d}) : {fiche['duree_s']} s, parole {med:.0f} Hz ({note(med)}), centre de chant "
          f"{FACTEUR_CHANT * med:.0f} Hz ; envoyée sur Kaggle (privé) à la première utilisation")
    for al in alertes:
        print('  ATTENTION :', al)


# --- Choix des flux et de l'extrait -----------------------------------------------------------------------------------

def flux(info, genre):
    return [s for s in info['streams'] if s.get('codec_type') == genre]


def flux_video(info):
    v = [s for s in flux(info, 'video') if not s.get('disposition', {}).get('attached_pic')]
    if not v:
        raise RuntimeError('aucune image dans ce fichier')
    return v[0]


def flux_audio(info):
    a = flux(info, 'audio')
    if not a:
        raise RuntimeError('aucun son dans ce fichier')
    return next((s for s in a if s.get('disposition', {}).get('default')), a[0])


def image_cle(video, info, t):
    """Dernière image clé à t ou avant (temps relatif au début du fichier) : l'image est copiée sans réencodage, l'extrait
    commence donc là, son et image ensemble."""
    v = flux_video(info)
    debut_fichier = float(info['format'].get('start_time', 0) or 0)
    absolu = t + debut_fichier
    sortie = lancer(['ffprobe', '-v', 'error', '-select_streams', str(v['index']), '-skip_frame', 'nokey',
                     '-show_entries', 'frame=pts_time,best_effort_timestamp_time', '-of', 'csv=p=0',
                     '-read_intervals', f'{max(0.0, absolu - 30):.3f}%{absolu + 0.05:.3f}', str(video)])
    temps = []
    for ligne in sortie.split():
        for champ in ligne.split(','):
            try:
                temps.append(float(champ))
                break
            except ValueError:
                continue
    avant = [x for x in temps if x <= absolu + 1e-3]
    return (max(avant) - debut_fichier) if avant else t


def preparer_source(video, info, debut, duree, travail):
    a = flux_audio(info)
    src = {'video': str(video), 'flux_audio': a['index'], 'langue': a.get('tags', {}).get('language'),
           'canaux_origine': a.get('channels'), 'codec_origine': a.get('codec_name')}
    if debut is not None or duree is not None:
        demande = debut or 0.0
        k = image_cle(video, info, demande)
        fin = (demande + duree) if duree else float(info['format']['duration'])
        src.update({'debut_demande': demande, 'debut': round(k + 0.001, 3), 'duree': round(fin - k, 3)})
        decalage = 0.0
    else:
        debut_fichier = float(info['format'].get('start_time', 0) or 0)
        decalage = float(a.get('start_time', 0) or 0) - debut_fichier
    src['decalage_son'] = round(decalage, 4) if abs(decalage) > 0.001 else 0.0
    coupe = (['-ss', src['debut'], '-t', src['duree']] if 'debut' in src else [])
    ff(*coupe, '-i', video, '-map', f"0:{a['index']}", '-ac', 2, '-ar', 44100, '-c:a', 'pcm_f32le', travail / 'original.wav')
    with open(travail / 'source.json', 'w') as f:
        json.dump(src, f, ensure_ascii=False, indent=1)
    return src


# --- Kaggle ----------------------------------------------------------------------------------------------------------

def envoyer_jeu(travaux, voix, dossier):
    """Une version du jeu de données privé par session : la référence de la voix choisie et le son des vidéos seulement,
    en FLAC 24 bits (gain de chaque vidéo noté dans tr['gain_entree'], à transmettre au noyau)."""
    jeu = dossier / 'jeu'
    shutil.rmtree(jeu, ignore_errors=True)
    jeu.mkdir(parents=True)
    shutil.copy2(voix['reference'], jeu / f"voix-{voix['id']}.wav")
    taille, taille_wav = 0, 0
    for tr in travaux:
        tr['gain_entree'] = encoder_flac(tr['travail'] / 'original.wav', jeu / tr['entree'])
        tr['taille_envoi'] = (jeu / tr['entree']).stat().st_size
        taille += tr['taille_envoi']
        taille_wav += (tr['travail'] / 'original.wav').stat().st_size
    with open(jeu / 'dataset-metadata.json', 'w') as f:
        json.dump({'title': JEU.split('/')[1], 'id': JEU, 'licenses': [{'name': 'unknown'}]}, f)
    existe = kaggle('datasets', 'status', JEU)[0] == 0
    dire(f'Kaggle : envoi du son de {len(travaux)} vidéo(s) ({taille / 1e6:.0f} Mo en FLAC au lieu de {taille_wav / 1e6:.0f} Mo '
         f'en WAV) et de la voix {voix["id"]} dans le jeu privé {JEU}')
    code, texte = (kaggle('datasets', 'version', '-p', str(jeu), '-m', f"{len(travaux)} video(s), voix {voix['id']}") if existe
                   else kaggle('datasets', 'create', '-p', str(jeu)))
    if code or re.search(r'error|fail|invalid', texte, re.I):
        raise RuntimeError('Kaggle : jeu de données refusé : ' + texte.strip()[-500:])
    debut = time.time()
    while True:
        _, fichiers = kaggle('datasets', 'files', JEU)
        _, etat = kaggle('datasets', 'status', JEU)
        if all(tr['entree'] in fichiers for tr in travaux) and re.search('ready', etat, re.I):
            break
        if time.time() - debut > 20 * 60:
            raise RuntimeError('Kaggle : jeu de données toujours en préparation après 20 min')
        time.sleep(10)
    time.sleep(30)   # le noyau monte la dernière version prête
    shutil.rmtree(jeu, ignore_errors=True)


def estimer_noyau_s(durees_s, pas, sorties, nettoyage=True, mode='auto', moteur=MOTEUR_DEFAUT, paroles=True):
    """Durée probable d'une session (de l'envoi du noyau à sa fin), d'après la durée audio des vidéos et les rapports
    passés (rapport.json sous <sorties>/*/travail/*/) : démarrage + installation + chargement des modèles, puis par
    seconde d'audio la séparation et l'analyse, le nettoyage (chant) et la conversion (proportionnelle aux pas de
    diffusion ; voix nettoyée : voix principale, chœurs et passages repris par la piste de secours convertis à part,
    environ 1,5 fois plus), enfin les paroles (chargement de Whisper large-v3 et du traducteur, puis transcription).
    Le mode n'est connu qu'après la séparation : en mode auto, l'estimation suppose du chant (la plus longue). Défauts
    prudents sans historique (nettoyage : banc d'essai du 04/11/2026 ; paroles : essai du même jour)."""
    import statistics
    fixes, extras, sep, net, conv, conv_net, par_fixes, par = [], [], [], [], [], [], [], []
    rapports = sorted(Path(sorties).glob('*/travail/*/rapport.json'), key=lambda p: p.stat().st_mtime)[-30:]
    for p in rapports:
        try:
            r = json.loads(p.read_text())
            d, tps = float(r['duree_s']), r.get('temps', {})
            sk = r.get('temps_local', {}).get('session_kaggle', {})
            v = r.get('variantes', {}).get('voix_convertie.flac', {})
            nettoyee = bool((r.get('nettoyage') or {}).get('applique'))
            if sk.get('installation_s') is not None:
                fixes.append(sk['installation_s'] + sk.get('chargement_modeles_s', 0)
                             + r['temps_local'].get('attente_demarrage_noyau_s', 0))
                if sk.get('chargement_nettoyage_s') is not None or sk.get('chargement_yingmusic_s') is not None:
                    extras.append((sk.get('chargement_nettoyage_s') or 0) + (sk.get('chargement_yingmusic_s') or 0))
            if d > 5 and tps.get('separation_s'):
                sep.append((tps['separation_s'] + tps.get('analyse_s', 0)) / d)
            if d > 5 and nettoyee and tps.get('nettoyage_s'):
                net.append(tps['nettoyage_s'] / d)
            if d > 5 and v.get('conversion_s') and v.get('pas_diffusion'):
                (conv_net if nettoyee else conv).append(v['conversion_s'] / d / v['pas_diffusion'])
            if ('paroles_copie_s' in sk and sk.get('paroles_total_s') is not None
                    and r.get('temps_local', {}).get('videos_dans_la_session') == 1):
                # tout ce qui n'est pas transcription (démarrage du processus, chargements, traduction) compte comme fixe ;
                # seulement les noyaux qui copient les modèles sur le disque local (avant : 5 min de chargement)
                par_fixes.append(max(float(sk['paroles_total_s']) - float(tps.get('paroles_s') or 0), 0.0))
            if d > 5 and tps.get('paroles_s'):
                par.append(tps['paroles_s'] / d)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    fixe = statistics.median(fixes) if fixes else 120.0
    t_sep = statistics.median(sep) if sep else 0.8
    t_conv = statistics.median(conv) if conv else 0.045
    chant = mode != 'parole'
    if nettoyage and chant:
        fixe += statistics.median(extras) if extras else 90.0   # chargement des deux Roformer et de YingMusic-SVC
        t_sep += statistics.median(net) if net else 2.3          # banc : ~9 min de plus pour 4 min de chant
        t_conv = statistics.median(conv_net) if conv_net else 1.5 * t_conv
    elif chant and moteur == 'yingmusic':
        fixe += 30.0   # chargement de YingMusic-SVC
    if paroles:
        fixe += statistics.median(par_fixes) if par_fixes else 90.0   # Whisper large-v3 (3 Go) et NLLB-200 (2,5 Go)
        t_sep += statistics.median(par) if par else 0.25
    return round(60 + fixe + sum(durees_s) * (t_sep + t_conv * pas))   # 60 s : file Kaggle et écriture des sorties


PANNE_MAXI_S = 3 * 3600   # coupure réseau tolérée pendant le suivi et le rapatriement (le noyau, lui, continue sur Kaggle)
RESEAU = re.compile(r'pas de réponse|timed? ?out|connection|connexion|network|réseau|temporary failure|name or service|'
                    r'resolve|unreachable|max retries|ssl|reset by peer|50[234]|bad gateway|service unavailable', re.I)


def panne_reseau(code, texte):
    """Échec d'une commande kaggle dû au réseau (à attendre), plutôt qu'à une vraie erreur (à signaler vite)."""
    return code == 124 or bool(RESEAU.search(texte or ''))


def pousser_noyau(reglages, dossier, attente_maxi, noyau, jeux, sources=(), videos=()):
    """Écrit et pousse le noyau (GPU T4 + internet) ; attend une place si les deux sessions GPU du compte sont prises.
    Rend (heure de la poussée, numéro de version Kaggle ou None)."""
    envoi = dossier / f"envoi-{noyau.split('/')[1]}"
    shutil.rmtree(envoi, ignore_errors=True)
    envoi.mkdir(parents=True)
    script = SCRIPT_NOYAU.read_text().replace(
        '__REGLAGES__', base64.b64encode(json.dumps(reglages).encode()).decode())
    # Kaggle exécute le script en carnet, sans fichier : il reçoit sa propre source, pour ses étapes à part (nettoyage,
    # YingMusic-SVC), chacune dans son processus
    marque = "SOURCE = '__SOURCE__'"
    if script.count(marque) != 1:
        raise RuntimeError(f'kaggle/doubler_kaggle.py : « {marque} » attendu une fois')
    script = script.replace(marque, f"SOURCE = '{base64.b64encode(script.encode()).decode()}'")
    (envoi / 'doubler_kaggle.py').write_text(script)
    with open(envoi / 'kernel-metadata.json', 'w') as f:
        json.dump({'id': noyau, 'title': noyau.split('/')[1], 'code_file': 'doubler_kaggle.py', 'language': 'python',
                   'kernel_type': 'script', 'is_private': True, 'enable_gpu': True, 'enable_tpu': False,
                   'enable_internet': True, 'machine_shape': 'NvidiaTeslaT4', 'dataset_sources': list(jeux),
                   'competition_sources': [], 'kernel_sources': list(sources), 'model_sources': []}, f, indent=1)
    debut, attente = time.time(), None
    try:
        while True:   # deux sessions GPU au plus sur le compte : la suivante attend une place
            code, texte = kaggle('kernels', 'push', '-p', str(envoi))
            if re.search(r'successfully pushed', texte, re.I):
                dire('Kaggle : noyau GPU lancé (' + texte.strip().splitlines()[-1].split('.')[0] + ')')
                break
            if not re.search(r'GPU session count|concurrent|too many', texte, re.I) or time.time() - debut > attente_maxi:
                raise RuntimeError('Kaggle : noyau refusé : ' + texte.strip()[-500:])
            if attente is None:
                dire('Kaggle : sessions GPU toutes prises, attente d\'une place')
                attente = Etape('attente', videos, 'sessions GPU Kaggle toutes prises : attente d\'une place').__enter__()
            time.sleep(60)
    except BaseException as e:
        if attente:
            attente.finir(e)
        raise
    if attente:
        attente.finir()
    m = re.search(r'version (\d+)', texte, re.I)
    return time.time(), (int(m.group(1)) if m else None)


def suivre_noyau(noyau, pousse, attente_maxi, videos=(), estimation_s=None, origine_limite=None):
    """Suit l'état du noyau jusqu'à la fin ; rend l'état final (COMPLETE, ERROR…). Une coupure réseau n'arrête pas le
    suivi : on réessaie à intervalles croissants (20 s à 5 min) jusqu'à PANNE_MAXI_S de coupure continue ; le noyau
    continue sur Kaggle et « doubler --reprendre » le retrouve. La limite attente_maxi ne compte pas les coupures."""
    origine = pousse if origine_limite is None else origine_limite
    etat, dit, vu, pannes = '', '', False, 0
    panne_depuis, panne_totale, pause = None, 0.0, 20.0
    etape = Etape('kaggle', videos, f"noyau {noyau}" + (f', environ {estimation_s / 60:.0f} min' if estimation_s else '')).__enter__()
    try:
        while True:
            time.sleep(pause)
            code, texte = kaggle('kernels', 'status', noyau)
            maintenant = time.time()
            if code:
                if panne_reseau(code, texte):
                    if panne_depuis is None:
                        panne_depuis = maintenant
                        dire(f"Kaggle injoignable ({texte.strip().splitlines()[-1][-120:] if texte.strip() else code}) : "
                             f'nouvel essai à intervalles croissants, jusqu\'à {PANNE_MAXI_S / 3600:.0f} h de coupure')
                    for v in videos:
                        EV('kaggle', v, statut='injoignable', depuis_s=round(maintenant - pousse),
                           panne_s=round(maintenant - panne_depuis), estimation_s=estimation_s)
                    if maintenant - panne_depuis > PANNE_MAXI_S:
                        raise RuntimeError(f'Kaggle injoignable depuis {(maintenant - panne_depuis) / 3600:.1f} h ; le noyau '
                                           'a pu finir sur Kaggle : relancer plus tard « doubler --reprendre »')
                    pause = min(300.0, pause * 1.5)
                    continue
                pannes += 1   # autre erreur (droits, noyau introuvable…) : on ne s'acharne pas
                if pannes >= 15:
                    raise RuntimeError('Kaggle : état indisponible : ' + texte.strip()[-300:])
                continue
            if panne_depuis is not None:
                panne_totale += maintenant - panne_depuis
                dire(f'Kaggle de nouveau joignable, après {(maintenant - panne_depuis) / 60:.0f} min de coupure')
                panne_depuis = None
            pannes, pause = 0, 20.0
            m = re.search(r'status "?(?:\w+\.)?(\w+)"?', texte, re.I)
            etat = (m.group(1) if m else 'inconnu').upper()
            for v in videos:
                EV('kaggle', v, statut=etat.lower(), depuis_s=round(maintenant - pousse), estimation_s=estimation_s)
            if etat != dit:
                dire(f'Kaggle : {etat.lower()} ({(maintenant - pousse) / 60:.1f} min)')
                dit = etat
            en_cours = bool(re.search(r'QUEUED|RUNNING|NEW|PENDING', etat))
            vu = vu or en_cours
            if not en_cours and (vu or maintenant - pousse - panne_totale > 300):
                break
            if maintenant - origine - panne_totale > attente_maxi:
                raise RuntimeError(f'Kaggle : noyau toujours en cours après {attente_maxi // 60} min')
    except BaseException as e:
        etape.finir(e)
        raise
    # un échec du noyau est signalé ici, mais son journal est rapatrié quand même (message d'erreur plus bas)
    etape.finir(None if etat == 'COMPLETE' else f'noyau terminé en {etat.lower()}')
    return etat


def rapatrier_noyau(noyau, retour, etat, motif=None, attendu='session.json', videos=(), etapes=None):
    """Télécharge les sorties du noyau dans retour (coupures réseau tolérées comme pour le suivi), puis vérifie qu'il n'a
    pas été refusé et qu'il a rendu le fichier attendu. etapes (dict) : l'étape « rapatriement » y est laissée ouverte
    en cas de réussite, pour que l'appelant la close après le décodage des fichiers. Rend la durée du téléchargement."""
    debut = time.time()
    shutil.rmtree(retour, ignore_errors=True)
    retour.mkdir(parents=True)
    rapatriement = Etape('rapatriement', videos, 'téléchargement des résultats du noyau').__enter__()
    try:
        essais, pause = 0, 15.0
        while True:
            code, texte = kaggle('kernels', 'output', noyau, '-p', str(retour), '--force', *(['--file-pattern', motif] if motif else []))
            if code == 0:
                break
            essais += 1
            if panne_reseau(code, texte):
                if time.time() - debut > PANNE_MAXI_S:
                    raise RuntimeError('Kaggle injoignable : sorties du noyau non rapatriées ; relancer plus tard « doubler --reprendre »')
                if essais == 1:
                    dire('Kaggle injoignable pendant le rapatriement : nouvel essai à intervalles croissants')
            elif essais >= 5:
                break   # pas une coupure : la vérification ci-dessous dira ce qui manque
            time.sleep(pause)
            pause = min(300.0, pause * 1.5)
    except BaseException as e:
        rapatriement.finir(e)
        raise
    duree = round(time.time() - debut, 1)
    lignes = []
    for f in retour.glob('*.log'):
        try:
            lignes += [l.get('data', '').rstrip() for l in json.loads(f.read_text())]
        except ValueError:
            pass
    journal = [l for l in '\n'.join(lignes).splitlines() if l.strip()]
    refus = next((l for l in journal if 'ACCÈS REFUSÉ' in l or 'FICHIER ABSENT' in l), None)
    if refus:
        rapatriement.finir(refus)
        raise RuntimeError('Kaggle : ' + refus)
    if etat != 'COMPLETE' or not (retour / attendu).exists():
        utiles = [l for l in journal if not re.search(r'Warning|warn\(|NbConvert|it/s\]|s/it\]', l)]
        rapatriement.finir(f'noyau terminé en {etat.lower()} sans résultat')
        raise RuntimeError(f'Kaggle : noyau terminé en {etat.lower()} sans résultat ; fin du journal :\n' + '\n'.join(utiles[-25:]))
    if etapes is None:
        rapatriement.finir()
    else:
        etapes['rapatriement'] = rapatriement
    return duree


def lancer_noyau(reglages, dossier, retour, attente_maxi, noyau=None, jeux=None, sources=(), motif=None,
                 attendu='session.json', videos=(), estimation_s=None, etapes=None, apres_poussee=None):
    """Pousse le noyau, suit son état, rapatrie ses sorties dans retour. apres_poussee(heure, version) est appelé dès
    que Kaggle a accepté le noyau (fichier de session pour --reprendre)."""
    noyau = noyau or NOYAU
    jeux = (JEU,) if jeux is None else jeux
    pousse, version = pousser_noyau(reglages, dossier, attente_maxi, noyau, jeux, sources, videos)
    if apres_poussee:
        apres_poussee(pousse, version)
    etat = suivre_noyau(noyau, pousse, attente_maxi, videos, estimation_s)
    fin = time.time()
    rapatriement_s = rapatrier_noyau(noyau, retour, etat, motif, attendu, videos, etapes)
    return {'pousse_epoch': pousse, 'version_noyau': version, 'noyau_depuis_envoi_s': round(fin - pousse, 1),
            'rapatriement_s': rapatriement_s}


# --- Mixage et remise sur la vidéo -----------------------------------------------------------------------------------

# Mix v2. Nuances : la voix convertie suit l'enveloppe de niveau de la voix d'origine (couplets doux, refrains
# puissants). Seed-VC et la compression aplatissent l'interprétation : sur un duo homme/femme, la voix d'origine varie de
# 5,6 dB d'une tranche de 10 s à l'autre, la voix convertie de 3 dB. Enveloppes mesurées sous 4 kHz (les « s » n'y
# comptent pas : le dé-esseur s'en charge), fenêtres RMS de 50 ms toutes les 10 ms, écart rapporté à son médian,
# borné, puis lissé ; nul hors des trames où les deux voix sont présentes (le bruit des silences n'est pas remonté).
NUANCES = {'trame_s': 0.01, 'fenetre_trames': 5, 'lissage_trames': 15, 'borne_db': 6.0, 'presence_db': 35.0,
           'plancher_dbfs': -70.0, 'bande_hz': 4000}
# « S » adoucis : deesser de ffmpeg, réglé (i=0,45) pour une voix au niveau actif DEESSER_NIVEAU_DBFS ; il dépend du
# niveau (±6 dB d'entrée : de -1,6 à -13,7 dB sur les « s »), d'où une mise au niveau avant et le gain inverse après.
# Mesuré sur la voix convertie d'un duo homme/femme : « s » -7 dB (bande 5-9 kHz), voyelles -0,02 dB (brillance -0,3 dB).
DEESSER = 'deesser=i=0.45:m=0.5:f=0.5'
DEESSER_NIVEAU_DBFS = -18.5
LIMITE = 10 ** (-1 / 20)   # limiteur final : -1 dBTP (0,891), au lieu de 0,95 sur les échantillons jusqu'au 04/11/2026


def energies_trames(fichier, filtre=None, sr=16000, trame_s=0.01):
    """Énergie moyenne (carré) de chaque trame de 10 ms du fichier mis en mono, lue en continu par ffmpeg (jamais le
    fichier entier en mémoire : une vidéo d'une heure passe aussi)."""
    import numpy as np
    pas = int(sr * trame_s)
    cmd = ['nice', '-n', '10', 'ffmpeg', '-v', 'error', '-i', str(fichier), '-vn', '-ac', '1', '-ar', str(sr),
           *(['-af', filtre] if filtre else []), '-f', 'f32le', '-']
    with tempfile.TemporaryFile() as erreurs:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=erreurs)
        morceaux, reste = [], np.zeros(0, np.float32)
        while True:
            b = proc.stdout.read(pas * 4 * 1000)   # 10 s
            if not b:
                break
            x = np.concatenate([reste, np.frombuffer(b[:len(b) // 4 * 4], np.float32)])
            n = len(x) // pas * pas
            morceaux.append((x[:n].astype(np.float64).reshape(-1, pas) ** 2).mean(1))
            reste = x[n:]
        proc.stdout.close()
        if proc.wait():
            erreurs.seek(0)
            raise RuntimeError(f'lecture impossible : {fichier}\n{erreurs.read().decode(errors="replace")[-800:]}')
    return np.concatenate(morceaux) if morceaux else np.zeros(0)


def niveau_actif_db(e, presence_db=35.0, plancher=-70.0):
    """Niveau (dBFS) des trames où le son est présent : à moins de presence_db sous le haut de l'enveloppe."""
    import numpy as np
    if not len(e):
        return None
    L = 10 * np.log10(e + 1e-12)
    actif = L > max(float(np.percentile(L, 95)) - presence_db, plancher)
    return float(10 * np.log10(e[actif].mean() + 1e-12)) if actif.any() else None


def courbe_nuances(e_src, e_conv, R=NUANCES):
    """Correction (dB, une valeur par trame de 10 ms) pour que la voix convertie suive les nuances de la voix d'origine."""
    import numpy as np
    n = min(len(e_src), len(e_conv))
    k = np.ones(R['fenetre_trames']) / R['fenetre_trames']
    Ls = 10 * np.log10(np.convolve(e_src[:n], k, 'same') + 1e-12)
    Lc = 10 * np.log10(np.convolve(e_conv[:n], k, 'same') + 1e-12)

    def presente(L):
        return L > max(float(np.percentile(L, 95)) - R['presence_db'], R['plancher_dbfs'])
    deux = presente(Ls) & presente(Lc)
    info = {'trames_deux_voix': round(float(deux.mean()), 3)}
    if deux.sum() < 100:   # moins d'une seconde en commun : rien à suivre
        return np.zeros(n), {**info, 'appliquee': False}
    d = Ls - Lc
    centre = float(np.median(d[deux]))
    brut = np.where(deux, d - centre, 0.0)
    c = np.clip(brut, -R['borne_db'], R['borne_db'])
    h = np.hanning(R['lissage_trames'] + 2)[1:-1]
    c = np.convolve(c, h / h.sum(), 'same')
    info.update({'appliquee': True, 'ecart_median_db': round(centre, 2),
                 'part_bornee': round(float((np.abs(brut[deux]) > R['borne_db']).mean()), 3),
                 'correction_p5_p95_db': [round(float(v), 2) for v in np.percentile(c[deux], [5, 95])]})
    return c, info


def appliquer_courbe(entree, sortie, c_db, trame_s=0.01, sr=44100):
    """sortie = entree x 10^(c/20), gain interpolé entre les centres des trames ; lu et écrit en continu (ffmpeg)."""
    import numpy as np
    ch = int(sonder(entree)['streams'][0]['channels'])
    centres = (np.arange(len(c_db)) + 0.5) * trame_s
    g = 10 ** (np.asarray(c_db, np.float64) / 20)
    with tempfile.TemporaryFile() as err_l, tempfile.TemporaryFile() as err_e:
        lecteur = subprocess.Popen(['nice', '-n', '10', 'ffmpeg', '-v', 'error', '-i', str(entree), '-ar', str(sr),
                                    '-f', 'f32le', '-'], stdout=subprocess.PIPE, stderr=err_l)
        ecrivain = subprocess.Popen(['nice', '-n', '10', 'ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(sr),
                                     '-ac', str(ch), '-i', '-', '-c:a', 'pcm_f32le', str(sortie)],
                                    stdin=subprocess.PIPE, stderr=err_e)
        pos = 0
        try:
            while True:
                b = lecteur.stdout.read(sr * 10 * ch * 4)
                if not b:
                    break
                x = np.frombuffer(b[:len(b) // (4 * ch) * 4 * ch], np.float32).reshape(-1, ch)
                gi = np.interp((pos + np.arange(len(x))) / sr, centres, g) if len(g) else np.ones(len(x))
                ecrivain.stdin.write((x * gi[:, None]).astype(np.float32).tobytes())
                pos += len(x)
        finally:
            ecrivain.stdin.close()
            lecteur.stdout.close()
            codes = (lecteur.wait(), ecrivain.wait())
        if any(codes):
            err_l.seek(0)
            err_e.seek(0)
            raise RuntimeError(f'gain des nuances impossible : {entree}\n'
                               f'{(err_l.read() + err_e.read()).decode(errors="replace")[-800:]}')


def calculer_reste(t):
    """instrumental.wav (musique, bruits) = original.wav - voix_source.wav, recalculé à chaque mixage, --remix compris :
    un instrumental.wav existant n'est jamais réutilisé. Soustraction en numpy, lue et écrite en continu : amix de
    ffmpeg 6.1 ignore le signe des poids (weights='1 -1' additionnait, d'où une voix d'origine 6 dB trop forte sous la
    voix convertie dans les doublages faits jusqu'au 04/11/2026). Garde-fous : reste + voix = original, reste pas plus
    fort que l'original, et presque pas corrélé à la voix (0,03 quand c'est juste, 0,84 avec l'ancienne erreur)."""
    import numpy as np
    sortie, tmp = t / 'instrumental.wav', t / '.instrumental.wav'
    with tempfile.TemporaryFile() as err_o, tempfile.TemporaryFile() as err_v, tempfile.TemporaryFile() as err_e:
        lecteurs = [subprocess.Popen(['nice', '-n', '10', 'ffmpeg', '-v', 'error', '-i', str(f), '-ac', '2', '-ar', '44100',
                                      '-f', 'f32le', '-'], stdout=subprocess.PIPE, stderr=err)
                    for f, err in ((t / 'original.wav', err_o), (t / 'voix_source.wav', err_v))]
        ecrivain = subprocess.Popen(['nice', '-n', '10', 'ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', '44100',
                                     '-ac', '2', '-i', '-', '-c:a', 'pcm_f32le', str(tmp)], stdin=subprocess.PIPE, stderr=err_e)
        so = sv = sr = srv = se = 0.0
        n_o = n_v = 0
        try:
            while True:
                bo, bv = (l.stdout.read(44100 * 10 * 8) for l in lecteurs)   # 10 s stéréo
                if not bo and not bv:
                    break
                o = np.frombuffer(bo[:len(bo) // 8 * 8], np.float32).reshape(-1, 2).astype(np.float64)
                v = np.frombuffer(bv[:len(bv) // 8 * 8], np.float32).reshape(-1, 2).astype(np.float64)
                n_o, n_v = n_o + len(o), n_v + len(v)
                if len(v) < len(o):   # voix plus courte (ne devrait pas arriver : le noyau la cale sur l'original)
                    v = np.vstack([v, np.zeros((len(o) - len(v), 2))])
                v = v[:len(o)]
                r = (o - v).astype(np.float32)
                ecrivain.stdin.write(r.tobytes())
                r = r.astype(np.float64)
                so, sv, sr = so + (o * o).sum(), sv + (v * v).sum(), sr + (r * r).sum()
                srv, se = srv + (r * v).sum(), se + ((r + v - o) ** 2).sum()
        finally:
            ecrivain.stdin.close()
            for l in lecteurs:
                l.stdout.close()
            codes = [l.wait() for l in lecteurs] + [ecrivain.wait()]
        if any(codes):
            for e in (err_o, err_v, err_e):
                e.seek(0)
            tmp.unlink(missing_ok=True)
            raise RuntimeError('calcul de la musique seule impossible :\n'
                               + b''.join(e.read() for e in (err_o, err_v, err_e)).decode(errors='replace')[-800:])
    db = lambda e: 10 * math.log10(e / max(n_o, 1) / 2 + 1e-30)
    m = {'niveau_original_db': round(db(so), 2), 'niveau_voix_db': round(db(sv), 2), 'niveau_reste_db': round(db(sr), 2),
         'correlation_reste_voix': round(srv / math.sqrt(sr * sv), 4) if sr > 0 and sv > 0 else 0.0,
         'ecart_reconstruction_db': round(db(se) - db(so), 1) if so > 0 else None, 'echantillons': n_o,
         'echantillons_voix': n_v}
    erreurs = []
    if abs(n_v - n_o) > 4410:
        erreurs.append(f'voix isolée de {n_v} échantillons pour un original de {n_o}')
    if m['niveau_reste_db'] > m['niveau_original_db'] + 1.0:
        erreurs.append(f"musique seule plus forte que l'original ({m['niveau_reste_db']:.1f} dB contre "
                       f"{m['niveau_original_db']:.1f} dB) : la voix a été ajoutée au lieu d'être retirée ?")
    if m['correlation_reste_voix'] > 0.3:
        erreurs.append(f"il reste de la voix dans la musique seule (corrélation {m['correlation_reste_voix']:.2f} avec la "
                       'voix isolée ; 0,03 attendu)')
    if m['ecart_reconstruction_db'] is not None and m['ecart_reconstruction_db'] > -100:
        erreurs.append(f"musique + voix ne redonnent pas l'original (écart {m['ecart_reconstruction_db']:.0f} dB)")
    if erreurs:
        tmp.unlink(missing_ok=True)
        raise RuntimeError('musique seule fausse, mixage arrêté : ' + ' ; '.join(erreurs))
    os.replace(tmp, sortie)
    return m


def voix_v2(t, tv, voix_fichier, hp, info):
    """Voix sèche du mix v2 : chaîne v1 (passe-haut, compression douce), puis dé-esseur au niveau de référence, puis
    nuances de la voix d'origine. Rend le fichier stéréo seche_<voix>.wav (mêmes noms de fichiers qu'en v1)."""
    seche = tv / f'seche_{voix_fichier.stem}.wav'
    comprimee, deessee = tv / f'.comprimee_{voix_fichier.stem}.wav', tv / f'.deessee_{voix_fichier.stem}.wav'
    try:
        ff('-i', voix_fichier, '-af',
           f'highpass=f={hp},acompressor=threshold=-20dB:ratio=2.5:attack=10:release=150,pan=stereo|c0=c0|c1=c0',
           '-c:a', 'pcm_f32le', comprimee)
        bande = f"lowpass=f={NUANCES['bande_hz']}"
        e_conv = energies_trames(comprimee, bande)
        e_src = energies_trames(t / 'voix_source.wav', f'highpass=f={hp},{bande}')
        actif = niveau_actif_db(e_conv)
        mise = 0.0 if actif is None else DEESSER_NIVEAU_DBFS - actif
        ff('-i', comprimee, '-af', f'volume={mise:.3f}dB,{DEESSER},volume={-mise:.3f}dB', '-c:a', 'pcm_f32le', deessee)
        c, info['nuances'] = courbe_nuances(e_src, e_conv)
        info['deesser'] = {'filtre': DEESSER, 'niveau_actif_voix_dbfs': None if actif is None else round(actif, 1),
                           'mise_au_niveau_db': round(mise, 2)}
        appliquer_courbe(deessee, seche, c)
    finally:
        for f in (comprimee, deessee):
            f.unlink(missing_ok=True)
    return seche


def mixer(t, tv, mode, voix_db, reverb_db, voix_fichier, sortie_wav, gains=None, version=2, info=None):
    """Voix sèche (passe-haut, compression douce ; en v2, dé-esseur et nuances de la voix d'origine) au niveau moyen de
    la voix d'origine + voix_db ; en chant, reverb synthétique reverb_db en dessous ; limiteur à 0,95. gains imposés :
    ceux du mix principal (variantes de comparaison). info reçoit les mesures du mix v2."""
    duree = duree_s(t / 'instrumental.wav')
    hp = 90 if mode == 'chant' else 70
    info = {} if info is None else info
    info['version'] = version
    if version >= 2:
        seche = voix_v2(t, tv, voix_fichier, hp, info)
    else:
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
    # limiteur sur la crête vraie (détection suréchantillonnée x4), -1 dBTP environ ; latency=1 : sans elle, alimiter
    # retarde le mix de 5 ms (son en retard sur l'image)
    graphe += f'aresample={4 * 44100},alimiter=limit={LIMITE:.4f}:level=disabled:latency=1,aresample=44100[m]'
    ff(*entrees, '-filter_complex', graphe, '-map', '[m]', '-c:a', 'pcm_s24le', sortie_wav)
    return g, cible


RECUL_FFMPEG_S = 3 / 23   # ffmpeg recule de 3/23 s avant de chercher (formats sans recherche par pts, images B : mkv, webm)


def premiere_image_copiee(video, info, fv, ss):
    """Instant (s, depuis le début du fichier) de la première image que « ffmpeg -ss ss -i vidéo -c copy » copiera."""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{ss:.6f}', '-i', str(video), '-map', f"0:{fv['index']}", '-c', 'copy',
                        '-frames:v', '1', '-copyts', '-f', 'framecrc', '-'], capture_output=True, text=True)
    tb = re.search(r'#tb 0: (\d+)/(\d+)', r.stdout)
    lignes = [l for l in r.stdout.splitlines() if l and not l.startswith('#')]
    if r.returncode or not tb or not lignes:
        return None
    pts = int(lignes[0].split(',')[2])
    return pts * int(tb.group(1)) / int(tb.group(2)) - float(info['format'].get('start_time', 0) or 0)


def ajuster_coupe(video, info, fv, debut):
    """Valeur de -ss qui fait partir la copie de l'image sur l'image clé de l'extrait (debut - 0,001 s). En mp4, -ss debut
    y tombe ; en mkv ou webm, ffmpeg recule de 0,13 s avant de chercher et repartait de l'image clé précédente (1,9 s
    d'image sans son sur un épisode de série) : on cherche alors 0,1315 s plus loin."""
    p = premiere_image_copiee(video, info, fv, debut)
    if p is None or p >= debut - 0.02:
        return debut
    ss = debut + RECUL_FFMPEG_S + 0.001
    p2 = premiere_image_copiee(video, info, fv, ss)
    if p2 is not None and abs(p2 - (debut - 0.001)) < 0.02:
        return ss
    dire(f'attention : la copie de l\'image commencera à {p:.3f} s au lieu de {debut:.3f} s (image sans son au début)')
    return debut


def plan_video(t):
    """Où et comment le mix sera remis sur la vidéo : conteneur de sortie, coupe de l'extrait (-ss/-t) et décalage du
    son dans la vidéo de sortie (decalage_son : instant de la vidéo de sortie où commence le mix ; 0 le plus souvent,
    -0,13 s pour un extrait mkv ou webm, le retard du flux audio d'origine pour une vidéo entière)."""
    src = json.loads((t / 'source.json').read_text())
    video = Path(src['video'])
    info = sonder(video)
    ext = video.suffix.lower()
    if ext not in CONTENEURS:
        ext = '.mkv'
    fv = flux_video(info)
    coupe, decalage_son = [], float(src.get('decalage_son') or 0)
    if 'debut' in src:
        ss = ajuster_coupe(video, info, fv, src['debut'])
        # la vidéo commence à ss, le son du mix à debut : le mix est décalé d'autant (négatif si ss > debut)
        coupe = ['-ss', f'{ss:.6f}', '-t', f"{src['debut'] + src['duree'] - ss:.6f}"]
        decalage_son += src['debut'] - ss
    return {'src': src, 'video': video, 'info': info, 'ext': ext, 'fv': fv, 'coupe': coupe, 'decalage_son': decalage_son}


SOUS_TITRES_NOUVEAUX = {'.mp4': 'mov_text', '.m4v': 'mov_text', '.mov': 'mov_text', '.mkv': 'srt', '.webm': 'webvtt'}


def remettre_sur_video(t, mix, dossier, base, sous_titres=(), plan=None):
    """Le mix remis sur la vidéo : image copiée sans réencodage, sous-titres d'origine gardés (ceux que le conteneur
    accepte tels quels), puis les pistes de sous_titres ajoutées ([{fichier (srt, temps de la vidéo de sortie), langue
    (ISO 639-2), titre}] : mov_text en mp4, srt en mkv, webvtt en webm), sans être affichées d'office."""
    p = plan or plan_video(t)
    src, video, info, ext, fv = p['src'], p['video'], p['info'], p['ext'], p['fv']
    audio, options, acceptes = CONTENEURS[ext]
    sortie = dossier / f'{base}{ext}'
    decalage = ['-itsoffset', f"{p['decalage_son']:.6f}"] if abs(p['decalage_son']) > 1e-6 else []
    cartes = ['-map', f"0:{fv['index']}", '-map', '1:a:0']
    gardes, jetes = [], []
    for s in flux(info, 'subtitle'):
        (gardes if s.get('codec_name') in acceptes else jetes).append(s)
    for s in gardes:
        cartes += ['-map', f"0:{s['index']}"]
    entrees, codecs = [], []
    for j, st in enumerate(sous_titres):
        k = len(gardes) + j   # index de la piste parmi les sous-titres de sortie
        entrees += ['-i', st['fichier']]
        cartes += ['-map', f'{2 + j}:0']
        codecs += [f'-c:s:{k}', SOUS_TITRES_NOUVEAUX.get(ext, 'srt'), f'-metadata:s:s:{k}', f"language={st['langue']}",
                   f'-metadata:s:s:{k}', f"title={st['titre']}", f'-disposition:s:{k}', '0']
        if ext in ('.mp4', '.m4v', '.mov'):
            codecs += [f'-metadata:s:s:{k}', f"handler_name={st['titre']}"]
    if ext == '.mkv':
        cartes += ['-map', '0:t?']   # polices jointes des sous-titres
        if sous_titres:
            options = [*options, '-default_mode', 'infer_no_subs']   # les paroles ne s'affichent pas d'office
    meta = ['-metadata:s:a:0', f"title={base.split(' - ', 1)[-1]}"]
    if src.get('langue'):
        meta += ['-metadata:s:a:0', f"language={src['langue']}"]
    ff(*p['coupe'], '-i', video, *decalage, '-i', mix, *entrees, *cartes, '-c:v', 'copy', '-c:s', 'copy', *codecs,
       '-c:t', 'copy', *audio, *meta, *options, sortie)
    if jetes:
        dire(f"sous-titres non copiés ({', '.join(sorted({s.get('codec_name', '?') for s in jetes}))}) : le conteneur "
             f'{ext} ne les accepte pas tels quels')
    return sortie


# --- Paroles : paroles.json (noyau), calage sur la vidéo, .srt, .lrc --------------------------------------------------

# Langues de Whisper : code ISO 639-2/B (étiquette des pistes mp4 et mkv) et nom français (titre des pistes)
LANGUES = {
    'af': ('afr', 'afrikaans'), 'am': ('amh', 'amharique'), 'ar': ('ara', 'arabe'), 'az': ('aze', 'azéri'),
    'be': ('bel', 'biélorusse'), 'bg': ('bul', 'bulgare'), 'bn': ('ben', 'bengali'), 'br': ('bre', 'breton'),
    'bs': ('bos', 'bosnien'), 'ca': ('cat', 'catalan'), 'cs': ('cze', 'tchèque'), 'cy': ('wel', 'gallois'),
    'da': ('dan', 'danois'), 'de': ('ger', 'allemand'), 'el': ('gre', 'grec'), 'en': ('eng', 'anglais'),
    'es': ('spa', 'espagnol'), 'et': ('est', 'estonien'), 'eu': ('baq', 'basque'), 'fa': ('per', 'persan'),
    'fi': ('fin', 'finnois'), 'fr': ('fre', 'français'), 'gl': ('glg', 'galicien'), 'gu': ('guj', 'gujarati'),
    'ha': ('hau', 'haoussa'), 'haw': ('haw', 'hawaïen'), 'he': ('heb', 'hébreu'), 'hi': ('hin', 'hindi'),
    'hr': ('hrv', 'croate'), 'ht': ('hat', 'créole haïtien'), 'hu': ('hun', 'hongrois'), 'hy': ('arm', 'arménien'),
    'id': ('ind', 'indonésien'), 'is': ('ice', 'islandais'), 'it': ('ita', 'italien'), 'ja': ('jpn', 'japonais'),
    'jw': ('jav', 'javanais'), 'ka': ('geo', 'géorgien'), 'kk': ('kaz', 'kazakh'), 'km': ('khm', 'khmer'),
    'kn': ('kan', 'kannada'), 'ko': ('kor', 'coréen'), 'la': ('lat', 'latin'), 'lb': ('ltz', 'luxembourgeois'),
    'ln': ('lin', 'lingala'), 'lo': ('lao', 'lao'), 'lt': ('lit', 'lituanien'), 'lv': ('lav', 'letton'),
    'mg': ('mlg', 'malgache'), 'mi': ('mao', 'maori'), 'mk': ('mac', 'macédonien'), 'ml': ('mal', 'malayalam'),
    'mr': ('mar', 'marathi'), 'ms': ('may', 'malais'), 'mt': ('mlt', 'maltais'), 'my': ('bur', 'birman'),
    'ne': ('nep', 'népalais'), 'nl': ('dut', 'néerlandais'), 'nn': ('nno', 'norvégien nynorsk'),
    'no': ('nor', 'norvégien'), 'oc': ('oci', 'occitan'), 'pa': ('pan', 'pendjabi'), 'pl': ('pol', 'polonais'),
    'ps': ('pus', 'pachto'), 'pt': ('por', 'portugais'), 'ro': ('rum', 'roumain'), 'ru': ('rus', 'russe'),
    'sd': ('snd', 'sindhi'), 'si': ('sin', 'cingalais'), 'sk': ('slo', 'slovaque'), 'sl': ('slv', 'slovène'),
    'sn': ('sna', 'shona'), 'so': ('som', 'somali'), 'sq': ('alb', 'albanais'), 'sr': ('srp', 'serbe'),
    'su': ('sun', 'soundanais'), 'sv': ('swe', 'suédois'), 'sw': ('swa', 'swahili'), 'ta': ('tam', 'tamoul'),
    'te': ('tel', 'télougou'), 'tg': ('tgk', 'tadjik'), 'th': ('tha', 'thaï'), 'tk': ('tuk', 'turkmène'),
    'tl': ('tgl', 'tagalog'), 'tr': ('tur', 'turc'), 'tt': ('tat', 'tatar'), 'uk': ('ukr', 'ukrainien'),
    'ur': ('urd', 'ourdou'), 'uz': ('uzb', 'ouzbek'), 'vi': ('vie', 'vietnamien'), 'yi': ('yid', 'yiddish'),
    'yo': ('yor', 'yoruba'), 'yue': ('chi', 'cantonais'), 'zh': ('chi', 'chinois'),
}


def langue_piste(code):
    """(étiquette ISO 639-2, nom français) d'une langue de Whisper ; ('und', code) si elle est inconnue."""
    return LANGUES.get(code or '', ('und', code or 'langue inconnue'))


def _segments_propres(segments, cle='texte', mots=False):
    """Segments valides (temps finis, texte non vide), triés ; fin avant le début : remise au début."""
    out = []
    for s in segments if isinstance(segments, list) else []:
        if not isinstance(s, dict):
            continue
        try:
            a, b = float(s['debut']), float(s['fin'])
        except (KeyError, TypeError, ValueError):
            continue
        texte = re.sub(r'\s+', ' ', str(s.get(cle) or '')).strip()
        if not (math.isfinite(a) and math.isfinite(b)) or not texte:
            continue
        item = {**s, 'debut': a, 'fin': max(a, b), cle: texte}
        if mots:
            item['mots'] = _segments_propres(s.get('mots'), 'mot')
        out.append(item)
    return sorted(out, key=lambda s: (s['debut'], s['fin']))


def lire_paroles(chemin):
    """paroles.json (format 1) lu et vérifié ; les cas abîmés (absent, JSON illisible, format inattendu, segments sans
    temps ou sans texte, temps non finis, traductions mal formées) ne font jamais échouer la mise en vidéo. Rend
    (paroles nettoyées ou None, raison)."""
    try:
        d = json.loads(Path(chemin).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None, 'pas de paroles.json'
    except (OSError, ValueError) as e:
        return None, f'paroles.json illisible ({e})'
    if not isinstance(d, dict) or not isinstance(d.get('segments'), list):
        return None, 'paroles.json : format inattendu (segments manquants)'
    d['segments'] = _segments_propres(d['segments'], mots=True)
    trad = d.get('traductions') if isinstance(d.get('traductions'), dict) else {}
    d['traductions'] = {lg: {**t, 'segments': _segments_propres(t.get('segments'))} for lg, t in trad.items()
                        if isinstance(t, dict) and isinstance(t.get('segments'), list)}
    try:
        d['decalage_s'] = float(d.get('decalage_s') or 0.0)
    except (TypeError, ValueError):
        d['decalage_s'] = 0.0
    if not math.isfinite(d['decalage_s']):
        d['decalage_s'] = 0.0
    if not isinstance(d.get('langue'), str) or not d['langue']:
        d['langue'] = None
    return d, None


def caler_paroles(d, decalage):
    """Temps des paroles en secondes de la vidéo de sortie : le noyau les donne en temps du son doublé (decalage_s 0) ;
    le mix commence à decalage s dans la vidéo. Idempotent (--remix) : decalage_s garde le décalage déjà appliqué.
    Ce qui tomberait avant le début de la vidéo est rogné ou retiré."""
    delta = float(decalage) - float(d.get('decalage_s') or 0.0)

    def decaler(segs, cle='texte', mots=False):
        out = []
        for s in segs:
            a, b = s['debut'] + delta, s['fin'] + delta
            if b <= 0:
                continue
            item = {**s, 'debut': round(max(a, 0.0), 6), 'fin': round(b, 6)}
            if mots:
                item['mots'] = decaler(s.get('mots') or [], 'mot')
            out.append(item)
        return out
    d['segments'] = decaler(d['segments'], mots=True)
    for t in d['traductions'].values():
        t['segments'] = decaler(t['segments'])
    d['decalage_s'] = round(float(decalage), 6)
    return d


def temps_srt(t):
    ms = int(round(max(t, 0.0) * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def couper_ligne(texte, largeur=42):
    """Texte trop long pour une ligne de sous-titres : deux lignes, coupées à l'espace le plus proche du milieu."""
    if len(texte) <= largeur or ' ' not in texte:
        return texte
    milieu = len(texte) // 2
    i = min((i for i, c in enumerate(texte) if c == ' '), key=lambda i: abs(i - milieu))
    return texte[:i] + '\n' + texte[i + 1:]


def cues(segments, duree_max=None, lecture_min_s=1.0):
    """Segments -> [(début, fin, texte)] affichables : chaque ligne reste au moins lecture_min_s (sans empiéter sur la
    suivante), jamais deux à la fois, rien après duree_max (fin de la vidéo)."""
    segs = [s for s in segments if duree_max is None or s['debut'] < duree_max]
    out = []
    for i, s in enumerate(segs):
        suivant = segs[i + 1]['debut'] if i + 1 < len(segs) else None
        fin = max(s['fin'], s['debut'] + lecture_min_s)
        if suivant is not None:
            fin = min(fin, suivant - 0.001)
        if duree_max is not None:
            fin = min(fin, duree_max)
        if fin - s['debut'] < 0.05:
            fin = s['debut'] + 0.05
        out.append((s['debut'], fin, s['texte']))
    return out


def ecrire_srt(segments, chemin, duree_max=None):
    """Fichier .srt (UTF-8) ; rend le nombre de répliques."""
    c = cues(segments, duree_max)
    texte = ''.join(f'{i}\n{temps_srt(a)} --> {temps_srt(b)}\n{couper_ligne(t)}\n\n' for i, (a, b, t) in enumerate(c, 1))
    tmp = Path(str(chemin) + '.tmp')
    tmp.write_text(texte, encoding='utf-8')
    os.replace(tmp, chemin)
    return len(c)


def temps_lrc(t):
    cs = int(round(max(t, 0.0) * 100))
    return f'[{cs // 6000:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}]'


def ecrire_lrc(segments, chemin, decalage=0.0, titre=None, langue=None):
    """Fichier .lrc (UTF-8) synchronisé ligne à ligne pour les lecteurs de musique, en temps du mp3 (temps de la vidéo
    moins decalage) ; une ligne vide efface le texte aux silences de plus de 2 s. Rend le nombre de lignes."""
    entetes = [f'[ti:{titre}]'] if titre else []
    entetes += [f'[la:{langue}]'] if langue else []
    entetes += ['[re:Voix-là (Whisper large-v3)]']
    lignes, n = [], 0
    segs = [s for s in segments if s['fin'] - decalage > 0]
    for i, s in enumerate(segs):
        lignes.append(f"{temps_lrc(s['debut'] - decalage)}{s['texte']}")
        n += 1
        suivant = segs[i + 1]['debut'] if i + 1 < len(segs) else None
        if suivant is None or suivant - s['fin'] > 2.0:
            lignes.append(temps_lrc(s['fin'] - decalage))
    tmp = Path(str(chemin) + '.tmp')
    tmp.write_text('\n'.join(entetes + lignes) + '\n', encoding='utf-8')
    os.replace(tmp, chemin)
    return n


def preparer_paroles(tv, dossier, base, decalage, duree_video=None):
    """paroles.json calé sur la vidéo de sortie (réécrit), puis <base>.<langue>.srt, <base>.fr.srt (traduction ; ou
    <base>.traduction.fr.srt si les paroles sont déjà en français) et <base>.lrc à côté des sorties. Rend (pistes de
    sous-titres pour remettre_sur_video, fichiers écrits, résumé pour le rapport)."""
    chemin = tv / 'paroles.json'
    d, raison = lire_paroles(chemin)
    if d is None:
        return [], [], {'raison': raison}
    d = caler_paroles(d, decalage)
    tmp = chemin.with_name(chemin.name + '.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, chemin)
    info = {'langue': d['langue'], 'lignes': len(d['segments']), 'decalage_s': d['decalage_s'],
            'traduction': bool((d['traductions'].get('fr') or {}).get('segments'))}
    if not d['segments']:
        return [], [], {**info, 'raison': 'aucune parole reconnue'}
    lg = d['langue'] or 'und'
    code, nom = langue_piste(d['langue'])
    pistes, fichiers = [], []
    srt = dossier / f'{base}.{lg}.srt'
    ecrire_srt(d['segments'], srt, duree_video)
    pistes.append({'fichier': srt, 'langue': code, 'titre': f'Paroles ({nom})'})
    fichiers.append(srt)
    if info['traduction']:
        srt_fr = dossier / (f'{base}.fr.srt' if lg != 'fr' else f'{base}.traduction.fr.srt')
        ecrire_srt(d['traductions']['fr']['segments'], srt_fr, duree_video)
        pistes.append({'fichier': srt_fr, 'langue': 'fre', 'titre': 'Paroles (français, traduction)'})
        fichiers.append(srt_fr)
    lrc = dossier / f'{base}.lrc'
    ecrire_lrc(d['segments'], lrc, decalage, base, d['langue'])
    fichiers.append(lrc)
    info.update({'fichiers': [f.name for f in fichiers], 'nom_langue': nom})
    return pistes, fichiers, info


# --- Une vidéo -------------------------------------------------------------------------------------------------------

def fmt_t(t):
    return f'{int(t // 60)}m{t % 60:04.1f}'.replace('.', ',')


TEMPS = r'(?:\d+:)?\d+(?:[.,]\d+)?'
EXTRAIT = re.compile(rf'(?P<debut>{TEMPS})?(?:\+(?P<duree>{TEMPS}))?')


def secondes(texte):
    """« 70 », « 70,5 » ou « 1:10 » -> secondes."""
    m, _, sec = texte.replace(',', '.').rpartition(':')
    return float(sec) + 60 * (int(m) if m else 0)


def lire_video(arg, a):
    """« vidéo » ou « vidéo@DEBUT+DUREE » : extrait propre à cette vidéo (secondes ou m:ss ; « @60 » de 60 s à la fin,
    « @+40 » les 40 premières secondes), sinon --debut / --duree. Un fichier qui existe tel quel n'est jamais découpé.
    Rend (chemin, début, durée)."""
    arg = str(arg)
    chemin, _, spec = arg.rpartition('@')
    if '@' not in arg or Path(arg).expanduser().exists() or not spec:
        return arg, a.debut, a.duree
    m = EXTRAIT.fullmatch(spec)
    if not m or not (m['debut'] or m['duree']):
        return arg, a.debut, a.duree   # pas un extrait : « fichier introuvable » sera dit plus loin
    return chemin, secondes(m['debut']) if m['debut'] else None, secondes(m['duree']) if m['duree'] else None


def emplacements(video, debut, duree, a, voix):
    video = Path(video).expanduser().resolve()
    if not video.is_file():
        raise RuntimeError(f'fichier introuvable : {video}')
    nom = video.stem
    if debut is not None or duree is not None:
        nom += f" (extrait {fmt_t(debut or 0)}{'+' + fmt_t(duree) if duree else ''})"
    dossier = Path(a.sortie).expanduser().resolve() / nom
    return {'video': video, 'nom': nom, 'dossier': dossier, 'travail': dossier / 'travail',
            'tv': dossier / 'travail' / voix['id'], 'base': f"{video.stem} - voix {voix['affichage']}"}


def finir(tr, a, voix, temps_local=None):
    """Mixage, MP3, remise sur la vidéo (et extraits de comparaison des pas de diffusion) depuis travail/."""
    t, tv, dossier, base = tr['travail'], tr['tv'], tr['dossier'], tr['base']
    for f in (t / 'original.wav', t / 'voix_source.wav', t / 'source.json', tv / 'voix_convertie.flac', tv / 'rapport.json'):
        if not f.exists():
            raise RuntimeError(f'travail incomplet ({f} manque) : relancer sans --remix')
    rapport = json.loads((tv / 'rapport.json').read_text())
    mode = rapport['mode']
    voix_db = a.voix_db if a.voix_db is not None else (4.0 if mode == 'chant' else 0.0)
    version = 1 if a.mix_v1 else 2
    titre = tr['nom']
    debut = debut0 = time.time()
    info = {}
    with Etape('mix', titre, f'mixage v{version} ({mode})'):
        reste = calculer_reste(t)   # toujours recalculé et vérifié (jamais l'instrumental.wav d'une version précédente)
        gains, cible = mixer(t, tv, mode, voix_db, a.reverb_db, tv / 'voix_convertie.flac', tv / 'mix.wav', version=version, info=info)
        mp3 = dossier / f'{base}.mp3'
        ff('-i', tv / 'mix.wav', '-c:a', 'libmp3lame', '-b:a', '320k', mp3)
        nu = info.get('nuances', {})
        if version < 2:
            detail = ' (ancien mixage : ni nuances ni dé-esseur)'
        elif nu.get('appliquee'):
            detail = (f" ; nuances de la voix d'origine {nu['correction_p5_p95_db'][0]:+.1f} à "
                      f"{nu['correction_p5_p95_db'][1]:+.1f} dB (p5-p95), « s » adoucis")
        else:
            detail = ' ; « s » adoucis, nuances non suivies (trop peu de voix commune)'
        dire(f"mixage {mode} v{version} : voix source {cible:.1f} dB, voix {voix_db:+g} dB"
             + (f', reverb {a.reverb_db:+g} dB sous la voix' if mode == 'chant' else ', sans reverb, fond sonore gardé') + detail)
        comp = rapport.get('comparaison_pas')
        extraits = []
        if comp and (tv / comp['fichier']).exists():
            mixer(t, tv, mode, voix_db, a.reverb_db, tv / comp['fichier'], tv / 'mix_comparaison.wav', gains, version=version)
            suffixe = '' if voix['id'] == VOIX_DEFAUT else f" (voix {voix['affichage']})"
            for pas, mix in zip(comp['pas'], (tv / 'mix.wav', tv / 'mix_comparaison.wav')):
                sortie = dossier / f'comparaison_{pas}pas{suffixe}.mp3'
                ff('-ss', f"{comp['debut_s']:.3f}", '-t', f"{comp['fin_s'] - comp['debut_s']:.3f}", '-i', mix,
                   '-af', 'afade=t=in:d=0.3', '-c:a', 'libmp3lame', '-b:a', '320k', sortie)
                extraits.append(sortie)
    mix_s = time.time() - debut
    debut = time.time()
    paroles_on = getattr(a, 'paroles', True)
    pistes_st, fichiers_paroles, info_paroles = [], [], {'demande': paroles_on}
    with Etape('video', titre, 'son remis sur la vidéo (image copiée)' + (', paroles en sous-titres' if paroles_on else '')):
        plan = plan_video(t)
        if paroles_on:
            try:
                pistes_st, fichiers_paroles, i_p = preparer_paroles(tv, dossier, base, plan['decalage_son'],
                                                                    duree_s(tv / 'mix.wav') + plan['decalage_son'])
                info_paroles.update(i_p)
            except Exception as e:  # noqa: BLE001 — des paroles abîmées n'empêchent jamais la vidéo
                info_paroles['raison'] = f'paroles inutilisables : {e}'
        try:
            sortie = remettre_sur_video(t, tv / 'mix.wav', dossier, base, pistes_st, plan)
        except Exception as e:  # noqa: BLE001
            if not pistes_st:
                raise
            dire(f'ATTENTION : pistes de paroles refusées par ffmpeg ({str(e).splitlines()[-1][:200]}) : vidéo sans elles')
            info_paroles['raison'] = 'pistes de sous-titres refusées par ffmpeg (fichiers .srt et .lrc gardés)'
            pistes_st = []
            sortie = remettre_sur_video(t, tv / 'mix.wav', dossier, base, (), plan)
        info_paroles['pistes_video'] = [st['titre'] for st in pistes_st]
    video_s = time.time() - debut
    rapport['mix'] = {**info, 'musique_seule': reste, 'limite_dbtp': round(20 * math.log10(LIMITE), 2), 'voix_db': voix_db, 'reverb_db': a.reverb_db if mode == 'chant' else None,
                      'gains_db': {k: round(v, 2) for k, v in gains.items()}, 'voix_source_db': cible}
    rapport['paroles_sorties'] = info_paroles
    if temps_local is not None:
        temps_local.update({'mix_local_s': round(mix_s, 1), 'remise_video_s': round(video_s, 1)})
        rapport['temps_local'] = temps_local
    (tv / 'rapport.json').write_text(json.dumps(rapport, ensure_ascii=False, indent=1))
    lrc = next((f for f in fichiers_paroles if f.suffix == '.lrc'), None)
    EV('sortie', titre, mp4=str(sortie), mp3=str(mp3), rapport=str(tv / 'rapport.json'),
       comparaisons=[str(e) for e in extraits] or None,
       courbes=str(tv / 'courbes.json') if (tv / 'courbes.json').exists() else None,
       paroles=str(tv / 'paroles.json') if paroles_on and (tv / 'paroles.json').exists() else None,
       srt=[str(f) for f in fichiers_paroles if f.suffix == '.srt'] or None, lrc=str(lrc) if lrc else None)
    m = rapport.get('mesures_musique', {})
    principale = rapport.get('variantes', {}).get('voix_convertie.flac', {})
    dire(f"mode {mode} ({'imposé' if rapport.get('mode_force') else 'détecté'} : musique sous la voix "
         f"{m.get('reste_sous_voix_db')} dB, tonalité {m.get('tonalite_reste')}, notes tenues {m.get('tenue')}) ; "
         f"modèle {rapport.get('modele')}, {principale.get('pas_diffusion')} pas")
    net = rapport.get('nettoyage')
    if net is not None:   # rapports d'avant le 04/11/2026 : ni moteur ni nettoyage
        nom_moteur = {'yingmusic': 'YingMusic-SVC', 'seedvc': 'Seed-VC'}.get(rapport.get('moteur'), rapport.get('moteur'))
        if net.get('applique'):
            niv = net.get('niveaux_db') or {}
            voix_txt = (f"voix nettoyée (voix principale {niv.get('lead_sec')} dB, chœurs {niv.get('choeurs_sec')} dB, "
                        f"reverb retirée {niv.get('reverb_jetee')} dB)")
        else:
            voix_txt = f"voix complète ({net.get('raison')})"
        dire(f'moteur {nom_moteur} ; {voix_txt}')
        if principale.get('repli'):
            dire(f"ATTENTION : YingMusic-SVC indisponible, repli sur Seed-VC 44 kHz ({principale['repli']})")
    sec = rapport.get('secours') or {}
    if sec.get('decision') == 'secours' and sec.get('zones'):
        dire(f"passages perdus au nettoyage repris de la voix complète : {len(sec['zones'])}, {sec.get('secondes', 0):.1f} s ("
             + ', '.join(f"{fmt_t(z['debut_s'])}–{fmt_t(z['fin_s'])}, écart {z['ecart_db']:.0f} dB" for z in sec['zones'][:6])
             + (', …' if len(sec['zones']) > 6 else '') + ')')
    if paroles_on:
        if fichiers_paroles:
            dire(f"paroles ({info_paroles.get('nom_langue')}, {info_paroles.get('lignes')} lignes"
                 f"{', traduction française' if info_paroles.get('traduction') else ''}) : "
                 + ', '.join(f.name for f in fichiers_paroles))
        else:
            kp = rapport.get('paroles') or {}
            dire(f"paroles : aucune ({kp.get('erreur') or info_paroles.get('raison') or 'rien de reconnu'})")
    print('  hauteur par passage (dt = demi-tons) :', flush=True)
    for l in rapport.get('frise', []):
        print('   ', l, flush=True)
    if extraits:
        dire(f"comparaison {comp['pas'][0]} / {comp['pas'][1]} pas sur {fmt_t(comp['debut_s'])}–{fmt_t(comp['fin_s'])} : "
             + ', '.join(e.name for e in extraits))
    dire(f'fini : {sortie}\n         {mp3}')
    EV('etape', titre, etape='fini', etat='fin', message=str(sortie), duree_s=round(time.time() - tr.get('debut_epoch', debut0), 1))
    return sortie


ETAT = Path.home() / '.local' / 'state' / 'voix-la'   # verrou Kaggle commun et session en cours (pour --reprendre)
SESSION = ETAT / 'session-en-cours.json'
DEFINITIF = re.compile(r'sans résultat|ACCÈS REFUSÉ|FICHIER ABSENT|noyau refusé')   # échecs que --reprendre ne rattrape pas


class Verrou:
    """Une seule session doubler à la fois sur le jeu de données et le noyau du compte : verrou commun (ETAT), plus
    l'ancien verrou du dossier de sorties (les doubler lancés avant le 04/11/2026 ne prennent que celui-là). Toujours
    dans le même ordre, donc sans interblocage. Événement « attente » si un autre doublage tient la place."""

    def __init__(self, kdir, videos=()):
        self.chemins, self.videos, self.fichiers = [ETAT / 'verrou-kaggle', kdir / 'verrou'], videos, []

    def __enter__(self):
        etape = None
        try:
            for c in self.chemins:
                c.parent.mkdir(parents=True, exist_ok=True)
                f = open(c, 'w')
                self.fichiers.append(f)
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if etape is None:
                        dire('Kaggle : un autre doublage utilise la session, attente de son tour')
                        etape = Etape('attente', self.videos, 'un autre doublage utilise Kaggle : attente de son tour').__enter__()
                    fcntl.flock(f, fcntl.LOCK_EX)
        except BaseException as e:
            if etape:
                etape.finir(e)
            self.__exit__(None, None, None)
            raise
        if etape:
            etape.finir()
        return self

    def __exit__(self, *exc):
        for f in reversed(self.fichiers):
            f.close()
        self.fichiers = []
        return False


CHAMPS_SESSION = ('id', 'nom', 'video', 'dossier', 'travail', 'tv', 'base', 'entree', 'gain_entree', 'taille_envoi',
                  'duree_audio_s', 'extraction_s', 'debut_epoch', 'debut_demande', 'duree_demande')


def ecrire_session(contenu):
    ETAT.mkdir(parents=True, exist_ok=True)
    tmp = SESSION.with_name(SESSION.name + '.tmp')
    tmp.write_text(json.dumps(contenu, ensure_ascii=False, indent=1, default=str))
    os.replace(tmp, SESSION)


def clore_session(etat):
    """La session en cours devient la dernière session (gardée pour mémoire) ; plus rien à reprendre."""
    if SESSION.exists():
        try:
            contenu = json.loads(SESSION.read_text())
            contenu.update({'etat': etat, 'close_le': datetime.datetime.now().astimezone().isoformat(timespec='seconds')})
            (ETAT / 'session-precedente.json').write_text(json.dumps(contenu, ensure_ascii=False, indent=1, default=str))
        except (OSError, ValueError):
            pass
        SESSION.unlink(missing_ok=True)


def preparer_travaux(videos, a, voix):
    """Extraction du son de chaque vidéo (« vidéo » ou « vidéo@DEBUT+DUREE ») ; rend (travaux, échecs)."""
    travaux, echecs = [], []
    for i, v in enumerate(videos):
        titre = Path(str(v)).stem
        try:
            chemin, debut_v, duree_v = lire_video(v, a)
            tr = emplacements(chemin, debut_v, duree_v, a, voix)
            titre = tr['nom']
            tr.update({'debut_epoch': time.time(), 'debut_demande': debut_v, 'duree_demande': duree_v})
            dire(f"=== {tr['video'].name} → {tr['dossier']}")
            with Etape('extraction', titre, f"son de {tr['video'].name}"):
                debut = time.time()
                info = sonder(tr['video'])
                tr['travail'].mkdir(parents=True, exist_ok=True)
                tr['tv'].mkdir(parents=True, exist_ok=True)
                src = preparer_source(tr['video'], info, debut_v, duree_v, tr['travail'])
                if 'debut' in src:
                    dire(f"extrait : {src['debut']:.3f} s (image clé avant {src['debut_demande']:g} s), {src['duree']:.3f} s")
                tr['duree_audio_s'] = duree_s(tr['travail'] / 'original.wav')
                tr['extraction_s'] = round(time.time() - debut, 1)
            ident = hashlib.sha1(f"{tr['video']}|{src.get('debut')}|{src.get('duree')}|{time.time()}|{i}".encode()).hexdigest()[:10]
            tr.update({'id': f'v{i + 1}', 'entree': f'entree-{ident}.flac'})
            travaux.append(tr)
        except Exception as e:  # noqa: BLE001
            dire(f'ÉCHEC {v} : {e}')
            EV('erreur', titre, message=str(e))
            echecs.append(str(v))
    return travaux, echecs


def ranger_retour(travaux, retour, kdir):
    """Sorties du noyau rangées dans travail/ et travail/<voix>/ (voix_convertie.flac, rapport.json, courbes.json…) ;
    la voix isolée revenue en FLAC (noyau récent) est décodée au niveau d'origine en voix_source.wav."""
    for f in retour.glob('*.log'):
        shutil.copy2(f, kdir / f.name)
    for tr in travaux:
        src = retour / tr['id']
        if not src.is_dir():
            continue
        tr['tv'].mkdir(parents=True, exist_ok=True)
        try:
            flac = src / 'voix_source.flac'
            if flac.exists():
                d0 = time.time()
                decoder_flac(flac, tr['travail'] / 'voix_source.wav')
                flac.unlink()
                Path(str(flac) + '.json').unlink(missing_ok=True)
                tr['decodage_retour_s'] = round(time.time() - d0, 1)
            elif (src / 'voix_source.wav').exists():
                shutil.move(str(src / 'voix_source.wav'), tr['travail'] / 'voix_source.wav')
        except Exception as e:  # noqa: BLE001 — finir() signalera la voix isolée manquante
            dire(f"ÉCHEC du décodage de la voix isolée ({tr['video'].name}) : {e}")
        for f in src.iterdir():
            shutil.move(str(f), tr['tv'] / f.name)
        for f in kdir.glob('*.log'):
            shutil.copy2(f, tr['tv'] / f.name)
    shutil.rmtree(retour, ignore_errors=True)


def finir_travaux(travaux, a, voix, session, temps, envoi_s, estimation):
    """Après la session Kaggle : mixage et remise sur la vidéo de chaque vidéo (rapport.json complété)."""
    echecs = []
    st = session.get('temps', {})
    attente = round(session['debut_epoch'] - temps['pousse_epoch'], 1) if session.get('debut_epoch') else None
    dire(f"GPU Kaggle : {temps['noyau_depuis_envoi_s'] / 60:.1f} min depuis l'envoi (démarrage {attente} s, installation "
         f"{st.get('installation_s', 0):.0f} s, séparation {st.get('separation_totale_s', 0):.0f} s, modèles "
         f"{st.get('chargement_modeles_s', 0):.0f} s, noyau {st.get('total_s', 0) / 60:.1f} min ; estimé "
         f"{(estimation or 0) / 60:.1f} min) ; rapatriement {temps['rapatriement_s']:.0f} s")
    for tr in travaux:
        try:
            rapport = json.loads((tr['tv'] / 'rapport.json').read_text())
            if rapport.get('erreur'):
                raise RuntimeError('Kaggle : ' + rapport['erreur'])
            # la musique seule (original - voix isolée) est calculée et vérifiée par finir(), comme pour --remix
            temps_local = {'extraction_s': tr.get('extraction_s'), 'envoi_jeu_s': envoi_s,
                           'envoi_flac_mo': round(tr['taille_envoi'] / 1e6, 1) if tr.get('taille_envoi') else None,
                           'gain_entree_db': round(20 * math.log10(tr['gain_entree']), 3) if tr.get('gain_entree') else None,
                           'attente_demarrage_noyau_s': attente, 'version_noyau': temps.get('version_noyau'),
                           'noyau_depuis_envoi_s': temps['noyau_depuis_envoi_s'], 'estimation_noyau_s': estimation,
                           'rapatriement_s': temps['rapatriement_s'], 'decodage_retour_s': tr.get('decodage_retour_s'),
                           'session_kaggle': st, 'videos_dans_la_session': len(travaux)}
            if temps.get('reprise'):
                temps_local['reprise'] = temps['reprise']
            dire(f"=== {tr['video'].name}")
            finir(tr, a, voix, temps_local)
        except Exception as e:  # noqa: BLE001
            dire(f"ÉCHEC {tr['video']} : {e}")
            EV('erreur', tr['nom'], message=str(e))
            echecs.append(str(tr['video']))
    return echecs


def fichier_ancres():
    """Le premier fichier d'ancres de genre présent parmi ANCRES (~/.config/voix-la, puis le dépôt), ou None."""
    return next((f for f in ANCRES if f.is_file()), None)


def lire_ancres():
    """Ancres de genre {'homme': […], 'femme': […]} du premier fichier de ANCRES, telles quelles ; None si aucun
    n'existe ou s'il est illisible : en chant, l'octave se décide alors sans empreinte (hauteur, notes graves, sinon
    frontière d'octave de la hauteur seule) et sans recherche de duo."""
    sans = "en chant, l'octave sera décidée sans empreinte (hauteur, notes graves), sans recherche de duo"
    fichier = fichier_ancres()
    if fichier is None:
        dire(f"ancres de genre absentes ({' ou '.join(map(abrege, ANCRES))}, facultatif) : {sans}")
        return None
    try:
        ancres = json.loads(fichier.read_text())
        h, f = ancres['homme'], ancres['femme']
        if not (isinstance(h, list) and isinstance(f, list) and len(h) == len(f) == 192
                and all(isinstance(v, (int, float)) for v in h + f)):
            raise ValueError('« homme » et « femme » : 192 nombres chacun attendus')
    except (OSError, ValueError, KeyError, TypeError) as e:
        dire(f'ATTENTION : ancres de genre illisibles ({fichier} : {e}) : {sans}')
        return None
    return {'homme': h, 'femme': f}


def traiter_lot(videos, a, voix):
    """Toutes les vidéos dans une seule session Kaggle, puis le mixage de chacune sur le PC."""
    if SESSION.exists():   # vérifié de nouveau sous le verrou ; ici, pour ne pas extraire le son pour rien
        raise SystemExit(f'une session Kaggle précédente n\'est pas finie ({SESSION}) : « doubler --reprendre » la termine '
                         '(sans repasser sur le GPU) ; effacer ce fichier pour l\'abandonner')
    travaux, echecs = preparer_travaux(videos, a, voix)
    if not travaux:
        return echecs
    titres = [tr['nom'] for tr in travaux]
    kdir = Path(a.sortie).expanduser().resolve() / '.kaggle'
    kdir.mkdir(parents=True, exist_ok=True)
    ancres = lire_ancres()

    def abandon(message):
        dire('ÉCHEC ' + message)
        for t in titres:
            EV('erreur', t, message=message)
        return echecs + [str(tr['video']) for tr in travaux]
    with Verrou(kdir, titres):
        if SESSION.exists():
            # un nouveau noyau effacerait la sortie de la session précédente, peut-être finie sur Kaggle sans le PC
            return abandon(f'une session Kaggle précédente n\'est pas finie ({SESSION}) : « doubler --reprendre » la '
                           'termine (sans repasser sur le GPU) ; effacer ce fichier pour l\'abandonner')
        debut = time.time()
        try:
            with Etape('envoi', titres, f'son de {len(travaux)} vidéo(s) en FLAC vers le jeu privé {JEU}'):
                envoyer_jeu(travaux, voix, kdir)
        except Exception as e:  # noqa: BLE001
            return abandon(f'de l\'envoi sur Kaggle : {e}')
        envoi_s = round(time.time() - debut, 1)
        # gain_entree : gain appliqué avant le FLAC (crête à -1 dBFS), retiré par le noyau à la lecture ;
        # retour_flac : la voix isolée revient en FLAC 24 bits (voix_source.flac et son gain dans voix_source.flac.json)
        # moteur_chant, nettoyage : chaîne du chant (YingMusic-SVC sur la voix nettoyée par défaut)
        # ancres : None sans fichier d'ancres (ANCRES ; le noyau décide alors l'octave sans empreinte)
        # paroles : transcription (Whisper large-v3) et traduction française dans le noyau (paroles.json)
        reglages = {**NOYAU_REGLAGES, 'pas': a.pas, 'ancres': ancres, 'paroles': bool(a.paroles),
                    'moteur_chant': a.moteur, 'nettoyage': not a.sans_nettoyage,
                    'voix': {'nom': voix['id'], 'fichier': f"voix-{voix['id']}.wav", 'f0_parole_hz': voix['f0'],
                             'facteur_chant': FACTEUR_CHANT},
                    'travaux': [{'id': tr['id'], 'entree': tr['entree'], 'mode': a.mode,
                                 'octave': a.octave if a.octave == 'auto' else int(a.octave),
                                 'comparer_modeles': bool(a.comparer_modeles), 'comparer_pas': a.comparer_pas,
                                 'gain_entree': tr['gain_entree'], 'gain_entree_db': round(20 * math.log10(tr['gain_entree']), 3),
                                 'retour_flac': True} for tr in travaux]}
        if a.modele_parole:
            reglages['modele_parole'] = a.modele_parole
        retour = kdir / 'retour'
        sources = [OUTILS] if (kdir / 'outils.json').exists() else []
        estimation = estimer_noyau_s([tr['duree_audio_s'] for tr in travaux], a.pas, a.sortie, not a.sans_nettoyage, a.mode,
                                     a.moteur, a.paroles)
        # limite du suivi : au moins le double de l'estimation (le nettoyage allonge beaucoup une longue chanson)
        attente_maxi = max((30 + 20 * len(travaux)) * 60, 2 * estimation + 30 * 60)
        dire(f"chaîne : chant → {'voix nettoyée' if not a.sans_nettoyage else 'voix complète (sans nettoyage)'}, "
             f"{'YingMusic-SVC' if a.moteur == 'yingmusic' else 'Seed-VC 44 kHz'} ; parole → Seed-VC 22 kHz ; "
             f"paroles → {'Whisper large-v3 et traduction française' if a.paroles else 'non'} ; "
             f"session estimée à {estimation / 60:.0f} min")

        def noter(pousse, version):
            ecrire_session({'noyau': NOYAU, 'jeu': JEU, 'version_noyau': version, 'pousse_epoch': pousse,
                            'poussee_le': datetime.datetime.fromtimestamp(pousse).astimezone().isoformat(timespec='seconds'),
                            'voix': voix['id'], 'sortie': str(Path(a.sortie).expanduser().resolve()), 'envoi_s': envoi_s,
                            'estimation_s': estimation, 'attente_maxi_s': attente_maxi, 'pas': a.pas,
                            'moteur': a.moteur, 'nettoyage': not a.sans_nettoyage, 'paroles': bool(a.paroles),
                            'travaux': [{k: tr.get(k) for k in CHAMPS_SESSION} for tr in travaux]})
        etapes = {}
        try:
            temps = lancer_noyau(reglages, kdir, retour, attente_maxi, sources=sources, videos=titres,
                                 estimation_s=estimation, etapes=etapes, apres_poussee=noter)
        except Exception as e:  # noqa: BLE001
            if SESSION.exists() and not DEFINITIF.search(str(e)):
                # noyau poussé : il a pu continuer (ou finir) sur Kaggle ; --reprendre le retrouvera
                return abandon(f'de la session Kaggle : {e}' + ('' if 'reprendre' in str(e) else
                               ' ; le noyau continue peut-être sur Kaggle : « doubler --reprendre » le finira'))
            clore_session('échec')
            return abandon(f'de la session Kaggle : {e}')
        session = json.loads((retour / 'session.json').read_text())
        ranger_retour(travaux, retour, kdir)
        clore_session('rapatriée')
        if etapes.get('rapatriement'):
            etapes['rapatriement'].finir()
    return echecs + finir_travaux(travaux, a, voix, session, temps, envoi_s, estimation)


def reprendre(a):
    """Finit la session Kaggle notée dans SESSION (noyau poussé puis suivi interrompu : coupure, PC éteint…) : suit le
    noyau s'il tourne encore, rapatrie sa sortie (si c'est bien celle de cette session), puis range, mixe et remet sur
    chaque vidéo, sans repasser sur le GPU. Rend la liste des échecs."""
    if not SESSION.exists():
        raise SystemExit(f'rien à reprendre : pas de session en cours ({SESSION}). Session non notée : récupérer la '
                         'sortie avec « kaggle kernels output <compte>/<noyau> », puis « doubler --remix ».')
    s = json.loads(SESSION.read_text())
    travaux = []
    for t in s['travaux']:
        tr = dict(t)
        for k in ('video', 'dossier', 'travail', 'tv'):
            tr[k] = Path(tr[k])
        travaux.append(tr)
    titres = [tr['nom'] for tr in travaux]
    voix = charger_voix(s['voix'])
    kdir = Path(s['sortie']) / '.kaggle'
    kdir.mkdir(parents=True, exist_ok=True)
    noyau = s['noyau']
    with Verrou(kdir, titres):
        if not SESSION.exists():
            raise SystemExit('la session a été finie par un autre doubler pendant l\'attente')
        dire(f"reprise de la session du {s.get('poussee_le')} : noyau {noyau} (version {s.get('version_noyau')}), "
             f"{len(travaux)} vidéo(s) : {', '.join(titres)}")
        etat = suivre_noyau(noyau, s['pousse_epoch'], s.get('attente_maxi_s', 3600), titres, s.get('estimation_s'),
                            origine_limite=time.time())
        fin = time.time()
        retour = kdir / 'retour'
        etapes = {}
        try:
            rapatriement_s = rapatrier_noyau(noyau, retour, etat, videos=titres, etapes=etapes)
        except Exception as e:
            if DEFINITIF.search(str(e)):
                clore_session('échec')   # rien à reprendre : le noyau a échoué
            raise
        # la sortie est-elle celle de cette session ? (le noyau a pu être renvoyé depuis : sa sortie serait autre)
        autres = []
        for tr in travaux:
            r = retour / tr['id'] / 'rapport.json'
            entree = (json.loads(r.read_text()).get('travail') or {}).get('entree') if r.exists() else None
            if entree and entree != tr['entree']:
                autres.append(f"{tr['id']} : {entree} au lieu de {tr['entree']}")
        if autres:
            etapes['rapatriement'].finir('sortie d\'une autre session')
            shutil.rmtree(retour, ignore_errors=True)
            raise SystemExit('la sortie du noyau sur Kaggle n\'est pas celle de cette session (noyau renvoyé depuis ?) : '
                             + ' ; '.join(autres) + f'. Effacer {SESSION} pour abandonner cette session.')
        session = json.loads((retour / 'session.json').read_text())
        ranger_retour(travaux, retour, kdir)
        clore_session('reprise')
        etapes['rapatriement'].finir()
    temps = {'pousse_epoch': s['pousse_epoch'], 'version_noyau': s.get('version_noyau'),
             'noyau_depuis_envoi_s': round(fin - s['pousse_epoch'], 1), 'rapatriement_s': rapatriement_s,
             'reprise': datetime.datetime.now().astimezone().isoformat(timespec='seconds')}
    return finir_travaux(travaux, a, voix, session, temps, s.get('envoi_s'), s.get('estimation_s'))


def preparer_outils(a):
    """Lance une fois le noyau « outils » : il dépose sur Kaggle (sortie privée de noyau) les wheels pip, Seed-VC au commit
    figé et les poids des modèles ; les doublages suivants le montent (kernel_sources) au lieu de tout retélécharger.
    Rien ne part du PC à part ce script, et seuls outils.json et le journal reviennent."""
    kdir = Path(a.sortie).expanduser().resolve() / '.kaggle'
    kdir.mkdir(parents=True, exist_ok=True)
    retour = kdir / 'retour-outils'
    # autre noyau, aucun jeu de données : peut tourner en même temps qu'un doublage (deux sessions GPU permises)
    temps = lancer_noyau({'outils': True}, kdir, retour, 60 * 60, noyau=OUTILS, jeux=(), motif=r'(outils\.json|\.log)$',
                         attendu='outils.json')
    fiche = json.loads((retour / 'outils.json').read_text())
    (kdir / 'outils.json').write_text(json.dumps(fiche, ensure_ascii=False, indent=1))
    shutil.rmtree(retour, ignore_errors=True)
    ying = fiche.get('yingmusic') or {}
    dire(f"outils prêts sur Kaggle ({OUTILS}) en {temps['noyau_depuis_envoi_s'] / 60:.1f} min : Seed-VC {fiche['commit'][:10]}, "
         f"YingMusic-SVC {str(ying.get('commit'))[:10]}, {len(fiche['wheels'])} wheels, Python {fiche['python']}, "
         f"modèles de séparation {', '.join(fiche.get('modeles_sep') or [])}, tailles {fiche['tailles_mo']} Mo")
    if (ying.get('essai') or {}).get('erreur'):
        dire(f"ATTENTION : YingMusic-SVC ne se charge pas sur Kaggle ({ying['essai']['erreur']}) : les doublages en chant "
             'se replieront sur Seed-VC 44 kHz')
    par = fiche.get('paroles') or {}
    essai = par.get('essai') or {}
    manque = [n for n in ('whisper', 'traducteur') if not par.get(n)]
    dire(f"paroles : Whisper large-v3 {'monté' if par.get('whisper') else 'absent (téléchargé à chaque doublage)'}"
         f" ({par.get('whisper_mo', '?')} Mo), traducteur NLLB-200 {'monté' if par.get('traducteur') else 'absent'}"
         f" ({par.get('traducteur_mo', '?')} Mo) ; essai : chargements {essai.get('chargements_s')}, temps {essai.get('temps')}"
         + (f", ERREUR {essai.get('erreur') or essai.get('erreur_traduction')}"
            if essai.get('erreur') or essai.get('erreur_traduction') else ''))
    if manque or essai.get('erreur'):
        dire('ATTENTION : paroles incomplètes dans les outils : les doublages les téléchargeront, ou se passeront de paroles')


def main():
    p = argparse.ArgumentParser(prog='doubler', description=__doc__.split('\n')[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split('\n', 1)[1])
    p.add_argument('videos', nargs='*', help='vidéo(s) mp4, mkv, webm, mov… ; « vidéo@60+40 » : extrait propre à cette '
                                              'vidéo (début+durée en s ou m:ss ; @60 jusqu\'à la fin, @+40 le début)')
    p.add_argument('--voix', default=None, metavar='NOM',
                   help='voix cible : un dossier de <données>/voix (défaut : clé « voix » de la configuration, '
                        'sinon la seule voix présente)')
    p.add_argument('--mode', choices=['auto', 'chant', 'parole'], default='auto')
    p.add_argument('--octave', choices=['auto', '0', '-12', '+12', '12'], default='auto',
                   help="auto : en chant, un seul décalage par chanteur, choisi d'après la hauteur (avec les ancres de "
                        "genre, facultatives, l'empreinte de la voix départage les cas ambigus et repère les duos) ; un "
                        'duo, retenu à quatre conditions, a un décalage par voix ; 0, -12 ou +12 : imposé partout')
    p.add_argument('--pas', type=int, default=30, help='pas de diffusion (défaut 30 ; 50 : ~40 %% de GPU de conversion en plus ; '
                                                       'au banc d\'essai, aucun écart mesurable et l\'oreille partagée)')
    p.add_argument('--moteur', choices=list(MOTEURS), default=None,
                   help='moteur de conversion du chant : yingmusic (YingMusic-SVC, défaut) ou seedvc (Seed-VC 44 kHz) ; '
                        'défaut lu dans la configuration (clé « moteur »). La parole passe toujours par Seed-VC 22 kHz')
    p.add_argument('--sans-nettoyage', action='store_true',
                   help='chant : convertir la voix isolée complète, sans séparer voix principale et chœurs ni retirer la '
                        'reverb (chaîne d\'avant, plus rapide)')
    p.add_argument('--sans-paroles', action='store_true',
                   help='ni transcription ni traduction des paroles, ni sous-titres, .srt ou .lrc (défaut : clé « paroles » '
                        'de la configuration, oui sinon ; avec --remix : la vidéo refaite sans les paroles)')
    p.add_argument('--voix-db', type=float, default=None, help='voix par rapport à celle de la vidéo (défaut +4 en chant, 0 en parole)')
    p.add_argument('--reverb-db', type=float, default=-13, help='reverb sous la voix sèche, en chant (défaut -13)')
    p.add_argument('--mix-v1', action='store_true',
                   help="ancien mixage : sans suivi des nuances de la voix d'origine ni dé-esseur (défaut : mixage v2)")
    p.add_argument('--debut', type=float, default=None, help="début de l'extrait (s), pour un essai")
    p.add_argument('--duree', type=float, default=None, help="durée de l'extrait (s)")
    p.add_argument('--remix', action='store_true', help='refaire seulement le mixage et la vidéo depuis travail/ (sans Kaggle)')
    p.add_argument('--reprendre', action='store_true',
                   help='finir la dernière session Kaggle interrompue (coupure, PC éteint) : suivi, rapatriement, mixage et '
                        'vidéo, sans repasser sur le GPU')
    p.add_argument('--donnees', default=None, metavar='DIR',
                   help=f'dossier des données voix/ et sorties/ (défaut : $VOIXLA_DONNEES, sinon « donnees » de '
                        f'{abrege(CONFIG)}, sinon {abrege(DONNEES_DEFAUT)})')
    p.add_argument('--sortie', default=None, help='dossier des sorties (défaut <données>/sorties)')
    p.add_argument('--evenements', default=None, metavar='FICHIER',
                   help='écrit l\'avancement en JSON, un événement par ligne (pour une interface)')
    p.add_argument('--comparer-pas', type=int, default=None, metavar='N',
                   help='convertit aussi ~30 s en N pas : comparaison_<pas>pas.mp3 et comparaison_<N>pas.mp3')
    p.add_argument('--ajouter-voix', nargs=2, metavar=('NOM', 'ENREGISTREMENT'), help='ajoute une voix depuis un enregistrement')
    p.add_argument('--voix-liste', action='store_true', help='affiche les voix disponibles')
    p.add_argument('--json', action='store_true', help='avec --voix-liste ou --config : sortie en JSON')
    p.add_argument('--config', nargs='*', default=None, metavar='CLE VALEUR',
                   help=f"sans argument : affiche la configuration ({abrege(CONFIG)}) ; avec CLE VALEUR : l'écrit "
                        f"(clés : {', '.join(CLES_CONFIG)})")
    p.add_argument('--modele-parole', choices=['parole22k', 'chant44k'], default=None, help=argparse.SUPPRESS)
    p.add_argument('--comparer-modeles', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--preparer-outils', action='store_true',
                   help='prépare une fois sur Kaggle les paquets et les modèles (installation plus rapide ensuite)')
    p.add_argument('--version', action='version', version=f'doubler (Voix-là) {VERSION}',
                   help='affiche la version et quitte')
    a = p.parse_args()
    EV.ouvrir(a.evenements)
    code = 1
    try:
        code = executer(p, a)
        return code
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if not isinstance(e.code, (int, type(None))):
            EV('erreur', None, message=str(e.code))
        raise
    except KeyboardInterrupt:
        code = 130
        EV('erreur', None, message='interrompu')
        raise
    except BaseException as e:
        EV('erreur', None, message=f'{type(e).__name__} : {e}')
        raise
    finally:
        EV('fin', None, code=code)


def executer(p, a):
    if a.config is not None:
        if len(a.config) not in (0, 2):
            p.error('--config : aucun argument (afficher) ou CLE VALEUR (écrire)')
        if a.config:
            configurer(a)   # dossier des voix : pour signaler une voix par défaut encore absente
            ecrire_config(*a.config)
            return 0
        configurer(a)
        afficher_config(a, a.json)
        return 0
    conf = configurer(a)
    if a.preparer_outils:
        configurer_kaggle(conf)
        preparer_outils(a)
        return 0
    if a.voix_liste:
        liste_voix(a.json)
        return 0
    if a.ajouter_voix:
        ajouter_voix(*a.ajouter_voix)
        return 0
    if a.reprendre:
        if not KAGGLE.exists() or not JETON.exists():
            sys.exit(f'Kaggle : commande ({KAGGLE}) ou clé (~/.kaggle/access_token) absente')
        a.paroles = not a.sans_paroles and paroles_config(conf)
        configurer_kaggle(conf)
        echecs = reprendre(a)
        if echecs:
            sys.exit(f"{len(echecs)} vidéo(s) en échec : {', '.join(map(str, echecs))}")
        return 0
    if not a.videos:
        p.error('aucune vidéo')
    if a.octave == '12':
        a.octave = '+12'
    moteur_conf = valeur_config(conf, 'moteur')
    if moteur_conf is not None and moteur_conf not in MOTEURS:
        raise SystemExit(f'{CONFIG} : moteur inconnu « {moteur_conf} » (moteurs : {", ".join(MOTEURS)})')
    moteur_donne = a.moteur is not None
    a.moteur = a.moteur or moteur_conf or MOTEUR_DEFAUT
    a.paroles = not a.sans_paroles and paroles_config(conf)
    if a.voix is None:
        a.voix = exiger_voix_defaut()
    voix = charger_voix(a.voix)
    if a.remix:
        if moteur_donne or a.sans_nettoyage:
            dire('--moteur et --sans-nettoyage sont sans effet avec --remix (le mixage repart de la voix déjà convertie)')
        echecs = []
        for v in a.videos:
            titre = Path(str(v)).stem
            try:
                chemin, debut_v, duree_v = lire_video(v, a)
                tr = emplacements(chemin, debut_v, duree_v, a, voix)
                titre = tr['nom']
                tr['debut_epoch'] = time.time()
                dire(f"=== {tr['video'].name} (remix, voix {voix['id']})")
                finir(tr, a, voix)
            except Exception as e:  # noqa: BLE001
                dire(f'ÉCHEC {v} : {e}')
                EV('erreur', titre, message=str(e))
                echecs.append(v)
    else:
        if not KAGGLE.exists() or not JETON.exists():
            sys.exit(f'Kaggle : commande ({KAGGLE}) ou clé (~/.kaggle/access_token) absente')
        configurer_kaggle(conf)
        echecs = traiter_lot(a.videos, a, voix)
    if echecs:
        sys.exit(f"{len(echecs)} vidéo(s) en échec : {', '.join(map(str, echecs))}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
