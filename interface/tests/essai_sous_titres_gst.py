"""Essais des sous-titres intégrés sous GStreamer, lancés à part sous Xvfb par test_sous_titres_gst (un processus par
essai). Sans aucun son : la sortie audio est fakeaudiosink (GST_PLUGIN_FEATURE_RANK, posé par l'essai), et les flux
sont muets en plus.

  python3 -m tests.essai_sous_titres_gst flux SORTIE VIDÉO…    pour chaque vidéo, un Gtk.MediaFile brut (témoin : le
                                                              rendu de GTK par défaut) puis un flux du lecteur
                                                              (gui/lecteur.media) : drapeau « text » de playbin,
                                                              éléments d'incrustation, texte sur l'image décodée
  python3 -m tests.essai_sous_titres_gst lecteur SORTIE DOSSIER   le lecteur de la page Résultats dans une fenêtre :
                                                              bascule de piste audio, déplacements, vidéo d'origine,
                                                              fenêtre fermée et retour, fermeture et réouverture ;
                                                              après chaque étape, l'état des playbins ouverts et une
                                                              capture de l'écran du lecteur ; enfin un témoin, le
                                                              drapeau « text » remis à la main

Le texte se voit sur l'image : les vidéos d'essai ont une image unie (FOND), et les sous-titres de GStreamer y sont
blancs cernés de noir, loin de ce fond. Une ligne JSON par mesure sur la sortie ; captures PNG dans SORTIE."""

import json
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gst, Gtk  # noqa: E402

import numpy as np  # noqa: E402

from voixla.gui import pistes_gst  # noqa: E402

FOND = (0x30, 0x50, 0x70)       # couleur unie des vidéos d'essai (test_sous_titres_gst.video_unie)
USINES_TEXTE = ("textoverlay", "subtitleoverlay")


def dire(**k):
    print(json.dumps(k, ensure_ascii=False), flush=True)


def boucle(secondes):
    fin = time.monotonic() + secondes
    ctx = GLib.MainContext.default()
    while time.monotonic() < fin:
        ctx.iteration(False)
        time.sleep(0.002)


def attendre(condition, delai=15.0):
    fin = time.monotonic() + delai
    while not condition() and time.monotonic() < fin:
        boucle(0.01)
    return bool(condition())


def incrustations(pb):
    """Les éléments d'incrustation de texte du playbin qui dessinent (pas muets) : [(usine, muet)]."""
    res = []
    it = pb.iterate_recurse()
    while True:
        r, e = it.next()
        if r != Gst.IteratorResult.OK:
            return res
        f = e.get_factory()
        if f is not None and f.get_name() in USINES_TEXTE:
            muet = bool(e.get_property("silent")) if e.find_property("silent") is not None else False
            res.append([f.get_name(), muet])


def pixels(texture):
    """(pixels du fond de la vidéo, pixels loin du fond : du texte dessiné), parmi les pixels opaques."""
    l, h = texture.get_width(), texture.get_height()
    d = Gdk.TextureDownloader.new(texture)
    d.set_format(Gdk.MemoryFormat.R8G8B8A8)
    octets, pas = d.download_bytes()
    a = np.frombuffer(octets.get_data(), np.uint8).reshape(h, pas)[:, :4 * l].reshape(h, l, 4).astype(np.int32)
    opaques = a[..., 3] == 255
    ecart = np.abs(a[..., :3] - np.array(FOND)).sum(-1)
    return int((opaques & (ecart <= 30)).sum()), int((opaques & (ecart > 120)).sum())


def capturer(widget, chemin=None, essais=40):
    """Le rendu du widget (comme une capture de la fenêtre sous Xvfb), en texture ; en PNG si chemin. Un rendu vide
    (pris pendant une mise en page) est refait à l'image suivante."""
    for _ in range(essais):
        l, h = widget.get_width(), widget.get_height()
        instantane = Gtk.Snapshot()
        if l > 0 and h > 0:
            Gtk.WidgetPaintable.new(widget).snapshot(instantane, l, h)
        noeud = instantane.to_node()
        if noeud is not None:
            t = widget.get_native().get_renderer().render_texture(noeud, Graphene.Rect().init(0, 0, l, h))
            if chemin is not None:
                t.save_to_png(str(chemin))
            return t
        boucle(0.05)
    return None


def etat_flux(m):
    """Ce que fait le playbin d'un flux du texte de la vidéo."""
    pb = pistes_gst.playbin(m)
    if pb is None:
        return {"playbin": None}
    return {"playbin": pb.get_factory().get_name(), "texte_coupe": pistes_gst.texte_coupe(m),
            "n_text": pb.get_property("n-text"), "incrustations": incrustations(pb)}


# --- un flux seul : témoin (rendu par défaut de GTK) et flux du lecteur -----------------------------------------------

def mesurer_flux(m, nom, sortie):
    m.set_muted(True)
    if not attendre(m.is_prepared):
        dire(nom=nom, erreur="flux jamais prêt", detail=str(m.get_error()))
        return
    # lu depuis le début : en mkv, un sous-titre commencé avant un déplacement n'est pas redonné par le démultiplexeur
    m.play()
    attendre(lambda: m.get_timestamp() >= 2_000_000, 8.0)
    m.pause()
    boucle(0.3)
    image = m.get_current_image()
    fond, texte = pixels(image) if isinstance(image, Gdk.Texture) else (None, None)
    if isinstance(image, Gdk.Texture):
        image.save_to_png(str(Path(sortie) / f"{nom}.png"))
    dire(nom=nom, **etat_flux(m), pixels_fond=fond, pixels_texte=texte, position=round(m.get_timestamp() / 1e6, 2))
    m.clear()


def flux(sortie, videos):
    from voixla.gui.lecteur import media

    class Proprio:
        durable = False

        def couper(self):
            pass

    proprio = Proprio()
    for i, v in enumerate(videos):
        mesurer_flux(Gtk.MediaFile.new_for_filename(str(v)), f"{i}-brut", sortie)
        mesurer_flux(media(v, proprio), f"{i}-lecteur", sortie)
    return 0


# --- le lecteur de la page Résultats, dans une fenêtre -----------------------------------------------------------------

def lecteur(sortie, dossier):
    from voixla import lecture as regles
    from voixla import pistes, rapports
    from voixla.gui.lecteur import Lecteur
    res = rapports.lire_dossier(Path(dossier), {"jules": "Jules"})[0]
    lec = Lecteur(hauteur=360)
    lec.rendre_muet()
    fenetre = Gtk.Window(title="Voix-là (essai)", default_width=680, default_height=480, child=lec)
    fenetre.present()
    n = [0]

    def definir():
        dispo = pistes.choisir_pistes(res, "video")
        lec.definir(pistes.sources_lecteur(dispo), "doublage",
                    sans_image={c for c, p in dispo.items() if not p.avec_image},
                    ephemeres={c for c, p in dispo.items() if p.copie}, pistes_audio=pistes.pistes_audio_lecteur(dispo))
        return attendre(lambda: lec.media() is not None and lec.media().is_prepared())

    def mesurer(etape):
        boucle(0.1)
        n[0] += 1
        t = capturer(lec.ecran, Path(sortie) / f"lecteur-{n[0]:02d}-{etape}.png")
        fond, texte = pixels(t) if t is not None else (None, None)
        ouverts = {id(m): m for m in lec.flux.values()}.values()
        dire(etape=etape, cle=lec.cle, flux=[etat_flux(m) for m in ouverts], courant=etat_flux(lec.media()),
             piste=pistes_gst.piste_lue(lec.media()), position=round(lec.position_ref(), 2),
             en_lecture=lec.en_lecture(), pixels_fond=fond, pixels_texte=texte)

    if not definir():
        dire(erreur="flux du doublage jamais prêt")
        return 1
    lec.aller_a(2.0, lire=True)
    boucle(1.0)
    mesurer("doublage")
    lec.basculer("beat")                       # même flux : current-audio
    boucle(0.9)
    mesurer("beat-meme-flux")
    lec.aller_a(4.0)                           # déplacement en lecture
    boucle(0.8)
    mesurer("deplacement")
    lec.pause()
    boucle(0.3)
    lec.aller_a(6.0)                           # déplacement en pause
    boucle(0.6)
    mesurer("pause")
    lec.media().play()
    boucle(0.4)
    lec.basculer("original")                   # la vidéo d'origine, avec ses propres sous-titres
    attendre(lambda: lec.media().is_prepared())
    boucle(1.0)
    mesurer("original")
    lec.basculer("doublage")
    boucle(0.8)
    mesurer("retour-doublage")
    son = regles.source_son("doublage", pistes.choisir_pistes(res, "mp3"), lec.sources.get("doublage"))
    lec.passer_en_son(son)                     # fenêtre fermée : le son seul…
    boucle(0.8)
    lec.revenir_en_video()                     # … puis la vidéo, rouverte
    attendre(lambda: lec.media().is_prepared())
    boucle(1.0)
    mesurer("fenetre-rouverte")
    lec.liberer()                              # fermeture, puis réouverture
    boucle(0.3)
    if not definir():
        dire(erreur="flux rouvert jamais prêt")
        return 1
    lec.aller_a(3.0, lire=True)
    boucle(1.0)
    mesurer("reouverture")
    # témoin : le drapeau « text » remis à la main (le rendu par défaut), le texte de GStreamer est sur la capture
    pb = pistes_gst.playbin(lec.media())
    pb.set_property("flags", int(pb.get_property("flags")) | pistes_gst.DRAPEAU_TEXTE)
    lec.aller_a(5.0)
    boucle(1.0)
    mesurer("temoin-texte-remis")
    lec.liberer()
    fenetre.destroy()
    return 0


def main(argv):
    import faulthandler
    faulthandler.dump_traceback_later(100, exit=True)    # un essai bloqué dit où, et s'arrête
    Gst.init(None)
    if len(argv) >= 3 and argv[0] == "flux":
        return flux(Path(argv[1]), [Path(v) for v in argv[2:]])
    if len(argv) == 3 and argv[0] == "lecteur":
        return lecteur(Path(argv[1]), Path(argv[2]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
