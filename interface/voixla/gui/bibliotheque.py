"""Page Résultats : la bibliothèque des doublages, en grille de cartes ou en liste triable par colonne.

- **Modèle** : un Gio.ListStore d'entrées (voixla.bibliotheque : une version, ou les versions d'une même chanson),
  filtré (Gtk.FilterListModel : recherche, voix, chant ou parole, auto-tune, duo, extrait ou entier) puis trié
  (Gtk.SortListModel, par le trieur des colonnes de la liste, que le menu de tri pilote aussi). Les deux vues lisent le
  même modèle, au travers d'un Gtk.NoSelection, ou d'un Gtk.MultiSelection en mode sélection. Elles sont virtualisées :
  seules les cartes ou les lignes visibles existent, et leurs images se chargent au fil du défilement (gui/vignettes.py).
- **Carte** : la miniature 16:9 (ou une pochette générée), le titre sur deux lignes au plus, les voix, la durée, la
  date et des badges (auto-tune, duo, extrait, langue des paroles, parole). Le bouton de lecture (au survol, ou toujours
  sur écran tactile) lance la lecture dans le lecteur de toute l'application, sans ouvrir le détail : la page du détail
  est créée sans être montrée, et lit le son seul tant qu'on ne la regarde pas (comme fenêtre fermée). Un clic sur la
  carte ouvre le détail (gui/page_resultats.PageDetail). Les versions d'une même chanson : une seule carte « 2 versions »,
  avec une pastille par voix qui ouvre sa version ; « Regrouper les versions » (menu de tri) les déplie.
- **Rien de l'ancienne liste ne se perd** : menu « ⋮ » (ouvrir le dossier, mettre à la corbeille), mode sélection
  (corbeille de plusieurs doublages, Échap pour sortir), F5, liste relue quand un doublage finit, « Voir le résultat »
  depuis les Travaux, scénarios des captures.
- Le choix de la vue, du tri et du regroupement est gardé dans interface.json (clé « bibliotheque »).
"""

import functools
import time
import weakref
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from .. import bibliotheque as bib
from .. import chemins, corbeille, formats, rapports
from .. import lecture as regles
from . import commun
from .lecture import lectures
from .page_resultats import PageDetail
from .vignettes import Image, miniatures

CSS = b"""
.bibliotheque-grille { background: transparent; padding: 10px 12px 24px 12px; }
.bibliotheque-grille > child { padding: 7px; border-radius: 16px; }
.carte-doublage { min-width: 176px; }
.bibliotheque-grille.etroite .carte-doublage { min-width: 136px; }
.carte-titre { font-weight: 700; }
.vignette-note { color: rgba(255, 255, 255, 0.9); -gtk-icon-shadow: 0 1px 4px rgba(0, 0, 0, 0.3); }
.vignette-pastille { background: rgba(0, 0, 0, 0.64); color: white; border-radius: 6px; padding: 1px 6px;
  font-size: 0.78em; font-weight: 700; }
.badge-image { font-size: 0.7em; font-weight: 800; padding: 1px 7px; border-radius: 99px;
  background: rgba(0, 0, 0, 0.64); color: white; }
.badge-image.accent { background: alpha(@accent_bg_color, 0.94); color: @accent_fg_color; }
.badge-image.alerte { background: alpha(@warning_bg_color, 0.96); color: @warning_fg_color; }
.lecture-rapide { min-width: 38px; min-height: 38px; padding: 0; opacity: 0; transition: opacity 120ms ease-out; }
.bibliotheque-grille > child:hover .lecture-rapide, .bibliotheque-grille > child:focus-within .lecture-rapide,
.tactile .lecture-rapide, .lecture-rapide.en-cours, .survol .lecture-rapide { opacity: 1; }
.pastille-voix { padding: 0 9px; min-height: 22px; border-radius: 99px; font-size: 0.82em; font-weight: 600;
  background: alpha(@accent_bg_color, 0.13); color: @accent_color; }
.pastille-voix:hover { background: alpha(@accent_bg_color, 0.24); }
.pastille-voix.en-cours { background: @accent_bg_color; color: @accent_fg_color; }
.puce-filtre { padding: 0 4px 0 10px; min-height: 26px; font-size: 0.9em; }
.filtre-actif { color: @accent_color; }
.bibliotheque-liste .vignette-note { color: rgba(255, 255, 255, 0.9); }
.ligne-titre { font-weight: 600; }
"""

GENRES = list(bib.GENRES.items())
LONGUEURS = list(bib.LONGUEURS.items())
MAX_PASTILLES = 3
PAQUET = 24          # entrées ajoutées au modèle par passage de la boucle (premier chargement, regroupement)


def _installer_css():
    if getattr(_installer_css, "fait", False):
        return
    fournisseur = Gtk.CssProvider()
    fournisseur.load_from_data(CSS, -1)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), fournisseur,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _installer_css.fait = True


class Element(GObject.Object):
    """Un élément du modèle : une entrée (voixla.bibliotheque.Entree). en_lecture : une de ses versions joue."""

    __gtype_name__ = "VoixlaElementBibliotheque"
    en_lecture = GObject.Property(type=bool, default=False)

    def __init__(self, entree):
        super().__init__()
        self.entree = entree


# GTK relie souvent une carte à une autre entrée (défilement, tri, filtre) : les cartes et les lignes gardent leurs
# widgets d'une liaison à l'autre (badges, pastilles), et les menus ne se construisent qu'à l'ouverture.

class Badges(Gtk.Box):
    """n badges réutilisés ; montrer(badges, n) en affiche n au plus. classes : celles de chaque badge, plus son
    style (accent, neutre, alerte)."""

    def __init__(self, n, classes, **k):
        super().__init__(spacing=4, **k)
        self.classes = list(classes)
        self.etiquettes = [Gtk.Label(css_classes=self.classes, visible=False) for _ in range(n)]
        for lb in self.etiquettes:
            self.append(lb)

    def montrer(self, badges, n=None):
        n = min(len(self.etiquettes), len(badges) if n is None else n, len(badges))
        for i, lb in enumerate(self.etiquettes):
            if i < n:
                b = badges[i]
                lb.set_label(b.texte)
                lb.set_tooltip_text(b.infobulle)
                lb.set_css_classes(self.classes + [b.style])
            lb.set_visible(i < n)
        return n > 0


def _menu_a_la_demande(bouton, fabrique):
    """Le menu d'un Gtk.MenuButton construit à son ouverture (fabrique() → Gio.Menu), pas à chaque liaison."""
    bouton.set_create_popup_func(lambda b, *_: b.set_menu_model(fabrique()))


class Pastilles(Gtk.Box):
    """Une pastille par version d'une chanson (la voix) : un clic ouvre cette version ; au-delà de MAX_PASTILLES, un
    menu « +N »."""

    def __init__(self, page):
        super().__init__(spacing=4)
        self.page = page
        self.entree = None
        self.boutons = []
        for _ in range(MAX_PASTILLES):
            b = Gtk.Button(child=Gtk.Label(ellipsize=Pango.EllipsizeMode.END), css_classes=["pastille-voix"],
                           valign=Gtk.Align.CENTER, visible=False)
            b.version = None
            b.connect("clicked", lambda bb: bb.version is not None and self.page.ouvrir_version(bb.version))
            self.append(b)
            self.boutons.append(b)
        self.plus = Gtk.MenuButton(css_classes=["pastille-voix"], valign=Gtk.Align.CENTER, visible=False,
                                   tooltip_text="Les autres versions")
        _menu_a_la_demande(self.plus, self._menu_plus)
        self.append(self.plus)

    def lier(self, entree, en_selection=False):
        self.entree = entree
        libelles = bib.libelles_versions(entree)
        for i, b in enumerate(self.boutons):
            if i < len(libelles):
                v, libelle = libelles[i]
                b.version = v
                b.get_child().set_label(libelle)
                b.set_tooltip_text(f"Ouvrir la version voix {v.voix}" +
                                   (f" (extrait à {formats.chrono(v.extrait[0])})" if v.extrait else ""))
                b.update_property([Gtk.AccessibleProperty.LABEL], [f"Ouvrir la version voix {libelle}"])
                b.set_sensitive(not en_selection)
                b.remove_css_class("en-cours")
            else:
                b.version = None
            b.set_visible(i < len(libelles))
        reste = len(libelles) - MAX_PASTILLES
        self.plus.set_menu_model(None)
        self.plus.set_visible(reste > 0)
        if reste > 0:
            self.plus.set_label(f"+{reste}")
            self.plus.set_sensitive(not en_selection)

    def _menu_plus(self):
        menu = Gio.Menu()
        for v, libelle in bib.libelles_versions(self.entree)[MAX_PASTILLES:] if self.entree else []:
            item = Gio.MenuItem.new(f"Voix {libelle}", None)
            item.set_action_and_target_value("bibliotheque.ouvrir", GLib.Variant.new_string(v.cle))
            menu.append_item(item)
        return menu

    def marquer(self, cle_version):
        """La pastille de la version qui joue, en couleur d'accent."""
        for b in self.boutons:
            if b.version is not None:
                (b.add_css_class if b.version.cle == cle_version else b.remove_css_class)("en-cours")


class Carte(Gtk.Box):
    """Une carte de la grille. Liée à un Element (lier) ; les rappels lisent self.element au moment du clic."""

    def __init__(self, page):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["carte-doublage"])
        self.page = page
        self.element = None
        self._signal = None
        self.image = Image(largeur_min=120, largeur_nat=256, rayon=10, taille_note=40)
        self.badges = Badges(4, ["badge-image"], halign=Gtk.Align.START, valign=Gtk.Align.START, margin_start=8,
                             margin_top=8)
        self.image.add_overlay(self.badges)
        # en bas à droite : le nombre de versions (une icône de copies et le nombre), puis la durée
        coin = Gtk.Box(spacing=4, halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=8, margin_bottom=8)
        self.n_versions = Gtk.Box(spacing=3, css_classes=["vignette-pastille"])
        self.n_versions.append(Gtk.Image(icon_name="edit-copy-symbolic", pixel_size=11))
        self.l_versions = Gtk.Label(css_classes=["chiffres"])
        self.n_versions.append(self.l_versions)
        coin.append(self.n_versions)
        self.duree = Gtk.Label(css_classes=["vignette-pastille", "chiffres"])
        coin.append(self.duree)
        self.image.add_overlay(coin)
        self.b_lire = Gtk.Button(icon_name="media-playback-start-symbolic",
                                 css_classes=["circular", "osd", "lecture-rapide"], halign=Gtk.Align.START,
                                 valign=Gtk.Align.END, margin_start=8, margin_bottom=8)
        self.b_lire.connect("clicked", lambda *_: self.element and self.page.lire_rapide(self.element.entree))
        self.image.add_overlay(self.b_lire)
        self.case = Gtk.CheckButton(can_target=False, can_focus=False, halign=Gtk.Align.END, valign=Gtk.Align.START,
                                    margin_end=6, margin_top=6, visible=False, css_classes=["selection-mode"])
        self.image.add_overlay(self.case)
        self.append(self.image)
        bas = Gtk.Box(spacing=2)
        textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        self.titre = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                               ellipsize=Pango.EllipsizeMode.END, max_width_chars=20, css_classes=["carte-titre"])
        self.sous = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=20,
                              css_classes=["caption", "dim-label"])
        self.pastilles = Pastilles(page)
        textes.append(self.titre)
        textes.append(self.sous)
        textes.append(self.pastilles)
        bas.append(textes)
        self.menu = Gtk.MenuButton(icon_name="view-more-symbolic", css_classes=["flat", "circular"],
                                   valign=Gtk.Align.START, tooltip_text="Actions")
        self.menu.update_property([Gtk.AccessibleProperty.LABEL], ["Actions"])
        _menu_a_la_demande(self.menu, lambda: self.page.menu_pour(self.element.entree) if self.element else
                           Gio.Menu())
        bas.append(self.menu)
        self.append(bas)
        page.cartes.add(self)

    def lier(self, element, item):
        self.delier()
        self.element = element
        e = element.entree
        p = e.principale
        sel = self.page.en_selection
        self.image.lier(p)
        self.badges.montrer(e.badges(), 2 if self.page.etroit else 4)
        self.duree.set_label(formats.chrono(e.duree_s) if e.duree_s else "")
        self.duree.set_visible(bool(e.duree_s))
        self.l_versions.set_label(str(e.n))
        self.n_versions.set_tooltip_text(f"{e.n} versions : voix {e.voix}")
        self.n_versions.set_visible(e.n > 1)
        self.titre.set_label(e.titre)
        self.titre.set_tooltip_text(e.titre if len(e.titre) > 40 else None)
        if e.n > 1:
            self.sous.set_label(f"{e.n} versions · {bib.date_courte(e.date)}")
        else:
            self.sous.set_label(bib.resume_version(p))
        if e.n > 1:
            self.pastilles.lier(e, sel)
        self.pastilles.set_visible(e.n > 1)
        self.menu.set_menu_model(None)        # refait à l'ouverture, pour cette entrée
        self.menu.set_visible(not sel)
        self.b_lire.set_visible(not sel and any(v.complet for v in e.versions))
        self.case.set_visible(sel)
        self.case.set_active(item.get_selected())
        item.set_accessible_label(bib.description_accessible(e))
        self._signal = element.connect("notify::en-lecture", lambda *_: self._maj_lecture())
        self._maj_lecture()

    def _maj_lecture(self):
        el = self.element
        joue = bool(el and el.get_property("en-lecture"))
        self.b_lire.set_icon_name("media-playback-pause-symbolic" if joue else "media-playback-start-symbolic")
        titre = el.entree.titre if el else ""
        texte = f"Mettre « {titre} » en pause" if joue else f"Lire « {titre} »"
        self.b_lire.set_tooltip_text(texte)
        self.b_lire.update_property([Gtk.AccessibleProperty.LABEL], [texte])
        (self.b_lire.add_css_class if joue else self.b_lire.remove_css_class)("en-cours")
        self.pastilles.marquer(self.page.cle_en_lecture_de(el.entree) if joue and el else None)

    def delier(self):
        if self.element is not None and self._signal is not None:
            self.element.disconnect(self._signal)
        self._signal = None
        self.element = None
        self.image.delier()


class CelluleTitre(Gtk.Box):
    """Colonne « Titre » de la liste : petite miniature, titre, et dessous les badges (ou, fenêtre étroite, les voix,
    la durée et la date, dont les colonnes sont cachées)."""

    def __init__(self, page):
        super().__init__(spacing=10, margin_top=2, margin_bottom=2)
        self.page = page
        self.element = None
        self.image = Image(largeur_min=64, largeur_nat=64, rayon=6, taille_note=16)
        self.image.set_valign(Gtk.Align.CENTER)
        self.append(self.image)
        textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER, hexpand=True)
        self.titre = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["ligne-titre"])
        self.sous = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["caption", "dim-label"])
        self.badges = Badges(4, ["badge"])
        self._a_des_badges = False
        textes.append(self.titre)
        textes.append(self.sous)
        textes.append(self.badges)
        self.append(textes)

    def lier(self, element):
        self.element = element
        e = element.entree
        self.image.lier(e.principale)
        self.titre.set_label(e.titre)
        self.titre.set_tooltip_text(e.titre)
        self._a_des_badges = self.badges.montrer(e.badges())
        self.etroit(self.page.etroit)

    def etroit(self, oui):
        e = self.element.entree if self.element else None
        if e is None:
            return
        duree = formats.chrono(e.duree_s) if e.duree_s else ""
        self.sous.set_label(" · ".join(m for m in (e.voix, duree, bib.date_courte(e.date)) if m))
        self.sous.set_visible(oui)
        self.badges.set_visible(not oui and self._a_des_badges)

    def delier(self):
        self.element = None
        self.image.delier()


class CelluleVoix(Gtk.Box):
    def __init__(self, page):
        super().__init__(valign=Gtk.Align.CENTER)
        self.label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.pastilles = Pastilles(page)
        self.append(self.label)
        self.append(self.pastilles)

    def lier(self, element, en_selection):
        e = element.entree
        self.label.set_label(e.principale.voix)
        self.label.set_visible(e.n == 1)
        if e.n > 1:
            self.pastilles.lier(e, en_selection)
        self.pastilles.set_visible(e.n > 1)


class CelluleActions(Gtk.Box):
    def __init__(self, page):
        super().__init__(spacing=2, valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
        self.page = page
        self.element = None
        self._signal = None
        self.b_lire = Gtk.Button(icon_name="media-playback-start-symbolic", css_classes=["flat", "circular"])
        self.b_lire.connect("clicked", lambda *_: self.element and self.page.lire_rapide(self.element.entree))
        self.menu = Gtk.MenuButton(icon_name="view-more-symbolic", css_classes=["flat", "circular"],
                                   tooltip_text="Actions")
        self.menu.update_property([Gtk.AccessibleProperty.LABEL], ["Actions"])
        _menu_a_la_demande(self.menu, lambda: self.page.menu_pour(self.element.entree) if self.element else
                           Gio.Menu())
        self.append(self.b_lire)
        self.append(self.menu)

    def lier(self, element):
        self.delier()
        self.element = element
        e = element.entree
        sel = self.page.en_selection
        self.menu.set_menu_model(None)
        self.menu.set_visible(not sel)
        self.b_lire.set_visible(not sel and any(v.complet for v in e.versions))
        self._signal = element.connect("notify::en-lecture", lambda *_: self._maj_lecture())
        self._maj_lecture()

    def _maj_lecture(self):
        el = self.element
        joue = bool(el and el.get_property("en-lecture"))
        self.b_lire.set_icon_name("media-playback-pause-symbolic" if joue else "media-playback-start-symbolic")
        texte = (f"Mettre « {el.entree.titre} » en pause" if joue else f"Lire « {el.entree.titre} »") if el else ""
        self.b_lire.set_tooltip_text(texte)
        self.b_lire.update_property([Gtk.AccessibleProperty.LABEL], [texte])
        (self.b_lire.add_css_class if joue else self.b_lire.remove_css_class)("accent")

    def delier(self):
        if self.element is not None and self._signal is not None:
            self.element.disconnect(self._signal)
        self._signal = None
        self.element = None


def _fabrique(setup, bind, unbind=None):
    f = Gtk.SignalListItemFactory()
    f.connect("setup", lambda _f, item: setup(item))
    f.connect("bind", lambda _f, item: bind(item))
    if unbind is not None:
        f.connect("unbind", lambda _f, item: unbind(item))
    return f


class PageBibliotheque(commun.Page):
    """La bibliothèque des doublages (section « Résultats »)."""

    def __init__(self, fenetre):
        super().__init__(fenetre, "Résultats", "resultats")
        _installer_css()
        self.choix = bib.preferences(fenetre.prefs)
        self.resultats = []           # voixla.rapports.Resultat, du plus récent au plus ancien
        self.versions = []            # voixla.bibliotheque.Version, dans le même ordre
        self.par_cle = {}             # Resultat.cle → Version
        self.entrees = {}             # Entree.cle → Entree
        self.filtre = bib.Filtre()
        self.remix_actif = False      # lu et posé par PageDetail
        self.a_ouvrir = None
        self.en_selection = False
        self.etroit = False
        self._charge = False
        self._chargement = 0
        self._apres = []              # rappels à jouer une fois la liste chargée (scénarios, ouvrir)
        self._en_lecture = None       # (dossier, voix) du résultat qui joue
        self._maj_tri = False
        self.cellules_titre = weakref.WeakSet()
        self.cartes = weakref.WeakSet()
        self._remplissage = 0
        self._priorites = None
        self.mesures = {}

        # --- modèle : magasin → filtre → tri → sélection (aucune, ou multiple en mode sélection) -------------------
        self.magasin = Gio.ListStore.new(Element)
        self.f_filtre = Gtk.CustomFilter.new(lambda el, *_: self.filtre.garde(el.entree))
        self.m_filtre = Gtk.FilterListModel.new(self.magasin, self.f_filtre)
        self.m_tri = Gtk.SortListModel.new(self.m_filtre, None)
        self.sel_aucune = Gtk.NoSelection.new(self.m_tri)
        self.sel_multiple = Gtk.MultiSelection.new(self.m_tri)
        self.sel_multiple.connect("selection-changed", lambda *_: self._maj_selection())
        self.m_tri.connect("items-changed", lambda *_: self._maj_affichage())

        self._actions()
        self._entete()
        self._vues()
        self._contenu()
        self._barre_selection()
        self._raccourcis()
        self._appliquer_tri()
        self._brancher()
        for defile in (self.defil_grille, self.defil_liste):
            defile.get_vadjustment().connect("value-changed", self._planifier_priorites)

        fenetre.ecouter("resultats", self.charger)
        fenetre.ecouter("donnees", self.charger)
        fenetre.ecouter("voix", self._voix_changees)
        lectures().ecouteurs.append(self._lecture_changee)
        seat = Gdk.Display.get_default().get_default_seat()
        if seat is not None and seat.get_capabilities() & Gdk.SeatCapabilities.TOUCH:
            self.add_css_class("tactile")     # pas de survol sur un écran tactile : le bouton de lecture reste visible

    # --- construction ------------------------------------------------------------------------------------------------

    def _actions(self):
        self.groupe = Gio.SimpleActionGroup()
        s = GLib.VariantType.new("s")

        def simple(nom, rappel, type_=s):
            a = Gio.SimpleAction.new(nom, type_)
            a.connect("activate", lambda _a, v: rappel(v.get_string() if v is not None else None))
            self.groupe.add_action(a)
            return a

        def etat(nom, valeur, rappel):
            v = GLib.Variant.new_boolean(valeur) if isinstance(valeur, bool) else GLib.Variant.new_string(valeur)
            a = Gio.SimpleAction.new_stateful(nom, None if isinstance(valeur, bool) else s, v)
            a.connect("change-state", lambda act, val: (act.set_state(val), rappel(val.unpack())))
            self.groupe.add_action(a)
            return a

        simple("dossier", self._ouvrir_dossier)
        simple("corbeille", self._corbeille_entree)
        simple("ouvrir", self._ouvrir_cle)
        simple("lire", lambda cle: cle in self.entrees and self.lire_rapide(self.entrees[cle]))
        simple("dossier-sorties", lambda _v: commun.ouvrir_dossier(
            self.fenetre, chemins.dossier_sorties(self.fenetre.donnees)), None)
        simple("effacer-filtres", lambda _v: self.effacer_filtres(), None)
        self.a_vue = etat("vue", self.choix["vue"], self.changer_vue)
        self.a_tri = etat("tri", self.choix["tri"], lambda v: self.trier(v, self.choix["inverse"]))
        self.a_inverse = etat("inverse", self.choix["inverse"], lambda v: self.trier(self.choix["tri"], v))
        self.a_regrouper = etat("regrouper", self.choix["regrouper"], self.regrouper)
        self.a_filtre = {
            "voix": etat("filtre-voix", "", lambda v: self.filtrer(voix=v)),
            "genre": etat("filtre-genre", "", lambda v: self.filtrer(genre=v)),
            "longueur": etat("filtre-longueur", "", lambda v: self.filtrer(longueur=v)),
            "autotune": etat("filtre-autotune", False, lambda v: self.filtrer(autotune=v)),
            "duo": etat("filtre-duo", False, lambda v: self.filtrer(duo=v)),
        }
        self.insert_action_group("bibliotheque", self.groupe)

    def _entete(self):
        self.titre_fenetre = Adw.WindowTitle(title="Résultats", subtitle="")
        self.entete.set_title_widget(self.titre_fenetre)
        self.b_dossier = commun.bouton(icone="folder-open-symbolic", infobulle="Ouvrir le dossier des sorties",
                                       clic=lambda: commun.ouvrir_dossier(
                                           self.fenetre, chemins.dossier_sorties(self.fenetre.donnees)))
        self.entete.pack_start(self.b_dossier)
        # de droite à gauche : sélection, recherche, tri et filtres, vue, actualiser
        self.b_selection = Gtk.ToggleButton(icon_name="selection-mode-symbolic", tooltip_text="Sélectionner")
        self.b_selection.connect("toggled", lambda b: self.selectionner(b.get_active()))
        self.entete.pack_end(self.b_selection)
        self.recherche = Gtk.SearchEntry(placeholder_text="Chercher un titre, une voix…", hexpand=True)
        self.recherche.update_property([Gtk.AccessibleProperty.LABEL], ["Chercher dans les doublages"])
        self.recherche.connect("search-changed", lambda e: self.filtrer(texte=e.get_text()))
        self.barre_recherche = Gtk.SearchBar(child=Adw.Clamp(maximum_size=600, child=self.recherche),
                                             show_close_button=False)
        self.barre_recherche.connect_entry(self.recherche)
        self.barre_recherche.set_key_capture_widget(self)
        self.vue.add_top_bar(self.barre_recherche)
        self.b_cherche = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Chercher (Ctrl+F)")
        self.b_cherche.bind_property("active", self.barre_recherche, "search-mode-enabled",
                                     GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        self.entete.pack_end(self.b_cherche)
        self.b_tri = Gtk.MenuButton(icon_name="view-sort-descending-symbolic", tooltip_text="Trier et filtrer",
                                    menu_model=self._menu_tri())
        self.b_tri.update_property([Gtk.AccessibleProperty.LABEL], ["Trier et filtrer"])
        self.entete.pack_end(self.b_tri)
        self.b_vues = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
        for vue, icone, libelle in (("grille", "view-grid-symbolic", "Grille"),
                                    ("liste", "view-list-symbolic", "Liste")):
            b = Gtk.ToggleButton(icon_name=icone, tooltip_text=f"Afficher en {libelle.lower()}",
                                 action_name="bibliotheque.vue", action_target=GLib.Variant.new_string(vue))
            b.update_property([Gtk.AccessibleProperty.LABEL], [f"Affichage en {libelle.lower()}"])
            self.b_vues.append(b)
        self.entete.pack_end(self.b_vues)
        self.b_actualiser = commun.bouton(icone="view-refresh-symbolic", infobulle="Actualiser (F5)",
                                          clic=self.actualiser)
        self.entete.pack_end(self.b_actualiser)
        # puces des filtres actifs, sous l'en-tête : chacune efface le sien
        self.puces = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=2, margin_bottom=8)
        defile = Gtk.ScrolledWindow(child=self.puces, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                    hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, propagate_natural_height=True)
        self.r_puces = Gtk.Revealer(child=defile, reveal_child=False)
        self.vue.add_top_bar(self.r_puces)

    def _menu_tri(self):
        menu = Gio.Menu()
        vue = Gio.Menu()           # aussi dans l'en-tête, sauf fenêtre étroite
        vue.append("Grille", "bibliotheque.vue::grille")
        vue.append("Liste", "bibliotheque.vue::liste")
        menu.append_section("Affichage", vue)
        tri = Gio.Menu()
        for cle in ("date", "titre", "voix", "duree"):
            tri.append(bib.LIBELLES_TRI[cle], f"bibliotheque.tri::{cle}")
        tri.append("Ordre inverse", "bibliotheque.inverse")
        menu.append_section("Trier par", tri)
        filtres = Gio.Menu()
        self.menu_voix = Gio.Menu()
        filtres.append_submenu("Voix", self.menu_voix)
        genre = Gio.Menu()
        for cle, libelle in GENRES:
            genre.append(libelle, f"bibliotheque.filtre-genre::{cle}")
        filtres.append_submenu("Chant ou parole", genre)
        longueur = Gio.Menu()
        for cle, libelle in LONGUEURS:
            longueur.append(libelle, f"bibliotheque.filtre-longueur::{cle}")
        filtres.append_submenu("Entier ou extrait", longueur)
        filtres.append("Avec auto-tune", "bibliotheque.filtre-autotune")
        filtres.append("Une voix par chanteur (duo)", "bibliotheque.filtre-duo")
        filtres.append("Effacer les filtres", "bibliotheque.effacer-filtres")
        menu.append_section("Afficher", filtres)
        divers = Gio.Menu()
        divers.append("Regrouper les versions d’une chanson", "bibliotheque.regrouper")
        divers.append("Ouvrir le dossier des sorties", "bibliotheque.dossier-sorties")
        menu.append_section(None, divers)
        self._remplir_menu_voix()
        return menu

    def _remplir_menu_voix(self):
        self.menu_voix.remove_all()
        self.menu_voix.append("Toutes les voix", "bibliotheque.filtre-voix::")
        noms = {v["id"]: v["nom"] for v in self.fenetre.voix}
        for ident, nom in bib.voix_presentes(self.versions, noms):
            item = Gio.MenuItem.new(nom, None)
            item.set_action_and_target_value("bibliotheque.filtre-voix", GLib.Variant.new_string(ident))
            self.menu_voix.append_item(item)

    def _vues(self):
        # --- grille --------------------------------------------------------------------------------------------------
        def setup(item):
            item.set_selectable(False)      # la sélection suit nos clics (mode sélection), pas ceux de GTK
            c = Carte(self)
            item.set_child(c)
            item.connect("notify::selected", lambda it, _p: c.case.set_active(it.get_selected()))

        self.grille = Gtk.GridView(model=None, factory=_fabrique(
            setup, lambda it: it.get_child().lier(it.get_item(), it), lambda it: it.get_child().delier()),
            single_click_activate=True, max_columns=8, min_columns=1, enable_rubberband=False,
            tab_behavior=Gtk.ListTabBehavior.ITEM, css_classes=["bibliotheque-grille"])
        self.grille.update_property([Gtk.AccessibleProperty.LABEL], ["Doublages"])
        self.grille.connect("activate", lambda _v, pos: self._active(pos))
        self.defil_grille = Gtk.ScrolledWindow(child=self.grille, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                               vexpand=True)

        # --- liste ---------------------------------------------------------------------------------------------------
        self.colonnes = Gtk.ColumnView(model=None, single_click_activate=True, show_row_separators=True,
                                       reorderable=False, tab_behavior=Gtk.ListTabBehavior.ITEM,
                                       css_classes=["bibliotheque-liste"])
        self.colonnes.update_property([Gtk.AccessibleProperty.LABEL], ["Doublages"])
        self.colonnes.connect("activate", lambda _v, pos: self._active(pos))

        def ligne_bind(row):
            row.set_selectable(False)
            el = row.get_item()
            if el is not None:
                row.set_accessible_label(bib.description_accessible(el.entree))
        self.colonnes.set_row_factory(_fabrique(lambda row: row.set_selectable(False), ligne_bind))

        def case_setup(cell):
            c = Gtk.CheckButton(can_target=False, can_focus=False, halign=Gtk.Align.CENTER,
                                css_classes=["selection-mode"])
            cell.set_child(c)
            cell.connect("notify::selected", lambda it, _p: c.set_active(it.get_selected()))
        self.col_case = Gtk.ColumnViewColumn(title="", factory=_fabrique(
            case_setup, lambda cell: cell.get_child().set_active(cell.get_selected())), fixed_width=44, visible=False)
        self.colonnes.append_column(self.col_case)

        def titre_setup(cell):
            c = CelluleTitre(self)
            cell.set_child(c)
            self.cellules_titre.add(c)
        self.col_titre = Gtk.ColumnViewColumn(title="Titre", factory=_fabrique(
            titre_setup, lambda cell: cell.get_child().lier(cell.get_item()),
            lambda cell: cell.get_child().delier()), expand=True, resizable=True)
        self.col_voix = Gtk.ColumnViewColumn(title="Voix", factory=_fabrique(
            lambda cell: cell.set_child(CelluleVoix(self)),
            lambda cell: cell.get_child().lier(cell.get_item(), self.en_selection)), fixed_width=190, resizable=True)

        def texte(attribut, xalign=0.0, chiffres=False):
            def setup(cell):
                cell.set_child(Gtk.Label(xalign=xalign, ellipsize=Pango.EllipsizeMode.END,
                                         css_classes=["chiffres"] if chiffres else []))

            def bind(cell):
                cell.get_child().set_label(attribut(cell.get_item().entree))
            return _fabrique(setup, bind)
        self.col_mode = Gtk.ColumnViewColumn(title="Mode", factory=texte(lambda e: e.mode or "—"), fixed_width=96)
        self.col_duree = Gtk.ColumnViewColumn(title="Durée", factory=texte(
            lambda e: formats.chrono(e.duree_s) if e.duree_s else "—", 1.0, True), fixed_width=80)
        self.col_date = Gtk.ColumnViewColumn(title="Date", factory=texte(lambda e: formats.date(e.date), 0.0, True),
                                             fixed_width=150)
        self.col_actions = Gtk.ColumnViewColumn(title="", factory=_fabrique(
            lambda cell: cell.set_child(CelluleActions(self)), lambda cell: cell.get_child().lier(cell.get_item()),
            lambda cell: cell.get_child().delier()), fixed_width=92)
        self.colonnes_tri = {"titre": self.col_titre, "voix": self.col_voix, "mode": self.col_mode,
                             "duree": self.col_duree, "date": self.col_date}
        def comparateur(critere):
            # PyGObject passe en plus les données utilisateur (None) : *_ ; le sens de la colonne, pour garder les
            # durées et modes inconnus à la fin
            def comparer(a, b, *_):
                t = self.colonnes.get_sorter()
                d = t.get_primary_sort_column() is self.colonnes_tri[critere] and \
                    t.get_primary_sort_order() == Gtk.SortType.DESCENDING
                return bib.ordre(a.entree, b.entree, critere, d)
            return comparer
        for cle, col in self.colonnes_tri.items():
            col.set_sorter(Gtk.CustomSorter.new(comparateur(cle)))
            col.set_id(cle)
        for col in (self.col_titre, self.col_voix, self.col_mode, self.col_duree, self.col_date, self.col_actions):
            self.colonnes.append_column(col)
        # un seul trieur pour les deux vues : celui des colonnes ; le menu de tri le pilote aussi
        self.trieur = self.colonnes.get_sorter()
        self.m_tri.set_sorter(self.trieur)
        self.trieur.connect("changed", lambda *_: self._tri_par_colonne())
        self.defil_liste = Gtk.ScrolledWindow(child=self.colonnes, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                              vexpand=True)
        # clavier : Espace lit (ou coche en mode sélection) la carte qui a le focus
        for vue in (self.grille, self.colonnes):
            touches = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
            touches.connect("key-pressed", self._touche_vue)
            vue.add_controller(touches)

    def _contenu(self):
        self.pile = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        self.pile.add_named(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                        width_request=32, height_request=32), "attente")
        self.pile.add_named(commun.page_vide(
            "folder-videos-symbolic", "Aucun doublage pour l’instant",
            "Vos doublages finis arrivent ici, avec une image de la vidéo, les voix et la durée. Un clic ouvre le "
            "lecteur, la frise d’octave et le remix rapide.",
            commun.bouton("Doubler une vidéo", "video-x-generic-symbolic", styles=("pill", "suggested-action"),
                          clic=lambda: self.fenetre.aller("doubler"))), "vide")
        self.rien = commun.page_vide("edit-find-symbolic", "Aucun doublage ne correspond", "",
                                     commun.bouton("Effacer la recherche et les filtres", styles=("pill",),
                                                   clic=self.effacer_tout))
        self.pile.add_named(self.rien, "rien")
        self.pile.add_named(self.defil_grille, "grille")
        self.pile.add_named(self.defil_liste, "liste")
        self.pile.set_visible_child_name("attente")
        # fenêtre étroite (Adw.Breakpoint) : cartes plus petites, colonnes secondaires cachées, moins de boutons
        bac = Adw.BreakpointBin(width_request=300, height_request=260)
        self.set_child(None)
        bac.set_child(self.vue)
        self.set_child(bac)
        etroit = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        for w in (self.b_actualiser, self.b_dossier, self.b_vues, self.col_voix, self.col_mode, self.col_duree,
                  self.col_date):
            etroit.add_setter(w, "visible", False)
        etroit.connect("apply", lambda *_: self._etroit(True))
        etroit.connect("unapply", lambda *_: self._etroit(False))
        bac.add_breakpoint(etroit)
        self.contenu(self.pile)

    def _barre_selection(self):
        # barre d'actions de la sélection : tout y rétrécit (fenêtre étroite de 360 px)
        self.barre_selection = Gtk.ActionBar()
        self.l_selection = Gtk.Label(css_classes=["caption", "chiffres"], ellipsize=Pango.EllipsizeMode.END)
        self.barre_selection.pack_start(commun.bouton(icone="edit-select-all-symbolic", infobulle="Tout sélectionner",
                                                      clic=self._tout_selectionner))
        self.barre_selection.set_center_widget(self.l_selection)
        self.b_corbeille = Gtk.Button(child=Adw.ButtonContent(label="Mettre à la corbeille…",
                                                              icon_name="user-trash-symbolic", can_shrink=True),
                                      css_classes=["destructive-action"])
        self.b_corbeille.connect("clicked", lambda *_: self._corbeille_selection())
        self.barre_selection.pack_end(self.b_corbeille)
        self.vue.add_bottom_bar(self.barre_selection)
        self.vue.set_reveal_bottom_bars(False)

    def _raccourcis(self):
        raccourcis = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        for touche, rappel in (("Escape", self._echap), ("<Control>f", self._ctrl_f),
                               ("Delete", self._touche_suppr), ("<Control>a", self._ctrl_a)):
            raccourcis.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(touche),
                                                     Gtk.CallbackAction.new(lambda *_a, r=rappel: r())))
        self.add_controller(raccourcis)

    # --- page ----------------------------------------------------------------------------------------------------

    def montrer(self):
        super().montrer()
        if not self._charge:
            self.charger()

    def cacher(self):
        super().cacher()
        self.b_selection.set_active(False)

    def actualiser(self):
        miniatures().oublier_impossibles()
        self.charger()

    def charger(self, *_):
        sorties = chemins.dossier_sorties(self.fenetre.donnees)
        noms = {v["id"]: v["nom"] for v in self.fenetre.voix}
        defaut = self.fenetre.voix_doubler
        self._chargement += 1
        n = self._chargement
        debut = time.monotonic()

        def lire():
            t0 = time.monotonic()
            rs = rapports.lister_resultats(sorties, noms, voix_defaut=defaut)
            vs = [bib.version(r, noms) for r in rs]
            return rs, vs, time.monotonic() - t0

        def fini(r):
            if n != self._chargement:
                return            # un chargement plus récent arrive
            self.resultats, self.versions, duree_lecture = r
            self.par_cle = {v.cle: v for v in self.versions}
            self.mesures.update({"lecture_disque_s": duree_lecture, "doublages": len(self.versions),
                                 "debut_chargement": debut})
            self._remplir()

        def erreur(e):
            if n == self._chargement:
                self._charge = True
                self.fenetre.toast(f"Résultats illisibles : {e}")
                self._maj_affichage()
        commun.en_fil(lire, fini, erreur)

    def apres_chargement(self, rappel):
        if self._charge and not self._apres:
            rappel()
        else:
            self._apres.append(rappel)

    def _voix_changees(self):
        """Les noms des voix ont pu changer (renommage, premier chargement) : on relit, sans bloquer."""
        if self._charge:
            self.charger()

    def _remplir(self):
        """Met le magasin à jour d'après self.versions. Peu de changements (un doublage fini, F5) : seulement ce qui a
        changé ; une entrée inchangée garde son élément, sa carte, sa sélection et la position du défilement.
        Sinon (premier chargement, regroupement) : tout, par paquets de PAQUET dans l'ordre affiché, le premier écran
        d'abord, pour que l'interface ne se fige pas pendant que GTK crée et relie les cartes."""
        t0 = time.monotonic()
        entrees = bib.regrouper(self.versions, self.choix["regrouper"])
        self.entrees = {e.cle: e for e in entrees}
        self._remplissage += 1
        n = self.magasin.get_n_items()
        anciennes = {self.magasin.get_item(i).entree.cle for i in range(n)}
        if n == 0 or len(anciennes ^ set(self.entrees)) > PAQUET:
            self.magasin.remove_all()
            tri, inverse = self.choix["tri"], self.choix["inverse"]
            ordre = functools.cmp_to_key(lambda a, b: bib.comparer(a, b, tri, inverse))
            entrees = sorted(entrees, key=lambda e: (not self.filtre.garde(e), ordre(e)))
            self.mesures["paquets_s"] = []
            self._ajouter_par_paquets(entrees, self._remplissage, t0)
            return
        for i in reversed(range(n)):
            el = self.magasin.get_item(i)
            e = self.entrees.get(el.entree.cle)
            if e is None:
                self.magasin.remove(i)
            elif e.signature() != el.entree.signature():
                self.magasin.splice(i, 1, [Element(e)])
            else:
                el.entree = e          # mêmes données ; les Resultat frais servent à ouvrir le détail
        neufs = [Element(e) for e in entrees if e.cle not in anciennes]
        if neufs:
            self.magasin.splice(self.magasin.get_n_items(), 0, neufs)
        self.mesures["paquets_s"] = [time.monotonic() - t0]
        self._fin_remplissage(t0)

    def _ajouter_par_paquets(self, entrees, n, t0):
        if n != self._remplissage:
            return False          # un remplissage plus récent a pris la place
        debut = time.monotonic()
        paquet, reste = entrees[:PAQUET], entrees[PAQUET:]
        self.magasin.splice(self.magasin.get_n_items(), 0, [Element(e) for e in paquet])
        self.mesures["paquets_s"].append(time.monotonic() - debut)
        if reste:
            # priorité « idle » : GTK met en page et dessine ce paquet avant le suivant
            GLib.idle_add(self._ajouter_par_paquets, reste, n, t0)
        else:
            self._fin_remplissage(t0)
        return False

    def _fin_remplissage(self, t0):
        self._remplir_menu_voix()
        self._en_lecture = None
        self._lecture_changee(lectures().dernier)
        premier = not self._charge
        self._charge = True
        self._maj_affichage()
        self._planifier_priorites()
        debut = self.mesures.pop("debut_chargement", None)
        self.mesures.update({"remplissage_s": time.monotonic() - t0,
                             "paquet_max_s": max(self.mesures.get("paquets_s") or [0.0])})
        if debut is not None:
            self.mesures["chargement_s"] = time.monotonic() - debut
        if premier:
            self.mesures["premier"] = True
        if self.a_ouvrir:
            dossier, voix = self.a_ouvrir
            self.a_ouvrir = None
            self.ouvrir(dossier, voix)
        apres, self._apres = self._apres, []
        for rappel in apres:
            rappel()

    # --- miniatures : celles qu'on voit d'abord ----------------------------------------------------------------------

    def _planifier_priorites(self, *_):
        if self._priorites is None:
            self._priorites = GLib.timeout_add(80, self._prioriser)

    def _prioriser(self):
        """Les miniatures attendues des cartes (ou lignes) à l'écran passent devant, de haut en bas : GTK relie
        d'avance bien plus de cartes qu'on n'en voit, et la file des miniatures sert sinon la dernière demande."""
        self._priorites = None
        grille = self.choix["vue"] == "grille"
        defile = self.defil_grille if grille else self.defil_liste
        hauteur = defile.get_height()
        visibles = []
        for c in list(self.cartes if grille else self.cellules_titre):
            cle = c.image.cle_attendue
            if cle is None or not c.get_mapped():
                continue
            ok, r = c.compute_bounds(defile)
            if ok and r.origin.y + r.size.height > 0 and r.origin.y < hauteur:
                visibles.append((round(r.origin.y), r.origin.x, cle))
        if visibles:
            visibles.sort(key=lambda x: (x[0], x[1]))
            miniatures().prioriser([cle for _y, _x, cle in visibles])
        return False

    # --- affichage -----------------------------------------------------------------------------------------------

    def _visibles(self):
        return [self.m_tri.get_item(i).entree for i in range(self.m_tri.get_n_items())]

    def _maj_affichage(self):
        if not self._charge and not self.versions:
            self.pile.set_visible_child_name("attente")
            return
        visibles = self._visibles()
        self.titre_fenetre.set_subtitle(bib.compte(visibles, list(self.entrees.values()), len(self.versions),
                                                   self.filtre))
        if not self.versions:
            nom = "vide"
        elif not visibles:
            nom = "rien"
            mots = self.filtre.texte.strip()
            self.rien.set_description((f"Rien ne contient « {commun.echapper(mots)} »" if mots else "Rien")
                                      + (" avec ces filtres." if self.filtre.actif else "."))
        else:
            nom = self.choix["vue"]
        if self.pile.get_visible_child_name() != nom:
            self.pile.set_visible_child_name(nom)
        self._maj_puces()
        self._maj_selection()

    def _maj_puces(self):
        commun.vider(self.puces)
        noms = dict(bib.voix_presentes(self.versions, {v["id"]: v["nom"] for v in self.fenetre.voix}))
        etiquettes = self.filtre.etiquettes(noms)
        for cle, libelle in etiquettes:
            contenu = Gtk.Box(spacing=4)
            contenu.append(Gtk.Label(label=libelle))
            contenu.append(Gtk.Image(icon_name="window-close-symbolic", pixel_size=12))
            b = Gtk.Button(child=contenu, css_classes=["pill", "puce-filtre"], tooltip_text=f"Retirer « {libelle} »")
            b.update_property([Gtk.AccessibleProperty.LABEL], [f"Retirer le filtre {libelle}"])
            b.connect("clicked", lambda _b, c=cle: self.effacer_filtres(c))
            self.puces.append(b)
        if len(etiquettes) > 1:
            self.puces.append(commun.bouton("Tout effacer", styles=("flat",), clic=self.effacer_filtres))
        self.r_puces.set_reveal_child(bool(etiquettes))
        (self.b_tri.add_css_class if self.filtre.actif else self.b_tri.remove_css_class)("filtre-actif")

    def _brancher(self):
        """Seule la vue affichée lit le modèle : GTK crée et relie d'avance des dizaines de cartes ou de lignes,
        inutile pour la vue cachée."""
        modele = self.sel_multiple if self.en_selection else self.sel_aucune
        for vue, nom in ((self.grille, "grille"), (self.colonnes, "liste")):
            voulu = modele if nom == self.choix["vue"] else None
            if vue.get_model() is not voulu:
                vue.set_model(voulu)
        self._planifier_priorites()

    def changer_vue(self, vue):
        if vue not in bib.VUES:
            return
        self.choix["vue"] = vue
        self._brancher()
        self._sauver()
        self._maj_affichage()
        (self.grille if vue == "grille" else self.colonnes).grab_focus()

    def _etroit(self, oui):
        self.etroit = oui
        (self.grille.add_css_class if oui else self.grille.remove_css_class)("etroite")
        if self.grille.get_model() is not None:
            self.grille.set_model(None)       # relie les cartes visibles : moins de badges en fenêtre étroite
            self._brancher()
        for c in list(self.cellules_titre):
            c.etroit(oui)

    def _sauver(self):
        self.fenetre.prefs = chemins.modifier_preference(bib.CLE_PREFERENCES, dict(self.choix))

    # --- tri, filtres, regroupement --------------------------------------------------------------------------------

    def _appliquer_tri(self):
        """Le tri choisi (menu ou préférences) passe au trieur des colonnes, que les deux vues partagent."""
        col = self.colonnes_tri[self.choix["tri"]]
        sens = Gtk.SortType.DESCENDING if bib.decroissant(self.choix["tri"], self.choix["inverse"]) else \
            Gtk.SortType.ASCENDING
        self._maj_tri = True
        try:
            self.colonnes.sort_by_column(col, sens)
        finally:
            self._maj_tri = False

    def trier(self, tri, inverse):
        if tri not in bib.TRIS:
            return
        self.choix.update(tri=tri, inverse=bool(inverse))
        self._appliquer_tri()
        self._maj_etats_tri()
        self._sauver()
        self._en_haut()

    def _tri_par_colonne(self):
        """Un clic sur l'en-tête d'une colonne : le menu de tri suit, et le choix est gardé."""
        if self._maj_tri:
            return
        col = self.trieur.get_primary_sort_column()
        if col is None or col.get_id() not in bib.TRIS:
            return
        tri = col.get_id()
        inverse = bib.inverse_de(tri, self.trieur.get_primary_sort_order() == Gtk.SortType.DESCENDING)
        if (tri, inverse) != (self.choix["tri"], self.choix["inverse"]):
            self.choix.update(tri=tri, inverse=inverse)
            self._maj_etats_tri()
            self._sauver()

    def _maj_etats_tri(self):
        self.a_tri.set_state(GLib.Variant.new_string(self.choix["tri"]))
        self.a_inverse.set_state(GLib.Variant.new_boolean(self.choix["inverse"]))

    def regrouper(self, oui):
        self.choix["regrouper"] = bool(oui)
        self._sauver()
        if self._charge:
            self._remplir()

    def filtrer(self, **valeurs):
        change = False
        for cle, val in valeurs.items():
            if getattr(self.filtre, cle) != val:
                setattr(self.filtre, cle, val)
                change = True
        if not change:
            return
        for cle, a in self.a_filtre.items():
            v = getattr(self.filtre, cle)
            a.set_state(GLib.Variant.new_boolean(v) if isinstance(v, bool) else GLib.Variant.new_string(v))
        self.f_filtre.changed(Gtk.FilterChange.DIFFERENT)
        if "texte" not in valeurs:
            self._en_haut()

    def effacer_filtres(self, cle=None):
        valeurs = {c: (False if c in ("autotune", "duo") else "") for c in ([cle] if cle else self.a_filtre)}
        self.filtrer(**valeurs)

    def effacer_tout(self):
        self.recherche.set_text("")
        self.filtre.texte = ""
        self.effacer_filtres()
        self.f_filtre.changed(Gtk.FilterChange.DIFFERENT)

    def _en_haut(self):
        for d in (self.defil_grille, self.defil_liste):
            d.get_vadjustment().set_value(0)

    # --- lecture -------------------------------------------------------------------------------------------------

    def cle_en_lecture_de(self, entree):
        """La clé (Resultat.cle) de la version de cette entrée qui joue, sinon None."""
        if self._en_lecture is None:
            return None
        return next((v.cle for v in entree.versions if v.cle_lecture == self._en_lecture), None)

    def _lecture_changee(self, etat):
        """À chaque changement de la lecture (et chaque seconde pendant qu'elle joue) : la carte de ce qui joue
        montre « pause »."""
        cle = None
        if etat is not None and etat.session and etat.genre == "resultat" and etat.statut == regles.LECTURE \
                and etat.cle and len(etat.cle) == 3:
            cle = (etat.cle[1], etat.cle[2])
        if cle == self._en_lecture:
            return
        self._en_lecture = cle
        for i in range(self.magasin.get_n_items()):
            el = self.magasin.get_item(i)
            joue = cle is not None and any(v.cle_lecture == cle for v in el.entree.versions)
            if el.get_property("en-lecture") != joue:
                el.set_property("en-lecture", joue)

    def lire_rapide(self, entree, version=None, muet=False):
        """Le bouton de lecture d'une carte : lit (ou met en pause) sans ouvrir le détail. La page du détail est créée
        sans être montrée ; elle lit le son seul (le MP3 du doublage) tant qu'on ne la regarde pas, puis reprend la
        vidéo au même instant quand on l'ouvre (barre « en cours de lecture », clic sur la carte)."""
        s = lectures().session
        if isinstance(s, PageDetail) and s.page is self and not s._oubliee and \
                any(s.meme_resultat(v.res) for v in entree.versions) and version is None:
            s.lecture_pause()
            lectures().prevoir()
            return s
        v = version or self.filtre.premiere(entree)
        if not v.complet:
            v = next((x for x in entree.versions if x.complet), v)
        if not v.complet:
            self.fenetre.toast("Doublage inachevé : rien à lire")
            return None
        d = PageDetail(self, v.res)
        if muet:
            d.lecteur.rendre_muet()
        d.connect("shown", lambda *_: d.revenir_en_video())
        if d.forme == "video":
            d.passer_en_son()
        # là où la lecture de ce doublage s'était arrêtée (préférence « positions »), sinon au début
        d.lecteur.aller_a(d.reprise or 0.0, lire=True)
        return d

    # --- ouvrir ----------------------------------------------------------------------------------------------------

    def _active(self, position):
        el = self.m_tri.get_item(position)
        if el is None:
            return
        if self.en_selection:
            sel = self.sel_multiple
            if sel.is_selected(position):
                sel.unselect_item(position)
            else:
                sel.select_item(position, False)
            return
        self.ouvrir_version(self.filtre.premiere(el.entree))

    def ouvrir_version(self, version):
        if self.en_selection:
            return
        self.ouvrir_detail(version.res)

    def _ouvrir_cle(self, cle):
        v = self.par_cle.get(cle)
        if v is not None:
            self.ouvrir_version(v)

    def ouvrir_detail(self, res):
        """Le détail d'un résultat ; celui qui joue (ou en pause) en arrière-plan est retrouvé tel quel."""
        s = lectures().session
        if isinstance(s, PageDetail) and s.page is self and not s._oubliee and s.meme_resultat(res):
            self.revenir_au_detail(s)
            return
        self.fenetre.pousser(PageDetail(self, res))

    def revenir_au_detail(self, detail):
        """Remontre une page de détail gardée (barre « en cours de lecture », clic sur la carte)."""
        if detail.dans_la_pile():
            self.fenetre.split.set_show_content(True)
        elif self.fenetre.nav.get_visible_page() is self:
            self.fenetre.pousser(detail)
        else:
            self.fenetre.montrer_pile("resultats", detail)

    def ouvrir(self, dossier, voix=None):
        """Ouvre le détail d'un dossier de sortie (« Voir le résultat » de la page Travaux)."""
        dossier = Path(dossier)
        r = next((x for x in self.resultats if x.dossier == dossier and (voix is None or x.voix == voix)), None)
        if r is None:
            self.a_ouvrir = (dossier, voix)
            self.charger()
            return
        self.ouvrir_detail(r)

    def proteger_caches(self):
        """Les copies vidéo et la voix combinée des détails ouverts ou en lecture : jamais purgées (chemins.proteger,
        lu par la purge des préparations, ici ou dans le préparateur en tâche de fond)."""
        pile = self.fenetre.nav.get_navigation_stack()
        details = [pile.get_item(i) for i in range(pile.get_n_items())] + [lectures().session]
        fichiers = [str(f) for d in dict.fromkeys(d for d in details if isinstance(d, PageDetail))
                    for f in d.fichiers_caches()]
        commun.en_fil(lambda: chemins.proteger(fichiers))

    # --- menu « ⋮ » et corbeille -------------------------------------------------------------------------------------

    def menu_pour(self, entree):
        menu = Gio.Menu()
        cible = GLib.Variant.new_string(entree.cle)
        if entree.n > 1:
            versions = Gio.Menu()
            for v, libelle in bib.libelles_versions(entree):
                item = Gio.MenuItem.new(f"Ouvrir la version {libelle}", None)
                item.set_action_and_target_value("bibliotheque.ouvrir", GLib.Variant.new_string(v.cle))
                versions.append_item(item)
            menu.append_section(None, versions)
        actions = Gio.Menu()
        for libelle, action in (("Ouvrir le dossier", "bibliotheque.dossier"),
                                (f"Mettre les {entree.n} versions à la corbeille…" if entree.n > 1 else
                                 "Mettre à la corbeille…", "bibliotheque.corbeille")):
            item = Gio.MenuItem.new(libelle, None)
            item.set_action_and_target_value(action, cible)
            actions.append_item(item)
        menu.append_section(None, actions)
        return menu

    def _ouvrir_dossier(self, cle):
        e = self.entrees.get(cle)
        if e is not None:
            commun.ouvrir_dossier(self.fenetre, e.principale.res.dossier)

    def _corbeille_entree(self, cle):
        e = self.entrees.get(cle)
        if e is not None:
            self.mettre_a_la_corbeille([v.res for v in e.versions])

    def mettre_a_la_corbeille(self, resultats, apres=None):
        """La seule mise à la corbeille de doublages (détail, menu « ⋮ », sélection) : confirmation, puis la lecture
        de ces doublages arrêtée et leurs flux fermés, puis l'envoi à la corbeille, un toast et la liste relue.
        apres(ok) à la fin."""
        liste = corbeille.plans(resultats)
        if not liste:
            return
        pris = corbeille.occupes(liste, self.fenetre.file.travaux())
        if pris:
            self.fenetre.toast(f"Un doublage en cours écrit dans « {pris[0]} » : corbeille impossible pour l’instant")
            return
        titre, corps, bouton = corbeille.confirmation(liste)
        n = sum(max(1, len(p.voix)) for p in liste)

        def faire():
            self._fermer_lectures(liste)

            def fini(ok, message, faits):
                if ok:
                    self.fenetre.toast("Doublage mis à la corbeille" if n == 1 else
                                       f"{n} doublages mis à la corbeille")
                else:
                    self.fenetre.toast(f"Corbeille impossible : {message}" +
                                       (f" ({faits} sur {len(liste)} déjà à la corbeille)" if faits else ""))
                self.b_selection.set_active(False)
                self.charger()
                if apres:
                    apres(ok)
            corbeille.enchainer(liste, commun.corbeille, fini)
        commun.confirmer(self.fenetre, titre, corps, bouton, faire)

    def _fermer_lectures(self, liste):
        """Avant la corbeille : chaque détail ouvert sur ces fichiers (affiché, ou qui joue en arrière-plan) arrête
        sa lecture et ferme ses flux, pour que rien ne lise un fichier qui part."""
        pile = self.fenetre.nav.get_navigation_stack()
        details = [pile.get_item(i) for i in range(pile.get_n_items())] + [lectures().session]
        for d in dict.fromkeys(d for d in details if isinstance(d, PageDetail)):
            if corbeille.concerne(liste, d.res.dossier, d.res.voix):
                d.fermer_pour_corbeille()

    # --- mode sélection ------------------------------------------------------------------------------------------

    def selectionner(self, oui):
        if oui == self.en_selection:
            return
        self.en_selection = oui
        if self.b_selection.get_active() != oui:
            self.b_selection.set_active(oui)
        self.sel_multiple.unselect_all()
        # changer de modèle relie toutes les cartes visibles : elles prennent leur case à cocher (ou la perdent)
        self._brancher()
        self.col_case.set_visible(oui)
        self.vue.set_reveal_bottom_bars(oui)
        self._maj_selection()

    def _selection(self):
        """Les entrées cochées."""
        bits = self.sel_multiple.get_selection()
        sortie = []
        for i in range(bits.get_size()):
            el = self.m_tri.get_item(bits.get_nth(i))
            if el is not None:
                sortie.append(el.entree)
        return sortie

    def _maj_selection(self):
        if not self.en_selection:
            return
        n = sum(e.n for e in self._selection())
        self.l_selection.set_label(formats.pluriel(n, "doublage sélectionné", "doublages sélectionnés") if n else
                                   "Cochez des doublages")
        self.b_corbeille.set_sensitive(n > 0)

    def _tout_selectionner(self):
        self.sel_multiple.select_all()

    def _corbeille_selection(self):
        choisis = [v.res for e in self._selection() for v in e.versions]
        if choisis:
            self.mettre_a_la_corbeille(choisis)

    # --- clavier -------------------------------------------------------------------------------------------------

    def _echap(self):
        if not self.en_selection:
            return False
        self.selectionner(False)
        return True

    def _ctrl_f(self):
        self.b_cherche.set_active(True)
        self.recherche.grab_focus()
        return True

    def _ctrl_a(self):
        if self.pile.get_visible_child_name() != self.choix["vue"]:
            return False          # rien à sélectionner (attente, aucun doublage, rien ne correspond)
        self.selectionner(True)
        self._tout_selectionner()
        return True

    def _touche_suppr(self):
        if self.en_selection:
            self._corbeille_selection()
            return True
        el = self._element_du_focus()
        if el is None:
            return False
        self._corbeille_entree(el.entree.cle)
        return True

    def _element_du_focus(self):
        """L'élément de la carte (ou de la ligne) qui a le focus, s'il y en a une."""
        def dedans(w, profondeur=3):
            """La carte ou la cellule liée sous w (le focus est sur la case de la grille, ou la ligne, elle-même)."""
            enfant = w.get_first_child() if profondeur else None
            while enfant is not None:
                if isinstance(enfant, (Carte, CelluleTitre, CelluleActions)) and enfant.element is not None:
                    return enfant.element
                trouve = dedans(enfant, profondeur - 1)
                if trouve is not None:
                    return trouve
                enfant = enfant.get_next_sibling()
            return None

        w = self.fenetre.get_focus()
        while w is not None and w is not self:
            if isinstance(w, (Carte, CelluleTitre, CelluleActions)) and w.element is not None:
                return w.element
            if w.get_parent() in (self.grille, self.colonnes) or isinstance(w.get_parent(), Gtk.ListView):
                return dedans(w)
            w = w.get_parent()
        return None

    def _touche_vue(self, _c, touche, _code, etat):
        if touche != Gdk.KEY_space or etat & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            return False
        focus = self.fenetre.get_focus()
        if isinstance(focus, (Gtk.Button, Gtk.CheckButton, Gtk.Entry, Gtk.Text)):
            return False                      # Espace appartient au bouton qui a le focus
        el = self._element_du_focus()
        if el is None:
            return False
        if self.en_selection:
            for i in range(self.m_tri.get_n_items()):
                if self.m_tri.get_item(i) is el:
                    self._active(i)
                    break
        else:
            self.lire_rapide(el.entree)
        return True

    # --- essais ----------------------------------------------------------------------------------------------------

    def _premiere(self, mot=""):
        mot = bib.normaliser(mot)
        for e in self._visibles():
            if not mot or any(mot in v.recherche for v in e.versions):
                return e
        return None

    def scenario(self, action):
        """Essais : « grille », « liste », « deplier », « selection » (deux entrées cochées), « corbeille »
        (confirmation pour la première entrée, sans rien envoyer), « corbeille-selection », « chercher-<mot> »,
        « filtre-<autotune|duo|chant|parole|extrait|entier|voix-<id>> », « tri-<critère> », « inverse »,
        « lire[-<mot>] » (lecture muette sans ouvrir le détail), « ouvrir[-<mot>] » (comme un clic sur la carte),
        « survol » (les boutons de lecture visibles, comme au survol), « pause-<s> » (la suite s secondes plus tard),
        « detail », « detail-<mot>[:actions du détail] ». Plusieurs actions : « liste+selection »."""
        if action.startswith("detail"):
            self.apres_chargement(lambda: GLib.timeout_add(700, lambda: (self._scenario_detail(action), False)[1]))
            return
        etapes = [a for a in action.split("+") if a]

        def jouer(i=0):
            while i < len(etapes):
                a = etapes[i]
                i += 1
                if a.startswith("pause-"):         # « pause-3 » : les étapes suivantes 3 s plus tard
                    GLib.timeout_add(int(float(a.split("-", 1)[1]) * 1000), lambda: (jouer(i), False)[1])
                    return False
                self._scenario(a)
            return False
        self.apres_chargement(lambda: GLib.timeout_add(500, jouer))

    def _scenario_detail(self, action):
        if action == "detail":
            e = next((x for x in self._visibles() if x.principale.complet), None) or self._premiere()
            if e is not None:
                self.ouvrir_detail(e.principale.res)
            return
        mot, _, suite = action.split("-", 1)[1].lower().partition(":")
        r = next((r for r in self.resultats if mot in r.titre.lower()), None)
        if r:
            d = PageDetail(self, r)
            self.fenetre.pousser(d)
            for morceau in suite.split("+") if suite else []:
                d.scenario(morceau)

    def _scenario(self, a):
        if a in bib.VUES:
            self.a_vue.change_state(GLib.Variant.new_string(a))
        elif a == "deplier":
            self.a_regrouper.change_state(GLib.Variant.new_boolean(False))
        elif a in ("selection", "corbeille-selection"):
            self.selectionner(True)
            for i in range(min(2, self.m_tri.get_n_items())):
                self.sel_multiple.select_item(i, False)
            if a == "corbeille-selection":
                self._corbeille_selection()
        elif a == "corbeille":
            e = self._premiere()
            if e is not None:
                self.mettre_a_la_corbeille([v.res for v in e.versions])
        elif a.startswith("chercher-"):
            self.b_cherche.set_active(True)
            self.recherche.set_text(a.split("-", 1)[1])
            self.filtrer(texte=self.recherche.get_text())
        elif a.startswith("filtre-voix-"):
            self.a_filtre["voix"].change_state(GLib.Variant.new_string(a.split("-", 2)[2]))
        elif a in ("filtre-autotune", "filtre-duo"):
            self.a_filtre[a.split("-")[1]].change_state(GLib.Variant.new_boolean(True))
        elif a in ("filtre-chant", "filtre-parole"):
            self.a_filtre["genre"].change_state(GLib.Variant.new_string(a.split("-")[1]))
        elif a in ("filtre-extrait", "filtre-entier"):
            self.a_filtre["longueur"].change_state(GLib.Variant.new_string(a.split("-")[1]))
        elif a.startswith("tri-"):
            self.a_tri.change_state(GLib.Variant.new_string(a.split("-", 1)[1]))
        elif a == "inverse":
            self.a_inverse.change_state(GLib.Variant.new_boolean(not self.choix["inverse"]))
        elif a == "lire" or a.startswith("lire-"):
            e = self._premiere(a.split("-", 1)[1] if "-" in a else "")
            if e is not None:
                self.lire_rapide(e, muet=True)
        elif a == "ouvrir" or a.startswith("ouvrir-"):
            e = self._premiere(a.split("-", 1)[1] if "-" in a else "")
            if e is not None:
                self.ouvrir_version(self.filtre.premiere(e))
        elif a == "survol":
            self.add_css_class("survol")
