"""Essais de voixla.telephone : liste des téléphones (adb devices -l), commande scrcpy selon les options connues,
prise complète, arrêt par SIGINT, prise vide ou en échec, abandon, « adb connect », téléphone imité. Avec un faux adb
et un faux scrcpy (faux_telephone.py) dans un dossier temporaire, et ffmpeg pour les sons : aucun téléphone ni réseau."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from voixla import telephone

ICI = Path(__file__).resolve().parent
FAUX = ICI / "faux_telephone.py"
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
VARIABLES = ("VOIXLA_SCRCPY", "VOIXLA_ADB", "ADB", "ADB_SERIAL", "VOIXLA_TELEPHONE_ESSAI")

SORTIE_ADB = """* daemon not running; starting now at tcp:5037
* daemon started successfully
List of devices attached
192.168.0.104:5555     device product:panther model:Pixel_7 device:panther transport_id:3
0A1B2C3D4E             unauthorized usb:1-4 transport_id:4
emulator-5554          offline transport_id:1
R58M12ABCDE            no permissions (missing udev rules? user is in the plugdev group); see [http://developer.android.com/tools/device.html] usb:1-1 transport_id:5
adb-28041FDH2001AB-Xyz12._adb-tls-connect._tcp device product:husky model:Pixel_8_Pro device:husky transport_id:6

"""
PIXEL_7 = "192.168.0.104:5555     device product:panther model:Pixel_7 device:panther transport_id:3"
PIXEL_8 = "28041FDH2001AB         device usb:1-1 product:husky model:Pixel_8_Pro device:husky transport_id:7"


def format_audio(fichier):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "stream=sample_rate,channels,sample_fmt,bits_per_raw_sample:format=duration", "-of", "json",
                        str(fichier)], capture_output=True, text=True, timeout=30, check=True)
    d = json.loads(r.stdout)
    s = d["streams"][0]
    return {"frequence": int(s["sample_rate"]), "canaux": s["channels"], "format": s["sample_fmt"],
            "duree": float(d["format"]["duration"])}


class EssaiTelephone(unittest.TestCase):
    """Dossier temporaire avec bin/adb et bin/scrcpy (le faux), VOIXLA_ADB et VOIXLA_SCRCPY vers eux, trace."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="voixla-telephone-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for outil in ("adb", "scrcpy"):
            f = self.bin / outil
            f.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAUX))} {outil} \"$@\"\n")
            f.chmod(0o755)
        self.fichier_trace = self.tmp / "trace.jsonl"
        env = {k: v for k, v in os.environ.items() if k not in VARIABLES and not k.startswith("FAUX_")}
        env.update({"VOIXLA_SCRCPY": str(self.bin / "scrcpy"), "VOIXLA_ADB": str(self.bin / "adb"),
                    "FAUX_TELEPHONE_TRACE": str(self.fichier_trace)})
        rustine = mock.patch.dict(os.environ, env, clear=True)
        rustine.start()
        self.addCleanup(rustine.stop)
        telephone._options.clear()
        self.addCleanup(telephone._options.clear)

    def trace(self):
        try:
            return [json.loads(l) for l in self.fichier_trace.read_text(encoding="utf-8").splitlines() if l.strip()]
        except OSError:
            return []

    def lancements(self, outil):
        return [t["argv"] for t in self.trace() if t.get("outil") == outil and "argv" in t]


class EssaiListe(unittest.TestCase):
    """Lecture de « adb devices -l », noms et libellés, sans rien lancer."""

    def test_lire_appareils(self):
        a = telephone.lire_appareils(SORTIE_ADB)
        self.assertEqual([x.serie for x in a], ["192.168.0.104:5555", "0A1B2C3D4E", "emulator-5554", "R58M12ABCDE",
                                                "adb-28041FDH2001AB-Xyz12._adb-tls-connect._tcp"])
        self.assertEqual([x.etat for x in a], ["device", "unauthorized", "offline", "no permissions", "device"])
        self.assertEqual(a[0].modele, "Pixel 7")
        self.assertEqual(a[4].modele, "Pixel 8 Pro")
        self.assertEqual(a[1].nom, "0A1B2C3D4E")    # sans modèle : la série
        self.assertEqual([x.sans_fil for x in a], [True, False, False, False, True])
        self.assertEqual(a[0].cle, "telephone:192.168.0.104:5555")
        self.assertEqual(telephone.lire_appareils("List of devices attached\n\n"), [])
        self.assertEqual(telephone.lire_appareils(""), [])

    def test_nom_modele(self):
        self.assertEqual(telephone.nom_modele("Pixel_7"), "Pixel 7")
        self.assertEqual(telephone.nom_modele("SM_G991B"), "SM G991B")
        self.assertEqual(telephone.nom_modele(""), "")

    def test_libelles(self):
        a = telephone.lire_appareils(SORTIE_ADB)
        self.assertEqual(telephone.libelle(a[0], a), "Téléphone Pixel 7 (sans fil, 192.168.0.104)")
        self.assertEqual(telephone.libelle(a[4], a), "Téléphone Pixel 8 Pro (sans fil)")
        usb = telephone.Appareil("28041FDH2001AB", "device", "Pixel 8 Pro")
        self.assertEqual(telephone.libelle(usb, [usb]), "Téléphone Pixel 8 Pro (USB)")
        # deux Pixel 7 en USB : la série les distingue
        b = [telephone.Appareil("AAA", "device", "Pixel 7"), telephone.Appareil("BBB", "device", "Pixel 7")]
        self.assertEqual(telephone.libelle(b[1], b), "Téléphone Pixel 7 (USB, BBB)")
        self.assertEqual(telephone.libelle(telephone.Appareil("essai-pixel-7", "device", "Pixel 7")),
                         "Téléphone Pixel 7 (essai)")

    def test_adb_serial_en_premier(self):
        a = [telephone.Appareil("AAA"), telephone.Appareil("192.168.0.104:5555"), telephone.Appareil("CCC")]
        with mock.patch.dict(os.environ, {"ADB_SERIAL": "192.168.0.104:5555"}):
            self.assertEqual([x.serie for x in telephone.trier(a)], ["192.168.0.104:5555", "AAA", "CCC"])
        with mock.patch.dict(os.environ, {"ADB_SERIAL": ""}):
            self.assertEqual([x.serie for x in telephone.trier(a)], ["AAA", "192.168.0.104:5555", "CCC"])

    def test_erreur_scrcpy(self):
        sortie = ("scrcpy 5.0.1 <https://github.com/Genymobile/scrcpy>\nERROR: Could not find any ADB device\n"
                  "ERROR: Server connection failed\n")
        self.assertEqual(telephone.erreur_scrcpy(sortie), "Could not find any ADB device ; Server connection failed")
        self.assertEqual(telephone.erreur_scrcpy("INFO: tout va bien"), "")


class EssaiCommande(EssaiTelephone):
    def test_commande_scrcpy_5(self):
        chemin, options = telephone.verifier_scrcpy()
        self.assertEqual(chemin, str(self.bin / "scrcpy"))
        version, _ = telephone.options_scrcpy(chemin)
        self.assertEqual(version, "5.0.1")
        cmd = telephone.commande_enregistrement(chemin, options, "192.168.0.104:5555", "/tmp/x.flac", 120)
        self.assertEqual(cmd, [chemin, "-s", "192.168.0.104:5555", "--no-video", "--no-playback", "--no-window",
                               "--audio-source=mic-voice-recognition", "--audio-codec=flac", "--require-audio",
                               "--record=/tmp/x.flac", "--time-limit=120"])

    def test_scrcpy_sans_les_options_recentes(self):
        os.environ["FAUX_SCRCPY_ANCIEN"] = "1"
        chemin, options = telephone.verifier_scrcpy()
        cmd = telephone.commande_enregistrement(chemin, options, "AAA", "/tmp/x.flac", 59.6)
        self.assertIn("--audio-source=mic", cmd)
        self.assertNotIn("--require-audio", cmd)
        self.assertEqual(cmd[-1], "--time-limit=60")

    def test_scrcpy_trop_ancien(self):
        os.environ["FAUX_SCRCPY_ANCIEN"] = "vieux"
        with self.assertRaises(telephone.TelephoneIndisponible) as e:
            telephone.verifier_scrcpy()
        self.assertEqual(e.exception.args[0], "scrcpy trop ancien")
        self.assertIn("--no-window", e.exception.args[1])
        self.assertIn("5.0.1", e.exception.args[1])

    def test_conversion(self):
        self.assertEqual(telephone.commande_conversion("a.flac", "a.wav", 24)[-6:],
                         ["-ac", "1", "-ar", "48000", "-c:a", "pcm_s24le", "a.wav"][-6:])
        self.assertIn("pcm_s16le", telephone.commande_conversion("a.flac", "a.wav", 16))

    def test_commandes_trouvees_par_le_path(self):
        """Sans VOIXLA_*, scrcpy vient du PATH et adb est celui livré à côté de scrcpy (le même que scrcpy prendra)."""
        del os.environ["VOIXLA_SCRCPY"], os.environ["VOIXLA_ADB"]
        os.environ["PATH"] = f"{self.bin}:/usr/bin:/bin"
        self.assertEqual(telephone.scrcpy(), str(self.bin / "scrcpy"))
        self.assertEqual(telephone.adb(), str(self.bin / "adb"))
        autre = self.tmp / "autre-adb"
        shutil.copy(self.bin / "adb", autre)
        os.environ["ADB"] = str(autre)           # la variable de scrcpy l'emporte
        self.assertEqual(telephone.adb(), str(autre))
        os.environ["VOIXLA_SCRCPY"] = str(self.tmp / "absent")
        self.assertIsNone(telephone.scrcpy())


class EssaiRecherche(EssaiTelephone):
    def test_telephones_prets(self):
        os.environ["FAUX_ADB_APPAREILS"] = f"0A1B2C3D4E unauthorized usb:1-4;{PIXEL_8};{PIXEL_7}"
        r = telephone.rechercher()
        self.assertEqual([a.serie for a in r.telephones], ["28041FDH2001AB", "192.168.0.104:5555"])
        self.assertEqual([a.serie for a in r.autres], ["0A1B2C3D4E"])
        self.assertEqual(r.message, "")
        self.assertIn(["devices", "-l"], self.lancements("adb"))
        os.environ["ADB_SERIAL"] = "192.168.0.104:5555"
        self.assertEqual([a.serie for a in telephone.rechercher().telephones], ["192.168.0.104:5555",
                                                                                "28041FDH2001AB"])

    def test_aucun_telephone(self):
        r = telephone.rechercher()
        self.assertEqual(r.telephones, [])
        self.assertEqual(r.titre, "Aucun téléphone connecté")
        self.assertIn("adb connect", r.message)
        self.assertIn("USB", r.message)

    def test_telephone_non_autorise(self):
        os.environ["FAUX_ADB_APPAREILS"] = "0A1B2C3D4E unauthorized usb:1-4 transport_id:4"
        r = telephone.rechercher()
        self.assertEqual(r.telephones, [])
        self.assertEqual(r.titre, "Téléphone non autorisé")
        self.assertIn("débogage USB", r.message)

    def test_scrcpy_ou_adb_absent(self):
        os.environ["VOIXLA_SCRCPY"] = str(self.tmp / "absent")
        r = telephone.rechercher()
        self.assertEqual((r.telephones, r.titre), ([], "scrcpy introuvable"))
        self.assertEqual(self.lancements("adb"), [])
        os.environ["VOIXLA_SCRCPY"] = str(self.bin / "scrcpy")
        os.environ["VOIXLA_ADB"] = str(self.tmp / "absent")
        self.assertEqual(telephone.rechercher().titre, "adb introuvable")

    def test_reconnexion_sans_fil(self):
        """« Réessayer » : adb connect pour les adresses IP:port seulement, une fois chacune, avant la liste."""
        os.environ["FAUX_ADB_APPAREILS"] = PIXEL_7
        r = telephone.rechercher(reconnecter=["192.168.0.104:5555", "0A1B2C3D4E", "", "192.168.0.104:5555"])
        self.assertEqual(len(r.telephones), 1)
        self.assertEqual(self.lancements("adb"), [["connect", "192.168.0.104:5555"], ["devices", "-l"]])

    def test_reconnexion_ratee(self):
        os.environ["FAUX_ADB_CONNECT"] = "echec"
        r = telephone.rechercher(reconnecter=["192.168.0.104:5555"])
        self.assertEqual(r.titre, "Aucun téléphone connecté")
        self.assertIn("« adb connect 192.168.0.104:5555 » : failed to connect", r.message)

    def test_telephone_imite(self):
        os.environ["VOIXLA_TELEPHONE_ESSAI"] = "1"
        os.environ["VOIXLA_SCRCPY"] = str(self.tmp / "absent")      # ni scrcpy ni adb ne sont lancés
        r = telephone.rechercher()
        self.assertEqual([telephone.libelle(a) for a in r.telephones], ["Téléphone Pixel 7 (essai)"])
        os.environ["VOIXLA_TELEPHONE_ESSAI"] = "aucun"
        self.assertEqual(telephone.rechercher().titre, "Aucun téléphone connecté")
        self.assertEqual(self.trace(), [])


@unittest.skipUnless(FFMPEG, "ffmpeg ou ffprobe absent")
class EssaiPrise(EssaiTelephone):
    def prise(self, duree=1, bits=24, nom="chant-20261106-120000.wav"):
        return telephone.Prise("192.168.0.104:5555", self.tmp / "voix" / "jules" / "chant" / nom, duree, bits)

    def en_fond(self, prise):
        resultat = {}
        fil = threading.Thread(target=lambda: resultat.setdefault("r", prise.executer()))
        fil.start()
        attente = time.monotonic() + 10
        while not any(a.startswith("--record=") for a in sum(self.lancements("scrcpy"), [])) \
                and time.monotonic() < attente:
            time.sleep(0.02)
        time.sleep(0.4)      # le faux scrcpy pose son gestionnaire de SIGINT juste après sa trace
        return fil, resultat

    def fichiers(self):
        return sorted(p.name for p in (self.tmp / "voix" / "jules" / "chant").iterdir())

    def test_prise_chant(self):
        p = self.prise(duree=1)
        self.assertIsNone(p.executer())
        self.assertEqual(self.fichiers(), ["chant-20261106-120000.wav"])     # le FLAC caché est effacé
        f = format_audio(p.wav)
        self.assertEqual((f["frequence"], f["canaux"], f["format"]), (48000, 1, "s32"))   # 24 bits dans s32
        self.assertAlmostEqual(f["duree"], 1.0, delta=0.4)
        scrcpy = [a for a in self.lancements("scrcpy") if "--help" not in a and "--version" not in a]
        self.assertEqual(len(scrcpy), 1)
        self.assertEqual(scrcpy[0][:2], ["-s", "192.168.0.104:5555"])
        self.assertIn("--time-limit=1", scrcpy[0])
        self.assertIn(f"--record={p.flac}", scrcpy[0])
        self.assertTrue(p.flac.name.startswith("."))
        # scrcpy reçoit l'adb de l'interface (un seul serveur adb)
        self.assertEqual([t["adb"] for t in self.trace() if t.get("outil") == "scrcpy" and "argv" in t][-1],
                         str(self.bin / "adb"))

    def test_prise_parole_16_bits(self):
        p = self.prise(duree=1, bits=16, nom="parole-20261106-120000.wav")
        self.assertIsNone(p.executer())
        f = format_audio(p.wav)
        self.assertEqual((f["frequence"], f["canaux"], f["format"]), (48000, 1, "s16"))

    def test_arret_avant_la_fin(self):
        """« Arrêter » : SIGINT au groupe de scrcpy, qui termine le fichier ; la prise garde ce qui a été chanté."""
        p = self.prise(duree=30)
        debut = time.monotonic()
        fil, resultat = self.en_fond(p)
        p.arreter()
        fil.join(15)
        self.assertFalse(fil.is_alive())
        self.assertIsNone(resultat["r"])
        self.assertLess(time.monotonic() - debut, 10)
        self.assertIn({"outil": "scrcpy", "signal": "SIGINT"}, self.trace())
        self.assertLess(format_audio(p.wav)["duree"], 5)
        self.assertEqual(self.fichiers(), ["chant-20261106-120000.wav"])

    def test_prise_vide(self):
        os.environ["FAUX_SCRCPY"] = "vide"
        p = self.prise(duree=1)
        erreur = p.executer()
        self.assertIn("le téléphone n’a rien enregistré", erreur)
        self.assertIn("192.168.0.104:5555", erreur)
        self.assertEqual(self.fichiers(), [])

    def test_scrcpy_en_echec(self):
        os.environ["FAUX_SCRCPY"] = "erreur"
        erreur = self.prise(duree=1).executer()
        self.assertIn("rien enregistré", erreur)
        self.assertIn("Could not find any ADB device", erreur)
        self.assertEqual(self.fichiers(), [])

    def test_abandon(self):
        p = self.prise(duree=30)
        fil, resultat = self.en_fond(p)
        p.abandonner()
        fil.join(15)
        self.assertIsNone(resultat["r"])
        self.assertEqual(self.fichiers(), [])

    def test_scrcpy_tetu(self):
        """S'il ignore SIGINT, SIGKILL après la marge ; rien n'est écrit, et l'erreur le dit."""
        os.environ["FAUX_SCRCPY"] = "tetu"
        p = self.prise(duree=30)
        p.MARGE_ARRET_S = 0.5
        debut = time.monotonic()
        fil, resultat = self.en_fond(p)
        p.arreter()
        fil.join(15)
        self.assertLess(time.monotonic() - debut, 8)
        self.assertIn("rien enregistré", resultat["r"])
        self.assertEqual(self.fichiers(), [])

    def test_scrcpy_absent(self):
        os.environ["VOIXLA_SCRCPY"] = str(self.tmp / "absent")
        self.assertIn("scrcpy introuvable", self.prise().executer())

    def test_prise_imitee(self):
        """VOIXLA_TELEPHONE_ESSAI : un son de synthèse en temps réel, converti comme une vraie prise."""
        os.environ["VOIXLA_TELEPHONE_ESSAI"] = "1"
        os.environ["VOIXLA_SCRCPY"] = str(self.tmp / "absent")
        p = telephone.Prise("essai-pixel-7", self.tmp / "voix" / "jules" / "chant" / "chant-essai.wav", 2, 24)
        debut = time.monotonic()
        self.assertIsNone(p.executer())
        self.assertGreater(time.monotonic() - debut, 1.2)        # en temps réel (-re, après une courte avance)
        f = format_audio(p.wav)
        self.assertEqual((f["frequence"], f["canaux"], f["format"]), (48000, 1, "s32"))
        self.assertEqual(self.fichiers(), ["chant-essai.wav"])
        self.assertEqual(self.trace(), [])


if __name__ == "__main__":
    unittest.main()
