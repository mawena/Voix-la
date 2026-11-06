"""Images de la bibliothèque des résultats : les miniatures 16:9, préparées en tâche de fond et chargées au fil du
défilement, et le widget qui les dessine (ou dessine une pochette générée).

- **Quelle image** : une image de la vidéo doublée, prise au tiers (voixla.pistes.preparer_miniature, forme « large »,
  le même cache que la miniature de la barre de lecture : ~/.cache/voix-la/miniatures/). Pour un doublage sans vidéo,
  la pochette intégrée au MP3, sur un fond flou tiré d'elle-même. Sans rien de tout cela, une pochette générée : un
  dégradé tiré du titre (voixla.bibliotheque.couleurs_pochette) et une note.
- **Sans jamais bloquer l'interface** : un fil lit les miniatures déjà en cache (une lecture de fichier et un décodage
  JPEG, quelques millisecondes) ; deux fils au plus en préparent de nouvelles (ffprobe puis ffmpeg, sous nice -n 19 et
  ionice -c3). Les deux files servent la dernière demande d'abord : ce qu'on voit passe devant ce qu'on a déjà fait
  défiler, et une demande qu'aucune carte n'attend plus est abandonnée. Une préparation attend tant que la mémoire
  disponible est sous le seuil des préparations (voixla.preparation.SEUIL_MEMOIRE_MO).
- **En mémoire** : les dernières images lues (Gdk.Texture), LIMITE_TEXTURES au plus ; une carte qui revient à l'écran
  les retrouve sans rien relire.
"""

import math
import threading
import time
import traceback
from collections import OrderedDict

import gi

gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from .. import pistes, preparation  # noqa: E402
from .. import bibliotheque as bib  # noqa: E402

LIMITE_TEXTURES = 160            # 480 × 270 × 4 octets ≈ 0,5 Mo chacune : 80 Mo au plus
FILS_PREPARATION = 2
ATTENTE_MEMOIRE_S = 5


class _Demande:
    __slots__ = ("cle", "fichier", "rappels")

    def __init__(self, cle, fichier):
        self.cle, self.fichier, self.rappels = cle, fichier, {}


class Miniatures:
    """Le service des miniatures 16:9. cle : la signature du fichier (chemin, taille, date ; voixla.bibliotheque.
    Version.image_sig) : un fichier refait (remix) a une autre clé, donc une autre image."""

    def __init__(self, fils=FILS_PREPARATION):
        self._cond = threading.Condition()
        self._lecture = OrderedDict()       # clé → _Demande : à lire en cache (dernière demande à la fin)
        self._preparation = OrderedDict()   # clé → _Demande : à préparer (ffmpeg)
        self._en_cours = {}                 # clé → _Demande lue ou préparée en ce moment
        self._textures = OrderedDict()      # clé → Gdk.Texture (les plus récemment vues à la fin)
        self._impossibles = {}              # clé → raison (pas d'image, fichier illisible) : pochette générée
        self._jeton = 0
        self._fils_max = max(1, fils)
        self._fils = []
        self.mesures = {"lues": 0, "preparees": 0, "echecs": 0, "duree_preparation_s": 0.0, "duree_lecture_s": 0.0,
                        "premiere_demande": None, "derniere_livraison": None}

    # --- interface (fil principal) -----------------------------------------------------------------------------------

    def texture(self, cle):
        t = self._textures.get(cle)
        if t is not None:
            self._textures.move_to_end(cle)
        return t

    def impossible(self, cle):
        return cle in self._impossibles

    def demander(self, cle, fichier, rappel):
        """rappel(texture ou None) dans le fil de l'interface, une fois l'image lue ou préparée (None : pas d'image,
        la pochette générée reste). Rend un jeton pour annuler(), ou None si la réponse est déjà venue."""
        t = self.texture(cle)
        if t is not None or cle in self._impossibles or fichier is None:
            rappel(t)
            return None
        if self.mesures["premiere_demande"] is None:
            self.mesures["premiere_demande"] = time.monotonic()
        with self._cond:
            self._jeton += 1
            jeton = (cle, self._jeton)
            d = self._en_cours.get(cle) or self._preparation.get(cle) or self._lecture.get(cle)
            if d is None:
                d = _Demande(cle, str(fichier))
                self._lecture[cle] = d
            elif cle in self._lecture:
                self._lecture.move_to_end(cle)
            elif cle in self._preparation:
                self._preparation.move_to_end(cle)
            d.rappels[jeton[1]] = rappel
            self._demarrer()
            self._cond.notify_all()
        return jeton

    def annuler(self, jeton):
        """La carte ne veut plus de cette image (elle montre autre chose, ou a quitté l'écran)."""
        if jeton is None:
            return
        cle, n = jeton
        with self._cond:
            for file in (self._lecture, self._preparation):
                d = file.get(cle)
                if d is not None:
                    d.rappels.pop(n, None)
                    if not d.rappels:
                        del file[cle]
            d = self._en_cours.get(cle)
            if d is not None:
                d.rappels.pop(n, None)

    def oublier_impossibles(self):
        """Actualiser (F5) : on réessaie les fichiers sans image (une pochette a pu être ajoutée)."""
        self._impossibles.clear()

    def occupe(self):
        with self._cond:
            return bool(self._lecture or self._preparation or self._en_cours)

    def _livrer(self, d, texture, raison):
        if texture is not None:
            self._textures[d.cle] = texture
            self._textures.move_to_end(d.cle)
            while len(self._textures) > LIMITE_TEXTURES:
                self._textures.popitem(last=False)
        elif raison:
            self._impossibles[d.cle] = raison
        with self._cond:
            if self._en_cours.get(d.cle) is d:
                del self._en_cours[d.cle]
            rappels = list(d.rappels.values())
            d.rappels.clear()
        self.mesures["derniere_livraison"] = time.monotonic()
        for r in rappels:
            try:
                r(texture)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        return False

    # --- fils --------------------------------------------------------------------------------------------------------

    def _demarrer(self):
        """Un lecteur, et des préparateurs au besoin (fils démons, créés à la première demande)."""
        self._fils = [f for f in self._fils if f.is_alive()]
        roles = [getattr(f, "role", "") for f in self._fils]
        if "lecture" not in roles:
            self._lancer("lecture")
        if self._preparation and roles.count("preparation") < min(self._fils_max, len(self._preparation)):
            self._lancer("preparation")

    def _lancer(self, role):
        f = threading.Thread(target=self._boucle, args=(role,), daemon=True, name=f"miniatures-{role}")
        f.role = role
        self._fils.append(f)
        f.start()

    def _prendre(self, role):
        file = self._lecture if role == "lecture" else self._preparation
        with self._cond:
            while not file:
                if not self._cond.wait(timeout=30) and not file:
                    # rien depuis 30 s : le fil s'arrête ; il quitte la liste sous le verrou, pour qu'une demande
                    # arrivée juste après en relance un autre (_demarrer)
                    self._fils = [f for f in self._fils if f is not threading.current_thread()]
                    return None
            cle, d = file.popitem(last=True)
            self._en_cours[cle] = d
            return d

    def _boucle(self, role):
        while True:
            d = self._prendre(role)
            if d is None:
                return
            try:
                if role == "lecture":
                    self._lire(d)
                else:
                    self._preparer(d)
            except Exception as e:  # noqa: BLE001 — un fichier abîmé ne doit rien arrêter
                traceback.print_exc()
                GLib.idle_add(self._livrer, d, None, str(e))

    def _charger(self, f):
        donnees = GLib.Bytes.new(f.read_bytes())
        return Gdk.Texture.new_from_bytes(donnees)

    def _lire(self, d):
        debut = time.monotonic()
        f = pistes.miniature_prete(d.fichier, "large")
        if f is None:
            with self._cond:
                if self._en_cours.get(d.cle) is d:
                    del self._en_cours[d.cle]
                if d.rappels:      # quelqu'un l'attend encore : à préparer
                    self._preparation[d.cle] = d
                    self._demarrer()
                    self._cond.notify_all()
            return
        t = self._charger(f)
        self.mesures["lues"] += 1
        self.mesures["duree_lecture_s"] += time.monotonic() - debut
        GLib.idle_add(self._livrer, d, t, "")

    def _preparer(self, d):
        if not d.rappels:
            with self._cond:
                self._en_cours.pop(d.cle, None)
            return
        while not preparation.memoire_suffisante(preparation.memoire_disponible_mo()):
            time.sleep(ATTENTE_MEMOIRE_S)     # garde-fou mémoire, dans le fil : l'interface continue
        debut = time.monotonic()
        try:
            f = pistes.preparer_miniature(d.fichier, "large", basse=True)
        except (pistes.ErreurPistes, OSError) as e:
            self.mesures["echecs"] += 1
            GLib.idle_add(self._livrer, d, None, str(e) or "pas d’image")
            return
        t = self._charger(f)
        self.mesures["preparees"] += 1
        self.mesures["duree_preparation_s"] += time.monotonic() - debut
        GLib.idle_add(self._livrer, d, t, "")


_instance = None


def miniatures():
    global _instance
    if _instance is None:
        _instance = Miniatures()
    return _instance


# --- Le widget -------------------------------------------------------------------------------------------------------

def _rgba(rgb, alpha=1.0):
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = (*rgb, alpha)
    return c


class Vignette(Gtk.Widget):
    """Une image 16:9 aux coins arrondis : la miniature si elle est là, sinon le dégradé de la pochette générée (la note
    est un Gtk.Image posé par-dessus, voir Image). Hauteur = largeur × 9/16."""

    __gtype_name__ = "VoixlaVignette"

    def __init__(self, largeur_min=120, largeur_nat=240, rayon=10, **k):
        super().__init__(**k)
        self.largeur_min, self.largeur_nat, self.rayon = largeur_min, largeur_nat, rayon
        self.texture = None
        self.couleurs = bib.couleurs_pochette("")

    def montrer(self, texture=None, titre=None):
        if titre is not None:
            self.couleurs = bib.couleurs_pochette(titre)
        if texture is not self.texture or titre is not None:
            self.texture = texture
            self.queue_draw()

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self.largeur_min, self.largeur_nat, -1, -1
        largeur = for_size if for_size > 0 else self.largeur_nat
        h = max(1, round(largeur * 9 / 16))
        return h, h, -1, -1

    def do_snapshot(self, s):
        l, h = self.get_width(), self.get_height()
        if l <= 0 or h <= 0:
            return
        cadre = Graphene.Rect().init(0, 0, l, h)
        arrondi = Gsk.RoundedRect()
        arrondi.init_from_rect(cadre, self.rayon)
        s.push_rounded_clip(arrondi)
        t = self.texture
        if t is not None:
            # comme object-fit: cover : l'image remplit le cadre, recadrée au centre
            tl, th = t.get_width(), t.get_height()
            e = max(l / tl, h / th)
            dl, dh = tl * e, th * e
            s.append_scaled_texture(t, Gsk.ScalingFilter.TRILINEAR,
                                    Graphene.Rect().init((l - dl) / 2, (h - dh) / 2, dl, dh))
        else:
            a, b, angle = self.couleurs
            r = math.radians(angle)
            cx, cy, demi = l / 2, h / 2, (abs(l * math.cos(r)) + abs(h * math.sin(r))) / 2
            debut = Graphene.Point().init(cx - math.cos(r) * demi, cy - math.sin(r) * demi)
            fin = Graphene.Point().init(cx + math.cos(r) * demi, cy + math.sin(r) * demi)
            arrets = []
            for pos, c in ((0.0, a), (1.0, b)):
                arret = Gsk.ColorStop()
                arret.offset, arret.color = pos, _rgba(c)
                arrets.append(arret)
            s.append_linear_gradient(cadre, debut, fin, arrets)
            # un léger voile en bas : la durée et les badges, en blanc, restent lisibles
            voile = []
            for pos, alpha in ((0.55, 0.0), (1.0, 0.22)):
                arret = Gsk.ColorStop()
                arret.offset, arret.color = pos, _rgba((0, 0, 0), alpha)
                voile.append(arret)
            s.append_linear_gradient(cadre, Graphene.Point().init(0, 0), Graphene.Point().init(0, h), voile)
        s.pop()


class Image(Gtk.Overlay):
    """La vignette et sa note (pochette générée) ; lier(version) demande la miniature au service, delier() annule.
    Les autres éléments posés sur l'image (badges, durée, bouton de lecture) s'ajoutent par add_overlay."""

    def __init__(self, largeur_min=120, largeur_nat=240, rayon=10, taille_note=40):
        super().__init__(css_classes=["vignette"])
        self.vignette = Vignette(largeur_min, largeur_nat, rayon)
        self.set_child(self.vignette)
        self.note = Gtk.Image(icon_name="emblem-music-symbolic", pixel_size=taille_note, can_target=False,
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, css_classes=["vignette-note"])
        self.add_overlay(self.note)
        self._jeton = None
        self._cle = None

    def lier(self, version):
        self.delier()
        cle = version.image_sig
        self._cle = cle
        t = miniatures().texture(cle) if cle else None
        self.vignette.montrer(t, version.titre)
        self.note.set_visible(t is None)
        if t is None and cle and not miniatures().impossible(cle):
            self._jeton = miniatures().demander(cle, version.image, lambda tex, c=cle: self._recue(c, tex))

    def _recue(self, cle, texture):
        self._jeton = None
        if cle != self._cle:
            return
        self.vignette.montrer(texture)
        self.note.set_visible(texture is None)

    def delier(self):
        miniatures().annuler(self._jeton)
        self._jeton = None
        self._cle = None
