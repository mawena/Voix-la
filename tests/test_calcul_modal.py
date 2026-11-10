"""Doubler sur Modal, avec un FAUX module modal (tests/faux_modal) : aucun compte, aucun réseau, aucune application.
Vérifie ce que calcul_modal.py et calcul_modal_hote.py demandent à Modal (carte, durée, volumes), le chemin du son (volume
éphémère effacé, rien de la voix dans le volume des modèles), les sorties rapatriées, les échecs et le mode « outils »."""
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

from commun import DEPOT, Dossier

sys.path.insert(0, str(DEPOT))
import calcul  # noqa: E402
import calcul_modal  # noqa: E402

FAUX = Path(__file__).resolve().parent / 'faux_modal'

NOYAU_IMITE = r'''
import json, os, sys
ent, sor = os.environ['DOUBLER_ENTREES'], os.environ['DOUBLER_SORTIE']
print('noyau imité : entrées', sorted(os.listdir(ent)), flush=True)
if os.path.exists(os.path.join(ent, 'echec')):
    sys.exit(3)
if os.environ.get('MODE_OUTILS'):
    json.dump({'commit': 'abc1234567', 'tailles_mo': {}}, open(os.path.join(sor, 'outils.json'), 'w'))
    open(os.path.join(sor, 'seed-vc.tar'), 'wb').write(b'x' * 10)
    sys.exit(0)
os.makedirs(os.path.join(sor, 'v1'), exist_ok=True)
open(os.path.join(sor, 'v1', 'voix_convertie.flac'), 'wb').write(os.urandom(2500000))   # plus de 2 Mio : plusieurs morceaux
json.dump({'gpus': {'utilisees': 1}}, open(os.path.join(sor, 'session.json'), 'w'))
'''


class Base(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.monde = self.tmp / 'monde'
        self.monde.mkdir()
        env = {'FAUX_MODAL_DIR': str(self.monde), 'PYTHONPATH': str(FAUX), 'VOIXLA_MODAL_PYTHON': sys.executable,
               'VOIXLA_MODAL_JETON': 'present'}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.script = self.tmp / 'noyau.py'
        self.script.write_text(NOYAU_IMITE)
        self.jeu = self.tmp / 'jeu'
        self.jeu.mkdir()
        (self.jeu / 'voix-jules.wav').write_bytes(b'REFERENCE-DE-LA-VOIX')
        (self.jeu / 'entree-a.flac').write_bytes(b'SON')
        self.retour = self.tmp / 'retour'
        self.lignes = []

    def lancer(self, **k):
        ctx = {'script': self.script, 'jeu': self.jeu, 'retour': self.retour, 'gpu': 'L4', 'timeout_s': 3600,
               'mode': 'doublage', **k}
        etats = []
        r = calcul_modal.lancer(ctx, self.lignes.append, etats.append)
        return r, etats

    def trace(self):
        return json.loads((self.monde / 'trace.json').read_text())


class Pret(Base):
    def test_pret(self):
        self.assertEqual(calcul_modal.pret(), (True, ''))
        with mock.patch.dict(os.environ, {'VOIXLA_MODAL_JETON': 'absent'}):
            ok, raison = calcul_modal.pret()
            self.assertFalse(ok)
            self.assertIn('modal setup', raison)
        with mock.patch.dict(os.environ, {'VOIXLA_MODAL_PYTHON': str(self.tmp / 'nulle-part')}):
            ok, raison = calcul_modal.pret()
            self.assertFalse(ok)
            self.assertIn('client modal absent', raison)
        with self.assertRaises(RuntimeError):
            with mock.patch.dict(os.environ, {'VOIXLA_MODAL_JETON': 'absent'}):
                self.lancer()

    def test_versions_comme_le_noyau_kaggle(self):
        """Les bibliothèques de l'image Modal sont celles de calcul.py (relevées sur le noyau Kaggle)."""
        texte = (DEPOT / 'calcul_modal_hote.py').read_text()
        paquets = re.search(r'PAQUETS = (\[.*?\])\n', texte, re.S).group(1)
        self.assertEqual(eval(paquets), calcul.paquets_noyau())
        self.assertIn(f"PYTHON_NOYAU = '{calcul.PYTHON_NOYAU}'", texte)
        self.assertIn(f"INDEX_TORCH = '{calcul.INDEX_TORCH}'", texte)


class Doublage(Base):
    def test_session_complete(self):
        r, etats = self.lancer()
        self.assertEqual(r['code'], 0)
        self.assertEqual(etats, ['application', 'demarrage', 'en_cours', 'fini'])
        self.assertEqual((self.retour / 'v1' / 'voix_convertie.flac').stat().st_size, 2500000, 'sorties par morceaux')
        self.assertTrue((self.retour / 'session.json').exists())
        journal = json.loads((self.retour / 'doublage-modal.log').read_text())
        self.assertTrue(any('noyau imité' in l['data'] for l in journal), 'journal au format Kaggle')
        t = self.trace()
        self.assertEqual(t['options'], {'gpu': 'L4', 'timeout': 3600, 'volumes': ['/echange', '/entrees/outils']})
        self.assertEqual(t['python'], '3.13')
        self.assertIn('torch==2.11.0', t['pip'])
        self.assertEqual(t['env']['HF_HOME'], '/entrees/outils/hf')
        self.assertTrue(t['ephemere_supprime'] and t['app_arretee'])

    def test_la_voix_ne_reste_nulle_part(self):
        self.lancer()
        self.assertFalse((self.monde / 'echange').exists(), 'volume éphémère supprimé')
        # le conteneur a effacé le son et la référence ; le volume des modèles (persistant) n'en a jamais reçu
        restes = [p.name for p in (self.monde / 'entrees').rglob('*') if p.is_file()] if (self.monde / 'entrees').exists() else []
        self.assertEqual(restes, [])
        persistants = [p.name for p in (self.monde / 'modeles').rglob('*') if p.is_file()] if (self.monde / 'modeles').exists() else []
        self.assertEqual(persistants, [])
        self.assertNotIn('commits', self.trace(), 'pas de commit du volume des modèles pour un doublage')

    def test_carte_et_duree_transmises(self):
        self.lancer(gpu='A10', timeout_s=1800)
        self.assertEqual(self.trace()['options']['gpu'], 'A10')
        self.assertEqual(self.trace()['options']['timeout'], 1800)

    def test_noyau_en_echec(self):
        (self.jeu / 'echec').write_bytes(b'')
        r, _ = self.lancer()
        self.assertEqual(r['code'], 3)
        self.assertFalse((self.retour / 'session.json').exists())

    def test_client_qui_echoue(self):
        with mock.patch.dict(os.environ, {'PYTHONPATH': str(self.tmp)}):   # pas de module modal du tout
            with self.assertRaises(RuntimeError) as e:
                self.lancer()
        self.assertIn('client a échoué', str(e.exception))

    def test_outils_dans_le_volume_public(self):
        with mock.patch.dict(os.environ, {'MODE_OUTILS': '1'}):
            r, _ = self.lancer(mode='outils', jeu=None)
        self.assertEqual(r['code'], 0)
        self.assertEqual(sorted(p.name for p in self.retour.iterdir()), ['doublage-modal.log', 'outils.json'],
                         'seul outils.json revient, pas les gigaoctets d\'outils')
        # DOUBLER_SORTIE était le montage du volume des modèles (<racine>/entrees/outils)
        self.assertEqual((self.monde / 'entrees' / 'outils' / 'seed-vc.tar').read_bytes(), b'x' * 10)
        self.assertEqual(self.trace()['commits'], ['voix-la-modeles'])


if __name__ == '__main__':
    unittest.main()
