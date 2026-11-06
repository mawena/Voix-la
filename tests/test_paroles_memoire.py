"""Paroles et mémoire du GPU : Whisper allégé des poids d'attention qui ne servent pas au mot à mot (alleger_whisper),
mémoire libre attendue avant le chargement, mot à mot à court de mémoire coupé en morceaux plus courts au lieu d'être
repassé tel quel, étape lancée sur la carte demandée.

Sans GPU ni modèle : faux modules, fausse mémoire, faux pipeline ; dans le cas normal (aucun manque de mémoire),
transcrire_video et la cascade de transcription font exactement les appels d'avant. Mots et noms inventés."""
import json
import os
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from commun import Dossier, charger_noyau
from test_deux_gpu_noyau import noyau_dans

N = charger_noyau()
GIO = 2 ** 30


class OutOfMemoryError(RuntimeError):
    """Même nom que torch.OutOfMemoryError (torch n'est pas là)."""


def oom():
    return OutOfMemoryError('CUDA out of memory. Tried to allocate 64.00 MiB. GPU 0 has a total capacity of 15.00 GiB')


class ManqueDeMemoire(unittest.TestCase):
    def test_reconnu(self):
        self.assertTrue(N.manque_de_memoire(oom()))
        self.assertTrue(N.manque_de_memoire(RuntimeError('CUDA out of memory. Tried to allocate 128.00 MiB')))
        self.assertFalse(N.manque_de_memoire(RuntimeError("CUDA error: unspecified launch failure")))
        self.assertFalse(N.manque_de_memoire(ValueError('language')))


class CascadeDeTranscription(unittest.TestCase):
    """transcrire_avec_repli : sans panne, les appels d'avant ; mot à mot à court de mémoire : rendu tout de suite (mode
    'mémoire'), mémoire rendue hors de l'erreur, sans repasser les garde-fous réduits."""

    def lancer(self, erreurs_par_appel, mots=True):
        appels, liberes = [], []

        def appeler(ts, gk):
            appels.append((ts, gk))
            e = erreurs_par_appel[len(appels) - 1] if len(appels) <= len(erreurs_par_appel) else None
            if e is not None:
                raise e
            return {'text': ' bonjour', 'chunks': []}

        def liberer():
            liberes.append(sys.exc_info()[0])   # None : la pile de l'erreur est lâchée
        return N.transcrire_avec_repli(appeler, 'fr', mots, liberer), appels, liberes

    def test_sans_panne_comme_avant(self):
        (sortie, mode, erreurs), appels, liberes = self.lancer([])
        self.assertEqual((mode, erreurs, liberes), ('mots', [], []))
        self.assertEqual(appels, [('word', {'language': 'fr', 'task': 'transcribe', **N.GARDE_FOUS_WHISPER})])
        self.assertEqual(sortie['text'], ' bonjour')

    def test_autre_erreur_garde_fous_reduits_comme_avant(self):
        (_, mode, erreurs), appels, liberes = self.lancer([ValueError('option refusée')])
        self.assertEqual(mode, 'mots, garde-fous réduits')
        self.assertEqual(appels[1], ('word', {'language': 'fr', 'task': 'transcribe'}))
        self.assertEqual((len(erreurs), liberes), (1, []))

    def test_memoire_rendue_et_morceau_a_couper(self):
        (sortie, mode, erreurs), appels, liberes = self.lancer([oom()])
        self.assertEqual((sortie, mode), (None, 'mémoire'))
        self.assertEqual(len(appels), 1, 'pas de seconde passe de la même taille')
        self.assertEqual(liberes, [None], 'mémoire rendue une fois, hors de l\'erreur')
        self.assertTrue(erreurs[0].startswith('mots : OutOfMemoryError'))

    def test_sans_mots(self):
        (_, mode, _), appels, liberes = self.lancer([oom()], mots=False)
        self.assertEqual([ts for ts, _ in appels], [True, True], 'segments, puis segments sans garde-fous')
        self.assertEqual(appels[0][1], {'language': 'fr', 'task': 'transcribe', **N.GARDE_FOUS_WHISPER})
        self.assertEqual((mode, liberes), ('segments, garde-fous réduits', [None]))


class CouperMorceau(unittest.TestCase):
    def test_entre_deux_phrases_au_plus_pres_du_milieu(self):
        phrases = [[0.0, 3.0], [3.5, 7.0], [7.4, 9.0], [10.0, 20.0]]
        moities = N.couper_morceau((0.0, 20.0, 'fr', [0, 1, 2, 3]), phrases, np.zeros(16000 * 21, np.float32))
        self.assertEqual(moities, [(0.0, 9.0, 'fr', [0, 1, 2]), (10.0, 20.0, 'fr', [3])])

    def test_une_phrase_au_creux_du_milieu(self):
        x = (0.3 * np.random.default_rng(3).standard_normal(16000 * 12)).astype(np.float32)
        x[int(6.3 * 16000):int(6.4 * 16000)] *= 0.01   # respiration
        x[int(3.0 * 16000):int(3.1 * 16000)] = 0.0      # plus bas, mais hors du tiers du milieu
        (a, c, lg, i), (c2, b, lg2, i2) = N.couper_morceau((2.0, 11.0, 'en', [4]), [[0, 1]] * 4 + [[2.0, 11.0]], x)
        self.assertEqual((a, b, lg, lg2, i, i2), (2.0, 11.0, 'en', 'en', [4], [4]))
        self.assertEqual(c, c2)
        self.assertTrue(6.3 <= c <= 6.4, c)

    def test_trop_court(self):
        self.assertIsNone(N.couper_morceau((1.0, 8.9, 'fr', [0, 1]), [[1.0, 4.0], [5.0, 8.9]], np.zeros(16000 * 10)))
        self.assertIsNotNone(N.couper_morceau((1.0, 8.9, 'fr', [0, 1]), [[1.0, 4.0], [5.0, 8.9]], np.zeros(16000 * 10),
                                              {'coupe_memoire_min_s': 3.0}))


def sortie_mots(duree, pas=0.4):
    """Sortie du pipeline en mot à mot : un mot inventé tous les pas s (temps relatifs au morceau)."""
    chunks = [{'text': f' m{int(round(t * 10))}', 'timestamp': (round(t, 2), round(t + 0.3, 2))}
              for t in np.arange(0.1, duree - 0.4, pas)]
    return {'text': ''.join(c['text'] for c in chunks), 'chunks': chunks}


class TranscrireVideoAvecPanne(unittest.TestCase):
    """transcrire_video avec un faux Whisper qui manque de mémoire en mot à mot au-delà d'une durée de morceau."""

    def lancer(self, phrases, limite_s=None):
        x16 = (0.2 * np.random.default_rng(5).standard_normal(16000 * 30)).astype(np.float32)
        v = {'phrases': phrases, 'chant': phrases, 'piste': 'lead_sec'}
        appels = []

        def transcrire(audio, lg, mots=True):
            duree = len(audio) / 16000
            appels.append((round(duree, 2), mots, audio))
            if mots and limite_s is not None and duree > limite_s:
                return None, 'mémoire', ['mots : ' + repr(oom())]
            return sortie_mots(duree), 'mots' if mots else 'segments', []
        r = {}
        lignes = N.transcrire_video(v, x16, lambda a: ('fr', 0.99), transcrire, r)
        return lignes, r, appels

    def test_sans_panne_les_appels_d_avant(self):
        phrases = [[1.0, 6.0], [6.5, 12.0], [12.4, 20.0], [24.0, 27.0]]
        lignes, r, appels = self.lancer(phrases)
        attendus = [round(b - a, 2) for a, b, _, _ in N.grouper_morceaux(phrases, ['fr'] * 4)]
        self.assertEqual([(d, m) for d, m, _ in appels], [(d, True) for d in attendus])
        self.assertEqual(r['modes'], {'mots': 2})
        self.assertNotIn('coupes_memoire', r)
        self.assertNotIn('erreurs', r)
        self.assertTrue(lignes)

    def test_coupe_entre_les_phrases(self):
        phrases = [[1.0, 6.0], [6.5, 12.0], [12.4, 20.0]]
        _, r_normal, appels_normal = self.lancer(phrases)
        lignes, r, appels = self.lancer(phrases, limite_s=12.0)
        self.assertEqual([(d, m) for d, m, _ in appels], [(19.0, True), (11.0, True), (7.6, True)])
        self.assertEqual((r['modes'], r['coupes_memoire']), ({'mots': 2}, 1))
        self.assertEqual(len(r['erreurs']), 1)
        # la première moitié : le son du morceau entier, au même échantillon près (mêmes phrases, mêmes fondus)
        entier, moitie = appels_normal[0][2], appels[1][2]
        self.assertTrue(np.array_equal(moitie, entier[:len(moitie)]))
        debuts = [m['debut'] for l in lignes for m in l['mots']]
        self.assertTrue(any(1.0 <= d < 12.0 for d in debuts) and any(12.4 <= d < 20.0 for d in debuts),
                        'des mots des deux moitiés, en temps de la piste')
        self.assertTrue(all(1.0 <= d < 20.0 for d in debuts))

    def test_trop_court_pour_couper_sans_les_mots(self):
        lignes, r, appels = self.lancer([[2.0, 11.0]], limite_s=1.0)
        self.assertEqual([m for _, m, _ in appels], [True, True, False, True, False])
        self.assertAlmostEqual(appels[1][0] + appels[3][0], 9.0, places=1)
        self.assertEqual(appels[1][0], appels[2][0], 'la même moitié, redemandée sans horodatage par mot')
        self.assertEqual((r['modes'], r['coupes_memoire']), ({'segments': 2}, 1))
        self.assertTrue(lignes)


class FauxPoids:
    def __init__(self, forme):
        self.shape = tuple(forme)

    def numel(self):
        return int(np.prod(self.shape))

    def new_empty(self, forme):
        return FauxPoids(forme)


class FauxModule:
    def __init__(self):
        self.avant, self.apres = [], []

    def register_forward_pre_hook(self, crochet, with_kwargs=False):
        self.avant.append((crochet, with_kwargs))

    def register_forward_hook(self, crochet, prepend=False):
        self.apres.append((crochet, prepend))


def faux_whisper(couches=6, tetes=((2, 0), (4, 3), (4, 5))):
    decodeur = types.SimpleNamespace(layers=[types.SimpleNamespace(self_attn=FauxModule(), encoder_attn=FauxModule())
                                             for _ in range(couches)])
    return types.SimpleNamespace(model=types.SimpleNamespace(encoder=FauxModule(), decoder=decodeur),
                                 generation_config=types.SimpleNamespace(alignment_heads=[list(t) for t in tetes]))


class AllegerWhisper(unittest.TestCase):
    def test_crochets(self):
        m = faux_whisper()
        info = N.alleger_whisper(m)
        self.assertEqual(info['attention_croisee_gardee'], '2 couches sur 6')
        (avant, kw), = m.model.encoder.avant
        self.assertTrue(kw)
        self.assertEqual(avant(None, ('x',), {'output_attentions': True, 'return_dict': True}),
                         (('x',), {'output_attentions': False, 'return_dict': True}))
        self.assertEqual(avant(None, ('x',), {'return_dict': True}), (('x',), {'return_dict': True}))
        for i, c in enumerate(m.model.decoder.layers):
            self.assertEqual([p for _, p in c.self_attn.apres], [True], 'en tête : avant les enregistreurs')
            self.assertEqual([p for _, p in c.encoder_attn.apres], [] if i in (2, 4) else [True], i)
        vider = m.model.decoder.layers[0].encoder_attn.apres[0][0]
        sortie = object()
        r = vider(None, (), (sortie, FauxPoids((5, 20, 1, 1500))))
        self.assertIs(r[0], sortie, 'la sortie de l\'attention ne change pas')
        self.assertEqual((r[1].shape, len(r)), ((5, 20, 1, 0), 2))
        self.assertIsNone(vider(None, (), (sortie, None)), 'sans poids (sdpa) : rien à vider')
        self.assertIsNone(vider(None, (), (sortie, FauxPoids((5, 20, 1, 0)))))

    def test_sans_tetes_d_alignement_toute_l_attention_croisee_reste(self):
        m = faux_whisper(4, ())
        m.generation_config.alignment_heads = None
        self.assertEqual(N.alleger_whisper(m)['attention_croisee_gardee'], '4 couches sur 4')
        self.assertTrue(all(not c.encoder_attn.apres and c.self_attn.apres for c in m.model.decoder.layers))


class AttendreMemoire(unittest.TestCase):
    def torch(self, libres, totale=15 * GIO):
        suite = iter(libres)
        dernier = [libres[0]]

        def info():
            dernier[0] = next(suite, dernier[0])
            return dernier[0], totale
        return types.SimpleNamespace(cuda=types.SimpleNamespace(mem_get_info=info))

    def test_attend_que_la_memoire_revienne(self):
        libre, totale, attente = N.attendre_memoire(self.torch([2 * GIO, 5 * GIO, 12 * GIO]), 8000, 5.0, 0.06)
        self.assertEqual((libre, totale), (12 * 1024, 15 * 1024))
        self.assertGreater(attente, 0.0)

    def test_assez_tout_de_suite(self):
        self.assertEqual(N.attendre_memoire(self.torch([14 * GIO]), 8000, 5.0, 0.01), (14 * 1024, 15 * 1024, 0.0))

    def test_attente_bornee(self):
        libre, _, attente = N.attendre_memoire(self.torch([3 * GIO]), 8000, 0.05, 0.01)
        self.assertEqual(libre, 3 * 1024)
        self.assertGreaterEqual(attente, 0.05)

    def test_sans_cuda(self):
        self.assertEqual(N.attendre_memoire(types.SimpleNamespace(cuda=types.SimpleNamespace()), 8000, 5.0), (None, None, 0.0))


class EtapeParolesMemoire(unittest.TestCase):
    """etape_paroles : allocateur à segments extensibles avant le premier appel à CUDA, mémoire libre notée, carte de
    l'étape dans le résultat (aussi sans tour de chargement)."""

    def lancer(self, d, travail, env=None):
        vu = {}
        torch = types.ModuleType('torch')
        torch.load, torch.float16 = (lambda *a, **k: None), 'float16'
        torch.cuda = types.SimpleNamespace(empty_cache=lambda: None, max_memory_reserved=lambda: 0,
                                           mem_get_info=lambda: (12 * GIO, 15 * GIO))
        tr = types.ModuleType('transformers')
        tr.__version__, tr.WhisperForConditionalGeneration, tr.WhisperProcessor, tr.pipeline = '0.0', object, object, None

        def depuis_hf(*a, **k):
            vu['alloc'] = os.environ.get('PYTORCH_CUDA_ALLOC_CONF')
            raise RuntimeError('chargement impossible : modèle absent')
        modules = {'torch': torch, 'torchaudio': types.ModuleType('torchaudio'), 'transformers': tr}
        with mock.patch.dict(sys.modules, modules), mock.patch.dict(os.environ, env or {}), \
                mock.patch.object(N, 'depuis_hf', depuis_hf), mock.patch.object(N, 'log', lambda *a: None), \
                mock.patch('traceback.print_exc', lambda *a, **k: None):
            if env is None:
                os.environ.pop('PYTORCH_CUDA_ALLOC_CONF', None)
            fil = threading.Thread(target=N.etape_paroles, args=(travail,), daemon=True)
            fil.start()
            fil.join(30)
            self.assertFalse(fil.is_alive())
        return json.loads(Path(travail['resultat']).read_text()), vu

    def test_memoire_et_allocateur(self):
        with Dossier() as d:
            res, vu = self.lancer(d, {'resultat': f'{d}/p.json', 'videos': []})
        self.assertEqual(vu['alloc'], 'expandable_segments:True', 'réglé avant le chargement de Whisper')
        self.assertEqual(res['memoire'], {'libre_mo': 12 * 1024, 'totale_mo': 15 * 1024,
                                          'allocateur': 'expandable_segments:True'})
        self.assertNotIn('carte', res)
        self.assertIn('Whisper indisponible', res['erreur'])

    def test_reglage_existant_garde(self):
        with Dossier() as d:
            res, vu = self.lancer(d, {'resultat': f'{d}/p.json', 'videos': [], 'carte': 1},
                                  {'PYTORCH_CUDA_ALLOC_CONF': 'max_split_size_mb:128'})
        self.assertEqual(vu['alloc'], 'max_split_size_mb:128,expandable_segments:True')
        self.assertEqual(res['carte'], 1, 'carte de l\'étape, sans tour de chargement')
        self.assertNotIn('attente_chargement_s', res)


class EtapeSurUneCarte(unittest.TestCase):
    """etape_a_part avec travail['carte'] et plusieurs cartes : le processus ne voit que cette carte ; sinon, comme avant."""

    def vue(self, travail, cartes=None):
        with Dossier() as d:
            m = noyau_dans(d)
            script = Path(d, 'etape.py')
            script.write_text('import json, os, sys\n'
                              't = json.load(open(sys.argv[2]))\n'
                              'json.dump({"vue": os.environ.get("CUDA_VISIBLE_DEVICES")}, open(t["resultat"], "w"))\n')
            with mock.patch.object(m, 'script_complet', lambda: str(script)), mock.patch.object(m, 'log', lambda *a: None), \
                    mock.patch.dict(os.environ, {'CUDA_VISIBLE_DEVICES': '0,1'}):
                args = ('paroles', {**travail, 'resultat': f'{d}/r/p.json'}) + ((cartes,) if cartes else ())
                return m.etape_a_part(*args)['vue']

    def test_cartes(self):
        self.assertEqual(self.vue({'carte': 1}, 2), '1', 'paroles sur la dernière carte')
        self.assertEqual(self.vue({}, 2), '0,1', 'sans carte : comme avant (la carte 0)')
        self.assertEqual(self.vue({'carte': 0}), '0,1', 'une carte (reprise YingMusic ou nettoyage) : comme avant')


if __name__ == '__main__':
    unittest.main()
