"""La barre latérale sans « Écoute à l'aveugle » et la séance d'enregistrement dans la vraie fenêtre, sous Xvfb et sur un
bus privé (tests/essai_seance_gui.py). Données inventées (tests/faux_seance.py)."""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from . import faux_seance as fs
from .commun import EssaiIsole

FFMPEG = shutil.which("ffmpeg")
INTERFACE = Path(__file__).resolve().parents[1]

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    GUI = True
except (ImportError, ValueError):
    GUI = False


class TestSectionsSansGtk(unittest.TestCase):
    """SECTIONS et RACCOURCIS_SECTIONS, lus dans le source (pas besoin d'écran)."""

    @unittest.skipUnless(GUI, "GTK absent")
    def test_sections(self):
        from voixla.gui import app
        tags = [s[0] for s in app.SECTIONS]
        self.assertEqual(tags, ["doubler", "travaux", "resultats", "karaoke", "voix", "etat"])
        self.assertNotIn("ecoute", tags)
        self.assertEqual([p[0] for p in app.PAGES_HORS_BARRE], ["ecoute"])
        self.assertEqual(app.RACCOURCIS_SECTIONS["ecoute"], 5)
        # chaque page de la barre a son raccourci, tous différents
        numeros = [app.RACCOURCIS_SECTIONS[t] for t in tags + ["ecoute"]]
        self.assertEqual(sorted(numeros), [1, 2, 3, 4, 5, 6, 7])


@unittest.skipUnless(FFMPEG and GUI and shutil.which("xvfb-run") and shutil.which("dbus-run-session")
                     and shutil.which("flock"), "ffmpeg, GTK, xvfb-run, dbus-run-session ou flock absent")
class EssaiPage(EssaiIsole):
    def setUp(self):
        super().setUp()
        os.environ["VOIXLA_CACHE"] = str(self.tmp / "cache")

    def test_page(self):
        fs.creer(self.donnees)
        env = dict(os.environ)
        env.update({"GTK_A11Y": "none", "NO_AT_BRIDGE": "1", "GIO_USE_VFS": "local", "GSK_RENDERER": "cairo",
                    "ADW_DISABLE_PORTAL": "1", "VOIXLA_APP_ID": f"io.mawena.VoixLa.Essai{os.getpid()}",
                    "GST_PLUGIN_FEATURE_RANK": "fakeaudiosink:MAX", "VOIXLA_PAGE": "voix",
                    "XDG_STATE_HOME": str(self.tmp / "xdg-etat"),
                    "PYTHONPATH": os.pathsep.join([str(INTERFACE)] + ([os.environ["PYTHONPATH"]]
                                                                        if os.environ.get("PYTHONPATH") else []))})
        verrou = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "voix-la-xvfb.verrou"
        cmd = ["flock", str(verrou), "dbus-run-session", f"--config-file={INTERFACE / 'outils' / 'bus-essai.conf'}",
               "--", "xvfb-run", "-a", "-s", "-screen 0 1400x1000x24", "nice", "-n", "10", sys.executable, "-m",
               "tests.essai_seance_gui"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                             cwd=str(INTERFACE), stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            sortie, erreur = p.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            sortie, erreur = p.communicate()
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGTERM)
        time.sleep(0.5)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGKILL)
        mesures = {}
        for ligne in sortie.splitlines():
            with contextlib.suppress(ValueError):
                mesures.update(json.loads(ligne))
        trace = sortie[-3000:] + erreur[-3000:]
        self.assertEqual(p.returncode, 0, trace)
        self.assertEqual(mesures.get("erreurs"), [], trace)
        self.assertTrue(mesures.get("fini"), trace)


if __name__ == "__main__":
    unittest.main()
