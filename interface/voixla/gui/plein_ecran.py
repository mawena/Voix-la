"""Plein écran du lecteur d'un résultat : l'image seule sur fond noir, les commandes et le titre en surimpression, qui
se masquent après MASQUE_MS sans mouvement (le pointeur aussi) et reviennent au moindre mouvement ou à une touche ; les
paroles en surimpression en bas, lisibles sur toute image (texte blanc cerné de sombre, gui/vue_paroles.Karaoke). Pour
un MP3, la pochette (ou la miniature de la vidéo, ou la pochette générée) et les paroles en grand.

La vue remplace le contenu de la fenêtre (Fenetre.entrer_plein_ecran : une pile, le reste de la fenêtre est caché et
ne se dessine plus) ; le lecteur reste celui de la page, et son image est reprise telle quelle (liaison de la propriété
« paintable » de son écran) : une bascule de piste, l'image arrêtée d'une piste sans image, tout suit. La lecture
continue sans coupure à l'entrée et à la sortie. Les touches sont celles de la page (PageDetail.raccourcis), plus
Échap pour sortir ; un double-clic sur l'image sort aussi.

Sobriété : rien ne se redessine que l'image (au rythme de la vidéo), les paroles (25 images/s au plus, pendant la
lecture) et les commandes quand elles sont visibles (4 fois par seconde au plus) ; la page, ses frises et ses
visualisations, cachées, ne se redessinent plus.
"""

from gi.repository import Adw, GLib, GObject, Gtk, Pango

from .. import bibliotheque as bib
from .. import lecture as regles
from .commandes import Commandes

MASQUE_MS = 3000             # commandes et pointeur masqués après 3 s sans mouvement
MARGE_PAROLES = 12           # paroles au-dessus du bord bas (commandes masquées)


class PleinEcran(Adw.Bin):
    def __init__(self, page):
        super().__init__(css_classes=["plein-ecran"], focusable=True)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{page.res.titre_court}, plein écran"])
        self.page = page
        self._minuteur = None
        self._dernier = None          # dernière position du pointeur (les mouvements de moins de 2 px ne comptent pas)
        self._survol = False          # pointeur sur les commandes : elles restent
        self._visibles = True
        self.toasts = Adw.ToastOverlay()
        self.set_child(self.toasts)
        surface = Gtk.Overlay()
        self.toasts.set_child(surface)

        # l'image : celle de l'écran du lecteur de la page, quelle que soit la piste
        # pile non homogène : seule la vue affichée compte pour la taille (la vue du MP3 ne déborde pas sur la vidéo)
        self.pile = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True,
                              hhomogeneous=False, vhomogeneous=False)
        self.image = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.image.update_property([Gtk.AccessibleProperty.LABEL], ["Vidéo"])
        self._liaisons = [page.lecteur.ecran.bind_property("paintable", self.image, "paintable",
                                                           GObject.BindingFlags.SYNC_CREATE)]
        self.pile.add_named(self.image, "video")
        self.son = self._vue_son()
        self.pile.add_named(self.son, "son")
        surface.set_child(self.pile)

        # bandeau d'une piste sans image (« Beat seul · son seul, image arrêtée »), comme sous le lecteur
        self.bandeau = Gtk.Label(css_classes=["osd", "bandeau-ecran"], halign=Gtk.Align.START, valign=Gtk.Align.START,
                                 margin_start=24, margin_top=86, can_target=False)
        for prop in ("label", "visible"):
            self._liaisons.append(page.lecteur.bandeau.bind_property(prop, self.bandeau, prop,
                                                                     GObject.BindingFlags.SYNC_CREATE))
        surface.add_overlay(self.bandeau)

        # paroles en surimpression (vidéo)
        self.paroles = page.creer_karaoke("video", precedente=False)
        self.paroles.set_valign(Gtk.Align.END)
        self.paroles.set_margin_start(48)
        self.paroles.set_margin_end(48)
        self.paroles.set_margin_bottom(MARGE_PAROLES)
        surface.add_overlay(self.paroles)
        self._anim_paroles = None

        # en haut : titre, piste et voix, sortie
        tete = Gtk.Box(spacing=12, css_classes=["osd", "plein-ecran-tete"], valign=Gtk.Align.START)
        textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.l_titre = Gtk.Label(label=page.res.titre_court, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                 css_classes=["title-4"])
        self.l_detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["caption"])
        textes.append(self.l_titre)
        textes.append(self.l_detail)
        tete.append(textes)
        sortir = Gtk.Button(icon_name="view-restore-symbolic", valign=Gtk.Align.CENTER, css_classes=["circular"],
                            tooltip_text="Quitter le plein écran (Échap ou F)")
        sortir.update_property([Gtk.AccessibleProperty.LABEL], ["Quitter le plein écran"])
        sortir.connect("clicked", lambda *_: page.basculer_plein_ecran())
        tete.append(sortir)
        self.haut = Gtk.Revealer(child=tete, reveal_child=True, valign=Gtk.Align.START,
                                 transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=200)
        surface.add_overlay(self.haut)

        # en bas : les commandes
        self.commandes = Commandes(page, plein_ecran=True)
        self.commandes.set_margin_start(24)
        self.commandes.set_margin_end(24)
        self.commandes.set_margin_bottom(24)
        self.bas = Gtk.Revealer(child=self.commandes, reveal_child=True, valign=Gtk.Align.END,
                                transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=200)
        surface.add_overlay(self.bas)
        survol = Gtk.EventControllerMotion()
        survol.connect("enter", lambda *_: setattr(self, "_survol", True))
        survol.connect("leave", lambda *_: setattr(self, "_survol", False))
        self.commandes.add_controller(survol)

        # mouvements, double-clic, touches
        mouvement = Gtk.EventControllerMotion()
        mouvement.connect("motion", self._mouvement)
        self.add_controller(mouvement)
        clic = Gtk.GestureClick()
        clic.connect("pressed", self._clic)
        self.pile.add_controller(clic)
        touches = Gtk.EventControllerKey()
        touches.connect("key-pressed", lambda *_: (self.montrer_commandes(), False)[1])
        self.add_controller(touches)
        self.add_controller(page.raccourcis(plein_ecran=True))
        self.insert_action_group("detail", page.actions)
        self.connect("map", lambda *_: self._apparition())
        self.connect("unmap", lambda *_: self._arreter_minuteur())
        self.forme_changee()
        self.lecture_changee()

    # --- MP3 : pochette et paroles en grand --------------------------------------------------------------------------

    def _vue_son(self):
        from .vignettes import Image
        page = self.page
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, halign=Gtk.Align.CENTER,
                        valign=Gtk.Align.CENTER, margin_top=72, margin_bottom=130, margin_start=48, margin_end=48)
        self.pochette = Image(largeur_min=200, largeur_nat=480, rayon=14, taille_note=96)
        self.pochette.set_halign(Gtk.Align.CENTER)
        try:
            self.pochette.lier(bib.version(page.res, {v["id"]: v["nom"] for v in page.fenetre.voix}))
        except Exception:  # noqa: BLE001 — sans image, la pochette générée
            pass
        boite.append(self.pochette)
        titre = Gtk.Label(label=page.res.titre_court, wrap=True, justify=Gtk.Justification.CENTER,
                          css_classes=["title-1", "plein-ecran-texte"])
        boite.append(titre)
        voix = page.res.voix_nom or page.res.voix or ""
        if voix:
            boite.append(Gtk.Label(label=f"voix {voix}", css_classes=["title-4", "plein-ecran-texte", "dim-label"]))
        # les paroles en grand sous la pochette (une ligne de plus que sur l'image)
        self.paroles_son = page.creer_karaoke("video", precedente=True, taille_min="grande")
        self.paroles_son.set_size_request(560, -1)
        boite.append(self.paroles_son)
        return boite

    def forme_changee(self):
        """Vidéo ou MP3 (bascule de la page) : l'image, ou la pochette et les paroles en grand."""
        son = self.page.forme != "video"
        self.pile.set_visible_child_name("son" if son else "video")
        self.paroles.set_visible(not son and self.page.paroles_en_surimpression())

    # --- commandes masquées sans mouvement ---------------------------------------------------------------------------

    def _apparition(self):
        self.grab_focus()
        self._placer_paroles(animer=False)
        self.montrer_commandes()
        self.lecture_changee()

    def _mouvement(self, _c, x, y):
        if self._dernier is not None and abs(x - self._dernier[0]) < 2 and abs(y - self._dernier[1]) < 2:
            return
        self._dernier = (x, y)
        self.montrer_commandes()

    def _clic(self, _g, n, _x, _y):
        self.montrer_commandes()
        if n == 2:
            self.page.basculer_plein_ecran()

    def montrer_commandes(self):
        if not self._visibles:
            self._visibles = True
            self.set_cursor(None)
            self.haut.set_reveal_child(True)
            self.bas.set_reveal_child(True)
            self.commandes.lecture_changee()
            self._placer_paroles()
        self._arreter_minuteur()
        self._minuteur = GLib.timeout_add(MASQUE_MS, self._masquer)

    def _arreter_minuteur(self):
        if self._minuteur is not None:
            GLib.source_remove(self._minuteur)
            self._minuteur = None

    def _masquer(self):
        self._minuteur = None
        ouvert = any(b is not None and b.get_active() for b in (self.commandes.b_vitesse, self.commandes.b_piste))
        if self._survol or ouvert:
            self._minuteur = GLib.timeout_add(MASQUE_MS, self._masquer)
            return False
        self.masquer_commandes()
        return False

    def masquer_commandes(self):
        self._visibles = False
        self.haut.set_reveal_child(False)
        self.bas.set_reveal_child(False)
        self.set_cursor_from_name("none")
        self.grab_focus()             # le focus ne reste pas sur un bouton caché (Espace : lecture, pas ce bouton)
        self._placer_paroles()

    @property
    def commandes_visibles(self):
        return self._visibles

    def _placer_paroles(self, animer=True):
        """Les paroles remontent au-dessus des commandes quand elles apparaissent, et redescendent ensuite."""
        commandes = self.commandes.measure(Gtk.Orientation.VERTICAL, -1)[1]     # hauteur naturelle
        # le karaoké garde sous le texte la place d'une ligne repliée : une petite marge suffit sans les commandes
        haut = commandes + 24 if self._visibles else MARGE_PAROLES
        if self.paroles.get_margin_bottom() == haut:
            return
        if self._anim_paroles is not None:
            self._anim_paroles.skip()
            self._anim_paroles = None
        if not animer or not self.get_mapped():
            self.paroles.set_margin_bottom(haut)
            return
        cible = Adw.PropertyAnimationTarget.new(self.paroles, "margin-bottom")
        self._anim_paroles = Adw.TimedAnimation.new(self.paroles, self.paroles.get_margin_bottom(), haut, 200, cible)
        self._anim_paroles.play()

    # --- suivi de la lecture -----------------------------------------------------------------------------------------

    def lecture_changee(self):
        """Lecture, pause, piste, vitesse : titre, commandes et paroles suivent."""
        e = self.page.etat_lecture()
        morceaux = [x for x in (regles.ligne_detail(e), regles.libelle_boucle(self.page.lecteur.boucle)) if x]
        self.l_detail.set_label(" · ".join(morceaux))
        self.commandes.lecture_changee()
        lec = self.page.lecteur
        for k in (self.paroles, self.paroles_son):
            if k.get_mapped():
                k.placer(lec.position_ref(), lec.en_lecture())

    def placer(self, t, lecture):
        """Déplacement (clic, touche) : les paroles sautent au nouvel instant."""
        for k in (self.paroles, self.paroles_son):
            if k.get_mapped():
                k.placer(t, lecture)
        self.commandes.lecture_changee()

    def fermer(self):
        """Avant de quitter le plein écran : plus de liaison à l'écran du lecteur, plus de minuteur."""
        self._arreter_minuteur()
        for liaison in self._liaisons:
            liaison.unbind()
        self._liaisons = []
        self.image.set_paintable(None)
        for k in (self.paroles, self.paroles_son):
            if getattr(k, "animateur", None):
                k.animateur.arreter()
        self.pochette.delier()

    def toast(self, texte, bouton=None, action=None, duree=4):
        t = Adw.Toast(title=GLib.markup_escape_text(texte), timeout=duree)
        if bouton and action:
            t.set_button_label(bouton)
            t.connect("button-clicked", lambda *_: action())
        self.toasts.add_toast(t)
