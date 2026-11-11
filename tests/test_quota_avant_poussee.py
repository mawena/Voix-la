"""Quota Kaggle lu par doubler avant de pousser un noyau : une ligne « X de GPU restantes, remise <jour> », un avertissement
si le quota est épuisé ou trop bas, la bascule sur Modal en calcul auto (KaggleIndisponible), et un doublage qui continue
si la lecture échoue. « kaggle » est la fausse commande interface/tests/faux_kaggle.py : aucun réseau, aucun jeton, aucune
session. HOME et l'état de l'interface (cache de la lecture) sont des dossiers temporaires."""
import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from commun import DEPOT, Dossier, charger_doubler

FAUX_KAGGLE = DEPOT / 'interface' / 'tests' / 'faux_kaggle.py'


class QuotaAvantPoussee(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.D = charger_doubler(self.home)
        self.D.KAGGLE = FAUX_KAGGLE
        env = mock.patch.dict(os.environ, {'VOIXLA_ETAT': str(self.tmp / 'etat'), 'HOME': str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        for v in ('FAUX_KAGGLE_PANNE', 'FAUX_KAGGLE_ANCIEN', 'FAUX_KAGGLE_QUOTA', 'FAUX_KAGGLE_TRACE'):
            os.environ.pop(v, None)
        self.trace = self.tmp / 'trace.jsonl'
        os.environ['FAUX_KAGGLE_TRACE'] = str(self.trace)

    def verifier(self, quota, estimation=1700, **k):
        os.environ['FAUX_KAGGLE_QUOTA'] = quota
        with contextlib.redirect_stdout(io.StringIO()) as sortie:
            try:
                return self.D.quota_kaggle_avant_poussee(estimation, **k)
            finally:
                self.sortie = sortie.getvalue()

    def appels(self):
        return [json.loads(l) for l in self.trace.read_text().splitlines()] if self.trace.exists() else []

    def test_il_reste_assez(self):
        lecture = self.verifier('10,30')
        self.assertEqual(lecture['total_s'], 30 * 3600)
        self.assertIn('Kaggle : 20 h de GPU restantes cette semaine, remise ', self.sortie)
        self.assertNotIn('attention', self.sortie)
        self.assertEqual(len(self.sortie.strip().splitlines()), 1, 'une seule ligne')
        self.assertEqual(self.appels(), [['quota', '--format', 'json']], 'lecture seule : « quota » et rien d\'autre')

    def test_quota_epuise(self):
        self.verifier('30,30')   # défaut : un avertissement, la session part
        self.assertIn('0 min de GPU restantes', self.sortie)
        self.assertIn('attention : quota épuisé', self.sortie)

    def test_quota_trop_bas_pour_l_estimation(self):
        self.verifier('29.8,30', estimation=1700)   # 12 min restantes pour 28 min estimées
        self.assertIn('12 min de GPU restantes', self.sortie)
        self.assertIn('attention : la session estimée à 28 min', self.sortie)
        self.assertIn('ne tient pas', self.sortie)

    def test_la_marge_compte(self):
        self.verifier('29.6,30', estimation=1300)   # 24 min restantes ; 21,7 min estimées, 26 min avec la marge de 20 %
        self.assertIn('attention', self.sortie)
        (self.tmp / 'etat' / 'quota-kaggle.json').unlink()   # sans la lecture gardée
        self.verifier('29.5,30', estimation=1300)   # 30 min restantes : tient avec la marge
        self.assertNotIn('attention', self.sortie)

    def test_calcul_auto_passe_a_modal(self):
        for quota in ('30,30', '29.8,30'):
            os.environ.pop('FAUX_KAGGLE_QUOTA', None)
            with self.assertRaises(self.D.KaggleIndisponible) as e:
                self.verifier(quota, auto=True)
            self.assertIn('Kaggle : ', str(e.exception))
            self.assertIn(' Kaggle : ', self.sortie, 'la ligne du quota est dite avant la bascule')
            (self.tmp / 'etat' / 'quota-kaggle.json').unlink(missing_ok=True)   # pas de lecture gardée d'un cas à l'autre

    def test_calcul_auto_assez_de_quota_reste_sur_kaggle(self):
        self.assertIsNotNone(self.verifier('5,30', auto=True))

    def test_sans_quota_garde_kaggle_en_auto(self):
        self.verifier('30,30', auto=True, sans_quota=True)
        self.assertIn('attention : quota épuisé', self.sortie)

    def test_lecture_en_panne_le_doublage_continue(self):
        os.environ['FAUX_KAGGLE_PANNE'] = '1'
        self.assertIsNone(self.verifier('10,30', auto=True))
        self.assertIn('quota non lu', self.sortie)
        self.assertIn('continue', self.sortie)

    def test_commande_kaggle_absente(self):
        self.D.KAGGLE = self.tmp / 'absente'
        self.assertIsNone(self.verifier('10,30', auto=True))
        self.assertIn('quota non lu (commande kaggle absente', self.sortie)

    def test_cli_trop_ancienne(self):
        os.environ['FAUX_KAGGLE_ANCIEN'] = '1'
        self.assertIsNone(self.verifier('10,30'))
        self.assertIn('trop ancienne', self.sortie)

    def test_module_du_quota_introuvable(self):
        with mock.patch.object(self.D, 'charger_quota', lambda: None):
            self.assertIsNone(self.verifier('10,30', auto=True))
        self.assertIn('quota non lu (module du quota introuvable)', self.sortie)

    def test_lecture_gardee_pas_de_second_appel(self):
        self.verifier('10,30')
        self.verifier('10,30')
        self.assertEqual(len(self.appels()), 1, 'le cache de l\'interface sert')

    def test_remise_passee_dans_la_lecture_gardee(self):
        self.verifier('30,30,2020-01-04T00:00:00+00:00')   # remise dans le passé : la semaine a repris
        self.assertIn('remise à zéro du quota est passée', self.sortie)
        self.assertNotIn('attention', self.sortie)

    def test_la_commande_de_doubler_est_utilisee(self):
        # doubler.KAGGLE (VOIXLA_KAGGLE) est celle qui lit le quota, pas une autre
        self.D.KAGGLE = self.tmp / 'autre'
        autre = self.tmp / 'autre'
        autre.write_text('#!/bin/sh\necho \'[{"resource":"GPU","used":"1.00h","total":"30.00h","refreshAt":'
                         '"2099-01-02T00:00:00Z"}]\'\n')
        autre.chmod(0o755)
        self.verifier('30,30')
        self.assertIn('29 h de GPU restantes', self.sortie)


if __name__ == '__main__':
    unittest.main()
