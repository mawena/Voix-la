"""Choix du moyen de calcul côté doubler.py : clés et option --calcul, plan « auto » (Kaggle, puis Modal), file « queued » de
Kaggle au-delà du seuil, bascule (lien du notebook à annuler, jamais « kaggle kernels delete »), estimation en dollars,
session sur Modal et sur Runpod avec les moyens imités, reprise, chemins du noyau par variables d'environnement. Comptes
et noms inventés ; HOME est un dossier temporaire ; rien ne part sur un réseau."""
import argparse
import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from commun import DEPOT, DOUBLER, NOYAU, Dossier, charger_doubler, charger_noyau, python

sys.path.insert(0, str(DEPOT))
import calcul  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.D = charger_doubler(self.home)
        self.D.JEU, self.D.NOYAU, self.D.OUTILS = 'jules/jeu', 'jules/doublage-voix-gpu', 'jules/outils'

    def silencieux(self, f, *a, **k):
        with contextlib.redirect_stdout(io.StringIO()) as sortie:
            try:
                return f(*a, **k)
            finally:
                self.sortie = sortie.getvalue()


class Configuration(Base):
    def test_cle_calcul(self):
        self.assertIn('calcul', self.D.CLES_CONFIG)
        self.assertEqual(self.D.calcul_config({}), 'kaggle', 'défaut : Kaggle, comme avant')
        for v in ('kaggle', 'modal', 'runpod', 'auto', 'AUTO'):
            self.silencieux(self.D.ecrire_config, 'calcul', v)
            self.assertEqual(json.loads(self.D.CONFIG.read_text())['calcul'], v.lower())
        with self.assertRaises(SystemExit):
            self.D.ecrire_config('calcul', 'ailleurs')
        self.assertEqual(self.silencieux(self.D.calcul_config, {'calcul': 'beaucoup'}), 'kaggle', 'illisible : kaggle')
        self.assertIn('illisible', self.sortie)

    def test_autres_cles(self):
        for cle in ('kaggle.attente_file_min', 'modal.gpu', 'runpod.gpu', 'runpod.duree_max_min'):
            self.assertIn(cle, self.D.CLES_CONFIG)
        self.silencieux(self.D.ecrire_config, 'modal.gpu', 'A10')
        self.silencieux(self.D.ecrire_config, 'kaggle.attente_file_min', '35')
        self.silencieux(self.D.ecrire_config, 'runpod.duree_max_min', '120')
        conf = json.loads(self.D.CONFIG.read_text())
        self.assertEqual((conf['modal']['gpu'], conf['kaggle']['attente_file_min'], conf['runpod']['duree_max_min']),
                         ('A10', 35, 120))
        self.assertEqual(self.D.gpu_calcul_config(conf, 'modal'), 'A10')
        self.assertEqual(self.D.gpu_calcul_config({}, 'modal'), 'L4', 'défaut raisonnable, réglable')
        self.assertEqual(self.D.gpu_calcul_config({}, 'runpod'), 'NVIDIA GeForce RTX 4090')
        self.assertEqual(self.D.entier_config({}, 'kaggle.attente_file_min', self.D.ATTENTE_FILE_MIN_DEFAUT), 20)
        for cle, v in (('kaggle.attente_file_min', '0'), ('kaggle.attente_file_min', 'vingt'), ('modal.gpu', 'L4;rm'),
                       ('runpod.gpu', '')):
            with self.assertRaises(SystemExit, msg=(cle, v)):
                self.D.ecrire_config(cle, v)

    def test_ligne_de_commande(self):
        env = {**os.environ, 'HOME': str(self.home)}
        r = subprocess.run([python(), str(DOUBLER), '--help'], capture_output=True, text=True, env=env, timeout=60)
        self.assertIn('--calcul', r.stdout)
        self.assertIn('--nettoyer-runpod', r.stdout)
        self.assertIn('--gpu', r.stdout, '--gpu garde son sens : le nombre de cartes')
        r = subprocess.run([python(), str(DOUBLER), 'x.mp4', '--calcul', 'ailleurs'], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        r = subprocess.run([python(), str(DOUBLER), '--config', '--json'], capture_output=True, text=True, env=env, timeout=60)
        effectif = json.loads(r.stdout)
        self.assertEqual(effectif['calcul'], 'kaggle')
        self.assertEqual(effectif['attente_file_min'], 20)
        self.assertEqual(effectif['modal']['gpu'], 'L4')
        self.assertFalse(effectif['modal']['pret'] and effectif['runpod']['pret'], 'ni jeton ni clé dans ce HOME')


class Plan(Base):
    def a(self, choix):
        return argparse.Namespace(calcul=choix)

    def test_un_seul_moyen(self):
        for choix in ('kaggle', 'modal', 'runpod'):
            self.assertEqual(self.D.plan_calcul(self.a(choix)), [choix])
        self.assertEqual(self.D.plan_calcul(argparse.Namespace()), ['kaggle'], 'sans option : Kaggle')

    def test_auto_kaggle_puis_modal(self):
        with mock.patch.object(self.D, 'moyen_pret', lambda nom: (True, '')):
            self.assertEqual(self.D.plan_calcul(self.a('auto')), ['kaggle', 'modal'])

    def test_auto_sans_jeton_kaggle_va_droit_a_modal(self):
        with mock.patch.object(self.D, 'moyen_pret', lambda nom: (False, 'clé absente')):
            plan = self.silencieux(self.D.plan_calcul, self.a('auto'))
        self.assertEqual(plan, ['modal'])
        self.assertIn('Kaggle n\'est pas disponible', self.sortie)

    def test_runpod_jamais_automatique(self):
        for pret in (True, False):
            with mock.patch.object(self.D, 'moyen_pret', lambda nom, p=pret: (p, '')):
                self.assertNotIn('runpod', self.silencieux(self.D.plan_calcul, self.a('auto')))


class FileKaggle(Base):
    """Calcul auto : un noyau Kaggle qui reste « queued » au-delà du seuil est abandonné pour Modal."""

    def horloge(self):
        t = [1000.0]
        p1 = mock.patch.object(self.D.time, 'time', lambda: t[0])
        p2 = mock.patch.object(self.D.time, 'sleep', lambda s: t.__setitem__(0, t[0] + s))
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        return t

    def kaggle(self, etats):
        suite = iter(etats)
        self.appels = []

        def faux(*args):
            self.appels.append(args)
            e = next(suite, etats[-1])
            return 0, f'jules/doublage-voix-gpu has status "KernelWorkerStatus.{e}"'
        return faux

    def test_queued_trop_longtemps(self):
        t = self.horloge()
        with mock.patch.object(self.D, 'kaggle', self.kaggle(['QUEUED'])), self.assertRaises(self.D.KaggleIndisponible) as e:
            self.silencieux(self.D.suivre_noyau, 'jules/doublage-voix-gpu', t[0], 6 * 3600, ['v'], file_maxi_s=20 * 60)
        self.assertIn('en file', str(e.exception))
        self.assertGreater(t[0] - 1000.0, 20 * 60)
        self.assertLess(t[0] - 1000.0, 20 * 60 + 60)
        self.assertTrue(all(a[:2] == ('kernels', 'status') for a in self.appels), 'seulement des lectures : jamais de delete')

    def test_sans_seuil_comme_avant(self):
        t = self.horloge()
        with mock.patch.object(self.D, 'kaggle', self.kaggle(['QUEUED'] * 5 + ['RUNNING', 'COMPLETE'])):
            etat = self.silencieux(self.D.suivre_noyau, 'jules/doublage-voix-gpu', t[0], 6 * 3600, ['v'])
        self.assertEqual(etat, 'COMPLETE')

    def test_un_noyau_qui_tourne_n_est_pas_abandonne(self):
        t = self.horloge()
        etats = ['QUEUED'] * 2 + ['RUNNING'] * 100 + ['COMPLETE']
        with mock.patch.object(self.D, 'kaggle', self.kaggle(etats)):
            etat = self.silencieux(self.D.suivre_noyau, 'jules/doublage-voix-gpu', t[0], 6 * 3600, ['v'],
                                   file_maxi_s=10 * 60)
        self.assertEqual(etat, 'COMPLETE', 'le seuil ne vise que la file, pas un noyau qui calcule')

    def test_bascule_donne_le_lien_du_notebook(self):
        self.D.ETAT = self.tmp / 'etat'
        self.D.SESSION = self.D.ETAT / 'session-en-cours.json'
        self.D.ecrire_session({'calcul': 'kaggle', 'noyau': self.D.NOYAU})
        with mock.patch.object(self.D, 'kaggle', side_effect=AssertionError('aucun appel kaggle attendu')):
            self.silencieux(self.D.basculer, self.D.KaggleIndisponible('Kaggle : noyau toujours en file'), 'modal')
        self.assertIn('bascule sur Modal', self.sortie)
        self.assertIn('https://www.kaggle.com/code/jules/doublage-voix-gpu', self.sortie)
        self.assertIn('annuler', self.sortie.lower())
        self.assertIn('ne sait pas l\'annuler', self.sortie)
        self.assertNotIn('delete', self.sortie)
        self.assertFalse(self.D.SESSION.exists(), 'la session Kaggle est close : elle ne bloque pas la suivante')
        self.assertTrue((self.D.ETAT / 'session-precedente.json').exists())

    def test_les_refus_de_kaggle_sont_des_indisponibilites(self):
        self.assertTrue(issubclass(self.D.KaggleIndisponible, RuntimeError))
        retour = self.tmp / 'retour'
        retour.mkdir()
        (retour / 'noyau.log').write_text(json.dumps([{'data': 'ACCÈS REFUSÉ : GPU indisponible\n'}]))
        self.assertIsInstance(self.D.erreur_sortie(retour, 'ERROR', 'session.json', 'Kaggle'), self.D.KaggleIndisponible)
        e = self.D.erreur_sortie(retour, 'ERROR', 'session.json', 'Modal')
        self.assertIsInstance(e, RuntimeError)
        self.assertNotIsInstance(e, self.D.KaggleIndisponible)


class Estimation(unittest.TestCase):
    def test_chanson_de_quatre_minutes(self):
        """Une session Kaggle d'environ 28 min sur une carte (4 min de chant) : durée et dollars selon la carte."""
        kaggle_s = 28 * 60
        resultats = {}
        for calcul_nom, gpu in (('modal', 'L4'), ('modal', 'A10'), ('modal', 'A100-40GB'),
                                ('runpod', 'NVIDIA GeForce RTX 4090')):
            resultats[gpu] = calcul.estimer_calcul(kaggle_s, calcul_nom, calcul.carte(gpu, calcul_nom))
        for gpu, (duree, cout) in resultats.items():
            self.assertTrue(10 * 60 < duree < 40 * 60, (gpu, duree))
            self.assertTrue(0.1 < cout < 0.8, (gpu, cout))
        self.assertGreater(resultats['L4'][0], resultats['A10'][0])
        self.assertGreater(resultats['A10'][0], resultats['A100-40GB'][0])
        self.assertIsNone(calcul.estimer_calcul(kaggle_s, 'modal', calcul.carte('H100', 'modal'))[1], 'carte inconnue : pas de prix')
        self.assertEqual(calcul.dollars(0.3), '0,30 $')
        self.assertEqual(calcul.dollars(None), 'coût inconnu')

    def test_deux_cartes(self):
        c = calcul.carte('L4:2', 'modal')
        self.assertEqual((c['nom'], c['cartes']), ('L4', 2))
        self.assertEqual(calcul.cartes_t4_pour(c), 2)
        un = calcul.estimer_calcul(1000, 'modal', calcul.carte('L4', 'modal'))
        deux = calcul.estimer_calcul(1000, 'modal', c)
        self.assertGreater(deux[1], 0)
        self.assertLessEqual(deux[1] / un[1], 2.2, 'deux cartes facturées deux fois')

    def test_cartes_illisibles(self):
        for v in ('', 'L4;rm -rf', 'L4:9', '../x;'):
            with self.assertRaises(ValueError):
                calcul.lire_gpu(v, 'modal')

    def test_historique_d_autres_calculs_ignore(self):
        with Dossier() as tmp:
            D = charger_doubler(tmp)
            rapport = tmp / 'sorties' / 'x' / 'travail' / 'jules'
            rapport.mkdir(parents=True)
            (rapport / 'rapport.json').write_text(json.dumps({
                'duree_s': 200.0, 'temps': {}, 'temps_local': {'calcul': 'modal', 'noyau_depuis_envoi_s': 99999,
                                                               'session_kaggle': {'installation_s': 9999}}}))
            sans = D.estimer_noyau_s([200.0], 30, [str(tmp / 'vide')])
            avec = D.estimer_noyau_s([200.0], 30, [str(tmp / 'sorties')])
        self.assertEqual(sans, avec, 'un rapport de session Modal ne sert pas à estimer Kaggle')


class CheminsDuNoyau(unittest.TestCase):
    def test_kaggle_par_defaut(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('DOUBLER_ENTREES', None)
            os.environ.pop('DOUBLER_SORTIE', None)
            n = charger_noyau()
        self.assertEqual((n.ENTREES, n.SORTIE), ('/kaggle/input', '/kaggle/working'))

    def test_chemins_deplaces(self):
        with Dossier() as tmp:
            (tmp / 'entrees' / 'outils').mkdir(parents=True)
            (tmp / 'entrees' / 'voix-jules.wav').write_bytes(b'x')
            (tmp / 'entrees' / 'outils' / 'outils.json').write_text('{"date": "2026-11-10"}')
            with mock.patch.dict(os.environ, {'DOUBLER_ENTREES': str(tmp / 'entrees'), 'DOUBLER_SORTIE': str(tmp / 'sortie')}):
                n = charger_noyau()
            self.assertEqual((n.ENTREES, n.SORTIE), (str(tmp / 'entrees'), str(tmp / 'sortie')))
            self.assertEqual(n.trouver('voix-jules.wav'), str(tmp / 'entrees' / 'voix-jules.wav'))
            self.assertEqual(n.trouver_outils(), str(tmp / 'entrees' / 'outils'), 'le volume des modèles y est monté')
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                n.trouver('absent.flac')

    def test_plus_de_chemin_kaggle_en_dur_sauf_les_defauts(self):
        lignes = NOYAU.read_text().splitlines()
        for l in lignes:
            if 'glob.glob(' in l or 'os.path.join(SORTIE' in l:
                self.assertNotIn('/kaggle/', l, l)
        defauts = [l for l in lignes if l.startswith(('ENTREES = ', 'SORTIE = '))]
        self.assertEqual(len(defauts), 2)
        for l in defauts:
            self.assertIn("os.environ.get('DOUBLER_", l)
            self.assertIn("or '/kaggle/", l, 'Kaggle par défaut')

    def test_script_du_noyau_fabrique_une_seule_fois(self):
        """construire_script est le seul fabricant : Kaggle, Modal et Runpod reçoivent le même texte."""
        with Dossier() as tmp:
            D = charger_doubler(tmp)
            script = D.construire_script({'pas': 30})
            self.assertNotIn('__REGLAGES__', script)
            self.assertNotIn("'__SOURCE__'", script)
            self.assertIn(base64.b64encode(json.dumps({'pas': 30}).encode()).decode(), script)


class SessionAilleurs(Base):
    """session_ailleurs et reprendre_ailleurs avec les moyens imités : ni Modal ni Runpod ne sont touchés."""

    def setUp(self):
        super().setUp()
        self.D.ETAT = self.tmp / 'etat'
        self.D.SESSION = self.D.ETAT / 'session-en-cours.json'
        voix = self.D.VOIX / 'jules'
        voix.mkdir(parents=True)
        (voix / 'reference.wav').write_bytes(b'REFERENCE')
        (voix / 'voix.json').write_text(json.dumps({'nom': 'Jules', 'hauteur_parole_mediane_hz': 110.0, 'duree_s': 20.0}))
        self.voix = self.D.charger_voix_spec('jules')
        self.kdir = self.tmp / 'sorties' / '.kaggle'
        self.kdir.mkdir(parents=True)
        tv = self.tmp / 'sorties' / 'Chanson' / 'travail' / 'jules'
        self.tr = {'id': 'v1', 'nom': 'Chanson', 'video': self.tmp / 'Chanson.mp4', 'entree': 'entree-a.flac',
                   'travail': tv.parent, 'tv': tv, 'duree_audio_s': 240.0, 'gain_entree': 1.0, 'taille_envoi': 3}
        self.a = argparse.Namespace(sortie=str(self.tmp / 'sorties'), pas=30, paroles=True, moteur='yingmusic',
                                    sans_nettoyage=False, mode='auto', octave='auto', comparer_modeles=False,
                                    comparer_pas=None, modele_parole=None, gpu='auto', transposer='auto',
                                    envoi_flac=16)
        self.abandons = []
        self.recu = {}
        for nom in ('estimer_noyau_s',):
            p = mock.patch.object(self.D, nom, lambda *a, **k: 1680)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(self.D, 'preparer_jeu', self.preparer_jeu)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(self.D, 'transposition_demandee', lambda a: 'auto')
        p.start()
        self.addCleanup(p.stop)

    def preparer_jeu(self, travaux, voix, dossier, bits=16):
        jeu = dossier / 'jeu'
        jeu.mkdir(parents=True, exist_ok=True)
        (jeu / 'voix-jules.wav').write_bytes(b'REFERENCE')
        (jeu / 'entree-a.flac').write_bytes(b'SON')
        self.recu['jeu'] = jeu
        return jeu, 3, 30

    def fabriquer(self, module, code=0, ecrit=True):
        def lancer(ctx, dire=print, sur_etat=None, **k):
            self.recu['ctx'] = ctx
            self.recu['k'] = k
            self.recu['script'] = Path(ctx['script']).read_text() if Path(ctx['script']).exists() else None
            self.recu['session_notee'] = json.loads(self.D.SESSION.read_text())
            if k.get('apres_creation'):
                k['apres_creation']({'pod_id': 'pod0123456789', 'pod_nom': 'voix-la-x', 'hote': '203.0.113.5',
                                     'port': 2222, 'duree_max_s': 5400, 'cree_epoch': 1.0})
                self.recu['session_avec_pod'] = json.loads(self.D.SESSION.read_text())
            if sur_etat:
                sur_etat('en_cours')
            retour = Path(ctx['retour'])
            (retour / 'v1').mkdir(parents=True, exist_ok=True)
            if ecrit:
                (retour / 'v1' / 'rapport.json').write_text('{}')
                (retour / 'v1' / 'voix_convertie.flac').write_bytes(b'FLAC')
                (retour / 'session.json').write_text(json.dumps({'gpus': {'utilisees': 1}}))
            return {'code': code, 'journal': ['fin'], 'etats': {}}
        return lancer

    def session(self, nom):
        return self.silencieux(self.D.session_ailleurs, nom, [self.tr], self.a, self.voix, self.kdir, ['Chanson'], None,
                               self.abandons.append, [])

    def test_modal(self):
        import calcul_modal
        with mock.patch.object(calcul_modal, 'pret', lambda: (True, '')), \
                mock.patch.object(calcul_modal, 'lancer', self.fabriquer(calcul_modal)):
            session, temps, envoi_s, estimation = self.session('modal')
        ctx = self.recu['ctx']
        self.assertEqual((ctx['gpu'], ctx['mode']), ('L4', 'doublage'))
        self.assertIn('SOURCE = \'', self.recu['script'], 'le même noyau que Kaggle, réglages et source remplacés')
        self.assertNotIn('__REGLAGES__', self.recu['script'])
        self.assertEqual(self.recu['session_notee']['calcul'], 'modal')
        self.assertEqual((temps['calcul'], temps['gpu_calcul']), ('modal', 'L4'))
        self.assertEqual(estimation, calcul.estimer_calcul(1680, 'modal', calcul.carte('L4', 'modal'))[0])
        self.assertRegex(self.sortie, r'Modal : carte L4, session estimée à \d+ min, coût estimé \d,\d\d \$')
        self.assertEqual((self.tr['tv'] / 'voix_convertie.flac').read_bytes(), b'FLAC', 'sorties rangées comme pour Kaggle')
        self.assertFalse(self.D.SESSION.exists(), 'session close')
        # la voix ne reste pas : ni le jeu local, ni le noyau écrit pour l'occasion
        self.assertFalse(self.recu['jeu'].exists())
        self.assertFalse((self.kdir / 'noyau-modal.py').exists())

    def test_runpod_note_le_pod(self):
        import calcul_runpod
        with mock.patch.object(calcul_runpod, 'pret', lambda: (True, '')), \
                mock.patch.object(calcul_runpod, 'lancer', self.fabriquer(calcul_runpod)):
            session, temps, envoi_s, estimation = self.session('runpod')
        self.assertEqual(self.recu['ctx']['gpu'], 'NVIDIA GeForce RTX 4090')
        self.assertEqual(self.recu['ctx']['duree_max_min'], 90, 'minuterie du pod : 90 min par défaut')
        s = self.recu['session_avec_pod']
        self.assertEqual((s['calcul'], s['pod_id'], s['hote'], s['port']), ('runpod', 'pod0123456789', '203.0.113.5', 2222))
        self.assertIn('pod supprimé seul après 90 min', self.sortie)
        self.assertEqual(temps['calcul'], 'runpod')

    def test_noyau_en_echec(self):
        import calcul_modal
        with mock.patch.object(calcul_modal, 'pret', lambda: (True, '')), \
                mock.patch.object(calcul_modal, 'lancer', self.fabriquer(calcul_modal, code=3, ecrit=False)):
            with self.assertRaises(self.D._Abandon) as e:
                self.session('modal')
        self.assertIn('sans résultat', str(e.exception))
        self.assertFalse(self.D.SESSION.exists())
        self.assertFalse(self.recu['jeu'].exists())

    def test_moyen_pas_pret(self):
        import calcul_modal
        with mock.patch.object(calcul_modal, 'pret', lambda: (False, 'jeton Modal absent')):
            with self.assertRaises(self.D._Abandon) as e:
                self.session('modal')
        self.assertIn('jeton Modal absent', str(e.exception))
        self.assertNotIn('jeu', self.recu, 'rien n\'est préparé ni envoyé')

    def test_interruption_clot_la_session(self):
        import calcul_modal

        def lancer(ctx, dire=print, sur_etat=None, **k):
            raise KeyboardInterrupt
        with mock.patch.object(calcul_modal, 'pret', lambda: (True, '')), mock.patch.object(calcul_modal, 'lancer', lancer):
            with self.assertRaises(KeyboardInterrupt):
                self.session('modal')
        self.assertFalse(self.D.SESSION.exists())
        self.assertFalse(self.recu['jeu'].exists(), 'le son n\'est pas gardé')

    def travaux_notes(self):
        return [{k: v for k, v in {**self.tr, 'video': str(self.tr['video']), 'travail': str(self.tr['travail']),
                                   'tv': str(self.tr['tv']), 'dossier': str(self.tr['travail'].parent)}.items()}]

    def noter(self, **extra):
        self.D.ETAT.mkdir(parents=True, exist_ok=True)
        self.D.ecrire_session({'noyau': None, 'poussee_le': 'x', 'pousse_epoch': 1.0, 'voix': 'jules', 'sortie': str(self.tmp / 'sorties'),
                               'travaux': self.travaux_notes(), **extra})

    def test_reprendre_modal_cloture_proprement(self):
        self.noter(calcul='modal')
        with self.assertRaises(SystemExit) as e:
            self.silencieux(self.D.reprendre_ailleurs, self.a, json.loads(self.D.SESSION.read_text()), [self.tr], ['Chanson'],
                            self.voix, self.kdir)
        self.assertIn('Modal', str(e.exception))
        self.assertIn('refaire', str(e.exception))
        self.assertFalse(self.D.SESSION.exists())
        self.assertTrue((self.D.ETAT / 'session-precedente.json').exists())

    def test_reprendre_runpod_sans_pod_nettoie(self):
        import calcul_runpod
        self.noter(calcul='runpod')
        appels = []
        with mock.patch.object(calcul_runpod, 'nettoyer', lambda dire=print: appels.append('nettoyer') or []):
            with self.assertRaises(SystemExit):
                self.silencieux(self.D.reprendre_ailleurs, self.a, json.loads(self.D.SESSION.read_text()), [self.tr],
                                ['Chanson'], self.voix, self.kdir)
        self.assertEqual(appels, ['nettoyer'])
        self.assertFalse(self.D.SESSION.exists())

    def test_reprendre_runpod_pod_disparu(self):
        import calcul_runpod
        self.noter(calcul='runpod', pod_id='pod0123456789', hote='h', port=22, duree_max_s=5400, cree_epoch=1.0)

        def reprendre(info, retour, dire=print, sur_etat=None):
            raise RuntimeError('Runpod : le pod pod0123456789 n\'existe plus')
        with mock.patch.object(calcul_runpod, 'reprendre', reprendre), \
                mock.patch.object(calcul_runpod, 'nettoyer', lambda dire=print: []):
            with self.assertRaises(SystemExit) as e:
                self.silencieux(self.D.reprendre_ailleurs, self.a, json.loads(self.D.SESSION.read_text()), [self.tr],
                                ['Chanson'], self.voix, self.kdir)
        self.assertIn('n\'existe plus', str(e.exception))
        self.assertFalse(self.D.SESSION.exists())

    def test_session_ailleurs_notee(self):
        self.assertFalse(self.D.session_ailleurs_notee())
        self.noter(calcul='kaggle')
        self.assertFalse(self.D.session_ailleurs_notee())
        self.noter(calcul='runpod')
        self.assertTrue(self.D.session_ailleurs_notee(), '--reprendre n\'exige pas Kaggle pour une session Runpod')


if __name__ == '__main__':
    unittest.main()
