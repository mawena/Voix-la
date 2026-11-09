"""État du poste et de Kaggle (sans GTK). Toutes ces fonctions sont lentes ou peuvent l'être : à appeler hors du fil
de l'interface. Le jeton Kaggle n'est jamais lu : seule sa présence est vérifiée."""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import chemins
from .travaux import verrou_tenu

JETONS = (Path.home() / ".kaggle" / "access_token", Path.home() / ".kaggle" / "kaggle.json")
STATUT = re.compile(r'status "?(?:\w+\.)?(\w+)"?', re.I)


def memoire():
    """(disponible, totale) en Go, d'après /proc/meminfo."""
    vals = {}
    try:
        for ligne in Path("/proc/meminfo").read_text().splitlines():
            cle, _, reste = ligne.partition(":")
            if cle in ("MemAvailable", "MemTotal"):
                vals[cle] = int(reste.split()[0]) / 1024 / 1024
    except (OSError, ValueError, IndexError):
        return None, None
    return vals.get("MemAvailable"), vals.get("MemTotal")


def charge():
    """(charge sur 1 min, nombre de cœurs)."""
    try:
        return os.getloadavg()[0], os.cpu_count() or 1
    except OSError:
        return None, os.cpu_count() or 1


def file_attente(timeout=10):
    """Sortie de « file-attente --etat » (file des travaux lourds du poste), ou None si l'outil manque."""
    outil = shutil.which("file-attente")
    if not outil:
        return None
    try:
        r = subprocess.run([outil, "--etat"], capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError):
        return None


def jeton_kaggle_present():
    """Le fichier du jeton existe-t-il ? (son contenu n'est jamais ouvert)"""
    return any(p.exists() for p in JETONS)


def commande_kaggle():
    if os.environ.get("VOIXLA_KAGGLE"):
        return Path(os.environ["VOIXLA_KAGGLE"]).expanduser()
    return Path.home() / "kaggle-venv" / "bin" / "kaggle"


def statut_noyau(ref, timeout=120):
    """(statut en minuscules ou None, texte) de « kaggle kernels status <ref> », avec une limite de temps comme dans
    doubler (une coupure réseau ne doit jamais figer l'appel)."""
    k = commande_kaggle()
    if not k.exists():
        return None, f"commande kaggle absente ({k})"
    try:
        r = subprocess.run([str(k), "kernels", "status", ref], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"pas de réponse en {timeout} s (réseau ?)"
    except OSError as e:
        return None, str(e)
    texte = ((r.stdout or "") + (r.stderr or "")).strip()
    m = STATUT.search(texte)
    if r.returncode or not m:
        return None, texte.splitlines()[-1] if texte else f"code {r.returncode}"
    return m.group(1).lower(), texte


def noyaux_connus(config_effective):
    """[(libellé, référence)] : noyaux de doubler (doublage, outils) et du banc d'essai s'il existe."""
    k = (config_effective or {}).get("kaggle") or {}
    noyaux = []
    if k.get("noyau"):
        noyaux.append(("Doublage", k["noyau"]))
    if k.get("outils"):
        noyaux.append(("Outils préparés", k["outils"]))
    banc = chemins.DEPOT / "banc" / "banc.py"
    try:
        m = re.search(r"^NOYAU = '([^']+)'", banc.read_text(), re.M)
        if m and m.group(1) not in [r for _, r in noyaux]:
            noyaux.append(("Banc d’essai", m.group(1)))
    except OSError:
        pass
    return noyaux


EN_COURS = ("queued", "running", "new", "pending")


# verrou commun et fichiers de session de doubler (toujours ~/.local/state/voix-la pour doubler) ;
# VOIXLA_ETAT_DOUBLER le déplace pour les essais et les captures seulement
ETAT_DOUBLER = (Path(os.environ["VOIXLA_ETAT_DOUBLER"]).expanduser() if os.environ.get("VOIXLA_ETAT_DOUBLER")
                else Path.home() / ".local" / "state" / "voix-la")


def verrou_doubler(donnees):
    """'pris', 'libre' ou 'absent' (doubler n'a encore jamais tourné). doubler tient deux verrous : le verrou commun
    (~/.local/state/voix-la/verrou-kaggle) et l'ancien, dans sorties/.kaggle/verrou."""
    fichiers = [ETAT_DOUBLER / "verrou-kaggle", chemins.fichier_verrou_doubler(donnees)]
    presents = [f for f in fichiers if f.exists()]
    if not presents:
        return "absent"
    return "pris" if any(verrou_tenu(f) for f in presents) else "libre"


def session_a_reprendre():
    """La session Kaggle interrompue que « doubler --reprendre » saurait finir (session-en-cours.json), ou None.
    Ignorée tant qu'un doubler tient le verrou commun (la session est alors la sienne, en cours)."""
    f = ETAT_DOUBLER / "session-en-cours.json"
    if not f.exists() or verrou_tenu(ETAT_DOUBLER / "verrou-kaggle"):
        return None
    d = chemins.lire_json(f, {}) or {}
    return d if isinstance(d, dict) else {"fichier": str(f)}


def session_existante():
    """Le contenu de session-en-cours.json (dict, éventuellement vide), qu'un doubler la suive ou non ; None si elle
    n'existe pas. Contrairement à session_a_reprendre, la session suivie par un doubler vivant est rendue aussi."""
    f = ETAT_DOUBLER / "session-en-cours.json"
    if not f.exists():
        return None
    d = chemins.lire_json(f, {}) or {}
    return d if isinstance(d, dict) else {}


NOYAU_SYNTAXE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def url_noyau(session):
    """Adresse de la page du notebook de la session sur Kaggle (https://www.kaggle.com/code/<compte>/<noyau>), d'après
    le champ « noyau » du fichier de session ; None s'il manque ou s'il n'a pas la forme <compte>/<noyau>."""
    noyau = (session or {}).get("noyau")
    if not isinstance(noyau, str) or not NOYAU_SYNTAXE.match(noyau.strip()):
        return None
    return "https://www.kaggle.com/code/" + noyau.strip()


def _session_suivie(session):
    """Vrai si un doubler vivant tient encore un des verrous de la session : le verrou commun, et l'ancien verrou du
    dossier de sorties de la session (doubler prend les deux, voir sa classe Verrou)."""
    verrous = [ETAT_DOUBLER / "verrou-kaggle"]
    sortie = (session or {}).get("sortie")
    if isinstance(sortie, str) and sortie:
        verrous.append(Path(sortie) / ".kaggle" / "verrou")
    return any(verrou_tenu(v) for v in verrous)


def abandonner_session(file=None, attente_s=20.0, maintenant=None):
    """Abandonne la session Kaggle en cours : session-en-cours.json est RENOMMÉ (jamais effacé) en
    session-abandonnee-<AAAAMMJJ-HHMM>.json dans le même dossier, et doubler ne la reprend plus ni ne la refuse.
    Si « file » (FileTravaux) est donnée, les travaux de reprise encore actifs sont d'abord annulés par le mécanisme
    habituel (marque « annuler »), puis on attend jusqu'à « attente_s » secondes que doubler lâche le verrou.
    Rend (état, détail) :
    - ("fait", chemin du fichier renommé) ;
    - ("absente", None) : pas de session ;
    - ("suivie", None) : un doubler vivant suit encore la session, rien n'est renommé ;
    - ("erreur", message). La page du notebook (url_noyau) reste à fermer à la main sur Kaggle : l'outil kaggle ne sait
    pas annuler une version en attente."""
    f = ETAT_DOUBLER / "session-en-cours.json"
    if file is not None:
        file.annuler_reprises()
    limite = time.monotonic() + (attente_s if file is not None else 0)
    while True:
        if not f.exists():
            return "absente", None
        session = chemins.lire_json(f, {}) or {}
        if not _session_suivie(session):
            break
        if time.monotonic() >= limite:
            return "suivie", None
        time.sleep(0.25)
    t = time.localtime(maintenant) if maintenant is not None else time.localtime()
    base = "session-abandonnee-" + time.strftime("%Y%m%d-%H%M", t)
    dest = f.with_name(base + ".json")
    n = 1
    while dest.exists():   # deux abandons dans la même minute : on ne remplace jamais un fichier gardé
        n += 1
        dest = f.with_name(f"{base}-{n}.json")
    try:
        os.rename(f, dest)
    except OSError as e:
        return "erreur", str(e)
    return "fait", dest
