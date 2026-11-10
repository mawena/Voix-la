"""Essais de voixla.quota (quota GPU de Kaggle), de l'attente du quota dans la file (travaux, exécuteur) et du point
d'accroche du secours. Données inventées, dossiers temporaires ; « kaggle » est la fausse commande tests/faux_kaggle.py
(aucun réseau, aucun jeton)."""

import datetime
import fcntl
import json
import os
import time
import unittest
import warnings
from pathlib import Path

from voixla import chemins, executeur, quota, secours, travaux
from voixla.travaux import FileTravaux

from .commun import FAUX_KAGGLE, EssaiIsole, attendre, creer_voix, lire_trace

H = 3600.0
# vendredi 6 novembre 2026, 08:41 UTC (la remise à zéro suivante est le samedi 7 à 00:00 UTC)
VENDREDI = datetime.datetime(2026, 11, 6, 8, 41, tzinfo=datetime.timezone.utc).timestamp()
SAMEDI = datetime.datetime(2026, 11, 7, 0, 0, tzinfo=datetime.timezone.utc).timestamp()


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat(timespec="milliseconds")


def toucher(f, ts):
    os.utime(f, (ts, ts))


class EssaiTemps(unittest.TestCase):
    def test_remise_suivante(self):
        self.assertEqual(quota.remise_suivante(VENDREDI), SAMEDI)
        # pile à l'heure de la remise : la suivante est une semaine plus tard
        self.assertEqual(quota.remise_suivante(SAMEDI), SAMEDI + 7 * 86400)
        self.assertEqual(quota.remise_suivante(SAMEDI - 1), SAMEDI)
        # autre réglage : lundi 06:00 UTC
        lundi = datetime.datetime(2026, 11, 9, 6, 0, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(quota.remise_suivante(VENDREDI, jour=0, heure=6), lundi)

    def test_heures(self):
        self.assertEqual(quota.heures(6 * H + 52 * 60), "6 h 52")
        self.assertEqual(quota.heures(30 * H), "30 h")
        self.assertEqual(quota.heures(45 * 60), "45 min")
        self.assertEqual(quota.heures(-5), "0 min")
        self.assertEqual(quota.heures(None), "—")

    def test_jour_heure(self):
        t = time.mktime((2026, 11, 7, 1, 0, 0, 0, 0, -1))   # heure locale
        self.assertEqual(quota.jour_heure(t, maintenant=t - 3 * 86400), "samedi 7 nov. à 01:00")
        self.assertEqual(quota.jour_heure(t, maintenant=t - 3600), "aujourd’hui à 01:00")
        self.assertEqual(quota.jour_heure(t, maintenant=t - 86400), "demain à 01:00")
        self.assertEqual(quota.regle_remise(), "samedi 00:00 UTC")

    def test_reglages(self):
        self.assertEqual(quota.reglages({}), (30 * H, 5, 0, 2 * H, "demander"))
        r = quota.reglages({"quota_h": 25, "quota_remise_jour": 0, "quota_remise_heure": 6, "quota_alerte_h": 1.5,
                            "quota_depassement": "attendre"})
        self.assertEqual(r, (25 * H, 0, 6, 1.5 * H, "attendre"))
        self.assertEqual(quota.reglages({"quota_depassement": "n’importe quoi"})[4], "demander")
        self.assertEqual(chemins.PREFERENCES_DEFAUT["quota_depassement"], "demander")
        self.assertEqual(chemins.PREFERENCES_DEFAUT["quota_h"], 30.0)


class EssaiLectureKaggle(EssaiIsole):
    def setUp(self):
        super().setUp()
        os.environ["VOIXLA_KAGGLE"] = str(FAUX_KAGGLE)
        self.trace = self.tmp / "kaggle.jsonl"
        os.environ["FAUX_KAGGLE_TRACE"] = str(self.trace)

    def test_analyse(self):
        texte = ('Warning: outdated\n[{"resource": "GPU", "used": "23.13h", "remaining": "6.87h", "total": "30.00h", '
                 '"refreshAt": "2026-11-07T00:00:00+00:00"}]')
        l = quota.analyser_sortie_kaggle(texte, maintenant=VENDREDI)
        self.assertAlmostEqual(l["utilise_s"], 23.13 * H)
        self.assertAlmostEqual(l["total_s"], 30 * H)
        self.assertEqual(l["remise"], SAMEDI)
        self.assertEqual(l["lu_le"], VENDREDI)
        # CLI d'avant 2.2.5 : heure UTC sans fuseau
        l = quota.analyser_sortie_kaggle('[{"resource": "GPU", "used": "1h", "total": "30h", '
                                         '"refreshAt": "2026-11-07T00:00:00"}]')
        self.assertEqual(l["remise"], SAMEDI)
        with self.assertRaises(ValueError):
            quota.analyser_sortie_kaggle('[{"resource": "TPU", "used": "0h", "total": "20h", "refreshAt": "x"}]')
        with self.assertRaises(ValueError):
            quota.analyser_sortie_kaggle("No quota information available")

    def test_lecture_par_la_cli(self):
        os.environ["FAUX_KAGGLE_QUOTA"] = "12.5,30,2026-11-07T00:00:00+00:00"
        os.environ["FAUX_KAGGLE_AVERTISSEMENT"] = "1"
        l, raison = quota.lire_kaggle()
        self.assertEqual(raison, "")
        self.assertAlmostEqual(l["utilise_s"], 12.5 * H)
        self.assertEqual(l["remise"], SAMEDI)
        self.assertEqual(lire_trace(self.trace), [["quota", "--format", "json"]])

    def test_cli_trop_ancienne_ou_absente(self):
        os.environ["FAUX_KAGGLE_ANCIEN"] = "1"
        l, raison = quota.lire_kaggle()
        self.assertIsNone(l)
        self.assertIn("trop ancienne", raison)
        os.environ["VOIXLA_KAGGLE"] = str(self.tmp / "absente")
        l, raison = quota.lire_kaggle()
        self.assertIsNone(l)
        self.assertIn("absente", raison)

    def test_lecture_gardee(self):
        os.environ["FAUX_KAGGLE_QUOTA"] = "10,30"
        l, _r = quota.lecture_kaggle(age_max=300)
        self.assertTrue(quota.fichier_lecture().is_file())
        self.assertEqual(quota.fichier_lecture().parent, self.etat)
        # moins de 5 min : reprise telle quelle, sans appeler Kaggle
        l2, _r = quota.lecture_kaggle(age_max=300)
        self.assertEqual(l2, l)
        self.assertEqual(len(lire_trace(self.trace)), 1)
        # age_max=0 : relue
        os.environ["FAUX_KAGGLE_QUOTA"] = "11,30"
        l3, _r = quota.lecture_kaggle(age_max=0)
        self.assertAlmostEqual(l3["utilise_s"], 11 * H)
        # Kaggle injoignable : la dernière lecture gardée, et la raison
        os.environ["FAUX_KAGGLE_PANNE"] = "1"
        l4, raison = quota.lecture_kaggle(age_max=0)
        self.assertAlmostEqual(l4["utilise_s"], 11 * H)
        self.assertIn("injoignable", raison)

    def test_jeton_jamais_ouvert(self):
        # la lecture passe par la CLI seule : quota.py ne nomme aucun fichier de jeton
        source = Path(quota.__file__).read_text(encoding="utf-8")
        self.assertNotIn("access_token", source)
        self.assertNotRegex(source, r"\.kaggle/|[^-]kaggle\.json")


class EssaiSessions(EssaiIsole):
    """Sessions locales : rapports, travaux de la file, fichiers de session de doubler, outils, banc."""

    def setUp(self):
        super().setUp()
        self.sorties = self.donnees / "sorties"
        self.racine = self.etat / "travaux"
        self.racine.mkdir(parents=True)
        self.etat_doubler.mkdir(parents=True)
        self.depot = self.tmp / "depot"

    def rapport(self, titre, voix, fin_noyau, k=1320.0, attente=4.0, version=42, rapatriement=27.0, videos=1,
                sorties=None):
        """Un doublage fini : rapport.json et journal du noyau rapatrié (heure du fichier = fin + rapatriement)."""
        tv = (sorties or self.sorties) / titre / "travail" / voix
        tv.mkdir(parents=True, exist_ok=True)
        r = {"duree_s": 200.0, "temps_local": {"noyau_depuis_envoi_s": k, "attente_demarrage_noyau_s": attente,
                                               "version_noyau": version, "rapatriement_s": rapatriement,
                                               "videos_dans_la_session": videos, "gpus": 2}}
        (tv / "rapport.json").write_text(json.dumps(r), encoding="utf-8")
        log = tv / "doublage-voix-gpu.log"
        log.write_text('[{"stream_name": "stdout", "time": 1.0, "data": "x"}]', encoding="utf-8")
        toucher(log, fin_noyau + rapatriement)
        toucher(tv / "rapport.json", fin_noyau + 200)
        return tv / "rapport.json"

    def travail(self, titre, pousse, duree=None, statut="fini", estimation=None, rapport=None, depuis=None):
        d = self.racine / f"20261106-{int(pousse)}-abcd"
        d.mkdir()
        ev = d / "evenements.jsonl"
        lignes = [{"t": iso(pousse), "type": "etape", "video": titre, "etape": "kaggle", "etat": "debut"}]
        if depuis is not None or estimation is not None:
            lignes.append({"t": iso(pousse + (depuis or 0)), "type": "kaggle", "video": titre, "statut": "running",
                           "depuis_s": depuis or 0, "estimation_s": estimation})
        if duree is not None:
            lignes.append({"t": iso(pousse + duree), "type": "etape", "video": titre, "etape": "kaggle", "etat": "fin",
                           "duree_s": duree})
        if rapport:
            lignes.append({"t": iso(pousse + (duree or 0) + 100), "type": "sortie", "video": titre,
                           "rapport": str(rapport)})
        ev.write_text("".join(json.dumps(l) + "\n" for l in lignes), encoding="utf-8")
        chemins.ecrire_json(d / "demande.json", {"type": "doublage", "titre": titre, "evenements": str(ev),
                                                 "estimations": {"kaggle": estimation or 900.0}})
        if statut:
            chemins.ecrire_json(d / "etat.json", {"statut": statut, "fin": pousse + (duree or depuis or 0) + 200})
        return d

    def locales(self, maintenant=VENDREDI, sorties=None):
        return quota.sessions_locales([self.sorties] if sorties is None else sorties, self.racine, self.etat_doubler,
                                      self.depot, depuis=maintenant - 8 * 86400, maintenant=maintenant)

    def test_rapport_seul(self):
        fin = VENDREDI - 3 * H
        self.rapport("Chanson d’essai", "jules", fin)
        (s,) = self.locales()
        self.assertEqual(s.genre, "doublage")
        self.assertEqual(s.version, 42)
        self.assertAlmostEqual(s.duree_s, 1316.0)          # noyau depuis l'envoi − attente du démarrage
        self.assertAlmostEqual(s.fin, fin, places=3)       # journal du noyau − rapatriement
        self.assertTrue(s.mesuree)
        self.assertEqual(s.titre, "Chanson d’essai")

    def test_videos_d_une_meme_session(self):
        fin = VENDREDI - 3 * H
        self.rapport("Chanson A", "jules", fin, videos=2)
        self.rapport("Chanson B", "jules", fin + 3, videos=2)
        sessions = self.locales()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].origines, ["rapport"])

    def test_deux_sessions_de_suite(self):
        self.rapport("Chanson A", "jules", VENDREDI - 5 * H, version=40)
        self.rapport("Chanson B", "jules", VENDREDI - 3 * H, version=41)
        self.assertEqual(len(self.locales()), 2)

    def test_travail_et_rapport_fusionnes(self):
        fin = VENDREDI - 3 * H
        r = self.rapport("Chanson d’essai", "jules", fin)
        self.travail("Chanson d’essai", fin - 1320, duree=1320.0, rapport=r)
        (s,) = self.locales()
        self.assertEqual(sorted(s.origines), ["rapport", "travail"])
        self.assertAlmostEqual(s.duree_s, 1316.0)   # le rapport donne les heures

    def test_rapport_hors_des_sorties_trouve_par_le_travail(self):
        ailleurs = self.tmp / "ailleurs"
        fin = VENDREDI - 3 * H
        r = self.rapport("Chanson d’essai", "jules", fin, sorties=ailleurs)
        self.travail("Chanson d’essai", fin - 1320, duree=1320.0, rapport=r)
        (s,) = self.locales()
        self.assertIn("rapport", s.origines)

    def test_travail_sans_rapport(self):
        # en échec après la session, annulé pendant (le noyau continue seul), en cours
        self.travail("Échec", VENDREDI - 6 * H, duree=600.0, statut="echec")
        self.travail("Annulé", VENDREDI - 4 * H, statut="annule", estimation=1100.0, depuis=300.0)
        sessions = self.locales()
        self.assertEqual([round(s.duree_s) for s in sessions], [600, 1100])
        self.assertTrue(sessions[0].mesuree)
        self.assertFalse(sessions[1].mesuree)   # durée estimée : celle que doubler prévoyait

    def test_travail_en_cours(self):
        d = self.travail("En cours", VENDREDI - 600, statut=None, estimation=1200.0, depuis=500.0)
        chemins.ecrire_json(d / "etat.json", {"statut": "en_cours", "pid": os.getpid(),
                                              "demarrage": travaux.demarrage_processus(os.getpid())})
        (s,) = self.locales()
        self.assertTrue(s.en_cours)
        self.assertAlmostEqual(s.duree_s, 600.0)
        # doubler mort sans conclure : la session n'est plus « en cours », sa durée est estimée
        chemins.ecrire_json(d / "etat.json", {"statut": "en_cours", "pid": 999999999, "demarrage": "1"})
        (s,) = self.locales()
        self.assertFalse(s.en_cours)
        self.assertAlmostEqual(s.duree_s, 1200.0)

    def test_sessions_de_doubler_en_ligne_de_commande(self):
        # la dernière session close, lancée hors de l'interface vers un autre dossier : comptée, avec son rapport
        ailleurs = self.tmp / "ailleurs"
        fin = VENDREDI - 2 * H
        r = self.rapport("Essai CLI", "jules", fin, version=43, sorties=ailleurs)
        chemins.ecrire_json(self.etat_doubler / "session-precedente.json", {
            "noyau": "essai/doublage-voix-gpu", "version_noyau": 43, "pousse_epoch": fin - 1320,
            "estimation_s": 1300, "etat": "rapatriée", "close_le": iso(fin + 30),
            "travaux": [{"nom": "Essai CLI", "tv": str(r.parent)}]})
        (s,) = self.locales()
        self.assertEqual(sorted(s.origines), ["rapport", "session-precedente"])
        self.assertTrue(s.mesuree)
        # sans le rapport (dossier effacé) : de la poussée à la clôture, durée estimée
        r.unlink()
        (s,) = self.locales()
        self.assertEqual(s.origines, ["session-precedente"])
        self.assertAlmostEqual(s.duree_s, 1350.0)
        self.assertFalse(s.mesuree)

    def test_session_en_cours_de_doubler(self):
        chemins.ecrire_json(self.etat_doubler / "session-en-cours.json", {
            "version_noyau": 44, "pousse_epoch": VENDREDI - 300, "estimation_s": 800,
            "travaux": [{"nom": "Lot", "tv": str(self.tmp / "x")}, {"nom": "Autre"}]})
        verrou = self.etat_doubler / "verrou-kaggle"
        verrou.write_text("")
        with open(verrou) as tenu:
            fcntl.flock(tenu, fcntl.LOCK_EX)    # doubler en pleine session
            (s,) = self.locales()
            self.assertTrue(s.en_cours)
            self.assertAlmostEqual(s.duree_s, 300.0)
            self.assertEqual(s.titre, "Lot (+1)")
        # verrou libre : session interrompue, durée estimée (au plus le temps écoulé)
        (s,) = self.locales()
        self.assertFalse(s.en_cours)
        self.assertAlmostEqual(s.duree_s, 300.0)
        (s,) = self.locales(maintenant=VENDREDI + 3600)
        self.assertAlmostEqual(s.duree_s, 800.0)

    def test_outils_et_banc(self):
        for f in (self.etat_doubler / "outils.json", self.sorties / ".kaggle" / "outils.json"):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"date": "2026-11-04 14:35", "commit": "abc"}), encoding="utf-8")
            toucher(f, VENDREDI - 40 * H)
        b = self.depot / "banc" / "resultats"
        (b / "s1").mkdir(parents=True)
        (b / "s1" / "suivi.json").write_text(json.dumps({"session": "s1", "pousse_epoch": VENDREDI - 30 * H}))
        (b / "s1" / "doublage-banc.log").write_text('[{"time": 1.0}, {"time": 2808.4}]')
        (b / "s2").mkdir()
        (b / "s2" / "suivi.json").write_text(json.dumps({"session": "s2", "pousse_epoch": VENDREDI - 20 * H,
                                                         "noyau_depuis_envoi_s": 822.2}))
        (b / "s3").mkdir()
        (b / "s3" / "suivi.json").write_text(json.dumps({"session": "s3", "pousse_epoch": VENDREDI - 10 * H}))
        sessions = self.locales()
        self.assertEqual([(s.genre, round(s.duree_s, 1)) for s in sessions],
                         [("outils", 660.0), ("banc", 2808.4), ("banc", 822.2)])   # s3 : durée inconnue

    def test_rien_d_avant_la_periode(self):
        self.rapport("Ancienne", "jules", VENDREDI - 20 * 86400)
        self.assertEqual(self.locales(), [])


class EssaiCalcul(unittest.TestCase):
    def session(self, debut, duree, en_cours=False):
        return quota.Session(debut, duree, "doublage", "rapport", en_cours=en_cours)

    def test_avec_kaggle(self):
        lecture = {"utilise_s": 20 * H, "total_s": 30 * H, "remise": SAMEDI, "lu_le": VENDREDI - 600}
        # une session finie avant la lecture (déjà comptée par Kaggle), une en cours depuis avant la lecture
        sessions = [self.session(VENDREDI - 5 * H, 1200), self.session(VENDREDI - 900, 900, en_cours=True)]
        e = quota.calculer(lecture, sessions, {}, VENDREDI)
        self.assertEqual(e.source, "kaggle")
        self.assertTrue(e.remise_verifiee)
        self.assertEqual(e.remise, SAMEDI)
        self.assertAlmostEqual(e.ajoute_s, 600)                 # la part de la session en cours depuis la lecture
        self.assertAlmostEqual(e.utilise_s, 20 * H + 600)
        self.assertAlmostEqual(e.reste_s, 10 * H - 600)
        self.assertAlmostEqual(e.local_s, 2100)
        self.assertAlmostEqual(e.en_cours_s, 900)

    def test_remise_passee_depuis_la_lecture(self):
        lecture = {"utilise_s": 29 * H, "total_s": 30 * H, "remise": SAMEDI, "lu_le": SAMEDI - H}
        apres = SAMEDI + 2 * H
        e = quota.calculer(lecture, [self.session(SAMEDI + H, 1800), self.session(SAMEDI - 2 * H, 1800)], {}, apres)
        self.assertEqual(e.source, "local")
        self.assertTrue(e.remise_verifiee)
        self.assertEqual(e.remise, SAMEDI + 7 * 86400)
        self.assertAlmostEqual(e.utilise_s, 1800)

    def test_sans_kaggle(self):
        e = quota.calculer(None, [self.session(VENDREDI - 3 * H, 1800), self.session(SAMEDI - 9 * 86400, 1800)],
                           {"quota_h": 25}, VENDREDI, raison="réseau")
        self.assertEqual(e.source, "local")
        self.assertFalse(e.remise_verifiee)
        self.assertEqual(e.total_s, 25 * H)
        self.assertAlmostEqual(e.utilise_s, 1800)
        self.assertEqual(e.raison, "réseau")
        # session à cheval sur la remise à zéro : seule la part de la semaine compte
        debut = SAMEDI - 7 * 86400 - 600
        e = quota.calculer(None, [self.session(debut, 1800)], {}, VENDREDI)
        self.assertAlmostEqual(e.utilise_s, 1200)

    def test_alerte(self):
        lecture = {"utilise_s": 28.5 * H, "total_s": 30 * H, "remise": SAMEDI, "lu_le": VENDREDI}
        e = quota.calculer(lecture, [], {}, VENDREDI)
        self.assertTrue(e.alerte(2 * H))
        self.assertFalse(e.alerte(1 * H))
        self.assertAlmostEqual(e.fraction, 0.95)


class EssaiVerdict(unittest.TestCase):
    def etat(self, reste_h):
        return quota.EtatQuota(30 * H, (30 - reste_h) * H, SAMEDI, "kaggle", True, [], 0.0)

    def test_tient_avec_la_marge(self):
        v = quota.juger(self.etat(1), 40 * 60)
        self.assertTrue(v.tient)                              # 40 min × 1,2 = 48 min ≤ 1 h
        self.assertAlmostEqual(v.reste_apres_s, 20 * 60)
        self.assertFalse(quota.juger(self.etat(1), 55 * 60).tient)   # 66 min > 1 h
        self.assertFalse(quota.juger(self.etat(1), 40 * 60, a_venir=30 * 60).tient)

    def test_plusieurs_sessions(self):
        demandes = [{"estimations": {"kaggle": 1800}}, {"estimations": {"kaggle": 1800}},
                    {"estimations": {"kaggle": 600}}]
        vs = quota.juger_demandes(self.etat(1), demandes)
        self.assertEqual([v.tient for v in vs], [True, False, True])
        self.assertEqual([v.a_venir_s for v in vs], [0, 1800, 1800])

    def test_besoin(self):
        self.assertEqual(quota.besoin_s({"estimations": {"kaggle": 1356.0}}), 1356.0)
        self.assertEqual(quota.besoin_s({"reprise": True, "estimations": {"kaggle": 900}}), 0.0)
        self.assertEqual(quota.besoin_s({"type": "remix"}), 0.0)
        self.assertEqual(quota.besoin_s({}), 0.0)

    def test_phrases(self):
        e = self.etat(6.5)
        self.assertEqual(quota.phrase_estimation(quota.juger(e, 23 * 60)),
                         "Ce doublage prendra environ 23 min de GPU ; il restera 6 h 07 cette semaine.")
        self.assertEqual(quota.phrase_estimation(quota.juger(self.etat(0.2), 23 * 60)),
                         "Ce doublage prendra environ 23 min de GPU, il n’en reste que 12 min cette semaine.")

    def test_mise_en_attente(self):
        v = quota.juger(self.etat(0.2), 23 * 60)
        d = quota.mettre_en_attente({"titre": "x", "estimations": {"kaggle": 1380}}, SAMEDI + 120, v)
        self.assertEqual(d["attendre_jusqu_a"], SAMEDI + 120)
        self.assertEqual(d["quota"]["decision"], "attendre")
        self.assertEqual(d["quota"]["besoin_s"], 1380)
        self.assertEqual(quota.marquer({"a": 1}, "lancer", v)["quota"]["decision"], "lancer")


class EssaiAttenteDansLaFile(EssaiIsole):
    """Un travail en attente du quota : il ne part qu'à la remise à zéro (ou sur « Lancer maintenant »), les travaux
    prêts passent devant, et l'exécuteur veille tant qu'il en reste."""

    def setUp(self):
        super().setUp()
        self.racine = self.etat / "travaux"
        self.file = FileTravaux(self.racine)
        creer_voix(self.donnees)
        self.trace = self.tmp / "trace.jsonl"
        os.environ["FAUX_DOUBLER_TRACE"] = str(self.trace)
        self._verrou = open(self.racine / "interface.verrou", "a")
        fcntl.flock(self._verrou, fcntl.LOCK_EX)
        self.addCleanup(self._verrou.close)
        self.addCleanup(self.arreter)
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore", ResourceWarning)

    def arreter(self):
        # plus rien en attente : l'exécuteur détaché s'arrête de lui-même
        for d in self.racine.iterdir():
            if d.is_dir() and not (d / "etat.json").exists():
                (d / "annuler").touch()
        attendre(lambda: not self.file.executeur_actif(), 15)

    def demande(self, nom, attendre_jusqu_a=None):
        v = self.video(nom)
        d = travaux.demande_doublage([{"chemin": str(v), "duree_s": 30.0}], {"voix": "jules", "pas": 30},
                                     self.donnees, estimations={"kaggle": 600.0})
        if attendre_jusqu_a is not None:
            d = quota.mettre_en_attente(d, attendre_jusqu_a)
        return d

    def test_prochain_et_reprise(self):
        a = self.file.ajouter(self.demande("A.mp4", time.time() + 3600), demarrer=False)
        b = self.file.ajouter(self.demande("B.mp4"), demarrer=False)
        self.assertTrue(a.attend_le_quota)
        self.assertEqual(a.statut, "en_attente")
        self.assertFalse(b.attend_le_quota)
        # le travail prêt passe devant celui qui attend la remise à zéro
        self.assertEqual(executeur.prochain(self.racine), b.dossier)
        self.assertAlmostEqual(executeur.prochaine_reprise(self.racine), a.reprise)
        self.assertEqual(executeur.prochain(self.racine, maintenant=a.reprise + 1), a.dossier)
        # « Lancer maintenant » : la marque suffit
        (a.dossier / travaux.LANCER).touch()
        self.assertIsNone(a.reprise)
        self.assertFalse(a.attend_le_quota)
        self.assertEqual(executeur.prochain(self.racine), a.dossier)

    def test_annule_pendant_l_attente(self):
        a = self.file.ajouter(self.demande("A.mp4", time.time() + 3600), demarrer=False)
        (a.dossier / "annuler").touch()
        self.assertIsNone(executeur.prochain(self.racine))
        self.assertIsNone(executeur.prochaine_reprise(self.racine))
        a.actualiser()
        self.assertEqual(a.statut, "annule")

    def test_executeur_veille_puis_lance_a_l_heure(self):
        a = self.file.ajouter(self.demande("A.mp4", time.time() + 4))
        b = self.file.ajouter(self.demande("B.mp4"))
        self.assertTrue(attendre(lambda: (b.actualiser(), b.statut)[1] == "fini", 30))
        a.actualiser()
        self.assertEqual(a.statut, "en_attente")         # pas encore l'heure
        self.assertTrue(self.file.executeur_actif())     # l'exécuteur veille, la file gardée
        self.assertTrue(attendre(lambda: (a.actualiser(), a.statut)[1] == "fini", 30))
        self.assertTrue(attendre(lambda: not self.file.executeur_actif(), 15))   # puis s'arrête, file vide
        self.assertGreater(a.etat["debut"], b.etat["fin"])

    def test_lancer_maintenant(self):
        a = self.file.ajouter(self.demande("A.mp4", time.time() + 3600))
        self.assertTrue(attendre(self.file.executeur_actif, 10))
        time.sleep(0.5)
        a.actualiser()
        self.assertEqual(a.statut, "en_attente")
        self.assertTrue(self.file.lancer_maintenant(a))
        self.assertTrue(attendre(lambda: (a.actualiser(), a.statut)[1] == "fini", 30))

    def test_gpu_a_venir(self):
        self.file.ajouter(self.demande("A.mp4", time.time() + 3600), demarrer=False)   # après la remise à zéro
        self.file.ajouter(self.demande("B.mp4"), demarrer=False)
        self.file.ajouter({**self.demande("C.mp4"), "reprise": True}, demarrer=False)
        self.assertAlmostEqual(quota.gpu_a_venir(self.file.travaux()), 600.0)


class EssaiEtatComplet(EssaiIsole):
    def test_etat_avec_la_fausse_cli(self):
        os.environ["VOIXLA_KAGGLE"] = str(FAUX_KAGGLE)
        os.environ["FAUX_KAGGLE_QUOTA"] = "28.4,30"
        e = quota.etat({}, self.donnees, self.etat / "travaux", depot=self.tmp)
        self.assertEqual(e.source, "kaggle")
        self.assertAlmostEqual(e.reste_s, 1.6 * H, delta=60)
        self.assertTrue(e.alerte(2 * H))
        self.assertGreater(e.remise, time.time())

    def test_etat_sans_kaggle(self):
        e = quota.etat({"quota_h": 30}, self.donnees, self.etat / "travaux", depot=self.tmp)
        self.assertEqual(e.source, "local")
        self.assertIn("absente", e.raison)
        self.assertEqual(e.utilise_s, 0.0)
        self.assertEqual(e.reste_s, 30 * H)


class EssaiSecours(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        from unittest import mock
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.maison = Path(self._t.name)   # ni client, ni jeton, ni clé : rien n'est prêt
        env = {"HOME": self._t.name}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for v in ("VOIXLA_MODAL_PYTHON", "VOIXLA_RUNPODCTL", "MODAL_TOKEN_ID", "RUNPOD_API_KEY"):
            os.environ.pop(v, None)
        q = mock.patch.object(Path, "home", classmethod(lambda cls: self.maison))
        q.start()
        self.addCleanup(q.stop)

    def poser(self, *chemins):
        for c in chemins:
            f = self.maison / c
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x")

    def test_moyens_branches(self):
        self.assertEqual([m.cle for m in secours.MOYENS], ["ancien-pc", "modal", "runpod"])
        self.assertEqual([m.cle for m in secours.MOYENS if m.branche], ["modal", "runpod"])
        self.assertIs(secours.choisi({"secours": "ancien-pc"}), secours.PAR_CLE["ancien-pc"])
        self.assertEqual(secours.PAR_CLE["ancien-pc"].pret(), (False, "pas encore branché"))
        with self.assertRaises(NotImplementedError):
            secours.preparer_demande(secours.PAR_CLE["ancien-pc"], {"argv": ["doubler"]})
        self.assertTrue(secours.PAR_CLE["runpod"].payant)

    def test_pret_selon_les_fichiers(self):
        self.assertEqual(secours.proposer_pour({}), [])
        self.assertEqual(secours.proposer_pour({"secours": "modal"}), [], "client et jeton absents")
        self.assertIn("client modal absent", secours.PAR_CLE["modal"].pret()[1])
        self.poser("modal-venv/bin/python")
        self.assertIn("jeton Modal absent", secours.PAR_CLE["modal"].pret()[1])
        self.poser(".modal.toml")
        self.assertEqual(secours.PAR_CLE["modal"].pret(), (True, ""))
        self.assertEqual(secours.proposer_pour({"secours": "modal"}), [secours.PAR_CLE["modal"]])
        self.assertEqual(secours.proposer_pour({"secours": "runpod"}), [], "Runpod : jamais sans clé ni runpodctl")
        self.poser(".local/bin/runpodctl", ".runpod/config.toml")
        self.assertEqual(secours.proposer_pour({"secours": "runpod"}), [secours.PAR_CLE["runpod"]])

    def test_demande_pour_le_moyen(self):
        d = {"argv": ["doubler", "clip.mp4", "--calcul=auto", "--voix=jules"], "reglages": {"voix": "jules"}}
        r = secours.preparer_demande(secours.PAR_CLE["modal"], d)
        self.assertEqual(r["argv"], ["doubler", "clip.mp4", "--voix=jules", "--calcul=modal"])
        self.assertEqual((r["secours"], r["reglages"]["calcul"]), ("modal", "modal"))
        self.assertEqual(d["argv"][2], "--calcul=auto", "la demande d'origine n'est pas modifiée")

    def test_calcul_choisi(self):
        self.assertEqual(secours.calcul_choisi({}), "")
        self.assertEqual(secours.calcul_choisi({"calcul": "auto"}), "auto")
        self.assertEqual(secours.calcul_choisi({"calcul": "n'importe quoi"}), "")


if __name__ == "__main__":
    unittest.main()
