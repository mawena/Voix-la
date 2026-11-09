"""Écoute à l'aveugle du banc d'essai (sans GTK, testable).

banc/ecoute/<extrait>/A.mp3, B.mp3… (mix complet), A_voix.mp3… (voix seule), original.mp3 ;
banc/ecoute/correspondance.json : {"<extrait>": {"A": "<version>", …}, "note": …, "graine": …, "gains_db": …}.
Les votes vont dans banc/ecoute/votes.json :
  {"format": 1, "votes": [{"extrait", "date", "notes": {"A": 1–5}, "classement": ["B", "A"…], "commentaire",
                           "correspondance": {"A": "<version>"…}}]}
La correspondance d'un extrait ne s'affiche qu'après un vote sur la même série de lettres (même correspondance).
Le même format sert à comparer plusieurs voix d'un doublage (voixla.comparaison) : un banc par dossier de sortie,
dans le cache, avec un seul extrait dont les versions sont les voix.
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import chemins

LETTRE = re.compile(r"^([A-Z])$")
CLES_HORS_EXTRAITS = {"note", "graine", "gains_db", "titres", "date", "ids", "voix"}


@dataclass
class Version:
    lettre: str
    mix: Path
    voix: Path | None = None


@dataclass
class Extrait:
    nom: str
    dossier: Path
    versions: list = field(default_factory=list)
    original: Path | None = None
    titre: str = ""


def charger(racine=None):
    """(extraits, correspondance), ou (None, {}) si le banc d'écoute n'existe pas encore."""
    racine = Path(racine) if racine else chemins.dossier_ecoute()
    if not racine.is_dir():
        return None, {}
    correspondance = chemins.lire_json(racine / "correspondance.json", {}) or {}
    titres = correspondance.get("titres") or {}
    extraits = []
    for d in sorted(p for p in racine.iterdir() if p.is_dir() and not p.name.startswith(".")):
        versions = []
        for f in sorted(d.glob("*.mp3")):
            m = LETTRE.match(f.stem)
            if m:
                voix = d / f"{f.stem}_voix.mp3"
                versions.append(Version(lettre=f.stem, mix=f, voix=voix if voix.exists() else None))
        if not versions:
            continue
        original = d / "original.mp3"
        extraits.append(Extrait(nom=d.name, dossier=d, versions=versions,
                                original=original if original.exists() else None,
                                titre=titres.get(d.name) or d.name.replace("-", " ").replace("_", " ").capitalize()))
    return extraits, correspondance


def autre_forme(cle, versions):
    """Interrupteur « voix seule » : pour la clé (extrait, lettre, « mix » | « voix ») en cours, la clé et le fichier
    de l'autre forme de la même version (mix ⇄ voix seule) ; None pour l'original, une lettre inconnue ou une version
    sans X_voix.mp3."""
    if not cle or len(cle) != 3:
        return None
    nom, lettre, sorte = cle
    v = next((x for x in versions or [] if x.lettre == lettre), None)
    if v is None or sorte not in ("mix", "voix"):
        return None
    if sorte == "mix":
        return ((nom, lettre, "voix"), v.voix) if v.voix else None
    return (nom, lettre, "mix"), v.mix


def correspondance_de(correspondance, extrait):
    c = (correspondance or {}).get(extrait)
    return {k: str(v) for k, v in c.items()} if isinstance(c, dict) else {}


def lire_votes(racine=None):
    racine = Path(racine) if racine else chemins.dossier_ecoute()
    d = chemins.lire_json(racine / "votes.json", {}) or {}
    votes = d.get("votes") if isinstance(d, dict) else None
    return [v for v in (votes or []) if isinstance(v, dict)]


def dernier_vote(votes, extrait, correspondance=None):
    """Le dernier vote sur cet extrait ; s'il porte sur une autre série de lettres (banc refait), il ne compte pas."""
    for v in reversed(votes):
        if v.get("extrait") != extrait:
            continue
        if correspondance and v.get("correspondance") != correspondance:
            return None   # banc refait (ou vote sans correspondance) : la nouvelle série reste cachée
        return v
    return None


def classement(notes):
    """Lettres de la mieux notée à la moins bien notée (à égalité, ordre alphabétique)."""
    return [l for l, _ in sorted(notes.items(), key=lambda x: (-int(x[1]), x[0]))]


def enregistrer_vote(extrait, notes, commentaire="", correspondance=None, racine=None):
    """Ajoute un vote à votes.json (écriture atomique) et le rend."""
    racine = Path(racine) if racine else chemins.dossier_ecoute()
    notes = {str(k): int(v) for k, v in notes.items() if v}
    if not notes:
        raise ValueError("aucune note")
    d = chemins.lire_json(racine / "votes.json", {}) or {}
    if not isinstance(d, dict):
        d = {}
    votes = [v for v in (d.get("votes") or []) if isinstance(v, dict)]
    vote = {"extrait": extrait, "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "notes": notes,
            "classement": classement(notes), "commentaire": commentaire.strip(),
            "correspondance": dict(correspondance or {})}
    votes.append(vote)
    chemins.ecrire_json(racine / "votes.json", {"format": 1, **{k: v for k, v in d.items() if k not in ("votes", "format")},
                                                "votes": votes})
    return vote
