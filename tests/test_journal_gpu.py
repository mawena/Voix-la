"""Journal durable des sessions GPU de doubler (sessions-gpu.jsonl) : une ligne au départ, une à la fin (reussie, echouee,
annulee, abandonnee), sans chemin ni nom de vidéo, et une écriture qui échoue ne fait jamais échouer le doublage.
Noyau, suivi et rapatriement sont imités : aucune session Kaggle, Modal ni Runpod ne part ; HOME est temporaire."""
import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from commun import Dossier, charger_doubler


class Journal(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.D = charger_doubler(self.home)
        self.D.NOYAU = 'jules/doublage-voix-gpu'
        self.D.ETAT = self.tmp / 'etat'
        self.fichier = self.D.ETAT / 'sessions-gpu.jsonl'

    def lignes(self):
        return [json.loads(l) for l in self.fichier.read_text(encoding='utf-8').splitlines()]

    def lancer(self, suivi, **k):
        """lancer_noyau avec poussée, suivi et rapatriement imités."""
        with mock.patch.object(self.D, 'pousser_noyau', lambda *a, **kw: (1000.0, 7)), \
                mock.patch.object(self.D, 'suivre_noyau', suivi), \
                mock.patch.object(self.D, 'rapatrier_noyau', lambda *a, **kw: 3.0), \
                mock.patch.object(self.D.time, 'time', lambda: 1900.0), \
                contextlib.redirect_stdout(io.StringIO()):
            return self.D.lancer_noyau({}, self.tmp, self.tmp / 'retour', 3600, estimation_s=1700, **k)

    def test_session_reussie(self):
        self.lancer(lambda *a, **k: 'COMPLETE', cartes=2)
        debut, fin = self.lignes()
        self.assertEqual((debut['evt'], fin['evt']), ('debut', 'fin'))
        self.assertEqual(debut['id'], fin['id'])
        self.assertEqual(debut['fournisseur'], 'kaggle')
        self.assertEqual(debut['nom'], 'doublage-voix-gpu', 'sans le compte Kaggle')
        self.assertEqual((debut['debut'], debut['cartes'], debut['version'], debut['commande']), (1000.0, 2, 7, 'doubler'))
        self.assertEqual(debut['machine'], self.D.MACHINE)
        self.assertEqual((fin['fin'], fin['duree_s'], fin['statut']), (1900.0, 900.0, 'reussie'))

    def test_aucun_chemin_ni_compte(self):
        self.lancer(lambda *a, **k: 'COMPLETE', commande='comparer')
        texte = self.fichier.read_text(encoding='utf-8')
        self.assertNotIn(str(self.tmp), texte)
        self.assertNotIn('jules', texte)
        self.assertNotIn('/', texte.replace('\\/', ''))
        self.assertEqual(self.lignes()[0]['commande'], 'comparer')

    def test_noyau_en_erreur(self):
        self.lancer(lambda *a, **k: 'ERROR')
        self.assertEqual(self.lignes()[-1]['statut'], 'echouee')

    def test_statuts_des_exceptions(self):
        for exc, statut in ((self.D.KaggleIndisponible('file'), 'abandonnee'), (KeyboardInterrupt(), 'annulee'),
                            (RuntimeError('x'), 'echouee')):
            self.fichier.unlink(missing_ok=True)

            def suivi(*a, **k):
                raise exc
            with self.assertRaises(type(exc)):
                self.lancer(suivi)
            self.assertEqual(self.lignes()[-1]['statut'], statut)
            self.assertEqual(self.lignes()[-1]['duree_s'], 900.0)

    def test_ajout_seul(self):
        self.lancer(lambda *a, **k: 'COMPLETE')
        self.lancer(lambda *a, **k: 'COMPLETE')
        self.assertEqual(len(self.lignes()), 4)

    def test_une_ecriture_qui_echoue_n_arrete_rien(self):
        self.D.ETAT.parent.mkdir(parents=True, exist_ok=True)
        self.D.ETAT.write_text('un fichier, pas un dossier')   # mkdir et open échouent
        etat = self.lancer(lambda *a, **k: 'COMPLETE')
        self.assertEqual(etat['version_noyau'], 7, 'le doublage va jusqu\'au bout')
        self.D.journal_gpu_debut('kaggle', None, 'pas un nombre', 'doubler')   # ni l'argument illisible ne lève
        self.D.journal_gpu_fin('kaggle', 'pas un nombre', 'reussie')

    def test_reprise_refait_le_meme_identifiant(self):
        self.D.journal_gpu_debut('modal', 'modal', 1234.7, 'doubler', cartes=1, machine='L4')
        self.D.journal_gpu_fin('modal', 1234.7, 'abandonnee', fin=1300.0)
        debut, fin = self.lignes()
        self.assertEqual(debut['id'], fin['id'])
        self.assertEqual(debut['machine'], 'L4')
        self.assertNotIn('version', debut, 'les champs inconnus ne sont pas écrits')


if __name__ == '__main__':
    unittest.main()
