"""La note de justesse du karaoké (voixla.justesse) : d'abord sur des hauteurs exactes (sans son), puis sur des prises
de synthèse (tests/faux_karaoke.py) passées par le même YIN que les vraies prises (voixla.analyse).

Ce qu'on attend d'une note juste et stable : 100 pour une prise juste, presque 0 pour une prise fausse d'un demi-ton,
100 une octave plus bas ou plus haut, un léger retard toléré, un grand retard compté (sauf s'il est compensé par la
latence), une ligne fausse repérée et elle seule, la même note pour la même prise, et à peine un point d'écart d'un
bruit de fond à l'autre."""

import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from voixla import analyse
from voixla import justesse as J

from . import faux_karaoke as fk

FFMPEG = shutil.which("ffmpeg")
PAS = J.PAS_S


def passage_entier():
    return J.Passage(0.0, fk.duree_chanson(), 1)


def decaler(f0, trames):
    """La prise en retard de trames (avance si négatif)."""
    if trames >= 0:
        return np.concatenate([np.zeros(trames, np.float32), f0])
    return f0[-trames:]


class TestCalculs(unittest.TestCase):

    def test_ecart_ramene_dans_l_octave(self):
        self.assertAlmostEqual(float(J.plier(1200)), 0.0)
        self.assertAlmostEqual(float(J.plier(-2400)), 0.0)
        self.assertAlmostEqual(float(J.plier(100)), 100.0)
        self.assertAlmostEqual(float(J.plier(-100)), -100.0)
        self.assertAlmostEqual(float(J.plier(700)), -500.0)       # une quinte au-dessus = une quarte en dessous
        self.assertAlmostEqual(float(J.cents(440 * 2 ** (1 / 12), 440)), 100.0, places=6)

    def test_credit(self):
        self.assertEqual(float(J.credit(0)), 1.0)
        self.assertEqual(float(J.credit(J.JUSTE_CENTS)), 1.0)
        self.assertEqual(float(J.credit(J.FAUX_CENTS)), 0.0)
        self.assertEqual(float(J.credit(400)), 0.0)
        self.assertAlmostEqual(float(J.credit((J.JUSTE_CENTS + J.FAUX_CENTS) / 2)), 0.5)

    def test_mediane_glissante_ignore_les_trous(self):
        x = np.array([np.nan, 10, 10, 300, 10, 10, np.nan])
        m = J.mediane_glissante(x, 5)
        self.assertTrue(np.isnan(m[0]) and np.isnan(m[-1]))
        self.assertEqual(m[3], 10.0)                              # un raté isolé disparaît

    def test_juste(self):
        ref = fk.f0_reference()
        r = J.noter(ref, ref.copy(), fk.lignes(), passage_entier(), 0.0)
        self.assertEqual(r["score"], 100)
        self.assertEqual(r["lignes_fausses"], 0)
        self.assertEqual(r["octave"], 0)
        self.assertEqual(len(r["tours"]), 1)
        self.assertEqual(len(r["tours"][0]["lignes"]), 8)
        self.assertEqual(r["tours"][0]["passages_faux"], [])

    def test_demi_ton(self):
        ref = fk.f0_reference()
        r = J.noter(ref, ref * 2 ** (1 / 12), fk.lignes(), passage_entier(), 0.0)
        self.assertLess(r["score"], 10)
        self.assertEqual(r["lignes_fausses"], 8)
        self.assertTrue(all("trop haut" in l["conseil"] for l in r["tours"][0]["lignes"]))
        self.assertTrue(r["tours"][0]["passages_faux"])

    def test_octaves(self):
        ref = fk.f0_reference()
        for facteur, octave in ((0.5, -1), (2.0, 1), (0.25, -2)):
            r = J.noter(ref, ref * facteur, fk.lignes(), passage_entier(), 0.0)
            self.assertEqual(r["score"], 100, facteur)
            self.assertEqual(r["octave"], octave)
            # la courbe montrée est ramenée à l'octave de l'original
            prise = np.array(r["tours"][0]["prise_hz"])
            voisee = (prise > 0) & (np.array(r["reference_hz"]) > 0)
            self.assertTrue(np.allclose(prise[voisee], np.array(r["reference_hz"])[voisee], rtol=0.01))

    def test_un_peu_en_retard_est_tolere(self):
        ref = fk.f0_reference()
        r = J.noter(ref, decaler(ref, 5), fk.lignes(), passage_entier(), 0.0)          # 100 ms
        self.assertGreaterEqual(r["score"], 98)
        self.assertAlmostEqual(r["retard_s"], 0.1, delta=0.021)

    def test_tres_en_retard_compte_sauf_compense(self):
        ref = fk.f0_reference()
        prise = decaler(ref, 20)                                                         # 400 ms
        r = J.noter(ref, prise, fk.lignes(), passage_entier(), 0.0)
        self.assertLess(r["score"], 80)
        self.assertTrue(any("en retard" in l["conseil"] for l in r["tours"][0]["lignes"]))
        r = J.noter(ref, prise, fk.lignes(), passage_entier(), -0.4)                     # latence de 400 ms
        self.assertEqual(r["score"], 100)

    def test_une_ligne_fausse(self):
        ref = fk.f0_reference()
        prise = ref.copy()
        d, f, _t = fk.lignes()[4]
        prise[int(d / PAS):int(f / PAS)] *= 2 ** (2 / 12)
        r = J.noter(ref, prise, fk.lignes(), passage_entier(), 0.0)
        notes = [l["score"] for l in r["tours"][0]["lignes"]]
        self.assertLess(notes[4], 10)
        self.assertEqual([n for i, n in enumerate(notes) if i != 4], [100.0] * 7)
        self.assertEqual(r["lignes_fausses"], 1)
        self.assertIn("trop haut (+2,0 demi-tons)", r["tours"][0]["lignes"][4]["conseil"])
        self.assertNotIn("retard", r["tours"][0]["lignes"][4]["conseil"])
        faux = r["tours"][0]["passages_faux"]
        self.assertTrue(faux and all(d - 0.1 <= p["debut"] and p["fin"] <= f + 0.1 for p in faux))
        self.assertAlmostEqual(r["score_exact"], 100 * 7 / 8, delta=2)

    def test_presence(self):
        """Ne chanter que la moitié de chaque note coûte ; souffles et consonnes (jusqu'à 40 % de trous) non."""
        ref = fk.f0_reference()
        for part, attendu in ((0.5, 100 * 0.5 / J.PRESENCE_PLEINE), (0.7, 100)):
            prise = ref.copy()
            for d, f, *_ in fk.notes():
                a, b = int(np.ceil(d / PAS)), int(f / PAS)
                prise[a + int((b - a) * part):b] = 0
            r = J.noter(ref, prise, fk.lignes(), passage_entier(), 0.0)
            self.assertAlmostEqual(r["score_exact"], attendu, delta=4, msg=part)
        r = J.noter(ref, np.zeros_like(ref), fk.lignes(), passage_entier(), 0.0)
        self.assertEqual(r["score"], 0)
        self.assertTrue(all(l["conseil"] == "pas assez chantée" for l in r["tours"][0]["lignes"]))

    def test_prise_arretee_plus_tot(self):
        ref = fk.f0_reference()
        r = J.noter(ref, ref[:int(16.0 / PAS)], fk.lignes(), passage_entier(), 0.0)
        lignes = r["tours"][0]["lignes"]
        self.assertEqual(r["score"], 100)                           # seules les lignes chantées comptent
        self.assertEqual(sum(1 for l in lignes if l.get("hors_prise")), 4)
        self.assertEqual(r["lignes_notees"], 4)

    def test_boucle(self):
        """Trois tours d'un passage : le deuxième faux d'un ton ; chacun noté à part, la moyenne au total."""
        ref = fk.f0_reference()
        p = J.Passage(14.4, 24.0, 3)
        a, b = int(round(p.debut / PAS)), int(round(p.fin / PAS))
        tour = ref[a:b]
        prise = np.concatenate([tour, tour * 2 ** (2 / 12), tour])
        r = J.noter(ref, prise, fk.lignes(), p, 0.0)
        notes = [t["score"] for t in r["tours"]]
        self.assertEqual(notes[0], 100)
        self.assertEqual(notes[2], 100)
        self.assertLess(notes[1], 10)
        self.assertEqual(r["meilleur_tour"], 0)
        self.assertAlmostEqual(r["score_exact"], sum(notes) / 3, delta=0.1)
        self.assertEqual([l["texte"] for l in r["tours"][0]["lignes"]], [t for _d, _f, t in fk.lignes()[4:6]])

    def test_sans_paroles(self):
        ref = fk.f0_reference()
        r = J.noter(ref, ref.copy(), [], passage_entier(), 0.0)
        lignes = r["tours"][0]["lignes"]
        self.assertEqual(len(lignes), 8)                            # les phrases de la mélodie
        self.assertEqual(lignes[0]["texte"], "Phrase 1")
        self.assertEqual(r["score"], 100)

    def test_meme_prise_meme_note(self):
        ref = fk.f0_reference()
        rng = np.random.default_rng(4)
        prise = ref * 2 ** (rng.normal(0, 15, len(ref)) / 1200)
        a = J.noter(ref, prise, fk.lignes(), passage_entier(), 0.0)
        b = J.noter(ref, prise.copy(), fk.lignes(), passage_entier(), 0.0)
        self.assertEqual(a, b)

    def test_stable_d_un_bruit_a_l_autre(self):
        """Hauteur bruitée de ±25 cents (écart type) trame à trame : la note ne bouge que d'un point."""
        ref = fk.f0_reference()
        notes = []
        for graine in range(6):
            rng = np.random.default_rng(graine)
            prise = ref * 2 ** (rng.normal(0, 25, len(ref)) / 1200)
            notes.append(J.noter(ref, prise, fk.lignes(), passage_entier(), 0.0)["score_exact"])
        self.assertLessEqual(max(notes) - min(notes), 1.5, notes)
        self.assertGreaterEqual(min(notes), 95)

    def test_phrases_et_textes(self):
        self.assertEqual(J.phrase_octave(0), "à la hauteur de l’original")
        self.assertIn("une octave plus bas", J.phrase_octave(-1))
        self.assertIn("2 octaves plus haut", J.phrase_octave(2))
        self.assertEqual(J.phrase_retard(0.03), "")
        self.assertEqual(J.phrase_retard(0.081), "ta voix arrive en moyenne 80 ms après l’original")
        self.assertIn("avant", J.phrase_retard(-0.1))
        self.assertEqual(J.commentaire(None), "Pas de note : rien à comparer")
        self.assertTrue(J.commentaire(95).startswith("Excellent"))


@unittest.skipUnless(FFMPEG, "ffmpeg absent")
class TestPrisesDeSynthese(unittest.TestCase):
    """De vraies prises (WAV 48 kHz) de synthèse, passées par le YIN des prises (voixla.analyse)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="voixla-justesse-")
        cls.tmp = Path(cls._tmp.name)
        cls.ref = fk.f0_reference()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def noter(self, decalage_s=0.0, **voix):
        f = fk.ecrire_wav(self.tmp / "prise.wav", fk.voix(**voix))
        f0 = analyse.analyser(f, ("f0",), ecrire_cache=False)["f0"]
        return J.noter(self.ref, f0, fk.lignes(), passage_entier(), decalage_s, analyse.PAS_F0_S)

    def test_juste(self):
        r = self.noter()
        self.assertGreaterEqual(r["score"], 97)
        self.assertEqual(r["lignes_fausses"], 0)

    def test_juste_avec_harmoniques_vibrato_et_souffle(self):
        r = self.noter(harmoniques=4, vibrato_cents=40, bruit_db=-40)
        self.assertGreaterEqual(r["score"], 90)

    def test_fausse_d_un_demi_ton(self):
        r = self.noter(transposition=1.0)
        self.assertLessEqual(r["score"], 15)
        self.assertEqual(r["lignes_fausses"], 8)
        r = self.noter(transposition=-1.0)
        self.assertLessEqual(r["score"], 15)
        self.assertTrue(all("trop bas" in l["conseil"] for l in r["tours"][0]["lignes"]))

    def test_quart_de_ton(self):
        r = self.noter(transposition=0.5)
        self.assertTrue(60 <= r["score"] <= 85, r["score"])

    def test_une_octave_plus_bas_n_est_pas_faux(self):
        r = self.noter(transposition=-12.0)
        self.assertGreaterEqual(r["score"], 97)
        self.assertEqual(r["octave"], -1)
        r = self.noter(transposition=12.0)
        self.assertGreaterEqual(r["score"], 97)
        self.assertEqual(r["octave"], 1)

    def test_en_retard(self):
        r = self.noter(retard=0.1)
        self.assertGreaterEqual(r["score"], 95)
        self.assertAlmostEqual(r["retard_s"], 0.1, delta=0.04)
        r = self.noter(retard=0.4)
        self.assertLess(r["score"], 80)
        r = self.noter(-0.4, retard=0.4)              # le même retard, compensé par la latence
        self.assertGreaterEqual(r["score"], 97)

    def test_une_ligne_fausse(self):
        r = self.noter(faux={4: 2.0})
        notes = [l["score"] for l in r["tours"][0]["lignes"]]
        self.assertLess(notes[4], 20)
        self.assertTrue(all(n >= 95 for i, n in enumerate(notes) if i != 4), notes)
        self.assertEqual(r["lignes_fausses"], 1)

    def test_stable_d_un_souffle_a_l_autre(self):
        notes = [self.noter(bruit_db=-35, vibrato_cents=25, graine=g)["score_exact"] for g in range(4)]
        self.assertLessEqual(max(notes) - min(notes), 2.0, notes)


if __name__ == "__main__":
    unittest.main()
