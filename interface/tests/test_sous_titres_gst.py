"""Sous-titres intégrés à une vidéo : GStreamer n'en dessine jamais (gui/pistes_gst.couper_texte).

La vidéo doublée a des pistes de sous-titres de paroles (mov_text en mp4, srt en mkv), et la vidéo d'origine peut avoir
les siennes. playbin, drapeau « text » actif par défaut, dessinait la première sur l'image : on aurait dit des
sous-titres incrustés, et ils doublaient les paroles de l'interface (plein écran, « Sous-titres sur la vidéo »).

- Le fichier n'a rien d'incrusté : ses paquets d'image sont ceux de la vidéo d'origine.
- couper_texte : drapeau retiré, une fois ; playbin introuvable : faux, dit une fois.
- Sous Xvfb : un Gtk.MediaFile brut (témoin) dessine bien le texte ; un flux du lecteur (gui/lecteur.media) n'a ni
  drapeau, ni incrustation, ni texte sur l'image, en mp4 comme en mkv et sur la vidéo d'origine. Le lecteur de la page
  Résultats, dans une fenêtre, le garde après une bascule de piste audio, des déplacements (en lecture et en pause),
  le passage par la vidéo d'origine, la fenêtre fermée puis rouverte, la fermeture puis la réouverture : capture de
  son écran à chaque étape, sans un pixel de texte.

Vidéos de synthèse à image unie (le texte se voit à coup sûr), doublées par les fonctions de doubler.py
(cinq_pistes.py), paroles inventées. VOIXLA_ESSAI_CAPTURES=DOSSIER garde les captures."""

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

from voixla import rapports

from . import cinq_pistes as C
from .commun import VRAI_DOUBLER
from .test_pistes import EssaiAvecCache, charger_doubler
from .test_pistes_dans_la_video import FFMPEG, GI, INTERFACE, env_gst

ECRAN = all(shutil.which(x) for x in ("xvfb-run", "dbus-run-session", "flock"))
DUREE = 12.0
FOND = "0x305070"            # essai_sous_titres_gst.FOND
PAROLES = {"format": 1, "langue": "es", "source": "whisper-large-v3", "decalage_s": 0.0,
           "segments": [{"debut": 0.5, "fin": DUREE - 0.5, "texte": "el gato come pan",
                         "mots": [{"debut": 0.5, "fin": 1.4, "mot": "el"}]}],
           "traductions": {"fr": {"source": "nllb-200-distilled-600M",
                                  "segments": [{"debut": 0.5, "fin": DUREE - 0.5, "texte": "le chat mange du pain"}]}}}
SOUS_TITRES_ORIGINE = "1\n00:00:00,000 --> 00:00:12,000\nun sous-titre d'origine\n"


def video_unie(chemin, sous_titres=None):
    """Vidéo de synthèse : image unie FOND (640 × 360, 25 i/s), son à 440 Hz en espagnol ; sous_titres : un .srt mis
    en piste de sous-titres (mov_text en mp4)."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    entrees, cartes = [], ["-map", "0:v", "-map", "1:a"]
    if sous_titres is not None:
        entrees, cartes = ["-i", str(sous_titres)], [*cartes, "-map", "2:s", "-c:s", "mov_text",
                                                     "-metadata:s:s:0", "language=fre"]
    subprocess.run(["nice", "-n", "10", "ffmpeg", "-v", "error", "-nostdin", "-y",
                    "-f", "lavfi", "-i", f"color=c={FOND}:size=640x360:rate=25:duration={DUREE}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={DUREE}:sample_rate=44100", *entrees, *cartes,
                    "-c:v", "mpeg4", "-q:v", "3", "-g", "25", "-c:a", "aac", "-metadata:s:a:0", "language=spa",
                    str(chemin)], check=True, timeout=120)
    return chemin


def lancer(argv, env, delai=150):
    """tests.essai_sous_titres_gst sous Xvfb, sur le bus privé, dans son propre groupe de processus : (résultat,
    lignes JSON)."""
    verrou = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "voix-la-xvfb.verrou"
    cmd = ["flock", str(verrou), "dbus-run-session", f"--config-file={INTERFACE / 'outils' / 'bus-essai.conf'}", "--",
           "xvfb-run", "-a", "-s", "-screen 0 1024x768x24", "nice", "-n", "10", sys.executable, "-m",
           "tests.essai_sous_titres_gst", *map(str, argv)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=str(INTERFACE),
                         stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        sortie, erreur = p.communicate(timeout=delai)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        sortie, erreur = p.communicate()
    lignes = []
    for ligne in sortie.splitlines():
        with contextlib.suppress(ValueError):
            lignes.append(json.loads(ligne))
    return subprocess.CompletedProcess(cmd, p.returncode, sortie, erreur), lignes


def sonder(video):
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(video)],
                                     capture_output=True, text=True, check=True, timeout=60).stdout)["streams"]


def paquets_image(video):
    """Les paquets de l'image (taille et somme de contrôle de chacun, sans les temps), copiés tels quels."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(video), "-map", "0:v", "-c", "copy", "-f",
                        "framemd5", "-"], capture_output=True, text=True, check=True, timeout=60)
    return [ligne.split(",")[-2:] for ligne in r.stdout.splitlines() if ligne and not ligne.startswith("#")]


# --- couper_texte, sans GStreamer ------------------------------------------------------------------------------------

class FauxPlaybin:
    def __init__(self, drapeaux):
        self.drapeaux = drapeaux
        self.poses = []

    def find_property(self, nom):
        return object() if nom == "flags" else None

    def get_property(self, nom):
        return self.drapeaux

    def set_property(self, nom, valeur):
        self.poses.append(valeur)
        self.drapeaux = valeur


@unittest.skipUnless(GI, "GStreamer ou GTK absent")
class EssaiCouperTexte(unittest.TestCase):
    def setUp(self):
        from voixla.gui import pistes_gst
        self.pg = pistes_gst
        rustine = mock.patch.object(pistes_gst, "_texte_signale", False)
        rustine.start()
        self.addCleanup(rustine.stop)

    def test_drapeau_texte_retire_une_fois(self):
        pb = FauxPlaybin(0x1 | 0x2 | 0x4 | 0x10)              # video, audio, text, soft-volume
        with mock.patch.object(self.pg, "playbin", return_value=pb):
            self.assertFalse(self.pg.texte_coupe(object()))
            self.assertTrue(self.pg.couper_texte(object()))
            self.assertEqual(pb.drapeaux, 0x1 | 0x2 | 0x10, "les autres drapeaux restent")
            self.assertTrue(self.pg.couper_texte(object()))
            self.assertEqual(len(pb.poses), 1, "déjà coupé : rien à reposer")
            self.assertTrue(self.pg.texte_coupe(object()))

    def test_playbin_introuvable(self):
        erreurs = io.StringIO()
        with mock.patch.object(self.pg, "playbin", return_value=None), contextlib.redirect_stderr(erreurs):
            self.assertFalse(self.pg.couper_texte(object()))
            self.assertFalse(self.pg.couper_texte(object()))
            self.assertIsNone(self.pg.texte_coupe(object()))
            self.assertFalse(self.pg.couper_texte(None), "sans flux : rien à dire")
        self.assertEqual(erreurs.getvalue().count("playbin introuvable"), 1, "dit une seule fois")
        self.assertIn("sous-titres", erreurs.getvalue())

    def test_playbin_qui_refuse(self):
        pb = FauxPlaybin(0x7)
        pb.set_property = mock.Mock(side_effect=TypeError("refusé"))
        with mock.patch.object(self.pg, "playbin", return_value=pb), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.pg.couper_texte(object()))

    def test_le_lecteur_coupe_chaque_flux_a_son_ouverture(self):
        from voixla.gui import lecteur
        ouverts = []
        with mock.patch.object(self.pg, "couper_texte", side_effect=ouverts.append), \
                mock.patch.object(lecteur.Gtk.MediaFile, "new_for_filename", return_value=mock.Mock()), \
                mock.patch.object(lecteur, "lectures") as coord:
            coord.return_value.suivre.side_effect = lambda m, _p: m
            m = lecteur.media("/nulle/part.mp4", object())
        self.assertEqual(ouverts, [m])


# --- vidéos doublées : rien d'incrusté, rien de dessiné par GStreamer -------------------------------------------------

@unittest.skipUnless(FFMPEG and VRAI_DOUBLER.is_file(), "ffmpeg ou doubler.py absent")
class EssaiSousTitresGst(EssaiAvecCache):
    """Doublages faits une fois pour la classe : « Clip » en mp4, dont la vidéo d'origine a ses propres sous-titres ;
    « Serie » en mkv. Tous deux avec les paroles en pistes de sous-titres."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._dossier = tempfile.TemporaryDirectory(prefix="voixla-sous-titres-")
        base = Path(cls._dossier.name).resolve()
        doubler = charger_doubler(base / "doubler")
        srt = base / "origine.srt"
        srt.write_text(SOUS_TITRES_ORIGINE, encoding="utf-8")
        cls.source_clip = video_unie(base / "donnees" / "Vidéos" / "Clip.mp4", sous_titres=srt)
        source_serie = video_unie(base / "donnees" / "Vidéos" / "Serie.mkv")
        with mock.patch.object(C, "PAROLES", PAROLES):
            cls.clip = C.doubler_cinq_pistes(doubler, base / "donnees", "Clip", ".mp4", duree=DUREE, paroles=True,
                                             source=cls.source_clip)
            cls.serie = C.doubler_cinq_pistes(doubler, base / "donnees", "Serie", ".mkv", duree=DUREE, paroles=True,
                                              source=source_serie)

    @classmethod
    def tearDownClass(cls):
        cls._dossier.cleanup()
        super().tearDownClass()

    def res(self, dossier):
        return rapports.lire_dossier(dossier, {"jules": "Jules"})[0]

    def sortie(self):
        d = os.environ.get("VOIXLA_ESSAI_CAPTURES")
        if d:
            Path(d).mkdir(parents=True, exist_ok=True)
            return Path(d)
        return self.tmp

    def test_rien_d_incruste_dans_le_fichier(self):
        """Les paroles sont des pistes de sous-titres en texte ; l'image est celle de la vidéo d'origine, paquet pour
        paquet."""
        for dossier, codec, n in ((self.clip, "mov_text", 3), (self.serie, "subrip", 2)):
            r = self.res(dossier)
            st = [s for s in sonder(r.video) if s["codec_type"] == "subtitle"]
            self.assertEqual([s["codec_name"] for s in st], [codec] * n, r.video.name)
            self.assertEqual([s["tags"].get("language") for s in st[-2:]], ["spa", "fre"], "paroles, puis traduction")
            self.assertEqual(paquets_image(r.video), paquets_image(r.video_source), "image copiée telle quelle")

    @unittest.skipUnless(GI and ECRAN, "GStreamer, GTK, xvfb-run, dbus-run-session ou flock absent")
    def test_un_flux_du_lecteur_ne_dessine_aucun_texte(self):
        """Témoin : un Gtk.MediaFile brut dessine la première piste de sous-titres (la preuve que l'essai voit le
        texte) ; un flux du lecteur n'a ni drapeau « text », ni incrustation, ni texte sur l'image."""
        videos = [self.res(self.clip).video, self.res(self.serie).video, self.source_clip]
        res, lignes = lancer(["flux", self.sortie(), *videos], env_gst(self.cache))
        self.assertEqual(res.returncode, 0, res.stdout[-2000:] + res.stderr[-2000:])
        m = {l["nom"]: l for l in lignes if "nom" in l}
        self.assertEqual(len(m), 2 * len(videos), lignes)
        for i, v in enumerate(videos):
            brut, lec = m[f"{i}-brut"], m[f"{i}-lecteur"]
            self.assertFalse(brut["texte_coupe"], v.name)
            self.assertTrue(any(not muet for _u, muet in brut["incrustations"]), brut)
            self.assertGreater(brut["pixels_texte"], 50, f"témoin : GStreamer dessine le texte ({brut})")
            self.assertEqual(lec["playbin"], "playbin", lec)
            self.assertTrue(lec["texte_coupe"], lec)
            self.assertGreaterEqual(lec["n_text"], 1, "la vidéo a bien des pistes de sous-titres")
            self.assertEqual([x for x in lec["incrustations"] if not x[1]], [], lec)
            self.assertEqual(lec["pixels_texte"], 0, f"aucun texte sur l'image : {lec}")
            self.assertGreater(lec["pixels_fond"], 100_000, "l'image est bien là")

    @unittest.skipUnless(GI and ECRAN, "GStreamer, GTK, xvfb-run, dbus-run-session ou flock absent")
    def test_le_lecteur_ne_dessine_jamais_de_texte(self):
        """Le lecteur de la page Résultats, dans une fenêtre sous Xvfb : à chaque étape, tous les playbins ouverts ont
        le texte coupé, et la capture de l'écran du lecteur n'a aucun pixel de texte."""
        res, lignes = lancer(["lecteur", self.sortie(), self.clip], env_gst(self.cache))
        if os.environ.get("VOIXLA_ESSAI_BAVARD"):
            print("\n".join(map(str, lignes)))
        self.assertEqual(res.returncode, 0, res.stdout[-2500:] + res.stderr[-2500:])
        etapes = [l for l in lignes if "etape" in l]
        self.assertEqual([e["etape"] for e in etapes],
                         ["doublage", "beat-meme-flux", "deplacement", "pause", "original", "retour-doublage",
                          "fenetre-rouverte", "reouverture", "temoin-texte-remis"], lignes)
        temoin = etapes.pop()
        self.assertFalse(temoin["courant"]["texte_coupe"])
        self.assertGreater(temoin["pixels_texte"], 50, f"témoin : la capture voit le texte de GStreamer ({temoin})")
        for e in etapes:
            for f in e["flux"]:
                self.assertTrue(f.get("texte_coupe"), e)
                self.assertEqual([x for x in f["incrustations"] if not x[1]], [], e)
            self.assertGreaterEqual(e["courant"]["n_text"], 1, f"le flux affiché a des sous-titres : {e}")
            self.assertEqual(e["pixels_texte"], 0, f"aucun texte sur l'écran du lecteur : {e}")
            self.assertGreater(e["pixels_fond"], 50_000, f"l'image est à l'écran : {e}")
        par = {e["etape"]: e for e in etapes}
        self.assertEqual(par["beat-meme-flux"]["piste"], 2, "le beat, dans le même flux")
        self.assertEqual(len(par["original"]["flux"]), 2, "la vidéo doublée et la vidéo d'origine")
        self.assertFalse(par["pause"]["en_lecture"])
        self.assertAlmostEqual(par["pause"]["position"], 6.0, delta=0.2)


if __name__ == "__main__":
    unittest.main()
