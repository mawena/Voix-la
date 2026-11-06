"""Une fausse bibliothèque de doublages, aux titres et aux voix inventés : pour les essais, les captures d'écran et la
mesure des performances de la page Résultats. Rien n'est écrit hors du dossier de données donné.

  python3 -m tests.fausse_bibliotheque DONNEES               les 12 doublages des captures (petites vidéos de synthèse)
  python3 -m tests.fausse_bibliotheque DONNEES --n 200       200 doublages (20 vidéos de synthèse, liées en dur)
  … --sans-videos                                            médias vides (pas d'ffmpeg ; pochettes générées)
"""

import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

VOIX = {"jules": "Jules", "marie": "Marie", "nora": "Nora", "sacha": "Sacha"}

# Images des vidéos de synthèse (ffmpeg -f lavfi) : des dégradés et des fractales, pour des miniatures variées
IMAGES = ["gradients=s=640x360:c0=0x1c71d8:c1=0xe66100:c2=0x9141ac:n=3:speed=0.02:r=10",
          "mandelbrot=s=640x360:r=10",
          "gradients=s=640x360:c0=0x26a269:c1=0xf6d32d:c2=0x1a5fb4:n=3:speed=0.02:r=10:seed=3",
          "life=s=320x180:mold=10:r=10:ratio=0.1:death_color=#c01c28:life_color=#f6d32d,scale=640:360",
          "gradients=s=640x360:c0=0xc01c28:c1=0x613583:c2=0xffbe6f:n=3:speed=0.02:r=10:seed=8",
          "cellauto=s=320x180:rule=110:r=10,scale=640:360,colorchannelmixer=rr=0.2:gg=0.6:bb=0.9",
          "gradients=s=640x360:c0=0x2190a4:c1=0x8ff0a4:n=2:speed=0.02:r=10:seed=5",
          "testsrc2=s=640x360:r=10",
          "gradients=s=640x360:c0=0x3d3846:c1=0xdc8add:c2=0x99c1f1:n=3:speed=0.02:r=10:seed=11",
          "gradients=s=640x360:c0=0x865e3c:c1=0xf9f06b:n=2:speed=0.02:r=10:seed=2"]

# Les doublages des captures : (titre, voix, mode, durée, options)
CAPTURES = [
    ("Lumière d’été", "jules", "chant", 214.0, {"langue": "es", "image": 0, "jours": 0.1}),
    ("Lumière d’été", "marie", "chant", 214.0, {"langue": "es", "autotune": True, "image": 0, "jours": 0.2}),
    ("Les vagues bleues", "nora", "chant", 40.0, {"langue": "en", "extrait": (62.0, 40.0), "image": 2, "jours": 1.2}),
    ("Minuit à Lagos", "jules+marie", "chant", 248.0, {"langue": "yo", "image": 4, "jours": 2.5}),
    ("Récit du phare", "sacha", "parole", 131.0, {"langue": "fr", "image": 6, "jours": 3.1}),
    ("Danse des lucioles", "marie", "chant", 187.0, {"mp3": True, "pochette": 1, "langue": "pt", "jours": 4.0}),
    ("Chanson sans image", "nora", "chant", 165.0, {"mp3": True, "jours": 5.4}),
    ("Horizon de cuivre", "jules", "chant", 229.0, {"langue": "en", "image": 9, "jours": 6.0}),
    ("Brume sur la rivière", "marie", "chant", 201.0, {"autotune": True, "langue": "fr", "image": 8, "jours": 8.0}),
    ("Étoile filante", "sacha", "chant", None, {"inacheve": True, "jours": 9.0}),
    ("Le jardin des heures", "nora", "chant", 30.0, {"extrait": (45.0, 30.0), "langue": "it", "image": 3,
                                                       "source": "Le jardin des heures", "jours": 11.0}),
    ("Le jardin des heures", "sacha", "chant", 196.0, {"langue": "it", "image": 3, "source": "Le jardin des heures",
                                                        "jours": 12.0, "dossier": "essais/Le jardin des heures"}),
    ("Ciel de craie", "jules", "chant", 176.0, {"langue": "en", "image": 5, "jours": 15.0}),
    ("Rêve en sépia", "marie", "chant", 25.0, {"extrait": (90.0, 25.0), "langue": "es", "image": 7, "jours": 20.0}),
]

MOTS_A = ["Lumière", "Rivière", "Nuit", "Soleil", "Vague", "Étoile", "Ciel", "Jardin", "Horizon", "Brume", "Rêve",
          "Danse", "Chemin", "Pluie", "Lune", "Marée", "Forêt", "Silence", "Écho", "Aube"]
MOTS_B = ["d’été", "bleue", "de minuit", "lointain", "d’argent", "sans fin", "de sable", "en sépia", "du nord",
          "de verre", "à Lagos", "de cuivre", "des heures", "d’automne", "secrète", "du matin"]


def nice_ffmpeg(*args):
    subprocess.run(["nice", "-n", "19", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    *map(str, args)], check=True, stdin=subprocess.DEVNULL, timeout=120)


def video_de_synthese(chemin, image, duree=2.0):
    """Une petite vidéo (640 × 360, 10 i/s, 5 au-delà de 5 s) avec un son doux, pour que ffprobe et la miniature
    aient de quoi faire."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    if duree > 5:
        image = image.replace(":r=10", ":r=5")
    nice_ffmpeg("-f", "lavfi", "-i", f"{image},trim=duration={duree}", "-f", "lavfi", "-i",
                f"sine=frequency=330:duration={duree}", "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-shortest", chemin)
    return chemin


def mp3_de_synthese(chemin, pochette=None, duree=2.0):
    """Un MP3 court, avec une pochette intégrée (image de synthèse) si pochette est un index d'IMAGES."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    entrees = ["-f", "lavfi", "-i", f"sine=frequency=262:duration={duree}"]
    if pochette is None:
        nice_ffmpeg(*entrees, "-c:a", "libmp3lame", "-b:a", "64k", chemin)
        return chemin
    png = chemin.with_suffix(".pochette.png")
    nice_ffmpeg("-f", "lavfi", "-i", IMAGES[pochette].replace("640x360", "500x500").replace("640:360", "500:500"),
                "-frames:v", "1", png)
    nice_ffmpeg(*entrees, "-i", png, "-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame", "-b:a", "64k", "-c:v",
                "png", "-disposition:v", "attached_pic", chemin)
    png.unlink()
    return chemin


def rapport(mode, duree, voix, options, morceaux=40):
    r = {"mode": mode, "duree_s": duree, "moteur": "yingmusic", "modele": "yingmusic",
         "nettoyage": {"applique": mode == "chant", "demande": True},
         "morceaux": [{"debut_s": i * 4.0, "fin_s": i * 4.0 + 4.0, "voix": "homme" if i % 3 else "femme",
                       "decalage": 0.0, "f0_med": 180.0 + i % 7} for i in range(morceaux)]}
    if options.get("langue"):
        r["paroles_sorties"] = {"langue": options["langue"], "lignes": 30,
                                "langue_chantee": {"langue": options["langue"], "secondes_par_langue": {}}}
    if options.get("autotune"):
        r["autotune"] = {"applique": True, "intensite": 70, "gamme": {"type": "tonalite", "tonalite": "la mineur"},
                         "fichiers": {}}
    if "+" in voix:
        a, b = voix.split("+")
        r["voix_cibles"] = {"voix": [{"cle": "homme", "nom": a}, {"cle": "femme", "nom": b}]}
    return r


def nom_voix(voix):
    return " et ".join(VOIX.get(m, m.capitalize()) for m in voix.split("+"))


def doublage(donnees, titre, voix, mode, duree, options, maintenant, videos=True, sources=None, morceaux=40,
             duree_media=30.0):
    """Un dossier de sortie (ou une voix de plus dans un dossier existant). duree_media : la durée des médias de
    synthèse (assez pour lire pendant une capture) ; avec sources (vidéos liées en dur), les MP3 restent vides."""
    nom_dossier = options.get("dossier") or titre
    if options.get("extrait"):
        d, u = options["extrait"]
        nom_dossier += f" (extrait {int(d // 60)}m{d % 60:04.1f}+{int(u // 60)}m{u % 60:04.1f})".replace(".", ",")
    dossier = donnees / "sorties" / nom_dossier
    travail = dossier / "travail"
    (travail / voix).mkdir(parents=True, exist_ok=True)
    source = donnees / "Vidéos" / f"{options.get('source') or titre}.mp4"
    debut = (options.get("extrait") or (0.0, 0.0))[0]
    (travail / "source.json").write_text(json.dumps({"video": str(source), "debut": debut, "duree": duree},
                                                    ensure_ascii=False), encoding="utf-8")
    base = f"{titre} - voix {nom_voix(voix)}"
    date = maintenant - options.get("jours", 0) * 86400
    if not options.get("inacheve"):
        (travail / voix / "rapport.json").write_text(json.dumps(rapport(mode, duree, voix, options, morceaux),
                                                                ensure_ascii=False), encoding="utf-8")
        mp3 = dossier / f"{base}.mp3"
        if videos and options.get("mp3"):
            mp3_de_synthese(mp3, options.get("pochette"), duree_media if sources is None else 2.0)
        elif videos and sources is not None and "image" in options:
            os.link(sources[options["image"] % len(sources)], dossier / f"{base}.mp4")
        elif videos and "image" in options:
            video_de_synthese(dossier / f"{base}.mp4", IMAGES[options["image"] % len(IMAGES)], duree_media)
            mp3_de_synthese(mp3, None, duree_media)
        elif not options.get("mp3"):
            (dossier / f"{base}.mp4").write_bytes(b"")
        if not mp3.exists():
            mp3.write_bytes(b"")
        if options.get("autotune"):
            (dossier / f"{base} (auto-tune).mp3").write_bytes(b"")
        for f in dossier.iterdir():
            if f.is_file():
                os.utime(f, (date, date))
        os.utime(travail / voix / "rapport.json", (date, date))
    os.utime(dossier, (date, date))
    return dossier


def creer(donnees, n=None, videos=True, graine=7):
    """Les doublages des captures (n None), ou n doublages inventés (versions de chansons, extraits, duos, parole,
    MP3 seuls). Rend le nombre de doublages."""
    donnees = Path(donnees)
    (donnees / "sorties").mkdir(parents=True, exist_ok=True)
    (donnees / "voix").mkdir(parents=True, exist_ok=True)
    for ident, nom in VOIX.items():
        d = donnees / "voix" / ident
        d.mkdir(exist_ok=True)
        (d / "voix.json").write_text(json.dumps({"nom": nom, "reference": "reference.wav", "duree_s": 20.0},
                                                ensure_ascii=False), encoding="utf-8")
    maintenant = time.time()
    if n is None:
        for titre, voix, mode, duree, options in CAPTURES:
            doublage(donnees, titre, voix, mode, duree, options, maintenant, videos)
        return len(CAPTURES)
    hasard = random.Random(graine)
    sources = None
    if videos:
        reserve = donnees / ".videos-de-synthese"
        sources = [video_de_synthese(reserve / f"v{i:02d}.mp4", IMAGES[i % len(IMAGES)]) for i in range(20)]
    faits, i = 0, 0
    while faits < n:
        titre = f"{hasard.choice(MOTS_A)} {hasard.choice(MOTS_B)} {i + 1}"
        versions = hasard.choice([1, 1, 1, 2, 2, 3])
        voix = hasard.sample(list(VOIX), versions)
        for k, v in enumerate(voix[:n - faits]):
            options = {"jours": hasard.uniform(0, 300), "image": hasard.randrange(20), "langue":
                       hasard.choice(["", "en", "es", "fr", "pt", "it"])}
            if hasard.random() < 0.15:
                options["extrait"] = (hasard.choice([30.0, 60.0, 90.0]), 30.0)
            if hasard.random() < 0.2:
                options["autotune"] = True
            if hasard.random() < 0.1:
                options.pop("image")
                options["mp3"] = True
            if hasard.random() < 0.08 and len(voix) == 1:
                v = hasard.choice(["jules+marie", "nora+sacha"])
            mode = "parole" if hasard.random() < 0.12 else "chant"
            duree = hasard.uniform(120, 300) if not options.get("extrait") else 30.0
            doublage(donnees, titre, v, mode, round(duree, 1), options, maintenant, videos, sources, morceaux=300)
            faits += 1
        i += 1
    if sources:
        shutil.rmtree(donnees / ".videos-de-synthese")      # les liens en dur gardent les fichiers
    return faits


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    n = int(argv[argv.index("--n") + 1]) if "--n" in argv else None
    faits = creer(argv[0], n, videos="--sans-videos" not in argv)
    print(json.dumps({"doublages": faits, "donnees": str(Path(argv[0]).resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
