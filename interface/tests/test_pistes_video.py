"""Essais des pistes en vidéo (voixla.pistes) : le beat et les voix seules lus sur l'image de la vidéo doublée, par une
copie en cache (flux vidéo copié sans réencodage, son de la piste). Calage lu dans la vidéo doublée (amorce AAC du
mkv, Opus du webm), commande ffmpeg, cache (clé, invalidation, purge), choix des pistes, repli quand la copie est
impossible, ligne de commande ; de bout en bout, sur de petites vidéos de synthèse remises en son par les fonctions de
doubler.py : même flux vidéo (mêmes paquets, même nombre d'images) et son calé comme celui du doublage.

Vidéos et sons de synthèse par ffmpeg (sous nice), de quelques secondes, dans des dossiers temporaires ; titres et voix
fictifs ; le cache va dans le dossier temporaire (VOIXLA_CACHE). Rien n'est lu ni écrit dans de vraies données."""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from voixla import chemins, pistes, rapports
from voixla import lecture as regles

from .commun import VRAI_DOUBLER
from .test_pistes import (RAPPORT_CHANT, RAPPORT_PAROLE, TOUT, EssaiAvecCache, charger_doubler, ecrire, ffmpeg,
                          position_ref, sources_avant, temps_source)

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
SR = 44100
TRAME_AAC = 1024


def info(fmt, *flux):
    """Une réponse de ffprobe (-show_format -show_streams) réduite à ce que calage_video lit."""
    return {"format": fmt, "streams": [{"index": i, **f} for i, f in enumerate(flux)]}


def nice_ffmpeg(*args):
    subprocess.run(["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    *map(str, args)], check=True, timeout=120, stdin=subprocess.DEVNULL)


# --- Calage --------------------------------------------------------------------------------------------------------

def aac(**k):
    return {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", **k}


class EssaiCalage(unittest.TestCase):
    """Le son de la piste commence où commence le mix : premier paquet du son doublé + amorce de l'encodeur."""

    def test_mp4_amorce_cachee_par_la_liste_d_edition(self):
        # mp4 : le premier paquet (l'amorce) est à −1024/44100 s, une liste d'édition la cache (initial_padding 0,
        # start_time 0) : le mix commence à 0
        c = pistes.calage_video(info({"start_time": "0.000000", "duration": "45.160000"},
                                     {"codec_type": "video", "start_time": "0.000000"},
                                     aac(start_time="0.000000", initial_padding=0)), premier_son=-1024 / 44100)
        self.assertEqual((c.index, c.fin, c.taux), (0, 45.16, 44100))
        self.assertAlmostEqual(c.debut_son, 0.0, places=9)

    def test_mkv_amorce_annoncee(self):
        # mkv : le conteneur annonce l'amorce (1024 échantillons) sans l'effacer ; temps arrondis à la ms
        c = pistes.calage_video(info({"start_time": "-0.023000", "duration": "14.046000"},
                                     {"codec_type": "video", "start_time": "0.023000"},
                                     aac(start_time="-0.023000", initial_padding=1024)), premier_son=-0.023)
        self.assertAlmostEqual(c.debut_son, -0.023 + 1024 / 44100, places=9)
        self.assertAlmostEqual(c.fin, 14.023, places=9)

    def test_mkv_start_time_trompeur(self):
        # vrai extrait mkv (avec sous-titres) : start_time du son à 0, mais son premier paquet à −23 ms ; c'est lui
        # qui compte (lire start_time décalait la copie d'une trame AAC, 23 ms)
        c = pistes.calage_video(info({"start_time": "0.000000", "duration": "40.68"},
                                     {"codec_type": "video", "start_time": "0.000000"},
                                     aac(start_time="0.000000", initial_padding=1024),
                                     {"codec_type": "subtitle", "start_time": "0.000000"}), premier_son=-0.023)
        self.assertAlmostEqual(c.debut_son, -0.023 + 1024 / 44100, places=9)

    def test_son_en_retard(self):
        # decalage_son de source.json (le son d'origine part après l'image), repris par doubler
        c = pistes.calage_video(info({"start_time": "0.000000", "duration": "14.215"},
                                     {"codec_type": "video", "start_time": "0.000000"},
                                     aac(start_time="0.154000", initial_padding=1024)), premier_son=0.154)
        self.assertAlmostEqual(c.debut_son, 0.154 + 1024 / 44100, places=9)

    def test_webm_amorce_opus_a_48_khz(self):
        opus = {"codec_type": "audio", "codec_name": "opus", "sample_rate": "48000", "start_time": "-0.007000"}
        c = pistes.calage_video(info({"start_time": "-0.007000", "duration": "14.016"},
                                     {"codec_type": "video", "start_time": "0.007000"},
                                     {**opus, "initial_padding": 312}), premier_son=-0.007)
        self.assertAlmostEqual(c.debut_son, -0.007 + 312 / 48000, places=9)
        self.assertEqual(c.taux, 48000)
        # amorce non annoncée : celle de libopus
        c = pistes.calage_video(info({"duration": "14"}, {"codec_type": "video"}, opus), premier_son=-0.007)
        self.assertAlmostEqual(c.debut_son, -0.007 + 312 / 48000, places=9)

    def test_image_jointe_et_son_par_defaut(self):
        i = info({"start_time": "0", "duration": "10"},
                 {"codec_type": "video", "disposition": {"attached_pic": 1}},
                 {"codec_type": "video", "start_time": "0"},
                 aac(disposition={"default": 0}),
                 {"codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": "44100",
                  "disposition": {"default": 1}})
        self.assertEqual(pistes.flux_son(i)["index"], 3)
        c = pistes.calage_video(i, premier_son=0.25)
        self.assertEqual((c.index, c.debut_son), (1, 0.25))      # pas d'amorce connue pour ce codec

    def test_sans_son_la_piste_part_avec_l_image(self):
        c = pistes.calage_video(info({"start_time": "0", "duration": "10"}, {"codec_type": "video",
                                                                            "start_time": "0.040"}))
        self.assertEqual((c.debut_son, c.taux), (0.04, None))

    def test_refus(self):
        with self.assertRaisesRegex(pistes.ErreurPistes, "pas d’image"):
            pistes.calage_video(info({"duration": "10"}, aac()), premier_son=0.0)
        with self.assertRaisesRegex(pistes.ErreurPistes, "durée"):
            pistes.calage_video(info({}, {"codec_type": "video"}))
        with self.assertRaisesRegex(pistes.ErreurPistes, "durée"):
            pistes.calage_video(info({"duration": "N/A"}, {"codec_type": "video"}))
        with self.assertRaisesRegex(pistes.ErreurPistes, "premier paquet"):
            pistes.calage_video(info({"duration": "10"}, {"codec_type": "video"}, aac()))


# --- Commande ffmpeg -------------------------------------------------------------------------------------------------

class EssaiCommande(unittest.TestCase):
    def test_mp4(self):
        c = pistes.commande_video("/s/doublee.mp4", "/t/instrumental.wav", "/c/.x.tmp.mp4",
                                  pistes.CalageVideo(0, 0.0, 45.16, 44100))
        self.assertEqual(c[:3], ["nice", "-n", "10"])
        self.assertEqual(c[-1], "/c/.x.tmp.mp4")
        i_video, i_son = c.index("/s/doublee.mp4"), c.index("/t/instrumental.wav")
        self.assertEqual(c[i_video - 1], "-i")
        self.assertLess(c.index("-copyts"), i_video)               # temps de la vidéo doublée gardés
        self.assertNotIn("-itsoffset", c)                           # son au début : rien à décaler
        self.assertEqual(c[c.index("-t") + 1], "45.160000")         # coupé à la fin de la vidéo
        self.assertTrue(i_video < c.index("-t") < i_son)            # -t porte sur le son seul
        self.assertEqual(c[c.index("-c:v") + 1], "copy")            # image sans réencodage
        cartes = [c[i + 1] for i, x in enumerate(c) if x == "-map"]
        self.assertEqual(cartes, ["0:0", "1:a:0"])
        self.assertEqual(c[c.index("-c:a") + 1:c.index("-c:a") + 4], ["aac", "-b:a", "256k"])
        self.assertEqual(c[c.index("-ar") + 1], "44100")

    def test_mkv_decale(self):
        cal = pistes.CalageVideo(1, -0.023 + 1024 / 44100, 7.097, 44100)
        c = pistes.commande_video("/s/d.mkv", "/t/b.wav", "/c/x.mkv", cal)
        i = c.index("-itsoffset")
        self.assertEqual(c[i + 1], "0.000220")
        self.assertTrue(c.index("/s/d.mkv") < i < c.index("/t/b.wav"))   # décale le son, pas l'image
        self.assertEqual(c[c.index("-t") + 1], f"{7.097 - cal.debut_son:.6f}")
        self.assertIn("0:1", c)

    def test_webm_opus(self):
        c = pistes.commande_video("/s/d.webm", "/t/b.wav", "/c/x.webm", pistes.CalageVideo(0, 0.0, 5.0, 48000))
        self.assertEqual(c[c.index("-c:a") + 1:c.index("-c:a") + 4], ["libopus", "-b:a", "192k"])
        self.assertNotIn("-ar", c)

    def test_conteneur(self):
        self.assertEqual(pistes.ext_video("/x/a.MP4"), ".mp4")
        self.assertEqual(pistes.ext_video("/x/a.webm"), ".webm")
        self.assertEqual(pistes.ext_video("/x/a.avi"), ".mkv")       # comme doubler


# --- Cache -----------------------------------------------------------------------------------------------------------

class EssaiCacheVideo(EssaiAvecCache):
    def test_cle_et_invalidation(self):
        r = self.doublage(fichiers=TOUT, rapport=RAPPORT_CHANT)
        ecrire(r.video, b"video doublee")
        beat, vs = r.travail / "instrumental.wav", r.travail / "voix_source.wav"
        f = pistes.chemin_video(r.video, pistes.identite_son(beat))
        self.assertEqual((f.parent, f.suffix), (self.cache / "videos", ".mp4"))
        self.assertEqual(pistes.chemin_video(r.video, pistes.identite_son(beat)), f)
        self.assertNotEqual(pistes.chemin_video(r.video, pistes.identite_son(vs)), f)
        self.assertIsNone(pistes.video_prete(r.video, son=beat))
        ecrire(f, b"copie")
        self.assertEqual(pistes.video_prete(r.video, son=beat), f)
        # un remix refait le beat (taille et date) : la copie ne correspond plus
        beat.write_bytes(b"beat refait")
        self.assertIsNone(pistes.video_prete(r.video, son=beat))
        # … et la vidéo doublée
        f2 = pistes.chemin_video(r.video, pistes.identite_son(beat))
        ecrire(f2, b"copie")
        r.video.write_bytes(b"video doublee refaite")
        self.assertIsNone(pistes.video_prete(r.video, son=beat))
        self.assertIsNone(pistes.video_prete(self.tmp / "absente.mp4", son=beat))

    def test_ma_voix_par_le_nom_de_sa_voix_en_cache(self):
        r = self.doublage(fichiers=TOUT, rapport=RAPPORT_CHANT)
        plan = pistes.plan_ma_voix(r.dossier_voix)
        ident = pistes.identite_son(voix=r.dossier_voix)
        self.assertEqual(ident, "voix:" + pistes.chemin_ma_voix(plan).name)   # sans que la voix soit calculée
        autre = json.loads(json.dumps(RAPPORT_CHANT))
        autre["mix"]["gains_db"]["seche"] = 2.0
        self.assertNotEqual(pistes.identite_son(voix=r.dossier_voix, rapport=autre), ident)
        brute = self.doublage(titre="Ancien", fichiers=["jules/voix_convertie.flac"], rapport={"mode": "chant"})
        self.assertIn("voix_convertie.flac", pistes.identite_son(voix=brute.dossier_voix))
        with self.assertRaises(pistes.ErreurPistes):
            pistes.identite_son(voix=self.doublage(titre="Rien", rapport=RAPPORT_CHANT).dossier_voix)

    def test_purge_garde_la_copie_qu_on_vient_de_faire(self):
        d = self.cache / "videos"
        d.mkdir(parents=True)
        for i in range(3):
            f = d / f"{i}.mp4"
            f.write_bytes(b"x" * 1024 * 1024)
            os.utime(f, (1000 + i, 1000 + i))
        neuve = d / "neuve.mp4"
        neuve.write_bytes(b"x" * 3 * 1024 * 1024)
        os.utime(neuve, (10, 10))       # même la plus ancienne
        self.assertEqual(chemins.purger_dossier(d, 2, garder=(neuve,)), 3)
        self.assertEqual([p.name for p in d.iterdir()], ["neuve.mp4"])

    def test_trop_lourde(self):
        r = self.doublage(fichiers=TOUT, rapport=RAPPORT_CHANT)
        ecrire(r.video, b"x" * 4096)
        self.assertEqual(pistes.trop_lourde(r.video), "")
        with mock.patch.object(pistes, "CACHE_VIDEO_MO", 0.001):
            self.assertIn("trop lourde", pistes.trop_lourde(r.video))
            p = pistes.choisir_pistes(r, "video")["beat"]
            self.assertFalse(p.video_a_preparer)
            self.assertIn("trop lourde", p.sans_video)
            with self.assertRaisesRegex(pistes.ErreurPistes, "trop lourde"):
                pistes.preparer_video(r.video, son=r.travail / "instrumental.wav")


# --- Choix des pistes ------------------------------------------------------------------------------------------------

class EssaiChoixVideo(EssaiAvecCache):
    def test_a_preparer(self):
        r = self.doublage(fichiers=TOUT, rapport=RAPPORT_CHANT)
        p = pistes.choisir_pistes(r, "video")
        for c in pistes.SANS_IMAGE:
            self.assertTrue(p[c].video_a_preparer, c)
            self.assertIsNone(p[c].video)
            self.assertFalse(p[c].avec_image)
        self.assertEqual(p["beat"].a_lire, r.travail / "instrumental.wav")   # le son seul en attendant
        self.assertIsNone(p["ma_voix"].a_lire)                                # la voix seule se prépare d'abord
        for c in ("doublage", "original"):
            self.assertFalse(p[c].video_a_preparer)
            self.assertTrue(p[c].avec_image)

    def test_copies_pretes(self):
        r = self.doublage(fichiers=TOUT, rapport=RAPPORT_CHANT)
        beat = pistes.chemin_video(r.video, pistes.identite_son(r.travail / "instrumental.wav"))
        voix = pistes.chemin_video(r.video, pistes.identite_son(voix=r.dossier_voix))
        ecrire(beat, b"copie")
        ecrire(voix, b"copie")
        p = pistes.choisir_pistes(r, "video")
        self.assertEqual((p["beat"].video, p["beat"].a_lire, p["beat"].avec_image), (beat, beat, True))
        self.assertFalse(p["beat"].video_a_preparer)
        self.assertEqual(p["beat"].decalage, 0.0)          # la copie a les temps de la vidéo doublée
        # ma voix seule : la copie suffit, même si sa voix en FLAC n'est pas (ou plus) en cache
        self.assertEqual((p["ma_voix"].a_lire, p["ma_voix"].disponible), (voix, True))
        self.assertTrue(p["voix_origine"].video_a_preparer)

    def test_mp3_et_doublage_sans_video(self):
        r = self.doublage(fichiers=TOUT, rapport=RAPPORT_CHANT)
        p = pistes.choisir_pistes(r, "mp3")
        for c in pistes.SANS_IMAGE:
            self.assertFalse(p[c].video_a_preparer or p[c].video)
            self.assertEqual(p[c].sans_video, "")            # MP3 choisi : pas d'écran, rien à expliquer
        r2 = self.doublage(titre="Son seul", fichiers=TOUT, rapport=RAPPORT_CHANT, video=False)
        for forme in ("mp3", "video"):
            p = pistes.choisir_pistes(r2, forme)
            self.assertIn("pas de vidéo doublée", p["beat"].sans_video)
            self.assertFalse(p["beat"].video_a_preparer)
            self.assertEqual(p["beat"].a_lire, r2.travail / "instrumental.wav")

    def test_piste_absente(self):
        r = self.doublage(fichiers=["original.wav"], rapport=RAPPORT_CHANT)
        p = pistes.choisir_pistes(r, "video")["beat"]
        self.assertFalse(p.disponible or p.video_a_preparer)
        self.assertEqual(p.sans_video, "")


# --- Préparation, repli, ligne de commande ------------------------------------------------------------------------------

def video_de_synthese(chemin, duree=3.0, image=True, son=True, ext=None):
    """Une petite vidéo (320×180, 25 i/s, image clé toutes les secondes, images B) avec un bruit rose en son."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    ext = ext or chemin.suffix
    entrees, cartes = [], []
    if image:
        entrees += ["-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=25:duration={duree}"]
        cartes += ["-map", f"{len(cartes) // 2}:v"]
        cartes += (["-c:v", "libvpx", "-b:v", "300k", "-deadline", "realtime", "-g", "25"] if ext == ".webm" else
                   ["-c:v", "libx264", "-preset", "veryfast", "-g", "25", "-bf", "2", "-pix_fmt", "yuv420p"])
    if son:
        n = len(entrees) // 4
        entrees += ["-f", "lavfi", "-i", f"anoisesrc=d={duree}:c=pink:r=44100:a=0.3"]
        cartes += ["-map", f"{n}:a", *(["-c:a", "libopus"] if ext == ".webm" else ["-c:a", "aac"])]
    nice_ffmpeg(*entrees, *cartes, "-shortest", chemin)
    return chemin


@unittest.skipUnless(FFMPEG, "ffmpeg absent")
class EssaiPreparerVideo(EssaiAvecCache):
    def doublage_video(self, titre="Chanson fictive", **k):
        r = self.doublage(titre=titre, fichiers=["original.wav", "voix_source.wav"], rapport=RAPPORT_PAROLE)
        video_de_synthese(r.video, **k)
        ffmpeg(r.travail / "instrumental.wav", "sine=frequency=440:sample_rate=44100", duree=3)
        ffmpeg(r.dossier_voix / "seche_voix_convertie.wav", "sine=frequency=330:sample_rate=44100", duree=3)
        return r

    def test_copie_puis_cache(self):
        r = self.doublage_video()
        avant = sorted(p.relative_to(r.dossier) for p in r.dossier.rglob("*"))
        f = pistes.preparer_video(r.video, son=r.travail / "instrumental.wav")
        self.assertEqual((f.parent, f.suffix), (self.cache / "videos", ".mp4"))
        self.assertEqual(sorted(p.relative_to(r.dossier) for p in r.dossier.rglob("*")), avant)   # rien d'écrit
        self.assertFalse(list(f.parent.glob("*.tmp*")))
        self.assertEqual(pistes.video_prete(r.video, son=r.travail / "instrumental.wav"), f)
        with mock.patch.object(pistes, "_lancer") as lancer:       # deuxième fois : depuis le cache
            self.assertEqual(pistes.preparer_video(r.video, son=r.travail / "instrumental.wav"), f)
            lancer.assert_not_called()
        p = pistes.choisir_pistes(r, "video")["beat"]
        self.assertEqual((p.video, p.a_lire), (f, f))

    def test_ma_voix_preparee_d_abord(self):
        r = self.doublage_video()
        f = pistes.preparer_video(r.video, voix=r.dossier_voix)
        self.assertTrue(f.is_file())
        self.assertIsNotNone(pistes.ma_voix_prete(r.dossier_voix))       # la voix seule est en cache aussi
        self.assertEqual(pistes.choisir_pistes(r, "video")["ma_voix"].video, f)

    def test_purge_a_la_taille(self):
        r = self.doublage_video()
        d = self.cache / "videos"
        d.mkdir(parents=True)
        vieille = d / "vieille.mp4"
        vieille.write_bytes(b"x" * 300 * 1024)
        os.utime(vieille, (1000, 1000))
        with mock.patch.object(pistes, "CACHE_VIDEO_MO", 0.2):
            f = pistes.preparer_video(r.video, son=r.travail / "instrumental.wav")
        self.assertFalse(vieille.exists())
        self.assertTrue(f.is_file())       # plus lourde que la limite, mais gardée : on va la lire

    def test_repli_sans_image(self):
        r = self.doublage_video(image=False)                       # « vidéo » qui n'a que du son
        with self.assertRaisesRegex(pistes.ErreurPistes, "pas d’image"):
            pistes.preparer_video(r.video, son=r.travail / "instrumental.wav")
        self.assertFalse((self.cache / "videos").exists() and any((self.cache / "videos").iterdir()))

    def test_repli_fichiers(self):
        r = self.doublage_video()
        with self.assertRaisesRegex(pistes.ErreurPistes, "introuvable"):
            pistes.preparer_video(self.tmp / "absente.mp4", son=r.travail / "instrumental.wav")
        with self.assertRaisesRegex(pistes.ErreurPistes, "introuvable"):
            pistes.preparer_video(r.video, son=r.travail / "absent.wav")
        ecrire(r.travail / "illisible.wav", b"pas du son")
        with self.assertRaises(pistes.ErreurPistes):                # ffmpeg échoue : rien ne reste en cache
            pistes.preparer_video(r.video, son=r.travail / "illisible.wav")
        self.assertFalse(list((self.cache / "videos").glob("*")))
        ecrire(r.video, b"abimee")
        with self.assertRaisesRegex(pistes.ErreurPistes, "illisible"):
            pistes.preparer_video(r.video, son=r.travail / "instrumental.wav")

    def lancer(self, *argv):
        sortie, erreur = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(erreur):
            code = pistes.main(list(map(str, argv)))
        return code, sortie.getvalue(), erreur.getvalue()

    def test_ligne_de_commande(self):
        r = self.doublage_video()
        code, sortie, _ = self.lancer("video", r.video, r.travail / "instrumental.wav")
        self.assertEqual(code, 0)
        self.assertTrue(Path(json.loads(sortie)["video"]).is_file())
        code, sortie, _ = self.lancer("video", r.video, "--voix", r.dossier_voix)
        self.assertEqual(code, 0)
        self.assertTrue(Path(json.loads(sortie)["video"]).is_file())
        code, _, erreur = self.lancer("video", self.tmp / "absente.mp4", r.travail / "instrumental.wav")
        self.assertEqual(code, 1)
        self.assertIn("erreur", erreur)
        for argv in (("video", r.video), ("video", r.video, "--voix"), ("video", r.video, "a", "b")):
            self.assertEqual(self.lancer(*argv)[0], 2, argv)


# --- De bout en bout : même image, son calé comme celui du doublage --------------------------------------------------

def paquets_video(f):
    """Les paquets du flux vidéo (temps, taille, empreinte), temps du fichier gardés."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-copyts", "-i", str(f), "-map", "0:v:0", "-c", "copy",
                        "-f", "framemd5", "-"], capture_output=True, text=True, check=True, timeout=60)
    return [l for l in r.stdout.splitlines() if l and not l.startswith("#")]


def sonde(f):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(f)],
                       capture_output=True, text=True, check=True, timeout=60)
    return json.loads(r.stdout)


def premier_paquet(f, flux):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", flux, "-show_entries", "packet=pts_time",
                        "-read_intervals", "%+#8", "-of", "csv=p=0", str(f)], capture_output=True, text=True,
                       check=True, timeout=60)
    return min(float(x) for x in re.findall(r"^-?\d+(?:\.\d+)?", r.stdout, re.M))   # « -0.023220, » (données jointes)


def debut_mesure(f, reference):
    """Où commence reference dans le son de f, en échantillons, mesuré par corrélation dans le son décodé et
    rapporté au temps du fichier (premier paquet de son), relatif à la première image."""
    import numpy as np

    def decoder(x):
        r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(x), "-map", "0:a:0", "-ac", "1", "-ar",
                            str(SR), "-f", "f32le", "-"], capture_output=True, check=True, timeout=60)
        return np.frombuffer(r.stdout, np.float32)
    son, ref = decoder(f), decoder(reference)
    morceau = ref[SR:2 * SR]                     # une seconde, au milieu
    n = 1 << int(np.ceil(np.log2(len(son) + len(morceau))))
    c = np.fft.irfft(np.fft.rfft(son, n) * np.conj(np.fft.rfft(morceau, n)), n)[:len(son)]
    k = int(np.argmax(c))
    pic = c[k] / (np.linalg.norm(morceau) * np.linalg.norm(son[k:k + len(morceau)]) + 1e-12)
    decalage = k - SR
    return (premier_paquet(f, "a:0") - premier_paquet(f, "v:0")) * SR + decalage, float(pic)


@unittest.skipUnless(FFMPEG and VRAI_DOUBLER.is_file(), "ffmpeg ou doubler.py absent")
class EssaiBoutEnBout(EssaiAvecCache):
    """Une vidéo de synthèse, son extrait et remis par les fonctions de doubler.py (preparer_source, plan_video,
    remettre_sur_video) avec un mix = beat + voix ; puis la copie vidéo du beat. Le flux vidéo doit être le même,
    paquet pour paquet, et le beat doit commencer dans la copie là où le mix commence dans la vidéo doublée."""

    def cas(self, nom, ext, debut=None, duree=None, retard=0.0, sous_titres=False):
        import numpy as np
        doubler = charger_doubler(self.tmp / f"doubler-{nom}")
        source = self.tmp / "videos" / f"Source {nom}{ext}"
        video_de_synthese(source, duree=6.0)
        if retard or sous_titres:
            brut = source.with_name(f"brut{ext}")
            source.rename(brut)
            entrees, cartes = ["-i", brut], ["-map", "0:v"]
            # son d'origine qui part après l'image (decalage_son dans source.json)
            entrees += [*(["-itsoffset", retard] if retard else []), "-i", brut]
            cartes += ["-map", "1:a"]
            if sous_titres:     # comme un épisode de série : une piste de sous-titres, que doubler copie
                srt = ecrire(self.tmp / "videos" / "st.srt", "1\n00:00:00,500 --> 00:00:05,000\nUn essai\n".encode())
                entrees += ["-i", srt]
                cartes += ["-map", "2:s"]
            nice_ffmpeg(*entrees, *cartes, "-c", "copy", *(["-c:s", "srt"] if sous_titres else []), source)
        d = self.donnees / "sorties" / f"Chanson {nom}"
        t = d / "travail"
        t.mkdir(parents=True)
        with contextlib.redirect_stdout(io.StringIO()):
            src = doubler.preparer_source(source, doubler.sonder(source), debut, duree, t)
            n = int(SR * (src.get("duree") or doubler.duree_s(t / "original.wav")))
            rng = np.random.default_rng(7)
            beat = rng.standard_normal(n).astype(np.float32) * 0.15
            voix = (np.sin(2 * np.pi * 220 * np.arange(n) / SR) * 0.1).astype(np.float32)
            for nom_f, x in (("instrumental.wav", beat), ("mix.wav", beat + voix)):
                (t / f"{nom_f}.f32").write_bytes(np.stack([x, x], 1).tobytes())
                nice_ffmpeg("-f", "f32le", "-ar", SR, "-ac", 2, "-i", t / f"{nom_f}.f32", "-c:a", "pcm_f32le",
                            t / nom_f)
                (t / f"{nom_f}.f32").unlink()
            plan = doubler.plan_video(t)
            doublee = doubler.remettre_sur_video(t, t / "mix.wav", d, "Chanson - voix Jules", plan=plan)
        copie = pistes.preparer_video(doublee, son=t / "instrumental.wav")
        self.assertEqual(copie.suffix, doublee.suffix)
        # même flux vidéo : mêmes paquets (empreinte, temps, taille), même nombre d'images, même codec
        a, b = paquets_video(doublee), paquets_video(copie)
        self.assertGreater(len(a), 25)
        self.assertEqual(len(a), len(b))
        self.assertEqual(a, b)
        sa, sb = sonde(doublee), sonde(copie)
        codec = [s["codec_name"] for s in sa["streams"] if s["codec_type"] == "video"]
        self.assertEqual(codec, [s["codec_name"] for s in sb["streams"] if s["codec_type"] == "video"])
        self.assertEqual([s["codec_type"] for s in sb["streams"]], ["video", "audio"])
        self.assertAlmostEqual(float(sb["format"]["duration"]), float(sa["format"]["duration"]), delta=0.05)
        # même temps pour le premier paquet de son (celui de l'amorce), au temps du conteneur près
        self.assertAlmostEqual(premier_paquet(copie, "a:0"), premier_paquet(doublee, "a:0"), delta=0.0011)
        # le son : le beat commence dans la copie là où le mix commence dans la vidéo doublée
        mix, pic_mix = debut_mesure(doublee, t / "mix.wav")
        nous, pic_beat = debut_mesure(copie, t / "instrumental.wav")
        self.assertGreater(pic_beat, 0.8)        # c'est bien le beat (le mix contient aussi la voix)
        self.assertGreater(pic_mix, 0.5)
        # exigence : à une trame audio près ; en fait au temps du conteneur près (1 ms en mkv et webm)
        self.assertLessEqual(abs(nous - mix), TRAME_AAC)
        self.assertLessEqual(abs(nous - mix), 0.0011 * SR, f"{nom} : doublage {mix:+.1f}, copie {nous:+.1f} éch.")
        self.temps_du_son(nom, d, source, copie, mix / SR, nous / SR, retard)
        return doublee, copie

    def temps_du_son(self, nom, d, source, copie, mix, beat, retard):
        """Le lecteur de la page d'un résultat compte en temps du son doublé (frise, courbe, MP3, sons seuls) : chaque
        source qui a une image est décalée du début du son en elle, mesuré (mix, beat : s depuis la première image), à
        30 ms près ; un son seul ne l'est pas. Les bascules à la même position gardent l'instant du son : de la vidéo
        au son seul (bascule Vidéo/MP3, fenêtre fermée) et retour, touche B."""
        t = d / "travail"
        ecrire(t / "jules" / "rapport.json", {"mode": "chant"})
        ecrire(d / "Chanson - voix Jules.mp3")       # vide : seul son nom compte ici
        r = rapports.lire_dossier(d, {"jules": "Jules"})[0]
        video = pistes.choisir_pistes(r, "video")
        s = pistes.sources_lecteur(video)
        self.assertEqual(s["beat"][0], copie)
        orig, pic = debut_mesure(source, t / "original.wav")      # le son d'origine extrait, dans la vidéo d'origine
        self.assertGreater(pic, 0.8)
        orig /= SR
        debuts = {"doublage": mix, "beat": beat, "original": orig}
        sons = pistes.choisir_pistes(r, "mp3")
        self.assertEqual({x[1] for x in pistes.sources_lecteur(sons).values()}, {0.0}, "en MP3 : le temps du son")
        for cle, debut in debuts.items():
            self.assertAlmostEqual(s[cle][1], debut, delta=0.03, msg=f"{nom}, {cle}")
            son = regles.source_son(cle, sons, s[cle])     # fenêtre fermée : le MP3, original.wav, le beat
            for temps in (1.0, 2.5):
                ref = position_ref(temps + debut, s[cle])  # on entend l'instant « temps » du son
                self.assertAlmostEqual(ref, temps, delta=0.03, msg=f"{nom}, {cle} : frise et courbe")
                self.assertAlmostEqual(temps_source(ref, son), temps, delta=0.03, msg=f"{nom}, {cle} → son seul")
                self.assertAlmostEqual(temps_source(position_ref(temps, son), s[cle]), temps + debut, delta=0.03,
                                       msg=f"{nom}, son seul → {cle}")
        # touche B : même instant du son dans la vidéo d'origine (deux mesures, chacune à 30 ms près)
        ref = position_ref(2.5 + mix, s["doublage"])
        self.assertAlmostEqual(temps_source(ref, s["original"]), 2.5 + orig, delta=0.06, msg=f"{nom}, touche B")
        avant = sources_avant(video)
        if retard:
            self.assertGreater(position_ref(2.5 + mix, avant["doublage"]) - 2.5, 0.1, "avant : 0,18 s trop tard")
        else:
            self.assertEqual(s, avant, f"{nom} : inchangé sans retard du son d'origine")

    def test_mp4_entier(self):
        self.cas("mp4-entier", ".mp4")

    def test_mp4_extrait_sur_image_cle(self):
        self.cas("mp4-extrait", ".mp4", debut=2.4, duree=3.0)

    def test_mkv_extrait_amorce_aac(self):
        # extrait mkv : doubler cherche 0,13 s plus loin et décale le mix d'autant, puis le conteneur recale tout
        self.cas("mkv-extrait", ".mkv", debut=2.4, duree=3.0)

    def test_mkv_extrait_avec_sous_titres(self):
        # comme un vrai épisode : avec une piste de sous-titres, le start_time du son ne compte plus l'amorce
        self.cas("mkv-sous-titres", ".mkv", debut=2.4, duree=3.0, sous_titres=True)

    def test_mkv_son_d_origine_en_retard(self):
        self.cas("mkv-retard", ".mkv", retard=0.2)

    def test_mp4_son_d_origine_en_retard(self):
        self.cas("mp4-retard", ".mp4", retard=0.2)

    def test_webm_opus(self):
        self.cas("webm-entier", ".webm")


if __name__ == "__main__":
    unittest.main()
