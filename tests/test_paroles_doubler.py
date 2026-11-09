"""Paroles, côté doubler.py : clé « paroles » et --sans-paroles, lecture de paroles.json (cas abîmés compris), calage
sur la vidéo de sortie (extraits, idempotent pour --remix), écriture des .srt et du .lrc, noms des fichiers, estimation
de la session. Textes inventés ; HOME est un dossier temporaire."""
import contextlib
import io
import json
import os
import subprocess
import unittest

from commun import DOUBLER, Dossier, charger_doubler, python


def paroles(langue='es', decalage=0.0, traduction=True):
    segs = [{'debut': 1.0, 'fin': 2.5, 'texte': 'el gato come pan', 'langue': 'es',
             'mots': [{'debut': 1.0, 'fin': 1.3, 'mot': 'el'}, {'debut': 1.3, 'fin': 1.8, 'mot': 'gato'}]},
            {'debut': 3.0, 'fin': 3.2, 'texte': 'la lune danse', 'langue': 'fr', 'mots': []},
            {'debut': 8.0, 'fin': 9.0, 'texte': 'la luna baila', 'langue': 'es', 'mots': []}]
    d = {'format': 1, 'langue': langue, 'source': 'whisper-large-v3', 'segments': segs, 'traductions': {},
         'decalage_s': decalage}
    if traduction:
        d['traductions']['fr'] = {'source': 'nllb-200-distilled-600M', 'segments': [
            {'debut': 1.0, 'fin': 2.5, 'texte': 'le chat mange du pain'}, {'debut': 3.0, 'fin': 3.2, 'texte': 'la lune danse'},
            {'debut': 8.0, 'fin': 9.0, 'texte': 'la lune danse'}]}
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.D = charger_doubler(self.home)

    def silencieux(self, f, *a, **k):
        with contextlib.redirect_stdout(io.StringIO()):
            return f(*a, **k)


class Configuration(Base):
    def test_oui_non(self):
        for v in ('oui', 'OUI', 'true', '1', 'yes', True):
            self.assertIs(self.D.oui_non(v), True)
        for v in ('non', 'Non', 'false', '0', 'no', False):
            self.assertIs(self.D.oui_non(v), False)
        self.assertIsNone(self.D.oui_non('peut-être'))

    def test_cle_paroles(self):
        self.assertIn('paroles', self.D.CLES_CONFIG)
        self.assertIs(self.D.paroles_config({}), True)
        self.silencieux(self.D.ecrire_config, 'paroles', 'non')
        conf = json.loads(self.D.CONFIG.read_text())
        self.assertIs(conf['paroles'], False)
        self.assertIs(self.D.paroles_config(conf), False)
        self.silencieux(self.D.ecrire_config, 'paroles', 'oui')
        self.assertIs(json.loads(self.D.CONFIG.read_text())['paroles'], True)
        with self.assertRaises(SystemExit):
            self.D.ecrire_config('paroles', 'peut-être')
        self.assertIs(self.silencieux(self.D.paroles_config, {'paroles': 'bof'}), True)
        self.assertIs(self.D.paroles_config({'paroles': 'non'}), False)

    def test_ligne_de_commande(self):
        env = {**os.environ, 'HOME': str(self.home)}
        r = subprocess.run([python(), str(DOUBLER), '--help'], capture_output=True, text=True, env=env, timeout=60)
        self.assertIn('--sans-paroles', r.stdout)
        r = subprocess.run([python(), str(DOUBLER), '--config', 'paroles', 'non'], capture_output=True, text=True, env=env,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([python(), str(DOUBLER), '--config', '--json'], capture_output=True, text=True, env=env, timeout=60)
        self.assertIs(json.loads(r.stdout)['paroles'], False)
        r = subprocess.run([python(), str(DOUBLER), '--config', 'paroles', 'bof'], capture_output=True, text=True, env=env,
                           timeout=60)
        self.assertNotEqual(r.returncode, 0)

    def test_estimation_avec_et_sans_paroles(self):
        avec = self.D.estimer_noyau_s([25.0], 30, self.tmp / 'sorties')
        sans = self.D.estimer_noyau_s([25.0], 30, self.tmp / 'sorties', paroles=False)
        self.assertGreater(avec, sans)
        # sans historique : 25 s de part fixe (copie locale des modèles), 0,45 s par seconde d'audio
        self.assertAlmostEqual(avec - sans, 25 + 25 * 0.45, delta=1)

    def test_estimation_d_apres_l_historique_des_paroles(self):
        """Seuls les noyaux qui copient les modèles sur le disque local comptent (avant : 5 min de chargement)."""
        def rapport(nom, sk):
            d = self.tmp / 'sorties' / nom / 'travail' / 'v'
            d.mkdir(parents=True)
            (d / 'rapport.json').write_text(json.dumps({
                'duree_s': 100.0, 'temps': {'paroles_s': 20.0},
                'temps_local': {'videos_dans_la_session': 1, 'session_kaggle': sk}}))
        rapport('ancien', {'paroles_total_s': 334.8})
        sans_historique = self.D.estimer_noyau_s([25.0], 30, self.tmp / 'sorties')
        rapport('nouveau', {'paroles_total_s': 60.0, 'paroles_copie_s': 40.0})
        avec = self.D.estimer_noyau_s([25.0], 30, self.tmp / 'sorties')
        # le débit de transcription de l'ancien rapport reste valable (0,2 s par seconde) ; seul le fixe change (25 s
        # sans historique)
        self.assertAlmostEqual(sans_historique - avec, 25 - 40, delta=1)


class Lecture(Base):
    def ecrire(self, contenu):
        f = self.tmp / 'paroles.json'
        f.write_text(contenu if isinstance(contenu, str) else json.dumps(contenu), encoding='utf-8')
        return f

    def test_cas_abimes(self):
        self.assertEqual(self.D.lire_paroles(self.tmp / 'absent.json'), (None, 'pas de paroles.json'))
        d, raison = self.D.lire_paroles(self.ecrire('{"format": 1, "segm'))
        self.assertIsNone(d)
        self.assertIn('illisible', raison)
        for mauvais in ([], {'format': 1}, {'segments': 'texte'}):
            self.assertIsNone(self.D.lire_paroles(self.ecrire(mauvais))[0])
        d, _ = self.D.lire_paroles(self.ecrire({
            'langue': 7, 'decalage_s': 'NaN', 'traductions': {'fr': {'segments': 'non'}, 'de': 'rien'},
            'segments': [{'debut': 2, 'fin': 3, 'texte': '  b  '}, {'debut': 'x', 'fin': 1, 'texte': 'a'},
                         {'debut': 0, 'texte': 'sans fin'}, {'debut': 1, 'fin': 0.5, 'texte': 'fin avant'},
                         {'debut': float('nan'), 'fin': 1, 'texte': 'nan'}, {'debut': 0, 'fin': 1, 'texte': '   '},
                         'pas un dict', {'debut': 4, 'fin': 5, 'texte': 'c', 'mots': [{'debut': 4, 'fin': 4.5}, 'x']}]}))
        self.assertEqual([(s['debut'], s['fin'], s['texte']) for s in d['segments']],
                         [(1.0, 1.0, 'fin avant'), (2.0, 3.0, 'b'), (4.0, 5.0, 'c')])
        self.assertEqual(d['segments'][2]['mots'], [])
        self.assertEqual(d['traductions'], {})
        self.assertEqual(d['decalage_s'], 0.0)
        self.assertIsNone(d['langue'])

    def test_calage_idempotent_et_extrait(self):
        d, _ = self.D.lire_paroles(self.ecrire(paroles()))
        d = self.D.caler_paroles(d, -0.1315)   # le calage des extraits mkv avant le 05/11/2026 (décalage donné à ffmpeg)
        self.assertAlmostEqual(d['segments'][0]['debut'], 0.8685, places=4)
        self.assertAlmostEqual(d['segments'][0]['mots'][1]['fin'], 1.6685, places=4)
        self.assertAlmostEqual(d['traductions']['fr']['segments'][2]['debut'], 7.8685, places=4)
        d2 = self.D.caler_paroles(json.loads(json.dumps(d)), -0.1315)   # --remix : rien ne bouge
        self.assertEqual(d2['segments'], d['segments'])
        d3 = self.D.caler_paroles(d2, 0.0)          # --remix d'un ancien extrait mkv : retour au temps de la vidéo
        self.assertAlmostEqual(d3['segments'][0]['debut'], 1.0, places=6)
        d4 = self.D.caler_paroles(json.loads(json.dumps(d3)), -1.5)      # à cheval sur le début de l'image : rogné
        self.assertEqual(d4['segments'][0]['debut'], 0.0)
        self.assertAlmostEqual(d4['segments'][0]['fin'], 1.0, places=6)
        self.assertEqual(len(d4['segments']), 3)
        d5 = self.D.caler_paroles(d3, -2.6)                              # entièrement avant : retiré
        self.assertEqual(len(d5['segments']), 2)
        self.assertAlmostEqual(d5['segments'][0]['debut'], 0.4, places=6)


class Fichiers(Base):
    def test_srt(self):
        f = self.tmp / 'a.srt'
        segs = [{'debut': 1.0, 'fin': 1.2, 'texte': 'court'},
                {'debut': 1.5, 'fin': 4.0, 'texte': 'une ligne bien trop longue pour tenir sur une seule ligne'},
                {'debut': 3.5, 'fin': 5.0, 'texte': 'chevauche'}, {'debut': 3725.25, 'fin': 3727.0, 'texte': 'tard'}]
        self.assertEqual(self.D.ecrire_srt(segs, f), 4)
        blocs = f.read_text(encoding='utf-8').split('\n\n')
        self.assertEqual(blocs[0], '1\n00:00:01,000 --> 00:00:01,499\ncourt')   # lu 1 s, sans empiéter sur la suivante
        self.assertEqual(blocs[1].splitlines()[1], '00:00:01,500 --> 00:00:03,499')
        self.assertEqual(len(blocs[1].splitlines()), 4)                         # coupée en deux lignes
        self.assertTrue(all(len(l) <= 42 for l in blocs[1].splitlines()[2:]))
        self.assertEqual(blocs[3], '4\n01:02:05,250 --> 01:02:07,000\ntard')
        self.assertEqual(self.D.ecrire_srt(segs, f, duree_max=4.2), 3)
        self.assertIn('00:00:03,500 --> 00:00:04,200', f.read_text())
        self.assertEqual(self.D.ecrire_srt([], f), 0)
        self.assertEqual(f.read_text(), '')

    def test_lrc(self):
        f = self.tmp / 'a.lrc'
        segs = [{'debut': 0.8685, 'fin': 2.3685, 'texte': 'el gato come pan'},
                {'debut': 2.8685, 'fin': 3.0685, 'texte': 'la lune danse'},
                {'debut': 61.0, 'fin': 62.0, 'texte': 'la luna baila'}]
        self.assertEqual(self.D.ecrire_lrc(segs, f, -0.1315, 'Titre', 'es'), 3)
        lignes = f.read_text(encoding='utf-8').splitlines()
        self.assertEqual(lignes[:3], ['[ti:Titre]', '[la:es]', '[re:Voix-là (Whisper large-v3)]'])
        self.assertEqual(lignes[3:], ['[00:01.00]el gato come pan', '[00:03.00]la lune danse', '[00:03.20]',
                                      '[01:01.13]la luna baila', '[01:02.13]'])

    def test_preparer_paroles_espagnol_et_traduction(self):
        tv, sortie = self.tmp / 'travail' / 'v', self.tmp / 'sortie'
        tv.mkdir(parents=True)
        sortie.mkdir()
        (tv / 'paroles.json').write_text(json.dumps(paroles()))
        pistes, fichiers, info = self.D.preparer_paroles(tv, sortie, 'Clip - voix Test', 0.0, 10.0)
        self.assertEqual(sorted(f.name for f in fichiers),
                         ['Clip - voix Test.es.srt', 'Clip - voix Test.fr.srt', 'Clip - voix Test.lrc'])
        self.assertEqual([(p['langue'], p['titre']) for p in pistes],
                         [('spa', 'Paroles (espagnol)'), ('fre', 'Paroles (français, traduction)')])
        self.assertIn('le chat mange du pain', (sortie / 'paroles' / 'Clip - voix Test.fr.srt').read_text())
        self.assertTrue(info['traduction'])
        self.assertEqual(json.loads((tv / 'paroles.json').read_text())['decalage_s'], 0.0)
        self.assertEqual([p['fichier'] for p in pistes], [f for f in fichiers if f.suffix == '.srt'])
        # même décalage pour ffmpeg : les pistes de la vidéo sont les .srt d'à côté
        pistes, fichiers, _ = self.D.preparer_paroles(tv, sortie, 'Clip - voix Test', 0.0, 10.0, decalage_ffmpeg=0.0)
        self.assertEqual([p['fichier'] for p in pistes], [f for f in fichiers if f.suffix == '.srt'])
        self.assertEqual(list(tv.glob(self.D.PISTES_PAROLES_TEMP)), [])

    def test_preparer_paroles_extrait_mkv(self):
        """Extrait mkv ou webm : le mix commence avec l'image (debut_son 0) mais ffmpeg le reçoit décalé de -0,13 s
        (decalage_son), comme l'image : paroles.json, .srt et .lrc au temps de la vidéo doublée, les pistes de la
        vidéo à part, au temps de la coupe (le conteneur les recale avec l'image et le son)."""
        tv, sortie = self.tmp / 'travail' / 'v', self.tmp / 'sortie'
        tv.mkdir(parents=True)
        sortie.mkdir()
        p = paroles()
        p['segments'].insert(0, {'debut': 0.05, 'fin': 0.1, 'texte': 'hola', 'langue': 'es'})
        (tv / 'paroles.json').write_text(json.dumps(p))
        pistes, fichiers, info = self.D.preparer_paroles(tv, sortie, 'Serie - voix Test', 0.0, 8.5,
                                                         decalage_ffmpeg=-0.131435)
        d = json.loads((tv / 'paroles.json').read_text())
        self.assertEqual((d['decalage_s'], info['decalage_s']), (0.0, 0.0))
        self.assertEqual([s['debut'] for s in d['segments']], [0.05, 1.0, 3.0, 8.0])
        srt = (sortie / 'paroles' / 'Serie - voix Test.es.srt').read_text()
        self.assertTrue(srt.startswith('1\n00:00:00,050 --> 00:00:00,999\nhola\n\n2\n00:00:01,000 --> '), srt)
        self.assertIn('00:00:08,000 --> 00:00:08,500\nla luna baila', srt)   # coupée à la fin de la vidéo
        self.assertIn('[00:01.00]el gato come pan', (sortie / 'Serie - voix Test.lrc').read_text())
        self.assertEqual(sorted(f.name for f in fichiers),
                         ['Serie - voix Test.es.srt', 'Serie - voix Test.fr.srt', 'Serie - voix Test.lrc'])
        self.assertEqual([(q['fichier'].parent, q['langue']) for q in pistes], [(tv, 'spa'), (tv, 'fre')])
        self.assertEqual(sorted(tv.glob(self.D.PISTES_PAROLES_TEMP)), sorted(q['fichier'] for q in pistes))
        video = pistes[0]['fichier'].read_text()
        self.assertTrue(video.startswith('1\n00:00:00,869 --> '), video)       # « hola », avant la coupe : retiré
        self.assertIn('00:00:07,869 --> 00:00:08,369\nla luna baila', video)   # fin de la vidéo au temps de la coupe
        self.assertIn('le chat mange du pain', pistes[1]['fichier'].read_text())

    def test_preparer_paroles_francais_et_cas_vides(self):
        tv, sortie = self.tmp / 'tv', self.tmp / 's'
        tv.mkdir()
        sortie.mkdir()
        (tv / 'paroles.json').write_text(json.dumps(paroles('fr', traduction=False)))
        pistes, fichiers, _ = self.D.preparer_paroles(tv, sortie, 'B', 0.0)
        self.assertEqual(sorted(f.name for f in fichiers), ['B.fr.srt', 'B.lrc'])
        self.assertEqual([p['titre'] for p in pistes], ['Paroles (français)'])
        (tv / 'paroles.json').write_text(json.dumps(paroles('fr')))
        _, fichiers, _ = self.D.preparer_paroles(tv, sortie, 'C', 0.0)
        self.assertIn('C.traduction.fr.srt', [f.name for f in fichiers])   # pas d'écrasement des paroles
        (tv / 'paroles.json').write_text(json.dumps({'format': 1, 'langue': None, 'segments': [], 'traductions': {}}))
        self.assertEqual(self.D.preparer_paroles(tv, sortie, 'D', 0.0)[:2], ([], []))
        (tv / 'paroles.json').write_text('{abîmé')
        pistes, fichiers, info = self.D.preparer_paroles(tv, sortie, 'E', 0.0)
        self.assertEqual((pistes, fichiers), ([], []))
        self.assertIn('illisible', info['raison'])
        (tv / 'paroles.json').unlink()
        self.assertEqual(self.D.preparer_paroles(tv, sortie, 'F', 0.0)[2]['raison'], 'pas de paroles.json')
        (tv / 'paroles.json').write_text(json.dumps({**paroles('xx'), 'traductions': {}}))
        pistes, _, _ = self.D.preparer_paroles(tv, sortie, 'G', 0.0)
        self.assertEqual((pistes[0]['langue'], pistes[0]['titre']), ('und', 'Paroles (xx)'))

    def test_langues(self):
        self.assertEqual(self.D.langue_piste('es'), ('spa', 'espagnol'))
        self.assertEqual(self.D.langue_piste('fr'), ('fre', 'français'))
        self.assertEqual(self.D.langue_piste(None), ('und', 'langue inconnue'))
        self.assertTrue(all(len(c) == 3 for c, _ in self.D.LANGUES.values()))


if __name__ == '__main__':
    unittest.main()
