"""Réglages : dossier de données et compte Kaggle (config.json partagé, écrit par « doubler --config » quand il
existe), voix et réglages de doublage par défaut, micro (préférences de l'interface)."""

import os

from gi.repository import Adw, Gio, GLib, Gtk

from .. import chemins, formats, moteur, systeme
from . import commun
from .enregistreur import sources_micro
from .page_doubler import MODES, OCTAVES, TEXTE_PAS, TEXTE_X42_ABSENT

TEXTE_AUTOTUNE_DEFAUT = ("Clé « autotune » de config.json : une version « (auto-tune) » en plus de chaque doublage, la "
                         "normale ne change pas ; la page Doubler peut l’allumer ou l’éteindre à chaque lancement")


class DialogueReglages(Adw.PreferencesDialog):
    def __init__(self, fenetre):
        super().__init__(title="Réglages", search_enabled=False)
        self.fenetre = fenetre
        self.prefs = chemins.lire_preferences()
        self.add(self._page_general())
        self.add(self._page_doublage())
        self.add(self._page_visualisations())
        commun.en_fil(moteur.config_effective, self._config_lue)
        commun.en_fil(sources_micro, self._micros)

    # --- Général : données et Kaggle -------------------------------------------------------------------------------

    def _page_general(self):
        p = Adw.PreferencesPage(title="Général", icon_name="emblem-system-symbolic", name="general")
        g = Adw.PreferencesGroup(title="Données",
                                 description="Dossier qui contient voix/, sorties/ et Vidéos/, séparé du code. "
                                             "doubler le lit dans ~/.config/voix-la/config.json.")
        self.r_donnees = Adw.ActionRow(title="Dossier de données", subtitle=commun.echapper(self.fenetre.donnees))
        self.r_donnees.add_suffix(commun.bouton("Changer…", valign=Gtk.Align.CENTER, clic=self.changer_donnees))
        g.add(self.r_donnees)
        if os.environ.get("VOIXLA_DONNEES"):
            g.add(commun.ligne("Imposé par VOIXLA_DONNEES", os.environ["VOIXLA_DONNEES"], "dialog-warning-symbolic"))
        p.add(g)
        self.g_kaggle = Adw.PreferencesGroup(title="Kaggle",
                                             description="Compte et noms du jeu de données et des noyaux privés. Le "
                                                         "jeton (~/.kaggle/access_token) n’est jamais lu ni affiché.")
        self.entrees = {}
        for cle, titre in (("compte", "Compte Kaggle (nom d’utilisateur)"), ("jeu", "Jeu de données des entrées"),
                           ("noyau", "Noyau GPU du doublage"), ("outils", "Noyau des outils préparés")):
            e = Adw.EntryRow(title=titre, show_apply_button=True)
            e.connect("apply", lambda r, c=cle: self.ecrire_kaggle(c, r.get_text().strip()))
            self.entrees[cle] = e
            self.g_kaggle.add(e)
        self.r_jeton = commun.ligne("Jeton Kaggle", "vérification…")
        self.g_kaggle.add(self.r_jeton)
        p.add(self.g_kaggle)
        return p

    def _config_lue(self, conf):
        self.conf = conf
        contenu = (conf or {}).get("contenu") or {}
        if getattr(self, "r_moteur", None) is not None and not getattr(self, "_moteur_relie", False):
            actuel = contenu.get("moteur") or (conf or {}).get("moteur") or "yingmusic"
            ids = [m[0] for m in moteur.MOTEURS]
            if actuel in ids:
                self.r_moteur.set_selected(ids.index(actuel))
            self._moteur_relie = True
            self.r_moteur.connect("notify::selected", lambda w, _p: self._ecrire("moteur", ids[w.get_selected()]))
        if getattr(self, "r_paroles", None) is not None and not getattr(self, "_paroles_relie", False):
            self.r_paroles.set_active(moteur.paroles_par_defaut(conf))
            self._paroles_relie = True
            self.r_paroles.connect("notify::active", lambda w, _p: self._ecrire(
                "paroles", "oui" if w.get_active() else "non", lambda oui=w.get_active(): self._paroles_ecrites(oui)))
        if getattr(self, "r_autotune", None) is not None and not getattr(self, "_autotune_relie", False):
            n = moteur.autotune_par_defaut(conf)
            self.r_autotune.set_enable_expansion(n > 0)
            self.r_at_n.set_value(n or moteur.AUTOTUNE_DEFAUT)
            connu, dossier = moteur.x42_autotune(conf)
            if connu and dossier is None:
                self.r_autotune.set_sensitive(n > 0)   # on peut encore l'éteindre
                self.r_autotune.set_subtitle(TEXTE_X42_ABSENT)
            self._autotune_relie = True
            self._autotune_attente = None
            self.r_autotune.connect("notify::enable-expansion", lambda *_: self._autotune_change())
            self.r_at_n.connect("notify::value", lambda *_: self._autotune_change())
        k = (conf or {}).get("kaggle") or {}
        for cle, e in self.entrees.items():
            brut = chemins.valeur(contenu, f"kaggle.{cle}")
            if brut is None and cle != "compte":
                brut = chemins.KAGGLE_DEFAUT[cle]
            e.set_text(str(brut or ""))
        complets = [f"{n} : {k.get(n)}" for n in ("jeu", "noyau", "outils") if k.get(n)]
        if complets:
            self.g_kaggle.set_description("Noms complets utilisés : " + " · ".join(complets)
                                          + ". Le jeton (~/.kaggle/access_token) n’est jamais lu ni affiché.")
        self.r_donnees.set_subtitle(commun.echapper(f"{conf.get('donnees')}  ({conf.get('source_donnees')})"))
        present = systeme.jeton_kaggle_present()
        self.r_jeton.set_subtitle("présent" if present else "absent : sans lui, aucune session GPU")

    def ecrire_kaggle(self, cle, valeur):
        if cle == "compte" and not valeur:
            self.fenetre.toast("Le compte Kaggle ne peut pas être vide")
            return
        self._ecrire(f"kaggle.{cle}", valeur)

    def _paroles_ecrites(self, oui):
        """Nouveau défaut des paroles : la page Doubler le reprend (son interrupteur revient au défaut)."""
        page = (getattr(self.fenetre, "pages", None) or {}).get("doubler")
        if page is not None and hasattr(page, "paroles_par_defaut_change"):
            page.paroles_par_defaut_change(oui)

    def _autotune_change(self):
        """Interrupteur ou intensité de l'auto-tune : écrit « doubler --config autotune N » (0 éteint), une fois le
        réglage posé (un clic sur la flèche du compteur n'écrit pas à chaque pas)."""
        if self._autotune_attente is not None:
            GLib.source_remove(self._autotune_attente)

        def ecrire():
            self._autotune_attente = None
            n = int(self.r_at_n.get_value()) if self.r_autotune.get_enable_expansion() else 0
            self._ecrire("autotune", str(n), lambda: self._autotune_ecrit(n))
            return False
        self._autotune_attente = GLib.timeout_add(700, ecrire)

    def _autotune_ecrit(self, n):
        page = (getattr(self.fenetre, "pages", None) or {}).get("doubler")
        if page is not None and hasattr(page, "autotune_par_defaut_change"):
            page.autotune_par_defaut_change(n)

    def _ecrire(self, cle, valeur, apres=None):
        def fini(r):
            ok, message = r
            if ok:
                self.add_toast(Adw.Toast(title=commun.echapper(f"{cle} enregistré"), timeout=3))
                if apres:
                    apres()
                commun.en_fil(moteur.config_effective, self._config_lue)
            else:
                self.add_toast(Adw.Toast(title=commun.echapper(f"{cle} : {message}"), timeout=6))
        commun.en_fil(lambda: moteur.ecrire_config(cle, valeur), fini)

    def changer_donnees(self):
        d = Gtk.FileDialog(title="Dossier de données", modal=True,
                           initial_folder=Gio.File.new_for_path(str(self.fenetre.donnees)))

        def fini(dlg, res):
            try:
                f = commun.chemin_de(dlg.select_folder_finish(res))
            except GLib.Error:
                return
            if f:
                manque = [n for n in ("voix", "sorties") if not (f / n).is_dir()]
                if manque:
                    self.add_toast(Adw.Toast(title=commun.echapper(
                        f"Pas de {', '.join(m + '/' for m in manque)} dans ce dossier : doubler les créera"), timeout=6))
                self._ecrire("donnees", str(f), self.fenetre.changer_donnees)
        d.select_folder(self.fenetre, None, fini)

    # --- Doublage par défaut ---------------------------------------------------------------------------------------

    def _page_doublage(self):
        p = Adw.PreferencesPage(title="Doublage", icon_name="video-x-generic-symbolic", name="doublage")
        g = Adw.PreferencesGroup(title="Réglages par défaut", description="Proposés à chaque nouveau doublage.")
        voix = self.fenetre.voix
        self.ids_voix = [v["id"] for v in voix]
        self.r_voix = Adw.ComboRow(title="Voix par défaut", model=Gtk.StringList.new([v["nom"] for v in voix] or ["—"]))
        defaut = self.fenetre.voix_defaut()
        # sans voix par défaut (plusieurs voix, aucune désignée) : rien n'est présélectionné
        if defaut in self.ids_voix:
            self.r_voix.set_selected(self.ids_voix.index(defaut))
        elif self.ids_voix:
            self.r_voix.set_selected(Gtk.INVALID_LIST_POSITION)
        self.r_voix.connect("notify::selected", lambda r, _p: 0 <= r.get_selected() < len(self.ids_voix) and self._pref(
            "voix_defaut", self.ids_voix[r.get_selected()]))
        g.add(self.r_voix)
        r_mode = Adw.ComboRow(title="Mode", model=Gtk.StringList.new([m[1] for m in MODES]))
        r_mode.set_selected(next((i for i, m in enumerate(MODES) if m[0] == self.prefs.get("mode")), 0))
        r_mode.connect("notify::selected", lambda r, _p: self._pref("mode", MODES[r.get_selected()][0]))
        g.add(r_mode)
        r_oct = Adw.ComboRow(title="Octave", model=Gtk.StringList.new([o[1] for o in OCTAVES]))
        r_oct.set_selected(next((i for i, o in enumerate(OCTAVES) if o[0] == str(self.prefs.get("octave"))), 0))
        r_oct.connect("notify::selected", lambda r, _p: self._pref("octave", OCTAVES[r.get_selected()][0]))
        g.add(r_oct)
        r_pas = Adw.SpinRow.new_with_range(10, 100, 5)
        r_pas.set_title("Pas de diffusion")
        r_pas.set_subtitle(TEXTE_PAS)
        r_pas.set_value(float(self.prefs.get("pas") or 30))
        r_pas.connect("notify::value", lambda r, _p: self._pref("pas", int(r.get_value())))
        g.add(r_pas)
        niveau = Adw.ExpanderRow(title="Niveau de la voix réglé à la main", show_enable_switch=True,
                                 subtitle="Sinon +4 dB en chant, 0 dB en parole",
                                 enable_expansion=self.prefs.get("voix_db") is not None)
        r_db = Adw.SpinRow.new_with_range(-12, 12, 0.5)
        r_db.set_title("Voix (dB)")
        r_db.set_digits(1)
        r_db.set_value(float(self.prefs.get("voix_db") if self.prefs.get("voix_db") is not None else 4))
        niveau.add_row(r_db)

        def niveau_change(*_):
            self._pref("voix_db", round(r_db.get_value(), 1) if niveau.get_enable_expansion() else None)
        niveau.connect("notify::enable-expansion", niveau_change)
        r_db.connect("notify::value", niveau_change)
        g.add(niveau)
        r_rev = Adw.SpinRow.new_with_range(-40, 0, 1)
        r_rev.set_title("Reverb (dB)")
        r_rev.set_subtitle("En chant seulement")
        r_rev.set_value(float(self.prefs.get("reverb_db") if self.prefs.get("reverb_db") is not None else -13))
        r_rev.connect("notify::value", lambda r, _p: self._pref("reverb_db", round(r.get_value(), 1)))
        g.add(r_rev)
        # options récentes de doubler, masquées tant que son aide ne les annonce pas
        caps = self.fenetre.caps
        if "--moteur" in caps:
            self.r_moteur = Adw.ComboRow(title="Moteur du chant (configuration de doubler)",
                                         subtitle="Clé « moteur » de config.json ; la parole passe toujours par "
                                                  "Seed-VC 22 kHz",
                                         model=Gtk.StringList.new([m[1] for m in moteur.MOTEURS]))
            g.add(self.r_moteur)
        if "--sans-nettoyage" in caps and "--moteur" in caps:
            from .page_doubler import CHAINES, EXPLICATIONS_CHAINES
            choix = [c for c in CHAINES if c[0] != "mesure"]
            r_ch = Adw.ComboRow(title="Chaîne proposée par défaut", model=Gtk.StringList.new([c[1] for c in choix]),
                                subtitle="Dans la page Doubler (modifiable avant chaque lancement). Chaîne d’avant : "
                                         + EXPLICATIONS_CHAINES["avant"])
            actuelle = self.prefs.get("chaine") or "defaut"
            r_ch.set_selected(next((i for i, c in enumerate(choix) if c[0] == actuelle), 0))
            r_ch.connect("notify::selected", lambda w, _p: self._pref("chaine", choix[w.get_selected()][0]))
            g.add(r_ch)
        if "--sans-paroles" in caps:
            self.r_paroles = Adw.SwitchRow(title="Paroles (configuration de doubler)", active=True,
                                           subtitle="Clé « paroles » de config.json : transcription des paroles et "
                                                    "traduction française ; la page Doubler peut les retirer d’un "
                                                    "lancement")
            g.add(self.r_paroles)
        if "--autotune" in caps:
            # clé « autotune » de doubler (0 : désactivé) ; grisée si doubler ne trouve pas x42-autotune
            self.r_autotune = Adw.ExpanderRow(title="Auto-tune (configuration de doubler)", show_enable_switch=True,
                                              subtitle=TEXTE_AUTOTUNE_DEFAUT)
            self.r_at_n = Adw.SpinRow.new_with_range(1, 100, 5)
            self.r_at_n.set_title("Intensité")
            self.r_at_n.set_subtitle("Bas : notes juste rendues plus justes ; haut : effet robotique, sauts de note")
            self.r_at_n.set_value(moteur.AUTOTUNE_DEFAUT)
            self.r_autotune.add_row(self.r_at_n)
            g.add(self.r_autotune)
        p.add(g)

        ge = Adw.PreferencesGroup(title="Enregistrement")
        self.r_micro = Adw.ComboRow(title="Micro", model=Gtk.StringList.new(["Micro par défaut du système"]))
        ge.add(self.r_micro)
        r_parole = Adw.SpinRow.new_with_range(20, 300, 5)
        r_parole.set_title("Durée d’un enregistrement de parole (s)")
        r_parole.set_value(float(self.prefs.get("duree_parole_s") or 60))
        r_parole.connect("notify::value", lambda r, _p: self._pref("duree_parole_s", int(r.get_value())))
        ge.add(r_parole)
        r_chant = Adw.SpinRow.new_with_range(30, 600, 10)
        r_chant.set_title("Durée d’une prise de chant (s)")
        r_chant.set_value(float(self.prefs.get("duree_prise_chant_s") or 120))
        r_chant.connect("notify::value", lambda r, _p: self._pref("duree_prise_chant_s", int(r.get_value())))
        ge.add(r_chant)
        externes = self.prefs.get("prises_externes") or {}
        self.r_externes = Adw.ActionRow(
            title="Autre dossier de prises de chant",
            subtitle=commun.echapper(" · ".join(f"{k} : {v}" for k, v in externes.items()) or
                                     "aucun (prises chant-*.wav faites hors de l’interface, comptées en lecture "
                                     "seule pour la voix par défaut)"))
        self.r_externes.add_suffix(commun.bouton("Choisir…", valign=Gtk.Align.CENTER, clic=self.choisir_externes))
        if externes:
            self.r_externes.add_suffix(commun.bouton(icone="edit-clear-symbolic", infobulle="Ne plus compter ce dossier",
                                                     styles=("flat",), valign=Gtk.Align.CENTER,
                                                     clic=lambda: self._externes({})))
        ge.add(self.r_externes)
        p.add(ge)

        gc = Adw.PreferencesGroup(title="Pistes séparées",
                                  description="Beat seul et voix seules se lisent sur l’image de la vidéo doublée "
                                              "grâce à des copies préparées à l’avance, en tâche de fond (environ "
                                              "150 Mo par chanson pour trois pistes). Les moins récemment lues sont "
                                              "effacées au-delà de cette taille ; celles du doublage ouvert ou en "
                                              "lecture, jamais.")
        from .. import pistes
        r_cache = Adw.SpinRow.new_with_range(0.5, 50, 0.5)
        r_cache.set_title("Taille du cache des pistes (Go)")
        r_cache.set_digits(1)
        r_cache.set_value(pistes.limite_cache_video_mo(self.prefs) / 1024)
        r_cache.connect("notify::value", lambda r, _p: self._pref("cache_pistes_mo", round(r.get_value() * 1024)))
        gc.add(r_cache)
        p.add(gc)
        return p

    def choisir_externes(self):
        d = Gtk.FileDialog(title="Dossier de prises de chant", modal=True)

        def fini(dlg, res):
            try:
                f = commun.chemin_de(dlg.select_folder_finish(res))
            except GLib.Error:
                return
            voix = self.fenetre.voix_defaut()
            if f and voix:
                self._externes({voix: str(f)})
        d.select_folder(self.fenetre, None, fini)

    def _externes(self, valeur):
        self._pref("prises_externes", valeur)
        self.r_externes.set_subtitle(commun.echapper(" · ".join(f"{k} : {v}" for k, v in valeur.items()) or "aucun"))
        self.add_toast(Adw.Toast(title="Prises de chant : réglage enregistré", timeout=3))

    def _page_visualisations(self):
        p = Adw.PreferencesPage(title="Visualisations", icon_name="view-reveal-symbolic", name="visualisations")
        g = Adw.PreferencesGroup(title="Visualisations du son",
                                 description="Sous le lecteur des résultats (et dans l’écoute à l’aveugle). Les "
                                             "calculs se font une fois, sous nice, puis restent en cache.")
        for cle, titre, sous_titre in (
                ("vis_melodie", "Courbe de mélodie", "Origine, consigne et voix convertie, sur une échelle de notes"),
                ("vis_spectre", "Spectrogramme", "Défile avec la lecture ; montre la piste écoutée (doublage, "
                                                 "original, beat, voix seule)"),
                ("vis_onde", "Forme d’onde", "Sous la frise d’octave, avec le curseur de lecture"),
                ("vis_vumetre", "Vu-mètre", "Niveau par bande de fréquence, animé pendant la lecture"),
                ("melodie_hz", "Mélodie en Hz", "Sinon en notes (do, ré, mi…, la3 = 440 Hz)"),
                ("melodie_autotune", "Courbe de la version auto-tune",
                 "Sur la mélodie, en ambre, pour les doublages qui ont une version auto-tune"),
                ("paroles", "Paroles", "Ligne en cours et suivante sous le lecteur : paroles de doubler, sinon "
                                       "sous-titres de la vidéo")):
            r = Adw.SwitchRow(title=titre, subtitle=sous_titre, active=bool(self.prefs.get(cle)))
            r.connect("notify::active", lambda w, _p, c=cle: self.fenetre.regler_visualisation(c, w.get_active()))
            g.add(r)
        p.add(g)
        return p

    def _micros(self, liste):
        self.micros = liste
        self.r_micro.set_model(Gtk.StringList.new([d for _n, d in liste]))
        noms = [n for n, _d in liste]
        if self.prefs.get("micro") in noms:
            self.r_micro.set_selected(noms.index(self.prefs.get("micro")))
        self.r_micro.connect("notify::selected", lambda r, _p: self._pref("micro", noms[r.get_selected()]))

    def _pref(self, cle, valeur):
        self.prefs = chemins.modifier_preference(cle, valeur)
        self.fenetre.preferences_changees()
