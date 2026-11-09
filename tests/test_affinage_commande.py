"""Commande affiner (affiner.py) : contrôle des prises, jeu de données et noyau écrits par --essai sans rien envoyer, refus
d'un chant insuffisant, session complète avec une fausse commande kaggle (envoi, suivi, rapatriement, rangement) et reprise.
Tout se passe dans un dossier temporaire (HOME y est déplacé) ; aucun réseau, aucun GPU, aucune vraie voix."""
import base64
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from commun import DEPOT, FFMPEG, SR, Dossier, charger_doubler, ecrire_wav, note
from test_affinage_noyau import prise

SCRIPT = DEPOT / 'affiner.py'


def charger_affiner(home):
    """affiner.py comme module, branché sur un doubler chargé avec HOME=home."""
    doub = charger_doubler(home)
    with mock.patch.dict(sys.modules, {'doubler': doub}):
        spec = importlib.util.spec_from_file_location('affiner_essai', SCRIPT)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    return m, doub


def monter(d, nb=3, duree=70.0, compte='compteA'):
    """HOME, données et voixA avec nb prises de chant ; rend (home, données)."""
    home = d / 'home'
    (home / '.config' / 'voix-la').mkdir(parents=True)
    if compte:
        (home / '.config' / 'voix-la' / 'config.json').write_text(json.dumps({'kaggle': {'compte': compte}}))
    donnees = d / 'donnees'
    voix = donnees / 'voix' / 'voixA'
    (voix / 'chant').mkdir(parents=True)
    (voix / 'voix.json').write_text(json.dumps({'nom': 'VoixA', 'hauteur_parole_mediane_hz': 110}))
    for i in range(nb):
        prise(voix / 'chant' / f'chant-{i + 1:02d}.wav', duree)
    return home, donnees


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Essai(unittest.TestCase):
    def test_essai_ecrit_tout_sans_envoyer(self):
        with Dossier() as d:
            home, donnees = monter(d)
            aff, doub = charger_affiner(home)
            self.assertEqual(aff.main(['voixA', '--essai', '--donnees', str(donnees)]), 0)
            essai = doub.ETAT / 'affinage' / 'voixA' / 'essai'
            flacs = sorted((essai / 'jeu' / 'chant').glob('prise-*.flac'))
            self.assertEqual(len(flacs), 3)
            meta = json.loads((essai / 'jeu' / 'dataset-metadata.json').read_text())
            self.assertEqual(meta['id'], 'compteA/affinage-voix-entrees')
            noyau = essai / 'envoi-affinage-voix-gpu'
            texte = (noyau / 'doubler_kaggle.py').read_text()
            self.assertNotIn("'__REGLAGES__'", texte)
            self.assertNotIn("SOURCE = '__SOURCE__'", texte)
            self.assertIn('def entrainer', texte)   # c'est bien le noyau d'affinage
            km = json.loads((noyau / 'kernel-metadata.json').read_text())
            self.assertEqual(km['id'], 'compteA/affinage-voix-gpu')
            self.assertTrue(km['is_private'] and km['enable_gpu'])
            self.assertEqual(km['dataset_sources'], ['compteA/affinage-voix-entrees'])
            self.assertEqual(km['kernel_sources'], ['compteA/doublage-outils'])
            plan = json.loads((essai / 'plan.json').read_text())
            self.assertEqual(plan['reglages']['voix'], 'voixA')
            self.assertEqual(plan['reglages']['max_steps'], 1500)
            self.assertGreater(plan['bilan']['utile_s'], 120)
            # rien n'est parti : seule la poussée du noyau a été « appelée », et remplacée ; ni jeu, ni session
            self.assertEqual({c[0] for c in plan['appels_kaggle_remplaces']}, {'kernels'})
            self.assertFalse((doub.ETAT / 'affinage-en-cours.json').exists())
            # les réglages envoyés sont lisibles par le noyau
            reglages = json.loads(base64.b64decode(texte.split("json.loads(base64.b64decode('", 1)[1].split("'", 1)[0]))
            self.assertEqual(reglages, plan['reglages'])

    def test_essai_sans_compte_kaggle(self):
        with Dossier() as d:
            home, donnees = monter(d, compte=None)
            aff, _ = charger_affiner(home)
            self.assertEqual(aff.main(['voixA', '--simulation', '--donnees', str(donnees)]), 0)
            with self.assertRaises(SystemExit):   # un vrai envoi exige le compte
                aff.main(['voixA', '--donnees', str(donnees)])

    def test_prises_donnees_et_voix_par_defaut(self):
        with Dossier() as d:
            home, donnees = monter(d, nb=0)
            fichiers = []
            for i in range(3):
                prise(d / f'a{i}.wav', 70.0)
                fichiers.append(str(d / f'a{i}.wav'))
            aff, doub = charger_affiner(home)
            self.assertEqual(aff.main(['--essai', '--donnees', str(donnees), '--prises', *fichiers]), 0)   # seule voix : défaut
            self.assertEqual(len(list((doub.ETAT / 'affinage' / 'voixA' / 'essai' / 'jeu' / 'chant').glob('*.flac'))), 3)

    def test_chant_insuffisant_refuse(self):
        with Dossier() as d:
            home, donnees = monter(d, nb=1, duree=30.0)
            aff, doub = charger_affiner(home)
            with self.assertRaises(SystemExit) as e:
                aff.main(['voixA', '--essai', '--donnees', str(donnees)])
            self.assertIn('chant exploitable', str(e.exception))
            self.assertFalse((doub.ETAT / 'affinage' / 'voixA' / 'essai' / 'jeu').exists())

    def test_voix_ou_prises_absentes(self):
        with Dossier() as d:
            home, donnees = monter(d, nb=0)
            aff, _ = charger_affiner(home)
            with self.assertRaises(SystemExit) as e:
                aff.main(['voixA', '--essai', '--donnees', str(donnees)])
            self.assertIn('aucune prise de chant', str(e.exception))
            with self.assertRaises(SystemExit) as e:
                aff.main(['inconnue', '--essai', '--donnees', str(donnees)])
            self.assertIn('voix inconnue', str(e.exception))

    def test_options_refusees(self):
        with Dossier() as d:
            aff, _ = charger_affiner(d)
            for args in (['--pas', '5'], ['--budget-h', '20']):
                with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
                    aff.lire_arguments(args)


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Controle(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.d = self._d.__enter__()
        self.aff, _ = charger_affiner(self.d)

    def tearDown(self):
        self._d.__exit__()

    def test_prise_correcte(self):
        prise(self.d / 'ok.wav', 40.0)
        c = self.aff.controler_prise(self.d / 'ok.wav')
        self.assertTrue(c['retenue'], c['alertes'])
        self.assertGreater(c['utile_s'], 15)
        self.assertLess(c['crete_dbfs'], -3)

    def test_saturee_bruitee_faible_illisible_courte(self):
        prise(self.d / 'sat.wav', 30.0, sature=True)
        prise(self.d / 'bruit.wav', 30.0, bruit=-34)
        prise(self.d / 'faible.wav', 30.0, niveau=-62, bruit=-95)
        (self.d / 'casse.wav').write_bytes(b'non')
        prise(self.d / 'court.wav', 3.0)
        mots = {'sat': 'saturée', 'bruit': 'bruit de fond', 'faible': 'niveau trop faible', 'casse': 'illisible', 'court': 'trop courte'}
        for nom, mot in mots.items():
            c = self.aff.controler_prise(self.d / f'{nom}.wav')
            self.assertFalse(c['retenue'], nom)
            self.assertTrue(any(mot in a for a in c['alertes']), (nom, c['alertes']))

    def test_bilan(self):
        faux = lambda u, ok=True: {'retenue': ok, 'utile_s': u}
        self.assertIn('aucune prise', self.aff.bilan([])['bloquant'])
        self.assertIn('au moins 120', self.aff.bilan([faux(60), faux(500, False)])['bloquant'])
        b = self.aff.bilan([faux(100), faux(100)])
        self.assertIsNone(b['bloquant'])
        self.assertTrue(b['alertes'])        # moins de 5 min : conseil
        b = self.aff.bilan([faux(400), faux(100, False)])
        self.assertEqual((b['alertes'], b['retenues'], b['ecartees']), ([], 1, 1))
        self.assertTrue(self.aff.bilan([faux(1000)])['alertes'])   # trop long : conseil

    def test_estimation(self):
        bas, haut = self.aff.estimer_s(1500)
        self.assertLess(bas, haut)
        self.assertLess(haut, 2 * 3600)   # tient dans le budget par défaut de 2 h
        self.assertLess(haut, 0.1 * 30 * 3600)   # et reste une petite part des 30 h hebdomadaires


class FausseCommandeKaggle:
    """Remplace doubler.kaggle : un jeu de données qui se crée, un noyau qui tourne puis finit et rend sa sortie."""

    def __init__(self, sortie_noyau):
        self.appels, self.sortie_noyau, self.etats = [], sortie_noyau, 0

    def __call__(self, *args):
        self.appels.append(args)
        if args[:2] == ('datasets', 'status'):
            return (0, 'ready') if any(a[:2] == ('datasets', 'create') for a in self.appels) else (1, 'not found')
        if args[:2] == ('datasets', 'create'):
            return 0, 'Your private Dataset is being created.'
        if args[:2] == ('datasets', 'files'):
            return 0, 'chant/prise-01.flac\nchant/prise-02.flac\nchant/prise-03.flac\nprise-01.flac prise-02.flac prise-03.flac'
        if args[:2] == ('kernels', 'push'):
            return 0, 'Kernel version 3 successfully pushed.'
        if args[:2] == ('kernels', 'status'):
            self.etats += 1
            return 0, 'compteA/affinage-voix-gpu has status "' + ('RUNNING' if self.etats == 1 else 'COMPLETE') + '"'
        if args[:2] == ('kernels', 'output'):
            retour = Path(args[args.index('-p') + 1])
            retour.mkdir(parents=True, exist_ok=True)
            self.sortie_noyau(retour)
            return 0, ''
        return 1, 'commande inattendue'


def sortie_complete(retour, complet=True):
    (retour / 'affinage').mkdir()
    (retour / 'affinage' / 'voixA.pth').write_bytes(b'poids' * 1000)
    (retour / 'affinage' / 'voixA.yml').write_text('sr: 44100\n')
    rapport = {'complet': complet, 'commit_seedvc': 'abc', 'preset': 'p.yml', 'point': {'pas': 1500 if complet else 750},
               'donnees': {'utile_s': 410.0, 'morceaux': 55},
               'entrainement': {'pertes': {'moyenne_fin': {'loss_mel': 0.31}}}, 'temps': {'total_s': 2400.0},
               'avertissements': []}
    (retour / 'rapport.json').write_text(json.dumps(rapport))
    if not complet:
        (retour / 'reprise').mkdir()
        (retour / 'reprise' / 'DiT_epoch_00003_step_00750.pth').write_bytes(b'r' * 500)


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Session(unittest.TestCase):
    def lancer(self, d, noyau_sortie, args=(), nb=3, duree=70.0):
        home, donnees = monter(d, nb=nb, duree=duree)
        aff, doub = charger_affiner(home)
        faux = FausseCommandeKaggle(noyau_sortie)
        doub.kaggle = faux
        with mock.patch('time.sleep'):
            code = aff.main(['voixA', '--donnees', str(donnees), *args])
        return aff, doub, faux, donnees, code

    def test_session_complete_et_rangement(self):
        with Dossier() as d:
            aff, doub, faux, donnees, code = self.lancer(d, sortie_complete)
            self.assertEqual(code, 0)
            ordre = [a[:2] for a in faux.appels if a[0] in ('datasets', 'kernels') and a[1] in ('create', 'push', 'output')]
            self.assertEqual(ordre, [('datasets', 'create'), ('kernels', 'push'), ('kernels', 'output')])
            rang = donnees / 'voix' / 'voixA' / 'affinage'
            self.assertTrue((rang / 'affinage.pth').is_file())
            self.assertTrue((rang / 'affinage.yml').is_file())
            fiche = json.loads((rang / 'affinage.json').read_text())
            self.assertTrue(fiche['complet'])
            self.assertEqual((fiche['pas'], fiche['moteur'], fiche['utile_s']), (1500, 'seedvc-44k', 410.0))
            self.assertEqual(fiche['sha256'], aff.sha256(rang / 'affinage.pth'))
            self.assertEqual(aff.fiche_affinage('voixA')['pas'], 1500)
            self.assertFalse((rang / 'reprise').exists())
            self.assertFalse((doub.ETAT / 'affinage-en-cours.json').exists())    # session close
            self.assertEqual(json.loads((doub.ETAT / 'affinage-precedent.json').read_text())['etat'], 'terminée')
            self.assertFalse((doub.ETAT / 'session-en-cours.json').exists())     # jamais celle de doubler
            # le jeu envoyé ne contient que le chant (rien d'autre de la voix)
            self.assertFalse(any('reference' in str(a) for a in faux.appels))

    def test_session_inachevee_puis_reprise_depuis_le_point(self):
        with Dossier() as d:
            aff, doub, faux, donnees, code = self.lancer(d, lambda r: sortie_complete(r, complet=False))
            self.assertEqual(code, 0)
            rang = donnees / 'voix' / 'voixA' / 'affinage'
            self.assertFalse(json.loads((rang / 'affinage.json').read_text())['complet'])
            point = aff.reprise_locale('voixA')
            self.assertEqual(point.name, 'DiT_epoch_00003_step_00750.pth')
            # --reprendre sans session notée : le point repart avec le jeu de données
            faux2 = FausseCommandeKaggle(sortie_complete)
            doub.kaggle = faux2
            with mock.patch('time.sleep'):
                self.assertEqual(aff.main(['voixA', '--donnees', str(donnees), '--reprendre']), 0)
            self.assertTrue(json.loads((rang / 'affinage.json').read_text())['complet'])
            self.assertFalse((rang / 'reprise').exists())
            self.assertTrue(any(a[:2] == ('datasets', 'version') or a[:2] == ('datasets', 'create') for a in faux2.appels))

    def test_reprendre_une_session_notee(self):
        with Dossier() as d:
            home, donnees = monter(d)
            aff, doub = charger_affiner(home)
            kdir = doub.ETAT / 'affinage' / 'voixA' / '.kaggle'
            kdir.mkdir(parents=True)
            aff.noter_session({'voix': 'voixA', 'noyau': 'compteA/affinage-voix-gpu', 'dossier': str(kdir),
                               'pousse_epoch': 1.0, 'estimation_s': 100})
            faux = FausseCommandeKaggle(sortie_complete)
            faux.etats = 1   # le noyau a fini
            doub.kaggle = faux
            with mock.patch('time.sleep'):
                self.assertEqual(aff.main(['--reprendre', '--donnees', str(donnees)]), 0)
            self.assertTrue(aff.fiche_affinage('voixA')['complet'])
            self.assertFalse(any(a[0] == 'datasets' for a in faux.appels))   # rien renvoyé, juste rapatrié

    def test_noyau_sans_point_est_une_erreur(self):
        with Dossier() as d:
            def vide(retour):
                (retour / 'rapport.json').write_text(json.dumps({'erreur': 'train.py muet'}))
            aff, doub, faux, donnees, code = self.lancer(d, vide)
            self.assertEqual(code, 1)
            self.assertIsNone(aff.fiche_affinage('voixA'))

    def test_rien_a_reprendre(self):
        with Dossier() as d:
            home, donnees = monter(d)
            aff, _ = charger_affiner(home)
            with self.assertRaises(SystemExit):
                aff.main(['voixA', '--reprendre', '--donnees', str(donnees)])


if __name__ == '__main__':
    unittest.main()
