"""Outils communs aux essais : environnement isolé (dossier temporaire, variables VOIXLA_*), faux doubler, voix,
attente d'une condition. Aucun essai n'écrit hors de son dossier temporaire."""

import json
import os
import shlex
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from voixla import moteur

ICI = Path(__file__).resolve().parent
FAUX_DOUBLER = ICI / "faux_doubler.py"
DEPOT = ICI.parents[1]
VRAI_DOUBLER = DEPOT / "doubler.py"

# Variables effacées puis reposées pour chaque essai (les FAUX_DOUBLER_* sont toutes effacées)
VARIABLES = ("VOIXLA_DONNEES", "VOIXLA_CONFIG", "VOIXLA_ETAT", "VOIXLA_DOUBLER", "VOIXLA_ECOUTE", "VOIXLA_KAGGLE",
             "DOUBLER_VOIX", "XDG_CONFIG_HOME", "XDG_STATE_HOME")


def commande_faux(chemin=FAUX_DOUBLER):
    """Valeur de VOIXLA_DOUBLER pour le faux doubler (chemin protégé pour shlex)."""
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(chemin))}"


def oublier_capacites():
    """Remet à zéro le cache de « doubler --help » (à faire quand la commande ou FAUX_DOUBLER_ANCIEN change)."""
    moteur._capacites.update({"cle": None, "valeur": frozenset(), "aide": ""})
    moteur._capacites.pop("erreur", None)


def creer_voix(donnees, ident="jules", nom="Jules", hauteur=110.0, reference=True, **autres):
    """Une voix dans <données>/voix/<ident>/ (voix.json, reference.wav vide)."""
    d = Path(donnees) / "voix" / ident
    d.mkdir(parents=True, exist_ok=True)
    fiche = {"nom": nom, "reference": "reference.wav", "duree_s": 20.0, "sonie_lufs": -26.0,
             "hauteur_parole_10_90_hz": [100.0, 125.0], "note_mediane": "A2", "date": "2026-11-03",
             "origine": "essai", "consentement": "voix d'essai", **autres}
    if hauteur is not None:
        fiche["hauteur_parole_mediane_hz"] = hauteur
    (d / "voix.json").write_text(json.dumps(fiche, ensure_ascii=False), encoding="utf-8")
    if reference:
        (d / "reference.wav").write_bytes(b"")
    return d


def attendre(condition, delai=20.0, pas=0.05):
    """Vrai dès que condition() l'est, faux après « delai » secondes."""
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(pas)
    return bool(condition())


def lire_trace(chemin):
    """Lignes JSON écrites par le faux doubler (FAUX_DOUBLER_TRACE)."""
    try:
        return [json.loads(l) for l in Path(chemin).read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


class EssaiIsole(unittest.TestCase):
    """Chaque essai a son dossier temporaire et ses variables : VOIXLA_DONNEES, VOIXLA_CONFIG, VOIXLA_ETAT,
    VOIXLA_ECOUTE, VOIXLA_DOUBLER (faux doubler) ; tout est rendu après l'essai, cache des capacités compris."""

    vitesse = "0.02"

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="voixla-essai-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.donnees = self.tmp / "donnees"
        self.config = self.tmp / "config"
        self.etat = self.tmp / "etat"
        for d in (self.donnees, self.config, self.etat):
            d.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in VARIABLES and not k.startswith("FAUX_DOUBLER_")}
        env.update({"VOIXLA_DONNEES": str(self.donnees), "VOIXLA_CONFIG": str(self.config),
                    "VOIXLA_ETAT": str(self.etat), "VOIXLA_ECOUTE": str(self.tmp / "ecoute"),
                    "VOIXLA_DOUBLER": commande_faux(), "FAUX_DOUBLER_VITESSE": self.vitesse})
        rustine = mock.patch.dict(os.environ, env, clear=True)
        rustine.start()
        self.addCleanup(rustine.stop)
        oublier_capacites()
        self.addCleanup(oublier_capacites)

    def ancien_doubler(self):
        """Le faux doubler imite désormais un ancien doubler (sans --evenements, --donnees, --config, --json, --mix-v1)."""
        os.environ["FAUX_DOUBLER_ANCIEN"] = "1"
        oublier_capacites()

    def video(self, nom="Clip.mp4"):
        """Une « vidéo » (fichier vide : le faux doubler ne la lit pas)."""
        d = self.tmp / "videos"
        d.mkdir(exist_ok=True)
        p = d / nom
        p.write_bytes(b"")
        return p
