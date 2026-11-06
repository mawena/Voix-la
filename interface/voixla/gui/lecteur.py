"""Lecteur vidéo et audio (Gtk.MediaFile + GStreamer), avec bascule entre sources à la même position.

L'image est un Gtk.Picture qui affiche le flux (un Gtk.MediaStream est un GdkPaintable) ; les commandes sont un
Gtk.MediaControls toujours visible, pour la vidéo comme pour le son. Une source sans image (le beat ou une voix seule,
à côté d'une vidéo, tant que sa copie vidéo se prépare) se lit sous l'image de la vidéo arrêtée, avec un bandeau qui
le dit : pas de second décodage, pas d'écran qui disparaît et fait sauter la page. Quand sa copie vidéo est prête, elle
la remplace à la même position (ajouter). Les sources « éphémères » (ces copies) sont fermées dès qu'on les quitte :
seuls le doublage et l'original restent ouverts, pour la touche B.

Pistes audio d'une même vidéo (pistes_audio, doubler ≥ 0.6.0 : le doublage, le beat, les voix seules… dans la vidéo
doublée) : un seul flux pour toutes, et la bascule change de piste audio dans ce flux (gui/pistes_gst.py, current-audio
de playbin) au lieu d'ouvrir un autre fichier. Même position, l'image continue, pas de seconde ouverture.

Quitter la page n'arrête pas la lecture : seul le suivi de la position (et les visualisations qu'il anime) s'arrête
tant que le lecteur est caché, et reprend au retour. Une seule lecture dans toute l'application : chaque flux est ouvert
par media(), qui le confie au coordinateur (gui/lecture.py) avec son propriétaire ; lancer une lecture ailleurs coupe
celle-ci, changer de source dans ce lecteur ne coupe rien. Fenêtre fermée, passer_en_son() remplace la vidéo par une
source sans image de même contenu, à la même position et sans blanc ; revenir_en_video() la remet.

Lecteur complet (page d'un résultat) : la vitesse (0,5× à 2×, hauteur gardée par scaletempo, gui/pistes_gst.py), le
volume et le muet valent pour tous les flux, présents et à venir (autre piste, son seul fenêtre fermée) ; la boucle A-B
est en temps de référence (celui du son doublé), elle survit donc aux bascules de piste. Elle est surveillée par son
propre minuteur, pendant la lecture seulement, que la page soit affichée ou non (plein écran, autre page, fenêtre
fermée).
"""

from gi.repository import GLib, Gtk

from .. import formats
from .. import lecture as regles
from ..lecture import Reprise
from . import pistes_gst
from .lecture import lectures

BOUCLE_MS = 40               # surveillance de la boucle A-B pendant la lecture (dépassement de B : 40 ms au plus)


def media(chemin, proprietaire):
    """Un flux (Gtk.MediaFile) suivi par le coordinateur : quand il joue, la lecture d'un autre propriétaire est
    coupée."""
    m = Gtk.MediaFile.new_for_filename(str(chemin))
    m.set_loop(False)
    return lectures().suivre(m, proprietaire)


def quand_pret(m, rappel):
    """rappel() dès que le flux est prêt (durée connue, déplacement possible)."""
    if m.is_prepared():
        rappel()
        return
    ident = []

    def pret(*_):
        if m.is_prepared():
            m.disconnect(ident[0])
            rappel()
    ident.append(m.connect("notify::prepared", pret))


class Lecteur(Gtk.Box):
    """Un lecteur ; sources = {clé: (chemin, décalage_s, libellé)} ; bascule entre sources à la même position
    (une source de décalage d est lue à position de référence + d). Dans la page d'un résultat, la position de
    référence est le temps du son doublé, quelle que soit la piste : celle d'une vidéo a pour décalage le début du son
    dans la vidéo doublée (voixla.pistes.source_lecteur).
    proprietaire : ce qui lit, pour le coordinateur (la page d'un résultat) ; à défaut, le lecteur lui-même, une
    préécoute (durable faux) qu'une autre lecture met en pause."""

    durable = False

    def __init__(self, hauteur=280, sur_position=None, proprietaire=None, commandes=True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.hauteur = hauteur
        self.ecran = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True,
                                 css_classes=["ecran-media"])
        self.ecran.set_size_request(-1, hauteur)
        # bandeau sur l'image arrêtée quand la source en cours n'a pas d'image
        self.bandeau = Gtk.Label(css_classes=["osd", "bandeau-ecran", "caption"], halign=Gtk.Align.START,
                                 valign=Gtk.Align.END, margin_start=10, margin_bottom=10, visible=False)
        self.superpose = Gtk.Overlay(child=self.ecran)
        self.superpose.add_overlay(self.bandeau)
        # commandes : Gtk.MediaControls par défaut ; commandes=False, la page pose les siennes (gui/commandes.py)
        self.commandes = Gtk.MediaControls(hexpand=True) if commandes else None
        self.append(self.superpose)
        if self.commandes is not None:
            self.append(self.commandes)
        self.vitesse = 1.0        # vitesse de tous les flux (scaletempo : la hauteur est gardée)
        self.volume = 1.0         # volume de tous les flux
        self.coupe = False        # son coupé par l'utilisateur (touche M), pour tous les flux
        self.boucle = ()          # boucle A-B en temps de référence : (a, None), (a, b) ou ()
        self.sur_etat = None      # rappel() : lecture ou pause du flux en cours, fin, vitesse (commandes, page)
        self._minuteur_boucle = None
        self.sources = {}
        self.flux = {}
        self.cle = None
        self.audio_seul = False
        self.sans_image = set()
        self.ephemeres = set()    # sources fermées dès qu'on les quitte (copies vidéo des pistes séparées)
        self.notes = {}           # clé → texte du bandeau d'une source sans image
        self.pistes_audio = {}    # clé → (vidéo, index de sa piste audio) : pistes d'une vidéo qui en a plusieurs
        self.sur_echec_piste = None   # rappel : une piste audio ne peut pas être choisie (playbin introuvable)
        self.muet = False         # essais : tous les flux, même ceux ouverts plus tard, restent muets
        self.sur_position = sur_position
        self.proprietaire = proprietaire
        self._minuteur = None
        self._fin_extrait = None
        self._attente = None      # [flux, position voulue, placé, lire] tant qu'un flux activé n'est pas prêt et placé
        self._reprise = None      # fenêtre fermée : la source vidéo à remettre (voixla.lecture.Reprise)
        # pas de pause quand on quitte la page : la lecture continue ; le suivi s'arrête caché et reprend au retour
        self.connect("map", lambda *_: self.media() and self._suivre())

    def _proprio(self):
        return self.proprietaire if self.proprietaire is not None else self

    def couper(self):
        """Une autre lecture commence (coordinateur) : pause."""
        self.pause()

    # --- sources -------------------------------------------------------------------------------------------------

    def definir(self, sources, cle=None, audio_seul=False, sans_image=(), ephemeres=(), notes=None, pistes_audio=None):
        """Remplace les sources ; garde la position si c'est le même contenu sous une autre forme. sans_image : les
        clés des sources sans image (lues sous l'image arrêtée d'une source qui en a une) ; ephemeres : celles qu'on
        ferme dès qu'on les quitte ; notes : le texte du bandeau des sources sans image ; pistes_audio : {clé: index}
        des sources lues par leur piste audio dans une vidéo à plusieurs pistes (voixla.pistes.pistes_audio_lecteur)."""
        self._tout_fermer()
        self.sources = {k: v for k, v in sources.items() if v and v[0]}
        self.audio_seul = audio_seul
        self.sans_image = set(sans_image)
        self.ephemeres = set(ephemeres)
        self.notes = dict(notes or {})
        # clé → (la vidéo à plusieurs pistes, index de la piste) : une source remplacée (son seul) n'en a plus
        self.pistes_audio = {k: (str(self.sources[k][0]), int(v)) for k, v in (pistes_audio or {}).items()
                             if k in self.sources and v is not None}
        self.superpose.set_visible(not audio_seul)
        cle = cle if cle in self.sources else next(iter(self.sources), None)
        if cle:
            self._activer(cle, 0.0, False)

    def ajouter(self, cle, source, sans_image=False, ephemere=False):
        """Ajoute ou remplace une source (une piste préparée après coup). Si c'est la source en cours (le beat lu sous
        l'image arrêtée, dont la copie vidéo vient d'être prête), on passe à la nouvelle à la même position, en lecture
        si on lisait. Fenêtre fermée (son seul), une nouvelle vidéo de la piste en cours attend le retour de la
        fenêtre."""
        if not source or not source[0]:
            return
        if self._reprise is not None and cle == self._reprise.cle:
            self._reprise = self._reprise.mise_a_jour(source, sans_image, ephemere)
            return
        self._remplacer(cle, source, sans_image, ephemere)

    def _remplacer(self, cle, source, sans_image=False, ephemere=False, flux=None):
        """ajouter, sans le cas du son seul ; flux : un flux déjà ouvert (et prêt) pour cette source. Un flux que
        d'autres pistes audio de la même vidéo lisent encore reste ouvert (en pause s'il était en cours) ; un nouveau
        flux de la vidéo devient celui de toutes ses pistes (_unifier)."""
        courante = cle == self.cle and cle in self.flux
        pos = self.position_ref() if courante else 0.0
        lisait = bool(courante and self.en_lecture())
        ancien = self.flux.pop(cle, None)
        if ancien is not None and (courante or not self._utilise(ancien)):
            ancien.pause()
        self.sources[cle] = source
        if flux is not None:
            self.flux[cle] = flux
            self._unifier(cle)
        (self.sans_image.add if sans_image else self.sans_image.discard)(cle)
        (self.ephemeres.add if ephemere else self.ephemeres.discard)(cle)
        if courante:
            self._activer(cle, pos, lisait)
        if ancien is not None and not self._utilise(ancien):
            self._fermer(ancien)

    def _piste_audio(self, cle):
        """L'index de la piste audio que lit la source cle dans sa vidéo à plusieurs pistes, sinon None (y compris
        quand la source a été remplacée par son son seul, fenêtre fermée)."""
        v = self.pistes_audio.get(cle)
        return v[1] if v is not None and cle in self.sources and str(self.sources[cle][0]) == v[0] else None

    def _meme_flux(self, a, b):
        """Vrai si les sources a et b sont deux pistes audio d'une même vidéo : un seul flux pour les deux."""
        return (self._piste_audio(a) is not None and self._piste_audio(b) is not None
                and self.pistes_audio[a][0] == self.pistes_audio[b][0])

    def _utilise(self, m):
        return any(v is m for v in self.flux.values())

    def _unifier(self, cle):
        """Les autres pistes audio de la vidéo de cle lisent désormais son flux ; l'ancien flux est fermé."""
        m = self.flux.get(cle)
        if m is None or self._piste_audio(cle) is None:
            return
        for k in list(self.flux):
            vieux = self.flux[k]
            if k != cle and vieux is not m and self._meme_flux(k, cle):
                self.flux[k] = m
                if not self._utilise(vieux):
                    self._fermer(vieux)

    def _choisir_son(self, m):
        """m (prêt) lit la piste audio de la source en cours, si elle en a une (vidéo à plusieurs pistes) : tout de
        suite s'il joue, sinon dès la reprise de la lecture (gui/pistes_gst.py). Si le playbin du flux est introuvable
        sur ce poste, sur_echec_piste() : la page revient aux copies vidéo."""
        index = self._piste_audio(self.cle)
        if index is None or self.flux.get(self.cle) is not m:
            return
        if not pistes_gst.choisir(m, index) and not pistes_gst.possible() and self.sur_echec_piste is not None:
            GLib.idle_add(lambda: (self.sur_echec_piste(), False)[1])

    def annoter(self, cle, texte):
        """Texte du bandeau d'une source sans image (« préparation de la vidéo… »), à jour si elle est en cours."""
        self.notes[cle] = texte
        if cle == self.cle and cle in self.sans_image and not self.audio_seul:
            self.bandeau.set_label(self._texte_bandeau(cle))

    def _texte_bandeau(self, cle):
        return f"{self.sources[cle][2]} · {self.notes.get(cle) or 'son seul, image arrêtée'}"

    @staticmethod
    def _fermer(m):
        if m is None:
            return
        try:
            m.pause()
            m.clear()
        except Exception:  # noqa: BLE001
            pass

    def _ouvrir(self, chemin):
        m = media(chemin, self._proprio())
        if self.muet or self.coupe:
            m.set_muted(True)
        if self.volume != 1.0:
            m.set_volume(self.volume)
        m.connect("notify::playing", lambda flux, _p: self._etat_flux(flux))
        m.connect("notify::ended", lambda flux, _p: self._fin_flux(flux))
        return m

    def _etat_flux(self, m):
        """Lecture ou pause d'un flux : la boucle A-B se surveille pendant la lecture ; les commandes suivent."""
        if m is not self.media():
            return
        self._surveiller_boucle()
        if self.sur_etat is not None:
            self.sur_etat()

    def _fin_flux(self, m):
        """Fin du flux en cours : avec une boucle A-B qui finit au bout du fichier, on repart de A."""
        if m is not self.media() or not m.get_ended():
            return
        if regles.boucle_complete(self.boucle):
            self.aller_a(self.boucle[0], lire=True)
        if self.sur_etat is not None:
            self.sur_etat()

    def _flux(self, cle):
        if cle not in self.flux:
            frere = next((k for k in self.flux if k != cle and self._meme_flux(k, cle)), None)
            self.flux[cle] = self.flux[frere] if frere is not None else self._ouvrir(self.sources[cle][0])
        return self.flux[cle]

    def rendre_muet(self):
        """Essais : coupe le son de tous les flux, présents et à venir."""
        self.muet = True
        for m in self.flux.values():
            m.set_muted(True)

    def _image_pour(self, cle, position_ref):
        """Ce que montre l'écran pendant une source sans image : l'image en cours, arrêtée ; à défaut, celle d'une
        source qui a une image, placée à la même position et laissée en pause."""
        courante = self.ecran.get_paintable()
        if courante is not None:
            self.ecran.set_paintable(courante.get_current_image())
            return
        autre = next((k for k in self.sources if k not in self.sans_image), None)
        if autre is None or self._reprise is not None:
            return                # fenêtre fermée : pas de vidéo ouverte pour une image que personne ne voit
        m = self._flux(autre)
        self.ecran.set_paintable(m)
        decalage = self.sources[autre][1] or 0.0
        quand_pret(m, lambda: m.seek(int(max(0.0, position_ref + decalage) * 1_000_000)))

    def _activer(self, cle, position_ref, lire):
        m = self._flux(cle)
        self.cle = cle
        if self.audio_seul:
            self.ecran.set_paintable(None)
            self.bandeau.set_visible(False)
        elif cle in self.sans_image:
            self._image_pour(cle, position_ref)
            self.bandeau.set_label(self._texte_bandeau(cle))
            self.bandeau.set_visible(True)
        else:
            courante = self.ecran.get_paintable()
            if m.is_prepared() or courante is None:
                self.ecran.set_paintable(m)
            else:
                # l'image en cours reste (figée) jusqu'à ce que la nouvelle vidéo soit prête : pas d'écran vide
                self.ecran.set_paintable(courante.get_current_image())
                quand_pret(m, lambda: self.flux.get(self.cle) is m and self.ecran.set_paintable(m))
            self.bandeau.set_visible(False)
        if self.commandes is not None:
            self.commandes.set_media_stream(m)
        decalage = self.sources[cle][1] or 0.0
        # tant que le flux n'est pas prêt et placé, sa position est celle qu'on lui a demandée : une nouvelle bascule
        # (la copie vidéo prête juste après) repart de là, pas de 0 ; et il « lit » déjà si on l'a demandé
        self._attente = [m, position_ref, False, lire]

        def placer():
            self._choisir_son(m)      # la piste audio de la source en cours (celle-ci, ou une autre du même flux)
            if self.vitesse != 1.0:   # un flux neuf lit à 1 : la vitesse avant le déplacement, qui la garde
                pistes_gst.regler_vitesse(m, self.vitesse)
            if position_ref or decalage:
                m.seek(int(max(0.0, position_ref + decalage) * 1_000_000))
            a = self._attente
            # une pause demandée pendant la préparation l'emporte
            if (a[3] if a is not None and a[0] is m else lire):
                m.play()
            if a is not None and a[0] is m:
                a[2] = True
        quand_pret(m, placer)
        self._suivre()

    def basculer(self, cle):
        """Passe à une autre source, à la même position (et en lecture si on lisait). Deux pistes audio d'une même
        vidéo : on change seulement de piste audio dans le flux en cours, sans pause ni déplacement."""
        if cle == self.cle or cle not in self.sources:
            return
        m = self.media()
        if m is not None and self._reprise is None and self._meme_flux(self.cle, cle):
            self.flux[cle] = m
            self.cle = cle
            if m.is_prepared():
                self._choisir_son(m)
            # sinon, placer (_activer) la choisira dès que le flux sera prêt
            self.bandeau.set_visible(False)
            self._suivre()
            return
        reprise, self._reprise = self._reprise, None
        ancien, ancienne_cle = m, self.cle
        pos = self.position_ref()
        lisait = self.en_lecture()
        if ancien:
            ancien.pause()
        self._activer(cle, pos, lisait)
        if ancienne_cle in self.ephemeres or (reprise is not None and reprise.cle == ancienne_cle):
            vieux = self.flux.pop(ancienne_cle, None)
            if vieux is not None and not self._utilise(vieux):
                self._fermer(vieux)
        if reprise is not None:
            self._remettre(reprise)

    def media(self):
        return self.flux.get(self.cle) if self.cle else None

    def en_lecture(self):
        """Vrai pendant la lecture, et pendant une bascule demandée en lecture dont le flux se prépare encore."""
        m = self.media()
        if not m:
            return False
        a = self._attente
        if a is not None and a[0] is m and not a[2]:
            return bool(a[3])
        return bool(m.get_playing())

    # --- fenêtre fermée : le son seul, puis retour à la vidéo ------------------------------------------------------

    def passer_en_son(self, son):
        """Remplace la source en cours par son (chemin, décalage, libellé) : même contenu sans image, à la même
        position, sans blanc (le son se prépare pendant que la vidéo joue encore). Le décodage vidéo s'arrête."""
        cle = self.cle
        if not cle or self._reprise is not None or cle not in self.sources:
            return False
        self._reprise = Reprise(cle, self.sources[cle], cle in self.sans_image, cle in self.ephemeres)
        self._substituer(cle, son, sans_image=True, ephemere=True)
        return True

    def revenir_en_video(self):
        """Remet la source d'avant passer_en_son, là où en est le son, en lecture ou en pause comme lui."""
        r, self._reprise = self._reprise, None
        if r is None:
            return False
        if self.cle != r.cle:
            self.sources[r.cle] = r.source
            return False
        # une piste audio d'une vidéo dont le flux est resté ouvert (en pause, pour les autres pistes) : on y revient,
        # il a déjà la bonne piste ; un nouveau flux, lui, ne peut la prendre qu'une fois en lecture (pistes_gst)
        video = self.pistes_audio.get(r.cle)
        frere = next((self.flux[k] for k in self.flux if k != r.cle and video is not None
                      and self._piste_audio(k) is not None and self.pistes_audio[k][0] == video[0]
                      and str(r.source[0]) == video[0]), None)
        if frere is not None and frere.is_prepared():
            self._remplacer(r.cle, r.source, r.sans_image, r.ephemere, flux=frere)
            return True
        self._substituer(r.cle, r.source, r.sans_image, r.ephemere)
        return True

    def _remettre(self, r):
        """Après une bascule pendant le son seul (le flux du son est fermé) : la source vidéo reprend sa place."""
        self.sources[r.cle] = r.source
        (self.sans_image.add if r.sans_image else self.sans_image.discard)(r.cle)
        (self.ephemeres.add if r.ephemere else self.ephemeres.discard)(r.cle)

    def _substituer(self, cle, source, sans_image, ephemere):
        """Comme _remplacer, mais le nouveau flux se prépare d'abord pendant que l'ancien joue encore."""
        actuel = self.flux.get(cle)
        if actuel is None or not actuel.get_playing():
            self._remplacer(cle, source, sans_image, ephemere)
            return
        nouveau = self._ouvrir(source[0])

        def pret():
            if self.cle != cle or self.flux.get(cle) is not actuel:
                self._fermer(nouveau)      # tout a changé pendant la préparation
                return
            self._remplacer(cle, source, sans_image, ephemere, flux=nouveau)
        quand_pret(nouveau, pret)

    def liberer(self):
        """Arrête et ferme tous les flux (avant un remix qui réécrit les fichiers, ou en fermant la page) : la lecture
        quitte la barre « en cours de lecture » et MPRIS."""
        self._tout_fermer()
        lectures().terminer(self._proprio())

    def _tout_fermer(self):
        self._reprise = None
        for m in {id(v): v for v in self.flux.values()}.values():     # un flux partagé n'est fermé qu'une fois
            try:
                m.pause()
                m.clear()
            except Exception:  # noqa: BLE001
                pass
        self.flux = {}
        self.cle = None
        self._attente = None
        self.ecran.set_paintable(None)
        self.bandeau.set_visible(False)
        if self.commandes is not None:
            self.commandes.set_media_stream(None)
        self._fin_extrait = None

    # --- position ------------------------------------------------------------------------------------------------

    def position_ref(self):
        """Position de référence (position dans la source en cours − son décalage), en secondes."""
        m = self.media()
        if not m:
            return 0.0
        a = self._attente
        if a is not None and a[0] is m:
            if not a[2] or m.is_seeking():
                return a[1]
            self._attente = None
        return max(0.0, m.get_timestamp() / 1_000_000 - (self.sources[self.cle][1] or 0.0))

    def position(self):
        m = self.media()
        return m.get_timestamp() / 1_000_000 if m else 0.0

    def duree(self):
        m = self.media()
        return m.get_duration() / 1_000_000 if m and m.is_prepared() else None

    def aller_a(self, s_ref, lire=None):
        m = self.media()
        if not m:
            return
        decalage = self.sources[self.cle][1] or 0.0
        self._attente = [m, s_ref, False, bool(lire) or self.en_lecture()]

        def placer():
            m.seek(int(max(0.0, s_ref + decalage) * 1_000_000))
            if lire:
                m.play()
            if self._attente and self._attente[0] is m:
                self._attente[2] = True
        quand_pret(m, placer)

    def aller_a_absolu(self, s, lire=None):
        """Position dans le fichier en cours (sans décalage)."""
        m = self.media()
        if not m:
            return

        def placer():
            m.seek(int(max(0.0, s) * 1_000_000))
            if lire:
                m.play()
        quand_pret(m, placer)

    def lire_extrait(self, debut, fin):
        """Lit de debut à fin (secondes du fichier en cours), puis s'arrête."""
        self._fin_extrait = fin
        self.aller_a_absolu(debut, lire=True)
        self._suivre()

    def pause(self):
        m = self.media()
        if m:
            m.pause()
        a = self._attente
        if a is not None and a[0] is m:
            a[3] = False          # une bascule en cours ne relance pas la lecture

    def duree_ref(self):
        """Durée en temps de référence (celle de la source en cours moins son décalage), None tant qu'elle n'est pas
        connue."""
        d = self.duree()
        if d is None or self.cle not in self.sources:
            return None
        return max(0.0, d - (self.sources[self.cle][1] or 0.0))

    # --- lecteur complet : vitesse, volume, boucle A-B, sauts --------------------------------------------------------

    def vitesse_possible(self):
        """Vrai si le flux en cours sait changer de vitesse en gardant la hauteur (scaletempo) ; None s'il n'est pas
        encore prêt."""
        m = self.media()
        if m is None or not m.is_prepared():
            return None
        return pistes_gst.garde_la_hauteur(m)

    def regler_vitesse(self, v):
        """La vitesse de tous les flux (bornée de 0,5 à 2) ; un flux pas encore prêt la prendra en se plaçant. Faux si
        le flux en cours ne peut pas la prendre sans changer la hauteur (elle reste alors à 1)."""
        v = regles.borner_vitesse(v)
        m = self.media()
        if m is not None and m.is_prepared():
            if not pistes_gst.regler_vitesse(m, v):
                self.vitesse = 1.0
                return False
        self.vitesse = v
        if self.sur_etat is not None:
            self.sur_etat()
        return True

    def regler_volume(self, v):
        """Le volume de tous les flux (0 à 1)."""
        self.volume = max(0.0, min(1.0, float(v)))
        for m in {id(f): f for f in self.flux.values()}.values():
            m.set_volume(self.volume)

    def couper_son(self, oui):
        """Son coupé (ou rendu) pour tous les flux ; les essais (rendre_muet) restent muets."""
        self.coupe = bool(oui)
        for m in {id(f): f for f in self.flux.values()}.values():
            m.set_muted(self.coupe or self.muet)

    def definir_boucle(self, boucle):
        """La boucle A-B ((a, None), (a, b) ou ()), en temps de référence : elle vaut pour toutes les pistes."""
        self.boucle = tuple(boucle or ())
        self._surveiller_boucle()

    def _surveiller_boucle(self):
        if self._minuteur_boucle is None and regles.boucle_complete(self.boucle) and self.en_lecture():
            self._minuteur_boucle = GLib.timeout_add(BOUCLE_MS, self._tic_boucle)

    def _tic_boucle(self):
        m = self.media()
        if m is None or not regles.boucle_complete(self.boucle) or not m.get_playing():
            self._minuteur_boucle = None
            return False
        a = self._attente
        if a is not None and a[0] is m and not a[2]:
            return True           # un déplacement en cours : on attend qu'il soit fait
        retour = regles.retour_boucle(self.boucle, self.position_ref())
        if retour is not None:
            self.aller_a(retour)
        return True

    def sauter(self, delta_s):
        """Avance ou recule de delta_s secondes (flèches), dans les limites du flux."""
        if self.media() is None:
            return None
        cible = max(0.0, self.position_ref() + delta_s)
        d = self.duree_ref()
        if d is not None:
            cible = min(cible, max(0.0, d - 0.05))
        self.aller_a(cible)
        return cible

    def pas_image(self, sens):
        """Une image plus loin (sens > 0) ou plus tôt (touches « . » et « , ») : en pause (la lecture s'arrête)."""
        m = self.media()
        if m is None or not m.is_prepared():
            return None
        if self.en_lecture():
            self.pause()
        ips = pistes_gst.images_par_seconde(m) if not self.audio_seul and self.cle not in self.sans_image else 25.0
        return self.sauter((1 if sens > 0 else -1) / max(1.0, ips))

    def _suivre(self):
        if self._minuteur is None:
            self._minuteur = GLib.timeout_add(200, self._tic)

    def _tic(self):
        m = self.media()
        if not m or not self.get_mapped():
            self._minuteur = None
            return False
        if self._fin_extrait is not None and m.get_playing() and self.position() >= self._fin_extrait:
            m.pause()
            self._fin_extrait = None
        if self.sur_position:
            self.sur_position(self.position_ref(), m.get_playing())
        return True


def libelle_position(s):
    return formats.chrono_precis(s)
