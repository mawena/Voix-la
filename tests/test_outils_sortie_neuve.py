"""Outils préparés (doubler --preparer-outils) et dossier de sortie neuf (--sortie) : la fiche outils.json est cherchée
dans <sortie>/.kaggle/, dans l'état commun (pour le même noyau d'outils), puis dans le dossier des sorties par défaut ;
--preparer-outils la laisse aussi dans l'état commun ; un doublage vers un dossier neuf monte les outils et estime sa
durée d'après l'historique du dossier par défaut. Comptes, voix et titres inventés ; HOME est un dossier temporaire ;
le noyau Kaggle est imité."""
import argparse
import contextlib
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from commun import FFMPEG, SR, Dossier, charger_doubler

FICHE = {'commit': 'abc1234567', 'python': '3.11', 'wheels': ['a.whl'], 'tailles_mo': {}, 'yingmusic': {},
         'paroles': {'whisper': 'paroles/whisper-large-v3', 'traducteur': 'paroles/nllb'}}


class Base(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.D = charger_doubler(self.tmp)
        self.D.JEU, self.D.NOYAU, self.D.OUTILS = 'jules/jeu', 'jules/noyau', 'jules/outils'
        self.neuve = self.tmp / 'ailleurs' / 'sorties-neuves'

    def ecrire(self, chemin, fiche=FICHE):
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(fiche))
        return chemin


class Fiche(Base):
    def test_aucune(self):
        self.assertIsNone(self.D.fiche_outils(self.neuve))

    def test_dossier_de_sortie_d_abord(self):
        propre = self.ecrire(self.neuve / '.kaggle' / 'outils.json')
        self.ecrire(self.D.SORTIES / '.kaggle' / 'outils.json')
        self.assertEqual(self.D.fiche_outils(self.neuve), propre)

    def test_sortie_neuve_dossier_par_defaut(self):
        defaut = self.ecrire(self.D.SORTIES / '.kaggle' / 'outils.json')
        self.assertFalse((self.neuve / '.kaggle').exists())
        self.assertEqual(self.D.fiche_outils(self.neuve), defaut)
        self.assertEqual(self.D.fiche_outils(str(self.neuve)), defaut, 'chemin en texte, comme a.sortie')

    def test_etat_commun_du_meme_noyau_seulement(self):
        commune = self.ecrire(self.D.ETAT / 'outils.json', {**FICHE, 'noyau': 'jules/outils'})
        self.assertEqual(self.D.fiche_outils(self.neuve), commune)
        self.ecrire(commune, {**FICHE, 'noyau': 'marie/outils'})
        self.assertIsNone(self.D.fiche_outils(self.neuve), 'outils d\'un autre compte : ignorés')
        commune.write_text('{abîmé')
        self.assertIsNone(self.D.fiche_outils(self.neuve))

    def test_preparer_outils_garde_la_fiche_pour_tous_les_dossiers(self):
        def lancer_noyau(reglages, kdir, retour, attente_maxi, **k):
            self.assertEqual(k['noyau'], 'jules/outils')
            retour.mkdir(parents=True)
            (retour / 'outils.json').write_text(json.dumps(FICHE))
            return {'noyau_depuis_envoi_s': 600.0}
        a = argparse.Namespace(sortie=str(self.tmp / 'sorties-a'))
        with mock.patch.object(self.D, 'lancer_noyau', lancer_noyau), contextlib.redirect_stdout(io.StringIO()):
            self.D.preparer_outils(a)
        self.assertEqual(json.loads((self.tmp / 'sorties-a' / '.kaggle' / 'outils.json').read_text()), FICHE)
        commune = json.loads((self.D.ETAT / 'outils.json').read_text())
        self.assertEqual(commune, {**FICHE, 'noyau': 'jules/outils'})
        # un autre dossier de sortie, neuf, et un autre dossier des données : les outils sont retrouvés
        self.D.SORTIES = self.tmp / 'autres-donnees' / 'sorties'
        self.assertEqual(self.D.fiche_outils(self.neuve), self.D.ETAT / 'outils.json')


def video(chemin, duree):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size=96x64:rate=25:duration={duree}',
                    '-f', 'lavfi', '-i', f'sine=frequency=330:duration={duree}:sample_rate={SR}', '-c:v', 'mpeg4',
                    '-g', '25', '-c:a', 'aac', str(chemin)], check=True)
    return chemin


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Doublage(Base):
    """traiter_lot vers un dossier neuf : le noyau imité s'arrête dès qu'il a reçu ses sources (rien n'est mixé)."""

    def setUp(self):
        super().setUp()
        d = self.D.VOIX / 'jules'
        d.mkdir(parents=True)
        (d / 'reference.wav').write_bytes(b'RIFFjules')
        (d / 'voix.json').write_text(json.dumps({'nom': 'Jules', 'hauteur_parole_mediane_hz': 110.0, 'duree_s': 20.0}))
        self.source = video(self.tmp / 'Une chanson.mp4', 3.0)

    def doubler(self):
        recu = {}

        def lancer_noyau(reglages, kdir, retour, attente_maxi, sources=(), **k):
            recu['sources'] = list(sources)
            raise RuntimeError('arrêt de l\'essai')

        def estimer(durees, pas, sorties, *a, **k):
            recu['historique'] = sorties
            return 600

        def envoyer(travaux, voix, kdir):
            for tr in travaux:
                tr['gain_entree'], tr['taille_envoi'] = 1.0, 1
        a = argparse.Namespace(sortie=str(self.neuve), debut=None, duree=None, pas=30, paroles=True, moteur='yingmusic',
                               sans_nettoyage=False, mode='auto', octave='auto', comparer_modeles=False,
                               comparer_pas=None, modele_parole=None, gpu='auto')
        with mock.patch.object(self.D, 'lancer_noyau', lancer_noyau), mock.patch.object(self.D, 'envoyer_jeu', envoyer), \
                mock.patch.object(self.D, 'estimer_noyau_s', estimer), \
                mock.patch.object(self.D, 'lire_ancres', lambda: None), \
                contextlib.redirect_stdout(io.StringIO()) as dit:
            echecs = self.D.traiter_lot([str(self.source)], a, self.D.charger_voix_spec('jules'))
        self.assertEqual(echecs, [str(self.source)], dit.getvalue())
        self.assertIn('arrêt de l\'essai', dit.getvalue())
        return recu, dit.getvalue()

    def test_outils_du_dossier_par_defaut_montes(self):
        self.ecrire(self.D.SORTIES / '.kaggle' / 'outils.json')
        recu, dit = self.doubler()
        self.assertEqual(recu['sources'], ['jules/outils'])
        self.assertNotIn('outils non préparés', dit)
        self.assertEqual([Path(s).resolve() for s in recu['historique']],
                         [self.neuve.resolve(), Path(self.D.SORTIES).resolve()])

    def test_sans_outils_prevenu(self):
        recu, dit = self.doubler()
        self.assertEqual(recu['sources'], [])
        self.assertIn('outils non préparés sur Kaggle', dit)


if __name__ == '__main__':
    unittest.main()
