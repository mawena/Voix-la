"""Essais de voixla.formats : mise en forme à la française."""

import time
import unittest

from voixla import formats
from voixla.formats import MOINS


def instant(annee, mois, jour, h, m, s=0):
    """Instant local (secondes depuis l'époque)."""
    return time.mktime((annee, mois, jour, h, m, s, 0, 0, -1))


class EssaiNombres(unittest.TestCase):
    def test_nombre(self):
        self.assertEqual(formats.nombre(1.5), "1,5")
        self.assertEqual(formats.nombre(-3), f"{MOINS}3")
        self.assertEqual(formats.nombre(2.0), "2")
        self.assertEqual(formats.nombre(100), "100")
        self.assertEqual(formats.nombre(1234.5), "1234,5")
        self.assertEqual(formats.nombre(0.25, 2), "0,25")
        self.assertEqual(formats.nombre(10, 0), "10")
        self.assertEqual(formats.nombre(None), "—")

    def test_zero_negatif(self):
        # −0,04 arrondi au dixième : « 0 », jamais « −0 »
        self.assertEqual(formats.nombre(-0.04), "0")
        self.assertEqual(formats.signe(-0.04), "+0")

    def test_signe_et_decibels(self):
        self.assertEqual(formats.signe(4), "+4")
        self.assertEqual(formats.signe(-13), f"{MOINS}13")
        self.assertEqual(formats.signe(0), "+0")
        self.assertEqual(formats.signe(None), "—")
        self.assertEqual(formats.db(4), "+4 dB")
        self.assertEqual(formats.db(-13.0), f"{MOINS}13 dB")
        self.assertEqual(formats.db(-1.25, 2), f"{MOINS}1,25 dB")

    def test_demi_tons(self):
        self.assertEqual(formats.demi_tons(0), "+0")
        self.assertEqual(formats.demi_tons(-12), f"{MOINS}12")
        self.assertEqual(formats.demi_tons(3.5), "+3,5")

    def test_pluriel(self):
        self.assertEqual(formats.pluriel(0, "vidéo"), "0 vidéo")
        self.assertEqual(formats.pluriel(1, "vidéo"), "1 vidéo")
        self.assertEqual(formats.pluriel(2, "vidéo"), "2 vidéos")
        self.assertEqual(formats.pluriel(3, "voix", "voix"), "3 voix")


class EssaiDurees(unittest.TestCase):
    def test_duree(self):
        self.assertEqual(formats.duree(0.4), "0,4 s")
        self.assertEqual(formats.duree(44), "44 s")
        self.assertEqual(formats.duree(233), "3 min 53 s")
        self.assertEqual(formats.duree(180), "3 min")
        self.assertEqual(formats.duree(3900), "1 h 05 min")
        self.assertEqual(formats.duree(7200), "2 h 00 min")
        self.assertEqual(formats.duree(None), "—")
        self.assertEqual(formats.duree(-5), "0 s")

    def test_duree_precise(self):
        self.assertEqual(formats.duree(5, precis=True), "5 s")
        self.assertEqual(formats.duree(2.5, precis=True), "2,5 s")
        self.assertEqual(formats.duree(44, precis=True), "44 s")

    def test_duree_courte(self):
        self.assertEqual(formats.duree_courte(3), "≈ 5 s")
        self.assertEqual(formats.duree_courte(42), "≈ 40 s")
        self.assertEqual(formats.duree_courte(360), "≈ 6 min")
        self.assertEqual(formats.duree_courte(4200), "≈ 1 h 10 min")
        self.assertEqual(formats.duree_courte(-3), "≈ 5 s")
        self.assertEqual(formats.duree_courte(None), "—")

    def test_chrono(self):
        self.assertEqual(formats.chrono(65), "1:05")
        self.assertEqual(formats.chrono(750), "12:30")
        self.assertEqual(formats.chrono(3723), "1:02:03")
        self.assertEqual(formats.chrono(-3), "0:00")
        self.assertEqual(formats.chrono(None), "—")

    def test_chrono_precis(self):
        self.assertEqual(formats.chrono_precis(65.3), "1:05,3")
        self.assertEqual(formats.chrono_precis(5), "0:05,0")
        self.assertEqual(formats.chrono_precis(-1), "0:00,0")
        self.assertEqual(formats.chrono_precis(None), "—")

    # Bogue corrigé, gardé en non-régression : 59,97 s s'écrit « 0:60,0 » (le dixième est arrondi après la division par 60). Correction proposée :
    # arrondir d'abord au dixième, s = round(max(0.0, float(s)), 1), puis divmod(s, 60).
    def test_chrono_precis_arrondi_a_la_minute(self):
        self.assertEqual(formats.chrono_precis(59.97), "1:00,0")

    def test_lire_secondes(self):
        self.assertEqual(formats.lire_secondes("1:05"), 65)
        self.assertEqual(formats.lire_secondes("1:05,5"), 65.5)
        self.assertEqual(formats.lire_secondes("65"), 65)
        self.assertEqual(formats.lire_secondes(" 12,5 "), 12.5)
        self.assertEqual(formats.lire_secondes("1:02:03"), 3723)
        self.assertIsNone(formats.lire_secondes(""))
        self.assertIsNone(formats.lire_secondes(None))
        self.assertIsNone(formats.lire_secondes("abc"))
        self.assertIsNone(formats.lire_secondes(f"{MOINS}5"))
        self.assertIsNone(formats.lire_secondes("-1:05"))

    # Bogue corrigé, gardé en non-régression : la docstring promet « 1 min 5 » → 65 s, mais lire_secondes rend None (float("1 min 5") échoue).
    # Correction proposée : reconnaître « (\d+) ?h », « (\d+) ?min », « (\d+(?:[.,]\d+)?) ?s? » avant float(),
    # ou retirer cet exemple de la docstring.
    def test_lire_secondes_minutes_en_toutes_lettres(self):
        self.assertEqual(formats.lire_secondes("1 min 5"), 65)


class EssaiDates(unittest.TestCase):
    def test_date(self):
        maintenant = instant(2026, 11, 4, 12, 0)
        self.assertEqual(formats.date(instant(2026, 11, 4, 9, 5), maintenant), "aujourd’hui 09:05")
        self.assertEqual(formats.date(instant(2026, 11, 3, 23, 30), maintenant), "hier 23:30")
        self.assertEqual(formats.date(instant(2026, 11, 1, 18, 41), maintenant), "01/11/2026 18:41")
        self.assertEqual(formats.date(instant(2025, 11, 4, 9, 5), maintenant), "04/11/2025 09:05")
        self.assertEqual(formats.date(0, maintenant), "—")
        self.assertEqual(formats.date(None), "—")

    def test_date_iso(self):
        self.assertEqual(formats.date_iso("2026-11-03"), "03/11/2026")
        self.assertEqual(formats.date_iso("2026-11-03T18:41:00+01:00"), "03/11/2026")
        self.assertEqual(formats.date_iso("03/11/2026"), "03/11/2026")
        self.assertEqual(formats.date_iso(""), "—")
        self.assertEqual(formats.date_iso(None), "—")


if __name__ == "__main__":
    unittest.main()
