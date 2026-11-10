"""Transposition de la chanson dans le noyau (sans GPU) : la règle du décalage (un nombre entier de demi-tons pour la
musique, au plus ±5, et l'octave de chaque voix en plus), sur des voix inventées : ténor aigu, chanteuse, voix proche de
la zone, duo, parole, bornes, --transposer non et N ; puis la décision sur des morceaux simulés (voix seule et duo),
l'octave imposée, le rapport et la zone envoyée par doubler.py (tessiture.json ou parole)."""
import math
import unittest

import numpy as np

from commun import SR, charger_noyau
from test_plusieurs_voix_noyau import Monde, cibles, reglages

N = charger_noyau()
PAS = N.PAS
CENTRE = 190.0
ZONE = [CENTRE * 2 ** (-4 / 12), CENTRE * 2 ** (4 / 12)]   # sans tessiture : centre de chant ±4 demi-tons


def voix(p10, p50, p90, zone=None, nom='jules'):
    return {'nom': nom, 'p10': p10, 'p50': p50, 'p90': p90, 'confort': zone or ZONE}


def dt_de(*v, **k):
    return N.choisir_transposition(list(v), **k)['musique_dt']


def f0_des_morceaux(segs, ecart=0.06, graine=3):
    """F0 RMVPE inventée : chaque morceau chanté tient sa hauteur (f0_med) à quelques centièmes d'octave près."""
    rng = np.random.default_rng(graine)
    f0 = np.zeros(max(s['fin'] for s in segs) // PAS + 2)
    for s in segs:
        if not s.get('muet') and s.get('f0_med'):
            a, b = s['debut'] // PAS, s['fin'] // PAS
            f0[a:b] = s['f0_med'] * 2 ** rng.normal(0, ecart, b - a)
    return f0


class RegleDuDecalage(unittest.TestCase):
    """choisir_transposition : celle de banc/realisme/transposition.py (regle_decalage), sur des hauteurs de voix
    convertie, octave comprise, face à une zone de confort."""

    def test_voix_proche_de_la_zone_ne_bouge_pas(self):
        r = N.choisir_transposition([voix(170, 192, 225)])
        self.assertEqual(r['musique_dt'], 0)
        self.assertTrue(r['voix'][0]['dans_zone'])

    def test_tenor_aigu_chanson_baissee(self):
        """Un ténor aigu (médiane 262 Hz une fois l'octave posée) : la chanson descend, la voix converti tombe dans la zone."""
        r = N.choisir_transposition([voix(222, 262, 310)])
        dt = r['musique_dt']
        self.assertIsInstance(dt, int)
        self.assertIn(dt, (-5, -4, -3))
        self.assertTrue(r['voix'][0]['dans_zone'], r['voix'])
        med = r['voix'][0]['apres_hz']['p50']
        self.assertLess(abs(math.log2(med / CENTRE)) * 12, 2.5)

    def test_chanteuse_chanson_baissee_un_peu(self):
        """Une chanteuse déjà descendue d'une octave (médiane 215 Hz) : un ou deux demi-tons de moins."""
        r = N.choisir_transposition([voix(185, 215, 255)])
        self.assertIn(r['musique_dt'], (-1, -2, -3))
        self.assertTrue(r['voix'][0]['dans_zone'])

    def test_voix_grave_chanson_montee(self):
        r = N.choisir_transposition([voix(120, 140, 165)])
        self.assertGreater(r['musique_dt'], 0)
        self.assertLessEqual(r['musique_dt'], 5)

    def test_bornes_plus_ou_moins_cinq(self):
        """Une voix très au-dessus ou très au-dessous de la zone : la chanson ne bouge jamais de plus de 5 demi-tons."""
        self.assertEqual(dt_de(voix(380, 440, 520)), -5)
        self.assertEqual(dt_de(voix(60, 70, 85)), 5)
        for p50 in range(60, 700, 20):
            self.assertLessEqual(abs(dt_de(voix(p50 * 0.85, p50, p50 * 1.2))), 5)

    def test_le_plus_petit_decalage_a_cout_egal(self):
        """Voix déjà dans la zone et près du centre : 0 ; décalage minimal quand plusieurs conviennent."""
        self.assertEqual(dt_de(voix(175, 190, 210)), 0)
        self.assertEqual(dt_de(voix(172, 196, 222)), 0)

    def test_zone_de_tessiture_mesuree(self):
        """Avec une zone mesurée plus haute, la même voix n'est plus transposée."""
        haut = [260.0, 340.0]
        self.assertLess(dt_de(voix(235, 262, 300)), 0)
        self.assertEqual(dt_de(voix(262, 300, 330, zone=haut)), 0)

    def test_duo_un_seul_decalage_pour_les_deux_voix(self):
        """Deux voix cibles (zones vers 190 et 380 Hz) : un seul décalage, celui qui minimise l'écart total aux deux zones."""
        z2 = [380 * 2 ** (-4 / 12), 380 * 2 ** (4 / 12)]
        a, b = voix(215, 250, 295), voix(300, 340, 400, zone=z2, nom='marie')
        r = N.choisir_transposition([a, b])
        P = N.TRANSPOSITION_DEFAUT

        def total(dt):
            return (sum(N.cout_transposition_voix(v, v['confort'], dt, P) for v in (a, b))
                    + P['poids_transposition'] * abs(dt))
        meilleur = min(range(-5, 6), key=lambda d: (round(total(d), 9), abs(d)))
        self.assertEqual(r['musique_dt'], meilleur)
        for d in range(-5, 6):
            self.assertLessEqual(total(r['musique_dt']), total(d) + 1e-9)
        self.assertEqual(len(r['voix']), 2)
        # sans la voix de marie, jules seul irait plus loin : le décalage du duo est un compromis
        self.assertNotEqual(dt_de(a), 0)

    def test_hauteurs_apres_et_decalage_entier(self):
        r = N.choisir_transposition([voix(222, 262, 310)])
        d = r['voix'][0]
        self.assertAlmostEqual(d['apres_hz']['p50'], 262 * 2 ** (r['musique_dt'] / 12), delta=0.1)
        self.assertEqual(d['avant_hz']['p50'], 262.0)


class OptionTransposer(unittest.TestCase):
    """--transposer auto|non|<N> vu du noyau : lire_transposer et decider_transposition."""

    def morceaux(self, f0_med=290.0, nb=8):
        m = Monde(5)
        segs = m.morceaux([(0, nb)], [m.chanteur('homme')], [f0_med], duree=6.0, voise=4.0)
        for s in segs:
            s['decalage'] = 0
        return segs

    def decider(self, demande, segs=None, mode='chant', zone=None):
        segs = segs or self.morceaux()
        v = [{'nom': 'jules', 'centre_hz': CENTRE, **({'zone': zone} if zone else {})}]
        return N.decider_transposition(segs, f0_des_morceaux(segs), v, demande, mode)

    def test_lecture(self):
        self.assertEqual([N.lire_transposer(x) for x in (None, '', 'auto', 'non', False, '-3', 4, '+2', 99, -99)],
                         ['auto', 'auto', 'auto', 'non', 'non', -3, 4, 2, 12, -12])
        self.assertEqual(N.lire_transposer('bof'), 'auto')

    def test_auto(self):
        r = self.decider('auto')
        self.assertEqual(r['demande'], 'auto')
        self.assertLess(r['musique_dt'], 0)
        self.assertGreaterEqual(r['musique_dt'], -5)
        self.assertTrue(r['applique'])
        self.assertIn('transposée', r['raison'])
        self.assertTrue(r['voix'][0]['dans_zone'])

    def test_non(self):
        r = self.decider('non')
        self.assertEqual((r['musique_dt'], r['applique']), (0, False))
        self.assertIn('--transposer non', r['raison'])
        self.assertEqual(r['voix'][0]['apres_hz'], r['voix'][0]['avant_hz'])

    def test_n_impose(self):
        for n in (-3, 2, 7):
            r = self.decider(n)
            self.assertEqual(r['musique_dt'], n)
            self.assertIn('imposée', r['raison'])
        self.assertEqual(self.decider(0)['applique'], False)

    def test_parole_jamais_transposee(self):
        for demande in ('auto', 4):
            r = self.decider(demande, mode='parole')
            self.assertEqual((r['musique_dt'], r['applique']), (0, False))
            self.assertIn('parole', r['raison'])

    def test_aucune_voix_chantee(self):
        segs = self.morceaux()
        for s in segs:
            s['muet'] = True
        r = self.decider('auto', segs)
        self.assertEqual(r['musique_dt'], 0)
        self.assertIn('aucune voix', r['raison'])

    def test_zone_envoyee_par_doubler(self):
        """tessiture.json (zone d'origine « tessiture ») ou, sans elle, centre de chant ±4 demi-tons (origine « parole »)."""
        r = self.decider('auto')
        self.assertEqual(r['zone_origine'], ['parole'])
        self.assertAlmostEqual(r['voix'][0]['zone_hz'][0], ZONE[0], delta=0.1)
        self.assertAlmostEqual(r['voix'][0]['zone_hz'][1], ZONE[1], delta=0.1)
        r = self.decider('auto', zone={'confort': [250.0, 340.0], 'origine': 'tessiture'})
        self.assertEqual(r['zone_origine'], ['tessiture'])
        self.assertEqual(r['voix'][0]['zone_hz'], [250.0, 340.0])
        self.assertEqual(r['musique_dt'], 0)   # la voix à 290 Hz est chez elle
        illisible = N.zone_de_voix({'zone': {'confort': ['a', 'b']}}, CENTRE)
        self.assertEqual(illisible['origine'], 'parole')
        self.assertEqual(N.zone_de_voix({'zone': {'confort': [300, 200]}}, CENTRE)['confort'], [200.0, 300.0])


class DecisionSurDesMorceaux(unittest.TestCase):
    """La décision en situation : octave d'abord (règle inchangée), puis musique_dt ajouté à chaque morceau et à tout ce que
    le noyau relit ensuite (modèle des pistes, décalages par voix, octave imposée)."""

    def test_voix_seule_octave_puis_transposition(self):
        m = Monde(11)
        segs = m.morceaux([(0, 12)], [m.chanteur('homme')], [300.0])
        f0 = f0_des_morceaux(segs)
        info = N.decider_chant(segs, reglages(m), CENTRE)
        octaves = {s['decalage'] for s in segs}
        self.assertEqual(len(octaves), 1)
        octave = octaves.pop()
        self.assertIn(octave, (-12, 0, 12))
        voix_t = [{'nom': 'jules', 'centre_hz': CENTRE}]
        rap = N.decider_transposition(segs, f0, voix_t, 'auto', 'chant')
        dt = rap['musique_dt']
        self.assertNotEqual(dt, 0)
        N.appliquer_transposition(segs, info, None, dt)
        self.assertEqual({s['decalage'] for s in segs}, {octave + dt})
        self.assertTrue(all(float(s['decalage']).is_integer() for s in segs))
        self.assertEqual(info['transposition_dt'], dt)
        self.assertEqual(info['voix_principale']['decalage'], octave + dt)
        for d in info['decalage_par_classe'].values():
            self.assertEqual(d % 12, dt % 12)
        # le décalage par voix du rapport : octave + transposition ; l'octave seule : le décalage moins musique_dt
        decs = N.decalages_par_voix_cible(segs, 1)
        self.assertEqual(decs, [octave + dt])
        # la voix convertie tombe là où la règle l'a annoncé
        f_conv = np.median(f0[f0 > 1]) * 2 ** ((octave + dt) / 12)
        self.assertAlmostEqual(f_conv, rap['voix'][0]['apres_hz']['p50'], delta=3.0)

    def test_octave_imposee_et_transposition_s_ajoutent(self):
        m = Monde(11)
        segs = m.morceaux([(0, 12)], [m.chanteur('homme')], [300.0])
        for s in segs:   # --octave -12 : la décision d'octave est remplacée avant la transposition
            s['decalage'], s['base'] = -12, 'imposé'
        f0 = f0_des_morceaux(segs)
        rap = N.decider_transposition(segs, f0, [{'nom': 'jules', 'centre_hz': CENTRE}], 'auto', 'chant')
        # 300 Hz - 12 = 150 Hz : trop bas pour 190 Hz, la chanson monte
        self.assertGreater(rap['musique_dt'], 0)
        N.appliquer_transposition(segs, {}, None, rap['musique_dt'])
        self.assertEqual({s['decalage'] for s in segs}, {-12 + rap['musique_dt']})
        # voix cibles : l'octave imposée est reprise à l'affectation des pistes, plus la transposition
        c = cibles()
        for s in segs:
            s['cible'] = 0
        plan = N.affecter_pistes(segs, ['voix'], {}, None, c, -12, None, rap['musique_dt'])
        self.assertEqual({a['decalage'] for a in plan['voix'].values()}, {-12 + rap['musique_dt']})
        self.assertEqual(N.decalages_par_voix({}, c, -12, rap['musique_dt']), [-12 + rap['musique_dt']] * 2)
        self.assertEqual(N.decalages_par_voix({}, c, None, 3), [3, 3])   # sans décision : la transposition seule

    def test_duo_un_seul_musique_dt_pour_les_deux_chanteurs(self):
        m = Monde(21)
        voix_o = [m.chanteur('homme'), m.chanteur('femme')]
        segs = m.morceaux([(k % 2, 1) for k in range(12)], voix_o, [250, 330], duree=4.0, voise=3.0)
        c = cibles(centres=(190.0, 380.0))
        info, modele = N.decider_chant_cibles(segs, reglages(m), c)
        self.assertEqual(info['voix'], 2, info.get('pourquoi_une_voix'))
        avant = list(modele['decalage'])
        avant_segs = {s['i']: s['decalage'] for s in segs}
        f0 = f0_des_morceaux(segs)
        voix_t = [{'nom': v['nom'], 'centre_hz': v['centre_hz']} for v in c['voix']]
        rap = N.decider_transposition(segs, f0, voix_t, 'auto', 'chant')
        dt = rap['musique_dt']
        self.assertEqual(len(rap['voix']), 2)
        self.assertLessEqual(abs(dt), 5)
        N.appliquer_transposition(segs, info, modele, dt)
        # le même dt pour les deux chanteurs, ajouté à l'octave de chacun
        self.assertEqual(modele['decalage'], [d + dt for d in avant])
        for s in segs:
            self.assertEqual(s['decalage'], avant_segs[s['i']] + dt)
        self.assertEqual(N.decalages_par_voix(info, c), [d + dt for d in avant])
        self.assertTrue(all(isinstance(d, int) for d in modele['decalage']))
        # et les chanteurs du rapport de décision portent le décalage final
        for ch in info['chanteurs']:
            self.assertIn(ch['decalage'], modele['decalage'])

    def test_rapport_complet_par_voix(self):
        m = Monde(11)
        segs = m.morceaux([(0, 12)], [m.chanteur('homme')], [300.0])
        f0 = f0_des_morceaux(segs)
        info = N.decider_chant(segs, reglages(m), CENTRE)
        rap = N.decider_transposition(segs, f0, [{'nom': 'jules', 'centre_hz': CENTRE}], 'auto', 'chant')
        N.appliquer_transposition(segs, info, None, rap['musique_dt'])
        decs = N.decalages_par_voix_cible(segs, 1)
        for cle in ('demande', 'mode', 'musique_dt', 'applique', 'zone_origine', 'voix', 'raison', 'max_dt'):
            self.assertIn(cle, rap)
        v = rap['voix'][0]
        for cle in ('voix', 'zone_hz', 'avant_hz', 'apres_hz', 'dans_zone', 'ecart_zone_dt'):
            self.assertIn(cle, v)
        self.assertEqual(rap['max_dt'], 5)
        self.assertEqual(decs[0] - rap['musique_dt'] in (-12, 0, 12), True)


if __name__ == '__main__':
    unittest.main()
