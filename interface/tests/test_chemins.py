"""Essais de voixla.chemins : dossiers, config.json partagé, préférences de l'interface."""

import importlib.util
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from voixla import chemins

from .commun import EssaiIsole


class EssaiDossiers(EssaiIsole):
    def test_variables_d_essai(self):
        self.assertEqual(chemins.dossier_config(), self.config)
        self.assertEqual(chemins.fichier_config(), self.config / "config.json")
        self.assertEqual(chemins.fichier_preferences(), self.config / "interface.json")
        self.assertEqual(chemins.dossier_etat(), self.etat)
        self.assertEqual(chemins.dossier_donnees(), self.donnees)
        self.assertEqual(chemins.dossier_ecoute(), self.tmp / "ecoute")

    def test_sans_variables(self):
        # seuls des chemins sont calculés ici : rien n'est lu ni écrit dans ~/.config ou ~/.local
        for v in ("VOIXLA_CONFIG", "VOIXLA_ETAT", "VOIXLA_ECOUTE"):
            del os.environ[v]
        self.assertEqual(chemins.dossier_config(), Path.home() / ".config" / "voix-la")
        self.assertEqual(chemins.dossier_etat(), Path.home() / ".local" / "state" / "voix-la")
        self.assertEqual(chemins.fichier_config(), Path.home() / ".config" / "voix-la" / "config.json")
        self.assertEqual(chemins.dossier_ecoute(), chemins.DEPOT / "banc" / "ecoute")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg-config")
        os.environ["XDG_STATE_HOME"] = str(self.tmp / "xdg-etat")
        self.assertEqual(chemins.dossier_config(), self.tmp / "xdg-config" / "voix-la")
        self.assertEqual(chemins.dossier_etat(), self.tmp / "xdg-etat" / "voix-la")
        # config.json reste là où doubler le lit, même avec XDG_CONFIG_HOME
        self.assertEqual(chemins.fichier_config(), Path.home() / ".config" / "voix-la" / "config.json")

    def test_depot_et_interface(self):
        self.assertTrue((chemins.DEPOT / "interface" / "voixla").is_dir())
        self.assertEqual(chemins.INTERFACE, chemins.DEPOT / "interface")

    def charger_copie(self, dossier_voixla):
        """voixla/chemins.py copié dans dossier_voixla, puis chargé à part (ses DEPOT et INTERFACE suivent la copie)."""
        dossier_voixla.mkdir(parents=True)
        shutil.copy2(Path(chemins.__file__), dossier_voixla / "chemins.py")
        spec = importlib.util.spec_from_file_location("chemins_copie", dossier_voixla / "chemins.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_disposition_du_depot(self):
        depot = self.tmp / "depot"
        c = self.charger_copie(depot / "interface" / "voixla")
        (depot / "doubler.py").write_text("")
        self.assertEqual((c.DEPOT, c.INTERFACE), (depot, depot / "interface"))

    def test_disposition_installee(self):
        # paquet Debian : /usr/lib/voix-la/{doubler.py, kaggle/, voixla/}
        lib = self.tmp / "usr" / "lib" / "voix-la"
        lib.mkdir(parents=True)
        (lib / "doubler.py").write_text("")
        c = self.charger_copie(lib / "voixla")
        self.assertEqual((c.DEPOT, c.INTERFACE), (lib, lib))

    def test_dossiers_derives(self):
        d = Path("/x/données")
        self.assertEqual(chemins.dossier_voix(d), d / "voix")
        self.assertEqual(chemins.dossier_sorties(d), d / "sorties")
        self.assertEqual(chemins.dossier_videos(d), d / "Vidéos")
        self.assertEqual(chemins.fichier_verrou_doubler(d), d / "sorties" / ".kaggle" / "verrou")

    def test_ordre_du_dossier_de_donnees(self):
        chemins.ecrire_json(chemins.fichier_config(), {"donnees": str(self.tmp / "par-config")})
        self.assertEqual(chemins.dossier_donnees(), self.donnees)           # VOIXLA_DONNEES d'abord
        del os.environ["VOIXLA_DONNEES"]
        self.assertEqual(chemins.dossier_donnees(), self.tmp / "par-config")   # puis config.json
        self.assertEqual(chemins.dossier_donnees({"donnees": "~/ailleurs"}), Path.home() / "ailleurs")
        self.assertEqual(chemins.dossier_donnees({}), chemins.DONNEES_DEFAUT)   # sinon le défaut
        self.assertEqual(chemins.DONNEES_DEFAUT, Path.home() / "Music" / "Voix-là")


class EssaiJson(EssaiIsole):
    def test_lire_json_absent_ou_illisible(self):
        defaut = {"a": [1]}
        lu = chemins.lire_json(self.tmp / "absent.json", defaut)
        self.assertEqual(lu, defaut)
        lu["a"].append(2)                       # une copie : le défaut n'est pas touché
        self.assertEqual(defaut, {"a": [1]})
        (self.tmp / "casse.json").write_text("{pas du json", encoding="utf-8")
        self.assertEqual(chemins.lire_json(self.tmp / "casse.json", []), [])
        self.assertIsNone(chemins.lire_json(self.tmp / "absent.json"))

    def test_ecrire_json(self):
        f = self.tmp / "a" / "b" / "fiche.json"
        chemins.ecrire_json(f, {"nom": "Mélodie", "liste": [1, 2]})
        texte = f.read_text(encoding="utf-8")
        self.assertIn("Mélodie", texte)            # pas d'échappement é
        self.assertTrue(texte.endswith("\n"))
        self.assertEqual(json.loads(texte), {"nom": "Mélodie", "liste": [1, 2]})
        self.assertEqual(sorted(p.name for p in f.parent.iterdir()), ["fiche.json"])   # pas de fichier temporaire

    def test_ecriture_ratee_garde_l_ancien_fichier(self):
        f = self.tmp / "fiche.json"
        chemins.ecrire_json(f, {"v": 1})
        with self.assertRaises(TypeError):
            chemins.ecrire_json(f, {"v": object()})
        self.assertEqual(json.loads(f.read_text(encoding="utf-8")), {"v": 1})
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith(".fiche.json.")], [])


class EssaiConfig(EssaiIsole):
    def test_lire_config(self):
        self.assertEqual(chemins.lire_config(), {})
        chemins.fichier_config().write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(chemins.lire_config(), {})        # pas un objet : ignoré
        chemins.fichier_config().write_text('{"kaggle": {"compte": "moi"}}', encoding="utf-8")
        self.assertEqual(chemins.lire_config(), {"kaggle": {"compte": "moi"}})

    def test_valeur_pointee(self):
        c = {"kaggle": {"compte": "moi", "vide": ""}, "donnees": "/d", "liste": [1]}
        self.assertEqual(chemins.valeur(c, "kaggle.compte"), "moi")
        self.assertEqual(chemins.valeur(c, "donnees"), "/d")
        self.assertEqual(chemins.valeur(c, "kaggle.vide", "x"), "")
        self.assertEqual(chemins.valeur(c, "kaggle.jeu", "défaut"), "défaut")
        self.assertIsNone(chemins.valeur(c, "donnees.sous"))
        self.assertIsNone(chemins.valeur(c, "liste.0"))

    def test_ecrire_cle_garde_les_autres(self):
        chemins.ecrire_json(chemins.fichier_config(), {"donnees": "/d", "kaggle": {"jeu": "j"}, "autre": 1})
        chemins.ecrire_cle_config("kaggle.compte", "moi")
        self.assertEqual(chemins.lire_config(), {"donnees": "/d", "kaggle": {"jeu": "j", "compte": "moi"}, "autre": 1})
        chemins.ecrire_cle_config("kaggle.jeu", None)       # None ou "" : la clé est retirée
        chemins.ecrire_cle_config("donnees", "")
        self.assertEqual(chemins.lire_config(), {"kaggle": {"compte": "moi"}, "autre": 1})

    def test_ecrire_cle_remplace_un_intermediaire(self):
        chemins.ecrire_json(chemins.fichier_config(), {"kaggle": "texte"})
        chemins.ecrire_cle_config("kaggle.compte", "moi")
        self.assertEqual(chemins.lire_config(), {"kaggle": {"compte": "moi"}})

    def test_kaggle(self):
        self.assertEqual(chemins.kaggle(), chemins.KAGGLE_DEFAUT)
        k = chemins.kaggle({"kaggle": {"compte": "moi", "noyau": "autre-noyau", "jeu": ""}})
        self.assertEqual(k, {"compte": "moi", "jeu": "doublage-voix-entrees", "noyau": "autre-noyau",
                             "outils": "doublage-outils"})
        chemins.ecrire_cle_config("kaggle.compte", "lu")
        self.assertEqual(chemins.kaggle()["compte"], "lu")

    def test_ref_kaggle(self):
        self.assertEqual(chemins.ref_kaggle("moi", "jeu"), "moi/jeu")
        self.assertEqual(chemins.ref_kaggle("moi", "autre/jeu"), "autre/jeu")
        self.assertIsNone(chemins.ref_kaggle("", "jeu"))
        self.assertIsNone(chemins.ref_kaggle(None, None))
        self.assertIsNone(chemins.ref_kaggle("moi", ""))


class EssaiPreferences(EssaiIsole):
    def test_defauts(self):
        p = chemins.lire_preferences()
        self.assertEqual(p, chemins.PREFERENCES_DEFAUT)
        p["prises_externes"]["autre"] = "/x"           # une copie profonde
        self.assertNotIn("autre", chemins.PREFERENCES_DEFAUT["prises_externes"])
        self.assertFalse(chemins.fichier_preferences().exists())

    def test_fichier_illisible_ou_pas_un_objet(self):
        chemins.fichier_preferences().write_text("[]", encoding="utf-8")
        self.assertEqual(chemins.lire_preferences(), chemins.PREFERENCES_DEFAUT)
        chemins.fichier_preferences().write_text("{", encoding="utf-8")
        self.assertEqual(chemins.lire_preferences(), chemins.PREFERENCES_DEFAUT)

    def test_cles_inconnues_ignorees(self):
        chemins.ecrire_json(chemins.fichier_preferences(), {"pas": 50, "inconnue": 1})
        p = chemins.lire_preferences()
        self.assertEqual(p["pas"], 50)
        self.assertNotIn("inconnue", p)
        chemins.ecrire_preferences({**p, "inconnue": 2})
        ecrit = json.loads(chemins.fichier_preferences().read_text(encoding="utf-8"))
        self.assertEqual(set(ecrit), set(chemins.PREFERENCES_DEFAUT))

    def test_modifier_preference(self):
        p = chemins.modifier_preference("voix_defaut", "jules-b")
        self.assertEqual(p["voix_defaut"], "jules-b")
        self.assertEqual(chemins.lire_preferences()["voix_defaut"], "jules-b")
        self.assertEqual(chemins.lire_preferences()["reverb_db"], -13.0)

    def test_modifier_plusieurs_preferences(self):
        chemins.modifier_preference("pas", 50)
        p = chemins.modifier_preferences({"voix_par_chanteur": True, "voix_chanteurs": ["jules", "marie"]})
        self.assertEqual((p["voix_par_chanteur"], p["voix_chanteurs"], p["pas"]), (True, ["jules", "marie"], 50))
        self.assertEqual(chemins.lire_preferences(), p)


class EssaiChoixChanteurs(EssaiIsole):
    """« Une voix par chanteur » gardé d'une session à l'autre : choix, duo et voix relus (voix inventées)."""

    def test_par_defaut_une_voix_pour_tous(self):
        p = chemins.lire_preferences()
        self.assertEqual((p["voix_par_chanteur"], p["duo_chanteurs"], p["voix_chanteurs"]), (False, "genre", []))
        self.assertEqual(chemins.choix_chanteurs(p), (False, "genre", [None, None]))
        self.assertEqual(chemins.choix_chanteurs(None), (False, "genre", [None, None]))

    def test_relu_a_la_session_suivante(self):
        chemins.modifier_preferences({"voix_par_chanteur": True, "duo_chanteurs": "ordre",
                                      "voix_chanteurs": ["jules", "marie"]})
        p = chemins.lire_preferences()      # nouvelle session : relu dans interface.json
        self.assertEqual(chemins.choix_chanteurs(p), (True, "ordre", ["jules", "marie"]))
        self.assertEqual(chemins.choix_chanteurs(p, ["marie", "jules", "paul"]), (True, "ordre", ["jules", "marie"]))

    def test_voix_disparue_pas_preselectionnee(self):
        p = {"voix_par_chanteur": True, "duo_chanteurs": "genre", "voix_chanteurs": ["jules", "marie"]}
        self.assertEqual(chemins.choix_chanteurs(p, ["marie"]), (True, "genre", [None, "marie"]))
        self.assertEqual(chemins.choix_chanteurs(p, []), (True, "genre", [None, None]))
        # sans liste des voix (pas encore lue), les voix gardées restent telles quelles
        self.assertEqual(chemins.choix_chanteurs(p)[2], ["jules", "marie"])

    def test_une_seule_voix_choisie(self):
        p = {"voix_par_chanteur": True, "voix_chanteurs": [None, "marie"]}
        self.assertEqual(chemins.choix_chanteurs(p, ["jules", "marie"]), (True, "genre", [None, "marie"]))
        self.assertEqual(chemins.choix_chanteurs({"voix_chanteurs": ["jules"]})[2], ["jules", None])

    def test_valeurs_abimees(self):
        chemins.ecrire_json(chemins.fichier_preferences(), {"voix_par_chanteur": "oui", "duo_chanteurs": "trio",
                                                            "voix_chanteurs": "jules"})
        self.assertEqual(chemins.choix_chanteurs(chemins.lire_preferences(), ["jules"]),
                         (False, "genre", [None, None]))
        for voix in ([1, ""], [None, {"id": "jules"}], {"0": "jules"}, None):
            self.assertEqual(chemins.choix_chanteurs({"voix_chanteurs": voix})[2], [None, None], voix)
        self.assertEqual(chemins.choix_chanteurs({"voix_par_chanteur": 1})[0], False, "vrai seulement s'il vaut true")
        self.assertEqual(chemins.choix_chanteurs({"voix_chanteurs": ["jules", "marie", "paul"]})[2],
                         ["jules", "marie"])

    def test_telechargements(self):
        with mock.patch.object(chemins.Path, "home", return_value=self.tmp):
            self.assertEqual(chemins.dossier_telechargements(), self.tmp)
            (self.tmp / "Téléchargements").mkdir()
            self.assertEqual(chemins.dossier_telechargements(), self.tmp / "Téléchargements")
            (self.tmp / "Downloads").mkdir()
            self.assertEqual(chemins.dossier_telechargements(), self.tmp / "Downloads")


if __name__ == "__main__":
    unittest.main()
