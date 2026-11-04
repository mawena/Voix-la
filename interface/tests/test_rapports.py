"""Essais de voixla.rapports : résultats sous sorties/, rapport.json, frise d'octave, durées par étape, estimation.

Deux sortes d'essais : sur des copies de vrais rapports (lus sans y toucher là où doubler les range, copiés dans un
dossier temporaire avec des médias vides), et sur des rapports fabriqués aux valeurs connues."""

import json
import math
import tempfile
import unittest
from pathlib import Path

from voixla import chemins, moteur, rapports
from voixla.formats import MOINS
from voixla.rapports import Resultat

LIBELLES_ETAPES = {"Extraction du son", "Envoi sur Kaggle", "Démarrage du noyau", "GPU Kaggle, depuis l’envoi",
                   "GPU Kaggle (cette vidéo)", "Rapatriement", "Reste (original − voix)", "Mixage", "Remise sur la vidéo",
                   "Décodage du retour (FLAC)"}
CLASSES = {"homme", "femme", "locuteur", "inconnu", "muet"}


def ecrire(chemin, contenu=b""):
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(contenu, (dict, list)):
        chemin.write_text(json.dumps(contenu, ensure_ascii=False), encoding="utf-8")
    else:
        chemin.write_bytes(contenu)
    return chemin


def sorties_reelles():
    """Le dossier où se trouvent de vrais résultats de doubler (lecture seule), ou None."""
    for d in (chemins.DONNEES_DEFAUT / "sorties", chemins.DEPOT / "sorties"):
        if d.is_dir() and (any(d.glob("*/travail/*/rapport.json")) or any(d.glob("*/*/travail/*/rapport.json"))):
            return d
    return None


def copier_sorties(source, cible):
    """Copie légère : source.json, rapport.json (s'ils se lisent) et des médias vides de même nom. Rend
    {(groupe, titre, voix): rapport}."""
    copies = {}
    for travail in sorted(list(source.glob("*/travail")) + list(source.glob("*/*/travail"))):
        dossier = travail.parent
        rel = dossier.relative_to(source)
        if any(p.startswith(".") for p in rel.parts):
            continue
        d = cible / rel
        d.mkdir(parents=True, exist_ok=True)
        for f in dossier.iterdir():
            if f.is_file() and (f.suffix.lower() in rapports.EXT_VIDEO or f.suffix.lower() == ".mp3"):
                ecrire(d / f.name)
        (d / "travail").mkdir(exist_ok=True)
        src = rapports._json(travail / "source.json")
        if src is not None:
            ecrire(d / "travail" / "source.json", src)
        groupe = str(rel.parent) if len(rel.parts) > 1 else ""
        voix = sorted(p for p in travail.iterdir() if p.is_dir())
        if not voix:
            copies[(groupe, dossier.name, None)] = None
        for vd in voix:
            rapport = rapports._json(vd / "rapport.json")
            (d / "travail" / vd.name).mkdir(parents=True, exist_ok=True)
            if rapport is not None:
                ecrire(d / "travail" / vd.name / "rapport.json", rapport)
            copies[(groupe, dossier.name, vd.name)] = rapport
    return copies


SOURCE_REELLE = sorties_reelles()


@unittest.skipIf(SOURCE_REELLE is None, "aucun vrai résultat de doubler sur ce poste")
class EssaiCopiesDeVraisRapports(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="voixla-rapports-")
        self.addCleanup(self._tmp.cleanup)
        self.sorties = Path(self._tmp.name) / "sorties"
        self.copies = copier_sorties(SOURCE_REELLE, self.sorties)
        ecrire(self.sorties / ".kaggle" / "cache" / "travail" / "jules" / "rapport.json", {"mode": "chant"})
        # noms affichés des voix de ce poste, lus à l'exécution (aucun nom réel dans le code)
        self.noms = {v["id"]: v["nom"] for v in moteur.lister_voix(SOURCE_REELLE.parent, caps=frozenset())}
        self.resultats = rapports.lister_resultats(self.sorties, self.noms)

    def test_un_resultat_par_dossier_et_par_voix(self):
        self.assertTrue(self.copies)
        vus = {(r.groupe, r.titre, r.voix) for r in self.resultats}
        self.assertEqual(vus, set(self.copies))          # .kaggle (dossier caché) ignoré
        dates = [r.date for r in self.resultats]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_proprietes(self):
        for r in self.resultats:
            with self.subTest(r=r.cle):
                rapport = self.copies[(r.groupe, r.titre, r.voix)]
                self.assertEqual(r.rapport, rapport)
                if rapport is None:
                    self.assertFalse(r.complet)
                    continue
                self.assertIn(r.mode, ("chant", "parole"))
                self.assertEqual(r.complet, r.video is not None or r.mp3 is not None)
                if r.mp3 is not None:
                    self.assertEqual(r.mp3.suffix, ".mp3")
                if r.video is not None:
                    self.assertIn(r.video.suffix, rapports.EXT_VIDEO)
                if r.voix in self.noms and (r.video or r.mp3):
                    m = rapports.VOIX_DANS_NOM.search((r.video or r.mp3).stem)
                    self.assertEqual(r.voix_nom, m.group(1) if m else self.noms[r.voix])
                self.assertEqual(r.extrait, moteur.extrait_du_nom(r.titre))
                self.assertNotIn("(extrait", r.titre_court)
                self.assertGreater(r.duree_s, 0)
                self.assertEqual(r.decalage_source_s, float((r.source or {}).get("debut") or 0.0))
                if r.source and r.source.get("video"):
                    self.assertEqual(r.video_source, Path(r.source["video"]))
                self.assertIsInstance(r.resume_hauteur(), str)
                if r.mode == "chant":
                    self.assertTrue(r.resume_hauteur())
                self.assertTrue(r.pas is None or isinstance(r.pas, int), r.pas)

    def test_frise(self):
        for r in self.resultats:
            if not r.rapport:
                continue
            with self.subTest(r=r.cle):
                segs = rapports.segments_frise(r.rapport)
                self.assertTrue(segs)
                self.assertLessEqual({s["classe"] for s in segs}, CLASSES)
                for s in segs:
                    self.assertLessEqual(s["debut"], s["fin"])
                    self.assertLessEqual(s["fin"], r.duree_s + 1.0)
                self.assertLessEqual(len(rapports.fusionner_segments(segs)), len(segs))
                self.assertEqual(rapports.duree_frise(r.rapport, segs), float(r.rapport["duree_s"]))
                # chaque ligne de « frise » écrite par le noyau se relit (repli quand « morceaux » manque)
                lignes = r.rapport.get("frise") or []
                repli = rapports.segments_frise({"frise": lignes})
                self.assertEqual(len(repli), len(lignes), lignes)
                self.assertLessEqual({s["classe"] for s in repli}, CLASSES)

    def test_durees(self):
        for r in self.resultats:
            if not r.rapport:
                continue
            with self.subTest(r=r.cle):
                lignes = rapports.durees_etapes(r.rapport)
                self.assertTrue(lignes)
                for libelle, s, details in lignes:
                    self.assertIn(libelle, LIBELLES_ETAPES)
                    self.assertGreaterEqual(s, 0.0)
                    self.assertTrue(all(b >= 0 for _, b in details))
                a = rapports.attente_gpu_probable(r.rapport)
                self.assertTrue(a is None or a > 300)

    def test_modele_d_estimation(self):
        modele = rapports.modele_durees(self.resultats)
        self.assertEqual(set(modele), set(rapports.MODELE_DEFAUT))
        for cle, (a, b) in modele.items():
            self.assertEqual(a, rapports.MODELE_DEFAUT[cle][0])
            self.assertTrue(math.isfinite(b) and b >= 0, (cle, b))
        e = rapports.estimer([40.0, 45.0], pas=30, modele=modele)
        self.assertGreater(e["kaggle"], 0)
        self.assertEqual(e["attente"], 0.0)


# --- Rapports fabriqués ------------------------------------------------------------------------------------------------

def morceau(debut, fin, voix=None, decalage=0, muet=False, f0=180.0, base="voix seule"):
    return {"debut_s": debut, "fin_s": fin, "voix": voix, "decalage": decalage, "muet": muet,
            "f0_med": None if muet else f0, "base": base}


class EssaiVoixEtFrise(unittest.TestCase):
    def test_classe_et_libelle(self):
        cas = [({"voix": "homme"}, "homme", "homme"), ({"voix": "femme"}, "femme", "femme"),
               ({"voix": "homme 2"}, "homme", "homme 2"), ({"voix": "?"}, "inconnu", "inconnue"),
               ({"voix": None}, "inconnu", "inconnue"), ({}, "inconnu", "inconnue"), ({"voix": 2}, "locuteur", "voix 2"),
               ({"voix": True}, "inconnu", "True"), ({"voix": "homme", "muet": True}, "muet", "muet")]
        for m, classe, libelle in cas:
            with self.subTest(m=m):
                self.assertEqual(rapports.classe_voix(m), classe)
                self.assertEqual(rapports.libelle_voix(m), libelle)

    def test_segments_des_morceaux(self):
        r = {"mode": "chant", "morceaux": [
            morceau(0, 5, muet=True, base="muet"), morceau(5, 10, "homme"), morceau(10, 20, "femme", -12),
            morceau(20, 25, "?", -12), morceau(25, 30, None), morceau(30, 35, 2), {"debut_s": "x", "fin_s": 40}]}
        segs = rapports.segments_frise(r)
        self.assertEqual([(s["debut"], s["fin"], s["classe"], s["voix"], s["decalage"]) for s in segs], [
            (0.0, 5.0, "muet", "muet", 0), (5.0, 10.0, "homme", "homme", 0), (10.0, 20.0, "femme", "femme", -12),
            (20.0, 25.0, "inconnu", "inconnue", -12), (25.0, 30.0, "inconnu", "inconnue", 0),
            (30.0, 35.0, "locuteur", "voix 2", 0)])
        self.assertIsNone(segs[0]["f0"])
        self.assertEqual(segs[1]["base"], "voix seule")

    def test_voix_seule_de_genre_connu(self):
        r = {"mode": "chant", "decision_hauteur": {"voix_seule": "femme"},
             "morceaux": [morceau(0, 5, None), morceau(5, 10, "?"), morceau(10, 15, None, muet=True), morceau(15, 20, "homme")]}
        self.assertEqual([(s["classe"], s["voix"]) for s in rapports.segments_frise(r)],
                         [("femme", "femme"), ("inconnu", "inconnue"), ("muet", "muet"), ("homme", "homme")])

    def test_parole_sans_locuteurs(self):
        r = {"mode": "parole", "morceaux": [morceau(0, 5, None), morceau(5, 10, 1), morceau(10, 12, None, muet=True)]}
        self.assertEqual([(s["classe"], s["voix"]) for s in rapports.segments_frise(r)],
                         [("locuteur", "voix seule"), ("locuteur", "voix 1"), ("muet", "muet")])

    def test_repli_sur_les_lignes_de_frise(self):
        r = {"duree_s": 340.0, "frise": [
            "00:00.0–00:45.1           f0  262 Hz  → +0 dt  (voix seule)",
            "00:15.7–00:35.3  voix femme  f0  121 Hz  → -12 dt  (voix de femme)",
            "00:35.3–00:40.5  voix ?  f0  142 Hz  → -12 dt  (ambigu, comme le précédent)",
            "01:59.7–05:27.4  voix homme 2  f0  222 Hz  → +3.5 dt  (voix seule (homme))",
            f"05:27.4–05:40.0  voix femme  f0    0 Hz  → {MOINS}12 dt  (voix seule (femme))",
            "ligne illisible"]}
        segs = rapports.segments_frise(r)
        self.assertEqual(len(segs), 5)
        self.assertEqual(segs[0], {"debut": 0.0, "fin": 45.1, "classe": "inconnu", "voix": "", "decalage": 0.0,
                                   "f0": 262.0, "base": "voix seule"})
        self.assertEqual((segs[1]["classe"], segs[1]["voix"], segs[1]["decalage"]), ("femme", "femme", -12.0))
        self.assertEqual((segs[2]["classe"], segs[2]["voix"]), ("inconnu", "?"))
        self.assertEqual((segs[3]["debut"], segs[3]["fin"], segs[3]["classe"], segs[3]["voix"], segs[3]["decalage"]),
                         (119.7, 327.4, "homme", "homme 2", 3.5))
        self.assertEqual(segs[4]["decalage"], -12.0)
        self.assertEqual(segs[3]["base"], "voix seule (homme)")
        self.assertEqual(rapports.segments_frise({}), [])
        self.assertEqual(rapports.segments_frise(None), [])

    def test_fusion_et_recherche(self):
        segs = [{"debut": 0.0, "fin": 5.0, "classe": "homme", "voix": "homme", "decalage": 0},
                {"debut": 5.02, "fin": 9.0, "classe": "homme", "voix": "homme", "decalage": 0},
                {"debut": 9.5, "fin": 12.0, "classe": "homme", "voix": "homme", "decalage": 0},
                {"debut": 12.0, "fin": 20.0, "classe": "femme", "voix": "femme", "decalage": -12}]
        copie = json.loads(json.dumps(segs))
        f = rapports.fusionner_segments(segs)
        self.assertEqual([(s["debut"], s["fin"], s["classe"]) for s in f],
                         [(0.0, 9.0, "homme"), (9.5, 12.0, "homme"), (12.0, 20.0, "femme")])
        self.assertEqual(segs, copie)                        # l'entrée n'est pas modifiée
        self.assertEqual(rapports.segment_a(f, 9.0), None)   # entre deux morceaux
        self.assertEqual(rapports.segment_a(f, 12.0)["classe"], "femme")
        self.assertEqual(rapports.segment_a(f, 0.0)["classe"], "homme")
        self.assertIsNone(rapports.segment_a(f, 20.0))

    def test_duree_frise(self):
        self.assertEqual(rapports.duree_frise({"duree_s": 40.48}), 40.48)
        self.assertEqual(rapports.duree_frise({"morceaux": [morceau(0, 5), morceau(5, 12.5)]}), 12.5)
        self.assertEqual(rapports.duree_frise({}), 0.0)


RAPPORT_LOT = {
    "mode": "chant", "duree_s": 45.08,
    "temps": {"separation_s": 40.1, "analyse_s": 1.9, "conversion_s": 56.6, "total_video_s": 99.4},
    "temps_local": {"extraction_s": 0.2, "envoi_jeu_s": 43.8, "attente_demarrage_noyau_s": 3.3,
                    "noyau_depuis_envoi_s": 233.4, "rapatriement_s": 13.7, "reste_local_s": 0.1,
                    "session_kaggle": {"installation_s": 40.8, "separation_totale_s": 60.3, "chargement_modeles_s": 35.0,
                                       "total_s": 201.5},
                    "videos_dans_la_session": 1, "mix_local_s": 1.4, "remise_video_s": 1.1},
}


class EssaiDurees(unittest.TestCase):
    def test_durees_etapes(self):
        lignes = rapports.durees_etapes(RAPPORT_LOT)
        self.assertEqual([(l, s) for l, s, _ in lignes], [
            ("Extraction du son", 0.2), ("Envoi sur Kaggle", 43.8), ("Démarrage du noyau", 3.3),
            ("GPU Kaggle, depuis l’envoi", 233.4), ("Rapatriement", 13.7), ("Reste (original − voix)", 0.1),
            ("Mixage", 1.4), ("Remise sur la vidéo", 1.1)])
        self.assertEqual(lignes[3][2], [("Installation des outils", 40.8), ("Chargement des modèles", 35.0),
                                        ("Séparation (toute la session)", 60.3), ("Séparation de cette vidéo", 40.1),
                                        ("Analyse (mode, hauteur)", 1.9), ("Conversion Seed-VC", 56.6),
                                        ("Noyau en tout", 201.5)])

    def test_durees_sans_temps_local(self):
        lignes = rapports.durees_etapes({"temps": RAPPORT_LOT["temps"]})
        self.assertEqual([(l, s) for l, s, _ in lignes], [("GPU Kaggle (cette vidéo)", 99.4)])
        self.assertEqual(len(lignes[0][2]), 3)
        self.assertEqual(rapports.durees_etapes({}), [])
        self.assertEqual(rapports.durees_etapes(None), [])

    def test_attente_gpu(self):
        long = {"temps_local": {"noyau_depuis_envoi_s": 15294.9, "session_kaggle": {"total_s": 603.9}}}
        self.assertAlmostEqual(rapports.attente_gpu_probable(long), 14691.0, places=6)
        self.assertIsNone(rapports.attente_gpu_probable(RAPPORT_LOT))      # 32 s : rien de notable
        self.assertIsNone(rapports.attente_gpu_probable({"temps_local": {"noyau_depuis_envoi_s": 100}}))
        self.assertIsNone(rapports.attente_gpu_probable(None))


def resultat(duree, temps_local=None):
    r = {"duree_s": duree}
    if temps_local is not None:
        r["temps_local"] = temps_local
    return Resultat(dossier=Path("/s/x"), voix="jules", rapport=r)


class EssaiEstimation(unittest.TestCase):
    def test_modele_ajuste(self):
        seul1 = {"extraction_s": 1.5, "mix_local_s": 6.0, "remise_video_s": 4.0, "envoi_jeu_s": 50.0,
                 "rapatriement_s": 17.0, "noyau_depuis_envoi_s": 400.0, "session_kaggle": {"total_s": 380.0},
                 "videos_dans_la_session": 1}
        seul2 = {"extraction_s": 2.5, "mix_local_s": 11.0, "remise_video_s": 7.0, "envoi_jeu_s": 65.0,
                 "rapatriement_s": 24.0, "noyau_depuis_envoi_s": 5000.0, "session_kaggle": {"total_s": 600.0},
                 "videos_dans_la_session": 1}
        lot = {"extraction_s": 1.0, "envoi_jeu_s": 999.0, "noyau_depuis_envoi_s": 999.0,
               "session_kaggle": {"total_s": 100.0}, "videos_dans_la_session": 3}
        res = [resultat(100, seul1), resultat(100, seul2), resultat(50, lot), resultat(100), resultat(0.5, seul1),
               Resultat(dossier=Path("/s/y"), voix=None)]
        m = rapports.modele_durees(res)
        attendu = {"extraction": 0.01, "mix": 0.075, "video": 0.045, "envoi": 0.225, "rapatriement": 0.105,
                   "kaggle": 3.8,    # 2,6 et 5,0 : la longue attente d'une place GPU est remplacée par total + 40 s
                   "attente": 0.0}
        for cle, b in attendu.items():
            self.assertEqual(m[cle][0], rapports.MODELE_DEFAUT[cle][0])
            self.assertAlmostEqual(m[cle][1], b, places=9, msg=cle)

    def test_modele_sans_historique(self):
        self.assertEqual(rapports.modele_durees([]), rapports.MODELE_DEFAUT)

    def test_estimer(self):
        e = rapports.estimer([60, 40])
        self.assertEqual(e, {"extraction": 1.3, "mix": 7.0, "video": 5.0, "envoi": 50.0, "rapatriement": 17.0,
                             "kaggle": 400.0, "attente": 0.0, "fini": 0.0})
        self.assertEqual(rapports.estimer([60, 40], pas=50)["kaggle"], 504.0)
        vide = rapports.estimer([])
        self.assertEqual((vide["extraction"], vide["kaggle"], vide["envoi"]), (0.5, 140.0, 35.0))
        self.assertEqual(rapports.estimer([None, 10])["mix"], 1.0 + 1.0 + 0.05 * 10)

    def test_facteur_pas(self):
        self.assertEqual(rapports.facteur_pas(30), 1.0)
        self.assertAlmostEqual(rapports.facteur_pas(50), 1.4)
        self.assertAlmostEqual(rapports.facteur_pas(70), 1.8)
        self.assertEqual(rapports.facteur_pas(None), 1.0)
        self.assertEqual(rapports.facteur_pas(0), 1.0)          # 0 compte comme « non indiqué »
        self.assertAlmostEqual(rapports.facteur_pas(5), 0.5)
        self.assertEqual(rapports.facteur_pas(-100), 0.3)


# --- Dossiers de sortie fabriqués --------------------------------------------------------------------------------------

class EssaiDossiers(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="voixla-rapports-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.sorties = self.tmp / "sorties"

    def fabriquer(self, titre, voix=("jules",), medias=("Clip - voix Jules.mp4", "Clip - voix Jules.mp3"),
                  rapport=None, source=None, groupe=""):
        d = self.sorties / groupe / titre if groupe else self.sorties / titre
        (d / "travail").mkdir(parents=True)
        for v in voix:
            (d / "travail" / v).mkdir()
            if rapport is not False:
                ecrire(d / "travail" / v / "rapport.json", rapport or {**RAPPORT_LOT, "modele": "chant44k",
                                                                       "variantes": {"voix_convertie.flac": {"pas_diffusion": 50}}})
        for m in medias:
            ecrire(d / m)
        if source is not None:
            ecrire(d / "travail" / "source.json", source)
        return d

    def test_un_resultat_complet(self):
        video = ecrire(self.tmp / "Vidéos" / "Clip.mp4")
        d = self.fabriquer("Clip (extrait 1m00,0+0m40,0)", source={"video": str(video), "debut": 54.921, "duree": 45.08})
        (r,) = rapports.lire_dossier(d, {"jules": "Jules"})
        self.assertEqual((r.voix, r.voix_nom, r.titre, r.titre_court), ("jules", "Jules", d.name, "Clip"))
        self.assertEqual(r.cle, f"{d}|jules")
        self.assertEqual(r.groupe, "")
        self.assertEqual(r.extrait, (60.0, 40.0))
        self.assertEqual((r.video.name, r.mp3.name), ("Clip - voix Jules.mp4", "Clip - voix Jules.mp3"))
        self.assertTrue(r.complet)
        self.assertEqual((r.mode, r.duree_s, r.modele, r.pas), ("chant", 45.08, "chant44k", 50))
        self.assertEqual(r.travail, d / "travail")
        self.assertEqual(r.dossier_voix, d / "travail" / "jules")
        self.assertEqual(r.video_source, video)
        self.assertTrue(r.source_presente)
        self.assertEqual(r.decalage_source_s, 54.921)
        self.assertIsNone(r.original_wav)
        ecrire(d / "travail" / "original.wav")
        self.assertEqual(r.original_wav, d / "travail" / "original.wav")
        self.assertEqual(r.date, r.video.stat().st_mtime)
        video.unlink()
        self.assertFalse(r.source_presente)

    def test_inacheve_et_sans_voix(self):
        d = self.fabriquer("Inachevé", rapport=False, medias=())
        (r,) = rapports.lire_dossier(d)
        self.assertIsNone(r.rapport)
        self.assertFalse(r.complet)
        self.assertEqual(r.pas, None)
        self.assertEqual(r.resume_hauteur(), "")
        d = self.fabriquer("Ancien", voix=(), medias=("Ancien - voix Jules.mp4", "Ancien - voix Jules.mp3"))
        (r,) = rapports.lire_dossier(d)
        self.assertIsNone(r.voix)
        self.assertEqual(r.video.name, "Ancien - voix Jules.mp4")
        self.assertFalse(r.complet)        # pas de rapport

    def test_duree_d_apres_la_source(self):
        d = self.fabriquer("Clip", rapport={"mode": "parole"}, source={"video": "/v/Clip.mp4", "duree": 12.5})
        (r,) = rapports.lire_dossier(d)
        self.assertEqual(r.duree_s, 12.5)
        self.assertFalse(r.source_presente)

    def deux_voix(self):
        return self.fabriquer("Clip", voix=("jules", "jules-b"), medias=(
            "Clip - voix Jules.mp4", "Clip - voix Jules.mp3", "Clip - voix Jules (B).mkv",
            "Clip - voix Jules (B).mp3", "comparaison_30pas.mp3", "comparaison_50pas.mp3",
            "comparaison_30pas (voix Jules (B)).mp3", "comparaison_50pas (voix Jules (B)).mp3", "notes.txt"))

    def test_deux_voix(self):
        d = self.deux_voix()
        res = {r.voix: r for r in rapports.lire_dossier(d, {"jules": "Jules", "jules-b": "Jules (B)"})}
        self.assertEqual(res["jules"].video.name, "Clip - voix Jules.mp4")
        self.assertEqual(res["jules-b"].video.name, "Clip - voix Jules (B).mkv")
        self.assertEqual(res["jules-b"].mp3.name, "Clip - voix Jules (B).mp3")
        self.assertEqual([c.name for c in res["jules-b"].comparaisons],
                         ["comparaison_30pas (voix Jules (B)).mp3", "comparaison_50pas (voix Jules (B)).mp3"])
        # sans les noms affichés, « jules-b » ne retrouve pas ses médias
        res = {r.voix: r for r in rapports.lire_dossier(d)}
        self.assertIsNone(res["jules-b"].video)

    # Bogue corrigé, gardé en non-régression : avec plusieurs voix dans un même dossier, la voix par défaut recevait
    # aussi les extraits de comparaison des autres voix (« comparaison_30pas (voix Jules (B)).mp3 »). doubler n'ajoute
    # aucun suffixe pour sa voix par défaut : seuls les fichiers sans « (voix » lui reviennent.
    def test_comparaisons_de_la_voix_par_defaut(self):
        d = self.deux_voix()
        res = {r.voix: r for r in rapports.lire_dossier(d, {"jules": "Jules", "jules-b": "Jules (B)"})}
        self.assertEqual([c.name for c in res["jules"].comparaisons], ["comparaison_30pas.mp3", "comparaison_50pas.mp3"])

    def trois_voix(self, rapports_voix=None):
        """marie (comparaisons suffixées), jules et paul (aucune) ; rapports_voix : {voix: rapport} propre à une voix."""
        d = self.fabriquer("Clip", voix=("jules", "marie", "paul"), medias=(
            "comparaison_30pas.mp3", "comparaison_50pas.mp3", "comparaison_30pas (voix Marie).mp3",
            "comparaison_50pas (voix Marie).mp3"))
        for v, rapport in (rapports_voix or {}).items():
            ecrire(d / "travail" / v / "rapport.json", rapport)
        return d

    def comparaisons(self, d, **options):
        return {r.voix: [c.name for c in r.comparaisons] for r in rapports.lire_dossier(d, **options)}

    def test_comparaisons_sans_suffixe_ambigues(self):
        # jules et paul n'ont aucune comparaison suffixée : sans voix par défaut connue, aucune des deux ne les reçoit
        d = self.trois_voix()
        self.assertEqual(self.comparaisons(d), {"jules": [], "paul": [], "marie": [
            "comparaison_30pas (voix Marie).mp3", "comparaison_50pas (voix Marie).mp3"]})
        # la voix par défaut de doubler tranche, quelle qu'elle soit ; une voix par défaut hors des candidates ne compte pas
        sans = ["comparaison_30pas.mp3", "comparaison_50pas.mp3"]
        self.assertEqual(self.comparaisons(d, voix_defaut="paul")["paul"], sans)
        self.assertEqual(self.comparaisons(d, voix_defaut="paul")["jules"], [])
        self.assertEqual(self.comparaisons(d, voix_defaut="jules")["jules"], sans)
        self.assertEqual(self.comparaisons(d, voix_defaut="marie")["jules"], [])

    def test_comparaisons_sans_suffixe_lues_dans_le_rapport(self):
        # seul le rapport de paul annonce des extraits de comparaison (30 et 50 pas) : ils sont à lui, même si la voix
        # par défaut indiquée est une autre (elle a pu changer depuis le doublage)
        d = self.trois_voix({"paul": {**RAPPORT_LOT, "comparaison_pas": {"debut_s": 1.0, "fin_s": 31.0, "pas": [30, 50],
                                                                         "fichier": "voix_convertie_50pas.flac"}}})
        sans = ["comparaison_30pas.mp3", "comparaison_50pas.mp3"]
        self.assertEqual(self.comparaisons(d)["paul"], sans)
        self.assertEqual(self.comparaisons(d, voix_defaut="jules")["paul"], sans)
        self.assertEqual(self.comparaisons(d, voix_defaut="jules")["jules"], [])

    def test_comparaisons_d_une_seule_voix(self):
        d = self.fabriquer("Clip", voix=("paul",), medias=("comparaison_30pas.mp3", "comparaison_50pas (voix Paul).mp3"))
        (r,) = rapports.lire_dossier(d, voix_defaut="jules")
        self.assertEqual([c.name for c in r.comparaisons], ["comparaison_30pas.mp3", "comparaison_50pas (voix Paul).mp3"])

    def test_une_voix_au_nom_affiche_inconnu(self):
        d = self.fabriquer("Clip", voix=("marie",), medias=("Clip - voix Marie Curie.mp4", "Clip - voix Marie Curie.mp3"))
        (r,) = rapports.lire_dossier(d)
        self.assertEqual(r.voix_nom, "Marie Curie")
        self.assertTrue(r.complet)

    def test_sous_dossiers(self):
        self.fabriquer("Clip")
        self.fabriquer("Clip", groupe="essais-mix-v2")
        self.fabriquer("Clip", groupe="a/b")                     # trop profond
        self.fabriquer("Clip", groupe=".cache")                  # caché
        res = rapports.lister_resultats(self.sorties)
        self.assertEqual(sorted(r.groupe for r in res), ["", "essais-mix-v2"])
        self.assertEqual(len(rapports.lister_resultats(self.sorties, profondeur=3)), 3)
        self.assertEqual(rapports.lister_resultats(self.tmp / "absent"), [])

    # Bogue corrigé, gardé en non-régression : Resultat.groupe lit self._groupe, posé seulement par lire_dossier ; un Resultat construit autrement
    # (essai, cache, modele_durees sur des résultats fabriqués) lève AttributeError. Correction proposée : un vrai champ
    # de la dataclass, « groupe: str = "" » (lire_dossier écrit r.groupe), ou getattr(self, "_groupe", "").
    def test_groupe_d_un_resultat_construit_a_la_main(self):
        self.assertEqual(Resultat(dossier=Path("/s/Clip"), voix="jules").groupe, "")


class EssaiResumeHauteur(unittest.TestCase):
    def resume(self, mode, decision):
        return Resultat(dossier=Path("/s/x"), voix="jules", rapport={"mode": mode, "decision_hauteur": decision}).resume_hauteur()

    def test_phrases(self):
        self.assertEqual(self.resume("parole", {"voix": 2}), "parole, 2 voix")
        self.assertEqual(self.resume("parole", {"voix": 1}), "parole")
        self.assertEqual(self.resume("parole", {}), "parole")
        self.assertEqual(self.resume("chant", {"decalage_par_genre": {"homme": 0, "femme": -12}, "voix_seule": "femme"}),
                         f"voix seule (femme), {MOINS}12 dt")
        self.assertEqual(self.resume("chant", {"decalage_par_classe": {"homme": 0, "femme": -12}, "voix_seule": "homme"}),
                         "voix seule (homme), +0 dt")
        self.assertEqual(self.resume("chant", {"decalage_par_genre": {"homme": 0, "femme": -12}, "voix_seule": None}),
                         f"duo : homme +0 dt, femme {MOINS}12 dt")
        self.assertEqual(self.resume("chant", {"decalage_global": 0}), "décalage global +0 dt")
        self.assertEqual(self.resume("chant", {}), "")

    def test_decalages_decimaux(self):
        self.assertEqual(self.resume("chant", {"decalage_par_genre": {"homme": 0.0, "femme": -12.0}}),
                         f"duo : homme +0 dt, femme {MOINS}12 dt")
        self.assertEqual(self.resume("chant", {"decalage_global": -0.5}), f"décalage global {MOINS}0,5 dt")



# --- Nouvelle chaîne : moteur, repli, nettoyage (rapports de doubler depuis le 04/11/2026) ---------------------------

RAPPORT_NETTOYE = {
    "mode": "chant", "duree_s": 40.5, "modele": "yingmusic", "moteur": "yingmusic",
    "moteur_info": {"chant_demande": "yingmusic", "utilise": "yingmusic", "modele": "yingmusic", "repli": None},
    "nettoyage": {"demande": True, "applique": True,
                  "niveaux_db": {"voix": -19.5, "lead": -19.8, "choeurs": -40.2, "reverb_jetee": -35.2}},
    "variantes": {"voix_convertie.flac": {"modele": "yingmusic", "moteur": "yingmusic", "pas_diffusion": 30}},
    "temps": {"separation_s": 23.0, "analyse_s": 1.5, "nettoyage_s": 74.6, "courbes_s": 0.3, "conversion_s": 128.3,
              "total_video_s": 228.8},
    "temps_local": {"extraction_s": 0.7, "envoi_jeu_s": 52.4, "noyau_depuis_envoi_s": 669.8, "estimation_noyau_s": 655,
                    "rapatriement_s": 20.7, "decodage_retour_s": 0.2, "videos_dans_la_session": 2,
                    "session_kaggle": {"installation_s": 42.2, "chargement_modeles_s": 37.7,
                                       "chargement_nettoyage_s": 28.8, "chargement_yingmusic_s": 24.5,
                                       "separation_totale_s": 89.0, "nettoyage_totale_s": 192.2,
                                       "yingmusic_total_s": 262.8, "total_s": 634.8},
                    "mix_local_s": 4.9, "remise_video_s": 3.1},
}


class EssaiNouvelleChaine(unittest.TestCase):
    def res(self, rapport):
        return Resultat(dossier=Path("/s/x"), voix="jules", rapport=rapport)

    def test_moteur_et_nettoyage(self):
        r = self.res(RAPPORT_NETTOYE)
        self.assertEqual((r.moteur, r.libelle_moteur(), r.moteur_demande, r.repli),
                         ("yingmusic", "YingMusic-SVC", "yingmusic", None))
        applique, texte = r.resume_nettoyage()
        self.assertTrue(applique)
        self.assertIn("voix principale −19,8 dBFS", texte)
        self.assertIn("reverb retirée −35,2 dBFS", texte)
        self.assertIn("chœurs 20 dB sous la voix principale", texte)

    def test_repli_sur_seedvc(self):
        rap = {**RAPPORT_NETTOYE, "modele": "chant44k", "moteur": "seedvc",
               "moteur_info": {"chant_demande": "yingmusic", "utilise": "seedvc", "repli": "poids introuvables"}}
        r = self.res(rap)
        self.assertEqual((r.moteur, r.libelle_moteur(), r.repli), ("seedvc", "Seed-VC 44 kHz", "poids introuvables"))
        self.assertEqual(rapports.libelle_conversion(rap), "Conversion Seed-VC")

    def test_nettoyage_non_applique_et_ancien_rapport(self):
        r = self.res({"nettoyage": {"demande": False, "applique": False, "raison": "nettoyage désactivé"}})
        self.assertEqual(r.resume_nettoyage(), (False, "nettoyage désactivé"))
        ancien = self.res({"modele": "chant44k"})
        self.assertIsNone(ancien.resume_nettoyage())
        self.assertEqual((ancien.moteur, ancien.libelle_moteur(), ancien.repli), ("seedvc", "Seed-VC 44 kHz", None))
        self.assertEqual(self.res({"modele": "parole22k"}).libelle_moteur(), "Seed-VC 22 kHz (parole)")
        self.assertEqual(self.res(None).moteur, None)

    def test_durees_de_la_nouvelle_chaine(self):
        lignes = dict((l, (s, d)) for l, s, d in rapports.durees_etapes(RAPPORT_NETTOYE))
        self.assertEqual(lignes["Décodage du retour (FLAC)"][0], 0.2)
        details = dict(lignes["GPU Kaggle, depuis l’envoi"][1])
        self.assertEqual(details["Nettoyage de cette vidéo"], 74.6)
        self.assertEqual(details["Nettoyage (toute la session)"], 192.2)
        self.assertEqual(details["YingMusic-SVC (toute la session)"], 262.8)
        self.assertEqual(details["Conversion YingMusic-SVC"], 128.3)
        self.assertEqual(details["Estimé par doubler"], 655)
        self.assertNotIn("Conversion Seed-VC", details)

    def test_estimation_du_chant_nettoye(self):
        res = [self.res(RAPPORT_NETTOYE)]
        m = rapports.modele_durees(res)
        self.assertAlmostEqual(m["kaggle_nettoye"][1], 228.8 / 40.5, places=6)
        sans = rapports.estimer([60], 30, m)["kaggle"]
        avec = rapports.estimer([60], 30, m, nettoyage=True)["kaggle"]
        self.assertGreater(avec, 1.8 * sans)
        # en parole, pas de nettoyage ; avec YingMusic, les pas ne changent rien
        self.assertEqual(rapports.estimer([60], 30, m, nettoyage=True, mode="parole")["kaggle"], sans)
        self.assertEqual(rapports.estimer([60], 100, m, nettoyage=True)["kaggle"], avec)
        self.assertGreater(rapports.estimer([60], 50, m, moteur="seedvc")["kaggle"], sans)


if __name__ == "__main__":
    unittest.main()
