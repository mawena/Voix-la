"""Pistes séparées d'un doublage (sans GTK, testable) : ce qu'on peut écouter dans la page Résultats, la voix
convertie seule telle qu'elle sonne dans le mix, et les fichiers à enregistrer.

Pistes, dans sorties/<titre>/ :
- doublage : la vidéo doublée « <titre> - voix <Nom>.mp4 » (ou .mkv…), ou son MP3 ;
- original : la vidéo d'origine (travail/source.json, ou celle choisie à la main), sinon travail/original.wav ;
- beat : travail/instrumental.wav, la musique sans aucune voix (original − voix isolée, refait à chaque mixage) ;
- ma_voix : la voix convertie seule, au niveau du mix (voir plan_ma_voix) ;
- voix_origine : travail/voix_source.wav, la voix d'origine isolée.
Toutes sont calées sur le doublage : le temps 0 est le début du son doublé (sauf la vidéo d'origine d'un extrait,
lue avec le décalage du début de l'extrait).

« Ma voix seule » refait la part de la voix dans le mix de doubler (fonction mixer de doubler.py), sans la musique :
  voix sèche      travail/<voix>/seche_voix_convertie.wav   au gain gains_db.seche  (rapport.json, clé « mix ») ;
  réverbération   travail/<voix>/reverb_voix_convertie.wav  au gain gains_db.reverb (en chant seulement) ;
additionnées (amix sans normalisation), puis passées au même limiteur (crête vraie, limite_dbtp du rapport), et
coupées à la durée de travail/instrumental.wav comme le mix. Le résultat va en cache, en FLAC, dans
~/.cache/voix-la/voix/, nommé d'après les fichiers et les gains (refait si un remix les change).

En ligne de commande (lancée par l'interface sous nice, ou par file-attente quand c'est long) :
  python3 -m voixla.pistes voix DOSSIER_VOIX
  python3 -m voixla.pistes exporter (SOURCE | --voix DOSSIER_VOIX) SORTIE.mp3|.flac|.wav
"""

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


def plan_ma_voix(dossier_voix, rapport=None):
    """Le plan de « ma voix seule » d'après travail/<voix>/ et rapport.json (lus sans y toucher)."""
    if dossier_voix is None:
        return PlanVoix(explication="doublage inachevé : pas de dossier de voix dans travail/")
    tv = Path(dossier_voix)
    if rapport is None:
        rapport = chemins.lire_json(tv / "rapport.json", {}) or {}
    mix = (rapport or {}).get("mix") if isinstance(rapport, dict) else None
    mix = mix if isinstance(mix, dict) else {}
    gains = mix.get("gains_db") if isinstance(mix.get("gains_db"), dict) else {}
    limite = 10 ** ((mix["limite_dbtp"] if _nombre(mix.get("limite_dbtp")) else LIMITE_DEFAUT_DBTP) / 20)
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


def _lancer(cmd, tmp, sortie):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if r.returncode:
            raise ErreurPistes((r.stderr or r.stdout).strip()[-600:] or f"ffmpeg a échoué ({r.returncode})")
        os.replace(tmp, sortie)
    finally:
        if Path(tmp).exists():
            Path(tmp).unlink()


def preparer_ma_voix(dossier_voix, rapport=None):
    """Le fichier de « ma voix seule » : en cache (calculé au besoin), ou la voix brute d'un ancien doublage."""
    plan = plan_ma_voix(dossier_voix, rapport)
    if plan.direct is not None:
        return plan.direct
    if not plan.a_combiner:
        raise ErreurPistes(plan.explication)
    sortie = chemin_ma_voix(plan)
    if sortie.is_file():
        os.utime(sortie)    # récemment écoutée : la purge la garde
        return sortie
    sortie.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(sortie)
    duree = duree_audio(plan.instrumental) if plan.instrumental else None
    _lancer(commande_ma_voix(plan, tmp, duree), tmp, sortie)
    chemins.purger_dossier(sortie.parent, CACHE_VOIX_MO)
    return sortie


def ma_voix_prete(dossier_voix, rapport=None):
    """Le fichier de « ma voix seule » s'il est déjà prêt (cache ou voix brute), sinon None ; ne calcule rien."""
    plan = plan_ma_voix(dossier_voix, rapport)
    if plan.direct is not None:
        return plan.direct
    f = chemin_ma_voix(plan) if plan.a_combiner else None
    return f if f is not None and f.is_file() else None


# --- Choix des pistes ------------------------------------------------------------------------------------------------

@dataclass
class Piste:
    cle: str
    fichier: Path | None = None     # ce qu'on lit ; None : indisponible, ou à préparer (a_preparer)
    decalage: float = 0.0           # position dans le fichier = position dans le doublage + décalage
    image: bool = False             # le fichier a une image
    explication: str = ""           # ce que la piste contient, ou pourquoi elle manque
    a_preparer: bool = False        # ma_voix : à combiner (en cache) avant de l'écouter

    @property
    def disponible(self):
        return self.fichier is not None or self.a_preparer

    @property
    def libelle(self):
        return LIBELLES.get(self.cle, self.cle)


def fichier_voix_source(travail):
    for nom in ("voix_source.wav", "voix_source.flac"):
        f = Path(travail) / nom
        if f.is_file():
            return f
    return None


def choisir_pistes(res, forme="video", source_choisie=None):
    """{clé: Piste} dans l'ordre de CLES, pour un résultat (voixla.rapports.Resultat) lu en « video » ou en « mp3 ».
    source_choisie : la vidéo d'origine indiquée à la main quand celle de source.json est introuvable."""
    p = {}
    original_wav = res.original_wav
    if forme == "video":
        p["doublage"] = Piste("doublage", res.video, image=True, explication="la vidéo doublée") if res.video else \
            Piste("doublage", explication="pas de vidéo doublée dans ce dossier")
        src = res.video_source if res.source_presente else _fichier(source_choisie)
        if src is not None:
            p["original"] = Piste("original", src, res.decalage_source_s, image=True,
                                  explication="la vidéo d’origine, à la même position")
        elif original_wav is not None:
            v = res.video_source
            p["original"] = Piste("original", original_wav,
                                  explication="vidéo d’origine introuvable" + (f" ({v})" if v else "")
                                  + " : son d’origine seul (travail/original.wav), sans image ; « Choisir… » dans "
                                    "Doublage pour retrouver l’image")
        else:
            p["original"] = Piste("original", explication="ni vidéo d’origine ni travail/original.wav")
    else:
        p["doublage"] = Piste("doublage", res.mp3, explication="le MP3 du doublage") if res.mp3 else \
            Piste("doublage", explication="pas de MP3 dans ce dossier")
        p["original"] = Piste("original", original_wav, explication="le son d’origine extrait (travail/original.wav)") \
            if original_wav is not None else Piste("original", explication="travail/original.wav manque")
    beat = _fichier(res.travail / "instrumental.wav")
    p["beat"] = Piste("beat", beat, explication="la musique sans aucune voix (travail/instrumental.wav)") if beat else \
        Piste("beat", explication="travail/instrumental.wav manque : doublage inachevé ou fait avant ce fichier")
    plan = plan_ma_voix(res.dossier_voix, res.rapport) if res.dossier_voix else \
        plan_ma_voix(None)
    if plan.direct is not None:
        p["ma_voix"] = Piste("ma_voix", plan.direct, explication=plan.explication)
    elif plan.a_combiner:
        f = chemin_ma_voix(plan)
        prete = f is not None and f.is_file()
        p["ma_voix"] = Piste("ma_voix", f if prete else None, explication=plan.explication, a_preparer=not prete)
    else:
        p["ma_voix"] = Piste("ma_voix", explication=plan.explication)
    vs = fichier_voix_source(res.travail)
    p["voix_origine"] = Piste("voix_origine", vs, explication=f"la voix d’origine isolée (travail/{vs.name})") if vs \
        else Piste("voix_origine", explication="travail/voix_source.wav manque")
    return {c: p[c] for c in CLES}


def piste_apres_changement(pistes, voulue):
    """La piste à garder quand les pistes changent (autre forme, remix) : la même si elle est encore là, sinon le
    doublage, sinon la première disponible ; None s'il n'y a rien à lire."""
    if voulue in pistes and pistes[voulue].disponible:
        return voulue
    if pistes.get("doublage") and pistes["doublage"].fichier is not None:
        return "doublage"
    return next((c for c, x in pistes.items() if x.fichier is not None), None)


# --- Noms des fichiers enregistrés -----------------------------------------------------------------------------------

def base_sorties(res):
    """« <titre> - voix <Nom> », le nom des sorties de ce doublage sans extension."""
    for f in (res.mp3, res.video):
        if f is not None:
            return f.stem
    titre = res.video_source.stem if res.video_source else res.titre_court
    return f"{titre} - voix {res.voix_nom or res.voix or '?'}"


def nom_export(res, cle, extension=".mp3"):
    """Nom d'un fichier enregistré à côté des sorties : « <titre> - voix <Nom> (beat).mp3 », « … (voix seule).mp3 »."""
    if cle not in SUFFIXES_EXPORT:
        raise ValueError(cle)
    return f"{base_sorties(res)}{SUFFIXES_EXPORT[cle]}{extension}"


def exports_existants(res):
    """Les fichiers déjà enregistrés pour ce doublage (pour la corbeille)."""
    return [res.dossier / nom_export(res, cle, ext) for cle in SUFFIXES_EXPORT for ext in FORMATS_EXPORT
            if (res.dossier / nom_export(res, cle, ext)).is_file()]


def commande_export(source, sortie, tmp=None):
    """ffmpeg (sous nice) qui écrit source dans sortie, au format de son extension (.mp3 320k, .flac ou .wav)."""
    sortie = Path(sortie)
    ext = sortie.suffix.lower()
    if ext not in FORMATS_EXPORT:
        raise ErreurPistes(f"format non pris en charge : « {sortie.suffix or 'sans extension'} » (mp3, flac ou wav)")
    return ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
            "-map", "0:a:0", "-map_metadata", "-1", "-metadata", f"title={sortie.stem}", *FORMATS_EXPORT[ext],
            str(tmp or sortie)]


def exporter(source, sortie):
    """Écrit source dans sortie (écriture atomique : fichier temporaire à côté, puis renommage)."""
    sortie = Path(sortie)
    if sortie.suffix.lower() not in FORMATS_EXPORT:
        raise ErreurPistes(f"format non pris en charge : « {sortie.suffix or 'sans extension'} » (mp3, flac ou wav)")
    if not Path(source).is_file():
        raise ErreurPistes(f"fichier introuvable : {source}")
    tmp = _tmp(sortie)
    _lancer(commande_export(source, sortie, tmp), tmp, sortie)
    return sortie


# --- Ligne de commande -----------------------------------------------------------------------------------------------

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    debut = time.time()
    try:
        if len(argv) == 2 and argv[0] == "voix":
            f = preparer_ma_voix(argv[1])
            print(json.dumps({"voix": str(f), "duree_s": round(time.time() - debut, 2)}, ensure_ascii=False))
            return 0
        if argv[:1] == ["exporter"] and len(argv) in (3, 4):
            if argv[1] == "--voix":
                if len(argv) != 4:
                    print(__doc__)
                    return 2
                source, sortie = preparer_ma_voix(argv[2]), argv[3]
            elif len(argv) == 3:
                source, sortie = argv[1], argv[2]
            else:
                print(__doc__)
                return 2
            exporter(source, sortie)
            print(json.dumps({"sortie": str(sortie), "duree_s": round(time.time() - debut, 2)}, ensure_ascii=False))
            return 0
    except (ErreurPistes, OSError) as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
