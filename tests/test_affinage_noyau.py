"""Noyau d'affinage (kaggle/affiner_kaggle.py) en simulation : sans GPU, sans Seed-VC, sans réseau. Les prises sont des
notes de synthèse (tests/commun.note) écrites dans un dossier temporaire ; train.py est remplacé par un faux entraîneur
qui imprime des pertes, sauvegarde des points de contrôle, plante ou ralentit à la demande."""
import base64
import json
import os
import re
import sys
import textwrap
import time
import types
import unittest
from unittest import mock

import numpy as np

from commun import DEPOT, FFMPEG, SR, Dossier, ecrire_wav, note

SOURCE = DEPOT / 'kaggle' / 'affiner_kaggle.py'


def charger():
    src = SOURCE.read_text(encoding='utf-8').replace("'__REGLAGES__'", repr(base64.b64encode(b'{}').decode()))
    m = types.ModuleType('affiner_kaggle_essai')
    m.__file__ = str(SOURCE)
    exec(compile(src, str(SOURCE), 'exec'), m.__dict__)
    return m


N = charger()

FAUX_TRAIN = textwrap.dedent('''
    import argparse, glob, os, re, sys, time
    p = argparse.ArgumentParser()
    for a in ('--config', '--dataset-dir', '--run-name', '--batch-size', '--max-steps', '--max-epochs', '--save-every',
              '--num-workers', '--gpu'):
        p.add_argument(a)
    a = p.parse_args()
    run = os.path.join('runs', a.run_name)
    os.makedirs(run, exist_ok=True)
    assert glob.glob(os.path.join(a.dataset_dir, '*.wav')), 'jeu vide'
    pas = 0
    for f in glob.glob(os.path.join(run, 'DiT_epoch_*_step_*.pth')):
        pas = max(pas, int(re.search(r'step_(\\d+)', f).group(1)))
    print('reprise a', pas, flush=True)
    plante = int(os.environ.get('FAUX_PLANTE_A', '0'))
    toujours = os.environ.get('FAUX_TOUJOURS') == '1'
    drapeau = os.path.join(run, 'plante')
    while pas < int(a.max_steps):
        pas += 1
        time.sleep(float(os.environ.get('FAUX_LENT', '0')))
        print(f'Step {pas} loss_mel: {1 / pas:.4f} loss_kl {2 / pas:.4f}', flush=True)
        if pas % int(a.save_every) == 0:
            open(os.path.join(run, f'DiT_epoch_00000_step_{pas:05d}.pth'), 'wb').write(b'x' * 100)
            vieux = sorted(glob.glob(os.path.join(run, 'DiT_epoch_*_step_*.pth')))[:-2]
            for v in vieux:
                os.remove(v)
        if plante and pas == plante and (toujours or not os.path.exists(drapeau)):
            open(drapeau, 'w').close()
            sys.exit(7)
    open(os.path.join(run, 'ft_model.pth'), 'wb').write(b'y' * 50)
''')


def prise(chemin, duree=40.0, niveau=-30, bruit=None, sature=False):
    """Prise de chant de synthèse : phrases de quatre notes tenues, pauses de 0,6 s, fond très bas."""
    x = np.zeros(int(duree * SR))
    t, k = 0.5, 0
    while t + 2.4 < duree:
        for i in range(4):
            x += note(220 * 2 ** ((k + i) % 5 / 12), t + i * 0.6, t + i * 0.6 + 0.55, niveau, duree, fondu=0.03)
        t += 2.4 + 0.6
        k += 1
    x += np.random.default_rng(1).standard_normal(len(x)) * 10 ** ((bruit if bruit is not None else -75) / 20)
    if sature:
        x = np.clip(x * 40, -1, 1)
    ecrire_wav(chemin, x)


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Donnees(unittest.TestCase):
    def test_decoupage_niveau_et_crete(self):
        with Dossier() as d:
            for i in range(3):
                prise(d / f'chant-{i}.wav', 45.0, niveau=-38 + 4 * i)
            r = N.preparer_donnees(sorted(str(f) for f in d.glob('chant-*.wav')), str(d / 'morceaux'), {})
            self.assertGreaterEqual(r['morceaux'], 9)
            self.assertEqual(r['refuses'], {})
            for f in (d / 'morceaux').glob('*.wav'):
                x = N.lire_audio(str(f))
                self.assertGreaterEqual(len(x) / SR, 3.0)
                self.assertLessEqual(len(x) / SR, 12.0)
                self.assertLess(20 * np.log10(np.abs(x).max()), -0.9)   # crête sous -1 dBFS
                env = N.enveloppe_db(x)
                self.assertAlmostEqual(N.niveau_actif_db(x, env, N.seuil_silence(env)), -23.0, delta=1.5)
            self.assertTrue(all(p['gain_db'] > 0 for p in r['prises']))

    def test_prise_saturee_refusee(self):
        with Dossier() as d:
            prise(d / 'chant-sat.wav', 30.0, sature=True)
            prise(d / 'chant-ok.wav', 30.0)
            r = N.preparer_donnees([str(d / 'chant-sat.wav'), str(d / 'chant-ok.wav')], str(d / 'm'), {})
            self.assertGreater(r['refuses'].get('saturé', 0), 0)
            self.assertTrue(all(f.name.startswith('p01') for f in (d / 'm').glob('*.wav')))

    def test_prise_bruitee_refusee(self):
        with Dossier() as d:
            prise(d / 'chant-bruit.wav', 30.0, niveau=-30, bruit=-34)
            r = N.preparer_donnees([str(d / 'chant-bruit.wav')], str(d / 'm'), {})
            self.assertEqual(r['morceaux'], 0)
            self.assertIn('bruité', r['refuses'])

    def test_prise_trop_faible_et_illisible(self):
        with Dossier() as d:
            prise(d / 'chant-faible.wav', 20.0, niveau=-62, bruit=-95)
            (d / 'chant-casse.wav').write_bytes(b'pas de l audio')
            r = N.preparer_donnees([str(d / 'chant-faible.wav'), str(d / 'chant-casse.wav')], str(d / 'm'), {})
            self.assertEqual(r['refuses'], {'trop faible': 1})
            self.assertEqual(len(r['illisibles']), 1)

    def test_passe_haut(self):
        t = np.arange(SR * 2) / SR
        x = (np.sin(2 * np.pi * 30 * t) + np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        y = N.passe_haut(x, 70)
        spectre = np.abs(np.fft.rfft(y)) / len(y)
        self.assertLess(spectre[60], 0.05 * spectre[880])   # 30 Hz : binaire 60 sur 2 s
        self.assertGreater(spectre[880], 0.45)

    def test_phrase_longue_coupee(self):
        env = np.full(6000, -20.0)   # 60 s de chant continu, creux toutes les 5 s
        env[500::500] = -50.0
        morceaux, _courts = N.decouper(env, -40.0, {})
        self.assertGreater(len(morceaux), 4)
        self.assertTrue(all(3.0 <= b - a <= 12.5 for a, b in morceaux))


class Pertes(unittest.TestCase):
    def test_lecture(self):
        self.assertEqual(N.lire_pertes('Step 120 loss_mel: 0.2500 loss_kl 1e-3'),
                         {'pas': 120, 'loss_mel': 0.25, 'loss_kl': 0.001})
        self.assertEqual(N.lire_pertes('iters 5, loss=0.5')['pas'], 5)
        self.assertIsNone(N.lire_pertes('Loading model'))

    def test_resume(self):
        pts = [{'pas': i, 'loss_mel': 1 / i} for i in range(1, 1001)]
        r = N.resume_pertes(pts, 100)
        self.assertLessEqual(len(r['points']), 100)
        self.assertEqual(r['nombre'], 1000)
        self.assertEqual(r['derniere']['pas'], 1000)
        self.assertLess(r['moyenne_fin']['loss_mel'], r['premiere']['loss_mel'])
        self.assertEqual(N.resume_pertes([])['nombre'], 0)


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Simulation(unittest.TestCase):
    def reglages(self, d, **plus):
        (d / 'train.py').write_text(FAUX_TRAIN)
        (d / 'entrees').mkdir(exist_ok=True)
        for i in range(3):
            if not (d / 'entrees' / f'chant-{i}.wav').exists():
                prise(d / 'entrees' / f'chant-{i}.wav', 45.0)
        return {'voix': 'voixA', 'simulation': True, 'entrees': str(d / 'entrees'), 'sortie': str(d / 'sortie'),
                'tmp': str(d / 'tmp'), 'seedvc': str(d / 'seedvc'), 'commande_train': [sys.executable, str(d / 'train.py')],
                'max_steps': 40, 'sauvegarde_tous': 10, 'batch': 2, **plus}

    def lancer(self, R, **env):
        N.DEBUT = time.time()
        with mock.patch.dict(os.environ, {k: str(v) for k, v in env.items()}):
            return N.main(R)

    def rapport(self, d, sortie='sortie'):
        return json.loads((d / sortie / 'rapport.json').read_text())

    def test_affinage_complet(self):
        with Dossier() as d:
            self.assertEqual(self.lancer(self.reglages(d)), 0)
            r = self.rapport(d)
            self.assertTrue(r['complet'])
            self.assertEqual(r['point']['pas'], 40)
            self.assertTrue((d / 'sortie' / 'affinage' / 'voixA.pth').exists())
            self.assertFalse((d / 'sortie' / 'reprise').exists())
            self.assertEqual(r['entrainement']['pertes']['nombre'], 40)
            self.assertEqual(r['entrainement']['pertes']['derniere']['pas'], 40)
            self.assertGreaterEqual(r['donnees']['utile_s'], 120)
            self.assertEqual(r['reglages']['batch'], 2)
            self.assertIn('total_s', r['temps'])

    def test_plantage_puis_reprise_seule(self):
        with Dossier() as d:
            self.assertEqual(self.lancer(self.reglages(d), FAUX_PLANTE_A=25), 0)
            r = self.rapport(d)
            lanc = r['entrainement']['lancements']
            self.assertEqual(len(lanc), 2)
            self.assertEqual(lanc[0]['code'], 7)
            self.assertEqual(lanc[1]['reprend_a'], 20)   # dernier point sauvegardé avant le plantage
            self.assertTrue(r['complet'])

    def test_plantages_sans_fin_abandonnes(self):
        with Dossier() as d:
            R = self.reglages(d, relances=1)
            self.assertEqual(self.lancer(R, FAUX_PLANTE_A=15, FAUX_TOUJOURS=1), 0)
            r = self.rapport(d)
            self.assertFalse(r['complet'])
            self.assertEqual(len(r['entrainement']['lancements']), 2)
            self.assertTrue((d / 'sortie' / 'reprise').is_dir())

    def test_budget_puis_reprise_dans_une_autre_session(self):
        with Dossier() as d:
            R = self.reglages(d, max_steps=100000, sauvegarde_tous=5, budget_s=8, marge_s=3)
            self.assertEqual(self.lancer(R, FAUX_LENT=0.05), 0)
            r = self.rapport(d)
            self.assertFalse(r['complet'])
            self.assertTrue(r['entrainement']['lancements'][-1]['arret_budget'])
            reprise = list((d / 'sortie' / 'reprise').glob('DiT_epoch_*_step_*.pth'))
            self.assertEqual(len(reprise), 1)
            pas = int(re.search(r'step_(\d+)', reprise[0].name).group(1))
            self.assertGreater(pas, 0)
            self.assertTrue((d / 'sortie' / 'affinage' / 'voixA.pth').exists())   # point utilisable quand même
            # deuxième session : le point revient par le jeu de données (reprise/), l'entraînement finit
            R2 = self.reglages(d, max_steps=pas + 10, sauvegarde_tous=5, sortie=str(d / 'sortie2'),
                               tmp=str(d / 'tmp2'), seedvc=str(d / 'seedvc2'), entrees_reprise=str(reprise[0].parent))
            self.assertEqual(self.lancer(R2), 0)
            r2 = self.rapport(d, 'sortie2')
            self.assertEqual(r2['reprise']['pas_repris'], pas)
            self.assertEqual(r2['entrainement']['lancements'][0]['reprend_a'], pas)
            self.assertTrue(r2['complet'])

    def test_pas_assez_de_chant(self):
        with Dossier() as d:
            R = self.reglages(d)
            for f in (d / 'entrees').glob('*.wav'):
                f.unlink()
            prise(d / 'entrees' / 'chant-court.wav', 20.0)
            self.assertEqual(self.lancer(R), 5)
            self.assertIn('pas assez', self.rapport(d)['erreur'])
            self.assertFalse((d / 'sortie' / 'affinage').exists())

    def test_aucune_prise(self):
        with Dossier() as d:
            R = self.reglages(d)
            for f in (d / 'entrees').glob('*.wav'):
                f.unlink()
            self.assertEqual(self.lancer(R), 4)


class Contrat(unittest.TestCase):
    def test_marques_pour_doubler_py(self):
        src = SOURCE.read_text(encoding='utf-8')
        self.assertEqual(src.count("SOURCE = '__SOURCE__'"), 1)   # pousser_noyau le remplace, exactement une fois
        self.assertEqual(src.count("'__REGLAGES__'"), 1)

    def test_meme_seedvc_que_le_noyau_de_doublage(self):
        doublage = (DEPOT / 'kaggle' / 'doubler_kaggle.py').read_text(encoding='utf-8')
        commit = re.search(r"^COMMIT = '([0-9a-f]{40})'", doublage, re.M).group(1)
        self.assertEqual(N.COMMIT, commit)   # un seul montage d'outils pour les deux noyaux

    def test_commande_train(self):
        c = N.commande_train({'batch': 2, 'max_steps': 1500, 'sauvegarde_tous': 250}, 'voixA', N.PRESET, '/d')
        self.assertIn('--save-every', c)
        self.assertEqual(c[c.index('--run-name') + 1], 'voixA')
        self.assertEqual(c[c.index('--max-steps') + 1], '1500')


if __name__ == '__main__':
    unittest.main()
