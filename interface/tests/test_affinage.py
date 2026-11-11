"""Essais de voixla.affinage (sans GTK) et du type de travail « affinage » : fiche et badge, bouton activé par la clé
« affinage_pret », évaluation du chant exploitable, durée estimée, demande dans la file (avec le faux affiner, dans un
dossier temporaire : rien n'est envoyé à Kaggle), suivi par événements, annulation, et doublages inchangés."""

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

from voixla import affinage, moteur, suivi, travaux
from voixla.travaux import FileTravaux

from .commun import EssaiIsole, attendre, creer_voix
from .test_seance import FFMPEG, synthese

FAUX_AFFINER = Path(__file__).resolve().parent / "faux_affiner.py"


def fiche(dossier, **autres):
    d = Path(dossier) / "affinage"
    d.mkdir(parents=True, exist_ok=True)
    (d / "affinage.pth").write_bytes(b"x")
    (d / "affinage.json").write_text(json.dumps({"point": "affinage.pth", "complet": True, "pas": 1500,
                                                 "utile_s": 400.0, "date": "2026-11-10T10:00:00+01:00", **autres}))
    return d


class Voix(EssaiIsole):
    def setUp(self):
        super().setUp()
        self.voix = creer_voix(self.donnees, "voixA", "VoixA")

    def test_badge_et_fiche(self):
        self.assertIsNone(affinage.libelle_badge(self.voix))
        self.assertFalse(affinage.est_affinee(self.voix))
        d = fiche(self.voix)
        self.assertEqual(affinage.libelle_badge(self.voix), ("affinée", "succes"))
        self.assertIn("1500 pas", affinage.resume_fiche(self.voix))
        (d / "affinage.json").write_text(json.dumps({"point": "affinage.pth", "complet": False, "pas": 750}))
        self.assertEqual(affinage.libelle_badge(self.voix)[0], "affinage inachevé")
        (d / "affinage.pth").unlink()                       # fiche sans son point de contrôle : pas affinée
        self.assertIsNone(affinage.libelle_badge(self.voix))
        (d / "affinage.json").write_text(json.dumps({"point": "../../voix.json"}))   # chemin hors du dossier : refusé
        self.assertFalse(affinage.est_affinee(self.voix))
        (d / "affinage.json").write_text("pas du json")
        self.assertFalse(affinage.est_affinee(self.voix))

    def test_reprise_et_session(self):
        self.assertIsNone(affinage.reprise_possible(self.voix))
        r = self.voix / "affinage" / "reprise"
        r.mkdir(parents=True)
        for pas in (250, 1000, 750):
            (r / f"DiT_epoch_00001_step_{pas:05d}.pth").write_bytes(b"x")
        self.assertEqual(affinage.reprise_possible(self.voix).name, "DiT_epoch_00001_step_01000.pth")
        self.assertIsNone(affinage.session_en_cours())
        (self.etat / "affinage-en-cours.json").write_text(json.dumps({"voix": "voixA"}))
        self.assertEqual(affinage.session_en_cours()["voix"], "voixA")

    def test_cle_affinage_pret(self):
        self.assertFalse(affinage.pret({}))
        self.assertFalse(affinage.pret({"affinage_pret": False}))
        self.assertFalse(affinage.pret({"affinage_pret": "non"}))
        self.assertTrue(affinage.pret({"affinage_pret": True}))
        self.assertTrue(affinage.pret({"contenu": {"affinage_pret": "oui"}}))
        self.assertFalse(affinage.pret(None))
        self.assertIn("essai réel", affinage.MENTION_EN_PREPARATION)

    def test_niveaux_et_avis(self):
        self.assertEqual([affinage.niveau(s) for s in (0, 60, 119, 120, 299, 300, 600, 700)],
                         ["aucun", "insuffisant", "insuffisant", "possible", "possible", "bon", "bon", "large"])
        self.assertFalse(affinage.peut_proposer(119))
        self.assertTrue(affinage.peut_proposer(120))
        self.assertIn("5 à 10 min", affinage.avis(200))
        self.assertIn("Aucun chant", affinage.avis(0))

    def test_estimation(self):
        bas, haut = affinage.estimer_s(1500)
        self.assertLess(bas, haut)
        self.assertLess(haut, 2 * 3600)
        self.assertIn("1500 pas", affinage.texte_estimation(1500))
        self.assertEqual(affinage.lire_pas("2000"), 2000)
        self.assertEqual(affinage.lire_pas("5"), affinage.PAS_DEFAUT)
        self.assertEqual(affinage.lire_pas("x"), affinage.PAS_DEFAUT)

    def test_bilan(self):
        b = affinage.bilan([{"retenue": True, "utile_s": 100.0, "duree_s": 120.0},
                            {"retenue": False, "utile_s": 0.0, "duree_s": 50.0},
                            {"retenue": True, "utile_s": 50.0, "duree_s": 60.0}])
        self.assertEqual((b["utile_s"], b["retenues"], b["ecartees"], b["total_s"]), (150.0, 2, 1, 230.0))


@unittest.skipUnless(FFMPEG, "ffmpeg absent")
class ChantReel(EssaiIsole):
    def test_evaluation_de_prises_de_telephone(self):
        chant = self.donnees / "voix" / "voixA" / "chant"
        chant.mkdir(parents=True)
        def phrases(f, hz, gain=0.25):    # des notes tenues 2,4 s séparées de silences : le fond est très bas
            return f"aevalsrc='clip({gain}*sin(2*PI*{f}*t),-1,1)*lt(mod(t,3),2.4)':s={hz}"
        synthese(chant / "20261109-153000-notes.m4a", phrases(220, 48000), 12, canaux=1)
        synthese(chant / "20261109-153100-notes.mp3", phrases(330, 44100), 12)
        synthese(chant / "20261109-153200-notes.wav", phrases(440, 16000), 3, canaux=1)   # trop courte
        synthese(chant / "20261109-153300-notes.wav", phrases(440, 44100, 40), 12, canaux=1)  # saturée
        prises = [(f, 0.0) for f in sorted(chant.iterdir())]
        cache = {}
        b = affinage.evaluer(prises, cache)
        self.assertEqual((b["retenues"], b["ecartees"]), (2, 2), b["prises"])
        self.assertGreater(b["utile_s"], 15)
        self.assertEqual(len(cache), 4)
        suivant = affinage.evaluer(prises, cache)           # gardé en cache tant que les fichiers ne changent pas
        self.assertEqual(suivant["utile_s"], b["utile_s"])


class Travail(EssaiIsole):
    def setUp(self):
        super().setUp()
        os.environ["VOIXLA_AFFINER"] = f"{shlex_quote(sys.executable)} {shlex_quote(FAUX_AFFINER)}"
        self.trace = self.tmp / "trace.jsonl"
        os.environ["FAUX_AFFINER_TRACE"] = str(self.trace)
        creer_voix(self.donnees, "voixA", "VoixA")
        self.file = FileTravaux(self.etat / "travaux")

    def test_arguments(self):
        a = moteur.args_affiner("voixA", self.donnees, pas=2000, budget_h=1.5, reprendre=True, evenements="/tmp/e.jsonl")
        self.assertEqual(a[2:], ["voixA", f"--donnees={self.donnees}", "--pas=2000", "--budget-h=1.5", "--reprendre",
                                 "--evenements=/tmp/e.jsonl"])
        self.assertNotIn("--pas", " ".join(moteur.args_affiner("voixA", self.donnees)))
        os.environ["VOIXLA_AFFINER"] = "/x/affiner --a"
        self.assertEqual(moteur.commande_affiner(), ["/x/affiner", "--a"])

    def test_demande(self):
        d = travaux.demande_affinage("voixA", "VoixA", self.donnees, estimations=affinage.estimations_travail())
        self.assertEqual((d["type"], d["voix"], d["videos"], d["sorties"]), ("affinage", "voixA", [], []))
        self.assertIn("VoixA", d["titre"])
        self.assertTrue(d["evenements"])
        self.assertIn("--evenements={evenements}", d["argv"])
        self.assertEqual(d["env"]["VOIXLA_DONNEES"], str(self.donnees))
        self.assertEqual(travaux.demande_doublage([{"chemin": "/v/a.mp4"}], {}, self.donnees)["type"], "doublage")  # inchangé

    def test_travail_de_bout_en_bout(self):
        t = self.file.ajouter(travaux.demande_affinage("voixA", "VoixA", self.donnees,
                                                       estimations=affinage.estimations_travail()))
        self.assertTrue(attendre(lambda: (t.actualiser(), t.statut)[1] == "fini", delai=30), (t.statut, t.journal))
        av = t.avancement
        self.assertEqual(av.type, "affinage")
        self.assertEqual(av.code, 0)
        self.assertEqual(list(av.etapes), suivi.ETAPES_AFFINAGE)
        self.assertEqual([av.etapes[n].etat for n in ("controle", "envoi", "attente", "kaggle", "rapatriement", "fini")],
                         ["fait"] * 6)
        self.assertEqual(av.etapes["kaggle"].libelle, "Affinage sur le GPU")
        self.assertEqual(av.estimation_doubler_s(), 1800.0)
        self.assertEqual(affinage.libelle_badge(self.donnees / "voix" / "voixA"), ("affinée", "succes"))
        lance = json.loads(self.trace.read_text().splitlines()[0])["argv"]
        self.assertEqual(lance[0], "voixA")

    def test_echec_suivi(self):
        os.environ["FAUX_AFFINER_ECHEC"] = "1"
        t = self.file.ajouter(travaux.demande_affinage("voixA", "VoixA", self.donnees))
        self.assertTrue(attendre(lambda: (t.actualiser(), t.statut)[1] == "echec", delai=30), (t.statut, t.journal))
        self.assertIn("train.py muet", t.avancement.erreurs)
        self.assertEqual(t.avancement.etapes["kaggle"].etat, "erreur")

    def test_annulation(self):
        os.environ["FAUX_AFFINER_VITESSE"] = "5"
        t = self.file.ajouter(travaux.demande_affinage("voixA", "VoixA", self.donnees))
        ev = t.dossier / "evenements.jsonl"
        self.assertTrue(attendre(lambda: ev.exists() and "controle" in ev.read_text(encoding="utf-8"), delai=30))
        self.file.annuler(t)
        self.assertTrue(attendre(lambda: (t.actualiser(), t.statut)[1] == "annule", delai=20), t.etat)
        self.assertFalse(affinage.est_affinee(self.donnees / "voix" / "voixA"))
        self.assertTrue(attendre(lambda: not self.file.executeur_actif(), delai=15))


def shlex_quote(x):
    import shlex
    return shlex.quote(str(x))


class DoublagesInchanges(unittest.TestCase):
    def test_etapes_des_doublages_et_remix(self):
        self.assertEqual(list(suivi.Avancement().etapes), suivi.NOMS)
        self.assertEqual(list(suivi.Avancement(type_travail="remix").etapes), suivi.ETAPES_REMIX)
        self.assertEqual(suivi.Avancement().etapes["kaggle"].libelle, "Conversion sur le GPU")
        self.assertNotIn("controle", suivi.NOMS)


class VoixAffineeDansDoubler(EssaiIsole):
    """La case « voix affinée » de la page Doubler : quelles voix ont un affinage, et la valeur de --affinee."""

    def test_ids_de_voix(self):
        self.assertEqual(affinage.ids_de_voix("voixA"), ["voixA"])
        self.assertEqual(affinage.ids_de_voix("homme:voixA,femme:voixB"), ["voixA", "voixB"])
        self.assertEqual(affinage.ids_de_voix(None), [])
        self.assertEqual(affinage.ids_de_voix(""), [])

    def test_voix_affinees(self):
        a = creer_voix(self.donnees, "voixA", "VoixA")
        creer_voix(self.donnees, "voixB", "VoixB")
        self.assertEqual(affinage.voix_affinees(self.donnees, "voixA"), [])
        fiche(a)
        self.assertEqual(affinage.voix_affinees(self.donnees, "voixA"), ["voixA"])
        self.assertEqual(affinage.voix_affinees(self.donnees, "homme:voixA,femme:voixB"), ["voixA"])
        self.assertEqual(affinage.voix_affinees(self.donnees, "voixB"), [])
        fiche(a, complet=False)   # inachevé : pas proposé
        self.assertEqual(affinage.voix_affinees(self.donnees, "voixA"), [])

    def test_choix_affinee(self):
        self.assertIsNone(affinage.choix_affinee(False, False))   # la case suit « affinage_pret » : doubler décide
        self.assertIsNone(affinage.choix_affinee(True, True))
        self.assertEqual(affinage.choix_affinee(True, False), "oui")    # essai avant d'avoir jugé l'affinage bon
        self.assertEqual(affinage.choix_affinee(False, True), "non")    # refus d'une voix affinée jugée bonne



if __name__ == "__main__":
    unittest.main()
