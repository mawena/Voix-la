"""Enregistrement au micro (GStreamer : pulsesrc → level → wavenc), avec texte à lire, compte à rebours, niveau en
direct, puis contrôle de qualité. Deux usages :
- « parole » : créer une nouvelle voix (doubler --ajouter-voix), comme le script enregistrer.sh ;
- « chant »  : une prise de chant pour l'affinage futur du modèle, comme le script enregistrer-chant.sh.
La liste « Micro » propose aussi le micro d'un téléphone Android (scrcpy, comme « enregistrer-chant.sh pixel », voir
voixla.telephone) : pas de niveau en direct, le contrôle de qualité le montre après la prise.
VOIXLA_MICRO_ESSAI=1 remplace le micro par un son de synthèse (essais sans micro) ; VOIXLA_TELEPHONE_ESSAI=1 imite un
téléphone (« aucun » : aucun téléphone).
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Adw, GLib, Gst, Gtk  # noqa: E402

from .. import chemins, controle, formats, moteur, telephone  # noqa: E402
from . import commun  # noqa: E402
from .lecteur import media  # noqa: E402
from .lecture import Apercu  # noqa: E402

TEXTE_PAROLE = (
    "Bonjour ! J’enregistre ma voix pour Voix-là. Je lis ce texte calmement, comme si je racontais une histoire à un "
    "ami.\n\nLe matin, j’aime marcher le long de la rivière, quand l’air est encore frais et que la ville se réveille "
    "doucement. Les oiseaux chantent, un chien aboie au loin, et le boulanger ouvre sa boutique. Parfois, je m’arrête "
    "pour regarder les bateaux glisser sur l’eau.\n\nEnsuite, je rentre préparer un café bien chaud et je relis mes "
    "notes de la veille. Il y a toujours quelque chose à apprendre : une nouvelle chanson, un mot étrange, une idée "
    "qui surprend. Quelle heure est-il ? Déjà midi ! Le temps file vraiment vite.\n\nMerci de votre écoute, et à très "
    "bientôt !")
CONSIGNES_PAROLE = ("30 à 60 s de parole naturelle, dans une pièce calme sans écho, le micro à environ 20 cm, ni "
                    "musique ni télé derrière. Uniquement la voix d’une personne d’accord pour être clonée.")
CONSIGNES_CHANT = (
    "• Chantez SANS musique dans le haut-parleur (des écouteurs sont permis), dans une pièce calme, le micro à 15–20 cm. "
    "Même si ça sonne faux : ce n’est pas grave.\n"
    "• Des chansons que vous connaissez. Variez : notes graves et aiguës, notes tenues, doux et fort, plusieurs "
    "voyelles (a, é, i, o, ou).\n"
    "• Chantez avec des PAROLES, pas seulement en fredonnant.")
NIVEAU = re.compile(r"rms=\([^)]*\)[<{]\s*([-0-9.e+inf, ]+)[>}]")
SOUS_TITRE_TELEPHONE = "Par scrcpy, son sans perte ; niveau visible après la prise"


def sources_micro():
    """[(nom pulse, description)] des entrées (sans les « monitor » des sorties) ; ('', défaut) en premier."""
    sortie = [("", "Micro par défaut du système")]
    try:
        r = subprocess.run(["pactl", "-f", "json", "list", "sources"], capture_output=True, text=True, timeout=10)
        for s in json.loads(r.stdout or "[]"):
            nom = s.get("name") or ""
            if nom.endswith(".monitor") or (s.get("monitor_of_sink") not in (None, "", "n/a")):
                continue
            sortie.append((nom, s.get("description") or nom))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return sortie


class Enregistrement:
    """Un enregistrement GStreamer vers un WAV mono 48 kHz (16 bits en parole, 24 bits en chant)."""

    par_telephone = False
    marge_s = 0          # arrêt par l'interface dès la durée atteinte

    def __init__(self, fichier, micro="", bits=16, sur_niveau=None, sur_fin=None):
        Gst.init(None)
        self.fichier = Path(fichier)
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        fmt = "S24LE" if bits == 24 else "S16LE"
        if os.environ.get("VOIXLA_MICRO_ESSAI"):
            source = "audiotestsrc is-live=true wave=sine freq=196 volume=0.12"
        else:
            source = "pulsesrc" + (f" device={micro}" if micro else "")
        # level n'accepte pas le 24 bits : mesure en flottant, puis conversion au format du fichier
        chaine = (f"{source} ! audioconvert ! audioresample ! audio/x-raw,format=F32LE,rate=48000,channels=1 ! "
                  f"level interval=100000000 post-messages=true ! audioconvert ! audio/x-raw,format={fmt} ! "
                  f"wavenc ! filesink name=fichier")
        self.pipeline = Gst.parse_launch(chaine)
        self.pipeline.get_by_name("fichier").set_property("location", str(self.fichier))
        self.sur_niveau, self.sur_fin = sur_niveau, sur_fin
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        self._ident = bus.connect("message", self._message)
        self.debut = None
        self.termine = False

    def demarrer(self):
        self.debut = time.time()
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._finir("le micro n’a pas pu démarrer")

    def arreter(self):
        """Fin propre : EOS, pour que wavenc écrive l'en-tête du fichier."""
        if not self.termine:
            self.pipeline.send_event(Gst.Event.new_eos())
            GLib.timeout_add(3000, lambda: (self._finir(None) if not self.termine else None, False)[1])

    def abandonner(self):
        self._finir(None, silencieux=True)
        try:
            self.fichier.unlink()
        except OSError:
            pass

    def ecoule(self):
        return time.time() - self.debut if self.debut else 0.0

    def _message(self, _bus, m):
        if m.type == Gst.MessageType.ELEMENT and m.get_structure() and m.get_structure().get_name() == "level":
            r = NIVEAU.search(m.get_structure().to_string())
            if r and self.sur_niveau:
                try:
                    v = [float(x) for x in r.group(1).replace(" ", "").split(",") if x]
                    self.sur_niveau(max(v) if v else -120.0)
                except ValueError:
                    pass
        elif m.type == Gst.MessageType.EOS:
            self._finir(None)
        elif m.type == Gst.MessageType.ERROR:
            err, _dbg = m.parse_error()
            self._finir(err.message)

    def _finir(self, erreur, silencieux=False):
        if self.termine:
            return
        self.termine = True
        self.pipeline.set_state(Gst.State.NULL)
        bus = self.pipeline.get_bus()
        bus.disconnect(self._ident)
        bus.remove_signal_watch()
        if self.sur_fin and not silencieux:
            self.sur_fin(erreur)


class EnregistrementTelephone:
    """Même usage qu'Enregistrement, au micro d'un téléphone (telephone.Prise, dans un fil à part) : vers le même WAV
    mono 48 kHz (16 ou 24 bits). Pas de niveau en direct : scrcpy n'en donne pas sans lecture sur le PC. scrcpy
    s'arrête seul à la durée prévue (--time-limit) ; « Arrêter » lui envoie SIGINT, comme Ctrl+C dans le script."""

    par_telephone = True
    marge_s = 10         # scrcpy met un moment à démarrer : l'interface ne l'arrête qu'après durée + marge

    def __init__(self, fichier, serie, duree, bits=24, sur_fin=None):
        self.fichier = Path(fichier)
        self.prise = telephone.Prise(serie, self.fichier, duree, bits)
        self.sur_fin = sur_fin
        self.debut = None
        self.termine = self.arret_demande = False

    def demarrer(self):
        self.debut = time.time()
        commun.en_fil(self.prise.executer, self._finir, lambda e: self._finir(str(e) or "erreur inattendue"))

    def arreter(self):
        if not self.termine:
            self.arret_demande = True
            self.prise.arreter()

    def abandonner(self):
        """Fenêtre fermée : la prise s'arrête et ses fichiers sont effacés, sans rappel."""
        self.termine = True
        self.prise.abandonner()

    def ecoule(self):
        return time.time() - self.debut if self.debut else 0.0

    def _finir(self, erreur):
        if self.termine:
            return
        self.termine = True
        if self.sur_fin:
            self.sur_fin(erreur)


class DialogueEnregistrement(Adw.Dialog):
    """Assistant : préparer → compte à rebours → enregistrement → contrôle → créer la voix / garder la prise."""

    def __init__(self, fenetre, mode="parole", voix=None, apres=None):
        super().__init__(title="Enregistrer ma voix" if mode == "parole" else "Prise de chant",
                         content_width=640, content_height=700)
        self.fenetre, self.mode, self.voix, self.apres = fenetre, mode, voix, apres
        self.enr = None
        self.fichier = None
        self.resultat = None
        self.lecture = None
        # sources : micros du PC (nom pulse, '' : celui par défaut) puis téléphones (telephone:<série adb>)
        self.micros = [("", "Micro par défaut du système")]
        self.telephones = []
        self._sources = list(self.micros)
        self._choix = None           # source choisie à la main dans ce dialogue
        self._remplissage = False
        self.source = ""
        self._ferme = False
        # réécoute de la prise : coupe la musique en cours, et se met en pause si une autre lecture commence
        self._apercu = Apercu(lambda: self.lecture and self.lecture.pause())
        self.duree = int(fenetre.prefs.get("duree_parole_s" if mode == "parole" else "duree_prise_chant_s")
                         or (60 if mode == "parole" else 120))
        vue = Adw.ToolbarView()
        vue.add_top_bar(Adw.HeaderBar())
        self.pile = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.pile.add_named(self._page_preparer(), "preparer")
        self.pile.add_named(self._page_decompte(), "decompte")
        self.pile.add_named(self._page_enregistrement(), "enregistrement")
        self.pile.add_named(self._page_controle(), "controle")
        vue.set_content(self.pile)
        self.set_child(vue)
        self.connect("closed", lambda *_: self._fermer())
        commun.en_fil(sources_micro, self._micros)
        self.chercher_telephones(reconnecter=False)

    # --- pages -----------------------------------------------------------------------------------------------------

    def _page_preparer(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Avant de commencer",
                                 description=CONSIGNES_PAROLE if self.mode == "parole" else CONSIGNES_CHANT)
        self.r_micro = Adw.ComboRow(title="Micro", model=Gtk.StringList.new(["Micro par défaut du système"]))
        self.r_micro.connect("notify::selected", lambda *_: self._source_choisie())
        g.add(self.r_micro)
        # téléphone : état de la recherche, « Réessayer », adresse sans fil (adb connect)
        self.r_telephone = Adw.ActionRow(title="Micro du téléphone", subtitle="Recherche d’un téléphone…",
                                         subtitle_lines=0)
        self.r_telephone.add_prefix(Gtk.Image(icon_name="phone-symbolic"))
        self.s_telephone = Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER)
        self.r_telephone.add_suffix(self.s_telephone)
        self.b_reessayer = commun.bouton("Réessayer", valign=Gtk.Align.CENTER, clic=self.chercher_telephones,
                                         sensitive=False)
        self.r_telephone.add_suffix(self.b_reessayer)
        g.add(self.r_telephone)
        self.e_adresse = Adw.EntryRow(title="Téléphone sans fil : adresse IP:port (adb connect)",
                                      show_apply_button=True, visible=False)
        self.e_adresse.set_text(self._adresse_connue())
        self.e_adresse.connect("apply", lambda *_: self.chercher_telephones())
        g.add(self.e_adresse)
        self.r_duree = Adw.SpinRow.new_with_range(10, 600, 5)
        self.r_duree.set_title("Durée (s)")
        self.r_duree.set_subtitle("60 s conseillées" if self.mode == "parole" else
                                  "120 s par prise ; « Arrêter » termine plus tôt")
        self.r_duree.set_value(self.duree)
        g.add(self.r_duree)
        if self.mode == "chant":
            g.add(commun.ligne_valeur("Voix", self.fenetre.nom_voix(self.voix)))
        page.add(g)
        if self.mode == "parole":
            gt = Adw.PreferencesGroup(title="Texte à lire",
                                      description="D’une voix posée, comme si vous expliquiez à quelqu’un.")
            gt.add(Gtk.Label(label=TEXTE_PAROLE, wrap=True, xalign=0, selectable=True, css_classes=["texte-a-lire"]))
            page.add(gt)
        gb = Adw.PreferencesGroup()
        b = commun.bouton("Commencer", "media-record-symbolic", styles=("suggested-action", "pill"),
                          clic=self.commencer, halign=Gtk.Align.CENTER)
        gb.add(b)
        page.add(gb)
        return page

    def _page_decompte(self):
        b = commun.boite(espace=12, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, vexpand=True)
        self.l_decompte = Gtk.Label(label="3", css_classes=["compte-rebours"])
        b.append(self.l_decompte)
        b.append(Gtk.Label(label="Préparez-vous…", css_classes=["title-3"]))
        return b

    def _page_enregistrement(self):
        b = commun.boite(espace=14, marges=18)
        tete = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        tete.append(Gtk.Image(icon_name="media-record-symbolic", css_classes=["error"], pixel_size=24))
        self.l_temps = Gtk.Label(css_classes=["title-2", "chiffres"])
        tete.append(self.l_temps)
        b.append(tete)
        self.niveau = Gtk.LevelBar(min_value=0, max_value=1, mode=Gtk.LevelBarMode.CONTINUOUS)
        self.niveau.add_offset_value("bas", 0.55)
        self.niveau.add_offset_value("haut", 0.9)
        self.niveau.add_offset_value("plein", 1.0)
        b.append(self.niveau)
        self.l_niveau = commun.legende("")
        self.l_niveau.set_halign(Gtk.Align.CENTER)
        b.append(self.l_niveau)
        if self.mode == "parole":
            t = Gtk.Label(label=TEXTE_PAROLE, wrap=True, xalign=0, css_classes=["texte-a-lire"])
            b.append(Gtk.ScrolledWindow(child=t, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        else:
            b.append(Gtk.Label(label="Chantez !", css_classes=["title-1"], vexpand=True))
        b.append(commun.bouton("Arrêter", "media-playback-stop-symbolic", styles=("destructive-action", "pill"),
                               clic=self.arreter, halign=Gtk.Align.CENTER))
        return b

    def _page_controle(self):
        page = Adw.PreferencesPage()
        self.g_mesures = Adw.PreferencesGroup(title="Contrôle de qualité")
        page.add(self.g_mesures)
        self.g_suite = Adw.PreferencesGroup()
        page.add(self.g_suite)
        self.page_controle = page
        return page

    # --- déroulement -----------------------------------------------------------------------------------------------

    def _micros(self, liste):
        self.micros = liste
        self._rendre_sources()

    # téléphones : recherche (fil à part), liste des sources, source retenue

    def _adresse_connue(self):
        """Adresse sans fil proposée : ADB_SERIAL, sinon celle du téléphone de la dernière prise."""
        memoire = self.fenetre.prefs.get("source_enregistrement") or ""
        for a in (telephone.serie_preferee(), memoire.removeprefix(telephone.PREFIXE)):
            if telephone.ADRESSE.match(a):
                return a
        return ""

    def chercher_telephones(self, reconnecter=True):
        """Liste les téléphones ; « Réessayer » (et l'adresse validée) fait d'abord « adb connect » sur l'adresse."""
        self.b_reessayer.set_sensitive(False)
        self.s_telephone.set_visible(True)
        self.r_telephone.set_title("Micro du téléphone")
        self.r_telephone.set_subtitle("Recherche d’un téléphone…")
        adresses = [self.e_adresse.get_text().strip()] if reconnecter else []
        commun.en_fil(lambda: telephone.rechercher(adresses), self._telephones_trouves,
                      lambda e: self._telephones_trouves(telephone.Recherche([], "Recherche impossible", str(e))))

    def _telephones_trouves(self, r):
        self.telephones = r.telephones
        self.s_telephone.set_visible(False)
        self.b_reessayer.set_sensitive(True)
        if r.telephones:
            noms = ", ".join(a.nom for a in r.telephones)
            self.r_telephone.set_title("Micro du téléphone")
            self.r_telephone.set_subtitle(commun.echapper(
                f"{noms} : à choisir dans « Micro ». Son sans perte par scrcpy ; pas de niveau en direct, il se voit "
                f"au contrôle après la prise."))
        else:
            self.r_telephone.set_title(commun.echapper(r.titre))
            self.r_telephone.set_subtitle(commun.echapper(r.message))
        # l'adresse sans fil sert quand adb répond mais qu'aucun téléphone n'est prêt
        self.e_adresse.set_visible(not r.telephones and r.titre in (telephone.AUCUN[0],
                                                                    telephone.ETATS["offline"][0]))
        self._rendre_sources()

    def _rendre_sources(self):
        sources = list(self.micros) + [(a.cle, telephone.libelle(a, self.telephones)) for a in self.telephones]
        cles = [c for c, _d in sources]
        voulue = self._cle_voulue(cles)
        self._remplissage = True
        try:
            self._sources = sources
            self.r_micro.set_model(Gtk.StringList.new([d for _c, d in sources]))
            self.r_micro.set_selected(cles.index(voulue) if voulue in cles else 0)
        finally:
            self._remplissage = False
        self._source_choisie(manuel=False)

    def _cle_voulue(self, cles):
        """Le choix fait dans ce dialogue, sinon la source de la dernière prise, sinon le micro des Réglages. Un
        téléphone d'avant revenu sous une autre série (port sans fil changé) cède la place au premier téléphone."""
        for cle in (self._choix, self.fenetre.prefs.get("source_enregistrement")):
            if not cle:
                continue
            if cle in cles:
                return cle
            if cle.startswith(telephone.PREFIXE):
                tels = [c for c in cles if c.startswith(telephone.PREFIXE)]
                if tels:
                    return tels[0]
        return self.fenetre.prefs.get("micro") or ""

    def _source(self):
        i = self.r_micro.get_selected()
        return self._sources[i][0] if 0 <= i < len(self._sources) else ""

    def _source_choisie(self, manuel=True):
        cle = self._source()
        if manuel and not self._remplissage:
            self._choix = cle
        self.r_micro.set_subtitle(SOUS_TITRE_TELEPHONE if cle.startswith(telephone.PREFIXE) else "")

    def _libelle_source(self, cle):
        return next((d for c, d in self._sources if c == cle), cle)

    def commencer(self):
        self.duree = int(self.r_duree.get_value())
        self.source = self._source()
        # source de la dernière prise : un téléphone, ou '' (le micro des Réglages, clé « micro »)
        memoire = self.source if self.source.startswith(telephone.PREFIXE) else ""
        if (self.fenetre.prefs.get("source_enregistrement") or "") != memoire:
            self.fenetre.prefs = chemins.modifier_preference("source_enregistrement", memoire)
        self.pile.set_visible_child_name("decompte")
        self._compte = 3
        self.l_decompte.set_label("3")
        GLib.timeout_add(1000, self._decompter)

    def _decompter(self):
        if self._ferme:          # fenêtre fermée pendant le compte à rebours : aucune prise ne commence
            return False
        self._compte -= 1
        if self._compte > 0:
            self.l_decompte.set_label(str(self._compte))
            return True
        self._enregistrer()
        return False

    def _chemin(self):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        voix = chemins.dossier_voix(self.fenetre.donnees)
        if self.mode == "parole":
            return voix / ".enregistrements" / f"parole-{stamp}.wav"
        return voix / self.voix / "chant" / f"chant-{stamp}.wav"

    def _enregistrer(self):
        self.fichier = self._chemin()
        bits = 24 if self.mode == "chant" else 16
        if self.source.startswith(telephone.PREFIXE):
            self.enr = EnregistrementTelephone(self.fichier, self.source.removeprefix(telephone.PREFIXE), self.duree,
                                               bits, sur_fin=self._fin_enregistrement)
        else:
            self.enr = Enregistrement(self.fichier, self.source, bits, sur_niveau=self._niveau,
                                      sur_fin=self._fin_enregistrement)
        self._page_selon_source(self.enr.par_telephone)
        self.pile.set_visible_child_name("enregistrement")
        self.enr.demarrer()
        self._tic()
        GLib.timeout_add(250, self._tic)

    def _page_selon_source(self, par_telephone):
        """Niveau en direct au micro du PC ; au téléphone, aucun (rien d'honnête à montrer) : une phrase à la place."""
        self.niveau.set_visible(not par_telephone)
        self.niveau.set_value(0)
        self.l_niveau.set_label(
            f"{self._libelle_source(self.source)} : pas de niveau en direct, il se voit au contrôle après la prise."
            if par_telephone else "")

    def _tic(self):
        if not self.enr or self.enr.termine:
            return False
        e = self.enr.ecoule()
        self.l_temps.set_label(f"{formats.chrono(min(e, self.duree))} / {formats.chrono(self.duree)}")
        if self.enr.par_telephone and (e >= self.duree or self.enr.arret_demande):
            self.l_niveau.set_label("Fin de la prise sur le téléphone, puis conversion…")
        if e >= self.duree + self.enr.marge_s:
            self.arreter()
            return False
        return True

    def _niveau(self, db):
        self.niveau.set_value(max(0.0, min(1.0, (db + 60) / 60)))
        self.l_niveau.set_label(f"niveau {formats.nombre(db, 0)} dBFS" + (" — trop fort !" if db > -6 else
                                                                         " — trop faible" if db < -45 else ""))

    def arreter(self):
        if self.enr and not self.enr.termine:
            self.enr.arreter()

    def _fin_enregistrement(self, erreur):
        if erreur:
            self.fenetre.toast(f"Enregistrement impossible : {erreur}")
            self.pile.set_visible_child_name("preparer")
            if self.enr and self.enr.par_telephone:
                self.chercher_telephones(reconnecter=False)     # le téléphone est peut-être parti
            return
        self._afficher_controle(None)
        f = self.fichier
        duree = self.duree
        fonction = (lambda: controle.controle_parole(f)) if self.mode == "parole" else \
            (lambda: controle.controle_chant(f, duree))
        commun.en_fil(fonction, self._afficher_controle)

    def _vider(self, groupe):
        for r in list(getattr(groupe, "_lignes", [])):
            groupe.remove(r)
        groupe._lignes = []

    def _ajouter(self, groupe, widget):
        groupe.add(widget)
        groupe._lignes = getattr(groupe, "_lignes", []) + [widget]

    def _afficher_controle(self, res):
        self.pile.set_visible_child_name("controle")
        self._vider(self.g_mesures)
        self._vider(self.g_suite)
        if res is None:
            r = Adw.ActionRow(title="Analyse de l’enregistrement…")
            r.add_suffix(Gtk.Spinner(spinning=True))
            self._ajouter(self.g_mesures, r)
            return
        self.resultat = res
        valeurs = [("Durée", formats.duree(res.get("duree_s"))),
                   ("Crête", f"{formats.nombre(res.get('crete_db'))} dBFS (sous −1)")]
        if self.mode == "parole":
            valeurs.append(("Volume moyen", f"{formats.nombre(res.get('moyen_db'))} dB (idéal −26 à −16)"))
        else:
            valeurs.append(("Sonie", f"{formats.nombre(res.get('lufs'))} LUFS (idéal −26 à −16)"))
            valeurs.append(("Silence", f"{formats.nombre(res.get('silence_pct'), 0)} % (idéal sous 30 %)"))
        valeurs.append(("Bruit de fond", f"{formats.nombre(res.get('bruit_db'))} dBFS (idéal sous −55)"))
        for a, b in valeurs:
            self._ajouter(self.g_mesures, commun.ligne_valeur(a, b))
        if res["alertes"]:
            for a in res["alertes"]:
                l = Adw.ActionRow(title=commun.echapper(a), title_lines=4)
                l.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
                self._ajouter(self.g_mesures, l)
        else:
            l = Adw.ActionRow(title="Qualité correcte")
            l.add_prefix(Gtk.Image(icon_name="object-select-symbolic", css_classes=["success"]))
            self._ajouter(self.g_mesures, l)
        ecouter = Adw.ActionRow(title="Écouter l’enregistrement", activatable=True)
        ecouter.add_prefix(Gtk.Image(icon_name="media-playback-start-symbolic"))
        ecouter.connect("activated", lambda *_: self._ecouter())
        self._ajouter(self.g_suite, ecouter)
        if self.mode == "parole":
            self.e_nom = Adw.EntryRow(title="Nom de la voix (minuscules, chiffres, - ou _)")
            self.e_nom.connect("changed", lambda *_: self._valider_nom())
            self._ajouter(self.g_suite, self.e_nom)
            self.c_accord = Adw.SwitchRow(title="C’est ma voix, ou celle d’une personne d’accord pour être clonée")
            self.c_accord.connect("notify::active", lambda *_: self._valider_nom())
            self._ajouter(self.g_suite, self.c_accord)
        boutons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=12)
        boutons.append(commun.bouton("Recommencer", "edit-undo-symbolic", clic=self.recommencer))
        if self.mode == "parole":
            self.b_creer = commun.bouton("Créer la voix", styles=("suggested-action",), clic=self.creer_voix,
                                         sensitive=False)
            boutons.append(self.b_creer)
        else:
            boutons.append(commun.bouton("Supprimer la prise", styles=("destructive-action",), clic=self.jeter))
            boutons.append(commun.bouton("Garder la prise", styles=("suggested-action",), clic=self.garder))
        self._ajouter(self.g_suite, boutons)

    def _valider_nom(self):
        nom = self.e_nom.get_text().strip()
        existe = (chemins.dossier_voix(self.fenetre.donnees) / nom / "voix.json").exists()
        ok = bool(moteur.NOM_VOIX.fullmatch(nom)) and not existe
        if nom and not ok:
            self.e_nom.add_css_class("error")
        else:
            self.e_nom.remove_css_class("error")
        self.b_creer.set_sensitive(ok and self.c_accord.get_active())
        self.b_creer.set_tooltip_text("Cette voix existe déjà" if existe else None)

    def _ecouter(self):
        if self.lecture:
            self.lecture.pause()
        self.lecture = media(self.fichier, self._apercu)
        self.lecture.play()

    def recommencer(self):
        if self.lecture:
            self.lecture.pause()
        try:
            if self.fichier:
                self.fichier.unlink()
        except OSError:
            pass
        self.pile.set_visible_child_name("preparer")

    def jeter(self):
        self.recommencer()
        self.fenetre.toast("Prise supprimée")
        self.close()
        if self.apres:
            self.apres()

    def garder(self):
        self.fenetre.toast(f"Prise gardée : {self.fichier.name}")
        self.fichier = None
        self.close()
        if self.apres:
            self.apres()

    def creer_voix(self):
        nom = self.e_nom.get_text().strip()
        self.b_creer.set_sensitive(False)
        self.b_creer.set_label("Création…")
        f, donnees = self.fichier, self.fenetre.donnees

        def fini(r):
            ok, message, alertes = r
            if not ok:
                self.b_creer.set_label("Créer la voix")
                self.b_creer.set_sensitive(True)
                commun.informer(self.fenetre, "La voix n’a pas pu être créée", message)
                return
            # l'enregistrement brut rejoint la voix (utile pour un affinage futur)
            dest = chemins.dossier_voix(donnees) / nom / f"enregistrement-{f.stem.split('-', 1)[1]}.wav"
            try:
                shutil.move(str(f), str(dest))
            except OSError:
                pass
            self.fichier = None
            self.fenetre.recharger_voix()
            self.fenetre.toast(f"Voix {nom} créée" + (f" — {len(alertes)} alerte(s)" if alertes else ""))
            if alertes:
                commun.informer(self.fenetre, f"Voix {nom} créée, avec des alertes", "\n".join(alertes))
            self.close()
            if self.apres:
                self.apres()
        commun.en_fil(lambda: moteur.ajouter_voix(nom, f, donnees), fini)

    def _fermer(self):
        self._ferme = True
        if self.enr and not self.enr.termine:
            self.enr.abandonner()
        if self.lecture:
            self.lecture.pause()

    def scenario(self, etape):
        """Pour les captures : montrer une étape sans micro (« telephone » : choisir le premier téléphone, imité par
        VOIXLA_TELEPHONE_ESSAI=1)."""
        if etape == "telephone":
            tels = [i for i, (c, _d) in enumerate(self._sources) if c.startswith(telephone.PREFIXE)]
            if tels:
                self.r_micro.set_selected(tels[0])
        elif etape == "enregistrement":
            self.source = self._source()
            par_telephone = self.source.startswith(telephone.PREFIXE)
            self.pile.set_visible_child_name("enregistrement")
            self._page_selon_source(par_telephone)
            self.l_temps.set_label("0:12 / 1:00")
            if not par_telephone:
                self._niveau(-18)
        elif etape == "controle":
            self._afficher_controle({"duree_s": 58.4, "crete_db": -3.2, "moyen_db": -21.5, "lufs": -20.9,
                                     "bruit_db": -58.1, "silence_pct": 12, "alertes": []})
