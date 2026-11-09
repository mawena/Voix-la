"""Comparer plusieurs voix d'un même extrait, à l'aveugle (sans GTK, testable).

`doubler <vidéo> --comparer voixA,voixB,voixC` fait, dans le dossier de la vidéo, une sortie complète par voix
(« <titre> - voix A.mp3 », travail/A/…) et un comparaison.json qui les réunit. Pour les écouter côte à côte sans savoir
laquelle est laquelle, ce module prépare un petit « banc d'écoute » du même format que celui du banc d'essai
(voixla.ecoute, page Écoute à l'aveugle), dans le cache :

    <cache>/comparaisons/<empreinte du dossier>/comparaison/
        A.mp3, B.mp3, C.mp3        les mix complets, en liens vers les mp3 des sorties (même durée, même instant)
        A_voix.mp3, B_voix.mp3…    la voix convertie seule de chaque version (celle du travail/<voix>/)
        original.mp3               l'extrait d'origine, pour comparer
    <cache>/comparaisons/<empreinte du dossier>/correspondance.json
        {"comparaison": {"A": "<nom de la voix>"…}, "ids": {"comparaison": {"A": "<identifiant>"…}}, "titres": …,
         "graine": …, "voix": ["<identifiant>"…]}
    <cache>/comparaisons/<empreinte du dossier>/votes.json   (voixla.ecoute.enregistrer_vote)

Les lettres sont tirées au hasard (graine gardée) ; tant que les voix comparées sont les mêmes, la même correspondance
est gardée, pour qu'un vote reste valable. La page d'écoute ne montre le nom des voix qu'après le vote.
"""

import hashlib
import os
import random
import shutil
import subprocess
from pathlib import Path

from . import chemins

NOM_MARQUE = "comparaison.json"
EXTRAIT = "comparaison"
LETTRES = "ABCDEFGHIJ"


def marque(dossier):
    """Le comparaison.json que doubler écrit dans le dossier d'une comparaison, ou None (doublage ordinaire)."""
    d = chemins.lire_json(Path(dossier) / NOM_MARQUE, None)
    return d if isinstance(d, dict) and isinstance(d.get("voix"), list) else None


def ordre_demande(dossier):
    """Les identifiants des voix de la comparaison, dans l'ordre demandé à doubler ([] sans comparaison.json)."""
    return [v.get("id") for v in (marque(dossier) or {}).get("voix", []) if isinstance(v, dict) and v.get("id")]


def comparables(resultats):
    """Les résultats d'un même dossier qu'on peut écouter l'un contre l'autre : une voix chacun (pas une voix par
    chanteur), un doublage fini (rapport sans erreur, mp3 présent), dans l'ordre de la comparaison s'il y en a une, sinon
    par nom de voix."""
    bons = [r for r in resultats if r.voix and not r.plusieurs_voix and r.mp3 is not None and Path(r.mp3).is_file()
            and isinstance(r.rapport, dict) and not r.rapport.get("erreur")]
    if not bons:
        return []
    ordre = ordre_demande(bons[0].dossier)
    return sorted(bons, key=lambda r: (ordre.index(r.voix) if r.voix in ordre else len(ordre), r.voix))


def peut_comparer(resultats):
    """Vrai si au moins deux voix du dossier ont un doublage fini (le bouton « Comparer les voix » de la page Résultats)."""
    return len(comparables(resultats)) >= 2


def racine_de(dossier, cache=None):
    """Le banc d'écoute de la comparaison de ce dossier de sortie (un par dossier)."""
    cache = Path(cache) if cache else chemins.dossier_cache() / "comparaisons"
    return cache / hashlib.sha1(str(Path(dossier).resolve()).encode()).hexdigest()[:12]


def _tirer(ids, graine=None):
    """{lettre: identifiant}, tirage au hasard (graine) des voix sur les lettres A, B, C…"""
    ordre = list(ids)
    random.Random(graine).shuffle(ordre)
    return dict(zip(LETTRES, ordre))


def correspondance(ids, ancienne=None, graine=None):
    """(lettres, graine) : {lettre: identifiant} gardé de l'ancienne correspondance si ce sont les mêmes voix, sinon tiré
    à nouveau."""
    anc = ((ancienne or {}).get("ids") or {}).get(EXTRAIT)
    if isinstance(anc, dict) and sorted(anc.values()) == sorted(ids) and (ancienne or {}).get("graine") is not None:
        return {str(k): str(v) for k, v in anc.items()}, ancienne["graine"]
    graine = graine if graine is not None else random.SystemRandom().randrange(1, 10 ** 9)
    return _tirer(ids, graine), graine


def _lier(cible, source):
    """cible → source (lien symbolique ; copie si le système de fichiers n'en veut pas)."""
    cible = Path(cible)
    if cible.is_symlink() or cible.exists():
        if cible.is_symlink() and os.readlink(cible) == str(source):
            return
        cible.unlink()
    try:
        cible.symlink_to(source)
    except OSError:
        shutil.copy2(source, cible)


def _en_mp3(source, dest, kbps=192):
    """source → dest en mp3 par ffmpeg, sauf si dest est déjà plus récent. Vrai si dest existe ensuite."""
    source, dest = Path(source), Path(dest)
    if not source.is_file():
        return False
    try:
        if dest.is_file() and dest.stat().st_mtime >= source.stat().st_mtime:
            return True
    except OSError:
        pass
    if not shutil.which("ffmpeg"):
        return False
    tmp = dest.with_name(f".{dest.stem}.tmp.mp3")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-vn", "-c:a", "libmp3lame", "-b:a",
                        f"{kbps}k", str(tmp)], capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        return False
    os.replace(tmp, dest)
    return True


def preparer(resultats, racine=None, graine=None, titre=None):
    """Prépare le banc d'écoute à l'aveugle des voix comparables de ces résultats (un même dossier de sortie) ; rend la
    racine du banc. ValueError s'il y a moins de deux voix comparables."""
    voix = comparables(resultats)
    if len(voix) < 2:
        raise ValueError("au moins deux voix avec un doublage fini sont nécessaires pour comparer")
    dossier = voix[0].dossier
    racine = Path(racine) if racine else racine_de(dossier)
    ext = racine / EXTRAIT
    ext.mkdir(parents=True, exist_ok=True)
    ids = [r.voix for r in voix]
    par_id = {r.voix: r for r in voix}
    ancienne = chemins.lire_json(racine / "correspondance.json", {}) or {}
    lettres, graine = correspondance(ids, ancienne, graine)
    # les fichiers d'une ancienne série de lettres ne doivent pas rester
    for f in list(ext.iterdir()):
        if f.name != "original.mp3":
            f.unlink(missing_ok=True)
    for lettre, ident in lettres.items():
        r = par_id[ident]
        _lier(ext / f"{lettre}.mp3", Path(r.mp3).resolve())
        seule = r.dossier / "travail" / ident / "voix_convertie.flac"
        _en_mp3(seule, ext / f"{lettre}_voix.mp3", 128)
    _en_mp3(voix[0].dossier / "travail" / "original.wav", ext / "original.mp3")
    noms = {l: (par_id[i].voix_nom or i) for l, i in lettres.items()}
    chemins.ecrire_json(racine / "correspondance.json", {
        EXTRAIT: noms, "ids": {EXTRAIT: lettres}, "titres": {EXTRAIT: titre or voix[0].titre_court},
        "graine": graine, "voix": ids,
        "note": "comparaison de voix (doubler --comparer) ; les lettres sont tirées au hasard"})
    return racine
