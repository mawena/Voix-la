"""Page Doubler : choisir des vidéos (sélecteur ou glisser-déposer), un extrait facultatif par vidéo, les réglages, et
lancer. Les vidéos qui ont le même extrait partent dans UN seul appel doubler, donc une seule session Kaggle."""

import subprocess
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .. import chemins, formats, moteur, rapports, systeme, travaux
from . import commun
from .lecteur import Lecteur

MODES = [("auto", "Automatique"), ("chant", "Chant"), ("parole", "Parole")]
OCTAVES = [("auto", "Automatique"), ("0", "0 (aucun décalage)"), ("-12", "−12 (une octave plus bas)"),
           ("+12", "+12 (une octave plus haut)")]
EXT_VIDEOS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
TEXTE_OCTAVE = ("Automatique : en chant, un seul décalage par chanteur, choisi d’après la hauteur (l’empreinte de la "
                "voix départage les cas ambigus) ; un duo, retenu à quatre conditions, a un décalage par voix. "
                "Sinon imposée partout")
TEXTE_PAS = ("30 par défaut ; 50 : environ 40 % de temps GPU de conversion en plus. Avec YingMusic, 50 ou 100 "
             "n’apportent rien de mesurable")
MOTEURS = [("", "Par défaut"), ("yingmusic", "YingMusic-SVC"), ("seedvc", "Seed-VC 44 kHz")]
# Chaîne de conversion du chant : raccourcis vers les réglages moteur / nettoyage
CHAINES = [("defaut", "Par défaut"), ("avant", "Chaîne d’avant"), ("mesure", "Sur mesure")]
EXPLICATIONS_CHAINES = {
    "defaut": "Voix principale et chœurs séparés, reverb retirée, moteur de la configuration (YingMusic-SVC) : "
              "environ deux fois plus de GPU",
    "avant": "Seed-VC 44 kHz sur la voix isolée complète, sans nettoyage : plus rapide",
    "mesure": "Moteur et nettoyage choisis ci-dessous",
}


def sonder_duree(chemin):
    """Durée d'une vidéo par ffprobe (lecture seule ; les vidéos téléchargées ne sont jamais exécutées)."""
    try:
        r = subprocess.run(["nice", "-n", "10", "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                            "csv=p=0", str(chemin)], capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def duree_audio(v):
    """Secondes de son à traiter : l'extrait, sinon la vidéo entière."""
    if v.get("extrait"):
        debut, duree = v["extrait"]
        if duree:
            return duree
        if v.get("duree_s"):
            return max(0.0, v["duree_s"] - debut)
    return v.get("duree_s") or 0.0


def texte_extrait(extrait, duree_totale=None):
    if not extrait:
        return "vidéo entière"
    debut, duree = extrait
    if duree:
        return f"extrait {formats.chrono(debut)} → {formats.chrono(debut + duree)} ({formats.duree(duree)})"
    return f"de {formats.chrono(debut)} à la fin"


class DialogueExtrait(Adw.Dialog):
    """Petit lecteur pour régler l'extrait d'une vidéo : « Début ici », « Fin ici », écouter l'extrait."""

    def __init__(self, video, valider):
        super().__init__(title="Extrait", content_width=760, content_height=640)
        self.video, self.valider = video, valider
        debut, duree = video.get("extrait") or (None, None)
        vue = Adw.ToolbarView()
        entete = Adw.HeaderBar()
        entete.set_title_widget(Adw.WindowTitle(title="Extrait", subtitle=Path(video["chemin"]).name))
        vue.add_top_bar(entete)
        b = commun.boite(espace=12, marges=12)
        self.lecteur = Lecteur(hauteur=300)
        self.lecteur.definir({"B": (video["chemin"], 0.0, "vidéo")})
        if debut:
            self.lecteur.aller_a_absolu(debut)   # montre l'image du début de l'extrait
        b.append(self.lecteur)

        grille = Gtk.Grid(column_spacing=8, row_spacing=8, halign=Gtk.Align.CENTER)
        self.e_debut = Gtk.Entry(width_chars=9, placeholder_text="0:00", text=formats.chrono_precis(debut) if debut is not None else "")
        self.e_fin = Gtk.Entry(width_chars=9, placeholder_text="fin",
                               text=formats.chrono_precis(debut + duree) if debut is not None and duree else "")
        for e in (self.e_debut, self.e_fin):
            e.add_css_class("chiffres")
            e.connect("changed", lambda *_: self._maj())
        grille.attach(Gtk.Label(label="Début", xalign=1), 0, 0, 1, 1)
        grille.attach(self.e_debut, 1, 0, 1, 1)
        grille.attach(commun.bouton("Début ici", "selection-start-symbolic", "Prendre la position du lecteur",
                                    clic=lambda: self._ici(self.e_debut)), 2, 0, 1, 1)
        grille.attach(Gtk.Label(label="Fin", xalign=1), 0, 1, 1, 1)
        grille.attach(self.e_fin, 1, 1, 1, 1)
        grille.attach(commun.bouton("Fin ici", "selection-end-symbolic", "Prendre la position du lecteur",
                                    clic=lambda: self._ici(self.e_fin)), 2, 1, 1, 1)
        b.append(grille)
        self.info = commun.legende("")
        self.info.set_halign(Gtk.Align.CENTER)
        b.append(self.info)
        b.append(commun.legende("L’extrait commence sur l’image clé qui précède le début (l’image n’est pas réencodée) : "
                                "il peut démarrer une ou deux secondes plus tôt."))
        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        actions.append(commun.bouton("Écouter l’extrait", "media-playback-start-symbolic", clic=self._ecouter))
        actions.append(commun.bouton("Vidéo entière", clic=lambda: self._fermer(None)))
        self.b_valider = commun.bouton("Valider", styles=("suggested-action",), clic=self._valider)
        actions.append(self.b_valider)
        b.append(actions)
        vue.set_content(b)
        self.set_child(vue)
        self.connect("closed", lambda *_: self.lecteur.liberer())
        self._maj()

    def _ici(self, entree):
        entree.set_text(formats.chrono_precis(self.lecteur.position()))

    def _bornes(self):
        debut = formats.lire_secondes(self.e_debut.get_text())
        fin = formats.lire_secondes(self.e_fin.get_text())
        return debut, fin

    def _maj(self):
        debut, fin = self._bornes()
        ok = True
        if self.e_debut.get_text().strip() and debut is None:
            ok = False
        if self.e_fin.get_text().strip() and fin is None:
            ok = False
        if ok and debut is not None and fin is not None and fin <= debut:
            ok = False
        if not ok:
            self.info.set_label("Bornes illisibles ou fin avant le début (écrire 1:05 ou 65).")
        elif debut is None and fin is None:
            self.info.set_label("Aucune borne : la vidéo entière sera doublée.")
        else:
            d = (fin - (debut or 0)) if fin is not None else None
            self.info.set_label(texte_extrait(((debut or 0.0), d)).capitalize())
        self.b_valider.set_sensitive(ok)

    def _ecouter(self):
        debut, fin = self._bornes()
        debut = debut or 0.0
        self.lecteur.lire_extrait(debut, fin if fin is not None else 10 ** 9)

    def _valider(self):
        debut, fin = self._bornes()
        if debut is None and fin is None:
            self._fermer(None)
        else:
            debut = debut or 0.0
            self._fermer((round(debut, 1), round(fin - debut, 1) if fin is not None else None))

    def _fermer(self, extrait):
        self.lecteur.liberer()
        self.valider(extrait)
        self.close()


class PageDoubler(commun.Page):
    def __init__(self, fenetre):
        super().__init__(fenetre, "Doubler", "doubler")
        self.videos = []
        self.modele = rapports.MODELE_DEFAUT
        self.entete.pack_start(commun.bouton(icone="list-add-symbolic", infobulle="Ajouter des vidéos",
                                             clic=self.choisir))

        self.banniere = Adw.Banner(button_label="Réglages")
        self.banniere.connect("button-clicked", lambda *_: fenetre.ouvrir_reglages())
        page = Adw.PreferencesPage()

        # Vidéos
        self.g_videos = Adw.PreferencesGroup(title="Vidéos",
                                             description="Une ou plusieurs vidéos : celles qui ont le même extrait "
                                                         "partent ensemble dans une seule session Kaggle.")
        self.zone = commun.boite(espace=8, css_classes=["zone-depot"])
        self.zone.append(Gtk.Image(icon_name="folder-videos-symbolic", pixel_size=40, css_classes=["dim-label"]))
        self.zone.append(Gtk.Label(label="Glissez des vidéos ici", css_classes=["title-4"]))
        self.zone.append(commun.legende("mp4, mkv, webm, mov… : chanson, duo, rap ou vidéo parlée"))
        self.zone.get_last_child().set_halign(Gtk.Align.CENTER)
        choisir = commun.bouton("Choisir des vidéos…", "document-open-symbolic", styles=("pill",), clic=self.choisir,
                                halign=Gtk.Align.CENTER)
        self.zone.append(choisir)
        self.g_videos.add(self.zone)
        self.liste = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE, margin_top=12)
        self.liste.set_visible(False)
        self.g_videos.add(self.liste)
        page.add(self.g_videos)

        # Réglages
        g = Adw.PreferencesGroup(title="Réglages")
        self.r_voix = Adw.ComboRow(title="Voix", subtitle="Voix clonée qui remplace celle de la vidéo")
        self.r_voix.connect("notify::selected", lambda *_: self._maj())
        g.add(self.r_voix)
        self.r_mode = Adw.ComboRow(title="Mode", model=Gtk.StringList.new([m[1] for m in MODES]),
                                   subtitle="Automatique : chant s’il y a une musique sous la voix et que la voix "
                                            "chante ou rappe, sinon parole")
        self.r_mode.connect("notify::selected", lambda *_: self._maj())
        g.add(self.r_mode)
        self.r_octave = Adw.ComboRow(title="Octave", model=Gtk.StringList.new([o[1] for o in OCTAVES]),
                                     subtitle=TEXTE_OCTAVE)
        g.add(self.r_octave)
        self.r_pas = Adw.SpinRow.new_with_range(10, 100, 5)
        self.r_pas.set_title("Pas de diffusion")
        self.r_pas.set_subtitle(TEXTE_PAS)
        self.r_pas.connect("notify::value", lambda *_: self._maj())
        g.add(self.r_pas)
        self.r_niveau = Adw.ExpanderRow(title="Niveau de la voix réglé à la main", show_enable_switch=True,
                                        subtitle="Sinon +4 dB en chant et 0 dB en parole, par rapport à la voix "
                                                 "d’origine")
        self.r_voix_db = Adw.SpinRow.new_with_range(-12, 12, 0.5)
        self.r_voix_db.set_title("Voix (dB)")
        self.r_voix_db.set_digits(1)
        self.r_niveau.add_row(self.r_voix_db)
        g.add(self.r_niveau)
        self.r_reverb = Adw.SpinRow.new_with_range(-40, 0, 1)
        self.r_reverb.set_title("Reverb (dB)")
        self.r_reverb.set_subtitle("Sous la voix sèche, en chant seulement ; −13 par défaut")
        g.add(self.r_reverb)
        self.r_mix_v1 = Adw.SwitchRow(title="Ancien mixage (v1)",
                                      subtitle="Sans suivi des nuances de la voix d’origine ni dé-esseur")
        g.add(self.r_mix_v1)
        self.r_chaine = Adw.ComboRow(title="Chaîne de conversion", model=Gtk.StringList.new([c[1] for c in CHAINES]),
                                     subtitle=EXPLICATIONS_CHAINES["defaut"])
        self.r_chaine.set_visible(False)
        self.r_chaine.connect("notify::selected", lambda *_: self._chaine_changee())
        g.add(self.r_chaine)
        self.r_moteur = Adw.ComboRow(title="Moteur du chant", model=Gtk.StringList.new([m[1] for m in MOTEURS]),
                                     subtitle="Par défaut : celui de la configuration (YingMusic-SVC sinon) ; la "
                                              "parole passe toujours par Seed-VC 22 kHz")
        self.r_moteur.set_visible(False)
        g.add(self.r_moteur)
        self.r_nettoyage = Adw.SwitchRow(title="Sans nettoyage",
                                         subtitle="Chant : convertir la voix isolée complète, sans séparer voix "
                                                  "principale et chœurs ni retirer la reverb (plus rapide)")
        self.r_nettoyage.set_visible(False)
        g.add(self.r_nettoyage)
        self.r_moteur.connect("notify::selected", lambda *_: self._maj())
        self.r_nettoyage.connect("notify::active", lambda *_: self._maj())
        page.add(g)

        # Lancer
        g_lancer = Adw.PreferencesGroup()
        barre = Gtk.Box(spacing=12, margin_top=6)
        self.resume = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        barre.append(self.resume)
        self.b_lancer = commun.bouton("Lancer", "media-playback-start-symbolic", styles=("suggested-action", "pill"),
                                      clic=self.lancer)
        self.b_lancer.set_valign(Gtk.Align.CENTER)
        barre.append(self.b_lancer)
        g_lancer.add(barre)
        self.note = commun.legende("")
        g_lancer.add(self.note)
        page.add(g_lancer)

        corps = commun.boite(espace=0)
        corps.append(self.banniere)
        corps.append(page)
        page.set_vexpand(True)
        self.contenu(corps)

        cible = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        cible.connect("drop", self._depose)
        cible.connect("enter", lambda *_: (self.zone.add_css_class("survol"), Gdk.DragAction.COPY)[1])
        cible.connect("leave", lambda *_: self.zone.remove_css_class("survol"))
        corps.add_controller(cible)

        self._appliquer_preferences()
        fenetre.ecouter("voix", self._voix_changees)
        fenetre.ecouter("donnees", self._donnees_changees)
        fenetre.ecouter("preferences", self._appliquer_preferences)
        self._voix_changees()
        self._maj()

    # --- préférences et voix ---------------------------------------------------------------------------------------

    def _appliquer_preferences(self):
        p = self.fenetre.prefs
        self.r_mode.set_selected(next((i for i, m in enumerate(MODES) if m[0] == p.get("mode")), 0))
        self.r_octave.set_selected(next((i for i, o in enumerate(OCTAVES) if o[0] == str(p.get("octave"))), 0))
        self.r_pas.set_value(float(p.get("pas") or 30))
        self.r_niveau.set_enable_expansion(p.get("voix_db") is not None)
        self.r_voix_db.set_value(float(p.get("voix_db") if p.get("voix_db") is not None else 4.0))
        self.r_reverb.set_value(float(p.get("reverb_db") if p.get("reverb_db") is not None else -13))
        if hasattr(self, "r_moteur"):
            self.r_moteur.set_selected(next((i for i, m in enumerate(MOTEURS) if m[0] == (p.get("moteur") or "")), 0))
            self.r_nettoyage.set_active(bool(p.get("sans_nettoyage")))
            chaine = p.get("chaine") or ("avant" if p.get("moteur") == "seedvc" and p.get("sans_nettoyage") else
                                         "mesure" if p.get("moteur") or p.get("sans_nettoyage") else "defaut")
            self.r_chaine.set_selected(next((i for i, c in enumerate(CHAINES) if c[0] == chaine), 0))

    def _voix_changees(self):
        voix = self.fenetre.voix
        actuelle = self._voix_choisie()
        self._ids_voix = [v["id"] for v in voix]
        self.r_voix.set_model(Gtk.StringList.new([f"{v['nom']} ({v['id']})" if v["nom"].lower() != v["id"]
                                                  else v["nom"] for v in voix] or ["aucune voix"]))
        cible = actuelle if actuelle in self._ids_voix else self.fenetre.voix_defaut()
        # sans voix par défaut (plusieurs voix, aucune désignée) : rien n'est présélectionné, « Lancer » attend un choix
        if cible in self._ids_voix:
            self.r_voix.set_selected(self._ids_voix.index(cible))
        elif voix:
            self.r_voix.set_selected(Gtk.INVALID_LIST_POSITION)
        self.r_voix.set_sensitive(bool(voix))
        self._maj()

    def _voix_choisie(self):
        ids = getattr(self, "_ids_voix", [])
        i = self.r_voix.get_selected()
        return ids[i] if 0 <= i < len(ids) else None

    def montrer(self):
        super().montrer()
        self._donnees_changees()

    def _donnees_changees(self):
        self.g_videos.set_description(
            "Une ou plusieurs vidéos, chacune avec son extrait facultatif : elles partent ensemble dans une seule "
            "session Kaggle." if "extrait_par_video" in self.fenetre.caps else
            "Une ou plusieurs vidéos : celles qui ont le même extrait partent ensemble dans une seule session Kaggle.")
        self.r_mix_v1.set_visible("--mix-v1" in self.fenetre.caps)
        # options récentes de doubler : masquées tant que son aide ne les annonce pas
        self.r_chaine.set_visible("--moteur" in self.fenetre.caps and "--sans-nettoyage" in self.fenetre.caps)
        self._chaine_changee()
        self._verifier_kaggle()
        self._charger_modele()

    def _chaine_changee(self):
        """« Par défaut » et « Chaîne d'avant » fixent moteur et nettoyage ; « Sur mesure » montre les deux réglages."""
        caps = self.fenetre.caps
        if not self.r_chaine.get_visible():
            self.r_moteur.set_visible("--moteur" in caps)
            self.r_nettoyage.set_visible("--sans-nettoyage" in caps)
            self._maj()
            return
        chaine = CHAINES[self.r_chaine.get_selected()][0]
        self.r_chaine.set_subtitle(EXPLICATIONS_CHAINES[chaine])
        if chaine == "defaut":
            self.r_moteur.set_selected(0)
            self.r_nettoyage.set_active(False)
        elif chaine == "avant":
            self.r_moteur.set_selected(next(i for i, m in enumerate(MOTEURS) if m[0] == "seedvc"))
            self.r_nettoyage.set_active(True)
        sur_mesure = chaine == "mesure"
        self.r_moteur.set_visible(sur_mesure)
        self.r_nettoyage.set_visible(sur_mesure)
        self._maj()

    def _options_chaine(self):
        """(moteur, sans_nettoyage) choisis, que les lignes soient visibles ou fixées par la chaîne."""
        caps = self.fenetre.caps
        moteur_ = MOTEURS[self.r_moteur.get_selected()][0] if "--moteur" in caps else ""
        sans = self.r_nettoyage.get_active() if "--sans-nettoyage" in caps else False
        return moteur_, sans

    def _estimer(self, durees, reglages):
        nettoyage = "--sans-nettoyage" in self.fenetre.caps and not reglages.get("sans_nettoyage")
        return rapports.estimer(durees, reglages.get("pas", 30), self.modele, nettoyage=nettoyage,
                                mode=reglages.get("mode", "auto"), moteur=reglages.get("moteur") or None)

    def _charger_modele(self):
        sorties = chemins.dossier_sorties(self.fenetre.donnees)

        def fini(m):
            self.modele = m
            self._maj()
        commun.en_fil(lambda: rapports.modele_durees(rapports.lister_resultats(sorties)), fini)

    def _verifier_kaggle(self):
        def lire():
            return systeme.jeton_kaggle_present(), moteur.config_effective()

        def fini(r):
            jeton, conf = r
            compte = ((conf or {}).get("kaggle") or {}).get("compte")
            if not jeton:
                self.banniere.set_title("Jeton Kaggle absent (~/.kaggle/access_token) : doubler ne pourra pas lancer "
                                        "de session GPU")
                self.banniere.set_revealed(True)
            elif not compte:
                self.banniere.set_title("Compte Kaggle non configuré : renseignez-le dans les réglages")
                self.banniere.set_revealed(True)
            else:
                self.banniere.set_revealed(False)
        commun.en_fil(lire, fini)

    # --- vidéos ------------------------------------------------------------------------------------------------------

    def choisir(self):
        dossier = chemins.dossier_videos(self.fenetre.donnees)
        if not dossier.is_dir():
            dossier = chemins.dossier_telechargements()
        d = Gtk.FileDialog(title="Choisir des vidéos", modal=True,
                           filters=commun.liste_filtres(commun.filtre_fichiers(*commun.FILTRE_VIDEOS),
                                                        commun.filtre_fichiers("Tous les fichiers", (), ("*",))),
                           initial_folder=Gio.File.new_for_path(str(dossier)))

        def fini(dlg, res):
            try:
                fichiers = dlg.open_multiple_finish(res)
            except GLib.Error:
                return
            self.ajouter([commun.chemin_de(fichiers.get_item(i)) for i in range(fichiers.get_n_items())])
        d.open_multiple(self.fenetre, None, fini)

    def _depose(self, _cible, valeur, _x, _y):
        self.zone.remove_css_class("survol")
        chemins_ = [commun.chemin_de(f) for f in valeur.get_files()]
        refuses = [p for p in chemins_ if p and p.suffix.lower() not in EXT_VIDEOS]
        self.ajouter([p for p in chemins_ if p and p.suffix.lower() in EXT_VIDEOS])
        if refuses:
            self.fenetre.toast(f"Ignoré (pas une vidéo) : {', '.join(p.name for p in refuses[:3])}")
        return True

    def ajouter(self, chemins_, extrait=None):
        deja = {v["chemin"] for v in self.videos}
        for p in chemins_:
            if not p or not p.is_file() or str(p) in deja:
                continue
            v = {"chemin": str(p), "duree_s": None, "extrait": extrait}
            self.videos.append(v)
            commun.en_fil(lambda v=v: sonder_duree(v["chemin"]), lambda d, v=v: self._duree_lue(v, d))
        self._rendre()

    def _duree_lue(self, v, d):
        v["duree_s"] = d
        self._rendre()

    def _rendre(self):
        commun.vider(self.liste)
        self.liste.set_visible(bool(self.videos))
        self.zone.set_visible(not self.videos)
        voix = self._voix_choisie()
        sorties = chemins.dossier_sorties(self.fenetre.donnees)
        for v in self.videos:
            p = Path(v["chemin"])
            r = Adw.ActionRow(title=commun.echapper(p.stem), title_lines=2)
            morceaux = [formats.chrono(v["duree_s"]) if v["duree_s"] else "durée…", texte_extrait(v["extrait"])]
            if voix and (sorties / moteur.nom_dossier(p.resolve(), v["extrait"]) / "travail" / voix /
                         "rapport.json").exists():
                morceaux.append("déjà doublée avec cette voix : sera refaite")
            r.set_subtitle(commun.echapper(" · ".join(morceaux)))
            r.add_prefix(Gtk.Image(icon_name="video-x-generic-symbolic"))
            b_ext = commun.bouton(icone="edit-cut-symbolic", infobulle="Choisir un extrait", styles=("flat",),
                                  clic=lambda v=v: self.regler_extrait(v), valign=Gtk.Align.CENTER)
            b_suppr = commun.bouton(icone="list-remove-symbolic", infobulle="Retirer de la liste", styles=("flat",),
                                    clic=lambda v=v: self.retirer(v), valign=Gtk.Align.CENTER)
            r.add_suffix(b_ext)
            r.add_suffix(b_suppr)
            self.liste.append(r)
        if self.videos:
            plus = Adw.ActionRow(title="Ajouter d’autres vidéos…", activatable=True)
            plus.add_prefix(Gtk.Image(icon_name="list-add-symbolic"))
            plus.connect("activated", lambda *_: self.choisir())
            self.liste.append(plus)
        self._maj()

    def retirer(self, v):
        self.videos.remove(v)
        self._rendre()

    def regler_extrait(self, v):
        def valider(extrait):
            v["extrait"] = extrait
            self._rendre()
        DialogueExtrait(v, valider).present(self.fenetre)

    # --- lancement ---------------------------------------------------------------------------------------------------

    def reglages(self):
        mode = MODES[self.r_mode.get_selected()][0]
        r = {"voix": self._voix_choisie(), "mode": mode, "octave": OCTAVES[self.r_octave.get_selected()][0],
             "pas": int(self.r_pas.get_value()),
             "voix_db": round(self.r_voix_db.get_value(), 1) if self.r_niveau.get_enable_expansion() else None,
             "reverb_db": round(self.r_reverb.get_value(), 1)}
        if self.r_mix_v1.get_visible() and self.r_mix_v1.get_active():
            r["mix_v1"] = True
        moteur_, sans = self._options_chaine()
        if moteur_:
            r["moteur"] = moteur_
        if sans:
            r["sans_nettoyage"] = True
        return r

    def _maj(self):
        groupes = moteur.grouper_par_extrait(self.videos, self.fenetre.caps)
        n = len(self.videos)
        reglages = self.reglages() if hasattr(self, "r_pas") else {}
        if not n:
            self.resume.set_label("Aucune vidéo choisie")
        else:
            est = sum(sum(self._estimer([duree_audio(v) for v in vids], reglages).values()) for _e, vids in groupes)
            sessions = "une seule session Kaggle" if len(groupes) == 1 else f"{len(groupes)} sessions Kaggle (une par extrait)"
            inconnue = any(v["duree_s"] is None and not (v.get("extrait") and v["extrait"][1]) for v in self.videos)
            self.resume.set_markup(f"<b>{formats.pluriel(n, 'vidéo')}</b> · {sessions} · "
                                   f"{'durée en cours de lecture' if inconnue else formats.duree_courte(est)}")
        if n and len(groupes) > 1:
            self.note.set_label("Les vidéos ont des extraits différents : doubler applique le même extrait à toutes les "
                                "vidéos d’un appel, donc un appel (et une session Kaggle) par extrait, l’un après "
                                "l’autre. Pour une seule session, donnez le même extrait à toutes.")
        else:
            self.note.set_label("Le travail part dans la file (onglet Travaux) : il continue si vous fermez la fenêtre.")
        self.b_lancer.set_sensitive(bool(n) and bool(self._voix_choisie()))

    def lancer(self):
        if not self.videos or not self._voix_choisie():
            return
        reglages = self.reglages()
        demandes = []
        for extrait, vids in moteur.grouper_par_extrait(self.videos, self.fenetre.caps):
            est = self._estimer([duree_audio(v) for v in vids], reglages)
            demandes.append(travaux.demande_doublage(
                [{"chemin": v["chemin"], "duree_s": v["duree_s"], "extrait": list(v["extrait"]) if v["extrait"] else None}
                 for v in vids], reglages, self.fenetre.donnees, estimations=est, extrait=extrait))
        self.fenetre.ajouter_travaux(demandes)
        n = len(self.videos)
        self.videos = []
        self._rendre()
        self.fenetre.toast(f"{formats.pluriel(n, 'vidéo')} dans la file", "Voir", lambda: self.fenetre.aller("travaux"))
        self.fenetre.aller("travaux")

    # --- essais ------------------------------------------------------------------------------------------------------

    def scenario(self, action):
        if action == "exemple":
            vids = sorted(chemins.dossier_videos(self.fenetre.donnees).glob("*.mp4"))[:1] or \
                sorted(chemins.dossier_videos(chemins.DONNEES_DEFAUT).glob("*.mp4"))[:1]
            self.ajouter(vids, extrait=(60.0, 40.0))
        elif action == "chaine-avant":
            self.scenario("exemple")
            self.r_chaine.set_selected(next(i for i, c in enumerate(CHAINES) if c[0] == "avant"))
        elif action == "chaine-mesure":
            self.scenario("exemple")
            self.r_chaine.set_selected(next(i for i, c in enumerate(CHAINES) if c[0] == "mesure"))
        elif action == "extrait" and self.videos:
            self.regler_extrait(self.videos[0])
        elif action == "exemple-extrait":
            self.scenario("exemple")
            GLib.timeout_add(600, lambda: (self.scenario("extrait"), False)[1])
