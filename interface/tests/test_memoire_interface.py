"""La mémoire de l'interface : une page quittée est rendue au ramasse-miettes (tests/essai_memoire_gui.py, sous Xvfb sur
un bus privé, son coupé), et le spectrogramme ne reste pas accroché au gestionnaire de styles de l'application."""

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

from . import fausse_bibliotheque as fb
from .test_pistes import FFMPEG, EssaiAvecCache

INTERFACE = Path(__file__).resolve().parents[1]

GUI = None
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    GUI = True
except (ImportError, ValueError):
    GUI = False


@unittest.skipUnless(FFMPEG and GUI and shutil.which("xvfb-run") and shutil.which("dbus-run-session")
                     and shutil.which("flock"), "ffmpeg, GTK, xvfb-run, dbus-run-session ou flock absent")
class EssaiMemoire(EssaiAvecCache):
    """Ouvrir et quitter des résultats ne laisse rien en vie (pages, spectrogrammes, flux) ; la page État rafraîchie
    garde le même nombre de widgets."""

    def test_pages_rendues(self):
        fb.creer(self.donnees, videos=True)
        env = dict(os.environ)
        env.update({"GTK_A11Y": "none", "NO_AT_BRIDGE": "1", "GIO_USE_VFS": "local", "GSK_RENDERER": "cairo",
                    "ADW_DISABLE_PORTAL": "1", "VOIXLA_APP_ID": f"io.mawena.VoixLa.Essai{os.getpid()}",
                    "GST_PLUGIN_FEATURE_RANK": "fakeaudiosink:MAX", "VOIXLA_PAGE": "resultats",
                    "VOIXLA_SANS_PREPARATION": "1", "XDG_STATE_HOME": str(self.tmp / "xdg-etat"),
                    "PYTHONPATH": os.pathsep.join([str(INTERFACE)] + ([os.environ["PYTHONPATH"]]
                                                                        if os.environ.get("PYTHONPATH") else []))})
        verrou = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "voix-la-xvfb.verrou"
        cmd = ["flock", str(verrou), "dbus-run-session", f"--config-file={INTERFACE / 'outils' / 'bus-essai.conf'}",
               "--", "xvfb-run", "-a", "-s", "-screen 0 1400x1000x24", "nice", "-n", "10", sys.executable, "-m",
               "tests.essai_memoire_gui"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                             cwd=str(INTERFACE), stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            sortie, erreur = p.communicate(timeout=240)
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
