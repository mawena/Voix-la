"""Détail d'un résultat (la liste des doublages est la bibliothèque, gui/bibliotheque.py) : un lecteur (vidéo ou MP3)
où l'on choisit ce qu'on écoute à la même position (doublage, original, beat seul, ma voix seule, voix d'origine
seule ; touche B : doublage ⇄ original), les paroles synchronisées, la frise d'octave, les durées par étape, un remix
rapide, l'enregistrement du beat ou de la voix seule, ouvrir le dossier, supprimer vers la corbeille.

La page qui crée un détail (page, ici la bibliothèque) fournit : fenetre, remix_actif, proteger_caches(),
ouvrir_detail(res), revenir_au_detail(detail) et mettre_a_la_corbeille(resultats, apres)."""

import time
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .. import chemins, formats, moteur, pistes, rapports
from .. import lecture as regles
from . import commun, pistes_gst
from . import visualisations as vis
from .analyses import analyses
from .commandes import Commandes
from .frise import Frise, FriseVoix, legende as legende_frise, legende_voix
from .lecteur import Lecteur
from .lecture import lectures
from .vue_paroles import TAILLES, Karaoke, PanneauParoles, VueParoles

PRODUITS_VIS = {"spectre": "spectre", "onde": "onde", "vumetre": "bandes"}
VOIX_SEULES = ("ma_voix", "ma_voix_at")     # voix seules combinées en cache (normale et auto-tune)
POSITION_S = 30              # pendant la lecture, la position est retenue (reprise) toutes les 30 s au plus
HAUTEUR_ECRAN = 320          # hauteur de l'image dans le détail (hors mode cinéma)
# Les panneaux que le mode cinéma (touche V) masque d'un coup : préférence → texte du menu « œil »
PANNEAUX = (("frise_octave", "Frise d’octave"), ("frise_voix", "Voix de chaque passage"),
            ("vis_melodie", "Courbe de mélodie"), ("vis_onde", "Forme d’onde"), ("vis_spectre", "Spectrogramme"),
            ("vis_vumetre", "Vu-mètre"), ("paroles", "Paroles"))


def phrase(texte):
    """Majuscule en tête, sauf devant un chemin (« travail/… »)."""
    if not texte or texte.startswith("travail/"):
        return texte or ""
    return texte[:1].upper() + texte[1:]


def sous_titre(res, maintenant=None):
    morceaux = []
    if res.voix_nom or res.voix:
        morceaux.append(f"voix {res.voix_nom or res.voix}")
    if res.mode:
        morceaux.append(res.mode)
    if res.autotune:
        morceaux.append("auto-tune")
    if res.duree_s:
        morceaux.append(formats.chrono(res.duree_s))
    if res.extrait:
        d, u = res.extrait
        morceaux.append(f"extrait à {formats.chrono(d)}")
    morceaux.append(formats.date(res.date, maintenant))
    if res.groupe:
        morceaux.append(f"dans {res.groupe}/")
    return " · ".join(morceaux)


class PageDetail(Adw.NavigationPage):
    """Un résultat : lecteur A/B, frise, informations, remix, durées.

    La page est aussi le propriétaire de sa lecture pour le coordinateur (gui/lecture.py) : une lecture durable. On
    peut quitter la page pendant la lecture : la page qui joue (ou en pause) reste gardée, la barre « en cours de
    lecture » de la fenêtre y ramène, et on la retrouve telle quelle (position, piste, lecture ou pause). Une page
    quittée qui ne lit pas est oubliée : ses flux se ferment."""

    durable = True

    def __init__(self, page, res):
        super().__init__(title=res.titre_court, tag="detail")
        self.page, self.fenetre, self.res = page, page.fenetre, res
        self.forme = "video" if res.video else "mp3"
        self.remix_en_cours = False
        self.piste = "doublage"       # ce qu'on écoute (clé de voixla.pistes, ou « comparaison »)
        self.voulue = None            # piste demandée qui se prépare encore (ma voix seule)
        self._maj = False             # bascules mises à jour par le code (pas par un clic)
        self._videos = set()          # pistes sans image dont la copie vidéo se prépare
        self._avancement = {}         # piste → avancement de sa préparation (0 à 1), quand ffmpeg le donne
        self._echecs = {}             # piste → raison d'une préparation ratée (bouton « Réessayer »)
        self._demandees = set()       # (fichier, produits) dont l'analyse a été demandée
        self._oubliee = False         # page quittée et fermée (flux fermés, plus d'écoute des préférences)
        self.autotune = False         # bascule « Auto-tune » (touche T) : doublage et voix seule auto-tunés
        self._preparations = set()    # voix seules en préparation (ma_voix, ma_voix_at)
        self.vue_paroles = None       # paroles sous le lecteur (préférence « paroles »)
        self.panneau_paroles = None   # « Toutes les paroles », à droite (préférence « paroles_panneau »)
        self.paroles_image = None     # sous-titres sur l'image hors du plein écran (préférence « paroles_video »)
        self._karaokes = []           # paroles en surimpression (sur l'image, en plein écran) à tenir à jour
        self.paroles_lues = None      # voixla.paroles.Paroles, une fois lues
        self._message_paroles = "Recherche des paroles…"
        self._paroles_demandees = False
        self.cinema = False           # mode cinéma (touche V) : tous les panneaux masqués, l'image en grand
        self.plein = None             # la vue plein écran (gui/plein_ecran.py), quand elle est ouverte
        self._minuteur_position = None    # pendant la lecture : la position retenue toutes les POSITION_S
        self._volume_a_ecrire = None
        self._maj_panneau = False
        self._attente_panneau = None
        self.passages = rapports.passages_voix(res.rapport)
        self.pistes_dispo = self._choisir_pistes(self.forme)
        self.actions = self._creer_actions()
        self.insert_action_group("detail", self.actions)
        vue = Adw.ToolbarView()
        entete = Adw.HeaderBar()
        self.titre_fenetre = Adw.WindowTitle(title=res.titre_court, subtitle=sous_titre(res))
        entete.set_title_widget(self.titre_fenetre)
        b_dossier = commun.bouton(icone="folder-open-symbolic", infobulle="Ouvrir le dossier",
                                  clic=lambda: commun.ouvrir_dossier(self.fenetre, res.dossier))
        entete.pack_end(b_dossier)
        b_suppr = commun.bouton(icone="user-trash-symbolic", infobulle="Mettre à la corbeille…",
                                clic=self.supprimer)
        entete.pack_end(b_suppr)
        self.b_oeil = Gtk.MenuButton(icon_name="view-reveal-symbolic", menu_model=self._menu_oeil(),
                                     tooltip_text="Panneaux, visualisations et paroles")
        self.b_oeil.update_property([Gtk.AccessibleProperty.LABEL], ["Panneaux, visualisations et paroles"])
        entete.pack_end(self.b_oeil)
        vue.add_top_bar(entete)
        # reprise à la dernière position (préférence « positions ») : on le dit, avec de quoi repartir du début
        self.banniere = Adw.Banner(button_label="Reprendre au début")
        self.banniere.connect("button-clicked", lambda *_: self._depuis_le_debut())
        vue.add_top_bar(self.banniere)

        corps = commun.boite(espace=14, marges=16)
        self.corps = corps
        # Bascules : forme (vidéo / MP3) et piste écoutée, à la même position
        barre = Gtk.Box(spacing=12)
        forme = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
        self.t_video = Gtk.ToggleButton(label="Vidéo", active=self.forme == "video", sensitive=bool(res.video))
        self.t_mp3 = Gtk.ToggleButton(label="MP3", group=self.t_video, active=self.forme == "mp3",
                                      sensitive=bool(res.mp3))
        self.t_video.connect("toggled", lambda b: b.get_active() and self.changer_forme("video"))
        self.t_mp3.connect("toggled", lambda b: b.get_active() and self.changer_forme("mp3"))
        forme.append(self.t_video)
        forme.append(self.t_mp3)
        barre.append(forme)
        barre.append(Gtk.Box(hexpand=True))
        groupe = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
        groupe.update_property([Gtk.AccessibleProperty.LABEL], ["Ce qu’on écoute"])
        self.t_pistes, premier = {}, None
        for cle in pistes.CLES:
            # libellés qui se raccourcissent (…) dans une fenêtre étroite plutôt que d'élargir la page ; « Voix
            # converties seules » pour une voix par chanteur
            t = Gtk.ToggleButton(child=Gtk.Label(label=self.pistes_dispo[cle].libelle,
                                                 ellipsize=Pango.EllipsizeMode.END),
                                 group=premier, active=cle == "doublage")
            premier = premier or t
            t.connect("toggled", lambda b, c=cle: b.get_active() and self.choisir_piste(c))
            groupe.append(t)
            self.t_pistes[cle] = t
        barre.append(groupe)
        # version auto-tune (doubler --autotune N) : la même piste auto-tunée, à la même position (touche T)
        self.t_autotune = Gtk.ToggleButton(label="Auto-tune", valign=Gtk.Align.CENTER, visible=bool(res.autotune),
                                           tooltip_text="Avec ou sans auto-tune, à la même position (touche T)")
        self.t_autotune.connect("toggled", lambda b: self._maj or self.basculer_autotune(b.get_active()))
        barre.append(self.t_autotune)
        corps.append(barre)
        self.note_piste = commun.legende("")
        self.note_piste.set_hexpand(True)
        self.b_reessayer = commun.bouton("Réessayer", "view-refresh-symbolic", styles=("flat",),
                                         valign=Gtk.Align.CENTER, visible=False, clic=self.reessayer,
                                         infobulle="Relancer la préparation des pistes qui ont échoué")
        ligne_note = Gtk.Box(spacing=8)
        ligne_note.append(self.note_piste)
        ligne_note.append(self.b_reessayer)
        corps.append(ligne_note)

        self.lecteur = Lecteur(hauteur=HAUTEUR_ECRAN, sur_position=self._position, proprietaire=self,
                               commandes=False)
        self.lecteur.sur_echec_piste = self._sans_pistes_audio
        self.lecteur.sur_etat = self._lecteur_change
        volume = self.fenetre.prefs.get("volume")
        if isinstance(volume, (int, float)) and not isinstance(volume, bool):
            self.lecteur.volume = max(0.0, min(1.0, float(volume)))
        self.commandes = Commandes(self)
        self.lecteur.append(self.commandes)
        # l'image prend le focus à l'ouverture (sinon le bouton retour l'aurait, et Espace ramènerait à la liste) ;
        # double-clic : plein écran
        self.lecteur.ecran.set_focusable(True)
        self.lecteur.ecran.update_property([Gtk.AccessibleProperty.LABEL],
                                           [f"{res.titre_court} : Espace pour lire ou mettre en pause, F pour le plein "
                                            "écran"])
        clic = Gtk.GestureClick()
        clic.connect("pressed", lambda _g, n, _x, _y: n == 2 and self.basculer_plein_ecran())
        self.lecteur.superpose.add_controller(clic)
        corps.append(self.lecteur)
        # Visualisations du son (chacune s'allume et s'éteint par le menu « œil » ; préférences mémorisées)
        self.vis = {}                 # nom → widget
        self.pistes = {}              # fichier → données d'analyse
        self.boite_vis = commun.boite(espace=10)
        corps.append(self.boite_vis)
        self.boite_onde = commun.boite(espace=6)

        # Frise d'octave
        self.segs = rapports.segments_frise(res.rapport)
        self.g_octave = commun.boite(espace=6)
        tete = Gtk.Box(spacing=8)
        tete.append(Gtk.Label(label="Frise d’octave", xalign=0, css_classes=["heading"], hexpand=True))
        self.passage = Gtk.Label(xalign=1, css_classes=["caption", "chiffres"])
        tete.append(self.passage)
        self.g_octave.append(tete)
        if self.segs:
            self.frise = Frise(sur_clic=self._clic_frise)
            self.frise.definir(self.segs, rapports.duree_frise(res.rapport, self.segs))
            self.g_octave.append(self.frise)
            self.g_octave.append(legende_frise(self.segs))
            self.g_octave.append(commun.legende("Chaque passage montre la voix détectée et son décalage en demi-tons ; "
                                                "un clic va à ce moment."))
        else:
            self.frise = None
            self.g_octave.append(commun.legende("Pas de frise dans ce rapport."))
        corps.append(self.g_octave)
        # une voix par chanteur : quelle voix cible chante chaque passage (voix principale et chœurs compris)
        self.frise_voix = None
        self.g_voix = commun.boite(espace=6, visible=False)
        if self.passages:
            noms = [v.get("nom") for v in res.voix_cibles if v.get("nom")] or res.voix_membres
            affichages = {n: self.fenetre.nom_voix(n) for n in noms}
            self.g_voix.append(Gtk.Label(label="Voix de chaque passage", xalign=0, css_classes=["heading"]))
            self.frise_voix = FriseVoix(sur_clic=self._clic_frise)
            self.frise_voix.definir(self.passages, noms, rapports.duree_frise(res.rapport, self.segs), affichages)
            self.g_voix.append(self.frise_voix)
            self.g_voix.append(legende_voix(noms, affichages))
        corps.append(self.g_voix)
        corps.append(self.boite_onde)

        # Informations
        self.g_infos = self._groupe_infos()
        corps.append(self.g_infos)
        self.g_pistes = self._groupe_pistes()
        corps.append(self.g_pistes)
        self.g_remix = self._groupe_remix()
        corps.append(self.g_remix)
        self.g_durees = self._groupe_durees()
        corps.append(self.g_durees)
        self.g_comparaisons = self._groupe_comparaisons() if res.comparaisons else None
        if self.g_comparaisons is not None:
            corps.append(self.g_comparaisons)

        self.defilement = commun.defile(corps, 980)
        self.clamp = corps.get_parent()
        # « Toutes les paroles » : un panneau à droite (par-dessus le contenu, fenêtre étroite)
        self.partage = Adw.OverlaySplitView(sidebar_position=Gtk.PackType.END, content=self.defilement,
                                            show_sidebar=False, min_sidebar_width=260, max_sidebar_width=380,
                                            sidebar_width_fraction=0.32)
        self.partage.connect("notify::show-sidebar", lambda *_: self._panneau_ferme())
        vue.set_content(self.partage)
        # fenêtre étroite : le panneau passe par-dessus le contenu, puis les commandes secondaires disparaissent
        cadre = Adw.BreakpointBin(child=vue)
        cadre.set_size_request(300, 300)
        etroit = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        etroit.add_setter(self.partage, "collapsed", True)
        cadre.add_breakpoint(etroit)
        tres_etroit = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 540sp"))
        tres_etroit.add_setter(self.partage, "collapsed", True)
        for w in self.commandes.secondaires:
            tres_etroit.add_setter(w, "visible", False)
        for cote in ("margin-start", "margin-end"):
            tres_etroit.add_setter(corps, cote, 8)
        cadre.add_breakpoint(tres_etroit)
        self.set_child(cadre)
        self.add_controller(self.raccourcis())
        self.add_controller(self.fleches())
        # un clic sur une bascule ne lui laisse pas le focus : Espace reste lecture / pause (Tab y mène toujours)
        for b in (self.t_video, self.t_mp3, self.t_autotune, *self.t_pistes.values()):
            b.set_focus_on_click(False)
        # quitter la page n'arrête pas la lecture ; une page quittée qui ne lit pas est oubliée
        self.connect("hidden", lambda *_: self._cachee())
        self.connect("shown", lambda *_: (self.page.proteger_caches(), self._focus_lecteur()))
        self.changer_forme(self.forme)
        self.fenetre.ecouter("visualisations", self._maj_visualisations)
        self._maj_visualisations()
        # reprise : là où la lecture de ce doublage s'était arrêtée
        self.reprise = regles.position_reprise(self.fenetre.prefs.get("positions"), self._cle_reprise(),
                                               res.duree_s)
        if self.reprise:
            self.lecteur.aller_a(self.reprise)
            self._position(self.reprise, False)
            self.banniere.set_title(f"Reprise à {formats.chrono(self.reprise)}, là où la lecture s’était arrêtée")
            self.banniere.set_revealed(True)
        if res.video is not None and pistes.a_sonder(res):
            # rapport sans pistes_audio : les titres des pistes audio de la vidéo, lus dans un fil (ffprobe)
            commun.en_fil(lambda: [pistes.titres_des_flux(v) for v in (res.video, res.video_autotune) if v],
                          lambda _r: self._apres_sondage())

    # --- lecteur ---------------------------------------------------------------------------------------------------

    def _choisir_pistes(self, forme):
        """Les pistes de ce résultat ; en vidéo, celles que la vidéo doublée a déjà s'y lisent sur place, tant que le
        playbin des flux est accessible (gui/pistes_gst.py), sinon par des copies vidéo comme un ancien doublage."""
        return pistes.choisir_pistes(self.res, forme, self.source_choisie(), dans_la_video=pistes_gst.possible())

    def _apres_sondage(self):
        """Les titres des pistes de la vidéo sont lus : si elle a des pistes séparées, on les lit sur place."""
        if self._oubliee or self.forme != "video" or self.piste == "comparaison":
            return
        if any(p.dans_la_video for p in self._choisir_pistes("video").values()):
            self.changer_forme("video")

    def _sans_pistes_audio(self):
        """Le playbin du lecteur est introuvable : les pistes séparées passent par des copies vidéo (en cache)."""
        if not self._oubliee and self.piste != "comparaison" and \
                any(p.dans_la_video for p in self.pistes_dispo.values()):
            self.changer_forme(self.forme)

    def changer_forme(self, forme):
        """Vidéo ou MP3 : recharge les pistes, garde la piste écoutée (si elle existe encore) et la position."""
        pos = self.lecteur.position_ref() if self.lecteur.media() else 0.0
        self.forme = forme
        self.pistes_dispo = self._choisir_pistes(forme)
        # le beat et les voix seules : sur place dans la vidéo doublée si elle a leurs pistes audio, sinon leur copie
        # vidéo si elle est prête (en cache), sinon le son seul ; le lecteur compte en temps du son doublé (frise,
        # courbes, paroles), et lit une vidéo décalée du début du son en elle (pistes.source_lecteur) : on passe de la
        # vidéo au son seul, et retour, au même instant du son
        sources = pistes.sources_lecteur(self.pistes_dispo)
        sans_image = {c for c, p in self.pistes_dispo.items() if not p.avec_image}
        ephemeres = {c for c in pistes.SANS_IMAGE_TOUTES if c in self.pistes_dispo and self.pistes_dispo[c].copie}
        cle = pistes.piste_apres_changement(self.pistes_dispo, self.voulue or self.piste)
        if cle is not None and self.pistes_dispo[cle].a_lire is None:
            # ma voix seule pas encore prête : on écoute le doublage pendant qu'elle se prépare
            self.voulue = cle
            cle = self._effective("doublage") if "doublage" in sources else next(iter(sources), None)
        self.lecteur.definir(sources, cle, audio_seul=forme == "mp3", sans_image=sans_image, ephemeres=ephemeres,
                             notes={c: self._note_bandeau(c) for c in pistes.SANS_IMAGE_TOUTES
                                    if c in self.pistes_dispo},
                             pistes_audio=pistes.pistes_audio_lecteur(self.pistes_dispo))
        if pos:
            self.lecteur.aller_a(pos)
        self.piste = self.lecteur.cle or "doublage"
        self._maj_bascules()
        self._maj_groupe_pistes()
        if self.voulue:
            self._preparer_ma_voix(prioritaire=True, cle=self.voulue)
        self._preparer_video(self.piste, prioritaire=True)
        self._preparer_tout()
        self._appliquer_pistes()
        self.page.proteger_caches()
        if self.paroles_image is not None:
            self.paroles_image.set_visible(forme == "video" and bool(self.fenetre.prefs.get("paroles_video")))
        if self.plein is not None:
            self.plein.forme_changee()
        self.commandes.lecture_changee()

    def _preparer_tout(self):
        """Dès l'ouverture, avant le moindre clic : ce qui manque se prépare en tâche de fond, une chose à la fois et
        à faible priorité (beat seul, ma voix seule combinée puis copiée sur l'image, voix d'origine seule). Rien pour
        une piste que la vidéo doublée a déjà : elle s'y lit sur place."""
        if self._oubliee:
            return
        for cle in pistes.SANS_IMAGE_TOUTES:
            p = self.pistes_dispo.get(cle)
            if p is None or cle in self._echecs or p.dans_la_video:
                continue
            if cle in VOIX_SEULES and p.a_preparer:
                self._preparer_ma_voix(cle=cle)      # puis sa copie vidéo
            else:
                self._preparer_video(cle)

    def fichiers_caches(self):
        """Les fichiers du cache que lit ce résultat (copies vidéo, ma voix seule) : la purge ne doit pas y toucher."""
        if self._oubliee:
            return []
        cache = chemins.dossier_cache()
        fichiers = [p.copie for p in self.pistes_dispo.values() if p.copie]
        for c in VOIX_SEULES:
            m = self.pistes_dispo.get(c)
            if m is not None and m.fichier is not None and cache in Path(m.fichier).parents:
                fichiers.append(m.fichier)
        return fichiers

    def _note_bandeau(self, cle):
        """Bandeau d'une piste lue sans image, à côté d'une vidéo."""
        p = self.pistes_dispo.get(cle)
        if p is not None and (p.video_a_preparer or cle in self._videos):
            a = self._avancement.get(cle)
            return f"préparation de la vidéo… {round(100 * a)} % (son seul, image arrêtée)" if a else \
                "préparation de la vidéo… (son seul, image arrêtée)"
        return "son seul, image arrêtée"

    def _avance(self, cle, fraction):
        self._avancement[cle] = fraction
        self.lecteur.annoter(cle, self._note_bandeau(cle))
        if (self.voulue or self.piste) == cle:
            self._maj_note()

    def _maj_bascules(self):
        """État des bascules de pistes : grisées (avec la raison en infobulle) si la piste manque."""
        self._maj = True
        try:
            courante = pistes.cle_de_base(self.voulue or self.piste)
            for c, t in self.t_pistes.items():
                p = self.pistes_dispo.get(c)
                ok = bool(p and p.disponible)
                t.set_sensitive(ok)
                infobulle = (pistes.INFOBULLE_VOIX_CONVERTIES if c == "ma_voix" and self.res.plusieurs_voix
                             else pistes.INFOBULLES[c])
                t.set_tooltip_text(infobulle if ok else f"{p.libelle} indisponible : {p.explication}")
                if c == courante:
                    t.set_active(True)
                elif t.get_active():
                    t.set_active(False)
            self.t_autotune.set_active(self.autotune)
        finally:
            self._maj = False
        self._maj_note()
        self._maj_action_piste()

    def _maj_note(self):
        c = self.voulue or self.piste
        p = self.pistes_dispo.get(c)
        o = self.pistes_dispo.get("original")
        if not any(x.fichier for x in self.pistes_dispo.values()):
            texte = "Aucun fichier à lire : doublage inachevé."
        elif p is None:
            return
        elif self.voulue:
            a = self._avancement.get(f"{self.voulue}_son")
            texte = (f"Préparation de « {p.libelle} » ({p.explication}){f', {round(100 * a)} %' if a else ''}, en "
                     "tâche de fond. En attendant, le doublage continue.")
        elif pistes.cle_de_base(c) == "doublage":
            texte = ("Touche B : l’original à la même position." if o and o.disponible else
                     f"Original indisponible : {o.explication}." if o else "")
        elif pistes.cle_de_base(c) == "original":
            texte = f"Original : {p.explication}. Touche B : retour au doublage."
        else:
            texte = f"{p.libelle} : {p.explication}.{self._note_image(c, p)} Touche B : retour au doublage."
        if self._echecs:
            texte += " " + " ".join(f"{pistes.LIBELLES.get(k, k)} : préparation impossible ({m})."
                                    for k, m in self._echecs.items())
        if self.res.autotune:
            texte += (" Auto-tune : version auto-tune (touche T : sans)." if self.autotune
                      else " Touche T : la version auto-tune.")
        self.note_piste.set_label(texte)
        self.b_reessayer.set_visible(bool(self._echecs))

    def _note_image(self, cle, p):
        """Ce qu'on voit pendant le beat ou une voix seule : la vidéo qui avance, sa préparation, ou pourquoi on s'en
        passe."""
        if pistes.cle_de_base(cle) not in pistes.SANS_IMAGE:
            return ""
        if self.forme == "video" and p.dans_la_video:
            return " L’image continue : seule la piste audio change."
        if self.forme == "video" and p.video:
            return " Sur l’image de la vidéo doublée, qui avance avec le son."
        if self.forme == "video" and (p.video_a_preparer or cle in self._videos):
            a = self._avancement.get(cle)
            return (" Préparation de la vidéo (l’image de la vidéo doublée, copiée telle quelle, en tâche de fond"
                    + (f", {round(100 * a)} %" if a else "") + ") : en attendant, le son seul sur l’image arrêtée.")
        if p.sans_video:
            return f" Sans image : {p.sans_video}."
        return ""

    def choisir_piste(self, cle):
        """Clic sur une piste : on y passe à la même position (ma voix seule se prépare d'abord, au besoin)."""
        if self._maj:
            return
        cle = self._effective(cle)
        p = self.pistes_dispo.get(cle)
        if p is None or not p.disponible:
            self._maj_bascules()
            return
        if p.a_lire is None:
            self.voulue = cle
            self._maj_note()
            self._preparer_ma_voix(prioritaire=True, cle=cle)
            return
        self.voulue = None
        if cle not in self.lecteur.sources:   # on écoutait un extrait de comparaison : retour aux pistes
            self.piste = cle
            t = self.t_video if self.forme == "video" else self.t_mp3
            if t.get_active():
                self.changer_forme(self.forme)
            else:
                t.set_active(True)            # qui recharge les pistes (changer_forme)
            return
        self.lecteur.basculer(cle)
        self.piste = self.lecteur.cle or cle
        if p.copie and self.forme == "video":
            pistes.toucher(p.copie)           # copie lue : la purge du cache la garde
        if cle in VOIX_SEULES and p.dans_la_video and p.a_preparer:
            # lue sur place ; sa voix combinée, en tâche de fond, servira au son seul si l'on ferme la fenêtre
            self._preparer_ma_voix(cle=cle)
        self._preparer_video(cle, prioritaire=True)
        self._maj_note()
        self._appliquer_pistes()

    def _effective(self, cle):
        """La piste lue pour la bascule cle : sa version auto-tune si « Auto-tune » est allumé et qu'elle existe (en
        vidéo, le beat, la voix d'origine et l'original lus dans la vidéo auto-tune quand elle les a)."""
        base = pistes.cle_de_base(cle)
        at = pistes.cle_ecoutee(base, self.autotune, self.pistes_dispo)
        q = self.pistes_dispo.get(at)
        return at if q is not None and q.disponible else base

    def basculer_autotune(self, oui):
        """Bascule « Auto-tune » : la piste écoutée passe à sa version auto-tune (ou en revient), à la même position ;
        les pistes sans version auto-tune (original, beat, voix d'origine) ne changent pas."""
        if self._maj or not self.res.autotune:
            return
        self.autotune = bool(oui)
        base = pistes.cle_de_base(self.voulue or self.piste)
        if base in self.t_pistes:
            self.choisir_piste(base)
        self._maj_bascules()

    def bascule_autotune(self):
        """Touche T."""
        if self.res.autotune:
            self.t_autotune.set_active(not self.autotune)

    def _preparer_video(self, cle, prioritaire=False):
        """La copie vidéo d'une piste sans image (beat, voix seules), préparée une fois en cache, en tâche de fond et à
        faible priorité (jamais par file-attente, voir voixla/preparation.py). En attendant, le son seul sur l'image
        arrêtée, avec l'avancement ; quand elle est prête, le lecteur y passe à la même position. Si elle est
        impossible, le son seul reste, avec la raison et « Réessayer ». prioritaire : la piste qu'on vient de choisir
        passe devant les préparations à l'avance."""
        p = self.pistes_dispo.get(cle)
        if self.forme != "video" or p is None or not p.video_a_preparer or self._oubliee:
            return
        if cle in VOIX_SEULES and p.fichier is None:
            return                            # la voix seule se prépare d'abord (_preparer_ma_voix)
        video = self.res.video
        voix = self.res.dossier_voix if cle in VOIX_SEULES else None
        son = None if voix else p.fichier
        at = cle == "ma_voix_at"
        if cle in self._videos:
            if prioritaire:                   # déjà demandée à l'avance : elle passe devant
                analyses().video(video, lambda *_: None, son=son, voix=voix, duree_s=self.res.duree_s,
                                 prioritaire=True, autotune=at)
            return
        self._videos.add(cle)

        def pret(f, message):
            if message == "attente" or self._oubliee:
                return
            self._videos.discard(cle)
            self._avancement.pop(cle, None)
            q = self.pistes_dispo.get(cle)
            if self.forme != "video" or q is None or self.res.video != video or q.video is not None:
                return                        # forme changée entre-temps : la copie est en cache pour la prochaine
            if message == "a_refaire":        # remix pendant la préparation : la copie suit les nouveaux fichiers
                self._preparer_video(cle)
                return
            q.video_a_preparer = False
            if f is None:
                q.sans_video = message or "copie vidéo impossible"
                self._echecs[cle] = q.sans_video
                self.lecteur.annoter(cle, "son seul, image arrêtée")
                self.fenetre.toast(f"{q.libelle} sans image : {q.sans_video}", "Réessayer", self.reessayer, duree=10)
            else:
                q.video = Path(f)
                if self.piste != "comparaison":   # sinon, elle sera lue au retour aux pistes (changer_forme)
                    self.lecteur.ajouter(cle, pistes.source_lecteur(q), sans_image=False, ephemere=True)
                if self.lecteur.cle == cle:
                    self.piste = cle
                self.page.proteger_caches()
            self._maj_note()
        self.lecteur.annoter(cle, self._note_bandeau(cle))
        analyses().video(video, pret, son=son, voix=voix, duree_s=self.res.duree_s, prioritaire=prioritaire,
                         avancement=lambda f, _e: not self._oubliee and self._avance(cle, f), autotune=at)

    def _preparer_ma_voix(self, prioritaire=False, cle="ma_voix"):
        """Ma voix seule (cle « ma_voix ») ou celle de la version auto-tune (« ma_voix_at »), combinée en cache. Pour
        une voix seule lue sur place dans la vidéo doublée, c'est un plus (son seul fenêtre fermée) : un échec ne se
        signale pas."""
        at = cle == "ma_voix_at"
        if cle in self._preparations or not self.res.dossier_voix or self._oubliee:
            if cle in self._preparations and prioritaire:
                analyses().ma_voix(self.res.dossier_voix, lambda *_: None, self.res.duree_s, prioritaire=True,
                                   autotune=at)
            return
        self._preparations.add(cle)

        def pret(f, message):
            if message == "attente" or self._oubliee:
                return
            self._preparations.discard(cle)
            self._avancement.pop(f"{cle}_son", None)
            p = self.pistes_dispo.get(cle)
            if f is None and p is not None and p.dans_la_video:
                return
            if f is None or p is None:
                if self.voulue == cle:
                    self.voulue = None
                self._echecs[cle] = message or "indisponible"
                self.fenetre.toast(f"{p.libelle if p else 'Voix seule'} : {message or 'indisponible'}", "Réessayer",
                                   self.reessayer, duree=10)
                self._maj_bascules()
                return
            p.fichier, p.a_preparer = Path(f), False
            if p.video is None:
                self.lecteur.ajouter(cle, pistes.source_lecteur(p), sans_image=True)
            if self.voulue == cle:
                self.voulue = None
                self.lecteur.basculer(cle)
                self.piste = self.lecteur.cle or cle
                self._preparer_video(cle, prioritaire=True)   # puis sa copie vidéo, pour que l'image avance
            else:
                self._preparer_video(cle)                     # à l'avance, pour le premier clic
            self._maj_bascules()
            self._maj_groupe_pistes()
            self._appliquer_pistes()
            self.page.proteger_caches()

        def avance(f, _etape):
            if not self._oubliee:
                self._avancement[f"{cle}_son"] = f
                if self.voulue == cle:
                    self._maj_note()
        analyses().ma_voix(self.res.dossier_voix, pret, self.res.duree_s, prioritaire=prioritaire, avancement=avance,
                           autotune=at)

    def reessayer(self):
        """« Réessayer » : relance les préparations ratées (la piste écoutée d'abord)."""
        if self._oubliee or not self._echecs:
            return
        echecs, self._echecs = self._echecs, {}
        for cle in echecs:
            q = self.pistes_dispo.get(cle)
            if q is None:
                continue
            if cle in VOIX_SEULES and q.fichier is None:
                q.a_preparer = True
            elif self.forme == "video" and q.video is None and q.fichier is not None:
                q.video_a_preparer, q.sans_video = True, ""
        courante = self.voulue or self.piste
        if courante in echecs:
            if courante in VOIX_SEULES and self.pistes_dispo[courante].fichier is None:
                self.voulue = courante
                self._preparer_ma_voix(prioritaire=True, cle=courante)
            else:
                self._preparer_video(courante, prioritaire=True)
        self._preparer_tout()
        self._maj_bascules()

    def source_choisie(self):
        """Vidéo source choisie à la main (préférences de l'interface) quand celle de source.json est introuvable."""
        from pathlib import Path as P
        v = (self.fenetre.prefs.get("sources_choisies") or {}).get(str(self.res.dossier))
        return P(v) if v and P(v).is_file() else None

    def choisir_source(self):
        v = self.res.video_source
        dossier = chemins.dossier_videos(self.fenetre.donnees)
        d = Gtk.FileDialog(title="Vidéo source", modal=True,
                           filters=commun.liste_filtres(commun.filtre_fichiers(*commun.FILTRE_VIDEOS)),
                           initial_folder=Gio.File.new_for_path(str(dossier if dossier.is_dir() else
                                                                    chemins.dossier_telechargements())))
        if v:
            d.set_initial_name(v.name)

        def fini(dlg, res):
            try:
                f = commun.chemin_de(dlg.open_finish(res))
            except GLib.Error:
                return
            if not f:
                return
            choix = dict(self.fenetre.prefs.get("sources_choisies") or {})
            choix[str(self.res.dossier)] = str(f)
            self.fenetre.prefs = chemins.modifier_preference("sources_choisies", choix)
            self.changer_forme(self.forme)
            self.fenetre.toast(f"Vidéo source : {f.name} (pour la bascule A/B ; les données ne sont pas modifiées)")
        d.open(self.fenetre, None, fini)

    def bascule_ab(self):
        """Touche B : doublage ⇄ original (et de toute autre piste au doublage)."""
        cible = pistes.bascule_ab(pistes.cle_de_base(self.piste))
        p = self.pistes_dispo.get(cible)
        if p is not None and p.a_lire is not None:
            self.t_pistes[cible].set_active(True)

    def lecture_pause(self):
        if self.lecteur.en_lecture():
            self.mettre_en_pause()
        else:
            self.lire()

    # --- lecteur complet : actions, menu « œil », touches -------------------------------------------------------------

    def _creer_actions(self):
        """Les actions de la page (groupe « detail ») : vitesse et piste (menus des commandes), mode cinéma,
        auto-tune ; elles servent aussi à la vue plein écran, où le groupe est repris."""
        g = Gio.SimpleActionGroup()

        def etat(nom, valeur, rappel):
            a = Gio.SimpleAction.new_stateful(nom, valeur.get_type() if valeur.get_type_string() == "s" else None,
                                              valeur)
            a.connect("change-state", lambda act, v: rappel(v))
            g.add_action(a)
        etat("vitesse", GLib.Variant.new_string("1"), lambda v: self.regler_vitesse(float(v.get_string())))
        etat("piste", GLib.Variant.new_string("doublage"), lambda v: self.touche_piste(v.get_string()))
        etat("cinema", GLib.Variant.new_boolean(False), lambda v: self.basculer_cinema(v.get_boolean()))
        etat("autotune", GLib.Variant.new_boolean(False), lambda v: self.res.autotune and
             self.t_autotune.set_active(v.get_boolean()))
        for nom, rappel in (("boucle", lambda: self.basculer_boucle()), ("capture", lambda: self.capturer_image()),
                            ("plein-ecran", lambda: self.basculer_plein_ecran())):
            a = Gio.SimpleAction.new(nom, None)
            a.connect("activate", lambda *_a, r=rappel: r())
            g.add_action(a)
        return g

    def _menu_oeil(self):
        """Le menu « œil » : chaque panneau (frises, visualisations), l'échelle de la mélodie, les paroles (sous le
        lecteur, panneau « Toutes les paroles », sur l'image, taille du texte) et le mode cinéma."""
        menu = Gio.Menu()
        panneaux = Gio.Menu()
        panneaux.append("Frise d’octave", "win.frise-octave")
        if self.passages:
            panneaux.append("Voix de chaque passage", "win.frise-voix")
        for action, libelle in (("vis-melodie", "Courbe de mélodie"), ("vis-onde", "Forme d’onde"),
                                ("vis-spectre", "Spectrogramme"), ("vis-vumetre", "Vu-mètre")):
            panneaux.append(libelle, f"win.{action}")
        menu.append_section("Panneaux", panneaux)
        self.menu_melodie = Gio.Menu()
        self.menu_melodie.append("Mélodie en Hz (sinon en notes)", "win.melodie-hz")
        self._maj_menu_autotune()     # « Courbe de la version auto-tune », si le doublage en a une
        menu.append_section(None, self.menu_melodie)
        paroles = Gio.Menu()
        paroles.append("Paroles", "win.paroles")
        paroles.append("Toutes les paroles, à droite (P)", "win.paroles-panneau")
        paroles.append("Sous-titres sur la vidéo", "win.paroles-video")
        tailles = Gio.Menu()
        for nom, libelle, _f in TAILLES:
            item = Gio.MenuItem.new(libelle, None)
            item.set_action_and_target_value("win.paroles-taille", GLib.Variant.new_string(nom))
            tailles.append_item(item)
        paroles.append_submenu("Taille du texte", tailles)
        menu.append_section("Paroles", paroles)
        cinema = Gio.Menu()
        cinema.append("Mode cinéma : tout masquer (V)", "detail.cinema")
        menu.append_section(None, cinema)
        return menu

    def menu_pistes(self):
        """Le choix de la piste dans les commandes du plein écran (touches 1 à 5)."""
        menu = Gio.Menu()
        for i, cle in enumerate(regles.TOUCHES_PISTES):
            p = self.pistes_dispo.get(cle)
            item = Gio.MenuItem.new(f"{p.libelle if p else pistes.LIBELLES[cle]} ({i + 1})", None)
            item.set_action_and_target_value("detail.piste", GLib.Variant.new_string(cle))
            menu.append_item(item)
        if self.res.autotune:
            autre = Gio.Menu()
            autre.append("Auto-tune (T)", "detail.autotune")
            menu.append_section(None, autre)
        return menu

    def raccourcis(self, plein_ecran=False):
        """Les touches du lecteur (portée MANAGED, comme B, T et Espace d'avant) : la page affichée, ou la vue plein
        écran (plein_ecran vrai) ; chacune ne répond que quand c'est elle qu'on voit."""
        c = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)

        def ajouter(touches, rappel):
            for t in touches.split():
                c.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(t),
                                                Gtk.CallbackAction.new(lambda *_: self._touche(rappel, plein_ecran))))
        ajouter("space", self.lecture_pause)
        ajouter("b", self.bascule_ab)
        ajouter("t", self.bascule_autotune)
        ajouter("comma", lambda: self.pas_image(-1))
        ajouter("period", lambda: self.pas_image(1))
        for i, cle in enumerate(regles.TOUCHES_PISTES):
            ajouter(f"{i + 1} KP_{i + 1}", lambda c=cle: self.touche_piste(c))
        ajouter("l", self.basculer_boucle)
        ajouter("bracketleft", lambda: self.changer_vitesse(-1))
        ajouter("bracketright", lambda: self.changer_vitesse(1))
        ajouter("m", lambda: self.couper_son(not self.lecteur.coupe))
        ajouter("f", self.basculer_plein_ecran)
        ajouter("s", self.capturer_image)
        if plein_ecran:
            ajouter("Escape", self.quitter_plein_ecran)
        else:
            ajouter("v", lambda: self.basculer_cinema(not self.cinema))
            ajouter("p", lambda: self.fenetre.lookup_action("paroles-panneau").activate(None))
        return c

    def fleches(self, plein_ecran=False):
        """Espace, flèches gauche et droite, et Origine : prises avant la fenêtre, qui ferait d'Espace « activer le
        focus » (et l'avalerait sur l'image) et des flèches un déplacement du focus d'un bouton à l'autre. Elles
        restent à ce qui s'en sert déjà : Espace à un bouton, une case ou une ligne qui a le focus (clavier), les
        flèches et Origine à une barre (position, volume), un texte ou une liste ; et tout reste à un menu ouvert."""
        c = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        actions = {Gdk.KEY_Left: lambda: self.sauter(-regles.SAUT_S), Gdk.KEY_Right: lambda: self.sauter(regles.SAUT_S),
                   Gdk.KEY_Home: lambda: self.aller_a(0.0), Gdk.KEY_space: self.lecture_pause}
        a_eux = {Gdk.KEY_space: (Gtk.Button, Gtk.CheckButton, Gtk.Switch, Gtk.Editable, Gtk.ListBox, Gtk.ListView,
                                 Gtk.DropDown, Gtk.Popover)}
        autres = (Gtk.Range, Gtk.Editable, Gtk.ListBox, Gtk.ListView, Gtk.Popover)

        def presse(_c, touche, _code, etat):
            rappel = actions.get(touche)
            if rappel is None or etat & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK |
                                         Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.SUPER_MASK):
                return False
            focus = self.fenetre.get_focus()
            if focus is not None and focus.get_native() is not self.fenetre:
                return False
            w = focus
            while w is not None:
                if isinstance(w, a_eux.get(touche, autres)):
                    return False
                w = w.get_parent()
            return self._touche(rappel, plein_ecran)
        c.connect("key-pressed", presse)
        return c

    def _touche(self, rappel, plein_ecran):
        if self._oubliee or (self.plein is not None) != plein_ecran:
            return False
        if not plein_ecran and not self.est_visible():
            return False
        rappel()
        if self.plein is not None:
            self.plein.montrer_commandes()
        return True

    def touche_piste(self, cle):
        """Touches 1 à 5 (et le choix de piste du plein écran) : la bascule de cette piste, si elle est là."""
        t = self.t_pistes.get(cle)
        if t is None:
            return
        if not t.get_sensitive():
            p = self.pistes_dispo.get(cle)
            self.fenetre.toast(f"{p.libelle if p else pistes.LIBELLES.get(cle, cle)} indisponible"
                               + (f" : {p.explication}" if p and p.explication else ""))
            self._maj_action_piste()
            return
        t.set_active(True)

    def _maj_action_piste(self):
        a = self.actions.lookup_action("piste")
        base = pistes.cle_de_base(self.voulue or self.piste)
        if a is not None and a.get_state().get_string() != base:
            a.set_state(GLib.Variant.new_string(base))
        a = self.actions.lookup_action("autotune")
        if a is not None and a.get_state().get_boolean() != self.autotune:
            a.set_state(GLib.Variant.new_boolean(self.autotune))

    # --- lecteur complet : déplacements, vitesse, son, boucle A-B ----------------------------------------------------

    def duree_lecture(self):
        """La durée montrée par les commandes, en temps de référence."""
        if self.piste == "comparaison":
            return self.lecteur.duree()
        return self.res.duree_s or self.lecteur.duree_ref()

    def _apres_deplacement(self, t):
        """Après un déplacement (touche, barre, MPRIS) : frises, paroles et commandes au nouvel instant."""
        if t is None:
            return
        lit = self.lecteur.en_lecture()
        self._position(t, lit)
        if self.plein is not None:
            self.plein.placer(t, lit)
        self.commandes.lecture_changee()
        lectures().prevoir()

    def sauter(self, delta_s):
        self._apres_deplacement(self.lecteur.sauter(delta_s))

    def pas_image(self, sens):
        self._apres_deplacement(self.lecteur.pas_image(sens))

    def vitesse_possible(self):
        return self.lecteur.vitesse_possible()

    def changer_vitesse(self, sens):
        self.regler_vitesse(regles.vitesse_voisine(self.lecteur.vitesse, sens))

    def regler_vitesse(self, v):
        """La vitesse de lecture (0,5× à 2×), la hauteur gardée ; pour toutes les pistes de ce résultat."""
        if not self.lecteur.regler_vitesse(v):
            self.fenetre.toast("Vitesse indisponible : ce lecteur ne garderait pas la hauteur du son")
        a = self.actions.lookup_action("vitesse")
        texte = f"{self.lecteur.vitesse:g}"
        if a.get_state().get_string() != texte:
            a.set_state(GLib.Variant.new_string(texte))
        self._annoncer(f"Vitesse {regles.libelle_vitesse(self.lecteur.vitesse)}")
        self._lecteur_change()

    def basculer_boucle(self):
        """Touche L : début de la boucle A-B ici, puis sa fin, puis plus de boucle. Elle vaut pour toutes les pistes :
        on compare le doublage et l'original au même passage."""
        b = regles.boucle_suivante(self.lecteur.boucle, self.lecteur.position_ref())
        self.lecteur.definir_boucle(b)
        self._annoncer("Boucle effacée" if not b else f"Début de la boucle à {formats.chrono(b[0])}"
                       if not regles.boucle_complete(b) else f"Boucle {regles.libelle_boucle(b)}")
        self._lecteur_change()

    def couper_son(self, oui):
        self.lecteur.couper_son(oui)
        self._lecteur_change()

    def regler_volume(self, v):
        """Volume (commandes, MPRIS) : tous les flux ; gardé dans les préférences une seconde après le dernier
        changement."""
        self.lecteur.regler_volume(v)
        if self._volume_a_ecrire is None:
            self._volume_a_ecrire = GLib.timeout_add(1000, self._ecrire_volume)
        self.commandes.lecture_changee()
        if self.plein is not None:
            self.plein.commandes.lecture_changee()
        lectures().prevoir()

    def _ecrire_volume(self):
        self._volume_a_ecrire = None
        if abs((self.fenetre.prefs.get("volume") or 0.0) - self.lecteur.volume) > 1e-3:
            self.fenetre.prefs = chemins.modifier_preference("volume", round(self.lecteur.volume, 3))
        return False

    def _annoncer(self, texte):
        """Un changement sans autre trace visible (vitesse, boucle) dit aux lecteurs d'écran."""
        try:
            self.commandes.announce(texte, Gtk.AccessibleAnnouncementPriority.MEDIUM)
        except (AttributeError, TypeError):
            pass

    def _lecteur_change(self):
        """Lecture, pause, fin, vitesse, boucle (rappel sur_etat du lecteur) : commandes, plein écran, paroles,
        position retenue pour la reprise, barre « en cours de lecture » et MPRIS."""
        if self._oubliee:
            return
        lit = self.lecteur.en_lecture()
        self.commandes.lecture_changee()
        if self.plein is not None:
            self.plein.lecture_changee()
        t = self.lecteur.position_ref()
        for w in [self.panneau_paroles, *self._karaokes]:
            if w is not None and w.get_mapped():
                w.placer(t, lit)
        if lit:
            if self._minuteur_position is None:
                self._minuteur_position = GLib.timeout_add_seconds(POSITION_S, self._garder_en_lisant)
            if self.banniere.get_revealed() and t > (self.reprise or 0.0) + 8:
                self.banniere.set_revealed(False)
        elif self.lecteur.media() is not None:
            self.garder_position()
        lectures().prevoir()

    def _garder_en_lisant(self):
        if self._oubliee or not self.lecteur.en_lecture():
            self._minuteur_position = None
            return False
        self.garder_position()
        if self.banniere.get_revealed():
            self.banniere.set_revealed(False)
        return True

    # --- reprise à la dernière position ------------------------------------------------------------------------------

    def _cle_reprise(self):
        return regles.cle_reprise(self.res.dossier, self.res.voix)

    def garder_position(self):
        """Retient la position de ce doublage (préférence « positions ») : on y reprendra en le rouvrant, depuis le
        détail ou la bibliothèque. Au début ou à la fin, il est oublié (on reprendra au début)."""
        if self._oubliee or self.piste == "comparaison" or self.lecteur.media() is None:
            return
        m = self.lecteur.media()
        pos = self.duree_lecture() if m.get_ended() else self.lecteur.position_ref()
        avant = self.fenetre.prefs.get("positions") or {}
        apres = regles.memoriser_position(avant, self._cle_reprise(), pos, self.duree_lecture(), time.time())
        if apres.get(self._cle_reprise()) != avant.get(self._cle_reprise()):
            self.fenetre.prefs = chemins.modifier_preference("positions", apres)

    def _depuis_le_debut(self):
        self.banniere.set_revealed(False)
        self.aller_a(0.0)
        self.reprise = None

    # --- plein écran, mode cinéma, capture ---------------------------------------------------------------------------

    def basculer_plein_ecran(self):
        if self.plein is not None:
            self.quitter_plein_ecran()
        else:
            self.entrer_plein_ecran()

    def entrer_plein_ecran(self):
        if self._oubliee or self.plein is not None:
            return
        from .plein_ecran import PleinEcran
        self.plein = PleinEcran(self)
        self.fenetre.entrer_plein_ecran(self.plein)

    def quitter_plein_ecran(self):
        vue, self.plein = self.plein, None
        if vue is None:
            return
        self._karaokes = [k for k in self._karaokes if k not in (vue.paroles, vue.paroles_son)]
        vue.fermer()
        if self.fenetre.plein_ecran is vue:
            self.fenetre.sortir_plein_ecran()
        if not self._oubliee:
            self._position(self.lecteur.position_ref(), self.lecteur.en_lecture())
            self._focus_lecteur()

    def _focus_lecteur(self):
        """Le focus sur l'image (ou, en MP3, sur le bouton de lecture) : les touches du lecteur y répondent."""
        cible = self.lecteur.ecran if self.forme == "video" else self.commandes.b_lecture
        GLib.idle_add(lambda: (not self._oubliee and cible.get_mapped() and cible.grab_focus(), False)[1])

    def paroles_en_surimpression(self):
        """En plein écran, les paroles sur l'image suivent l'interrupteur « Paroles »."""
        return bool(self.fenetre.prefs.get("paroles"))

    def basculer_cinema(self, oui):
        """Mode cinéma (touche V) : tous les panneaux masqués d'un coup (frises, visualisations, paroles,
        informations), l'image en grand ; encore V pour les retrouver tels qu'ils étaient."""
        self.cinema = bool(oui)
        a = self.actions.lookup_action("cinema")
        if a.get_state().get_boolean() != self.cinema:
            a.set_state(GLib.Variant.new_boolean(self.cinema))
        self._maj_visibilites()
        self._annoncer("Mode cinéma" if self.cinema else "Panneaux réaffichés")

    def _maj_visibilites(self):
        """Ce qui se voit, d'après les préférences (menu « œil ») et le mode cinéma."""
        p, c = self.fenetre.prefs, self.cinema
        self.g_octave.set_visible(not c and bool(p.get("frise_octave", True)))
        self.g_voix.set_visible(not c and bool(self.passages) and bool(p.get("frise_voix", True)))
        for w in (self.boite_vis, self.boite_onde, self.g_infos, self.g_pistes, self.g_remix, self.g_durees,
                  self.g_comparaisons, self.vue_paroles):
            if w is not None:
                w.set_visible(not c)
        voulu = not c and bool(p.get("paroles_panneau"))
        if voulu and self.partage.get_width() <= 0:
            # ouvert avant la première mise en page, le panneau recevrait une largeur absurde (libadwaita 1.5) : il
            # s'ouvre à la première image où la page a sa largeur
            if self._attente_panneau is None:
                self._attente_panneau = self.partage.add_tick_callback(self._panneau_affiche)
            voulu = False
        self._maj_panneau = True
        try:
            self.partage.set_show_sidebar(voulu)
        finally:
            self._maj_panneau = False
        if self.paroles_image is not None:
            self.paroles_image.set_visible(self.forme == "video" and bool(p.get("paroles_video")))
        # l'image en grand : elle prend la hauteur libre, et la largeur jusqu'à 1800 px
        self.lecteur.ecran.set_size_request(-1, 200 if c else HAUTEUR_ECRAN)
        for w in (self.lecteur, self.lecteur.superpose, self.lecteur.ecran):
            w.set_vexpand(c)
        self.clamp.set_maximum_size(1800 if c else 980)
        self.clamp.set_tightening_threshold(1600 if c else 784)

    def _panneau_affiche(self, *_):
        if self.partage.get_width() <= 0 and not self._oubliee:
            return GLib.SOURCE_CONTINUE
        self._attente_panneau = None
        if not self._oubliee:
            self._maj_visibilites()
        return GLib.SOURCE_REMOVE

    def _panneau_ferme(self):
        """Le panneau « Toutes les paroles » refermé par un clic à côté (fenêtre étroite) : la préférence suit."""
        if self._maj_panneau or self._oubliee or self.cinema:
            return
        voulu = self.partage.get_show_sidebar()
        if voulu != bool(self.fenetre.prefs.get("paroles_panneau")):
            self.fenetre.regler_visualisation("paroles_panneau", voulu)

    def image_a_capturer(self):
        return self.forme == "video" and self.lecteur.ecran.get_paintable() is not None

    def capturer_image(self):
        """Touche S : l'image en cours, à sa taille d'origine, en PNG dans Images/Voix-là (écrite dans un fil)."""
        if not self.image_a_capturer():
            self.fenetre.toast("Pas d’image à enregistrer (MP3, ou vidéo pas encore prête)")
            return
        try:
            from gi.repository import Graphene
            image = self.lecteur.ecran.get_paintable().get_current_image()
            l, h = image.get_intrinsic_width(), image.get_intrinsic_height()
            if l <= 0 or h <= 0:
                raise ValueError("image vide")
            instantane = Gtk.Snapshot()
            image.snapshot(instantane, l, h)
            noeud = instantane.to_node()
            if noeud is None:
                raise ValueError("image vide")
            texture = self.fenetre.get_native().get_renderer().render_texture(
                noeud, Graphene.Rect().init(0, 0, l, h))
            telechargeur = Gdk.TextureDownloader.new(texture)
            telechargeur.set_format(Gdk.MemoryFormat.R8G8B8A8)
            octets, pas = telechargeur.download_bytes()
        except Exception as e:  # noqa: BLE001
            self.fenetre.toast(f"Capture impossible : {e}")
            return
        q = self.pistes_dispo.get(self.piste)
        dossier = chemins.dossier_images(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES))
        sortie = dossier / chemins.nom_capture(self.res.titre_court, q.libelle if q else "",
                                               self.lecteur.position_ref())

        def ecrire():
            from gi.repository import GdkPixbuf
            dossier.mkdir(parents=True, exist_ok=True)
            pb = GdkPixbuf.Pixbuf.new_from_bytes(octets, GdkPixbuf.Colorspace.RGB, True, 8, l, h, pas)
            pb.savev(str(sortie), "png", [], [])
            return sortie

        def fini(f):
            self.fenetre.toast(f"Image enregistrée : {f.name}", "Montrer", lambda: commun.montrer_fichier(
                self.fenetre, f), duree=6)
        commun.en_fil(ecrire, fini, lambda e: self.fenetre.toast(f"Capture impossible : {e}"))

    # --- lecture durable (coordinateur, barre « en cours de lecture », MPRIS) ----------------------------------------

    def meme_resultat(self, res):
        return res is not None and self.res.dossier == res.dossier and self.res.voix == res.voix

    def dans_la_pile(self):
        pile = self.fenetre.nav.get_navigation_stack()
        return any(pile.get_item(i) is self for i in range(pile.get_n_items()))

    def est_visible(self):
        return self.fenetre.page_vue() is self

    def _cachee(self):
        if not self.dans_la_pile() and lectures().session is not self:
            self.oublier()

    def quitter_session(self):
        """Une autre lecture durable a pris la place : si la page n'est plus affichée, elle est oubliée."""
        if not self.dans_la_pile():
            self.oublier()

    def oublier(self):
        """Ferme les flux et cesse d'écouter les préférences : la page ne reviendra plus."""
        if self._oubliee:
            return
        self.garder_position()
        self.quitter_plein_ecran()
        self._oubliee = True
        self.fenetre.oublier("visualisations", self._maj_visualisations)
        for w in [*self.vis.values(), self.vue_paroles, self.panneau_paroles, *self._karaokes]:
            if getattr(w, "animateur", None):
                w.animateur.arreter()
        for ident in (self._minuteur_position, self._volume_a_ecrire):
            if ident is not None:
                GLib.source_remove(ident)
        if self._volume_a_ecrire is not None:
            self._volume_a_ecrire = None
            self._ecrire_volume()
        self._minuteur_position = None
        self.lecteur.definir_boucle(())
        self.lecteur.liberer()
        self.page.proteger_caches()

    def couper(self):
        self.lecteur.pause()

    def etat_lecture(self):
        m = self.lecteur.media()
        if m is None or self._oubliee:
            statut = regles.ARRET
        elif self.lecteur.en_lecture():
            statut = regles.LECTURE
        elif m.get_ended():
            statut = regles.ARRET
        else:
            statut = regles.PAUSE
        r = self.res
        if self.piste == "comparaison":
            source = self.lecteur.sources.get("comparaison")
            piste = f"comparaison {source[2]}".replace("_", " ") if source else "comparaison"
            duree = self.lecteur.duree()
        else:
            q = self.pistes_dispo.get(self.piste)
            piste = q.libelle if q is not None else pistes.LIBELLES.get(self.piste, self.piste)
            duree = r.duree_s or self._duree_ref()
        return regles.Etat(statut=statut, position_s=self.lecteur.position_ref(), duree_s=duree, titre=r.titre_court,
                           voix=r.voix_nom or (r.voix or ""), piste=piste, genre="resultat",
                           cle=("resultat", str(r.dossier), r.voix or ""), video=str(r.video) if r.video else None,
                           volume=self.lecteur.volume, vitesse=self.lecteur.vitesse,
                           vitesse_reglable=m is not None and self.lecteur.vitesse_possible() is not False,
                           boucle=tuple(self.lecteur.boucle) if regles.boucle_complete(self.lecteur.boucle) else ())

    def _duree_ref(self):
        d = self.lecteur.duree()
        if d is None or self.lecteur.cle not in self.lecteur.sources:
            return None
        return max(0.0, d - (self.lecteur.sources[self.lecteur.cle][1] or 0.0))

    def lire(self):
        m = self.lecteur.media()
        if m is None:
            return
        if m.get_ended():
            self.lecteur.aller_a(0.0, lire=True)
        else:
            m.play()

    def mettre_en_pause(self):
        self.lecteur.pause()

    def arreter(self):
        """Stop (barre, MPRIS) : pause au début ; la lecture quitte la barre et MPRIS, et la page est oubliée si elle
        n'est plus affichée."""
        self.lecteur.pause()
        if self.lecteur.media() is not None:
            self.lecteur.aller_a(0.0)
            self._position(0.0, False)
            self.garder_position()            # arrêté : on reprendra au début
        if self.dans_la_pile():
            lectures().terminer(self)
        else:
            self.oublier()

    def aller_a(self, s):
        self.lecteur.aller_a(s)
        self._apres_deplacement(s)

    def revenir(self):
        self.page.revenir_au_detail(self)

    def passer_en_son(self):
        """Fenêtre fermée : la piste en cours passe de la vidéo à son son seul (le MP3 du doublage, le son d'origine
        extrait, le fichier du beat ou d'une voix seule), à la même position, pour ne pas décoder d'image pour rien."""
        cle = self.lecteur.cle
        if self._oubliee or self.forme != "video" or cle not in self.pistes_dispo or cle in self.lecteur.sans_image:
            return
        try:
            # une piste lue sur place dans la vidéo doublée (ou sa version auto-tune : beat_at…) a le même son seul
            # que celle de la vidéo normale : le fichier du beat, de la voix d'origine, travail/original.wav…
            sons = pistes.choisir_pistes(self.res, "mp3", self.source_choisie())
            son = regles.source_son(cle if cle in sons else pistes.cle_de_base(cle), sons,
                                    self.lecteur.sources.get(cle))
        except (OSError, pistes.ErreurPistes):
            return
        if son is not None:
            self.lecteur.passer_en_son(son)

    def revenir_en_video(self):
        if not self._oubliee:
            self.lecteur.revenir_en_video()

    def _position(self, t, lecture):
        """t : la position du lecteur, en temps du son doublé, celui de la frise, de courbes.json et des analyses du
        son (même en vidéo, où le son peut commencer après l'image : voir pistes.source_lecteur)."""
        if self.frise:
            self.frise.placer(t)
        if self.frise_voix is not None:
            self.frise_voix.placer(t)
        for w in self.vis.values():
            w.placer(t, lecture)
        for w in (self.vue_paroles, self.panneau_paroles, self.paroles_image):
            if w is not None and w.get_visible():
                w.placer(t, lecture)
        s = rapports.segment_a(self.segs, t)
        texte = formats.chrono_precis(t)
        if s:
            texte += " · " + ("muet" if s["classe"] == "muet" else
                              f"voix {s['voix']}" + (f" · {formats.demi_tons(s['decalage'])} dt"
                                                     if s.get("decalage") is not None else ""))
        if self.frise_voix is not None:
            p = self.frise_voix.passage_a(t)
            if p:
                texte += " · " + " et ".join(self.fenetre.nom_voix(v) for v in p["voix"])
        self.passage.set_label(texte)

    def _clic_frise(self, t):
        self.lecteur.aller_a(t)
        self._position(t, False)

    # --- visualisations ------------------------------------------------------------------------------------------

    def _fournisseur(self):
        m = self.lecteur.media()
        return self.lecteur.position_ref(), bool(m and m.get_playing())

    def fichier_piste(self, cle):
        """Le son analysé pour une piste : le doublage (MP3 de préférence, sinon la première piste de la vidéo), le son
        d'origine extrait (même temps que le doublage), le beat, ma voix seule une fois prête (ou sa piste dans la
        vidéo doublée, pistes.source_analyse), la voix d'origine."""
        r = self.res
        if pistes.cle_de_base(cle) == "original":
            return r.original_wav
        if cle == "doublage_at":
            return r.mp3_autotune or r.video_autotune
        if cle not in self.pistes_dispo or cle == "doublage":
            return r.mp3 or r.video
        p = self.pistes_dispo.get(cle)
        if p is None:
            return None
        if p.dans_la_video and (cle in VOIX_SEULES or p.fichier is None):
            return pistes.source_analyse(p.video, p.piste_audio)    # ce qu'on entend : la piste de la vidéo
        return p.fichier or p.video      # le son de la copie vidéo si la voix seule a quitté le cache

    def _maj_paroles(self):
        """Paroles allumées ou éteintes (menu « œil ») : sous le lecteur, dans le panneau « Toutes les paroles », sur
        l'image. Lues une seule fois (paroles.json, sinon sous-titres de la vidéo doublée), pour tous ces affichages ;
        la position du lecteur est en temps du son doublé (voir voixla.paroles.ecart_lecture)."""
        p = self.fenetre.prefs
        langue, taille = p.get("paroles_langue") or "deux", p.get("paroles_taille") or "normale"
        debut_son = pistes.debut_son_video(self.res)
        if p.get("paroles") and self.vue_paroles is None:
            w = VueParoles(sur_clic=self._clic_frise, langue=langue, sur_langue=self._langue_paroles,
                           debut_son=debut_son, taille=taille)
            w.suivre(self._fournisseur)
            self.corps.insert_child_after(w, self.lecteur)
            self.vue_paroles = w
            self._nourrir(w)
        elif not p.get("paroles") and self.vue_paroles is not None:
            if self.vue_paroles.animateur:
                self.vue_paroles.animateur.arreter()
            self.corps.remove(self.vue_paroles)
            self.vue_paroles = None
        if p.get("paroles_panneau") and self.panneau_paroles is None:
            w = PanneauParoles(sur_clic=self._clic_frise, langue=langue, debut_son=debut_son)
            w.suivre(self._fournisseur)
            self.partage.set_sidebar(w)
            self.panneau_paroles = w
            self._nourrir(w)
        if p.get("paroles_video") and self.paroles_image is None:
            w = self.creer_karaoke("video", precedente=False)
            w.set_valign(Gtk.Align.END)
            for cote in ("start", "end"):
                getattr(w, f"set_margin_{cote}")(24)
            w.set_margin_bottom(18)
            self.lecteur.superpose.add_overlay(w)
            self.paroles_image = w
        for w in [self.vue_paroles, self.panneau_paroles, *self._karaokes]:
            if w is None:
                continue
            if getattr(w, "langue", langue) != langue:
                w.regler_langue(langue)
            if hasattr(w, "regler_taille") and w is not self.panneau_paroles:
                w.regler_taille(self._taille_karaoke(w, taille))

    def _taille_karaoke(self, w, taille):
        """La taille choisie, sans descendre sous le minimum d'un affichage (paroles en grand d'un MP3 en plein
        écran)."""
        mini = getattr(w, "taille_min", None)
        rangs = [n for n, _l, _f in TAILLES]
        if mini in rangs and taille in rangs and rangs.index(mini) > rangs.index(taille):
            return mini
        return taille

    def creer_karaoke(self, style, precedente=False, taille_min=None):
        """Des paroles en surimpression (sur l'image, en plein écran) : tenues à jour par la page (paroles lues,
        langue, taille)."""
        p = self.fenetre.prefs
        w = Karaoke(style=style, taille=p.get("paroles_taille") or "normale", sur_clic=None, precedente=precedente,
                    debut_son=pistes.debut_son_video(self.res), langue=p.get("paroles_langue") or "deux")
        w.taille_min = taille_min
        if taille_min:
            w.regler_taille(self._taille_karaoke(w, p.get("paroles_taille") or "normale"))
        w.suivre(self._fournisseur)
        self._karaokes.append(w)
        self._nourrir(w, message=False)
        return w

    def _nourrir(self, w, message=True):
        """Donne les paroles (ou leur absence) à un affichage ; la première demande les fait lire."""
        if self.paroles_lues is not None:
            w.definir(self.paroles_lues)
            w.placer(self.lecteur.position_ref())
        else:
            w.dire(self._message_paroles if message else "")
        if self._paroles_demandees:
            return
        self._paroles_demandees = True
        r = self.res

        def lu(p, msg):
            if self._oubliee:
                return
            if p is not None:
                self.paroles_lues = p
            elif msg == "attente":
                self._message_paroles = "Lecture des sous-titres de la vidéo doublée (sous nice)…"
            else:
                self._message_paroles = f"Pas de paroles : {msg}" if msg else "Pas de paroles"
            for x in [self.vue_paroles, self.panneau_paroles, *self._karaokes]:
                if x is None:
                    continue
                if p is not None:
                    x.definir(p)
                    x.placer(self.lecteur.position_ref(), self.lecteur.en_lecture())
                else:
                    x.dire(self._message_paroles if x in (self.vue_paroles, self.panneau_paroles) else "")
        analyses().paroles(r.dossier_voix, r.video, lu)

    def _langue_paroles(self, valeur):
        self.fenetre.prefs = chemins.modifier_preference("paroles_langue", valeur)
        for w in [self.panneau_paroles, *self._karaokes]:
            if w is not None:
                w.regler_langue(valeur)

    def _maj_visualisations(self):
        self._maj_paroles()
        self._maj_visibilites()
        if self.plein is not None:
            self.plein.forme_changee()
        if not vis.disponible():
            return
        p = self.fenetre.prefs
        voulus = {"vumetre": p.get("vis_vumetre"), "melodie": p.get("vis_melodie"), "spectre": p.get("vis_spectre"),
                  "onde": p.get("vis_onde")}
        for nom, oui in voulus.items():
            if oui and nom not in self.vis:
                self._creer(nom)
            elif not oui and nom in self.vis:
                w = self.vis.pop(nom)
                conteneur = getattr(w, "_conteneur", w)
                conteneur.get_parent().remove(conteneur)
        if "melodie" in self.vis:
            self.vis["melodie"].en_hz = bool(p.get("melodie_hz"))
            self.vis["melodie"].montrer_autotune = bool(p.get("melodie_autotune"))
            self.vis["melodie"].queue_draw()
        self._charger_pistes()

    def _creer(self, nom):
        duree = self.res.duree_s
        if nom == "melodie":
            w = vis.Melodie(sur_clic=self._clic_frise, en_hz=bool(self.fenetre.prefs.get("melodie_hz")),
                            autotune=bool(self.fenetre.prefs.get("melodie_autotune")))
            boite = commun.boite(espace=4)
            boite.append(Gtk.Label(label="Mélodie", xalign=0, css_classes=["heading"]))
            boite.append(w)
            boite.append(vis.legende_melodie(autotune=bool(self.res.autotune)))
            if self.res.dossier_voix and self.res.rapport:
                def lu(c, message, w=w, titre=boite.get_first_child()):
                    if c is not None:
                        w.definir(c, self.segs, duree)
                        titre.set_label("Mélodie" + (" — courbes de doubler" if c.get("origine") == "doubler"
                                                     else " — calcul local (ancien doublage)"))
                    elif message == "attente":
                        w.dire("Calcul de la courbe de mélodie (sous nice ; il attend son tour si le poste est chargé)…")
                    else:
                        w.dire(message or "Pas de courbe de mélodie")
                analyses().courbes(self.res.dossier_voix, lu, duree)
            else:
                w.dire("Doublage inachevé : pas de courbe de mélodie")
        elif nom == "spectre":
            w = vis.Spectre(sur_clic=self._clic_frise)
            boite = commun.boite(espace=4)
            self.l_spectre = Gtk.Label(label="Spectrogramme", xalign=0, css_classes=["heading"])
            boite.append(self.l_spectre)
            boite.append(w)
        elif nom == "onde":
            w = vis.Onde(sur_clic=self._clic_frise)
            boite = commun.boite(espace=4)
            boite.append(Gtk.Label(label="Forme d’onde", xalign=0, css_classes=["heading"]))
            boite.append(w)
        else:
            w = vis.VuMetre()
            boite = w
        w._conteneur = boite
        w.suivre(self._fournisseur)
        w.placer(self.lecteur.position_ref())
        self.vis[nom] = w
        cible = self.boite_onde if nom == "onde" else self.boite_vis
        ordre = ["vumetre", "melodie", "spectre"]
        if cible is self.boite_vis:
            # garder l'ordre vu-mètre, mélodie, spectrogramme
            apres = None
            for autre in ordre[:ordre.index(nom)]:
                if autre in self.vis:
                    apres = getattr(self.vis[autre], "_conteneur", self.vis[autre])
            cible.insert_child_after(boite, apres)
        else:
            cible.append(boite)

    def _produits(self):
        return [p for nom, p in PRODUITS_VIS.items() if nom in self.vis]

    def _cle_vis(self):
        return self.piste if self.piste in self.pistes_dispo else "doublage"

    def _charger_pistes(self, cles=None):
        """Analyse (en cache, sinon sous nice) des pistes à visualiser : par défaut le doublage, l'original et la piste
        écoutée ; les autres le seront quand on les écoutera."""
        produits = self._produits()
        if not produits:
            return
        cles = cles or list(dict.fromkeys(["doublage", "original", self._cle_vis()]))
        fichiers = [f for f in dict.fromkeys(self.fichier_piste(c) for c in cles) if f]
        fichiers = [f for f in fichiers if (str(f), tuple(produits)) not in self._demandees]
        if not fichiers:
            if self.fichier_piste(self._cle_vis()) is None:
                for nom in PRODUITS_VIS:
                    if nom in self.vis:
                        self.vis[nom].dire("Pas de son à analyser pour cette piste")
            return
        self._demandees.update((str(f), tuple(produits)) for f in fichiers)

        def lu(r, message):
            for f, d in r.items():
                if d is not None:
                    self.pistes[str(f)] = {**self.pistes.get(str(f), {}), **d}
            if message == "attente":
                for nom in PRODUITS_VIS:
                    if nom in self.vis and not self._donnees(self._cle_vis()):
                        self.vis[nom].dire("Analyse du son (sous nice ; elle attend son tour si le poste est chargé)…")
            elif message:
                for nom in PRODUITS_VIS:
                    if nom in self.vis:
                        self.vis[nom].dire(message)
            self._appliquer_pistes()
        analyses().pistes(fichiers, produits, lu, self.res.duree_s)

    def _donnees(self, cle):
        f = self.fichier_piste(cle)
        return self.pistes.get(str(f)) if f else None

    def _appliquer_pistes(self):
        """Les visualisations montrent la piste écoutée (son analyse est demandée la première fois)."""
        produits = self._produits()
        if not produits:
            return
        cle = self._cle_vis()
        d = self._donnees(cle)
        if d is None or any(p not in d for p in produits):
            f = self.fichier_piste(cle)
            if f is not None and (str(f), tuple(produits)) not in self._demandees:
                for nom in PRODUITS_VIS:
                    if nom in self.vis:
                        self.vis[nom].dire(f"Analyse de la piste « {self.pistes_dispo[cle].libelle} »…")
                self._charger_pistes([cle])
            return
        for nom, produit in PRODUITS_VIS.items():
            if nom in self.vis and produit in d:
                self.vis[nom].definir(d)
        if "spectre" in self.vis and hasattr(self, "l_spectre"):
            self.l_spectre.set_label(f"Spectrogramme — {self.pistes_dispo[cle].libelle}")

    # --- groupes ---------------------------------------------------------------------------------------------------

    def _groupe_infos(self):
        r, rap = self.res, self.res.rapport or {}
        g = Adw.PreferencesGroup(title="Doublage")
        if not r.complet:
            g.set_description("Doublage inachevé : pas de rapport ou pas de fichier final dans ce dossier.")
        g.add(commun.ligne_valeur("Voix", f"{r.voix_nom} ({r.voix})" if r.voix else "—"))
        lignes_vc = r.resume_voix_cibles()
        if lignes_vc:   # une voix par chanteur : la décision, les voix entendues, les passages en duo
            l_vc = Adw.ActionRow(title="Une voix par chanteur", subtitle=commun.echapper("\n".join(lignes_vc)),
                                 subtitle_lines=6)
            g.add(l_vc)
        if r.mode:
            g.add(commun.ligne_valeur("Mode", r.mode + (" (imposé)" if rap.get("mode_force") else " (détecté)")))
        if r.modele or r.pas:
            texte = f"{r.libelle_moteur() or r.modele or '—'} · {r.pas or '—'} pas"
            demande = r.moteur_demande
            sous_titre = None
            if r.repli:
                sous_titre = f"Repli sur Seed-VC : {r.repli}"
            elif demande and r.moteur and demande != r.moteur:
                sous_titre = f"Demandé : {rapports.NOMS_MOTEURS.get(demande, demande)}"
            elif (rap.get("moteur_info") or {}).get("note"):
                sous_titre = rap["moteur_info"]["note"]
            ligne_m = commun.ligne_valeur("Moteur", texte, sous_titre)
            if r.repli:
                ligne_m.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"],
                                             tooltip_text="YingMusic-SVC a échoué : la conversion s’est repliée sur "
                                                          "Seed-VC 44 kHz"))
            g.add(ligne_m)
        net = r.resume_nettoyage()
        if net is not None:
            applique, texte = net
            ligne_n = Adw.ActionRow(title="Nettoyage", subtitle=commun.echapper(texte), subtitle_lines=3)
            ligne_n.add_suffix(commun.badge("appliqué" if applique else "non appliqué",
                                            "succes" if applique else "neutre"))
            g.add(ligne_n)
        h = r.resume_hauteur()
        if h:
            raison = r.raison_hauteur()
            ligne_h = commun.ligne_valeur("Hauteur", h, raison or None)
            ligne_h.set_subtitle_lines(3)
            g.add(ligne_h)
        at = r.resume_autotune()
        if at:
            ton, mesures = at
            l_at = Adw.ActionRow(title="Auto-tune", subtitle=commun.echapper(
                f"intensité {r.autotune.get('intensite')} · gamme {ton}" + (f"\n{mesures}" if mesures else "")),
                subtitle_lines=3)
            l_at.add_suffix(commun.badge("version en plus", "succes"))
            g.add(l_at)
        elif isinstance(rap.get("autotune"), dict) and rap["autotune"].get("raison"):
            g.add(commun.ligne_valeur("Auto-tune", "non appliqué", rap["autotune"]["raison"]))
        mix = rap.get("mix") or {}
        if mix:
            texte = f"v{mix.get('version', '?')} · voix {formats.db(mix.get('voix_db'))}"
            if mix.get("reverb_db") is not None:
                texte += f" · reverb {formats.db(mix['reverb_db'])}"
            g.add(commun.ligne_valeur("Mixage", texte))
        if r.duree_s:
            g.add(commun.ligne_valeur("Durée", formats.chrono(r.duree_s)))
        if r.extrait:
            d, u = r.extrait
            ex = f"{formats.chrono(d)} → {formats.chrono(d + u)}" if u else f"à partir de {formats.chrono(d)}"
            if (r.source or {}).get("debut") is not None:
                ex += f" (image clé à {formats.chrono_precis(r.source['debut'])})"
            g.add(commun.ligne_valeur("Extrait", ex))
        src = r.video_source
        ligne_src = commun.ligne("Vidéo source", str(src) if src else "non indiquée")
        if src and not r.source_presente:
            ligne_src.add_suffix(commun.badge("introuvable", "alerte"))
            choisie = self.source_choisie()
            if choisie:
                ligne_src.set_subtitle(commun.echapper(f"{src}\nchoisie à la main : {choisie}"))
            ligne_src.add_suffix(commun.bouton("Choisir…", valign=Gtk.Align.CENTER, clic=self.choisir_source,
                                               infobulle="Choisir la vidéo d’origine pour la bascule A/B "
                                                         "(travail/source.json n’est pas modifié)"))
        g.add(ligne_src)
        for titre, f in (("Vidéo doublée", r.video), ("Vidéo auto-tune", r.video_autotune if r.autotune else None)):
            if f:
                l = commun.ligne(titre, f.name)
                l.add_suffix(commun.bouton(icone="folder-open-symbolic", infobulle="Montrer dans le dossier",
                                           styles=("flat",), valign=Gtk.Align.CENTER,
                                           clic=lambda f=f: commun.montrer_fichier(self.fenetre, f)))
                g.add(l)
        return g

    def _groupe_pistes(self):
        g = Adw.PreferencesGroup(title="Pistes séparées",
                                 description="À écouter seules par les bascules au-dessus du lecteur, ou à enregistrer "
                                             "en MP3 (ou FLAC) à côté des sorties. Les fichiers de doubler ne sont "
                                             "pas modifiés.")
        self.lignes_pistes = {}
        voix = "les voix converties" if self.res.plusieurs_voix else "ma voix seule"
        choix = [("beat", "Enregistrer le beat…"), ("ma_voix", f"Enregistrer {voix}…"), ("voix_origine", None)]
        if "ma_voix_at" in self.pistes_dispo:
            choix.insert(2, ("ma_voix_at", f"Enregistrer {voix} (auto-tune)…"))
        for cle, libelle in choix:
            ligne = Adw.ActionRow(title=self.pistes_dispo[cle].libelle, subtitle_lines=3)
            b = None
            if libelle:
                b = commun.bouton(libelle, valign=Gtk.Align.CENTER, clic=lambda c=cle: self.enregistrer(c))
                ligne.add_suffix(b)
            self.lignes_pistes[cle] = (ligne, b)
            g.add(ligne)
        self._maj_groupe_pistes()
        return g

    def _maj_groupe_pistes(self):
        for cle, (ligne, b) in getattr(self, "lignes_pistes", {}).items():
            p = self.pistes_dispo.get(cle)
            if p is None:
                continue
            texte = phrase(p.explication)
            if cle in VOIX_SEULES and p.a_preparer and not p.dans_la_video:
                texte += " ; préparée en cache au premier usage."
            ligne.set_subtitle(commun.echapper(texte))
            if b is not None:
                b.set_sensitive(p.disponible)
                b.set_tooltip_text(None if p.disponible else f"Indisponible : {p.explication}")

    def enregistrer(self, cle):
        """« Enregistrer le beat… », « Enregistrer ma voix seule… » : sélecteur ouvert sur le dossier des sorties,
        avec le nom des sorties (« <titre> - voix <Nom> (beat).mp3 ») ; .flac ou .wav si on change l'extension."""
        p = self.pistes_dispo.get(cle)
        if p is None or not p.disponible:
            return
        filtres = commun.liste_filtres(commun.filtre_fichiers("MP3 (320 kb/s)", ("audio/mpeg",), ("*.mp3",)),
                                       commun.filtre_fichiers("FLAC (sans perte)", ("audio/flac",), ("*.flac",)))
        d = Gtk.FileDialog(title=f"Enregistrer {'le beat' if cle == 'beat' else p.libelle.lower()}", modal=True,
                           filters=filtres, initial_folder=Gio.File.new_for_path(str(self.res.dossier)),
                           initial_name=pistes.nom_export(self.res, cle, ".mp3"))

        def fini(dlg, r):
            try:
                f = commun.chemin_de(dlg.save_finish(r))
            except GLib.Error:
                return
            if not f:
                return
            if f.suffix.lower() not in pistes.FORMATS_EXPORT:
                f = f.with_name(f.name + ".mp3")
            self._exporter(cle, f)
        d.save(self.fenetre, None, fini)

    def _exporter(self, cle, sortie):
        p = self.pistes_dispo.get(cle)
        self.fenetre.toast(f"Enregistrement de {sortie.name} (sous nice)…")

        def fini(ok, message):
            if ok:
                self.fenetre.toast(f"Enregistré : {sortie.name}", "Montrer",
                                   lambda: commun.montrer_fichier(self.fenetre, sortie), duree=6)
            else:
                commun.informer(self.fenetre, "Enregistrement impossible", message)
        source = pistes.source_export(p)     # fichier de travail/ ou du cache, sinon la piste de la vidéo doublée
        if source is None:
            analyses().exporter(self.res.dossier_voix, sortie, fini, self.res.duree_s, voix=True,
                                autotune=cle == "ma_voix_at")
        else:
            analyses().exporter(source[0], sortie, fini, self.res.duree_s, piste_audio=source[1])

    def _groupe_remix(self):
        r, rap = self.res, self.res.rapport or {}
        mix = rap.get("mix") or {}
        g = Adw.PreferencesGroup(title="Remix rapide",
                                 description="Refait seulement le mixage et la remise sur la vidéo, depuis travail/, "
                                             "sans Kaggle : quelques secondes.")
        chant = r.mode == "chant"
        voix_db = mix.get("voix_db")
        if voix_db is None:
            voix_db = self.fenetre.prefs.get("voix_db")
        if voix_db is None:
            voix_db = 4.0 if chant else 0.0
        self.s_voix = Adw.SpinRow.new_with_range(-12, 12, 0.5)
        self.s_voix.set_title("Niveau de la voix (dB)")
        self.s_voix.set_subtitle("Par rapport à la voix d’origine")
        self.s_voix.set_digits(1)
        self.s_voix.set_value(float(voix_db))
        g.add(self.s_voix)
        self.s_reverb = Adw.SpinRow.new_with_range(-40, 0, 1)
        self.s_reverb.set_title("Reverb (dB)")
        self.s_reverb.set_subtitle("Sous la voix sèche" if chant else "Sans effet en parole (pas de reverb)")
        rev = mix.get("reverb_db")
        self.s_reverb.set_value(float(rev if rev is not None else (self.fenetre.prefs.get("reverb_db") or -13)))
        self.s_reverb.set_sensitive(chant)
        g.add(self.s_reverb)
        self.s_v1 = Adw.SwitchRow(title="Ancien mixage (v1)", subtitle="Sans suivi des nuances ni dé-esseur",
                                  active=mix.get("version") == 1)
        self.s_v1.set_visible("--mix-v1" in self.fenetre.caps)
        g.add(self.s_v1)
        # version auto-tune (doubler --remix --autotune N, --sans-autotune) : les réglages de la page Doubler ; allumée
        # si le doublage en a une ; grisée sans --autotune ou sans x42-autotune
        from .page_doubler import reglages_autotune
        self.r_at, self.at_echelle, self.r_at_gamme = reglages_autotune("Version auto-tune")
        self.r_at_gamme.set_visible("--autotune-gamme" in self.fenetre.caps)
        self._conf_at = None          # « doubler --config --json » : intensité par défaut, présence de x42-autotune
        self._maj_remix_autotune()
        self.r_at.connect("notify::enable-expansion", lambda *_: self._sous_titre_autotune())
        g.add(self.r_at)
        if "--autotune" in self.fenetre.caps:
            commun.en_fil(moteur.config_effective, self._conf_autotune)
        possible, raison = moteur.remix_possible(r.video_source, r.dossier) if r.voix and r.rapport else \
            (False, "doublage inachevé")
        ligne = Adw.ActionRow(title="Refaire le mixage", subtitle=raison if not possible else
                              "Les fichiers mp4 et mp3 de ce doublage sont remplacés")
        self.roue = Gtk.Spinner(valign=Gtk.Align.CENTER)
        ligne.add_suffix(self.roue)
        self.b_remix = commun.bouton("Remixer", styles=("suggested-action",), clic=self.remixer,
                                     valign=Gtk.Align.CENTER, sensitive=possible)
        ligne.add_suffix(self.b_remix)
        g.add(ligne)
        self.l_remix = ligne
        return g

    def _maj_remix_autotune(self):
        """Réglages d'auto-tune du remix d'après le doublage : interrupteur allumé s'il a sa version auto-tune,
        intensité de rapport.json (sinon le défaut de la configuration de doubler, sinon 70), gamme du rapport si elle
        avait été imposée (sinon Auto) ; grisés si le remix ne peut pas faire d'auto-tune, avec la raison."""
        version, n, gamme = moteur.autotune_du_resultat(self.res.rapport, self._conf_at)
        self.r_at.set_enable_expansion(version)
        self.at_echelle.set_value(n)
        self.r_at_gamme.set_selected([v for v, _l in moteur.GAMMES].index(gamme))
        self._sous_titre_autotune()

    def _conf_autotune(self, conf):
        """La configuration de doubler est lue : intensité par défaut (si le rapport n'en donne pas et qu'on n'y a
        pas touché), x42-autotune absent (grisé)."""
        if self._oubliee:
            return
        avant = moteur.autotune_du_resultat(self.res.rapport, self._conf_at)[1]
        self._conf_at = conf if isinstance(conf, dict) else {}
        n = moteur.autotune_du_resultat(self.res.rapport, self._conf_at)[1]
        if round(self.at_echelle.get_value()) == avant:
            self.at_echelle.set_value(n)
        self._sous_titre_autotune()

    def _sous_titre_autotune(self):
        """Ce que le remix fera de la version auto-tune, en sous-titre de son interrupteur."""
        from .page_doubler import TEXTE_X42_ABSENT
        possible, cause = moteur.autotune_remix_possible(self.fenetre.caps, self._conf_at)
        self.r_at.set_sensitive(possible)
        version = bool(self.res.autotune)
        if not possible:
            texte = (TEXTE_X42_ABSENT if cause == "x42" else
                     "doubler trop ancien, sans --autotune : le remix ne touche pas à l’auto-tune")
        elif self.r_at.get_enable_expansion():
            texte = ("Refaite avec ces réglages ; la version normale ne change pas" if version else
                     "Une version « (auto-tune) » en plus (vidéo, mp3, .lrc) ; la version normale ne change pas")
        elif version:
            texte = "Éteinte : au remix, la version auto-tune (vidéo, mp3, .lrc) ira à la corbeille"
        else:
            texte = "Pas de version auto-tune ; allumez pour en faire une en plus de la version normale"
        self.r_at.set_subtitle(texte)

    def _reglages_autotune(self):
        """Options d'auto-tune du remix (moteur.reglages_remix_autotune) d'après les réglages affichés."""
        gamme = moteur.GAMMES[self.r_at_gamme.get_selected()][0] if self.r_at_gamme.get_visible() else "auto"
        return moteur.reglages_remix_autotune(self.r_at.get_enable_expansion(), self.at_echelle.get_value(), gamme,
                                              bool(self.res.autotune), possible=self.r_at.get_sensitive())

    def _maj_menu_autotune(self):
        """« Courbe de la version auto-tune » dans le menu des visualisations, seulement si le doublage en a une."""
        n = self.menu_melodie.get_n_items()
        if self.res.autotune and n < 2:
            self.menu_melodie.append("Courbe de la version auto-tune", "win.melodie-autotune")
        elif not self.res.autotune and n > 1:
            self.menu_melodie.remove(1)

    def _rafraichir(self):
        """Après un remix, ce qui dépend des fichiers du doublage suit le rapport relu : la bascule « Auto-tune »
        (touche T) apparaît ou disparaît, le sous-titre, le menu de la mélodie, les groupes Doublage et Pistes
        séparées, les réglages d'auto-tune du remix."""
        r = self.res
        self.t_autotune.set_visible(bool(r.autotune))
        self.titre_fenetre.set_subtitle(sous_titre(r))
        self._maj_menu_autotune()
        for nom, faire in (("g_infos", self._groupe_infos), ("g_pistes", self._groupe_pistes)):
            ancien, neuf = getattr(self, nom), faire()
            self.corps.insert_child_after(neuf, ancien)
            self.corps.remove(ancien)
            setattr(self, nom, neuf)
        # préparations ratées d'une piste qui n'existe plus (ma voix seule auto-tunée d'une version retirée)
        self._echecs = {k: m for k, m in self._echecs.items() if k in self.pistes_dispo}
        self._maj_remix_autotune()
        self._maj_note()

    def _groupe_durees(self):
        g = Adw.PreferencesGroup(title="Durées par étape")
        lignes = rapports.durees_etapes(self.res.rapport)
        if not lignes:
            g.add(commun.ligne("Aucune durée dans ce rapport"))
            return g
        for libelle, s, details in lignes:
            if details:
                e = Adw.ExpanderRow(title=commun.echapper(libelle))
                self.depliant_durees = e
                e.add_suffix(Gtk.Label(label=formats.duree(s, precis=True), css_classes=["dim-label", "chiffres"]))
                for a, b in details:
                    e.add_row(commun.ligne_valeur(a, formats.duree(b, precis=True)))
                g.add(e)
            else:
                g.add(commun.ligne_valeur(libelle, formats.duree(s, precis=True)))
        # attente d'une place GPU, session partagée, deux cartes T4 (temps de GPU additionnés) : toutes les notes
        notes = rapports.notes_durees(self.res.rapport)
        if notes:
            g.set_description(commun.echapper(" ".join(notes)))
        return g

    def _groupe_comparaisons(self):
        g = Adw.PreferencesGroup(title="Comparaisons de pas",
                                 description="Le même passage d’environ 30 s converti avec deux nombres de pas.")
        for f in self.res.comparaisons:
            l = commun.ligne(f.stem.replace("_", " "))
            l.add_suffix(commun.bouton(icone="media-playback-start-symbolic", infobulle="Écouter", styles=("flat",),
                                       valign=Gtk.Align.CENTER, clic=lambda f=f: self._ecouter_fichier(f)))
            g.add(l)
        return g

    def _ecouter_fichier(self, f):
        self.voulue = None
        self.lecteur.definir({"comparaison": (f, 0.0, f.stem)}, "comparaison", audio_seul=True)
        self.piste = "comparaison"
        self._maj = True
        for t in (*self.t_pistes.values(), self.t_video, self.t_mp3):
            t.set_active(False)
        self._maj = False
        self.note_piste.set_label(f"Lecture de {f.name} (revenir avec Vidéo, MP3 ou une piste).")
        m = self.lecteur.media()
        if m:
            m.play()

    def scenario(self, action):
        """Essai : « ab » (lecture muette, puis bascule sur l'original à la même position), « aller-<s> »,
        « piste-<clé> » (beat, ma_voix…), « lire » (lecture muette), « remix » (défile jusqu'au remix rapide),
        « remix-at » ou « remix-sans-at » (interrupteur « Version auto-tune »), « remixer » (lance le remix)."""
        if action.startswith("piste-"):
            # après « lire » (1,8 s) : la vidéo a déjà une image à figer
            cle = action.split("-", 1)[1]
            GLib.timeout_add(2600, lambda: (cle in self.t_pistes and self.t_pistes[cle].set_active(True), False)[1])
        if action == "mp3" and self.t_mp3.get_sensitive():
            self.t_mp3.set_active(True)
        if action == "autotune":
            GLib.timeout_add(2600, lambda: (self.bascule_autotune(), False)[1])
        if action in ("lire", "ab") or action.startswith("piste-"):
            self.lecteur.rendre_muet()     # essais : jamais de son, même sur une piste ouverte plus tard
        if action == "lire":
            def lire_muet():
                m = self.lecteur.media()
                if m:
                    m.play()
                return False
            GLib.timeout_add(1800, lire_muet)
        if action == "durees" and getattr(self, "depliant_durees", None):
            self.depliant_durees.set_expanded(True)
        if action in ("remix-at", "remix-sans-at"):
            # interrupteur « Version auto-tune » du remix rapide, allumé (déplié) ou éteint
            GLib.timeout_add(1200, lambda: (self.r_at.set_enable_expansion(action == "remix-at"), False)[1])
        if action == "remixer":
            GLib.timeout_add(1500, lambda: (self.remixer(), False)[1])
        if action == "remix":
            def vers_remix():
                # le groupe « Remix rapide » en haut de la vue (après les autres actions, qui changent la hauteur)
                from gi.repository import Graphene    # chargé par gui/essais.py (captures d'essai)
                ok, point = self.g_remix.compute_point(self.corps, Graphene.Point())
                if ok:
                    self.defilement.get_vadjustment().set_value(max(0.0, point.y - 12))
                return False
            GLib.timeout_add(3000, vers_remix)
        if action == "bas":
            def descendre():
                a = self.defilement.get_vadjustment()
                a.set_value(a.get_upper() - a.get_page_size())
                return False
            GLib.timeout_add(2500, descendre)
        if action.startswith("aller-"):
            t = float(action.split("-", 1)[1])
            GLib.timeout_add(1500, lambda: (self.lecteur.aller_a(t), self._position(t, False), False)[2])
        # lecteur complet (captures) : plein écran (commandes visibles, puis masquées), mode cinéma, panneau
        # « Toutes les paroles », menu « œil », boucle A-B, vitesse, sous-titres sur l'image, taille des paroles
        if action == "plein-ecran":
            GLib.timeout_add(2200, lambda: (self.entrer_plein_ecran(), False)[1])
        if action == "masquer":
            GLib.timeout_add(3600, lambda: (self.plein is not None and self.plein.masquer_commandes(), False)[1])
        if action == "cinema":
            GLib.timeout_add(1200, lambda: (self.basculer_cinema(True), False)[1])
        if action == "panneau":
            self.fenetre.regler_visualisation("paroles_panneau", True)
        if action == "sous-titres":
            self.fenetre.regler_visualisation("paroles_video", True)
        if action.startswith("taille-"):
            self.fenetre.regler_visualisation("paroles_taille", action.split("-", 1)[1])
        if action == "oeil":
            GLib.timeout_add(2600, lambda: (self.b_oeil.popup(), False)[1])
        if action.startswith("boucle-"):
            a, b = (float(x) for x in action.split("-")[1:3])
            GLib.timeout_add(1600, lambda: (self.lecteur.definir_boucle((a, b)), self._lecteur_change(), False)[2])
        if action.startswith("vitesse-"):
            v = float(action.split("-", 1)[1])
            GLib.timeout_add(2400, lambda: (self.regler_vitesse(v), False)[1])
        if action == "clavier":
            GLib.timeout_add(2600, lambda: (self._essai_clavier(), False)[1])
        if action == "frises":
            def vers_frises():
                from gi.repository import Graphene    # chargé par gui/essais.py (captures d'essai)
                ok, point = self.g_octave.compute_point(self.corps, Graphene.Point())
                if ok:
                    self.defilement.get_vadjustment().set_value(max(0.0, point.y - 12))
                return False
            GLib.timeout_add(3000, vers_frises)

    def _essai_clavier(self):
        """Essai (scénario « clavier », sous Xvfb) : de vraies touches envoyées par xdotool, et l'état relevé après
        chacune, une ligne JSON par touche sur la sortie."""
        import json
        import subprocess
        touches = ["Right", "Right", "Left", "l", "Right", "l", "bracketright", "bracketright", "bracketleft", "m",
                   "m", "3", "1", "b", "v", "v", "p", "p", "space", "period", "comma", "f", "space", "Right", "l",
                   "space", "Escape", "Home", "s"]

        def etat():
            return {"position": round(self.lecteur.position_ref(), 2), "boucle": list(self.lecteur.boucle),
                    "vitesse": self.lecteur.vitesse, "coupe": self.lecteur.coupe, "piste": self.voulue or self.piste,
                    "cinema": self.cinema, "panneau": bool(self.fenetre.prefs.get("paroles_panneau")),
                    "plein": self.plein is not None, "lecture": self.lecteur.en_lecture(),
                    "focus": type(self.fenetre.get_focus()).__name__}

        def suivante(i=0):
            if i >= len(touches):
                return False
            subprocess.run(["xdotool", "key", "--clearmodifiers", touches[i]], timeout=5, check=False)
            GLib.timeout_add(450, lambda: (print(json.dumps({"touche": touches[i], **etat()}), flush=True),
                                           suivante(i + 1), False)[2])
            return False
        print(json.dumps({"touche": None, **etat()}), flush=True)
        suivante()
        if action.startswith("puis-"):
            # après « lire » : on quitte le résultat pendant la lecture (barre « en cours de lecture ») ; « liste » :
            # retour à la liste des résultats, sinon une autre section
            cible = action.split("-", 1)[1]
            GLib.timeout_add(3200, lambda: (self.fenetre.nav.pop() if cible == "liste" else self.fenetre.aller(cible),
                                            False)[1])
        if action == "pause":
            GLib.timeout_add(4200, lambda: (self.mettre_en_pause(), False)[1])
        if action == "retour":
            # après « puis-liste » : retour au résultat qui joue, comme par un clic dans la liste (état retrouvé)
            GLib.timeout_add(5000, lambda: (self.page.ouvrir_detail(self.res), False)[1])
        if action == "fermer":
            # après « lire » : la fenêtre se ferme pendant la lecture (l'application reste, la musique continue)
            GLib.timeout_add(3000, lambda: (self.fenetre.close(), False)[1])
        if action == "ab":
            def lire():
                m = self.lecteur.media()
                if m:
                    m.set_muted(True)
                    m.play()
                return False

            def basculer():
                self.bascule_ab()
                m = self.lecteur.media()
                if m:
                    m.set_muted(True)
                return False
            GLib.timeout_add(1000, lire)
            GLib.timeout_add(3500, basculer)

    # --- actions ---------------------------------------------------------------------------------------------------

    def remixer(self):
        r = self.res
        occupe = [t for t in self.fenetre.file.travaux()
                  if t.actif and str(r.dossier) in (t.demande.get("sorties") or [])]
        if occupe:
            self.fenetre.toast("Un doublage en cours écrit dans ce dossier : remix impossible pour l’instant")
            return
        if self.page.remix_actif:
            self.fenetre.toast("Un remix est déjà en cours")
            return
        voix_db = round(self.s_voix.get_value(), 1)
        reverb = round(self.s_reverb.get_value(), 1) if r.mode == "chant" else None
        v1 = self.s_v1.get_visible() and self.s_v1.get_active()
        at = self._reglages_autotune()
        pos = self.lecteur.position_ref()
        self.lecteur.liberer()   # les fichiers vont être réécrits
        self.page.remix_actif = True
        self.roue.set_spinning(True)
        self.b_remix.set_sensitive(False)
        self.l_remix.set_subtitle("Remix en cours…")
        donnees = self.fenetre.donnees

        def fini(res):
            ok, sortie = res
            self.page.remix_actif = False
            self.roue.set_spinning(False)
            self.b_remix.set_sensitive(True)
            if ok:
                frais = rapports.lire_dossier(r.dossier, {v["id"]: v["nom"] for v in self.fenetre.voix},
                                              r.groupe, self.fenetre.voix_doubler)
                self.res = next((x for x in frais if x.voix == r.voix), r)
                if not self.res.autotune:
                    self.autotune = False     # version auto-tune retirée (ou pas faite) : la bascule T s'éteint
                suite = (f", auto-tune {self.res.autotune.get('intensite')}" if at.get("autotune") and self.res.autotune
                         else ", auto-tune non appliqué" if at.get("autotune")
                         else ", version auto-tune à la corbeille" if at.get("sans_autotune") else "")
                self.l_remix.set_subtitle(f"Remixé : voix {formats.db(voix_db)}"
                                          + (f", reverb {formats.db(reverb)}" if reverb is not None else "") + suite)
                self.fenetre.toast("Remix fini")
            else:
                lignes = [l for l in sortie.strip().splitlines() if l.strip()]
                self.l_remix.set_subtitle(commun.echapper(lignes[-1] if lignes else "échec"))
                commun.informer(self.fenetre, "Le remix a échoué", "\n".join(lignes[-12:]) or "sans message")
            self.changer_forme(self.forme)
            self.lecteur.aller_a(pos)
            if ok:
                self._rafraichir()
        spec = moteur.spec_de_rapport(r.voix, r.rapport)   # « homme:a,femme:b » pour le dossier « a+b »
        commun.en_fil(lambda: moteur.remixer(r.video_source, spec, r.dossier, donnees, voix_db, reverb, v1,
                                             autotune=at), fini)

    def supprimer(self):
        """Bouton corbeille du détail : la même mise à la corbeille que la bibliothèque
        (PageBibliotheque.mettre_a_la_corbeille), puis retour à la bibliothèque."""
        self.page.mettre_a_la_corbeille([self.res], apres=lambda ok: ok and self.dans_la_pile() and
                                        self.fenetre.nav.pop())

    def fermer_pour_corbeille(self):
        """Ses fichiers vont à la corbeille : lecture arrêtée, flux fermés (copies vidéo en cache comprises) ; la page
        n'est plus la lecture en cours, et elle est oubliée si elle n'est plus affichée."""
        self.lecteur.liberer()
        if not self.dans_la_pile():
            self.oublier()
