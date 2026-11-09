"""doubler sur un fichier audio seul (mp3, wav, flac, m4a, ogg, opus) : détection (la pochette d'un mp3 n'est pas une
image), extraction du son (extrait exact, sans image clé), mixage et sorties sans image (mp3, .lrc, .srt, aucune vidéo,
aucune piste audio en plus), auto-tune sans vidéo, --remix en ligne de commande. Sons et paroles de synthèse."""
import argparse
import contextlib
import io
import json
import os
import subprocess
import unittest

from commun import DOUBLER, FFMPEG, SR, Dossier, charger_doubler, ecrire_wav, ffprobe, python
from test_pistes_audio import JULES, PAROLES, bruit, travail, video

FORMATS = {'.wav': [], '.flac': [], '.mp3': ['-c:a', 'libmp3lame'], '.ogg': ['-c:a', 'libvorbis'],
           '.opus': ['-c:a', 'libopus'], '.m4a': ['-c:a', 'aac']}


def son(chemin, duree=6.0, pochette=False):
    """Fichier audio de synthèse ; pochette : une image jointe (mp3, comme une pochette d'album)."""
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'sine=frequency=440:duration={duree}:sample_rate={SR}']
    if pochette:
        cmd += ['-f', 'lavfi', '-i', 'testsrc=size=64x64:rate=1:duration=1', '-map', '0:a', '-map', '1:v', '-c:v',
                'mjpeg', '-disposition:v', 'attached_pic', '-id3v2_version', '3']
    cmd += FORMATS[chemin.suffix] + [str(chemin)]
    subprocess.run(cmd, check=True)
    return chemin


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class SonSeul(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        (self.tmp / 'home').mkdir()
        self.D = charger_doubler(self.tmp / 'home')
        (self.tmp / 'sons').mkdir()

    def test_detection(self):
        for ext in FORMATS:
            f = son(self.tmp / 'sons' / f'Chanson{ext}')
            self.assertFalse(self.D.a_une_image(self.D.sonder(f)), ext)
        f = son(self.tmp / 'sons' / 'Pochette.mp3', pochette=True)
        self.assertFalse(self.D.a_une_image(self.D.sonder(f)), 'la pochette jointe n\'est pas une image')
        v = video(self.tmp / 'sons' / 'Clip.mp4', 2.0)
        self.assertTrue(self.D.a_une_image(self.D.sonder(v)))

    def test_extraction_entiere_et_extrait(self):
        for ext in FORMATS:
            f = son(self.tmp / 'sons' / f'Chanson{ext}', 6.0)
            t = self.tmp / f'travail{ext}'
            t.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                src = self.D.preparer_source(f, self.D.sonder(f), None, None, t)
            self.assertTrue(src['audio_seul'], ext)
            self.assertNotIn('debut', src)
            self.assertAlmostEqual(self.D.duree_s(t / 'original.wav'), 6.0, delta=0.15, msg=ext)
        f = self.tmp / 'sons' / 'Chanson.flac'
        t = self.tmp / 'travail-extrait'
        t.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            src = self.D.preparer_source(f, self.D.sonder(f), 2.0, 3.0, t)
        self.assertEqual((src['debut'], src['duree'], src['decalage_son']), (2.0, 3.0, 0.0))   # exactement où demandé
        self.assertAlmostEqual(self.D.duree_s(t / 'original.wav'), 3.0, delta=0.02)
        # fin après la fin du son : coupée ; début après la fin : refusé
        t2 = self.tmp / 'travail-fin'
        t2.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            src = self.D.preparer_source(f, self.D.sonder(f), 4.0, 10.0, t2)
        self.assertAlmostEqual(src['duree'], 2.0, delta=0.05)
        with self.assertRaises(RuntimeError):
            self.D.preparer_source(f, self.D.sonder(f), 30.0, None, self.tmp / 'travail-extrait')

    def finir(self, nom, ext, extrait=None, paroles=False, autotune=None):
        source = son(self.tmp / 'sons' / f'{nom}{ext}', 8.0)
        dossier = self.tmp / 'sorties' / nom
        t, tv, src = travail(self.D, source, dossier, JULES, extrait, paroles=paroles)
        a = argparse.Namespace(voix_db=None, mix_v1=False, reverb_db=-13.0, paroles=bool(paroles), pistes_audio='toutes',
                               autotune=autotune, sans_autotune=False, autotune_gamme=None)
        a.autotune_reglage = self.D.reglage_autotune(a, {})
        tr = {'travail': t, 'tv': tv, 'dossier': dossier, 'base': f'{nom} - voix Jules', 'nom': nom}
        with contextlib.redirect_stdout(io.StringIO()) as texte:
            sortie = self.D.finir(tr, a, JULES)
        return sortie, dossier, t, tv, src, texte.getvalue()

    def test_sorties_sans_image(self):
        for ext in ('.mp3', '.wav', '.flac', '.m4a'):
            sortie, dossier, t, tv, src, texte = self.finir(f'Titre{ext[1:]}', ext, paroles=True)
            nom = f'Titre{ext[1:]}'
            self.assertEqual(sortie, dossier / f'{nom} - voix Jules.mp3', ext)
            self.assertEqual(ffprobe(sortie, '-show_streams')['streams'][0]['codec_type'], 'audio')
            self.assertEqual(sorted(f.suffix for f in dossier.iterdir() if f.is_file()), ['.lrc', '.mp3'])
            self.assertTrue((dossier / 'paroles' / f'{nom} - voix Jules.es.srt').is_file())
            self.assertIn(f'fini : {sortie}', texte)
            rapport = json.loads((tv / 'rapport.json').read_text())
            self.assertIsNone(rapport['pistes_audio']['video'])
            self.assertEqual(rapport['paroles_sorties']['pistes_video'], [])
            self.assertEqual([f.name for f in tv.glob('.*')], [])
            self.assertTrue(src['audio_seul'])

    def test_extrait_et_sans_paroles(self):
        sortie, dossier, t, tv, src, _ = self.finir('Extrait', '.flac', extrait=(2.0, 4.0))
        self.assertAlmostEqual(self.D.duree_s(sortie), 4.0, delta=0.15)
        self.assertEqual([f.name for f in dossier.iterdir() if f.is_file()], ['Extrait - voix Jules.mp3'])

    def test_auto_tune_sans_video(self):
        from test_pistes_audio import FAT1
        if not FAT1:
            self.skipTest('x42-autotune absent')
        sortie, dossier, t, tv, src, _ = self.finir('Tune', '.mp3', autotune=60)
        self.assertEqual(sorted(f.suffix for f in dossier.iterdir() if f.is_file()), ['.mp3', '.mp3'])
        info = json.loads((tv / 'rapport.json').read_text())['autotune']
        self.assertTrue(info['applique'])
        self.assertIsNone(info['fichiers']['video'])

    def test_remix_en_ligne_de_commande(self):
        env = {**os.environ, 'HOME': str(self.tmp / 'home')}
        for v in ('VOIXLA_KAGGLE', 'VOIXLA_DONNEES', 'DOUBLER_VOIX'):
            env.pop(v, None)
        donnees = self.tmp / 'donnees'
        (donnees / 'voix' / 'jules').mkdir(parents=True)
        ecrire_wav(donnees / 'voix' / 'jules' / 'reference.wav', bruit(SR, 5, -20))
        (donnees / 'voix' / 'jules' / 'voix.json').write_text(json.dumps(
            {'nom': 'Jules', 'reference': 'reference.wav', 'hauteur_parole_mediane_hz': 120.0}))
        source = son(self.tmp / 'sons' / 'Remix.mp3', 8.0)
        dossier = donnees / 'sorties' / 'Remix (extrait 0m02,0+0m04,0)'
        travail(self.D, source, dossier, JULES, (2.0, 4.0), paroles=True)
        r = subprocess.run([python(), str(DOUBLER), str(source), '--remix', '--voix', 'jules', '--debut', '2', '--duree',
                            '4', '--donnees', str(donnees)], capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        mp3 = dossier / 'Remix - voix Jules.mp3'
        self.assertTrue(mp3.is_file())
        self.assertEqual([f.suffix for f in dossier.iterdir() if f.is_file() and f.suffix != '.lrc'], ['.mp3'])
        self.assertIn(f'fini : {mp3}', r.stdout)
        # --sans-paroles : le .lrc n'est plus refait mais le mp3 l'est
        r = subprocess.run([python(), str(DOUBLER), str(source), '--remix', '--voix', 'jules', '--debut', '2', '--duree',
                            '4', '--sans-paroles', '--donnees', str(donnees)], capture_output=True, text=True, env=env,
                           timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])


if __name__ == '__main__':
    unittest.main()
