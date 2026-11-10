"""De trois à six voix cibles (noyau, sans GPU) : chanteurs repérés par leurs empreintes de synthèse, ordre d'apparition,
décalage de chaque couple, repli vers le duo ou la voix seule, reconnaissance des pistes à N chanteurs, transposition,
voix par trame, versions des autres voix. Voix inventées (jules, marie, luc, lea, paul, anne)."""
import json
import unittest

import numpy as np

from commun import SR, charger_noyau

N = charger_noyau()
DIM = 192
NOMS = ('jules', 'marie', 'luc', 'lea', 'paul', 'anne')
CENTRES = (200.0, 400.0, 220.0, 380.0, 260.0, 330.0)


def unite(v):
    return v / np.linalg.norm(v)


class Monde:
    """Empreintes de synthèse : une direction commune et une part propre à chaque chanteur."""

    def __init__(self, nb, graine=11):
        self.rng = np.random.default_rng(graine)
        self.commun = unite(self.rng.standard_normal(DIM))
        self.voix = []
        for _ in range(nb):
            p = self.rng.standard_normal(DIM)
            p = unite(p - (p @ self.commun) * self.commun)
            self.voix.append(unite(self.commun + 1.1 * p))

    def morceaux(self, plan, hauteurs, bruit=0.3, duree=6.0, voise=4.0):
        out, t, i = [], 0.0, 0
        for k, nb in plan:
            for _ in range(nb):
                e = unite(self.voix[k] + bruit * unite(self.rng.standard_normal(DIM)))
                out.append({'i': i, 'debut_s': round(t, 3), 'fin_s': round(t + duree, 3), 'muet': False,
                            'f0_med': round(float(hauteurs[k] * 2 ** self.rng.normal(0, 0.05)), 1), 'voise_s': voise,
                            'emb': e, 'debut': int(t * SR), 'fin': int((t + duree) * SR), 'verite': k})
                i += 1
                t += duree
        return out


def cibles(nb, mode='ordre'):
    cle = 'chanteur' if mode == 'ordre' else 'personne'
    return {'mode': mode, 'voix': [{'cle': f'{cle}{k + 1}', 'nom': NOMS[k], 'centre_hz': CENTRES[k], 'fichier': f'voix-{NOMS[k]}.wav'}
                                   for k in range(nb)]}


R = {'voise_min_s': 0.8, 'centre_homme_hz': 250, 'centre_femme_hz': 420}
HAUTEURS = [120, 250, 130, 240, 150, 200]


class LectureDesReglages(unittest.TestCase):
    def voix(self, nb, cle='chanteur'):
        return [{'cle': f'{cle}{k + 1}', 'nom': NOMS[k], 'fichier': f'{NOMS[k]}.wav'} for k in range(nb)]

    def test_de_deux_a_six(self):
        for nb in range(2, 7):
            c = N.lire_cibles({'voix_cibles': {'voix': self.voix(nb)}})
            self.assertEqual((c['mode'], len(c['voix'])), ('ordre', nb))

    def test_personnes(self):
        c = N.lire_cibles({'voix_cibles': {'voix': self.voix(3, 'personne')}})
        self.assertEqual(c['mode'], 'personnes')
        c = N.lire_cibles({'voix_cibles': {'mode': 'personnes', 'voix': self.voix(2, 'personne')}})
        self.assertEqual(c['mode'], 'personnes')

    def test_genre_a_deux_au_plus(self):
        v = self.voix(3)
        with self.assertRaises(ValueError):
            N.lire_cibles({'voix_cibles': {'mode': 'genre', 'voix': v}})


class QuatreChanteurs(unittest.TestCase):
    def setUp(self):
        self.m = Monde(4)
        # apparition : 0, 1, 2, 3 puis reprises, sections de 24 à 30 s
        self.segs = self.m.morceaux([(0, 5), (1, 5), (2, 4), (3, 5), (1, 4), (0, 5), (3, 4), (2, 5)], HAUTEURS)

    def test_un_chanteur_par_voix_dans_l_ordre_d_apparition(self):
        info, modele = N.decider_chant_cibles(self.segs, R, cibles(4))
        self.assertEqual(info['voix'], 4)
        self.assertEqual(info['axe'], 'empreintes')
        # chanteur 1 = le premier entendu (verite 0), 2 = verite 1, 3 = verite 2, 4 = verite 3
        attendu = [NOMS[s['verite']] for s in self.segs]
        self.assertEqual([s['voix_cible'] for s in self.segs], attendu)
        self.assertEqual([s['chanteur'] for s in self.segs], [f"chanteur{s['verite'] + 1}" for s in self.segs])
        self.assertIsNotNone(modele)
        self.assertEqual(len(modele['centres']), 4)
        self.assertEqual(sorted(c['voix'] for c in info['chanteurs']), sorted(NOMS[:4]))
        json.dumps(info)   # le rapport reste du JSON

    def test_ordre_d_apparition_et_non_ordre_des_empreintes(self):
        # le premier à chanter est la 3e empreinte du monde : il prend quand même la première voix
        segs = self.m.morceaux([(2, 5), (0, 5), (3, 5), (1, 5), (2, 3)], HAUTEURS)
        N.decider_chant_cibles(segs, R, cibles(4))
        rang = {2: 'jules', 0: 'marie', 3: 'luc', 1: 'lea'}
        self.assertEqual([s['voix_cible'] for s in segs], [rang[s['verite']] for s in segs])

    def test_decalage_de_chaque_couple(self):
        # chanteur 1 (120 Hz) -> jules (centre 200) : 0 ; chanteur 2 (250 Hz) -> marie (centre 400) : +12 ;
        # chanteur 3 (130 Hz) -> luc (centre 220) : 0 ; chanteur 4 (240 Hz) -> lea (centre 380) : +12
        info, _ = N.decider_chant_cibles(self.segs, R, cibles(4))
        par_voix = {c['voix']: c['decalage'] for c in info['chanteurs']}
        self.assertEqual(par_voix, {'jules': 0, 'marie': 12, 'luc': 0, 'lea': 12})
        self.assertEqual([s['decalage'] for s in self.segs],
                         [{'jules': 0, 'marie': 12, 'luc': 0, 'lea': 12}[s['voix_cible']] for s in self.segs])
        self.assertEqual(set(info['decalage_par_voix_et_classe']), set(NOMS[:4]))

    def test_muets_suivent_le_morceau_precedent(self):
        self.segs[7].update({'muet': True, 'voise_s': 0.0, 'f0_med': None, 'emb': None})
        N.decider_chant_cibles(self.segs, R, cibles(4))
        self.assertEqual(self.segs[7]['voix_cible'], self.segs[6]['voix_cible'])
        self.assertNotIn('voix', self.segs[7])

    def test_trois_voix_demandees_trois_chanteurs_une_voix_de_trop(self):
        segs = self.m.morceaux([(0, 5), (1, 5), (2, 5), (0, 4)], HAUTEURS)
        info, _ = N.decider_chant_cibles(segs, R, cibles(5))
        self.assertEqual(info['voix'], 3)
        self.assertEqual(info['demandees'], 5)
        self.assertIn('sans chanteur', info['message'])
        self.assertEqual({s['voix_cible'] for s in segs}, {'jules', 'marie', 'luc'})


class SixChanteurs(unittest.TestCase):
    def test_six_voix(self):
        m = Monde(6, graine=5)
        segs = m.morceaux([(k, 5) for k in range(6)] + [(k, 3) for k in (3, 1, 5, 0, 2, 4)], HAUTEURS)
        info, modele = N.decider_chant_cibles(segs, R, cibles(6))
        self.assertEqual(info['voix'], 6)
        self.assertEqual([s['voix_cible'] for s in segs], [NOMS[s['verite']] for s in segs])
        self.assertEqual(len(modele['decalage']), 6)


class Replis(unittest.TestCase):
    def test_une_seule_voix_chante(self):
        m = Monde(1)
        segs = m.morceaux([(0, 14)], HAUTEURS)
        info, modele = N.decider_chant_cibles(segs, R, cibles(4))
        self.assertEqual(info['voix'], 1)
        self.assertIsNone(modele)
        self.assertEqual({s['voix_cible'] for s in segs}, {'jules'})
        self.assertIn('une seule voix', info['message'])

    def test_deux_chanteurs_pour_quatre_voix(self):
        m = Monde(2)
        segs = m.morceaux([(0, 6), (1, 6), (0, 6), (1, 6)], HAUTEURS)
        info, modele = N.decider_chant_cibles(segs, R, cibles(4))
        self.assertEqual(info['voix'], 2)
        self.assertEqual({s['voix_cible'] for s in segs}, {'jules', 'marie'})
        self.assertEqual(set(info['decalage_par_voix_et_classe']), set(NOMS[:4]), 'les autres voix ont leur décalage')

    def test_deux_voix_comme_avant(self):
        # même décision que le chemin à deux voix, sans passer par le chemin à N
        m = Monde(2)
        segs = m.morceaux([(0, 6), (1, 6), (0, 6), (1, 6)], HAUTEURS)
        a = [dict(s) for s in segs]
        N.decider_chant_cibles(a, R, cibles(2))
        b = [dict(s) for s in segs]
        N.decider_chant_cibles(b, R, cibles(2, 'personnes'))
        self.assertEqual([(s['voix_cible'], s['decalage']) for s in a], [(s['voix_cible'], s['decalage']) for s in b])


class ReconnaissanceDesPistes(unittest.TestCase):
    def setUp(self):
        self.m = Monde(4)
        self.segs = self.m.morceaux([(0, 5), (1, 5), (2, 5), (3, 5)], HAUTEURS)
        self.info, self.modele = N.decider_chant_cibles(self.segs, R, cibles(4))
        self.R = {**N.OCTAVE_DEFAUT, **N.CHANTEURS_DEFAUT}

    def mesure(self, verite, f0, niveau=-3.0):
        e = unite(self.m.voix[verite] + 0.2 * unite(self.m.rng.standard_normal(DIM)))
        return {'voise_s': 4.0, 'f0_med': f0, 'niveau_db': niveau, 'score': [round(float(e @ c), 3) for c in self.modele['centres']]}

    def test_choeur_d_un_autre_chanteur(self):
        # morceau du chanteur 1 (k = 0), piste chantée par le chanteur 3 (verite 2)
        k, etat, raison, marge = N.reconnaitre(self.mesure(2, 130.0), self.modele, 0, self.R)
        self.assertEqual((k, etat), (2, 'autre'))
        self.assertGreaterEqual(marge, 2.0)
        self.assertIn(self.modele['noms'][2], raison)

    def test_meme_chanteur(self):
        k, etat, *_ = N.reconnaitre(self.mesure(1, 250.0), self.modele, 1, self.R)
        self.assertEqual((k, etat), (1, 'meme'))

    def test_piste_faible_garde_le_chanteur_du_morceau(self):
        k, etat, *_ = N.reconnaitre(self.mesure(3, 240.0, niveau=-30.0), self.modele, 0, self.R)
        self.assertEqual((k, etat), (0, 'faible'))

    def test_affectation_des_pistes(self):
        mesures = {'choeurs_sec': {s['i']: self.mesure((s['verite'] + 1) % 4, HAUTEURS[(s['verite'] + 1) % 4])
                                   for s in self.segs}}
        aff = N.affecter_pistes(self.segs, ['voix', 'choeurs_sec'], mesures, self.modele, cibles(4), None, None, 0)
        for s in self.segs:
            self.assertEqual(aff['voix'][s['i']]['voix'], NOMS[s['verite']])
            self.assertEqual(aff['choeurs_sec'][s['i']]['voix'], NOMS[(s['verite'] + 1) % 4])
            self.assertEqual(aff['choeurs_sec'][s['i']]['etat'], 'autre')

    def test_mesurer_piste_donne_un_score_par_chanteur(self):
        y = (np.sin(2 * np.pi * 200 * np.arange(SR * 2) / SR) * 0.1).astype(np.float32)
        f0 = np.full(len(y) // N.PAS, 200.0)
        out = N.mesurer_piste(y, f0, y, [(0, 0, len(y))], lambda x: unite(np.ones(DIM)), self.modele['centres'])
        self.assertEqual(len(out[0]['score']), 4)


class TransposeEtVersions(unittest.TestCase):
    def test_transposition_commune_a_toutes_les_voix(self):
        m = Monde(4)
        segs = m.morceaux([(0, 5), (1, 5), (2, 5), (3, 5)], HAUTEURS)
        N.decider_chant_cibles(segs, R, cibles(4))
        info = {'chanteurs': [], 'decalage_par_voix_et_classe': {n: {'homme': 0, 'femme': 0} for n in NOMS[:4]}}
        decs = [s['decalage'] for s in segs]
        N.appliquer_transposition(segs, info, None, 2)
        self.assertEqual([s['decalage'] for s in segs], [d + 2 for d in decs])

    def test_versions_autres_pour_n_voix(self):
        morceaux = [(0, 0, 100, 0, 5, 5, 1), (1, 100, 200, 12, 5, 5, 3)]
        out = N.versions_autres(morceaux, [0, 12, 0, 12, 0], garder=None)
        self.assertEqual([r for r, _ in out], [1, 2, 3, 4], 'une version dans chacune des quatre autres voix')
        self.assertEqual([m[6] for m in out[0][1]], [2, 4])
        garde = N.versions_autres(morceaux, [0, 12, 0, 12, 0], garder={1})
        self.assertEqual([len(ms) for _, ms in garde], [1, 1, 1, 1])

    def test_voix_par_trame_n_voix(self):
        k = 1000
        lisse = np.zeros(k)
        morceaux = [(0, 0, 20 * N.PAS, 0, 0, 0, 3), (1, 20 * N.PAS, 40 * N.PAS, 0, 0, 0, 5)]
        p = {'morceaux': morceaux, 'gain_trames': np.ones(k), 'lisse': lisse, 'secours': False}
        code, _ = N.voix_par_trames([p], lisse, k, 6)
        self.assertEqual(int(code[10]), 3)
        self.assertEqual(int(code[30]), 5)
        self.assertEqual(int(code[100]), -1)

    def test_utiles_garde_les_passages_incertains(self):
        segs = [{'i': i, 'muet': False, 'f0_med': 150.0, 'base': 'x'} for i in range(4)]
        segs[2]['f0_med'] = 280.0   # hauteur ambiguë
        utiles = N.morceaux_utiles(segs, None, None, 4)
        self.assertIn(2, utiles)
        self.assertIn('hauteur ambiguë', utiles[2])


if __name__ == '__main__':
    unittest.main()
