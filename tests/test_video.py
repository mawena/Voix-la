"""Mise en vidéo des paroles (ffmpeg) : pistes de sous-titres ajoutées (mov_text en mp4, srt en mkv) avec leur langue et
leur titre, sous-titres d'origine gardés, extrait calé sur la vidéo de sortie, puis finir() de bout en bout (mixage, mp3,
.srt, .lrc, événement « sortie »), --remix (idempotent) et sans paroles. Vidéos de synthèse de quelques secondes."""
import argparse
import contextlib
import io
import json
import subprocess
import unittest

import numpy as np

from commun import FFMPEG, SR, Dossier, charger_doubler, ecrire_wav, ffprobe, note


def video(chemin, duree=4.0, sous_titre=True):
    """Vidéo de synthèse (image clé chaque seconde), son, et une piste de sous-titres d'origine."""
    srt = chemin.with_suffix('.orig.srt')
    srt.write_text('1\n00:00:00,500 --> 00:00:01,500\nsous-titre d\'origine\n\n', encoding='utf-8')
    codec = 'mov_text' if chemin.suffix == '.mp4' else 'srt'
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size=128x96:rate=25:duration={duree}',
           '-f', 'lavfi', '-i', f'sine=frequency=440:duration={duree}:sample_rate={SR}']
    if sous_titre:
        cmd += ['-i', str(srt), '-map', '0', '-map', '1', '-map', '2', '-c:s', codec, '-metadata:s:s:0', 'language=eng']
    cmd += ['-c:v', 'mpeg4', '-g', '25', '-c:a', 'aac', str(chemin)]
    subprocess.run(cmd, check=True)
    return chemin


def paroles_json(debut=1.0):
    return {'format': 1, 'langue': 'es', 'source': 'whisper-large-v3', 'decalage_s': 0.0,
            'segments': [{'debut': debut, 'fin': debut + 1.0, 'texte': 'el gato come pan', 'langue': 'es',
                          'mots': [{'debut': debut, 'fin': debut + 0.4, 'mot': 'el'}]}],
            'traductions': {'fr': {'source': 'nllb-200-distilled-600M',
                                   'segments': [{'debut': debut, 'fin': debut + 1.0, 'texte': 'le chat mange du pain'}]}}}


def sous_titres(chemin):
    return [s for s in ffprobe(chemin, '-show_streams')['streams'] if s['codec_type'] == 'subtitle']


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Video(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        (self.tmp / 'home').mkdir()
        self.D = charger_doubler(self.tmp / 'home')

    def travail(self, source, extrait=None):
        t, tv = self.tmp / 'travail', self.tmp / 'travail' / 'test'
        tv.mkdir(parents=True, exist_ok=True)
        if extrait:
            with contextlib.redirect_stdout(io.StringIO()):
                self.D.preparer_source(source, self.D.sonder(source), extrait[0], extrait[1], t)
        else:
            (t / 'source.json').write_text(json.dumps({'video': str(source), 'flux_audio': 1, 'decalage_son': 0.0}))
            ecrire_wav(t / 'original.wav', note(440, 0, 4, -20, 4), canaux=2)
        (tv / 'paroles.json').write_text(json.dumps(paroles_json()))
        return t, tv

    def remettre(self, source, extrait=None):
        t, tv = self.travail(source, extrait)
        plan = self.D.plan_video(t)
        pistes, fichiers, info = self.D.preparer_paroles(tv, self.tmp, 'Essai - voix Test', plan['decalage_son'], 10.0)
        with contextlib.redirect_stdout(io.StringIO()):
            sortie = self.D.remettre_sur_video(t, t / 'original.wav', self.tmp, 'Essai - voix Test', pistes, plan)
        return sortie, plan

    def verifier_pistes(self, sortie, codec):
        st = sous_titres(sortie)
        self.assertEqual(len(st), 3, st)
        self.assertEqual([s['codec_name'] for s in st], [codec] * 3)
        tags = [s.get('tags', {}) for s in st]
        self.assertEqual([t.get('language') for t in tags], ['eng', 'spa', 'fre'])
        titres = [t.get('title') or t.get('handler_name') for t in tags[1:]]
        self.assertEqual(titres, ['Paroles (espagnol)', 'Paroles (français, traduction)'])
        self.assertEqual([s['disposition']['default'] for s in st[1:]], [0, 0])

    def test_mp4_mov_text_et_sous_titres_d_origine(self):
        sortie, _ = self.remettre(video(self.tmp / 'source.mp4'))
        self.assertEqual(sortie.suffix, '.mp4')
        self.verifier_pistes(sortie, 'mov_text')

    def test_mkv_srt_et_sous_titres_d_origine(self):
        sortie, _ = self.remettre(video(self.tmp / 'source.mkv'))
        self.verifier_pistes(sortie, 'subrip')

    def pts_paroles(self, sortie):
        """Instant (s, depuis le début du fichier) de la première réplique de la piste des paroles."""
        info = ffprobe(sortie, '-show_packets', '-show_format', '-select_streams', 's:1')
        debut = float(info['format'].get('start_time') or 0)
        pleins = [p for p in info['packets'] if int(p['size']) > 2]   # mp4 : échantillons vides entre les répliques
        return float(pleins[0]['pts_time']) - debut

    def test_extrait_cale_sur_la_video_de_sortie(self):
        for ext in ('.mp4', '.mkv'):
            with self.subTest(ext=ext):
                source = video(self.tmp / f'longue{ext}', duree=10.0)
                sortie, plan = self.remettre(source, (3.0, 4.0))
                d = json.loads((self.tmp / 'travail' / 'test' / 'paroles.json').read_text())
                self.assertAlmostEqual(d['decalage_s'], plan['decalage_son'], places=5)
                attendu = 1.0 + plan['decalage_son']   # 1 s après le début du son doublé
                self.assertAlmostEqual(self.pts_paroles(sortie), attendu, delta=0.06)
                self.assertAlmostEqual(d['segments'][0]['debut'], max(attendu, 0.0), places=5)
                srt = (self.tmp / 'Essai - voix Test.es.srt').read_text()
                self.assertTrue(srt.startswith('1\n'), srt)
                for f in (self.tmp / 'travail').iterdir():
                    if f.is_file():
                        f.unlink()

    def test_finir_de_bout_en_bout_remix_et_sans_paroles(self):
        source = video(self.tmp / 'clip.mp4')
        dossier = self.tmp / 'sorties' / 'clip'
        t, tv = dossier / 'travail', dossier / 'travail' / 'test'
        tv.mkdir(parents=True)
        (t / 'source.json').write_text(json.dumps({'video': str(source), 'flux_audio': 1, 'decalage_son': 0.0}))
        voix_o = note(220, 0.5, 3.5, -22, 4)
        ecrire_wav(t / 'original.wav', note(1000, 0, 4, -20, 4, harmoniques=1) + voix_o, canaux=2)   # sans harmonique commune
        ecrire_wav(t / 'voix_source.wav', voix_o, canaux=2)
        ecrire_wav(tv / 'voix.wav', note(230, 0.5, 3.5, -22, 4))
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(tv / 'voix.wav'), '-c:a', 'flac', '-sample_fmt', 's32',
                        str(tv / 'voix_convertie.flac')], check=True)
        (tv / 'rapport.json').write_text(json.dumps({'mode': 'chant', 'frise': [], 'secours': {
            'decision': 'secours', 'secondes': 1.2, 'zones': [{'debut_s': 1.0, 'fin_s': 2.2, 'ecart_db': 40.0}]}}))
        (tv / 'paroles.json').write_text(json.dumps(paroles_json()))
        evenements = self.tmp / 'ev.jsonl'
        self.D.EV.ouvrir(evenements)
        tr = {'travail': t, 'tv': tv, 'dossier': dossier, 'base': 'clip - voix Test', 'nom': 'clip'}
        a = argparse.Namespace(voix_db=None, mix_v1=False, reverb_db=-13.0, paroles=True)
        voix = {'id': 'test', 'affichage': 'Test'}
        with contextlib.redirect_stdout(io.StringIO()) as texte:
            sortie = self.D.finir(tr, a, voix)
        self.assertIn('passages perdus au nettoyage repris', texte.getvalue())
        self.assertIn('paroles (espagnol, 1 lignes, traduction française)', texte.getvalue())
        for nom in ('clip - voix Test.mp4', 'clip - voix Test.mp3', 'clip - voix Test.es.srt', 'clip - voix Test.fr.srt',
                    'clip - voix Test.lrc'):
            self.assertTrue((dossier / nom).exists(), nom)
        self.assertEqual(len(sous_titres(sortie)), 3)
        ev = [json.loads(l) for l in evenements.read_text().splitlines()]
        s = next(e for e in ev if e['type'] == 'sortie')
        self.assertEqual(s['paroles'], str(tv / 'paroles.json'))
        self.assertEqual(sorted(s['srt']), sorted(str(dossier / f'clip - voix Test.{x}.srt') for x in ('es', 'fr')))
        self.assertEqual(s['lrc'], str(dossier / 'clip - voix Test.lrc'))
        rapport = json.loads((tv / 'rapport.json').read_text())
        self.assertEqual(rapport['paroles_sorties']['pistes_video'], ['Paroles (espagnol)', 'Paroles (français, traduction)'])
        musique = rapport['mix']['musique_seule']
        self.assertLess(musique['correlation_reste_voix'], 0.3)
        avant = (tv / 'paroles.json').read_text()
        with contextlib.redirect_stdout(io.StringIO()):
            self.D.finir(tr, a, voix)                    # --remix : mêmes paroles, mêmes temps
        self.assertEqual((tv / 'paroles.json').read_text(), avant)
        a.paroles = False
        with contextlib.redirect_stdout(io.StringIO()):
            sortie = self.D.finir(tr, a, voix)
        self.assertEqual(len(sous_titres(sortie)), 1)   # seulement les sous-titres d'origine
        ev = [json.loads(l) for l in evenements.read_text().splitlines()]
        self.assertNotIn('paroles', [e for e in ev if e['type'] == 'sortie'][-1])

    def test_paroles_abimees_la_video_est_faite(self):
        source = video(self.tmp / 'abime.mkv', sous_titre=False)
        t, tv = self.travail(source)
        (tv / 'paroles.json').write_text('{"segments": [{"debut": "x"')
        plan = self.D.plan_video(t)
        pistes, _, info = self.D.preparer_paroles(tv, self.tmp, 'Abîmé', plan['decalage_son'])
        self.assertEqual(pistes, [])
        with contextlib.redirect_stdout(io.StringIO()):
            sortie = self.D.remettre_sur_video(t, t / 'original.wav', self.tmp, 'Abîmé', pistes, plan)
        self.assertEqual(sous_titres(sortie), [])
        self.assertTrue(np.isfinite(float(ffprobe(sortie, '-show_format')['format']['duration'])))


if __name__ == '__main__':
    unittest.main()
