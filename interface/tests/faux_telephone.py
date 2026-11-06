#!/usr/bin/env python3
"""Faux adb et faux scrcpy pour les essais de voixla.telephone : aucun téléphone, aucun réseau.
« faux_telephone.py adb … » ou « faux_telephone.py scrcpy … » ; test_telephone les installe dans un dossier bin/ sous
les noms adb et scrcpy (petits scripts sh qui font exec).

Variables d'environnement :
- FAUX_ADB_APPAREILS : lignes de « adb devices -l » après l'en-tête, séparées par « ; » (défaut : aucune) ;
- FAUX_ADB_CONNECT : « ok » (défaut) ou « echec », réponse de « adb connect » ;
- FAUX_SCRCPY : « ok » (défaut : un FLAC stéréo 48 kHz de la durée écoulée, écrit à la fin comme scrcpy, sur SIGINT
  ou à --time-limit), « vide » (fichier vide), « erreur » (« ERROR: Could not find any ADB device », code 1, aucun
  fichier) ou « tetu » (ignore SIGINT : il faut SIGKILL) ;
- FAUX_SCRCPY_ANCIEN : « 1 » : aide d'un scrcpy sans mic-voice-recognition ni --require-audio ; « vieux » : sans
  --no-window non plus ;
- FAUX_TELEPHONE_TRACE=<fichier> : une ligne JSON par lancement (outil, argv, variable ADB) et par SIGINT reçu.
"""

import json
import os
import signal
import subprocess
import sys
import time

AIDE = """Usage: scrcpy [options]

Options:

    --audio-codec=name
        Select an audio codec (opus, aac, flac or raw).

    --audio-source=source
        Select the audio source. Possible values are:
         - "output": forwards the whole audio output.
         - "mic": captures the microphone.
{sources}
    -N, --no-playback
        Disable video and audio playback on the computer.

    --no-video
        Disable video forwarding.

{fenetre}
    -r, --record=file.mp4
        Record screen to file.
{exige}
    -s, --serial=serial
        The device serial number.

    --time-limit=seconds
        Set the maximum mirroring time, in seconds.
"""


def tracer(**valeurs):
    chemin = os.environ.get("FAUX_TELEPHONE_TRACE")
    if chemin:
        with open(chemin, "a", encoding="utf-8") as f:
            f.write(json.dumps(valeurs, ensure_ascii=False) + "\n")


def adb(args):
    tracer(outil="adb", argv=args)
    if args[:1] == ["devices"]:
        print("List of devices attached")
        for ligne in (os.environ.get("FAUX_ADB_APPAREILS") or "").split(";"):
            if ligne.strip():
                print(ligne.strip())
        print()
        return 0
    if args[:1] == ["connect"] and len(args) > 1:
        if os.environ.get("FAUX_ADB_CONNECT", "ok") == "ok":
            print(f"connected to {args[1]}")
            return 0
        print(f"failed to connect to '{args[1]}': Connection refused")
        return 1
    print(f"adb: unknown command {' '.join(args)}", file=sys.stderr)
    return 1


def aide():
    ancien = os.environ.get("FAUX_SCRCPY_ANCIEN", "")
    sources = "" if ancien else ('         - "mic-voice-recognition": captures the microphone tuned for voice\n'
                                 "        recognition.\n")
    fenetre = "" if ancien == "vieux" else "    --no-window\n        Disable scrcpy window.\n"
    exige = "" if ancien else "\n    --require-audio\n        Make scrcpy fail if audio is enabled but does not work.\n"
    return AIDE.format(sources=sources, fenetre=fenetre, exige=exige)


def scrcpy(args):
    tracer(outil="scrcpy", argv=args, adb=os.environ.get("ADB"))
    if "--version" in args:
        print("scrcpy 5.0.1 <https://github.com/Genymobile/scrcpy>")
        return 0
    if "--help" in args or "-h" in args:
        print(aide())
        return 0
    mode = os.environ.get("FAUX_SCRCPY", "ok")
    valeurs = dict(a[2:].split("=", 1) for a in args if a.startswith("--") and "=" in a)
    fichier, limite = valeurs.get("record"), float(valeurs.get("time-limit", "5"))
    print("scrcpy 5.0.1 <https://github.com/Genymobile/scrcpy>")
    if mode == "erreur":
        print("ERROR: Could not find any ADB device", flush=True)
        print("ERROR: Server connection failed", flush=True)
        return 1
    arret = []

    def sur_sigint(*_):
        tracer(outil="scrcpy", signal="SIGINT")
        arret.append(True)

    if mode == "tetu":
        signal.signal(signal.SIGINT, lambda *_: tracer(outil="scrcpy", signal="SIGINT"))
    else:
        signal.signal(signal.SIGINT, sur_sigint)
    print(f"INFO: Recording started to flac file: {fichier}", flush=True)
    debut = time.monotonic()
    while time.monotonic() - debut < limite and not arret:
        time.sleep(0.05)
    duree = max(0.3, time.monotonic() - debut)
    if mode == "vide":
        open(fichier, "wb").close()
    else:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=330:sample_rate=48000:duration={duree:.2f}", "-ac", "2", "-c:a", "flac",
                        fichier], check=True, timeout=60)
    print(f"INFO: Recording complete to flac file: {fichier}", flush=True)
    return 0


if __name__ == "__main__":
    outil, reste = sys.argv[1], sys.argv[2:]
    sys.exit(adb(reste) if outil == "adb" else scrcpy(reste))
