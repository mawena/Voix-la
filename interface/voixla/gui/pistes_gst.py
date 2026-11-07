"""Pistes audio d'un flux vidéo (Gtk.MediaFile) : changer de piste dans la même vidéo, sans changer de fichier. Aussi :
les sous-titres intégrés, que GStreamer ne dessine jamais (couper_texte), la vitesse et la cadence d'images.

Une vidéo doublée par doubler ≥ 0.6.0 a plusieurs pistes audio, calées à l'échantillon près (voixla.pistes). Le
lecteur passe de l'une à l'autre par current-audio du playbin de GStreamer qui lit la vidéo : même position, image qui
continue, sans rouvrir de fichier.

Gtk.MediaFile ne donne pas accès à son playbin. Avec GStreamer, c'est un GtkGstMediaFile : sa structure C est celle
de GtkMediaFile suivie de deux pointeurs, son lecteur (GtkGstPlayer, copie de GstPlay dans GTK 4.14 ; GstPlay
ailleurs) et son image (GtkGstPaintable). Le lecteur a la propriété « pipeline » : le playbin. On le retrouve par la
structure, avec des garde-fous : nom du type, tailles des structures, type de l'objet pointé, puis usine « playbin ».
Si quoi que ce soit ne correspond pas (autre version de GTK, autre moteur), playbin() rend None et l'interface lit
comme pour un ancien doublage, par des copies vidéo (hors_service).

Bascule : dans playbin, la piste choisie passe par un input-selector (toutes les pistes sont décodées, quelques %
d'un cœur pour cinq pistes AAC), puis par la file audio de playsink (« aqueue »), qui garde jusqu'à 1 s de son déjà
choisi : la nouvelle piste ne s'entendait qu'au bout de 1,07 s. Cette file est ramenée à FILE_AUDIO_S (0,2 s) pour les
vidéos à plusieurs pistes : bascule entendue en 0,26 s (mesuré), au même instant du son, sans blanc.

Piège de playbin (GStreamer 1.24, reproduit sans GTK) : changer current-audio pipeline en pause, ou juste avant une
pause, puis déplacer la lecture (déplacement avec vidage) bloque playbin pour de bon ; il faut ensuite tuer le
processus. On ne change donc de piste que pendant la lecture (état PLAYING atteint). Flux en pause : la piste voulue
est gardée, et posée dès que la lecture reprend (vérifié toutes les ATTENTE_MS) ; on entend alors encore la piste
d'avant pendant 0,2 à 0,3 s.
"""

import ctypes
import sys
import weakref

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, GObject, Gst, Gtk  # noqa: E402

FILE_AUDIO_S = 0.2           # file audio de playsink pour une vidéo à plusieurs pistes (1 s par défaut)
ATTENTE_MS = 50              # flux en pause : on regarde aussi souvent si la lecture a repris
USINES = ("playbin", "playbin3")

hors_service = False         # le playbin d'un flux est introuvable sur ce poste : plus d'essai, copies vidéo
_nouvel_objet = None         # pygobject_new (API C de PyGObject) : un objet Python pour un pointeur de GObject
_gobject = None


def _dire(texte, sujet="pistes audio"):
    print(f"voix-la : {sujet} : {texte}", file=sys.stderr, flush=True)


def _api():
    global _nouvel_objet, _gobject
    if _nouvel_objet is None:
        from gi import _gi
        lire = ctypes.pythonapi.PyCapsule_GetPointer
        lire.restype = ctypes.c_void_p
        lire.argtypes = [ctypes.py_object, ctypes.c_char_p]
        api = lire(_gi._PyGObject_API, b"gobject._PyGObject_API")
        # struct _PyGObject_Functions (pygobject.h) : register_class, register_wrapper, lookup_class, newgobj…
        _nouvel_objet = ctypes.PYFUNCTYPE(ctypes.py_object, ctypes.c_void_p)((ctypes.c_void_p * 4).from_address(api)[3])
        _gobject = ctypes.CDLL("libgobject-2.0.so.0")
        _gobject.g_type_name.restype = ctypes.c_char_p
        _gobject.g_type_name.argtypes = [ctypes.c_size_t]
    return _nouvel_objet, _gobject


def structure_connue(m):
    """Vrai si m est un GtkGstMediaFile de la structure attendue (GtkMediaFile, puis deux pointeurs)."""
    try:
        t = m.__gtype__
        if t.name != "GtkGstMediaFile":
            return False
        taille = GObject.type_query(t).instance_size
        parent = GObject.type_query(Gtk.MediaFile.__gtype__).instance_size
        return taille - parent == 2 * ctypes.sizeof(ctypes.c_void_p)
    except Exception:  # noqa: BLE001
        return False


def possible():
    """Vrai tant que rien n'a montré que le playbin des flux est introuvable (voir hors_service)."""
    return not hors_service


def playbin(m):
    """Le playbin qui lit m (un Gtk.MediaFile ouvert sur un fichier), ou None (voir l'en-tête)."""
    return _lecteur_et_playbin(m)[1]


def lecteur_gst(m):
    """Le lecteur GStreamer de m (GtkGstPlayer, copie de GstPlay ; GstPlay ailleurs), ou None : sa propriété « rate »
    règle la vitesse de lecture."""
    return _lecteur_et_playbin(m)[0]


def _lecteur_et_playbin(m):
    """(lecteur, playbin) de m, ou (None, None) (voir l'en-tête)."""
    if m is None or hors_service or not structure_connue(m):
        return None, None
    try:
        nouveau, gobj = _api()
        taille_pointeur = ctypes.sizeof(ctypes.c_void_p)
        parent = GObject.type_query(Gtk.MediaFile.__gtype__).instance_size
        # adresse du GObject : la capsule __gpointer__ de PyGObject (API publique), jamais déduite d'autre chose ;
        # sans elle, pas de lecture en mémoire (une adresse fausse ferait planter toute l'interface)
        lire = ctypes.pythonapi.PyCapsule_GetPointer
        lire.restype = ctypes.c_void_p
        lire.argtypes = [ctypes.py_object, ctypes.c_char_p]
        adresse = lire(m.__gpointer__, None)
        if not adresse:
            raise ValueError("adresse du GObject inconnue")
        for decalage in (parent, parent + taille_pointeur):
            p = ctypes.c_void_p.from_address(adresse + decalage).value
            if not p or p % taille_pointeur:
                continue
            classe = ctypes.c_void_p.from_address(p).value          # GTypeInstance → GTypeClass
            nom = gobj.g_type_name(ctypes.c_size_t.from_address(classe).value) if classe else None
            if not nom or b"Play" not in nom or b"Paintable" in nom:
                continue
            lecteur = nouveau(p)
            if lecteur.find_property("pipeline") is None:
                continue
            pb = lecteur.get_property("pipeline")
            usine = pb.get_factory() if pb is not None else None
            if usine is not None and usine.get_name() in USINES:
                return lecteur, pb
    except Exception as e:  # noqa: BLE001
        _dire(f"playbin introuvable ({type(e).__name__} : {e})")
    return None, None


# --- Sous-titres intégrés : jamais dessinés par GStreamer ---------------------------------------------------------------
#
# playbin a le drapeau « text » par défaut (GstPlay et le lecteur de GTK ne l'enlèvent pas) : la première piste de
# sous-titres en texte d'une vidéo (les paroles ajoutées par doubler, en mp4 comme en mkv, ou les sous-titres d'une
# vidéo d'origine) était dessinée sur l'image par subtitleoverlay/textoverlay, quelles que soient sa disposition et son
# drapeau « enabled ». On aurait dit des sous-titres incrustés, et ils s'ajoutaient aux paroles de l'interface (plein
# écran, « Sous-titres sur la vidéo ») : en double. Les seules paroles affichées sont celles de l'interface.
#
# Le drapeau est retiré à l'ouverture de chaque flux (lecteur.media), avant que playbin ne relie ses pistes : playsink
# ne crée alors aucun élément d'incrustation ; retiré plus tard, playsink rend ses incrustations muettes (« silent »).
# current-text à -1 ne coupe rien (c'est « automatique » : playbin garde la piste 0), et l'API de GstPlay
# (subtitle-track-enabled) n'est pas accessible : le lecteur de GTK 4.14 en est une copie privée (GtkGstPlayer).
# Le drapeau tient après une bascule de piste audio (current-audio), un déplacement, en pause et en lecture ; un flux
# rouvert (autre piste, vidéo d'origine, retour de la fenêtre fermée) est un nouveau playbin, coupé à son ouverture.
# Vérifié avec GTK 4.14 et GStreamer 1.24 (tests/test_sous_titres_gst.py).
#
# Playbin introuvable (voir l'en-tête) : rien à couper, un message le dit une fois. GStreamer dessine alors la
# première piste de sous-titres comme avant, en plus des paroles de l'interface sur l'image ; la lecture, elle, ne
# change pas.

DRAPEAU_TEXTE = 1 << 2       # GST_PLAY_FLAG_TEXT de playbin et playbin3 : sous-titres dessinés sur l'image
_texte_signale = False       # « playbin introuvable : sous-titres non coupés » déjà dit


def couper_texte(m):
    """Le playbin de m (un Gtk.MediaFile, prêt ou non) ne dessine plus aucun sous-titre : drapeau « text » retiré (voir
    plus haut). Vrai si c'est fait (ou l'était déjà), faux si le playbin est introuvable."""
    global _texte_signale
    pb = playbin(m)
    try:
        if pb is not None and pb.find_property("flags") is not None:
            drapeaux = int(pb.get_property("flags"))
            if drapeaux & DRAPEAU_TEXTE:
                pb.set_property("flags", drapeaux & ~DRAPEAU_TEXTE)
            return True
    except Exception as e:  # noqa: BLE001
        _dire(f"rendu de GStreamer impossible à couper ({type(e).__name__} : {e})", "sous-titres")
        return False
    if m is not None and not _texte_signale:
        _texte_signale = True
        _dire("playbin introuvable : GStreamer peut dessiner sur l'image la piste de sous-titres d'une vidéo, en plus "
              "des paroles de l'interface", "sous-titres")
    return False


def texte_coupe(m):
    """Vrai si le playbin de m ne dessine aucun sous-titre (drapeau « text » absent) ; None si le playbin est
    introuvable."""
    pb = playbin(m)
    if pb is None or pb.find_property("flags") is None:
        return None
    return not int(pb.get_property("flags")) & DRAPEAU_TEXTE


# --- Vitesse de lecture, image par image ------------------------------------------------------------------------------
#
# Le lecteur de GTK (comme GstPlay) pose scaletempo en filtre audio de son playbin (« audio-filter ») : à une autre
# vitesse que 1, le son garde sa hauteur (indispensable pour du chant). Sa propriété « rate » relance la lecture à la
# même position avec la nouvelle vitesse, que les déplacements suivants gardent. Vérifié avec GTK 4.14 et GStreamer
# 1.24 (tests/essai_lecteur_complet.py : la fréquence entendue ne change pas à 0,5× ni à 2×).

def garde_la_hauteur(m):
    """Vrai si le son de m passe par scaletempo (la hauteur est gardée quand la vitesse change)."""
    pb = playbin(m)
    if pb is None or pb.find_property("audio-filter") is None:
        return False
    try:
        f = pb.get_property("audio-filter")
        usine = f.get_factory() if f is not None else None
        return usine is not None and usine.get_name() == "scaletempo"
    except Exception:  # noqa: BLE001
        return False


def vitesse(m):
    """La vitesse de lecture de m (1 si elle est inconnue)."""
    lec = lecteur_gst(m)
    try:
        return float(lec.get_property("rate")) if lec is not None and lec.find_property("rate") else 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def regler_vitesse(m, v):
    """Règle la vitesse de m (prêt), si son son garde sa hauteur (scaletempo) ; rien si c'est déjà elle. Vrai si m lit
    à cette vitesse."""
    if m is None or not m.is_prepared():
        return False
    lec = lecteur_gst(m)
    if lec is None or lec.find_property("rate") is None:
        return False
    try:
        if abs(float(lec.get_property("rate")) - v) < 1e-3:
            return True
        if not garde_la_hauteur(m):
            return False
        lec.set_property("rate", float(v))
        return True
    except Exception as e:  # noqa: BLE001
        _dire(f"vitesse impossible ({type(e).__name__} : {e})")
        return False


def images_par_seconde(m, defaut=25.0):
    """La cadence d'images de la vidéo que lit m (caps du flux vidéo choisi par playbin), sinon defaut."""
    pb = playbin(m) if m is not None and m.is_prepared() else None
    if pb is None:
        return defaut
    try:
        pad = pb.emit("get-video-pad", max(0, pb.get_property("current-video")))
        caps = pad.get_current_caps() if pad is not None else None
        if caps is not None and caps.get_size():
            ok, n, d = caps.get_structure(0).get_fraction("framerate")
            if ok and n > 0 and d > 0:
                return n / d
    except Exception:  # noqa: BLE001
        pass
    return defaut


def regler_file_audio(pb, secondes=FILE_AUDIO_S):
    """La file audio de playsink (« aqueue ») ramenée à secondes : une bascule s'entend d'autant plus tôt."""
    try:
        q = pb.get_by_name("aqueue")
        if q is not None and q.find_property("max-size-time") is not None:
            voulu = int(secondes * Gst.SECOND)
            if q.get_property("max-size-time") != voulu:
                q.set_property("max-size-time", voulu)
    except Exception:  # noqa: BLE001
        pass


def nombre_de_pistes(m):
    """Le nombre de pistes audio de m une fois prêt (n-audio de playbin), sinon None."""
    pb = playbin(m) if m is not None and m.is_prepared() else None
    return pb.get_property("n-audio") if pb is not None and pb.find_property("n-audio") else None


def piste_lue(m):
    """L'index de la piste audio que lit m (current-audio), sinon None."""
    pb = playbin(m) if m is not None and m.is_prepared() else None
    return pb.get_property("current-audio") if pb is not None and pb.find_property("current-audio") else None


def en_lecture(pb):
    """Vrai si le pipeline est en lecture, sans changement d'état en cours."""
    try:
        _r, actuel, attendu = pb.get_state(0)
    except Exception:  # noqa: BLE001
        return False
    return actuel == Gst.State.PLAYING and attendu == Gst.State.VOID_PENDING


_voulues = weakref.WeakKeyDictionary()     # flux → piste audio voulue, pas encore posée (flux en pause)
_attentes = weakref.WeakKeyDictionary()    # flux → minuteur qui attend sa lecture


def poser(m, pb=None):
    """Pose la piste voulue de m si le flux joue (voir l'en-tête) ; sinon, attend que la lecture reprenne. Vrai si
    elle est posée (ou l'était déjà)."""
    index = _voulues.get(m)
    pb = pb if pb is not None else playbin(m)
    if index is None or pb is None:
        _voulues.pop(m, None)
        return False
    if pb.get_property("current-audio") == index:
        _voulues.pop(m, None)
        return True
    if en_lecture(pb):
        pb.set_property("current-audio", index)
        _voulues.pop(m, None)
        return True
    if m not in _attentes:
        ref = weakref.ref(m)

        def verifier():
            flux = ref()
            if flux is None or flux not in _voulues or not flux.is_prepared():
                if flux is not None:
                    _attentes.pop(flux, None)
                    _voulues.pop(flux, None)
                return False
            if poser(flux):
                _attentes.pop(flux, None)
                return False
            return True
        _attentes[m] = GLib.timeout_add(ATTENTE_MS, verifier)
    return False


def choisir(m, index):
    """Fait lire à m (prêt) sa piste audio n° index (a:N), à la même position : current-audio de son playbin, file
    audio raccourcie ; en pause, dès la reprise de la lecture (poser). Vrai si c'est fait ou prévu ; faux si m n'est
    pas prêt, si index est hors des pistes, ou si le playbin est introuvable (alors hors_service : l'interface revient
    aux copies vidéo)."""
    global hors_service
    if m is None or index is None or not m.is_prepared():
        return False
    pb = playbin(m)
    if pb is None:
        if not hors_service:
            hors_service = True
            _dire("playbin du lecteur introuvable : les pistes séparées passeront par des copies vidéo")
        return False
    n = pb.get_property("n-audio")
    if not 0 <= index < n:
        _dire(f"piste audio {index} demandée, la vidéo en a {n}")
        return False
    regler_file_audio(pb)
    _voulues[m] = index
    poser(m, pb)
    return True


def piste_voulue(m):
    """La piste audio que m lira dès la reprise de la lecture, si elle n'est pas encore posée, sinon None."""
    return _voulues.get(m) if m is not None else None
