"""Page Voix : la liste des voix (durée de la référence, hauteur de parole, alertes de voix.json), écouter la
référence, voix par défaut, ajouter (depuis un fichier ou en s'enregistrant), renommer, mettre à la corbeille ; et les
prises de chant pour l'affinage du modèle (total cumulé face à l'objectif de 5 à 10 min), et « Affiner ma voix sur mon
chant… » : un travail « affinage » dans la file des travaux (commande affiner, voixla/affinage.py), activé par la clé
« affinage_pret » de config.json ; un badge « affinée » marque une voix qui a un point de contrôle."""

import json
import os
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .. import affinage, chemins, controle, formats, moteur, travaux
from . import commun
from .enregistreur import DialogueEnregistrement
from .lecteur import media
from .lecture import Apercu


class PageVoix(commun.Page):
    def __init__(self, fenetre):
        super().__init__(fenetre, "Voix", "voix")
        self.lecture = None
        self.bouton_lecture = None
        # préécoute d'une référence : coupe la musique en cours, et s'arrête si une autre lecture commence
        self._apercu = Apercu(self.arreter_lecture)
        self.voix_chant = None
        self._cache_prises = {}          # évaluations des prises (affinage.evaluer_prise), tant que le fichier ne change pas
        self._utile_s = 0.0
        self._ecartees = 0
        menu = Gio.Menu()
        menu.append("Depuis un fichier audio ou vidéo…", "voix.fichier")
        menu.append("M’enregistrer au micro…", "voix.enregistrer")
        actions = self._groupe = Gio.SimpleActionGroup()
        for nom, rappel in (("fichier", self.depuis_fichier), ("enregistrer", self.enregistrer_parole)):
            a = Gio.SimpleAction.new(nom, None)
            a.connect("activate", lambda *_a, r=rappel: r())
            actions.add_action(a)
        self.insert_action_group("voix", actions)
        ajouter = Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=menu, tooltip_text="Ajouter une voix")
        self.entete.pack_start(ajouter)
        self.entete.pack_end(commun.bouton(icone="folder-open-symbolic", infobulle="Ouvrir le dossier des voix",
                                           clic=lambda: commun.ouvrir_dossier(
                                               fenetre, chemins.dossier_voix(fenetre.donnees))))

        self.page = Adw.PreferencesPage()
        self.g_voix = Adw.PreferencesGroup(
            title="Voix", description="Ajoutez une voix depuis un fichier audio ou vidéo (ou glissez-le sur cette page), "
                                      "ou enregistrez-vous au micro. Une voix = un dossier voix/&lt;nom&gt;/ : "
                                      "reference.wav (20 à 25 s de parole) et voix.json. Seule la référence de la voix "
                                      "choisie part sur Kaggle.")
        # bouton visible à côté du titre (le « + » de la barre de titre seul passait inaperçu)
        bouton_ajout = Gtk.MenuButton(menu_model=menu, valign=Gtk.Align.CENTER, css_classes=["suggested-action"],
                                      tooltip_text="Depuis un fichier audio ou vidéo, ou au micro")
        bouton_ajout.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Ajouter une voix"))
        self.g_voix.set_header_suffix(bouton_ajout)
        self.page.add(self.g_voix)
        cible = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        cible.connect("drop", self._depose)
        self.page.add_controller(cible)
        self.g_chant = Adw.PreferencesGroup(
            title="Chant pour l’affinage",
            description="Prises de chant (48 kHz) pour affiner plus tard le modèle sur votre timbre : 5 à 10 min au "
                        "total.")
        # séance guidée de prises chantées (gui/seance.py), empilée par-dessus cette page : pas d'onglet de plus
        b_seance = commun.bouton("Enregistrer ma voix chantée…", "media-record-symbolic", styles=("suggested-action",),
                                 infobulle="Séance guidée : notes, chansons, import des fichiers du téléphone",
                                 clic=self.ouvrir_seance, valign=Gtk.Align.CENTER)
        self.g_chant.set_header_suffix(b_seance)
        self.r_voix_chant = Adw.ComboRow(title="Voix")
        self.r_voix_chant.connect("notify::selected", lambda *_: self._voix_chant_changee())
        self.g_chant.add(self.r_voix_chant)
        self.r_total = Adw.ActionRow(title="Total cumulé")
        self.barre_total = Gtk.LevelBar(min_value=0, max_value=600, valign=Gtk.Align.CENTER, width_request=160)
        self.barre_total.add_offset_value("minimum", 300)
        self.barre_total.add_offset_value("objectif", 600)
        self.r_total.add_suffix(self.barre_total)
        self.g_chant.add(self.r_total)
        enr = Adw.ActionRow(title="Enregistrer une prise", subtitle="2 min par prise, contrôle de qualité ensuite",
                            activatable=True)
        enr.add_prefix(Gtk.Image(icon_name="media-record-symbolic"))
        enr.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        enr.connect("activated", lambda *_: self.enregistrer_chant())
        self.g_chant.add(enr)
        self.page.add(self.g_chant)
        self._construire_affinage()    # entre « Chant pour l’affinage » et la liste des prises
        self.g_prises = Adw.PreferencesGroup(title="Prises")
        self.page.add(self.g_prises)
        self.contenu(self.page)
        self._lignes_voix, self._lignes_prises = [], []
        fenetre.ecouter("voix", self.rendre)
        fenetre.ecouter("travaux", self._maj_affinage)    # le bouton se grise tant qu'un affinage de la voix est dans la file
        self.rendre()

    def montrer(self):
        super().montrer()
        self.fenetre.recharger_voix()

    def actualiser(self):
        self.fenetre.recharger_voix()

    def cacher(self):
        super().cacher()
        self.arreter_lecture()

    def fermer(self):
        self.arreter_lecture()

    # --- liste des voix ----------------------------------------------------------------------------------------------

    def rendre(self):
        for r in self._lignes_voix:
            self.g_voix.remove(r)
        self._lignes_voix = []
        defaut = self.fenetre.voix_defaut()
        if not self.fenetre.voix:
            r = Adw.ActionRow(title="Aucune voix", subtitle="Ajoutez-en une avec le bouton +")
            self.g_voix.add(r)
            self._lignes_voix.append(r)
        for v in self.fenetre.voix:
            self.g_voix.add(self._ligne_voix(v, v["id"] == defaut))
        ids = [v["id"] for v in self.fenetre.voix]
        # avant set_model : le nouveau modèle sélectionne sa première ligne, ce qui changerait self.voix_chant
        cible = self.voix_chant if self.voix_chant in ids else defaut
        self._ids_chant = ids
        self.r_voix_chant.set_model(Gtk.StringList.new([v["nom"] for v in self.fenetre.voix] or ["aucune voix"]))
        if cible in ids:
            self.r_voix_chant.set_selected(ids.index(cible))
        elif ids:
            self.r_voix_chant.set_selected(Gtk.INVALID_LIST_POSITION)   # plusieurs voix, aucune par défaut
        self._voix_chant_changee()

    def _ligne_voix(self, v, defaut):
        e = Adw.ExpanderRow(title=commun.echapper(v["nom"]))
        morceaux = []
        if v.get("duree_s"):
            morceaux.append(f"référence {formats.nombre(v['duree_s'])} s")
        if v.get("hauteur_hz"):
            morceaux.append(f"parole {formats.nombre(v['hauteur_hz'], 0)} Hz" + (f" ({v['note']})" if v.get("note") else ""))
        if v.get("date"):
            morceaux.append(formats.date_iso(v["date"]))
        morceaux.append(f"dossier {v['id']}")
        e.set_subtitle(commun.echapper(" · ".join(morceaux)))
        lire = commun.bouton(icone="media-playback-start-symbolic", infobulle="Écouter la référence",
                             styles=("flat", "circular"), valign=Gtk.Align.CENTER)
        lire.connect("clicked", lambda b, v=v: self.basculer_lecture(b, v["reference"]))
        lire.set_sensitive(Path(v["reference"]).exists())
        e.add_prefix(lire)
        # Adw.ExpanderRow place chaque suffixe AVANT les précédents : on les ajoute de droite à gauche
        menu = Gio.Menu()
        if not defaut:
            menu.append("Choisir par défaut", f"voix.defaut::{v['id']}")
        menu.append("Renommer…", f"voix.renommer::{v['id']}")
        menu.append("Mettre à la corbeille…", f"voix.supprimer::{v['id']}")
        e.add_suffix(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, css_classes=["flat"],
                                    valign=Gtk.Align.CENTER, tooltip_text="Actions"))
        if v.get("alertes"):
            icone = Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"],
                              tooltip_text="\n".join(v["alertes"]))
            e.add_suffix(icone)
        if v.get("imitation_ia"):
            e.add_suffix(commun.badge("Voix d’une vidéo · imitation IA", "alerte"))
        badge = affinage.libelle_badge(v["dossier"])
        if badge:
            e.add_suffix(commun.badge(*badge))
        if defaut:
            e.add_suffix(commun.badge("par défaut", "accent"))
        self._actions_voix()
        # détails
        if v.get("hauteur_10_90"):
            a, b = v["hauteur_10_90"]
            e.add_row(commun.ligne_valeur("Hauteur de parole (10e–90e centile)",
                                          f"{formats.nombre(a, 0)}–{formats.nombre(b, 0)} Hz"))
        if v.get("hauteur_hz"):
            e.add_row(commun.ligne_valeur("Centre de chant estimé", f"{formats.nombre(2 * v['hauteur_hz'], 0)} Hz"))
        if v.get("snr_db") is not None:
            e.add_row(commun.ligne_valeur("Parole au-dessus du fond", f"{formats.nombre(v['snr_db'], 0)} dB"))
        if affinage.est_affinee(v["dossier"]):
            e.add_row(commun.ligne_valeur("Affinée sur votre chant", affinage.resume_fiche(v["dossier"]),
                                          "Voix utilisée par Seed-VC 44 kHz pour doubler (repli : la référence de parole)"))
        if v.get("imitation_ia"):
            e.add_row(Adw.ActionRow(title="Voix d’artiste : imitation par IA", subtitle="Pour un usage privé seulement. Les "
                                    "doublages faits avec elle portent « imitation par IA » dans leurs fichiers.",
                                    subtitle_lines=3))
        for cle, titre in (("origine", "Origine"), ("consentement", "Consentement")):
            if v.get(cle):
                r = Adw.ActionRow(title=titre, subtitle=commun.echapper(v[cle]), subtitle_lines=4)
                e.add_row(r)
        for a in v.get("alertes") or []:
            r = Adw.ActionRow(title=commun.echapper(a), title_lines=3)
            r.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
            e.add_row(r)
        self._lignes_voix.append(e)
        return e

    def _actions_voix(self):
        if getattr(self, "_actions_posees", False):
            return
        self._actions_posees = True
        groupe = self._groupe
        for nom, rappel in (("defaut", self.choisir_defaut), ("renommer", self.renommer), ("supprimer", self.supprimer)):
            a = Gio.SimpleAction.new(nom, GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, p, r=rappel: r(p.get_string()))
            groupe.add_action(a)

    def _voix(self, ident):
        return next((v for v in self.fenetre.voix if v["id"] == ident), None)

    # --- lecture -----------------------------------------------------------------------------------------------------

    def basculer_lecture(self, bouton, fichier):
        if self.bouton_lecture is bouton and self.lecture and self.lecture.get_playing():
            self.arreter_lecture()
            return
        self.arreter_lecture()
        self.lecture = media(fichier, self._apercu)
        self.bouton_lecture = bouton
        bouton.set_icon_name("media-playback-stop-symbolic")
        self.lecture.connect("notify::ended", lambda m, _p: m.get_ended() and self.arreter_lecture())
        self.lecture.play()

    def arreter_lecture(self):
        if self.lecture:
            self.lecture.pause()
            self.lecture = None
        if self.bouton_lecture:
            self.bouton_lecture.set_icon_name("media-playback-start-symbolic")
            self.bouton_lecture = None

    # --- actions sur les voix ----------------------------------------------------------------------------------------

    def choisir_defaut(self, ident):
        self.fenetre.prefs = chemins.modifier_preference("voix_defaut", ident)
        self.fenetre.preferences_changees()
        self.fenetre.toast(f"{self.fenetre.nom_voix(ident)} : voix par défaut")

    def _depose(self, _cible, valeur, _x, _y):
        """Un fichier audio ou vidéo glissé sur la page : on demande le nom de la nouvelle voix."""
        fichiers = [p for p in (commun.chemin_de(f) for f in valeur.get_files()) if p and p.is_file()]
        if not fichiers:
            return False
        if len(fichiers) > 1:
            self.fenetre.toast("Une voix à la fois : seul le premier fichier est pris")
        self.demander_nom(fichiers[0])
        return True

    def depuis_fichier(self):
        d = Gtk.FileDialog(title="Enregistrement de la voix", modal=True,
                           filters=commun.liste_filtres(commun.filtre_fichiers(*commun.FILTRE_AUDIO)),
                           initial_folder=Gio.File.new_for_path(str(chemins.dossier_telechargements())))

        def fini(dlg, res):
            try:
                f = commun.chemin_de(dlg.open_finish(res))
            except GLib.Error:
                return
            if f:
                self.demander_nom(f)
        d.open(self.fenetre, None, fini)

    def demander_nom(self, fichier):
        boite = commun.boite(espace=8)
        entree = Gtk.Entry(placeholder_text="ex. marie, paul-2", activates_default=True)
        accord = Gtk.CheckButton(label="C’est ma voix, ou celle d’une personne d’accord pour être clonée")
        boite.append(entree)
        boite.append(accord)
        boite.append(commun.legende("Minuscules, chiffres, - ou _. L’enregistrement est nettoyé : meilleur extrait de "
                                    "20 à 25 s, −26 LUFS, hauteur de parole mesurée."))
        d = Adw.AlertDialog(heading="Nouvelle voix", body=f"Depuis « {fichier.name} »", extra_child=boite)
        d.add_response("annuler", "Annuler")
        d.add_response("ok", "Ajouter")
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")
        d.set_response_enabled("ok", False)

        def valider(*_):
            nom = entree.get_text().strip()
            existe = (chemins.dossier_voix(self.fenetre.donnees) / nom / "voix.json").exists()
            d.set_response_enabled("ok", bool(moteur.NOM_VOIX.fullmatch(nom)) and not existe and accord.get_active())
        entree.connect("changed", valider)
        accord.connect("toggled", valider)

        def reponse(_d, r):
            if r == "ok":
                self.ajouter_voix(entree.get_text().strip(), fichier)
        d.connect("response", reponse)
        d.present(self.fenetre)

    def ajouter_voix(self, nom, fichier):
        self.fenetre.toast(f"Ajout de la voix {nom}…")
        donnees = self.fenetre.donnees

        def fini(r):
            ok, message, alertes = r
            if not ok:
                commun.informer(self.fenetre, "La voix n’a pas pu être ajoutée", message)
                return
            self.fenetre.recharger_voix()
            if alertes:
                commun.informer(self.fenetre, f"Voix {nom} ajoutée, avec des alertes", "\n".join(alertes))
            else:
                self.fenetre.toast(f"Voix {nom} ajoutée")
        commun.en_fil(lambda: moteur.ajouter_voix(nom, fichier, donnees), fini)

    def enregistrer_parole(self):
        self.arreter_lecture()
        DialogueEnregistrement(self.fenetre, "parole").present(self.fenetre)

    def renommer(self, ident):
        v = self._voix(ident)
        if not v:
            return
        boite = commun.boite(espace=8)
        e_nom = Gtk.Entry(text=v["nom"], activates_default=True)
        e_id = Gtk.Entry(text=ident)
        boite.append(Gtk.Label(label="Nom affiché (dans le nom des fichiers doublés)", xalign=0, css_classes=["caption"]))
        boite.append(e_nom)
        boite.append(Gtk.Label(label="Identifiant (nom du dossier, option --voix)", xalign=0, css_classes=["caption"]))
        boite.append(e_id)
        avert = commun.legende("")
        boite.append(avert)
        d = Adw.AlertDialog(heading=f"Renommer « {v['nom']} »", extra_child=boite)
        d.add_response("annuler", "Annuler")
        d.add_response("ok", "Renommer")
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")

        def valider(*_):
            nouveau = e_id.get_text().strip()
            nom = e_nom.get_text().strip()
            dossier = chemins.dossier_voix(self.fenetre.donnees)
            ok_id = nouveau == ident or (moteur.NOM_VOIX.fullmatch(nouveau) and not (dossier / nouveau).exists())
            avert.set_label("Les doublages déjà faits gardent l’ancien identifiant : leur remix ne trouvera plus la "
                            "voix sous son nouveau nom." if nouveau != ident else "")
            d.set_response_enabled("ok", bool(nom) and bool(ok_id) and (nom != v["nom"] or nouveau != ident))
        e_nom.connect("changed", valider)
        e_id.connect("changed", valider)
        valider()

        def reponse(_d, r):
            if r != "ok":
                return
            try:
                self._renommer(ident, e_id.get_text().strip(), e_nom.get_text().strip())
            except (OSError, ValueError) as ex:
                commun.informer(self.fenetre, "Renommage impossible", str(ex))
        d.connect("response", reponse)
        d.present(self.fenetre)

    def _renommer(self, ident, nouveau, nom):
        dossier = chemins.dossier_voix(self.fenetre.donnees)
        fiche = dossier / ident / "voix.json"
        donnees = json.loads(fiche.read_text(encoding="utf-8"))
        if nom != donnees.get("nom"):
            donnees["nom"] = nom
            chemins.ecrire_json(fiche, donnees, indent=2)
        if nouveau != ident:
            if (dossier / nouveau).exists():
                raise ValueError(f"le dossier {nouveau} existe déjà")
            os.rename(dossier / ident, dossier / nouveau)
            if self.fenetre.prefs.get("voix_defaut") == ident:
                self.fenetre.prefs = chemins.modifier_preference("voix_defaut", nouveau)
        self.fenetre.recharger_voix()
        self.fenetre.toast("Voix renommée")

    def supprimer(self, ident):
        v = self._voix(ident)
        if not v:
            return
        corps = (f"Le dossier voix/{ident}/ (référence, voix.json"
                 + (", prises de chant" if (Path(v["dossier"]) / "chant").exists() else "")
                 + ") va à la corbeille ; on peut le récupérer depuis la corbeille. Les doublages déjà faits restent.")
        if ident == self.fenetre.voix_defaut():
            corps += "\nC’est la voix par défaut : ensuite, ce sera celle de doubler, ou la seule qui reste."

        def faire():
            self.arreter_lecture()

            def fini(ok, message):
                if ok:
                    self.fenetre.toast(f"Voix {v['nom']} mise à la corbeille")
                    self.fenetre.recharger_voix()
                else:
                    commun.informer(self.fenetre, "Corbeille impossible", message)
            commun.corbeille(v["dossier"], fini)
        commun.confirmer(self.fenetre, f"Mettre la voix « {v['nom']} » à la corbeille ?", corps,
                         "Mettre à la corbeille", faire)

    # --- prises de chant ---------------------------------------------------------------------------------------------

    def _voix_chant_changee(self):
        ids = getattr(self, "_ids_chant", [])
        i = self.r_voix_chant.get_selected()
        self.voix_chant = ids[i] if 0 <= i < len(ids) else None
        self.charger_prises()

    def dossiers_prises(self, ident):
        """(dossier propre de la voix, [dossiers externes en lecture seule])."""
        propre = chemins.dossier_voix(self.fenetre.donnees) / ident / "chant"
        externes = [Path(p).expanduser() for k, p in (self.fenetre.prefs.get("prises_externes") or {}).items()
                    if k == ident]
        return propre, externes

    def charger_prises(self):
        for r in self._lignes_prises:
            self.g_prises.remove(r)
        self._lignes_prises = []
        ident = self.voix_chant
        if not ident:
            self.r_total.set_subtitle("Choisissez une voix" if getattr(self, "_ids_chant", []) else "Aucune voix")
            self._utile_s, self._ecartees = 0.0, 0
            self._maj_affinage()
            return
        propre, externes = self.dossiers_prises(ident)

        def fini(liste):
            if ident != self.voix_chant:
                return
            self._afficher_prises(liste, propre)
            self._evaluer_affinage(ident, liste)
        commun.en_fil(lambda: controle.prises_chant([propre, *externes]), fini)

    def _afficher_prises(self, prises, propre):
        for r in self._lignes_prises:
            self.g_prises.remove(r)
        self._lignes_prises = []
        total = sum(d for _f, d in prises)
        self.barre_total.set_value(min(600, total))
        self.r_total.set_subtitle(f"{formats.duree(total)} sur un objectif de 5 à 10 min. {controle.avis_total(total)}")
        if not prises:
            r = Adw.ActionRow(title="Aucune prise pour cette voix")
            self.g_prises.add(r)
            self._lignes_prises.append(r)
        for f, d in reversed(prises):
            externe = f.parent.resolve() != propre.resolve()
            r = Adw.ActionRow(title=commun.echapper(f.name),
                              subtitle=commun.echapper(f"{formats.duree(d)}"
                                                       + (f" · {f.parent} (enregistrer-chant.sh, lecture seule)"
                                                          if externe else "")))
            lire = commun.bouton(icone="media-playback-start-symbolic", infobulle="Écouter",
                                 styles=("flat", "circular"), valign=Gtk.Align.CENTER)
            lire.connect("clicked", lambda b, f=f: self.basculer_lecture(b, f))
            r.add_prefix(lire)
            verif = commun.bouton(icone="emblem-ok-symbolic", infobulle="Contrôler la qualité", styles=("flat",),
                                  valign=Gtk.Align.CENTER)
            verif.connect("clicked", lambda _b, f=f: self.controler(f))
            r.add_suffix(verif)
            if not externe:
                r.add_suffix(commun.bouton(icone="user-trash-symbolic", infobulle="Mettre à la corbeille",
                                           styles=("flat",), valign=Gtk.Align.CENTER,
                                           clic=lambda f=f: self.jeter_prise(f)))
            self.g_prises.add(r)
            self._lignes_prises.append(r)

    # --- affinage ----------------------------------------------------------------------------------------------------

    def _construire_affinage(self):
        self.g_affinage = Adw.PreferencesGroup(
            title="Affiner ma voix sur mon chant",
            description="Apprend votre timbre chanté sur 5 à 10 min de votre chant, avec un entraînement sur le GPU "
                        "gratuit de Kaggle, pour qu’on vous reconnaisse dans vos doublages. Seules vos prises de chant "
                        "partent, dans un jeu de données privé.")
        self.b_affiner = commun.bouton("Affiner ma voix sur mon chant…", "emblem-favorite-symbolic",
                                       styles=("suggested-action",), clic=self.affiner, valign=Gtk.Align.CENTER)
        self.g_affinage.set_header_suffix(self.b_affiner)
        self.r_affinage = Adw.ActionRow(title="Chant exploitable", subtitle_lines=3)
        self.r_affinage.add_prefix(Gtk.Image(icon_name="audio-input-microphone-symbolic"))
        self.badge_affinage = commun.badge("affinée", "succes")
        self.badge_affinage.set_valign(Gtk.Align.CENTER)
        self.badge_affinage.set_visible(False)
        self.r_affinage.add_suffix(self.badge_affinage)
        self.g_affinage.add(self.r_affinage)
        self.r_affinage_pret = Adw.ActionRow(title="Affinage", subtitle=affinage.MENTION_EN_PREPARATION, subtitle_lines=3)
        self.r_affinage_pret.add_prefix(Gtk.Image(icon_name="dialog-information-symbolic"))
        self.g_affinage.add(self.r_affinage_pret)
        self.r_reprendre = Adw.ActionRow(title="Reprendre l’affinage", activatable=True, subtitle_lines=3,
                                         subtitle="Finit un affinage interrompu : rapatrie ce qu’a rendu Kaggle, ou "
                                                  "relance depuis le dernier point de contrôle.")
        self.r_reprendre.add_prefix(Gtk.Image(icon_name="view-refresh-symbolic"))
        self.r_reprendre.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.r_reprendre.connect("activated", lambda *_: self.affiner(reprendre=True))
        self.g_affinage.add(self.r_reprendre)
        self.page.add(self.g_affinage)
        self._maj_affinage()

    def _evaluer_affinage(self, ident, prises):
        """Chant exploitable de la voix (prises retenues, silences déduits), mesuré hors du fil de l'interface."""
        self.r_affinage.set_subtitle("Mesure du chant exploitable…")
        cache = self._cache_prises

        def fini(bilan):
            if ident != self.voix_chant:
                return
            self._utile_s, self._ecartees = bilan["utile_s"], bilan["ecartees"]
            self._maj_affinage()
        commun.en_fil(lambda: affinage.evaluer(prises, cache), fini)

    def _maj_affinage(self):
        """Bouton, état et badge selon la voix choisie, son chant exploitable et la clé « affinage_pret »."""
        ident = self.voix_chant
        v = self._voix(ident) if ident else None
        dossier = v["dossier"] if v else None
        pret = affinage.pret(chemins.lire_config())
        utile = self._utile_s if ident else 0.0
        actif = self._affinage_actif(ident) if ident else None
        reprise = bool(dossier) and (affinage.reprise_possible(dossier) is not None
                                     or (affinage.session_en_cours() or {}).get("voix") == ident)
        self.b_affiner.set_visible(bool(ident) and affinage.peut_proposer(utile))
        self.b_affiner.set_sensitive(pret and actif is None)
        self.b_affiner.set_tooltip_text(
            "Lance l’affinage sur Kaggle : un travail dans la file, avec suivi et annulation" if pret
            else affinage.MENTION_EN_PREPARATION)
        self.r_affinage_pret.set_visible(not pret)
        self.r_reprendre.set_visible(pret and reprise and actif is None)
        badge = affinage.libelle_badge(dossier) if dossier else None
        self.badge_affinage.set_visible(bool(badge))
        if badge:
            self.badge_affinage.set_label(badge[0])
        if not ident:
            self.r_affinage.set_subtitle("Choisissez une voix")
            return
        texte = affinage.texte_etat(dossier, utile, None, affinage.session_en_cours() if reprise else None) if dossier \
            else affinage.avis(utile)
        if actif:
            texte = "Un affinage de cette voix est dans la file des travaux. " + texte
        if self._ecartees and utile:
            n = self._ecartees
            texte += f" {n} prise{'s' if n > 1 else ''} écartée{'s' if n > 1 else ''} (saturée, bruitée ou trop faible)."
        self.r_affinage.set_subtitle(commun.echapper(texte))

    def _affinage_actif(self, ident):
        """Le travail d'affinage de cette voix déjà dans la file (en attente ou en cours), ou None."""
        for t in self.fenetre.file.travaux():
            t.actualiser()
            if t.demande.get("type") == "affinage" and t.actif and t.demande.get("voix") == ident:
                return t
        return None

    def affiner(self, reprendre=False):
        """Ajoute le travail « affinage » à la file : même suivi et même annulation qu'un doublage (page Travaux)."""
        ident = self.voix_chant
        if not ident or not affinage.pret(chemins.lire_config()):
            self.fenetre.toast(affinage.MENTION_EN_PREPARATION)
            return
        if self._affinage_actif(ident):
            self.fenetre.toast("Un affinage de cette voix est déjà dans la file")
            return
        nom = self.fenetre.nom_voix(ident)
        if reprendre:
            corps = (f"Finit l’affinage interrompu de la voix « {nom} » : rapatrie ce que Kaggle a rendu, ou relance "
                     "depuis le dernier point de contrôle gardé sur ce poste.")
        else:
            corps = (f"Les prises de chant de la voix « {nom} » ({formats.duree(self._utile_s)} exploitables) partent dans "
                     "un jeu de données privé de votre compte Kaggle, puis un entraînement de Seed-VC 44 kHz tourne sur "
                     f"le GPU. {affinage.texte_estimation()} Le résultat revient dans voix/{ident}/affinage/ ; le travail "
                     "se suit et s’annule dans la page Travaux.")

        def faire():
            demande = travaux.demande_affinage(ident, nom, self.fenetre.donnees, reprendre=reprendre,
                                               estimations=affinage.estimations_travail())
            self.fenetre.ajouter_travaux([demande])
            self.fenetre.toast(f"Affinage de {nom} ajouté à la file", "Travaux", lambda: self.fenetre.aller("travaux"))
            self._maj_affinage()
        commun.confirmer(self.fenetre, "Reprendre l’affinage ?" if reprendre else f"Affiner la voix « {nom} » ?", corps,
                         "Reprendre" if reprendre else "Affiner", faire, destructif=False)

    def controler(self, f):
        self.fenetre.toast(f"Contrôle de {f.name}…", duree=2)

        def fini(res):
            texte = (f"Durée {formats.duree(res.get('duree_s'))}, crête {formats.nombre(res.get('crete_db'))} dBFS, "
                     f"{formats.nombre(res.get('lufs'))} LUFS, bruit de fond {formats.nombre(res.get('bruit_db'))} dBFS, "
                     f"silence {formats.nombre(res.get('silence_pct'), 0)} %.\n\n")
            texte += "\n".join(f"• {a}" for a in res["alertes"]) if res["alertes"] else "Qualité correcte."
            commun.informer(self.fenetre, f"Contrôle de {f.name}", texte)
        commun.en_fil(lambda: controle.controle_chant(f), fini)

    def jeter_prise(self, f):
        def faire():
            self.arreter_lecture()
            commun.corbeille(f, lambda ok, m: (self.fenetre.toast("Prise mise à la corbeille" if ok else m),
                                               self.charger_prises()))
        commun.confirmer(self.fenetre, "Mettre cette prise à la corbeille ?", f.name, "Mettre à la corbeille", faire)

    def ouvrir_seance(self):
        """La séance d'enregistrement de la voix chantée, pour la voix choisie dans « Chant pour l'affinage »."""
        if not self.voix_chant:
            self.fenetre.toast("Choisissez d’abord une voix")
            return None
        self.arreter_lecture()
        from .seance import PageSeance
        page = PageSeance(self.fenetre, self.voix_chant, self.fenetre.nom_voix(self.voix_chant))
        self.fenetre.pousser(page)
        return page

    def enregistrer_chant(self):
        if not self.voix_chant:
            self.fenetre.toast("Choisissez d’abord une voix")
            return
        self.arreter_lecture()
        DialogueEnregistrement(self.fenetre, "chant", self.voix_chant, apres=self.charger_prises).present(self.fenetre)

    # --- essais ------------------------------------------------------------------------------------------------------

    def scenario(self, action):
        if action.startswith("enregistrer"):
            d = DialogueEnregistrement(self.fenetre, "chant" if "chant" in action else "parole", self.voix_chant)
            d.present(self.fenetre)
            # « enregistrer[-chant][-telephone][-enregistrement|-controle] », étapes jouées dans l'ordre
            etapes = [e for e in action.split("-")[1:] if e in ("telephone", "enregistrement", "controle")]
            if etapes:
                GLib.timeout_add(500, lambda: ([d.scenario(e) for e in etapes], False)[1])
        elif action.startswith("seance"):
            # « seance » ou « seance-chanson » : la séance (étape 4 avec ses paroles pour le second)
            if not self.voix_chant and self.fenetre.voix:
                self.r_voix_chant.set_selected(0)
            page = self.ouvrir_seance()
            if page is not None and action.endswith("chanson"):
                GLib.timeout_add(300, lambda: (page.scenario("chanson"), False)[1])
        elif action == "affinage":
            if not self.voix_chant and self.fenetre.voix:
                self.r_voix_chant.set_selected(0)
        elif action == "deplier":
            def deplier():
                for r in self._lignes_voix:
                    if isinstance(r, Adw.ExpanderRow):
                        r.set_expanded(True)
                        break
                return False
            GLib.timeout_add(1200, deplier)
