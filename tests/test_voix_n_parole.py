"""Parole à plusieurs voix (noyau, sans GPU) : qui parle quand, sur une voix de synthèse (un ton par personne) et une
empreinte imitée (la fréquence dominante dit la personne) : nombre de personnes trouvées, ordre d'apparition, changements
de personne en pleine parole placés au creux d'énergie, décision par morceau, repli vers moins de personnes."""
import json
import unittest

import numpy as np

from commun import SR, charger_noyau

N = charger_noyau()
PAS = N.PAS
DIM = 192
FREQS = (180.0, 260.0, 340.0, 420.0, 500.0, 580.0)
NOMS = ('jules', 'marie', 'luc', 'lea', 'paul', 'anne')


def base(k):
    rng = np.random.default_rng(100 + k)
    v = rng.standard_normal(DIM)
    return v / np.linalg.norm(v)


BASES = [base(k) for k in range(6)]


def empreinte_fn(y):
    """Imitation de CAMPPlus : la personne est la fréquence dominante de la fenêtre ; un peu de bruit déterministe."""
    y = np.asarray(y, np.float64)
    spectre = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    f = np.argmax(spectre) * SR / len(y)
    k = int(np.argmin([abs(f - q) for q in FREQS]))
    rng = np.random.default_rng(len(y) % 97 + int(f))
    e = BASES[k] + 0.25 * rng.standard_normal(DIM) / np.sqrt(DIM) * 3
    return e / np.linalg.norm(e)


def scene(tours, pause_s=0.0):
    """tours : [(personne, durée s, pause avant s)] -> (voix, enveloppe dB par trame, seuil, instants vrais des changements)."""
    morceaux, env, t, vrais, dernier = [], [], 0.0, [], None
    for p, d, pause in tours:
        if pause:
            morceaux.append(np.zeros(int(pause * SR)))
            env += [-80.0] * int(round(pause * 100))
        if dernier is not None and dernier != p:
            vrais.append(t + pause)
        n = int(d * SR)
        tt = np.arange(n) / SR
        y = 0.1 * sum(np.sin(2 * np.pi * FREQS[p] * h * tt) / h for h in range(1, 4))
        y *= 0.5 * (1 - np.cos(2 * np.pi * np.arange(n) / n)) * 0.3 + 0.7   # un creux d'énergie à chaque bord
        morceaux.append(y)
        env += [-20.0] * int(round(d * 100))
        t += pause + d
        dernier = p
    x = np.concatenate(morceaux).astype(np.float32)
    lisse = np.asarray(env[:len(x) // PAS + 1] + [-80.0] * max(0, len(x) // PAS + 1 - len(env)))
    return x, lisse, -50.0, vrais


class Diarisation(unittest.TestCase):
    def test_trois_personnes_dans_l_ordre(self):
        # B parle en premier, puis A, puis C, puis B et A ; changements avec et sans pause
        x, lisse, seuil, vrais = scene([(1, 6, 0), (0, 5, 0.5), (2, 6, 0), (1, 5, 0.4), (0, 6, 0), (2, 5, 0.6), (1, 4, 0)])
        d = N.diariser(x, lisse, seuil, 3, empreinte_fn)
        self.assertEqual(d['k'], 3)
        t = d['trames']
        # première personne entendue = rang 0 : B (1), puis A (0) = rang 1, puis C (2) = rang 2
        self.assertEqual((int(t[300]), int(t[900]), int(t[1500])), (0, 1, 2))
        self.assertEqual((int(t[2000]), int(t[2500]), int(t[3100]), int(t[3600])), (0, 1, 2, 0))
        json.dumps({k: v for k, v in d.items() if k not in ('trames',)})

    def test_changement_en_pleine_parole_coupe_au_creux(self):
        x, lisse, seuil, vrais = scene([(0, 6, 0), (1, 6, 0), (0, 6, 0)])
        d = N.diariser(x, lisse, seuil, 2, empreinte_fn)
        self.assertEqual(d['k'], 2)
        self.assertEqual(len(d['coupes']), len(vrais))
        for c, v in zip(d['coupes'], vrais):   # sans pause : changements en pleine parole
            self.assertLess(abs(c / SR - v), 0.8, 'le changement tombe près du vrai')

    def test_moins_de_personnes_que_de_voix(self):
        x, lisse, seuil, _ = scene([(0, 6, 0), (1, 6, 0.5), (0, 6, 0.5), (1, 6, 0.5)])
        d = N.diariser(x, lisse, seuil, 4, empreinte_fn)
        self.assertEqual(d['k'], 2)
        self.assertTrue(any('pourquoi' in e for e in d['essais']))

    def test_une_seule_personne(self):
        x, lisse, seuil, _ = scene([(2, 8, 0), (2, 8, 0.5), (2, 6, 0.5)])
        d = N.diariser(x, lisse, seuil, 3, empreinte_fn)
        self.assertEqual(d['k'], 1)
        self.assertEqual(d['coupes'], [])
        self.assertEqual({int(v) for v in d['trames'][lisse > seuil]}, {0})

    def test_six_personnes(self):
        tours = [(k, 5, 0.5) for k in range(6)] + [(k, 4, 0.5) for k in (3, 1, 5, 0, 2, 4)]
        x, lisse, seuil, _ = scene(tours)
        d = N.diariser(x, lisse, seuil, 6, empreinte_fn)
        self.assertEqual(d['k'], 6)
        self.assertEqual([int(d['trames'][int((0.25 + 5.5 * k + 2.5) * 100)]) for k in range(6)], list(range(6)))

    def test_fusion_des_coupes(self):
        self.assertEqual(N.fusionner_coupes([0, 100000, 400000], [100200, 250000, 399000], 400000), [0, 100000, 250000, 400000])


class DecisionParMorceau(unittest.TestCase):
    def setUp(self):
        self.x, self.lisse, self.seuil, self.vrais = scene([(1, 6, 0), (0, 6, 0), (2, 6, 0.5), (1, 6, 0)])
        self.d = N.diariser(self.x, self.lisse, self.seuil, 3, empreinte_fn)
        coupes = N.fusionner_coupes([0, len(self.x)], self.d['coupes'], len(self.x))
        self.segs = [{'i': i, 'debut': a, 'fin': b, 'debut_s': round(a / SR, 3), 'fin_s': round(b / SR, 3), 'muet': False,
                      'f0_med': 150.0, 'voise_s': (b - a) / SR} for i, (a, b) in enumerate(zip(coupes[:-1], coupes[1:]))]
        self.cibles = {'mode': 'personnes', 'voix': [{'cle': f'personne{k + 1}', 'nom': NOMS[k], 'f0_parole': 120.0 + 40 * k,
                                                       'centre_hz': 200.0} for k in range(3)]}

    def test_une_personne_par_morceau_et_voix_dans_l_ordre(self):
        info = N.decider_personnes(self.segs, self.d, self.cibles)
        self.assertEqual(info['voix'], 3)
        self.assertEqual(info['axe'], 'diarisation')
        self.assertEqual(info['mode'], 'personnes')
        vrai_par_morceau = []
        for s in self.segs:
            milieu = (s['debut_s'] + s['fin_s']) / 2
            vrai_par_morceau.append(1 if milieu < 6 else 0 if milieu < 12 else 2 if milieu < 18.5 else 1)
        ordre = {1: 'jules', 0: 'marie', 2: 'luc'}   # B entendu en premier : première voix
        self.assertEqual([s['voix_cible'] if 'voix_cible' in s else self.cibles['voix'][s['cible']]['nom'] for s in self.segs],
                         [ordre[v] for v in vrai_par_morceau])
        self.assertEqual([s['voix'] for s in self.segs], [[1, 2, 3][[1, 0, 2].index(v)] for v in vrai_par_morceau])
        self.assertIn('3 personnes repérées', info['message'])
        json.dumps(info)

    def test_morceau_muet(self):
        self.segs[1]['muet'] = True
        N.decider_personnes(self.segs, self.d, self.cibles)
        self.assertEqual(self.segs[1]['base'], 'muet')
        self.assertNotIn('cible', self.segs[1])

    def test_une_seule_personne_repérée(self):
        x, lisse, seuil, _ = scene([(0, 8, 0), (0, 8, 0.5)])
        d = N.diariser(x, lisse, seuil, 3, empreinte_fn)
        segs = [{'i': 0, 'debut': 0, 'fin': len(x), 'debut_s': 0.0, 'fin_s': len(x) / SR, 'muet': False, 'f0_med': 150.0,
                 'voise_s': 10.0}]
        info = N.decider_personnes(segs, d, self.cibles)
        self.assertEqual(info['voix'], 1)
        self.assertEqual(segs[0]['cible'], 0)
        self.assertIn('une seule personne', info['message'])

    def test_lecture_du_mode_personnes(self):
        c = N.lire_cibles({'voix_cibles': {'voix': [{'cle': f'personne{k + 1}', 'nom': NOMS[k], 'fichier': f'{NOMS[k]}.wav'}
                                                    for k in range(3)]}})
        self.assertEqual(c['mode'], 'personnes')


if __name__ == '__main__':
    unittest.main()
