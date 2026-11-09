"""Transposition (banc/realisme/transposition.py) : Rubber Band R3 par ctypes et règle du décalage de la voix.

Sons synthétiques seulement. Les essais de Rubber Band sont sautés si librubberband est absente."""
import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI.parent))

import transposition as T  # noqa: E402

NUMPY = importlib.util.find_spec('numpy') is not None
SCIPY = importlib.util.find_spec('scipy') is not None
try:
    T.bibliotheque()
    RUBBERBAND = NUMPY
except OSError:
    RUBBERBAND = False

# Tessiture inventée, même schéma que le JSON de tessiture.py (Hz)
TESSITURE = {'p05': 98.0, 'p10': 110.0, 'p50': 165.0, 'p90': 247.0, 'p95': 277.0, 'confort': [123.0, 220.0]}
TENOR = {'p10': 245.0, 'p50': 295.0, 'p90': 411.0}
FEMME_AIGUE = {'p10': 330.0, 'p50': 400.0, 'p90': 520.0}
GRAVE = {'p10': 100.0, 'p50': 130.0, 'p90': 170.0}


def f0_final(source, d):
    return {k: v * 2 ** (d / 12) for k, v in source.items()}


@unittest.skipUnless(RUBBERBAND, 'numpy et librubberband requis')
class Rubberband(unittest.TestCase):
    def sinus(self, f=440.0, duree=3.0, sr=44100):
        import numpy as np
        t = np.arange(int(duree * sr)) / sr
        x = (0.3 * np.sin(2 * np.pi * f * t) + 0.1 * np.sin(2 * np.pi * 2 * f * t)).astype('float32')
        return np.stack([x, x * 0.8], 1)

    def test_r3_est_le_moteur_actif(self):
        self.assertEqual(T.version_moteur('r3'), 3)
        self.assertEqual(T.version_moteur('r2'), 2)

    def test_duree_a_l_echantillon_pres(self):
        x = self.sinus()
        for n in (-5, -1, 2, 3, 5):
            y = T.transposer(x, n)
            self.assertEqual(y.shape, x.shape, n)

    def test_zero_demiton_rend_l_entree(self):
        x = self.sinus()
        import numpy as np
        self.assertTrue(np.array_equal(T.transposer(x, 0), x))

    def test_hauteur_juste_a_quelques_cents(self):
        import numpy as np
        sr = 44100
        x = self.sinus(440.0, 3.0)
        for n in (-3, 2, 5):
            y = T.transposer(x, n)
            seg = y[sr // 2:-sr // 2, 0] * np.hanning(len(y) - sr)
            spec = np.abs(np.fft.rfft(seg, 1 << 19))
            f = np.fft.rfftfreq(1 << 19, 1 / sr)
            mesure = f[np.argmax(spec)]
            attendu = 440.0 * 2 ** (n / 12)
            self.assertLess(abs(1200 * math.log2(mesure / attendu)), 8.0, (n, mesure, attendu))

    def test_mono_accepte(self):
        x = self.sinus()[:, 0]
        self.assertEqual(T.transposer(x, 2).shape, x.shape)

    def test_ffmpeg_repli_meme_longueur(self):
        import shutil
        if not shutil.which('ffmpeg'):
            self.skipTest('ffmpeg absent')
        x = self.sinus(duree=2.0)
        try:
            y = T.transposer_ffmpeg(x, 3)
        except Exception as e:  # noqa: BLE001 — filtre rubberband non compilé
            self.skipTest(f'filtre rubberband de ffmpeg indisponible : {e}')
        self.assertEqual(y.shape, x.shape)

    @unittest.skipUnless(SCIPY, 'scipy requis')
    def test_mesure_de_qualite(self):
        x = self.sinus(duree=4.0)
        r = T.mesurer_qualite(x, 3)
        for moteur in ('r3', 'r2'):
            self.assertEqual(r[moteur]['ecart_longueur_echantillons'], 0)
            self.assertIn('aller_retour_ecart_spectral_db', r[moteur])
            self.assertIn('secondes_mur', r[moteur])
        self.assertLess(r['r3']['aller_retour_ecart_spectral_db'], 6.0)


class RegleDecalage(unittest.TestCase):
    def test_tenor_trop_haut_descend_d_environ_une_octave_avec_musique_transposee(self):
        r = T.regle_decalage(TENOR, TESSITURE)
        self.assertEqual(r['decalage_voix_dt'], r['transposition_musique_dt'] + 12 * r['octave_voix'])
        self.assertEqual(r['octave_voix'], -1)
        self.assertTrue(r['dans_confort'], r)
        self.assertLessEqual(abs(r['transposition_musique_dt']), T.DEMITONS_MUSIQUE_MAX)
        f = f0_final(TENOR, r['decalage_voix_dt'])
        bas, haut = TESSITURE['confort']
        self.assertGreaterEqual(f['p10'], bas)
        self.assertLessEqual(f['p90'], haut)

    def test_voix_aigue_de_femme(self):
        r = T.regle_decalage(FEMME_AIGUE, TESSITURE)
        self.assertTrue(r['dans_confort'], r)
        self.assertEqual(r['octave_voix'], -1)
        self.assertLessEqual(abs(r['transposition_musique_dt']), T.DEMITONS_MUSIQUE_MAX)

    def test_voix_deja_dans_la_zone_ne_bouge_pas(self):
        src = {'p10': 140.0, 'p50': 165.0, 'p90': 200.0}
        r = T.regle_decalage(src, TESSITURE)
        self.assertEqual((r['decalage_voix_dt'], r['transposition_musique_dt'], r['octave_voix']), (0, 0, 0))

    def test_voix_trop_grave_monte(self):
        r = T.regle_decalage(GRAVE, TESSITURE)
        self.assertGreater(r['decalage_voix_dt'], 0)
        self.assertTrue(r['dans_confort'], r)

    def test_sans_transposition_octaves_seules(self):
        for src in (TENOR, FEMME_AIGUE, GRAVE):
            r = T.regle_decalage(src, TESSITURE, transposer_musique=False)
            self.assertEqual(r['transposition_musique_dt'], 0)
            self.assertEqual(r['decalage_voix_dt'] % 12, 0)

    def test_la_transposition_ameliore_ou_egale_les_octaves_seules(self):
        for src in (TENOR, FEMME_AIGUE, GRAVE, {'p10': 200.0, 'p50': 260.0, 'p90': 330.0}):
            avec = T.regle_decalage(src, TESSITURE)
            sans = T.regle_decalage(src, TESSITURE, transposer_musique=False)
            self.assertLessEqual(avec['ecart_confort_dt'], sans['ecart_confort_dt'] + 1e-9)

    def test_musique_jamais_transposee_au_dela_du_plafond(self):
        for p50 in range(120, 700, 25):
            src = {'p10': p50 * 0.85, 'p50': float(p50), 'p90': p50 * 1.3}
            r = T.regle_decalage(src, TESSITURE)
            self.assertLessEqual(abs(r['transposition_musique_dt']), T.DEMITONS_MUSIQUE_MAX, p50)
            self.assertEqual(r['decalage_voix_dt'] - r['transposition_musique_dt'], 12 * r['octave_voix'])

    def test_decalage_entier(self):
        r = T.regle_decalage(TENOR, TESSITURE)
        self.assertIsInstance(r['decalage_voix_dt'], int)

    def test_cli_et_schema_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'tessiture.json'
            p.write_text(json.dumps(TESSITURE))
            self.assertEqual(T.charger_tessiture(str(p))['confort'], [123.0, 220.0])
            p.write_text(json.dumps({'p50': 1}))
            with self.assertRaises(ValueError):
                T.charger_tessiture(str(p))


class DecalageParPhrase(unittest.TestCase):
    def phrases(self):
        # trois groupes : médium, aigu, grave ; les silences (s) entre phrases valent 0,1 (court) ou 1,0 (long)
        def p(a, b, med):
            return {'debut_s': a, 'fin_s': b, 'p10': med * 0.9, 'p50': float(med), 'p90': med * 1.15}
        return [p(0, 4, 165), p(4.1, 8, 170), p(9.0, 13, 300), p(14.0, 18, 310), p(19.0, 23, 120), p(23.1, 27, 125)]

    def test_changements_aux_silences_seulement_et_par_petits_pas(self):
        ph = self.phrases()
        r = T.decalage_par_phrase(ph, TESSITURE, 0, ecart_max=6, silence_min_s=0.25, pas_max=2)
        self.assertEqual(len(r), len(ph))
        for i in range(1, len(ph)):
            changement = r[i]['decalage_dt'] != r[i - 1]['decalage_dt']
            silence = ph[i]['debut_s'] - ph[i - 1]['fin_s']
            if changement:
                self.assertGreaterEqual(silence, 0.25, f'phrase {i} : changement sans vrai silence')
                self.assertLessEqual(abs(r[i]['decalage_dt'] - r[i - 1]['decalage_dt']), 2)
                self.assertEqual(r[i]['change_a_s'], ph[i]['debut_s'])

    def test_suit_la_hauteur_des_phrases(self):
        ph = self.phrases()
        r = T.decalage_par_phrase(ph, TESSITURE, -6, ecart_max=6, lissage=0.2)
        self.assertLess(r[2]['decalage_dt'], r[0]['decalage_dt'], 'phrase aiguë : décalage plus bas')
        self.assertGreater(r[4]['decalage_dt'], r[2]['decalage_dt'], 'phrase grave : décalage plus haut')

    def test_toujours_dans_la_fourchette_du_decalage_global(self):
        r = T.decalage_par_phrase(self.phrases(), TESSITURE, 3, ecart_max=2)
        for f in r:
            self.assertLessEqual(abs(f['decalage_dt'] - 3), 2)

    def test_liste_vide(self):
        self.assertEqual(T.decalage_par_phrase([], TESSITURE, 0), [])

    @unittest.skipUnless(NUMPY, 'numpy requis')
    def test_phrases_depuis_une_courbe_de_f0(self):
        import numpy as np
        t = np.arange(0, 10, 0.02)
        f = np.where(((t > 1) & (t < 3)) | ((t > 5) & (t < 8)), 200.0, 0.0)
        ph = T.phrases_depuis_f0(t, f)
        self.assertEqual(len(ph), 2)
        self.assertAlmostEqual(ph[0]['p50'], 200.0)


if __name__ == '__main__':
    unittest.main()
