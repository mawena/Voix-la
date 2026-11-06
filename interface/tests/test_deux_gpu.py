"""Essais des deux GPU de Kaggle côté interface : clés « gpu » et « kaggle.machine » de doubler (lecture, écriture,
capacité), cartes prévues, estimation de durée des doublages nettoyés à une et à deux cartes, durées par étape d'un
rapport à deux cartes, et le faux doubler (--gpu, temps_local.gpus). Rapports inventés."""

import os
import unittest
from pathlib import Path
from unittest import mock

from voixla import chemins, moteur, rapports
from voixla.rapports import Resultat

from .commun import EssaiIsole, creer_voix, oublier_capacites

TOUTES = frozenset(moteur.OPTIONS_SUIVIES)
SANS_GPU = TOUTES - {"--gpu"}
D = 228.0   # une chanson de 3 min 48


def rapport_nettoye(temps, temps_local, moteur_="yingmusic"):
    """Rapport inventé d'une chanson en chant nettoyé (voix principale et chœurs séparés, reverb retirée)."""
    modele = "yingmusic" if moteur_ == "yingmusic" else "chant44k"
    return {"mode": "chant", "duree_s": D, "modele": modele, "moteur": moteur_,
            "nettoyage": {"demande": True, "applique": True},
            "variantes": {"voix_convertie.flac": {"modele": modele, "moteur": moteur_, "pas_diffusion": 30}},
            "temps": dict(temps), "temps_local": dict(temps_local)}


LOCAL = {"extraction_s": 1.0, "envoi_jeu_s": 60.0, "rapatriement_s": 30.0, "decodage_retour_s": 0.5,
         "videos_dans_la_session": 1, "mix_local_s": 10.0, "remise_video_s": 5.0}
# une carte, ancien rapport (sans « gpus ») : le GPU de la vidéo est du temps réel
UNE_CARTE = rapport_nettoye(
    {"separation_s": 120.0, "analyse_s": 8.0, "nettoyage_s": 560.0, "courbes_s": 2.0, "conversion_s": 540.0,
     "total_video_s": 1300.0},
    {**LOCAL, "noyau_depuis_envoi_s": 1596.0, "estimation_noyau_s": 1550,
     "session_kaggle": {"installation_s": 40.0, "chargement_modeles_s": 36.0, "chargement_nettoyage_s": 30.0,
                        "chargement_yingmusic_s": 25.0, "separation_totale_s": 125.0, "nettoyage_totale_s": 590.0,
                        "yingmusic_total_s": 570.0, "total_s": 1550.0}})
# deux cartes : nettoyage_s et conversion_s additionnent le temps de GPU des deux cartes (total_video_s aussi)
SESSION_2 = {"installation_s": 40.0, "chargement_modeles_s": 36.0, "chargement_nettoyage_s": 30.0,
             "chargement_yingmusic_s": 25.0, "separation_totale_s": 125.0, "nettoyage_totale_s": 427.0,
             "nettoyage_gpu_s": 560.0, "yingmusic_total_s": 329.0, "yingmusic_gpu_s": 550.0, "total_s": 1120.0}
TEMPS_2 = {"separation_s": 120.0, "analyse_s": 8.0, "nettoyage_s": 560.0, "courbes_s": 2.0, "conversion_s": 550.0,
           "total_video_s": 1240.0}
DEUX_CARTES = rapport_nettoye(TEMPS_2, {**LOCAL, "noyau_depuis_envoi_s": 1164.0, "estimation_noyau_s": 1180,
                                        "gpus": 2, "session_kaggle": SESSION_2})


def res(rapport):
    return Resultat(dossier=Path("/s/x"), voix="jules", rapport=rapport)


def avec(rapport, **temps_local):
    """Copie du rapport, temps_local modifié (None retire la clé)."""
    tl = {**rapport["temps_local"], **temps_local}
    return {**rapport, "temps_local": {k: v for k, v in tl.items() if v is not None}}


# --- Estimation des durées --------------------------------------------------------------------------------------------

class EssaiEstimation(unittest.TestCase):
    """Le modèle de l'interface (repli sans doubler.py), sans les paroles : voir test_estimation pour elles."""

    def kaggle(self, resultats, cartes, durees=(D,)):
        m = rapports.modele_durees([res(r) for r in resultats])
        return rapports.estimer(list(durees), 30, m, nettoyage=True, moteur="yingmusic", cartes=cartes,
                                paroles=False)["kaggle"]

    def test_une_carte_comme_avant(self):
        # ancien rapport (sans gpus) ou gpus à 1 : temps.total_video_s, comme avant ; la pente à deux cartes reste
        for r in (UNE_CARTE, avec(UNE_CARTE, gpus=1)):
            m = rapports.modele_durees([res(r)])
            self.assertAlmostEqual(m["kaggle_nettoye"][1], 1300.0 / D, places=9)
            self.assertEqual(m["kaggle_nettoye_2cartes"], rapports.MODELE_DEFAUT["kaggle_nettoye_2cartes"])
            self.assertEqual(self.kaggle([r], 1), 230.0 + 1300.0)

    def test_deux_cartes_duree_reelle_de_la_session(self):
        m = rapports.modele_durees([res(DEUX_CARTES)])
        self.assertAlmostEqual(m["kaggle_nettoye_2cartes"][1], (1164.0 - 230.0) / D, places=9)   # environ 4,1
        self.assertEqual(m["kaggle_nettoye"], rapports.MODELE_DEFAUT["kaggle_nettoye"])         # rien à une carte
        self.assertEqual(self.kaggle([DEUX_CARTES], 2), 1164.0)        # la session réelle
        # avant : total_video_s (temps des deux cartes additionnés) surestimait la session d'un quart
        self.assertGreater((230.0 + 1240.0) / 1164.0, 1.25)

    def test_longue_attente_d_une_place_gpu(self):
        r = avec(DEUX_CARTES, noyau_depuis_envoi_s=5000.0)
        m = rapports.modele_durees([res(r)])
        self.assertAlmostEqual(m["kaggle_nettoye_2cartes"][1], (1120.0 + 40.0 - 230.0) / D, places=9)

    def test_lot_a_deux_cartes(self):
        # plusieurs vidéos par session : total_video_s ramené au temps réel par la part mesurée dans la session
        # (nettoyage : (427 − 30) / 560 ; YingMusic-SVC : (329 − 25) / 550)
        r = avec(DEUX_CARTES, videos_dans_la_session=2)
        self.assertAlmostEqual(rapports.gpu_reel_deux_cartes(r), 1240.0 - (560.0 - 397.0) - (550.0 - 304.0), places=6)
        m = rapports.modele_durees([res(r)])
        self.assertAlmostEqual(m["kaggle_nettoye_2cartes"][1], 831.0 / D, places=6)

    def test_lot_sans_mesure_de_la_session(self):
        # sans nettoyage_gpu_s ni yingmusic_gpu_s : les parts de doubler (0,70 et 0,55)
        sk = {k: v for k, v in SESSION_2.items() if not k.endswith("_gpu_s")}
        r = avec(DEUX_CARTES, videos_dans_la_session=3, session_kaggle=sk)
        self.assertAlmostEqual(rapports.gpu_reel_deux_cartes(r), 1240.0 - 0.30 * 560.0 - 0.45 * 550.0, places=6)

    def test_parts_bornees_et_seed_vc(self):
        sk = {**SESSION_2, "nettoyage_totale_s": 5000.0, "yingmusic_total_s": 30.0}   # parts bornées à 1 et 0,4
        r = avec(DEUX_CARTES, videos_dans_la_session=2, session_kaggle=sk)
        self.assertAlmostEqual(rapports.gpu_reel_deux_cartes(r), 1240.0 - 0.6 * 550.0, places=6)
        # Seed-VC ne se partage pas entre les cartes : seule la part du nettoyage compte
        seed = rapport_nettoye(TEMPS_2, {**DEUX_CARTES["temps_local"], "videos_dans_la_session": 2}, moteur_="seedvc")
        self.assertAlmostEqual(rapports.gpu_reel_deux_cartes(seed), 1240.0 - (560.0 - 397.0), places=6)
        self.assertIsNone(rapports.gpu_reel_deux_cartes({"temps": {}}))
        self.assertIsNone(rapports.gpu_reel_deux_cartes(None))

    def test_historique_melange(self):
        # chaque pente ne prend que ses rapports : une carte pour une carte, deux pour deux
        self.assertEqual(self.kaggle([UNE_CARTE, DEUX_CARTES], 1), 230.0 + 1300.0)
        self.assertEqual(self.kaggle([UNE_CARTE, DEUX_CARTES], 2), 1164.0)

    def test_sans_historique(self):
        self.assertEqual(self.kaggle([], 1), round(230.0 + 5.2 * D, 1))     # défaut d'avant, inchangé
        self.assertEqual(self.kaggle([], 2), round(230.0 + 3.8 * D, 1))     # défauts de doubler, paroles à part
        self.assertEqual(rapports.modele_durees([]), rapports.MODELE_DEFAUT)

    def test_cartes_sans_effet_hors_chant_nettoye(self):
        m = rapports.modele_durees([res(DEUX_CARTES)])
        for k in ({"nettoyage": False}, {"nettoyage": True, "mode": "parole"}):
            with self.subTest(**k):
                self.assertEqual(rapports.estimer([D], 30, m, cartes=2, **k),
                                 rapports.estimer([D], 30, m, cartes=1, **k))
        self.assertEqual(rapports.estimer([D], 30, m, nettoyage=True, cartes=None),
                         rapports.estimer([D], 30, m, nettoyage=True, cartes=1))


# --- Durées par étape -------------------------------------------------------------------------------------------------

class EssaiDurees(unittest.TestCase):
    def test_deux_cartes(self):
        lignes = rapports.durees_etapes(DEUX_CARTES)
        libelles = [l for l, _, _ in lignes]
        self.assertIn("GPU Kaggle (2 cartes T4), depuis l’envoi", libelles)
        self.assertNotIn("GPU Kaggle, depuis l’envoi", libelles)
        details = dict(dict((l, d) for l, _, d in lignes)["GPU Kaggle (2 cartes T4), depuis l’envoi"])
        # durées réelles de la session, et leur temps de GPU (deux cartes additionnées) juste après
        self.assertEqual(details["Nettoyage (toute la session)"], 427.0)
        self.assertEqual(details["Nettoyage, temps de GPU des 2 cartes additionnés"], 560.0)
        self.assertEqual(details["YingMusic-SVC (toute la session)"], 329.0)
        self.assertEqual(details["YingMusic-SVC, temps de GPU des 2 cartes additionnés"], 550.0)
        self.assertEqual(details["Nettoyage de cette vidéo (temps de GPU des 2 cartes additionnés)"], 560.0)
        self.assertEqual(details["Conversion YingMusic-SVC (temps de GPU des 2 cartes additionnés)"], 550.0)
        self.assertNotIn("Nettoyage de cette vidéo", details)
        ordre = [a for a, _ in dict((l, d) for l, _, d in lignes)["GPU Kaggle (2 cartes T4), depuis l’envoi"]]
        self.assertEqual(ordre.index("Nettoyage, temps de GPU des 2 cartes additionnés"),
                         ordre.index("Nettoyage (toute la session)") + 1)

    def test_une_carte_inchangee(self):
        # même avec un nettoyage_gpu_s dans la session, une carte garde les libellés d'avant
        r = avec(UNE_CARTE, gpus=1, session_kaggle={**UNE_CARTE["temps_local"]["session_kaggle"],
                                                    "nettoyage_gpu_s": 560.0, "yingmusic_gpu_s": 540.0})
        for rap in (UNE_CARTE, r):
            details = dict(dict((l, d) for l, _, d in rapports.durees_etapes(rap))["GPU Kaggle, depuis l’envoi"])
            self.assertEqual(details["Nettoyage de cette vidéo"], 560.0)
            self.assertEqual(details["Conversion YingMusic-SVC"], 540.0)
            self.assertFalse(any("cartes" in a for a in details), details)
        self.assertEqual(rapports.notes_durees(UNE_CARTE), [])

    def test_seed_vc_a_deux_cartes(self):
        seed = rapport_nettoye(TEMPS_2, DEUX_CARTES["temps_local"], moteur_="seedvc")
        lignes = dict((l, d) for l, _, d in rapports.durees_etapes(seed))
        details = dict(lignes["GPU Kaggle (2 cartes T4), depuis l’envoi"])
        self.assertEqual(details["Conversion Seed-VC"], 550.0)     # sur une carte : rien d'additionné

    def test_sans_noyau_depuis_envoi(self):
        r = avec(DEUX_CARTES, noyau_depuis_envoi_s=None)
        self.assertIn(("GPU Kaggle (2 cartes T4) (cette vidéo)", 1240.0),
                      [(l, s) for l, s, _ in rapports.durees_etapes(r)])

    def test_notes(self):
        notes = rapports.notes_durees(DEUX_CARTES)
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("2 cartes T4 : "))
        self.assertIn("additionnent le temps de GPU des deux cartes", notes[0])
        # toutes les notes ensemble (avant, la session partagée effaçait l'attente d'une place GPU)
        r = avec(DEUX_CARTES, noyau_depuis_envoi_s=5000.0, videos_dans_la_session=2)
        notes = rapports.notes_durees(r)
        self.assertEqual(len(notes), 3)
        self.assertTrue(notes[0].startswith("Dont environ "))
        self.assertEqual(notes[1], "Session Kaggle partagée par 2 vidéos.")
        self.assertEqual(rapports.notes_durees(None), [])

    def test_cartes_gpu(self):
        for tl, n in ((None, 1), ({}, 1), ({"gpus": 1}, 1), ({"gpus": 2}, 2), ({"gpus": "2"}, 2), ({"gpus": 0}, 1),
                      ({"gpus": "deux"}, 1), ({"gpus": None}, 1)):
            with self.subTest(tl=tl):
                self.assertEqual(rapports.cartes_gpu({"temps_local": tl} if tl is not None else {}), n)
        self.assertEqual(rapports.cartes_gpu(None), 1)


# --- Configuration de doubler -----------------------------------------------------------------------------------------

class EssaiConfigGpu(EssaiIsole):
    def par_doubler(self, sans_gpu=False):
        """Comme hors essai : config.json « de doubler » (le faux) dans un dossier temporaire, écrit par --config."""
        del os.environ["VOIXLA_CONFIG"]
        os.environ["FAUX_DOUBLER_CONFIG"] = str(self.tmp / "config-doubler")
        if sans_gpu:
            os.environ["FAUX_DOUBLER_SANS_GPU"] = "1"
        oublier_capacites()
        interface = self.tmp / "config-interface" / "config.json"
        rustine = mock.patch.object(chemins, "fichier_config", return_value=interface)
        rustine.start()
        self.addCleanup(rustine.stop)
        return self.tmp / "config-doubler" / "config.json"

    def test_lecture(self):
        for conf, attendu in ((None, "auto"), ({}, "auto"), ({"gpu": "1"}, "1"), ({"gpu": 2}, "2"),
                              ({"gpu": " AUTO "}, "auto"), ({"gpu": "3"}, "auto"), ({"gpu": True}, "auto"),
                              ({"contenu": {"gpu": "1"}}, "1"), ({"gpu": "2", "contenu": {"gpu": "1"}}, "2")):
            with self.subTest(conf=conf):
                self.assertEqual(moteur.gpu_par_defaut(conf), attendu)
        self.assertEqual(moteur.machine_kaggle(None), "NvidiaTeslaT4")
        self.assertEqual(moteur.machine_kaggle({"kaggle": {"compte": "moi"}}), "NvidiaTeslaT4")
        self.assertEqual(moteur.machine_kaggle({"kaggle": {"machine": "NvidiaTeslaP100"}}), "NvidiaTeslaP100")
        self.assertEqual(moteur.machine_kaggle({"contenu": {"kaggle": {"machine": "NvidiaTeslaP100"}}}),
                         "NvidiaTeslaP100")
        self.assertIn("gpu", moteur.CLES_CONFIG)
        self.assertIn("kaggle.machine", moteur.CLES_CONFIG)

    def test_cartes_prevues(self):
        p100 = {"kaggle": {"machine": "NvidiaTeslaP100"}}
        for conf, caps, n in (({}, TOUTES, 2), ({"gpu": "auto"}, TOUTES, 2), ({"gpu": "1"}, TOUTES, 1),
                              ({"gpu": "2"}, TOUTES, 2), (p100, TOUTES, 1), ({**p100, "gpu": "2"}, TOUTES, 2),
                              ({}, SANS_GPU, 1), ({"gpu": "2"}, SANS_GPU, 1), (None, TOUTES, 2)):
            with self.subTest(conf=conf, gpu="--gpu" in caps):
                self.assertEqual(moteur.cartes_prevues(conf, caps), n)

    def test_capacite(self):
        self.assertIn("--gpu", moteur.capacites())
        os.environ["FAUX_DOUBLER_SANS_GPU"] = "1"     # doubler 0.5.0 : réglage grisé
        oublier_capacites()
        self.assertNotIn("--gpu", moteur.capacites())
        self.assertIn("--autotune", moteur.capacites())
        self.assertEqual(moteur.cartes_prevues({}), 1)

    def test_ecriture_directe(self):
        # VOIXLA_CONFIG (essais) : écrite directement, en texte comme doubler --config
        self.assertEqual(moteur.ecrire_config("gpu", " 2 "), (True, ""))
        self.assertEqual(chemins.lire_config(), {"gpu": "2"})
        self.assertEqual(moteur.gpu_par_defaut(moteur.config_effective()), "2")
        self.assertEqual(moteur.ecrire_config("gpu", "AUTO"), (True, ""))
        self.assertEqual(chemins.lire_config(), {"gpu": "auto"})
        self.assertEqual(moteur.ecrire_config("kaggle.machine", "NvidiaTeslaP100"), (True, ""))
        conf = moteur.config_effective()
        self.assertEqual(moteur.machine_kaggle(conf), "NvidiaTeslaP100")
        self.assertEqual(moteur.cartes_prevues(conf, TOUTES), 1)

    def test_par_doubler(self):
        f = self.par_doubler()
        ok, message = moteur.ecrire_config("gpu", "1")
        self.assertTrue(ok, message)
        self.assertEqual(message, f"gpu = 1  ({f})")
        self.assertEqual(chemins.lire_json(f), {"gpu": "1"})
        conf = moteur.config_effective()
        self.assertTrue(conf["par_doubler"])
        self.assertEqual((conf["gpu"], conf["kaggle"]["machine"]), ("1", "NvidiaTeslaT4"))
        self.assertEqual(moteur.cartes_prevues(conf), 1)
        ok, message = moteur.ecrire_config("gpu", "3")
        self.assertFalse(ok)
        self.assertIn("gpu : auto, 1 ou 2", message)
        ok, message = moteur.ecrire_config("kaggle.machine", "pas une machine")
        self.assertFalse(ok)
        self.assertIn("kaggle.machine", message)
        self.assertTrue(moteur.ecrire_config("gpu", "auto")[0])
        self.assertEqual(moteur.cartes_prevues(moteur.config_effective()), 2)

    def test_doubler_sans_la_cle_gpu(self):
        f = self.par_doubler(sans_gpu=True)
        conf = moteur.config_effective()
        self.assertNotIn("gpu", conf)
        self.assertNotIn("machine", conf["kaggle"])
        self.assertEqual(moteur.gpu_par_defaut(conf), "auto")
        self.assertEqual(moteur.cartes_prevues(conf), 1)          # une carte, comme avant
        ok, message = moteur.ecrire_config("gpu", "2")
        self.assertFalse(ok)
        self.assertIn("clé inconnue", message)
        self.assertFalse(f.exists())


# --- Le faux doubler : temps_local.gpus -------------------------------------------------------------------------------

class EssaiFauxDoublage(EssaiIsole):
    def doubler(self, nom, conf=None, en_plus=()):
        if conf is not None:
            chemins.ecrire_json(chemins.fichier_config(), conf)
        v = self.video(f"{nom}.mp4")
        argv = moteur.args_doublage([str(v)], {"voix": "jules", "pas": 30}, self.donnees)
        code, sortie = moteur.lancer([*argv, *en_plus], moteur.environnement(self.donnees), timeout=60)
        self.assertEqual(code, 0, sortie)
        r = next(r for r in rapports.lister_resultats(chemins.dossier_sorties(self.donnees)) if r.titre == nom)
        return r.rapport, sortie

    def test_cartes_dans_les_rapports(self):
        creer_voix(self.donnees)
        for nom, conf, en_plus, cartes in (("Auto", None, (), 2), ("Une", {"gpu": "1"}, (), 1),
                                           ("Option", {"gpu": "2"}, ("--gpu=1",), 1),
                                           ("Autre machine", {"kaggle": {"machine": "NvidiaTeslaP100"}}, (), 1),
                                           ("Deux", {"gpu": "2"}, (), 2)):
            with self.subTest(nom):
                rapport, sortie = self.doubler(nom, conf, en_plus)
                self.assertEqual(rapport["temps_local"]["gpus"], cartes)
                self.assertEqual(rapports.cartes_gpu(rapport), cartes)
                self.assertIn(f"{cartes} utilisée(s)", sortie)
                libelle = "GPU Kaggle (2 cartes T4), depuis l’envoi" if cartes == 2 else "GPU Kaggle, depuis l’envoi"
                self.assertIn(libelle, [l for l, _, _ in rapports.durees_etapes(rapport)])

    def test_doubler_sans_gpu(self):
        os.environ["FAUX_DOUBLER_SANS_GPU"] = "1"
        oublier_capacites()
        creer_voix(self.donnees)
        rapport, sortie = self.doubler("Avant")
        self.assertNotIn("gpus", rapport["temps_local"])
        self.assertEqual(rapports.cartes_gpu(rapport), 1)
        self.assertNotIn("utilisée(s)", sortie)


if __name__ == "__main__":
    unittest.main()
