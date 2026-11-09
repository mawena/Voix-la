"""Paroles d'une vidéo doublée (09/11/2026) : en mp4, m4v et mov, ffmpeg 6.1 pose toujours le drapeau « enabled » sur la
première piste de sous-titres, et mpv la sélectionne alors d'office ; doubler l'efface (tkhd) quand aucune piste gardée
n'est « default ». Les .srt vont dans paroles/ (VLC et mpv les chargent sinon d'office à côté de la vidéo), le .lrc reste à
côté du mp3 ; --remix range ceux d'un ancien doublage."""
import contextlib
import io
import json
import subprocess
import unittest

from commun import FFMPEG, Dossier, charger_doubler, ecrire_wav, ffprobe
from test_pistes_audio import bruit, video
from test_paroles_doubler import paroles

SRT = '1\n00:00:01,000 --> 00:00:02,000\nel gato come pan\n\n'


def pistes_st(fichier):
    return [(s['tags'].get('title') or s['tags'].get('handler_name'), s['disposition']['default'])
            for s in ffprobe(fichier, '-show_streams')['streams'] if s['codec_type'] == 'subtitle']


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class SousTitresMp4(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        (self.tmp / 'home').mkdir()
        self.D = charger_doubler(self.tmp / 'home')

    def remettre(self, ext, paroles_en_plus, sous_titre_origine=False, defaut_origine=True):
        """defaut_origine : la piste de sous-titres d'origine est « default » (ffmpeg l'active d'office dans un mp4) ;
        faux : son drapeau est effacé de la source."""
        source = video(self.tmp / f'Clip{ext}', 4.0, sous_titre=sous_titre_origine)
        if sous_titre_origine and not defaut_origine:
            self.D.desactiver_sous_titres_mp4(source)
            self.assertEqual(pistes_st(source)[0][1], 0)
        t = self.tmp / 'travail'
        t.mkdir(exist_ok=True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.D.preparer_source(source, self.D.sonder(source), None, None, t)
            plan = self.D.plan_video(t)
        n = self.D.echantillons(t / 'original.wav')[0]
        ecrire_wav(self.tmp / 'mix.wav', bruit(n, 7, -18), canaux=2)
        srt = self.tmp / 'p.srt'
        srt.write_text(SRT, encoding='utf-8')
        st = [{'fichier': srt, 'langue': 'spa', 'titre': 'Paroles (espagnol)'}] if paroles_en_plus else []
        with contextlib.redirect_stdout(io.StringIO()):
            return self.D.remettre_sur_video(t, self.tmp / 'mix.wav', self.tmp, f'Clip{ext[1:]} - voix Jules', st, plan, (),
                                             'Doublage (voix Jules)', 'spa')

    def test_paroles_non_activees_en_mp4(self):
        for ext in ('.mp4', '.m4v', '.mov'):
            with self.subTest(ext=ext):
                sortie = self.remettre(ext, True)
                self.assertEqual(pistes_st(sortie), [('Paroles (espagnol)', 0)])
                r = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(sortie), '-map', '0:s:0', '-f', 'srt', '-'],
                                   capture_output=True, text=True, check=True)
                self.assertIn('el gato come pan', r.stdout)   # le sous-titre est intact

    def test_sous_titre_d_origine_par_defaut_garde(self):
        etat = pistes_st(self.remettre('.mp4', True, sous_titre_origine=True))
        self.assertEqual([d for _, d in etat], [1, 0], etat)   # celle d'origine reste par défaut

    def test_sous_titre_d_origine_sans_defaut(self):
        etat = pistes_st(self.remettre('.mp4', True, sous_titre_origine=True, defaut_origine=False))
        self.assertEqual([d for _, d in etat], [0, 0], etat)

    def test_sans_sous_titres_rien_a_faire(self):
        self.assertEqual(pistes_st(self.remettre('.mp4', False)), [])

    def test_mkv_non_touche(self):
        self.assertEqual(len(pistes_st(self.remettre('.mkv', True))), 1)


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class SrtDansParoles(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        (self.tmp / 'home').mkdir()
        self.D = charger_doubler(self.tmp / 'home')

    def test_srt_dans_paroles_lrc_a_cote_et_anciens_ranges(self):
        tv, sortie = self.tmp / 'tv', self.tmp / 's'
        tv.mkdir()
        sortie.mkdir()
        (tv / 'paroles.json').write_text(json.dumps(paroles()))
        for ancien in ('Clip - voix Test.es.srt', 'Clip - voix Test.fr.srt', 'Autre - voix Test.es.srt'):
            (sortie / ancien).write_text('ancien')
        _, fichiers, info = self.D.preparer_paroles(tv, sortie, 'Clip - voix Test', 0.0, 10.0)
        self.assertEqual(sorted(p.name for p in sortie.iterdir()),
                         ['Autre - voix Test.es.srt', 'Clip - voix Test.lrc', 'paroles'])
        self.assertEqual(sorted(p.name for p in (sortie / 'paroles').iterdir()),
                         ['Clip - voix Test.es.srt', 'Clip - voix Test.fr.srt'])
        self.assertEqual(info['srt_deplaces'], ['Clip - voix Test.es.srt', 'Clip - voix Test.fr.srt'])
        self.assertEqual(info['fichiers'], ['paroles/Clip - voix Test.es.srt', 'paroles/Clip - voix Test.fr.srt',
                                            'Clip - voix Test.lrc'])
        self.assertNotEqual((sortie / 'paroles' / 'Clip - voix Test.es.srt').read_text(), 'ancien')


if __name__ == '__main__':
    unittest.main()
