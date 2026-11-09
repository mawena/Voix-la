"""Séance d'enregistrement de la voix chantée : la vue (la logique est dans voixla/seance.py, sans GTK).

Ouverte depuis la page Voix (« Enregistrer ma voix chantée… »), empilée par-dessus elle : pas d'onglet de plus dans la
barre latérale. Les étapes (parole, tessiture, notes à tenir, chanson, chanson libre) ont de grandes consignes ; les
pistes se jouent avec le lecteur de l'application (gui/lecteur.py, ses commandes : lecture / pause, barre de position)
et le temps restant ; les paroles de la chanson (le .lrc du doublage, décalé des 5 s de bips) s'affichent avec la vue
des paroles de l'application (gui/vue_paroles.py). L'import des fichiers du téléphone, leur contrôle et la durée déjà
enregistrée sont en bas.
"""

import threading
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .. import bibliotheque, chemins, formats
from .. import seance as S
from . import commun
from .lecteur import Lecteur
from .vue_paroles import VueParoles

CSS = b"""
.seance-titre { font-size: 1.9em; font-weight: 800; }
.seance-consigne { font-size: 1.25em; }
.seance-carte { padding: 20px; }
"""
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
        self.etape = S.ETAPES[0]
        self.conseils = []              # [Conseil], les conseillées d'abord
        self.annuler = threading.Event()
        self._piste = None              # (« guide » | « musique » | « notes », chemin) chargée dans le lecteur
        self._boutons_etape = {}
        self._rangees_controle = []
        self._en_preparation = False
        self._bloque = False

        vue = Adw.ToolbarView()
        vue.add_top_bar(Adw.HeaderBar())
        self.set_child(vue)
        defile = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        vue.set_content(defile)
        pince = Adw.Clamp(maximum_size=820, tightening_threshold=600)
        defile.set_child(pince)
        corps = commun.boite(espace=18, marges=16)
        pince.set_child(corps)

        intro = Gtk.Label(
            label=f"Voix « {nom} » : pour que le modèle te reconnaisse dans tes doublages, il lui faut ta voix "
                  "chantée, pas seulement ta parole. Tu chantes dans ton téléphone, casque branché sur le PC, sur "
                  "les pistes de cette page ; puis tu importes tes fichiers ici.",
            xalign=0, wrap=True, css_classes=["dim-label"])
        corps.append(intro)

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
            b = Gtk.ToggleButton(label=f"{i}  {e.titre}", group=premier, active=e is self.etape)
            premier = premier or b
            b.connect("toggled", lambda b, e=e: b.get_active() and self.choisir_etape(e.cle))
            barre.append(b)
            self._boutons_etape[e.cle] = b
        defile_etapes = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, child=barre,
                                           hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        corps.append(defile_etapes)

        # la carte de l'étape
        self.carte = commun.boite(espace=12, css_classes=["card", "seance-carte"])
        tete = Gtk.Box(spacing=12)
        self.l_titre = Gtk.Label(xalign=0, hexpand=True, wrap=True, css_classes=["seance-titre"])
        self.b_duree = commun.badge("", "accent")
        tete.append(self.l_titre)
        tete.append(self.b_duree)
        self.carte.append(tete)
        self.b_consignes = commun.boite(espace=8)
        self.carte.append(self.b_consignes)

        # chanson : doublage, tonalité, boutons
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

        # paroles
        self.b_paroles = commun.boite(espace=4)
        self.vue_paroles = VueParoles(lambda t: self.lecteur.aller_a(t), langue="originale", taille="tres-grande")
        self.vue_paroles.karaoke.set_size_request(-1, 300)   # grandes paroles, lisibles de loin pendant la prise
        self.vue_paroles.b_panneau.set_visible(False)
        self.vue_paroles.suivre(self._fournisseur)
        self.vue_paroles.dire("Choisis un doublage pour voir ses paroles")
        self.b_paroles.append(self.vue_paroles)
        self.l_note_paroles = commun.legende(S.NOTE_PAROLES[:1].upper() + S.NOTE_PAROLES[1:]
                                             + ". Elles sont décalées des 5 s de bips : elles suivent ta prise.")
        self.b_paroles.append(self.l_note_paroles)
        self.carte.append(self.b_paroles)
        corps.append(self.carte)

        # import
        self.g_import = Adw.PreferencesGroup(
            title="Importer mes enregistrements",
            description="Choisis les fichiers de ton téléphone (m4a, aac, mp3, wav, ogg, opus, 3gp, amr) : ils sont "
                        "copiés tels quels dans le dossier de la voix, puis contrôlés (durée, saturation, bruit de "
                        "fond, musique du casque).")
        self.r_etape_import = Adw.ComboRow(title="Étape de ces fichiers")
        self.r_etape_import.set_model(Gtk.StringList.new([f"{i}  {e.titre}" for i, e in enumerate(S.ETAPES, 1)]))
        self.g_import.add(self.r_etape_import)
        self.b_importer = commun.bouton("Importer mes enregistrements…", "document-open-symbolic",
                                        styles=("suggested-action",), clic=self.importer, halign=Gtk.Align.START)
        corps.append(self.g_import)
        boite_import = commun.boite(espace=8)
        boite_import.append(self.b_importer)
        corps.append(boite_import)
        self.g_controles = Adw.PreferencesGroup(title="Contrôle des fichiers importés", visible=False)
        corps.append(self.g_controles)

        # durées
        self.g_total = Adw.PreferencesGroup(title="Déjà enregistré pour cette voix")
        self.r_chant, self.barre_chant = self._ligne_total("Voix chantée", S.OBJECTIF_CHANT_S)
        self.r_parole, self.barre_parole = self._ligne_total("Parole", S.OBJECTIF_PAROLE_S)
        corps.append(self.g_total)

        self.connect("hidden", lambda *_: self._quitter())
        self.choisir_etape("parole")
        self.charger_doublages()
        self.charger_totaux()

    # --- étapes -----------------------------------------------------------------------------------------------------

    def _ligne_total(self, titre, objectif):
        r = Adw.ActionRow(title=titre)
        barre = Gtk.LevelBar(min_value=0, max_value=objectif, valign=Gtk.Align.CENTER, width_request=160)
        barre.add_offset_value("low", 0.0)
        barre.add_offset_value("high", objectif)
        r.add_suffix(barre)
        self.g_total.add(r)
        return r, barre

    def choisir_etape(self, cle):
        e = S.etape(cle)
        self.etape = e
        self._boutons_etape[cle].set_active(True)
        self.lecteur.liberer()
        self._piste = None
        self.l_titre.set_label(f"{S.ETAPES.index(e) + 1}. {e.titre}")
        self.b_duree.set_label(f"≈ {formats.duree(e.duree_s)}")
        commun.vider(self.b_consignes)
        for c in e.consignes:
            self.b_consignes.append(Gtk.Label(label=c, xalign=0, wrap=True, css_classes=["seance-consigne"]))
        self.b_chanson.set_visible(e.piste == "chanson")
        self.b_paroles.set_visible(e.piste == "chanson")
        self.b_lecteur.set_visible(False)
        self.b_progres.set_visible(False)
        self.r_etape_import.set_selected(S.ETAPES.index(e))
        if e.piste == "notes":
            self.preparer_notes()
        elif e.piste == "chanson":
            self._doublage_change()

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
        for b in (self.b_repeter, self.b_prise):
            b.set_sensitive(False)

    def _fin_preparation(self):
        self._en_preparation = False
        self.b_progres.set_visible(False)
        for b in (self.b_repeter, self.b_prise):
            b.set_sensitive(self.conseil() is not None)

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

    def _doublage_change(self):
        if self._bloque:
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
                self.importer_fichiers(fichiers)
        d.open_multiple(self.fenetre, None, fini)

    def importer_fichiers(self, fichiers):
        cle = S.ETAPES[max(0, self.r_etape_import.get_selected())].cle
        piste = self._piste_de(cle)
        voix = self.dossier_voix
        self.b_importer.set_sensitive(False)
        self.fenetre.toast(f"Import de {len(fichiers)} fichier{'s' if len(fichiers) > 1 else ''}…", duree=2)

        def faire():
            sortie = []
            for source, dest, erreur in S.importer(fichiers, voix, cle):
                sortie.append((source, dest, erreur, S.controler_prise(dest, cle, piste) if dest else None))
            return sortie

        def fini(sortie):
            self.b_importer.set_sensitive(True)
            self.montrer_controles(sortie)
            self.charger_totaux()
        commun.en_fil(faire, fini, lambda e: (self.b_importer.set_sensitive(True),
                                              commun.informer(self.fenetre, "Import impossible", str(e))))

    def _piste_de(self, cle):
        """La piste que jouait le casque pour les prises de cette étape, si elle est préparée."""
        if cle == "notes":
            p = S.dossier_seance() / "notes.mp3"
            return p if p.is_file() else None
        if cle == "chanson":
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
