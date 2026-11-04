"""MPRIS : la lecture en cours visible et pilotable depuis le bureau (menu du haut de GNOME, touches multimédia), par
org.mpris.MediaPlayer2 et org.mpris.MediaPlayer2.Player sur le bus de session. Gio seul (Gio.DBusConnection,
register_object), sans GTK ni dépendance nouvelle : testable sur un bus privé.

Le serveur ne sait rien de l'interface : il lit l'état (voixla.lecture.Etat) et transmet les commandes à un « pilote »,
qui a :
  etat()                          → voixla.lecture.Etat
  lire(), pause(), lecture_pause(), arreter()
  deplacer(delta_s), placer(position_s)
  relever()                       (Raise : rouvrir la fenêtre)
  quitter()
  regler_volume(v)                (facultatif)
Pas de file : Next et Previous ne font rien (CanGoNext et CanGoPrevious sont faux).

Nom de bus : org.mpris.MediaPlayer2.voixla ; pour les essais (VOIXLA_APP_ID défini), org.mpris.MediaPlayer2.voixla.
essai<pid>, avec l'identité « Voix-là (essai) » : un essai n'apparaît jamais comme le vrai lecteur.
"""

import hashlib
import os

from gi.repository import Gio, GLib

from . import APP_ID, NOM
from . import lecture as regles

CHEMIN = "/org/mpris/MediaPlayer2"
RACINE = "org.mpris.MediaPlayer2"
LECTEUR = "org.mpris.MediaPlayer2.Player"
PROPRIETES = "org.freedesktop.DBus.Properties"
NOM_BUS = "org.mpris.MediaPlayer2.voixla"
PAS_DE_PISTE = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
PREFIXE_PISTE = "/io/mawena/VoixLa/piste/"

XML = """<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition">
      <arg direction="in" name="TrackId" type="o"/>
      <arg direction="in" name="Position" type="x"/>
    </method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>"""

_INFO = Gio.DBusNodeInfo.new_for_xml(XML)
INTERFACES = {i.name: i for i in _INFO.interfaces}


def essai(env=None):
    return bool((os.environ if env is None else env).get("VOIXLA_APP_ID"))


def nom_bus(env=None, pid=None):
    """org.mpris.MediaPlayer2.voixla, ou …voixla.essai<pid> pour un essai (VOIXLA_APP_ID défini)."""
    if essai(env):
        return f"{NOM_BUS}.essai{os.getpid() if pid is None else pid}"
    return NOM_BUS


def identite(env=None):
    return f"{NOM} (essai)" if essai(env) else NOM


def identifiant_piste(cle):
    """Chemin d'objet D-Bus stable pour un morceau (cle de voixla.lecture.Etat)."""
    if not cle:
        return PAS_DE_PISTE
    return PREFIXE_PISTE + hashlib.sha1(repr(tuple(cle)).encode()).hexdigest()[:20]


def us(secondes):
    return int(round(max(0.0, float(secondes or 0.0)) * 1_000_000))


def uri_fichier(chemin):
    try:
        return GLib.filename_to_uri(str(chemin), None)
    except (GLib.Error, TypeError):
        return None


def metadonnees(etat):
    """Metadata MPRIS (dict de GLib.Variant) : identifiant, titre, voix et piste (en « artiste », ce que GNOME
    affiche sous le titre), durée, image de la vidéo en cache ; et voixla:voix, voixla:piste à part."""
    m = {"mpris:trackid": GLib.Variant("o", identifiant_piste(etat.cle if etat.session else ()))}
    if not etat.session:
        return m
    m["xesam:title"] = GLib.Variant("s", etat.titre or NOM)
    detail = regles.ligne_detail(etat)
    if detail:
        m["xesam:artist"] = GLib.Variant("as", [detail])
    m["xesam:album"] = GLib.Variant("s", NOM)
    if etat.duree_s:
        m["mpris:length"] = GLib.Variant("x", us(etat.duree_s))
    if etat.image:
        uri = uri_fichier(etat.image)
        if uri:
            m["mpris:artUrl"] = GLib.Variant("s", uri)
    if etat.voix:
        m["voixla:voix"] = GLib.Variant("s", etat.voix)
    if etat.piste:
        m["voixla:piste"] = GLib.Variant("s", etat.piste)
    return m


def proprietes(etat, nom=None):
    """{interface: {propriété: GLib.Variant}} pour cet état (Position comprise)."""
    session = etat.session
    return {
        RACINE: {
            "CanQuit": GLib.Variant("b", True),
            "CanRaise": GLib.Variant("b", True),
            "HasTrackList": GLib.Variant("b", False),
            "Identity": GLib.Variant("s", nom or identite()),
            "DesktopEntry": GLib.Variant("s", APP_ID),
            "SupportedUriSchemes": GLib.Variant("as", []),
            "SupportedMimeTypes": GLib.Variant("as", []),
        },
        LECTEUR: {
            "PlaybackStatus": GLib.Variant("s", etat.statut if session else regles.ARRET),
            "Rate": GLib.Variant("d", 1.0),
            "Metadata": GLib.Variant("a{sv}", metadonnees(etat)),
            "Volume": GLib.Variant("d", float(etat.volume)),
            "Position": GLib.Variant("x", us(etat.position_s) if session else 0),
            "MinimumRate": GLib.Variant("d", 1.0),
            "MaximumRate": GLib.Variant("d", 1.0),
            "CanGoNext": GLib.Variant("b", False),
            "CanGoPrevious": GLib.Variant("b", False),
            "CanPlay": GLib.Variant("b", session),
            "CanPause": GLib.Variant("b", session),
            "CanSeek": GLib.Variant("b", bool(session and etat.duree_s)),
            "CanControl": GLib.Variant("b", True),
        },
    }


def changements(avant, apres):
    """{interface: {propriété: valeur}} de ce qui a changé de avant à apres (sorties de proprietes()), sans Position :
    MPRIS ne la signale pas, les clients l'extrapolent (et Seeked dit les sauts)."""
    r = {}
    for iface, props in apres.items():
        anciennes = (avant or {}).get(iface, {})
        diff = {k: v for k, v in props.items()
                if k != "Position" and (k not in anciennes or not anciennes[k].equal(v))}
        if diff:
            r[iface] = diff
    return r


class ServeurMpris:
    """Les deux interfaces MPRIS sur une connexion D-Bus (celle de l'application, ou un bus privé dans les essais).
    publier() enregistre les objets et prend le nom ; actualiser() émet PropertiesChanged pour ce qui a changé ;
    signaler_saut() émet Seeked ; retirer() rend tout."""

    def __init__(self, connexion, pilote, nom=None, identite_=None):
        self.connexion = connexion
        self.pilote = pilote
        self.nom = nom or nom_bus()
        self.identite = identite_ or identite()
        self._enregistrements = []
        self._nom_id = None
        self._dernier = None
        self.nom_pris = None        # None tant qu'on ne sait pas ; True ou False ensuite

    @property
    def publie(self):
        return bool(self._enregistrements)

    def _etat(self):
        try:
            return self.pilote.etat()
        except Exception:  # noqa: BLE001 — un état illisible ne doit pas casser le bus
            import traceback
            traceback.print_exc()
            return regles.Etat()

    def publier(self):
        if self.publie:
            return
        for iface in (RACINE, LECTEUR):
            self._enregistrements.append(self.connexion.register_object(
                CHEMIN, INTERFACES[iface], self._methode, self._lire_propriete, self._ecrire_propriete))
        self._dernier = proprietes(self._etat(), self.identite)
        self._nom_id = Gio.bus_own_name_on_connection(
            self.connexion, self.nom, Gio.BusNameOwnerFlags.NONE,
            lambda *_: setattr(self, "nom_pris", True), lambda *_: setattr(self, "nom_pris", False))

    def retirer(self):
        if self._nom_id is not None:
            Gio.bus_unown_name(self._nom_id)
            self._nom_id = None
        for ident in self._enregistrements:
            try:
                self.connexion.unregister_object(ident)
            except Exception:  # noqa: BLE001
                pass
        self._enregistrements = []
        self._dernier = None
        self.nom_pris = None

    def actualiser(self):
        """Émet PropertiesChanged pour chaque interface dont une propriété a changé ; renvoie ces changements."""
        if not self.publie:
            return {}
        nouveau = proprietes(self._etat(), self.identite)
        diff = changements(self._dernier, nouveau)
        self._dernier = nouveau
        for iface, props in diff.items():
            self.connexion.emit_signal(None, CHEMIN, PROPRIETES, "PropertiesChanged",
                                       GLib.Variant("(sa{sv}as)", (iface, props, [])))
        return diff

    def signaler_saut(self, position_s):
        if self.publie:
            self.connexion.emit_signal(None, CHEMIN, LECTEUR, "Seeked", GLib.Variant("(x)", (us(position_s),)))

    # --- appels D-Bus ----------------------------------------------------------------------------------------------

    def _lire_propriete(self, _connexion, _envoyeur, _chemin, iface, nom):
        props = proprietes(self._etat(), self.identite).get(iface, {})
        return props.get(nom)

    def _ecrire_propriete(self, _connexion, _envoyeur, _chemin, iface, nom, valeur):
        if iface == LECTEUR and nom == "Volume":
            regler = getattr(self.pilote, "regler_volume", None)
            if regler:
                regler(max(0.0, min(1.0, valeur.get_double())))
                GLib.idle_add(lambda: (self.actualiser(), False)[1])
            return True
        return True     # Rate : seul 1,0 est possible, on ignore

    def _methode(self, _connexion, _envoyeur, _chemin, iface, nom, parametres, invocation):
        p = self.pilote
        try:
            if iface == RACINE and nom == "Raise":
                p.relever()
            elif iface == RACINE and nom == "Quit":
                # répondre d'abord : l'application quitte juste après
                invocation.return_value(None)
                GLib.idle_add(lambda: (p.quitter(), False)[1])
                return
            elif iface == LECTEUR and nom == "Play":
                p.lire()
            elif iface == LECTEUR and nom == "Pause":
                p.pause()
            elif iface == LECTEUR and nom == "PlayPause":
                p.lecture_pause()
            elif iface == LECTEUR and nom == "Stop":
                p.arreter()
            elif iface == LECTEUR and nom == "Seek":
                (delta,) = parametres.unpack()
                e = self._etat()
                if e.session and e.duree_s:
                    # au-delà de la fin : à la fin (pas de piste suivante), la lecture s'y termine d'elle-même
                    cible = max(0.0, min(e.duree_s, e.position_s + delta / 1_000_000))
                    p.placer(cible)
                    self.signaler_saut(cible)
            elif iface == LECTEUR and nom == "SetPosition":
                piste, position = parametres.unpack()
                e = self._etat()
                s = position / 1_000_000
                if e.session and piste == identifiant_piste(e.cle) and 0 <= s <= (e.duree_s or 0):
                    p.placer(s)
                    self.signaler_saut(s)
            elif iface == LECTEUR and nom == "OpenUri":
                invocation.return_dbus_error("org.mpris.MediaPlayer2.Player.Error.NotSupported",
                                             "Voix-là n’ouvre pas d’adresse")
                return
            # Next, Previous : pas de file
        except Exception as e:  # noqa: BLE001
            invocation.return_dbus_error("org.mpris.MediaPlayer2.Error.Failed", str(e))
            return
        invocation.return_value(None)
        GLib.idle_add(lambda: (self.actualiser(), False)[1])
