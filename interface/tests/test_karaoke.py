"""Le karaoké (voixla.karaoke, gui/page_karaoke.py) : passage, prises (cachées, gardées, jetées), beat du passage, mix
calé, MP3, calibration de la latence par les bips, analyse complète en ligne de commande, sobriété (nice, file-attente
au-delà d'une minute de son), et la page dans la vraie fenêtre sous Xvfb (tests/essai_karaoke_gui.py : une vraie prise
au micro de synthèse, aucun son joué). Données inventées (tests/faux_karaoke.py)."""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

import numpy as np

from voixla import chemins, karaoke as K, paroles as P, preparation as prep, rapports
from voixla.justesse import Passage

from . import faux_karaoke as fk
from .commun import EssaiIsole

FFMPEG = shutil.which("ffmpeg")
INTERFACE = Path(__file__).resolve().parents[1]


class EssaiAvecCache(EssaiIsole):
    def setUp(self):
        super().setUp()
        os.environ["VOIXLA_CACHE"] = str(self.tmp / "cache")


def lire_wav(f):
    with wave.open(str(f), "rb") as w:
        n, sr, ch = w.getnframes(), w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(n), "<i2").astype(np.float32) / 32768
    return x.reshape(-1, ch), sr


def debut_du_son(x, sr, seuil=0.02):
    """Instant (s) du premier échantillon au-dessus du seuil."""
    i = np.flatnonzero(np.abs(x) > seuil)
    return i[0] / sr if len(i) else None


class TestPassage(unittest.TestCase):

    def test_passage_par_lignes(self):
        lignes = fk.lignes()
        p = K.passage_lignes(lignes, 1, 3, 31.8)
        self.assertAlmostEqual(p.debut, lignes[1][0] - 2.0)
        self.assertAlmostEqual(p.fin, lignes[3][1] + 1.0)
        self.assertEqual(p.repetitions, 1)
        self.assertEqual(K.passage_lignes(lignes, 3, 1, 31.8), p)              # ordre inversé
        self.assertEqual(K.passage_lignes(lignes, 0, 0, 31.8).debut, 0.0)       # pas avant le début
        self.assertEqual(K.passage_lignes(lignes, 7, 7, 31.0).fin, 31.0)        # ni après la fin
        self.assertEqual(K.passage_lignes(lignes, 0, 1, 31.8, 99).repetitions, K.REPETITIONS_MAX)
        with self.assertRaises(K.ErreurKaraoke):
            K.passage_lignes([], 0, 0, 30)

    def test_passage_par_temps(self):
        self.assertEqual(K.passage_temps(5, 20, 30, 3), Passage(5.0, 20.0, 3))
        self.assertEqual(K.passage_temps(-2, 50, 30).fin, 30.0)
        with self.assertRaises(K.ErreurKaraoke):
            K.passage_temps(10, 10.5, 30)
        self.assertEqual(Passage(2.0, 12.0, 3).total, 30.0)

    def test_lignes_des_paroles_en_temps_du_son(self):
        d = fk.paroles()
        d["decalage_s"] = 0.5            # paroles.json : temps du son + decalage_s
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "paroles.json"
            f.write_text(json.dumps(d), encoding="utf-8")
            p = P.lire_paroles(f)
        lignes = K.lignes_paroles(p)
        self.assertEqual(len(lignes), 8)
        self.assertAlmostEqual(lignes[0][0], fk.lignes()[0][0] - 0.5)
        self.assertEqual(lignes[0][2], fk.LIGNES[0][0])
        self.assertEqual(K.lignes_paroles(None), [])

    def test_paroles_du_passage(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "paroles.json"
            f.write_text(json.dumps(fk.paroles()), encoding="utf-8")
            p = P.lire_paroles(f)
        lignes = fk.lignes()
        q = K.paroles_du_passage(p, K.passage_lignes(lignes, 1, 2, 31.8))
        # le passage part 2 s avant la ligne 2 : la fin de la ligne 1 y tombe, mais pas sa majeure partie
        self.assertEqual([l.texte for l in q.originale.lignes], [lignes[1][2], lignes[2][2]])
        self.assertEqual(len(p.originale.lignes), 8)                             # l'original n'est pas touché
        self.assertIsNone(K.paroles_du_passage(None, Passage(0, 1)))


class TestPrises(EssaiAvecCache):

    def test_cachee_gardee_jetee(self):
        d = self.tmp / "Chanson"
        a = K.nouvelle_prise(d, quand=1_800_000_000)
        self.assertTrue(a.name.startswith(".prise-") and a.parent == d / "karaoke")
        a.write_bytes(b"RIFF")
        b = K.nouvelle_prise(d, quand=1_800_000_000)                             # même seconde : un autre nom
        self.assertNotEqual(a, b)
        K.ecrire_plan(a, {"format": 1, "resultat": {"score": 70}})
        self.assertTrue(K.en_attente(a))
        self.assertEqual(K.prises_gardees(d), [])
        g = K.garder(a)
        self.assertFalse(a.exists() or K.plan_de(a).exists())
        self.assertTrue(g.is_file() and K.plan_de(g).is_file() and not K.en_attente(g))
        self.assertEqual(K.prises_gardees(d), [(g, {"format": 1, "resultat": {"score": 70}})])
        self.assertEqual(K.garder(g), g)
        mix = self.tmp / "cache" / "karaoke" / "x-mix.flac"
        mix.parent.mkdir(parents=True)
        mix.write_bytes(b"")
        K.ecrire_plan(g, {"resultat": {"mix": str(mix)}})
        K.jeter(g)
        self.assertFalse(g.exists() or K.plan_de(g).exists() or mix.exists())

    def test_prises_cachees_oubliees_effacees(self):
        d = self.tmp / "Chanson"
        vieille = K.nouvelle_prise(d, quand=1_800_000_000)
        vieille.write_bytes(b"")
        K.ecrire_plan(vieille, {})
        os.utime(vieille, (time.time() - 2 * 86400, time.time() - 2 * 86400))
        recente = d / "karaoke" / ".prise-20990101-000000.wav"
        recente.write_bytes(b"")
        gardee = d / "karaoke" / "prise-20200101-000000.wav"
        gardee.write_bytes(b"")
        os.utime(gardee, (time.time() - 9 * 86400, time.time() - 9 * 86400))
        K.nouvelle_prise(d)
        self.assertFalse(vieille.exists() or K.plan_de(vieille).exists())
        self.assertTrue(recente.exists() and gardee.exists())

    def test_decalage(self):
        plan = {"latence_ms": 150, "mesure": {"prise_debut_s": 0.031, "beat_debut_s": 0.052}}
        self.assertAlmostEqual(K.decalage(plan), 0.031 - 0.052 - 0.150)
        self.assertEqual(K.decalage({}), 0.0)
        self.assertEqual(K.decalage({"latence_ms": "?"}), 0.0)

    def test_nom_du_mp3(self):
        d = self.tmp / "Chanson"
        f = K.nom_mp3(d, "Brise : de mer", quand=1_800_000_000)
        self.assertEqual(f.parent, d / "karaoke")
        self.assertTrue(f.name.startswith("Brise - de mer - karaoké ") and f.suffix == ".mp3", f.name)
        f.parent.mkdir(parents=True)
        f.write_bytes(b"")
        self.assertTrue(K.nom_mp3(d, "Brise : de mer", quand=1_800_000_000).name.endswith(" (2).mp3"))

    def test_la_bibliotheque_ignore_le_dossier_karaoke(self):
        sorties = self.donnees / "sorties"
        d = sorties / "Chanson"
        (d / "travail" / "jules").mkdir(parents=True)
        (d / "Chanson - voix Jules.mp3").write_bytes(b"")
        avant = rapports.lister_resultats(sorties)
        k = d / "karaoke"
        k.mkdir()
        (k / "prise-20260101-000000.wav").write_bytes(b"")
        (k / "Chanson - karaoké 2026-01-01 00h00.mp3").write_bytes(b"")
        (k / "travail").mkdir()                                                  # même pas ainsi
        apres = rapports.lister_resultats(sorties)
        self.assertEqual([(r.dossier, r.voix, r.mp3) for r in apres], [(r.dossier, r.voix, r.mp3) for r in avant])

    def test_preferences_par_defaut(self):
        self.assertEqual(chemins.PREFERENCES_DEFAUT["karaoke"],
                         {"latence_ms": 0, "micro": None, "repetitions": 3, "derniere": ""})
        p = chemins.modifier_preference("karaoke", {"latence_ms": 140})
        self.assertEqual(chemins.lire_preferences()["karaoke"], {"latence_ms": 140})
        self.assertEqual(p["karaoke"]["latence_ms"], 140)


class TestNiveaux(unittest.TestCase):

    def test_gain_de_la_voix(self):
        sr = 16000
        t = np.arange(sr * 4) / sr
        beat = 0.2 * np.sin(2 * np.pi * 110 * t)                                 # −17 dBFS
        voix = np.zeros_like(t)
        voix[sr:3 * sr] = 0.02 * np.sin(2 * np.pi * 330 * t[sr:3 * sr])          # −37 dBFS, la moitié du temps
        self.assertAlmostEqual(K.niveau_actif_db(voix), -37.0, delta=0.5)
        self.assertAlmostEqual(K.gain_voix(voix, beat), 21.0, delta=0.6)          # la voix 1 dB au-dessus du beat
        self.assertEqual(K.gain_voix(np.zeros(sr), beat), K.GAIN_MAX_DB)           # borné
        self.assertEqual(K.gain_voix(voix * 0 + 0.9, beat * 1e-4), K.GAIN_MIN_DB)


class TestCalibration(unittest.TestCase):
    SR = 16000

    def enregistrement(self, latence, decalage_mesure=0.02, absents=(), graine=1, ecart=0.0):
        rng = np.random.default_rng(graine)
        y = rng.normal(0, 0.003, int(7 * self.SR))
        b = K.bip(self.SR)
        for k, u in enumerate(K.instants_bips()):
            if k in absents:
                continue
            a = int(round((u - decalage_mesure + latence + (ecart * k)) * self.SR))
            y[a:a + len(b)] += 0.2 * b
            y[a + 400:a + 400 + len(b)] += 0.06 * b                              # un écho de la pièce
        return y

    def test_latence_retrouvee(self):
        for latence in (0.0, 0.137, 0.42):
            r = K.mesurer_latence(self.enregistrement(latence), K.instants_bips(), 0.02, self.SR)
            self.assertAlmostEqual(r["latence_ms"], latence * 1000, delta=1)
            self.assertEqual((r["entendus"], r["total"]), (4, 4))

    def test_un_bip_perdu_suffit_pas_deux(self):
        r = K.mesurer_latence(self.enregistrement(0.2, absents=(1,)), K.instants_bips(), 0.02, self.SR)
        self.assertEqual(r["entendus"], 3)
        with self.assertRaises(K.ErreurKaraoke):
            K.mesurer_latence(self.enregistrement(0.2, absents=(1, 2)), K.instants_bips(), 0.02, self.SR)

    def test_rien_ou_des_bips_qui_ne_s_accordent_pas(self):
        with self.assertRaises(K.ErreurKaraoke):
            K.mesurer_latence(np.random.default_rng(2).normal(0, 0.01, 7 * self.SR), K.instants_bips(), 0, self.SR)
        with self.assertRaises(K.ErreurKaraoke):
            K.mesurer_latence(self.enregistrement(0.1, ecart=0.05), K.instants_bips(), 0.02, self.SR)

    @unittest.skipUnless(FFMPEG, "ffmpeg absent")
    def test_fichier_des_bips(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "bips.wav"
            instants = K.ecrire_bips(f)
            x, sr = lire_wav(f)
            self.assertEqual((sr, x.shape[1]), (K.SR, 2))
            self.assertAlmostEqual(debut_du_son(x[:, 0], sr), instants[0], delta=0.002)
            r = K.mesurer_latence(f, instants, 0.0)            # le fichier lui-même : latence nulle
            self.assertEqual(r["latence_ms"], 0)


@unittest.skipUnless(FFMPEG, "ffmpeg absent")
class TestBeatEtMix(EssaiAvecCache):

    def test_beat_du_passage_repete(self):
        source = fk.ecrire_wav(self.tmp / "beat.wav", fk.beat(12.0, sr=44100), sr=44100, canaux=2)
        p = Passage(2.5, 6.0, 3)
        b = K.preparer_beat(source, p)
        self.assertTrue(b.is_file() and b.parent == self.tmp / "cache" / "karaoke")
        x, sr = lire_wav(b)
        self.assertEqual((sr, x.shape[1]), (48000, 2))
        un = int(round(3.5 * sr))
        self.assertAlmostEqual(len(x) / sr, 10.5, delta=0.002)
        self.assertTrue(np.array_equal(x[:un], x[un:2 * un]) and np.array_equal(x[:un], x[2 * un:]))
        mtime = b.stat().st_mtime_ns
        self.assertEqual(K.preparer_beat(source, p), b)                          # en cache
        self.assertEqual(b.stat().st_mtime_ns >= mtime, True)
        self.assertNotEqual(K.preparer_beat(source, Passage(2.5, 6.0, 1)), b)
        self.assertEqual([f.name for f in b.parent.iterdir() if ".tmp" in f.name], [])

    def test_mix_cale(self):
        """La voix d'une prise tombe dans le mix au temps du beat donné par le décalage, dans les deux sens."""
        sr = K.SR
        beat = fk.ecrire_wav(self.tmp / "beat.wav", np.zeros(5 * sr), canaux=2)
        voix = np.zeros(4 * sr)
        voix[sr:sr + sr // 2] = 0.3 * np.sin(2 * np.pi * 440 * np.arange(sr // 2) / sr)   # à 1,0 s de la prise
        prise = fk.ecrire_wav(self.tmp / "prise.wav", voix)
        for decal in (0.3, -0.2):
            mix = K.mixer(prise, beat, decal, 0.0, self.tmp / f"mix{decal}.flac")
            x = K.lire_mono(mix, sr)
            self.assertAlmostEqual(debut_du_son(x, sr), 1.0 + decal, delta=0.003)
            self.assertAlmostEqual(len(x) / sr, 5.0, delta=0.01)                 # la durée du beat
        self.assertIn("adelay=delays=14400S", K.graphe_mix(0.3, 0.0))
        self.assertIn("atrim=start_sample=9600", K.graphe_mix(-0.2, 0.0))

    def test_mp3(self):
        mix = fk.ecrire_wav(self.tmp / "mix.wav", 0.1 * np.ones(K.SR), canaux=2)
        f = K.exporter_mp3(mix, self.tmp / "k" / "a.mp3", "Brise de mer (karaoké)", "justesse 80/100")
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=title,comment", "-of", "json",
                            str(f)], capture_output=True, text=True)
        tags = json.loads(r.stdout)["format"]["tags"]
        self.assertEqual(tags.get("title"), "Brise de mer (karaoké)")
        self.assertIn("80/100", tags.get("comment", ""))


@unittest.skipUnless(FFMPEG, "ffmpeg absent")
class TestAnalyse(EssaiAvecCache):
    """Une prise de synthèse dans un vrai dossier de doublage inventé, notée par « python3 -m voixla.karaoke »."""

    def preparer(self, voix, passage, latence_ms=0, mesure=(0.0, 0.0), retard_prise=0.0):
        dossier = fk.creer(self.donnees)
        travail = dossier / "travail"
        source = K.fichier_beat(travail)
        prise = K.nouvelle_prise(dossier)
        x = np.concatenate([np.zeros(int(round(retard_prise * fk.SR)), np.float32), voix])
        fk.ecrire_wav(prise, x)
        K.ecrire_plan(prise, {"format": 1, "titre": fk.TITRE, "dossier": str(dossier), "voix": "jules",
                              "dossier_voix": str(travail / "jules"), "travail": str(travail),
                              "passage": {"debut": passage.debut, "fin": passage.fin,
                                          "repetitions": passage.repetitions},
                              "lignes": [list(l) for l in fk.lignes()], "latence_ms": latence_ms,
                              "mesure": {"prise_debut_s": mesure[0], "beat_debut_s": mesure[1]},
                              "beat": {"source": str(source), "fichier": ""}})
        return dossier, prise

    def test_boucle_latence_et_mesures(self):
        """Trois tours (le deuxième faux d'un ton), 250 ms de latence et un départ du micro 20 ms avant le beat."""
        p = Passage(14.4, 24.0, 3)
        tours = [fk.voix(debut=p.debut, fin=p.fin, faux={4: f, 5: f}) for f in (0.0, 2.0, 0.0)]
        decal = 0.03 - 0.05 - 0.25
        dossier, prise = self.preparer(np.concatenate(tours), p, 250, (0.03, 0.05), retard_prise=-decal)
        env = dict(os.environ, PYTHONPATH=str(INTERFACE), VOIXLA_AVANCEMENT="1")
        r = subprocess.run([sys.executable, "-m", "voixla.karaoke", "analyser", str(prise)], capture_output=True,
                           text=True, env=env, cwd=str(INTERFACE), timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        lignes = [json.loads(l) for l in r.stdout.splitlines() if l.startswith("{")]
        self.assertTrue(any("avancement" in l for l in lignes))
        res = K.lire_plan(prise)["resultat"]
        notes = [t["score"] for t in res["tours"]]
        self.assertGreaterEqual(notes[0], 95)
        self.assertGreaterEqual(notes[2], 95)
        self.assertLess(notes[1], 20)
        self.assertAlmostEqual(res["decalage_s"], decal, places=4)
        self.assertEqual(res["latence_ms"], 250)
        self.assertTrue(Path(res["mix"]).is_file())
        self.assertAlmostEqual(K.duree_audio(res["mix"]), p.total, delta=0.02)
        self.assertEqual(res["reference"], "courbes.json")
        sortie = K.nom_mp3(dossier, fk.TITRE)
        r = subprocess.run([sys.executable, "-m", "voixla.karaoke", "mp3", str(prise), str(sortie), "--titre",
                            "Brise de mer (karaoké)"], capture_output=True, text=True, env=env, cwd=str(INTERFACE),
                           timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(sortie.is_file() and sortie.parent == dossier / "karaoke")

    def test_reference_calculee_depuis_la_voix_d_origine(self):
        dossier, prise = self.preparer(fk.voix(transposition=-12), K.passage_entier(fk.duree_chanson()))
        (dossier / "travail" / "jules" / "courbes.json").unlink()
        res = K.analyser(prise)
        self.assertEqual(res["reference"], "voix_source (YIN)")
        self.assertGreaterEqual(res["score"], 95)
        self.assertEqual(res["octave"], -1)

    def test_sans_reference_le_mix_se_fait(self):
        dossier, prise = self.preparer(fk.voix(), K.passage_entier(fk.duree_chanson()))
        (dossier / "travail" / "jules" / "courbes.json").unlink()
        (dossier / "travail" / "voix_source.wav").unlink()
        res = K.analyser(prise)
        self.assertIsNone(res["score"])
        self.assertIn("pas de note", res["raison"])
        self.assertTrue(Path(res["mix"]).is_file())

    def test_mix_refait_s_il_a_ete_purge(self):
        dossier, prise = self.preparer(fk.voix(), K.passage_lignes(fk.lignes(), 0, 1, fk.duree_chanson()))
        res = K.analyser(prise)
        Path(res["mix"]).unlink()
        self.assertIsNone(K.mix_pret(prise))
        sortie = K.exporter(prise, dossier / "karaoke" / "a.mp3", "t")
        self.assertTrue(sortie.is_file() and K.mix_pret(prise) is not None)
        self.assertEqual(K.lire_plan(prise)["resultat"]["score"], res["score"])   # la note n'est pas refaite

    def test_ligne_de_commande_sans_plan(self):
        r = subprocess.run([sys.executable, "-m", "voixla.karaoke", "analyser", str(self.tmp / "absente.wav")],
                           capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(INTERFACE)),
                           cwd=str(INTERFACE), timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("plan de la prise introuvable", r.stderr)


class TestSobriete(unittest.TestCase):

    def test_nice_et_file_attente_au_dela_d_une_minute(self):
        court = prep.commande("python3", "voixla.karaoke", ["analyser", "p.wav"], 45.0, "/usr/bin/file-attente")
        self.assertEqual(court[:3], ["nice", "-n", "10"])
        long = prep.commande("python3", "voixla.karaoke", ["analyser", "p.wav"], 240.0, "/usr/bin/file-attente")
        self.assertEqual(long[:2], ["/usr/bin/file-attente", "voix-la-analyse"])
        self.assertIn("nice", long)


try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    GUI = True
except (ImportError, ValueError):
    GUI = False


@unittest.skipUnless(FFMPEG and GUI and shutil.which("xvfb-run") and shutil.which("dbus-run-session")
                     and shutil.which("flock"), "ffmpeg, GTK, xvfb-run, dbus-run-session ou flock absent")
class EssaiPage(EssaiAvecCache):
    """La page sous Xvfb, sur un bus privé, micro de synthèse et sortie fakeaudiosink (tests/essai_karaoke_gui.py)."""

    def test_page(self):
        fk.creer(self.donnees)
        prise = fk.ecrire_wav(self.tmp / "prise-imitee.wav",
                              fk.voix(transposition=-12, vibrato_cents=30, harmoniques=4, faux={2: 1.5},
                                      bruit_db=-48, graine=3))
        env = dict(os.environ)
        env.update({"GTK_A11Y": "none", "NO_AT_BRIDGE": "1", "GIO_USE_VFS": "local", "GSK_RENDERER": "cairo",
                    "ADW_DISABLE_PORTAL": "1", "VOIXLA_APP_ID": f"io.mawena.VoixLa.Essai{os.getpid()}",
                    "GST_PLUGIN_FEATURE_RANK": "fakeaudiosink:MAX", "VOIXLA_PAGE": "karaoke",
                    "VOIXLA_MICRO_ESSAI": "1", "VOIXLA_KARAOKE_PRISE": str(prise),
                    "XDG_STATE_HOME": str(self.tmp / "xdg-etat"),
                    "PYTHONPATH": os.pathsep.join([str(INTERFACE)] + ([os.environ["PYTHONPATH"]]
                                                                        if os.environ.get("PYTHONPATH") else []))})
        verrou = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "voix-la-xvfb.verrou"
        cmd = ["flock", str(verrou), "dbus-run-session", f"--config-file={INTERFACE / 'outils' / 'bus-essai.conf'}",
               "--", "xvfb-run", "-a", "-s", "-screen 0 1400x1000x24", "nice", "-n", "10", sys.executable, "-m",
               "tests.essai_karaoke_gui"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                             cwd=str(INTERFACE), stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            sortie, erreur = p.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            sortie, erreur = p.communicate()
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGTERM)
        time.sleep(0.5)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGKILL)
        mesures = {}
        for ligne in sortie.splitlines():
            with contextlib.suppress(ValueError):
                mesures.update(json.loads(ligne))
        trace = sortie[-3000:] + erreur[-3000:]
        self.assertEqual(p.returncode, 0, trace)
        self.assertEqual(mesures.get("erreurs"), [], trace)
        self.assertTrue(mesures.get("fini"), trace)


if __name__ == "__main__":
    unittest.main()
