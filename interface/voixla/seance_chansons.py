"""Chansons du domaine public de la séance (étape 5) : notes, paroles, transposition, accords et synthèse (numpy).

- Les notes sont écrites en solfège (« do4 1 » : do de l'octave 4, une noire ; « sol4 ½ » : une croche). do4 = MIDI 60.
- Les paroles sont écrites syllabe par syllabe, les syllabes séparées par « / » ; les espaces et la ponctuation restent
  dans le texte (« Au/ clair/ de/ la/ lu/ne,/ mon/ a/mi/ Pier/rot, »). Une syllabe par note : l'élision d'un e muet
  devant une voyelle fait une seule syllabe (« re un », « le y »). compter_syllabes() compte à la française, pour que
  le test vérifie les paroles écrites à la main ; EXCEPTIONS corrige les mots que la règle ne devine pas.
- La chanson est transposée en demi-tons entiers pour que sa note médiane tombe sur le centre C de la voix.
- Les accords I, IV et V (do majeur, puis transposés) sont posés par blanches d'après la mélodie.
Rien d'ici ne touche au disque : l'écriture des pistes et des bips est dans seance.py.
"""

import math
import re
from dataclasses import dataclass

TEMPO_BPM = 88
RATE = 44100
BATTEMENTS_PAR_ACCORD = 2          # des accords en blanches

# --- Notes ---------------------------------------------------------------------------------------------------------

_DEGRES = {"do": 0, "ré": 2, "re": 2, "mi": 4, "fa": 5, "sol": 7, "la": 9, "si": 11}


def midi_de(nom):
    """« do4 » → 60, « la3 » → 57, « ré4 » → 62."""
    m = re.fullmatch(r"(do|ré|re|mi|fa|sol|la|si)(\d)", nom.strip().lower())
    if m is None:
        raise ValueError(f"note inconnue : {nom!r}")
    return 12 * (int(m.group(2)) + 1) + _DEGRES[m.group(1)]


def lire_notes(texte):
    """« do4 1, ré4 ½, mi4 2 » → ((60, 1.0), (62, 0.5), (64, 2.0)) : (MIDI, durée en temps ; 1 = noire)."""
    sortie = []
    for morceau in texte.split(","):
        nom, duree = morceau.split()
        sortie.append((midi_de(nom), 0.5 if duree == "½" else float(duree)))
    return tuple(sortie)


def hauteur_hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def midi_de_hz(f):
    return 69 + 12 * math.log2(f / 440.0)


@dataclass(frozen=True)
class LigneChant:
    notes: tuple            # ((midi, durée en temps), …)
    syllabes: tuple         # une syllabe par note, avec ses espaces et sa ponctuation

    @property
    def texte(self):
        return "".join(self.syllabes).strip()

    @property
    def temps(self):
        return sum(d for _m, d in self.notes)


def ligne(notes, syllabes):
    return LigneChant(lire_notes(notes), tuple(syllabes.split("/")))


@dataclass(frozen=True)
class Chanson:
    cle: str                # « au-clair-de-la-lune » : sert au nom du fichier conseillé (« 5-au-clair-de-la-lune »)
    titre: str
    lignes: tuple           # les lignes chantées, dans l'ordre, une fois
    passes: int = 1         # nombre de fois que la chanson est chantée de suite

    @property
    def nom_fichier(self):
        return f"5-{self.cle}"

    def lignes_jouees(self):
        return self.lignes * self.passes

    @property
    def temps(self):
        return sum(l.temps for l in self.lignes_jouees())

    def duree_s(self, tempo=TEMPO_BPM):
        return self.temps * 60.0 / tempo


# --- Au clair de la lune (domaine public) ----------------------------------------------------------------------------

PHRASE_A = "do4 1, do4 1, do4 1, ré4 1, mi4 2, ré4 2, do4 1, mi4 1, ré4 1, ré4 1, do4 4"
PHRASE_B = "ré4 1, ré4 1, ré4 1, ré4 1, la3 2, la3 2, ré4 1, do4 1, si3 1, la3 1, sol3 4"
ORDRE_COUPLET = (PHRASE_A, PHRASE_A, PHRASE_B, PHRASE_A)

COUPLET_1 = (
    "Au/ clair/ de/ la/ lu/ne,/ mon/ a/mi/ Pier/rot,",
    "Prê/te-/moi/ ta/ plu/me/ pour/ é/cri/re un/ mot.",
    "Ma/ chan/del/le est/ mor/te,/ je/ n'ai/ plus/ de/ feu.",
    "Ou/vre-/moi/ ta/ por/te/ pour/ l'a/mour/ de/ Dieu.",
)
COUPLET_2 = (
    "Au/ clair/ de/ la/ lu/ne,/ Pier/rot/ ré/pon/dit :",
    "« Je/ n'ai/ pas/ de/ plu/me,/ je/ suis/ dans/ mon/ lit.",
    "Va/ chez/ la/ voi/si/ne,/ je/ crois/ qu'el/le y/ est,",
    "Car/ dans/ sa/ cui/si/ne/ on/ bat/ le/ bri/quet. »",
)
AU_CLAIR_DE_LA_LUNE = Chanson(
    "au-clair-de-la-lune", "Au clair de la lune",
    tuple(ligne(n, s) for couplet in (COUPLET_1, COUPLET_2) for n, s in zip(ORDRE_COUPLET, couplet)))

# --- Frère Jacques (domaine public) : chaque groupe est joué deux fois -----------------------------------------------

_GROUPES_JACQUES = (
    ("do4 1, ré4 1, mi4 1, do4 1", "Frè/re/ Jac/ques,"),
    ("mi4 1, fa4 1, sol4 2", "Dor/mez-/vous ?"),
    ("sol4 ½, la4 ½, sol4 ½, fa4 ½, mi4 1, do4 1", "Son/nez/ les/ ma/ti/nes,"),
    ("do4 1, sol3 1, do4 2", "Ding,/ dang,/ dong."),
)
FRERE_JACQUES = Chanson("frere-jacques", "Frère Jacques",
                        tuple(ligne(n, s) for n, s in _GROUPES_JACQUES for _fois in range(2)), passes=2)

CHANSONS = (AU_CLAIR_DE_LA_LUNE, FRERE_JACQUES)


def chanson(cle):
    return next((c for c in CHANSONS if c.cle == cle), None)


# --- Compter les syllabes à la française -----------------------------------------------------------------------------

VOYELLES = set("aeiouyàâäéèêëîïôöûùüœæ")
# mot (en minuscules, sans ponctuation) → nombre de syllabes chantées, quand la règle ne le devine pas :
# « cuisine on » : le e muet est tenu sur la longue note (ré, 2 temps) au lieu d'être élidé.
EXCEPTIONS = {"cuisine": 3}


def _mots(texte):
    return [m for m in re.findall(r"[a-zàâäéèêëîïôöûùüœæç]+", texte.lower().replace("’", "'"))]


def _commence_par_voyelle(mot):
    return bool(mot) and (mot[0] in VOYELLES or mot[0] == "h")


def compter_mot(mot, suivant=None):
    """Syllabes d'un mot : un groupe de voyelles = une syllabe (le u de « qu » ne compte pas) ; le e muet final est
    chanté, sauf devant une voyelle (élision) quand le mot a d'autres syllabes."""
    if mot in EXCEPTIONS:
        return EXCEPTIONS[mot]
    lettres = []
    for i, c in enumerate(mot):
        lettres.append(c in VOYELLES and not (c == "u" and i > 0 and mot[i - 1] == "q"))
    groupes = len([i for i, v in enumerate(lettres) if v and (i == 0 or not lettres[i - 1])])
    muet = re.search(r"[^aeiouyàâäéèêëîïôöûùüœæ]e[s]?$", mot) is not None and groupes > 1
    if muet and suivant is not None and _commence_par_voyelle(suivant):
        groupes -= 1
    return groupes


def compter_syllabes(texte):
    """Nombre de syllabes chantées d'une ligne de texte (élisions comprises)."""
    mots = _mots(texte)
    return sum(compter_mot(m, mots[i + 1] if i + 1 < len(mots) else None) for i, m in enumerate(mots))


# --- Transposition ----------------------------------------------------------------------------------------------------

def note_mediane(chanson):
    """La note médiane (MIDI, éventuellement entre deux notes) de la mélodie, chaque note comptée une fois."""
    notes = sorted(m for l in chanson.lignes_jouees() for m, _d in l.notes)
    n = len(notes)
    return notes[n // 2] if n % 2 else (notes[n // 2 - 1] + notes[n // 2]) / 2


def transposition(chanson, centre_hz):
    """Demi-tons entiers (+ : plus haut) qui amènent la note médiane de la mélodie sur le centre C de la voix."""
    return int(round(midi_de_hz(centre_hz) - note_mediane(chanson)))


def nom_note(midi):
    noms = ("do", "do♯", "ré", "ré♯", "mi", "fa", "fa♯", "sol", "sol♯", "la", "la♯", "si")
    midi = int(round(midi))
    return f"{noms[midi % 12]}{midi // 12 - 1}"


# --- Calendrier : quand chaque note, chaque syllabe ------------------------------------------------------------------

def calendrier(chanson, tempo=TEMPO_BPM):
    """[(ligne, [(début s, fin s, midi, syllabe)])] : les notes de chaque ligne jouée, en secondes depuis le début de la
    chanson (avant les bips)."""
    seconde = 60.0 / tempo
    t, sortie = 0.0, []
    for l in chanson.lignes_jouees():
        notes = []
        for (m, d), syl in zip(l.notes, l.syllabes):
            notes.append((t * seconde, (t + d) * seconde, m, syl))
            t += d
        sortie.append((l, notes))
    return sortie


def paroles(chanson, centre_s=0.0, tempo=TEMPO_BPM, langue="fr"):
    """Les paroles synchronisées de la chanson (voixla.paroles.Paroles), une ligne à la fois, avec une syllabe par note
    (le karaoké remplit la syllabe en cours). centre_s : décalage de départ (les 5 s de bips)."""
    from .paroles import Couche, Ligne, Mot, Paroles
    lignes = []
    for l, notes in calendrier(chanson, tempo):
        mots = [Mot(centre_s + a, centre_s + b, syl.strip()) for a, b, _m, syl in notes]
        lignes.append(Ligne(centre_s + notes[0][0], centre_s + notes[-1][1], l.texte, mots))
    return Paroles(Couche(langue, "paroles de la séance, domaine public", lignes), {}, "seance")


# --- Accords I, IV et V ---------------------------------------------------------------------------------------------

ACCORDS = (("I", 0, (0, 4, 7)), ("V", 7, (7, 11, 2)), ("IV", 5, (5, 9, 0)))     # (nom, fondamentale, classes), do majeur


def choisir_accords(chanson, tempo=TEMPO_BPM):
    """[(début s, fin s, nom de l'accord)] : un accord par blanche. Celui dont les notes recouvrent le plus la mélodie
    (à égalité, celui d'avant pour ne pas changer pour rien, puis I, V, IV) ; le dernier est I."""
    seconde = 60.0 / tempo
    notes = [(a, b, m % 12) for _l, ns in calendrier(chanson, tempo) for a, b, m, _s in ns]
    total = notes[-1][1]
    pas = BATTEMENTS_PAR_ACCORD * seconde
    n = int(round(total / pas))
    accords, precedent = [], None
    for k in range(n):
        d, f = k * pas, (k + 1) * pas
        score = {nom: 0.0 for nom, _r, _c in ACCORDS}
        for a, b, pc in notes:
            recouvre = min(b, f) - max(a, d)
            if recouvre > 0:
                for nom, _r, classes in ACCORDS:
                    if pc in classes:
                        score[nom] += recouvre
        meilleur = max(score.values())
        gagnants = [nom for nom, _r, _c in ACCORDS if score[nom] >= meilleur - 1e-9]
        nom = precedent if precedent in gagnants else gagnants[0]
        accords.append((d, f, nom))
        precedent = nom
    if accords:
        accords[-1] = (*accords[-1][:2], "I")
    return accords


def notes_accord(nom, transposition=0):
    """Les notes MIDI d'un accord posé dans do majeur, puis transposé : une basse et la triade en position serrée."""
    racine, classes = next((r, c) for n, r, c in ACCORDS if n == nom)
    basse = 36 + racine
    haut = []
    for pc in classes:
        m = 50 + (pc - 50) % 12
        haut.append(m)
    return [basse + transposition] + [m + transposition for m in haut]


# --- Synthèse -------------------------------------------------------------------------------------------------------

def _np():
    import numpy
    return numpy


def piano(frequence, duree_s, force=1.0, rate=RATE):
    """Une note douce façon piano : fondamentale et harmoniques qui s'éteignent à leur rythme, attaque de 8 ms,
    extinction courte à la fin de la note (rien ne claque)."""
    np = _np()
    relache = 0.12
    n = int((duree_s + relache) * rate)
    t = np.arange(n) / rate
    s = np.zeros(n)
    for k, (amp, tau) in enumerate(((1.0, 1.4), (0.42, 0.9), (0.22, 0.55), (0.1, 0.35), (0.05, 0.25)), 1):
        if frequence * k < rate / 2.2:
            s += amp * np.sin(2 * np.pi * frequence * k * t) * np.exp(-t / tau)
    env = np.minimum(1.0, t / 0.008)
    fin = np.clip((duree_s + relache - t) / relache, 0.0, 1.0)
    return 0.30 * force * s * env * fin


def _poser(sortie, debut_s, signal, rate=RATE):
    i = int(round(debut_s * rate))
    j = min(len(sortie), i + len(signal))
    if j > i:
        sortie[i:j] += signal[:j - i]


def rendre(chanson, centre_hz, avec_melodie, tempo=TEMPO_BPM, rate=RATE, queue_s=2.5):
    """Le son de la chanson (tableau mono, flottant, à partir de 0 s, sans bips) dans la hauteur de la voix : les accords
    en blanches, plus la mélodie au piano (avec_melodie) ou un léger métronome (accompagnement seul)."""
    np = _np()
    decalage = transposition(chanson, centre_hz)
    seconde = 60.0 / tempo
    total = chanson.temps * seconde
    sortie = np.zeros(int((total + queue_s) * rate))
    for d, f, nom in choisir_accords(chanson, tempo):
        duree = f - d + (queue_s if f >= total - 1e-6 else 0.0)
        for i, m in enumerate(notes_accord(nom, decalage)):
            _poser(sortie, d, piano(hauteur_hz(m), duree, 0.55 if i == 0 else 0.4, rate), rate)
    if avec_melodie:
        for _l, notes in calendrier(chanson, tempo):
            for a, b, m, _s in notes:
                _poser(sortie, a, piano(hauteur_hz(m + decalage), (b - a) * 0.97, 1.0, rate), rate)
    else:
        for k in range(int(round(chanson.temps))):
            f = 1500.0 if k % 4 == 0 else 1000.0
            n = int(0.03 * rate)
            t = np.arange(n) / rate
            _poser(sortie, k * seconde, 0.16 * np.sin(2 * np.pi * f * t) * np.exp(-t / 0.008), rate)
    return sortie
