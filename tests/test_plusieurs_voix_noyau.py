"""Plusieurs voix cibles, une par chanteur (noyau, sans GPU) : lecture des réglages, chanteur et voix cible de chaque
morceau sur des empreintes de synthèse (duo mixte, duo de même genre, voix seule), décalage de chaque couple chanteur ->
voix cible, reconnaissance du chanteur de chaque piste, plan des morceaux, assemblage, voix entendue à chaque trame,
rapport ; et, avec une seule voix, les mêmes résultats qu'avant au bit près (comparaison avec le noyau d'avant, lu dans
l'historique git). Voix et chanteurs inventés (jules, marie)."""
import json
import subprocess
import sys
import types
import unittest
from unittest import mock

import numpy as np

from commun import DEPOT, SR, charger_noyau, note

N = charger_noyau()
DIM = 192
AVANT = '048b423'   # develop juste avant les voix cibles multiples : le noyau à une seule voix


def unite(v):
    return v / np.linalg.norm(v)


class Monde:
    """Empreintes de synthèse : une direction commune, un axe homme - femme (d) et une part propre à chaque chanteur ;
    ancres de genre de part et d'autre de d (genre_score = empreinte · d)."""

    def __init__(self, graine=7):
        self.rng = np.random.default_rng(graine)
        self.commun = unite(self.rng.standard_normal(DIM))
        d = self.rng.standard_normal(DIM)
        self.d = unite(d - (d @ self.commun) * self.commun)
        self.ancres = {'homme': (0.5 * self.d).tolist(), 'femme': (-0.5 * self.d).tolist()}

    def chanteur(self, genre):
        p = self.rng.standard_normal(DIM)
        p = unite(p - (p @ self.commun) * self.commun - (p @ self.d) * self.d)
        return unite(self.commun + (0.35 if genre == 'homme' else -0.35) * self.d + 0.9 * p)

    def morceaux(self, plan, voix, hauteurs, bruit=0.3, duree=6.0, voise=4.0):
        """plan : [(chanteur, nombre de morceaux)] ; un morceau de duree s par entrée, voisé voise s."""
        out, t, i = [], 0.0, 0
        for k, nb in plan:
            for _ in range(nb):
                e = unite(voix[k] + bruit * unite(self.rng.standard_normal(DIM)))
                out.append({'i': i, 'debut_s': round(t, 3), 'fin_s': round(t + duree, 3), 'muet': False,
                            'f0_med': round(float(hauteurs[k] * 2 ** self.rng.normal(0, 0.08)), 1), 'voise_s': voise,
                            'emb': e, 'debut': int(t * SR), 'fin': int((t + duree) * SR), 'verite': k})
                i += 1
                t += duree
        return out


def cibles(mode='genre', centres=(200.0, 400.0), noms=('jules', 'marie')):
    cles = ('homme', 'femme') if mode == 'genre' else ('chanteur1', 'chanteur2')
    return {'mode': mode, 'voix': [{'cle': c, 'nom': n, 'centre_hz': x, 'fichier': f'voix-{n}.wav'}
                                   for c, n, x in zip(cles, noms, centres)]}


def reglages(monde, **k):
    return {'ancres': monde.ancres, 'voise_min_s': 0.8, 'centre_homme_hz': 250, 'centre_femme_hz': 420, **k}


class LectureDesReglages(unittest.TestCase):
    def test_absentes_ou_une_seule_voix(self):
        self.assertIsNone(N.lire_cibles({}))
        self.assertIsNone(N.lire_cibles({'voix_cibles': None}))
        une = {'voix_cibles': {'mode': 'genre', 'voix': [{'cle': 'homme', 'nom': 'jules', 'fichier': 'voix-jules.wav'}]}}
        self.assertIsNone(N.lire_cibles(une))
        meme = {'voix_cibles': {'mode': 'genre', 'voix': [{'cle': 'homme', 'nom': 'jules', 'fichier': 'voix-jules.wav'},
                                                          {'cle': 'femme', 'nom': 'jules', 'fichier': 'voix-jules.wav'}]}}
        self.assertIsNone(N.lire_cibles(meme), 'deux fois la même voix : une seule voix, le cas d’avant')

    def test_deux_voix_et_mode(self):
        r = {'voix_cibles': {'voix': [{'cle': 'homme', 'nom': 'jules', 'fichier': 'a.wav'},
                                      {'cle': 'femme', 'nom': 'marie', 'fichier': 'b.wav'}]}}
        c = N.lire_cibles(r)
        self.assertEqual(c['mode'], 'genre')
        self.assertEqual([v['nom'] for v in c['voix']], ['jules', 'marie'])
        r['voix_cibles']['voix'][0]['cle'], r['voix_cibles']['voix'][1]['cle'] = 'chanteur1', 'chanteur2'
        self.assertEqual(N.lire_cibles(r)['mode'], 'ordre')
        r['voix_cibles']['mode'] = 'hasard'
        with self.assertRaises(ValueError):
            N.lire_cibles(r)


class DuoMixte(unittest.TestCase):
    """Mode genre : chanteur (vers 200 Hz) et chanteuse (vers 400 Hz) en sections alternées."""

    def setUp(self):
        self.m = Monde()
        self.voix = [self.m.chanteur('homme'), self.m.chanteur('femme')]
        self.segs = self.m.morceaux([(0, 5), (1, 5), (0, 5), (1, 5)], self.voix, [200, 400])

    def test_chaque_morceau_prend_la_voix_de_son_chanteur(self):
        info, modele = N.decider_chant_cibles(self.segs, reglages(self.m), cibles())
        self.assertEqual(info['voix'], 2)
        self.assertEqual(info['axe'], 'genre')
        attendu = ['jules' if s['verite'] == 0 else 'marie' for s in self.segs]
        self.assertEqual([s['voix_cible'] for s in self.segs], attendu)
        self.assertEqual([s['chanteur'] for s in self.segs], ['homme' if s['verite'] == 0 else 'femme' for s in self.segs])
        self.assertEqual({s['voix'] for s in self.segs}, {'homme', 'femme'})   # l'interface lit la classe dans « voix »
        self.assertIsNotNone(modele)
        self.assertEqual(sorted(c['voix'] for c in info['chanteurs']), ['jules', 'marie'])
        json.dumps(info)   # le rapport reste du JSON (pas de numpy)

    def test_decalage_de_chaque_couple(self):
        # voix d'homme (centre 200 Hz) pour le chanteur, voix de femme (400 Hz) pour la chanteuse : 0 partout
        N.decider_chant_cibles(self.segs, reglages(self.m), cibles())
        self.assertEqual({s['decalage'] for s in self.segs}, {0})
        # voix croisées : homme -> une voix de femme (+12), femme -> une voix d'homme (-12)
        segs = [dict(s) for s in self.segs]
        info, _ = N.decider_chant_cibles(segs, reglages(self.m), cibles(centres=(400.0, 200.0), noms=('marie', 'jules')))
        self.assertEqual([s['decalage'] for s in segs], [12 if s['verite'] == 0 else -12 for s in segs])
        couples = {c['classe']: (c['voix'], c['decalage']) for c in info['chanteurs']}
        self.assertEqual(couples, {'homme': ('marie', 12), 'femme': ('jules', -12)})
        self.assertEqual(info['decalage_par_voix_et_classe'], {'marie': {'homme': 12, 'femme': 0},
                                                                'jules': {'homme': 0, 'femme': -12}})

    def test_mode_ordre_garde_le_duo_mixte_sur_l_axe_du_genre(self):
        info, _ = N.decider_chant_cibles(self.segs, reglages(self.m), cibles('ordre'))
        self.assertEqual(info['axe'], 'genre')
        # chanteur 1 : celui qui chante le premier (le chanteur) -> la première voix
        self.assertEqual([s['voix_cible'] for s in self.segs], ['jules' if s['verite'] == 0 else 'marie' for s in self.segs])

    def test_muets_et_sourds_suivent_le_morceau_precedent(self):
        self.segs[6].update({'muet': True, 'voise_s': 0.0, 'f0_med': None, 'emb': None})
        self.segs[7].update({'voise_s': 0.0, 'f0_med': None})
        N.decider_chant_cibles(self.segs, reglages(self.m), cibles())
        self.assertEqual(self.segs[6]['voix_cible'], self.segs[5]['voix_cible'])
        self.assertEqual(self.segs[7]['voix_cible'], self.segs[5]['voix_cible'])
        self.assertNotIn('voix', self.segs[6])


class VoixSeule(unittest.TestCase):
    def setUp(self):
        self.m = Monde(11)

    def test_chanteuse_seule_tout_en_voix_de_femme(self):
        segs = self.m.morceaux([(0, 16)], [self.m.chanteur('femme')], [410])
        info, modele = N.decider_chant_cibles(segs, reglages(self.m), cibles())
        self.assertIsNone(modele)
        self.assertEqual(info['voix'], 1)
        self.assertEqual({s['voix_cible'] for s in segs}, {'marie'})
        self.assertIn('une seule voix détectée (femme)', info['message'])
        self.assertIn('marie', info['message'])
        self.assertEqual({s['decalage'] for s in segs}, {0})   # 420 Hz typique contre une voix de femme à 400 Hz

    def test_mode_ordre_voix_seule_prend_la_premiere(self):
        segs = self.m.morceaux([(0, 16)], [self.m.chanteur('femme')], [410])
        info, _ = N.decider_chant_cibles(segs, reglages(self.m), cibles('ordre'))
        self.assertEqual({s['voix_cible'] for s in segs}, {'jules'})
        self.assertIn('chanteur 1', info['message'])
        self.assertEqual({s['decalage'] for s in segs}, {-12})   # chanteuse -> voix d'homme : -12

    def test_un_chanteur_deux_styles_n_est_pas_un_duo(self):
        """Couplet et refrain d'un même chanteur : ses empreintes forment deux groupes proches (le style), mais trop
        peu séparés pour deux chanteurs (silhouette sous le seuil)."""
        h = self.m.chanteur('homme')
        style = unite(self.m.rng.standard_normal(DIM))
        styles = [unite(h + 0.25 * style), unite(h - 0.25 * style)]
        segs = self.m.morceaux([(0, 5), (1, 5), (0, 5), (1, 5)], styles, [200, 200], bruit=0.6)
        info, modele = N.decider_chant_cibles(segs, reglages(self.m), cibles('ordre'))
        self.assertIsNone(modele)
        self.assertLess(info['essai_duo']['empreintes']['silhouette'], N.CHANTEURS_DEFAUT['axe_silhouette_min'])
        self.assertEqual({s['voix_cible'] for s in segs}, {'jules'})

    def test_sans_ancres_classe_d_apres_la_hauteur(self):
        segs = self.m.morceaux([(0, 16)], [self.m.chanteur('homme')], [190])
        info, modele = N.decider_chant_cibles(segs, {'voise_min_s': 0.8}, cibles())
        self.assertIsNone(modele)
        self.assertIn('sans ancres', info['empreinte'])
        self.assertEqual({s['voix_cible'] for s in segs}, {'jules'})

    def test_aucun_morceau_chante(self):
        segs = self.m.morceaux([(0, 3)], [self.m.chanteur('homme')], [190])
        for s in segs:
            s.update({'muet': True, 'voise_s': 0.0, 'f0_med': None, 'emb': None})
        info, modele = N.decider_chant_cibles(segs, reglages(self.m), cibles())
        self.assertIsNone(modele)
        self.assertEqual(info['voix'], 0)
        self.assertEqual({s['decalage'] for s in segs}, {0})


class DuoMemeGenre(unittest.TestCase):
    def setUp(self):
        self.m = Monde(5)
        self.voix = [self.m.chanteur('homme'), self.m.chanteur('homme')]

    def test_mode_genre_une_seule_voix_pour_deux_hommes(self):
        segs = self.m.morceaux([(0, 5), (1, 5), (0, 5), (1, 5)], self.voix, [200, 210])
        info, modele = N.decider_chant_cibles(segs, reglages(self.m), cibles())
        self.assertIsNone(modele)
        self.assertEqual({s['voix_cible'] for s in segs}, {'jules'})

    def test_mode_ordre_departage_par_l_empreinte(self):
        segs = self.m.morceaux([(0, 5), (1, 5), (0, 5), (1, 5)], self.voix, [200, 210])
        info, modele = N.decider_chant_cibles(segs, reglages(self.m), cibles('ordre'))
        self.assertEqual(info['axe'], 'empreintes')
        self.assertGreaterEqual(info['essai_duo']['empreintes']['silhouette'], N.CHANTEURS_DEFAUT['axe_silhouette_min'])
        self.assertEqual([s['voix_cible'] for s in segs], ['jules' if s['verite'] == 0 else 'marie' for s in segs])
        self.assertEqual([s['chanteur'] for s in segs], ['chanteur1' if s['verite'] == 0 else 'chanteur2' for s in segs])
        # deux hommes : noms numérotés dans l'ordre d'apparition ; chanteur 2 -> voix de femme : +12
        self.assertEqual({s['voix'] for s in segs}, {'homme 1', 'homme 2'})
        self.assertEqual({(s['voix_cible'], s['decalage']) for s in segs}, {('jules', 0), ('marie', 12)})
        self.assertIsNotNone(modele['vecteur'])

    def test_ordre_d_apparition(self):
        segs = self.m.morceaux([(1, 5), (0, 5), (1, 5), (0, 5)], self.voix, [210, 200])
        N.decider_chant_cibles(segs, reglages(self.m), cibles('ordre'))
        self.assertEqual(segs[0]['voix_cible'], 'jules', 'le premier qui chante est le chanteur 1')
        self.assertEqual([s['voix_cible'] for s in segs], ['jules' if s['verite'] == 1 else 'marie' for s in segs])


def modele_mixte():
    """Modèle de reconnaissance d'un duo mixte : axe du genre (chanteuse -0,25, chanteur +0,25), hauteurs 400 / 200 Hz."""
    return {'axe': 'genre_score', 'vecteur': None, 'mu': [-0.25, 0.25], 'sigma': [0.05, 0.05],
            'hauteur': [(np.log2(400), 0.1), (np.log2(200), 0.1)], 'cible': [1, 0], 'decalage': [0, 0],
            'noms': ['femme', 'homme']}


class Reconnaissance(unittest.TestCase):
    R = {**N.OCTAVE_DEFAUT, **N.CHANTEURS_DEFAUT}

    def test_l_autre_chanteur_reconnu_par_hauteur_et_empreinte(self):
        k, etat, raison, marge = N.reconnaitre({'voise_s': 3.0, 'f0_med': 390, 'score': -0.22, 'niveau_db': -6},
                                               modele_mixte(), 1, self.R)
        self.assertEqual((k, etat), (0, 'autre'))
        self.assertIn('empreinte et hauteur', raison)
        self.assertGreater(marge, self.R['marge_reconnaissance'])

    def test_meme_chanteur(self):
        k, etat, _, marge = N.reconnaitre({'voise_s': 3.0, 'f0_med': 205, 'score': 0.27, 'niveau_db': -1},
                                          modele_mixte(), 1, self.R)
        self.assertEqual((k, etat), (1, 'meme'))
        self.assertLess(marge, 0)

    def test_incertaine_garde_le_chanteur_du_morceau(self):
        # entre les deux : hauteur au milieu géométrique, empreinte au milieu
        k, etat, _, _ = N.reconnaitre({'voise_s': 3.0, 'f0_med': 283, 'score': 0.0, 'niveau_db': -3}, modele_mixte(), 0, self.R)
        self.assertEqual((k, etat), (0, 'incertaine'))
        k, etat, _, _ = N.reconnaitre({'voise_s': 3.0, 'f0_med': 283, 'score': 0.0, 'niveau_db': -3}, modele_mixte(), 1, self.R)
        self.assertEqual((k, etat), (1, 'incertaine'))

    def test_piste_faible_ou_sans_voisement(self):
        k, etat, raison, _ = N.reconnaitre({'voise_s': 3.0, 'f0_med': 390, 'score': -0.25, 'niveau_db': -32},
                                           modele_mixte(), 1, self.R)
        self.assertEqual((k, etat), (1, 'faible'))
        self.assertIn('-32 dB', raison)
        k, etat, _, _ = N.reconnaitre({'voise_s': 0.1, 'f0_med': 390, 'niveau_db': -3}, modele_mixte(), 1, self.R)
        self.assertEqual((k, etat), (1, 'sans_indice'))
        self.assertEqual(N.reconnaitre(None, modele_mixte(), 0, self.R)[1], 'sans_indice')

    def test_hauteur_seule_sans_empreinte(self):
        k, etat, raison, _ = N.reconnaitre({'voise_s': 3.0, 'f0_med': 410, 'niveau_db': -3}, modele_mixte(), 1, self.R)
        self.assertEqual((k, etat), (0, 'autre'))
        self.assertIn('(hauteur)', raison)


class MesuresDesPistes(unittest.TestCase):
    """mesurer_piste sur des sons : voix principale d'homme (200 Hz), chœurs de femme (400 Hz) 10 dB plus bas ;
    l'empreinte de synthèse suit la hauteur dominante du son (vers l'homme sous 300 Hz, vers la femme au-dessus)."""

    def setUp(self):
        self.duree = 12.0
        self.lead = note(200, 0.0, 12.0, -20, self.duree)
        self.choeurs = note(450, 6.0, 12.0, -30, self.duree)   # 450 Hz : aucune harmonique commune avec 200 Hz
        self.x = self.lead + self.choeurs
        k = int(self.duree * 100)
        self.f0_lead = np.full(k, 200.0)
        self.f0_ch = np.where(np.arange(k) >= 600, 450.0, 0.0)
        self.vec = np.array([1.0, -1.0])

        def empreinte(y):
            spec = np.abs(np.fft.rfft(y))
            f = np.argmax(spec) * SR / len(y)
            return unite(np.array([1.0, 0.2]) if f < 300 else np.array([0.2, 1.0]))
        self.empreinte = empreinte
        self.morceaux = [(0, 0, int(6 * SR)), (1, int(6 * SR), int(12 * SR))]

    def test_hauteur_niveau_et_score(self):
        m = N.mesurer_piste(self.choeurs, self.f0_ch, self.x, self.morceaux, self.empreinte, self.vec)
        self.assertIsNone(m[0]['f0_med'])
        self.assertEqual(m[1]['f0_med'], 450.0)
        self.assertAlmostEqual(m[1]['voise_s'], 6.0, delta=0.02)
        self.assertAlmostEqual(m[1]['niveau_db'], -10.4, delta=0.3)
        self.assertLess(m[1]['score'], 0)
        ml = N.mesurer_piste(self.lead, self.f0_lead, self.x, self.morceaux, self.empreinte, self.vec)
        self.assertGreater(ml[1]['score'], 0)
        self.assertAlmostEqual(ml[0]['niveau_db'], 0.0, delta=0.05)

    def test_porte_de_secours(self):
        porte = np.zeros(len(self.x), np.float32)
        porte[int(7 * SR):int(9 * SR)] = 1
        m = N.mesurer_piste(self.x, np.where(np.arange(1200) >= 600, 400.0, 200.0), self.x, self.morceaux,
                            self.empreinte, self.vec, porte)
        self.assertEqual(m[0]['voise_s'], 0.0)   # porte fermée sur tout le premier morceau
        self.assertAlmostEqual(m[1]['voise_s'], 2.0, delta=0.02)
        self.assertEqual(m[1]['niveau_db'], 0.0)

    def test_duo_dans_un_morceau(self):
        """Le chanteur sur la voix principale, la chanteuse sur les chœurs : deux voix différentes dans le même morceau."""
        modele = {**modele_mixte(), 'vecteur': self.vec, 'mu': [-0.8, 0.8], 'sigma': [0.1, 0.1]}
        mes = {'lead_sec': N.mesurer_piste(self.lead, self.f0_lead, self.x, self.morceaux, self.empreinte, self.vec),
               'choeurs_sec': N.mesurer_piste(self.choeurs, self.f0_ch, self.x, self.morceaux, self.empreinte, self.vec)}
        segs = [{'i': 0, 'debut_s': 0.0, 'fin_s': 6.0, 'muet': False, 'k': 1, 'cible': 0, 'decalage': 0},
                {'i': 1, 'debut_s': 6.0, 'fin_s': 12.0, 'muet': False, 'k': 1, 'cible': 0, 'decalage': 0}]
        aff = N.affecter_pistes(segs, ['lead_sec', 'choeurs_sec'], mes, modele, cibles())
        self.assertEqual((aff['lead_sec'][1]['voix'], aff['choeurs_sec'][1]['voix']), ('jules', 'marie'))
        self.assertEqual(aff['choeurs_sec'][1]['etat'], 'autre')
        self.assertEqual(aff['choeurs_sec'][0]['etat'], 'faible')   # chœurs muets : chanteur du morceau
        rap = N.rapport_affectation(segs, aff, {'lead_sec': {0, 1}, 'choeurs_sec': {1}}, cibles(), {'message': 'duo'})
        self.assertEqual(rap['morceaux_en_duo'], 1)
        self.assertEqual(rap['voix_utilisees'], ['jules', 'marie'])
        self.assertEqual(segs[1]['voix_pistes'], {'lead_sec': 'jules', 'choeurs_sec': 'marie'})
        self.assertEqual(segs[0]['voix_pistes'], {'lead_sec': 'jules'})
        self.assertEqual([l['i'] for l in rap['pistes']['choeurs_sec']], [1])
        self.assertEqual(rap['resume']['choeurs_sec'], {'morceaux': 1, 'par_voix': {'marie': 1}, 'etats': {'autre': 1}})
        json.dumps(rap)


class Affectation(unittest.TestCase):
    def segs(self):
        return [{'i': i, 'debut_s': 6.0 * i, 'fin_s': 6.0 * i + 6, 'muet': i == 2, 'k': (None if i == 2 else i % 2),
                 'cible': i % 2, 'decalage': 0} for i in range(4)]

    def test_voix_complete_et_voix_seule_suivent_le_morceau(self):
        segs = self.segs()
        aff = N.affecter_pistes(segs, ['voix'], {}, modele_mixte(), cibles())
        self.assertEqual(sorted(aff['voix']), [0, 1, 3], 'un morceau muet n’est pas converti')
        self.assertEqual({a['etat'] for a in aff['voix'].values()}, {'voix_complete'})
        self.assertEqual([aff['voix'][i]['voix'] for i in (0, 1, 3)], ['jules', 'marie', 'marie'])
        aff = N.affecter_pistes(segs, ['lead_sec', 'choeurs_sec'], {}, None, cibles())
        self.assertEqual({a['etat'] for p in aff.values() for a in p.values()}, {'voix_seule'})

    def test_octave_imposee(self):
        segs = self.segs()
        modele = {**modele_mixte(), 'decalage': [-12, 0]}
        mes = {'lead_sec': {0: {'voise_s': 3, 'f0_med': 400, 'score': -0.25, 'niveau_db': 0}}}
        aff = N.affecter_pistes(segs, ['lead_sec'], mes, modele, cibles())
        self.assertEqual((aff['lead_sec'][0]['voix'], aff['lead_sec'][0]['decalage']), ('marie', -12))
        aff = N.affecter_pistes(segs, ['lead_sec'], mes, modele, cibles(), octave_imposee=12)
        self.assertEqual({a['decalage'] for a in aff['lead_sec'].values()}, {12})


class PlanEtAssemblage(unittest.TestCase):
    def setUp(self):
        self.segs = [{'i': i, 'debut': i * SR, 'fin': (i + 1) * SR, 'muet': i == 1, 'decalage': 12 * (i % 2) - 0,
                      'cible': i % 2} for i in range(4)]
        k = 4 * 100 + 1
        self.piste = {'nom': 'lead_sec', 'lisse': np.full(k, -20.0), 'seuil': -60.0}

    def test_sans_affectation_six_elements(self):
        m = N.morceaux_piste(self.segs, self.piste, {SR: 100, 2 * SR: 200})
        self.assertEqual(m, [(0, 0, SR, 0, 0, 100), (2, 2 * SR, 3 * SR, 0, 200, 0), (3, 3 * SR, 4 * SR, 12, 0, 0)])

    def test_affectation_decalage_et_voix_de_la_piste(self):
        self.piste['affectation'] = {0: (1, 12), 2: (0, 0), 3: (1, -12)}
        m = N.morceaux_piste(self.segs, self.piste, {})
        self.assertEqual(m, [(0, 0, SR, 12, 0, 0, 1), (2, 2 * SR, 3 * SR, 0, 0, 0, 0), (3, 3 * SR, 4 * SR, -12, 0, 0, 1)])

    def test_assembler_voix_par_morceau(self):
        graines = []
        torch = types.SimpleNamespace(manual_seed=graines.append)
        x = np.random.default_rng(1).standard_normal(4 * SR).astype(np.float32)
        conv = lambda xs, d, v=0: xs * (1 + d / 100) + v
        six = [(0, 0, SR, 0, 0, 100), (2, 2 * SR, 3 * SR, 0, 200, 100)]
        sept = [m + (0,) for m in six]
        a = N.assembler(x, six, 2000, conv, 1234, torch)
        b = N.assembler(x, sept, 2000, conv, 1234, torch)
        self.assertTrue(np.array_equal(a, b), 'voix 0 partout : la même piste, au bit près')
        self.assertEqual(graines, [1234, 1236, 1234, 1236])
        appels = []
        N.assembler(x, [six[0] + (1,), six[1] + (0,)], 2000, lambda xs, d, v=0: appels.append(v) or xs, 1, torch)
        self.assertEqual(appels, [1, 0])
        appels = []
        N.assembler(x, six, 2000, lambda *a: appels.append(len(a)) or a[0], 1, torch)
        self.assertEqual(appels, [2, 2], 'une seule voix : convertir_morceau(xs, décalage), comme avant')


class VoixParTrame(unittest.TestCase):
    def test_codes_dominante_et_passages(self):
        k = 400
        lead = {'morceaux': [(0, 0, 200 * N.PAS, 0, 0, 0, 0)], 'gain_trames': np.ones(k), 'lisse': np.full(k, -20.0)}
        ch = {'morceaux': [(1, 100 * N.PAS, 300 * N.PAS, -12, 0, 0, 1)], 'gain_trames': np.ones(k), 'lisse': np.full(k, -30.0)}
        faible = {'morceaux': [(2, 300 * N.PAS, 350 * N.PAS, 5, 0, 0, 0)], 'gain_trames': np.ones(k), 'lisse': np.full(k, -60.0)}
        code, dec = N.voix_par_trames([lead, ch, faible], np.full(k, -20.0), k, 2)
        self.assertEqual(code[50], 0)
        self.assertEqual(code[150], 2, 'les deux voix ensemble : un duo')
        self.assertEqual(code[250], 1)
        self.assertEqual(code[320], -1, 'piste 40 dB sous la voix complète : ne compte pas')
        self.assertEqual((dec[150], dec[250]), (0, -12), 'décalage de la piste dominante')
        p = N.passages_voix(code, ['jules', 'marie'])
        self.assertEqual(p, [{'debut_s': 0.0, 'fin_s': 1.0, 'voix': ['jules']},
                             {'debut_s': 1.0, 'fin_s': 2.0, 'voix': ['jules', 'marie']},
                             {'debut_s': 2.0, 'fin_s': 3.0, 'voix': ['marie']}])

    def test_secours_compte_sur_sa_porte(self):
        k = 100
        g = np.zeros(k)
        g[20:40] = 1
        sec = {'morceaux': [(0, 0, 100 * N.PAS, 0, 0, 0, 1)], 'gain_trames': g, 'lisse': np.full(k, -90.0), 'secours': True}
        code, _ = N.voix_par_trames([sec], np.full(k, -20.0), k, 2)
        self.assertEqual(code[30], 1)
        self.assertEqual(code[10], -1)


class EtapeYing(unittest.TestCase):
    """etape_ying : chaque référence préparée une fois, chaque morceau converti avec celle de sa voix."""

    def lancer(self, module, travail):
        prepare, appels, ecrits = [], [], {}

        class FauxMoteur:
            installation, chargement_s = {'origine': 'essai'}, 1.0
            torch = types.SimpleNamespace(manual_seed=lambda g: None, cuda=types.SimpleNamespace(empty_cache=lambda: None))

            def __init__(self, *a):
                pass

            def preparer_ref(self, chemin):
                prepare.append(chemin)
                return {'chemin': chemin}

            def convertir_morceau(self, xs, ref, dec, pas):
                appels.append((ref['chemin'], dec))
                return xs * 0.5 + dec

        faux_sf = types.SimpleNamespace(write=lambda chemin, y, sr, subtype=None: ecrits.__setitem__(chemin, np.array(y)))
        x = np.linspace(-1, 1, 3 * SR).astype(np.float32)
        with mock.patch.dict(sys.modules, {'soundfile': faux_sf}), \
                mock.patch.object(module, 'MoteurYing', FauxMoteur), mock.patch.object(module, 'lire_mono', lambda c: x), \
                mock.patch.object(module, 'ecrire_json', lambda c, d: None), mock.patch.object(module, 'log', lambda *a: None), \
                mock.patch.dict(module.REGLAGES, {'graine': 1234}):
            module.etape_ying(travail)
        return prepare, appels, ecrits

    def travail(self, morceaux, **k):
        return {'reference': 'ref-jules.wav', 'pad': 1000, 'resultat': '/inexistant/r.json',
                'pistes': [{'cle': 'v|f|lead_sec', 'entree': 'lead.wav', 'sortie': 'sortie.wav', 'pas': 30,
                            'morceaux': morceaux}], **k}

    def test_plusieurs_references(self):
        m = [[0, 0, SR, 0, 0, 100, 0], [1, SR, 2 * SR, 12, 100, 100, 1], [2, 2 * SR, 3 * SR, 0, 100, 0, 0]]
        prepare, appels, _ = self.lancer(N, self.travail(m, references=['ref-jules.wav', 'ref-marie.wav']))
        self.assertEqual(prepare, ['ref-jules.wav', 'ref-marie.wav'], 'chaque référence préparée une seule fois')
        self.assertEqual(appels, [('ref-jules.wav', 0), ('ref-marie.wav', 12), ('ref-jules.wav', 0)])

    @unittest.skipUnless(subprocess.run(['git', '-C', str(DEPOT), 'cat-file', '-e', f'{AVANT}:kaggle/doubler_kaggle.py'],
                                        capture_output=True).returncode == 0, 'historique git absent')
    def test_une_voix_comme_avant(self):
        ancien = noyau_avant()
        m = [[0, 0, SR, 0, 0, 100], [1, SR, 2 * SR, -12, 100, 100]]
        p_new, a_new, e_new = self.lancer(N, self.travail(m))
        p_old, a_old, e_old = self.lancer(ancien, self.travail(m))
        self.assertEqual((p_new, a_new), (p_old, a_old))
        self.assertTrue(np.array_equal(e_new['sortie.wav'], e_old['sortie.wav']))


def noyau_avant():
    import base64
    src = subprocess.run(['git', '-C', str(DEPOT), 'show', f'{AVANT}:kaggle/doubler_kaggle.py'], capture_output=True,
                         text=True, check=True).stdout
    src = src.replace("'__REGLAGES__'", repr(base64.b64encode(b'{}').decode()))
    m = types.ModuleType('doubler_kaggle_avant')
    exec(compile(src, 'doubler_kaggle_avant.py', 'exec'), m.__dict__)
    return m


@unittest.skipUnless(subprocess.run(['git', '-C', str(DEPOT), 'cat-file', '-e', f'{AVANT}:kaggle/doubler_kaggle.py'],
                                    capture_output=True).returncode == 0, 'historique git absent')
class UneVoixAuBitPres(unittest.TestCase):
    """Une seule voix : les fonctions du chemin d'avant rendent exactement ce que rendait le noyau d'avant."""

    @classmethod
    def setUpClass(cls):
        cls.A = noyau_avant()

    def test_decision_d_octave(self):
        m = Monde(3)
        voix = [m.chanteur('homme'), m.chanteur('femme')]
        segs = m.morceaux([(0, 5), (1, 5), (0, 5), (1, 5)], voix, [200, 400])
        for centre in (200.0, 290.0, 400.0):
            a, b = [dict(s) for s in segs], [dict(s) for s in segs]
            R = reglages(m)
            ia, ib = self.A.decider_chant(a, R, centre, None), N.decider_chant(b, R, centre, None)
            self.assertEqual(json.dumps(ia, sort_keys=True), json.dumps(ib, sort_keys=True))
            self.assertEqual([(s['decalage'], s.get('voix'), s['base']) for s in a],
                             [(s['decalage'], s.get('voix'), s['base']) for s in b])

    def test_plan_et_assemblage(self):
        segs = [{'i': i, 'debut': i * SR, 'fin': (i + 1) * SR, 'muet': i == 1, 'decalage': (-12, 0, 0, 12)[i]}
                for i in range(4)]
        k = 4 * 100 + 1
        lisse = np.full(k, -20.0)
        lisse[200:300] = -80
        for piste in ({'nom': 'voix'}, {'nom': 'lead_sec', 'lisse': lisse, 'seuil': -60.0},
                      {'nom': 'secours', 'zones': [{'debut_s': 2.5, 'fin_s': 3.2}]}):
            self.assertEqual(N.morceaux_piste(segs, piste, {SR: 100}), self.A.morceaux_piste(segs, piste, {SR: 100}))
        torch = types.SimpleNamespace(manual_seed=lambda g: None)
        x = np.random.default_rng(2).standard_normal(4 * SR).astype(np.float32)
        plan = N.morceaux_piste(segs, {'nom': 'voix'}, {SR: 100, 2 * SR: 2000, 3 * SR: 300})
        conv = lambda xs, d: np.sin(xs * (1 + d / 24))
        self.assertTrue(np.array_equal(N.assembler(x, plan, 1000, conv, 7, torch),
                                       self.A.assembler(x, plan, 1000, conv, 7, torch)))


if __name__ == '__main__':
    unittest.main()
