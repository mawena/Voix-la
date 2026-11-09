"""Corriger à la main qui chante quoi dans un duo (une voix par chanteur).

Noyau (chaîne entière sans GPU, imitations de test_plusieurs_voix_chaine) : avec versions_duo, chaque passage de chaque
piste est aussi converti dans l'autre voix et rapatrié (versions/) ; le résultat automatique ne change pas d'un bit
(voix convertie, courbes, rapport hors la section versions) ; « utiles » ne convertit que les passages qui comptent ;
sans le réglage, ni versions ni couches.

PC (doubler.py) : la voix convertie recomposée depuis les deux versions, sans correction, est celle du noyau ; avec une
correction, le passage choisi sonne dans la voix choisie (note témoin), les autres ne changent pas ; --passage,
--affectation (fichier, auto), l'affectation gardée par un remix sans option, le retour à l'automatique (voix convertie
rendue au bit près), rapport.json et courbes.json qui suivent, et les messages clairs : doublage sans versions, passage
sans version, voix inconnue."""
import argparse
import contextlib
import io
import json
import shutil
import subprocess
import unittest

import numpy as np

from commun import FFMPEG, SR, Dossier, charger_doubler, ecrire_wav
from test_plusieurs_voix_chaine import Timbres, lancer_noyau, lire_son, reglages, sans_durees, temoins

DUO = [('homme', 'jules'), ('femme', 'marie')]


def noyau(versions=None, moteur='yingmusic', cartes=1):
    """Sortie du noyau (dossier, rapport, voix convertie, courbes) ; le dossier est à effacer par l'appelant."""
    d = Dossier()
    dossier = d.__enter__()
    R = reglages(Timbres(), moteur, DUO)
    if versions is not None:
        R['versions_duo'] = versions
    sortie = lancer_noyau(dossier, R, cartes=cartes)
    return d, dossier, sortie


def vrai_flac(chemin):
    """Un faux FLAC du noyau d'essai (numpy dans le fichier) devient un vrai FLAC 24 bits, par ffmpeg."""
    x, sr = lire_son(chemin)
    canaux = 1 if x.ndim == 1 else x.shape[1]
    tmp = chemin.with_name(chemin.name + '.wav')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(sr), '-ac', str(canaux), '-i', '-', '-c:a',
                    'pcm_f32le', str(tmp)], input=np.asarray(x, np.float32).tobytes(), check=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(tmp), '-c:a', 'flac', '-sample_fmt', 's32',
                    '-bits_per_raw_sample', '24', str(chemin)], check=True)
    tmp.unlink()


class VersionsAuNoyau(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sans = noyau()
        cls.toutes = noyau('toutes')
        cls.utiles = noyau('utiles')

    @classmethod
    def tearDownClass(cls):
        for d, *_ in (cls.sans, cls.toutes, cls.utiles):
            d.__exit__(None, None, None)

    def rapport(self, s):
        return json.loads((s[2] / 'rapport.json').read_text().replace(str(s[1]), '<dossier>'))

    def test_automatique_identique(self):
        a, b = self.sans[2], self.toutes[2]
        self.assertTrue(np.array_equal(lire_son(a / 'voix_convertie.flac')[0], lire_son(b / 'voix_convertie.flac')[0]))
        self.assertEqual((a / 'courbes.json').read_text(), (b / 'courbes.json').read_text())
        ra, rb = self.rapport(self.sans), self.rapport(self.toutes)
        self.assertIn('versions', rb)
        rb.pop('versions')
        rb['reglages'].pop('versions_duo')   # l'écho du réglage lui-même
        self.assertEqual(json.dumps(sans_durees(ra), sort_keys=True), json.dumps(sans_durees(rb), sort_keys=True))

    def test_sans_reglage_rien_de_plus(self):
        self.assertNotIn('versions', self.rapport(self.sans))
        self.assertFalse((self.sans[2] / 'versions').exists())
        self.assertEqual(sorted(p.name for p in self.sans[2].iterdir()),
                         sorted(p.name for p in self.toutes[2].iterdir() if p.name != 'versions'))

    def test_versions_ecrites(self):
        s = self.toutes
        v = self.rapport(s)['versions']
        self.assertEqual((v['mode'], v['dossier'], v['voix_cibles']), ('toutes', 'versions', ['jules', 'marie']))
        rec = json.loads((s[2] / 'versions' / 'versions.json').read_text())
        self.assertEqual([p['nom'] for p in rec['pistes']], ['lead_sec', 'choeurs_sec', 'secours'])
        for p in rec['pistes']:
            for f in p['fichiers']:
                self.assertTrue((s[2] / 'versions' / f).exists(), f)
            for m in p['morceaux']:
                self.assertEqual(m[7], [0, 1], 'toutes : les deux voix disponibles')
        morceaux = {m['i'] for m in self.rapport(s)['morceaux'] if not m['muet']}
        self.assertEqual(set(v['morceaux']), morceaux)
        self.assertGreater(v['conversion_s'], 0)

    def test_utiles_moins_de_passages(self):
        vt, vu = self.rapport(self.toutes)['versions'], self.rapport(self.utiles)['versions']
        self.assertEqual(vu['mode'], 'utiles')
        self.assertLess(len(vu['morceaux']), len(vt['morceaux']))
        self.assertGreater(len(vu['morceaux']), 0)
        self.assertTrue(set(vu['morceaux']) <= set(vt['morceaux']))
        self.assertTrue(set(vu['raisons']) >= set(map(str, vu['morceaux'])))
        # même résultat automatique
        self.assertTrue(np.array_equal(lire_son(self.toutes[2] / 'voix_convertie.flac')[0],
                                       lire_son(self.utiles[2] / 'voix_convertie.flac')[0]))

    def test_deux_cartes_meme_resultat(self):
        d, dossier, s = noyau('toutes', cartes=2)
        try:
            self.assertTrue(np.array_equal(lire_son(s / 'voix_convertie.flac')[0],
                                           lire_son(self.toutes[2] / 'voix_convertie.flac')[0]))
            for p in json.loads((s / 'versions' / 'versions.json').read_text())['pistes']:
                for f in p['fichiers']:
                    ref = lire_son(self.toutes[2] / 'versions' / f)[0]
                    self.assertTrue(np.array_equal(lire_son(s / 'versions' / f)[0], ref), f)
        finally:
            d.__exit__(None, None, None)


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class CorrectionAuPC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.noyau = noyau('toutes')
        _, _, sortie = cls.noyau
        for f in [sortie / 'voix_convertie.flac', *(sortie / 'versions').glob('*.flac')]:
            vrai_flac(f)
        cls.auto = lire_son_vrai(sortie / 'voix_convertie.flac')
        cls.rapport = json.loads((sortie / 'rapport.json').read_text())
        cls.courbes = (sortie / 'courbes.json').read_text()

    @classmethod
    def tearDownClass(cls):
        cls.noyau[0].__exit__(None, None, None)

    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        (self.tmp / 'home').mkdir()
        self.D = charger_doubler(self.tmp / 'home')
        self.tv = self.tmp / 'sortie' / 'travail' / 'jules+marie'
        shutil.copytree(self.noyau[2], self.tv)
        self.voix = {'id': 'jules+marie', 'affichage': 'Jules et Marie', 'membres': [
            {'id': 'jules', 'affichage': 'Jules', 'cle': 'homme'}, {'id': 'marie', 'affichage': 'Marie', 'cle': 'femme'}]}
        self.tr = {'tv': self.tv, 'travail': self.tv.parent, 'dossier': self.tv.parents[1]}

    def args(self, passage=None, affectation=None):
        return argparse.Namespace(passage=passage, affectation=affectation)

    def appliquer(self, **options):
        with contextlib.redirect_stdout(io.StringIO()) as texte:
            corriges = self.D.appliquer_affectation(self.tr, self.args(**options), self.voix)
        self.texte = texte.getvalue()
        return corriges

    def voix_convertie(self):
        return lire_son_vrai(self.tv / 'voix_convertie.flac')

    def test_sans_option_rien_ne_change(self):
        self.assertEqual(self.appliquer(), [])
        self.assertTrue(np.array_equal(self.voix_convertie(), self.auto))
        self.assertFalse((self.tv / 'affectation.json').exists())

    def test_recomposition_sans_correction_egale_le_noyau(self):
        """La voix convertie recomposée depuis les couches, sans correction, retrouve celle du noyau (à l'arrondi du FLAC
        24 bits près) : même assemblage, même porte, même mise au niveau, même garde-crête."""
        rec = json.loads((self.tv / 'versions' / 'versions.json').read_text())
        total, code, _, choix, gains = self.D.recomposer(self.tv, rec, None, self.rapport)
        self.assertLess(np.max(np.abs(total - self.auto[:len(total)])), 2e-4 * max(np.max(np.abs(self.auto)), 1e-9))
        attendu = json.loads(self.courbes)['voix_par_trame']
        self.assertEqual([int(v) for v in code[::2][:len(attendu)]], attendu[:len(code[::2])])

    def passage_de(self, t):
        return next(m for m in self.rapport['morceaux'] if not m['muet'] and m['debut_s'] <= t < m['fin_s'])

    def test_un_passage_dans_l_autre_voix(self):
        m = self.passage_de(15.0)   # la voix de jules (chanteur)
        a, b = m['debut_s'], m['fin_s']
        avant = temoins(self.auto, a + 0.5, b - 0.5)
        self.assertGreater(avant['jules'], -30)
        self.assertLess(avant['marie'], -50)
        self.assertEqual(self.appliquer(passage=[f"{m['i']}=marie"]), [m['i']])
        y = self.voix_convertie()
        apres = temoins(y, a + 0.5, b - 0.5)
        self.assertGreater(apres['marie'], -30, apres)
        self.assertLess(apres['jules'], -50, apres)
        # ailleurs, rien n'a bougé (à l'arrondi près), et le passage voisin est inchangé
        ailleurs = np.ones(len(y), bool)
        ailleurs[int((a - 0.4) * SR):int((b + 0.4) * SR)] = False
        n = min(len(y), len(self.auto))
        self.assertLess(np.max(np.abs(y[:n][ailleurs[:n]] - self.auto[:n][ailleurs[:n]])), 3e-3)
        self.assertEqual(json.loads((self.tv / 'affectation.json').read_text())['passages'], {str(m['i']): 'marie'})
        rapport = json.loads((self.tv / 'rapport.json').read_text())
        man = rapport['affectation']['manuelle']
        self.assertEqual((man['corriges'], man['passages']), ([m['i']], {str(m['i']): 'marie'}))
        corrige = next(x for x in rapport['morceaux'] if x['i'] == m['i'])
        self.assertTrue(corrige['corrige'])
        self.assertEqual(corrige['voix_cible'], 'marie')
        self.assertEqual(set(corrige['voix_pistes'].values()), {'marie'})
        self.assertEqual(set(corrige['voix_pistes_auto'].values()), {'jules'})
        self.assertFalse(next(x for x in rapport['morceaux'] if x['i'] != m['i'] and not x['muet']).get('corrige'))
        # la frise « Voix de chaque passage » suit
        qui = [p['voix'] for p in rapport['affectation']['passages'] if p['debut_s'] <= a + 1 and p['fin_s'] >= b - 1]
        self.assertEqual(qui, [['marie']])
        # courbes.json suit
        c = json.loads((self.tv / 'courbes.json').read_text())
        self.assertEqual(c['voix_par_trame'][int((a + 1) / 0.02)], 1)
        self.assertEqual(len(c['voix_par_trame']), len(c['source_hz']))
        self.assertGreater(c['convertie_hz'][int((a + 1) / 0.02)], 100)

    def test_les_deux_et_piste_par_piste(self):
        m = self.passage_de(15.0)
        self.appliquer(passage=[f"{m['i']}=deux"])
        t = temoins(self.voix_convertie(), m['debut_s'] + 0.5, m['fin_s'] - 0.5)
        self.assertGreater(t['jules'], -30)
        self.assertGreater(t['marie'], -30)
        rapport = json.loads((self.tv / 'rapport.json').read_text())
        corrige = next(x for x in rapport['morceaux'] if x['i'] == m['i'])
        self.assertEqual(corrige['voix_pistes']['lead_sec'], ['jules', 'marie'])
        c = json.loads((self.tv / 'courbes.json').read_text())
        self.assertEqual(c['voix_par_trame'][int((m['debut_s'] + 1) / 0.02)], 2)
        # une seule piste : les chœurs du duo final passent à jules
        duo = next(x for x in self.rapport['morceaux'] if not x['muet'] and x['debut_s'] > 56)
        self.appliquer(passage=[f"{duo['i']}/choeurs=jules"])
        rapport = json.loads((self.tv / 'rapport.json').read_text())
        c2 = next(x for x in rapport['morceaux'] if x['i'] == duo['i'])
        self.assertEqual(c2['voix_pistes']['choeurs_sec'], 'jules')
        self.assertEqual(c2['voix_pistes']['lead_sec'], duo['voix_pistes']['lead_sec'])
        gardee = json.loads((self.tv / 'affectation.json').read_text())
        self.assertEqual((gardee['passages'], gardee['pistes']), ({str(m['i']): 'deux'}, {str(duo['i']): {'choeurs_sec': 'jules'}}))

    def test_remix_sans_option_garde_la_correction_et_auto_la_rend(self):
        m = self.passage_de(15.0)
        self.appliquer(passage=[f"{m['i']}=marie"])
        corrigee = self.voix_convertie()
        self.assertFalse(np.array_equal(corrigee, self.auto))
        self.assertEqual(self.appliquer(), [m['i']], 'un remix sans option garde l\'affectation')
        self.assertTrue(np.array_equal(self.voix_convertie(), corrigee))
        self.assertEqual(self.appliquer(affectation='auto'), [])
        self.assertTrue(np.array_equal(self.voix_convertie(), self.auto), 'automatique rendue au bit près')
        self.assertFalse((self.tv / 'affectation.json').exists())
        self.assertEqual((self.tv / 'courbes.json').read_text(), self.courbes)
        rapport = json.loads((self.tv / 'rapport.json').read_text())
        self.assertNotIn('manuelle', rapport['affectation'])
        self.assertEqual(rapport['affectation']['passages'], self.rapport['affectation']['passages'])
        self.assertEqual(rapport['morceaux'], self.rapport['morceaux'])

    def test_passage_auto_et_plages(self):
        chantes = [x['i'] for x in self.rapport['morceaux'] if not x['muet']]
        a, b = chantes[0], chantes[1]
        self.appliquer(passage=[f'{a},{b}=marie'])
        self.assertEqual(sorted(json.loads((self.tv / 'affectation.json').read_text())['passages'], key=int),
                         [str(a), str(b)])
        self.appliquer(passage=[f'{a}=auto'])
        self.assertNotIn(str(a), json.loads((self.tv / 'affectation.json').read_text())['passages'])

    def test_affectation_par_fichier(self):
        m = self.passage_de(15.0)
        f = self.tmp / 'a.json'
        f.write_text(json.dumps({'format': 1, 'passages': {str(m['i']): 'femme'}}))
        self.appliquer(affectation=str(f))   # la clé homme/femme est acceptée
        self.assertEqual(json.loads((self.tv / 'affectation.json').read_text())['passages'], {str(m['i']): 'marie'})

    def test_erreurs_claires(self):
        with self.assertRaisesRegex(RuntimeError, 'voix « zoe » inconnue'):
            self.appliquer(passage=['3=zoe'])
        with self.assertRaisesRegex(RuntimeError, 'N=voix attendu'):
            self.appliquer(passage=['marie'])
        with self.assertRaisesRegex(RuntimeError, 'sans voix à corriger'):
            self.appliquer(passage=['9999=marie'])
        # doublage sans les deux versions
        rapport = json.loads((self.tv / 'rapport.json').read_text())
        for variante, attendu in ((lambda r: r.pop('versions'), 'avant le 09/11/2026'),
                                  (lambda r: r.update(versions={'mode': 'non'}), '--versions-duo non'),
                                  (lambda r: r.update(versions={'mode': 'toutes', 'erreur': 'disque plein'}), 'disque plein')):
            r = json.loads(json.dumps(rapport))
            variante(r)
            (self.tv / 'rapport.json').write_text(json.dumps(r))
            m = self.passage_de(15.0)
            with self.assertRaisesRegex(RuntimeError, "n'a pas les deux versions de chaque passage.*" + attendu):
                self.appliquer(passage=[f"{m['i']}=marie"])
        # une seule voix : rien à corriger
        self.voix = {'id': 'jules', 'affichage': 'Jules'}
        with self.assertRaisesRegex(RuntimeError, "pas une voix par chanteur"):
            self.appliquer(passage=['3=marie'])

    def test_passage_sans_version_dans_la_voix_demandee(self):
        """Doublage « utiles » : un passage qui n'a pas ses deux versions ne peut pas changer de voix."""
        rec = json.loads((self.tv / 'versions' / 'versions.json').read_text())
        m = self.passage_de(15.0)
        for p in rec['pistes']:
            for mo in p['morceaux']:
                if mo[0] == m['i']:
                    mo[7] = [mo[5]]
        (self.tv / 'versions' / 'versions.json').write_text(json.dumps(rec))
        with self.assertRaisesRegex(RuntimeError, f"passage {m['i']}.*pas de version dans la voix marie"):
            self.appliquer(passage=[f"{m['i']}=marie"])


def lire_son_vrai(f):
    """Un vrai FLAC mono lu par ffmpeg (flottant)."""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(f), '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-'],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.float32).astype(np.float64)


if __name__ == '__main__':
    unittest.main()
