"""Export pour les réseaux, dans l'interface : le dialogue « Partager un extrait » (le travail est fait par
voixla/export_social.py). On l'ouvre depuis le détail d'un doublage (bouton « Partager… » de l'en-tête) ou depuis le
menu « ⋮ » d'une carte ou d'une ligne de la bibliothèque.

- **Extrait** : début et fin (15 à 60 s, en temps du doublage, comme le lecteur). « Prendre la boucle » reprend la
  boucle A-B du lecteur du détail (prise d'office à l'ouverture si elle est posée) ; « Prendre » la proposition : le
  refrain, ou le passage le plus chanté ; « Écouter » pose la boucle A-B du lecteur sur l'extrait et le lit, avec la
  piste choisie (depuis le détail).
- **Réglages** : format, cadrage, piste son, fondu, paroles karaoké et traduction, mention en surimpression (allumée à
  chaque ouverture : on partage une voix clonée). Format, cadrage, fondu, traduction et texte de la mention sont
  gardés dans interface.json (clé « partage »).
- **Aperçu** : une image de la vidéo à partager, où un mot se remplit (ou à l'instant choisi sous l'image), refaite
  une demi-seconde après chaque changement, dans un fil (ffmpeg, une image, sous nice ; jamais par file-attente).
- **Export** : « python3 -m voixla.export_social » dans un processus à part, dans son propre groupe, par file-attente
  au-delà de quelques secondes de calcul (export_social.commande_interface). L'avancement arrive en lignes JSON ;
  « Annuler » envoie SIGTERM au groupe (file-attente, python, ffmpeg), puis SIGKILL 5 s plus tard. Un seul export à la
  fois ; il continue si l'on ferme le dialogue, et un toast le dit à la fin, avec « Ouvrir le dossier »."""

import json
import os
import signal
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

from gi.repository import Adw, Gdk, GLib, Gtk

from .. import chemins, formats, pistes
from .. import export_social as es
from .. import preparation as prep
from . import commun
from .analyses import analyses

DELAI_APERCU_MS = 500        # l'aperçu est refait une demi-seconde après le dernier changement
ARRET_FORCE_S = 5            # « Annuler » : SIGKILL si le groupe n'est pas parti après SIGTERM
LARGEUR_APERCU = 270
SOUS_TITRES_FORMATS = {"vertical": "1080 × 1920 : TikTok, Reels, Shorts, statut WhatsApp",
                       "carre": "1080 × 1080 : fil d’actualité",
                       "origine": "La taille de la vidéo (1920 au plus sur le grand côté)"}
SOUS_TITRES_CADRAGES = {"flou": "L’image entière, sur un fond flou tiré d’elle-même ; les paroles dessous",
                        "recadre": "L’image remplit le cadre, coupée au centre ; les paroles dessus"}

CSS = b"""
.apercu-partage { background: black; border-radius: 10px; }
.etat-partage { font-feature-settings: "tnum"; }
"""


def _installer_css():
    if getattr(_installer_css, "fait", False) or Gdk.Display.get_default() is None:
        return
    f = Gtk.CssProvider()
    f.load_from_data(CSS, -1)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), f, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _installer_css.fait = True


def mo(octets):
    return f"{formats.nombre(octets / 1e6, 1)} Mo"


# --- L'export en cours ------------------------------------------------------------------------------------------------

class Exportation:
    """Un export lancé par l'interface : son processus (groupe à part), son avancement, son annulation. Les dialogues
    ouverts s'y abonnent (ecouteurs : rappels sans argument, appelés dans le fil de l'interface)."""

    def __init__(self, res, reglages, sortie, commande):
        self.res, self.reglages, self.sortie, self.commande = res, reglages, Path(sortie), commande
        self.processus = None
        self.fraction = 0.0
        self.attente = ""             # motif donné par file-attente tant que l'export attend son tour
        self.fini = False
        self.ok = False
        self.annulee = False
        self.message = ""
        self.resultat = None          # le JSON final de voixla.export_social (sortie, taille_o, temps_s…)
        self.ecouteurs = []

    @property
    def en_cours(self):
        return not self.fini

    def lancer(self):
        threading.Thread(target=self._fil, daemon=True).start()

    def _fil(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(chemins.INTERFACE)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH")
                                                                         else []))
        env["VOIXLA_AVANCEMENT"] = "1"
        lignes = []
        try:
            p = subprocess.Popen(self.commande, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        except OSError as e:
            GLib.idle_add(self._terminer, False, f"lancement impossible : {e}")
            return
        self.processus = p
        if self.annulee:
            self._signaler(signal.SIGTERM)
        for ligne in p.stdout:
            ligne = ligne.strip()
            av = prep.lire_avancement(ligne)
            if av is not None:
                GLib.idle_add(self._avancer, av[0])
            elif ligne.startswith("{") and '"sortie"' in ligne:
                try:
                    self.resultat = json.loads(ligne)
                except ValueError:
                    lignes.append(ligne)
            elif "en file d'attente" in ligne:
                GLib.idle_add(self._attendre, ligne.split(" : ", 1)[-1])
            elif "son tour est venu" in ligne:
                GLib.idle_add(self._attendre, "")
            elif ligne:
                lignes.append(ligne)
        code = p.wait()
        ok = code == 0 and self.resultat is not None
        message = "" if ok else (lignes[-1].removeprefix("erreur :").strip() if lignes else f"code {code}")
        GLib.idle_add(self._terminer, ok, message)

    def _prevenir(self):
        for e in list(self.ecouteurs):
            try:
                e()
            except Exception:  # noqa: BLE001
                import traceback
                traceback.print_exc()

    def _avancer(self, fraction):
        self.fraction, self.attente = fraction, ""
        self._prevenir()
        return False

    def _attendre(self, motif):
        self.attente = motif
        self._prevenir()
        return False

    def _terminer(self, ok, message):
        self.fini, self.ok, self.message = True, ok and not self.annulee, message
        global _courante
        if _courante is self:
            _courante = None
        self._prevenir()
        return False

    def _signaler(self, sig):
        p = self.processus
        if p is None or p.poll() is not None:
            return
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def annuler(self):
        """SIGTERM au groupe (file-attente, python, ffmpeg) : le fichier temporaire est effacé ; SIGKILL s'il traîne."""
        if self.fini:
            return
        self.annulee = True
        self._signaler(signal.SIGTERM)
        GLib.timeout_add_seconds(ARRET_FORCE_S, lambda: (self._signaler(signal.SIGKILL), False)[1])
        self._prevenir()


_courante = None


def export_courant():
    """L'export lancé par l'interface qui tourne encore, ou None (un seul à la fois)."""
    return _courante


def exporter(fenetre, res, reglages, sortie=None):
    """Lance l'export (un seul à la fois) et rend son Exportation ; un toast à la fin si aucun dialogue ne le suit."""
    global _courante
    if _courante is not None:
        return None
    sortie = Path(sortie) if sortie else es.sortie_par_defaut(res, reglages)
    commande = es.commande_interface(sys.executable, res, reglages, sortie, shutil_which("file-attente"))
    e = Exportation(res, replace(reglages), sortie, commande)

    def fin():
        # un dialogue qui suit l'export dit lui-même la fin ; sinon (dialogue fermé), un toast
        if not e.fini or any(x is not fin for x in e.ecouteurs):
            return
        if e.ok and e.resultat:
            f = Path(e.resultat.get("sortie") or e.sortie)
            fenetre.toast(f"Vidéo à partager prête : {f.name}", "Ouvrir le dossier",
                          lambda: commun.montrer_fichier(fenetre, f), duree=8)
        elif not e.annulee:
            fenetre.toast(f"Export pour les réseaux impossible : {e.message}", duree=8)
    e.ecouteurs.append(fin)
    _courante = e
    e.lancer()
    return e


def shutil_which(nom):
    import shutil
    return shutil.which(nom)


# --- Le dialogue -----------------------------------------------------------------------------------------------------

class DialoguePartage(Adw.Dialog):
    """« Partager un extrait » d'un doublage : extrait, format, son, paroles, mention, aperçu et export."""

    def __init__(self, fenetre, res, detail=None):
        super().__init__(title="Partager un extrait", content_width=900, content_height=780, width_request=360,
                         height_request=420)
        _installer_css()
        self.fenetre, self.res, self.detail = fenetre, res, detail
        self.paroles = None
        self.total = res.duree_s
        self.proposition = None
        self.recherche_finie = False
        self.touche = False            # l'extrait a été choisi à la main (ou la boucle prise) : la proposition attend
        self.instant = None            # instant de l'aperçu choisi sous l'image (s depuis le début de l'extrait)
        self._maj = False              # champs remplis par le code (pas une saisie)
        self._apercu_n = 0
        self._apercu_en_cours = False
        self._apercu_a_refaire = False
        self._minuteur_apercu = None
        self._fichier_apercu = None
        self._ferme = False
        self.export = None             # l'Exportation suivie par ce dialogue
        self.dernier = None            # le fichier produit par le dernier export réussi
        prefs = fenetre.prefs.get("partage") if isinstance(fenetre.prefs.get("partage"), dict) else {}
        self.reglages = es.Reglages(
            format=prefs.get("format") if prefs.get("format") in es.FORMATS else "vertical",
            cadrage=prefs.get("cadrage") if prefs.get("cadrage") in es.CADRAGES else "flou",
            traduction=bool(prefs.get("traduction", False)), fondu=bool(prefs.get("fondu", True)),
            texte_mention=str(prefs.get("texte_mention") or es.MENTION_DEFAUT), mention=True)
        self.possibles = es.pistes_possibles(res)
        # l'extrait de départ : la boucle A-B du lecteur, sinon la position du lecteur (la proposition le remplacera)
        ext = es.extrait_de_boucle(self._boucle(), self.total)
        if ext:
            self.reglages.debut, self.reglages.duree = ext
            self.touche = True
        else:
            pos = detail.lecteur.position_ref() if detail is not None else 0.0
            self.reglages.debut, self.reglages.duree = es.borner_extrait(pos or 0.0, es.DUREE_DEFAUT_S, self.total)

        vue = Adw.ToolbarView()
        entete = Adw.HeaderBar()
        nom = res.voix_nom or res.voix or ""
        entete.set_title_widget(Adw.WindowTitle(title="Partager un extrait",
                                                subtitle=res.titre_court + (f" · voix {nom}" if nom else "")))
        vue.add_top_bar(entete)
        self.corps = Gtk.Box(spacing=18, margin_top=12, margin_bottom=12, margin_start=18, margin_end=18)
        self.corps.append(self._colonne_apercu())
        self.page = Adw.PreferencesPage(hexpand=True)
        for g in (self._groupe_extrait(), self._groupe_image(), self._groupe_son(), self._groupe_paroles(),
                  self._groupe_mention()):
            self.page.add(g)
        self.corps.append(self.page)
        vue.set_content(self.corps)
        vue.add_bottom_bar(self._barre_bas())
        self.set_child(vue)
        etroit = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        etroit.add_setter(self.corps, "orientation", Gtk.Orientation.VERTICAL)
        self.add_breakpoint(etroit)
        self.connect("closed", lambda *_: self._fermer())

        self._ecrire_extrait()
        self._maj_tout()
        # les paroles (paroles.json, sinon les sous-titres de la vidéo doublée, extraits au besoin), puis la proposition
        analyses().paroles(res.dossier_voix, res.video, self._paroles_lues)
        if self.total is None:
            commun.en_fil(self._lire_total, self._total_lu)
        courant = export_courant()
        if courant is not None and courant.res.cle == res.cle:
            self._suivre(courant)
        self._planifier_apercu(0)

    # --- construction ---------------------------------------------------------------------------------------------

    def _colonne_apercu(self):
        b = commun.boite(espace=8, valign=Gtk.Align.START, halign=Gtk.Align.CENTER, hexpand=False)
        b.set_size_request(LARGEUR_APERCU, -1)
        b.append(Gtk.Label(label="Aperçu", xalign=0, css_classes=["heading"]))
        cadre = Gtk.Overlay(css_classes=["apercu-partage"], overflow=Gtk.Overflow.HIDDEN, halign=Gtk.Align.CENTER)
        self.image = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        self.image.set_size_request(LARGEUR_APERCU, int(LARGEUR_APERCU * 16 / 9))
        self.image.update_property([Gtk.AccessibleProperty.LABEL], ["Aperçu d’une image de la vidéo à partager"])
        cadre.set_child(self.image)
        self.attente_apercu = Gtk.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=32,
                                          height_request=32)
        cadre.add_overlay(self.attente_apercu)
        b.append(cadre)
        self.curseur = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, draw_value=False,
                                 adjustment=Gtk.Adjustment(lower=0, upper=self.reglages.duree, step_increment=0.5,
                                                           page_increment=5))
        self.curseur.update_property([Gtk.AccessibleProperty.LABEL], ["Instant de l’aperçu"])
        self.curseur.connect("value-changed", lambda *_: self._curseur_bouge())
        b.append(self.curseur)
        self.l_apercu = commun.legende("")
        self.l_apercu.add_css_class("etat-partage")
        self.l_apercu.set_max_width_chars(36)
        b.append(self.l_apercu)
        return b

    def _groupe_extrait(self):
        g = Adw.PreferencesGroup(title="Extrait", description="")
        self.g_extrait = g
        if self.detail is not None:
            self.b_ecouter = commun.bouton("Écouter", "media-playback-start-symbolic", styles=("flat",),
                                           infobulle="Lire l’extrait en boucle dans le lecteur, avec la piste choisie",
                                           clic=self._ecouter, valign=Gtk.Align.CENTER)
            g.set_header_suffix(self.b_ecouter)
        self.e_debut = Adw.EntryRow(title="Début")
        self.e_fin = Adw.EntryRow(title="Fin")
        for e in (self.e_debut, self.e_fin):
            e.add_css_class("chiffres")
            e.connect("changed", lambda *_: self._extrait_saisi())
            g.add(e)
        self.r_boucle = Adw.ActionRow(title="Boucle A-B du lecteur")
        self.b_boucle = commun.bouton("Prendre la boucle", styles=("flat",), valign=Gtk.Align.CENTER,
                                      clic=self._prendre_boucle)
        self.r_boucle.add_suffix(self.b_boucle)
        g.add(self.r_boucle)
        self.r_proposition = Adw.ActionRow(title="Proposition", subtitle="Recherche du refrain…")
        self.b_proposition = commun.bouton("Prendre", styles=("flat",), valign=Gtk.Align.CENTER, sensitive=False,
                                           clic=self._prendre_proposition)
        self.r_proposition.add_suffix(self.b_proposition)
        g.add(self.r_proposition)
        return g

    def _groupe_image(self):
        g = Adw.PreferencesGroup(title="Image")
        self.r_format = Adw.ComboRow(title="Format", model=Gtk.StringList.new(
            [es.LIBELLES_FORMATS[f] for f in es.FORMATS]))
        self.r_format.set_selected(es.FORMATS.index(self.reglages.format))
        self.r_format.connect("notify::selected", lambda *_: self._regle(format=es.FORMATS[self.r_format.get_selected()]))
        g.add(self.r_format)
        self.r_cadrage = Adw.ComboRow(title="Cadrage", model=Gtk.StringList.new(["Fond flou", "Recadrée"]))
        self.r_cadrage.set_selected(es.CADRAGES.index(self.reglages.cadrage))
        self.r_cadrage.connect("notify::selected",
                               lambda *_: self._regle(cadrage=es.CADRAGES[self.r_cadrage.get_selected()]))
        g.add(self.r_cadrage)
        return g

    def _groupe_son(self):
        g = Adw.PreferencesGroup(title="Son")
        self.pistes = [p for p in es.PISTES if not self.possibles.get(p)]
        if not self.pistes:
            self.pistes = ["doublage"]
        self.r_piste = Adw.ComboRow(title="Piste", model=Gtk.StringList.new(
            [es.LIBELLES_PISTES[p] for p in self.pistes]))
        manquantes = [f"{es.LIBELLES_PISTES[p]} : {r}" for p, r in self.possibles.items() if r and p != "autotune"]
        if manquantes:
            self.r_piste.set_subtitle(commun.echapper("Indisponible — " + " ; ".join(manquantes)))
        self.r_piste.connect("notify::selected", lambda *_: self._regle(piste=self.pistes[self.r_piste.get_selected()]))
        g.add(self.r_piste)
        self.r_fondu = Adw.SwitchRow(title="Fondu d’entrée et de sortie", subtitle="Du son et de l’image",
                                     active=self.reglages.fondu)
        self.r_fondu.connect("notify::active", lambda *_: self._regle(fondu=self.r_fondu.get_active()))
        g.add(self.r_fondu)
        return g

    def _groupe_paroles(self):
        g = Adw.PreferencesGroup(title="Paroles")
        self.r_paroles = Adw.SwitchRow(title="Paroles karaoké", subtitle="Recherche des paroles…", active=True)
        self.r_paroles.connect("notify::active", lambda *_: self._regle(paroles=self.r_paroles.get_active()))
        g.add(self.r_paroles)
        self.r_traduction = Adw.SwitchRow(title="Traduction sous la ligne", subtitle="En français",
                                          active=self.reglages.traduction, visible=False)
        self.r_traduction.connect("notify::active", lambda *_: self._regle(traduction=self.r_traduction.get_active()))
        g.add(self.r_traduction)
        return g

    def _groupe_mention(self):
        g = Adw.PreferencesGroup(title="Mention")
        self.r_mention = Adw.SwitchRow(title="Mention en surimpression",
                                       subtitle="Discrète, en haut à gauche : la voix est clonée", active=True)
        self.r_mention.connect("notify::active", lambda *_: self._regle(mention=self.r_mention.get_active()))
        g.add(self.r_mention)
        self.e_mention = Adw.EntryRow(title="Texte de la mention", text=self.reglages.texte_mention)
        self.e_mention.connect("changed", lambda *_: self._regle(texte_mention=self.e_mention.get_text()))
        g.add(self.e_mention)
        return g

    def _barre_bas(self):
        b = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=18, margin_end=18)
        etat = commun.boite(espace=4, hexpand=True, valign=Gtk.Align.CENTER)
        self.l_etat = Gtk.Label(xalign=0, wrap=True, css_classes=["caption", "etat-partage"])
        etat.append(self.l_etat)
        self.progres = Gtk.ProgressBar(visible=False)
        etat.append(self.progres)
        b.append(etat)
        self.b_dossier = commun.bouton("Ouvrir le dossier", "folder-open-symbolic", clic=self._ouvrir_dossier,
                                       valign=Gtk.Align.CENTER)
        b.append(self.b_dossier)
        self.b_annuler = commun.bouton("Annuler", styles=("destructive-action",), visible=False,
                                       valign=Gtk.Align.CENTER, clic=self._annuler)
        b.append(self.b_annuler)
        self.b_exporter = commun.bouton("Exporter", "document-save-symbolic", styles=("suggested-action",),
                                        valign=Gtk.Align.CENTER, clic=self._exporter)
        b.append(self.b_exporter)
        return b

    # --- extrait ---------------------------------------------------------------------------------------------------

    def _boucle(self):
        if self.detail is None:
            return None
        b = getattr(self.detail.lecteur, "boucle", None)
        return tuple(b) if b and len(b) == 2 and b[1] is not None else None

    def _ecrire_extrait(self):
        self._maj = True
        self.e_debut.set_text(formats.chrono_precis(self.reglages.debut))
        self.e_fin.set_text(formats.chrono_precis(self.reglages.fin))
        self._maj = False

    def _lire_extrait(self):
        """(début, durée) d'après les champs, ou None s'ils sont illisibles."""
        a = formats.lire_secondes(self.e_debut.get_text())
        b = formats.lire_secondes(self.e_fin.get_text())
        if a is None or b is None or b <= a:
            return None
        return a, b - a

    def _extrait_saisi(self):
        if self._maj:
            return
        self.touche = True
        lu = self._lire_extrait()
        if lu is not None:
            self.reglages.debut, self.reglages.duree = lu
            self.instant = None
        self._maj_tout()
        self._planifier_apercu()

    def _poser_extrait(self, debut, duree):
        self.reglages.debut, self.reglages.duree = es.borner_extrait(debut, duree, self.total)
        self.instant = None
        self._ecrire_extrait()
        self._maj_tout()
        self._planifier_apercu()

    def _prendre_boucle(self):
        ext = es.extrait_de_boucle(self._boucle(), self.total)
        if ext:
            self.touche = True
            self._poser_extrait(*ext)

    def _prendre_proposition(self):
        if self.proposition is not None:
            self.touche = True
            self._poser_extrait(self.proposition.debut, self.proposition.duree)

    def _ecouter(self):
        if self.detail is None or self.detail._oubliee:
            return
        r = self.reglages
        self.detail.ecouter_extrait(r.debut, r.fin, r.piste)

    # --- données lues en tâche de fond ----------------------------------------------------------------------------

    def _lire_total(self):
        f = self.res.video or self.res.mp3
        d = pistes.duree_audio(f) if f else None
        return max(0.0, d - pistes.debut_son_video(self.res)) if d and f == self.res.video else d

    def _total_lu(self, total):
        if self._ferme or not total:
            return
        self.total = total
        if not self.touche:
            self._poser_extrait(self.reglages.debut, self.reglages.duree)
        else:
            self._maj_tout()

    def _paroles_lues(self, p, message):
        if self._ferme or message == "attente":
            return
        self.paroles = p
        if p is None:
            self.r_paroles.set_subtitle(commun.echapper((message or "pas de paroles").capitalize()))
            self.r_paroles.set_sensitive(False)
        else:
            couches = p.couches("deux")
            self.r_paroles.set_subtitle(commun.echapper(es.P.legende_source(p, couches[:1])))
            self.r_traduction.set_visible(p.a_deux_langues())
        res = self.res
        commun.en_fil(lambda: self._proposer(res, p), self._proposition_lue)
        self._planifier_apercu()

    @staticmethod
    def _proposer(res, p):
        voise, pas = es.lire_voisement(res.dossier_voix)
        return es.proposer_extrait(p, es.ecart_paroles(res, p) if p is not None else 0.0, voise, pas or 0.02,
                                   res.duree_s)

    def _proposition_lue(self, prop):
        if self._ferme:
            return
        self.recherche_finie = True
        self.proposition = prop
        if prop is None:
            self.r_proposition.set_subtitle("Ni paroles ni courbe de la voix : pas de proposition")
            self.b_proposition.set_sensitive(False)
        else:
            self.r_proposition.set_subtitle(
                f"{prop.libelle[:1].upper()}{prop.libelle[1:]} : {formats.chrono_precis(prop.debut)} → "
                f"{formats.chrono_precis(prop.debut + prop.duree)} ({formats.duree(prop.duree)})")
            self.b_proposition.set_sensitive(True)
            if not self.touche:
                self._poser_extrait(prop.debut, prop.duree)

    # --- réglages --------------------------------------------------------------------------------------------------

    def _regle(self, **valeurs):
        for k, v in valeurs.items():
            setattr(self.reglages, k, v)
        self._maj_tout()
        self._planifier_apercu()

    def _maj_tout(self):
        r = self.reglages
        lu = self._lire_extrait()
        if lu is None:
            self.g_extrait.set_description("Début ou fin illisible, ou fin avant le début (écrire 1:05 ou 65)")
        else:
            debut, duree = es.borner_extrait(lu[0], lu[1], self.total)
            texte = f"{formats.duree(duree)}, de {formats.chrono_precis(debut)} à {formats.chrono_precis(debut + duree)}"
            if abs(debut - lu[0]) > 0.05 or abs(duree - lu[1]) > 0.05:
                texte += " (ramené à 15 à 60 s, dans le doublage)"
            self.g_extrait.set_description(texte)
        b = self._boucle()
        if self.detail is None:
            self.r_boucle.set_subtitle("Ouvrez le doublage pour choisir l’extrait au lecteur")
        elif b:
            self.r_boucle.set_subtitle(f"A-B {formats.chrono_precis(b[0])} → {formats.chrono_precis(b[1])}")
        else:
            self.r_boucle.set_subtitle("Aucune boucle : touche L (ou bouton A-B) dans le lecteur, au début puis à la "
                                       "fin du passage")
        self.b_boucle.set_sensitive(bool(b))
        self.r_cadrage.set_sensitive(r.format != "origine")
        self.r_format.set_subtitle(SOUS_TITRES_FORMATS[r.format])
        self.r_cadrage.set_subtitle(SOUS_TITRES_CADRAGES[r.cadrage] if r.format != "origine" else
                                    "L’image telle quelle")
        self.e_mention.set_sensitive(r.mention)
        self.r_traduction.set_sensitive(r.paroles)
        if self.export is None or self.export.fini:
            self._etat_repos()
        self._maj_boutons()

    def _maj_boutons(self):
        autre = export_courant()
        occupe = autre is not None and autre is not self.export
        en_cours = self.export is not None and not self.export.fini
        self.b_exporter.set_sensitive(not en_cours and not occupe and self._lire_extrait() is not None)
        self.b_exporter.set_tooltip_text("Un autre export est en cours" if occupe else None)
        self.b_annuler.set_visible(en_cours)
        self.progres.set_visible(en_cours)

    def _etat_repos(self):
        r = self.reglages
        if self.dernier is not None:
            return
        taille = "1080 × 1920" if r.format == "vertical" else "1080 × 1080" if r.format == "carre" else \
            "taille d’origine"
        self.l_etat.set_label(f"Vidéo H.264 et AAC de {formats.duree(r.duree)} en {taille}, "
                              f"{formats.nombre(es.taille_max_mo(r.duree), 0)} Mo au plus · dans "
                              f"« {self.res.dossier.name}/partage/ »")
        self.l_etat.remove_css_class("error")

    # --- aperçu ----------------------------------------------------------------------------------------------------

    def _curseur_bouge(self):
        if self._maj:
            return
        self.instant = self.curseur.get_value()
        self._planifier_apercu()

    def _planifier_apercu(self, delai=DELAI_APERCU_MS):
        if self._ferme:
            return
        if self._minuteur_apercu is not None:
            GLib.source_remove(self._minuteur_apercu)
        self._minuteur_apercu = GLib.timeout_add(delai, self._lancer_apercu)

    def _lancer_apercu(self):
        self._minuteur_apercu = None
        if self._ferme:
            return False
        if self._apercu_en_cours:
            self._apercu_a_refaire = True
            return False
        if self._lire_extrait() is None:
            return False
        self._apercu_en_cours = True
        self._apercu_n += 1
        n = self._apercu_n
        r = replace(self.reglages)
        r.texte_mention = r.texte_mention.strip() or es.MENTION_DEFAUT
        instant, p, res = self.instant, (self.paroles if r.paroles else None), self.res
        sortie = chemins.dossier_cache() / "partage" / f"apercu-{os.getpid()}-{n}.png"
        self.attente_apercu.start()
        commun.en_fil(lambda: es.apercu(res, r, sortie, instant, paroles=p),
                      lambda x: self._apercu_pret(n, x), lambda e: self._apercu_rate(n, e))
        return False

    def _apercu_fini(self):
        self._apercu_en_cours = False
        self.attente_apercu.stop()
        if self._apercu_a_refaire and not self._ferme:
            self._apercu_a_refaire = False
            self._planifier_apercu(0)

    def _apercu_pret(self, n, resultat):
        f, instant = resultat
        if self._ferme:
            Path(f).unlink(missing_ok=True)
            return
        try:
            texture = Gdk.Texture.new_from_filename(str(f))
        except GLib.Error as e:
            self.l_apercu.set_label(f"Aperçu illisible : {e.message}")
            texture = None
        if texture is not None:
            self.image.set_paintable(texture)
            # le cadre de l'aperçu prend les proportions de la vidéo
            l, h = texture.get_width(), texture.get_height()
            self.image.set_size_request(LARGEUR_APERCU, int(LARGEUR_APERCU * h / max(1, l)))
            ancien, self._fichier_apercu = self._fichier_apercu, Path(f)
            if ancien is not None and ancien != self._fichier_apercu:
                ancien.unlink(missing_ok=True)
            self._maj = True
            self.curseur.get_adjustment().set_upper(max(0.5, self.reglages.duree))
            self.curseur.set_value(instant)
            self._maj = False
            self.l_apercu.set_label(f"À {formats.chrono_precis(instant)} de l’extrait"
                                    f"{'' if self.instant is not None else ' : un mot qui se remplit'} ; "
                                    "glisser pour voir un autre instant")
        self._apercu_fini()

    def _apercu_rate(self, n, e):
        if not self._ferme:
            self.l_apercu.set_label(f"Aperçu impossible : {str(e)[-200:]}")
        self._apercu_fini()

    # --- export ----------------------------------------------------------------------------------------------------

    def _memoriser(self):
        r = self.reglages
        v = {"format": r.format, "cadrage": r.cadrage, "traduction": r.traduction, "fondu": r.fondu,
             "texte_mention": r.texte_mention.strip() or es.MENTION_DEFAUT}
        if self.fenetre.prefs.get("partage") != v:
            self.fenetre.prefs = chemins.modifier_preference("partage", v)

    def _exporter(self):
        lu = self._lire_extrait()
        if lu is None or export_courant() is not None:
            return
        r = replace(self.reglages)
        r.debut, r.duree = es.borner_extrait(lu[0], lu[1], self.total)
        r.texte_mention = r.texte_mention.strip() or es.MENTION_DEFAUT
        r.paroles = r.paroles and self.r_paroles.get_sensitive()
        self._memoriser()
        self.dernier = None
        self.b_dossier.remove_css_class("suggested-action")
        self.b_exporter.add_css_class("suggested-action")
        e = exporter(self.fenetre, self.res, r)
        if e is None:
            self.fenetre.toast("Un export est déjà en cours")
            return
        self._suivre(e)

    def _suivre(self, e):
        self.export = e
        e.ecouteurs.append(self._export_change)
        self._export_change()

    def _export_change(self):
        e = self.export
        if e is None or self._ferme:
            return
        self.l_etat.remove_css_class("error")
        if not e.fini:
            if e.annulee:
                self.l_etat.set_label("Annulation…")
            elif e.attente:
                self.l_etat.set_label(f"En file d’attente (travaux lourds du poste) : {e.attente}")
                self.progres.pulse()
            else:
                self.l_etat.set_label(f"Encodage… {formats.nombre(100 * e.fraction, 0)} % · {e.sortie.name}")
                self.progres.set_fraction(e.fraction)
        elif e.ok and e.resultat:
            f = Path(e.resultat.get("sortie") or e.sortie)
            self.dernier = f
            notes = e.resultat.get("notes") or []
            self.l_etat.set_label(f"Prête : {f.name} · {mo(e.resultat.get('taille_o') or 0)} · calculée en "
                                  f"{formats.duree(e.resultat.get('temps_s'))}" + (f" ({' ; '.join(notes)})"
                                                                                   if notes else ""))
            self.b_dossier.add_css_class("suggested-action")
            self.b_exporter.remove_css_class("suggested-action")
        elif e.annulee:
            self.l_etat.set_label("Export annulé.")
        else:
            self.l_etat.set_label(f"Export impossible : {e.message}")
            self.l_etat.add_css_class("error")
        self._maj_boutons()

    def _annuler(self):
        if self.export is not None:
            self.export.annuler()

    def _ouvrir_dossier(self):
        if self.dernier is not None and self.dernier.is_file():
            commun.montrer_fichier(self.fenetre, self.dernier)
            return
        d = es.dossier_partage(self.res)
        commun.ouvrir_dossier(self.fenetre, d if d.is_dir() else self.res.dossier)

    def _fermer(self):
        """Le dialogue se ferme : l'export continue (toast à la fin), l'aperçu s'arrête, ses images partent."""
        self._ferme = True
        if self._minuteur_apercu is not None:
            GLib.source_remove(self._minuteur_apercu)
            self._minuteur_apercu = None
        if self.export is not None and self._export_change in self.export.ecouteurs:
            self.export.ecouteurs.remove(self._export_change)
        if self._fichier_apercu is not None:
            self._fichier_apercu.unlink(missing_ok=True)
        self._memoriser()


def ouvrir(fenetre, res, detail=None):
    """Ouvre « Partager un extrait » pour ce doublage (detail : sa page, pour la boucle A-B et « Écouter »)."""
    d = DialoguePartage(fenetre, res, detail)
    d.present(fenetre)
    return d
