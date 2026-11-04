"""Exécuteur détaché de la file des travaux : python3 -m voixla.executeur <dossier des travaux>.

Lancé par l'interface dans sa propre session (start_new_session) : il survit à la fermeture de la fenêtre. Il prend
les travaux en attente un par un (un seul doubler à la fois), lance chacun dans son propre groupe de processus, écrit
etat.json, et tue le groupe si la marque « annuler » apparaît. Il s'arrête quand la file est vide. Un verrou flock
(executeur.verrou) garantit qu'il n'en tourne qu'un. Si la fenêtre est fermée (verrou interface.verrou libre), il
prévient par une notification du bureau (notify-send) à la fin de chaque travail. À la fin de chaque travail, il lance
aussi la préparation des pistes séparées des doublages produits (voixla/preparation.py), en tâche de fond.
"""

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import chemins
from .travaux import demarrage_processus, verrou_tenu

DELAI_SIGKILL = 8.0


def journal(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def ecrire_etat(dossier, etat):
    chemins.ecrire_json(Path(dossier) / "etat.json", etat)


def prochain(racine):
    """Le plus ancien travail ni commencé ni annulé ; les travaux annulés avant d'avoir commencé sont clos ici."""
    for d in sorted(Path(racine).iterdir()):
        if not d.is_dir() or not (d / "demande.json").exists() or (d / "etat.json").exists():
            continue
        if (d / "annuler").exists():
            ecrire_etat(d, {"statut": "annule", "fin": time.time(), "avant_debut": True})
            continue
        return d
    return None


def notifier(racine, titre, corps):
    if verrou_tenu(Path(racine) / "interface.verrou"):
        return   # la fenêtre est ouverte : c'est elle qui notifie
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "--app-name=Voix-là", "--icon=io.mawena.VoixLa", titre, corps],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)


def executer(racine, d):
    demande = json.loads((d / "demande.json").read_text(encoding="utf-8"))
    env = {**os.environ, **(demande.get("env") or {}), "PYTHONUNBUFFERED": "1"}
    # PYTHONPATH de l'exécuteur (dossier interface/) : rendu tel qu'il était avant, doubler n'en a pas besoin
    origine = env.pop("VOIXLA_PYTHONPATH_ORIGINE", "")
    if origine:
        env["PYTHONPATH"] = origine
    else:
        env.pop("PYTHONPATH", None)
    journal(f"début {d.name} : {' '.join(demande['argv'][:4])}…")
    with open(d / "journal.txt", "ab") as j:
        try:
            p = subprocess.Popen(demande["argv"], stdout=j, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 cwd=demande.get("cwd") or str(Path.home()), env=env, start_new_session=True)
        except OSError as e:
            j.write(f"impossible de lancer doubler : {e}\n".encode())
            ecrire_etat(d, {"statut": "echec", "code": 127, "debut": time.time(), "fin": time.time(), "message": str(e)})
            return
    debut = time.time()
    etat = {"statut": "en_cours", "pid": p.pid, "pgid": p.pid, "demarrage": demarrage_processus(p.pid),
            "debut": debut, "executeur": os.getpid(), "executeur_demarrage": demarrage_processus(os.getpid())}
    ecrire_etat(d, etat)
    annule, t_signal, tue = False, None, False
    while True:
        try:
            code = p.wait(timeout=1)
            break
        except subprocess.TimeoutExpired:
            pass
        if not annule and (d / "annuler").exists():
            annule, t_signal = True, time.time()
            journal(f"annulation de {d.name} : SIGTERM au groupe {p.pid}")
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if annule and not tue and time.time() - t_signal > DELAI_SIGKILL:
            tue = True
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if annule:
        try:   # ce qui resterait du groupe (ffmpeg, kaggle…)
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    statut = "annule" if annule else ("fini" if code == 0 else "echec")
    ecrire_etat(d, {**etat, "statut": statut, "code": code, "fin": time.time()})
    journal(f"fin {d.name} : {statut} (code {code}, {time.time() - debut:.0f} s)")
    titre = demande.get("titre") or d.name
    if statut in ("fini", "echec"):
        preparer_apres(d, demande)
    if statut == "fini":
        notifier(racine, "Doublage fini", titre)
    elif statut == "echec":
        notifier(racine, "Doublage en échec", titre)


def preparer_apres(d, demande, lancer=None):
    """Les doublages produits (événements « sortie » du travail) : leurs pistes séparées se préparent tout de suite,
    en tâche de fond et en priorité basse (voixla/preparation.py), que la fenêtre soit ouverte ou non. C'est
    l'exécuteur qui s'en charge : il est seul à voir chaque fin de travail, et il tourne même sans l'interface. Il
    n'attend pas : le travail suivant de la file part aussitôt. Même après un échec, les vidéos déjà finies d'un lot
    sont préparées. Rend les dossiers concernés."""
    from . import preparation
    if os.environ.get("VOIXLA_SANS_PREPARATION"):
        return []
    ev = preparation.lire_evenements(demande.get("evenements") or d / "evenements.jsonl") \
        if demande.get("evenements") else []
    dossiers = preparation.dossiers_des_sorties(ev, demande)
    if dossiers:
        journal(f"préparation des pistes en tâche de fond : {len(dossiers)} dossier(s)")
        args = (["--donnees", demande["donnees"]] if demande.get("donnees") else []) + dossiers
        try:
            (lancer or preparation.lancer_en_fond)(args)
        except OSError as e:
            journal(f"préparation impossible : {e}")
    return dossiers


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    racine = Path(argv[0]) if argv else chemins.dossier_etat() / "travaux"
    racine.mkdir(parents=True, exist_ok=True)
    verrou = open(racine / "executeur.verrou", "a")
    try:
        fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0   # un autre exécuteur tient la file
    journal(f"exécuteur {os.getpid()} : {racine}")
    while True:
        d = prochain(racine)
        if d is None:
            # file vide : on relâche, puis on regarde une dernière fois (un travail ajouté juste avant le relâchement
            # serait sinon oublié : l'interface a vu le verrou pris et n'a pas lancé d'exécuteur)
            fcntl.flock(verrou, fcntl.LOCK_UN)
            if prochain(racine) is None:
                journal("file vide, arrêt")
                return 0
            try:
                fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            continue
        try:
            executer(racine, d)
        except Exception as e:  # noqa: BLE001 — un travail cassé ne doit pas bloquer la file
            journal(f"erreur sur {d.name} : {e}")
            if not (d / "etat.json").exists() or json.loads((d / "etat.json").read_text()).get("statut") == "en_cours":
                ecrire_etat(d, {"statut": "echec", "code": 1, "fin": time.time(), "message": str(e)})


if __name__ == "__main__":
    sys.exit(main())
