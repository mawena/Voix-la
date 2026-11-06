"""Deux GPU, côté doubler.py : clés « gpu » et « kaggle.machine », option --gpu, machine demandée à l'envoi du noyau
(et repli sur la machine à deux T4 si Kaggle refuse une autre machine), réglage gpus transmis au noyau, estimation de la
durée à deux cartes. Comptes et noms inventés ; HOME est un dossier temporaire."""
import argparse
import contextlib
import io
import json
import os
import shutil
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
        un = self.D.estimer_noyau_s([230.0], 30, self.tmp / 'rien', paroles=False)
        deux = self.D.estimer_noyau_s([230.0], 30, self.tmp / 'rien', paroles=False, cartes=2)
        self.assertLess(deux, un)
        # sans historique : nettoyage x 0,70 et conversion x 0,55 ; à une carte, ces deux étapes coûtent en plus ce
        # qu'elles prennent hors de leur temps de GPU et de leur chargement ; le reste ne change pas
        t_net, t_conv = 2.3, 1.5 * 0.045 * 30
        self.assertAlmostEqual(un - deux, 230 * (t_net * 0.30 + t_conv * 0.45) + sum(self.D.SURCOUTS_UNE_CARTE_S.values()),
                               delta=1)

    def test_paroles_pendant_la_conversion(self):
        """Deux cartes, YingMusic-SVC : les paroles (P, sur la carte 1, attente du chargement de la carte 0 comprise)
        coûtent la moitié de leur durée, pas moins que ce qui dépasse la conversion (Y) ; une carte : P en entier."""
        est = lambda **k: self.D.estimer_noyau_s([230.0], k.pop('pas', 30), self.tmp / 'rien', **k)
        p_seq, p_carte = 25 + 230 * 0.45, 25 + 25 + 230 * 0.45   # défauts sans historique
        self.assertAlmostEqual(est() - est(paroles=False), p_seq, delta=1)
        self.assertAlmostEqual(est(cartes=2) - est(cartes=2, paroles=False), 0.5 * p_carte, delta=1)
        # conversion très courte (2 pas) : les paroles dépassent, tout ce qui dépasse compte
        y = 230 * 1.5 * 0.045 * 2 * 0.55
        self.assertGreater(p_carte - y, 0.5 * p_carte)
        self.assertAlmostEqual(est(cartes=2, pas=2) - est(cartes=2, pas=2, paroles=False), p_carte - y, delta=1)
        # parole, ou Seed-VC : rien ne tourne pendant les paroles, P en entier
        for k in ({'mode': 'parole'}, {'moteur': 'seedvc'}):
            self.assertAlmostEqual(est(cartes=2, **k) - est(cartes=2, paroles=False, **k), p_seq, delta=1)

    def rapport(self, nom, duree, sk, temps, gpus=1, attente=5.0, reel=None, nettoyage=True, pas=30, **tl):
        d = self.tmp / 'sorties' / nom / 'travail' / 'jules'
        d.mkdir(parents=True)
        (d / 'rapport.json').write_text(json.dumps({
            'duree_s': duree, 'temps': temps, 'nettoyage': {'applique': nettoyage},
            'variantes': {'voix_convertie.flac': {'conversion_s': temps.get('conversion_s'), 'pas_diffusion': pas}},
            'temps_local': {'gpus': gpus, 'videos_dans_la_session': 1, 'attente_demarrage_noyau_s': attente,
                            'noyau_depuis_envoi_s': reel, 'session_kaggle': sk, **tl}}))

    def test_une_session_passee_redonne_sa_duree(self):
        """Avec pour seul historique une session inventée, l'estimation du même doublage redonne sa durée réelle : une
        carte, deux cartes, paroles après la conversion ou sur la carte 1 pendant la conversion. Le reste de la session
        (démarrage, chargement de la séparation, fin du noyau) se mesure sur elle."""
        temps = {'separation_s': 50.0, 'analyse_s': 10.0, 'nettoyage_s': 200.0, 'conversion_s': 180.0}
        base = {'installation_s': 40.0, 'chargement_modeles_s': 30.0, 'chargement_nettoyage_s': 20.0,
                'chargement_yingmusic_s': 15.0}
        cas = {
            # une carte, sans paroles : nettoyage et conversion à la suite, chargements compris
            'une carte': (1, False, {'nettoyage_totale_s': 220.0, 'yingmusic_total_s': 195.0}, {}, 700.0),
            # une carte, paroles après la conversion : 15 s fixes, 30 s de transcription
            'une carte, paroles': (1, True, {'nettoyage_totale_s': 220.0, 'yingmusic_total_s': 195.0,
                                             'paroles_copie_s': 20.0, 'paroles_total_s': 45.0}, {'paroles_s': 30.0}, 745.0),
            # deux cartes, sans paroles : nettoyage 0,7 et conversion 0,55 de leur temps de GPU
            'deux cartes': (2, False, {'nettoyage_totale_s': 20 + 0.7 * 200, 'nettoyage_gpu_s': 200.0,
                                       'yingmusic_total_s': 15 + 0.55 * 180, 'yingmusic_gpu_s': 180.0}, {}, 600.0),
            # deux cartes, paroles pendant la conversion : P = 20 + 30 s, la moitié s'ajoute à la conversion
            'deux cartes, paroles': (2, True, {'nettoyage_totale_s': 20 + 0.7 * 200, 'nettoyage_gpu_s': 200.0,
                                               'yingmusic_total_s': 15 + 0.55 * 180 + 0.5 * 50, 'yingmusic_gpu_s': 180.0,
                                               'paroles_pendant_conversion_s': 50.0, 'paroles_total_s': 50.0,
                                               'paroles_copie_s': 20.0}, {'paroles_s': 30.0}, 625.0),
        }
        for nom, (cartes, paroles, sk, t, reel) in cas.items():
            with self.subTest(nom):
                shutil.rmtree(self.tmp / 'sorties', ignore_errors=True)
                self.rapport('clip', 100.0, {**base, **sk}, {**temps, **t}, gpus=cartes, reel=reel)
                self.assertAlmostEqual(self.D.estimer_noyau_s([100.0], 30, self.tmp / 'sorties', paroles=paroles,
                                                              cartes=cartes), reel, delta=1)

    def test_conversion_d_apres_les_sessions_ou_les_paroles_tournaient_pendant(self):
        """Part de la conversion à deux cartes : la moitié des paroles retirée ; une session où la carte 1 n'a pas
        rejoint la conversion (paroles plus longues) ne dit rien de cette part."""
        sans = lambda: self.D.estimer_noyau_s([200.0], 30, self.tmp / 'sorties', paroles=False, cartes=2)
        t_conv = 1.5 * 0.045 * 30   # sans conversion mesurée
        self.rapport('trop-longues', 200.0, {'yingmusic_total_s': 150.0, 'chargement_yingmusic_s': 20.0,
                                             'yingmusic_gpu_s': 300.0, 'paroles_pendant_conversion_s': 140.0}, {}, gpus=2)
        reference = self.D.estimer_noyau_s([200.0], 30, self.tmp / 'rien', paroles=False, cartes=2)
        self.assertEqual(sans(), reference, 'session ignorée : part par défaut (0,55)')
        shutil.rmtree(self.tmp / 'sorties')
        self.rapport('pendant', 200.0, {'yingmusic_total_s': 300.0, 'chargement_yingmusic_s': 20.0,
                                        'yingmusic_gpu_s': 400.0, 'paroles_pendant_conversion_s': 100.0}, {}, gpus=2)
        fc = (300 - 20 - 0.5 * 100) / 400
        self.assertAlmostEqual(reference - sans(), 200 * t_conv * (0.55 - fc), delta=1)

    def test_historique_de_plusieurs_dossiers(self):
        """Un dossier de sortie neuf n'a pas d'historique : celui du dossier par défaut compte aussi, sans doublon."""
        self.rapport('clip', 100.0, {'installation_s': 40.0, 'chargement_modeles_s': 30.0, 'nettoyage_totale_s': 220.0,
                                     'yingmusic_total_s': 195.0}, {'separation_s': 50.0, 'conversion_s': 180.0},
                     reel=700.0)
        neuf = self.tmp / 'neuf'
        neuf.mkdir()
        seul = self.D.estimer_noyau_s([100.0], 30, self.tmp / 'sorties')
        self.assertNotEqual(self.D.estimer_noyau_s([100.0], 30, neuf), seul)
        self.assertEqual(self.D.estimer_noyau_s([100.0], 30, [neuf, self.tmp / 'sorties']), seul)
        lien = self.tmp / 'lien'
        lien.symlink_to(self.tmp / 'sorties')
        self.assertEqual(len(self.D.rapports_passes([self.tmp / 'sorties', str(self.tmp / 'sorties'), lien])), 1)

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
        un = self.D.estimer_noyau_s([230.0], 30, self.tmp / 'sorties', paroles=False)
        deux = self.D.estimer_noyau_s([230.0], 30, self.tmp / 'sorties', paroles=False, cartes=2)
        t_net, t_conv = 2.3, 1.5 * 0.045 * 30
        self.assertAlmostEqual(un - deux, 230 * (t_net * (1 - 0.6) + t_conv * (1 - 0.5))
                               + sum(self.D.SURCOUTS_UNE_CARTE_S.values()), delta=1)

    def session(self, nom, duree, cartes=1, pas=30, conversion=None, sk=None, reel=None, **k):
        """Session passée inventée dont les temps par seconde de son sont les mêmes quelle que soit la durée
        (séparation 0,5 s, nettoyage 2 s, conversion 0,06 s par pas) : seule la conversion (conversion, en s par pas
        et par seconde de son) distingue deux sessions."""
        temps = {'separation_s': 0.5 * duree, 'nettoyage_s': 2.0 * duree,
                 'conversion_s': (0.06 if conversion is None else conversion) * pas * duree}
        if cartes == 2 and sk is None:   # temps de GPU des deux cartes additionnés, parts par défaut en temps réel
            f = self.D.FACTEURS_DEUX_CARTES
            sk = {'nettoyage_gpu_s': temps['nettoyage_s'], 'nettoyage_totale_s': f['nettoyage'] * temps['nettoyage_s'],
                  'yingmusic_gpu_s': temps['conversion_s'], 'yingmusic_total_s': f['conversion'] * temps['conversion_s']}
        self.rapport(nom, duree, sk or {}, temps, gpus=cartes, pas=pas, reel=reel, **k)

    def test_une_carte_d_apres_une_session_a_deux_cartes(self):
        """Le défaut du 06/11/2026 (--gpu 1, 18,5 min annoncées pour 22,8) : une session à deux cartes note le temps de
        GPU des deux cartes additionnées, tâches et morceaux seuls ; une carte seule lit, assemble et écrit en plus chaque
        piste (FACTEURS_UNE_CARTE), et le nettoyage et YingMusic-SVC y prennent quelques secondes de plus que leur temps
        de GPU et leur chargement (SURCOUTS_UNE_CARTE_S). Avant : ce temps de GPU comptait tel quel, sans ce surcoût."""
        sk = {'installation_s': 40.0, 'chargement_modeles_s': 30.0, 'chargement_nettoyage_s': 20.0,
              'chargement_yingmusic_s': 15.0, 'nettoyage_totale_s': 20 + 0.7 * 200, 'nettoyage_gpu_s': 200.0,
              'yingmusic_total_s': 15 + 0.55 * 180, 'yingmusic_gpu_s': 180.0}
        self.rapport('clip', 100.0, sk, {'separation_s': 50.0, 'analyse_s': 10.0, 'nettoyage_s': 200.0,
                                         'conversion_s': 180.0}, gpus=2, reel=600.0)
        est = lambda cartes: self.D.estimer_noyau_s([100.0], 30, self.tmp / 'sorties', paroles=False, cartes=cartes)
        self.assertAlmostEqual(est(2), 600.0, delta=1)
        # une carte : tout ce qui n'est pas partagé entre les cartes, puis nettoyage et conversion d'une carte seule
        reste = 600 - 5 - 40 - 30 - 50 - 10 - sk['nettoyage_totale_s'] - sk['yingmusic_total_s']
        avant = 5 + 40 + 30 + 20 + 15 + reste + 50 + 10 + 200 + 180
        f, s = self.D.FACTEURS_UNE_CARTE, self.D.SURCOUTS_UNE_CARTE_S
        attendu = avant + 200 * (f['nettoyage'] - 1) + 180 * (f['conversion'] - 1) + s['nettoyage'] + s['conversion']
        self.assertAlmostEqual(est(1), attendu, delta=1)

    def test_une_carte_avec_un_historique_a_deux_cartes(self):
        """Mêmes temps de GPU notés à une ou à deux cartes : l'estimation à une carte compte plus pour ceux d'une session
        à deux cartes (FACTEURS_UNE_CARTE) ; celle à deux cartes ne change pas."""
        for cartes in (1, 2):
            with self.subTest(historique=cartes):
                shutil.rmtree(self.tmp / 'sorties', ignore_errors=True)
                self.session('a', 230.0, cartes=cartes)
                self.session('b', 250.0, cartes=cartes)
                est = {c: self.D.estimer_noyau_s([200.0], 30, self.tmp / 'sorties', paroles=False, cartes=c) for c in (1, 2)}
                if cartes == 1:
                    une = est
                else:
                    f = self.D.FACTEURS_UNE_CARTE
                    self.assertAlmostEqual(est[1] - une[1], 200 * (2.0 * (f['nettoyage'] - 1)
                                                                   + 0.06 * 30 * (f['conversion'] - 1)), delta=1)
                    self.assertEqual(est[2], une[2])

    def test_surcout_a_une_carte_mesure(self):
        """À une carte, ce que le nettoyage et YingMusic-SVC prennent hors de leur temps de GPU et de leur chargement se
        mesure sur les sessions passées à une carte : le même doublage redonne sa durée réelle, surcoût compris."""
        temps = {'separation_s': 50.0, 'analyse_s': 10.0, 'nettoyage_s': 200.0, 'conversion_s': 180.0}
        sk = {'installation_s': 40.0, 'chargement_modeles_s': 30.0, 'chargement_nettoyage_s': 20.0,
              'chargement_yingmusic_s': 15.0, 'nettoyage_totale_s': 20 + 200 + 4.0, 'yingmusic_total_s': 15 + 180 + 14.0}
        self.rapport('clip', 100.0, sk, temps, gpus=1, reel=718.0)
        self.assertAlmostEqual(self.D.estimer_noyau_s([100.0], 30, self.tmp / 'sorties', paroles=False), 718.0, delta=1)

    def test_conversion_au_meme_nombre_de_pas(self):
        """La conversion n'est pas proportionnelle aux pas (50 pas : environ 1,3 fois 30 pas) : seules comptent les
        sessions passées au même nombre de pas, s'il y en a ; sinon toutes, ramenées au pas."""
        self.session('trente', 230.0, pas=30, conversion=0.067)
        self.session('cinquante', 230.0, pas=50, conversion=0.051)
        tout = {pas: self.D.estimer_noyau_s([200.0], pas, self.tmp / 'sorties', paroles=False) for pas in (30, 50, 40)}
        for pas, garde in ((30, 'trente'), (50, 'cinquante')):
            with self.subTest(pas=pas):
                shutil.rmtree(self.tmp / 'sorties' / ('cinquante' if garde == 'trente' else 'trente'))
                self.assertEqual(self.D.estimer_noyau_s([200.0], pas, self.tmp / 'sorties', paroles=False), tout[pas])
                self.session('cinquante' if garde == 'trente' else 'trente', 230.0, pas=80 - pas,
                             conversion=0.051 if garde == 'trente' else 0.067)
        # 40 pas, aucune session : la médiane de toutes, par pas
        self.assertAlmostEqual(tout[40] - tout[30], 200 * (40 * (0.067 + 0.051) / 2 - 30 * 0.067), delta=1)

    def test_conversion_de_la_meme_chanson(self):
        """Une chanson déjà doublée (même durée de son, à MEME_CHANSON_S près) reprend sa conversion : elle dépend de la
        place des chœurs, que la durée ne dit pas. Pas un extrait court (MEME_CHANSON_MIN_S), qui peut tomber sur la
        durée d'un autre."""
        for duree in (169.39, 40.0):
            for cartes in (1, 2):
                with self.subTest(duree=duree, cartes=cartes):
                    shutil.rmtree(self.tmp / 'sorties', ignore_errors=True)
                    self.session('b', 230.0, cartes=1, conversion=0.067)
                    self.session('c', 250.0, cartes=1, conversion=0.067)
                    # chœurs partout : deux pistes entières à convertir
                    self.session('chargee', duree + 0.5, cartes=1, conversion=0.099)
                    est = lambda: self.D.estimer_noyau_s([duree], 30, self.tmp / 'sorties', paroles=False, cartes=cartes)
                    autre = est()
                    shutil.rmtree(self.tmp / 'sorties' / 'chargee')
                    self.session('chargee', duree + 0.04, cartes=1, conversion=0.099)
                    ecart = est() - autre
                    if duree < self.D.MEME_CHANSON_MIN_S:
                        self.assertEqual(ecart, 0)
                    else:
                        fc = 1.0 if cartes == 1 else self.D.FACTEURS_DEUX_CARTES['conversion']
                        self.assertAlmostEqual(ecart, duree * 30 * (0.099 - 0.067) * fc, delta=1)

    def test_cartes_prevues(self):
        self.assertEqual(self.D.cartes_prevues('auto', 'NvidiaTeslaT4'), 2)
        self.assertEqual(self.D.cartes_prevues('1', 'NvidiaTeslaT4'), 1)
        self.assertEqual(self.D.cartes_prevues('auto', 'NvidiaL4'), 1)
        self.assertEqual(self.D.cartes_prevues('2', 'NvidiaL4'), 2)


if __name__ == '__main__':
    unittest.main()
