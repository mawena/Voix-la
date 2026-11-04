"""Essais de doubler.py hors de l'interface : version, lanceur bin/doubler (dépôt et disposition du paquet Debian),
noyau et ancres de genre trouvés à côté du code ou dans ~/.config/voix-la. Rien n'est lu ni écrit hors du dossier
temporaire : HOME y est déplacé, et doubler.py y est copié."""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import voixla

from .commun import DEPOT, VRAI_DOUBLER

LANCEUR = DEPOT / "bin" / "doubler"


@unittest.skipUnless(VRAI_DOUBLER.is_file(), "doubler.py absent")
class EssaiDoubler(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="voixla-doubler-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()

    def charger(self, depot):
        """doubler.py copié dans depot (avec kaggle/doubler_kaggle.py), chargé comme module avec HOME=self.home."""
        (depot / "kaggle").mkdir(parents=True, exist_ok=True)
        shutil.copy2(VRAI_DOUBLER, depot / "doubler.py")
        (depot / "kaggle" / "doubler_kaggle.py").write_text("")
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            os.environ.pop("VOIXLA_KAGGLE", None)
            spec = importlib.util.spec_from_file_location("doubler_copie", depot / "doubler.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module

    @staticmethod
    def ancres(fichier, valeur):
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_text(json.dumps({"homme": [valeur] * 192, "femme": [-valeur] * 192}))

    def lire_ancres(self, module):
        with contextlib.redirect_stdout(io.StringIO()) as sortie:
            return module.lire_ancres(), sortie.getvalue()

    def test_version_identique_a_l_interface(self):
        r = subprocess.run([sys.executable, str(VRAI_DOUBLER), "--version"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.split()[-1], voixla.__version__)

    def test_lanceur_du_depot(self):
        r = subprocess.run([sys.executable, str(LANCEUR), "--version"], capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout.split()[-1]), (0, voixla.__version__))

    def test_lanceur_dans_la_disposition_du_paquet(self):
        # usr/bin/doubler retrouve usr/lib/voix-la/doubler.py à côté de lui (paquet installé ou simplement extrait)
        usr = self.tmp / "usr"
        (usr / "bin").mkdir(parents=True)
        shutil.copy2(LANCEUR, usr / "bin" / "doubler")
        self.charger(usr / "lib" / "voix-la")
        r = subprocess.run([sys.executable, str(usr / "bin" / "doubler"), "--version"], capture_output=True, text=True,
                           timeout=30)
        self.assertEqual((r.returncode, r.stdout.split()[-1]), (0, voixla.__version__))

    def test_noyau_a_cote_du_code(self):
        lib = self.tmp / "usr" / "lib" / "voix-la"
        d = self.charger(lib)
        self.assertEqual(d.DEPOT, lib)
        self.assertEqual(d.SCRIPT_NOYAU, lib / "kaggle" / "doubler_kaggle.py")
        self.assertTrue(d.SCRIPT_NOYAU.is_file())

    def test_ancres_configuration_puis_depot(self):
        depot = self.tmp / "depot"
        d = self.charger(depot)
        config = self.home / ".config" / "voix-la" / "ancres_genre.json"
        self.assertEqual(d.ANCRES, (config, depot / "kaggle" / "ancres_genre.json"))
        ancres, sortie = self.lire_ancres(d)
        self.assertIsNone(ancres)
        self.assertIn("ancres de genre absentes", sortie)
        self.ancres(depot / "kaggle" / "ancres_genre.json", 1.0)
        self.assertEqual(d.fichier_ancres(), depot / "kaggle" / "ancres_genre.json")
        self.assertEqual(self.lire_ancres(d)[0]["homme"], [1.0] * 192)
        self.ancres(config, 2.0)                                   # ~/.config/voix-la passe en premier
        self.assertEqual(d.fichier_ancres(), config)
        self.assertEqual(self.lire_ancres(d)[0]["homme"], [2.0] * 192)

    def test_ancres_illisibles(self):
        d = self.charger(self.tmp / "depot")
        f = self.home / ".config" / "voix-la" / "ancres_genre.json"
        f.parent.mkdir(parents=True)
        f.write_text('{"homme": [1, 2], "femme": [3]}')
        ancres, sortie = self.lire_ancres(d)
        self.assertIsNone(ancres)
        self.assertIn("illisibles", sortie)

    def test_commande_kaggle(self):
        d = self.charger(self.tmp / "depot")
        self.assertEqual(d.KAGGLE, self.home / "kaggle-venv" / "bin" / "kaggle")
        with mock.patch.dict(os.environ, {"HOME": str(self.home), "VOIXLA_KAGGLE": "~/ailleurs/kaggle"}):
            spec = importlib.util.spec_from_file_location("doubler_kaggle_var", self.tmp / "depot" / "doubler.py")
            autre = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(autre)
        self.assertEqual(autre.KAGGLE, self.home / "ailleurs" / "kaggle")


if __name__ == "__main__":
    unittest.main()
