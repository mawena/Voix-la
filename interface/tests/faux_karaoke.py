"""Une chanson inventée pour essayer le karaoké (voixla.karaoke, voixla.justesse) : un doublage de synthèse dont on
connaît la mélodie note à note, et des prises de synthèse (juste, fausse d'un demi-ton, une octave plus bas, en
retard…). Titre, voix et paroles inventés. Rien n'est écrit hors du dossier donné.

- travail/instrumental.wav : le beat (une basse et un battement, 44,1 kHz stéréo) ;
- travail/voix_source.wav : la voix d'origine, une note pure par mot ;
- travail/<voix>/paroles.json (mots minutés), courbes.json (source_hz : la mélodie exacte), rapport.json ;
- « <titre> - voix <Nom>.mp3 » : le doublage (un son pur, pour que le dossier soit complet).

  python3 -m tests.faux_karaoke DONNEES [--prises DOSSIER]
"""

import json
import math
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

TITRE = "Brise de mer"
VOIX = ("jules", "Jules")
SR = 48000
INTRO_S = 2.0
# huit lignes de quatre à six mots : (texte, notes MIDI de chaque mot) ; une note par mot, des sauts d'au moins un ton
LIGNES = [
    ("Le vent se lève sur la baie", [69, 72, 74, 72, 69, 67]),
    ("Les voiles dansent au matin", [64, 67, 69, 71, 69]),
    ("J'entends la mer qui m'appelle", [72, 74, 76, 74, 72]),
    ("Et mon cœur part avec elle", [69, 67, 64, 67, 69]),
    ("Brise de mer, emporte-moi", [76, 74, 72, 74]),
    ("Jusqu'aux îles du soleil", [72, 69, 67, 69, 72]),
    ("Brise de mer, chante pour moi", [76, 74, 72, 71, 69]),
    ("La nuit tombe sur le port", [67, 69, 72, 69, 67, 64]),
]
DUREE_LIGNE = 3.0
ECART_LIGNE = 0.6          # silence entre deux lignes
SILENCE_MOT = 0.04         # entre deux mots


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def notes():
    """[(debut, fin, midi, ligne, mot)] en temps du son doublé."""
    sortie = []
    t = INTRO_S
    for k, (texte, midis) in enumerate(LIGNES):
        mots = texte.split()
        d = (DUREE_LIGNE - SILENCE_MOT * (len(mots) - 1)) / len(mots)
        u = t
        for m, (mot, midi) in enumerate(zip(mots, midis)):
            sortie.append((round(u, 3), round(u + d, 3), midi, k, mot))
            u += d + SILENCE_MOT
        t += DUREE_LIGNE + ECART_LIGNE
    return sortie


def duree_chanson():
    return INTRO_S + len(LIGNES) * (DUREE_LIGNE + ECART_LIGNE) + 1.0


def lignes():
    """[(debut, fin, texte)] en temps du son."""
    sortie = []
    for k, (texte, _m) in enumerate(LIGNES):
        d = INTRO_S + k * (DUREE_LIGNE + ECART_LIGNE)
        sortie.append((round(d, 3), round(d + DUREE_LIGNE, 3), texte))
    return sortie


def paroles():
    segs = []
    tout = notes()
    for k, (d, f, texte) in enumerate(lignes()):
        mots = [{"debut": a, "fin": b, "mot": mot} for a, b, _m, l, mot in tout if l == k]
        segs.append({"debut": d, "fin": f, "texte": texte, "mots": mots})
    return {"format": 1, "langue": "fr", "source": "transcription d’essai", "decalage_s": 0.0, "segments": segs}


def f0_reference(pas=0.02, duree=None):
    """La mélodie exacte, en Hz (0 entre les mots et les lignes), trame i à i × pas."""
    duree = duree or duree_chanson()
    n = int(round(duree / pas))
    f = np.zeros(n, np.float32)
    for d, fin, midi, _l, _m in notes():
        f[int(math.ceil(d / pas)):int(fin / pas)] = hz(midi)
    return f


def voix(transposition=0.0, retard=0.0, vibrato_cents=0.0, bruit_db=None, harmoniques=1, faux=None, graine=0,
         debut=0.0, fin=None, sr=SR, niveau=0.25, tempo=1.0):
    """Une voix de synthèse qui chante la mélodie (temps du son), transposée (demi-tons), en retard (s) ; faux :
    {ligne: écart en demi-tons} ; coupée à [debut, fin) du temps du son, puis décalée de retard. Rend un tableau."""
    rng = np.random.default_rng(graine)
    fin = duree_chanson() if fin is None else fin
    n = int(round((fin - debut + max(0.0, retard)) * sr))
    x = np.zeros(n, np.float64)
    for d, f, midi, ligne, _mot in notes():
        if f <= debut or d >= fin:
            continue
        d2, f2 = max(d, debut), min(f, fin)
        a = int(round((d2 - debut + retard) * sr))
        b = int(round((f2 - debut + retard) * sr))
        if b <= a or a >= n:
            continue
        b = min(b, n)
        t = np.arange(b - a) / sr
        demi = transposition + (faux or {}).get(ligne, 0.0)
        frequence = hz(midi) * 2 ** (demi / 12)
        inst = frequence * 2 ** (vibrato_cents / 1200 * np.sin(2 * np.pi * 5.5 * t)) if vibrato_cents else \
            np.full(len(t), frequence)
        phase = 2 * np.pi * np.cumsum(inst) / sr
        s = sum(np.sin(phase * h) / h for h in range(1, harmoniques + 1))
        env = np.minimum(1.0, np.minimum(t / 0.01, (t[-1] - t + 1 / sr) / 0.01))
        x[a:b] += niveau * s * env
    if bruit_db is not None:
        x += rng.normal(0, 10 ** (bruit_db / 20), n)
    return x.astype(np.float32)


def ecrire_wav(chemin, x, sr=SR, canaux=1):
    """WAV 16 bits (mono, ou stéréo dupliqué)."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    y = np.clip(np.asarray(x, np.float64), -1, 1)
    if canaux == 2:
        y = np.repeat(y[:, None], 2, axis=1)
    with wave.open(str(chemin), "wb") as w:
        w.setnchannels(canaux)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((y * 32767).astype("<i2").tobytes())
    return chemin


def beat(duree=None, sr=44100):
    """Le beat : une basse à 55 Hz par temps (120 bpm) et un battement bref, sans voix."""
    duree = duree or duree_chanson()
    n = int(duree * sr)
    t = np.arange(n) / sr
    x = 0.18 * np.sin(2 * np.pi * 55 * t) * (0.5 + 0.5 * np.cos(2 * np.pi * 2 * t)) ** 4
    rng = np.random.default_rng(7)
    for k in range(int(duree * 2)):
        a = int(k * 0.5 * sr)
        b = min(n, a + int(0.03 * sr))
        x[a:b] += 0.08 * rng.normal(0, 1, b - a) * np.linspace(1, 0, b - a)
    return x


def creer(donnees, titre=TITRE, voix_id=VOIX):
    """Le doublage inventé dans <donnees>/sorties/<titre>/ ; rend le dossier."""
    donnees = Path(donnees)
    d = donnees / "sorties" / titre
    t = d / "travail"
    tv = t / voix_id[0]
    tv.mkdir(parents=True, exist_ok=True)
    duree = duree_chanson()
    ecrire_wav(t / "instrumental.wav", beat(duree), sr=44100, canaux=2)
    ecrire_wav(t / "voix_source.wav", voix(sr=44100), sr=44100, canaux=2)
    (t / "source.json").write_text(json.dumps({"video": str(donnees / "Vidéos" / f"{titre}.mp4"),
                                               "decalage_son": 0.0}, ensure_ascii=False), encoding="utf-8")
    (tv / "rapport.json").write_text(json.dumps({"mode": "chant", "duree_s": duree, "moteur": "yingmusic"}),
                                     encoding="utf-8")
    (tv / "paroles.json").write_text(json.dumps(paroles(), ensure_ascii=False), encoding="utf-8")
    ref = f0_reference()
    (tv / "courbes.json").write_text(json.dumps({"format": 1, "pas_s": 0.02,
                                                 "source_hz": [round(float(v), 1) for v in ref],
                                                 "convertie_hz": [round(float(v), 1) for v in ref],
                                                 "decalage_dt": [0] * len(ref)}), encoding="utf-8")
    subprocess.run(["nice", "-n", "19", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi",
                    "-i", f"sine=frequency=440:sample_rate=44100:duration={duree:g}", "-c:a", "libmp3lame", "-b:a",
                    "64k", str(d / f"{titre} - voix {voix_id[1]}.mp3")], check=True, stdin=subprocess.DEVNULL,
                   timeout=120)
    voix_d = donnees / "voix" / voix_id[0]
    voix_d.mkdir(parents=True, exist_ok=True)
    (voix_d / "voix.json").write_text(json.dumps({"nom": voix_id[1], "reference": "reference.wav", "duree_s": 20.0,
                                                  "consentement": "voix d'essai"}, ensure_ascii=False),
                                      encoding="utf-8")
    return d


PRISES = {
    "juste": {},
    "demi-ton": {"transposition": 1.0},
    "octave-bas": {"transposition": -12.0},
    "retard-100ms": {"retard": 0.1},
    "retard-400ms": {"retard": 0.4},
    "ligne-fausse": {"faux": {4: 2.0}},
    "vibrato": {"vibrato_cents": 40.0, "harmoniques": 3},
}


def main(argv=None):
    import argparse
    a = argparse.ArgumentParser(prog="python3 -m tests.faux_karaoke")
    a.add_argument("donnees")
    a.add_argument("--prises", help="dossier où écrire les prises de synthèse (prise-<genre>.wav)")
    args = a.parse_args(argv)
    print(creer(args.donnees))
    if args.prises:
        for nom, k in PRISES.items():
            print(ecrire_wav(Path(args.prises) / f"prise-{nom}.wav", voix(**k)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
