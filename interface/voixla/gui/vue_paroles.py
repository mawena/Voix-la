"""Paroles synchronisées sous le lecteur : la ligne en cours (le mot en cours surligné quand doubler donne les mots)
et la suivante, dans la langue d'origine, en français, ou les deux. Un clic sur une ligne va à son début.

Les paroles viennent de travail/<voix>/paroles.json, sinon des sous-titres de la vidéo doublée (voixla.paroles).
Sobriété : le texte n'est refait que quand la ligne ou le mot en cours change ; pendant la lecture, le suivi passe
par l'horloge d'image (25 fois par seconde au plus, comme les visualisations), et s'arrête à la pause.
"""

from gi.repository import GLib, Gtk, Pango

from .. import paroles as P
from .visualisations import Animateur, accent

CHOIX = (("originale", "Originale"), ("fr", "Français"), ("deux", "Les deux"))
SANS_LIGNE = "♪"          # entre deux lignes (passage instrumental)


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in rgb)


def _esc(texte):
    return GLib.markup_escape_text(texte or "")


balisage = P.balisage
legende_source = P.legende_source


class LigneParoles(Gtk.Box):
    """Une ligne cliquable : texte principal et, en mode « les deux », la ligne correspondante de l'autre langue."""

    def __init__(self, classes, sur_clic, lignes_max):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1, css_classes=["paroles-ligne"])
        self.debut = None
        self.principale = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=lignes_max,
                                    ellipsize=Pango.EllipsizeMode.END, css_classes=list(classes))
        self.secondaire = Gtk.Label(xalign=0, lines=1, ellipsize=Pango.EllipsizeMode.END,
                                    css_classes=["paroles-traduction", *[c for c in classes if c != "paroles-courante"]])
        self.append(self.principale)
        self.append(self.secondaire)
        self._lignes_max = lignes_max
        # hauteur fixe (lignes_max lignes) : la page ne saute pas quand une ligne courte suit une longue
        for etiquette, n in ((self.principale, lignes_max), (self.secondaire, 1)):
            etiquette.connect("realize", lambda w, n=n: self._fixer(w, n))
        clic = Gtk.GestureClick()
        clic.connect("pressed", lambda *_: self.debut is not None and sur_clic(self.debut))
        self.add_controller(clic)
        self.set_cursor_from_name("pointer")

    @staticmethod
    def _fixer(etiquette, n):
        _l, h = etiquette.create_pango_layout("Ag").get_pixel_size()
        etiquette.set_size_request(-1, h * n)

    def montrer(self, ligne, balise, seconde=None, avec_seconde=False):
        self.debut = ligne.debut if ligne is not None else None
        self.principale.set_markup(balise if ligne is not None else _esc(SANS_LIGNE))
        self.secondaire.set_visible(avec_seconde)
        self.secondaire.set_label(seconde.texte if seconde is not None else "")
        self.set_tooltip_text(None if ligne is None else "Aller au début de cette ligne")


class VueParoles(Gtk.Box):
    """Bloc « Paroles » : en-tête (source, choix de la langue), ligne en cours, ligne suivante."""

    def __init__(self, sur_clic, langue="deux", sur_langue=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Paroles synchronisées sur la lecture"])
        self.paroles = None
        self.langue = langue if langue in dict(CHOIX) else "deux"
        self.sur_langue = sur_langue
        self.position = 0.0
        self.animateur = None
        self._etat = None
        tete = Gtk.Box(spacing=8)
        tete.append(Gtk.Label(label="Paroles", xalign=0, css_classes=["heading"]))
        self.l_source = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                                  css_classes=["caption", "dim-label"])
        tete.append(self.l_source)
        self.boite_choix = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER, visible=False)
        self.t_choix = {}
        premier = None
        for valeur, libelle in CHOIX:
            t = Gtk.ToggleButton(label=libelle, group=premier, active=valeur == self.langue,
                                 css_classes=["caption"])
            premier = premier or t
            t.connect("toggled", lambda b, v=valeur: b.get_active() and self._choisir(v))
            self.boite_choix.append(t)
            self.t_choix[valeur] = t
        tete.append(self.boite_choix)
        self.append(tete)
        self.courante = LigneParoles(["paroles-courante"], sur_clic, 2)
        self.suivante = LigneParoles(["paroles-suivante"], sur_clic, 1)
        self.append(self.courante)
        self.append(self.suivante)
        self.message = Gtk.Label(xalign=0, wrap=True, css_classes=["caption", "dim-label"], visible=False)
        self.append(self.message)
        self.dire("Recherche des paroles…")
        self.connect("unmap", lambda *_: self.animateur and self.animateur.arreter())

    # --- données ---------------------------------------------------------------------------------------------------

    def suivre(self, fournisseur):
        self.animateur = Animateur(self, fournisseur)

    def definir(self, paroles):
        self.paroles = paroles
        self._etat = None
        self.message.set_visible(False)
        self.courante.set_visible(True)
        self.suivante.set_visible(True)
        self.boite_choix.set_visible(paroles.a_deux_langues())
        self._maj(force=True)

    def dire(self, message):
        self.paroles = None
        self._etat = None
        self.l_source.set_label("")
        self.boite_choix.set_visible(False)
        self.courante.set_visible(False)
        self.suivante.set_visible(False)
        self.message.set_label(message[:1].upper() + message[1:] if message else "")
        self.message.set_visible(bool(message))

    def regler_langue(self, valeur):
        if valeur not in self.t_choix:
            return
        self.langue = valeur
        self.t_choix[valeur].set_active(True)
        self._maj(force=True)

    def _choisir(self, valeur):
        if valeur == self.langue:
            return
        self.langue = valeur
        self._maj(force=True)
        if self.sur_langue:
            self.sur_langue(valeur)

    # --- lecture ---------------------------------------------------------------------------------------------------

    def placer(self, t, lecture=False):
        self.position = float(t or 0.0)
        self._maj()
        if lecture and self.animateur:
            self.animateur.relancer()

    def _maj(self, force=False):
        if self.paroles is None:
            return
        couches = self.paroles.couches(self.langue)
        principale = couches[0]
        seconde = couches[1] if len(couches) > 1 else None
        t = self.position
        i_c, i_s = P.position(principale.lignes, t)
        lc = principale.lignes[i_c] if i_c is not None else None
        ls = principale.lignes[i_s] if i_s is not None else None
        i_mot = P.mot_en_cours(lc.mots, t) if lc is not None and lc.mots else None
        etat = (self.langue, i_c, i_s, i_mot)
        if etat == self._etat and not force:
            return
        self._etat = etat
        if force:
            self.l_source.set_label(legende_source(self.paroles, couches))
        couleur = _hex(accent(self))
        self.courante.montrer(lc, balisage(lc, i_mot, couleur),
                              P.correspondante(seconde.lignes, lc) if seconde is not None and lc else None,
                              seconde is not None)
        self.suivante.montrer(ls, balisage(ls, -1, couleur),
                              P.correspondante(seconde.lignes, ls) if seconde is not None and ls else None,
                              seconde is not None)
