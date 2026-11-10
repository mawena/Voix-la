#!/usr/bin/env python3
"""Faux doubler pour les essais de l'interface Voix-là, choisi par VOIXLA_DOUBLER="python3 …/faux_doubler.py".

Il imite doubler.py : mêmes options (aide argparse), mêmes lignes de texte « HH:MM:SS … » dans le même ordre, mêmes
événements (--evenements), mêmes fichiers de sortie (travail/source.json, travail/<voix>/rapport.json minimal,
travail/<voix>/paroles.json inventé sauf avec --sans-paroles ou la clé « paroles » à non, fichiers .mp4/.mp3 vides).
AUCUN appel réseau, aucun Kaggle, aucun ffmpeg : chaque étape est une courte pause.
Il meurt normalement sur SIGTERM (essai d'annulation).

Variables d'environnement :
- FAUX_DOUBLER_VITESSE : secondes par étape (défaut 0.2) ;
- FAUX_DOUBLER_ANCIEN=1 : ancien doubler, sans --evenements, --donnees, --config, --json ni --mix-v1 (voix lues dans
  $DOUBLER_VOIX, sorties dans --sortie, ligne « mixage chant : … » sans numéro de version) ;
- FAUX_DOUBLER_ECHEC : « kaggle » (le noyau finit en erreur : ligne « ÉCHEC de la session Kaggle : … », code 1),
  « envoi » (jeu de données refusé) ou « mix » (le mixage de la première vidéo échoue, les autres continuent) ;
- FAUX_DOUBLER_COUPURE=1 : une coupure réseau pendant le suivi du noyau (statut « injoignable ») ;
- FAUX_DOUBLER_DUREE : durée du son d'une vidéo entière, en secondes (défaut 30) ;
- FAUX_DOUBLER_ESTIMATION : durée de session annoncée (estimation_s des événements « kaggle », estimation_noyau_s du
  rapport), en secondes, à la place de son estimation inventée ;
- FAUX_DOUBLER_ENFANT=<fichier> : lance un processus enfant qui ignore SIGTERM (comme un ffmpeg têtu) et écrit son pid
  dans ce fichier (l'annulation doit tuer tout le groupe de processus) ;
- FAUX_DOUBLER_TRACE=<fichier> : une ligne JSON par lancement (argv, début, variables utiles) et une à la fin (code) ;
- FAUX_DOUBLER_CONFIG=<dossier> : son config.json, à la place du fichier fixe ~/.config/voix-la/config.json du vrai
  (sinon $VOIXLA_CONFIG/config.json) ;
- FAUX_DOUBLER_X42=absent : greffon x42-autotune absent (--config --json : x42_autotune null ; auto-tune refusé) ;
- FAUX_DOUBLER_SANS_GPU=1 : doubler récent d'avant les deux GPU (0.5.0) : ni --gpu, ni clés « gpu » et
  « kaggle.machine », ni temps_local.gpus dans les rapports.
Deux GPU (--gpu auto|1|2, clé « gpu », clé « kaggle.machine ») : comme doubler, deux cartes en auto sur la machine
NvidiaTeslaT4 (défaut), une sur une autre ; --config --json donne « gpu » et « kaggle.machine », et les rapports
temps_local.gpus (cartes utilisées).
Une voix par chanteur (--voix homme:a,femme:b ou chanteur1:a,chanteur2:b) : voix combinée « a+b », sorties « voix A et
B », rapport inventé d'un duo (voix_cibles, chanteurs, affectation, passages) et courbes.json avec voix_par_trame ; les
deux versions de chaque passage chanté (versions/versions.json et rapport.versions, sauf FAUX_DOUBLER_SANS_VERSIONS=1 :
comme un ancien doublage). Correction à la main (--remix --affectation FICHIER|auto, --passage N=VOIX), comme doubler :
travail/<a+b>/affectation.json écrit ou effacé, rapport (affectation.passages recalculés à partir des morceaux,
secondes_par_voix, morceaux_en_duo, manuelle ; morceaux corrige, voix_pistes, voix_pistes_auto) et courbes.json
(voix_par_trame) refaits, l'automatique gardé dans versions/auto.json ; sans les deux versions : « ÉCHEC », comme doubler.
Fichier audio seul (mp3, wav, flac…) en entrée : sortie mp3 sans vidéo, source.json « audio_seul », comme doubler.
FAUX_DOUBLER_SANS_AFFECTATION=1 : doubler trop ancien, sans --affectation ni --passage.
Comparer des voix (--comparer A,B,C) : une seule session pour toutes les voix ; une sortie complète par voix dans le
dossier de la vidéo (« <titre> - voix A.<ext> », travail/A/, rapport inventé de chaque voix), comparaison.json, un événement
« sortie » par voix ; --remix --comparer les refait toutes. FAUX_DOUBLER_SANS_COMPARER=1 : doubler d'avant --comparer.
Auto-tune (--autotune [N], --sans-autotune, --autotune-gamme, clé « autotune ») : fichiers « (auto-tune) » vides et
section « autotune » du rapport, comme doubler. Avec --remix : --autotune N crée ou refait la version (gamme
--autotune-gamme, sinon auto) ; sans option, une version déjà là est refaite à la même intensité et la même gamme ;
--sans-autotune la retire (fichiers effacés, jamais envoyés à la vraie corbeille ; « retires » dans le rapport).
config.json n'est jamais lu ni écrit ailleurs ; il refuse d'écrire dans ~/Music, ~/.config, ~/.local et dans les
dossiers voix/ et sorties/ du dépôt.
"""

import argparse
import datetime
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ANCIEN = os.environ.get("FAUX_DOUBLER_ANCIEN") == "1"
SANS_GPU = ANCIEN or os.environ.get("FAUX_DOUBLER_SANS_GPU") == "1"
SANS_AFFECTATION = ANCIEN or SANS_GPU or os.environ.get("FAUX_DOUBLER_SANS_AFFECTATION") == "1"
SANS_VERSIONS = os.environ.get("FAUX_DOUBLER_SANS_VERSIONS") == "1"
SANS_COMPARER = SANS_AFFECTATION or os.environ.get("FAUX_DOUBLER_SANS_COMPARER") == "1"
ECHEC = (os.environ.get("FAUX_DOUBLER_ECHEC") or "").strip().lower()
DEPOT = Path(__file__).resolve().parents[2]
DONNEES_DEFAUT = Path.home() / "Music" / "Voix-là"
INTERDITS = (Path.home() / "Music", Path.home() / ".config", Path.home() / ".local", DEPOT / "sorties", DEPOT / "voix")
FACTEUR_CHANT = 2.0
KAGGLE_DEFAUTS = {"jeu": "doublage-voix-entrees", "noyau": "doublage-voix-gpu", "outils": "doublage-outils"}
CLES_CONFIG = {"donnees": "dossier des données (voix/, sorties/)", "kaggle.compte": "compte Kaggle (nom d'utilisateur)",
               "kaggle.jeu": "jeu de données privé des entrées", "kaggle.noyau": "noyau GPU privé du doublage",
               "kaggle.outils": "noyau privé des outils préparés",
               "paroles": "transcription des paroles et traduction française : oui (défaut) ou non",
               "autotune": "intensité de l'auto-tune à chaque doublage, de 0 (désactivé, défaut) à 100",
               "voix": "voix cible par défaut, un dossier de <données>/voix (sinon la seule voix présente)"}
MACHINE_DEFAUT = "NvidiaTeslaT4"
GPU_CHOIX = ("auto", "1", "2")
if not SANS_GPU:
    CLES_CONFIG.update({"gpu": "cartes GPU utilisées par le noyau : auto (défaut), 1 ou 2",
                        "kaggle.machine": f"machine Kaggle demandée à l'envoi du noyau (défaut : {MACHINE_DEFAUT})"})
# paroles inventées du faux doubler (format 1 de travail/<voix>/paroles.json)
PAROLES_FAUSSES = [(0.5, 2.5, "Un matin de pluie fine"), (3.0, 5.0, "le café refroidit doucement"),
                   (6.0, 8.5, "et la radio chante encore")]
CONTENEURS = (".mp4", ".m4v", ".mov", ".mkv", ".webm")
SONS = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".oga", ".aac")   # sans image : la sortie est le mp3 seul
SECRETS = re.compile(r"jeton|token|secret|password|mot.?de.?passe|(^|[._])(cle|clé|key)($|[._])", re.I)

AIDE = """doubler (Voix-là) : remplace la voix d'une vidéo par une voix clonée (FAUX doubler des essais, sans Kaggle).

  doubler <vidéo> [<vidéo>…] [--voix NOM | --voix homme:NOM,femme:NOM | --voix chanteur1:NOM,chanteur2:NOM]
          [--mode auto|chant|parole] [--octave auto|0|-12|+12] [--pas 30]
          [--voix-db 4] [--reverb-db -13] [--mix-v1] [--debut S --duree S] [--remix] [--sortie DIR]
          [--autotune [N]] [--autotune-gamme GAMME] [--sans-autotune]
          [--comparer-pas 50] [--donnees DIR] [--evenements FICHIER]
          [--comparer NOM,NOM[,NOM…]]
  doubler --ajouter-voix <nom> <enregistrement>      ajoute une voix (copie telle quelle, sans analyse)
  doubler --voix-liste [--json]                      voix disponibles
  doubler --config [CLE VALEUR]                      affiche la configuration, ou écrit une clé
  doubler --reprendre                                finit une session Kaggle interrompue (rien à reprendre ici)
  doubler "clip.mp4@60+40" "film.mkv@1:10+35"        un extrait propre à chaque vidéo d'un lot

Faux doubler : chaque étape est une pause de FAUX_DOUBLER_VITESSE secondes ; aucun réseau, aucun ffmpeg.
"""
AIDE_ANCIENNE = """doubler : remplace la voix d'une vidéo par une voix clonée (FAUX doubler, ancienne version).

  doubler <vidéo> [<vidéo>…] [--voix NOM] [--mode auto|chant|parole] [--octave auto|0|-12|+12] [--pas 30]
          [--voix-db 4] [--reverb-db -13] [--debut S --duree S] [--remix] [--sortie DIR] [--comparer-pas 50]
  doubler --ajouter-voix <nom> <enregistrement>      ajoute une voix
  doubler --voix-liste                               voix disponibles
Voix : dossier $DOUBLER_VOIX.
"""

# Fixés par configurer()
DONNEES = None
SOURCE_DONNEES = "défaut"
VOIX = None
VOIX_DEFAUT = None   # voix_par_defaut() : clé « voix » de config.json, sinon la seule voix de VOIX
VOIX_DEFAUT_SOURCE = None
COMPTE = "essai"
JEU = NOYAU = OUTILS = None
_mix_rate = False   # FAUX_DOUBLER_ECHEC=mix : seule la première vidéo échoue


def vitesse():
    try:
        return max(0.0, float(os.environ.get("FAUX_DOUBLER_VITESSE") or 0.2))
    except ValueError:
        return 0.2


def pause(n=1.0):
    time.sleep(vitesse() * n)


def dire(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def tracer(**champs):
    f = os.environ.get("FAUX_DOUBLER_TRACE")
    if not f:
        return
    with open(f, "a", encoding="utf-8") as sortie:
        sortie.write(json.dumps({"pid": os.getpid(), **champs}, ensure_ascii=False) + "\n")


def verifier_ecriture(chemin):
    """Refus d'écrire hors des dossiers d'essai (jamais dans les vraies données)."""
    p = Path(chemin).expanduser().resolve()
    for interdit in INTERDITS:
        try:
            p.relative_to(interdit.resolve())
        except ValueError:
            continue
        raise SystemExit(f"faux doubler : refus d'écrire dans {p} (dossier réel ; poser VOIXLA_DONNEES ou --sortie vers "
                         "un dossier d'essai)")
    return p


# --- Événements (même format que doubler.py) --------------------------------------------------------------------------

class Evenements:
    def __init__(self):
        self.f = None

    def ouvrir(self, chemin):
        if chemin:
            Path(chemin).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self.f = open(Path(chemin).expanduser(), "a", encoding="utf-8")

    def __call__(self, type_, video=None, **champs):
        if not self.f:
            return
        ev = {"t": datetime.datetime.now().astimezone().isoformat(timespec="milliseconds"), "type": type_, "video": video}
        ev.update({k: v for k, v in champs.items() if v is not None})
        try:
            self.f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
            self.f.flush()
        except OSError:
            pass


EV = Evenements()


class Etape:
    def __init__(self, etape, videos, message=None):
        self.etape, self.message = etape, message
        self.videos = [videos] if isinstance(videos, str) or videos is None else list(videos)

    def __enter__(self):
        self.t0, self.fini = time.time(), False
        for v in self.videos:
            EV("etape", v, etape=self.etape, etat="debut", message=self.message)
        return self

    def finir(self, erreur=None, message=None):
        if self.fini:
            return
        self.fini = True
        duree = round(time.time() - self.t0, 1)
        for v in self.videos:
            EV("etape", v, etape=self.etape, etat="erreur" if erreur else "fin", duree_s=duree,
               message=str(erreur) if erreur else (message or self.message))

    def __exit__(self, genre, exc, tb):
        self.finir(exc)
        return False


# --- Configuration (seulement dans $VOIXLA_CONFIG) ---------------------------------------------------------------------

def fichier_config():
    d = os.environ.get("FAUX_DOUBLER_CONFIG") or os.environ.get("VOIXLA_CONFIG")
    return Path(d).expanduser() / "config.json" if d else None


def lire_config():
    f = fichier_config()
    if f is None or not f.exists():
        return {}
    try:
        conf = json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SystemExit(f"{f} illisible ({e}) : corriger le JSON, ou l'effacer et refaire doubler --config …")
    if not isinstance(conf, dict):
        raise SystemExit(f'{f} : un objet JSON est attendu ({{"kaggle": {{"compte": "…"}}}})')
    return conf


def valeur_config(conf, cle):
    for morceau in cle.split("."):
        if not isinstance(conf, dict) or morceau not in conf:
            return None
        conf = conf[morceau]
    return conf


def ecrire_config(cle, valeur):
    f = fichier_config()
    if f is None:
        raise SystemExit("faux doubler : ni FAUX_DOUBLER_CONFIG ni VOIXLA_CONFIG, refus d'écrire dans ~/.config")
    verifier_ecriture(f)
    if cle not in CLES_CONFIG:
        raise SystemExit(f"clé inconnue : {cle} (clés : {', '.join(CLES_CONFIG)})")
    if cle == "donnees":
        valeur = str(Path(valeur).expanduser().resolve())   # comme le vrai : "" devient le dossier courant
    if cle == "paroles":
        if str(valeur).strip().lower() not in ("oui", "non", "vrai", "faux", "true", "false", "1", "0"):
            raise SystemExit(f"paroles : oui ou non (pas « {valeur} »)")
        valeur = str(valeur).strip().lower() in ("oui", "vrai", "true", "1")
    if cle == "autotune":
        v = str(valeur).strip().lower()
        valeur = 0 if v in ("non", "faux", "false", "off") else int(v) if v.isdigit() else -1
        if not 0 <= valeur <= 100:
            raise SystemExit("autotune : un entier de 0 à 100 ; 0 le désactive")
    if cle == "gpu":
        valeur = str(valeur).strip().lower()
        if valeur not in GPU_CHOIX:
            raise SystemExit(f"gpu : auto, 1 ou 2 attendu (reçu : {valeur})")
    if cle == "kaggle.machine" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,39}", str(valeur)):
        raise SystemExit(f"kaggle.machine : nom de machine Kaggle attendu, par exemple {MACHINE_DEFAUT} "
                         f"(reçu : {valeur})")
    conf = lire_config()
    d = conf
    *chemin, fin = cle.split(".")
    for morceau in chemin:
        if not isinstance(d.get(morceau), dict):
            d[morceau] = {}
        d = d[morceau]
    d[fin] = valeur
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(conf, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, f)
    print(f"{cle} = {valeur}  ({f})")


def masquer(conf):
    if isinstance(conf, dict):
        return {k: ("***" if SECRETS.search(str(k)) else masquer(v)) for k, v in conf.items()}
    return conf


def configurer(a):
    """Comme doubler : --donnees, $VOIXLA_DONNEES, « donnees » de config.json, sinon défaut (où rien n'est écrit).
    Ancien doubler : voix dans $DOUBLER_VOIX, sorties dans --sortie, rien d'autre."""
    global DONNEES, SOURCE_DONNEES, VOIX, VOIX_DEFAUT, VOIX_DEFAUT_SOURCE, COMPTE, JEU, NOYAU, OUTILS
    conf = {} if ANCIEN else lire_config()
    if ANCIEN:
        DONNEES, SOURCE_DONNEES = None, "ancien doubler"
    else:
        for valeur, source in ((getattr(a, "donnees", None), "option --donnees"),
                               (os.environ.get("VOIXLA_DONNEES"), "VOIXLA_DONNEES"),
                               (valeur_config(conf, "donnees"), str(fichier_config()))):
            if valeur:
                DONNEES, SOURCE_DONNEES = Path(str(valeur)).expanduser(), source
                break
        else:
            DONNEES, SOURCE_DONNEES = DONNEES_DEFAUT, "défaut"
    if os.environ.get("DOUBLER_VOIX"):
        VOIX = Path(os.environ["DOUBLER_VOIX"]).expanduser()
    elif DONNEES is not None:
        VOIX = DONNEES / "voix"
    else:
        VOIX = None
    if not a.sortie and DONNEES is not None:
        a.sortie = str(DONNEES / "sorties")
    k = conf.get("kaggle") if isinstance(conf.get("kaggle"), dict) else {}
    COMPTE = str(k.get("compte") or "essai").strip()
    JEU, NOYAU, OUTILS = (f"{COMPTE}/{k.get(n) or d}" for n, d in KAGGLE_DEFAUTS.items())
    VOIX_DEFAUT, VOIX_DEFAUT_SOURCE = voix_par_defaut(conf)
    return conf


def voix_presentes():
    return sorted(p.parent.name for p in VOIX.glob("*/voix.json")) if VOIX else []


def voix_par_defaut(conf):
    """Comme doubler : clé « voix » de config.json, sinon la seule voix présente. Rend (nom ou None, d'où il vient)."""
    nom = valeur_config(conf, "voix")
    if nom:
        return str(nom), str(fichier_config())
    presentes = voix_presentes()
    if len(presentes) == 1:
        return presentes[0], f"seule voix de {VOIX}"
    if not presentes:
        return None, f"aucune voix dans {VOIX}"
    return None, f"{len(presentes)} voix dans {VOIX} et aucune choisie (doubler --config voix NOM)"


def exiger_voix_defaut():
    """Sans --voix : la voix par défaut, ou un message qui dit comment la choisir (mêmes messages que doubler)."""
    presentes = voix_presentes()
    if VOIX_DEFAUT and VOIX_DEFAUT in presentes:
        return VOIX_DEFAUT
    if VOIX_DEFAUT:
        raise SystemExit(f"voix par défaut « {VOIX_DEFAUT} » (clé voix de config.json) absente de {VOIX} (disponibles : "
                         f"{', '.join(presentes) or 'aucune'}) : choisir avec --voix NOM, ou doubler --config voix NOM")
    if not presentes:
        raise SystemExit(f"aucune voix dans {VOIX} : en ajouter une (doubler --ajouter-voix <nom> <enregistrement>)")
    raise SystemExit(f"plusieurs voix dans {VOIX} ({', '.join(presentes)}) et aucune par défaut : choisir avec --voix NOM, "
                     "ou une fois pour toutes avec doubler --config voix NOM")


def afficher_config(a, en_json=False):
    conf = lire_config()
    compte = valeur_config(conf, "kaggle.compte")
    noms = {n: (f"{compte}/{valeur_config(conf, 'kaggle.' + n) or d}" if compte else None) for n, d in KAGGLE_DEFAUTS.items()}
    f = fichier_config()
    effectif = {"config": str(f) if f else None, "donnees": str(DONNEES), "source_donnees": SOURCE_DONNEES,
                "voix": str(VOIX), "sorties": a.sortie, "voix_defaut": VOIX_DEFAUT,
                "source_voix_defaut": VOIX_DEFAUT_SOURCE, "kaggle": {"compte": compte, **noms},
                "autotune": autotune_config(conf), "x42_autotune": x42(),
                "jeton_kaggle": "absent", "commande_kaggle": "(faux doubler : aucune)", "contenu": masquer(conf)}
    if not SANS_GPU:
        effectif["gpu"] = gpu_config(conf)
        effectif["kaggle"]["machine"] = machine_config(conf)
    if en_json:
        print(json.dumps(effectif, ensure_ascii=False, indent=1))
        return
    print(f"configuration : {f}{'' if f and f.exists() else ' (absente)'}")
    print(json.dumps(masquer(conf), ensure_ascii=False, indent=1))
    print("valeurs utilisées :")
    print(f"  données        {DONNEES}  ({SOURCE_DONNEES})")
    print(f"  voix           {VOIX}{'  ($DOUBLER_VOIX)' if os.environ.get('DOUBLER_VOIX') else ''}")
    print(f"  sorties        {a.sortie}")
    print(f"  voix (défaut)  {VOIX_DEFAUT or '—'}  ({VOIX_DEFAUT_SOURCE})")
    if not SANS_GPU:
        print(f"  gpu            {gpu_config(conf)}{'' if valeur_config(conf, 'gpu') is not None else '  (défaut)'}")
    for n in KAGGLE_DEFAUTS:
        print(f"  kaggle.{n:8s} {noms[n] or '— (kaggle.compte manque : doubler --config kaggle.compte <nom>)'}")
    if not SANS_GPU:
        print(f"  kaggle.machine {machine_config(conf)}")
    print("  jeton Kaggle   absent (faux doubler, jamais lu)")
    print("clés : " + " ; ".join(f"{c} ({d})" for c, d in CLES_CONFIG.items()))


# --- Voix ------------------------------------------------------------------------------------------------------------

def charger_voix(nom):
    if VOIX is None:
        raise SystemExit("voix inconnue : DOUBLER_VOIX n'est pas posée (ancien doubler)")
    d = VOIX / nom
    fiche = d / "voix.json"
    if not fiche.exists():
        dispo = ", ".join(sorted(p.parent.name for p in VOIX.glob("*/voix.json"))) or "aucune"
        raise SystemExit(f"voix inconnue : {nom} (disponibles : {dispo} ; ajouter : doubler --ajouter-voix {nom} <enregistrement>)")
    f = json.loads(fiche.read_text(encoding="utf-8"))
    ref = d / f.get("reference", "reference.wav")
    if not ref.exists():
        raise SystemExit(f"référence absente : {ref}")
    f0 = f.get("hauteur_parole_mediane_hz")
    if not f0:
        raise SystemExit(f"{fiche} : hauteur_parole_mediane_hz manque")
    return {"id": nom, "affichage": f.get("nom") or nom.capitalize(), "reference": ref, "f0": float(f0), "fiche": f}


CLES_VOIX = {"genre": ("homme", "femme"), "ordre": tuple(f"chanteur{k}" for k in range(1, 7)),
             "personnes": tuple(f"personne{k}" for k in range(1, 7))}


def joindre_noms(noms):
    noms = list(noms)
    return noms[0] if len(noms) == 1 else ", ".join(noms[:-1]) + " et " + noms[-1] if noms else ""


def charger_voix_spec(texte):
    """Comme doubler : « NOM », ou « homme:a,femme:b » / « chanteur1:a,chanteur2:b » (voix combinée « a+b »)."""
    texte = str(texte).strip()
    if ":" not in texte:
        return charger_voix(texte)
    paires = []
    for morceau in (m.strip() for m in texte.split(",") if m.strip()):
        cle, _, nom = (x.strip() for x in morceau.partition(":"))
        cle = cle.lower().replace(" ", "").replace("-", "").replace("_", "")
        if not nom or cle in (c for c, _ in paires):
            raise SystemExit(f"--voix {texte} : « {morceau} » : clé:voix attendu")
        paires.append((cle.lower(), nom))
    mode = next((m for m, cles in CLES_VOIX.items() if all(c in cles for c, _ in paires)), None)
    if mode is None:
        raise SystemExit(f"--voix {texte} : homme/femme, chanteurN et personneN ne se mélangent pas")
    paires.sort(key=lambda p: CLES_VOIX[mode].index(p[0]))
    noms = list(dict.fromkeys(n for _, n in paires))
    if len(noms) == 1:
        return charger_voix(noms[0])
    membres = [charger_voix(n) for n in noms]
    for (cle, _), m in zip(paires, membres):
        m["cle"] = cle
    return {"id": "+".join(noms), "affichage": joindre_noms(m["affichage"] for m in membres),
            "reference": membres[0]["reference"], "f0": membres[0]["f0"], "fiche": membres[0]["fiche"], "mode": mode,
            "membres": membres, "spec": ",".join(f"{c}:{n}" for c, n in paires)}


def voix_membres(voix):
    return voix.get("membres") or [voix]


def charger_voix_comparees(texte):
    """Comme doubler : « A,B,C » (deux à six voix entières différentes) ; la première avec la liste « comparees »."""
    noms = list(dict.fromkeys(n.strip() for n in str(texte).split(",") if n.strip()))
    if any(":" in n for n in noms):
        raise SystemExit(f"--comparer {texte} : des noms de voix seulement ; une voix par chanteur ne se compare pas")
    if len(noms) < 2:
        raise SystemExit("--comparer demande au moins deux voix différentes (A,B,C) ; une seule : --voix NOM")
    if len(noms) > 6:
        raise SystemExit(f"--comparer : {len(noms)} voix, 6 au plus en une session")
    membres = [charger_voix(n) for n in noms]
    return {**membres[0], "comparees": membres, "spec": ",".join(noms)}


def sortie_de_voix(tr, voix):
    return {**tr, "tv": tr["travail"] / voix["id"], "base": f"{tr['video'].stem} - voix {voix['affichage']}"}


def ecrire_comparaison(tr, comparees):
    (tr["dossier"] / "comparaison.json").write_text(json.dumps({
        "format": 1, "cree_le": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "voix": [{"id": v["id"], "nom": v["affichage"], "fichier": sortie_de_voix(tr, v)["base"]} for v in comparees]},
        ensure_ascii=False, indent=1), encoding="utf-8")


def autotune_config(conf):
    v = valeur_config(conf, "autotune")
    try:
        return max(0, min(100, int(v))) if v is not None and not isinstance(v, bool) else (70 if v else 0)
    except (TypeError, ValueError):
        return 0


def x42():
    return None if os.environ.get("FAUX_DOUBLER_X42") == "absent" else "/usr/lib/lv2 (faux)"


def gpu_config(conf):
    """Comme doubler : clé « gpu » (auto, 1 ou 2), auto si absente ou illisible."""
    v = valeur_config(conf, "gpu")
    v = "auto" if v is None else str(v).strip().lower()
    return v if v in GPU_CHOIX else "auto"


def machine_config(conf):
    return str(valeur_config(conf, "kaggle.machine") or MACHINE_DEFAUT)


def cartes_utilisees(a):
    """Cartes que le faux noyau « utilise » : --gpu, sinon la clé « gpu » ; auto : deux sur la machine NvidiaTeslaT4
    (GPU T4 x2), une sur une autre (comme le vrai noyau, qui prend les cartes qu'il voit)."""
    gpu = getattr(a, "gpu", None) or gpu_config(lire_config())
    if gpu in ("1", "2"):
        return int(gpu)
    return 2 if machine_config(lire_config()) == MACHINE_DEFAUT else 1


def liste_voix(en_json=False):
    fiches = sorted(VOIX.glob("*/voix.json")) if VOIX else []
    if en_json:
        voix = []
        for p in fiches:
            f = json.loads(p.read_text(encoding="utf-8"))
            ref = p.parent / f.get("reference", "reference.wav")
            f0 = f.get("hauteur_parole_mediane_hz")
            voix.append({"id": p.parent.name, **f, "chemin_reference": str(ref), "reference_presente": ref.exists(),
                         "dossier": str(p.parent), "defaut": p.parent.name == VOIX_DEFAUT,
                         "centre_chant_hz": round(FACTEUR_CHANT * f0, 1) if f0 else None})
        print(json.dumps(voix, ensure_ascii=False, indent=1))
        return
    if not fiches:
        print(f"aucune voix dans {VOIX} (ajouter : doubler --ajouter-voix <nom> <enregistrement>)")
        return
    print(f"{'voix':14s} {'nom affiché':16s} {'parole':>8s} {'chant':>8s} {'durée':>7s}  date        origine")
    for p in fiches:
        f = json.loads(p.read_text(encoding="utf-8"))
        f0 = f.get("hauteur_parole_mediane_hz") or 0
        defaut = " (défaut)" if p.parent.name == VOIX_DEFAUT else ""
        print(f"{p.parent.name:14s} {(f.get('nom') or '') + defaut:16s} {f0:6.1f} Hz {FACTEUR_CHANT * f0:5.0f} Hz "
              f"{f.get('duree_s', 0):5.1f} s  {f.get('date', ''):10s}  {f.get('origine', '')[:70]}")


def ajouter_voix(nom, enregistrement):
    """Version minimale : l'enregistrement est copié tel quel comme référence (aucune analyse)."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", nom):
        raise SystemExit("nom de voix : minuscules, chiffres, - ou _ (ex. marie, paul-2)")
    src = Path(enregistrement).expanduser().resolve()
    if not src.is_file():
        raise SystemExit(f"fichier introuvable : {src}")
    if VOIX is None:
        raise SystemExit("faux doubler : dossier des voix inconnu")
    d = verifier_ecriture(VOIX / nom)
    if (d / "voix.json").exists():
        raise SystemExit(f"la voix {nom} existe déjà ({d}) : choisir un autre nom ou effacer ce dossier")
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, d / "reference.wav")
    fiche = {"nom": nom.replace("-", " ").replace("_", " ").title(), "reference": "reference.wav", "duree_s": 20.0,
             "frequence_hz": 44100, "canaux": 1, "sonie_lufs": -26.0, "hauteur_parole_mediane_hz": 100.0,
             "hauteur_parole_10_90_hz": [90.0, 115.0], "note_mediane": "G2", "origine": f"{src} (faux doubler)",
             "consentement": "à confirmer : uniquement la voix d'une personne d'accord pour être clonée",
             "parole_s": 20.0, "rapport_signal_bruit_db": 40.0, "alertes": ["faux doubler : aucune analyse"],
             "date": datetime.date.today().isoformat()}
    (d / "voix.json").write_text(json.dumps(fiche, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"voix {nom} ajoutée ({d}) : 20.0 s, parole 100 Hz (G2), centre de chant 200 Hz ; envoyée sur Kaggle "
          "(privé) à la première utilisation")
    for al in fiche["alertes"]:
        print("  ATTENTION :", al)


# --- Une vidéo -------------------------------------------------------------------------------------------------------

def fmt_t(t):
    return f"{int(t // 60)}m{t % 60:04.1f}".replace(".", ",")


def chrono(t):
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


TEMPS = r"(?:\d+:)?\d+(?:[.,]\d+)?"
EXTRAIT = re.compile(rf"(?P<debut>{TEMPS})?(?:\+(?P<duree>{TEMPS}))?")


def secondes(texte):
    m, _, sec = texte.replace(",", ".").rpartition(":")
    return float(sec) + 60 * (int(m) if m else 0)


def lire_video(arg, a):
    """« vidéo » ou « vidéo@DEBUT+DUREE », comme doubler."""
    arg = str(arg)
    chemin, _, spec = arg.rpartition("@")
    if "@" not in arg or Path(arg).expanduser().exists() or not spec:
        return arg, a.debut, a.duree
    m = EXTRAIT.fullmatch(spec)
    if not m or not (m["debut"] or m["duree"]):
        return arg, a.debut, a.duree
    return chemin, secondes(m["debut"]) if m["debut"] else None, secondes(m["duree"]) if m["duree"] else None


def emplacements(video, debut, duree, a, voix):
    video = Path(video).expanduser().resolve()
    if not video.is_file():
        raise RuntimeError(f"fichier introuvable : {video}")
    nom = video.stem
    if debut is not None or duree is not None:
        nom += f" (extrait {fmt_t(debut or 0)}{'+' + fmt_t(duree) if duree else ''})"
    dossier = verifier_ecriture(Path(a.sortie).expanduser().resolve() / nom)
    return {"video": video, "nom": nom, "dossier": dossier, "travail": dossier / "travail",
            "tv": dossier / "travail" / voix["id"], "base": f"{video.stem} - voix {voix['affichage']}"}


def duree_totale():
    try:
        return float(os.environ.get("FAUX_DOUBLER_DUREE") or 30.0)
    except ValueError:
        return 30.0


def preparer_source(video, debut, duree, travail):
    src = {"video": str(video), "flux_audio": 1, "langue": "fra", "canaux_origine": 2, "codec_origine": "aac"}
    audio = Path(str(video)).suffix.lower() in SONS
    if audio:
        src["audio_seul"] = True
    if debut is not None or duree is not None:
        demande = debut or 0.0
        k = demande if audio else max(0.0, demande - 0.04)   # « image clé » un peu avant, sauf pour un son seul
        fin = (demande + duree) if duree else max(duree_totale(), demande + 1.0)
        src.update({"debut_demande": demande, "debut": round(k if audio else k + 0.001, 3), "duree": round(fin - k, 3)})
    src["decalage_son"] = 0.0
    (travail / "original.wav").write_bytes(b"")
    with open(travail / "source.json", "w", encoding="utf-8") as f:
        json.dump(src, f, ensure_ascii=False, indent=1)
    return src


def preparer_travaux(videos, a, voix):
    travaux, echecs = [], []
    for i, v in enumerate(videos):
        titre = Path(str(v)).stem
        try:
            chemin, debut_v, duree_v = lire_video(v, a)
            tr = emplacements(chemin, debut_v, duree_v, a, voix)
            titre = tr["nom"]
            tr.update({"debut_epoch": time.time(), "id": f"v{i + 1}"})
            dire(f"=== {tr['video'].name} → {tr['dossier']}")
            with Etape("extraction", titre, f"son de {tr['video'].name}"):
                debut = time.time()
                pause()
                tr["travail"].mkdir(parents=True, exist_ok=True)
                tr["tv"].mkdir(parents=True, exist_ok=True)
                src = preparer_source(tr["video"], debut_v, duree_v, tr["travail"])
                if "debut" in src:
                    dire(f"extrait : {src['debut']:.3f} s (image clé avant {src['debut_demande']:g} s), {src['duree']:.3f} s")
                tr["duree_audio_s"] = src.get("duree", duree_totale())
                tr["extraction_s"] = round(time.time() - debut, 1)
            travaux.append(tr)
        except Exception as e:  # noqa: BLE001
            dire(f"ÉCHEC {v} : {e}")
            EV("erreur", titre, message=str(e))
            echecs.append(str(v))
    return travaux, echecs


def rapport_kaggle(tr, a, voix):
    """Ce que le noyau rendrait : rapport.json minimal (mode, durée, morceaux, frise, temps)."""
    d = float(tr["duree_audio_s"])
    mode = a.mode if a.mode != "auto" else "chant"
    if mode == "chant":
        dec_femme = {"auto": -12, "0": 0, "-12": -12, "+12": 12}.get(a.octave, -12)
        dec_homme = {"auto": 0, "0": 0, "-12": -12, "+12": 12}.get(a.octave, 0)
        morceaux = [
            {"i": 0, "debut_s": 0.0, "fin_s": round(d * 0.1, 2), "muet": True, "f0_med": None, "voise_s": 0.0,
             "decalage": dec_homme, "base": "muet"},
            {"i": 1, "debut_s": round(d * 0.1, 2), "fin_s": round(d * 0.6, 2), "muet": False, "f0_med": 180.0,
             "voise_s": round(d * 0.4, 2), "voix": "homme", "decalage": dec_homme, "base": "voix d'homme"},
            {"i": 2, "debut_s": round(d * 0.6, 2), "fin_s": round(d, 2), "muet": False, "f0_med": 330.0,
             "voise_s": round(d * 0.3, 2), "voix": "femme", "decalage": dec_femme, "base": "voix de femme"},
        ]
        decision = {"decalage_par_genre": {"homme": dec_homme, "femme": dec_femme}, "voix_seule": None,
                    "f0_mediane_globale_hz": 230.0, "part_homme": 0.57}
    else:
        morceaux = [{"i": 0, "debut_s": 0.0, "fin_s": round(d, 2), "muet": False, "f0_med": 120.0,
                     "voise_s": round(d * 0.6, 2), "voix": 1, "decalage": -2, "base": "parole"}]
        decision = {"voix": 1, "f0_mediane_globale_hz": 120.0}
    frise = []
    for m in morceaux:
        if m["muet"]:
            continue
        v = f"voix {m['voix']}" if m.get("voix") else "       "
        frise.append(f"{chrono(m['debut_s'])}–{chrono(m['fin_s'])}  {v}  f0 {m['f0_med'] or 0:4.0f} Hz  → "
                     f"{m['decalage']:+g} dt  ({m['base']})")
    conv = round(d * 0.045 * a.pas, 1)
    plus = {}
    if voix.get("membres") and mode == "chant":
        # une voix par chanteur (comme doubler_kaggle.py) : le chanteur (morceau 1) et la chanteuse (morceau 2), les
        # deux ensemble à la fin du morceau 2 (voix principale et chœurs chacun dans sa voix)
        m1, m2 = voix["membres"][0]["id"], voix["membres"][1]["id"]
        morceaux[0]["voix_cible"] = m1
        morceaux[1].update({"voix_cible": m1, "voix_pistes": {"lead_sec": m1}})
        morceaux[2].update({"voix_cible": m2, "voix_pistes": {"lead_sec": m2, "choeurs_sec": m1}})
        fin_duo = round(d * 0.8, 2)
        plus = {
            "voix_cibles": {"mode": voix["mode"], "voix": [{"cle": m["cle"], "nom": m["id"], "affichage": m["affichage"],
                                                            "centre_chant_hz": round(FACTEUR_CHANT * m["f0"], 1)}
                                                           for m in voix["membres"]]},
            "affectation": {"mode": voix["mode"], "message": "duo", "voix_utilisees": [m1, m2], "morceaux_en_duo": 1,
                            "passages": [{"debut_s": round(d * 0.1, 2), "fin_s": round(d * 0.6, 2), "voix": [m1]},
                                         {"debut_s": round(d * 0.6, 2), "fin_s": fin_duo, "voix": [m2]},
                                         {"debut_s": fin_duo, "fin_s": round(d, 2), "voix": [m1, m2]}],
                            "secondes_par_voix": {m1: round(d * 0.5, 1), m2: round(d * 0.2, 1), "ensemble": round(d * 0.2, 1)}},
        }
        decision = {"voix": 2, "axe": "genre", "message": f"duo : homme → {m1} (+0), femme → {m2} (+0)",
                    "chanteurs": [{"nom": "homme", "classe": "homme", "voix": m1, "decalage": dec_homme},
                                  {"nom": "femme", "classe": "femme", "voix": m2, "decalage": 0}],
                    "f0_mediane_globale_hz": 230.0}
        morceaux[2]["decalage"] = 0
        noms_v = [m["id"] for m in voix["membres"]]
        if len(noms_v) > 2:   # trois voix ou plus : un passage par voix, puis toutes ensemble à la fin
            nb = len(noms_v)
            bornes = [round(d * (0.1 + 0.7 * k / nb), 2) for k in range(nb + 1)]
            plus["voix_cibles"]["voix"] = [{"cle": m["cle"], "nom": m["id"], "affichage": m["affichage"],
                                            "centre_chant_hz": round(FACTEUR_CHANT * m["f0"], 1)} for m in voix["membres"]]
            plus["affectation"] = {
                "mode": voix["mode"], "message": f"{nb} voix", "voix_utilisees": noms_v, "morceaux_en_duo": 1,
                "passages": [{"debut_s": bornes[k], "fin_s": bornes[k + 1], "voix": [noms_v[k]]} for k in range(nb)]
                            + [{"debut_s": bornes[nb], "fin_s": round(d, 2), "voix": noms_v[:2]}],
                "secondes_par_voix": {**{n: round(d * 0.7 / nb, 1) for n in noms_v}, "ensemble": round(d * 0.2, 1)}}
            decision = {"voix": nb, "axe": "empreintes", "message": f"{nb} voix : " + ", ".join(noms_v),
                        "chanteurs": [{"nom": f"voix {k + 1}", "voix": n, "decalage": 0} for k, n in enumerate(noms_v)],
                        "f0_mediane_globale_hz": 230.0}
            m1, m2 = noms_v[0], noms_v[1]
        # les deux versions de chaque passage chanté (doubler --versions-duo toutes, défaut d'une voix par chanteur)
        if not SANS_VERSIONS:
            plus["versions"] = ({"mode": "non"} if os.environ.get("FAUX_DOUBLER_VERSIONS") == "non" else
                                {"mode": "toutes" if len(noms_v) == 2 else "utiles", "dossier": "versions",
                                 "voix_cibles": noms_v, "decalages": {n: dec_homme if k == 0 else 0 for k, n in enumerate(noms_v)},
                                 "morceaux": [1, 2], "secondes": round(d * 0.9, 1),
                                 "conversion_s": conv, "conversion_auto_s": conv, "surcout": float(len(noms_v) - 1)})
    return {
        **plus,
        "id": tr["id"], "reglages": {"pas": a.pas}, "voix_cible": voix["id"],
        "travail": {"entree": f"entree-{tr['id']}.flac"},
        "temps": {"separation_s": round(d * 0.7, 1), "analyse_s": round(d * 0.04, 1), "conversion_s": conv,
                  "total_video_s": round(d * 0.74 + conv, 1)},
        "mesures_musique": {"reste_sous_voix_db": -8.5, "tonalite_reste": 0.62, "tenue": 0.31},
        "mode": mode, "mode_force": a.mode != "auto", "decision_hauteur": decision,
        "modele": "chant44k" if mode == "chant" else "parole22k",
        "variantes": {"voix_convertie.flac": {"modele": "chant44k" if mode == "chant" else "parole22k",
                                              "pas_diffusion": a.pas, "conversion_s": conv}},
        "duree_s": round(d, 3), "morceaux": morceaux, "frise": frise,
    }


def ecrire_paroles(tv, a):
    """travail/<voix>/paroles.json (paroles inventées), sauf avec --sans-paroles ou la clé « paroles » à non ; un
    paroles.json déjà là (remix) est gardé. Rend son chemin, ou None."""
    refus = str(valeur_config(lire_config(), "paroles")).strip().lower() in ("false", "non", "faux", "0")
    if ANCIEN or getattr(a, "sans_paroles", False) or refus:
        return None
    f = tv / "paroles.json"
    if not f.exists():
        segments = [{"debut": d, "fin": fin, "texte": texte,
                     "mots": [{"debut": round(d + k * (fin - d) / len(texte.split()), 3),
                               "fin": round(d + (k + 1) * (fin - d) / len(texte.split()), 3), "mot": m}
                              for k, m in enumerate(texte.split())]}
                    for d, fin, texte in PAROLES_FAUSSES]
        verifier_ecriture(f)
        f.write_text(json.dumps({"format": 1, "langue": "fr", "source": "faux-whisper", "segments": segments},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    return f


FICHIERS_AUTOTUNE_TRAVAIL = ("mix_autotune.wav", "seche_voix_autotune.wav", "reverb_voix_autotune.wav")
TONIQUES_FAUX = ("do", "do#", "re", "mib", "mi", "fa", "fa#", "sol", "lab", "la", "sib", "si")


def lire_gamme_faux(gamme):
    """« la-mineur » → (9, « mineur ») ; « sib » → (10, « majeur ») ; auto, chromatique ou une autre écriture (« Am ») :
    (None, None). Le vrai doubler lit toutes les écritures (lire_gamme) ; le faux, celles de l'interface."""
    tonique, _, mode = str(gamme or "").strip().lower().partition("-")
    if tonique not in TONIQUES_FAUX:
        return None, None
    return TONIQUES_FAUX.index(tonique), "mineur" if mode in ("mineur", "min", "m") else "majeur"


def autotune(a, rapport, tv, dossier, base, sortie, mode):
    """Version auto-tune, comme doubler : --sans-autotune la retire ; --autotune N, la clé « autotune » ou une version
    déjà là (remix) la font (fichiers vides) ; greffon absent (FAUX_DOUBLER_X42=absent) : refusée, avec la raison.
    Écrit rapport["autotune"] et rend l'événement « autotune » (ou None)."""
    if ANCIEN:
        return None
    b = f"{base} (auto-tune)"
    if a.sans_autotune:
        sorties = [f for f in (dossier / f"{b}{e}" for e in (*CONTENEURS, ".mp3", ".lrc")) if f.exists()]
        travail = [f for f in (tv / n for n in FICHIERS_AUTOTUNE_TRAVAIL) if f.exists()]
        for f in sorties + travail:
            f.unlink()      # jamais la vraie corbeille du bureau pendant les essais
        dire("version auto-tune retirée : " + ", ".join([f"{f.name} (effacé, faux doubler)" for f in sorties]
                                                       + [f"travail/{tv.name}/{f.name} (effacé)" for f in travail])
             if sorties or travail else "--sans-autotune : pas de version auto-tune à retirer")
        rapport["autotune"] = {"applique": False, "intensite": 0, "source": "option --sans-autotune",
                               "raison": "version auto-tune retirée (--sans-autotune)",
                               "retires": {"corbeille": [], "effaces": [f.name for f in sorties],
                                           "travail": [f.name for f in travail]}}
        return None
    prec = rapport.get("autotune") if isinstance(rapport.get("autotune"), dict) else {}
    # comme doubler.reglage_pour : --autotune N l'emporte ; sans option, une version déjà là est gardée à jour (même
    # intensité, même gamme demandée sauf --autotune-gamme) ; sinon la clé « autotune » de la configuration
    gardee = a.autotune is None and bool(prec.get("applique") and prec.get("intensite"))
    n = a.autotune if a.autotune is not None else prec["intensite"] if gardee else autotune_config(lire_config())
    if not n:
        return None
    if mode == "parole" and a.autotune is None:
        rapport["autotune"] = {"applique": False, "intensite": n, "raison": "parole : auto-tune seulement sur demande"}
        return None
    if x42() is None:
        dire("ATTENTION : auto-tune demandé, mais x42-autotune absent : installez x42-plugins : sudo apt install "
             "x42-plugins ; version normale seule")
        rapport["autotune"] = {"applique": False, "intensite": n, "raison": "x42-autotune absent : installez x42-plugins"}
        return None
    for f in FICHIERS_AUTOTUNE_TRAVAIL if mode == "chant" else FICHIERS_AUTOTUNE_TRAVAIL[:2]:
        (tv / f).write_bytes(b"")
    mp3, lrc = dossier / f"{b}.mp3", dossier / f"{b}.lrc"
    video = dossier / f"{b}{sortie.suffix}" if sortie is not None else None
    for f in (mp3, video, lrc):
        if f is not None:
            f.write_bytes(b"")
    gamme = a.autotune_gamme or ((prec.get("gamme") or {}).get("demandee") if gardee else None) or "auto"
    tonique, mode_g = lire_gamme_faux(gamme)
    chromatique = gamme == "chromatique"
    rapport["autotune"] = {
        "applique": True, "intensite": int(n), "explicite": a.autotune is not None,
        "source": ("option --autotune" if a.autotune is not None else
                   "doublage précédent (version auto-tune gardée à jour)" if gardee else "configuration"),
        "gamme": {"demandee": gamme, "type": "chromatique" if chromatique else "tonalite",
                  "tonalite": None if chromatique else f"{TONIQUES_FAUX[tonique]} {mode_g}" if mode_g else "fa majeur",
                  "tonique": None if chromatique else 5 if tonique is None else tonique,
                  "mode": None if chromatique else mode_g or "majeur",
                  "masque": [1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0], "accord_hz": 440.0, "detection": {"correlation": 0.81}},
        "mesures": {"avant": {"ecart_median_cents": 32.0, "part_a_10_cents": 0.21},
                    "apres": {"ecart_median_cents": 4.0, "part_a_10_cents": 0.86}},
        "gains_db": {"seche": 6.5, **({"reverb": 1.2} if mode == "chant" else {})}, "temps_s": 2.0,
        "fichiers": {"mp3": mp3.name, "mix": "mix_autotune.wav", "seche": "seche_voix_autotune.wav",
                     "reverb": "reverb_voix_autotune.wav" if mode == "chant" else None, "video": video.name if video else None, "lrc": lrc.name}}
    g = rapport["autotune"]["gamme"]
    dire(f"auto-tune {n} : gamme {g['tonalite'] or 'chromatique'}, écart médian aux notes 32 → 4 cents")
    return {"mp4": str(video) if video else None, "mp3": str(mp3), "lrc": str(lrc), "intensite": int(n), "tonalite": g["tonalite"],
            "gamme": g["type"]}


# --- Une voix par chanteur : correction à la main (--remix --affectation FICHIER|auto, --passage N=VOIX) -------------

SANS_LES_DEUX = ("ce doublage n'a pas les deux versions de chaque passage (doublé avant leur arrivée, ou avec "
                 "--versions-duo non) : refaites-le sans --remix pour pouvoir corriger qui chante quoi")
PISTES_ALIAS = {"principale": "lead_sec", "lead": "lead_sec", "lead_sec": "lead_sec", "choeurs": "choeurs_sec",
                "chœurs": "choeurs_sec", "choeurs_sec": "choeurs_sec", "secours": "secours", "voix": "voix"}


def voix_choisie(texte, voix):
    """Une valeur de --passage ou d'affectation.json : nom de voix, clé (homme, chanteur1…), « deux » ou « auto »."""
    t = str(texte).strip().lower()
    if t in ("deux", "les-deux", "les deux", "ensemble"):
        return "deux"
    if t == "auto":
        return "auto"
    for m in voix["membres"]:
        if t in (m["id"], m.get("cle")):
            return m["id"]
    raise RuntimeError(f"voix inconnue « {texte} » (voix : {', '.join(m['id'] for m in voix['membres'])}, deux ou auto)")


def appliquer_passage(spec, passages, pistes, voix):
    """« N=VOIX », « N-M=VOIX » ou « N/piste=VOIX » ajouté à l'affectation (auto : retiré)."""
    cible, sep, valeur = str(spec).partition("=")
    if not sep:
        raise RuntimeError(f"--passage {spec} : N=VOIX attendu")
    numeros, _, piste = cible.strip().partition("/")
    a, _, b = numeros.partition("-")
    try:
        bornes = range(int(a), int(b or a) + 1)
    except ValueError:
        raise RuntimeError(f"--passage {spec} : numéro de passage attendu") from None
    choix = voix_choisie(valeur, voix)
    for i in bornes:
        k = str(i)
        if piste:
            p = PISTES_ALIAS.get(piste.strip().lower())
            if p is None:
                raise RuntimeError(f"--passage {spec} : piste inconnue « {piste} »")
            d = pistes.setdefault(k, {})
            d.pop(p, None) if choix == "auto" else d.update({p: choix})
            if not d:
                pistes.pop(k)
        elif choix == "auto":
            passages.pop(k, None)
            pistes.pop(k, None)
        else:
            passages[k] = choix


def _lire_json(chemin):
    try:
        d = json.loads(Path(chemin).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def corriger_affectation(a, tv, rapport, voix):
    """Comme doubler : l'affectation gardée (affectation.json), remplacée par --affectation FICHIER (auto : vidée),
    complétée par --passage ; appliquée au rapport et à courbes.json depuis l'automatique (versions/auto.json, gardé à
    la première correction). Rend le nombre de passages corrigés (0 : l'automatique)."""
    demande = getattr(a, "affectation", None) or getattr(a, "passage", None)
    fichier, versions, auto = tv / "affectation.json", tv / "versions" / "versions.json", tv / "versions" / "auto.json"
    if not voix.get("membres") or rapport.get("mode") != "chant":
        if demande:
            raise RuntimeError("--affectation et --passage : seulement avec une voix par chanteur, en chant")
        return 0
    gardee = _lire_json(fichier) or {}
    if a.affectation == "auto":
        gardee = {}
    elif a.affectation:
        gardee = _lire_json(Path(a.affectation).expanduser())
        if gardee is None:
            raise RuntimeError(f"--affectation {a.affectation} : fichier JSON illisible")
    passages = {str(k): voix_choisie(v, voix) for k, v in (gardee.get("passages") or {}).items()}
    pistes = {str(k): {PISTES_ALIAS.get(p, p): voix_choisie(x, voix) for p, x in d.items()}
              for k, d in (gardee.get("pistes") or {}).items() if isinstance(d, dict)}
    passages = {k: v for k, v in passages.items() if v != "auto"}
    for spec in getattr(a, "passage", None) or []:
        appliquer_passage(spec, passages, pistes, voix)
    if (passages or pistes) and not versions.is_file():
        raise RuntimeError(SANS_LES_DEUX)
    dispo = {int(i) for i in (_lire_json(versions) or {}).get("morceaux") or []}
    for k in list(passages) + list(pistes):
        if int(k) not in dispo:
            raise RuntimeError(f"passage {k} : pas de version dans les deux voix (pas un passage chanté)")
    courbes = tv / "courbes.json"
    if (passages or pistes) and not auto.exists():
        auto.write_text(json.dumps({"affectation": {k: v for k, v in (rapport.get("affectation") or {}).items()
                                                    if k != "manuelle"},
                                    "morceaux": rapport.get("morceaux"), "frise": rapport.get("frise"),
                                    "courbes": _lire_json(courbes)}, ensure_ascii=False), encoding="utf-8")
    snap = _lire_json(auto)
    if not passages and not pistes:
        fichier.unlink(missing_ok=True)
        (tv / "voix_corrigee.flac").unlink(missing_ok=True)
        if snap:
            rapport.update({"affectation": snap["affectation"], "morceaux": snap["morceaux"], "frise": snap["frise"]})
            if snap.get("courbes"):
                courbes.write_text(json.dumps(snap["courbes"]), encoding="utf-8")
        if demande:
            dire("affectation automatique (corrections à la main retirées)")
        return 0
    noms = [m["id"] for m in voix["membres"]]
    fichier.write_text(json.dumps({"format": 1, "voix_cibles": noms, "passages": passages,
                                   **({"pistes": pistes} if pistes else {})}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    chanteurs = {c.get("voix"): c for c in (rapport.get("decision_hauteur") or {}).get("chanteurs") or []}
    morceaux = json.loads(json.dumps(snap["morceaux"]))
    corriges = sorted({int(k) for k in list(passages) + list(pistes)})
    for m in morceaux:
        vpa = m.get("voix_pistes") or ({"lead_sec": m["voix_cible"]} if m.get("voix_cible") and not m.get("muet") else {})
        if m.get("muet") or not vpa:
            continue
        k = str(m["i"])
        nouveau = {}
        for piste, v in vpa.items():
            choix = (pistes.get(k) or {}).get(piste) or passages.get(k)
            nouveau[piste] = (list(noms) if choix == "deux" else choix) if choix else v
        m["voix_pistes_auto"], m["voix_pistes"] = vpa, nouveau
        if int(k) in corriges:
            m["corrige"] = True
            choix = passages.get(k)
            if choix == "deux":
                m["base"] = "corrigé à la main : les deux"
            elif choix:
                c = chanteurs.get(choix) or {}
                m.update({"voix_cible": choix, "voix": c.get("classe") or m.get("voix"),
                          "decalage": c.get("decalage", m.get("decalage")), "base": f"corrigé à la main : {choix}"})
            else:
                m["base"] = "corrigé à la main, piste par piste"
    # passages de la frise recalculés simplement à partir des morceaux (le vrai doubler les refait trame par trame)
    par = []
    for m in morceaux:
        if m.get("muet") or not m.get("voix_pistes"):
            continue
        v = sorted({n for x in m["voix_pistes"].values() for n in (x if isinstance(x, list) else [x])}, key=noms.index)
        if par and par[-1]["voix"] == v and abs(par[-1]["fin_s"] - m["debut_s"]) < 0.05:
            par[-1]["fin_s"] = m["fin_s"]
        else:
            par.append({"debut_s": m["debut_s"], "fin_s": m["fin_s"], "voix": v})
    sec = {n: round(sum(p["fin_s"] - p["debut_s"] for p in par if p["voix"] == [n]), 1) for n in noms}
    sec["ensemble"] = round(sum(p["fin_s"] - p["debut_s"] for p in par if len(p["voix"]) > 1), 1)
    duo = sum(1 for m in morceaux if len({n for x in (m.get("voix_pistes") or {}).values()
                                         for n in (x if isinstance(x, list) else [x])}) >= 2)
    rapport["affectation"] = {**snap["affectation"], "passages": par, "secondes_par_voix": sec, "morceaux_en_duo": duo,
                              "manuelle": {"fichier": "affectation.json", "passages": passages, "pistes": pistes,
                                           "corriges": corriges, "voix_convertie": "voix_corrigee.flac"}}
    rapport["morceaux"] = morceaux
    (tv / "voix_corrigee.flac").write_bytes(b"")
    c = snap.get("courbes")
    if c and c.get("voix_par_trame") is not None:
        vt = []
        for k in range(len(c["voix_par_trame"])):
            t = k * float(c.get("pas_s") or 0.02)
            p = next((p for p in par if p["debut_s"] <= t < p["fin_s"]), None)
            vt.append(-1 if p is None else len(noms) if len(p["voix"]) > 1 else noms.index(p["voix"][0]))
        courbes.write_text(json.dumps({**c, "voix_par_trame": vt}), encoding="utf-8")
    dire(f"affectation corrigée à la main : {len(corriges)} passage(s) ({', '.join(map(str, corriges))}) ; "
         "voix_corrigee.flac refaite depuis les deux versions")
    return len(corriges)


def finir(tr, a, voix, temps_local=None):
    """Mixage, MP3, remise sur la vidéo depuis travail/ (fichiers vides), comme doubler.finir."""
    global _mix_rate
    t, tv, dossier, base = tr["travail"], tr["tv"], tr["dossier"], tr["base"]
    for f in (t / "original.wav", t / "voix_source.wav", t / "source.json", tv / "voix_convertie.flac", tv / "rapport.json"):
        if not f.exists():
            raise RuntimeError(f"travail incomplet ({f} manque) : relancer sans --remix")
    rapport = json.loads((tv / "rapport.json").read_text(encoding="utf-8"))
    mode = rapport["mode"]
    voix_db = a.voix_db if a.voix_db is not None else (4.0 if mode == "chant" else 0.0)
    version = 1 if getattr(a, "mix_v1", False) else 2
    titre = tr["nom"]
    debut = debut0 = time.time()
    cible = -20.0
    if getattr(a, "remix", False) or (tv / "affectation.json").exists():
        corriger_affectation(a, tv, rapport, voix)   # une erreur : « ÉCHEC », rien n'est mixé (comme doubler)
    with Etape("mix", titre, f"mixage v{version} ({mode})"):
        pause()
        if ECHEC == "mix" and not _mix_rate:
            _mix_rate = True
            raise RuntimeError(f"échec : ffmpeg -i {t / 'instrumental.wav'}…\nInvalid data found when processing input (faux)")
        mp3 = dossier / f"{base}.mp3"
        mp3.write_bytes(b"")
        suite = f", reverb {a.reverb_db:+g} dB sous la voix" if mode == "chant" else ", sans reverb, fond sonore gardé"
        if ANCIEN:
            dire(f"mixage {mode} : voix source {cible:.1f} dB, voix {voix_db:+g} dB" + suite)
        else:
            detail = (" (ancien mixage : ni nuances ni dé-esseur)" if version < 2
                      else " ; nuances de la voix d'origine -2.1 à +1.8 dB (p5-p95), « s » adoucis")
            dire(f"mixage {mode} v{version} : voix source {cible:.1f} dB, voix {voix_db:+g} dB" + suite + detail)
    mix_s = time.time() - debut
    debut = time.time()
    src = json.loads((t / "source.json").read_text(encoding="utf-8"))
    audio = bool(src.get("audio_seul"))
    with Etape("video", titre, "son seul : pas d'image, mp3 et paroles" if audio else "son remis sur la vidéo (image copiée)"):
        pause()
        ext = Path(src["video"]).suffix.lower()
        sortie = mp3 if audio else dossier / f"{base}{ext if ext in CONTENEURS else '.mkv'}"
        sortie.write_bytes(b"")
    video_s = time.time() - debut
    rapport["mix"] = {"version": version, "voix_db": voix_db, "reverb_db": a.reverb_db if mode == "chant" else None,
                      "voix_source_db": cible}
    if temps_local is not None:
        temps_local.update({"mix_local_s": round(mix_s, 1), "remise_video_s": round(video_s, 1)})
        rapport["temps_local"] = temps_local
    (tv / "rapport.json").write_text(json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8")
    paroles = ecrire_paroles(tv, a)
    ev_at = autotune(a, rapport, tv, dossier, base, None if audio else sortie, mode)
    (tv / "rapport.json").write_text(json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8")
    EV("sortie", titre, mp4=None if audio else str(sortie), mp3=str(mp3), rapport=str(tv / "rapport.json"), comparaisons=None,
       **({"paroles": str(paroles)} if paroles else {}), autotune=ev_at, voix=[v["id"] for v in voix_membres(voix)])
    m = rapport.get("mesures_musique", {})
    dire(f"mode {mode} ({'imposé' if rapport.get('mode_force') else 'détecté'} : musique sous la voix "
         f"{m.get('reste_sous_voix_db')} dB, tonalité {m.get('tonalite_reste')}, notes tenues {m.get('tenue')}) ; "
         f"modèle {rapport.get('modele')}, {rapport.get('variantes', {}).get('voix_convertie.flac', {}).get('pas_diffusion')} pas")
    print("  hauteur par passage (dt = demi-tons) :", flush=True)
    for ligne in rapport.get("frise", []):
        print("   ", ligne, flush=True)
    dire(f"fini : {sortie}" + ("" if audio else f"\n         {mp3}"))
    EV("etape", titre, etape="fini", etat="fin", message=str(sortie), duree_s=round(time.time() - tr.get("debut_epoch", debut0), 1))
    return sortie


# --- La session « Kaggle » (simulée) -----------------------------------------------------------------------------------

class Verrou:
    """Verrou de session comme doubler : <sorties>/.kaggle/verrou (et $VOIXLA_ETAT/verrou-kaggle si posée ; jamais
    ~/.local/state)."""

    def __init__(self, kdir, videos=()):
        self.chemins = [kdir / "verrou"]
        if os.environ.get("VOIXLA_ETAT"):
            self.chemins.insert(0, Path(os.environ["VOIXLA_ETAT"]).expanduser() / "verrou-kaggle")
        self.videos, self.fichiers = videos, []

    def __enter__(self):
        etape = None
        for c in self.chemins:
            c.parent.mkdir(parents=True, exist_ok=True)
            f = open(c, "w")
            self.fichiers.append(f)
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if etape is None:
                    dire("Kaggle : un autre doublage utilise la session, attente de son tour")
                    etape = Etape("attente", self.videos, "un autre doublage utilise Kaggle : attente de son tour").__enter__()
                fcntl.flock(f, fcntl.LOCK_EX)
        if etape:
            etape.finir()
        return self

    def __exit__(self, *exc):
        for f in reversed(self.fichiers):
            f.close()
        self.fichiers = []
        return False


def envoyer_jeu(travaux, voix):
    pause(0.5)   # « encodage FLAC »
    taille_wav = sum(tr["duree_audio_s"] * 44100 * 2 * 4 for tr in travaux)
    for tr in travaux:
        tr["taille_envoi"] = int(tr["duree_audio_s"] * 44100 * 2 * 3 * 0.6)
        tr["gain_entree"] = 0.89
    taille = sum(tr["taille_envoi"] for tr in travaux)
    dire(f"Kaggle : envoi du son de {len(travaux)} vidéo(s) ({taille / 1e6:.0f} Mo en FLAC au lieu de {taille_wav / 1e6:.0f} Mo "
         f"en WAV) et de {'la voix ' if len(voix_membres(voix)) == 1 else 'les voix '}"
         f"{' et '.join(v['id'] for v in voix_membres(voix))} dans le jeu privé {JEU}")
    if ECHEC == "envoi":
        raise RuntimeError("Kaggle : jeu de données refusé : 403 - Forbidden (faux doubler)")
    pause(0.5)   # « téléversement et préparation du jeu »


def estimer_noyau_s(durees_s, pas):
    if os.environ.get("FAUX_DOUBLER_ESTIMATION"):
        return round(float(os.environ["FAUX_DOUBLER_ESTIMATION"]))
    return round(60 + 120.0 + sum(durees_s) * (0.8 + 0.045 * pas))


def lancer_noyau(travaux, titres, a, voix, estimation):
    """Poussée, suivi (queued, running, complete ou error), rapatriement : mêmes lignes et événements que doubler."""
    pause(0.5)
    dire("Kaggle : noyau GPU lancé (Kernel version 7 successfully pushed)")
    pousse = time.time()
    statuts = ["queued", "running", "running", "error" if ECHEC == "kaggle" else "complete"]
    if os.environ.get("FAUX_DOUBLER_COUPURE") == "1":
        statuts.insert(2, "injoignable")
    etape = Etape("kaggle", titres, f"noyau {NOYAU}" + (f", environ {estimation / 60:.0f} min" if estimation else "")).__enter__()
    dit, etat, k, panne_vue = "", "", 0, False
    for statut in statuts:
        pause()
        depuis = 6 + 12 * k   # minutes affichées comme une vraie session : 0.1, 0.3, 0.5…
        k += 1
        if statut == "injoignable":
            if not panne_vue:
                dire("Kaggle injoignable (pas de réponse en 120 s (réseau ?)) : nouvel essai à intervalles croissants, "
                     "jusqu'à 3 h de coupure")
                panne_vue = True
            for v in titres:
                EV("kaggle", v, statut="injoignable", depuis_s=depuis, panne_s=12, estimation_s=estimation)
            continue
        if panne_vue:
            dire("Kaggle de nouveau joignable, après 0 min de coupure")
            panne_vue = False
        etat = statut.upper()
        for v in titres:
            EV("kaggle", v, statut=statut, depuis_s=depuis, estimation_s=estimation)
        if etat != dit:
            dire(f"Kaggle : {statut} ({depuis / 60:.1f} min)")
            dit = etat
    etape.finir(None if etat == "COMPLETE" else f"noyau terminé en {etat.lower()}")
    noyau_s = round(time.time() - pousse, 1)
    debut_rap = time.time()
    rapatriement = Etape("rapatriement", titres, "téléchargement des résultats du noyau").__enter__()
    pause()
    if etat != "COMPLETE":
        rapatriement.finir(f"noyau terminé en {etat.lower()} sans résultat")
        raise RuntimeError(f"Kaggle : noyau terminé en {etat.lower()} sans résultat ; fin du journal :\n"
                           "torch.OutOfMemoryError: CUDA out of memory (faux noyau)")
    # sorties du « noyau » rangées dans travail/ et travail/<voix>/ (comparer : une par voix, la voix isolée commune)
    sorties = ([(sortie_de_voix(tr, v), v) for tr in travaux for v in voix["comparees"]] if voix.get("comparees")
               else [(tr, voix) for tr in travaux])
    for tr, voix in sorties:
        tr["tv"].mkdir(parents=True, exist_ok=True)
        (tr["travail"] / "voix_source.wav").write_bytes(b"")
        (tr["tv"] / "voix_convertie.flac").write_bytes(b"")
        # nouveau doublage : la correction à la main d'avant ne vaut plus (comme doubler au rapatriement)
        for f in ("affectation.json", "voix_corrigee.flac"):
            (tr["tv"] / f).unlink(missing_ok=True)
        shutil.rmtree(tr["tv"] / "versions", ignore_errors=True)
        rapport = rapport_kaggle(tr, a, voix)
        (tr["tv"] / "rapport.json").write_text(json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8")
        v = rapport.get("versions")
        if isinstance(v, dict) and v.get("mode") not in (None, "non"):
            (tr["tv"] / "versions").mkdir()
            (tr["tv"] / "versions" / "versions.json").write_text(json.dumps(
                {"format": 1, "mode": v["mode"], "voix_cibles": v["voix_cibles"], "morceaux": v["morceaux"],
                 "faux": True}), encoding="utf-8")
        if rapport.get("voix_cibles"):
            k = int(float(tr["duree_audio_s"]) / 0.02)
            vt = [-1 if i < 0.1 * k else 0 if i < 0.6 * k else 1 if i < 0.8 * k else 2 for i in range(k)]
            (tr["tv"] / "courbes.json").write_text(json.dumps(
                {"format": 1, "pas_s": 0.02, "source_hz": [0 if v < 0 else 200.0 for v in vt],
                 "convertie_hz": [0 if v < 0 else 200.0 for v in vt], "decalage_dt": [0] * k,
                 "voix_cibles": [m["id"] for m in voix["membres"]], "voix_par_trame": vt}), encoding="utf-8")
    temps = {"pousse_epoch": pousse, "version_noyau": 7, "noyau_depuis_envoi_s": noyau_s,
             "rapatriement_s": round(time.time() - debut_rap, 1)}
    return temps, rapatriement


def traiter_lot(videos, a, voix):
    lot_comparees = voix.get("comparees")
    travaux, echecs = preparer_travaux(videos, a, voix)
    if not travaux:
        return echecs
    titres = [tr["nom"] for tr in travaux]
    kdir = verifier_ecriture(Path(a.sortie).expanduser().resolve() / ".kaggle")
    kdir.mkdir(parents=True, exist_ok=True)

    def abandon(message):
        dire("ÉCHEC " + message)
        for t in titres:
            EV("erreur", t, message=message)
        return echecs + [str(tr["video"]) for tr in travaux]
    with Verrou(kdir, titres):
        debut = time.time()
        try:
            with Etape("envoi", titres, f"son de {len(travaux)} vidéo(s) en FLAC vers le jeu privé {JEU}"):
                envoyer_jeu(travaux, voix)
        except Exception as e:  # noqa: BLE001
            return abandon(f"de l'envoi sur Kaggle : {e}")
        envoi_s = round(time.time() - debut, 1)
        estimation = estimer_noyau_s([tr["duree_audio_s"] for tr in travaux], a.pas)
        try:
            temps, rapatriement = lancer_noyau(travaux, titres, a, voix, estimation)
        except Exception as e:  # noqa: BLE001
            return abandon(f"de la session Kaggle : {e}")
        rapatriement.finir()
    st = {"installation_s": 41.0, "separation_totale_s": 60.0, "chargement_modeles_s": 35.0,
          "total_s": round(temps["noyau_depuis_envoi_s"], 1)}
    cartes = None if SANS_GPU else cartes_utilisees(a)
    if cartes:
        vues = 2 if machine_config(lire_config()) == MACHINE_DEFAUT else 1
        dire(f"GPU Kaggle : {vues} carte(s) ({', '.join(['Tesla T4'] * vues)}), {min(cartes, vues)} utilisée(s)")
        cartes = min(cartes, vues)
    dire(f"GPU Kaggle : {temps['noyau_depuis_envoi_s'] / 60:.1f} min depuis l'envoi (démarrage 3.0 s, installation "
         f"{st['installation_s']:.0f} s, séparation {st['separation_totale_s']:.0f} s, modèles "
         f"{st['chargement_modeles_s']:.0f} s, noyau {st['total_s'] / 60:.1f} min ; estimé "
         f"{estimation / 60:.1f} min) ; rapatriement {temps['rapatriement_s']:.0f} s")
    cibles = [(tr, voix) for tr in travaux]
    if voix.get("comparees"):   # une sortie complète par voix, dans l'ordre demandé
        cibles = [(sortie_de_voix(tr, v), v) for tr in travaux for v in voix["comparees"]]
    for tr, voix in cibles:
        try:
            temps_local = {"extraction_s": tr.get("extraction_s"), "envoi_jeu_s": envoi_s,
                           "envoi_flac_mo": round(tr["taille_envoi"] / 1e6, 1), "gain_entree_db": -1.0,
                           "attente_demarrage_noyau_s": 3.0, "version_noyau": temps["version_noyau"],
                           "noyau_depuis_envoi_s": temps["noyau_depuis_envoi_s"], "estimation_noyau_s": estimation,
                           "rapatriement_s": temps["rapatriement_s"], "decodage_retour_s": 0.0, "session_kaggle": st,
                           "videos_dans_la_session": len(travaux)}
            if cartes:
                temps_local["gpus"] = cartes   # cartes utilisées par le noyau, comme doubler
            dire(f"=== {tr['video'].name}")
            finir(tr, a, voix, temps_local)
        except Exception as e:  # noqa: BLE001
            dire(f"ÉCHEC {tr['video']} : {e}")
            EV("erreur", tr["nom"], message=str(e))
            echecs.append(str(tr["video"]))
    for tr in travaux:
        if lot_comparees:
            ecrire_comparaison(tr, lot_comparees)
    return echecs


# --- Programme -------------------------------------------------------------------------------------------------------

def analyseur():
    p = argparse.ArgumentParser(prog="doubler", description=(AIDE_ANCIENNE if ANCIEN else AIDE).split("\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=(AIDE_ANCIENNE if ANCIEN else AIDE).split("\n", 1)[1])
    p.add_argument("videos", nargs="*", help="vidéo(s) mp4, mkv, webm, mov…, ou fichier(s) audio seul mp3, wav, flac, m4a, ogg, opus (sortie : mp3, "
                        "sans image) ; « vidéo@60+40 » : extrait propre à cette vidéo")
    p.add_argument("--voix", default=None, metavar="NOM",
                   help="voix cible : un dossier de <données>/voix (défaut : clé « voix » de la configuration, "
                        "sinon la seule voix présente) ; une voix par chanteur : homme:NOM,femme:NOM (duo mixte) ou "
                        "chanteur1:NOM,chanteur2:NOM[,chanteur3:NOM…] (même genre) ; une voix par personne : "
                        "personne1:NOM,personne2:NOM[,…]")
    if not SANS_COMPARER:
        p.add_argument("--comparer", default=None, metavar="A,B[,C…]",
                       help="comparer des voix : le même extrait doublé dans chaque voix, en une seule session ; "
                            "une sortie par voix ; remplace --voix")
    p.add_argument("--mode", choices=["auto", "chant", "parole"], default="auto")
    p.add_argument("--octave", choices=["auto", "0", "-12", "+12", "12"], default="auto",
                   help="auto : décidé passage par passage ; 0, -12 ou +12 : imposé partout")
    p.add_argument("--pas", type=int, default=30, help="pas de diffusion Seed-VC (défaut 30)")
    p.add_argument("--voix-db", type=float, default=None, help="voix par rapport à celle de la vidéo (défaut +4 en chant, 0 en parole)")
    p.add_argument("--reverb-db", type=float, default=-13, help="reverb sous la voix sèche, en chant (défaut -13)")
    if not ANCIEN:
        p.add_argument("--moteur", choices=["yingmusic", "seedvc"], default=None,
                       help="moteur de conversion du chant (défaut lu dans la configuration, clé « moteur »)")
        p.add_argument("--sans-nettoyage", action="store_true", help="chant : voix isolée complète, sans nettoyage")
        p.add_argument("--sans-paroles", action="store_true",
                       help="sans transcription des paroles ni traduction (défaut : clé « paroles » de la configuration)")
        p.add_argument("--mix-v1", action="store_true", help="ancien mixage : sans suivi des nuances ni dé-esseur")
        p.add_argument("--transposer", default=None, metavar="auto|non|N",
                       help="chant : transposer la chanson entière dans la tessiture de la voix cible (auto, non, ou N demi-tons)")
        p.add_argument("--autotune", nargs="?", type=int, const=70, default=None, metavar="N",
                       help="auto-tune de la voix, intensité N de 0 à 100 (70 sans valeur) : version « (auto-tune) » en plus")
        p.add_argument("--sans-autotune", action="store_true", help="pas d'auto-tune ; avec --remix : version retirée")
        p.add_argument("--autotune-gamme", default=None, metavar="GAMME",
                       help="gamme de l'auto-tune : auto, chromatique, ou une tonalité (la-mineur, do#-majeur…)")
    if not SANS_GPU:
        p.add_argument("--gpu", choices=list(GPU_CHOIX), default=None,
                       help="cartes GPU du noyau : auto (défaut : les deux T4 de la machine Kaggle), 1 ou 2 ; "
                            "défaut lu dans la configuration (clé « gpu »)")
    if not SANS_GPU:   # doubler d'avant --calcul : même cas que les deux GPU (0.5.0)
        p.add_argument("--calcul", choices=["kaggle", "modal", "runpod", "auto"], default=None,
                       help="où tourne le GPU : kaggle (défaut), modal, runpod (payant, jamais automatique) ou auto ; "
                            "le faux n'appelle ni Modal ni Runpod")
        p.add_argument("--nettoyer-runpod", action="store_true", help="supprime les pods Runpod voix-la-… restants (le faux : rien)")
    p.add_argument("--debut", type=float, default=None, help="début de l'extrait (s), pour un essai")
    p.add_argument("--duree", type=float, default=None, help="durée de l'extrait (s)")
    p.add_argument("--remix", action="store_true", help="refaire seulement le mixage et la vidéo depuis travail/ (sans Kaggle)")
    if not SANS_AFFECTATION:
        p.add_argument("--affectation", default=None, metavar="FICHIER|auto",
                       help="avec --remix, une voix par chanteur : affectation corrigée à la main (JSON), ou auto pour "
                            "revenir à l'automatique ; gardée dans travail/<a+b>/affectation.json")
        p.add_argument("--passage", action="append", default=None, metavar="N=VOIX",
                       help="avec --remix : passage N (N-M, N/piste) chanté par VOIX (nom, homme, femme, deux, auto)")
    if not ANCIEN:
        p.add_argument("--reprendre", action="store_true", help="finir la dernière session Kaggle interrompue")
        p.add_argument("--donnees", default=None, metavar="DIR", help="dossier des données voix/ et sorties/")
    p.add_argument("--sortie", default=None, help="dossier des sorties (défaut <données>/sorties)")
    if not ANCIEN:
        p.add_argument("--evenements", default=None, metavar="FICHIER",
                       help="écrit l'avancement en JSON, un événement par ligne (pour une interface)")
    p.add_argument("--comparer-pas", type=int, default=None, metavar="N", help="convertit aussi ~30 s en N pas")
    p.add_argument("--ajouter-voix", nargs=2, metavar=("NOM", "ENREGISTREMENT"), help="ajoute une voix depuis un enregistrement")
    p.add_argument("--voix-liste", action="store_true", help="affiche les voix disponibles")
    if not ANCIEN:
        p.add_argument("--json", action="store_true", help="avec --voix-liste ou --config : sortie en JSON")
        p.add_argument("--config", nargs="*", default=None, metavar="CLE VALEUR",
                       help=f"sans argument : affiche la configuration ; avec CLE VALEUR : l'écrit (clés : {', '.join(CLES_CONFIG)})")
        p.add_argument("--preparer-outils", action="store_true", help="prépare une fois sur Kaggle les paquets et les modèles")
    p.add_argument("--modele-parole", choices=["parole22k", "chant44k"], default=None, help=argparse.SUPPRESS)
    p.add_argument("--comparer-modeles", action="store_true", help=argparse.SUPPRESS)
    return p


def lancer_enfant():
    """FAUX_DOUBLER_ENFANT : un enfant du même groupe, sourd à SIGTERM (seul SIGKILL l'arrête)."""
    f = os.environ.get("FAUX_DOUBLER_ENFANT")
    if not f:
        return None
    enfant = subprocess.Popen([sys.executable, "-c", "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                               "time.sleep(120)"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    Path(f).write_text(str(enfant.pid))
    return enfant


def main():
    tracer(argv=sys.argv[1:], debut=time.time(), ancien=ANCIEN,
           env={k: os.environ.get(k) for k in ("VOIXLA_DONNEES", "DOUBLER_VOIX", "VOIXLA_CONFIG", "PYTHONPATH")})
    p = analyseur()
    a = p.parse_args()
    if ANCIEN:
        a.evenements = a.donnees = a.config = a.autotune = a.autotune_gamme = None
        a.json = a.mix_v1 = a.reprendre = a.preparer_outils = a.sans_autotune = False
    if SANS_AFFECTATION:
        a.affectation = a.passage = None
    if SANS_COMPARER:
        a.comparer = None
    EV.ouvrir(a.evenements)
    code = 1
    enfant = None
    try:
        enfant = lancer_enfant()
        code = executer(p, a)
        return code
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if not isinstance(e.code, (int, type(None))):
            EV("erreur", None, message=str(e.code))
        raise
    except KeyboardInterrupt:
        code = 130
        EV("erreur", None, message="interrompu")
        raise
    except BaseException as e:
        EV("erreur", None, message=f"{type(e).__name__} : {e}")
        raise
    finally:
        EV("fin", None, code=code)
        if enfant is not None:
            enfant.kill()
            enfant.wait()
        tracer(fin=time.time(), code=code)


def executer(p, a):
    if a.config is not None:
        if len(a.config) not in (0, 2):
            p.error("--config : aucun argument (afficher) ou CLE VALEUR (écrire)")
        if a.config:
            ecrire_config(*a.config)
            return 0
        configurer(a)
        afficher_config(a, a.json)
        return 0
    configurer(a)
    if a.preparer_outils:
        raise SystemExit("faux doubler : --preparer-outils n'est pas imité (il faudrait Kaggle)")
    if a.voix_liste:
        liste_voix(a.json)
        return 0
    if a.ajouter_voix:
        ajouter_voix(*a.ajouter_voix)
        return 0
    if a.reprendre:
        raise SystemExit("rien à reprendre : pas de session en cours (faux doubler)")
    if not a.videos:
        p.error("aucune vidéo")
    if not a.sortie:
        raise SystemExit("faux doubler (ancien) : --sortie manque")
    verifier_ecriture(a.sortie)
    if a.octave == "12":
        a.octave = "+12"
    if a.comparer is not None:
        if a.voix is not None:
            p.error("--comparer remplace --voix : les voix à comparer sont toutes dans --comparer A,B,C")
        voix = charger_voix_comparees(a.comparer)
    else:
        if a.voix is None:
            a.voix = exiger_voix_defaut()
        voix = charger_voix_spec(a.voix)
    if a.remix:
        echecs = []
        for v in a.videos:
            titre = Path(str(v)).stem
            try:
                chemin, debut_v, duree_v = lire_video(v, a)
                tr = emplacements(chemin, debut_v, duree_v, a, voix)
                titre = tr["nom"]
                tr["debut_epoch"] = time.time()
                if voix.get("comparees"):
                    for vj in voix["comparees"]:
                        dire(f"=== {tr['video'].name} (remix, voix {vj['id']})")
                        finir(sortie_de_voix(tr, vj), a, vj)
                    ecrire_comparaison(tr, voix["comparees"])
                    continue
                dire(f"=== {tr['video'].name} (remix, voix {voix['id']})")
                finir(tr, a, voix)
            except Exception as e:  # noqa: BLE001
                dire(f"ÉCHEC {v} : {e}")
                EV("erreur", titre, message=str(e))
                echecs.append(v)
    else:
        echecs = traiter_lot(a.videos, a, voix)
    if echecs:
        sys.exit(f"{len(echecs)} vidéo(s) en échec : {', '.join(map(str, echecs))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
