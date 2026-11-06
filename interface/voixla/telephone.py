"""Enregistrement au micro d'un téléphone Android (sans GTK), comme « enregistrer-chant.sh pixel » : scrcpy enregistre
le micro du téléphone en FLAC sans perte, sans image ni son sur le PC (--no-video --no-playback --no-window
--audio-source=mic-voice-recognition --audio-codec=flac --record=… --time-limit=…), puis ffmpeg le convertit en WAV
mono 48 kHz : 24 bits en chant, 16 bits en parole, comme l'enregistrement au micro du PC.

Téléphones : « adb devices -l » (état « device »), celui de ADB_SERIAL en premier, branchés en USB (débogage USB) ou
sans fil (« adb connect <adresse IP>:5555 »). Arrêt avant la fin : SIGINT au groupe de scrcpy, comme Ctrl+C dans le
script, pour qu'il termine le fichier.

Commandes : VOIXLA_SCRCPY (sinon scrcpy du PATH, sinon ~/.local/bin/scrcpy) et VOIXLA_ADB (sinon ADB, la variable de
scrcpy, sinon l'adb livré à côté de scrcpy, sinon celui du PATH) ; scrcpy reçoit le même adb (variable ADB), pour un
seul serveur adb. Seules les options que le scrcpy installé connaît sont passées (lues dans « scrcpy --help »).
VOIXLA_TELEPHONE_ESSAI=1 imite un téléphone (son de synthèse par ffmpeg, sans adb ni scrcpy) ; « aucun » : aucun
téléphone. Tout ici est lent ou peut l'être : à appeler hors du fil de l'interface (sauf Prise.arreter et
Prise.abandonner, qui n'envoient qu'un signal).
"""

import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import controle

PREFIXE = "telephone:"          # clé d'une source « téléphone » dans l'interface : telephone:<série adb>
OPTIONS_REQUISES = ("--serial", "--no-video", "--no-playback", "--no-window", "--audio-source", "--audio-codec",
                    "--record", "--time-limit")
SOURCE_AUDIO = "mic-voice-recognition"   # comme le script ; « mic » si ce scrcpy ne la connaît pas
ADRESSE = re.compile(r"^[A-Za-z0-9.-]+:\d{1,5}$")   # appareil sans fil à joindre par « adb connect »
ETATS = {
    "unauthorized": ("Téléphone non autorisé",
                     "Acceptez « Autoriser le débogage USB » sur l’écran du téléphone, puis Réessayer."),
    "authorizing": ("Autorisation en cours", "Acceptez le débogage USB sur l’écran du téléphone, puis Réessayer."),
    "offline": ("Téléphone hors ligne",
                "Débranchez-le et rebranchez-le (sans fil : « adb connect » à nouveau), puis Réessayer."),
    "no permissions": ("Téléphone inaccessible",
                       "Le PC n’a pas les droits USB sur ce téléphone (règles udev, paquet android-sdk-platform-tools-"
                       "common), puis Réessayer."),
}
AUCUN = ("Aucun téléphone connecté",
         "Branchez-le en USB (débogage USB activé dans les options pour les développeurs), ou sans fil : "
         "« adb connect <adresse IP>:5555 » (ou l’adresse ci-dessous), puis Réessayer.")


class TelephoneIndisponible(Exception):
    """scrcpy ou adb manquant, ou scrcpy trop ancien : le message dit quoi faire."""


@dataclass(frozen=True)
class Appareil:
    serie: str
    etat: str = "device"
    modele: str = ""        # « Pixel 7 » d'après model:Pixel_7

    @property
    def nom(self):
        return self.modele or self.serie

    @property
    def sans_fil(self):
        return ":" in self.serie or "._adb-tls-connect." in self.serie

    @property
    def cle(self):
        return PREFIXE + self.serie


@dataclass
class Recherche:
    """Résultat d'une recherche : téléphones prêts (ADB_SERIAL en premier) ; sinon titre et message de ce qui manque."""
    telephones: list = field(default_factory=list)
    titre: str = ""
    message: str = ""
    autres: list = field(default_factory=list)      # appareils vus mais pas prêts (non autorisés, hors ligne…)


def essai():
    """Valeur de VOIXLA_TELEPHONE_ESSAI : '' (vrai téléphone), 'aucun' ou '1' (téléphone imité)."""
    v = (os.environ.get("VOIXLA_TELEPHONE_ESSAI") or "").strip().lower()
    return "" if v in ("", "0", "non") else ("aucun" if v == "aucun" else "1")


def serie_preferee():
    return (os.environ.get("ADB_SERIAL") or "").strip()


def nom_modele(modele):
    """« Pixel_7 » → « Pixel 7 »."""
    return " ".join((modele or "").replace("_", " ").split())


def lire_appareils(texte):
    """Appareils de la sortie de « adb devices -l » (lignes « * daemon … » et en-tête ignorées)."""
    appareils = []
    for ligne in (texte or "").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("*") or ligne.lower().startswith("list of devices"):
            continue
        morceaux = ligne.split()
        if len(morceaux) < 2:
            continue
        serie, etat, reste = morceaux[0], morceaux[1], morceaux[2:]
        if etat == "no" and reste[:1] == ["permissions"]:
            etat = "no permissions"
        attributs = dict(m.split(":", 1) for m in reste if ":" in m and not m.startswith(("(", "[")))
        appareils.append(Appareil(serie, etat, nom_modele(attributs.get("model", ""))))
    return appareils


def trier(appareils, prefere=None):
    """ADB_SERIAL (ou « prefere ») en premier, l'ordre d'adb ensuite."""
    prefere = serie_preferee() if prefere is None else prefere
    return sorted(appareils, key=lambda a: a.serie != prefere)


def libelle(appareil, tous=()):
    """Nom dans la liste des micros : « Téléphone Pixel 7 (USB) », « Téléphone Pixel 7 (sans fil, 192.168.0.104) »."""
    if appareil.serie.startswith("essai"):
        return f"Téléphone {appareil.nom} (essai)"
    if not appareil.sans_fil:
        liaison = "USB"
    elif ADRESSE.match(appareil.serie):
        liaison = "sans fil, " + appareil.serie.rsplit(":", 1)[0]
    else:
        liaison = "sans fil"
    texte = f"Téléphone {appareil.nom} ({liaison})"
    if appareil.modele and sum(1 for a in tous if a.modele == appareil.modele and a.sans_fil == appareil.sans_fil) > 1:
        texte = f"Téléphone {appareil.nom} ({liaison}, {appareil.serie})"
    return texte


# --- commandes -----------------------------------------------------------------------------------------------------

def _executable(chemin):
    if not chemin:
        return None
    p = Path(chemin).expanduser()
    return str(p) if p.is_file() and os.access(p, os.X_OK) else None


def scrcpy():
    """Chemin de scrcpy, ou None."""
    if os.environ.get("VOIXLA_SCRCPY"):
        return _executable(os.environ["VOIXLA_SCRCPY"])
    return shutil.which("scrcpy") or _executable(Path.home() / ".local" / "bin" / "scrcpy")


def adb(chemin_scrcpy=None):
    """Chemin d'adb : celui que scrcpy prendra (un seul serveur adb pour les deux), ou None."""
    if os.environ.get("VOIXLA_ADB"):
        return _executable(os.environ["VOIXLA_ADB"])
    if os.environ.get("ADB"):
        return _executable(os.environ["ADB"]) or shutil.which(os.environ["ADB"])
    s = chemin_scrcpy or scrcpy()
    if s:
        voisin = _executable(Path(s).resolve().parent / "adb")
        if voisin:
            return voisin
    return shutil.which("adb")


_options = {}


def options_scrcpy(chemin):
    """(version, options et valeurs connues) de ce scrcpy, d'après --version et --help (gardé par chemin et date)."""
    try:
        cle = (chemin, os.stat(chemin).st_mtime_ns)
    except OSError:
        return "", frozenset()
    if cle not in _options:
        try:
            aide = subprocess.run([chemin, "--help"], capture_output=True, text=True, timeout=15)
            texte = (aide.stdout or "") + (aide.stderr or "")
            v = subprocess.run([chemin, "--version"], capture_output=True, text=True, timeout=15)
            version = re.search(r"scrcpy\s+v?([0-9][0-9.]*)", (v.stdout or "") + (v.stderr or ""))
        except (OSError, subprocess.SubprocessError):
            return "", frozenset()
        jetons = set(re.findall(r"--[a-z][a-z0-9-]*", texte)) | set(re.findall(r'"([a-z][a-z0-9-]*)"', texte))
        _options[cle] = (version.group(1) if version else "", frozenset(jetons))
    return _options[cle]


def verifier_scrcpy():
    """(chemin de scrcpy, options) ; TelephoneIndisponible si scrcpy manque ou ne connaît pas les options requises."""
    chemin = scrcpy()
    if not chemin:
        raise TelephoneIndisponible("scrcpy introuvable", "Installez scrcpy (github.com/Genymobile/scrcpy) pour "
                                    "enregistrer au micro du téléphone, puis Réessayer.")
    version, options = options_scrcpy(chemin)
    if not options:
        raise TelephoneIndisponible("scrcpy ne répond pas", f"« {chemin} --help » n’a rien donné.")
    manque = [o for o in OPTIONS_REQUISES if o not in options]
    if manque:
        raise TelephoneIndisponible("scrcpy trop ancien", f"scrcpy {version or '?'} ne connaît pas "
                                    f"{', '.join(manque)} : mettez-le à jour.")
    return chemin, options


def commande_enregistrement(chemin_scrcpy, options, serie, flac, duree):
    """La commande scrcpy d'une prise (comme le script), avec les seules options que ce scrcpy connaît."""
    source = SOURCE_AUDIO if SOURCE_AUDIO in options else "mic"
    cmd = [chemin_scrcpy, "-s", serie, "--no-video", "--no-playback", "--no-window", f"--audio-source={source}",
           "--audio-codec=flac"]
    if "--require-audio" in options:
        cmd.append("--require-audio")      # échec net si le micro ne s'ouvre pas, plutôt qu'une prise vide
    return cmd + [f"--record={flac}", f"--time-limit={max(1, int(round(duree)))}"]


def commande_essai(flac, duree):
    """Prise imitée (VOIXLA_TELEPHONE_ESSAI) : un son de synthèse en temps réel, FLAC stéréo 48 kHz comme scrcpy ;
    ffmpeg termine aussi son fichier sur SIGINT."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-re", "-f", "lavfi", "-i",
            "sine=frequency=220:sample_rate=48000", "-t", str(max(1, int(round(duree)))), "-ac", "2", "-c:a", "flac",
            str(flac)]


def commande_conversion(flac, wav, bits):
    """FLAC du téléphone → WAV mono 48 kHz, 24 bits (chant) ou 16 bits (parole), comme le script."""
    codec = "pcm_s24le" if bits == 24 else "pcm_s16le"
    return ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", str(flac), "-ac", "1",
            "-ar", "48000", "-c:a", codec, str(wav)]


# --- recherche ----------------------------------------------------------------------------------------------------

def connecter_sans_fil(adresses, chemin_adb=None):
    """« adb connect » pour chaque adresse IP:port (les séries USB sont ignorées) : [(adresse, réussi, sortie)]."""
    chemin_adb = chemin_adb or adb()
    faits, vus = [], set()
    for a in adresses:
        a = (a or "").strip()
        if not chemin_adb or not ADRESSE.match(a) or a in vus:
            continue
        vus.add(a)
        try:
            r = subprocess.run([chemin_adb, "connect", a], capture_output=True, text=True, timeout=15)
            sortie = " ".join(((r.stdout or "") + (r.stderr or "")).split())
            ok = r.returncode == 0 and "connected to" in sortie and "cannot" not in sortie and "failed" not in sortie
        except (OSError, subprocess.SubprocessError) as e:
            sortie, ok = str(e), False
        faits.append((a, ok, sortie))
    return faits


def rechercher(reconnecter=()):
    """Les téléphones prêts à enregistrer, après « adb connect » des adresses sans fil de « reconnecter » (lent : adb
    peut démarrer son serveur, et « adb connect » attendre le réseau)."""
    mode = essai()
    if mode == "aucun":
        return Recherche([], *AUCUN)
    if mode:
        return Recherche([Appareil("essai-pixel-7", "device", "Pixel 7")])
    try:
        chemin_scrcpy, _ = verifier_scrcpy()
    except TelephoneIndisponible as e:
        return Recherche([], *e.args)
    chemin_adb = adb(chemin_scrcpy)
    if not chemin_adb:
        return Recherche([], "adb introuvable", "Installez adb (paquet adb), ou scrcpy avec son adb, puis Réessayer.")
    echecs = [f"« adb connect {a} » : {s or 'échec'}" for a, ok, s in connecter_sans_fil(reconnecter, chemin_adb)
              if not ok]
    try:
        r = subprocess.run([chemin_adb, "devices", "-l"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return Recherche([], "adb ne répond pas", str(e))
    if r.returncode != 0:
        return Recherche([], "adb ne répond pas", " ".join((r.stderr or r.stdout or "").split())[:300])
    appareils = lire_appareils(r.stdout)
    prets = trier([a for a in appareils if a.etat == "device"])
    autres = [a for a in appareils if a.etat != "device"]
    if prets:
        return Recherche(prets, autres=autres)
    if autres:
        titre, message = ETATS.get(autres[0].etat, (f"Téléphone {autres[0].etat}", "Réessayez une fois prêt."))
        message = f"{autres[0].nom} : {message}"
    else:
        titre, message = AUCUN
    if echecs:
        message += "\n" + "\n".join(echecs)
    return Recherche([], titre, message, autres)


# --- une prise -----------------------------------------------------------------------------------------------------

def _effacer(*fichiers):
    for f in fichiers:
        try:
            Path(f).unlink()
        except OSError:
            pass


def erreur_scrcpy(sortie):
    """Les lignes « ERROR: … » de scrcpy (la cause d'un échec), sans le préfixe."""
    lignes = [l.split(":", 1)[1].strip() for l in (sortie or "").splitlines() if l.startswith("ERROR:")]
    return " ; ".join(dict.fromkeys(l for l in lignes if l))[:300]


class Prise:
    """Une prise au micro du téléphone vers un WAV : executer() bloque jusqu'au bout (enregistrement puis conversion,
    dans un fil à part) ; arreter() et abandonner() peuvent venir d'un autre fil. Le FLAC passe par un fichier caché
    à côté du WAV, effacé après la conversion (gardé si elle échoue, comme le script)."""

    DEMARRAGE_S = 30     # scrcpy s'arrête seul à --time-limit ; au-delà de durée + DEMARRAGE_S, il reçoit SIGINT
    MARGE_ARRET_S = 10   # après SIGINT, le temps de finir le fichier ; ensuite SIGKILL

    def __init__(self, serie, wav, duree, bits=24):
        self.serie, self.wav, self.duree, self.bits = serie, Path(wav), duree, bits
        self.flac = self.wav.with_name(f".{self.wav.stem}-telephone.flac")
        self.processus = None
        self.sortie = ""
        self._verrou = threading.Lock()
        self._arret = self._abandon = False
        self._sigint_a = None

    def commande(self):
        """(commande, environnement) ; TelephoneIndisponible si scrcpy ou adb manque."""
        if essai():
            return commande_essai(self.flac, self.duree), None
        chemin_scrcpy, options = verifier_scrcpy()
        env = dict(os.environ)
        chemin_adb = adb(chemin_scrcpy)
        if chemin_adb:
            env["ADB"] = chemin_adb
        return commande_enregistrement(chemin_scrcpy, options, self.serie, self.flac, self.duree), env

    def arreter(self):
        """Fin de la prise avant la durée prévue : SIGINT, comme Ctrl+C (scrcpy termine alors le fichier)."""
        with self._verrou:
            self._arret = True
            self._signal(signal.SIGINT)

    def abandonner(self):
        """Prise abandonnée (fenêtre fermée) : arrêt, puis fichiers effacés par executer()."""
        with self._verrou:
            self._abandon = self._arret = True
            self._signal(signal.SIGINT)

    def _signal(self, sig):
        p = self.processus
        if p is None or p.returncode is not None:
            return
        if sig == signal.SIGINT:
            if self._sigint_a is not None:
                return
            self._sigint_a = time.monotonic()
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def executer(self):
        """Enregistre puis convertit : None si la prise est là (ou abandonnée), sinon le message d'erreur."""
        self.wav.parent.mkdir(parents=True, exist_ok=True)
        _effacer(self.flac)
        try:
            cmd, env = self.commande()
        except TelephoneIndisponible as e:
            return f"{e.args[0]} — {e.args[1]}" if len(e.args) > 1 else str(e)
        with self._verrou:
            if self._abandon:
                return None
            if self._arret:
                return "prise arrêtée avant de commencer"
            try:
                self.processus = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                  stderr=subprocess.STDOUT, text=True, errors="replace", env=env,
                                                  start_new_session=True)
            except OSError as e:
                return f"{Path(cmd[0]).name} n’a pas pu démarrer : {e}"
        self.sortie = self._attendre(self.processus)
        if self._abandon:
            _effacer(self.flac, self.wav)
            return None
        return self._convertir()

    def _attendre(self, p):
        limite = time.monotonic() + self.duree + self.DEMARRAGE_S
        morceaux = []
        while True:
            try:
                sortie, _ = p.communicate(timeout=0.5)
                morceaux.append(sortie or "")
                return "".join(morceaux)
            except subprocess.TimeoutExpired:
                maintenant = time.monotonic()
                with self._verrou:
                    if self._sigint_a is None and maintenant > limite:
                        self._signal(signal.SIGINT)
                    elif self._sigint_a is not None and maintenant > self._sigint_a + self.MARGE_ARRET_S:
                        self._signal(signal.SIGKILL)

    def _convertir(self):
        rien = f"le téléphone n’a rien enregistré (téléphone connecté ? {self.serie})"
        detail = erreur_scrcpy(self.sortie)
        try:
            vide = self.flac.stat().st_size == 0
        except OSError:
            vide = True
        if vide:
            _effacer(self.flac, self.wav)
            return rien + (f" — scrcpy : {detail}" if detail else "")
        try:
            r = subprocess.run(commande_conversion(self.flac, self.wav, self.bits), capture_output=True, text=True,
                               timeout=300)
            ok, message = r.returncode == 0, " ".join((r.stderr or "").split())[:300]
        except (OSError, subprocess.SubprocessError) as e:
            ok, message = False, str(e)
        if not ok:
            _effacer(self.wav)
            return f"conversion du FLAC impossible ({message or 'ffmpeg'}) ; la prise reste dans {self.flac}"
        d = controle.duree(self.wav)
        if not d or d < 0.1:
            _effacer(self.flac, self.wav)
            return rien + (f" — scrcpy : {detail}" if detail else "")
        _effacer(self.flac)
        if self._abandon:
            _effacer(self.wav)
        return None
