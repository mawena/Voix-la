"""Essais de l'abandon d'une session Kaggle (systeme.abandonner_session), du refus d'un doublage à cause d'une session
en cours (Travail.refus_session) et de l'attente anormale d'un GPU (travaux.attente_anormale). Tout se passe dans un
dossier temporaire : aucun fichier d'état réel, aucune session Kaggle."""

import fcntl
import json
import time
import unittest

from voixla import suivi, systeme, travaux
from voixla.travaux import FileTravaux

from .commun import EssaiIsole

SESSION = {"noyau": "exemple/doublage-voix-gpu", "version_noyau": 7, "pousse_epoch": 1000.0}
MESSAGE_REFUS = ("une session Kaggle précédente n'est pas finie (/état/session-en-cours.json) : « doubler --reprendre » "
                 "la termine (sans repasser sur le GPU) ; effacer ce fichier pour l'abandonner")


class EssaiAbandon(EssaiIsole):
    def setUp(self):
        super().setUp()
        self.etat_doubler.mkdir(exist_ok=True)
        self.fichier = self.etat_doubler / "session-en-cours.json"

    def poser_session(self, contenu=None):
        self.fichier.write_text(json.dumps(contenu or SESSION))

    def test_renomme_sans_effacer(self):
        self.poser_session()
        t = time.mktime((2026, 11, 9, 14, 5, 0, 0, 0, -1))
        etat, dest = systeme.abandonner_session(maintenant=t)
        self.assertEqual(etat, "fait")
        self.assertEqual(dest.name, "session-abandonnee-20261109-1405.json")
        self.assertEqual(dest.parent, self.etat_doubler)
        self.assertFalse(self.fichier.exists())
        self.assertEqual(json.loads(dest.read_text()), SESSION)   # contenu intact
        self.assertIsNone(systeme.session_a_reprendre())
        self.assertIsNone(systeme.session_existante())

    def test_pas_de_session(self):
        self.assertEqual(systeme.abandonner_session(), ("absente", None))

    def test_deux_abandons_dans_la_meme_minute_ne_se_remplacent_pas(self):
        t = time.mktime((2026, 11, 9, 14, 5, 0, 0, 0, -1))
        self.poser_session({"noyau": "exemple/premier"})
        _, d1 = systeme.abandonner_session(maintenant=t)
        self.poser_session({"noyau": "exemple/second"})
        _, d2 = systeme.abandonner_session(maintenant=t)
        self.assertNotEqual(d1, d2)
        self.assertEqual(json.loads(d1.read_text())["noyau"], "exemple/premier")
        self.assertEqual(json.loads(d2.read_text())["noyau"], "exemple/second")

    def test_un_doubler_vivant_empeche_le_renommage(self):
        self.poser_session()
        with open(self.etat_doubler / "verrou-kaggle", "w") as tenu:
            fcntl.flock(tenu, fcntl.LOCK_EX)              # comme un doubler qui suit la session
            self.assertEqual(systeme.abandonner_session(), ("suivie", None))
            self.assertTrue(self.fichier.exists())
            self.assertIsNotNone(systeme.session_existante())   # visible même suivie, contrairement à session_a_reprendre
            self.assertIsNone(systeme.session_a_reprendre())
        self.assertEqual(systeme.abandonner_session()[0], "fait")

    def test_ancien_verrou_du_dossier_de_sorties(self):
        sortie = self.tmp / "sorties"
        (sortie / ".kaggle").mkdir(parents=True)
        self.poser_session({**SESSION, "sortie": str(sortie)})
        with open(sortie / ".kaggle" / "verrou", "w") as tenu:
            fcntl.flock(tenu, fcntl.LOCK_EX)
            self.assertEqual(systeme.abandonner_session()[0], "suivie")
            self.assertTrue(self.fichier.exists())

    def test_annule_d_abord_les_reprises_actives(self):
        file = FileTravaux(self.etat / "travaux")
        reprise = file.ajouter({**travaux.demande_reprise(self.donnees)}, demarrer=False)
        autre = file.ajouter({"type": "doublage", "titre": "autre", "argv": ["true"], "videos": []}, demarrer=False)
        self.poser_session()
        etat, _ = systeme.abandonner_session(file, attente_s=0)
        self.assertEqual(etat, "fait")
        self.assertTrue(reprise.annulation_demandee)        # le mécanisme d'annulation habituel (marque « annuler »)
        self.assertFalse(autre.annulation_demandee)         # un doublage ordinaire n'est pas touché
        reprise.actualiser()
        self.assertEqual(reprise.statut, "annule")

    def test_url_du_notebook(self):
        self.assertEqual(systeme.url_noyau(SESSION), "https://www.kaggle.com/code/exemple/doublage-voix-gpu")
        for mauvais in (None, {}, {"noyau": ""}, {"noyau": "sans-compte"}, {"noyau": "a/b/c"},
                        {"noyau": "x/y; rm -rf"}, {"noyau": 3}):
            self.assertIsNone(systeme.url_noyau(mauvais), mauvais)


class EssaiRefus(EssaiIsole):
    def travail(self, statut, journal, **demande):
        file = FileTravaux(self.etat / "travaux")
        t = file.ajouter({"type": "doublage", "titre": "essai", "argv": ["true"], "videos": [], **demande}, demarrer=False)
        travaux.chemins.ecrire_json(t.dossier / "etat.json", {"statut": statut, "code": 1, "fin": time.time()})
        (t.dossier / "journal.txt").write_text(journal)
        t.actualiser()
        return t

    def test_refus_repere_par_le_message_de_doubler(self):
        self.assertTrue(self.travail("echec", "début\n" + MESSAGE_REFUS + "\n").refus_session)
        self.assertTrue(self.travail("echec", "ÉCHEC " + MESSAGE_REFUS.replace("'", "’") + "\n").refus_session)

    def test_autres_echecs_ne_sont_pas_des_refus(self):
        self.assertFalse(self.travail("echec", "ÉCHEC de l'envoi sur Kaggle : réseau\n").refus_session)
        self.assertFalse(self.travail("annule", MESSAGE_REFUS).refus_session)
        self.assertFalse(self.travail("fini", MESSAGE_REFUS).refus_session)

    def test_la_reprise_elle_meme_n_est_pas_un_refus(self):
        self.assertFalse(self.travail("echec", MESSAGE_REFUS, reprise=True).refus_session)


class EssaiAttenteAnormale(unittest.TestCase):
    def avancement(self, statut, depuis_s, estimation_s=None):
        av = suivi.Avancement(nb_videos=1, estimations=None, type_travail="doublage", debut=None)
        ev = {"type": "kaggle", "statut": statut, "depuis_s": depuis_s, "t": 5000.0}
        if estimation_s is not None:
            ev["estimation_s"] = estimation_s
        av.appliquer(ev)
        return av

    def test_seuils(self):
        n = 5000.0
        # estimation 17 min : 2 x 17 = 34 min, plafonné à 30 min
        t = travaux.texte_attente_anormale("31 min")
        self.assertIn("31 min", t)
        self.assertIn("panne", t)
        self.assertIn("bannière d’incident", t)
        self.assertIsNone(travaux.attente_anormale(self.avancement("queued", 29 * 60, 1020), n))
        self.assertEqual(travaux.attente_anormale(self.avancement("queued", 31 * 60, 1020), n), 31 * 60)
        # estimation courte : 2 x 4 min = 8 min, relevé à 10 min
        self.assertIsNone(travaux.attente_anormale(self.avancement("queued", 9 * 60, 240), n))
        self.assertIsNotNone(travaux.attente_anormale(self.avancement("queued", 11 * 60, 240), n))
        # estimation de 12 min : 2 x 12 = 24 min
        self.assertIsNone(travaux.attente_anormale(self.avancement("queued", 20 * 60, 720), n))
        self.assertIsNotNone(travaux.attente_anormale(self.avancement("queued", 25 * 60, 720), n))
        # sans estimation : 30 min
        self.assertIsNone(travaux.attente_anormale(self.avancement("queued", 25 * 60), n))
        self.assertIsNotNone(travaux.attente_anormale(self.avancement("queued", 31 * 60), n))

    def test_le_temps_passe_depuis_l_evenement_compte(self):
        av = self.avancement("queued", 20 * 60, 1020)
        self.assertIsNone(travaux.attente_anormale(av, 5000.0))
        self.assertIsNotNone(travaux.attente_anormale(av, 5000.0 + 11 * 60))

    def test_seul_queued_est_anormal(self):
        for statut in ("running", "new", "pending", "complete", "injoignable"):
            self.assertIsNone(travaux.attente_anormale(self.avancement(statut, 3 * 3600, 600), 5000.0), statut)

    def test_sans_evenement_kaggle(self):
        av = suivi.Avancement(nb_videos=1, estimations=None, type_travail="doublage", debut=None)
        self.assertIsNone(travaux.attente_anormale(av))


if __name__ == "__main__":
    unittest.main()
