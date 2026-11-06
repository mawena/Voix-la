"""Précision et recouvrement des Roformer du noyau (séparation, karaoké, dereverb) : réglages lus, options passées à
audio-separator, et repli en fp32 quand la demi-précision rend un son non fini.

Sans GPU ni modèle : audio_separator et soundfile sont imités, les chemins du noyau (/tmp, /kaggle) sont déplacés dans
un dossier temporaire."""
import base64
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from commun import NOYAU, SR, Dossier, charger_noyau

N = charger_noyau()


def noyau_dans(dossier):
    src = NOYAU.read_text(encoding='utf-8').replace("'__REGLAGES__'", repr(base64.b64encode(b'{}').decode()))
    src = src.replace('/tmp/', f'{dossier}/t/').replace('/kaggle/', f'{dossier}/kaggle/')
    m = types.ModuleType('noyau_precision')
    m.__file__ = str(NOYAU)
    exec(compile(src, 'noyau_precision.py', 'exec'), m.__dict__)
    (Path(dossier) / 't').mkdir(parents=True, exist_ok=True)
    return m


def sf_ecrire(chemin, y, sr, subtype=None, format=None):
    with open(chemin, 'wb') as f:
        np.save(f, np.asarray(y, np.float32))


def sf_lire(chemin, dtype='float32', always_2d=False):
    with open(chemin, 'rb') as f:
        y = np.load(f)
    if always_2d and y.ndim == 1:
        y = y[:, None]
    return y.astype(dtype), SR


class FauxSeparateur:
    """Garde les options reçues ; en demi-précision, rend des NaN si deborde est vrai."""
    crees, deborde = [], False

    def __init__(self, model_file_dir, output_dir, mdxc_params=None, **autres):
        options = {k: v for k, v in autres.items() if k in ('use_autocast', 'use_native_fp16', 'use_torch_compile')}
        self.dossier, self.options, self.mdxc = output_dir, options, mdxc_params or {}
        FauxSeparateur.crees.append({'options': options, 'overlap': self.mdxc.get('overlap')})
        demi = options.get('use_native_fp16') or options.get('use_autocast')
        self.model_instance = types.SimpleNamespace(effective_precision='native_fp16' if demi else 'fp32')

    def load_model(self, model_filename):
        self.modele = model_filename

    def separate(self, fichier, custom_output_names=None):
        x, _ = sf_lire(fichier, always_2d=True)
        y = 0.6 * x
        if FauxSeparateur.deborde and (self.options.get('use_native_fp16') or self.options.get('use_autocast')):
            y = np.full_like(x, np.nan)
        base = os.path.splitext(os.path.basename(fichier))[0]
        if custom_output_names:   # séparation du noyau : voix_brute.wav et autre_brut.wav
            noms = ['voix_brute.wav', 'autre_brut.wav']
        elif 'dereverb' in self.modele:
            noms = [f'{base}_(noreverb)_x.wav', f'{base}_(reverb)_x.wav']
        else:
            noms = [f'{base}_(Vocals)_x.wav', f'{base}_(Instrumental)_x.wav']
        sf_ecrire(os.path.join(self.dossier, noms[0]), y, SR)
        sf_ecrire(os.path.join(self.dossier, noms[1]), x - np.nan_to_num(y), SR)
        return noms


def modules_faux():
    sep = types.ModuleType('audio_separator.separator')
    sep.Separator = FauxSeparateur
    torch = types.SimpleNamespace(load=lambda *a, **k: None, cuda=types.SimpleNamespace(empty_cache=lambda: None))
    return {'soundfile': types.SimpleNamespace(write=sf_ecrire, read=sf_lire), 'audio_separator': types.ModuleType('audio_separator'),
            'audio_separator.separator': sep, 'torch': torch}


def son(duree=2.0):
    t = np.arange(int(duree * SR)) / SR
    return (0.3 * np.sin(2 * np.pi * 220 * t)[:, None] * np.array([1.0, 0.8])).astype(np.float32)


class Reglages(unittest.TestCase):
    def test_defaut_et_reglages(self):
        r = N.reglage_separation({})
        self.assertEqual((r['precision'], r['recouvrement']), (N.SEPARATION_DEFAUT['precision'], N.SEPARATION_DEFAUT['recouvrement']))
        self.assertEqual(r['options'], N.PRECISIONS_ROFORMER[r['precision']])
        r = N.reglage_separation({'separation_precision': 'fp16', 'separation_recouvrement': 2})
        self.assertEqual(r, {'precision': 'fp16', 'options': {'use_native_fp16': True}, 'recouvrement': 2})
        self.assertEqual(N.reglage_separation({'separation_precision': 'int8'})['precision'], 'fp32', 'inconnue : fp32')
        self.assertEqual(N.PRECISIONS_ROFORMER['fp32'], {}, 'fp32 : le Separator d\'avant, sans option')

    def test_defauts_du_banc_vitesse(self):
        """Banc du 06/11/2026 : autocast par défaut (même vitesse que le fp16 natif, plus près du fp32), recouvrement
        inchangé (2 change nettement les chœurs secs : il attend l'écoute)."""
        self.assertEqual(N.SEPARATION_DEFAUT, {'precision': 'autocast', 'recouvrement': 4})
        self.assertEqual(N.NETTOYAGE_PRECISION_DEFAUT, 'autocast')
        self.assertEqual(N.reglage_separation({})['options'], {'use_autocast': True})
        self.assertEqual(N.reglage_separation({'separation_precision': 'fp32'})['options'], {}, 'fp32 : retour possible')


class SeparationDuNoyau(unittest.TestCase):
    def lancer(self, d, reglage):
        os.makedirs(f'{d}/sep', exist_ok=True)
        sf_ecrire(f'{d}/entree.wav', son(), SR)
        travaux = [{'id': 'v1', 'entree': f'{d}/entree.wav', 'voix': f'{d}/voix.wav', 'reste': f'{d}/reste.wav',
                    'gain_entree': 1.0, 'voix_flac': None, 'separation': reglage}]
        with mock.patch.dict(sys.modules, modules_faux()), \
                mock.patch.object(sys, 'argv', ['separer.py', json.dumps(travaux), f'{d}/sep']):
            exec(compile(N.SEPARER, 'separer.py', 'exec'), {'__name__': '__main__'})
        return sf_lire(f'{d}/voix.wav', always_2d=True)[0]

    def setUp(self):
        FauxSeparateur.crees, FauxSeparateur.deborde = [], False

    def test_options_et_recouvrement(self):
        with Dossier() as d:
            v = self.lancer(d, N.reglage_separation({'separation_precision': 'fp16', 'separation_recouvrement': 2}))
        self.assertEqual(FauxSeparateur.crees, [{'options': {'use_native_fp16': True}, 'overlap': 2}])
        np.testing.assert_allclose(v, 0.6 * son(), rtol=1e-5, atol=1e-6)

    def test_sans_reglage_comme_avant(self):
        with Dossier() as d:
            self.lancer(d, None)
        self.assertEqual(FauxSeparateur.crees, [{'options': {}, 'overlap': 4}])

    def test_son_non_fini_refait_en_fp32(self):
        FauxSeparateur.deborde = True
        with Dossier() as d:
            v = self.lancer(d, N.reglage_separation({'separation_precision': 'fp16'}))
        self.assertTrue(np.isfinite(v).all())
        self.assertEqual([c['options'] for c in FauxSeparateur.crees], [{'use_native_fp16': True}, {}])


class NettoyageDuNoyau(unittest.TestCase):
    def lancer(self, d, precision):
        m = noyau_dans(d)
        voix = f'{d}/t/sep/v1_voix.wav'
        os.makedirs(os.path.dirname(voix), exist_ok=True)
        sf_ecrire(voix, son(), SR)
        travail = {'videos': [{'id': 'v1', 'voix': voix, 'dossier': f'{d}/t/net/v1'}], 'karaoke': ['karaoke.ckpt'],
                   'dereverb': 'dereverb.ckpt', 'recouvrement': 2, 'precision': precision, 'resultat': f'{d}/t/net/res.json'}
        with mock.patch.dict(sys.modules, modules_faux()), mock.patch.object(m, 'log', lambda *a: None):
            m.etape_nettoyer(travail)
        return m.lire_json(travail['resultat'], {}), m

    def setUp(self):
        FauxSeparateur.crees, FauxSeparateur.deborde = [], False

    def test_precision_passee_aux_deux_modeles(self):
        with Dossier() as d:
            res, _ = self.lancer(d, 'fp16')
            self.assertEqual(res['precision'], 'fp16')
            self.assertEqual([c['options'] for c in FauxSeparateur.crees], [{'use_native_fp16': True}] * 2)
            self.assertEqual({c['overlap'] for c in FauxSeparateur.crees}, {2})
            self.assertTrue(res['videos']['v1'].get('lead_sec'))

    def test_fp32_sans_option_et_sans_cle(self):
        with Dossier() as d:
            res, _ = self.lancer(d, None)
        self.assertNotIn('precision', res, 'fp32 : le résultat d\'avant')
        self.assertEqual([c['options'] for c in FauxSeparateur.crees], [{}, {}])

    def test_son_non_fini_refait_en_fp32(self):
        FauxSeparateur.deborde = True
        with Dossier() as d:
            res, m = self.lancer(d, 'autocast')
            self.assertEqual(len(res['repli_fp32']), 3, 'karaoké et deux dereverb refaits')
            lead = sf_lire(res['videos']['v1']['lead_sec'], always_2d=True)[0]
        self.assertTrue(np.isfinite(lead).all())
        fus = N.fusionner_nettoyage([res, {'videos': {}}])
        self.assertEqual((fus['precision'], len(fus['repli_fp32'])), ('autocast', 3))


if __name__ == '__main__':
    unittest.main()
