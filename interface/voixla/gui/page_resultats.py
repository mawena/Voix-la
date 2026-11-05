"""Page Résultats : la liste des doublages, et pour chacun un lecteur (vidéo ou MP3) où l'on choisit ce qu'on écoute
à la même position (doublage, original, beat seul, ma voix seule, voix d'origine seule ; touche B : doublage ⇄
original), les paroles synchronisées, la frise d'octave, les durées par étape, un remix rapide, l'enregistrement du
beat ou de la voix seule, ouvrir le dossier, supprimer vers la corbeille."""

from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .. import chemins, corbeille, formats, moteur, pistes, rapports
from .. import lecture as regles
from . import commun
from . import visualisations as vis
from .analyses import analyses
from .frise import Frise, FriseVoix, legende as legende_frise, legende_voix
from .lecteur import Lecteur
from .lecture import lectures
from .vue_paroles import VueParoles

PRODUITS_VIS = {"spectre": "spectre", "onde": "onde", "vumetre": "bandes"}
VOIX_SEULES = ("ma_voix", "ma_voix_at")     # voix seules combinées en cache (normale et auto-tune)


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
        self.vue_paroles = None
        self.pistes_dispo = pistes.choisir_pistes(res, self.forme, self.source_choisie())
        vue = Adw.ToolbarView()
        entete = Adw.HeaderBar()
        entete.set_title_widget(Adw.WindowTitle(title=res.titre_court, subtitle=sous_titre(res)))
        b_dossier = commun.bouton(icone="folder-open-symbolic", infobulle="Ouvrir le dossier",
                                  clic=lambda: commun.ouvrir_dossier(self.fenetre, res.dossier))
        entete.pack_end(b_dossier)
        b_suppr = commun.bouton(icone="user-trash-symbolic", infobulle="Mettre à la corbeille…",
                                clic=self.supprimer)
        entete.pack_end(b_suppr)
        menu_vis = Gio.Menu()
        for action, libelle in (("vis-melodie", "Courbe de mélodie"), ("vis-spectre", "Spectrogramme"),
                                ("vis-onde", "Forme d’onde"), ("vis-vumetre", "Vu-mètre")):
            menu_vis.append(libelle, f"win.{action}")
        section = Gio.Menu()
        section.append("Mélodie en Hz (sinon en notes)", "win.melodie-hz")
        if res.autotune:
            section.append("Courbe de la version auto-tune", "win.melodie-autotune")
        menu_vis.append_section(None, section)
        section = Gio.Menu()
        section.append("Paroles", "win.paroles")
        menu_vis.append_section(None, section)
        entete.pack_end(Gtk.MenuButton(icon_name="view-reveal-symbolic", menu_model=menu_vis,
                                       tooltip_text="Visualisations du son et paroles"))
        vue.add_top_bar(entete)

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

        self.lecteur = Lecteur(hauteur=320, sur_position=self._position, proprietaire=self)
        corps.append(self.lecteur)
        # Visualisations du son (chacune s'allume et s'éteint par le menu « œil » ; préférences mémorisées)
        self.vis = {}                 # nom → widget
        self.pistes = {}              # fichier → données d'analyse
        self.boite_vis = commun.boite(espace=10)
        corps.append(self.boite_vis)
        self.boite_onde = commun.boite(espace=6)

        # Frise
        self.segs = rapports.segments_frise(res.rapport)
        g_frise = commun.boite(espace=6)
        tete = Gtk.Box(spacing=8)
        tete.append(Gtk.Label(label="Frise d’octave", xalign=0, css_classes=["heading"], hexpand=True))
        self.passage = Gtk.Label(xalign=1, css_classes=["caption", "chiffres"])
        tete.append(self.passage)
        g_frise.append(tete)
        if self.segs:
            self.frise = Frise(sur_clic=self._clic_frise)
            self.frise.definir(self.segs, rapports.duree_frise(res.rapport, self.segs))
            g_frise.append(self.frise)
            g_frise.append(legende_frise(self.segs))
            g_frise.append(commun.legende("Chaque passage montre la voix détectée et son décalage en demi-tons ; un clic "
                                          "va à ce moment."))
        else:
            self.frise = None
            g_frise.append(commun.legende("Pas de frise dans ce rapport."))
        # une voix par chanteur : quelle voix cible chante chaque passage (voix principale et chœurs compris)
        self.frise_voix = None
        passages = rapports.passages_voix(res.rapport)
        if passages:
            noms = [v.get("nom") for v in res.voix_cibles if v.get("nom")] or res.voix_membres
            affichages = {n: self.fenetre.nom_voix(n) for n in noms}
            g_frise.append(Gtk.Label(label="Voix de chaque passage", xalign=0, css_classes=["heading"], margin_top=6))
            self.frise_voix = FriseVoix(sur_clic=self._clic_frise)
            self.frise_voix.definir(passages, noms, rapports.duree_frise(res.rapport, self.segs), affichages)
            g_frise.append(self.frise_voix)
            g_frise.append(legende_voix(noms, affichages))
        corps.append(g_frise)
        corps.append(self.boite_onde)

        # Informations
        corps.append(self._groupe_infos())
        corps.append(self._groupe_pistes())
        corps.append(self._groupe_remix())
        corps.append(self._groupe_durees())
        if res.comparaisons:
            corps.append(self._groupe_comparaisons())

        self.defilement = commun.defile(corps, 980)
        vue.set_content(self.defilement)
        self.set_child(vue)
        raccourcis = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        raccourcis.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("b"),
                                                 Gtk.CallbackAction.new(lambda *_: (self.bascule_ab(), True)[1])))
        raccourcis.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("t"),
                                                 Gtk.CallbackAction.new(lambda *_: (self.bascule_autotune(), True)[1])))
        raccourcis.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("space"),
                                                 Gtk.CallbackAction.new(lambda *_: (self.lecture_pause(), True)[1])))
        self.add_controller(raccourcis)
        # quitter la page n'arrête pas la lecture ; une page quittée qui ne lit pas est oubliée
        self.connect("hidden", lambda *_: self._cachee())
        self.connect("shown", lambda *_: self.page.proteger_caches())
        self.changer_forme(self.forme)
        self.fenetre.ecouter("visualisations", self._maj_visualisations)
        self._maj_visualisations()

    # --- lecteur ---------------------------------------------------------------------------------------------------

    def changer_forme(self, forme):
        """Vidéo ou MP3 : recharge les pistes, garde la piste écoutée (si elle existe encore) et la position."""
        pos = self.lecteur.position_ref() if self.lecteur.media() else 0.0
        self.forme = forme
        self.pistes_dispo = pistes.choisir_pistes(self.res, forme, self.source_choisie())
        # le beat et les voix seules : leur copie vidéo si elle est prête (en cache), sinon le son seul ; le lecteur
        # compte en temps du son doublé (frise, courbes, paroles), et lit une vidéo décalée du début du son en elle
        # (pistes.source_lecteur) : on passe de la vidéo au son seul, et retour, au même instant du son
        sources = pistes.sources_lecteur(self.pistes_dispo)
        sans_image = {c for c, p in self.pistes_dispo.items() if not p.avec_image}
        ephemeres = {c for c in pistes.SANS_IMAGE_TOUTES if c in self.pistes_dispo and self.pistes_dispo[c].video}
        cle = pistes.piste_apres_changement(self.pistes_dispo, self.voulue or self.piste)
        if cle is not None and self.pistes_dispo[cle].a_lire is None:
            # ma voix seule pas encore prête : on écoute le doublage pendant qu'elle se prépare
            self.voulue = cle
            cle = self._effective("doublage") if "doublage" in sources else next(iter(sources), None)
        self.lecteur.definir(sources, cle, audio_seul=forme == "mp3", sans_image=sans_image, ephemeres=ephemeres,
                             notes={c: self._note_bandeau(c) for c in pistes.SANS_IMAGE_TOUTES
                                    if c in self.pistes_dispo})
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

    def _preparer_tout(self):
        """Dès l'ouverture, avant le moindre clic : ce qui manque se prépare en tâche de fond, une chose à la fois et
        à faible priorité (beat seul, ma voix seule combinée puis copiée sur l'image, voix d'origine seule)."""
        if self._oubliee:
            return
        for cle in pistes.SANS_IMAGE_TOUTES:
            p = self.pistes_dispo.get(cle)
            if p is None or cle in self._echecs:
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
        fichiers = [p.video for p in self.pistes_dispo.values() if p.video]
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
        elif c in ("doublage", "doublage_at"):
            texte = ("Touche B : l’original à la même position." if o and o.fichier else
                     f"Original indisponible : {o.explication}." if o else "")
        elif c == "original":
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
        if cle not in pistes.SANS_IMAGE_TOUTES:
            return ""
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
        if p.video and self.forme == "video":
            pistes.toucher(p.video)           # copie lue : la purge du cache la garde
        self._preparer_video(cle, prioritaire=True)
        self._maj_note()
        self._appliquer_pistes()

    def _effective(self, cle):
        """La piste lue pour la bascule cle : sa version auto-tune si « Auto-tune » est allumé et qu'elle existe."""
        base = pistes.cle_de_base(cle)
        at = pistes.cle_ecoutee(base, self.autotune)
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
        """Ma voix seule (cle « ma_voix ») ou celle de la version auto-tune (« ma_voix_at »), combinée en cache."""
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
        if p is not None and p.fichier is not None:
            self.t_pistes[cible].set_active(True)

    def lecture_pause(self):
        if self.lecteur.en_lecture():
            self.mettre_en_pause()
        else:
            self.lire()

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
        self._oubliee = True
        self.fenetre.oublier("visualisations", self._maj_visualisations)
        for w in list(self.vis.values()) + ([self.vue_paroles] if self.vue_paroles is not None else []):
            if getattr(w, "animateur", None):
                w.animateur.arreter()
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
                           volume=m.get_volume() if m is not None else 1.0)

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
        if self.dans_la_pile():
            lectures().terminer(self)
        else:
            self.oublier()

    def aller_a(self, s):
        self.lecteur.aller_a(s)
        self._position(s, self.lecteur.en_lecture())

    def regler_volume(self, v):
        m = self.lecteur.media()
        if m is not None:
            m.set_volume(v)

    def revenir(self):
        self.page.revenir_au_detail(self)

    def passer_en_son(self):
        """Fenêtre fermée : la piste en cours passe de la vidéo à son son seul (le MP3 du doublage, le son d'origine
        extrait, le fichier du beat ou d'une voix seule), à la même position, pour ne pas décoder d'image pour rien."""
        cle = self.lecteur.cle
        if self._oubliee or self.forme != "video" or cle not in self.pistes_dispo or cle in self.lecteur.sans_image:
            return
        try:
            son = regles.source_son(cle, pistes.choisir_pistes(self.res, "mp3", self.source_choisie()),
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
        if self.vue_paroles is not None:
            self.vue_paroles.placer(t, lecture)
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
        """Le son analysé pour une piste : le doublage (MP3 de préférence), le son d'origine extrait (même temps que
        le doublage), le beat, ma voix seule une fois prête, la voix d'origine."""
        r = self.res
        if cle == "original":
            return r.original_wav
        if cle == "doublage_at":
            return r.mp3_autotune or r.video_autotune
        if cle not in self.pistes_dispo or cle == "doublage":
            return r.mp3 or r.video
        p = self.pistes_dispo.get(cle)
        return (p.fichier or p.video) if p else None     # le son de la copie vidéo si la voix seule a quitté le cache

    def _maj_paroles(self):
        """Paroles allumées ou éteintes (menu « œil ») : sous le lecteur, chargées une fois."""
        voulu = bool(self.fenetre.prefs.get("paroles"))
        if voulu and self.vue_paroles is None:
            # position du lecteur en temps du son doublé : voir voixla.paroles.ecart_lecture
            w = VueParoles(sur_clic=self._clic_frise, langue=self.fenetre.prefs.get("paroles_langue") or "deux",
                           sur_langue=self._langue_paroles, debut_son=pistes.decalage_son(self.res.source))
            w.suivre(self._fournisseur)
            self.corps.insert_child_after(w, self.lecteur)
            self.vue_paroles = w
            r = self.res

            def lu(p, message):
                if p is not None:
                    w.definir(p)
                    w.placer(self.lecteur.position_ref())
                elif message == "attente":
                    w.dire("Lecture des sous-titres de la vidéo doublée (sous nice)…")
                else:
                    w.dire(f"Pas de paroles : {message}" if message else "Pas de paroles")
            analyses().paroles(r.dossier_voix, r.video, lu)
        elif not voulu and self.vue_paroles is not None:
            self.corps.remove(self.vue_paroles)
            self.vue_paroles = None

    def _langue_paroles(self, valeur):
        self.fenetre.prefs = chemins.modifier_preference("paroles_langue", valeur)

    def _maj_visualisations(self):
        self._maj_paroles()
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
            if cle in VOIX_SEULES and p.a_preparer:
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
        if cle in VOIX_SEULES and p.fichier is None:
            analyses().exporter(self.res.dossier_voix, sortie, fini, self.res.duree_s, voix=True,
                                autotune=cle == "ma_voix_at")
        else:
            analyses().exporter(p.fichier, sortie, fini, self.res.duree_s)

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
        attente = rapports.attente_gpu_probable(self.res.rapport)
        if attente:
            g.set_description(f"Dont environ {formats.duree(attente)} d’attente d’une place GPU ou de démarrage.")
        tl = (self.res.rapport or {}).get("temps_local") or {}
        if (tl.get("videos_dans_la_session") or 1) > 1:
            g.set_description(f"Session Kaggle partagée par {tl['videos_dans_la_session']} vidéos.")
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
        « piste-<clé> » (beat, ma_voix…), « lire » (lecture muette)."""
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
        if action == "bas":
            def descendre():
                a = self.defilement.get_vadjustment()
                a.set_value(a.get_upper() - a.get_page_size())
                return False
            GLib.timeout_add(2500, descendre)
        if action.startswith("aller-"):
            t = float(action.split("-", 1)[1])
            GLib.timeout_add(1500, lambda: (self.lecteur.aller_a(t), self._position(t, False), False)[2])
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
                self.l_remix.set_subtitle(f"Remixé : voix {formats.db(voix_db)}"
                                          + (f", reverb {formats.db(reverb)}" if reverb is not None else ""))
                self.fenetre.toast("Remix fini")
                frais = rapports.lire_dossier(r.dossier, {v["id"]: v["nom"] for v in self.fenetre.voix},
                                              r.groupe, self.fenetre.voix_doubler)
                self.res = next((x for x in frais if x.voix == r.voix), r)
            else:
                lignes = [l for l in sortie.strip().splitlines() if l.strip()]
                self.l_remix.set_subtitle(commun.echapper(lignes[-1] if lignes else "échec"))
                commun.informer(self.fenetre, "Le remix a échoué", "\n".join(lignes[-12:]) or "sans message")
            self.changer_forme(self.forme)
            self.lecteur.aller_a(pos)
        spec = moteur.spec_de_rapport(r.voix, r.rapport)   # « homme:a,femme:b » pour le dossier « a+b »
        commun.en_fil(lambda: moteur.remixer(r.video_source, spec, r.dossier, donnees, voix_db, reverb, v1), fini)

    def supprimer(self):
        """Bouton corbeille du détail : la même mise à la corbeille que la liste (PageResultats.mettre_a_la_corbeille),
        puis retour à la liste."""
        self.page.mettre_a_la_corbeille([self.res], apres=lambda ok: ok and self.dans_la_pile() and
                                        self.fenetre.nav.pop())

    def fermer_pour_corbeille(self):
        """Ses fichiers vont à la corbeille : lecture arrêtée, flux fermés (copies vidéo en cache comprises) ; la page
        n'est plus la lecture en cours, et elle est oubliée si elle n'est plus affichée."""
        self.lecteur.liberer()
        if not self.dans_la_pile():
            self.oublier()


class PageResultats(commun.Page):
    """La liste des doublages. Chaque ligne a un menu « ⋮ » (ouvrir le dossier, mettre à la corbeille) ; le mode
    « Sélectionner » coche plusieurs lignes et les met à la corbeille d'un coup, avec une seule confirmation."""

    def __init__(self, fenetre):
        super().__init__(fenetre, "Résultats", "resultats")
        self.resultats = []
        self.remix_actif = False
        self.a_ouvrir = None
        self.en_selection = False
        self.selection = set()        # clés (Resultat.cle) des lignes cochées
        self.cases = {}               # clé → case à cocher de sa ligne
        actions = Gio.SimpleActionGroup()
        for nom, rappel in (("dossier", self._ouvrir_dossier), ("corbeille", self._corbeille_ligne)):
            a = Gio.SimpleAction.new(nom, GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, v, r=rappel: r(v.get_string()))
            actions.add_action(a)
        self.insert_action_group("resultats", actions)
        self.recherche = Gtk.SearchEntry(placeholder_text="Chercher un titre…", hexpand=True)
        self.recherche.connect("search-changed", lambda *_: self.rendre())
        barre = Gtk.SearchBar(child=Adw.Clamp(maximum_size=600, child=self.recherche), show_close_button=False)
        barre.connect_entry(self.recherche)
        self.vue.add_top_bar(barre)
        b_cherche = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Chercher")
        b_cherche.bind_property("active", barre, "search-mode-enabled", 1 | 2)
        self.entete.pack_end(b_cherche)
        # mode sélection (comme Fichiers ou Photos de GNOME) : cases à cocher, barre d'actions en bas
        self.b_selection = Gtk.ToggleButton(icon_name="selection-mode-symbolic", tooltip_text="Sélectionner")
        self.b_selection.connect("toggled", lambda b: self.selectionner(b.get_active()))
        self.entete.pack_end(self.b_selection)
        self.entete.pack_end(commun.bouton(icone="view-refresh-symbolic", infobulle="Actualiser (F5)",
                                           clic=self.charger))
        self.entete.pack_start(commun.bouton(icone="folder-open-symbolic", infobulle="Ouvrir le dossier des sorties",
                                             clic=lambda: commun.ouvrir_dossier(
                                                 fenetre, chemins.dossier_sorties(fenetre.donnees))))
        self.pile = Gtk.Stack()
        self.liste = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.liste.connect("row-activated", lambda _l, r: self._ligne_activee(r))
        b = commun.boite(espace=12, marges=18)
        self.compte = commun.legende("")
        b.append(self.compte)
        b.append(self.liste)
        self.pile.add_named(commun.defile(b, 860), "liste")
        self.pile.add_named(commun.page_vide("folder-videos-symbolic", "Aucun doublage",
                                            "Les doublages finis apparaissent ici, avec un lecteur, la frise "
                                            "d’octave et le remix rapide.",
                                            commun.bouton("Doubler une vidéo", styles=("pill", "suggested-action"),
                                                          clic=lambda: fenetre.aller("doubler"))), "vide")
        self.pile.add_named(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                        width_request=32, height_request=32), "attente")
        self.pile.set_visible_child_name("attente")
        self.contenu(self.pile)
        # barre d'actions de la sélection : tout y rétrécit (fenêtre étroite de 360 px)
        self.barre_selection = Gtk.ActionBar()
        self.l_selection = Gtk.Label(css_classes=["caption", "chiffres"], ellipsize=Pango.EllipsizeMode.END)
        self.barre_selection.pack_start(commun.bouton(icone="edit-select-all-symbolic", infobulle="Tout sélectionner",
                                                      clic=self._tout_selectionner))
        self.barre_selection.set_center_widget(self.l_selection)
        self.b_corbeille = Gtk.Button(child=Adw.ButtonContent(label="Mettre à la corbeille…",
                                                              icon_name="user-trash-symbolic", can_shrink=True),
                                      css_classes=["destructive-action"])
        self.b_corbeille.connect("clicked", lambda *_: self._corbeille_selection())
        self.barre_selection.pack_end(self.b_corbeille)
        self.vue.add_bottom_bar(self.barre_selection)
        self.vue.set_reveal_bottom_bars(False)
        raccourcis = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        raccourcis.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                                 Gtk.CallbackAction.new(lambda *_: self._echap())))
        self.add_controller(raccourcis)
        fenetre.ecouter("resultats", self.charger)
        fenetre.ecouter("donnees", self.charger)
        self._charge = False

    def montrer(self):
        super().montrer()
        if not self._charge:
            self.charger()

    def cacher(self):
        super().cacher()
        self.b_selection.set_active(False)

    def actualiser(self):
        self.charger()

    def charger(self, *_):
        sorties = chemins.dossier_sorties(self.fenetre.donnees)
        noms = {v["id"]: v["nom"] for v in self.fenetre.voix}
        defaut = self.fenetre.voix_doubler

        def fini(liste):
            self._charge = True
            self.resultats = liste
            self.rendre()
            if self.a_ouvrir:
                dossier, voix = self.a_ouvrir
                self.a_ouvrir = None
                self.ouvrir(dossier, voix)
        commun.en_fil(lambda: rapports.lister_resultats(sorties, noms, voix_defaut=defaut), fini)

    def rendre(self):
        commun.vider(self.liste)
        self.cases = {}
        filtre = self.recherche.get_text().strip().lower()
        vus = [r for r in self.resultats if not filtre or filtre in r.titre.lower() or filtre in (r.groupe or "").lower()]
        self.selection &= {r.cle for r in self.resultats}
        self._maj_selection()
        if not self.resultats:
            self.pile.set_visible_child_name("vide")
            return
        self.pile.set_visible_child_name("liste")
        complets = sum(1 for r in self.resultats if r.complet)
        self.compte.set_label(f"{formats.pluriel(complets, 'doublage')}"
                              + (f", {formats.pluriel(len(self.resultats) - complets, 'inachevé')}"
                                 if len(self.resultats) > complets else "")
                              + f" dans {chemins.dossier_sorties(self.fenetre.donnees)}")
        for r in vus:
            ligne = Adw.ActionRow(title=commun.echapper(r.titre_court), subtitle=commun.echapper(sous_titre(r)),
                                  activatable=True, title_lines=2)
            ligne.res = r
            if self.en_selection:
                case = Gtk.CheckButton(active=r.cle in self.selection, valign=Gtk.Align.CENTER,
                                       css_classes=["selection-mode"])
                case.update_property([Gtk.AccessibleProperty.LABEL], [f"Sélectionner {r.titre_court}"])
                case.connect("toggled", lambda c, k=r.cle: self._cocher(k, c.get_active()))
                ligne.add_prefix(case)
                ligne.set_activatable_widget(case)
                self.cases[r.cle] = case
            ligne.add_prefix(Gtk.Image(icon_name="video-x-generic-symbolic" if r.video else
                                       "audio-x-generic-symbolic" if r.mp3 else "dialog-warning-symbolic"))
            if not r.complet:
                ligne.add_suffix(commun.badge("inachevé", "alerte"))
            if r.repli:
                ligne.add_suffix(commun.badge("repli Seed-VC", "alerte"))
            if r.moteur == "yingmusic":
                ligne.add_suffix(commun.badge("YingMusic", "neutre"))
            if r.mode:
                ligne.add_suffix(commun.badge(r.mode, "neutre"))
            if not self.en_selection:
                # actions de la ligne dans un menu « ⋮ », comme sur les lignes de la page Voix : rien de destructif à
                # un clic, à côté du chevron qui ouvre le détail
                menu = Gio.Menu()
                cible = GLib.Variant.new_string(r.cle)
                for libelle, action in (("Ouvrir le dossier", "resultats.dossier"),
                                        ("Mettre à la corbeille…", "resultats.corbeille")):
                    item = Gio.MenuItem.new(libelle, None)
                    item.set_action_and_target_value(action, cible)
                    menu.append_item(item)
                ligne.add_suffix(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, css_classes=["flat"],
                                                valign=Gtk.Align.CENTER, tooltip_text="Actions"))
                ligne.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            self.liste.append(ligne)

    def proteger_caches(self):
        """Les copies vidéo et la voix combinée des détails ouverts ou en lecture : jamais purgées (chemins.proteger,
        lu par la purge des préparations, ici ou dans le préparateur en tâche de fond)."""
        pile = self.fenetre.nav.get_navigation_stack()
        details = [pile.get_item(i) for i in range(pile.get_n_items())] + [lectures().session]
        fichiers = [str(f) for d in dict.fromkeys(d for d in details if isinstance(d, PageDetail))
                    for f in d.fichiers_caches()]
        commun.en_fil(lambda: chemins.proteger(fichiers))

    # --- mise à la corbeille -------------------------------------------------------------------------------------

    def _resultat(self, cle):
        return next((r for r in self.resultats if r.cle == cle), None)

    def _ouvrir_dossier(self, cle):
        r = self._resultat(cle)
        if r is not None:
            commun.ouvrir_dossier(self.fenetre, r.dossier)

    def _corbeille_ligne(self, cle):
        r = self._resultat(cle)
        if r is not None:
            self.mettre_a_la_corbeille([r])

    def mettre_a_la_corbeille(self, resultats, apres=None):
        """La seule mise à la corbeille de doublages (détail, menu d'une ligne, sélection) : confirmation, puis la
        lecture de ces doublages arrêtée et leurs flux fermés, puis l'envoi à la corbeille, un toast et la liste relue.
        apres(ok) à la fin."""
        liste = corbeille.plans(resultats)
        if not liste:
            return
        pris = corbeille.occupes(liste, self.fenetre.file.travaux())
        if pris:
            self.fenetre.toast(f"Un doublage en cours écrit dans « {pris[0]} » : corbeille impossible pour l’instant")
            return
        titre, corps, bouton = corbeille.confirmation(liste)
        n = sum(max(1, len(p.voix)) for p in liste)

        def faire():
            self._fermer_lectures(liste)

            def fini(ok, message, faits):
                if ok:
                    self.fenetre.toast("Doublage mis à la corbeille" if n == 1 else
                                       f"{n} doublages mis à la corbeille")
                else:
                    self.fenetre.toast(f"Corbeille impossible : {message}" +
                                       (f" ({faits} sur {len(liste)} déjà à la corbeille)" if faits else ""))
                self.b_selection.set_active(False)
                self.charger()
                if apres:
                    apres(ok)
            corbeille.enchainer(liste, commun.corbeille, fini)
        commun.confirmer(self.fenetre, titre, corps, bouton, faire)

    def _fermer_lectures(self, liste):
        """Avant la corbeille : chaque détail ouvert sur ces fichiers (affiché, ou qui joue en arrière-plan) arrête
        sa lecture et ferme ses flux, pour que rien ne lise un fichier qui part."""
        pile = self.fenetre.nav.get_navigation_stack()
        details = [pile.get_item(i) for i in range(pile.get_n_items())] + [lectures().session]
        for d in dict.fromkeys(d for d in details if isinstance(d, PageDetail)):
            if corbeille.concerne(liste, d.res.dossier, d.res.voix):
                d.fermer_pour_corbeille()

    # --- mode sélection ------------------------------------------------------------------------------------------

    def selectionner(self, oui):
        if oui == self.en_selection:
            return
        self.en_selection = oui
        self.selection = set()
        if self.b_selection.get_active() != oui:
            self.b_selection.set_active(oui)
        self.vue.set_reveal_bottom_bars(oui)
        self.rendre()

    def _echap(self):
        if not self.en_selection:
            return False
        self.selectionner(False)
        return True

    def _ligne_activee(self, ligne):
        if self.en_selection:
            return            # la case de la ligne s'est cochée (widget d'activation)
        self.ouvrir_detail(ligne.res)

    def _cocher(self, cle, oui):
        (self.selection.add if oui else self.selection.discard)(cle)
        self._maj_selection()

    def _tout_selectionner(self):
        for case in self.cases.values():
            case.set_active(True)

    def _maj_selection(self):
        n = len(self.selection)
        self.l_selection.set_label(formats.pluriel(n, "doublage sélectionné", "doublages sélectionnés") if n else
                                   "Cochez des doublages")
        self.b_corbeille.set_sensitive(n > 0)

    def _corbeille_selection(self):
        choisis = [r for r in self.resultats if r.cle in self.selection]
        if choisis:
            self.mettre_a_la_corbeille(choisis)

    def ouvrir_detail(self, res):
        """Le détail d'un résultat ; celui qui joue (ou en pause) en arrière-plan est retrouvé tel quel."""
        s = lectures().session
        if isinstance(s, PageDetail) and s.page is self and not s._oubliee and s.meme_resultat(res):
            self.revenir_au_detail(s)
            return
        self.fenetre.pousser(PageDetail(self, res))

    def revenir_au_detail(self, detail):
        """Remontre une page de détail gardée (barre « en cours de lecture », clic dans la liste)."""
        if detail.dans_la_pile():
            self.fenetre.split.set_show_content(True)
        elif self.fenetre.nav.get_visible_page() is self:
            self.fenetre.pousser(detail)
        else:
            self.fenetre.montrer_pile("resultats", detail)

    def ouvrir(self, dossier, voix=None):
        """Ouvre le détail d'un dossier de sortie (depuis la page Travaux)."""
        dossier = Path(dossier)
        r = next((x for x in self.resultats if x.dossier == dossier and (voix is None or x.voix == voix)), None)
        if r is None:
            self.a_ouvrir = (dossier, voix)
            self.charger()
            return
        self.ouvrir_detail(r)

    def scenario(self, action):
        """Essais : « detail », « detail-<mot>[:actions du détail] », « selection » (mode sélection, deux lignes
        cochées), « corbeille » (confirmation pour la première ligne, sans rien envoyer), « corbeille-selection »."""
        if action in ("selection", "corbeille-selection"):
            def cocher():
                self.selectionner(True)
                for case in list(self.cases.values())[:2]:
                    case.set_active(True)
                if action == "corbeille-selection":
                    self._corbeille_selection()
                return False
            GLib.timeout_add(900, cocher)
        elif action == "corbeille":
            GLib.timeout_add(900, lambda: (self.resultats and self.mettre_a_la_corbeille([self.resultats[0]]),
                                           False)[1])
        if action == "detail":
            def ouvrir():
                if self.resultats:
                    premier = next((r for r in self.resultats if r.complet), self.resultats[0])
                    self.ouvrir_detail(premier)
                return False
            GLib.timeout_add(700, ouvrir)
        elif action.startswith("detail-"):
            mot, _, suite = action.split("-", 1)[1].lower().partition(":")

            def ouvrir2():
                r = next((r for r in self.resultats if mot in r.titre.lower()), None)
                if r:
                    d = PageDetail(self, r)
                    self.fenetre.pousser(d)
                    for morceau in suite.split("+") if suite else []:
                        d.scenario(morceau)
                return False
            GLib.timeout_add(700, ouvrir2)
