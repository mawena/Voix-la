"""Page Doubler : choisir des vidéos (sélecteur ou glisser-déposer), un extrait facultatif par vidéo, les réglages, et
lancer. Les vidéos qui ont le même extrait partent dans UN seul appel doubler, donc une seule session Kaggle."""

import subprocess
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .. import chemins, formats, moteur, quota, rapports, secours, systeme, travaux
from . import commun
from .lecteur import Lecteur

MODES = [("auto", "Automatique"), ("chant", "Chant"), ("parole", "Parole")]
OCTAVES = [("auto", "Automatique"), ("0", "0 (aucun décalage)"), ("-12", "−12 (une octave plus bas)"),
           ("+12", "+12 (une octave plus haut)")]
EXT_VIDEOS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
TEXTE_OCTAVE = ("Automatique : en chant, un seul décalage par chanteur, choisi d’après la hauteur (l’empreinte de la "
                "voix départage les cas ambigus) ; un duo, retenu à quatre conditions, a un décalage par voix. "
                "Sinon imposée partout")
TEXTE_TRANSPOSER = ("Allumé : une chanson trop haute ou trop basse pour ta voix est transposée en entier, musique "
                    "comprise (comme un changement de tonalité au karaoké), de 5 demi-tons au plus. Éteint : la "
                    "musique reste intacte")
INFOBULLE_TRANSPOSER = ("Tes proches ne reconnaissent ta voix que lorsqu’elle chante près de sa zone naturelle. "
                        "Si la chanson est hors de ta tessiture, elle est transposée d’un nombre entier de demi-tons "
                        "(5 au plus, le même pour toute la chanson), la musique avec la voix. À éteindre pour garder "
                        "la musique d’origine : la voix est alors seulement décalée d’une octave quand il le faut. "
                        "Sans effet sur la parole. Demande la bibliothèque Rubber Band (paquet librubberband2)")
TEXTE_PAS = ("30 par défaut ; 50 : environ 40 % de temps GPU de conversion en plus. Avec YingMusic, 50 ou 100 "
             "n’apportent rien de mesurable")
TEXTE_PAROLES = ("Transcription des paroles (Whisper) et traduction française si la chanson n’est pas en français, "
                 "affichées sous le lecteur des résultats")
CALCULS_LIBELLES = ["Selon la configuration", *[l for _c, l in secours.CALCULS]]
MOTEURS =[("", "Par défaut"), ("yingmusic", "YingMusic-SVC"), ("seedvc", "Seed-VC 44 kHz")]
# Chaîne de conversion du chant : raccourcis vers les réglages moteur / nettoyage
CHAINES = [("defaut", "Par défaut"), ("avant", "Chaîne d’avant"), ("mesure", "Sur mesure")]
# Une voix par chanteur (doubler --voix homme:a,femme:b ou chanteur1:a,chanteur2:b)
# Comparer plusieurs voix (doubler --comparer A,B,C) : le même extrait dans chacune, en une seule session
CHOIX_VOIX = [("une", "Une voix pour tous"), ("chanteurs", "Une voix par chanteur"),
              ("comparer", "Comparer plusieurs voix")]
COMPARER_MAXI = 6
TEXTE_COMPARER = ("Cochez deux voix ou plus : le même extrait est doublé dans chacune, en une seule session Kaggle. "
                  "Envoi, séparation, nettoyage et paroles ne sont payés qu’une fois ; seule la conversion est "
                  "refaite pour chaque voix. Les résultats s’écoutent ensuite à l’aveugle, côte à côte")
DUOS = [("genre", "Duo mixte"), ("ordre", "Même genre")]   # homme et femme ; deux chanteurs, ordre d'apparition
LIBELLES_CHANTEURS = {"genre": ("Voix d’homme", "Voix de femme"), "ordre": ("Chanteur 1", "Chanteur 2")}
SOUS_TITRES_CHANTEURS = {
    "genre": ("Voix qui remplace le chanteur", "Voix qui remplace la chanteuse"),
    "ordre": ("Voix de celui qui chante le premier", "Voix de l’autre chanteur"),
}
TEXTE_PAR_CHANTEUR = ("Duo mixte : un homme et une femme ; même genre : chanteur 1, celui qui chante le premier. Chaque "
                      "passage, et dans un duo la voix principale et les chœurs, prend la voix du chanteur qu’on y "
                      "reconnaît ; si la chanson n’a qu’une voix, tout prend la voix de sa classe (sinon la première). "
                      "En parole, la première voix seule")
TEXTE_AUTOTUNE = ("Version « (auto-tune) » en plus de la normale, qui ne change pas : x42-autotune sur la voix sèche, "
                  "dans la gamme détectée sur la musique")
TEXTE_X42_ABSENT = "x42-autotune absent : installez x42-plugins (sudo apt install x42-plugins)"
EXPLICATIONS_CHAINES = {
    "defaut": "Voix principale et chœurs séparés, reverb retirée, moteur de la configuration (YingMusic-SVC) : "
              "environ deux fois plus de GPU",
    "avant": "Seed-VC 44 kHz sur la voix isolée complète, sans nettoyage : plus rapide",
    "mesure": "Moteur et nettoyage choisis ci-dessous",
}


def reglages_autotune(titre="Auto-tune", sous_titre=TEXTE_AUTOTUNE):
    """Les réglages d'auto-tune de la page Doubler et du remix rapide des Résultats : (interrupteur, intensité, gamme).
    L'interrupteur est une Adw.ExpanderRow éteinte, qui déplie l'intensité (Gtk.Scale de 1 à 100, AUTOTUNE_DEFAUT
    marqué) et la gamme (Adw.ComboRow sur moteur.GAMMES : Auto, Chromatique, les 24 tonalités)."""
    rangee = Adw.ExpanderRow(title=titre, subtitle=sous_titre, show_enable_switch=True, enable_expansion=False)
    r_intensite = Adw.ActionRow(title="Intensité",
                                subtitle="Bas : notes juste rendues plus justes ; haut : effet robotique")
    echelle = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 100, 1)
    echelle.set_value(moteur.AUTOTUNE_DEFAUT)
    echelle.set_size_request(220, -1)
    echelle.set_valign(Gtk.Align.CENTER)
    echelle.set_draw_value(True)
    echelle.set_value_pos(Gtk.PositionType.LEFT)
    echelle.set_digits(0)
    echelle.add_mark(moteur.AUTOTUNE_DEFAUT, Gtk.PositionType.BOTTOM, None)
    echelle.update_property([Gtk.AccessibleProperty.LABEL], ["Intensité de l’auto-tune, de 1 à 100"])
    r_intensite.add_suffix(echelle)
    rangee.add_row(r_intensite)
    gamme = Adw.ComboRow(title="Gamme", model=Gtk.StringList.new([x[1] for x in moteur.GAMMES]),
                         subtitle="Auto : la tonalité détectée sur la musique seule (chromatique si elle est peu sûre)")
    rangee.add_row(gamme)
    return rangee, echelle, gamme


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
        self._conf = {}                # « doubler --config --json » (cartes GPU prévues pour l'estimation)
        self._sans_memoire = False     # vrai pendant les changements faits par la page (pas un choix à garder)
        self.entete.pack_start(commun.bouton(icone="list-add-symbolic", infobulle="Ajouter des vidéos",
                                             clic=self.choisir))

        self.banniere = Adw.Banner(button_label="Réglages")
        self.banniere.connect("button-clicked", lambda *_: fenetre.ouvrir_reglages())
        # alerte du quota GPU (moins de quota_alerte_h heures restantes cette semaine)
        self.banniere_quota = Adw.Banner(button_label="Voir le quota")
        self.banniere_quota.connect("button-clicked", lambda *_: fenetre.aller("etat"))
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
        # une voix pour tous, ou une par chanteur (si doubler l'annonce dans son aide)
        self.r_choix_voix = Adw.ComboRow(title="Voix cible", model=Gtk.StringList.new([c[1] for c in CHOIX_VOIX]),
                                         visible=False)
        self.r_choix_voix.connect("notify::selected", lambda *_: self._choix_voix_change())
        g.add(self.r_choix_voix)
        self.r_voix = Adw.ComboRow(title="Voix", subtitle="Voix clonée qui remplace celle de la vidéo")
        self.r_voix.connect("notify::selected", lambda *_: self._maj())
        g.add(self.r_voix)
        self.r_duo = Adw.ComboRow(title="Chanteurs", model=Gtk.StringList.new([d[1] for d in DUOS]), visible=False,
                                  subtitle=TEXTE_PAR_CHANTEUR)
        self.r_duo.connect("notify::selected", lambda *_: self._choix_voix_change())
        g.add(self.r_duo)
        # comparer plusieurs voix : une case par voix (deux au moins), l'ordre est celui de la liste des voix
        self.r_comparees = Adw.ExpanderRow(title="Voix à comparer", subtitle=TEXTE_COMPARER, visible=False,
                                           expanded=True)
        self._lignes_comparees = {}     # identifiant → (ligne, case)
        self._choix_dispo = list(CHOIX_VOIX)
        g.add(self.r_comparees)
        self.r_voix_a = Adw.ComboRow(title=LIBELLES_CHANTEURS["genre"][0], visible=False)
        self.r_voix_b = Adw.ComboRow(title=LIBELLES_CHANTEURS["genre"][1], visible=False)
        for r in (self.r_voix_a, self.r_voix_b):
            r.connect("notify::selected", lambda *_: self._maj())
            g.add(r)
        # le choix, le duo et les deux voix sont gardés dans interface.json d'une session à l'autre
        for r in (self.r_choix_voix, self.r_duo, self.r_voix_a, self.r_voix_b):
            r.connect("notify::selected", lambda *_: self._memoriser_chanteurs())
        self.r_mode = Adw.ComboRow(title="Mode", model=Gtk.StringList.new([m[1] for m in MODES]),
                                   subtitle="Automatique : chant s’il y a une musique sous la voix et que la voix "
                                            "chante ou rappe, sinon parole")
        self.r_mode.connect("notify::selected", lambda *_: self._maj())
        g.add(self.r_mode)
        self.r_octave = Adw.ComboRow(title="Octave", model=Gtk.StringList.new([o[1] for o in OCTAVES]),
                                     subtitle=TEXTE_OCTAVE)
        g.add(self.r_octave)
        # transposition de la chanson : visible seulement si doubler annonce --transposer ; allumée par défaut
        self.r_transposer = Adw.SwitchRow(title="Transposer la chanson dans ma tessiture", active=True, visible=False,
                                          subtitle=TEXTE_TRANSPOSER)
        self.r_transposer.set_tooltip_text(INFOBULLE_TRANSPOSER)
        g.add(self.r_transposer)
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
        # où tourne le GPU : visible seulement si doubler annonce --calcul ; le défaut vient des Réglages (préférence
        # « calcul »), sinon de la configuration de doubler
        self.r_calcul = Adw.ComboRow(title="Calcul (GPU)", model=Gtk.StringList.new(CALCULS_LIBELLES),
                                     subtitle="Kaggle est gratuit. Modal : crédit gratuit mensuel. Runpod : payant, le "
                                              "pod est supprimé à la fin. Auto : Kaggle, puis Modal s’il n’est pas "
                                              "disponible")
        self.r_calcul.set_visible(False)
        self.r_calcul.connect("notify::selected", lambda *_: self._maj())
        g.add(self.r_calcul)
        # paroles : visible seulement si doubler annonce --sans-paroles ; par défaut, la clé « paroles » de sa
        # configuration (vraie si absente)
        self.r_paroles = Adw.SwitchRow(title="Paroles", active=True, visible=False, subtitle=TEXTE_PAROLES)
        self._paroles_touchees = False
        self._paroles_auto = False
        self.r_paroles.connect("notify::active", lambda *_: self._paroles_auto or
                               setattr(self, "_paroles_touchees", True))
        g.add(self.r_paroles)
        # auto-tune : visible si doubler annonce --autotune ; par défaut, la clé « autotune » de sa configuration ;
        # grisé si doubler ne trouve pas x42-autotune
        self.r_autotune, self.at_echelle, self.r_at_gamme = reglages_autotune()
        self.r_autotune.set_visible(False)
        self._autotune_touche = False
        self._autotune_auto = False
        self._autotune_conf = 0
        self.r_autotune.connect("notify::enable-expansion", lambda *_: self._autotune_auto or
                                setattr(self, "_autotune_touche", True))
        g.add(self.r_autotune)
        page.add(g)

        # Lancer
        g_lancer = Adw.PreferencesGroup()
        barre = Gtk.Box(spacing=12, margin_top=6)
        colonne = commun.boite(espace=4, hexpand=True, valign=Gtk.Align.CENTER)
        self.resume = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        colonne.append(self.resume)
        # quota GPU : « ce doublage prendra environ X min de GPU ; il restera Y h », à côté de « Lancer »
        self.l_quota = Gtk.Label(xalign=0, wrap=True, css_classes=["quota-doubler"])
        self.l_quota.set_visible(False)
        colonne.append(self.l_quota)
        barre.append(colonne)
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
        corps.append(self.banniere_quota)
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
        fenetre.ecouter("quota", self._maj)
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
        # une voix par chanteur : le choix et le duo gardés (les deux voix, elles, attendent la liste des voix)
        par, duo, _voix = chemins.choix_chanteurs(p)
        self._sans_memoire = True
        try:
            self.r_choix_voix.set_selected(next(i for i, c in enumerate(CHOIX_VOIX)
                                                if c[0] == ("chanteurs" if par else "une")))
            self.r_duo.set_selected(next((i for i, d in enumerate(DUOS) if d[0] == duo), 0))
        finally:
            self._sans_memoire = False

    def _voix_changees(self):
        voix = self.fenetre.voix
        # les choix en cours, lus avant que la liste change (leur position dans le menu change avec elle)
        actuelle = self._choix_menu(self.r_voix) if hasattr(self, "_ids_voix") else None
        gardes = [self._choix_menu(r) for r in (self.r_voix_a, self.r_voix_b)]
        self._ids_voix = [v["id"] for v in voix]
        # voix cochées pour la comparaison : celles en cours, sinon celles gardées dans interface.json
        cochees = (set(self.voix_a_comparer()) if self._lignes_comparees else
                   {v for v in (self.fenetre.prefs.get("voix_comparees") or []) if isinstance(v, str)})
        # une voix par chanteur : le choix en cours, sinon celui gardé dans interface.json s'il existe encore ; une
        # voix disparue n'est pas présélectionnée et « Lancer » attend un choix
        memoire = chemins.choix_chanteurs(self.fenetre.prefs, self._ids_voix)[2]
        noms = [f"{v['nom']} ({v['id']})" if v["nom"].lower() != v["id"] else v["nom"] for v in voix] or ["aucune voix"]
        self._sans_memoire = True
        try:
            self.r_voix.set_model(Gtk.StringList.new(noms))
            cible = actuelle if actuelle in self._ids_voix else self.fenetre.voix_defaut()
            # sans voix par défaut (plusieurs voix, aucune désignée) : rien n'est présélectionné, « Lancer » attend un
            # choix
            if cible in self._ids_voix:
                self.r_voix.set_selected(self._ids_voix.index(cible))
            elif voix:
                self.r_voix.set_selected(Gtk.INVALID_LIST_POSITION)
            self.r_voix.set_sensitive(bool(voix))
            for r, garde, gardee in zip((self.r_voix_a, self.r_voix_b), gardes, memoire):
                r.set_model(Gtk.StringList.new(noms))
                cible = garde if garde in self._ids_voix else gardee
                r.set_selected(self._ids_voix.index(cible) if cible in self._ids_voix else Gtk.INVALID_LIST_POSITION)
                r.set_sensitive(bool(voix))
        finally:
            self._sans_memoire = False
        self._refaire_comparees(voix, cochees)
        self._maj()

    def _refaire_comparees(self, voix, cochees):
        """Une case par voix dans « Voix à comparer » (les cochées gardées)."""
        self._sans_memoire = True
        try:
            for ligne, _ in self._lignes_comparees.values():
                self.r_comparees.remove(ligne)
            self._lignes_comparees = {}
            for v in voix:
                case = Gtk.CheckButton(active=v["id"] in cochees, valign=Gtk.Align.CENTER)
                ligne = Adw.ActionRow(title=commun.echapper(v["nom"]), activatable_widget=case)
                if v["nom"].lower() != v["id"]:
                    ligne.set_subtitle(commun.echapper(v["id"]))
                ligne.add_prefix(case)
                case.connect("toggled", lambda *_: self._comparees_changees())
                self.r_comparees.add_row(ligne)
                self._lignes_comparees[v["id"]] = (ligne, case)
        finally:
            self._sans_memoire = False

    def voix_a_comparer(self):
        """Les voix cochées pour la comparaison, dans l'ordre de la liste des voix (la première sert à l'analyse)."""
        return [i for i in getattr(self, "_ids_voix", []) if i in self._lignes_comparees
                and self._lignes_comparees[i][1].get_active()]

    def _comparees_changees(self):
        """Une case cochée ou décochée : gardée dans interface.json, estimation et bouton « Lancer » refaits."""
        if not self._sans_memoire:
            cochees = self.voix_a_comparer()
            if cochees != list(self.fenetre.prefs.get("voix_comparees") or []):
                self.fenetre.prefs = chemins.modifier_preferences({"voix_comparees": cochees})
        self._maj()

    def _choix_menu(self, r):
        ids = getattr(self, "_ids_voix", [])
        i = r.get_selected()
        return ids[i] if 0 <= i < len(ids) else None

    def _memoriser_chanteurs(self):
        """Un choix fait à la main (une voix pour tous ou par chanteur, duo, voix de chaque chanteur) est gardé dans
        interface.json. Une ligne sans voix choisie garde la voix gardée jusque-là."""
        if self._sans_memoire or not hasattr(self, "_ids_voix"):
            return
        avant = chemins.choix_chanteurs(self.fenetre.prefs)
        i, j = self.r_choix_voix.get_selected(), self.r_duo.get_selected()
        par = 0 <= i < len(self._choix_dispo) and self._choix_dispo[i][0] == "chanteurs"
        duo = DUOS[j][0] if 0 <= j < len(DUOS) else avant[1]
        voix = [self._choix_menu(r) or v for r, v in zip((self.r_voix_a, self.r_voix_b), avant[2])]
        if (par, duo, voix) == avant:
            return
        self.fenetre.prefs = chemins.modifier_preferences({"voix_par_chanteur": par, "duo_chanteurs": duo,
                                                           "voix_chanteurs": voix})

    def par_chanteur(self):
        """Vrai si « Une voix par chanteur » est choisi (et que doubler le connaît)."""
        return self._choix_courant() == "chanteurs"

    def comparer(self):
        """Vrai si « Comparer plusieurs voix » est choisi (et que doubler connaît --comparer)."""
        return self._choix_courant() == "comparer"

    def _choix_courant(self):
        i = self.r_choix_voix.get_selected()
        if not self.r_choix_voix.get_visible() or not 0 <= i < len(self._choix_dispo):
            return "une"
        return self._choix_dispo[i][0]

    def _modele_choix(self):
        """Les façons de choisir la voix que doubler connaît (une voix par chanteur, comparer) ; le choix en cours est
        gardé."""
        caps = self.fenetre.caps
        dispo = [c for c in CHOIX_VOIX if c[0] == "une" or (c[0] == "chanteurs" and "voix_par_chanteur" in caps)
                 or (c[0] == "comparer" and "--comparer" in caps)]
        modele = self.r_choix_voix.get_model()
        if [c[0] for c in dispo] == [c[0] for c in self._choix_dispo] and modele is not None \
                and modele.get_n_items() == len(dispo):
            return
        courant = self._choix_courant()
        self._sans_memoire = True
        try:
            self._choix_dispo = dispo
            self.r_choix_voix.set_model(Gtk.StringList.new([c[1] for c in dispo]))
            self.r_choix_voix.set_selected(next((i for i, c in enumerate(dispo) if c[0] == courant), 0))
        finally:
            self._sans_memoire = False

    def _choix_voix_change(self):
        chanteurs, comparer = self.par_chanteur(), self.comparer()
        mode = DUOS[self.r_duo.get_selected()][0]
        self.r_comparees.set_visible(comparer)
        self.r_voix.set_visible(not chanteurs and not comparer)
        for r, titre, sous in zip((self.r_voix_a, self.r_voix_b), LIBELLES_CHANTEURS[mode], SOUS_TITRES_CHANTEURS[mode]):
            r.set_visible(chanteurs)
            r.set_title(titre)
            r.set_subtitle(sous)
        self.r_duo.set_visible(chanteurs)
        self._rendre()

    def _voix_choisie(self):
        """L'identifiant de la voix, ou la spécification --voix d'une voix par chanteur (« homme:a,femme:b ») ; None
        tant que le choix n'est pas complet."""
        if self.comparer():
            c = self.voix_a_comparer()
            return c[0] if len(c) >= 2 else None   # la première voix sert à l'analyse ; reglages() liste toutes les voix
        if self.par_chanteur():
            a, b = self._choix_menu(self.r_voix_a), self._choix_menu(self.r_voix_b)
            return moteur.spec_voix(DUOS[self.r_duo.get_selected()][0], [a, b]) if a and b else None
        return self._choix_menu(self.r_voix)

    def montrer(self):
        super().montrer()
        self._donnees_changees()
        self.fenetre.actualiser_quota()

    def _donnees_changees(self):
        self.g_videos.set_description(
            "Une ou plusieurs vidéos, chacune avec son extrait facultatif : elles partent ensemble dans une seule "
            "session Kaggle." if "extrait_par_video" in self.fenetre.caps else
            "Une ou plusieurs vidéos : celles qui ont le même extrait partent ensemble dans une seule session Kaggle.")
        self.r_mix_v1.set_visible("--mix-v1" in self.fenetre.caps)
        self.r_transposer.set_visible("--transposer" in self.fenetre.caps)
        self.r_paroles.set_visible("--sans-paroles" in self.fenetre.caps)
        self._modele_choix()
        self.r_choix_voix.set_visible("voix_par_chanteur" in self.fenetre.caps or "--comparer" in self.fenetre.caps)
        self.r_autotune.set_visible("--autotune" in self.fenetre.caps)
        self.r_at_gamme.set_visible("--autotune-gamme" in self.fenetre.caps)
        self._choix_voix_change()
        # options récentes de doubler : masquées tant que son aide ne les annonce pas
        self.r_chaine.set_visible("--moteur" in self.fenetre.caps and "--sans-nettoyage" in self.fenetre.caps)
        self._chaine_changee()
        if hasattr(self, "r_calcul"):
            self.r_calcul.set_visible("--calcul" in self.fenetre.caps)
            cles = ["", *[c for c, _l in secours.CALCULS]]
            self.r_calcul.set_selected(cles.index(secours.calcul_choisi(self.fenetre.prefs)))
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
        """Durées prévues par étape ; la session Kaggle, celle que doubler calcule lui-même sur les rapports du dossier
        des sorties (rapports.estimer avec sorties), la même qu'il annoncera au lancement."""
        caps = self.fenetre.caps
        nettoyage = "--sans-nettoyage" in caps and not reglages.get("sans_nettoyage")
        paroles = "--sans-paroles" in caps and not reglages.get("sans_paroles")
        # deux cartes T4 (clé « gpu » à auto ou 2 sur la machine par défaut) : le chant nettoyé va plus vite
        cartes = moteur.cartes_prevues(self._conf, caps)
        voix = reglages.get("voix")
        voix_cibles = max(1, len(moteur.membres_voix(moteur.id_voix(voix)))) if voix else 1
        comparees = len(reglages.get("comparer") or []) or 1
        return rapports.estimer(durees, reglages.get("pas", 30), self.modele, nettoyage=nettoyage,
                                mode=reglages.get("mode", "auto"), moteur=reglages.get("moteur") or None, cartes=cartes,
                                paroles=paroles, voix_cibles=voix_cibles, voix_comparees=comparees,
                                sorties=chemins.dossier_sorties(self.fenetre.donnees))

    def gpu_par_defaut_change(self, gpu):
        """Depuis les Réglages : nouvelle clé « gpu » de doubler, l'estimation de durée suit."""
        self._conf = {**(self._conf or {}), "gpu": gpu}
        self._maj()

    def _charger_modele(self):
        sorties = chemins.dossier_sorties(self.fenetre.donnees)

        def fini(m):
            self.modele = m
            self._maj()

        def charger():
            moteur.module_doubler()   # doubler.py chargé ici, hors de la boucle de l'interface (son estimation)
            return rapports.modele_durees(rapports.lister_resultats(sorties))
        commun.en_fil(charger, fini)

    def paroles_par_defaut_change(self, oui):
        """Depuis les Réglages : le nouveau défaut remplace le choix fait à la main."""
        self._paroles_touchees = False
        self._paroles_defaut(oui)

    def _paroles_defaut(self, oui):
        """Le réglage par défaut de doubler (clé « paroles ») ; un choix fait à la main dans la page est gardé.
        À « non », --sans-paroles ne peut que les retirer : l'interrupteur est alors grisé, avec la raison."""
        if not oui:
            self._paroles_auto = True
            self.r_paroles.set_active(False)
            self._paroles_auto = False
            self.r_paroles.set_sensitive(False)
            self.r_paroles.set_subtitle("Désactivées dans la configuration de doubler (clé « paroles » à non) : "
                                        "à changer dans les Réglages")
            return
        self.r_paroles.set_sensitive(True)
        self.r_paroles.set_subtitle(TEXTE_PAROLES)
        if not self._paroles_touchees:
            self._paroles_auto = True
            self.r_paroles.set_active(True)
            self._paroles_auto = False

    def autotune_par_defaut_change(self, n):
        """Depuis les Réglages : le nouveau défaut de l'auto-tune remplace le choix fait à la main."""
        self._autotune_touche = False
        self._autotune_defaut(n, getattr(self, "_x42", (False, None)))

    def _autotune_defaut(self, n, x42):
        """Clé « autotune » de doubler (0 : éteint) ; x42 = (connu, dossier) : sans le greffon, l'interrupteur est
        grisé et éteint, avec la raison. Un choix fait à la main dans la page est gardé."""
        self._autotune_conf, self._x42 = n, x42
        connu, dossier = x42
        if connu and dossier is None:
            self._autotune_auto = True
            self.r_autotune.set_enable_expansion(False)
            self._autotune_auto = False
            self.r_autotune.set_sensitive(False)
            self.r_autotune.set_subtitle(TEXTE_X42_ABSENT)
            return
        self.r_autotune.set_sensitive(True)
        self.r_autotune.set_subtitle(TEXTE_AUTOTUNE)
        if not self._autotune_touche:
            self._autotune_auto = True
            self.r_autotune.set_enable_expansion(n > 0)
            self.at_echelle.set_value(n or moteur.AUTOTUNE_DEFAUT)
            self._autotune_auto = False

    def _verifier_kaggle(self):
        def lire():
            return systeme.jeton_kaggle_present(), moteur.config_effective()

        def fini(r):
            jeton, conf = r
            self._conf = conf if isinstance(conf, dict) else {}
            self._maj()
            self._paroles_defaut(moteur.paroles_par_defaut(conf))
            self._autotune_defaut(moteur.autotune_par_defaut(conf), moteur.x42_autotune(conf))
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
            if voix and (sorties / moteur.nom_dossier(p.resolve(), v["extrait"]) / "travail" / moteur.id_voix(voix) /
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

    def calcul_choisi(self):
        """Le moyen de calcul choisi dans la page (kaggle, modal, runpod, auto), ou chaîne vide : celui de la configuration
        de doubler (la ligne est cachée si doubler ne connaît pas --calcul)."""
        if not hasattr(self, "r_calcul") or not self.r_calcul.get_visible():
            return ""
        return ["", *[c for c, _l in secours.CALCULS]][self.r_calcul.get_selected()]

    def reglages(self):
        mode = MODES[self.r_mode.get_selected()][0]
        r = {"voix": self._voix_choisie(), "mode": mode, "octave": OCTAVES[self.r_octave.get_selected()][0],
             "pas": int(self.r_pas.get_value()),
             "voix_db": round(self.r_voix_db.get_value(), 1) if self.r_niveau.get_enable_expansion() else None,
             "reverb_db": round(self.r_reverb.get_value(), 1)}
        if self.comparer() and len(self.voix_a_comparer()) >= 2:
            r["comparer"] = self.voix_a_comparer()   # doubler --comparer A,B,C (remplace --voix)
        if self.r_mix_v1.get_visible() and self.r_mix_v1.get_active():
            r["mix_v1"] = True
        if self.r_transposer.get_visible():
            r["transposer"] = "auto" if self.r_transposer.get_active() else "non"
        moteur_, sans = self._options_chaine()
        if moteur_:
            r["moteur"] = moteur_
        if sans:
            r["sans_nettoyage"] = True
        if self.r_paroles.get_visible() and not self.r_paroles.get_active():
            r["sans_paroles"] = True
        calcul = self.calcul_choisi()
        if calcul:
            r["calcul"] = calcul
        if self.r_autotune.get_visible():
            if self.r_autotune.get_sensitive() and self.r_autotune.get_enable_expansion():
                r["autotune"] = int(round(self.at_echelle.get_value()))
                gamme = moteur.GAMMES[self.r_at_gamme.get_selected()][0] if self.r_at_gamme.get_visible() else "auto"
                if gamme != "auto":
                    r["autotune_gamme"] = gamme
            elif self._autotune_conf > 0:
                r["sans_autotune"] = True   # éteint ici alors que la configuration de doubler l'allume
        return r

    def _maj(self):
        groupes = moteur.grouper_par_extrait(self.videos, self.fenetre.caps)
        n = len(self.videos)
        reglages = self.reglages() if hasattr(self, "r_pas") else {}
        # une estimation par session (groupe de même extrait), une seule fois : la durée totale et le GPU du quota
        estimations = [self._estimer([duree_audio(v) for v in vids], reglages) for _e, vids in groupes] if n else []
        if not n:
            self.resume.set_label("Aucune vidéo choisie")
        else:
            est = sum(sum(e.values()) for e in estimations)
            calcul = reglages.get("calcul") or moteur.calcul_par_defaut(self._conf)
            nom = {"modal": "Modal", "runpod": "Runpod"}.get(calcul, "Kaggle")
            sessions = (f"une seule session {nom}" if len(groupes) == 1
                        else f"{len(groupes)} sessions {nom} (une par extrait)")
            inconnue = any(v["duree_s"] is None and not (v.get("extrait") and v["extrait"][1]) for v in self.videos)
            texte = 'durée en cours de lecture' if inconnue else formats.duree_courte(est)
            dc = None if inconnue else moteur.duree_cout_calcul([e.get("kaggle") or 0.0 for e in estimations], calcul,
                                                              self._conf)
            if dc:   # Modal et Runpod : durée sur la carte réglée, et coût en dollars (estimation)
                texte = (f"{formats.duree_courte(dc[0])} sur {nom}"
                         + (f", environ {dc[1]:.2f} $".replace(".", ",") if dc[1] is not None else ""))
            self.resume.set_markup(f"<b>{formats.pluriel(n, 'vidéo')}</b> · {sessions} · {texte}")
        if n and len(groupes) > 1:
            self.note.set_label("Les vidéos ont des extraits différents : doubler applique le même extrait à toutes les "
                                "vidéos d’un appel, donc un appel (et une session Kaggle) par extrait, l’un après "
                                "l’autre. Pour une seule session, donnez le même extrait à toutes.")
        else:
            self.note.set_label("Le travail part dans la file (onglet Travaux) : il continue si vous fermez la fenêtre.")
        self.b_lancer.set_sensitive(bool(n) and bool(self._voix_choisie()))
        if self.comparer():
            self.r_comparees.set_subtitle(self._texte_comparees(n))
        sur_kaggle = (reglages.get("calcul") or moteur.calcul_par_defaut(self._conf)) not in ("modal", "runpod")
        self._maj_quota([e.get("kaggle") or 0.0 for e in estimations] if sur_kaggle else [0.0] * len(estimations))
        if self.par_chanteur():
            a, b = self._choix_menu(self.r_voix_a), self._choix_menu(self.r_voix_b)
            self.r_duo.set_subtitle(TEXTE_PAR_CHANTEUR if not (a and b and a == b) else
                                    "La même voix pour les deux chanteurs : un doublage à une voix, comme « Une voix "
                                    "pour tous »")

    def _texte_comparees(self, n_videos):
        """Sous-titre de « Voix à comparer » : ce qui manque, puis le coût, et le conseil de l'extrait court."""
        c = self.voix_a_comparer()
        if len(c) < 2:
            return "Cochez au moins deux voix"
        if len(c) > COMPARER_MAXI:
            return f"{COMPARER_MAXI} voix au plus par session"
        texte = (f"{len(c)} voix, une session : la conversion est refaite pour chacune, le reste (envoi, séparation, "
                 "nettoyage, paroles) une seule fois")
        if n_videos and any(not v.get("extrait") for v in self.videos):
            texte += ". Conseil : un extrait de 20 à 40 s suffit pour choisir une voix (« Extrait… » sur la vidéo)"
        return texte

    # --- quota GPU -------------------------------------------------------------------------------------------------

    def _verdicts(self, besoins):
        """[quota.Verdict] des sessions à lancer (secondes de GPU de chacune), après ce que la file prendra encore ;
        None tant que le quota n'est pas lu."""
        e = self.fenetre.quota
        if e is None:
            return None
        a_venir = quota.gpu_a_venir(self.fenetre.file.travaux())
        return quota.juger_demandes(e, [{"estimations": {"kaggle": b}} for b in besoins], a_venir)

    def _maj_quota(self, besoins):
        """Ligne du quota à côté de « Lancer » (besoins : secondes de GPU de chaque session à lancer), et la bannière
        d'alerte (moins de quota_alerte_h heures restantes)."""
        e = self.fenetre.quota
        _q, _j, _h, seuil, politique = quota.reglages(self.fenetre.prefs)
        if e is None:
            self.l_quota.set_visible(False)
            self.banniere_quota.set_revealed(False)
            return
        remise = quota.jour_heure(e.remise)
        if e.alerte(seuil):
            self.banniere_quota.set_title(f"Quota GPU Kaggle : plus que {quota.heures(e.reste_s)} cette semaine, "
                                          f"remise à zéro {remise}")
        self.banniere_quota.set_revealed(e.alerte(seuil))
        inconnue = any(v["duree_s"] is None and not (v.get("extrait") and v["extrait"][1]) for v in self.videos)
        if not self.videos or inconnue:
            self.l_quota.set_label(f"Quota GPU Kaggle : {quota.heures(e.reste_s)} restantes sur "
                                   f"{quota.heures(e.total_s)} cette semaine, remise à zéro {remise}.")
            self.l_quota.set_css_classes(["quota-doubler", "dim-label", "caption"])
            self.l_quota.set_visible(True)
            return
        verdicts = self._verdicts(besoins)
        total = quota.Verdict(sum(v.besoin_s for v in verdicts), verdicts[0].reste_s, verdicts[0].a_venir_s,
                              all(v.tient for v in verdicts), e.remise)
        texte = quota.phrase_estimation(total)
        if not total.tient:
            texte += {"demander": f" Au lancement : attendre la remise à zéro ({remise}) ou lancer quand même.",
                      "attendre": f" Il attendra la remise à zéro, {remise} (réglage).",
                      "lancer": " Il sera lancé quand même (réglage)."}[politique]
        self.l_quota.set_label(texte)
        self.l_quota.set_css_classes(["quota-doubler"] + ([] if total.tient else ["warning"]))
        self.l_quota.set_visible(True)

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
        if (reglages.get("calcul") or moteur.calcul_par_defaut(self._conf)) in ("modal", "runpod"):
            # ni le quota de Kaggle ni sa file ne sont en jeu : pas de jugement, et rien à ajouter au GPU promis à la file
            for d in demandes:
                d["estimations"] = {**d["estimations"], "kaggle_avant_calcul": d["estimations"].get("kaggle"),
                                    "kaggle": 0.0}
            self._mettre_dans_la_file(demandes)
            return
        verdicts = self._verdicts([quota.besoin_s(d) for d in demandes])
        if verdicts is None or all(v.tient for v in verdicts):
            self._mettre_dans_la_file([d if v is None else quota.marquer(d, "tient", v)
                                       for d, v in zip(demandes, verdicts or [None] * len(demandes))])
            return
        politique = quota.reglages(self.fenetre.prefs)[4]
        if politique == "demander":
            self._demander_quota(demandes, verdicts)
        else:
            self._decider_quota(politique, demandes, verdicts)

    def _decider_quota(self, decision, demandes, verdicts, moyen=None):
        """« attendre » : les sessions qui ne tiennent pas attendent la remise à zéro dans la file ; « lancer » : tout
        part quand même ; « secours » : celles qui ne tiennent pas partent par ce moyen (voixla/secours.py)."""
        sortie, attente = [], 0
        for d, v in zip(demandes, verdicts):
            if v.tient:
                sortie.append(quota.marquer(d, "tient", v))
            elif decision == "attendre":
                sortie.append(quota.mettre_en_attente(d, v.remise + quota.REPRISE_APRES_S, v))
                attente += 1
            elif decision == "secours" and moyen is not None:
                sortie.append(secours.preparer_demande(moyen, quota.marquer(d, "secours", v)))
            else:
                sortie.append(quota.marquer(d, "lancer", v))
        message = None
        if attente:
            reprise = quota.jour_heure(verdicts[0].remise + quota.REPRISE_APRES_S)
            message = (f"{formats.pluriel(attente, 'doublage')} en attente du quota GPU : départ tout seul {reprise}, "
                       "même fenêtre fermée")
        self._mettre_dans_la_file(sortie, message)

    def _demander_quota(self, demandes, verdicts):
        """Le doublage ne tient plus dans le reste du quota : attendre la remise à zéro (proposé d'abord), lancer quand
        même, ou un moyen de secours s'il y en a un de branché (voixla/secours.py)."""
        e = self.fenetre.quota
        besoin = sum(v.besoin_s for v in verdicts)
        dispo = max(0.0, verdicts[0].reste_s - verdicts[0].a_venir_s)
        remise = quota.jour_heure(e.remise + quota.REPRISE_APRES_S)
        corps = (f"Ce doublage prendra environ {quota.heures(besoin)} de GPU ; il n’en reste que {quota.heures(dispo)} "
                 "cette semaine")
        corps += (f", dont {quota.heures(verdicts[0].a_venir_s)} déjà promises à la file." if verdicts[0].a_venir_s >= 60
                  else ".")
        if len(verdicts) > 1:
            n = sum(1 for v in verdicts if not v.tient)
            corps += f" {n} session{'s' if n > 1 else ''} sur {len(verdicts)} ne {'tient' if n < 2 else 'tiennent'} pas."
        corps += (f"\n\nAttendre : le doublage reste dans la file et part tout seul à la remise à zéro du quota, "
                  f"{remise}, même fenêtre fermée.\n\nLancer quand même : Kaggle ne vérifie le quota qu’au démarrage "
                  "d’une session, et une session commencée avec du quota peut le dépasser, une fois par semaine.")
        if dispo < 60:
            corps += " Le quota est épuisé : Kaggle refusera sans doute la session."
        d = Adw.AlertDialog(heading="Le quota GPU ne suffit plus", body=corps)
        d.add_response("annuler", "Annuler")
        d.add_response("lancer", "Lancer quand même")
        for m in secours.proposer_pour(self.fenetre.prefs):
            d.add_response(f"secours:{m.cle}", f"Doubler sur {m.nom}")
        d.add_response("attendre", "Attendre la remise à zéro")
        d.set_response_appearance("attendre", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("attendre")
        d.set_close_response("annuler")

        def reponse(_d, r):
            if r in ("attendre", "lancer"):
                self._decider_quota(r, demandes, verdicts)
            elif r.startswith("secours:"):
                self._decider_quota("secours", demandes, verdicts, secours.PAR_CLE.get(r.partition(":")[2]))
        d.connect("response", reponse)
        d.present(self.fenetre)
        self._dialogue_quota = d

    def _mettre_dans_la_file(self, demandes, message=None):
        self.fenetre.ajouter_travaux(demandes)
        n = len(self.videos)
        self.videos = []
        self._rendre()
        self.fenetre.toast(message or f"{formats.pluriel(n, 'vidéo')} dans la file", "Voir",
                           lambda: self.fenetre.aller("travaux"), duree=8 if message else 4)
        self.fenetre.aller("travaux")

    # --- essais ------------------------------------------------------------------------------------------------------

    def _defiler_en_bas(self):
        """Fait défiler la page jusqu'en bas (le Gtk.ScrolledWindow de l'Adw.PreferencesPage)."""
        a_voir = [self.vue.get_content()]
        while a_voir:
            w = a_voir.pop()
            if isinstance(w, Gtk.ScrolledWindow):
                adj = w.get_vadjustment()
                adj.set_value(adj.get_upper() - adj.get_page_size())
                return
            enfant = w.get_first_child()
            while enfant is not None:
                a_voir.append(enfant)
                enfant = enfant.get_next_sibling()

    def scenario(self, action):
        if action.endswith("+bas"):
            # le bas de la page (« Lancer » et la ligne du quota), une fois la vidéo et le quota lus
            action = action[:-4]
            GLib.timeout_add(3000, lambda: (self._defiler_en_bas(), False)[1])
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
        elif action in ("par-chanteur", "par-chanteur-ordre"):
            self.scenario("exemple")
            self.r_choix_voix.set_selected(1)
            self.r_duo.set_selected(1 if action.endswith("ordre") else 0)
            ids = getattr(self, "_ids_voix", [])
            if len(ids) >= 2:
                self.r_voix_a.set_selected(0)
                self.r_voix_b.set_selected(1)
            self.r_autotune.set_enable_expansion(True)
        elif action == "exemple-extrait":
            self.scenario("exemple")
            GLib.timeout_add(600, lambda: (self.scenario("extrait"), False)[1])
        elif action == "exemple-entier":
            self.ajouter(sorted(chemins.dossier_videos(self.fenetre.donnees).glob("*.mp4"))[:1])
        elif action == "quota-dialogue":
            # vidéo entière, puis « Lancer » une fois sa durée et le quota lus : le dialogue du quota s'ouvre si le
            # doublage ne tient pas (rien n'entre dans la file tant qu'on n'a pas répondu)
            self.scenario("exemple-entier")
            GLib.timeout_add(2500, lambda: (self.lancer(), False)[1])
