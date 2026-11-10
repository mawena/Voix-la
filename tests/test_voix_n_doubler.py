"""De deux à six voix (côté doubler.py) : syntaxe de --voix (chanteurN, personneN), voix combinée, noms des sorties,
réglages envoyés au noyau, versions par défaut avec plus de deux voix, estimation de durée, choix « a+b » d'une
correction. Voix inventées (jules, marie, luc, lea)."""
import argparse
import contextlib
import io
import json
import unittest

from commun import Dossier, charger_doubler

with Dossier() as _h:
    D0 = charger_doubler(_h)


def ajouter_voix(D, nom, affichage, f0):
    d = D.VOIX / nom
    d.mkdir(parents=True, exist_ok=True)
    (d / 'reference.wav').write_bytes(b'RIFF' + nom.encode())
    (d / 'voix.json').write_text(json.dumps({'nom': affichage, 'hauteur_parole_mediane_hz': f0, 'duree_s': 20.0}))


class Syntaxe(unittest.TestCase):
    def test_trois_a_six_chanteurs(self):
        self.assertEqual(D0.lire_spec_voix('chanteur3:luc,chanteur1:jules,chanteur2:marie'),
                         ('ordre', [('chanteur1', 'jules'), ('chanteur2', 'marie'), ('chanteur3', 'luc')]))
        six = ','.join(f'chanteur{k}:v{k}' for k in range(6, 0, -1))
        mode, paires = D0.lire_spec_voix(six)
        self.assertEqual((mode, [c for c, _ in paires]), ('ordre', [f'chanteur{k}' for k in range(1, 7)]))

    def test_personnes(self):
        self.assertEqual(D0.lire_spec_voix('personne2:marie,personne1:jules'),
                         ('personnes', [('personne1', 'jules'), ('personne2', 'marie')]))
        self.assertEqual(D0.lire_spec_voix('Personne 1:jules,personne-2:marie,personne_3:luc')[0], 'personnes')

    def test_erreurs(self):
        sept = ','.join(f'chanteur{k}:v{k}' for k in range(1, 8))
        for texte, motif in ((sept, 'clé inconnue'), ('chanteur1:jules,chanteur3:marie', 'sans trou'),
                             ('chanteur2:jules,chanteur3:marie', 'sans trou'),
                             ('personne1:jules,chanteur2:marie', 'ne se mélangent pas'),
                             ('homme:jules,personne2:marie', 'ne se mélangent pas'),
                             ('homme:jules,femme:marie,homme:luc', 'deux fois')):
            with self.subTest(texte=texte), self.assertRaisesRegex(ValueError, motif):
                D0.lire_spec_voix(texte)

    def test_joindre_noms(self):
        self.assertEqual(D0.joindre_noms(['A']), 'A')
        self.assertEqual(D0.joindre_noms(['A', 'B']), 'A et B')
        self.assertEqual(D0.joindre_noms(['A', 'B', 'C', 'D']), 'A, B, C et D')


class VoixCombinee(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.D = charger_doubler(self.tmp)
        for nom, f0 in (('jules', 110.0), ('marie', 210.0), ('luc', 130.0), ('lea', 190.0)):
            ajouter_voix(self.D, nom, nom.capitalize(), f0)

    def charger(self, texte):
        with contextlib.redirect_stdout(io.StringIO()) as sortie:
            v = self.D.charger_voix_spec(texte)
        return v, sortie.getvalue()

    def test_quatre_chanteurs(self):
        v, _ = self.charger('chanteur2:marie,chanteur1:jules,chanteur4:lea,chanteur3:luc')
        self.assertEqual((v['id'], v['affichage'], v['mode']), ('jules+marie+luc+lea', 'Jules, Marie, Luc et Lea', 'ordre'))
        self.assertEqual(v['spec'], 'chanteur1:jules,chanteur2:marie,chanteur3:luc,chanteur4:lea')
        self.assertEqual([m['cle'] for m in v['membres']], ['chanteur1', 'chanteur2', 'chanteur3', 'chanteur4'])
        self.assertEqual(self.D.charger_voix_spec(v['spec'])['id'], v['id'], 'la spécification gardée pour --reprendre')
        self.assertEqual(self.D.decrire_voix(v), 'chanteur 1 → jules, chanteur 2 → marie, chanteur 3 → luc, chanteur 4 → lea')

    def test_personnes(self):
        v, _ = self.charger('personne1:jules,personne2:marie,personne3:luc')
        self.assertEqual((v['mode'], v['affichage']), ('personnes', 'Jules, Marie et Luc'))
        self.assertEqual(self.D.decrire_voix(v), 'personne 1 → jules, personne 2 → marie, personne 3 → luc')

    def test_une_voix_donnee_deux_fois_garde_la_bonne_cle(self):
        v, _ = self.charger('chanteur1:jules,chanteur2:jules,chanteur3:marie')
        self.assertEqual([(m['id'], m['cle']) for m in v['membres']], [('jules', 'chanteur1'), ('marie', 'chanteur3')])

    def test_deux_voix_comme_avant(self):
        v, _ = self.charger('homme:jules,femme:marie')
        self.assertEqual((v['id'], v['affichage'], v['spec']), ('jules+marie', 'Jules et Marie', 'homme:jules,femme:marie'))

    def test_reglages_du_noyau(self):
        a = argparse.Namespace(pas=30, paroles=True, moteur='yingmusic', sans_nettoyage=False, mode='auto', octave='auto',
                               transposer='auto', comparer_modeles=False, comparer_pas=None, gpu='auto', modele_parole=None,
                               versions_duo='utiles')
        v, _ = self.charger('personne1:jules,personne2:marie,personne3:luc')
        r = self.D.reglages_noyau(a, v, None, [])
        self.assertEqual(r['voix_cibles']['mode'], 'personnes')
        self.assertEqual([x['cle'] for x in r['voix_cibles']['voix']], ['personne1', 'personne2', 'personne3'])
        self.assertEqual(r['versions_duo'], 'utiles')


class Messages(unittest.TestCase):
    def test_trois_voix_dans_le_rapport(self):
        rapport = {'voix_cibles': {'mode': 'ordre'},
                   'decision_hauteur': {'voix': 3, 'axe': 'empreintes', 'chanteurs': [
                       {'nom': 'chanteur 1', 'voix': 'jules', 'decalage': 0}, {'nom': 'chanteur 2', 'voix': 'marie', 'decalage': 12},
                       {'nom': 'chanteur 3', 'voix': 'luc', 'decalage': -12}]},
                   'affectation': {'secondes_par_voix': {'jules': 20.0, 'marie': 15.0, 'luc': 9.0, 'ensemble': 4.0}}}
        lignes = D0.lignes_voix_cibles(rapport)
        self.assertIn('3 voix', lignes[0])
        self.assertIn('marie (+12 dt)', lignes[0])
        self.assertIn('luc 9 s', lignes[1])

    def test_parole_par_personne(self):
        rapport = {'voix_cibles': {'mode': 'personnes'},
                   'decision_hauteur': {'voix': 2, 'axe': 'diarisation', 'chanteurs': [
                       {'nom': 'personne 1', 'voix': 'jules'}, {'nom': 'personne 2', 'voix': 'marie'}]}}
        self.assertIn('qui parle quand', D0.lignes_voix_cibles(rapport)[0])
        self.assertNotIn('dt', D0.lignes_voix_cibles(rapport)[0])


class Estimation(unittest.TestCase):
    def estimer(self, **k):
        with Dossier() as d:
            return D0.estimer_noyau_s([180.0], 30, [str(d)], True, 'chant', 'yingmusic', True, **k)

    def test_deux_voix_comme_avant(self):
        base = self.estimer(voix_cibles=2, versions_duo='toutes')
        self.assertGreater(base, self.estimer(voix_cibles=1))

    def test_le_cout_croit_avec_le_nombre_de_voix(self):
        for versions in ('toutes', 'utiles'):
            durees = [self.estimer(voix_cibles=n, versions_duo=versions) for n in (2, 3, 4, 6)]
            self.assertEqual(durees, sorted(durees), versions)
            self.assertLess(durees[0], durees[-1])
        self.assertLess(self.estimer(voix_cibles=4, versions_duo='utiles'), self.estimer(voix_cibles=4, versions_duo='toutes'))
        self.assertLess(self.estimer(voix_cibles=4, versions_duo='non'), self.estimer(voix_cibles=4, versions_duo='utiles'))

    def test_parole_par_personne_un_peu_plus_long_qu_une_voix(self):
        with Dossier() as d:
            une = D0.estimer_noyau_s([180.0], 30, [str(d)], True, 'parole', 'yingmusic', True, voix_cibles=1)
            trois = D0.estimer_noyau_s([180.0], 30, [str(d)], True, 'parole', 'yingmusic', True, voix_cibles=3, personnes=True)
            chanteurs = D0.estimer_noyau_s([180.0], 30, [str(d)], True, 'parole', 'yingmusic', True, voix_cibles=3)
        self.assertGreater(trois, une)
        self.assertEqual(chanteurs, une, 'en parole, des chanteurs ne comptent pas : la première voix seule')


class ChoixCorrection(unittest.TestCase):
    noms = ['jules', 'marie', 'luc']
    cles = {'chanteur1': 'jules', 'chanteur2': 'marie', 'chanteur3': 'luc'}

    def test_une_voix_ou_une_clef(self):
        self.assertEqual(D0.nom_voix_choisie('luc', self.noms, self.cles), 'luc')
        self.assertEqual(D0.nom_voix_choisie('chanteur3', self.noms, self.cles), 'luc')

    def test_plusieurs_voix_ensemble(self):
        self.assertEqual(D0.nom_voix_choisie('luc+jules', self.noms, self.cles), 'jules+luc', 'dans l’ordre du doublage')
        self.assertEqual(D0.nom_voix_choisie('chanteur2+chanteur3', self.noms, self.cles), 'marie+luc')
        self.assertEqual(D0.nom_voix_choisie('jules+jules', self.noms, self.cles), 'jules')
        self.assertEqual(D0.nom_voix_choisie('deux', self.noms, self.cles), 'deux')
        with self.assertRaises(ValueError):
            D0.nom_voix_choisie('jules+hugo', self.noms, self.cles)
        with self.assertRaises(ValueError):
            D0.nom_voix_choisie('jules+auto', self.noms, self.cles)

    def test_lire_passage(self):
        self.assertEqual(D0.lire_passage('12-13=marie+luc', self.noms, self.cles), ([12, 13], None, 'marie+luc'))

    def test_voix_de_la_piste(self):
        m = [5, 0, 10, 0, 0, 1, 0, [0, 1, 2]]
        piste = {'nom': 'lead_sec'}
        self.assertEqual(D0.voix_de_la_piste({'passages': {'5': 'marie+luc'}}, piste, m, 5, self.noms), [1, 2])
        self.assertEqual(D0.voix_de_la_piste({'passages': {'5': 'deux'}}, piste, m, 5, self.noms), [0, 1, 2])
        self.assertEqual(D0.voix_de_la_piste({'passages': {'5': 'luc'}}, piste, m, 5, self.noms), [2])
        self.assertEqual(D0.voix_de_la_piste(None, piste, m, 5, self.noms), [1])


if __name__ == '__main__':
    unittest.main()
