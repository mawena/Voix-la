"""Un doublage inventé pour essayer l'export pour les réseaux (voixla.export_social) : une vidéo doublée de synthèse à
cinq pistes audio (Doublage, Original, Beat seul, Voix convertie seule, Voix d'origine seule), son MP3, sa version
auto-tune, travail/ (source.json, instrumental.wav, <voix>/rapport.json, paroles.json, courbes.json). Titre, voix et
paroles inventés ; les paroles ont deux couplets et un refrain chanté deux fois, minutés mot à mot, avec leur
traduction française.

Chaque piste est un son pur de fréquence connue (FREQUENCES) : un essai retrouve la piste exportée par sa fréquence.
Rien n'est écrit hors du dossier de données donné.

  python3 -m tests.faux_partage DONNEES [--duree 75] [--taille 1280x720] [--sans-autotune]
"""

import json
import subprocess
import sys
from pathlib import Path

TITRE = "Lumière du soir"
VOIX = ("jules", "Jules")
FREQUENCES = {"doublage": 440.0, "original": 550.0, "beat": 330.0, "ma_voix": 660.0, "voix_origine": 770.0,
              "doublage_at": 495.0}
TITRES_PISTES = ["Doublage", "Original", "Beat seul", "Voix convertie seule", "Voix d'origine seule"]

COUPLET_1 = [("Las calles duermen bajo la lluvia", "Les rues dorment sous la pluie"),
             ("Y tu recuerdo baila conmigo", "Et ton souvenir danse avec moi"),
             ("Cuento las horas hasta el alba", "Je compte les heures jusqu’à l’aube"),
             ("Mi voz te busca en el silencio", "Ma voix te cherche dans le silence")]
COUPLET_2 = [("Los faroles tiemblan en el puerto", "Les lanternes tremblent sur le port"),
             ("El mar escribe tu nombre en la arena", "La mer écrit ton nom sur le sable"),
             ("Guardo el verano dentro del pecho", "Je garde l’été au fond de la poitrine"),
             ("Y el viento sopla sin preguntar", "Et le vent souffle sans demander")]
REFRAIN = [("Lumbre del alba, quédate aquí", "Lueur de l’aube, reste ici"),
           ("Canta conmigo hasta el final", "Chante avec moi jusqu’à la fin"),
           ("Lumbre del alba, no te vayas", "Lueur de l’aube, ne t’en va pas"),
           ("Que la noche nos vuelva a encontrar", "Que la nuit nous retrouve encore")]
# (début du bloc, lignes) : intro, couplet, refrain, couplet, refrain
STRUCTURE = ((4.0, COUPLET_1), (19.0, REFRAIN), (36.0, COUPLET_2), (51.0, REFRAIN))
PAS_LIGNE = 3.6
ECART_LIGNE = 0.4


def paroles(duree=75.0, decalage_s=0.0):
    """paroles.json (format 1) : chaque ligne dure PAS_LIGNE − ECART_LIGNE, ses mots se partagent ce temps selon leur
    longueur ; temps de la vidéo doublée (temps du son + decalage_s)."""
    lignes, traductions = [], []
    for debut, bloc in STRUCTURE:
        for k, (texte, fr) in enumerate(bloc):
            t = debut + k * PAS_LIGNE + decalage_s
            fin = t + PAS_LIGNE - ECART_LIGNE
            if fin > duree + decalage_s:
                break
            mots, total, u = [], sum(len(m) + 1 for m in texte.split()), t
            for m in texte.split():
                d = (fin - t) * (len(m) + 1) / total
                mots.append({"debut": round(u, 3), "fin": round(u + d * 0.92, 3), "mot": m.strip(",")})
                u += d
            lignes.append({"debut": round(t, 3), "fin": round(fin, 3), "texte": texte, "langue": "es", "mots": mots})
            traductions.append({"debut": round(t, 3), "fin": round(fin, 3), "texte": fr})
    return {"format": 1, "langue": "es", "source": "transcription d’essai", "decalage_s": decalage_s,
            "piste": "lead_sec", "segments": lignes,
            "traductions": {"fr": {"source": "traduction d’essai", "segments": traductions}}}


def courbes(duree=75.0, pas_s=0.02):
    """courbes.json : la voix convertie chante pendant les lignes (220 Hz), se tait ailleurs."""
    p = paroles(duree)
    n = int(duree / pas_s)
    hz = [0.0] * n
    for l in p["segments"]:
        for i in range(int(l["debut"] / pas_s), min(n, int(l["fin"] / pas_s))):
            hz[i] = 220.0
    return {"format": 1, "pas_s": pas_s, "source_hz": hz, "convertie_hz": hz, "decalage_dt": [0] * n}


def ffmpeg(*args, timeout=600):
    subprocess.run(["nice", "-n", "19", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    *map(str, args)], check=True, stdin=subprocess.DEVNULL, timeout=timeout)


def _sons(duree, frequences):
    entrees = []
    for f in frequences:
        entrees += ["-f", "lavfi", "-i", f"sine=frequency={f:g}:sample_rate=44100:duration={duree:g}"]
    return entrees


def video_doublee(chemin, duree, taille, frequences, image=None):
    """La vidéo doublée : image de synthèse (dégradés qui bougent ; ou l'image de la vidéo « image », copiée) et cinq
    pistes audio titrées comme celles de doubler."""
    l, h = taille
    if image is None:
        entree_image = ["-f", "lavfi", "-i", f"gradients=s={l}x{h}:c0=0x1c71d8:c1=0xe66100:c2=0x9141ac:c3=0x26a269"
                                            f":n=4:speed=0.015:r=25:duration={duree:g}"]
        codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p", "-g", "50"]
    else:
        entree_image, codec = ["-i", str(image)], ["-c:v", "copy"]
    cartes = ["-map", "0:v:0"]
    meta = []
    for i, t in enumerate(TITRES_PISTES):
        cartes += ["-map", f"{i + 1}:a"]
        meta += [f"-metadata:s:a:{i}", f"title={t}", f"-metadata:s:a:{i}", f"handler_name={t}",
                 f"-metadata:s:a:{i}", "language=spa"]
    ffmpeg(*entree_image, *_sons(duree, frequences), *cartes, *codec, "-c:a", "aac", "-b:a", "96k", "-ac", "2",
           *meta, "-disposition:a", "0", "-disposition:a:0", "default", "-t", f"{duree:g}", chemin)
    return chemin


def creer(donnees, duree=75.0, taille=(1280, 720), autotune=True, mp3=True, titre=TITRE, voix=VOIX):
    """Le doublage inventé dans <donnees>/sorties/<titre>/ ; rend le dossier."""
    donnees = Path(donnees)
    d = donnees / "sorties" / titre
    t = d / "travail"
    tv = t / voix[0]
    tv.mkdir(parents=True, exist_ok=True)
    base = f"{titre} - voix {voix[1]}"
    video = video_doublee(d / f"{base}.mp4", duree, taille,
                          [FREQUENCES[c] for c in ("doublage", "original", "beat", "ma_voix", "voix_origine")])
    if mp3:
        ffmpeg("-f", "lavfi", "-i", f"sine=frequency={FREQUENCES['doublage']:g}:sample_rate=44100:duration={duree:g}",
               "-c:a", "libmp3lame", "-b:a", "128k", d / f"{base}.mp3")
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency={FREQUENCES['beat']:g}:sample_rate=44100:duration={duree:g}",
           "-c:a", "pcm_s16le", t / "instrumental.wav")
    rapport = {"mode": "chant", "duree_s": duree, "moteur": "yingmusic",
               "pistes_audio": {"reglage": "toutes", "video": TITRES_PISTES},
               "paroles_sorties": {"langue": "es"}}
    if autotune:
        video_doublee(d / f"{base} (auto-tune).mp4", duree, taille,
                      [FREQUENCES[c] for c in ("doublage_at", "original", "beat", "ma_voix", "voix_origine")],
                      image=video)
        rapport["autotune"] = {"applique": True, "intensite": 70,
                               "gamme": {"type": "tonalite", "tonalite": "la mineur"},
                               "fichiers": {"video": f"{base} (auto-tune).mp4"}}
        rapport["pistes_audio"]["video_autotune"] = TITRES_PISTES
    source = donnees / "Vidéos" / f"{titre}.mp4"
    (t / "source.json").write_text(json.dumps({"video": str(source), "debut": 0, "duree": duree,
                                               "decalage_son": 0.0}, ensure_ascii=False), encoding="utf-8")
    (tv / "rapport.json").write_text(json.dumps(rapport, ensure_ascii=False), encoding="utf-8")
    (tv / "paroles.json").write_text(json.dumps(paroles(duree), ensure_ascii=False), encoding="utf-8")
    (tv / "courbes.json").write_text(json.dumps(courbes(duree)), encoding="utf-8")
    voix_d = donnees / "voix" / voix[0]
    voix_d.mkdir(parents=True, exist_ok=True)
    (voix_d / "voix.json").write_text(json.dumps({"nom": voix[1], "reference": "reference.wav", "duree_s": 20.0,
                                                  "consentement": "voix d'essai"}, ensure_ascii=False),
                                      encoding="utf-8")
    return d


def main(argv=None):
    import argparse
    a = argparse.ArgumentParser(prog="python3 -m tests.faux_partage")
    a.add_argument("donnees")
    a.add_argument("--duree", type=float, default=75.0)
    a.add_argument("--taille", default="1280x720")
    a.add_argument("--sans-autotune", action="store_true")
    args = a.parse_args(argv)
    l, h = (int(x) for x in args.taille.lower().split("x"))
    print(creer(args.donnees, args.duree, (l, h), autotune=not args.sans_autotune))
    return 0


if __name__ == "__main__":
    sys.exit(main())
