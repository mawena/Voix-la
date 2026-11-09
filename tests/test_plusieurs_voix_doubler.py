"""Une voix par chanteur, côté doubler.py : syntaxe de --voix, voix combinée (id, affichage, ordre des clés, erreurs),
noms des sorties, réglages envoyés au noyau (une seule voix : ceux d'avant, tels quels), envoi des références, messages
après le doublage, puis une session entière (le noyau Kaggle imité : réglages reçus, session pour --reprendre, rapport
d'un duo) et --remix, avec l'auto-tune de la voix combinée. Voix et titres inventés (jules, marie)."""
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

import numpy as np

from commun import DOUBLER, FFMPEG, SR, Dossier, charger_doubler, ecrire_wav, python

with Dossier() as _h:
    D0 = charger_doubler(_h)
FAT1 = FFMPEG and D0.trouver_fat1() is not None and D0.ffmpeg_lv2()


def ajouter_voix(D, nom, affichage, f0):
    d = D.VOIX / nom
    d.mkdir(parents=True, exist_ok=True)
    (d / 'reference.wav').write_bytes(b'RIFF' + nom.encode())
    (d / 'voix.json').write_text(json.dumps({'nom': affichage, 'hauteur_parole_mediane_hz': f0, 'duree_s': 20.0}))


def ton(f, duree, niveau_db=-20, debut=0.0, fin=None, sr=SR):
    t = np.arange(int(duree * sr)) / sr
    y = sum(np.sin(2 * np.pi * f * h * t) / h for h in range(1, 6))
    y *= 10 ** (niveau_db / 20) / np.sqrt(np.mean(y ** 2))
    fin = duree if fin is None else fin
    return y * ((t >= debut) & (t < fin))


class Syntaxe(unittest.TestCase):
    def test_une_voix(self):
        self.assertEqual(D0.lire_spec_voix('jules'), (None, [(None, 'jules')]))
        with self.assertRaises(ValueError):
            D0.lire_spec_voix('Jules!')

    def test_duo_mixte_rangé_homme_puis_femme(self):
        self.assertEqual(D0.lire_spec_voix('femme:marie,homme:jules'), ('genre', [('homme', 'jules'), ('femme', 'marie')]))
        self.assertEqual(D0.lire_spec_voix(' Homme : jules , femme:marie '), ('genre', [('homme', 'jules'), ('femme', 'marie')]))

    def test_duo_ordre(self):
        self.assertEqual(D0.lire_spec_voix('chanteur2:marie,chanteur1:jules'),
                         ('ordre', [('chanteur1', 'jules'), ('chanteur2', 'marie')]))
        self.assertEqual(D0.lire_spec_voix('chanteur-1:jules,chanteur_2:marie')[0], 'ordre')

    def test_erreurs(self):
        for texte, motif in (('homme:jules,chanteur2:marie', 'ne se mélangent pas'), ('basse:jules', 'clé inconnue'),
                             ('homme:jules,homme:marie', 'deux fois'), ('homme:', 'clé:voix attendu'),
                             ('homme:Jules', 'nom de voix invalide'), (',', 'aucune voix'), ('jules,marie', 'clé:voix')):
            with self.subTest(texte=texte), self.assertRaisesRegex(ValueError, motif):
                D0.lire_spec_voix(texte)


class VoixCombinee(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.D = charger_doubler(self.tmp)
        ajouter_voix(self.D, 'jules', 'Jules', 110.0)
        ajouter_voix(self.D, 'marie', 'Marie', 210.0)

    def charger(self, texte):
        with contextlib.redirect_stdout(io.StringIO()) as sortie:
            v = self.D.charger_voix_spec(texte)
        return v, sortie.getvalue()

    def test_une_voix_comme_avant(self):
        v, _ = self.charger('jules')
        self.assertEqual(v, self.D.charger_voix('jules'))
        self.assertEqual(self.D.voix_membres(v), [v])

    def test_deux_voix(self):
        v, _ = self.charger('femme:marie,homme:jules')
        self.assertEqual((v['id'], v['affichage'], v['mode'], v['spec']),
                         ('jules+marie', 'Jules et Marie', 'genre', 'homme:jules,femme:marie'))
        self.assertEqual([(m['id'], m['cle']) for m in v['membres']], [('jules', 'homme'), ('marie', 'femme')])
        self.assertEqual((v['f0'], v['reference']), (110.0, self.D.VOIX / 'jules' / 'reference.wav'))
        self.assertEqual(self.D.charger_voix_spec(v['spec'])['id'], v['id'], 'la spécification gardée pour --reprendre')
        self.assertEqual(self.D.decrire_voix(v), 'homme → jules, femme → marie')
        o, _ = self.charger('chanteur1:marie,chanteur2:jules')
        self.assertEqual((o['id'], o['mode']), ('marie+jules', 'ordre'))
        self.assertEqual(self.D.decrire_voix(o), 'chanteur 1 → marie, chanteur 2 → jules')

    def test_une_seule_voix_differente(self):
        for texte in ('homme:jules', 'homme:jules,femme:jules'):
            v, texte_dit = self.charger(texte)
            self.assertEqual(v, self.D.charger_voix('jules'))
            self.assertIn('une seule voix différente', texte_dit)

    def test_voix_inconnue_ou_syntaxe(self):
        with self.assertRaises(SystemExit) as e:
            self.charger('homme:jules,femme:lea')
        self.assertIn('voix inconnue : lea', str(e.exception.code))
        self.assertIn('disponibles : jules, marie', str(e.exception.code))
        with self.assertRaises(SystemExit) as e:
            self.charger('homme:jules,chanteur2:marie')
        self.assertIn('--voix homme:jules,chanteur2:marie', str(e.exception.code))

    def test_noms_des_sorties(self):
        video = self.tmp / 'Une chanson.mp4'
        video.write_bytes(b'')
        a = argparse.Namespace(sortie=str(self.tmp / 'sorties'))
        v, _ = self.charger('homme:jules,femme:marie')
        tr = self.D.emplacements(video, 10, 45, a, v)
        self.assertEqual(tr['base'], 'Une chanson - voix Jules et Marie')
        self.assertEqual(tr['tv'], self.tmp / 'sorties' / 'Une chanson (extrait 0m10,0+0m45,0)' / 'travail' / 'jules+marie')
        self.assertEqual(self.D.base_autotune(tr['base']), 'Une chanson - voix Jules et Marie (auto-tune)')

    def test_reglages_du_noyau(self):
        a = argparse.Namespace(pas=30, paroles=True, moteur='yingmusic', sans_nettoyage=False, mode='auto', octave='auto',
                               comparer_modeles=False, comparer_pas=None, modele_parole=None)
        travaux = [{'id': 'v1', 'entree': 'entree-x.flac', 'gain_entree': 2.0}]
        un = self.D.reglages_noyau(a, self.D.charger_voix('jules'), None, travaux)
        # une voix : exactement les réglages d'avant (plus gpus, les cartes GPU : auto sans --gpu)
        attendu = {**self.D.NOYAU_REGLAGES, 'pas': 30, 'ancres': None, 'paroles': True, 'moteur_chant': 'yingmusic',
                   'nettoyage': True, 'gpus': 'auto', 'voix': {'nom': 'jules', 'fichier': 'voix-jules.wav', 'f0_parole_hz': 110.0,
                                               'facteur_chant': 2.0},
                   'travaux': [{'id': 'v1', 'entree': 'entree-x.flac', 'mode': 'auto', 'octave': 'auto',
                                'comparer_modeles': False, 'comparer_pas': None, 'gain_entree': 2.0,
                                'gain_entree_db': 6.021, 'retour_flac': True}]}
        self.assertEqual(json.dumps(un), json.dumps(attendu))
        v, _ = self.charger('femme:marie,homme:jules')
        deux = self.D.reglages_noyau(a, v, None, travaux)
        self.assertEqual(deux['voix'], attendu['voix'])
        self.assertEqual(deux['voix_cibles'], {'mode': 'genre', 'voix': [
            {'cle': 'homme', 'nom': 'jules', 'fichier': 'voix-jules.wav', 'f0_parole_hz': 110.0, 'facteur_chant': 2.0,
             'affichage': 'Jules'},
            {'cle': 'femme', 'nom': 'marie', 'fichier': 'voix-marie.wav', 'f0_parole_hz': 210.0, 'facteur_chant': 2.0,
             'affichage': 'Marie'}]})
        self.assertEqual(deux.pop('versions_duo'), 'toutes')   # deux versions de chaque passage, seulement en duo
        self.assertEqual({k: v for k, v in deux.items() if k != 'voix_cibles'}, un)

    def test_estimation(self):
        un = self.D.estimer_noyau_s([60.0], 30, str(self.tmp / 'rien'))
        self.assertEqual(self.D.estimer_noyau_s([60.0], 30, str(self.tmp / 'rien'), voix_cibles=1), un)
        self.assertGreater(self.D.estimer_noyau_s([60.0], 30, str(self.tmp / 'rien'), voix_cibles=2), un)
        self.assertEqual(self.D.estimer_noyau_s([60.0], 30, str(self.tmp / 'rien'), mode='parole', voix_cibles=2),
                         self.D.estimer_noyau_s([60.0], 30, str(self.tmp / 'rien'), mode='parole'))

    def test_envoi_de_toutes_les_references(self):
        v, _ = self.charger('homme:jules,femme:marie')
        self.D.JEU = 'compte/jeu'
        envoyes = {}

        def kaggle(*args):
            if args[:2] in (('datasets', 'version'), ('datasets', 'create')):
                envoyes['fichiers'] = sorted(f.name for f in (self.tmp / 'k' / 'jeu').iterdir())
                envoyes['message'] = args
                return 0, 'ok'
            return 0, 'ready ' + ' '.join(envoyes.get('fichiers', []))
        t = self.tmp / 't'
        t.mkdir()
        (t / 'original.wav').write_bytes(b'x' * 100)
        tr = {'travail': t, 'entree': 'entree-a.flac'}
        with mock.patch.object(self.D, 'kaggle', kaggle), mock.patch.object(self.D.time, 'sleep', lambda s: None), \
                mock.patch.object(self.D, 'encoder_flac', lambda w, f, bits=16: Path(f).write_bytes(b'f') or 1.0), \
                contextlib.redirect_stdout(io.StringIO()) as dit:
            self.D.envoyer_jeu([tr], v, self.tmp / 'k')
        self.assertEqual(envoyes['fichiers'], ['dataset-metadata.json', 'entree-a.flac', 'voix-jules.wav', 'voix-marie.wav'])
        self.assertIn('les voix jules et marie', dit.getvalue())
        self.assertIn('voix jules+marie', ' '.join(envoyes['message']))


def rapport_duo(entree=None, mode='chant'):
    """Rapport du noyau pour un duo homme:jules,femme:marie (ce que doubler_kaggle.py écrit)."""
    r = {'mode': mode, 'frise': [], 'travail': {'entree': entree},
         'voix_cibles': {'mode': 'genre', 'voix': [{'cle': 'homme', 'nom': 'jules'}, {'cle': 'femme', 'nom': 'marie'}]},
         'decision_hauteur': {'voix': 2, 'axe': 'genre', 'message': 'duo : homme → jules (+0), femme → marie (+0)',
                              'chanteurs': [{'nom': 'homme', 'voix': 'jules', 'decalage': 0},
                                            {'nom': 'femme', 'voix': 'marie', 'decalage': 0}]},
         'affectation': {'secondes_par_voix': {'jules': 2.0, 'marie': 2.0, 'ensemble': 1.5}, 'morceaux_en_duo': 1,
                         'message': 'duo'},
         'morceaux': [{'i': 0, 'debut_s': 0.0, 'fin_s': 3.0, 'voix': 'homme', 'voix_cible': 'jules', 'decalage': 0}]}
    if mode == 'parole':
        r = {'mode': 'parole', 'frise': [], 'travail': {'entree': entree},
             'voix_cibles': {'mode': 'genre', 'voix': [], 'message': 'parole : une seule voix cible, la première (jules)'}}
    return r


class Messages(unittest.TestCase):
    def test_duo_une_voix_parole(self):
        self.assertEqual(D0.lignes_voix_cibles({'mode': 'chant'}), [])
        l = D0.lignes_voix_cibles(rapport_duo())
        self.assertEqual(l[0], 'voix cibles, duo mixte (axe du genre) : homme → jules (+0 dt), femme → marie (+0 dt)')
        self.assertEqual(l[1], 'voix entendues : jules 2 s, marie 2 s, ensemble 2 s ; 1 passage(s) en duo (voix principale '
                               'et chœurs)')
        seule = {'voix_cibles': {'mode': 'genre'}, 'decision_hauteur': {
            'voix': 1, 'message': 'une seule voix détectée (femme) : tout prend la voix marie',
            'pourquoi_une_voix': 'empreintes trop proches'}}
        self.assertEqual(D0.lignes_voix_cibles(seule), ['ATTENTION : une seule voix détectée (femme) : tout prend la voix '
                                                        'marie (empreintes trop proches)'])
        self.assertIn('parole', D0.lignes_voix_cibles(rapport_duo(mode='parole'))[0])


def video(chemin, duree):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size=96x64:rate=25:duration={duree}',
                    '-f', 'lavfi', '-i', f'sine=frequency=330:duration={duree}:sample_rate={SR}', '-c:v', 'mpeg4',
                    '-g', '25', '-c:a', 'aac', str(chemin)], check=True)
    return chemin


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Session(unittest.TestCase):
    """Une session entière avec deux voix, le noyau Kaggle imité : extraction, envoi, réglages reçus, session pour
    --reprendre, rapatriement, mixage, noms des sorties, événement « sortie », messages ; puis --remix, auto-tune."""
    DUREE = 5.0

    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.D = charger_doubler(self.tmp)
        ajouter_voix(self.D, 'jules', 'Jules', 110.0)
        ajouter_voix(self.D, 'marie', 'Marie', 210.0)
        self.D.JEU, self.D.NOYAU, self.D.OUTILS = 'compte/jeu', 'compte/noyau', 'compte/outils'
        self.source = video(self.tmp / 'Une chanson.mp4', self.DUREE)
        self.evenements = self.tmp / 'ev.jsonl'
        self.D.EV.ouvrir(self.evenements)
        self.addCleanup(lambda: self.D.EV.f.close())
        self.D.corbeille = lambda fichiers: ([], [])   # jamais la vraie corbeille

    def args(self, **k):
        return argparse.Namespace(**{'sortie': str(self.tmp / 'sorties'), 'debut': None, 'duree': None, 'pas': 30,
                                     'paroles': False, 'moteur': 'yingmusic', 'sans_nettoyage': False, 'mode': 'auto',
                                     'octave': 'auto', 'comparer_modeles': False, 'comparer_pas': None,
                                     'modele_parole': None, 'voix_db': None, 'mix_v1': False, 'reverb_db': -13.0,
                                     'autotune': None, 'sans_autotune': False, 'autotune_gamme': None, **k})

    def faux_noyau(self, recu):
        def lancer_noyau(reglages, kdir, retour, attente_maxi, sources=(), videos=(), estimation_s=None, etapes=None,
                         apres_poussee=None, **k):
            recu['reglages'] = reglages
            apres_poussee(1000.0, 7)
            recu['session'] = json.loads(self.D.SESSION.read_text())
            (retour / 'v1').mkdir(parents=True)
            (retour / 'session.json').write_text(json.dumps({'debut_epoch': 1010.0, 'temps': {}}))
            t = Path(recu['session']['travaux'][0]['travail'])
            shutil.copy2(t / 'original.wav', retour / 'v1' / 'voix_source.wav')
            ecrire_wav(retour / 'v1' / 'voix.wav', ton(220, self.DUREE, -24) + ton(440, self.DUREE, -30, 2.0))
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(retour / 'v1' / 'voix.wav'), '-c:a', 'flac',
                            '-sample_fmt', 's32', str(retour / 'v1' / 'voix_convertie.flac')], check=True)
            (retour / 'v1' / 'voix.wav').unlink()
            (retour / 'v1' / 'rapport.json').write_text(json.dumps(rapport_duo(reglages['travaux'][0]['entree'])))
            return {'pousse_epoch': 1000.0, 'version_noyau': 7, 'noyau_depuis_envoi_s': 60.0, 'rapatriement_s': 1.0}
        return lancer_noyau

    def envoyer(self, travaux, voix, kdir, bits=16):
        self.envoyees = [m['id'] for m in self.D.voix_membres(voix)]
        for tr in travaux:
            tr['gain_entree'], tr['taille_envoi'] = 1.0, 1

    def test_session_puis_remix(self):
        recu = {}
        v = self.D.charger_voix_spec('femme:marie,homme:jules')
        with mock.patch.object(self.D, 'lancer_noyau', self.faux_noyau(recu)), \
                mock.patch.object(self.D, 'envoyer_jeu', self.envoyer), \
                mock.patch.object(self.D, 'lire_ancres', lambda: None), \
                contextlib.redirect_stdout(io.StringIO()) as dit:
            a = self.args()
            a.autotune_reglage = self.D.reglage_autotune(a, {})
            echecs = self.D.traiter_lot([str(self.source)], a, v)
        texte = dit.getvalue()
        self.assertEqual(echecs, [], texte)
        self.assertEqual(self.envoyees, ['jules', 'marie'])
        self.assertEqual([x['nom'] for x in recu['reglages']['voix_cibles']['voix']], ['jules', 'marie'])
        self.assertEqual(recu['reglages']['voix']['nom'], 'jules')
        self.assertEqual(recu['session']['voix'], 'homme:jules,femme:marie')
        precedente = json.loads((self.D.ETAT / 'session-precedente.json').read_text())
        self.assertEqual(precedente['voix'], 'homme:jules,femme:marie')
        dossier = self.tmp / 'sorties' / 'Une chanson'
        for f in ('Une chanson - voix Jules et Marie.mp4', 'Une chanson - voix Jules et Marie.mp3',
                  'travail/jules+marie/voix_convertie.flac', 'travail/jules+marie/mix.wav'):
            self.assertTrue((dossier / f).exists(), f)
        self.assertIn('une voix par chanteur (duo mixte) : homme → jules, femme → marie', texte)
        self.assertIn('voix cibles, duo mixte (axe du genre) : homme → jules (+0 dt), femme → marie (+0 dt)', texte)
        ev = [json.loads(l) for l in self.evenements.read_text().splitlines()]
        sortie = [e for e in ev if e['type'] == 'sortie'][-1]
        self.assertEqual(sortie['voix'], ['jules', 'marie'])
        self.assertEqual(sortie['mp4'], str(dossier / 'Une chanson - voix Jules et Marie.mp4'))
        self.assertTrue(sortie['rapport'].endswith('travail/jules+marie/rapport.json'))

        # --remix : même dossier, retrouvé par la même voix combinée (quel que soit l'ordre donné)
        avant = (dossier / 'Une chanson - voix Jules et Marie.mp3').stat().st_mtime_ns
        env = {**os.environ, 'HOME': str(self.tmp)}
        for k in ('VOIXLA_DONNEES', 'DOUBLER_VOIX', 'VOIXLA_KAGGLE'):
            env.pop(k, None)
        r = subprocess.run([python(), str(DOUBLER), str(self.source), '--remix', '--voix', 'homme:jules,femme:marie',
                            '--sortie', str(self.tmp / 'sorties'), '--sans-paroles', '--voix-db', '2'],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('(remix, voix homme → jules, femme → marie)', r.stdout)
        self.assertGreater((dossier / 'Une chanson - voix Jules et Marie.mp3').stat().st_mtime_ns, avant)
        self.assertEqual(json.loads((dossier / 'travail/jules+marie/rapport.json').read_text())['mix']['voix_db'], 2.0)

    @unittest.skipUnless(FAT1, 'x42-autotune (fat1) absent')
    def test_auto_tune_de_la_voix_combinee(self):
        recu = {}
        v = self.D.charger_voix_spec('homme:jules,femme:marie')
        with mock.patch.object(self.D, 'lancer_noyau', self.faux_noyau(recu)), \
                mock.patch.object(self.D, 'envoyer_jeu', self.envoyer), \
                mock.patch.object(self.D, 'lire_ancres', lambda: None), \
                contextlib.redirect_stdout(io.StringIO()) as dit:
            a = self.args(autotune=60, autotune_gamme='chromatique')
            a.autotune_reglage = self.D.reglage_autotune(a, {})
            echecs = self.D.traiter_lot([str(self.source)], a, v)
        self.assertEqual(echecs, [], dit.getvalue())
        dossier = self.tmp / 'sorties' / 'Une chanson'
        b = 'Une chanson - voix Jules et Marie (auto-tune)'
        for f in (f'{b}.mp4', f'{b}.mp3', 'travail/jules+marie/mix_autotune.wav'):
            self.assertTrue((dossier / f).exists(), f)
        rapport = json.loads((dossier / 'travail/jules+marie/rapport.json').read_text())
        self.assertTrue(rapport['autotune']['applique'])
        sortie = [json.loads(l) for l in self.evenements.read_text().splitlines() if '"sortie"' in l][-1]
        self.assertEqual(sortie['autotune']['mp4'], str(dossier / f'{b}.mp4'))
        self.assertEqual(sortie['voix'], ['jules', 'marie'])


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Commande(unittest.TestCase):
    def test_aide_et_voix_liste(self):
        with Dossier() as tmp:
            env = {**os.environ, 'HOME': str(tmp)}
            for k in ('VOIXLA_DONNEES', 'DOUBLER_VOIX', 'VOIXLA_KAGGLE'):
                env.pop(k, None)
            r = subprocess.run([python(), str(DOUBLER), '--help'], capture_output=True, text=True, env=env)
            self.assertIn('homme:NOM,femme:NOM', r.stdout)
            self.assertIn('chanteur1:NOM,chanteur2:NOM', r.stdout)
            r = subprocess.run([python(), str(DOUBLER), 'x.mp4', '--voix', 'homme:jules,chanteur1:marie'],
                               capture_output=True, text=True, env=env)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('ne se mélangent pas', r.stderr)


if __name__ == '__main__':
    unittest.main()
