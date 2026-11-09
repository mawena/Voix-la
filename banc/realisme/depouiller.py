#!/usr/bin/env python3
"""Dépouille les réponses du test « Est-ce lui ? ».

  depouiller.py --cles D/cles.json --reponses reponses-*.json [--json sortie.json]

Pour chaque condition : nombre de réponses, taux de « oui » (avec intervalle de Wilson à 95 %), « non », « je ne sais pas »,
confiance moyenne. Puis le détail par chanson et par auditeur. Le vrai chant sert de plafond, les leurres de plancher.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _agreger(lignes: list[dict]) -> dict:
    n = len(lignes)
    oui = sum(1 for l in lignes if l["reponse"] == "oui")
    non = sum(1 for l in lignes if l["reponse"] == "non")
    nsp = sum(1 for l in lignes if l["reponse"] == "nsp")
    conf = [l["confiance"] for l in lignes if l["confiance"]]
    lo, hi = wilson(oui, n)
    return {"n": n, "oui": oui, "non": non, "nsp": nsp, "taux_oui": oui / n if n else 0.0,
            "ic95_oui": [lo, hi], "confiance_moyenne": (sum(conf) / len(conf)) if conf else None}


def depouiller(cles: dict, fichiers_reponses: list[dict]) -> dict:
    par_id = {e["id"]: e for e in cles["extraits"]}
    lignes = []
    inconnus = 0
    for rep in fichiers_reponses:
        if rep.get("session") != cles.get("session"):
            raise ValueError("ces réponses ne correspondent pas à cette clé (session différente)")
        for r in rep["reponses"]:
            if r.get("reponse") is None:
                continue
            e = par_id.get(r["id"])
            if e is None:
                inconnus += 1
                continue
            lignes.append({"participant": rep.get("participant", "?"), "condition": e["condition"],
                           "chanson": e.get("chanson", ""), "reponse": r["reponse"], "confiance": r.get("confiance")})
    out = {"n_auditeurs": len({l["participant"] for l in lignes}), "n_reponses": len(lignes), "ignorees": inconnus}
    for cle_nom, cle in (("par_condition", lambda l: l["condition"]),
                         ("par_condition_et_chanson", lambda l: f'{l["condition"]} | {l["chanson"]}'),
                         ("par_auditeur_et_condition", lambda l: f'{l["participant"]} | {l["condition"]}')):
        g = defaultdict(list)
        for l in lignes:
            g[cle(l)].append(l)
        out[cle_nom] = {k: _agreger(v) for k, v in sorted(g.items())}
    return out


def afficher(res: dict) -> str:
    t = [f"{res['n_auditeurs']} auditeur(s), {res['n_reponses']} réponses"
         + (f" ({res['ignorees']} ignorées)" if res["ignorees"] else ""), ""]
    for titre, k in (("Par condition", "par_condition"), ("Par condition et chanson", "par_condition_et_chanson"),
                     ("Par auditeur", "par_auditeur_et_condition")):
        t += [titre, f"{'':42s} {'n':>3s} {'oui':>5s} {'IC95 %':>13s} {'non':>4s} {'nsp':>4s} {'conf.':>6s}"]
        for nom, a in res[k].items():
            c = f"{a['confiance_moyenne']:.1f}" if a["confiance_moyenne"] is not None else "-"
            t.append(f"{nom[:42]:42s} {a['n']:3d} {a['taux_oui']:5.0%} "
                     f"[{a['ic95_oui'][0]:.0%}-{a['ic95_oui'][1]:.0%}]"
                     f" {a['non']:4d} {a['nsp']:4d} {c:>6s}")
        t.append("")
    return "\n".join(t)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cles", required=True), p.add_argument("--reponses", nargs="+", required=True)
    p.add_argument("--json")
    a = p.parse_args(argv)
    cles = json.loads(Path(a.cles).read_text())
    reps = [json.loads(Path(f).read_text()) for f in a.reponses]
    res = depouiller(cles, reps)
    print(afficher(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
