"""Essais de voixla.systeme : mémoire, charge, outil file-attente, jeton et commande Kaggle (une fausse commande
kaggle dans un dossier temporaire : aucun appel réseau), noyaux connus, verrou de doubler."""

import fcntl
import os
import unittest
from unittest import mock
from pathlib import Path

from voixla import chemins, systeme

from .commun import EssaiIsole


class EssaiPoste(EssaiIsole):
    def test_memoire_et_charge(self):
        dispo, totale = systeme.memoire()
        self.assertGreater(totale, 0)
        self.assertGreater(dispo, 0)
        self.assertLessEqual(dispo, totale)
        charge, coeurs = systeme.charge()
        self.assertGreaterEqual(charge, 0)
        self.assertEqual(coeurs, os.cpu_count())

    def test_file_attente_absente(self):
        os.environ["PATH"] = str(self.tmp / "vide")
        self.assertIsNone(systeme.file_attente())

    def test_file_attente(self):
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        outil = bin_ / "file-attente"
        outil.write_text('#!/bin/sh\n[ "$1" = "--etat" ] && echo "Mémoire disponible : 7.1 Go ; charge 1.7 pour 8 cœurs"\n')
        outil.chmod(0o755)
        os.environ["PATH"] = f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}"
        self.assertEqual(systeme.file_attente(), "Mémoire disponible : 7.1 Go ; charge 1.7 pour 8 cœurs")

    def test_jeton_seulement_par_son_existence(self):
        self.assertIsInstance(systeme.jeton_kaggle_present(), bool)
        self.assertEqual(systeme.JETONS[0], Path.home() / ".kaggle" / "access_token")


class EssaiKaggle(EssaiIsole):
    def fausse_kaggle(self, script):
        k = self.tmp / "kaggle"
        k.write_text("#!/bin/sh\n" + script)
        k.chmod(0o755)
        os.environ["VOIXLA_KAGGLE"] = str(k)
        return k

    def test_commande(self):
        self.assertEqual(systeme.commande_kaggle(), Path.home() / "kaggle-venv" / "bin" / "kaggle")
        os.environ["VOIXLA_KAGGLE"] = "~/autre/kaggle"
        self.assertEqual(systeme.commande_kaggle(), Path.home() / "autre" / "kaggle")

    def test_statut(self):
        self.fausse_kaggle('echo "moi/doublage-voix-gpu has status \\"KernelWorkerStatus.RUNNING\\""\n')
        statut, texte = systeme.statut_noyau("moi/doublage-voix-gpu")
        self.assertEqual(statut, "running")
        self.assertIn("KernelWorkerStatus.RUNNING", texte)
        self.fausse_kaggle('echo \'moi/x has status "complete"\'\n')
        self.assertEqual(systeme.statut_noyau("moi/x")[0], "complete")

    def test_erreurs(self):
        os.environ["VOIXLA_KAGGLE"] = str(self.tmp / "absente")
        statut, texte = systeme.statut_noyau("moi/x")
        self.assertIsNone(statut)
        self.assertIn("commande kaggle absente", texte)
        self.fausse_kaggle('echo "404 - Not Found" >&2\nexit 1\n')
        self.assertEqual(systeme.statut_noyau("moi/x"), (None, "404 - Not Found"))
        self.fausse_kaggle("exec sleep 5\n")
        self.assertEqual(systeme.statut_noyau("moi/x", timeout=0.5), (None, "pas de réponse en 0.5 s (réseau ?)"))

    def test_noyaux_connus(self):
        conf = {"kaggle": {"noyau": "moi/doublage-voix-gpu", "outils": "moi/doublage-outils"}}
        noyaux = systeme.noyaux_connus(conf)
        self.assertEqual(noyaux[:2], [("Doublage", "moi/doublage-voix-gpu"), ("Outils préparés", "moi/doublage-outils")])
        self.assertLessEqual(len(noyaux), 3)          # plus le banc d'essai, s'il est dans le dépôt
        self.assertTrue(all(lib == "Banc d’essai" for lib, _ in systeme.noyaux_connus(None)))


class EssaiVerrou(EssaiIsole):
    def test_verrou_de_doubler(self):
        # le verrou commun de doubler (~/.local/state/voix-la/verrou-kaggle) est déplacé dans le dossier d'essai
        rustine = mock.patch.object(systeme, "ETAT_DOUBLER", self.tmp / "etat-doubler")
        rustine.start()
        self.addCleanup(rustine.stop)
        self.assertEqual(systeme.verrou_doubler(self.donnees), "absent")
        f = chemins.fichier_verrou_doubler(self.donnees)
        f.parent.mkdir(parents=True)
        f.write_text("")
        self.assertEqual(systeme.verrou_doubler(self.donnees), "libre")
        with open(f, "w") as tenu:
            fcntl.flock(tenu, fcntl.LOCK_EX)              # comme un doubler en pleine session
            self.assertEqual(systeme.verrou_doubler(self.donnees), "pris")
        self.assertEqual(systeme.verrou_doubler(self.donnees), "libre")
        # le verrou commun pris suffit à dire « pris »
        commun_ = self.tmp / "etat-doubler" / "verrou-kaggle"
        commun_.parent.mkdir(parents=True)
        with open(commun_, "w") as tenu:
            fcntl.flock(tenu, fcntl.LOCK_EX)
            self.assertEqual(systeme.verrou_doubler(self.donnees), "pris")
            self.assertIsNone(systeme.session_a_reprendre())   # session en cours, pas à reprendre
        (self.tmp / "etat-doubler" / "session-en-cours.json").write_text('{"noyau": "compte/noyau"}')
        self.assertEqual(systeme.session_a_reprendre(), {"noyau": "compte/noyau"})


if __name__ == "__main__":
    unittest.main()
