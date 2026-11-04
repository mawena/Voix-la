"""Essais de voixla.analyse : courbes de mélodie (courbes.json au format 1, consigne, décalages par trame), F0 (YIN)
sur des trames numpy et sur des sons synthétiques générés par ffmpeg dans un dossier temporaire, découpage en trames,
spectrogramme, bandes du vu-mètre, forme d'onde, caches .npz (dans le dossier temporaire par VOIXLA_CACHE), repli
courbes-locales.json et ligne de commande. Aucun appel à doubler ni à kaggle ; rien n'est écrit hors des dossiers
temporaires."""

import contextlib
import gc
import io
import json
import math
import os
import time
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

import numpy as np

from voixla import analyse, chemins

from .commun import EssaiIsole

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
INTERFACE = Path(__file__).resolve().parents[1]
SR = analyse.SR
# trames de YIN telles qu'analyser les fait : fenêtre de 40 ms plus la plus longue période cherchée, toutes les 20 ms
LONGUEUR_YIN = int(SR * 0.04) + int(SR / analyse.F0_MIN)
PAS_YIN = int(SR * analyse.PAS_F0_S)


# --- Sons d'essai (ffmpeg) -------------------------------------------------------------------------------------------

def ffmpeg(sortie, entrees, filtre=None, sr=44100):
    """Écrit « sortie » (mono, sr Hz ; WAV 16 bits ou FLAC d'après l'extension) à partir de sources lavfi."""
    commande = ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    for e in entrees:
        commande += ["-f", "lavfi", "-i", e]
    if filtre:
        commande += ["-filter_complex", filtre]
    commande += ["-ac", "1", "-ar", str(sr), str(sortie)]
    subprocess.run(commande, check=True, timeout=60, stdin=subprocess.DEVNULL)
    return Path(sortie)


def sinus(sortie, hz, duree=3, volume=1, sr=44100):
    """Sinus d'amplitude 1/8 × volume (crête −18 dBFS à volume 1)."""
    return ffmpeg(sortie, [f"sine=frequency={hz}:duration={duree}:sample_rate={sr}"],
                  f"volume={volume}" if volume != 1 else None, sr)


# amplitudes des harmoniques 1 à 8 : la 2e plus forte que la fondamentale, comme souvent dans une voix chantée
HARMONIQUES = (0.5, 1.0, 0.6, 0.45, 0.3, 0.2, 0.12, 0.08)


def voix_chantee(sortie, hz=196, duree=3, sr=44100):
    """Son riche en harmoniques avec un vibrato de 5 Hz à ±1,5 % autour de hz (sol2 par défaut)."""
    phase = f"(2*PI*{hz}*t-{hz * 0.015 / 5:.4f}*cos(10*PI*t))"
    somme = "+".join(f"{a}*sin({k}*{phase})" for k, a in enumerate(HARMONIQUES, 1))
    return ffmpeg(sortie, [f"aevalsrc=exprs=0.08*({somme}):s={sr}:d={duree}"], sr=sr)


FABRIQUES = {
    "sinus_220": ("sinus_220.wav", lambda f: sinus(f, 220)),
    "sinus_98": ("sinus_98.wav", lambda f: sinus(f, 98)),
    "sinus_659": ("sinus_659.wav", lambda f: sinus(f, 659.3)),
    "glissade": ("glissade.wav", lambda f: ffmpeg(f, ["sine=frequency=220:duration=1.5:sample_rate=44100",
                                                      "sine=frequency=330:duration=1.5:sample_rate=44100"],
                                                  "[0:a][1:a]concat=n=2:v=0:a=1")),
    "silence": ("silence.wav", lambda f: ffmpeg(f, ["aevalsrc=exprs=0:s=44100:d=3"])),
    "bruit": ("bruit.wav", lambda f: ffmpeg(
        f, ["anoisesrc=duration=3:color=white:amplitude=0.01:sample_rate=44100:seed=42"])),   # vers −45 dBFS
    "chant": ("chant.wav", lambda f: voix_chantee(f)),
    "court": ("court.wav", lambda f: sinus(f, 220, duree=2.345, sr=22050)),
    "la_1k": ("la_1k.wav", lambda f: sinus(f, 1000, duree=2, sr=16000)),
    "la_1k_fort": ("la_1k_fort.wav", lambda f: sinus(f, 1000, duree=2, volume=2, sr=16000)),
    "source_220": ("voix_source.wav", lambda f: sinus(f, 220)),
    "convertie_110": ("voix_convertie.flac", lambda f: sinus(f, 110)),
}
_SONS = {}
_ANALYSES = {}
_DOSSIER_SONS = []


def son(nom):
    """Chemin d'un son d'essai, généré au premier besoin dans un dossier temporaire du module (effacé à la fin)."""
    if nom not in _SONS:
        if not _DOSSIER_SONS:
            _DOSSIER_SONS.append(tempfile.TemporaryDirectory(prefix="voixla-analyse-sons-"))
        fichier, fabrique = FABRIQUES[nom]
        _SONS[nom] = fabrique(Path(_DOSSIER_SONS[0].name) / fichier)
    return _SONS[nom]


def analyse_de(nom, produits):
    """Résultat d'analyser (sans cache) pour un son d'essai, calculé une fois ; à ne pas modifier."""
    cle = (nom, tuple(produits))
    if cle not in _ANALYSES:
        _ANALYSES[cle] = analyse.analyser(son(nom), produits, ecrire_cache=False)
    return _ANALYSES[cle]


def tearDownModule():
    for d in _DOSSIER_SONS:
        d.cleanup()


def voisees(f0):
    return np.asarray(f0)[np.asarray(f0) > 0]


class EssaiAvecCache(EssaiIsole):
    """EssaiIsole, plus le cache des analyses dans le dossier temporaire (VOIXLA_CACHE, et XDG_CACHE_HOME par
    prudence) : rien n'est écrit dans ~/.cache/voix-la. Les variables sont rendues après l'essai."""

    def setUp(self):
        super().setUp()
        self.cache = self.tmp / "cache"
        os.environ["VOIXLA_CACHE"] = str(self.cache)
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "xdg-cache")

    def fichiers_cache(self):
        d = self.cache / "pistes"
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


# --- lire_courbes ----------------------------------------------------------------------------------------------------

class EssaiLireCourbes(EssaiIsole):
    def ecrire(self, contenu, nom="courbes.json"):
        f = self.tmp / nom
        f.write_text(contenu if isinstance(contenu, str) else json.dumps(contenu, ensure_ascii=False),
                     encoding="utf-8")
        return f

    def courbes(self, **autres):
        d = {"format": 1, "pas_s": 0.02, "source_hz": [0, 220.0, 220.5, 0], "convertie_hz": [0, 110.0, 110.2, 0],
             "decalage_dt": [0, -12, -12, 0]}
        d.update(autres)
        return {k: v for k, v in d.items() if v is not ...}

    def test_fichier_valide(self):
        c = analyse.lire_courbes(self.ecrire(self.courbes()))
        self.assertEqual(c["pas_s"], 0.02)
        np.testing.assert_allclose(c["source"], [0, 220.0, 220.5, 0], rtol=1e-6)
        np.testing.assert_allclose(c["convertie"], [0, 110.0, 110.2, 0], rtol=1e-6)
        np.testing.assert_array_equal(c["decalage"], [0, -12, -12, 0])
        for k in ("source", "convertie", "decalage"):
            self.assertEqual(c[k].dtype, np.float32, k)
        self.assertIsNone(c["choeurs"])
        self.assertEqual(c["origine"], "doubler")

    def test_origine(self):
        # courbes-locales.json sans origine : « interface » ; une origine écrite dans le fichier est gardée
        self.assertEqual(analyse.lire_courbes(self.ecrire(self.courbes(), "courbes-locales.json"))["origine"],
                         "interface")
        f = self.ecrire(self.courbes(origine="interface (YIN local, 16 kHz)"), "courbes-locales.json")
        self.assertEqual(analyse.lire_courbes(f)["origine"], "interface (YIN local, 16 kHz)")

    def test_choeurs(self):
        for cle in ("chœurs_hz", "choeurs_hz"):
            with self.subTest(cle=cle):
                c = analyse.lire_courbes(self.ecrire(self.courbes(**{cle: [0, 330.0, 331.0, 0]})))
                np.testing.assert_allclose(c["choeurs"], [0, 330.0, 331.0, 0], rtol=1e-6)
        # absents, vides, plus courts (complétés par des 0) ou plus longs (tronqués)
        self.assertIsNone(analyse.lire_courbes(self.ecrire(self.courbes(**{"chœurs_hz": []})))["choeurs"])
        c = analyse.lire_courbes(self.ecrire(self.courbes(**{"chœurs_hz": [300.0]})))
        np.testing.assert_array_equal(c["choeurs"], [300.0, 0, 0, 0])
        c = analyse.lire_courbes(self.ecrire(self.courbes(**{"chœurs_hz": [300.0] * 9})))
        self.assertEqual(len(c["choeurs"]), 4)

    def test_longueurs_differentes(self):
        # tronquées à la plus courte de source et convertie ; le décalage est tronqué ou complété par des 0
        c = analyse.lire_courbes(self.ecrire(self.courbes(source_hz=[100, 200, 300, 400, 500],
                                                          convertie_hz=[110, 210, 310],
                                                          decalage_dt=[1, 2, 3, 4])))
        for k in ("source", "convertie", "decalage"):
            self.assertEqual(len(c[k]), 3, k)
        np.testing.assert_array_equal(c["source"], [100, 200, 300])
        np.testing.assert_array_equal(c["decalage"], [1, 2, 3])
        c = analyse.lire_courbes(self.ecrire(self.courbes(source_hz=[100, 200, 300],
                                                          convertie_hz=[110, 210, 310, 410, 510],
                                                          decalage_dt=[-12])))
        np.testing.assert_array_equal(c["convertie"], [110, 210, 310])
        np.testing.assert_array_equal(c["decalage"], [-12, 0, 0])
        # une seule des deux courbes : l'autre est muette
        c = analyse.lire_courbes(self.ecrire(self.courbes(source_hz=[], convertie_hz=[110, 210])))
        np.testing.assert_array_equal(c["source"], [0, 0])
        self.assertIsNone(analyse.lire_courbes(self.ecrire(self.courbes(source_hz=[], convertie_hz=[]))))

    def test_format_different(self):
        for format_ in (2, 0, "1", None, ...):
            with self.subTest(format=format_):
                self.assertIsNone(analyse.lire_courbes(self.ecrire(self.courbes(format=format_))))

    def test_pas_absent_ou_nul(self):
        for pas in (..., 0, None, -0.02, "abc", []):
            with self.subTest(pas=pas):
                self.assertIsNone(analyse.lire_courbes(self.ecrire(self.courbes(pas_s=pas))))
        self.assertEqual(analyse.lire_courbes(self.ecrire(self.courbes(pas_s=0.01)))["pas_s"], 0.01)

    def test_json_illisible(self):
        self.assertIsNone(analyse.lire_courbes(self.ecrire('{"format": 1, "pas_s": 0.02, "source_hz": [1')))
        self.assertIsNone(analyse.lire_courbes(self.ecrire("")))
        self.assertIsNone(analyse.lire_courbes(self.ecrire("[1, 2, 3]")))
        self.assertIsNone(analyse.lire_courbes(self.tmp / "absent.json"))

    def test_valeurs_non_voisees(self):
        # null, négatives, sous 1 Hz, texte, NaN (accepté par le module json) : non voisées (0)
        brut = ('{"format": 1, "pas_s": 0.02, "source_hz": [null, -220, 0.5, "220", NaN, 220],'
                ' "convertie_hz": [110, null, -5, 110, 110, 110], "decalage_dt": [null, -12, "x", 0, 0, 0]}')
        c = analyse.lire_courbes(self.ecrire(brut))
        np.testing.assert_array_equal(c["source"], [0, 0, 0, 0, 0, 220])
        np.testing.assert_array_equal(c["convertie"], [110, 0, 0, 110, 110, 110])
        np.testing.assert_array_equal(c["decalage"], [0, -12, 0, 0, 0, 0])

    # Bogue corrigé, gardé en non-régression : une courbe qui n'est pas une liste (nombre, booléen) fait lever TypeError (« for x in (v or []) » sur un
    # nombre) au lieu de rendre None comme pour un fichier illisible. Correction proposée : dans _tableau, traiter
    # tout ce qui n'est pas une liste comme vide (« if not isinstance(v, list): v = [] »), de même pour decalage_dt.
    def test_courbe_qui_n_est_pas_une_liste(self):
        self.assertIsNone(analyse.lire_courbes(self.ecrire(self.courbes(source_hz=5, convertie_hz=7))))


# --- consigne et décalages -------------------------------------------------------------------------------------------

class EssaiConsigne(unittest.TestCase):
    def test_octaves(self):
        c = analyse.consigne([220.0, 220.0, 220.0, 0.0], [-12, 12, 0, 12])
        np.testing.assert_allclose(c, [110.0, 440.0, 220.0, 0.0], rtol=1e-6)
        self.assertEqual(c.dtype, np.float32)
        np.testing.assert_allclose(analyse.consigne([220.0], [7]), [220 * 2 ** (7 / 12)], rtol=1e-6)

    def test_zero_reste_zero(self):
        np.testing.assert_array_equal(analyse.consigne([0, 0, 0], [-12, 0, 12]), [0, 0, 0])

    def test_decalage_plus_court_ou_plus_long(self):
        c = analyse.consigne([220.0] * 4, [12])
        np.testing.assert_allclose(c, [440.0, 220.0, 220.0, 220.0], rtol=1e-6)
        c = analyse.consigne([220.0] * 2, [-12, -12, 12, 12])
        np.testing.assert_allclose(c, [110.0, 110.0], rtol=1e-6)
        np.testing.assert_array_equal(analyse.consigne([220.0, 0.0], []), [220.0, 0.0])


class EssaiDecalagesParTrame(unittest.TestCase):
    MORCEAUX = [{"debut_s": 0.0, "fin_s": 1.0, "decalage": -12}, {"debut_s": 1.0, "fin_s": 2.0, "decalage": 0},
                {"debut_s": 2.5, "fin_s": 3.0, "decalage": 12}]

    def test_morceaux_du_rapport(self):
        d = analyse.decalages_par_trame({"mode": "chant", "modele": "chant44k", "morceaux": self.MORCEAUX}, 150, 0.02)
        self.assertEqual(d.dtype, np.float32)
        self.assertEqual(len(d), 150)
        np.testing.assert_array_equal(d[:50], -12)       # [0 ; 1[ s
        np.testing.assert_array_equal(d[50:125], 0)      # [1 ; 2[ s, puis le trou entre 2 et 2,5 s
        np.testing.assert_array_equal(d[125:], 12)       # [2,5 ; 3[ s

    def test_morceaux_incomplets(self):
        r = {"mode": "chant", "morceaux": [{"debut_s": 0, "fin_s": 1, "decalage": 0.5},
                                           {"debut_s": 1, "decalage": 12},                  # sans fin : ignoré
                                           {"debut_s": 1, "fin_s": 2, "decalage": None},    # None : 0
                                           {"debut_s": "x", "fin_s": 3, "decalage": 12}]}   # illisible : ignoré
        d = analyse.decalages_par_trame(r, 100, 0.02)
        np.testing.assert_array_equal(d[:50], 0.5)
        np.testing.assert_array_equal(d[50:], 0)

    def test_parole_avec_le_modele_parole22k(self):
        r = {"mode": "parole", "modele": "parole22k", "morceaux": self.MORCEAUX}
        np.testing.assert_array_equal(analyse.decalages_par_trame(r, 150, 0.02), np.zeros(150))

    def test_parole_avec_le_modele_chant(self):
        r = {"mode": "parole", "modele": "chant44k", "morceaux": self.MORCEAUX}
        self.assertEqual(analyse.decalages_par_trame(r, 150, 0.02)[0], -12)

    def test_sans_rapport(self):
        for r in (None, {}, {"morceaux": None}):
            with self.subTest(rapport=r):
                np.testing.assert_array_equal(analyse.decalages_par_trame(r, 7, 0.02), np.zeros(7))


# --- Notes et niveaux ------------------------------------------------------------------------------------------------

class EssaiNotes(unittest.TestCase):
    def test_note_fr(self):
        self.assertEqual(analyse.note_fr(440), "la3")
        self.assertEqual(analyse.note_fr(261.6), "do3")
        self.assertEqual(analyse.note_fr(220), "la2")
        self.assertEqual(analyse.note_fr(246.94), "si2")         # l'octave change sur le do
        self.assertEqual(analyse.note_fr(466.16), "la♯3")
        self.assertEqual(analyse.note_fr(523.25), "do4")
        self.assertEqual(analyse.note_fr(98), "sol1")
        for hz in (0, None, -5, np.float32(0)):
            self.assertEqual(analyse.note_fr(hz), "", hz)

    def test_hz_de_midi(self):
        self.assertAlmostEqual(analyse.hz_de_midi(69), 440.0)
        self.assertAlmostEqual(analyse.hz_de_midi(60), 261.63, places=2)
        self.assertEqual(analyse.note_fr(analyse.hz_de_midi(57)), "la2")

    def test_octets(self):
        np.testing.assert_array_equal(analyse.en_octets([0, -90, -200, 6]), [255, 0, 0, 255])
        for db in (-80.0, -45.0, -18.0, -1.0):
            # un octet vaut 90 / 255 ≈ 0,35 dB (en_octets tronque)
            self.assertAlmostEqual(float(analyse.depuis_octets(analyse.en_octets(db))), db, delta=0.36)


# --- YIN et nettoyage, sur des trames numpy (sans ffmpeg) ------------------------------------------------------------

def signal_riche(hz, duree, amplitudes=HARMONIQUES, sr=SR):
    t = np.arange(int(duree * sr)) / sr
    return sum(a * np.sin(2 * np.pi * k * hz * t) for k, a in enumerate(amplitudes, 1))


def trames_yin(x):
    """Trames de LONGUEUR_YIN échantillons toutes les PAS_YIN, comme dans analyser."""
    return np.lib.stride_tricks.sliding_window_view(np.asarray(x, np.float32), LONGUEUR_YIN)[::PAS_YIN]


class EssaiYinTrames(unittest.TestCase):
    def test_sinus(self):
        t = np.arange(SR // 2) / SR
        f0, rms = analyse.yin_trames(trames_yin(0.3 * np.sin(2 * np.pi * 220 * t)))
        self.assertEqual((f0.dtype, rms.dtype), (np.float32, np.float32))
        self.assertEqual(len(f0), (SR // 2 - LONGUEUR_YIN) // PAS_YIN + 1)
        np.testing.assert_allclose(f0, 220, rtol=0.005)
        np.testing.assert_allclose(rms, 20 * math.log10(0.3 / math.sqrt(2)), atol=0.3)

    def test_une_frequence_par_trame(self):
        # chaque ligne est traitée à part : six hauteurs de 65 à 950 Hz dans le même appel
        t = np.arange(LONGUEUR_YIN) / SR
        frequences = [65, 100, 220, 440, 800, 950]
        f0, _ = analyse.yin_trames(np.stack([0.2 * np.sin(2 * np.pi * f * t + 1) for f in frequences]))
        np.testing.assert_allclose(f0, frequences, rtol=0.01)

    def test_son_riche_sans_erreur_d_octave(self):
        for hz in (98, 150, 196, 440):
            for nom, amplitudes in (("voix", HARMONIQUES), ("fondamentale absente", (0,) + HARMONIQUES[1:]),
                                    ("dent de scie", tuple(1 / k for k in range(1, 15)))):
                with self.subTest(hz=hz, son=nom):
                    f0, _ = analyse.yin_trames(trames_yin(0.05 * signal_riche(hz, 0.2, amplitudes)))
                    np.testing.assert_allclose(f0, hz, rtol=0.01)

    def test_bruit_non_voise(self):
        bruit = np.random.default_rng(7).normal(0, 0.1, (100, LONGUEUR_YIN)).astype(np.float32)
        f0, _ = analyse.yin_trames(bruit)
        self.assertLess((f0 > 0).mean(), 0.05)

    def test_aucune_trame(self):
        f0, rms = analyse.yin_trames(np.zeros((0, LONGUEUR_YIN), np.float32))
        self.assertEqual((len(f0), len(rms)), (0, 0))

    # Bogue corrigé, gardé en non-régression : sur un silence numérique (zéros), la différence cumulée normalisée vaut 0 partout (0 / (0 + 1e-12)) :
    # chaque trame passe sous le seuil dès la plus courte période et sort « voisée » à 1000 Hz (F0_MAX). C'est masqué
    # dans analyser par nettoyer_f0 (niveau < −55 dBFS), mais yin_trames seul ment. Correction proposée : cm = 1 là
    # où la somme cumulée de dt est nulle (convention de YIN), p. ex.
    # « s = np.cumsum(dt[:, 1:], 1) ; cm[:, 1:] = np.where(s > 1e-9, dt[:, 1:] * np.arange(1, tmax + 1) / s, 1) ».
    def test_silence_numerique(self):
        f0, rms = analyse.yin_trames(np.zeros((4, LONGUEUR_YIN), np.float32))
        self.assertTrue((rms < -100).all())
        np.testing.assert_array_equal(f0, 0)


class EssaiNettoyerF0(unittest.TestCase):
    @staticmethod
    def nettoyer(f0, rms=-20.0):
        f0 = np.asarray(f0, np.float32)
        return analyse.nettoyer_f0(f0, np.broadcast_to(np.float32(rms), f0.shape).copy())

    def test_point_isole_retire(self):
        np.testing.assert_array_equal(self.nettoyer([0, 0, 0, 200, 0, 0, 0]), 0)
        np.testing.assert_array_equal(self.nettoyer([0, 0, 200, 200, 0, 0]), [0, 0, 200, 200, 0, 0])

    def test_niveau_trop_faible(self):
        f0 = np.full(30, 200, np.float32)
        rms = np.full(30, -10, np.float32)
        rms[5:9] = -58          # sous −55 dBFS
        rms[15:19] = -52        # plus de 40 dB sous le niveau haut (−10)
        r = analyse.nettoyer_f0(f0, rms)
        np.testing.assert_array_equal(r[5:9], 0)
        np.testing.assert_array_equal(r[15:19], 0)
        np.testing.assert_allclose(r[[0, 10, 20, 29]], 200, rtol=1e-4)

    def test_saut_d_octave_isole_ramene(self):
        f0 = np.full(21, 200, np.float32)
        f0[10], f0[15] = 400, 100
        np.testing.assert_allclose(self.nettoyer(f0), 200, rtol=1e-4)

    def test_vrai_changement_d_octave_garde(self):
        f0 = np.array([200] * 20 + [400] * 20, np.float32)
        np.testing.assert_allclose(self.nettoyer(f0), f0, rtol=1e-4)

    def test_vide(self):
        self.assertEqual(len(analyse.nettoyer_f0(np.zeros(0, np.float32), np.zeros(0, np.float32))), 0)

    # Bogue corrigé, gardé en non-régression : les voisins sont pris avec np.roll, qui boucle : la première et la dernière trame se « voient » l'une
    # l'autre, et deux points isolés aux deux bouts du son sont gardés. Correction proposée : décaler sans boucler,
    # p. ex. « avant = np.r_[False, v[:-1]] ; apres = np.r_[v[1:], False] ; isole = v & ~avant & ~apres ».
    def test_points_isoles_aux_deux_bouts(self):
        np.testing.assert_array_equal(self.nettoyer([200, 0, 0, 0, 0, 0, 0, 200]), 0)


# --- Trameur ---------------------------------------------------------------------------------------------------------

def trames_attendues(x, longueur, pas):
    """La trame k commence à k × pas ; une trame par début avant la fin du son, complétée par du silence."""
    n = -(-len(x) // pas)
    p = np.concatenate([x, np.zeros(n * pas + longueur, np.float32)])
    return np.stack([p[k * pas:k * pas + longueur] for k in range(n)]) if n else np.zeros((0, longueur), np.float32)


def decouper(x, longueur, pas, tailles):
    """Pousse x dans un Trameur par blocs des tailles données, puis le reste avec fin=True."""
    t = analyse.Trameur(longueur, pas)
    sortie, i = [], 0
    for n in tailles:
        sortie.append(np.array(t.pousser(x[i:i + n])))
        i += n
    sortie.append(np.array(t.pousser(x[i:], fin=True)))
    return np.concatenate(sortie)


class EssaiTrameur(unittest.TestCase):
    REGLAGES = {"spectre": (analyse.N_FFT, SR // analyse.FPS_SPECTRE), "onde": (SR // analyse.FPS_ONDE,) * 2,
                "f0": (LONGUEUR_YIN, PAS_YIN), "petit": (7, 3)}

    def setUp(self):
        self.x = np.random.default_rng(3).uniform(-1, 1, 10007).astype(np.float32)

    def test_independant_de_la_taille_des_blocs(self):
        irreguliers = [0, 1, 2, 1023, 1024, 1025, 0, 399, 401, 5, 3000, 17]       # 6897 échantillons, reste en fin
        tout_avant = irreguliers + [len(self.x) - sum(irreguliers)]               # le bloc de fin est vide
        for nom, (longueur, pas) in self.REGLAGES.items():
            with self.subTest(reglage=nom):
                attendu = trames_attendues(self.x, longueur, pas)
                self.assertEqual(len(attendu), math.ceil(len(self.x) / pas))
                un_bloc = decouper(self.x, longueur, pas, [])
                np.testing.assert_array_equal(un_bloc, attendu)
                np.testing.assert_array_equal(decouper(self.x, longueur, pas, irreguliers), attendu)
                np.testing.assert_array_equal(decouper(self.x, longueur, pas, tout_avant), attendu)
                np.testing.assert_array_equal(decouper(self.x, longueur, pas, [1] * 500), attendu)

    def test_fin_completee_par_du_silence(self):
        longueur, pas = self.REGLAGES["spectre"]
        tr = decouper(self.x, longueur, pas, [5000])
        k = len(tr) - 1
        utiles = len(self.x) - k * pas                     # échantillons du son dans la dernière trame
        self.assertGreater(utiles, 0)
        np.testing.assert_array_equal(tr[-1, :utiles], self.x[k * pas:])
        np.testing.assert_array_equal(tr[-1, utiles:], 0)

    def test_son_plus_court_qu_une_trame(self):
        tr = decouper(self.x[:100], 1024, 400, [])
        self.assertEqual(tr.shape, (1, 1024))
        np.testing.assert_array_equal(tr[0, :100], self.x[:100])
        np.testing.assert_array_equal(tr[0, 100:], 0)

    def test_flux_vide(self):
        t = analyse.Trameur(1024, 400)
        self.assertEqual(t.pousser(np.zeros(0, np.float32)).shape, (0, 1024))
        self.assertEqual(t.pousser(np.zeros(0, np.float32), fin=True).shape, (0, 1024))

    # Bogue corrigé (non atteint par analyser, où le pas ne dépasse jamais la longueur) : avec pas > longueur, les
    # échantillons entre la fin du bloc et le début de la trame suivante ne sont pas sautés au bloc suivant
    # (« self.tampon = b[n * pas:] » est vide mais la position est perdue) : les trames dépendent alors de la taille
    # des blocs. Correction proposée : garder un compte d'échantillons à sauter (n × pas − len(b)) et le retirer du
    # début du bloc suivant, ou refuser pas > longueur dans __init__.
    def test_pas_plus_grand_que_la_trame(self):
        x = np.arange(20, dtype=np.float32)
        np.testing.assert_array_equal(decouper(x, 2, 5, [4, 3, 5]), trames_attendues(x, 2, 5))


# --- F0 sur des sons synthétiques (ffmpeg) ---------------------------------------------------------------------------

@unittest.skipUnless(FFMPEG, "ffmpeg ou ffprobe absent")
class EssaiF0Synthetique(unittest.TestCase):
    def f0(self, nom):
        return analyse_de(nom, ("f0",))["f0"]

    def test_sinus_220(self):
        f0 = self.f0("sinus_220")
        v = voisees(f0)
        self.assertGreater(len(v) / len(f0), 0.9)
        self.assertAlmostEqual(np.median(v) / 220, 1, delta=0.01)
        self.assertGreater((np.abs(v / 220 - 1) < 0.02).mean(), 0.95)

    def test_autres_hauteurs(self):
        for nom, hz in (("sinus_98", 98), ("sinus_659", 659.3)):
            with self.subTest(hz=hz):
                v = voisees(self.f0(nom))
                self.assertGreater(len(v), 100)
                self.assertAlmostEqual(np.median(v) / hz, 1, delta=0.01)

    def test_glissade_deux_paliers(self):
        f0 = self.f0("glissade")
        t = np.arange(len(f0)) * analyse.PAS_F0_S
        bas, haut = voisees(f0[(t > 0.1) & (t < 1.4)]), voisees(f0[(t > 1.6) & (t < 2.9)])
        self.assertAlmostEqual(np.median(bas) / 220, 1, delta=0.01)
        self.assertAlmostEqual(np.median(haut) / 330, 1, delta=0.01)
        self.assertGreater((np.abs(bas / 220 - 1) < 0.02).mean(), 0.95)
        self.assertGreater((np.abs(haut / 330 - 1) < 0.02).mean(), 0.95)
        # le changement tombe à 1,5 s (trame 75), à deux trames près
        premiere_haute = int(np.argmax(f0 > 275))
        self.assertAlmostEqual(premiere_haute, 75, delta=2)

    def test_silence(self):
        f0 = self.f0("silence")
        self.assertEqual(len(f0), 150)
        np.testing.assert_array_equal(f0, 0)

    def test_bruit_faible(self):
        f0 = self.f0("bruit")
        self.assertLess((f0 > 0).mean(), 0.05)

    def test_voix_chantee(self):
        f0 = self.f0("chant")
        v = voisees(f0)
        self.assertGreater(len(v) / len(f0), 0.9)
        self.assertAlmostEqual(np.median(v) / 196, 1, delta=0.01)
        # vibrato de ±1,5 % : tout reste à moins de 3 % de sol2, aucune trame à l'octave (98 ou 392 Hz) ni à la quinte
        self.assertGreater((np.abs(v / 196 - 1) < 0.03).mean(), 0.97)
        self.assertEqual(int(((v < 196 / 1.3) | (v > 196 * 1.3)).sum()), 0)

    def test_longueur_des_trames(self):
        for nom, duree in (("sinus_220", 3.0), ("silence", 3.0), ("court", 2.345)):
            with self.subTest(son=nom):
                r = analyse_de(nom, ("f0",))
                self.assertAlmostEqual(r["duree_s"], duree, delta=0.005)
                self.assertEqual(len(r["f0"]), round(r["duree_s"] / 0.02))
                self.assertAlmostEqual(len(r["f0"]), duree / 0.02, delta=1)
                self.assertEqual(r["f0_pas"], 0.02)
                self.assertEqual(r["f0"].dtype, np.float32)


# --- analyser : spectrogramme, bandes, niveau, onde ------------------------------------------------------------------

@unittest.skipUnless(FFMPEG, "ffmpeg ou ffprobe absent")
class EssaiAnalyser(unittest.TestCase):
    PRODUITS = ("spectre", "onde", "bandes")

    def test_formes(self):
        r = analyse_de("la_1k", self.PRODUITS)
        duree = r["duree_s"]
        self.assertAlmostEqual(duree, 2.0, delta=0.005)
        n = round(duree * SR)
        self.assertEqual(r["spectre"].shape, (160, math.ceil(n / (SR // 40))))
        self.assertAlmostEqual(r["spectre"].shape[1], duree * 40, delta=1)
        self.assertEqual(r["spectre"].dtype, np.uint8)
        self.assertEqual(r["bandes"].shape, (r["spectre"].shape[1] // 2, 24))
        self.assertAlmostEqual(r["bandes"].shape[0], duree * 20, delta=1)
        self.assertEqual(r["bandes"].dtype, np.uint8)
        self.assertEqual(r["niveau"].shape, (r["bandes"].shape[0],))
        self.assertAlmostEqual(r["onde"].shape[0], duree * 100, delta=1)
        self.assertEqual(r["onde"].shape[1], 2)
        self.assertEqual(r["onde"].dtype, np.int8)
        self.assertEqual((r["spectre_fps"], r["bandes_fps"], r["onde_fps"]), (40, 20, 100))
        np.testing.assert_array_equal(r["spectre_f"], [50, 8000])
        self.assertNotIn("f0", r)

    def test_produits_demandes_seulement(self):
        r = analyse_de("la_1k", ("onde",))
        self.assertEqual(set(r), {"duree_s", "onde", "onde_fps"})

    def test_ligne_du_spectrogramme(self):
        spectre = analyse_de("la_1k", self.PRODUITS)["spectre"]
        profil = np.median(spectre[:, 5:-5], axis=1)
        # ligne de 1 kHz sur l'échelle logarithmique 50 Hz – 8 kHz, les aigus en haut (ligne 0 = 8 kHz)
        ligne = math.floor(160 * math.log(1000 / 50) / math.log(8000 / 50))
        self.assertEqual(int(np.argmax(profil)), 159 - ligne)
        for hz in (250, 4000):      # deux octaves plus bas ou plus haut : au moins 20 dB plus sombre
            autre = 159 - math.floor(160 * math.log(hz / 50) / math.log(8000 / 50))
            self.assertGreater(int(profil[159 - ligne]) - int(profil[autre]), 20 / 90 * 255, hz)

    def test_bande_du_vu_metre(self):
        bandes = analyse_de("la_1k", self.PRODUITS)["bandes"]
        profil = np.median(bandes[3:-3], axis=0)
        self.assertEqual(int(np.argmax(profil)), math.floor(24 * math.log(1000 / 60) / math.log(8000 / 60)))

    def test_niveau_et_onde(self):
        faible = analyse_de("la_1k", self.PRODUITS)
        fort = analyse_de("la_1k_fort", self.PRODUITS)
        n_faible, n_fort = np.median(faible["niveau"][3:-3]), np.median(fort["niveau"][3:-3])
        # sinus d'amplitude 1/8 : −18 dBFS ; deux fois plus fort : −12 dBFS
        self.assertAlmostEqual(n_faible, int(analyse.en_octets(20 * math.log10(1 / 8))), delta=2)
        self.assertAlmostEqual(n_fort, int(analyse.en_octets(20 * math.log10(1 / 4))), delta=2)
        self.assertGreater(n_fort, n_faible + 10)
        self.assertAlmostEqual(np.median(faible["onde"][5:-5, 1]), 16, delta=1)     # 127 / 8
        self.assertAlmostEqual(np.median(faible["onde"][5:-5, 0]), -16, delta=1)
        self.assertAlmostEqual(np.median(fort["onde"][5:-5, 1]), 32, delta=1)

    def test_independant_des_blocs_de_decodage(self):
        # les mêmes échantillons en un bloc ou en blocs irréguliers donnent exactement les mêmes produits
        x = analyse.decoder(son("chant"))
        coupes = [0, 1, 7, 333, 1000, 4096, 4096, 4097, 16000, 20011, len(x)]     # dont un bloc vide

        def un_bloc(fichier, *a, **k):
            yield x

        def irreguliers(fichier, *a, **k):
            for debut, fin in zip(coupes, coupes[1:]):
                yield x[debut:fin]
        with mock.patch.object(analyse, "decoder_blocs", un_bloc):
            a = analyse.analyser("inutile.wav", analyse.PRODUITS, ecrire_cache=False)
        with mock.patch.object(analyse, "decoder_blocs", irreguliers):
            b = analyse.analyser("inutile.wav", analyse.PRODUITS, ecrire_cache=False)
        self.assertEqual(a["duree_s"], b["duree_s"])
        for k in ("spectre", "bandes", "niveau", "onde", "f0"):
            with self.subTest(produit=k):
                self.assertEqual(a[k].shape, b[k].shape)
                np.testing.assert_allclose(a[k].astype(np.float64), b[k].astype(np.float64), atol=1e-4)

    def test_decodage(self):
        f = son("sinus_220")
        x = analyse.decoder(f)
        self.assertEqual(x.dtype, np.float32)
        self.assertAlmostEqual(len(x) / SR, 3.0, delta=0.005)
        blocs = list(analyse.decoder_blocs(f, bloc_s=1))
        self.assertTrue(all(len(b) == SR for b in blocs[:-1]))
        np.testing.assert_array_equal(np.concatenate(blocs), x)
        self.assertAlmostEqual(analyse.duree_audio(f), 3.0, delta=0.01)

    def test_fichier_illisible(self):
        with tempfile.TemporaryDirectory(prefix="voixla-essai-") as d:
            texte = Path(d) / "pas-un-son.wav"
            texte.write_text("ceci n'est pas un son", encoding="utf-8")
            for f in (Path(d) / "absent.wav", texte):
                with self.subTest(fichier=f.name):
                    with self.assertRaises(analyse.ErreurAnalyse):
                        analyse.analyser(f, ("onde",), ecrire_cache=False)
            self.assertIsNone(analyse.duree_audio(Path(d) / "absent.wav"))


# --- Caches des pistes -----------------------------------------------------------------------------------------------

def interdit(*a, **k):
    raise AssertionError("decoder_blocs appelé : le cache aurait dû suffire")


@unittest.skipUnless(FFMPEG, "ffmpeg ou ffprobe absent")
class EssaiCaches(EssaiAvecCache):
    PRODUITS = ("spectre", "onde", "bandes")

    def setUp(self):
        super().setUp()
        self.piste = self.tmp / "piste.wav"
        shutil.copy(son("la_1k"), self.piste)

    def test_dossier_du_cache(self):
        self.assertEqual(chemins.dossier_cache(), self.cache)
        c = analyse.chemin_cache(self.piste, "spectre")
        self.assertEqual(c.parent, self.cache / "pistes")
        self.assertTrue(c.name.endswith("-spectre.npz"))
        self.assertNotEqual(c, analyse.chemin_cache(self.piste, "onde"))
        self.assertEqual(c, analyse.chemin_cache(str(self.piste), "spectre"))

    def test_deuxieme_lecture_sans_recalcul(self):
        self.assertIsNone(analyse.en_cache(self.piste, self.PRODUITS))
        r = analyse.analyser(self.piste, self.PRODUITS)
        with mock.patch.object(analyse, "decoder_blocs", interdit):
            for _ in range(2):
                c = analyse.en_cache(self.piste, self.PRODUITS)
                self.assertIsNotNone(c)
                for k in ("spectre", "bandes", "niveau", "onde", "spectre_f"):
                    np.testing.assert_array_equal(c[k], r[k], k)
                    self.assertEqual(c[k].dtype, r[k].dtype, k)
                self.assertIsInstance(c["duree_s"], float)
                self.assertAlmostEqual(c["duree_s"], r["duree_s"])
                self.assertEqual((c["spectre_fps"], c["bandes_fps"], c["onde_fps"]), (40.0, 20.0, 100.0))
            # la ligne de commande ne recalcule pas non plus ce qui est en cache
            with contextlib.redirect_stdout(io.StringIO()) as sortie:
                self.assertEqual(analyse.main(["pistes", str(self.piste)]), 0)
        self.assertEqual(json.loads(sortie.getvalue())["pistes"], [str(self.piste)])

    def test_fichier_modifie_contenu(self):
        analyse.analyser(self.piste, self.PRODUITS)
        avant = analyse.chemin_cache(self.piste, "spectre")
        st = os.stat(self.piste)
        shutil.copy(son("sinus_220"), self.piste)          # autre contenu, autre taille…
        os.utime(self.piste, ns=(st.st_atime_ns, st.st_mtime_ns))           # … même date qu'avant
        self.assertNotEqual(os.stat(self.piste).st_size, st.st_size)
        self.assertNotEqual(analyse.chemin_cache(self.piste, "spectre"), avant)
        self.assertIsNone(analyse.en_cache(self.piste, self.PRODUITS))
        self.assertEqual(analyse.produits_manquants(self.piste, self.PRODUITS), list(self.PRODUITS))
        r = analyse.analyser(self.piste, self.PRODUITS)                      # réanalysé : 3 s au lieu de 2
        self.assertAlmostEqual(analyse.en_cache(self.piste, self.PRODUITS)["duree_s"], r["duree_s"])
        self.assertAlmostEqual(r["duree_s"], 3.0, delta=0.005)

    def test_fichier_modifie_date_seulement(self):
        analyse.analyser(self.piste, ("onde",))
        st = os.stat(self.piste)
        os.utime(self.piste, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        self.assertIsNone(analyse.charger_produit(self.piste, "onde"))
        os.utime(self.piste, ns=(st.st_atime_ns, st.st_mtime_ns))          # date d'origine : la clé revient
        self.assertIsNotNone(analyse.charger_produit(self.piste, "onde"))

    def test_produits_manquants(self):
        tous = list(self.PRODUITS)
        self.assertEqual(analyse.produits_manquants(self.piste, tous), tous)
        analyse.analyser(self.piste, ("spectre",))
        self.assertEqual(analyse.produits_manquants(self.piste, tous), ["onde", "bandes"])
        self.assertIsNone(analyse.en_cache(self.piste, tous))
        self.assertIsNotNone(analyse.en_cache(self.piste, ["spectre"]))
        analyse.analyser(self.piste, ("onde", "bandes"))
        self.assertEqual(analyse.produits_manquants(self.piste, tous), [])
        self.assertTrue({"spectre", "onde", "bandes", "niveau"} <= set(analyse.en_cache(self.piste, tous)))
        absent = self.tmp / "absent.wav"
        self.assertEqual(analyse.produits_manquants(absent, tous), tous)
        self.assertIsNone(analyse.en_cache(absent, tous))

    def test_produit_f0_en_cache(self):
        r = analyse.analyser(self.piste, ("f0",))
        c = analyse.charger_produit(self.piste, "f0")
        np.testing.assert_array_equal(c["f0"], r["f0"])
        self.assertEqual(c["f0_pas"], 0.02)

    def test_ecriture_atomique(self):
        analyse.analyser(self.piste, self.PRODUITS)
        noms = self.fichiers_cache()
        self.assertEqual(len(noms), 3)
        self.assertTrue(all(n.endswith(".npz") and ".tmp" not in n for n in noms), noms)
        self.assertEqual({n.rsplit("-", 1)[1] for n in noms}, {"spectre.npz", "onde.npz", "bandes.npz"})
        # une seconde écriture remplace le fichier sans laisser de fichier temporaire
        analyse.analyser(self.piste, ("onde",))
        self.assertEqual(self.fichiers_cache(), noms)

    def test_cache_illisible(self):
        c = analyse.chemin_cache(self.piste, "onde")
        c.parent.mkdir(parents=True)
        c.write_text("pas un npz", encoding="utf-8")
        self.assertIsNone(analyse.charger_produit(self.piste, "onde"))
        self.assertEqual(analyse.produits_manquants(self.piste, ["onde"]), ["onde"])

    # Bogue corrigé, gardé en non-régression : un .npz tronqué ou abîmé dans le cache (disque plein, copie interrompue, cache restauré…) fait lever
    # zipfile.BadZipFile (qui n'est ni OSError ni ValueError) au lieu de rendre None : la visualisation plante au lieu
    # de recalculer. Correction proposée : dans charger_produit, attraper aussi zipfile.BadZipFile, zlib.error,
    # EOFError et KeyError (ou Exception), et effacer le fichier abîmé.
    def test_cache_tronque(self):
        analyse.analyser(self.piste, ("onde",))
        c = analyse.chemin_cache(self.piste, "onde")
        brut = c.read_bytes()
        c.write_bytes(brut[:len(brut) // 2])
        # quand np.load lève BadZipFile, numpy laisse le fichier ouvert : on le referme ici sans avertissement
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            try:
                r, erreur = analyse.charger_produit(self.piste, "onde"), None
            except Exception as e:      # noqa: BLE001
                r, erreur = "exception", type(e).__name__
            gc.collect()
        self.assertIsNone(erreur, f"charger_produit a levé {erreur}")
        self.assertIsNone(r)


# --- Courbes de mélodie : courbes_pour et repli local ----------------------------------------------------------------

COURBES_DOUBLER = {"format": 1, "pas_s": 0.02, "source_hz": [0, 220.0, 0], "convertie_hz": [0, 110.0, 0],
                   "decalage_dt": [0, -12, 0]}


def sig(f):
    st = os.stat(f)
    return [st.st_size, st.st_mtime_ns]


class EssaiCourbesPour(EssaiIsole):
    """courbes_pour ne lit que les dates et tailles des sons : de faux fichiers suffisent ici."""

    def setUp(self):
        super().setUp()
        self.travail = self.tmp / "sortie" / "travail"
        self.voix = self.travail / "jules"
        self.voix.mkdir(parents=True)

    def sources(self, nom_source="voix_source.wav"):
        (self.travail / nom_source).write_bytes(b"source")
        (self.voix / "voix_convertie.flac").write_bytes(b"convertie")

    def locales(self, **autres):
        d = dict(COURBES_DOUBLER, source_hz=[0, 221.0, 0], origine="interface (YIN local, 16 kHz)",
                 sources={"voix_source": sig(analyse.fichier_voix_source(self.travail)),
                          "voix_convertie": sig(self.voix / "voix_convertie.flac")})
        d.update(autres)
        chemins.ecrire_json(self.voix / "courbes-locales.json", d)

    def test_fichiers_absents(self):
        c, raison = analyse.courbes_pour(self.voix)
        self.assertIsNone(c)
        self.assertNotEqual(raison, "a_calculer")
        self.assertIn("absente", raison)
        (self.travail / "voix_source.wav").write_bytes(b"source")          # convertie toujours absente
        self.assertEqual(analyse.courbes_pour(self.voix), (None, raison))
        (self.travail / "voix_source.wav").unlink()
        (self.voix / "voix_convertie.flac").write_bytes(b"convertie")       # source absente
        self.assertEqual(analyse.courbes_pour(self.voix), (None, raison))

    def test_a_calculer(self):
        self.sources()
        self.assertEqual(analyse.courbes_pour(self.voix), (None, "a_calculer"))
        self.sources("voix_source.flac")
        (self.travail / "voix_source.wav").unlink()
        self.assertEqual(analyse.courbes_pour(self.voix), (None, "a_calculer"))

    def test_courbes_json_prioritaire(self):
        self.sources()
        self.locales()
        chemins.ecrire_json(self.voix / "courbes.json", COURBES_DOUBLER)
        c, raison = analyse.courbes_pour(self.voix)
        self.assertIsNone(raison)
        self.assertEqual(c["origine"], "doubler")
        self.assertEqual(c["source"][1], 220.0)
        # sans sons dans travail/, courbes.json suffit
        shutil.rmtree(self.voix.parent / "jules")
        self.voix.mkdir()
        chemins.ecrire_json(self.voix / "courbes.json", COURBES_DOUBLER)
        self.assertIsNone(analyse.courbes_pour(self.voix)[1])

    def test_courbes_json_illisible_repli_local(self):
        self.sources()
        self.locales()
        chemins.ecrire_json(self.voix / "courbes.json", dict(COURBES_DOUBLER, format=2))
        c, raison = analyse.courbes_pour(self.voix)
        self.assertIsNone(raison)
        self.assertEqual(c["origine"], "interface (YIN local, 16 kHz)")

    def test_courbes_locales_a_jour(self):
        self.sources()
        self.locales()
        c, raison = analyse.courbes_pour(self.voix)
        self.assertIsNone(raison)
        self.assertEqual(c["source"][1], 221.0)
        self.assertEqual(c["origine"], "interface (YIN local, 16 kHz)")

    def test_courbes_locales_perimees(self):
        self.sources()
        self.locales()
        conv = self.voix / "voix_convertie.flac"
        conv.write_bytes(b"voix convertie refaite")                         # autre taille
        self.assertEqual(analyse.courbes_pour(self.voix), (None, "a_calculer"))
        self.locales()
        st = os.stat(conv)
        os.utime(conv, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))         # même taille, autre date
        self.assertEqual(analyse.courbes_pour(self.voix), (None, "a_calculer"))
        self.locales()
        source = self.travail / "voix_source.wav"
        st = os.stat(source)
        os.utime(source, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))      # voix isolée refaite
        self.assertEqual(analyse.courbes_pour(self.voix), (None, "a_calculer"))

    def test_courbes_locales_sans_signature(self):
        self.sources()
        self.locales(sources=None)
        self.assertEqual(analyse.courbes_pour(self.voix), (None, "a_calculer"))
        self.locales(format=2)
        self.assertEqual(analyse.courbes_pour(self.voix), (None, "a_calculer"))


def faux_travail(racine, rapport=None):
    """Un faux travail/ : voix_source.wav (sinus 220 Hz), jules/voix_convertie.flac (sinus 110 Hz, une octave plus
    bas) et jules/rapport.json (un morceau de 3 s décalé de −12 demi-tons). Rend le dossier de la voix."""
    travail = Path(racine) / "sortie" / "travail"
    voix = travail / "jules"
    voix.mkdir(parents=True)
    shutil.copy(son("source_220"), travail / "voix_source.wav")
    shutil.copy(son("convertie_110"), voix / "voix_convertie.flac")
    if rapport is None:
        rapport = {"mode": "chant", "modele": "chant44k",
                   "morceaux": [{"i": 0, "debut_s": 0.0, "fin_s": 3.0, "decalage": -12}]}
    chemins.ecrire_json(voix / "rapport.json", rapport)
    return voix


@unittest.skipUnless(FFMPEG, "ffmpeg ou ffprobe absent")
class EssaiCourbesLocales(EssaiAvecCache):
    def test_de_bout_en_bout(self):
        voix = faux_travail(self.tmp)
        self.assertEqual(analyse.courbes_pour(voix), (None, "a_calculer"))
        c = analyse.calculer_courbes_locales(voix)
        self.assertEqual(len(c["source"]), 150)
        self.assertEqual(c["pas_s"], 0.02)
        np.testing.assert_array_equal(c["decalage"], -12)
        self.assertAlmostEqual(np.median(voisees(c["source"])) / 220, 1, delta=0.01)
        self.assertAlmostEqual(np.median(voisees(c["convertie"])) / 110, 1, delta=0.01)
        # la consigne (source décalée de −12) retombe sur la voix convertie
        consigne = analyse.consigne(c["source"], c["decalage"])
        deux = (consigne > 0) & (c["convertie"] > 0)
        self.assertGreater(deux.mean(), 0.9)
        self.assertGreater((np.abs(consigne[deux] / c["convertie"][deux] - 1) < 0.02).mean(), 0.95)
        # fichier écrit d'un coup (pas de fichier temporaire), au format de courbes.json, relu par courbes_pour
        f = voix / "courbes-locales.json"
        d = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual((d["format"], d["pas_s"]), (1, 0.02))
        self.assertEqual(len(d["source_hz"]), len(d["convertie_hz"]))
        self.assertEqual(d["sources"], {"voix_source": sig(voix.parent / "voix_source.wav"),
                                        "voix_convertie": sig(voix / "voix_convertie.flac")})
        self.assertEqual(sorted(p.name for p in voix.iterdir()),
                         ["courbes-locales.json", "rapport.json", "voix_convertie.flac"])
        relu, raison = analyse.courbes_pour(voix)
        self.assertIsNone(raison)
        self.assertEqual(relu["origine"], "interface (YIN local, 16 kHz)")
        for k in ("source", "convertie", "decalage"):
            np.testing.assert_allclose(relu[k], c[k], atol=0.051)          # arrondi à 0,1 Hz dans le fichier
        # rien dans le cache des pistes : le repli n'y écrit pas
        self.assertEqual(self.fichiers_cache(), [])
        # voix convertie refaite : à recalculer
        conv = voix / "voix_convertie.flac"
        st = os.stat(conv)
        os.utime(conv, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        self.assertEqual(analyse.courbes_pour(voix), (None, "a_calculer"))

    def test_rapport_donne_parole22k(self):
        voix = faux_travail(self.tmp)
        c = analyse.calculer_courbes_locales(voix, rapport={"mode": "parole", "modele": "parole22k",
                                                            "morceaux": [{"debut_s": 0, "fin_s": 3, "decalage": -12}]})
        np.testing.assert_array_equal(c["decalage"], 0)

    def test_sources_absentes(self):
        voix = self.tmp / "sortie" / "travail" / "jules"
        voix.mkdir(parents=True)
        with self.assertRaises(analyse.ErreurAnalyse):
            analyse.calculer_courbes_locales(voix)
        self.assertFalse((voix / "courbes-locales.json").exists())


# --- Ligne de commande -----------------------------------------------------------------------------------------------

@unittest.skipUnless(FFMPEG, "ffmpeg ou ffprobe absent")
class EssaiLigneDeCommande(EssaiAvecCache):
    def lancer(self, *arguments):
        """python3 -m voixla.analyse … dans un sous-processus (sous nice), avec PYTHONPATH=interface et les variables
        d'essai (cache temporaire) ; rend (code, dernière ligne JSON ou None, sortie d'erreur)."""
        env = dict(os.environ, PYTHONPATH=str(INTERFACE))
        r = subprocess.run(["nice", "-n", "10", sys.executable, "-m", "voixla.analyse", *map(str, arguments)],
                           cwd=self.tmp, env=env, capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
        lignes = r.stdout.strip().splitlines()
        try:
            dernier = json.loads(lignes[-1]) if lignes else None
        except ValueError:
            dernier = None
        return r.returncode, dernier, r.stderr

    def test_pistes(self):
        piste = self.tmp / "piste.wav"
        shutil.copy(son("la_1k"), piste)
        code, sortie, erreur = self.lancer("pistes", piste, "--produits", "spectre,onde,inconnu")
        self.assertEqual(code, 0, erreur)
        self.assertEqual(sortie["pistes"], [str(piste)])
        self.assertEqual(sortie["produits"], ["spectre", "onde"])
        self.assertEqual(len(self.fichiers_cache()), 2)
        # même clé de cache d'un processus à l'autre
        with mock.patch.object(analyse, "decoder_blocs", interdit):
            self.assertIsNotNone(analyse.en_cache(piste, ("spectre", "onde")))
        self.assertEqual(analyse.produits_manquants(piste, ("spectre", "onde", "bandes")), ["bandes"])
        # produits par défaut : seules les bandes manquaient
        code, sortie, erreur = self.lancer("pistes", piste)
        self.assertEqual(code, 0, erreur)
        self.assertEqual(sortie["produits"], ["spectre", "onde", "bandes"])
        self.assertEqual(len(self.fichiers_cache()), 3)

    def test_pistes_illisible(self):
        code, _, erreur = self.lancer("pistes", self.tmp / "absent.wav")
        self.assertNotEqual(code, 0)
        self.assertIn("son illisible", erreur)

    def test_courbes(self):
        voix = faux_travail(self.tmp)
        code, sortie, erreur = self.lancer("courbes", voix)
        self.assertEqual(code, 0, erreur)
        self.assertEqual(sortie["courbes"], str(voix))
        self.assertEqual(sortie["trames"], 150)
        c, raison = analyse.courbes_pour(voix)
        self.assertIsNone(raison)
        self.assertEqual(len(c["source"]), 150)

    def test_sans_commande(self):
        for argv in ([], ["autre"]):
            with self.subTest(argv=argv):
                with contextlib.redirect_stdout(io.StringIO()) as sortie:
                    self.assertEqual(analyse.main(argv), 2)
                self.assertIn("python3 -m voixla.analyse pistes", sortie.getvalue())

    # Bogue corrigé, gardé en non-régression : « courbes » sans dossier et « --produits » sans valeur font lever IndexError (trace Python, code 1)
    # au lieu d'afficher l'aide avec le code 2. Correction proposée : dans main, vérifier len(argv) ≥ 2 pour
    # « courbes » et la présence d'une valeur après « --produits », sinon print(__doc__) et return 2.
    def test_courbes_sans_dossier(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(analyse.main(["courbes"]), 2)

    def test_produits_sans_valeur(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(analyse.main(["pistes", "--produits"]), 2)


class EssaiPurgeCache(EssaiAvecCache):
    """Le cache des pistes reste sous sa taille maximale : les analyses les moins récemment utilisées partent."""

    def test_purge_lru(self):
        d = self.cache / "pistes"
        d.mkdir(parents=True)
        maintenant = time.time()
        for i in range(5):                     # 5 fichiers de 1 Mo, du plus ancien (0) au plus récent (4)
            f = d / f"{i:024x}-spectre.npz"
            f.write_bytes(b"x" * 1024 * 1024)
            os.utime(f, (maintenant - 1000 + i * 100, maintenant - 1000 + i * 100))
        vieux_tmp = d / "abc.npz.123.tmp.npz"
        vieux_tmp.write_bytes(b"x")
        os.utime(vieux_tmp, (maintenant - 7200, maintenant - 7200))
        self.assertEqual(analyse.purger_cache(max_mo=3), 3)       # 2 plus anciens + le .tmp abandonné
        self.assertEqual(self.fichiers_cache(), [f"{i:024x}-spectre.npz" for i in (2, 3, 4)])
        self.assertEqual(analyse.purger_cache(max_mo=3), 0)

    def test_purge_sans_cache(self):
        self.assertEqual(analyse.purger_cache(), 0)


if __name__ == "__main__":
    unittest.main()
