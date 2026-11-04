"""Enchaînement de l'étape paroles du noyau (transcrire_video, traduire_lignes) avec de faux modèles : langue par
phrase, morceaux de même langue, silence hors des phrases, temps décalés au début de chaque morceau, garde-fous, lignes,
traduction ligne par ligne. Textes inventés."""
import unittest

import numpy as np

from commun import charger_noyau

N = charger_noyau()


def sortie_pipeline(mots, pas=0.4):
    """Sortie du pipeline de transformers (return_timestamps='word') : temps relatifs au morceau."""
    chunks, t = [], 0.0
    for m in mots:
        if m is None:
            t += 1.0
            continue
        chunks.append({'text': ' ' + m, 'timestamp': (round(t, 2), round(t + pas * 0.9, 2))})
        t += pas
    return {'text': ''.join(c['text'] for c in chunks), 'chunks': chunks}


class Enchainement(unittest.TestCase):
    def setUp(self):
        self.x16 = np.full(16000 * 20, 0.1, np.float32)
        self.v = {'phrases': [[1.0, 4.0], [5.0, 8.0], [12.0, 15.0]], 'chant': [[1.0, 4.0], [5.0, 8.0], [12.0, 15.0]],
                  'piste': 'lead_sec'}
        self.appels = []

    def detecter(self, audio):
        return ('es', 0.9) if len(self.appels) < 2 and not self.appels.append(1) else ('fr', 0.95)

    def transcrire(self, audio, lg):
        self.appels.append((len(audio) / 16000, lg, audio))
        if lg == 'es':
            # 0-3 s : phrase 1 ; 3-4 s : pause muette ; 4-7 s : phrase 2
            return sortie_pipeline(['El', 'gato', 'come', 'pan.', None, None, None, 'La', 'luna', 'baila']), 'mots', []
        return sortie_pipeline(["Sous-titres", "réalisés", "par", "la", "communauté", "d'Amara.org"]), 'mots', ['x : y']

    def test_transcrire_video(self):
        r = {}
        lignes = N.transcrire_video(self.v, self.x16, self.detecter, self.transcrire, r)
        es = [a for a in self.appels if isinstance(a, tuple) and a[1] == 'es'][0]
        self.assertAlmostEqual(es[0], 7.0)                                  # phrases 1 et 2 dans un seul morceau
        audio = es[2]
        self.assertTrue(np.all(audio[int(3.1 * 16000):int(3.9 * 16000)] == 0))   # la pause entre les phrases : muette
        self.assertAlmostEqual(float(audio[int(1.0 * 16000)]), 0.1, places=6)
        self.assertEqual([l['texte'] for l in lignes], ['El gato come pan.', 'La luna baila'])
        self.assertAlmostEqual(lignes[0]['debut'], 1.0)                     # temps de la piste, pas du morceau
        self.assertAlmostEqual(lignes[1]['debut'], 1.0 + 1.6 + 3.0, places=2)
        self.assertEqual(r['langue'], 'es')
        self.assertEqual(r['modes'], {'mots': 2})
        self.assertEqual(r['retraits']['lignes'], 1)                        # le générique inventé
        self.assertEqual([p[4] for p in r['langues_phrases']], ['es', 'es', 'fr'])
        self.assertEqual(r['erreurs'], ['x : y'])
        self.assertEqual(r['lignes'], 2)

    def test_echec_du_pipeline_et_mots_sans_fin(self):
        def rien(audio, lg):
            return None, None, ['mots : RuntimeError()'] * 4

        r = {}
        self.assertEqual(N.transcrire_video(self.v, self.x16, lambda a: ('es', 0.9), rien, r), [])
        self.assertEqual(r['modes'], {'échec': 2})

        def segments(audio, lg):
            return {'text': ' uno dos tres', 'chunks': [{'text': ' uno dos tres', 'timestamp': (0.5, None)},
                                                        {'text': ' sin inicio', 'timestamp': (None, 1.0)},
                                                        {'text': None, 'timestamp': (1.0, 2.0)}]}, 'segments', []
        lignes = N.transcrire_video({**self.v, 'phrases': [[1.0, 4.0]]}, self.x16, lambda a: ('es', 0.9), segments, {})
        self.assertEqual([l['texte'] for l in lignes], ['uno dos tres'])
        self.assertAlmostEqual(lignes[0]['debut'], 1.5)
        self.assertAlmostEqual(lignes[0]['fin'], 2.0)

    def test_phrase_trop_courte_pour_la_langue(self):
        r = {}
        v = {'phrases': [[1.0, 1.05], [2.0, 6.0]], 'chant': [[1.0, 6.0]], 'piste': 'voix'}
        N.transcrire_video(v, self.x16, lambda a: ('fr', 0.99), lambda a, lg: (None, None, []), r)
        self.assertEqual([p[2] for p in r['langues_phrases']], [None, 'fr'])
        self.assertEqual([p[4] for p in r['langues_phrases']], ['fr', 'fr'])

    def test_traduire_lignes(self):
        textes = {'v1': [{'texte': 'el gato', 'langue': 'es'}, {'texte': 'la lune', 'langue': 'fr'},
                         {'texte': 'the cat', 'langue': 'en'}, {'texte': 'neko', 'langue': 'xx'}],
                  'v2': [{'texte': 'la luna', 'langue': 'es'}]}
        lots = []

        def lot(lg, lignes):
            lots.append((lg, list(lignes)))
            return None if lg == 'xx' else [f'[{lg}] {t}' for t in lignes]
        trad, info = N.traduire_lignes(textes, lot, taille_lot=1)
        self.assertEqual(trad['v1'], ['[es] el gato', 'la lune', '[en] the cat', 'neko'])
        self.assertEqual(trad['v2'], ['[es] la luna'])
        self.assertEqual(info, {'lignes_traduites': 3, 'langues_sans_traduction': ['xx']})
        self.assertNotIn('fr', [lg for lg, _ in lots])
        trad, _ = N.traduire_lignes({'v': [{'texte': 'la lune', 'langue': 'fr'}]}, lot, toujours=True)
        self.assertEqual(trad['v'], ['[fr] la lune'])
        trad, _ = N.traduire_lignes({'v': [{'texte': 'el gato', 'langue': 'es'}]}, lambda lg, l: ['  '])
        self.assertEqual(trad['v'], ['el gato'])   # traduction vide : la ligne d'origine reste


class CopieLocale(unittest.TestCase):
    """copier_paroles / chemins_paroles : les modèles du montage copiés d'un trait sur le disque local, en tâche de fond ;
    sinon le montage, sinon Hugging Face."""

    def test_copie_puis_chemins(self):
        import json
        from commun import Dossier
        with Dossier() as d:
            outils = d / 'outils'
            for nom in ('whisper-large-v3', 'nllb-200-distilled-600M'):
                (outils / 'paroles' / nom).mkdir(parents=True)
                (outils / 'paroles' / nom / 'config.json').write_text('{}')
                (outils / 'paroles' / nom / 'model.safetensors').write_bytes(b'x' * 1000)
            (outils / 'outils.json').write_text(json.dumps({'paroles': {'whisper': 'paroles/whisper-large-v3',
                                                                        'traducteur': 'paroles/nllb-200-distilled-600M'}}))
            etat = N.copier_paroles(str(outils), str(d / 'local'))
            chemins, attente = N.chemins_paroles(etat)
            self.assertEqual(chemins, {'whisper': str(d / 'local' / 'whisper-large-v3'),
                                       'traducteur': str(d / 'local' / 'nllb-200-distilled-600M')})
            self.assertEqual((d / 'local' / 'whisper-large-v3' / 'model.safetensors').stat().st_size, 1000)
            self.assertIsNotNone(etat['duree_s'])
            self.assertIsNone(etat['erreur'])
            self.assertFalse((d / 'local' / 'whisper-large-v3.part').exists())

    def test_copie_impossible_ou_montage_absent(self):
        import json
        from commun import Dossier
        with Dossier() as d:
            outils = d / 'outils'
            (outils / 'paroles' / 'whisper-large-v3').mkdir(parents=True)
            (outils / 'paroles' / 'whisper-large-v3' / 'config.json').write_text('{}')
            (outils / 'outils.json').write_text(json.dumps({'paroles': {'whisper': 'paroles/whisper-large-v3',
                                                                        'traducteur': 'paroles/absent'}}))
            bloque = d / 'fichier'
            bloque.write_text('pas un dossier')   # la destination ne peut pas être créée : le montage reste
            etat = N.copier_paroles(str(outils), str(bloque))
            chemins, _ = N.chemins_paroles(etat)
            self.assertEqual(chemins, {'whisper': str(outils / 'paroles' / 'whisper-large-v3')})
            self.assertIn('whisper', etat['erreur'])
        self.assertEqual(N.chemins_paroles(N.copier_paroles(None))[0], {})


class Chargement(unittest.TestCase):
    """depuis_hf : montage puis Hugging Face, options refusées retirées une à une (API de transformers changée)."""

    def setUp(self):
        import sys
        import types
        faux = types.ModuleType('torch')
        faux.float16 = 'float16'
        if 'torch' not in sys.modules:   # sur le PC, pas de torch : un faux le temps de l'essai
            sys.modules['torch'] = faux
            self.addCleanup(sys.modules.pop, 'torch', None)

    def classe(self, refus=(), absentes=()):
        appels = []

        class Fausse:
            @classmethod
            def from_pretrained(cls, src, **k):
                appels.append((src, sorted(k)))
                if src in absentes:
                    raise OSError(f'{src} : config.json introuvable')
                for o in refus:
                    if o in k:
                        raise TypeError(f"from_pretrained() got an unexpected keyword argument '{o}'")
                return ('modèle', src, k.get('dtype') or k.get('torch_dtype'))
        return Fausse, appels

    def test_montage_puis_internet(self):
        Fausse, appels = self.classe(absentes=('/montage/whisper',))
        obj, src, erreurs = N.depuis_hf(Fausse, ['/montage/whisper', 'openai/whisper-large-v3'], '/tmp/cache')
        self.assertEqual(src, 'openai/whisper-large-v3')
        self.assertEqual(obj[2], 'float16')
        self.assertEqual(len(erreurs), 1)
        obj, src, _ = N.depuis_hf(Fausse, [None, 'openai/whisper-large-v3'], '/tmp/cache', demi=False)
        self.assertEqual(appels[-1], ('openai/whisper-large-v3', ['cache_dir']))

    def test_options_refusees(self):
        Fausse, appels = self.classe(refus=('dtype', 'attn_implementation'))
        obj, src, erreurs = N.depuis_hf(Fausse, ['modele'], '/tmp/cache', attn_implementation='eager')
        self.assertEqual(obj[2], 'float16')
        self.assertEqual(appels[-1], ('modele', ['cache_dir', 'torch_dtype']))
        with self.assertRaises(RuntimeError):
            N.depuis_hf(self.classe(absentes=('a', 'b'))[0], ['a', 'b'], '/tmp/cache')


if __name__ == '__main__':
    unittest.main()
