"""Commandes du lecteur d'un résultat : sous l'image dans le détail, et en surimpression en plein écran
(gui/plein_ecran.py). Elles remplacent le Gtk.MediaControls du lecteur ordinaire.

Deux rangées :
- la position : temps écoulé, barre de progression (la boucle A-B y est surlignée), temps restant ;
- lecture / pause, recul et avance de 5 s, son coupé et volume, boucle A-B, vitesse (0,5× à 2×, la hauteur est
  gardée), capture de l'image (vidéo), plein écran. En plein écran s'y ajoutent le choix de la piste et les paroles.

Tout passe par la page (gui/page_resultats.PageDetail) : lecture_pause(), sauter(), aller_a(), basculer_boucle(),
regler_vitesse() (action detail.vitesse), couper_son(), regler_volume(), capturer_image(), basculer_plein_ecran(). Les
positions sont en temps de référence (celui du son doublé, comme la frise et les paroles), quelle que soit la piste.

Sobriété : la position se met à jour 4 fois par seconde au plus, seulement pendant la lecture et si les commandes sont
affichées ; sinon à chaque changement (déplacement, pause, piste, vitesse), quand la page appelle actualiser().
"""

import time

from gi.repository import Gio, GLib, Gtk

from .. import formats
from .. import lecture as regles

TIC_MS = 250
GLISSE_MS = 80               # déplacements regroupés pendant qu'on fait glisser la barre


def _icone(nom, infobulle, *classes, **k):
    b = Gtk.Button(icon_name=nom, tooltip_text=infobulle, css_classes=["flat", *classes], valign=Gtk.Align.CENTER, **k)
    b.update_property([Gtk.AccessibleProperty.LABEL], [infobulle.split(" (")[0]])
    return b


def menu_vitesses():
    """Le menu des vitesses (action detail.vitesse, cible « 1.25 »…)."""
    menu = Gio.Menu()
    for v in regles.VITESSES:
        item = Gio.MenuItem.new(regles.libelle_vitesse(v) + (" (normale)" if v == 1.0 else ""), None)
        item.set_action_and_target_value("detail.vitesse", GLib.Variant.new_string(f"{v:g}"))
        menu.append_item(item)
    return menu


class Commandes(Gtk.Box):
    """Les commandes d'un lecteur de résultat. plein_ecran : version en surimpression (classe osd), avec le choix de la
    piste et l'interrupteur des paroles."""

    def __init__(self, page, plein_ecran=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                         css_classes=["commandes-lecteur", *(["osd", "commandes-plein-ecran"] if plein_ecran else [])])
        self.page = page
        self.plein_ecran = plein_ecran
        self._minuteur = None
        self._glisse = None           # position demandée en faisant glisser la barre, pas encore envoyée
        self._glisse_jusqu = 0.0      # la barre ne suit pas la lecture pendant qu'on la fait glisser
        self._textes = {}

        # rangée 1 : la position
        haut = Gtk.Box(spacing=8)
        self.l_ecoule = Gtk.Label(label="0:00", xalign=1, width_chars=5, css_classes=["caption", "chiffres"])
        self.echelle = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, draw_value=False,
                                 adjustment=Gtk.Adjustment(lower=0, upper=1, step_increment=1, page_increment=5),
                                 css_classes=["position-lecture"])
        self.echelle.update_property([Gtk.AccessibleProperty.LABEL], ["Position de lecture"])
        self.echelle.connect("change-value", self._glisser)
        self.zone_boucle = Gtk.DrawingArea(can_target=False, can_focus=False)
        self.zone_boucle.set_draw_func(self._dessiner_boucle)
        sur_echelle = Gtk.Overlay(child=self.echelle, hexpand=True)
        sur_echelle.add_overlay(self.zone_boucle)
        self.l_reste = Gtk.Label(label="", xalign=0, width_chars=6, css_classes=["caption", "chiffres", "dim-label"])
        self.l_reste.set_tooltip_text("Temps restant")
        haut.append(self.l_ecoule)
        haut.append(sur_echelle)
        haut.append(self.l_reste)
        self.append(haut)

        # rangée 2 : les boutons
        bas = Gtk.Box(spacing=4)
        self.b_lecture = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER,
                                    css_classes=["circular", "bouton-lecture"], tooltip_text="Lecture (Espace)")
        self.b_lecture.connect("clicked", lambda *_: page.lecture_pause())
        self.b_recul = _icone("media-seek-backward-symbolic", "Reculer de 5 s (flèche gauche)")
        self.b_recul.connect("clicked", lambda *_: page.sauter(-regles.SAUT_S))
        self.b_avance = _icone("media-seek-forward-symbolic", "Avancer de 5 s (flèche droite)")
        self.b_avance.connect("clicked", lambda *_: page.sauter(regles.SAUT_S))
        self.b_son = Gtk.ToggleButton(icon_name="audio-volume-high-symbolic", valign=Gtk.Align.CENTER,
                                      css_classes=["flat"], tooltip_text="Couper le son (M)")
        self.b_son.update_property([Gtk.AccessibleProperty.LABEL], ["Couper le son"])
        self.b_son.connect("toggled", lambda b: self._maj or page.couper_son(b.get_active()))
        self.volume = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, draw_value=False, valign=Gtk.Align.CENTER,
                                adjustment=Gtk.Adjustment(lower=0, upper=1, step_increment=0.05, page_increment=0.1))
        self.volume.set_size_request(90, -1)
        self.volume.update_property([Gtk.AccessibleProperty.LABEL], ["Volume"])
        self.volume.connect("value-changed", lambda s: self._maj or page.regler_volume(s.get_value()))
        self._maj = False
        for w in (self.b_lecture, self.b_recul, self.b_avance, self.b_son, self.volume):
            bas.append(w)
        bas.append(Gtk.Box(hexpand=True))
        self.l_boucle = Gtk.Label(css_classes=["caption", "chiffres", "dim-label"], visible=False)
        bas.append(self.l_boucle)
        self.b_boucle = Gtk.Button(label="A-B", valign=Gtk.Align.CENTER, css_classes=["flat", "bouton-boucle"])
        self.b_boucle.connect("clicked", lambda *_: page.basculer_boucle())
        bas.append(self.b_boucle)
        self.b_vitesse = Gtk.MenuButton(label="1×", menu_model=menu_vitesses(), valign=Gtk.Align.CENTER,
                                        css_classes=["flat", "chiffres"],
                                        tooltip_text="Vitesse de lecture, la hauteur est gardée ([ et ])")
        self.b_vitesse.update_property([Gtk.AccessibleProperty.LABEL], ["Vitesse de lecture"])
        bas.append(self.b_vitesse)
        self.b_piste = None
        self.b_paroles = None
        if plein_ecran:
            self.b_piste = Gtk.MenuButton(icon_name="audio-x-generic-symbolic", valign=Gtk.Align.CENTER,
                                          css_classes=["flat"], tooltip_text="Ce qu’on écoute (touches 1 à 5, B)",
                                          menu_model=page.menu_pistes())
            self.b_piste.update_property([Gtk.AccessibleProperty.LABEL], ["Ce qu’on écoute"])
            bas.append(self.b_piste)
            self.b_paroles = Gtk.ToggleButton(icon_name="format-justify-center-symbolic", valign=Gtk.Align.CENTER,
                                              css_classes=["flat"], action_name="win.paroles",
                                              tooltip_text="Paroles sur l’image")
            self.b_paroles.update_property([Gtk.AccessibleProperty.LABEL], ["Paroles sur l’image"])
            bas.append(self.b_paroles)
        self.b_capture = _icone("camera-photo-symbolic", "Enregistrer l’image en PNG (S)")
        self.b_capture.connect("clicked", lambda *_: page.capturer_image())
        bas.append(self.b_capture)
        self.b_plein = _icone("view-restore-symbolic" if plein_ecran else "view-fullscreen-symbolic",
                              "Quitter le plein écran (F ou Échap)" if plein_ecran else "Plein écran (F)")
        self.b_plein.connect("clicked", lambda *_: page.basculer_plein_ecran())
        bas.append(self.b_plein)
        self.append(bas)
        # un clic ne laisse pas le focus sur un bouton : Espace reste lecture / pause, les flèches restent ±5 s (Tab
        # mène toujours à chaque bouton, et Espace ou Entrée l'active alors)
        enfant = bas.get_first_child()
        while enfant is not None:
            enfant.set_focus_on_click(False)
            if isinstance(enfant, Gtk.MenuButton) and enfant.get_first_child() is not None:
                enfant.get_first_child().set_focus_on_click(False)
            enfant = enfant.get_next_sibling()
        self.volume.set_focus_on_click(True)      # le volume garde le focus : les flèches le règlent
        # fenêtre étroite (Adw.Breakpoint de la page) : ces commandes-là disparaissent d'abord (la capture et la boucle
        # écrite en clair suivent le volume : voir _etroit)
        self.secondaires = (self.volume, self.b_recul, self.b_avance)
        self.connect("map", lambda *_: (self.actualiser(), self._suivre()))

    # --- mise à jour -------------------------------------------------------------------------------------------------

    def _texte(self, label, texte):
        if self._textes.get(label) != texte:
            self._textes[label] = texte
            label.set_label(texte)

    def actualiser(self):
        """Tout ce que montrent les commandes, d'après le lecteur de la page (rien de coûteux)."""
        page, lec = self.page, self.page.lecteur
        lit = lec.en_lecture()
        pos = lec.position_ref()
        duree = page.duree_lecture()
        glisse = time.monotonic() < self._glisse_jusqu
        if not glisse:
            self._texte(self.l_ecoule, formats.chrono(pos))
        self._texte(self.l_reste, regles.temps_restant(pos, duree))
        adj = self.echelle.get_adjustment()
        haut = max(1.0, duree or 1.0)
        if abs(adj.get_upper() - haut) > 0.01:
            adj.set_upper(haut)
            self.zone_boucle.queue_draw()
        if not glisse and abs(adj.get_value() - pos) > 0.05:
            adj.set_value(min(pos, haut))
        self.echelle.set_sensitive(lec.media() is not None)
        icone = "media-playback-pause-symbolic" if lit else "media-playback-start-symbolic"
        if self.b_lecture.get_icon_name() != icone:
            self.b_lecture.set_icon_name(icone)
            texte = "Pause" if lit else "Lecture"
            self.b_lecture.set_tooltip_text(f"{texte} (Espace)")
            self.b_lecture.update_property([Gtk.AccessibleProperty.LABEL], [texte])
        self._maj = True
        try:
            if self.b_son.get_active() != lec.coupe:
                self.b_son.set_active(lec.coupe)
            self.b_son.set_icon_name("audio-volume-muted-symbolic" if lec.coupe or lec.volume == 0 else
                                     "audio-volume-high-symbolic" if lec.volume > 0.66 else
                                     "audio-volume-medium-symbolic" if lec.volume > 0.33 else
                                     "audio-volume-low-symbolic")
            self.b_son.set_tooltip_text("Rendre le son (M)" if lec.coupe else "Couper le son (M)")
            if abs(self.volume.get_value() - lec.volume) > 1e-3:
                self.volume.set_value(lec.volume)
        finally:
            self._maj = False
        possible = page.vitesse_possible()
        self.b_vitesse.set_label(regles.libelle_vitesse(lec.vitesse))
        self.b_vitesse.set_sensitive(possible is not False)
        if possible is False:
            self.b_vitesse.set_tooltip_text("Vitesse indisponible : ce lecteur ne garderait pas la hauteur du son")
        self._maj_boucle()
        self.b_capture.set_visible(page.image_a_capturer() and not self._etroit())

    def _etroit(self):
        return not self.volume.get_visible() and not self.plein_ecran

    def _maj_boucle(self):
        b = self.page.lecteur.boucle
        complete = regles.boucle_complete(b)
        if complete:
            infobulle = f"Effacer la boucle {regles.libelle_boucle(b)} (L)"
            nom = f"Boucle A-B de {formats.chrono(b[0])} à {formats.chrono(b[1])}, activée"
        elif b:
            infobulle = f"Fin de la boucle ici ; début posé à {formats.chrono(b[0])} (L)"
            nom = "Poser la fin de la boucle A-B"
        else:
            infobulle = "Répéter un passage : début de la boucle A-B ici (L)"
            nom = "Poser le début de la boucle A-B"
        if self._textes.get("boucle") != (b, infobulle):
            self._textes["boucle"] = (b, infobulle)
            self.b_boucle.set_label("A-B" if complete or not b else "A…")
            self.b_boucle.set_tooltip_text(infobulle)
            self.b_boucle.update_property([Gtk.AccessibleProperty.LABEL], [nom])
            (self.b_boucle.add_css_class if b else self.b_boucle.remove_css_class)("accent")
            (self.b_boucle.remove_css_class if b else self.b_boucle.add_css_class)("flat")
            self.l_boucle.set_label(regles.libelle_boucle(b) if complete else "")
            self.l_boucle.set_visible(complete and not self._etroit())
            self.zone_boucle.queue_draw()

    def _suivre(self):
        if self._minuteur is None and self.get_mapped() and self.page.lecteur.en_lecture():
            self._minuteur = GLib.timeout_add(TIC_MS, self._tic)

    def _tic(self):
        if not self.get_mapped():
            self._minuteur = None
            return False
        self.actualiser()
        if not self.page.lecteur.en_lecture():
            self._minuteur = None
            return False
        return True

    def lecture_changee(self):
        """Lecture, pause, fin, vitesse, piste : la page prévient (rappel sur_etat du lecteur)."""
        if self.get_mapped():
            self.actualiser()
            self._suivre()

    # --- barre de progression ----------------------------------------------------------------------------------------

    def _glisser(self, _echelle, _defilement, valeur):
        """Clic ou glissement sur la barre (souris, clavier) : déplacements regroupés toutes les GLISSE_MS."""
        haut = self.echelle.get_adjustment().get_upper()
        valeur = max(0.0, min(haut, valeur))
        self._glisse_jusqu = time.monotonic() + 0.5
        self._texte(self.l_ecoule, formats.chrono(valeur))
        if self._glisse is None:
            GLib.timeout_add(GLISSE_MS, self._envoyer)
        self._glisse = valeur
        return False

    def _envoyer(self):
        v, self._glisse = self._glisse, None
        if v is not None:
            self.page.aller_a(v)
        return False

    def _dessiner_boucle(self, _zone, cr, largeur, hauteur):
        """La boucle A-B sur la barre : une bande de la couleur d'accent entre A et B (un trait à A seul)."""
        b = self.page.lecteur.boucle
        duree = self.echelle.get_adjustment().get_upper()
        if not b or duree <= 0:
            return
        rect = self.echelle.get_range_rect()
        debut, fin = self.echelle.get_slider_range()
        curseur = max(0, fin - debut)
        x0, utile = rect.x + curseur / 2, max(1.0, rect.width - curseur)
        ok, c = self.get_style_context().lookup_color("accent_color")
        r, v, bl = (c.red, c.green, c.blue) if ok else (0.21, 0.52, 0.89)
        milieu = rect.y + rect.height / 2
        xa = x0 + utile * min(1.0, b[0] / duree)
        if regles.boucle_complete(b):
            xb = x0 + utile * min(1.0, b[1] / duree)
            cr.set_source_rgba(r, v, bl, 0.35)
            cr.rectangle(xa, milieu - 5, max(2.0, xb - xa), 10)
            cr.fill()
            cr.set_source_rgba(r, v, bl, 0.95)
            for x in (xa, xb):
                cr.rectangle(x - 1, milieu - 7, 2, 14)
            cr.fill()
        else:
            cr.set_source_rgba(r, v, bl, 0.95)
            cr.rectangle(xa - 1, milieu - 7, 2, 14)
            cr.fill()
