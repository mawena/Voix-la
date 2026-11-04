"""Essais de voixla.travaux et voixla.executeur : file détachée des travaux, avec le vrai exécuteur et le faux doubler
dans un dossier temporaire (un dossier par travail : demande.json, etat.json, annuler, journal.txt, evenements.jsonl)."""

import contextlib
import fcntl
import io
import json
import os
import signal
import subprocess
import sys
import time
import unittest
import uuid
import warnings
from pathlib import Path
from unittest import mock

from voixla import executeur, paroles, travaux
from voixla.travaux import TERMINES, FileTravaux, Travail

from .commun import FAUX_DOUBLER, EssaiIsole, attendre, creer_voix, lire_trace

REGLAGES = {"voix": "jules", "mode": "auto", "octave": "auto", "pas": 30, "voix_db": None, "reverb_db": -13.0}
ETATS_REUSSITE = {"extraction": "fait", "envoi": "fait", "attente": "saute", "kaggle": "fait", "rapatriement": "fait",
                  "mix": "fait", "video": "fait", "fini": "fait"}


def mort(pid):
    """Vrai si le processus n'existe plus (ou n'est plus qu'un zombie)."""
    try:
        s = Path(f"/proc/{int(pid)}/stat").read_text()
    except (OSError, ValueError):
        return True
    return s[s.rindex(") ") + 2:].split(" ")[0] in ("Z", "X")


def statut_de(travail):
    travail.actualiser()
    return travail.statut


class EssaiFile(EssaiIsole):
    def setUp(self):
        super().setUp()
        self.racine = self.etat / "travaux"
        self.file = FileTravaux(self.racine)
        creer_voix(self.donnees)
        self.trace = self.tmp / "trace.jsonl"
        os.environ["FAUX_DOUBLER_TRACE"] = str(self.trace)
        # verrou de la fenêtre tenu, comme si l'interface était ouverte : l'exécuteur ne lance pas notify-send
        self._verrou = open(self.racine / "interface.verrou", "a")
        fcntl.flock(self._verrou, fcntl.LOCK_EX)
        self.addCleanup(self._verrou.close)
        self.addCleanup(self.nettoyer)
        # FileTravaux.demarrer_executeur abandonne son Popen (l'exécuteur est détaché) et executeur.main ne ferme pas
        # son verrou (il sort aussitôt d'ordinaire) : avertissements sans conséquence quand on les appelle ici
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore", ResourceWarning)

    def nettoyer(self):
        """Après un essai raté : plus aucun processus ne doit rester (exécuteur, faux doubler, enfant)."""
        for d in self.racine.iterdir():
            if not d.is_dir():
                continue
            etat = travaux.lire_json_sur(d / "etat.json") or {}
            if not etat:
                (d / "annuler").touch()
            elif etat.get("statut") == "en_cours" and etat.get("pgid"):
                with contextlib.suppress(OSError):
                    os.killpg(int(etat["pgid"]), signal.SIGKILL)
        attendre(lambda: not self.file.executeur_actif(), delai=10)
        f = self.tmp / "enfant.pid"
        if f.exists():
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(f.read_text()), signal.SIGKILL)

    def demande(self, *noms, extrait=None):
        videos = [{"chemin": str(self.video(n))} for n in noms]
        return travaux.demande_doublage(videos, dict(REGLAGES), self.donnees, extrait=extrait,
                                        estimations={"kaggle": 60.0, "mix": 1.0})

    def tous_finis(self):
        ts = self.file.travaux()
        for t in ts:
            t.actualiser()
        return bool(ts) and all(t.statut in TERMINES for t in ts) and not self.file.executeur_actif()

    def executer_ici(self, travail):
        """Le code de l'exécuteur, dans ce processus (un seul travail, sortie de son journal avalée)."""
        with contextlib.redirect_stdout(io.StringIO()):
            executeur.executer(self.racine, travail.dossier)
        travail.actualiser()

    def lancements(self):
        """Lancements de doublage du faux doubler (sans les « --help »), avec leur fin."""
        lignes = lire_trace(self.trace)
        fins = {l["pid"]: l for l in lignes if "fin" in l}
        return [{**l, "fin": fins.get(l["pid"], {}).get("fin")} for l in lignes if "argv" in l and l["argv"] != ["--help"]]


class EssaiAjout(EssaiFile):
    def test_ajout(self):
        avant = time.time()
        t = self.file.ajouter(self.demande("A.mp4"), demarrer=False)
        d = t.dossier
        self.assertEqual(d.parent, self.racine)
        self.assertEqual(t.demande["id"], t.id)
        self.assertGreaterEqual(t.cree, avant)
        self.assertEqual(t.demande["evenements"], str(d / "evenements.jsonl"))
        self.assertIn(f"--evenements={d / 'evenements.jsonl'}", t.demande["argv"])
        self.assertFalse(any("{evenements}" in a for a in t.demande["argv"]))
        self.assertEqual(json.loads((d / "demande.json").read_text(encoding="utf-8"))["argv"], t.demande["argv"])
        self.assertEqual((t.statut, t.actif, t.titre), ("en_attente", True, "A"))
        self.assertEqual(self.file.travaux(), [t])                 # le même objet, gardé en cache
        self.assertEqual(self.file.en_attente(), [t])
        self.assertIsNone(self.file.en_cours())
        self.assertFalse(self.file.retirer(t))                     # pas encore terminé
        self.assertFalse(self.file.executeur_actif())
        self.assertFalse((d / "etat.json").exists())

    def test_demande_doublage(self):
        d = self.demande("A.mp4", "B b.mkv", extrait=(60.0, 40.0))
        self.assertEqual(d["titre"], "2 vidéos : A, B b")
        self.assertEqual([v["nom"] for v in d["videos"]], ["A.mp4", "B b.mkv"])
        self.assertEqual(d["extrait"], [60.0, 40.0])
        self.assertTrue(d["evenements"])
        self.assertIn("--evenements={evenements}", d["argv"])
        self.assertIn("--debut=60", d["argv"])
        self.assertEqual(d["sorties"], [str(self.donnees / "sorties" / "A (extrait 1m00,0+0m40,0)"),
                                        str(self.donnees / "sorties" / "B b (extrait 1m00,0+0m40,0)")])
        self.assertEqual(d["env"]["VOIXLA_DONNEES"], str(self.donnees))
        self.assertEqual(d["type"], "doublage")

    def test_demande_pour_un_ancien_doubler(self):
        self.ancien_doubler()
        d = self.demande("A.mp4")
        self.assertIsNone(d["evenements"])
        self.assertIn(f"--sortie={self.donnees / 'sorties'}", d["argv"])
        self.assertEqual(d["env"]["DOUBLER_VOIX"], str(self.donnees / "voix"))
        t = self.file.ajouter(d, demarrer=False)
        self.assertFalse(t.avec_evenements)

    def test_ordre_d_ajout(self):
        # identifiants aléatoires décroissants, ajoutés dans la même seconde : l'ordre d'ajout doit rester
        suite = [uuid.UUID("ffffffff-ffff-4fff-bfff-ffffffffffff"), uuid.UUID("00000000-0000-4000-8000-000000000000")]
        with mock.patch.object(travaux.uuid, "uuid4", side_effect=suite):
            t1 = self.file.ajouter(self.demande("A.mp4"), demarrer=False)
            t2 = self.file.ajouter(self.demande("B.mp4"), demarrer=False)
        self.assertEqual(self.file.travaux(), [t1, t2])
        self.assertEqual(executeur.prochain(self.racine), t1.dossier)


class EssaiExecuteur(EssaiFile):
    def test_executeur_reel(self):
        t = self.file.ajouter(self.demande("A.mp4", "B b.mkv"))
        self.assertTrue(attendre(self.tous_finis, delai=30), self.file.racine)
        t.actualiser()
        self.assertEqual(t.statut, "fini", t.journal)
        self.assertEqual(t.etat["code"], 0)
        self.assertLessEqual(t.etat["debut"], t.etat["fin"])
        av = t.avancement
        self.assertEqual((av.termine, av.code), (True, 0))
        self.assertEqual({n: av.etapes[n].etat for n in av.ordre}, ETATS_REUSSITE)
        self.assertEqual(len(av.sorties), 4)
        self.assertTrue(all(Path(p).exists() for p in av.sorties), av.sorties)
        # l'événement « sortie » donne travail/<voix>/paroles.json, au format que lit la page Résultats
        self.assertEqual(len(av.paroles), 2)
        self.assertTrue(all(paroles.lire_paroles(p) is not None for p in av.paroles), av.paroles)
        self.assertIn("=== A.mp4 →", t.journal)
        self.assertIn("fini :", t.journal)
        self.assertIn("file vide, arrêt", (self.racine / "executeur.log").read_text(encoding="utf-8"))
        self.assertFalse(self.file.executeur_actif())
        (lancement,) = self.lancements()
        # doubler reçoit l'environnement d'origine : ni le PYTHONPATH de l'exécuteur, ni sa variable de passage
        self.assertEqual(lancement["env"]["PYTHONPATH"], os.environ.get("PYTHONPATH"))
        self.assertEqual(lancement["env"]["VOIXLA_DONNEES"], str(self.donnees))

    def test_un_seul_travail_a_la_fois(self):
        os.environ["FAUX_DOUBLER_VITESSE"] = "0.05"
        t1 = self.file.ajouter(self.demande("A.mp4"))
        t2 = self.file.ajouter(self.demande("B.mp4"))        # l'exécuteur tourne peut-être déjà : un seul tiendra la file
        vus_ensemble = []

        def surveiller():
            for t in (t1, t2):
                t.actualiser()
            if t1.statut == "en_cours" and t2.statut == "en_cours":
                vus_ensemble.append(time.time())
            return self.tous_finis()
        self.assertTrue(attendre(surveiller, delai=40, pas=0.02))
        self.assertEqual(vus_ensemble, [])
        self.assertEqual((t1.statut, t2.statut), ("fini", "fini"))
        self.assertLessEqual(t1.etat["fin"], t2.etat["debut"])
        l1, l2 = self.lancements()
        self.assertTrue(l1["argv"][0].endswith("A.mp4") and l2["argv"][0].endswith("B.mp4"))
        self.assertLessEqual(l1["fin"], l2["debut"])

    def test_annulation_tue_le_groupe(self):
        os.environ["FAUX_DOUBLER_VITESSE"] = "1.0"
        os.environ["FAUX_DOUBLER_ENFANT"] = str(self.tmp / "enfant.pid")
        t = self.file.ajouter(self.demande("A.mp4"))
        ev = t.dossier / "evenements.jsonl"
        self.assertTrue(attendre(lambda: ev.exists() and '"etape": "kaggle"' in ev.read_text(encoding="utf-8"), delai=30))
        enfant = int((self.tmp / "enfant.pid").read_text())
        self.assertFalse(mort(enfant))
        self.file.annuler(t)
        self.assertTrue(attendre(lambda: statut_de(t) == "annule", delai=15), t.etat)
        self.assertEqual(t.etat["code"], -signal.SIGTERM)
        self.assertTrue(attendre(lambda: mort(enfant), delai=5), "l'enfant qui ignore SIGTERM a survécu")
        self.assertTrue(attendre(lambda: not self.file.executeur_actif(), delai=10))
        av = t.avancement
        self.assertTrue(av.termine)
        self.assertEqual(av.etapes["kaggle"].etat, "annule")
        self.assertNotEqual(av.etapes["fini"].etat, "fait")
        self.assertTrue(av.kaggle_parti())
        self.assertNotIn("fini :", t.journal)
        self.assertNotIn('"type": "fin"', ev.read_text(encoding="utf-8"))      # tué net, sans événement « fin »
        self.assertIn("annulation de", (self.racine / "executeur.log").read_text(encoding="utf-8"))

    def test_annulation_avant_le_debut(self):
        t1 = self.file.ajouter(self.demande("A.mp4"), demarrer=False)
        t2 = self.file.ajouter(self.demande("B.mp4"), demarrer=False)
        self.file.annuler(t1)
        self.assertEqual(t1.statut, "annule")                        # marque posée, pas encore d'etat.json
        self.assertFalse(t1.actif)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(executeur.main([str(self.racine)]), 0)
        t1.actualiser()
        t2.actualiser()
        self.assertEqual(t1.etat["statut"], "annule")
        self.assertTrue(t1.etat["avant_debut"])
        self.assertTrue(t1.avancement.termine)
        self.assertFalse((t1.dossier / "journal.txt").exists())
        self.assertEqual(t2.statut, "fini")
        self.assertEqual([l["argv"][0] for l in self.lancements()], [str(self.tmp / "videos" / "B.mp4")])

    def test_executeur_unique(self):
        t = self.file.ajouter(self.demande("A.mp4"), demarrer=False)
        with open(self.file.verrou_executeur, "a") as autre:
            fcntl.flock(autre, fcntl.LOCK_EX)                        # un autre exécuteur tient la file
            self.assertTrue(self.file.executeur_actif())
            self.assertFalse(self.file.demarrer_executeur())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(executeur.main([str(self.racine)]), 0)
        t.actualiser()
        self.assertEqual(t.statut, "en_attente")
        self.assertFalse(self.file.executeur_actif())

    def test_echec(self):
        os.environ["FAUX_DOUBLER_ECHEC"] = "kaggle"
        t = self.file.ajouter(self.demande("A.mp4"), demarrer=False)
        self.executer_ici(t)
        self.assertEqual((t.statut, t.etat["code"]), ("echec", 1))
        self.assertEqual(t.avancement.code, 1)
        self.assertEqual(t.avancement.etapes["kaggle"].etat, "erreur")
        self.assertTrue(t.avancement.erreurs)
        self.assertIn("vidéo(s) en échec", t.derniere_erreur())
        self.assertTrue(self.file.retirer(t))
        self.assertFalse(t.dossier.exists())
        self.assertEqual(self.file.travaux(), [])

    def test_commande_introuvable(self):
        d = {**self.demande("A.mp4"), "argv": [str(self.tmp / "nulle-part" / "doubler"), "x.mp4"]}
        t = self.file.ajouter(d, demarrer=False)
        self.executer_ici(t)
        self.assertEqual((t.statut, t.etat["code"]), ("echec", 127))
        self.assertIn("impossible de lancer doubler", t.journal)
        self.assertIn("impossible de lancer doubler", t.derniere_erreur())

    def test_sans_evenements(self):
        """Ancien doubler : l'avancement est déduit du journal (LectureTexte)."""
        self.ancien_doubler()
        t = self.file.ajouter(self.demande("A.mp4"), demarrer=False)
        self.executer_ici(t)
        self.assertEqual(t.statut, "fini", t.journal)
        self.assertFalse((t.dossier / "evenements.jsonl").exists())
        av = t.avancement
        self.assertEqual({n: av.etapes[n].etat for n in av.ordre}, {**ETATS_REUSSITE, "attente": "fait"})
        self.assertEqual(len(av.sorties), 2)
        self.assertTrue(all(Path(p).exists() for p in av.sorties))


class EssaiReprise(EssaiFile):
    def test_relecture_apres_redemarrage_de_l_interface(self):
        t = self.file.ajouter(self.demande("A.mp4"), demarrer=False)
        with contextlib.redirect_stdout(io.StringIO()):
            executeur.executer(self.racine, t.dossier)
        # nouvelle fenêtre : nouvelle file, tout est relu depuis les fichiers du travail
        (r,) = FileTravaux(self.racine).travaux()
        self.assertIsNot(r, t)
        change, nouveau = r.actualiser()
        self.assertTrue(change)
        self.assertEqual(nouveau, (t.dossier / "journal.txt").read_text(encoding="utf-8"))
        self.assertEqual(r.statut, "fini")
        self.assertEqual({n: r.avancement.etapes[n].etat for n in r.avancement.ordre}, ETATS_REUSSITE)
        self.assertEqual(r.avancement.code, 0)
        self.assertEqual(len(r.avancement.sorties), 2)
        self.assertEqual(r.avancement.debut, r.etat["debut"])
        self.assertEqual(r.actualiser(), (False, ""))               # rien de neuf

    def fabriquer(self, etat=None, evenements=True):
        d = self.racine / "20261104-090000.000000-abcd"
        d.mkdir()
        travaux.chemins.ecrire_json(d / "demande.json", {
            "id": d.name, "titre": "A", "type": "doublage", "videos": [{"chemin": "/v/A.mp4", "nom": "A.mp4"}],
            "evenements": str(d / "evenements.jsonl") if evenements else None, "argv": ["doubler"], "cree": time.time()})
        if etat is not None:
            travaux.chemins.ecrire_json(d / "etat.json", etat)
        return Travail(d)

    def test_lecture_par_morceaux(self):
        t = self.fabriquer()
        j = t.dossier / "journal.txt"
        j.write_bytes(b"ligne 1\nligne in")
        self.assertEqual(t.actualiser(), (True, "ligne 1\n"))
        with open(j, "ab") as f:
            f.write("complète\n".encode())
        self.assertEqual(t.actualiser()[1], "ligne incomplète\n")
        with open(j, "ab") as f:
            f.write(b"\xc3")                                       # « é » coupé en deux écritures
        self.assertEqual(t.actualiser(), (False, ""))
        with open(j, "ab") as f:
            f.write(b"\xa9t\xc3\xa9\n")
        self.assertEqual(t.actualiser()[1], "été\n")
        self.assertEqual(t.journal, "ligne 1\nligne incomplète\nété\n")
        ev = '{"t": "2026-11-04T09:00:00.000+00:00", "type": "etape", "video": "A", "etape": "extraction", "etat": "debut"}\n'
        e = t.dossier / "evenements.jsonl"
        e.write_text(ev[:40], encoding="utf-8")
        t.actualiser()
        self.assertIsNone(t.avancement.debut)
        with open(e, "a", encoding="utf-8") as f:
            f.write(ev[40:])
        self.assertTrue(t.actualiser()[0])
        self.assertEqual(t.avancement.etape_courante(), "extraction")

    def test_interrompu_si_le_processus_est_mort(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        t = self.fabriquer({"statut": "en_cours", "pid": p.pid, "pgid": p.pid, "demarrage": "1",
                            "debut": time.time() - 10})
        t.actualiser()
        self.assertEqual(t.statut, "interrompu")
        self.assertFalse(t.actif)
        self.assertTrue(t.avancement.termine)
        self.assertEqual(t.avancement.code, 1)
        self.assertIn("le suivi s’est arrêté (poste redémarré ?) : état inconnu", t.avancement.erreurs)
        self.assertTrue(self.file.retirer(t))

    def test_fin_en_cours_d_ecriture_par_l_executeur(self):
        """doubler vient de finir et l'exécuteur n'a pas encore écrit « fini » : ce n'est pas une interruption."""
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        verrou = open(self.racine / "executeur.verrou", "a")
        self.addCleanup(verrou.close)
        fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
        moi = os.getpid()
        t = self.fabriquer({"statut": "en_cours", "pid": p.pid, "pgid": p.pid, "demarrage": "1",
                            "debut": time.time() - 10, "executeur": moi,
                            "executeur_demarrage": travaux.demarrage_processus(moi)})
        t.actualiser()
        self.assertEqual(t.statut, "en_cours")
        self.assertFalse(t.avancement.termine)
        time.sleep(0.01)
        travaux.chemins.ecrire_json(t.dossier / "etat.json", {**t.etat, "statut": "fini", "code": 0, "fin": time.time()})
        t.actualiser()
        self.assertEqual(t.statut, "fini")
        self.assertEqual(t.avancement.code, 0)
        self.assertEqual(t.avancement.erreurs, [])

    def test_interrompu_puis_fini_se_corrige(self):
        """« interrompu » vu juste avant que l'état final n'arrive : le vrai statut final remplace la conclusion."""
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        t = self.fabriquer({"statut": "en_cours", "pid": p.pid, "pgid": p.pid, "demarrage": "1",
                            "debut": time.time() - 10})
        ev = '{"t": "2026-01-01T10:00:00.000+00:00", "type": "etape", "video": "A", "etape": "extraction", "etat": "debut"}\n'
        (t.dossier / "evenements.jsonl").write_text(ev, encoding="utf-8")
        t.actualiser()
        self.assertEqual(t.statut, "interrompu")
        self.assertEqual(t.avancement.code, 1)
        time.sleep(0.01)
        travaux.chemins.ecrire_json(t.dossier / "etat.json", {**t.etat, "statut": "fini", "code": 0, "fin": time.time()})
        t.actualiser()
        self.assertEqual(t.statut, "fini")
        self.assertEqual(t.avancement.code, 0)
        self.assertNotIn("le suivi s’est arrêté (poste redémarré ?) : état inconnu", t.avancement.erreurs)

    def test_numero_repris_par_un_autre_processus(self):
        moi = os.getpid()
        t = self.fabriquer({"statut": "en_cours", "pid": moi, "pgid": moi, "demarrage": "1"})
        t.actualiser()
        self.assertEqual(t.statut, "interrompu")
        t = Travail(t.dossier)
        travaux.chemins.ecrire_json(t.dossier / "etat.json", {"statut": "en_cours", "pid": moi,
                                                              "demarrage": travaux.demarrage_processus(moi)})
        t.actualiser()
        self.assertEqual(t.statut, "en_cours")
        self.assertTrue(t.actif)
        self.assertFalse(t.avancement.termine)

    def test_annuler_sans_executeur(self):
        """L'exécuteur ne tourne plus : FileTravaux.annuler tue lui-même le groupe du doubler."""
        os.environ["FAUX_DOUBLER_VITESSE"] = "1.0"
        t = self.fabriquer()
        v = self.video("A.mp4")
        with open(t.dossier / "journal.txt", "ab") as j:
            p = subprocess.Popen([sys.executable, str(FAUX_DOUBLER), str(v), "--donnees", str(self.donnees),
                                  f"--evenements={t.dossier / 'evenements.jsonl'}"],
                                 stdout=j, stderr=subprocess.STDOUT, start_new_session=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        travaux.chemins.ecrire_json(t.dossier / "etat.json", {"statut": "en_cours", "pid": p.pid, "pgid": p.pid,
                                                              "demarrage": travaux.demarrage_processus(p.pid),
                                                              "debut": time.time()})
        t.actualiser()
        self.assertEqual(t.statut, "en_cours")
        self.assertFalse(self.file.executeur_actif())
        self.file.annuler(t)
        self.assertEqual(p.wait(timeout=10), -signal.SIGTERM)
        t.actualiser()
        self.assertIn(t.statut, ("interrompu", "annule"))
        self.assertTrue(t.annulation_demandee)


if __name__ == "__main__":
    unittest.main()
