"""Essai du lecteur complet (gui/lecteur.py, gui/pistes_gst.py), lancé à part par test_lecteur_complet sous Xvfb, sur
un bus privé, sans aucun son : la sortie audio est fakeaudiosink (GST_PLUGIN_FEATURE_RANK, posé par l'essai) et les
flux sont muets en plus. Le son est écouté avant le volume du playbin, donc après scaletempo.

  python3 -m tests.essai_lecteur_complet DOSSIER

DOSSIER contient deux.mp4 (deux pistes audio, sinus à 440 Hz puis 660 Hz : « doublage » et « beat » dans le même flux)
et autre.mp4 (sinus à 550 Hz : l'« original », un autre fichier). Une ligne JSON par mesure sur la sortie :
- la hauteur : scaletempo est bien le filtre audio, et la fréquence entendue reste celle du sinus à 0,5× et à 2×,
  pendant que la position avance deux fois moins ou deux fois plus vite ;
- la boucle A-B : la position reste entre A et B et y revient plusieurs fois ; elle survit à une bascule dans le même
  flux (on entend alors 660 Hz) et à une bascule vers un autre fichier, qui prend aussi la vitesse ;
- image par image, en pause ; le volume, qui vaut pour les flux ouverts ensuite."""

import json
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst, Gtk  # noqa: E402

import numpy as np  # noqa: E402

from voixla.gui import pistes_gst  # noqa: E402
from voixla.gui.lecteur import Lecteur  # noqa: E402


def dire(**k):
    print(json.dumps(k, ensure_ascii=False), flush=True)


def boucle(secondes, rappel=None):
    fin = time.monotonic() + secondes
    ctx = GLib.MainContext.default()
    while time.monotonic() < fin:
        ctx.iteration(False)
        if rappel is not None:
            rappel()
        time.sleep(0.002)


def attendre(test, delai=15.0):
    fin = time.monotonic() + delai
    ctx = GLib.MainContext.default()
    while not test() and time.monotonic() < fin:
        ctx.iteration(False)
        time.sleep(0.005)
    return test()


class Oreille:
    """Le son de chaque flux, pris à l'entrée du volume du playbin (après scaletempo) : (instant, échantillons mono,
    taux)."""

    def __init__(self):
        self.sons = []
        self.branches = set()

    def brancher(self, m):
        if id(m) in self.branches or not m.is_prepared():
            return
        pb = pistes_gst.playbin(m)
        if pb is None:
            return
        it = pb.iterate_recurse()
        volumes = []
        while True:
            r, e = it.next()
            if r != Gst.IteratorResult.OK:
                break
            f = e.get_factory()
            if f is not None and f.get_name() == "volume":
                volumes.append(e)
        if volumes:
            volumes[0].get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, self._son)
            self.branches.add(id(m))

    def _son(self, pad, info):
        b = info.get_buffer()
        caps = pad.get_current_caps()
        if caps is None:
            return Gst.PadProbeReturn.OK
        s = caps.get_structure(0)
        fmt, canaux, taux = s.get_string("format"), s.get_int("channels")[1], s.get_int("rate")[1]
        ok, carte = b.map(Gst.MapFlags.READ)
        if ok:
            brut = bytes(carte.data)
            b.unmap(carte)
            x = np.frombuffer(brut, np.float32) if fmt == "F32LE" else \
                np.frombuffer(brut, np.int16).astype(np.float32) / 32768.0
            x = x.reshape(-1, canaux).mean(1) if canaux > 1 else x
            self.sons.append((time.monotonic(), x.astype(np.float64), taux))
        return Gst.PadProbeReturn.OK

    def frequence(self, depuis):
        """La fréquence dominante de ce qu'on a entendu depuis l'instant depuis (Hz), ou None."""
        morceaux = [(x, t) for i, x, t in self.sons if i >= depuis]
        if not morceaux:
            return None
        taux = morceaux[-1][1]
        x = np.concatenate([m for m, t in morceaux if t == taux])
        if len(x) < taux // 4:
            return None
        x = x[-min(len(x), taux):] * np.hanning(min(len(x), taux))
        spectre = np.abs(np.fft.rfft(x, 8 * len(x)))
        return float(np.argmax(spectre) * taux / (8 * len(x)))


def main(argv):
    dossier = Path(argv[0])
    deux, autre = dossier / "deux.mp4", dossier / "autre.mp4"
    lec = Lecteur(commandes=False)
    lec.rendre_muet()
    lec.definir({"doublage": (deux, 0.0, "Doublage"), "beat": (deux, 0.0, "Beat seul"),
                 "original": (autre, 0.0, "Original")}, "doublage", pistes_audio={"doublage": 0, "beat": 1})
    m = lec.media()
    if not attendre(m.is_prepared):
        dire(erreur="flux jamais prêt")
        return 1
    oreille = Oreille()
    oreille.brancher(m)
    dire(scaletempo=pistes_gst.garde_la_hauteur(m), vitesse_possible=lec.vitesse_possible())

    # vitesse : la hauteur ne bouge pas, la position avance plus ou moins vite
    m.play()
    boucle(1.2)
    dire(vitesse=1.0, frequence=oreille.frequence(time.monotonic() - 0.8))
    for v in (2.0, 0.5):
        lec.regler_vitesse(v)
        boucle(0.4)
        p0, t0 = lec.position_ref(), time.monotonic()
        boucle(1.6)
        avance = (lec.position_ref() - p0) / (time.monotonic() - t0)
        dire(vitesse=v, lue=pistes_gst.vitesse(m), frequence=oreille.frequence(t0 + 0.3), avance=round(avance, 3))
    lec.regler_vitesse(1.0)

    # boucle A-B, puis bascule dans le même flux (current-audio), puis vers un autre fichier
    positions = []

    def noter():
        positions.append(lec.position_ref())
    lec.definir_boucle((1.0, 2.0))
    lec.aller_a(0.9, lire=True)
    boucle(3.5, noter)
    dire(boucle="doublage", **resume(positions))
    positions.clear()
    lec.basculer("beat")
    t0 = time.monotonic()
    boucle(3.5, noter)
    dire(boucle="beat", meme_flux=lec.media() is m, frequence=oreille.frequence(t0 + 0.6), **resume(positions))
    positions.clear()
    lec.regler_vitesse(1.5)
    lec.basculer("original")
    attendre(lambda: lec.media() is not None and lec.media().is_prepared())
    oreille.brancher(lec.media())
    t0 = time.monotonic()
    boucle(4.0, noter)
    dire(boucle="original", autre_flux=lec.media() is not m, lue=pistes_gst.vitesse(lec.media()),
         frequence=oreille.frequence(t0 + 0.8), **resume(positions))
    lec.definir_boucle(())
    lec.regler_vitesse(1.0)

    # image par image (en pause) ; le volume vaut pour les flux ouverts ensuite
    lec.basculer("doublage")
    attendre(lambda: lec.media().is_prepared())
    lec.pause()
    lec.aller_a(3.0)
    boucle(0.6)
    avant = lec.position_ref()
    lec.pas_image(1)
    boucle(0.6)
    apres = lec.position_ref()
    lec.pas_image(-1)
    boucle(0.6)
    dire(image=round(apres - avant, 4), retour=round(lec.position_ref() - avant, 4), ips=pistes_gst.images_par_seconde(
        lec.media()), en_lecture=lec.en_lecture())
    lec.regler_volume(0.4)
    lec.basculer("original")
    attendre(lambda: lec.media().is_prepared())
    dire(volume=round(lec.media().get_volume(), 3), muet=lec.media().get_muted())
    lec.liberer()
    dire(fini=True)
    return 0


def resume(positions):
    """Ce que la boucle a fait : bornes des positions (après la première demi-seconde) et nombre de retours à A."""
    p = positions[len(positions) // 7:]
    retours = sum(1 for a, b in zip(p, p[1:]) if b < a - 0.3)
    return {"mini": round(min(p), 3) if p else None, "maxi": round(max(p), 3) if p else None, "retours": retours}


if __name__ == "__main__":
    Gst.init(None)
    Gtk.init()
    sys.exit(main(sys.argv[1:]))
