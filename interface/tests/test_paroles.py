"""Essais de voixla.paroles : lecture de paroles.json (cas abîmés compris), couches et langues, lecture des SRT,
sous-titres de petites vidéos fabriquées par ffmpeg dans un dossier temporaire (extraction, cache dans VOIXLA_CACHE,
choix de la couche originale et de la traduction), repli de paroles_pour, synchronisation (ligne en cours, ligne
suivante, mot en cours, ligne correspondante, fragments) et ligne de commande. Paroles d'essai inventées ; rien n'est
écrit hors des dossiers temporaires."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from voixla import chemins, paroles
from voixla.paroles import Couche, Ligne, Mot, Paroles

from .commun import VRAI_DOUBLER, EssaiIsole
from .test_pistes import charger_doubler
from .test_pistes_video import SR, debut_mesure, nice_ffmpeg, video_de_synthese

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
INTERFACE = Path(__file__).resolve().parents[1]

SRT_ES = "1\n00:00:00,500 --> 00:00:02,000\nUna canción de prueba\n\n2\n00:00:02,500 --> 00:00:04,000\nPara jules\n"
SRT_FR = "1\n00:00:00,500 --> 00:00:02,000\nUne chanson d'essai\n\n2\n00:00:02,500 --> 00:00:04,000\nPour jules\n"


# --- Petites vidéos d'essai (ffmpeg) ---------------------------------------------------------------------------------

_DOSSIER = []
_VIDEOS = {}


def _ffmpeg(sortie, srts=(), options=()):
    """Vidéo noire de 6 s (64×36, son muet), avec un flux de sous-titres par SRT donné."""
    d = Path(_DOSSIER[0].name)
    commande = ["nice", "-n", "10", "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi",
                "-i", "color=c=black:s=64x36:d=6", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono"]
    cartes = ["-map", "0:v", "-map", "1:a"]
    for i, texte in enumerate(srts):
        f = d / f"{sortie.stem}-{i}.srt"
        f.write_text(texte, encoding="utf-8")
        commande += ["-i", str(f)]
        cartes += ["-map", str(i + 2)]
    commande += [*cartes, "-t", "6", "-c:v", "mpeg4", "-c:a", "aac", *options, str(sortie)]
    subprocess.run(commande, check=True, timeout=60, stdin=subprocess.DEVNULL, capture_output=True)
    return sortie


FABRIQUES = {
    # deux flux subrip : espagnol (par défaut) et français
    "deux.mkv": lambda f: _ffmpeg(f, (SRT_ES, SRT_FR), ("-c:s", "srt", "-metadata:s:s:0", "language=spa",
                                                         "-metadata:s:s:1", "language=fra",
                                                         "-metadata:s:s:1", "title=Traduction",
                                                         "-disposition:s:0", "default", "-disposition:s:1", "0")),
    # un flux mov_text en français
    "fra.mp4": lambda f: _ffmpeg(f, (SRT_FR,), ("-c:s", "mov_text", "-metadata:s:s:0", "language=fra")),
    "sans.mp4": lambda f: _ffmpeg(f),
}


def video(nom):
    """Chemin d'une vidéo d'essai, fabriquée au premier besoin dans un dossier temporaire du module (à ne pas
    modifier : la copier d'abord)."""
    if nom not in _VIDEOS:
        if not _DOSSIER:
            _DOSSIER.append(tempfile.TemporaryDirectory(prefix="voixla-paroles-videos-"))
        _VIDEOS[nom] = FABRIQUES[nom](Path(_DOSSIER[0].name) / nom)
    return _VIDEOS[nom]


def tearDownModule():
    for d in _DOSSIER:
        d.cleanup()


def segment(debut, fin, texte, mots=None):
    s = {"debut": debut, "fin": fin, "texte": texte}
    if mots is not None:
        s["mots"] = [{"debut": a, "fin": b, "mot": m} for a, b, m in mots]
    return s


PAROLES_VALIDES = {
    "format": 1, "langue": "es", "source": "whisper-large-v3",
    "segments": [
        segment(64.5, 66.0, " Una canción de prueba", [(64.5, 64.8, " Una"), (64.8, 65.3, " canción"),
                                                       (65.3, 65.5, " de"), (65.5, 66.0, " prueba")]),
        segment(67.0, 69.0, "Para jules, otra vez", [(67.0, 67.4, "Para"), (67.4, 68.0, "jules,"),
                                                     (68.0, 68.5, "otra"), (68.5, 69.0, "vez")]),
    ],
    "traductions": {"fr": {"source": "modele-de-traduction", "segments": [
        segment(64.5, 66.0, "Une chanson d'essai"), segment(67.0, 69.0, "Pour jules, encore une fois")]}},
}


class EssaiAvecCache(EssaiIsole):
    """EssaiIsole, plus le cache dans le dossier temporaire (VOIXLA_CACHE, et XDG_CACHE_HOME par prudence)."""

    def setUp(self):
        super().setUp()
        self.cache = self.tmp / "cache"
        os.environ["VOIXLA_CACHE"] = str(self.cache)
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "xdg-cache")
        self.dossier_voix = self.tmp / "sorties" / "Chanson fictive" / "travail" / "jules"
        self.dossier_voix.mkdir(parents=True)

    def ecrire_paroles(self, contenu):
        f = self.dossier_voix / "paroles.json"
        f.write_text(contenu if isinstance(contenu, str) else json.dumps(contenu, ensure_ascii=False),
                     encoding="utf-8")
        return f

    def fausse_video(self, nom="Chanson fictive - voix Jules.mp4", contenu=b"pas une vraie video"):
        f = self.tmp / "sorties" / "Chanson fictive" / nom
        f.write_bytes(contenu)
        return f

    def ecrire_index(self, video, flux, srts=None, **autres):
        """Index de cache fabriqué pour « video » (aucun ffmpeg) : flux = dicts partiels, srts = {n: texte} ;
        autres : champs de plus dans l'index (decalage_s…)."""
        cle = paroles.cle_video(video)
        d = paroles.dossier_cache()
        d.mkdir(parents=True, exist_ok=True)
        complets = []
        for i, f in enumerate(flux):
            g = {"n": i, "index": i + 2, "codec": "subrip", "langue": None, "titre": "", "defaut": False,
                 "force": False, "texte": True, "fichier": None, **f}
            if (srts or {}).get(g["n"]) is not None:
                g["fichier"] = f"{cle}-{g['n']}.srt"
                (d / g["fichier"]).write_text(srts[g["n"]], encoding="utf-8")
            complets.append(g)
        (d / f"{cle}.json").write_text(json.dumps({"format": 1, "video": str(video), "flux": complets, **autres}),
                                       encoding="utf-8")
        return d / f"{cle}.json"


# --- paroles.json ----------------------------------------------------------------------------------------------------

class EssaiLireParoles(EssaiAvecCache):
    def lire(self, contenu):
        return paroles.lire_paroles(self.ecrire_paroles(contenu))

    def un_segment(self, *segments):
        """Paroles faites d'un segment valide (10 → 11 s, « ligne valide ») et des segments donnés."""
        return {"format": 1, "segments": [segment(10.0, 11.0, "ligne valide"), *segments]}

    def test_valide_avec_mots_et_traduction(self):
        p = self.lire(PAROLES_VALIDES)
        self.assertEqual(p.origine, "paroles.json")
        self.assertEqual((p.originale.langue, p.originale.source, p.originale.titre), ("es", "whisper-large-v3", ""))
        self.assertEqual([(l.debut, l.fin) for l in p.originale.lignes], [(64.5, 66.0), (67.0, 69.0)])
        self.assertEqual(p.originale.lignes[0].texte, "Una canción de prueba")
        self.assertEqual([m.mot for m in p.originale.lignes[0].mots], ["Una", "canción", "de", "prueba"])
        self.assertEqual(p.originale.lignes[0].mots[1], Mot(64.8, 65.3, "canción"))
        fr = p.traductions["fr"]
        self.assertEqual((fr.langue, fr.source), ("fr", "modele-de-traduction"))
        self.assertEqual([l.texte for l in fr.lignes], ["Une chanson d'essai", "Pour jules, encore une fois"])
        self.assertEqual(fr.lignes[0].mots, [])

    def test_decalage(self):
        self.assertEqual(self.lire(PAROLES_VALIDES).decalage_s, 0.0)
        p = self.lire({**PAROLES_VALIDES, "decalage_s": -0.1314})
        self.assertEqual(p.decalage_s, -0.1314)
        self.assertEqual(p.originale.lignes[0].debut, 64.5, "les temps du fichier sont gardés tels quels")
        self.assertEqual(self.lire({**PAROLES_VALIDES, "decalage_s": 0.177}).decalage_s, 0.177)
        for abime in ("-0.13", True, None, [1], {"s": 1}):
            self.assertEqual(self.lire({**PAROLES_VALIDES, "decalage_s": abime}).decalage_s, 0.0, abime)
        self.assertEqual(self.lire('{"format": 1, "decalage_s": NaN, "segments": [{"debut": 1, "fin": 2, '
                                   '"texte": "x"}]}').decalage_s, 0.0)

    def test_sans_mots(self):
        p = self.lire({"format": 1, "langue": "it", "segments": [segment(1, 2, "senza parole")]})
        self.assertEqual(p.originale.lignes, [Ligne(1.0, 2.0, "senza parole", [])])
        self.assertIsInstance(p.originale.lignes[0].debut, float)

    def test_sans_traductions(self):
        p = self.lire({k: v for k, v in PAROLES_VALIDES.items() if k != "traductions"})
        self.assertEqual(p.traductions, {})
        self.assertFalse(p.a_deux_langues())

    def test_fichier_absent(self):
        self.assertIsNone(paroles.lire_paroles(self.dossier_voix / "paroles.json"))

    def test_json_illisible(self):
        self.assertIsNone(self.lire('{"format": 1, "segments": [{"debut": 1,'))
        self.assertIsNone(self.lire(""))

    def test_pas_un_dict(self):
        self.assertIsNone(self.lire([segment(1, 2, "dans une liste")]))
        self.assertIsNone(self.lire("12"))

    def test_format_different(self):
        for fmt in (2, 0, "1", True, None):
            with self.subTest(format=fmt):
                self.assertIsNone(self.lire({"format": fmt, "segments": [segment(1, 2, "texte")]}))
        self.assertIsNone(self.lire({"segments": [segment(1, 2, "sans format")]}))
        self.assertIsNotNone(self.lire({"format": 1.0, "segments": [segment(1, 2, "format 1.0")]}))

    def test_segments_pas_une_liste(self):
        for s in ({"debut": 1, "fin": 2, "texte": "x"}, "texte", None, 3):
            with self.subTest(segments=s):
                self.assertIsNone(self.lire({"format": 1, "segments": s}))

    def test_aucun_segment_valide(self):
        self.assertIsNone(self.lire({"format": 1, "segments": []}))
        self.assertIsNone(self.lire({"format": 1, "segments": [segment(2, 1, "à l'envers"), "pas un dict"]}))

    def test_segments_abimes_ignores(self):
        abimes = ["pas un dict", 7, None, {"fin": 2, "texte": "sans début"}, {"debut": 1, "texte": "sans fin"},
                  segment("1.0", 2, "début en chaîne"), segment(1, "2", "fin en chaîne"),
                  segment(True, 2, "début booléen"), segment(1, False, "fin booléenne"),
                  segment(float("nan"), 2, "début NaN"), segment(1, float("inf"), "fin infinie"),
                  segment(-1, 2, "début négatif"), segment(3, 2, "fin avant le début")]
        p = self.lire(self.un_segment(*abimes))
        self.assertEqual([l.texte for l in p.originale.lignes], ["ligne valide"])

    def test_fin_egale_au_debut_acceptee(self):
        p = self.lire(self.un_segment(segment(12.0, 12.0, "instantané")))
        self.assertEqual([l.texte for l in p.originale.lignes], ["ligne valide", "instantané"])

    def test_texte_absent_ou_vide(self):
        p = self.lire(self.un_segment(
            {"debut": 12, "fin": 13, "mots": [{"debut": 12, "fin": 12.5, "mot": " re"},
                                               {"debut": 12.5, "fin": 13, "mot": "fait "}]},
            segment(14, 15, "   ", [(14, 15, "seul")]),
            segment(16, 17, "   "), segment(18, 19, 123), {"debut": 20, "fin": 21}))
        self.assertEqual([l.texte for l in p.originale.lignes], ["ligne valide", "re fait", "seul"])

    def test_espaces_du_texte(self):
        p = self.lire({"format": 1, "segments": [segment(1, 2, "  deux\nlignes   ici  ")]})
        self.assertEqual(p.originale.lignes[0].texte, "deux lignes ici")

    def test_mots_abimes_ignores_un_par_un(self):
        p = self.lire({"format": 1, "segments": [segment(1, 4, "un deux trois", None) | {"mots": [
            {"debut": 3.0, "fin": 3.5, "mot": " trois"}, "pas un dict", {"debut": 1, "fin": 1.5, "mot": ""},
            {"debut": 1, "fin": 1.5, "mot": "   "}, {"debut": "1", "fin": 1.5, "mot": "chaîne"},
            {"debut": 1, "fin": 0.5, "mot": "envers"}, {"debut": float("nan"), "fin": 1, "mot": "nan"},
            {"debut": 1, "fin": 1.5, "mot": 5}, {"debut": 1.0, "fin": 1.5, "mot": "un"},
            {"debut": 2.0, "fin": 2.5, "mot": "deux "}]}]})
        self.assertEqual(p.originale.lignes[0].mots, [Mot(1.0, 1.5, "un"), Mot(2.0, 2.5, "deux"),
                                                      Mot(3.0, 3.5, "trois")])

    def test_mots_pas_une_liste(self):
        p = self.lire({"format": 1, "segments": [segment(1, 2, "texte") | {"mots": {"debut": 1}}]})
        self.assertEqual(p.originale.lignes[0].mots, [])

    def test_segments_tries(self):
        p = self.lire({"format": 1, "segments": [segment(30, 31, "trois"), segment(10, 11, "un"),
                                                 segment(20, 21, "deux")]})
        self.assertEqual([l.texte for l in p.originale.lignes], ["un", "deux", "trois"])

    def test_traductions_abimees(self):
        base = {"format": 1, "segments": [segment(1, 2, "texte")]}
        for t in ([segment(1, 2, "liste")], "fr", None, 3):
            with self.subTest(traductions=t):
                self.assertEqual(self.lire(base | {"traductions": t}).traductions, {})
        p = self.lire(base | {"traductions": {"fr": "pas un dict", "en": {"segments": "pas une liste"},
                                              "de": {"segments": [segment(2, 1, "envers")]},
                                              "it": {"source": 5, "segments": [segment(1, 2, "testo")]}}})
        self.assertEqual(list(p.traductions), ["it"])
        self.assertEqual((p.traductions["it"].langue, p.traductions["it"].source), ("it", ""))

    def test_cle_francaise_rangee_sous_fr(self):
        base = {"format": 1, "segments": [segment(1, 2, "texte")]}
        p = self.lire(base | {"traductions": {"fra": {"segments": [segment(1, 2, "traduit")]}}})
        self.assertEqual(list(p.traductions), ["fr"])
        self.assertEqual((p.traductions["fr"].langue, p.traductions["fr"].lignes[0].texte), ("fra", "traduit"))
        self.assertTrue(p.a_deux_langues())
        # « fr » et « fra » à la fois : « fr » l'emporte, quel que soit l'ordre
        for ordre in (("fr", "fra"), ("fra", "fr")):
            t = {c: {"segments": [segment(1, 2, f"version {c}")]} for c in ordre}
            self.assertEqual(self.lire(base | {"traductions": t}).traductions["fr"].lignes[0].texte, "version fr")

    def test_langue_et_source_abimees(self):
        for langue, source in ((12, None), ("  ", 5), (None, ["x"])):
            p = self.lire({"format": 1, "langue": langue, "source": source, "segments": [segment(1, 2, "x")]})
            self.assertEqual((p.originale.langue, p.originale.source), (None, ""))
        p = self.lire({"format": 1, "segments": [segment(1, 2, "x")]})
        self.assertEqual((p.originale.langue, p.originale.source), (None, ""))

    def test_fichier_jamais_modifie(self):
        f = self.ecrire_paroles(PAROLES_VALIDES)
        avant, date = f.read_bytes(), f.stat().st_mtime_ns
        paroles.lire_paroles(f)
        paroles.paroles_pour(self.dossier_voix)
        self.assertEqual((f.read_bytes(), f.stat().st_mtime_ns), (avant, date))
        self.assertEqual(sorted(p.name for p in self.dossier_voix.iterdir()), ["paroles.json"])


# --- Couches et langues ----------------------------------------------------------------------------------------------

def couche(langue, *textes, debut=0.0):
    return Couche(langue=langue, source="essai", lignes=[Ligne(debut + i, debut + i + 0.8, t)
                                                         for i, t in enumerate(textes)])


class EssaiCouches(unittest.TestCase):
    def setUp(self):
        self.es, self.fr = couche("es", "hola"), couche("fr", "bonjour")
        self.p = Paroles(self.es, {"fr": self.fr}, "paroles.json")

    def test_choix(self):
        self.assertEqual(self.p.couches("originale"), [self.es])
        self.assertEqual(self.p.couches("fr"), [self.fr])
        self.assertEqual(self.p.couches("deux"), [self.es, self.fr])
        self.assertEqual(self.p.couches("autre chose"), [self.es, self.fr])
        self.assertEqual(self.p.couches(None), [self.es, self.fr])
        self.assertTrue(self.p.a_deux_langues())

    def test_sans_traduction(self):
        p = Paroles(self.es, {}, "paroles.json")
        for choix in ("originale", "fr", "deux", "x"):
            self.assertEqual(p.couches(choix), [self.es])
        self.assertFalse(p.a_deux_langues())

    def test_traduction_vide_ou_autre_langue(self):
        for trad in ({"fr": couche("fr")}, {"en": couche("en", "hello")}):
            p = Paroles(self.es, trad, "paroles.json")
            self.assertEqual(p.couches("deux"), [self.es])
            self.assertEqual(p.couches("fr"), [self.es])
            self.assertFalse(p.a_deux_langues())


class EssaiLangues(unittest.TestCase):
    def test_est_francais(self):
        for code in ("fr", "FR", "fra", "fre", "French", "français", "fr-FR", "fr_CA", " fra "):
            self.assertTrue(paroles.est_francais(code), code)
        for code in (None, "", "es", "eng", "und", "frisian", 5):
            self.assertFalse(paroles.est_francais(code), code)

    def test_nom_langue(self):
        attendus = {"fr": "français", "fra": "français", "fre": "français", "FRA": "français", "fr-CA": "français",
                    "en": "anglais", "eng": "anglais", "es": "espagnol", "spa": "espagnol", "it": "italien",
                    "ita": "italien", "de": "allemand", "deu": "allemand", "ger": "allemand", "pt": "portugais",
                    "por": "portugais", "pt_BR": "portugais", "nl": "néerlandais", "nld": "néerlandais",
                    "dut": "néerlandais", "ja": "japonais", "jpn": "japonais", "ko": "coréen", "kor": "coréen",
                    "zh": "chinois", "zho": "chinois", "chi": "chinois", "ar": "arabe", "ara": "arabe",
                    "ru": "russe", "rus": "russe", "und": "langue non indiquée", None: "langue non indiquée",
                    "": "langue non indiquée", "french": "français", "xyz": "xyz", "Tlh": "Tlh"}
        for code, nom in attendus.items():
            self.assertEqual(paroles.nom_langue(code), nom, code)


# --- SRT -------------------------------------------------------------------------------------------------------------

class EssaiLireSrt(unittest.TestCase):
    def textes(self, srt, **k):
        return [(l.debut, l.fin, l.texte) for l in paroles.lire_srt(srt, **k).lignes]

    def test_normal(self):
        c = paroles.lire_srt(SRT_FR, langue="fra", titre="Traduction")
        self.assertEqual((c.langue, c.source, c.titre), ("fra", "sous-titres de la vidéo", "Traduction"))
        self.assertEqual([(l.debut, l.fin, l.texte, l.mots) for l in c.lignes],
                         [(0.5, 2.0, "Une chanson d'essai", []), (2.5, 4.0, "Pour jules", [])])

    def test_bom_et_crlf(self):
        srt = "﻿1\r\n00:00:01,000 --> 00:00:02,000\r\nPremière\r\nsuite\r\n\r\n2\r\n" \
              "00:01:02,250 --> 01:00:03,000\r\nDeuxième\r\n"
        self.assertEqual(self.textes(srt), [(1.0, 2.0, "Première suite"), (62.25, 3603.0, "Deuxième")])

    def test_balises_et_entites(self):
        srt = "1\n00:00:01,000 --> 00:00:02,000\n<i>Penchée</i> et <b>grasse</b>\n" \
              "<font color=\"#ffff00\">jaune</font> &amp; &lt;fin&gt;\n"
        self.assertEqual(self.textes(srt), [(1.0, 2.0, "Penchée et grasse jaune & <fin>")])

    def test_accolades_ass(self):
        srt = "1\n00:00:01,000 --> 00:00:02,000\n{\\an8}En haut{\\i1} de l'écran\\Nsuite\n"
        self.assertEqual(self.textes(srt), [(1.0, 2.0, "En haut de l'écran suite")])

    def test_numeros_absents_et_formats_de_temps(self):
        srt = "0:00:01.5 --> 0:00:02.75\nsans numéro, point et heure courte\n\n" \
              "00:00:03,00 --> 00:00:04,000 X1:0 X2:100\navec position\n"
        self.assertEqual(self.textes(srt), [(1.5, 2.75, "sans numéro, point et heure courte"),
                                            (3.0, 4.0, "avec position")])

    def test_sans_ligne_vide_entre_les_blocs(self):
        srt = "1\n00:00:01,000 --> 00:00:02,000\nun\n2\n00:00:03,000 --> 00:00:04,000\ndeux\n"
        self.assertEqual(self.textes(srt), [(1.0, 2.0, "un"), (3.0, 4.0, "deux")])

    def test_blocs_abimes_ignores(self):
        srt = "1\n00:00:01 --> 00:00:02\nsans millisecondes\n\n" \
              "2\npas de temps ici\n\n" \
              "3\n00:00:05,000 --> 00:00:04,000\nfin avant le début\n\n" \
              "4\n00:00:06,000 --> 00:00:07,000\n<i></i>\n\n" \
              "5\n00:00:08,000 --> 00:00:09,000\n\n" \
              "6\n00:00:10,000 --> 00:00:11,000\nbon bloc\n"
        self.assertEqual(self.textes(srt), [(10.0, 11.0, "bon bloc")])

    def test_ordre(self):
        srt = "1\n00:00:09,000 --> 00:00:10,000\ntrois\n\n2\n00:00:01,000 --> 00:00:02,000\nun\n\n" \
              "3\n00:00:05,000 --> 00:00:06,000\ndeux\n"
        self.assertEqual([t for _d, _f, t in self.textes(srt)], ["un", "deux", "trois"])

    def test_vide(self):
        for texte in ("", None, "﻿", "rien du tout"):
            c = paroles.lire_srt(texte, langue="  ")
            self.assertEqual((c.lignes, c.langue), ([], None))


# --- Sous-titres en cache (index fabriqué) ---------------------------------------------------------------------------

class EssaiSousTitresEnCache(EssaiAvecCache):
    def test_a_extraire_sans_index(self):
        self.assertEqual(paroles.sous_titres_en_cache(self.fausse_video()), (None, "a_extraire"))

    def test_index_illisible(self):
        v = self.fausse_video()
        f = self.ecrire_index(v, [])
        for contenu in ("{pas du json", json.dumps([1, 2]), json.dumps({"format": 2, "flux": []}),
                        json.dumps({"format": 1, "flux": "rien"})):
            f.write_text(contenu, encoding="utf-8")
            self.assertEqual(paroles.sous_titres_en_cache(v), (None, "a_extraire"), contenu)

    def test_video_absente(self):
        for v in (None, "", self.tmp / "absente.mp4", self.tmp):
            self.assertEqual(paroles.sous_titres_en_cache(v), (None, "vidéo doublée introuvable"), v)

    def test_pas_de_flux(self):
        v = self.fausse_video()
        self.ecrire_index(v, [])
        self.assertEqual(paroles.sous_titres_en_cache(v), (None, "pas de sous-titres dans la vidéo doublée"))

    def test_flux_image_seulement(self):
        v = self.fausse_video()
        self.ecrire_index(v, [{"codec": "hdmv_pgs_subtitle", "texte": False},
                              {"codec": "dvd_subtitle", "texte": False}])
        self.assertEqual(paroles.sous_titres_en_cache(v),
                         (None, "sous-titres en image (dvd_subtitle, hdmv_pgs_subtitle) : pas de texte à afficher"))

    def test_srt_efface_par_la_purge(self):
        v = self.fausse_video()
        self.ecrire_index(v, [{"langue": "fra"}], {0: SRT_FR})
        (paroles.dossier_cache() / f"{paroles.cle_video(v)}-0.srt").unlink()
        self.assertEqual(paroles.sous_titres_en_cache(v), (None, "a_extraire"))

    def test_flux_en_echec_ignore(self):
        v = self.fausse_video()
        self.ecrire_index(v, [{"langue": "spa", "erreur": "échec"}, {"langue": "fra", "fichier": "../ailleurs.srt"},
                              {"langue": "ita"}], {2: SRT_ES})
        p, raison = paroles.sous_titres_en_cache(v)
        self.assertIsNone(raison)
        self.assertEqual((p.originale.langue, p.traductions), ("ita", {}))

    def test_sous_titres_vides(self):
        v = self.fausse_video()
        self.ecrire_index(v, [{"langue": "fra"}, {"langue": "spa", "fichier": None}], {0: "\n\n"})
        self.assertEqual(paroles.sous_titres_en_cache(v), (None, "sous-titres vides"))

    def test_originale_non_forcee_et_traduction_francaise(self):
        v = self.fausse_video()
        self.ecrire_index(v, [{"langue": "fra", "force": True, "defaut": True, "titre": "FORCED"},
                              {"langue": "spa"}], {0: SRT_FR, 1: SRT_ES})
        p, raison = paroles.sous_titres_en_cache(v)
        self.assertIsNone(raison)
        self.assertEqual(p.origine, "sous-titres")
        self.assertEqual((p.originale.langue, p.originale.source), ("spa", "sous-titres de la vidéo"))
        self.assertEqual((p.traductions["fr"].langue, p.traductions["fr"].titre), ("fra", "FORCED"))
        self.assertEqual(p.originale.lignes[0].texte, "Una canción de prueba")

    def test_traduction_non_forcee_preferee(self):
        v = self.fausse_video()
        self.ecrire_index(v, [{"langue": "spa", "defaut": True}, {"langue": "fra", "force": True, "titre": "FORCED"},
                              {"langue": "fre", "titre": "Complets"}], {0: SRT_ES, 1: SRT_FR, 2: SRT_FR})
        p, _r = paroles.sous_titres_en_cache(v)
        self.assertEqual((p.originale.langue, p.traductions["fr"].titre), ("spa", "Complets"))

    def test_defaut_prefere_puis_premier(self):
        v = self.fausse_video()
        self.ecrire_index(v, [{"langue": "eng"}, {"langue": "ita", "defaut": True}], {0: SRT_ES, 1: SRT_ES})
        self.assertEqual(paroles.sous_titres_en_cache(v)[0].originale.langue, "ita")
        v2 = self.fausse_video("Autre - voix Jules.mkv")
        self.ecrire_index(v2, [{"langue": "eng"}, {"langue": "ita"}], {0: SRT_ES, 1: SRT_ES})
        self.assertEqual(paroles.sous_titres_en_cache(v2)[0].originale.langue, "eng")

    def test_debut_negatif_du_fichier_rajoute(self):
        v = self.fausse_video()
        bornes = lambda: [(l.debut, l.fin) for l in paroles.sous_titres_en_cache(v)[0].originale.lignes]
        self.ecrire_index(v, [{"langue": "fra"}], {0: SRT_FR}, decalage_s=-0.25)
        self.assertEqual(bornes(), [(0.25, 1.75), (2.25, 3.75)])
        self.ecrire_index(v, [{"langue": "fra"}], {0: SRT_FR}, decalage_s=-1.0)
        self.assertEqual(bornes(), [(0.0, 1.0), (1.5, 3.0)])            # jamais avant 0
        for dec in (0.3, 0, "x", True, None):                            # positif ou abîmé : ignoré
            self.ecrire_index(v, [{"langue": "fra"}], {0: SRT_FR}, decalage_s=dec)
            self.assertEqual(bornes(), [(0.5, 2.0), (2.5, 4.0)], dec)

    def test_originale_francaise_sans_traduction(self):
        v = self.fausse_video()
        self.ecrire_index(v, [{"langue": "fra", "defaut": True}, {"langue": "fre"}], {0: SRT_FR, 1: SRT_FR})
        p, _r = paroles.sous_titres_en_cache(v)
        self.assertEqual((p.originale.langue, p.traductions), ("fra", {}))
        self.assertFalse(p.a_deux_langues())


# --- Extraction réelle (ffmpeg) --------------------------------------------------------------------------------------

@unittest.skipUnless(FFMPEG, "ffmpeg et ffprobe sont nécessaires")
class EssaiExtraction(EssaiAvecCache):
    def copie(self, nom):
        f = self.tmp / "videos" / nom
        f.parent.mkdir(exist_ok=True)
        shutil.copy2(video(nom), f)
        return f

    def test_flux_mkv_deux_langues(self):
        flux = paroles.flux_sous_titres(video("deux.mkv"))
        self.assertEqual([(f["n"], f["index"], f["codec"], f["langue"], f["titre"], f["texte"], f["force"])
                          for f in flux],
                         [(0, 2, "subrip", "spa", "", True, False), (1, 3, "subrip", "fra", "Traduction", True, False)])
        self.assertTrue(flux[0]["defaut"])
        self.assertFalse(flux[1]["defaut"])

    def test_extraction_et_lecture(self):
        v = self.copie("deux.mkv")
        index = paroles.extraire_sous_titres(v)
        cle = paroles.cle_video(v)
        self.assertEqual((index["format"], index["video"]), (1, str(v.resolve())))
        self.assertEqual([f["fichier"] for f in index["flux"]], [f"{cle}-0.srt", f"{cle}-1.srt"])
        # mkv avec AAC : le fichier commence un peu avant 0 (amorce) ; les temps lus restent ceux du lecteur
        self.assertLessEqual(index["decalage_s"], 0.0)
        self.assertEqual(json.loads((paroles.dossier_cache() / f"{cle}.json").read_text(encoding="utf-8")), index)
        p, raison = paroles.sous_titres_en_cache(v)
        self.assertIsNone(raison)
        self.assertEqual((p.origine, p.originale.langue, p.originale.source),
                         ("sous-titres", "spa", "sous-titres de la vidéo"))
        self.assertEqual([(l.debut, l.fin, l.texte) for l in p.originale.lignes],
                         [(0.5, 2.0, "Una canción de prueba"), (2.5, 4.0, "Para jules")])
        self.assertEqual((p.traductions["fr"].langue, p.traductions["fr"].titre), ("fra", "Traduction"))
        self.assertEqual(p.couches("fr")[0].lignes[1].texte, "Pour jules")

    def test_mp4_mov_text_francais(self):
        v = self.copie("fra.mp4")
        index = paroles.extraire_sous_titres(v)
        self.assertEqual([(f["codec"], f["langue"]) for f in index["flux"]], [("mov_text", "fra")])
        p, _r = paroles.sous_titres_en_cache(v)
        self.assertEqual((p.originale.langue, p.traductions), ("fra", {}))
        self.assertEqual([l.texte for l in p.originale.lignes], ["Une chanson d'essai", "Pour jules"])

    def test_sans_sous_titres(self):
        v = self.copie("sans.mp4")
        self.assertEqual(paroles.extraire_sous_titres(v)["flux"], [])
        self.assertEqual(paroles.sous_titres_en_cache(v), (None, "pas de sous-titres dans la vidéo doublée"))

    def test_rien_a_cote_de_la_video(self):
        v = self.copie("deux.mkv")
        avant = sorted(p.name for p in v.parent.iterdir())
        paroles.extraire_sous_titres(v)
        paroles.sous_titres_en_cache(v)
        self.assertEqual(sorted(p.name for p in v.parent.iterdir()), avant)
        self.assertEqual(paroles.dossier_cache(), self.cache / "sous-titres")
        cle = paroles.cle_video(v)
        self.assertEqual(sorted(p.name for p in paroles.dossier_cache().iterdir()),
                         [f"{cle}-0.srt", f"{cle}-1.srt", f"{cle}.json"])
        self.assertFalse((self.tmp / "xdg-cache").exists())

    def test_deuxieme_lecture_sans_reextraction(self):
        v = self.copie("deux.mkv")
        paroles.extraire_sous_titres(v)
        d = paroles.dossier_cache()
        dates = {p.name: p.stat().st_mtime_ns for p in d.iterdir()}
        time.sleep(0.02)
        premier = paroles.sous_titres_en_cache(v)
        self.assertEqual(paroles.sous_titres_en_cache(v), premier)
        self.assertEqual(paroles.paroles_pour(self.dossier_voix, v), premier)
        self.assertEqual({p.name: p.stat().st_mtime_ns for p in d.iterdir()}, dates)

    def test_video_modifiee_nouvelle_cle(self):
        v = self.copie("deux.mkv")
        cle = paroles.cle_video(v)
        paroles.extraire_sous_titres(v)
        st = v.stat()
        os.utime(v, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        self.assertNotEqual(paroles.cle_video(v), cle)
        self.assertEqual(paroles.sous_titres_en_cache(v), (None, "a_extraire"))
        paroles.extraire_sous_titres(v)
        self.assertIsNotNone(paroles.sous_titres_en_cache(v)[0])

    def test_video_illisible(self):
        v = self.fausse_video()
        with self.assertRaises(paroles.ErreurParoles):
            paroles.flux_sous_titres(v)
        with self.assertRaises(paroles.ErreurParoles):
            paroles.extraire_sous_titres(v)
        self.assertEqual(paroles.sous_titres_en_cache(v), (None, "a_extraire"))

    def test_video_absente(self):
        with self.assertRaises(paroles.ErreurParoles):
            paroles.extraire_sous_titres(self.tmp / "absente.mp4")

    def test_purge_du_cache(self):
        v = self.copie("deux.mkv")
        d = paroles.dossier_cache()
        d.mkdir(parents=True)
        vieux = d / "ancien.srt.1234.abcd.tmp"
        vieux.write_text("abandonné")
        os.utime(vieux, (time.time() - 7200, time.time() - 7200))
        paroles.extraire_sous_titres(v)
        self.assertFalse(vieux.exists())


# --- paroles_pour ----------------------------------------------------------------------------------------------------

class EssaiParolesPour(EssaiAvecCache):
    def test_paroles_json_prioritaire(self):
        v = self.fausse_video()
        self.ecrire_index(v, [{"langue": "fra"}], {0: SRT_FR})
        self.ecrire_paroles(PAROLES_VALIDES)
        p, raison = paroles.paroles_pour(self.dossier_voix, v)
        self.assertEqual((p.origine, raison), ("paroles.json", None))

    def test_repli_sur_les_sous_titres(self):
        v = self.fausse_video()
        self.ecrire_index(v, [{"langue": "fra"}], {0: SRT_FR})
        p, raison = paroles.paroles_pour(self.dossier_voix, v)
        self.assertEqual((p.origine, p.originale.langue, raison), ("sous-titres", "fra", None))
        self.assertEqual(paroles.paroles_pour(None, v)[0].origine, "sous-titres")

    def test_a_extraire(self):
        v = self.fausse_video()
        self.assertEqual(paroles.paroles_pour(self.dossier_voix, v), (None, "a_extraire"))
        self.ecrire_paroles("{abîmé")
        self.assertEqual(paroles.paroles_pour(self.dossier_voix, v), (None, "a_extraire"))

    def test_paroles_json_abime(self):
        v = self.fausse_video()
        self.ecrire_paroles({"format": 1, "segments": [segment(2, 1, "envers")]})
        self.ecrire_index(v, [{"langue": "fra"}], {0: SRT_FR})
        self.assertEqual(paroles.paroles_pour(self.dossier_voix, v)[0].origine, "sous-titres")
        self.ecrire_index(v, [])
        self.assertEqual(paroles.paroles_pour(self.dossier_voix, v),
                         (None, "paroles.json illisible ; pas de sous-titres dans la vidéo doublée"))
        self.assertEqual(paroles.paroles_pour(self.dossier_voix),
                         (None, "paroles.json illisible ; pas de vidéo doublée pour les sous-titres"))

    def test_rien(self):
        self.assertEqual(paroles.paroles_pour(self.dossier_voix), (None, "ni paroles.json ni vidéo doublée"))
        self.assertEqual(paroles.paroles_pour(None, None), (None, "ni paroles.json ni vidéo doublée"))
        self.assertEqual(paroles.paroles_pour(self.dossier_voix, self.tmp / "absente.mp4"),
                         (None, "vidéo doublée introuvable"))


# --- Temps du lecteur ------------------------------------------------------------------------------------------------

def paroles_a(debut, decalage_s=0.0, origine="paroles.json"):
    """Des paroles d'une ligne, qui commence à debut (temps des paroles)."""
    return Paroles(Couche("es", "essai", [Ligne(debut, debut + 0.5, "ligne d'essai")]), {}, origine, decalage_s)


class EssaiEcartLecture(unittest.TestCase):
    """Position du lecteur → temps des paroles. Un repère à 1,0 s du son doublé : paroles.json le met à 1,0 s +
    decalage_s (doubler.caler_paroles) ; le lecteur l'entend à 1,0 s dans un son seul (MP3, original.wav, beat), et à
    1,0 s + debut_son dans la vidéo doublée."""

    def en_cours(self, p, position, avec_image, debut_son):
        return paroles.position(p.originale.lignes, position + paroles.ecart_lecture(p, avec_image, debut_son))[0]

    def test_sans_decalage(self):
        p = paroles_a(1.0)
        for avec_image in (True, False):
            for debut_son in (0.0, None):
                self.assertEqual(paroles.ecart_lecture(p, avec_image, debut_son), 0.0)

    def test_extrait_mkv_ou_webm(self):
        # doubler décale le mix de −0,1314 s, mais le conteneur recale tout sur la première image : le son commence
        # avec l'image (decalage_son 0 dans source.json) ; le repère s'entend à 1,0 s, en vidéo comme en son seul
        p = paroles_a(0.8686, -0.1314)
        for avec_image in (True, False):
            self.assertAlmostEqual(paroles.ecart_lecture(p, avec_image, 0.0), -0.1314)
            self.assertEqual(self.en_cours(p, 1.0, avec_image, 0.0), 0)
            self.assertIsNone(self.en_cours(p, 0.9, avec_image, 0.0), "pas 0,13 s trop tôt")
        self.assertEqual(paroles.position(p.originale.lignes, 0.9)[0], 0, "sans l'écart : 0,13 s trop tôt")

    def test_video_entiere_au_son_en_retard(self):
        # son d'origine 0,177 s après l'image (decalage_son de source.json, repris par doubler et dans decalage_s) :
        # la vidéo doublée a le même temps que paroles.json ; le MP3 et les sons seuls, celui du son
        p = paroles_a(1.177, 0.177)
        self.assertEqual(paroles.ecart_lecture(p, True, 0.177), 0.0)
        self.assertAlmostEqual(paroles.ecart_lecture(p, False, 0.177), 0.177)
        self.assertEqual(self.en_cours(p, 1.18, True, 0.177), 0)
        self.assertEqual(self.en_cours(p, 1.0, False, 0.177), 0)
        self.assertIsNone(self.en_cours(p, 0.9, False, 0.177))
        self.assertIsNone(paroles.position(p.originale.lignes, 1.0)[0], "sans l'écart : MP3 0,18 s trop tard")

    def test_sans_source_json(self):
        # debut_son inconnu : decalage_s s'il est positif (vidéo entière au son en retard), sinon 0 (extrait)
        self.assertAlmostEqual(paroles.ecart_lecture(paroles_a(0.8686, -0.1314), True, None), -0.1314)
        self.assertEqual(paroles.ecart_lecture(paroles_a(1.177, 0.177), True, None), 0.0)
        self.assertAlmostEqual(paroles.ecart_lecture(paroles_a(1.177, 0.177), False, None), 0.177)

    def test_sous_titres_en_temps_de_la_video(self):
        p = paroles_a(1.177, origine="sous-titres")
        self.assertEqual(paroles.ecart_lecture(p, True, 0.177), 0.0)
        self.assertAlmostEqual(paroles.ecart_lecture(p, False, 0.177), 0.177)
        self.assertEqual(paroles.ecart_lecture(p, False, None), 0.0)
        # un decalage_s ne concerne que paroles.json
        self.assertEqual(paroles.ecart_lecture(paroles_a(1.0, 0.5, "sous-titres"), True, 0.0), 0.0)

    def test_sans_paroles(self):
        self.assertEqual(paroles.ecart_lecture(None, False, 0.177), 0.0)

    def test_decalage_son_de_source_json(self):
        self.assertEqual(paroles.decalage_son({"decalage_son": 0.177}), 0.177)
        self.assertEqual(paroles.decalage_son({"decalage_son": 0.0, "debut": 2.401}), 0.0)
        for abime in ({}, None, [], {"decalage_son": "0.1"}, {"decalage_son": True}, {"decalage_son": float("inf")}):
            self.assertIsNone(paroles.decalage_son(abime), abime)


@unittest.skipUnless(FFMPEG and VRAI_DOUBLER.is_file(), "ffmpeg ou doubler.py absent")
class EssaiEcartBoutEnBout(EssaiIsole):
    """De bout en bout : de petites vidéos de synthèse remises en son par les fonctions de doubler.py (preparer_source,
    plan_video, remettre_sur_video), une ligne de paroles au repère, à 2,5 s du son doublé, calée par
    doubler.caler_paroles puis relue par lire_paroles. Le début du son est mesuré dans la vidéo doublée (corrélation,
    relatif à la première image). La ligne doit tomber sur le repère à 30 ms près : en vidéo, et dans un son seul
    (MP3, travail/original.wav, beat ou voix seule sans copie vidéo). Lue par GStreamer, la vidéo doublée d'un mkv
    porte en plus l'amorce de l'AAC (23 ms) ; le MP3, le délai de l'encodeur (25 ms)."""

    REPERE = 2.5

    def doubler_synthese(self, nom, ext, debut=None, duree=None, retard=0.0):
        import numpy as np
        doubler = charger_doubler(self.tmp / f"doubler-{nom}")
        source = self.tmp / "videos" / f"Source {nom}{ext}"
        video_de_synthese(source, duree=6.0)
        if retard:      # son d'origine qui part après l'image (decalage_son dans source.json)
            brut = source.with_name(f"brut{ext}")
            source.rename(brut)
            nice_ffmpeg("-i", brut, "-itsoffset", retard, "-i", brut, "-map", "0:v", "-map", "1:a", "-c", "copy",
                        source)
        d = self.donnees / "sorties" / f"Chanson {nom}"
        t = d / "travail"
        t.mkdir(parents=True)
        with contextlib.redirect_stdout(io.StringIO()):
            src = doubler.preparer_source(source, doubler.sonder(source), debut, duree, t)
            n = int(SR * (src.get("duree") or doubler.duree_s(t / "original.wav")))
            mix = np.random.default_rng(3).standard_normal(n).astype(np.float32) * 0.2
            (t / "mix.f32").write_bytes(np.stack([mix, mix], 1).tobytes())
            nice_ffmpeg("-f", "f32le", "-ar", SR, "-ac", 2, "-i", t / "mix.f32", "-c:a", "pcm_f32le", t / "mix.wav")
            plan = doubler.plan_video(t)
            doublee = doubler.remettre_sur_video(t, t / "mix.wav", d, "Chanson - voix Jules", plan=plan)
            cale = doubler.caler_paroles({"format": 1, "langue": "es", "decalage_s": 0.0, "traductions": {},
                                          "segments": [segment(self.REPERE, self.REPERE + 1.0, "repère")]},
                                         plan["decalage_son"])
        f = t / "jules" / "paroles.json"
        f.parent.mkdir()
        f.write_text(json.dumps(cale), encoding="utf-8")
        p = paroles.lire_paroles(f)
        debut_son = paroles.decalage_son(json.loads((t / "source.json").read_text(encoding="utf-8")))
        echantillons, pic = debut_mesure(doublee, t / "mix.wav")
        self.assertGreater(pic, 0.8)
        video = self.REPERE + echantillons / SR       # où le lecteur entend le repère dans la vidéo doublée
        ligne = p.originale.lignes[0].debut
        for position, avec_image in ((video, True), (self.REPERE, False)):
            t_paroles = position + paroles.ecart_lecture(p, avec_image, debut_son)
            self.assertAlmostEqual(t_paroles, ligne, delta=0.03, msg=f"{nom}, avec image : {avec_image}")
            self.assertEqual(paroles.position(p.originale.lignes, t_paroles + 0.03)[0], 0)
        return p, video, ligne

    def test_extrait_mkv(self):
        p, video, ligne = self.doubler_synthese("mkv-extrait", ".mkv", debut=1.4, duree=4.0)
        self.assertAlmostEqual(p.decalage_s, -0.1315, delta=0.002)
        # sans l'écart, la ligne venait 0,13 s trop tôt, en vidéo comme en son seul
        self.assertGreater(video - ligne, 0.1)
        self.assertGreater(self.REPERE - ligne, 0.1)

    def test_mkv_entier_au_son_en_retard(self):
        p, video, ligne = self.doubler_synthese("mkv-retard", ".mkv", retard=0.2)
        self.assertGreater(p.decalage_s, 0.1)
        self.assertAlmostEqual(video, ligne, delta=0.03, msg="en vidéo, paroles.json était déjà juste")
        self.assertGreater(ligne - self.REPERE, 0.1, "en son seul, la ligne venait trop tard")

    def test_mp4_extrait(self):
        p, video, ligne = self.doubler_synthese("mp4-extrait", ".mp4", debut=1.4, duree=4.0)
        self.assertEqual(p.decalage_s, 0.0)


# --- Synchronisation -------------------------------------------------------------------------------------------------

def lignes(*bornes):
    return [Ligne(float(d), float(f), f"ligne {i}") for i, (d, f) in enumerate(bornes)]


class EssaiPosition(unittest.TestCase):
    def setUp(self):
        self.l = lignes((1, 3), (4, 6), (6, 8))

    def test_avant_la_premiere(self):
        self.assertEqual(paroles.position(self.l, 0.0), (None, 0))
        self.assertEqual(paroles.position(self.l, 0.999), (None, 0))

    def test_dedans(self):
        self.assertEqual(paroles.position(self.l, 1.0), (0, 1))
        self.assertEqual(paroles.position(self.l, 2.9), (0, 1))
        self.assertEqual(paroles.position(self.l, 5.0), (1, 2))

    def test_bord_de_fin_exclu(self):
        self.assertEqual(paroles.position(self.l, 3.0), (None, 1))
        self.assertEqual(paroles.position(self.l, 6.0), (2, None))    # lignes contiguës : la suivante commence

    def test_entre_deux_lignes(self):
        self.assertEqual(paroles.position(self.l, 3.5), (None, 1))

    def test_apres_la_derniere(self):
        self.assertEqual(paroles.position(self.l, 8.0), (None, None))
        self.assertEqual(paroles.position(self.l, 100.0), (None, None))

    def test_chevauchement(self):
        l = lignes((0, 5), (2, 4), (3, 10), (12, 13))
        self.assertEqual(paroles.position(l, 3.5), (2, 3))     # celle qui a commencé en dernier
        self.assertEqual(paroles.position(l, 2.5), (1, 2))
        l2 = lignes((0, 10), (2, 4))
        self.assertEqual(paroles.position(l2, 5.0), (0, None))  # la courte est finie, la longue continue

    def test_meme_debut(self):
        l = lignes((1, 2), (1, 3))
        self.assertEqual(paroles.position(l, 1.5), (1, None))
        self.assertEqual(paroles.position(l, 2.5), (1, None))
        self.assertEqual(paroles.position(l, 0.5), (None, 0))

    def test_vide(self):
        self.assertEqual(paroles.position([], 1.0), (None, None))
        self.assertEqual(paroles.position(None, 1.0), (None, None))


class EssaiMotEnCours(unittest.TestCase):
    def test_mots(self):
        mots = [Mot(1.0, 1.2, "un"), Mot(1.5, 1.8, "deux"), Mot(2.0, 2.4, "trois")]
        for t, i in ((0.5, None), (1.0, 0), (1.3, 0), (1.5, 1), (1.9, 1), (2.0, 2), (10.0, 2)):
            self.assertEqual(paroles.mot_en_cours(mots, t), i, t)

    def test_vide(self):
        self.assertIsNone(paroles.mot_en_cours([], 1.0))
        self.assertIsNone(paroles.mot_en_cours(None, 1.0))


class EssaiCorrespondante(unittest.TestCase):
    def test_plus_grand_recouvrement(self):
        trad = lignes((9, 10.5), (10.5, 12.5), (13, 14))
        self.assertIs(paroles.correspondante(trad, Ligne(10, 12, "x")), trad[1])

    def test_debut_proche_sans_recouvrement(self):
        trad = lignes((20.4, 21), (30, 31))
        self.assertIs(paroles.correspondante(trad, Ligne(20.0, 20.2, "x")), trad[0])
        self.assertIs(paroles.correspondante(trad, Ligne(20.2, 20.2, "instantanée")), trad[0])
        self.assertIsNone(paroles.correspondante(trad, Ligne(25, 26, "x")))
        self.assertIsNone(paroles.correspondante(lignes((21, 22)), Ligne(20.0, 20.4, "x")))

    def test_vide(self):
        self.assertIsNone(paroles.correspondante([], Ligne(1, 2, "x")))


class EssaiFragments(unittest.TestCase):
    def ligne(self, texte, *mots):
        return Ligne(0.0, 5.0, texte, [Mot(float(i), i + 0.5, m) for i, m in enumerate(mots)])

    def test_mots_avec_ponctuation(self):
        l = self.ligne("¿Qué tal, amigo?", "¿Qué", "tal,", "amigo?")
        f = paroles.fragments(l)
        self.assertEqual("".join(t for t, _i in f), l.texte)
        self.assertEqual([i for _t, i in f if i is not None], [0, 1, 2])

    def test_ponctuation_gardee_hors_des_mots(self):
        f = paroles.fragments(self.ligne("¿Qué tal, amigo?", "Qué", "tal", "amigo"))
        self.assertEqual(f, [("¿", None), ("Qué", 0), (" ", None), ("tal", 1), (", ", None), ("amigo", 2),
                             ("?", None)])

    def test_casse_ignoree(self):
        f = paroles.fragments(self.ligne("Hola Jules", "hola", "JULES"))
        self.assertEqual(f, [("Hola", 0), (" ", None), ("Jules", 1)])

    def test_mots_repetes(self):
        f = paroles.fragments(self.ligne("la la la", "la", "la", "la"))
        self.assertEqual(f, [("la", 0), (" ", None), ("la", 1), (" ", None), ("la", 2)])

    def test_mot_introuvable_reconstruction(self):
        f = paroles.fragments(self.ligne("Bonjour tout le monde", "hola", "mundo"))
        self.assertEqual(f, [("hola", 0), (" ", None), ("mundo", 1)])
        f2 = paroles.fragments(self.ligne("un deux", "deux", "un"))    # ordre impossible dans le texte
        self.assertEqual("".join(t for t, _i in f2), "deux un")

    def test_sans_mots(self):
        self.assertEqual(paroles.fragments(Ligne(0, 1, "texte seul")), [("texte seul", None)])

    def test_lettre_qui_change_de_longueur(self):
        l = self.ligne("İstanbul ici", "İstanbul", "ici")
        self.assertEqual("".join(t for t, _i in paroles.fragments(l)), l.texte)


# --- Affichage -------------------------------------------------------------------------------------------------------

ACCENT = "#3584e4"


class EssaiBalisage(unittest.TestCase):
    def setUp(self):
        self.l = Ligne(1.0, 4.0, "Un matin, de pluie", [Mot(1.0, 1.5, "Un"), Mot(1.6, 2.2, "matin"),
                                                        Mot(2.4, 2.8, "de"), Mot(2.9, 3.6, "pluie")])

    def sans_balises(self, texte):
        import re
        return re.sub(r"<[^>]+>", "", texte)

    def test_mot_en_cours_souligne(self):
        b = paroles.balisage(self.l, 1, ACCENT)
        self.assertIn(f'<span foreground="{ACCENT}" underline="single">matin</span>', b)
        self.assertTrue(b.startswith("Un "))                              # déjà chanté : tel quel
        self.assertIn('<span alpha="58%">pluie</span>', b)                # à venir : atténué
        self.assertIn('<span alpha="58%">, </span>', b)                   # ponctuation qui suit le mot en cours
        self.assertEqual(self.sans_balises(b), self.l.texte)

    def test_avant_le_premier_mot(self):
        b = paroles.balisage(self.l, None, ACCENT)
        self.assertNotIn(ACCENT, b)
        self.assertEqual(b.count('alpha="58%"'), 7)                       # 4 mots et les 3 morceaux entre eux

    def test_dernier_mot(self):
        b = paroles.balisage(self.l, 3, ACCENT)
        self.assertTrue(b.endswith(f'<span foreground="{ACCENT}" underline="single">pluie</span>'))
        self.assertNotIn("alpha", b)

    def test_ligne_suivante_ou_sans_mots(self):
        self.assertEqual(paroles.balisage(self.l, -1, ACCENT), self.l.texte)
        self.assertEqual(paroles.balisage(Ligne(0, 1, "Sans mots"), 0, ACCENT), "Sans mots")
        self.assertEqual(paroles.balisage(None, 0, ACCENT), "")

    def test_echappement(self):
        l = Ligne(0, 2, "Toi & moi <3", [Mot(0, 1, "Toi"), Mot(1, 1.5, "&"), Mot(1.5, 2, "moi")])
        b = paroles.balisage(l, 1, ACCENT)
        self.assertIn(">&amp;</span>", b)
        self.assertIn("&lt;3", b)
        self.assertNotIn("<3", b)
        self.assertEqual(paroles.balisage(Ligne(0, 1, "a < b"), -1, ACCENT), "a &lt; b")


class EssaiLegendeSource(unittest.TestCase):
    def test_paroles_json(self):
        es, fr = couche("es", "hola"), couche("fr", "bonjour")
        es.source, fr.source = "whisper-large-v3", "traducteur-essai"
        p = Paroles(es, {"fr": fr}, "paroles.json")
        self.assertEqual(paroles.legende_source(p, p.couches("deux")),
                         "Whisper-large-v3 · espagnol ; traduction en français : traducteur-essai")
        self.assertEqual(paroles.legende_source(p, p.couches("originale")), "Whisper-large-v3 · espagnol")

    def test_sous_titres(self):
        c = Couche("fra", paroles.SOURCE_SOUS_TITRES, [Ligne(0, 1, "x")], titre="FORCED")
        p = Paroles(c, {}, "sous-titres")
        self.assertEqual(paroles.legende_source(p, [c]), "Sous-titres de la vidéo · français (fra) · forcés")
        c2 = Couche(None, paroles.SOURCE_SOUS_TITRES, [Ligne(0, 1, "x")])
        self.assertEqual(paroles.legende_source(Paroles(c2, {}, "sous-titres"), [c2]),
                         "Sous-titres de la vidéo · langue non indiquée")


# --- Ligne de commande -----------------------------------------------------------------------------------------------

class EssaiLigneDeCommande(EssaiAvecCache):
    def test_sans_argument_valide(self):
        for argv in ([], ["sous-titres"], ["autre", "x"], ["sous-titres", "a", "b"]):
            sortie = io.StringIO()
            with contextlib.redirect_stdout(sortie):
                self.assertEqual(paroles.main(argv), 2, argv)
            self.assertIn("python3 -m voixla.paroles sous-titres VIDEO", sortie.getvalue())

    def test_video_absente(self):
        erreur = io.StringIO()
        with contextlib.redirect_stderr(erreur):
            self.assertEqual(paroles.main(["sous-titres", str(self.tmp / "absente.mp4")]), 1)
        self.assertIn("sous-titres impossibles", erreur.getvalue())

    @unittest.skipUnless(FFMPEG, "ffmpeg et ffprobe sont nécessaires")
    def test_sous_processus(self):
        v = self.tmp / "Chanson fictive - voix Jules.mkv"
        shutil.copy2(video("deux.mkv"), v)
        r = subprocess.run([sys.executable, "-m", "voixla.paroles", "sous-titres", str(v)], cwd=str(INTERFACE),
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual((d["video"], d["flux"], d["texte"]), (str(v.resolve()), 2, 2))
        self.assertIsInstance(d["duree_s"], float)
        self.assertTrue((chemins.dossier_cache() / "sous-titres" / f"{paroles.cle_video(v)}.json").exists())
        self.assertIsNotNone(paroles.sous_titres_en_cache(v)[0])


if __name__ == "__main__":
    unittest.main()
