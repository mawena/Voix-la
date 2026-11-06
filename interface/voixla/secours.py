"""Secours : doubler ailleurs que sur le GPU de Kaggle quand le quota de la semaine ne suffit plus (sans GTK).

Point d'accroche seulement : aucun moyen n'est encore branché. Deux viendront plus tard :
- « ancien-pc » : un ancien PC du réseau, par SSH, qui ferait la partie GPU du doublage ;
- « runpod » : un GPU loué à la demande sur RunPod.

Ce qui est déjà en place, et où brancher un moyen :
- la préférence « secours » d'interface.json (vide : aucun), réglée dans Réglages > Doublage ;
- le dialogue « ne tient plus dans le quota » de la page Doubler propose, en plus de « Attendre la remise à zéro » et
  « Lancer quand même », un bouton « Doubler sur <nom> » par moyen de proposer_pour() ;
- choisi, le moyen transforme la demande de la file par preparer_demande() (autre commande, autre argv) : l'exécuteur
  la lance comme n'importe quel travail, sans rien savoir du secours ; la page Travaux montre « secours » dans le
  résumé (demande["secours"]).

Brancher un moyen = lui écrire pret() (le moyen répond-il ? hôte SSH joignable, clé RunPod présente…) et demande()
(la demande transformée), puis le marquer branche=True.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Moyen:
    cle: str
    nom: str
    description: str
    branche: bool = False      # vrai une fois le moyen écrit

    def pret(self, conf=None):
        """(prêt, raison) : le moyen peut-il prendre un doublage maintenant ?"""
        if not self.branche:
            return False, "pas encore branché"
        raise NotImplementedError(self.cle)

    def demande(self, demande, conf=None):
        """La demande de la file, transformée pour ce moyen (argv, env, « secours »)."""
        raise NotImplementedError(f"secours « {self.cle} » pas encore branché")


MOYENS = (
    Moyen("ancien-pc", "Ancien PC (SSH)", "la partie GPU sur un ancien PC du réseau, par SSH"),
    Moyen("runpod", "RunPod", "un GPU loué à la demande sur RunPod"),
)
PAR_CLE = {m.cle: m for m in MOYENS}


def choisi(prefs):
    """Le moyen choisi dans les préférences (clé « secours »), ou None."""
    return PAR_CLE.get(str((prefs or {}).get("secours") or ""))


def proposer_pour(prefs, conf=None):
    """Les moyens à proposer quand un doublage ne tient plus dans le quota : celui des préférences, s'il est branché
    et prêt. Rien pour l'instant."""
    m = choisi(prefs)
    if m is None or not m.branche:
        return []
    ok, _raison = m.pret(conf)
    return [m] if ok else []


def preparer_demande(moyen, demande, conf=None):
    """La demande de la file pour ce moyen, marquée « secours »."""
    return {**moyen.demande(demande, conf), "secours": moyen.cle}
