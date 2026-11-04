"""Frise d'octave : un bandeau coloré par voix (homme, femme, inconnue, muet ; voix 1, 2… en parole), le décalage
de hauteur écrit sur chaque passage, la position de lecture, et un clic pour aller à ce moment."""

import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo  # noqa: E402

from .. import formats, rapports  # noqa: E402

COULEURS = {
    "homme": (0.21, 0.52, 0.89),      # bleu
    "femme": (0.75, 0.38, 0.80),      # violet
    "inconnu": (0.90, 0.65, 0.04),    # ambre
}
LOCUTEURS = [(0.13, 0.57, 0.64), (0.90, 0.38, 0.0), (0.15, 0.64, 0.41), (0.75, 0.11, 0.16)]
LIBELLES = {"homme": "Voix d’homme", "femme": "Voix de femme", "inconnu": "Voix inconnue", "muet": "Muet"}


def couleur(seg):
    if seg["classe"] == "locuteur":
        try:
            k = int(str(seg["voix"]).split()[-1]) - 1
        except ValueError:
            k = 0
        return LOCUTEURS[k % len(LOCUTEURS)]
    return COULEURS.get(seg["classe"])


class Frise(Gtk.DrawingArea):
    def __init__(self, sur_clic=None):
        super().__init__(content_height=64, hexpand=True, has_tooltip=True)
        self.segs, self.blocs, self.duree, self.position = [], [], 0.0, None
        self.sur_clic = sur_clic
        self.set_draw_func(self._dessiner)
        clic = Gtk.GestureClick()
        clic.connect("pressed", self._clic)
        self.add_controller(clic)
        self.connect("query-tooltip", self._infobulle)
        self.set_cursor_from_name("pointer")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Frise des passages : voix et décalage de hauteur"])

    def definir(self, segs, duree):
        self.segs = segs or []
        self.blocs = rapports.fusionner_segments(self.segs)
        self.duree = duree or max((s["fin"] for s in self.segs), default=0.0)
        self.queue_draw()

    def placer(self, t):
        if t is None or self.position is None or abs((self.position or 0) - t) > 0.05:
            self.position = t
            self.queue_draw()

    def _texte(self, cr, texte, taille=8.5, gras=False):
        """Mise en page Pango (le texte « jouet » de Cairo n'a pas le vrai signe moins) ; rend (layout, l, h)."""
        layout = self.create_pango_layout(texte)
        police = Pango.FontDescription.from_string(f"{'Bold ' if gras else ''}{taille}")
        layout.set_font_description(police)
        _encre, logique = layout.get_pixel_extents()
        return layout, logique.width, logique.height

    def _x(self, t, largeur):
        return 0 if self.duree <= 0 else max(0.0, min(largeur, t / self.duree * largeur))

    def _dessiner(self, _zone, cr, largeur, hauteur):
        fg = self.get_color()
        haut, bande = 4, hauteur - 26
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.08)
        cr.rectangle(0, haut, largeur, bande)
        cr.fill()
        if self.duree <= 0:
            return
        for b in self.blocs:
            x0, x1 = self._x(b["debut"], largeur), self._x(b["fin"], largeur)
            c = couleur(b)
            if c is None:   # muet
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.10)
            else:
                cr.set_source_rgba(*c, 0.88)
            cr.rectangle(x0, haut, max(1.0, x1 - x0 - 1), bande)
            cr.fill()
            if c is not None and b.get("decalage") is not None and x1 - x0 > 22:
                layout, lt, ht = self._texte(cr, formats.demi_tons(b["decalage"]), 8.5, True)
                if lt + 6 < x1 - x0:
                    cr.set_source_rgba(1, 1, 1, 0.95)
                    cr.move_to(x0 + (x1 - x0 - lt) / 2, haut + (bande - ht) / 2)
                    PangoCairo.show_layout(cr, layout)
        # graduations
        pas = next((p for p in (5, 10, 15, 30, 60, 120, 300, 600) if self.duree / p <= max(2, largeur / 70)), 600)
        t = 0.0
        while t <= self.duree + 0.01:
            x = self._x(t, largeur)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35)
            cr.rectangle(x, haut + bande, 1, 4)
            cr.fill()
            layout, lt, ht = self._texte(cr, formats.chrono(t), 7.5)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            cr.move_to(min(max(0, x - lt / 2), largeur - lt), hauteur - ht - 1)
            PangoCairo.show_layout(cr, layout)
            t += pas
        if self.position is not None:
            x = self._x(self.position, largeur)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.95)
            cr.rectangle(x - 1, 0, 2, haut + bande + 3)
            cr.fill()

    def _t(self, x):
        largeur = self.get_width() or 1
        return max(0.0, min(self.duree, x / largeur * self.duree))

    def _clic(self, _g, _n, x, _y):
        if self.sur_clic and self.duree > 0:
            self.sur_clic(self._t(x))

    def _infobulle(self, _w, x, _y, _clavier, tip):
        if self.duree <= 0:
            return False
        t = self._t(x)
        s = rapports.segment_a(self.segs, t)
        if not s:
            return False
        lignes = [f"{formats.chrono_precis(s['debut'])} – {formats.chrono_precis(s['fin'])}"]
        if s["classe"] == "muet":
            lignes.append("muet")
        else:
            lignes.append(f"voix {s['voix']}" if s["classe"] != "locuteur" else s["voix"])
            if s.get("f0"):
                lignes.append(f"hauteur médiane {formats.nombre(s['f0'], 0)} Hz")
            if s.get("decalage") is not None:
                lignes.append(f"décalage {formats.demi_tons(s['decalage'])} demi-tons")
        if s.get("base"):
            lignes.append(f"({s['base']})")
        tip.set_text("\n".join(lignes))
        return True


def legende(segs):
    """Petite légende des couleurs présentes."""
    b = Gtk.Box(spacing=14)
    vues = []
    for s in segs:
        cle = s["classe"] if s["classe"] != "locuteur" else s["voix"]
        if cle not in vues:
            vues.append(cle)
    ordre = ["homme", "femme", "inconnu"] + sorted(v for v in vues if v.startswith("voix ")) + ["muet"]
    for cle in ordre:
        if cle not in vues:
            continue
        item = Gtk.Box(spacing=6)
        pastille = Gtk.DrawingArea(content_width=12, content_height=12, valign=Gtk.Align.CENTER)
        seg = {"classe": "locuteur", "voix": cle} if cle.startswith("voix ") else {"classe": cle, "voix": cle}
        c = couleur(seg)

        def dessiner(_z, cr, w, h, c=c, z=pastille):
            if c is None:
                fg = z.get_color()
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.25)
            else:
                cr.set_source_rgba(*c, 0.9)
            cr.arc(w / 2, h / 2, min(w, h) / 2, 0, 6.2832)
            cr.fill()
        pastille.set_draw_func(dessiner)
        item.append(pastille)
        item.append(Gtk.Label(label=LIBELLES.get(cle, cle.capitalize()), css_classes=["caption"]))
        b.append(item)
    return b
