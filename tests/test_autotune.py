"""Auto-tune (x42-autotune, fat1, par le filtre lv2 de ffmpeg) : correspondance intensité -> ports, syntaxe de la gamme,
détection de la tonalité sur des accords de synthèse, repli sans le greffon, puis finir() de bout en bout sur une voix
de synthèse un peu fausse (noms des fichiers, rapport, événement « sortie », courbes.json, calage), versions normales
identiques au bit près, --remix (retrait, version gardée à jour) et parole.

Les essais qui font tourner fat1 ont besoin du greffon : paquet x42-plugins, ou LV2_PATH vers un dossier lv2 qui
contient fat1.lv2 (par exemple le paquet extrait sans l'installer : apt download x42-plugins, dpkg-deb -x) ; sinon,
ils sont sautés."""
import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from commun import DOUBLER, FFMPEG, SR, Dossier, charger_doubler, ecrire_wav, ffprobe, python

with Dossier() as _h:
    D0 = charger_doubler(_h)   # fonctions pures (ni fichier ni réseau)
FAT1 = FFMPEG and D0.trouver_fat1() is not None and D0.ffmpeg_lv2()
NOTES_MIDI = {'C': 0, 'C#': 1, 'D': 2, 'Eb': 3, 'E': 4, 'F': 5, 'F#': 6, 'G': 7, 'Ab': 8, 'A': 9, 'Bb': 10, 'B': 11}


def accords(noms, duree=1.0, accord_hz=440.0, sr=SR, graine=3):
    """Enchaînement d'accords de synthèse (« Am », « E7 »…) : notes à harmoniques, basse deux octaves plus bas."""
    rng = np.random.default_rng(graine)
    y = []
    for nom in noms:
        racine = nom[:2] if len(nom) > 1 and nom[1] in '#b' else nom[:1]
        r, reste = NOTES_MIDI[racine], nom[len(racine):]
        notes = [r, (r + (3 if reste.startswith('m') else 4)) % 12, (r + 7) % 12] + ([(r + 10) % 12] if '7' in reste else [])
        t = np.arange(int(duree * sr)) / sr
        x = np.zeros_like(t)
        env = np.minimum(1, t / 0.01) * np.exp(-1.5 * t)
        for k, pc in enumerate(notes):
            for octave in ((3, 4) if k else (2, 3)):
                f = accord_hz * 2 ** ((12 * (octave + 1) + pc - 69) / 12)
                for h in range(1, 7):
                    x += np.sin(2 * np.pi * f * h * t + rng.uniform(0, 6)) / h ** 1.3 * env
        y.append(x)
    y = np.concatenate(y)
    return 0.1 * y / np.abs(y).max()


def chant(midi_trames, sr=SR, niveau_db=-22.0):
    """Voix chantée de synthèse : hauteur MIDI toutes les 10 ms (nan : silence), harmoniques et deux formants."""
    m = np.asarray(midi_trames, float)
    n = len(m) * sr // 100
    t_tr = (np.arange(len(m)) + 0.5) / 100
    t = np.arange(n) / sr
    hauteur = np.interp(t, t_tr, np.nan_to_num(m, nan=60))
    voise = np.convolve(np.interp(t, t_tr, (~np.isnan(m)).astype(float)), np.ones(882) / 882, 'same')
    f = 440 * 2 ** ((hauteur - 69) / 12)
    phase = 2 * np.pi * np.cumsum(f) / sr
    y = np.zeros(n)
    for h in range(1, 13):
        fh = f * h
        y += (1 / h) * (1 + 2 * np.exp(-((fh - 700) / 300) ** 2) + np.exp(-((fh - 1200) / 400) ** 2)) * np.sin(h * phase) \
            * (fh < sr / 2 - 1000)
    y *= voise
    return y * 10 ** (niveau_db / 20) / (np.sqrt(np.mean(y[voise > 0.5] ** 2)) + 1e-12)


def melodie_fausse(duree_s, decalages=(+35, -30, +25, -40, +30, -25), notes=(57, 60, 64, 62, 59, 57), debut_s=0.5):
    """La mineur, chaque note fausse de quelques dizaines de cents, petit vibrato ; silence au début et à la fin."""
    m = np.full(int(duree_s * 100), np.nan)
    d = int((duree_s - debut_s - 0.3) * 100 / len(notes))
    for k, (n, c) in enumerate(zip(notes, decalages)):
        a = int(debut_s * 100) + k * d
        tt = np.arange(d - 4) / 100
        m[a:a + d - 4] = n + c / 100 + 0.12 * np.sin(2 * np.pi * 5.5 * tt) * np.clip((tt - 0.2) / 0.2, 0, 1)
    return m


def ecarts_cents(hz, masque, accord=440.0):
    hz = np.asarray([h for h in hz if h > 0], float)
    return D0.ecarts_gamme(hz, masque, accord)


def sha(chemin):
    return hashlib.sha256(Path(chemin).read_bytes()).hexdigest()


def sous_titres(chemin):
    return [s for s in ffprobe(chemin, '-show_streams')['streams'] if s['codec_type'] == 'subtitle']


# --- Sans ffmpeg ------------------------------------------------------------------------------------------------------

class Correspondance(unittest.TestCase):
    def test_bornes_monotonie_et_passes(self):
        D = D0
        self.assertEqual(D.ports_autotune(0), [])
        precedent = None
        for n in range(1, 101):
            passes = D.ports_autotune(n)
            self.assertEqual(len(passes), 1 if n <= 80 else 2, n)
            for p in passes:
                for k, (lo, hi) in D.FAT1_BORNES.items():
                    self.assertTrue(lo <= p[k] <= hi, (n, k, p[k]))
                self.assertEqual((p['mode'], p['fastmode'], p['offset']), (0, 0, 0.0))
            p = passes[0]
            if precedent:
                self.assertGreaterEqual(p['corr'], precedent['corr'])
                self.assertLessEqual(p['filter'], precedent['filter'])
                self.assertLessEqual(p['bias'], precedent['bias'])
            precedent = p

    def test_valeurs_reperes(self):
        D = D0
        bas = D.ports_autotune(30)
        self.assertEqual(len(bas), 1)
        self.assertEqual((bas[0]['corr'], bas[0]['bias']), (0.5, 0.7))
        self.assertAlmostEqual(bas[0]['filter'], 0.19, delta=0.005)    # lent : garde le vibrato
        haut = D.ports_autotune(100)
        self.assertEqual([(p['corr'], p['filter'], p['bias']) for p in haut], [(1.0, 0.02, 0.0), (1.0, 0.02, 0.0)])
        self.assertEqual(D.ports_autotune(81)[1]['corr'], 0.05)
        self.assertEqual(D.ports_autotune(250), D.ports_autotune(100))
        self.assertEqual(D.borner_ports({'filter': 0.001, 'corr': 3, 'autre': 7}), {'filter': 0.02, 'corr': 1.0, 'autre': 7})

    def test_intensite(self):
        D = D0
        for v, attendu in (('70', 70), ('0', 0), ('1', 1), ('100', 100), ('oui', 70), ('non', 0), ('off', 0), (True, 70),
                           (False, 0), (55, 55), ('42.0', 42)):
            self.assertEqual(D.intensite_autotune(v), attendu, v)
        for v in ('101', '-1', '7.5', 'beaucoup', ''):
            with self.assertRaises(ValueError, msg=v):
                D.intensite_autotune(v)

    def test_video_apres_autotune_sans_valeur(self):
        D = D0
        self.assertEqual(D.arguments_autotune(['--autotune', 'clip.mp4']), ['--autotune=70', 'clip.mp4'])
        self.assertEqual(D.arguments_autotune(['--autotune', '30', 'clip.mp4']), ['--autotune', '30', 'clip.mp4'])
        self.assertEqual(D.arguments_autotune(['clip.mp4', '--autotune']), ['clip.mp4', '--autotune'])
        self.assertEqual(D.arguments_autotune(['--autotune', '--remix']), ['--autotune=70', '--remix'])

    def test_priorites(self):
        D = D0
        ns = lambda **k: argparse.Namespace(**{'autotune': None, 'sans_autotune': False, 'autotune_gamme': None, **k})
        self.assertEqual(D.reglage_autotune(ns(), {})['intensite'], 0)
        self.assertEqual(D.reglage_autotune(ns(), {'autotune': 40})['intensite'], 40)
        r = D.reglage_autotune(ns(autotune=70), {'autotune': 40})
        self.assertEqual((r['intensite'], r['explicite']), (70, True))
        r = D.reglage_autotune(ns(autotune=70, sans_autotune=True), {'autotune': 40})
        self.assertEqual((r['intensite'], r['retirer']), (0, True))
        r = D.reglage_autotune(ns(autotune_gamme='la-mineur'), {})
        self.assertEqual((r['gamme'], r['gamme_donnee']), ('la-mineur', True))
        # --remix sans option : la version auto-tune existante est gardée à jour
        rapport = {'autotune': {'applique': True, 'intensite': 30, 'explicite': False, 'gamme': {'demandee': 'do-majeur'}}}
        r = D.reglage_pour(argparse.Namespace(autotune_reglage=D.reglage_autotune(ns(), {})), rapport)
        self.assertEqual((r['intensite'], r['gamme']), (30, 'do-majeur'))
        r = D.reglage_pour(argparse.Namespace(autotune_reglage=D.reglage_autotune(ns(autotune=90), {})), rapport)
        self.assertEqual(r['intensite'], 90)


class Gamme(unittest.TestCase):
    def test_syntaxe(self):
        D = D0
        cas = {'auto': None, 'Automatique': None, 'chromatique': 'chromatique', 'chromatic': 'chromatique',
               'la-mineur': (9, 'mineur'), 'La mineur': (9, 'mineur'), 'la_mineur': (9, 'mineur'), 'lam': (9, 'mineur'),
               'do#-majeur': (1, 'majeur'), 'do♯ majeur': (1, 'majeur'), 'do-dièse-majeur': (1, 'majeur'),
               'ré-bémol-majeur': (1, 'majeur'), 'réb': (1, 'majeur'), 'sib': (10, 'majeur'), 'si': (11, 'majeur'),
               'mib-mineur': (3, 'mineur'), 'sol': (7, 'majeur'), 'fa#m': (6, 'mineur'), 'Am': (9, 'mineur'),
               'C#m': (1, 'mineur'), 'Eb': (3, 'majeur'), 'Eb-minor': (3, 'mineur'), 'B': (11, 'majeur'),
               'Bb': (10, 'majeur'), 'Bbm': (10, 'mineur'), 'Bm': (11, 'mineur'), 'F# major': (6, 'majeur'),
               'g-min': (7, 'mineur'), 'D maj': (2, 'majeur')}
        for texte, attendu in cas.items():
            g = D.lire_gamme(texte)
            if attendu is None:
                self.assertEqual(g, {'type': 'auto'}, texte)
            elif attendu == 'chromatique':
                self.assertEqual(g, {'type': 'chromatique'}, texte)
            else:
                self.assertEqual((g['type'], g['tonique'], g['mode']), ('tonalite', *attendu), texte)
        for texte in ('h-majeur', 'la-moyen', '', 'do##', 'majeur', 'la mineur bis'):
            with self.assertRaises(ValueError, msg=texte):
                D.lire_gamme(texte)

    def test_noms_et_masques(self):
        D = D0
        self.assertEqual(D.nom_tonalite(9, 'mineur'), ('la mineur', 'Am'))
        self.assertEqual(D.nom_tonalite(1, 'majeur'), ('réb majeur', 'Db'))
        self.assertEqual(D.nom_tonalite(10, 'majeur'), ('sib majeur', 'Bb'))
        self.assertEqual(D.masque_gamme(0, 'majeur'), [1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1])
        self.assertEqual(D.masque_gamme(9, 'mineur'), D.masque_gamme(0, 'majeur'))   # relatives : mêmes notes
        self.assertEqual(sum(D.masque_gamme(6, 'majeur')), 7)

    def test_option_refusee_par_argparse(self):
        r = subprocess.run([python(), str(DOUBLER), '--autotune-gamme', 'h-majeur', 'x.mp4'], capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn('gamme illisible', r.stderr)


# --- Tonalité (ffmpeg) ------------------------------------------------------------------------------------------------

@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Tonalite(unittest.TestCase):
    ESSAIS = {'do majeur': ['C', 'F', 'G', 'C', 'Am', 'Dm', 'G7', 'C'],
              'la mineur': ['Am', 'Dm', 'E', 'Am', 'F', 'Dm', 'E7', 'Am'],
              'mi majeur': ['E', 'A', 'B', 'E', 'C#m', 'F#m', 'B7', 'E'],
              'sib majeur': ['Bb', 'Eb', 'F', 'Bb', 'Gm', 'Cm', 'F7', 'Bb'],
              'fa# mineur': ['F#m', 'D', 'A', 'E', 'F#m', 'Bm', 'C#', 'F#m']}

    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)

    def gamme(self, x, texte='auto'):
        ecrire_wav(self.tmp / 'musique.wav', x, canaux=2)
        with contextlib.redirect_stdout(io.StringIO()):
            return D0.choisir_gamme(texte, self.tmp / 'musique.wav')

    def test_accords_de_synthese(self):
        for attendu, noms in self.ESSAIS.items():
            with self.subTest(attendu=attendu):
                g = self.gamme(accords(noms * 2))
                self.assertEqual(g['type'], 'tonalite')
                self.assertEqual(g['tonalite'], attendu, g['detection'])
                self.assertTrue(g['detection']['sure'])
                self.assertAlmostEqual(g['accord_hz'], 440.0, delta=0.6)
                gamme = D0.masque_gamme(g['tonique'], g['mode'])
                self.assertTrue(all(g['masque'][k] for k in range(12) if gamme[k]))   # les 7 notes, au moins
        g = self.gamme(accords(self.ESSAIS['la mineur'] * 2))
        self.assertIn('sol#', g['notes_ajoutees'])   # sensible de l'accord de mi majeur (mineur harmonique)
        self.assertEqual(g['tonalite_en'], 'Am')

    def test_accord_435_hz(self):
        g = self.gamme(accords(self.ESSAIS['do majeur'] * 2, accord_hz=435.0))
        self.assertEqual(g['tonalite'], 'do majeur')
        self.assertAlmostEqual(g['accord_cents'], 1200 * np.log2(435 / 440), delta=2.0)
        self.assertAlmostEqual(g['accord_hz'], 435.0, delta=0.6)

    def test_peu_sur_ou_silence_chromatique(self):
        rng = np.random.default_rng(5)
        g = self.gamme(rng.standard_normal(SR * 6) * 0.05)
        self.assertEqual((g['type'], g['masque']), ('chromatique', [1] * 12))
        self.assertIn('peu sûre', g['repli'])
        g = self.gamme(np.zeros(SR * 3))
        self.assertEqual(g['type'], 'chromatique')
        self.assertIn('trop faible', g['repli'])
        self.assertEqual(g['accord_hz'], 440.0)

    def test_gamme_imposee(self):
        g = self.gamme(accords(self.ESSAIS['mi majeur'] * 2), 'la-mineur')
        self.assertEqual((g['tonalite'], g['notes_ajoutees']), ('la mineur', []))
        self.assertEqual(g['masque'], D0.masque_gamme(9, 'mineur'))
        self.assertEqual(g['detection']['tonalite'], 'mi majeur')   # la détection reste notée
        g = self.gamme(accords(self.ESSAIS['mi majeur'] * 2), 'chromatique')
        self.assertEqual((g['type'], g['repli']), ('chromatique', None))


# --- finir() de bout en bout ------------------------------------------------------------------------------------------

def video(chemin, duree):
    srt = chemin.with_suffix('.orig.srt')
    srt.write_text('1\n00:00:00,500 --> 00:00:01,500\nsous-titre d\'origine\n\n', encoding='utf-8')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size=128x96:rate=25:duration={duree}',
                    '-f', 'lavfi', '-i', f'sine=frequency=440:duration={duree}:sample_rate={SR}', '-i', str(srt),
                    '-map', '0', '-map', '1', '-map', '2', '-c:s', 'mov_text', '-metadata:s:s:0', 'language=eng',
                    '-c:v', 'mpeg4', '-g', '25', '-c:a', 'aac', str(chemin)], check=True)
    return chemin


PAROLES = {'format': 1, 'langue': 'es', 'source': 'whisper-large-v3', 'decalage_s': 0.0,
           'segments': [{'debut': 1.0, 'fin': 2.0, 'texte': 'el gato come pan', 'langue': 'es',
                         'mots': [{'debut': 1.0, 'fin': 1.4, 'mot': 'el'}]}],
           'traductions': {'fr': {'source': 'nllb-200-distilled-600M',
                                  'segments': [{'debut': 1.0, 'fin': 2.0, 'texte': 'le chat mange du pain'}]}}}


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Finir(unittest.TestCase):
    DUREE = 6.0

    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)
        (self.tmp / 'home').mkdir()
        self.D = charger_doubler(self.tmp / 'home')
        self.corbeille = self.tmp / 'corbeille'
        self.corbeille.mkdir()

        def corbeille(fichiers):   # jamais la vraie corbeille de l'utilisateur
            jetes = []
            for f in fichiers:
                if Path(f).exists():
                    shutil.move(str(f), self.corbeille / Path(f).name)
                    jetes.append(Path(f))
            return jetes, []
        self.D.corbeille = corbeille
        self.travail()

    def travail(self, mode='chant'):
        d = self.DUREE
        self.source = video(self.tmp / 'clip.mp4', d)
        self.dossier = self.tmp / 'sorties' / 'clip'
        t, tv = self.dossier / 'travail', self.dossier / 'travail' / 'test'
        tv.mkdir(parents=True)
        self.t, self.tv = t, tv
        (t / 'source.json').write_text(json.dumps({'video': str(self.source), 'flux_audio': 1, 'decalage_son': 0.0}))
        musique = accords(['Am', 'Dm', 'E', 'Am'], duree=d / 4)
        juste = melodie_fausse(d, decalages=(0, 0, 0, 0, 0, 0))
        voix_o = chant(juste)
        ecrire_wav(t / 'original.wav', musique + voix_o, canaux=2)
        ecrire_wav(t / 'voix_source.wav', voix_o, canaux=2)
        self.fausse = melodie_fausse(d)
        ecrire_wav(tv / 'voix.wav', chant(self.fausse))
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(tv / 'voix.wav'), '-c:a', 'flac', '-sample_fmt', 's32',
                        str(tv / 'voix_convertie.flac')], check=True)
        (tv / 'voix.wav').unlink()
        (tv / 'rapport.json').write_text(json.dumps({'mode': mode, 'frise': []}))
        (tv / 'paroles.json').write_text(json.dumps(PAROLES))
        hz = lambda m: [round(float(440 * 2 ** ((v - 69) / 12)), 1) if v == v else 0 for v in m[::2]]
        self.courbes0 = {'format': 1, 'pas_s': 0.02, 'source_hz': hz(juste), 'convertie_hz': hz(self.fausse),
                         'decalage_dt': [0] * len(juste[::2])}
        (tv / 'courbes.json').write_text(json.dumps(self.courbes0, separators=(',', ':')))
        self.evenements = self.tmp / 'ev.jsonl'
        self.D.EV.ouvrir(self.evenements)
        self.addCleanup(self.D.EV.f.close)

    def finir(self, intensite=None, sans=False, gamme=None, conf=None):
        a = argparse.Namespace(voix_db=None, mix_v1=False, reverb_db=-13.0, paroles=True, autotune=intensite,
                               sans_autotune=sans, autotune_gamme=gamme)
        with contextlib.redirect_stdout(io.StringIO()) as texte:
            a.autotune_reglage = self.D.reglage_autotune(a, conf or {})
            tr = {'travail': self.t, 'tv': self.tv, 'dossier': self.dossier, 'base': 'clip - voix Test', 'nom': 'clip'}
            self.D.finir(tr, a, {'id': 'test', 'affichage': 'Test'})
        self.texte = texte.getvalue()
        self.rapport = json.loads((self.tv / 'rapport.json').read_text())
        return [json.loads(l) for l in self.evenements.read_text().splitlines() if '"sortie"' in l][-1]

    def normales(self):
        noms = ['clip - voix Test.mp4', 'clip - voix Test.mp3', 'clip - voix Test.lrc', 'paroles/clip - voix Test.es.srt']
        travail = ['mix.wav', 'seche_voix_convertie.wav', 'reverb_voix_convertie.wav', 'instrumental.wav']
        return {n: sha(self.dossier / n) for n in noms} | {n: sha(self.tv / n if n != 'instrumental.wav' else self.t / n)
                                                          for n in travail}

    def autotunes(self):
        return sorted([f.name for f in self.dossier.glob('*(auto-tune)*')]
                      + [f'travail/{f.name}' for f in self.tv.glob('*autotune*')])

    def test_repli_sans_greffon(self):
        with mock.patch.dict(os.environ, {'LV2_PATH': ''}):
            self.D.LV2_STANDARD = []
            ev = self.finir(70)
        self.assertIn('installez x42-plugins : sudo apt install x42-plugins', self.texte)
        self.assertIn('version normale seule', self.texte)
        self.assertTrue((self.dossier / 'clip - voix Test.mp4').exists())
        self.assertTrue((self.dossier / 'clip - voix Test.mp3').exists())
        self.assertEqual(self.autotunes(), [])
        self.assertFalse(self.rapport['autotune']['applique'])
        self.assertIn('x42-plugins', self.rapport['autotune']['raison'])
        self.assertNotIn('autotune', ev)
        self.assertNotIn('autotune_hz', json.loads((self.tv / 'courbes.json').read_text()))

    @unittest.skipUnless(FAT1, 'x42-autotune (fat1) absent')
    def test_bout_en_bout_noms_rapport_evenement_courbes(self):
        ev = self.finir(100)
        b = 'clip - voix Test (auto-tune)'
        self.assertEqual(self.autotunes(), sorted([f'{b}.mp3', f'{b}.mp4', f'{b}.lrc', 'travail/mix_autotune.wav',
                                                   'travail/reverb_voix_autotune.wav',
                                                   'travail/seche_voix_autotune.wav']))
        # mêmes pistes de paroles et sous-titres d'origine que la version normale ; même .lrc
        self.assertEqual(len(sous_titres(self.dossier / f'{b}.mp4')), 3)
        self.assertEqual((self.dossier / f'{b}.lrc').read_bytes(), (self.dossier / 'clip - voix Test.lrc').read_bytes())
        genres = lambda f: [(s['codec_type'], s['codec_name']) for s in ffprobe(f, '-show_streams')['streams']]
        self.assertEqual(genres(self.dossier / f'{b}.mp4'), genres(self.dossier / 'clip - voix Test.mp4'))
        at = self.rapport['autotune']
        self.assertTrue(at['applique'])
        self.assertEqual((at['intensite'], at['source'], len(at['passes'])), (100, 'option --autotune', 2))
        self.assertEqual(at['gamme']['tonalite'], 'la mineur')
        self.assertEqual(at['fichiers'], {'mp3': f'{b}.mp3', 'mix': 'mix_autotune.wav', 'seche': 'seche_voix_autotune.wav',
                                          'reverb': 'reverb_voix_autotune.wav', 'video': f'{b}.mp4', 'lrc': f'{b}.lrc'})
        self.assertEqual(at['gains_db'], self.rapport['mix']['gains_db'])   # même niveau que la version normale
        avant, apres = at['mesures']['avant'], at['mesures']['apres']
        self.assertGreater(avant['ecart_median_cents'], 20)   # voix fausse de 25 à 40 cents
        self.assertLess(apres['ecart_median_cents'], 6)       # ramenée sur les notes
        self.assertGreater(apres['part_a_10_cents'], 0.7)
        self.assertEqual(ev['autotune']['mp4'], str(self.dossier / f'{b}.mp4'))
        self.assertEqual(ev['autotune']['mp3'], str(self.dossier / f'{b}.mp3'))
        self.assertEqual((ev['autotune']['intensite'], ev['autotune']['tonalite']), (100, 'la mineur'))
        self.assertEqual(ev['mp4'], str(self.dossier / 'clip - voix Test.mp4'))
        # courbes.json : autotune_hz ajouté, au même pas, le reste inchangé
        c = json.loads((self.tv / 'courbes.json').read_text())
        self.assertEqual({k: v for k, v in c.items() if k != 'autotune_hz'}, self.courbes0)
        self.assertEqual(len(c['autotune_hz']), len(c['convertie_hz']))
        self.assertEqual([h > 0 for h in c['autotune_hz']], [h > 0 for h in c['convertie_hz']])
        m = at['gamme']['masque']
        self.assertGreater(np.median(ecarts_cents(c['convertie_hz'], m)), 20)
        self.assertLess(np.median(ecarts_cents(c['autotune_hz'], m)), 8)
        # même longueur que la voix sèche, et calée dessus (latence de fat1 compensée)
        x = D0.lire_brut(self.tv / 'seche_voix_convertie.wav')
        y = D0.lire_brut(self.tv / 'seche_voix_autotune.wav')
        self.assertEqual(len(x), len(y))
        seuil = 0.05 * np.abs(x).max()
        self.assertLess(abs(int(np.argmax(np.abs(y) > seuil)) - int(np.argmax(np.abs(x) > seuil))), 90)   # < 2 ms
        self.assertIn('auto-tune 100', self.texte)

    @unittest.skipUnless(FAT1, 'x42-autotune (fat1) absent')
    def test_versions_normales_identiques(self):
        self.finir()
        sans = self.normales()
        self.assertNotIn('autotune', self.rapport)
        self.finir(70)
        self.assertTrue(self.rapport['autotune']['applique'])
        self.assertEqual(self.normales(), sans)
        self.finir(30, gamme='chromatique')
        self.assertEqual(self.normales(), sans)

    @unittest.skipUnless(FAT1, 'x42-autotune (fat1) absent')
    def test_remix_retrait_et_version_gardee_a_jour(self):
        self.finir(70)
        avant = self.autotunes()
        self.assertEqual(len(avant), 6)
        ev = self.finir(sans=True)                    # --remix --sans-autotune
        self.assertEqual(self.autotunes(), [])
        self.assertEqual(sorted(f.name for f in self.corbeille.iterdir()),
                         sorted(n for n in avant if not n.startswith('travail/')))
        self.assertFalse(self.rapport['autotune']['applique'])
        self.assertIn('--sans-autotune', self.rapport['autotune']['raison'])
        self.assertNotIn('autotune', ev)
        self.assertNotIn('autotune_hz', json.loads((self.tv / 'courbes.json').read_text()))
        self.assertTrue((self.dossier / 'clip - voix Test.mp4').exists())
        self.finir()                                  # --remix sans option, configuration à 0 : rien de refait
        self.assertEqual(self.autotunes(), [])
        self.finir(30, gamme='la-mineur')             # --remix --autotune 30
        self.assertEqual(len(self.autotunes()), 6)
        self.assertEqual(self.rapport['autotune']['gamme']['demandee'], 'la-mineur')
        mix30 = sha(self.tv / 'mix_autotune.wav')
        self.finir(conf={'autotune': 90})            # --remix sans option : même intensité qu'avant, pas la configuration
        at = self.rapport['autotune']
        self.assertEqual((at['intensite'], at['gamme']['demandee']), (30, 'la-mineur'))
        self.assertTrue(at['source'].startswith('doublage précédent'))
        self.assertEqual(sha(self.tv / 'mix_autotune.wav'), mix30)
        self.finir(90)                                # --remix --autotune 90 : refait
        self.assertEqual(self.rapport['autotune']['intensite'], 90)
        self.assertNotEqual(sha(self.tv / 'mix_autotune.wav'), mix30)


@unittest.skipUnless(FFMPEG, 'ffmpeg absent')
class Parole(Finir):
    def travail(self, mode='parole'):
        super().travail('parole')

    def test_configuration_seule_pas_d_autotune(self):
        self.finir(conf={'autotune': 70})
        self.assertIn("auto-tune non appliqué : c'est de la parole", self.texte)
        self.assertFalse(self.rapport['autotune']['applique'])
        self.assertEqual(self.autotunes(), [])

    @unittest.skipUnless(FAT1, 'x42-autotune (fat1) absent')
    def test_demande_explicite_avec_avertissement(self):
        self.finir(50)
        self.assertIn('sur de la parole, comme demandé', self.texte)
        self.assertTrue(self.rapport['autotune']['applique'])
        self.assertIsNone(self.rapport['autotune']['fichiers']['reverb'])   # parole : pas de reverb
        self.assertFalse((self.tv / 'reverb_voix_autotune.wav').exists())

    # les essais de chant n'ont pas lieu d'être refaits en parole
    test_bout_en_bout_noms_rapport_evenement_courbes = None
    test_versions_normales_identiques = None
    test_remix_retrait_et_version_gardee_a_jour = None
    test_repli_sans_greffon = None


class Commande(unittest.TestCase):
    def setUp(self):
        self._d = Dossier()
        self.tmp = self._d.__enter__()
        self.addCleanup(self._d.__exit__)

    def doubler(self, *args):
        env = {**os.environ, 'HOME': str(self.tmp)}
        for v in ('VOIXLA_DONNEES', 'DOUBLER_VOIX', 'VOIXLA_KAGGLE'):
            env.pop(v, None)
        return subprocess.run([python(), str(DOUBLER), *args], capture_output=True, text=True, env=env)

    def test_aide_annonce_les_options(self):
        r = self.doubler('--help')
        self.assertEqual(r.returncode, 0)
        for o in ('--autotune', '--sans-autotune', '--autotune-gamme'):
            self.assertRegex(r.stdout, o.replace('-', r'\-') + r'(?![\w-])')

    def test_cle_de_configuration(self):
        config = self.tmp / '.config' / 'voix-la' / 'config.json'
        self.assertEqual(self.doubler('--config', 'autotune', '70').returncode, 0)
        self.assertEqual(json.loads(config.read_text())['autotune'], 70)
        self.assertEqual(self.doubler('--config', 'autotune', 'non').returncode, 0)
        self.assertEqual(json.loads(config.read_text())['autotune'], 0)
        r = self.doubler('--config', 'autotune', '150')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('de 0 à 100', r.stderr)
        r = self.doubler('--config', '--json')
        self.assertEqual(json.loads(r.stdout)['autotune'], 0)


if __name__ == '__main__':
    unittest.main()
