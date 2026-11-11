"""Essais de la frise d'octave à trois voix ou plus : couleur de chaque chanteur."""

import unittest

try:
    from voixla.gui import frise
except Exception:   # noqa: BLE001 — GTK absent
    frise = None


@unittest.skipIf(frise is None, "GTK indisponible")
class EssaiFriseChanteurs(unittest.TestCase):
    def seg(self, rang):
        return {"classe": "chanteur", "voix": f"chanteur {rang}", "rang": rang, "voix_cible": f"voix_{rang}"}

    def test_couleurs_distinctes_et_stables(self):
        couleurs = [frise.couleur(self.seg(k)) for k in range(1, 7)]
        self.assertEqual(len(set(couleurs)), 6)
        self.assertEqual(couleurs, [frise.couleur(self.seg(k)) for k in range(1, 7)])
        for c in couleurs:
            self.assertNotIn(c, frise.COULEURS.values())

    def test_genre_et_parole_inchanges(self):
        self.assertEqual(frise.couleur({"classe": "homme", "voix": "homme"}), frise.COULEURS["homme"])
        self.assertEqual(frise.couleur({"classe": "locuteur", "voix": "voix 2"}), frise.LOCUTEURS[1])
        self.assertIsNone(frise.couleur({"classe": "muet", "voix": "muet"}))


if __name__ == "__main__":
    unittest.main()
