"""Karaoké (sans GTK, testable) : chanter soi-même sur le beat seul d'un doublage, enregistrer sa prise, la noter
(voixla.justesse) et la mixer avec le beat.

Fichiers :
- <dossier du doublage>/karaoke/ : les prises, en WAV mono 48 kHz 24 bits, chacune avec son JSON (le plan de la prise :
  passage, lignes, latence, mesures de GStreamer ; puis sa note). Une prise pas encore gardée est cachée
  (.prise-AAAAMMJJ-HHMMSS.wav et .json) ; « Garder » la rend visible (prise-….wav et .json) ; « Jeter » l'efface. Les
  prises cachées de plus d'un jour sont effacées à la prise suivante dans ce dossier. Le mix ta voix + beat enregistré
  en MP3 : « <titre> - karaoké AAAA-MM-JJ HHhMM.mp3 ». La bibliothèque ne lit pas ce sous-dossier.
- ~/.cache/voix-la/karaoke/ (VOIXLA_CACHE) : le beat du passage (WAV 48 kHz, la boucle déjà répétée) et le mix (FLAC),
  refaits au besoin ; 500 Mo au plus, les moins récemment utilisés effacés d'abord.

Temps et calage. La chanson compte en temps du son doublé (celui de travail/instrumental.wav, de courbes.json et des
paroles moins leur decalage_s). Le passage [debut, fin), répété « repetitions » fois, devient le beat lu : son temps u
vaut k × durée + (temps de la chanson − debut) au tour k. Le beat et le micro tournent dans un même pipeline GStreamer
(gui/page_karaoke.py) : une seule horloge, et deux mesures prises pendant la prise :
- prise_debut_s : l'instant (temps de l'horloge du pipeline) du premier échantillon enregistré ;
- beat_debut_s : l'instant où le début du beat sort de GStreamer (latence du pipeline comprise).
Reste la latence du matériel (sortie son, casque, micro), que GStreamer ne voit pas : réglée à la main en ms ou
mesurée par les bips (mesurer_latence). Le premier échantillon de la prise tombe alors au temps du beat
  decalage = prise_debut_s − beat_debut_s − latence.

En ligne de commande (lancée par l'interface sous nice, et par file-attente au-delà d'une minute de son) :
  python3 -m voixla.karaoke analyser PRISE.wav [--mix-seulement]    note (dans PRISE.json) et mix en cache
  python3 -m voixla.karaoke mp3 PRISE.wav SORTIE.mp3 [--titre T]    le mix ta voix + beat en MP3
VOIXLA_AVANCEMENT=1 : avancement en lignes JSON.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path

import numpy as np

from . import chemins
from .justesse import Passage

DOSSIER = "karaoke"
SR = 48000
CACHE_MAX_MO = 500
AVANT_LIGNE_S = 2.0          # un extrait commence 2 s avant sa première ligne…
APRES_LIGNE_S = 1.0          # … et finit 1 s après sa dernière
REPETITIONS_MAX = 10
GAIN_MIN_DB, GAIN_MAX_DB = -20.0, 30.0
VOIX_AU_DESSUS_DB = 1.0      # la voix du mix, un peu au-dessus du beat
LIMITE = 0.89                # limiteur du mix (−1 dBFS)
VERSION_MIX = 2              # dans la clé du mix en cache : un mix d'une version précédente est refait
ATTENTE_MAX_S = 86400        # prises cachées (ni gardées ni jetées) effacées au bout d'un jour
MP3 = ["-c:a", "libmp3lame", "-b:a", "256k"]
# calibration : des bips (glissando de 600 à 3000 Hz en 50 ms, sans ambiguïté de période)
BIPS = 4
BIP_DEBUT_S = 1.0
BIP_PERIODE_S = 1.0
BIP_DUREE_S = 0.05
LATENCE_MIN_S, LATENCE_MAX_S = -0.05, 0.8
BIPS_ECART_MAX_S = 0.015


class ErreurKaraoke(RuntimeError):
    pass


def _avancer(fraction, etape):
    if os.environ.get("VOIXLA_AVANCEMENT"):
        print(json.dumps({"avancement": round(fraction, 3), "etape": etape}), flush=True)


def _ffmpeg(*args, timeout=900):
    r = subprocess.run(["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                        *map(str, args)], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise ErreurKaraoke(f"ffmpeg : {r.stderr.strip()[-300:] or r.returncode}")


def _tmp(chemin, suffixe=None):
    chemin = Path(chemin)
    return chemin.with_name(f".{chemin.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp{suffixe or chemin.suffix}")


# --- Doublage : beat, lignes, passage ---------------------------------------------------------------------------------

def dossier_karaoke(dossier):
    return Path(dossier) / DOSSIER


def dossier_cache():
    return chemins.dossier_cache() / "karaoke"


def fichier_beat(travail):
    """Le beat seul du doublage (travail/instrumental.wav), ou None."""
    f = Path(travail) / "instrumental.wav"
    return f if f.is_file() else None


def duree_audio(fichier):
    """Durée d'un son (s) : l'en-tête d'un WAV entier, sinon ffprobe ; None si illisible."""
    try:
        with wave.open(str(fichier), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError, OSError):
        pass
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(fichier)], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def lignes_paroles(paroles):
    """Les lignes des paroles (voixla.paroles.Paroles) en temps du son : [(debut, fin, texte)]."""
    if paroles is None:
        return []
    from . import paroles as P
    ecart = P.ecart_lecture(paroles, False, None)
    sortie = []
    for l in paroles.originale.lignes:
        d, f = l.debut - ecart, l.fin - ecart
        if f > 0 and f > d:
            sortie.append((round(max(0.0, d), 3), round(f, 3), l.texte))
    return sortie


def paroles_du_passage(paroles, passage):
    """Les paroles réduites aux lignes du passage (celles qui y tombent pour plus de la moitié, comme pour la note) :
    pendant la prise, on ne voit que ce qu'on chante."""
    if paroles is None:
        return None
    import dataclasses
    from . import paroles as P
    ecart = P.ecart_lecture(paroles, False, None)

    def dedans(l):
        d, f = l.debut - ecart, l.fin - ecart
        return f > d and min(f, passage.fin) - max(d, passage.debut) >= 0.5 * (f - d)

    def couche(c):
        return dataclasses.replace(c, lignes=[l for l in c.lignes if dedans(l)])
    return dataclasses.replace(paroles, originale=couche(paroles.originale),
                               traductions={k: couche(c) for k, c in paroles.traductions.items()})


def passage_entier(duree):
    return Passage(0.0, float(duree), 1)


def passage_lignes(lignes, premiere, derniere, duree, repetitions=1):
    """Le passage de la ligne premiere à la ligne derniere (incluses), AVANT_LIGNE_S avant et APRES_LIGNE_S après."""
    if not lignes:
        raise ErreurKaraoke("pas de paroles pour choisir un passage")
    i, j = sorted((max(0, min(premiere, len(lignes) - 1)), max(0, min(derniere, len(lignes) - 1))))
    debut = max(0.0, lignes[i][0] - AVANT_LIGNE_S)
    fin = min(float(duree), lignes[j][1] + APRES_LIGNE_S) if duree else lignes[j][1] + APRES_LIGNE_S
    return Passage(round(debut, 3), round(fin, 3), max(1, min(REPETITIONS_MAX, int(repetitions))))


def passage_temps(debut, fin, duree, repetitions=1):
    """Un passage donné par ses temps (secondes), borné à la chanson ; ErreurKaraoke s'il est vide."""
    debut = max(0.0, float(debut))
    fin = min(float(duree), float(fin)) if duree else float(fin)
    if fin - debut < 1.0:
        raise ErreurKaraoke("passage trop court (une seconde au moins)")
    return Passage(round(debut, 3), round(fin, 3), max(1, min(REPETITIONS_MAX, int(repetitions))))


# --- Beat du passage (en cache) -----------------------------------------------------------------------------------

def _signature(f):
    p = Path(f).resolve()
    st = p.stat()
    return f"{p}|{st.st_size}|{st.st_mtime_ns}"


def chemin_beat(source, passage):
    cle = hashlib.sha1(f"{_signature(source)}|{passage.debut:.3f}|{passage.fin:.3f}|{passage.repetitions}|{SR}"
                       .encode()).hexdigest()[:24]
    return dossier_cache() / f"{cle}-beat.wav"


def preparer_beat(source, passage):
    """Le beat du passage, répété pour une boucle : WAV 48 kHz stéréo 16 bits en cache (ffmpeg sous nice, quelques
    secondes pour une chanson entière). Rend son chemin."""
    sortie = chemin_beat(source, passage)
    if sortie.is_file():
        os.utime(sortie)
        return sortie
    sortie.parent.mkdir(parents=True, exist_ok=True)
    un = _tmp(sortie)
    tout = _tmp(sortie)
    try:
        _ffmpeg("-ss", f"{passage.debut:.3f}", "-t", f"{passage.duree:.3f}", "-i", source, "-map", "0:a:0",
                "-ac", 2, "-ar", SR, "-c:a", "pcm_s16le", un)
        if passage.repetitions > 1:
            with wave.open(str(un), "rb") as r:
                params, trames = r.getparams(), r.readframes(r.getnframes())
            with wave.open(str(tout), "wb") as w:
                w.setparams(params)
                for _ in range(passage.repetitions):
                    w.writeframes(trames)
            os.replace(tout, sortie)
        else:
            os.replace(un, sortie)
    finally:
        for f in (un, tout):
            f.unlink(missing_ok=True)
    chemins.purger_dossier(dossier_cache(), CACHE_MAX_MO, garder=[sortie])
    return sortie


# --- Prises -----------------------------------------------------------------------------------------------------------

def nom_prise(quand=None):
    return time.strftime("prise-%Y%m%d-%H%M%S", time.localtime(quand or time.time()))


def nouvelle_prise(dossier, quand=None):
    """Le chemin d'une nouvelle prise (cachée tant qu'elle n'est pas gardée) ; efface d'abord les prises cachées de
    plus d'un jour de ce dossier."""
    d = dossier_karaoke(dossier)
    d.mkdir(parents=True, exist_ok=True)
    purger_en_attente(dossier)
    base = nom_prise(quand)
    f, k = d / f".{base}.wav", 2
    while f.exists() or (d / f"{base}.wav").exists():
        f = d / f".{base}-{k}.wav"
        k += 1
    return f


def plan_de(prise):
    return Path(prise).with_suffix(".json")


def lire_plan(prise):
    d = chemins.lire_json(plan_de(prise), {})
    return d if isinstance(d, dict) else {}


def ecrire_plan(prise, plan):
    chemins.ecrire_json(plan_de(prise), plan)


def en_attente(prise):
    return Path(prise).name.startswith(".")


def garder(prise):
    """La prise devient visible (prise-….wav et .json) ; rend son nouveau chemin."""
    prise = Path(prise)
    if not en_attente(prise):
        return prise
    nouveau = prise.with_name(prise.name[1:])
    os.replace(prise, nouveau)
    if plan_de(prise).exists():
        os.replace(plan_de(prise), plan_de(nouveau))
    return nouveau


def jeter(prise):
    """Efface la prise, son JSON et son mix en cache."""
    prise = Path(prise)
    plan = lire_plan(prise)
    mix = (plan.get("resultat") or {}).get("mix")
    for f in (prise, plan_de(prise), Path(mix) if mix else None):
        if f is not None:
            Path(f).unlink(missing_ok=True)


def prises_gardees(dossier):
    """[(prise, plan)] des prises gardées de ce doublage, de la plus récente à la plus ancienne."""
    d = dossier_karaoke(dossier)
    if not d.is_dir():
        return []
    return [(f, lire_plan(f)) for f in sorted(d.glob("prise-*.wav"), reverse=True)]


def purger_en_attente(dossier, age_s=ATTENTE_MAX_S, maintenant=None):
    """Efface les prises cachées (ni gardées ni jetées) de plus de age_s ; rend leur nombre."""
    d = dossier_karaoke(dossier)
    maintenant = maintenant or time.time()
    n = 0
    for f in d.glob(".prise-*.wav") if d.is_dir() else []:
        try:
            vieille = maintenant - f.stat().st_mtime > age_s
        except OSError:
            continue
        if vieille:
            jeter(f)
            n += 1
    return n


def decalage(plan):
    """Temps du beat lu où tombe le premier échantillon de la prise : mesures de GStreamer moins la latence."""
    m = plan.get("mesure") or {}
    try:
        return (float(m.get("prise_debut_s") or 0.0) - float(m.get("beat_debut_s") or 0.0)
                - float(plan.get("latence_ms") or 0.0) / 1000.0)
    except (TypeError, ValueError):
        return 0.0


def passage_du_plan(plan):
    p = plan.get("passage") or {}
    return Passage(float(p.get("debut") or 0.0), float(p.get("fin") or 0.0), int(p.get("repetitions") or 1))


# --- Niveaux et mix -----------------------------------------------------------------------------------------------

def niveau_db(x):
    x = np.asarray(x, np.float64)
    return float(10 * np.log10(np.mean(x ** 2) + 1e-12)) if len(x) else -120.0


def niveau_actif_db(x, sr=16000, trame_s=0.05, plage_db=30.0):
    """Niveau (RMS, dBFS) des seuls passages chantés : les trames à moins de plage_db de la 95e centile."""
    x = np.asarray(x, np.float64)
    n = int(sr * trame_s)
    if len(x) < n:
        return niveau_db(x)
    k = len(x) // n
    e = 10 * np.log10(np.mean(x[:k * n].reshape(k, n) ** 2, axis=1) + 1e-12)
    haut = np.percentile(e, 95)
    actives = e[e >= haut - plage_db]
    return float(10 * np.log10(np.mean(10 ** (actives / 10)) + 1e-12)) if len(actives) else -120.0


def gain_voix(prise, beat, sr=16000):
    """Gain (dB) qui met la voix chantée un peu au-dessus du beat, borné."""
    g = niveau_db(beat) + VOIX_AU_DESSUS_DB - niveau_actif_db(prise, sr)
    return round(float(min(GAIN_MAX_DB, max(GAIN_MIN_DB, g))), 1)


def chemin_mix(prise, decalage_s, gain_db, beat):
    cle = hashlib.sha1(f"{_signature(prise)}|{_signature(beat)}|{decalage_s:.4f}|{gain_db:.1f}|{VERSION_MIX}"
                       .encode()).hexdigest()[:24]
    return dossier_cache() / f"{cle}-mix.flac"


def graphe_mix(decalage_s, gain_db, sr=SR):
    """Le filtre ffmpeg du mix : la voix (entrée 1) calée sur le beat (entrée 0), au gain donné, en stéréo, puis le
    limiteur (son anticipation compensée : rien ne glisse) ; la durée est celle du beat."""
    if decalage_s >= 0:
        cale = f"adelay=delays={int(round(decalage_s * sr))}S:all=1"
    else:
        cale = f"atrim=start_sample={int(round(-decalage_s * sr))},asetpts=PTS-STARTPTS"
    return (f"[1:a]aresample={sr},aformat=channel_layouts=mono,{cale},volume={gain_db:.2f}dB,"
            f"pan=stereo|c0=c0|c1=c0[v];[0:a]aresample={sr},aformat=channel_layouts=stereo[b];"
            f"[b][v]amix=inputs=2:duration=first:normalize=0,alimiter=limit={LIMITE}:level=0:latency=1[m]")


def mixer(prise, beat, decalage_s, gain_db, sortie):
    """Ta voix + le beat, calés, en FLAC (écriture atomique)."""
    sortie = Path(sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(sortie)
    try:
        _ffmpeg("-i", beat, "-i", prise, "-filter_complex", graphe_mix(decalage_s, gain_db), "-map", "[m]",
                "-c:a", "flac", tmp)
        os.replace(tmp, sortie)
    finally:
        tmp.unlink(missing_ok=True)
    chemins.purger_dossier(dossier_cache(), CACHE_MAX_MO, garder=[sortie])
    return sortie


def nom_mp3(dossier, titre, quand=None):
    """<dossier>/karaoke/<titre> - karaoké AAAA-MM-JJ HHhMM.mp3 (« (2) » si le nom est pris)."""
    d = dossier_karaoke(dossier)
    base = f"{titre} - karaoké {time.strftime('%Y-%m-%d %Hh%M', time.localtime(quand or time.time()))}"
    for c in '/\\:*?"<>|\0':
        base = base.replace(c, "-")
    f, k = d / f"{base}.mp3", 2
    while f.exists():
        f = d / f"{base} ({k}).mp3"
        k += 1
    return f


def exporter_mp3(mix, sortie, titre="", commentaire=""):
    sortie = Path(sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(sortie)
    try:
        meta = []
        if titre:
            meta += ["-metadata", f"title={titre}"]
        if commentaire:
            meta += ["-metadata", f"comment={commentaire}"]
        _ffmpeg("-i", mix, *MP3, *meta, "-f", "mp3", tmp)
        os.replace(tmp, sortie)
    finally:
        tmp.unlink(missing_ok=True)
    return sortie


# --- Calibration de la latence : des bips joués et enregistrés -------------------------------------------------------

def bip(sr=SR, duree=BIP_DUREE_S, f0=600.0, f1=3000.0):
    """Un bip : glissando de f0 à f1 Hz, attaques adoucies (2 ms)."""
    t = np.arange(int(duree * sr)) / sr
    phase = 2 * np.pi * (f0 * t + (f1 - f0) * t ** 2 / (2 * duree))
    env = np.minimum(1.0, np.minimum(t / 0.002, (duree - t) / 0.002))
    return (np.sin(phase) * env).astype(np.float32)


def instants_bips(n=BIPS, debut=BIP_DEBUT_S, periode=BIP_PERIODE_S):
    return [round(debut + k * periode, 3) for k in range(n)]


def ecrire_bips(sortie, n=BIPS, debut=BIP_DEBUT_S, periode=BIP_PERIODE_S, niveau=0.5):
    """Le son de la calibration (WAV stéréo 48 kHz) : n bips, le premier à debut ; rend leurs instants."""
    instants = instants_bips(n, debut, periode)
    x = np.zeros(int((debut + n * periode + 0.5) * SR), np.float32)
    b = bip() * niveau
    for t in instants:
        a = int(round(t * SR))
        x[a:a + len(b)] += b
    sortie = Path(sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(sortie), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.repeat(x[:, None], 2, axis=1) * 32767).astype("<i2").tobytes())
    return instants


def lire_mono(fichier, sr=16000):
    from . import analyse
    return analyse.decoder(fichier, sr)


def mesurer_latence(enregistrement, instants, decalage_mesure=0.0, sr=16000):
    """Latence du matériel d'après l'enregistrement des bips : pour chaque bip joué à l'instant u du beat, son arrivée
    t dans la prise (filtre adapté, corrélation normalisée) ; latence = t + decalage_mesure − u, la médiane des bips
    entendus. decalage_mesure : prise_debut_s − beat_debut_s de la prise. ErreurKaraoke si moins de trois bips sont
    entendus, ou s'ils ne s'accordent pas (écart de plus de 15 ms). Rend {latence_ms, ecart_ms, entendus, total}."""
    x = np.asarray(enregistrement if isinstance(enregistrement, np.ndarray) else lire_mono(enregistrement, sr),
                   np.float64)
    modele = bip(sr).astype(np.float64)
    m = len(modele)
    norme_m = np.sqrt(np.sum(modele ** 2))
    trouves = []
    for u in instants:
        a = int(round((u - decalage_mesure + LATENCE_MIN_S) * sr))
        b = int(round((u - decalage_mesure + LATENCE_MAX_S) * sr)) + m
        a, b = max(0, a), min(len(x), b)
        if b - a <= m:
            continue
        seg = x[a:b]
        c = np.correlate(seg, modele, mode="valid")
        energie = np.convolve(seg ** 2, np.ones(m), mode="valid")
        rho = np.abs(c) / (norme_m * np.sqrt(energie) + 1e-12)
        k = int(np.argmax(rho))
        if rho[k] < 0.5 or np.sqrt(energie[k] / m) < 1e-3:
            continue
        t = (a + k) / sr
        trouves.append(t + decalage_mesure - u)
    if len(trouves) < 3:
        raise ErreurKaraoke(f"bips mal entendus ({len(trouves)} sur {len(instants)}) : sans casque, volume assez fort, "
                            "ou un écouteur tout contre le micro")
    trouves = np.array(trouves)
    med = float(np.median(trouves))
    proches = trouves[np.abs(trouves - med) <= BIPS_ECART_MAX_S]
    if len(proches) < 3:
        raise ErreurKaraoke("les bips ne s’accordent pas (écho, bruit ?) : recommencez dans le calme")
    return {"latence_ms": int(round(float(np.median(proches)) * 1000)),
            "ecart_ms": round(float(proches.max() - proches.min()) * 1000, 1),
            "entendus": int(len(proches)), "total": len(instants)}


# --- Référence et analyse d'une prise -------------------------------------------------------------------------------

def reference(dossier_voix, travail):
    """(F0 de l'original en Hz, pas, origine) : source_hz de courbes.json (ou de courbes-locales.json), sinon YIN sur
    travail/voix_source.* (gardé dans le cache des analyses). ErreurKaraoke sans aucune des deux."""
    from . import analyse
    for nom in ("courbes.json", "courbes-locales.json"):
        if dossier_voix:
            c = analyse.lire_courbes(Path(dossier_voix) / nom)
            if c is not None and (c["source"] > 0).any():
                return np.asarray(c["source"], np.float64), float(c["pas_s"]), nom
    src = analyse.fichier_voix_source(travail) if travail else None
    if src is None:
        raise ErreurKaraoke("ni courbes.json ni voix d’origine (travail/voix_source.wav) : pas de note possible")
    r = analyse.charger_produit(src, "f0") or analyse.analyser(src, ("f0",), ecrire_cache=True)
    return np.asarray(r["f0"], np.float64), float(r["f0_pas"]), "voix_source (YIN)"


def analyser(prise, mix_seulement=False):
    """Note la prise (voixla.justesse) et prépare son mix avec le beat ; écrit la note dans le JSON de la prise et la
    rend."""
    from . import analyse, justesse
    prise = Path(prise)
    plan = lire_plan(prise)
    if not plan:
        raise ErreurKaraoke(f"plan de la prise introuvable : {plan_de(prise)}")
    passage = passage_du_plan(plan)
    if passage.duree <= 0:
        raise ErreurKaraoke("passage vide")
    decal = decalage(plan)
    source = Path((plan.get("beat") or {}).get("source") or "")
    beat = Path((plan.get("beat") or {}).get("fichier") or "")
    if not beat.is_file():
        if not source.is_file():
            raise ErreurKaraoke(f"beat introuvable : {source}")
        beat = preparer_beat(source, passage)
    res = plan.get("resultat") if mix_seulement else None
    if not isinstance(res, dict):
        _avancer(0.05, "hauteur de la prise")
        f0 = analyse.analyser(prise, ("f0",), ecrire_cache=False)["f0"]
        _avancer(0.45, "mélodie de l’original")
        try:
            ref, pas_ref, origine = reference(plan.get("dossier_voix"), plan.get("travail"))
        except ErreurKaraoke as e:
            ref, pas_ref, origine = None, justesse.PAS_S, str(e)
        _avancer(0.6, "note")
        lignes = [(float(d), float(f), str(t)) for d, f, t in plan.get("lignes") or []]
        if ref is not None:
            res = justesse.noter(ref, f0, lignes, passage, decal, analyse.PAS_F0_S, pas_ref)
        else:
            res = {"format": 1, "score": None, "commentaire": justesse.commentaire(None), "tours": [],
                   "raison": origine}
        res["reference"] = origine
    _avancer(0.7, "mix")
    x_prise, x_beat = lire_mono(prise), lire_mono(beat)
    gain = gain_voix(x_prise, x_beat)
    del x_prise, x_beat
    mix = chemin_mix(prise, decal, gain, beat)
    if not mix.is_file():
        mixer(prise, beat, decal, gain, mix)
    res.update({"decalage_s": round(decal, 4), "gain_db": gain, "mix": str(mix), "latence_ms": plan.get("latence_ms"),
                "date": time.strftime("%Y-%m-%dT%H:%M:%S")})
    plan = lire_plan(prise) or plan          # relu : l'interface a pu changer la latence entre-temps
    plan["resultat"] = res
    plan.setdefault("beat", {})["fichier"] = str(beat)
    ecrire_plan(prise, plan)
    _avancer(1.0, "fini")
    return res


def mix_pret(prise):
    """Le mix en cache de cette prise s'il existe encore, sinon None."""
    res = lire_plan(prise).get("resultat") or {}
    f = Path(res["mix"]) if res.get("mix") else None
    return f if f is not None and f.is_file() else None


def exporter(prise, sortie, titre=""):
    """Le mix de la prise en MP3 (refait au besoin), avec la note en commentaire."""
    mix = mix_pret(prise)
    if mix is None:
        analyser(prise, mix_seulement=True)
        mix = mix_pret(prise)
    if mix is None:
        raise ErreurKaraoke("mix impossible")
    score = (lire_plan(prise).get("resultat") or {}).get("score")
    note = f"Karaoké Voix-là : justesse {score}/100" if score is not None else "Karaoké Voix-là"
    return exporter_mp3(mix, sortie, titre, note)


# --- Ligne de commande -------------------------------------------------------------------------------------------

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    debut = time.time()
    try:
        if len(argv) >= 2 and argv[0] == "analyser":
            res = analyser(argv[1], mix_seulement="--mix-seulement" in argv[2:])
            print(json.dumps({"prise": argv[1], "score": res.get("score"), "mix": res.get("mix"),
                              "duree_s": round(time.time() - debut, 2)}, ensure_ascii=False))
            return 0
        if len(argv) >= 3 and argv[0] == "mp3":
            titre = argv[argv.index("--titre") + 1] if "--titre" in argv[3:-1] else ""
            f = exporter(argv[1], argv[2], titre)
            print(json.dumps({"mp3": str(f), "duree_s": round(time.time() - debut, 2)}, ensure_ascii=False))
            return 0
    except (ErreurKaraoke, OSError) as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())

