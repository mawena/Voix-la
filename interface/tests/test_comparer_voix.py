"""Comparer plusieurs voix en une session (doubler --comparer), côté interface : options passées à doubler, estimation de
durée (la conversion se multiplie, le reste non), le faux doubler de bout en bout (une sortie complète par voix,
comparaison.json, remix de chacune), le banc d'écoute à l'aveugle préparé dans le cache (lettres tirées au hasard et
gardées, liens vers les mp3, correspondance révélée par le vote), puis la page sous Xvfb (tests/essai_comparer_gui.py).
Voix inventées (voixa, voixb, voixc)."""

import contextlib
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

from voixla import chemins, comparaison, ecoute, moteur, rapports

from .commun import EssaiIsole, creer_voix, oublier_capacites

INTERFACE = Path(__file__).resolve().parents[1]
NOMS = {"voixa": "Voix A", "voixb": "Voix B", "voixc": "Voix C"}
VOIX = ("voixa", "voixb", "voixc")


def creer_voix_essai(donnees):
    for ident, nom, hauteur in (("voixa", "Voix A", 110.0), ("voixb", "Voix B", 210.0), ("voixc", "Voix C", 160.0)):
        creer_voix(donnees, ident, nom, hauteur=hauteur)


def son(chemin, hauteur_hz, duree_s=30):
    """Un vrai son de synthèse (ffmpeg) : une note, dans le format du chemin."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={hauteur_hz}:duration={duree_s}",
                    str(chemin)], check=True, capture_output=True)


def dossier_compare(donnees, voix=VOIX, titre="Chanson (extrait 0m10,0+0m30,0)", marque=True, audio=False):
    """Un dossier de sortie tel que doubler --comparer le laisse (fichiers inventés) : un mp3 par voix, travail/<voix>/.
    audio : de vrais sons de synthèse (une note par voix) au lieu de fichiers à contenu inventé."""
    d = chemins.dossier_sorties(donnees) / titre
    (d / "travail").mkdir(parents=True)
    (d / "travail" / "source.json").write_text(json.dumps({"video": str(donnees / "v.mp4")}))
    (d / "travail" / "original.wav").write_bytes(b"")
    for v in voix:
        (d / "travail" / v).mkdir()
        (d / "travail" / v / "rapport.json").write_text(json.dumps({"mode": "chant", "duree_s": 30.0, "frise": [],
                                                                    "voix_cible": {"nom": v}}))
        (d / "travail" / v / "voix_convertie.flac").write_bytes(b"")
        if audio:
            son(d / f"Chanson - voix {NOMS[v]}.mp3", 330 + 110 * VOIX.index(v))
            son(d / "travail" / v / "voix_convertie.flac", 330 + 110 * VOIX.index(v), 10)
        else:
            (d / f"Chanson - voix {NOMS[v]}.mp3").write_bytes(b"mp3 " + v.encode())
        (d / f"Chanson - voix {NOMS[v]}.mp4").write_bytes(b"")
    if marque:
        (d / "comparaison.json").write_text(json.dumps({"format": 1, "cree_le": "2026-11-09T10:00:00+01:00", "voix": [
            {"id": v, "nom": NOMS[v], "fichier": f"Chanson - voix {NOMS[v]}"} for v in voix]}))
    return d


class Options(EssaiIsole):
    def test_doubler_annonce_comparer(self):
        self.assertIn("--comparer", moteur.capacites())
        self.assertNotIn("--comparer", moteur.analyser_aide("  --comparer-pas N   comparaison de pas"),
                         "--comparer-pas n'est pas --comparer")
        self.assertIn("--comparer", moteur.analyser_aide("  --comparer A,B[,C…]   comparer des voix"))

    def test_arguments(self):
        v = self.video()
        caps = moteur.capacites()
        a = moteur.args_doublage([v], {"voix": "voixb", "comparer": ["voixb", "voixa", "voixc"], "pas": 30}, self.donnees,
                                 caps=caps)
        self.assertIn("--comparer=voixb,voixa,voixc", a)
        self.assertFalse(any(x.startswith("--voix=") for x in a), "--comparer remplace --voix")
        a = moteur.args_doublage([v], {"voix": "voixb", "comparer": ["voixb"]}, self.donnees, caps=caps)
        self.assertIn("--voix=voixb", a, "une seule voix : un doublage ordinaire")
        self.assertFalse(any(x.startswith("--comparer=") for x in a))
        a = moteur.args_doublage([v], {"voix": "voixb", "comparer": ["voixb", "voixa"]}, self.donnees,
                                 caps=caps - {"--comparer"})
        self.assertIn("--voix=voixb", a, "doubler d'avant --comparer : la première voix seule")

    def test_estimation(self):
        un, deux, trois = (moteur.estimation_noyau_s([120.0], 30, [self.tmp / "rien"], voix_comparees=n)
                           for n in (1, 2, 3))
        self.assertTrue(un < deux < trois < 3 * un, (un, deux, trois))
        d = rapports.estimer([120.0], 30, None, nettoyage=True, sorties=self.tmp / "rien")
        c = rapports.estimer([120.0], 30, None, nettoyage=True, sorties=self.tmp / "rien", voix_comparees=3)
        self.assertGreater(c["kaggle"], d["kaggle"])
        for etape in ("mix", "video"):
            self.assertAlmostEqual(c[etape], 3 * d[etape], delta=0.3, msg="refaits pour chaque voix")
        for etape in ("extraction", "envoi", "rapatriement"):
            self.assertEqual(c[etape], d[etape], "une seule fois")

    def test_estimation_modele_de_repli(self):
        # sans doubler.py pour estimer : le modèle de l'interface ajoute aussi la conversion de chaque voix
        un = rapports.estimer([120.0], 30, None, nettoyage=True)["kaggle"]
        trois = rapports.estimer([120.0], 30, None, nettoyage=True, voix_comparees=3)["kaggle"]
        self.assertTrue(un < trois < 3 * un, (un, trois))

    def test_doubler_ancien_sans_estimation_comparee(self):
        class Ancien:
            @staticmethod
            def estimer_noyau_s(durees_s, pas, sorties, nettoyage=True):
                return 100.0
        with mock.patch.object(moteur, "module_doubler", lambda: Ancien):
            self.assertIsNone(moteur.estimation_noyau_s([60.0], 30, [self.tmp], voix_comparees=2),
                              "doubler d'avant --comparer : le repli de l'interface")


class FauxDoubler(EssaiIsole):
    """Le faux doubler, appelé comme l'interface l'appelle."""

    def setUp(self):
        super().setUp()
        creer_voix_essai(self.donnees)
        self.video_ = self.video("Chanson.mp4")
        self.sorties = chemins.dossier_sorties(self.donnees)

    def doubler(self, voix=("voixb", "voixa", "voixc"), **reglages):
        argv = moteur.args_doublage([self.video_], {"voix": voix[0], "comparer": list(voix), **reglages}, self.donnees,
                                    extrait=(10, 30))
        return moteur.lancer(argv, moteur.environnement(self.donnees), timeout=60)

    def test_une_sortie_par_voix(self):
        code, sortie = self.doubler()
        self.assertEqual(code, 0, sortie)
        dossier = self.sorties / "Chanson (extrait 0m10,0+0m30,0)"
        resultats = rapports.lire_dossier(dossier, NOMS)
        self.assertEqual(sorted(r.voix for r in resultats), ["voixa", "voixb", "voixc"])
        for r in resultats:
            self.assertTrue(r.complet and r.mp3 and r.video, r.voix)
            self.assertEqual(r.mp3.name, f"Chanson - voix {NOMS[r.voix]}.mp3")
        self.assertEqual(comparaison.ordre_demande(dossier), ["voixb", "voixa", "voixc"], "l'ordre demandé")
        self.assertEqual([r.voix for r in comparaison.comparables(resultats)], ["voixb", "voixa", "voixc"])
        self.assertTrue(comparaison.peut_comparer(resultats))

    def test_evenements(self):
        ev = self.tmp / "ev.jsonl"
        argv = [*moteur.args_doublage([self.video_], {"voix": "voixa", "comparer": ["voixa", "voixb"]}, self.donnees),
                f"--evenements={ev}"]
        code, sortie = moteur.lancer(argv, moteur.environnement(self.donnees), timeout=60)
        self.assertEqual(code, 0, sortie)
        sorties = [json.loads(l) for l in ev.read_text().splitlines() if '"sortie"' in l]
        self.assertEqual([e["voix"] for e in sorties], [["voixa"], ["voixb"]], "un événement par voix")

    def test_remix_de_chaque_sortie(self):
        self.assertEqual(self.doubler()[0], 0)
        dossier = self.sorties / "Chanson (extrait 0m10,0+0m30,0)"
        for ident in VOIX:
            ok, s = moteur.remixer(self.video_, ident, dossier, self.donnees, voix_db=2.0)
            self.assertTrue(ok, s)
            r = next(x for x in rapports.lire_dossier(dossier, NOMS) if x.voix == ident)
            self.assertEqual(r.rapport["mix"]["voix_db"], 2.0)
        autre = next(x for x in rapports.lire_dossier(dossier, NOMS) if x.voix == "voixa")
        self.assertEqual(autre.rapport["mix"]["voix_db"], 2.0)

    def test_refus_comme_doubler(self):
        for voix, motif in (("voixa", "au moins deux voix"), ("homme:voixa,femme:voixb", "ne se compare pas")):
            argv = [*moteur.commande(), str(self.video_), f"--comparer={voix}", "--donnees", str(self.donnees)]
            code, sortie = moteur.lancer(argv, moteur.environnement(self.donnees), timeout=60)
            self.assertNotEqual(code, 0)
            self.assertIn(motif, sortie)


class Banc(EssaiIsole):
    """Le banc d'écoute à l'aveugle d'une comparaison."""

    def setUp(self):
        super().setUp()
        self.dossier = dossier_compare(self.donnees)
        self.racine = self.tmp / "cache" / "banc"

    def resultats(self):
        return rapports.lire_dossier(self.dossier, NOMS)

    def test_banc_au_format_de_l_ecoute(self):
        racine = comparaison.preparer(self.resultats(), self.racine, graine=7)
        self.assertEqual(racine, self.racine)
        extraits, corr = ecoute.charger(racine)
        self.assertEqual([e.nom for e in extraits], ["comparaison"])
        ext = extraits[0]
        self.assertEqual([v.lettre for v in ext.versions], ["A", "B", "C"])
        self.assertEqual(ext.titre, "Chanson", "le titre du dossier, sans l'extrait")
        lettres = ecoute.correspondance_de(corr, "comparaison")
        self.assertEqual(sorted(lettres.values()), ["Voix A", "Voix B", "Voix C"], "noms des voix, cachés jusqu'au vote")
        ids = corr["ids"]["comparaison"]
        for v in ext.versions:
            self.assertTrue(v.mix.is_symlink(), "un lien vers le mp3 du doublage, pas une copie")
            self.assertEqual(v.mix.read_bytes(), b"mp3 " + ids[v.lettre].encode(), "A est bien la voix de la correspondance")
        self.assertEqual(sorted(corr["voix"]), list(VOIX))

    def test_tirage_au_hasard_et_gardé(self):
        premier = comparaison.preparer(self.resultats(), self.racine, graine=1)
        c1 = json.loads((premier / "correspondance.json").read_text())["ids"]["comparaison"]
        comparaison.preparer(self.resultats(), self.racine, graine=2)   # même voix : la correspondance est gardée
        c2 = json.loads((premier / "correspondance.json").read_text())["ids"]["comparaison"]
        self.assertEqual(c1, c2, "un vote reste valable tant que les voix sont les mêmes")
        ordres = set()
        for g in range(1, 40):
            ordres.add(tuple(comparaison._tirer(VOIX, g).values()))
        self.assertGreater(len(ordres), 3, "les lettres sont tirées au hasard")

    def test_nouvelles_voix_nouveau_tirage(self):
        comparaison.preparer(self.resultats(), self.racine, graine=3)
        shutil_rm = [self.dossier / "travail" / "voixc", self.dossier / "Chanson - voix Voix C.mp3",
                     self.dossier / "Chanson - voix Voix C.mp4"]
        for f in shutil_rm:
            if f.is_dir():
                for x in f.iterdir():
                    x.unlink()
                f.rmdir()
            else:
                f.unlink()
        comparaison.preparer(self.resultats(), self.racine, graine=4)
        extraits, corr = ecoute.charger(self.racine)
        self.assertEqual([v.lettre for v in extraits[0].versions], ["A", "B"], "l'ancienne lettre C n'est pas gardée")
        self.assertEqual(sorted(corr["voix"]), ["voixa", "voixb"])

    def test_le_vote_revele_les_voix(self):
        comparaison.preparer(self.resultats(), self.racine, graine=5)
        extraits, corr = ecoute.charger(self.racine)
        lettres = ecoute.correspondance_de(corr, "comparaison")
        self.assertIsNone(ecoute.dernier_vote(ecoute.lire_votes(self.racine), "comparaison", lettres), "caché")
        vote = ecoute.enregistrer_vote("comparaison", {"A": 5, "B": 2, "C": 3}, "A, nettement", lettres, self.racine)
        self.assertEqual(vote["classement"], ["A", "C", "B"])
        revele = ecoute.dernier_vote(ecoute.lire_votes(self.racine), "comparaison", lettres)
        self.assertEqual(revele["correspondance"], lettres)
        # le banc d'essai n'est pas touché
        self.assertFalse((chemins.dossier_ecoute() / "votes.json").exists())

    def test_moins_de_deux_voix(self):
        solo = dossier_compare(self.donnees, ("voixa",), titre="Seule", marque=False)
        r = rapports.lire_dossier(solo, NOMS)
        self.assertFalse(comparaison.peut_comparer(r))
        with self.assertRaises(ValueError):
            comparaison.preparer(r, self.racine)

    def test_doublage_ordinaire_a_plusieurs_voix(self):
        # sans comparaison.json (voix doublées l'une après l'autre) : comparables aussi, par nom de voix
        d = dossier_compare(self.donnees, ("voixb", "voixa"), titre="Autre", marque=False)
        r = rapports.lire_dossier(d, NOMS)
        self.assertIsNone(comparaison.marque(d))
        self.assertEqual([x.voix for x in comparaison.comparables(r)], ["voixa", "voixb"])

    def test_doublage_inacheve_ou_duo_ignore(self):
        (self.dossier / "Chanson - voix Voix C.mp3").unlink()
        r = rapports.lire_dossier(self.dossier, NOMS)
        self.assertEqual([x.voix for x in comparaison.comparables(r)], ["voixa", "voixb"])
        r2 = rapports.lire_dossier(self.dossier, NOMS)
        r2[0].voix = "voixa+voixb"   # une voix par chanteur : pas comparable
        self.assertNotIn("voixa+voixb", [x.voix for x in comparaison.comparables(r2)])

    def test_racine_par_dossier(self):
        cache = self.tmp / "cache"
        a, b = comparaison.racine_de(self.dossier, cache), comparaison.racine_de(self.dossier.parent / "Autre", cache)
        self.assertNotEqual(a, b)
        self.assertEqual(a, comparaison.racine_de(self.dossier, cache))
        self.assertEqual(a.parent, cache)


class Page(EssaiIsole):
    """La page Écoute sur une comparaison, dans la vraie fenêtre, sous Xvfb, sur un bus privé, son coupé
    (tests/essai_comparer_gui.py) : groupe « Comparer les voix », versions anonymes, passage de l'une à l'autre à la même
    position, vote qui dévoile les voix, retour au banc d'essai."""

    def test_page(self):
        if not (shutil.which("ffmpeg") and shutil.which("xvfb-run") and shutil.which("dbus-run-session")):
            self.skipTest("ffmpeg, Xvfb ou dbus-run-session absent")
        os.environ["VOIXLA_CACHE"] = str(self.tmp / "cache")
        creer_voix_essai(self.donnees)
        dossier_compare(self.donnees, audio=True)
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
               "tests.essai_comparer_gui"]
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
