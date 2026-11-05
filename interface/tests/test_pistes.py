"""Essais de voixla.pistes : plan de « ma voix seule » (gains du mix, fichiers manquants, anciens doublages), même son
que la part de la voix dans le mix de doubler.py (sa fonction mixer, appliquée à une musique muette), cache, choix des
pistes de la page Résultats, noms et écriture des fichiers enregistrés, ligne de commande ; fichiers enregistrés
écartés des sorties de doubler (voixla.rapports).

Sons de synthèse par ffmpeg (sous nice), dans des dossiers temporaires ; titres et voix fictifs (« jules ») ; le cache
va dans le dossier temporaire (VOIXLA_CACHE). Rien n'est lu ni écrit dans de vraies données."""

import contextlib
import importlib.util
import io
import json
import math
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from voixla import chemins, pistes, rapports
from voixla.rapports import Resultat

from .commun import VRAI_DOUBLER, EssaiIsole

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
SR = 44100


def ecrire(chemin, contenu=b""):
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(contenu, (dict, list)):
        chemin.write_text(json.dumps(contenu, ensure_ascii=False), encoding="utf-8")
    else:
        chemin.write_bytes(contenu)
    return chemin


def ffmpeg(sortie, source, duree=3.0, canaux=2, codec=("-c:a", "pcm_f32le")):
    """Un son lavfi (sine=…, aevalsrc=…) écrit en WAV flottant (ou au codec donné)."""
    Path(sortie).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi",
                    "-i", f"{source}:d={duree}" if "=" in source else source, "-ac", str(canaux), "-ar", str(SR),
                    *codec, str(sortie)], check=True, timeout=60, stdin=subprocess.DEVNULL)
    return Path(sortie)


def echantillons(fichier):
    """Échantillons flottants (stéréo entrelacée) d'un fichier, par ffmpeg."""
    import array
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(fichier), "-ac", "2", "-ar", str(SR), "-f",
                        "f32le", "-"], capture_output=True, check=True, timeout=60)
    a = array.array("f")
    a.frombytes(r.stdout[:len(r.stdout) // 4 * 4])
    return a


def rms_db(a):
    return 10 * math.log10(sum(x * x for x in a) / max(1, len(a)) + 1e-30)


def duree(fichier):
    return pistes.duree_audio(fichier)


class EssaiAvecCache(EssaiIsole):
    def setUp(self):
        super().setUp()
        self.cache = self.tmp / "cache"
        os.environ["VOIXLA_CACHE"] = str(self.cache)
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "xdg-cache")

    def doublage(self, titre="Chanson fictive", voix="jules", nom="Jules", fichiers=(), rapport=None, source=True,
                 video=True, mp3=True):
        """Un dossier de sortie fictif : sorties/<titre>/, médias vides, travail/ avec les fichiers demandés (vides)."""
        d = self.donnees / "sorties" / titre
        t = d / "travail"
        tv = t / voix
        tv.mkdir(parents=True, exist_ok=True)
        if video:
            ecrire(d / f"{titre} - voix {nom}.mp4")
        if mp3:
            ecrire(d / f"{titre} - voix {nom}.mp3")
        for f in fichiers:
            ecrire(t / f)
        if source:
            v = ecrire(self.tmp / "videos" / f"{titre}.mp4")
            ecrire(t / "source.json", {"video": str(v), "debut": 12.5, "duree": 40.0})
        if rapport is not None:
            ecrire(tv / "rapport.json", rapport)
        return rapports.lire_dossier(d, {voix: nom})[0]


RAPPORT_CHANT = {"mode": "chant", "mix": {"version": 2, "voix_db": 4.0, "reverb_db": -13, "limite_dbtp": -1.0,
                                          "gains_db": {"seche": 8.2, "reverb": 24.3}}}
RAPPORT_PAROLE = {"mode": "parole", "mix": {"version": 2, "voix_db": 0.0, "reverb_db": None, "limite_dbtp": -1.0,
                                            "gains_db": {"seche": -9.6}}}


# --- Plan de « ma voix seule » ---------------------------------------------------------------------------------------

class EssaiPlanMaVoix(EssaiAvecCache):
    def plan(self, fichiers, rapport):
        r = self.doublage(fichiers=[f"jules/{f}" for f in fichiers], rapport=rapport)
        return pistes.plan_ma_voix(r.dossier_voix, r.rapport), r

    def test_chant_seche_et_reverb_aux_gains_du_mix(self):
        p, r = self.plan(["seche_voix_convertie.wav", "reverb_voix_convertie.wav"], RAPPORT_CHANT)
        self.assertTrue(p.exact and p.a_combiner and p.possible)
        self.assertEqual((p.gain_seche, p.gain_reverb), (8.2, 24.3))
        self.assertEqual(p.seche, r.dossier_voix / "seche_voix_convertie.wav")
        self.assertEqual(p.reverb, r.dossier_voix / "reverb_voix_convertie.wav")
        self.assertAlmostEqual(p.limite, 10 ** (-1 / 20), places=6)
        self.assertIn("+8,2 dB", p.explication)
        self.assertIn("+24,3 dB", p.explication)

    def test_parole_sans_reverb_meme_si_le_fichier_reste(self):
        # un ancien mix chant a pu laisser reverb_voix_convertie.wav : en parole, le mix n'en a pas
        p, _ = self.plan(["seche_voix_convertie.wav", "reverb_voix_convertie.wav"], RAPPORT_PAROLE)
        self.assertTrue(p.exact)
        self.assertIsNone(p.reverb)
        self.assertEqual(p.gain_seche, -9.6)
        self.assertIn("sans réverbération", p.explication)

    def test_reverb_absente(self):
        p, _ = self.plan(["seche_voix_convertie.wav"], RAPPORT_CHANT)
        self.assertFalse(p.exact)
        self.assertTrue(p.a_combiner)
        self.assertIsNone(p.reverb)
        self.assertIn("réverbération absente", p.explication)

    def test_gains_absents(self):
        p, _ = self.plan(["seche_voix_convertie.wav", "reverb_voix_convertie.wav"], {"mode": "chant", "mix": {}})
        self.assertFalse(p.exact)
        self.assertEqual(p.gain_seche, 0.0)
        self.assertIsNone(p.reverb)
        self.assertIn("gains du mix absents", p.explication)

    def test_rapport_abime(self):
        for rapport in ({"mix": "abîmé"}, {"mix": {"gains_db": "abîmé"}}, {"mix": {"gains_db": {"seche": True}}},
                        {"mix": {"gains_db": {"seche": "8"}}}, {"mix": {"gains_db": {"seche": float("nan")}}}, [],
                        {"mix": {"gains_db": {"seche": 3.0, "reverb": "x"}, "limite_dbtp": "x"}}):
            with self.subTest(rapport=rapport):
                p = pistes.plan_ma_voix(self.doublage(fichiers=["jules/seche_voix_convertie.wav"]).dossier_voix,
                                        rapport)
                self.assertTrue(p.possible)
                self.assertTrue(math.isfinite(p.gain_seche))
                self.assertAlmostEqual(p.limite, 10 ** (-1 / 20), places=6)

    def test_rapport_lu_sur_le_disque(self):
        r = self.doublage(fichiers=["jules/seche_voix_convertie.wav"], rapport=RAPPORT_PAROLE)
        self.assertEqual(pistes.plan_ma_voix(r.dossier_voix).gain_seche, -9.6)

    def test_ancien_doublage_voix_brute(self):
        p, r = self.plan(["voix_convertie.flac"], {"mode": "chant"})
        self.assertFalse(p.a_combiner)
        self.assertEqual(p.direct, r.dossier_voix / "voix_convertie.flac")
        self.assertIn("brute", p.explication)
        self.assertIsNone(pistes.chemin_ma_voix(p))

    def test_rien(self):
        p, _ = self.plan([], RAPPORT_CHANT)
        self.assertFalse(p.possible)
        self.assertIn("absente", p.explication)
        self.assertFalse(pistes.plan_ma_voix(None).possible)

    def test_limite_par_defaut(self):
        p, _ = self.plan(["seche_voix_convertie.wav"], {"mix": {"gains_db": {"seche": 1.0}}})
        self.assertAlmostEqual(p.limite, 10 ** (pistes.LIMITE_DEFAUT_DBTP / 20))


class EssaiGrapheEtCache(EssaiAvecCache):
    def test_graphe_du_mix_sans_la_musique(self):
        p = pistes.PlanVoix(seche=Path("/s.wav"), reverb=Path("/r.wav"), gain_seche=8.2, gain_reverb=24.3,
                            limite=10 ** (-1 / 20))
        g = pistes.graphe_ma_voix(p)
        self.assertIn("[0:a]volume=8.20dB[s]", g)
        self.assertIn("[1:a]volume=24.30dB[r]", g)
        self.assertIn("[s][r]amix=inputs=2:normalize=0:duration=first", g)
        self.assertIn("aresample=176400,alimiter=limit=0.8913:level=disabled:latency=1,aresample=44100[m]", g)
        g = pistes.graphe_ma_voix(pistes.PlanVoix(seche=Path("/s.wav"), gain_seche=-9.6))
        self.assertTrue(g.startswith("[0:a]volume=-9.60dB,aresample="))
        self.assertNotIn("amix", g)

    def test_commande(self):
        p = pistes.PlanVoix(seche=Path("/s.wav"), reverb=Path("/r.wav"), gain_seche=1, gain_reverb=2)
        c = pistes.commande_ma_voix(p, "/x/sortie.flac", duree=41.98)
        self.assertEqual(c[:3], ["nice", "-n", "10"])
        self.assertEqual(c[c.index("-t") + 1], "41.980")
        self.assertEqual(c[-3:], ["-f", "flac", "/x/sortie.flac"])
        self.assertNotIn("-t", pistes.commande_ma_voix(p, "/x/sortie.flac"))
        with self.assertRaises(pistes.ErreurPistes):
            pistes.commande_ma_voix(pistes.PlanVoix(direct=Path("/v.flac")), "/x/s.flac")

    def test_cle_du_cache(self):
        r = self.doublage(fichiers=["jules/seche_voix_convertie.wav", "jules/reverb_voix_convertie.wav",
                                    "instrumental.wav"], rapport=RAPPORT_CHANT)
        p = pistes.plan_ma_voix(r.dossier_voix, r.rapport)
        f = pistes.chemin_ma_voix(p)
        self.assertEqual(f.parent, self.cache / "voix")
        self.assertEqual(f.suffix, ".flac")
        self.assertEqual(pistes.chemin_ma_voix(pistes.plan_ma_voix(r.dossier_voix, r.rapport)), f)
        autre = json.loads(json.dumps(RAPPORT_CHANT))
        autre["mix"]["gains_db"]["seche"] = 9.0
        self.assertNotEqual(pistes.chemin_ma_voix(pistes.plan_ma_voix(r.dossier_voix, autre)), f)
        # un remix refait la voix sèche : nouvelle clé
        seche = r.dossier_voix / "seche_voix_convertie.wav"
        seche.write_bytes(b"refaite")
        self.assertNotEqual(pistes.chemin_ma_voix(pistes.plan_ma_voix(r.dossier_voix, r.rapport)), f)

    def test_purge_du_dossier(self):
        d = self.cache / "voix"
        d.mkdir(parents=True)
        for i in range(4):
            f = d / f"{i}.flac"
            f.write_bytes(b"x" * 1024 * 1024)
            os.utime(f, (1000 + i, 1000 + i))
        vieux_tmp = d / ".a.1.tmp.flac"
        vieux_tmp.write_bytes(b"x")
        os.utime(vieux_tmp, (1, 1))
        self.assertEqual(chemins.purger_dossier(d, 2), 3)      # les 2 plus anciens et le .tmp abandonné
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["2.flac", "3.flac"])
        self.assertEqual(chemins.purger_dossier(self.tmp / "absent", 1), 0)


# --- Le même son que dans le mix ---------------------------------------------------------------------------------------

def charger_doubler(tmp):
    """doubler.py copié dans tmp et chargé comme module, HOME déplacé dans tmp (rien n'est lu dans ~/.config)."""
    depot = Path(tmp) / "depot"
    (depot / "kaggle").mkdir(parents=True)
    shutil.copy2(VRAI_DOUBLER, depot / "doubler.py")
    (depot / "kaggle" / "doubler_kaggle.py").write_text("")
    home = Path(tmp) / "home"
    home.mkdir()
    with mock.patch.dict(os.environ, {"HOME": str(home)}):
        spec = importlib.util.spec_from_file_location("doubler_pour_pistes", depot / "doubler.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


@unittest.skipUnless(FFMPEG and VRAI_DOUBLER.is_file(), "ffmpeg ou doubler.py absent")
class EssaiCommeLeMix(EssaiAvecCache):
    """La voix seule refaite par l'interface est, à l'échantillon près, la part de la voix dans le mix de doubler :
    on fait mixer doubler sur une musique muette, puis on compare son mix.wav à notre voix seule."""

    def preparer_travail(self, mode):
        r = self.doublage(titre=f"Essai {mode}", rapport={"mode": mode})
        t, tv = r.travail, r.dossier_voix
        ffmpeg(t / "instrumental.wav", "aevalsrc=exprs=0|0:s=44100")                       # musique muette
        ffmpeg(t / "voix_source.wav", "sine=frequency=220:sample_rate=44100")
        ffmpeg(tv / "voix_convertie.flac", "aevalsrc=exprs=0.3*sin(2*PI*196*t)+0.15*sin(2*PI*392*t):s=44100",
               canaux=1, codec=("-c:a", "flac"))
        return r

    def comparer(self, mode):
        doubler = charger_doubler(self.tmp / "doubler")
        r = self.preparer_travail(mode)
        t, tv = r.travail, r.dossier_voix
        gains, _cible = doubler.mixer(t, tv, mode, 4.0 if mode == "chant" else 0.0, -13, tv / "voix_convertie.flac",
                                      tv / "mix.wav", version=1)
        rapport = {"mode": mode, "mix": {"version": 1, "limite_dbtp": round(20 * math.log10(doubler.LIMITE), 2),
                                         "gains_db": {k: round(v, 2) for k, v in gains.items()}}}
        ecrire(tv / "rapport.json", rapport)
        plan = pistes.plan_ma_voix(tv, rapport)
        self.assertTrue(plan.exact)
        self.assertEqual(plan.reverb is not None, mode == "chant")
        voix = pistes.preparer_ma_voix(tv, rapport)
        self.assertEqual(voix.parent, self.cache / "voix")
        mix, nous = echantillons(tv / "mix.wav"), echantillons(voix)
        self.assertEqual(len(nous), len(mix))
        ecart = max(abs(a - b) for a, b in zip(mix, nous))
        self.assertLess(ecart, 2e-4, f"écart maximal {ecart}")
        self.assertGreater(rms_db(mix), -40)          # il y a bien de la voix
        return gains

    def test_chant_voix_seche_et_reverb(self):
        gains = self.comparer("chant")
        self.assertIn("reverb", gains)

    def test_parole_voix_seche_seule(self):
        gains = self.comparer("parole")
        self.assertNotIn("reverb", gains)


@unittest.skipUnless(FFMPEG, "ffmpeg absent")
class EssaiPreparer(EssaiAvecCache):
    def test_niveau_et_cache(self):
        r = self.doublage(rapport={"mode": "parole", "mix": {"gains_db": {"seche": 6.0}, "limite_dbtp": -1.0}})
        tv = r.dossier_voix
        ffmpeg(tv / "seche_voix_convertie.wav", "sine=frequency=330:sample_rate=44100", duree=2)   # crête −18 dBFS
        ffmpeg(r.travail / "instrumental.wav", "aevalsrc=exprs=0|0:s=44100", duree=1.5)
        f = pistes.preparer_ma_voix(tv)
        self.assertTrue(f.is_file())
        self.assertAlmostEqual(duree(f), 1.5, delta=0.05)          # coupée à la durée du mix (instrumental)
        a = echantillons(f)
        source = echantillons(tv / "seche_voix_convertie.wav")
        self.assertAlmostEqual(rms_db(a) - rms_db(source[:len(a)]), 6.0, delta=0.15)
        self.assertEqual(pistes.ma_voix_prete(tv), f)
        self.assertFalse(list(f.parent.glob("*.tmp*")))
        with mock.patch.object(pistes, "_lancer") as lancer:       # deuxième fois : depuis le cache
            self.assertEqual(pistes.preparer_ma_voix(tv), f)
            lancer.assert_not_called()

    def test_rien_n_est_ecrit_dans_le_doublage(self):
        r = self.doublage(rapport=RAPPORT_PAROLE)
        ffmpeg(r.dossier_voix / "seche_voix_convertie.wav", "sine=frequency=330:sample_rate=44100", duree=1)
        avant = sorted(p.relative_to(r.dossier) for p in r.dossier.rglob("*"))
        pistes.preparer_ma_voix(r.dossier_voix)
        self.assertEqual(sorted(p.relative_to(r.dossier) for p in r.dossier.rglob("*")), avant)

    def test_pas_encore_prete(self):
        r = self.doublage(fichiers=["jules/seche_voix_convertie.wav"], rapport=RAPPORT_PAROLE)
        self.assertIsNone(pistes.ma_voix_prete(r.dossier_voix))

    def test_voix_brute_telle_quelle(self):
        r = self.doublage(fichiers=["jules/voix_convertie.flac"], rapport={"mode": "chant"})
        self.assertEqual(pistes.preparer_ma_voix(r.dossier_voix), r.dossier_voix / "voix_convertie.flac")
        self.assertEqual(pistes.ma_voix_prete(r.dossier_voix), r.dossier_voix / "voix_convertie.flac")

    def test_impossible(self):
        r = self.doublage(rapport=RAPPORT_CHANT)
        with self.assertRaises(pistes.ErreurPistes):
            pistes.preparer_ma_voix(r.dossier_voix)

    def test_fichier_illisible(self):
        r = self.doublage(fichiers=["jules/seche_voix_convertie.wav"], rapport=RAPPORT_PAROLE)   # fichier vide
        with self.assertRaises(pistes.ErreurPistes):
            pistes.preparer_ma_voix(r.dossier_voix)
        self.assertFalse(any((self.cache / "voix").glob("*")))


# --- Choix des pistes ------------------------------------------------------------------------------------------------

TOUT = ["original.wav", "instrumental.wav", "voix_source.wav", "jules/seche_voix_convertie.wav",
        "jules/reverb_voix_convertie.wav"]


class EssaiChoixDesPistes(EssaiAvecCache):
    def test_video_complete(self):
        r = self.doublage(fichiers=TOUT, rapport=RAPPORT_CHANT)
        p = pistes.choisir_pistes(r, "video")
        self.assertEqual(list(p), list(pistes.CLES))
        self.assertEqual((p["doublage"].fichier, p["doublage"].image), (r.video, True))
        self.assertEqual((p["original"].fichier, p["original"].decalage, p["original"].image),
                         (r.video_source, 12.5, True))
        self.assertEqual((p["beat"].fichier, p["beat"].image), (r.travail / "instrumental.wav", False))
        self.assertEqual(p["voix_origine"].fichier, r.travail / "voix_source.wav")
        self.assertTrue(p["ma_voix"].a_preparer and p["ma_voix"].disponible)
        self.assertIsNone(p["ma_voix"].fichier)
        for c in ("beat", "ma_voix", "voix_origine"):
            self.assertEqual(p[c].decalage, 0.0)        # calées sur le doublage
            self.assertFalse(p[c].image)
        self.assertEqual(p["beat"].libelle, "Beat seul")

    def test_ma_voix_deja_prete(self):
        r = self.doublage(fichiers=TOUT, rapport=RAPPORT_CHANT)
        f = pistes.chemin_ma_voix(pistes.plan_ma_voix(r.dossier_voix, r.rapport))
        ecrire(f, b"fausse voix en cache")
        p = pistes.choisir_pistes(r, "video")["ma_voix"]
        self.assertEqual(p.fichier, f)
        self.assertFalse(p.a_preparer)

    def test_video_d_origine_introuvable(self):
        r = self.doublage(fichiers=TOUT, rapport=RAPPORT_CHANT)
        r.video_source.unlink()
        o = pistes.choisir_pistes(r, "video")["original"]
        self.assertEqual((o.fichier, o.image, o.decalage), (r.travail / "original.wav", False, 0.0))
        self.assertIn("introuvable", o.explication)
        self.assertIn("Choisir", o.explication)
        # vidéo choisie à la main : l'image revient, au décalage de l'extrait
        choisie = ecrire(self.tmp / "ailleurs" / "Chanson fictive.mp4")
        o = pistes.choisir_pistes(r, "video", choisie)["original"]
        self.assertEqual((o.fichier, o.image, o.decalage), (choisie, True, 12.5))
        # choisie à la main, mais déplacée depuis : repli sur le son d'origine
        choisie.unlink()
        self.assertEqual(pistes.choisir_pistes(r, "video", choisie)["original"].fichier, r.travail / "original.wav")

    def test_mp3(self):
        r = self.doublage(fichiers=TOUT, rapport=RAPPORT_CHANT)
        p = pistes.choisir_pistes(r, "mp3")
        self.assertEqual(p["doublage"].fichier, r.mp3)
        self.assertEqual((p["original"].fichier, p["original"].decalage), (r.travail / "original.wav", 0.0))
        self.assertFalse(any(x.image for x in p.values()))

    def test_ancien_doublage(self):
        r = self.doublage(fichiers=["original.wav", "jules/voix_convertie.flac"], rapport={"mode": "chant"},
                          source=False)
        p = pistes.choisir_pistes(r, "video")
        self.assertIsNone(p["beat"].fichier)
        self.assertFalse(p["beat"].disponible)
        self.assertIn("instrumental.wav manque", p["beat"].explication)
        self.assertFalse(p["voix_origine"].disponible)
        self.assertEqual(p["ma_voix"].fichier, r.dossier_voix / "voix_convertie.flac")
        self.assertEqual(p["original"].fichier, r.travail / "original.wav")    # pas de source.json : le son seul

    def test_doublage_inacheve(self):
        r = self.doublage(video=False, mp3=False, rapport=None, source=False)
        p = pistes.choisir_pistes(r, "mp3")
        self.assertFalse(any(x.disponible for x in p.values()))
        self.assertTrue(all(x.explication for x in p.values()))
        self.assertIsNone(pistes.piste_apres_changement(p, "doublage"))

    def test_sans_dossier_de_voix(self):
        d = self.donnees / "sorties" / "Vide"
        ecrire(d / "travail" / "original.wav")
        r = rapports.lire_dossier(d)[0]
        self.assertIsNone(r.voix)
        p = pistes.choisir_pistes(r, "mp3")
        self.assertFalse(p["ma_voix"].disponible)
        self.assertIn("inachevé", p["ma_voix"].explication)

    def test_piste_apres_changement(self):
        r = self.doublage(fichiers=TOUT, rapport=RAPPORT_CHANT)
        p = pistes.choisir_pistes(r, "video")
        self.assertEqual(pistes.piste_apres_changement(p, "beat"), "beat")
        self.assertEqual(pistes.piste_apres_changement(p, "ma_voix"), "ma_voix")     # à préparer, mais disponible
        self.assertEqual(pistes.piste_apres_changement(p, "comparaison"), "doublage")
        r2 = self.doublage(titre="Autre", fichiers=["original.wav"], rapport=RAPPORT_CHANT)
        p2 = pistes.choisir_pistes(r2, "video")
        self.assertEqual(pistes.piste_apres_changement(p2, "beat"), "doublage")
        sans_video = pistes.choisir_pistes(self.doublage(titre="Son seul", fichiers=["original.wav"], video=False,
                                                         mp3=False, rapport=RAPPORT_CHANT), "video")
        self.assertEqual(pistes.piste_apres_changement(sans_video, "doublage"), "original")

    def test_touche_b(self):
        self.assertEqual(pistes.bascule_ab("doublage"), "original")
        self.assertEqual(pistes.bascule_ab("original"), "doublage")
        for c in ("beat", "ma_voix", "voix_origine", "comparaison", None):
            self.assertEqual(pistes.bascule_ab(c), "doublage")


# --- Temps du son, temps de la piste ---------------------------------------------------------------------------------

DEBUT_SON = 0.177       # son d'origine 0,177 s après l'image : decalage_son de source.json (vidéo entière)


def position_ref(temps, source):
    """Lecteur.position_ref (gui/lecteur.py) : la position du lecteur quand sa source (chemin, décalage, libellé) en
    est à « temps » (s, temps du fichier)."""
    return max(0.0, temps - (source[1] or 0.0))


def temps_source(position, source):
    """Lecteur._activer et Lecteur.aller_a : où le lecteur place sa source pour une position (bascule à la même
    position, retour de la fenêtre, clic sur la frise ou sur une ligne des paroles)."""
    return max(0.0, position + (source[1] or 0.0))


def sources_avant(dispo):
    """Les sources du lecteur comme la page les bâtissait avant : sans le début du son dans la vidéo doublée."""
    return {c: (p.a_lire, p.decalage, p.libelle) for c, p in dispo.items() if p.a_lire}


def resultat_source(essai, source, titre="Chanson fictive", autotune=False, copie_beat=True):
    """Un doublage fictif (essai.doublage) dont travail/source.json vaut {"video": …, **source}, avec sa version
    auto-tune (vidéo et MP3) si autotune, et la copie vidéo du beat prête en cache si copie_beat."""
    rapport = {**RAPPORT_CHANT, "autotune": {"applique": True}} if autotune else RAPPORT_CHANT
    r = essai.doublage(titre=titre, fichiers=TOUT, rapport=rapport)
    if autotune:
        for ext in (".mp4", ".mp3"):
            ecrire(r.dossier / f"{titre} - voix Jules (auto-tune){ext}")
    ecrire(r.travail / "source.json", {"video": str(r.video_source), **source})
    if copie_beat:
        ecrire(pistes.chemin_video(r.video, pistes.identite_son(r.travail / "instrumental.wav")), b"copie")
    return rapports.lire_dossier(r.dossier, {"jules": "Jules"})[0]


class EssaiTempsDuSon(EssaiAvecCache):
    """Le lecteur de la page d'un résultat compte en temps du son doublé : celui de la frise, de courbes.json, des
    analyses du son, du MP3, de travail/original.wav, du beat et des voix seules. Une piste qui a une image (vidéo
    doublée, sa version auto-tune, une copie des pistes séparées, la vidéo d'origine) compte en temps de la vidéo
    doublée, où le son commence à decalage_son de source.json : sa source est décalée d'autant (avance_piste, la règle
    de voixla.paroles.ecart_lecture)."""

    def test_regle(self):
        self.assertEqual(pistes.avance_piste(True, DEBUT_SON), DEBUT_SON)
        self.assertEqual(pistes.avance_piste(False, DEBUT_SON), 0.0)
        self.assertEqual(pistes.avance_piste(True, None), 0.0, "source.json absent : la vidéo au temps du son")
        self.assertEqual(pistes.decalage_son({"video": "x.mp4", "decalage_son": DEBUT_SON}), DEBUT_SON)
        self.assertEqual(pistes.decalage_son({"decalage_son": 0}), 0.0)
        for abime in (None, {}, {"debut": 12.5}, {"decalage_son": "0.1"}, {"decalage_son": False},
                      {"decalage_son": float("nan")}):
            self.assertIsNone(pistes.decalage_son(abime), abime)

    def test_video_entiere_au_son_en_retard(self):
        r = resultat_source(self, {"decalage_son": DEBUT_SON}, autotune=True)
        p = pistes.choisir_pistes(r, "video")
        s = pistes.sources_lecteur(p)
        for c in ("doublage", "doublage_at", "original", "beat"):
            self.assertTrue(p[c].avec_image, c)
            self.assertEqual(s[c][1], DEBUT_SON, c)
        self.assertEqual(s["beat"][0], p["beat"].video, "la copie vidéo du beat, prête en cache")
        self.assertEqual(s["original"][0], r.video_source)
        # la voix d'origine, dont la copie se prépare : le son seul sous l'image arrêtée, en temps du son
        self.assertEqual(s["voix_origine"], (r.travail / "voix_source.wav", 0.0, "Voix d’origine seule"))
        self.assertNotIn("ma_voix", s, "à combiner d'abord")
        # le son doublé à 10,0 s s'entend à 10,177 s de la vidéo doublée : la frise et la courbe reçoivent 10,0 s
        ref = position_ref(10.0 + DEBUT_SON, s["doublage"])
        self.assertAlmostEqual(ref, 10.0)
        self.assertAlmostEqual(position_ref(10.0 + DEBUT_SON, sources_avant(p)["doublage"]), 10.0 + DEBUT_SON,
                               msg="avant : 0,18 s trop tard")
        # bascules à la même position : vers le son seul, même instant du son ; touche B, même image
        self.assertAlmostEqual(temps_source(ref, s["voix_origine"]), 10.0)
        self.assertAlmostEqual(temps_source(ref, s["original"]), 10.0 + DEBUT_SON)
        self.assertAlmostEqual(temps_source(ref, s["beat"]), 10.0 + DEBUT_SON)
        # en MP3 (bascule Vidéo/MP3), tout est en temps du son
        m = pistes.choisir_pistes(r, "mp3")
        sm = pistes.sources_lecteur(m)
        self.assertEqual({c: x[1] for c, x in sm.items()}, {c: 0.0 for c in sm})
        self.assertEqual(sm, sources_avant(m))
        self.assertAlmostEqual(temps_source(ref, sm["doublage"]), 10.0)
        self.assertAlmostEqual(temps_source(position_ref(10.0, sm["doublage"]), s["doublage"]), 10.0 + DEBUT_SON)

    def test_ma_voix_prete_apres_coup(self):
        # page_resultats : la voix seule combinée s'ajoute en son seul, puis sa copie vidéo (lecteur.ajouter)
        r = resultat_source(self, {"decalage_son": DEBUT_SON})
        q = pistes.choisir_pistes(r, "video")["ma_voix"]
        q.fichier, q.a_preparer = self.cache / "voix" / "jules.flac", False
        self.assertEqual(pistes.source_lecteur(q), (q.fichier, 0.0, "Ma voix seule"))
        q.video = self.cache / "videos" / "jules.mp4"
        self.assertEqual(pistes.source_lecteur(q), (q.video, DEBUT_SON, "Ma voix seule"))

    def test_video_d_origine_deplacee_ou_choisie(self):
        r = resultat_source(self, {"decalage_son": DEBUT_SON})
        r.video_source.unlink()
        o = pistes.choisir_pistes(r, "video")["original"]
        self.assertEqual(pistes.source_lecteur(o), (r.travail / "original.wav", 0.0, "Original"))
        choisie = ecrire(self.tmp / "ailleurs" / "Chanson fictive.mp4")
        o = pistes.choisir_pistes(r, "video", choisie)["original"]
        self.assertEqual(pistes.source_lecteur(o), (choisie, DEBUT_SON, "Original"))

    def test_extrait_et_video_au_son_a_l_heure_inchanges(self):
        for i, source in enumerate(({"debut": 12.5, "duree": 40.0, "decalage_son": 0.0},     # extrait
                                    {"decalage_son": 0.0},                                    # son à l'heure
                                    {"debut": 12.5, "duree": 40.0})):                         # sans decalage_son
            r = resultat_source(self, source, titre=f"Chanson {i}", autotune=True)
            for forme in ("video", "mp3"):
                p = pistes.choisir_pistes(r, forme)
                self.assertEqual(pistes.sources_lecteur(p), sources_avant(p), (source, forme))
            s = pistes.sources_lecteur(pistes.choisir_pistes(r, "video"))
            self.assertEqual(s["original"][1], source.get("debut", 0.0), "au début de l'extrait")
            self.assertEqual(s["doublage"][1], 0.0)


# --- Fichiers enregistrés --------------------------------------------------------------------------------------------

class EssaiNoms(EssaiAvecCache):
    def test_convention_des_sorties(self):
        r = self.doublage(titre="Chanson fictive (extrait 1m00,0+0m40,0)", rapport=RAPPORT_CHANT)
        self.assertEqual(r.mp3.name, "Chanson fictive (extrait 1m00,0+0m40,0) - voix Jules.mp3")
        self.assertEqual(pistes.nom_export(r, "beat"), "Chanson fictive (extrait 1m00,0+0m40,0) - voix Jules (beat).mp3")
        self.assertEqual(pistes.nom_export(r, "ma_voix", ".flac"),
                         "Chanson fictive (extrait 1m00,0+0m40,0) - voix Jules (voix seule).flac")
        with self.assertRaises(ValueError):
            pistes.nom_export(r, "original")

    def test_sans_media(self):
        r = self.doublage(video=False, mp3=False, rapport=RAPPORT_CHANT)
        self.assertEqual(pistes.nom_export(r, "beat"), "Chanson fictive - voix Jules (beat).mp3")

    def test_exports_existants_et_ecartes_des_sorties(self):
        r = self.doublage(rapport=RAPPORT_CHANT)
        b = ecrire(r.dossier / pistes.nom_export(r, "beat"))
        v = ecrire(r.dossier / pistes.nom_export(r, "ma_voix", ".flac"))
        self.assertEqual(sorted(pistes.exports_existants(r)), sorted([b, v]))
        relu = rapports.lire_dossier(r.dossier, {"jules": "Jules"})[0]
        self.assertEqual((relu.video, relu.mp3), (r.video, r.mp3))
        # nom affiché différent de celui des fichiers : le seul doublage du dossier est encore trouvé
        relu = rapports.lire_dossier(r.dossier, {"jules": "Jules Fictif"})[0]
        self.assertEqual(relu.mp3, r.mp3)

    def test_commande_d_export(self):
        c = pistes.commande_export("/t/instrumental.wav", "/s/A - voix Jules (beat).mp3")
        self.assertEqual(c[:3], ["nice", "-n", "10"])
        self.assertIn("libmp3lame", c)
        self.assertIn("320k", c)
        self.assertIn("title=A - voix Jules (beat)", c)
        self.assertIn("flac", pistes.commande_export("/t/x.wav", "/s/y.FLAC"))
        with self.assertRaises(pistes.ErreurPistes):
            pistes.commande_export("/t/x.wav", "/s/y.ogg")


@unittest.skipUnless(FFMPEG, "ffmpeg absent")
class EssaiExporter(EssaiAvecCache):
    def test_mp3_flac_wav(self):
        src = ffmpeg(self.tmp / "beat.wav", "sine=frequency=440:sample_rate=44100", duree=1.5)
        for ext, codec in ((".mp3", "mp3"), (".flac", "flac"), (".wav", "pcm_s24le")):
            with self.subTest(ext=ext):
                sortie = self.tmp / "sorties" / f"Chanson fictive - voix Jules (beat){ext}"
                sortie.parent.mkdir(exist_ok=True)
                self.assertEqual(pistes.exporter(src, sortie), sortie)
                r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of",
                                    "csv=p=0", str(sortie)], capture_output=True, text=True, timeout=30)
                self.assertEqual(r.stdout.strip(), codec)
                self.assertAlmostEqual(duree(sortie), 1.5, delta=0.1)
        self.assertFalse(list((self.tmp / "sorties").glob(".*tmp*")))

    def test_remplace_un_fichier_existant(self):
        src = ffmpeg(self.tmp / "beat.wav", "sine=frequency=440:sample_rate=44100", duree=1)
        sortie = ecrire(self.tmp / "x.mp3", b"ancien")
        pistes.exporter(src, sortie)
        self.assertNotEqual(sortie.read_bytes(), b"ancien")

    def test_refus(self):
        src = ffmpeg(self.tmp / "beat.wav", "sine=frequency=440:sample_rate=44100", duree=1)
        with self.assertRaises(pistes.ErreurPistes):
            pistes.exporter(src, self.tmp / "x.ogg")
        with self.assertRaises(pistes.ErreurPistes):
            pistes.exporter(self.tmp / "absent.wav", self.tmp / "x.mp3")
        with self.assertRaises(pistes.ErreurPistes):
            pistes.exporter(ecrire(self.tmp / "vide.wav"), self.tmp / "y.mp3")     # illisible
        self.assertFalse((self.tmp / "y.mp3").exists())
        self.assertFalse(list(self.tmp.glob(".*tmp*")))


# --- Ligne de commande -----------------------------------------------------------------------------------------------

@unittest.skipUnless(FFMPEG, "ffmpeg absent")
class EssaiLigneDeCommande(EssaiAvecCache):
    def lancer(self, *argv):
        sortie, erreur = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(erreur):
            code = pistes.main(list(map(str, argv)))
        return code, sortie.getvalue(), erreur.getvalue()

    def test_voix_puis_export(self):
        r = self.doublage(rapport=RAPPORT_PAROLE)
        ffmpeg(r.dossier_voix / "seche_voix_convertie.wav", "sine=frequency=330:sample_rate=44100", duree=1)
        code, sortie, _ = self.lancer("voix", r.dossier_voix)
        self.assertEqual(code, 0)
        f = Path(json.loads(sortie)["voix"])
        self.assertTrue(f.is_file())
        cible = self.tmp / "export" / "Chanson fictive - voix Jules (voix seule).mp3"
        cible.parent.mkdir()
        code, sortie, _ = self.lancer("exporter", "--voix", r.dossier_voix, cible)
        self.assertEqual((code, json.loads(sortie)["sortie"]), (0, str(cible)))
        self.assertTrue(cible.is_file())
        code, _, _ = self.lancer("exporter", f, self.tmp / "export" / "copie.flac")
        self.assertEqual(code, 0)

    def test_sous_processus(self):
        # comme l'interface la lance : python3 -m voixla.pistes, sans argument valide → aide et code 2
        r = subprocess.run([sys.executable, "-m", "voixla.pistes"], capture_output=True, text=True, timeout=30,
                           cwd=chemins.INTERFACE)
        self.assertEqual(r.returncode, 2)
        self.assertIn("python3 -m voixla.pistes voix", r.stdout)

    def test_erreurs(self):
        r = self.doublage(rapport=RAPPORT_CHANT)
        code, _, erreur = self.lancer("voix", r.dossier_voix)
        self.assertEqual(code, 1)
        self.assertIn("erreur", erreur)
        self.assertEqual(self.lancer("voix")[0], 2)
        self.assertEqual(self.lancer("exporter", "--voix", r.dossier_voix)[0], 2)
        self.assertEqual(self.lancer("autre", "x", "y")[0], 2)
        self.assertEqual(self.lancer("exporter", self.tmp / "absent.wav", self.tmp / "x.mp3")[0], 1)


if __name__ == "__main__":
    unittest.main()
