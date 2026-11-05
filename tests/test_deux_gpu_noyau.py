"""Deux GPU dans le noyau (machine Kaggle « GPU T4 x2 ») : choix du nombre de cartes, plans de répartition, et surtout
le même résultat qu'avec une carte, au bit près.

Sans GPU ni modèle : les séparations du nettoyage sont imitées par des transformations fixes en numpy (faux
audio_separator), soundfile par des fichiers numpy, YingMusic-SVC par un faux moteur. Les étapes « parallèles » tournent
l'une après l'autre dans ce processus, dans un ordre quelconque (le résultat ne doit pas en dépendre). Tous les chemins
du noyau (/tmp, /kaggle) sont déplacés dans un dossier temporaire."""
import base64
import json
import os
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from commun import NOYAU, SR, Dossier, charger_noyau

N = charger_noyau()


def noyau_dans(dossier):
    """Le noyau comme module, ses chemins /tmp et /kaggle déplacés dans dossier."""
    src = NOYAU.read_text(encoding='utf-8').replace("'__REGLAGES__'", repr(base64.b64encode(b'{}').decode()))
    src = src.replace('/tmp/', f'{dossier}/t/').replace('/kaggle/', f'{dossier}/kaggle/')
    m = types.ModuleType('noyau_deux_gpu')
    m.__file__ = str(NOYAU)
    exec(compile(src, 'noyau_deux_gpu.py', 'exec'), m.__dict__)
    (Path(dossier) / 't').mkdir(parents=True, exist_ok=True)
    return m


# --- Faux soundfile (fichiers numpy sous le nom demandé) et faux audio_separator ----------------------------------------

def sf_ecrire(chemin, y, sr, subtype=None, format=None):
    with open(chemin, 'wb') as f:
        np.save(f, np.asarray(y, np.float32))


def sf_lire(chemin, dtype='float32', always_2d=False):
    with open(chemin, 'rb') as f:
        y = np.load(f)
    if always_2d and y.ndim == 1:
        y = y[:, None]
    return y.astype(dtype), SR


FAUX_SF = types.SimpleNamespace(write=sf_ecrire, read=sf_lire)
TRANSFORMATIONS = {   # modèle -> (piste gardée, autre piste) : des fonctions fixes et non linéaires
    'karaoke-a.ckpt': ('(Vocals)', '(Instrumental)', lambda x: 0.7 * x + 0.05 * np.tanh(3 * x)),
    'dereverb.ckpt': ('(noreverb)', '(reverb)', lambda x: 0.8 * x - 0.1 * x ** 3),
}


class FauxSeparateur:
    charges = []

    def __init__(self, model_file_dir, output_dir, **k):
        self.dossier = output_dir

    def load_model(self, model_filename):
        if model_filename not in TRANSFORMATIONS:
            raise RuntimeError(f'modèle inconnu {model_filename}')
        self.modele = model_filename
        FauxSeparateur.charges.append(model_filename)

    def separate(self, fichier):
        x, _ = sf_lire(fichier, always_2d=True)
        garde, autre, f = TRANSFORMATIONS[self.modele]
        base = os.path.splitext(os.path.basename(fichier))[0]
        noms = [f'{base}_{garde}_{self.modele}.wav', f'{base}_{autre}_{self.modele}.wav']
        sf_ecrire(os.path.join(self.dossier, noms[0]), f(x).astype(np.float32), SR)
        sf_ecrire(os.path.join(self.dossier, noms[1]), (x - f(x)).astype(np.float32), SR)
        return noms


def modules_faux():
    sep = types.ModuleType('audio_separator.separator')
    sep.Separator = FauxSeparateur
    torch = types.SimpleNamespace(load=lambda *a, **k: None, cuda=types.SimpleNamespace(empty_cache=lambda: None))
    return {'soundfile': FAUX_SF, 'audio_separator': types.ModuleType('audio_separator'), 'audio_separator.separator': sep,
            'torch': torch}


def voix_de_synthese(duree, graine):
    rng = np.random.default_rng(graine)
    t = np.arange(int(duree * SR)) / SR
    x = 0.3 * np.sin(2 * np.pi * 220 * t)[:, None] * np.array([1.0, 0.8]) + 0.05 * rng.standard_normal((len(t), 2))
    return x.astype(np.float32)


class ChoixDesCartes(unittest.TestCase):
    def test_nombre_de_cartes(self):
        self.assertEqual(N.cartes_a_utiliser('auto', 2), (2, None))
        self.assertEqual(N.cartes_a_utiliser(None, 2), (2, None))
        self.assertEqual(N.cartes_a_utiliser('auto', 4), (2, None), 'deux cartes au plus')
        self.assertEqual(N.cartes_a_utiliser(1, 2), (1, None), 'une carte demandée : la chaîne d\'avant, sans avis')
        self.assertEqual(N.cartes_a_utiliser('2', 2), (2, None))
        n, repli = N.cartes_a_utiliser('auto', 1)
        self.assertEqual(n, 1)
        self.assertIn('une à une', repli)
        n, repli = N.cartes_a_utiliser(2, 1)
        self.assertEqual(n, 1)
        self.assertIn('au lieu de 2', repli)
        self.assertEqual(N.cartes_a_utiliser('auto', 0)[0], 1)

    def test_compter_les_cartes(self):
        torch = types.SimpleNamespace(cuda=types.SimpleNamespace(device_count=lambda: 2, get_device_name=lambda i: f'T4 n°{i}'))
        self.assertEqual(N.compter_cartes(torch), (2, ['T4 n°0', 'T4 n°1']))
        sans = types.SimpleNamespace(cuda=types.SimpleNamespace(get_device_name=lambda i: 'T4'))
        self.assertEqual(N.compter_cartes(sans), (1, []), 'CUDA muet : une carte, la chaîne d\'avant')

    def test_environnement_de_chaque_carte(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CUDA_VISIBLE_DEVICES', None)
            self.assertEqual(N.env_carte(0, 1), {}, 'une carte : rien ne change')
            self.assertEqual([N.env_carte(k, 2) for k in (0, 1)], [{'CUDA_VISIBLE_DEVICES': '0'}, {'CUDA_VISIBLE_DEVICES': '1'}])
            os.environ['CUDA_VISIBLE_DEVICES'] = '3,5'
            self.assertEqual([N.env_carte(k, 2) for k in (0, 1)], [{'CUDA_VISIBLE_DEVICES': '3'}, {'CUDA_VISIBLE_DEVICES': '5'}])


class PlanDuNettoyage(unittest.TestCase):
    def test_une_carte_ordre_d_avant(self):
        self.assertEqual(N.plan_nettoyage([('v1', 30), ('v2', 200)], 1),
                         [[['karaoke', 'v1'], ['karaoke', 'v2'], ['dereverb', 'v1', 'lead'], ['dereverb', 'v1', 'choeurs'],
                           ['dereverb', 'v2', 'lead'], ['dereverb', 'v2', 'choeurs']]])

    def test_une_video_deux_cartes(self):
        self.assertEqual(N.plan_nettoyage([('v1', 230)], 2),
                         [[['karaoke', 'v1'], ['dereverb', 'v1', 'lead']], [['dereverb', 'v1', 'choeurs']]],
                         'karaoké puis voix principale sur la carte 0, chœurs sur la carte 1')

    def test_lot_chaque_tache_une_fois_et_equilibre(self):
        videos = [('v1', 60.0), ('v2', 240.0), ('v3', 120.0), ('v4', 30.0)]
        plan = N.plan_nettoyage(videos, 2)
        toutes = [tuple(t) for p in plan for t in p]
        attendues = [('karaoke', v) for v, _ in videos] + [('dereverb', v, p) for v, _ in videos for p in ('lead', 'choeurs')]
        self.assertEqual(sorted(toutes), sorted(attendues))
        for p in plan:
            genres = [t[0] for t in p]
            self.assertEqual(genres, sorted(genres, key=lambda g: g != 'karaoke'), 'karaokés avant dereverb sur chaque carte')
        duree = dict(videos)
        charges = [sum(duree[t[1]] for t in p) for p in plan]
        self.assertLess(max(charges) / sum(charges), 0.6)


class RepartitionDesMorceaux(unittest.TestCase):
    def test_chaque_morceau_une_fois_charges_proches(self):
        rng = np.random.default_rng(3)
        pistes = []
        for nom, k in (('lead_sec', 30), ('choeurs_sec', 12), ('secours', 2)):
            debuts = np.cumsum(rng.integers(SR, 8 * SR, k))
            pistes.append({'nom': nom, 'pas': 30, 'morceaux': [[i, int(a), int(a + rng.integers(SR // 2, 8 * SR)), 0, 0, 0]
                                                               for i, a in enumerate(debuts)]})
        parts = N.repartir_morceaux(pistes, 11025, 2, 25.0)
        tout = sorted(tuple(t) for p in parts for t in p)
        self.assertEqual(tout, sorted((pi, mi) for pi, p in enumerate(pistes) for mi in range(len(p['morceaux']))))
        for p in parts:
            self.assertEqual(p, sorted(p), 'dans l\'ordre des pistes puis des morceaux')
        cout = lambda pi, mi: (pistes[pi]['morceaux'][mi][2] - pistes[pi]['morceaux'][mi][1]) / SR + 25.5
        charges = [sum(cout(*t) for t in p) for p in parts]
        self.assertLess(abs(charges[0] - charges[1]) / sum(charges), 0.05)

    def test_un_seul_morceau(self):
        parts = N.repartir_morceaux([{'pas': 30, 'morceaux': [[0, 0, SR, 0, 0, 0]]}], 1000, 2)
        self.assertEqual(parts, [[[0, 0]], []])


class LancementEnParallele(unittest.TestCase):
    """etapes_a_part : deux vrais processus, chacun ne voyant que sa carte ; témoin de fin de chacun ; code de sortie."""

    def test_deux_processus(self):
        with Dossier() as d:
            m = noyau_dans(d)
            script = Path(d, 'etape.py')
            script.write_text('import json, os, sys, time\n'
                              't = json.load(open(sys.argv[2]))\n'
                              'time.sleep(0.2 * t["attente"])\n'
                              'json.dump({"etape": sys.argv[1], "vue": os.environ.get("CUDA_VISIBLE_DEVICES")}, '
                              'open(t["resultat"], "w"))\n'
                              'sys.exit(t["code"])\n')
            etat = Path(d, 'etat')
            etat.mkdir()
            travaux = [{'resultat': f'{d}/r/r{k}.json', 'etat': str(etat), 'attente': 2 - k, 'code': k} for k in (0, 1)]
            with mock.patch.object(m, 'script_complet', lambda: str(script)), mock.patch.object(m, 'log', lambda *a: None), \
                    mock.patch.dict(os.environ, {'CUDA_VISIBLE_DEVICES': '0,1'}):
                res = m.etapes_a_part('nettoyer', travaux, 2)
            self.assertEqual([(r['etape'], r['vue'], r['code'], r['carte']) for r in res],
                             [('nettoyer', '0', 0, 0), ('nettoyer', '1', 1, 1)])
            self.assertEqual(sorted(p.name for p in etat.iterdir()), ['fin_0', 'fin_1'])


class SeparationDUnLot(unittest.TestCase):
    """separer_videos : un lot partagé entre deux cartes (un processus par carte, chacun son dossier de travail) ; une
    carte en échec : ses vidéos reprises sur la carte 0 ; une vidéo seule, ou le modèle absent : un seul processus."""

    def lancer(self, d, tailles, codes=(0, 0), modele=True):
        m = noyau_dans(d)
        if modele:
            os.makedirs(f'{d}/t/modeles_sep', exist_ok=True)
            Path(d, 't', 'modeles_sep', 'vocals_mel_band_roformer.ckpt').write_bytes(b'')
        liste = []
        for i, taille in enumerate(tailles):
            Path(d, f'entree{i}.flac').write_bytes(b'x' * taille)
            liste.append({'id': f'v{i + 1}', 'entree': str(Path(d, f'entree{i}.flac'))})
        lances, seuls = [], []

        class Proc:
            def __init__(self, cmd, env):
                self.k = len(lances)
                lances.append(([t['id'] for t in json.loads(cmd[2])], cmd[3], env.get('CUDA_VISIBLE_DEVICES')))
                self.returncode = codes[self.k]

            def wait(self):
                return self.returncode
        with mock.patch.object(m.subprocess, 'Popen', Proc), mock.patch.object(m, 'log', lambda *a: None), \
                mock.patch.object(m, 'lancer', lambda *c: seuls.append([t['id'] for t in json.loads(c[2])])), \
                mock.patch.dict(os.environ, {'CUDA_VISIBLE_DEVICES': '0,1'}):
            m.separer_videos(liste, 2)
        return lances, seuls

    def test_lot_sur_deux_cartes(self):
        with Dossier() as d:
            lances, seuls = self.lancer(d, (100, 400, 300))
        self.assertEqual(lances, [(['v2'], f'{d}/t/sep/carte0', '0'), (['v3', 'v1'], f'{d}/t/sep/carte1', '1')])
        self.assertEqual(seuls, [])

    def test_carte_en_echec(self):
        with Dossier() as d:
            _, seuls = self.lancer(d, (100, 400, 300), codes=(0, 1))
        self.assertEqual(seuls, [['v3', 'v1']], 'vidéos de la carte 1 reprises sur la carte 0')

    def test_un_seul_processus(self):
        with Dossier() as d1, Dossier() as d2:
            self.assertEqual(self.lancer(d1, (100,)), ([], [['v1']]))
            self.assertEqual(self.lancer(d2, (100, 200), modele=False), ([], [['v1', 'v2']]))


class AssemblageAuBitPres(unittest.TestCase):
    def test_morceaux_convertis_ailleurs_meme_piste(self):
        """Morceaux qui se chevauchent à trois (marges plus longues qu'un morceau) : la somme garde l'ordre d'avant."""
        torch = types.SimpleNamespace(manual_seed=lambda g: None)
        x = np.random.default_rng(5).standard_normal(3 * SR).astype(np.float32)
        plan = [(0, 0, SR, 0, 0, 300), (1, SR, SR + 2000, 12, 300, 300), (2, SR + 2000, 2 * SR, -12, 300, 200),
                (3, 2 * SR, 3 * SR, 0, 200, 0)]
        conv = lambda xs, d: np.sin(xs * (1 + d / 24)).astype(np.float32)
        une_carte = N.assembler(x, plan, 4000, conv, 7, torch)
        faits = {}
        for k in (3, 1, 0, 2):   # convertis dans le désordre, comme sur deux cartes
            lo, hi = N.bornes_morceau(plan[k], 4000, len(x))
            faits[k] = conv(x[lo:hi], plan[k][3])
        deux_cartes = N.poser_morceaux(len(x), plan, 4000, lambda k, m, lo, hi: faits[k])
        self.assertTrue(np.array_equal(une_carte, deux_cartes))


class EtapesYing(unittest.TestCase):
    """etape_ying en morceaux sur deux cartes, puis assemblage : la piste d'une carte seule, au bit près."""

    class FauxMoteur:
        installation, chargement_s = {'origine': 'essai', 'duree_s': 0.0}, 1.0
        graine = []
        torch = types.SimpleNamespace(manual_seed=lambda g: EtapesYing.FauxMoteur.graine.__setitem__(slice(None), [g]),
                                      cuda=types.SimpleNamespace(empty_cache=lambda: None))

        def __init__(self, *a):
            pass

        def preparer_ref(self, chemin):
            return {'chemin': chemin, 'f': 1.0 + len(chemin) / 100}

        def convertir_morceau(self, xs, ref, dec, pas):
            # dépend de la graine du morceau, comme la diffusion (bruit de départ tiré après manual_seed)
            bruit = np.random.default_rng(EtapesYing.FauxMoteur.graine[0]).standard_normal(len(xs)).astype(np.float32)
            return (np.tanh(xs * ref['f'] * (1 + dec / 24)) + 0.01 * bruit).astype(np.float32)

    def travail(self, d, pistes):
        return {'reference': 'ref-jules.wav', 'pad': 4000, 'resultat': f'{d}/t/ying/resultat.json', 'outils': None,
                'checkpoint': None, 'pistes': pistes}

    def lancer(self, d, cartes, panne=None, ordre=1):
        m = noyau_dans(d)
        x = {nom: np.random.default_rng(i).standard_normal(4 * SR).astype(np.float32)
             for i, nom in enumerate(('lead_sec', 'choeurs_sec'))}
        pistes = [{'cle': f'v1|voix_convertie.flac|{nom}', 'entree': nom, 'pas': 30, 'sortie': f'{d}/t/ying/v1_0_{nom}.wav',
                   'morceaux': [[i, i * SR, (i + 1) * SR, (-12, 0, 12, 0)[i], 0 if i == 0 else 300, 0 if i == 3 else 300]
                                for i in range(4) if nom == 'lead_sec' or i % 2]} for nom in ('lead_sec', 'choeurs_sec')]
        lancees = []

        def une(etape, travail):
            assert etape == 'ying'
            lancees.append(('seule', travail.get('carte'), len(travail.get('taches') or [])))
            m.etape_ying(travail)
            return {**(m.lire_json(travail['resultat'], {}) or {}), 'code': 0, 'duree_s': 0.1}

        def plusieurs(etape, travaux, n):
            res = [None] * len(travaux)
            for k in list(range(len(travaux)))[::ordre]:
                if k == panne:
                    lancees.append(('panne', k, 0))
                    res[k] = {'code': 1, 'duree_s': 0.1, 'carte': k}   # carte arrêtée avant toute conversion
                    continue
                lancees.append(('carte', k, len(travaux[k]['taches'])))
                m.etape_ying(travaux[k])
                res[k] = {**(m.lire_json(travaux[k]['resultat'], {}) or {}), 'code': 0, 'duree_s': 0.1, 'carte': k}
            return res
        os.makedirs(f'{d}/t/ying', exist_ok=True)
        with mock.patch.dict(sys.modules, {'soundfile': FAUX_SF}), \
                mock.patch.object(m, 'MoteurYing', self.FauxMoteur), mock.patch.object(m, 'lire_mono', lambda c: x[c]), \
                mock.patch.object(m, 'log', lambda *a: None), mock.patch.object(m, 'etape_a_part', une), \
                mock.patch.object(m, 'etapes_a_part', plusieurs), \
                mock.patch.object(m, 'installer_ying', lambda o=None: {'origine': 'essai', 'duree_s': 0.0}), \
                mock.patch.dict(m.REGLAGES, {'graine': 1234}):
            travail = self.travail(d, pistes)
            res = m.ying_sur_cartes(travail, 2, 20.0) if cartes > 1 else une('ying', travail)
            sorties = {cle: sf_lire(p['sortie'])[0] for cle, p in res['pistes'].items() if p.get('sortie')}
        return res, sorties, lancees

    def test_deux_cartes_meme_pistes(self):
        with Dossier() as d1, Dossier() as d2, Dossier() as d3:
            r1, s1, _ = self.lancer(d1, 1)
            r2, s2, l2 = self.lancer(d2, 2)
            r3, s3, _ = self.lancer(d3, 2, ordre=-1)
        self.assertEqual([x[0] for x in l2], ['carte', 'carte'])
        self.assertEqual(sorted(s1), sorted(s2))
        for cle in s1:
            self.assertTrue(np.array_equal(s1[cle], s2[cle]), cle)
            self.assertTrue(np.array_equal(s1[cle], s3[cle]), f'{cle} : ordre des cartes sans effet')
        self.assertEqual({c: p['morceaux'] for c, p in r1['pistes'].items()}, {c: p['morceaux'] for c, p in r2['pistes'].items()})
        self.assertEqual(r2['installation'], {'origine': 'essai', 'duree_s': 0.0})
        self.assertEqual(len(r2['cartes']), 2)

    def test_carte_en_panne_reprise_sur_la_carte_0(self):
        with Dossier() as d1, Dossier() as d2:
            _, s1, _ = self.lancer(d1, 1)
            r2, s2, l2 = self.lancer(d2, 2, panne=1)
        self.assertEqual([x[0] for x in l2], ['carte', 'panne', 'seule'])
        self.assertTrue(r2['cartes'][-1]['reprise'])
        for cle in s1:
            self.assertTrue(np.array_equal(s1[cle], s2[cle]), cle)


class NettoyageSurDeuxCartes(unittest.TestCase):
    """etape_nettoyer en tâches sur deux cartes (témoins entre elles) et nettoyer_sur_cartes : les pistes sèches et les
    niveaux d'une carte seule, au bit près, pour une vidéo seule et pour un lot."""

    def lancer(self, d, cartes, durees=(3.0,), panne=None):
        m = noyau_dans(d)
        videos = []
        for i, duree in enumerate(durees):
            voix = f'{d}/t/sep/v{i + 1}_voix.wav'
            os.makedirs(os.path.dirname(voix), exist_ok=True)
            sf_ecrire(voix, voix_de_synthese(duree, i), SR)
            videos.append({'id': f'v{i + 1}', 'voix': voix, 'dossier': f'{d}/t/net/v{i + 1}'})
        base = {'videos': videos, 'karaoke': ['karaoke-absent.ckpt', 'karaoke-a.ckpt'], 'dereverb': 'dereverb.ckpt',
                'recouvrement': 4}

        def une(etape, travail):
            m.etape_nettoyer(travail)
            return {**(m.lire_json(travail['resultat'], {}) or {}), 'code': 0, 'duree_s': 0.1}

        def plusieurs(etape, travaux, n):
            # en même temps, comme sur deux cartes (chacune attend les karaokés de l'autre) ; témoin de fin de chaque carte
            res = [None] * len(travaux)

            def carte(k, t):
                try:
                    res[k] = {'code': 1, 'duree_s': 0.1, 'carte': k} if k == panne else {**une(etape, t), 'carte': k}
                finally:
                    with open(os.path.join(t['etat'], f'fin_{k}'), 'w') as f:
                        f.write('0')
            fils = [threading.Thread(target=carte, args=(k, t), daemon=True) for k, t in enumerate(travaux)]
            for f in fils:
                f.start()
            for f in fils:
                f.join(60)
                self.assertFalse(f.is_alive(), 'une carte attend sans fin')
            return res
        with mock.patch.dict(sys.modules, modules_faux()), mock.patch.object(m, 'log', lambda *a: None), \
                mock.patch.object(m, 'etape_a_part', une), mock.patch.object(m, 'etapes_a_part', plusieurs):
            if cartes > 1:
                res = m.nettoyer_sur_cartes(base, [(v['id'], duree) for v, duree in zip(videos, durees)], cartes)
            else:
                res = une('nettoyer', {**base, 'resultat': f'{d}/t/net/resultat.json'})
            pistes = {(vid, nom): sf_lire(r[nom])[0] for vid, r in res['videos'].items() for nom in ('lead_sec', 'choeurs_sec')
                      if r.get(nom)}
            restes = sorted(str(p.relative_to(d)) for p in Path(d, 't', 'net').rglob('*.wav'))
        return res, pistes, restes

    def comparer(self, durees, panne=None):
        with Dossier() as d1, Dossier() as d2:
            r1, p1, restes1 = self.lancer(d1, 1, durees)
            r2, p2, restes2 = self.lancer(d2, 2, durees, panne)
        self.assertEqual(sorted(p1), sorted(p2))
        self.assertEqual(len(p1), 2 * len(durees))
        for cle in p1:
            self.assertTrue(np.array_equal(p1[cle], p2[cle]), cle)
        for vid in r1['videos']:
            self.assertEqual(r1['videos'][vid]['niveaux_db'], r2['videos'][vid]['niveaux_db'], vid)
            self.assertNotIn('erreur', r2['videos'][vid])
            self.assertEqual(sorted(r1['videos'][vid]['durees_s']), sorted(r2['videos'][vid]['durees_s']))
        self.assertEqual((r1['karaoke'], r2['karaoke']), ('karaoke-a.ckpt', 'karaoke-a.ckpt'))
        self.assertEqual(restes1, restes2, 'mêmes fichiers laissés (voix principale et chœurs de travail effacés)')
        return r2

    def test_une_video(self):
        r = self.comparer((3.0,))
        self.assertEqual([c['taches'] and [t[0] for t in c['taches']] for c in r['cartes']],
                         [['karaoke_v1', 'dereverb_v1_lead'], ['dereverb_v1_choeurs']])

    def test_un_lot(self):
        self.comparer((2.0, 4.0, 1.0))

    def test_carte_en_panne(self):
        r = self.comparer((3.0, 2.0), panne=1)
        self.assertEqual(len(r['cartes']), 3, 'deux cartes, puis la reprise sur la carte 0')

    def test_taches_a_reprendre(self):
        with Dossier() as d:
            plan = N.plan_nettoyage([('v1', 1.0), ('v2', 1.0)], 1)[0]
            for nom, fin, carte in (('karaoke_v1', 'ok', 0), ('karaoke_v2', 'erreur', 0), ('dereverb_v1_lead', 'erreur', 1),
                                    ('dereverb_v1_choeurs', 'ok', 1)):
                Path(d, f'{nom}.{fin}').write_text(str(carte))
            self.assertEqual(N.taches_a_reprendre(plan, str(d)), [['dereverb', 'v1', 'lead']],
                             'erreur sur la carte 1 : refaite ; karaoké raté sur la carte 0 : ses dereverb abandonnés')


if __name__ == '__main__':
    unittest.main()
