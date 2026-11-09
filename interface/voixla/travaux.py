"""File des travaux doubler, côté interface (sans GTK, testable).

Un travail = un dossier ~/.local/state/voix-la/travaux/<id>/ :
- demande.json : écrit une fois par l'interface (commande, vidéos, réglages, estimations) ;
- etat.json    : écrit seulement par l'exécuteur (en_cours, fini, echec, annule ; pid, groupe, code) ;
- annuler      : marque posée par l'interface pour demander l'arrêt ;
- lancer       : marque posée par l'interface pour lancer tout de suite un travail en attente du quota GPU ;
- journal.txt  : sortie complète de doubler ; evenements.jsonl : ses événements (--evenements).
L'exécuteur (executeur.py) tourne détaché de la fenêtre et lance les travaux un par un : un seul doubler à la fois,
et les travaux continuent si la fenêtre se ferme. Chaque fichier n'a qu'un seul écrivain : pas de course.

Attente du quota GPU (voir quota.py) : une demande qui porte « attendre_jusqu_a » (epoch, la remise à zéro du quota)
reste en attente jusqu'à cette heure-là, ou jusqu'à la marque « lancer ». L'exécuteur veille en attendant, fenêtre
fermée comprise, et les travaux qui tiennent dans le quota passent devant.
"""

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import chemins, moteur, suivi

TERMINES = ("fini", "echec", "annule", "interrompu")
REFUS_SESSION = re.compile(r"session Kaggle pr[ée]c[ée]dente n.est pas finie")   # message de doubler.py
SEUIL_ATTENTE_MINI_S = 10 * 60    # en dessous, une attente « queued » n'a rien d'anormal
SEUIL_ATTENTE_MAXI_S = 30 * 60    # au-delà, elle l'est toujours, quelle que soit l'estimation
LANCER = "lancer"   # marque : lancer maintenant un travail en attente du quota


def heure_de_reprise(dossier, demande=None):
    """Heure (epoch) jusqu'à laquelle un travail pas encore commencé attend la remise à zéro du quota GPU, ou None
    (pas en attente du quota, ou marque « lancer » posée)."""
    dossier = Path(dossier)
    demande = demande if demande is not None else (chemins.lire_json(dossier / "demande.json", {}) or {})
    r = demande.get("attendre_jusqu_a") if isinstance(demande, dict) else None
    if not r or (dossier / LANCER).exists():
        return None
    try:
        return float(r)
    except (TypeError, ValueError):
        return None


def demarrage_processus(pid):
    """Date de démarrage d'un processus (/proc/<pid>/stat, champ 22) : distingue un processus d'un autre qui aurait
    repris son numéro."""
    try:
        s = Path(f"/proc/{int(pid)}/stat").read_text()
        return s[s.rindex(") ") + 2:].split(" ")[19]
    except (OSError, ValueError, IndexError):
        return None


def vivant(pid, demarrage=None):
    if not pid:
        return False
    d = demarrage_processus(pid)
    return d is not None and (demarrage is None or d == str(demarrage))


def verrou_tenu(chemin):
    """Vrai si un autre processus tient le verrou flock de ce fichier (essai non bloquant, aussitôt relâché)."""
    try:
        fd = os.open(str(chemin), os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


class Travail:
    def __init__(self, dossier):
        self.dossier = Path(dossier)
        self.id = self.dossier.name
        self.demande = chemins.lire_json(self.dossier / "demande.json", {}) or {}
        self.etat = {}
        self._etat_mtime = None
        self.journal = ""                # texte complet lu jusqu'ici
        self._off_journal = 0
        self._reste_journal = b""
        self._off_ev = 0
        self._reste_ev = b""
        self.avec_evenements = bool(self.demande.get("evenements"))
        self.avancement = self._nouvel_avancement()
        self.lecture_texte = None
        self._fin_appliquee = False       # statut terminal déjà appliqué à l'avancement

    # --- propriétés ----------------------------------------------------------------------------------------------------

    @property
    def titre(self):
        return self.demande.get("titre") or self.id

    @property
    def cree(self):
        return float(self.demande.get("cree") or 0)

    @property
    def annulation_demandee(self):
        return (self.dossier / "annuler").exists()

    @property
    def statut(self):
        s = self.etat.get("statut")
        if s == "en_cours":
            if not vivant(self.etat.get("pid"), self.etat.get("demarrage")):
                # doubler vient de finir : tant que l'exécuteur qui l'a lancé tourne, il va écrire le statut final
                if self._executeur_vivant():
                    return "en_cours"
                return "interrompu"
            return "en_cours"
        if s:
            return s
        return "annule" if self.annulation_demandee else "en_attente"

    def _executeur_vivant(self):
        """Vrai si l'exécuteur qui a lancé ce travail tourne encore et tient le verrou de la file."""
        pid = self.etat.get("executeur")
        if not pid or not vivant(pid, self.etat.get("executeur_demarrage")):
            return False
        return verrou_tenu(self.dossier.parent / "executeur.verrou")

    @property
    def actif(self):
        return self.statut in ("en_attente", "en_cours")

    @property
    def reprise(self):
        """En attente du quota GPU : l'heure (epoch) de la remise à zéro où il repartira tout seul ; sinon None."""
        if self.statut != "en_attente":
            return None
        return heure_de_reprise(self.dossier, self.demande)

    @property
    def attend_le_quota(self):
        """Vrai tant que ce travail attend la remise à zéro du quota GPU (l'heure n'est pas encore venue)."""
        r = self.reprise
        return r is not None and r > time.time()

    # --- lecture des fichiers ----------------------------------------------------------------------------------------

    def actualiser(self):
        """Relit etat.json, la suite du journal et des événements. Rend (changé, nouveau texte du journal)."""
        change = False
        try:
            m = (self.dossier / "etat.json").stat().st_mtime_ns
        except OSError:
            m = None
        if m != self._etat_mtime:
            self._etat_mtime = m
            self.etat = chemins.lire_json(self.dossier / "etat.json", {}) or {}
            change = True
        debut = self.etat.get("debut")
        if debut and self.avancement.debut is None:
            self.avancement.debut = float(debut)
        nouveau, lignes = self._lire_suite("journal.txt", "_off_journal", "_reste_journal")
        if nouveau:
            self.journal += nouveau
            change = True
        if self.avec_evenements:
            _, lignes_ev = self._lire_suite(Path(self.demande["evenements"]).name, "_off_ev", "_reste_ev",
                                            chemin=Path(self.demande["evenements"]))
            for l in lignes_ev:
                ev = moteur.analyser_evenement(l)
                if ev:
                    self.avancement.appliquer(ev)
                    change = True
        else:
            if self.lecture_texte is None:
                self.lecture_texte = moteur.LectureTexte(float(debut) if debut else self.cree or time.time())
            for l in lignes:
                for ev in self.lecture_texte.ligne(l):
                    self.avancement.appliquer(ev)
                    change = True
        statut = self.statut
        if self._fin_appliquee == "interrompu" and statut in TERMINES and statut != "interrompu":
            # l'état final est arrivé après une conclusion « interrompu » : on refait l'avancement avec la vraie fin
            self._refaire_avancement()
        if statut in TERMINES and not self._fin_appliquee:
            self._fin_appliquee = statut
            code = self.etat.get("code")
            fin = self.etat.get("fin") or self.avancement.dernier_t
            if statut == "interrompu":
                self.avancement.terminer(1, t=fin)
                self.avancement.erreurs.append("le suivi s’est arrêté (poste redémarré ?) : état inconnu")
            else:
                self.avancement.terminer(0 if statut == "fini" else (code if code not in (None, 0) else 1),
                                         t=fin, annule=statut == "annule")
                if statut == "echec" and not self.avancement.erreurs:
                    self.avancement.erreurs.append(self.derniere_erreur())
            change = True
        return change, nouveau

    def _nouvel_avancement(self):
        return suivi.Avancement(
            nb_videos=len(self.demande.get("videos") or []) or 1,
            estimations=self.demande.get("estimations"),
            type_travail=self.demande.get("type", "doublage"),
            debut=None)

    def _refaire_avancement(self):
        """Repart d'un avancement neuf (événements relus depuis le début, ou journal déjà lu) ; le journal affiché ne
        change pas."""
        self.avancement = self._nouvel_avancement()
        self._fin_appliquee = False
        debut = self.etat.get("debut")
        if debut:
            self.avancement.debut = float(debut)
        if self.avec_evenements:
            self._off_ev, self._reste_ev = 0, b""
            _, lignes_ev = self._lire_suite(Path(self.demande["evenements"]).name, "_off_ev", "_reste_ev",
                                            chemin=Path(self.demande["evenements"]))
            for l in lignes_ev:
                ev = moteur.analyser_evenement(l)
                if ev:
                    self.avancement.appliquer(ev)
        else:
            self.lecture_texte = moteur.LectureTexte(float(debut) if debut else self.cree or time.time())
            for l in self.journal.splitlines():
                for ev in self.lecture_texte.ligne(l):
                    self.avancement.appliquer(ev)

    def _lire_suite(self, nom, attr_off, attr_reste, chemin=None):
        chemin = chemin or self.dossier / nom
        try:
            with open(chemin, "rb") as f:
                f.seek(getattr(self, attr_off))
                brut = f.read(4 * 1024 * 1024)
        except OSError:
            return "", []
        if not brut:
            return "", []
        setattr(self, attr_off, getattr(self, attr_off) + len(brut))
        brut = getattr(self, attr_reste) + brut
        coupe = brut.rfind(b"\n")
        if coupe < 0:
            setattr(self, attr_reste, brut)
            return "", []
        complet, reste = brut[:coupe + 1], brut[coupe + 1:]
        setattr(self, attr_reste, reste)
        texte = complet.decode("utf-8", errors="replace")
        return texte, texte.splitlines()

    def derniere_erreur(self):
        """La ligne la plus parlante de la fin du journal."""
        lignes = [l.strip() for l in self.journal.strip().splitlines() if l.strip()]
        for l in reversed(lignes[-40:]):
            if "ÉCHEC" in l or "en échec" in l or "Error" in l or "erreur" in l.lower():
                return l
        return lignes[-1] if lignes else "échec sans message"

    def videos(self):
        return self.demande.get("videos") or []

    @property
    def est_reprise(self):
        return bool(self.demande.get("reprise"))

    @property
    def refus_session(self):
        """Vrai si ce travail a échoué parce que doubler refuse de lancer un doublage tant qu'une session Kaggle
        précédente n'est pas finie. Repéré par l'échec (code non nul) ET le message de doubler dans le journal (le
        même texte au refus rapide et au refus sous le verrou) ; un travail réussi ou annulé n'en est jamais un."""
        return self.statut == "echec" and not self.est_reprise and bool(REFUS_SESSION.search(self.journal))


def texte_attente_anormale(duree):
    """Le message d'une attente « queued » anormale : les causes possibles, dont un incident chez Kaggle."""
    return (f"Kaggle n’a pas encore donné de GPU : le noyau est en file d’attente depuis {duree}. Une autre session du "
            "compte (un autre projet ?) occupe peut-être les GPU, ou Kaggle a un incident (une bannière « notebooks qui ne "
            "démarrent pas » s’affiche alors sur la page du notebook : « Ouvrir la page du notebook sur Kaggle » l’ouvre). "
            "Vous pouvez patienter, ou abandonner cette session.")


def attente_anormale(avancement, maintenant=None):
    """Durée (s) de l'attente d'un GPU par Kaggle si elle est anormale, sinon None. Anormale : le noyau est encore
    « queued » depuis plus de 2 fois l'estimation de doubler, avec au moins 10 min et au plus 30 min (sans estimation :
    30 min). Un autre projet du même compte peut occuper les GPU et laisser la session en file."""
    k = getattr(avancement, "kaggle", None) or {}
    if (k.get("statut") or "").lower() != "queued":
        return None
    maintenant = time.time() if maintenant is None else maintenant
    try:
        depuis = float(k.get("depuis_s") or 0) + max(0.0, maintenant - float(k.get("t") or maintenant))
        est = float(k.get("estimation_s") or 0)
    except (TypeError, ValueError):
        return None
    seuil = SEUIL_ATTENTE_MAXI_S if est <= 0 else min(SEUIL_ATTENTE_MAXI_S, max(SEUIL_ATTENTE_MINI_S, 2 * est))
    return depuis if depuis > seuil else None


class FileTravaux:
    """La file : ajout, liste, annulation, retrait ; démarre l'exécuteur détaché quand il le faut."""

    def __init__(self, racine=None):
        self.racine = Path(racine) if racine else chemins.dossier_etat() / "travaux"
        self.racine.mkdir(parents=True, exist_ok=True)
        self._cache = {}

    @property
    def verrou_executeur(self):
        return self.racine / "executeur.verrou"

    def executeur_actif(self):
        return verrou_tenu(self.verrou_executeur)

    def nouvel_id(self):
        """Identifiant triable dans l'ordre d'ajout (à la microseconde près), puis un suffixe aléatoire."""
        t = time.time()
        ident = time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + f".{int(t * 1e6) % 1000000:06d}-" + uuid.uuid4().hex[:4]
        while (self.racine / ident).exists():
            ident += "x"
        return ident

    def ajouter(self, demande, demarrer=True):
        """Ajoute un travail (demande : argv, env, titre, type, videos, reglages, estimations…) et réveille l'exécuteur."""
        ident = self.nouvel_id()
        d = self.racine / ident
        d.mkdir(parents=True)
        demande = {**demande, "id": ident, "cree": time.time()}
        if demande.get("evenements") is True:
            demande["evenements"] = str(d / "evenements.jsonl")
            demande["argv"] = [a.replace("{evenements}", demande["evenements"]) for a in demande["argv"]]
        chemins.ecrire_json(d / "demande.json", demande)
        t = Travail(d)
        self._cache[ident] = t
        if demarrer:
            self.demarrer_executeur()
        return t

    def travaux(self):
        """Tous les travaux, du plus ancien au plus récent (les objets sont gardés d'un appel à l'autre)."""
        vus = set()
        for d in sorted(self.racine.iterdir()) if self.racine.is_dir() else []:
            if not d.is_dir() or not (d / "demande.json").exists():
                continue
            vus.add(d.name)
            if d.name not in self._cache:
                self._cache[d.name] = Travail(d)
        for ident in list(self._cache):
            if ident not in vus:
                del self._cache[ident]
        return [self._cache[i] for i in sorted(self._cache)]

    def en_attente(self):
        return [t for t in self.travaux() if t.statut == "en_attente"]

    def en_cours(self):
        return next((t for t in self.travaux() if t.statut == "en_cours"), None)

    def annuler(self, travail):
        """Demande l'arrêt : marque « annuler » (l'exécuteur tue le groupe de processus). Si l'exécuteur ne tourne
        plus, on tue le groupe nous-mêmes."""
        (travail.dossier / "annuler").touch()
        travail.actualiser()
        if travail.etat.get("statut") == "en_cours" and not self.executeur_actif():
            # l'exécuteur ne tourne plus (il ne peut donc plus écrire etat.json) : on arrête et on conclut nous-mêmes
            tuer_groupe(travail.etat.get("pgid"))
            chemins.ecrire_json(travail.dossier / "etat.json", {**travail.etat, "statut": "annule", "fin": time.time(),
                                                               "par": "interface"})
            travail.actualiser()

    def annuler_reprises(self):
        """Annule les travaux de reprise (« doubler --reprendre ») encore actifs, par le mécanisme d'annulation habituel.
        Rend ceux qu'on vient d'annuler."""
        annules = []
        for t in self.travaux():
            t.actualiser()
            if t.est_reprise and t.actif and not t.annulation_demandee:
                self.annuler(t)
                annules.append(t)
        return annules

    def lancer_maintenant(self, travail):
        """Un travail en attente du quota part dès que la file le permet (marque « lancer », lue par l'exécuteur)."""
        if travail.statut != "en_attente":
            return False
        (travail.dossier / LANCER).touch()
        self.demarrer_executeur()
        return True

    def retirer(self, travail):
        """Efface un travail terminé (son dossier d'état, pas ses sorties)."""
        if travail.statut not in TERMINES:
            return False
        shutil.rmtree(travail.dossier, ignore_errors=True)
        self._cache.pop(travail.id, None)
        return True

    def demarrer_executeur(self):
        """Lance l'exécuteur détaché (nouvelle session : il survit à la fenêtre) s'il ne tourne pas déjà."""
        if self.executeur_actif():
            return False
        if not any(t.statut == "en_attente" for t in self.travaux()):
            return False
        env = {**os.environ, "VOIXLA_PYTHONPATH_ORIGINE": os.environ.get("PYTHONPATH", ""),
               "PYTHONPATH": os.pathsep.join(
                   [str(chemins.INTERFACE)] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else []))}
        with open(self.racine / "executeur.log", "ab") as log:
            subprocess.Popen([sys.executable, "-m", "voixla.executeur", str(self.racine)], cwd=str(chemins.INTERFACE),
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env,
                             start_new_session=True)
        return True


def tuer_groupe(pgid, delai=6.0):
    """SIGTERM au groupe de processus, puis SIGKILL s'il en reste après « delai » secondes (dans un fil à part)."""
    if not pgid:
        return
    try:
        os.killpg(int(pgid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, ValueError):
        return

    def achever():
        time.sleep(delai)
        try:
            os.killpg(int(pgid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    import threading
    threading.Thread(target=achever, daemon=True).start()


def demande_doublage(videos, reglages, donnees, titre=None, estimations=None, extrait=None):
    """Demande prête pour FileTravaux.ajouter : un seul appel doubler pour toutes ces vidéos (même extrait)."""
    caps = moteur.capacites()
    avec_ev = "--evenements" in caps
    argv = moteur.args_doublage(videos, reglages, donnees, extrait=extrait,
                                evenements="{evenements}" if avec_ev else None, caps=caps)
    noms = [Path(v["chemin"]).name for v in videos]
    if not titre:
        titre = Path(noms[0]).stem if len(noms) == 1 else f"{len(noms)} vidéos : " + ", ".join(Path(n).stem for n in noms)
    return {
        "type": "doublage", "titre": titre, "argv": argv, "env": moteur.environnement(donnees, caps),
        "cwd": str(Path.home()), "evenements": True if avec_ev else None,
        "videos": [{**v, "nom": Path(v["chemin"]).name} for v in videos], "extrait": list(extrait) if extrait else None,
        "reglages": reglages, "estimations": estimations or {}, "donnees": str(donnees),
        "sorties": [str(chemins.dossier_sorties(donnees) / moteur.nom_dossier(
            v["chemin"], extrait or (v.get("extrait") if "extrait_par_video" in caps else None))) for v in videos],
    }


def demande_doubler_prise(entree, duree_s, voix, donnees, estimations=None, titre=None):
    """Demande « Doubler ma prise » : doubler sur le mix prise + beat (FLAC de karaoke/, voir karaoke.preparer_entree_doubler)
    dans cette voix. Une prise chantée sur un beat est du chant : le mode est imposé (sans lui, une voix très présente sur
    un beat discret pourrait être prise pour de la parole)."""
    d = demande_doublage([{"chemin": str(entree), "duree_s": duree_s, "extrait": None}],
                         {"voix": voix, "mode": "chant", "pas": 30}, donnees, titre=titre or Path(entree).stem,
                         estimations=estimations)
    d["prise_karaoke"] = True
    return d


def juger_quota(demande, etat_quota, file_travaux, politique):
    """(demande marquée, attend) : la demande après le jugement du quota GPU, comme la page Doubler. Quota inconnu : telle
    quelle. Elle tient : marquée « tient ». Sinon, avec la politique « lancer » elle part quand même (marquée), avec
    toute autre elle attend la remise à zéro (attend vrai ; pas de dialogue ici : il y a un seul travail, et attendre
    ne coûte rien)."""
    from . import quota
    if etat_quota is None:
        return demande, False
    (v,) = quota.juger_demandes(etat_quota, [{"estimations": {"kaggle": quota.besoin_s(demande)}}],
                                quota.gpu_a_venir(file_travaux))
    if v.tient:
        return quota.marquer(demande, "tient", v), False
    if politique == "lancer":
        return quota.marquer(demande, "lancer", v), False
    return quota.mettre_en_attente(demande, v.remise + quota.REPRISE_APRES_S, v), True


def demande_reprise(donnees):
    """Demande pour « doubler --reprendre » (session Kaggle interrompue : rapatriement, mix et vidéo, sans GPU)."""
    caps = moteur.capacites()
    avec_ev = "--evenements" in caps
    return {"type": "doublage", "titre": "Reprise de la dernière session Kaggle",
            "argv": moteur.args_reprendre(donnees, "{evenements}" if avec_ev else None, caps),
            "env": moteur.environnement(donnees, caps), "cwd": str(Path.home()),
            "evenements": True if avec_ev else None, "videos": [], "reglages": {}, "estimations": {},
            "donnees": str(donnees), "sorties": [], "reprise": True}


def lire_json_sur(chemin):
    try:
        return json.loads(Path(chemin).read_text())
    except (OSError, ValueError):
        return None
