"""Doubler sur Runpod, avec de FAUX runpodctl, ssh et scp (tests/faux_runpod) : aucun pod, aucune clé, aucun réseau. Le script
du pod (SCRIPT_POD) tourne pour de bon, dans un dossier. Vérifie surtout que le pod est TOUJOURS supprimé : fin normale,
noyau en échec, envoi refusé, création en échec, interruption, suppression qui résiste ; la minuterie du pod ; le
nettoyage des restes (seulement les pods voix-la-…) ; la reprise ; la voix effacée du pod."""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from commun import DEPOT, Dossier

sys.path.insert(0, str(DEPOT))
import calcul  # noqa: E402
import calcul_runpod as R  # noqa: E402

FAUX = Path(__file__).resolve().parent / 'faux_runpod'
NOYAU_IMITE = r'''
import json, os, sys
ent, sor = os.environ['DOUBLER_ENTREES'], os.environ['DOUBLER_SORTIE']
print('noyau imité : entrées', sorted(os.listdir(ent)), flush=True)
if os.path.exists(os.path.join(ent, 'echec')):
    sys.exit(3)
os.makedirs(os.path.join(sor, 'v1'), exist_ok=True)
open(os.path.join(sor, 'v1', 'voix_convertie.flac'), 'wb').write(b'VOIX')
json.dump({'gpus': {'utilisees': 1}}, open(os.path.join(sor, 'session.json'), 'w'))
'''


class Base(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.etat = self.tmp / 'runpod'
        self.pod = self.tmp / 'pod'
        self.etat.mkdir()
        self.pod.mkdir()
        for f in ('runpodctl', 'ssh', 'scp', 'bin/sleep'):
            os.chmod(FAUX / f, 0o755)
        env = {'FAUX_RUNPOD_DIR': str(self.etat), 'FAUX_POD_DIR': str(self.pod), 'VOIXLA_SSH': str(FAUX / 'ssh'),
               'VOIXLA_SCP': str(FAUX / 'scp'), 'VOIXLA_SSH_CLE': str(self.tmp / 'cle'), 'VOIXLA_SSH_PUB': 'ssh-rsa AAAA essai',
               'VOIXLA_RUNPOD_CLE': 'present', 'VOIXLA_RUNPOD_VITE': '1',
               'PATH': f"{FAUX / 'bin'}{os.pathsep}{os.environ['PATH']}"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(R, 'RUNPODCTL', FAUX / 'runpodctl')
        p2.start()
        self.addCleanup(p2.stop)
        self.script = self.tmp / 'noyau.py'
        self.script.write_text(NOYAU_IMITE)
        self.jeu = self.tmp / 'jeu'
        self.jeu.mkdir()
        (self.jeu / 'voix-jules.wav').write_bytes(b'REFERENCE-DE-LA-VOIX')
        (self.jeu / 'entree-a.flac').write_bytes(b'SON')
        self.retour = self.tmp / 'retour'
        self.dit = []

    def ctx(self, **k):
        return {'script': self.script, 'jeu': self.jeu, 'retour': self.retour, 'gpu': 'NVIDIA GeForce RTX 4090',
                'cartes': 1, 'duree_max_min': 90, 'suivi_s': 0.05, **k}

    def lancer(self, **k):
        return R.lancer(self.ctx(**k), self.dit.append, apres_creation=k.pop('apres_creation', None))

    def trace(self):
        f = self.etat / 'trace.txt'
        return f.read_text().splitlines() if f.exists() else []

    def pods(self):
        f = self.etat / 'pods.json'
        return json.loads(f.read_text()) if f.exists() else []

    def pods_voix_la(self):
        return [p for p in self.pods() if p['name'].startswith('voix-la-')]


class Pret(Base):
    def test_pret(self):
        self.assertTrue(R.pret()[0])
        with mock.patch.object(R, 'RUNPODCTL', self.tmp / 'absent'):
            self.assertIn('runpodctl absent', R.pret()[1])
        with mock.patch.dict(os.environ, {'VOIXLA_RUNPOD_CLE': ''}, clear=False), \
                mock.patch.object(R, 'CONFIG_RUNPOD', self.tmp / 'nulle-part'):
            os.environ.pop('RUNPOD_API_KEY', None)
            self.assertIn('clé Runpod absente', R.pret()[1])


class Doublage(Base):
    def test_session_complete_et_pod_supprime(self):
        creations = []
        r = self.lancer(apres_creation=creations.append)
        self.assertEqual(r['code'], 0)
        self.assertEqual((self.retour / 'v1' / 'voix_convertie.flac').read_bytes(), b'VOIX')
        self.assertTrue((self.retour / 'session.json').exists())
        journal = json.loads((self.retour / 'doublage-runpod.log').read_text())
        self.assertTrue(any('noyau imité' in l['data'] for l in journal))
        self.assertEqual(self.pods_voix_la(), [], 'le pod est supprimé')
        self.assertEqual([p['name'] for p in self.pods()], ['mon-autre-pod'], 'et rien d\'autre')
        self.assertEqual(creations[0]['pod_id'], 'pod0123456789')
        self.assertTrue(creations[0]['pod_nom'].startswith('voix-la-'))
        creer = next(l for l in self.trace() if l.startswith('pod create'))
        for attendu in ('--image runpod/pytorch:', '--gpu-id NVIDIA GeForce RTX 4090', '--ports 22/tcp', '--ssh', '--wait',
                        '--name voix-la-', 'PUBLIC_KEY'):
            self.assertIn(attendu, creer)
        self.assertIn('pod delete pod0123456789', self.trace())

    def test_minuterie_posee_avant_l_envoi_du_son(self):
        self.lancer()
        t = self.trace()
        iminuterie = next(i for i, l in enumerate(t) if 'pod.sh minuterie 5400' in l)
        isenvoi = next(i for i, l in enumerate(t) if l.startswith('scp') and '/jeu root@' in l)
        self.assertLess(iminuterie, isenvoi)

    def test_voix_effacee_du_pod(self):
        self.lancer()
        for reste in ('entrees', 'jeu', 'noyau.py', 'sortie'):
            self.assertFalse((self.pod / reste).exists(), reste)

    def test_noyau_en_echec_pod_supprime(self):
        (self.jeu / 'echec').write_bytes(b'')
        r = self.lancer()
        self.assertEqual(r['code'], 3)
        self.assertEqual(self.pods_voix_la(), [])

    def test_envoi_refuse_pod_supprime(self):
        with mock.patch.dict(os.environ, {'FAUX_SCP_ECHEC': 'envoi'}):
            with self.assertRaises(RuntimeError):
                self.lancer()
        self.assertEqual(self.pods_voix_la(), [])

    def test_rapatriement_refuse_pod_supprime(self):
        with mock.patch.dict(os.environ, {'FAUX_SCP_ECHEC': 'retour'}):
            with self.assertRaises(RuntimeError) as e:
                self.lancer()
        self.assertIn('rapatriement', str(e.exception))
        self.assertEqual(self.pods_voix_la(), [])

    def test_creation_en_echec_avec_pod_cree(self):
        with mock.patch.dict(os.environ, {'FAUX_RUNPOD_ECHEC_CREATION': '1'}):
            with self.assertRaises(RuntimeError):
                self.lancer()
        self.assertEqual(self.pods_voix_la(), [], 'le pod créé mais pas prêt est supprimé')

    def test_creation_en_echec_sans_identifiant(self):
        with mock.patch.dict(os.environ, {'FAUX_RUNPOD_SANS_ID': '1'}):
            with self.assertRaises(RuntimeError):
                self.lancer()
        self.assertEqual(self.pods_voix_la(), [], 'retrouvé par son nom voix-la-…')

    def test_interruption_pod_supprime(self):
        def interrompre(mot):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            R.lancer(self.ctx(), self.dit.append, sur_etat=interrompre)
        self.assertEqual(self.pods_voix_la(), [])

    def test_pod_injoignable_trop_longtemps(self):
        with mock.patch.dict(os.environ, {'FAUX_SSH_PANNE': '1'}):
            with self.assertRaises(RuntimeError):
                self.lancer()
        self.assertEqual(self.pods_voix_la(), [])

    def test_noyau_trop_long_pod_supprime(self):
        # un noyau qui ne finit jamais : la durée maximale (ici 1 s) arrête le suivi, le pod est supprimé
        self.script.write_text('import time\ntime.sleep(5)\n')
        with mock.patch.object(R, 'SUIVI_S', 0.05):
            with self.assertRaises(RuntimeError) as e:
                R.lancer(self.ctx(duree_max_min=0.02), self.dit.append)
        self.assertIn('toujours en cours', str(e.exception))
        self.assertEqual(self.pods_voix_la(), [])

    def test_suppression_qui_resiste(self):
        with mock.patch.dict(os.environ, {'FAUX_RUNPOD_SUPPRESSIONS_ECHOUEES': '2'}):
            self.lancer()
        self.assertEqual(self.pods_voix_la(), [], 'réussie au troisième essai')
        with mock.patch.dict(os.environ, {'FAUX_RUNPOD_SUPPRESSIONS_ECHOUEES': 'toujours'}):
            self.assertFalse(R.supprimer_pod('pod0123456789', self.dit.append))
        self.assertTrue(any('IL COÛTE' in l and '--nettoyer-runpod' in l for l in self.dit), self.dit)


class Minuterie(Base):
    def test_le_pod_se_supprime_seul(self):
        """minuterie.sh, exécuté pour de bon, appelle runpodctl pod delete $RUNPOD_POD_ID après le délai."""
        bin_ctl = self.tmp / 'bin_ctl'
        bin_ctl.mkdir()
        (bin_ctl / 'runpodctl').symlink_to(FAUX / 'runpodctl')
        with mock.patch.dict(os.environ, {'PATH': f"{bin_ctl}{os.pathsep}{os.environ['PATH']}"}):
            R.poser_minuterie('203.0.113.5', 2222, 3600, self.dit.append)
            for _ in range(60):
                if 'pod delete pod0123456789' in self.trace():
                    break
                time.sleep(0.1)
        self.assertIn('pod delete pod0123456789', self.trace())
        self.assertTrue(any('minuterie posée' in l for l in self.dit))


class Nettoyage(Base):
    def test_seuls_les_pods_voix_la_sont_supprimes(self):
        (self.etat / 'pods.json').write_text(json.dumps([
            {'id': 'autrepod0001', 'name': 'mon-autre-pod'}, {'id': 'resteun0001', 'name': 'voix-la-20261110-101010-ab12'},
            {'id': 'restedeux002', 'name': 'voix-la-20261109-090909-cd34'}, {'id': 'autrepod0003', 'name': 'voix-la'}]))
        self.assertEqual(sorted(R.nettoyer(self.dit.append)), ['restedeux002', 'resteun0001'])
        self.assertEqual([p['id'] for p in self.pods()], ['autrepod0001', 'autrepod0003'])

    def test_rien_a_nettoyer(self):
        self.assertEqual(R.nettoyer(self.dit.append), [])
        self.assertTrue(any('aucun pod' in l for l in self.dit))


class Reprise(Base):
    def info(self):
        return {'pod_id': 'pod0123456789', 'hote': '203.0.113.5', 'port': 2222, 'duree_max_s': 5400,
                'cree_epoch': time.time()}

    def test_pod_fini_rapatrie_puis_supprime(self):
        (self.etat / 'pods.json').write_text(json.dumps([{'id': 'pod0123456789', 'name': 'voix-la-x'}]))
        (self.pod / 'pod.sh').write_text(R.SCRIPT_POD)   # posé par la session interrompue, comme sur un vrai pod
        (self.pod / 'sortie' / 'v1').mkdir(parents=True)
        (self.pod / 'sortie' / 'v1' / 'voix_convertie.flac').write_bytes(b'VOIX')
        (self.pod / 'sortie' / 'session.json').write_text('{}')
        (self.pod / 'noyau.log').write_text('fin du noyau\n')
        (self.pod / 'fini').write_text('0\n')
        with mock.patch.object(R, 'SUIVI_S', 0.05):
            r = R.reprendre(self.info(), self.retour, self.dit.append)
        self.assertEqual(r['code'], 0)
        self.assertTrue((self.retour / 'session.json').exists())
        self.assertEqual(self.pods_voix_la(), [])

    def test_pod_disparu(self):
        with self.assertRaises(RuntimeError) as e:
            R.reprendre(self.info(), self.retour, self.dit.append)
        self.assertIn('n\'existe plus', str(e.exception))


class Lecture(unittest.TestCase):
    def test_hote_ssh(self):
        self.assertEqual(calcul.hote_ssh('ssh root@203.0.113.5 -p 2222 -i ~/.ssh/id_rsa'), ('203.0.113.5', 2222))
        self.assertEqual(calcul.hote_ssh('ssh -p 2222 root@pod.example.net'), ('pod.example.net', 2222))
        self.assertEqual(calcul.hote_ssh(json.dumps({'runtime': {'ports': [{'ip': '198.51.100.7', 'privatePort': 22,
                                                                          'publicPort': 41022}]}})), ('198.51.100.7', 41022))
        self.assertEqual(calcul.hote_ssh(json.dumps({'ip': '198.51.100.7', 'port': 41022})), ('198.51.100.7', 41022))
        self.assertIsNone(calcul.hote_ssh('rien'))

    def test_identifiant_et_liste(self):
        self.assertEqual(calcul.identifiant_pod('{"id": "abc123def456", "name": "x"}'), 'abc123def456')
        self.assertEqual(calcul.identifiant_pod('avis\n{"id": "abc123def456"}'), 'abc123def456')
        self.assertIsNone(calcul.identifiant_pod('error: not ready'))
        self.assertEqual(calcul.pods_voix_la('[{"id":"a1","name":"voix-la-x"},{"id":"b2","name":"ailleurs"}]'),
                         [('a1', 'voix-la-x')])
        self.assertEqual(calcul.pods_voix_la('{"pods":[{"id":"a1","name":"voix-la-x"}]}'), [('a1', 'voix-la-x')])


if __name__ == '__main__':
    unittest.main()
