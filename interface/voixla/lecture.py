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


# --- Fenêtre fermée : passer sur le son seul, puis revenir à la vidéo ------------------------------------------------

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
