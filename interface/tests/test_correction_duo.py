"""Corriger à la main qui chante chaque passage d'un duo (une voix par chanteur), côté interface : lecture des deux
versions et de l'affectation gardée (rapports), affectation voulue, raisons d'un passage non corrigeable, options du
remix (moteur : --affectation FICHIER|auto, fichier temporaire effacé), le faux doubler de bout en bout (doublage,
correction, remix sans option qui la garde, retour à l'automatique, ancien doublage, doubler trop ancien), puis la page
sous Xvfb (tests/essai_correction_gui.py). Duo inventé : tests/faux_duo.py (jules, marie)."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voixla import chemins, moteur, rapports

from . import faux_duo
from .commun import EssaiIsole, creer_voix, oublier_capacites

INTERFACE = Path(__file__).resolve().parents[1]
NOMS = {"jules": "Jules", "marie": "Marie"}


def resultat(donnees):
    return rapports.lire_dossier(chemins.dossier_sorties(donnees) / faux_duo.TITRE, NOMS)[0]


class Lecture(EssaiIsole):
    """Ce que l'interface lit d'un duo qui a les deux versions de chaque passage."""

    def setUp(self):
        super().setUp()
        self.dossier = faux_duo.creer(self.donnees, videos=False)
        self.r = resultat(self.donnees)

    def test_versions_et_passages(self):
        r = self.r
        self.assertEqual(r.voix, "jules+marie")
        self.assertEqual(r.versions_duo["mode"], "toutes")
        self.assertIsNone(r.affectation_gardee)
        ms = rapports.morceaux_duo(r.rapport, r.versions_duo)
        self.assertEqual(len(ms), 14, "les passages chantés seulement (trois muets)")
        self.assertTrue(all(m["corrigeable"] and not m["corrige"] for m in ms))
        m7 = next(m for m in ms if m["i"] == 7)
        self.assertEqual((m7["debut"], m7["fin"], m7["voix"], m7["voix_auto"]), (22.7, 26.5, ["jules"], ["jules"]))
        self.assertEqual(next(m for m in ms if m["i"] == 13)["voix"], ["jules", "marie"], "deux pistes, deux voix")
        self.assertEqual(rapports.morceau_duo_a(ms, 24.0)["i"], 7)
        self.assertIsNone(rapports.morceau_duo_a(ms, 14.5), "muet : pas de passage")
        self.assertEqual(rapports.voix_cibles_noms(r.rapport), ["jules", "marie"])
        self.assertEqual(rapports.etat_correction(r, moteur.capacites()), (True, ""))

    def test_utiles_seulement(self):
        rap = json.loads((self.r.dossier_voix / "rapport.json").read_text())
        rap["versions"].update({"mode": "utiles", "morceaux": [6, 7, 8]})
        ms = rapports.morceaux_duo(rap, rap["versions"])
        self.assertEqual([m["i"] for m in ms if m["corrigeable"]], [6, 7, 8])

    def test_sans_versions(self):
        shutil.rmtree(self.r.dossier_voix / "versions")
        r = resultat(self.donnees)
        self.assertIsNone(r.versions_duo, "rapport.versions sans versions/versions.json : pas corrigeable")
        self.assertEqual(rapports.etat_correction(r, moteur.capacites()), (False, rapports.RAISON_SANS_VERSIONS))
        self.assertTrue(all(not m["corrigeable"] for m in rapports.morceaux_duo(r.rapport, r.versions_duo)))
        rap = json.loads((r.dossier_voix / "rapport.json").read_text())
        rap["versions"] = {"mode": "non"}
        (r.dossier_voix / "rapport.json").write_text(json.dumps(rap))
        self.assertEqual(rapports.etat_correction(resultat(self.donnees), moteur.capacites())[1],
                         rapports.RAISON_VERSIONS_NON)
        rap.pop("versions")
        (r.dossier_voix / "rapport.json").write_text(json.dumps(rap))
        self.assertEqual(rapports.etat_correction(resultat(self.donnees), moteur.capacites())[1],
                         rapports.RAISON_SANS_VERSIONS, "ancien doublage")

    def test_doubler_trop_ancien(self):
        caps = moteur.capacites() - {"--affectation", "--passage"}
        self.assertEqual(rapports.etat_correction(self.r, caps), (False, rapports.RAISON_DOUBLER_ANCIEN))
        self.assertFalse(moteur.correction_possible(caps))

    def test_source_absente_et_une_voix(self):
        (self.donnees / "Vidéos" / f"{faux_duo.TITRE}.mp4").unlink()
        possible, raison = rapports.etat_correction(resultat(self.donnees), moteur.capacites())
        self.assertFalse(possible)
        self.assertIn("vidéo source introuvable", raison)
        r = self.r
        r.voix = "jules"
        self.assertEqual(rapports.etat_correction(r, moteur.capacites())[0], False)

    def test_affectation_voulue(self):
        noms = ["jules", "marie"]
        self.assertIsNone(rapports.affectation_voulue(None, {}, noms))
        a = rapports.affectation_voulue(None, {8: "marie", 7: "marie", 13: "deux"}, noms)
        self.assertEqual(a, {"format": 1, "voix_cibles": noms, "passages": {"7": "marie", "8": "marie", "13": "deux"}})
        garde = {"format": 1, "passages": {"7": "marie", "8": "marie"}, "pistes": {"14": {"choeurs_sec": "marie"}}}
        a = rapports.affectation_voulue(garde, {8: "auto", 14: "jules"}, noms)
        self.assertEqual(a["passages"], {"7": "marie", "14": "jules"}, "auto retire, un choix de passage remplace "
                                                                        "les pistes")
        self.assertNotIn("pistes", a)
        self.assertIsNone(rapports.affectation_voulue({"passages": {"7": "marie"}}, {7: "auto"}, noms),
                          "plus de correction : l'automatique")
        self.assertEqual(rapports.choix_garde(garde, 7), "marie")
        self.assertEqual(rapports.choix_garde(garde, 14), "pistes")
        self.assertEqual(rapports.choix_garde(garde, 3), "auto")
        self.assertEqual(rapports.choix_garde(None, 3), "auto")

    def test_rapport_corrige(self):
        rap = json.loads((self.r.dossier_voix / "rapport.json").read_text())
        m = next(x for x in rap["morceaux"] if x["i"] == 13)
        m.update({"voix_pistes": {"lead_sec": ["jules", "marie"]}, "voix_pistes_auto": {"lead_sec": "jules"},
                  "corrige": True})
        rap["affectation"]["manuelle"] = {"fichier": "affectation.json", "passages": {"7": "marie", "13": "deux"},
                                          "corriges": [7, 13]}
        self.assertEqual(rapports.passages_corriges(rap), {7, 13})
        segs = rapports.segments_frise(rap)
        s13 = next(s for s in segs if s["debut"] == 43.0)
        self.assertEqual(s13["voix_pistes"], {"lead_sec": ["jules", "marie"]})
        self.assertTrue(s13["corrige"])
        ms = {x["i"]: x for x in rapports.morceaux_duo(rap, rap["versions"])}
        self.assertEqual((ms[13]["voix"], ms[13]["voix_auto"], ms[13]["corrige"]), (["jules", "marie"], ["jules"], True))
        self.assertTrue(ms[7]["corrige"], "d'après affectation.manuelle")
        self.r.rapport = rap
        self.assertTrue(any("corrigé à la main : 2 passages" in l for l in self.r.resume_voix_cibles()))


class Arguments(EssaiIsole):
    def test_options_du_remix(self):
        caps = moteur.capacites()
        self.assertTrue({"--affectation", "--passage"} <= caps)
        v, d = self.video(), chemins.dossier_sorties(self.donnees) / "Clip"
        a = moteur.args_remix(v, "homme:jules,femme:marie", d, self.donnees, caps=caps, affectation="auto")
        self.assertIn("--affectation=auto", a)
        a = moteur.args_remix(v, "jules", d, self.donnees, caps=caps)
        self.assertFalse(any(x.startswith("--affectation") for x in a))
        a = moteur.args_remix(v, "homme:jules,femme:marie", d, self.donnees, caps=frozenset({"--remix"}),
                              affectation="auto")
        self.assertFalse(any(x.startswith("--affectation") for x in a), "doubler trop ancien : rien")

    def test_fichier_temporaire(self):
        vus = []

        def lancer(argv, env=None, timeout=60):
            f = next(x.split("=", 1)[1] for x in argv if x.startswith("--affectation="))
            vus.append((f, json.loads(Path(f).read_text())))
            return 0, "fini"
        voulue = {"format": 1, "voix_cibles": ["jules", "marie"], "passages": {"7": "marie"}}
        d = chemins.dossier_sorties(self.donnees) / "Clip"
        with mock.patch.object(moteur, "lancer", lancer):
            ok, _ = moteur.remixer(self.video(), "homme:jules,femme:marie", d, self.donnees, affectation=voulue)
        self.assertTrue(ok)
        f, contenu = vus[0]
        self.assertEqual(contenu, voulue)
        self.assertFalse(Path(f).exists(), "fichier temporaire effacé après le remix")
        self.assertFalse(str(f).startswith(str(d)), "hors du dossier du doublage")


class FauxDoubler(EssaiIsole):
    """Le faux doubler, appelé comme l'interface l'appelle."""

    def test_doublage_correction_et_retour(self):
        creer_voix(self.donnees, "jules", "Jules")
        creer_voix(self.donnees, "marie", "Marie", hauteur=210.0)
        v = self.video("Chanson.mp4")
        spec = moteur.spec_voix("genre", ["jules", "marie"])
        code, sortie = moteur.lancer(moteur.args_doublage([v], {"voix": spec}, self.donnees),
                                     moteur.environnement(self.donnees), timeout=60)
        self.assertEqual(code, 0, sortie)
        r = rapports.lister_resultats(chemins.dossier_sorties(self.donnees), NOMS)[0]
        self.assertEqual(rapports.etat_correction(r, moteur.capacites()), (True, ""))
        self.assertEqual([m["i"] for m in rapports.morceaux_duo(r.rapport, r.versions_duo)], [1, 2])
        voulue = rapports.affectation_voulue(r.affectation_gardee, {1: "marie"}, ["jules", "marie"])
        ok, s = moteur.remixer(v, moteur.spec_de_rapport(r.voix, r.rapport), r.dossier, self.donnees,
                               affectation=voulue)
        self.assertTrue(ok, s)
        r = rapports.lire_dossier(r.dossier, NOMS)[0]
        self.assertEqual(r.affectation_gardee["passages"], {"1": "marie"})
        self.assertEqual(rapports.passages_corriges(r.rapport), {1})
        self.assertEqual(rapports.passages_voix(r.rapport)[0]["voix"], ["marie"])
        self.assertTrue((r.dossier_voix / "voix_corrigee.flac").exists())
        # un remix sans option (remix rapide) garde la correction
        ok, s = moteur.remixer(v, moteur.spec_de_rapport(r.voix, r.rapport), r.dossier, self.donnees, voix_db=2.0)
        self.assertTrue(ok, s)
        r = rapports.lire_dossier(r.dossier, NOMS)[0]
        self.assertEqual(rapports.passages_corriges(r.rapport), {1})
        # retour à l'automatique
        ok, s = moteur.remixer(v, moteur.spec_de_rapport(r.voix, r.rapport), r.dossier, self.donnees, affectation="auto")
        self.assertTrue(ok, s)
        r = rapports.lire_dossier(r.dossier, NOMS)[0]
        self.assertIsNone(r.affectation_gardee)
        self.assertEqual(rapports.passages_corriges(r.rapport), set())
        self.assertNotIn("manuelle", r.rapport["affectation"])
        self.assertEqual(rapports.passages_voix(r.rapport)[0]["voix"], ["jules"], "l'automatique retrouvé")
        self.assertFalse((r.dossier_voix / "voix_corrigee.flac").exists())

    def test_passage_en_ligne_de_commande(self):
        faux_duo.creer(self.donnees, videos=False)
        r = resultat(self.donnees)
        argv = moteur.args_remix(r.video_source, "homme:jules,femme:marie", r.dossier, self.donnees)
        code, s = moteur.lancer([*argv, "--passage", "7-8=femme", "--passage", "13/choeurs=jules"],
                                moteur.environnement(self.donnees), timeout=60)
        self.assertEqual(code, 0, s)
        r = resultat(self.donnees)
        self.assertEqual(r.affectation_gardee["passages"], {"7": "marie", "8": "marie"})
        self.assertEqual(r.affectation_gardee["pistes"], {"13": {"choeurs_sec": "jules"}})
        self.assertEqual(rapports.passages_corriges(r.rapport), {7, 8, 13})
        self.assertEqual(rapports.choix_garde(r.affectation_gardee, 13), "pistes")
        ms = {m["i"]: m for m in rapports.morceaux_duo(r.rapport, r.versions_duo)}
        self.assertEqual(ms[7]["voix"], ["marie"])
        self.assertEqual(ms[13]["voix"], ["jules"], "chœurs repris dans la voix de Jules")
        courbes = json.loads((r.dossier_voix / "courbes.json").read_text())
        self.assertEqual(courbes["voix_par_trame"][int(24 / 0.02)], 1, "voix_par_trame suit")

    def test_ancien_doublage(self):
        faux_duo.creer(self.donnees, videos=False, versions=False)
        r = resultat(self.donnees)
        self.assertEqual(rapports.etat_correction(r, moteur.capacites())[1], rapports.RAISON_SANS_VERSIONS)
        ok, s = moteur.remixer(r.video_source, "homme:jules,femme:marie", r.dossier, self.donnees,
                               affectation={"format": 1, "passages": {"7": "marie"}})
        self.assertFalse(ok)
        self.assertIn("deux versions", s)
        ok, s = moteur.remixer(r.video_source, "homme:jules,femme:marie", r.dossier, self.donnees, affectation="auto")
        self.assertTrue(ok, "revenir à l'automatique ne demande rien")

    def test_doubler_trop_ancien(self):
        os.environ["FAUX_DOUBLER_SANS_AFFECTATION"] = "1"
        oublier_capacites()
        self.assertNotIn("--affectation", moteur.capacites())
        faux_duo.creer(self.donnees, videos=False)
        self.assertEqual(rapports.etat_correction(resultat(self.donnees), moteur.capacites())[1],
                         rapports.RAISON_DOUBLER_ANCIEN)

    def test_sans_versions_au_doublage(self):
        creer_voix(self.donnees, "jules", "Jules")
        creer_voix(self.donnees, "marie", "Marie", hauteur=210.0)
        os.environ["FAUX_DOUBLER_SANS_VERSIONS"] = "1"
        v = self.video("Chanson.mp4")
        code, s = moteur.lancer(moteur.args_doublage([v], {"voix": "homme:jules,femme:marie"}, self.donnees),
                                moteur.environnement(self.donnees), timeout=60)
        self.assertEqual(code, 0, s)
        r = rapports.lister_resultats(chemins.dossier_sorties(self.donnees), NOMS)[0]
        self.assertIsNone(r.versions_duo)
        self.assertFalse((r.dossier_voix / "versions").exists())


GUI = None
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    GUI = True
except (ImportError, ValueError):
    GUI = False
FFMPEG = bool(shutil.which("ffmpeg"))


@unittest.skipUnless(FFMPEG and GUI and shutil.which("xvfb-run") and shutil.which("dbus-run-session")
                     and shutil.which("flock"), "ffmpeg, GTK, xvfb-run, dbus-run-session ou flock absent")
class Page(EssaiIsole):
    """La frise corrigeable dans la vraie fenêtre, sous Xvfb, sur un bus privé, son coupé
    (tests/essai_correction_gui.py) : menu d'un passage, choix en attente, « Appliquer » (remix du faux doubler),
    passages corrigés marqués, « Revenir à l'automatique »."""

    def test_page(self):
        import contextlib
        os.environ["VOIXLA_CACHE"] = str(self.tmp / "cache")
        faux_duo.creer(self.donnees, videos=True)
        env = dict(os.environ)
        env.update({"GTK_A11Y": "none", "NO_AT_BRIDGE": "1", "GIO_USE_VFS": "local", "GSK_RENDERER": "cairo",
                    "ADW_DISABLE_PORTAL": "1", "VOIXLA_APP_ID": f"io.mawena.VoixLa.Essai{os.getpid()}",
                    "GST_PLUGIN_FEATURE_RANK": "fakeaudiosink:MAX", "VOIXLA_PAGE": "resultats",
                    "XDG_STATE_HOME": str(self.tmp / "xdg-etat"),
                    "PYTHONPATH": os.pathsep.join([str(INTERFACE)] + ([os.environ["PYTHONPATH"]]
                                                                        if os.environ.get("PYTHONPATH") else []))})
        verrou = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "voix-la-xvfb.verrou"
        cmd = ["flock", str(verrou), "dbus-run-session", f"--config-file={INTERFACE / 'outils' / 'bus-essai.conf'}",
               "--", "xvfb-run", "-a", "-s", "-screen 0 1400x1000x24", "nice", "-n", "10", sys.executable, "-m",
               "tests.essai_correction_gui"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                             cwd=str(INTERFACE), stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            sortie, erreur = p.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            sortie, erreur = p.communicate()
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGKILL)
        mesures = {}
        for l in sortie.splitlines():
            with contextlib.suppress(ValueError):
                mesures.update(json.loads(l))
        trace = sortie[-3000:] + erreur[-3000:]
        self.assertEqual(p.returncode, 0, trace)
        self.assertEqual(mesures.get("erreurs"), [], trace)
        self.assertTrue(mesures.get("fini"), trace)


if __name__ == "__main__":
    unittest.main()
