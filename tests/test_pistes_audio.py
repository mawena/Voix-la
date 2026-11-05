"""Pistes audio de la vidéo doublée : le doublage (première piste, seule par défaut), puis l'original, le beat seul, la
voix convertie seule et la voix d'origine seule. Pour chaque cas (mp4 entier, extrait mp4, extrait mkv avec paroles,
son d'origine en retard, webm, parole, auto-tune, voix par chanteur) : ordre, disposition, titres, langue, codec,
durées, alternate_group en mp4, et calage de chaque piste sur le doublage à la milliseconde près (corrélation du son
décodé avec le fichier de travail de chaque piste) ; voix convertie seule + beat ≈ doublage. Puis le retrait
(pistes_audio doublage), la configuration et --remix en ligne de commande. Vidéos et sons de synthèse (bruits à bande
limitée, un par piste), quelques secondes chacun."""
import argparse
import contextlib
import io
import json
import os
import struct
import subprocess
import unittest

import numpy as np

from commun import DOUBLER, FFMPEG, SR, Dossier, charger_doubler, ecrire_wav, ffprobe, python

JULES = {'id': 'jules', 'affichage': 'Jules'}
DUO = {'id': 'jules+marie', 'affichage': 'Jules et Marie', 'membres': [{'id': 'jules', 'affichage': 'Jules'},
                                                                         {'id': 'marie', 'affichage': 'Marie'}]}
UN_MS = SR // 1000   # 44 échantillons


def encodeur(nom):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True)
    return f' {nom} ' in r.stdout


WEBM = FFMPEG and encodeur('libvpx') and encodeur('libopus')
with Dossier() as _h:
    FAT1 = FFMPEG and charger_doubler(_h).trouver_fat1() is not None and charger_doubler(_h).ffmpeg_lv2()


def bruit(n, graine, niveau_db, bande=(150.0, 5000.0)):
    """Bruit à bande limitée, RMS niveau_db dBFS : corrélation nette (pic de 0,2 ms), bien gardé par l'AAC et l'Opus."""
    x = np.random.default_rng(graine).standard_normal(n)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    X[(f < bande[0]) | (f > bande[1])] = 0
    y = np.fft.irfft(X, n)
    return y * 10 ** (niveau_db / 20) / np.sqrt(np.mean(y ** 2))


def video(chemin, duree, retard=0.0, sous_titre=False):
    """Vidéo de synthèse (image clé chaque seconde), son en espagnol (spa), en retard de retard s sur l'image."""
    webm = chemin.suffix == '.webm'
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size=128x96:rate=25:duration={duree}',
           *(['-itsoffset', str(retard)] if retard else []),
           '-f', 'lavfi', '-i', f'sine=frequency=440:duration={duree}:sample_rate={SR}']
    cartes = ['-map', '0:v', '-map', '1:a']
    if sous_titre:
        srt = chemin.with_suffix('.orig.srt')
        srt.write_text('1\n00:00:00,500 --> 00:00:01,500\nsous-titre d\'origine\n\n', encoding='utf-8')
        cmd += ['-i', str(srt)]
        cartes += ['-map', '2', '-c:s', 'mov_text' if chemin.suffix == '.mp4' else 'srt']
    cmd += [*cartes, *(['-c:v', 'libvpx', '-b:v', '300k'] if webm else ['-c:v', 'mpeg4']), '-g', '25',
            '-c:a', 'libopus' if webm else 'aac', '-metadata:s:a:0', 'language=spa', str(chemin)]
    subprocess.run(cmd, check=True)
    return chemin


def decoder(fichier, carte='0:a:0'):
    """Son mono 44,1 kHz d'une piste (float32)."""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(fichier), '-map', carte, '-ac', '1', '-ar', str(SR),
                        '-f', 'f32le', '-'], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.float32).astype(np.float64)


def position(son, ref):
    """(où commence ref dans son, en échantillons ; pic de corrélation normalisé) : une seconde du milieu de ref
    cherchée dans son par corrélation."""
    p0 = len(ref) // 2 - SR // 2
    morceau = ref[p0:p0 + SR]
    n = 1 << int(np.ceil(np.log2(len(son) + len(morceau))))
    c = np.fft.irfft(np.fft.rfft(son, n) * np.conj(np.fft.rfft(morceau, n)), n)[:len(son) - len(morceau) + 1]
    k = int(np.argmax(c))
    pic = c[k] / (np.linalg.norm(morceau) * np.linalg.norm(son[k:k + len(morceau)]) + 1e-12)
    return k - p0, float(pic)


def boites(b, debut, fin):
    i = debut
    while i + 8 <= fin:
        taille, genre = struct.unpack('>I4s', b[i:i + 8])
        entete = 8
        if taille == 1:
            taille, entete = struct.unpack('>Q', b[i + 8:i + 16])[0], 16
        elif taille == 0:
            taille = fin - i
        yield genre, i + entete, i + taille
        i += taille


def pistes_mp4(chemin):
    """[(type de piste, drapeaux du tkhd, alternate_group)] d'un mp4, dans l'ordre (lu dans moov/trak/tkhd et hdlr)."""
    b = chemin.read_bytes()
    sortie = []
    for g, d, f in boites(b, 0, len(b)):
        for g2, d2, f2 in (boites(b, d, f) if g == b'moov' else ()):
            if g2 != b'trak':
                continue
            piste = {}
            for g3, d3, f3 in boites(b, d2, f2):
                if g3 == b'tkhd':
                    v1 = b[d3] == 1
                    p = d3 + 4 + (32 if v1 else 20) + 8   # dates, track_ID, réservé, durée, puis 8 octets réservés
                    piste['drapeaux'] = int.from_bytes(b[d3 + 1:d3 + 4], 'big')
                    piste['groupe'] = struct.unpack('>h', b[p + 2:p + 4])[0]
                if g3 == b'mdia':
                    for g4, d4, _ in boites(b, d3, f3):
                        if g4 == b'hdlr':
                            piste['type'] = b[d4 + 8:d4 + 12].decode('latin-1')
            sortie.append((piste.get('type'), piste.get('drapeaux'), piste.get('groupe')))
    return sortie


PAROLES = {'format': 1, 'langue': 'es', 'source': 'whisper-large-v3', 'decalage_s': 0.0,
           'segments': [{'debut': 1.0, 'fin': 2.0, 'texte': 'el gato come pan', 'langue': 'es',
                         'mots': [{'debut': 1.0, 'fin': 1.4, 'mot': 'el'}]}],
           'traductions': {'fr': {'source': 'nllb-200-distilled-600M',
                                  'segments': [{'debut': 1.0, 'fin': 2.0, 'texte': 'le chat mange du pain'}]}}}


def travail(D, source, dossier, voix, extrait=None, mode='chant', ecart_voix=0, paroles=False):
    """travail/ comme au retour de Kaggle : source.json et original.wav extraits de la vidéo (extrait compris), puis
    original.wav refait à la même longueur avec des sons de synthèse : musique (bruit 1) + voix d'origine (bruit 2) ;
    voix_source.wav = la voix d'origine (ecart_voix échantillons de moins : complétée ou coupée à la remise sur la
    vidéo) ; voix convertie : bruit 3. Rend (t, tv, source.json)."""
    t = dossier / 'travail'
    tv = t / voix['id']
    tv.mkdir(parents=True)
    with contextlib.redirect_stdout(io.StringIO()):
        src = D.preparer_source(source, D.sonder(source), *(extrait or (None, None)), t)
    n = D.echantillons(t / 'original.wav')[0]
    musique, voix_o = bruit(n, 1, -20), bruit(n, 2, -22)
    ecrire_wav(t / 'original.wav', musique + voix_o, canaux=2)
    ecrire_wav(t / 'voix_source.wav', voix_o[:n - ecart_voix], canaux=2)
    ecrire_wav(tv / 'voix.wav', bruit(n, 3, -22))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(tv / 'voix.wav'), '-c:a', 'flac', '-sample_fmt', 's32',
                    str(tv / 'voix_convertie.flac')], check=True)
    (tv / 'voix.wav').unlink()
    (tv / 'rapport.json').write_text(json.dumps({'mode': mode, 'frise': []}))
    if paroles:
        (tv / 'paroles.json').write_text(json.dumps(PAROLES))
    return t, tv, src


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class PistesAudio(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        (self.tmp / 'home').mkdir()
        self.D = charger_doubler(self.tmp / 'home')

    def doubler(self, nom, ext, voix=JULES, extrait=None, retard=0.0, duree=6.0, mode='chant', pistes='toutes',
                ecart_voix=0, paroles=False, autotune=None):
        (self.tmp / 'videos').mkdir(exist_ok=True)
        source = video(self.tmp / 'videos' / f'{nom}{ext}', duree, retard, sous_titre=paroles)
        dossier = self.tmp / 'sorties' / nom
        t, tv, src = travail(self.D, source, dossier, voix, extrait, mode, ecart_voix, paroles)
        a = argparse.Namespace(voix_db=None, mix_v1=False, reverb_db=-13.0, paroles=paroles, pistes_audio=pistes,
                               autotune=autotune, sans_autotune=False, autotune_gamme=None)
        a.autotune_reglage = self.D.reglage_autotune(a, {})
        tr = {'travail': t, 'tv': tv, 'dossier': dossier, 'base': f"{nom} - voix {voix['affichage']}", 'nom': nom}
        with contextlib.redirect_stdout(io.StringIO()) as texte:
            sortie = self.D.finir(tr, a, voix)
        self.texte = texte.getvalue()
        return sortie, t, tv, src

    def voix_seule(self, t, tv, autotune=False):
        """La voix convertie seule attendue, refaite d'après les fichiers et les gains du mix (rapport.json)."""
        gains = json.loads((tv / 'rapport.json').read_text())['mix']['gains_db']
        nom = 'autotune' if autotune else 'convertie'
        ref = self.tmp / f'voix_seule_{nom}.wav'
        self.D.assembler(t, tv / f'seche_voix_{nom}.wav', tv / f'reverb_voix_{nom}.wav' if 'reverb' in gains else None,
                         gains, ref, musique=False)
        return ref

    def verifier(self, sortie, t, tv, titres, autotune=False, langue='spa'):
        """Les cinq pistes de sortie : ordre, disposition, titres, langue, codec, durées, groupe mp4, calage, contenu."""
        info = ffprobe(sortie, '-show_streams', '-show_format')
        genres = [s['codec_type'] for s in info['streams']]
        self.assertEqual(genres[:6], ['video'] + ['audio'] * 5, genres)
        self.assertNotIn('audio', genres[6:])
        sons = [s for s in info['streams'] if s['codec_type'] == 'audio']
        mp4, webm = sortie.suffix == '.mp4', sortie.suffix == '.webm'
        self.assertEqual([s['disposition']['default'] for s in sons], [1, 0, 0, 0, 0])
        self.assertEqual([s.get('tags', {}).get('handler_name' if mp4 else 'title') for s in sons], titres)
        self.assertEqual([s.get('tags', {}).get('language') for s in sons], [langue] * 5)
        self.assertEqual({s['codec_name'] for s in sons}, {'opus' if webm else 'aac'})
        self.assertEqual(len({s['start_time'] for s in sons}), 1, [s['start_time'] for s in sons])
        if mp4:
            self.assertEqual(len({s['duration'] for s in sons}), 1, [s['duration'] for s in sons])
            audio = [(drapeaux, groupe) for genre, drapeaux, groupe in pistes_mp4(sortie) if genre == 'soun']
            self.assertEqual(len(audio), 5)
            self.assertEqual(len({g for _, g in audio}), 1, audio)          # un seul alternate_group…
            self.assertNotEqual(audio[0][1], 0)                              # …non nul : une piste jouée à la fois
            self.assertEqual([d & 1 for d, _ in audio], [1, 0, 0, 0, 0])     # seul le doublage est activé
        else:
            self.assertEqual(len({s['tags']['DURATION'] for s in sons}), 1, [s['tags']['DURATION'] for s in sons])
        # contenu et calage : chaque piste décodée retrouve son fichier de travail, au même endroit que le doublage
        decodes = [decoder(sortie, f'0:a:{k}') for k in range(5)]
        self.assertEqual(len({len(y) for y in decodes}), 1, [len(y) for y in decodes])
        mix = tv / ('mix_autotune.wav' if autotune else 'mix.wav')
        refs = [decoder(f) for f in (mix, t / 'original.wav', t / 'instrumental.wav', self.voix_seule(t, tv, autotune),
                                     t / 'voix_source.wav')]
        mesures = [[position(y, r) for r in refs] for y in decodes]
        debuts = [mesures[k][k][0] for k in range(5)]
        for k in range(5):
            pics = [p for _, p in mesures[k]]
            self.assertEqual(int(np.argmax(pics)), k, f'piste {k} : {pics}')   # chaque piste à sa place
            self.assertGreater(pics[k], 0.9, f'piste {k} : {pics}')
            self.assertLessEqual(abs(debuts[k] - debuts[0]), UN_MS, f'piste {k} décalée : {debuts}')
        # voix convertie seule + beat ≈ doublage (limiteur peu sollicité) : écart de l'encodage, -38 dB en AAC 256k,
        # -23 dB en Opus 192k (bruit large bande, deux encodages contre un)
        n = min(len(y) for y in decodes)
        reste = decodes[2][:n] + decodes[3][:n] - decodes[0][:n]
        ecart_db = 20 * np.log10(np.sqrt(np.mean(reste ** 2)) / np.sqrt(np.mean(decodes[0][:n] ** 2)))
        self.assertLess(ecart_db, -20 if webm else -30)
        return info, debuts

    def titres(self, voix=JULES, autotune=False):
        nom, at = voix['affichage'], ' auto-tune' if autotune else ''
        seule = 'Voix converties seules' if voix.get('membres') else 'Voix convertie seule'
        return [f'Doublage{at} (voix {nom})', 'Original', 'Beat seul', f'{seule}{at} ({nom})', "Voix d'origine seule"]

    def test_mp4_entier(self):
        sortie, t, tv, _ = self.doubler('Clip', '.mp4')
        self.assertEqual(sortie.name, 'Clip - voix Jules.mp4')
        _, debuts = self.verifier(sortie, t, tv, self.titres())
        self.assertLessEqual(abs(debuts[0]), UN_MS)   # vidéo entière, son à l'heure : le doublage part avec l'image
        rapport = json.loads((tv / 'rapport.json').read_text())
        self.assertEqual(rapport['pistes_audio'], {'reglage': 'toutes', 'video': self.titres()})
        self.assertEqual(sorted(f.name for f in tv.glob('.*')), [])   # voix convertie seule : fichier effacé
        # mix.wav = beat + voix convertie seule, au limiteur près (presque pas sollicité ici) ; les 10 premières et
        # dernières ms exceptées : la musique y passe par le rééchantillonnage du limiteur, qui commence et finit en
        # fondu (écart de 2e-3 aux bords, 8e-6 ailleurs)
        mix, beat, seule = (decoder(f) for f in (tv / 'mix.wav', t / 'instrumental.wav', self.voix_seule(t, tv)))
        self.assertEqual(len(mix), len(seule))
        self.assertLess(np.max(np.abs(mix - beat[:len(mix)] - seule)[UN_MS * 10:-UN_MS * 10]), 1e-4)

    def test_extrait_mp4_et_voix_d_origine_plus_courte(self):
        sortie, t, tv, src = self.doubler('Extrait', '.mp4', extrait=(3.0, 4.0), duree=10.0, ecart_voix=1000)
        self.assertIn('debut', src)
        self.verifier(sortie, t, tv, self.titres())

    def test_extrait_mkv_avec_paroles_et_sous_titres_d_origine(self):
        sortie, t, tv, _ = self.doubler('Serie', '.mkv', extrait=(3.0, 4.0), duree=10.0, paroles=True)
        info, _ = self.verifier(sortie, t, tv, self.titres())
        st = [s for s in info['streams'] if s['codec_type'] == 'subtitle']
        self.assertEqual(len(st), 3)   # d'origine, espagnol, français : après les pistes audio
        self.assertEqual([s['index'] for s in st], [6, 7, 8])
        self.assertEqual([s['disposition']['default'] for s in st], [0, 0, 0])

    def test_son_d_origine_en_retard(self):
        sortie, t, tv, src = self.doubler('Retard', '.mp4', retard=0.5)
        self.assertAlmostEqual(src['decalage_son'], 0.5, delta=0.03)
        info, _ = self.verifier(sortie, t, tv, self.titres())
        sons = [s for s in info['streams'] if s['codec_type'] == 'audio']
        image = next(s for s in info['streams'] if s['codec_type'] == 'video')
        for s in sons:   # toutes les pistes partent avec le doublage, 0,5 s après l'image
            self.assertAlmostEqual(float(s['start_time']) - float(image['start_time']), src['decalage_son'], delta=0.03)

    @unittest.skipUnless(WEBM, 'libvpx ou libopus absent')
    def test_webm(self):
        sortie, t, tv, _ = self.doubler('Web', '.webm', extrait=(1.0, 4.0))
        self.assertEqual(sortie.suffix, '.webm')
        self.verifier(sortie, t, tv, self.titres())

    def test_parole_et_voix_par_chanteur(self):
        sortie, t, tv, _ = self.doubler('Duo', '.mkv', voix=DUO, mode='parole')
        self.assertFalse((tv / 'reverb_voix_convertie.wav').exists())   # parole : pas de reverb
        self.assertEqual(self.titres(DUO)[3], 'Voix converties seules (Jules et Marie)')
        self.verifier(sortie, t, tv, self.titres(DUO))

    @unittest.skipUnless(FAT1, 'x42-autotune (fat1) absent')
    def test_auto_tune(self):
        sortie, t, tv, _ = self.doubler('Juste', '.mp4', autotune=60)
        at = self.D.base_autotune('Juste - voix Jules')
        self.assertTrue(json.loads((tv / 'rapport.json').read_text())['autotune']['applique'], self.texte)
        self.verifier(sortie, t, tv, self.titres())
        self.verifier(sortie.with_name(f'{at}.mp4'), t, tv, self.titres(autotune=True), autotune=True)
        rapport = json.loads((tv / 'rapport.json').read_text())
        self.assertEqual(rapport['pistes_audio']['video_autotune'], self.titres(autotune=True))
        self.assertEqual(sorted(f.name for f in tv.glob('.*')), [])

    def test_retrait_le_doublage_seul(self):
        sortie, t, tv, _ = self.doubler('Seul', '.mp4', pistes='doublage')
        sons = [s for s in ffprobe(sortie, '-show_streams')['streams'] if s['codec_type'] == 'audio']
        self.assertEqual(len(sons), 1)
        self.assertEqual((sons[0]['disposition']['default'], sons[0]['tags']['handler_name'], sons[0]['tags']['language']),
                         (1, 'Doublage (voix Jules)', 'spa'))
        self.assertEqual(json.loads((tv / 'rapport.json').read_text())['pistes_audio'],
                         {'reglage': 'doublage', 'video': ['Doublage (voix Jules)']})
        # même mix, même mp3 qu'avec les cinq pistes
        mix, mp3 = (tv / 'mix.wav').read_bytes(), (tv.parents[1] / 'Seul - voix Jules.mp3').read_bytes()
        _, _, tv5, _ = self.doubler('Toutes', '.mp4')
        self.assertEqual((tv5 / 'mix.wav').read_bytes(), mix)
        self.assertEqual((tv5.parents[1] / 'Toutes - voix Jules.mp3').read_bytes(), mp3)

    def test_voix_seule_egale_au_mix_sur_musique_muette(self):
        n = 3 * SR
        t = self.tmp / 'muet'
        (t / 'tv').mkdir(parents=True)
        ecrire_wav(t / 'instrumental.wav', bruit(n, 1, -20), canaux=2)
        ecrire_wav(t / 'tv' / 'seche.wav', bruit(n, 3, -22), canaux=2)
        ecrire_wav(t / 'tv' / 'reverb.wav', bruit(n, 4, -35), canaux=2)
        g = {'seche': 3.21, 'reverb': -7.5}
        self.D.assembler(t, t / 'tv' / 'seche.wav', t / 'tv' / 'reverb.wav', g, t / 'seule.wav', musique=False)
        ecrire_wav(t / 'instrumental.wav', np.zeros(n), canaux=2)
        self.D.assembler(t, t / 'tv' / 'seche.wav', t / 'tv' / 'reverb.wav', g, t / 'muet.wav')
        a, b = decoder(t / 'seule.wav'), decoder(t / 'muet.wav')
        self.assertEqual(len(a), len(b))
        self.assertLess(np.max(np.abs(a - b)), 2e-4)
        self.assertGreater(np.sqrt(np.mean(a ** 2)), 0.01)


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Configuration(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.D = charger_doubler(self.home)

    def test_cle_pistes_audio(self):
        self.assertEqual(self.D.pistes_audio_config({}), 'toutes')
        with contextlib.redirect_stdout(io.StringIO()):
            self.D.ecrire_config('pistes_audio', ' Doublage ')
            self.assertEqual(self.D.pistes_audio_config(self.D.lire_config()), 'doublage')
            with self.assertRaises(SystemExit):
                self.D.ecrire_config('pistes_audio', 'deux')
            self.assertEqual(self.D.pistes_audio_config({'pistes_audio': 'bof'}), 'toutes')   # illisible : défaut

    def test_remix_en_ligne_de_commande(self):
        """doubler --remix sur un doublage déjà fait (sans Kaggle) : cinq pistes ; clé pistes_audio doublage : une
        seule ; --pistes-audio toutes l'emporte sur la clé."""
        env = {**os.environ, 'HOME': str(self.home)}
        for v in ('VOIXLA_KAGGLE', 'VOIXLA_DONNEES', 'DOUBLER_VOIX'):
            env.pop(v, None)
        donnees = self.tmp / 'donnees'
        (donnees / 'voix' / 'jules').mkdir(parents=True)
        ecrire_wav(donnees / 'voix' / 'jules' / 'reference.wav', bruit(SR, 5, -20))
        (donnees / 'voix' / 'jules' / 'voix.json').write_text(json.dumps(
            {'nom': 'Jules', 'reference': 'reference.wav', 'hauteur_parole_mediane_hz': 120.0}))
        (self.tmp / 'videos').mkdir()
        source = video(self.tmp / 'videos' / 'Clip.mp4', 4.0)
        travail(self.D, source, donnees / 'sorties' / 'Clip', JULES)
        sortie = donnees / 'sorties' / 'Clip' / 'Clip - voix Jules.mp4'

        def doubler(*options):
            r = subprocess.run([python(), str(DOUBLER), '--remix', '--voix', 'jules', '--donnees', str(donnees),
                                '--sans-paroles', *options, str(source)], capture_output=True, text=True, env=env,
                               timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            sons = [s for s in ffprobe(sortie, '-show_streams')['streams'] if s['codec_type'] == 'audio']
            return [(s['tags']['handler_name'], s['disposition']['default']) for s in sons]

        r = subprocess.run([python(), str(DOUBLER), '--help'], capture_output=True, text=True, env=env, timeout=60)
        self.assertIn('--pistes-audio', r.stdout)
        self.assertEqual(doubler(), [('Doublage (voix Jules)', 1), ('Original', 0), ('Beat seul', 0),
                                     ('Voix convertie seule (Jules)', 0), ("Voix d'origine seule", 0)])
        r = subprocess.run([python(), str(DOUBLER), '--config', 'pistes_audio', 'doublage'], capture_output=True,
                           text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([python(), str(DOUBLER), '--config', '--json'], capture_output=True, text=True, env=env,
                           timeout=60)
        self.assertEqual(json.loads(r.stdout)['pistes_audio'], 'doublage')
        self.assertEqual(doubler(), [('Doublage (voix Jules)', 1)])
        self.assertEqual(len(doubler('--pistes-audio', 'toutes')), 5)
        r = subprocess.run([python(), str(DOUBLER), '--config'], capture_output=True, text=True, env=env, timeout=60)
        self.assertIn('pistes audio   doublage', r.stdout)


if __name__ == '__main__':
    unittest.main()
