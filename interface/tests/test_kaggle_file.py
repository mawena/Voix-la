"""Essais de voixla.kaggle_file (les notebooks du compte Kaggle en attente ou en cours). Données inventées ; « kaggle »
est la fausse commande tests/faux_kaggle.py (aucun réseau, aucun jeton, aucun vrai notebook)."""

import datetime
import json
import os
import time
import unittest

from voixla import kaggle_file as kf

from .commun import FAUX_KAGGLE, EssaiIsole

MAINTENANT = datetime.datetime(2026, 11, 9, 18, 0, tzinfo=datetime.timezone.utc).timestamp()
VOIXLA = "exemple/doublage-voix-gpu"
AUTRE = "exemple/autre-projet"


def quand(il_y_a_s):
    d = datetime.datetime.fromtimestamp(MAINTENANT - il_y_a_s, datetime.timezone.utc)
    return d.strftime("%Y-%m-%d %H:%M:%S.%f")


class EssaiAnalyse(unittest.TestCase):
    def test_liste(self):
        texte = ("Warning: outdated\nref,title,author,lastRunTime,totalVotes\n"
                 f"{VOIXLA},Doublage voix GPU,Auteur Exemple,2026-11-09 16:28:00.000000,0\n"
                 f"{AUTRE},Autre projet,Auteur Exemple,2026-11-08 09:00:00,0\n")
        l = kf.analyser_liste(texte)
        self.assertEqual([x[0] for x in l], [VOIXLA, AUTRE])
        self.assertEqual(l[0][1], "Doublage voix GPU")
        self.assertEqual(l[0][2], datetime.datetime(2026, 11, 9, 16, 28, tzinfo=datetime.timezone.utc).timestamp())
        self.assertEqual(kf.analyser_liste("ref,title,author,lastRunTime,totalVotes\n"), [])
        self.assertEqual(kf.analyser_liste(""), [])
        with self.assertRaises(ValueError):
            kf.analyser_liste("<html>erreur</html>")

    def test_statut(self):
        self.assertEqual(kf.analyser_statut(f'{VOIXLA} has status "KernelWorkerStatus.QUEUED"'), "queued")
        self.assertEqual(kf.analyser_statut('x has status "KernelWorkerStatus.CANCEL_ACKNOWLEDGED"'),
                         "cancel_acknowledged")
        self.assertIsNone(kf.analyser_statut("rien"))

    def test_url(self):
        self.assertEqual(kf.url_page(VOIXLA), "https://www.kaggle.com/code/exemple/doublage-voix-gpu")
        self.assertIsNone(kf.url_page("pas une référence"))
        self.assertIsNone(kf.url_page(None))

    def test_depuis(self):
        n = kf.Notebook(VOIXLA, "t", MAINTENANT - (1 * 3600 + 32 * 60 + 5))
        self.assertEqual(kf.depuis(n, MAINTENANT), "lancé il y a 1 h 32")
        n.lance_le = MAINTENANT - 12 * 60
        self.assertEqual(kf.depuis(n, MAINTENANT), "lancé il y a 12 min")
        n.lance_le = MAINTENANT - 5
        self.assertEqual(kf.depuis(n, MAINTENANT), "lancé à l’instant")
        n.lance_le = MAINTENANT - 30 * 3600
        self.assertEqual(kf.depuis(n, MAINTENANT), "lancé il y a 1 j 6 h")
        n.lance_le = None
        self.assertEqual(kf.depuis(n, MAINTENANT), "heure de lancement inconnue")


class EssaiLecture(EssaiIsole):
    def setUp(self):
        super().setUp()
        os.environ["VOIXLA_KAGGLE"] = str(FAUX_KAGGLE)
        self.trace = self.tmp / "kaggle.jsonl"
        os.environ["FAUX_KAGGLE_TRACE"] = str(self.trace)
        self.noyaux = self.tmp / "noyaux.json"

    def ecrire(self, noyaux):
        self.noyaux.write_text(json.dumps(noyaux), encoding="utf-8")
        os.environ["FAUX_KAGGLE_NOYAUX"] = str(self.noyaux)

    def appels(self):
        if not self.trace.exists():
            return []
        return [json.loads(l) for l in self.trace.read_text().splitlines()]

    def lire(self, **k):
        k.setdefault("voixla", {VOIXLA})
        return kf.lire(maintenant=MAINTENANT, **k)

    def test_deux_sessions_en_attente(self):
        self.ecrire([
            {"ref": VOIXLA, "title": "Doublage voix GPU", "lastRunTime": quand(92 * 60), "status": "queued"},
            {"ref": AUTRE, "title": "Autre projet", "lastRunTime": quand(2 * 3600), "status": "queued"},
            {"ref": "exemple/fini-hier", "title": "Fini", "lastRunTime": quand(5 * 3600), "status": "complete"},
            {"ref": "exemple/vieux", "title": "Vieux", "lastRunTime": quand(40 * 3600), "status": "complete"},
        ])
        l = self.lire()
        self.assertTrue(l.ok)
        self.assertFalse(l.partielle)
        self.assertEqual([n.ref for n in l.actifs], [VOIXLA, AUTRE])
        self.assertEqual([n.ref for n in l.termines], ["exemple/fini-hier"])
        self.assertEqual([n.voixla for n in l.actifs], [True, False])
        self.assertEqual(l.actifs[0].libelle, "En attente d’un GPU")
        self.assertEqual(len(l.en_attente), 2)
        self.assertIn("une par une", kf.aide(l))
        self.assertEqual(kf.resume(l), "2 en attente d’un GPU sur le compte")
        # le notebook de plus de 24 h n'a pas coûté d'appel « status »
        statuts = [a[2] for a in self.appels() if a[:2] == ["kernels", "status"]]
        self.assertCountEqual(statuts, [VOIXLA, AUTRE, "exemple/fini-hier"])
        listes = [a for a in self.appels() if a[:2] == ["kernels", "list"]]
        self.assertEqual(len(listes), 1)
        self.assertIn("--mine", listes[0])
        self.assertIn("dateRun", listes[0])

    def test_une_seule_session_en_attente(self):
        self.ecrire([{"ref": VOIXLA, "title": "Doublage", "lastRunTime": quand(600), "status": "running"}])
        l = self.lire()
        self.assertEqual(l.actifs[0].libelle, "En cours")
        self.assertNotIn("une par une", kf.aide(l))
        self.assertIn("ni la place dans sa file", kf.aide(l))
        self.assertEqual(kf.resume(l), "1 en cours sur le compte")

    def test_rien_en_cours(self):
        self.ecrire([{"ref": VOIXLA, "title": "Doublage", "lastRunTime": quand(3600), "status": "complete"},
                     {"ref": AUTRE, "title": "Autre", "lastRunTime": quand(7200), "status": "error"}])
        l = self.lire()
        self.assertEqual(l.actifs, [])
        self.assertEqual([n.libelle for n in l.termines], ["Terminé", "Échec"])
        self.assertEqual(kf.resume(l), "rien en attente ni en cours sur le compte")

    def test_arret_demande(self):
        self.ecrire([{"ref": AUTRE, "title": "Autre", "lastRunTime": quand(600), "status": "cancel_requested"},
                     {"ref": VOIXLA, "title": "V", "lastRunTime": quand(1200), "status": "cancel_acknowledged"}])
        l = self.lire()
        self.assertEqual([n.ref for n in l.actifs], [AUTRE])
        self.assertEqual(l.termines[0].libelle, "Arrêté")

    def test_etat_inconnu_de_kaggle(self):
        self.ecrire([{"ref": AUTRE, "title": "Autre", "lastRunTime": quand(600), "status": "etat_nouveau"}])
        l = self.lire()
        self.assertEqual(l.notebooks[0].categorie, "inconnu")
        self.assertTrue(l.partielle)

    def test_un_statut_en_panne(self):
        self.ecrire([{"ref": VOIXLA, "title": "V", "lastRunTime": quand(600), "status": "running"},
                     {"ref": AUTRE, "title": "A", "lastRunTime": quand(900), "status": "queued", "panne": True}])
        l = self.lire()
        self.assertTrue(l.ok)
        self.assertTrue(l.partielle)
        self.assertEqual([n.ref for n in l.actifs], [VOIXLA])
        inconnu = [n for n in l.termines if n.ref == AUTRE][0]
        self.assertEqual(inconnu.libelle, "État illisible")
        self.assertIn("injoignable", inconnu.erreur)

    def test_limite_d_appels(self):
        self.ecrire([{"ref": f"exemple/n{i}", "title": f"N{i}", "lastRunTime": quand(60 * (i + 1)), "status": "queued"}
                     for i in range(10)])
        l = self.lire()
        statuts = [a for a in self.appels() if a[:2] == ["kernels", "status"]]
        self.assertEqual(len(statuts), kf.MAX_STATUTS)
        self.assertEqual(len(l.en_attente), kf.MAX_STATUTS)   # les plus récents
        self.assertTrue(all(a[2] in {f"exemple/n{i}" for i in range(kf.MAX_STATUTS)} for a in statuts))
        listes = [a for a in self.appels() if a[:2] == ["kernels", "list"]]
        self.assertEqual(listes[0][listes[0].index("--page-size") + 1], str(kf.NB_LISTE))

    def test_appels_en_parallele_par_trois(self):
        self.ecrire([{"ref": f"exemple/n{i}", "title": f"N{i}", "lastRunTime": quand(60 * (i + 1)), "status": "running"}
                     for i in range(5)])
        os.environ["FAUX_KAGGLE_LENT"] = "0.5"
        t0 = time.monotonic()
        l = self.lire()
        duree = time.monotonic() - t0
        self.assertEqual(len(l.actifs), 5)
        # 1 liste + 5 états par 3 = 0,5 + 2 x 0,5 s (en série : 3 s)
        self.assertLess(duree, 2.4)
        self.assertGreater(duree, 1.4)

    def test_noyau_de_voixla_d_apres_la_session(self):
        self.etat_doubler.mkdir(parents=True, exist_ok=True)
        (self.etat_doubler / "session-precedente.json").write_text(json.dumps({"noyau": VOIXLA}), encoding="utf-8")
        self.ecrire([{"ref": VOIXLA, "title": "V", "lastRunTime": quand(60), "status": "queued"},
                     {"ref": AUTRE, "title": "A", "lastRunTime": quand(90), "status": "queued"}])
        self.assertIn(VOIXLA, kf.refs_voixla())
        l = kf.lire(maintenant=MAINTENANT)
        self.assertEqual({n.ref: n.voixla for n in l.notebooks}, {VOIXLA: True, AUTRE: False})

    def test_noyau_de_voixla_d_apres_la_configuration(self):
        conf = {"kaggle": {"compte": "exemple", "noyau": "doublage-voix-gpu"}}
        self.assertIn(VOIXLA, kf.refs_voixla(etat_doubler=self.tmp / "vide", conf=conf))

    def test_commande_absente(self):
        os.environ["VOIXLA_KAGGLE"] = str(self.tmp / "absente")
        l = self.lire()
        self.assertFalse(l.ok)
        self.assertIn("absente", l.indisponible)
        self.assertEqual(l.notebooks, [])
        self.assertTrue(kf.resume(l).startswith("indisponible"))

    def test_reseau_en_panne(self):
        os.environ["FAUX_KAGGLE_PANNE"] = "1"
        l = self.lire()
        self.assertFalse(l.ok)
        self.assertIn("injoignable", l.indisponible)

    def test_delai_depasse(self):
        self.ecrire([{"ref": VOIXLA, "title": "V", "lastRunTime": quand(60), "status": "queued"}])
        os.environ["FAUX_KAGGLE_LENT"] = "3"
        t0 = time.monotonic()
        l = self.lire(delai_liste=0.4)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertFalse(l.ok)
        self.assertIn("pas de réponse", l.indisponible)

    def test_delai_d_un_statut(self):
        self.ecrire([{"ref": VOIXLA, "title": "V", "lastRunTime": quand(60), "status": "queued"}])
        os.environ["FAUX_KAGGLE_LENT"] = "0.01"
        l = self.lire(delai_statut=0.001)   # impossible de répondre à temps
        self.assertTrue(l.ok)
        self.assertTrue(l.partielle)
        self.assertIn("pas de réponse", l.notebooks[0].erreur)

    def test_liste_illisible(self):
        os.environ["FAUX_KAGGLE_LISTE_BRUTE"] = "<html>page d'erreur</html>"
        l = self.lire()
        self.assertFalse(l.ok)
        self.assertIn("illisible", l.indisponible)

    def test_compte_sans_notebook(self):
        self.ecrire([])
        l = self.lire()
        self.assertTrue(l.ok)
        self.assertEqual(l.notebooks, [])
        self.assertEqual(kf.resume(l), "rien en attente ni en cours sur le compte")

    def test_avertissement_de_version(self):
        self.ecrire([{"ref": VOIXLA, "title": "V", "lastRunTime": quand(60), "status": "running"}])
        os.environ["FAUX_KAGGLE_AVERTISSEMENT"] = "1"
        self.assertEqual(len(self.lire().actifs), 1)

    def test_jamais_de_suppression(self):
        self.ecrire([{"ref": VOIXLA, "title": "V", "lastRunTime": quand(60), "status": "queued"}])
        self.lire()
        for a in self.appels():
            self.assertNotIn("delete", a)
            self.assertNotIn("push", a)
            self.assertEqual(a[0], "kernels")
            self.assertIn(a[1], ("list", "status"))


class EssaiCache(unittest.TestCase):
    def suivi(self):
        self.tours = []

        def lecteur(maintenant=None, **_):
            self.tours.append(maintenant)
            return kf.Lecture(lu_le=maintenant)
        return kf.Suivi(lecteur)

    def test_pas_plus_d_un_tour_par_minute(self):
        s = self.suivi()
        l, nouveau = s.lire_si_besoin(maintenant=1000.0)
        self.assertTrue(nouveau)
        l2, nouveau = s.lire_si_besoin(maintenant=1030.0)
        self.assertFalse(nouveau)
        self.assertIs(l2, l)
        _, nouveau = s.lire_si_besoin(maintenant=1059.0)
        self.assertFalse(nouveau)
        _, nouveau = s.lire_si_besoin(maintenant=1060.0)
        self.assertTrue(nouveau)
        self.assertEqual(self.tours, [1000.0, 1060.0])

    def test_page_cachee_sans_travail(self):
        s = self.suivi()
        l, nouveau = s.lire_si_besoin(visible=False, travail_actif=False, maintenant=1000.0)
        self.assertFalse(nouveau)
        self.assertIsNone(l)
        self.assertEqual(self.tours, [])
        _, nouveau = s.lire_si_besoin(visible=False, travail_actif=True, maintenant=1000.0)
        self.assertTrue(nouveau)

    def test_actualiser_force_mais_pas_en_rafale(self):
        s = self.suivi()
        s.lire_si_besoin(maintenant=1000.0)
        _, nouveau = s.lire_si_besoin(forcer=True, maintenant=1005.0)
        self.assertFalse(nouveau)
        _, nouveau = s.lire_si_besoin(forcer=True, maintenant=1020.0)
        self.assertTrue(nouveau)

    def test_un_seul_tour_a_la_fois(self):
        import threading
        debut, fin = threading.Event(), threading.Event()

        def lecteur(maintenant=None, **_):
            debut.set()
            fin.wait(5)
            return kf.Lecture(lu_le=maintenant)
        s = kf.Suivi(lecteur)
        fil = threading.Thread(target=lambda: s.lire_si_besoin(maintenant=1000.0))
        fil.start()
        self.assertTrue(debut.wait(5))
        _, nouveau = s.lire_si_besoin(forcer=True, maintenant=5000.0)
        self.assertFalse(nouveau)
        fin.set()
        fil.join()
        self.assertEqual(s.appels, 1)


if __name__ == "__main__":
    unittest.main()
