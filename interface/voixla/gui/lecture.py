"""Une seule lecture pour toute l'application (les règles sont dans voixla/lecture.py, sans GTK).

Chaque flux ouvert par lecteur.media() est suivi ici avec son propriétaire : dès qu'il joue, celui qui lisait est
coupé. La session (la dernière lecture durable : un résultat, l'écoute à l'aveugle) est montrée par la barre « en
cours de lecture » de la fenêtre et publiée sur MPRIS (voixla/mpris.py) ; l'application décide d'après elle si elle
reste en vie quand on ferme la fenêtre.

Un propriétaire durable a, en plus de couper(), durable et quitter_session() :
  etat_lecture()            → voixla.lecture.Etat
  lire(), mettre_en_pause(), lecture_pause(), arreter(), aller_a(position_s)
  revenir()                 montrer sa page
  est_visible()             sa page est celle qu'on voit
  passer_en_son(), revenir_en_video()   fenêtre fermée puis rouverte (facultatifs)
  regler_volume(v)          (facultatif)
  regler_vitesse(v)         (facultatif : etat_lecture().vitesse_reglable le dit)
Les propriétaires ne sont tenus ici que par des références faibles, sauf l'actif et la session (le coordinateur).
"""

import time
import traceback
import weakref

from gi.repository import GLib

from .. import lecture as regles
from .. import mpris

DELAI_MS = 120     # les changements d'un flux (pause de l'ancien, lecture du nouveau) sont regroupés


class Apercu:
    """Propriétaire d'une préécoute (référence d'une voix, prise de chant) : couper() l'arrête. Il s'arrête aussi avec sa
    page ; il coupe la musique quand il commence, sans prendre sa place dans la barre ni dans MPRIS."""
    durable = False

    def __init__(self, couper):
        self._couper = couper

    def couper(self):
        self._couper()


class Lectures:
    def __init__(self):
        self.coord = regles.Coordinateur()
        self.dernier = regles.Etat()  # le dernier état calculé (la barre)
        self.ecouteurs = []          # rappel(etat) après chaque changement (la barre)
        self.app = None              # Application : MPRIS (sa connexion D-Bus), règles de vie, Raise et Quit
        self.serveur = None
        self._prevu = None
        self._minuteur = None
        self._mesure = None          # (position_s, instant, en_lecture, cle) : pour repérer les sauts
        self._miniatures = {}        # vidéo → miniature (chemin), ou "" pendant le calcul ou s'il est impossible

    # --- flux ------------------------------------------------------------------------------------------------------

    def suivre(self, m, proprietaire):
        """Suit un flux (Gtk.MediaStream) de ce propriétaire."""
        ref = weakref.ref(proprietaire)
        m.connect("notify::playing", lambda flux, _p: self._joue(flux, ref))
        m.connect("notify::ended", lambda *_: self.prevoir())
        return m

    def _joue(self, m, ref):
        p = ref()
        if p is not None and m.get_playing():
            self.coord.commence(p)
        self.prevoir()

    def terminer(self, proprietaire):
        """proprietaire ne lit plus du tout (arrêt, page fermée) : il quitte la barre et MPRIS."""
        if self.coord.termine(proprietaire):
            self.prevoir()

    @property
    def session(self):
        return self.coord.session

    def session_visible(self):
        s = self.session
        try:
            return bool(s is not None and s.est_visible())
        except Exception:  # noqa: BLE001
            return False

    # --- état ------------------------------------------------------------------------------------------------------

    def etat_courant(self):
        s = self.session
        if s is None:
            return regles.Etat()
        try:
            e = s.etat_lecture()
        except Exception:  # noqa: BLE001 — une page à moitié fermée ne doit rien casser
            traceback.print_exc()
            return regles.Etat()
        if e.video and not e.image:
            e.image = self._miniature(e.video)
        return e

    def _miniature(self, video):
        video = str(video)
        if video in self._miniatures:
            return self._miniatures[video] or None
        self._miniatures[video] = ""
        from .analyses import analyses

        def pret(f, message):
            if message == "attente":
                return
            self._miniatures[video] = str(f) if f else ""
            if f:
                self.prevoir()
        analyses().miniature(video, pret)
        return None

    def prevoir(self):
        if self._prevu is None:
            self._prevu = GLib.timeout_add(DELAI_MS, self._a_jour)

    def _a_jour(self):
        self._prevu = None
        self.actualiser()
        return False

    def actualiser(self):
        etat = self.etat_courant()
        self.dernier = etat
        self._reperer_saut(etat)
        for r in list(self.ecouteurs):
            try:
                r(etat)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        self._publier(etat)
        if etat.statut == regles.LECTURE and self._minuteur is None:
            self._minuteur = GLib.timeout_add_seconds(1, self._tic)
        if self.app is not None:
            self.app.lecture_changee(etat)
        return etat

    def _tic(self):
        """Une fois par seconde pendant la lecture : position de la barre, sauts pour MPRIS."""
        if self.actualiser().statut == regles.LECTURE:
            return True
        self._minuteur = None
        return False

    def _reperer_saut(self, etat):
        maintenant = time.monotonic()
        m = self._mesure
        if m and etat.session and m[3] == etat.cle and regles.saut(m[0], etat.position_s, maintenant - m[1], m[2]):
            if self.serveur is not None and self.serveur.publie:
                self.serveur.signaler_saut(etat.position_s)
        self._mesure = (etat.position_s, maintenant, etat.statut == regles.LECTURE, etat.cle) if etat.session \
            else None

    # --- MPRIS -----------------------------------------------------------------------------------------------------

    def _publier(self, etat):
        """Le lecteur MPRIS n'existe que pendant une session : GNOME le montre, puis l'oublie à l'arrêt."""
        if etat.session:
            if self.serveur is None:
                connexion = self.app.get_dbus_connection() if self.app is not None else None
                if connexion is None:
                    return
                self.serveur = mpris.ServeurMpris(connexion, self)
            if self.serveur.publie:
                self.serveur.actualiser()
            else:
                try:
                    self.serveur.publier()
                except GLib.Error:
                    traceback.print_exc()
        elif self.serveur is not None and self.serveur.publie:
            self.serveur.retirer()

    def fermer(self):
        if self.serveur is not None:
            self.serveur.retirer()

    # pilote de voixla.mpris.ServeurMpris (et de la barre)

    def etat(self):
        return self.etat_courant()

    def _session(self, methode, *args):
        s = self.session
        f = getattr(s, methode, None) if s is not None else None
        if callable(f):
            f(*args)
        self.prevoir()

    def lire(self):
        self._session("lire")

    def pause(self):
        self._session("mettre_en_pause")

    def lecture_pause(self):
        self._session("lecture_pause")

    def arreter(self):
        self._session("arreter")

    def placer(self, position_s):
        self._session("aller_a", max(0.0, position_s))

    def deplacer(self, delta_s):
        self.placer(self.etat_courant().position_s + delta_s)

    def regler_volume(self, v):
        self._session("regler_volume", v)

    def regler_vitesse(self, v):
        self._session("regler_vitesse", v)

    def revenir(self):
        """Montre la page de ce qui joue (barre, Raise)."""
        self._session("revenir")

    def relever(self):
        if self.app is not None:
            self.app.activate()

    def quitter(self):
        if self.app is not None:
            self.app.quitter_tout()

    # --- fenêtre ---------------------------------------------------------------------------------------------------

    def fenetre_cachee(self):
        """Fenêtre fermée pendant la lecture : pas de décodage vidéo pour rien, la session passe sur son son seul."""
        self._session("passer_en_son")

    def fenetre_montree(self):
        self._session("revenir_en_video")


_instance = None


def lectures():
    global _instance
    if _instance is None:
        _instance = Lectures()
    return _instance
