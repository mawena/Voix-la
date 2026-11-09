"""Les notebooks du compte Kaggle en attente ou en cours (sans GTK, testable) : ce que l'interface montre dans la
section « Sur Kaggle » de la page Travaux.

Ce que Kaggle donne
- « kaggle kernels list --mine --sort-by dateRun -v » : les notebooks du compte, du dernier lancé au plus ancien (CSV :
  ref, title, author, lastRunTime, totalVotes ; l'heure est en UTC, sans fuseau).
- « kaggle kernels status <ref> » : l'état du dernier lancement (QUEUED, RUNNING, COMPLETE, ERROR, CANCEL_REQUESTED,
  CANCEL_ACKNOWLEDGED…).
Ce que Kaggle ne donne PAS : la place dans sa file, l'heure de démarrage prévue, la machine demandée, ni de quoi
annuler une version (« kaggle kernels delete » supprime tout le notebook : jamais appelé ici). Pour arrêter une
session, l'utilisateur ouvre la page du notebook (url_page).

Coût et prudence
- Un tour = 1 appel « list » + 1 appel « status » par notebook lancé depuis moins de 24 h (au plus MAX_STATUTS, par 3 en
  parallèle) : au plus 1 + MAX_STATUTS appels, et pas plus d'un tour toutes les AGE_MAX_S secondes (cache partagé par
  les pages). Le bouton « Actualiser » peut forcer un tour, mais pas plus d'un toutes les AGE_MIN_S secondes.
- Rien ne s'exécute dans le fil GTK : l'appelant lance lire() dans un fil. Chaque appel a son délai maxi. Une erreur
  (commande absente, réseau, réponse illisible) donne une lecture « indisponible » avec la raison, jamais une exception.
- Le jeton n'est jamais ouvert ici : la CLI le lit elle-même.
"""

import csv
import datetime
import io
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import chemins, systeme

HEURE = 3600.0
NB_LISTE = 12            # notebooks lus dans « list » (les derniers lancés)
RECENT_S = 24 * HEURE    # seuls les notebooks lancés depuis moins de 24 h ont leur état lu
MAX_STATUTS = 5          # appels « status » par tour au plus
PARALLELE = 3
AGE_MAX_S = 60           # une lecture plus récente est reprise telle quelle
AGE_MIN_S = 20           # un tour forcé (« Actualiser ») attend au moins ceci après le précédent
DELAI_LISTE_S = 30
DELAI_STATUT_S = 20

# état brut de Kaggle (minuscules, sans « KernelWorkerStatus. ») → catégorie
CATEGORIES = {
    "queued": "attente", "new": "attente", "pending": "attente",
    "running": "cours",
    "cancel_requested": "arret",
    "complete": "fini",
    "error": "echec",
    "cancel_acknowledged": "annule",
}
ACTIVES = ("attente", "cours", "arret")
LIBELLES = {
    "attente": "En attente d’un GPU", "cours": "En cours", "arret": "Arrêt demandé", "fini": "Terminé",
    "echec": "Échec", "annule": "Arrêté", "inconnu": "État illisible", "ancien": "Ancien",
}
STYLES = {"attente": "alerte", "cours": "accent", "arret": "alerte", "fini": "succes", "echec": "erreur",
          "annule": "neutre", "inconnu": "neutre", "ancien": "neutre"}


@dataclass
class Notebook:
    ref: str
    titre: str
    lance_le: float | None        # secondes epoch, None si illisible
    etat: str = ""                # état brut en minuscules (« queued »), vide si non lu
    categorie: str = "inconnu"
    voixla: bool = False          # le notebook de Voix-là (celui de doubler)
    erreur: str = ""

    @property
    def actif(self):
        return self.categorie in ACTIVES

    @property
    def url(self):
        return url_page(self.ref)

    @property
    def libelle(self):
        return LIBELLES.get(self.categorie, self.categorie)

    @property
    def style(self):
        return STYLES.get(self.categorie, "neutre")


@dataclass
class Lecture:
    notebooks: list = field(default_factory=list)
    lu_le: float = 0.0
    indisponible: str = ""        # raison si la liste n'a pas pu être lue ; vide sinon
    partielle: bool = False       # au moins un état n'a pas pu être lu

    @property
    def ok(self):
        return not self.indisponible

    @property
    def actifs(self):
        return sorted((n for n in self.notebooks if n.actif), key=lambda n: -(n.lance_le or 0))

    @property
    def termines(self):
        return sorted((n for n in self.notebooks if not n.actif), key=lambda n: -(n.lance_le or 0))

    @property
    def en_attente(self):
        return [n for n in self.notebooks if n.categorie == "attente"]


# --- Lecture de la sortie de Kaggle ------------------------------------------------------------------------------------

def url_page(ref):
    """Page du notebook sur Kaggle, ou None si la référence n'a pas la forme <compte>/<notebook>."""
    ref = (ref or "").strip()
    return "https://www.kaggle.com/code/" + ref if systeme.NOYAU_SYNTAXE.match(ref) else None


def _date_utc(texte):
    try:
        d = datetime.datetime.fromisoformat((texte or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.timestamp()


def analyser_liste(texte):
    """[(ref, titre, lance_le)] d'après le CSV de « kernels list -v » ; ValueError si ce n'est pas le CSV attendu.
    Un éventuel avertissement de version avant l'en-tête est ignoré."""
    lignes = (texte or "").splitlines()
    for i, l in enumerate(lignes):
        if l.startswith("ref,"):
            lignes = lignes[i:]
            break
    else:
        if any(l.strip() for l in lignes):
            raise ValueError("liste illisible")
        return []
    sortie = []
    for r in csv.DictReader(io.StringIO("\n".join(lignes))):
        ref = (r.get("ref") or "").strip()
        if ref:
            sortie.append((ref, (r.get("title") or "").strip() or ref.split("/")[-1], _date_utc(r.get("lastRunTime"))))
    return sortie


def analyser_statut(texte):
    """L'état brut en minuscules (« queued ») d'après la sortie de « kernels status », ou None."""
    m = systeme.STATUT.search(texte or "")
    return m.group(1).lower() if m else None


def refs_voixla(etat_doubler=None, conf=None):
    """Références (minuscules) des notebooks de Voix-là : le champ « noyau » des fichiers de session de doubler et
    le noyau de la configuration (compte/nom). Sans compte configuré, la configuration ne donne rien."""
    refs = set()
    dossier = Path(etat_doubler) if etat_doubler else systeme.ETAT_DOUBLER
    for nom in ("session-en-cours.json", "session-precedente.json"):
        d = chemins.lire_json(dossier / nom, {})
        n = d.get("noyau") if isinstance(d, dict) else None
        if isinstance(n, str) and n.strip():
            refs.add(n.strip().lower())
    try:
        k = chemins.kaggle(conf)
        r = chemins.ref_kaggle(k.get("compte"), k.get("noyau"))
        if r:
            refs.add(r.lower())
    except Exception:  # noqa: BLE001 — la configuration illisible ne doit pas empêcher la lecture
        pass
    return refs


# --- Appels à Kaggle -----------------------------------------------------------------------------------------------------

def _appel(commande, args, delai):
    """(code, texte) d'une commande kaggle ; ('absente'|'delai'|'erreur', raison) si elle n'a pas pu tourner."""
    try:
        r = subprocess.run([str(commande), *args], capture_output=True, text=True, timeout=delai,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "delai", f"pas de réponse de Kaggle en {delai} s (réseau ?)"
    except OSError as e:
        return "erreur", str(e)
    return r.returncode, (r.stdout or "") + "\n" + (r.stderr or "")


def _derniere_ligne(texte, defaut):
    lignes = [l.strip() for l in (texte or "").splitlines() if l.strip()]
    return (lignes[-1] if lignes else defaut)[:200]


def lire(commande=None, maintenant=None, voixla=None, nombre=NB_LISTE, delai_liste=DELAI_LISTE_S,
         delai_statut=DELAI_STATUT_S):
    """Un tour complet : la liste des derniers notebooks du compte, puis l'état de ceux lancés depuis moins de 24 h.
    Toujours une Lecture (jamais d'exception) ; à appeler hors du fil de l'interface. voixla : références du notebook
    de Voix-là (défaut : refs_voixla())."""
    maintenant = time.time() if maintenant is None else maintenant
    k = Path(commande) if commande else systeme.commande_kaggle()
    if not k.exists():
        return Lecture(lu_le=maintenant, indisponible=f"commande kaggle absente ({k})")
    code, texte = _appel(k, ["kernels", "list", "--mine", "--sort-by", "dateRun", "--page-size", str(nombre), "-v"],
                         delai_liste)
    if code != 0:
        if code in ("delai", "erreur"):
            raison = texte
        else:
            raison = _derniere_ligne(texte, f"code {code}")
        return Lecture(lu_le=maintenant, indisponible=raison)
    try:
        liste = analyser_liste(texte)
    except ValueError as e:
        return Lecture(lu_le=maintenant, indisponible=f"réponse de Kaggle illisible ({e})")
    connus = {r.lower() for r in (refs_voixla() if voixla is None else voixla)}
    notebooks = [Notebook(ref, titre, quand, voixla=ref.lower() in connus) for ref, titre, quand in liste[:nombre]]
    recents = [n for n in notebooks if n.lance_le is not None and maintenant - n.lance_le < RECENT_S]
    recents.sort(key=lambda n: -n.lance_le)
    a_lire = [n for n in recents if systeme.NOYAU_SYNTAXE.match(n.ref)][:MAX_STATUTS]
    for n in notebooks:
        if n not in a_lire:
            n.categorie = "ancien" if n not in recents else "inconnu"
            n.erreur = "" if n not in recents else "état non lu (limite d’appels par tour)"

    def statut(n):
        code, texte = _appel(k, ["kernels", "status", n.ref], delai_statut)
        etat = analyser_statut(texte) if code == 0 else None
        if etat is None:
            n.erreur = texte if code in ("delai", "erreur") else _derniere_ligne(texte, f"code {code}")
            return
        n.etat, n.categorie = etat, CATEGORIES.get(etat, "inconnu")

    if a_lire:
        with ThreadPoolExecutor(max_workers=PARALLELE) as pool:
            list(pool.map(statut, a_lire))
    partielle = any(n.categorie == "inconnu" and n in a_lire for n in notebooks)
    # les anciens (plus de 24 h ou non lus faute de place) ne sont pas montrés
    gardes = [n for n in notebooks if n.categorie != "ancien" and (n in a_lire or n.categorie == "inconnu")]
    return Lecture(notebooks=gardes, lu_le=maintenant, partielle=partielle)


# --- Cache et délais -------------------------------------------------------------------------------------------------

class Suivi:
    """Cache partagé : les pages demandent, un seul tour à la fois, jamais plus d'un par AGE_MAX_S sauf demande
    explicite (alors pas avant AGE_MIN_S). Thread-safe ; lire_si_besoin() est à appeler hors du fil GTK."""

    def __init__(self, lecteur=None):
        self._lecteur = lecteur or lire
        self._verrou = threading.Lock()
        self.derniere = None          # dernière Lecture (réussie ou non)
        self.dernier_tour = 0.0
        self.appels = 0               # nombre de tours faits (pour les essais)

    def a_relire(self, visible=True, travail_actif=False, forcer=False, maintenant=None):
        """Faut-il faire un tour ? Seulement si la page est visible ou si un travail Voix-là est en cours, et si la
        dernière lecture a plus de AGE_MAX_S secondes (forcer : AGE_MIN_S)."""
        maintenant = time.time() if maintenant is None else maintenant
        if not (visible or travail_actif or forcer):
            return False
        age = maintenant - self.dernier_tour
        return age >= (AGE_MIN_S if forcer else AGE_MAX_S)

    def lire_si_besoin(self, visible=True, travail_actif=False, forcer=False, maintenant=None, **options):
        """(lecture, nouveau) : la lecture en cache, relue si besoin. Si un tour est déjà en cours dans un autre fil,
        rend le cache sans attendre (nouveau False)."""
        if not self._verrou.acquire(blocking=False):
            return self.derniere, False
        try:
            if not self.a_relire(visible, travail_actif, forcer, maintenant):
                return self.derniere, False
            self.dernier_tour = time.time() if maintenant is None else maintenant
            self.appels += 1
            self.derniere = self._lecteur(maintenant=maintenant, **options)
            return self.derniere, True
        finally:
            self._verrou.release()


SUIVI = Suivi()


# --- Mise en forme ---------------------------------------------------------------------------------------------------

def depuis(n, maintenant=None):
    """« lancé il y a 1 h 32 » ; « lancé à l’instant » ; « lancé à une heure inconnue »."""
    maintenant = time.time() if maintenant is None else maintenant
    if n.lance_le is None:
        return "heure de lancement inconnue"
    s = max(0.0, maintenant - n.lance_le)
    if s < 60:
        return "lancé à l’instant"
    if s < HEURE:
        return f"lancé il y a {int(s // 60)} min"
    h, m = divmod(int(s // 60), 60)
    if h >= 24:
        return f"lancé il y a {h // 24} j {h % 24} h"
    return f"lancé il y a {h} h {m:02d}"


def aide(lecture):
    """Phrase d'aide sous la liste : ce que Kaggle ne dit pas, et, si plusieurs sessions attendent, pourquoi."""
    texte = ("Kaggle ne donne ni la place dans sa file, ni l’heure de démarrage prévue : on ne voit que l’état de "
             "chaque notebook du compte, lu par la ligne de commande. Si rien ne démarre, la cause peut aussi être un "
             "incident chez Kaggle : la page du notebook affiche alors une bannière d’incident.")
    if lecture is not None and lecture.actifs:
        texte += (" Pour arrêter une session, ouvrez la page de son notebook : la ligne de commande ne sait pas "
                  "l’annuler.")
    if lecture is not None and len(lecture.en_attente) >= 2:
        texte += (" Plusieurs sessions de ce compte attendent en même temps : Kaggle peut les faire passer une par "
                  "une, ou n’avoir plus de GPU libre.")
    return texte


def resume(lecture):
    """Une ligne pour la page État : « 1 en attente d’un GPU, 1 en cours sur le compte »."""
    if lecture is None:
        return "pas encore lu"
    if not lecture.ok:
        return "indisponible : " + lecture.indisponible
    att = len(lecture.en_attente)
    cours = len([n for n in lecture.notebooks if n.categorie in ("cours", "arret")])
    morceaux = []
    if att:
        morceaux.append(f"{att} en attente d’un GPU")
    if cours:
        morceaux.append(f"{cours} en cours")
    if not morceaux:
        return "rien en attente ni en cours sur le compte"
    return ", ".join(morceaux) + " sur le compte"


def heure_lecture(lecture):
    return time.strftime("%H:%M", time.localtime(lecture.lu_le)) if lecture and lecture.lu_le else "—"
