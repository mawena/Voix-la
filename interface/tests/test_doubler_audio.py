"""Un doublage dont la source est un fichier audio seul (mp3, wav, flac…) : le faux doubler de bout en bout (mp3 seul,
sans vidéo), la bibliothèque (pochette générée, résultat complet), le remix, la corbeille ; et la carte d'une comparaison
dans la page Travaux (toutes les voix, pas seulement la première). Voix inventées."""

import json
import subprocess
import sys
import unittest

from voixla import bibliotheque as bib
from voixla import chemins, corbeille, moteur, pistes, rapports

from .commun import EssaiIsole, creer_voix

NOMS = {"voixa": "Voix A", "voixb": "Voix B"}


class DoublerSonSeul(EssaiIsole):
    def setUp(self):
        super().setUp()
        creer_voix(self.donnees, "voixa", "Voix A", hauteur=110.0)
        self.source = self.video("Chanson d'exemple.mp3")
        self.sorties = chemins.dossier_sorties(self.donnees)

    def doubler(self, extrait=None, **reglages):
        argv = moteur.args_doublage([self.source], {"voix": "voixa", **reglages}, self.donnees, extrait=extrait)
        return moteur.lancer(argv, moteur.environnement(self.donnees), timeout=60)

    def resultat(self, dossier="Chanson d'exemple"):
        (r,) = rapports.lire_dossier(self.sorties / dossier, NOMS)
        return r

    def test_sortie_mp3_seule(self):
        code, sortie = self.doubler()
        self.assertEqual(code, 0, sortie)
        r = self.resultat()
        self.assertTrue(r.complet)
        self.assertIsNone(r.video)
        self.assertEqual(r.mp3.name, "Chanson d'exemple - voix Voix A.mp3")
        self.assertEqual(json.loads((r.travail / "source.json").read_text())["audio_seul"], True)
        self.assertEqual(sorted(f.suffix for f in r.dossier.iterdir() if f.is_file() and f.suffix != ".lrc"), [".mp3"])

    def test_extrait_remix_et_reprise_du_dossier(self):
        code, sortie = self.doubler(extrait=(10, 30))
        self.assertEqual(code, 0, sortie)
        dossier = self.sorties / "Chanson d'exemple (extrait 0m10,0+0m30,0)"
        r = self.resultat(dossier.name)
        self.assertEqual(r.source["debut"], 10.0, "un son seul commence exactement où on le demande")
        self.assertEqual(moteur.remix_possible(r.video_source, dossier), (True, ""))
        ok, s = moteur.remixer(self.source, "voixa", dossier, self.donnees, voix_db=2.0)
        self.assertTrue(ok, s)
        self.assertEqual(self.resultat(dossier.name).rapport["mix"]["voix_db"], 2.0)
        self.assertIsNone(self.resultat(dossier.name).video)

    def test_bibliotheque_et_corbeille(self):
        self.assertEqual(self.doubler()[0], 0)
        r = self.resultat()
        v = bib.version(r, NOMS)
        self.assertTrue(v.complet)
        self.assertEqual(v.image, r.mp3, "l'image est celle du mp3 (sa pochette) ; sans pochette, une pochette générée")
        self.assertEqual(len(bib.couleurs_pochette(v.titre)), 3)
        (plan,) = corbeille.plans([r])
        self.assertTrue(plan.tout_le_dossier)
        self.assertEqual(plan.fichiers, [r.dossier])
        # plusieurs voix : le mp3 et le dossier de la voix partent, pas de vidéo à chercher
        self.assertEqual([f.name for f in corbeille._fichiers_de_la_voix(r) if f.suffix == ".mp3"], [r.mp3.name])

    def test_miniature_sans_pochette_echoue_proprement(self):
        self.assertEqual(self.doubler()[0], 0)
        r = self.resultat()
        with self.assertRaises((pistes.ErreurPistes, OSError)):
            pistes.preparer_miniature(r.mp3, "large")


class CarteComparaison(unittest.TestCase):
    def test_toutes_les_voix(self):
        try:
            from voixla.gui import page_travaux
        except (ImportError, ValueError) as e:
            self.skipTest(f"GTK indisponible : {e}")
        nom = lambda v: NOMS.get(v, v)
        t = page_travaux.resume_reglages({"reglages": {"voix": "voixb", "comparer": ["voixb", "voixa"], "pas": 30}}, nom)
        self.assertIn("comparaison de 2 voix : Voix B, Voix A", t)
        self.assertNotIn("voix Voix B,", t)
        t = page_travaux.resume_reglages({"reglages": {"voix": "voixa", "comparer": ["voixa"], "pas": 30}}, nom)
        self.assertTrue(t.startswith("voix Voix A"), t)
        t = page_travaux.resume_reglages({"reglages": {"voix": "voixa", "pas": 30}}, nom)
        self.assertTrue(t.startswith("voix Voix A"), t)


if __name__ == "__main__":
    unittest.main()
