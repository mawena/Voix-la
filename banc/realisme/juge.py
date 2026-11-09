#!/usr/bin/env python3
"""Juge de ressemblance de locuteur : « est-ce bien la voix cible ? ».

Deux encodeurs indépendants (jamais CAMPPlus, que le moteur de conversion utilise) :
  - « wavlm-sv » : microsoft/wavlm-base-plus-sv (transformers) ;
  - « ecapa »    : speechbrain/spkrec-ecapa-voxceleb (ECAPA-TDNN, SpeechBrain).

Pour chaque clip (4 à 10 s, voix seule) et chaque encodeur : cosinus avec le centroïde des références,
rang parmi des leurres et verdict par rapport à un seuil calibré (point d'égale erreur).

Commandes :
  juge.py calibrer  --chant D --parole D --leurres D --sortie calib.json
  juge.py juger     --refs D --clips F... --calib calib.json
  juge.py leurres-vocalset --parquet P --sortie D        (extrait des leurres d'un parquet VocalSet)
  juge.py valider   --sorties D --refs D --calib calib.json --md resultat.md

Les résultats chiffrés ne sont jamais écrits dans le code : ils vont dans un dossier ignoré par git.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from math import gcd
from pathlib import Path

import numpy as np

SR = 16000
CACHE = Path(os.path.expanduser("~/.cache/voixla-realisme"))
EXT_AUDIO = (".wav", ".flac", ".mp3", ".ogg", ".m4a")
NOMS_ENCODEURS = ("wavlm-sv", "ecapa")


# ----------------------------------------------------------------------------- audio

def charger_audio(chemin, sr: int = SR) -> np.ndarray:
    """Charge un fichier audio en mono float32 à `sr` Hz."""
    import soundfile as sf
    from scipy.signal import resample_poly

    y, sr0 = sf.read(str(chemin), dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    if sr0 != sr:
        g = gcd(int(sr0), int(sr))
        y = resample_poly(y, sr // g, sr0 // g).astype(np.float32)
    return y


def fichiers_audio(chemin) -> list[Path]:
    p = Path(os.path.expanduser(str(chemin)))
    if p.is_file():
        return [p]
    return sorted(f for f in p.rglob("*") if f.suffix.lower() in EXT_AUDIO)


def _niveau_db(y: np.ndarray, sr: int, fen: float = 0.05) -> np.ndarray:
    n = max(1, int(fen * sr))
    k = len(y) // n
    if k == 0:
        return np.array([-120.0])
    r = np.sqrt(np.mean(y[: k * n].reshape(k, n) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(r + 1e-9)


def part_active(y: np.ndarray, sr: int = SR, marge_db: float = 35.0) -> float:
    """Part de trames de 50 ms qui portent du signal (à moins de `marge_db` du pic de niveau)."""
    niv = _niveau_db(y, sr)
    return float(np.mean(niv > niv.max() - marge_db)) if niv.max() > -80 else 0.0


def decouper(y: np.ndarray, sr: int = SR, duree: float = 8.0, pas: float | None = None,
             activite_min: float = 0.6, maximum: int | None = None) -> list[tuple[float, np.ndarray]]:
    """Découpe en clips de `duree` s ; écarte les clips presque silencieux. Rend [(début_s, clip)].

    Un enregistrement plus court que 4 s est rendu entier. `maximum` répartit les clips retenus.
    """
    n = int(duree * sr)
    pas_n = int((pas or duree) * sr)
    if len(y) < 4 * sr:
        return []
    if len(y) <= n:
        return [(0.0, y)]
    # le niveau de référence est celui du morceau entier, pas celui de chaque clip
    niv_global = _niveau_db(y, sr).max()
    clips = []
    for d in range(0, len(y) - n + 1, pas_n):
        c = y[d:d + n]
        niv = _niveau_db(c, sr)
        if np.mean(niv > niv_global - 35.0) >= activite_min:
            clips.append((d / sr, c))
    if maximum and len(clips) > maximum:
        idx = np.linspace(0, len(clips) - 1, maximum).round().astype(int)
        clips = [clips[i] for i in sorted(set(idx))]
    return clips


# ----------------------------------------------------------------------------- encodeurs

class Encodeur:
    nom = ""
    licence = ""

    def plonger(self, y: np.ndarray) -> np.ndarray:  # y : mono 16 kHz
        raise NotImplementedError

    def plonger_clips(self, clips) -> np.ndarray:
        e = np.stack([self.plonger(c) for c in clips]) if len(clips) else np.zeros((0, 1))
        return e


def _unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64).ravel()
    return v / (np.linalg.norm(v) + 1e-12)


class WavLMSV(Encodeur):
    nom = "wavlm-sv"
    licence = "modèle microsoft/wavlm-base-plus-sv (fiche du modèle : licence à consulter sur Hugging Face)"

    def __init__(self):
        import torch
        from transformers import AutoFeatureExtractor, WavLMForXVector
        torch.set_num_threads(int(os.environ.get("JUGE_THREADS", "4")))
        self._torch = torch
        self.fe = AutoFeatureExtractor.from_pretrained("microsoft/wavlm-base-plus-sv")
        self.m = WavLMForXVector.from_pretrained("microsoft/wavlm-base-plus-sv").eval()

    def plonger(self, y):
        t = self._torch
        x = self.fe(y, sampling_rate=SR, return_tensors="pt")
        with t.inference_mode():
            e = self.m(**x).embeddings
        return _unit(e[0].numpy())


class Ecapa(Encodeur):
    nom = "ecapa"
    licence = "SpeechBrain spkrec-ecapa-voxceleb : Apache-2.0"

    def __init__(self):
        import torch
        from speechbrain.inference.speaker import EncoderClassifier
        torch.set_num_threads(int(os.environ.get("JUGE_THREADS", "4")))
        self._torch = torch
        self.m = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(CACHE / "ecapa"), run_opts={"device": "cpu"})

    def plonger(self, y):
        t = self._torch
        with t.inference_mode():
            e = self.m.encode_batch(t.from_numpy(np.ascontiguousarray(y))[None, :])
        return _unit(e.reshape(-1).numpy())


def charger_encodeur(nom: str) -> Encodeur:
    if nom == "wavlm-sv":
        return WavLMSV()
    if nom == "ecapa":
        return Ecapa()
    raise ValueError(f"encodeur inconnu : {nom} (choix : {', '.join(NOMS_ENCODEURS)})")


# ----------------------------------------------------------------------------- scores

def centroide(emb: np.ndarray) -> np.ndarray:
    return _unit(np.mean(np.asarray(emb, dtype=np.float64), axis=0))


def cosinus(emb: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Cosinus de chaque ligne de `emb` avec le vecteur `ref` (tous deux normalisés)."""
    emb = np.asarray(emb, dtype=np.float64)
    emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-12)
    return emb @ _unit(ref)


def seuil_eer(positifs, negatifs) -> dict:
    """Seuil d'égale erreur : taux de faux rejets (positifs < seuil) = taux de fausses acceptations."""
    pos = np.sort(np.asarray(positifs, dtype=float))
    neg = np.sort(np.asarray(negatifs, dtype=float))
    cand = np.unique(np.concatenate([pos, neg]))
    cand = np.concatenate([[cand[0] - 1e-6], (cand[:-1] + cand[1:]) / 2, [cand[-1] + 1e-6]])
    meilleur = None
    for s in cand:
        frr = float(np.mean(pos < s))
        far = float(np.mean(neg >= s))
        d = abs(frr - far)
        if meilleur is None or d < meilleur[0] - 1e-12:
            meilleur = (d, float(s), frr, far)
    _, s, frr, far = meilleur
    return {"seuil": s, "eer": (frr + far) / 2, "frr": frr, "far": far}


def rang_parmi_leurres(score: float, scores_leurres) -> int:
    """1 = au-dessus de tous les leurres ; n+1 = en dessous de tous."""
    sl = np.asarray(scores_leurres, dtype=float)
    return int(1 + np.sum(sl >= score))


def verdict(score: float, seuil: float, marge: float) -> str:
    if score >= seuil + marge:
        return "oui"
    if score <= seuil - marge:
        return "non"
    return "limite"


def _stats(x) -> dict:
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return {"n": 0}
    return {"n": int(x.size), "moyenne": float(x.mean()), "ecart_type": float(x.std()),
            "min": float(x.min()), "p10": float(np.percentile(x, 10)),
            "mediane": float(np.median(x)), "p90": float(np.percentile(x, 90)), "max": float(x.max())}


# ----------------------------------------------------------------------------- embeddings d'un dossier

def plonger_dossier(enc: Encodeur, chemin, duree: float = 8.0, pas: float | None = None,
                    maximum_par_fichier: int | None = 12) -> tuple[np.ndarray, list[str]]:
    """Découpe chaque fichier du dossier en clips et rend (embeddings, étiquettes « fichier@début »)."""
    embs, etiq = [], []
    for f in fichiers_audio(chemin):
        y = charger_audio(f)
        for d, c in decouper(y, duree=duree, pas=pas, maximum=maximum_par_fichier):
            embs.append(enc.plonger(c))
            etiq.append(f"{f.stem}@{d:.0f}")
    return (np.stack(embs) if embs else np.zeros((0, 1))), etiq


# ----------------------------------------------------------------------------- calibration

def calibrer_embeddings(chant, parole, leurres, graine: int = 0) -> dict:
    """Calibre un encodeur à partir d'embeddings (tableaux n × d, ou None).

    Références = centroïde du chant s'il existe, sinon de la parole (calibration « provisoire »).
    Les clips positifs sont notés en laissant le clip de côté. Le seuil d'égale erreur sépare
    les positifs de même domaine que les références (chant, ou parole faute de chant) des leurres.
    """
    chant = None if chant is None or len(chant) == 0 else np.asarray(chant)
    parole = None if parole is None or len(parole) == 0 else np.asarray(parole)
    leurres = np.asarray(leurres)
    if chant is None and parole is None:
        raise ValueError("il faut du chant ou de la parole de la voix cible")
    base = chant if chant is not None else parole

    def sans_soi(emb, i):
        return centroide(np.delete(emb, i, axis=0))

    pos_base = np.array([float(cosinus(base[i:i + 1], sans_soi(base, i))[0]) for i in range(len(base))])
    ref = centroide(base)
    pos_parole = None
    if chant is not None and parole is not None:
        pos_parole = cosinus(parole, ref)
    s_leurres = cosinus(leurres, ref)
    res = seuil_eer(pos_base, s_leurres)
    marge = 0.5 * float(np.std(pos_base)) if len(pos_base) > 1 else 0.0
    out = {
        "provisoire": chant is None,
        "domaine_positifs": "chant" if chant is not None else "parole",
        "distribution_positifs": _stats(pos_base),
        "distribution_parole": _stats(pos_parole) if pos_parole is not None else None,
        "distribution_leurres": _stats(s_leurres),
        "seuil": res["seuil"], "eer": res["eer"], "frr": res["frr"], "far": res["far"],
        "marge_limite": marge,
    }
    if pos_parole is not None:
        out["parole_au_dessus_du_seuil"] = float(np.mean(pos_parole >= res["seuil"]))
    return out


def calibrer(dossier_chant, dossier_parole, dossier_leurres, sortie, encodeurs=NOMS_ENCODEURS,
             duree: float = 8.0, pas: float | None = None, notes: str = "") -> dict:
    """Calibre chaque encodeur ; écrit `sortie` (JSON) et `sortie`.npz (embeddings des leurres)."""
    sortie = Path(sortie)
    resultat = {"encodeurs": {}, "duree_clip_s": duree, "notes": notes}
    npz = {}
    for nom in encodeurs:
        enc = charger_encodeur(nom)
        chant = plonger_dossier(enc, dossier_chant, duree, pas)[0] if dossier_chant else None
        parole = plonger_dossier(enc, dossier_parole, duree, pas or duree / 2)[0] if dossier_parole else None
        leurres, etiq_l = plonger_dossier(enc, dossier_leurres, duree, pas)
        if len(leurres) < 2:
            raise ValueError("pas assez de leurres")
        cal = calibrer_embeddings(chant, parole, leurres)
        cal["licence_encodeur"] = enc.licence
        cal["n_leurres_fichiers"] = len({e.split("@")[0] for e in etiq_l})
        resultat["encodeurs"][nom] = cal
        npz[f"leurres_{nom}"] = leurres
        base = chant if chant is not None else parole
        npz[f"reference_{nom}"] = centroide(base)
        del enc
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps(resultat, indent=2, ensure_ascii=False))
    np.savez(sortie.with_suffix(".npz"), **npz)
    return resultat


def charger_calibration(chemin) -> tuple[dict, dict]:
    chemin = Path(chemin)
    cal = json.loads(chemin.read_text())
    npz = chemin.with_suffix(".npz")
    emb = dict(np.load(npz)) if npz.exists() else {}
    return cal, emb


# ----------------------------------------------------------------------------- jugement

def juger_embeddings(nom: str, emb_clips, emb_refs, cal: dict, emb_cal: dict) -> list[dict]:
    """Pour chaque clip : cosinus, rang parmi les leurres, verdict (un encodeur)."""
    c = cal["encodeurs"][nom]
    ref = centroide(emb_refs)
    cos = cosinus(emb_clips, ref)
    leurres = emb_cal.get(f"leurres_{nom}")
    s_l = cosinus(leurres, ref) if leurres is not None else None
    sorties = []
    for s in cos:
        d = {"cosinus": float(s), "verdict": verdict(float(s), c["seuil"], c["marge_limite"])}
        if s_l is not None:
            d["rang"] = rang_parmi_leurres(float(s), s_l)
            d["n_leurres"] = int(len(s_l))
        sorties.append(d)
    return sorties


def juger(clips, refs, cal_chemin, encodeurs=NOMS_ENCODEURS) -> dict:
    """`clips` : liste de (nom, np.ndarray mono 16 kHz) ; `refs` : dossier ou fichiers de référence."""
    cal, emb_cal = charger_calibration(cal_chemin)
    sortie = {nom: {} for nom, _ in clips}
    for e in encodeurs:
        enc = charger_encodeur(e)
        er = plonger_dossier(enc, refs, 8.0, 4.0)[0]
        ec = enc.plonger_clips([c for _, c in clips])
        for (nom, _), r in zip(clips, juger_embeddings(e, ec, er, cal, emb_cal)):
            sortie[nom][e] = r
    return sortie


# ----------------------------------------------------------------------------- leurres VocalSet

def extraire_leurres_vocalset(parquet, dossier_sortie, hommes_seulement: bool = True,
                              clips_par_chanteur: int = 12, duree: float = 8.0) -> dict:
    """Extrait des leurres (voix d'hommes chantées) d'un parquet du miroir de VocalSet (CC BY 4.0).

    Les noms de fichier VocalSet commencent par l'identifiant du chanteur (m1, f2...). Les exercices d'un
    chanteur sont mis bout à bout, puis découpés en clips de `duree` s, répartis sur tout le corpus.
    """
    import io
    import pyarrow.parquet as pq
    import soundfile as sf
    from scipy.signal import resample_poly

    pf = pq.ParquetFile(str(parquet))
    par_chanteur: dict[str, list[np.ndarray]] = {}
    for g in range(pf.metadata.num_row_groups):
        for ligne in pf.read_row_group(g, columns=["audio"]).to_pylist():
            nom = os.path.basename(ligne["audio"]["path"] or "")
            spk = nom.split("_")[0]
            if hommes_seulement and not spk.startswith("m"):
                continue
            y, sr = sf.read(io.BytesIO(ligne["audio"]["bytes"]), dtype="float32", always_2d=True)
            y = y.mean(axis=1)
            if sr != SR:
                k = gcd(sr, SR)
                y = resample_poly(y, SR // k, sr // k).astype(np.float32)
            par_chanteur.setdefault(spk, []).append(y)
    dossier_sortie = Path(dossier_sortie)
    resume = {}
    for spk, listes in sorted(par_chanteur.items()):
        # un exercice sur trois, pour couvrir toutes les tessitures sans tout garder
        tout = np.concatenate(listes[:: max(1, len(listes) // (clips_par_chanteur * 2))])
        clips = decouper(tout, duree=duree, maximum=clips_par_chanteur)
        (dossier_sortie / spk).mkdir(parents=True, exist_ok=True)
        for i, (_, c) in enumerate(clips):
            sf.write(str(dossier_sortie / spk / f"{spk}_{i:02d}.wav"), c, SR)
        resume[spk] = len(clips)
    return resume


# ----------------------------------------------------------------------------- validation

def _trouver(travail: Path, motifs) -> Path | None:
    for m in motifs:
        t = sorted(travail.glob(m))
        if t:
            return t[0]
    return None


def valider(racine_sorties, refs, cal_chemin, md_sortie, encodeurs=NOMS_ENCODEURS, clips_par_chanson=10) -> dict:
    """Passe le juge sur les doublages existants : sortie convertie contre la référence, et contre
    la voix d'origine isolée de la même chanson."""
    cal, emb_cal = charger_calibration(cal_chemin)
    racine = Path(os.path.expanduser(str(racine_sorties)))
    encs = {e: charger_encodeur(e) for e in encodeurs}
    refs_emb = {e: plonger_dossier(encs[e], refs, 8.0, 4.0)[0] for e in encodeurs}
    resultats = {}
    for travail in sorted(racine.glob("*/travail")):
        conv = _trouver(travail, ["*/voix_convertie.flac", "voix_convertie.flac"])
        orig = _trouver(travail, ["voix_source.wav"])
        if conv is None or orig is None:
            continue
        titre = travail.parent.name
        yc, yo = charger_audio(conv), charger_audio(orig)
        n = min(len(yc), len(yo))
        yc, yo = yc[:n], yo[:n]
        # fenêtres communes : retenues d'après la voix d'origine, qui est la plus propre
        fen = decouper(yo, duree=8.0, maximum=clips_par_chanson)
        deb = [int(d * SR) for d, _ in fen]
        cc = [yc[d:d + 8 * SR] for d in deb]
        co = [yo[d:d + 8 * SR] for d in deb]
        r = {"n_clips": len(deb), "duree_s": n / SR, "duo": "+" in conv.parent.name}
        for e in encodeurs:
            ec = encs[e].plonger_clips(cc)
            eo = encs[e].plonger_clips(co)
            c = cal["encodeurs"][e]
            vs_cible = juger_embeddings(e, ec, refs_emb[e], cal, emb_cal)
            orig_vs_cible = juger_embeddings(e, eo, refs_emb[e], cal, emb_cal)
            # ressemblance de la sortie au chanteur d'origine : cosinus avec le centroïde de sa voix
            cos_orig = cosinus(ec, centroide(eo))
            r[e] = {
                "cos_cible": float(np.mean([v["cosinus"] for v in vs_cible])),
                "rang_cible_moyen": float(np.mean([v["rang"] for v in vs_cible])) if "rang" in vs_cible[0] else None,
                "part_oui_cible": float(np.mean([v["verdict"] == "oui" for v in vs_cible])),
                "cos_cible_original": float(np.mean([v["cosinus"] for v in orig_vs_cible])),
                "cos_original": float(np.mean(cos_orig)),
                "seuil": c["seuil"],
            }
        resultats[titre] = r
    # écrire le résultat dans le dossier ignoré par git
    md = ["# Juge sur les doublages existants", ""]
    md.append("Calibration : " + ("PROVISOIRE (pas de chant de la voix cible, positifs = parole)"
              if next(iter(cal["encodeurs"].values()))["provisoire"] else "avec chant de la voix cible"))
    for e in encodeurs:
        c = cal["encodeurs"][e]
        md.append(f"- {e} : seuil {c['seuil']:.3f}, EER {c['eer']:.2f}, leurres {c['distribution_leurres']['moyenne']:.3f} "
                  f"± {c['distribution_leurres']['ecart_type']:.3f}, positifs {c['distribution_positifs']['moyenne']:.3f} "
                  f"± {c['distribution_positifs']['ecart_type']:.3f}")
    md += ["", "| Chanson | clips | " + " | ".join(f"{e} cible | {e} oui | {e} orig." for e in encodeurs) + " |",
           "|---|---|" + "---|" * (3 * len(encodeurs))]
    for t, r in resultats.items():
        cols = " | ".join(f"{r[e]['cos_cible']:.3f} | {r[e]['part_oui_cible']:.0%} | {r[e]['cos_original']:.3f}"
                          for e in encodeurs)
        md.append(f"| {t}{' (duo)' if r['duo'] else ''} | {r['n_clips']} | {cols} |")
    Path(md_sortie).parent.mkdir(parents=True, exist_ok=True)
    Path(md_sortie).write_text("\n".join(md) + "\n")
    Path(md_sortie).with_suffix(".json").write_text(json.dumps(resultats, indent=2, ensure_ascii=False))
    return resultats


# ----------------------------------------------------------------------------- ligne de commande

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("calibrer")
    c.add_argument("--chant"), c.add_argument("--parole"), c.add_argument("--leurres", required=True)
    c.add_argument("--sortie", required=True), c.add_argument("--notes", default="")
    c.add_argument("--encodeurs", nargs="+", default=list(NOMS_ENCODEURS))
    j = sp.add_parser("juger")
    j.add_argument("--refs", required=True), j.add_argument("--calib", required=True)
    j.add_argument("--clips", nargs="+", required=True), j.add_argument("--sortie")
    j.add_argument("--duree", type=float, default=8.0)
    lv = sp.add_parser("leurres-vocalset")
    lv.add_argument("--parquet", required=True), lv.add_argument("--sortie", required=True)
    v = sp.add_parser("valider")
    v.add_argument("--sorties", required=True), v.add_argument("--refs", required=True)
    v.add_argument("--calib", required=True), v.add_argument("--md", required=True)
    a = p.parse_args(argv)
    if a.cmd == "calibrer":
        r = calibrer(a.chant, a.parole, a.leurres, a.sortie, a.encodeurs, notes=a.notes)
        for e, cal in r["encodeurs"].items():
            print(f"{e}: seuil {cal['seuil']:.3f} EER {cal['eer']:.2f} provisoire={cal['provisoire']}")
    elif a.cmd == "juger":
        clips = []
        for f in a.clips:
            for fichier in fichiers_audio(f):
                for d, y in decouper(charger_audio(fichier), duree=a.duree):
                    clips.append((f"{fichier.name}@{d:.0f}", y))
        r = juger(clips, a.refs, a.calib)
        txt = json.dumps(r, indent=2, ensure_ascii=False)
        Path(a.sortie).write_text(txt) if a.sortie else print(txt)
    elif a.cmd == "leurres-vocalset":
        print(extraire_leurres_vocalset(a.parquet, a.sortie))
    elif a.cmd == "valider":
        valider(a.sorties, a.refs, a.calib, a.md)


if __name__ == "__main__":
    sys.exit(main())
