"""Session GPU « réalisme » (banc/realisme/noyau_realisme.py) en simulation : sans GPU, sans modèle, sans Kaggle, sans réseau.

Le noyau complet (doubler_kaggle.py + noyau_banc.py + transposition.py + noyau_realisme.py) est construit, compilé, puis son
étape « realisme » est exécutée avec un faux moteur de conversion dans un dossier temporaire (/tmp et /kaggle déplacés), comme
tests/test_banc_vitesse.py le fait pour le banc."""
import base64
import importlib.util
import json
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ICI = Path(__file__).resolve().parent
REPO = ICI.parents[2]
sys.path.insert(0, str(ICI.parent))

NUMPY = importlib.util.find_spec('numpy') is not None
if NUMPY:
    import numpy as np
    import noyau_realisme as NR

SR = 44100
NOYAU = REPO / 'kaggle' / 'doubler_kaggle.py'
CAS = {
    'voix': {'nom': 'cible', 'reference': 'voix/cible/reference.wav', 'dossier_prises': 'prises',
             'prises_entrainement': [], 'prise_tenue': 'x.wav'},
    'extraits': {'un': {'titre': 'Un', 'debut': 0.0, 'fin': 5.0, 'decalage': -12, 'original': 'o.wav', 'voix': 'v.wav'},
                 'deux': {'titre': 'Deux', 'debut': 0.0, 'fin': 5.0, 'decalage': 0, 'original': 'o2.wav', 'voix': 'v2.wav'}}}
TESSITURE = {'p05': 98.0, 'p10': 110.0, 'p50': 165.0, 'p90': 247.0, 'p95': 277.0, 'confort': [123.0, 220.0]}


def charger_banc_dans(dossier):
    """banc.py et la configuration d'un fichier des cas, avec ses données dans le dossier temporaire."""
    (Path(dossier) / 'sessions').mkdir(parents=True, exist_ok=True)
    cas = Path(dossier) / 'cas.json'
    cas.write_text(json.dumps(CAS))
    with mock.patch.dict(os.environ, {'BANC_DONNEES': str(dossier)}):
        return NR.charger_banc(str(cas))


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


class Maillage:
    """Imite un tenseur de mel : seulement size(2)."""

    def __init__(self, n):
        self.n = n

    def size(self, axe):
        return self.n


def noyau_dans(dossier, reglages):
    """Le noyau complet exécuté comme module, /tmp et /kaggle déplacés dans le dossier ; faux soundfile et faux torch."""
    banc = charger_banc_dans(dossier)
    complet = NR.construire_noyau_realisme({**reglages, 'voix_cible': 'cible'}, NOYAU, banc)[1]
    src = complet.replace('/tmp/', f'{dossier}/t/').replace('/kaggle/', f'{dossier}/kaggle/')
    for d in ('t', 'kaggle/input/jeu', 'kaggle/working'):
        (Path(dossier) / d).mkdir(parents=True, exist_ok=True)
    cuda = types.SimpleNamespace(empty_cache=lambda: None, reset_peak_memory_stats=lambda: None, synchronize=lambda: None,
                                 max_memory_allocated=lambda: 0)
    torch = types.SimpleNamespace(load=lambda *a, **k: None, cuda=cuda, manual_seed=lambda s: None)
    m = types.ModuleType('noyau_realisme_essai')
    with mock.patch.dict(sys.modules, {'soundfile': FAUX_SF, 'torch': torch}):
        exec(compile(src, 'noyau_realisme_essai.py', 'exec'), m.__dict__)
    m.sf_faux, m.torch_faux = FAUX_SF, torch
    return m, complet


def deux_notes(f1, f2, duree=2.0):
    """f1 pendant duree s, 1 s de silence, f2 pendant duree s (mono, 44,1 kHz) : deux morceaux pour le découpage."""
    t = np.arange(int(duree * SR)) / SR
    a, b = 0.3 * np.sin(2 * np.pi * f1 * t), 0.3 * np.sin(2 * np.pi * f2 * t)
    return np.concatenate([a, np.zeros(SR), b]).astype(np.float32)


@unittest.skipUnless(NUMPY, 'numpy requis')
class Variantes(unittest.TestCase):
    def test_balayage_de_cfg_et_alias(self):
        reg = {'cfg': 0.7, 'pas': 30, 'graine': 1234}
        v = NR.developper_variantes([{'nom': 'Ra', 'decalage_dt': 'regle'},
                                     {'nom': 'Rc', 'decalage_dt': 'regle', 'balayage': {'cfg': [0.5, 0.7, 1.0, 1.5]}}], reg)
        self.assertEqual([x['nom'] for x in v], ['Ra', 'Rc-cfg0.5', 'Rc-cfg0.7', 'Rc-cfg1', 'Rc-cfg1.5'])
        self.assertEqual([x['cfg'] for x in v[1:]], [0.5, 0.7, 1.0, 1.5])
        self.assertEqual(v[2].get('alias_de'), 'Ra', 'cfg 0,7 = réglage de Ra : pas reconverti')
        self.assertTrue(all('alias_de' not in x for x in (v[0], v[1], v[3], v[4])))

    def test_balayage_style_et_defauts(self):
        reg = {'cfg': 0.7, 'pas': 30, 'graine': 1234}
        v = NR.developper_variantes([{'nom': 'Rs', 'balayage': {'style_r': [0.5, 0]}}], reg)
        self.assertEqual([(x['nom'], x['style_r']) for x in v], [('Rs-style_r0.5', 0.5), ('Rs-style_r0', 0)])
        self.assertEqual(v[0]['reference'], 'A')
        self.assertEqual((v[0]['pas'], v[0]['graine']), (30, 1234))

    def test_champ_inconnu_et_nom_double_refuses(self):
        with self.assertRaises(ValueError):
            NR.developper_variantes([{'nom': 'X', 'cdf': 1}], {})
        with self.assertRaises(ValueError):
            NR.developper_variantes([{'nom': 'X'}, {'nom': 'X', 'cfg': 1.0}], {'cfg': 0.7})

    def test_choix_de_la_reference_par_hauteur(self):
        regs = [{'nom': 'grave', 'centre_hz': 130.0}, {'nom': 'median', 'centre_hz': 200.0}, {'nom': 'aigu', 'centre_hz': 300.0}]
        self.assertEqual(NR.choisir_reference(120.0, regs)['nom'], 'grave')
        self.assertEqual(NR.choisir_reference(210.0, regs)['nom'], 'median')
        self.assertEqual(NR.choisir_reference(330.0, regs)['nom'], 'aigu')
        self.assertEqual(NR.choisir_reference(None, regs)['nom'], 'median', 'sans hauteur : le registre du milieu')
        self.assertEqual(NR.choisir_reference(1000.0, regs)['nom'], 'aigu')

    def test_centiles_f0_ignore_le_non_voise(self):
        c = NR.centiles_f0([0, 0, 100, 200, 300, 0, 0.5])
        self.assertEqual(c['p50'], 200.0)
        self.assertIsNone(NR.centiles_f0([0, 0, 1]))

    def test_decalage_de_variante(self):
        d, m, r = NR.decalage_de_variante({'decalage_dt': 3}, -12)
        self.assertEqual((d, m, r), (3, 3, None))
        d, m, r = NR.decalage_de_variante({'decalage_dt': -9}, 0)
        self.assertEqual((d, m), (-9, 3), 'voix −9 = −12 + 3 : musique +3')
        d, m, r = NR.decalage_de_variante({'decalage_dt': 'defaut', 'musique_dt': None}, -12)
        self.assertEqual((d, m), (-12, None))
        d, m, r = NR.decalage_de_variante({'decalage_dt': 'regle'}, 0, {'p10': 245.0, 'p50': 295.0, 'p90': 411.0}, TESSITURE)
        self.assertEqual(d - m, 12 * r['octave_voix'])
        self.assertTrue(r['dans_confort'])
        with self.assertRaises(ValueError):
            NR.decalage_de_variante({'decalage_dt': 'regle'}, 0)


@unittest.skipUnless(NUMPY, 'numpy requis')
class NoyauComplet(unittest.TestCase):
    def test_noyau_construit_compile_et_decodable(self):
        with tempfile.TemporaryDirectory() as d:
            banc = charger_banc_dans(d)
            lanceur, complet = NR.construire_noyau_realisme({'pas': 30}, NOYAU, banc)
            compile(complet, 'complet.py', 'exec')
            self.assertEqual(base64.b64decode(re.search(r"b64decode\('([^']+)'\)", lanceur).group(1)).decode(), complet)
            for nom in ('def etape_realisme', 'def etape_convertir', 'def classe_moteur_realisme', 'TRANSPOSITION_SRC = '):
                self.assertIn(nom, complet)
            self.assertEqual(complet.count("if __name__ == '__main__':\n    principal()"), 1)
            # principal redéfini AVANT l'appel final : l'étape realisme y est servie
            self.assertLess(complet.index('def principal():\n    """Celui du banc, plus'), complet.rindex('    principal()'))
            self.assertNotIn('envoi', lanceur.lower().replace('lanceur', ''))

    def test_noyau_du_banc_inchange(self):
        """Le noyau du banc n'est pas touché : ses étapes et son lanceur sont ceux de banc.py."""
        with tempfile.TemporaryDirectory() as d:
            banc = charger_banc_dans(d)
            ref = banc.construire_noyau({'pas': 30}, NOYAU)
            src_banc = base64.b64decode(re.search(r"b64decode\('([^']+)'\)", ref).group(1)).decode()
            _, complet = NR.construire_noyau_realisme({'pas': 30}, NOYAU, banc)
            self.assertTrue(complet.replace(NR.texte_noyau(), '').count('def etape_convertir') == 1)
            self.assertIn('def etape_juger', src_banc)
            self.assertIn('def etape_juger', complet)

    def test_dispatch_de_principal(self):
        with tempfile.TemporaryDirectory() as d:
            m, _ = noyau_dans(d, {'pas': 30, 'cfg': 0.7, 'fp16': True, 'graine': 1, 'pad_s': 0.25, 'fondu_s': 0.06,
                                  'extraits': {}, 'etapes': []})
            vus = []
            m.etape_realisme = lambda a: vus.append(a)
            with mock.patch.object(sys, 'argv', ['x', 'realisme', json.dumps({'etape': 'realisme', 'variantes': []})]):
                m.principal()
            self.assertEqual(vus, [{'etape': 'realisme', 'variantes': []}])
            m._principal_banc = lambda: vus.append('banc')
            with mock.patch.object(sys, 'argv', ['x', 'juger', '{}']):
                m.principal()
            self.assertEqual(vus[-1], 'banc')

    def test_plan_sans_rien_lancer(self):
        with tempfile.TemporaryDirectory() as d:
            banc = charger_banc_dans(d)
            (Path(d) / 'sessions' / 's.json').write_text(json.dumps(NR.SESSION_MODELE))
            p = NR.plan('s', banc)
            variantes = p['etapes'][0]['variantes']
            self.assertEqual(len(variantes), 1 + 1 + 1 + 2 + 4 + 0, 'R0, Ra, Rb, Rs x2, Rc x4')
            self.assertEqual(sum(1 for v in variantes if 'alias_de' in v), 1)
            self.assertEqual(p['conversions'], 8 * 2, '8 conversions réelles (9 variantes, 1 alias) sur chacun des 2 extraits')
            self.assertAlmostEqual(p['secondes_a_convertir'], 8 * 2 * 5.0)
            self.assertGreater(p['gpu_estime_min'], 10)
            self.assertLess(p['gpu_estime_min'], 90)

    def test_simuler_compile_le_noyau_et_ne_lance_rien(self):
        with tempfile.TemporaryDirectory() as d:
            charger_banc_dans(d)
            (Path(d) / 'sessions' / 's.json').write_text(json.dumps(NR.SESSION_MODELE))
            with mock.patch.dict(os.environ, {'BANC_DONNEES': str(d)}), mock.patch('subprocess.run') as lancement:
                p = NR.simuler('s', str(Path(d) / 'cas.json'))
            lancement.assert_not_called()
            self.assertTrue(p['noyau']['compile'])
            self.assertTrue(p['noyau']['etape_realisme'])
            self.assertEqual(p['conversions'], 16)


class FauxYing:
    """Ce que MoteurYing du banc fournit à la sous-classe du noyau réalisme, sans torch."""
    type = 'yingmusic'
    registre = []

    def __init__(self, checkpoint=None, config=None):
        self.chargement_s = 0.0
        self.torch = None   # remplacé après construction
        self.appels = []

    def preparer_ref(self, chemin):
        return {'mel2': Maillage(400), 'duree': 12.0, 'nom': os.path.basename(chemin), 'style_prompt': 'sp'}

    def length_regulator(self, s, longueur, f0, style2):
        return 'cond', np.ones(4, np.float32)

    def convertir_morceau(self, xs, ref, decalage, pas):
        self.appels.append({'ref': ref['nom'], 'decalage': decalage, 'echelle': self.echelle_style})
        t = np.arange(len(xs)) / SR
        return (0.2 * np.sin(2 * np.pi * 150 * 2 ** (decalage / 12) * t)).astype(np.float32)


CENTRES = {'cible-reference-A.wav': 200.0, 'cible-reference-B.wav': 130.0, 'cible-reference-C.wav': 300.0}


def moteur_faux(m):
    """La vraie sous-classe du noyau, posée sur FauxYing ; F0 estimé par FFT (aucun modèle)."""
    m.MoteurYing = FauxYing
    cls = m.classe_moteur_realisme()

    class Essai(cls):
        def centre_f0(self, chemin):
            return CENTRES[os.path.basename(chemin)]

        def f0_morceau(self, xs):
            spec = np.abs(np.fft.rfft(xs * np.hanning(len(xs))))
            f = np.fft.rfftfreq(len(xs), 1 / SR)
            return {'p10': float(f[spec.argmax()]), 'p50': float(f[spec.argmax()]), 'p90': float(f[spec.argmax()])}
    moteur = Essai()
    moteur.torch = m.torch_faux
    return moteur


@unittest.skipUnless(NUMPY, 'numpy requis')
class EtapeRealisme(unittest.TestCase):
    REGLAGES = {'pas': 30, 'cfg': 0.7, 'fp16': True, 'graine': 1234, 'pad_s': 0.25, 'fondu_s': 0.06, 'tessiture': TESSITURE,
                'extraits': {'un': {'decalage': -12, 'paroles': None}, 'deux': {'decalage': 0, 'paroles': None}},
                'etapes': [], 'session': 'essai'}

    def preparer(self, d, refs=('A', 'B', 'C')):
        m, _ = noyau_dans(d, self.REGLAGES)
        jeu = Path(d) / 'kaggle' / 'input' / 'jeu'
        for lettre in refs:
            sf_ecrire(str(jeu / f'cible-reference-{lettre}.wav'), np.zeros(SR), SR)
        for ex, (f1, f2) in {'un': (150.0, 300.0), 'deux': (200.0, 200.0)}.items():
            sf_ecrire(str(jeu / f'extrait-{ex}-voix.wav'), deux_notes(f1, f2, 14.0), SR)   # 29 s : plus que le morceau maximal, donc 2 morceaux
            (Path(d) / 'kaggle' / 'working' / ex).mkdir(parents=True, exist_ok=True)
            (Path(d) / 't' / ex).mkdir(parents=True, exist_ok=True)
        moteur = moteur_faux(m)
        m.creer_moteur = lambda mot, ck=None: moteur
        return m, moteur

    def lancer(self, m, variantes, **extra):
        with mock.patch.dict(sys.modules, {'soundfile': FAUX_SF}):
            m.etape_realisme({'etape': 'realisme', 'moteur': {'type': 'yingmusic'}, 'variantes': variantes, **extra})

    def fiche(self, d, ex, nom):
        return json.loads((Path(d) / 'kaggle' / 'working' / ex / f'{nom}.json').read_text())

    def test_decalage_arbitraire_et_musique_de_meme_decalage(self):
        with tempfile.TemporaryDirectory() as d:
            m, moteur = self.preparer(d)
            self.lancer(m, [{'nom': 'R3', 'decalage_dt': 3}, {'nom': 'Rm9', 'decalage_dt': -9}, {'nom': 'R0', 'musique_dt': None}])
            f = self.fiche(d, 'un', 'R3')
            self.assertEqual((f['decalage_dt'], f['musique_dt'], f['type']), (3, 3, 'version'))
            self.assertEqual(self.fiche(d, 'un', 'Rm9')['musique_dt'], 3)
            f0 = self.fiche(d, 'un', 'R0')
            self.assertEqual((f0['decalage_dt'], f0['musique_dt']), (-12, None), 'décalage de l\'extrait (un : −12)')
            self.assertEqual(self.fiche(d, 'deux', 'R0')['decalage_dt'], 0)
            self.assertEqual({a['decalage'] for a in moteur.appels if a['ref'] == 'cible-reference-A.wav'}, {3, -9, -12, 0})
            y = sf_lire(str(Path(d) / 'kaggle' / 'working' / 'un' / 'R3.flac'))[0]
            self.assertEqual(len(y), len(deux_notes(150.0, 300.0, 14.0)), 'même longueur que la voix source')

    def test_references_par_registre_selon_la_hauteur_du_passage(self):
        with tempfile.TemporaryDirectory() as d:
            m, moteur = self.preparer(d)
            reg = [{'nom': 'grave', 'reference': 'B'}, {'nom': 'median', 'reference': 'A'}, {'nom': 'aigu', 'reference': 'C'}]
            self.lancer(m, [{'nom': 'Rb', 'decalage_dt': 0, 'registres': reg}], extraits=['un'])
            f = self.fiche(d, 'un', 'Rb')
            self.assertEqual(f['reference'], 'registres')
            self.assertEqual([r['centre_hz'] for r in f['registres']], [130.0, 200.0, 300.0], 'centre mesuré sur chaque référence')
            self.assertEqual(f['usage_references'], {'grave': 1, 'aigu': 1}, 'morceau à 150 Hz : grave ; à 300 Hz : aigu')
            self.assertEqual(sorted(a['ref'] for a in moteur.appels), ['cible-reference-B.wav', 'cible-reference-C.wav'])
            # le décalage compte : voix décalée de -12, le morceau à 300 Hz devient 150 Hz (grave) et celui à 150 Hz, 75 Hz (grave)
            moteur.appels.clear()
            self.lancer(m, [{'nom': 'Rb12', 'decalage_dt': -12, 'registres': reg}], extraits=['un'])
            self.assertEqual(self.fiche(d, 'un', 'Rb12')['usage_references'], {'grave': 2})

    def test_une_reference_absente_saute_la_variante(self):
        with tempfile.TemporaryDirectory() as d:
            m, moteur = self.preparer(d, refs=('A',))
            reg = [{'nom': 'grave', 'reference': 'B'}, {'nom': 'aigu', 'reference': 'C'}]
            self.lancer(m, [{'nom': 'Rb', 'registres': reg}, {'nom': 'R0'}])
            f = self.fiche(d, 'un', 'Rb')
            self.assertEqual(f['type'], 'sautee')
            self.assertEqual(sorted(f['references_absentes']), ['cible-reference-B.wav', 'cible-reference-C.wav'])
            self.assertEqual(self.fiche(d, 'un', 'R0')['type'], 'version')

    def test_residu_de_style_coupe_ou_attenue(self):
        with tempfile.TemporaryDirectory() as d:
            m, moteur = self.preparer(d)
            vus = {}
            for echelle in (1.0, 0.5, 0):
                moteur.echelle_style = echelle
                vus[echelle] = moteur.length_regulator(None, 10, None, None)[1]
            self.assertTrue(np.array_equal(vus[1.0], np.ones(4)))
            self.assertTrue(np.allclose(vus[0.5], 0.5))
            self.assertIsNone(vus[0], 'résidu coupé : style_r = None')
            moteur.echelle_style = 1.0
            self.lancer(m, [{'nom': 'Rs', 'balayage': {'style_r': [0.5, 0]}}])
            self.assertEqual(self.fiche(d, 'un', 'Rs-style_r0.5')['style_r'], 0.5)
            self.assertEqual({a['echelle'] for a in moteur.appels}, {0.5, 0.0})

    def test_style_de_la_reference_jamais_touche(self):
        with tempfile.TemporaryDirectory() as d:
            m, moteur = self.preparer(d)
            moteur.echelle_style = 0
            moteur.preparer_ref('x/cible-reference-A.wav')
            self.assertEqual(moteur.echelle_style, 0, 'rétabli après la préparation de la référence')

    def test_balayage_de_cfg_applique_chaque_valeur_puis_restaure(self):
        with tempfile.TemporaryDirectory() as d:
            m, moteur = self.preparer(d)
            cfgs = []
            appel = moteur.convertir_morceau.__func__ if hasattr(moteur.convertir_morceau, '__func__') else None
            base = FauxYing.convertir_morceau

            def espion(self, xs, ref, decalage, pas):
                cfgs.append(m.B['cfg'])
                return base(self, xs, ref, decalage, pas)
            FauxYing.convertir_morceau = espion
            try:
                self.lancer(m, [{'nom': 'Ra', 'decalage_dt': 0}, {'nom': 'Rc', 'decalage_dt': 0, 'balayage': {'cfg': [0.5, 0.7, 1.0, 1.5]}}])
            finally:
                FauxYing.convertir_morceau = base
            self.assertEqual(sorted(set(cfgs)), [0.5, 0.7, 1.0, 1.5])
            self.assertEqual(m.B['cfg'], 0.7, 'cfg des réglages rétabli')
            self.assertEqual(self.fiche(d, 'un', 'Rc-cfg0.7')['type'], 'alias')
            self.assertEqual(self.fiche(d, 'un', 'Rc-cfg1.5')['cfg'], 1.5)

    def test_regle_dans_le_noyau(self):
        with tempfile.TemporaryDirectory() as d:
            m, moteur = self.preparer(d)
            moteur.f0_global = lambda x: {'p10': 245.0, 'p50': 295.0, 'p90': 411.0}
            self.lancer(m, [{'nom': 'Ra', 'decalage_dt': 'regle'}])
            f = self.fiche(d, 'un', 'Ra')
            self.assertEqual(f['regle']['decalage_voix_dt'], f['decalage_dt'])
            self.assertTrue(f['regle']['dans_confort'])
            self.assertEqual(f['decalage_dt'] - f['musique_dt'], 12 * f['regle']['octave_voix'])

    def test_extraits_restreints(self):
        with tempfile.TemporaryDirectory() as d:
            m, moteur = self.preparer(d)
            self.lancer(m, [{'nom': 'R0'}], extraits=['deux'])
            self.assertTrue((Path(d) / 'kaggle' / 'working' / 'deux' / 'R0.json').exists())
            self.assertFalse((Path(d) / 'kaggle' / 'working' / 'un' / 'R0.json').exists())


@unittest.skipUnless(NUMPY, 'numpy requis')
class SessionModele(unittest.TestCase):
    def test_modele_valide(self):
        s = NR.SESSION_MODELE
        self.assertEqual([e['etape'] for e in s['etapes']], ['realisme'], 'pas de juger : il suppose un décalage par extrait')
        v = NR.developper_variantes(s['etapes'][0]['variantes'], s['reglages'])
        self.assertEqual(len(v), 9)
        self.assertEqual(sorted(x['cfg'] for x in v if x['nom'].startswith('Rc-')), [0.5, 0.7, 1.0, 1.5])
        self.assertEqual({x['style_r'] for x in v if x['nom'].startswith('Rs-')}, {0.5, 0})
        self.assertTrue(any(x.get('registres') for x in v))
        texte = json.dumps(s)
        self.assertNotIn('/ho' 'me/', texte)
        self.assertNotIn('kaggle.' 'com', texte)
        self.assertNotIn('/', s['noyau'], 'nom seul : le compte est celui de la configuration')


if __name__ == '__main__':
    unittest.main()
