"""Secours : doubler ailleurs que sur le GPU de Kaggle quand son quota de la semaine ne suffit plus (sans GTK).

Deux moyens sont branchés, les mêmes que l'option « --calcul » de doubler (voir calcul.py du dépôt) :
- « modal » : Modal, crédit gratuit mensuel, application éphémère ;
- « runpod » : un pod Runpod RTX 4090, PAYANT, toujours supprimé à la fin. Jamais lancé sans que l'utilisateur l'ait choisi
  (Réglages > Doublage > Secours, ou le bouton du dialogue « ne tient plus dans le quota »).
« ancien-pc » (la partie GPU sur un ancien PC du réseau, par SSH) n'est pas branché.

Où ils s'accrochent :
- la préférence « secours » d'interface.json (vide : aucun), réglée dans Réglages > Doublage ;
- le dialogue « ne tient plus dans le quota » de la page Doubler propose, en plus de « Attendre la remise à zéro » et
  « Lancer quand même », un bouton « Doubler sur <nom> » par moyen de proposer_pour() ;
- choisi, le moyen transforme la demande de la file par preparer_demande() : même argv avec « --calcul=<moyen> » ;
  l'exécuteur la lance comme n'importe quel travail, sans rien savoir du secours ; la page Travaux montre « secours »
  dans le résumé (demande["secours"]).
- la préférence « calcul » (kaggle, modal, runpod, auto) est le moyen choisi pour TOUS les doublages : la page Doubler
  la propose, moteur.args_doublage la transmet.

Ici, « prêt » ne regarde que la présence des fichiers (client, jeton) : jamais leur contenu, jamais le réseau.
"""

import os
from dataclasses import dataclass
from pathlib import Path

CALCULS = (("kaggle", "Kaggle (gratuit)"), ("modal", "Modal"), ("runpod", "Runpod (payant)"),
           ("auto", "Auto : Kaggle, puis Modal"))


def _modal_pret():
    python = Path(os.environ.get("VOIXLA_MODAL_PYTHON") or Path.home() / "modal-venv" / "bin" / "python").expanduser()
    if not python.exists():
        return False, f"client modal absent ({python})"
    if not ((Path.home() / ".modal.toml").exists() or (os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"))):
        return False, "jeton Modal absent (modal setup)"
    return True, ""


def _runpod_pret():
    ctl = Path(os.environ.get("VOIXLA_RUNPODCTL") or Path.home() / ".local" / "bin" / "runpodctl").expanduser()
    if not ctl.exists():
        return False, f"runpodctl absent ({ctl})"
    if not ((Path.home() / ".runpod" / "config.toml").exists() or os.environ.get("RUNPOD_API_KEY")):
        return False, "clé Runpod absente (runpodctl doctor)"
    return True, ""


@dataclass(frozen=True)
class Moyen:
    cle: str
    nom: str
    description: str
    branche: bool = False      # vrai une fois le moyen écrit
    payant: bool = False

    def pret(self, conf=None):
        """(prêt, raison) : le moyen peut-il prendre un doublage maintenant ?"""
        if not self.branche:
            return False, "pas encore branché"
        return {"modal": _modal_pret, "runpod": _runpod_pret}[self.cle]()

    def demande(self, demande, conf=None):
        """La demande de la file, transformée pour ce moyen : le même argv, avec « --calcul=<moyen> »."""
        if not self.branche:
            raise NotImplementedError(f"secours « {self.cle} » pas encore branché")
        argv = [a for a in demande.get("argv", []) if not str(a).startswith("--calcul")]
        # « --calcul X » en deux mots n'est jamais écrit par l'interface (elle écrit --calcul=X) ; l'argv ne garde qu'un choix
        return {**demande, "argv": [*argv, f"--calcul={self.cle}"],
                "reglages": {**(demande.get("reglages") or {}), "calcul": self.cle}}


MOYENS = (
    Moyen("ancien-pc", "Ancien PC (SSH)", "la partie GPU sur un ancien PC du réseau, par SSH"),
    Moyen("modal", "Modal", "Modal : crédit gratuit mensuel, application éphémère, carte L4 par défaut", branche=True),
    Moyen("runpod", "Runpod", "un pod RTX 4090 loué à la demande (payant, supprimé à la fin)", branche=True, payant=True),
)
PAR_CLE = {m.cle: m for m in MOYENS}


def choisi(prefs):
    """Le moyen choisi dans les préférences (clé « secours »), ou None."""
    return PAR_CLE.get(str((prefs or {}).get("secours") or ""))


def calcul_choisi(prefs):
    """Le moyen de calcul de tous les doublages (clé « calcul » des préférences) : kaggle, modal, runpod ou auto ; chaîne
    vide si la préférence manque ou est illisible : doubler décide alors (sa clé « calcul », kaggle par défaut)."""
    v = str((prefs or {}).get("calcul") or "")
    return v if v in dict(CALCULS) else ""


def proposer_pour(prefs, conf=None):
    """Les moyens à proposer quand un doublage ne tient plus dans le quota : celui des préférences, s'il est branché
    et prêt."""
    m = choisi(prefs)
    if m is None or not m.branche:
        return []
    ok, _raison = m.pret(conf)
    return [m] if ok else []


def preparer_demande(moyen, demande, conf=None):
    """La demande de la file pour ce moyen, marquée « secours »."""
    return {**moyen.demande(demande, conf), "secours": moyen.cle}
