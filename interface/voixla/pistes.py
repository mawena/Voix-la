"""Pistes séparées d'un doublage (sans GTK, testable) : ce qu'on peut écouter dans la page Résultats, la voix
convertie seule telle qu'elle sonne dans le mix, et les fichiers à enregistrer.

Pistes, dans sorties/<titre>/ :
- doublage : la vidéo doublée « <titre> - voix <Nom>.mp4 » (ou .mkv…), ou son MP3 ;
- original : la vidéo d'origine (travail/source.json, ou celle choisie à la main), sinon travail/original.wav ;
- beat : travail/instrumental.wav, la musique sans aucune voix (original − voix isolée, refait à chaque mixage) ;
- ma_voix : la voix convertie seule, au niveau du mix (voir plan_ma_voix) ;
- voix_origine : travail/voix_source.wav, la voix d'origine isolée.
Toutes sont calées sur le doublage : le lecteur compte en temps du son doublé, dont le 0 est le début du son doublé
(celui du MP3, de travail/original.wav, du beat, des voix seules, de courbes.json et de la frise). Une piste qui a une
image compte en temps de cette image : avance_piste, la seule règle de passage d'un temps à l'autre
(voixla.paroles.ecart_lecture s'en sert aussi), avec pour chaque image l'instant où le son y commence. Dans la vidéo
doublée (et ses copies des pistes séparées, ses pistes audio, sa version auto-tune), c'est debut_son_video :
decalage_s de paroles.json, exact depuis doubler 0.6.0, sinon decalage_son de travail/source.json. Dans la vidéo
d'origine, c'est decalage_son de source.json (le retard de son son ; 0 pour un extrait), plus le début de l'extrait.

« Ma voix seule » refait la part de la voix dans le mix de doubler (fonction mixer de doubler.py), sans la musique :
  voix sèche      travail/<voix>/seche_voix_convertie.wav   au gain gains_db.seche  (rapport.json, clé « mix ») ;
  réverbération   travail/<voix>/reverb_voix_convertie.wav  au gain gains_db.reverb (en chant seulement) ;
additionnées (amix sans normalisation), puis passées au même limiteur (crête vraie, limite_dbtp du rapport), et
coupées à la durée de travail/instrumental.wav comme le mix. Le résultat va en cache, en FLAC, dans
~/.cache/voix-la/voix/, nommé d'après les fichiers et les gains (refait si un remix les change).

Pistes dans la vidéo (doubler ≥ 0.6.0) : la vidéo doublée a ses propres pistes audio, calées entre elles à
l'échantillon près (Doublage, la seule par défaut, puis Original, Beat seul, Voix convertie seule, Voix d'origine
seule ; de même pour la version auto-tune). Elles sont repérées par leur titre, dans l'ordre, d'après rapport.json
(pistes_audio.video, pistes_audio.video_autotune), sinon d'après les titres des flux (pistes_de_la_video). Le beat et
les voix seules s'y lisent alors sur place : même flux que le doublage, autre piste audio (Piste.piste_audio, que le
lecteur donne à current-audio de playbin), sans copie à préparer. L'original reste la vidéo d'origine quand on l'a ;
sinon, c'est la piste Original de la vidéo doublée, sur son image, plutôt que travail/original.wav sans image.

Sinon (anciens doublages, à une seule piste audio), en vidéo, le beat et les voix seules se lisent sur l'image de la
vidéo doublée, qui avance avec le son : une copie préparée une fois en cache (~/.cache/voix-la/videos/), avec le flux
vidéo de la vidéo doublée copié sans réencodage (-c:v copy) et le son de la piste encodé comme doubler le fait (AAC
256k, Opus 192k en webm), coupé à la fin de la vidéo. Le son y commence exactement là où commence le son du doublage, lu
dans la vidéo doublée (voir calage_video) : la copie a les mêmes temps que la vidéo doublée, et on y passe à la même
position. Un seul décodage vidéo à la fois.

En ligne de commande (lancée par l'interface et par le préparateur, voixla/preparation.py : tout de suite, sous
nice -n 19 et ionice -c3, jamais par file-attente) :
  python3 -m voixla.pistes voix DOSSIER_VOIX [--autotune]
  python3 -m voixla.pistes video VIDEO_DOUBLEE (SON | --voix DOSSIER_VOIX [--autotune])
  python3 -m voixla.pistes exporter (SOURCE [--piste-audio N] | --voix DOSSIER_VOIX [--autotune]) SORTIE.mp3|.flac|.wav
  (--autotune : la voix seule de la version auto-tune, d'après seche_voix_autotune.wav et reverb_voix_autotune.wav ;
   --piste-audio N : la piste audio N de SOURCE, une vidéo doublée à plusieurs pistes, au lieu de la première)
  python3 -m voixla.pistes miniature VIDEO_DOUBLEE     (une image de la vidéo, en cache, pour MPRIS)
"""

import contextlib
import fcntl
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import chemins, formats

VERSION = 1
CLES = ("doublage", "original", "beat", "ma_voix", "voix_origine")
LIBELLES = {"doublage": "Doublage", "original": "Original", "beat": "Beat seul", "ma_voix": "Ma voix seule",
            "voix_origine": "Voix d’origine seule"}
INFOBULLES = {"doublage": "Le doublage : la musique et ma voix (touche B : l’original à la même position)",
              "original": "Le son d’origine, à la même position (touche B : retour au doublage)",
              "beat": "La musique seule, sans aucune voix",
              "ma_voix": "Ma voix convertie seule, comme dans le mix : voix sèche et sa réverbération",
              "voix_origine": "La voix d’origine isolée, sans la musique"}
LIMITE_DEFAUT_DBTP = -1.0       # limiteur du mix de doubler quand le rapport ne le dit pas
SR = 44100
CACHE_VOIX_MO = 300             # au-delà, les voix préparées les moins récemment écoutées sont effacées
FORMATS_EXPORT = {".mp3": ["-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3"],
                  ".flac": ["-c:a", "flac", "-f", "flac"],
                  ".wav": ["-c:a", "pcm_s24le", "-f", "wav"]}
SANS_IMAGE = ("beat", "ma_voix", "voix_origine")    # pistes sans image, lues en vidéo par une copie (voir plus bas)
# Une voix par chanteur : « ma voix seule » réunit les voix converties de tous les chanteurs
LIBELLE_VOIX_CONVERTIES = "Voix converties seules"
INFOBULLE_VOIX_CONVERTIES = "Les voix converties seules, comme dans le mix : voix sèches et leur réverbération"
# Version auto-tune (doubler --autotune N, rapport « autotune » appliqué) : la même piste, auto-tunée, lue à la même
# position par la bascule « Auto-tune » (touche T). Fichiers : « <titre> - voix X (auto-tune).<ext> » et .mp3 ; voix
# seule d'après travail/<voix>/seche_voix_autotune.wav et reverb_voix_autotune.wav, aux gains du rapport.
CLES_AUTOTUNE = {"doublage": "doublage_at", "ma_voix": "ma_voix_at"}
SANS_IMAGE_TOUTES = SANS_IMAGE + ("ma_voix_at",)
LIBELLES_AUTOTUNE = {"doublage_at": "Doublage (auto-tune)", "ma_voix_at": "Ma voix seule (auto-tune)"}
# En vidéo, la vidéo auto-tune à plusieurs pistes a aussi l'original, le beat et la voix d'origine, au même son que la
# vidéo normale : avec « Auto-tune », ils s'y lisent sur place (même flux que le doublage auto-tune), sous ces clés.
CLES_AUTOTUNE_VIDEO = {"original": "original_at", "beat": "beat_at", "voix_origine": "voix_origine_at"}
# Titres des pistes audio de la vidéo doublée (doubler.titres_pistes), comparés en minuscules : début du titre → clé
TITRES_PISTES = (("doublage", "doublage"), ("original", "original"), ("beat seul", "beat"),
                 ("voix convertie seule", "ma_voix"), ("voix converties seules", "ma_voix"),
                 ("voix d'origine seule", "voix_origine"))
VERSION_VIDEO = 1
CACHE_VIDEO_MO = 2048           # au-delà, les copies vidéo les moins récemment lues sont effacées (réglable :
                                # préférence cache_pistes_mo, Réglages > Doublage)
CONTENEURS_VIDEO = (".mp4", ".m4v", ".mov", ".mkv", ".webm")    # ceux de doubler ; sinon .mkv, comme lui
SON_VIDEO = {".webm": ["-c:a", "libopus", "-b:a", "192k"]}     # sinon AAC 256k (CONTENEURS de doubler.py)
SON_VIDEO_DEFAUT = ["-c:a", "aac", "-b:a", "256k"]
AMORCES = {"aac": 1024, "opus": 312}   # échantillons d'amorce de ces encodeurs de ffmpeg (aac, libopus à 48 kHz)
# fichiers enregistrés par l'interface à côté des sorties : « <titre> - voix <Nom> (beat).mp3 »… (que
# rapports.EXPORT_DANS_NOM écarte des sorties de doubler)
SUFFIXES_EXPORT = {"beat": " (beat)", "ma_voix": " (voix seule)"}


class ErreurPistes(RuntimeError):
    pass


def _nombre(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _fichier(p):
    return p if p is not None and Path(p).is_file() else None


def bascule_ab(courante):
    """Touche B : du doublage à l'original, et de toute autre piste au doublage."""
    return "original" if courante == "doublage" else "doublage"


# --- Temps du son, temps de la piste ---------------------------------------------------------------------------------

def decalage_son(source):
    """Où commence le son doublé dans la vidéo doublée (s) : « decalage_son » de travail/source.json (le retard du son
    d'origine d'une vidéo entière, que doubler garde ; 0 pour un extrait, dont la vidéo commence sur son image), ou
    None si source.json manque ou ne le dit pas."""
    v = source.get("decalage_son") if isinstance(source, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def avance_piste(avec_image, debut_son):
    """Temps de la piste lue = temps du son doublé + cette avance (s). Une piste qui a une image compte en temps de
    cette image, où le son commence à debut_son (None : 0) : la vidéo doublée, ses copies des pistes séparées et ses
    pistes audio, qui gardent ses temps (debut_son_video), ou la vidéo d'origine (decalage_son de source.json) ; un son
    lu seul (le MP3, travail/original.wav, le beat ou une voix seule sans image, le son seul fenêtre fermée) compte en
    temps du son."""
    return float(debut_son or 0.0) if avec_image else 0.0


def decalage_paroles(dossier_voix):
    """decalage_s de travail/<voix>/paroles.json (s), s'il est valide et positif, sinon None. Depuis doubler 0.6.0,
    c'est l'instant exact de la vidéo doublée où commence le son doublé (plan_video : debut_son), y compris pour un
    extrait dont la copie de l'image part d'une image clé antérieure (« image sans son au début »). Un ancien
    paroles.json d'extrait mkv ou webm portait le décalage donné à ffmpeg (−0,13 s), que le conteneur annule : une
    valeur négative est donc ignorée. Lu une fois par version du fichier (taille, date)."""
    if dossier_voix is None:
        return None
    f = Path(dossier_voix) / "paroles.json"
    try:
        st = f.stat()
    except OSError:
        return None
    cle = (str(f), st.st_size, st.st_mtime_ns)
    if cle not in _DECALAGES:
        d = chemins.lire_json(f, None)
        v = d.get("decalage_s") if isinstance(d, dict) else None
        if len(_DECALAGES) > 200:
            _DECALAGES.clear()
        _DECALAGES[cle] = float(v) if _nombre(v) and v >= 0 else None
    return _DECALAGES[cle]


_DECALAGES = {}


def debut_son_video(res):
    """Où commence le son doublé dans la vidéo doublée (s), la règle des pistes qui ont son image (avance_piste) :
    decalage_s de paroles.json quand il existe (exact), sinon decalage_son de travail/source.json, sinon 0."""
    v = decalage_paroles(getattr(res, "dossier_voix", None))
    if v is not None:
        return v
    return decalage_son(getattr(res, "source", None)) or 0.0


# --- Pistes audio de la vidéo doublée --------------------------------------------------------------------------------

def _titre_norme(titre):
    return " ".join(str(titre).replace("’", "'").split()).casefold()


def cle_du_titre(titre):
    """La clé de piste d'un titre de piste audio de doubler (« Beat seul » → beat), sinon None."""
    t = _titre_norme(titre)
    for debut, cle in TITRES_PISTES:
        if t == debut or t.startswith(debut + " "):
            return cle
    return None


def reperer_pistes(titres):
    """{clé: index de la piste parmi les sons de la vidéo (a:N)} d'après les titres des pistes audio, dans leur ordre :
    doublage, original, beat, ma_voix (voix convertie seule, ou voix converties seules), voix_origine. La première de
    chaque sorte compte, un titre inconnu est sauté sans décaler les autres. Vide s'il n'y a pas de doublage, ou rien
    d'autre que lui (vidéo à une seule piste)."""
    sortie = {}
    for i, t in enumerate(titres if isinstance(titres, (list, tuple)) else ()):
        c = cle_du_titre(t) if isinstance(t, str) else None
        if c is not None and c not in sortie:
            sortie[c] = i
    return sortie if "doublage" in sortie and len(sortie) > 1 else {}


def pistes_de_la_video(res, autotune=False, titres_flux=None):
    """Les pistes audio de la vidéo doublée (de sa version auto-tune si autotune) : {clé: index (a:N)}, d'après
    rapport.json (pistes_audio.video, pistes_audio.video_autotune) quand il le dit, sinon d'après titres_flux, les
    titres des flux de la vidéo (titres_des_flux). Le rapport l'emporte : s'il ne donne pas de liste pour cette vidéo
    (doublage seul, vidéo auto-tune ratée), il n'y a pas de pistes en plus, quoi que disent les flux."""
    rapport = getattr(res, "rapport", None)
    pa = rapport.get("pistes_audio") if isinstance(rapport, dict) else None
    if isinstance(pa, dict):
        return reperer_pistes(pa.get("video_autotune" if autotune else "video"))
    return reperer_pistes(titres_flux) if titres_flux else {}


_TITRES = {}


def titres_des_flux(video):
    """Titres des pistes audio d'une vidéo, dans l'ordre (tag title, ou handler_name en mp4), par ffprobe ; [] si
    illisible. Gardés par version du fichier (chemin, taille, date)."""
    try:
        cle = _signature(video)
    except OSError:
        return []
    if cle not in _TITRES:
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream_tags",
                                "-of", "json", str(video)], capture_output=True, text=True, timeout=30,
                               stdin=subprocess.DEVNULL)
            flux = (json.loads(r.stdout or "{}").get("streams") or []) if r.returncode == 0 else []
        except (OSError, ValueError, subprocess.SubprocessError):
            flux = []
        titres = []
        for f in flux:
            tags = {str(k).lower(): v for k, v in (f.get("tags") or {}).items()}
            titres.append(str(tags.get("title") or tags.get("handler_name") or ""))
        if len(_TITRES) > 200:
            _TITRES.clear()
        _TITRES[cle] = titres
    return _TITRES[cle]


SEP_PISTE_AUDIO = "#piste-audio="


def source_analyse(fichier, piste_audio=None):
    """Ce qu'analysent les visualisations du son (voixla.analyse), en texte : un fichier (sa première piste audio :
    le doublage, pour une vidéo doublée), ou une autre de ses pistes audio (a:N) : « <fichier>#piste-audio=N »."""
    return f"{fichier}{SEP_PISTE_AUDIO}{int(piste_audio)}" if piste_audio else str(fichier)


def separer_source(source):
    """(fichier, index de la piste audio) d'une source de source_analyse ; 0 pour un fichier seul."""
    s = str(source)
    f, sep, n = s.rpartition(SEP_PISTE_AUDIO)
    if sep and n.isdigit():
        return f, int(n)
    return s, 0


def titres_en_cache(video):
    """Les titres des flux déjà lus par titres_des_flux, sinon None ; ne lance rien (stat seul)."""
    try:
        return _TITRES.get(_signature(video))
    except OSError:
        return None


def a_sonder(res):
    """Vrai si les pistes de la vidéo doublée ne sont connues que par ses flux (rapport sans pistes_audio) et que
    ses titres ne sont pas encore lus : l'interface les fait lire par titres_des_flux, dans un fil."""
    rapport = getattr(res, "rapport", None)
    if isinstance(rapport, dict) and isinstance(rapport.get("pistes_audio"), dict):
        return False
    return any(v is not None and titres_en_cache(v) is None
               for v in (res.video, getattr(res, "video_autotune", None)))


def _titres(video, res, sonder):
    """Titres des flux de video quand le rapport ne dit rien des pistes audio : lus (sonder) ou déjà en cache."""
    rapport = getattr(res, "rapport", None)
    if video is None or (isinstance(rapport, dict) and isinstance(rapport.get("pistes_audio"), dict)):
        return None
    return titres_des_flux(video) if sonder else titres_en_cache(video)


# --- Ma voix seule -----------------------------------------------------------------------------------------------------

@dataclass
class PlanVoix:
    """Comment obtenir « ma voix seule » : combiner seche (+ reverb) aux gains du mix, ou lire direct tel quel."""
    seche: Path | None = None
    reverb: Path | None = None
    gain_seche: float = 0.0
    gain_reverb: float | None = None
    limite: float = 10 ** (LIMITE_DEFAUT_DBTP / 20)   # amplitude (crête vraie)
    instrumental: Path | None = None                    # pour couper à la durée du mix
    direct: Path | None = None
    exact: bool = False         # même voix que dans le mix (gains du rapport, fichiers de ce mixage)
    explication: str = ""

    @property
    def possible(self):
        return self.seche is not None or self.direct is not None

    @property
    def a_combiner(self):
        return self.seche is not None


def plan_ma_voix(dossier_voix, rapport=None, autotune=False):
    """Le plan de « ma voix seule » d'après travail/<voix>/ et rapport.json (lus sans y toucher). autotune : celle de
    la version auto-tune (seche_voix_autotune.wav, reverb_voix_autotune.wav, gains de rapport.autotune), seulement si
    cette version est valide (autotune.applique)."""
    if dossier_voix is None:
        return PlanVoix(explication="doublage inachevé : pas de dossier de voix dans travail/")
    tv = Path(dossier_voix)
    if rapport is None:
        rapport = chemins.lire_json(tv / "rapport.json", {}) or {}
    mix = (rapport or {}).get("mix") if isinstance(rapport, dict) else None
    mix = mix if isinstance(mix, dict) else {}
    gains = mix.get("gains_db") if isinstance(mix.get("gains_db"), dict) else {}
    limite = 10 ** ((mix["limite_dbtp"] if _nombre(mix.get("limite_dbtp")) else LIMITE_DEFAUT_DBTP) / 20)
    if autotune:
        at = (rapport or {}).get("autotune") if isinstance(rapport, dict) else None
        if not (isinstance(at, dict) and at.get("applique")):
            return PlanVoix(explication="pas de version auto-tune valide pour ce doublage")
        seche = _fichier(tv / "seche_voix_autotune.wav")
        if seche is None:
            return PlanVoix(explication="voix auto-tunée absente de travail/ (seche_voix_autotune.wav)")
        g_at = at.get("gains_db") if isinstance(at.get("gains_db"), dict) else gains
        gains = {**gains, **{k: v for k, v in g_at.items() if _nombre(v)}}
        reverb = _fichier(tv / "reverb_voix_autotune.wav")
        instrumental = _fichier(tv.parent / "instrumental.wav")
        if not _nombre(gains.get("seche")):
            return PlanVoix(seche=seche, limite=limite, instrumental=instrumental,
                            explication="gains du mix absents du rapport : voix auto-tunée à son propre niveau")
        plan = PlanVoix(seche=seche, gain_seche=float(gains["seche"]), limite=limite, instrumental=instrumental)
        if _nombre(gains.get("reverb")) and reverb is not None:
            plan.reverb, plan.gain_reverb, plan.exact = reverb, float(gains["reverb"]), True
            plan.explication = (f"voix auto-tunée ({formats.db(plan.gain_seche)}) et sa réverbération "
                                f"({formats.db(plan.gain_reverb)}), aux gains du mix")
        else:
            plan.exact = not _nombre(gains.get("reverb"))
            plan.explication = f"voix auto-tunée au niveau du mix ({formats.db(plan.gain_seche)})"
        return plan
    seche = _fichier(tv / "seche_voix_convertie.wav")
    reverb = _fichier(tv / "reverb_voix_convertie.wav")
    instrumental = _fichier(tv.parent / "instrumental.wav")
    brute = _fichier(tv / "voix_convertie.flac")
    if seche is None:
        if brute is not None:
            return PlanVoix(direct=brute, explication="ancien doublage sans voix sèche dans travail/ : voix convertie "
                                                      "brute, avant le mixage (ni compression ni réverbération)")
        return PlanVoix(explication="voix convertie absente de travail/ (doublage inachevé ?)")
    if not _nombre(gains.get("seche")):
        return PlanVoix(seche=seche, limite=limite, instrumental=instrumental,
                        explication="gains du mix absents du rapport : voix sèche à son propre niveau, sans "
                                    "réverbération")
    plan = PlanVoix(seche=seche, gain_seche=float(gains["seche"]), limite=limite, instrumental=instrumental)
    if _nombre(gains.get("reverb")):
        if reverb is not None:
            plan.reverb, plan.gain_reverb, plan.exact = reverb, float(gains["reverb"]), True
            plan.explication = (f"voix sèche ({formats.db(plan.gain_seche)}) et sa réverbération "
                                f"({formats.db(plan.gain_reverb)}), aux gains du mix")
        else:
            plan.explication = (f"réverbération absente de travail/ : voix sèche seule, au niveau du mix "
                                f"({formats.db(plan.gain_seche)})")
    else:
        plan.exact = True
        plan.explication = f"voix sèche au niveau du mix ({formats.db(plan.gain_seche)}), sans réverbération " \
                           "(le mix n’en a pas)"
    return plan


def _signature(f):
    p = Path(f).resolve()
    st = p.stat()
    return f"{p}|{st.st_size}|{st.st_mtime_ns}"


def chemin_ma_voix(plan):
    """Fichier en cache de ce plan (qu'il existe ou non) ; None si le plan lit un fichier tel quel ou est vide."""
    if not plan.a_combiner:
        return None
    morceaux = [str(VERSION), _signature(plan.seche), f"{plan.gain_seche:.3f}", f"{plan.limite:.5f}"]
    if plan.reverb is not None:
        morceaux += [_signature(plan.reverb), f"{plan.gain_reverb:.3f}"]
    if plan.instrumental is not None:
        morceaux.append(_signature(plan.instrumental))
    cle = hashlib.sha1("|".join(morceaux).encode()).hexdigest()[:24]
    return chemins.dossier_cache() / "voix" / f"{cle}.flac"


def graphe_ma_voix(plan):
    """Le graphe de filtres ffmpeg : celui du mix de doubler, sans la musique."""
    limiteur = f"aresample={4 * SR},alimiter=limit={plan.limite:.4f}:level=disabled:latency=1,aresample={SR}[m]"
    g = f"[0:a]volume={plan.gain_seche:.2f}dB"
    if plan.reverb is not None:
        return g + f"[s];[1:a]volume={plan.gain_reverb:.2f}dB[r];[s][r]amix=inputs=2:normalize=0:duration=first," \
                   + limiteur
    return g + "," + limiteur


def duree_audio(fichier):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(fichier)], capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def commande_ma_voix(plan, sortie, duree=None):
    """ffmpeg (sous nice) qui écrit « ma voix seule » de ce plan dans sortie (FLAC)."""
    if not plan.a_combiner:
        raise ErreurPistes(plan.explication or "rien à combiner")
    cmd = ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(plan.seche)]
    if plan.reverb is not None:
        cmd += ["-i", str(plan.reverb)]
    cmd += ["-filter_complex", graphe_ma_voix(plan), "-map", "[m]"]
    if duree:
        cmd += ["-t", f"{duree:.3f}"]
    return cmd + ["-c:a", "flac", "-f", "flac", str(sortie)]


def _tmp(sortie):
    sortie = Path(sortie)
    return sortie.with_name(f".{sortie.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp{sortie.suffix}")


@contextlib.contextmanager
def verrou_fichier(sortie):
    """Un fichier du cache à la fois : deux préparations du même fichier (l'interface et le préparateur en tâche de
    fond) ne se font pas en double ; la seconde attend, puis trouve le fichier prêt. Verrous dans <cache>/verrous/."""
    d = chemins.dossier_cache() / "verrous"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{Path(sortie).parent.name}-{Path(sortie).name}.verrou", "a") as v:
        fcntl.flock(v, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(v, fcntl.LOCK_UN)


def avancement_cli(etape):
    """Avec VOIXLA_AVANCEMENT=1 (l'interface) : l'avancement d'une préparation, en lignes JSON
    {"avancement": 0.42, "etape": "video"} sur la sortie, tous les 5 % au plus ; sinon None."""
    if not os.environ.get("VOIXLA_AVANCEMENT"):
        return None
    dernier = [-1.0]

    def dire(f):
        if f - dernier[0] >= 0.05 - 1e-9 or f >= 1.0:
            dernier[0] = f
            print(json.dumps({"avancement": round(f, 3), "etape": etape}), flush=True)
    return dire


def lire_progression(lignes, duree, avancement):
    """Suit la sortie de ffmpeg -progress : out_time_us (ou out_time_ms, en µs elle aussi) rapporté à duree."""
    for ligne in lignes:
        cle, _, valeur = ligne.strip().partition("=")
        if cle in ("out_time_us", "out_time_ms") and duree:
            try:
                avancement(max(0.0, min(1.0, int(valeur) / 1e6 / duree)))
            except ValueError:
                pass
        elif cle == "progress" and valeur.strip() == "end":
            avancement(1.0)


def _lancer(cmd, tmp, sortie, duree=None, avancement=None):
    try:
        if avancement is not None and duree:
            # -progress : l'avancement ligne à ligne (le fichier de sortie reste le dernier argument)
            cmd = [*cmd[:-1], "-progress", "pipe:1", "-nostats", cmd[-1]]
            with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  stdin=subprocess.DEVNULL) as p:
                lire_progression(p.stdout, duree, avancement)
                erreur = p.stderr.read()
                code = p.wait()
            if code:
                raise ErreurPistes(erreur.strip()[-600:] or f"ffmpeg a échoué ({code})")
        else:
            r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            if r.returncode:
                raise ErreurPistes((r.stderr or r.stdout).strip()[-600:] or f"ffmpeg a échoué ({r.returncode})")
        os.replace(tmp, sortie)
    finally:
        if Path(tmp).exists():
            Path(tmp).unlink()


def preparer_ma_voix(dossier_voix, rapport=None, avancement=None, autotune=False):
    """Le fichier de « ma voix seule » (de sa version auto-tune si autotune) : en cache (calculé au besoin), ou la
    voix brute d'un ancien doublage. avancement(fraction) pendant le calcul."""
    plan = plan_ma_voix(dossier_voix, rapport, autotune)
    if plan.direct is not None:
        return plan.direct
    if not plan.a_combiner:
        raise ErreurPistes(plan.explication)
    sortie = chemin_ma_voix(plan)
    if sortie.is_file():
        os.utime(sortie)    # récemment écoutée : la purge la garde
        return sortie
    with verrou_fichier(sortie):
        if sortie.is_file():
            return sortie     # faite pendant qu'on attendait le verrou
        sortie.parent.mkdir(parents=True, exist_ok=True)
        tmp = _tmp(sortie)
        duree = duree_audio(plan.instrumental) if plan.instrumental else None
        _lancer(commande_ma_voix(plan, tmp, duree), tmp, sortie, duree or duree_audio(plan.seche), avancement)
        chemins.purger_dossier(sortie.parent, CACHE_VOIX_MO, garder=(sortie,))
    return sortie


def ma_voix_prete(dossier_voix, rapport=None, autotune=False):
    """Le fichier de « ma voix seule » s'il est déjà prêt (cache ou voix brute), sinon None ; ne calcule rien."""
    plan = plan_ma_voix(dossier_voix, rapport, autotune)
    if plan.direct is not None:
        return plan.direct
    f = chemin_ma_voix(plan) if plan.a_combiner else None
    return f if f is not None and f.is_file() else None


# --- Choix des pistes ------------------------------------------------------------------------------------------------

@dataclass
class Piste:
    cle: str
    fichier: Path | None = None     # ce qu'on lit ; None : indisponible, ou à préparer (a_preparer)
    decalage: float = 0.0           # début de l'extrait dans la vidéo d'origine, sinon 0 (voir decalage_lu)
    image: bool = False             # le fichier a une image
    explication: str = ""           # ce que la piste contient, ou pourquoi elle manque
    a_preparer: bool = False        # ma_voix : à combiner (en cache) avant de l'écouter
    video: Path | None = None       # en vidéo : la copie en cache (image de la vidéo doublée, son de cette piste), ou
                                    # la vidéo doublée elle-même quand elle a cette piste (piste_audio)
    video_a_preparer: bool = False  # en vidéo : copie à faire pour lire cette piste sur l'image qui avance
    sans_video: str = ""            # pourquoi cette piste se lit sans image (pas de vidéo doublée, copie impossible)

    @property
    def disponible(self):
        return self.fichier is not None or self.a_preparer or self.video is not None

    @property
    def a_lire(self):
        """Ce que lit le lecteur : la copie vidéo (ou la vidéo doublée qui a cette piste) si elle est prête, sinon le
        son seul."""
        return self.video or self.fichier

    @property
    def avec_image(self):
        return self.image or self.video is not None

    nom: str = ""                   # libellé propre à ce résultat (« Voix converties seules »), sinon LIBELLES
    debut_son: float = 0.0          # où le son commence dans l'image lue (avance_piste) : debut_son_video pour la
                                    # vidéo doublée, decalage_son de source.json pour la vidéo d'origine
    piste_audio: int | None = None  # en vidéo : l'index (a:N) de cette piste parmi les sons de a_lire, une vidéo
                                    # doublée à plusieurs pistes audio ; lue sur place (current-audio de playbin)

    @property
    def decalage_lu(self):
        """Position dans ce que lit le lecteur (a_lire) = temps du son doublé + ce décalage : celui de l'extrait, plus
        avance_piste (debut_son) si a_lire a une image."""
        return self.decalage + avance_piste(self.avec_image, self.debut_son)

    @property
    def libelle(self):
        return self.nom or LIBELLES.get(self.cle) or LIBELLES_AUTOTUNE.get(self.cle, self.cle)

    @property
    def dans_la_video(self):
        """Vrai si la piste se lit dans la vidéo doublée elle-même, par une de ses pistes audio (sans copie)."""
        return self.piste_audio is not None and self.video is not None

    @property
    def copie(self):
        """La copie vidéo en cache que lit cette piste (à protéger de la purge, à fermer quand on la quitte), sinon
        None ; jamais la vidéo doublée elle-même."""
        return None if self.piste_audio is not None else self.video


def fichier_voix_source(travail):
    for nom in ("voix_source.wav", "voix_source.flac"):
        f = Path(travail) / nom
        if f.is_file():
            return f
    return None


def _dans_la_video(piste, video, index, titre):
    """La piste se lit dans video (la vidéo doublée, ou sa version auto-tune), par sa piste audio n° index (a:N)."""
    contenu = piste.explication if piste.fichier is not None or piste.a_preparer else ""
    piste.video, piste.piste_audio = Path(video), int(index)
    piste.video_a_preparer, piste.sans_video = False, ""
    piste.explication = (f"{contenu} ; " if contenu else "") + f"piste « {titre} » de la vidéo doublée, lue sur place"


def choisir_pistes(res, forme="video", source_choisie=None, sonder=False, dans_la_video=True):
    """{clé: Piste} dans l'ordre de CLES (puis les versions auto-tune), pour un résultat (voixla.rapports.Resultat) lu
    en « video » ou en « mp3 ». source_choisie : la vidéo d'origine indiquée à la main quand celle de source.json est
    introuvable.

    En vidéo, si la vidéo doublée a ses pistes audio (pistes_de_la_video), le beat, les voix seules et, sans la vidéo
    d'origine, l'original s'y lisent sur place (Piste.piste_audio), sans copie ; dans_la_video faux : on s'en passe,
    comme pour un ancien doublage à une piste. Quand rapport.json ne dit rien des pistes audio, leurs titres sont lus
    dans les flux : par ffprobe si sonder (hors de l'interface), sinon seulement s'ils sont déjà connus (a_sonder)."""
    p = {}
    original_wav = res.original_wav
    dans, src = {}, None
    if forme == "video" and res.video is not None and dans_la_video:
        dans = pistes_de_la_video(res, False, _titres(res.video, res, sonder))
    if forme == "video":
        p["doublage"] = Piste("doublage", res.video, image=True, explication="la vidéo doublée") if res.video else \
            Piste("doublage", explication="pas de vidéo doublée dans ce dossier")
        if dans:
            p["doublage"].piste_audio = dans["doublage"]
        src = res.video_source if res.source_presente else _fichier(source_choisie)
        v = res.video_source
        introuvable = "vidéo d’origine introuvable" + (f" ({v})" if v else "")
        if src is not None:
            p["original"] = Piste("original", src, res.decalage_source_s, image=True,
                                  explication="la vidéo d’origine, à la même position")
        elif "original" in dans:
            # la piste Original de la vidéo doublée : sur son image, calée sur le doublage à l'échantillon près
            p["original"] = Piste("original", original_wav)
            _dans_la_video(p["original"], res.video, dans["original"], "Original")
            p["original"].explication = (f"{introuvable} : piste « Original » de la vidéo doublée, sur son image ; "
                                         "« Choisir… » dans Doublage pour retrouver la vidéo d’origine")
        elif original_wav is not None:
            p["original"] = Piste("original", original_wav,
                                  explication=introuvable + " : son d’origine seul (travail/original.wav), sans "
                                                            "image ; « Choisir… » dans Doublage pour retrouver l’image")
        else:
            p["original"] = Piste("original", explication="ni vidéo d’origine ni travail/original.wav")
    else:
        p["doublage"] = Piste("doublage", res.mp3, explication="le MP3 du doublage") if res.mp3 else \
            Piste("doublage", explication="pas de MP3 dans ce dossier")
        p["original"] = Piste("original", original_wav, explication="le son d’origine extrait (travail/original.wav)") \
            if original_wav is not None else Piste("original", explication="travail/original.wav manque")
    p.update(_pistes_seules(res, autotune=False))
    if getattr(res, "plusieurs_voix", False):
        p["ma_voix"].nom = LIBELLE_VOIX_CONVERTIES
    plan = plan_ma_voix(res.dossier_voix, res.rapport) if res.dossier_voix else plan_ma_voix(None)
    for c in SANS_IMAGE:
        if c in dans:
            _dans_la_video(p[c], res.video, dans[c], p[c].libelle)
        else:
            _choisir_video(res, forme, p[c], plan)
    sortie = {c: p[c] for c in CLES}
    sortie.update(pistes_autotune(res, forme, sonder, dans_la_video, original_dans=p["original"].dans_la_video))
    debut_son = debut_son_video(res)
    for q in sortie.values():
        q.debut_son = debut_son
    if src is not None:      # la vidéo d'origine : son propre départ du son (son retard ; 0 pour un extrait)
        sortie["original"].debut_son = decalage_son(getattr(res, "source", None)) or 0.0
    return sortie


def _pistes_seules(res, autotune=False):
    """Beat seul, ma voix seule (celle de la version auto-tune si autotune, sous la clé ma_voix_at) et voix d'origine
    seule, en son seul : fichiers de travail/, ma voix seule combinée en cache."""
    p = {}
    beat = _fichier(res.travail / "instrumental.wav")
    p["beat"] = Piste("beat", beat, explication="la musique sans aucune voix (travail/instrumental.wav)") if beat else \
        Piste("beat", explication="travail/instrumental.wav manque : doublage inachevé ou fait avant ce fichier")
    cle = "ma_voix_at" if autotune else "ma_voix"
    plan = plan_ma_voix(res.dossier_voix, res.rapport, autotune=autotune) if res.dossier_voix else plan_ma_voix(None)
    if plan.direct is not None:
        p[cle] = Piste(cle, plan.direct, explication=plan.explication)
    elif plan.a_combiner:
        f = chemin_ma_voix(plan)
        prete = f is not None and f.is_file()
        p[cle] = Piste(cle, f if prete else None, explication=plan.explication, a_preparer=not prete)
    else:
        p[cle] = Piste(cle, explication=plan.explication)
    vs = fichier_voix_source(res.travail)
    p["voix_origine"] = Piste("voix_origine", vs, explication=f"la voix d’origine isolée (travail/{vs.name})") if vs \
        else Piste("voix_origine", explication="travail/voix_source.wav manque")
    return p


def source_lecteur(piste):
    """La source du lecteur (gui/lecteur.py) pour cette piste : (ce qu'on lit, décalage, libellé). Le lecteur compte
    en temps du son doublé et lit la source à position + décalage (Piste.decalage_lu)."""
    return (piste.a_lire, piste.decalage_lu, piste.libelle)


def sources_lecteur(dispo):
    """{clé: source du lecteur} des pistes (choisir_pistes) qu'on peut lire tout de suite (voir source_lecteur)."""
    return {c: source_lecteur(p) for c, p in dispo.items() if p.a_lire}


def pistes_audio_lecteur(dispo):
    """{clé: index de sa piste audio} des pistes lues dans une vidéo à plusieurs pistes, le doublage compris : le
    lecteur lit celles d'une même vidéo dans un seul flux, et change de piste audio (current-audio), pas de fichier."""
    return {c: p.piste_audio for c, p in dispo.items() if p.piste_audio is not None and p.a_lire is not None}


def pistes_autotune(res, forme="video", sonder=False, dans_la_video=True, original_dans=False):
    """{doublage_at, ma_voix_at} si le doublage a une version auto-tune valide (rapport.autotune.applique), sinon {}.
    En vidéo, si la vidéo auto-tune a ses pistes audio (pistes_audio.video_autotune), ma voix seule auto-tunée s'y lit
    sur place, et aussi le beat, la voix d'origine et (original_dans : sans la vidéo d'origine) l'original, sous les
    clés beat_at, voix_origine_at et original_at : même son que dans la vidéo normale, même flux que le doublage
    auto-tune."""
    if not getattr(res, "autotune", None):
        return {}
    p = {}
    media = res.video_autotune if forme == "video" else res.mp3_autotune
    p["doublage_at"] = Piste("doublage_at", media, image=forme == "video",
                             explication="la version auto-tune du doublage") if media else \
        Piste("doublage_at", explication="fichier auto-tune absent du dossier")
    dans = {}
    if forme == "video" and media is not None and dans_la_video:
        dans = pistes_de_la_video(res, True, _titres(media, res, sonder))
    if dans:
        p["doublage_at"].piste_audio = dans["doublage"]
    seules = _pistes_seules(res, autotune=True)
    p["ma_voix_at"] = seules["ma_voix_at"]
    if getattr(res, "plusieurs_voix", False):
        p["ma_voix_at"].nom = LIBELLE_VOIX_CONVERTIES + " (auto-tune)"
    if "ma_voix" in dans:
        _dans_la_video(p["ma_voix_at"], media, dans["ma_voix"], p["ma_voix_at"].libelle)
    else:
        _choisir_video(res, forme, p["ma_voix_at"], plan_ma_voix(res.dossier_voix, res.rapport, autotune=True)
                       if res.dossier_voix else plan_ma_voix(None))
    for base, cle in CLES_AUTOTUNE_VIDEO.items():
        if base not in dans or (base == "original" and not original_dans):
            continue
        q = Piste(cle, res.original_wav) if base == "original" else seules[base]
        q.cle, q.nom = cle, LIBELLES[base]
        _dans_la_video(q, media, dans[base], "Original" if base == "original" else LIBELLES[base])
        q.explication += " (version auto-tune)"
        p[cle] = q
    return p


def cle_ecoutee(cle, autotune, dispo=None):
    """La piste réellement lue : sa version auto-tune si la bascule est allumée et qu'elle existe (doublage, ma voix
    seule), sinon la piste elle-même. dispo (les pistes du résultat) : en vidéo, l'original, le beat et la voix
    d'origine se lisent aussi dans la vidéo auto-tune quand elle les a (CLES_AUTOTUNE_VIDEO) : même son, même flux que
    le doublage auto-tune."""
    if not autotune:
        return cle
    if cle in CLES_AUTOTUNE:
        return CLES_AUTOTUNE[cle]
    at = CLES_AUTOTUNE_VIDEO.get(cle)
    return at if dispo is not None and at in dispo else cle


def cle_de_base(cle):
    """La piste d'une version auto-tune (« doublage_at » → « doublage », « beat_at » → « beat »)."""
    return next((b for b, a in (*CLES_AUTOTUNE.items(), *CLES_AUTOTUNE_VIDEO.items()) if a == cle), cle)


def _choisir_video(res, forme, piste, plan):
    """La copie vidéo d'une piste sans image : prête en cache, à préparer, ou la raison de s'en passer. Ne lit que la
    taille et la date des fichiers (appelé dans le fil de l'interface)."""
    if not piste.disponible:
        return
    if res.video is None:
        piste.sans_video = "pas de vidéo doublée (MP3 seul)"
        return
    if forme != "video":
        return
    try:
        trop = trop_lourde(res.video)
        if trop:
            piste.sans_video = trop
            return
        f = chemin_video(res.video, identite_plan(plan) if piste.cle in ("ma_voix", "ma_voix_at")
                         else _signature(piste.fichier))
    except (OSError, ErreurPistes) as e:
        piste.sans_video = str(e) or "copie vidéo impossible"
        return
    if f.is_file():
        piste.video = f
    else:
        piste.video_a_preparer = True


def piste_apres_changement(pistes, voulue):
    """La piste à garder quand les pistes changent (autre forme, remix) : la même si elle est encore là, sinon le
    doublage, sinon la première disponible ; None s'il n'y a rien à lire."""
    if voulue in pistes and pistes[voulue].disponible:
        return voulue
    if pistes.get("doublage") and pistes["doublage"].fichier is not None:
        return "doublage"
    return next((c for c, x in pistes.items() if x.fichier is not None), None)


# --- Pistes en vidéo : l'image de la vidéo doublée, le son de la piste ----------------------------------------------

@dataclass
class CalageVideo:
    index: int                  # flux vidéo copié
    debut_son: float            # instant (temps du fichier) où commence le son du doublage, donc celui de la piste
    fin: float                  # fin de la vidéo doublée : le son de la piste y est coupé
    taux: int | None = None     # fréquence du son du doublage (gardée en AAC)


def _flottant(v, defaut=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return defaut
    return x if math.isfinite(x) else defaut


def sonder(video):
    """ffprobe de la vidéo doublée (format et flux, en JSON)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise ErreurPistes(f"ffprobe impossible : {e}") from e
    if r.returncode:
        raise ErreurPistes(f"vidéo doublée illisible : {(r.stderr or '').strip()[-300:] or r.returncode}")
    try:
        return json.loads(r.stdout or "{}")
    except ValueError as e:
        raise ErreurPistes("vidéo doublée illisible : réponse de ffprobe incomprise") from e


def flux_son(info):
    """Le flux de son de la vidéo doublée (celui par défaut), ou None."""
    sons = [f for f in info.get("streams") or [] if f.get("codec_type") == "audio"]
    return next((f for f in sons if (f.get("disposition") or {}).get("default")), sons[0] if sons else None)


def premier_paquet(video, index):
    """Temps (s, temps du fichier) du premier paquet du flux index : celui qui porte l'amorce de l'encodeur."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", str(index), "-show_entries", "packet=pts_time",
                            "-read_intervals", "%+#1", "-of", "csv=p=0", str(video)],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise ErreurPistes(f"ffprobe impossible : {e}") from e
    for x in r.stdout.replace(",", " ").split():
        t = _flottant(x)
        if t is not None:
            return t
    raise ErreurPistes("premier paquet de son illisible dans la vidéo doublée")


def calage_video(info, premier_son=None):
    """Où va le son d'une piste dans sa copie, d'après la vidéo doublée : là où y commence le son du doublage.
    doubler a remis le mix sur la vidéo avec un décalage (decalage_son de source.json ; pour un extrait, plus l'écart
    entre l'image clé où part la copie de l'image et le début du son ; puis le conteneur peut tout décaler pour éviter
    des temps négatifs) : on le lit dans le résultat au lieu de le recalculer.

    premier_son : le temps du premier paquet du son doublé (premier_paquet). Ce paquet porte l'amorce de l'encodeur
    (1024 échantillons d'AAC, 312 d'Opus à 48 kHz), placée avant le mix : on vise ce même temps pour le premier paquet
    de la copie, encodée de la même façon. Le son commence donc à premier_son + amorce. L'amorce est celle que le
    conteneur annonce (initial_padding, en mkv et webm), sinon celle des encodeurs de doubler (en mp4, une liste
    d'édition la cache). Le temps de début du flux (start_time) ne convient pas : il compte l'amorce en mp4, pas
    toujours en mkv. Avec -copyts, la copie garde les temps de la vidéo doublée : même image, même début du son, même
    fin."""
    images = [f for f in info.get("streams") or [] if f.get("codec_type") == "video"
              and not (f.get("disposition") or {}).get("attached_pic")]
    if not images:
        raise ErreurPistes("pas d’image dans la vidéo doublée")
    son = flux_son(info)
    fmt = info.get("format") or {}
    debut_fichier = _flottant(fmt.get("start_time"), 0.0)
    duree = _flottant(fmt.get("duration"))
    if duree is None or duree <= 0:
        raise ErreurPistes("durée de la vidéo doublée inconnue")
    taux = None
    if son is None:     # pas de son à suivre : la piste part avec l'image
        debut_son = _flottant(images[0].get("start_time"), debut_fichier)
    else:
        if premier_son is None:
            raise ErreurPistes("premier paquet de son illisible dans la vidéo doublée")
        taux = int(_flottant(son.get("sample_rate"), 0) or 0) or None
        amorce = int(_flottant(son.get("initial_padding"), 0) or 0) or AMORCES.get(son.get("codec_name"), 0)
        debut_son = premier_son + (amorce / taux if taux else 0.0)
    return CalageVideo(int(images[0]["index"]), debut_son, debut_fichier + duree, taux)


def ext_video(video):
    ext = Path(video).suffix.lower()
    return ext if ext in CONTENEURS_VIDEO else ".mkv"


def commande_video(video, son, sortie, calage):
    """ffmpeg (sous nice) qui écrit la copie : l'image de la vidéo doublée copiée telle quelle (mêmes paquets, mêmes
    temps), le son de la piste placé au début du son du doublage et coupé à la fin de la vidéo, encodé comme doubler
    (AAC 256k, Opus 192k en webm ; conteneur deviné par ffmpeg d'après l'extension, comme doubler)."""
    ext = Path(sortie).suffix.lower()
    cmd = ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-copyts", "-i", str(video)]
    if abs(calage.debut_son) > 1e-6:
        cmd += ["-itsoffset", f"{calage.debut_son:.6f}"]
    cmd += ["-t", f"{max(0.001, calage.fin - calage.debut_son):.6f}", "-i", str(son),
            "-map", f"0:{calage.index}", "-map", "1:a:0", "-c:v", "copy"]
    if ext in SON_VIDEO:
        cmd += SON_VIDEO[ext]
    else:
        cmd += SON_VIDEO_DEFAUT + (["-ar", str(calage.taux)] if calage.taux else [])
    return cmd + [str(sortie)]


def limite_cache_video_mo(prefs=None):
    """Taille du cache des copies vidéo : la préférence cache_pistes_mo (Réglages), sinon CACHE_VIDEO_MO (2 Go)."""
    prefs = chemins.lire_preferences() if prefs is None else prefs
    v = _flottant(prefs.get("cache_pistes_mo"))
    return v if v and v > 0 else CACHE_VIDEO_MO


def trop_lourde(video, limite_mo=None):
    """La raison de ne pas copier une vidéo doublée plus lourde que tout le cache des copies, sinon "" (stat seul)."""
    limite = limite_cache_video_mo() if limite_mo is None else limite_mo
    taille = Path(video).stat().st_size
    if taille > limite * 1024 * 1024:
        return (f"vidéo doublée trop lourde pour le cache des copies ({taille / 1024 ** 3:.1f} Go, "
                f"{formats.nombre(limite / 1024, 1)} Go au plus ; réglable dans Réglages)")
    return ""


def identite_plan(plan):
    """Ce qui identifie « ma voix seule » : le nom de son fichier en cache (qui dépend déjà des fichiers et des
    gains du mix), ou la signature de la voix brute d'un ancien doublage."""
    if plan.direct is not None:
        return _signature(plan.direct)
    if plan.a_combiner:
        return "voix:" + chemin_ma_voix(plan).name
    raise ErreurPistes(plan.explication or "pas de voix à lire")


def identite_son(son=None, voix=None, rapport=None, autotune=False):
    """Ce qui identifie le son d'une copie : la signature du fichier, ou « ma voix seule » d'un dossier de voix."""
    return identite_plan(plan_ma_voix(voix, rapport, autotune)) if voix is not None else _signature(son)


def chemin_video(video, identite):
    """Copie en cache (qu'elle existe ou non) pour cette vidéo doublée et ce son : nommée d'après leur chemin, leur
    taille et leur date ; un remix (vidéo refaite) ou un son refait donnent un autre nom."""
    cle = hashlib.sha1(f"{VERSION_VIDEO}|{_signature(video)}|{identite}".encode()).hexdigest()[:24]
    return chemins.dossier_cache() / "videos" / f"{cle}{ext_video(video)}"


def video_prete(video, son=None, voix=None, rapport=None, autotune=False):
    """La copie si elle est déjà en cache, sinon None ; ne calcule rien."""
    try:
        f = chemin_video(video, identite_son(son, voix, rapport, autotune))
    except (OSError, ErreurPistes):
        return None
    return f if f.is_file() else None


def toucher(f):
    """Copie lue : la purge la garde plus longtemps."""
    try:
        os.utime(f)
    except OSError:
        pass


def preparer_video(video, son=None, voix=None, avancement=None, autotune=False):
    """La copie vidéo d'une piste (en cache, calculée au besoin). son : le fichier de la piste ; voix : le dossier
    d'une voix, pour « ma voix seule » (combinée d'abord, au besoin). Rien n'est écrit à côté de la vidéo doublée.
    avancement(fraction) pendant la copie. autotune : avec la voix seule de la version auto-tune."""
    video = Path(video)
    if not video.is_file():
        raise ErreurPistes(f"vidéo doublée introuvable : {video}")
    trop = trop_lourde(video)
    if trop:
        raise ErreurPistes(trop)
    if voix is None and (son is None or not Path(son).is_file()):
        raise ErreurPistes(f"son introuvable : {son}")
    sortie = chemin_video(video, identite_son(son, voix, autotune=autotune))
    if sortie.is_file():
        toucher(sortie)
        return sortie
    info = sonder(video)
    son_doublage = flux_son(info)
    calage = calage_video(info, premier_paquet(video, son_doublage["index"]) if son_doublage else None)
    if voix is not None:
        son = preparer_ma_voix(voix, autotune=autotune)
    with verrou_fichier(sortie):
        if sortie.is_file():
            return sortie     # faite pendant qu'on attendait le verrou
        sortie.parent.mkdir(parents=True, exist_ok=True)
        tmp = _tmp(sortie)
        _lancer(commande_video(video, son, tmp, calage), tmp, sortie, calage.fin - calage.debut_son, avancement)
        chemins.purger_dossier(sortie.parent, limite_cache_video_mo(), garder=(sortie,))
    return sortie


# --- Miniature : une image de la vidéo doublée (MPRIS, barre « en cours de lecture ») -------------------------------

VERSION_MINIATURE = 1
CACHE_MINIATURES_MO = 20
COTE_MINIATURE = 512


def chemin_miniature(video):
    """Miniature en cache (qu'elle existe ou non), nommée d'après le chemin, la taille et la date de la vidéo."""
    cle = hashlib.sha1(f"{VERSION_MINIATURE}|{_signature(video)}".encode()).hexdigest()[:24]
    return chemins.dossier_cache() / "miniatures" / f"{cle}.jpg"


def miniature_prete(video):
    """La miniature si elle est déjà en cache, sinon None ; ne calcule rien."""
    try:
        f = chemin_miniature(video)
    except OSError:
        return None
    return f if f.is_file() else None


def instant_miniature(duree):
    """L'image prise au tiers de la vidéo (passé l'introduction souvent noire), à 1 s pour une vidéo très courte."""
    d = _flottant(duree)
    return max(0.0, d / 3) if d and d > 3 else min(1.0, max(0.0, (d or 0.0) / 2))


def commande_miniature(video, sortie, instant):
    """ffmpeg (sous nice) : une image carrée de COTE_MINIATURE px, prise à instant (recadrée au centre), en JPEG."""
    c = COTE_MINIATURE
    return ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{max(0.0, instant):.3f}", "-i", str(video), "-map", "0:v:0", "-frames:v", "1",
            "-vf", f"scale={c}:{c}:force_original_aspect_ratio=increase,crop={c}:{c}", "-q:v", "4",
            "-f", "mjpeg", str(sortie)]


def preparer_miniature(video):
    """La miniature d'une vidéo doublée (en cache, calculée au besoin). Rien n'est écrit à côté de la vidéo."""
    video = Path(video)
    if not video.is_file():
        raise ErreurPistes(f"vidéo introuvable : {video}")
    sortie = chemin_miniature(video)
    if sortie.is_file():
        toucher(sortie)
        return sortie
    info = sonder(video)
    if not any(f.get("codec_type") == "video" and not (f.get("disposition") or {}).get("attached_pic")
               for f in info.get("streams") or []):
        raise ErreurPistes("pas d’image dans cette vidéo")
    sortie.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(sortie)
    _lancer(commande_miniature(video, tmp, instant_miniature((info.get("format") or {}).get("duration"))), tmp,
            sortie)
    chemins.purger_dossier(sortie.parent, CACHE_MINIATURES_MO, garder=(sortie,))
    return sortie


# --- Noms des fichiers enregistrés -----------------------------------------------------------------------------------

def base_sorties(res):
    """« <titre> - voix <Nom> », le nom des sorties de ce doublage sans extension."""
    for f in (res.mp3, res.video):
        if f is not None:
            return f.stem
    titre = res.video_source.stem if res.video_source else res.titre_court
    return f"{titre} - voix {res.voix_nom or res.voix or '?'}"


def nom_export(res, cle, extension=".mp3"):
    """Nom d'un fichier enregistré à côté des sorties : « <titre> - voix <Nom> (beat).mp3 », « … (voix seule).mp3 » ;
    voix seule de la version auto-tune : « <titre> - voix <Nom> (auto-tune) (voix seule).mp3 »."""
    if cle == "ma_voix_at":
        return f"{base_sorties(res)} (auto-tune){SUFFIXES_EXPORT['ma_voix']}{extension}"
    if cle not in SUFFIXES_EXPORT:
        raise ValueError(cle)
    return f"{base_sorties(res)}{SUFFIXES_EXPORT[cle]}{extension}"


def exports_existants(res):
    """Les fichiers déjà enregistrés pour ce doublage (pour la corbeille), version auto-tune comprise."""
    return [res.dossier / nom_export(res, cle, ext) for cle in (*SUFFIXES_EXPORT, "ma_voix_at") for ext in FORMATS_EXPORT
            if (res.dossier / nom_export(res, cle, ext)).is_file()]


def commande_export(source, sortie, tmp=None, piste_audio=None):
    """ffmpeg (sous nice) qui écrit source dans sortie, au format de son extension (.mp3 320k, .flac ou .wav).
    piste_audio : la piste audio de source à prendre (a:N, une vidéo doublée à plusieurs pistes), sinon la première
    (le doublage, dans une vidéo doublée)."""
    sortie = Path(sortie)
    ext = sortie.suffix.lower()
    if ext not in FORMATS_EXPORT:
        raise ErreurPistes(f"format non pris en charge : « {sortie.suffix or 'sans extension'} » (mp3, flac ou wav)")
    return ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
            "-map", f"0:a:{int(piste_audio or 0)}", "-map_metadata", "-1", "-metadata", f"title={sortie.stem}",
            *FORMATS_EXPORT[ext], str(tmp or sortie)]


def exporter(source, sortie, piste_audio=None):
    """Écrit source (sa piste audio piste_audio, sinon la première) dans sortie (écriture atomique : fichier
    temporaire à côté, puis renommage)."""
    sortie = Path(sortie)
    if sortie.suffix.lower() not in FORMATS_EXPORT:
        raise ErreurPistes(f"format non pris en charge : « {sortie.suffix or 'sans extension'} » (mp3, flac ou wav)")
    if not Path(source).is_file():
        raise ErreurPistes(f"fichier introuvable : {source}")
    tmp = _tmp(sortie)
    _lancer(commande_export(source, sortie, tmp, piste_audio), tmp, sortie)
    return sortie


def source_export(piste):
    """Ce qu'« Enregistrer… » écrit pour une piste : (fichier, piste audio) ou None s'il faut d'abord combiner ma voix
    seule. Le fichier de travail/ ou du cache quand il existe sans calcul (sans perte : beat, ma voix seule déjà
    combinée) ; sinon, la piste de la vidéo doublée qui l'a (-map 0:a:N), au lieu de recalculer ma voix seule."""
    if piste.fichier is not None:
        return piste.fichier, None
    if piste.dans_la_video:
        return piste.video, piste.piste_audio
    return None


# --- Ligne de commande -----------------------------------------------------------------------------------------------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    debut = time.time()
    # --autotune (n'importe où) : « ma voix seule » de la version auto-tune ; --piste-audio N : la piste a:N à exporter
    autotune = "--autotune" in argv
    argv = [a for a in argv if a != "--autotune"]
    piste_audio = None
    if "--piste-audio" in argv:
        i = argv.index("--piste-audio")
        try:
            piste_audio = int(argv[i + 1])
        except (IndexError, ValueError):
            print(__doc__)
            return 2
        del argv[i:i + 2]
    try:
        if len(argv) == 2 and argv[0] == "voix":
            f = preparer_ma_voix(argv[1], avancement=avancement_cli("voix"), autotune=autotune)
            print(json.dumps({"voix": str(f), "duree_s": round(time.time() - debut, 2)}, ensure_ascii=False))
            return 0
        if argv[:1] == ["video"] and len(argv) in (3, 4):
            if (len(argv) == 4) != (argv[2] == "--voix"):
                print(__doc__)
                return 2
            av = avancement_cli("video")
            f = preparer_video(argv[1], voix=argv[3], avancement=av, autotune=autotune) if len(argv) == 4 else \
                preparer_video(argv[1], son=argv[2], avancement=av)
            print(json.dumps({"video": str(f), "duree_s": round(time.time() - debut, 2)}, ensure_ascii=False))
            return 0
        if argv[:1] == ["exporter"] and len(argv) in (3, 4):
            if argv[1] == "--voix":
                if len(argv) != 4:
                    print(__doc__)
                    return 2
                source, sortie = preparer_ma_voix(argv[2], autotune=autotune), argv[3]
            elif len(argv) == 3:
                source, sortie = argv[1], argv[2]
            else:
                print(__doc__)
                return 2
            exporter(source, sortie, piste_audio)
            print(json.dumps({"sortie": str(sortie), "duree_s": round(time.time() - debut, 2)}, ensure_ascii=False))
            return 0
        if len(argv) == 2 and argv[0] == "miniature":
            f = preparer_miniature(argv[1])
            print(json.dumps({"miniature": str(f), "duree_s": round(time.time() - debut, 2)}, ensure_ascii=False))
            return 0
    except (ErreurPistes, OSError) as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
