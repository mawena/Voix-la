#!/usr/bin/env python3
"""Fausse commande kaggle pour les essais et les captures : « quota » et « kernels status », sans réseau ni jeton.

Variables :
- FAUX_KAGGLE_QUOTA="utilisé,total[,refreshAt]" en heures (défaut « 27.5,30 », remise au prochain samedi 00:00 UTC) ;
- FAUX_KAGGLE_ANCIEN=1 : une CLI d'avant « kaggle quota » (choix invalide, code 2) ;
- FAUX_KAGGLE_PANNE=1 : Kaggle injoignable (code 1) ;
- FAUX_KAGGLE_AVERTISSEMENT=1 : un avertissement de version avant le JSON, comme la vraie CLI ;
- FAUX_KAGGLE_TRACE=fichier : une ligne par appel (les arguments).
"""

import datetime
import json
import os
import sys


def samedi_suivant():
    maintenant = datetime.datetime.now(datetime.timezone.utc)
    base = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
    r = base + datetime.timedelta(days=(5 - base.weekday()) % 7)
    if r <= maintenant:
        r += datetime.timedelta(days=7)
    return r.isoformat()


def main(args):
    if os.environ.get("FAUX_KAGGLE_TRACE"):
        with open(os.environ["FAUX_KAGGLE_TRACE"], "a", encoding="utf-8") as f:
            f.write(json.dumps(args) + "\n")
    if os.environ.get("FAUX_KAGGLE_PANNE"):
        print("Connection error: Kaggle injoignable", file=sys.stderr)
        return 1
    if args[:1] == ["quota"]:
        if os.environ.get("FAUX_KAGGLE_ANCIEN"):
            print("kaggle: error: argument command: invalid choice: 'quota' (choose from 'competitions', 'datasets')",
                  file=sys.stderr)
            return 2
        morceaux = (os.environ.get("FAUX_KAGGLE_QUOTA") or "27.5,30").split(",")
        utilise, total = float(morceaux[0]), float(morceaux[1])
        remise = morceaux[2] if len(morceaux) > 2 else samedi_suivant()
        lignes = [{"resource": "GPU", "used": f"{utilise:.2f}h", "remaining": f"{max(0.0, total - utilise):.2f}h",
                   "total": f"{total:.2f}h", "refreshAt": remise},
                  {"resource": "TPU", "used": "0.00h", "remaining": "20.00h", "total": "20.00h", "refreshAt": remise}]
        if os.environ.get("FAUX_KAGGLE_AVERTISSEMENT"):
            print("Warning: Looks like you're using an outdated `kaggle` version (installed: 2.2.4), please consider "
                  "upgrading to the latest version (2.2.5)")
        print(json.dumps(lignes, indent=2))
        return 0
    if args[:2] == ["kernels", "status"] and len(args) > 2:
        print(f'{args[2]} has status "KernelWorkerStatus.COMPLETE"')
        return 0
    print(f"faux kaggle : commande non imitée : {' '.join(args)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
