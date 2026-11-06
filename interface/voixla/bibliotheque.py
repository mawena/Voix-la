"""Bibliothèque des doublages (sans GTK, testable) : ce que la page Résultats montre de chaque doublage, le
regroupement des versions d'une même chanson, le tri, les filtres et la recherche, les couleurs des pochettes générées
et le choix d'affichage gardé dans interface.json.

- Une **version** est un résultat (un dossier de sortie × une voix, voir voixla.rapports) avec ce qu'on en affiche,
  calculé une fois, dans le fil du chargement : titre, voix, mode, durée, date, badges (auto-tune, duo, extrait,
  langue des paroles, parole), fichier d'où tirer l'image, clés de tri et texte de recherche.
- Une **entrée** est ce qu'affiche une carte (grille) ou une ligne (liste) : une version seule, ou toutes les versions
  d'une même chanson (même vidéo source, voix différentes), la plus récente d'abord. regrouper(…, False) déplie tout.
- Le **filtre** garde une entrée dont au moins une version passe ; ouvrir une entrée ouvre la première version qui passe.
- Le **tri** compare des clés calculées d'avance (cles_tri) : la page s'en sert dans ses Gtk.CustomSorter.
"""

import hashlib
import json
import os
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import chemins, formats, moteur

# --- Choix d'affichage (interface.json, clé « bibliotheque ») --------------------------------------------------------

CLE_PREFERENCES = "bibliotheque"
VUES = ("grille", "liste")
TRIS = ("date", "titre", "voix", "duree", "mode")
LIBELLES_TRI = {"date": "Date", "titre": "Titre", "voix": "Voix", "duree": "Durée", "mode": "Mode"}
# ordre naturel : le plus récent d'abord pour la date, croissant (A → Z, le plus court) pour le reste
DECROISSANT = {"date": True, "titre": False, "voix": False, "duree": False, "mode": False}
PREFERENCES = {"vue": "grille", "tri": "date", "inverse": False, "regrouper": True}


def declarer_preferences():
    """Déclare le défaut de la clé « bibliotheque » d'interface.json auprès de voixla.chemins, qui ne lit et n'écrit que
    les clés de PREFERENCES_DEFAUT : sans cela, la moindre autre préférence enregistrée l'effacerait. Idempotent."""
    chemins.PREFERENCES_DEFAUT.setdefault(CLE_PREFERENCES, dict(PREFERENCES))


def preferences(prefs):
    """Le choix d'affichage d'après les préférences (prefs.get(clé, défaut)), une valeur inconnue remplacée par son
    défaut."""
    lu = (prefs or {}).get(CLE_PREFERENCES, {})
    lu = lu if isinstance(lu, dict) else {}
    p = dict(PREFERENCES)
    if lu.get("vue") in VUES:
        p["vue"] = lu["vue"]
    if lu.get("tri") in TRIS:
        p["tri"] = lu["tri"]
    for cle in ("inverse", "regrouper"):
        if isinstance(lu.get(cle), bool):
            p[cle] = lu[cle]
    return p


def decroissant(tri, inverse=False):
    """Le tri se fait-il en ordre décroissant ? (ordre naturel du critère, retourné si inverse)."""
    return DECROISSANT.get(tri, False) != bool(inverse)


def inverse_de(tri, decroissant_):
    """Le contraire de decroissant() : « inverse » d'après le sens affiché par l'en-tête d'une colonne."""
    return DECROISSANT.get(tri, False) != bool(decroissant_)


# --- Texte ---------------------------------------------------------------------------------------------------------

def normaliser(texte):
    """Sans accents ni casse (« Été » → « ete »), pour la recherche et le tri."""
    t = unicodedata.normalize("NFKD", str(texte or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).casefold()


LANGUES = {"fr": "français", "en": "anglais", "es": "espagnol", "pt": "portugais", "it": "italien", "de": "allemand",
           "nl": "néerlandais", "ja": "japonais", "ko": "coréen", "zh": "chinois", "ar": "arabe", "ru": "russe",
           "tr": "turc", "hi": "hindi", "ln": "lingala", "sw": "swahili", "yo": "yoruba", "wo": "wolof",
           "ha": "haoussa", "pl": "polonais", "el": "grec", "he": "hébreu", "id": "indonésien", "vi": "vietnamien",
           "uk": "ukrainien", "ro": "roumain", "sv": "suédois", "ca": "catalan", "la": "latin"}


def nom_langue(code):
    return LANGUES.get(code or "", code or "")


# --- Versions --------------------------------------------------------------------------------------------------------

@dataclass
class Badge:
    texte: str
    style: str           # neutre, accent, alerte
    infobulle: str
    cle: str             # autotune, duo, extrait, langue, parole, inacheve, repli


@dataclass
class Version:
    res: object                       # voixla.rapports.Resultat
    cle: str                          # Resultat.cle
    titre: str
    voix: str                         # nom affiché (« Jules », « Jules et Marie »)
    voix_ids: tuple                   # voix réelles (« jules », « marie »)
    mode: str                         # chant, parole ou ''
    duree_s: float | None
    date: float
    autotune: bool
    duo: bool                         # une voix par chanteur
    extrait: tuple | None             # (début, durée) demandés
    langue: str                       # code des paroles (« es »), ou ''
    complet: bool
    repli: bool                       # YingMusic-SVC a échoué, Seed-VC a pris le relais
    chanson: str                      # clé de regroupement (voir cle_chanson)
    image: Path | None = None         # d'où tirer l'image : la vidéo doublée, sinon le MP3 (sa pochette)
    image_sig: tuple | None = None    # (chemin, taille, date) de ce fichier : une autre image après un remix
    groupe: str = ""
    recherche: str = ""               # titre, voix et groupe, sans accents ni casse
    tri_titre: str = ""
    tri_voix: str = ""

    @property
    def cle_lecture(self):
        """La clé de la lecture de ce résultat (voixla.lecture.Etat.cle, sans le genre)."""
        return str(self.res.dossier), self.res.voix or ""

    def signature(self):
        return (self.cle, self.titre, self.voix, self.mode, self.duree_s, self.date, self.autotune, self.duo,
                self.extrait, self.langue, self.complet, self.repli, self.image_sig, self.groupe)


def cle_chanson(res):
    """Ce qui fait « la même chanson » : la vidéo source (travail/source.json) ; sans elle, le titre dans le même
    dossier des sorties (le nom que doubler donne au dossier, sans « (extrait …) »)."""
    v = (res.source or {}).get("video") if isinstance(res.source, dict) else None
    if v:
        return "source:" + os.path.normpath(str(v))
    return "titre:" + str(Path(res.dossier).parent / normaliser(res.titre_court))


def langue_paroles(res, lire_fichier=True):
    """Le code de la langue chantée (« es »), d'après rapport.json (paroles_sorties, pistes_audio), sinon d'après
    travail/<voix>/paroles.json ; '' si on ne la connaît pas."""
    r = res.rapport if isinstance(res.rapport, dict) else {}
    ps = r.get("paroles_sorties") if isinstance(r.get("paroles_sorties"), dict) else {}
    lc = ps.get("langue_chantee") if isinstance(ps.get("langue_chantee"), dict) else {}
    pa = r.get("pistes_audio") if isinstance(r.get("pistes_audio"), dict) else {}
    la = pa.get("langue") if isinstance(pa.get("langue"), dict) else {}
    for code in (lc.get("langue"), ps.get("langue"), la.get("paroles")):
        if isinstance(code, str) and code.strip():
            return code.strip().lower()
    if lire_fichier and res.dossier_voix is not None:
        f = res.dossier_voix / "paroles.json"
        try:
            if f.stat().st_size <= 4 * 1024 * 1024:
                d = json.loads(f.read_text(encoding="utf-8"))
                code = d.get("langue") if isinstance(d, dict) else None
                if isinstance(code, str) and code.strip():
                    return code.strip().lower()
        except (OSError, ValueError):
            pass
    return ""


def _signature_fichier(f):
    try:
        st = Path(f).stat()
    except OSError:
        return None
    return str(f), st.st_size, st.st_mtime_ns


def version(res, noms_voix=None):
    """La version d'un résultat (lit le disque : dates des fichiers, au besoin paroles.json) ; à appeler hors du fil de
    l'interface."""
    noms_voix = noms_voix or {}
    titre = res.titre_court
    voix = res.voix_nom or (res.voix and " et ".join(noms_voix.get(m) or m.capitalize()
                                                   for m in moteur.membres_voix(res.voix))) or ""
    image = res.video or res.mp3
    sig = _signature_fichier(image) if image is not None else None
    groupe = res.groupe or ""
    v = Version(res=res, cle=res.cle, titre=titre, voix=voix, voix_ids=tuple(moteur.membres_voix(res.voix)),
                mode=res.mode or "", duree_s=res.duree_s, date=res.date, autotune=bool(res.autotune),
                duo=bool(res.plusieurs_voix), extrait=res.extrait, langue=langue_paroles(res), complet=res.complet,
                repli=bool(res.repli), chanson=cle_chanson(res), image=image if sig else None, image_sig=sig,
                groupe=groupe)
    v.tri_titre = normaliser(titre)
    v.tri_voix = normaliser(voix)
    v.recherche = " ".join(normaliser(x) for x in (titre, voix, " ".join(v.voix_ids), groupe, v.mode,
                                                    nom_langue(v.langue)) if x)
    return v


def badges(v):
    """Les badges d'une version, dans l'ordre d'affichage."""
    sortie = []
    if not v.complet:
        sortie.append(Badge("inachevé", "alerte", "Doublage inachevé : pas encore de rapport, de vidéo ou de MP3",
                            "inacheve"))
    if v.autotune:
        sortie.append(Badge("auto-tune", "accent", "Une version auto-tune existe (touche T dans le détail)",
                            "autotune"))
    if v.duo:
        sortie.append(Badge("duo", "neutre", f"Une voix par chanteur : {v.voix}", "duo"))
    if v.extrait:
        d, u = v.extrait
        texte = f"Extrait à partir de {formats.chrono(d)}" + (f", {formats.duree(u)}" if u else "")
        sortie.append(Badge("extrait", "neutre", texte, "extrait"))
    if v.langue:
        sortie.append(Badge(v.langue.upper(), "neutre", f"Paroles en {nom_langue(v.langue)}", "langue"))
    if v.mode == "parole":
        sortie.append(Badge("parole", "neutre", "Mode parole (vidéo parlée)", "parole"))
    if v.repli:
        sortie.append(Badge("repli", "alerte", "YingMusic-SVC a échoué : la voix vient de Seed-VC", "repli"))
    return sortie


# --- Entrées (cartes de la grille, lignes de la liste) ----------------------------------------------------------------

@dataclass
class Entree:
    cle: str
    versions: list = field(default_factory=list)     # la plus récente d'abord
    _cles: dict | None = field(default=None, repr=False, compare=False)

    @property
    def principale(self):
        return self.versions[0]

    @property
    def n(self):
        return len(self.versions)

    @property
    def titre(self):
        return self.principale.titre

    @property
    def date(self):
        return max(v.date for v in self.versions)

    @property
    def duree_s(self):
        return self.principale.duree_s

    @property
    def voix(self):
        """« Jules », ou « Jules, Marie » pour plusieurs versions (sans doublon)."""
        return ", ".join(dict.fromkeys(v.voix for v in self.versions if v.voix))

    @property
    def mode(self):
        return " et ".join(dict.fromkeys(v.mode for v in self.versions if v.mode))

    def signature(self):
        return tuple(v.signature() for v in self.versions)

    def badges(self):
        """Ceux de la version seule ; pour plusieurs versions, ceux d'au moins une version (« inachevé » : toutes)."""
        if self.n == 1:
            return badges(self.principale)
        vus, sortie = set(), []
        for v in self.versions:
            for b in badges(v):
                if b.cle == "inacheve" or (b.cle, b.texte) in vus:
                    continue
                vus.add((b.cle, b.texte))
                if b.cle == "duo":
                    b = Badge("duo", "neutre", "Au moins une version a une voix par chanteur", "duo")
                elif b.cle == "extrait":
                    b = Badge("extrait", "neutre", "Au moins une version est un extrait", "extrait")
                sortie.append(b)
        if all(not v.complet for v in self.versions):
            sortie.insert(0, badges(self.principale)[0])
        return sortie

    def cles_tri(self):
        """Clés de tri par critère, croissantes (le sens est appliqué par la page) ; la clé de l'entrée départage.
        Calculées une fois : un tri les compare des centaines de fois."""
        if self._cles is not None:
            return self._cles
        p = self.principale
        d = self.date
        duree = self.duree_s
        self._cles = {"date": (d, p.tri_titre, self.cle),
                      "titre": (p.tri_titre, -d, self.cle),
                      "voix": (normaliser(self.voix), p.tri_titre, self.cle),
                      "duree": (duree is None, duree or 0.0, p.tri_titre, self.cle),
                      "mode": (self.mode or "~", p.tri_titre, self.cle)}
        return self._cles


def regrouper(versions, actif=True):
    """Les entrées : une par chanson (versions de la même vidéo source), ou une par version si actif est faux. L'ordre
    est celui des versions données (la page trie ensuite)."""
    if not actif:
        return [Entree(cle="version:" + v.cle, versions=[v]) for v in versions]
    groupes = {}
    for v in versions:
        groupes.setdefault(v.chanson, []).append(v)
    sortie = []
    for chanson, vs in groupes.items():
        vs = sorted(vs, key=lambda v: (-v.date, v.tri_voix, v.cle))
        cle = ("chanson:" + chanson) if len(vs) > 1 else ("version:" + vs[0].cle)
        sortie.append(Entree(cle=cle, versions=vs))
    return sortie


def libelles_versions(entree):
    """[(version, libellé)] des pastilles d'une entrée : la voix, précisée quand deux versions ont la même (extrait ou
    entier, puis date)."""
    noms = [v.voix or "?" for v in entree.versions]
    sortie = []
    for v, nom in zip(entree.versions, noms):
        libelle = nom
        if noms.count(nom) > 1:
            libelle += f" · extrait {formats.chrono(v.extrait[0])}" if v.extrait else " · entier"
        sortie.append([v, libelle])
    vus = [l for _v, l in sortie]
    for paire in sortie:
        if vus.count(paire[1]) > 1:
            paire[1] += f" · {formats.date(paire[0].date)}"
    return [tuple(p) for p in sortie]


def ordre(a, b, tri):
    """-1, 0 ou 1 : a avant b dans l'ordre croissant de ce critère (le trieur des colonnes de la page applique le
    sens)."""
    ka, kb = a.cles_tri()[tri], b.cles_tri()[tri]
    return (ka > kb) - (ka < kb)


def comparer(a, b, tri, inverse=False):
    """-1, 0 ou 1 : a avant b, pour ce critère, dans l'ordre affiché (decroissant())."""
    r = ordre(a, b, tri)
    return -r if decroissant(tri, inverse) else r


MOIS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


def date_courte(ts, maintenant=None):
    """« aujourd'hui », « hier », « il y a 3 jours », « 17 oct. », « 17 oct. 2025 » : la date d'une carte."""
    if not ts:
        return ""
    maintenant = maintenant or time.time()
    lt, ln = time.localtime(ts), time.localtime(maintenant)
    jours = (time.mktime((ln.tm_year, ln.tm_mon, ln.tm_mday, 0, 0, 0, 0, 0, -1)) -
             time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))) / 86400
    jours = round(jours)
    if jours <= 0:
        return "aujourd’hui"
    if jours == 1:
        return "hier"
    if jours < 7:
        return f"il y a {jours} jours"
    texte = f"{lt.tm_mday} {MOIS[lt.tm_mon - 1]}"
    return texte if lt.tm_year == ln.tm_year else f"{texte} {lt.tm_year}"


# --- Filtres et recherche ----------------------------------------------------------------------------------------------

GENRES = {"": "Chant et parole", "chant": "Chant", "parole": "Parole"}
LONGUEURS = {"": "Entiers et extraits", "entier": "Entiers", "extrait": "Extraits"}


@dataclass
class Filtre:
    texte: str = ""
    voix: str = ""          # identifiant d'une voix réelle (« marie ») ; '' : toutes
    genre: str = ""         # '', chant ou parole
    longueur: str = ""      # '', entier ou extrait
    autotune: bool = False  # seulement les versions avec auto-tune
    duo: bool = False       # seulement une voix par chanteur

    def mots(self):
        return normaliser(self.texte).split()

    @property
    def actif(self):
        """Un filtre autre que la recherche."""
        return bool(self.voix or self.genre or self.longueur or self.autotune or self.duo)

    @property
    def quelque_chose(self):
        return self.actif or bool(self.mots())

    def garde_version(self, v, mots=None):
        mots = self.mots() if mots is None else mots
        if any(m not in v.recherche for m in mots):
            return False
        if self.voix and self.voix not in v.voix_ids:
            return False
        if self.genre and v.mode != self.genre:
            return False
        if self.longueur == "extrait" and not v.extrait:
            return False
        if self.longueur == "entier" and v.extrait:
            return False
        if self.autotune and not v.autotune:
            return False
        if self.duo and not v.duo:
            return False
        return True

    def garde(self, entree):
        mots = self.mots()
        return any(self.garde_version(v, mots) for v in entree.versions)

    def premiere(self, entree):
        """La version à ouvrir : la première (la plus récente) qui passe le filtre, sinon la principale."""
        mots = self.mots()
        return next((v for v in entree.versions if self.garde_version(v, mots)), entree.principale)

    def etiquettes(self, noms_voix=None):
        """[(clé, libellé)] des filtres actifs (les puces sous l'en-tête, chacune efface le sien)."""
        noms_voix = noms_voix or {}
        sortie = []
        if self.voix:
            sortie.append(("voix", f"Voix {noms_voix.get(self.voix) or self.voix.capitalize()}"))
        if self.genre:
            sortie.append(("genre", GENRES[self.genre]))
        if self.longueur:
            sortie.append(("longueur", LONGUEURS[self.longueur]))
        if self.autotune:
            sortie.append(("autotune", "Avec auto-tune"))
        if self.duo:
            sortie.append(("duo", "Une voix par chanteur"))
        return sortie

    def effacer(self, cle=None):
        """Efface un filtre (clé d'etiquettes()), ou tous (pas la recherche) ; vrai si quelque chose a changé."""
        avant = (self.voix, self.genre, self.longueur, self.autotune, self.duo)
        for c in ([cle] if cle else ["voix", "genre", "longueur", "autotune", "duo"]):
            setattr(self, c, False if c in ("autotune", "duo") else "")
        return avant != (self.voix, self.genre, self.longueur, self.autotune, self.duo)


def voix_presentes(versions, noms_voix=None):
    """[(identifiant, nom)] des voix réelles des doublages, par nom (le menu du filtre « Voix »)."""
    noms_voix = noms_voix or {}
    vus = {}
    for v in versions:
        for i in v.voix_ids:
            if i not in vus:
                nom = noms_voix.get(i)
                if not nom and len(v.voix_ids) == 1 and v.voix:
                    nom = v.voix
                vus[i] = nom or i.capitalize()
    return sorted(vus.items(), key=lambda x: (normaliser(x[1]), x[0]))


def compte(visibles, entrees, n_versions):
    """Le sous-titre de l'en-tête : « 18 doublages · 12 chansons », ou « 5 sur 18 doublages » quand on filtre.
    visibles : les entrées affichées ; entrees : toutes ; n_versions : le nombre de doublages."""
    if not n_versions:
        return "Aucun doublage"
    vus = sum(e.n for e in visibles)
    if len(visibles) < len(entrees):
        return f"{vus} sur {formats.pluriel(n_versions, 'doublage')}"
    texte = formats.pluriel(n_versions, "doublage")
    if len(entrees) < n_versions:
        texte += f" · {formats.pluriel(len(entrees), 'chanson')}"
    return texte


def resume_version(v, maintenant=None):
    """« Jules · hier » : la ligne sous le titre d'une carte."""
    return " · ".join(x for x in (v.voix, date_courte(v.date, maintenant)) if x)


def description_accessible(entree):
    """Ce qu'un lecteur d'écran dit d'une carte ou d'une ligne."""
    p = entree.principale
    morceaux = [p.titre]
    if entree.n > 1:
        morceaux.append(f"{entree.n} versions : voix {entree.voix}")
    elif p.voix:
        morceaux.append(f"voix {p.voix}")
    if p.duree_s:
        morceaux.append(formats.duree(p.duree_s))
    morceaux += [b.texte for b in entree.badges()]
    morceaux.append(formats.date(entree.date))
    return ", ".join(morceaux)


# --- Pochettes générées --------------------------------------------------------------------------------------------

# Dégradés de la palette GNOME, assez sombres pour une note blanche, en clair comme en sombre
DEGRADES = (("#1c71d8", "#813d9c"), ("#26a269", "#1a5fb4"), ("#e66100", "#c01c28"), ("#9141ac", "#e01b24"),
            ("#2190a4", "#26a269"), ("#986a44", "#63452c"), ("#c01c28", "#813d9c"), ("#1a5fb4", "#2190a4"),
            ("#c64600", "#9141ac"), ("#3d3846", "#1c71d8"), ("#a51d2d", "#e66100"), ("#613583", "#1c71d8"))


def _rgb(hexa):
    h = hexa.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def couleurs_pochette(titre):
    """((r, g, b), (r, g, b), angle) : le dégradé de la pochette générée d'un titre, toujours le même pour un titre
    (sha1, et non hash(), qui change d'un lancement à l'autre)."""
    h = hashlib.sha1(normaliser(titre).encode("utf-8")).digest()
    a, b = DEGRADES[h[0] % len(DEGRADES)]
    return _rgb(a), _rgb(b), (45, 90, 135, 160)[h[1] % 4]
