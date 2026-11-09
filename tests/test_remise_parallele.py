"""Remise sur la vidéo en parallèle (06/11/2026) : chaque son est encodé à part, chacun dans son processus ffmpeg, puis
la commande finale multiplexe sans réencoder (-c:a copy). Comparée à la remise d'avant (une seule commande ffmpeg qui
encodait les cinq pistes l'une après l'autre ; doubler.py de develop avant ce changement, lu dans l'historique), sur des
vidéos de synthèse, pour un mp4 entier, un mp4 au son en retard, un extrait mp4, un extrait mkv à images B (décalage
négatif), un extrait mkv avec paroles et sous-titres d'origine, un mkv entier, un extrait webm et un webm entier (Opus) :
- même son décodé au bit près pour chaque piste ;
- mêmes paquets aux mêmes instants, comptés depuis la première image (début et fin de chaque flux : image, cinq sons,
  sous-titres) : le calage ne change pas ;
- mêmes titres, langues, dispositions, codecs, nombres de paquets ; en mp4 et en webm, mêmes débuts de flux. En mkv, le
  muxeur recale le fichier sur le premier paquet qu'il reçoit, désormais l'amorce AAC du son (23 ms) au lieu de l'image :
  toute la vidéo glisse d'un bloc de 23 ms au plus. En mp4 au son en retard, la liste d'édition finit 8 échantillons plus
  tôt (l'amorce de fin de l'AAC, que ffmpeg ne retire d'ailleurs pas au décodage).
Puis l'envoi en FLAC 16 bits (clé envoi_flac : 24 bits comme avant)."""
import contextlib
import importlib.util
import io
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from commun import DEPOT, FFMPEG, SR, Dossier, charger_doubler, ecrire_wav, ffprobe
from test_pistes_audio import WEBM, bruit, travail, video

AVANT = 'be8a198'   # develop avant la remise en parallèle
HISTORIQUE = subprocess.run(['git', '-C', str(DEPOT), 'cat-file', '-e', f'{AVANT}:doubler.py'],
                            capture_output=True).returncode == 0
JULES = {'id': 'jules', 'affichage': 'Jules'}


def charger_ancien(dossier, home):
    """doubler.py d'avant la remise en parallèle, chargé comme module (HOME déplacé, comme charger_doubler)."""
    f = dossier / 'doubler_avant.py'
    f.write_text(subprocess.run(['git', '-C', str(DEPOT), 'show', f'{AVANT}:doubler.py'], capture_output=True, text=True,
                                check=True).stdout, encoding='utf-8')
    with mock.patch.dict(os.environ, {'HOME': str(home)}):
        for v in ('VOIXLA_KAGGLE', 'VOIXLA_DONNEES', 'DOUBLER_VOIX'):
            os.environ.pop(v, None)
        spec = importlib.util.spec_from_file_location('doubler_avant', f)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    return m


def son_brut(fichier, k):
    """Octets du son décodé de la piste audio k, tel quel (fréquence et canaux du flux, flottant 32 bits)."""
    return subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(fichier), '-map', f'0:a:{k}', '-f', 'f32le', '-'],
                          capture_output=True, check=True).stdout


def flux(fichier):
    """Ce qui doit être pareil d'un flux à l'autre : type, codec, nombre de paquets, fréquence, canaux, titre, langue,
    disposition par défaut ; et, à part, le début de chaque flux."""
    info = ffprobe(fichier, '-show_streams', '-count_packets')
    sortie, debuts = [], []
    for s in info['streams']:
        tags = s.get('tags') or {}
        sortie.append({k: s.get(k) for k in ('codec_type', 'codec_name', 'nb_read_packets', 'sample_rate', 'channels')}
                      | {'titre': tags.get('handler_name') or tags.get('title'), 'langue': tags.get('language'),
                         'defaut': (s.get('disposition') or {}).get('default')})
        debuts.append(float(s['start_time']))
    return sortie, debuts


def paquets(fichier):
    """Début et fin (dernier paquet compris) de chaque flux, en s, comptés depuis le premier paquet de l'image."""
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'packet=stream_index,pts_time,duration_time', '-of',
                        'csv=p=0', str(fichier)], capture_output=True, text=True, check=True)
    d = {}
    for ligne in r.stdout.splitlines():
        p = ligne.split(',')
        if len(p) < 3 or p[1] in ('', 'N/A'):
            continue
        i, t = int(p[0]), float(p[1])
        fin = t + (float(p[2]) if p[2] not in ('', 'N/A') else 0.0)
        a, b = d.get(i, (t, fin))
        d[i] = (min(a, t), max(b, fin))
    zero = d[0][0]
    return {i: (round(a - zero, 4), round(b - zero, 4)) for i, (a, b) in sorted(d.items())}


@unittest.skipUnless(FFMPEG and HISTORIQUE, 'ffmpeg ou historique absent')
class RemiseParallele(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._d = Dossier()
        cls.tmp = cls._d.__enter__()
        (cls.tmp / 'home').mkdir()
        cls.D = charger_doubler(cls.tmp / 'home')
        cls.A = charger_ancien(cls.tmp, cls.tmp / 'home')

    @classmethod
    def tearDownClass(cls):
        cls._d.__exit__(None, None, None)

    def comparer(self, nom, ext, extrait=None, retard=0.0, duree=6.0, paroles=False, images_b=False):
        """La même remise (mix et quatre pistes en plus, paroles s'il y en a) par l'ancienne et la nouvelle façon.
        Rend (flux, débuts des flux) de la remise d'avant."""
        (self.tmp / 'videos').mkdir(exist_ok=True)
        source = video(self.tmp / 'videos' / f'{nom}{ext}', duree, retard, sous_titre=paroles, images_b=images_b)
        dossier = self.tmp / 'sorties' / nom
        t, tv, _ = travail(self.D, source, dossier, JULES, extrait)
        n = self.D.echantillons(t / 'original.wav')[0]
        ecrire_wav(tv / 'mix.wav', bruit(n, 7, -18), canaux=2)
        ecrire_wav(t / 'instrumental.wav', bruit(n, 8, -20), canaux=2)
        ecrire_wav(tv / 'seule.wav', bruit(n - 300, 9, -24), canaux=2)   # plus courte : complétée de silence
        pistes = [{'fichier': t / 'original.wav', 'titre': 'Original'},
                  {'fichier': t / 'instrumental.wav', 'titre': 'Beat seul'},
                  {'fichier': tv / 'seule.wav', 'titre': 'Voix convertie seule (Jules)'},
                  {'fichier': t / 'voix_source.wav', 'titre': "Voix d'origine seule"}]
        st = []
        if paroles:
            srt = tv / 'paroles.srt'
            srt.write_text('1\n00:00:01,000 --> 00:00:02,000\nel gato come pan\n\n', encoding='utf-8')
            st = [{'fichier': srt, 'langue': 'spa', 'titre': 'Paroles (espagnol)'}]
        sorties = {}
        for cle, M in (('avant', self.A), ('apres', self.D)):
            d = dossier / cle
            d.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                sorties[cle] = M.remettre_sur_video(t, tv / 'mix.wav', d, f'{nom} - voix Jules', st, M.plan_video(t),
                                                    pistes, 'Doublage (voix Jules)', 'spa')
        a, b = sorties['avant'], sorties['apres']
        (fa, da), (fb, db) = flux(a), flux(b)
        self.assertEqual(fb, fa)
        for k in range(5):
            self.assertEqual(son_brut(b, k), son_brut(a, k), f'piste audio {k} : son décodé différent')
        self.assertEqual(paquets(b), paquets(a), 'paquets aux mêmes instants depuis la première image')
        glissement = {round(y - x, 4) for x, y in zip(da, db)}
        self.assertEqual(len(glissement), 1, f'tous les flux glissent ensemble : {da} → {db}')
        if ext == '.mkv':
            self.assertLessEqual(abs(glissement.pop()), 0.024)
        else:
            self.assertEqual(db, da)
        self.assertEqual(sorted(p.name for p in t.glob('.pistes-*')), [], 'fichiers encodés effacés')
        return fa, da

    def test_mp4_entier(self):
        self.comparer('Clip', '.mp4')

    def test_mp4_son_en_retard(self):
        _, debuts = self.comparer('Retard', '.mp4', retard=0.5)
        self.assertGreater(debuts[1] - debuts[0], 0.4)

    def test_extrait_mp4(self):
        self.comparer('Extrait', '.mp4', extrait=(3.0, 4.0), duree=10.0)

    def test_extrait_mkv_images_b(self):
        self.comparer('Recul', '.mkv', extrait=(3.0, 4.0), duree=10.0, images_b=True)

    def test_extrait_mkv_paroles(self):
        streams, _ = self.comparer('Serie', '.mkv', extrait=(3.0, 4.0), duree=10.0, paroles=True)
        self.assertEqual([s['codec_type'] for s in streams].count('subtitle'), 2)

    def test_mkv_entier(self):
        self.comparer('Entier', '.mkv')

    @unittest.skipUnless(WEBM, 'libvpx ou libopus absent')
    def test_extrait_webm(self):
        self.comparer('Web', '.webm', extrait=(1.0, 4.0))

    @unittest.skipUnless(WEBM, 'libvpx ou libopus absent')
    def test_webm_entier(self):
        self.comparer('WebE', '.webm')

    def test_encodages_repris(self):
        """Dans un même mixage, la version auto-tune reprend les pistes déjà encodées (l'original, le beat, la voix
        d'origine) : seuls les sons nouveaux sont encodés."""
        t = self.tmp / 'repris'
        t.mkdir()
        sons = []
        for k in range(3):
            ecrire_wav(t / f's{k}.wav', bruit(SR, 20 + k, -20), canaux=2)
            sons.append((t / f's{k}.wav', None if k == 0 else f'aresample={SR},apad=whole_len={SR},atrim=end_sample={SR}'))
        audio = self.D.CONTENEURS['.mp4'][0]
        appels = []
        vrai_ff = self.D.ff
        with self.D.PistesEncodees(t) as e, \
                mock.patch.object(self.D, 'ff', lambda *a, **k: (appels.append(a), vrai_ff(*a, **k))):
            f1 = e.encoder(sons, audio, '.mp4')
            f2 = e.encoder([sons[1], sons[2], sons[0]], audio, '.mp4')
            self.assertEqual(f2, [f1[1], f1[2], f1[0]])
            self.assertEqual(len(appels), 3)
            self.assertTrue(all(f.suffix == '.m4a' and f.exists() for f in f1))
            dossier = e.dossier
        self.assertFalse(dossier.exists())


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class EnvoiFlac(unittest.TestCase):
    """Le son part à Kaggle en FLAC 16 bits (crête ramenée à -1 dBFS par un gain connu) ; clé envoi_flac 24 : comme
    avant. Le noyau le lit avec soundfile en flottant (lire_entree), comme le FLAC 24 bits : rien n'y change ; ici, ffmpeg
    le décode de la même façon (entier / 2^15)."""

    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        (self.tmp / 'home').mkdir()
        self.D = charger_doubler(self.tmp / 'home')

    def lire(self, f):
        r = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(f), '-f', 'f32le', '-'], capture_output=True, check=True)
        return np.frombuffer(r.stdout, np.float32).astype(np.float64).reshape(-1, 2)

    def test_seize_et_vingt_quatre_bits(self):
        n = 3 * SR
        x = np.stack([bruit(n, 1, -12), bruit(n, 2, -12)], 1)
        x[SR] = [1.12, -1.05]   # au-dessus de 0 dBFS, comme souvent dans les sources
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(SR), '-ac', '2', '-i', '-', '-c:a',
                        'pcm_f32le', str(self.tmp / 'o.wav')], input=x.astype(np.float32).tobytes(), check=True)
        tailles = {}
        for bits, fmt, rapport_min in ((16, 's16', 80.0), (24, 's32', 120.0)):
            f = self.tmp / f'e{bits}.flac'
            gain = self.D.encoder_flac(self.tmp / 'o.wav', f, bits)
            s = ffprobe(f, '-show_streams')['streams'][0]
            self.assertEqual((s['codec_name'], s['sample_fmt']), ('flac', fmt))
            self.assertEqual(int(s.get('bits_per_raw_sample') or 0), bits)
            self.assertAlmostEqual(20 * np.log10(gain), -1 - 20 * np.log10(np.abs(x).max()), delta=0.01)
            y = self.lire(f) / gain
            erreur = 10 * np.log10(np.sum(x ** 2) / np.sum((y - x) ** 2))
            self.assertGreater(erreur, rapport_min, f'{bits} bits : {erreur:.1f} dB')
            tailles[bits] = f.stat().st_size
        self.assertLess(tailles[16], 0.8 * tailles[24])

    def test_cle_envoi_flac(self):
        self.assertEqual(self.D.envoi_flac_config({}), 16)
        self.assertEqual(self.D.envoi_flac_config({'envoi_flac': 24}), 24)
        self.assertEqual(self.D.envoi_flac_config({'envoi_flac': '24 bits'}), 24)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.D.envoi_flac_config({'envoi_flac': 'beaucoup'}), 16)   # illisible : défaut
            self.D.ecrire_config('envoi_flac', '24')
            self.assertEqual(self.D.envoi_flac_config(self.D.lire_config()), 24)
            with self.assertRaises(SystemExit):
                self.D.ecrire_config('envoi_flac', '32')

    def test_envoi_du_jeu(self):
        """envoyer_jeu encode avec les bits demandés et note dans le travail ce qui est parti."""
        recus = []
        tr = {'travail': self.tmp, 'entree': 'entree-x.flac'}
        ecrire_wav(self.tmp / 'original.wav', bruit(SR, 3, -20), canaux=2)
        self.D.JEU = 'compte/jeu'
        with mock.patch.object(self.D, 'kaggle', lambda *a: (0, 'entree-x.flac ready')), \
                mock.patch.object(self.D, 'encoder_flac', lambda w, f, bits=16: (recus.append(bits),
                                                                                 Path(f).write_bytes(b'f'), 1.0)[2]), \
                mock.patch.object(self.D.time, 'sleep', lambda s: None), contextlib.redirect_stdout(io.StringIO()) as dit:
            self.D.envoyer_jeu([tr], {'id': 'jules', 'reference': self.tmp / 'original.wav'}, self.tmp / 'k', 24)
        self.assertEqual(recus, [24])
        self.assertEqual(tr['bits_envoi'], 24)
        self.assertIn('FLAC 24 bits', dit.getvalue())


if __name__ == '__main__':
    unittest.main()
