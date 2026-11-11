"""Seul module qui connaît doubler : où le trouver, quelles options il accepte (détectées par « doubler --help »),
comment construire ses commandes, et comment lire ce qu'il écrit.

Options récentes, utilisées seulement si « doubler --help » les annonce, avec un repli sinon :
- --evenements FICHIER : un événement JSON par ligne. Repli : lecture de la sortie texte (LectureTexte) ;
- --donnees DOSSIER    : dossier de données. Repli : --sortie <données>/sorties et DOUBLER_VOIX=<données>/voix ;
- --config [CLE VALEUR] : écriture de config.json. Repli : écriture directe du fichier (chemins.ecrire_cle_config) ;
- --voix-liste --json  : liste des voix. Repli : lecture directe de voix/*/voix.json ;
- --gpu auto|1|2       : cartes GPU du noyau, et clé « gpu » de la configuration. Sans elle : une carte, réglage grisé.
- --affinee auto|oui|non : voix affinée sur le chant (commande affiner). Sans lui : pas de choix dans la page Doubler.
- --calcul kaggle|modal|runpod|auto : où tourne le GPU (clé « calcul »). Sans lui : Kaggle, comme avant. --nettoyer-runpod
  supprime les pods Runpod voix-la-… restants.
VOIXLA_DOUBLER (essais) remplace la commande doubler, par exemple par un faux doubler. Sinon, « doubler » du PATH
(/usr/bin/doubler avec le paquet Debian), puis doubler.py à côté du code : celui du dépôt, ou /usr/lib/voix-la.
L'estimation de durée d'une session vient de doubler.py lui-même (estimation_noyau_s : sa fonction estimer_noyau_s,
chargée depuis le doubler.py livré avec l'interface, sans le lancer).
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import types
import unicodedata
from datetime import datetime
from pathlib import Path

from . import chemins

OPTIONS_SUIVIES = ("--evenements", "--donnees", "--config", "--json", "--voix-liste", "--ajouter-voix", "--remix",
                   "--debut", "--duree", "--pas", "--voix-db", "--reverb-db", "--octave", "--mode", "--sortie",
                   "--mix-v1", "--comparer-pas", "--reprendre", "--moteur", "--sans-nettoyage", "--sans-paroles",
                   "--autotune", "--sans-autotune", "--autotune-gamme", "--gpu", "--passage", "--affectation",
                   "--comparer", "--transposer", "--calcul", "--nettoyer-runpod", "--sans-quota",
                   "--affinee", "--voix-depuis")
MOTEURS = (("yingmusic", "YingMusic-SVC"), ("seedvc", "Seed-VC 44 kHz"))
NOM_VOIX = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")   # même règle que doubler --ajouter-voix
EXTRAIT_NOM = re.compile(r" \(extrait (\d+)m(\d+(?:,\d+)?)(?:\+(\d+)m(\d+(?:,\d+)?))?\)$")
ETAPES = ("extraction", "envoi", "attente", "kaggle", "rapatriement", "mix", "video", "fini")


# --- La commande -----------------------------------------------------------------------------------------------------

def commande():
    """argv de doubler : VOIXLA_DOUBLER, sinon « doubler » du PATH, sinon doubler.py à côté du code (le dépôt, ou
    /usr/lib/voix-la une fois le paquet installé : le même fichier que /usr/bin/doubler)."""
    if os.environ.get("VOIXLA_DOUBLER"):
        return shlex.split(os.environ["VOIXLA_DOUBLER"])
    trouve = shutil.which("doubler")
    if trouve:
        return [trouve]
    local = chemins.DEPOT / "doubler.py"
    if local.exists():
        return [sys.executable, str(local)]
    return ["doubler"]


def commande_affiner():
    """argv de affiner (affinage de la voix sur son chant) : VOIXLA_AFFINER, sinon « affiner » du PATH, sinon affiner.py à
    côté du code (le dépôt, ou /usr/lib/voix-la une fois le paquet installé)."""
    if os.environ.get("VOIXLA_AFFINER"):
        return shlex.split(os.environ["VOIXLA_AFFINER"])
    trouve = shutil.which("affiner")
    if trouve:
        return [trouve]
    local = chemins.DEPOT / "affiner.py"
    if local.exists():
        return [sys.executable, str(local)]
    return ["affiner"]


def _fichier_commande(cmd):
    """Le fichier qui porte le code (pour savoir s'il a changé) : le script après l'interpréteur, sinon l'exécutable."""
    for morceau in reversed(cmd):
        p = Path(morceau).expanduser()
        if p.is_file():
            return p.resolve()
    w = shutil.which(cmd[0]) if cmd else None
    return Path(w).resolve() if w else None


_capacites = {"cle": None, "valeur": frozenset(), "aide": ""}


def capacites(forcer=False):
    """Options annoncées par « doubler --help » ; le résultat est gardé tant que le fichier de doubler ne change pas."""
    cmd = commande()
    f = _fichier_commande(cmd)
    try:
        cle = (tuple(cmd), f.stat().st_mtime if f else None)
    except OSError:
        cle = (tuple(cmd), None)
    if not forcer and _capacites["cle"] == cle:
        return _capacites["valeur"]
    try:
        r = subprocess.run(cmd + ["--help"], capture_output=True, text=True, timeout=30,
                           env={**os.environ, "PYTHONUNBUFFERED": "1"})
        texte = (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        texte = ""
        _capacites["erreur"] = str(e)
    valeur = analyser_aide(texte)
    _capacites.update({"cle": cle, "valeur": valeur, "aide": texte})
    return valeur


def analyser_aide(texte):
    caps = {o for o in OPTIONS_SUIVIES if re.search(re.escape(o) + r"(?![\w-])", texte or "")}
    if re.search(r"vid[ée]o@\d", texte or ""):
        caps.add("extrait_par_video")   # « vidéo@60+40 » : un extrait propre à chaque vidéo d'un même lot
    if re.search(r"audio\s+seul", texte or ""):
        caps.add("son_seul")   # un fichier audio seul (mp3, wav, flac…) en entrée : sortie mp3, sans image
    if "homme:NOM,femme:NOM" in (texte or "") and "chanteur1:NOM,chanteur2:NOM" in (texte or ""):
        caps.add("voix_par_chanteur")   # --voix homme:a,femme:b ou chanteur1:a,chanteur2:b : une voix par chanteur
        if "personne1:NOM,personne2:NOM" in (texte or ""):
            caps.add("voix_par_personne")   # jusqu'à six voix : chanteur1:…,chanteur6:… et personne1:…,personne6:…
    return frozenset(caps)


def disponible():
    """doubler répond-il ? (au moins --remix et --voix-liste dans son aide)"""
    c = capacites()
    return "--remix" in c and "--voix-liste" in c


def environnement(donnees, caps=None):
    """Variables à ajouter pour lancer doubler sur ce dossier de données."""
    caps = capacites() if caps is None else caps
    env = {"PYTHONUNBUFFERED": "1", "VOIXLA_DONNEES": str(donnees)}
    if "--donnees" not in caps:
        env["DOUBLER_VOIX"] = str(chemins.dossier_voix(donnees))   # repli : doubler.py lit DOUBLER_VOIX
    return env


def _nombre(x):
    return f"{float(x):g}"


def _donnees(donnees, caps):
    return ["--donnees", str(donnees)] if "--donnees" in caps else []


# --- Construction des commandes --------------------------------------------------------------------------------------

def grouper_par_extrait(videos, caps=None):
    """[(extrait, [vidéos])] : les vidéos qui partent dans un même appel doubler (une seule session Kaggle).
    Si doubler accepte « vidéo@début+durée », toutes les vidéos vont ensemble (extrait None : chacune porte le sien) ;
    sinon --debut/--duree valent pour tout l'appel, donc un appel par extrait identique."""
    caps = capacites() if caps is None else caps
    if "extrait_par_video" in caps:
        return [(None, list(videos))] if videos else []
    groupes = {}
    for v in videos:
        cle = v.get("extrait")
        cle = (round(float(cle[0]), 3), None if cle[1] is None else round(float(cle[1]), 3)) if cle else None
        groupes.setdefault(cle, []).append(v)
    return list(groupes.items())


def spec_extrait(extrait):
    """(60, 40) → « 60+40 » ; (60, None) → « 60 » (jusqu'à la fin), pour « vidéo@… »."""
    debut, duree = extrait
    return f"{_nombre(debut or 0)}" + (f"+{_nombre(duree)}" if duree else "")


# --- Une voix par chanteur (--voix homme:a,femme:b ou chanteur1:a,chanteur2:b), comme doubler.lire_spec_voix --------

CLES_VOIX = {"genre": ("homme", "femme"), "ordre": tuple(f"chanteur{k}" for k in range(1, 7)),
             "personnes": tuple(f"personne{k}" for k in range(1, 7))}   # jusqu'à six voix


def lire_spec_voix(spec):
    """« a » → (None, [(None, « a »)]) ; « homme:a,femme:b » → (« genre », [(« homme », « a »), (« femme », « b »)]),
    rangé dans l'ordre des clés ; None si illisible (doubler dira pourquoi)."""
    spec = str(spec or "").strip()
    if ":" not in spec:
        return (None, [(None, spec)]) if NOM_VOIX.fullmatch(spec) else None
    paires = []
    for morceau in (m.strip() for m in spec.split(",") if m.strip()):
        cle, _, nom = (x.strip() for x in morceau.partition(":"))
        cle = cle.lower().replace(" ", "").replace("-", "").replace("_", "")
        if not NOM_VOIX.fullmatch(nom) or cle in (c for c, _ in paires):
            return None
        paires.append((cle, nom))
    mode = next((m for m, cles in CLES_VOIX.items() if paires and all(c in cles for c, _ in paires)), None)
    if mode is None:
        return None
    return mode, sorted(paires, key=lambda p: CLES_VOIX[mode].index(p[0]))


def spec_voix(mode, voix):
    """« homme:a,femme:b » (mode genre) ou « chanteur1:a,chanteur2:b » (mode ordre) ; une seule voix différente :
    son nom seul (doubler en fait alors un doublage à une voix)."""
    if len(set(voix)) <= 1:
        return voix[0] if voix else None
    return ",".join(f"{c}:{v}" for c, v in zip(CLES_VOIX[mode], voix))


def id_voix(spec):
    """Le dossier travail/<id> que doubler donne à cette voix : le nom, ou « a+b » pour une voix par chanteur (dans
    l'ordre des clés, quel que soit l'ordre donné)."""
    lu = lire_spec_voix(spec)
    if lu is None:
        return spec
    noms = list(dict.fromkeys(n for _, n in lu[1]))
    return "+".join(noms)


def membres_voix(ident):
    """Les voix réelles d'un dossier travail/<id> : [« a »] ou [« a », « b »] (« a+b »)."""
    return [m for m in str(ident or "").split("+") if m]


def spec_de_rapport(ident, rapport):
    """La spécification --voix d'un résultat (remix) : d'après voix_cibles du rapport (clé et nom de chaque voix),
    sinon l'identifiant du dossier, tel quel."""
    vc = (rapport or {}).get("voix_cibles") if isinstance(rapport, dict) else None
    voix = (vc or {}).get("voix") if isinstance(vc, dict) else None
    if isinstance(voix, list) and len(voix) >= 2 and all(isinstance(v, dict) and v.get("cle") and v.get("nom")
                                                          for v in voix):
        return ",".join(f"{v['cle']}:{v['nom']}" for v in voix)
    return ident


# --- Auto-tune -------------------------------------------------------------------------------------------------------

AUTOTUNE_DEFAUT = 70
TONIQUES = (("do", "do"), ("do#", "do♯"), ("re", "ré"), ("mib", "mi♭"), ("mi", "mi"), ("fa", "fa"), ("fa#", "fa♯"),
            ("sol", "sol"), ("lab", "la♭"), ("la", "la"), ("sib", "si♭"), ("si", "si"))
# (valeur de --autotune-gamme, libellé) : auto, chromatique, puis les 24 tonalités
GAMMES = [("auto", "Auto (tonalité détectée)"), ("chromatique", "Chromatique (les 12 notes)")] + \
    [(f"{v}-{m}", f"{l} {m}") for m in ("majeur", "mineur") for v, l in TONIQUES]


def args_autotune(reglages, caps):
    """Options d'auto-tune d'un doublage : --autotune=N (1 à 100) et sa gamme, ou --sans-autotune (désactivé alors que
    la configuration de doubler l'allume) ; rien si doubler ne les connaît pas."""
    if "--autotune" not in caps:
        return []
    n = reglages.get("autotune")
    if isinstance(n, (int, float)) and not isinstance(n, bool) and n > 0:
        a = [f"--autotune={int(round(min(100, n)))}"]
        g = reglages.get("autotune_gamme")
        if g and g != "auto" and "--autotune-gamme" in caps:
            a.append(f"--autotune-gamme={g}")
        return a
    if reglages.get("sans_autotune") and "--sans-autotune" in caps:
        return ["--sans-autotune"]
    return []


def _intensite(v):
    """Une intensité d'auto-tune de 1 à 100, sinon None (absente, 0 : désactivé, illisible)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    n = int(round(v))
    return n if 1 <= n <= 100 else None


def gamme_du_rapport(rapport):
    """La gamme imposée au doublage précédent (rapport.json, autotune.gamme), en valeur de GAMMES : « chromatique »,
    « la-mineur »… ; « auto » si elle avait été détectée, ou si le rapport n'en dit rien de lisible."""
    at = (rapport or {}).get("autotune") if isinstance(rapport, dict) else None
    g = at.get("gamme") if isinstance(at, dict) else None
    if not isinstance(g, dict):
        return "auto"
    s = unicodedata.normalize("NFD", str(g.get("demandee") or "auto").strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c)).replace("♯", "#").replace("♭", "b")
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    if s in ("", "auto", "automatique", "detectee"):
        return "auto"
    valeurs = [v for v, _l in GAMMES]
    if s in valeurs:
        return s
    if g.get("type") == "chromatique":
        return "chromatique"
    # demandée autrement (« Am », « sib », « C#m ») : la tonique (0 à 11, do = 0) et le mode lus par doubler
    t, m = g.get("tonique"), g.get("mode")
    if isinstance(t, int) and not isinstance(t, bool) and 0 <= t < 12 and m in ("majeur", "mineur"):
        return f"{TONIQUES[t][0]}-{m}"
    return "auto"


def autotune_du_resultat(rapport, conf=None):
    """Réglages d'auto-tune présentés au remix d'un doublage : (version, intensité, gamme). version : le doublage a
    déjà sa version auto-tune (autotune.applique) ; intensité : celle du rapport, sinon la clé « autotune » de la
    configuration de doubler, sinon AUTOTUNE_DEFAUT ; gamme : celle du rapport si elle avait été imposée, sinon
    « auto »."""
    at = (rapport or {}).get("autotune") if isinstance(rapport, dict) else None
    at = at if isinstance(at, dict) else {}
    n = _intensite(at.get("intensite")) or _intensite(autotune_par_defaut(conf)) or AUTOTUNE_DEFAUT
    return bool(at.get("applique")), n, gamme_du_rapport(rapport)


def autotune_remix_possible(caps, conf=None):
    """(vrai, '') si le remix peut faire ou retirer une version auto-tune, sinon (faux, cause) : « doubler » (doubler
    sans --autotune ni --sans-autotune) ou « x42 » (greffon absent : x42_autotune null dans « doubler --config
    --json »)."""
    if "--autotune" not in caps or "--sans-autotune" not in caps:
        return False, "doubler"
    connu, dossier = x42_autotune(conf)
    if connu and dossier is None:
        return False, "x42"
    return True, ""


def reglages_remix_autotune(allume, intensite, gamme, version, possible=True):
    """Réglages d'auto-tune d'un remix, pour args_autotune : interrupteur allumé, --autotune N (et sa gamme si elle
    n'est pas « auto ») ; éteint : --sans-autotune, toujours (avec une version, elle va à la corbeille ; sans version,
    doubler n'en refait pas une même si la clé « autotune » de sa configuration est allumée) ; auto-tune impossible :
    rien, comme un remix d'avant l'auto-tune. version : sert au sous-titre de la page, plus à la commande."""
    if not possible:
        return {}
    if allume:
        r = {"autotune": max(1, min(100, int(round(intensite or AUTOTUNE_DEFAUT))))}
        if gamme and gamme != "auto":
            r["autotune_gamme"] = gamme
        return r
    return {"sans_autotune": True}


def args_doublage(videos, reglages, donnees, extrait=None, evenements=None, caps=None):
    """argv d'un doublage : toutes les vidéos dans un seul appel, donc une seule session Kaggle. videos : chemins, ou
    dicts {chemin, extrait} (extrait propre à chaque vidéo si doubler accepte « vidéo@début+durée »)."""
    caps = capacites() if caps is None else caps
    a = [*commande()]
    for v in videos:
        if isinstance(v, dict):
            chemin, ex = str(Path(v["chemin"])), v.get("extrait")
            if ex and "extrait_par_video" in caps and not extrait:
                chemin += "@" + spec_extrait(ex)
            a.append(chemin)
        else:
            a.append(str(Path(v)))
    comparer = [v for v in (reglages.get("comparer") or []) if v]
    if len(comparer) > 1 and "--comparer" in caps:
        a.append(f"--comparer={','.join(comparer)}")   # plusieurs voix, une session ; remplace --voix
    elif reglages.get("voix"):
        a.append(f"--voix={reglages['voix']}")
    if reglages.get("mode") and reglages["mode"] != "auto":
        a.append(f"--mode={reglages['mode']}")
    if reglages.get("octave") not in (None, "", "auto"):
        o = str(reglages["octave"])
        a.append(f"--octave={'+12' if o in ('12', '+12') else o}")
    t = reglages.get("transposer")
    if t not in (None, "") and "--transposer" in caps:   # auto (défaut de doubler), non, ou N demi-tons imposés
        t = "non" if t is False else "auto" if t is True else str(t)
        a.append(f"--transposer={t}")
    if reglages.get("pas"):
        a.append(f"--pas={int(reglages['pas'])}")
    if reglages.get("voix_db") is not None:
        a.append(f"--voix-db={_nombre(reglages['voix_db'])}")
    if reglages.get("reverb_db") is not None:
        a.append(f"--reverb-db={_nombre(reglages['reverb_db'])}")
    if reglages.get("mix_v1") and "--mix-v1" in caps:
        a.append("--mix-v1")
    if reglages.get("moteur") and "--moteur" in caps:
        a.append(f"--moteur={reglages['moteur']}")
    if reglages.get("sans_nettoyage") and "--sans-nettoyage" in caps:
        a.append("--sans-nettoyage")
    if reglages.get("calcul") in ("kaggle", "modal", "runpod", "auto") and "--calcul" in caps:
        a.append(f"--calcul={reglages['calcul']}")   # où tourne le GPU ; sans lui, la clé « calcul » de doubler
    if reglages.get("affinee") in ("auto", "oui", "non") and "--affinee" in caps:
        a.append(f"--affinee={reglages['affinee']}")   # voix affinée : sans lui, auto (clé « affinee » de doubler)
    if reglages.get("sans_paroles") and "--sans-paroles" in caps:
        a.append("--sans-paroles")
    a += args_autotune(reglages, caps)
    if extrait:
        debut, duree = extrait
        a.append(f"--debut={_nombre(debut or 0)}")
        if duree:
            a.append(f"--duree={_nombre(duree)}")
    if "--donnees" in caps:
        a += _donnees(donnees, caps)
    else:
        a.append(f"--sortie={chemins.dossier_sorties(donnees)}")
    if evenements and "--evenements" in caps:
        a.append(f"--evenements={evenements}")
    return a


def fmt_t(t):
    """Même écriture que doubler pour le nom d'un dossier d'extrait (1m00,0)."""
    return f"{int(t // 60)}m{t % 60:04.1f}".replace(".", ",")


def nom_dossier(video, extrait=None):
    """Nom du dossier de sortie que doubler donnera à cette vidéo."""
    nom = Path(video).stem
    if extrait:
        debut, duree = extrait
        nom += f" (extrait {fmt_t(debut or 0)}{'+' + fmt_t(duree) if duree else ''})"
    return nom


def extrait_du_nom(nom):
    """« Titre (extrait 1m00,0+0m40,0) » → (60.0, 40.0) ; « (extrait 1m00,0) » → (60.0, None) ; sinon None."""
    m = EXTRAIT_NOM.search(nom)
    if not m:
        return None
    debut = int(m.group(1)) * 60 + float(m.group(2).replace(",", "."))
    duree = int(m.group(3)) * 60 + float(m.group(4).replace(",", ".")) if m.group(3) else None
    return debut, duree


def args_remix(video_source, voix, dossier_resultat, donnees, voix_db=None, reverb_db=None, mix_v1=False, caps=None,
               autotune=None, affectation=None):
    """argv d'un remix (mixage et remise sur la vidéo depuis travail/, sans Kaggle, quelques secondes).
    doubler retrouve le dossier par le nom de la vidéo et l'extrait : on lui redonne les mêmes, et le dossier parent
    du résultat comme --sortie (un résultat peut être rangé dans un sous-dossier de sorties/). autotune : réglages de
    reglages_remix_autotune (--autotune N [--autotune-gamme G], --sans-autotune, ou rien). affectation (une voix par
    chanteur) : chemin d'un fichier d'affectation corrigée, ou « auto » (revenir à l'automatique) ; seulement si doubler
    connaît --affectation."""
    caps = capacites() if caps is None else caps
    dossier_resultat = Path(dossier_resultat)
    a = [*commande(), str(video_source), "--remix", f"--voix={voix}", f"--sortie={dossier_resultat.parent}"]
    extrait = extrait_du_nom(dossier_resultat.name)
    if extrait:
        a.append(f"--debut={_nombre(extrait[0])}")
        if extrait[1]:
            a.append(f"--duree={_nombre(extrait[1])}")
    if voix_db is not None:
        a.append(f"--voix-db={_nombre(voix_db)}")
    if reverb_db is not None:
        a.append(f"--reverb-db={_nombre(reverb_db)}")
    if mix_v1 and "--mix-v1" in caps:
        a.append("--mix-v1")
    a += args_autotune(autotune or {}, caps)
    if affectation and "--affectation" in caps:
        a.append(f"--affectation={affectation}")
    a += _donnees(donnees, caps)
    return a


def correction_possible(caps=None):
    """doubler sait-il corriger à la main qui chante chaque passage d'un duo (--affectation) ?"""
    caps = capacites() if caps is None else caps
    return "--affectation" in caps


def ecrire_affectation(affectation):
    """L'affectation voulue (dict, format de travail/<a+b>/affectation.json) dans un fichier temporaire, hors du dossier
    du doublage ; rend son chemin (à effacer après le remix)."""
    import tempfile
    fd, chemin = tempfile.mkstemp(prefix="voixla-affectation-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(affectation, f, ensure_ascii=False, indent=1)
    return chemin


def remix_possible(video_source, dossier_resultat):
    """(vrai, '') si doubler retrouvera ce dossier à partir de la vidéo source, sinon (faux, raison)."""
    if not video_source:
        return False, "la vidéo source n’est pas indiquée dans travail/source.json"
    v = Path(video_source)
    if not v.is_file():
        return False, f"vidéo source introuvable : {v}"
    attendu = nom_dossier(v.resolve(), extrait_du_nom(Path(dossier_resultat).name))
    if attendu != Path(dossier_resultat).name:
        return False, f"le dossier ne porte plus le nom que doubler attend (« {attendu} »)"
    return True, ""


def args_reprendre(donnees, evenements=None, caps=None):
    """« doubler --reprendre » : finit la dernière session Kaggle interrompue (rapatriement, mix, vidéo ; sans GPU)."""
    caps = capacites() if caps is None else caps
    a = [*commande(), "--reprendre", *_donnees(donnees, caps)]
    if evenements and "--evenements" in caps:
        a.append(f"--evenements={evenements}")
    return a


def args_affiner(voix, donnees, pas=None, budget_h=None, reprendre=False, evenements=None, essai=False):
    """« affiner VOIX » : affine la voix sur ses prises de chant (voix/<voix>/chant/). Les options d'affiner (--donnees,
    --evenements…) existent depuis sa première version : pas besoin d'interroger son aide."""
    a = [*commande_affiner(), str(voix), f"--donnees={donnees}"]
    if pas:
        a.append(f"--pas={int(pas)}")
    if budget_h:
        a.append(f"--budget-h={_nombre(budget_h)}")
    if reprendre:
        a.append("--reprendre")
    if essai:
        a.append("--essai")
    if evenements:
        a.append(f"--evenements={evenements}")
    return a


def args_ajouter_voix(nom, fichier, donnees, caps=None):
    caps = capacites() if caps is None else caps
    return [*commande(), "--ajouter-voix", nom, str(fichier), *_donnees(donnees, caps)]


def args_voix_depuis(source, nom, chanteur, donnees, caps=None):
    """argv de doubler --voix-depuis : garder la voix d'un chanteur d'un doublage fini (chanteur : N ou clé, None si le
    doublage n'a qu'une voix)."""
    caps = capacites() if caps is None else caps
    a = [*commande(), "--voix-depuis", str(source), nom]
    if chanteur not in (None, ""):
        a += ["--chanteur", str(chanteur)]
    return [*a, *_donnees(donnees, caps)]


def chanteurs_gardables(rapport):
    """Les voix d'un doublage qu'on peut garder comme voix cible : [{"n": 1, "cle": "homme", "secondes": 83.0}], dans
    l'ordre d'apparition (comme --chanteur N de doubler) ; vide sans passage chanté. rapport : rapport.json lu."""
    ordre = {}
    for m in (rapport or {}).get("morceaux") or []:
        try:
            if m.get("muet") or not (m.get("voise_s") or 0) > 0:
                continue
            cle = m.get("chanteur") or "voix"
            ordre[cle] = ordre.get(cle, 0.0) + float(m["fin_s"]) - float(m["debut_s"])
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
    return [{"n": n, "cle": c, "secondes": round(s, 1)} for n, (c, s) in enumerate(ordre.items(), 1)]


def lancer(argv, env_plus=None, timeout=60, cwd=None):
    """Lance une commande courte et rend (code, sortie complète). À appeler hors du fil de l'interface."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                           env={**os.environ, **(env_plus or {})})
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"pas de réponse en {timeout} s"
    except OSError as e:
        return 127, str(e)


def ajouter_voix(nom, fichier, donnees):
    """doubler --ajouter-voix : rend (ok, message, alertes)."""
    code, sortie = lancer(args_ajouter_voix(nom, fichier, donnees), environnement(donnees), timeout=300)
    alertes = [l.split("ATTENTION :", 1)[1].strip() for l in sortie.splitlines() if "ATTENTION :" in l]
    lignes = [l.strip() for l in sortie.strip().splitlines() if l.strip()]
    if code:
        return False, (lignes[-1] if lignes else f"échec (code {code})"), alertes
    message = next((l for l in lignes if l.startswith(f"voix {nom} ajoutée")), lignes[0] if lignes else "voix ajoutée")
    return True, message, alertes


def voix_depuis(source, nom, chanteur, donnees):
    """doubler --voix-depuis : rend (ok, message, alertes). Sans GPU : quelques secondes."""
    code, sortie = lancer(args_voix_depuis(source, nom, chanteur, donnees), environnement(donnees), timeout=300)
    alertes = [l.split("ATTENTION :", 1)[1].strip() for l in sortie.splitlines() if "ATTENTION :" in l]
    lignes = [l.strip() for l in sortie.strip().splitlines() if l.strip()]
    if code:
        return False, (lignes[-1] if lignes else f"échec (code {code})"), alertes
    message = next((l for l in lignes if l.startswith(f"voix {nom} gardée")), lignes[0] if lignes else "voix gardée")
    return True, message, alertes


def remixer(video_source, voix, dossier_resultat, donnees, voix_db=None, reverb_db=None, mix_v1=False, autotune=None,
            affectation=None):
    """Remix : rend (ok, sortie complète). autotune : voir args_remix. affectation : « auto », un chemin, ou un dict
    (l'affectation corrigée voulue, écrite dans un fichier temporaire effacé après le remix)."""
    temporaire = ecrire_affectation(affectation) if isinstance(affectation, dict) else None
    try:
        code, sortie = lancer(args_remix(video_source, voix, dossier_resultat, donnees, voix_db, reverb_db, mix_v1,
                                         autotune=autotune, affectation=temporaire or affectation),
                              environnement(donnees), timeout=900)
    finally:
        if temporaire:
            Path(temporaire).unlink(missing_ok=True)
    return code == 0 and "ÉCHEC" not in sortie, sortie


# --- Voix ------------------------------------------------------------------------------------------------------------

def _fiche_voix(dossier):
    fiche = dossier / "voix.json"
    try:
        f = json.loads(fiche.read_text(encoding="utf-8"))
        if not isinstance(f, dict):
            raise ValueError
        alertes = list(f.get("alertes") or [])
    except (OSError, ValueError):
        f, alertes = {}, ["voix.json illisible"]
    ref = dossier / str(f.get("reference") or "reference.wav")
    if not ref.exists():
        alertes.append("reference.wav absente")
    if not f.get("hauteur_parole_mediane_hz") and "voix.json illisible" not in alertes:
        alertes.append("hauteur de parole manquante")
    return {
        "id": dossier.name,
        "nom": f.get("nom") or dossier.name.capitalize(),
        "dossier": dossier,
        "reference": ref,
        "duree_s": f.get("duree_s"),
        "hauteur_hz": f.get("hauteur_parole_mediane_hz"),
        "hauteur_10_90": f.get("hauteur_parole_10_90_hz"),
        "note": f.get("note_mediane"),
        "date": f.get("date"),
        "origine": (f.get("origine_detail") if f.get("imitation_ia") else None) or f.get("origine"),
        "imitation_ia": bool(f.get("imitation_ia")),
        "consentement": f.get("consentement"),
        "parole_s": f.get("parole_s"),
        "snr_db": f.get("rapport_signal_bruit_db"),
        "sonie_lufs": f.get("sonie_lufs"),
        "alertes": alertes,
        "fiche": f,
    }


def lister_voix(donnees, caps=None):
    """Voix du dossier de données. Lecture directe de voix/*/voix.json ; si doubler a « --voix-liste --json », sa
    liste fait foi pour l'ordre et les voix reconnues (les champs lus en direct complètent)."""
    caps = capacites() if caps is None else caps
    dossier = chemins.dossier_voix(donnees)
    voix = {}
    for fiche in sorted(dossier.glob("*/voix.json")):
        voix[fiche.parent.name] = _fiche_voix(fiche.parent)
    if "--json" in caps and "--voix-liste" in caps:
        code, sortie = lancer([*commande(), "--voix-liste", "--json", *_donnees(donnees, caps)],
                              environnement(donnees, caps), timeout=30)
        liste = _lire_liste_json(sortie) if code == 0 else None
        if liste is not None:
            ordre = []
            for e in liste:
                ident = str(e.get("id") or e.get("voix") or e.get("dossier_nom") or "")
                if not ident:
                    continue
                v = voix.get(ident) or _fiche_voix(dossier / ident)
                v["defaut_doubler"] = bool(e.get("defaut"))
                for cle_json, cle in (("nom", "nom"), ("duree_s", "duree_s"),
                                      ("hauteur_parole_mediane_hz", "hauteur_hz"), ("alertes", "alertes")):
                    if e.get(cle_json) not in (None, "", []):
                        v[cle] = e[cle_json]
                ordre.append(v)
            if ordre:
                return ordre
    return list(voix.values())


def _lire_liste_json(texte):
    try:
        d = json.loads(texte)
    except ValueError:
        # au cas où une ligne de texte précéderait le JSON
        debut = min([i for i in (texte.find("["), texte.find("{")) if i >= 0] or [-1])
        if debut < 0:
            return None
        try:
            d = json.loads(texte[debut:])
        except ValueError:
            return None
    if isinstance(d, dict):
        d = d.get("voix", d.get("liste"))
        if isinstance(d, dict):
            d = [{"id": k, **(v if isinstance(v, dict) else {})} for k, v in d.items()]
    return [e for e in d if isinstance(e, dict)] if isinstance(d, list) else None


def voix_de_config(conf):
    """Identifiant de la voix par défaut écrit dans une configuration, ou None. Accepte config.json (clé « voix ») et
    la sortie de « doubler --config --json » (« voix_defaut », « voix » quand c'est un nom de voix et non le dossier
    des voix, ou « voix » de « contenu »)."""
    if not isinstance(conf, dict):
        return None
    contenu = conf.get("contenu") if isinstance(conf.get("contenu"), dict) else {}
    for v in (conf.get("voix_defaut"), conf.get("voix"), contenu.get("voix")):
        if isinstance(v, dict):
            v = v.get("defaut") or v.get("id")
        if isinstance(v, str) and NOM_VOIX.fullmatch(v.strip()):
            return v.strip()
    return None


def voix_par_defaut(voix, conf=None):
    """La voix que doubler prend sans --voix, parmi « voix » (liste de lister_voix) : celle qu'il annonce (« defaut »
    de --voix-liste --json), sinon la clé « voix » de sa configuration (conf : config_effective() ou config.json),
    sinon la seule voix présente. None s'il y en a plusieurs sans défaut : doubler demande alors --voix."""
    ids = [v["id"] for v in voix]
    annoncee = next((v["id"] for v in voix if v.get("defaut_doubler")), None)
    if annoncee:
        return annoncee
    ident = voix_de_config(conf)
    if ident in ids:
        return ident
    return ids[0] if len(ids) == 1 else None


# --- config.json -----------------------------------------------------------------------------------------------------

def config_effective():
    """Configuration telle que doubler l'utilise : « doubler --config --json » s'il existe (dossier de données et sa
    source, noms Kaggle complets, présence du jeton), sinon reconstruite depuis config.json. Le jeton n'est jamais lu."""
    caps = capacites()
    # doubler lit toujours ~/.config/voix-la/config.json : avec VOIXLA_CONFIG (essais), on lit le fichier d'essai
    if "--config" in caps and "--json" in caps and not os.environ.get("VOIXLA_CONFIG"):
        code, sortie = lancer([*commande(), "--config", "--json"], timeout=30)
        if code == 0:
            try:
                d = json.loads(sortie[sortie.find("{"):])
                if isinstance(d, dict):
                    d["par_doubler"] = True
                    return d
            except ValueError:
                pass
    conf = chemins.lire_config()
    k = chemins.kaggle(conf)
    donnees = chemins.dossier_donnees(conf)
    source = ("VOIXLA_DONNEES" if os.environ.get("VOIXLA_DONNEES") else
              str(chemins.fichier_config()) if conf.get("donnees") else "défaut")
    return {"config": str(chemins.fichier_config()), "donnees": str(donnees), "source_donnees": source,
            "voix": str(chemins.dossier_voix(donnees)), "sorties": str(chemins.dossier_sorties(donnees)),
            "kaggle": {"compte": k["compte"] or None, **{n: chemins.ref_kaggle(k["compte"], k[n])
                                                         for n in ("jeu", "noyau", "outils")}},
            "contenu": conf, "par_doubler": False}


CLES_CONFIG = ("donnees", "kaggle.compte", "kaggle.jeu", "kaggle.noyau", "kaggle.outils", "kaggle.machine", "moteur",
               "paroles", "autotune", "gpu", "calcul", "kaggle.attente_file_min", "modal.gpu", "runpod.gpu",
               "runpod.duree_max_min")
CLES_BOOLEENNES = ("paroles",)      # « doubler --config paroles oui|non » ; un booléen dans config.json
VRAI = ("oui", "vrai", "true", "1", "on", "yes")
FAUX = ("non", "faux", "false", "0", "off", "no")


def booleen(valeur, defaut=True):
    """Valeur booléenne d'une clé de configuration : vrai, faux, « oui », « non »… ; defaut sinon."""
    if isinstance(valeur, bool):
        return valeur
    if isinstance(valeur, (int, float)):
        return bool(valeur)
    t = str(valeur).strip().lower() if valeur is not None else ""
    return True if t in VRAI else False if t in FAUX else defaut


def paroles_par_defaut(conf):
    """Transcription des paroles par défaut (clé « paroles » de la configuration de doubler, vraie si absente)."""
    conf = conf if isinstance(conf, dict) else {}
    contenu = conf.get("contenu") if isinstance(conf.get("contenu"), dict) else {}
    return booleen(contenu.get("paroles", conf.get("paroles")), True)


def autotune_par_defaut(conf):
    """Intensité de l'auto-tune de chaque doublage d'après la configuration de doubler (clé « autotune », 0 à 100 ;
    0 ou absente : désactivé)."""
    conf = conf if isinstance(conf, dict) else {}
    contenu = conf.get("contenu") if isinstance(conf.get("contenu"), dict) else {}
    v = conf.get("autotune", contenu.get("autotune"))
    if isinstance(v, bool):
        return AUTOTUNE_DEFAUT if v else 0
    try:
        return max(0, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


# Cartes GPU du noyau (clé « gpu » de doubler, option --gpu) : (valeur, libellé)
GPU_CHOIX = (("auto", "Auto"), ("1", "1"), ("2", "2"))
MACHINE_DEFAUT = "NvidiaTeslaT4"    # machine Kaggle par défaut de doubler : GPU T4 x2, deux cartes


def gpu_par_defaut(conf):
    """Cartes GPU demandées par la configuration de doubler : « auto », « 1 » ou « 2 » (clé « gpu » ; auto si absente
    ou illisible, comme doubler)."""
    conf = conf if isinstance(conf, dict) else {}
    contenu = conf.get("contenu") if isinstance(conf.get("contenu"), dict) else {}
    v = conf.get("gpu", contenu.get("gpu"))
    v = str(v).strip().lower() if v is not None and not isinstance(v, bool) else "auto"
    return v if v in ("auto", "1", "2") else "auto"


def machine_kaggle(conf):
    """Machine Kaggle demandée par doubler (clé « kaggle.machine »), NvidiaTeslaT4 (deux T4) par défaut."""
    conf = conf if isinstance(conf, dict) else {}
    contenu = conf.get("contenu") if isinstance(conf.get("contenu"), dict) else {}
    for k in (conf.get("kaggle"), contenu.get("kaggle")):
        if isinstance(k, dict) and isinstance(k.get("machine"), str) and k["machine"].strip():
            return k["machine"].strip()
    return MACHINE_DEFAUT


def cartes_prevues(conf, caps=None):
    """Cartes que le noyau utilisera probablement (estimation de durée), comme doubler.cartes_prevues : 1 si doubler
    ne connaît pas --gpu (une carte, comme avant) ou si « gpu » vaut 1 ; 2 si « gpu » vaut 2, ou en auto sur la
    machine à deux T4 ; 1 sur une autre machine en auto (nombre de cartes inconnu)."""
    caps = capacites() if caps is None else caps
    if "--gpu" not in caps:
        return 1
    gpu = gpu_par_defaut(conf)
    if gpu == "1":
        return 1
    return 2 if gpu == "2" or machine_kaggle(conf) == MACHINE_DEFAUT else 1


def calcul_par_defaut(conf):
    """Moyen de calcul de la configuration de doubler (clé « calcul » : kaggle, modal, runpod, auto ; kaggle sinon)."""
    conf = conf if isinstance(conf, dict) else {}
    contenu = conf.get("contenu") if isinstance(conf.get("contenu"), dict) else {}
    v = str(conf.get("calcul", contenu.get("calcul")) or "").strip().lower()
    return v if v in ("kaggle", "modal", "runpod", "auto") else "kaggle"


def duree_cout_calcul(estimations_kaggle_s, calcul, conf):
    """(durée en s, coût en dollars ou None) d'une session sur Modal ou Runpod, selon la carte réglée (« modal.gpu »,
    « runpod.gpu »), comme doubler.estimer_calcul ; la somme des sessions. None pour Kaggle et auto (gratuits à
    l'estimation), ou si doubler.py ne se charge pas."""
    if calcul not in ("modal", "runpod"):
        return None
    d = module_doubler()
    if d is None or not hasattr(d, "charger_calcul"):
        return None
    conf = conf if isinstance(conf, dict) else {}
    contenu = conf.get("contenu") if isinstance(conf.get("contenu"), dict) else conf
    try:
        c = d.charger_calcul()
        carte = c.carte(d.gpu_calcul_config(contenu, calcul), calcul)
        total, cout = 0, 0.0
        for e in estimations_kaggle_s:
            duree, prix = c.estimer_calcul(float(e), calcul, carte)
            total += duree
            cout = None if prix is None or cout is None else cout + prix
        return total, (None if cout is None else round(cout, 2))
    except Exception:  # noqa: BLE001 — doubler.py d'une autre version : pas d'estimation en dollars
        return None


def x42_autotune(conf):
    """(connu, dossier) : doubler --config --json dit-il où est le greffon x42-autotune ? connu faux avec un ancien
    doubler (on ne sait pas) ; dossier None si le greffon manque."""
    conf = conf if isinstance(conf, dict) else {}
    if "x42_autotune" not in conf:
        return False, None
    return True, conf.get("x42_autotune") or None


def ecrire_config(cle, valeur):
    """Écrit une clé de config.json : par « doubler --config CLE VALEUR » s'il existe, sinon directement.
    Rend (ok, message)."""
    caps = capacites()
    if valeur in (None, ""):
        # doubler --config ne sait pas effacer une clé (et prendrait "" pour le dossier courant)
        try:
            chemins.ecrire_cle_config(cle, None)
            return True, ""
        except OSError as e:
            return False, str(e)
    if "--config" in caps and not os.environ.get("VOIXLA_CONFIG"):
        code, sortie = lancer([*commande(), "--config", cle, "" if valeur is None else str(valeur)], timeout=30)
        if code == 0:
            return True, sortie.strip()
        return False, sortie.strip().splitlines()[-1] if sortie.strip() else f"doubler --config : code {code}"
    if cle in CLES_BOOLEENNES:
        valeur = booleen(valeur)     # écrite directement : un vrai booléen, comme doubler --config
    elif cle == "autotune":
        valeur = autotune_par_defaut({"autotune": valeur})   # un entier de 0 à 100, comme doubler --config
    elif cle == "gpu":
        valeur = gpu_par_defaut({"gpu": valeur})             # « auto », « 1 » ou « 2 », comme doubler --config
    try:
        chemins.ecrire_cle_config(cle, valeur)
        return True, ""
    except OSError as e:
        return False, str(e)


# --- Estimation de durée de doubler -----------------------------------------------------------------------------------

_doubler = {"cle": None, "module": None}
_verrou_doubler = threading.Lock()


def fichier_doubler():
    """doubler.py livré avec l'interface : celui du dépôt, ou /usr/lib/voix-la une fois le paquet Debian installé
    (chemins.DEPOT), le même fichier que lance /usr/bin/doubler."""
    return chemins.DEPOT / "doubler.py"


def module_doubler():
    """doubler.py chargé comme module, pour appeler ses fonctions sans le lancer : son programme ne tourne que sous
    « if __name__ == '__main__' », et ses constantes ne font que nommer des chemins. Ni .pyc écrit à côté, ni entrée dans
    sys.modules. Gardé tant que le fichier ne change pas ; None s'il manque ou ne se charge pas."""
    f = fichier_doubler()
    try:
        cle = (str(f), f.stat().st_mtime_ns)
    except OSError:
        return None
    with _verrou_doubler:
        if _doubler["cle"] != cle:
            module = types.ModuleType("voixla_doubler")
            module.__file__ = str(f)
            try:
                exec(compile(f.read_bytes(), str(f), "exec"), module.__dict__)   # noqa: S102 — le doubler livré
            except (Exception, SystemExit):  # noqa: BLE001 — doubler.py illisible : l'interface garde son repli
                module = None
            _doubler.update(cle=cle, module=module)
        return _doubler["module"]


def estimation_noyau_s(durees_s, pas, sorties, nettoyage=True, mode="auto", moteur_chant="yingmusic", paroles=True,
                       voix_cibles=1, cartes=1, voix_comparees=1):
    """Durée probable de la session Kaggle (de la poussée du noyau à sa fin) calculée par doubler lui-même
    (doubler.estimer_noyau_s, sur les rapports passés de sorties) : la valeur qu'il annoncera au lancement, dans
    estimation_s de ses événements « kaggle ». None si doubler.py manque, ne se charge pas, ou n'accepte pas ces
    paramètres (doubler d'avant les paroles pendant la conversion) : l'interface prend alors son propre modèle."""
    d = module_doubler()
    estimer = getattr(d, "estimer_noyau_s", None)
    if not callable(estimer):
        return None
    try:
        options = dict(nettoyage=bool(nettoyage), mode=mode or "auto", moteur=moteur_chant or "yingmusic",
                       paroles=bool(paroles), voix_cibles=max(1, int(voix_cibles or 1)), cartes=max(1, int(cartes or 1)))
        if options["voix_cibles"] > 1 and "versions_duo" in getattr(estimer, "__code__", None).co_varnames:
            # doubler convertit chaque passage dans les deux voix en duo (son défaut), seulement les utiles au-delà
            options["versions_duo"] = "toutes" if options["voix_cibles"] == 2 else "utiles"
        if int(voix_comparees or 1) > 1:
            if "voix_comparees" not in getattr(estimer, "__code__", None).co_varnames:
                return None    # doubler d'avant --comparer : l'interface prend son propre modèle
            options["voix_comparees"] = int(voix_comparees)
        s = estimer([float(x or 0) for x in durees_s], int(pas or 30), [Path(x) for x in sorties], **options)
        return float(s) if s is not None and float(s) >= 0 else None
    except Exception:  # noqa: BLE001 — erreur dans doubler.py : le repli de l'interface
        return None


# --- Événements ------------------------------------------------------------------------------------------------------

def analyser_evenement(ligne):
    """Une ligne de --evenements → dict normalisé, ou None si illisible."""
    ligne = ligne.strip()
    if not ligne:
        return None
    try:
        e = json.loads(ligne)
    except ValueError:
        return None
    if not isinstance(e, dict) or "type" not in e:
        return None
    e["t"] = lire_instant(e.get("t"))
    if e.get("video") is not None:
        e["video"] = str(e["video"])
    return e


def lire_instant(t):
    """Instant d'un événement : heure ISO (« 2026-11-04T09:20:01.123+01:00 », ce qu'écrit doubler) ou secondes."""
    if isinstance(t, (int, float)):
        return float(t)
    if isinstance(t, str) and t:
        try:
            return float(t)
        except ValueError:
            pass
        try:
            d = datetime.fromisoformat(t.replace("Z", "+00:00"))
            return d.timestamp()
        except ValueError:
            pass
    return time.time()


HEURE = re.compile(r"^(\d\d):(\d\d):(\d\d) (.*)$")
STATUT_KAGGLE = re.compile(r"^Kaggle : ([a-zA-Z_]+) \((\d+(?:[.,]\d+)?) min\)$")


class LectureTexte:
    """Repli quand doubler n'a pas --evenements : déduit les mêmes événements de sa sortie texte (« HH:MM:SS … »)."""

    def __init__(self, debut_epoch=None):
        self.base = debut_epoch or time.time()
        self.dernier_t = self.base
        self.ouvertes = {}         # (etape, video) → vrai si commencée et pas finie
        self.video_locale = None   # vidéo en cours de mixage / remise sur la vidéo
        self.attend_mp3 = None     # vidéo dont la ligne suivante donne le MP3
        self.video_extraction = None

    def _temps(self, h, m, s):
        d = datetime.fromtimestamp(self.base).replace(hour=h, minute=m, second=s, microsecond=0).timestamp()
        while d < self.dernier_t - 43200:   # passage à minuit
            d += 86400
        self.dernier_t = max(self.dernier_t, d)
        return d

    def _ev(self, t, type_, **k):
        return {"t": t, "type": type_, **k}

    def _debut(self, t, etape, video=None, message=None):
        self.ouvertes[(etape, video)] = True
        e = self._ev(t, "etape", etape=etape, etat="debut", video=video)
        if message:
            e["message"] = message
        return [e]

    def _fin(self, t, etape, video=None):
        if self.ouvertes.pop((etape, video), None):
            return [self._ev(t, "etape", etape=etape, etat="fin", video=video)]
        return []

    def _fermer_session(self, t, jusqu_a):
        """Ferme les étapes de session ouvertes avant « jusqu_a »."""
        evs = []
        for etape in ETAPES[:ETAPES.index(jusqu_a)]:
            for (e, v) in list(self.ouvertes):
                if e == etape:
                    evs += self._fin(t, e, v)
        return evs

    def ligne(self, texte):
        """Une ligne de sortie → liste d'événements."""
        texte = texte.rstrip("\n")
        m = HEURE.match(texte)
        if not m:
            brut = texte.strip()
            if self.attend_mp3 and brut.endswith(".mp3"):
                v, self.attend_mp3 = self.attend_mp3, None
                return [self._ev(self.dernier_t, "sortie", chemin=brut, video=v)]
            if re.match(r"^\d+ vidéo\(s\) en échec", brut):
                return [self._ev(self.dernier_t, "erreur", message=brut)]
            return []
        t = self._temps(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        corps = m.group(4).strip()
        self.attend_mp3 = None

        r = re.match(r"^=== (.+) \(remix, voix (.+)\)$", corps)
        if r:
            v = r.group(1)
            self.video_locale = v
            return self._debut(t, "mix", v, "remix")
        r = re.match(r"^=== (.+?) → (.+)$", corps)
        if r:
            v = r.group(1)
            evs = self._fin(t, "extraction", self.video_extraction) if self.video_extraction else []
            self.video_extraction = v
            return evs + self._debut(t, "extraction", v)
        r = re.match(r"^=== (.+)$", corps)
        if r:
            v = r.group(1)
            evs = self._fermer_session(t, "mix")
            if self.video_locale:
                evs += self._fin(t, "mix", self.video_locale) + self._fin(t, "video", self.video_locale)
            self.video_locale = v
            return evs + self._debut(t, "mix", v)
        if corps.startswith("extrait : "):
            return [self._ev(t, "message", message=corps, video=self.video_extraction)]
        r = re.match(r"^ÉCHEC de la session Kaggle : (.*)$", corps)
        if r:
            etape = next((e for (e, _v) in self.ouvertes if e in ("envoi", "attente", "kaggle", "rapatriement")), "kaggle")
            self.ouvertes = {k: v for k, v in self.ouvertes.items() if k[0] not in ("envoi", "attente", "kaggle")}
            return [self._ev(t, "etape", etape=etape, etat="erreur", message=r.group(1)),
                    self._ev(t, "erreur", message=r.group(1))]
        r = re.match(r"^ÉCHEC (.+?) : (.*)$", corps)
        if r:
            v = Path(r.group(1)).name
            ouvertes = [e for (e, vv) in self.ouvertes if vv == v]
            evs = []
            for e in ouvertes:
                self.ouvertes.pop((e, v), None)
                evs.append(self._ev(t, "etape", etape=e, etat="erreur", video=v, message=r.group(2)))
            return evs + [self._ev(t, "erreur", message=r.group(2), video=v)]
        if corps.startswith("Kaggle : envoi du son"):
            evs = self._fin(t, "extraction", self.video_extraction) if self.video_extraction else []
            self.video_extraction = None
            return evs + self._debut(t, "envoi", None, corps.split(" : ", 1)[1])
        if corps.startswith("Kaggle : sessions GPU toutes prises"):
            return (self._fin(t, "envoi") + self._debut(t, "attente", None, "toutes les places GPU du compte sont prises")
                    + [self._ev(t, "kaggle", statut="attente d’une place", depuis_s=0)])
        if corps.startswith("Kaggle : noyau GPU lancé"):
            evs = self._fin(t, "envoi")
            if not self.ouvertes.get(("attente", None)):
                evs += self._debut(t, "attente", None, "noyau envoyé, en attente de démarrage")
            return evs
        r = STATUT_KAGGLE.match(corps)
        if r:
            statut = r.group(1).lower()
            depuis = float(r.group(2).replace(",", ".")) * 60
            evs = [self._ev(t, "kaggle", statut=statut, depuis_s=depuis)]
            if statut == "running":
                evs = self._fin(t, "envoi") + self._fin(t, "attente") + evs
                if not self.ouvertes.get(("kaggle", None)):
                    evs += self._debut(t, "kaggle")
            elif statut == "complete":
                evs += self._fin(t, "attente") + self._fin(t, "kaggle") + self._debut(t, "rapatriement")
            elif statut in ("error", "cancelacknowledged", "cancelrequested", "cancelled", "canceled"):
                for etape in ("attente", "kaggle"):
                    if self.ouvertes.pop((etape, None), None):
                        evs.append(self._ev(t, "etape", etape=etape, etat="erreur", message=f"noyau Kaggle : {statut}"))
            return evs
        if corps.startswith("GPU Kaggle : "):
            return self._fermer_session(t, "mix") + [self._ev(t, "message", message=corps)]
        r = re.match(r"^mixage (chant|parole)(?: v\d+)? : (.*)$", corps)
        if r:
            v = self.video_locale
            return (self._fin(t, "mix", v) + self._debut(t, "video", v)
                    + [self._ev(t, "message", message=corps, video=v)])
        if re.match(r"^mode (chant|parole) \(", corps) or corps.startswith("sous-titres non copiés") \
                or corps.startswith("comparaison "):
            return [self._ev(t, "message", message=corps, video=self.video_locale)]
        r = re.match(r"^fini : (.+)$", corps)
        if r:
            v = self.video_locale
            self.attend_mp3 = v
            return self._fin(t, "video", v) + [self._ev(t, "sortie", chemin=r.group(1).strip(), video=v)]
        return []

    def terminer(self, t=None):
        """Fin du processus : rien à déduire de plus (Avancement.terminer conclut avec le code de sortie)."""
        return []
