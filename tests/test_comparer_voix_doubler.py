"""Comparer des voix (--comparer A,B,C), côté doubler.py : lecture et refus de la liste de voix, réglages envoyés au
noyau (rien ne change sans --comparer), envoi des références, estimation (la conversion se multiplie, le reste non),
puis une session entière (le noyau Kaggle imité rend un dossier par voix) : une sortie complète par voix dans le même
dossier, comparaison.json, session gardée pour --reprendre ; et --remix sur chacune des sorties. Voix et titres inventés
(voixa, voixb, voixc)."""
import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from commun import DOUBLER, FFMPEG, Dossier, charger_doubler, ecrire_wav, python
from test_plusieurs_voix_doubler import ajouter_voix, ton, video

VOIX = (('voixa', 'Voix A', 110.0), ('voixb', 'Voix B', 210.0), ('voixc', 'Voix C', 160.0))


def avec_voix(test, noms=VOIX):
    test._d = Dossier()
    test.tmp = test._d.__enter__()
    test.addCleanup(test._d.__exit__)
    test.D = charger_doubler(test.tmp)
    for nom, affichage, f0 in noms:
        ajouter_voix(test.D, nom, affichage, f0)


class Lecture(unittest.TestCase):
    def setUp(self):
        avec_voix(self)

    def comparer(self, texte):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.D.charger_voix_comparees(texte)

    def test_trois_voix(self):
        v = self.comparer('voixc, voixa,voixb')
        self.assertEqual([m['id'] for m in v['comparees']], ['voixc', 'voixa', 'voixb'], "l'ordre demandé est gardé")
        self.assertEqual((v['id'], v['f0'], v['spec']), ('voixc', 160.0, 'voixc,voixa,voixb'), 'la première analyse')
        self.assertEqual(self.D.voix_membres(v), [v], 'pas une voix par chanteur')
        self.assertEqual([m['id'] for m in self.D.voix_a_envoyer(v)], ['voixc', 'voixa', 'voixb'])
        self.assertEqual(self.D.charger_voix_comparees(v['spec'])['spec'], v['spec'], 'la spécification de --reprendre')

    def test_doublons_ignores(self):
        v = self.comparer('voixa,voixb,voixa')
        self.assertEqual([m['id'] for m in v['comparees']], ['voixa', 'voixb'])

    def test_refus(self):
        for texte, motif in (('voixa', 'au moins deux voix différentes'), ('voixa,voixa', 'au moins deux voix différentes'),
                             ('', 'au moins deux voix'), ('homme:voixa,femme:voixb', 'ne se compare pas'),
                             ('voixa,Voix B', 'nom de voix invalide'), ('voixa,absente', 'voix inconnue : absente'),
                             (','.join(f'voix{i}' for i in range(7)), 'au plus')):
            with self.subTest(texte=texte), self.assertRaises(SystemExit) as e:
                self.comparer(texte)
            self.assertRegex(str(e.exception.code), motif)

    def test_sorties_une_par_voix(self):
        video_ = self.tmp / 'Une chanson.mp4'
        video_.write_bytes(b'')
        a = argparse.Namespace(sortie=str(self.tmp / 'sorties'))
        v = self.comparer('voixa,voixb,voixc')
        tr = self.D.emplacements(video_, 10, 20, a, v)
        dossier = self.tmp / 'sorties' / 'Une chanson (extrait 0m10,0+0m20,0)'
        self.assertEqual(tr['dossier'], dossier)
        sorties = [self.D.sortie_de_voix(tr, m) for m in v['comparees']]
        self.assertEqual([s['base'] for s in sorties],
                         ['Une chanson - voix Voix A', 'Une chanson - voix Voix B', 'Une chanson - voix Voix C'])
        self.assertEqual([s['tv'] for s in sorties], [dossier / 'travail' / n for n in ('voixa', 'voixb', 'voixc')])
        self.assertTrue(all(s['dossier'] == dossier and s['travail'] == tr['travail'] for s in sorties))


class Reglages(unittest.TestCase):
    def setUp(self):
        avec_voix(self)
        self.a = argparse.Namespace(pas=30, paroles=True, moteur='yingmusic', sans_nettoyage=False, mode='auto',
                                    octave='auto', comparer_modeles=False, comparer_pas=None, modele_parole=None)
        self.travaux = [{'id': 'v1', 'entree': 'entree-x.flac', 'gain_entree': 2.0}]

    def test_sans_comparer_rien_ne_change(self):
        r = self.D.reglages_noyau(self.a, self.D.charger_voix('voixa'), None, self.travaux)
        self.assertNotIn('comparer', r)
        self.assertNotIn('voix_cibles', r)

    def test_voix_a_comparer(self):
        with contextlib.redirect_stdout(io.StringIO()):
            v = self.D.charger_voix_comparees('voixb,voixa,voixc')
        r = self.D.reglages_noyau(self.a, v, None, self.travaux)
        # chaque voix porte sa zone de chant (transposition, 0.15.0) : vérifiée à part, puis retirée pour comparer le reste
        for d in [r['voix'], *r['comparer']['voix']]:
            self.assertIn('confort', d.pop('zone'))
        self.assertEqual(r['voix'], {'nom': 'voixb', 'fichier': 'voix-voixb.wav', 'f0_parole_hz': 210.0, 'facteur_chant': 2.0})
        self.assertEqual(r['comparer'], {'voix': [
            {'nom': 'voixb', 'fichier': 'voix-voixb.wav', 'f0_parole_hz': 210.0, 'facteur_chant': 2.0, 'affichage': 'Voix B'},
            {'nom': 'voixa', 'fichier': 'voix-voixa.wav', 'f0_parole_hz': 110.0, 'facteur_chant': 2.0, 'affichage': 'Voix A'},
            {'nom': 'voixc', 'fichier': 'voix-voixc.wav', 'f0_parole_hz': 160.0, 'facteur_chant': 2.0,
             'affichage': 'Voix C'}]})
        self.assertNotIn('voix_cibles', r, "pas de répartition par chanteur")
        self.assertNotIn('versions_duo', r)
        self.assertEqual([t['id'] for t in r['travaux']], ['v1'], 'une seule vidéo à séparer, quel que soit le nombre de voix')

    def test_envoi_de_toutes_les_references(self):
        with contextlib.redirect_stdout(io.StringIO()):
            v = self.D.charger_voix_comparees('voixa,voixb,voixc')
        self.D.JEU = 'compte/jeu'
        envoyes = {}

        def kaggle(*args):
            if args[:2] in (('datasets', 'version'), ('datasets', 'create')):
                envoyes['fichiers'] = sorted(f.name for f in (self.tmp / 'k' / 'jeu').iterdir())
                return 0, 'ok'
            return 0, 'ready ' + ' '.join(envoyes.get('fichiers', []))
        t = self.tmp / 't'
        t.mkdir()
        (t / 'original.wav').write_bytes(b'x' * 100)
        with mock.patch.object(self.D, 'kaggle', kaggle), mock.patch.object(self.D.time, 'sleep', lambda s: None), \
                mock.patch.object(self.D, 'encoder_flac', lambda w, f, bits=16: Path(f).write_bytes(b'f') or 1.0), \
                contextlib.redirect_stdout(io.StringIO()) as dit:
            self.D.envoyer_jeu([{'travail': t, 'entree': 'entree-a.flac'}], v, self.tmp / 'k')
        self.assertEqual(envoyes['fichiers'], ['dataset-metadata.json', 'entree-a.flac', 'voix-voixa.wav',
                                               'voix-voixb.wav', 'voix-voixc.wav'], 'le son une fois, une référence par voix')
        self.assertIn('les voix voixa et voixb et voixc', dit.getvalue())


class Estimation(unittest.TestCase):
    def setUp(self):
        avec_voix(self)
        self.rien = str(self.tmp / 'rien')

    def est(self, **k):
        return self.D.estimer_noyau_s([120.0], 30, self.rien, **k)

    def test_une_voix_comme_avant(self):
        self.assertEqual(self.est(voix_comparees=1), self.est())
        self.assertEqual(self.est(voix_comparees=0), self.est())

    def test_la_conversion_se_multiplie_le_reste_non(self):
        un, deux, trois = (self.est(voix_comparees=n) for n in (1, 2, 3))
        self.assertGreater(deux, un)
        self.assertGreater(trois, deux)
        # trois conversions coûtent moins que trois sessions entières : les frais fixes ne sont payés qu'une fois
        self.assertLess(trois, 3 * un)
        self.assertLess(deux, 2 * un)
        # les deux GPU se partagent les voix : le surcoût d'une voix de plus reste celui d'une conversion à deux cartes
        un2, trois2 = (self.est(voix_comparees=n, cartes=2) for n in (1, 3))
        self.assertGreater(trois2, un2)
        self.assertLess(trois2, 3 * un2)

    def test_parole_pas_de_multiplication(self):
        # le noyau convertit chaque voix aussi en parole : la conversion se multiplie quel que soit le mode
        un, trois = (self.est(mode='parole', voix_comparees=n) for n in (1, 3))
        self.assertGreaterEqual(trois, un)


def rapport_voix(entree, voix):
    return {'mode': 'chant', 'frise': [], 'modele': 'chant44k', 'moteur': 'yingmusic', 'travail': {'entree': entree},
            'voix_cible': {'nom': voix}}


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Session(unittest.TestCase):
    """Trois voix en une session : le noyau Kaggle imité rend la voix isolée une fois et un dossier par voix."""
    DUREE = 5.0

    def setUp(self):
        avec_voix(self)
        self.D.JEU, self.D.NOYAU, self.D.OUTILS = 'compte/jeu', 'compte/noyau', 'compte/outils'
        self.source = video(self.tmp / 'Une chanson.mp4', self.DUREE)
        self.evenements = self.tmp / 'ev.jsonl'
        self.D.EV.ouvrir(self.evenements)
        self.addCleanup(lambda: self.D.EV.f.close())
        self.D.corbeille = lambda fichiers: ([], [])   # jamais la vraie corbeille
        self.dossier = self.tmp / 'sorties' / 'Une chanson'

    def args(self, **k):
        return argparse.Namespace(**{'sortie': str(self.tmp / 'sorties'), 'debut': None, 'duree': None, 'pas': 30,
                                     'paroles': False, 'moteur': 'yingmusic', 'sans_nettoyage': False, 'mode': 'auto',
                                     'octave': 'auto', 'comparer_modeles': False, 'comparer_pas': None,
                                     'modele_parole': None, 'voix_db': None, 'mix_v1': False, 'reverb_db': -13.0,
                                     'autotune': None, 'sans_autotune': False, 'autotune_gamme': None, **k})

    def faux_noyau(self, recu, sans=()):
        def lancer_noyau(reglages, kdir, retour, attente_maxi, sources=(), videos=(), estimation_s=None, etapes=None,
                         apres_poussee=None, **k):
            recu['reglages'] = reglages
            apres_poussee(1000.0, 7)
            recu['session'] = json.loads(self.D.SESSION.read_text())
            (retour / 'v1').mkdir(parents=True)
            (retour / 'session.json').write_text(json.dumps({'debut_epoch': 1010.0, 'temps': {}}))
            t = Path(recu['session']['travaux'][0]['travail'])
            shutil.copy2(t / 'original.wav', retour / 'v1' / 'voix_source.wav')
            for i, m in enumerate(reglages['comparer']['voix']):
                if m['nom'] in sans:
                    continue
                d = retour / 'v1' / m['nom']
                d.mkdir()
                ecrire_wav(d / 'voix.wav', ton(220 + 40 * i, self.DUREE, -24))
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(d / 'voix.wav'), '-c:a', 'flac', '-sample_fmt',
                                's32', str(d / 'voix_convertie.flac')], check=True)
                (d / 'voix.wav').unlink()
                (d / 'rapport.json').write_text(json.dumps(rapport_voix(reglages['travaux'][0]['entree'], m['nom'])))
            return {'pousse_epoch': 1000.0, 'version_noyau': 7, 'noyau_depuis_envoi_s': 60.0, 'rapatriement_s': 1.0}
        return lancer_noyau

    def envoyer(self, travaux, voix, kdir, bits=16):
        self.envoyees = [m['id'] for m in self.D.voix_a_envoyer(voix)]
        for tr in travaux:
            tr['gain_entree'], tr['taille_envoi'] = 1.0, 1

    def doubler(self, recu, texte='voixb,voixa,voixc', sans=()):
        with contextlib.redirect_stdout(io.StringIO()):
            v = self.D.charger_voix_comparees(texte)
        with mock.patch.object(self.D, 'lancer_noyau', self.faux_noyau(recu, sans)), \
                mock.patch.object(self.D, 'envoyer_jeu', self.envoyer), \
                mock.patch.object(self.D, 'lire_ancres', lambda: None), \
                contextlib.redirect_stdout(io.StringIO()) as dit:
            a = self.args()
            a.autotune_reglage = self.D.reglage_autotune(a, {})
            echecs = self.D.traiter_lot([str(self.source)], a, v)
        return echecs, dit.getvalue(), v

    def test_une_sortie_complete_par_voix(self):
        recu = {}
        echecs, texte, _ = self.doubler(recu)
        self.assertEqual(echecs, [], texte)
        self.assertEqual(self.envoyees, ['voixb', 'voixa', 'voixc'])
        self.assertEqual([m['nom'] for m in recu['reglages']['comparer']['voix']], ['voixb', 'voixa', 'voixc'])
        self.assertEqual(len(recu['reglages']['travaux']), 1, 'une seule vidéo envoyée, pas une par voix')
        for f in self.dossier.glob('Une chanson - voix *'):
            self.assertIn(f.suffix, ('.mp4', '.mp3'))
        for affichage, nom in (('Voix A', 'voixa'), ('Voix B', 'voixb'), ('Voix C', 'voixc')):
            for f in (f'Une chanson - voix {affichage}.mp4', f'Une chanson - voix {affichage}.mp3',
                      f'travail/{nom}/voix_convertie.flac', f'travail/{nom}/mix.wav', f'travail/{nom}/rapport.json'):
                self.assertTrue((self.dossier / f).exists(), f)
            rapport = json.loads((self.dossier / 'travail' / nom / 'rapport.json').read_text())
            self.assertEqual(rapport['voix_cible']['nom'], nom, 'le rapport de chaque voix est le sien')
            self.assertEqual(rapport['temps_local']['voix_comparees'], 3)
            self.assertEqual(rapport['mix']['voix_db'], 4.0)
        self.assertTrue((self.dossier / 'travail' / 'voix_source.wav').exists(), 'la voix isolée, commune, une fois')
        self.assertFalse((self.dossier / 'travail' / 'voixa' / 'voix_source.wav').exists())
        self.assertIn('comparaison de 3 voix en une session : voixb, voixa, voixc', texte)
        comp = json.loads((self.dossier / 'comparaison.json').read_text())
        self.assertEqual([v['id'] for v in comp['voix']], ['voixb', 'voixa', 'voixc'])
        self.assertEqual([v['fichier'] for v in comp['voix']],
                         ['Une chanson - voix Voix B', 'Une chanson - voix Voix A', 'Une chanson - voix Voix C'])
        ev = [json.loads(l) for l in self.evenements.read_text().splitlines()]
        sorties = [e for e in ev if e['type'] == 'sortie']
        self.assertEqual([e['voix'] for e in sorties], [['voixb'], ['voixa'], ['voixc']], 'un événement par voix')
        self.assertTrue(all(e['rapport'].endswith('/rapport.json') for e in sorties))

    def test_session_gardee_pour_reprendre(self):
        recu = {}
        self.doubler(recu)
        self.assertEqual(recu['session']['comparer'], ['voixb', 'voixa', 'voixc'])
        self.assertEqual(recu['session']['voix'], 'voixb,voixa,voixc')
        precedente = json.loads((self.D.ETAT / 'session-precedente.json').read_text())
        self.assertEqual(precedente['comparer'], ['voixb', 'voixa', 'voixc'])

    def test_une_voix_sans_resultat_n_arrete_pas_les_autres(self):
        echecs, texte, _ = self.doubler({}, sans=('voixa',))
        self.assertEqual(echecs, [str(self.source.resolve())])
        self.assertTrue((self.dossier / 'Une chanson - voix Voix B.mp4').exists())
        self.assertTrue((self.dossier / 'Une chanson - voix Voix C.mp4').exists())
        self.assertFalse((self.dossier / 'Une chanson - voix Voix A.mp4').exists())

    def test_ranger_retour_par_voix(self):
        with contextlib.redirect_stdout(io.StringIO()):
            v = self.D.charger_voix_comparees('voixa,voixb')
        travail = self.tmp / 'sorties' / 'X' / 'travail'
        tr = {'id': 'v1', 'video': Path('X.mp4'), 'travail': travail, 'tv': travail / 'voixa', 'dossier': travail.parent}
        (travail / 'voixa').mkdir(parents=True)
        (travail / 'voixa' / 'affectation.json').write_text('{}')   # un ancien doublage : remplacé
        retour, kdir = self.tmp / 'retour', self.tmp / 'kaggle'
        kdir.mkdir()
        for nom in ('voixa', 'voixb'):
            (retour / 'v1' / nom).mkdir(parents=True)
            (retour / 'v1' / nom / 'rapport.json').write_text(json.dumps({'voix': nom}))
        (retour / 'noyau.log').write_text('log')
        self.D.ranger_retour([tr], retour, kdir, v)
        for nom in ('voixa', 'voixb'):
            self.assertEqual(json.loads((travail / nom / 'rapport.json').read_text()), {'voix': nom})
            self.assertTrue((travail / nom / 'noyau.log').exists())
        self.assertFalse((travail / 'voixa' / 'affectation.json').exists())
        self.assertFalse(retour.exists())

    def test_remix_de_chaque_sortie(self):
        self.doubler({})
        env = {**os.environ, 'HOME': str(self.tmp)}
        for k in ('VOIXLA_DONNEES', 'DOUBLER_VOIX', 'VOIXLA_KAGGLE'):
            env.pop(k, None)
        avant = {n: (self.dossier / f'Une chanson - voix {n}.mp3').stat().st_mtime_ns for n in ('Voix A', 'Voix B', 'Voix C')}
        r = subprocess.run([python(), str(DOUBLER), str(self.source), '--remix', '--comparer', 'voixa,voixb,voixc',
                            '--sortie', str(self.tmp / 'sorties'), '--sans-paroles', '--voix-db', '2'],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for nom, n in (('voixa', 'Voix A'), ('voixb', 'Voix B'), ('voixc', 'Voix C')):
            self.assertGreater((self.dossier / f'Une chanson - voix {n}.mp3').stat().st_mtime_ns, avant[n])
            self.assertEqual(json.loads((self.dossier / 'travail' / nom / 'rapport.json').read_text())['mix']['voix_db'], 2.0)
        self.assertIn('(remix, voix voixb)', r.stdout)
        self.assertTrue((self.dossier / 'comparaison.json').exists())

    def test_remix_d_une_seule_voix_de_la_comparaison(self):
        # une sortie de la comparaison se remixe aussi seule, comme n'importe quel doublage
        self.doubler({})
        env = {**os.environ, 'HOME': str(self.tmp)}
        for k in ('VOIXLA_DONNEES', 'DOUBLER_VOIX', 'VOIXLA_KAGGLE'):
            env.pop(k, None)
        r = subprocess.run([python(), str(DOUBLER), str(self.source), '--remix', '--voix', 'voixb', '--sortie',
                            str(self.tmp / 'sorties'), '--sans-paroles', '--voix-db', '1'],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((self.dossier / 'travail' / 'voixb' / 'rapport.json').read_text())['mix']['voix_db'], 1.0)
        self.assertEqual(json.loads((self.dossier / 'travail' / 'voixa' / 'rapport.json').read_text())['mix']['voix_db'], 4.0)

    def test_remix_sans_travail_d_une_voix(self):
        self.doubler({})
        shutil.rmtree(self.dossier / 'travail' / 'voixc')
        env = {**os.environ, 'HOME': str(self.tmp)}
        for k in ('VOIXLA_DONNEES', 'DOUBLER_VOIX', 'VOIXLA_KAGGLE'):
            env.pop(k, None)
        r = subprocess.run([python(), str(DOUBLER), str(self.source), '--remix', '--comparer', 'voixa,voixb,voixc',
                            '--sortie', str(self.tmp / 'sorties'), '--sans-paroles'],
                           capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('voix sans travail à remixer : voixc', r.stdout + r.stderr)
        self.assertIn('=== Une chanson.mp4 (remix, voix voixb)', r.stdout, 'les autres voix sont refaites quand même')


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Commande(unittest.TestCase):
    def lancer(self, *args):
        with Dossier() as tmp:
            env = {**os.environ, 'HOME': str(tmp)}
            for k in ('VOIXLA_DONNEES', 'DOUBLER_VOIX', 'VOIXLA_KAGGLE'):
                env.pop(k, None)
            D = charger_doubler(tmp)
            for nom, affichage, f0 in VOIX:
                ajouter_voix(D, nom, affichage, f0)
            return subprocess.run([python(), str(DOUBLER), *args], capture_output=True, text=True, env=env)

    def test_aide(self):
        r = self.lancer('--help')
        self.assertIn('--comparer A,B[,C', r.stdout)
        self.assertIn('--comparer-pas', r.stdout, "l'option d'avant est toujours là")

    def test_refus_propres(self):
        for args, motif in ((('x.mp4', '--comparer', 'voixa,voixb', '--voix', 'voixc'), '--comparer remplace --voix'),
                            (('x.mp4', '--comparer', 'voixa'), 'au moins deux voix différentes'),
                            (('x.mp4', '--comparer', 'homme:voixa,femme:voixb'), 'ne se compare pas'),
                            (('x.mp4', '--comparer', 'voixa,voixb', '--remix', '--passage', '1=voixa'),
                             'ne vont pas avec --comparer')):
            with self.subTest(args=args):
                r = self.lancer(*args)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(motif, r.stderr)


if __name__ == '__main__':
    unittest.main()
