"""Séance d'enregistrement de la voix chantée : la vue (la logique est dans voixla/seance.py, sans GTK).

Ouverte depuis la page Voix (« Enregistrer ma voix chantée… »), empilée par-dessus elle : pas d'onglet de plus dans la
barre latérale. En haut, « Comment ça marche » (5 lignes). Chaque étape (parole, tessiture, notes à tenir, chanson,
chanson libre) affiche sa durée, une case « Fait » (gardée dans voix/<nom>/seance.json) et le nom de fichier conseillé
pour le téléphone ; tout le contenu est fourni : le texte à lire en grand (étape 1), la piste de tessiture guide réglée
sur la voix (étape 2), deux chansons du domaine public avec accompagnement et paroles (étape 5). Les pistes se jouent avec le lecteur de l'application (gui/lecteur.py, ses commandes : lecture / pause, barre de position)
et le temps restant ; les paroles de la chanson (le .lrc du doublage, décalé des 5 s de bips) s'affichent avec la vue
des paroles de l'application (gui/vue_paroles.py). L'import des fichiers du téléphone, leur contrôle et la durée déjà
enregistrée sont en bas. L'import reconnaît l'étape d'un fichier à son nom (« 1-parole », « 5-frere-jacques »…) et la
laisse corriger.

Pour les essais et les captures : VOIXLA_SEANCE_ETAPE (étape ouverte), VOIXLA_SEANCE_CHANSON (chanson de l'étape 5),
VOIXLA_SEANCE_POSITION (instant, en secondes, où placer les paroles).
"""

import os
import threading
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from .. import bibliotheque, chemins, formats
from .. import seance as S
from .. import seance_chansons as CH
from .. import seance_textes as TX
from . import commun
from .lecteur import Lecteur
from .vue_paroles import VueParoles

CSS = b"""
.seance-titre { font-size: 1.9em; font-weight: 800; }
.seance-consigne { font-size: 1.25em; }
.seance-carte { padding: 20px; }
.seance-pas { font-size: 1.15em; }
.seance-nom { font-family: monospace; font-weight: 700; font-size: 1.15em; }
"""

TAILLES_TEXTE = (0.6, 0.8, 1.0, 1.25, 1.5, 1.8)     # facteurs de taille du texte à lire (1,0 : déjà grand)
ECHELLE_TEXTE = 2.0                                   # le texte à lire est 2 fois la taille normale, puis ce facteur
# les fichiers que l'import sait ranger : (libellé, étape, chanson de l'étape 5)
CHOIX_IMPORT = tuple((f"{i}  {e.titre}", e.cle, "") for i, e in enumerate(S.ETAPES[:4], 1)) + tuple(
    (f"5  {c.titre}", "libre", c.cle) for c in CH.CHANSONS) + (("5  Chanson libre (un doublage)", "libre", ""),)
CHOIX_CHANSON_LIBRE = tuple((c.cle, c.titre) for c in CH.CHANSONS) + (("doublage", "Un de mes doublages"),)
_css = False


def _installer_css():
    global _css
    if _css:
        return
    from gi.repository import Gdk
    f = Gtk.CssProvider()
    f.load_from_data(CSS, -1)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), f, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css = True


def nom_langue(code):
    return bibliotheque.nom_langue(code) or "langue inconnue"


def libelle_conseil(c, rang, avec_langue=False):
    """« ★ Titre (+6 demi-tons) » : les trois premières chansons conseillées portent une étoile ; « [anglais] » quand
    on montre toutes les langues."""
    texte = c.doublage.titre + (f" [{nom_langue(c.doublage.langue)}]" if avec_langue else "")
    if c.ecart_st is not None:
        e = c.ecart_st
        texte += f"  ({'+' if e >= 0.5 else ''}{formats.nombre(e, 0)} demi-tons)"
    return ("★ " if rang < 3 and c.ecart_st is not None else "") + texte


class PageSeance(Adw.NavigationPage):
    def __init__(self, fenetre, ident, nom):
        super().__init__(title="Enregistrer ma voix chantée", tag="seance")
        _installer_css()
        self.fenetre = fenetre
        self.ident, self.nom = ident, nom
        self.dossier_voix = chemins.dossier_voix(fenetre.donnees) / ident
        self.etat = S.lire_etat(self.dossier_voix)
        self.etape = S.ETAPES[0]
        self.conseils = []              # [Conseil], les conseillées d'abord
        self.annuler = threading.Event()
        self._piste = None              # (« guide » | « musique » | « notes » | …, chemin) chargée dans le lecteur
        self._boutons_etape = {}
        self._rangees_controle = []
        self._en_preparation = False
        self._bloque = False
        self.centre_hz, self.centre_source = S.centre_voix(self.dossier_voix, self.etat["centre"])
        self.chanson_libre = CH.CHANSONS[0].cle      # « doublage » : un doublage de la bibliothèque
        self._lignes_import, self._fichiers_import = [], []

        vue = Adw.ToolbarView()
        vue.add_top_bar(Adw.HeaderBar())
        self.set_child(vue)
        defile = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        vue.set_content(defile)
        pince = Adw.Clamp(maximum_size=1100, tightening_threshold=800)
        defile.set_child(pince)
        corps = commun.boite(espace=18, marges=16)
        pince.set_child(corps)

        # comment ça marche : 5 lignes
        comment = commun.boite(espace=6, css_classes=["card", "seance-carte"])
        comment.append(Gtk.Label(label=f"Comment ça marche — voix « {nom} »", xalign=0, css_classes=["title-3"]))
        for i, texte in enumerate(S.COMMENT_CA_MARCHE, 1):
            comment.append(Gtk.Label(label=f"{i}.  {texte}", xalign=0, wrap=True, css_classes=["seance-pas"]))
        corps.append(comment)

        # rappel des consignes
        g = Adw.PreferencesGroup(title="Avant de commencer")
        for i, texte in enumerate(S.CONSIGNES_GENERALES):
            r = Adw.ActionRow(title=commun.echapper(texte), title_lines=0)
            r.add_prefix(Gtk.Image(icon_name=("audio-input-microphone-symbolic", "audio-headphones-symbolic",
                                              "input-tablet-symbolic", "mail-send-symbolic")[i]))
            g.add(r)
        corps.append(g)

        # choix de l'étape
        barre = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        premier = None
        for i, e in enumerate(S.ETAPES, 1):
            b = Gtk.ToggleButton(label=self._libelle_etape(i, e), group=premier, active=e is self.etape)
            premier = premier or b
            b.connect("toggled", lambda b, e=e: b.get_active() and self.choisir_etape(e.cle))
            barre.append(b)
            self._boutons_etape[e.cle] = b
        defile_etapes = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, child=barre,
                                           hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        corps.append(defile_etapes)

        # la carte de l'étape : titre, durée, case « Fait », nom de fichier conseillé, consignes
        self.carte = commun.boite(espace=12, css_classes=["card", "seance-carte"])
        tete = Gtk.Box(spacing=12)
        self.l_titre = Gtk.Label(xalign=0, hexpand=True, wrap=True, css_classes=["seance-titre"])
        self.b_duree = commun.badge("", "accent")
        self.c_fait = Gtk.CheckButton(label="Fait", valign=Gtk.Align.CENTER,
                                      tooltip_text="Coche quand l’étape est enregistrée : la case est gardée")
        self.c_fait.connect("toggled", lambda b: self._fait_change(b.get_active()))
        tete.append(self.l_titre)
        tete.append(self.b_duree)
        tete.append(self.c_fait)
        self.carte.append(tete)
        self.l_nom_fichier = Gtk.Label(xalign=0, wrap=True, use_markup=True)
        self.carte.append(self.l_nom_fichier)
        self.b_consignes = commun.boite(espace=8)
        self.carte.append(self.b_consignes)

        # étape 1 : le texte à lire, en grand, puis la parole libre
        self.b_texte = commun.boite(espace=12)
        regl = Gtk.Box(spacing=6)
        regl.append(Gtk.Label(label="Taille du texte", xalign=0, css_classes=["heading"]))
        self.b_moins = commun.bouton(None, "list-remove-symbolic", "Texte plus petit", clic=lambda: self._taille_texte(-1))
        self.b_plus = commun.bouton(None, "list-add-symbolic", "Texte plus grand", clic=lambda: self._taille_texte(1))
        self.l_taille = Gtk.Label(css_classes=["chiffres", "dim-label"], width_chars=5)
        for w in (self.b_moins, self.l_taille, self.b_plus):
            regl.append(w)
        self.b_texte.append(regl)
        self.b_paragraphes = commun.boite(espace=18)
        self.b_texte.append(self.b_paragraphes)
        self.carte.append(self.b_texte)

        # étapes 2 et 5 : le centre de la voix, sinon « voix grave » ou « voix aiguë »
        self.b_centre = commun.boite(espace=6)
        self.l_centre = Gtk.Label(xalign=0, wrap=True, css_classes=["seance-pas"])
        self.b_centre.append(self.l_centre)
        self.b_choix_voix = Gtk.Box(spacing=8, halign=Gtk.Align.START)
        premier = None
        self._boutons_voix = {}
        for cle, libelle, hz in TX.CHOIX_VOIX:
            b = Gtk.ToggleButton(label=f"{libelle} ({hz:.0f} Hz)", group=premier,
                                 active=self.etat["centre"] == cle and self.centre_hz == hz)
            premier = premier or b
            b.connect("toggled", lambda b, c=cle: b.get_active() and self._choisir_voix(c))
            self.b_choix_voix.append(b)
            self._boutons_voix[cle] = b
        self.b_centre.append(self.b_choix_voix)
        self.carte.append(self.b_centre)

        # étape 5 : quelle chanson ?
        self.b_libre = commun.boite(espace=8)
        liste_libre = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.r_libre = Adw.ComboRow(title="Chanson", subtitle="Du domaine public, avec accompagnement et paroles")
        self.r_libre.set_model(Gtk.StringList.new([t for _c, t in CHOIX_CHANSON_LIBRE]))
        self.r_libre.connect("notify::selected", lambda *_: self._libre_change())
        liste_libre.append(self.r_libre)
        self.b_libre.append(liste_libre)
        self.l_transposition = commun.legende("")
        self.b_libre.append(self.l_transposition)
        self.b_publique = Gtk.Box(spacing=8, halign=Gtk.Align.START)
        self.b_melodie = commun.bouton("S’entraîner : avec la mélodie", "media-playback-start-symbolic",
                                       infobulle="4 bips, puis la chanson au piano avec ses accords",
                                       clic=lambda: self.preparer_publique("melodie"))
        self.b_accomp = commun.bouton("Prise : accompagnement seul", "media-record-symbolic",
                                      styles=("suggested-action",),
                                      infobulle="La vraie prise : 4 bips, puis les accords et un léger métronome",
                                      clic=lambda: self.preparer_publique("accompagnement"))
        self.b_publique.append(self.b_melodie)
        self.b_publique.append(self.b_accomp)
        self.b_libre.append(self.b_publique)
        self.carte.append(self.b_libre)

        # chanson d'un doublage : doublage, tonalité, boutons
        self.b_chanson = commun.boite(espace=8)
        liste = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.r_doublage = Adw.ComboRow(title="Doublage", subtitle="Les chansons conseillées pour ta voix d’abord")
        self.r_doublage.set_model(Gtk.StringList.new(["Chargement…"]))
        self.r_doublage.connect("notify::selected", lambda *_: self._doublage_change())
        liste.append(self.r_doublage)
        self._doublages, self._zone, self._langues = [], None, S.LANGUES_DEFAUT
        self.r_langues = Adw.SwitchRow(title="Montrer toutes les langues")
        self.r_langues.connect("notify::active", lambda *_: self._filtrer())
        liste.append(self.r_langues)
        self.r_tonalite = Adw.ComboRow(title="Tonalité")
        self.r_tonalite.set_model(Gtk.StringList.new([S.nom_tonalite(t).capitalize() for t in S.TONALITES]))
        self.r_tonalite.connect("notify::selected", lambda *_: self._tonalite_change())
        liste.append(self.r_tonalite)
        self.b_chanson.append(liste)
        self.l_conseil = commun.legende("")
        self.b_chanson.append(self.l_conseil)
        boutons = Gtk.Box(spacing=8, halign=Gtk.Align.START)
        self.b_repeter = commun.bouton("Répéter avec la voix", "media-playback-start-symbolic",
                                       infobulle="Guide : la musique et la voix d’origine, pour trouver ta place",
                                       clic=lambda: self.preparer_chanson("guide"))
        self.b_prise = commun.bouton("Prise : musique seule", "media-record-symbolic", styles=("suggested-action",),
                                     infobulle="La vraie prise : 4 bips, puis la musique sans la voix",
                                     clic=lambda: self.preparer_chanson("musique"))
        boutons.append(self.b_repeter)
        boutons.append(self.b_prise)
        self.b_chanson.append(boutons)
        self.carte.append(self.b_chanson)

        # préparation et lecteur
        self.b_progres = commun.boite(espace=4)
        self.l_progres = commun.legende("")
        self.progres = Gtk.ProgressBar(hexpand=True)
        self.b_progres.append(self.l_progres)
        self.b_progres.append(self.progres)
        self.carte.append(self.b_progres)
        self.b_lecteur = commun.boite(espace=4)
        self.l_piste = Gtk.Label(xalign=0, css_classes=["heading"])
        self.lecteur = Lecteur(hauteur=1, sur_position=self._position)
        self.l_restant = Gtk.Label(xalign=1, css_classes=["chiffres", "dim-label"])
        entete_lecteur = Gtk.Box(spacing=8)
        self.l_piste.set_hexpand(True)
        entete_lecteur.append(self.l_piste)
        entete_lecteur.append(self.l_restant)
        self.b_lecteur.append(entete_lecteur)
        self.b_lecteur.append(self.lecteur)
        self.carte.append(self.b_lecteur)

        # paroles (ou ce qui s'affiche pendant la piste : sirènes, notes de la gamme)
        self.b_paroles = commun.boite(espace=4)
        self.vue_paroles = VueParoles(lambda t: self.lecteur.aller_a(t), langue="originale", taille="geante")
        self.vue_paroles.karaoke.set_size_request(-1, 360)   # grandes paroles, lisibles de loin pendant la prise
        self.vue_paroles.b_panneau.set_visible(False)
        self.vue_paroles.suivre(self._fournisseur)
        self.vue_paroles.dire("Choisis un doublage pour voir ses paroles")
        self.b_paroles.append(self.vue_paroles)
        self.l_note_paroles = commun.legende("")
        self.b_paroles.append(self.l_note_paroles)
        self.carte.append(self.b_paroles)
        corps.append(self.carte)

        # import
        self.g_import = Adw.PreferencesGroup(
            title="Importer mes enregistrements",
            description="Choisis les fichiers de ton téléphone (m4a, aac, mp3, wav, ogg, opus, 3gp, amr). L’étape de "
                        "chacun est reconnue à son nom (« 1-parole », « 5-frere-jacques »…) et tu peux la corriger ; "
                        "ils sont copiés tels quels dans le dossier de la voix, puis contrôlés (durée, saturation, "
                        "bruit de fond, musique du casque).")
        self.r_etape_import = Adw.ComboRow(title="Étape des fichiers au nom non reconnu")
        self.r_etape_import.set_model(Gtk.StringList.new([f"{i}  {e.titre}" for i, e in enumerate(S.ETAPES, 1)]))
        self.g_import.add(self.r_etape_import)
        corps.append(self.g_import)
        self.g_a_importer = Adw.PreferencesGroup(title="Étape de chaque fichier", visible=False)
        corps.append(self.g_a_importer)
        self.b_importer = commun.bouton("Importer mes enregistrements…", "document-open-symbolic",
                                        styles=("suggested-action",), clic=self.importer, halign=Gtk.Align.START)
        self.b_valider = commun.bouton("Importer ces fichiers", "emblem-ok-symbolic", styles=("suggested-action",),
                                       clic=self.valider_import, halign=Gtk.Align.START, visible=False)
        boite_import = Gtk.Box(spacing=8, halign=Gtk.Align.START)
        boite_import.append(self.b_importer)
        boite_import.append(self.b_valider)
        corps.append(boite_import)
        self.g_controles = Adw.PreferencesGroup(title="Contrôle des fichiers importés", visible=False)
        corps.append(self.g_controles)

        # durées
        self.g_total = Adw.PreferencesGroup(title="Déjà enregistré pour cette voix")
        self.r_chant, self.barre_chant = self._ligne_total("Voix chantée", S.OBJECTIF_CHANT_S)
        self.r_parole, self.barre_parole = self._ligne_total("Parole", S.OBJECTIF_PAROLE_S)
        corps.append(self.g_total)

        self._construire_texte()
        self.connect("hidden", lambda *_: self._quitter())
        choisie = os.environ.get("VOIXLA_SEANCE_CHANSON")      # essais et captures : « frere-jacques »…
        if choisie in dict(CHOIX_CHANSON_LIBRE):
            self.r_libre.set_selected([c for c, _t in CHOIX_CHANSON_LIBRE].index(choisie))
        self.chanson_libre = CHOIX_CHANSON_LIBRE[max(0, self.r_libre.get_selected())][0]
        self.choisir_etape(os.environ.get("VOIXLA_SEANCE_ETAPE") or "parole")
        self.charger_doublages()
        self.charger_totaux()

    # --- étapes -----------------------------------------------------------------------------------------------------

    def _libelle_etape(self, i, e):
        return f"{'✓ ' if self.etat['fait'].get(e.cle) else ''}{i}  {e.titre}"

    def _ligne_total(self, titre, objectif):
        r = Adw.ActionRow(title=titre)
        barre = Gtk.LevelBar(min_value=0, max_value=objectif, valign=Gtk.Align.CENTER, width_request=160)
        barre.add_offset_value("low", 0.0)
        barre.add_offset_value("high", objectif)
        r.add_suffix(barre)
        self.g_total.add(r)
        return r, barre

    # --- étape 1 : le texte à lire --------------------------------------------------------------------------------

    def _construire_texte(self):
        """Le texte à lire (voixla/seance_textes.py), puis la parole libre : un Gtk.Label par paragraphe, à l'échelle
        choisie."""
        self._etiquettes_texte = []

        def para(texte, gras=False, marge=0):
            l = Gtk.Label(label=texte, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                          margin_start=marge, natural_wrap_mode=Gtk.NaturalWrapMode.WORD)
            self._etiquettes_texte.append((l, gras))
            self.b_paragraphes.append(l)
            return l
        for t in TX.TEXTE_PAROLE:
            para(t)
        self.b_paragraphes.append(Gtk.Separator())
        para(TX.TITRE_LIBRE, gras=True)
        para(TX.CONSIGNE_LIBRE)
        for sujet in TX.SUJETS_LIBRES:
            para(f"•  {sujet}", marge=12)
        self._appliquer_taille_texte()

    def _appliquer_taille_texte(self):
        facteur = ECHELLE_TEXTE * self.etat["taille_texte"]
        for l, gras in self._etiquettes_texte:
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_scale_new(facteur))
            if gras:
                attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
            l.set_attributes(attrs)
        self.l_taille.set_label(f"{round(self.etat['taille_texte'] * 100)} %")
        t = self.etat["taille_texte"]
        self.b_moins.set_sensitive(t > TAILLES_TEXTE[0] + 1e-6)
        self.b_plus.set_sensitive(t < TAILLES_TEXTE[-1] - 1e-6)

    def _taille_texte(self, sens):
        i = min(range(len(TAILLES_TEXTE)), key=lambda k: abs(TAILLES_TEXTE[k] - self.etat["taille_texte"]))
        i = max(0, min(len(TAILLES_TEXTE) - 1, i + sens))
        self.etat["taille_texte"] = TAILLES_TEXTE[i]
        self._appliquer_taille_texte()
        self._enregistrer_etat()

    def _enregistrer_etat(self):
        try:
            S.ecrire_etat(self.dossier_voix, self.etat)
        except OSError as e:
            self.fenetre.toast(f"Séance non enregistrée : {e}")

    def _fait_change(self, fait):
        if self._bloque:
            return
        self.etat["fait"][self.etape.cle] = bool(fait)
        self._enregistrer_etat()
        i = S.ETAPES.index(self.etape) + 1
        self._boutons_etape[self.etape.cle].set_label(self._libelle_etape(i, self.etape))

    # --- le centre de la voix --------------------------------------------------------------------------------------

    def _maj_centre(self):
        """Dit d'où vient le centre C de la voix ; sans mesure, propose « voix grave » ou « voix aiguë »."""
        if self.centre_hz:
            self.l_centre.set_label(TX.EXPLICATION_CENTRE[0].format(source=self.centre_source,
                                                                    hz=formats.nombre(self.centre_hz, 0)))
        else:
            self.l_centre.set_label(TX.EXPLICATION_CENTRE[1])
        mesure = S.zone_de_chant(self.dossier_voix) is not None
        self.b_choix_voix.set_visible(not mesure)
        self.l_centre.set_visible(True)

    def _choisir_voix(self, cle):
        if self._bloque:
            return
        self.etat["centre"] = cle
        self._enregistrer_etat()
        self.centre_hz, self.centre_source = S.centre_voix(self.dossier_voix, cle)
        self._maj_centre()
        self._piste_changee()
        self._paroles_libre()
        if self.etape.cle == "tessiture":
            self.preparer_tessiture()

    def _piste_changee(self):
        """Le centre ou la tonalité ont changé : la piste chargée n'est plus la bonne."""
        self.lecteur.liberer()
        self._piste = None
        self.b_lecteur.set_visible(False)

    def choisir_etape(self, cle):
        e = S.etape(cle)
        self.etape = e
        self._boutons_etape[cle].set_active(True)
        self.lecteur.liberer()
        self._piste = None
        self._bloque = True
        self.c_fait.set_active(bool(self.etat["fait"].get(cle)))
        self._bloque = False
        self.l_titre.set_label(f"{S.ETAPES.index(e) + 1}. {e.titre}")
        self.b_duree.set_label(f"≈ {formats.duree(e.duree_s)}")
        noms = " ou ".join(f"« {n} »" for n in e.noms)
        self.l_nom_fichier.set_markup(f"Nom du fichier à donner sur le téléphone : <b>{commun.echapper(noms)}</b>"
                                      + (" (un fichier par chanson)" if len(e.noms) > 1 else ""))
        commun.vider(self.b_consignes)
        consignes = e.consignes
        if cle == "libre" and self.chanson_libre == "doublage":
            consignes = TX.CONSIGNES_CHANSON_DOUBLAGE
        for c in consignes:
            self.b_consignes.append(Gtk.Label(label=c, xalign=0, wrap=True, css_classes=["seance-consigne"]))
        doublage = (cle == "chanson") or (cle == "libre" and self.chanson_libre == "doublage")
        self.b_texte.set_visible(cle == "parole")
        self.b_centre.set_visible(cle in ("tessiture", "libre") and not doublage)
        self.b_libre.set_visible(cle == "libre")
        self.b_publique.set_visible(cle == "libre" and not doublage)
        self.l_transposition.set_visible(cle == "libre" and not doublage)
        self.b_chanson.set_visible(doublage)
        self.b_paroles.set_visible(doublage or cle in ("tessiture", "libre"))
        self.b_lecteur.set_visible(False)
        self.b_progres.set_visible(False)
        self.r_etape_import.set_selected(S.ETAPES.index(e))
        self._maj_centre()
        if e.piste == "notes":
            self.preparer_notes()
        elif e.piste == "tessiture":
            self.l_note_paroles.set_visible(True)
            self.l_note_paroles.set_label("Ce qui s’affiche suit la piste : la sirène en cours, puis le nom de chaque "
                                          "note de la gamme. Les 5 premières secondes sont les bips.")
            if self.centre_hz:
                self.preparer_tessiture()
            else:
                self.vue_paroles.dire("Choisis ta voix (grave ou aiguë) pour régler la piste")
        elif e.piste == "chanson" or (cle == "libre" and doublage):
            self._doublage_change()
        elif cle == "libre":
            self._paroles_libre()

    def _libre_change(self):
        """Étape 5 : une chanson du domaine public, ou un doublage."""
        i = self.r_libre.get_selected()
        self.chanson_libre = CHOIX_CHANSON_LIBRE[i][0] if 0 <= i < len(CHOIX_CHANSON_LIBRE) else CHOIX_CHANSON_LIBRE[0][0]
        if self.etape.cle == "libre" and not self._bloque:
            self.choisir_etape("libre")

    def _placer_essai(self):
        """Essais et captures : VOIXLA_SEANCE_POSITION place les paroles à cet instant (en secondes)."""
        try:
            t = float(os.environ.get("VOIXLA_SEANCE_POSITION", ""))
        except ValueError:
            return
        # la vue des paroles se replace à sa mise en page : on attend qu'elle soit posée
        GLib.timeout_add(1200, lambda: (self.vue_paroles.placer(t), False)[1])

    def _paroles_libre(self):
        """Les paroles de la chanson du domaine public choisie, et la transposition dans la voix."""
        c = CH.chanson(self.chanson_libre)
        if c is None:
            return
        self.l_note_paroles.set_visible(True)
        self.l_note_paroles.set_label("Chanson du domaine public. Les paroles suivent la piste (décalées des 5 s de bips) "
                                      "et la syllabe à chanter se remplit.")
        self.vue_paroles.definir(S.paroles_chanson_publique(c.cle))
        self.vue_paroles.placer(0.0)
        self._placer_essai()
        if self.centre_hz:
            d = CH.transposition(c, self.centre_hz)
            sens = "plus haut" if d > 0 else "plus bas"
            texte = (f"Placée dans ta voix : la note médiane de la mélodie tombe sur ton centre "
                     f"({formats.nombre(self.centre_hz, 0)} Hz), la chanson est "
                     + (f"{abs(d)} demi-ton{'s' if abs(d) > 1 else ''} {sens} que sa version d’origine."
                        if d else "dans sa tonalité d’origine.")
                     + f" Elle dure {formats.duree(c.duree_s() + S.DEBUT_CHANSON_S)} avec les bips.")
        else:
            texte = "Choisis ta voix (grave ou aiguë) pour régler les pistes."
        self.l_transposition.set_label(texte)
        for b in (self.b_melodie, self.b_accomp):
            b.set_sensitive(bool(self.centre_hz) and not self._en_preparation)

    def _quitter(self):
        self.annuler.set()
        self.lecteur.liberer()

    # --- pistes -----------------------------------------------------------------------------------------------------

    def _progres(self, fraction, texte):
        if self.annuler.is_set():
            return False
        self.progres.set_fraction(max(0.0, min(1.0, fraction)))
        self.l_progres.set_label(texte)
        return False

    def _demarrer_preparation(self, texte):
        self._en_preparation = True
        self.b_progres.set_visible(True)
        self.progres.set_fraction(0.0)
        self.l_progres.set_label(texte)
        for b in (self.b_repeter, self.b_prise, self.b_melodie, self.b_accomp):
            b.set_sensitive(False)

    def _fin_preparation(self):
        self._en_preparation = False
        self.b_progres.set_visible(False)
        for b in (self.b_repeter, self.b_prise):
            b.set_sensitive(self.conseil() is not None)
        for b in (self.b_melodie, self.b_accomp):
            b.set_sensitive(bool(self.centre_hz))

    def preparer_notes(self):
        self._demarrer_preparation("Préparation des notes…")
        annuler = self.annuler

        def faire():
            return S.preparer_notes(lambda f, t: GLib.idle_add(self._progres, f, t), annuler=annuler)

        def fini(chemin):
            self._fin_preparation()
            if self.etape.piste == "notes":
                self.charger_piste("notes", chemin, "Les notes à tenir", lire=False)

        def echec(e):
            self._fin_preparation()
            commun.informer(self.fenetre, "Piste impossible à préparer", str(e))
        commun.en_fil(faire, fini, echec)

    def preparer_tessiture(self):
        """La piste des sirènes et de la gamme, réglée sur le centre C de la voix ; ce qui s'affiche la suit."""
        if not self.centre_hz or self._en_preparation:
            return
        self._demarrer_preparation("Préparation de la tessiture…")
        annuler, centre = self.annuler, self.centre_hz

        def faire():
            return S.preparer_tessiture(centre, lambda f, t: GLib.idle_add(self._progres, f, t), annuler=annuler)

        def fini(chemin):
            self._fin_preparation()
            if self.etape.piste == "tessiture" and centre == self.centre_hz:
                self.vue_paroles.definir(S.paroles_tessiture(centre))
                self.vue_paroles.placer(0.0)
                self._placer_essai()
                self.charger_piste("tessiture", chemin, "Tessiture : sirènes et gamme", lire=False)

        def echec(e):
            self._fin_preparation()
            commun.informer(self.fenetre, "Piste impossible à préparer", str(e))
        commun.en_fil(faire, fini, echec)

    def preparer_publique(self, genre):
        """genre : « melodie » (pour s'entraîner) ou « accompagnement » (la vraie prise), pour la chanson choisie."""
        c = CH.chanson(self.chanson_libre)
        if c is None or not self.centre_hz or self._en_preparation:
            return
        self._demarrer_preparation(f"Préparation de « {c.titre} »…")
        annuler, centre = self.annuler, self.centre_hz

        def faire():
            return S.preparer_chanson_publique(c.cle, genre, centre, lambda f, t: GLib.idle_add(self._progres, f, t),
                                               annuler=annuler)

        def fini(chemin):
            self._fin_preparation()
            if self.etape.cle == "libre" and self.chanson_libre == c.cle:
                self.charger_piste(genre, chemin, f"{c.titre} · {S.GENRES_CHANSON[genre]}", lire=True)

        def echec(e):
            self._fin_preparation()
            commun.informer(self.fenetre, "Piste impossible à préparer", str(e))
        commun.en_fil(faire, fini, echec)

    def conseil(self):
        i = self.r_doublage.get_selected()
        return self.conseils[i] if 0 <= i < len(self.conseils) else None

    def tonalite(self):
        i = self.r_tonalite.get_selected()
        return S.TONALITES[i] if 0 <= i < len(S.TONALITES) else 0

    def preparer_chanson(self, genre):
        """genre : « guide » (avec la voix, pour répéter) ou « musique » (la vraie prise, musique seule)."""
        c = self.conseil()
        if c is None or self._en_preparation:
            return
        t = self.tonalite()
        self._demarrer_preparation("Préparation des pistes…")
        annuler, travail = self.annuler, c.doublage.travail

        def faire():
            return S.preparer_pistes(travail, t, lambda f, texte: GLib.idle_add(self._progres, f, texte),
                                     annuler=annuler)

        def fini(pistes):
            self._fin_preparation()
            titre = "Guide avec la voix (pour répéter)" if genre == "guide" else "Prise : musique seule"
            self.charger_piste(genre, pistes[genre], f"{titre} · {S.nom_tonalite(t)}", lire=True)

        def echec(e):
            self._fin_preparation()
            commun.informer(self.fenetre, "Pistes impossibles à préparer", str(e))
        commun.en_fil(faire, fini, echec)

    def charger_piste(self, genre, chemin, titre, lire=False):
        self._piste = (genre, Path(chemin))
        self.l_piste.set_label(titre)
        self.lecteur.definir({"piste": (str(chemin), 0.0, titre)}, audio_seul=True)
        self.b_lecteur.set_visible(True)
        self.l_restant.set_label("")
        if lire:
            self.lecteur.aller_a(0.0, lire=True)

    def _position(self, t, _lit):
        d = self.lecteur.duree()
        self.l_restant.set_label(f"reste {formats.duree(max(0.0, d - t))}" if d else "")

    def _fournisseur(self):
        m = self.lecteur.media()
        return self.lecteur.position_ref(), bool(m and m.get_playing())

    # --- chansons conseillées ---------------------------------------------------------------------------------------

    def charger_doublages(self):
        sorties, voix = chemins.dossier_sorties(self.fenetre.donnees), self.dossier_voix

        def faire():
            return S.lister_doublages(sorties), S.zone_de_chant(voix), S.langues_voix(voix)

        def fini(r):
            self._doublages, self._zone, self._langues = r
            self.r_langues.set_subtitle("Langues de la voix : " + ", ".join(
                nom_langue(c) for c in self._langues) + " (voix.json, clé « langues »)")
            self._filtrer()
        commun.en_fil(faire, fini, lambda e: self.fenetre.toast(f"Doublages illisibles : {e}"))

    def _filtrer(self):
        """Les chansons conseillées, dans les langues de la voix ; l'interrupteur montre toutes les langues."""
        toutes = self.r_langues.get_active()
        conseils = S.conseiller(self._doublages, self._zone, self._langues, toutes)
        self.conseils = conseils
        self._bloque = True
        if conseils:
            self.r_doublage.set_model(Gtk.StringList.new([libelle_conseil(c, i, toutes) for i, c in enumerate(conseils)]))
        else:
            self.r_doublage.set_model(Gtk.StringList.new(["Aucun doublage dans les langues de la voix"]))
        self._bloque = False
        self.r_doublage.set_selected(0)
        self._doublage_change()

    def _mode_doublage(self):
        return self.etape.cle == "chanson" or (self.etape.cle == "libre" and self.chanson_libre == "doublage")

    def _doublage_change(self):
        if self._bloque or not self._mode_doublage():
            return
        c = self.conseil()
        for b in (self.b_repeter, self.b_prise):
            b.set_sensitive(c is not None and not self._en_preparation)
        if c is None:
            self.l_conseil.set_label("Doublez d’abord une chanson : la séance a besoin de sa musique seule.")
            self.vue_paroles.dire("Pas de doublage choisi")
            return
        self.l_conseil.set_label(S.libelle_ecart(c) + (
            "\nSa tessiture est plus large que ta zone de confort." if c.zone_trop_etroite else ""))
        self._bloque = True
        self.r_tonalite.set_selected(S.TONALITES.index(c.tonalite))
        self._bloque = False
        self._paroles(c)

    def _tonalite_change(self):
        if self._bloque:
            return
        # la piste chargée n'est plus celle de ce réglage
        self.lecteur.liberer()
        self._piste = None
        self.b_lecteur.set_visible(False)

    def _paroles(self, c):
        p = S.lire_lrc(c.doublage.lrc) if c.doublage.lrc else None
        if p is None:
            self.vue_paroles.dire("Pas de paroles pour ce doublage : chante la mélodie")
            self.l_note_paroles.set_visible(False)
            return
        self.l_note_paroles.set_visible(True)
        self.l_note_paroles.set_label(S.NOTE_PAROLES[:1].upper() + S.NOTE_PAROLES[1:]
                                      + ". Elles sont décalées des 5 s de bips : elles suivent ta prise.")
        self.vue_paroles.definir(p)
        self.vue_paroles.placer(0.0)

    # --- import et contrôles ----------------------------------------------------------------------------------------

    def importer(self):
        filtre = commun.filtre_fichiers("Enregistrements du téléphone", ("audio/*",),
                                        tuple(f"*{x}" for x in S.FORMATS_IMPORT))
        d = Gtk.FileDialog(title="Importer mes enregistrements", modal=True, filters=commun.liste_filtres(filtre),
                           initial_folder=Gio.File.new_for_path(str(chemins.dossier_telechargements())))

        def fini(dlg, res):
            try:
                choisis = dlg.open_multiple_finish(res)
            except GLib.Error:
                return
            fichiers = [p for p in (commun.chemin_de(choisis.get_item(i)) for i in range(choisis.get_n_items())) if p]
            if fichiers:
                self.proposer_import(fichiers)
        d.open_multiple(self.fenetre, None, fini)

    def importer_fichiers(self, fichiers, choix=None):
        """Copie et contrôle des fichiers. choix : [(étape, chanson de l'étape 5)] un par fichier ; sinon l'étape est
        reconnue d'après le nom (« 3-notes.m4a »), à défaut celle de la liste « nom non reconnu »."""
        defaut = S.ETAPES[max(0, self.r_etape_import.get_selected())].cle
        if choix is None:
            choix = []
            for f in fichiers:
                cle, detail = S.reconnaitre(Path(f).name)
                choix.append((cle or defaut, detail))
        voix = self.dossier_voix
        pistes = [self._piste_de(cle, detail) for cle, detail in choix]
        self.b_importer.set_sensitive(False)
        self.fenetre.toast(f"Import de {len(fichiers)} fichier{'s' if len(fichiers) > 1 else ''}…", duree=2)

        def faire():
            sortie = []
            for f, (cle, detail), piste in zip(fichiers, choix, pistes):
                for source, dest, erreur in S.importer([f], voix, cle, detail=detail):
                    sortie.append((source, dest, erreur, S.controler_prise(dest, cle, piste) if dest else None))
            return sortie

        def fini(sortie):
            self.b_importer.set_sensitive(True)
            self.montrer_controles(sortie)
            self.charger_totaux()
        commun.en_fil(faire, fini, lambda e: (self.b_importer.set_sensitive(True),
                                              commun.informer(self.fenetre, "Import impossible", str(e))))

    def proposer_import(self, fichiers):
        """Une ligne par fichier choisi, avec l'étape reconnue d'après son nom, à corriger avant d'importer."""
        for r in self._lignes_import:
            self.g_a_importer.remove(r)
        self._lignes_import, self._fichiers_import = [], list(fichiers)
        defaut = S.ETAPES[max(0, self.r_etape_import.get_selected())].cle
        libelles = [t for t, _c, _d in CHOIX_IMPORT]
        for f in fichiers:
            cle, detail = S.reconnaitre(Path(f).name)
            reconnu = cle is not None
            cle = cle or defaut
            r = Adw.ComboRow(title=commun.echapper(Path(f).name),
                             subtitle="Étape reconnue d’après le nom" if reconnu else "Nom non reconnu : à choisir")
            r.set_model(Gtk.StringList.new(libelles))
            r.set_selected(next((i for i, (_t, c, d) in enumerate(CHOIX_IMPORT) if c == cle and d == detail),
                                next(i for i, (_t, c, _d) in enumerate(CHOIX_IMPORT) if c == cle)))
            self.g_a_importer.add(r)
            self._lignes_import.append(r)
        self.g_a_importer.set_visible(bool(fichiers))
        self.b_valider.set_visible(bool(fichiers))

    def valider_import(self):
        choix = [(CHOIX_IMPORT[max(0, r.get_selected())][1], CHOIX_IMPORT[max(0, r.get_selected())][2])
                 for r in self._lignes_import]
        fichiers = self._fichiers_import
        self.proposer_import([])
        if fichiers:
            self.importer_fichiers(fichiers, choix)

    def _piste_de(self, cle, detail=""):
        """La piste que jouait le casque pour les prises de cette étape, si elle est préparée."""
        if cle == "notes":
            p = S.dossier_seance() / "notes.mp3"
            return p if p.is_file() else None
        if cle == "tessiture" and self.centre_hz:
            p = S.chemin_tessiture(self.centre_hz)
            return p if p.is_file() else None
        if cle == "libre" and detail and self.centre_hz:
            p = S.chemin_chanson(detail, "accompagnement", self.centre_hz)
            return p if p.is_file() else None
        if cle in ("chanson", "libre") and not detail:
            if self._piste and self._piste[0] in ("musique", "guide"):
                return self._piste[1]
            c = self.conseil()
            if c is not None:
                p = S.pistes_chanson(c.doublage.travail, self.tonalite())["musique"]
                return p if p.is_file() else None
        return None

    def montrer_controles(self, sortie):
        for r in self._rangees_controle:
            self.g_controles.remove(r)
        self._rangees_controle = []
        self.g_controles.set_visible(bool(sortie))
        for source, dest, erreur, c in sortie:
            if dest is None:
                r = Adw.ActionRow(title=commun.echapper(source.name), subtitle=commun.echapper(erreur))
                r.add_prefix(Gtk.Image(icon_name="dialog-error-symbolic", css_classes=["error"]))
            else:
                morceaux = [f"{formats.duree(c.duree_s)}"]
                if c.crete_db is not None:
                    morceaux.append(f"crête {formats.nombre(c.crete_db)} dBFS")
                if c.bruit_db is not None:
                    morceaux.append(f"bruit de fond {formats.nombre(c.bruit_db)} dBFS")
                if c.fuite is not None:
                    morceaux.append(f"ressemblance avec la piste {formats.nombre(c.fuite, 2)}")
                sous = " · ".join(morceaux) + ("".join(f"\n• {a}" for a in c.alertes) if c.alertes
                                               else "\nQualité correcte.")
                r = Adw.ActionRow(title=commun.echapper(dest.name), subtitle=commun.echapper(sous), subtitle_lines=0)
                r.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic" if c.alertes else "emblem-ok-symbolic",
                                       css_classes=["warning"] if c.alertes else ["success"]))
            self.g_controles.add(r)
            self._rangees_controle.append(r)

    # --- durées -----------------------------------------------------------------------------------------------------

    def charger_totaux(self):
        voix = self.dossier_voix

        def fini(r):
            self.montrer_totaux(*r)
        commun.en_fil(lambda: S.duree_enregistree(voix), fini)

    def montrer_totaux(self, chant_s, parole_s):
        self.barre_chant.set_value(min(S.OBJECTIF_CHANT_S, chant_s))
        self.barre_parole.set_value(min(S.OBJECTIF_PAROLE_S, parole_s))
        self.r_chant.set_subtitle(f"{formats.duree(chant_s)} sur un objectif de {formats.duree(S.OBJECTIF_CHANT_S)}")
        self.r_parole.set_subtitle(f"{formats.duree(parole_s)} sur un objectif de {formats.duree(S.OBJECTIF_PAROLE_S)}")

    # --- essais -----------------------------------------------------------------------------------------------------

    def scenario(self, action):
        """« chanson » : l'étape 4 ; « import » : un fichier de synthèse importé (VOIXLA_SEANCE_FICHIER)."""
        if action.startswith("chanson"):
            self.choisir_etape("chanson")
