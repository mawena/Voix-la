"""Analyses du son pour les visualisations (sans GTK, testable) : mélodie (F0), spectrogramme, forme d'onde, niveaux
par bande pour le vu-mètre. Le son est décodé par ffmpeg (mono 16 kHz, par blocs : mémoire bornée même pour un film)
et analysé avec numpy ; les résultats vont en cache.

- Courbes de mélodie : travail/<voix>/courbes.json, écrit par doubler (format 1 : pas_s, source_hz, convertie_hz,
  decalage_dt, chœurs_hz facultatif ; 0 = non voisé ; temps 0 = début de l'audio doublé). Pour un ancien doublage,
  repli local : YIN sur travail/voix_source.* et travail/<voix>/voix_convertie.flac, résultat dans le même format dans
  travail/<voix>/courbes-locales.json (avec la signature des fichiers analysés, pour savoir s'il est à jour).
- Spectrogramme, forme d'onde, bandes : un fichier .npz par piste et par produit dans ~/.cache/voix-la/pistes/, nommé
  d'après le chemin, la taille et la date du fichier (un fichier refait est réanalysé).
En ligne de commande (lancé par l'interface sous nice, ou par file-attente quand c'est long) :
  python3 -m voixla.analyse pistes FICHIER… [--produits spectre,onde,bandes]
  python3 -m voixla.analyse courbes DOSSIER_VOIX
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import numpy as np

from . import chemins

VERSION = 2          # 2 : trames datées au milieu de leur fenêtre
SR = 16000
CACHE_MAX_MO = 500         # au-delà, les analyses les moins récemment utilisées sont effacées
BLOC_S = 30
# mélodie
PAS_F0_S = 0.02
F0_MIN, F0_MAX = 60.0, 1000.0
# spectrogramme et bandes
N_FFT = 1024
FPS_SPECTRE = 40                  # une colonne toutes les 25 ms
LIGNES_SPECTRE = 160              # échelle de fréquence logarithmique
F_BAS, F_HAUT = 50.0, 8000.0
NB_BANDES = 24
FPS_BANDES = 20
# forme d'onde
FPS_ONDE = 100
# échelle des niveaux stockés : −90..0 dBFS → 0..255
PLANCHER_DB = -90.0
PRODUITS = ("spectre", "onde", "bandes", "f0")


class ErreurAnalyse(RuntimeError):
    pass


# --- Décodage par blocs ----------------------------------------------------------------------------------------------

def decoder_blocs(fichier, sr=SR, bloc_s=BLOC_S):
    """Son mono à sr Hz, float32, bloc par bloc (ffmpeg en flux : la mémoire ne dépend pas de la durée)."""
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-i", str(fichier), "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    taille = sr * bloc_s * 4
    reste = b""
    fini = False
    try:
        while True:
            brut = p.stdout.read(taille)
            if not brut:
                break
            brut = reste + brut
            coupe = len(brut) // 4 * 4
            reste = brut[coupe:]
            if coupe:
                yield np.frombuffer(brut[:coupe], np.float32)
        fini = True
    finally:
        if not fini:
            p.kill()
        p.stdout.close()
        erreur = p.stderr.read().decode(errors="replace")
        p.stderr.close()
        code = p.wait()
    if code != 0:
        raise ErreurAnalyse(f"son illisible : {fichier} ({erreur.strip()[-300:] or code})")


def decoder(fichier, sr=SR):
    blocs = list(decoder_blocs(fichier, sr))
    return np.concatenate(blocs) if blocs else np.zeros(0, np.float32)


class Trameur:
    """Découpe un flux en trames de L échantillons ; la trame k commence à l'échantillon k × pas."""

    def __init__(self, longueur, pas):
        self.longueur, self.pas = int(longueur), int(pas)
        self.tampon = np.zeros(0, np.float32)
        self.total = 0          # échantillons reçus
        self.rendues = 0        # trames déjà rendues
        self.sauter = 0         # échantillons à sauter au début du bloc suivant (pas plus grand que la trame)

    def pousser(self, bloc, fin=False):
        vide = np.zeros((0, self.longueur), np.float32)
        self.total += len(bloc)
        b = np.concatenate([self.tampon, bloc]) if len(self.tampon) else np.asarray(bloc, np.float32)
        if self.sauter:
            d = min(self.sauter, len(b))
            b, self.sauter = b[d:], self.sauter - d
        restantes = None
        if fin:
            # trames qui commencent avant la fin du son, complétées par du silence
            restantes = -(-self.total // self.pas) - self.rendues
            if restantes <= 0:
                self.tampon = np.zeros(0, np.float32)
                return vide
            besoin = (restantes - 1) * self.pas + self.longueur
            if len(b) < besoin:
                b = np.concatenate([b, np.zeros(besoin - len(b), np.float32)])
        if len(b) < self.longueur:
            self.tampon = b
            return vide
        n = (len(b) - self.longueur) // self.pas + 1
        if restantes is not None:
            n = min(n, restantes)
        trames = np.lib.stride_tricks.sliding_window_view(b, self.longueur)[::self.pas][:n]
        consomme = n * self.pas
        if consomme >= len(b):
            self.sauter, self.tampon = consomme - len(b), np.zeros(0, np.float32)
        else:
            self.tampon = b[consomme:].copy()
        self.rendues += n
        return trames


# --- F0 (YIN vectorisé) ----------------------------------------------------------------------------------------------

def yin_trames(trames, sr=SR, fenetre=640, fmin=F0_MIN, fmax=F0_MAX, seuil=0.15):
    """Hauteur (Hz, 0 = non voisé) et niveau (dBFS) de trames de fenetre + sr/fmin échantillons, sans boucle Python
    par trame (YIN : différence cumulée normalisée, premier creux sous le seuil, interpolation parabolique)."""
    tmin, tmax = int(sr / fmax), int(sr / fmin)
    n = len(trames)
    if n == 0:
        return np.zeros(0, np.float32), np.zeros(0, np.float32)
    fr = np.asarray(trames, np.float64)
    a = fr[:, :fenetre]
    rms = 10 * np.log10((a ** 2).mean(1) + 1e-12)
    nfft = 1 << int(np.ceil(np.log2(2 * fenetre + tmax)))
    r = np.fft.irfft(np.conj(np.fft.rfft(a, nfft)) * np.fft.rfft(fr, nfft), nfft)[:, :tmax + 1]
    c = np.cumsum(np.pad(fr ** 2, ((0, 0), (1, 0))), axis=1)
    dt = c[:, fenetre][:, None] + (c[:, fenetre:fenetre + tmax + 1] - c[:, :tmax + 1]) - 2 * r
    cm = np.ones_like(dt)
    somme = np.cumsum(dt[:, 1:], axis=1)
    # silence numérique : différence nulle partout, pas une période (sinon tout sortirait voisé à fmax)
    cm[:, 1:] = np.where(somme > 1e-9, dt[:, 1:] * np.arange(1, tmax + 1) / np.maximum(somme, 1e-12), 1.0)
    sous = cm[:, tmin:tmax] < seuil
    voise = sous.any(1)
    t = np.argmax(sous, 1) + tmin
    lignes = np.arange(n)
    for _ in range(32):   # descente jusqu'au creux local
        suivant = np.minimum(t + 1, tmax)
        avance = (cm[lignes, suivant] < cm[lignes, t]) & (t + 1 <= tmax)
        if not avance.any():
            break
        t = np.where(avance, t + 1, t)
    tt = np.clip(t, 1, tmax - 1)
    y0, y1, y2 = cm[lignes, tt - 1], cm[lignes, tt], cm[lignes, tt + 1]
    den = y0 - 2 * y1 + y2
    decal = np.where(np.abs(den) > 1e-12, 0.5 * (y0 - y2) / np.where(np.abs(den) > 1e-12, den, 1), 0.0)
    periode = tt + np.clip(decal, -1, 1)
    f0 = np.where(voise & (periode > 0), sr / np.maximum(periode, 1e-6), 0.0)
    return f0.astype(np.float32), rms.astype(np.float32)


def nettoyer_f0(f0, rms):
    """Niveau trop faible → non voisé ; points isolés retirés ; sauts d'octave isolés ramenés (médiane glissante)."""
    f0 = f0.copy()
    if len(f0) == 0:
        return f0
    haut = np.percentile(rms, 95) if len(rms) else -20
    f0[(rms < -55) | (rms < haut - 40)] = 0
    v = f0 > 0
    avant, apres = np.r_[False, v[:-1]], np.r_[v[1:], False]   # pas de np.roll : il relierait la fin au début
    isole = v & ~avant & ~apres
    f0[isole] = 0
    v = f0 > 0
    if v.sum() > 5:
        lf = np.where(v, np.log2(np.maximum(f0, 1)), np.nan)
        k = 7
        p = np.pad(lf, (k // 2, k // 2), constant_values=np.nan)
        fen = np.lib.stride_tricks.sliding_window_view(p, k)
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                med = np.nanmedian(fen, axis=1)
        ecart = lf - med
        corr = v & np.isfinite(med) & (np.abs(np.abs(ecart) - 1) < 0.15)
        lf[corr] = lf[corr] - np.round(ecart[corr])
        f0 = np.where(v, 2 ** np.nan_to_num(lf), 0).astype(np.float32)
    return f0


# --- Spectrogramme, bandes, onde -------------------------------------------------------------------------------------

def _lignes_log(nb, f_bas, f_haut, n_fft=N_FFT, sr=SR):
    """Pour chaque ligne d'une échelle logarithmique, l'intervalle de cases FFT à regrouper (au moins une case)."""
    bords = np.geomspace(f_bas, f_haut, nb + 1)
    case = sr / n_fft
    lo = np.floor(bords[:-1] / case).astype(int)
    hi = np.maximum(lo + 1, np.ceil(bords[1:] / case).astype(int))
    return np.clip(lo, 0, n_fft // 2), np.clip(hi, 1, n_fft // 2 + 1)


def _regrouper(puissance, lo, hi):
    """Puissance moyenne de chaque groupe de cases (somme cumulée : un seul passage)."""
    c = np.concatenate([np.zeros((len(puissance), 1)), np.cumsum(puissance, axis=1)], axis=1)
    return (c[:, hi] - c[:, lo]) / (hi - lo)


def en_octets(db):
    """dBFS → 0..255 (−90 dB → 0, 0 dB → 255)."""
    return np.clip((np.asarray(db) - PLANCHER_DB) / -PLANCHER_DB * 255, 0, 255).astype(np.uint8)


def depuis_octets(v):
    return np.asarray(v, np.float32) / 255 * -PLANCHER_DB + PLANCHER_DB


# --- Une passe sur une piste -----------------------------------------------------------------------------------------

def analyser(fichier, produits=("spectre", "onde", "bandes"), ecrire_cache=True):
    """Tous les produits demandés en une seule lecture du fichier. Rend un dict (voir charger_produit)."""
    produits = [p for p in PRODUITS if p in produits]
    fenetre_yin = int(SR * 0.04)
    tmax = int(SR / F0_MIN)
    t_spec = Trameur(N_FFT, SR // FPS_SPECTRE) if {"spectre", "bandes"} & set(produits) else None
    t_onde = Trameur(SR // FPS_ONDE, SR // FPS_ONDE) if "onde" in produits else None
    t_f0 = Trameur(fenetre_yin + tmax, int(SR * PAS_F0_S)) if "f0" in produits else None
    hann = np.hanning(N_FFT).astype(np.float32)
    norme = hann.sum() / 2                       # sinus pleine échelle → 0 dBFS
    lo_s, hi_s = _lignes_log(LIGNES_SPECTRE, F_BAS, F_HAUT)
    lo_b, hi_b = _lignes_log(NB_BANDES, 60.0, F_HAUT)
    spec, bandes, niveau, onde, f0, rms_f0 = [], [], [], [], [], []
    total = 0

    def traiter(bloc, fin=False):
        if t_spec is not None:
            tr = t_spec.pousser(bloc, fin)
            if len(tr):
                mag = np.abs(np.fft.rfft(tr * hann, axis=1)) / norme
                p = mag.astype(np.float64) ** 2
                if "spectre" in produits:
                    spec.append(en_octets(10 * np.log10(_regrouper(p, lo_s, hi_s) + 1e-12)))
                if "bandes" in produits:
                    bandes.append(en_octets(10 * np.log10(_regrouper(p, lo_b, hi_b) + 1e-12)))
                    niveau.append(en_octets(10 * np.log10((tr[:, N_FFT // 4:3 * N_FFT // 4] ** 2).mean(1) * 2 + 1e-12)))
        if t_onde is not None:
            tr = t_onde.pousser(bloc, fin)
            if len(tr):
                onde.append(np.stack([np.clip(tr.min(1), -1, 1), np.clip(tr.max(1), -1, 1)], 1))
        if t_f0 is not None:
            tr = t_f0.pousser(bloc, fin)
            for d in range(0, len(tr), 2000):
                f, r = yin_trames(tr[d:d + 2000], SR, fenetre_yin)
                f0.append(f)
                rms_f0.append(r)

    # chaque trame est datée au milieu de sa fenêtre : on fait précéder le son d'une demi-fenêtre de silence
    if t_spec is not None:
        t_spec.pousser(np.zeros(N_FFT // 2, np.float32))
    if t_f0 is not None:
        t_f0.pousser(np.zeros(fenetre_yin // 2, np.float32))
    for bloc in decoder_blocs(fichier):
        total += len(bloc)
        traiter(bloc)
    traiter(np.zeros(0, np.float32), fin=True)
    duree = total / SR
    r = {"duree_s": duree}
    n_spec = -(-total // (SR // FPS_SPECTRE))      # une colonne par pas commencé (sans la demi-fenêtre ajoutée)
    if "spectre" in produits:
        m = np.concatenate(spec)[:n_spec] if spec else np.zeros((0, LIGNES_SPECTRE), np.uint8)
        r["spectre"] = np.ascontiguousarray(m[:, ::-1].T)     # lignes : aigus en haut
        r["spectre_fps"] = FPS_SPECTRE
        r["spectre_f"] = np.array([F_BAS, F_HAUT])
    if "bandes" in produits:
        b = np.concatenate(bandes)[:n_spec] if bandes else np.zeros((0, NB_BANDES), np.uint8)
        nv = np.concatenate(niveau)[:n_spec] if niveau else np.zeros(0, np.uint8)
        pas = FPS_SPECTRE // FPS_BANDES
        k = len(b) // pas * pas
        r["bandes"] = b[:k].reshape(-1, pas, NB_BANDES).max(1) if k else np.zeros((0, NB_BANDES), np.uint8)
        r["niveau"] = nv[:k].reshape(-1, pas).max(1) if k else np.zeros(0, np.uint8)
        r["bandes_fps"] = FPS_BANDES
    if "onde" in produits:
        o = np.concatenate(onde) if onde else np.zeros((0, 2), np.float32)
        r["onde"] = np.round(o * 127).astype(np.int8)
        r["onde_fps"] = FPS_ONDE
    if "f0" in produits:
        f = np.concatenate(f0) if f0 else np.zeros(0, np.float32)
        rr = np.concatenate(rms_f0) if rms_f0 else np.zeros(0, np.float32)
        n = int(round(duree / PAS_F0_S))
        r["f0"] = nettoyer_f0(f[:n], rr[:n])
        r["f0_pas"] = PAS_F0_S
    if ecrire_cache:
        for p in produits:
            ecrire_produit(fichier, p, r)
    return r


# --- Cache des pistes ------------------------------------------------------------------------------------------------

CLES = {"spectre": ("spectre", "spectre_fps", "spectre_f"), "bandes": ("bandes", "niveau", "bandes_fps"),
        "onde": ("onde", "onde_fps"), "f0": ("f0", "f0_pas")}


def signature(fichier):
    """Chemin réel, taille et date : change dès que le fichier est refait."""
    p = Path(fichier).expanduser().resolve()
    st = p.stat()
    return f"{p}|{st.st_size}|{st.st_mtime_ns}|{VERSION}"


def chemin_cache(fichier, produit):
    cle = hashlib.sha1(f"{signature(fichier)}|{produit}".encode()).hexdigest()[:24]
    return chemins.dossier_cache() / "pistes" / f"{cle}-{produit}.npz"


def ecrire_produit(fichier, produit, r):
    f = chemin_cache(fichier, produit)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp.npz")
    try:
        np.savez_compressed(tmp, duree_s=np.array(r["duree_s"]), **{k: np.asarray(r[k]) for k in CLES[produit]})
        os.replace(tmp, f)
    finally:
        if tmp.exists():
            tmp.unlink()


def purger_cache(max_mo=CACHE_MAX_MO):
    """Garde le cache des pistes sous max_mo : efface les analyses les moins récemment utilisées (et les .tmp
    abandonnés). Rend le nombre de fichiers effacés."""
    d = chemins.dossier_cache() / "pistes"
    if not d.is_dir():
        return 0
    fichiers, efface = [], 0
    for f in d.iterdir():
        try:
            st = f.stat()
        except OSError:
            continue
        if ".tmp" in f.name and time.time() - st.st_mtime > 3600:
            f.unlink(missing_ok=True)
            efface += 1
            continue
        fichiers.append((max(st.st_atime, st.st_mtime), st.st_size, f))
    total = sum(s for _t, s, _f in fichiers)
    for _t, s, f in sorted(fichiers):
        if total <= max_mo * 1024 * 1024:
            break
        f.unlink(missing_ok=True)
        total -= s
        efface += 1
    return efface


def charger_produit(fichier, produit):
    """Le produit en cache (dict), ou None s'il manque ou si le fichier a changé."""
    try:
        f = chemin_cache(fichier, produit)
    except OSError:
        return None
    if not f.exists():
        return None
    try:
        with np.load(f) as d:
            r = {k: d[k] for k in d.files}
        r["duree_s"]
    except Exception:  # noqa: BLE001 — .npz tronqué ou abîmé (BadZipFile, zlib.error, EOFError, KeyError…)
        try:
            f.unlink()      # il sera recalculé
        except OSError:
            pass
        return None
    r["duree_s"] = float(r["duree_s"])
    for k in ("spectre_fps", "bandes_fps", "onde_fps", "f0_pas"):
        if k in r:
            r[k] = float(r[k])
    return r


def en_cache(fichier, produits):
    """Tous les produits demandés, s'ils sont tous en cache ; sinon None."""
    sortie = {}
    for p in produits:
        r = charger_produit(fichier, p)
        if r is None:
            return None
        sortie.update(r)
    return sortie


def produits_manquants(fichier, produits):
    return [p for p in produits if charger_produit(fichier, p) is None]


# --- Courbes de mélodie ----------------------------------------------------------------------------------------------

def _liste(v):
    return v if isinstance(v, (list, tuple)) else []


def _tableau(v):
    a = np.asarray([x if isinstance(x, (int, float)) and x == x else 0 for x in _liste(v)], np.float32)
    return np.where(a > 1, a, 0).astype(np.float32)


def lire_courbes(chemin):
    """courbes.json (ou courbes-locales.json) → {pas_s, source, convertie, decalage, choeurs, origine}, tableaux de
    même longueur ; None si le fichier manque, est illisible ou n'est pas au format 1."""
    d = chemins.lire_json(chemin)
    if not isinstance(d, dict) or d.get("format") != 1:
        return None
    try:
        pas = float(d.get("pas_s") or 0)
    except (TypeError, ValueError):
        return None
    if pas <= 0:
        return None
    src, conv = _tableau(d.get("source_hz")), _tableau(d.get("convertie_hz"))
    dec = np.asarray([x if isinstance(x, (int, float)) and x == x else 0 for x in _liste(d.get("decalage_dt"))],
                     np.float32)
    choeurs = d.get("chœurs_hz", d.get("choeurs_hz"))
    ch = _tableau(choeurs) if choeurs else None
    n = min(len(src), len(conv)) if len(src) and len(conv) else max(len(src), len(conv))
    if n == 0:
        return None

    def ajuster(a):
        a = a[:n]
        return np.pad(a, (0, n - len(a))) if len(a) < n else a
    return {"pas_s": pas, "source": ajuster(src), "convertie": ajuster(conv), "decalage": ajuster(dec),
            "choeurs": ajuster(ch) if ch is not None else None,
            "origine": d.get("origine") or ("doubler" if Path(chemin).name == "courbes.json" else "interface")}


def consigne(source, decalage):
    """La hauteur visée : l'origine décalée du décalage de chaque trame (demi-tons), 0 là où l'origine est muette."""
    source = np.asarray(source, np.float32)
    decalage = np.asarray(decalage, np.float32)[:len(source)]
    if len(decalage) < len(source):
        decalage = np.pad(decalage, (0, len(source) - len(decalage)))
    return np.where(source > 0, source * np.power(2.0, decalage / 12.0), 0).astype(np.float32)


def fichier_voix_source(travail):
    for nom in ("voix_source.wav", "voix_source.flac"):
        f = Path(travail) / nom
        if f.exists():
            return f
    return None


def decalages_par_trame(rapport, n, pas):
    """Décalage (demi-tons) de chaque trame d'après les morceaux du rapport ; 0 avec le modèle parole 22 kHz (il suit
    la hauteur de la voix cible de lui-même), comme dans courbes.json."""
    dec = np.zeros(n, np.float32)
    r = rapport or {}
    if r.get("mode") == "parole" and str(r.get("modele", "")).startswith("parole"):
        return dec
    t = np.arange(n) * pas
    for m in r.get("morceaux") or []:
        try:
            d = float(m.get("decalage") or 0)
            dec[(t >= float(m["debut_s"])) & (t < float(m["fin_s"]))] = d
        except (KeyError, TypeError, ValueError):
            continue
    return dec


def _sig_courte(f):
    st = Path(f).stat()
    return [st.st_size, st.st_mtime_ns]


def sources_courbes_locales(dossier_voix):
    dossier_voix = Path(dossier_voix)
    src = fichier_voix_source(dossier_voix.parent)
    conv = dossier_voix / "voix_convertie.flac"
    return src, (conv if conv.exists() else None)


def courbes_pour(dossier_voix):
    """(courbes, None) si elles sont prêtes : courbes.json de doubler, sinon courbes-locales.json à jour ;
    (None, 'a_calculer') si le repli local est possible ; (None, raison) sinon."""
    dossier_voix = Path(dossier_voix)
    c = lire_courbes(dossier_voix / "courbes.json")
    if c:
        return c, None
    src, conv = sources_courbes_locales(dossier_voix)
    if not src or not conv:
        return None, "voix isolée ou voix convertie absente de travail/ : pas de courbe de mélodie"
    loc = dossier_voix / "courbes-locales.json"
    d = chemins.lire_json(loc)
    if isinstance(d, dict) and d.get("sources") == {"voix_source": _sig_courte(src), "voix_convertie": _sig_courte(conv)}:
        c = lire_courbes(loc)
        if c:
            return c, None
    return None, "a_calculer"


def duree_audio(fichier):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(fichier)], capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def calculer_courbes_locales(dossier_voix, rapport=None):
    """Repli pour un ancien doublage : F0 (YIN) de la voix isolée et de la voix convertie, décalages du rapport ;
    écrit travail/<voix>/courbes-locales.json au format de courbes.json et le rend (lu par lire_courbes)."""
    dossier_voix = Path(dossier_voix)
    src, conv = sources_courbes_locales(dossier_voix)
    if not src or not conv:
        raise ErreurAnalyse("voix isolée ou voix convertie absente")
    if rapport is None:
        rapport = chemins.lire_json(dossier_voix / "rapport.json", {}) or {}
    f_src = analyser(src, ("f0",), ecrire_cache=False)["f0"]
    f_conv = analyser(conv, ("f0",), ecrire_cache=False)["f0"]
    n = min(len(f_src), len(f_conv))
    dec = decalages_par_trame(rapport, n, PAS_F0_S)
    hz = lambda f: [round(float(v), 1) if v > 1 else 0 for v in f[:n]]
    d = {"format": 1, "pas_s": PAS_F0_S, "source_hz": hz(f_src), "convertie_hz": hz(f_conv),
         "decalage_dt": [int(v) if float(v).is_integer() else round(float(v), 1) for v in dec],
         "origine": "interface (YIN local, 16 kHz)",
         "sources": {"voix_source": _sig_courte(src), "voix_convertie": _sig_courte(conv)},
         "date": time.strftime("%Y-%m-%dT%H:%M:%S")}
    sortie = dossier_voix / "courbes-locales.json"
    tmp = sortie.with_name(f"{sortie.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, sortie)
    finally:
        if tmp.exists():
            tmp.unlink()
    return lire_courbes(sortie)


# --- Notes -------------------------------------------------------------------------------------------------------------

NOTES_FR = ["do", "do♯", "ré", "ré♯", "mi", "fa", "fa♯", "sol", "sol♯", "la", "la♯", "si"]


def note_fr(hz):
    """440 → « la3 » (numérotation française : la3 = 440 Hz, do3 = 261,6 Hz)."""
    if not hz or hz <= 0:
        return ""
    m = int(round(69 + 12 * np.log2(hz / 440.0)))
    return f"{NOTES_FR[m % 12]}{m // 12 - 2}"


def hz_de_midi(m):
    return 440.0 * 2 ** ((m - 69) / 12)


# --- Ligne de commande -----------------------------------------------------------------------------------------------

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("pistes", "courbes"):
        print(__doc__)
        return 2
    debut = time.time()
    if argv[0] == "courbes":
        if len(argv) < 2:
            print(__doc__)
            return 2
        c = calculer_courbes_locales(argv[1])
        print(json.dumps({"courbes": argv[1], "trames": len(c["source"]), "duree_s": round(time.time() - debut, 2)}))
        return 0
    produits = ("spectre", "onde", "bandes")
    fichiers = []
    i = 1
    while i < len(argv):
        if argv[i] == "--produits":
            if i + 1 >= len(argv):
                print(__doc__)
                return 2
            produits = tuple(p for p in argv[i + 1].split(",") if p in PRODUITS)
            i += 2
            continue
        fichiers.append(argv[i])
        i += 1
    for f in fichiers:
        manque = produits_manquants(f, produits)
        if manque:
            analyser(f, manque)
    purger_cache()
    print(json.dumps({"pistes": fichiers, "produits": list(produits), "duree_s": round(time.time() - debut, 2)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
