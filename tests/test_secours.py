"""Passages perdus au nettoyage (noyau : zones_perdues, decider_secours, porte_zones, morceaux_piste, gain_secours), sur
des signaux de synthèse : une voix principale en trois phrases, chacune suivie d'une traîne de réverbération, des chœurs,
un « oooh » de fond que le nettoyage a jeté, un souffle (bruit sans hauteur) et une note trop brève, perdus eux aussi.
La chaîne de détection est celle de preparer_pistes : enveloppes, seuils et portes de chaque piste nettoyée."""
import unittest

import numpy as np

from commun import SR, charger_noyau, note

N = charger_noyau()
DUREE = 15.0
PHRASES = [(1.0, 3.0), (4.0, 6.0), (9.6, 12.0)]
OOOH = (7.2, 9.0)
SOUFFLE = (13.6, 14.4)
BREVE = (0.2, 0.4)


def scene(perdre_oooh=True):
    """(voix complète, voix principale sèche, chœurs secs, f0 par trame de 10 ms)."""
    n = int(DUREE * SR)
    lead = sum(note(220, a, b, -20, DUREE) for a, b in PHRASES)
    traines = np.zeros(n)
    for _, b in PHRASES:   # réverbération de la dernière note : -25 dB/s (grande salle), voisée (cas le plus dur)
        t = np.arange(int(1.5 * SR)) / SR
        y = note(220, 0, 1.5, -22, 1.5, fondu=0.001)[:len(t)] * 10 ** (-25 * t / 20)
        traines[int(b * SR):int(b * SR) + len(y)] += y[:n - int(b * SR)]
    choeurs = note(330, 4.0, 6.0, -32, DUREE)
    oooh = note(262, *OOOH, -22, DUREE)
    rng = np.random.default_rng(3)
    souffle = np.zeros(n)
    a, b = int(SOUFFLE[0] * SR), int(SOUFFLE[1] * SR)
    souffle[a:b] = rng.standard_normal(b - a) * 10 ** (-24 / 20)
    breve = note(262, *BREVE, -22, DUREE)
    voix = lead + traines + choeurs + oooh + souffle + breve
    lead_sec = lead + (0 if perdre_oooh else oooh)
    k = n // N.PAS + 1
    t = np.arange(k) * 0.01
    f0 = np.zeros(k)
    for a, b in PHRASES:
        f0[(t >= a) & (t < b + 1.0)] = 220      # la traîne garde la hauteur de la note
    f0[(t >= OOOH[0]) & (t < OOOH[1])] = 262
    f0[(t >= BREVE[0]) & (t < BREVE[1])] = 262
    return voix, lead_sec, choeurs, f0[:n // N.PAS]   # RMVPE rend une trame de moins que l'enveloppe


def detecter(voix, nettes, f0, reglages=None):
    env = N.enveloppe_db(voix)
    lisse, seuil = N.lisser_db(env), N.seuil_silence(env)
    pistes = []
    for x in nettes:
        e = N.enveloppe_db(x)
        lt, st = N.lisser_db(e), N.seuil_silence(e) if np.any(x) else -60.0
        pistes.append((lt, N.porte(lt, st, len(x))[1]))
    zones, info = N.zones_perdues(env, lisse, seuil, f0, pistes, reglages)
    active = float((lisse > seuil).sum()) * 0.01
    return zones, info, N.decider_secours(zones, active, reglages)


class Detection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.voix, cls.lead, cls.choeurs, cls.f0 = scene()
        cls.zones, cls.info, cls.decision = detecter(cls.voix, [cls.lead, cls.choeurs], cls.f0)

    def test_le_oooh_perdu_est_retrouve(self):
        self.assertEqual(self.decision[0], 'secours')
        self.assertEqual(len(self.zones), 1, self.zones)
        z = self.zones[0]
        self.assertAlmostEqual(z['debut_s'], OOOH[0], delta=0.15)
        self.assertAlmostEqual(z['fin_s'], OOOH[1], delta=0.2)
        self.assertGreater(z['ecart_db'], 30)
        self.assertGreater(z['voise'], 0.9)
        self.assertAlmostEqual(z['f0_hz'], 262, delta=1)
        self.assertAlmostEqual(z['niveau_voix_dbfs'], -22, delta=2)
        for cle in ('debut_s', 'fin_s', 'duree_s', 'ecart_db', 'ouvert', 'repris_s'):
            self.assertIn(cle, z)

    def test_traines_souffle_et_note_breve_ecartes(self):
        raisons = [z['raison'] for z in self.info['ecartees']]
        self.assertTrue(any('traîne' in r for r in raisons), raisons)
        self.assertTrue(any('voisée' in r for r in raisons), raisons)
        self.assertTrue(any('courte' in r for r in raisons), raisons)
        for z in self.zones:   # rien de gardé sur les phrases chantées ni sur leurs traînes
            for a, b in PHRASES:
                self.assertFalse(z['debut_s'] < b + 1.0 and z['fin_s'] > a, z)

    def test_ecart_nul_rien_ne_change(self):
        """Les pistes nettoyées contiennent tout : aucune zone, aucune piste de secours (le mix ne change pas)."""
        voix, lead, choeurs, f0 = scene(perdre_oooh=False)
        nette = voix - choeurs   # tout ce qui n'est pas dans les chœurs est dans la voix principale
        zones, _, (decision, secondes) = detecter(voix, [nette, choeurs], f0)
        self.assertEqual(zones, [])
        self.assertEqual((decision, secondes), ('aucun', 0.0))

    def test_nettoyage_rate_voix_complete(self):
        """Pistes nettoyées muettes : tout serait repris, la voix complète est convertie seule (chaîne d'avant)."""
        zones, _, (decision, _) = detecter(self.voix, [np.zeros_like(self.voix), np.zeros_like(self.voix)], self.f0)
        self.assertTrue(zones)
        self.assertEqual(decision, 'voix_complete')

    def test_seuils_reglables(self):
        zones, info, _ = detecter(self.voix, [self.lead, self.choeurs], self.f0, {'duree_min_s': 5.0})
        self.assertEqual(zones, [])
        self.assertEqual(info['reglages']['duree_min_s'], 5.0)

    def test_pas_de_doublon_dans_un_passage(self):
        """Un passage perdu coupé par une syllabe que la voix principale a gardée : une seule zone, porte fermée sur la
        syllabe."""
        n = int(DUREE * SR)
        voix = note(262, 7.0, 9.0, -22, DUREE)
        lead = voix.copy()
        lead[:int(7.9 * SR)] = 0
        lead[int(8.15 * SR):] = 0      # 0,25 s gardées au milieu
        f0 = np.where((np.arange(n // N.PAS) * 0.01 >= 7.0) & (np.arange(n // N.PAS) * 0.01 < 9.0), 262.0, 0.0)
        voix = voix + note(220, 1, 3, -20, DUREE)   # une phrase ailleurs, pour un haut d'enveloppe réaliste
        lead = lead + note(220, 1, 3, -20, DUREE)
        zones, _, _ = detecter(voix, [lead], f0)
        self.assertEqual(len(zones), 1, zones)
        ouvert = zones[0]['ouvert']
        self.assertEqual(len(ouvert), 2, ouvert)
        self.assertLessEqual(ouvert[0][1], 7.95)
        self.assertGreaterEqual(ouvert[1][0], 8.1)
        g, trames = N.porte_zones(zones, n)
        self.assertLess(g[int(8.03 * SR)], 0.01)
        self.assertGreater(g[int(7.5 * SR)], 0.99)
        self.assertGreater(g[int(8.6 * SR)], 0.99)

    def test_f0_plus_long_ou_vide(self):
        voix, lead, choeurs, f0 = scene()
        z1, _, _ = detecter(voix, [lead, choeurs], np.concatenate([f0, np.zeros(500)]))
        self.assertEqual(len(z1), 1)
        z2, info, _ = detecter(voix, [lead, choeurs], np.zeros(0))
        self.assertEqual(z2, [])   # sans hauteur, rien de voisé : rien n'est repris
        self.assertTrue(all('voisée' in z['raison'] or 'courte' in z['raison'] for z in info['ecartees']))

    def test_voix_muette(self):
        x = np.zeros(int(3 * SR))
        zones, info, (decision, _) = detecter(x, [x], np.zeros(len(x) // N.PAS))
        self.assertEqual((zones, decision), ([], 'aucun'))


class Porte(unittest.TestCase):
    def test_porte_des_zones(self):
        n = int(10 * SR)
        zones = [{'debut_s': 2.0, 'fin_s': 4.0, 'duree_s': 2.0}, {'debut_s': 9.95, 'fin_s': 10.0, 'duree_s': 0.05}]
        g, trames = N.porte_zones(zones, n)
        self.assertEqual(len(g), n)
        self.assertEqual(len(trames), len(g[::N.PAS]))
        self.assertTrue(np.all((g >= 0) & (g <= 1)))
        self.assertEqual(float(g[int(1.0 * SR)]), 0.0)
        self.assertEqual(float(g[int(3.0 * SR)]), 1.0)
        self.assertAlmostEqual(float(g[int(2.0 * SR)]), 0.5, delta=0.02)   # fondu centré sur le bord
        montee = g[int(1.9 * SR):int(2.1 * SR)]
        self.assertTrue(np.all(np.diff(montee) >= -1e-7))
        self.assertEqual(float(g[int(4.1 * SR)]), 0.0)

    def test_morceaux_de_la_piste_de_secours(self):
        segs = [{'i': i, 'debut': int(a * SR), 'fin': int(b * SR), 'muet': m, 'decalage': d}
                for i, (a, b, m, d) in enumerate([(0, 3, False, 0), (3, 6, False, -12), (6, 8, True, 0), (8, 12, False, 0)])]
        fondus = {int(3 * SR): 100, int(6 * SR): 200}
        secours = {'nom': 'secours', 'zones': [{'debut_s': 5.0, 'fin_s': 5.5}]}
        m = N.morceaux_piste(segs, secours, fondus)
        self.assertEqual([x[0] for x in m], [1])
        self.assertEqual(m[0][3], -12)                       # même décalage que la voix complète
        self.assertEqual((m[0][4], m[0][5]), (100, 200))     # mêmes fondus aux mêmes coupes
        voix = {'nom': 'voix'}
        self.assertEqual([x[0] for x in N.morceaux_piste(segs, voix, fondus)], [0, 1, 3])
        lisse = np.full(12 * 100 + 1, -80.0)
        lisse[0:300] = -20
        nette = {'nom': 'lead_sec', 'lisse': lisse, 'seuil': -60.0}
        self.assertEqual([x[0] for x in N.morceaux_piste(segs, nette, fondus)], [0])
        bord = {'nom': 'secours', 'zones': [{'debut_s': 6.0 + 0.05, 'fin_s': 7.9}]}   # touche le morceau 1 par son fondu
        self.assertEqual([x[0] for x in N.morceaux_piste(segs, bord, fondus)], [1])

    def test_gain_de_la_piste_de_secours(self):
        n = SR * 2
        x = np.sin(np.arange(n) / 10)
        gain = np.zeros(n)
        gain[SR // 2:SR] = 1
        self.assertAlmostEqual(N.gain_secours(x, 0.5 * x * gain, gain), 2.0, places=3)
        self.assertAlmostEqual(N.gain_secours(x, 1e-4 * x * gain, gain), 10.0, places=3)   # borné à +20 dB
        self.assertEqual(N.gain_secours(x, x, np.zeros(n)), 1.0)
        self.assertEqual(N.gain_secours(x, np.zeros(n), gain), 1.0)


class OutilLocal(unittest.TestCase):
    """kaggle/secours_local.py : la somme des pistes nettoyées reconstituée d'après la voix convertie sèche d'un doublage
    déjà fait (muette là où rien n'a été converti) retrouve le passage perdu."""

    def test_reconstitution_d_apres_la_voix_convertie(self):
        import importlib.util
        from commun import DEPOT
        spec = importlib.util.spec_from_file_location('secours_local', DEPOT / 'kaggle' / 'secours_local.py')
        S = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(S)
        voix, lead, choeurs, f0 = scene()
        seche = 0.5 * (lead + choeurs)            # la voix convertie : 6 dB plus bas, rien sur le « oooh »
        x, nette, decal = S.reconstituer(N, voix, seche)
        self.assertAlmostEqual(decal, 6.0, delta=1.5)
        zones, info, decision, secondes, active = S.detecter(N, x, nette, f0)
        self.assertEqual(decision, 'secours')
        self.assertTrue(any(abs(z['debut_s'] - OOOH[0]) < 0.2 and abs(z['fin_s'] - OOOH[1]) < 0.25 for z in zones), zones)


class Suites(unittest.TestCase):
    def test_suites(self):
        self.assertEqual(N.suites([]), [])
        self.assertEqual(N.suites([0, 1, 1, 0, 1]), [(1, 3), (4, 5)])
        self.assertEqual(N.suites([1, 1]), [(0, 2)])

    def test_niveau_nettoye_somme_des_puissances(self):
        a = np.full(10, -20.0)
        ln = N.niveau_nettoye_db([(a, np.ones(10)), (a, np.ones(10))], 10)
        self.assertTrue(np.allclose(ln, -20 + 10 * np.log10(2), atol=1e-6))
        ferme = N.niveau_nettoye_db([(a, np.zeros(10))], 12)   # porte fermée, piste plus courte
        self.assertTrue(np.all(ferme < -100))


if __name__ == '__main__':
    unittest.main()
