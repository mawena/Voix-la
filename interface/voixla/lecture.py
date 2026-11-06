"""La lecture dans toute l'application (sans GTK, testable) : un seul lecteur actif à la fois, ce qu'on montre de la
lecture en cours (barre « en cours de lecture », MPRIS), le retour sur la vidéo après un passage sur le son seul, et
les règles de vie de l'application quand on ferme sa fenêtre.

Un « propriétaire » est ce qui lit : le détail d'un résultat, l'écoute à l'aveugle, une préécoute (voix, prise de
chant, extrait à doubler). Il a :
- couper() : mettre sa lecture en pause, parce qu'une autre commence ;
- durable : vrai pour ce qui continue sans sa page, et même sans la fenêtre (un résultat, l'écoute à l'aveugle) ; faux
  pour une préécoute, qui s'arrête avec sa page ;
- quitter_session() (facultatif) : appelé quand une autre lecture durable le remplace (il peut alors fermer ses flux).

La « session » est la dernière lecture durable : c'est elle que montrent la barre et MPRIS, qu'on peut reprendre, et
qui garde l'application en vie fenêtre fermée. Une préécoute la coupe (pause) sans la remplacer.
"""

from dataclasses import dataclass

from . import formats

LECTURE, PAUSE, ARRET = "Playing", "Paused", "Stopped"     # les états de lecture de MPRIS
DELAI_PAUSE_S = 15 * 60      # fenêtre fermée et lecture en pause depuis si longtemps : l'application quitte
TOLERANCE_SAUT_S = 1.5       # écart à la position attendue au-delà duquel on signale un saut (MPRIS « Seeked »)


def _appeler(objet, methode):
    f = getattr(objet, methode, None)
    if callable(f):
        f()


def durable(proprietaire):
    return bool(getattr(proprietaire, "durable", False))


class Coordinateur:
    """Un seul propriétaire lit à la fois. commence(p) quand une lecture de p démarre : celui qui lisait est coupé ;
    si p est durable, il devient la session (l'ancienne session est coupée puis quitte). termine(p) quand p ne lit plus
    du tout (arrêt, page fermée)."""

    def __init__(self):
        self.actif = None          # le dernier propriétaire qui a lancé une lecture
        self.session = None        # la dernière lecture durable
        self.observateurs = []     # rappels sans argument, après chaque changement

    def commence(self, proprietaire):
        """Renvoie le propriétaire coupé, ou None. Une bascule à l'intérieur d'un même propriétaire (autre piste du
        même résultat) ne coupe rien."""
        if proprietaire is None:
            return None
        coupe = None
        change = False
        if proprietaire is not self.actif:
            coupe, self.actif = self.actif, proprietaire
            change = True
            if coupe is not None:
                _appeler(coupe, "couper")
        if durable(proprietaire) and proprietaire is not self.session:
            ancienne, self.session = self.session, proprietaire
            change = True
            if ancienne is not None:
                if ancienne is not coupe:
                    _appeler(ancienne, "couper")
                _appeler(ancienne, "quitter_session")
        if change:
            self._prevenir()
        return coupe

    def termine(self, proprietaire):
        """proprietaire ne lit plus : il n'est plus ni actif ni session. Vrai si quelque chose a changé."""
        change = False
        if proprietaire is not None and self.actif is proprietaire:
            self.actif = None
            change = True
        if proprietaire is not None and self.session is proprietaire:
            self.session = None
            change = True
        if change:
            self._prevenir()
        return change

    def _prevenir(self):
        for r in list(self.observateurs):
            r()


# --- Ce qu'on montre de la lecture en cours ------------------------------------------------------------------------

@dataclass
class Etat:
    """La lecture en cours, telle que la montrent la barre et MPRIS. cle : ce qui identifie le morceau (un résultat
    et sa voix, un extrait de l'écoute à l'aveugle) ; il ne change pas quand on change de piste."""
    statut: str = ARRET
    position_s: float = 0.0
    duree_s: float | None = None
    titre: str = ""
    voix: str = ""                 # nom de la voix (résultat)
    piste: str = ""                # ce qu'on écoute : « Doublage », « Beat seul », « B — mix »…
    genre: str = ""                # « resultat », « ecoute » ; vide : pas de session
    cle: tuple = ()
    video: str | None = None       # la vidéo d'où tirer une image (miniature), s'il y en a une
    image: str | None = None       # cette image, une fois en cache : pour MPRIS et la barre
    volume: float = 1.0
    retour: bool = True            # on peut revenir à la page qui lit
    vitesse: float = 1.0           # vitesse de lecture (la hauteur est gardée : scaletempo)
    vitesse_reglable: bool = False     # la session sait changer de vitesse (MPRIS : MinimumRate, MaximumRate)
    boucle: tuple = ()             # boucle A-B en cours : (a, b) en secondes, () sans boucle complète

    @property
    def session(self):
        return bool(self.genre)


def ligne_detail(etat):
    """La ligne sous le titre : « voix Jules · Beat seul », « Écoute à l’aveugle · B — mix »."""
    morceaux = []
    if etat.genre == "ecoute":
        morceaux.append("Écoute à l’aveugle")
    elif etat.voix:
        morceaux.append(f"voix {etat.voix}")
    if etat.piste:
        morceaux.append(etat.piste)
    if abs((etat.vitesse or 1.0) - 1.0) > 1e-3:
        morceaux.append(libelle_vitesse(etat.vitesse))
    if boucle_complete(etat.boucle):
        morceaux.append("boucle A-B")
    return " · ".join(morceaux)


def ligne_position(etat):
    """« 1:23 / 3:45 », « 1:23 » sans durée connue, « fini » à la fin."""
    if etat.statut == ARRET and etat.duree_s and etat.position_s >= etat.duree_s - 0.5:
        return "fini"
    pos = formats.chrono(max(0.0, etat.position_s))
    return f"{pos} / {formats.chrono(etat.duree_s)}" if etat.duree_s else pos


def fraction(etat):
    """Avancement de 0 à 1 (barre de progression), 0 sans durée connue."""
    if not etat.duree_s or etat.duree_s <= 0:
        return 0.0
    return max(0.0, min(1.0, etat.position_s / etat.duree_s))


def saut(avant_s, apres_s, ecoule_s, en_lecture, tolerance_s=TOLERANCE_SAUT_S):
    """Vrai si la position a sauté (déplacement à la souris, clic sur la frise…) plutôt qu'avancé normalement :
    écart de plus de tolerance_s à la position attendue (avant + temps écoulé en lecture, avant en pause)."""
    if avant_s is None or apres_s is None:
        return False
    attendu = avant_s + (ecoule_s if en_lecture else 0.0)
    return abs(apres_s - attendu) > tolerance_s


# --- Lecteur complet : vitesse, boucle A-B, sauts, reprise à la dernière position -----------------------------------

VITESSES = (0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0)     # menu et touches [ ] du lecteur d'un résultat
SAUT_S = 5.0                 # flèches gauche et droite
BOUCLE_MIN_S = 0.2           # une boucle A-B plus courte que cela n'est pas posée
REPRISE_MIN_S = 5.0          # en deçà, on ne retient pas la position : on reprendra au début
REPRISE_FIN_S = 10.0         # à moins de 10 s de la fin, le doublage est fini : on reprendra au début
POSITIONS_MAX = 200          # positions retenues au plus (les plus anciennes oubliées d'abord)
# touches 1 à 5 du détail d'un résultat : les bascules de pistes, dans leur ordre (voixla.pistes.CLES)
TOUCHES_PISTES = ("doublage", "original", "beat", "ma_voix", "voix_origine")


def borner_vitesse(v):
    """Une vitesse entre la plus lente et la plus rapide de VITESSES ; 1 si v n'est pas un nombre."""
    if isinstance(v, bool):
        return 1.0
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 1.0
    if v != v:                # NaN
        return 1.0
    return max(VITESSES[0], min(VITESSES[-1], v))


def vitesse_voisine(v, sens):
    """La vitesse de VITESSES juste au-dessus (sens > 0) ou juste en dessous (sens < 0) de v, bornée."""
    v = borner_vitesse(v)
    if sens > 0:
        return next((x for x in VITESSES if x > v + 1e-6), VITESSES[-1])
    return next((x for x in reversed(VITESSES) if x < v - 1e-6), VITESSES[0])


def libelle_vitesse(v):
    """« 1× », « 0,75× », « 1,5× »."""
    texte = f"{round(borner_vitesse(v), 2):.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{texte}×"


def boucle_complete(boucle):
    return bool(boucle) and len(boucle) == 2 and boucle[0] is not None and boucle[1] is not None


def boucle_suivante(boucle, position_s):
    """Touche L (et bouton A-B) : sans boucle, pose A à la position ; avec A seul, pose B (A et B remis dans l'ordre ;
    trop près de A, rien ne change) ; avec une boucle complète, l'efface. Rend (a, None), (a, b) ou ()."""
    p = max(0.0, float(position_s or 0.0))
    if not boucle:
        return (p, None)
    if not boucle_complete(boucle):
        a = boucle[0]
        if abs(p - a) < BOUCLE_MIN_S:
            return tuple(boucle)
        return (min(a, p), max(a, p))
    return ()


def retour_boucle(boucle, position_s):
    """La position où revenir (A) si la lecture a atteint B ; sinon None."""
    if not boucle_complete(boucle) or position_s is None:
        return None
    a, b = boucle
    return a if position_s >= b else None


def libelle_boucle(boucle):
    """« A 1:02 » (A posé), « A-B 1:02 → 1:10 », ou ""."""
    if not boucle:
        return ""
    if not boucle_complete(boucle):
        return f"A {formats.chrono(boucle[0])}"
    return f"A-B {formats.chrono(boucle[0])} → {formats.chrono(boucle[1])}"


def temps_restant(position_s, duree_s):
    """« −1:23 » : ce qui reste à lire ; "" sans durée connue."""
    if not duree_s or duree_s <= 0:
        return ""
    return "−" + formats.chrono(max(0.0, duree_s - max(0.0, position_s or 0.0)))


def cle_reprise(dossier, voix):
    """La clé d'un doublage dans les positions retenues (préférence « positions »)."""
    return f"{dossier}|{voix or ''}"


def position_a_garder(position_s, duree_s=None):
    """La position à retenir pour la reprise, ou None : au tout début, ou à la fin (le doublage est fini), on
    reprendra au début."""
    if isinstance(position_s, bool):
        return None
    try:
        p = float(position_s)
    except (TypeError, ValueError):
        return None
    if p != p or p < REPRISE_MIN_S or p == float("inf"):
        return None
    if duree_s and p > float(duree_s) - REPRISE_FIN_S:
        return None
    return round(p, 2)


def memoriser_position(positions, cle, position_s, duree_s=None, maintenant=0.0, maximum=POSITIONS_MAX):
    """Les positions retenues, mises à jour pour cle (une copie) : la position à garder (position_a_garder), ou cle
    oubliée ; au-delà de maximum, les plus anciennes sont oubliées. Les entrées abîmées sont écartées."""
    sortie = {k: v for k, v in (positions if isinstance(positions, dict) else {}).items()
              if isinstance(k, str) and isinstance(v, dict) and position_a_garder(v.get("position")) is not None}
    p = position_a_garder(position_s, duree_s)
    if p is None:
        sortie.pop(cle, None)
    else:
        sortie[cle] = {"position": p, "date": float(maintenant or 0.0)}
    if len(sortie) > maximum:
        def date(k):
            d = sortie[k].get("date")
            return float(d) if isinstance(d, (int, float)) and not isinstance(d, bool) else 0.0
        garder = sorted(sortie, key=date, reverse=True)[:maximum]
        sortie = {k: sortie[k] for k in garder}
    return sortie


def position_reprise(positions, cle, duree_s=None):
    """La position où reprendre ce doublage, ou None (jamais lu, lu jusqu'au bout, entrée abîmée)."""
    v = positions.get(cle) if isinstance(positions, dict) else None
    if not isinstance(v, dict):
        return None
    return position_a_garder(v.get("position"), duree_s)


# --- Fenêtre fermée : passer sur le son seul, puis revenir à la vidéo------------------------------------------------

@dataclass
class Reprise:
    """Ce qu'il faut pour revenir à la vidéo après un passage sur le son seul (fenêtre fermée) : la piste et sa source
    vidéo (chemin, décalage, libellé), avec ses drapeaux. La position et la lecture ou la pause sont celles du moment du
    retour : on revient là où le son en est."""
    cle: str
    source: tuple
    sans_image: bool = False
    ephemere: bool = False

    def mise_a_jour(self, source, sans_image=False, ephemere=False):
        """La source vidéo a changé pendant qu'on écoutait le son seul (copie vidéo prête entre-temps)."""
        return Reprise(self.cle, source, sans_image, ephemere)


def source_son(cle, pistes_son, source_actuelle=None):
    """La source sans image de même contenu pour la piste cle, prise dans les pistes de la forme « mp3 »
    (voixla.pistes.choisir_pistes(res, "mp3")) : le MP3 du doublage, le son d'origine extrait, le fichier du beat ou
    d'une voix seule ; (chemin, décalage, libellé), ou None s'il n'y en a pas ou si c'est déjà elle qu'on lit."""
    p = (pistes_son or {}).get(cle)
    if p is None or getattr(p, "fichier", None) is None:
        return None
    if source_actuelle is not None and str(source_actuelle[0]) == str(p.fichier):
        return None
    return (p.fichier, float(getattr(p, "decalage", 0.0) or 0.0), getattr(p, "libelle", cle))


# --- Règles de vie de l'application --------------------------------------------------------------------------------

def a_la_fermeture(statut):
    """Fermer la fenêtre : « cacher » pendant une lecture (l'application reste, sans fenêtre, et la musique
    continue) ; sinon « quitter », comme avant."""
    return "cacher" if statut == LECTURE else "quitter"


def sans_fenetre(statut, pause_depuis_s=None, delai_s=DELAI_PAUSE_S):
    """Fenêtre fermée, après un changement de la lecture : « rester » (ça joue), « attendre » (en pause depuis moins de
    delai_s : on revérifiera), « quitter » (fini, arrêté, plus de session, ou pause trop longue)."""
    if statut == LECTURE:
        return "rester"
    if statut == PAUSE and (pause_depuis_s or 0.0) < delai_s:
        return "attendre"
    return "quitter"


@dataclass
class Vie:
    """L'état qui décide si l'application reste en vie : fenêtre visible ou cachée, et depuis quand la lecture est en
    pause fenêtre cachée. decider(statut, maintenant) → « rester », « attendre » ou « quitter »."""
    fenetre_visible: bool = True
    pause_depuis: float | None = None
    delai_s: float = DELAI_PAUSE_S

    def fermer(self, statut):
        """La fenêtre se ferme : « cacher » ou « quitter »."""
        d = a_la_fermeture(statut)
        if d == "cacher":
            self.fenetre_visible = False
        return d

    def ouvrir(self):
        self.fenetre_visible = True
        self.pause_depuis = None

    def decider(self, statut, maintenant):
        if self.fenetre_visible:
            self.pause_depuis = None
            return "rester"
        if statut == PAUSE:
            if self.pause_depuis is None:
                self.pause_depuis = maintenant
        else:
            self.pause_depuis = None
        d = sans_fenetre(statut, (maintenant - self.pause_depuis) if self.pause_depuis is not None else None,
                         self.delai_s)
        return d

    def reste_pause(self, maintenant):
        """Secondes avant que la pause, fenêtre cachée, ne fasse quitter (None hors de ce cas)."""
        if self.fenetre_visible or self.pause_depuis is None:
            return None
        return max(0.0, self.delai_s - (maintenant - self.pause_depuis))
