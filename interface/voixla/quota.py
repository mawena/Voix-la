"""Quota hebdomadaire de GPU Kaggle (sans GTK, testable) : heures utilisées et restantes, remise à zéro, et si un
doublage tient encore dans la semaine.

Règle de Kaggle
- Environ 30 h de GPU par semaine et par compte, parfois plus selon la demande. Une session sur deux cartes T4 compte
  une seule fois : c'est la durée de la session qui compte, pas le nombre de cartes.
- Remise à zéro chaque samedi à 00:00 UTC, sur une fenêtre fixe, non glissante. Vérifié le 06/11/2026 : c'est le
  « refreshAt » que rend « kaggle quota », et ce que dit le personnel de Kaggle.
- Le compteur de Kaggle avance pendant la session : relevé toutes les 4 min le 06/11/2026, il montait pendant qu'un
  noyau tournait et s'est arrêté à sa fin.

Sources, dans l'ordre
1. « kaggle quota --format json » (CLI officielle, 2.2.4 ou plus récente) : utilisé, total et remise à zéro. C'est une
   lecture seule, qui ne consomme rien. L'interface n'ouvre jamais le jeton : la CLI le lit elle-même. La lecture est
   gardée dans <état>/quota-kaggle.json ; ce qui a tourné depuis (session en cours, session finie après la lecture)
   s'y ajoute d'après le compte local.
2. Sans réponse de Kaggle : le compte local de la semaine, avec le quota et la remise à zéro des préférences
   (quota_h, quota_remise_jour, quota_remise_heure ; défaut : 30 h, samedi 00:00 UTC).

Compte local : les sessions GPU enregistrées sur ce poste (sessions_locales). Une session compte du démarrage du noyau
à sa fin. Les vidéos d'une même session (un lot) n'en font qu'une, et chaque session vue par plusieurs sources n'est
comptée qu'une fois (même version du noyau, ou mêmes heures : doubler n'a jamais deux sessions à la fois).
- rapport.json des doublages (temps_local.noyau_depuis_envoi_s − attente_demarrage_noyau_s, version_noyau) ;
- travaux de la file de l'interface (étape « kaggle » de leurs événements), réussis, en échec, annulés ou en cours ;
- session-en-cours.json et session-precedente.json de doubler, y compris pour un doublage lancé en ligne de commande ;
- sessions-gpu.jsonl, le journal durable de doubler et d'affiner (une ligne au départ, une à la fin de chaque session) :
  il survit au ménage des dossiers, contrairement aux sources ci-dessus ;
- outils préparés (doubler --preparer-outils : outils.json, durée estimée à OUTILS_S) ;
- essais du banc (banc/resultats/<S>/suivi.json, ou le journal du noyau rapatrié).
Ce qui échappe au compte local : voir LIMITES.
"""

import datetime
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import chemins, systeme
from .travaux import verrou_tenu, vivant

HEURE = 3600.0
SEMAINE_S = 7 * 86400.0
QUOTA_DEFAUT_H = 30.0
REMISE_JOUR = 5          # samedi (lundi = 0)
REMISE_HEURE = 0         # heure UTC
ALERTE_DEFAUT_H = 2.0
# marge sur l'estimation d'un doublage avant de dire qu'il tient : l'écart médian est d'environ −4 %, mais une chanson
# chargée en chœurs peut prendre jusqu'à 40 % de conversion en plus ; Kaggle ne vérifie le quota qu'au démarrage d'une
# session et ne laisse le dépasser qu'une fois par semaine (d'après son personnel)
MARGE = 0.2
OUTILS_S = 11 * 60.0     # doubler --preparer-outils : 10,6 min mesurées, durée non enregistrée
SESSION_MAX_S = 12 * HEURE   # une session Kaggle ne dépasse pas 12 h : borne d'une session « en cours » sans fin
REPRISE_APRES_S = 120    # un travail en attente part deux minutes après la remise à zéro (écart d'horloge)
AGE_MAX_S = 300          # une lecture de Kaggle plus récente est reprise telle quelle
DELAI_KAGGLE_S = 60
FICHIER_LECTURE = "quota-kaggle.json"
JOURNAL_GPU = "sessions-gpu.jsonl"   # écrit par doubler.py (journal_gpu), à côté de session-en-cours.json
DUREE_INCONNUE_S = HEURE   # session du journal jamais close, sans estimation : une heure (borne prudente)
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
# Que faire d'un doublage qui ne tient plus dans le reste du quota (préférence « quota_depassement »)
DEPASSEMENTS = (
    ("demander", "Demander"),
    ("attendre", "Attendre la remise à zéro"),
    ("lancer", "Lancer quand même"),
)
DEPASSEMENT_DEFAUT = "demander"

# Ce que le compte local ne voit pas (page État) ; « kaggle quota », lui, compte tout
LIMITES = (
    "les sessions lancées depuis un autre poste, ou sur le site de Kaggle (carnet interactif) ;",
    "un doublage en ligne de commande vers un autre dossier (--sortie) : seule la dernière session de doubler "
    "(session-precedente.json) mène à son rapport ;",
    "une session sans rapport (échec, annulation) lancée en ligne de commande et suivie d’une autre ;",
    "un doublage mis à la corbeille, sauf s’il est passé par la file de l’interface ;",
    "la durée exacte des outils préparés (estimée à 11 min) et des essais du banc sans suivi.",
)


# --- Temps et mise en forme ------------------------------------------------------------------------------------------

def remise_suivante(maintenant=None, jour=REMISE_JOUR, heure=REMISE_HEURE):
    """Prochaine remise à zéro (epoch) strictement après maintenant : le jour (lundi = 0) à l'heure UTC donnés."""
    maintenant = time.time() if maintenant is None else float(maintenant)
    d = datetime.datetime.fromtimestamp(maintenant, datetime.timezone.utc)
    base = d.replace(hour=int(heure) % 24, minute=0, second=0, microsecond=0)
    r = base + datetime.timedelta(days=(int(jour) - base.weekday()) % 7)
    if r.timestamp() <= maintenant:
        r += datetime.timedelta(days=7)
    return r.timestamp()


def heures(s):
    """Durée de quota : « 6 h 52 », « 30 h », « 45 min », « 0 min »."""
    if s is None:
        return "—"
    m = int(round(max(0.0, float(s)) / 60.0))
    h, m = divmod(m, 60)
    if not h:
        return f"{m} min"
    return f"{h} h {m:02d}" if m else f"{h} h"


def jour_heure(ts, maintenant=None):
    """« samedi 7 nov. à 01:00 » (heure locale) ; « aujourd'hui à 18:00 », « demain à 01:00 » au plus près."""
    if not ts:
        return "—"
    lt = time.localtime(ts)
    ln = time.localtime(time.time() if maintenant is None else maintenant)
    h = time.strftime("%H:%M", lt)
    jours = (datetime.date(lt.tm_year, lt.tm_mon, lt.tm_mday) - datetime.date(ln.tm_year, ln.tm_mon, ln.tm_mday)).days
    if jours == 0:
        return f"aujourd’hui à {h}"
    if jours == 1:
        return f"demain à {h}"
    return f"{JOURS[lt.tm_wday]} {lt.tm_mday} {MOIS[lt.tm_mon - 1]} à {h}"


def regle_remise(jour=REMISE_JOUR, heure=REMISE_HEURE):
    """« samedi 00:00 UTC »."""
    return f"{JOURS[int(jour) % 7]} {int(heure) % 24:02d}:00 UTC"


# --- Préférences -----------------------------------------------------------------------------------------------------

def _nombre(x, defaut=None):
    try:
        return float(x) if x is not None else defaut
    except (TypeError, ValueError):
        return defaut


def reglages(prefs=None):
    """(quota_s, jour, heure, alerte_s, depassement) d'après les préférences de l'interface."""
    p = prefs if prefs is not None else chemins.lire_preferences()
    quota_h = _nombre(p.get("quota_h"), QUOTA_DEFAUT_H)
    jour = int(_nombre(p.get("quota_remise_jour"), REMISE_JOUR)) % 7
    heure = int(_nombre(p.get("quota_remise_heure"), REMISE_HEURE)) % 24
    alerte_h = _nombre(p.get("quota_alerte_h"), ALERTE_DEFAUT_H)
    dep = p.get("quota_depassement")
    dep = dep if dep in {d for d, _l in DEPASSEMENTS} else DEPASSEMENT_DEFAUT
    return max(0.0, quota_h) * HEURE, jour, heure, max(0.0, alerte_h) * HEURE, dep


# --- Lecture de « kaggle quota » -------------------------------------------------------------------------------------

def _heures_texte(t):
    """« 23.13h » → secondes."""
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*h?\s*$", str(t))
    if not m:
        raise ValueError(f"durée illisible : {t!r}")
    return float(m.group(1)) * HEURE


def _date_utc(t):
    d = datetime.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    if d.utcoffset() is None:
        d = d.replace(tzinfo=datetime.timezone.utc)   # la CLI d'avant 2.2.5 rend l'heure UTC sans fuseau
    return d.timestamp()


def analyser_sortie_kaggle(texte, maintenant=None):
    """{utilise_s, total_s, remise, lu_le} d'après la sortie JSON de « kaggle quota --format json » (ligne GPU).
    Un avertissement de la CLI avant le JSON est ignoré. ValueError si la sortie est illisible ou sans GPU."""
    debut = texte.find("[")
    if debut < 0:
        raise ValueError("réponse sans JSON")
    lignes, _fin = json.JSONDecoder().raw_decode(texte[debut:])
    gpu = next((l for l in lignes if isinstance(l, dict) and str(l.get("resource", "")).upper() == "GPU"), None)
    if gpu is None:
        raise ValueError("pas de quota GPU dans la réponse")
    total = _heures_texte(gpu["total"])
    if "used" in gpu:
        utilise = _heures_texte(gpu["used"])
    else:
        utilise = total - _heures_texte(gpu["remaining"])
    return {"utilise_s": utilise, "total_s": total, "remise": _date_utc(gpu.get("refreshAt") or gpu.get("refresh_at")),
            "lu_le": time.time() if maintenant is None else maintenant}


def lire_kaggle(commande=None, delai=DELAI_KAGGLE_S):
    """(lecture, raison) : la lecture de « kaggle quota --format json » ({utilise_s, total_s, remise, lu_le}), ou None
    et la raison. Lecture seule ; le jeton n'est ouvert que par la CLI, jamais ici."""
    k = Path(commande) if commande else systeme.commande_kaggle()
    if not k.exists():
        return None, f"commande kaggle absente ({k})"
    try:
        r = subprocess.run([str(k), "quota", "--format", "json"], capture_output=True, text=True, timeout=delai,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, f"pas de réponse de Kaggle en {delai} s (réseau ?)"
    except OSError as e:
        return None, str(e)
    sortie = (r.stdout or "") + "\n" + (r.stderr or "")
    if r.returncode:
        if "invalid choice" in sortie and "quota" in sortie:
            return None, "CLI kaggle trop ancienne : pas de commande « quota » (2.2.4 ou plus récente)"
        lignes = [l.strip() for l in sortie.splitlines() if l.strip()]
        return None, (lignes[-1] if lignes else f"code {r.returncode}")[:200]
    try:
        return analyser_sortie_kaggle(r.stdout or ""), ""
    except (ValueError, KeyError, TypeError) as e:
        return None, f"réponse de « kaggle quota » illisible ({e})"


def fichier_lecture():
    return chemins.dossier_etat() / FICHIER_LECTURE


def derniere_lecture():
    """La dernière lecture réussie de Kaggle, gardée sur le disque, ou None."""
    d = chemins.lire_json(fichier_lecture(), None)
    if not isinstance(d, dict):
        return None
    try:
        return {k: float(d[k]) for k in ("utilise_s", "total_s", "remise", "lu_le")}
    except (KeyError, TypeError, ValueError):
        return None


def lecture_kaggle(age_max=AGE_MAX_S, commande=None, maintenant=None):
    """(lecture, raison) : la lecture gardée si elle a moins de age_max secondes, sinon une nouvelle lecture (gardée
    à son tour) ; si Kaggle ne répond pas, la dernière lecture gardée et la raison."""
    maintenant = time.time() if maintenant is None else maintenant
    gardee = derniere_lecture()
    if gardee and 0 <= maintenant - gardee["lu_le"] < age_max:
        return gardee, ""
    lecture, raison = lire_kaggle(commande)
    if lecture:
        try:
            chemins.ecrire_json(fichier_lecture(), lecture)
        except OSError:
            pass
        return lecture, ""
    return gardee, raison


# --- Sessions enregistrées sur ce poste ------------------------------------------------------------------------------

@dataclass
class Session:
    debut: float               # démarrage du noyau (epoch)
    duree_s: float
    genre: str                 # doublage, outils, banc
    origine: str               # rapport, travail, session-precedente, session-en-cours, outils, banc
    titre: str = ""
    version: int | None = None  # version du noyau de doublage (une par session)
    cle: str = ""              # identité pour les outils et le banc
    mesuree: bool = True       # faux : durée estimée
    en_cours: bool = False
    origines: list = field(default_factory=list)

    @property
    def fin(self):
        return self.debut + self.duree_s

    def part(self, debut, fin):
        """Secondes de cette session comprises entre debut et fin."""
        return max(0.0, min(self.fin, fin) - max(self.debut, debut))


LIBELLES_COMMANDE = {"doubler": "Doublage", "comparer": "Comparaison de voix", "affiner": "Affinage de voix",
                     "outils": "Outils préparés"}
PRIORITE = {"rapport": 0, "travail": 1, "journal": 1.5, "session-en-cours": 2, "session-precedente": 3, "outils": 4, "banc": 4}
LIBELLES_ORIGINE = {"rapport": "rapport", "travail": "file de l’interface", "journal": "journal des sessions GPU",
                    "session-en-cours": "session en cours",
                    "session-precedente": "dernière session de doubler", "outils": "outils préparés",
                    "banc": "banc d’essai"}


def _mtime(p):
    try:
        return Path(p).stat().st_mtime
    except OSError:
        return None


def duree_journal_noyau(fichier):
    """Durée de marche d'un noyau d'après son journal rapatrié (<noyau>.log : liste JSON aux champs « time »)."""
    try:
        lignes = json.loads(Path(fichier).read_text(encoding="utf-8", errors="replace"))
        return max(float(l.get("time") or 0) for l in lignes if isinstance(l, dict)) or None
    except (OSError, ValueError, TypeError):
        return None


def session_du_rapport(fichier):
    """La session GPU d'un rapport.json (temps_local de doubler), ou None. Sa fin vient du journal du noyau rapatrié
    (heure du fichier moins le rapatriement), sinon de la voix convertie rapatriée, sinon du rapport."""
    f = Path(fichier)
    r = chemins.lire_json(f, None)
    if not isinstance(r, dict):
        return None
    tl = r.get("temps_local") or {}
    k = _nombre(tl.get("noyau_depuis_envoi_s"))
    if not k:
        return None
    duree = max(0.0, k - (_nombre(tl.get("attente_demarrage_noyau_s"), 0.0) or 0.0))
    rapatriement = _nombre(tl.get("rapatriement_s"), 0.0) or 0.0
    fin = None
    for log in sorted(f.parent.glob("*.log")):
        t = _mtime(log)
        if t:
            fin = t - rapatriement
            break
    if fin is None:
        fin = _mtime(f.parent / "voix_convertie.flac")
    if fin is None:
        t = _mtime(f) or 0.0
        fin = t - sum(_nombre(tl.get(c), 0.0) or 0.0 for c in ("mix_local_s", "remise_video_s", "rapatriement_s"))
    v = tl.get("version_noyau")
    return Session(debut=fin - duree, duree_s=duree, genre="doublage", origine="rapport",
                   titre=f.parent.parent.parent.name, version=int(v) if isinstance(v, (int, float)) else None)


def rapports_recents(dossiers, depuis):
    """rapport.json des dossiers de sortie (<sorties>/<titre>/travail/<voix>/, et un niveau de sous-dossier) écrits
    depuis « depuis » : un rapport est toujours écrit après sa session."""
    vus, sortie = set(), []
    for base in dossiers:
        base = Path(base)
        for motif in ("*/travail/*/rapport.json", "*/*/travail/*/rapport.json"):
            try:
                fichiers = list(base.glob(motif))
            except OSError:
                continue
            for f in fichiers:
                t = _mtime(f)
                cle = str(f.resolve())
                if t is not None and t >= depuis and cle not in vus:
                    vus.add(cle)
                    sortie.append(f)
    return sortie


def _evenements(chemin):
    try:
        lignes = Path(chemin).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    evs = []
    for l in lignes:
        try:
            ev = json.loads(l)
        except ValueError:
            continue
        if isinstance(ev, dict):
            evs.append(ev)
    return evs


def _t(ev):
    try:
        return _date_utc(ev["t"])
    except (KeyError, ValueError, TypeError):
        return None


def sessions_des_travaux(racine, maintenant=None):
    """(sessions, rapports) des travaux de la file : l'étape « kaggle » de leurs événements (de la poussée du noyau à
    sa fin), et les rapport.json qu'ils ont produits. Un travail annulé ou interrompu pendant la session : le noyau a
    continué seul sur Kaggle, sa durée est estimée (estimation de doubler, au moins le temps déjà vu)."""
    maintenant = time.time() if maintenant is None else maintenant
    sessions, rapports = [], []
    try:
        dossiers = sorted(p for p in Path(racine).iterdir() if p.is_dir())
    except OSError:
        return sessions, rapports
    for d in dossiers:
        demande = chemins.lire_json(d / "demande.json", {}) or {}
        if not isinstance(demande, dict) or not demande.get("evenements"):
            continue
        etat = chemins.lire_json(d / "etat.json", {}) or {}
        pousse = fin = duree = depuis = estimation = None
        for ev in _evenements(demande["evenements"]):
            if ev.get("type") == "etape" and ev.get("etape") == "kaggle":
                if ev.get("etat") == "debut" and pousse is None:
                    pousse = _t(ev)
                elif ev.get("etat") in ("fin", "erreur"):
                    fin = _t(ev)
                    duree = _nombre(ev.get("duree_s"))
            elif ev.get("type") == "kaggle":
                depuis = _nombre(ev.get("depuis_s"), depuis)
                estimation = _nombre(ev.get("estimation_s"), estimation)
            elif ev.get("type") == "sortie" and ev.get("rapport"):
                rapports.append(Path(ev["rapport"]))
        if pousse is None:
            continue
        titre = demande.get("titre") or d.name
        statut = etat.get("statut")
        if statut == "en_cours" and not vivant(etat.get("pid"), etat.get("demarrage")):
            statut = "interrompu"   # doubler mort sans conclure (poste redémarré…)
        if duree is not None or fin is not None:
            s = Session(pousse, duree if duree is not None else max(0.0, fin - pousse), "doublage", "travail", titre)
        elif statut in (None, "en_cours"):
            s = Session(pousse, min(SESSION_MAX_S, max(0.0, maintenant - pousse)), "doublage", "travail", titre,
                        en_cours=True)
        else:
            # annulé, en échec ou interrompu pendant la session : le noyau a continué seul jusqu'à sa fin
            vu = max(depuis or 0.0, (_nombre(etat.get("fin"), pousse) or pousse) - pousse)
            prevu = estimation or _nombre((demande.get("estimations") or {}).get("kaggle"), 0.0) or 0.0
            s = Session(pousse, max(vu, prevu), "doublage", "travail", titre, mesuree=False)
        sessions.append(s)
    return sessions, rapports


def _session_doubler(fichier, origine, maintenant, en_cours=False):
    d = chemins.lire_json(fichier, None)
    if not isinstance(d, dict):
        return None, []
    pousse = _nombre(d.get("pousse_epoch"))
    if not pousse:
        return None, []
    noms = [str(t.get("nom")) for t in d.get("travaux") or [] if isinstance(t, dict) and t.get("nom")]
    titre = noms[0] + (f" (+{len(noms) - 1})" if len(noms) > 1 else "") if noms else "doublage"
    rapports = [Path(t["tv"]) / "rapport.json" for t in d.get("travaux") or [] if isinstance(t, dict) and t.get("tv")]
    v = d.get("version_noyau")
    version = int(v) if isinstance(v, (int, float)) else None
    if en_cours:
        return Session(pousse, min(SESSION_MAX_S, max(0.0, maintenant - pousse)), "doublage", origine, titre, version,
                       en_cours=True), rapports
    close = None
    try:
        close = _date_utc(d["close_le"])
    except (KeyError, ValueError, TypeError):
        pass
    if close and close > pousse:
        # de la poussée à la clôture : rapatriement compris (une demi-minute de trop, par prudence)
        return Session(pousse, close - pousse, "doublage", origine, titre, version, mesuree=False), rapports
    est = _nombre(d.get("estimation_s"), 0.0) or 0.0
    return Session(pousse, min(max(est, 0.0), max(0.0, maintenant - pousse)), "doublage", origine, titre, version,
                   mesuree=False), rapports


def sessions_de_doubler(etat_doubler, maintenant=None):
    """(sessions, rapports) d'après session-en-cours.json (en cours si doubler tient le verrou commun ; sinon
    interrompue, durée estimée) et session-precedente.json (la dernière session close), écrits par doubler qu'il soit
    lancé par l'interface ou en ligne de commande."""
    maintenant = time.time() if maintenant is None else maintenant
    etat_doubler = Path(etat_doubler)
    sessions, rapports = [], []
    en_cours = verrou_tenu(etat_doubler / "verrou-kaggle")
    for nom, origine, tenue in (("session-en-cours.json", "session-en-cours", en_cours),
                                ("session-precedente.json", "session-precedente", False)):
        s, r = _session_doubler(etat_doubler / nom, origine, maintenant, tenue)
        if s:
            sessions.append(s)
            rapports += r
    return sessions, rapports


def sessions_outils(fichiers):
    """Outils préparés (doubler --preparer-outils) : outils.json, écrit à la fin de la session ; durée non enregistrée,
    estimée à OUTILS_S. Une même préparation (même date) n'est comptée qu'une fois."""
    vues, sessions = set(), []
    for f in fichiers:
        t = _mtime(f)
        d = chemins.lire_json(f, None)
        if t is None or not isinstance(d, dict):
            continue
        cle = str(d.get("date") or d.get("commit") or t)
        if cle in vues:
            continue
        vues.add(cle)
        sessions.append(Session(t - OUTILS_S, OUTILS_S, "outils", "outils", "Outils préparés (--preparer-outils)",
                                cle=f"outils:{cle}", mesuree=False))
    return sessions


def sessions_banc(resultats):
    """Essais du banc : banc/resultats/<S>/suivi.json (poussée, durée depuis l'envoi), ou le journal du noyau
    rapatrié dans ce dossier."""
    sessions = []
    try:
        dossiers = sorted(p for p in Path(resultats).iterdir() if p.is_dir())
    except OSError:
        return sessions
    for d in dossiers:
        s = chemins.lire_json(d / "suivi.json", None)
        if not isinstance(s, dict) or not _nombre(s.get("pousse_epoch")):
            continue
        duree, mesuree = _nombre(s.get("noyau_depuis_envoi_s")), True
        if duree is None:
            duree = max((duree_journal_noyau(l) or 0.0 for l in d.glob("*.log")), default=0.0) or None
        if duree is None:
            continue   # ni fin ni journal : durée inconnue (voir LIMITES)
        sessions.append(Session(float(s["pousse_epoch"]), duree, "banc", "banc", f"Banc d’essai, session {d.name}",
                                cle=f"banc:{d.name}:{s['pousse_epoch']}", mesuree=mesuree))
    return sessions


def sessions_du_journal(fichier, etat_doubler=None, maintenant=None):
    """Les sessions Kaggle du journal durable sessions-gpu.jsonl de doubler : une ligne « debut », puis une ligne « fin »
    de même identifiant (la dernière compte). Les lignes illisibles, les fournisseurs autres que Kaggle (Modal et Runpod
    ne comptent pas dans le quota de Kaggle) et les fins sans début sont ignorés. Sans fin : la session est en cours si
    doubler tient le verrou commun (la plus récente seulement), sinon elle a été interrompue (poste redémarré…) et sa
    durée est estimée (estimation de doubler, au plus le temps écoulé)."""
    maintenant = time.time() if maintenant is None else maintenant
    debuts, fins = {}, {}
    for ev in _evenements(fichier):
        ident = ev.get("id")
        if not isinstance(ident, str) or ev.get("fournisseur") != "kaggle":
            continue
        if ev.get("evt") == "debut" and _nombre(ev.get("debut")):
            debuts.setdefault(ident, ev)
        elif ev.get("evt") == "fin":
            fins[ident] = ev
    verrou = etat_doubler is not None and verrou_tenu(Path(etat_doubler) / "verrou-kaggle")
    ouvertes = sorted((i for i in debuts if i not in fins), key=lambda i: _nombre(debuts[i]["debut"]))
    sessions = []
    for ident, d in debuts.items():
        debut = _nombre(d["debut"])
        v = d.get("version")
        version = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        commande = str(d.get("commande") or "doubler")
        genre = "outils" if commande == "outils" else "doublage"
        titre = LIBELLES_COMMANDE.get(commande, "Session GPU")
        f = fins.get(ident)
        en_cours = False
        mesuree = True
        if f is not None:
            duree = _nombre(f.get("duree_s"))
            if duree is None:
                fin = _nombre(f.get("fin"))
                duree = fin - debut if fin else None
            if duree is None:
                continue
        else:
            ecoule = max(0.0, maintenant - debut)
            if verrou and ident == ouvertes[-1]:
                en_cours, duree = True, min(SESSION_MAX_S, ecoule)
            else:
                mesuree = False
                duree = min(_nombre(d.get("estimation_s"), DUREE_INCONNUE_S) or DUREE_INCONNUE_S, ecoule)
        sessions.append(Session(debut, max(0.0, min(duree, SESSION_MAX_S)), genre, "journal", titre, version,
                                mesuree=mesuree, en_cours=en_cours))
    return sessions


def _meme(a, b):
    if a.genre != b.genre:
        return False
    if a.cle and b.cle:
        return a.cle == b.cle
    if (a.cle or b.cle) and a.genre != "outils":
        return False
    # (outils : la fiche outils.json a une clé, la ligne du journal n'en a pas : seules les heures les rapprochent)
    if a.version is not None and b.version is not None:
        return a.version == b.version
    # doubler ne tient qu'une session à la fois (verrou commun) : deux sessions qui se chevauchent sont la même
    return min(a.fin, b.fin) - max(a.debut, b.debut) > 10


def fusionner(sessions):
    """Une session par session réelle : celles vues par plusieurs sources sont fusionnées, la source la plus sûre
    donnant les heures (rapport, puis travail de la file, puis fichiers de session de doubler)."""
    sortie = []
    for s in sorted(sessions, key=lambda x: (PRIORITE.get(x.origine, 9), x.debut)):
        autre = next((o for o in sortie if _meme(o, s)), None)
        if autre is None:
            s.origines = [s.origine]
            sortie.append(s)
            continue
        if s.origine not in autre.origines:
            autre.origines.append(s.origine)
        autre.en_cours = autre.en_cours or s.en_cours
        if autre.version is None:
            autre.version = s.version
        if autre.en_cours and s.en_cours and s.fin > autre.fin:
            autre.duree_s = s.fin - autre.debut
    return sorted(sortie, key=lambda x: x.debut)


def sessions_locales(sorties=(), racine_travaux=None, etat_doubler=None, depot=None, depuis=None, maintenant=None):
    """Toutes les sessions GPU enregistrées sur ce poste, fusionnées (une par session réelle), commencées ou finies
    depuis « depuis ». sorties : dossiers des sorties à parcourir ; les rapports des travaux et des fichiers de session
    s'y ajoutent, où qu'ils soient."""
    maintenant = time.time() if maintenant is None else maintenant
    depuis = maintenant - SEMAINE_S if depuis is None else depuis
    racine_travaux = Path(racine_travaux) if racine_travaux else chemins.dossier_etat() / "travaux"
    etat_doubler = Path(etat_doubler) if etat_doubler else systeme.ETAT_DOUBLER
    depot = Path(depot) if depot else chemins.DEPOT
    s_travaux, r_travaux = sessions_des_travaux(racine_travaux, maintenant)
    s_doubler, r_doubler = sessions_de_doubler(etat_doubler, maintenant)
    s_journal = sessions_du_journal(etat_doubler / JOURNAL_GPU, etat_doubler, maintenant)
    rapports = rapports_recents(list(sorties), depuis)
    vus = {str(f.resolve()) for f in rapports}
    for f in r_travaux + r_doubler:
        try:
            cle = str(f.resolve())
        except OSError:
            continue
        t = _mtime(f)
        if cle not in vus and t is not None and t >= depuis:
            vus.add(cle)
            rapports.append(f)
    s_rapports = [s for s in (session_du_rapport(f) for f in rapports) if s]
    outils = [etat_doubler / "outils.json"] + [Path(s) / ".kaggle" / "outils.json" for s in sorties]
    toutes = (s_rapports + s_travaux + s_journal + s_doubler + sessions_outils(outils)
              + sessions_banc(depot / "banc" / "resultats"))
    return [s for s in fusionner(toutes) if s.fin >= depuis]


# --- État du quota ---------------------------------------------------------------------------------------------------

@dataclass
class EtatQuota:
    total_s: float
    utilise_s: float
    remise: float                  # prochaine remise à zéro (epoch)
    source: str                    # « kaggle » ou « local »
    remise_verifiee: bool          # donnée par Kaggle (sinon : réglage)
    sessions: list                 # sessions locales de la semaine
    local_s: float                 # compte local de la semaine
    lu_le: float | None = None     # lecture de Kaggle
    kaggle_s: float | None = None  # utilisé selon Kaggle, à la lecture
    ajoute_s: float = 0.0          # ajouté au chiffre de Kaggle : ce qui a tourné depuis la lecture
    en_cours_s: float = 0.0        # session en cours, déjà comptée
    raison: str = ""               # pourquoi Kaggle n'a pas répondu
    calcule_le: float = 0.0

    @property
    def reste_s(self):
        return max(0.0, self.total_s - self.utilise_s)

    @property
    def fraction(self):
        return min(1.0, self.utilise_s / self.total_s) if self.total_s > 0 else 1.0

    @property
    def debut_semaine(self):
        return self.remise - SEMAINE_S

    def alerte(self, seuil_s):
        return self.reste_s < seuil_s


def calculer(lecture, sessions, prefs=None, maintenant=None, raison=""):
    """EtatQuota d'après une lecture de Kaggle (ou None) et les sessions locales.

    Avec Kaggle : son total et sa remise à zéro ; utilisé = son chiffre + ce que les sessions locales ont tourné depuis
    la lecture (le compteur avance pendant une session ; la part d'avant la lecture y est déjà). Si la remise à zéro
    lue est passée, la semaine suivante repart de zéro (compte local). Sans Kaggle : le compte local, avec le quota et
    la remise à zéro des préférences."""
    maintenant = time.time() if maintenant is None else maintenant
    quota_s, jour, heure, _alerte, _dep = reglages(prefs)
    en_cours = sum(s.part(s.debut, maintenant) for s in sessions if s.en_cours)
    if lecture:
        remise = float(lecture["remise"])
        avancee = remise <= maintenant
        while remise <= maintenant:
            remise += SEMAINE_S
        debut = remise - SEMAINE_S
        local = sum(s.part(debut, min(remise, maintenant)) for s in sessions)
        if avancee:
            return EtatQuota(float(lecture["total_s"]), local, remise, "local", True, sessions, local,
                             lu_le=lecture["lu_le"], en_cours_s=en_cours, raison=raison, calcule_le=maintenant)
        lu = float(lecture["lu_le"])
        ajoute = sum(s.part(max(debut, lu), maintenant) for s in sessions)
        return EtatQuota(float(lecture["total_s"]), float(lecture["utilise_s"]) + ajoute, remise, "kaggle", True,
                         sessions, local, lu_le=lu, kaggle_s=float(lecture["utilise_s"]), ajoute_s=ajoute,
                         en_cours_s=en_cours, raison=raison, calcule_le=maintenant)
    remise = remise_suivante(maintenant, jour, heure)
    debut = remise - SEMAINE_S
    local = sum(s.part(debut, maintenant) for s in sessions)
    return EtatQuota(quota_s, local, remise, "local", False, sessions, local, en_cours_s=en_cours, raison=raison,
                     calcule_le=maintenant)


def etat(prefs=None, donnees=None, racine_travaux=None, age_max=AGE_MAX_S, interroger=True, maintenant=None,
         depot=None):
    """L'état du quota, tout compris (lent : peut lancer « kaggle quota », jusqu'à DELAI_KAGGLE_S ; à appeler hors du
    fil de l'interface). interroger faux : la dernière lecture gardée seulement, sans appel à Kaggle. depot : où
    chercher banc/resultats (défaut : le dépôt de l'interface)."""
    maintenant = time.time() if maintenant is None else maintenant
    if interroger:
        lecture, raison = lecture_kaggle(age_max)
    else:
        lecture, raison = derniere_lecture(), ""
    donnees = donnees or chemins.dossier_donnees()
    sessions = sessions_locales([chemins.dossier_sorties(donnees)], racine_travaux, depot=depot,
                                depuis=maintenant - SEMAINE_S - 86400, maintenant=maintenant)
    return calculer(lecture, sessions, prefs, maintenant, raison)


# --- Un doublage tient-il dans le reste ? ----------------------------------------------------------------------------

def besoin_s(demande):
    """Secondes de GPU que demandera un travail : la session Kaggle estimée (estimations « kaggle ») ; 0 pour une
    reprise (rapatriement seul, sans GPU)."""
    if not isinstance(demande, dict) or demande.get("reprise") or demande.get("type", "doublage") != "doublage":
        return 0.0
    return max(0.0, _nombre((demande.get("estimations") or {}).get("kaggle"), 0.0) or 0.0)


def gpu_a_venir(travaux_, maintenant=None):
    """Secondes de GPU que la file prendra encore cette semaine : le reste de la session du travail en cours, et les
    travaux en attente qui partiront avant la remise à zéro (pas ceux mis en attente du quota)."""
    maintenant = time.time() if maintenant is None else maintenant
    total = 0.0
    for t in travaux_:
        statut = t.statut
        if statut == "en_cours":
            besoin = besoin_s(t.demande)
            av = t.avancement
            e = av.etapes.get("kaggle")
            if e is not None and e.etat in ("fait", "saute", "erreur", "annule"):
                continue
            if e is not None and e.etat == "en_cours":
                est = av.estimation_doubler_s() or besoin
                total += max(0.0, est - e.ecoule_s(maintenant))
            else:
                total += besoin
        elif statut == "en_attente" and not t.attend_le_quota:
            total += besoin_s(t.demande)
    return total


@dataclass
class Verdict:
    besoin_s: float
    reste_s: float          # reste du quota, avant ce doublage
    a_venir_s: float        # pris par la file avant lui
    tient: bool
    remise: float

    @property
    def reste_apres_s(self):
        return self.reste_s - self.a_venir_s - self.besoin_s


def juger(etat_, besoin, a_venir=0.0, marge=MARGE):
    """Le doublage tient-il dans le reste du quota, après la file et avec la marge ?"""
    dispo = etat_.reste_s - a_venir
    return Verdict(besoin, etat_.reste_s, a_venir, besoin * (1 + marge) <= dispo, etat_.remise)


def juger_demandes(etat_, demandes, a_venir=0.0, marge=MARGE):
    """[Verdict] pour des demandes lancées ensemble, l'une après l'autre dans la file (chacune après les précédentes
    qui tiennent ; une demande qui attend la remise à zéro ne prend rien cette semaine)."""
    verdicts, pris = [], float(a_venir)
    for d in demandes:
        v = juger(etat_, besoin_s(d), pris, marge)
        if v.tient:
            pris += v.besoin_s
        verdicts.append(v)
    return verdicts


def mettre_en_attente(demande, remise, verdict=None):
    """La demande, mise en attente jusqu'à la remise à zéro (l'exécuteur ne la lance qu'à cette heure-là)."""
    d = dict(demande)
    d["attendre_jusqu_a"] = float(remise)
    d["quota"] = {"decision": "attendre", **_chiffres(verdict)}
    return d


def marquer(demande, decision, verdict=None):
    """La demande, avec la décision prise sur le quota (« tient » ou « lancer » : lancée quand même). « lancer » ajoute
    --sans-quota : doubler lit lui aussi le quota avant de pousser le noyau, et en --calcul auto il passerait sinon sur
    Modal, contre le choix fait ici de lancer sur Kaggle quand même."""
    d = {**demande, "quota": {"decision": decision, **_chiffres(verdict)}}
    return sans_bascule(d) if decision == "lancer" else d


def sans_bascule(demande):
    """La demande avec --sans-quota en fin d'argv, si elle a un argv et que doubler connaît l'option ; sinon telle quelle."""
    argv = demande.get("argv")
    if not isinstance(argv, list) or "--sans-quota" in argv:
        return demande
    from . import moteur
    if "--sans-quota" not in moteur.capacites():
        return demande
    return {**demande, "argv": [*argv, "--sans-quota"]}


def _chiffres(v):
    if v is None:
        return {}
    return {"besoin_s": round(v.besoin_s), "reste_s": round(v.reste_s), "a_venir_s": round(v.a_venir_s)}


def phrase_estimation(v):
    """« Ce doublage prendra environ 23 min de GPU ; il restera 6 h 29 cette semaine. »"""
    texte = f"Ce doublage prendra environ {heures(v.besoin_s)} de GPU"
    if v.tient:
        return f"{texte} ; il restera {heures(max(0.0, v.reste_apres_s))} cette semaine."
    dispo = max(0.0, v.reste_s - v.a_venir_s)
    return f"{texte}, il n’en reste que {heures(dispo)} cette semaine" + (
        f" (dont {heures(v.a_venir_s)} déjà promises à la file)" if v.a_venir_s >= 60 else "") + "."
