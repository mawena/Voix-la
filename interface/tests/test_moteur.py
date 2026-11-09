"""Essais de voixla.moteur : aide de doubler, commandes (avec et sans les options récentes), noms d'extraits, voix,
config.json, événements (--evenements) et lecture de la sortie texte (LectureTexte), avec le faux doubler."""

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from voixla import chemins, moteur, suivi

from .commun import FAUX_DOUBLER, VRAI_DOUBLER, EssaiIsole, commande_faux, creer_voix, lire_trace

RECENTES = {"--evenements", "--donnees", "--config", "--json", "--mix-v1"}
AVANT_LE_VRAI = set()    # options que le faux annonce avant le vrai doubler (aucune)
REGLAGES = {"voix": "jules", "mode": "auto", "octave": "auto", "pas": 30, "voix_db": None, "reverb_db": -13.0}


def local(annee, mois, jour, h, m, s=0):
    return datetime(annee, mois, jour, h, m, s).timestamp()


# --- L'aide et les capacités -----------------------------------------------------------------------------------------

class EssaiAide(unittest.TestCase):
    def test_options_reconnues(self):
        texte = """usage: doubler [--voix VOIX] [--mix-v1] [--donnees DIR] [--evenements FICHIER]
          [--voix-liste] [--json] [--config [CLE VALEUR ...]] [--remix] [--debut DEBUT] [--duree DUREE]"""
        self.assertEqual(moteur.analyser_aide(texte),
                         {"--mix-v1", "--donnees", "--evenements", "--voix-liste", "--json", "--config", "--remix",
                          "--debut", "--duree"})

    def test_pas_de_faux_positif(self):
        texte = "--jsonx --config-bis --donnees_vieilles --evenements-2 --comparer-pas --voix-listes"
        self.assertEqual(moteur.analyser_aide(texte), {"--comparer-pas"})
        self.assertEqual(moteur.analyser_aide(""), frozenset())
        self.assertEqual(moteur.analyser_aide(None), frozenset())

    @unittest.skipUnless(VRAI_DOUBLER.is_file(), "doubler.py absent")
    def test_le_faux_annonce_les_options_du_vrai(self):
        # « doubler --help » ne fait rien d'autre qu'afficher l'aide (argparse sort avant tout traitement)
        vrai = subprocess.run([sys.executable, str(VRAI_DOUBLER), "--help"], capture_output=True, text=True, timeout=30)
        faux = subprocess.run([sys.executable, str(FAUX_DOUBLER), "--help"], capture_output=True, text=True, timeout=30)
        self.assertEqual(vrai.returncode, 0)
        options_vrai = moteur.analyser_aide(vrai.stdout)
        self.assertTrue(RECENTES <= options_vrai, "le vrai doubler a perdu une option suivie")
        # AVANT_LE_VRAI : options que le faux annonce déjà alors qu'elles arrivent dans doubler par une autre branche
        # (à retirer de l'ensemble une fois la fusion faite) ; toutes les autres doivent être les mêmes
        self.assertEqual(moteur.analyser_aide(faux.stdout) - AVANT_LE_VRAI, options_vrai - AVANT_LE_VRAI)


class EssaiCapacites(EssaiIsole):
    def test_doubler_recent(self):
        caps = moteur.capacites()
        self.assertEqual(caps, frozenset(moteur.OPTIONS_SUIVIES) | {"extrait_par_video", "voix_par_chanteur"})
        self.assertTrue(moteur.disponible())
        self.assertIn("usage: doubler", moteur._capacites["aide"])

    def test_ancien_doubler(self):
        self.ancien_doubler()
        caps = moteur.capacites()
        # l'ancien doubler n'a ni les options récentes ni --reprendre (seules les options suivies sont comparées)
        self.assertEqual(caps & frozenset(moteur.OPTIONS_SUIVIES),
                         frozenset(moteur.OPTIONS_SUIVIES) - RECENTES - {"--reprendre", "--moteur", "--sans-nettoyage",
                                                                         "--sans-paroles", "--autotune",
                                                                         "--sans-autotune", "--autotune-gamme",
                                                                         "--gpu", "--passage", "--affectation"})
        self.assertTrue(moteur.disponible())

    def test_doubler_introuvable(self):
        os.environ["VOIXLA_DOUBLER"] = str(self.tmp / "nulle-part" / "doubler")
        self.assertEqual(moteur.capacites(), frozenset())
        self.assertFalse(moteur.disponible())
        self.assertIn("erreur", moteur._capacites)

    def test_cache_tant_que_le_fichier_ne_change_pas(self):
        copie = self.tmp / "faux.py"
        shutil.copy2(FAUX_DOUBLER, copie)
        os.environ["VOIXLA_DOUBLER"] = commande_faux(copie)
        os.environ["FAUX_DOUBLER_TRACE"] = str(self.tmp / "trace.jsonl")
        moteur.capacites()
        moteur.capacites()
        self.assertEqual(len(lire_trace(self.tmp / "trace.jsonl")), 1)      # un seul « --help »
        moteur.capacites(forcer=True)
        self.assertEqual(len(lire_trace(self.tmp / "trace.jsonl")), 2)
        t = copie.stat().st_mtime + 10
        os.utime(copie, (t, t))                                               # doubler mis à jour
        moteur.capacites()
        self.assertEqual(len(lire_trace(self.tmp / "trace.jsonl")), 3)
        self.assertEqual(lire_trace(self.tmp / "trace.jsonl")[-1]["argv"], ["--help"])

    def test_commande(self):
        os.environ["VOIXLA_DOUBLER"] = "python3 '/chemin avec espace/faux doubler.py' --x"
        self.assertEqual(moteur.commande(), ["python3", "/chemin avec espace/faux doubler.py", "--x"])
        del os.environ["VOIXLA_DOUBLER"]
        cmd = moteur.commande()
        self.assertTrue(cmd and (cmd[-1].endswith("doubler") or cmd[-1].endswith("doubler.py")))

    def test_commande_hors_du_depot(self):
        # sans VOIXLA_DOUBLER : « doubler » du PATH (/usr/bin/doubler du paquet), sinon doubler.py à côté du code
        del os.environ["VOIXLA_DOUBLER"]
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        os.environ["PATH"] = str(bin_)
        lib = self.tmp / "lib" / "voix-la"
        lib.mkdir(parents=True)
        (lib / "doubler.py").write_text("")
        with mock.patch.object(chemins, "DEPOT", lib):
            self.assertEqual(moteur.commande(), [sys.executable, str(lib / "doubler.py")])
            lanceur = bin_ / "doubler"
            lanceur.write_text("#!/bin/sh\n")
            lanceur.chmod(0o755)
            self.assertEqual(moteur.commande(), [str(lanceur)])

    def test_environnement(self):
        caps = moteur.capacites()
        self.assertEqual(moteur.environnement(self.donnees, caps),
                         {"PYTHONUNBUFFERED": "1", "VOIXLA_DONNEES": str(self.donnees)})
        anciennes = caps - RECENTES
        self.assertEqual(moteur.environnement(self.donnees, anciennes)["DOUBLER_VOIX"], str(self.donnees / "voix"))


# --- Construction des commandes --------------------------------------------------------------------------------------

class EssaiMoteurEtNettoyage(EssaiIsole):
    """--moteur et --sans-nettoyage : passés seulement si doubler les annonce."""

    def test_options_annoncees(self):
        caps = frozenset(moteur.OPTIONS_SUIVIES)
        a = moteur.args_doublage(["/v/A.mp4"], {"voix": "jules", "moteur": "seedvc", "sans_nettoyage": True}, "/d",
                                 caps=caps)
        self.assertIn("--moteur=seedvc", a)
        self.assertIn("--sans-nettoyage", a)

    def test_options_absentes(self):
        caps = frozenset(moteur.OPTIONS_SUIVIES) - {"--moteur", "--sans-nettoyage"}
        a = moteur.args_doublage(["/v/A.mp4"], {"voix": "jules", "moteur": "seedvc", "sans_nettoyage": True}, "/d",
                                 caps=caps)
        self.assertFalse(any(x.startswith("--moteur") or x == "--sans-nettoyage" for x in a))

    def test_moteur_par_defaut(self):
        a = moteur.args_doublage(["/v/A.mp4"], {"voix": "jules", "moteur": ""}, "/d",
                                 caps=frozenset(moteur.OPTIONS_SUIVIES))
        self.assertFalse(any(x.startswith("--moteur") for x in a))


class EssaiParoles(EssaiIsole):
    """--sans-paroles : passé seulement si doubler l'annonce ; défaut lu dans la clé « paroles » de sa configuration."""

    def test_sans_paroles_annonce(self):
        a = moteur.args_doublage(["/v/A.mp4"], {"voix": "jules", "sans_paroles": True}, "/d",
                                 caps=frozenset(moteur.OPTIONS_SUIVIES))
        self.assertIn("--sans-paroles", a)

    def test_sans_paroles_absent_de_l_aide(self):
        a = moteur.args_doublage(["/v/A.mp4"], {"voix": "jules", "sans_paroles": True}, "/d",
                                 caps=frozenset(moteur.OPTIONS_SUIVIES) - {"--sans-paroles"})
        self.assertNotIn("--sans-paroles", a)

    def test_paroles_par_defaut(self):
        a = moteur.args_doublage(["/v/A.mp4"], {"voix": "jules"}, "/d", caps=frozenset(moteur.OPTIONS_SUIVIES))
        self.assertNotIn("--sans-paroles", a)

    def test_le_faux_annonce_sans_paroles(self):
        self.assertIn("--sans-paroles", moteur.capacites())
        self.ancien_doubler()
        self.assertNotIn("--sans-paroles", moteur.capacites())

    def test_booleen(self):
        for v in (True, "oui", "OUI", " vrai ", "true", "1", 1):
            self.assertTrue(moteur.booleen(v, False), v)
        for v in (False, "non", "Non", "faux", "false", "0", 0):
            self.assertFalse(moteur.booleen(v, True), v)
        for v in (None, "", "peut-être", [], {}):
            self.assertTrue(moteur.booleen(v, True), v)
            self.assertFalse(moteur.booleen(v, False), v)

    def test_lecture_de_la_configuration(self):
        self.assertTrue(moteur.paroles_par_defaut(None))
        self.assertTrue(moteur.paroles_par_defaut({}))
        self.assertTrue(moteur.paroles_par_defaut({"contenu": {}}))
        self.assertFalse(moteur.paroles_par_defaut({"contenu": {"paroles": False}}))
        self.assertFalse(moteur.paroles_par_defaut({"contenu": {"paroles": "non"}}))
        self.assertFalse(moteur.paroles_par_defaut({"paroles": False}))          # « doubler --config --json »
        self.assertTrue(moteur.paroles_par_defaut({"contenu": "abîmé", "paroles": "oui"}))

    def test_ecriture_directe_en_booleen(self):
        # VOIXLA_CONFIG (essais) : écrite directement, en vrai booléen, et relue comme telle
        for texte, attendu in (("non", False), ("oui", True)):
            ok, _ = moteur.ecrire_config("paroles", texte)
            self.assertTrue(ok)
            conf = chemins.lire_config()
            self.assertIs(conf["paroles"], attendu)
            self.assertIs(moteur.paroles_par_defaut(moteur.config_effective()), attendu)

    def test_le_faux_ecrit_la_cle(self):
        env = {**os.environ, "FAUX_DOUBLER_CONFIG": str(self.config)}
        r = subprocess.run([sys.executable, str(FAUX_DOUBLER), "--config", "paroles", "non"], capture_output=True,
                           text=True, timeout=30, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIs(json.loads((self.config / "config.json").read_text(encoding="utf-8"))["paroles"], False)
        r = subprocess.run([sys.executable, str(FAUX_DOUBLER), "--config", "paroles", "bof"], capture_output=True,
                           text=True, timeout=30, env=env)
        self.assertNotEqual(r.returncode, 0)


class EssaiCommandes(EssaiIsole):
    def setUp(self):
        super().setUp()
        self.caps = frozenset(moteur.OPTIONS_SUIVIES)
        self.anciennes = self.caps - RECENTES
        self.cmd = moteur.commande()

    def test_doublage_recent(self):
        r = {**REGLAGES, "mode": "chant", "octave": "12", "pas": 50, "voix_db": 4.0, "reverb_db": -13.0, "mix_v1": True}
        a = moteur.args_doublage(["/v/A.mp4", "/v/B b.mkv"], r, self.donnees, extrait=(60.0, 40.0),
                                 evenements="/e/ev.jsonl", caps=self.caps)
        self.assertEqual(a, [*self.cmd, "/v/A.mp4", "/v/B b.mkv", "--voix=jules", "--mode=chant", "--octave=+12",
                             "--pas=50", "--voix-db=4", "--reverb-db=-13", "--mix-v1", "--debut=60", "--duree=40",
                             "--donnees", str(self.donnees), "--evenements=/e/ev.jsonl"])

    def test_doublage_ancien_doubler(self):
        r = {**REGLAGES, "mix_v1": True, "voix_db": 2.5}
        a = moteur.args_doublage(["/v/A.mp4"], r, self.donnees, evenements="/e/ev.jsonl", caps=self.anciennes)
        self.assertEqual(a, [*self.cmd, "/v/A.mp4", "--voix=jules", "--pas=30", "--voix-db=2.5", "--reverb-db=-13",
                             f"--sortie={self.donnees / 'sorties'}"])

    def test_reglages_par_defaut_omis(self):
        a = moteur.args_doublage(["/v/A.mp4"], {"voix": "", "mode": "auto", "octave": "auto"}, self.donnees,
                                 caps=self.caps)
        self.assertEqual(a, [*self.cmd, "/v/A.mp4", "--donnees", str(self.donnees)])
        a = moteur.args_doublage(["/v/A.mp4"], {"octave": 0}, self.donnees, extrait=(0, None), caps=self.caps)
        self.assertIn("--octave=0", a)
        self.assertIn("--debut=0", a)
        self.assertFalse(any(x.startswith("--duree") for x in a))
        a = moteur.args_doublage(["/v/A.mp4"], {"octave": "-12"}, self.donnees, extrait=(12.5, 7.25), caps=self.caps)
        n = len(self.cmd) + 1
        self.assertEqual(a[n:n + 3], ["--octave=-12", "--debut=12.5", "--duree=7.25"])

    def test_le_faux_doubler_accepte_la_commande(self):
        """La commande construite passe telle quelle (recent puis ancien doubler) et le dossier est celui attendu."""
        creer_voix(self.donnees)
        v = self.video("Clip.mp4")
        for ancien in (False, True):
            with self.subTest(ancien=ancien):
                if ancien:
                    self.ancien_doubler()
                shutil.rmtree(self.donnees / "sorties", ignore_errors=True)
                caps = moteur.capacites()
                ev = self.tmp / f"ev-{ancien}.jsonl"
                argv = moteur.args_doublage([str(v)], {**REGLAGES, "mix_v1": True}, self.donnees, extrait=(61.5, 20),
                                            evenements=str(ev), caps=caps)
                code, sortie = moteur.lancer(argv, moteur.environnement(self.donnees, caps), timeout=60)
                self.assertEqual(code, 0, sortie)
                attendu = chemins.dossier_sorties(self.donnees) / moteur.nom_dossier(v, (61.5, 20))
                self.assertTrue((attendu / "Clip - voix Jules.mp4").exists(), sortie)
                self.assertEqual(ev.exists(), not ancien)
                self.assertEqual("mixage chant v1 :" in sortie, not ancien)

    def test_ajouter_voix(self):
        f = self.tmp / "enregistrement.wav"
        f.write_bytes(b"RIFF")
        self.assertEqual(moteur.args_ajouter_voix("marie", f, self.donnees, caps=self.caps),
                         [*self.cmd, "--ajouter-voix", "marie", str(f), "--donnees", str(self.donnees)])
        ok, message, alertes = moteur.ajouter_voix("marie", f, self.donnees)
        self.assertTrue(ok, message)
        self.assertTrue(message.startswith("voix marie ajoutée"), message)
        self.assertEqual(alertes, ["faux doubler : aucune analyse"])
        self.assertTrue((self.donnees / "voix" / "marie" / "voix.json").exists())
        ok, message, _ = moteur.ajouter_voix("Marie!", f, self.donnees)
        self.assertFalse(ok)
        self.assertIn("nom de voix", message)


class EssaiExtraits(unittest.TestCase):
    def test_fmt_t(self):
        self.assertEqual(moteur.fmt_t(60), "1m00,0")
        self.assertEqual(moteur.fmt_t(605.5), "10m05,5")
        self.assertEqual(moteur.fmt_t(0), "0m00,0")

    def test_nom_dossier(self):
        self.assertEqual(moteur.nom_dossier("/v/Clip.mp4"), "Clip")
        self.assertEqual(moteur.nom_dossier("/v/Clip.mp4", (60, 40)), "Clip (extrait 1m00,0+0m40,0)")
        self.assertEqual(moteur.nom_dossier("/v/Clip.mp4", (60, None)), "Clip (extrait 1m00,0)")
        self.assertEqual(moteur.nom_dossier("/v/Clip.mp4", (None, 30)), "Clip (extrait 0m00,0+0m30,0)")

    def test_extrait_du_nom(self):
        self.assertEqual(moteur.extrait_du_nom("Groupe Fictif - Chanson d'essai (clip) (extrait 1m00,0+0m40,0)"),
                         (60.0, 40.0))
        self.assertEqual(moteur.extrait_du_nom("Clip (extrait 1m00,0)"), (60.0, None))
        self.assertEqual(moteur.extrait_du_nom("Clip (extrait 10m05,5+1m02,3)"), (605.5, 62.3))
        self.assertIsNone(moteur.extrait_du_nom("Clip"))
        self.assertIsNone(moteur.extrait_du_nom("Clip (extrait 1m00,0+0m40,0) copie"))

    def test_aller_retour(self):
        for extrait in ((60.0, 40.0), (0.0, 30.0), (75.5, None), (3725.3, 12.0)):
            debut, duree = moteur.extrait_du_nom(moteur.nom_dossier("/v/x.mp4", extrait))
            self.assertAlmostEqual(debut, extrait[0], places=6)
            if extrait[1] is None:
                self.assertIsNone(duree)
            else:
                self.assertAlmostEqual(duree, extrait[1], places=6)

    def test_grouper_par_extrait(self):
        a = {"chemin": "a", "extrait": (60, 40)}
        b = {"chemin": "b", "extrait": [60.0, 40.0]}
        c = {"chemin": "c"}
        d = {"chemin": "d", "extrait": (60, None)}
        e = {"chemin": "e", "extrait": None}
        sans = frozenset(moteur.OPTIONS_SUIVIES)   # doubler sans « vidéo@début+durée »
        self.assertEqual(moteur.grouper_par_extrait([a, c, b, d, e], sans),
                         [((60.0, 40.0), [a, b]), (None, [c, e]), ((60.0, None), [d])])
        self.assertEqual(moteur.grouper_par_extrait([], sans), [])
        # avec « vidéo@début+durée » : tout le lot dans un seul appel, chaque vidéo porte son extrait
        avec = sans | {"extrait_par_video"}
        self.assertEqual(moteur.grouper_par_extrait([a, c, d], avec), [(None, [a, c, d])])
        argv = moteur.args_doublage([a, c, d], {"voix": "jules"}, "/d", caps=avec)
        self.assertIn("a@60+40", argv)
        self.assertIn("c", argv)
        self.assertIn("d@60", argv)
        self.assertFalse(any(x.startswith("--debut") for x in argv))


class EssaiRemix(EssaiIsole):
    def test_args_remix(self):
        caps = frozenset(moteur.OPTIONS_SUIVIES)
        resultat = self.donnees / "sorties" / "essais" / "Clip (extrait 1m00,0+0m40,0)"
        a = moteur.args_remix("/v/Clip.mp4", "jules", resultat, self.donnees, voix_db=2, reverb_db=-15, mix_v1=True,
                              caps=caps)
        self.assertEqual(a, [*moteur.commande(), "/v/Clip.mp4", "--remix", "--voix=jules",
                             f"--sortie={resultat.parent}", "--debut=60", "--duree=40", "--voix-db=2", "--reverb-db=-15",
                             "--mix-v1", "--donnees", str(self.donnees)])
        a = moteur.args_remix("/v/Clip.mp4", "jules", self.donnees / "sorties" / "Clip", self.donnees, mix_v1=True,
                              caps=caps - RECENTES)
        self.assertEqual(a, [*moteur.commande(), "/v/Clip.mp4", "--remix", "--voix=jules",
                             f"--sortie={self.donnees / 'sorties'}"])

    def test_remix_possible(self):
        v = self.video("Clip.mp4")
        ok, raison = moteur.remix_possible(None, self.tmp / "Clip")
        self.assertFalse(ok)
        self.assertIn("source.json", raison)
        ok, raison = moteur.remix_possible(self.tmp / "absente.mp4", self.tmp / "absente")
        self.assertFalse(ok)
        self.assertIn("introuvable", raison)
        self.assertEqual(moteur.remix_possible(v, self.tmp / "Clip (extrait 1m00,0+0m40,0)"), (True, ""))
        self.assertEqual(moteur.remix_possible(str(v), self.tmp / "Clip"), (True, ""))
        ok, raison = moteur.remix_possible(v, self.tmp / "Clip renommé")
        self.assertFalse(ok)
        self.assertIn("« Clip »", raison)

    def test_remixer_avec_le_faux_doubler(self):
        creer_voix(self.donnees)
        v = self.video("Clip.mp4")
        caps = moteur.capacites()
        argv = moteur.args_doublage([str(v)], REGLAGES, self.donnees, extrait=(60, 40), caps=caps)
        code, sortie = moteur.lancer(argv, moteur.environnement(self.donnees, caps))
        self.assertEqual(code, 0, sortie)
        resultat = self.donnees / "sorties" / "Clip (extrait 1m00,0+0m40,0)"
        ok, sortie = moteur.remixer(v, "jules", resultat, self.donnees, voix_db=2)
        self.assertTrue(ok, sortie)
        self.assertIn("=== Clip.mp4 (remix, voix jules)", sortie)
        self.assertIn("voix +2 dB", sortie)
        ok, sortie = moteur.remixer(v, "jules", self.donnees / "sorties" / "Clip", self.donnees)
        self.assertFalse(ok)          # pas de travail/ pour la vidéo entière
        self.assertIn("ÉCHEC", sortie)


# --- Voix ------------------------------------------------------------------------------------------------------------

class EssaiVoix(EssaiIsole):
    def setUp(self):
        super().setUp()
        creer_voix(self.donnees, "jules", "Jules", 110.0)
        creer_voix(self.donnees, "jules-b", "Jules (B)", 98.0, alertes=["bruité"])
        creer_voix(self.donnees, "sans-ref", "Sans référence", None, reference=False)
        os.environ["FAUX_DOUBLER_TRACE"] = str(self.tmp / "trace.jsonl")

    def test_liste_par_doubler(self):
        chemins.ecrire_json(chemins.fichier_config(), {"voix": "jules"})      # voix par défaut de doubler
        voix = moteur.lister_voix(self.donnees)
        self.assertEqual([v["id"] for v in voix], ["jules", "jules-b", "sans-ref"])
        self.assertEqual([v["nom"] for v in voix], ["Jules", "Jules (B)", "Sans référence"])
        self.assertEqual([v["defaut_doubler"] for v in voix], [True, False, False])
        self.assertEqual(moteur.voix_par_defaut(voix), "jules")
        self.assertEqual(voix[0]["hauteur_hz"], 110.0)
        self.assertEqual(voix[0]["dossier"], self.donnees / "voix" / "jules")
        self.assertEqual(voix[1]["alertes"], ["bruité"])
        self.assertEqual(voix[2]["alertes"], ["reference.wav absente", "hauteur de parole manquante"])
        appels = [t["argv"] for t in lire_trace(self.tmp / "trace.jsonl") if "argv" in t]
        self.assertIn(["--voix-liste", "--json", "--donnees", str(self.donnees)], appels)

    def test_liste_ancien_doubler(self):
        self.ancien_doubler()
        voix = moteur.lister_voix(self.donnees)
        self.assertEqual([v["id"] for v in voix], ["jules", "jules-b", "sans-ref"])
        self.assertNotIn("defaut_doubler", voix[0])
        appels = [t["argv"] for t in lire_trace(self.tmp / "trace.jsonl") if "argv" in t]
        self.assertEqual(appels, [["--help"]])       # lecture directe, sans « --voix-liste »

    def test_voix_json_illisible(self):
        d = self.donnees / "voix" / "casse"
        d.mkdir()
        (d / "voix.json").write_text("{", encoding="utf-8")
        voix = {v["id"]: v for v in moteur.lister_voix(self.donnees)}
        # doubler échoue sur ce fichier : repli sur la lecture directe, qui garde la voix avec une alerte
        self.assertEqual(sorted(voix), ["casse", "jules", "jules-b", "sans-ref"])
        self.assertIn("voix.json illisible", voix["casse"]["alertes"])
        self.assertEqual(voix["casse"]["nom"], "Casse")

    def test_aucune_voix_par_defaut(self):
        # plusieurs voix et aucune clé « voix » : doubler n'en désigne aucune, l'interface ne présélectionne rien
        voix = moteur.lister_voix(self.donnees)
        self.assertEqual([v["defaut_doubler"] for v in voix], [False, False, False])
        self.assertIsNone(moteur.voix_par_defaut(voix))
        self.assertIsNone(moteur.voix_par_defaut(voix, moteur.config_effective()))
        # et doubler sans --voix refuse en disant comment choisir
        code, sortie = moteur.lancer([*moteur.commande(), str(self.video()), "--donnees", str(self.donnees)],
                                     timeout=60)
        self.assertNotEqual(code, 0)
        self.assertIn("aucune par défaut", sortie)

    def test_seule_voix(self):
        for d in ("jules-b", "sans-ref"):
            shutil.rmtree(self.donnees / "voix" / d)
        voix = moteur.lister_voix(self.donnees)
        self.assertEqual([v["defaut_doubler"] for v in voix], [True])
        self.assertEqual(moteur.voix_par_defaut([{**voix[0], "defaut_doubler": False}]), "jules")

    def test_voix_par_defaut_de_la_configuration(self):
        voix = [{"id": "jules"}, {"id": "marie"}, {"id": "paul"}]
        self.assertEqual(moteur.voix_par_defaut(voix, {"voix": "marie"}), "marie")                    # config.json
        self.assertEqual(moteur.voix_par_defaut(voix, {"voix": "/d/voix", "voix_defaut": "paul"}), "paul")
        self.assertEqual(moteur.voix_par_defaut(voix, {"voix": "/d/voix", "contenu": {"voix": "marie"}}), "marie")
        self.assertIsNone(moteur.voix_par_defaut(voix, {"voix": "/d/voix"}))         # dossier des voix, pas un nom
        self.assertIsNone(moteur.voix_par_defaut(voix, {"voix_defaut": "absente"}))  # voix effacée depuis
        self.assertIsNone(moteur.voix_par_defaut(voix, None))
        self.assertEqual(moteur.voix_par_defaut([{"id": "jules"}, {"id": "paul", "defaut_doubler": True}],
                                                {"voix": "jules"}), "paul")         # annoncée par --voix-liste
        self.assertEqual(moteur.voix_par_defaut([{"id": "paul"}], {"voix_defaut": "absente"}), "paul")
        self.assertIsNone(moteur.voix_par_defaut([]))
        self.assertIsNone(moteur.voix_de_config({"voix": "Pas un nom !"}))
        self.assertEqual(moteur.voix_de_config({"voix": {"defaut": "marie"}}), "marie")

    def test_lire_liste_json(self):
        self.assertEqual(moteur._lire_liste_json('[{"id": "a"}, 3]'), [{"id": "a"}])
        self.assertEqual(moteur._lire_liste_json('ATTENTION : x\n[{"id": "a"}]'), [{"id": "a"}])
        self.assertEqual(moteur._lire_liste_json('{"voix": {"a": {"nom": "A"}, "b": 1}}'),
                         [{"id": "a", "nom": "A"}, {"id": "b"}])
        self.assertEqual(moteur._lire_liste_json('{"liste": [{"id": "a"}]}'), [{"id": "a"}])
        self.assertIsNone(moteur._lire_liste_json("rien"))
        self.assertIsNone(moteur._lire_liste_json('{"autre": 1}'))


# --- config.json -----------------------------------------------------------------------------------------------------

class EssaiConfig(EssaiIsole):
    """config.json : avec VOIXLA_CONFIG (essais), l'interface lit et écrit le fichier d'essai elle-même (le vrai doubler
    lirait toujours ~/.config/voix-la/config.json) ; sans elle, elle passe par « doubler --config ». Pour ce second
    cas, le faux doubler lit son fichier dans FAUX_DOUBLER_CONFIG et chemins.fichier_config est redirigé : rien ne
    touche ~/.config."""

    def par_doubler(self):
        """Comme hors essai : VOIXLA_CONFIG retirée ; config.json « de doubler » dans un dossier temporaire."""
        del os.environ["VOIXLA_CONFIG"]
        os.environ["FAUX_DOUBLER_CONFIG"] = str(self.tmp / "config-doubler")
        interface = self.tmp / "config-interface" / "config.json"
        rustine = mock.patch.object(chemins, "fichier_config", return_value=interface)
        rustine.start()
        self.addCleanup(rustine.stop)
        return self.tmp / "config-doubler" / "config.json", interface

    def test_config_par_doubler(self):
        f, interface = self.par_doubler()
        chemins.ecrire_json(f, {"kaggle": {"compte": "moi", "noyau": "mon-noyau"}})
        c = moteur.config_effective()
        self.assertTrue(c["par_doubler"])
        self.assertEqual(c["config"], str(f))
        self.assertEqual(c["donnees"], str(self.donnees))
        self.assertEqual(c["source_donnees"], "VOIXLA_DONNEES")
        self.assertEqual(c["kaggle"], {"compte": "moi", "jeu": "moi/doublage-voix-entrees", "noyau": "moi/mon-noyau",
                                       "outils": "moi/doublage-outils", "machine": "NvidiaTeslaT4"})
        self.assertEqual(c["gpu"], "auto")
        self.assertEqual(c["jeton_kaggle"], "absent")
        self.assertFalse(interface.exists())
        self.assertIsNone(moteur.voix_de_config(c))
        chemins.ecrire_json(f, {"voix": "marie"})
        c = moteur.config_effective()
        self.assertEqual((c["voix_defaut"], moteur.voix_de_config(c)), ("marie", "marie"))

    def test_config_reconstruite(self):
        for ancien in (False, True):
            with self.subTest(ancien=ancien):
                if ancien:
                    self.ancien_doubler()
                os.environ["VOIXLA_DONNEES"] = str(self.donnees)
                chemins.ecrire_json(chemins.fichier_config(), {"kaggle": {"compte": "moi"}})
                c = moteur.config_effective()
                self.assertFalse(c["par_doubler"])          # VOIXLA_CONFIG posée, ou doubler sans --config
                self.assertEqual(c["config"], str(self.config / "config.json"))
                self.assertEqual(c["donnees"], str(self.donnees))
                self.assertEqual(c["source_donnees"], "VOIXLA_DONNEES")
                self.assertEqual(c["voix"], str(self.donnees / "voix"))
                self.assertEqual(c["kaggle"]["noyau"], "moi/doublage-voix-gpu")
                del os.environ["VOIXLA_DONNEES"]
                c = moteur.config_effective()
                self.assertEqual(c["source_donnees"], "défaut")
                self.assertEqual(c["donnees"], str(chemins.DONNEES_DEFAUT))
                chemins.ecrire_cle_config("donnees", str(self.tmp / "d"))
                self.assertEqual(moteur.config_effective()["source_donnees"], str(chemins.fichier_config()))

    def test_ecrire_config_directement(self):
        for ancien in (False, True):
            with self.subTest(ancien=ancien):
                if ancien:
                    chemins.fichier_config().unlink()
                    self.ancien_doubler()
                os.environ["FAUX_DOUBLER_TRACE"] = str(self.tmp / f"trace-{ancien}.jsonl")
                self.assertEqual(moteur.ecrire_config("kaggle.compte", "moi"), (True, ""))
                self.assertEqual(moteur.ecrire_config("donnees", str(self.tmp / "d")), (True, ""))
                self.assertEqual(chemins.lire_config(), {"kaggle": {"compte": "moi"}, "donnees": str(self.tmp / "d")})
                self.assertEqual(moteur.ecrire_config("donnees", None), (True, ""))
                self.assertEqual(chemins.lire_config(), {"kaggle": {"compte": "moi"}})
                appels = [t["argv"] for t in lire_trace(self.tmp / f"trace-{ancien}.jsonl") if "argv" in t]
                self.assertFalse(any("--config" in a for a in appels), appels)

    def test_ecrire_config_par_doubler(self):
        f, interface = self.par_doubler()
        ok, message = moteur.ecrire_config("kaggle.compte", "moi")
        self.assertTrue(ok, message)
        self.assertEqual(message, f"kaggle.compte = moi  ({f})")
        ok, message = moteur.ecrire_config("donnees", str(self.tmp / "d"))
        self.assertTrue(ok, message)
        self.assertEqual(chemins.lire_json(f), {"kaggle": {"compte": "moi"}, "donnees": str(self.tmp / "d")})
        self.assertFalse(interface.exists())

    def test_cle_refusee_par_doubler(self):
        self.par_doubler()
        ok, message = moteur.ecrire_config("inconnue", "x")
        self.assertFalse(ok)
        self.assertIn("clé inconnue", message)

    # Effacer une clé : doubler --config ne sait pas le faire (il prendrait "" pour le dossier courant), donc
    # ecrire_config écrit directement config.json, qui est en vrai le même fichier que celui de doubler.
    def test_effacer_le_dossier_de_donnees_par_doubler(self):
        f, _ = self.par_doubler()
        rustine = mock.patch.object(chemins, "fichier_config", return_value=f)   # un seul fichier, comme en vrai
        rustine.start()
        self.addCleanup(rustine.stop)
        moteur.ecrire_config("donnees", str(self.tmp / "d"))
        ok, _ = moteur.ecrire_config("donnees", None)
        self.assertTrue(ok)
        self.assertNotIn("donnees", chemins.lire_json(f, {}))


# --- Événements (--evenements) ---------------------------------------------------------------------------------------

class EssaiEvenements(unittest.TestCase):
    def test_ligne_du_vrai_format(self):
        ligne = ('{"t": "2026-11-04T09:20:01.123+01:00", "type": "etape", "video": "Clip (extrait 1m00,0+0m40,0)", '
                 '"etape": "extraction", "etat": "debut", "message": "son de Clip.mp4"}\n')
        e = moteur.analyser_evenement(ligne)
        attendu = datetime(2026, 11, 4, 9, 20, 1, 123000, tzinfo=timezone(timedelta(hours=1))).timestamp()
        self.assertAlmostEqual(e["t"], attendu, places=3)
        self.assertEqual((e["type"], e["etape"], e["etat"], e["video"]),
                         ("etape", "extraction", "debut", "Clip (extrait 1m00,0+0m40,0)"))

    def test_autres_types(self):
        k = moteur.analyser_evenement('{"t": "2026-11-04T09:21:00.000+00:00", "type": "kaggle", "video": "A", '
                                      '"statut": "running", "depuis_s": 80, "estimation_s": 300}')
        self.assertEqual((k["statut"], k["depuis_s"], k["estimation_s"]), ("running", 80, 300))
        s = moteur.analyser_evenement('{"t": "2026-11-04T09:30:00.000+00:00", "type": "sortie", "video": "A", '
                                      '"mp4": "/s/A/A - voix Jules.mp4", "mp3": "/s/A/A - voix Jules.mp3", '
                                      '"rapport": "/s/A/travail/jules/rapport.json"}')
        self.assertEqual(s["mp3"], "/s/A/A - voix Jules.mp3")
        f = moteur.analyser_evenement('{"t": "2026-11-04T09:31:00.000Z", "type": "fin", "video": null, "code": 0}')
        self.assertIsNone(f["video"])
        self.assertEqual(f["t"], datetime(2026, 11, 4, 9, 31, tzinfo=timezone.utc).timestamp())
        self.assertEqual(moteur.analyser_evenement('{"t": 5, "type": "erreur", "video": 12}')["video"], "12")

    def test_lignes_illisibles(self):
        for ligne in ("", "   \n", "{pas du json", "[1, 2]", '"texte"', '{"t": 1}'):
            self.assertIsNone(moteur.analyser_evenement(ligne), ligne)

    def test_lire_instant(self):
        self.assertEqual(moteur.lire_instant(12), 12.0)
        self.assertEqual(moteur.lire_instant("1730000000.5"), 1730000000.5)
        self.assertEqual(moteur.lire_instant("2026-11-04T08:00:00+00:00"),
                         datetime(2026, 11, 4, 8, tzinfo=timezone.utc).timestamp())
        avant = time.time()
        self.assertGreaterEqual(moteur.lire_instant(None), avant)
        self.assertGreaterEqual(moteur.lire_instant("hier"), avant)


# --- Sortie texte (repli sans --evenements) ---------------------------------------------------------------------------

def sortie_lot(mixage="mixage chant v2 : voix source -20.3 dB, voix +4 dB, reverb -13 dB sous la voix ; « s » adoucis, "
                      "nuances non suivies (trop peu de voix commune)"):
    """Sortie texte d'un lot de deux vidéos, au format de doubler (lignes de dire() et print())."""
    return f"""09:00:01 === A.mp4 → /s/A (extrait 1m00,0+0m40,0)
09:00:02 extrait : 59.961 s (image clé avant 60 s), 40.040 s
09:00:03 === B.mkv → /s/B (extrait 1m00,0+0m40,0)
09:00:04 extrait : 59.961 s (image clé avant 60 s), 40.040 s
09:00:10 Kaggle : envoi du son de 2 vidéo(s) (13 Mo en FLAC au lieu de 28 Mo en WAV) et de la voix jules dans le jeu privé moi/doublage-voix-entrees
09:01:00 Kaggle : noyau GPU lancé (Kernel version 7 successfully pushed)
09:01:20 Kaggle : queued (0.3 min)
09:01:40 Kaggle : running (0.5 min)
09:06:00 Kaggle : complete (5.0 min)
09:06:30 GPU Kaggle : 5.5 min depuis l'envoi (démarrage 3.3 s, installation 41 s, séparation 60 s, modèles 35 s, noyau 5.0 min ; estimé 6.0 min) ; rapatriement 20 s
09:06:31 === A.mp4
09:06:35 {mixage}
09:06:37 mode chant (détecté : musique sous la voix -8.5 dB, tonalité 0.62, notes tenues 0.31) ; modèle chant44k, 30 pas
  hauteur par passage (dt = demi-tons) :
    00:00.0–00:40.0  voix homme  f0  180 Hz  → +0 dt  (voix seule (homme))
09:06:38 fini : /s/A (extrait 1m00,0+0m40,0)/A - voix Jules.mp4
         /s/A (extrait 1m00,0+0m40,0)/A - voix Jules.mp3
09:06:39 === B.mkv
09:06:43 {mixage}
09:06:45 mode chant (détecté : musique sous la voix -8.5 dB, tonalité 0.62, notes tenues 0.31) ; modèle chant44k, 30 pas
  hauteur par passage (dt = demi-tons) :
    00:00.0–00:40.0  voix femme  f0  330 Hz  → -12 dt  (voix seule (femme))
09:06:46 fini : /s/B (extrait 1m00,0+0m40,0)/B - voix Jules.mkv
         /s/B (extrait 1m00,0+0m40,0)/B - voix Jules.mp3
"""


def lire_texte(texte, base, nb_videos=2, type_travail="doublage", code=0):
    """(événements, Avancement) déduits d'une sortie texte, comme Travail.actualiser sans --evenements."""
    lecture = moteur.LectureTexte(base)
    av = suivi.Avancement(nb_videos=nb_videos, type_travail=type_travail)
    evs = []
    for l in texte.splitlines():
        for e in lecture.ligne(l):
            evs.append(e)
            av.appliquer(e)
    evs += lecture.terminer()
    if code is not None:
        av.terminer(code, t=lecture.dernier_t)
    return evs, av


class EssaiLectureTexte(unittest.TestCase):
    def setUp(self):
        self.base = local(2026, 11, 4, 8, 59)

    def etapes(self, evs):
        return [(e["etape"], e["etat"], e.get("video")) for e in evs if e["type"] == "etape"]

    def test_evenements_deduits(self):
        evs, _ = lire_texte(sortie_lot(), self.base)
        etapes = self.etapes(evs)
        self.assertEqual(etapes[:8], [
            ("extraction", "debut", "A.mp4"), ("extraction", "fin", "A.mp4"), ("extraction", "debut", "B.mkv"),
            ("extraction", "fin", "B.mkv"), ("envoi", "debut", None), ("envoi", "fin", None),
            ("attente", "debut", None), ("attente", "fin", None)])
        self.assertIn(("kaggle", "debut", None), etapes)
        self.assertIn(("rapatriement", "fin", None), etapes)
        self.assertIn(("mix", "debut", "A.mp4"), etapes)
        self.assertIn(("mix", "fin", "A.mp4"), etapes)
        t0 = next(e["t"] for e in evs if e["type"] == "etape")
        self.assertEqual(t0, local(2026, 11, 4, 9, 0, 1))
        statuts = [(e["statut"], e["depuis_s"]) for e in evs if e["type"] == "kaggle"]
        self.assertEqual(statuts, [("queued", 18.0), ("running", 30.0), ("complete", 300.0)])
        sorties = [(e["chemin"], e["video"]) for e in evs if e["type"] == "sortie"]
        self.assertEqual(sorties, [
            ("/s/A (extrait 1m00,0+0m40,0)/A - voix Jules.mp4", "A.mp4"),
            ("/s/A (extrait 1m00,0+0m40,0)/A - voix Jules.mp3", "A.mp4"),
            ("/s/B (extrait 1m00,0+0m40,0)/B - voix Jules.mkv", "B.mkv"),
            ("/s/B (extrait 1m00,0+0m40,0)/B - voix Jules.mp3", "B.mkv")])
        messages = [e["message"] for e in evs if e["type"] == "message"]
        self.assertTrue(any(m.startswith("extrait : ") for m in messages))
        self.assertTrue(any(m.startswith("GPU Kaggle : ") for m in messages))
        self.assertTrue(any(m.startswith("mode chant (") for m in messages))

    def test_avancement_d_un_lot(self):
        _, av = lire_texte(sortie_lot(), self.base)
        etats = {n: av.etapes[n].etat for n in av.ordre}
        for n in ("extraction", "envoi", "attente", "kaggle", "rapatriement", "mix", "fini"):
            self.assertEqual(etats[n], "fait", n)
        self.assertEqual(len(av.sorties), 4)
        self.assertEqual(av.etapes["kaggle"].cumul_s, 260.0)     # 09:01:40 → 09:06:00
        self.assertEqual(av.statut_kaggle(), "terminé")
        self.assertEqual(av.resume(), "Fini")

    def test_ancienne_ligne_de_mixage(self):
        evs, av = lire_texte(sortie_lot("mixage chant : voix source -20.3 dB, voix +4 dB, reverb -13 dB sous la voix"),
                             self.base)
        self.assertIn(("video", "debut", "A.mp4"), self.etapes(evs))
        self.assertIn(("video", "fin", "B.mkv"), self.etapes(evs))
        self.assertEqual(av.etapes["video"].etat, "fait")

    # doubler écrit « mixage chant v2 : … » (ou v1) depuis le mixage v2 : l'étape « Remise sur la vidéo » doit être vue
    def test_ligne_de_mixage_v2(self):
        evs, av = lire_texte(sortie_lot(), self.base)
        self.assertIn(("video", "debut", "A.mp4"), self.etapes(evs))
        self.assertEqual(av.etapes["video"].etat, "fait")

    def test_passage_de_minuit(self):
        base = local(2026, 11, 4, 23, 59, 50)
        lecture = moteur.LectureTexte(base)
        e1 = lecture.ligne("23:59:55 === A.mp4 → /s/A")[0]
        e2 = lecture.ligne("00:00:05 Kaggle : envoi du son de 1 vidéo(s) (1 Mo en FLAC au lieu de 2 Mo en WAV) et de la "
                           "voix jules dans le jeu privé moi/doublage-voix-entrees")[0]
        self.assertEqual(e1["t"], local(2026, 11, 4, 23, 59, 55))
        self.assertEqual(e2["t"] - e1["t"], 10.0)

    def test_sessions_gpu_toutes_prises(self):
        texte = """10:00:00 === A.mp4 → /s/A
10:00:05 Kaggle : envoi du son de 1 vidéo(s) (1 Mo en FLAC au lieu de 2 Mo en WAV) et de la voix jules dans le jeu privé moi/j
10:01:00 Kaggle : sessions GPU toutes prises, attente d'une place
10:09:00 Kaggle : noyau GPU lancé (Kernel version 8 successfully pushed)
10:09:20 Kaggle : running (0.3 min)
"""
        evs, av = lire_texte(texte, local(2026, 11, 4, 9, 59), nb_videos=1, code=None)
        self.assertEqual([x for x in self.etapes(evs) if x[0] == "attente"], [("attente", "debut", None),
                                                                              ("attente", "fin", None)])
        self.assertEqual(av.etapes["attente"].cumul_s, 500.0)      # 10:01:00 → 10:09:20
        self.assertEqual(av.etape_courante(), "kaggle")
        self.assertEqual(av.kaggle["statut"], "running")

    def test_echec_de_la_session(self):
        texte = sortie_lot().split("09:06:00")[0] + """09:05:00 Kaggle : error (4.0 min)
09:05:30 ÉCHEC de la session Kaggle : Kaggle : noyau terminé en error sans résultat ; fin du journal :
torch.OutOfMemoryError: CUDA out of memory
2 vidéo(s) en échec : /v/A.mp4, /v/B.mkv
"""
        evs, av = lire_texte(texte, self.base, code=1)
        self.assertIn(("kaggle", "erreur", None), self.etapes(evs))
        erreurs = [e["message"] for e in evs if e["type"] == "erreur"]
        self.assertEqual(erreurs, ["Kaggle : noyau terminé en error sans résultat ; fin du journal :",
                                   "2 vidéo(s) en échec : /v/A.mp4, /v/B.mkv"])
        self.assertEqual(av.etapes["kaggle"].etat, "erreur")
        self.assertEqual(av.etapes["mix"].etat, "a_venir")
        self.assertEqual(av.resume(), "Échec")
        self.assertIn("2 vidéo(s) en échec : /v/A.mp4, /v/B.mkv", av.erreurs)

    def test_echec_d_une_video(self):
        texte = """10:00:00 === A.mp4
10:00:03 ÉCHEC /v/A.mp4 : travail incomplet (/s/A/travail/voix_source.wav manque) : relancer sans --remix
"""
        evs, _ = lire_texte(texte, local(2026, 11, 4, 9, 59), nb_videos=1, code=None)
        self.assertEqual(self.etapes(evs), [("mix", "debut", "A.mp4"), ("mix", "erreur", "A.mp4")])
        self.assertEqual(evs[-1], {"t": local(2026, 11, 4, 10, 0, 3), "type": "erreur", "video": "A.mp4",
                                   "message": "travail incomplet (/s/A/travail/voix_source.wav manque) : relancer sans --remix"})

    def test_remix(self):
        texte = """10:00:00 === A.mp4 (remix, voix jules)
10:00:02 mixage chant : voix source -20.3 dB, voix +4 dB, reverb -13 dB sous la voix
10:00:03 mode chant (détecté : musique sous la voix -8.5 dB, tonalité 0.62, notes tenues 0.31) ; modèle chant44k, 30 pas
10:00:04 fini : /s/A/A - voix Jules.mp4
         /s/A/A - voix Jules.mp3
"""
        evs, av = lire_texte(texte, local(2026, 11, 4, 9, 59), nb_videos=1, type_travail="remix")
        self.assertEqual(self.etapes(evs)[0], ("mix", "debut", "A.mp4"))
        self.assertEqual(evs[0]["message"], "remix")
        self.assertEqual([av.etapes[n].etat for n in av.ordre], ["fait", "fait", "fait"])
        self.assertEqual(av.sorties, ["/s/A/A - voix Jules.mp4", "/s/A/A - voix Jules.mp3"])

    def test_lignes_sans_heure_ignorees(self):
        lecture = moteur.LectureTexte(self.base)
        self.assertEqual(lecture.ligne("  hauteur par passage (dt = demi-tons) :"), [])
        self.assertEqual(lecture.ligne("    00:00.0–00:40.0  voix homme  f0  180 Hz  → +0 dt  (voix seule)"), [])
        self.assertEqual(lecture.ligne("Traceback (most recent call last):"), [])
        self.assertEqual(lecture.ligne("09:00:00 une ligne inconnue"), [])
        self.assertEqual(lecture.terminer(), [])


class EssaiLectureDuFauxDoubler(EssaiIsole):
    """La sortie texte réelle du faux doubler (même format que doubler), lue par LectureTexte."""

    def lancer(self):
        creer_voix(self.donnees)
        videos = [self.video("A.mp4"), self.video("B b.mkv")]
        caps = moteur.capacites()
        argv = moteur.args_doublage([str(v) for v in videos], REGLAGES, self.donnees, caps=caps)
        debut = time.time()
        code, sortie = moteur.lancer(argv, moteur.environnement(self.donnees, caps))
        self.assertEqual(code, 0, sortie)
        return lire_texte(sortie, debut)

    def test_ancien_doubler(self):
        self.ancien_doubler()
        evs, av = self.lancer()
        self.assertEqual({n: av.etapes[n].etat for n in av.ordre},
                         {n: "fait" for n in av.ordre})
        self.assertEqual(len(av.sorties), 4)
        self.assertTrue(all(Path(p).exists() for p in av.sorties))

    def test_doubler_recent(self):
        evs, av = self.lancer()
        self.assertEqual({n: av.etapes[n].etat for n in av.ordre}, {n: "fait" for n in av.ordre})
        self.assertEqual(len(av.sorties), 4)


if __name__ == "__main__":
    unittest.main()
