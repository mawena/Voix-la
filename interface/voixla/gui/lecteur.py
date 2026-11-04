"""Lecteur vidéo et audio (Gtk.MediaFile + GStreamer), avec bascule entre sources à la même position.

L'image est un Gtk.Picture qui affiche le flux (un Gtk.MediaStream est un GdkPaintable) ; les commandes sont un
Gtk.MediaControls toujours visible, pour la vidéo comme pour le son. Une source sans image (le beat ou une voix seule,
à côté d'une vidéo) se lit sous l'image de la vidéo arrêtée, avec un bandeau qui le dit : pas de second décodage, pas
d'écran qui disparaît et fait sauter la page.
"""

from gi.repository import GLib, Gtk

from .. import formats


def media(chemin):
    m = Gtk.MediaFile.new_for_filename(str(chemin))
    m.set_loop(False)
    return m


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
    (position de référence = position de la source « B » ; une source de décalage d est lue à position + d)."""

    def __init__(self, hauteur=280, sur_position=None):
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
        self.muet = False         # essais : tous les flux, même ceux ouverts plus tard, restent muets
        self.sur_position = sur_position
        self._minuteur = None
        self._fin_extrait = None
        self.connect("unmap", lambda *_: self.pause())
        self.connect("map", lambda *_: self.media() and self._suivre())   # le suivi s'arrête quand on est caché

    # --- sources -------------------------------------------------------------------------------------------------

    def definir(self, sources, cle=None, audio_seul=False, sans_image=()):
        """Remplace les sources ; garde la position si c'est le même contenu sous une autre forme. sans_image : les
        clés des sources sans image (lues sous l'image arrêtée d'une source qui en a une)."""
        self.liberer()
        self.sources = {k: v for k, v in sources.items() if v and v[0]}
        self.audio_seul = audio_seul
        self.sans_image = set(sans_image)
        self.superpose.set_visible(not audio_seul)
        cle = cle if cle in self.sources else next(iter(self.sources), None)
        if cle:
            self._activer(cle, 0.0, False)

    def ajouter(self, cle, source, sans_image=False):
        """Ajoute (ou remplace) une source sans toucher à celle en cours (une piste préparée après coup)."""
        if not source or not source[0]:
            return
        if cle in self.flux and cle != self.cle:
            try:
                self.flux.pop(cle).clear()
            except Exception:  # noqa: BLE001
                pass
        self.sources[cle] = source
        (self.sans_image.add if sans_image else self.sans_image.discard)(cle)

    def _flux(self, cle):
        if cle not in self.flux:
            self.flux[cle] = media(self.sources[cle][0])
            if self.muet:
                self.flux[cle].set_muted(True)
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
        if autre is None:
            return
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
            self.bandeau.set_label(f"{self.sources[cle][2]} · son seul, image arrêtée")
            self.bandeau.set_visible(True)
        else:
            self.ecran.set_paintable(m)
            self.bandeau.set_visible(False)
        self.commandes.set_media_stream(m)
        decalage = self.sources[cle][1] or 0.0

        def placer():
            if position_ref or decalage:
                m.seek(int(max(0.0, position_ref + decalage) * 1_000_000))
            if lire:
                m.play()
        quand_pret(m, placer)
        self._suivre()

    def basculer(self, cle):
        """Passe à une autre source, à la même position (et en lecture si on lisait)."""
        if cle == self.cle or cle not in self.sources:
            return
        ancien = self.media()
        pos = self.position_ref()
        lisait = bool(ancien and ancien.get_playing())
        if ancien:
            ancien.pause()
        self._activer(cle, pos, lisait)

    def media(self):
        return self.flux.get(self.cle) if self.cle else None

    def liberer(self):
        """Arrête et ferme tous les flux (avant un remix qui réécrit les fichiers, ou en quittant la page)."""
        for m in self.flux.values():
            try:
                m.pause()
                m.clear()
            except Exception:  # noqa: BLE001
                pass
        self.flux = {}
        self.cle = None
        self.ecran.set_paintable(None)
        self.bandeau.set_visible(False)
        self.commandes.set_media_stream(None)
        self._fin_extrait = None

    # --- position ------------------------------------------------------------------------------------------------

    def position_ref(self):
        """Position dans la référence (le doublage), en secondes."""
        m = self.media()
        if not m:
            return 0.0
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

        def placer():
            m.seek(int(max(0.0, s_ref + decalage) * 1_000_000))
            if lire:
                m.play()
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
        if not m.get_playing() and self._fin_extrait is None:
            # on continue à suivre lentement (déplacements à la souris) tant que la page est visible
            pass
        return True


def libelle_position(s):
    return formats.chrono_precis(s)
