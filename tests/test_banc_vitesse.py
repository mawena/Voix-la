"""Banc vitesse (banc/banc.py, banc/noyau_banc.py) : le noyau du banc bâti sur le noyau actuel du dépôt, les variantes
de séparation et de nettoyage (temps, SDR à la référence, pistes écrites), l'égalisation du timbre, et les suites
d'étapes réparties sur les cartes.

Sans GPU ni modèle : audio_separator, soundfile et torch sont imités ; les chemins du noyau (/tmp, /kaggle) sont
déplacés dans un dossier temporaire."""
import base64
import importlib.util
import json
import os
import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from commun import DEPOT, NOYAU, SR, Dossier

BANC_PY = DEPOT / 'banc' / 'banc.py'
SCIPY = importlib.util.find_spec('scipy') is not None
if SCIPY:   # chargé avant les mock.patch.dict(sys.modules) : numpy.fft ne se charge qu'une fois par processus
    import scipy.signal  # noqa: F401


def charger_banc():
    spec = importlib.util.spec_from_file_location('banc_essai', BANC_PY)
    m = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop('BANC_DONNEES', None)
        spec.loader.exec_module(m)
    return m


BANC = charger_banc()


def source_complete(reglages):
    """Le code que le lanceur du banc écrit dans /tmp et exécute sur Kaggle."""
    lanceur = BANC.construire_noyau(reglages, NOYAU)
    return base64.b64decode(re.search(r"b64decode\('([^']+)'\)", lanceur).group(1)).decode()


def banc_dans(dossier, reglages):
    src = source_complete(reglages).replace('/tmp/', f'{dossier}/t/').replace('/kaggle/', f'{dossier}/kaggle/')
    m = types.ModuleType('banc_noyau_essai')
    exec(compile(src, 'banc_noyau_essai.py', 'exec'), m.__dict__)
    for d in ('t', 'kaggle/input/jeu', 'kaggle/working'):
        (Path(dossier) / d).mkdir(parents=True, exist_ok=True)
    return m


# --- Faux soundfile, faux audio_separator, faux torch ---------------------------------------------------------------

def sf_ecrire(chemin, y, sr, subtype=None, format=None):
    with open(chemin, 'wb') as f:
        np.save(f, np.asarray(y, np.float32))


def sf_lire(chemin, dtype='float32', always_2d=False):
    with open(chemin, 'rb') as f:
        y = np.load(f)
    if always_2d and y.ndim == 1:
        y = y[:, None]
    return y.astype(dtype), SR


FAUX_SF = types.SimpleNamespace(write=sf_ecrire, read=sf_lire)
MODELES = {   # modèle -> pistes (la première gardée par le noyau), transformation de la première
    'vocals_mel_band_roformer.ckpt': (('(Vocals)', '(Other)'), lambda x: 0.6 * x + 0.05 * np.tanh(3 * x)),
    'mel_band_roformer_karaoke_becruily.ckpt': (('(Vocals)', '(Instrumental)'), lambda x: 0.7 * x),
    'bs_roformer_karaoke_anvuew.ckpt': (('(karaoke)', '(Vocals)'), lambda x: 0.5 * x),   # la voix principale en second
    'dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt': (('(noreverb)', '(reverb)'), lambda x: 0.8 * x - 0.1 * x ** 3),
}


class FauxSeparateur:
    vus = []

    def __init__(self, model_file_dir, output_dir, use_autocast=False, use_native_fp16=False, use_torch_compile=False,
                 mdxc_params=None, **k):
        self.dossier, self.mdxc = output_dir, mdxc_params or {}
        self.precision = 'native_fp16' if use_native_fp16 else ('autocast' if use_autocast else 'fp32')
        self.model_instance = types.SimpleNamespace(effective_precision=self.precision, effective_torch_compile=use_torch_compile)

    def load_model(self, model_filename):
        if model_filename not in MODELES:
            raise RuntimeError(f'modèle inconnu {model_filename}')
        self.modele = model_filename
        FauxSeparateur.vus.append((model_filename, self.precision, self.mdxc.get('overlap')))

    def separate(self, fichier):
        x, _ = sf_lire(fichier, always_2d=True)
        pistes, f = MODELES[self.modele]
        y = f(x)
        if self.precision != 'fp32':   # demi-précision : arrondi de float16, recouvrement 2 : un peu d'écart en plus
            y = y.astype(np.float16).astype(np.float32)
        if self.mdxc.get('overlap') == 2:
            y = y * 0.999
        base = os.path.splitext(os.path.basename(fichier))[0]
        noms = [f'{base}_{p}_{self.modele}.wav' for p in pistes]
        if pistes[1] == '(Vocals)':   # karaoké anvuew imité : la voix principale est la seconde piste
            sf_ecrire(os.path.join(self.dossier, noms[1]), y, SR)
            sf_ecrire(os.path.join(self.dossier, noms[0]), x - y, SR)
        else:
            sf_ecrire(os.path.join(self.dossier, noms[0]), y, SR)
            sf_ecrire(os.path.join(self.dossier, noms[1]), x - y, SR)
        return noms


def modules_faux():
    sep = types.ModuleType('audio_separator.separator')
    sep.Separator = FauxSeparateur
    cuda = types.SimpleNamespace(empty_cache=lambda: None, reset_peak_memory_stats=lambda: None,
                                 max_memory_allocated=lambda: 512 * 2 ** 20, synchronize=lambda: None)
    torch = types.SimpleNamespace(load=lambda *a, **k: None, cuda=cuda)
    return {'soundfile': FAUX_SF, 'audio_separator': types.ModuleType('audio_separator'), 'audio_separator.separator': sep,
            'torch': torch}


def son(duree, graine):
    rng = np.random.default_rng(graine)
    t = np.arange(int(duree * SR)) / SR
    x = 0.3 * np.sin(2 * np.pi * 220 * t)[:, None] * np.array([1.0, 0.8]) + 0.05 * rng.standard_normal((len(t), 2))
    return x.astype(np.float32)


class NoyauDuBanc(unittest.TestCase):
    def test_bati_sur_le_noyau_du_depot(self):
        src = source_complete({'pas': 30})
        compile(src, 'banc_complet.py', 'exec')
        for nom in ('def etape_ying', 'def etape_separer_voix', 'def etape_nettoyer_variantes', 'def etape_deriver',
                    'def lancer_en_parallele'):
            self.assertIn(nom, src)
        self.assertNotIn("if __name__ == '__main__':\n    if len(sys.argv)", src, 'le bloc final du noyau est retiré')
        self.assertIn("if __name__ == '__main__':\n    principal()", src)

    def test_moteur_du_noyau_garde(self):
        with Dossier() as d:
            m = banc_dans(d, {'pas': 30})
            self.assertIsNotNone(m._MOTEUR_YING_NOYAU, 'MoteurYing du noyau gardé avant la classe du banc')
            self.assertIsNot(m._MOTEUR_YING_NOYAU, m.MoteurYing)
            self.assertIn('outils', m._MOTEUR_YING_NOYAU.__init__.__code__.co_varnames)


class VariantesDeSeparation(unittest.TestCase):
    def setUp(self):
        FauxSeparateur.vus = []

    def test_separation_puis_nettoyage(self):
        with Dossier() as d, mock.patch.dict(sys.modules, modules_faux()):
            m = banc_dans(d, {'pas': 30, 'dossier_pistes': f'{d}/pistes',
                              'extraits': {'aa': {'decalage': 0}, 'bb': {'decalage': -12}}})
            for ex, g in (('aa', 1), ('bb', 2)):
                sf_ecrire(f'{d}/kaggle/input/jeu/extrait-{ex}-original.wav', son(14, g), SR)
                os.makedirs(f'{d}/pistes/{ex}', exist_ok=True)
                os.makedirs(f'{d}/kaggle/working/{ex}', exist_ok=True)
            m.etape_separer_voix({'extraits': ['aa'], 'modele': 'vocals_mel_band_roformer.ckpt', 'reference': 'R',
                                  'variantes': [{'nom': 'H2', 'precision': 'fp16', 'recouvrement': 2},
                                                {'nom': 'R', 'precision': 'fp32', 'recouvrement': 4}]})
            self.assertFalse(os.path.exists(f'{d}/kaggle/working/bb/separation_voix.json'), 'seulement les extraits de l\'étape')
            res = json.loads(Path(f'{d}/kaggle/working/aa/separation_voix.json').read_text())
            self.assertEqual(list(res['variantes']), ['R', 'H2'], 'la référence d\'abord')
            r, h = res['variantes']['R'], res['variantes']['H2']
            self.assertEqual(r['sdr_voix_db'], 200.0)
            self.assertTrue(30 < h['sdr_voix_db'] < 200, h)
            self.assertEqual(h['precision_effective'], 'native_fp16')
            self.assertEqual(h['memoire_gpu_max_mo'], 512)
            for f in ('pistes/aa/voix.wav', 'pistes/aa/voix_H2.wav', 'kaggle/working/aa/voix.flac'):
                self.assertTrue(os.path.exists(f'{d}/{f}'), f)
            self.assertEqual([v[1:] for v in FauxSeparateur.vus], [('fp32', 4), ('native_fp16', 2)])

            m.etape_nettoyer_variantes({'extraits': ['aa'], 'reference': 'R', 'variantes': [
                {'nom': 'R', 'precision': 'fp32', 'recouvrement': 4},
                {'nom': 'A', 'precision': 'autocast', 'recouvrement': 4},
                {'nom': 'T', 'precision': 'fp16', 'recouvrement': 2, 'source': 'voix@H2'},
                {'nom': 'K', 'precision': 'fp32', 'recouvrement': 4, 'karaoke': 'bs_roformer_karaoke_anvuew.ckpt'}]})
            net = json.loads(Path(f'{d}/kaggle/working/aa/nettoyage.json').read_text())
            self.assertEqual(set(net['variantes']), {'R', 'A', 'T', 'K'})
            for v in ('A', 'T', 'K'):
                s = net['variantes'][v]['sdr_db']
                self.assertEqual(set(s), {'lead', 'lead_sec', 'choeurs_sec'}, v)
                self.assertEqual(set(net['variantes'][v]['durees_s']), {'karaoke', 'dereverb_lead', 'dereverb_choeurs'})
            self.assertGreater(net['variantes']['A']['sdr_db']['lead_sec'], net['variantes']['T']['sdr_db']['lead_sec'],
                               'T part de la voix fp16 : écart de bout en bout plus grand')
            k = net['variantes']['K']
            self.assertTrue(k['pistes_karaoke'][-1].startswith('=> ') and '(Vocals)' in k['pistes_karaoke'][-1],
                            'karaoké qui rend « Vocals » et « karaoke » : la voix principale est « Vocals »')
            self.assertEqual(k['karaoke']['modele'], 'bs_roformer_karaoke_anvuew.ckpt')
            for f in ('lead_sec', 'choeurs_sec', 'lead_sec_A', 'choeurs_sec_T', 'lead_sec_K'):
                self.assertTrue(os.path.exists(f'{d}/pistes/aa/{f}.wav'), f)
            self.assertEqual(m.piste_chemin('aa', 'lead_sec@K'), f'{d}/pistes/aa/lead_sec_K.wav')
            self.assertEqual(m.piste_chemin('aa', 'voix'), f'{d}/pistes/aa/voix.wav')

    def test_sdr(self):
        with Dossier() as d:
            m = banc_dans(d, {})
            x = son(1, 3)[:, 0]
            self.assertEqual(m.sdr_db(x, x), 200.0)
            self.assertAlmostEqual(m.sdr_db(x, 0.9 * x), 20.0, places=2)


@unittest.skipUnless(SCIPY, 'scipy absent')
class EgalisationDuTimbre(unittest.TestCase):
    def test_rapproche_l_equilibre_spectral(self):
        import scipy.signal
        with Dossier() as d:
            m = banc_dans(d, {})
            rng = np.random.default_rng(5)
            src = rng.standard_normal(SR * 6).astype(np.float32) * 0.1
            b, a = scipy.signal.butter(2, 3000, 'lowpass', fs=SR)   # « voix convertie » plus sourde que la source
            conv = scipy.signal.lfilter(b, a, src).astype(np.float32)
            avant = m.ecart_spectral_db(conv, src)
            f, dd = m.courbe_egalisation(src, conv, 1.0, 6.0)
            self.assertLessEqual(float(np.abs(dd).max()), 6.0 + 1e-9)
            self.assertEqual(float(np.abs(dd[f < 60]).max()), 0.0)
            y = m.appliquer_egalisation(conv, dd)
            self.assertEqual(len(y), len(conv))
            apres = m.ecart_spectral_db(y, src)
            self.assertLess(apres, 0.6 * avant, (avant, apres))   # bornée à ±6 dB : la correction reste partielle
            self.assertGreater(float(dd[(f > 6000) & (f < 10000)].mean()), 3.0, 'aigus relevés')


class FauxTenseur:
    def __init__(self, n):
        self.n = n

    def size(self, i):
        return self.n

    def float(self):
        return self


class FauxMoteur:
    """MoteurYing du noyau imité : même interface (outils, checkpoint), une « conversion » fixe et déterministe."""
    references = []

    def __init__(self, outils=None, checkpoint=None):
        self.torch = types.SimpleNamespace(
            manual_seed=lambda g: None, float16='float16',
            autocast=lambda **k: mock.MagicMock(__enter__=lambda s: None, __exit__=lambda s, *a: False),
            cuda=types.SimpleNamespace(empty_cache=lambda: None, reset_peak_memory_stats=lambda: None,
                                       max_memory_allocated=lambda: 3 * 2 ** 30, synchronize=lambda: None))
        self.chargement_s, self.sr, self.hop = 0.1, SR, 512
        self.semantic_fn = lambda w: None
        self.f0_fn = lambda w: None
        self.vocoder_fn = lambda c: FauxTenseur(1)
        self.mel_fn = lambda x: None

    def preparer_ref(self, chemin):
        FauxMoteur.references.append(os.path.basename(chemin))
        return {'mel2': FauxTenseur(1787), 'duree': 20.75}

    def length_regulator(self, *a):
        return None, None

    def inference(self, *a):
        return None

    def convertir_morceau(self, xs, ref, decalage, pas):
        self.semantic_fn(None)
        self.f0_fn(None)
        self.length_regulator()
        self.inference()
        self.vocoder_fn(None).float()
        return (0.5 * xs * (1 + 0.01 * pas)).astype(np.float32)


class ConversionDuBanc(unittest.TestCase):
    def test_versions_graine_reference_profil_puis_egalisation(self):
        with Dossier() as d, mock.patch.dict(sys.modules, modules_faux()):
            m = banc_dans(d, {'pas': 30, 'cfg': 0.7, 'fp16': True, 'graine': 1234, 'pad_s': 0.25, 'fondu_s': 0.06,
                              'reference': 'cible-reference-A.wav', 'dossier_pistes': f'{d}/pistes',
                              'extraits': {'aa': {'decalage': 0}}})
            for f in ('cible-reference-A.wav', 'cible-reference-AB.wav'):
                sf_ecrire(f'{d}/kaggle/input/jeu/{f}', son(1, 1)[:, 0], SR)
            os.makedirs(f'{d}/pistes/aa')
            os.makedirs(f'{d}/kaggle/working/aa')
            x = son(20, 4)
            x[8 * SR:9 * SR] = 0   # un silence : plusieurs morceaux
            for p in ('lead_sec', 'choeurs_sec', 'lead_sec_K'):
                sf_ecrire(f'{d}/pistes/aa/{p}.wav', x * (0.3 if 'choeurs' in p else 1.0), SR)
            FauxMoteur.references = []
            with mock.patch.object(m, '_MOTEUR_YING_NOYAU', FauxMoteur):
                m.etape_convertir({'extraits': ['aa'], 'moteur': {'type': 'yingmusic_noyau'}, 'versions': [
                    {'nom': 'profil', 'pistes': ['lead_sec'], 'limite_s': 5, 'profil': True},
                    {'nom': 'Y30', 'pas': 30, 'pistes': ['lead_sec', 'choeurs_sec']},
                    {'nom': 'Y20', 'pas': 20, 'graine': 7, 'pistes': ['lead_sec', 'choeurs_sec']},
                    {'nom': 'AB', 'reference': 'cible-reference-AB.wav', 'pistes': ['lead_sec']},
                    {'nom': 'K30', 'pistes': ['lead_sec@K']},
                    {'nom': 'V16', 'pistes': ['lead_sec'], 'vocodeur_fp16': True}]})
            w = Path(f'{d}/kaggle/working/aa')
            fiche = lambda v: json.loads((w / f'{v}.json').read_text())
            self.assertEqual(FauxMoteur.references, ['cible-reference-A.wav', 'cible-reference-AB.wav'],
                             'chaque référence préparée une fois')
            p = fiche('profil')
            self.assertEqual(p['type'], 'mesure')
            self.assertEqual(set(p['profil_s']), {'whisper_s', 'rmvpe_s', 'diffusion_s', 'vocodeur_s', 'regulateur_s'})
            self.assertEqual(fiche('Y30')['type'], 'version')
            self.assertEqual((fiche('Y20')['graine'], fiche('Y20')['pas']), (7, 20))
            self.assertEqual(fiche('AB')['reference'], 'cible-reference-AB.wav')
            self.assertTrue(fiche('V16')['vocodeur_fp16'])
            self.assertEqual(fiche('Y30')['pistes']['lead_sec']['memoire_gpu_max_mo'], 3072)
            for f in ('Y30.flac', 'Y30_lead_sec.flac', 'Y30_choeurs_sec.flac', 'K30.flac', 'V16.flac'):
                self.assertTrue((w / f).exists(), f)
            if SCIPY:
                m.etape_deriver({'extraits': ['aa'], 'versions': [{'nom': 'EQ1', 'de': 'Y30', 'lissage_oct': 1.0}]})
                eq = fiche('EQ1')
                self.assertEqual((eq['type'], eq['derivee_de']), ('version', 'Y30'))
                self.assertEqual(set(eq['egalisation']['moyenne_par_bande_db']), {'lead_sec', 'choeurs_sec'})
                for f in ('EQ1.flac', 'EQ1_lead_sec.flac', 'EQ1_choeurs_sec.flac'):
                    self.assertTrue((w / f).exists(), f)
                y0, y1 = sf_lire(str(w / 'Y30_lead_sec.flac'))[0], sf_lire(str(w / 'EQ1_lead_sec.flac'))[0]
                self.assertAlmostEqual(float(np.sqrt((y1 ** 2).mean() / (y0 ** 2).mean())), 1.0, delta=0.15,
                                       msg='même niveau après égalisation')


class SuitesSurLesCartes(unittest.TestCase):
    def test_une_suite_par_carte_ou_tout_sur_la_carte_0(self):
        with Dossier() as d:
            m = banc_dans(d, {'limite_totale_s': 600})
            vus = []

            def faux_lancer(etape, env=None, prefixe=''):
                vus.append((etape['nom'], (env or {}).get('CUDA_VISIBLE_DEVICES')))
                return {'nom': etape['nom'], 'code': 0}
            etape = {'etape': 'parallele', 'cartes': [[{'etape': 'x', 'nom': 'a1'}, {'etape': 'x', 'nom': 'a2'}],
                                                      [{'etape': 'x', 'nom': 'b1'}]]}
            session = {'etapes': []}
            with mock.patch.object(m, 'lancer_etape', faux_lancer), mock.patch.object(m, 'nombre_cartes', lambda: 2):
                m.lancer_en_parallele(etape, session)
            self.assertEqual(sorted(vus), [('a1', '0'), ('a2', '0'), ('b1', '1')])
            self.assertEqual(vus.index(('a1', '0')) < vus.index(('a2', '0')), True, 'dans l\'ordre sur sa carte')
            self.assertEqual(sorted(e['carte'] for e in session['etapes']), [0, 0, 1])
            vus.clear()
            with mock.patch.object(m, 'lancer_etape', faux_lancer), mock.patch.object(m, 'nombre_cartes', lambda: 1):
                m.lancer_en_parallele(etape, {'etapes': []})
            self.assertEqual(vus, [('a1', None), ('a2', None), ('b1', None)], 'une carte : tout à la suite, sans masque')


if __name__ == '__main__':
    unittest.main()
