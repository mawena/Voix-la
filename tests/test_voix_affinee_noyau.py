"""Voix affinée dans le noyau de doublage (kaggle/doubler_kaggle.py), sans GPU ni modèle ni réseau : la chaîne entière du
noyau avec les faux moteurs de test_plusieurs_voix_chaine.py.

Le faux Seed-VC reconnaît le chargement d'un point de contrôle affiné (Namespace.checkpoint) : le modèle chargé ainsi
ajoute une note de 6000 Hz à la voix convertie, ce qui dit, passage par passage, quel modèle a chanté. Ce que ces essais
vérifient : le point de contrôle de la voix est chargé (avec sa config, sinon celle de Seed-VC) à la place des poids de
base, pour cette voix seulement ; un point absent, abîmé (somme SHA-256) ou refusé au chargement laisse les poids de base,
dit pourquoi dans le rapport et n'échoue pas ; sans affinage, rien ne change."""
import hashlib
import json
import unittest
from unittest import mock

import numpy as np

import test_plusieurs_voix_chaine as chaine
from commun import SR, Dossier
from test_plusieurs_voix_chaine import FauxSeedVC, T, Timbres, lancer_noyau, lire_son, reglages

OCTETS = b'point de controle de pacotille'
SOMME = hashlib.sha256(OCTETS).hexdigest()
PRESET = 'configs/presets/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml'


class FauxSeedVCAffinable(FauxSeedVC):
    """Garde la trace de chaque chargement ; un modèle chargé depuis un point de contrôle ajoute 6000 Hz à son son."""
    echec_affine = False

    def __init__(self, timbres):
        super().__init__(timbres)
        self.appels = []

    def load_models(self, ns):
        self.appels.append({'f0': ns.f0_condition, 'checkpoint': ns.checkpoint, 'config': ns.config})
        if ns.checkpoint and self.echec_affine:
            raise RuntimeError('clés du point de contrôle incompatibles')
        modele, sem, f0, vocodeur, camp, mel, args = super().load_models(ns)
        if ns.checkpoint:
            base = vocodeur

            def vocodeur(m):
                y = base(m).a
                t = np.arange(y.shape[-1]) / args['sampling_rate']
                return T(y + (0.05 * np.sin(2 * np.pi * 6000 * t) * (np.abs(y) > 0)).astype(np.float32))
        return modele, sem, f0, vocodeur, camp, mel, args


def part_6000(y, a, b):
    """Part (dB) de l'énergie de y entre a et b secondes qui tombe autour de 6000 Hz."""
    seg = np.asarray(y[int(a * SR):int(b * SR)], np.float64)
    s = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    return 10 * np.log10(s[(f > 5900) & (f < 6100)].sum() / (s.sum() + 1e-20) + 1e-20)


def reglage_affinage(nom='jules', **autres):
    return {'fichier': f'affinage-{nom}.pth', 'config': f'affinage-{nom}.yml', 'sha256': SOMME, 'pas': 1500,
            'complet': True, **autres}


def lancer(R, fichiers=('jules',), faux=None, **options):
    """Noyau lancé avec les fichiers d'affinage des voix données dans le jeu de données ; rend (voix convertie, rapport,
    faux Seed-VC)."""
    faux = faux or FauxSeedVCAffinable
    vus = []

    class Faux(faux):
        def __init__(self, timbres):
            super().__init__(timbres)
            vus.append(self)
    with Dossier() as d:
        jeu = d / 'kaggle' / 'input' / 'jeu'
        jeu.mkdir(parents=True)
        for nom in fichiers:
            (jeu / f'affinage-{nom}.pth').write_bytes(OCTETS)
            (jeu / f'affinage-{nom}.yml').write_text('model_params: {}\n')
        with mock.patch.object(chaine, 'FauxSeedVC', Faux):
            sortie = lancer_noyau(d, R, **options)
        return (lire_son(sortie / 'voix_convertie.flac')[0], json.loads((sortie / 'rapport.json').read_text()),
                vus[0])


def avec_affinage(R, **modif):
    R['voix']['affinage'] = reglage_affinage(**modif)
    return R


class UneVoixAffinee(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.y, cls.rapport, cls.faux = lancer(avec_affinage(reglages(Timbres())))

    def test_point_charge_pour_la_voix(self):
        charges = [a for a in self.faux.appels if a['checkpoint']]
        self.assertEqual(len(charges), 1)
        self.assertTrue(charges[0]['checkpoint'].endswith('affinage-jules.pth'))
        self.assertTrue(charges[0]['config'].endswith('affinage-jules.yml'))
        self.assertTrue(charges[0]['f0'])   # le modèle 44 kHz du chant

    def test_base_toujours_charge(self):
        # RMVPE et CAMPPlus servent à l'analyse : le modèle de base reste chargé
        self.assertTrue(any(a['checkpoint'] is None and a['f0'] for a in self.faux.appels))

    def test_voix_convertie_par_le_modele_affine(self):
        self.assertGreater(part_6000(self.y, 1, 9), -25)
        self.assertGreater(part_6000(self.y, 28, 53), -25)

    def test_rapport_dit_quel_modele(self):
        i = self.rapport['affinage']['jules']
        self.assertTrue(i['utilise'])
        self.assertIsNone(i['raison'])
        self.assertEqual(i['pas'], 1500)
        self.assertEqual(self.rapport['variantes']['voix_convertie.flac']['voix_affinees'], ['jules'])
        self.assertEqual(self.rapport['moteur'], 'seedvc')


class CommeAvant(unittest.TestCase):
    def test_sans_affinage_rien_ne_change(self):
        y, rapport, faux = lancer(reglages(Timbres()))
        self.assertTrue(all(a['checkpoint'] is None for a in faux.appels))
        self.assertNotIn('affinage', rapport)
        self.assertNotIn('voix_affinees', rapport['variantes']['voix_convertie.flac'])
        self.assertLess(part_6000(y, 1, 9), -50)

    def test_yingmusic_sans_affinage(self):
        _, rapport, faux = lancer(reglages(Timbres(), 'yingmusic'))
        self.assertTrue(all(a['checkpoint'] is None for a in faux.appels))
        self.assertEqual(rapport['moteur'], 'yingmusic')
        self.assertNotIn('affinage', rapport)


class RepliSurLesPoidsDeBase(unittest.TestCase):
    def verifier(self, y, rapport, motif):
        i = rapport['affinage']['jules']
        self.assertFalse(i['utilise'])
        self.assertIn(motif, i['raison'])
        self.assertLess(part_6000(y, 1, 9), -50)   # poids de base : pas de note de 6000 Hz
        self.assertEqual(rapport['moteur'], 'seedvc')   # le doublage va à son terme

    def test_point_absent(self):
        y, rapport, faux = lancer(avec_affinage(reglages(Timbres())), fichiers=())
        self.assertTrue(all(a['checkpoint'] is None for a in faux.appels))
        self.verifier(y, rapport, 'absent')

    def test_somme_differente(self):
        y, rapport, faux = lancer(avec_affinage(reglages(Timbres()), sha256='0' * 64))
        self.assertTrue(all(a['checkpoint'] is None for a in faux.appels))   # abîmé : jamais chargé
        self.verifier(y, rapport, 'SHA-256')

    def test_chargement_refuse(self):
        class Refuse(FauxSeedVCAffinable):
            echec_affine = True
        y, rapport, faux = lancer(avec_affinage(reglages(Timbres())), faux=Refuse)
        self.assertEqual(len([a for a in faux.appels if a['checkpoint']]), 1)   # essayé une fois, pas plus
        self.verifier(y, rapport, 'incompatibles')

    def test_sans_config_celle_de_seed_vc(self):
        R = avec_affinage(reglages(Timbres()), config=None)
        _, _, faux = lancer(R)
        self.assertEqual([a['config'] for a in faux.appels if a['checkpoint']], [PRESET])


class DeuxVoixUneSeuleAffinee(unittest.TestCase):
    """homme:jules (affinée), femme:marie (poids de base) : seule la voix qui a un affinage le prend."""

    def test_chaque_voix_son_modele(self):
        R = reglages(Timbres(), 'seedvc', [('homme', 'jules'), ('femme', 'marie')])
        R['voix_cibles']['voix'][0]['affinage'] = reglage_affinage()
        R['voix']['affinage'] = reglage_affinage()
        y, rapport, faux = lancer(R)
        self.assertEqual(len([a for a in faux.appels if a['checkpoint']]), 1)
        self.assertGreater(part_6000(y, 1, 9), -25)       # le chanteur, en jules affiné
        self.assertLess(part_6000(y, 28, 53), -50)        # la chanteuse, en marie de base
        self.assertEqual(rapport['variantes']['voix_convertie.flac']['voix_affinees'], ['jules'])
        self.assertEqual(list(rapport['affinage']), ['jules'])
        self.assertTrue(rapport['affinage']['jules']['utilise'])


if __name__ == '__main__':
    unittest.main()
