"""Paroles sur une carte pendant que YingMusic-SVC convertit sur l'autre (machine Kaggle « GPU T4 x2 ») : la dernière
carte fait d'abord les paroles, puis rejoint la conversion ; les pistes restent celles d'une carte seule, au bit près, et
l'étape paroles reçoit le même travail qu'après la conversion.

Sans GPU ni modèle : YingMusic-SVC est un faux moteur, les cartes des fils de ce processus (ou de vrais processus qui ne
font qu'attendre), torch et transformers de faux modules. Tous les chemins du noyau (/tmp, /kaggle) sont déplacés dans
un dossier temporaire. Noms inventés."""
import json
import os
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from commun import NOYAU, SR, Dossier, charger_noyau, note
from test_deux_gpu_noyau import FAUX_SF, noyau_dans, sf_lire
from test_plusieurs_voix_chaine import (SCENE, VOIX, FauxSeedVC, T, Timbres, ecrire_son, faux_librosa, faux_torch,
                                        hauteur_dominante, hauteur_trames, lire_son, reechantillonner, reglages,
                                        sans_durees, synthese)

N = charger_noyau()


class EtapePrealable(unittest.TestCase):
    """etapes_a_part avec une étape préalable sur la dernière carte : deux vrais processus par carte, l'un après l'autre,
    la même carte pour les deux ; l'autre carte ne l'attend pas."""

    def lancer(self, d, code_paroles=0):
        m = noyau_dans(d)
        script = Path(d, 'etape.py')
        script.write_text('import json, os, sys, time\n'
                          't = json.load(open(sys.argv[2]))\n'
                          'debut = time.time()\n'
                          'time.sleep(t["attente"])\n'
                          'json.dump({"etape": sys.argv[1], "vue": os.environ.get("CUDA_VISIBLE_DEVICES"), "debut": debut, '
                          '"fin": time.time(), "chanson": t.get("chanson")}, open(t["resultat"], "w"))\n'
                          'sys.exit(t["code"])\n')
        etat = Path(d, 'etat')
        etat.mkdir()
        travaux = [{'resultat': f'{d}/r/ying{k}.json', 'etat': str(etat), 'attente': 0.3, 'code': 0} for k in (0, 1)]
        avant = {1: ('paroles', {'resultat': f'{d}/r/paroles.json', 'attente': 1.2, 'code': code_paroles,
                                 'chanson': 'La valse des nuages'})}
        with mock.patch.object(m, 'script_complet', lambda: str(script)), mock.patch.object(m, 'log', lambda *a: None), \
                mock.patch.dict(os.environ, {'CUDA_VISIBLE_DEVICES': '0,1'}):
            res = m.etapes_a_part('ying', travaux, 2, avant=avant)
        return res, etat

    def test_paroles_puis_conversion_sur_la_meme_carte(self):
        with Dossier() as d:
            res, etat = self.lancer(d)
            temoins = sorted(p.name for p in etat.iterdir())
        r0, r1 = res
        self.assertEqual((r0['etape'], r0['vue'], r0['code'], r0['carte']), ('ying', '0', 0, 0))
        self.assertNotIn('avant', r0)
        self.assertEqual((r1['etape'], r1['vue'], r1['code'], r1['carte']), ('ying', '1', 0, 1))
        p = r1['avant']
        self.assertEqual((p['etape'], p['vue'], p['code'], p['carte'], p['chanson']), ('paroles', '1', 0, 1, 'La valse des nuages'))
        self.assertGreaterEqual(r1['debut'], p['fin'], 'la conversion de la carte 1 part après ses paroles')
        self.assertLess(r0['debut'], p['fin'], 'la carte 0 convertit sans attendre les paroles')
        self.assertGreaterEqual(r1['depart_s'], 1.2)
        self.assertEqual(p['duree_s'], r1['depart_s'])
        self.assertLess(r1['duree_s'], r1['depart_s'], 'duree_s : la conversion seule')
        self.assertEqual(temoins, ['fin_0', 'fin_1'])

    def test_paroles_en_echec_la_carte_convertit_quand_meme(self):
        with Dossier() as d:
            res, _ = self.lancer(d, code_paroles=3)
        self.assertEqual(res[1]['avant']['code'], 3)
        self.assertEqual((res[1]['etape'], res[1]['code']), ('ying', 0))


class FauxMoteur:
    """YingMusic-SVC imité : chaque morceau dépend de sa graine (bruit de départ de la diffusion)."""
    installation, chargement_s = {'origine': 'essai', 'duree_s': 0.0}, 1.0
    local = threading.local()   # graine du fil (une carte = un fil)
    crees = []                   # (fil, heure) de chaque chargement
    torch = types.SimpleNamespace(manual_seed=lambda g: setattr(FauxMoteur.local, 'graine', g),
                                  cuda=types.SimpleNamespace(empty_cache=lambda: None, max_memory_reserved=lambda: 3 * 2 ** 30))

    def __init__(self, *a):
        FauxMoteur.crees.append((threading.current_thread().name, time.time()))

    def preparer_ref(self, chemin):
        return {'chemin': chemin, 'f': 1.0 + len(chemin) / 100}

    def convertir_morceau(self, xs, ref, dec, pas):
        time.sleep(0.1)
        bruit = np.random.default_rng(FauxMoteur.local.graine).standard_normal(len(xs)).astype(np.float32)
        return (np.tanh(xs * ref['f'] * (1 + dec / 24)) + 0.01 * bruit).astype(np.float32)


class ConversionApresLesParoles(unittest.TestCase):
    """ying_sur_cartes avec les paroles sur la dernière carte : pistes d'une carte seule au bit près, chaque morceau
    converti une fois, la carte 1 ne convertit qu'après ses paroles et ne charge rien s'il ne reste aucun morceau."""

    def lancer(self, d, cartes, paroles_s=None):
        m = noyau_dans(d)
        FauxMoteur.crees = []
        x = {nom: np.random.default_rng(i).standard_normal(4 * SR).astype(np.float32)
             for i, nom in enumerate(('lead_sec', 'choeurs_sec'))}
        pistes = [{'cle': f'v1|voix_convertie.flac|{nom}', 'entree': nom, 'pas': 30, 'sortie': f'{d}/t/ying/v1_0_{nom}.wav',
                   'morceaux': [[i, i * SR, (i + 1) * SR, (-12, 0, 12, 0)[i], 0 if i == 0 else 300, 0 if i == 3 else 300]
                                for i in range(4) if nom == 'lead_sec' or i % 2]} for nom in ('lead_sec', 'choeurs_sec')]
        travail_paroles = {'whisper': '/local/whisper', 'traducteur': '/local/nllb', 'cache': '/tmp/hf', 'resultat': f'{d}/t/p.json',
                           'videos': [{'id': 'v1', 'audio': 'lead_sec', 'piste': 'lead_sec', 'phrases': [[0.5, 3.5]]}]}
        journal = {}

        def une(etape, travail):
            m.etape_ying(travail)
            return {**(m.lire_json(travail['resultat'], {}) or {}), 'code': 0, 'duree_s': 0.1}

        def plusieurs(etape, travaux, n, avant=None):
            # chaque carte dans son fil ; la carte d'une étape préalable la fait d'abord (fausses paroles qui attendent)
            avant, res = avant or {}, [None] * len(travaux)

            def carte(k):
                threading.current_thread().name = f'carte{k}'
                try:
                    if k in avant:
                        nom, tp = avant[k]
                        journal['travail_paroles'] = tp
                        time.sleep(paroles_s)
                        journal['fin_paroles'] = time.time()
                        prealable = {'etape': nom, 'code': 0, 'duree_s': paroles_s, 'carte': k}
                    m.etape_ying(travaux[k])
                    res[k] = {**(m.lire_json(travaux[k]['resultat'], {}) or {}), 'code': 0, 'duree_s': 0.1, 'carte': k}
                    if k in avant:
                        res[k].update({'avant': prealable, 'depart_s': paroles_s})
                finally:
                    Path(travaux[k]['etat'], f'fin_{k}').write_text('0')
            fils = [threading.Thread(target=carte, args=(k,), daemon=True) for k in range(len(travaux))]
            for f in fils:
                f.start()
            for f in fils:
                f.join(60)
                self.assertFalse(f.is_alive(), 'une carte attend sans fin')
            return res
        os.makedirs(f'{d}/t/ying', exist_ok=True)
        with mock.patch.dict(sys.modules, {'soundfile': FAUX_SF}), \
                mock.patch.object(m, 'MoteurYing', FauxMoteur), mock.patch.object(m, 'lire_mono', lambda c: x[c]), \
                mock.patch.object(m, 'log', lambda *a: None), mock.patch.object(m, 'etape_a_part', une), \
                mock.patch.object(m, 'etapes_a_part', plusieurs), \
                mock.patch.object(m, 'installer_ying', lambda o=None: {'origine': 'essai', 'duree_s': 0.0}), \
                mock.patch.dict(m.REGLAGES, {'graine': 4321}):
            travail = {'reference': 'ref-ondine.wav', 'pad': 4000, 'resultat': f'{d}/t/ying/resultat.json', 'outils': None,
                       'checkpoint': None, 'pistes': pistes}
            if cartes == 1:
                res = une('ying', travail)
            else:
                res = m.ying_sur_cartes(travail, cartes, 20.0, ('paroles', travail_paroles) if paroles_s is not None else None)
            sorties = {cle: sf_lire(p['sortie'])[0] for cle, p in res['pistes'].items() if p.get('sortie')}
        return res, sorties, journal, travail_paroles, list(FauxMoteur.crees)

    def comparer(self, paroles_s):
        with Dossier() as d1, Dossier() as d2:
            _, s1, _, _, _ = self.lancer(d1, 1)
            r2, s2, journal, tp, crees = self.lancer(d2, 2, paroles_s)
            etat = f'{d2}/t/ying/etat'
        self.assertEqual(sorted(s1), sorted(s2))
        self.assertEqual(len(s1), 2)
        for cle in s1:
            self.assertTrue(np.array_equal(s1[cle], s2[cle]), cle)
        self.assertEqual(sum(c['morceaux'] for c in r2['cartes']), 6, 'chaque morceau converti une fois')
        self.assertEqual(r2['avant'], {'etape': 'paroles', 'code': 0, 'duree_s': paroles_s, 'carte': 1})
        self.assertEqual(journal['travail_paroles'], {**tp, 'etat': etat, 'carte': 1},
                         'les paroles reçoivent le travail d\'après la conversion, plus leur carte et le tour de chargement')
        self.assertEqual([c.get('avant') for c in r2['cartes']], [None, 'paroles'])
        return r2, journal, crees

    def test_paroles_courtes_la_carte_1_rejoint_la_conversion(self):
        r, journal, crees = self.comparer(0.25)
        self.assertEqual([c['carte'] for c in r['cartes']], [0, 1])
        self.assertGreaterEqual(r['cartes'][1]['morceaux'], 1, 'la carte 1 a pris des morceaux après ses paroles')
        self.assertGreater(r['cartes'][0]['morceaux'], r['cartes'][1]['morceaux'])
        chargements = dict(crees)
        self.assertGreaterEqual(chargements['carte1'], journal['fin_paroles'], 'YingMusic-SVC chargé après les paroles')
        self.assertEqual(r['cartes'][1]['memoire_gpu_max_mo'], 3072)

    def test_paroles_longues_la_carte_1_ne_charge_rien(self):
        r, _, crees = self.comparer(1.5)
        self.assertEqual([c['morceaux'] for c in r['cartes']], [6, 0], 'la carte 0 a tout converti pendant les paroles')
        self.assertTrue(r['cartes'][1]['saute'])
        self.assertEqual([fil for fil, _ in crees], ['carte0'], 'la carte 1 ne charge pas YingMusic-SVC pour rien')

    def test_sans_paroles_comme_avant(self):
        with Dossier() as d:
            r, _, journal, _, _ = self.lancer(d, 2)
        self.assertNotIn('avant', r)
        self.assertEqual(journal, {})
        self.assertNotIn('avant', r['cartes'][1])


class RienAPrendre(unittest.TestCase):
    def test_tous_pris(self):
        with Dossier() as d:
            taches = [[0, 0], [0, 1], [1, 0]]
            self.assertFalse(N.tous_pris({'reclamations': str(d), 'taches': taches}))
            for pi, mi in taches[:2]:
                Path(d, f'{pi}_{mi}').write_text('')
            self.assertFalse(N.tous_pris({'reclamations': str(d), 'taches': taches}))
            Path(d, '1_0').write_text('')
            self.assertTrue(N.tous_pris({'reclamations': str(d), 'taches': taches}))
        self.assertFalse(N.tous_pris({'taches': taches}), 'une carte seule : toujours à faire')
        self.assertFalse(N.tous_pris({'reclamations': '/nulle/part', 'taches': []}))

    def test_etape_ying_sans_morceau_libre(self):
        with Dossier() as d:
            m = noyau_dans(d)
            rec = Path(d, 'rec')
            rec.mkdir()
            Path(rec, '0_0').write_text('')
            charges = []
            with mock.patch.dict(sys.modules, {'soundfile': FAUX_SF}), \
                    mock.patch.object(m, 'MoteurYing', lambda *a: charges.append(a)), mock.patch.object(m, 'log', lambda *a: None):
                m.etape_ying({'resultat': f'{d}/r.json', 'reclamations': str(rec), 'taches': [[0, 0]], 'carte': 1,
                              'etat': str(d), 'checkpoint': None, 'pistes': []})
            res = json.loads(Path(d, 'r.json').read_text())
        self.assertEqual(charges, [])
        self.assertEqual((res['saute'], res['carte'], res['morceaux_faits']), (True, 1, {}))


class RepriseDesParoles(unittest.TestCase):
    def test_paroles_a_reprendre(self):
        self.assertTrue(N.paroles_a_reprendre(None), 'pas tournées (aucun morceau YingMusic, étape en échec)')
        self.assertTrue(N.paroles_a_reprendre({'code': -9, 'videos': {}}), 'processus tué : carte arrêtée en route')
        self.assertTrue(N.paroles_a_reprendre({'code': 0, 'erreur': "Whisper indisponible : RuntimeError('CUDA error: "
                                                                    "unspecified launch failure')"}))
        self.assertFalse(N.paroles_a_reprendre({'code': 0, 'videos': {'v1': {'lignes': 12}}}))
        self.assertFalse(N.paroles_a_reprendre({'code': 0, 'erreur': "Whisper indisponible : OSError('introuvable')"}),
                         'erreur de contenu : elle se répéterait sur la carte 0')


class ChargementDeWhisper(unittest.TestCase):
    """etape_paroles sur une carte parmi plusieurs : Whisper attend que YingMusic-SVC soit chargé sur la carte 0 (un
    chargement à la fois) ; sur une carte seule, rien ne change."""

    def modules(self):
        torch = types.ModuleType('torch')
        torch.load, torch.float16 = (lambda *a, **k: None), 'float16'
        torch.cuda = types.SimpleNamespace(empty_cache=lambda: None, max_memory_reserved=lambda: 0)
        tr = types.ModuleType('transformers')
        tr.__version__, tr.WhisperForConditionalGeneration, tr.WhisperProcessor, tr.pipeline = '0.0', object, object, None
        return {'torch': torch, 'torchaudio': types.ModuleType('torchaudio'), 'transformers': tr}

    def lancer(self, d, travail, avant_charge_s=None):
        appels = []

        def depuis_hf(*a, **k):   # Whisper absent : l'étape s'arrête là, sans paroles (comme sur Kaggle)
            appels.append(time.time())
            raise RuntimeError('chargement impossible : modèle absent')
        with mock.patch.dict(sys.modules, self.modules()), mock.patch.dict(os.environ), \
                mock.patch.object(N, 'depuis_hf', depuis_hf), mock.patch.object(N, 'log', lambda *a: None), \
                mock.patch('traceback.print_exc', lambda *a, **k: None):
            fil = threading.Thread(target=N.etape_paroles, args=(travail,), daemon=True)
            fil.start()
            charge = None
            if avant_charge_s is not None:
                time.sleep(avant_charge_s)
                charge = time.time()
                Path(travail['etat'], 'charge_0').write_text('')
            fil.join(30)
            self.assertFalse(fil.is_alive())
        return json.loads(Path(travail['resultat']).read_text()), appels, charge

    def test_attend_le_chargement_de_la_carte_0(self):
        with Dossier() as d:
            res, appels, charge = self.lancer(d, {'resultat': f'{d}/p.json', 'etat': str(d), 'carte': 1, 'videos': []}, 0.8)
            temoins = sorted(p.name for p in Path(d).iterdir())
        self.assertEqual(len(appels), 1)
        self.assertGreaterEqual(appels[0], charge, 'Whisper chargé après YingMusic-SVC sur la carte 0')
        self.assertGreaterEqual(res['attente_chargement_s'], 0.5)
        self.assertEqual(res['carte'], 1)
        self.assertIn('Whisper indisponible', res['erreur'])
        self.assertIn('charge_1', temoins)

    def test_une_carte_rien_ne_change(self):
        with Dossier() as d:
            res, appels, _ = self.lancer(d, {'resultat': f'{d}/p.json', 'videos': []})
            fichiers = sorted(p.name for p in Path(d).iterdir())
        self.assertEqual(len(appels), 1)
        self.assertNotIn('attente_chargement_s', res)
        self.assertNotIn('carte', res)
        self.assertEqual(fichiers, ['p.json'], 'aucun témoin de chargement')


# --- Chaîne entière du noyau (main) avec les paroles ------------------------------------------------------------------

def lancer_chaine(dossier, R, cartes=1, panne_paroles=False):
    """Chaîne entière du noyau (main), comme lancer_noyau de test_plusieurs_voix_chaine (mêmes imitations de torch, de
    Seed-VC, de YingMusic-SVC, de la séparation et du nettoyage), plus une fausse étape paroles : paroles.json d'après la
    piste et les phrases reçues (niveau de chaque phrase dans le son lu). Deux cartes : chaque carte dans un fil, l'étape
    préalable d'une carte avant la sienne ; panne_paroles : la carte des paroles s'arrête en route (code 1, rien écrit).
    Rend (dossier de sortie de la vidéo, journal des étapes : (étape, carte, moment))."""
    import base64
    import warnings
    src = NOYAU.read_text(encoding='utf-8').replace("'__REGLAGES__'", repr(base64.b64encode(b'{}').decode()))
    src = src.replace('/tmp/', f'{dossier}/t/').replace('/kaggle/', f'{dossier}/kaggle/')
    m = types.ModuleType('noyau_chaine_paroles')
    exec(compile(src, 'noyau_chaine_paroles.py', 'exec'), m.__dict__)
    m.REGLAGES = R
    timbres, torch = Timbres(), faux_torch()
    seedvc = FauxSeedVC(timbres)
    entrees = dossier / 'kaggle' / 'input' / 'jeu'
    entrees.mkdir(parents=True, exist_ok=True)
    (entrees / 'v1.flac').write_bytes(b'')
    for nom, (f, d, _) in VOIX.items():
        ecrire_son(entrees / f'voix-{nom}.wav', note(f, 0.0, d, -20, d), SR)
    (dossier / 't' / 'seed-vc').mkdir(parents=True, exist_ok=True)
    journal, verrou = [], threading.Lock()

    def noter(*e):
        with verrou:
            journal.append(e)

    def lancer(*commande, **options):
        assert commande[1].endswith('separer.py'), commande
        for t in json.loads(commande[2]):
            ecrire_son(t['voix'], np.stack([SCENE['voix']] * 2, 1), SR)
            ecrire_son(t['reste'], np.stack([SCENE['reste']] * 2, 1), SR)
            with open(t['voix'] + '.duree', 'w') as f:
                f.write('1.0')

    class FauxYing:
        def __init__(self, *a, **k):
            self.torch, self.installation, self.chargement_s = torch, {'origine': 'essai', 'duree_s': 0.0}, 0.0

        def preparer_ref(self, chemin):
            d, s = lire_son(chemin)
            return {'tag': hauteur_dominante(reechantillonner(d, s, 16000))}

        def convertir_morceau(self, xs, ref, decalage, pas):
            f = hauteur_trames(reechantillonner(xs, SR, 16000))
            f = np.where(f > 1, f * 2 ** (decalage / 12), 0)
            return synthese(f[np.minimum(np.arange(len(xs)) // 441, len(f) - 1)], ref['tag'], SR)

    def paroles(travail, carte):
        noter('paroles', carte, 'debut')
        res = {'implementation': 'fausse', 'chargements_s': {'whisper': 0.1}, 'temps': {'transcription_s': 0.2}, 'videos': {}}
        if travail.get('etat'):
            res.update({'carte': travail.get('carte'), 'attente_chargement_s': 0.0})
        for v in travail['videos']:
            x = lire_son(v['audio'])[0]
            x = x.mean(1) if x.ndim == 2 else x
            lignes = [{'debut': a, 'fin': b, 'langue': 'fr', 'mots': [],
                       'texte': f'phrase {i} au niveau {float(np.sqrt(np.mean(x[int(a * SR):int(b * SR)] ** 2))):.7f}'}
                      for i, (a, b) in enumerate(v['phrases'])]
            m.ecrire_json(v['sortie'], m.paroles_json(lignes, piste=v.get('piste')))
            res['videos'][v['id']] = {'lignes': len(lignes), 'transcription_s': 0.2, 'fichier': 'paroles.json',
                                      'traduction': False}
        m.ecrire_json(travail['resultat'], res)
        noter('paroles', carte, 'fin')
        return {**res, 'code': 0, 'duree_s': 0.3}

    def etape(nom, travail):
        if nom == 'nettoyer':
            res = {'karaoke': 'faux', 'dereverb': 'faux', 'recouvrement': 4, 'chargements_s': {}, 'videos': {}}
            for v in travail['videos']:
                os.makedirs(v['dossier'], exist_ok=True)
                r = res['videos'][v['id']] = {'niveaux_db': {}, 'durees_s': {}, 'duree_s': 0.5}
                for cle, piste in (('lead_sec', 'lead'), ('choeurs_sec', 'choeurs')):
                    r[cle] = os.path.join(v['dossier'], f'{cle}.wav')
                    ecrire_son(r[cle], np.stack([SCENE[piste]] * 2, 1), SR)
            return {**res, 'code': 0, 'duree_s': 0.5}
        if nom == 'paroles':
            return paroles(travail, travail.get('carte'))
        assert nom == 'ying', nom
        noter('ying', travail.get('carte'), 'debut')
        m.etape_ying(travail)
        noter('ying', travail.get('carte'), 'fin')
        return {**(m.lire_json(travail['resultat'], {}) or {}), 'code': 0, 'duree_s': 0.1}

    def etapes(nom, travaux, n, avant=None):
        avant, res = avant or {}, [None] * len(travaux)

        def carte(k):
            try:
                prealable = None
                if k in avant:
                    if panne_paroles:
                        noter(avant[k][0], k, 'panne')
                        prealable = {'code': 1, 'duree_s': 0.1, 'carte': k}
                    else:
                        prealable = {**paroles(avant[k][1], k), 'carte': k}
                res[k] = {**etape(nom, travaux[k]), 'carte': k}
                if prealable is not None:
                    res[k].update({'avant': prealable, 'depart_s': prealable['duree_s']})
            finally:
                if travaux[k].get('etat'):
                    with open(os.path.join(travaux[k]['etat'], f'fin_{k}'), 'w') as f:
                        f.write('0')
        fils = [threading.Thread(target=carte, args=(k,), daemon=True) for k in reversed(range(len(travaux)))]
        for f in fils:
            f.start()
        for f in fils:
            f.join(120)
            assert not f.is_alive(), 'une carte attend sans fin'
        return res

    if cartes > 1:
        torch.cuda.device_count = lambda: cartes
        m.etapes_a_part = etapes
        m.installer_ying = lambda o=None: {'origine': 'essai', 'duree_s': 0.0}
    for nom, valeur in {'verifier_acces': lambda: None, 'trouver_outils': lambda: None, 'installer': lambda o=None: None,
                        'cloner': lambda o=None: 'faux', 'poids_depuis_outils': lambda o: None,
                        'copier_paroles': lambda o: {}, 'log': lambda *a: None, 'lancer': lancer, 'etape_a_part': etape,
                        'MoteurYing': FauxYing,
                        'mesures_musique': lambda v, r: {'reste_sous_voix_db': -5.0, 'reste_actif': 0.9,
                                                         'tonalite_reste': 0.5, 'voix_active': 0.8,
                                                         'niveau_voix_dbfs': -20.0, 'niveau_reste_dbfs': -26.0}}.items():
        setattr(m, nom, valeur)
    modules = {'soundfile': types.SimpleNamespace(write=ecrire_son, read=lire_son), 'torch': torch,
               'torchaudio': types.SimpleNamespace(
                   functional=types.SimpleNamespace(resample=lambda x, de, vers: T(reechantillonner(x, de, vers))),
                   compliance=types.SimpleNamespace(kaldi=types.SimpleNamespace(fbank=timbres.fbank))),
               'librosa': faux_librosa(), 'inference': seedvc}
    ici, chemin = os.getcwd(), list(sys.path)
    try:
        with mock.patch.dict(sys.modules, modules), warnings.catch_warnings():
            warnings.simplefilter('ignore', ResourceWarning)
            m.main()
    finally:
        os.chdir(ici)
        sys.path[:] = chemin
    return dossier / 'kaggle' / 'working' / 'v1', journal


class ChaineAvecParoles(unittest.TestCase):
    """main() sur une machine à deux cartes, paroles demandées : la voix convertie, les courbes, paroles.json et le
    rapport d'une carte seule, au bit près ; les paroles tournent sur la carte 1 avant qu'elle ne rejoigne la conversion.
    Une carte (réglage gpus à 1), ou rien à convertir par YingMusic-SVC : paroles après la conversion, comme avant."""

    def sorties(self, R, cartes, panne=False):
        with Dossier() as d:
            s, journal = lancer_chaine(d, R, cartes, panne)
            rapport = json.loads((s / 'rapport.json').read_text().replace(str(d), '<dossier>'))
            return {'voix': lire_son(s / 'voix_convertie.flac')[0], 'courbes': (s / 'courbes.json').read_text(),
                    'paroles': (s / 'paroles.json').read_text(), 'rapport': sans_durees(rapport),
                    'session': json.loads((s.parent / 'session.json').read_text()), 'journal': journal}

    def identiques(self, a, b):
        self.assertTrue(np.array_equal(a['voix'], b['voix']), 'voix convertie')
        self.assertEqual(a['courbes'], b['courbes'], 'courbes.json')
        self.assertEqual(a['paroles'], b['paroles'], 'paroles.json')
        self.assertEqual(json.dumps(a['rapport'], sort_keys=True), json.dumps(b['rapport'], sort_keys=True), 'rapport.json')

    def test_meme_resultat_paroles_sur_la_carte_1(self):
        R = {**reglages(Timbres(), 'yingmusic'), 'paroles': True}
        une, deux = self.sorties(R, 1), self.sorties(R, 2)
        self.identiques(une, deux)
        self.assertGreater(len(json.loads(une['paroles'])['segments']), 3)
        j1 = une['journal']
        self.assertEqual([e for e in j1 if e[0] == 'paroles'], [('paroles', None, 'debut'), ('paroles', None, 'fin')])
        self.assertLess(j1.index(('ying', None, 'fin')), j1.index(('paroles', None, 'debut')), 'une carte : après la conversion')
        j2 = deux['journal']
        self.assertEqual([e for e in j2 if e[0] == 'paroles'], [('paroles', 1, 'debut'), ('paroles', 1, 'fin')])
        self.assertLess(j2.index(('paroles', 1, 'fin')), j2.index(('ying', 1, 'debut')), 'la carte 1 convertit après ses paroles')
        s1, s2 = une['session'], deux['session']
        self.assertNotIn('pendant_conversion', s1['paroles'])
        self.assertNotIn('paroles_pendant_conversion_s', s1['temps'])
        self.assertEqual((s2['paroles']['pendant_conversion'], s2['paroles']['carte']), (True, 1))
        self.assertEqual(s2['temps']['paroles_pendant_conversion_s'], 0.3)
        self.assertEqual([c.get('avant') for c in s2['yingmusic']['cartes']], [None, 'paroles'])
        self.assertEqual(deux['rapport']['paroles']['piste'], 'lead_sec')

    def test_carte_des_paroles_en_panne(self):
        R = {**reglages(Timbres(), 'yingmusic'), 'paroles': True}
        une, panne = self.sorties(R, 1), self.sorties(R, 2, panne=True)
        self.identiques(une, panne)
        j = panne['journal']
        self.assertIn(('paroles', 1, 'panne'), j)
        self.assertLess(j.index(('ying', 0, 'fin')), j.index(('paroles', None, 'debut')), 'reprises sur la carte 0, à la fin')
        self.assertTrue(panne['session']['paroles']['reprise'])

    def test_une_carte_demandee_ou_rien_pour_yingmusic(self):
        for nom, R in (('gpus à 1', {**reglages(Timbres(), 'yingmusic'), 'paroles': True, 'gpus': 1}),
                       ('Seed-VC', {**reglages(Timbres(), 'seedvc'), 'paroles': True})):
            with self.subTest(nom):
                une, deux = self.sorties(R, 1), self.sorties(R, 2)
                self.identiques(une, deux)
                self.assertEqual([e for e in deux['journal'] if e[0] == 'paroles'],
                                 [('paroles', None, 'debut'), ('paroles', None, 'fin')])
                self.assertNotIn('paroles_pendant_conversion_s', deux['session']['temps'])


if __name__ == '__main__':
    unittest.main()
