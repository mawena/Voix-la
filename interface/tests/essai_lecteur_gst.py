"""Essais de lecture GStreamer d'une vidéo doublée à plusieurs pistes audio, lancés à part par
test_pistes_dans_la_video (un processus par essai : GStreamer et GTK y sont réglés avant de démarrer). Sans aucun son :
la sortie audio est fakeaudiosink (GST_PLUGIN_FEATURE_RANK, posé par l'essai), et les flux sont muets en plus.

  python3 -m tests.essai_lecteur_gst flux VIDEO          un Gtk.MediaFile seul (sans écran) : son playbin, la piste
                                                         lue par défaut, celle qu'on entend
  python3 -m tests.essai_lecteur_gst lecteur DOSSIER     le lecteur de la page Résultats (gui/lecteur.py, sous Xvfb) :
                                                         bascules dans le même flux, vers la vidéo d'origine et
                                                         retour, son seul (fenêtre fermée) et retour

Une ligne JSON par mesure sur la sortie. Ce qu'on entend est reconnu par corrélation avec chaque piste de la vidéo,
décodée par ffmpeg, à la place que donne le temps (pts) de chaque tampon arrivé à la sortie audio."""

import json
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst, Gtk  # noqa: E402

import numpy as np  # noqa: E402

from tests import cinq_pistes as C  # noqa: E402
from voixla.gui import pistes_gst  # noqa: E402

SR = 44100


def dire(**k):
    print(json.dumps(k, ensure_ascii=False), flush=True)


def elements(pb, test):
    it = pb.iterate_recurse()
    while True:
        r, e = it.next()
        if r != Gst.IteratorResult.OK:
            return
        if test(e):
            yield e


def usine(e):
    f = e.get_factory()
    return f.get_name() if f else ""


def classe(e):
    f = e.get_factory()
    return (f.get_metadata("klass") or "") if f else ""


class Ecoute:
    """Les tampons arrivés à la sortie audio (instant, pts, son mono) et les images décodées, de chaque flux branché."""

    def __init__(self):
        self.sons = []          # (instant monotone, pts en s, son mono à SR, flux)
        self.images = []        # (instant monotone, flux)
        self.branches = set()

    def brancher(self, m, nom):
        pb = pistes_gst.playbin(m)
        if pb is None or nom in self.branches:
            return pb is not None
        sorties = list(elements(pb, lambda e: usine(e) == "fakeaudiosink"))
        volumes = list(elements(pb, lambda e: usine(e) == "volume"))
        decodeurs = list(elements(pb, lambda e: "Decoder/Video" in classe(e)))
        if not sorties or not volumes:
            dire(erreur="sortie audio : fakeaudiosink ou volume absent (aucun son ne doit sortir)")
            return False
        # le son est pris avant le volume : les flux sont muets, et la sortie est fakeaudiosink, qui ne joue rien
        volumes[0].get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER,
                                                    lambda pad, info: self._son(pad, info, nom))
        for d in decodeurs:
            d.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER,
                                              lambda pad, info: self._image(nom))
        self.branches.add(nom)
        return True

    def _son(self, pad, info, nom):
        b = info.get_buffer()
        s = pad.get_current_caps().get_structure(0)
        fmt, canaux, taux = s.get_string("format"), s.get_int("channels")[1], s.get_int("rate")[1]
        ok, carte = b.map(Gst.MapFlags.READ)
        if ok:
            brut = bytes(carte.data)
            b.unmap(carte)
            x = np.frombuffer(brut, np.float32) if fmt == "F32LE" else \
                np.frombuffer(brut, np.int16).astype(np.float32) / 32768.0
            x = x.reshape(-1, canaux).mean(1) if canaux > 1 else x
            if taux == SR and b.pts != Gst.CLOCK_TIME_NONE:
                self.sons.append((time.monotonic(), b.pts / Gst.SECOND, x.astype(np.float64), nom))
        return Gst.PadProbeReturn.OK

    def _image(self, nom):
        self.images.append((time.monotonic(), nom))
        return Gst.PadProbeReturn.OK


class Reconnaissance:
    """Quelle piste de la vidéo joue un tampon : corrélation avec chaque piste décodée, à sa place (pts + écart)."""

    def __init__(self, video):
        self.refs = []
        k = 0
        while True:
            try:
                self.refs.append(C.decoder(video, f"0:a:{k}"))
            except Exception:  # noqa: BLE001 — plus de piste
                break
            k += 1
        self.ecart = None

    def caler(self, tampons, piste=0):
        """L'écart (échantillons) entre la place donnée par le pts et le décodage de ffmpeg (amorce de l'encodeur)."""
        morceau = np.concatenate([x for _, _, x, _ in tampons])[:SR // 2]
        debut = int(round(tampons[0][1] * SR))
        meilleur = (-1.0, 0)
        for ecart in range(-4096, 4097, 1):
            i = debut + ecart
            if i < 0:
                continue
            r = self.refs[piste][i:i + len(morceau)]
            if len(r) < len(morceau):
                continue
            c = float(np.dot(r, morceau) / (np.linalg.norm(r) * np.linalg.norm(morceau) + 1e-12))
            if c > meilleur[0]:
                meilleur = (c, ecart)
        self.ecart = meilleur[1]
        return meilleur

    def piste(self, pts, x, seuil=0.9):
        i = int(round(pts * SR)) + (self.ecart or 0)
        scores = []
        for r in self.refs:
            seg = r[i:i + len(x)] if i >= 0 else np.zeros(0)
            scores.append(0.0 if len(seg) < len(x) or len(x) < 64 else
                          float(np.dot(seg, x) / (np.linalg.norm(seg) * np.linalg.norm(x) + 1e-12)))
        k = int(np.argmax(scores)) if scores else -1
        return (k if k >= 0 and scores[k] > seuil else None), scores


def attendre_pret(m, delai=15.0):
    fin = time.monotonic() + delai
    ctx = GLib.MainContext.default()
    while not m.is_prepared() and time.monotonic() < fin:
        ctx.iteration(False)
        time.sleep(0.005)
    return m.is_prepared()


def boucle(secondes):
    fin = time.monotonic() + secondes
    ctx = GLib.MainContext.default()
    while time.monotonic() < fin:
        ctx.iteration(False)
        time.sleep(0.002)


def flux(video):
    m = Gtk.MediaFile.new_for_filename(str(video))
    m.set_muted(True)
    if not attendre_pret(m):
        dire(erreur="flux jamais prêt", detail=str(m.get_error()))
        return 1
    pb = pistes_gst.playbin(m)
    dire(structure=pistes_gst.structure_connue(m), playbin=usine(pb) if pb is not None else None,
         n_audio=pistes_gst.nombre_de_pistes(m), current_audio=pistes_gst.piste_lue(m))
    e = Ecoute()
    if not e.brancher(m, "video"):
        return 1
    m.play()
    boucle(2.0)
    m.pause()
    rec = Reconnaissance(video)
    tampons = [t for t in e.sons if t[1] > 0.3]
    c, ecart = rec.caler(tampons, 0)
    entendues = [rec.piste(pts, x)[0] for _, pts, x, _ in tampons]
    dire(entendue=max(set(entendues), key=entendues.count), part=entendues.count(0) / max(1, len(entendues)),
         correlation_doublage=round(c, 4), ecart=ecart, images=len(e.images))
    m.clear()
    return 0


def lecteur(dossier):
    from voixla import lecture as regles
    from voixla import pistes, rapports
    from voixla.gui.lecteur import Lecteur
    res = rapports.lire_dossier(Path(dossier), {"jules": "Jules"})[0]
    dispo = pistes.choisir_pistes(res, "video")
    pa = pistes.pistes_audio_lecteur(dispo)
    dire(pistes_audio=pa)
    lec = Lecteur()
    lec.rendre_muet()
    lec.definir(pistes.sources_lecteur(dispo), "doublage", sans_image={c for c, p in dispo.items() if not p.avec_image},
                ephemeres={c for c, p in dispo.items() if p.copie}, pistes_audio=pa)
    m = lec.media()
    if not attendre_pret(m):
        dire(erreur="flux du doublage jamais prêt")
        return 1
    e = Ecoute()
    e.brancher(m, "doublee")
    rec = Reconnaissance(res.video)
    boucle(0.3)
    m.play()
    bascules = []

    def basculer(cle):
        avant = lec.position_ref()
        t = time.monotonic()
        lec.basculer(cle)
        bascules.append({"cle": cle, "instant": t, "position_avant": avant, "piste": pa.get(cle)})
        boucle(0.05)
        bascules[-1]["position_apres"] = lec.position_ref()

    boucle(1.5)
    basculer("beat")
    dire(meme_flux=lec.flux.get("beat") is lec.flux.get("doublage"), current_audio=pistes_gst.piste_lue(m))
    boucle(1.0)
    basculer("voix_origine")
    boucle(1.0)
    basculer("doublage")
    boucle(0.8)
    # la vidéo d'origine : un autre flux ; puis retour au doublage, dont le flux est resté ouvert
    pos = lec.position_ref()
    lec.basculer("original")
    attendre_pret(lec.media())
    e.brancher(lec.media(), "origine")
    boucle(0.8)
    dire(flux_ouverts=len({id(v) for v in lec.flux.values()}), position_origine_avant=round(pos, 3),
         position_origine=round(lec.position_ref(), 3))
    lec.basculer("doublage")
    boucle(0.6)
    basculer("beat")
    boucle(0.6)
    # fenêtre fermée : le son seul du beat (travail/instrumental.wav), puis retour sur la vidéo
    son = regles.source_son("beat", pistes.choisir_pistes(res, "mp3"), lec.sources.get("beat"))
    avant = lec.position_ref()
    lec.passer_en_son(son)
    boucle(1.0)
    pendant = lec.position_ref()
    dire(son_seul=str(son[0]) if son else None, son_seul_en_cours=str(lec.sources.get("beat", ("",))[0]),
         position_avant_son=round(avant, 3), position_son=round(pendant, 3))
    lec.revenir_en_video()
    m2 = lec.media()
    dire(retour_flux_d_avant=m2 is m, retour_piste_en_attente=pistes_gst.piste_voulue(m2),
         retour_piste_immediate=pistes_gst.piste_lue(m2))
    boucle(1.2)
    dire(retour_meme_flux=lec.flux.get("beat") is lec.flux.get("doublage"), retour_en_video=str(lec.sources["beat"][0]),
         current_audio_retour=pistes_gst.piste_lue(m2), flux_ouverts_retour=len({id(v) for v in lec.flux.values()}),
         position_retour=round(lec.position_ref(), 3), en_lecture=lec.en_lecture())
    # en pause : la piste n'est posée qu'à la reprise (playbin se bloquerait au déplacement suivant)
    lec.pause()
    boucle(0.4)
    lec.basculer("voix_origine")
    dire(pause_voulue=pistes_gst.piste_voulue(m2), pause_lue=pistes_gst.piste_lue(m2))
    lec.aller_a(3.0)
    boucle(0.6)
    lec.media().play()
    boucle(1.0)
    dire(reprise_lue=pistes_gst.piste_lue(m2), reprise_voulue=pistes_gst.piste_voulue(m2),
         position_reprise=round(lec.position_ref(), 3), en_lecture_reprise=lec.en_lecture())
    lec.liberer()
    # analyse des bascules dans le flux de la vidéo doublée
    tampons = [t for t in e.sons if t[3] == "doublee"]
    debut = [t for t in tampons if t[0] < bascules[0]["instant"] and t[1] > 0.3]
    c, ecart = rec.caler(debut, pa["doublage"])
    dire(calage=round(c, 4), ecart=ecart)
    for i, b in enumerate(bascules[:3]):
        fin = bascules[i + 1]["instant"] if i + 1 < len(bascules) else float("inf")
        avant = [rec.piste(pts, x)[0] for t, pts, x, _ in tampons if b["instant"] - 0.6 < t < b["instant"]]
        apres = [(t, pts, rec.piste(pts, x)[0]) for t, pts, x, _ in tampons if b["instant"] <= t < fin]
        premier = next(((t, pts) for t, pts, k in apres if k == b["piste"]), None)
        suite = [k for t, pts, k in apres if premier and t >= premier[0]]
        images = sum(1 for t, nom in e.images if nom == "doublee" and b["instant"] <= t < b["instant"] + 0.5)
        dire(bascule=b["cle"], piste=b["piste"], avant=max(set(avant), key=avant.count) if avant else None,
             latence=round(premier[0] - b["instant"], 3) if premier else None,
             pts=round(premier[1], 3) if premier else None,
             fidele=round(suite.count(b["piste"]) / max(1, len(suite)), 3),
             position_avant=round(b["position_avant"], 3), position_apres=round(b["position_apres"], 3),
             images_pendant=images)
    return 0


def main(argv):
    import faulthandler
    faulthandler.dump_traceback_later(80, exit=True)    # un essai bloqué dit où, et s'arrête
    Gst.init(None)
    if len(argv) == 2 and argv[0] == "flux":
        return flux(Path(argv[1]))
    if len(argv) == 2 and argv[0] == "lecteur":
        return lecteur(Path(argv[1]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
