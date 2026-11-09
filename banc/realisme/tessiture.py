#!/usr/bin/env python3
"""Tessiture : mesure de la hauteur d'une voix et transposition d'une chanson pour la faire entrer dans la zone de confort.

  tessiture.py mesurer --audio D_OU_F... --sortie tessiture.json
  tessiture.py transposer --tessiture tessiture.json --voix-chanson F --sortie plan.json

`mesurer` : hauteur par pyin (librosa, CPU), centiles 5, 10, 50, 90 et 95 en Hz et en notes, zone de confort
(centiles 10 à 90 par défaut). `transposer` : nombre entier de demi-tons à appliquer à TOUTE la chanson pour que la voix
principale tombe dans la zone de confort, avec la marge et les notes extrêmes. Sortie JSON lisible par un autre outil.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SR = 16000
CENTILES = (5, 10, 50, 90, 95)
NOMS_NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
FMIN, FMAX = 65.0, 1100.0   # C2 à C6 environ


def hz_en_midi(hz: float) -> float:
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def midi_en_hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69.0) / 12.0)


def midi_en_note(m: float) -> str:
    """Nom de la note la plus proche, avec l'écart en cents si la hauteur n'est pas juste : « A3 », « F#2+23c »."""
    n = int(round(m))
    cents = int(round((m - n) * 100))
    nom = f"{NOMS_NOTES[n % 12]}{n // 12 - 1}"
    return nom + (f"{cents:+d}c" if cents else "")


def hauteurs_pyin(y: np.ndarray, sr: int = SR, fmin: float = FMIN, fmax: float = FMAX,
                  prob_min: float = 0.6) -> np.ndarray:
    """Hauteurs (Hz) des trames voisées, par pyin (librosa)."""
    import librosa
    f0, voise, prob = librosa.pyin(y, fmin=fmin, fmax=fmax, sr=sr, frame_length=1024, hop_length=320)
    ok = voise & np.isfinite(f0) & (prob >= prob_min)
    return f0[ok]


def tessiture_depuis_f0(f0: np.ndarray, zone=(10, 90)) -> dict:
    """Centiles de hauteur (Hz et notes) et zone de confort, à partir d'une liste de hauteurs en Hz."""
    f0 = np.asarray(f0, dtype=float)
    f0 = f0[np.isfinite(f0) & (f0 > 0)]
    if f0.size < 20:
        raise ValueError("pas assez de trames voisées pour mesurer une tessiture")
    midi = np.array([hz_en_midi(f) for f in f0])
    cent = {c: float(np.percentile(midi, c)) for c in CENTILES}
    out = {
        "n_trames": int(f0.size),
        "centiles": {str(c): {"hz": midi_en_hz(m), "midi": m, "note": midi_en_note(m)} for c, m in cent.items()},
    }
    bas, haut = float(np.percentile(midi, zone[0])), float(np.percentile(midi, zone[1]))
    out["zone_confort"] = {"centiles": list(zone), "bas_midi": bas, "haut_midi": haut,
                           "bas_hz": midi_en_hz(bas), "haut_hz": midi_en_hz(haut),
                           "bas_note": midi_en_note(bas), "haut_note": midi_en_note(haut),
                           "largeur_demitons": haut - bas}
    return out


def mesurer(chemins, zone=(10, 90), duree_max: float = 600.0) -> dict:
    """Mesure la tessiture sur un ou plusieurs enregistrements (fichiers ou dossiers)."""
    from juge import charger_audio, fichiers_audio
    f0s, fichiers = [], []
    reste = duree_max
    for c in chemins:
        for f in fichiers_audio(c):
            if reste <= 0:
                break
            y = charger_audio(f, SR)[: int(reste * SR)]
            reste -= len(y) / SR
            f0s.append(hauteurs_pyin(y))
            fichiers.append(f.name)
    out = tessiture_depuis_f0(np.concatenate(f0s), zone)
    out["n_fichiers"] = len(fichiers)
    out["methode"] = "pyin (librosa), trames voisées de probabilité >= 0,6"
    return out


def proposer_transposition(tessiture_cible: dict, tessiture_chanson: dict, maximum: int = 12) -> dict:
    """Transposition entière (demi-tons) de toute la chanson vers la zone de confort de la voix cible.

    On choisit n qui maximise la marge, c'est-à-dire le plus petit des deux écarts (centile 10 de la chanson
    au-dessus du bas de la zone, centile 90 de la chanson sous le haut de la zone). Marge > 0 : les centiles 10 à 90 de la chanson tiennent dans la zone ; marge < 0 : la chanson est plus
    large que la zone, on rend l'écart le plus petit. À marge égale, on garde le plus petit déplacement.
    """
    z = tessiture_cible["zone_confort"]
    c = tessiture_chanson["centiles"]
    s10, s90 = c["10"]["midi"], c["90"]["midi"]
    s5, s95, s50 = c["5"]["midi"], c["95"]["midi"], c["50"]["midi"]

    def marge(n):
        return min((s10 + n) - z["bas_midi"], z["haut_midi"] - (s90 + n))

    candidats = range(-maximum, maximum + 1)
    n = max(candidats, key=lambda k: (round(marge(k), 6), -abs(k)))
    cible_c = tessiture_cible["centiles"]
    res = {
        "demitons": int(n),
        "marge_demitons": float(marge(n)),
        "zone_confort_note": [z["bas_note"], z["haut_note"]],
        "chanson_apres": {
            "p5": midi_en_note(s5 + n), "p10": midi_en_note(s10 + n), "p50": midi_en_note(s50 + n),
            "p90": midi_en_note(s90 + n), "p95": midi_en_note(s95 + n),
        },
        "chanson_avant": {"p5": midi_en_note(s5), "p50": midi_en_note(s50), "p95": midi_en_note(s95)},
        "note_la_plus_basse_p5": midi_en_note(s5 + n),
        "note_la_plus_haute_p95": midi_en_note(s95 + n),
        # les centiles 5 et 95 de la chanson comparés aux centiles 5 et 95 de la voix cible
        "depassement_bas_demitons": float(max(0.0, cible_c["5"]["midi"] - (s5 + n))),
        "depassement_haut_demitons": float(max(0.0, (s95 + n) - cible_c["95"]["midi"])),
        "sans_transposition": {"marge_demitons": float(marge(0)), "n": 0},
    }
    # variantes au demi-ton le plus proche, pour qu'un autre outil puisse choisir autrement
    res["alternatives"] = [{"demitons": int(k), "marge_demitons": float(marge(k))}
                           for k in sorted(candidats, key=lambda k: (-round(marge(k), 6), abs(k)))[1:4]]
    return res


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    m = sp.add_parser("mesurer")
    m.add_argument("--audio", nargs="+", required=True), m.add_argument("--sortie")
    m.add_argument("--zone", nargs=2, type=int, default=[10, 90])
    t = sp.add_parser("transposer")
    t.add_argument("--tessiture", required=True, help="JSON rendu par `mesurer` pour la voix cible")
    t.add_argument("--voix-chanson", required=True, help="voix principale isolée de la chanson")
    t.add_argument("--sortie"), t.add_argument("--maximum", type=int, default=12)
    a = p.parse_args(argv)
    if a.cmd == "mesurer":
        out = mesurer(a.audio, tuple(a.zone))
    else:
        cible = json.loads(Path(a.tessiture).read_text())
        chanson = mesurer([a.voix_chanson])
        out = {"chanson": chanson, "plan": proposer_transposition(cible, chanson, a.maximum)}
    txt = json.dumps(out, indent=2, ensure_ascii=False)
    Path(a.sortie).write_text(txt) if a.sortie else print(txt)


if __name__ == "__main__":
    sys.exit(main())
