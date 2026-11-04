"""Visualisations du son, synchronisées sur un lecteur : courbe de mélodie, spectrogramme qui défile, forme d'onde,
vu-mètre. Les données sont précalculées (voixla.analyse) ; ici on ne fait que dessiner.

Sobriété : un widget ne s'anime (tic de l'horloge d'image, 25 images par seconde au plus) que pendant la lecture et
s'il est affiché ; sinon il ne se redessine que quand la position change. GTK suspend l'horloge d'une fenêtre cachée.
"""

import math
import time

import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from .. import formats, rapports  # noqa: E402
from .frise import couleur  # noqa: E402

try:
    import numpy as np
except ImportError:   # les visualisations sont alors indisponibles (le reste de l'interface marche)
    np = None

IMAGES_S = 25
FENETRE_S = 12.0          # largeur de temps visible (mélodie, spectrogramme)
AVANT = 0.3               # le curseur est au tiers gauche


def texte(widget, cr, chaine, taille=8.0, gras=False):
    layout = widget.create_pango_layout(chaine)
    layout.set_font_description(Pango.FontDescription.from_string(f"{'Bold ' if gras else ''}{taille}"))
    _e, l = layout.get_pixel_extents()
    return layout, l.width, l.height


def sombre():
    return Adw.StyleManager.get_default().get_dark()


def accent(widget):
    """Couleur d'accent du thème (rgb), bleu GNOME à défaut."""
    try:
        ok, c = widget.get_style_context().lookup_color("accent_color")
        if ok:
            return c.red, c.green, c.blue
    except (AttributeError, TypeError):
        pass
    return 0.21, 0.52, 0.89


class Animateur:
    """Redessine un widget à 25 images par seconde au plus pendant la lecture. fournisseur() → (position_s, lecture)."""

    def __init__(self, widget, fournisseur):
        self.widget, self.fournisseur = widget, fournisseur
        self.ident = None
        self.dernier = 0.0

    def relancer(self):
        if self.ident is None and self.widget.get_mapped():
            self.ident = self.widget.add_tick_callback(self._tic)

    def arreter(self):
        if self.ident is not None:
            self.widget.remove_tick_callback(self.ident)
            self.ident = None

    def _tic(self, _w, horloge):
        maintenant = horloge.get_frame_time() / 1e6
        if maintenant - self.dernier < 1.0 / IMAGES_S - 0.002:
            return GLib.SOURCE_CONTINUE
        self.dernier = maintenant
        t, lecture = self.fournisseur()
        self.widget.placer(t)
        if not lecture:
            self.ident = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE


class Visu(Gtk.DrawingArea):
    """Base : position, fenêtre de temps, clic pour aller à un moment, animation pendant la lecture."""

    def __init__(self, hauteur, sur_clic=None, accessible=""):
        super().__init__(content_height=hauteur, hexpand=True)
        self.position = 0.0
        self.duree = 0.0
        self.sur_clic = sur_clic
        self.animateur = None
        self.message = ""
        self.set_draw_func(self._dessiner)
        if sur_clic:
            clic = Gtk.GestureClick()
            clic.connect("pressed", lambda _g, _n, x, _y: self._clic(x))
            self.add_controller(clic)
            self.set_cursor_from_name("pointer")
        if accessible:
            self.update_property([Gtk.AccessibleProperty.LABEL], [accessible])
        self.connect("unmap", lambda *_: self.animateur and self.animateur.arreter())

    def suivre(self, fournisseur):
        self.animateur = Animateur(self, fournisseur)

    def placer(self, t, lecture=False):
        t = float(t or 0.0)
        if abs(t - self.position) > 1e-3:
            self.position = t
            self.queue_draw()
        if lecture and self.animateur:
            self.animateur.relancer()

    def dire(self, message):
        self.message = message
        self.queue_draw()

    # fenêtre glissante [t0, t1] autour de la position
    def fenetre(self):
        t0 = self.position - AVANT * FENETRE_S
        if self.duree and self.duree < FENETRE_S:
            t0 = 0.0
        t0 = max(0.0, min(t0, max(0.0, self.duree - FENETRE_S))) if self.duree else max(0.0, t0)
        return t0, t0 + FENETRE_S

    def _clic(self, x):
        t0, t1 = self.fenetre()
        largeur = max(1, self.get_width() - self.marge_gauche())
        t = t0 + (x - self.marge_gauche()) / largeur * (t1 - t0)
        if self.sur_clic:
            self.sur_clic(max(0.0, min(self.duree or t, t)))

    def marge_gauche(self):
        return 0

    def _dessiner(self, _z, cr, largeur, hauteur):
        fg = self.get_color()
        if self.message:
            layout, l, h = texte(self, cr, self.message, 9)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            cr.move_to(max(4, (largeur - l) / 2), (hauteur - h) / 2)
            PangoCairo.show_layout(cr, layout)
            return
        self.dessiner(cr, largeur, hauteur, fg)

    def dessiner(self, cr, largeur, hauteur, fg):
        pass

    def curseur(self, cr, x, haut, bas, fg):
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.9)
        cr.rectangle(x - 1, haut, 2, bas - haut)
        cr.fill()


# --- Courbe de mélodie -----------------------------------------------------------------------------------------------

class Melodie(Visu):
    """Origine en trait fin, consigne (origine décalée) en pointillés, voix convertie en trait plein ; couleurs de la
    frise d'octave ; échelle en notes (do, ré…) ou en Hz."""

    def __init__(self, sur_clic=None, en_hz=False):
        super().__init__(170, sur_clic, "Courbe de mélodie : origine, consigne et voix convertie")
        self.en_hz = en_hz
        self.c = None
        self.dire("Chargement de la courbe de mélodie…")

    def definir(self, courbes, segs, duree=None):
        self.c = courbes
        n = len(courbes["source"])
        self.pas = float(courbes["pas_s"])
        self.duree = duree or n * self.pas
        self.src = courbes["source"]
        self.conv = courbes["convertie"]
        from .. import analyse
        self.cons = analyse.consigne(self.src, courbes["decalage"])
        self.choeurs = courbes.get("choeurs")
        # couleur de chaque trame d'après la frise (indice dans self.teintes)
        self.teintes = [None]
        idx = np.zeros(n, np.int16)
        t = np.arange(n) * self.pas
        for s in segs or []:
            c = couleur(s)
            if c is None:
                continue
            if c not in self.teintes:
                self.teintes.append(c)
            idx[(t >= s["debut"]) & (t < s["fin"])] = self.teintes.index(c)
        self.idx = idx
        # l'échelle couvre aussi la voix d'origine (une octave au-dessus de la consigne quand on descend de −12)
        voisees = np.concatenate([v[v > 0] for v in (self.conv, self.cons, self.src) if v is not None]) if n \
            else np.zeros(0)
        if len(voisees) > 20:
            lo, hi = np.percentile(voisees, [2, 98])
        else:
            lo, hi = 110.0, 440.0
        m_lo = 69 + 12 * math.log2(max(40.0, lo) / 440) - 2
        m_hi = 69 + 12 * math.log2(max(lo * 1.1, hi) / 440) + 2
        if m_hi - m_lo < 14:
            milieu = (m_lo + m_hi) / 2
            m_lo, m_hi = milieu - 7, milieu + 7
        self.m_lo, self.m_hi = m_lo, m_hi
        self.message = ""
        self.queue_draw()

    def marge_gauche(self):
        return 42

    def _y(self, hz, haut, h):
        m = 69 + 12 * np.log2(np.maximum(hz, 1e-3) / 440)
        return haut + h - (m - self.m_lo) / (self.m_hi - self.m_lo) * h

    def dessiner(self, cr, largeur, hauteur, fg):
        from .. import analyse
        g = self.marge_gauche()
        haut, bas = 6, hauteur - 16
        h = bas - haut
        w = largeur - g - 4
        t0, t1 = self.fenetre()
        # grille et échelle
        cr.set_line_width(1)
        if self.en_hz:
            reperes = [f for f in (50, 75, 100, 150, 200, 300, 400, 600, 800, 1200)
                       if self.m_lo <= 69 + 12 * math.log2(f / 440) <= self.m_hi]
            etiquettes = [(f, f"{f} Hz") for f in reperes]
        else:
            etiquettes = []
            for m in range(int(math.ceil(self.m_lo)), int(self.m_hi) + 1):
                if m % 12 in (0, 4, 7):   # do, mi, sol
                    f = analyse.hz_de_midi(m)
                    etiquettes.append((f, analyse.note_fr(f) if m % 12 == 0 or (self.m_hi - self.m_lo) < 26 else ""))
        for f, nom in etiquettes:
            y = float(self._y(np.array(f), haut, h))
            fort = nom.startswith("do") or self.en_hz
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.16 if fort else 0.07)
            cr.move_to(g, y)
            cr.line_to(g + w, y)
            cr.stroke()
            if nom:
                layout, lt, ht = texte(self, cr, nom, 7.5)
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
                cr.move_to(g - lt - 5, y - ht / 2)
                PangoCairo.show_layout(cr, layout)
        # graduations du temps
        pas_t = 2 if FENETRE_S <= 16 else 5
        t = math.ceil(t0 / pas_t) * pas_t
        while t <= t1:
            x = g + (t - t0) / (t1 - t0) * w
            layout, lt, ht = texte(self, cr, formats.chrono(t), 7.5)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.5)
            cr.move_to(x - lt / 2, hauteur - ht - 1)
            PangoCairo.show_layout(cr, layout)
            t += pas_t
        if self.c is None:
            return
        i0 = max(0, int(t0 / self.pas) - 1)
        i1 = min(len(self.src), int(t1 / self.pas) + 2)
        if i1 <= i0:
            return
        xs = g + (np.arange(i0, i1) * self.pas - t0) / (t1 - t0) * w
        cr.save()
        cr.rectangle(g, haut - 2, w, h + 4)
        cr.clip()
        neutre = (fg.red, fg.green, fg.blue)
        if self.choeurs is not None:
            self._tracer(cr, xs, self.choeurs[i0:i1], None, haut, h, neutre, 0.28, 1.0, [1, 3])
        self._tracer(cr, xs, self.src[i0:i1], None, haut, h, neutre, 0.5, 1.0, None)
        # voix convertie aux couleurs de la frise, puis la consigne en pointillés par-dessus (couleur du texte) :
        # là où la voix tient la consigne, les pointillés courent sur le trait plein
        self._tracer(cr, xs, self.conv[i0:i1], self.idx[i0:i1], haut, h, neutre, 0.95, 2.6, None)
        self._tracer(cr, xs, self.cons[i0:i1], None, haut, h, neutre, 0.85, 1.2, [4, 3])
        cr.restore()
        x = g + (self.position - t0) / (t1 - t0) * w
        if g <= x <= g + w:
            self.curseur(cr, x, haut - 2, bas + 2, fg)

    def _tracer(self, cr, xs, hz, idx, haut, h, neutre, alpha, epaisseur, tirets):
        """Une courbe, coupée là où ce n'est pas voisé, colorée passage par passage (couleurs de la frise)."""
        ys = self._y(hz, haut, h)
        voise = hz > 0
        # pas de trait entre deux trames séparées par un saut de plus de 3 demi-tons (erreur d'octave, attaque)
        m = 12 * np.log2(np.maximum(hz, 1e-3))
        lien = np.zeros(len(hz), bool)
        if len(hz) > 1:
            lien[:-1] = voise[:-1] & voise[1:] & (np.abs(np.diff(m)) <= 3)
        cr.set_line_width(epaisseur)
        cr.set_dash(tirets or [], 0)
        cr.set_line_join(1)
        n = len(hz)
        i = 0
        while i < n:
            if not voise[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and lien[j]:
                j += 1
            k = i
            while k <= j:   # morceaux de même couleur dans la portion voisée i..j
                teinte = int(idx[k]) if idx is not None else 0
                m = k
                while m + 1 <= j and (idx is None or idx[m + 1] == teinte):
                    m += 1
                c = self.teintes[teinte] if idx is not None and teinte else neutre
                cr.set_source_rgba(*c, alpha)
                cr.move_to(xs[k], ys[k])
                fin = min(m + 1, j)   # jusqu'au premier point du morceau suivant : pas de trou
                for q in range(k + 1, fin + 1):
                    cr.line_to(xs[q], ys[q])
                if fin == k:
                    cr.line_to(xs[k] + 1, ys[k])
                cr.stroke()
                k = m + 1
            i = j + 1
        cr.set_dash([], 0)


def legende_melodie():
    b = Gtk.Box(spacing=16)
    for motif, libelle in (("fin", "voix d’origine"), ("tirets", "consigne (origine décalée)"),
                           ("plein", "voix convertie")):
        item = Gtk.Box(spacing=6)
        z = Gtk.DrawingArea(content_width=26, content_height=10, valign=Gtk.Align.CENTER)

        def dessiner(_z, cr, w, h, motif=motif, z=z):
            fg = z.get_color()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.8)
            cr.set_line_width({"fin": 1.0, "tirets": 1.6, "plein": 2.6}[motif])
            if motif == "tirets":
                cr.set_dash([5, 3], 0)
            cr.move_to(1, h / 2)
            cr.line_to(w - 1, h / 2)
            cr.stroke()
        z.set_draw_func(dessiner)
        item.append(z)
        item.append(Gtk.Label(label=libelle, css_classes=["caption"]))
        b.append(item)
    return b


# --- Spectrogramme ---------------------------------------------------------------------------------------------------

def palette(fonce):
    """Table 256 × RGBA : les faibles niveaux sont transparents (le fond du thème reste visible) ; en sombre, des
    tons chauds de plus en plus clairs ; en clair, une encre bleue de plus en plus foncée."""
    x = np.linspace(0, 1, 256)
    if fonce:
        reperes = [(0.0, (40, 20, 90)), (0.35, (120, 40, 140)), (0.6, (220, 70, 90)), (0.8, (250, 150, 50)),
                   (1.0, (255, 240, 170))]
    else:
        reperes = [(0.0, (150, 180, 230)), (0.35, (70, 110, 210)), (0.6, (40, 60, 170)), (0.8, (40, 20, 110)),
                   (1.0, (10, 5, 40))]
    pos = [p for p, _ in reperes]
    rgb = np.stack([np.interp(x, pos, [c[k] for _, c in reperes]) for k in range(3)], 1)
    alpha = np.clip(x / 0.35, 0, 1) ** 1.3 * 255
    return np.concatenate([rgb, alpha[:, None]], 1).astype(np.uint8)


def etirement(valeurs):
    """Table 256 → 256 qui étale les niveaux utiles d'une piste (du 55e au 99,7e centile)."""
    if valeurs.size == 0:
        return np.arange(256, dtype=np.uint8)
    lo, hi = np.percentile(valeurs[::max(1, valeurs.size // 200000)], [55, 99.7])
    hi = max(hi, lo + 10)
    return np.clip((np.arange(256) - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)


class Spectre(Visu):
    """Spectrogramme d'une piste, qui défile avec la lecture (fenêtre de 12 s, curseur au tiers)."""

    def __init__(self, sur_clic=None):
        super().__init__(130, sur_clic, "Spectrogramme qui défile avec la lecture")
        self.donnees = None
        self.dire("Chargement du spectrogramme…")
        self._lut = None
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self._recolorer())

    def definir(self, piste):
        """piste : dict de voixla.analyse (spectre, spectre_fps, spectre_f, duree_s), ou None."""
        if not piste or "spectre" not in piste:
            self.donnees = None
            self.dire("Pas de spectrogramme pour cette piste")
            return
        self.donnees = piste["spectre"]
        self.fps = float(piste["spectre_fps"])
        self.f_bas, self.f_haut = (float(x) for x in piste.get("spectre_f", (50, 8000)))
        self.duree = float(piste["duree_s"])
        self._etire = etirement(self.donnees)
        self._recolorer()
        self.message = ""
        self.queue_draw()

    def _recolorer(self):
        if self.donnees is not None:
            self._lut = palette(sombre())[self._etire]
        self.queue_draw()

    def marge_gauche(self):
        return 34

    def dessiner(self, cr, largeur, hauteur, fg):
        if self.donnees is None or self._lut is None:
            return
        g = self.marge_gauche()
        haut, bas = 2, hauteur - 14
        h, w = bas - haut, largeur - g - 4
        t0, t1 = self.fenetre()
        c0, c1 = int(t0 * self.fps), int(math.ceil(t1 * self.fps))
        tranche = self.donnees[:, max(0, c0):min(self.donnees.shape[1], c1)]
        if tranche.shape[1] > 0:
            rgba = np.ascontiguousarray(self._lut[tranche])
            lignes, cols = tranche.shape
            pix = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(rgba.tobytes()), GdkPixbuf.Colorspace.RGB, True, 8,
                                                  cols, lignes, cols * 4)
            x0 = g + (max(0, c0) / self.fps - t0) / (t1 - t0) * w
            cr.save()
            cr.rectangle(g, haut, w, h)
            cr.clip()
            cr.translate(x0, haut)
            cr.scale((cols / self.fps) / (t1 - t0) * w / cols, h / lignes)
            Gdk.cairo_set_source_pixbuf(cr, pix, 0, 0)
            import cairo
            cr.get_source().set_filter(cairo.FILTER_BILINEAR)
            cr.paint()
            cr.restore()
        # échelle de fréquence (logarithmique)
        for f, nom in ((100, "100"), (250, "250"), (500, "500"), (1000, "1k"), (2000, "2k"), (4000, "4k")):
            if not (self.f_bas < f < self.f_haut):
                continue
            y = bas - math.log(f / self.f_bas) / math.log(self.f_haut / self.f_bas) * h
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
            cr.rectangle(g, y, w, 1)
            cr.fill()
            layout, lt, ht = texte(self, cr, nom, 7.5)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            cr.move_to(g - lt - 5, y - ht / 2)
            PangoCairo.show_layout(cr, layout)
        layout, lt, ht = texte(self, cr, "Hz", 7)
        cr.move_to(g - lt - 5, bas - ht + 2)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.45)
        PangoCairo.show_layout(cr, layout)
        pas_t = 2
        t = math.ceil(t0 / pas_t) * pas_t
        while t <= t1:
            x = g + (t - t0) / (t1 - t0) * w
            layout, lt, ht = texte(self, cr, formats.chrono(t), 7.5)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.5)
            cr.move_to(x - lt / 2, hauteur - ht - 1)
            PangoCairo.show_layout(cr, layout)
            t += pas_t
        x = g + (self.position - t0) / (t1 - t0) * w
        if g <= x <= g + w:
            self.curseur(cr, x, haut, bas, fg)


# --- Forme d'onde ----------------------------------------------------------------------------------------------------

class Onde(Visu):
    """Crêtes de toute la piste (comme la frise), partie déjà lue en couleur d'accent, curseur, clic pour y aller."""

    def __init__(self, sur_clic=None):
        super().__init__(56, sur_clic, "Forme d’onde de la piste")
        self.onde = None
        self._reduite = (None, None)
        self.dire("Chargement de la forme d’onde…")

    def definir(self, piste):
        if not piste or "onde" not in piste:
            self.onde = None
            self.dire("Pas de forme d’onde pour cette piste")
            return
        self.onde = piste["onde"].astype(np.float32) / 127.0
        self.duree = float(piste["duree_s"])
        self._reduite = (None, None)
        self.message = ""
        self.queue_draw()

    def fenetre(self):
        return 0.0, max(self.duree, 1e-3)

    def _reduire(self, colonnes):
        if self._reduite[0] == colonnes:
            return self._reduite[1]
        n = len(self.onde)
        bords = np.linspace(0, n, colonnes + 1).astype(int)
        bords = np.minimum(bords, n - 1) if n else bords
        lo = np.minimum.reduceat(self.onde[:, 0], bords[:-1]) if n else np.zeros(colonnes)
        hi = np.maximum.reduceat(self.onde[:, 1], bords[:-1]) if n else np.zeros(colonnes)
        self._reduite = (colonnes, (lo, hi))
        return lo, hi

    def dessiner(self, cr, largeur, hauteur, fg):
        if self.onde is None or len(self.onde) == 0:
            return
        colonnes = max(1, int(largeur / 2))
        lo, hi = self._reduire(colonnes)
        milieu, ampl = hauteur / 2, hauteur / 2 - 2
        c_accent = accent(self)
        lu = self.position / max(self.duree, 1e-3) * largeur
        for k in range(colonnes):
            x = k * largeur / colonnes
            if x < lu:
                cr.set_source_rgba(*c_accent, 0.9)
            else:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35)
            y0 = milieu - max(0.01, hi[k]) * ampl
            y1 = milieu - min(-0.01, lo[k]) * ampl
            cr.rectangle(x, y0, max(1.0, largeur / colonnes - 0.6), y1 - y0)
            cr.fill()
        self.curseur(cr, lu, 0, hauteur, fg)


# --- Vu-mètre --------------------------------------------------------------------------------------------------------

class VuMetre(Visu):
    """Barres de fréquences (24 bandes, de 60 Hz à 8 kHz) et niveau global, d'après des trames précalculées (20 par
    seconde, interpolées), avec des crêtes qui retombent doucement."""

    def __init__(self):
        super().__init__(64, None, "Vu-mètre : niveau par bande de fréquence")
        self.bandes = None
        self.cretes = None
        self._t_crete = 0.0
        self.dire("Chargement du vu-mètre…")

    def definir(self, piste):
        if not piste or "bandes" not in piste:
            self.bandes = None
            self.dire("Pas de vu-mètre pour cette piste")
            return
        self.bandes = piste["bandes"].astype(np.float32)
        self.niveau = piste["niveau"].astype(np.float32)
        self.fps = float(piste["bandes_fps"])
        self.duree = float(piste["duree_s"])
        # étirement bande par bande : les aigus, moins forts, restent visibles
        if self.bandes.size:
            self.lo = np.percentile(self.bandes, 40, axis=0)
            self.hi = np.maximum(np.percentile(self.bandes, 99.5, axis=0), self.lo + 10)
        else:
            self.lo, self.hi = 0.0, 255.0
        nl = self.niveau[self.niveau > 0]
        self.n_lo, self.n_hi = (np.percentile(nl, [10, 99.5]) if nl.size else (0, 255))
        self.n_hi = max(self.n_hi, self.n_lo + 10)
        self.cretes = np.zeros(self.bandes.shape[1] if self.bandes.ndim == 2 else 24, np.float32)
        self.message = ""
        self.queue_draw()

    def _valeurs(self, t):
        f = t * self.fps
        i = int(f)
        if i >= len(self.bandes) - 1:
            i = max(0, len(self.bandes) - 1)
            return self.bandes[i], self.niveau[i] if len(self.niveau) else 0
        a = f - i
        return (1 - a) * self.bandes[i] + a * self.bandes[i + 1], (1 - a) * self.niveau[i] + a * self.niveau[i + 1]

    def dessiner(self, cr, largeur, hauteur, fg):
        if self.bandes is None or len(self.bandes) == 0:
            return
        b, nv = self._valeurs(self.position)
        v = np.clip((b - self.lo) / (self.hi - self.lo), 0, 1)
        maintenant = time.monotonic()
        dt = min(0.2, maintenant - self._t_crete)
        self._t_crete = maintenant
        self.cretes = np.maximum(v, self.cretes - dt * 0.8)
        nb = len(v)
        jauge = 14
        w = largeur - jauge - 10
        larg = w / nb
        c = accent(self)
        for k in range(nb):
            x = k * larg + 1
            hh = v[k] * (hauteur - 4)
            cr.set_source_rgba(*c, 0.35 + 0.6 * v[k])
            cr.rectangle(x, hauteur - 2 - hh, max(1.0, larg - 2), hh)
            cr.fill()
            yc = hauteur - 2 - self.cretes[k] * (hauteur - 4)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.55)
            cr.rectangle(x, yc - 1, max(1.0, larg - 2), 2)
            cr.fill()
        # niveau global (jauge verticale à droite)
        n = float(np.clip((nv - self.n_lo) / (self.n_hi - self.n_lo), 0, 1))
        x = largeur - jauge
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
        cr.rectangle(x, 2, jauge, hauteur - 4)
        cr.fill()
        teinte = (0.15, 0.64, 0.41) if n < 0.8 else (0.9, 0.65, 0.04) if n < 0.95 else (0.88, 0.11, 0.14)
        cr.set_source_rgba(*teinte, 0.9)
        cr.rectangle(x, hauteur - 2 - n * (hauteur - 4), jauge, n * (hauteur - 4))
        cr.fill()


def disponible():
    return np is not None


def segments(rapport):
    return rapports.segments_frise(rapport)
