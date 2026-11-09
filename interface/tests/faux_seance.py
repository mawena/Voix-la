"""Données inventées pour la séance d'enregistrement : des voix (voixA, voixB), trois doublages de « Chanson d'exemple »
(travail/instrumental.wav et voix_source.wav de synthèse, rapport.json avec la tessiture du chanteur d'origine, .lrc), et
quelques prises déjà enregistrées. Aucun vrai titre ni vrai artiste.

    python3 -m tests.faux_seance <dossier de données>        # pour les captures (VOIXLA_DONNEES)
"""

import json
import subprocess
import sys
from pathlib import Path

from .commun import creer_voix

LIGNES = ["Première ligne de la chanson d’exemple", "Une deuxième ligne, un peu plus longue que la première",
          "Le refrain revient ici", "Encore une ligne pour la route", "La dernière ligne, puis le silence"]
# (titre, hauteur médiane du chanteur d'origine en Hz)
DOUBLAGES = (("Chanson d'exemple A", 205.0), ("Chanson d'exemple B", 290.0), ("Chanson d'exemple C", 400.0))


def son(chemin, hauteur_hz, duree_s, canaux=2):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={hauteur_hz}:duration={duree_s}:sample_rate=44100", "-ac", str(canaux),
                    str(chemin)], check=True, capture_output=True, timeout=60)


def creer_doublage(sorties, titre, f0, duree_s=14, lrc=True, langue="fr"):
    d = Path(sorties) / titre
    (d / "travail" / "voixA").mkdir(parents=True)
    son(d / "travail" / "instrumental.wav", 220, duree_s)
    son(d / "travail" / "voix_source.wav", 440, duree_s, canaux=1)
    rapport = {"decision_hauteur": {"voix_principale": {"f0_p10_hz": round(f0 * 0.8, 1), "f0_med_hz": f0,
                                                        "f0_p90_hz": round(f0 * 1.25, 1), "classe": "homme"}}}
    if langue:
        rapport["paroles_sorties"] = {"langue_chantee": {"langue": langue}}
    (d / "travail" / "voixA" / "rapport.json").write_text(json.dumps(rapport), encoding="utf-8")
    if lrc:
        lignes = ["[la:fr]"] + [f"[00:{2 + 2 * i:02d}.00]{t}" for i, t in enumerate(LIGNES)]
        (d / f"{titre} - voix voixA.lrc").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return d


def creer(donnees, avec_prises=True):
    donnees = Path(donnees)
    creer_voix(donnees, "voixA", "Voix A", hauteur=100.0)
    creer_voix(donnees, "voixB", "Voix B", hauteur=140.0)
    sorties = donnees / "sorties"
    sorties.mkdir(parents=True, exist_ok=True)
    for titre, f0 in DOUBLAGES:
        # C est chantée en anglais : cachée tant qu'on ne montre pas toutes les langues
        creer_doublage(sorties, titre, f0, lrc=titre.endswith("A") or titre.endswith("B"),
                       langue="en" if titre.endswith("C") else "fr")
    if avec_prises:
        voix = donnees / "voix" / "voixA"
        (voix / "chant").mkdir(exist_ok=True)
        (voix / "parole").mkdir(exist_ok=True)
        son(voix / "chant" / "20261108-200000-notes.m4a", 330, 95)
        son(voix / "chant" / "20261108-201500-chanson.m4a", 300, 200)
        son(voix / "parole" / "20261108-190000-parole.m4a", 200, 125, canaux=1)
    return donnees


if __name__ == "__main__":
    creer(sys.argv[1])
