"""Une voix par chanteur et version auto-tune, côté interface (sans écran) : syntaxe et options de doubler, lecture des
résultats (voix combinée « a+b », fichiers « (auto-tune) », passages par voix, résumés), pistes (doublage et voix seule
auto-tunés, « Voix converties seules »), mise à la corbeille, préparation à l'avance, courbes, puis un doublage entier
par le faux doubler. Voix et titres inventés (jules, marie)."""

import contextlib
import io
import json
import os
from pathlib import Path
from unittest import mock

from voixla import analyse, chemins, corbeille, moteur, pistes, preparation, rapports

from .commun import EssaiIsole, creer_voix


class Syntaxe(EssaiIsole):
    def test_specification(self):
        self.assertEqual(moteur.lire_spec_voix("jules"), (None, [(None, "jules")]))
        self.assertEqual(moteur.lire_spec_voix("femme:marie,homme:jules"),
                         ("genre", [("homme", "jules"), ("femme", "marie")]))
        self.assertEqual(moteur.lire_spec_voix("chanteur2:marie,chanteur1:jules")[0], "ordre")
        self.assertIsNone(moteur.lire_spec_voix("homme:jules,chanteur2:marie"))
        self.assertEqual(moteur.spec_voix("genre", ["jules", "marie"]), "homme:jules,femme:marie")
        self.assertEqual(moteur.spec_voix("ordre", ["jules", "marie"]), "chanteur1:jules,chanteur2:marie")
        self.assertEqual(moteur.spec_voix("genre", ["jules", "jules"]), "jules", "même voix : une voix")
        self.assertEqual(moteur.id_voix("femme:marie,homme:jules"), "jules+marie")
        self.assertEqual(moteur.id_voix("jules"), "jules")
        self.assertEqual(moteur.membres_voix("jules+marie"), ["jules", "marie"])
        rapport = {"voix_cibles": {"voix": [{"cle": "homme", "nom": "jules"}, {"cle": "femme", "nom": "marie"}]}}
        self.assertEqual(moteur.spec_de_rapport("jules+marie", rapport), "homme:jules,femme:marie")
        self.assertEqual(moteur.spec_de_rapport("jules", {}), "jules")

    def test_aide_et_arguments(self):
        caps = moteur.capacites()
        self.assertIn("voix_par_chanteur", caps)
        self.assertTrue({"--autotune", "--sans-autotune", "--autotune-gamme"} <= caps)
        v = self.video()
        a = moteur.args_doublage([v], {"voix": "homme:jules,femme:marie", "pas": 30, "autotune": 60,
                                       "autotune_gamme": "la-mineur"}, self.donnees, caps=caps)
        self.assertIn("--voix=homme:jules,femme:marie", a)
        self.assertIn("--autotune=60", a)
        self.assertIn("--autotune-gamme=la-mineur", a)
        a = moteur.args_doublage([v], {"voix": "jules", "autotune": 50, "autotune_gamme": "auto"}, self.donnees, caps=caps)
        self.assertNotIn("--autotune-gamme=auto", " ".join(a))
        a = moteur.args_doublage([v], {"voix": "jules", "sans_autotune": True}, self.donnees, caps=caps)
        self.assertIn("--sans-autotune", a)
        self.assertEqual(moteur.args_autotune({"autotune": 60}, frozenset()), [], "doubler sans auto-tune : rien")

    def test_configuration(self):
        self.assertEqual(moteur.autotune_par_defaut({"autotune": 40}), 40)
        self.assertEqual(moteur.autotune_par_defaut({"contenu": {"autotune": 150}}), 100)
        self.assertEqual(moteur.autotune_par_defaut({}), 0)
        self.assertEqual(moteur.x42_autotune({}), (False, None))
        self.assertEqual(moteur.x42_autotune({"x42_autotune": None}), (True, None))
        conf = moteur.config_effective()
        self.assertEqual(moteur.x42_autotune(conf)[0], conf.get("par_doubler", False))
        os.environ["FAUX_DOUBLER_X42"] = "absent"
        ok, _ = moteur.ecrire_config("autotune", "55")
        self.assertTrue(ok)
        self.assertEqual(moteur.autotune_par_defaut(chemins.lire_config()), 55)


def resultat_duo(dossier, autotune=True, ident="jules+marie"):
    """Un dossier de sortie d'un duo « voix Jules et Marie », avec sa version auto-tune (fichiers vides)."""
    tv = dossier / "travail" / ident
    tv.mkdir(parents=True)
    (dossier / "travail" / "source.json").write_text(json.dumps({"video": str(dossier / "Clip.mp4")}))
    (dossier / "travail" / "instrumental.wav").write_bytes(b"")
    base = "Clip - voix Jules et Marie"
    for f in (f"{base}.mp4", f"{base}.mp3") + ((f"{base} (auto-tune).mp4", f"{base} (auto-tune).mp3",
                                               f"{base} (auto-tune).lrc") if autotune else ()):
        (dossier / f).write_bytes(b"")
    for f in ("seche_voix_convertie.wav", "reverb_voix_convertie.wav") + \
            (("seche_voix_autotune.wav", "reverb_voix_autotune.wav", "mix_autotune.wav") if autotune else ()):
        (tv / f).write_bytes(b"x")
    rapport = {"mode": "chant", "duree_s": 30.0, "mix": {"gains_db": {"seche": 6.0, "reverb": 1.0}, "limite_dbtp": -1.0},
               "voix_cibles": {"mode": "genre", "voix": [{"cle": "homme", "nom": "jules"}, {"cle": "femme", "nom": "marie"}]},
               "decision_hauteur": {"voix": 2, "message": "duo : homme → jules (+0), femme → marie (+0)",
                                    "chanteurs": [{"nom": "homme", "voix": "jules", "decalage": 0},
                                                  {"nom": "femme", "voix": "marie", "decalage": 0}]},
               "affectation": {"passages": [{"debut_s": 1.0, "fin_s": 10.0, "voix": ["jules"]},
                                            {"debut_s": 10.0, "fin_s": 20.0, "voix": ["jules", "marie"]}],
                               "secondes_par_voix": {"jules": 9.0, "marie": 0.0, "ensemble": 10.0}, "morceaux_en_duo": 2},
               "morceaux": [{"i": 0, "debut_s": 0.0, "fin_s": 10.0, "voix": "homme", "voix_cible": "jules",
                             "decalage": 0, "voix_pistes": {"lead_sec": "jules"}},
                            {"i": 1, "debut_s": 10.0, "fin_s": 20.0, "voix": "femme", "voix_cible": "marie",
                             "decalage": 0, "voix_pistes": {"lead_sec": "marie", "choeurs_sec": "jules"}}],
               "temps_local": {"mix_local_s": 5.0}}
    if autotune:
        rapport["autotune"] = {"applique": True, "intensite": 70, "temps_s": 2.5,
                               "gamme": {"type": "tonalite", "tonalite": "fa majeur", "demandee": "auto"},
                               "mesures": {"avant": {"ecart_median_cents": 31.0, "part_a_10_cents": 0.2},
                                           "apres": {"ecart_median_cents": 4.0, "part_a_10_cents": 0.86}},
                               "gains_db": {"seche": 6.0, "reverb": 1.0},
                               "fichiers": {"lrc": f"{base} (auto-tune).lrc"}}
    (tv / "rapport.json").write_text(json.dumps(rapport, ensure_ascii=False))
    return tv


class Resultats(EssaiIsole):
    def setUp(self):
        super().setUp()
        self.dossier = chemins.dossier_sorties(self.donnees) / "Clip"
        self.tv = resultat_duo(self.dossier)

    def resultat(self):
        return rapports.lire_dossier(self.dossier, {"jules": "Jules", "marie": "Marie"})[0]

    def test_voix_combinee_et_version_auto_tune(self):
        r = self.resultat()
        self.assertEqual((r.voix, r.voix_nom, r.voix_membres, r.plusieurs_voix),
                         ("jules+marie", "Jules et Marie", ["jules", "marie"], True))
        self.assertEqual(r.video.name, "Clip - voix Jules et Marie.mp4")
        self.assertEqual(r.video_autotune.name, "Clip - voix Jules et Marie (auto-tune).mp4")
        self.assertEqual(r.mp3_autotune.name, "Clip - voix Jules et Marie (auto-tune).mp3")
        self.assertEqual(r.lrc_autotune.name, "Clip - voix Jules et Marie (auto-tune).lrc")
        self.assertEqual(len(rapports.lister_resultats(self.dossier.parent)), 1, "pas de fausse voix « (auto-tune) »")
        self.assertEqual([v["nom"] for v in r.voix_cibles], ["jules", "marie"])
        self.assertIn("duo : homme → jules", r.resume_hauteur())
        lignes = r.resume_voix_cibles()
        self.assertTrue(lignes[0].startswith("duo : homme → jules"))
        self.assertTrue(any("ensemble" in l for l in lignes))
        ton, mesures = r.resume_autotune()
        self.assertEqual(ton, "fa majeur (détectée)")
        self.assertIn("31 → 4 cents", mesures)
        self.assertIn("20 % → 86 %", mesures)

    def test_auto_tune_non_applique(self):
        rap = json.loads((self.tv / "rapport.json").read_text())
        rap["autotune"] = {"applique": False, "raison": "x42-autotune absent"}
        (self.tv / "rapport.json").write_text(json.dumps(rap))
        r = self.resultat()
        self.assertIsNone(r.autotune)
        self.assertIsNone(r.resume_autotune())
        self.assertNotIn("doublage_at", pistes.choisir_pistes(r, "mp3"))

    def test_frise_et_passages(self):
        r = self.resultat()
        segs = rapports.segments_frise(r.rapport)
        self.assertEqual(segs[1]["voix_cible"], "marie")
        self.assertEqual(segs[1]["voix_pistes"], {"lead_sec": "marie", "choeurs_sec": "jules"})
        p = rapports.passages_voix(r.rapport)
        self.assertEqual(p[1], {"debut": 10.0, "fin": 20.0, "voix": ["jules", "marie"]})
        self.assertEqual(rapports.passages_voix({}), [])
        mixage = next(l for l in rapports.durees_etapes(r.rapport) if l[0] == "Mixage")
        self.assertEqual(mixage[2], [("dont la version auto-tune", 2.5)])

    def test_pistes(self):
        r = self.resultat()
        p = pistes.choisir_pistes(r, "mp3")
        self.assertEqual(list(p)[:5], list(pistes.CLES))
        self.assertEqual(p["ma_voix"].libelle, "Voix converties seules")
        self.assertEqual(p["doublage_at"].fichier, r.mp3_autotune)
        self.assertEqual(p["ma_voix_at"].libelle, "Voix converties seules (auto-tune)")
        self.assertTrue(p["ma_voix_at"].a_preparer)
        plan = pistes.plan_ma_voix(r.dossier_voix, r.rapport, autotune=True)
        self.assertEqual((plan.seche.name, plan.reverb.name), ("seche_voix_autotune.wav", "reverb_voix_autotune.wav"))
        self.assertTrue(plan.exact)
        self.assertNotEqual(pistes.chemin_ma_voix(plan), pistes.chemin_ma_voix(pistes.plan_ma_voix(r.dossier_voix,
                                                                                                    r.rapport)))
        self.assertEqual(pistes.cle_ecoutee("doublage", True), "doublage_at")
        self.assertEqual(pistes.cle_ecoutee("beat", True), "beat")
        self.assertEqual(pistes.cle_ecoutee("doublage", False), "doublage")
        self.assertEqual(pistes.cle_de_base("ma_voix_at"), "ma_voix")
        self.assertEqual(pistes.nom_export(r, "ma_voix_at"), "Clip - voix Jules et Marie (auto-tune) (voix seule).mp3")

    def test_ligne_de_commande_auto_tune(self):
        vus = []
        with mock.patch.object(pistes, "preparer_ma_voix", lambda d, avancement=None, autotune=False:
                               vus.append((d, autotune)) or Path("x.flac")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pistes.main(["voix", str(self.tv), "--autotune"]), 0)
            self.assertEqual(pistes.main(["voix", str(self.tv)]), 0)
        self.assertEqual(vus, [(str(self.tv), True), (str(self.tv), False)])

    def test_corbeille(self):
        r = self.resultat()
        (self.dossier / "travail" / "marie").mkdir()      # une autre voix : seuls les fichiers de celle-ci partent
        exporte = self.dossier / pistes.nom_export(r, "ma_voix_at")
        exporte.write_bytes(b"")
        p = corbeille.plans([r])[0]
        noms = {Path(f).name for f in p.fichiers}
        for n in ("Clip - voix Jules et Marie (auto-tune).mp4", "Clip - voix Jules et Marie (auto-tune).mp3",
                  "Clip - voix Jules et Marie (auto-tune).lrc", exporte.name, "jules+marie"):
            self.assertIn(n, noms)

    def test_preparation_a_l_avance(self):
        r = self.resultat()
        r.video = None   # MP3 seul : les voix seules se combinent, sans copie vidéo
        t = preparation.taches(r)
        self.assertIn(["voix", str(r.dossier_voix), "--autotune"], [x.argv for x in t])
        self.assertIn(["voix", str(r.dossier_voix)], [x.argv for x in t])


class Courbes(EssaiIsole):
    def test_autotune_et_voix_par_trame(self):
        f = self.tmp / "courbes.json"
        f.write_text(json.dumps({"format": 1, "pas_s": 0.02, "source_hz": [0, 200, 210], "convertie_hz": [0, 200, 211],
                                 "decalage_dt": [0, 0, 0], "autotune_hz": [0, 196.0, 220.0], "voix_cibles": ["jules", "marie"],
                                 "voix_par_trame": [-1, 0, 2]}))
        c = analyse.lire_courbes(f)
        self.assertEqual(list(c["autotune"]), [0, 196.0, 220.0])
        self.assertEqual(list(c["voix"]), [-1, 0, 2])
        self.assertEqual(c["voix_cibles"], ["jules", "marie"])
        f.write_text(json.dumps({"format": 1, "pas_s": 0.02, "source_hz": [0, 200], "convertie_hz": [0, 200],
                                 "decalage_dt": [0, 0]}))
        c = analyse.lire_courbes(f)
        self.assertIsNone(c["autotune"])
        self.assertIsNone(c["voix"])


class DoublageEntier(EssaiIsole):
    """Le faux doubler, appelé comme l'interface l'appelle : deux voix, auto-tune, puis la liste des résultats."""

    def test_deux_voix_et_auto_tune(self):
        creer_voix(self.donnees, "jules", "Jules")
        creer_voix(self.donnees, "marie", "Marie", hauteur=210.0)
        v = self.video("Chanson.mp4")
        reglages = {"voix": moteur.spec_voix("genre", ["jules", "marie"]), "pas": 30, "autotune": 60}
        ev = self.tmp / "ev.jsonl"
        code, sortie = moteur.lancer(moteur.args_doublage([{"chemin": str(v), "extrait": (10, 20)}], reglages,
                                                          self.donnees, evenements=str(ev)),
                                     moteur.environnement(self.donnees), timeout=60)
        self.assertEqual(code, 0, sortie)
        res = rapports.lister_resultats(chemins.dossier_sorties(self.donnees), {"jules": "Jules", "marie": "Marie"})
        self.assertEqual(len(res), 1)
        r = res[0]
        self.assertEqual((r.voix, r.voix_nom), ("jules+marie", "Jules et Marie"))
        self.assertTrue(r.autotune)
        self.assertEqual(r.video_autotune.name, "Chanson - voix Jules et Marie (auto-tune).mp4")
        self.assertEqual(len(rapports.passages_voix(r.rapport)), 3)
        sortie_ev = [json.loads(l) for l in ev.read_text().splitlines() if '"sortie"' in l][-1]
        self.assertEqual(sortie_ev["voix"], ["jules", "marie"])
        self.assertEqual(sortie_ev["autotune"]["intensite"], 60)
        self.assertEqual(moteur.id_voix(reglages["voix"]), r.voix, "« Voir le résultat » retrouve le dossier")
        # remix par la spécification lue dans le rapport, puis retrait de la version auto-tune
        ok, s = moteur.remixer(v, moteur.spec_de_rapport(r.voix, r.rapport), r.dossier, self.donnees)
        self.assertTrue(ok, s)
        code, s = moteur.lancer([*moteur.args_remix(v, moteur.spec_de_rapport(r.voix, r.rapport), r.dossier,
                                                    self.donnees), "--sans-autotune"],
                                moteur.environnement(self.donnees), timeout=60)
        self.assertEqual(code, 0, s)
        r2 = rapports.lire_dossier(r.dossier, {"jules": "Jules", "marie": "Marie"})[0]
        self.assertIsNone(r2.autotune)
        self.assertIsNone(r2.video_autotune)

    def test_greffon_absent(self):
        creer_voix(self.donnees, "jules", "Jules")
        os.environ["FAUX_DOUBLER_X42"] = "absent"
        v = self.video()
        code, sortie = moteur.lancer(moteur.args_doublage([v], {"voix": "jules", "autotune": 70}, self.donnees),
                                     moteur.environnement(self.donnees), timeout=60)
        self.assertEqual(code, 0, sortie)
        self.assertIn("installez x42-plugins", sortie)
        r = rapports.lister_resultats(chemins.dossier_sorties(self.donnees))[0]
        self.assertIsNone(r.autotune)
        # doubler --config --json le dit (config_effective lit le fichier d'essai quand VOIXLA_CONFIG est posée)
        code, s = moteur.lancer([*moteur.commande(), "--config", "--json"], moteur.environnement(self.donnees))
        self.assertEqual(moteur.x42_autotune(json.loads(s[s.find("{"):])), (True, None))
