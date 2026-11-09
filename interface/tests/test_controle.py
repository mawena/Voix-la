"""Essais de voixla.controle : contrôle qualité des enregistrements, sur des sons de quelques secondes générés par
ffmpeg dans un dossier temporaire (sinus et bruit faible, saturé, trop faible, bruité, trop de silences, trop court)."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from voixla import controle
from voixla.formats import MOINS

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def generer(sortie, sinus_s, total_s, volume, bruit=0.0):
    """Sinus 440 Hz (amplitude 1/8 × volume) pendant sinus_s, puis silence jusqu'à total_s, plus un bruit blanc
    d'amplitude « bruit » sur toute la durée ; mono 48 kHz 16 bits."""
    entrees = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={sinus_s}:sample_rate=48000"]
    if bruit:
        entrees += ["-f", "lavfi", "-i", f"anoisesrc=duration={total_s}:color=white:amplitude={bruit}:sample_rate=48000:seed=42"]
        graphe = f"[0:a]volume={volume},apad=whole_dur={total_s}[s];[s][1:a]amix=inputs=2:normalize=0:duration=longest"
    else:
        graphe = f"[0:a]volume={volume},apad=whole_dur={total_s}"
    subprocess.run(["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *entrees,
                    "-filter_complex", graphe, "-ac", "1", "-c:a", "pcm_s16le", str(sortie)], check=True, timeout=60)


SONS = {
    "propre": (3, 4, 2, 0.001),        # crête −12 dBFS, pauses sur un fond à −65 dBFS
    "sature": (3, 3, 16, 0.0),         # écrêté à 0 dBFS
    "faible": (3, 4, 0.04, 0.0002),    # vers −50 dBFS
    "bruite": (4, 4, 2, 0.08),         # fond à −15 dBFS, sans pause
    "silences": (1, 4, 2, 0.001),      # 75 % de silence
    "court": (0.3, 0.3, 2, 0.0),
}


@unittest.skipUnless(FFMPEG, "ffmpeg ou ffprobe absent")
class EssaiControle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="voixla-sons-")
        cls.dossier = Path(cls._tmp.name)
        cls.chant, cls.parole = {}, {}
        for nom, (sinus_s, total_s, volume, bruit) in SONS.items():
            f = cls.dossier / f"{nom}.wav"
            generer(f, sinus_s, total_s, volume, bruit)
            cls.chant[nom] = controle.controle_chant(f)
            cls.parole[nom] = controle.controle_parole(f)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_mesures(self):
        m = self.chant["propre"]
        self.assertAlmostEqual(m["duree_s"], 4.0, delta=0.05)
        self.assertAlmostEqual(m["crete_db"], -12.0, delta=0.5)
        self.assertAlmostEqual(m["moyen_db"], -16.3, delta=1.0)
        self.assertAlmostEqual(m["lufs"], -16.0, delta=1.5)
        self.assertEqual(len(m["niveaux"]), 40)                 # fenêtres de 100 ms
        self.assertEqual(m["niveaux"], sorted(m["niveaux"]))
        self.assertLess(m["bruit_db"], -55)
        self.assertAlmostEqual(m["silence_pct"], 25.0, delta=3)

    def test_prise_propre(self):
        # seule la durée est en cause (quelques secondes au lieu de 30 s au moins)
        self.assertEqual(self.chant["propre"]["alertes"], ["Prise trop courte (4 s) : visez la durée prévue."])
        self.assertEqual(self.parole["propre"]["alertes"], ["Trop court (4 s) : 30 à 60 s de parole sont conseillées."])
        self.assertFalse(self.chant["propre"]["ok"])

    def test_saturation(self):
        self.assertGreater(self.chant["sature"]["crete_db"], -1)
        self.assertTrue(self.chant["sature"]["alertes"][0].startswith("Saturation : crête proche de 0 dBFS"))
        self.assertIn("Ça sature : éloignez-vous un peu ou baissez le volume d’entrée.", self.parole["sature"]["alertes"])

    def test_trop_faible(self):
        a = self.chant["faible"]["alertes"]
        self.assertTrue(a[0].startswith(f"Niveau trop faible ({MOINS}"), a)
        self.assertIn(" LUFS) : rapprochez-vous du micro", a[0])
        self.assertTrue(self.parole["faible"]["alertes"][0].startswith("Trop faible : rapprochez-vous du micro"))
        self.assertEqual(len(a), 2)                              # faible et court, rien d'autre

    def test_bruit_de_fond(self):
        a = self.chant["bruite"]["alertes"]
        self.assertTrue(a[0].startswith(f"Bruit de fond trop haut ({MOINS}"), a)
        self.assertIn("dBFS) : pièce plus calme", a[0])
        self.assertIn("Trop de bruit de fond par rapport à la voix", self.parole["bruite"]["alertes"][0])

    def test_trop_de_silence(self):
        self.assertAlmostEqual(self.chant["silences"]["silence_pct"], 75.0, delta=3)
        self.assertEqual(self.chant["silences"]["alertes"][0],
                         "Trop de silence (75 %) : enchaînez davantage les phrases, moins de pauses.")

    def test_trop_court_pour_l_analyse(self):
        self.assertEqual(self.chant["court"]["alertes"], ["prise trop courte pour être analysée"])
        self.assertFalse(self.chant["court"]["ok"])

    def test_duree_demandee(self):
        r = controle.controle_chant(self.dossier / "propre.wav", duree_demandee=6)
        self.assertEqual(r["alertes"], ["Prise trop courte (4 s) : visez la durée prévue."])

    def test_fichier_illisible(self):
        absent = self.dossier / "absent.wav"
        self.assertIsNone(controle.duree(absent))
        self.assertEqual(controle.controle_parole(absent)["alertes"], ["enregistrement illisible"])
        self.assertEqual(controle.controle_chant(absent)["alertes"], ["prise trop courte pour être analysée"])

    def test_prises_chant(self):
        d = self.dossier / "prises"
        d.mkdir(exist_ok=True)
        shutil.copy(self.dossier / "propre.wav", d / "chant-01.wav")
        shutil.copy(self.dossier / "court.wav", d / "chant-02.wav")
        shutil.copy(self.dossier / "court.wav", d / "parole.wav")
        lien = self.dossier / "lien-vers-prises"
        if not lien.exists():
            os.symlink(d, lien)
        prises = controle.prises_chant([d, lien, self.dossier / "absent", str(d)])
        self.assertEqual([p.name for p, _ in prises], ["chant-01.wav", "chant-02.wav"])     # sans doublon
        self.assertAlmostEqual(prises[0][1], 4.0, delta=0.05)
        self.assertAlmostEqual(prises[1][1], 0.3, delta=0.05)
        self.assertEqual(controle.prises_chant([]), [])

    def test_prises_importees_par_la_seance(self):
        # les fichiers du téléphone importés par la séance d'enregistrement comptent aussi, quel que soit leur format
        d = self.dossier / "prises-importees"
        d.mkdir(exist_ok=True)
        shutil.copy(self.dossier / "propre.wav", d / "chant-01.wav")
        shutil.copy(self.dossier / "court.wav", d / "20261109-153012-notes.m4a")
        shutil.copy(self.dossier / "court.wav", d / "20261109-153500-chanson.MP3")
        shutil.copy(self.dossier / "court.wav", d / "autre.m4a")
        (d / "chant-notes.txt").write_text("pas un son")
        noms = [p.name for p, _ in controle.prises_chant([d])]
        self.assertEqual(noms, ["20261109-153012-notes.m4a", "20261109-153500-chanson.MP3", "chant-01.wav"])


class EssaiAvis(unittest.TestCase):
    def test_avis_total(self):
        self.assertEqual(controle.avis_total(0), "Encore au moins 5 min pour atteindre le minimum.")
        self.assertEqual(controle.avis_total(120), "Encore au moins 3 min pour atteindre le minimum.")
        self.assertEqual(controle.avis_total(300), "Minimum atteint ; vous pouvez continuer jusqu’à l’objectif haut.")
        self.assertEqual(controle.avis_total(599), "Minimum atteint ; vous pouvez continuer jusqu’à l’objectif haut.")
        self.assertEqual(controle.avis_total(600), "Objectif atteint, c’est largement suffisant.")

    # Bogue corrigé, gardé en non-régression : à moins de 30 s du minimum, l'avis dit « Encore au moins 0 min » (round). Correction proposée :
    # math.ceil((OBJECTIF_CHANT_MIN_S - total_s) / 60), ou les secondes quand il reste moins d'une minute.
    def test_avis_juste_avant_le_minimum(self):
        self.assertEqual(controle.avis_total(280), "Encore au moins 1 min pour atteindre le minimum.")


if __name__ == "__main__":
    unittest.main()
