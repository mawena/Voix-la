"""Un duo inventé à corriger à la main (« Valse des lanternes - voix Jules et Marie ») : seize passages, dont un
refrain chanté par la chanteuse que doubler a mis dans la voix de Jules (passages 7 et 8), deux passages chantés
ensemble (voix principale et chœurs dans des voix différentes), les deux versions de chaque passage chanté
(versions/versions.json et rapport.versions), courbes.json avec voix_par_trame. Voix et titre inventés (jules, marie).
Le faux doubler (tests/faux_doubler.py) sait le corriger : doubler --remix --affectation …, --passage 7-8=marie.

  python3 -m tests.faux_duo DONNEES                  le duo, avec une petite vidéo de synthèse de 60 s (ffmpeg)
  python3 -m tests.faux_duo DONNEES --sans-videos    médias vides (pas d'ffmpeg)
  python3 -m tests.faux_duo DONNEES --sans-versions  comme un ancien doublage, sans les deux versions

Rien n'est écrit hors du dossier de données donné.
"""

import json
import sys
from pathlib import Path

TITRE = "Valse des lanternes"
VOIX = {"jules": ("Jules", "homme", 110.0), "marie": ("Marie", "femme", 210.0)}
DUREE = 60.0
# (début, fin, voix principale, chœurs) ; None : muet
PASSAGES = [(0.0, 2.0, None, None), (2.0, 6.0, "jules", None), (6.0, 10.2, "jules", None), (10.2, 14.0, "jules", None),
            (14.0, 14.8, None, None), (14.8, 18.9, "marie", None), (18.9, 22.7, "marie", None),
            (22.7, 26.5, "jules", None), (26.5, 30.4, "jules", None), (30.4, 31.2, None, None),
            (31.2, 35.0, "jules", None), (35.0, 39.1, "jules", None), (39.1, 43.0, "marie", None),
            (43.0, 47.2, "jules", "marie"), (47.2, 51.5, "marie", "jules"), (51.5, 55.8, "jules", None),
            (55.8, 60.0, "marie", None)]
NOMS = ["jules", "marie"]
CLASSE = {"jules": "homme", "marie": "femme"}


def morceaux():
    sortie = []
    for i, (a, b, lead, choeurs) in enumerate(PASSAGES):
        m = {"i": i, "debut_s": a, "fin_s": b, "muet": lead is None, "f0_med": None, "voise_s": 0.0, "decalage": 0,
             "base": "muet"}
        if lead:
            vp = {"lead_sec": lead, **({"choeurs_sec": choeurs} if choeurs else {})}
            m.update({"f0_med": 190.0 if lead == "jules" else 350.0, "voise_s": round(0.8 * (b - a), 2),
                      "voix": CLASSE[lead], "voix_cible": lead, "chanteur": CLASSE[lead], "voix_pistes": vp,
                      "base": f"duo, voix {CLASSE[lead]} → {lead}"})
        sortie.append(m)
    return sortie


def passages_de(ms):
    """Passages de la frise « Voix de chaque passage » d'après les morceaux (voix de toutes les pistes)."""
    par = []
    for m in ms:
        if m["muet"]:
            continue
        v = sorted({n for x in m["voix_pistes"].values() for n in (x if isinstance(x, list) else [x])}, key=NOMS.index)
        if par and par[-1]["voix"] == v and abs(par[-1]["fin_s"] - m["debut_s"]) < 0.05:
            par[-1]["fin_s"] = m["fin_s"]
        else:
            par.append({"debut_s": m["debut_s"], "fin_s": m["fin_s"], "voix": v})
    return par


def rapport(versions=True):
    ms = morceaux()
    par = passages_de(ms)
    sec = {n: round(sum(p["fin_s"] - p["debut_s"] for p in par if p["voix"] == [n]), 1) for n in NOMS}
    sec["ensemble"] = round(sum(p["fin_s"] - p["debut_s"] for p in par if len(p["voix"]) > 1), 1)
    chantes = [m["i"] for m in ms if not m["muet"]]
    r = {"mode": "chant", "mode_force": False, "duree_s": DUREE, "moteur": "yingmusic", "modele": "yingmusic",
         "nettoyage": {"demande": True, "applique": True}, "reglages": {"pas": 30},
         "variantes": {"voix_convertie.flac": {"modele": "yingmusic", "moteur": "yingmusic", "pas_diffusion": 30,
                                               "conversion_s": 80.0}},
         "voix_cibles": {"mode": "genre", "voix": [{"cle": CLASSE[n], "nom": n, "affichage": VOIX[n][0],
                                                    "centre_chant_hz": 2 * VOIX[n][2]} for n in NOMS]},
         "decision_hauteur": {"voix": 2, "axe": "genre", "message": "duo : homme → jules (+0), femme → marie (+0)",
                              "chanteurs": [{"nom": "homme", "classe": "homme", "voix": "jules", "decalage": 0},
                                            {"nom": "femme", "classe": "femme", "voix": "marie", "decalage": 0}]},
         "affectation": {"mode": "genre", "message": "duo", "voix_utilisees": NOMS, "passages": par,
                         "secondes_par_voix": sec, "morceaux_en_duo": 2},
         "morceaux": ms, "frise": [], "mix": {"version": 2, "voix_db": 4.0, "reverb_db": -13.0},
         "temps": {"separation_s": 15.0, "conversion_s": 80.0}}
    if versions:
        r["versions"] = {"mode": "toutes", "dossier": "versions", "voix_cibles": NOMS, "decalages": {n: 0 for n in NOMS},
                         "morceaux": chantes, "secondes": 52.0, "conversion_s": 80.0, "conversion_auto_s": 80.0,
                         "surcout": 1.0}
    return r


def courbes(ms):
    k = int(DUREE / 0.02)
    vt = []
    for j in range(k):
        t = j * 0.02
        p = next((p for p in passages_de(ms) if p["debut_s"] <= t < p["fin_s"]), None)
        vt.append(-1 if p is None else 2 if len(p["voix"]) > 1 else NOMS.index(p["voix"][0]))
    hz = [0 if v < 0 else 196.0 if v == 0 else 349.2 for v in vt]
    return {"format": 1, "pas_s": 0.02, "source_hz": hz, "convertie_hz": hz, "decalage_dt": [0] * k,
            "voix_cibles": NOMS, "voix_par_trame": vt}


def creer(donnees, videos=True, versions=True):
    """Le duo dans <données>/sorties/<titre>/, ses deux voix dans <données>/voix/, sa vidéo source dans
    <données>/Vidéos/ (le remix la retrouve). Rend le dossier de sortie."""
    donnees = Path(donnees)
    for ident, (nom, _classe, hauteur) in VOIX.items():
        d = donnees / "voix" / ident
        d.mkdir(parents=True, exist_ok=True)
        (d / "voix.json").write_text(json.dumps({"nom": nom, "reference": "reference.wav", "duree_s": 20.0,
                                                 "hauteur_parole_mediane_hz": hauteur}, ensure_ascii=False),
                                     encoding="utf-8")
        (d / "reference.wav").write_bytes(b"")
    source = donnees / "Vidéos" / f"{TITRE}.mp4"
    dossier = donnees / "sorties" / TITRE
    tv = dossier / "travail" / "jules+marie"
    tv.mkdir(parents=True, exist_ok=True)
    base = f"{TITRE} - voix Jules et Marie"
    if videos:
        from .fausse_bibliotheque import IMAGES, mp3_de_synthese, video_de_synthese
        video_de_synthese(source, IMAGES[4], DUREE)
        video_de_synthese(dossier / f"{base}.mp4", IMAGES[4], DUREE)
        mp3_de_synthese(dossier / f"{base}.mp3", None, DUREE)
    else:
        source.parent.mkdir(parents=True, exist_ok=True)
        for f in (source, dossier / f"{base}.mp4", dossier / f"{base}.mp3"):
            f.write_bytes(b"")
    t = dossier / "travail"
    (t / "source.json").write_text(json.dumps({"video": str(source), "decalage_son": 0.0}, ensure_ascii=False),
                                   encoding="utf-8")
    for f, hz in (("original.wav", 330), ("voix_source.wav", 262), ("instrumental.wav", 196)):
        if videos:   # des sons lisibles : le détail prépare le beat seul et la voix d'origine
            from .fausse_bibliotheque import nice_ffmpeg
            nice_ffmpeg("-f", "lavfi", "-i", f"sine=frequency={hz}:duration={DUREE}", "-c:a", "pcm_s16le", t / f)
        else:
            (t / f).write_bytes(b"")
    (tv / "voix_convertie.flac").write_bytes(b"")
    r = rapport(versions)
    (tv / "rapport.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    (tv / "courbes.json").write_text(json.dumps(courbes(r["morceaux"])), encoding="utf-8")
    if versions:
        (tv / "versions").mkdir(exist_ok=True)
        (tv / "versions" / "versions.json").write_text(json.dumps(
            {"format": 1, "mode": "toutes", "voix_cibles": NOMS, "morceaux": r["versions"]["morceaux"], "faux": True}),
            encoding="utf-8")
    return dossier


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    d = creer(argv[0], videos="--sans-videos" not in argv, versions="--sans-versions" not in argv)
    print(json.dumps({"dossier": str(d.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
