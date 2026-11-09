"""Transposition de la musique seule en demi-tons entiers (Rubber Band R3), et règle du décalage de la voix.

Deux parties, sans GPU :

1. Transposition. Rubber Band 3.x (bibliothèque partagée librubberband2) est appelé par ctypes : le filtre `rubberband`
   de ffmpeg n'expose PAS le choix du moteur (options : tempo, pitch, transients, detector, phase, window, smoothing,
   formant, pitchq, channels ; pas d'option « engine »), donc il ne sait faire que R2. Le binaire `rubberband`
   (paquet rubberband-cli) n'est pas installé. L'API C est stable et s'appelle sans en-têtes.
   - moteur « r3 » : OptionEngineFiner ; moteur « r2 » : OptionEngineFaster (comparaison) ;
   - rapport de temps 1,0, traitement hors ligne (study puis process), durée rendue à l'échantillon près.

2. Règle du décalage (voir regle_decalage) : décalage de la voix = transposition de la musique + octave, choisi pour que la
   voix convertie tombe dans la zone de confort de la voix cible (JSON de tessiture : p05, p10, p50, p90, p95, confort).
   Repli : un décalage par phrase (decalage_par_phrase), qui ne change qu'aux silences.

Ligne de commande : transposition.py {transposer,mesurer,regle} --aide.
"""
import argparse
import ctypes
import ctypes.util
import glob
import json
import math
import os
import subprocess
import sys
import time

SR = 44100

# --- Rubber Band par ctypes ----------------------------------------------------------------------------------------

OPT_PROCESS_OFFLINE = 0x00000000
OPT_THREADING_NEVER = 0x00010000
OPT_THREADING_AUTO = 0x00000000
OPT_TRANSIENTS_CRISP = 0x00000000
OPT_TRANSIENTS_MIXED = 0x00000100
OPT_TRANSIENTS_SMOOTH = 0x00000200
OPT_PHASE_LAMINAR = 0x00000000
OPT_PITCH_HIGH_QUALITY = 0x02000000
OPT_CHANNELS_APART = 0x00000000
OPT_CHANNELS_TOGETHER = 0x10000000
OPT_ENGINE_FASTER = 0x00000000
OPT_ENGINE_FINER = 0x20000000
OPT_FORMANT_SHIFTED = 0x00000000     # instruments : formants non préservés (la préservation vise la voix)
OPT_FORMANT_PRESERVED = 0x01000000

MOTEURS = {'r3': OPT_ENGINE_FINER, 'r2': OPT_ENGINE_FASTER}

_LIB = None


def bibliotheque():
    """La bibliothèque Rubber Band (librubberband.so.2 ou plus récente), ou OSError."""
    global _LIB
    if _LIB is not None:
        return _LIB
    candidats = [ctypes.util.find_library('rubberband')]
    for motif in ('/usr/lib/*-linux-gnu*/librubberband.so*', '/usr/lib/librubberband.so*', '/usr/local/lib/librubberband.so*'):
        candidats += sorted(glob.glob(motif))
    for c in candidats:
        if not c:
            continue
        try:
            lib = ctypes.CDLL(c)
        except OSError:
            continue
        lib.rubberband_new.restype = ctypes.c_void_p
        lib.rubberband_new.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_double, ctypes.c_double]
        lib.rubberband_delete.argtypes = [ctypes.c_void_p]
        lib.rubberband_set_expected_input_duration.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        lib.rubberband_set_max_process_size.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        flottants = ctypes.POINTER(ctypes.POINTER(ctypes.c_float))
        lib.rubberband_study.argtypes = [ctypes.c_void_p, flottants, ctypes.c_uint, ctypes.c_int]
        lib.rubberband_process.argtypes = [ctypes.c_void_p, flottants, ctypes.c_uint, ctypes.c_int]
        lib.rubberband_available.argtypes = [ctypes.c_void_p]
        lib.rubberband_available.restype = ctypes.c_int
        lib.rubberband_retrieve.argtypes = [ctypes.c_void_p, flottants, ctypes.c_uint]
        lib.rubberband_retrieve.restype = ctypes.c_uint
        lib.rubberband_get_engine_version.argtypes = [ctypes.c_void_p]
        lib.rubberband_get_engine_version.restype = ctypes.c_int
        _LIB = lib
        return lib
    raise OSError('librubberband introuvable (paquet librubberband2)')


def _pointeurs(canaux):
    """Tableau de pointeurs float* vers des vecteurs numpy contigus (un par canal)."""
    t = (ctypes.POINTER(ctypes.c_float) * len(canaux))()
    for i, c in enumerate(canaux):
        t[i] = c.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    return t


def transposer(x, demitons, sr=SR, moteur='r3', threads=False, bloc=8192):
    """x : tableau float (n,) ou (n, canaux) ; rend le même (float32), décalé de `demitons` (flottant permis, mais la règle
    du projet est : entiers), rapport de temps 1,0, même longueur à l'échantillon près.

    Rubber Band hors ligne : toute l'entrée est étudiée (study) puis traitée (process) ; la sortie est coupée ou complétée
    de zéros à la longueur d'entrée. threads=False : un seul cœur (OptionThreadingNever)."""
    import numpy as np
    x = np.asarray(x, np.float32)
    mono = x.ndim == 1
    x2 = x[:, None] if mono else x
    n, k = x2.shape
    if demitons == 0:
        return x.copy()
    lib = bibliotheque()
    options = (OPT_PROCESS_OFFLINE | MOTEURS[moteur] | OPT_PITCH_HIGH_QUALITY | OPT_FORMANT_SHIFTED
               | (OPT_THREADING_AUTO if threads else OPT_THREADING_NEVER) | OPT_TRANSIENTS_CRISP
               | (OPT_CHANNELS_TOGETHER if k > 1 else OPT_CHANNELS_APART))
    etat = lib.rubberband_new(sr, k, options, 1.0, 2.0 ** (demitons / 12.0))
    if not etat:
        raise RuntimeError('rubberband_new a échoué')
    entree = [np.ascontiguousarray(x2[:, c]) for c in range(k)]
    sortie = [np.zeros(n + 65536, np.float32) for _ in range(k)]
    ecrit = 0
    try:
        lib.rubberband_set_expected_input_duration(etat, n)
        for a in range(0, n, bloc):
            b = min(n, a + bloc)
            lib.rubberband_study(etat, _pointeurs([c[a:b] for c in entree]), b - a, int(b == n))
        for a in range(0, n, bloc):
            b = min(n, a + bloc)
            lib.rubberband_process(etat, _pointeurs([c[a:b] for c in entree]), b - a, int(b == n))
            while True:
                dispo = lib.rubberband_available(etat)
                if dispo <= 0:
                    break
                lot = [np.zeros(dispo, np.float32) for _ in range(k)]
                obtenu = lib.rubberband_retrieve(etat, _pointeurs(lot), dispo)
                for c in range(k):
                    if ecrit + obtenu > len(sortie[c]):
                        sortie[c] = np.concatenate([sortie[c], np.zeros(len(sortie[c]), np.float32)])
                    sortie[c][ecrit:ecrit + obtenu] = lot[c][:obtenu]
                ecrit += obtenu
        while True:   # fin de fichier : ce qui reste
            dispo = lib.rubberband_available(etat)
            if dispo <= 0:
                break
            lot = [np.zeros(dispo, np.float32) for _ in range(k)]
            obtenu = lib.rubberband_retrieve(etat, _pointeurs(lot), dispo)
            for c in range(k):
                if ecrit + obtenu > len(sortie[c]):
                    sortie[c] = np.concatenate([sortie[c], np.zeros(len(sortie[c]), np.float32)])
                sortie[c][ecrit:ecrit + obtenu] = lot[c][:obtenu]
            ecrit += obtenu
    finally:
        lib.rubberband_delete(etat)
    y = np.stack([s[:ecrit] for s in sortie], axis=1)
    y = y[:n] if len(y) >= n else np.pad(y, ((0, n - len(y)), (0, 0)))
    return y[:, 0] if mono else y


def version_moteur(moteur='r3'):
    """Version de moteur rapportée par la bibliothèque pour ce réglage (2 ou 3) : preuve que R3 est bien actif."""
    lib = bibliotheque()
    etat = lib.rubberband_new(SR, 1, OPT_PROCESS_OFFLINE | MOTEURS[moteur], 1.0, 1.0)
    try:
        return int(lib.rubberband_get_engine_version(etat))
    finally:
        lib.rubberband_delete(etat)


def transposer_ffmpeg(x, demitons, sr=SR):
    """Repli : filtre rubberband de ffmpeg (R2 seulement, pas de choix de moteur). Même longueur que l'entrée."""
    import numpy as np
    x = np.asarray(x, np.float32)
    x2 = x[:, None] if x.ndim == 1 else x
    n, k = x2.shape
    filtre = f'rubberband=pitch={2.0 ** (demitons / 12.0):.8f}:pitchq=quality:channels=together'
    r = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'f32le', '-ar', str(sr), '-ac', str(k), '-i', '-', '-af', filtre,
                        '-f', 'f32le', '-'], input=np.ascontiguousarray(x2).tobytes(), capture_output=True, check=True)
    y = np.frombuffer(r.stdout, np.float32).reshape(-1, k)
    y = y[:n] if len(y) >= n else np.pad(y, ((0, n - len(y)), (0, 0)))
    return y[:, 0] if x.ndim == 1 else y


def lire_audio(chemin, debut_s=0.0, duree_s=None):
    """Fichier audio -> (float32 (n, canaux), sr). soundfile si présent, sinon ffmpeg."""
    import numpy as np
    try:
        import soundfile as sf
        info = sf.info(chemin)
        a = int(debut_s * info.samplerate)
        b = None if duree_s is None else a + int(duree_s * info.samplerate)
        x, sr = sf.read(chemin, start=a, stop=b, dtype='float32', always_2d=True)
        return x, sr
    except ImportError:
        cmd = ['ffmpeg', '-v', 'error', '-ss', str(debut_s), '-i', str(chemin), *(['-t', str(duree_s)] if duree_s else []),
               '-f', 'f32le', '-ac', '2', '-ar', str(SR), '-']
        return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.float32).reshape(-1, 2), SR


def ecrire_audio(chemin, x, sr=SR):
    import soundfile as sf
    sf.write(chemin, x, sr, subtype='FLOAT' if str(chemin).endswith('.wav') else None)


def transposer_fichier(entree, sortie, demitons, moteur='r3'):
    x, sr = lire_audio(entree)
    t0 = time.time()
    y = transposer(x, demitons, sr, moteur)
    ecrire_audio(sortie, y, sr)
    return {'demitons': demitons, 'moteur': moteur, 'secondes': round(time.time() - t0, 2), 'echantillons': int(len(y)),
            'identique_en_duree': len(y) == len(x)}


# --- Mesures de qualité --------------------------------------------------------------------------------------------

def _stft_mag(x, n_fft=2048, hop=512):
    import numpy as np
    from scipy.signal import stft
    _, _, z = stft(x, nperseg=n_fft, noverlap=n_fft - hop, window='hann', boundary=None, padded=False)
    return np.abs(z)


def _bandes_tiers(mag, sr=SR, n_fft=2048, fmin=100.0, fmax=16000.0):
    """Énergie (dB) par bande de tiers d'octave et par trame : tableau (bandes, trames)."""
    import numpy as np
    f = np.fft.rfftfreq(n_fft, 1.0 / sr)
    n = int(math.floor(3 * math.log2(fmax / fmin)))
    bords = fmin * 2.0 ** (np.arange(n + 2) / 3.0)
    lignes = []
    for a, b in zip(bords[:-1], bords[1:]):
        m = (f >= a) & (f < b)
        lignes.append((mag[m] ** 2).sum(0) if m.any() else np.zeros(mag.shape[1]))
    return 10 * np.log10(np.array(lignes) + 1e-12)


def _mono(x):
    return x.mean(1) if x.ndim == 2 else x


def ecart_spectral_db(a, b, sr=SR):
    """Écart moyen absolu (dB) entre les spectrogrammes en tiers d'octave de a et b, sur les trames où a dépasse
    −60 dB sous sa crête (le silence ne compte pas) ; 0 = identiques."""
    import numpy as np
    A, B = _bandes_tiers(_stft_mag(_mono(a))), _bandes_tiers(_stft_mag(_mono(b)))
    n = min(A.shape[1], B.shape[1])
    A, B = A[:, :n], B[:, :n]
    total = 10 * np.log10((10 ** (A / 10)).sum(0) + 1e-12)
    actif = total > total.max() - 60
    return float(np.abs(A[:, actif] - B[:, actif]).mean())


def force_attaques(x, sr=SR):
    """Enveloppe d'attaque (1 valeur par trame de 11,6 ms) : somme sur les tiers d'octave de la hausse positive d'énergie (dB)
    d'une trame à la suivante. Ne dépend presque pas de la hauteur, donc comparable avant et après décalage."""
    import numpy as np
    L = _bandes_tiers(_stft_mag(_mono(x)))
    return np.maximum(np.diff(L, axis=1), 0).sum(0)


def mesure_transitoires(orig, decale):
    """Corrélation des enveloppes d'attaque (1 = attaques aux mêmes instants) et conservation de leur force (rapport des
    centiles 99 : < 1 = attaques adoucies)."""
    import numpy as np
    a, b = force_attaques(orig), force_attaques(decale)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    return {'correlation': round(float(np.corrcoef(a, b)[0, 1]), 4),
            'force_p99_rapport': round(float(np.percentile(b, 99) / (np.percentile(a, 99) + 1e-9)), 4),
            'force_moyenne_rapport': round(float(b.mean() / (a.mean() + 1e-9)), 4)}


def decalage_mesure_cents(orig, decale, sr=SR, bins_par_octave=120):
    """Décalage de hauteur réellement obtenu (cents), lu par intercorrélation des spectres moyens sur un axe logarithmique."""
    import numpy as np
    from scipy.signal import correlate

    def spectre_log(x):
        m = _stft_mag(_mono(x), 8192, 4096)
        moy = np.log10((m ** 2).mean(1) + 1e-12)
        f = np.fft.rfftfreq(8192, 1.0 / sr)
        axe = 100.0 * 2.0 ** (np.arange(0, int(bins_par_octave * math.log2(8000 / 100))) / bins_par_octave)
        s = np.interp(axe, f, moy)
        return s - np.convolve(s, np.ones(bins_par_octave) / bins_par_octave, 'same')   # enlève la pente générale
    a, b = spectre_log(orig), spectre_log(decale)
    c = correlate(b, a, mode='full')
    lags = np.arange(-len(a) + 1, len(a))
    garde = np.abs(lags) < bins_par_octave
    i = int(np.argmax(np.where(garde, c, -np.inf)))
    if 0 < i < len(c) - 1:   # sommet de la parabole
        d = c[i - 1] - 2 * c[i] + c[i + 1]
        delta = 0.5 * (c[i - 1] - c[i + 1]) / d if d else 0.0
    else:
        delta = 0.0
    return float((lags[i] + delta) * 1200.0 / bins_par_octave)


def erreur_accord_cents(orig, decale, demitons, sr=SR):
    """Écart d'accord (cents) du décalage obtenu à un nombre entier de demi-tons. Le spectre moyen d'une musique tonale se
    superpose à lui-même tous les demi-tons : on ne mesure donc que l'écart modulo 100 cents, ramené dans [-50, 50). Un moteur
    qui désaccorde la musique (rapport de hauteur inexact) y est vu ; la bonne valeur est 0."""
    m = decalage_mesure_cents(orig, decale, sr)
    return ((m - 100.0 * demitons + 50.0) % 100.0) - 50.0


def ecart_aigus_db(a, b, sr=SR, au_dessus_hz=10000.0):
    """Différence moyenne signée (dB, b moins a) de l'énergie des tiers d'octave au-dessus de au_dessus_hz, sur les trames
    actives : négatif = aigus perdus."""
    import numpy as np
    A, B = _bandes_tiers(_stft_mag(_mono(a))), _bandes_tiers(_stft_mag(_mono(b)))
    n = min(A.shape[1], B.shape[1])
    A, B = A[:, :n], B[:, :n]
    centres = 100.0 * 2.0 ** ((np.arange(A.shape[0]) + 0.5) / 3.0)
    haut = centres > au_dessus_hz
    total = 10 * np.log10((10 ** (A / 10)).sum(0) + 1e-12)
    actif = total > total.max() - 60
    return float((B[haut][:, actif] - A[haut][:, actif]).mean())


def niveaux(x):
    import numpy as np
    m = _mono(x).astype(np.float64)
    return {'rms_db': round(float(10 * np.log10((m ** 2).mean() + 1e-12)), 2),
            'crete_db': round(float(20 * np.log10(np.abs(x).max() + 1e-12)), 2)}


def mesurer_qualite(x, demitons, sr=SR, moteurs=('r3', 'r2'), aller_retour=True):
    """Qualité d'une transposition par moteur : temps (mur et CPU du processus), longueur, justesse du décalage, écart
    spectral à l'original après aller-retour (+n puis −n : l'original est la référence), attaques, niveau."""
    res = {}
    for mot in moteurs:
        t0, c0 = time.time(), time.process_time()
        y = transposer(x, demitons, sr, mot)
        mur, cpu = time.time() - t0, time.process_time() - c0
        fiche = {'secondes_mur': round(mur, 2), 'secondes_cpu': round(cpu, 2), 'rtf': round(mur / (len(x) / sr), 3),
                 'ecart_longueur_echantillons': int(len(y) - len(x)),
                 'erreur_accord_cents': round(erreur_accord_cents(x, y, demitons, sr), 1),
                 'transitoires': mesure_transitoires(x, y), 'niveau_avant': niveaux(x), 'niveau_apres': niveaux(y)}
        if aller_retour:
            retour = transposer(y, -demitons, sr, mot)
            fiche['aller_retour_ecart_spectral_db'] = round(ecart_spectral_db(x, retour, sr), 3)
            fiche['aller_retour_transitoires'] = mesure_transitoires(x, retour)
            fiche['aller_retour_aigus_db'] = round(ecart_aigus_db(x, retour, sr), 2)
        res[mot] = fiche
    return res


# --- Règle du décalage de la voix quand la chanson est transposée -------------------------------------------------

DEMITONS_MUSIQUE_MAX = 5      # au-delà de ±5 dt, la musique transposée s'entend (rapport 03 §6)
POIDS = {'hors_confort': 3.0, 'centre': 0.5, 'transposition': 0.15, 'octave': 0.5}


def dt(f_hz, ref_hz):
    """Écart en demi-tons de f_hz à ref_hz."""
    return 12.0 * math.log2(f_hz / ref_hz)


def _cout_voix(source, tess, d, poids):
    """Coût d'un décalage total d (demi-tons) de la voix : débordement des centiles bas et haut de la voix décalée hors de
    la zone de confort, plus écart de sa médiane au centre (géométrique) de la zone."""
    bas, haut = tess['confort']
    centre = math.sqrt(bas * haut)
    deborde = 0.0
    for cle in ('p10', 'p90'):
        if cle in source:
            f = source[cle] * 2.0 ** (d / 12.0)
            deborde += max(0.0, dt(bas, f)) + max(0.0, dt(f, haut))
    med = source['p50'] * 2.0 ** (d / 12.0)
    return poids['hors_confort'] * deborde + poids['centre'] * abs(dt(med, centre))


def regle_decalage(source, tess, transposer_musique=True, max_musique=DEMITONS_MUSIQUE_MAX, poids=None, etendue=24):
    """Décalage de la voix = transposition de la musique + octave.

    source : {'p10','p50','p90'} (Hz) de la voix d'origine (F0 voisée, RMVPE) ; tess : JSON de tessiture de la voix cible
    ({'p05','p10','p50','p90','p95','confort': [bas, haut]}, Hz).

    Principe :
    - d = décalage total de la voix (demi-tons entiers), cherché sur [-etendue, +etendue] pour que les centiles p10 et p90 de la
      voix convertie tombent dans la zone de confort et sa médiane près du centre de la zone ;
    - la musique n'est transposée que de T = d ramené dans [-6, +6] (d modulo 12) : la voix reste dans la tonalité de la
      musique, et l'écart d - T est une octave entière (k octaves, voix plus haut ou plus bas que la musique) ;
    - coût de la musique : POIDS['transposition'] x |T| ; coût d'une octave de voix : POIDS['octave'] x |k| ; |T| est borné à
      max_musique (au-delà on préfère une autre octave) ;
    - transposer_musique faux : T = 0, seules les octaves (d multiple de 12) : le comportement actuel (-12, 0, +12) mais sans
      borne de ±12.
    Rend un dict : decalage_voix_dt (entier, à donner au convertisseur), transposition_musique_dt, octave_voix, f0_convertie
    (p10/p50/p90 Hz), dans_confort (p10 et p90 dedans), ecart_confort_dt (débordement cumulé), cout, alternatives (3 suivantes)."""
    poids = {**POIDS, **(poids or {})}
    candidats = []
    for d in range(-etendue, etendue + 1):
        t = d if not transposer_musique else ((d + 6) % 12) - 6
        if not transposer_musique and d % 12:
            continue
        if abs(t) > max_musique:
            continue
        k = (d - t) // 12
        cout = (_cout_voix(source, tess, d, poids) + poids['transposition'] * abs(t) + poids['octave'] * abs(k)
                + 1e-3 * abs(d))
        candidats.append((cout, d, t, k))
    candidats.sort()
    bas, haut = tess['confort']

    def fiche(c):
        cout, d, t, k = c
        f = {cle: round(source[cle] * 2.0 ** (d / 12.0), 1) for cle in ('p10', 'p50', 'p90') if cle in source}
        dehors = sum(max(0.0, dt(bas, v)) + max(0.0, dt(v, haut)) for cle, v in f.items() if cle in ('p10', 'p90'))
        return {'decalage_voix_dt': d, 'transposition_musique_dt': t, 'octave_voix': k, 'f0_convertie': f,
                'dans_confort': dehors < 1e-9, 'ecart_confort_dt': round(dehors, 2), 'cout': round(cout, 3)}
    meilleur = fiche(candidats[0])
    meilleur['alternatives'] = [fiche(c) for c in candidats[1:4]]
    return meilleur


def _cout_phrase(phrase, tess, d, poids):
    return _cout_voix({cle: phrase[cle] for cle in ('p10', 'p50', 'p90') if cle in phrase}, tess, d, poids)


def decalage_par_phrase(phrases, tess, decalage_global, ecart_max=3, silence_min_s=0.25, pas_max=2, lissage=0.6, poids=None):
    """Repli : un décalage entier par phrase, au plus ecart_max demi-tons du décalage global, avec continuité.

    phrases : liste triée de {'debut_s','fin_s','p10','p50','p90'} (F0 de la voix d'origine dans la phrase, Hz).
    - Le décalage ne peut changer d'une phrase à la suivante que si le silence qui les sépare dure au moins silence_min_s,
      sinon il reste le même (jamais de saut au milieu d'un son).
    - Un changement ne dépasse pas pas_max demi-tons ; chaque demi-ton de changement coûte `lissage` (continuité).
    - Programmation dynamique (Viterbi) sur le coût de zone de confort de chaque phrase.
    Rend une liste de {'debut_s','fin_s','decalage_dt'} (et 'change_a_s' quand il change).
    Valable surtout si la musique n'est PAS transposée en conséquence : le décalage s'écarte alors de la tonalité de la
    musique (voir MIXAGE.md) ; pour rester dans la tonalité, passer harmonique=... n'est pas proposé : on garde la règle
    globale (octaves seules)."""
    poids = {**POIDS, **(poids or {})}
    if not phrases:
        return []
    etats = list(range(decalage_global - ecart_max, decalage_global + ecart_max + 1))
    inf = float('inf')
    couts = [[_cout_phrase(p, tess, d, poids) for d in etats] for p in phrases]
    best = [couts[0][:]]
    retour = [[None] * len(etats)]
    for i in range(1, len(phrases)):
        libre = phrases[i]['debut_s'] - phrases[i - 1]['fin_s'] >= silence_min_s
        ligne, prec = [], []
        for j, d in enumerate(etats):
            meilleur, arg = inf, None
            for jj, dd in enumerate(etats):
                if dd != d and (not libre or abs(dd - d) > pas_max):
                    continue
                v = best[i - 1][jj] + lissage * abs(dd - d)
                if v < meilleur:
                    meilleur, arg = v, jj
            ligne.append(meilleur + couts[i][j])
            prec.append(arg)
        best.append(ligne)
        retour.append(prec)
    j = min(range(len(etats)), key=lambda q: best[-1][q])
    choix = [0] * len(phrases)
    for i in range(len(phrases) - 1, -1, -1):
        choix[i] = j
        j = retour[i][j] if retour[i][j] is not None else j
    sortie, precedent = [], None
    for p, j in zip(phrases, choix):
        d = etats[j]
        f = {'debut_s': p['debut_s'], 'fin_s': p['fin_s'], 'decalage_dt': d}
        if precedent is not None and d != precedent:
            f['change_a_s'] = p['debut_s']
        sortie.append(f)
        precedent = d
    return sortie


def phrases_depuis_f0(temps_s, f0_hz, silence_min_s=0.25, f0_min=50.0):
    """Phrases (voisées séparées par au moins silence_min_s) à partir d'une courbe de F0 (0 ou NaN = non voisé) : liste de
    {'debut_s','fin_s','p10','p50','p90'}. Utile avec courbes.json ou RMVPE."""
    import numpy as np
    t, f = np.asarray(temps_s, float), np.asarray(f0_hz, float)
    voise = np.isfinite(f) & (f > f0_min)
    idx = np.flatnonzero(voise)
    if not len(idx):
        return []
    coupures = np.flatnonzero(np.diff(t[idx]) > silence_min_s)
    debuts = np.r_[idx[0], idx[coupures + 1]]
    fins = np.r_[idx[coupures], idx[-1]]
    sortie = []
    for a, b in zip(debuts, fins):
        v = f[a:b + 1][voise[a:b + 1]]
        if len(v) < 5:
            continue
        sortie.append({'debut_s': float(t[a]), 'fin_s': float(t[b]), 'p10': float(np.percentile(v, 10)),
                       'p50': float(np.percentile(v, 50)), 'p90': float(np.percentile(v, 90))})
    return sortie


def charger_tessiture(chemin):
    """JSON de tessiture (tessiture.py) ; vérifie le schéma attendu."""
    with open(chemin) as f:
        d = json.load(f)
    for cle in ('p05', 'p10', 'p50', 'p90', 'p95', 'confort'):
        if cle not in d:
            raise ValueError(f'tessiture : « {cle} » manque')
    if len(d['confort']) != 2 or d['confort'][0] >= d['confort'][1]:
        raise ValueError('tessiture : confort = [bas, haut] attendu')
    return d


# --- Ligne de commande ---------------------------------------------------------------------------------------------

def _principal(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sp = p.add_subparsers(dest='cmd', required=True)
    t = sp.add_parser('transposer', help='musique seule transposée en demi-tons entiers')
    t.add_argument('entree')
    t.add_argument('sortie')
    t.add_argument('demitons', type=int)
    t.add_argument('--moteur', choices=sorted(MOTEURS), default='r3')
    m = sp.add_parser('mesurer', help='qualité de la transposition (R2 et R3) sur un extrait de musique seule')
    m.add_argument('entree')
    m.add_argument('--debut', type=float, default=30.0)
    m.add_argument('--duree', type=float, default=40.0)
    m.add_argument('--demitons', type=int, nargs='+', default=[1, 2, 3, 5, -1, -2, -3, -5])
    m.add_argument('--json', help='fichier des mesures')
    r = sp.add_parser('regle', help='décalage de la voix, transposition de la musique et octave')
    r.add_argument('tessiture', help='JSON de tessiture de la voix cible')
    r.add_argument('--p10', type=float, required=True)
    r.add_argument('--p50', type=float, required=True)
    r.add_argument('--p90', type=float, required=True)
    r.add_argument('--sans-transposition', action='store_true', help='octaves seules (comportement actuel)')
    a = p.parse_args(argv)
    if a.cmd == 'transposer':
        print(json.dumps(transposer_fichier(a.entree, a.sortie, a.demitons, a.moteur), ensure_ascii=False))
    elif a.cmd == 'mesurer':
        x, sr = lire_audio(a.entree, a.debut, a.duree)
        sortie = {'fichier': os.path.basename(a.entree), 'debut_s': a.debut, 'duree_s': round(len(x) / sr, 2),
                  'versions_moteur': {m: version_moteur(m) for m in MOTEURS}, 'decalages': {}}
        for n in a.demitons:
            sortie['decalages'][f'{n:+d}'] = mesurer_qualite(x, n, sr)
            print(f'{n:+d} dt : ' + json.dumps({m: {k: v for k, v in f.items() if k in ("secondes_mur", "erreur_accord_cents",
                  "aller_retour_ecart_spectral_db")} for m, f in sortie['decalages'][f"{n:+d}"].items()}), flush=True)
        if a.json:
            os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
            with open(a.json, 'w') as f:
                json.dump(sortie, f, ensure_ascii=False, indent=1)
    elif a.cmd == 'regle':
        res = regle_decalage({'p10': a.p10, 'p50': a.p50, 'p90': a.p90}, charger_tessiture(a.tessiture),
                             transposer_musique=not a.sans_transposition)
        print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    _principal()
