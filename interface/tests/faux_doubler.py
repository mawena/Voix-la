#!/usr/bin/env python3
"""Faux doubler pour les essais de l'interface Voix-là, choisi par VOIXLA_DOUBLER="python3 …/faux_doubler.py".

Il imite doubler.py : mêmes options (aide argparse), mêmes lignes de texte « HH:MM:SS … » dans le même ordre, mêmes
événements (--evenements), mêmes fichiers de sortie (travail/source.json, travail/<voix>/rapport.json minimal,
fichiers .mp4/.mp3 vides). AUCUN appel réseau, aucun Kaggle, aucun ffmpeg : chaque étape est une courte pause.
Il meurt normalement sur SIGTERM (essai d'annulation).

Variables d'environnement :
- FAUX_DOUBLER_VITESSE : secondes par étape (défaut 0.2) ;
- FAUX_DOUBLER_ANCIEN=1 : ancien doubler, sans --evenements, --donnees, --config, --json ni --mix-v1 (voix lues dans
  $DOUBLER_VOIX, sorties dans --sortie, ligne « mixage chant : … » sans numéro de version) ;
- FAUX_DOUBLER_ECHEC : « kaggle » (le noyau finit en erreur : ligne « ÉCHEC de la session Kaggle : … », code 1),
  « envoi » (jeu de données refusé) ou « mix » (le mixage de la première vidéo échoue, les autres continuent) ;
- FAUX_DOUBLER_COUPURE=1 : une coupure réseau pendant le suivi du noyau (statut « injoignable ») ;
- FAUX_DOUBLER_DUREE : durée du son d'une vidéo entière, en secondes (défaut 30) ;
- FAUX_DOUBLER_ENFANT=<fichier> : lance un processus enfant qui ignore SIGTERM (comme un ffmpeg têtu) et écrit son pid
  dans ce fichier (l'annulation doit tuer tout le groupe de processus) ;
- FAUX_DOUBLER_TRACE=<fichier> : une ligne JSON par lancement (argv, début, variables utiles) et une à la fin (code) ;
- FAUX_DOUBLER_CONFIG=<dossier> : son config.json, à la place du fichier fixe ~/.config/voix-la/config.json du vrai
  (sinon $VOIXLA_CONFIG/config.json).
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
ECHEC = (os.environ.get("FAUX_DOUBLER_ECHEC") or "").strip().lower()
DEPOT = Path(__file__).resolve().parents[2]
DONNEES_DEFAUT = Path.home() / "Music" / "Voix-là"
INTERDITS = (Path.home() / "Music", Path.home() / ".config", Path.home() / ".local", DEPOT / "sorties", DEPOT / "voix")
FACTEUR_CHANT = 2.0
KAGGLE_DEFAUTS = {"jeu": "doublage-voix-entrees", "noyau": "doublage-voix-gpu", "outils": "doublage-outils"}
CLES_CONFIG = {"donnees": "dossier des données (voix/, sorties/)", "kaggle.compte": "compte Kaggle (nom d'utilisateur)",
               "kaggle.jeu": "jeu de données privé des entrées", "kaggle.noyau": "noyau GPU privé du doublage",
               "kaggle.outils": "noyau privé des outils préparés",
               "voix": "voix cible par défaut, un dossier de <données>/voix (sinon la seule voix présente)"}
CONTENEURS = (".mp4", ".m4v", ".mov", ".mkv", ".webm")
SECRETS = re.compile(r"jeton|token|secret|password|mot.?de.?passe|(^|[._])(cle|clé|key)($|[._])", re.I)

AIDE = """doubler (Voix-là) : remplace la voix d'une vidéo par une voix clonée (FAUX doubler des essais, sans Kaggle).

  doubler <vidéo> [<vidéo>…] [--voix NOM] [--mode auto|chant|parole] [--octave auto|0|-12|+12] [--pas 30]
          [--voix-db 4] [--reverb-db -13] [--mix-v1] [--debut S --duree S] [--remix] [--sortie DIR]
          [--comparer-pas 50] [--donnees DIR] [--evenements FICHIER]
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
                "jeton_kaggle": "absent", "commande_kaggle": "(faux doubler : aucune)", "contenu": masquer(conf)}
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
    for n in KAGGLE_DEFAUTS:
        print(f"  kaggle.{n:8s} {noms[n] or '— (kaggle.compte manque : doubler --config kaggle.compte <nom>)'}")
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
    if debut is not None or duree is not None:
        demande = debut or 0.0
        k = max(0.0, demande - 0.04)   # « image clé » un peu avant le début demandé, comme une vraie vidéo
        fin = (demande + duree) if duree else max(duree_totale(), demande + 1.0)
        src.update({"debut_demande": demande, "debut": round(k + 0.001, 3), "duree": round(fin - k, 3)})
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
    return {
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
    with Etape("video", titre, "son remis sur la vidéo (image copiée)"):
        pause()
        src = json.loads((t / "source.json").read_text(encoding="utf-8"))
        ext = Path(src["video"]).suffix.lower()
        sortie = dossier / f"{base}{ext if ext in CONTENEURS else '.mkv'}"
        sortie.write_bytes(b"")
    video_s = time.time() - debut
    rapport["mix"] = {"version": version, "voix_db": voix_db, "reverb_db": a.reverb_db if mode == "chant" else None,
                      "voix_source_db": cible}
    if temps_local is not None:
        temps_local.update({"mix_local_s": round(mix_s, 1), "remise_video_s": round(video_s, 1)})
        rapport["temps_local"] = temps_local
    (tv / "rapport.json").write_text(json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8")
    EV("sortie", titre, mp4=str(sortie), mp3=str(mp3), rapport=str(tv / "rapport.json"), comparaisons=None)
    m = rapport.get("mesures_musique", {})
    dire(f"mode {mode} ({'imposé' if rapport.get('mode_force') else 'détecté'} : musique sous la voix "
         f"{m.get('reste_sous_voix_db')} dB, tonalité {m.get('tonalite_reste')}, notes tenues {m.get('tenue')}) ; "
         f"modèle {rapport.get('modele')}, {rapport.get('variantes', {}).get('voix_convertie.flac', {}).get('pas_diffusion')} pas")
    print("  hauteur par passage (dt = demi-tons) :", flush=True)
    for ligne in rapport.get("frise", []):
        print("   ", ligne, flush=True)
    dire(f"fini : {sortie}\n         {mp3}")
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
         f"en WAV) et de la voix {voix['id']} dans le jeu privé {JEU}")
    if ECHEC == "envoi":
        raise RuntimeError("Kaggle : jeu de données refusé : 403 - Forbidden (faux doubler)")
    pause(0.5)   # « téléversement et préparation du jeu »


def estimer_noyau_s(durees_s, pas):
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
    for tr in travaux:   # sorties du « noyau » rangées dans travail/ et travail/<voix>/
        (tr["travail"] / "voix_source.wav").write_bytes(b"")
        (tr["tv"] / "voix_convertie.flac").write_bytes(b"")
        (tr["tv"] / "rapport.json").write_text(json.dumps(rapport_kaggle(tr, a, voix), ensure_ascii=False, indent=1),
                                              encoding="utf-8")
    temps = {"pousse_epoch": pousse, "version_noyau": 7, "noyau_depuis_envoi_s": noyau_s,
             "rapatriement_s": round(time.time() - debut_rap, 1)}
    return temps, rapatriement


def traiter_lot(videos, a, voix):
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
    dire(f"GPU Kaggle : {temps['noyau_depuis_envoi_s'] / 60:.1f} min depuis l'envoi (démarrage 3.0 s, installation "
         f"{st['installation_s']:.0f} s, séparation {st['separation_totale_s']:.0f} s, modèles "
         f"{st['chargement_modeles_s']:.0f} s, noyau {st['total_s'] / 60:.1f} min ; estimé "
         f"{estimation / 60:.1f} min) ; rapatriement {temps['rapatriement_s']:.0f} s")
    for tr in travaux:
        try:
            temps_local = {"extraction_s": tr.get("extraction_s"), "envoi_jeu_s": envoi_s,
                           "envoi_flac_mo": round(tr["taille_envoi"] / 1e6, 1), "gain_entree_db": -1.0,
                           "attente_demarrage_noyau_s": 3.0, "version_noyau": temps["version_noyau"],
                           "noyau_depuis_envoi_s": temps["noyau_depuis_envoi_s"], "estimation_noyau_s": estimation,
                           "rapatriement_s": temps["rapatriement_s"], "decodage_retour_s": 0.0, "session_kaggle": st,
                           "videos_dans_la_session": len(travaux)}
            dire(f"=== {tr['video'].name}")
            finir(tr, a, voix, temps_local)
        except Exception as e:  # noqa: BLE001
            dire(f"ÉCHEC {tr['video']} : {e}")
            EV("erreur", tr["nom"], message=str(e))
            echecs.append(str(tr["video"]))
    return echecs


# --- Programme -------------------------------------------------------------------------------------------------------

def analyseur():
    p = argparse.ArgumentParser(prog="doubler", description=(AIDE_ANCIENNE if ANCIEN else AIDE).split("\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=(AIDE_ANCIENNE if ANCIEN else AIDE).split("\n", 1)[1])
    p.add_argument("videos", nargs="*", help="vidéo(s) mp4, mkv, webm, mov… ; « vidéo@60+40 » : extrait propre à cette vidéo")
    p.add_argument("--voix", default=None, metavar="NOM",
                   help="voix cible : un dossier de <données>/voix (défaut : clé « voix » de la configuration, "
                        "sinon la seule voix présente)")
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
        p.add_argument("--mix-v1", action="store_true", help="ancien mixage : sans suivi des nuances ni dé-esseur")
    p.add_argument("--debut", type=float, default=None, help="début de l'extrait (s), pour un essai")
    p.add_argument("--duree", type=float, default=None, help="durée de l'extrait (s)")
    p.add_argument("--remix", action="store_true", help="refaire seulement le mixage et la vidéo depuis travail/ (sans Kaggle)")
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
        a.evenements = a.donnees = a.config = None
        a.json = a.mix_v1 = a.reprendre = a.preparer_outils = False
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
    if a.voix is None:
        a.voix = exiger_voix_defaut()
    voix = charger_voix(a.voix)
    if a.remix:
        echecs = []
        for v in a.videos:
            titre = Path(str(v)).stem
            try:
                chemin, debut_v, duree_v = lire_video(v, a)
                tr = emplacements(chemin, debut_v, duree_v, a, voix)
                titre = tr["nom"]
                tr["debut_epoch"] = time.time()
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
