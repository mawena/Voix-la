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
    if seg["classe"] == "chanteur":   # trois voix cibles ou plus : la couleur de la voix cible du chanteur (FriseVoix)
        return couleur_voix(int(seg.get("rang") or 1) - 1)
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
            lignes.append(s["voix"] if s["classe"] == "locuteur" else rapports.nom_segment(s))
            if s.get("f0"):
                lignes.append(f"hauteur médiane {formats.nombre(s['f0'], 0)} Hz")
            if s.get("decalage") is not None:
                lignes.append(f"décalage {formats.demi_tons(s['decalage'])} demi-tons")
        if s.get("base"):
            lignes.append(f"({s['base']})")
        tip.set_text("\n".join(lignes))
        return True


# --- Une voix par chanteur : quelle voix cible chante chaque passage ---------------------------------------------------

# sarcelle, orange (la 1re et la 2e voix cible, comme avant), puis violet, vert, rose, bleu : jusqu'à six voix cibles
COULEURS_VOIX = [(0.13, 0.57, 0.64), (0.90, 0.38, 0.0), (0.58, 0.36, 0.80), (0.36, 0.67, 0.17), (0.85, 0.24, 0.50),
                 (0.20, 0.38, 0.85)]


def couleur_voix(i):
    return COULEURS_VOIX[i % len(COULEURS_VOIX)]


class FriseVoix(Gtk.DrawingArea):
    """Bandeau des voix cibles : chaque passage dans la couleur de la voix qui le chante ; deux voix ensemble (voix
    principale et chœurs) : le bandeau partagé, une moitié par voix. Position de lecture, clic pour y aller.

    Corrigeable (definir_morceaux, une voix par chanteur avec les deux versions de chaque passage) : chaque passage du
    rapport est délimité d'un fin trait ; un clic sur l'un d'eux appelle sur_passage(passage, x) (la page ouvre son menu :
    chanté par l'une, par l'autre, les deux, automatique). Les choix en attente (definir_attente) sont hachurés dans la
    couleur choisie ; les passages corrigés à la main ont un repère sous le bandeau."""

    def __init__(self, sur_clic=None, sur_passage=None):
        super().__init__(content_height=36, hexpand=True, has_tooltip=True)
        self.passages, self.noms, self.affichages, self.duree, self.position = [], [], {}, 0.0, None
        self.sur_clic, self.sur_passage = sur_clic, sur_passage
        self.morceaux = []          # passages du rapport (rapports.morceaux_duo)
        self.corrigeable = False    # menu de correction au clic
        self.attente = {}           # numéro de passage → choix en attente (nom, « deux », « auto »)
        self.selection = None       # numéro du passage dont le menu est ouvert
        self.set_draw_func(self._dessiner)
        clic = Gtk.GestureClick()
        clic.connect("pressed", self._clic)
        self.add_controller(clic)
        self.connect("query-tooltip", self._infobulle)
        self.set_cursor_from_name("pointer")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Frise des voix : quelle voix chante chaque passage"])

    def definir(self, passages, noms, duree, affichages=None):
        """passages : rapports.passages_voix ; noms : les voix cibles dans leur ordre (couleurs)."""
        self.passages, self.noms, self.duree = passages or [], list(noms or []), duree or 0.0
        self.affichages = affichages or {}
        if not self.duree:
            self.duree = max((p["fin"] for p in self.passages), default=0.0)
        self.queue_draw()

    def definir_morceaux(self, morceaux, corrigeable=False):
        """morceaux : rapports.morceaux_duo (délimités, repères des corrections) ; corrigeable : menu au clic."""
        self.morceaux, self.corrigeable = list(morceaux or []), bool(corrigeable)
        if not self.duree:
            self.duree = max((m["fin"] for m in self.morceaux), default=0.0)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             ["Frise des voix : quelle voix chante chaque passage" +
                              (" ; un clic sur un passage pour choisir qui le chante" if corrigeable else "")])
        self.queue_draw()

    def definir_attente(self, attente):
        self.attente = dict(attente or {})
        self.queue_draw()

    def selectionner(self, i):
        if self.selection != i:
            self.selection = i
            self.queue_draw()

    def morceau_a(self, t):
        return rapports.morceau_duo_a(self.morceaux, t)

    def morceau(self, i):
        return next((m for m in self.morceaux if m["i"] == i), None)

    def x_de(self, t):
        """Abscisse (pixels) de l'instant t, pour pointer le menu d'un passage."""
        return self._x(t, self.get_width() or 1)

    def voix_du_choix(self, m, choix):
        """Les noms entendus sur le passage m avec ce choix (nom, « deux », « auto »)."""
        if choix == rapports.CHOIX_DEUX:
            return list(self.noms)
        if choix == rapports.CHOIX_AUTO:
            return list(m["voix_auto"])
        return [choix]

    def placer(self, t):
        if t is None or self.position is None or abs((self.position or 0) - t) > 0.05:
            self.position = t
            self.queue_draw()

    def _x(self, t, largeur):
        return 0 if self.duree <= 0 else max(0.0, min(largeur, t / self.duree * largeur))

    def _indice(self, nom):
        return self.noms.index(nom) if nom in self.noms else len(self.noms)

    def _bandes(self, cr, voix, x0, x1, haut, bande, alpha=0.9):
        """Le bandeau d'un passage : une bande par voix entendue (deux voix : une moitié chacune)."""
        for k, nom in enumerate(voix):
            h = bande / len(voix)
            cr.set_source_rgba(*couleur_voix(self._indice(nom)), alpha)
            cr.rectangle(x0, haut + k * h, max(1.0, x1 - x0), h)
            cr.fill()

    def _dessiner(self, _zone, cr, largeur, hauteur):
        fg = self.get_color()
        reperes = bool(self.morceaux)   # place des repères (corrigé à la main, en attente) sous le bandeau
        haut, bande = 3, hauteur - (12 if reperes else 6)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.08)
        cr.rectangle(0, haut, largeur, bande)
        cr.fill()
        if self.duree <= 0:
            return
        for p in self.passages:
            self._bandes(cr, p["voix"] or [], self._x(p["debut"], largeur), self._x(p["fin"], largeur), haut, bande)
        for m in self.morceaux:
            x0, x1 = self._x(m["debut"], largeur), self._x(m["fin"], largeur)
            if m["i"] in self.attente:
                # choix en attente : la couleur choisie, hachurée de blanc (pas encore appliqué)
                self._bandes(cr, self.voix_du_choix(m, self.attente[m["i"]]), x0, x1, haut, bande, 0.95)
                cr.save()
                cr.rectangle(x0, haut, max(1.0, x1 - x0), bande)
                cr.clip()
                cr.set_source_rgba(1, 1, 1, 0.55)
                cr.set_line_width(2)
                x = x0 - bande
                while x < x1:
                    cr.move_to(x, haut + bande)
                    cr.line_to(x + bande, haut)
                    x += 7
                cr.stroke()
                cr.restore()
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
                cr.set_dash([3, 2])
                cr.set_line_width(2)
                cr.move_to(x0 + 1, hauteur - 4)
                cr.line_to(max(x0 + 2, x1 - 1), hauteur - 4)
                cr.stroke()
                cr.set_dash([])
            elif m["corrige"]:
                # corrigé à la main : un trait plein sous le passage, et un coin marqué en haut à gauche
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.85)
                cr.rectangle(x0 + 1, hauteur - 6, max(1.0, x1 - x0 - 2), 4)
                cr.fill()
                cr.move_to(x0, haut)
                cr.line_to(x0 + min(9, max(3.0, x1 - x0)), haut)
                cr.line_to(x0, haut + min(9, bande))
                cr.close_path()
                cr.fill()
        # un fin trait entre les passages : on voit ce qu'on clique
        if self.morceaux:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.45 if self.corrigeable else 0.25)
            bords = sorted({round(self._x(t, largeur)) for m in self.morceaux for t in (m["debut"], m["fin"])})
            for x in bords:
                if 0 < x < largeur:
                    cr.rectangle(x - 0.5, haut, 1, bande)
            cr.fill()
        m = self.morceau(self.selection) if self.selection is not None else None
        if m is not None:
            x0, x1 = self._x(m["debut"], largeur), self._x(m["fin"], largeur)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.95)
            cr.set_line_width(2)
            cr.rectangle(x0 + 1, haut + 1, max(1.0, x1 - x0 - 2), bande - 2)
            cr.stroke()
        if self.position is not None:
            x = self._x(self.position, largeur)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.95)
            cr.rectangle(x - 1, 0, 2, hauteur)
            cr.fill()

    def _t(self, x):
        largeur = self.get_width() or 1
        return max(0.0, min(self.duree, x / largeur * self.duree))

    def _clic(self, _g, _n, x, _y):
        if self.duree <= 0:
            return
        t = self._t(x)
        if self.sur_clic:
            self.sur_clic(t)
        m = self.morceau_a(t) if self.corrigeable else None
        if m is not None and self.sur_passage:
            self.sur_passage(m, x)

    def passage_a(self, t):
        return next((p for p in self.passages if p["debut"] <= t < p["fin"]), None)

    def _nommer(self, noms):
        noms = [self.affichages.get(n, n.capitalize()) for n in noms]
        return formats.joindre(noms) + " ensemble" if len(noms) > 1 else noms[0] if noms else "—"

    def _infobulle(self, _w, x, _y, _clavier, tip):
        if self.duree <= 0:
            return False
        t = self._t(x)
        p, m = self.passage_a(t), self.morceau_a(t)
        if not p and not m:
            return False
        lignes = []
        if m is not None:
            lignes.append(f"Passage {m['i']} · {formats.chrono_precis(m['debut'])} – {formats.chrono_precis(m['fin'])}")
            lignes.append(self._nommer(m["voix"]))
            if m["corrige"]:
                lignes.append(f"corrigé à la main (automatique : {self._nommer(m['voix_auto'])})")
            if m["i"] in self.attente:
                lignes.append(f"choix en attente : {self._nommer(self.voix_du_choix(m, self.attente[m['i']]))}")
            if self.corrigeable:
                lignes.append("un clic : choisir qui chante ce passage" if m["corrigeable"] else
                              "pas de seconde version pour ce passage")
        else:
            lignes += [f"{formats.chrono_precis(p['debut'])} – {formats.chrono_precis(p['fin'])}", self._nommer(p["voix"])]
        tip.set_text("\n".join(lignes))
        return True


def legende_voix(noms, affichages=None):
    """Légende de la frise des voix : une pastille par voix, et « ensemble »."""
    affichages = affichages or {}
    b = Gtk.Box(spacing=14)
    for i, nom in enumerate(noms):
        item = Gtk.Box(spacing=6)
        pastille = Gtk.DrawingArea(content_width=12, content_height=12, valign=Gtk.Align.CENTER)

        def dessiner(_z, cr, w, h, c=couleur_voix(i)):
            cr.set_source_rgba(*c, 0.9)
            cr.arc(w / 2, h / 2, min(w, h) / 2, 0, 6.2832)
            cr.fill()
        pastille.set_draw_func(dessiner)
        item.append(pastille)
        item.append(Gtk.Label(label=affichages.get(nom, nom.capitalize()), css_classes=["caption"]))
        b.append(item)
    if len(noms) > 1:
        item = Gtk.Box(spacing=6)
        pastille = Gtk.DrawingArea(content_width=12, content_height=12, valign=Gtk.Align.CENTER)

        n_bandes = min(len(noms), 3)

        def deux(_z, cr, w, h):
            for k in range(n_bandes):
                cr.set_source_rgba(*couleur_voix(k), 0.9)
                cr.rectangle(0, k * h / n_bandes, w, h / n_bandes)
                cr.fill()
        pastille.set_draw_func(deux)
        item.append(pastille)
        item.append(Gtk.Label(label=("les deux ensemble (voix principale et chœurs)" if len(noms) == 2 else
                                     "plusieurs voix ensemble (voix principale et chœurs) : une bande par voix"),
                              css_classes=["caption"]))
        b.append(item)
    return b


def legende_corrections():
    """Repères de la frise corrigeable : corrigé à la main (trait plein dessous, coin marqué), choix en attente
    (hachures)."""
    b = Gtk.Box(spacing=14)
    for libelle, dessin in (("corrigé à la main", "corrige"), ("choix en attente", "attente")):
        item = Gtk.Box(spacing=6)
        pastille = Gtk.DrawingArea(content_width=16, content_height=12, valign=Gtk.Align.CENTER)

        def repere(z, cr, w, h, dessin=dessin):
            fg = z.get_color()
            if dessin == "corrige":
                cr.set_source_rgba(*couleur_voix(1), 0.9)
                cr.rectangle(0, 0, w, h - 4)
                cr.fill()
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.85)
                cr.rectangle(0, h - 3, w, 3)
                cr.move_to(0, 0)
                cr.line_to(6, 0)
                cr.line_to(0, 6)
                cr.close_path()
                cr.fill()
            else:
                cr.set_source_rgba(*couleur_voix(1), 0.95)
                cr.rectangle(0, 0, w, h - 4)
                cr.fill()
                cr.set_source_rgba(1, 1, 1, 0.6)
                cr.set_line_width(2)
                for x in range(-12, w, 6):
                    cr.move_to(x, h - 4)
                    cr.line_to(x + h - 4, 0)
                cr.stroke()
        pastille.set_draw_func(repere)
        item.append(pastille)
        item.append(Gtk.Label(label=libelle, css_classes=["caption"]))
        b.append(item)
    return b


def legende(segs):
    """Petite légende des couleurs présentes."""
    b = Gtk.Box(spacing=14)
    vues, chanteurs = [], {}
    for s in segs:
        cle = s["voix"] if s["classe"] in ("locuteur", "chanteur") else s["classe"]
        if s["classe"] == "chanteur":
            chanteurs[cle] = s
        if cle not in vues:
            vues.append(cle)
    ordre = (["homme", "femme", "inconnu"] + sorted(v for v in vues if v.startswith("voix "))
             + sorted(chanteurs) + ["muet"])
    for cle in ordre:
        if cle not in vues:
            continue
        item = Gtk.Box(spacing=6)
        pastille = Gtk.DrawingArea(content_width=12, content_height=12, valign=Gtk.Align.CENTER)
        if cle in chanteurs:
            seg = chanteurs[cle]
        else:
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
        item.append(Gtk.Label(label=rapports.nom_segment(chanteurs[cle]) if cle in chanteurs
                              else LIBELLES.get(cle, cle.capitalize()), css_classes=["caption"]))
        b.append(item)
    return b
