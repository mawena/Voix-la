"""Séance d'enregistrement de la voix chantée (sans GTK, testable).

Pour être reconnue dans un doublage, une voix a besoin de sa version CHANTÉE. La séance guide la prise : des étapes
(parole, tessiture, notes à tenir, chanson, chanson libre), des pistes jouées par l'interface (ffmpeg, mp3, dans
~/.cache/voix-la/seance/<empreinte>/), le choix des chansons qui vont bien à la voix, puis l'import des fichiers
audio faits au téléphone, copiés tels quels dans voix/<nom>/chant/ ou voix/<nom>/parole/, avec un contrôle.

Les fichiers importés ne sont lus que par ffmpeg (protocoles limités aux fichiers) : rien de ce qu'ils contiennent
n'est exécuté ni interprété.
"""

import hashlib
import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import chemins, controle, formats
from . import seance_chansons as chansons
from . import seance_textes as textes

# --- Étapes --------------------------------------------------------------------------------------------------------

BIPS_S = (1.0, 2.0, 3.0, 4.0)       # bips à 880 Hz ; le 4e : on tape dans ses mains
DEBUT_CHANSON_S = 5.0               # la chanson démarre ici, après les bips
HAUTEUR_BIP_HZ = 880
LIMITE = 0.89                       # limiteur du mixage
NOTES_HZ = (110.0, 130.81, 164.81, 196.0, 220.0, 261.63)
NOTE_TENUE_S, NOTE_SILENCE_S = 5, 3
TOURS_NOTES = 2                     # un tour sur « aaa », un tour sur « ou »
VOIX_GUIDE_DB = -6.0                # voix d'origine du guide
TONALITES = tuple(range(0, -8, -1))  # 0 : d'origine, puis −1 à −7 demi-tons
OBJECTIF_CHANT_S = 10 * 60
OBJECTIF_PAROLE_S = 5 * 60
NOTE_PAROLES = "transcription automatique, il peut y avoir des fautes"
FORMATS_IMPORT = (".m4a", ".aac", ".mp3", ".wav", ".ogg", ".opus", ".3gp", ".amr")

CONSIGNES_GENERALES = (
    "Téléphone à 20 cm de la bouche, réglé sur la meilleure qualité d’enregistrement, en mode avion.",
    "Casque bien fermé et volume modéré : la musique ne doit pas passer dans le micro.",
    "Au 4e bip, tape dans tes mains : cela permet de recaler ta prise, même avec la latence d’un casque Bluetooth.",
    "Ne t’envoie pas les fichiers par WhatsApp (le son est recompressé) : câble USB, ou un dossier partagé.",
)
COMMENT_CA_MARCHE = textes.COMMENT_CA_MARCHE


@dataclass(frozen=True)
class Etape:
    cle: str
    titre: str
    consignes: tuple
    duree_s: int
    piste: str | None      # None, « tessiture », « notes », « chanson » ou « libre »
    dossier: str           # « parole » ou « chant » : où va l'enregistrement dans voix/<nom>/
    noms: tuple = ()       # noms de fichier conseillés sur le téléphone, sans extension


# durée de la piste de la tessiture : 5 s de bips, 6 sirènes séparées de 3 s, puis la gamme deux fois
SIRENE_S, SILENCE_SIRENE_S, SIRENES_PAR_VOYELLE = 8.0, 3.0, 3
NOTE_GAMME_S = 1.2
VOYELLES_SIRENE = ("aaa", "ou")
VOYELLES_GAMME = ("la", "mi")
DEGRES_GAMME = (0, 2, 4, 5, 7, 9, 11, 12)
NOMS_GAMME = ("do", "ré", "mi", "fa", "sol", "la", "si", "do")
INTRO_GAMME_S = 2.0
TESSITURE_S = (DEBUT_CHANSON_S + len(VOYELLES_SIRENE) * SIRENES_PAR_VOYELLE * (SIRENE_S + SILENCE_SIRENE_S)
               + len(VOYELLES_GAMME) * (INTRO_GAMME_S + (2 * len(DEGRES_GAMME) - 1) * NOTE_GAMME_S + SILENCE_SIRENE_S)
               + 2.0)

ETAPES = (
    Etape("parole", "Parole", (textes.CONSIGNE_PAROLE,), 180, None, "parole", ("1-parole",)),
    Etape("tessiture", "Tessiture", textes.CONSIGNES_TESSITURE, int(round(TESSITURE_S)), "tessiture", "chant",
          ("2-tessiture",)),
    Etape("notes", "Notes à tenir", (
        "La piste joue 6 notes, de la plus grave à la plus aiguë : tiens chacune 5 secondes, puis repose-toi 3 secondes.",
        "Deux tours : le premier sur « aaa », le second sur « ou ».",
        "Chante la note que tu entends, sans vibrato ni effet."), TOURS_NOTES * len(NOTES_HZ) * (
            NOTE_TENUE_S + NOTE_SILENCE_S), "notes", "chant", ("3-notes",)),
    Etape("chanson", "Chanson", (
        "Choisis un doublage et une tonalité. Répète d’abord avec la voix d’origine pour trouver ta place.",
        "Puis la vraie prise sur la musique seule : chante toute la chanson, avec les paroles ci-dessous.",
        "Tape dans tes mains sur le 4e bip."), 4 * 60, "chanson", "chant", ("4-chanson",)),
    Etape("libre", "Chanson libre", textes.CONSIGNES_CHANSON_PUBLIQUE, 3 * 60, "libre", "chant",
          tuple(c.nom_fichier for c in chansons.CHANSONS)),
)


def etape(cle):
    return next((e for e in ETAPES if e.cle == cle), None)


# --- Préparation des pistes ----------------------------------------------------------------------------------------

class ErreurSeance(RuntimeError):
    pass


def dossier_seance():
    return chemins.dossier_cache() / "seance"


def empreinte(travail):
    """Identifie les sons d'un doublage (chemin, taille et date de instrumental.wav et voix_source.wav)."""
    h = hashlib.sha1()
    travail = Path(travail)
    h.update(str(travail.resolve()).encode())
    for nom in ("instrumental.wav", "voix_source.wav"):
        try:
            s = (travail / nom).stat()
            h.update(f"|{nom}:{s.st_size}:{int(s.st_mtime)}".encode())
        except OSError:
            h.update(f"|{nom}:-".encode())
    return h.hexdigest()[:12]


def nom_tonalite(t):
    return "tonalité d’origine" if not t else f"{abs(t)} demi-ton{'s' if abs(t) > 1 else ''} plus bas"


def _ratio(t):
    return 2 ** (t / 12)


def _suffixe(t):
    return "" if not t else f"_m{abs(int(t))}"


def _ffmpeg(args, duree_s, progression=None, bas=0.0, haut=1.0, annuler=None):
    """Lance ffmpeg ; progression(fraction, texte) entre bas et haut d'après -progress. Lève ErreurSeance si échec."""
    cmd = ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-nostats", "-y", "-loglevel", "error", "-progress", "pipe:1",
           *args]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as e:
        raise ErreurSeance(f"ffmpeg est introuvable : {e}") from e
    for ligne in p.stdout:
        if annuler is not None and annuler.is_set():
            p.kill()
            raise ErreurSeance("préparation annulée")
        m = re.match(r"out_time_(?:us|ms)=(\d+)", ligne)
        if m and progression and duree_s:
            fait = min(1.0, int(m.group(1)) / 1e6 / duree_s)
            progression(bas + (haut - bas) * fait)
    erreur = p.stderr.read()
    p.stdout.close()
    p.stderr.close()
    if p.wait() != 0:
        raise ErreurSeance(f"ffmpeg a échoué : {erreur.strip()[-300:]}")


def _filtre_bips():
    parts, noms = [], []
    for i, t in enumerate(BIPS_S):
        parts.append(f"sine=f={HAUTEUR_BIP_HZ}:d=0.15:r=44100,aformat=channel_layouts=stereo,"
                     f"afade=t=out:st=0.10:d=0.05,volume=2.5,adelay={int(t * 1000)}:all=1[b{i}]")
        noms.append(f"[b{i}]")
    return parts, noms


def _encoder(sortie):
    return ["-c:a", "libmp3lame", "-q:a", "3", "-ar", "44100", "-ac", "2", str(sortie)]


def piste_notes(sortie, progression=None, annuler=None):
    """Les 6 notes à tenir (sinus), 5 s de son et 3 s de silence, deux tours."""
    parts, noms = [], []
    n = 0
    for _tour in range(TOURS_NOTES):
        for f in NOTES_HZ:
            parts.append(f"sine=f={f}:d={NOTE_TENUE_S}:r=44100,aformat=channel_layouts=stereo,"
                         f"afade=t=in:d=0.08,afade=t=out:st={NOTE_TENUE_S - 0.08}:d=0.08,volume=4,"
                         f"apad=pad_dur={NOTE_SILENCE_S}[n{n}]")
            noms.append(f"[n{n}]")
            n += 1
    duree = n * (NOTE_TENUE_S + NOTE_SILENCE_S)
    graphe = ";".join(parts + ["".join(noms) + f"concat=n={n}:v=0:a=1,alimiter=limit={LIMITE}:level=0[s]"])
    sortie = Path(sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    tmp = sortie.with_name(sortie.stem + ".part" + sortie.suffix)
    _ffmpeg(["-filter_complex", graphe, "-map", "[s]", *_encoder(tmp)], duree, progression, annuler=annuler)
    tmp.replace(sortie)
    return sortie


def piste_chanson(travail, sortie, avec_voix, tonalite=0, progression=None, bas=0.0, haut=1.0, annuler=None):
    """Musique seule (instrumental.wav), ou guide (musique + voix d'origine à −6 dB), transposée de `tonalite` demi-
    tons (rubberband), décalée de 5 s derrière 4 bips à 880 Hz ; limiteur à 0,89."""
    travail = Path(travail)
    inst, voix = travail / "instrumental.wav", travail / "voix_source.wav"
    if not inst.is_file():
        raise ErreurSeance("ce doublage n’a pas de musique seule (travail/instrumental.wav)")
    if avec_voix and not voix.is_file():
        raise ErreurSeance("ce doublage n’a pas la voix d’origine (travail/voix_source.wav)")
    entrees = ["-i", str(inst)] + (["-i", str(voix)] if avec_voix else [])
    base = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    if avec_voix:
        corps = (f"[0:a]{base}[i];[1:a]{base},volume={VOIX_GUIDE_DB}dB[v];"
                 f"[i][v]amix=inputs=2:normalize=0:duration=longest[mix]")
    else:
        corps = f"[0:a]{base}[mix]"
    suite = "[mix]"
    if tonalite:
        suite += f"rubberband=pitch={_ratio(tonalite):.6f}:pitchq=quality:channels=together,"
    suite += f"adelay={int(DEBUT_CHANSON_S * 1000)}:all=1[m]"
    bips, noms = _filtre_bips()
    final = f"[m]{''.join(noms)}amix=inputs={1 + len(noms)}:normalize=0:duration=longest,alimiter=limit={LIMITE}:level=0[s]"
    graphe = ";".join([corps, suite, *bips, final])
    sortie = Path(sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    tmp = sortie.with_name(sortie.stem + ".part" + sortie.suffix)
    duree = (controle.duree(inst) or 0.0) + DEBUT_CHANSON_S
    _ffmpeg([*entrees, "-filter_complex", graphe, "-map", "[s]", *_encoder(tmp)], duree, progression, bas, haut, annuler)
    tmp.replace(sortie)
    return sortie


def pistes_chanson(travail, tonalite=0, cache=None):
    """Les chemins (guide, musique) d'une chanson dans cette tonalité, déjà préparés ou non."""
    d = Path(cache) if cache else dossier_seance()
    d = d / empreinte(travail)
    s = _suffixe(tonalite)
    return {"guide": d / f"guide{s}.mp3", "musique": d / f"musique{s}.mp3"}


def preparer_pistes(travail, tonalite=0, progression=None, cache=None, annuler=None):
    """Prépare (hors du fil de l'interface) le guide avec la voix et la musique seule de ce doublage, dans cette tonalité
    (0, ou −1 à −7). Ce qui existe déjà n'est pas refait. progression(fraction, texte). Renvoie {"guide", "musique"}."""
    if tonalite not in TONALITES:
        raise ErreurSeance(f"tonalité inconnue : {tonalite}")
    pistes = pistes_chanson(travail, tonalite, cache)
    a_faire = [(c, p) for c, p in pistes.items() if not (p.is_file() and p.stat().st_size > 0)]
    rendre = progression or (lambda *_: None)
    for i, (cle, chemin) in enumerate(a_faire):
        n = len(a_faire)
        texte = ("Préparation du guide avec la voix…" if cle == "guide" else "Préparation de la musique seule…")
        rendre(i / n, texte)
        piste_chanson(travail, chemin, cle == "guide", tonalite,
                      lambda f, t=texte, i=i, n=n: rendre((i + f) / n, t), annuler=annuler)
    rendre(1.0, "Pistes prêtes")
    return pistes


def preparer_notes(progression=None, cache=None, annuler=None):
    """La piste des notes à tenir (la même pour toutes les chansons)."""
    sortie = (Path(cache) if cache else dossier_seance()) / "notes.mp3"
    if not (sortie.is_file() and sortie.stat().st_size > 0):
        piste_notes(sortie, (lambda f: progression(f, "Préparation des notes…")) if progression else None, annuler)
    return sortie


# --- Centre de la voix, tessiture et chansons du domaine public (synthèse numpy, encodage ffmpeg) --------------------

CENTRE_GRAVE_HZ, CENTRE_AIGU_HZ = textes.CHOIX_VOIX[0][2], textes.CHOIX_VOIX[1][2]
VERSION_PISTES = 1       # change quand la synthèse change : les pistes du cache sont refaites


def centre_voix(dossier_voix, choix=None):
    """(C en Hz, d'où il vient) : le milieu de la zone de confort de tessiture.json (sinon son p50), sinon deux fois la
    hauteur de parole médiane de voix.json ; à défaut, le choix « grave » (190 Hz) ou « aigue » (380 Hz) fait à la main.
    (None, '') si rien ne permet de le dire."""
    zone = zone_de_chant(dossier_voix)
    if zone is not None:
        return zone[0], ("ta tessiture mesurée" if zone[3] else "deux fois la hauteur de ta parole")
    for cle, _libelle, hz in textes.CHOIX_VOIX:
        if choix == cle:
            return hz, f"voix {'grave' if cle == 'grave' else 'aiguë'}, d’après ton choix"
    return None, ""


def _signal_bips(longueur_s, rate=44100):
    """Les 4 bips à 880 Hz (t = 1, 2, 3, 4 s), 0,15 s chacun, dans un tableau mono de longueur_s secondes."""
    import numpy as np
    sortie = np.zeros(int(longueur_s * rate))
    n = int(0.15 * rate)
    t = np.arange(n) / rate
    bip = 0.31 * np.sin(2 * np.pi * HAUTEUR_BIP_HZ * t) * np.clip((0.15 - t) / 0.05, 0.0, 1.0)
    for d in BIPS_S:
        i = int(d * rate)
        sortie[i:i + n] += bip
    return sortie


def _ecrire_mp3(signal, sortie, annuler=None, progression=None, rate=44100):
    """Écrit un signal mono (flottant) en mp3 stéréo : limiteur à 0,89, wav temporaire, ffmpeg."""
    import numpy as np
    import wave
    sortie = Path(sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    pic = float(np.max(np.abs(signal))) if len(signal) else 0.0
    if pic > LIMITE:
        signal = signal * (LIMITE / pic)
    pcm = (np.clip(signal, -1.0, 1.0) * 32767).astype("<i2")
    brut = sortie.with_name(sortie.stem + ".part.wav")
    tmp = sortie.with_name(sortie.stem + ".part" + sortie.suffix)
    try:
        with wave.open(str(brut), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        _ffmpeg(["-i", str(brut), *_encoder(tmp)], len(signal) / rate, progression, annuler=annuler)
        tmp.replace(sortie)
    finally:
        for f in (brut, tmp):
            f.unlink(missing_ok=True)
    return sortie


def _tonalite_douce(frequence, rate=44100, harmoniques=(1.0, 0.35, 0.12)):
    """Phase cumulée d'une fréquence qui varie (tableau) → son doux : fondamentale et deux harmoniques."""
    import numpy as np
    phase = 2 * np.pi * np.cumsum(frequence) / rate
    return sum(a * np.sin(k * phase) for k, a in enumerate(harmoniques, 1)) / sum(harmoniques)


def _enveloppe_douce(n, montee_s, descente_s, rate=44100):
    import numpy as np
    i = np.arange(n) / rate
    mont = 0.5 - 0.5 * np.cos(np.pi * np.clip(i / montee_s, 0.0, 1.0))
    desc = 0.5 - 0.5 * np.cos(np.pi * np.clip((n / rate - i) / descente_s, 0.0, 1.0))
    return mont * desc


def sirene(centre_hz, rate=44100):
    """Une sirène guide : une glissade continue de C × 2^(−9/12) à C × 2^(+9/12), puis retour, en 8 s (un cosinus sur la
    hauteur en demi-tons : lente aux extrémités, sans à-coup), son doux, enveloppe douce."""
    import numpy as np
    n = int(SIRENE_S * rate)
    t = np.arange(n) / rate
    x = 0.5 - 0.5 * np.cos(2 * np.pi * t / SIRENE_S)             # 0 → 1 → 0
    f = centre_hz * 2.0 ** ((-9.0 + 18.0 * x) / 12.0)
    return 0.32 * _tonalite_douce(f, rate) * _enveloppe_douce(n, 0.4, 0.4, rate)


def tonique_gamme(centre_hz):
    """La tonique de la gamme : le demi-ton entier le plus proche de C × 2^(−5/12), en MIDI."""
    return int(round(chansons.midi_de_hz(centre_hz * 2.0 ** (-5 / 12))))


def notes_gamme(centre_hz):
    """Les 15 notes de la gamme, montée puis descente : [(nom, fréquence Hz)]."""
    t = tonique_gamme(centre_hz)
    montee = list(zip(NOMS_GAMME, DEGRES_GAMME))
    chemin = montee + montee[-2::-1]
    return [(nom, chansons.hauteur_hz(t + d)) for nom, d in chemin]


def calendrier_tessiture(centre_hz):
    """[(début s, fin s, texte, genre)] de la piste de tessiture, bips compris (les 5 premières secondes) ; genre :
    « sirene », « repos », « intro » (annonce de la gamme) ou « note »."""
    t, sortie = DEBUT_CHANSON_S, []
    n = SIRENES_PAR_VOYELLE
    for v in VOYELLES_SIRENE:
        for k in range(1, n + 1):
            sortie.append((t, t + SIRENE_S, f"Sirène sur « {v} »  ({k}/{n})", "sirene"))
            t += SIRENE_S
            sortie.append((t, t + SILENCE_SIRENE_S, "Respire…", "repos"))
            t += SILENCE_SIRENE_S
    for v in VOYELLES_GAMME:
        sortie.append((t, t + INTRO_GAMME_S, f"La gamme, sur « {v} »", "intro"))
        t += INTRO_GAMME_S
        for nom, _f in notes_gamme(centre_hz):
            sortie.append((t, t + NOTE_GAMME_S, nom.capitalize(), "note"))
            t += NOTE_GAMME_S
        sortie.append((t, t + SILENCE_SIRENE_S, "Respire…", "repos"))
        t += SILENCE_SIRENE_S
    return sortie


def signal_tessiture(centre_hz, rate=44100):
    """Le son de la piste de tessiture (mono) : bips, 6 sirènes guides, la gamme deux fois."""
    import numpy as np
    fin = calendrier_tessiture(centre_hz)[-1][1]
    sortie = np.zeros(int((max(fin, TESSITURE_S - 2.0) + 2.0) * rate))
    sortie[:int(DEBUT_CHANSON_S * rate)] = _signal_bips(DEBUT_CHANSON_S, rate)
    notes = iter(notes_gamme(centre_hz) * len(VOYELLES_GAMME))
    for a, _b, _texte, genre in calendrier_tessiture(centre_hz):
        i = int(round(a * rate))
        if genre == "sirene":
            son = sirene(centre_hz, rate)
        elif genre == "note":
            _nom, f = next(notes)
            n = int(NOTE_GAMME_S * rate)
            son = 0.3 * _tonalite_douce(np.full(n, f), rate) * _enveloppe_douce(n, 0.06, 0.08, rate)
        else:
            continue
        sortie[i:i + len(son)] += son
    return sortie


def paroles_tessiture(centre_hz):
    """Ce qui s'affiche pendant la piste de tessiture (voixla.paroles.Paroles) : la sirène en cours, le nom de chaque note
    de la gamme en grand, et les moments de repos."""
    from .paroles import Couche, Ligne, Paroles
    lignes = [Ligne(a, b, texte) for a, b, texte, _g in calendrier_tessiture(centre_hz)]
    return Paroles(Couche("fr", "piste de la séance", lignes), {}, "seance")


def chemin_tessiture(centre_hz, cache=None):
    return (Path(cache) if cache else dossier_seance()) / f"tessiture-v{VERSION_PISTES}-{int(round(centre_hz))}.mp3"


def preparer_tessiture(centre_hz, progression=None, cache=None, annuler=None):
    """La piste de la tessiture, réglée sur le centre C de la voix (ce qui existe déjà n'est pas refait)."""
    sortie = chemin_tessiture(centre_hz, cache)
    if not (sortie.is_file() and sortie.stat().st_size > 0):
        _ecrire_mp3(signal_tessiture(centre_hz), sortie, annuler,
                    (lambda f: progression(f, "Préparation de la tessiture…")) if progression else None)
    return sortie


GENRES_CHANSON = {"melodie": "avec la mélodie", "accompagnement": "accompagnement seul"}


def chemin_chanson(cle, genre, centre_hz, cache=None):
    c = chansons.chanson(cle)
    if c is None or genre not in GENRES_CHANSON:
        raise ErreurSeance(f"chanson inconnue : {cle} ({genre})")
    d = chansons.transposition(c, centre_hz)
    return (Path(cache) if cache else dossier_seance()) / f"{cle}-v{VERSION_PISTES}-{d:+d}-{genre}.mp3"


def preparer_chanson_publique(cle, genre, centre_hz, progression=None, cache=None, annuler=None):
    """La piste d'une chanson du domaine public : 4 bips, puis (à 5 s) la chanson dans la hauteur de la voix, avec la
    mélodie au piano ou en accompagnement seul (accords et métronome léger). Hors du fil de l'interface."""
    sortie = chemin_chanson(cle, genre, centre_hz, cache)
    if not (sortie.is_file() and sortie.stat().st_size > 0):
        import numpy as np
        c = chansons.chanson(cle)
        son = chansons.rendre(c, centre_hz, genre == "melodie")
        signal = np.concatenate([_signal_bips(DEBUT_CHANSON_S), son])
        _ecrire_mp3(signal, sortie, annuler,
                    (lambda f: progression(f, "Préparation de la chanson…")) if progression else None)
    return sortie


def paroles_chanson_publique(cle):
    """Les paroles synchronisées d'une chanson du domaine public, décalées des 5 s de bips."""
    c = chansons.chanson(cle)
    return chansons.paroles(c, DEBUT_CHANSON_S) if c else None


# --- Ce que la personne a déjà fait (voix/<nom>/seance.json) ---------------------------------------------------------

def fichier_etat(dossier_voix):
    return Path(dossier_voix) / "seance.json"


def lire_etat(dossier_voix):
    """{"fait": {étape: vrai}, "taille_texte": facteur, "centre": « grave » | « aigue »} lu dans seance.json ; les valeurs
    abîmées sont ignorées."""
    brut = chemins.lire_json(fichier_etat(dossier_voix), {})
    brut = brut if isinstance(brut, dict) else {}
    fait = brut.get("fait") if isinstance(brut.get("fait"), dict) else {}
    taille = brut.get("taille_texte")
    centre = brut.get("centre")
    return {"fait": {e.cle: fait.get(e.cle) is True for e in ETAPES},
            "taille_texte": float(taille) if isinstance(taille, (int, float)) and not isinstance(taille, bool)
            and 0.5 <= taille <= 4.0 else 1.0,
            "centre": centre if centre in ("grave", "aigue") else None}


def ecrire_etat(dossier_voix, etat):
    donnees = {"fait": {k: bool(v) for k, v in etat.get("fait", {}).items() if etape(k) is not None},
               "taille_texte": etat.get("taille_texte", 1.0)}
    if etat.get("centre") in ("grave", "aigue"):
        donnees["centre"] = etat["centre"]
    Path(dossier_voix).mkdir(parents=True, exist_ok=True)
    chemins.ecrire_json(fichier_etat(dossier_voix), donnees)


def marquer_fait(dossier_voix, cle_etape, fait=True):
    etat = lire_etat(dossier_voix)
    etat["fait"][cle_etape] = bool(fait)
    ecrire_etat(dossier_voix, etat)
    return etat


# --- Reconnaître l'étape d'un fichier d'après son nom ------------------------------------------------------------------

def _normaliser(nom):
    import unicodedata
    sans = unicodedata.normalize("NFD", nom.lower())
    sans = "".join(c for c in sans if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", sans).strip("-")


_MOTS_ETAPE = (("parole", ("parole",)), ("tessiture", ("tessiture", "sirene", "gamme")), ("notes", ("notes",)),
               ("chanson", ("chanson",)), ("libre", ("clair-de-la-lune", "frere-jacques", "libre")))


def reconnaitre(nom_fichier):
    """(clé de l'étape, clé de la chanson ou '') d'après le nom d'un fichier : « 1-parole.m4a », « 5-frere-jacques.mp3 »,
    « Tessiture 2.wav »… ; (None, '') si rien n'est reconnu. Le numéro du début l'emporte, sinon le nom suffit."""
    base = _normaliser(Path(nom_fichier).stem)
    morceau = next((c for c in chansons.CHANSONS if c.cle in base), None)
    if morceau is not None:
        return "libre", morceau.cle
    numero = re.match(r"([1-5])(?:-|$)", base)
    if numero:
        return ETAPES[int(numero.group(1)) - 1].cle, ""
    return next((cle for cle, mots in _MOTS_ETAPE if any(m in base for m in mots)), None), ""


# --- Chansons conseillées ------------------------------------------------------------------------------------------

@dataclass
class Doublage:
    dossier: Path
    titre: str
    travail: Path
    lrc: Path | None = None
    chanteur: dict | None = None      # {f0_p10_hz, f0_med_hz, f0_p90_hz, classe}
    langue: str = ""                  # langue chantée (« fr », « en »…), '' si on ne la connaît pas


@dataclass
class Conseil:
    doublage: Doublage
    ecart_st: float | None            # tessiture du chanteur d'origine − zone de chant de la voix (demi-tons, + : trop haut)
    tonalite: int                     # conseillée : 0, ou −1 à −7
    reste_st: float | None            # écart qui reste après la transposition
    ambitus_st: float | None = None
    zone_trop_etroite: bool = False


def _nombre(d, cle):
    v = d.get(cle) if isinstance(d, dict) else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 else None


def chanteur_principal(rapport):
    """Le chanteur d'origine d'après rapport.json : decision_hauteur.voix_principale, sinon le premier dictionnaire qui
    porte f0_p10_hz, f0_med_hz, f0_p90_hz et classe."""
    trouves = []

    def parcourir(o):
        if isinstance(o, dict):
            if _nombre(o, "f0_med_hz") and "classe" in o:
                trouves.append(o)
            for v in o.values():
                parcourir(v)
        elif isinstance(o, list):
            for v in o:
                parcourir(v)
    dh = rapport.get("decision_hauteur") if isinstance(rapport, dict) else None
    vp = dh.get("voix_principale") if isinstance(dh, dict) else None
    if isinstance(vp, dict) and _nombre(vp, "f0_med_hz"):
        return {k: vp.get(k) for k in ("f0_p10_hz", "f0_med_hz", "f0_p90_hz", "classe")}
    parcourir(rapport)
    if not trouves:
        return None
    return {k: trouves[0].get(k) for k in ("f0_p10_hz", "f0_med_hz", "f0_p90_hz", "classe")}


LANGUES_DEFAUT = ("fr",)


def _code_langue(code):
    """« fr-FR », « fra », « FR » → « fr » ; '' pour une valeur vide."""
    c = str(code or "").strip().lower().replace("_", "-").split("-")[0]
    return {"fra": "fr", "fre": "fr", "eng": "en", "spa": "es", "por": "pt", "ita": "it", "deu": "de",
            "ger": "de"}.get(c, c)


def langues_voix(dossier_voix):
    """Les langues que parle la voix (voix.json, clé « langues »), ("fr",) par défaut."""
    v = chemins.lire_json(Path(dossier_voix) / "voix.json", {})
    liste = v.get("langues") if isinstance(v, dict) else None
    codes = tuple(c for c in (_code_langue(x) for x in liste) if c) if isinstance(liste, list) else ()
    return codes or LANGUES_DEFAUT


def langue_du_doublage(dossier, rapport=None):
    """La langue chantée : rapport.json (paroles_sorties, pistes_audio), sinon travail/<voix>/paroles.json, sinon le
    [la:xx] du .lrc, sinon le suffixe d'un .srt (« … .en.srt »). '' si inconnue."""
    dossier = Path(dossier)
    r = rapport if isinstance(rapport, dict) else {}
    ps = r.get("paroles_sorties") if isinstance(r.get("paroles_sorties"), dict) else {}
    lc = ps.get("langue_chantee") if isinstance(ps.get("langue_chantee"), dict) else {}
    pa = r.get("pistes_audio") if isinstance(r.get("pistes_audio"), dict) else {}
    la = pa.get("langue") if isinstance(pa.get("langue"), dict) else {}
    for code in (lc.get("langue"), ps.get("langue"), la.get("paroles")):
        if isinstance(code, str) and code.strip():
            return _code_langue(code)
    for f in sorted((dossier / "travail").glob("*/paroles.json")):
        d = chemins.lire_json(f, {})
        if isinstance(d, dict) and isinstance(d.get("langue"), str) and d["langue"].strip():
            return _code_langue(d["langue"])
    for f in sorted(dossier.glob("*.lrc")):
        try:
            m = re.search(r"^\[la:([^\]]+)\]", f.read_text(encoding="utf-8", errors="replace"), re.M)
        except OSError:
            continue
        if m and _code_langue(m.group(1)):
            return _code_langue(m.group(1))
    srt = {_code_langue(m.group(1)) for f in dossier.glob("*.srt")
           if (m := re.search(r"\.([A-Za-z]{2,3})\.srt$", f.name))}
    if len(srt) == 1:
        return next(iter(srt))
    return next((c for c in sorted(srt) if c != "fr"), "fr" if srt else "")


def lister_doublages(sorties, profondeur=2):
    """Les doublages de la bibliothèque qui ont leurs sons de travail (travail/instrumental.wav et voix_source.wav) :
    [Doublage]. Le chanteur vient du premier travail/<voix>/rapport.json lisible."""
    sorties = Path(sorties)
    trouves = []

    def voir(d, niveau):
        if not d.is_dir() or d.name.startswith("."):
            return
        travail = d / "travail"
        if (travail / "instrumental.wav").is_file():
            chanteur = None
            for r in sorted(travail.glob("*/rapport.json")):
                chanteur = chanteur_principal(chemins.lire_json(r, {}))
                if chanteur:
                    break
            lrcs = sorted(d.glob("*.lrc"), key=lambda p: ("(auto-tune)" in p.name, p.name))
            rap = next((chemins.lire_json(r, {}) for r in sorted(travail.glob("*/rapport.json"))), {})
            trouves.append(Doublage(d, re.sub(r"\s*\(extrait [^)]*\)\s*$", "", d.name), travail,
                                    lrcs[0] if lrcs else None, chanteur, langue_du_doublage(d, rap)))
        elif niveau < profondeur:
            for s in sorted(d.iterdir()):
                voir(s, niveau + 1)
    try:
        for d in sorted(sorties.iterdir()):
            voir(d, 1)
    except OSError:
        pass
    return trouves


def zone_de_chant(dossier_voix):
    """(centre_hz, bas_hz, haut_hz, mesuree) de la voix : tessiture.json s'il existe (confort [bas, haut], sinon p50),
    sinon deux fois la hauteur de parole médiane de voix.json. None si on ne peut rien en dire."""
    dossier_voix = Path(dossier_voix)
    t = chemins.lire_json(dossier_voix / "tessiture.json", None)
    if isinstance(t, dict):
        confort = t.get("confort")
        if (isinstance(confort, (list, tuple)) and len(confort) == 2 and _nombre({"a": confort[0]}, "a")
                and _nombre({"a": confort[1]}, "a")):
            bas, haut = sorted((float(confort[0]), float(confort[1])))
            return math.sqrt(bas * haut), bas, haut, True
        p50 = _nombre(t, "p50")
        if p50:
            return p50, _nombre(t, "p10") or p50, _nombre(t, "p90") or p50, True
    v = chemins.lire_json(dossier_voix / "voix.json", {})
    h = _nombre(v, "hauteur_parole_mediane_hz")
    if not h:
        return None
    return 2 * h, None, None, False


def demi_tons(f, ref):
    return 12 * math.log2(f / ref)


def tonalite_conseillee(ecart_st):
    """0, ou −1 à −7 : on baisse le morceau de l'écart (arrondi), au plus de 7 demi-tons ; jamais on ne le monte."""
    if ecart_st is None or ecart_st < 0.5:
        return 0
    return -min(7, int(round(ecart_st)))


def conseiller(doublages, zone, langues=None, toutes=False):
    """Classe les doublages pour une voix : d'abord ceux dont la tessiture, une fois transposée, est la plus proche de
    la zone de chant ; ceux sans tessiture connue à la fin. zone : voir zone_de_chant (ou None). Seuls les doublages
    dans une des `langues` de la voix sont gardés (langues_voix) ; toutes=True les garde tous (langue inconnue comprise)."""
    liste = []
    autorisees = {_code_langue(x) for x in langues} if langues else None
    for d in doublages:
        if autorisees is not None and not toutes and d.langue not in autorisees:
            continue
        f =_nombre(d.chanteur, "f0_med_hz") if d.chanteur else None
        if f is None or zone is None:
            liste.append(Conseil(d, None, 0, None))
            continue
        centre, bas, haut, _mesuree = zone
        ecart = demi_tons(f, centre)
        t = tonalite_conseillee(ecart)
        reste = ecart - (-t)          # t ≤ 0 : le morceau descend de |t|
        p10, p90 = _nombre(d.chanteur, "f0_p10_hz"), _nombre(d.chanteur, "f0_p90_hz")
        ambitus = demi_tons(p90, p10) if p10 and p90 else None
        etroite = bool(ambitus and bas and haut and ambitus > demi_tons(haut, bas) + 2)
        liste.append(Conseil(d, ecart, t, reste, ambitus, etroite))
    # écart restant après la transposition, plus un léger prix pour chaque demi-ton baissé (le son s'en ressent)
    liste.sort(key=lambda c: (c.reste_st is None,
                              abs(c.reste_st) + 0.25 * abs(c.tonalite) if c.reste_st is not None else 0,
                              c.doublage.titre))
    return liste


def libelle_ecart(c):
    if c.ecart_st is None:
        return "tessiture inconnue"
    e = c.ecart_st
    sens = "plus haut" if e > 0 else "plus bas"
    txt = f"chanteur d’origine {formats.nombre(abs(e), 1)} demi-ton{'s' if abs(e) >= 2 else ''} {sens} que ta zone"
    return f"{txt} · conseillé : {nom_tonalite(c.tonalite)}"


# --- Paroles synchronisées -----------------------------------------------------------------------------------------

_LRC = re.compile(r"^\[(\d+):(\d{1,2}(?:[.,]\d+)?)\](.*)$")


def lire_lrc(chemin, decalage_s=DEBUT_CHANSON_S):
    """Un .lrc de doubler → paroles.Paroles, décalées de `decalage_s` (les 5 s de bips avant la chanson). None s'il
    n'y a aucune ligne."""
    from .paroles import Couche, Ligne, Paroles
    try:
        texte = Path(chemin).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    debuts, langue = [], None
    for brut in texte.splitlines():
        m = _LRC.match(brut.strip())
        if m:
            mots = m.group(3).strip()
            if mots:
                debuts.append((int(m.group(1)) * 60 + float(m.group(2).replace(",", ".")), mots))
        elif brut.startswith("[la:"):
            langue = brut[4:].strip(" ]") or None
    if not debuts:
        return None
    debuts.sort()
    lignes = []
    for i, (t, mots) in enumerate(debuts):
        fin = debuts[i + 1][0] if i + 1 < len(debuts) else t + 4.0
        lignes.append(Ligne(t + decalage_s, fin + decalage_s, mots))
    return Paroles(Couche(langue, "transcription automatique", lignes), {}, "lrc")


# --- Import des enregistrements ------------------------------------------------------------------------------------

def format_accepte(fichier):
    return Path(fichier).suffix.lower() in FORMATS_IMPORT


def dossier_destination(dossier_voix, cle_etape):
    e = etape(cle_etape)
    if e is None:
        raise ErreurSeance(f"étape inconnue : {cle_etape}")
    return Path(dossier_voix) / e.dossier


def nom_importe(cle_etape, suffixe, existants=(), maintenant=None, detail=""):
    """Un nom horodaté qui garde l'étape : 20261109-153012-notes.m4a (-2, -3… si deux fichiers dans la même seconde) ;
    detail (la chanson de l'étape 5) le suit : 20261109-153012-libre-frere-jacques.m4a."""
    base = time.strftime("%Y%m%d-%H%M%S", time.localtime(maintenant if maintenant is not None else time.time()))
    suffixe = suffixe.lower()
    etiquette = f"{cle_etape}-{detail}" if detail else cle_etape
    nom, n = f"{base}-{etiquette}{suffixe}", 1
    while nom in existants:
        n += 1
        nom = f"{base}-{etiquette}-{n}{suffixe}"
    return nom


def importer(fichiers, dossier_voix, cle_etape, maintenant=None, detail=""):
    """Copie tels quels des fichiers audio choisis à la main dans voix/<nom>/chant/ ou parole/ ; renvoie
    [(source, destination | None, message d'erreur)]. Un fichier de format inconnu, absent ou qui n'est pas lisible
    par ffprobe comme un son est refusé."""
    dest = dossier_destination(dossier_voix, cle_etape)
    resultats = []
    for f in fichiers:
        f = Path(f)
        if not f.is_file():
            resultats.append((f, None, "fichier introuvable"))
            continue
        if not format_accepte(f):
            resultats.append((f, None, f"format non pris en charge ({f.suffix or 'sans extension'})"))
            continue
        if controle.duree(f.resolve()) is None:
            resultats.append((f, None, "ce fichier n’est pas un son lisible"))
            continue
        dest.mkdir(parents=True, exist_ok=True)
        nom = nom_importe(cle_etape, f.suffix, {p.name for p in dest.iterdir()}, maintenant, detail)
        try:
            shutil.copyfile(f, dest / nom)
        except OSError as e:
            resultats.append((f, None, f"copie impossible : {e}"))
            continue
        resultats.append((f, dest / nom, ""))
    return resultats


EXTENSIONS_AUDIO = {*FORMATS_IMPORT, ".flac", ".opus"}


def duree_enregistree(dossier_voix):
    """(secondes de chant, secondes de parole) déjà enregistrées pour cette voix (voix/<nom>/chant/ et parole/)."""
    def somme(d):
        total = 0.0
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix.lower() in EXTENSIONS_AUDIO:
                    total += controle.duree(f) or 0.0
        return total
    d = Path(dossier_voix)
    return somme(d / "chant"), somme(d / "parole")


# --- Contrôles à l'import ------------------------------------------------------------------------------------------

SEUIL_FUITE = 0.6        # corrélation d'enveloppes au-dessus de laquelle on entend la musique du casque
ENVELOPPE_HZ = 20
LISSAGE_S = 1.0          # variations plus lentes que cela (les phrases du chanteur) sont retirées


def _enveloppe(fichier, duree_max=None):
    """Amplitude efficace par tranches de 50 ms d'un son décodé par ffmpeg (mono, 8 kHz), sans ses variations lentes
    (celles des phrases du chanteur) : il reste le rythme, que la musique du casque et la piste jouée ont en commun."""
    cmd = ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file",
           "-i", str(Path(fichier).resolve()), *(["-t", str(duree_max)] if duree_max else []), "-vn", "-ac", "1",
           "-ar", "8000", "-f", "s16le", "-"]
    try:
        brut = subprocess.run(cmd, capture_output=True, timeout=300).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    import array
    a = array.array("h")
    a.frombytes(brut[:len(brut) // 2 * 2])
    pas = 8000 // ENVELOPPE_HZ
    env = []
    for i in range(0, len(a) - pas + 1, pas):
        env.append(math.sqrt(sum(x * x for x in a[i:i + pas]) / pas))
    if not env:
        return []
    demi = max(1, int(LISSAGE_S * ENVELOPPE_HZ / 2))
    cumul = [0.0]
    for x in env:
        cumul.append(cumul[-1] + x)
    n = len(env)
    return [x - (cumul[min(n, i + demi + 1)] - cumul[max(0, i - demi)]) / (min(n, i + demi + 1) - max(0, i - demi))
            for i, x in enumerate(env)]


def _correlation(a, b):
    n = len(a)
    if n < 20:
        return 0.0
    sa = math.sqrt(sum(x * x for x in a))
    sb = math.sqrt(sum(x * x for x in b))
    if sa < 1e-9 or sb < 1e-9:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (sa * sb)


def correlation_musique(prise, piste, fenetre_s=30):
    """Corrélation (0 à 1) entre l'enveloppe de la prise et celle de la piste jouée, avec le meilleur décalage : élevée
    si l'on entend la musique du casque dans la prise. Une fenêtre du milieu de la piste est cherchée dans la prise."""
    t = _enveloppe(prise)
    p = _enveloppe(piste)
    m = min(int(fenetre_s * ENVELOPPE_HZ), len(p), len(t))
    if m < 30:
        return 0.0
    debut = max(0, len(p) // 2 - m // 2)
    fen = p[debut:debut + m]
    meilleur = 0.0
    for dec in range(0, len(t) - m + 1):
        seg = t[dec:dec + m]
        c = _correlation(fen, seg)
        if c > meilleur:
            meilleur = c
    return meilleur


@dataclass
class Controle:
    fichier: Path
    duree_s: float | None = None
    crete_db: float | None = None
    bruit_db: float | None = None
    lufs: float | None = None
    fuite: float | None = None
    alertes: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.alertes


def controler_prise(fichier, cle_etape, piste=None):
    """Durée, saturation, bruit de fond et, si on connaît la piste jouée, la musique du casque entendue dans la prise.
    À lancer hors du fil de l'interface (quelques passes ffmpeg)."""
    f = Path(fichier).resolve()
    e = etape(cle_etape)
    c = Controle(f)
    m = controle.mesures(f)
    c.duree_s, c.crete_db, c.lufs = m["duree_s"], m["crete_db"], m["lufs"]
    niveaux = m["niveaux"]
    if c.duree_s is None or len(niveaux) < 5:
        c.alertes.append("fichier illisible ou trop court pour être analysé")
        return c
    c.bruit_db = controle._centile(niveaux, 0.05)
    minimum = 20 if cle_etape == "parole" else 30
    if e is not None and c.duree_s < min(minimum, 0.5 * e.duree_s):
        c.alertes.append(f"Prise trop courte ({formats.nombre(c.duree_s, 0)} s) : vise environ "
                         f"{formats.duree(e.duree_s)}.")
    if c.crete_db is not None and c.crete_db > -1:
        c.alertes.append("Saturation : la crête touche 0 dBFS. Recule à 20 cm ou baisse le volume d’entrée, "
                         "puis refais une prise.")
    if c.lufs is not None and c.lufs < -30:
        c.alertes.append(f"Niveau trop faible ({formats.nombre(c.lufs)} LUFS) : rapproche-toi du micro.")
    if c.bruit_db is not None and c.bruit_db > -45:
        c.alertes.append(f"Bruit de fond trop haut ({formats.nombre(c.bruit_db)} dBFS) : pièce plus calme, "
                         "ventilateur et fenêtre fermés.")
    if piste and Path(piste).is_file():
        c.fuite = correlation_musique(f, piste)
        if c.fuite >= SEUIL_FUITE:
            c.alertes.append(f"On entend la musique du casque dans la prise (ressemblance {formats.nombre(c.fuite, 2)} "
                             "avec la piste jouée) : casque mieux fermé, volume plus bas.")
    return c
