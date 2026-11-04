"""python3 -m voixla : lance l'interface."""

import sys

from . import gstreamer

gstreamer.preparer()          # avant GTK : décodage vidéo logiciel (voir voixla/gstreamer.py)

from .gui.app import lancer   # noqa: E402

sys.exit(lancer(sys.argv))
