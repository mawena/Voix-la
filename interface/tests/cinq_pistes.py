"""Doublages de synthèse à cinq pistes audio (Doublage, Original, Beat seul, Voix convertie seule, Voix d'origine
seule), faits par les fonctions de doubler.py (preparer_source, finir) comme au retour de Kaggle : mix, MP3, vidéo
doublée remise en son, rapport.json (pistes_audio) et, sur demande, paroles.json calé sur la vidéo (decalage_s).

Sons de synthèse : bruits à bande limitée, un par piste (musique, voix d'origine, voix convertie), qu'une corrélation
retrouve sans ambiguïté dans le son décodé. Vidéos de quelques secondes, titres et voix fictifs, tout dans le dossier
temporaire de l'essai (doubler.py y est copié, HOME aussi : voir test_pistes.charger_doubler)."""

import argparse
import contextlib
import io
import json
import subprocess
from pathlib import Path

SR = 44100
JULES = {"id": "jules", "affichage": "Jules"}
PAROLES = {"format": 1, "langue": "es", "source": "whisper-large-v3", "decalage_s": 0.0,
           "segments": [{"debut": 1.0, "fin": 2.0, "texte": "el gato come pan",
                         "mots": [{"debut": 1.0, "fin": 1.4, "mot": "el"}]}],
           "traductions": {"fr": {"source": "nllb-200-distilled-600M",
                                  "segments": [{"debut": 1.0, "fin": 2.0, "texte": "le chat mange du pain"}]}}}
GRAINES = {"musique": 1, "voix_origine": 2, "voix_convertie": 3}


def bruit(n, graine, niveau_db, bande=(150.0, 5000.0)):
    """Bruit à bande limitée, RMS niveau_db dBFS (pic de corrélation net, bien gardé par l'AAC et l'Opus)."""
    import numpy as np
    x = np.random.default_rng(graine).standard_normal(n)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    X[(f < bande[0]) | (f > bande[1])] = 0
    y = np.fft.irfft(X, n)
    return y * 10 ** (niveau_db / 20) / np.sqrt(np.mean(y ** 2))


def ecrire_wav(chemin, x, canaux=2):
    import numpy as np
    x = np.asarray(x, np.float32)
    if canaux == 2 and x.ndim == 1:
        x = np.stack([x, x], axis=1)
    subprocess.run(["nice", "-n", "10", "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "f32le", "-ar", str(SR),
                    "-ac", str(canaux), "-i", "-", "-c:a", "pcm_f32le", str(chemin)], input=x.tobytes(), check=True,
                   timeout=120)


def video_source(chemin, duree=6.0, retard=0.0, gop=25, images_b=False):
    """Vidéo de synthèse (testsrc, 25 i/s, une image clé toutes les gop images ; images B si images_b), son en
    espagnol, en retard de retard s sur l'image ; mp4/mkv en mpeg4 et AAC, webm en VP8 et Opus."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    webm = chemin.suffix == ".webm"
    cmd = ["nice", "-n", "10", "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi",
           "-i", f"testsrc=size=160x90:rate=25:duration={duree}", *(["-itsoffset", str(retard)] if retard else []),
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={duree}:sample_rate={SR}", "-map", "0:v", "-map", "1:a",
           *(["-c:v", "libvpx", "-b:v", "200k", "-deadline", "realtime"] if webm else ["-c:v", "mpeg4"]),
           "-g", str(gop), *(["-bf", "2"] if images_b else []), "-c:a", "libopus" if webm else "aac",
           "-metadata:s:a:0", "language=spa", str(chemin)]
    subprocess.run(cmd, check=True, timeout=120)
    return chemin


def doubler_cinq_pistes(doubler, donnees, nom, ext=".mp4", extrait=None, retard=0.0, duree=6.0, mode="chant",
                        pistes="toutes", paroles=False, gop=25, images_b=False, image_cle=None, voix=JULES,
                        source=None):
    """Un doublage complet dans <donnees>/sorties/<nom>/ par doubler.finir : vidéo doublée (cinq pistes, ou une seule
    si pistes « doublage »), MP3, travail/ (original.wav, instrumental.wav, voix_source.wav, <voix>/rapport.json…).
    extrait : (début, durée) ; image_cle : remplace doubler.image_cle (début d'extrait hors image clé : la copie de
    l'image part alors d'une image clé antérieure, « image sans son au début ») ; source : une vidéo d'origine déjà
    faite (sinon video_source). Rend le dossier de sortie."""
    source = source or video_source(Path(donnees) / "Vidéos" / f"{nom}{ext}", duree, retard, gop, images_b)
    dossier = Path(donnees) / "sorties" / nom
    t = dossier / "travail"
    tv = t / voix["id"]
    tv.mkdir(parents=True)
    ancien = doubler.image_cle
    if image_cle is not None:
        doubler.image_cle = image_cle
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            doubler.preparer_source(source, doubler.sonder(source), *(extrait or (None, None)), t)
            n = doubler.echantillons(t / "original.wav")[0]
            musique, voix_o = bruit(n, GRAINES["musique"], -20), bruit(n, GRAINES["voix_origine"], -22)
            ecrire_wav(t / "original.wav", musique + voix_o)
            ecrire_wav(t / "voix_source.wav", voix_o)
            ecrire_wav(tv / "voix.wav", bruit(n, GRAINES["voix_convertie"], -22), canaux=1)
            subprocess.run(["nice", "-n", "10", "ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(tv / "voix.wav"),
                            "-c:a", "flac", "-sample_fmt", "s32", str(tv / "voix_convertie.flac")], check=True,
                           timeout=120)
            (tv / "voix.wav").unlink()
            (tv / "rapport.json").write_text(json.dumps({"mode": mode, "frise": []}))
            if paroles:
                (tv / "paroles.json").write_text(json.dumps(PAROLES))
            a = argparse.Namespace(voix_db=None, mix_v1=False, reverb_db=-13.0, paroles=paroles, pistes_audio=pistes,
                                   autotune=None, sans_autotune=False, autotune_gamme=None)
            a.autotune_reglage = doubler.reglage_autotune(a, {})
            doubler.finir({"travail": t, "tv": tv, "dossier": dossier, "base": f"{nom} - voix {voix['affichage']}",
                           "nom": nom}, a, voix)
    finally:
        doubler.image_cle = ancien
    return dossier


def decoder(fichier, carte="0:a:0", sr=SR):
    """Son mono d'une piste (float64), décodé par ffmpeg."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(fichier), "-map", carte, "-ac", "1", "-ar",
                        str(sr), "-f", "f32le", "-"], capture_output=True, check=True, timeout=120)
    return np.frombuffer(r.stdout, np.float32).astype(np.float64)


def position(son, ref, sr=SR):
    """(où commence ref dans son, en échantillons ; pic de corrélation normalisé) : une seconde du milieu de ref
    cherchée dans son."""
    import numpy as np
    p0 = max(0, len(ref) // 2 - sr // 2)
    morceau = ref[p0:p0 + sr]
    n = 1 << int(np.ceil(np.log2(len(son) + len(morceau))))
    c = np.fft.irfft(np.fft.rfft(son, n) * np.conj(np.fft.rfft(morceau, n)), n)[:max(1, len(son) - len(morceau) + 1)]
    k = int(np.argmax(c))
    pic = c[k] / (np.linalg.norm(morceau) * np.linalg.norm(son[k:k + len(morceau)]) + 1e-12)
    return k - p0, float(pic)


def reference(dossier, piste, voix="jules"):
    """Le son de travail/ que porte une piste de la vidéo doublée (pour la reconnaître par corrélation)."""
    t = Path(dossier) / "travail"
    return {"doublage": t / voix / "mix.wav", "original": t / "original.wav", "beat": t / "instrumental.wav",
            "voix_origine": t / "voix_source.wav"}[piste]
