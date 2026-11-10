"""Paroles synchronisées : le karaoké sous le lecteur, le même en surimpression sur la vidéo, et le panneau « Toutes les
paroles ».

- **Karaoke** (Gtk.DrawingArea) : la ligne en cours en grand et centrée, la précédente et la suivante plus petites et
  estompées. Le mot en cours se remplit progressivement à la couleur d'accent, de son début à sa fin (voixla.paroles.
  remplissage) ; les mots déjà chantés restent remplis, ceux à venir sont atténués. Entre deux lignes, la suivante
  prend la place centrale si elle commence bientôt, sinon « ♪ ». Le passage d'une ligne à l'autre glisse en douceur
  (position, taille, opacité). Style « video » : texte blanc à contour sombre, remplissage jaune, fond transparent,
  pour la surimpression sur l'image.
- **VueParoles** : l'en-tête (source, choix de la langue, bouton du panneau) et le karaoké, sous le lecteur.
- **PanneauParoles** : toutes les lignes, la ligne en cours mise en valeur et gardée vers le centre par un défilement
  doux (suspendu quelques secondes quand on fait défiler à la main) ; un clic ou Entrée sur une ligne y va.

Les paroles viennent de travail/<voix>/paroles.json, sinon des sous-titres de la vidéo doublée (voixla.paroles).
placer(t) reçoit la position du lecteur (temps du son doublé, quelle que soit la piste) : voixla.paroles.ecart_lecture
la passe en temps des paroles, et un clic rend une position du lecteur.

Sobriété : les mises en page Pango ne sont refaites que quand la ligne, la langue, la taille ou la largeur changent ;
le karaoké ne se redessine que s'il est affiché, pendant la lecture (25 images par seconde au plus, voir
visualisations.Animateur), et seulement si quelque chose change (un mot qui se remplit, une transition de 0,25 s) ;
le panneau ne suit la lecture que 4 fois par seconde, et ne touche à ses lignes que quand la ligne en cours change.
"""

import math
import time

import cairo
import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from .. import formats  # noqa: E402
from .. import paroles as P  # noqa: E402
from .visualisations import Animateur, accent  # noqa: E402

CHOIX = (("originale", "Originale"), ("fr", "Français"), ("deux", "Les deux"))
SANS_LIGNE = "♪"          # entre deux lignes (passage instrumental)
TAILLES = (("petite", "Petite", 0.8), ("normale", "Normale", 1.0), ("grande", "Grande", 1.3),
           ("tres-grande", "Très grande", 1.65))

# tailles hors des réglages (pas dans TAILLES : les listes de l'interface ne les montrent pas) : « géante », pour les
# paroles de la séance d'enregistrement, lues de loin
TAILLES_HORS_LISTE = {"geante": 2.2}

DUREE_TRANSITION_S = 0.25
IMAGES_TRANSITION_S = 30  # rythme des transitions (bref : 0,25 s)
ECHELLE_VOISINE = 0.7     # taille de la précédente et de la suivante par rapport à la ligne en cours
ECHELLE_TRADUCTION = 0.62 # taille de la traduction par rapport à sa ligne
ALPHA_A_VENIR = 0.55      # mots pas encore chantés de la ligne en cours (style « page »)
JAUNE = (0.965, 0.827, 0.176)     # #f6d32d : remplissage karaoké sur la vidéo
BLANC_VOISIN = (0.86, 0.86, 0.86)
SUSPENSION_MANUELLE_S = 4.0
SUIVI_PANNEAU_MS = 250

balisage = P.balisage
legende_source = P.legende_source

CSS = b"""
.paroles-panneau-liste { background: transparent; }
.paroles-panneau-ligne { border-radius: 8px; margin: 1px 4px; }
.paroles-panneau-ligne.courante { background: alpha(@accent_bg_color, 0.16); }
.paroles-panneau-ligne.courante .paroles-panneau-texte { color: @accent_color; font-weight: 700; }
.paroles-panneau-temps { font-feature-settings: "tnum"; opacity: 0.6; min-width: 2.8em; }
.paroles-panneau-traduction { font-style: italic; opacity: 0.72; }
"""


def facteur_taille(nom):
    """Facteur de la taille du texte des paroles (« petite » … « tres-grande ») ; 1,0 pour un nom inconnu."""
    return next((f for n, _l, f in TAILLES if n == nom), TAILLES_HORS_LISTE.get(nom, 1.0))


def installer_css():
    """Styles des paroles, installés une fois pour tout l'affichage."""
    if getattr(installer_css, "fait", False):
        return
    affichage = Gdk.Display.get_default()
    if affichage is None:
        return
    fournisseur = Gtk.CssProvider()
    fournisseur.load_from_data(CSS, -1)
    Gtk.StyleContext.add_provider_for_display(affichage, fournisseur, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    installer_css.fait = True


def _maintenant():
    return GLib.get_monotonic_time() / 1e6


def _melange(a, b, x):
    return tuple(u + (v - u) * x for u, v in zip(a, b))


def _sans_alpha(rgba, alpha):
    """rgba dont on retire l'opacité alpha de l'ensemble (appliquée au groupe, paint_with_alpha)."""
    return (*rgba[:3], min(1.0, rgba[3] / max(alpha, 1e-3)))


class _Mise:
    """La mise en page d'une ligne (refaite seulement si la ligne, la langue, la taille ou la largeur changent)."""
    __slots__ = ("ligne", "texte", "layout", "trad", "h_texte", "h_trad", "mots")


class Suivi:
    """Comme visualisations.Animateur, en plus lent (GLib.timeout) : pour ce qui n'a besoin que de la ligne en cours."""

    def __init__(self, widget, fournisseur, intervalle_ms=SUIVI_PANNEAU_MS):
        self.widget, self.fournisseur, self.intervalle_ms = widget, fournisseur, intervalle_ms
        self.ident = None

    def relancer(self):
        if self.ident is None and self.widget.get_mapped():
            self.ident = GLib.timeout_add(self.intervalle_ms, self._tic)

    def arreter(self):
        if self.ident is not None:
            GLib.source_remove(self.ident)
            self.ident = None

    def _tic(self):
        if not self.widget.get_mapped():
            self.ident = None
            return False
        t, lecture = self.fournisseur()
        self.widget.placer(t)
        if not lecture:
            self.ident = None
            return False
        return True


# --- Karaoké ---------------------------------------------------------------------------------------------------------

class Karaoke(Gtk.DrawingArea):
    """Les lignes des paroles autour de la position : précédente, en cours (remplie mot à mot), suivante.

    style « page » (sous le lecteur, couleurs du thème) ou « video » (surimpression : blanc à contour sombre,
    remplissage jaune ; precedente=False n'y montre que la ligne en cours et la suivante). sur_clic(position du
    lecteur) : clic sur une ligne ; sans lui, le widget style « video » laisse passer les clics (can_target faux)."""

    def __init__(self, style="page", taille="normale", sur_clic=None, precedente=True, debut_son=None, langue="deux"):
        super().__init__(hexpand=True, css_classes=["paroles-karaoke"])
        installer_css()
        self.style = style if style in ("page", "video") else "page"
        self.taille = taille if taille in TAILLES_HORS_LISTE or any(n == taille for n, _l, _f in TAILLES) else "normale"
        self.sur_clic = sur_clic
        self.precedente = bool(precedente)
        self.debut_son = debut_son
        self.langue = langue if langue in dict(CHOIX) else "deux"
        self.paroles = None
        self.message = ""
        self.position = 0.0        # temps des paroles
        self.animateur = None
        self._fournisseur = None
        self._principale = None
        self._seconde = None
        self._etat = None          # voixla.paroles.voisines : (précédente, focale, suivante, active)
        self._centre = None        # voixla.paroles.centre_karaoke (x,5 : « ♪ » entre deux lignes)
        self._transition = None    # (centre de départ, instant)
        self._tic = None
        self._dernier_tic = 0.0
        self._sig = None           # dernier remplissage dessiné (pour ne redessiner que s'il change)
        self._mises = {}
        self._largeur = None
        self._mesures = None
        self._zones = []           # [(y haut, y bas, indice)] des lignes dessinées, pour le clic
        self._libelle = None
        self.set_draw_func(self._dessiner)
        if sur_clic is not None:
            clic = Gtk.GestureClick()
            clic.connect("pressed", lambda _g, _n, _x, y: self._clic(y))
            self.add_controller(clic)
            self.set_cursor_from_name("pointer")
        elif self.style == "video":
            self.set_can_target(False)
        self.connect("map", lambda *_: self._au_map())
        self.connect("unmap", lambda *_: self._a_l_unmap())
        self.connect("resize", lambda _w, l, _h: self._redimensionne(l))
        self.connect("realize", lambda *_: (self._oublier_mises(), self._maj_hauteur()))
        self._maj_libelle()
        self._maj_hauteur()

    # --- données ---------------------------------------------------------------------------------------------------

    def suivre(self, fournisseur):
        self._fournisseur = fournisseur
        self.animateur = Animateur(self, fournisseur)

    def definir(self, paroles):
        self.paroles = paroles
        self.message = ""
        self._prendre_couches()
        self._oublier_mises()
        self._maj_hauteur()
        self._maj(force=True)
        self.queue_draw()

    def dire(self, message):
        self.paroles = None
        self._principale = self._seconde = None
        self._etat = self._centre = self._transition = None
        self.message = message[:1].upper() + message[1:] if message else ""
        self._oublier_mises()
        self._maj_libelle()
        self.queue_draw()

    def regler_langue(self, valeur):
        if valeur not in dict(CHOIX) or valeur == self.langue:
            return
        self.langue = valeur
        if self.paroles is not None:
            self._prendre_couches()
            self._oublier_mises()
            self._maj_hauteur()
            self._maj(force=True)

    def regler_taille(self, nom):
        nom = nom if nom in TAILLES_HORS_LISTE or any(n == nom for n, _l, _f in TAILLES) else "normale"
        if nom == self.taille:
            return
        self.taille = nom
        self._oublier_mises()
        self._maj_hauteur()
        self.queue_draw()

    def _prendre_couches(self):
        couches = self.paroles.couches(self.langue) if self.paroles is not None else [None]
        self._principale = couches[0]
        self._seconde = couches[1] if len(couches) > 1 else None

    def _oublier_mises(self):
        self._mises = {}
        self._mesures = None

    # --- lecture ---------------------------------------------------------------------------------------------------

    def _ecart(self):
        # le lecteur compte en temps du son doublé, comme une piste sans image (voixla.paroles.ecart_lecture)
        return P.ecart_lecture(self.paroles, False, self.debut_son)

    def placer(self, t, lecture=False):
        """t : la position du lecteur ; gardée en temps des paroles."""
        self.position = float(t or 0.0) + self._ecart()
        self._maj()
        if lecture and self.animateur:
            self.animateur.relancer()

    def _maj(self, force=False):
        if self._principale is None or not self._principale.lignes:
            return
        lignes = self._principale.lignes
        etat = P.voisines(lignes, self.position)
        centre = P.centre_karaoke(lignes, self.position)
        if force or self._centre is None:
            self._centre, self._transition = centre, None
        elif centre != self._centre:
            depart = self._centre_affiche()
            self._centre = centre
            if abs(centre - depart) <= 1.01 and self.get_mapped():
                self._lancer_transition(depart)
            else:
                self._transition = None        # saut de plus d'une ligne : sans animation
        redessiner = force
        if force or etat != self._etat:
            self._etat = etat
            self._maj_libelle()
            redessiner = True
        sig = None
        focale, active = etat[1], etat[3]
        if focale is not None and active and lignes[focale].mots:
            mots = lignes[focale].mots
            i = P.mot_en_cours(mots, self.position)
            if i is not None:
                sig = (focale, i, round(P.fraction_mot(mots[i], self.position) * 60))
        if sig != self._sig:
            self._sig = sig
            redessiner = True
        if redessiner:
            self.queue_draw()

    def _maj_libelle(self):
        """Nom accessible : la ligne en cours (mis à jour seulement quand elle change)."""
        if self._principale is None:
            texte = f"Paroles : {self.message}" if self.message else "Paroles"
        elif self._etat is None or self._etat[1] is None:
            texte = "Paroles : passage instrumental"
        else:
            texte = f"Paroles : {self._principale.lignes[self._etat[1]].texte}"
        if texte != self._libelle:
            self._libelle = texte
            self.update_property([Gtk.AccessibleProperty.LABEL], [texte])

    # --- transitions -----------------------------------------------------------------------------------------------

    def _lancer_transition(self, depart):
        self._transition = (depart, _maintenant())
        if self._tic is None and self.get_mapped():
            self._tic = self.add_tick_callback(self._tic_transition)

    def _tic_transition(self, _w, horloge):
        maintenant = horloge.get_frame_time() / 1e6
        if self._transition is None or maintenant - self._transition[1] >= DUREE_TRANSITION_S:
            self._transition = None
            self._tic = None
            self.queue_draw()
            return GLib.SOURCE_REMOVE
        if maintenant - self._dernier_tic >= 1.0 / IMAGES_TRANSITION_S - 0.002:
            self._dernier_tic = maintenant
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _centre_affiche(self):
        if self._transition is None:
            return self._centre
        depart, t0 = self._transition
        p = (_maintenant() - t0) / DUREE_TRANSITION_S
        if p >= 1.0:
            self._transition = None
            return self._centre
        e = 1.0 - (1.0 - p) ** 3          # ralenti en fin de course
        return depart + (self._centre - depart) * e

    def _au_map(self):
        if self._fournisseur is not None:
            t, lecture = self._fournisseur()
            self.placer(t, lecture)

    def _a_l_unmap(self):
        if self.animateur:
            self.animateur.arreter()
        if self._tic is not None:
            self.remove_tick_callback(self._tic)
            self._tic = None
        self._transition = None

    def _redimensionne(self, largeur):
        if largeur == self._largeur:
            return
        self._largeur = largeur
        self._mises = {}
        if self.style == "video":
            # la taille du texte suit la largeur de l'image : la hauteur voulue aussi (après l'allocation en cours)
            avant = self._mesures
            self._mesures = None
            if avant is None or abs(self._grand_pt() - avant["pt"]) > 0.5:
                GLib.idle_add(lambda: (self._maj_hauteur(), False)[1])
            else:
                self._mesures = avant

    # --- mise en page ----------------------------------------------------------------------------------------------

    def _base_pt(self):
        d = self.get_pango_context().get_font_description()
        s = d.get_size() / Pango.SCALE if d is not None else 0.0
        if d is not None and d.get_size_is_absolute():
            s *= 72.0 / 96.0
        return s if s > 0 else 11.0

    def _grand_pt(self):
        f = facteur_taille(self.taille)
        if self.style == "video":
            largeur = self.get_width() or self._largeur or 900
            return max(13.0, min(44.0, largeur * 0.021)) * f
        return self._base_pt() * 1.9 * f

    def _police(self, pt, gras=True, italique=False):
        d = self.get_pango_context().get_font_description()
        d = d.copy() if d is not None else Pango.FontDescription()
        d.set_size(max(1, int(pt * Pango.SCALE)))
        d.set_weight(Pango.Weight.BOLD if gras else Pango.Weight.NORMAL)
        d.set_style(Pango.Style.ITALIC if italique else Pango.Style.NORMAL)
        return d

    def _mesurer(self):
        if self._mesures is None:
            pt = self._grand_pt()
            lay = self.create_pango_layout("Ag")
            lay.set_font_description(self._police(pt))
            lh = lay.get_pixel_size()[1]
            lay.set_font_description(self._police(pt * ECHELLE_TRADUCTION, gras=False, italique=True))
            lh_trad = lay.get_pixel_size()[1]
            self._mesures = {"pt": pt, "lh": lh, "lh_trad": lh_trad, "ecart": max(2.0, lh * 0.22),
                             "ecart_trad": max(1.0, lh * 0.06), "marge": 8.0 if self.style == "page" else 6.0,
                             "trait": max(2.0, pt * 1.333 * 0.13)}
        return self._mesures

    def _avec_traduction(self):
        return self.langue == "deux" and self.paroles is not None and self.paroles.a_deux_langues()

    def _maj_hauteur(self):
        """Hauteur fixe selon la taille du texte (et la traduction) : la page ne saute pas d'une ligne à l'autre."""
        m = self._mesurer()
        lh, e = m["lh"], m["ecart"]
        trad = (m["lh_trad"] + m["ecart_trad"]) if self._avec_traduction() else 0.0
        if self.style == "video":
            h = 2 * m["marge"] + 2 * lh + trad + e + lh * ECHELLE_VOISINE
        else:
            h = 2 * m["marge"] + 2 * lh + trad + 2 * (e + (lh + trad) * ECHELLE_VOISINE)
        h = int(math.ceil(h))
        if h != self.get_content_height():
            self.set_content_height(h)

    def _mise(self, i, largeur):
        m = self._mises.get(i)
        if m is not None:
            return m
        mes = self._mesurer()
        ligne = self._principale.lignes[i]
        texte, mots = P.portees(ligne)
        m = _Mise()
        m.ligne, m.texte = ligne, texte
        utile = int(max(20.0, largeur - 2 * mes["marge"]) * Pango.SCALE)
        m.layout = self.create_pango_layout(texte)
        m.layout.set_font_description(self._police(mes["pt"]))
        m.layout.set_width(utile)
        m.layout.set_wrap(Pango.WrapMode.WORD)
        m.layout.set_alignment(Pango.Alignment.CENTER)
        m.layout.set_height(-3)
        m.layout.set_ellipsize(Pango.EllipsizeMode.END)
        m.h_texte = m.layout.get_pixel_size()[1]
        m.trad, m.h_trad = None, 0
        if self._seconde is not None:
            c = P.correspondante(self._seconde.lignes, ligne)
            if c is not None and c.texte:
                m.trad = self.create_pango_layout(c.texte)
                m.trad.set_font_description(self._police(mes["pt"] * ECHELLE_TRADUCTION, gras=False,
                                                         italique=True))
                m.trad.set_width(utile)
                m.trad.set_wrap(Pango.WrapMode.WORD)
                m.trad.set_alignment(Pango.Alignment.CENTER)
                m.trad.set_height(-2)
                m.trad.set_ellipsize(Pango.EllipsizeMode.END)
                m.h_trad = m.trad.get_pixel_size()[1]

        def octet(k):
            return len(texte[:k].encode("utf-8"))
        # (octet du début, octet du dernier caractère, octet du début étendu, octet du dernier caractère étendu,
        # indice du mot) : la plage du mot, et celle qu'on colore une fois le mot chanté (ponctuation qui suit)
        m.mots = []
        for k, (d, f, s, i_mot) in enumerate(mots):
            debut_etendu = 0 if k == 0 else d
            m.mots.append((octet(d), octet(max(d, f - 1)), octet(debut_etendu), octet(max(debut_etendu, s - 1)),
                           i_mot))
        self._mises[i] = m
        return m

    @staticmethod
    def _rects(lay, b0, b1, fraction=1.0):
        """Rectangles (unités Pango) de la plage d'octets [b0, b1] (b1 : début du dernier caractère), coupée à
        fraction de sa largeur. Une plage sur deux lignes (mot coupé) : la fin de la première puis le début de la
        seconde, une fois la plage entière."""
        r0, r1 = lay.index_to_pos(b0), lay.index_to_pos(b1)
        x0, x1 = min(r0.x, r0.x + r0.width), max(r1.x, r1.x + r1.width)
        if r0.y == r1.y:
            return [(x0, r0.y, max(0, x1 - x0) * fraction, r0.height)]
        grand = 1 << 28
        if fraction < 1.0:
            return [(x0, r0.y, max(0, lay.get_width() - x0) * fraction, r0.height)]
        return [(x0, r0.y, grand, r0.height), (-grand, r1.y, x1 + grand, r1.height)]

    # --- dessin ----------------------------------------------------------------------------------------------------

    def _dessiner(self, _z, cr, largeur, hauteur):
        fg = self.get_color()
        texte = (fg.red, fg.green, fg.blue)
        self._zones = []
        if self._principale is None or not self._principale.lignes:
            if self.message and self.style == "page":
                lay = self.create_pango_layout(self.message)
                lay.set_width(int(max(20, largeur - 16) * Pango.SCALE))
                lay.set_wrap(Pango.WrapMode.WORD)
                lay.set_alignment(Pango.Alignment.CENTER)
                _l, h = lay.get_pixel_size()
                cr.set_source_rgba(*texte, 0.6)
                cr.move_to(8, (hauteur - h) / 2)
                PangoCairo.show_layout(cr, lay)
            return
        if largeur != self._largeur:
            self._largeur = largeur
            self._mises = {}
        mes = self._mesurer()
        lignes = self._principale.lignes
        n = len(lignes)
        c = self._centre_affiche()
        if c is None:
            return
        # éléments du rail : les lignes autour du centre, et « ♪ » aux demi-places (centre d'arrivée et de départ)
        notes = {x for x in (self._centre, self._transition[0] if self._transition else None)
                 if x is not None and abs(x - round(x)) > 0.25}
        elements = []
        for i in range(max(0, math.floor(c) - 2), min(n, math.ceil(c) + 3)):
            r = i - c
            if abs(r) <= 2.2 and (self.precedente or r > -1.0):
                elements.append((float(i), r, i))
        for q in notes:
            r = q - c
            vis = max(0.0, 1.0 - 2.0 * abs(r))
            if vis > 0:
                elements.append((q, r, None))
        elements.sort(key=lambda e: e[0])
        lh, ecart = mes["lh"], mes["ecart"]
        rail = []           # (clé, y haut, hauteur, élément)
        y = 0.0
        for cle, r, i in elements:
            petit = min(1.0, 2.0 * abs(r))
            if i is None:
                vis = max(0.0, 1.0 - 2.0 * abs(r))
                h, g = lh * vis, ecart * vis
                donnees = ("note", vis, petit)
            else:
                m = self._mise(i, largeur)
                ech = 1.0 - (1.0 - ECHELLE_VOISINE) * petit
                # la place de la traduction reste réservée même quand elle s'efface (vidéo : sous la seule ligne en
                # cours) : rien ne se chevauche pendant une transition
                brut = m.h_texte + ((mes["ecart_trad"] + m.h_trad) if m.trad else 0.0)
                h, g = brut * ech, ecart
                donnees = ("ligne", m, ech, petit, r)
            rail.append((cle, y, h, donnees))
            y += h + g
        if not rail:
            return
        # le centre du rail (interpolé entre les deux éléments qui l'encadrent) se place au point d'ancrage
        milieux = [(cle, y0 + h / 2) for cle, y0, h, _d in rail]
        avant = [x for x in milieux if x[0] <= c + 1e-9]
        apres = [x for x in milieux if x[0] >= c - 1e-9]
        if avant and apres and apres[0][0] > avant[-1][0]:
            (ka, ma), (kb, mb) = avant[-1], apres[0]
            point = ma + (mb - ma) * (c - ka) / (kb - ka)
        else:
            point = (avant[-1] if avant else apres[0])[1]
        if self.style == "video":
            ancre = mes["marge"] + lh
        else:
            ancre = hauteur / 2
        decalage = ancre - point
        focale, active = (self._etat[1], self._etat[3]) if self._etat else (None, False)
        for _cle, y0, h, d in rail:
            if d[0] == "note":
                self._dessiner_note(cr, largeur, y0 + decalage, h, d[1], texte)
                continue
            _s, m, ech, petit, r = d
            alpha = 1.0
            if self.style == "page":
                alpha = 1.0 - 0.5 * petit
            if abs(r) > 1.0:
                alpha *= max(0.0, 1.0 - (abs(r) - 1.0) / 0.5)     # au-delà des voisines : invisible à 1,5
            if not self.precedente and r < 0:
                alpha *= max(0.0, 1.0 + 2.0 * r)        # la ligne finie s'efface en montant
            if alpha <= 0.01:
                continue
            i = int(round(_cle))
            if focale is not None:
                passee = i < focale
            else:
                passee = self._etat is not None and self._etat[0] is not None and i <= self._etat[0]
            en_cours = i == focale and active
            self._dessiner_ligne(cr, largeur, y0 + decalage, m, ech, petit, alpha, passee, en_cours, texte)
            if alpha > 0.25:
                self._zones.append((y0 + decalage, y0 + decalage + h, i))

    def _couleurs(self, texte, petit, alpha, chantee):
        """(couleur des mots à venir, couleur des mots chantés, couleur de la traduction), en rgba."""
        if self.style == "video":
            base = _melange((1.0, 1.0, 1.0), BLANC_VOISIN, petit)
            rempli = _melange(JAUNE, BLANC_VOISIN, petit)
            return (*base, alpha), (*rempli, alpha), (0.94, 0.94, 0.94, alpha * (1.0 - petit))
        a_venir = alpha * (1.0 if chantee else ALPHA_A_VENIR + (1.0 - ALPHA_A_VENIR) * petit)
        rempli = _melange(accent(self), texte, petit)
        return (*texte, a_venir), (*rempli, alpha), (*texte, alpha * 0.72)

    def _dessiner_ligne(self, cr, largeur, y, m, ech, petit, alpha, passee, en_cours, texte):
        mes = self._mesurer()
        sans_mots = not m.mots
        base, rempli, trad = self._couleurs(texte, petit, alpha, chantee=sans_mots and (passee or en_cours))
        rects = []
        if not sans_mots and (passee or en_cours):
            for b0, b1, e0, e1, i_mot in m.mots:
                fr = 1.0 if passee else P.fraction_mot(m.ligne.mots[i_mot], self.position)
                if fr >= 1.0:
                    rects += self._rects(m.layout, e0, e1)
                elif fr > 0.0:
                    rects += self._rects(m.layout, b0, b1, fr)
        groupe = alpha < 0.999 and (self.style == "video" or rects)
        cr.save()
        cr.translate(largeur / 2, y)
        cr.scale(ech, ech)
        cr.translate(-largeur / 2 + mes["marge"], 0)
        if groupe:
            # opacité de l'ensemble (contour, texte, remplissage) sans que les couches se voient l'une sous l'autre
            cr.push_group()
            base, rempli, trad = (_sans_alpha(c, alpha) for c in (base, rempli, trad))
        self._couche(cr, m.layout, base, rempli, rects)
        if m.trad is not None and trad[3] > 0.01:
            cr.translate(0, m.h_texte + mes["ecart_trad"])
            self._couche(cr, m.trad, trad, None, None)
        if groupe:
            cr.pop_group_to_source()
            cr.paint_with_alpha(alpha)
        cr.restore()

    def _couche(self, cr, lay, base, rempli, rects):
        if self.style == "video":
            mes = self._mesurer()
            cr.set_line_join(cairo.LineJoin.ROUND)
            cr.move_to(0, 0)
            PangoCairo.layout_path(cr, lay)
            cr.set_source_rgba(0, 0, 0, 0.3 * base[3])
            cr.set_line_width(mes["trait"] * 2.2)
            cr.stroke_preserve()
            cr.set_source_rgba(0, 0, 0, 0.85 * base[3])
            cr.set_line_width(mes["trait"])
            cr.stroke()
        cr.set_source_rgba(*base)
        cr.move_to(0, 0)
        PangoCairo.show_layout(cr, lay)
        if rects:
            cr.save()
            for x, y, l, h in rects:
                cr.rectangle(x / Pango.SCALE, y / Pango.SCALE - 2, l / Pango.SCALE, h / Pango.SCALE + 4)
            cr.clip()
            cr.set_source_rgba(*rempli)
            cr.move_to(0, 0)
            PangoCairo.show_layout(cr, lay)
            cr.restore()

    def _dessiner_note(self, cr, largeur, y, h, vis, texte):
        if vis <= 0.01 or h <= 1:
            return
        mes = self._mesurer()
        lay = self.create_pango_layout(SANS_LIGNE)
        lay.set_font_description(self._police(mes["pt"]))
        l, hl = lay.get_pixel_size()
        cr.save()
        cr.translate(largeur / 2, y + h / 2)
        cr.scale(vis, vis)
        cr.translate(-l / 2, -hl / 2)
        if self.style == "video":
            self._couche(cr, lay, (1.0, 1.0, 1.0, vis), None, None)
        else:
            cr.set_source_rgba(*texte, 0.5 * vis)
            cr.move_to(0, 0)
            PangoCairo.show_layout(cr, lay)
        cr.restore()

    def _clic(self, y):
        for haut, bas, i in self._zones:
            if haut <= y <= bas and self._principale is not None and i < len(self._principale.lignes):
                self.sur_clic(max(0.0, self._principale.lignes[i].debut - self._ecart()))
                return


# --- Sous le lecteur -------------------------------------------------------------------------------------------------

class VueParoles(Gtk.Box):
    """Bloc « Paroles » : en-tête (source, choix de la langue, panneau « Toutes les paroles »), puis le karaoké.

    placer() reçoit la position du lecteur, en temps du son doublé quelle que soit la piste (voixla.pistes.
    source_lecteur) ; debut_son : où le son doublé commence dans la vidéo doublée, pour les sous-titres, en temps de la
    vidéo. voixla.paroles.ecart_lecture passe de ce temps à celui des paroles, et le clic sur une ligne rend une
    position du lecteur."""

    def __init__(self, sur_clic, langue="deux", sur_langue=None, debut_son=None, taille="normale"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        installer_css()
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Paroles synchronisées sur la lecture"])
        self.sur_clic = sur_clic
        self.debut_son = debut_son
        self.paroles = None
        self.langue = langue if langue in dict(CHOIX) else "deux"
        self.sur_langue = sur_langue
        self.position = 0.0
        self._t = 0.0              # dernière position du lecteur reçue (l'écart change avec les paroles)
        self.animateur = None
        self._fournisseur = None
        tete = Gtk.Box(spacing=8)
        tete.append(Gtk.Label(label="Paroles", xalign=0, css_classes=["heading"]))
        self.l_source = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                                  css_classes=["caption", "dim-label"])
        tete.append(self.l_source)
        self.boite_choix = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER, visible=False)
        self.boite_choix.update_property([Gtk.AccessibleProperty.LABEL], ["Langue des paroles"])
        self.t_choix = {}
        premier = None
        for valeur, libelle in CHOIX:
            t = Gtk.ToggleButton(label=libelle, group=premier, active=valeur == self.langue,
                                 css_classes=["caption"], focus_on_click=False)
            premier = premier or t
            t.connect("toggled", lambda b, v=valeur: b.get_active() and self._choisir(v))
            self.boite_choix.append(t)
            self.t_choix[valeur] = t
        tete.append(self.boite_choix)
        self.b_panneau = Gtk.ToggleButton(icon_name="view-list-symbolic", action_name="win.paroles-panneau",
                                          css_classes=["flat"], valign=Gtk.Align.CENTER, focus_on_click=False,
                                          tooltip_text="Toutes les paroles (P)")
        self.b_panneau.update_property([Gtk.AccessibleProperty.LABEL], ["Toutes les paroles"])
        tete.append(self.b_panneau)
        self.append(tete)
        self.karaoke = Karaoke("page", taille, sur_clic=sur_clic, debut_son=debut_son, langue=self.langue)
        self.append(self.karaoke)
        self.message = Gtk.Label(xalign=0, wrap=True, css_classes=["caption", "dim-label"], visible=False)
        self.append(self.message)
        self.dire("Recherche des paroles…")
        self.connect("unmap", lambda *_: self.animateur and self.animateur.arreter())
        self.connect("map", lambda *_: self._au_map())

    @property
    def taille(self):
        return self.karaoke.taille

    # --- données ---------------------------------------------------------------------------------------------------

    def suivre(self, fournisseur):
        self._fournisseur = fournisseur
        self.animateur = Animateur(self, fournisseur)

    def definir(self, paroles):
        self.paroles = paroles
        self.message.set_visible(False)
        self.karaoke.set_visible(True)
        self.boite_choix.set_visible(paroles.a_deux_langues())
        self.l_source.set_label(legende_source(paroles, paroles.couches(self.langue)))
        self.karaoke.definir(paroles)
        self.placer(self._t)

    def dire(self, message):
        self.paroles = None
        self.l_source.set_label("")
        self.boite_choix.set_visible(False)
        self.karaoke.set_visible(False)
        self.karaoke.dire("")
        self.message.set_label(message[:1].upper() + message[1:] if message else "")
        self.message.set_visible(bool(message))

    def regler_langue(self, valeur):
        if valeur not in self.t_choix:
            return
        self.langue = valeur
        self.t_choix[valeur].set_active(True)
        self._appliquer_langue()

    def regler_taille(self, nom):
        self.karaoke.regler_taille(nom)

    def _choisir(self, valeur):
        if valeur == self.langue:
            return
        self.langue = valeur
        self._appliquer_langue()
        if self.sur_langue:
            self.sur_langue(valeur)

    def _appliquer_langue(self):
        self.karaoke.regler_langue(self.langue)
        if self.paroles is not None:
            self.l_source.set_label(legende_source(self.paroles, self.paroles.couches(self.langue)))

    # --- lecture ---------------------------------------------------------------------------------------------------

    def _ecart(self):
        # le lecteur compte en temps du son doublé, comme une piste sans image
        return P.ecart_lecture(self.paroles, False, self.debut_son)

    def placer(self, t, lecture=False):
        """t : la position du lecteur ; gardée en temps des paroles."""
        self._t = float(t or 0.0)
        self.position = self._t + self._ecart()
        self.karaoke.placer(t)
        if lecture and self.animateur:
            self.animateur.relancer()

    def _au_map(self):
        if self._fournisseur is not None:
            t, lecture = self._fournisseur()
            self.placer(t, lecture)


# --- Panneau « Toutes les paroles » ----------------------------------------------------------------------------------

class PanneauParoles(Gtk.Box):
    """Toutes les lignes des paroles, la ligne en cours mise en valeur et gardée vers le centre ; un clic (ou Entrée)
    sur une ligne y va. Les lignes ne sont construites que quand le panneau est affiché."""

    def __init__(self, sur_clic, langue="deux", debut_son=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["paroles-panneau"])
        installer_css()
        self.set_size_request(220, -1)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Toutes les paroles"])
        self.sur_clic = sur_clic
        self.debut_son = debut_son
        self.langue = langue if langue in dict(CHOIX) else "deux"
        self.paroles = None
        self.position = 0.0
        self._t = 0.0
        self.animateur = None
        self._fournisseur = None
        self.rangees = []
        self._a_construire = False
        self._courante = None
        self._cible = None
        self._a_centrer = False
        self._manuel_jusqua = 0.0
        self._auto = False
        self._anim = None
        tete = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=6)
        tete.append(Gtk.Label(label="Toutes les paroles", xalign=0, hexpand=True, css_classes=["heading"]))
        fermer = Gtk.Button(icon_name="window-close-symbolic", action_name="win.paroles-panneau",
                            css_classes=["flat", "circular"], valign=Gtk.Align.CENTER,
                            tooltip_text="Fermer le panneau (P)")
        fermer.update_property([Gtk.AccessibleProperty.LABEL], ["Fermer le panneau « Toutes les paroles »"])
        tete.append(fermer)
        self.append(tete)
        self.liste = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["paroles-panneau-liste"])
        self.liste.update_property([Gtk.AccessibleProperty.LABEL], ["Lignes des paroles"])
        self.liste.connect("row-activated", lambda _l, r: self._aller(r))
        self.defile = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self.liste)
        self.message = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, valign=Gtk.Align.CENTER,
                                 css_classes=["dim-label"], margin_start=12, margin_end=12)
        self.pile = Gtk.Stack(vexpand=True)
        self.pile.add_named(self.defile, "liste")
        self.pile.add_named(self.message, "message")
        self.append(self.pile)
        adj = self.defile.get_vadjustment()
        adj.connect("value-changed", lambda *_: self._defile_a_la_main())
        roulette = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        roulette.connect("scroll", lambda *_: (self._suspendre(), False)[1])
        self.defile.add_controller(roulette)
        self.connect("map", lambda *_: self._au_map())
        self.connect("unmap", lambda *_: self.animateur and self.animateur.arreter())
        self.dire("Recherche des paroles…")

    # --- données ---------------------------------------------------------------------------------------------------

    def suivre(self, fournisseur):
        self._fournisseur = fournisseur
        self.animateur = Suivi(self, fournisseur)

    def definir(self, paroles):
        self.paroles = paroles
        self.pile.set_visible_child_name("liste")
        self._a_construire = True
        if self.get_mapped():
            self._construire()

    def dire(self, message):
        self.paroles = None
        self._vider()
        self.message.set_label(message[:1].upper() + message[1:] if message else "Pas de paroles")
        self.pile.set_visible_child_name("message")

    def regler_langue(self, valeur):
        if valeur not in dict(CHOIX) or valeur == self.langue:
            return
        self.langue = valeur
        if self.paroles is not None:
            self._a_construire = True
            if self.get_mapped():
                self._construire()

    def _ecart(self):
        return P.ecart_lecture(self.paroles, False, self.debut_son)

    def _vider(self):
        self.liste.remove_all()
        self.rangees = []
        self._courante = self._cible = None

    def _construire(self):
        """Une rangée par ligne (temps, texte, traduction) : seulement à l'affichage, une fois par paroles et langue."""
        self._a_construire = False
        self._vider()
        if self.paroles is None:
            return
        couches = self.paroles.couches(self.langue)
        principale = couches[0]
        seconde = couches[1] if len(couches) > 1 else None
        ecart = self._ecart()
        if not principale.lignes:
            self.dire("Pas de paroles")
            return
        for i, l in enumerate(principale.lignes):
            temps = formats.chrono(max(0.0, l.debut - ecart))
            boite = Gtk.Box(spacing=10, margin_top=5, margin_bottom=5, margin_start=8, margin_end=8)
            boite.append(Gtk.Label(label=temps, xalign=0, valign=Gtk.Align.START,
                                   css_classes=["caption", "paroles-panneau-temps"]))
            colonne = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
            colonne.append(Gtk.Label(label=l.texte, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                     css_classes=["paroles-panneau-texte"]))
            trad = P.correspondante(seconde.lignes, l) if seconde is not None else None
            if trad is not None and trad.texte:
                colonne.append(Gtk.Label(label=trad.texte, xalign=0, wrap=True,
                                         wrap_mode=Pango.WrapMode.WORD_CHAR,
                                         css_classes=["caption", "paroles-panneau-traduction"]))
            boite.append(colonne)
            r = Gtk.ListBoxRow(child=boite, activatable=True, css_classes=["paroles-panneau-ligne"])
            r.indice = i
            r.update_property([Gtk.AccessibleProperty.LABEL],
                              [f"{temps} : {l.texte}" + (f" ({trad.texte})" if trad is not None and trad.texte
                                                          else "")])
            r.update_property([Gtk.AccessibleProperty.DESCRIPTION], ["Aller à cette ligne"])
            self.liste.append(r)
            self.rangees.append(r)
        self.placer(self._t)
        self._a_centrer = True
        self._centrer_bientot(doux=False)

    # --- lecture ---------------------------------------------------------------------------------------------------

    def placer(self, t, lecture=False):
        """t : la position du lecteur ; gardée en temps des paroles."""
        self._t = float(t or 0.0)
        self.position = self._t + self._ecart()
        if lecture and self.animateur:
            self.animateur.relancer()
        if self.paroles is None or not self.rangees:
            return
        lignes = self.paroles.couches(self.langue)[0].lignes
        courante, suivante = P.position(lignes, self.position)
        if courante != self._courante:
            if self._courante is not None and self._courante < len(self.rangees):
                self.rangees[self._courante].remove_css_class("courante")
            if courante is not None:
                self.rangees[courante].add_css_class("courante")
            self._courante = courante
        cible = courante if courante is not None else (suivante if suivante is not None else len(lignes) - 1)
        if cible != self._cible:
            self._cible = cible
            self._a_centrer = True
        if self._a_centrer and time.monotonic() >= self._manuel_jusqua:
            self._a_centrer = False
            self._centrer(cible)

    def _au_map(self):
        if self._a_construire:
            self._construire()
        if self._fournisseur is not None:
            t, lecture = self._fournisseur()
            self.placer(t, lecture)
        self._a_centrer = True
        self._centrer_bientot(doux=False)

    def _centrer_bientot(self, doux=True):
        """Centre la ligne visée après la mise en page (les rangées viennent d'être construites ou affichées)."""
        def une_fois(*_):
            if self._cible is not None and self._a_centrer:
                self._a_centrer = False
                self._centrer(self._cible, doux)
            return GLib.SOURCE_REMOVE
        self.add_tick_callback(une_fois)

    def _centrer(self, i, doux=True):
        if i is None or not 0 <= i < len(self.rangees) or not self.get_mapped():
            self._a_centrer = True
            return
        ok, rect = self.rangees[i].compute_bounds(self.liste)
        if not ok or rect.get_height() <= 0:
            self._a_centrer = True
            return
        adj = self.defile.get_vadjustment()
        cible = rect.get_y() + rect.get_height() / 2 - adj.get_page_size() / 2
        cible = max(adj.get_lower(), min(cible, adj.get_upper() - adj.get_page_size()))
        if abs(cible - adj.get_value()) < 1:
            return
        if self._anim is not None:
            self._anim.skip()
        if not doux:
            self._auto = True
            adj.set_value(cible)
            self._auto = False
            return
        self._auto = True
        self._anim = Adw.TimedAnimation(widget=self.defile, value_from=adj.get_value(), value_to=cible, duration=350,
                                        target=Adw.PropertyAnimationTarget.new(adj, "value"),
                                        easing=Adw.Easing.EASE_OUT_CUBIC)
        self._anim.connect("done", lambda *_: self._fin_animation())
        self._anim.play()

    def _fin_animation(self):
        self._auto = False
        self._anim = None

    def _defile_a_la_main(self):
        if not self._auto:
            self._manuel_jusqua = time.monotonic() + SUSPENSION_MANUELLE_S

    def _suspendre(self):
        """La roulette ou le pavé tactile : on laisse la main quelques secondes."""
        if self._anim is not None:
            self._anim.pause()
            self._fin_animation()
        self._manuel_jusqua = time.monotonic() + SUSPENSION_MANUELLE_S

    def _aller(self, rangee):
        if self.paroles is None:
            return
        lignes = self.paroles.couches(self.langue)[0].lignes
        i = getattr(rangee, "indice", None)
        if i is None or not 0 <= i < len(lignes):
            return
        self._manuel_jusqua = 0.0          # on a choisi une ligne : le panneau la suit de nouveau
        self.sur_clic(max(0.0, lignes[i].debut - self._ecart()))
