"""Lecteur vidéo et audio (Gtk.MediaFile + GStreamer), avec bascule entre sources à la même position.

L'image est un Gtk.Picture qui affiche le flux (un Gtk.MediaStream est un GdkPaintable) ; les commandes sont un
Gtk.MediaControls toujours visible, pour la vidéo comme pour le son. Une source sans image (le beat ou une voix seule,
à côté d'une vidéo, tant que sa copie vidéo se prépare) se lit sous l'image de la vidéo arrêtée, avec un bandeau qui
le dit : pas de second décodage, pas d'écran qui disparaît et fait sauter la page. Quand sa copie vidéo est prête, elle
la remplace à la même position (ajouter). Les sources « éphémères » (ces copies) sont fermées dès qu'on les quitte :
seuls le doublage et l'original restent ouverts, pour la touche B.

Quitter la page n'arrête pas la lecture : seul le suivi de la position (et les visualisations qu'il anime) s'arrête
tant que le lecteur est caché, et reprend au retour. Une seule lecture dans toute l'application : chaque flux est ouvert
par media(), qui le confie au coordinateur (gui/lecture.py) avec son propriétaire ; lancer une lecture ailleurs coupe
celle-ci, changer de source dans ce lecteur ne coupe rien. Fenêtre fermée, passer_en_son() remplace la vidéo par une
source sans image de même contenu, à la même position et sans blanc ; revenir_en_video() la remet.
"""

from gi.repository import GLib, Gtk

from .. import formats
from ..lecture import Reprise
from .lecture import lectures


def media(chemin, proprietaire):
    """Un flux (Gtk.MediaFile) suivi par le coordinateur : quand il joue, la lecture d'un autre propriétaire est
    coupée."""
    m = Gtk.MediaFile.new_for_filename(str(chemin))
    m.set_loop(False)
    return lectures().suivre(m, proprietaire)


def quand_pret(m, rappel):
    """rappel() dès que le flux est prêt (durée connue, déplacement possible)."""
    if m.is_prepared():
        rappel()
        return
    ident = []

    def pret(*_):
        if m.is_prepared():
            m.disconnect(ident[0])
            rappel()
    ident.append(m.connect("notify::prepared", pret))


class Lecteur(Gtk.Box):
    """Un lecteur ; sources = {clé: (chemin, décalage_s, libellé)} ; bascule entre sources à la même position
    (une source de décalage d est lue à position de référence + d). Dans la page d'un résultat, la position de
    référence est le temps du son doublé, quelle que soit la piste : celle d'une vidéo a pour décalage le début du son
    dans la vidéo doublée (voixla.pistes.source_lecteur).
    proprietaire : ce qui lit, pour le coordinateur (la page d'un résultat) ; à défaut, le lecteur lui-même, une
    préécoute (durable faux) qu'une autre lecture met en pause."""

    durable = False

    def __init__(self, hauteur=280, sur_position=None, proprietaire=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.ecran = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True,
                                 css_classes=["ecran-media"])
        self.ecran.set_size_request(-1, hauteur)
        # bandeau sur l'image arrêtée quand la source en cours n'a pas d'image
        self.bandeau = Gtk.Label(css_classes=["osd", "bandeau-ecran", "caption"], halign=Gtk.Align.START,
                                 valign=Gtk.Align.END, margin_start=10, margin_bottom=10, visible=False)
        self.superpose = Gtk.Overlay(child=self.ecran)
        self.superpose.add_overlay(self.bandeau)
        self.commandes = Gtk.MediaControls(hexpand=True)
        self.append(self.superpose)
        self.append(self.commandes)
        self.sources = {}
        self.flux = {}
        self.cle = None
        self.audio_seul = False
        self.sans_image = set()
        self.ephemeres = set()    # sources fermées dès qu'on les quitte (copies vidéo des pistes séparées)
        self.notes = {}           # clé → texte du bandeau d'une source sans image
        self.muet = False         # essais : tous les flux, même ceux ouverts plus tard, restent muets
        self.sur_position = sur_position
        self.proprietaire = proprietaire
        self._minuteur = None
        self._fin_extrait = None
        self._attente = None      # [flux, position voulue, placé, lire] tant qu'un flux activé n'est pas prêt et placé
        self._reprise = None      # fenêtre fermée : la source vidéo à remettre (voixla.lecture.Reprise)
        # pas de pause quand on quitte la page : la lecture continue ; le suivi s'arrête caché et reprend au retour
        self.connect("map", lambda *_: self.media() and self._suivre())

    def _proprio(self):
        return self.proprietaire if self.proprietaire is not None else self

    def couper(self):
        """Une autre lecture commence (coordinateur) : pause."""
        self.pause()

    # --- sources -------------------------------------------------------------------------------------------------

    def definir(self, sources, cle=None, audio_seul=False, sans_image=(), ephemeres=(), notes=None):
        """Remplace les sources ; garde la position si c'est le même contenu sous une autre forme. sans_image : les
        clés des sources sans image (lues sous l'image arrêtée d'une source qui en a une) ; ephemeres : celles qu'on
        ferme dès qu'on les quitte ; notes : le texte du bandeau des sources sans image."""
        self._tout_fermer()
        self.sources = {k: v for k, v in sources.items() if v and v[0]}
        self.audio_seul = audio_seul
        self.sans_image = set(sans_image)
        self.ephemeres = set(ephemeres)
        self.notes = dict(notes or {})
        self.superpose.set_visible(not audio_seul)
        cle = cle if cle in self.sources else next(iter(self.sources), None)
        if cle:
            self._activer(cle, 0.0, False)

    def ajouter(self, cle, source, sans_image=False, ephemere=False):
        """Ajoute ou remplace une source (une piste préparée après coup). Si c'est la source en cours (le beat lu sous
        l'image arrêtée, dont la copie vidéo vient d'être prête), on passe à la nouvelle à la même position, en lecture
        si on lisait. Fenêtre fermée (son seul), une nouvelle vidéo de la piste en cours attend le retour de la
        fenêtre."""
        if not source or not source[0]:
            return
        if self._reprise is not None and cle == self._reprise.cle:
            self._reprise = self._reprise.mise_a_jour(source, sans_image, ephemere)
            return
        self._remplacer(cle, source, sans_image, ephemere)

    def _remplacer(self, cle, source, sans_image=False, ephemere=False, flux=None):
        """ajouter, sans le cas du son seul ; flux : un flux déjà ouvert (et prêt) pour cette source."""
        courante = cle == self.cle and cle in self.flux
        pos = self.position_ref() if courante else 0.0
        lisait = bool(courante and self.en_lecture())
        ancien = self.flux.pop(cle, None)
        if ancien is not None:
            ancien.pause()
        if flux is not None:
            self.flux[cle] = flux
        self.sources[cle] = source
        (self.sans_image.add if sans_image else self.sans_image.discard)(cle)
        (self.ephemeres.add if ephemere else self.ephemeres.discard)(cle)
        if courante:
            self._activer(cle, pos, lisait)
        self._fermer(ancien)

    def annoter(self, cle, texte):
        """Texte du bandeau d'une source sans image (« préparation de la vidéo… »), à jour si elle est en cours."""
        self.notes[cle] = texte
        if cle == self.cle and cle in self.sans_image and not self.audio_seul:
            self.bandeau.set_label(self._texte_bandeau(cle))

    def _texte_bandeau(self, cle):
        return f"{self.sources[cle][2]} · {self.notes.get(cle) or 'son seul, image arrêtée'}"

    @staticmethod
    def _fermer(m):
        if m is None:
            return
        try:
            m.pause()
            m.clear()
        except Exception:  # noqa: BLE001
            pass

    def _ouvrir(self, chemin):
        m = media(chemin, self._proprio())
        if self.muet:
            m.set_muted(True)
        return m

    def _flux(self, cle):
        if cle not in self.flux:
            self.flux[cle] = self._ouvrir(self.sources[cle][0])
        return self.flux[cle]

    def rendre_muet(self):
        """Essais : coupe le son de tous les flux, présents et à venir."""
        self.muet = True
        for m in self.flux.values():
            m.set_muted(True)

    def _image_pour(self, cle, position_ref):
        """Ce que montre l'écran pendant une source sans image : l'image en cours, arrêtée ; à défaut, celle d'une
        source qui a une image, placée à la même position et laissée en pause."""
        courante = self.ecran.get_paintable()
        if courante is not None:
            self.ecran.set_paintable(courante.get_current_image())
            return
        autre = next((k for k in self.sources if k not in self.sans_image), None)
        if autre is None or self._reprise is not None:
            return                # fenêtre fermée : pas de vidéo ouverte pour une image que personne ne voit
        m = self._flux(autre)
        self.ecran.set_paintable(m)
        decalage = self.sources[autre][1] or 0.0
        quand_pret(m, lambda: m.seek(int(max(0.0, position_ref + decalage) * 1_000_000)))

    def _activer(self, cle, position_ref, lire):
        m = self._flux(cle)
        self.cle = cle
        if self.audio_seul:
            self.ecran.set_paintable(None)
            self.bandeau.set_visible(False)
        elif cle in self.sans_image:
            self._image_pour(cle, position_ref)
            self.bandeau.set_label(self._texte_bandeau(cle))
            self.bandeau.set_visible(True)
        else:
            courante = self.ecran.get_paintable()
            if m.is_prepared() or courante is None:
                self.ecran.set_paintable(m)
            else:
                # l'image en cours reste (figée) jusqu'à ce que la nouvelle vidéo soit prête : pas d'écran vide
                self.ecran.set_paintable(courante.get_current_image())
                quand_pret(m, lambda: self.flux.get(self.cle) is m and self.ecran.set_paintable(m))
            self.bandeau.set_visible(False)
        self.commandes.set_media_stream(m)
        decalage = self.sources[cle][1] or 0.0
        # tant que le flux n'est pas prêt et placé, sa position est celle qu'on lui a demandée : une nouvelle bascule
        # (la copie vidéo prête juste après) repart de là, pas de 0 ; et il « lit » déjà si on l'a demandé
        self._attente = [m, position_ref, False, lire]

        def placer():
            if position_ref or decalage:
                m.seek(int(max(0.0, position_ref + decalage) * 1_000_000))
            a = self._attente
            # une pause demandée pendant la préparation l'emporte
            if (a[3] if a is not None and a[0] is m else lire):
                m.play()
            if a is not None and a[0] is m:
                a[2] = True
        quand_pret(m, placer)
        self._suivre()

    def basculer(self, cle):
        """Passe à une autre source, à la même position (et en lecture si on lisait)."""
        if cle == self.cle or cle not in self.sources:
            return
        reprise, self._reprise = self._reprise, None
        ancien, ancienne_cle = self.media(), self.cle
        pos = self.position_ref()
        lisait = self.en_lecture()
        if ancien:
            ancien.pause()
        self._activer(cle, pos, lisait)
        if ancienne_cle in self.ephemeres or (reprise is not None and reprise.cle == ancienne_cle):
            self._fermer(self.flux.pop(ancienne_cle, None))
        if reprise is not None:
            self._remettre(reprise)

    def media(self):
        return self.flux.get(self.cle) if self.cle else None

    def en_lecture(self):
        """Vrai pendant la lecture, et pendant une bascule demandée en lecture dont le flux se prépare encore."""
        m = self.media()
        if not m:
            return False
        a = self._attente
        if a is not None and a[0] is m and not a[2]:
            return bool(a[3])
        return bool(m.get_playing())

    # --- fenêtre fermée : le son seul, puis retour à la vidéo ------------------------------------------------------

    def passer_en_son(self, son):
        """Remplace la source en cours par son (chemin, décalage, libellé) : même contenu sans image, à la même
        position, sans blanc (le son se prépare pendant que la vidéo joue encore). Le décodage vidéo s'arrête."""
        cle = self.cle
        if not cle or self._reprise is not None or cle not in self.sources:
            return False
        self._reprise = Reprise(cle, self.sources[cle], cle in self.sans_image, cle in self.ephemeres)
        self._substituer(cle, son, sans_image=True, ephemere=True)
        return True

    def revenir_en_video(self):
        """Remet la source d'avant passer_en_son, là où en est le son, en lecture ou en pause comme lui."""
        r, self._reprise = self._reprise, None
        if r is None:
            return False
        if self.cle != r.cle:
            self.sources[r.cle] = r.source
            return False
        self._substituer(r.cle, r.source, r.sans_image, r.ephemere)
        return True

    def _remettre(self, r):
        """Après une bascule pendant le son seul (le flux du son est fermé) : la source vidéo reprend sa place."""
        self.sources[r.cle] = r.source
        (self.sans_image.add if r.sans_image else self.sans_image.discard)(r.cle)
        (self.ephemeres.add if r.ephemere else self.ephemeres.discard)(r.cle)

    def _substituer(self, cle, source, sans_image, ephemere):
        """Comme _remplacer, mais le nouveau flux se prépare d'abord pendant que l'ancien joue encore."""
        actuel = self.flux.get(cle)
        if actuel is None or not actuel.get_playing():
            self._remplacer(cle, source, sans_image, ephemere)
            return
        nouveau = self._ouvrir(source[0])

        def pret():
            if self.cle != cle or self.flux.get(cle) is not actuel:
                self._fermer(nouveau)      # tout a changé pendant la préparation
                return
            self._remplacer(cle, source, sans_image, ephemere, flux=nouveau)
        quand_pret(nouveau, pret)

    def liberer(self):
        """Arrête et ferme tous les flux (avant un remix qui réécrit les fichiers, ou en fermant la page) : la lecture
        quitte la barre « en cours de lecture » et MPRIS."""
        self._tout_fermer()
        lectures().terminer(self._proprio())

    def _tout_fermer(self):
        self._reprise = None
        for m in self.flux.values():
            try:
                m.pause()
                m.clear()
            except Exception:  # noqa: BLE001
                pass
        self.flux = {}
        self.cle = None
        self._attente = None
        self.ecran.set_paintable(None)
        self.bandeau.set_visible(False)
        self.commandes.set_media_stream(None)
        self._fin_extrait = None

    # --- position ------------------------------------------------------------------------------------------------

    def position_ref(self):
        """Position de référence (position dans la source en cours − son décalage), en secondes."""
        m = self.media()
        if not m:
            return 0.0
        a = self._attente
        if a is not None and a[0] is m:
            if not a[2] or m.is_seeking():
                return a[1]
            self._attente = None
        return max(0.0, m.get_timestamp() / 1_000_000 - (self.sources[self.cle][1] or 0.0))

    def position(self):
        m = self.media()
        return m.get_timestamp() / 1_000_000 if m else 0.0

    def duree(self):
        m = self.media()
        return m.get_duration() / 1_000_000 if m and m.is_prepared() else None

    def aller_a(self, s_ref, lire=None):
        m = self.media()
        if not m:
            return
        decalage = self.sources[self.cle][1] or 0.0
        self._attente = [m, s_ref, False, bool(lire) or self.en_lecture()]

        def placer():
            m.seek(int(max(0.0, s_ref + decalage) * 1_000_000))
            if lire:
                m.play()
            if self._attente and self._attente[0] is m:
                self._attente[2] = True
        quand_pret(m, placer)

    def aller_a_absolu(self, s, lire=None):
        """Position dans le fichier en cours (sans décalage)."""
        m = self.media()
        if not m:
            return

        def placer():
            m.seek(int(max(0.0, s) * 1_000_000))
            if lire:
                m.play()
        quand_pret(m, placer)

    def lire_extrait(self, debut, fin):
        """Lit de debut à fin (secondes du fichier en cours), puis s'arrête."""
        self._fin_extrait = fin
        self.aller_a_absolu(debut, lire=True)
        self._suivre()

    def pause(self):
        m = self.media()
        if m:
            m.pause()
        a = self._attente
        if a is not None and a[0] is m:
            a[3] = False          # une bascule en cours ne relance pas la lecture

    def _suivre(self):
        if self._minuteur is None:
            self._minuteur = GLib.timeout_add(200, self._tic)

    def _tic(self):
        m = self.media()
        if not m or not self.get_mapped():
            self._minuteur = None
            return False
        if self._fin_extrait is not None and m.get_playing() and self.position() >= self._fin_extrait:
            m.pause()
            self._fin_extrait = None
        if self.sur_position:
            self.sur_position(self.position_ref(), m.get_playing())
        return True


def libelle_position(s):
    return formats.chrono_precis(s)
