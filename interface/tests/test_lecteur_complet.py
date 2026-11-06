"""Le lecteur complet d'un résultat : vitesse (la hauteur gardée), boucle A-B, sauts, reprise à la dernière position,
temps restant (voixla/lecture.py, sans GTK) ; la vitesse dans MPRIS ; les préférences et les captures d'image
(voixla/chemins.py) ; et, dans un processus à part sous Xvfb et sans aucun son, le lecteur lui-même
(tests/essai_lecteur_complet.py)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from gi.repository import GLib

from voixla import chemins, mpris
from voixla import lecture as regles

from .commun import EssaiIsole
from .test_pistes_dans_la_video import GI, env_gst

FFMPEG = shutil.which("ffmpeg")
ICI = Path(__file__).resolve().parent
INTERFACE = ICI.parent


class EssaiVitesse(unittest.TestCase):
    def test_bornes_et_voisines(self):
        self.assertEqual(regles.borner_vitesse(3), 2.0)
        self.assertEqual(regles.borner_vitesse(0.1), 0.5)
        for abime in (None, "vite", float("nan"), True):
            self.assertEqual(regles.borner_vitesse(abime), 1.0, abime)
        self.assertEqual(regles.vitesse_voisine(1.0, 1), 1.1)
        self.assertEqual(regles.vitesse_voisine(1.0, -1), 0.9)
        self.assertEqual(regles.vitesse_voisine(2.0, 1), 2.0, "déjà au plus vite")
        self.assertEqual(regles.vitesse_voisine(0.5, -1), 0.5, "déjà au plus lent")
        self.assertEqual(regles.vitesse_voisine(1.3, 1), 1.5, "entre deux crans : le suivant")
        self.assertEqual(regles.vitesse_voisine(1.3, -1), 1.25)

    def test_libelles(self):
        self.assertEqual([regles.libelle_vitesse(v) for v in (1.0, 0.75, 1.5, 2, 1.25)],
                         ["1×", "0,75×", "1,5×", "2×", "1,25×"])


class EssaiBoucle(unittest.TestCase):
    def test_trois_appuis(self):
        b = regles.boucle_suivante((), 10.0)
        self.assertEqual(b, (10.0, None), "A posé")
        self.assertFalse(regles.boucle_complete(b))
        self.assertEqual(regles.boucle_suivante(b, 10.1), b, "trop près de A : rien ne change")
        b = regles.boucle_suivante(b, 4.0)
        self.assertEqual(b, (4.0, 10.0), "B avant A : remis dans l'ordre")
        self.assertTrue(regles.boucle_complete(b))
        self.assertEqual(regles.boucle_suivante(b, 3.0), (), "troisième appui : effacée")

    def test_retour_a_A(self):
        b = (4.0, 10.0)
        self.assertIsNone(regles.retour_boucle(b, 9.9))
        self.assertEqual(regles.retour_boucle(b, 10.0), 4.0)
        self.assertEqual(regles.retour_boucle(b, 30.0), 4.0, "au-delà de B (déplacement) : retour à A")
        self.assertIsNone(regles.retour_boucle((4.0, None), 30.0), "A seul : pas de boucle")
        self.assertIsNone(regles.retour_boucle((), 30.0))

    def test_libelles(self):
        self.assertEqual(regles.libelle_boucle(()), "")
        self.assertEqual(regles.libelle_boucle((62.0, None)), "A 1:02")
        self.assertEqual(regles.libelle_boucle((62.0, 70.5)), "A-B 1:02 → 1:10")


class EssaiTemps(unittest.TestCase):
    def test_temps_restant(self):
        self.assertEqual(regles.temps_restant(30, 214), "−3:04")
        self.assertEqual(regles.temps_restant(300, 214), "−0:00")
        self.assertEqual(regles.temps_restant(30, None), "")
        self.assertEqual(regles.temps_restant(None, 60), "−1:00")

    def test_touches_des_pistes(self):
        from voixla import pistes
        self.assertEqual(regles.TOUCHES_PISTES, pistes.CLES, "touches 1 à 5 : l'ordre des bascules")


class EssaiReprise(unittest.TestCase):
    def test_position_a_garder(self):
        self.assertIsNone(regles.position_a_garder(3.0, 200), "au début : on reprendra au début")
        self.assertIsNone(regles.position_a_garder(195.0, 200), "à la fin : fini")
        self.assertEqual(regles.position_a_garder(42.123, 200), 42.12)
        self.assertEqual(regles.position_a_garder(42.0, None), 42.0, "durée inconnue")
        for abime in (None, "x", float("nan"), float("inf"), True):
            self.assertIsNone(regles.position_a_garder(abime, 200), abime)

    def test_memoriser_et_reprendre(self):
        cle = regles.cle_reprise("/sorties/Ciel", "jules")
        self.assertEqual(cle, "/sorties/Ciel|jules")
        p = regles.memoriser_position({}, cle, 42.0, 200, maintenant=10)
        self.assertEqual(regles.position_reprise(p, cle, 200), 42.0)
        self.assertIsNone(regles.position_reprise(p, "autre|x", 200))
        p2 = regles.memoriser_position(p, cle, 199.0, 200, maintenant=11)
        self.assertNotIn(cle, p2, "lu jusqu'au bout : oublié")
        self.assertIn(cle, p, "une copie : l'original ne change pas")
        abime = {"a|b": "x", "c|d": {"position": "12"}, cle: {"position": 30.0, "date": 1}, 3: {"position": 9}}
        self.assertEqual(set(regles.memoriser_position(abime, "e|f", 20.0, None, 2)), {cle, "e|f"})
        self.assertIsNone(regles.position_reprise({cle: {"position": 30.0}}, cle, 35.0), "fichier raccourci")
        self.assertIsNone(regles.position_reprise("abîmé", cle))

    def test_les_plus_anciennes_partent(self):
        p = {}
        for i in range(12):
            p = regles.memoriser_position(p, f"d{i}|v", 30.0 + i, None, maintenant=i, maximum=10)
        self.assertEqual(len(p), 10)
        self.assertNotIn("d0|v", p)
        self.assertNotIn("d1|v", p)
        self.assertIn("d11|v", p)


class EssaiEtat(unittest.TestCase):
    def test_ligne_detail(self):
        e = regles.Etat(statut=regles.LECTURE, titre="Ciel", voix="Jules", piste="Beat seul", genre="resultat")
        self.assertEqual(regles.ligne_detail(e), "voix Jules · Beat seul", "inchangée à 1× sans boucle")
        e.vitesse, e.boucle = 1.5, (4.0, 10.0)
        self.assertEqual(regles.ligne_detail(e), "voix Jules · Beat seul · 1,5× · boucle A-B")

    def test_mpris_vitesse(self):
        e = regles.Etat(statut=regles.LECTURE, titre="Ciel", genre="resultat", cle=("resultat", "/x", "jules"),
                        duree_s=60.0)
        p = mpris.proprietes(e)[mpris.LECTEUR]
        self.assertEqual((p["Rate"].get_double(), p["MinimumRate"].get_double(), p["MaximumRate"].get_double()),
                         (1.0, 1.0, 1.0), "une session qui ne règle pas sa vitesse")
        e.vitesse, e.vitesse_reglable = 1.5, True
        p = mpris.proprietes(e)[mpris.LECTEUR]
        self.assertEqual((p["Rate"].get_double(), p["MinimumRate"].get_double(), p["MaximumRate"].get_double()),
                         (1.5, 0.5, 2.0))

    def test_mpris_ecrire_rate(self):
        class Pilote:
            def __init__(self):
                self.journal = []
                self.e = regles.Etat(statut=regles.LECTURE, titre="Ciel", genre="resultat", cle=("r", "/x", "j"),
                                     duree_s=60.0, vitesse_reglable=True)

            def etat(self):
                return self.e

            def regler_vitesse(self, v):
                self.journal.append(("vitesse", v))

            def pause(self):
                self.journal.append(("pause",))

        p = Pilote()
        s = mpris.ServeurMpris(None, p)
        s._ecrire_propriete(None, None, None, mpris.LECTEUR, "Rate", GLib.Variant("d", 3.0))
        s._ecrire_propriete(None, None, None, mpris.LECTEUR, "Rate", GLib.Variant("d", 0.0))
        p.e.vitesse_reglable = False
        s._ecrire_propriete(None, None, None, mpris.LECTEUR, "Rate", GLib.Variant("d", 1.5))
        self.assertEqual(p.journal, [("vitesse", 2.0), ("pause",)], "bornée ; 0 vaut pause ; sinon ignorée")


class EssaiPreferences(EssaiIsole):
    def test_defauts(self):
        p = chemins.lire_preferences()
        self.assertEqual((p["frise_octave"], p["frise_voix"], p["paroles_panneau"], p["paroles_video"],
                          p["paroles_taille"], p["volume"], p["positions"]),
                         (True, True, False, False, "normale", 1.0, {}))
        chemins.modifier_preference("positions", {"a|b": {"position": 12.0, "date": 1.0}})
        chemins.modifier_preference("volume", 0.5)
        ecrit = json.loads(chemins.fichier_preferences().read_text(encoding="utf-8"))
        self.assertEqual((ecrit["positions"]["a|b"]["position"], ecrit["volume"]), (12.0, 0.5))

    def test_captures(self):
        self.assertEqual(chemins.nom_capture("Ciel: craie/2", "Beat seul", 83.456), "Ciel- craie-2 - Beat seul - "
                                                                                    "1m23,46s.png")
        self.assertEqual(chemins.nom_capture("Ciel", "", 5), "Ciel - 0m05,00s.png")
        with tempfile.TemporaryDirectory() as d:
            os.environ["VOIXLA_IMAGES"] = d
            try:
                self.assertEqual(chemins.dossier_images("/nulle/part"), Path(d))
            finally:
                del os.environ["VOIXLA_IMAGES"]
            self.assertEqual(chemins.dossier_images(d), Path(d) / "Voix-là")


# --- Le lecteur sous GStreamer (processus à part, Xvfb, aucun son) ----------------------------------------------------

def fabriquer(dossier):
    """deux.mp4 : deux pistes audio (sinus à 440 Hz « Doublage », à 660 Hz « Beat seul ») ; autre.mp4 : sinus à
    550 Hz. 8 s, 25 images/s, petites : quelques dixièmes de seconde sous nice."""
    import subprocess
    base = ["nice", "-n", "19", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    image = ["-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=8"]
    sinus = lambda f: ["-f", "lavfi", "-i", f"sine=frequency={f}:duration=8:sample_rate=44100"]  # noqa: E731
    sortie = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k"]
    subprocess.run([*base, *image, *sinus(440), *sinus(660), "-map", "0:v", "-map", "1:a", "-map", "2:a", *sortie,
                    "-metadata:s:a:0", "title=Doublage", "-metadata:s:a:1", "title=Beat seul",
                    str(dossier / "deux.mp4")], check=True, timeout=120)
    subprocess.run([*base, *image, *sinus(550), "-map", "0:v", "-map", "1:a", *sortie, str(dossier / "autre.mp4")],
                   check=True, timeout=120)


def lancer_essai(argv, env, delai=150):
    """tests.essai_lecteur_complet sous Xvfb, sur un bus privé (outils/bus-essai.conf), une capture à la fois (même
    verrou que les captures), dans son propre groupe de processus : tout est tué s'il ne finit pas."""
    import contextlib
    import signal
    import subprocess
    import sys
    verrou = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "voix-la-xvfb.verrou"
    cmd = ["flock", str(verrou), "dbus-run-session", f"--config-file={INTERFACE / 'outils' / 'bus-essai.conf'}", "--",
           "xvfb-run", "-a", "-s", "-screen 0 640x480x24", "nice", "-n", "10", sys.executable, "-m",
           "tests.essai_lecteur_complet", *map(str, argv)]
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


@unittest.skipUnless(FFMPEG and GI and shutil.which("xvfb-run") and shutil.which("dbus-run-session")
                     and shutil.which("flock"), "ffmpeg, GStreamer, xvfb-run, dbus-run-session ou flock absent")
class EssaiLecteurGst(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dossier = tempfile.TemporaryDirectory(prefix="voixla-lecteur-")
        cls.dossier = Path(cls._dossier.name)
        fabriquer(cls.dossier)
        cls.res, cls.mesures = lancer_essai([cls.dossier], env_gst(cls.dossier / "cache"))

    @classmethod
    def tearDownClass(cls):
        cls._dossier.cleanup()

    def mesure(self, **cles):
        for l in self.mesures:
            if all(l.get(k) == v for k, v in cles.items()):
                return l
        self.fail(f"pas de mesure {cles} : {self.mesures}\n{self.res.stderr[-2000:]}")

    def test_fini(self):
        self.assertEqual(self.res.returncode, 0, self.res.stdout[-1500:] + self.res.stderr[-1500:])
        self.mesure(fini=True)

    def test_la_hauteur_est_gardee(self):
        """scaletempo est le filtre audio du lecteur de GTK : à 0,5× comme à 2×, on entend toujours 440 Hz, et la
        position avance deux fois moins, ou deux fois plus vite."""
        self.assertTrue(self.mesure(scaletempo=True)["vitesse_possible"])
        self.assertAlmostEqual(self.mesure(vitesse=1.0)["frequence"], 440, delta=6)
        for v in (2.0, 0.5):
            m = self.mesure(vitesse=v)
            self.assertAlmostEqual(m["lue"], v, places=3)
            self.assertAlmostEqual(m["frequence"], 440, delta=6, msg=m)
            self.assertAlmostEqual(m["avance"], v, delta=0.2 * v, msg=m)

    def test_boucle_a_travers_les_pistes(self):
        """La boucle A-B (1 s → 2 s) tient dans le doublage, après une bascule dans le même flux (on entend le beat,
        660 Hz), puis vers un autre fichier (550 Hz), qui prend aussi la vitesse."""
        for nom in ("doublage", "beat", "original"):
            m = self.mesure(boucle=nom)
            self.assertGreaterEqual(m["mini"], 0.85, m)
            self.assertLessEqual(m["maxi"], 2.0 + 0.25, m)
            self.assertGreaterEqual(m["retours"], 2, m)
        self.assertTrue(self.mesure(boucle="beat")["meme_flux"])
        self.assertAlmostEqual(self.mesure(boucle="beat")["frequence"], 660, delta=8)
        o = self.mesure(boucle="original")
        self.assertTrue(o["autre_flux"])
        self.assertAlmostEqual(o["lue"], 1.5, places=3)
        self.assertAlmostEqual(o["frequence"], 550, delta=8)

    def test_image_par_image_et_volume(self):
        m = self.mesure(ips=25.0)
        self.assertAlmostEqual(m["image"], 0.04, delta=0.015, msg=m)
        self.assertAlmostEqual(m["retour"], 0.0, delta=0.015, msg=m)
        self.assertFalse(m["en_lecture"], "en pause")
        v = self.mesure(volume=0.4)
        self.assertTrue(v["muet"], "les essais restent muets")
