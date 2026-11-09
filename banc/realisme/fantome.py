#!/usr/bin/env python3
"""Fantôme : reste de la voix d'origine dans la musique seule.

  fantome.py analyser --musique F --voix F [--controles F...] [--sortie res.json]
  fantome.py lot --sorties D --md resultat.md

Sur les passages chantés (là où la voix d'origine isolée est présente), par fenêtres de 4 s :
  1. similarité de locuteur (deux encodeurs) entre la musique seule filtrée sur la bande vocale et la voix d'origine ;
     elle est comparée à des voix de contrôle (voix d'autres chansons) : on rend un écart réduit (z) ;
  2. énergie de 1 à 4 kHz de la musique aux instants des syllabes d'origine : excès en dB sur les instants de repos
     de la voix, et corrélation des enveloppes (sans la tendance lente) ; chacune est comparée à des décalages
     aléatoires de la voix (z), car une musique rythmée est naturellement en phase avec le chant.
Rend un score global (part du temps chanté où le fantôme est détecté, z >= 2) et la liste des moments les plus suspects.
Un z élevé n'est pas une preuve : c'est un classement des moments à écouter.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from juge import NOMS_ENCODEURS, SR, charger_audio, charger_encodeur, cosinus, _niveau_db  # noqa: E402

BANDE_VOCALE = (150.0, 5000.0)
BANDE_SYLLABES = (1000.0, 4000.0)
SEUIL_Z = 2.0


def filtrer_bande(y: np.ndarray, sr: int = SR, bande=BANDE_VOCALE) -> np.ndarray:
    from scipy.signal import butter, sosfiltfilt
    sos = butter(4, bande, btype="bandpass", fs=sr, output="sos")
    return sosfiltfilt(sos, y).astype(np.float32)


def enveloppe_bande_db(y: np.ndarray, sr: int = SR, bande=BANDE_SYLLABES, hop: float = 0.01) -> np.ndarray:
    """Énergie en dB par trame de 10 ms dans la bande donnée (STFT de 32 ms)."""
    from scipy.signal import stft
    n = int(0.032 * sr)
    f, _, Z = stft(y, fs=sr, nperseg=n, noverlap=n - int(hop * sr), boundary=None, padded=False)
    m = (f >= bande[0]) & (f <= bande[1])
    e = np.sum(np.abs(Z[m]) ** 2, axis=0)
    return 10 * np.log10(e + 1e-12)


def _sans_tendance(x: np.ndarray, trames: int = 50) -> np.ndarray:
    k = np.ones(trames) / trames
    return x - np.convolve(x, k, mode="same")


def _lisser(x: np.ndarray, trames: int = 3) -> np.ndarray:
    return np.convolve(x, np.ones(trames) / trames, mode="same")


def metriques_energie(ev: np.ndarray, em: np.ndarray) -> tuple[float, float]:
    """(excès en dB, corrélation) de l'énergie de la musique `em` aux instants de syllabes de la voix `ev` (dB par trame)."""
    n = min(len(ev), len(em))
    ev, em = _lisser(ev[:n]), em[:n]
    actif = ev > ev.max() - 30.0
    if actif.sum() < 20:
        return 0.0, 0.0
    haut = actif & (ev >= np.percentile(ev[actif], 60))
    bas = actif & (ev <= np.percentile(ev[actif], 30))
    exces = float(np.mean(em[haut]) - np.mean(em[bas])) if haut.any() and bas.any() else 0.0
    a, b = _sans_tendance(ev), _sans_tendance(em)
    corr = float(np.corrcoef(a, b)[0, 1]) if a.std() > 1e-9 and b.std() > 1e-9 else 0.0
    return exces, corr


def z_energie(ev: np.ndarray, em: np.ndarray, rng: np.random.Generator, n_decalages: int = 20) -> dict:
    """Mesures observées et leur écart réduit par rapport à des décalages circulaires aléatoires de la voix."""
    n = min(len(ev), len(em))
    ev, em = ev[:n], em[:n]
    exces, corr = metriques_energie(ev, em)
    nulls = []
    for _ in range(n_decalages):
        d = int(rng.integers(150, max(151, n - 150))) if n > 320 else n // 2
        nulls.append(metriques_energie(np.roll(ev, d), em))
    nulls = np.array(nulls)
    z = lambda v, col: float((v - nulls[:, col].mean()) / (nulls[:, col].std() + 1e-3))
    return {"exces_db": exces, "correlation": corr, "z_exces": z(exces, 0), "z_correlation": z(corr, 1)}


def fenetres_chantees(voix: np.ndarray, sr: int = SR, duree: float = 4.0, activite_min: float = 0.5,
                      maximum: int = 40) -> list[int]:
    """Débuts (échantillons) des fenêtres de `duree` s où la voix d'origine chante."""
    n = int(duree * sr)
    niv_max = _niveau_db(voix, sr).max()
    deb = []
    for d in range(0, len(voix) - n + 1, n):
        niv = _niveau_db(voix[d:d + n], sr)
        if np.mean(niv > niv_max - 35.0) >= activite_min:
            deb.append(d)
    if len(deb) > maximum:
        deb = [deb[i] for i in sorted(set(np.linspace(0, len(deb) - 1, maximum).round().astype(int)))]
    return deb


def plonger_fenetres(enc, y: np.ndarray, deb: list[int], duree: float = 4.0, sr: int = SR) -> np.ndarray:
    n = int(duree * sr)
    return enc.plonger_clips([y[d:d + n] for d in deb])


_CACHE_CONTROLES: dict = {}


def _emb_controle(enc, nom: str, chemin, duree: float) -> np.ndarray:
    """Embeddings (mémorisés) de quelques fenêtres chantées d'une voix de contrôle."""
    cle = (str(chemin), nom, duree)
    if cle not in _CACHE_CONTROLES:
        c = filtrer_bande(charger_audio(chemin))
        deb = fenetres_chantees(c, duree=duree, maximum=8)
        _CACHE_CONTROLES[cle] = plonger_fenetres(enc, c, deb, duree) if deb else np.zeros((0, 1))
    return _CACHE_CONTROLES[cle]


def combiner_z(zs: list[float]) -> float:
    return float(np.mean(zs))


def analyser(musique, voix, controles=(), encodeurs=NOMS_ENCODEURS, duree: float = 4.0, maximum: int = 40,
             graine: int = 0, encs=None) -> dict:
    """Fantôme d'une chanson. `musique` et `voix` : chemins ; `controles` : voix d'autres chansons."""
    rng = np.random.default_rng(graine)
    ym = charger_audio(musique)
    yv = charger_audio(voix)
    n = min(len(ym), len(yv))
    ym, yv = ym[:n], yv[:n]
    deb = fenetres_chantees(yv, duree=duree, maximum=maximum)
    if not deb:
        return {"erreur": "aucun passage chanté détecté", "fenetres": []}
    fm, fv = filtrer_bande(ym), filtrer_bande(yv)
    L = int(duree * SR)
    fen = [{"debut_s": d / SR, "fin_s": (d + L) / SR} for d in deb]

    # 1. énergie des syllabes (sur toute la fenêtre, voix et musique filtrées à la même bande)
    for w, d in zip(fen, deb):
        ev = enveloppe_bande_db(yv[d:d + L])
        em = enveloppe_bande_db(ym[d:d + L])
        w.update(z_energie(ev, em, rng))

    # 2. similarité de locuteur contre des voix de contrôle
    encs = encs or {e: charger_encodeur(e) for e in encodeurs}
    for e in encodeurs:
        em_m = plonger_fenetres(encs[e], fm, deb, duree)
        em_v = plonger_fenetres(encs[e], fv, deb, duree)
        sim = np.array([float(cosinus(em_m[i:i + 1], em_v[i])[0]) for i in range(len(deb))])
        ctrl_emb = np.concatenate([_emb_controle(encs[e], e, c, duree) for c in controles]) if len(controles) else em_v
        # contrôle : similarité de chaque fenêtre de musique avec les voix de contrôle (autres que la sienne)
        ctrl = np.array([[float(cosinus(em_m[i:i + 1], v)[0]) for v in ctrl_emb if not np.allclose(v, em_v[i])]
                         for i in range(len(deb))], dtype=object)
        toutes = np.concatenate([np.asarray(c, dtype=float) for c in ctrl]) if len(ctrl) else np.array([0.0])
        mu, sd = float(toutes.mean()), float(toutes.std()) + 1e-3
        for w, s in zip(fen, sim):
            w[f"similarite_{e}"] = float(s)
            w[f"z_similarite_{e}"] = float((s - mu) / sd)

    for w in fen:
        zs = [w["z_exces"], w["z_correlation"]] + [w[f"z_similarite_{e}"] for e in encodeurs]
        w["z"] = combiner_z(zs)
    suspects = sorted(fen, key=lambda w: -w["z"])
    med = lambda k: float(np.median([w[k] for w in fen]))
    return {
        "n_fenetres": len(fen),
        "duree_chantee_s": len(fen) * duree,
        "score_global": float(np.mean([w["z"] >= SEUIL_Z for w in fen])),   # part du chant où le fantôme est détecté
        "z_moyen": float(np.mean([w["z"] for w in fen])),
        "exces_db_median": med("exces_db"),
        "correlation_mediane": med("correlation"),
        **{f"similarite_{e}_mediane": med(f"similarite_{e}") for e in encodeurs},
        **{f"z_similarite_{e}_moyen": float(np.mean([w[f"z_similarite_{e}"] for w in fen])) for e in encodeurs},
        "moments_suspects": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in w.items()}
                             for w in suspects[:8]],
        "controles": "voix d'autres chansons" if controles else "autres fenêtres de la même voix",
        "note": f"z >= {SEUIL_Z} : moment à écouter. Les décalages aléatoires de la voix servent de référence à l'énergie.",
    }


def lot(racine_sorties, md_sortie, encodeurs=NOMS_ENCODEURS, maximum: int = 25) -> dict:
    """Fantôme de chaque chanson d'un dossier de sorties (musique seule = instrumental.wav, voix = voix_source.wav)."""
    racine = Path(os.path.expanduser(str(racine_sorties)))
    paires = []
    for t in sorted(racine.glob("*/travail")):
        if (t / "instrumental.wav").exists() and (t / "voix_source.wav").exists():
            paires.append((t.parent.name, t / "instrumental.wav", t / "voix_source.wav"))
    encs = {e: charger_encodeur(e) for e in encodeurs}
    res = {}
    for i, (titre, m, v) in enumerate(paires):
        autres = [p[2] for j, p in enumerate(paires) if j != i]
        res[titre] = analyser(m, v, controles=autres, encodeurs=encodeurs, maximum=maximum, encs=encs)
    md = ["# Fantôme sur les musiques seules existantes", "",
          "Score = part du temps chanté où le fantôme est détecté (z combiné >= 2). Contrôles : voix des autres chansons.", "",
          "| Chanson | chant analysé (s) | score | z moyen | excès 1-4 kHz (dB) | corrélation | "
          + " | ".join(f"sim. {e}" for e in encodeurs) + " | moment le plus suspect |",
          "|---|---|---|---|---|---|" + "---|" * len(encodeurs) + "---|"]
    for t, r in res.items():
        if "erreur" in r:
            md.append(f"| {t} | - | {r['erreur']} |")
            continue
        top = r["moments_suspects"][0]
        md.append(f"| {t} | {r['duree_chantee_s']:.0f} | {r['score_global']:.0%} | {r['z_moyen']:.2f} | "
                  f"{r['exces_db_median']:.2f} | {r['correlation_mediane']:.2f} | "
                  + " | ".join(f"{r[f'similarite_{e}_mediane']:.2f}" for e in encodeurs)
                  + f" | {top['debut_s']:.0f}-{top['fin_s']:.0f} s (z {top['z']:.1f}) |")
    Path(md_sortie).parent.mkdir(parents=True, exist_ok=True)
    Path(md_sortie).write_text("\n".join(md) + "\n")
    Path(md_sortie).with_suffix(".json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    return res


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    a_ = sp.add_parser("analyser")
    a_.add_argument("--musique", required=True), a_.add_argument("--voix", required=True)
    a_.add_argument("--controles", nargs="*", default=[]), a_.add_argument("--sortie")
    l_ = sp.add_parser("lot")
    l_.add_argument("--sorties", required=True), l_.add_argument("--md", required=True)
    a = p.parse_args(argv)
    if a.cmd == "analyser":
        r = analyser(a.musique, a.voix, a.controles)
        txt = json.dumps(r, indent=2, ensure_ascii=False)
        Path(a.sortie).write_text(txt) if a.sortie else print(txt)
    else:
        lot(a.sorties, a.md)


if __name__ == "__main__":
    sys.exit(main())
