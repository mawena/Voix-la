"""MPRIS (voixla/mpris.py) : nom de bus, propriétés et métadonnées, puis le serveur sur un bus de session privé
(dbus-daemon lancé pour l'essai, avec outils/bus-essai.conf : jamais le bus du bureau) : Play, Pause, Raise, Seek…
appelés par gdbus, et les signaux PropertiesChanged et Seeked reçus par un second client."""

import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from gi.repository import Gio, GLib

from voixla import APP_ID
from voixla import lecture as regles
from voixla import mpris

CONF = Path(__file__).resolve().parents[1] / "outils" / "bus-essai.conf"
DBUS = shutil.which("dbus-daemon")
GDBUS = shutil.which("gdbus")


def etat_chanson(**k):
    valeurs = dict(statut=regles.PAUSE, position_s=12.0, duree_s=200.0, titre="Chanson fictive", voix="Jules",
                   piste="Doublage", genre="resultat", cle=("resultat", "/sorties/Chanson fictive", "jules"),
                   image="/cache/miniatures/abc.jpg")
    valeurs.update(k)
    return regles.Etat(**valeurs)


class EssaiNoms(unittest.TestCase):
    def test_vrai_nom(self):
        self.assertEqual(mpris.nom_bus({}), "org.mpris.MediaPlayer2.voixla")
        self.assertEqual(mpris.identite({}), "Voix-là")

    def test_essai(self):
        env = {"VOIXLA_APP_ID": "io.mawena.VoixLa.Essai1"}
        self.assertEqual(mpris.nom_bus(env, pid=4242), "org.mpris.MediaPlayer2.voixla.essai4242")
        self.assertEqual(mpris.nom_bus(env), f"org.mpris.MediaPlayer2.voixla.essai{os.getpid()}")
        self.assertEqual(mpris.identite(env), "Voix-là (essai)")
        self.assertTrue(Gio.dbus_is_name(mpris.nom_bus(env)))

    def test_identifiant_piste(self):
        a = mpris.identifiant_piste(("resultat", "/sorties/Chanson fictive", "jules"))
        self.assertTrue(a.startswith(mpris.PREFIXE_PISTE))
        self.assertTrue(GLib.Variant.is_object_path(a))
        self.assertEqual(a, mpris.identifiant_piste(("resultat", "/sorties/Chanson fictive", "jules")), "stable")
        self.assertNotEqual(a, mpris.identifiant_piste(("resultat", "/sorties/Chanson fictive", "lea")))
        self.assertEqual(mpris.identifiant_piste(()), mpris.PAS_DE_PISTE)


class EssaiProprietes(unittest.TestCase):
    def test_metadonnees(self):
        m = mpris.metadonnees(etat_chanson())
        self.assertEqual(m["xesam:title"].unpack(), "Chanson fictive")
        self.assertEqual(m["xesam:artist"].unpack(), ["voix Jules · Doublage"])
        self.assertEqual(m["mpris:length"].unpack(), 200_000_000)
        self.assertEqual(m["mpris:artUrl"].unpack(), "file:///cache/miniatures/abc.jpg")
        self.assertEqual(m["voixla:voix"].unpack(), "Jules")
        self.assertEqual(m["voixla:piste"].unpack(), "Doublage")
        self.assertEqual(m["mpris:trackid"].get_type_string(), "o")

    def test_changer_de_piste_garde_le_morceau(self):
        a = mpris.metadonnees(etat_chanson())
        b = mpris.metadonnees(etat_chanson(piste="Beat seul"))
        self.assertEqual(a["mpris:trackid"].unpack(), b["mpris:trackid"].unpack())
        self.assertEqual(b["xesam:artist"].unpack(), ["voix Jules · Beat seul"])

    def test_sans_session(self):
        m = mpris.metadonnees(regles.Etat())
        self.assertEqual(list(m), ["mpris:trackid"])
        self.assertEqual(m["mpris:trackid"].unpack(), mpris.PAS_DE_PISTE)
        p = mpris.proprietes(regles.Etat())[mpris.LECTEUR]
        self.assertEqual(p["PlaybackStatus"].unpack(), "Stopped")
        self.assertFalse(p["CanPlay"].unpack())

    def test_sans_image_ni_duree(self):
        m = mpris.metadonnees(etat_chanson(image=None, duree_s=None, voix="", piste=""))
        self.assertNotIn("mpris:artUrl", m)
        self.assertNotIn("mpris:length", m)
        self.assertNotIn("xesam:artist", m)
        self.assertFalse(mpris.proprietes(etat_chanson(duree_s=None))[mpris.LECTEUR]["CanSeek"].unpack())

    def test_proprietes(self):
        p = mpris.proprietes(etat_chanson(statut=regles.LECTURE), "Voix-là")
        r, l = p[mpris.RACINE], p[mpris.LECTEUR]
        self.assertEqual(r["Identity"].unpack(), "Voix-là")
        self.assertEqual(r["DesktopEntry"].unpack(), APP_ID)
        self.assertTrue(r["CanQuit"].unpack() and r["CanRaise"].unpack())
        self.assertFalse(r["HasTrackList"].unpack())
        self.assertEqual(l["PlaybackStatus"].unpack(), "Playing")
        self.assertEqual(l["Position"].unpack(), 12_000_000)
        for nom in ("CanPlay", "CanPause", "CanSeek", "CanControl"):
            self.assertTrue(l[nom].unpack(), nom)
        self.assertFalse(l["CanGoNext"].unpack() or l["CanGoPrevious"].unpack(), "pas de file")
        # chaque propriété a le type annoncé par l'introspection
        for iface, props in p.items():
            info = mpris.INTERFACES[iface]
            for nom, v in props.items():
                self.assertEqual(v.get_type_string(), info.lookup_property(nom).signature, nom)
            self.assertEqual({x.name for x in info.properties}, set(props), iface)

    def test_changements(self):
        a = mpris.proprietes(etat_chanson(statut=regles.PAUSE))
        b = mpris.proprietes(etat_chanson(statut=regles.LECTURE, position_s=30.0))
        d = mpris.changements(a, b)
        self.assertEqual(list(d), [mpris.LECTEUR])
        self.assertEqual(set(d[mpris.LECTEUR]), {"PlaybackStatus"}, "Position ne se signale pas")
        self.assertEqual(mpris.changements(b, b), {})
        c = mpris.proprietes(etat_chanson(statut=regles.LECTURE, piste="Original"))
        self.assertEqual(set(mpris.changements(b, c)[mpris.LECTEUR]), {"Metadata"})
        self.assertIn(mpris.RACINE, mpris.changements(None, b), "première fois : tout")


class Pilote:
    """Le pilote factice du serveur : un état, et le journal des commandes reçues."""

    def __init__(self):
        self.e = etat_chanson()
        self.appels = []

    def etat(self):
        return self.e

    def lire(self):
        self.appels.append("lire")
        self.e.statut = regles.LECTURE

    def pause(self):
        self.appels.append("pause")
        self.e.statut = regles.PAUSE

    def lecture_pause(self):
        self.appels.append("lecture_pause")
        self.e.statut = regles.PAUSE if self.e.statut == regles.LECTURE else regles.LECTURE

    def arreter(self):
        self.appels.append("arreter")
        self.e = regles.Etat()

    def placer(self, s):
        self.appels.append(("placer", round(s, 3)))
        self.e.position_s = s

    def deplacer(self, d):
        self.placer(self.e.position_s + d)

    def relever(self):
        self.appels.append("relever")

    def quitter(self):
        self.appels.append("quitter")

    def regler_volume(self, v):
        self.appels.append(("volume", v))
        self.e.volume = v


@unittest.skipUnless(DBUS and GDBUS and CONF.is_file(), "dbus-daemon ou gdbus absent")
class EssaiBusPrive(unittest.TestCase):
    """Le serveur sur un bus privé, appelé comme le ferait GNOME (gdbus), signaux reçus par un autre client."""

    NOM = "org.mpris.MediaPlayer2.voixla.essai1"

    @classmethod
    def setUpClass(cls):
        cls.bus = subprocess.Popen([DBUS, f"--config-file={CONF}", "--nofork", "--print-address=1"],
                                   stdout=subprocess.PIPE, text=True)
        cls.adresse = cls.bus.stdout.readline().strip()

    @classmethod
    def tearDownClass(cls):
        cls.bus.terminate()
        cls.bus.wait(timeout=10)
        cls.bus.stdout.close()

    def connexion(self):
        drapeaux = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        c = Gio.DBusConnection.new_for_address_sync(self.adresse, drapeaux, None, None)
        self.addCleanup(c.close_sync, None)
        return c

    def setUp(self):
        self.pilote = Pilote()
        self.serveur = mpris.ServeurMpris(self.connexion(), self.pilote, nom=self.NOM, identite_="Voix-là (essai)")
        self.addCleanup(self.serveur.retirer)
        self.client = self.connexion()
        self.signaux = []
        ident = self.client.signal_subscribe(None, None, None, mpris.CHEMIN, None, Gio.DBusSignalFlags.NONE,
                                             lambda _c, _e, _ch, iface, nom, params: self.signaux.append(
                                                 (iface, nom, params.unpack())))
        self.addCleanup(self.client.signal_unsubscribe, ident)
        self.serveur.publier()
        self.assertTrue(self.tourner(lambda: self.serveur.nom_pris is True), "nom de bus pris")

    @staticmethod
    def tourner(condition, delai=5.0):
        ctx = GLib.MainContext.default()
        fin = time.monotonic() + delai
        while time.monotonic() < fin:
            while ctx.pending():
                ctx.iteration(False)
            if condition():
                return True
            time.sleep(0.005)
        return bool(condition())

    def gdbus(self, *args):
        """gdbus en processus à part, pendant que la boucle de ce processus sert les appels."""
        p = subprocess.Popen([GDBUS, "call", "--address", self.adresse, "--dest", self.NOM, "--object-path",
                              mpris.CHEMIN, "--method", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
        self.assertTrue(self.tourner(lambda: p.poll() is not None, 10.0), "gdbus a répondu")
        sortie, erreur = p.communicate()
        self.assertEqual(p.returncode, 0, erreur)
        return sortie.strip()

    def changes(self, propriete):
        return [s[2][1][propriete] for s in self.signaux
                if s[1] == "PropertiesChanged" and propriete in s[2][1]]

    def test_proprietes_lues(self):
        self.assertEqual(self.gdbus("org.freedesktop.DBus.Properties.Get", mpris.RACINE, "Identity"),
                         "(<'Voix-là (essai)'>,)")
        self.assertEqual(self.gdbus("org.freedesktop.DBus.Properties.Get", mpris.RACINE, "DesktopEntry"),
                         f"(<'{APP_ID}'>,)")
        self.assertIn("'Paused'", self.gdbus("org.freedesktop.DBus.Properties.Get", mpris.LECTEUR,
                                             "PlaybackStatus"))
        tout = self.gdbus("org.freedesktop.DBus.Properties.GetAll", mpris.LECTEUR)
        for attendu in ("'xesam:title': <'Chanson fictive'>", "'mpris:length': <int64 200000000>",
                        "'CanGoNext': <false>", "'Position': <int64 12000000>"):
            self.assertIn(attendu, tout)

    def test_play_pause_et_properties_changed(self):
        self.gdbus(f"{mpris.LECTEUR}.Play")
        self.assertTrue(self.tourner(lambda: "Playing" in self.changes("PlaybackStatus")))
        self.gdbus(f"{mpris.LECTEUR}.Pause")
        self.assertTrue(self.tourner(lambda: self.changes("PlaybackStatus")[-1:] == ["Paused"]))
        self.gdbus(f"{mpris.LECTEUR}.PlayPause")
        self.assertTrue(self.tourner(lambda: self.changes("PlaybackStatus")[-1:] == ["Playing"]))
        self.assertEqual(self.pilote.appels, ["lire", "pause", "lecture_pause"])
        self.assertTrue(all(s[2][0] == mpris.LECTEUR for s in self.signaux if s[1] == "PropertiesChanged"))

    def test_raise_et_quit(self):
        self.gdbus(f"{mpris.RACINE}.Raise")
        self.assertEqual(self.pilote.appels, ["relever"])
        self.gdbus(f"{mpris.RACINE}.Quit")
        self.assertTrue(self.tourner(lambda: "quitter" in self.pilote.appels), "Quit répond, puis quitte")

    def test_stop(self):
        self.gdbus(f"{mpris.LECTEUR}.Stop")
        self.assertTrue(self.tourner(lambda: "Stopped" in self.changes("PlaybackStatus")))
        self.assertIn("arreter", self.pilote.appels)

    def test_seek_et_seeked(self):
        self.gdbus(f"{mpris.LECTEUR}.Seek", "30000000")
        self.assertTrue(self.tourner(lambda: any(s[1] == "Seeked" for s in self.signaux)))
        self.assertEqual(self.pilote.appels, [("placer", 42.0)])
        self.assertEqual([s[2] for s in self.signaux if s[1] == "Seeked"], [(42_000_000,)])
        self.gdbus(f"{mpris.LECTEUR}.Seek", "--", "-100000000")
        self.assertEqual(self.pilote.appels[-1], ("placer", 0.0), "pas avant le début")
        self.gdbus(f"{mpris.LECTEUR}.Seek", "900000000")
        self.assertEqual(self.pilote.appels[-1], ("placer", 200.0), "au-delà de la fin : à la fin")

    def test_set_position(self):
        piste = mpris.identifiant_piste(self.pilote.e.cle)
        self.gdbus(f"{mpris.LECTEUR}.SetPosition", "/autre/piste", "5000000")
        self.assertEqual(self.pilote.appels, [], "une autre piste : ignoré, comme le veut MPRIS")
        self.gdbus(f"{mpris.LECTEUR}.SetPosition", piste, "5000000")
        self.assertEqual(self.pilote.appels, [("placer", 5.0)])
        self.gdbus(f"{mpris.LECTEUR}.SetPosition", piste, "500000000")
        self.assertEqual(self.pilote.appels, [("placer", 5.0)], "au-delà de la durée : ignoré")

    def test_volume(self):
        self.gdbus("org.freedesktop.DBus.Properties.Set", mpris.LECTEUR, "Volume", "<0.5>")
        self.assertEqual(self.pilote.appels, [("volume", 0.5)])
        self.assertTrue(self.tourner(lambda: 0.5 in self.changes("Volume")))

    def test_actualiser_ne_signale_que_les_changements(self):
        self.assertEqual(self.serveur.actualiser(), {})
        self.pilote.e.position_s = 50.0
        self.assertEqual(self.serveur.actualiser(), {}, "la position ne se signale pas")
        self.pilote.e.piste = "Beat seul"
        d = self.serveur.actualiser()
        self.assertEqual(set(d.get(mpris.LECTEUR, {})), {"Metadata"})
        self.assertTrue(self.tourner(lambda: self.changes("Metadata")))
        self.assertEqual(self.changes("Metadata")[-1]["xesam:artist"], ["voix Jules · Beat seul"])

    def test_retirer_rend_le_nom(self):
        def a_le_nom():
            r = self.client.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                      "NameHasOwner", GLib.Variant("(s)", (self.NOM,)), None,
                                      Gio.DBusCallFlags.NONE, 2000, None)
            return r.unpack()[0]
        self.assertTrue(a_le_nom())
        self.serveur.retirer()
        self.assertTrue(self.tourner(lambda: not a_le_nom()))
        self.assertFalse(self.serveur.publie)
        self.serveur.publier()
        self.assertTrue(self.tourner(a_le_nom), "republié pour la session suivante")


if __name__ == "__main__":
    unittest.main()
