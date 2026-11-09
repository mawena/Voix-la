"""Comparer des voix, côté noyau (chaîne entière de main(), sans GPU ni modèle ni réseau : les mêmes imitations que
test_plusieurs_voix_chaine.py).

Le même extrait est converti dans plusieurs voix en une seule session : la voix isolée est séparée une fois, nettoyée une
fois, transcrite une fois ; chaque voix a son analyse (l'octave dépend de la voix), sa conversion (YingMusic-SVC comme
Seed-VC, une et deux cartes) et son sous-dossier de sortie <vidéo>/<voix>/. Chaque sortie doit être, au bit près, celle
d'un doublage à cette voix seule : comparer ne change rien au rendu, il ne fait que partager les frais fixes. Voix
inventées (voixa, voixb, voixc) ; la note témoin des faux moteurs dit quelle référence a servi."""
import json
import unittest

import numpy as np

import test_plusieurs_voix_chaine as C
from commun import SR, Dossier, charger_noyau

N = charger_noyau()
C.VOIX.update({'voixa': (120, 24.0, 110.0), 'voixb': (230, 22.0, 210.0), 'voixc': (170, 23.0, 150.0)})
VOIX = ('voixa', 'voixb', 'voixc')


def voix_noyau(nom):
    return {'nom': nom, 'fichier': f'voix-{nom}.wav', 'f0_parole_hz': C.VOIX[nom][2], 'facteur_chant': 2.0}


def reglages(noms, moteur='seedvc', mode='chant', paroles=False):
    """Réglages du noyau pour une voix (noms à un élément : comme avant) ou plusieurs voix à comparer."""
    r = C.reglages(C.Timbres(), moteur)
    r['voix'] = voix_noyau(noms[0])
    r['paroles'] = paroles
    r['travaux'][0]['mode'] = mode
    if len(noms) > 1:
        r['comparer'] = {'voix': [{**voix_noyau(n), 'affichage': n} for n in noms]}
    return r


def temoin_db(y, nom, a=1.0, b=9.0):
    """Énergie (dB) de la note témoin de la voix nom (2000 Hz + 10 x la hauteur de sa référence) dans y entre a et b s."""
    seg = np.asarray(y[int(a * SR):int(b * SR)], np.float64)
    s = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    c = 2000 + 10 * C.VOIX[nom][0]
    return 10 * np.log10(s[(f > c - 60) & (f < c + 60)].sum() / (s.sum() + 1e-20) + 1e-20)


def lire(dossier, nom):
    y = C.lire_son(dossier / nom / 'voix_convertie.flac')[0]
    return y, json.loads((dossier / nom / 'rapport.json').read_text()), json.loads((dossier / nom / 'courbes.json').read_text())


def lancer(R, **k):
    with Dossier() as d:
        compteurs = {}
        sortie = C.lancer_noyau(d, R, compteurs=compteurs, **k)
        session = json.loads((sortie.parent / 'session.json').read_text())
        contenu = sorted(str(p.relative_to(sortie)) for p in sortie.rglob('*') if p.is_file())
        res = {'compteurs': compteurs, 'session': session, 'contenu': contenu}
        if len(R.get('comparer', {}).get('voix', ())) > 1:
            res['voix'] = {n: lire(sortie, n) for n in (v['nom'] for v in R['comparer']['voix'])}
            res['paroles'] = {n: json.loads((sortie / n / 'paroles.json').read_text()) for n in res['voix']
                              if (sortie / n / 'paroles.json').exists()}
            res['source'] = (sortie / 'voix_source.wav').exists()
        else:
            res['voix'] = {R['voix']['nom']: (C.lire_son(sortie / 'voix_convertie.flac')[0],
                                              json.loads((sortie / 'rapport.json').read_text()),
                                              json.loads((sortie / 'courbes.json').read_text()))}
        return res


class ComparerEnChant(unittest.TestCase):
    """Trois voix, une session, la chanson de synthèse de test_plusieurs_voix_chaine (80 s, chanteur puis chanteuse)."""

    @classmethod
    def setUpClass(cls):
        cls.comparaison, cls.seules = {}, {}
        for moteur in ('seedvc', 'yingmusic'):
            cls.comparaison[moteur] = lancer(reglages(VOIX, moteur, paroles=True))
            cls.seules[moteur] = {n: lancer(reglages((n,), moteur))['voix'][n] for n in VOIX}
        cls.deux_cartes = lancer(reglages(VOIX, 'yingmusic'), cartes=2)

    def test_une_sortie_par_voix_et_frais_fixes_une_fois(self):
        for moteur, res in self.comparaison.items():
            with self.subTest(moteur=moteur):
                self.assertEqual(res['compteurs'], {'separation': 1, 'nettoyage': 1, 'paroles': 1},
                                 'séparation, nettoyage et paroles une seule fois pour les trois voix')
                self.assertTrue(res['source'], 'la voix isolée, commune, reste à la racine de la vidéo')
                for n in VOIX:
                    for f in ('voix_convertie.flac', 'rapport.json', 'courbes.json', 'paroles.json'):
                        self.assertIn(f'{n}/{f}', res['contenu'])
                    self.assertNotIn(f'{n}/voix_source.wav', res['contenu'])
                self.assertNotIn('rapport.json', res['contenu'], 'pas de rapport commun : un par voix')

    def test_chaque_voix_a_son_timbre(self):
        for moteur, res in self.comparaison.items():
            for n, (y, rapport, _) in res['voix'].items():
                with self.subTest(moteur=moteur, voix=n):
                    propre = temoin_db(y, n)
                    self.assertGreater(propre, -30)
                    for autre in VOIX:
                        if autre != n:
                            self.assertLess(temoin_db(y, autre), propre - 25, f'{autre} entendue dans {n}')
                    self.assertEqual(rapport['voix_cible']['nom'], n)
                    self.assertEqual(rapport['moteur'], moteur)
                    self.assertEqual(rapport['id'], f'v1~{VOIX.index(n)}')
                    self.assertEqual(rapport['travail']['dossier'].rsplit('/', 1)[-1], n)

    def test_chaque_sortie_est_celle_d_un_doublage_a_cette_voix(self):
        for moteur, res in self.comparaison.items():
            for n in VOIX:
                with self.subTest(moteur=moteur, voix=n):
                    y, rapport, courbes = res['voix'][n]
                    y1, rapport1, courbes1 = self.seules[moteur][n]
                    self.assertTrue(np.array_equal(y, y1), 'voix convertie identique au bit près')
                    self.assertEqual(courbes, courbes1)
                    for cle in ('morceaux', 'decision_hauteur', 'frise', 'mode', 'nettoyage', 'secours', 'modele',
                                'coupes_dans_le_son_s'):
                        self.assertEqual(C.sans_durees(rapport.get(cle)), C.sans_durees(rapport1.get(cle)), cle)

    def test_l_octave_se_decide_pour_chaque_voix(self):
        res = self.comparaison['seedvc']['voix']
        decalages = {n: sorted({m['decalage'] for m in res[n][1]['morceaux'] if not m['muet']}) for n in VOIX}
        seules = {n: sorted({m['decalage'] for m in self.seules['seedvc'][n][1]['morceaux'] if not m['muet']}) for n in VOIX}
        self.assertEqual(decalages, seules)

    def test_deux_cartes_meme_resultat_qu_une(self):
        for n in VOIX:
            with self.subTest(voix=n):
                self.assertTrue(np.array_equal(self.deux_cartes['voix'][n][0], self.comparaison['yingmusic']['voix'][n][0]))
        cartes = self.deux_cartes['session']['yingmusic'].get('cartes') or []
        self.assertEqual(len(cartes), 2, 'la conversion des voix est répartie sur les deux cartes')
        self.assertEqual(self.deux_cartes['compteurs']['separation'], 1)

    def test_paroles_partagees(self):
        res = self.comparaison['seedvc']
        self.assertEqual(set(res['paroles']), set(VOIX))
        self.assertTrue(all(p == res['paroles']['voixa'] for p in res['paroles'].values()))
        paroles = [res['voix'][n][1]['paroles'] for n in VOIX]
        self.assertTrue(all(p == paroles[0] for p in paroles), 'même bilan des paroles dans chaque rapport')
        self.assertEqual(paroles[0]['phrases'] > 0, True)

    def test_session(self):
        s = self.comparaison['seedvc']['session']
        self.assertEqual([v['nom'] for v in s['comparer']['voix']], list(VOIX))
        self.assertEqual(s['voix']['nom'], 'voixa', 'la première voix est celle de l\'analyse')
        self.assertTrue(all(v['centre_chant_hz'] > 0 for v in s['comparer']['voix']))
        self.assertNotIn('voix_cibles', s, 'pas une voix par chanteur')


class ComparerEnParole(unittest.TestCase):
    def test_chaque_voix_a_son_timbre_en_parole(self):
        res = lancer(reglages(('voixa', 'voixb'), mode='parole'))
        self.assertEqual(res['compteurs'].get('nettoyage', 0), 0, 'pas de nettoyage en parole')
        for n, (y, rapport, _) in res['voix'].items():
            with self.subTest(voix=n):
                self.assertEqual(rapport['mode'], 'parole')
                autre = 'voixb' if n == 'voixa' else 'voixa'
                self.assertGreater(temoin_db(y, n), temoin_db(y, autre) + 20)


class SansComparer(unittest.TestCase):
    def test_une_seule_voix_comme_avant(self):
        res = lancer(reglages(('voixa',)))
        self.assertIn('voix_convertie.flac', res['contenu'])
        self.assertIn('rapport.json', res['contenu'])
        self.assertNotIn('comparer', res['session'])
        self.assertEqual(res['voix']['voixa'][1]['id'], 'v1')

    def test_lecture_des_reglages(self):
        self.assertIsNone(N.lire_comparees({}))
        self.assertIsNone(N.lire_comparees({'comparer': {'voix': [voix_noyau('voixa')]}}), 'une seule : pas de comparaison')
        doublon = {'comparer': {'voix': [voix_noyau('voixa'), voix_noyau('voixa')]}}
        self.assertIsNone(N.lire_comparees(doublon))
        v = N.lire_comparees({'comparer': {'voix': [voix_noyau(n) for n in VOIX] + [{'nom': 'sans-fichier'}]}})
        self.assertEqual([x['nom'] for x in v], list(VOIX))


if __name__ == '__main__':
    unittest.main()
