"""Contrôle de qualité des enregistrements (sans GTK), avec ffmpeg, mêmes mesures et mêmes seuils que les scripts
d'enregistrement de parole (enregistrer.sh) et de chant (enregistrer-chant.sh --verifier). À appeler hors du fil
de l'interface (quelques passes ffmpeg courtes)."""

import math
import re
import subprocess
from pathlib import Path

from .formats import nombre

OBJECTIF_CHANT_MIN_S = 5 * 60
OBJECTIF_CHANT_MAX_S = 10 * 60


def _ffmpeg_stderr(args, timeout=120):
    r = subprocess.run(["nice", "-n", "10", "ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True,
                       text=True, timeout=timeout)
    return (r.stderr or "") + (r.stdout or "")


def duree(fichier):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(fichier)],
                           capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def mesures(fichier):
    """Durée, crête et niveau moyen (dBFS), sonie (LUFS), niveaux RMS par fenêtres de 100 ms triés."""
    f = str(fichier)
    d = duree(f)
    vd = _ffmpeg_stderr(["-i", f, "-af", "volumedetect", "-f", "null", "-"])
    crete = re.search(r"max_volume: ([-0-9.]+)", vd)
    moyen = re.search(r"mean_volume: ([-0-9.]+)", vd)
    eb = _ffmpeg_stderr(["-i", f, "-af", "ebur128", "-f", "null", "-"])
    lufs = re.findall(r"^\s+I:\s+(-?[0-9.]+) LUFS", eb, re.M)
    fen = _ffmpeg_stderr(["-i", f, "-af", "aformat=channel_layouts=mono,aresample=48000,asetnsamples=n=4800:p=0,"
                                      "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
                          "-f", "null", "-"])
    niveaux = []
    for v in re.findall(r"RMS_level=(\S+)", fen):
        try:
            niveaux.append(-120.0 if v in ("-inf", "inf") else float(v))
        except ValueError:
            pass
    niveaux.sort()
    return {"duree_s": d, "crete_db": float(crete.group(1)) if crete else None,
            "moyen_db": float(moyen.group(1)) if moyen else None, "lufs": float(lufs[-1]) if lufs else None,
            "niveaux": niveaux}


def _centile(trie, q):
    if not trie:
        return None
    return trie[min(len(trie) - 1, int(len(trie) * q))]


def controle_chant(fichier, duree_demandee=0):
    """Comme enregistrer-chant.sh : saturation (crête > −1), niveau trop faible (< −30 LUFS), bruit de fond (5e centile
    des fenêtres > −45 dBFS), trop de silence (> 40 %), prise trop courte (< 30 s ou < moitié de la durée prévue)."""
    m = mesures(fichier)
    alertes, niveaux = [], m["niveaux"]
    if len(niveaux) < 5 or m["duree_s"] is None:
        return {**m, "alertes": ["prise trop courte pour être analysée"], "ok": False}
    bruit = _centile(niveaux, 0.05)
    fort = _centile(niveaux, 0.90)
    seuil = min(fort - 25, -45)
    silence = 100 * sum(1 for n in niveaux if n < seuil) / len(niveaux)
    m.update({"bruit_db": bruit, "silence_pct": silence})
    if m["crete_db"] is not None and m["crete_db"] > -1:
        alertes.append("Saturation : crête proche de 0 dBFS. Reculez à 20 cm ou baissez le volume d’entrée, "
                       "puis refaites une prise.")
    if m["lufs"] is not None and m["lufs"] < -30:
        alertes.append(f"Niveau trop faible ({nombre(m['lufs'])} LUFS) : rapprochez-vous du micro (15 à 20 cm), chantez "
                       "plus franchement ou montez le volume d’entrée.")
    if bruit > -45:
        alertes.append(f"Bruit de fond trop haut ({nombre(bruit)} dBFS) : pièce plus calme, ventilateur et fenêtre fermés, "
                       "pas de musique dans le haut-parleur (des écouteurs sont permis).")
    if silence > 40:
        alertes.append(f"Trop de silence ({nombre(silence, 0)} %) : enchaînez davantage les phrases, moins de pauses.")
    if m["duree_s"] < 30 or (duree_demandee and m["duree_s"] < 0.5 * duree_demandee):
        alertes.append(f"Prise trop courte ({nombre(m['duree_s'], 0)} s) : visez la durée prévue.")
    return {**m, "alertes": alertes, "ok": not alertes}


def controle_parole(fichier):
    """Comme enregistrer.sh : volume moyen (idéal −26 à −16 dB, trop faible sous −28), crête (sous −1), bruit de fond
    (la voix doit dépasser le fond d'au moins 20 dB)."""
    m = mesures(fichier)
    alertes, niveaux = [], m["niveaux"]
    if m["moyen_db"] is None:
        return {**m, "bruit_db": None, "alertes": ["enregistrement illisible"], "ok": False}
    if len(niveaux) < 5 or m["duree_s"] is None:
        return {**m, "bruit_db": None, "alertes": ["enregistrement trop court pour être analysé"], "ok": False}
    bruit = _centile(niveaux, 0.05)
    m["bruit_db"] = bruit
    if m["moyen_db"] < -28:
        alertes.append("Trop faible : rapprochez-vous du micro (20 cm) ou montez le volume d’entrée (Paramètres > Son).")
    if m["crete_db"] is not None and m["crete_db"] > -1:
        alertes.append("Ça sature : éloignez-vous un peu ou baissez le volume d’entrée.")
    if bruit is not None and bruit - m["moyen_db"] > -20:
        alertes.append("Trop de bruit de fond par rapport à la voix : pièce plus calme, ventilateur éteint, "
                       "ou micro de casque.")
    if m["duree_s"] is not None and m["duree_s"] < 20:
        alertes.append(f"Trop court ({nombre(m['duree_s'], 0)} s) : 30 à 60 s de parole sont conseillées.")
    return {**m, "alertes": alertes, "ok": not alertes}


def prises_chant(dossiers):
    """[(Path, durée)] de tous les chant-*.wav de ces dossiers, triés par nom."""
    vus, prises = set(), []
    for d in dossiers:
        d = Path(d).expanduser()
        if not d.is_dir():
            continue
        for f in sorted(d.glob("chant-*.wav")):
            r = f.resolve()
            if r in vus:
                continue
            vus.add(r)
            prises.append((f, duree(f) or 0.0))
    return prises


def avis_total(total_s):
    """Phrase sur le total cumulé face à l'objectif de 5 à 10 min."""
    if total_s < OBJECTIF_CHANT_MIN_S:
        return f"Encore au moins {math.ceil((OBJECTIF_CHANT_MIN_S - total_s) / 60)} min pour atteindre le minimum."
    if total_s < OBJECTIF_CHANT_MAX_S:
        return "Minimum atteint ; vous pouvez continuer jusqu’à l’objectif haut."
    return "Objectif atteint, c’est largement suffisant."
