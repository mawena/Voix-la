"""Préparation à l'avance des pistes séparées (sans GTK, testable) : beat seul, ma voix seule (combinée puis copiée sur
l'image), voix d'origine seule et miniature, pour que la première lecture d'une piste soit immédiate.

Ces préparations sont légères (une copie vidéo : l'image copiée telle quelle et un son AAC, quelques secondes ; la voix
combinée : un mixage de deux sons). Elles ne passent donc pas par file-attente, la file des travaux lourds du poste,
partagée avec d'autres sessions : elles tournent tout de suite, en tâche de fond, sous nice -n 19 et ionice -c3 (classe
« idle » : elles cèdent le processeur et le disque à tout le reste), une à la fois. Garde-fou : tant que la mémoire
disponible (MemAvailable) est sous SEUIL_MEMOIRE_MO, elles attendent. Seules les analyses du son de plus d'une minute
(voixla.analyse : spectre, courbes) gardent file-attente.

Quand :
- à la fin d'un doublage : l'exécuteur détaché (voixla/executeur.py) lit les événements « sortie » du travail et lance
  ce module en tâche de fond, pour les dossiers produits. Il tourne quoi qu'il arrive à la fenêtre : ouverte, fermée,
  ou application quittée ;
- à l'ouverture du détail d'un résultat : l'interface prépare ce qui manque, dans sa propre file (gui/analyses.py) ;
- au démarrage de l'interface : les MAX_RECENTS doublages les plus récents à qui il manque quelque chose, seulement
  quand le poste est calme (charge et mémoire, voir poste_calme).
Un seul préparateur à la fois (verrou), et chaque fichier du cache a son verrou (voixla.pistes) : deux préparations du
même fichier ne se font jamais en double.

En ligne de commande (lancée par lancer_en_fond) :
  python3 -m voixla.preparation [--si-calme] [--donnees DOSSIER] (--recents N | DOSSIER_DE_SORTIE…)
"""

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import chemins

SEUIL_MEMOIRE_MO = 700         # en dessous, une préparation attend (le poste a souvent 2 à 3 Go libres)
ATTENTE_MEMOIRE_S = 10
ATTENTE_MAX_S = 30 * 60        # au-delà, le préparateur en tâche de fond abandonne (il refera au prochain tour)
MAX_RECENTS = 5
CHARGE_CALME = 0.5             # charge moyenne sur une minute, par cœur
MEMOIRE_CALME_MO = 1500
SEUIL_FILE_S = 60.0            # analyses du son : au-delà, par file-attente
LEGERS = ("voixla.pistes", "voixla.paroles")     # modules des préparations légères (copies, voix, sous-titres…)


# --- Ressources du poste --------------------------------------------------------------------------------------------

def memoire_disponible_mo(texte=None):
    """MemAvailable de /proc/meminfo, en Mo (None si illisible)."""
    if texte is None:
        try:
            texte = Path("/proc/meminfo").read_text()
        except OSError:
            return None
    for ligne in texte.splitlines():
        if ligne.startswith("MemAvailable:"):
            try:
                return int(ligne.split()[1]) / 1024
            except (IndexError, ValueError):
                return None
    return None


def memoire_suffisante(dispo_mo, seuil_mo=SEUIL_MEMOIRE_MO):
    """Vrai s'il y a assez de mémoire pour une préparation (inconnue : on ne bloque pas)."""
    return dispo_mo is None or dispo_mo >= seuil_mo


def poste_calme(charge_1min, coeurs, dispo_mo, charge_max=CHARGE_CALME, memoire_min_mo=MEMOIRE_CALME_MO):
    """Le poste est-il assez calme pour préparer à l'avance ce que personne n'a encore demandé ?"""
    if dispo_mo is not None and dispo_mo < memoire_min_mo:
        return False
    return charge_1min is None or charge_1min / max(1, coeurs or 1) <= charge_max


def etat_du_poste():
    try:
        charge = os.getloadavg()[0]
    except OSError:
        charge = None
    return charge, os.cpu_count() or 1, memoire_disponible_mo()


# --- Commandes ------------------------------------------------------------------------------------------------------

def priorite_basse(commande, ionice=None):
    """nice -n 19 et, si ionice existe, classe d'entrées-sorties « idle »."""
    ionice = shutil.which("ionice") if ionice is None else ionice
    return ["nice", "-n", "19", *([ionice, "-c3"] if ionice else []), *commande]


def est_legere(module):
    return module in LEGERS


def commande(python, module, argv, duree_s=None, file_attente=None, ionice=None):
    """La commande d'une demande de gui/analyses.py : préparation légère tout de suite, en priorité basse ; analyse
    du son sous nice, par file-attente au-delà de SEUIL_FILE_S de son (ou durée inconnue)."""
    base = [python, "-m", module, *argv]
    if est_legere(module):
        return priorite_basse(base, ionice)
    cmd = ["nice", "-n", "10", *base]
    if file_attente and (duree_s is None or duree_s > SEUIL_FILE_S):
        cmd = [file_attente, "voix-la-analyse", "1", "2", "--", *cmd]
    return cmd


def lire_avancement(ligne):
    """{"avancement": 0.42, "etape": "video"} d'une ligne de sortie de voixla.pistes, sinon None."""
    ligne = (ligne or "").strip()
    if not ligne.startswith("{") or "avancement" not in ligne:
        return None
    try:
        d = json.loads(ligne)
        return max(0.0, min(1.0, float(d["avancement"]))), str(d.get("etape") or "")
    except (ValueError, KeyError, TypeError):
        return None


# --- File de l'interface : une demande à la fois, la piste cliquée d'abord ------------------------------------------

@dataclass
class Demande:
    cle: tuple
    argv: list
    module: str = "voixla.analyse"
    duree_s: float | None = None
    apres: list = field(default_factory=list)          # rappels (ok, sortie)
    avancement: list = field(default_factory=list)     # rappels (fraction, étape)


class FilePreparations:
    """Demandes en attente, sans doublon : une même demande (même clé) n'est lancée qu'une fois, ses rappels
    s'ajoutent. Une demande prioritaire (la piste qu'on vient de cliquer) passe devant. Une demande finie (réussie ou
    non) peut être redemandée : c'est le « Réessayer »."""

    def __init__(self):
        self.attente = []
        self.en_cours = None

    def _trouver(self, cle):
        if self.en_cours is not None and self.en_cours.cle == cle:
            return self.en_cours
        return next((d for d in self.attente if d.cle == cle), None)

    def ajouter(self, demande, prioritaire=False):
        """Vrai si c'est une nouvelle demande ; sinon ses rappels rejoignent celle qui existe (avancée si
        prioritaire)."""
        d = self._trouver(demande.cle)
        if d is not None:
            d.apres.extend(demande.apres)
            d.avancement.extend(demande.avancement)
            if prioritaire and d is not self.en_cours:
                self.attente.remove(d)
                self.attente.insert(0, d)
            return False
        if prioritaire:
            self.attente.insert(0, demande)
        else:
            self.attente.append(demande)
        return True

    def prochaine(self):
        if self.en_cours is not None or not self.attente:
            return None
        self.en_cours = self.attente.pop(0)
        return self.en_cours

    def finir(self):
        d, self.en_cours = self.en_cours, None
        return d

    def occupee(self):
        return self.en_cours is not None or bool(self.attente)


def peut_lancer(demande, dispo_mo, seuil_mo=SEUIL_MEMOIRE_MO):
    """Garde-fou mémoire des préparations légères (les analyses lourdes passent par file-attente, qui a le sien)."""
    return not est_legere(demande.module) or memoire_suffisante(dispo_mo, seuil_mo)


# --- Ce qu'il reste à préparer pour un doublage ---------------------------------------------------------------------

@dataclass
class Tache:
    nom: str                   # beat, ma_voix, voix_origine, miniature
    argv: list                 # arguments de python3 -m voixla.pistes


def taches(res):
    """Ce qui manque à ce doublage, dans l'ordre : beat seul, ma voix seule (combinée puis copiée sur l'image), voix
    d'origine seule, miniature. Sans vidéo doublée (MP3 seul) : seulement ma voix seule combinée."""
    from . import pistes
    p = pistes.choisir_pistes(res, "video")
    t = []
    # ma_voix_at : la voix seule de la version auto-tune (la vidéo auto-tune, elle, existe déjà)
    for cle in pistes.SANS_IMAGE_TOUTES:
        q = p.get(cle)
        if q is None:
            continue
        voix = cle in ("ma_voix", "ma_voix_at")
        at = ["--autotune"] if cle == "ma_voix_at" else []
        if q.video_a_preparer:
            if voix and res.dossier_voix is not None and q.fichier is None:
                t.append(Tache(cle, ["video", str(res.video), "--voix", str(res.dossier_voix), *at]))
            elif q.fichier is not None:
                t.append(Tache(cle, ["video", str(res.video), str(q.fichier)] if not voix else
                               ["video", str(res.video), "--voix", str(res.dossier_voix), *at]))
        elif voix and q.a_preparer and res.dossier_voix is not None:
            t.append(Tache(cle, ["voix", str(res.dossier_voix), *at]))
    if res.video is not None and pistes.miniature_prete(res.video) is None:
        t.append(Tache("miniature", ["miniature", str(res.video)]))
    return t


def recents(resultats, n=MAX_RECENTS):
    """Les n doublages complets les plus récents (resultats est déjà trié du plus récent au plus ancien)."""
    return [r for r in resultats if r.complet][:n]


def dossiers_des_sorties(evenements, demande=None):
    """Les dossiers de sortie d'un travail, d'après ses événements « sortie » (mp4, mp3 ou chemin écrits par doubler),
    sinon d'après les sorties prévues par la demande ; sans doublon, dans l'ordre."""
    vus = []
    for ev in evenements:
        if not isinstance(ev, dict) or ev.get("type") != "sortie":
            continue
        for cle in ("mp4", "mp3", "chemin", "fichier"):
            f = ev.get(cle)
            if f and str(Path(f).parent) not in vus:
                vus.append(str(Path(f).parent))
                break
    if not vus:
        for s in (demande or {}).get("sorties") or []:
            if s not in vus:
                vus.append(str(s))
    return [d for d in vus if Path(d, "travail").is_dir()]


def lire_evenements(chemin):
    sortie = []
    try:
        lignes = Path(chemin).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return sortie
    for l in lignes:
        try:
            sortie.append(json.loads(l))
        except ValueError:
            continue
    return sortie


def noms_des_voix(donnees):
    """{identifiant: nom affiché} d'après voix/*/voix.json (lecture directe, sans lancer doubler)."""
    noms = {}
    for fiche in sorted(chemins.dossier_voix(donnees).glob("*/voix.json")):
        nom = (chemins.lire_json(fiche, {}) or {}).get("nom")
        if nom:
            noms[fiche.parent.name] = nom
    return noms


# --- Lancement en tâche de fond ---------------------------------------------------------------------------------------

def fichier_journal():
    return chemins.dossier_etat() / "preparation.log"


def lancer_en_fond(args, env=None, popen=subprocess.Popen):
    """Lance python3 -m voixla.preparation args, détaché (sa propre session), en priorité basse ; sa sortie va dans
    ~/.local/state/voix-la/preparation.log. Ne l'attend pas."""
    env = dict(os.environ if env is None else env)
    env["PYTHONPATH"] = os.pathsep.join([str(chemins.INTERFACE)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH")
                                                                     else []))
    journal = fichier_journal()
    journal.parent.mkdir(parents=True, exist_ok=True)
    with open(journal, "ab") as j:
        return popen(priorite_basse([sys.executable, "-m", "voixla.preparation", *map(str, args)]),
                     stdin=subprocess.DEVNULL, stdout=j, stderr=subprocess.STDOUT, env=env, start_new_session=True,
                     cwd=str(chemins.INTERFACE))


# --- Le préparateur ---------------------------------------------------------------------------------------------------

def _dire(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def attendre(condition, delai_s=ATTENTE_MAX_S, pas_s=ATTENTE_MEMOIRE_S, dormir=time.sleep, horloge=time.monotonic):
    """Attend que condition() soit vraie ; faux au bout de delai_s."""
    fin = horloge() + delai_s
    while not condition():
        if horloge() >= fin:
            return False
        dormir(pas_s)
    return True


def preparer(resultats, si_calme=False, faire=None, etat=etat_du_poste, dormir=time.sleep):
    """Prépare ce qui manque, une tâche à la fois, en attendant la mémoire (et le calme si si_calme). faire(tache)
    exécute une tâche (par défaut : voixla.pistes.main). Rend (faites, échouées)."""
    from . import pistes
    faire = faire or (lambda t: pistes.main(t.argv) == 0)
    faites, echouees = 0, 0
    for r in resultats:
        for t in taches(r):
            def pret():
                charge, coeurs, dispo = etat()
                if si_calme:
                    return poste_calme(charge, coeurs, dispo)
                return memoire_suffisante(dispo)
            if not attendre(pret, dormir=dormir):
                _dire("poste occupé trop longtemps : on s'arrête là, on reprendra au prochain tour")
                return faites, echouees
            _dire(f"{r.titre_court} : {t.nom}")
            try:
                ok = faire(t)
            except Exception as e:  # noqa: BLE001 — une piste ratée n'arrête pas les autres
                _dire(f"{r.titre_court} : {t.nom} impossible : {e}")
                ok = False
            faites += bool(ok)
            echouees += not ok
    return faites, echouees


def main(argv=None):
    import argparse
    from . import rapports
    a = argparse.ArgumentParser(prog="python3 -m voixla.preparation", description=__doc__.split("\n\n")[0])
    a.add_argument("dossiers", nargs="*")
    a.add_argument("--recents", type=int, default=0)
    a.add_argument("--si-calme", action="store_true")
    a.add_argument("--donnees")
    o = a.parse_args(argv)
    donnees = Path(o.donnees) if o.donnees else chemins.dossier_donnees()
    verrous = chemins.dossier_cache() / "verrous"
    verrous.mkdir(parents=True, exist_ok=True)
    with open(verrous / "preparation.verrou", "a") as v:
        fcntl.flock(v, fcntl.LOCK_EX)        # un préparateur à la fois : les suivants attendent leur tour
        noms = noms_des_voix(donnees)
        if o.recents:
            rs = recents(rapports.lister_resultats(chemins.dossier_sorties(donnees), noms), o.recents)
        else:
            rs = [r for d in o.dossiers for r in rapports.lire_dossier(Path(d), noms)]
        faites, echouees = preparer(rs, si_calme=o.si_calme)
        _dire(f"fini : {faites} préparée(s), {echouees} impossible(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
