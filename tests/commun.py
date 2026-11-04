"""Outils communs des essais de doubler.py et du noyau Kaggle : rien n'est lu ni écrit hors d'un dossier temporaire
(HOME y est déplacé avant de charger doubler.py), aucune donnée réelle, aucun réseau, aucun GPU."""
import base64
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

import numpy as np

DEPOT = Path(__file__).resolve().parent.parent
NOYAU = DEPOT / 'kaggle' / 'doubler_kaggle.py'
DOUBLER = DEPOT / 'doubler.py'
SR = 44100
FFMPEG = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))


def charger_noyau():
    """Le noyau comme module, sans rien lancer (ses réglages, remplacés par doubler.py avant l'envoi, valent {})."""
    src = NOYAU.read_text(encoding='utf-8').replace("'__REGLAGES__'", repr(base64.b64encode(b'{}').decode()))
    m = types.ModuleType('doubler_kaggle_essai')
    m.__file__ = str(NOYAU)
    exec(compile(src, str(NOYAU), 'exec'), m.__dict__)
    return m


def charger_doubler(home):
    """doubler.py chargé comme module, avec HOME=home (configuration, état et données dans le dossier temporaire)."""
    with mock.patch.dict(os.environ, {'HOME': str(home)}):
        for v in ('VOIXLA_KAGGLE', 'VOIXLA_DONNEES', 'DOUBLER_VOIX'):
            os.environ.pop(v, None)
        spec = importlib.util.spec_from_file_location('doubler_essai', DOUBLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class Dossier:
    """Dossier temporaire (contexte)."""

    def __enter__(self):
        self._t = tempfile.TemporaryDirectory(prefix='voixla-essai-')
        return Path(self._t.name).resolve()

    def __exit__(self, *exc):
        self._t.cleanup()
        return False


def note(f0, debut, fin, niveau_db, duree, sr=SR, harmoniques=5, fondu=0.02):
    """Note tenue (harmoniques décroissantes), RMS niveau_db dBFS, entre debut et fin, sur un signal de duree s."""
    x = np.zeros(int(duree * sr))
    a, b = int(debut * sr), int(fin * sr)
    t = np.arange(b - a) / sr
    y = sum(np.sin(2 * np.pi * f0 * h * t) / h for h in range(1, harmoniques + 1))
    y *= 10 ** (niveau_db / 20) / np.sqrt(np.mean(y ** 2))
    f = int(fondu * sr)
    y[:f] *= np.linspace(0, 1, f)
    y[-f:] *= np.linspace(1, 0, f)
    x[a:b] = y
    return x


def ecrire_wav(chemin, x, sr=SR, canaux=1):
    """WAV flottant 32 bits par ffmpeg (aucune bibliothèque audio à installer)."""
    x = np.asarray(x, np.float32)
    if canaux == 2 and x.ndim == 1:
        x = np.stack([x, x], axis=1)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(sr), '-ac', str(canaux), '-i', '-',
                    '-c:a', 'pcm_f32le', str(chemin)], input=x.tobytes(), check=True)


def ffprobe(chemin, *options):
    import json
    r = subprocess.run(['ffprobe', '-v', 'error', '-of', 'json', *options, str(chemin)], capture_output=True, text=True,
                       check=True)
    return json.loads(r.stdout)


def python():
    return sys.executable
