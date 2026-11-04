"""Paroles, côté noyau, sans GPU : phrases à transcrire, langue par passage, morceaux, garde-fous contre les
hallucinations (mots hors du chant, boucles inventées, génériques de sous-titres), lignes, contenu de paroles.json.
Textes inventés pour l'essai."""
import unittest

import numpy as np

from commun import charger_noyau

N = charger_noyau()


def mots(texte, debut=0.0, pas=0.4, langue='es'):
    out = []
    for i, m in enumerate(texte.split()):
        out.append({'debut': round(debut + i * pas, 3), 'fin': round(debut + i * pas + pas * 0.9, 3), 'mot': m,
                    'brut': ' ' + m, 'langue': langue})
    return out


class Zones(unittest.TestCase):
    def piste(self, chantees, duree=60.0, hauteur=True):
        k = int(duree * 100) + 1
        lisse = np.full(k, -80.0)
        f0 = np.zeros(k - 1)
        for a, b in chantees:
            lisse[int(a * 100):int(b * 100)] = -20
            if hauteur:
                f0[int(a * 100):int(b * 100)] = 220
        return lisse, f0

    def test_phrases_proches_fusionnees_et_marges(self):
        lisse, f0 = self.piste([(10, 12), (12.3, 14), (20, 22)])
        phrases, chant = N.zones_a_transcrire(lisse, -60, f0)
        self.assertEqual(len(phrases), 2)
        self.assertAlmostEqual(phrases[0][0], 10 - 0.25, delta=0.02)
        self.assertAlmostEqual(phrases[0][1], 14 + 0.25, delta=0.02)
        self.assertEqual(len(chant), 3)   # le chant brut garde ses trous, pour juger les mots

    def test_silence_et_bruit_sans_hauteur(self):
        lisse, f0 = self.piste([(5, 9)], hauteur=False)
        self.assertEqual(N.zones_a_transcrire(lisse, -60, f0), ([], []))
        lisse, f0 = self.piste([])
        self.assertEqual(N.zones_a_transcrire(lisse, -60, f0), ([], []))

    def test_phrase_trop_courte_ignoree(self):
        lisse, f0 = self.piste([(5, 5.2), (20, 23)])
        phrases, _ = N.zones_a_transcrire(lisse, -60, f0)
        self.assertEqual(len(phrases), 1)

    def test_longue_phrase_coupee_a_25_s(self):
        lisse, f0 = self.piste([(1, 59)], duree=60)
        lisse[2200] = -45   # un creux : la coupe y tombe (fenêtre permise : 15,75 à 25,75 s)
        phrases, _ = N.zones_a_transcrire(lisse, -60, f0)
        self.assertGreaterEqual(len(phrases), 3)
        self.assertTrue(all(b - a <= 25.0 + 1e-6 for a, b in phrases), phrases)
        self.assertAlmostEqual(phrases[0][1], 22.0, delta=0.02)
        self.assertEqual(phrases[0][0], 0.75)
        for (a1, b1), (a2, b2) in zip(phrases, phrases[1:]):
            self.assertEqual(b1, a2)

    def test_f0_plus_court_que_l_enveloppe(self):
        lisse, f0 = self.piste([(1, 3)], duree=5)
        phrases, _ = N.zones_a_transcrire(lisse, -60, f0[:150])
        self.assertEqual(len(phrases), 1)


class Langues(unittest.TestCase):
    def test_phrase_peu_sure_ou_courte_prend_la_voisine(self):
        self.assertEqual(N.lisser_langues([('es', 0.9, 3), ('fr', 0.3, 2), ('es', 0.95, 4)]), ['es', 'es', 'es'])
        self.assertEqual(N.lisser_langues([('es', 0.9, 3), ('en', 0.9, 1.0), ('fr', 0.95, 4)]), ['es', 'es', 'fr'])
        self.assertEqual(N.lisser_langues([('es', 0.9, 3), ('fr', 0.9, 3)]), ['es', 'fr'])   # un duo bilingue

    def test_aucune_sure_ou_inconnue(self):
        self.assertEqual(N.lisser_langues([('es', 0.4, 3), ('fr', 0.2, 1)]), ['es', 'fr'])
        self.assertEqual(N.lisser_langues([(None, 0.0, 0.5), ('fr', 0.9, 3)]), ['fr', 'fr'])
        self.assertEqual(N.lisser_langues([]), [])

    def test_morceaux_par_langue_pause_et_longueur(self):
        phrases = [[0, 5], [5.5, 9], [9.5, 12], [20, 24], [24.5, 46]]
        m = N.grouper_morceaux(phrases, ['es', 'es', 'fr', 'fr', 'fr'])
        self.assertEqual([(a, b, lg, i) for a, b, lg, i in m],
                         [(0, 9, 'es', [0, 1]), (9.5, 12, 'fr', [2]), (20, 24, 'fr', [3]), (24.5, 46, 'fr', [4])])


class GardeFous(unittest.TestCase):
    def test_mots_hors_du_chant_retires(self):
        m = mots('uno dos tres cuatro', 0.0, 1.0)
        garde, compte = N.nettoyer_mots(m, [[0.0, 2.0]])
        self.assertEqual([x['mot'] for x in garde], ['uno', 'dos'])
        self.assertEqual(compte['hors_chant'], 2)

    def test_boucle_inventee_retiree_refrain_chante_garde(self):
        rapide = mots('el gato ' * 6, 0.0, 0.05)              # 12 mots en 0,6 s : impossible à chanter
        garde, n = N.retirer_boucles(rapide)
        self.assertEqual([x['mot'] for x in garde], ['el', 'gato'])
        self.assertEqual(n, 10)
        chante = mots('no no no', 0.0, 0.5)
        self.assertEqual(N.retirer_boucles(chante)[0], chante)
        vers = mots('la luna baila ' * 3, 0.0, 0.4)
        self.assertEqual(len(N.retirer_boucles(vers)[0]), 9)

    def test_boucle_retiree_d_office_si_texte_trop_repetitif(self):
        texte = ' '.join(['la luna baila'] * 12)
        self.assertGreater(N.taux_compression(texte), 2.4)
        m = mots(texte, 0.0, 0.4)
        garde, compte = N.nettoyer_mots(m, [[0.0, 100.0]], texte)
        self.assertEqual([x['mot'] for x in garde], ['la', 'luna', 'baila'])
        self.assertEqual(compte['boucles'], 33)
        self.assertLess(N.taux_compression('el gato come pan en la cocina de la casa azul'), 2.4)
        self.assertEqual(N.taux_compression(''), 0.0)

    def test_generiques_et_musique_seule_retires_doublons_fusionnes(self):
        lignes = [{'debut': 0, 'fin': 2, 'texte': "Sous-titres réalisés par la communauté d'Amara.org", 'mots': []},
                  {'debut': 2, 'fin': 3, 'texte': '♪', 'mots': []},
                  {'debut': 3, 'fin': 5, 'texte': 'El gato come pan.', 'mots': []},
                  {'debut': 4.5, 'fin': 6, 'texte': 'el gato come pan', 'mots': []},
                  {'debut': 7, 'fin': 8, 'texte': '¡Suscríbete al canal!', 'mots': []},
                  {'debut': 9, 'fin': 10, 'texte': 'Thanks for watching!', 'mots': []},
                  {'debut': 11, 'fin': 12, 'texte': 'El gato come pan.', 'mots': []}]
        out, n = N.filtrer_lignes(lignes)
        self.assertEqual([(l['debut'], l['fin'], l['texte']) for l in out],
                         [(3, 6, 'El gato come pan.'), (11, 12, 'El gato come pan.')])   # la reprise plus loin reste
        self.assertEqual(n, 5)


class Lignes(unittest.TestCase):
    def test_coupures(self):
        m = mots('el gato come pan.', 0.0, 0.4) + mots('la luna baila', 2.0, 0.4) + mots('sur le toit', 3.4, 0.4, 'fr')
        lignes = N.lignes_de_mots(m)
        self.assertEqual([l['texte'] for l in lignes], ['el gato come pan.', 'la luna baila', 'sur le toit'])
        self.assertEqual([l['langue'] for l in lignes], ['es', 'es', 'fr'])
        self.assertEqual(lignes[0]['debut'], 0.0)
        self.assertEqual(lignes[0]['fin'], 1.56)
        self.assertEqual(lignes[0]['mots'][0], {'debut': 0.0, 'fin': 0.36, 'mot': 'el'})

    def test_ligne_trop_longue_coupee(self):
        m = mots(' '.join(['palabra'] * 20), 0.0, 0.3)
        lignes = N.lignes_de_mots(m)
        self.assertGreater(len(lignes), 1)
        self.assertTrue(all(len(l['texte']) <= 50 for l in lignes))

    def test_sans_espaces_et_proportionnels(self):
        m = N.mots_proportionnels(' el gato come', 1.0, 2.0)
        self.assertEqual([x['mot'] for x in m], ['el', 'gato', 'come'])
        self.assertAlmostEqual(m[0]['debut'], 1.0)
        self.assertAlmostEqual(m[-1]['fin'], 2.0)
        self.assertEqual(N.mots_proportionnels('', 0, 1), [])
        jp = [{'debut': 0, 'fin': 0.3, 'mot': '猫', 'brut': '猫'}, {'debut': 0.3, 'fin': 0.6, 'mot': 'が', 'brut': 'が'}]
        self.assertEqual(N.lignes_de_mots(jp)[0]['texte'], '猫が')


class Contrat(unittest.TestCase):
    def test_paroles_json(self):
        lignes = [{'debut': 64.5, 'fin': 66.0, 'texte': 'el gato come pan', 'langue': 'es',
                   'mots': [{'debut': 64.5, 'fin': 64.8, 'mot': 'el'}]},
                  {'debut': 67.0, 'fin': 69.0, 'texte': 'la lune danse', 'langue': 'fr', 'mots': []},
                  {'debut': 70.0, 'fin': 75.0, 'texte': 'la luna baila', 'langue': 'es', 'mots': []}]
        d = N.paroles_json(lignes, ['le chat mange du pain', 'la lune danse', 'la lune danse'], piste='lead_sec')
        self.assertEqual(d['format'], 1)
        self.assertEqual(d['langue'], 'es')
        self.assertEqual(d['source'], 'whisper-large-v3')
        self.assertEqual(d['segments'][0]['mots'][0], {'debut': 64.5, 'fin': 64.8, 'mot': 'el'})
        fr = d['traductions']['fr']
        self.assertEqual(fr['source'], 'nllb-200-distilled-600M')
        self.assertEqual([(s['debut'], s['fin']) for s in fr['segments']], [(64.5, 66.0), (67.0, 69.0), (70.0, 75.0)])
        self.assertEqual(fr['segments'][0]['texte'], 'le chat mange du pain')
        self.assertEqual(d['decalage_s'], 0.0)
        vide = N.paroles_json([])
        self.assertEqual((vide['langue'], vide['segments'], vide['traductions']), (None, [], {}))

    def test_codes_nllb(self):
        for lg in ('es', 'en', 'pt', 'it', 'de', 'fr'):
            self.assertIn(lg, N.NLLB)
        self.assertEqual(N.NLLB['es'], 'spa_Latn')
        self.assertEqual(N.NLLB['fr'], 'fra_Latn')


if __name__ == '__main__':
    unittest.main()
