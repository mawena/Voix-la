"""Paroles synchronisées (sans GTK ni numpy, testable) : lecture de travail/<voix>/paroles.json, écrit par doubler,
sinon repli sur les sous-titres de la vidéo doublée, extraits par ffmpeg et gardés en cache. Synchronisation sur la
lecture : ligne en cours, ligne suivante, mot en cours.

- paroles.json (format 1 ; temps en secondes de la vidéo doublée, « decalage_s » : leur décalage par rapport au son
  doublé, dont le temps vaut debut − decalage_s) :
    {"format": 1, "langue": "es", "source": "whisper-large-v3", "decalage_s": 0.0,
     "segments": [{"debut": 64.5, "fin": 66.0, "texte": "…", "mots": [{"debut": 64.5, "fin": 64.8, "mot": "…"}]}],
     "traductions": {"fr": {"source": "<modèle>", "segments": [{"debut": 64.5, "fin": 66.0, "texte": "…"}]}}}
  « mots », « traductions » et « decalage_s » sont facultatifs. Les segments ou mots abîmés sont ignorés un par un ; le
  fichier n'est jamais modifié. Le temps des paroles n'est pas celui du lecteur : voir ecart_lecture.
- Sous-titres : chaque flux texte de la vidéo doublée (subrip, mov_text, ass…) est converti en SRT par
  « ffmpeg -map 0:s:N -f srt » dans ~/.cache/voix-la/sous-titres/ (VOIXLA_CACHE pour les essais) : <clé>.json (index
  des flux : langue, titre, codec, fichier) et <clé>-<N>.srt. La clé dépend du chemin, de la taille et de la date de la
  vidéo : une vidéo refaite est ré-extraite. Rien n'est écrit à côté de la vidéo. Les sous-titres en image (PGS, DVD)
  ne sont pas lus. ffmpeg compte les temps du SRT depuis le début du fichier ; un début négatif (délai d'amorce de
  l'AAC dans un mkv, « decalage_s » de l'index) est rajouté à la lecture, pour suivre la position du lecteur.
En ligne de commande (lancé par l'interface sous nice) :
  python3 -m voixla.paroles sous-titres VIDEO
"""

import bisect
import hashlib
import html
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import chemins
from .pistes import avance_piste, decalage_son  # noqa: F401  (decalage_son : lu dans travail/source.json)

VERSION = 1                 # version du cache des sous-titres
CACHE_MAX_MO = 20
SOURCE_SOUS_TITRES = "sous-titres de la vidéo"
CODECS_TEXTE = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd", "subviewer", "jacosub",
                "realtext", "sami", "stl"}
ECART_PROCHE_S = 0.5        # correspondante : écart de début toléré quand aucune ligne ne recouvre


class ErreurParoles(RuntimeError):
    pass


@dataclass
class Mot:
    debut: float
    fin: float
    mot: str              # espaces de tête et de fin retirés


@dataclass
class Ligne:
    debut: float
    fin: float
    texte: str
    mots: list = field(default_factory=list)      # [Mot], peut être vide


@dataclass
class Couche:
    """Un texte synchronisé dans une langue."""
    langue: str | None    # code tel que donné ("es", "fra"…), None si inconnu
    source: str           # "whisper-large-v3", modèle de traduction, "sous-titres de la vidéo"…
    lignes: list          # [Ligne] triées par début
    titre: str = ""       # titre du flux de sous-titres (« FORCED »…), sinon ""


@dataclass
class Paroles:
    originale: Couche
    traductions: dict     # {"fr": Couche} ; peut être vide
    origine: str          # "paroles.json" ou "sous-titres"
    decalage_s: float = 0.0   # paroles.json : décalage par rapport au son doublé (temps du son = debut − decalage_s)

    def _francaise(self):
        c = self.traductions.get("fr")
        return c if c is not None and c.lignes else None

    def couches(self, choix):
        """choix ∈ {"originale", "fr", "deux"} → [Couche] à afficher, principale d'abord (1 ou 2 couches) :
        "originale" → [originale] ; "fr" → [traductions["fr"]] si elle existe, sinon [originale] ;
        "deux" → [originale, traductions["fr"]] si elle existe, sinon [originale]. Valeur inconnue → comme "deux"."""
        fr = self._francaise()
        if choix == "originale":
            return [self.originale]
        if choix == "fr":
            return [fr] if fr else [self.originale]
        return [self.originale, fr] if fr else [self.originale]

    def a_deux_langues(self):
        """Vrai si une traduction française non vide existe (le choix de langue a alors un sens)."""
        return self._francaise() is not None


# --- Langues ---------------------------------------------------------------------------------------------------------

LANGUES = {
    "fr": "français", "fra": "français", "fre": "français",
    "en": "anglais", "eng": "anglais",
    "es": "espagnol", "spa": "espagnol",
    "it": "italien", "ita": "italien",
    "de": "allemand", "deu": "allemand", "ger": "allemand",
    "pt": "portugais", "por": "portugais",
    "nl": "néerlandais", "nld": "néerlandais", "dut": "néerlandais",
    "ja": "japonais", "jpn": "japonais",
    "ko": "coréen", "kor": "coréen",
    "zh": "chinois", "zho": "chinois", "chi": "chinois",
    "ar": "arabe", "ara": "arabe",
    "ru": "russe", "rus": "russe",
    "und": "langue non indiquée",
}


def _code(code):
    return code.strip().lower().replace("_", "-") if isinstance(code, str) else ""


def est_francais(code):
    """fr, fra, fre, french, français, fr-FR, fr_CA… (casse ignorée)."""
    c = _code(code)
    return c in ("fr", "fra", "fre", "french", "français", "francais") or c.startswith("fr-")


def nom_langue(code):
    """Nom français d'un code de langue courant (ISO 639-1 ou 639-2) ; un code inconnu est rendu tel quel."""
    c = _code(code)
    if not c:
        return "langue non indiquée"
    if c in LANGUES:
        return LANGUES[c]
    if c.split("-")[0] in LANGUES:
        return LANGUES[c.split("-")[0]]
    if est_francais(c):
        return "français"
    return code.strip()


# --- paroles.json ----------------------------------------------------------------------------------------------------

def _temps(v):
    """Un temps valide (nombre fini, positif ou nul) en float, sinon None (booléens et chaînes refusés)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) and v >= 0 else None


def _texte(v):
    return " ".join(v.split()) if isinstance(v, str) else ""


def _mots(liste):
    if not isinstance(liste, list):
        return []
    mots = []
    for m in liste:
        if not isinstance(m, dict):
            continue
        d, f = _temps(m.get("debut")), _temps(m.get("fin"))
        mot = _texte(m.get("mot"))
        if d is None or f is None or f < d or not mot:
            continue
        mots.append(Mot(d, f, mot))
    mots.sort(key=lambda m: (m.debut, m.fin))
    return mots


def _lignes(segments):
    """Segments valides en [Ligne] triées ; None si segments n'est pas une liste."""
    if not isinstance(segments, list):
        return None
    lignes = []
    for s in segments:
        if not isinstance(s, dict):
            continue
        d, f = _temps(s.get("debut")), _temps(s.get("fin"))
        if d is None or f is None or f < d:
            continue
        mots = _mots(s.get("mots"))
        texte = _texte(s.get("texte"))
        if not texte:
            if not mots:
                continue
            texte = " ".join(m.mot for m in mots)
        lignes.append(Ligne(d, f, texte, mots))
    lignes.sort(key=lambda l: (l.debut, l.fin))
    return lignes


def lire_paroles(chemin):
    """travail/<voix>/paroles.json → Paroles, ou None s'il manque, est illisible, n'est pas au format 1 ou n'a aucun
    segment valide."""
    d = chemins.lire_json(chemin)
    if not isinstance(d, dict):
        return None
    fmt = d.get("format")
    if isinstance(fmt, bool) or fmt != 1:
        return None
    lignes = _lignes(d.get("segments"))
    if not lignes:
        return None
    langue = d.get("langue") if isinstance(d.get("langue"), str) and d["langue"].strip() else None
    source = d["source"] if isinstance(d.get("source"), str) else ""
    traductions = {}
    t = d.get("traductions")
    if isinstance(t, dict):
        for cle, v in t.items():
            if not isinstance(v, dict):
                continue
            l = _lignes(v.get("segments"))
            if not l:
                continue
            rangee = "fr" if est_francais(cle) else str(cle)
            if rangee in traductions and cle != "fr":
                continue   # « fr » et « fra » à la fois : « fr » l'emporte
            traductions[rangee] = Couche(langue=str(cle), source=v["source"] if isinstance(v.get("source"), str)
                                         else "", lignes=l)
    return Paroles(Couche(langue=langue.strip() if langue else None, source=source, lignes=lignes), traductions,
                   "paroles.json", _decalage(d.get("decalage_s")) or 0.0)


def _decalage(v):
    """Un décalage valide (nombre fini, de signe quelconque) en float, sinon None (booléens et chaînes refusés)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def ecart_lecture(paroles, avec_image=True, debut_son=None):
    """Temps des paroles = position dans la piste lue + cet écart (s) ; retrancher l'écart d'un temps des paroles donne
    la position où aller.

    La piste lue compte en temps du son doublé + pistes.avance_piste(avec_image, debut_son), la règle de passage du
    temps du son au temps de la piste :
    - avec image (la vidéo doublée, ses copies des pistes séparées, qui gardent ses temps, et la vidéo d'origine au
      décalage de l'extrait) : le temps de la vidéo doublée, où le son doublé commence à debut_son
      (pistes.debut_son_video : decalage_s de paroles.json, sinon decalage_son de source.json) ;
    - sans image (le MP3, travail/original.wav, le beat ou une voix seule lus sans copie vidéo, le son seul quand la
      fenêtre est fermée) : le temps du son doublé. C'est aussi le temps du lecteur de la page d'un résultat, quelle
      que soit la piste (pistes.Piste.decalage_lu).
    paroles.json est en temps du son + decalage_s (doubler.caler_paroles). Pour un extrait mkv ou webm, doubler
    décale le mix de −0,13 s (decalage_s), mais le conteneur recale tout à partir de la première image : dans la
    vidéo doublée, le son commence avec l'image (debut_son 0), pas 0,13 s avant. Les sous-titres de la vidéo doublée
    sont en temps de la vidéo : temps du son + debut_son. debut_son None (source.json absent ou sans decalage_son) :
    decalage_s s'il est positif (vidéo entière au son en retard), sinon 0."""
    if paroles is None:
        return 0.0
    dec = paroles.decalage_s if paroles.origine == "paroles.json" else 0.0
    video = debut_son if debut_son is not None else max(0.0, dec)
    # temps du son = position − avance_piste ; paroles.json : temps du son + decalage_s ; sous-titres : + video
    return (dec if paroles.origine == "paroles.json" else video) - avance_piste(avec_image, video)


# --- SRT -------------------------------------------------------------------------------------------------------------

_HORODATAGE = r"(\d{1,2}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
FLECHE = re.compile(rf"^\s*{_HORODATAGE}\s*-->\s*{_HORODATAGE}")
BALISE = re.compile(r"</?[A-Za-z][^>]*>")
ACCOLADES_ASS = re.compile(r"\{[^}]*\}")


def _secondes(h, m, s, fraction):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(fraction) / 10 ** len(fraction)


def _nettoyer(lignes):
    texte = " ".join(lignes).replace("\\N", " ").replace("\\n", " ")
    texte = ACCOLADES_ASS.sub("", texte)
    texte = BALISE.sub("", texte)
    return " ".join(html.unescape(texte).split())


def lire_srt(texte, langue=None, source=SOURCE_SOUS_TITRES, titre=""):
    """Texte SRT → Couche (BOM, CRLF, numéros absents, blocs abîmés ignorés, balises et accolades ASS retirées,
    lignes d'un bloc jointes par une espace), lignes triées par début."""
    texte = (texte or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    blocs, courant = [], None
    for brute in texte.split("\n"):
        m = FLECHE.match(brute)
        if m:
            if courant is not None:
                if courant[2] and courant[2][-1].strip().isdigit():
                    courant[2].pop()   # numéro du bloc suivant, sans ligne vide avant lui
                blocs.append(courant)
            g = m.groups()
            courant = [_secondes(*g[:4]), _secondes(*g[4:]), []]
            continue
        if courant is None:
            continue
        if not brute.strip():
            blocs.append(courant)
            courant = None
            continue
        courant[2].append(brute)
    if courant is not None:
        blocs.append(courant)
    lignes = []
    for debut, fin, contenu in blocs:
        t = _nettoyer(contenu)
        if t and fin >= debut:
            lignes.append(Ligne(float(debut), float(fin), t))
    lignes.sort(key=lambda l: (l.debut, l.fin))
    return Couche(langue=langue if isinstance(langue, str) and langue.strip() else None, source=source, lignes=lignes,
                  titre=titre or "")


# --- Sous-titres de la vidéo -----------------------------------------------------------------------------------------

def _etiquette(tags, nom):
    for k, v in (tags or {}).items():
        if k.lower() == nom and isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _sonder(video):
    """(flux de sous-titres, début du fichier en secondes) d'après ffprobe."""
    entrees = "format=start_time:stream=index,codec_name:stream_tags=language,title:" \
              "stream_disposition=default,forced"
    commande = ["nice", "-n", "10", "ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", entrees,
                "-of", "json", str(video)]
    try:
        r = subprocess.run(commande, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise ErreurParoles(f"ffprobe impossible : {e}") from e
    if r.returncode != 0:
        raise ErreurParoles(f"vidéo illisible : {video} ({r.stderr.strip()[-300:] or r.returncode})")
    try:
        d = json.loads(r.stdout or "{}")
        flux = d.get("streams") or []
        debut = float((d.get("format") or {}).get("start_time") or 0)
    except (ValueError, AttributeError, TypeError) as e:
        raise ErreurParoles(f"réponse de ffprobe illisible : {e}") from e
    return flux, (debut if math.isfinite(debut) else 0.0)


def flux_sous_titres(video):
    """Les flux de sous-titres de la vidéo (ffprobe) : [{n, index, codec, langue, titre, defaut, force, texte}]."""
    return _lire_flux(_sonder(video)[0])


def _lire_flux(flux):
    sortie = []
    for n, s in enumerate(f for f in flux if isinstance(f, dict)):
        codec = str(s.get("codec_name") or "")
        tags = s.get("tags") if isinstance(s.get("tags"), dict) else {}
        dispo = s.get("disposition") if isinstance(s.get("disposition"), dict) else {}
        titre = _etiquette(tags, "title") or ""
        sortie.append({"n": n, "index": s.get("index"), "codec": codec, "langue": _etiquette(tags, "language"),
                       "titre": titre, "defaut": bool(dispo.get("default")),
                       "force": bool(dispo.get("forced")) or "forced" in titre.lower(),
                       "texte": codec.lower() in CODECS_TEXTE})
    return sortie


def dossier_cache():
    return chemins.dossier_cache() / "sous-titres"


def cle_video(video):
    """Clé du cache : chemin réel, taille et date de la vidéo (change dès qu'elle est refaite)."""
    p = Path(video).expanduser().resolve()
    st = p.stat()
    return hashlib.sha1(f"{p}|{st.st_size}|{st.st_mtime_ns}|{VERSION}".encode()).hexdigest()[:24]


def _tmp(chemin):
    return chemin.with_name(f"{chemin.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")


def extraire_sous_titres(video):
    """Extrait chaque flux texte en SRT dans le cache, écrit l'index <clé>.json et le rend."""
    video = Path(video).expanduser()
    if not video.is_file():
        raise ErreurParoles(f"vidéo doublée introuvable : {video}")
    cle = cle_video(video)
    d = dossier_cache()
    d.mkdir(parents=True, exist_ok=True)
    bruts, debut_fichier = _sonder(video)
    flux = []
    for f in _lire_flux(bruts):
        f = {**f, "fichier": None}
        if f["texte"]:
            nom = f"{cle}-{f['n']}.srt"
            tmp = _tmp(d / nom)
            try:
                r = subprocess.run(["nice", "-n", "10", "ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(video),
                                    "-map", f"0:s:{f['n']}", "-f", "srt", str(tmp)],
                                   capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
                if r.returncode == 0 and tmp.exists():
                    os.replace(tmp, d / nom)
                    f["fichier"] = nom
                else:
                    f["erreur"] = r.stderr.strip()[-300:] or f"ffmpeg : code {r.returncode}"
            except (OSError, subprocess.SubprocessError) as e:
                f["erreur"] = str(e)
            finally:
                if tmp.exists():
                    tmp.unlink()
        flux.append(f)
    # ffmpeg retranche le début du fichier des temps du SRT. Un début négatif (délai d'amorce de l'AAC dans un mkv)
    # n'existe pas pour le lecteur, qui compte depuis l'image 0 : on le rajoute à la lecture du SRT.
    index = {"format": 1, "video": str(video.resolve()), "decalage_s": round(min(0.0, debut_fichier), 6),
             "flux": flux}
    sortie = d / f"{cle}.json"
    tmp = _tmp(sortie)
    try:
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, sortie)
    finally:
        if tmp.exists():
            tmp.unlink()
    chemins.purger_dossier(d, CACHE_MAX_MO)
    return index


def sous_titres_en_cache(video):
    """(Paroles, None) si les sous-titres extraits sont en cache ; (None, "a_extraire") s'il faut les extraire ;
    (None, raison) s'il n'y a rien à afficher."""
    if not video:
        return None, "vidéo doublée introuvable"
    video = Path(video).expanduser()
    try:
        if not video.is_file():
            return None, "vidéo doublée introuvable"
        cle = cle_video(video)
    except OSError:
        return None, "vidéo doublée introuvable"
    d = dossier_cache()
    index = chemins.lire_json(d / f"{cle}.json")
    if not isinstance(index, dict) or index.get("format") != 1 or not isinstance(index.get("flux"), list):
        return None, "a_extraire"
    flux = [f for f in index["flux"] if isinstance(f, dict)]
    if not flux:
        return None, "pas de sous-titres dans la vidéo doublée"
    textes = [f for f in flux if f.get("texte")]
    if not textes:
        codecs = ", ".join(sorted({str(f.get("codec") or "?") for f in flux}))
        return None, f"sous-titres en image ({codecs}) : pas de texte à afficher"
    dec = index.get("decalage_s")
    avance = _temps(-dec) if isinstance(dec, (int, float)) and not isinstance(dec, bool) and dec < 0 else None
    lus = []
    for f in textes:
        nom = f.get("fichier")
        if not isinstance(nom, str) or not nom or "/" in nom or nom.startswith("."):
            continue   # extraction en échec pour ce flux
        try:
            contenu = (d / nom).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None, "a_extraire"   # SRT effacé par la purge du cache : on refait l'extraction
        c = lire_srt(contenu, langue=f.get("langue"), titre=f.get("titre") or "")
        if avance:
            c.lignes = [Ligne(max(0.0, round(l.debut - avance, 3)), max(0.0, round(l.fin - avance, 3)), l.texte)
                        for l in c.lignes]
        if c.lignes:
            lus.append((f, c))
    if not lus:
        return None, "sous-titres vides"
    ordre = sorted(lus, key=lambda fc: (bool(fc[0].get("force")), not fc[0].get("defaut"), fc[0].get("n") or 0))
    originale = ordre[0][1]
    traductions = {}
    if not est_francais(originale.langue):
        fr = [c for _f, c in ordre[1:] if est_francais(c.langue)]
        if fr:
            traductions["fr"] = fr[0]
    return Paroles(originale, traductions, "sous-titres"), None


def paroles_pour(dossier_voix, video=None):
    """paroles.json de la voix, sinon les sous-titres de la vidéo doublée : (Paroles, None), (None, "a_extraire")
    ou (None, raison)."""
    abime = False
    if dossier_voix:
        f = Path(dossier_voix) / "paroles.json"
        if f.exists():
            p = lire_paroles(f)
            if p is not None:
                return p, None
            abime = True
    prefixe = "paroles.json illisible ; " if abime else ""
    if not video:
        return None, prefixe + ("pas de vidéo doublée pour les sous-titres" if abime else
                                "ni paroles.json ni vidéo doublée")
    p, raison = sous_titres_en_cache(video)
    if p is not None:
        return p, None
    if raison == "a_extraire":
        return None, raison
    return None, prefixe + raison


# --- Synchronisation -------------------------------------------------------------------------------------------------

def position(lignes, t):
    """(indice de la ligne en cours ou None, indice de la ligne suivante ou None) à l'instant t."""
    if not lignes:
        return None, None
    k = bisect.bisect_right([l.debut for l in lignes], t)     # lignes[:k] ont commencé
    courante = None
    for i in range(k - 1, -1, -1):
        if lignes[i].debut <= t < lignes[i].fin:
            courante = i
            break
    return courante, (k if k < len(lignes) else None)


def mot_en_cours(mots, t):
    """Indice du dernier mot commencé à t (il reste surligné jusqu'au début du suivant), ou None."""
    if not mots:
        return None
    k = bisect.bisect_right([m.debut for m in mots], t)
    return k - 1 if k else None


def correspondante(lignes, ligne):
    """Dans une autre couche, la ligne qui recouvre le plus celle-ci, sinon celle qui commence le plus près (à moins
    de 0,5 s), sinon None."""
    meilleure, recouvrement = None, 0.0
    for l in lignes or []:
        r = min(l.fin, ligne.fin) - max(l.debut, ligne.debut)
        if r > recouvrement:
            meilleure, recouvrement = l, r
    if meilleure is not None:
        return meilleure
    proche = min(lignes or [], key=lambda l: abs(l.debut - ligne.debut), default=None)
    if proche is not None and abs(proche.debut - ligne.debut) <= ECART_PROCHE_S:
        return proche
    return None


def _minuscules(s):
    """s en minuscules, sauf si cela change sa longueur (quelques lettres hors de l'alphabet latin) : les indices
    trouvés dans le texte en minuscules doivent valoir dans le texte affiché."""
    m = s.lower()
    return (m, True) if len(m) == len(s) else (s, False)


def fragments(ligne):
    """[(texte, indice du mot ou None)] dont la concaténation rend le texte affiché : les mots sont repérés dans le
    texte (ponctuation et espaces gardés) ; si l'un d'eux n'y est pas, le texte est refait à partir des mots."""
    texte = ligne.texte
    if not ligne.mots:
        return [(texte, None)]
    bas, plie = _minuscules(texte)
    morceaux, pos = [], 0
    for i, m in enumerate(ligne.mots):
        aiguille = _minuscules(m.mot)[0] if plie else m.mot
        j = bas.find(aiguille, pos)
        if j < 0:
            break
        if j > pos:
            morceaux.append((texte[pos:j], None))
        morceaux.append((texte[j:j + len(aiguille)], i))
        pos = j + len(aiguille)
    else:
        if pos < len(texte):
            morceaux.append((texte[pos:], None))
        return morceaux
    sortie = []
    for i, m in enumerate(ligne.mots):
        if i:
            sortie.append((" ", None))
        sortie.append((m.mot, i))
    return sortie


# --- Karaoké : lignes voisines et remplissage des mots (sans GTK) ----------------------------------------------------

AVANCE_FOCALE_S = 3.0       # entre deux lignes : la suivante prend la place centrale si elle commence dans ce délai


def voisines(lignes, t, avance_s=AVANCE_FOCALE_S):
    """Les lignes à montrer à l'instant t : (précédente, focale, suivante, active), des indices ou None.

    - focale : la ligne en cours (active vrai) ; entre deux lignes, la prochaine si elle commence dans moins de
      avance_s (active faux : on la montre, pas encore chantée) ; sinon None (passage instrumental, « ♪ ») ;
    - précédente et suivante : les voisines de la focale dans la liste (indice − 1 et + 1) ; sans focale, la dernière
      ligne commencée et la prochaine.
    Lignes vides ou absentes : (None, None, None, False)."""
    n = len(lignes or [])
    if not n:
        return None, None, None, False
    courante, suivante = position(lignes, t)
    if courante is not None:
        focale, active = courante, True
    elif suivante is not None and lignes[suivante].debut - t <= avance_s:
        focale, active = suivante, False
    else:
        prec = (suivante - 1 if suivante else None) if suivante is not None else n - 1
        return prec, None, suivante, False
    return (focale - 1 if focale > 0 else None), focale, (focale + 1 if focale + 1 < n else None), active


def centre_karaoke(lignes, t, avance_s=AVANCE_FOCALE_S):
    """La place centrale du karaoké, en « indice de ligne » : la focale (voisines), sinon, en passage instrumental,
    la demi-place entre la dernière ligne commencée et la prochaine (k − 0,5 ; −0,5 avant la première, n − 0,5 après
    la dernière). Une transition douce fait glisser d'une valeur à l'autre ; None sans lignes."""
    n = len(lignes or [])
    if not n:
        return None
    prec, focale, suivante, _a = voisines(lignes, t, avance_s)
    if focale is not None:
        return float(focale)
    return (suivante if suivante is not None else n) - 0.5


def portees(ligne):
    """(texte affiché, [(début, fin, suite, indice du mot)]) : pour chaque mot, sa place dans le texte affiché (en
    caractères, celui que donne fragments) ; suite : le début du mot suivant (ou la fin du texte), pour colorer aussi la
    ponctuation et l'espace qui suivent un mot déjà chanté. Ligne sans mots : (texte, [])."""
    if ligne is None:
        return "", []
    morceaux = fragments(ligne)
    texte = "".join(t for t, _i in morceaux)
    mots, pos = [], 0
    for t, i in morceaux:
        if i is not None:
            mots.append([pos, pos + len(t), None, i])
        pos += len(t)
    for k, m in enumerate(mots):
        m[2] = mots[k + 1][0] if k + 1 < len(mots) else len(texte)
    return texte, [tuple(m) for m in mots]


def fraction_mot(mot, t):
    """Part du mot déjà chantée à t, de 0 (pas commencé) à 1 (fini), linéaire de son début à sa fin. Un mot de durée
    nulle (ou négative) passe d'un coup de 0 à 1 à son début."""
    if t < mot.debut:
        return 0.0
    if mot.fin <= mot.debut or t >= mot.fin:
        return 1.0
    return (t - mot.debut) / (mot.fin - mot.debut)


def remplissage(ligne, t):
    """[(début, fin, fraction)] : pour chaque mot de la ligne, sa place dans le texte affiché (portees) et la part déjà
    chantée à t (fraction_mot). Ligne sans mots : []."""
    if ligne is None or not ligne.mots:
        return []
    _texte, mots = portees(ligne)
    return [(d, f, fraction_mot(ligne.mots[i], t)) for d, f, _s, i in mots]


# --- Affichage (texte Pango, sans GTK) -------------------------------------------------------------------------------

def _esc(texte):
    return html.escape(texte or "", quote=False)


def balisage(ligne, i_mot, couleur):
    """Texte Pango d'une ligne : mots déjà chantés tels quels, mot en cours souligné à la couleur donnée (« #3584e4 »),
    mots à venir atténués ; la ponctuation suit le mot qui la précède. i_mot = None : la ligne a commencé mais pas son
    premier mot (tout est à venir) ; i_mot = -1 ou ligne sans mots : le texte tel quel (ligne suivante)."""
    if ligne is None:
        return ""
    if not ligne.mots or i_mot == -1:
        return _esc(ligne.texte)
    morceaux, avant = [], -1
    for texte, i in fragments(ligne):
        rang = i if i is not None else avant
        t = _esc(texte)
        if i is not None and i == i_mot:
            morceaux.append(f'<span foreground="{couleur}" underline="single">{t}</span>')
        elif i_mot is None or rang > i_mot or (i is None and rang == i_mot):
            morceaux.append(f'<span alpha="58%">{t}</span>')
        else:
            morceaux.append(t)
        if i is not None:
            avant = i
    return "".join(morceaux)


def legende_source(paroles, couches):
    """D'où viennent les paroles affichées : « Whisper-large-v3 · espagnol ; traduction en français : <modèle> » ou
    « Sous-titres de la vidéo · français (fra) · forcés »."""
    morceaux = []
    for k, c in enumerate(couches):
        langue = nom_langue(c.langue)
        if paroles.origine == "sous-titres":
            code = f" ({c.langue})" if c.langue else ""
            force = " · forcés" if "forced" in (c.titre or "").lower() else ""
            morceaux.append(f"{'sous-titres de la vidéo · ' if k == 0 else ''}{langue}{code}{force}")
        elif k == 0:
            morceaux.append(f"{c.source or 'transcription'} · {langue}")
        else:
            morceaux.append(f"traduction en {langue}" + (f" : {c.source}" if c.source else ""))
    texte = " ; ".join(morceaux)
    return texte[:1].upper() + texte[1:]


# --- Ligne de commande -----------------------------------------------------------------------------------------------

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "sous-titres":
        print(__doc__)
        return 2
    debut = time.time()
    try:
        index = extraire_sous_titres(argv[1])
    except (ErreurParoles, OSError) as e:
        print(f"sous-titres impossibles : {e}", file=sys.stderr)
        return 1
    print(json.dumps({"video": index["video"], "flux": len(index["flux"]),
                      "texte": sum(1 for f in index["flux"] if f.get("texte")),
                      "duree_s": round(time.time() - debut, 2)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
