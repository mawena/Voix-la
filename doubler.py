#!/usr/bin/python3
"""doubler (Voix-là) : remplace la voix d'une vidéo par une voix clonée (YingMusic-SVC, Seed-VC ; GPU gratuit de Kaggle).

  doubler <vidéo> [<vidéo>…] [--voix NOM | --voix homme:NOM,femme:NOM | --voix chanteur1:NOM,chanteur2:NOM]
          [--mode auto|chant|parole] [--octave auto|0|-12|+12] [--pas 30]
          [--moteur yingmusic|seedvc] [--sans-nettoyage] [--sans-paroles] [--gpu auto|1|2] [--voix-db 4] [--reverb-db -13]
          [--mix-v1] [--pistes-audio toutes|doublage]
          [--autotune [N]] [--autotune-gamme auto|chromatique|la-mineur…] [--sans-autotune]
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
     française (NLLB-200) quand elles ne sont pas en français. La machine Kaggle (NvidiaTeslaT4) a deux cartes T4 : le
     nettoyage et YingMusic-SVC (et la séparation d'un lot) s'y partagent le travail, sans changer le résultat (--gpu 1 :
     une carte, comme avant ; une machine à une seule carte : repli automatique) ;
  3. de retour sur le PC : mixage puis remise sur chaque vidéo, image et sous-titres d'origine copiés sans réencodage,
     paroles ajoutées en pistes de sous-titres. Mixage v2 (défaut) : la voix suit les nuances de la voix d'origine (gain
     par fenêtres de 10 ms, lissé sur 150 ms, borné à ±6 dB, seulement là où les deux voix chantent), « s » adoucis
     (dé-esseur), voix +4 dB et reverb -13 dB en chant ; parole sans reverb. --mix-v1 : l'ancien mixage.
Sorties : <sortie>/<titre>/<titre> - voix <Nom>.<conteneur d'origine> et .mp3, .<langue>.srt, .fr.srt (traduction) et
.lrc (paroles synchronisées, à côté du mp3) ; travail/ (son d'origine, voix isolée, reste) et travail/<voix>/ (voix
convertie, rapport.json avec les temps de chaque étape, paroles.json). La vidéo a cinq pistes audio, calées entre elles
et sur l'image : Doublage (la première, seule jouée par défaut), Original, Beat seul, Voix convertie seule (telle
qu'elle sonne dans le mix) et Voix d'origine seule ; --pistes-audio doublage (clé « pistes_audio ») : le doublage seul.
Leur étiquette de langue est celle des paroles reconnues (la plus chantée, si la chanson en mêle deux), sinon celle du
son d'origine. --remix refait seulement le mixage et la remise sur la vidéo, paroles et pistes audio comprises. Seuls
le son des vidéos et la référence de la voix choisie partent sur Kaggle.
Une voix par chanteur (--voix homme:A,femme:B pour un duo mixte, chanteur1:A,chanteur2:B pour un duo de même genre) :
chaque piste de chaque passage (voix principale, chœurs, secours) prend la voix du chanteur qu'on y reconnaît, l'octave
se décide pour chaque couple chanteur -> voix ; sorties <titre> - voix A et B.<ext>, travail/A+B/.
Auto-tune (--autotune [N], de 0 à 100, 70 sans valeur ; clé « autotune » de la configuration, 0 ou absente : désactivé) :
en plus de la version normale, inchangée, <titre> - voix <Nom> (auto-tune).<conteneur>, .mp3 et .lrc, faits sur le PC
par x42-autotune (paquet x42-plugins, filtre lv2 de ffmpeg) sur la voix sèche, avant la reverb, dans la gamme détectée
sur la musique seule (--autotune-gamme). Bas : notes juste rendues plus justes ; haut : effet robotique, sauts de note.
Données (voix/, sorties/) : --donnees, sinon $VOIXLA_DONNEES, sinon « donnees » de ~/.config/voix-la/config.json,
sinon ~/Music/Voix-là. Voix par défaut : « voix » du même fichier (doubler --config voix NOM), sinon la seule voix de
<données>/voix. Compte Kaggle : « kaggle.compte » (doubler --config kaggle.compte <nom>) ; moteur du chant par
défaut : « moteur » (doubler --config moteur seedvc), sinon yingmusic. Pistes audio de la vidéo : « pistes_audio »
(toutes, défaut, ou doublage). Cartes GPU : « gpu » (auto, 1 ou 2) ; machine Kaggle : « kaggle.machine »
(NvidiaTeslaT4 par défaut : GPU T4 x2). Commande kaggle : $VOIXLA_KAGGLE, sinon ~/kaggle-venv/bin/kaggle. Ancres de
genre (facultatives) : ~/.config/voix-la/ancres_genre.json, sinon kaggle/ du dépôt.
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

VERSION = '0.6.0'   # même version que le paquet Debian (debian/changelog) et l'interface (voixla.__version__)
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
# Machine Kaggle demandée à l'envoi du noyau (kernel-metadata.json, machine_shape) : « NvidiaTeslaT4 » est la machine
# « GPU T4 x2 », deux cartes T4 de 16 Go (doc. de la CLI kaggle, kernels_metadata.md ; vérifié par nvidia-smi -L sur un
# noyau privé le 05/11/2026). Le quota GPU hebdomadaire se compte en heures de session, pas en heures par carte.
MACHINE_DEFAUT = 'NvidiaTeslaT4'
MACHINE_SYNTAXE = r'[A-Za-z][A-Za-z0-9]{1,39}'
GPU_CHOIX = ('auto', '1', '2')   # cartes utilisées par le noyau ; auto : toutes celles de la machine, deux au plus
# Pistes audio de la vidéo doublée : toutes (le doublage, en premier et seul par défaut, puis l'original, le beat seul,
# la voix convertie seule et la voix d'origine seule) ou doublage (lui seul, comme avant le 05/11/2026)
PISTES_AUDIO = ('toutes', 'doublage')
PISTES_AUDIO_DEFAUT = 'toutes'
CLES_CONFIG = {'autotune': 'intensité de l\'auto-tune à chaque doublage, de 0 (désactivé, défaut) à 100',
               'donnees': 'dossier des données (voix/, sorties/)',
               'gpu': 'cartes GPU utilisées par le noyau : auto (défaut : les deux T4 de la machine, une seule si elle '
                      'n\'en a qu\'une), 1 (une carte, comme avant) ou 2',
               'kaggle.compte': 'compte Kaggle (nom d\'utilisateur)',
               'kaggle.jeu': 'jeu de données privé des entrées',
               'kaggle.machine': f'machine Kaggle demandée à l\'envoi du noyau (défaut : {MACHINE_DEFAUT}, GPU T4 x2)',
               'kaggle.noyau': 'noyau GPU privé du doublage',
               'kaggle.outils': 'noyau privé des outils préparés',
               'moteur': 'moteur de conversion du chant : yingmusic (défaut) ou seedvc',
               'paroles': 'paroles à chaque doublage (transcription, traduction française, sous-titres) : oui (défaut) '
                          'ou non',
               'pistes_audio': 'pistes audio de la vidéo doublée : toutes (défaut : doublage, original, beat seul, voix '
                               'convertie seule, voix d\'origine seule ; le doublage par défaut) ou doublage (lui seul)',
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
MACHINE = MACHINE_DEFAUT   # configurer_kaggle() : clé « kaggle.machine » de CONFIG
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
    if cle == 'gpu':
        valeur = str(valeur).strip().lower()
        if valeur not in GPU_CHOIX:
            raise SystemExit(f'gpu : auto, 1 ou 2 attendu (reçu : {valeur})')
    if cle == 'pistes_audio':
        valeur = str(valeur).strip().lower()
        if valeur not in PISTES_AUDIO:
            raise SystemExit(f'pistes_audio : toutes ou doublage attendu (reçu : {valeur})')
    if cle == 'kaggle.machine' and not re.fullmatch(MACHINE_SYNTAXE, valeur):
        raise SystemExit(f'kaggle.machine : nom de machine Kaggle attendu, par exemple {MACHINE_DEFAUT} (reçu : {valeur})')
    if cle == 'voix' and not re.fullmatch(NOM_VOIX, valeur):
        raise SystemExit(f'nom de voix invalide : {valeur} (minuscules, chiffres, - ou _ ; ex. marie, jean-b)')
    if cle == 'paroles':
        booleen = oui_non(valeur)
        if booleen is None:
            raise SystemExit(f'paroles : oui ou non attendu (reçu : {valeur})')
        valeur = booleen
    if cle == 'autotune':
        try:
            valeur = intensite_autotune(valeur)
        except ValueError as e:
            raise SystemExit(f'autotune : {e} ; 0 le désactive') from None
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


def gpu_config(conf):
    """Clé « gpu » de la configuration : auto (défaut), 1 ou 2 ; une valeur illisible compte comme auto, avec un avis."""
    v = valeur_config(conf, 'gpu')
    if v is None:
        return 'auto'
    v = str(v).strip().lower()
    if v not in GPU_CHOIX:
        dire(f'attention : « gpu » de {CONFIG} illisible ({v!r}) : auto (doubler --config gpu auto|1|2)')
        return 'auto'
    return v


def pistes_audio_config(conf):
    """Clé « pistes_audio » de la configuration : toutes (défaut) ou doublage ; une valeur illisible compte comme
    toutes, avec un avis."""
    v = valeur_config(conf, 'pistes_audio')
    if v is None:
        return PISTES_AUDIO_DEFAUT
    v = str(v).strip().lower()
    if v not in PISTES_AUDIO:
        dire(f'attention : « pistes_audio » de {CONFIG} illisible ({v!r}) : {PISTES_AUDIO_DEFAUT} '
             '(doubler --config pistes_audio toutes|doublage)')
        return PISTES_AUDIO_DEFAUT
    return v


def cartes_prevues(gpu, machine):
    """Cartes que le noyau utilisera probablement (estimation de durée) : 1 si on le demande ; 2 si on le demande, ou
    en auto sur la machine à deux T4 (NvidiaTeslaT4) ; 1 sur une autre machine en auto (nombre inconnu)."""
    if str(gpu) == '1':
        return 1
    return 2 if str(gpu) == '2' or machine == MACHINE_DEFAUT else 1


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
    """Identifiants <compte>/<nom> du jeu de données et des noyaux, d'après CONFIG ; le compte n'a pas de défaut. Machine
    Kaggle demandée à l'envoi : « kaggle.machine », sinon MACHINE_DEFAUT."""
    global JEU, NOYAU, OUTILS, MACHINE
    conf = lire_config() if conf is None else conf
    k = conf.get('kaggle') if isinstance(conf.get('kaggle'), dict) else {}
    machine = str(k.get('machine') or '').strip()
    if machine and not re.fullmatch(MACHINE_SYNTAXE, machine):
        dire(f'attention : « kaggle.machine » de {CONFIG} illisible ({machine!r}) : {MACHINE_DEFAUT}')
        machine = ''
    MACHINE = machine or MACHINE_DEFAUT
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
    autotune = autotune_config(conf)
    gpu = gpu_config(conf)
    pistes_audio = pistes_audio_config(conf)
    fat1 = trouver_fat1()
    effectif = {'config': str(CONFIG), 'donnees': str(DONNEES), 'source_donnees': a.source_donnees, 'voix': str(VOIX),
                'sorties': a.sortie, 'voix_defaut': VOIX_DEFAUT, 'source_voix_defaut': VOIX_DEFAUT_SOURCE,
                'moteur': moteur, 'paroles': paroles, 'autotune': autotune, 'gpu': gpu, 'pistes_audio': pistes_audio,
                'x42_autotune': str(fat1) if fat1 else None,
                'kaggle': {'compte': valeur_config(conf, 'kaggle.compte'), **noms, 'machine': MACHINE},
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
    print(f"  auto-tune      {autotune or 'désactivé'}{'' if valeur_config(conf, 'autotune') is not None else '  (défaut)'}"
          f" ; x42-autotune {fat1 or 'absent (sudo apt install x42-plugins)'}")
    print(f"  gpu            {gpu}{'' if valeur_config(conf, 'gpu') is not None else '  (défaut)'}"
          f" : {'une carte' if gpu == '1' else 'deux cartes' if gpu == '2' else 'les cartes de la machine, deux au plus'}")
    print(f"  pistes audio   {pistes_audio}{'' if valeur_config(conf, 'pistes_audio') is not None else '  (défaut)'} : "
          + ('le doublage seul' if pistes_audio == 'doublage' else
             "doublage (par défaut), original, beat seul, voix convertie seule, voix d'origine seule"))
    for n in KAGGLE_DEFAUTS:
        print(f"  kaggle.{n:8s} {noms[n] or '— (kaggle.compte manque : doubler --config kaggle.compte <nom>)'}")
    print(f"  kaggle.machine {MACHINE}{'' if valeur_config(conf, 'kaggle.machine') else '  (défaut : GPU T4 x2)'}")
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


def ff(*args, env=None):
    lancer(['nice', '-n', '10', 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)],
           **({'env': env} if env is not None else {}))


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


# Une voix par chanteur : --voix homme:NOM,femme:NOM (duo mixte) ou chanteur1:NOM,chanteur2:NOM (duo de même genre,
# chanteurs pris dans l'ordre d'apparition). Le noyau reçoit voix_cibles (kaggle/doubler_kaggle.py, decider_chanteurs).
CLES_VOIX = {'genre': ('homme', 'femme'), 'ordre': ('chanteur1', 'chanteur2')}


def lire_spec_voix(texte):
    """« NOM » -> (None, [(None, NOM)]) ; « homme:a,femme:b » ou « chanteur1:a,chanteur2:b » -> (mode, [(clé, nom)]),
    rangé dans l'ordre des clés (homme puis femme, chanteur1 puis chanteur2). ValueError si la syntaxe est fausse."""
    texte = str(texte).strip()
    if ':' not in texte and ',' not in texte:
        if not re.fullmatch(NOM_VOIX, texte):
            raise ValueError(f'nom de voix invalide : « {texte} » (minuscules, chiffres, - ou _)')
        return None, [(None, texte)]
    toutes = [c for cles in CLES_VOIX.values() for c in cles]
    paires = []
    for morceau in (m.strip() for m in texte.split(',')):
        if not morceau:
            continue
        cle, sep, nom = (x.strip() for x in morceau.partition(':'))
        cle = cle.lower().replace(' ', '').replace('-', '').replace('_', '')
        if not sep or not nom:
            raise ValueError(f'« {morceau} » : clé:voix attendu ({", ".join(f"{c}:NOM" for c in toutes)})')
        if cle not in toutes:
            raise ValueError(f'clé inconnue « {cle} » : homme et femme (duo mixte), ou chanteur1 et chanteur2 (duo de '
                             'même genre)')
        if not re.fullmatch(NOM_VOIX, nom):
            raise ValueError(f'nom de voix invalide : « {nom} » (minuscules, chiffres, - ou _)')
        if cle in (c for c, _ in paires):
            raise ValueError(f'clé {cle} donnée deux fois')
        paires.append((cle, nom))
    if not paires:
        raise ValueError('aucune voix')
    modes = {m for m, cles in CLES_VOIX.items() for c, _ in paires if c in cles}
    if len(modes) > 1:
        raise ValueError('homme/femme et chanteur1/chanteur2 ne se mélangent pas : homme:…,femme:… pour un duo mixte, '
                         'chanteur1:…,chanteur2:… pour un duo de même genre')
    mode = modes.pop()
    return mode, sorted(paires, key=lambda p: CLES_VOIX[mode].index(p[0]))


def charger_voix_spec(texte):
    """La voix de --voix : une voix (charger_voix, comme avant), ou plusieurs voix, une par chanteur, réunies en une voix
    combinée : id « a+b » (dossier travail/a+b/), affichage « A et B » (« <titre> - voix A et B.<ext> »), référence et
    hauteur de la première (celles de l'analyse), plus mode, membres (chacun avec sa clé) et spec (à garder pour
    --reprendre). Une seule voix différente (« homme:a » seul, ou la même deux fois) : la voix seule, comme avant."""
    try:
        mode, paires = lire_spec_voix(texte)
    except ValueError as e:
        raise SystemExit(f'--voix {texte} : {e}') from None
    noms = list(dict.fromkeys(n for _, n in paires))
    if mode is None or len(noms) == 1:
        v = charger_voix(noms[0])
        if mode is not None:
            dire(f'--voix {texte} : une seule voix différente ({noms[0]}) : doublage à une voix, comme --voix {noms[0]}')
        return v
    membres = [charger_voix(n) for n in noms]
    for (cle, _), m in zip(paires, membres):
        m['cle'] = cle
    return {'id': '+'.join(noms), 'affichage': ' et '.join(m['affichage'] for m in membres),
            'reference': membres[0]['reference'], 'f0': membres[0]['f0'], 'fiche': membres[0]['fiche'], 'mode': mode,
            'membres': membres, 'spec': ','.join(f'{c}:{n}' for c, n in paires)}


def voix_membres(voix):
    """Les voix réelles d'une voix (combinée ou non)."""
    return voix.get('membres') or [voix]


def decrire_voix(voix):
    """« jules », ou « homme → jules, femme → marie » (plusieurs voix)."""
    if not voix.get('membres'):
        return voix['id']
    return ', '.join(f"{m['cle'].replace('chanteur', 'chanteur ')} → {m['id']}" for m in voix['membres'])


def lignes_voix_cibles(rapport):
    """Ce qu'il faut dire après un doublage à plusieurs voix (rapport du noyau) : la décision (duo, ou une seule voix
    trouvée, avec la raison), puis les secondes chantées par chaque voix et les passages en duo. [] avec une seule voix."""
    vc = rapport.get('voix_cibles')
    if not isinstance(vc, dict):
        return []
    if vc.get('message'):   # parole : la première voix seule
        return [f"voix cibles : {vc['message']}"]
    dh = rapport.get('decision_hauteur') or {}
    lignes = []
    if dh.get('voix') == 2:
        lignes.append(f"voix cibles, {'duo mixte' if vc.get('mode') == 'genre' else 'duo'} ("
                      f"{'axe du genre' if dh.get('axe') == 'genre' else 'empreintes de la chanson'}) : "
                      + ', '.join(f"{c.get('nom')} → {c.get('voix')} ({c.get('decalage', 0):+d} dt)"
                                  for c in dh.get('chanteurs') or []))
    elif dh.get('message'):
        lignes.append(f"ATTENTION : {dh['message']}" + (f" ({dh['pourquoi_une_voix']})" if dh.get('pourquoi_une_voix') else ''))
    aff = rapport.get('affectation') or {}
    sec = aff.get('secondes_par_voix') or {}
    if sec:
        lignes.append('voix entendues : ' + ', '.join(f'{v} {s:.0f} s' for v, s in sec.items() if v != 'ensemble')
                      + (f", ensemble {sec['ensemble']:.0f} s" if sec.get('ensemble') else '')
                      + (f" ; {aff['morceaux_en_duo']} passage(s) en duo (voix principale et chœurs)"
                         if aff.get('morceaux_en_duo') else ''))
    if aff.get('erreur'):
        lignes.append(f"ATTENTION : reconnaissance des pistes impossible ({aff['erreur']}) : chaque piste prend la voix "
                      'du passage')
    return lignes


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
    """Une version du jeu de données privé par session : la référence de la voix choisie (de chaque voix, avec une voix
    par chanteur) et le son des vidéos seulement, en FLAC 24 bits (gain de chaque vidéo noté dans tr['gain_entree'], à
    transmettre au noyau)."""
    jeu = dossier / 'jeu'
    shutil.rmtree(jeu, ignore_errors=True)
    jeu.mkdir(parents=True)
    for m in voix_membres(voix):
        shutil.copy2(m['reference'], jeu / f"voix-{m['id']}.wav")
    taille, taille_wav = 0, 0
    for tr in travaux:
        tr['gain_entree'] = encoder_flac(tr['travail'] / 'original.wav', jeu / tr['entree'])
        tr['taille_envoi'] = (jeu / tr['entree']).stat().st_size
        taille += tr['taille_envoi']
        taille_wav += (tr['travail'] / 'original.wav').stat().st_size
    with open(jeu / 'dataset-metadata.json', 'w') as f:
        json.dump({'title': JEU.split('/')[1], 'id': JEU, 'licenses': [{'name': 'unknown'}]}, f)
    existe = kaggle('datasets', 'status', JEU)[0] == 0
    noms = [m['id'] for m in voix_membres(voix)]
    dire(f'Kaggle : envoi du son de {len(travaux)} vidéo(s) ({taille / 1e6:.0f} Mo en FLAC au lieu de {taille_wav / 1e6:.0f} Mo '
         f"en WAV) et de {'la voix ' + noms[0] if len(noms) == 1 else 'les voix ' + ' et '.join(noms)} dans le jeu privé {JEU}")
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


# Sans historique (voir estimer_noyau_s), sur deux cartes : part du temps de GPU (deux cartes additionnées) que le
# nettoyage et YingMusic-SVC prennent en temps réel, chargements à part (mesuré le 05/11/2026 : 0,69 et 0,55) ; part de
# leur durée que coûtent les paroles, faites sur la carte 1 pendant que la carte 0 commence la conversion YingMusic-SVC :
# la moitié (la carte 0 convertit seule pendant ce temps ; mesuré le 06/11/2026 : 0,4 à 0,45), jamais moins que ce qui
# dépasse la conversion (paroles plus longues que toute la conversion à deux cartes)
FACTEURS_DEUX_CARTES = {'nettoyage': 0.70, 'conversion': 0.55, 'paroles': 0.5}
# Une carte (cartes=1) : le temps de GPU du nettoyage et de YingMusic-SVC que notent les sessions à deux cartes (deux
# cartes additionnées, tâches et morceaux seuls) ramené à celui d'une carte seule, qui lit, assemble et écrit chaque
# piste dans ce temps-là (mesuré le 06/11/2026 sur la même chanson de 2 min 49, à une puis à deux cartes : nettoyage
# 376 s contre 346, conversion 504 s contre 482). Toujours à une carte, ce que ces deux étapes prennent en plus de leur
# temps de GPU et de leur chargement, quelle que soit la durée du son (démarrage de leur processus, préparation de la
# voix de référence…), sans session passée à une carte pour le mesurer (04 au 06/11/2026 : 3 à 4 s et 13 à 15 s)
FACTEURS_UNE_CARTE = {'nettoyage': 1.09, 'conversion': 1.04}
SURCOUTS_UNE_CARTE_S = {'nettoyage': 3.5, 'conversion': 13.5}
# Même chanson, déjà doublée : son de même durée, à MEME_CHANSON_S près, plus long que MEME_CHANSON_MIN_S (un extrait
# court peut tomber sur la durée d'un autre)
MEME_CHANSON_S, MEME_CHANSON_MIN_S = 0.05, 60.0


def rapports_passes(sorties, n=30):
    """Les n derniers rapport.json (<sorties>/*/travail/*/rapport.json) d'un dossier de sorties, ou de plusieurs (une
    liste ; un rapport vu deux fois ne compte qu'une fois), du plus ancien au plus récent."""
    dossiers = [sorties] if isinstance(sorties, (str, os.PathLike)) else list(sorties)
    vus = {}
    for s in dossiers:
        for p in Path(s).expanduser().glob('*/travail/*/rapport.json'):
            try:
                vus.setdefault(p.resolve(), p.stat().st_mtime)
            except OSError:
                continue
    return sorted(vus, key=vus.get)[-n:]


def estimer_noyau_s(durees_s, pas, sorties, nettoyage=True, mode='auto', moteur=MOTEUR_DEFAUT, paroles=True, voix_cibles=1,
                    cartes=1):
    """Durée probable d'une session (de l'envoi du noyau à sa fin), d'après la durée audio des vidéos et les rapports
    passés (rapports_passes : sous <sorties>/*/travail/*/, sorties pouvant être une liste de dossiers) : démarrage (file
    Kaggle comprise) + installation + chargement des modèles, le reste de la session (chargement de la séparation, fin
    des vidéos, fin du noyau), puis par seconde d'audio la séparation et l'analyse, le nettoyage (chant) et la conversion
    (par pas de diffusion ; voix nettoyée : voix principale, chœurs et passages repris par la piste de secours convertis
    à part, environ 1,5 fois plus), enfin les paroles (une part fixe : démarrage, chargement de Whisper large-v3 et du
    traducteur depuis leur copie locale ; puis transcription et traduction, par seconde d'audio).
    La conversion se mesure sur les sessions passées au même nombre de pas s'il y en a (elle n'est pas proportionnelle
    aux pas : 50 pas prennent 1,3 fois 30 pas sur la même chanson), et sur la même chanson si elle a déjà été doublée
    (même durée de son, MEME_CHANSON_S) : elle dépend surtout de la place des chœurs, que la durée seule ne dit pas (du
    04 au 06/11/2026, de 0,042 à 0,099 s par pas et par seconde de son ; une chanson aux chœurs partout en fait deux
    pistes entières).
    Le mode n'est connu qu'après la séparation : en mode auto, l'estimation suppose du chant (la plus longue). Défauts
    prudents sans historique (nettoyage : banc d'essai du 04/11/2026 ; paroles : essais du 06/11/2026).
    Deux cartes (cartes=2, machine GPU T4 x2) : les temps par seconde d'audio restent des temps de GPU (somme des
    cartes) ; le nettoyage et la conversion YingMusic-SVC, partagés entre les cartes, n'en prennent qu'une part en temps
    réel (FACTEURS_DEUX_CARTES, ou ce qu'ont pris les sessions passées à deux cartes), et la séparation d'un lot environ
    la moitié (jamais moins que la plus longue vidéo). Avec YingMusic-SVC, les paroles (P, leur durée sur la carte 1,
    attente du chargement de la carte 0 comprise) passent pendant la conversion (Y, sa durée à deux cartes) : elles
    coûtent max(0,5 × P, P − Y). Une carte, la parole ou Seed-VC : les paroles après la conversion, P en entier.
    Une carte (cartes=1) : le temps de GPU du nettoyage et de YingMusic-SVC noté par une session à deux cartes compte
    pour ce qu'il aurait pris sur une carte seule (FACTEURS_UNE_CARTE), et ces deux étapes coûtent en plus ce qu'elles
    prennent hors de leur temps de GPU et de leur chargement (SURCOUTS_UNE_CARTE_S, ou ce qu'elles ont pris aux
    sessions passées à une carte)."""
    import statistics
    une = cartes <= 1
    fixes, autres, extras, sep, net, convs, par, par_fixes, par_fixes_carte = [], [], [], [], [], [], [], [], []
    f_net, f_conv = [], []
    surcouts = {'nettoyage': [], 'conversion': []}
    f_par = FACTEURS_DEUX_CARTES['paroles']
    for p in rapports_passes(sorties):
        try:
            r = json.loads(p.read_text())
            d, tps = float(r['duree_s']), r.get('temps', {})
            tl = r.get('temps_local', {})
            sk = tl.get('session_kaggle', {})
            v = r.get('variantes', {}).get('voix_convertie.flac', {})
            nettoyee = bool((r.get('nettoyage') or {}).get('applique'))
            seule = tl.get('videos_dans_la_session') == 1
            deux = (tl.get('gpus') or 1) >= 2
            ying_v = (v.get('moteur') or 'yingmusic') == 'yingmusic'
            # paroles faites sur la carte 1 pendant la conversion (leur durée, comprise dans yingmusic_total_s)
            pendant = sk.get('paroles_pendant_conversion_s')
            if sk.get('installation_s') is not None:
                fixes.append(sk['installation_s'] + (sk.get('chargement_modeles_s') or 0)
                             + (tl.get('attente_demarrage_noyau_s') or 0))
                if sk.get('chargement_nettoyage_s') is not None or sk.get('chargement_yingmusic_s') is not None:
                    extras.append((sk.get('chargement_nettoyage_s') or 0) + (sk.get('chargement_yingmusic_s') or 0))
                if seule and d > 5 and tl.get('noyau_depuis_envoi_s') and not tl.get('reprise'):
                    # le reste de la session, hors des parts estimées ici : démarrage du noyau, chargement de la
                    # séparation, fin de la vidéo, écriture et fin du noyau, jusqu'à ce que doubler la voie finie
                    ying_s = sk.get('yingmusic_total_s')
                    reste = (tl['noyau_depuis_envoi_s'] - (tl.get('attente_demarrage_noyau_s') or 0) - sk['installation_s']
                             - (sk.get('chargement_modeles_s') or 0) - (tps.get('separation_s') or 0)
                             - (tps.get('analyse_s') or 0) - (sk.get('nettoyage_totale_s') or 0)
                             - (ying_s if ying_s is not None else (tps.get('conversion_s') or 0))
                             - (0 if pendant is not None else (sk.get('paroles_total_s') or 0)))
                    if 0 < reste < 600:
                        autres.append(reste)
            if d > 5 and tps.get('separation_s'):
                sep.append((tps['separation_s'] + tps.get('analyse_s', 0)) / d)
            # estimation à une carte d'après une session à deux cartes : son temps de GPU ramené à une carte seule
            # (la conversion seulement si YingMusic-SVC l'a partagée entre les cartes)
            if d > 5 and nettoyee and tps.get('nettoyage_s'):
                net.append(tps['nettoyage_s'] / d * (FACTEURS_UNE_CARTE['nettoyage'] if une and deux else 1.0))
            if d > 5 and v.get('conversion_s') and v.get('pas_diffusion'):
                e = FACTEURS_UNE_CARTE['conversion'] if une and deux and sk.get('yingmusic_gpu_s') and ying_v else 1.0
                # (par pas et par seconde de son, pas, voix nettoyée, durée du son : même pas, même chanson)
                convs.append((v['conversion_s'] / d / v['pas_diffusion'] * e, v['pas_diffusion'], nettoyee, d))
            if not deux and seule:
                # une carte : ce que le nettoyage et YingMusic-SVC prennent hors de leur temps de GPU et de leur chargement
                if nettoyee and tps.get('nettoyage_s') and sk.get('nettoyage_totale_s') is not None:
                    x = sk['nettoyage_totale_s'] - (sk.get('chargement_nettoyage_s') or 0) - tps['nettoyage_s']
                    if 0 <= x < 120:
                        surcouts['nettoyage'].append(x)
                if (ying_v and v.get('conversion_s') and sk.get('yingmusic_total_s') is not None
                        and len(r.get('variantes') or {}) == 1):
                    x = sk['yingmusic_total_s'] - (sk.get('chargement_yingmusic_s') or 0) - v['conversion_s']
                    if 0 <= x < 120:
                        surcouts['conversion'].append(x)
            if 'paroles_copie_s' in sk and sk.get('paroles_total_s') is not None and seule:
                # tout ce qui n'est pas transcription (démarrage du processus, chargements, traduction) compte comme fixe ;
                # seulement les noyaux qui copient les modèles sur le disque local (avant : 5 min de chargement). Sur la
                # carte 1 pendant la conversion, l'attente du chargement de YingMusic-SVC sur la carte 0 en fait partie
                (par_fixes_carte if pendant is not None else par_fixes).append(
                    max(float(sk['paroles_total_s']) - float(tps.get('paroles_s') or 0), 0.0))
            if d > 5 and tps.get('paroles_s'):
                par.append(tps['paroles_s'] / d)
            if deux and seule:
                # part du temps de GPU (somme des cartes) que le nettoyage et YingMusic-SVC ont pris en temps réel ;
                # paroles pendant la conversion : leur part (f_par) retirée, si la carte 1 a rejoint la conversion
                if sk.get('nettoyage_gpu_s'):
                    f_net.append((sk['nettoyage_totale_s'] - (sk.get('chargement_nettoyage_s') or 0)) / sk['nettoyage_gpu_s'])
                if sk.get('yingmusic_gpu_s'):
                    x = sk['yingmusic_total_s'] - (sk.get('chargement_yingmusic_s') or 0)
                    if pendant is None or x > pendant:
                        f_conv.append((x - f_par * (pendant or 0)) / sk['yingmusic_gpu_s'])
        except (OSError, ValueError, KeyError, TypeError):
            continue

    def debit_conversion(duree, nettoyee):
        """Conversion par pas et par seconde de son d'une vidéo de cette durée : sessions passées au même nombre de pas
        s'il y en a, celles de la même chanson parmi elles s'il y en a ; None sans historique."""
        genre = [c for c in convs if c[2] == nettoyee]
        memes_pas = [c for c in genre if c[1] == pas] or genre
        meme = [c[0] for c in memes_pas if duree > MEME_CHANSON_MIN_S and abs(c[3] - duree) <= MEME_CHANSON_S]
        return statistics.median(meme or [c[0] for c in memes_pas]) if memes_pas else None

    fixe = statistics.median(fixes) if fixes else 120.0
    autre = statistics.median(autres) if autres else 80.0
    t_sep = statistics.median(sep) if sep else 0.8
    t_net = 0.0
    chant = mode != 'parole'
    nettoie = nettoyage and chant
    if nettoie:
        fixe += statistics.median(extras) if extras else 90.0   # chargement des deux Roformer et de YingMusic-SVC
        t_net = statistics.median(net) if net else 2.3           # banc : ~9 min de plus pour 4 min de chant
    elif chant and moteur == 'yingmusic':
        fixe += 30.0   # chargement de YingMusic-SVC

    def t_conv(duree):
        t = debit_conversion(duree, False)
        t = 0.045 if t is None else t
        if nettoie:   # sans voix nettoyée passée : 1,5 fois la voix complète
            t_n = debit_conversion(duree, True)
            t = 1.5 * t if t_n is None else t_n
        return t
    if voix_cibles > 1 and chant:   # une voix par chanteur : chaque référence préparée par moteur, pistes reconnues
        fixe += 20.0 * (voix_cibles - 1)
        t_sep += 0.05
    total, plus_longue = float(sum(durees_s)), float(max(durees_s, default=0.0))
    conversion = pas * sum(float(x) * t_conv(float(x)) for x in durees_s)
    # paroles : part fixe (copie locale des modèles) et transcription, traduction comprise, par seconde d'audio
    t_par = statistics.median(par) if par else 0.45
    p_fixe = statistics.median(par_fixes) if par_fixes else 25.0
    t_paroles = (p_fixe + total * t_par) if paroles else 0.0
    ying = chant and moteur == 'yingmusic'
    if une:
        # le nettoyage et YingMusic-SVC prennent un peu plus que leur temps de GPU et leur chargement
        surcout = sum((statistics.median(surcouts[k]) if surcouts[k] else SURCOUTS_UNE_CARTE_S[k])
                      for k, oui in (('nettoyage', nettoie), ('conversion', ying)) if oui)
        return round(autre + fixe + t_paroles + surcout + total * (t_sep + t_net) + conversion)
    # Deux cartes : seuls le nettoyage, la conversion YingMusic-SVC et la séparation d'un lot se partagent entre les
    # cartes ; les paroles passent sur la carte 1 pendant la conversion YingMusic-SVC
    f_sep = max(0.5, plus_longue / total) if len(durees_s) > 1 and total > 0 else 1.0
    borne = lambda f: min(1.0, max(0.4, f))
    fn = borne(statistics.median(f_net)) if f_net else FACTEURS_DEUX_CARTES['nettoyage']
    fc = borne(statistics.median(f_conv)) if f_conv else FACTEURS_DEUX_CARTES['conversion']
    conversion *= fc if ying else 1.0
    if paroles and ying:
        # sans session passée de ce genre : la part fixe d'avant, plus l'attente du chargement de YingMusic-SVC sur la
        # carte 0 (les modèles se chargent l'un après l'autre)
        p = (statistics.median(par_fixes_carte) if par_fixes_carte else p_fixe + 25.0) + total * t_par
        t_paroles = max(f_par * p, p - conversion)
    return round(autre + fixe + t_paroles + total * (t_sep * f_sep + t_net * fn) + conversion)


PANNE_MAXI_S = 3 * 3600   # coupure réseau tolérée pendant le suivi et le rapatriement (le noyau, lui, continue sur Kaggle)
RESEAU = re.compile(r'pas de réponse|timed? ?out|connection|connexion|network|réseau|temporary failure|name or service|'
                    r'resolve|unreachable|max retries|ssl|reset by peer|50[234]|bad gateway|service unavailable', re.I)


def panne_reseau(code, texte):
    """Échec d'une commande kaggle dû au réseau (à attendre), plutôt qu'à une vraie erreur (à signaler vite)."""
    return code == 124 or bool(RESEAU.search(texte or ''))


MACHINE_REFUSEE = re.compile(r'machine.?shape|accelerator|machineshape', re.I)


def metadonnees_noyau(noyau, jeux, sources, machine):
    """kernel-metadata.json du noyau : privé, GPU et internet, sur la machine demandée (machine_shape)."""
    return {'id': noyau, 'title': noyau.split('/')[1], 'code_file': 'doubler_kaggle.py', 'language': 'python',
            'kernel_type': 'script', 'is_private': True, 'enable_gpu': True, 'enable_tpu': False,
            'enable_internet': True, 'machine_shape': machine, 'dataset_sources': list(jeux),
            'competition_sources': [], 'kernel_sources': list(sources), 'model_sources': []}


def pousser_noyau(reglages, dossier, attente_maxi, noyau, jeux, sources=(), videos=()):
    """Écrit et pousse le noyau (GPU + internet, machine MACHINE : deux T4 par défaut) ; attend une place si les sessions
    GPU du compte sont prises. Une autre machine (kaggle.machine) refusée par Kaggle : nouvel essai sur MACHINE_DEFAUT (le
    noyau s'adapte au nombre de cartes qu'il trouve). Rend (heure de la poussée, numéro de version Kaggle ou None)."""
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
    machine = MACHINE

    def ecrire_metadonnees():
        with open(envoi / 'kernel-metadata.json', 'w') as f:
            json.dump(metadonnees_noyau(noyau, jeux, sources, machine), f, indent=1)
    ecrire_metadonnees()
    debut, attente = time.time(), None
    try:
        while True:   # deux sessions GPU au plus sur le compte : la suivante attend une place
            code, texte = kaggle('kernels', 'push', '-p', str(envoi))
            if re.search(r'successfully pushed', texte, re.I):
                dire('Kaggle : noyau GPU lancé (' + texte.strip().splitlines()[-1].split('.')[0] + f', machine {machine})')
                break
            if machine != MACHINE_DEFAUT and MACHINE_REFUSEE.search(texte) and not re.search(r'GPU session count', texte, re.I):
                dire(f'Kaggle : machine {machine} refusée ({texte.strip().splitlines()[-1][-200:]}) : nouvel essai sur '
                     f'{MACHINE_DEFAUT} (GPU T4 x2)')
                machine = MACHINE_DEFAUT
                ecrire_metadonnees()
                continue
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
    rev = None
    if mode == 'chant':
        rev = reverb(tv, seche, f'reverb_{voix_fichier.stem}.wav', duree)
        if 'reverb' not in g:
            g['reverb'] = cible - moyenne_db(rev) + voix_db + reverb_db
    assembler(t, seche, rev, g, sortie_wav)
    return g, cible


def reverb(tv, seche, nom, duree):
    """Reverb synthétique de la voix sèche (réponse impulsionnelle tv/reverb_ir.wav, faite une fois), coupée à duree."""
    ir = tv / 'reverb_ir.wav'
    if not ir.exists():
        ff('-f', 'lavfi', '-i', 'anoisesrc=d=1.6:c=pink:r=44100:a=0.5:seed=11', '-f', 'lavfi',
           '-i', 'anoisesrc=d=1.6:c=pink:r=44100:a=0.5:seed=23', '-filter_complex',
           '[0][1]join=inputs=2:channel_layout=stereo,afade=t=out:st=0:d=1.6:curve=exp,highpass=f=250,lowpass=f=6500', ir)
    rev = tv / nom
    ff('-i', seche, '-i', ir, '-filter_complex', '[0:a]adelay=25|25[d];[d][1:a]afir[w]',
       '-map', '[w]', '-t', f'{duree:.3f}', '-c:a', 'pcm_f32le', rev)
    return rev


def assembler(t, seche, rev, g, sortie_wav, musique=True):
    """Mix final : musique seule + voix sèche (gain g['seche']) + reverb (g['reverb'], s'il y en a une), puis limiteur.
    musique faux : la voix convertie seule, telle qu'elle sonne dans le mix (piste « voix convertie seule » de la vidéo) :
    même graphe, musique mise à zéro (elle ne sert plus qu'à donner au résultat la durée du mix)."""
    entrees = ['-i', t / 'instrumental.wav', '-i', seche]
    graphe, fond = ('', '[0:a]') if musique else ('[0:a]volume=0[z];', '[z]')
    graphe += f"[1:a]volume={g['seche']:.2f}dB[s];"
    if rev is not None:
        entrees += ['-i', rev]
        graphe += f"[2:a]volume={g['reverb']:.2f}dB[r];{fond}[s][r]amix=inputs=3:normalize=0:duration=first,"
    else:
        graphe += f'{fond}[s]amix=inputs=2:normalize=0:duration=first,'
    # limiteur sur la crête vraie (détection suréchantillonnée x4), -1 dBTP environ ; latency=1 : sans elle, alimiter
    # retarde le mix de 5 ms (son en retard sur l'image)
    graphe += f'aresample={4 * 44100},alimiter=limit={LIMITE:.4f}:level=disabled:latency=1,aresample=44100[m]'
    ff(*entrees, '-filter_complex', graphe, '-map', '[m]', '-c:a', 'pcm_s24le', sortie_wav)


# --- Auto-tune : x42-autotune (fat1) par le filtre lv2 de ffmpeg, sur la voix sèche, avant la reverb ------------------
#
# La voix sèche du mix (seche_voix_convertie.wav) passe dans fat1, puis la reverb et le mix sont refaits aux gains du
# mix normal (même niveau : les deux versions se comparent à l'oreille) : seche_voix_autotune.wav,
# reverb_voix_autotune.wav (chant), mix_autotune.wav, puis « <titre> - voix <Nom> (auto-tune).<ext> », .mp3 et .lrc.
# La version normale n'est jamais touchée (mêmes commandes, mêmes fichiers qu'avant). Tout sur le PC, sans GPU.

FAT1_URI = 'http://gareus.org/oss/lv2/fat1'   # x42-Autotune : masque de 12 notes (ports m00 à m11, do à si)
# Dossiers LV2 cherchés après $LV2_PATH : ceux de lilv (la bibliothèque LV2 de ffmpeg), et les dossiers multiarch
LV2_STANDARD = ['~/.lv2', '/usr/local/lib/lv2', '/usr/lib/lv2', '/usr/local/lib/*-linux-gnu*/lv2', '/usr/lib/*-linux-gnu*/lv2']
AUTOTUNE_DEFAUT = 70   # --autotune sans valeur
# Bornes déclarées des ports (fat1.ttl) : toujours respectées ; hors bornes, fat1 peut planter ffmpeg (filter 0,005 :
# erreur de segmentation, essai du 04/11/2026)
FAT1_BORNES = {'mode': (0, 2), 'tuning': (400.0, 480.0), 'bias': (0.0, 1.0), 'filter': (0.02, 0.5), 'corr': (0.0, 1.0),
               'offset': (-2.0, 2.0), 'fastmode': (0, 1)}
FAT1_LATENCE = 1056   # échantillons (44,1 et 48 kHz, tous réglages sauf fastmode) ; remesurée à chaque doublage
# Krumhansl-Kessler (1982) : poids des 12 degrés en majeur et en mineur, depuis la tonique
KK_MAJEUR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
KK_MINEUR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
GAMMES = {'majeur': (0, 2, 4, 5, 7, 9, 11), 'mineur': (0, 2, 3, 5, 7, 8, 10)}   # mineur naturel
NOTES_FR = ['do', 'do#', 'ré', 'ré#', 'mi', 'fa', 'fa#', 'sol', 'sol#', 'la', 'la#', 'si']
TONIQUES = {'majeur': ['do', 'réb', 'ré', 'mib', 'mi', 'fa', 'fa#', 'sol', 'lab', 'la', 'sib', 'si'],
            'mineur': ['do', 'do#', 'ré', 'mib', 'mi', 'fa', 'fa#', 'sol', 'sol#', 'la', 'sib', 'si']}
TONIQUES_EN = {'majeur': ['C', 'Db', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B'],
               'mineur': ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'G#', 'A', 'Bb', 'B']}
LETTRES = {'do': 0, 're': 2, 'mi': 4, 'fa': 5, 'sol': 7, 'la': 9, 'si': 11,
           'c': 0, 'd': 2, 'e': 4, 'f': 5, 'g': 7, 'a': 9, 'b': 11}
GAMME_SYNTAXE = re.compile(r'(do|re|mi|fa|sol|la|si|[a-g])(#|b|-?diese|-?bemol)?'
                           r'(?:-?(majeur|maj|major|mineur|min|minor|m))?')
GAMME_AUTO = {'auto', 'automatique', 'detectee'}
GAMME_CHROMATIQUE = {'chromatique', 'chromatic', 'aucune', 'toutes'}
GAMME_AIDE = ('auto (défaut : tonalité détectée sur la musique seule), chromatique (les 12 notes), ou une tonalité : '
              'tonique do ré mi fa sol la si (ou C D E F G A B), suivie de # ou b, puis -majeur ou -mineur '
              '(alias maj, min, major, minor, m ; majeur si omis). Ex. : la-mineur, do#-majeur, sib, Am, C#m, Eb-minor')
# Tonalité retenue si la corrélation au profil dépasse SEUIL_TONALITE et devance d'au moins SEUIL_ECART la meilleure
# tonalité d'une autre gamme (la relative, mêmes notes, ne compte pas) ; sinon, gamme chromatique
SEUIL_TONALITE = 0.6
SEUIL_ECART = 0.05


def borner_ports(ports):
    """Valeurs ramenées dans les bornes déclarées de fat1 (FAT1_BORNES)."""
    out = {}
    for k, v in ports.items():
        if k in FAT1_BORNES:
            lo, hi = FAT1_BORNES[k]
            v = min(max(v, lo), hi)
        out[k] = v
    return out


def ports_autotune(intensite):
    """Intensité (1 à 100) -> réglages de fat1, un dictionnaire par passe ([] à 0). Avec u = N/100 :
      corr   = min(1 ; 0,2 + u)     part de l'écart corrigée ;
      filter = 0,5 x 0,04^u        lissage de la correction tant que la note ne change pas, de 0,5 (lent, garde le
                                   vibrato) à 0,02 (son minimum : suit tout, effet robot) ; à un changement de note, fat1
                                   saute toujours d'un coup ;
      bias   = 1 - u               préférence pour la note en cours (bas : moins de sauts parasites ; haut : accroche
                                   la note la plus proche) ;
      fastmode 0 (le direct ne sert à rien hors temps réel ; la latence est compensée), mode 0 (auto : note la plus
      proche du masque), offset 0. Au-delà de 80, une deuxième passe en série (corr = (N - 80) / 20, autres ports
      identiques) : une passe à filter 0,02 laisse 40 % du vibrato, deux passes 17 % (correction quasi totale à 100).
    Mesures sur une voix de synthèse fausse de 29 cents (médiane), vibrato de 14 cents : README, rapport de la branche."""
    n = max(0, min(100, int(round(float(intensite)))))
    if not n:
        return []
    u = n / 100
    p = {'mode': 0, 'tuning': 440.0, 'bias': round(1 - u, 3), 'filter': round(0.5 * 0.04 ** u, 4),
         'corr': round(min(1.0, 0.2 + u), 3), 'offset': 0.0, 'fastmode': 0}
    passes = [p]
    if n > 80:
        passes.append({**p, 'corr': round((n - 80) / 20, 3)})
    return [borner_ports(q) for q in passes]


def intensite_autotune(valeur):
    """« 70 » -> 70 (entier de 0 à 100) ; oui -> AUTOTUNE_DEFAUT, non -> 0 ; ValueError sinon."""
    if isinstance(valeur, bool):
        return AUTOTUNE_DEFAUT if valeur else 0
    s = str(valeur).strip().lower()
    if s in OUI - {'1'}:
        return AUTOTUNE_DEFAUT
    if s in NON - {'0'}:
        return 0
    try:
        v = float(str(valeur).replace(',', '.'))
    except ValueError:
        raise ValueError(f'intensité de 0 à 100 attendue (reçu : {valeur})') from None
    if not (math.isfinite(v) and 0 <= v <= 100) or v != int(v):
        raise ValueError(f'intensité de 0 à 100 attendue, nombre entier (reçu : {valeur})')
    return int(v)


def _intensite_option(texte):
    try:
        return intensite_autotune(texte)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def autotune_config(conf):
    """Clé « autotune » de la configuration : intensité de 0 (désactivé, ou absente) à 100 ; illisible : 0, avec un avis."""
    v = valeur_config(conf, 'autotune')
    if v is None:
        return 0
    try:
        return intensite_autotune(v)
    except ValueError:
        dire(f'attention : « autotune » de {CONFIG} illisible ({v!r}) : auto-tune désactivé (doubler --config autotune 0-100)')
        return 0


def normaliser_gamme(texte):
    import unicodedata
    s = unicodedata.normalize('NFD', str(texte).strip().lower())
    s = ''.join(c for c in s if not unicodedata.combining(c)).replace('♯', '#').replace('♭', 'b')
    return re.sub(r'-+', '-', re.sub(r'[\s_]+', '-', s)).strip('-')


def lire_gamme(texte):
    """--autotune-gamme -> {'type': 'auto'}, {'type': 'chromatique'} ou {'type': 'tonalite', 'tonique': 0-11 (do = 0),
    'mode': 'majeur'|'mineur'} ; ValueError sinon (syntaxe : GAMME_AIDE). Accents, casse, espaces et ♯ ♭ indifférents."""
    s = normaliser_gamme(texte)
    if s in GAMME_AUTO:
        return {'type': 'auto'}
    if s in GAMME_CHROMATIQUE:
        return {'type': 'chromatique'}
    m = GAMME_SYNTAXE.fullmatch(s)
    if not m:
        raise ValueError(f'gamme illisible : {texte!r} ({GAMME_AIDE})')
    note, alt, mode = m.groups()
    pc = LETTRES[note] + ((1 if '#' in alt or 'diese' in alt else -1) if alt else 0)
    return {'type': 'tonalite', 'tonique': pc % 12,
            'mode': 'mineur' if mode in ('mineur', 'min', 'minor', 'm') else 'majeur'}


def _gamme_option(texte):
    try:
        lire_gamme(texte)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    return texte


def nom_tonalite(tonique, mode):
    """(9, 'mineur') -> ('la mineur', 'Am')."""
    return f'{TONIQUES[mode][tonique]} {mode}', TONIQUES_EN[mode][tonique] + ('m' if mode == 'mineur' else '')


def masque_gamme(tonique, mode):
    """Masque de 12 notes (do à si, 1 = note de la gamme) de fat1."""
    return [1 if (k - tonique) % 12 in GAMMES[mode] else 0 for k in range(12)]


def lire_blocs(fichier, sr, secondes=30.0, filtre=None):
    """Son mono à sr Hz, par blocs de `secondes` (float64), lu en continu par ffmpeg (jamais tout en mémoire)."""
    import numpy as np
    cmd = ['nice', '-n', '10', 'ffmpeg', '-v', 'error', '-i', str(fichier), '-vn', '-ac', '1', '-ar', str(sr),
           *(['-af', filtre] if filtre else []), '-f', 'f32le', '-']
    taille = int(sr * secondes) * 4
    with tempfile.TemporaryFile() as erreurs:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=erreurs)
        fini, reste = False, b''
        try:
            while True:
                b = proc.stdout.read(taille)
                if not b:
                    break
                b = reste + b
                k = len(b) // 4 * 4
                reste = b[k:]
                yield np.frombuffer(b[:k], np.float32).astype(np.float64)
            fini = True
        finally:
            proc.stdout.close()
            if not fini:
                proc.kill()
            code = proc.wait()
        if fini and code:
            erreurs.seek(0)
            raise RuntimeError(f'lecture impossible : {fichier}\n{erreurs.read().decode(errors="replace")[-800:]}')


def chroma_musique(fichier, sr=11025, n_fft=8192, pas=4096, fmin=80.0, fmax=1200.0, fmax_accord=2500.0):
    """Profil des 12 notes (do = 0) de la musique et son accord, lus en continu. Chaque trame (743 ms, toutes les 371 ms ;
    trames de moins de -55 dBFS écartées) : puissance du spectre de 80 Hz à 1,2 kHz (fondamentales des accords et de la
    basse), rapportée au pic de la trame, répartie en 120 cases de 10 cents puis ramenée à une somme de 1 (un passage
    fort ne compte pas plus qu'un faible). La puissance, et non l'amplitude compressée, pour que les harmoniques (quinte,
    tierce majeure au-dessus de chaque note) ne fassent pas prendre un majeur pour le mineur de sa médiante (do majeur
    pris pour mi mineur ; essai du 04/11/2026 sur 7 tonalités de synthèse : 3 sur 7 en log, 7 sur 7 en puissance).
    Accord : écart des pics du spectre (150 Hz-2,5 kHz, à moins de 30 dB du plus fort, position affinée par
    interpolation) aux demi-tons du la 440, en moyenne circulaire pondérée ; les 120 cases sont ensuite regroupées par
    demi-ton autour de cet accord. Rend {chroma (12), accord_cents, accord_fiabilite (0-1), trames}."""
    import numpy as np
    k = np.arange(n_fft // 2 + 1)
    f = k * sr / n_fft
    bande = (f >= fmin) & (f <= fmax)
    fin = np.round((12 * np.log2(f[bande] / 440.0) + 69) * 10).astype(int) % 120
    fenetre = np.hanning(n_fft)
    seuil = 10 ** (-55 / 20)
    chroma_fin, S, W, trames = np.zeros(120), 0j, 0.0, 0
    buf = np.zeros(0)
    for bloc in lire_blocs(fichier, sr):
        buf = np.concatenate([buf, bloc])
        n = (len(buf) - n_fft) // pas + 1 if len(buf) >= n_fft else 0
        if n <= 0:
            continue
        idx = np.arange(n)[:, None] * pas + np.arange(n_fft)[None, :]
        x = buf[idx]
        buf = buf[n * pas:]
        x = x[np.sqrt((x ** 2).mean(1)) > seuil]
        if not len(x):
            continue
        X = np.abs(np.fft.rfft(x * fenetre, axis=1))
        mb = X[:, bande]
        c = (mb / (mb.max(1, keepdims=True) + 1e-12)) ** 2
        for ligne in c:
            cf = np.bincount(fin, weights=ligne, minlength=120)
            chroma_fin += cf / (cf.sum() + 1e-12)
        trames += len(x)
        # pics : maxima locaux à moins de 30 dB du plus fort de la trame (entre 150 Hz et fmax_accord)
        zone = (f >= 150) & (f <= fmax_accord)
        mx = X[:, zone].max(1, keepdims=True) + 1e-12
        L = np.log(X + 1e-12)
        centre = X[:, 1:-1]
        pic = (centre > X[:, :-2]) & (centre >= X[:, 2:]) & (centre > mx * 10 ** (-30 / 20))
        pic &= zone[1:-1][None, :]
        i, j = np.nonzero(pic)
        j = j + 1
        a, b, cc = L[i, j - 1], L[i, j], L[i, j + 1]
        den = a - 2 * b + cc
        d = np.where(np.abs(den) > 1e-12, 0.5 * (a - cc) / np.where(np.abs(den) > 1e-12, den, 1), 0.0)
        fp = (j + np.clip(d, -0.5, 0.5)) * sr / n_fft
        ecart = (1200 * np.log2(fp / 440.0) + 50) % 100 - 50
        w = X[i, j]
        S += (w * np.exp(2j * np.pi * ecart / 100)).sum()
        W += w.sum()
    if not trames:
        return {'chroma': None, 'accord_cents': 0.0, 'accord_fiabilite': 0.0, 'trames': 0}
    cents = float(np.angle(S) / (2 * np.pi) * 100) if W > 0 else 0.0
    fiab = float(abs(S) / W) if W > 0 else 0.0
    decal = int(round(cents / 10))
    chroma = np.array([sum(chroma_fin[(10 * n + decal + j) % 120] for j in range(-5, 5)) for n in range(12)]) / trames
    return {'chroma': chroma, 'accord_cents': cents, 'accord_fiabilite': fiab, 'trames': trames}


def tonalite_de(chroma):
    """Corrélation (Pearson) du profil des 12 notes aux 24 tonalités de Krumhansl-Kessler. Rend la meilleure tonalité,
    sa corrélation, l'écart à la meilleure tonalité d'une autre gamme (la relative, mêmes 7 notes, ne compte pas : la
    gamme donnée à fat1 est la même) et les trois premières."""
    import numpy as np
    c = np.asarray(chroma, float)
    cands = []
    for mode, prof in (('majeur', KK_MAJEUR), ('mineur', KK_MINEUR)):
        for t in range(12):
            r = float(np.corrcoef(c, np.roll(prof, t))[0, 1]) if c.std() > 0 else 0.0
            cands.append((r if math.isfinite(r) else 0.0, t, mode))
    cands.sort(key=lambda x: -x[0])
    r1, t1, m1 = cands[0]
    notes1 = {(t1 + i) % 12 for i in GAMMES[m1]}
    autre = next((r for r, t, m in cands[1:] if {(t + i) % 12 for i in GAMMES[m]} != notes1), 0.0)
    return {'tonique': t1, 'mode': m1, 'correlation': round(r1, 3), 'ecart': round(r1 - autre, 3),
            'candidates': [[nom_tonalite(t, m)[0], round(r, 3)] for r, t, m in cands[:3]]}


def choisir_gamme(texte, musique):
    """Gamme et accord donnés à fat1. Accord (port tuning) : celui de la musique seule s'il est fiable, sinon 440 Hz.
    auto : tonalité détectée sur la musique seule, plus les notes hors gamme aussi présentes que la plus faible de la
    gamme et qu'un quart de la plus forte (sensible d'un mineur harmonique, emprunts, tonalité voisine hésitante ; deux
    au plus : une note de trop corrige un peu moins, une note qui manque fausse le chant), et repli chromatique si la
    détection est peu sûre ;
    chromatique : les 12 notes ; une tonalité : exactement ses 7 notes."""
    demande = lire_gamme(texte or 'auto')
    try:
        ch = chroma_musique(musique)
    except Exception as e:  # noqa: BLE001 — sans musique lisible : accord 440, gamme chromatique en auto
        dire(f'attention : musique seule illisible pour la tonalité ({e})')
        ch = {'chroma': None, 'accord_cents': 0.0, 'accord_fiabilite': 0.0, 'trames': 0}
    fiable = ch['accord_fiabilite'] >= 0.2 and ch['trames'] >= 5
    cents = round(ch['accord_cents'], 1) if fiable else 0.0
    accord = round(min(max(440.0 * 2 ** (cents / 1200), FAT1_BORNES['tuning'][0]), FAT1_BORNES['tuning'][1]), 2)
    g = {'demandee': texte or 'auto', 'accord_hz': accord, 'accord_cents': cents,
         'accord_fiabilite': round(ch['accord_fiabilite'], 3), 'repli': None, 'notes_ajoutees': []}
    det = None
    if ch['chroma'] is not None:
        det = tonalite_de(ch['chroma'])
        det['tonalite'] = nom_tonalite(det['tonique'], det['mode'])[0]
        det['sure'] = det['correlation'] >= SEUIL_TONALITE and det['ecart'] >= SEUIL_ECART
        det['profil'] = [round(float(v) / float(max(ch['chroma'])), 3) for v in ch['chroma']]
    g['detection'] = det
    if demande['type'] == 'tonalite':
        t, m = demande['tonique'], demande['mode']
    elif demande['type'] == 'auto' and det and det['sure']:
        t, m = det['tonique'], det['mode']
    else:
        if demande['type'] == 'auto':
            g['repli'] = ('musique trop faible pour trouver la tonalité' if det is None else
                          f"tonalité peu sûre ({det['tonalite']} : corrélation {det['correlation']:.2f}, écart "
                          f"{det['ecart']:.2f} ; il faut {SEUIL_TONALITE} et {SEUIL_ECART}) : gamme chromatique")
        masque = [1] * 12
        return {**g, 'type': 'chromatique', 'tonalite': None, 'tonalite_en': None, 'tonique': None, 'mode': None,
                'masque': masque, 'notes': list(NOTES_FR)}
    masque = masque_gamme(t, m)
    if demande['type'] == 'auto':
        chroma = ch['chroma']
        plancher = max(min(chroma[k] for k in range(12) if masque[k]), 0.25 * max(chroma))
        ajout = sorted((k for k in range(12) if not masque[k] and chroma[k] >= plancher), key=lambda k: -chroma[k])[:2]
        for k in ajout:
            masque[k] = 1
        g['notes_ajoutees'] = [NOTES_FR[k] for k in sorted(ajout)]
    fr, en = nom_tonalite(t, m)
    return {**g, 'type': 'tonalite', 'tonalite': fr, 'tonalite_en': en, 'tonique': t, 'mode': m, 'masque': masque,
            'notes': [NOTES_FR[k] for k in range(12) if masque[k]]}


def trouver_fat1():
    """Dossier LV2 qui contient x42-autotune (fat1), cherché dans $LV2_PATH puis dans LV2_STANDARD ; None s'il manque."""
    import glob
    dossiers = [d for d in os.environ.get('LV2_PATH', '').split(':') if d]
    for motif in LV2_STANDARD:
        dossiers += sorted(glob.glob(os.path.expanduser(motif)))
    for d in dossiers:
        d = Path(d).expanduser()
        if (d / 'fat1.lv2' / 'manifest.ttl').is_file():
            return d
        for m in sorted(d.glob('*.lv2/manifest.ttl')) if d.is_dir() else ():
            try:
                if f'<{FAT1_URI}>' in m.read_text(errors='replace'):
                    return d
            except OSError:
                continue
    return None


def ffmpeg_lv2():
    """ffmpeg a-t-il le filtre lv2 (compilé avec lilv) ?"""
    r = subprocess.run(['ffmpeg', '-hide_banner', '-filters'], capture_output=True, text=True)
    return bool(re.search(r'^\s*\S+\s+lv2\s', r.stdout, re.M))


def filtre_fat1(ports, masque):
    """Filtre lv2 de ffmpeg pour une passe de fat1 (URI et valeurs entre apostrophes : ni « : » ni « | » à échapper)."""
    c = '|'.join([f'{k}={v:.6g}' for k, v in ports.items()] + [f'm{k:02d}={int(v)}' for k, v in enumerate(masque)])
    return f"lv2=p='{FAT1_URI.replace(':', chr(92) + ':')}':c='{c}'"


_LATENCES = {}


def latence_fat1(dossier_lv2, sr=44100):
    """Latence de fat1 en échantillons, mesurée sur 0,6 s de bruit (correction nulle : sortie = entrée retardée), par
    corrélation ; FAT1_LATENCE si la mesure n'est pas nette. Une fois par dossier LV2."""
    import numpy as np
    cle = (str(dossier_lv2), sr)
    if cle in _LATENCES:
        return _LATENCES[cle]
    lat = FAT1_LATENCE
    try:
        x = np.random.default_rng(1056).standard_normal(int(0.6 * sr)).astype(np.float32) * 0.05
        x[:sr // 10] = 0
        r = subprocess.run(['nice', '-n', '10', 'ffmpeg', '-v', 'error', '-f', 'f32le', '-ar', str(sr), '-ac', '1', '-i', '-',
                            '-af', filtre_fat1({'mode': 0, 'corr': 0.0, 'fastmode': 0}, [1] * 12), '-f', 'f32le', '-'],
                           input=x.tobytes(), capture_output=True, env={**os.environ, 'LV2_PATH': str(dossier_lv2)},
                           timeout=60)
        y = np.frombuffer(r.stdout, np.float32).astype(np.float64)
        if not r.returncode and len(y) >= len(x):
            a = x[sr // 10:].astype(np.float64)
            c = np.correlate(y[sr // 10:], a[:len(a) - 4096], 'valid')
            k = int(np.argmax(np.abs(c)))
            if np.abs(c[k]) > 0.3 * (a[:len(a) - 4096] ** 2).sum():
                lat = k
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    _LATENCES[cle] = lat
    return lat


def appliquer_fat1(entree, sortie, passes, masque, dossier_lv2, latence):
    """entree (voix sèche stéréo, deux canaux identiques) -> sortie auto-tunée, même longueur à l'échantillon près et
    calée dessus : silence ajouté à la fin (apad), passes de fat1 en série (mono), début retiré de la latence totale
    (atrim), puis remise en stéréo."""
    total = latence * len(passes)
    chaine = ','.join(['pan=mono|c0=0.5*c0+0.5*c1', f'apad=pad_len={total}', *(filtre_fat1(p, masque) for p in passes),
                       f'atrim=start_sample={total}', 'asetpts=PTS-STARTPTS', 'pan=stereo|c0=c0|c1=c0'])
    ff('-i', entree, '-af', chaine, '-c:a', 'pcm_f32le', sortie, env={**os.environ, 'LV2_PATH': str(dossier_lv2)})


def f0_fichier(fichier, sr=16000, fmin=60, fmax=1100, seuil=0.15):
    """Hauteur YIN (Hz, 0 = non voisé) et niveau (dBFS) toutes les 10 ms, trame i centrée sur i x 10 ms (comme les
    courbes du noyau), lu en continu : len(son) // 160 trames."""
    import numpy as np
    W, H, tmax = 640, 160, int(sr / fmin)
    buf, n_son, f0s, niv = np.zeros(W // 2), 0, [], []
    for bloc in lire_blocs(fichier, sr):
        n_son += len(bloc)
        buf = np.concatenate([buf, bloc])
        n = (len(buf) - W - tmax) // H
        if n > 0:
            f, r = yin(buf[:n * H + W + tmax], sr, W, H, fmin, fmax, seuil)
            f0s.append(f)
            niv.append(r)
            buf = buf[n * H:]
    reste = n_son // H - sum(len(f) for f in f0s)
    if reste > 0:
        buf = np.concatenate([buf, np.zeros(reste * H + W + tmax)])
        f, r = yin(buf[:reste * H + W + tmax], sr, W, H, fmin, fmax, seuil)
        f0s.append(f)
        niv.append(r)
    f0 = np.concatenate(f0s)[:n_son // H] if f0s else np.zeros(0)
    return f0, (np.concatenate(niv)[:n_son // H] if niv else np.zeros(0))


def ecarts_gamme(f0, masque, accord_hz):
    """Écart (cents, 0 à 600) de chaque hauteur à la note du masque la plus proche, dans l'accord donné."""
    import numpy as np
    notes = np.array([k for k in range(12) if masque[k]], float)
    pc = (12 * np.log2(np.asarray(f0, float) / accord_hz) + 69) % 12
    d = np.abs(pc[:, None] - notes[None, :])
    return np.minimum(d, 12 - d).min(1) * 100


def mesurer_autotune(seche, seche_at, gamme):
    """Justesse de la voix sèche avant et après l'auto-tune : écart aux notes de la gamme (médiane, 90e centile, part
    à 10 cents près) sur les trames voisées des deux (YIN ; niveau à moins de 30 dB du haut de la voix, et au-dessus de
    -50 dBFS). Rend (mesures, hauteurs avant, hauteurs après) ; les hauteurs servent aussi à courbes.json."""
    import numpy as np
    fa, na = f0_fichier(seche)
    fb, nb = f0_fichier(seche_at)
    n = min(len(fa), len(fb))
    fa, na, fb, nb = fa[:n], na[:n], fb[:n], nb[:n]

    def voise(f, niv):
        haut = float(np.percentile(niv, 95)) if len(niv) else -120.0
        return (f > 0) & (niv > max(-50.0, haut - 30))
    commun = voise(fa, na) & voise(fb, nb)
    mes = {'trames_voisees': int(commun.sum()), 'secondes_voisees': round(float(commun.sum()) * 0.01, 1),
           'accord_hz': gamme['accord_hz'], 'gamme': gamme['tonalite'] or 'chromatique'}
    for nom, f in (('avant', fa), ('apres', fb)):
        e = ecarts_gamme(f[commun], gamme['masque'], gamme['accord_hz']) if commun.any() else np.zeros(0)
        mes[nom] = ({'ecart_median_cents': round(float(np.median(e)), 1),
                     'ecart_p90_cents': round(float(np.percentile(e, 90)), 1),
                     'part_a_10_cents': round(float((e < 10).mean()), 3)} if len(e) else None)
    return mes, fa, fb


def courbe_autotune(tv, fa, fb):
    """Ajoute autotune_hz à courbes.json (format 1 gardé ; clé ajoutée en dernier, les autres inchangées) : la mélodie
    convertie (convertie_hz, RMVPE du noyau) décalée, trame par trame (20 ms), de la correction mesurée (rapport des
    hauteurs YIN de la voix auto-tunée et de la voix sèche ; interpolée là où YIN ne trouve rien ; au-delà de 1,5
    demi-ton, erreur d'octave de YIN, écartée). 0 là où convertie_hz vaut 0. Rend le nombre de trames, ou None si
    courbes.json manque."""
    import numpy as np
    chemin = tv / 'courbes.json'
    try:
        d = json.loads(chemin.read_text())
        conv = np.asarray(d['convertie_hz'], float)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    pas = max(1, int(round(float(d.get('pas_s') or 0.02) / 0.01)))
    a, b = fa[::pas], fb[::pas]
    n = min(len(a), len(b))
    c = np.full(len(conv), np.nan)
    with np.errstate(divide='ignore', invalid='ignore'):
        r = 1200 * np.log2(b[:n] / a[:n])
    ok = (a[:n] > 0) & (b[:n] > 0) & np.isfinite(r) & (np.abs(r) <= 150)
    k = min(n, len(conv))
    c[:k] = np.where(ok[:k], r[:k], np.nan)
    v = np.nonzero(np.isfinite(c))[0]
    c = np.interp(np.arange(len(conv)), v, c[v]) if len(v) else np.zeros(len(conv))
    d['autotune_hz'] = [round(float(f * 2 ** (x / 1200)), 1) if f > 1 else 0 for f, x in zip(conv, c)]
    tmp = chemin.with_name(chemin.name + '.tmp')
    tmp.write_text(json.dumps(d, separators=(',', ':')))
    os.replace(tmp, chemin)
    return len(conv)


def retirer_courbe_autotune(tv):
    chemin = tv / 'courbes.json'
    try:
        d = json.loads(chemin.read_text())
    except (OSError, ValueError):
        return
    if isinstance(d, dict) and 'autotune_hz' in d:
        del d['autotune_hz']
        tmp = chemin.with_name(chemin.name + '.tmp')
        tmp.write_text(json.dumps(d, separators=(',', ':')))
        os.replace(tmp, chemin)


FICHIERS_AUTOTUNE_TRAVAIL = ('mix_autotune.wav', 'seche_voix_autotune.wav', 'reverb_voix_autotune.wav')


def base_autotune(base):
    return f'{base} (auto-tune)'


def corbeille(fichiers):
    """Fichiers mis à la corbeille du bureau (gio trash : récupérables), sinon effacés. Rend (à la corbeille, effacés)."""
    fichiers = [Path(f) for f in fichiers if Path(f).exists()]
    if fichiers and shutil.which('gio'):
        subprocess.run(['gio', 'trash', '--', *map(str, fichiers)], capture_output=True, text=True)
    jetes = [f for f in fichiers if not f.exists()]
    effaces = []
    for f in fichiers:
        if f.exists():
            f.unlink(missing_ok=True)
            effaces.append(f)
    return jetes, effaces


def retirer_autotune(tv, dossier, base):
    """--sans-autotune : la version auto-tune retirée. Vidéo, mp3 et .lrc « (auto-tune) » à la corbeille (récupérables ;
    effacés s'il n'y a pas de corbeille), fichiers de travail effacés (refaits par --remix --autotune N), autotune_hz
    retiré de courbes.json. Rend la section autotune du rapport."""
    b = base_autotune(base)
    sorties = [dossier / f'{b}{ext}' for ext in (*CONTENEURS, '.mp3', '.lrc')]
    jetes, effaces = corbeille(sorties)
    travail = [tv / n for n in FICHIERS_AUTOTUNE_TRAVAIL if (tv / n).exists()]
    for f in travail:
        f.unlink(missing_ok=True)
    retirer_courbe_autotune(tv)
    if jetes or effaces or travail:
        dire('version auto-tune retirée : ' + ', '.join(
            [f'{f.name} (corbeille)' for f in jetes] + [f'{f.name} (effacé, pas de corbeille)' for f in effaces]
            + [f'travail/{tv.name}/{f.name} (effacé)' for f in travail]))
    else:
        dire('--sans-autotune : pas de version auto-tune à retirer')
    return {'applique': False, 'intensite': 0, 'source': 'option --sans-autotune',
            'raison': 'version auto-tune retirée (--sans-autotune)',
            'retires': {'corbeille': [f.name for f in jetes], 'effaces': [f.name for f in effaces],
                        'travail': [f.name for f in travail]}}


def reglage_autotune(a, conf):
    """Intensité de l'auto-tune demandée : --sans-autotune l'emporte, puis --autotune [N], puis la clé « autotune » de la
    configuration (0 ou absente : désactivé). Rend {intensite, source, explicite, gamme, gamme_donnee, retirer}."""
    gamme = getattr(a, 'autotune_gamme', None)
    r = {'gamme': gamme or 'auto', 'gamme_donnee': gamme is not None, 'retirer': False, 'explicite': False}
    if getattr(a, 'sans_autotune', False):
        return {**r, 'intensite': 0, 'source': 'option --sans-autotune', 'retirer': True}
    if getattr(a, 'autotune', None) is not None:
        return {**r, 'intensite': int(a.autotune), 'source': 'option --autotune', 'explicite': True}
    return {**r, 'intensite': autotune_config(conf), 'source': 'configuration (clé autotune)'}


def reglage_pour(a, rapport):
    """Réglage de l'auto-tune pour ce doublage. Sans --autotune ni --sans-autotune, un doublage qui a déjà sa version
    auto-tune la garde à jour (--remix) : même intensité, même gamme demandée (sauf --autotune-gamme)."""
    r = getattr(a, 'autotune_reglage', None) or {'intensite': 0, 'source': 'défaut', 'explicite': False,
                                                 'gamme': 'auto', 'gamme_donnee': False, 'retirer': False}
    prec = rapport.get('autotune') if isinstance(rapport.get('autotune'), dict) else {}
    if r['retirer'] or r['explicite'] or not prec.get('applique') or not prec.get('intensite'):
        return r
    gamme = r['gamme'] if r['gamme_donnee'] else ((prec.get('gamme') or {}).get('demandee') or 'auto')
    try:
        lire_gamme(gamme)
    except ValueError:
        gamme = 'auto'
    return {**r, 'intensite': int(prec['intensite']), 'source': 'doublage précédent (version auto-tune gardée à jour)',
            'explicite': bool(prec.get('explicite')), 'gamme': gamme}


def faire_autotune(t, tv, dossier, base, mode, gains, reglage):
    """Version auto-tune, après le mix normal : voix sèche passée dans fat1, reverb, mix aux gains du mix normal, mp3.
    N'échoue jamais : plugin absent, parole sans demande explicite ou erreur -> version normale seule, avec un message.
    Rend la section autotune du rapport ('applique' vrai si la version existe)."""
    n = reglage['intensite']
    info = {'applique': False, 'intensite': n, 'source': reglage['source'], 'explicite': reglage['explicite'],
            'moteur': 'x42-autotune (fat1, LV2)'}
    t0 = time.time()
    if mode == 'parole' and not reglage['explicite']:
        dire(f"auto-tune non appliqué : c'est de la parole ({reglage['source']}) ; --autotune {n} l'impose")
        return {**info, 'raison': 'parole : auto-tune seulement sur demande explicite (--autotune N)'}
    if mode == 'parole':
        dire(f'attention : auto-tune {n} sur de la parole, comme demandé : effet marqué (voix chantée artificielle)')
    plugin = trouver_fat1()
    if plugin is None or not ffmpeg_lv2():
        raison = ('x42-autotune absent : installez x42-plugins : sudo apt install x42-plugins' if plugin is None
                  else 'ffmpeg sans le filtre lv2 (compilé sans lilv)')
        dire(f'ATTENTION : auto-tune demandé, mais {raison} ; version normale seule')
        return {**info, 'raison': raison}
    seche, seche_at = tv / 'seche_voix_convertie.wav', tv / 'seche_voix_autotune.wav'
    b = base_autotune(base)
    mp3 = dossier / f'{b}.mp3'
    try:
        gamme = choisir_gamme(reglage['gamme'], t / 'instrumental.wav')
        if gamme['repli']:
            dire(f"auto-tune : {gamme['repli']}")
        passes = [{**p, 'tuning': gamme['accord_hz']} for p in ports_autotune(n)]
        latence = latence_fat1(plugin)
        appliquer_fat1(seche, seche_at, passes, gamme['masque'], plugin, latence)
        rev = None
        if 'reverb' in gains:
            rev = reverb(tv, seche_at, 'reverb_voix_autotune.wav', duree_s(t / 'instrumental.wav'))
        else:
            (tv / 'reverb_voix_autotune.wav').unlink(missing_ok=True)
        assembler(t, seche_at, rev, gains, tv / 'mix_autotune.wav')
        ff('-i', tv / 'mix_autotune.wav', '-c:a', 'libmp3lame', '-b:a', '320k', mp3)
    except Exception as e:  # noqa: BLE001 — l'auto-tune ne fait jamais échouer le doublage
        for f in FICHIERS_AUTOTUNE_TRAVAIL:
            (tv / f).unlink(missing_ok=True)
        dire(f'ATTENTION : auto-tune impossible ({str(e).strip().splitlines()[0][:300]}) ; version normale seule')
        return {**info, 'raison': f'erreur : {str(e).strip()[:500]}'}
    info.update({'applique': True, 'gamme': gamme, 'passes': passes, 'latence_echantillons': latence,
                 'plugin': {'uri': FAT1_URI, 'dossier_lv2': str(plugin)},
                 'gains_db': {k: round(v, 2) for k, v in gains.items()},
                 'fichiers': {'mp3': mp3.name, 'mix': 'mix_autotune.wav', 'seche': seche_at.name,
                              'reverb': rev.name if rev else None}})
    try:
        mes, fa, fb = mesurer_autotune(seche, seche_at, gamme)
        info['mesures'] = mes
        trames = courbe_autotune(tv, fa, fb)
        info['courbe'] = {'fichier': 'courbes.json', 'cle': 'autotune_hz', 'trames': trames} if trames else None
    except Exception as e:  # noqa: BLE001 — mesures pour le rapport seulement
        info['mesures'] = {'erreur': str(e)[:300]}
    info['temps_s'] = round(time.time() - t0, 1)
    return info


RECUL_FFMPEG_S = 3 / 23   # ffmpeg recule de 3/23 s avant de chercher (formats sans recherche par pts, images B : mkv, webm)
# Conteneurs de sortie sans liste d'édition : rien n'y commence avant 0, le muxeur avance tous les temps (image, son,
# sous-titres) d'autant que la première image copiée d'un extrait est avant la coupe. En mp4, m4v et mov, une liste
# d'édition garde la coupe pour temps 0.
RECALES_SUR_L_IMAGE = ('.mkv', '.webm')


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
    d'image sans son sur un épisode de série) : on cherche alors 0,1315 s plus loin. Rend (-ss, instant de la première
    image copiée depuis le début du fichier, None si ffmpeg ne l'a pas dit)."""
    p = premiere_image_copiee(video, info, fv, debut)
    if p is None or p >= debut - 0.02:
        return debut, p
    ss = debut + RECUL_FFMPEG_S + 0.001
    p2 = premiere_image_copiee(video, info, fv, ss)
    if p2 is not None and abs(p2 - (debut - 0.001)) < 0.02:
        return ss, p2
    dire(f'attention : la copie de l\'image commencera à {p:.3f} s au lieu de {debut:.3f} s (image sans son au début)')
    return debut, p


def plan_video(t):
    """Où et comment le mix sera remis sur la vidéo : conteneur de sortie, coupe de l'extrait (-ss/-t), décalage du mix
    donné à ffmpeg (decalage_son : -itsoffset, compté depuis la coupe ; 0 le plus souvent, -0,13 s pour un extrait mkv
    ou webm, le retard du flux audio d'origine pour une vidéo entière) et instant de la vidéo doublée où le mix commence
    vraiment (debut_son : celui des paroles, de paroles.json et des .srt).

    Les deux diffèrent pour un extrait mkv ou webm (RECALES_SUR_L_IMAGE) : le conteneur recale tous les temps sur la
    première image copiée, le mix commence donc avec elle (debut_son 0, pas -0,13 s), ou plus tard si la copie part d'une
    image clé antérieure à celle de l'extrait (debut - 0,001 s ; repli d'ajuster_coupe, image sans son au début). En mp4,
    m4v et mov, et pour une vidéo entière, ils sont égaux."""
    src = json.loads((t / 'source.json').read_text())
    video = Path(src['video'])
    info = sonder(video)
    ext = video.suffix.lower()
    if ext not in CONTENEURS:
        ext = '.mkv'
    fv = flux_video(info)
    coupe, decalage_son = [], float(src.get('decalage_son') or 0)
    debut_son = decalage_son
    if 'debut' in src:
        ss, image = ajuster_coupe(video, info, fv, src['debut'])
        # la vidéo commence à ss, le son du mix à debut : le mix est décalé d'autant (négatif si ss > debut)
        coupe = ['-ss', f'{ss:.6f}', '-t', f"{src['debut'] + src['duree'] - ss:.6f}"]
        decalage_son += src['debut'] - ss
        debut_son = decalage_son
        if ext in RECALES_SUR_L_IMAGE and image is not None:
            debut_son = max(0.0, round(src['debut'] - 0.001 - image, 3))   # avance de la copie sur l'image clé (au ms)
    return {'src': src, 'video': video, 'info': info, 'ext': ext, 'fv': fv, 'coupe': coupe, 'decalage_son': decalage_son,
            'debut_son': debut_son}


SOUS_TITRES_NOUVEAUX = {'.mp4': 'mov_text', '.m4v': 'mov_text', '.mov': 'mov_text', '.mkv': 'srt', '.webm': 'webvtt'}
CONTENEURS_MP4 = ('.mp4', '.m4v', '.mov')   # titre des pistes lu dans handler_name (title n'y est pas gardé)


def echantillons(fichier):
    """(nombre d'échantillons, fréquence) du premier flux audio d'un fichier (exact pour un WAV)."""
    d = json.loads(lancer(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
                           'stream=duration_ts,sample_rate', '-of', 'json', str(fichier)]))
    s = (d.get('streams') or [{}])[0]
    return int(s.get('duration_ts') or 0), int(s.get('sample_rate') or 0)


def remettre_sur_video(t, mix, dossier, base, sous_titres=(), plan=None, pistes=(), titre=None, langue=None):
    """Le mix remis sur la vidéo : image copiée sans réencodage, sous-titres d'origine gardés (ceux que le conteneur
    accepte tels quels), puis les pistes de sous_titres ajoutées ([{fichier (srt, au temps de la coupe, celui du
    décalage du mix decalage_son : le conteneur les recale avec l'image et le son), langue (ISO 639-2), titre}] :
    mov_text en mp4, srt en mkv, webvtt en webm), sans être affichées d'office.
    Le mix est la première piste audio et la seule par défaut (l'interface et les lecteurs prennent la piste 0) ; titre :
    son titre (défaut « voix <Nom> », d'après base). pistes : pistes audio en plus, après lui, dans l'ordre ([{fichier,
    titre}], sons de l'extrait comme le mix), calées exactement comme lui (même -itsoffset, même coupe de l'image) et
    ramenées à sa durée, à l'échantillon près. Toutes : codec de CONTENEURS, étiquette de langue langue (ISO 639-2/B ;
    défaut : celle de la source, quand elle est connue), titre aussi dans handler_name en mp4 ; en mp4, ffmpeg les met
    dans le même alternate_group (une seule jouée)."""
    p = plan or plan_video(t)
    src, video, info, ext, fv = p['src'], p['video'], p['info'], p['ext'], p['fv']
    audio, options, acceptes = CONTENEURS[ext]
    sortie = dossier / f'{base}{ext}'
    langue = langue or src.get('langue')
    decalage = ['-itsoffset', f"{p['decalage_son']:.6f}"] if abs(p['decalage_son']) > 1e-6 else []
    sons = [{'fichier': mix, 'titre': titre or base.split(' - ', 1)[-1]}, *pistes]
    entrees, cartes = [], ['-map', f"0:{fv['index']}"]
    for i, s in enumerate(sons):
        entrees += [*decalage, '-i', s['fichier']]
        cartes += ['-map', f'{1 + i}:a:0']
    gardes, jetes = [], []
    for s in flux(info, 'subtitle'):
        (gardes if s.get('codec_name') in acceptes else jetes).append(s)
    for s in gardes:
        cartes += ['-map', f"0:{s['index']}"]
    codecs = []
    for j, st in enumerate(sous_titres):
        k = len(gardes) + j   # index de la piste parmi les sous-titres de sortie
        entrees += ['-i', st['fichier']]
        cartes += ['-map', f'{1 + len(sons) + j}:0']
        codecs += [f'-c:s:{k}', SOUS_TITRES_NOUVEAUX.get(ext, 'srt'), f'-metadata:s:s:{k}', f"language={st['langue']}",
                   f'-metadata:s:s:{k}', f"title={st['titre']}", f'-disposition:s:{k}', '0']
        if ext in CONTENEURS_MP4:
            codecs += [f'-metadata:s:s:{k}', f"handler_name={st['titre']}"]
    if ext == '.mkv':
        cartes += ['-map', '0:t?']   # polices jointes des sous-titres
        if sous_titres:
            options = [*options, '-default_mode', 'infer_no_subs']   # les paroles ne s'affichent pas d'office
    n, sr = echantillons(mix) if pistes else (0, 0)
    meta = []
    for k, s in enumerate(sons):
        meta += [f'-metadata:s:a:{k}', f"title={s['titre']}", f'-disposition:a:{k}', 'default' if k == 0 else '0']
        if langue:
            meta += [f'-metadata:s:a:{k}', f'language={langue}']
        if ext in CONTENEURS_MP4:
            meta += [f'-metadata:s:a:{k}', f"handler_name={s['titre']}"]
        if k and n and sr:   # même durée que le mix : complétée de silence ou coupée à la fin, jamais décalée
            meta += [f'-filter:a:{k}', f'aresample={sr},apad=whole_len={n},atrim=end_sample={n}']
    ff(*p['coupe'], '-i', video, *entrees, *cartes, '-c:v', 'copy', '-c:s', 'copy', *codecs,
       '-c:t', 'copy', *audio, *meta, *options, sortie)
    if jetes:
        dire(f"sous-titres non copiés ({', '.join(sorted({s.get('codec_name', '?') for s in jetes}))}) : le conteneur "
             f'{ext} ne les accepte pas tels quels')
    return sortie


def titres_pistes(voix, autotune=False):
    """Titres des pistes audio de la vidéo doublée, d'après la voix (« Jules », ou « Jules et Marie » avec une voix par
    chanteur) : Doublage (voix Jules), Original, Beat seul, Voix convertie seule (Jules), Voix d'origine seule."""
    nom, at = voix['affichage'], ' auto-tune' if autotune else ''
    convertie = 'Voix converties seules' if len(voix_membres(voix)) > 1 else 'Voix convertie seule'
    return {'doublage': f'Doublage{at} (voix {nom})', 'original': 'Original', 'beat': 'Beat seul',
            'voix': f'{convertie}{at} ({nom})', 'voix_origine': "Voix d'origine seule"}


def pistes_en_plus(t, tv, voix, gains, autotune=False):
    """Pistes audio de la vidéo doublée après le doublage : original (travail/original.wav), beat seul
    (travail/instrumental.wav, celui de ce mixage), voix convertie seule, voix d'origine seule (travail/voix_source.wav).
    La voix convertie seule est celle du mix, sans la musique : assembler() avec la voix sèche et la reverb de ce mixage
    (de la version auto-tune si autotune), aux mêmes gains, au même limiteur ; écrite dans travail/<voix>/.voix_seule*.wav,
    effacé après la vidéo. Sans elle (erreur), les trois autres pistes restent."""
    titres = titres_pistes(voix, autotune)
    nom = 'autotune' if autotune else 'convertie'
    rev = tv / f'reverb_voix_{nom}.wav' if 'reverb' in gains else None
    seule = tv / f".voix_seule{'_autotune' if autotune else ''}.wav"
    try:
        assembler(t, tv / f'seche_voix_{nom}.wav', rev, gains, seule, musique=False)
        voix_seule = [{'fichier': seule, 'titre': titres['voix']}]
    except Exception as e:  # noqa: BLE001 — une piste en moins, jamais de vidéo en moins
        seule.unlink(missing_ok=True)
        dire(f"ATTENTION : {titres['voix'].lower()} impossible ({str(e).strip().splitlines()[-1][:200]}) : "
             'vidéo sans cette piste')
        voix_seule = []
    return [{'fichier': t / 'original.wav', 'titre': titres['original']},
            {'fichier': t / 'instrumental.wav', 'titre': titres['beat']}, *voix_seule,
            {'fichier': t / 'voix_source.wav', 'titre': titres['voix_origine']}]


def video_avec_repli(t, mix, dossier, base, pistes_st, pistes_a, plan, titre, langue=None):
    """remettre_sur_video (langue : étiquette des pistes audio) ; refusée par ffmpeg, elle est refaite sans les pistes
    audio en plus, puis aussi sans les pistes de paroles : la vidéo doublée passe avant elles. Rend (vidéo, pistes de
    paroles gardées, pistes audio en plus gardées)."""
    essais = [(pistes_st, pistes_a)] + [(pistes_st, [])] * bool(pistes_a) + [([], [])] * bool(pistes_st)
    for i, (st, pa) in enumerate(essais):
        try:
            return remettre_sur_video(t, mix, dossier, base, st, plan, pa, titre, langue), st, pa
        except Exception as e:  # noqa: BLE001
            if i == len(essais) - 1:
                raise
            dire(f"ATTENTION : {'pistes audio en plus' if pa else 'pistes de paroles'} refusées par ffmpeg "
                 f'({str(e).strip().splitlines()[-1][:200]}) : vidéo sans elles')


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


def langue_chantee(d):
    """Langue principale de paroles lues (lire_paroles) : celle chantée le plus longtemps, en additionnant la durée de
    ses lignes (fin - début, 0,01 s au moins), comme le noyau pour la langue de paroles.json. Une ligne sans langue
    compte pour celle du fichier ; à égalité, la langue du fichier l'emporte, sinon la première chantée. Rend (code
    Whisper ou None, {code: secondes chantées})."""
    secondes = {}
    for s in d.get('segments') or []:
        lg = s.get('langue') if isinstance(s.get('langue'), str) and s.get('langue') else d.get('langue')
        if lg:
            secondes[lg] = secondes.get(lg, 0.0) + max(s['fin'] - s['debut'], 0.01)
    secondes = {lg: round(x, 2) for lg, x in secondes.items()}
    if not secondes:
        return None, {}
    egales = [lg for lg, x in secondes.items() if x == max(secondes.values())]
    return (d['langue'] if d.get('langue') in egales else egales[0]), secondes


def langue_pistes_audio(chantee, info_paroles, src):
    """Étiquette de langue des pistes audio de la vidéo doublée, la même pour toutes (Original et Voix d'origine seule
    comprises : la langue chantée est la même) : la langue principale des paroles reconnues (chantee : {langue,
    secondes_par_langue} de preparer_paroles ; None sans paroles), sinon celle du son de la source (source.json : src),
    sinon aucune. info_paroles : demande (paroles demandées), raison (pourquoi il n'y en a pas). Rend le résumé de
    rapport.json (pistes_audio.langue) : etiquette (ISO 639-2/B, ou None), provenance (paroles, source ou None),
    source ; avec les paroles : paroles (code Whisper), nom, secondes_par_langue ; sinon raison."""
    source = src.get('langue') or None
    code = (chantee or {}).get('langue')
    if code in LANGUES:
        etiquette, nom = LANGUES[code]
        return {'etiquette': etiquette, 'provenance': 'paroles', 'paroles': code, 'nom': nom,
                'secondes_par_langue': chantee.get('secondes_par_langue') or {}, 'source': source}
    if not info_paroles.get('demande'):
        raison = 'sans paroles'
    elif code:
        raison = f'langue des paroles inconnue ({code})'
    elif chantee:
        raison = 'langue des paroles non indiquée'
    else:
        raison = info_paroles.get('raison') or 'pas de paroles'
    return {'etiquette': source, 'provenance': 'source' if source else None, 'source': source, 'raison': raison}


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
    """Temps des paroles décalés de decalage s : le noyau les donne en temps du son doublé (decalage_s 0) ; pour
    paroles.json et les .srt, decalage est l'instant de la vidéo doublée où commence le mix (plan_video : debut_son).
    Idempotent (--remix) : decalage_s garde le décalage déjà appliqué, un ancien calage est donc corrigé. Ce qui
    tomberait avant 0 est rogné ou retiré."""
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


PISTES_PAROLES_TEMP = '.paroles_video_*.srt'   # dans travail/<voix>/ : pistes de paroles au temps de la coupe


def preparer_paroles(tv, dossier, base, decalage, duree_video=None, decalage_ffmpeg=None):
    """paroles.json calé sur la vidéo doublée (réécrit), puis <base>.<langue>.srt, <base>.fr.srt (traduction ; ou
    <base>.traduction.fr.srt si les paroles sont déjà en français) et <base>.lrc à côté des sorties. decalage :
    instant de la vidéo doublée où commence le mix (plan_video : debut_son) ; duree_video : sa fin, dans le même temps.
    decalage_ffmpeg : le décalage du mix donné à ffmpeg (plan_video : decalage_son), s'il diffère (extrait mkv ou webm) :
    les pistes de sous-titres pour remettre_sur_video, que le conteneur recale avec l'image et le son, sont alors
    écrites à ce décalage dans travail/<voix>/.paroles_video_<n>.srt (à effacer après la vidéo). Rend (pistes de
    sous-titres pour remettre_sur_video, fichiers écrits, résumé pour le rapport ; avec des paroles, langue_chantee y
    est en plus, pour langue_pistes_audio)."""
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
    principale, secondes = langue_chantee(d)
    info['langue_chantee'] = {'langue': principale, 'secondes_par_langue': secondes}
    lg = d['langue'] or 'und'
    code, nom = langue_piste(d['langue'])
    a_ecrire = [(f'{base}.{lg}.srt', code, f'Paroles ({nom})', lambda x: x['segments'])]
    if info['traduction']:
        a_ecrire.append((f'{base}.fr.srt' if lg != 'fr' else f'{base}.traduction.fr.srt', 'fre',
                         'Paroles (français, traduction)', lambda x: x['traductions']['fr']['segments']))
    dv = fin_v = None
    if decalage_ffmpeg is not None and abs(float(decalage_ffmpeg) - float(decalage)) > 1e-6:
        dv = caler_paroles(json.loads(json.dumps(d)), decalage_ffmpeg)
        fin_v = None if duree_video is None else duree_video - float(decalage) + float(decalage_ffmpeg)
    pistes, fichiers = [], []
    for n, (nom_f, code_f, titre, segments) in enumerate(a_ecrire):
        srt = dossier / nom_f
        ecrire_srt(segments(d), srt, duree_video)
        fichiers.append(srt)
        if dv is not None:
            if not segments(dv):
                continue
            srt = tv / PISTES_PAROLES_TEMP.replace('*', str(n))
            ecrire_srt(segments(dv), srt, fin_v)
        pistes.append({'fichier': srt, 'langue': code_f, 'titre': titre})
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
    reglage = reglage_pour(a, rapport)
    if not reglage['intensite'] and reglage['gamme_donnee'] and not reglage['retirer']:
        dire('--autotune-gamme sans effet : auto-tune désactivé (--autotune N, ou doubler --config autotune N)')
    info_at = None
    with Etape('mix', titre, f'mixage v{version} ({mode})'
               + (f", auto-tune {reglage['intensite']}" if reglage['intensite'] else '')):
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
        # version auto-tune, en plus (la version normale ci-dessus ne dépend jamais de l'auto-tune)
        if reglage['intensite']:
            info_at = faire_autotune(t, tv, dossier, base, mode, gains, reglage)
        elif reglage['retirer']:
            info_at = retirer_autotune(tv, dossier, base)
    mix_s = time.time() - debut
    debut = time.time()
    paroles_on = getattr(a, 'paroles', True)
    pistes_st, fichiers_paroles, info_paroles, chantee = [], [], {'demande': paroles_on}, None
    with Etape('video', titre, 'son remis sur la vidéo (image copiée)' + (', paroles en sous-titres' if paroles_on else '')):
        plan = plan_video(t)
        if paroles_on:
            try:   # paroles.json, .srt : temps de la vidéo doublée ; pistes de la vidéo : temps de la coupe
                pistes_st, fichiers_paroles, i_p = preparer_paroles(tv, dossier, base, plan['debut_son'],
                                                                    duree_s(tv / 'mix.wav') + plan['debut_son'],
                                                                    decalage_ffmpeg=plan['decalage_son'])
                chantee = i_p.pop('langue_chantee', None)
                info_paroles.update(i_p)
            except Exception as e:  # noqa: BLE001 — des paroles abîmées n'empêchent jamais la vidéo
                info_paroles['raison'] = f'paroles inutilisables : {e}'
        # pistes audio : le doublage (piste 0, seule par défaut), puis original, beat seul, voix convertie seule, voix
        # d'origine seule (sauf --pistes-audio doublage, ou la clé pistes_audio de la configuration) ; toutes étiquetées
        # de la langue des paroles, sinon de celle de la source
        toutes = getattr(a, 'pistes_audio', None) != 'doublage'
        langue_a = langue_pistes_audio(chantee, info_paroles, plan['src'])
        info_pistes = {'reglage': 'toutes' if toutes else 'doublage', 'langue': langue_a}
        if langue_a['provenance'] == 'paroles' and langue_a['etiquette'] != langue_a['source']:
            dire(f"pistes audio en {langue_a['nom']} ({langue_a['etiquette']}), la langue des paroles"
                 f" (son d'origine : {langue_a['source'] or 'sans langue'})")
        try:
            pistes_a = pistes_en_plus(t, tv, voix, gains) if toutes else []
            demandees = pistes_st
            sortie, pistes_st, pistes_a = video_avec_repli(t, tv / 'mix.wav', dossier, base, pistes_st, pistes_a, plan,
                                                           titres_pistes(voix)['doublage'], langue_a['etiquette'])
            if demandees and not pistes_st:
                info_paroles['raison'] = 'pistes de sous-titres refusées par ffmpeg (fichiers .srt et .lrc gardés)'
            info_pistes['video'] = [titres_pistes(voix)['doublage'], *(p['titre'] for p in pistes_a)]
            info_paroles['pistes_video'] = [st['titre'] for st in pistes_st]
            sortie_at = lrc_at = None
            if info_at and info_at.get('applique'):
                b_at = base_autotune(base)
                try:   # mêmes pistes de paroles et sous-titres d'origine que la version normale, pistes audio auto-tune
                    pistes_at = pistes_en_plus(t, tv, voix, gains, autotune=True) if toutes else []
                    sortie_at, _, pistes_at = video_avec_repli(t, tv / 'mix_autotune.wav', dossier, b_at, pistes_st,
                                                               pistes_at, plan, titres_pistes(voix, True)['doublage'],
                                                               langue_a['etiquette'])
                    info_at['fichiers']['video'] = sortie_at.name
                    info_pistes['video_autotune'] = [titres_pistes(voix, True)['doublage'],
                                                     *(p['titre'] for p in pistes_at)]
                except Exception as e:  # noqa: BLE001 — le mp3 auto-tune reste
                    dire(f'ATTENTION : vidéo auto-tune impossible ({str(e).strip().splitlines()[-1][:200]}) : mp3 seul')
                    info_at['fichiers']['video'] = None
                    info_at['erreur_video'] = str(e).strip()[:500]
                lrc = next((f for f in fichiers_paroles if f.suffix == '.lrc'), None)
                if lrc is not None:
                    lrc_at = dossier / f'{b_at}.lrc'
                    shutil.copy2(lrc, lrc_at)
                info_at['fichiers']['lrc'] = lrc_at.name if lrc_at else None
        finally:   # voix convertie seule, pistes de paroles au temps de la coupe : le temps de les mettre dans la vidéo
            for f in (tv / '.voix_seule.wav', tv / '.voix_seule_autotune.wav', *tv.glob(PISTES_PAROLES_TEMP)):
                f.unlink(missing_ok=True)
    video_s = time.time() - debut
    rapport['mix'] = {**info, 'musique_seule': reste, 'limite_dbtp': round(20 * math.log10(LIMITE), 2), 'voix_db': voix_db, 'reverb_db': a.reverb_db if mode == 'chant' else None,
                      'gains_db': {k: round(v, 2) for k, v in gains.items()}, 'voix_source_db': cible}
    rapport['paroles_sorties'] = info_paroles
    rapport['pistes_audio'] = info_pistes
    if info_at is not None:
        rapport['autotune'] = info_at
    if temps_local is not None:
        temps_local.update({'mix_local_s': round(mix_s, 1), 'remise_video_s': round(video_s, 1)})
        rapport['temps_local'] = temps_local
    (tv / 'rapport.json').write_text(json.dumps(rapport, ensure_ascii=False, indent=1))
    lrc = next((f for f in fichiers_paroles if f.suffix == '.lrc'), None)
    ev_at = None
    if info_at and info_at.get('applique'):
        g_at = info_at.get('gamme') or {}
        ev_at = {'mp4': str(sortie_at) if sortie_at else None, 'mp3': str(dossier / info_at['fichiers']['mp3']),
                 'lrc': str(lrc_at) if lrc_at else None, 'mix': str(tv / 'mix_autotune.wav'),
                 'seche': str(tv / 'seche_voix_autotune.wav'),
                 'reverb': str(tv / info_at['fichiers']['reverb']) if info_at['fichiers'].get('reverb') else None,
                 'intensite': info_at['intensite'], 'tonalite': g_at.get('tonalite'), 'gamme': g_at.get('type')}
    EV('sortie', titre, mp4=str(sortie), mp3=str(mp3), rapport=str(tv / 'rapport.json'),
       comparaisons=[str(e) for e in extraits] or None,
       courbes=str(tv / 'courbes.json') if (tv / 'courbes.json').exists() else None,
       paroles=str(tv / 'paroles.json') if paroles_on and (tv / 'paroles.json').exists() else None,
       srt=[str(f) for f in fichiers_paroles if f.suffix == '.srt'] or None, lrc=str(lrc) if lrc else None,
       autotune=ev_at, voix=[v['id'] for v in voix_membres(voix)])
    m = rapport.get('mesures_musique', {})
    principale = rapport.get('variantes', {}).get('voix_convertie.flac', {})
    dire(f"mode {mode} ({'imposé' if rapport.get('mode_force') else 'détecté'} : musique sous la voix "
         f"{m.get('reste_sous_voix_db')} dB, tonalité {m.get('tonalite_reste')}, notes tenues {m.get('tenue')}) ; "
         f"modèle {rapport.get('modele')}, {principale.get('pas_diffusion')} pas")
    for ligne in lignes_voix_cibles(rapport):
        dire(ligne)
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
    if info_at and info_at.get('applique'):
        g_at, mes = info_at['gamme'], info_at.get('mesures') or {}
        det = g_at.get('detection') or {}
        if g_at['type'] == 'tonalite':
            gamme_txt = g_at['tonalite'] + (f" (détectée, corrélation {det.get('correlation')})"
                                            if lire_gamme(g_at['demandee'])['type'] == 'auto' else ' (imposée)')
            if g_at.get('notes_ajoutees'):
                gamme_txt += f", plus {', '.join(g_at['notes_ajoutees'])}"
        else:
            gamme_txt = 'chromatique'
        av, ap = mes.get('avant') or {}, mes.get('apres') or {}
        dire(f"auto-tune {info_at['intensite']} ({info_at['source']}) : gamme {gamme_txt}, accord {g_at['accord_hz']:g} Hz, "
             f"{len(info_at['passes'])} passe(s) ; écart médian aux notes {av.get('ecart_median_cents')} → "
             f"{ap.get('ecart_median_cents')} cents (90e centile {av.get('ecart_p90_cents')} → {ap.get('ecart_p90_cents')})")
    print('  hauteur par passage (dt = demi-tons) :', flush=True)
    for l in rapport.get('frise', []):
        print('   ', l, flush=True)
    if extraits:
        dire(f"comparaison {comp['pas'][0]} / {comp['pas'][1]} pas sur {fmt_t(comp['debut_s'])}–{fmt_t(comp['fin_s'])} : "
             + ', '.join(e.name for e in extraits))
    dire(f'fini : {sortie}\n         {mp3}' + ''.join(f'\n         {f}' for f in (sortie_at, ev_at and ev_at['mp3']) if f))
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
    g = session.get('gpus') or {}
    if g:
        dire(f"GPU Kaggle : {g.get('visibles')} carte(s) ({', '.join(g.get('noms') or []) or '?'}), {g.get('utilisees')} "
             'utilisée(s)' + (f" ; {g['repli']}" if g.get('repli') else ''))
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
                           'session_kaggle': st, 'videos_dans_la_session': len(travaux),
                           'gpus': (session.get('gpus') or {}).get('utilisees', 1)}
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


def reglages_noyau(a, voix, ancres, travaux):
    """Réglages du noyau (doubler_kaggle.py, REGLAGES) pour ces vidéos et cette voix. Une voix : ceux d'avant, tels quels.
    Plusieurs voix (voix combinée de charger_voix_spec) : « voix » est la première (analyse, parole), et voix_cibles les
    donne toutes, avec leur clé (homme, femme, chanteur1, chanteur2) et le mode (genre, ordre).
    gain_entree : gain appliqué avant le FLAC (crête à -1 dBFS), retiré par le noyau à la lecture ; retour_flac : la voix
    isolée revient en FLAC 24 bits (voix_source.flac et son gain dans voix_source.flac.json) ; moteur_chant, nettoyage :
    chaîne du chant (YingMusic-SVC sur la voix nettoyée par défaut) ; ancres : None sans fichier d'ancres (ANCRES ; le
    noyau décide alors l'octave sans empreinte) ; paroles : transcription (Whisper large-v3) et traduction française dans
    le noyau (paroles.json) ; gpus : cartes GPU à utiliser (auto, 1 ou 2 ; le noyau en prend au plus ce qu'il trouve)."""
    v0 = voix_membres(voix)[0]
    reglages = {**NOYAU_REGLAGES, 'pas': a.pas, 'ancres': ancres, 'paroles': bool(a.paroles),
                'moteur_chant': a.moteur, 'nettoyage': not a.sans_nettoyage,
                'gpus': 'auto' if getattr(a, 'gpu', None) in (None, 'auto') else int(a.gpu),
                'voix': {'nom': v0['id'], 'fichier': f"voix-{v0['id']}.wav", 'f0_parole_hz': v0['f0'],
                         'facteur_chant': FACTEUR_CHANT},
                'travaux': [{'id': tr['id'], 'entree': tr['entree'], 'mode': a.mode,
                             'octave': a.octave if a.octave == 'auto' else int(a.octave),
                             'comparer_modeles': bool(a.comparer_modeles), 'comparer_pas': a.comparer_pas,
                             'gain_entree': tr['gain_entree'], 'gain_entree_db': round(20 * math.log10(tr['gain_entree']), 3),
                             'retour_flac': True} for tr in travaux]}
    if voix.get('membres'):
        reglages['voix_cibles'] = {'mode': voix['mode'], 'voix': [
            {'cle': m['cle'], 'nom': m['id'], 'fichier': f"voix-{m['id']}.wav", 'f0_parole_hz': m['f0'],
             'facteur_chant': FACTEUR_CHANT, 'affichage': m['affichage']} for m in voix['membres']]}
    if a.modele_parole:
        reglages['modele_parole'] = a.modele_parole
    return reglages


def fiche_outils(sortie):
    """outils.json des outils préparés sur Kaggle (doubler --preparer-outils), ou None. Ces outils sont ceux du compte
    Kaggle (noyau OUTILS), pas d'un dossier de sortie : la fiche est cherchée dans <sortie>/.kaggle/, puis dans ETAT
    (copie qu'y laisse --preparer-outils, retenue pour le même noyau OUTILS seulement), puis dans le dossier des sorties
    par défaut (<données>/sorties/.kaggle/, où --preparer-outils l'écrivait seul). Un doublage vers un dossier neuf
    (--sortie) monte ainsi les outils déjà préparés."""
    commune = ETAT / 'outils.json'
    for f in (Path(sortie).expanduser().resolve() / '.kaggle' / 'outils.json', commune,
              Path(SORTIES).expanduser().resolve() / '.kaggle' / 'outils.json'):
        if not f.is_file():
            continue
        if f == commune:
            try:
                if json.loads(f.read_text()).get('noyau') != OUTILS:
                    continue   # outils d'un autre compte ou d'un autre noyau (clés kaggle.* changées depuis)
            except (OSError, ValueError, AttributeError):
                continue
        return f
    return None


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
        # réglages du noyau (reglages_noyau) ; voix_cibles seulement avec plusieurs voix (sinon ceux d'avant, tels quels)
        reglages = reglages_noyau(a, voix, ancres, travaux)
        retour = kdir / 'retour'
        outils = fiche_outils(a.sortie)
        sources = [OUTILS] if outils else []
        cartes = cartes_prevues(getattr(a, 'gpu', 'auto'), MACHINE)
        # historique : les rapports de ce dossier de sortie, et ceux du dossier par défaut (un dossier neuf n'en a pas)
        estimation = estimer_noyau_s([tr['duree_audio_s'] for tr in travaux], a.pas, [a.sortie, SORTIES],
                                     not a.sans_nettoyage, a.mode, a.moteur, a.paroles, len(voix_membres(voix)), cartes)
        # limite du suivi : au moins le double de l'estimation (le nettoyage allonge beaucoup une longue chanson)
        attente_maxi = max((30 + 20 * len(travaux)) * 60, 2 * estimation + 30 * 60)
        dire(f"chaîne : chant → {'voix nettoyée' if not a.sans_nettoyage else 'voix complète (sans nettoyage)'}, "
             f"{'YingMusic-SVC' if a.moteur == 'yingmusic' else 'Seed-VC 44 kHz'} ; parole → Seed-VC 22 kHz ; "
             f"paroles → {'Whisper large-v3 et traduction française' if a.paroles else 'non'} ; "
             f"{'deux cartes T4' if cartes == 2 else 'une carte'} (machine {MACHINE}) ; "
             f"session estimée à {estimation / 60:.0f} min")
        if not outils:
            dire('outils non préparés sur Kaggle : la session télécharge paquets et modèles, environ 4 min de plus '
                 '(doubler --preparer-outils, une fois)')
        if voix.get('membres'):
            dire(f"une voix par chanteur ({'duo mixte' if voix['mode'] == 'genre' else 'chanteurs dans l’ordre d’apparition'}) : "
                 f"{decrire_voix(voix)} ; en parole, {voix['membres'][0]['id']} seule")

        def noter(pousse, version):
            # voix : la spécification de --voix (homme:a,femme:b) avec plusieurs voix, pour que --reprendre la retrouve
            ecrire_session({'noyau': NOYAU, 'jeu': JEU, 'version_noyau': version, 'pousse_epoch': pousse,
                            'poussee_le': datetime.datetime.fromtimestamp(pousse).astimezone().isoformat(timespec='seconds'),
                            'voix': voix.get('spec') or voix['id'], 'sortie': str(Path(a.sortie).expanduser().resolve()),
                            'envoi_s': envoi_s,
                            'estimation_s': estimation, 'attente_maxi_s': attente_maxi, 'pas': a.pas,
                            'moteur': a.moteur, 'nettoyage': not a.sans_nettoyage, 'paroles': bool(a.paroles),
                            'gpu': getattr(a, 'gpu', 'auto'), 'machine': MACHINE,
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
    voix = charger_voix_spec(s['voix'])   # un nom, ou la spécification homme:a,femme:b
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
    Rien ne part du PC à part ce script, et seuls outils.json et le journal reviennent. La fiche est gardée dans
    <sortie>/.kaggle/ et, avec le nom du noyau, dans ETAT : tout dossier de sortie la retrouve (fiche_outils)."""
    kdir = Path(a.sortie).expanduser().resolve() / '.kaggle'
    kdir.mkdir(parents=True, exist_ok=True)
    retour = kdir / 'retour-outils'
    # autre noyau, aucun jeu de données : peut tourner en même temps qu'un doublage (deux sessions GPU permises)
    temps = lancer_noyau({'outils': True}, kdir, retour, 60 * 60, noyau=OUTILS, jeux=(), motif=r'(outils\.json|\.log)$',
                         attendu='outils.json')
    fiche = json.loads((retour / 'outils.json').read_text())
    (kdir / 'outils.json').write_text(json.dumps(fiche, ensure_ascii=False, indent=1))
    ETAT.mkdir(parents=True, exist_ok=True)
    (ETAT / 'outils.json').write_text(json.dumps({**fiche, 'noyau': OUTILS}, ensure_ascii=False, indent=1))
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


def arguments_autotune(argv):
    """« --autotune clip.mp4 » : la vidéo n'est pas une intensité. « --autotune » suivi d'autre chose qu'une intensité
    devient « --autotune=70 » (argparse prendrait sinon la vidéo pour la valeur et refuserait la commande)."""
    out = list(argv)
    for i, x in enumerate(out):
        if x == '--':
            break
        if x == '--autotune' and i + 1 < len(out):
            try:
                intensite_autotune(out[i + 1])
            except ValueError:
                out[i] = f'--autotune={AUTOTUNE_DEFAUT}'
    return out


def main():
    p = argparse.ArgumentParser(prog='doubler', description=__doc__.split('\n')[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split('\n', 1)[1])
    p.add_argument('videos', nargs='*', help='vidéo(s) mp4, mkv, webm, mov… ; « vidéo@60+40 » : extrait propre à cette '
                                              'vidéo (début+durée en s ou m:ss ; @60 jusqu\'à la fin, @+40 le début)')
    p.add_argument('--voix', default=None, metavar='NOM',
                   help='voix cible : un dossier de <données>/voix (défaut : clé « voix » de la configuration, '
                        'sinon la seule voix présente) ; une voix par chanteur : homme:NOM,femme:NOM (duo mixte) ou '
                        'chanteur1:NOM,chanteur2:NOM (duo de même genre, dans l\'ordre d\'apparition) : sorties « voix A et '
                        'B », dossier travail/A+B ; une seule voix trouvée dans la chanson : tout dans la voix de sa classe, '
                        'sinon la première')
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
    p.add_argument('--pistes-audio', choices=list(PISTES_AUDIO), default=None,
                   help='pistes audio de la vidéo doublée : toutes (défaut : le doublage, en premier et seul par défaut, '
                        'puis original, beat seul, voix convertie seule, voix d\'origine seule) ou doublage (lui seul). '
                        'Défaut lu dans la configuration (clé « pistes_audio ») ; avec --remix : la vidéo refaite ainsi')
    p.add_argument('--gpu', choices=list(GPU_CHOIX), default=None,
                   help="cartes GPU du noyau : auto (défaut : les deux T4 de la machine Kaggle, une seule si elle n'en a "
                        "qu'une), 1 (une carte, comme avant) ou 2 ; même résultat, plus vite à deux. Défaut lu dans la "
                        'configuration (clé « gpu »)')
    p.add_argument('--voix-db', type=float, default=None, help='voix par rapport à celle de la vidéo (défaut +4 en chant, 0 en parole)')
    p.add_argument('--reverb-db', type=float, default=-13, help='reverb sous la voix sèche, en chant (défaut -13)')
    p.add_argument('--mix-v1', action='store_true',
                   help="ancien mixage : sans suivi des nuances de la voix d'origine ni dé-esseur (défaut : mixage v2)")
    p.add_argument('--autotune', nargs='?', type=_intensite_option, const=AUTOTUNE_DEFAUT, default=None, metavar='N',
                   help=f'auto-tune de la voix, intensité N de 0 à 100 ({AUTOTUNE_DEFAUT} sans valeur ; 0 : désactivé) : '
                        'bas, notes juste rendues plus justes ; haut, effet robotique, sauts de note francs. Version '
                        '« (auto-tune) » de la vidéo, du mp3 et du .lrc en plus de la version normale, faite sur le PC par '
                        'x42-autotune (paquet x42-plugins). Défaut : clé « autotune » de la configuration (0 sinon). En '
                        'parole, seulement avec --autotune. Avec --remix, sans --autotune ni --sans-autotune, une version '
                        'auto-tune existante est refaite à la même intensité')
    p.add_argument('--sans-autotune', action='store_true',
                   help="pas d'auto-tune (l'emporte sur --autotune et la configuration) ; avec --remix : version auto-tune "
                        'retirée (vidéo, mp3 et .lrc à la corbeille)')
    p.add_argument('--autotune-gamme', type=_gamme_option, default=None, metavar='GAMME',
                   help=f'gamme de l\'auto-tune : {GAMME_AIDE}')
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
    a = p.parse_args(arguments_autotune(sys.argv[1:]))
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
    a.pistes_audio = a.pistes_audio or pistes_audio_config(conf)
    if a.reprendre:
        if not KAGGLE.exists() or not JETON.exists():
            sys.exit(f'Kaggle : commande ({KAGGLE}) ou clé (~/.kaggle/access_token) absente')
        a.paroles = not a.sans_paroles and paroles_config(conf)
        a.autotune_reglage = reglage_autotune(a, conf)
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
    a.gpu = a.gpu or gpu_config(conf)
    a.paroles = not a.sans_paroles and paroles_config(conf)
    a.autotune_reglage = reglage_autotune(a, conf)
    if a.voix is None:
        a.voix = exiger_voix_defaut()
    voix = charger_voix_spec(a.voix)   # une voix, ou une par chanteur (homme:a,femme:b, chanteur1:a,chanteur2:b)
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
                dire(f"=== {tr['video'].name} (remix, voix {decrire_voix(voix)})")
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
