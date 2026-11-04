"""Application Voix-là (GTK4/libadwaita) : fenêtre, barre latérale des sections, suivi de la file des travaux,
notifications, barre « en cours de lecture ».

Fermer la fenêtre pendant une lecture ne l'arrête pas : la fenêtre se cache, l'application reste en vie
(Gio.Application.hold) et la musique continue, sur son seul son si c'était une vidéo. Elle quitte quand la lecture finit
ou s'arrête (ou après une longue pause), sauf si la fenêtre est rouverte entre-temps : relancer voix-la (instance
unique) ou « Raise » de MPRIS la remontre telle qu'on l'a laissée. Sans lecture, fermer la fenêtre quitte comme avant.
Les règles sont dans voixla/lecture.py (sans GTK), la lecture partagée dans gui/lecture.py.
"""

import fcntl
import importlib
import os
import sys
import time
import traceback

from gi.repository import Adw, Gio, GLib, Gtk  # versions fixées dans gui/__init__.py

from .. import APP_ID, NOM, __version__, chemins, moteur, travaux
from .. import lecture as regles
from .. import preparation
from . import commun
from .barre_lecture import BarreLecture
from .lecture import lectures

DELAI_PREPARATION_S = 45      # après le démarrage : la fenêtre et ses premières lectures d'abord

SECTIONS = [
    ("doubler", "Doubler", "video-x-generic-symbolic", "page_doubler", "PageDoubler"),
    ("travaux", "Travaux", "view-list-ordered-symbolic", "page_travaux", "PageTravaux"),
    ("resultats", "Résultats", "media-playback-start-symbolic", "page_resultats", "PageResultats"),
    ("voix", "Voix", "audio-input-microphone-symbolic", "page_voix", "PageVoix"),
    ("ecoute", "Écoute à l’aveugle", "audio-headphones-symbolic", "page_ecoute", "PageEcoute"),
    ("etat", "État", "preferences-system-details-symbolic", "page_etat", "PageEtat"),
]


def charger_page(fenetre, tag, titre, module, classe):
    """Importe une page ; si elle est cassée, une page d'erreur la remplace (le reste de l'application marche)."""
    try:
        m = importlib.import_module(f".{module}", __package__)
        return getattr(m, classe)(fenetre)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return commun.PageProvisoire(fenetre, titre, tag, f"{type(e).__name__} : {e}")


class Fenetre(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=NOM)
        self.set_default_size(1180, 800)
        self.set_size_request(360, 480)
        commun.installer_css()
        self.config = chemins.lire_config()
        self.donnees = chemins.dossier_donnees(self.config)
        self.prefs = chemins.lire_preferences()
        self.file = travaux.FileTravaux()
        self.caps = frozenset()
        self.voix = []
        self.voix_doubler = None   # voix que doubler prend sans --voix (voir moteur.voix_par_defaut)
        self.ecouteurs = {"voix": [], "travaux": [], "resultats": [], "donnees": [], "preferences": [],
                          "visualisations": []}
        self.statuts_vus = {}
        self._verrou = None
        self._prendre_verrou_interface()
        self.dans_le_fond = False      # fenêtre cachée pendant une lecture (fermée par l'utilisateur)

        # Barre latérale
        self.liste = Gtk.ListBox(css_classes=["navigation-sidebar"], selection_mode=Gtk.SelectionMode.SINGLE)
        self.lignes, self.badges = {}, {}
        for tag, titre, icone, _m, _c in SECTIONS:
            r = Gtk.ListBoxRow()
            b = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            b.append(Gtk.Image(icon_name=icone))
            b.append(Gtk.Label(label=titre, xalign=0, hexpand=True))
            badge = commun.badge("", "accent")
            badge.set_visible(False)
            b.append(badge)
            r.set_child(b)
            r.tag = tag
            self.liste.append(r)
            self.lignes[tag], self.badges[tag] = r, badge
        self.liste.connect("row-activated", lambda _l, r: self.aller(r.tag))
        menu = Gio.Menu()
        menu.append("Réglages", "win.reglages")
        menu.append("Raccourcis clavier", "win.show-help-overlay")
        menu.append(f"À propos de {NOM}", "win.a-propos")
        entete_laterale = Adw.HeaderBar()
        entete_laterale.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                                tooltip_text="Menu principal", primary=True))
        vue_laterale = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=self.liste,
                                                                  hscrollbar_policy=Gtk.PolicyType.NEVER))
        vue_laterale.add_top_bar(entete_laterale)
        self.etat_lateral = Gtk.Label(xalign=0, wrap=True, css_classes=["caption", "dim-label"],
                                      margin_start=12, margin_end=12, margin_bottom=10, margin_top=6)
        vue_laterale.add_bottom_bar(self.etat_lateral)

        # Contenu : une pile de navigation par section (la page de détail d'un résultat s'y empile)
        self.nav = Adw.NavigationView()
        self.nav.connect("notify::visible-page", lambda *_: self._page_changee())
        self.pages = {}
        for tag, titre, _i, module, classe in SECTIONS:
            self.pages[tag] = charger_page(self, tag, titre, module, classe)

        self.split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(title=NOM, child=vue_laterale),
            content=Adw.NavigationPage(title="Contenu", child=self.nav),
            min_sidebar_width=190, max_sidebar_width=250, sidebar_width_fraction=0.22)
        self.split.connect("notify::show-content", lambda *_: self._maj_barre())
        self.split.connect("notify::collapsed", lambda *_: self._maj_barre())
        self.toasts = Adw.ToastOverlay(child=self.split)
        # Barre « en cours de lecture » : en bas de toute la fenêtre, sous la barre latérale et le contenu, pour rester
        # à portée même fenêtre étroite (barre latérale repliée) ; les notifications s'affichent au-dessus d'elle
        self.barre = BarreLecture()
        self.cadre = Adw.ToolbarView(content=self.toasts, bottom_bar_style=Adw.ToolbarStyle.RAISED,
                                     reveal_bottom_bars=False)
        self.cadre.add_bottom_bar(self.barre)
        self.set_content(self.cadre)
        lectures().ecouteurs.append(self._maj_barre)
        etroit = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        etroit.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(etroit)

        self._actions()
        self.page_courante = None
        self.aller(os.environ.get("VOIXLA_PAGE", "doubler"), montrer_contenu=False)
        self.connect("close-request", self._fermeture)

        commun.en_fil(lambda: moteur.capacites(forcer=True), self._capacites_lues)
        self.recharger_voix()
        self._minuteur_travaux = None
        self.suivre_travaux()
        # au démarrage, quand le poste est calme : les pistes des derniers doublages, en tâche de fond
        GLib.timeout_add_seconds(DELAI_PREPARATION_S, self._preparer_recents)

    def _preparer_recents(self):
        """Les MAX_RECENTS doublages les plus récents à qui il manque des copies : préparés par le préparateur en tâche
        de fond (voixla/preparation.py), qui attend que le poste soit calme. Pas pendant les captures d'essai."""
        if not (os.environ.get("VOIXLA_CAPTURE") or os.environ.get("VOIXLA_SANS_PREPARATION")):
            try:
                preparation.lancer_en_fond(["--recents", preparation.MAX_RECENTS, "--si-calme",
                                            "--donnees", self.donnees])
            except OSError:
                traceback.print_exc()
        return False

    # --- actions ---------------------------------------------------------------------------------------------------

    def _actions(self):
        for nom, rappel, raccourcis in (
                ("reglages", self.ouvrir_reglages, ["<Control>comma"]),
                ("a-propos", self.a_propos, []),
                ("actualiser", self.actualiser_page, ["F5", "<Control>r"])):
            a = Gio.SimpleAction.new(nom, None)
            a.connect("activate", lambda *_a, r=rappel: r())
            self.add_action(a)
            if raccourcis:
                self.get_application().set_accels_for_action(f"win.{nom}", raccourcis)
        # Visualisations du son : interrupteurs (cases du menu « œil »), mémorisés dans interface.json
        for nom, cle in (("vis-melodie", "vis_melodie"), ("vis-spectre", "vis_spectre"), ("vis-onde", "vis_onde"),
                         ("vis-vumetre", "vis_vumetre"), ("melodie-hz", "melodie_hz"), ("paroles", "paroles")):
            a = Gio.SimpleAction.new_stateful(nom, None, GLib.Variant.new_boolean(bool(self.prefs.get(cle))))
            a.connect("change-state", lambda act, v, c=cle: self.basculer_visualisation(act, v, c))
            self.add_action(a)
        for i, (tag, *_r) in enumerate(SECTIONS):
            a = Gio.SimpleAction.new(f"section-{tag}", None)
            a.connect("activate", lambda *_a, t=tag: self.aller(t))
            self.add_action(a)
            self.get_application().set_accels_for_action(f"win.section-{tag}", [f"<Alt>{i + 1}"])
        self.set_help_overlay(self._aide_clavier())

    def _aide_clavier(self):
        fen = Gtk.ShortcutsWindow(modal=True)
        section = Gtk.ShortcutsSection(section_name="general", visible=True)
        groupe = Gtk.ShortcutsGroup(title="Général")
        for acc, titre in (("<Alt>1", "Doubler"), ("<Alt>2", "Travaux"), ("<Alt>3", "Résultats"), ("<Alt>4", "Voix"),
                           ("<Alt>5", "Écoute à l’aveugle"), ("<Alt>6", "État"), ("F5", "Actualiser la page"),
                           ("<Control>comma", "Réglages"), ("<Control>question", "Raccourcis clavier")):
            groupe.add_shortcut(Gtk.ShortcutsShortcut(accelerator=acc, title=titre))
        section.add_group(groupe)
        lecture = Gtk.ShortcutsGroup(title="Résultats")
        for acc, titre in (("b", "Basculer doublage ⇄ original"), ("space", "Lecture / pause")):
            lecture.add_shortcut(Gtk.ShortcutsShortcut(accelerator=acc, title=titre))
        section.add_group(lecture)
        lecture = Gtk.ShortcutsGroup(title="Écoute à l’aveugle")
        for acc, titre in (("1 2 3 4 5", "Noter la version (0 ou retour arrière : effacer)"),
                           ("v", "Basculer mix ⇄ voix seule de la version en cours")):
            lecture.add_shortcut(Gtk.ShortcutsShortcut(accelerator=acc, title=titre))
        section.add_group(lecture)
        fen.add_section(section)
        return fen

    def basculer_visualisation(self, action, valeur, cle):
        action.set_state(valeur)
        self.prefs = chemins.modifier_preference(cle, valeur.get_boolean())
        self.prevenir("visualisations")

    def regler_visualisation(self, cle, actif):
        """Depuis les réglages : même effet qu'une case du menu."""
        nom = {"vis_melodie": "vis-melodie", "vis_spectre": "vis-spectre", "vis_onde": "vis-onde",
               "vis_vumetre": "vis-vumetre", "melodie_hz": "melodie-hz", "paroles": "paroles"}[cle]
        self.lookup_action(nom).change_state(GLib.Variant.new_boolean(bool(actif)))

    def ouvrir_reglages(self, page=None):
        try:
            from .reglages import DialogueReglages
            d = DialogueReglages(self)
            if page:
                d.set_visible_page_name(page)
            d.present(self)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.toast(f"Réglages indisponibles : {e}")

    def a_propos(self):
        d = Adw.AboutDialog(application_name=NOM, application_icon=APP_ID, version=__version__,
                            developer_name="mawena", license_type=Gtk.License.MIT_X11,
                            comments="Double des vidéos (chansons, duos, rap, vidéos parlées) avec une voix clonée, "
                                     "grâce à doubler et au GPU gratuit de Kaggle.",
                            copyright="© 2026 mawena")
        d.present(self)

    def actualiser_page(self):
        p = self.pages.get(self.page_courante)
        if p and hasattr(p, "actualiser"):
            p.actualiser()

    # --- navigation ------------------------------------------------------------------------------------------------

    def aller(self, tag, montrer_contenu=True):
        if tag not in self.pages:
            tag = "doubler"
        page = self.pages[tag]
        self.liste.select_row(self.lignes[tag])
        if self.nav.get_visible_page() is not page or len(self.nav.get_navigation_stack()) > 1:
            self.nav.replace([page])
        if montrer_contenu:
            self.split.set_show_content(True)
        return page

    def _page_changee(self):
        visible = self.nav.get_visible_page()
        racine = self.nav.get_navigation_stack().get_item(0) if self.nav.get_navigation_stack().get_n_items() else None
        tag = getattr(racine, "get_tag", lambda: None)()
        if tag != self.page_courante:
            ancienne = self.pages.get(self.page_courante)
            if ancienne:
                ancienne.cacher()
            self.page_courante = tag
            p = self.pages.get(tag)
            if p:
                p.montrer()
        self.split.get_content().set_title(visible.get_title() if visible else NOM)
        self._maj_barre()

    def ouvrir_resultat(self, dossier, voix=None):
        p = self.aller("resultats")
        if hasattr(p, "ouvrir"):
            p.ouvrir(dossier, voix)

    def pousser(self, page):
        self.nav.push(page)
        self.split.set_show_content(True)

    def montrer_pile(self, tag, *suite):
        """La section tag avec ces pages par-dessus (retour à un résultat gardé, depuis la barre de lecture)."""
        page = self.pages[tag]
        self.liste.select_row(self.lignes[tag])
        self.nav.replace([page, *suite])
        self.split.set_show_content(True)

    def page_vue(self):
        """La page du contenu qu'on voit (None si la fenêtre est cachée, ou si la barre latérale la recouvre)."""
        if not self.get_visible() or (self.split.get_collapsed() and not self.split.get_show_content()):
            return None
        return self.nav.get_visible_page()

    # --- lecture en cours ------------------------------------------------------------------------------------------

    def _maj_barre(self, etat=None):
        """La barre « en cours de lecture » : visible pendant une session, sauf sur la page qui lit."""
        if not hasattr(self, "cadre"):
            return                    # pendant la construction de la fenêtre
        etat = etat if etat is not None else lectures().dernier
        voir = bool(etat.session and self.get_visible() and not lectures().session_visible())
        if voir:
            self.barre.montrer(etat)
        if self.cadre.get_reveal_bottom_bars() != voir:
            self.cadre.set_reveal_bottom_bars(voir)

    # --- données partagées -----------------------------------------------------------------------------------------

    def ecouter(self, sujet, rappel):
        self.ecouteurs[sujet].append(rappel)

    def oublier(self, sujet, rappel):
        try:
            self.ecouteurs[sujet].remove(rappel)
        except ValueError:
            pass

    def prevenir(self, sujet, *args):
        for r in list(self.ecouteurs[sujet]):
            try:
                r(*args)
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    def _capacites_lues(self, caps):
        self.caps = caps
        if not moteur.disponible():
            self.toast("doubler ne répond pas : vérifiez la commande doubler (État)")
        self.recharger_voix()
        self.prevenir("donnees")

    def recharger_voix(self, rappel=None):
        donnees = self.donnees

        def lire():
            liste = moteur.lister_voix(donnees)
            # défaut annoncé par « doubler --voix-liste --json » ; sinon clé « voix » de « doubler --config --json »
            conf = None
            if not any(v.get("defaut_doubler") for v in liste):
                try:
                    conf = moteur.config_effective()
                except Exception:  # noqa: BLE001 — sans configuration lisible, repli sur la seule voix
                    traceback.print_exc()
            return liste, moteur.voix_par_defaut(liste, conf)

        def lu(res):
            self.voix, self.voix_doubler = res
            self.prevenir("voix")
            if rappel:
                rappel()
        commun.en_fil(lire, lu)

    def voix_defaut(self):
        """Voix présélectionnée : celle des préférences de l'interface, sinon celle de doubler (clé « voix » de sa
        configuration, ou la seule voix). None avec plusieurs voix et aucun défaut : rien n'est présélectionné."""
        ids = [v["id"] for v in self.voix]
        pref = self.prefs.get("voix_defaut")
        if pref in ids:
            return pref
        return self.voix_doubler if self.voix_doubler in ids else None

    def nom_voix(self, ident):
        return next((v["nom"] for v in self.voix if v["id"] == ident), ident.capitalize() if ident else "")

    def changer_donnees(self):
        """Après un changement du dossier de données dans les réglages."""
        self.config = chemins.lire_config()
        self.donnees = chemins.dossier_donnees(self.config)
        self.recharger_voix()
        self.prevenir("donnees")
        self.prevenir("resultats")

    def preferences_changees(self):
        self.prefs = chemins.lire_preferences()
        self.prevenir("preferences")
        self.prevenir("voix")

    # --- travaux -----------------------------------------------------------------------------------------------------

    def ajouter_travaux(self, demandes):
        for d in demandes:
            self.file.ajouter(d)
        self.suivre_travaux()
        self.prevenir("travaux")

    def suivre_travaux(self):
        """Relit la file : une fois par seconde tant qu'un travail est actif, sinon seulement à la demande."""
        actifs = 0
        for t in self.file.travaux():
            change, nouveau = t.actualiser()
            statut = t.statut
            avant = self.statuts_vus.get(t.id)
            if avant is not None and avant != statut and statut in travaux.TERMINES:
                self._travail_termine(t)
            self.statuts_vus[t.id] = statut
            if t.actif:
                actifs += 1
            if change:
                self.prevenir("travaux", t, nouveau)
        if any(t.statut == "en_attente" for t in self.file.travaux()) and not self.file.executeur_actif():
            self.file.demarrer_executeur()
        self.badges["travaux"].set_label(str(actifs))
        self.badges["travaux"].set_visible(actifs > 0)
        en_cours = self.file.en_cours()
        self.etat_lateral.set_label(f"En cours : {en_cours.avancement.resume()}" if en_cours else "")
        self.etat_lateral.set_visible(bool(en_cours))
        if actifs and self._minuteur_travaux is None:
            self._minuteur_travaux = GLib.timeout_add_seconds(1, self._tic_travaux)
        return actifs

    def _tic_travaux(self):
        if self.suivre_travaux():
            return True
        self._minuteur_travaux = None
        return False

    def _travail_termine(self, t):
        ok = t.statut == "fini"
        titre = {"fini": "Doublage fini", "echec": "Doublage en échec", "annule": "Doublage annulé",
                 "interrompu": "Doublage interrompu"}.get(t.statut, "Doublage terminé")
        if t.statut != "annule":
            n = Gio.Notification.new(titre)
            n.set_body(t.titre if ok else f"{t.titre}\n{(t.avancement.erreurs or [''])[-1]}")
            self.get_application().send_notification(f"travail-{t.id}", n)
        self.toast(f"{titre} : {t.titre}")
        self.prevenir("resultats")

    # --- divers ------------------------------------------------------------------------------------------------------

    def toast(self, texte, bouton=None, action=None, duree=4):
        t = Adw.Toast(title=commun.echapper(texte), timeout=duree)
        if bouton and action:
            t.set_button_label(bouton)
            t.connect("button-clicked", lambda *_: action())
        self.toasts.add_toast(t)

    def _prendre_verrou_interface(self):
        """Verrou partagé tenu tant que la fenêtre vit : l'exécuteur détaché sait alors qu'il n'a pas à notifier."""
        try:
            self._verrou = open(self.file.racine / "interface.verrou", "a")
            fcntl.flock(self._verrou, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            self._verrou = None

    def _fermeture(self, *_):
        """Fermeture par l'utilisateur : pendant une lecture, la fenêtre se cache et l'application reste ; sinon on
        quitte comme avant (les pages se ferment, le verrou de l'interface tombe avec le processus)."""
        self._prevenir_travaux()
        e = lectures().etat_courant()
        if self.get_application().vie.fermer(e.statut if e.session else regles.ARRET) == "cacher":
            self.cacher_dans_le_fond()
            return True
        self._fermer_pages()
        return False

    def _prevenir_travaux(self):
        en_cours = self.file.en_cours()
        attente = self.file.en_attente()
        if en_cours or attente:
            n = Gio.Notification.new("Les doublages continuent")
            n.set_body(f"{len(attente) + (1 if en_cours else 0)} travail(aux) dans la file : ils continuent sans la "
                       "fenêtre, et une notification arrivera à la fin de chacun.")
            self.get_application().send_notification("fermeture", n)

    def _fermer_pages(self):
        for p in self.pages.values():
            if hasattr(p, "fermer"):
                try:
                    p.fermer()
                except Exception:  # noqa: BLE001
                    pass

    def cacher_dans_le_fond(self):
        """La fenêtre disparaît, la lecture continue : l'application est tenue (hold) ; la page affichée se cache (ses
        minuteurs s'arrêtent) ; la vidéo en cours passe sur son seul son. Le suivi des travaux continue, et avec lui
        leurs notifications (le verrou de l'interface reste pris tant que l'application vit)."""
        self.dans_le_fond = True
        self.set_visible(False)
        self.get_application().garder()
        p = self.pages.get(self.page_courante)
        if p:
            p.cacher()
        lectures().fenetre_cachee()
        lectures().prevoir()

    def reouvrir(self):
        """Relance de voix-la (activate) ou Raise de MPRIS : la fenêtre revient telle qu'on l'a laissée."""
        cachee = not self.get_visible()
        self.present()
        if not cachee:
            return
        self.dans_le_fond = False
        app = self.get_application()
        app.vie.ouvrir()
        app.relacher()
        p = self.pages.get(self.page_courante)
        if p:
            p.montrer()
        lectures().fenetre_montree()
        lectures().prevoir()
        self._maj_barre()

    def fermer_vraiment(self):
        """Fin de la lecture fenêtre cachée, ou Quit de MPRIS : on quitte pour de bon."""
        self._fermer_pages()
        try:
            lectures().ecouteurs.remove(self._maj_barre)
        except ValueError:
            pass
        self.destroy()


class Application(Adw.Application):
    def __init__(self):
        # VOIXLA_APP_ID : instance distincte pour les essais, sans réveiller l'application ouverte
        super().__init__(application_id=os.environ.get("VOIXLA_APP_ID", APP_ID),
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        # Le serveur son (WirePlumber) retient volume et sourdine par nom d'application : les essais, qui jouent
        # son coupé, prennent un autre nom pour ne pas laisser la vraie application muette au lancement suivant.
        GLib.set_application_name(f"{NOM} (essai)" if os.environ.get("VOIXLA_APP_ID") else NOM)
        self.fenetre = None
        self.vie = regles.Vie()
        self._tenue = False
        self._verif_pause = None
        self._quitte = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        lectures().app = self       # MPRIS sur la connexion de l'application, règles de vie, Raise et Quit

    def do_activate(self):
        if self.fenetre is not None:
            self.fenetre.reouvrir()     # instance unique : la fenêtre, cachée ou non, revient telle quelle
            return
        self.fenetre = Fenetre(self)
        self.fenetre.present()
        if os.environ.get("VOIXLA_CAPTURE"):
            from . import essais
            essais.preparer_capture(self, self.fenetre)

    def do_shutdown(self):
        lectures().fermer()
        Adw.Application.do_shutdown(self)

    # --- vie sans fenêtre ------------------------------------------------------------------------------------------

    def garder(self):
        if not self._tenue:
            self._tenue = True
            self.hold()

    def relacher(self):
        if self._tenue:
            self._tenue = False
            self.release()

    def lecture_changee(self, etat):
        """Après chaque changement de la lecture : fenêtre cachée, on quitte quand elle finit ou s'arrête, ou après
        une longue pause (voixla.lecture.sans_fenetre)."""
        if self.fenetre is None or self._quitte:
            return
        d = self.vie.decider(etat.statut if etat.session else regles.ARRET, time.monotonic())
        if d == "quitter":
            GLib.idle_add(lambda: (self.quitter_tout(), False)[1])
        elif d == "attendre" and self._verif_pause is None:
            reste = self.vie.reste_pause(time.monotonic()) or 0.0
            self._verif_pause = GLib.timeout_add_seconds(int(reste) + 1, self._reverifier)

    def _reverifier(self):
        self._verif_pause = None
        self.lecture_changee(lectures().etat_courant())
        return False

    def quitter_tout(self):
        """Quitte pour de bon (fin de la lecture fenêtre cachée, Quit de MPRIS)."""
        if self._quitte:
            return
        self._quitte = True
        s = lectures().session
        if s is not None:
            try:
                s.mettre_en_pause()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        lectures().fermer()
        f, self.fenetre = self.fenetre, None
        if f is not None:
            f.fermer_vraiment()
        self.relacher()


def lancer(argv=None):
    return Application().run((argv or sys.argv)[:1])
