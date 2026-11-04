"""Éléments partagés par les pages : styles, travail hors du fil de l'interface, dialogues, petites briques."""

import threading
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

CSS = b"""
.badge { font-size: 0.75em; font-weight: bold; padding: 2px 8px; border-radius: 99px; }
.badge.accent { background: @accent_bg_color; color: @accent_fg_color; }
.badge.neutre { background: alpha(currentColor, 0.1); }
.badge.succes { background: alpha(@success_color, 0.18); color: @success_color; }
.badge.alerte { background: alpha(@warning_color, 0.2); color: @warning_color; }
.badge.erreur { background: alpha(@error_color, 0.18); color: @error_color; }
.carte-travail { padding: 16px; }
.chiffres { font-feature-settings: "tnum"; }
.journal { font-family: monospace; font-size: 0.85em; }
.journal text { background: transparent; }
.zone-depot { border: 2px dashed alpha(currentColor, 0.25); border-radius: 12px; padding: 18px; }
.zone-depot.survol { border-color: @accent_color; background: alpha(@accent_bg_color, 0.12); }
.lettre { font-size: 1.5em; font-weight: 800; min-width: 1.6em; }
.etoile { padding: 2px 3px; min-width: 22px; min-height: 22px; }
.etoile.allumee { color: @warning_color; }
.compte-rebours { font-size: 4.5em; font-weight: 800; }
.texte-a-lire { font-size: 1.12em; }
.ecran-media { background: black; border-radius: 8px; }
.bandeau-ecran { padding: 4px 10px; border-radius: 6px; }
.paroles-ligne { padding: 2px 6px; border-radius: 6px; }
.paroles-ligne:hover { background: alpha(currentColor, 0.06); }
.paroles-courante { font-size: 1.22em; font-weight: 600; }
.paroles-suivante { opacity: 0.62; }
.paroles-traduction { font-style: italic; opacity: 0.72; }
.pastille { min-width: 12px; min-height: 12px; border-radius: 99px; }
.etape-icone { min-width: 16px; }
"""


def installer_css():
    fournisseur = Gtk.CssProvider()
    fournisseur.load_from_data(CSS, -1)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), fournisseur,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def en_fil(travail, fini=None, erreur=None):
    """Exécute travail() dans un fil à part, puis fini(résultat) (ou erreur(exception)) dans le fil de l'interface."""
    def lancer():
        try:
            r = travail()
        except Exception as e:  # noqa: BLE001 — remonté à l'interface
            if erreur:
                # e est effacé à la sortie du except : on le fige dans un argument par défaut
                GLib.idle_add(lambda x=e: (erreur(x), False)[1])
            else:
                import traceback
                traceback.print_exc()
            return
        if fini:
            GLib.idle_add(lambda x=r: (fini(x), False)[1])
    threading.Thread(target=lancer, daemon=True).start()


def vider(boite):
    while (enfant := boite.get_first_child()) is not None:
        boite.remove(enfant)


def legende(texte, erreur=False, selectionnable=False):
    return Gtk.Label(label=texte, xalign=0, wrap=True, selectable=selectionnable,
                     css_classes=["caption", "error" if erreur else "dim-label"])


def badge(texte, style="neutre"):
    return Gtk.Label(label=texte, css_classes=["badge", style], valign=Gtk.Align.CENTER)


def echapper(texte):
    return GLib.markup_escape_text(str(texte))


def bouton(libelle=None, icone=None, infobulle=None, styles=(), clic=None, **k):
    b = Gtk.Button(**({"label": libelle} if libelle else {}), **({"icon_name": icone} if icone and not libelle else {}),
                   tooltip_text=infobulle, css_classes=list(styles), **k)
    if libelle and icone:
        contenu = Adw.ButtonContent(label=libelle, icon_name=icone)
        b.set_child(contenu)
    if clic:
        b.connect("clicked", lambda *_: clic())
    return b


def confirmer(parent, titre, corps, libelle, rappel, destructif=True, libelle_annuler="Annuler"):
    d = Adw.AlertDialog(heading=titre, body=corps)
    d.add_response("annuler", libelle_annuler)
    d.add_response("ok", libelle)
    d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE if destructif
                              else Adw.ResponseAppearance.SUGGESTED)
    d.set_default_response("annuler" if destructif else "ok")
    d.set_close_response("annuler")
    d.connect("response", lambda _d, r: r == "ok" and rappel())
    d.present(parent)
    return d


def informer(parent, titre, corps):
    d = Adw.AlertDialog(heading=titre, body=corps)
    d.add_response("ok", "Fermer")
    d.present(parent)
    return d


def ouvrir_dossier(parent, chemin):
    """Ouvre un dossier dans le gestionnaire de fichiers."""
    Gtk.FileLauncher.new(Gio.File.new_for_path(str(chemin))).launch(parent, None, None, None)


def montrer_fichier(parent, chemin):
    """Ouvre le dossier qui contient ce fichier, fichier sélectionné."""
    Gtk.FileLauncher.new(Gio.File.new_for_path(str(chemin))).open_containing_folder(parent, None, None, None)


def corbeille(chemin, fini):
    """Met un fichier ou un dossier à la corbeille (Gio.File.trash), puis fini(ok, message)."""
    f = Gio.File.new_for_path(str(chemin))

    def termine(fichier, resultat):
        try:
            fichier.trash_finish(resultat)
            fini(True, "")
        except GLib.Error as e:
            fini(False, e.message)
    f.trash_async(GLib.PRIORITY_DEFAULT, None, termine)


def ligne(titre, sous_titre=None, icone=None, activable=False):
    r = Adw.ActionRow(title=echapper(titre), activatable=activable)
    if sous_titre:
        r.set_subtitle(echapper(sous_titre))
    if icone:
        r.add_prefix(Gtk.Image(icon_name=icone))
    return r


def ligne_valeur(titre, valeur, sous_titre=None):
    """Ligne « titre … valeur » (valeur à droite, en chiffres alignés)."""
    r = ligne(titre, sous_titre)
    r.add_suffix(Gtk.Label(label=str(valeur), css_classes=["dim-label", "chiffres"], valign=Gtk.Align.CENTER,
                           selectable=True))
    return r


def page_vide(icone, titre, description, *boutons):
    p = Adw.StatusPage(icon_name=icone, title=titre, description=description, vexpand=True)
    if boutons:
        b = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        for x in boutons:
            b.append(x)
        p.set_child(b)
    return p


def defile(enfant, largeur=760):
    """Contenu centré, limité en largeur, dans une zone qui défile verticalement."""
    clamp = Adw.Clamp(maximum_size=largeur, tightening_threshold=int(largeur * 0.8), child=enfant)
    return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=clamp)


def boite(vertical=True, espace=12, marges=0, **k):
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=espace, **k)
    if marges:
        for cote in ("top", "bottom", "start", "end"):
            getattr(b, f"set_margin_{cote}")(marges)
    return b


def filtre_fichiers(nom, types_mime=(), motifs=()):
    f = Gtk.FileFilter(name=nom)
    for t in types_mime:
        f.add_mime_type(t)
    for m in motifs:
        f.add_pattern(m)
    return f


def liste_filtres(*filtres):
    s = Gio.ListStore.new(Gtk.FileFilter)
    for f in filtres:
        s.append(f)
    return s


FILTRE_VIDEOS = ("Vidéos", ("video/*",), ("*.mp4", "*.mkv", "*.webm", "*.mov", "*.m4v"))
FILTRE_AUDIO = ("Sons et vidéos", ("audio/*", "video/*"), ("*.wav", "*.mp3", "*.ogg", "*.opus", "*.m4a", "*.flac"))


def chemin_de(gfile):
    p = gfile.get_path() if gfile else None
    return Path(p) if p else None


class Page(Adw.NavigationPage):
    """Page racine d'une section : en-tête propre (boutons de la page) et contenu. La fenêtre appelle montrer()
    quand la page devient visible et cacher() quand on la quitte."""

    def __init__(self, fenetre, titre, tag):
        super().__init__(title=titre, tag=tag)
        self.fenetre = fenetre
        self.entete = Adw.HeaderBar()
        self.vue = Adw.ToolbarView()
        self.vue.add_top_bar(self.entete)
        self.set_child(self.vue)
        self.visible_ = False

    def contenu(self, widget):
        self.vue.set_content(widget)

    def montrer(self):
        self.visible_ = True

    def cacher(self):
        self.visible_ = False


class PageProvisoire(Page):
    def __init__(self, fenetre, titre, tag, raison=""):
        super().__init__(fenetre, titre, tag)
        self.contenu(page_vide("dialog-warning-symbolic", f"{titre} indisponible",
                               "Cette page n’a pas pu être chargée." + (f"\n{raison}" if raison else "")))
