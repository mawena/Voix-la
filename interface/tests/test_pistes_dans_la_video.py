"""Pistes audio dans la vidéo doublée (doubler ≥ 0.6.0 : Doublage, Original, Beat seul, Voix convertie seule, Voix
d'origine seule, calées à l'échantillon près).

- Repérage des pistes par leur titre et leur ordre, d'après rapport.json (pistes_audio) ou les titres des flux.
- Tout ce qui lit la vidéo doublée prend le doublage : le lecteur (playbin, current-audio), les analyses du son,
  l'export, les copies, la miniature, le son seul fenêtre fermée.
- Les bascules Beat seul, Ma voix seule et Voix d'origine seule lisent la piste de la vidéo, sur place : pas de copie à
  préparer, même flux, même position, image qui continue ; l'original reste la vidéo d'origine ; un ancien doublage
  à une piste garde les copies.
- Extrait dont la copie de l'image part d'une image clé antérieure : le son doublé commence à decalage_s de
  paroles.json, pas à decalage_son de source.json.

Doublages de synthèse faits par les fonctions de doubler.py (voir cinq_pistes.py), dans un dossier temporaire ; le
cache va dans le dossier de l'essai (VOIXLA_CACHE). La lecture GStreamer tourne dans un processus à part, sans aucun son
(fakeaudiosink), le lecteur de la page sous Xvfb et sur un bus de session privé (outils/bus-essai.conf)."""

import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voixla import gstreamer, pistes, preparation, rapports
from voixla import lecture as regles

from . import cinq_pistes as C
from .commun import VRAI_DOUBLER
from .test_pistes import RAPPORT_CHANT, EssaiAvecCache, charger_doubler, ecrire

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
ICI = Path(__file__).resolve().parent
INTERFACE = ICI.parent
TITRES = ["Doublage (voix Jules)", "Original", "Beat seul", "Voix convertie seule (Jules)", "Voix d'origine seule"]
UNE_TRAME = 1024


def gi_disponible():
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst, Gtk  # noqa: F401
        return True
    except (ImportError, ValueError):
        return False


GI = gi_disponible()


# --- Repérage des pistes -------------------------------------------------------------------------------------------

class EssaiReperage(unittest.TestCase):
    def test_les_cinq_pistes(self):
        self.assertEqual(pistes.reperer_pistes(TITRES),
                         {"doublage": 0, "original": 1, "beat": 2, "ma_voix": 3, "voix_origine": 4})

    def test_voix_par_chanteur_et_auto_tune(self):
        duo = ["Doublage (voix Jules et Marie)", "Original", "Beat seul", "Voix converties seules (Jules et Marie)",
               "Voix d'origine seule"]
        self.assertEqual(pistes.reperer_pistes(duo)["ma_voix"], 3)
        at = ["Doublage auto-tune (voix Jules)", "Original", "Beat seul", "Voix convertie seule auto-tune (Jules)",
              "Voix d’origine seule"]                   # apostrophe typographique
        self.assertEqual(pistes.reperer_pistes(at),
                         {"doublage": 0, "original": 1, "beat": 2, "ma_voix": 3, "voix_origine": 4})

    def test_par_le_titre_pas_par_la_position(self):
        # la voix convertie seule a échoué chez doubler : quatre pistes, la voix d'origine en 3
        quatre = ["Doublage (voix Jules)", "Original", "Beat seul", "Voix d'origine seule"]
        self.assertEqual(pistes.reperer_pistes(quatre), {"doublage": 0, "original": 1, "beat": 2, "voix_origine": 3})
        # un titre inconnu ne décale pas les autres ; la première piste de chaque sorte compte
        self.assertEqual(pistes.reperer_pistes(["Doublage (voix Jules)", "Commentaire", "Beat seul", "Beat seul"]),
                         {"doublage": 0, "beat": 2})
        self.assertEqual(pistes.cle_du_titre("Beat seulement"), None)
        self.assertEqual(pistes.cle_du_titre("  beat   SEUL "), "beat")

    def test_rien_en_plus(self):
        for titres in ([], ["Doublage (voix Jules)"], ["Original", "Beat seul"], None, "Beat seul", [3, None]):
            self.assertEqual(pistes.reperer_pistes(titres), {}, titres)

    def test_le_rapport_l_emporte_sur_les_flux(self):
        r = rapports.Resultat(dossier=Path("/x"), voix="jules", rapport={"pistes_audio": {"reglage": "toutes",
                                                                                          "video": TITRES}})
        self.assertEqual(pistes.pistes_de_la_video(r)["voix_origine"], 4)
        self.assertEqual(pistes.pistes_de_la_video(r, autotune=True), {}, "pas de vidéo auto-tune dans le rapport")
        r.rapport = {"pistes_audio": {"reglage": "doublage", "video": ["Doublage (voix Jules)"]}}
        self.assertEqual(pistes.pistes_de_la_video(r, titres_flux=TITRES), {}, "le rapport dit : doublage seul")
        r.rapport = {"mode": "chant"}                    # rapport d'avant les cinq pistes : les titres des flux
        self.assertEqual(pistes.pistes_de_la_video(r, titres_flux=TITRES)["beat"], 2)
        self.assertEqual(pistes.pistes_de_la_video(r), {})

    def test_cles_auto_tune(self):
        self.assertEqual(pistes.cle_ecoutee("beat", True), "beat")
        self.assertEqual(pistes.cle_ecoutee("beat", True, {"beat_at": 1}), "beat_at")
        self.assertEqual(pistes.cle_ecoutee("beat", False, {"beat_at": 1}), "beat")
        self.assertEqual(pistes.cle_ecoutee("ma_voix", True, {}), "ma_voix_at")
        for at, base in (("beat_at", "beat"), ("voix_origine_at", "voix_origine"), ("original_at", "original"),
                         ("doublage_at", "doublage")):
            self.assertEqual(pistes.cle_de_base(at), base)

    def test_source_d_analyse(self):
        self.assertEqual(pistes.source_analyse("/v/a.mp4"), "/v/a.mp4")
        self.assertEqual(pistes.source_analyse("/v/a.mp4", 0), "/v/a.mp4", "la première piste : le fichier seul")
        s = pistes.source_analyse("/v/a#b.mp4", 3)
        self.assertEqual(pistes.separer_source(s), ("/v/a#b.mp4", 3))
        self.assertEqual(pistes.separer_source("/v/a.mp4"), ("/v/a.mp4", 0))
        self.assertEqual(pistes.separer_source("/v/a.mp4#piste-audio=x"), ("/v/a.mp4#piste-audio=x", 0))


# --- Début du son doublé dans la vidéo doublée -----------------------------------------------------------------------

class EssaiDebutDuSon(EssaiAvecCache):
    def test_decalage_des_paroles_d_abord(self):
        r = self.doublage(fichiers=["original.wav"], rapport=RAPPORT_CHANT)
        ecrire(r.travail / "source.json", {"video": str(r.video_source), "debut": 12.5, "duree": 40.0,
                                           "decalage_son": 0.0})
        r = rapports.lire_dossier(r.dossier, {"jules": "Jules"})[0]
        self.assertEqual(pistes.debut_son_video(r), 0.0, "sans paroles.json : decalage_son de source.json")
        ecrire(r.dossier_voix / "paroles.json", {"format": 1, "decalage_s": 0.96, "segments": []})
        self.assertEqual(pistes.debut_son_video(r), 0.96)
        # ancien extrait mkv (−0,13 s donnés à ffmpeg, annulés par le conteneur), valeurs abîmées : source.json
        for abime in (-0.1314, "0.5", None, float("inf"), True):
            pistes._DECALAGES.clear()
            ecrire(r.dossier_voix / "paroles.json", {"format": 1, "segments": [], "decalage_s": abime})
            self.assertEqual(pistes.debut_son_video(r), 0.0, abime)
        pistes._DECALAGES.clear()
        (r.dossier_voix / "paroles.json").write_text("{abîmé", encoding="utf-8")
        self.assertEqual(pistes.debut_son_video(r), 0.0)
        r.voix = None                                       # doublage inachevé, sans dossier de voix
        self.assertEqual(pistes.debut_son_video(r), 0.0)

    def test_une_seule_regle_pour_les_pistes_et_la_video_d_origine(self):
        r = self.doublage(fichiers=["original.wav", "instrumental.wav", "voix_source.wav"], rapport=RAPPORT_CHANT)
        ecrire(r.travail / "source.json", {"video": str(r.video_source), "debut": 12.5, "duree": 40.0,
                                           "decalage_son": 0.0})
        ecrire(r.dossier_voix / "paroles.json", {"format": 1, "decalage_s": 0.477, "segments": []})
        r = rapports.lire_dossier(r.dossier, {"jules": "Jules"})[0]
        ecrire(pistes.chemin_video(r.video, pistes.identite_son(r.travail / "instrumental.wav")), b"copie")
        s = pistes.sources_lecteur(pistes.choisir_pistes(r, "video"))
        self.assertEqual(s["doublage"][1], 0.477)
        self.assertEqual(s["beat"][1], 0.477, "la copie garde les temps de la vidéo doublée")
        self.assertEqual(s["original"][1], 12.5, "la vidéo d'origine : début de l'extrait, sans le décalage")
        self.assertEqual(s["voix_origine"][1], 0.0, "son seul : temps du son")
        self.assertEqual({x[1] for x in pistes.sources_lecteur(pistes.choisir_pistes(r, "mp3")).values()}, {0.0})


# --- Doublages de synthèse à cinq pistes -----------------------------------------------------------------------------

@unittest.skipUnless(FFMPEG and VRAI_DOUBLER.is_file(), "ffmpeg ou doubler.py absent")
class EssaiCinqPistes(EssaiAvecCache):
    """Doublages faits une fois pour la classe : mp4 entier à cinq pistes, le même au doublage seul, extrait mkv dont
    la copie de l'image part d'une image clé antérieure."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._dossier = tempfile.TemporaryDirectory(prefix="voixla-cinq-pistes-")
        base = Path(cls._dossier.name).resolve()
        doubler = charger_doubler(base / "doubler")
        cls.d = {}
        for nom, ext, k in (("Clip", ".mp4", {}), ("Seul", ".mp4", {"pistes": "doublage"}),
                            ("Avance", ".mkv", {"extrait": (2.5, 3.0), "duree": 8.0, "paroles": True,
                                                "image_cle": lambda v, i, t: t})):
            cls.d[nom] = C.doubler_cinq_pistes(doubler, base / "donnees", nom, ext, **k)

    @classmethod
    def tearDownClass(cls):
        cls._dossier.cleanup()
        super().tearDownClass()

    def res(self, nom):
        return rapports.lire_dossier(self.d[nom], {"jules": "Jules"})[0]

    def corr(self, son, ref):
        return C.position(son, ref)

    # --- les pistes et leur contenu -------------------------------------------------------------------------------

    def test_chaque_piste_reperee_porte_son_son(self):
        r = self.res("Clip")
        idx = pistes.pistes_de_la_video(r)
        self.assertEqual(idx, {"doublage": 0, "original": 1, "beat": 2, "ma_voix": 3, "voix_origine": 4})
        ma_voix = pistes.preparer_ma_voix(r.dossier_voix)
        refs = {c: C.decoder(C.reference(r.dossier, c)) for c in ("doublage", "original", "beat", "voix_origine")}
        refs["ma_voix"] = C.decoder(ma_voix)
        debuts = {}
        for cle, k in idx.items():
            son = C.decoder(r.video, f"0:a:{k}")
            mesures = {c: self.corr(son, ref) for c, ref in refs.items()}
            self.assertEqual(max(mesures, key=lambda c: mesures[c][1]), cle, f"piste {k} : {mesures}")
            self.assertGreater(mesures[cle][1], 0.9)
            debuts[cle] = mesures[cle][0]
        self.assertLessEqual(max(debuts.values()) - min(debuts.values()), 44, debuts)    # 1 ms
        self.assertEqual(pistes.titres_des_flux(r.video), TITRES, "titres des flux, en mp4 par handler_name")

    def test_beat_et_voix_seules_sur_place_sans_copie(self):
        r = self.res("Clip")
        p = pistes.choisir_pistes(r, "video")
        for cle, k in (("beat", 2), ("ma_voix", 3), ("voix_origine", 4)):
            q = p[cle]
            self.assertTrue(q.dans_la_video, cle)
            self.assertEqual((q.a_lire, q.piste_audio, q.copie, q.video_a_preparer), (r.video, k, None, False), cle)
            self.assertTrue(q.disponible and q.avec_image)
            self.assertEqual(q.decalage_lu, p["doublage"].decalage_lu, "même temps que le doublage")
            self.assertIn("de la vidéo doublée", q.explication)
        self.assertIsNone(p["ma_voix"].fichier, "ma voix seule n'a pas à être combinée pour être lue")
        self.assertEqual(p["beat"].fichier, r.travail / "instrumental.wav", "le son seul reste là (fenêtre fermée)")
        self.assertEqual(p["doublage"].piste_audio, 0)
        self.assertEqual(pistes.pistes_audio_lecteur(p), {"doublage": 0, "beat": 2, "ma_voix": 3, "voix_origine": 4})
        # l'original reste la vidéo d'origine, avec son propre calage
        self.assertEqual((p["original"].a_lire, p["original"].piste_audio), (r.video_source, None))
        # rien à préparer à l'avance que la miniature
        self.assertEqual([t.nom for t in preparation.taches(r)], ["miniature"])
        self.assertFalse((self.cache / "videos").exists())
        # en MP3 : les fichiers comme avant
        m = pistes.choisir_pistes(r, "mp3")
        self.assertEqual(pistes.pistes_audio_lecteur(m), {})
        self.assertEqual(m["beat"].a_lire, r.travail / "instrumental.wav")
        self.assertTrue(m["ma_voix"].a_preparer)
        # sans le playbin (dans_la_video faux) : les copies, comme un ancien doublage
        sans = pistes.choisir_pistes(r, "video", dans_la_video=False)
        self.assertEqual(pistes.pistes_audio_lecteur(sans), {})
        self.assertTrue(sans["beat"].video_a_preparer)

    def test_original_sans_la_video_d_origine(self):
        r = self.res("Clip")
        with mock.patch.object(rapports.Resultat, "source_presente", property(lambda s: False)):
            p = pistes.choisir_pistes(r, "video")
            o = p["original"]
            self.assertEqual((o.a_lire, o.piste_audio, o.decalage_lu), (r.video, 1, p["doublage"].decalage_lu))
            self.assertIn("piste « Original »", o.explication)
            self.assertEqual(pistes.pistes_audio_lecteur(p)["original"], 1)
            # vidéo d'origine choisie à la main : c'est elle
            choisie = ecrire(self.tmp / "ailleurs" / "Clip.mp4", r.video_source.read_bytes())
            o = pistes.choisir_pistes(r, "video", choisie)["original"]
            self.assertEqual((o.a_lire, o.piste_audio), (choisie, None))

    def test_ancien_doublage_a_une_piste_inchange(self):
        r = self.res("Seul")
        self.assertEqual(pistes.pistes_de_la_video(r), {})
        p = pistes.choisir_pistes(r, "video")
        self.assertEqual(pistes.pistes_audio_lecteur(p), {})
        for c in pistes.SANS_IMAGE:
            self.assertFalse(p[c].dans_la_video, c)
            self.assertTrue(p[c].video_a_preparer, c)
        self.assertEqual([t.nom for t in preparation.taches(r)], ["beat", "ma_voix", "voix_origine", "miniature"])
        # rapport ancien (sans pistes_audio) : les titres de ses flux, une seule piste, rien en plus
        r.rapport = {k: v for k, v in r.rapport.items() if k != "pistes_audio"}
        pistes._TITRES.clear()
        self.assertTrue(pistes.a_sonder(r))
        self.assertEqual(pistes.titres_des_flux(r.video), ["Doublage (voix Jules)"])
        self.assertFalse(pistes.a_sonder(r))
        self.assertEqual(pistes.pistes_audio_lecteur(pistes.choisir_pistes(r, "video")), {})

    def test_rapport_ancien_les_titres_des_flux(self):
        r = self.res("Clip")
        r.rapport = {k: v for k, v in r.rapport.items() if k != "pistes_audio"}
        pistes._TITRES.clear()
        self.assertTrue(pistes.a_sonder(r))
        self.assertEqual(pistes.pistes_audio_lecteur(pistes.choisir_pistes(r, "video")), {}, "pas encore lus")
        p = pistes.choisir_pistes(r, "video", sonder=True)
        self.assertEqual(pistes.pistes_audio_lecteur(p), {"doublage": 0, "beat": 2, "ma_voix": 3, "voix_origine": 4})
        self.assertFalse(pistes.a_sonder(r))

    # --- ce qui lit la vidéo doublée prend le doublage ----------------------------------------------------------------

    def test_analyses_export_copies_miniature_prennent_le_doublage(self):
        from voixla import analyse
        r = self.res("Clip")
        mix, beat = (C.decoder(C.reference(r.dossier, c)) for c in ("doublage", "beat"))
        # analyses du son : la vidéo seule → le doublage ; la piste 2 → le beat
        for source, ref in ((str(r.video), mix), (pistes.source_analyse(r.video, 2), beat)):
            son = analyse.decoder(source, sr=C.SR)
            self.assertGreater(self.corr(son.astype(float), ref)[1], 0.9, source)
        self.assertNotEqual(analyse.signature(r.video), analyse.signature(pistes.source_analyse(r.video, 2)))
        self.assertEqual(analyse.signature(r.video), analyse.signature(pistes.source_analyse(r.video, 0)))
        # export : la vidéo seule → le doublage ; --piste-audio 2 → le beat
        for n, ref in ((None, mix), (2, beat)):
            sortie = self.tmp / f"export-{n}.wav"
            pistes.exporter(r.video, sortie, n)
            self.assertGreater(self.corr(C.decoder(sortie), ref)[1], 0.9, n)
        with contextlib.redirect_stdout(io.StringIO()):
            code = pistes.main(["exporter", str(r.video), str(self.tmp / "cli.wav"), "--piste-audio", "2"])
        self.assertEqual(code, 0)
        self.assertGreater(self.corr(C.decoder(self.tmp / "cli.wav"), beat)[1], 0.9)
        # copies (anciens doublages, ou playbin introuvable) : calées sur le doublage, la piste par défaut
        info = pistes.sonder(r.video)
        son = pistes.flux_son(info)
        self.assertEqual((son["index"], son["tags"].get("handler_name")), (1, TITRES[0]))
        copie = pistes.preparer_video(r.video, son=r.travail / "instrumental.wav")
        flux = [s for s in pistes.sonder(copie)["streams"] if s["codec_type"] == "audio"]
        self.assertEqual(len(flux), 1)
        d_mix = self.corr(C.decoder(r.video), mix)[0]
        d_copie = self.corr(C.decoder(copie), beat)[0]
        self.assertLessEqual(abs(d_mix - d_copie), 48, "le beat de la copie part où part le doublage")
        # miniature : l'image seule
        self.assertTrue(pistes.preparer_miniature(r.video).is_file())

    def test_son_seul_fenetre_fermee(self):
        r = self.res("Clip")
        video = pistes.choisir_pistes(r, "video")
        sons = pistes.choisir_pistes(r, "mp3")
        s = pistes.sources_lecteur(video)
        self.assertEqual(regles.source_son("doublage", sons, s["doublage"])[0], r.mp3, "le MP3 : le doublage seul")
        self.assertEqual(regles.source_son("beat", sons, s["beat"])[0], r.travail / "instrumental.wav")
        self.assertEqual(regles.source_son("voix_origine", sons, s["voix_origine"])[0], r.travail / "voix_source.wav")
        self.assertIsNone(regles.source_son("ma_voix", sons, s["ma_voix"]), "pas encore combinée : la vidéo continue")
        f = pistes.preparer_ma_voix(r.dossier_voix)
        self.assertEqual(regles.source_son("ma_voix", pistes.choisir_pistes(r, "mp3"), s["ma_voix"])[0], f)
        for cle in ("beat", "voix_origine"):
            self.assertEqual(regles.source_son(cle, sons, s[cle])[1], 0.0, "le son seul compte en temps du son")
        self.assertEqual(pistes.cle_de_base("beat_at"), "beat", "le beat de la vidéo auto-tune : même son seul")

    def test_enregistrer_extrait_la_piste_de_la_video(self):
        r = self.res("Clip")
        p = pistes.choisir_pistes(r, "video")
        self.assertEqual(pistes.source_export(p["beat"]), (r.travail / "instrumental.wav", None),
                         "le beat : son fichier de travail, sans perte, rien à calculer")
        self.assertEqual(pistes.source_export(p["ma_voix"]), (r.video, 3), "ma voix seule : la piste, sans recalcul")
        f = pistes.preparer_ma_voix(r.dossier_voix)
        p = pistes.choisir_pistes(r, "video")
        self.assertEqual(pistes.source_export(p["ma_voix"]), (f, None), "déjà combinée : son FLAC")
        ancien = pistes.choisir_pistes(self.res("Seul"), "video")
        self.assertIsNone(pistes.source_export(ancien["ma_voix"]), "ancien doublage : combinée d'abord")

    # --- extrait dont la copie de l'image part d'une image clé antérieure --------------------------------------------

    def test_extrait_image_sans_son_au_debut(self):
        r = self.res("Avance")
        dec = json.loads((r.dossier_voix / "paroles.json").read_text())["decalage_s"]
        self.assertEqual(r.source.get("decalage_son"), 0.0)
        self.assertGreater(dec, 0.3, "la copie de l'image part d'une image clé antérieure")
        # où commence vraiment le son doublé dans la vidéo doublée : premier paquet de son + amorce, et corrélation
        info = pistes.sonder(r.video)
        c = pistes.calage_video(info, pistes.premier_paquet(r.video, pistes.flux_son(info)["index"]))
        self.assertAlmostEqual(c.debut_son, dec, delta=0.003)
        self.assertEqual(pistes.debut_son_video(r), dec)
        p = pistes.choisir_pistes(r, "video")
        s = pistes.sources_lecteur(p)
        self.assertEqual(s["doublage"][1], dec)
        self.assertEqual(s["beat"][1], dec, "piste de la vidéo : même départ")
        self.assertAlmostEqual(s["original"][1], r.source["debut"], places=6)
        # la règle d'avant (decalage_son de source.json) aurait décalé paroles, frise et bascules de près de 0,5 s
        self.assertGreater(abs(s["doublage"][1] - (pistes.decalage_son(r.source) or 0.0)), 0.3)


# --- Lecture GStreamer (processus à part, sans son) ------------------------------------------------------------------

def env_gst(cache):
    env = {k: v for k, v in os.environ.items()}
    env.pop("GST_PLUGIN_FEATURE_RANK", None)
    gstreamer.preparer(env)
    # aucune sortie son réelle : autoaudiosink prend fakeaudiosink
    env["GST_PLUGIN_FEATURE_RANK"] = ",".join(x for x in (env.get("GST_PLUGIN_FEATURE_RANK"), "fakeaudiosink:MAX") if x)
    env.update({"VOIXLA_CACHE": str(cache), "GTK_A11Y": "none", "NO_AT_BRIDGE": "1", "GIO_USE_VFS": "local",
                "GSK_RENDERER": "cairo", "ADW_DISABLE_PORTAL": "1",
                "PYTHONPATH": os.pathsep.join([str(INTERFACE)] + ([os.environ["PYTHONPATH"]]
                                                                    if os.environ.get("PYTHONPATH") else []))})
    return env


def lancer_essai(argv, env, ecran=False, delai=120):
    cmd = ["nice", "-n", "10", sys.executable, "-m", "tests.essai_lecteur_gst", *map(str, argv)]
    if ecran:
        verrou = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "voix-la-xvfb.verrou"
        cmd = ["flock", str(verrou), "dbus-run-session", f"--config-file={INTERFACE / 'outils' / 'bus-essai.conf'}",
               "--", "xvfb-run", "-a", "-s", "-screen 0 640x480x24", *cmd]
    else:
        env = {k: v for k, v in env.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    # un groupe de processus à lui : s'il ne finit pas, tout est tué (Xvfb et le bus privé compris)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=str(INTERFACE),
                         stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        sortie, erreur = p.communicate(timeout=delai)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        sortie, erreur = p.communicate()
    r = subprocess.CompletedProcess(cmd, p.returncode, sortie, erreur)
    lignes = []
    for l in r.stdout.splitlines():
        with contextlib.suppress(ValueError):
            lignes.append(json.loads(l))
    return r, lignes


@unittest.skipUnless(FFMPEG and VRAI_DOUBLER.is_file() and GI, "ffmpeg, doubler.py ou GStreamer absent")
class EssaiLectureGst(EssaiAvecCache):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._dossier = tempfile.TemporaryDirectory(prefix="voixla-gst-")
        base = Path(cls._dossier.name).resolve()
        cls.clip = C.doubler_cinq_pistes(charger_doubler(base / "doubler"), base / "donnees", "Clip", ".mp4",
                                         duree=16.0)

    @classmethod
    def tearDownClass(cls):
        cls._dossier.cleanup()
        super().tearDownClass()

    def test_le_lecteur_lit_le_doublage(self):
        """Gtk.MediaFile (le lecteur de l'interface) : son playbin est retrouvé, il lit la piste 0, et c'est bien le
        doublage qu'on entend (corrélation avec chaque piste de la vidéo)."""
        r = rapports.lire_dossier(self.clip, {"jules": "Jules"})[0]
        res, lignes = lancer_essai(["flux", r.video], env_gst(self.cache))
        self.assertEqual(res.returncode, 0, res.stdout[-1500:] + res.stderr[-1500:])
        mesures = {k: v for l in lignes for k, v in l.items()}
        self.assertEqual((mesures["structure"], mesures["playbin"], mesures["n_audio"], mesures["current_audio"]),
                         (True, "playbin", 5, 0), lignes)
        self.assertEqual(mesures["entendue"], 0, lignes)
        self.assertGreater(mesures["part"], 0.95, lignes)
        self.assertGreater(mesures["images"], 10, "l'image est décodée")

    @unittest.skipUnless(shutil.which("xvfb-run") and shutil.which("dbus-run-session") and shutil.which("flock"),
                         "xvfb-run, dbus-run-session ou flock absent")
    def test_bascules_dans_le_meme_flux(self):
        """Le lecteur de la page Résultats : Beat seul, Voix d'origine seule et retour au doublage changent de piste
        dans le même flux (current-audio) : même position, nouvelle piste entendue en moins de 0,45 s, calée à une
        trame près, image qui continue. L'original ouvre la vidéo d'origine, et le retour retrouve le flux du
        doublage. Fenêtre fermée : le son seul du beat (son fichier), puis retour sur la vidéo, dans un seul flux, sur
        la piste du beat. En pause, la piste attend la reprise (playbin se bloquerait au déplacement suivant)."""
        res, lignes = lancer_essai(["lecteur", self.clip], env_gst(self.cache), ecran=True)
        if os.environ.get("VOIXLA_ESSAI_BAVARD"):
            print("\n".join(map(str, lignes)))
        self.assertEqual(res.returncode, 0, res.stdout[-2500:] + res.stderr[-2500:])
        m = {}
        for l in lignes:
            if "bascule" in l:
                m.setdefault("bascules", []).append(l)
            else:
                m.update(l)
        self.assertEqual(m["pistes_audio"], {"doublage": 0, "beat": 2, "ma_voix": 3, "voix_origine": 4})
        self.assertTrue(m["meme_flux"])
        self.assertGreater(m["calage"], 0.95, lignes)
        self.assertEqual([b["bascule"] for b in m["bascules"]], ["beat", "voix_origine", "doublage"])
        for b in m["bascules"]:
            self.assertIsNotNone(b["latence"], b)
            self.assertLess(b["latence"], 0.45, b)
            self.assertGreater(b["fidele"], 0.95, b)
            self.assertLess(abs(b["position_apres"] - b["position_avant"]), 0.15, b)
            self.assertGreater(b["images_pendant"], 6, f"l'image continue : {b}")
        self.assertEqual(m["flux_ouverts"], 2, "la vidéo doublée (toutes ses pistes) et la vidéo d'origine")
        self.assertLess(abs(m["position_origine"] - m["position_origine_avant"]), 1.5)
        self.assertTrue(m["son_seul"].endswith("instrumental.wav"), m)
        self.assertEqual(m["son_seul_en_cours"], m["son_seul"])
        self.assertTrue(m["retour_flux_d_avant"], "retour sur le flux de la vidéo resté ouvert, qui a déjà la piste")
        self.assertEqual((m["retour_piste_en_attente"], m["retour_piste_immediate"]), (None, 2))
        self.assertTrue(m["retour_meme_flux"])
        self.assertTrue(m["retour_en_video"].endswith(".mp4"))
        self.assertEqual(m["current_audio_retour"], 2)
        self.assertEqual(m["flux_ouverts_retour"], 2)
        self.assertTrue(m["en_lecture"])
        self.assertGreater(m["position_retour"], m["position_son"])
        # en pause : la piste attend la reprise (changer current-audio en pause bloque playbin au déplacement suivant)
        self.assertEqual((m["pause_voulue"], m["pause_lue"]), (4, 2))
        self.assertEqual((m["reprise_lue"], m["reprise_voulue"]), (4, None))
        self.assertTrue(m["en_lecture_reprise"])
        self.assertAlmostEqual(m["position_reprise"], 4.0, delta=0.4, msg="déplacé à 3 s en pause, puis 1 s de lecture")


if __name__ == "__main__":
    unittest.main()
