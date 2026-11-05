"""Deux GPU, côté doubler.py : clés « gpu » et « kaggle.machine », option --gpu, machine demandée à l'envoi du noyau
(et repli sur la machine à deux T4 si Kaggle refuse une autre machine), réglage gpus transmis au noyau, estimation de la
durée à deux cartes. Comptes et noms inventés ; HOME est un dossier temporaire."""
import argparse
import contextlib
import io
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from commun import DOUBLER, Dossier, charger_doubler, python


class Base(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.D = charger_doubler(self.home)

    def silencieux(self, f, *a, **k):
        with contextlib.redirect_stdout(io.StringIO()):
            return f(*a, **k)


class Configuration(Base):
    def test_cle_gpu(self):
        self.assertIn('gpu', self.D.CLES_CONFIG)
        self.assertEqual(self.D.gpu_config({}), 'auto')
        for v in ('1', '2', 'auto', 'AUTO'):
            self.silencieux(self.D.ecrire_config, 'gpu', v)
            self.assertEqual(json.loads(self.D.CONFIG.read_text())['gpu'], v.lower())
        for v in ('3', '0', 'deux'):
            with self.assertRaises(SystemExit):
                self.D.ecrire_config('gpu', v)
        self.assertEqual(self.D.gpu_config({'gpu': '1'}), '1')
        self.assertEqual(self.silencieux(self.D.gpu_config, {'gpu': 'beaucoup'}), 'auto', 'illisible : auto, avec un avis')

    def test_cle_machine(self):
        self.assertIn('kaggle.machine', self.D.CLES_CONFIG)
        self.silencieux(self.D.ecrire_config, 'kaggle.compte', 'jules')
        self.silencieux(self.D.ecrire_config, 'kaggle.machine', 'NvidiaL4')
        conf = json.loads(self.D.CONFIG.read_text())
        self.assertEqual(conf['kaggle'], {'compte': 'jules', 'machine': 'NvidiaL4'})
        self.D.configurer_kaggle(conf)
        self.assertEqual(self.D.MACHINE, 'NvidiaL4')
        self.D.configurer_kaggle({'kaggle': {'compte': 'jules'}})
        self.assertEqual(self.D.MACHINE, 'NvidiaTeslaT4', 'défaut : la machine à deux T4')
        for v in ('Nvidia T4', 'x', '../autre'):
            with self.assertRaises(SystemExit):
                self.D.ecrire_config('kaggle.machine', v)

    def test_ligne_de_commande(self):
        env = {**os.environ, 'HOME': str(self.home)}
        r = subprocess.run([python(), str(DOUBLER), '--help'], capture_output=True, text=True, env=env, timeout=60)
        self.assertIn('--gpu', r.stdout)
        r = subprocess.run([python(), str(DOUBLER), '--config', 'gpu', '1'], capture_output=True, text=True, env=env,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([python(), str(DOUBLER), '--config', '--json'], capture_output=True, text=True, env=env, timeout=60)
        effectif = json.loads(r.stdout)
        self.assertEqual(effectif['gpu'], '1')
        self.assertEqual(effectif['kaggle']['machine'], 'NvidiaTeslaT4')
        r = subprocess.run([python(), str(DOUBLER), '--config'], capture_output=True, text=True, env=env, timeout=60)
        self.assertIn('kaggle.machine NvidiaTeslaT4', r.stdout)
        r = subprocess.run([python(), str(DOUBLER), 'x.mp4', '--gpu', '3'], capture_output=True, text=True, env=env, timeout=60)
        self.assertNotEqual(r.returncode, 0)


class Noyau(Base):
    def test_metadonnees_et_reglage(self):
        meta = self.D.metadonnees_noyau('jules/doublage-voix-gpu', ['jules/jeu'], [], 'NvidiaTeslaT4')
        self.assertEqual((meta['machine_shape'], meta['enable_gpu'], meta['is_private']), ('NvidiaTeslaT4', True, True))
        a = argparse.Namespace(pas=30, paroles=True, moteur='yingmusic', sans_nettoyage=False, mode='auto', octave='auto',
                               comparer_modeles=False, comparer_pas=None, modele_parole=None, gpu='1')
        (self.home / 'Music' / 'Voix-là' / 'voix' / 'jules').mkdir(parents=True)
        voix = {'id': 'jules', 'f0': 110.0, 'reference': None}
        with mock.patch.object(self.D, 'voix_membres', lambda v: [voix]):
            self.assertEqual(self.D.reglages_noyau(a, voix, None, [])['gpus'], 1)
            a.gpu = 'auto'
            self.assertEqual(self.D.reglages_noyau(a, voix, None, [])['gpus'], 'auto')

    def pousser(self, reponses):
        envoyes = []

        def kaggle(*args):
            if args[:2] == ('kernels', 'push'):
                envoyes.append(json.loads((Path(args[3]) / 'kernel-metadata.json').read_text())['machine_shape'])
                return reponses.pop(0)
            return 0, ''
        with mock.patch.object(self.D, 'kaggle', kaggle), mock.patch.object(self.D.time, 'sleep', lambda s: None), \
                contextlib.redirect_stdout(io.StringIO()) as dit:
            self.D.pousser_noyau({}, self.tmp / 'k', 600, 'jules/doublage-voix-gpu', ['jules/jeu'])
        return envoyes, dit.getvalue()

    def test_machine_envoyee(self):
        envoyes, dit = self.pousser([(0, 'Kernel version 3 successfully pushed.')])
        self.assertEqual(envoyes, ['NvidiaTeslaT4'])
        self.assertIn('machine NvidiaTeslaT4', dit)

    def test_autre_machine_refusee_repli_sur_deux_t4(self):
        self.D.MACHINE = 'NvidiaL4'
        envoyes, dit = self.pousser([(1, 'Invalid machine shape: NvidiaL4'), (0, 'Kernel version 4 successfully pushed.')])
        self.assertEqual(envoyes, ['NvidiaL4', 'NvidiaTeslaT4'])
        self.assertIn('refusée', dit)

    def test_machine_par_defaut_refusee_pas_de_boucle(self):
        with self.assertRaises(RuntimeError):
            self.pousser([(1, 'Invalid machine shape: NvidiaTeslaT4')])


class Estimation(Base):
    def test_une_carte_comme_avant(self):
        for k in ({}, {'mode': 'parole'}, {'nettoyage': False}, {'paroles': False}):
            self.assertEqual(self.D.estimer_noyau_s([200.0], 30, self.tmp / 'rien', **k),
                             self.D.estimer_noyau_s([200.0], 30, self.tmp / 'rien', cartes=1, **k))

    def test_deux_cartes_plus_court_en_chant(self):
        un = self.D.estimer_noyau_s([230.0], 30, self.tmp / 'rien')
        deux = self.D.estimer_noyau_s([230.0], 30, self.tmp / 'rien', cartes=2)
        self.assertLess(deux, un)
        # sans historique : nettoyage x 0,70 et conversion x 0,55 ; le reste ne change pas
        t_net, t_conv = 2.3, 1.5 * 0.045 * 30
        self.assertAlmostEqual(un - deux, 230 * (t_net * 0.30 + t_conv * 0.45), delta=1)

    def test_rien_a_partager(self):
        """Parole d'une vidéo seule : ni nettoyage ni YingMusic-SVC, une seule séparation : la même durée."""
        self.assertEqual(self.D.estimer_noyau_s([200.0], 30, self.tmp / 'rien', mode='parole', cartes=2),
                         self.D.estimer_noyau_s([200.0], 30, self.tmp / 'rien', mode='parole'))
        seedvc = self.D.estimer_noyau_s([200.0], 30, self.tmp / 'rien', moteur='seedvc', nettoyage=False, cartes=2)
        self.assertEqual(seedvc, self.D.estimer_noyau_s([200.0], 30, self.tmp / 'rien', moteur='seedvc', nettoyage=False))

    def test_lot_separation_partagee(self):
        un = self.D.estimer_noyau_s([100.0, 100.0], 30, self.tmp / 'rien', mode='parole')
        deux = self.D.estimer_noyau_s([100.0, 100.0], 30, self.tmp / 'rien', mode='parole', cartes=2)
        self.assertAlmostEqual(un - deux, 200 * 0.8 * 0.5, delta=1)

    def test_historique_a_deux_cartes(self):
        d = self.tmp / 'sorties' / 'clip' / 'travail' / 'jules'
        d.mkdir(parents=True)
        sk = {'nettoyage_totale_s': 330.0, 'chargement_nettoyage_s': 30.0, 'nettoyage_gpu_s': 500.0,
              'yingmusic_total_s': 220.0, 'chargement_yingmusic_s': 20.0, 'yingmusic_gpu_s': 400.0}
        (d / 'rapport.json').write_text(json.dumps({'duree_s': 200.0, 'temps': {}, 'temps_local': {
            'gpus': 2, 'videos_dans_la_session': 1, 'session_kaggle': sk}}))
        un = self.D.estimer_noyau_s([230.0], 30, self.tmp / 'sorties')
        deux = self.D.estimer_noyau_s([230.0], 30, self.tmp / 'sorties', cartes=2)
        t_net, t_conv = 2.3, 1.5 * 0.045 * 30
        self.assertAlmostEqual(un - deux, 230 * (t_net * (1 - 0.6) + t_conv * (1 - 0.5)), delta=1)

    def test_cartes_prevues(self):
        self.assertEqual(self.D.cartes_prevues('auto', 'NvidiaTeslaT4'), 2)
        self.assertEqual(self.D.cartes_prevues('1', 'NvidiaTeslaT4'), 1)
        self.assertEqual(self.D.cartes_prevues('auto', 'NvidiaL4'), 1)
        self.assertEqual(self.D.cartes_prevues('2', 'NvidiaL4'), 2)


if __name__ == '__main__':
    unittest.main()
