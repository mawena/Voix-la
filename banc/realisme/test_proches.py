#!/usr/bin/env python3
"""Page « Est-ce lui ? » pour les proches : dossier autonome (HTML local, sans serveur ni réseau) + extraits mp3.

  test_proches.py --manifeste m.json --sortie D [--duree 9] [--graine 1]

Manifeste (JSON) : liste d'entrées
  {"fichier": "voix_seule.flac", "condition": "vrai | A | B | leurre | origine ...", "chanson": "étiquette libre",
   "extraits": 2, "debut": 12.5}
`extraits` (1 par défaut) : nombre d'extraits pris à des endroits différents ; `debut` : début imposé (s).
Les extraits (8 à 10 s, voix seule) sont mis au même niveau puis mélangés au hasard. Sortie :
  D/test/index.html, D/test/audio/*.mp3   -> le dossier à donner aux proches (rien n'y révèle la condition)
  D/cles.json                              -> la clé (extrait -> condition), à garder ; lue par depouiller.py
Les réponses se font sur la page, restent dans le navigateur et s'exportent en un fichier JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SR_MP3 = 32000
NIVEAU_CIBLE_DB = -20.0     # niveau RMS des trames actives, identique pour tous les extraits
MODELE = Path(__file__).resolve().parent / "page" / "modele.html"


def choisir_extraits(y: np.ndarray, sr: int, duree: float, nombre: int, debut: float | None = None,
                     activite_min: float = 0.8) -> list[tuple[float, np.ndarray]]:
    """Choisit `nombre` extraits de `duree` s, bien chantés (peu de silence), répartis sur l'enregistrement."""
    from juge import decouper
    if debut is not None:
        d = int(debut * sr)
        return [(debut, y[d:d + int(duree * sr)])]
    cand = decouper(y, sr=sr, duree=duree, pas=duree / 2, activite_min=activite_min)
    if not cand:    # enregistrement court ou très aéré : on relâche l'exigence
        cand = decouper(y, sr=sr, duree=min(duree, len(y) / sr), activite_min=0.3)
    if not cand:
        raise ValueError("enregistrement trop court ou silencieux pour en tirer un extrait")
    # on évite les extraits qui se recouvrent
    idx = np.linspace(0, len(cand) - 1, min(nombre, len(cand))).round().astype(int)
    choisis, derniere_fin = [], -1e9
    for i in sorted(set(idx)):
        d, c = cand[i]
        if d >= derniere_fin:
            choisis.append((d, c))
            derniere_fin = d + duree
    return choisis


def mettre_au_niveau(y: np.ndarray, sr: int, cible_db: float = NIVEAU_CIBLE_DB) -> np.ndarray:
    """Même niveau RMS (sur les trames actives) pour tous les extraits, sans écrêtage, avec de courts fondus."""
    n = max(1, int(0.05 * sr))
    k = len(y) // n
    r = np.sqrt(np.mean(y[: k * n].reshape(k, n) ** 2, axis=1) + 1e-12)
    actif = r > r.max() * 10 ** (-30 / 20)
    niveau = np.sqrt(np.mean(r[actif] ** 2)) if actif.any() else r.mean()
    gain = 10 ** (cible_db / 20) / (niveau + 1e-12)
    gain = min(gain, 0.97 / (np.max(np.abs(y)) + 1e-12))
    out = (y * gain).astype(np.float32)
    f = min(int(0.05 * sr), len(out) // 2)
    rampe = np.linspace(0, 1, f, dtype=np.float32)
    out[:f] *= rampe
    out[-f:] *= rampe[::-1]
    return out


def ecrire_mp3(y: np.ndarray, sr: int, chemin: Path) -> None:
    import soundfile as sf
    with tempfile.TemporaryDirectory() as t:
        wav = Path(t) / "x.wav"
        sf.write(str(wav), y, sr)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-map_metadata", "-1",
                        "-codec:a", "libmp3lame", "-q:a", "3", "-ac", "1", str(chemin)], check=True)


def generer(manifeste: list[dict], sortie, duree: float = 9.0, graine: int | None = None) -> dict:
    from juge import charger_audio
    if not 8.0 <= duree <= 10.0:
        raise ValueError("la durée d'un extrait doit être comprise entre 8 et 10 s")
    rng = random.Random(graine)
    sortie = Path(os.path.expanduser(str(sortie)))
    dossier = sortie / "test"
    (dossier / "audio").mkdir(parents=True, exist_ok=True)
    session = uuid.UUID(int=rng.getrandbits(128)).hex[:10]
    items = []
    for e in manifeste:
        y = charger_audio(e["fichier"], SR_MP3)
        for d, c in choisir_extraits(y, SR_MP3, duree, int(e.get("extraits", 1)), e.get("debut")):
            items.append({"condition": str(e["condition"]), "chanson": str(e.get("chanson", "")),
                          "source": Path(e["fichier"]).name, "debut_s": round(float(d), 2), "audio": c})
    rng.shuffle(items)
    clips, cles = [], []
    for i, it in enumerate(items, 1):
        ident = f"e{i:02d}"
        nom = f"audio/{ident}-{rng.getrandbits(24):06x}.mp3"     # nom neutre : rien ne trahit la condition
        ecrire_mp3(mettre_au_niveau(it.pop("audio"), SR_MP3), SR_MP3, dossier / nom)
        clips.append({"id": ident, "fichier": nom})
        cles.append({"id": ident, "fichier": nom, **it})
    page = MODELE.read_text()
    page = page.replace("__SESSION__", session).replace("__CLIPS__", json.dumps(clips))
    (dossier / "index.html").write_text(page)
    cle = {"session": session, "duree_extrait_s": duree, "extraits": cles}
    (sortie / "cles.json").write_text(json.dumps(cle, indent=2, ensure_ascii=False))
    return cle


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifeste", required=True), p.add_argument("--sortie", required=True)
    p.add_argument("--duree", type=float, default=9.0), p.add_argument("--graine", type=int)
    a = p.parse_args(argv)
    manifeste = json.loads(Path(a.manifeste).read_text())
    cle = generer(manifeste, a.sortie, a.duree, a.graine)
    n = len(cle["extraits"])
    print(f"{n} extraits ; page : {Path(a.sortie) / 'test' / 'index.html'} ; clé : {Path(a.sortie) / 'cles.json'}")


if __name__ == "__main__":
    sys.exit(main())
