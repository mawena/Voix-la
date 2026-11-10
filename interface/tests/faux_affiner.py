#!/usr/bin/env python3
"""Faux « affiner » pour les essais de l'interface : écrit les événements d'un vrai affinage (controle, envoi, attente,
kaggle, rapatriement, fin) sans rien envoyer et range une fiche affinage.json dans la voix. FAUX_AFFINER_VITESSE : secondes
entre deux étapes (défaut 0,02) ; FAUX_AFFINER_ECHEC=1 : s'arrête en erreur ; FAUX_AFFINER_TRACE : fichier où écrire les
arguments reçus."""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path


def main(argv):
    arg = {a.split("=", 1)[0]: (a.split("=", 1) + [""])[1] for a in argv if a.startswith("--")}
    nom = next((a for a in argv if not a.startswith("--")), "")
    if os.environ.get("FAUX_AFFINER_TRACE"):
        with open(os.environ["FAUX_AFFINER_TRACE"], "a") as f:
            f.write(json.dumps({"argv": argv}) + "\n")
    ev = open(arg["--evenements"], "a", encoding="utf-8") if arg.get("--evenements") else None

    def evenement(type_, **k):
        if ev:
            ev.write(json.dumps({"t": datetime.now().astimezone().isoformat(timespec="milliseconds"), "type": type_,
                                 "video": None, **k}) + "\n")
            ev.flush()
    vitesse = float(os.environ.get("FAUX_AFFINER_VITESSE", "0.02"))
    for etape in ("controle", "envoi", "attente", "kaggle", "rapatriement"):
        evenement("etape", etape=etape, etat="debut")
        if etape == "kaggle":
            evenement("kaggle", statut="running", depuis_s=0, estimation_s=1800)
        print(f"{etape}…", flush=True)
        time.sleep(vitesse)
        if etape == "kaggle" and os.environ.get("FAUX_AFFINER_ECHEC"):
            evenement("erreur", message="train.py muet")
            evenement("fin", code=1)
            return 1
        evenement("etape", etape=etape, etat="fin", duree_s=vitesse)
    d = Path(arg.get("--donnees", ".")) / "voix" / nom / "affinage"
    d.mkdir(parents=True, exist_ok=True)
    (d / "affinage.pth").write_bytes(b"poids")
    (d / "affinage.json").write_text(json.dumps({"format": 1, "voix": nom, "point": "affinage.pth", "pas": 1500,
                                                 "complet": True, "utile_s": 400.0, "taille_mo": 0.0}))
    evenement("fin", code=0)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
