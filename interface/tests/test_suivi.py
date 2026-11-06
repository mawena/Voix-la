"""Essais de voixla.suivi : Avancement reconstruit à partir des événements JSON de doubler (format --evenements)."""

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from voixla import moteur, suivi

DONNEES_ESSAI = Path(__file__).resolve().parent / "donnees"
BASE = datetime(2026, 11, 4, 9, 0, 0).astimezone()
T0 = BASE.timestamp()
A, B = "A (extrait 1m00,0+0m40,0)", "B (extrait 1m00,0+0m40,0)"
ESTIMATIONS = {"extraction": 2.0, "envoi": 40.0, "attente": 0.0, "kaggle": 300.0, "rapatriement": 10.0, "mix": 4.0,
               "video": 2.0, "fini": 0.0}


def ligne(dt, type_, video=None, **champs):
    """Une ligne d'événement comme l'écrit doubler (t en heure ISO locale avec fuseau, champs None retirés)."""
    ev = {"t": (BASE + timedelta(seconds=dt)).isoformat(timespec="milliseconds"), "type": type_, "video": video}
    ev.update({k: v for k, v in champs.items() if v is not None})
    return json.dumps(ev, ensure_ascii=False)


def etape(dt, nom, etat, videos, message=None, duree_s=None):
    """Événements « etape » d'une étape, un par vidéo (comme doubler.Etape)."""
    return [ligne(dt, "etape", v, etape=nom, etat=etat, message=message, duree_s=duree_s) for v in videos]


def debut_du_lot():
    """Extraction des deux vidéos, envoi, noyau en cours (jusqu'à t = 81 s)."""
    return [
        *etape(0, "extraction", "debut", [A], "son de A.mp4"), *etape(1, "extraction", "fin", [A], "son de A.mp4", 1.0),
        *etape(1, "extraction", "debut", [B], "son de B.mkv"), *etape(2, "extraction", "fin", [B], "son de B.mkv", 1.0),
        *etape(2, "envoi", "debut", [A, B], "son de 2 vidéo(s) en FLAC vers le jeu privé moi/doublage-voix-entrees"),
        *etape(40, "envoi", "fin", [A, B], "son de 2 vidéo(s) en FLAC vers le jeu privé moi/doublage-voix-entrees", 38.0),
        *etape(41, "kaggle", "debut", [A, B], "noyau moi/doublage-voix-gpu, environ 5 min"),
        *[ligne(61, "kaggle", v, statut="queued", depuis_s=20, estimation_s=300) for v in (A, B)],
        *[ligne(81, "kaggle", v, statut="running", depuis_s=40, estimation_s=300) for v in (A, B)],
    ]


def video_finie(dt, v, echec_mix=False):
    """Mixage, remise sur la vidéo, sortie et « fini » d'une vidéo, de dt à dt + 5 s."""
    if echec_mix:
        return [*etape(dt, "mix", "debut", [v], "mixage v2 (chant)"),
                *etape(dt + 3, "mix", "erreur", [v], "échec : ffmpeg -i instrumental.wav…", 3.0),
                ligne(dt + 3, "erreur", v, message="échec : ffmpeg -i instrumental.wav…")]
    sortie = f"/s/{v}/{v.split(' (')[0]} - voix Jules"
    return [
        *etape(dt, "mix", "debut", [v], "mixage v2 (chant)"), *etape(dt + 3, "mix", "fin", [v], "mixage v2 (chant)", 3.0),
        *etape(dt + 3, "video", "debut", [v], "son remis sur la vidéo (image copiée)"),
        *etape(dt + 5, "video", "fin", [v], "son remis sur la vidéo (image copiée)", 2.0),
        ligne(dt + 5, "sortie", v, mp4=f"{sortie}.mp4", mp3=f"{sortie}.mp3", rapport=f"/s/{v}/travail/jules/rapport.json"),
        ligne(dt + 5, "etape", v, etape="fini", etat="fin", message=f"{sortie}.mp4", duree_s=float(dt + 5)),
    ]


def lot_reussi():
    return [
        *debut_du_lot(),
        *[ligne(301, "kaggle", v, statut="complete", depuis_s=260, estimation_s=300) for v in (A, B)],
        *etape(301, "kaggle", "fin", [A, B], "noyau moi/doublage-voix-gpu, environ 5 min", 260.0),
        *etape(301, "rapatriement", "debut", [A, B], "téléchargement des résultats du noyau"),
        *etape(311, "rapatriement", "fin", [A, B], "téléchargement des résultats du noyau", 10.0),
        *video_finie(311, A), *video_finie(316, B),
        ligne(321, "fin", None, code=0),
    ]


def lot_kaggle_en_erreur():
    msg = "de la session Kaggle : Kaggle : noyau terminé en error sans résultat ; fin du journal :\nCUDA out of memory"
    return [
        *debut_du_lot(),
        *[ligne(120, "kaggle", v, statut="error", depuis_s=79, estimation_s=300) for v in (A, B)],
        *etape(120, "kaggle", "erreur", [A, B], "noyau terminé en error", 79.0),
        *etape(120, "rapatriement", "debut", [A, B], "téléchargement des résultats du noyau"),
        *etape(125, "rapatriement", "erreur", [A, B], "noyau terminé en error sans résultat", 5.0),
        ligne(125, "erreur", A, message=msg), ligne(125, "erreur", B, message=msg),
        ligne(125, "erreur", None, message="2 vidéo(s) en échec : /v/A.mp4, /v/B.mkv"),
        ligne(125, "fin", None, code=1),
    ]


def rejouer(lignes, av=None, **k):
    av = av or suivi.Avancement(**({"nb_videos": 2} | k))
    for l in lignes:
        ev = moteur.analyser_evenement(l)
        assert ev is not None, l
        av.appliquer(ev)
    return av


def etats(av):
    return {n: av.etapes[n].etat for n in av.ordre}


class EssaiLot(unittest.TestCase):
    def test_reussite(self):
        av = rejouer(lot_reussi(), estimations=ESTIMATIONS)
        self.assertEqual(etats(av), {"extraction": "fait", "envoi": "fait", "attente": "saute", "kaggle": "fait",
                                     "rapatriement": "fait", "mix": "fait", "video": "fait", "fini": "fait"})
        self.assertTrue(av.termine)
        self.assertEqual(av.code, 0)
        self.assertEqual(av.erreurs, [])
        self.assertEqual(av.sorties, ["/s/A (extrait 1m00,0+0m40,0)/A - voix Jules.mp4",
                                      "/s/A (extrait 1m00,0+0m40,0)/A - voix Jules.mp3",
                                      "/s/B (extrait 1m00,0+0m40,0)/B - voix Jules.mp4",
                                      "/s/B (extrait 1m00,0+0m40,0)/B - voix Jules.mp3"])
        self.assertEqual(av.debut, T0)
        self.assertAlmostEqual(av.ecoule_s(), 321.0, places=3)
        self.assertEqual(av.restant_s(), 0.0)
        self.assertIsNone(av.etape_courante())
        self.assertEqual(av.derniere_atteinte(), "fini")
        self.assertEqual(av.statut_kaggle(), "terminé")
        self.assertEqual(av.resume(), "Fini")
        self.assertEqual(av.etapes["envoi"].ecoule_s(T0 + 999), 38.0)     # durée annoncée par doubler
        self.assertAlmostEqual(av.etapes["kaggle"].cumul_s, 260.0, places=3)
        self.assertAlmostEqual(av.etapes["mix"].cumul_s, 6.0, places=3)     # 3 s par vidéo
        self.assertEqual(av.etapes["mix"].faits, {A, B})

    def test_compteurs_par_video(self):
        lignes = lot_reussi()
        av = rejouer(lignes[:2])                                   # extraction de A finie
        self.assertEqual(av.etapes["extraction"].etat, "partiel")
        self.assertEqual(av.etapes["extraction"].faits, {A})
        rejouer(lignes[2:3], av)                                   # extraction de B commencée
        self.assertEqual(av.etape_courante(), "extraction")
        self.assertEqual(av.resume(T0 + 1.5), "Extraction du son (2/2) · 1,5 s")
        rejouer(lignes[3:], av)
        self.assertEqual(av.etapes["extraction"].etat, "fait")

    def test_etape_de_session_et_mixage(self):
        lignes = lot_reussi()
        i_kaggle = next(i for i, l in enumerate(lignes) if '"statut": "running"' in l)
        av = rejouer(lignes[:i_kaggle + 1])
        self.assertEqual(av.etape_courante(), "kaggle")
        self.assertEqual(av.resume(T0 + 100), "Conversion sur le GPU · 59 s")
        self.assertEqual(av.statut_kaggle(), "en cours")
        self.assertTrue(av.kaggle_parti())
        i_mix_b = next(i for i, l in enumerate(lignes) if f'"video": "{B}"' in l and '"etape": "mix", "etat": "debut"' in l)
        av = rejouer(lignes[:i_mix_b + 1])
        self.assertEqual(av.etapes["mix"].faits, {A})
        self.assertEqual(av.resume(T0 + 317), "Mixage (2/2) · 4 s")

    def test_echec_de_la_session(self):
        av = rejouer(lot_kaggle_en_erreur())
        self.assertEqual(av.etapes["kaggle"].etat, "erreur")
        self.assertEqual(av.etapes["rapatriement"].etat, "erreur")
        self.assertEqual([av.etapes[n].etat for n in ("mix", "video", "fini")], ["a_venir"] * 3)
        self.assertEqual((av.termine, av.code), (True, 1))
        self.assertEqual(av.resume(), "Échec")
        self.assertIn("Conversion sur le GPU : noyau terminé en error", av.erreurs)
        self.assertIn("Rapatriement : noyau terminé en error sans résultat", av.erreurs)
        self.assertIn("2 vidéo(s) en échec : /v/A.mp4, /v/B.mkv", av.erreurs)
        self.assertTrue(any(e.startswith(f"{A} : de la session Kaggle") for e in av.erreurs))
        self.assertEqual(len(av.erreurs), len(set(av.erreurs)))     # messages répétés par vidéo : comptés une fois
        self.assertEqual(av.statut_kaggle(), "en erreur")
        self.assertEqual(av.sorties, [])

    def test_echec_d_une_seule_video(self):
        lignes = [l for l in lot_reussi() if '"type": "fin"' not in l]
        i = next(i for i, l in enumerate(lignes) if '"etape": "mix"' in l)
        lignes = lignes[:i] + video_finie(311, A, echec_mix=True) + video_finie(316, B) + [
            ligne(321, "erreur", None, message="1 vidéo(s) en échec : /v/A.mp4"), ligne(321, "fin", None, code=1)]
        av = rejouer(lignes)
        self.assertEqual(av.code, 1)
        self.assertEqual(len(av.sorties), 2)
        self.assertIn("Mixage : échec : ffmpeg -i instrumental.wav…", av.erreurs)
        self.assertIn(f"{A} : échec : ffmpeg -i instrumental.wav…", av.erreurs)
        self.assertNotEqual(av.etapes["mix"].etat, "fait")         # une vidéo sur deux seulement
        self.assertEqual(av.etapes["video"].faits, {B})


class EssaiAnnulation(unittest.TestCase):
    def test_pendant_le_noyau(self):
        av = rejouer(debut_du_lot(), estimations=ESTIMATIONS)
        av.terminer(-15, t=T0 + 90, annule=True)
        self.assertEqual(av.etapes["kaggle"].etat, "annule")
        self.assertAlmostEqual(av.etapes["kaggle"].cumul_s, 49.0, places=3)
        self.assertEqual([av.etapes[n].etat for n in ("extraction", "envoi")], ["fait", "fait"])
        self.assertEqual(av.etapes["mix"].etat, "a_venir")
        self.assertTrue(av.kaggle_parti())          # la session continuera seule sur Kaggle
        self.assertTrue(av.termine)
        self.assertEqual(av.code, -15)
        self.assertEqual(av.restant_s(), 0.0)
        self.assertAlmostEqual(av.ecoule_s(), 90.0, places=3)

    def test_avant_l_envoi(self):
        av = rejouer(debut_du_lot()[:3])
        av.terminer(-15, t=T0 + 1.5, annule=True)
        self.assertEqual(av.etapes["extraction"].etat, "annule")
        self.assertFalse(av.kaggle_parti())

    def test_code_de_sortie_apres_l_evenement_fin(self):
        av = rejouer(lot_reussi())
        av.terminer(0, t=T0 + 400)          # Travail.actualiser conclut aussi avec le code de l'exécuteur
        self.assertEqual(av.code, 0)
        self.assertAlmostEqual(av.fin, T0 + 321, places=3)


class EssaiTempsRestant(unittest.TestCase):
    def test_sans_estimation(self):
        self.assertIsNone(suivi.Avancement(nb_videos=2).restant_s(T0))

    def test_avant_le_debut(self):
        av = suivi.Avancement(nb_videos=2, estimations=ESTIMATIONS)
        self.assertEqual(av.restant_s(T0), 358.0)
        self.assertEqual(av.resume(T0), "Démarrage…")
        self.assertEqual(av.ecoule_s(T0), 0.0)

    def test_extraction_par_video(self):
        av = rejouer(lot_reussi()[:3], estimations=ESTIMATIONS)
        # extraction : 1 s par vidéo, A faite, B commencée il y a 0,5 s ; puis 40 + 0 + 300 + 10 + 4 + 2
        self.assertAlmostEqual(av.restant_s(T0 + 1.5), 356.5, places=3)

    def test_etape_de_session(self):
        av = rejouer(lot_reussi()[:6], estimations=ESTIMATIONS)     # envoi commencé à t = 2
        self.assertAlmostEqual(av.restant_s(T0 + 12), 30 + 300 + 10 + 4 + 2, places=3)

    def test_noyau_estime_par_doubler(self):
        av = rejouer(debut_du_lot(), estimations={**ESTIMATIONS, "kaggle": 999.0})
        self.assertEqual(av.estimations["kaggle"], 300.0)      # estimation_s de doubler retenue
        # reste du noyau = 300 − (40 + 20 s depuis le dernier statut) = 240 ; puis 10 + 4 + 2
        self.assertAlmostEqual(av.restant_s(T0 + 101), 256.0, places=3)
        self.assertAlmostEqual(av.restant_s(T0 + 900), 16.0, places=3)     # jamais négatif

    def test_fini(self):
        av = rejouer(lot_reussi(), estimations=ESTIMATIONS)
        self.assertEqual(av.restant_s(T0 + 2), 0.0)


V = "Essai (extrait 1m00,0+0m40,0)"
ESTIMATIONS_INTERFACE = {**ESTIMATIONS, "kaggle": 1500.0, "rapatriement": 20.0, "mix": 10.0, "video": 5.0}


def lancement(estimation_s=1000):
    """Une vidéo : extraction, envoi, noyau poussé à t = 50 ; premier état du noyau 20 s plus tard."""
    return [*etape(0, "extraction", "debut", [V]), *etape(2, "extraction", "fin", [V], duree_s=2.0),
            *etape(2, "envoi", "debut", [V]), *etape(50, "envoi", "fin", [V], duree_s=48.0),
            *etape(50, "kaggle", "debut", [V], "noyau moi/doublage-voix-gpu, environ 17 min"),
            ligne(70, "kaggle", V, statut="running", depuis_s=20, estimation_s=estimation_s)]


class EssaiEstimationDeDoublerLancee(unittest.TestCase):
    """L'estimation de l'interface avant le lancement ; celle de doubler (estimation_s) une fois le noyau lancé."""

    def rejouer(self, lignes):
        return rejouer(lignes, nb_videos=1, estimations=ESTIMATIONS_INTERFACE)

    def test_avant_le_lancement_l_estimation_de_l_interface(self):
        av = self.rejouer(lancement()[:4])                                   # envoi fini, noyau pas encore poussé
        self.assertIsNone(av.estimation_doubler_s())
        self.assertIsNone(av.texte_kaggle(T0 + 50))
        self.assertAlmostEqual(av.restant_s(T0 + 50), 1500.0 + 20 + 10 + 5, places=3)
        av = self.rejouer(lancement()[:5])                                   # poussé, aucun état encore
        self.assertEqual(av.estimations["kaggle"], 1500.0)
        self.assertAlmostEqual(av.restant_s(T0 + 60), 1490.0 + 35, places=3)

    def test_une_fois_lance_l_estimation_de_doubler(self):
        av = self.rejouer(lancement())
        self.assertEqual(av.estimation_doubler_s(), 1000.0)
        self.assertEqual(av.estimations["kaggle"], 1000.0)                 # remplace celle de l'interface
        # reste = 1000 − (20 s au premier état + 30 s depuis) + rapatriement, mixage et vidéo
        self.assertAlmostEqual(av.restant_s(T0 + 100), 950.0 + 35, places=3)
        self.assertEqual(av.texte_kaggle(T0 + 100), "GPU Kaggle : en cours depuis 50 s · durée prévue par doubler ≈ 17 min")
        # le noyau dépasse l'estimation : plus rien à attendre de lui, et la ligne le dit
        self.assertAlmostEqual(av.restant_s(T0 + 2000), 35.0, places=3)
        self.assertIn("au-delà de la durée prévue par doubler (≈ 17 min)", av.texte_kaggle(T0 + 2000))

    def test_doubler_sans_estimation(self):
        av = self.rejouer(lancement(estimation_s=None))
        self.assertIsNone(av.estimation_doubler_s())
        self.assertEqual(av.estimations["kaggle"], 1500.0)
        self.assertEqual(av.texte_kaggle(T0 + 100), "GPU Kaggle : en cours depuis 50 s")
        self.assertAlmostEqual(av.restant_s(T0 + 100), 1450.0 + 35, places=3)


class EssaiCasParticuliers(unittest.TestCase):
    def test_fin_sans_debut(self):
        av = rejouer([ligne(5, "etape", A, etape="envoi", etat="fin", duree_s=3.0)])
        e = av.etapes["envoi"]
        self.assertEqual(e.etat, "fait")
        self.assertEqual(e.debut, T0 + 5)
        self.assertEqual(e.cumul_s, 0.0)
        self.assertEqual(e.duree_annoncee, 3.0)

    def test_evenements_inconnus_et_messages(self):
        av = rejouer([ligne(0, "etape", A, etape="reprise", etat="debut"), ligne(1, "inconnu", A),
                      ligne(2, "message", A, message="extrait : 59.961 s"),
                      ligne(3, "kaggle", A, statut="injoignable", depuis_s=300, panne_s=60)])
        self.assertEqual(av.messages, [(T0 + 2, "extrait : 59.961 s")])
        self.assertEqual(av.statut_kaggle(), "injoignable")
        self.assertFalse(av.termine)

    def test_une_etape_qui_commence_ferme_les_precedentes(self):
        av = rejouer([*etape(0, "extraction", "debut", [A]), *etape(10, "envoi", "debut", [A])], nb_videos=1)
        self.assertEqual(av.etapes["extraction"].etat, "fait")
        self.assertAlmostEqual(av.etapes["extraction"].cumul_s, 10.0, places=3)
        self.assertEqual(av.etape_courante(), "envoi")

    def test_remix(self):
        av = suivi.Avancement(nb_videos=1, type_travail="remix")
        self.assertEqual(av.ordre, ["mix", "video", "fini"])
        rejouer([*video_finie(0, A), ligne(6, "fin", None, code=0)], av)
        self.assertEqual(etats(av), {"mix": "fait", "video": "fait", "fini": "fait"})
        self.assertFalse(av.kaggle_parti())
        self.assertEqual(len(av.sorties), 2)


class EssaiVraiDoublage(unittest.TestCase):
    """Événements d'un vrai doublage (parole, extrait d'un épisode fictif ; titre, chemins et dates fictifs), écrits par doubler --evenements."""

    def setUp(self):
        self.lignes = (DONNEES_ESSAI / "evenements-parole.jsonl").read_text(encoding="utf-8").splitlines()

    def test_toutes_les_lignes_se_lisent(self):
        evs = [moteur.analyser_evenement(l) for l in self.lignes]
        self.assertTrue(all(evs))
        self.assertEqual([e["t"] for e in evs], sorted(e["t"] for e in evs))
        self.assertEqual({e["type"] for e in evs}, {"etape", "kaggle", "sortie", "fin"})

    def test_rejoue(self):
        av = rejouer(self.lignes, nb_videos=1)
        self.assertEqual(etats(av), {"extraction": "fait", "envoi": "fait", "attente": "saute", "kaggle": "fait",
                                     "rapatriement": "fait", "mix": "fait", "video": "fait", "fini": "fait"})
        self.assertEqual(av.code, 0)
        self.assertEqual([Path(s).suffix for s in av.sorties], [".mkv", ".mp3"])
        self.assertEqual(av.erreurs, [])
        self.assertAlmostEqual(av.ecoule_s(), 259.25, places=2)
        self.assertEqual(av.etapes["kaggle"].duree_annoncee, 195.4)
        self.assertEqual(av.estimations["kaggle"], 222.0)

    def test_temps_restant_pendant_le_noyau(self):
        i = next(i for i, l in enumerate(self.lignes) if '"depuis_s": 87' in l)
        av = rejouer(self.lignes[:i + 1], nb_videos=1, estimations={"rapatriement": 10.0, "mix": 4.0, "video": 3.0})
        t = moteur.analyser_evenement(self.lignes[i])["t"]
        self.assertAlmostEqual(av.restant_s(t), (222 - 87) + 10 + 4 + 3, places=3)



class EssaiCourbesAnnoncees(unittest.TestCase):
    def test_sortie_avec_courbes(self):
        av = suivi.Avancement(1)
        av.appliquer({"t": 1.0, "type": "sortie", "video": "A", "mp4": "/s/A/A.mp4", "mp3": "/s/A/A.mp3",
                      "rapport": "/s/A/travail/jules/rapport.json", "courbes": "/s/A/travail/jules/courbes.json"})
        self.assertEqual(av.courbes, ["/s/A/travail/jules/courbes.json"])
        self.assertNotIn("/s/A/travail/jules/courbes.json", av.sorties)

    def test_sortie_avec_paroles(self):
        av = suivi.Avancement(1)
        av.appliquer({"t": 1.0, "type": "sortie", "video": "A", "mp4": "/s/A/A.mp4", "mp3": "/s/A/A.mp3",
                      "rapport": "/s/A/travail/jules/rapport.json", "paroles": "/s/A/travail/jules/paroles.json"})
        self.assertEqual(av.paroles, ["/s/A/travail/jules/paroles.json"])
        self.assertNotIn("/s/A/travail/jules/paroles.json", av.sorties)
        self.assertEqual(av.sorties, ["/s/A/A.mp4", "/s/A/A.mp3"])

    def test_sortie_sans_paroles(self):
        av = suivi.Avancement(1)
        av.appliquer({"t": 1.0, "type": "sortie", "video": "A", "mp4": "/s/A/A.mp4", "paroles": None})
        self.assertEqual(av.paroles, [])


if __name__ == "__main__":
    unittest.main()
