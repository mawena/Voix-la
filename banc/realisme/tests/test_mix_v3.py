"""Mixage v3 (banc/realisme/mixage_v3.py) sur des sons synthétiques : aucune donnée réelle, aucun réseau, aucun GPU.

Lancer avec le venv du banc : ~/voixla-realisme-venv/bin/python -m unittest discover -s banc/realisme/tests -p "test_mix*.py".
Sans numpy, scipy, soundfile et pyloudnorm, les essais sont sautés."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI.parent))

MODULES = all(importlib.util.find_spec(m) for m in ('numpy', 'scipy', 'soundfile', 'pyloudnorm'))
if MODULES:
    import numpy as np
    import soundfile as sf
    import mixage_v3 as M

SR = 44100


def voix_synthetique(duree, f0=220.0, rt60=None, graine=1, phrases=((0.3, 1.2), (2.0, 3.0), (3.8, 4.8))):
    """Voyelles tenues (harmoniques de f0) par phrases, éventuellement avec une reverb de RT60 donné ; rend (sec, complet)."""
    rng = np.random.default_rng(graine)
    n = int(duree * SR)
    t = np.arange(n) / SR
    sec = np.zeros(n)
    for a, b in phrases:
        m = (t >= a) & (t < b)
        env = np.clip((t[m] - a) / 0.03, 0, 1) * np.clip((b - t[m]) / 0.02, 0, 1)
        sec[m] = env * sum(np.sin(2 * np.pi * f0 * h * t[m]) / h for h in range(1, 12))
    sec *= 0.1
    if rt60 is None:
        return sec.astype(np.float32), sec.astype(np.float32)
    k = int(SR * rt60 * 1.2)
    ir = rng.standard_normal(k) * 10 ** (-3.0 * np.arange(k) / SR / rt60)
    ir /= np.sqrt((ir ** 2).sum())
    from scipy.signal import fftconvolve
    complet = 0.6 * sec + 0.8 * fftconvolve(sec, ir)[:n]
    return sec.astype(np.float32), complet.astype(np.float32)


def chanson_synthetique(dossier, duree=12.0):
    """Dossier travail/ d'un doublage imité : original, instrumental, voix_source, <voix>/voix_convertie.flac, seche, reverb,
    rapport.json."""
    t = Path(dossier) / 'travail'
    v = t / 'voix'
    v.mkdir(parents=True)
    n = int(duree * SR)
    rng = np.random.default_rng(3)
    tt = np.arange(n) / SR
    musique = np.stack([0.08 * np.sin(2 * np.pi * 110 * tt) + 0.02 * rng.standard_normal(n),
                        0.08 * np.sin(2 * np.pi * 165 * tt) + 0.02 * rng.standard_normal(n)], 1).astype(np.float32)
    phr = ((0.5, 2.5), (3.5, 6.0), (7.0, 11.0))
    src, src_rev = voix_synthetique(duree, 220.0, rt60=1.0, phrases=phr)
    conv, _ = voix_synthetique(duree, 130.0, phrases=phr, graine=2)
    conv = conv * 0.5
    sf.write(str(t / 'instrumental.wav'), musique, SR, subtype='FLOAT')
    sf.write(str(t / 'voix_source.wav'), np.stack([src_rev, src_rev], 1), SR, subtype='FLOAT')
    sf.write(str(t / 'original.wav'), musique + np.stack([src_rev, src_rev], 1), SR, subtype='FLOAT')
    sf.write(str(v / 'voix_convertie.flac'), conv, SR, subtype='PCM_24')
    sf.write(str(v / 'seche_voix_convertie.wav'), np.stack([conv, conv], 1), SR, subtype='FLOAT')
    sf.write(str(v / 'reverb_voix_convertie.wav'), np.stack([conv * 0.3, conv * 0.3], 1), SR, subtype='FLOAT')
    (v / 'rapport.json').write_text(json.dumps({'mix': {'gains_db': {'seche': 10.0, 'reverb': 20.0}}}))
    return t


@unittest.skipUnless(MODULES, 'numpy, scipy, soundfile, pyloudnorm requis')
class Mesures(unittest.TestCase):
    def test_rt60_retrouve(self):
        _, complet = voix_synthetique(10.0, rt60=1.0, phrases=((0.5, 1.5), (3.0, 4.0), (5.5, 6.5), (8.0, 9.0)))
        r = M.mesurer_rt60(complet)
        self.assertIsNotNone(r['rt60_s'])
        self.assertAlmostEqual(r['rt60_brut_s'], 1.0, delta=0.35)

    def test_rt60_plafonne_a_deux_secondes(self):
        _, complet = voix_synthetique(14.0, rt60=3.0, phrases=((0.5, 1.5), (5.0, 6.0), (9.0, 10.0)))
        r = M.mesurer_rt60(complet)
        if r['rt60_s'] is not None:
            self.assertLessEqual(r['rt60_s'], 2.0)

    def test_rir_decroit_comme_demande(self):
        ir = M.reponse_impulsionnelle(1.0, 30.0)
        self.assertEqual(ir.shape[1], 2)
        self.assertTrue(np.allclose(np.abs(ir[:int(0.03 * SR) - 5]), 0.0), 'pré-délai de 30 ms : silence avant')
        e = np.cumsum((ir[::-1, 0].astype(np.float64) ** 2))[::-1]
        L = 10 * np.log10(e / e[0] + 1e-12)
        t = np.arange(len(L)) / SR
        m = (L < -5) & (L > -25)
        pente = np.polyfit(t[m], L[m], 1)[0]
        self.assertAlmostEqual(-60.0 / pente, 1.0, delta=0.35)

    def test_predelai_mesure_avec_piste_de_reverb(self):
        sec, _ = voix_synthetique(10.0, phrases=((0.5, 1.2), (2.5, 3.2), (4.5, 5.2), (6.5, 7.2)))
        ir = np.zeros(int(0.5 * SR))
        ir[int(0.040 * SR)] = 1.0   # un écho à 40 ms
        from scipy.signal import fftconvolve
        humide = fftconvolve(sec, ir)[:len(sec)].astype(np.float32) * 0.5
        pd = M.mesurer_predelai(sec, humide)
        self.assertIsNotNone(pd)
        self.assertAlmostEqual(pd, 40.0, delta=4.0)

    def test_sans_piste_de_reverb_predelai_par_defaut_signale(self):
        _, complet = voix_synthetique(10.0, rt60=1.0, phrases=((0.5, 1.5), (3.0, 4.0), (5.5, 6.5), (8.0, 9.0)))
        r = M.mesurer_reverb(complet)
        self.assertEqual(r['source'], 'voix_origine')
        self.assertFalse(r['predelai_mesure'])
        self.assertEqual(r['predelai_ms'], M.REVERB['predelai_defaut_ms'])


@unittest.skipUnless(MODULES, 'numpy, scipy, soundfile, pyloudnorm requis')
class Traitements(unittest.TestCase):
    def test_eq_ne_depasse_jamais_les_plafonds(self):
        rng = np.random.default_rng(0)
        src = rng.standard_normal(SR * 6).astype(np.float32)
        from scipy.signal import butter, sosfilt
        conv = sosfilt(butter(2, 700.0, 'lowpass', fs=SR, output='sos'), src).astype(np.float32)   # très différent
        for plafond in (3.0, 1.5, 0.0):
            c, corr = M.courbe_eq(src, conv, plafond)
            self.assertLessEqual(float(np.abs(corr).max()), plafond + 1e-9)
            milieu = (c >= 250) & (c <= 4000)
            self.assertLessEqual(float(np.abs(corr[milieu]).max()), min(plafond, 2.0) + 1e-9)
        c, corr = M.courbe_eq(src, conv, 3.0)
        self.assertGreater(float(np.abs(corr).max()), 2.5, 'la différence est grande : le plafond général est atteint')

    def test_eq_appliquee_garde_la_longueur_et_ne_bouge_pas_a_zero(self):
        rng = np.random.default_rng(1)
        x = rng.standard_normal(SR * 3).astype(np.float32) * 0.1
        c = 150.0 * 2.0 ** (np.arange(0, 19) / 3.0)
        y0 = M.appliquer_eq(x, c, np.zeros(len(c)))
        self.assertTrue(np.array_equal(x, y0))
        y = M.appliquer_eq(x, c, np.full(len(c), 2.0))
        self.assertEqual(len(y), len(x))
        self.assertAlmostEqual(20 * np.log10(np.std(y[2000:-2000]) / np.std(x[2000:-2000])), 1.7, delta=1.2)

    def test_gain_suit_le_rapport_voix_musique_de_l_original(self):
        n = SR * 12
        t = np.arange(n) / SR
        voix = (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        musique = np.full(n, 0.05, np.float32) * np.sin(2 * np.pi * 110 * t).astype(np.float32)
        conv = voix * 0.25   # 12 dB plus bas que la voix d'origine
        c, info = M.courbe_gain_voix(voix, conv, musique, voix_db=1.0)
        self.assertEqual(len(c), n)
        self.assertAlmostEqual(float(np.median(c)), 12.04 + 1.0, delta=0.6)
        c0, _ = M.courbe_gain_voix(voix, conv, musique, voix_db=0.0)
        self.assertAlmostEqual(float(np.median(c)) - float(np.median(c0)), 1.0, delta=0.05)

    def test_rapport_suit_la_musique_transposee(self):
        n = SR * 12
        t = np.arange(n) / SR
        voix = (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        musique = (0.05 * np.sin(2 * np.pi * 110 * t)).astype(np.float32)
        c1, _ = M.courbe_gain_voix(voix, voix, musique, musique)
        c2, _ = M.courbe_gain_voix(voix, voix, musique, musique * 0.5)   # musique 6 dB plus bas : la voix baisse aussi
        self.assertAlmostEqual(float(np.median(c1)) - float(np.median(c2)), 6.02, delta=0.2)

    def test_compresseur_reduit_les_forts_et_pas_les_faibles(self):
        t = np.arange(SR * 4) / SR
        x = np.where(t < 2, 0.4, 0.02) * np.sin(2 * np.pi * 300 * t)
        y, red = M.compresseur(x.astype(np.float32))
        fort = 20 * np.log10(np.std(y[SR // 2:SR]) / np.std(x[SR // 2:SR]))
        faible = 20 * np.log10(np.std(y[3 * SR:]) / np.std(x[3 * SR:]))
        self.assertLess(fort, -3.0)
        self.assertGreater(faible, -0.5)

    def test_deesseur_reduit_les_s_trop_forts(self):
        rng = np.random.default_rng(2)
        n = SR * 4
        t = np.arange(n) / SR
        voyelle = 0.1 * np.sin(2 * np.pi * 300 * t) + 0.05 * np.sin(2 * np.pi * 900 * t)
        from scipy.signal import butter, sosfilt
        s = sosfilt(butter(4, [5000, 9000], 'bandpass', fs=SR, output='sos'), rng.standard_normal(n)) * 0.1
        ref = voyelle + 0.02 * s / s.std() * 0.1   # voix d'origine : peu de sifflantes
        x = voyelle + np.where((t > 1) & (t < 2), 0.1 * s / s.std(), 0.0)   # sifflantes fortes entre 1 et 2 s
        y, info = M.deesseur(x.astype(np.float32), ref.astype(np.float32))
        bande = lambda z: np.std(sosfilt(butter(4, [5000, 9000], 'bandpass', fs=SR, output='sos'), z[SR + 2000:2 * SR - 2000]))
        self.assertLess(bande(y), bande(x) * 0.8)
        self.assertGreater(info['reduction_max_db'], 1.0)

    def test_limiteur_tient_moins_un_dbtp(self):
        rng = np.random.default_rng(4)
        x = (rng.standard_normal((SR * 3, 2)) * 0.5).astype(np.float32)
        x[SR] = 1.8
        lim = M.courbe_limiteur(x)
        y = x * lim[:, None]
        self.assertLessEqual(M.crete_vraie_db(y), -1.0 + 0.1)
        self.assertLessEqual(float(lim.max()), 1.0)

    def test_finaliser_atteint_le_loudness_cible(self):
        rng = np.random.default_rng(5)
        x = (rng.standard_normal((SR * 6, 2)) * 0.05).astype(np.float32)
        mix, g, lim, info = M.finaliser({'a': x}, -20.0)
        self.assertAlmostEqual(info['lufs_final'], -20.0, delta=0.1)
        self.assertLessEqual(info['crete_vraie_dbtp'], -1.0 + 0.1)

    def test_choeurs_en_stereo_et_sous_le_lead(self):
        rng = np.random.default_rng(6)
        lead = (0.1 * rng.standard_normal(SR * 3)).astype(np.float32)
        choeurs = (0.1 * rng.standard_normal(SR * 3)).astype(np.float32)
        sec, hum = M.choeurs_v3(choeurs, lead, {'rt60_s': 1.0, 'predelai_ms': 30.0, 'humide_sur_sec_db': None})
        self.assertEqual(sec.shape, (SR * 3, 2))
        self.assertAlmostEqual(M.niveau_actif_db(sec[:, 0]) - M.niveau_actif_db(lead), -6.0, delta=1.0)
        corr = float(np.corrcoef(sec[:, 0], sec[:, 1])[0, 1])
        self.assertLess(abs(corr), 0.4, 'G et D décorrélés : image large')


@unittest.skipUnless(MODULES, 'numpy, scipy, soundfile, pyloudnorm requis')
class Variantes(unittest.TestCase):
    def test_variantes_meme_loudness_et_lettres_au_hasard(self):
        with tempfile.TemporaryDirectory() as d:
            t = chanson_synthetique(d)
            ch = M.Chanson(t, 'voix', 0.0, 12.0)
            corr, absentes, fiches = M.produire(ch, 'extrait', Path(d) / 'sortie', mp3=False)
            self.assertEqual(sorted(corr.values()), sorted(v for v in M.VARIANTES if v not in absentes))
            self.assertIn('C', absentes, 'la variante C exige la voix reconvertie (GPU)')
            self.assertIn('D', absentes, 'sans piste de reverb retirée, D et E ne sont pas produites')
            self.assertIn('E', absentes)
            lufs = {v: f['mesures']['lufs_final'] for v, f in fiches.items()}
            self.assertLessEqual(max(lufs.values()) - min(lufs.values()), 0.15, lufs)
            for v, f in fiches.items():
                self.assertLessEqual(f['mesures']['crete_vraie_dbtp'], -0.9, v)
            self.assertAlmostEqual(list(lufs.values())[0], ch.cible_lufs, delta=0.15)

    def test_mp3_dans_le_format_de_l_ecoute(self):
        import shutil
        if not shutil.which('ffmpeg'):
            self.skipTest('ffmpeg absent')
        with tempfile.TemporaryDirectory() as d:
            t = chanson_synthetique(d, 8.0)
            ch = M.Chanson(t, 'voix', 0.0, 8.0)
            corr, absentes, _ = M.produire(ch, 'extrait', Path(d) / 'sortie', variantes=['A', 'B', 'F'])
            dossier = Path(d) / 'sortie' / 'ecoute' / 'extrait'
            self.assertEqual(sorted(corr), ['A', 'B', 'C'])
            for lettre in corr:
                self.assertTrue((dossier / f'{lettre}.mp3').exists())
                self.assertTrue((dossier / f'{lettre}_voix.mp3').exists())
            self.assertTrue((dossier / 'original.mp3').exists())

    def test_sans_reverb_donne_moins_d_energie_apres_la_voix(self):
        with tempfile.TemporaryDirectory() as d:
            t = chanson_synthetique(d)
            ch = M.Chanson(t, 'voix', 0.0, 12.0)
            avec, _ = ch.mix_v3()
            sans, _ = ch.mix_v3(reverb=None)
            self.assertIn('reverb', avec)
            self.assertNotIn('reverb', sans)

    def test_chanson_avec_piste_de_reverb_ouvre_d_et_e(self):
        with tempfile.TemporaryDirectory() as d:
            t = chanson_synthetique(d)
            src, complet = voix_synthetique(12.0, 220.0, rt60=1.0, phrases=((0.5, 2.5), (3.5, 6.0), (7.0, 11.0)))
            sf.write(str(t / 'reverb_source.wav'), np.stack([complet - 0.6 * src] * 2, 1), SR, subtype='FLOAT')
            ch = M.Chanson(t, 'voix', 0.0, 12.0)
            self.assertEqual(ch.mesure_reverb['source'], 'piste_reverb')
            corr, absentes, _ = M.produire(ch, 'extrait', Path(d) / 'sortie', variantes=['B', 'D', 'E'], mp3=False)
            self.assertEqual(absentes, {})
            self.assertEqual(sorted(corr.values()), ['B', 'D', 'E'])


if __name__ == '__main__':
    unittest.main()
