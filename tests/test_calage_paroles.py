"""Calage des paroles sur la vidéo doublée, mesuré : un bip à 1,5 s du son doublé, une ligne de paroles au même instant
(paroles.json du noyau, en temps du son). Après finir(), le bip est cherché dans la vidéo doublée (son décodé, temps
du fichier, relatif à la première image) et dans le mp3 ; paroles.json, le .srt d'à côté et la piste de paroles de la
vidéo doivent tomber sur le bip de la vidéo, le .lrc sur celui du mp3, et debut - decalage_s sur le temps du son.
Cas : extrait mkv avec images B (ffmpeg recule de 0,13 s avant de chercher : le mix lui est donné décalé de -0,13 s,
mais le conteneur recale tout sur la première image), extrait mkv dont la copie part d'une image clé antérieure,
extrait mp4, extrait webm, mkv entier au son d'origine en retard, puis --remix d'un ancien extrait mkv (paroles calées
à -0,13 s avant le 05/11/2026). Vidéos et sons de synthèse, quelques secondes chacun."""
import argparse
import contextlib
import io
import json
import re
import subprocess
import unittest

import numpy as np

from commun import FFMPEG, SR, Dossier, charger_doubler, ecrire_wav, ffprobe

T_BIP = 1.5
JULES = {'id': 'jules', 'affichage': 'Jules'}
BASE = 'Chanson - voix Jules'


def encodeur(nom):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True)
    return f' {nom} ' in r.stdout


WEBM = FFMPEG and encodeur('libvpx') and encodeur('libopus')


def bruit(n, graine, niveau_db):
    y = np.random.default_rng(graine).standard_normal(n)
    return y * 10 ** (niveau_db / 20) / np.sqrt(np.mean(y ** 2))


def bip():
    """Glissando de 500 à 4000 Hz, 50 ms : un seul pic de corrélation, net, bien gardé par l'AAC, l'Opus et le MP3."""
    d = 0.05
    t = np.arange(int(d * SR)) / SR
    y = 0.5 * np.sin(2 * np.pi * (500 * t + 3500 / (2 * d) * t ** 2))
    r = SR // 1000
    y[:r] *= np.linspace(0, 1, r)
    y[-r:] *= np.linspace(1, 0, r)
    return y


def video(chemin, duree, retard=0.0):
    """Vidéo de synthèse (image clé chaque seconde ; images B, sauf en webm), son en retard de retard s sur l'image."""
    webm = chemin.suffix == '.webm'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size=128x96:rate=25:duration={duree}',
                    *(['-itsoffset', str(retard)] if retard else []),
                    '-f', 'lavfi', '-i', f'sine=frequency=440:duration={duree}:sample_rate={SR}', '-map', '0:v', '-map',
                    '1:a', *(['-c:v', 'libvpx', '-b:v', '300k'] if webm else ['-c:v', 'mpeg4', '-bf', '2']), '-g', '25',
                    '-c:a', 'libopus' if webm else 'aac', str(chemin)], check=True)
    return chemin


def decoder(fichier, temps_du_fichier=False):
    """Son mono 44,1 kHz ; temps_du_fichier : l'échantillon 0 au temps 0 du fichier (trou du début en silence)."""
    filtre = ['-copyts', '-i', str(fichier), '-af', f'aresample={SR}:async=1:first_pts=0'] if temps_du_fichier else \
        ['-i', str(fichier)]
    r = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', *filtre, '-map', '0:a:0', '-ac', '1', '-ar', str(SR),
                        '-f', 'f32le', '-'], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.float32).astype(np.float64)


def ou_bip(son):
    g = bip()
    n = 1 << int(np.ceil(np.log2(len(son) + len(g))))
    c = np.fft.irfft(np.fft.rfft(son, n) * np.conj(np.fft.rfft(g, n)), n)[:len(son) - len(g) + 1]
    return int(np.argmax(c)) / SR


def premiere_image(fichier):
    images = ffprobe(fichier, '-show_frames', '-select_streams', 'v:0', '-read_intervals', '%+#5')['frames']
    return min(float(f['best_effort_timestamp_time']) for f in images)


def premiere_replique(fichier, titre='Paroles (espagnol)'):
    """Temps du fichier de la première réplique de la piste de paroles."""
    flux = [s for s in ffprobe(fichier, '-show_streams')['streams'] if s['codec_type'] == 'subtitle']
    k = next(i for i, s in enumerate(flux) if titre in ((s.get('tags') or {}).get('title'),
                                                         (s.get('tags') or {}).get('handler_name')))
    paquets = ffprobe(fichier, '-show_packets', '-select_streams', f's:{k}')['packets']
    return float(next(p for p in paquets if int(p['size']) > 2)['pts_time'])


def temps(motif, texte):
    m = re.search(motif, texte, re.M)
    return [float(x) for x in m.groups()]


def paroles_json(debut=T_BIP, decalage=0.0):
    return {'format': 1, 'langue': 'es', 'source': 'whisper-large-v3', 'decalage_s': decalage,
            'segments': [{'debut': debut, 'fin': debut + 1.0, 'texte': 'el gato come pan', 'langue': 'es',
                          'mots': [{'debut': debut, 'fin': debut + 0.4, 'mot': 'el'}]}],
            'traductions': {'fr': {'source': 'nllb-200-distilled-600M',
                                   'segments': [{'debut': debut, 'fin': debut + 1.0, 'texte': 'le chat mange du pain'}]}}}


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class CalageParoles(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        (self.tmp / 'home').mkdir()
        self.D = charger_doubler(self.tmp / 'home')

    def doubler(self, ext, extrait=None, retard=0.0, duree=6.0, paroles=None):
        """travail/ comme au retour de Kaggle (bip dans la musique à T_BIP du son doublé), puis finir()."""
        source = video(self.tmp / f'Chanson{ext}', duree, retard)
        self.dossier = self.tmp / 'sorties' / 'Chanson'
        t = self.dossier / 'travail'
        self.tv = t / 'jules'
        self.tv.mkdir(parents=True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.src = self.D.preparer_source(source, self.D.sonder(source), *(extrait or (None, None)), t)
        n = self.D.echantillons(t / 'original.wav')[0]
        musique, voix_o = bruit(n, 1, -30), bruit(n, 2, -32)
        g = bip()
        musique[int(T_BIP * SR):int(T_BIP * SR) + len(g)] += g
        ecrire_wav(t / 'original.wav', musique + voix_o, canaux=2)
        ecrire_wav(t / 'voix_source.wav', voix_o, canaux=2)
        ecrire_wav(self.tv / 'voix.wav', bruit(n, 3, -32))
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(self.tv / 'voix.wav'), '-c:a', 'flac', '-sample_fmt',
                        's32', str(self.tv / 'voix_convertie.flac')], check=True)
        (self.tv / 'voix.wav').unlink()
        (self.tv / 'rapport.json').write_text(json.dumps({'mode': 'chant', 'frise': []}))
        (self.tv / 'paroles.json').write_text(json.dumps(paroles or paroles_json()))
        self.a = argparse.Namespace(voix_db=None, mix_v1=False, reverb_db=-13.0, paroles=True, pistes_audio='toutes',
                                    autotune=None, sans_autotune=False, autotune_gamme=None)
        self.a.autotune_reglage = self.D.reglage_autotune(self.a, {})
        self.tr = {'travail': t, 'tv': self.tv, 'dossier': self.dossier, 'base': BASE, 'nom': 'Chanson'}
        return self.finir()

    def finir(self):
        with contextlib.redirect_stdout(io.StringIO()) as texte:
            sortie = self.D.finir(self.tr, self.a, JULES)
            self.plan = self.D.plan_video(self.tr['travail'])
        self.texte = texte.getvalue()
        return sortie

    def verifier(self, sortie, debut_son):
        """Le bip et la ligne de paroles au même instant : dans la vidéo (paroles.json, .srt, piste de la vidéo, à
        3 ms près), dans le mp3 (.lrc, au centième près) ; debut_son : où commence le mix dans la vidéo."""
        image = premiere_image(sortie)
        video = ou_bip(decoder(sortie, temps_du_fichier=True)) - image
        mp3 = ou_bip(decoder(self.dossier / f'{BASE}.mp3'))
        self.assertAlmostEqual(mp3, T_BIP, delta=0.001)
        self.assertAlmostEqual(video, T_BIP + debut_son, delta=0.003)
        d = json.loads((self.tv / 'paroles.json').read_text())
        self.assertAlmostEqual(d['decalage_s'], debut_son, delta=0.002)
        self.assertEqual(json.loads((self.tv / 'rapport.json').read_text())['paroles_sorties']['decalage_s'],
                         d['decalage_s'])
        for nom, t in (('paroles.json', d['segments'][0]['debut']), ('mot', d['segments'][0]['mots'][0]['debut']),
                       ('traduction', d['traductions']['fr']['segments'][0]['debut']),
                       ('.es.srt', temps(r'^(\d+):(\d+):(\d+),(\d+) -->', (self.dossier / f'{BASE}.es.srt').read_text())),
                       ('.fr.srt', temps(r'^(\d+):(\d+):(\d+),(\d+) -->', (self.dossier / f'{BASE}.fr.srt').read_text())),
                       ('piste de la vidéo', premiere_replique(sortie) - image),
                       ('traduction de la vidéo', premiere_replique(sortie, 'Paroles (français, traduction)') - image)):
            if isinstance(t, list):
                t = t[0] * 3600 + t[1] * 60 + t[2] + t[3] / 1000
            self.assertAlmostEqual(t, video, delta=0.003, msg=nom)
        self.assertAlmostEqual(d['segments'][0]['debut'] - d['decalage_s'], mp3, delta=0.003, msg='temps du son')
        mn, s = temps(r'^\[(\d+):(\d+\.\d+)\]el gato', (self.dossier / f'{BASE}.lrc').read_text())
        self.assertAlmostEqual(mn * 60 + s, mp3, delta=0.006, msg='.lrc')
        self.assertEqual(sorted(f.name for f in self.tv.glob('.*')), [])   # pistes au temps de la coupe effacées
        return d

    def test_extrait_mkv_avec_images_b(self):
        sortie = self.doubler('.mkv', extrait=(3.0, 4.0), duree=10.0)
        self.assertAlmostEqual(self.plan['decalage_son'], -0.1314, delta=0.001)   # ffmpeg a reculé de 0,13 s
        self.assertEqual(self.plan['debut_son'], 0.0)
        d = self.verifier(sortie, 0.0)
        self.assertEqual(d['decalage_s'], 0.0)

    def test_extrait_mkv_copie_depuis_une_image_cle_anterieure(self):
        """ffmpeg ne tombe pas sur l'image clé de l'extrait (ici, en le trompant sur son recul) : la copie de l'image
        part de l'image clé d'avant, environ 1 s plus tôt, et le son commence d'autant après l'image."""
        self.D.RECUL_FFMPEG_S = 1.2
        sortie = self.doubler('.mkv', extrait=(3.0, 4.0), duree=10.0)
        self.assertIn('image sans son au début', self.texte)
        self.assertEqual(self.plan['decalage_son'], 0.0)
        self.assertGreater(self.plan['debut_son'], 0.5)
        self.verifier(sortie, self.plan['debut_son'])

    def test_extrait_mp4(self):
        sortie = self.doubler('.mp4', extrait=(3.0, 4.0), duree=10.0)
        self.assertEqual((self.plan['decalage_son'], self.plan['debut_son']), (0.0, 0.0))
        self.assertEqual(self.verifier(sortie, 0.0)['decalage_s'], 0.0)

    @unittest.skipUnless(WEBM, 'libvpx ou libopus absent')
    def test_extrait_webm(self):
        sortie = self.doubler('.webm', extrait=(3.0, 4.0), duree=10.0)
        self.assertEqual(self.verifier(sortie, 0.0)['decalage_s'], 0.0)

    def test_mkv_entier_au_son_d_origine_en_retard(self):
        sortie = self.doubler('.mkv', retard=0.3)
        self.assertGreater(self.src['decalage_son'], 0.2)
        self.assertEqual(self.plan['debut_son'], self.src['decalage_son'])
        self.verifier(sortie, self.src['decalage_son'])

    def test_remix_d_un_ancien_extrait_mkv(self):
        """Avant le 05/11/2026, paroles.json et le .srt d'un extrait mkv étaient calés à -0,13 s (le décalage donné à
        ffmpeg) : --remix (finir() refait depuis travail/) les recale sur la vidéo, .lrc et mp3 inchangés."""
        ancien = paroles_json(T_BIP - 0.131435, -0.131435)
        sortie = self.doubler('.mkv', extrait=(3.0, 4.0), duree=10.0, paroles=ancien)
        self.verifier(sortie, 0.0)
        sortie = self.finir()   # un second --remix ne change rien
        self.assertEqual(self.verifier(sortie, 0.0)['segments'][0]['debut'], T_BIP)


if __name__ == '__main__':
    unittest.main()
