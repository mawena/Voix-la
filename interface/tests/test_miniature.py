"""Miniature d'une vidéo doublée (voixla/pistes.py) : l'image de MPRIS et de la barre « en cours de lecture ». Nom en
cache, instant choisi, commande ffmpeg, et une vraie miniature d'une petite vidéo de synthèse (sous nice)."""

import contextlib
import io
import json
import os
import subprocess
import unittest

from voixla import pistes

from .test_pistes import FFMPEG, EssaiAvecCache, ecrire
from .test_pistes_video import video_de_synthese


class EssaiMiniature(EssaiAvecCache):
    def test_chemin_en_cache(self):
        v = ecrire(self.tmp / "Chanson - voix Jules.mp4", b"x")
        f = pistes.chemin_miniature(v)
        self.assertEqual(f.parent, self.cache / "miniatures")
        self.assertEqual(f.suffix, ".jpg")
        self.assertEqual(f, pistes.chemin_miniature(v), "stable")
        self.assertIsNone(pistes.miniature_prete(v))
        st = v.stat()
        os.utime(v, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
        self.assertNotEqual(f, pistes.chemin_miniature(v), "vidéo refaite (remix) : autre miniature")
        self.assertIsNone(pistes.miniature_prete(self.tmp / "absente.mp4"))

    def test_instant(self):
        self.assertAlmostEqual(pistes.instant_miniature(225.0), 75.0, msg="au tiers")
        self.assertAlmostEqual(pistes.instant_miniature("12.0"), 4.0)
        self.assertEqual(pistes.instant_miniature(2.0), 1.0)
        self.assertEqual(pistes.instant_miniature(None), 0.0)
        self.assertEqual(pistes.instant_miniature("n/a"), 0.0)

    def test_commande(self):
        cmd = pistes.commande_miniature("/v/Chanson.mp4", "/c/x.jpg", 75.0)
        self.assertEqual(cmd[:3], ["nice", "-n", "10"])
        self.assertLess(cmd.index("-ss"), cmd.index("-i"), "saut rapide, avant l'ouverture")
        self.assertIn("75.000", cmd)
        self.assertEqual(cmd[cmd.index("-frames:v") + 1], "1")
        self.assertIn("crop=512:512", cmd[cmd.index("-vf") + 1])
        self.assertEqual(cmd[-1], "/c/x.jpg")

    def test_refus(self):
        with self.assertRaises(pistes.ErreurPistes):
            pistes.preparer_miniature(self.tmp / "absente.mp4")

    @unittest.skipUnless(FFMPEG, "ffmpeg absent")
    def test_vraie_miniature(self):
        v = video_de_synthese(self.tmp / "Chanson - voix Jules.mp4", duree=3.0)
        f = pistes.preparer_miniature(v)
        self.assertEqual(f, pistes.chemin_miniature(v))
        self.assertTrue(f.is_file())
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_name", "-of",
                            "csv=p=0", str(f)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "mjpeg,512,512")
        self.assertEqual(pistes.miniature_prete(v), f)
        self.assertEqual(pistes.preparer_miniature(v), f, "déjà en cache : rien à refaire")
        self.assertEqual([p for p in f.parent.iterdir() if p.suffix != ".jpg"], [], "pas de fichier temporaire")

    @unittest.skipUnless(FFMPEG, "ffmpeg absent")
    def test_sans_image(self):
        v = video_de_synthese(self.tmp / "Son seul.mp4", duree=2.0, image=False)
        with self.assertRaises(pistes.ErreurPistes):
            pistes.preparer_miniature(v)

    @unittest.skipUnless(FFMPEG, "ffmpeg absent")
    def test_ligne_de_commande(self):
        v = video_de_synthese(self.tmp / "Chanson - voix Jules.mp4", duree=2.0)
        sortie, erreur = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(erreur):
            self.assertEqual(pistes.main(["miniature", str(v)]), 0)
            self.assertEqual(pistes.main(["miniature", str(self.tmp / "absente.mp4")]), 1)
        self.assertEqual(json.loads(sortie.getvalue().splitlines()[0])["miniature"], str(pistes.miniature_prete(v)))
        self.assertIn("vidéo introuvable", erreur.getvalue())


if __name__ == "__main__":
    unittest.main()
