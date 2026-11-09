"""Mixage v3 (rapport de recherche sur le mixage, § 8) : voix convertie + musique seule, sans GPU.

Chaîne de la voix : passe-haut 90 Hz, EQ douce plafonnée (forme à grande échelle seulement), compression douce 2:1, dé-esseur
réglé sur la voix d'origine, niveau qui suit le rapport voix/musique de l'original par segments de 3 s (+1 dB par défaut),
reverb neuve sur la voix seule réglée sur le RT60 et le pré-délai mesurés de la voix d'origine (RT60 plafonné à 2 s), chœurs à
−6 dB en stéréo, loudness intégré égal à celui de l'original, limiteur −1 dBTP.

Variantes d'écoute à l'aveugle (lettres du rapport) :
  A  mix v2 actuel (témoin)            B  v3                       C  v3 + musique transposée (voix reconvertie : GPU)
  D  v3, RIR par déconvolution (piste de reverb retirée requise)   E  v3 + 25 % de la reverb d'origine (idem)
  F  v3 sans reverb                    G  voix +4 / +1 / 0 dB       H  correction de hauteur (non produite ici)
  I  EQ plafonnée à 3 / 1,5 / 0 dB
Chaque variante reçoit une lettre tirée au hasard ; la correspondance va dans un fichier à part. Même loudness pour toutes.

Dépendances : numpy, scipy, soundfile, pyloudnorm (venv dédié), ffmpeg pour les mp3.
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

SR = 44100

# Réglages par défaut (rapport 03 § 8)
VOIX_DB = 1.0              # voix = rapport voix/musique de l'original + 1 dB
SEGMENT_S = 3.0            # segments du rapport voix/musique
LISSAGE_S = 1.0
HP_HZ = 90.0
EQ_PLAFOND_DB = 3.0        # plafond général de l'EQ de forme
EQ_PLAFOND_MEDIUM_DB = 2.0  # entre 250 Hz et 4 kHz : jamais plus
EQ_BANDE_MEDIUM = (250.0, 4000.0)
EQ_BANDE_ACTIVE = (150.0, 10000.0)
COMPRESSION = {'seuil_db': -20.0, 'ratio': 2.0, 'attaque_ms': 15.0, 'relachement_ms': 150.0}
DEESSEUR = {'bande_hz': (5000.0, 9000.0), 'reduction_max_db': 6.0, 'pente': 0.7}
REVERB = {'rt60_max_s': 2.0, 'predelai_defaut_ms': 30.0, 'niveau_defaut_db': -17.0, 'niveau_max_db': -13.0,
          'passe_haut_hz': 400.0, 'passe_bas_hz': 7000.0, 'fin_de_phrase_db': 2.0}
CHOEURS = {'niveau_db': -6.0, 'reverb_en_plus_db': 3.0, 'passe_bas_hz': 6000.0}
PLAFOND_DBTP = -1.0


# --- Entrées / sorties ---------------------------------------------------------------------------------------------

def lire(chemin, debut_s=0.0, duree_s=None):
    """Fichier audio -> float32 (n, canaux) à 44,1 kHz ; mono et stéréo conservés."""
    import soundfile as sf
    info = sf.info(str(chemin))
    a = int(debut_s * info.samplerate)
    b = None if duree_s is None else a + int(duree_s * info.samplerate)
    x, sr = sf.read(str(chemin), start=a, stop=b, dtype='float32', always_2d=True)
    if sr != SR:
        from scipy.signal import resample_poly
        g = math.gcd(sr, SR)
        x = resample_poly(x, SR // g, sr // g, axis=0).astype(np.float32)
    return x


def mono(x):
    x = np.asarray(x)
    return x.mean(1).astype(np.float32) if x.ndim == 2 else x.astype(np.float32)


def stereo(x):
    x = np.asarray(x, np.float32)
    return np.stack([x, x], 1) if x.ndim == 1 else (np.concatenate([x, x], 1) if x.shape[1] == 1 else x)


def caler(x, n):
    """x coupé ou complété de zéros à n échantillons."""
    return x[:n] if len(x) >= n else np.pad(x, [(0, n - len(x))] + [(0, 0)] * (x.ndim - 1))


def db(v, plancher=1e-12):
    return 10.0 * np.log10(np.maximum(v, plancher))


def gain_db(g_db):
    return 10.0 ** (np.asarray(g_db) / 20.0)


def ecrire_mp3(chemin, x, sr=SR, debit='320k'):
    """x float32 (n, 2) -> mp3 par ffmpeg (entrée f32le sur un tube : aucun fichier intermédiaire)."""
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(sr), '-ac', '2', '-i', '-', '-c:a', 'libmp3lame',
           '-b:a', debit, str(chemin)]
    subprocess.run(cmd, input=np.ascontiguousarray(np.clip(x, -1, 1), np.float32).tobytes(), check=True, capture_output=True)


# --- Niveaux -------------------------------------------------------------------------------------------------------

def trames_db(x, sr=SR, trame_s=0.01):
    """Niveau (dB, puissance) de chaque trame de 10 ms d'un signal mono."""
    p = int(sr * trame_s)
    n = len(x) // p * p
    return db((x[:n].astype(np.float64).reshape(-1, p) ** 2).mean(1))


def niveau_actif_db(x, sr=SR, presence_db=35.0):
    """Niveau RMS (dBFS) des trames de 10 ms à moins de presence_db sous le centile 95 : le niveau « quand ça chante »."""
    L = trames_db(mono(x), sr)
    if not len(L):
        return -120.0
    act = L > L.max() - presence_db if len(L) < 20 else L > np.percentile(L, 95) - presence_db
    return float(db((10 ** (L[act] / 10)).mean())) if act.any() else -120.0


def niveaux_segments(x, seg_s=SEGMENT_S, sr=SR, presence_db=35.0, plancher_db=-70.0):
    """Niveau actif (dB) par segment de seg_s ; NaN où le segment est (presque) vide : moins de 15 % de trames actives.
    Les trames actives sont celles à moins de presence_db sous le centile 95 du signal ENTIER."""
    L = trames_db(mono(x), sr)
    seuil = max(float(np.percentile(L, 95)) - presence_db, plancher_db) if len(L) else plancher_db
    par = int(round(seg_s / 0.01))
    n = int(math.ceil(len(L) / par))
    sortie = np.full(n, np.nan)
    for i in range(n):
        s = L[i * par:(i + 1) * par]
        act = s > seuil
        if act.mean() >= 0.15:
            sortie[i] = db((10 ** (s[act] / 10)).mean())
    return sortie


def niveaux_musique(x, seg_s=SEGMENT_S, sr=SR):
    """Niveau (dB, puissance moyenne) par segment : la musique est continue, pas de seuil de présence."""
    L = trames_db(mono(x), sr)
    par = int(round(seg_s / 0.01))
    n = int(math.ceil(len(L) / par))
    return np.array([db((10 ** (L[i * par:(i + 1) * par] / 10)).mean()) for i in range(n)])


def _remplir(v):
    """Remplace les NaN d'une courbe par interpolation (début et fin : valeur la plus proche) ; tout NaN : zéros."""
    v = np.array(v, float)
    bon = np.isfinite(v)
    if not bon.any():
        return np.zeros_like(v)
    if bon.all():
        return v
    i = np.arange(len(v))
    return np.interp(i, i[bon], v[bon])


def courbe_vers_echantillons(par_segment, n, seg_s=SEGMENT_S, lissage_s=LISSAGE_S, sr=SR):
    """Valeurs par segment (dB) -> une valeur par échantillon : paliers de seg_s, lissés par une fenêtre de Hann de lissage_s."""
    from scipy.signal import fftconvolve
    pas = 100   # points par seconde
    par_point = np.repeat(par_segment, int(round(seg_s * pas)))
    k = np.hanning(max(int(lissage_s * pas), 3))
    k /= k.sum()
    pad = len(k) // 2
    ext = np.concatenate([np.full(pad, par_point[0]), par_point, np.full(pad, par_point[-1])])
    lisse = fftconvolve(ext, k, mode='same')[pad:pad + len(par_point)]
    t_pts = (np.arange(len(lisse)) + 0.5) / pas
    return np.interp(np.arange(n) / sr, t_pts, lisse).astype(np.float32)


def courbe_gain_voix(voix_source, voix_conv, musique_orig, musique_cur=None, voix_db=VOIX_DB, seg_s=SEGMENT_S,
                     lissage_s=LISSAGE_S, borne_db=(-20.0, 25.0)):
    """Gain (dB, par échantillon) à appliquer à la voix convertie pour que son niveau suive le rapport voix/musique de
    l'original par segments : niveau voulu = niveau de la musique (actuelle) + rapport de l'original + voix_db.
    Segments sans voix : valeur voisine (interpolation). Rend (courbe, info)."""
    musique_cur = musique_orig if musique_cur is None else musique_cur
    Ls = niveaux_segments(voix_source, seg_s)
    Lc = niveaux_segments(voix_conv, seg_s)
    Mo, Mc = niveaux_musique(musique_orig, seg_s), niveaux_musique(musique_cur, seg_s)
    n = min(len(Ls), len(Lc), len(Mo), len(Mc))
    rapport = Ls[:n] - Mo[:n]
    voulu = Mc[:n] + rapport + voix_db
    g = voulu - Lc[:n]
    valide = np.isfinite(g)
    info = {'segments': int(n), 'segments_avec_voix': int(valide.sum()),
            'rapport_voix_musique_original_db': {'p10': None, 'p50': None, 'p90': None}, 'voix_db': voix_db}
    if valide.any():
        r = rapport[np.isfinite(rapport)]
        info['rapport_voix_musique_original_db'] = {k: round(float(np.percentile(r, p)), 2) for k, p in
                                                    (('p10', 10), ('p50', 50), ('p90', 90))}
        info['gain_segments_db'] = {'p10': round(float(np.percentile(g[valide], 10)), 2),
                                    'p50': round(float(np.percentile(g[valide], 50)), 2),
                                    'p90': round(float(np.percentile(g[valide], 90)), 2)}
    g = np.clip(_remplir(g), *borne_db)
    return courbe_vers_echantillons(g, len(voix_conv), seg_s, lissage_s), info


# --- Chaîne de la voix ---------------------------------------------------------------------------------------------

def passe_haut(x, f=HP_HZ, sr=SR):
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(2, f, 'highpass', fs=sr, output='sos'), x, axis=0).astype(np.float32)


def spectre_actif_db(x, sr=SR, n_fft=4096):
    """Spectre moyen (dB) des trames actives (à moins de 35 dB sous le centile 95) : (fréquences, dB)."""
    from scipy.signal import stft
    f, _, z = stft(mono(x), fs=sr, nperseg=n_fft, noverlap=n_fft // 2, boundary=None, padded=False)
    P = np.abs(z) ** 2
    e = db(P.sum(0))
    act = e > np.percentile(e, 95) - 35
    if not act.any():
        act = np.ones_like(act)
    return f, db(P[:, act].mean(1))


def courbe_eq(src, conv, plafond_db=EQ_PLAFOND_DB, sr=SR):
    """Correction d'EQ (dB) par tiers d'octave entre EQ_BANDE_ACTIVE : différence des spectres moyens (voix d'origine −
    voix convertie, mêmes énergies entre 250 Hz et 4 kHz), lissée sur une octave (forme à grande échelle : les creux fins du
    chanteur d'origine ne sont pas recopiés), bornée à ±plafond_db, et à ±EQ_PLAFOND_MEDIUM_DB entre 250 Hz et 4 kHz.
    Rend (centres_hz, correction_db)."""
    f, S = spectre_actif_db(src, sr)
    _, C = spectre_actif_db(conv, sr)
    centres = 150.0 * 2.0 ** (np.arange(0, int(math.floor(3 * math.log2(10000 / 150))) + 1) / 3.0)

    def tiers(spec):
        sortie = []
        for c in centres:
            m = (f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))
            sortie.append(db((10 ** (spec[m] / 10)).mean()) if m.any() else -120.0)
        return np.array(sortie)
    d = tiers(S) - tiers(C)
    ref = (centres >= EQ_BANDE_MEDIUM[0]) & (centres <= EQ_BANDE_MEDIUM[1])
    d = d - np.median(d[ref])   # le niveau d'ensemble ne se règle pas ici (voir courbe_gain_voix)
    lisse = np.convolve(np.pad(d, 1, mode='edge'), np.ones(3) / 3, 'valid')   # +/- 1/3 d'octave
    lisse = np.convolve(np.pad(lisse, 1, mode='edge'), np.ones(3) / 3, 'valid')   # (deux passes : environ une octave)
    borne = np.where(ref, min(plafond_db, EQ_PLAFOND_MEDIUM_DB), plafond_db)
    return centres, np.clip(lisse, -borne, borne)


def appliquer_eq(x, centres, corr_db, sr=SR, taps=2047):
    """EQ à phase linéaire (FIR) : correction constante entre les centres, 0 dB sous 120 Hz et sur 11 kHz."""
    from scipy.signal import firwin2, oaconvolve
    if not np.any(np.abs(corr_db) > 1e-6):
        return np.asarray(x, np.float32)
    f = np.concatenate([[0.0, 100.0], centres, [11000.0, sr / 2.0]])
    g = np.concatenate([[0.0, corr_db[0] * 0.0], corr_db, [0.0, 0.0]])
    g[1] = 0.0
    h = firwin2(taps, f, 10 ** (g / 20), fs=sr)
    y = oaconvolve(x, h[:, None] if x.ndim == 2 else h, mode='full', axes=0)
    d = taps // 2
    return y[d:d + len(x)].astype(np.float32)


def compresseur(x, seuil_db=COMPRESSION['seuil_db'], ratio=COMPRESSION['ratio'], attaque_ms=COMPRESSION['attaque_ms'],
                relachement_ms=COMPRESSION['relachement_ms'], sr=SR):
    """Compresseur doux (anticipation nulle, détection RMS sur 5 ms, genou souple de 6 dB). Rend (y, réduction_max_db)."""
    x = np.asarray(x, np.float32)
    bloc = max(int(sr * 0.001), 1)   # le gain est calculé par blocs de 1 ms
    n = len(x) // bloc * bloc
    r = np.sqrt((x[:n].astype(np.float64).reshape(-1, bloc) ** 2).mean(1) + 1e-12)
    from scipy.ndimage import uniform_filter1d
    nivdb = 20 * np.log10(uniform_filter1d(r, 5) + 1e-9)
    genou = 6.0
    exces = nivdb - seuil_db
    sortie = np.where(exces <= -genou / 2, 0.0,
                      np.where(exces >= genou / 2, exces * (1 - 1 / ratio),
                               (1 - 1 / ratio) * (exces + genou / 2) ** 2 / (2 * genou)))
    a, rl = math.exp(-1.0 / max(attaque_ms, 0.1)), math.exp(-1.0 / max(relachement_ms, 0.1))
    lisse = np.zeros_like(sortie)
    e = 0.0
    for i, v in enumerate(sortie):
        c = a if v > e else rl
        e = c * e + (1 - c) * v
        lisse[i] = e
    g = np.interp(np.arange(len(x)) / bloc, np.arange(len(lisse)) + 0.5, -lisse)
    return (x * (10 ** (g / 20)).astype(np.float32).reshape(-1, *([1] * (x.ndim - 1)))).astype(np.float32), float(lisse.max())


def _rapport_sibilance_db(x, sr=SR, bande=DEESSEUR['bande_hz']):
    """Rapport (dB) par trame de 10 ms entre la bande des « s » et la bande 150 Hz–5 kHz ; NaN hors voix."""
    from scipy.signal import butter, sosfilt
    m = mono(x)
    hi = sosfilt(butter(4, bande, 'bandpass', fs=sr, output='sos'), m)
    lo = sosfilt(butter(4, [150.0, bande[0]], 'bandpass', fs=sr, output='sos'), m)
    Lh, Ll = trames_db(hi, sr), trames_db(lo, sr)
    n = min(len(Lh), len(Ll))
    actif = Ll[:n] > np.percentile(Ll[:n], 95) - 30
    r = Lh[:n] - Ll[:n]
    return np.where(actif, r, np.nan)


def deesseur(x, ref_src, sr=SR, reduction_max_db=DEESSEUR['reduction_max_db'], pente=DEESSEUR['pente']):
    """Dé-esseur à bande (5–9 kHz) : seuil = centile 90 du rapport sibilantes/voyelles de la voix d'ORIGINE (même
    brillance de « s »), réduction de la seule bande quand le rapport de x le dépasse, de pente×excès, bornée.
    Rend (y, info)."""
    from scipy.signal import butter, sosfiltfilt
    from scipy.ndimage import uniform_filter1d
    ref = _rapport_sibilance_db(ref_src, sr)
    cur = _rapport_sibilance_db(x, sr)
    seuil = float(np.nanpercentile(ref, 90)) if np.isfinite(ref).any() else -10.0
    exces = np.clip(np.nan_to_num(cur - seuil, nan=0.0), 0, None)
    red = np.minimum(exces * pente, reduction_max_db)
    red = uniform_filter1d(red, 3)
    g = np.interp(np.arange(len(x)) / int(sr * 0.01), np.arange(len(red)) + 0.5, -red)
    sos = butter(4, DEESSEUR['bande_hz'], 'bandpass', fs=sr, output='sos')
    bande = sosfiltfilt(sos, x, axis=0)   # phase nulle : x − bande retire vraiment la bande
    gl = (10 ** (g / 20)).astype(np.float32).reshape(-1, *([1] * (np.ndim(x) - 1)))
    y = (x - bande + bande * gl).astype(np.float32)
    info = {'seuil_rapport_db': round(seuil, 2), 'reduction_max_db': round(float(red.max()) if len(red) else 0.0, 2),
            'part_trames_reduites': round(float((red > 0.1).mean()) if len(red) else 0.0, 3)}
    return y, info


# --- Reverb neuve sur la voix seule --------------------------------------------------------------------------------

def enveloppe_5ms(x, sr=SR, bande=(200.0, 4000.0)):
    from scipy.signal import butter, sosfilt
    b = sosfilt(butter(4, bande, 'bandpass', fs=sr, output='sos'), mono(x))
    p = int(sr * 0.005)
    n = len(b) // p * p
    return db((b[:n].astype(np.float64).reshape(-1, p) ** 2).mean(1))


def mesurer_rt60(x, sr=SR, rt60_max=REVERB['rt60_max_s']):
    """RT60 par décroissances libres (méthode du son qui s'arrête) sur l'enveloppe (200 Hz–4 kHz, pas de 5 ms) :
    - repérer la fin d'un son : une crête de l'enveloppe, puis la première trame 5 dB plus bas ;
    - sauter 30 ms (la fin du son direct), puis ajuster une droite sur la queue, jusqu'à 15 dB plus bas que son début
      (au plus 2 s ; arrêt si l'enveloppe remonte de 3 dB) ;
    - RT60 = −60 / pente ; on garde les ajustements corrects (R² > 0,85, chute ≥ 8 dB, ≥ 10 trames) et la médiane.
    Rend un dict : rt60_s (plafonné à rt60_max), rt60_brut_s, p25_s, p75_s, ajustements."""
    from scipy.ndimage import median_filter
    e = median_filter(enveloppe_5ms(x, sr), 3)
    haut = np.percentile(e, 95)
    pas, n = 0.005, len(e)
    cand = []
    i = 0
    while i < n - 20:
        if e[i] > haut - 25 and e[i] >= e[max(0, i - 10):i + 11].max() - 1e-9:
            pic = e[i]
            j = i
            while j < n - 1 and e[j] > pic - 5 and (j - i) * pas < 6.0:   # tenue du son
                j += 1
            debut = j + 6
            fin_son = j
            if debut < n - 12:
                L0 = e[debut]
                k, minimum = debut, L0
                while k < n - 1 and (k - debut) * pas < 2.0:
                    k += 1
                    minimum = min(minimum, e[k])
                    if e[k] > minimum + 3.0 or e[k] < L0 - 15:
                        break
                seg = np.arange(debut, k + 1)
                if len(seg) >= 10 and (L0 - e[seg].min()) >= 8:
                    t = seg * pas
                    p = np.polyfit(t, e[seg], 1)
                    res = e[seg] - np.polyval(p, t)
                    r2 = 1 - (res ** 2).sum() / (((e[seg] - e[seg].mean()) ** 2).sum() + 1e-12)
                    if p[0] < -3 and r2 > 0.85:
                        cand.append(-60.0 / p[0])
                i = max(k, fin_son) + 1
            else:
                break
        else:
            i += 1
    cand = np.array([c for c in cand if 0.1 <= c <= 6.0])
    if len(cand) < 3:
        return {'rt60_s': None, 'rt60_brut_s': None, 'ajustements': int(len(cand))}
    brut = float(np.median(cand))
    return {'rt60_s': round(min(brut, rt60_max), 3), 'rt60_brut_s': round(brut, 3),
            'p25_s': round(float(np.percentile(cand, 25)), 3), 'p75_s': round(float(np.percentile(cand, 75)), 3),
            'ajustements': int(len(cand))}


def mesurer_predelai(sec, humide, sr=SR, maxi_ms=120.0):
    """Pré-délai (ms) entre la voix sèche et sa reverb : retard qui maximise l'intercorrélation des hausses d'enveloppe
    (pas de 1 ms, 100 Hz–8 kHz) de la voix sèche et de la piste de reverb. None si la corrélation est faible."""
    from scipy.signal import butter, sosfilt, correlate

    def hausses(x):
        b = sosfilt(butter(4, [100.0, 8000.0], 'bandpass', fs=sr, output='sos'), mono(x))
        p = int(sr * 0.001)
        n = len(b) // p * p
        L = db((b[:n].astype(np.float64).reshape(-1, p) ** 2).mean(1))
        return np.maximum(np.diff(L), 0)
    a, b = hausses(sec), hausses(humide)
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    c = correlate(b, a, mode='full', method='fft')
    lags = np.arange(-n + 1, n)
    m = (lags >= 0) & (lags <= maxi_ms)
    if not m.any():
        return None
    k = int(np.argmax(np.where(m, c, -np.inf)))
    force = float(c[k] / (np.sqrt((a ** 2).sum() * (b ** 2).sum()) + 1e-12))
    return float(lags[k]) if force > 0.05 else None


def mesurer_reverb(voix_source, humide=None, sr=SR):
    """RT60, pré-délai et rapport humide/sec de la reverb de la voix d'origine.
    - avec la piste de reverb retirée par le dereverb (humide) : RT60 sur elle, pré-délai par intercorrélation avec la voix
      sèche (voix_source − humide), rapport humide/sec en dB ;
    - sans elle : RT60 sur la voix d'origine isolée ; pré-délai non mesurable (valeur par défaut) ; niveau par défaut.
    Rend un dict (source : « piste_reverb » ou « voix_origine »)."""
    res = {'source': 'piste_reverb' if humide is not None else 'voix_origine'}
    if humide is not None:
        n = min(len(voix_source), len(humide))
        sec = mono(voix_source)[:n] - mono(humide)[:n]
        rt = mesurer_rt60(humide, sr)
        pd = mesurer_predelai(sec, humide, sr)
        res.update(rt)
        res['predelai_ms'] = pd if pd is not None else REVERB['predelai_defaut_ms']
        res['predelai_mesure'] = pd is not None
        res['humide_sur_sec_db'] = round(niveau_actif_db(humide) - niveau_actif_db(sec), 2)
    else:
        res.update(mesurer_rt60(voix_source, sr))
        res['predelai_ms'] = REVERB['predelai_defaut_ms']
        res['predelai_mesure'] = False
        res['humide_sur_sec_db'] = None
    if res.get('rt60_s') is None:
        res['rt60_s'] = 1.2   # voix trop continue pour mesurer : valeur de salle moyenne, signalée
        res['rt60_mesure'] = False
    else:
        res['rt60_mesure'] = True
    return res


def reponse_impulsionnelle(rt60_s, predelai_ms, sr=SR, graine=11, passe_haut=REVERB['passe_haut_hz'],
                           passe_bas=REVERB['passe_bas_hz']):
    """RIR stéréo décorrélée de queue exponentielle (−60 dB à rt60_s), les aigus s'éteignant deux fois plus vite, précédée
    de predelai_ms de silence ; passe-haut 400 Hz et passe-bas 7 kHz sur le retour. Normalisée en énergie."""
    from scipy.signal import butter, sosfilt
    rng = np.random.default_rng(graine)
    n = int(sr * min(rt60_s * 1.15, 3.0))
    t = np.arange(n) / sr
    sortie = []
    for _ in range(2):
        bruit = rng.standard_normal(n)
        grave = sosfilt(butter(2, 3000.0, 'lowpass', fs=sr, output='sos'), bruit)
        aigu = bruit - grave
        y = grave * 10 ** (-3.0 * t / rt60_s) + aigu * 10 ** (-3.0 * t / (rt60_s * 0.5))   # −60 dB = 10^-3
        y = sosfilt(butter(2, passe_haut, 'highpass', fs=sr, output='sos'), y)
        y = sosfilt(butter(2, passe_bas, 'lowpass', fs=sr, output='sos'), y)
        sortie.append(y)
    ir = np.stack(sortie, 1)
    ir /= np.sqrt((ir ** 2).sum()) + 1e-12
    return np.concatenate([np.zeros((int(sr * predelai_ms / 1000.0), 2)), ir]).astype(np.float32)


def rir_par_deconvolution(sec, complet, sr=SR, duree_s=2.0, regularisation=1e-3):
    """RIR moyenne de la reverb d'origine, par division régularisée des spectres croisés (rapport § 1.1) :
    H(f) = S_xy / (S_xx + λ), x = voix sèche, y = voix complète. Rend une RIR mono, fenêtrée et normalisée (variante D)."""
    from scipy.signal import csd, welch
    n = min(len(sec), len(complet))
    x, y = mono(sec)[:n], mono(complet)[:n]
    nper = int(sr * duree_s * 2)
    f, sxy = csd(x, y, fs=sr, nperseg=nper)
    _, sxx = welch(x, fs=sr, nperseg=nper)
    lam = regularisation * sxx.max()
    h = np.fft.irfft(sxy / (sxx + lam))
    h = h[:int(sr * duree_s)]
    pic = int(np.argmax(np.abs(h)))
    h = h[pic:] * np.exp(-np.arange(len(h) - pic) / (sr * duree_s / 5))   # le direct est retiré, queue fenêtrée
    h[:int(0.002 * sr)] = 0.0
    return (h / (np.sqrt((h ** 2).sum()) + 1e-12)).astype(np.float32)


def appliquer_reverb(voix_mono, ir):
    """Reverb stéréo de la voix mono par convolution (FFT) ; mêmes longueur que la voix."""
    from scipy.signal import oaconvolve
    ir2 = ir[:, None] if ir.ndim == 1 else ir
    y = oaconvolve(voix_mono[:, None], ir2, mode='full', axes=0)[:len(voix_mono)]
    return (np.repeat(y, 2, 1) if y.shape[1] == 1 else y).astype(np.float32)


def courbe_fin_de_phrase(voix_mono, sr=SR, bonus_db=REVERB['fin_de_phrase_db'], delai_s=0.6):
    """Gain de reverb (linéaire, par échantillon) : 1, et +bonus_db pendant delai_s après chaque arrêt de la voix
    (automation de fin de phrase)."""
    from scipy.ndimage import uniform_filter1d, maximum_filter1d
    L = trames_db(voix_mono, sr)
    actif = (L > np.percentile(L, 95) - 35).astype(float)
    actif = uniform_filter1d(maximum_filter1d(actif, 20), 20) > 0.5   # trous < 0,2 s comblés
    # arrêt : trame inactive précédée d'une active dans les delai_s qui précèdent
    depuis = np.full(len(actif), 1e9)
    d = 1e9
    for i in range(len(actif)):
        d = 0.0 if actif[i] else d + 0.01
        depuis[i] = d
    apres = ((depuis > 0.0) & (depuis <= delai_s)).astype(float)
    g_db = uniform_filter1d(apres, 30) * bonus_db
    return (10 ** (np.interp(np.arange(len(voix_mono)) / int(sr * 0.01), np.arange(len(g_db)) + 0.5, g_db) / 20)).astype(np.float32)


# --- Chœurs en stéréo ----------------------------------------------------------------------------------------------

def elargir_stereo(x_mono, sr=SR, longueur=512, graine=5):
    """Image large d'une piste mono : deux filtres passe-tout à phase aléatoire (modules égaux à 1) décorrèlent G et D ;
    énergie conservée, somme mono sans creux marqué."""
    rng = np.random.default_rng(graine)
    from scipy.signal import oaconvolve
    sortie = []
    for _ in range(2):
        ph = rng.uniform(-np.pi, np.pi, longueur // 2 + 1)
        ph[0] = ph[-1] = 0
        h = np.fft.irfft(np.exp(1j * ph), longueur)
        h *= np.hanning(longueur) ** 0.5
        h /= np.sqrt((h ** 2).sum())
        sortie.append(oaconvolve(x_mono, h)[:len(x_mono)])
    return np.stack(sortie, 1).astype(np.float32)


# --- Limiteur et loudness ------------------------------------------------------------------------------------------

def loudness_lufs(x, sr=SR):
    import pyloudnorm as pyln
    return float(pyln.Meter(sr).integrated_loudness(np.asarray(x, np.float64)))


def crete_vraie_db(x, sr=SR):
    """Crête vraie (dBTP) : suréchantillonnage ×4."""
    from scipy.signal import resample_poly
    return float(20 * np.log10(np.abs(resample_poly(np.asarray(x, np.float32), 4, 1, axis=0)).max() + 1e-12))


def courbe_limiteur(x, plafond_dbtp=PLAFOND_DBTP, anticipation_ms=5.0, sr=SR):
    """Gain linéaire (par échantillon) qui ramène la crête vraie sous plafond_dbtp : détection ×4, minimum glissant sur
    l'anticipation puis lissage de la même largeur (le gain est toujours ≤ au gain requis à chaque crête)."""
    from scipy.signal import resample_poly
    from scipy.ndimage import minimum_filter1d, uniform_filter1d
    lim = 10 ** (plafond_dbtp / 20)
    sur = np.abs(resample_poly(np.asarray(x, np.float32), 4, 1, axis=0)).max(1)
    n4 = len(sur) // 4 * 4
    pic = sur[:n4].reshape(-1, 4).max(1)
    pic = np.concatenate([pic, np.zeros(len(x) - len(pic))]) if len(pic) < len(x) else pic[:len(x)]
    requis = np.minimum(1.0, lim / np.maximum(pic, 1e-9))
    L = max(int(sr * anticipation_ms / 1000), 3) | 1
    g = uniform_filter1d(minimum_filter1d(requis, 2 * L + 1), 2 * L + 1)
    return np.minimum(g, requis).astype(np.float32)


def finaliser(pistes, cible_lufs, plafond_dbtp=PLAFOND_DBTP, tolerance=0.05, iterations=6, sr=SR):
    """pistes : dict nom -> (n, 2) à sommer. Gain commun (loudness intégré = cible_lufs), puis limiteur ; la boucle ajuste
    le gain tant que le loudness après limiteur s'écarte de plus de `tolerance` LU. Rend (mix, gain_commun_lineaire,
    courbe_limiteur, info) ; le gain du limiteur s'applique aussi aux pistes seules (voix seule au niveau du mix)."""
    somme = sum(pistes.values())
    lufs0 = loudness_lufs(somme, sr)
    g_db = cible_lufs - lufs0
    for _ in range(iterations):
        y = somme * float(gain_db(g_db))
        lim = courbe_limiteur(y, plafond_dbtp, sr=sr)
        mix = y * lim[:, None]
        ecart = cible_lufs - loudness_lufs(mix, sr)
        if abs(ecart) <= tolerance:
            break
        g_db += ecart
    return mix, float(gain_db(g_db)), lim, {'lufs_avant_db': round(lufs0, 2), 'gain_commun_db': round(float(g_db), 2),
                                            'lufs_final': round(loudness_lufs(mix, sr), 2), 'cible_lufs': round(cible_lufs, 2),
                                            'crete_vraie_dbtp': round(crete_vraie_db(mix, sr), 2),
                                            'reduction_limiteur_max_db': round(float(-20 * np.log10(lim.min())), 2)}


# --- Voix v3 -------------------------------------------------------------------------------------------------------

def voix_v3(conv, src, musique_orig, musique_cur=None, voix_db=VOIX_DB, eq_plafond_db=EQ_PLAFOND_DB, sr=SR):
    """Voix sèche v3 (mono) : passe-haut, EQ douce, compression, dé-esseur, niveau suivant le rapport voix/musique.
    conv : voix convertie ; src : voix d'origine isolée (mesures seulement). Rend (mono, info)."""
    info = {}
    x = passe_haut(mono(conv), HP_HZ, sr)
    # niveau de travail : le compresseur et le dé-esseur voient une voix à −20 dBFS actif, comme en v2
    mise = -20.0 - niveau_actif_db(x)
    x = x * float(gain_db(mise))
    centres, corr = courbe_eq(src, x, eq_plafond_db, sr)
    x = appliquer_eq(x, centres, corr, sr)
    info['eq'] = {'plafond_db': eq_plafond_db, 'correction_max_db': round(float(np.abs(corr).max()), 2),
                  'correction_max_250_4000_db': round(float(np.abs(corr[(centres >= 250) & (centres <= 4000)]).max()), 2),
                  'courbe_db': {f'{c:.0f}': round(float(v), 2) for c, v in zip(centres, corr)}}
    x, red = compresseur(x, sr=sr)
    info['compression'] = {**COMPRESSION, 'reduction_max_db': round(red, 2)}
    x, info['deesseur'] = deesseur(x, src, sr)
    courbe, info['niveau'] = courbe_gain_voix(src, x, musique_orig, musique_cur, voix_db)
    x = x * (10 ** (courbe / 20))
    info['niveau']['mise_au_niveau_db'] = round(mise, 2)
    return x.astype(np.float32), info


def reverb_voix(voix_mono, mesure, niveau_db=None, ir=None, fin_de_phrase=True, sr=SR, graine=11):
    """Retour de reverb stéréo de la voix : RIR neuve (RT60 et pré-délai mesurés) ou ir donnée ; niveau actif =
    niveau actif de la voix + niveau_db (défaut : mesuré si la piste de reverb existe, borné à −13 dB, sinon −17 dB)."""
    if niveau_db is None:
        mes = mesure.get('humide_sur_sec_db')
        niveau_db = REVERB['niveau_defaut_db'] if mes is None else float(np.clip(mes, -25.0, REVERB['niveau_max_db']))
    niveau_db = min(niveau_db, REVERB['niveau_max_db'])
    if ir is None:
        ir = reponse_impulsionnelle(min(mesure['rt60_s'], REVERB['rt60_max_s']), mesure['predelai_ms'], sr, graine)
    wet = appliquer_reverb(voix_mono, ir)
    if fin_de_phrase:
        wet = wet * courbe_fin_de_phrase(voix_mono, sr)[:, None]
    g = niveau_actif_db(voix_mono) + niveau_db - niveau_actif_db(wet)
    return (wet * float(gain_db(g))).astype(np.float32), {'niveau_db': round(niveau_db, 2), 'gain_db': round(float(g), 2)}


def choeurs_v3(choeurs, lead_mono, mesure, sr=SR, niveau_db=CHOEURS['niveau_db'], voix_db_ref=None):
    """Chœurs à niveau_db sous la voix principale, en stéréo large, avec une reverb +3 dB et plus sombre. Rend (sec, humide)."""
    from scipy.signal import butter, sosfilt
    x = passe_haut(mono(choeurs), HP_HZ, sr)
    ecart = niveau_actif_db(lead_mono) + niveau_db - niveau_actif_db(x)
    x = x * float(gain_db(ecart))
    large = elargir_stereo(x, sr)
    sombre = sosfilt(butter(2, CHOEURS['passe_bas_hz'], 'lowpass', fs=sr, output='sos'), x)
    ir = reponse_impulsionnelle(min(mesure['rt60_s'], REVERB['rt60_max_s']), mesure['predelai_ms'] + 20.0, sr, graine=23,
                                passe_bas=CHOEURS['passe_bas_hz'])
    wet = appliquer_reverb(sombre.astype(np.float32), ir)
    niveau = REVERB['niveau_defaut_db'] if mesure.get('humide_sur_sec_db') is None else float(
        np.clip(mesure['humide_sur_sec_db'], -25.0, REVERB['niveau_max_db']))
    g = niveau_actif_db(x) + niveau + CHOEURS['reverb_en_plus_db'] - niveau_actif_db(wet)
    return large, (wet * float(gain_db(g))).astype(np.float32)


# --- Variantes -----------------------------------------------------------------------------------------------------

def v2_pistes(dossier_voix, rapport, debut_s, duree_s):
    """Pistes du mix v2 tel que doubler le fait : voix sèche, reverb et leurs gains (rapport.json, mix.gains_db)."""
    g = (rapport.get('mix') or {}).get('gains_db') or {}
    sec = lire(Path(dossier_voix) / 'seche_voix_convertie.wav', debut_s, duree_s)
    rev = lire(Path(dossier_voix) / 'reverb_voix_convertie.wav', debut_s, duree_s)
    n = len(sec)
    return stereo(sec) * float(gain_db(g['seche'])), caler(stereo(rev), n) * float(gain_db(g['reverb'])), g


class Chanson:
    """Une chanson chargée sur un extrait : pistes d'entrée et mesures de l'original, faites une fois."""

    def __init__(self, travail, voix_dossier, debut_s=None, duree_s=60.0, humide=None, choeurs=None, voix_convertie=None,
                 sr=SR):
        t = Path(travail)
        self.t, self.vd = t, t / voix_dossier
        rapport = json.loads((self.vd / 'rapport.json').read_text()) if (self.vd / 'rapport.json').exists() else {}
        self.rapport = rapport
        if debut_s is None:
            debut_s = self.choisir_debut(duree_s)
        self.debut_s, self.duree_s = debut_s, duree_s
        self.original = stereo(lire(t / 'original.wav', debut_s, duree_s))
        self.n = len(self.original)
        self.musique = caler(stereo(lire(t / 'instrumental.wav', debut_s, duree_s)), self.n)
        self.src = caler(mono(lire(t / 'voix_source.wav', debut_s, duree_s)), self.n)
        chemin_voix = Path(voix_convertie) if voix_convertie else self.vd / 'voix_convertie.flac'
        self.conv = caler(mono(lire(chemin_voix, debut_s, duree_s)), self.n)
        h = Path(humide) if humide else t / 'reverb_source.wav'
        self.humide = caler(mono(lire(h, debut_s, duree_s)), self.n) if h.exists() else None
        self.choeurs = caler(mono(lire(choeurs, debut_s, duree_s)), self.n) if choeurs and Path(choeurs).exists() else None
        self.mesure_reverb = mesurer_reverb(self.src, self.humide)
        self.cible_lufs = loudness_lufs(self.original)

    def choisir_debut(self, duree_s):
        """Début de la fenêtre de duree_s où la voix convertie est la plus présente (énergie des trames actives)."""
        import soundfile as sf
        chemin = self.vd / 'voix_convertie.flac'
        total = sf.info(str(chemin)).frames / sf.info(str(chemin)).samplerate
        x = mono(lire(chemin))
        e = trames_db(x)
        actif = (e > np.percentile(e, 95) - 35).astype(float)
        par = int(duree_s * 100)
        if len(actif) <= par:
            return 0.0
        c = np.concatenate([[0], np.cumsum(actif)])
        meilleur = int(np.argmax(c[par:] - c[:-par]))
        return round(min(meilleur / 100.0, max(total - duree_s, 0.0)), 1)

    def mix_v2(self):
        sec, rev, g = v2_pistes(self.vd, self.rapport, self.debut_s, self.duree_s)
        return {'musique': self.musique, 'voix': caler(sec, self.n), 'reverb': caler(rev, self.n)}, {'gains_db': g}

    def mix_v3(self, voix_db=VOIX_DB, eq_plafond_db=EQ_PLAFOND_DB, reverb='mesuree', musique=None, voix=None,
               reverb_niveau_db=None, rir=None, melange_reverb_origine=0.0):
        """Pistes du mix v3. reverb : « mesuree » (RIR neuve), « deconvolution » (RIR estimée, piste de reverb requise),
        None (sans reverb). musique : musique transposée (n, 2) ou None. voix : voix convertie à utiliser (variante C)."""
        mus = self.musique if musique is None else caler(stereo(musique), self.n)
        conv = self.conv if voix is None else caler(mono(voix), self.n)
        lead, info = voix_v3(conv, self.src, self.musique, mus, voix_db, eq_plafond_db)
        pistes = {'musique': mus, 'voix': stereo(lead)}
        info['reverb_mesure'] = self.mesure_reverb
        if reverb is not None:
            ir = None
            if reverb == 'deconvolution':
                if self.humide is None:
                    raise ValueError('RIR par déconvolution : piste de reverb retirée absente')
                n = min(len(self.src), len(self.humide))
                ir = rir_par_deconvolution(self.src[:n] - self.humide[:n], self.src[:n])
            wet, info['reverb'] = reverb_voix(lead, self.mesure_reverb, reverb_niveau_db, ir)
            if melange_reverb_origine and self.humide is not None:
                a = niveau_actif_db(wet)
                o = stereo(self.humide)
                pistes['reverb_origine'] = o * float(gain_db(a - niveau_actif_db(o) + 20 * math.log10(melange_reverb_origine)))
            pistes['reverb'] = wet
        if self.choeurs is not None:
            sec, hum = choeurs_v3(self.choeurs, lead, self.mesure_reverb)
            pistes['choeurs'] = sec
            pistes['choeurs_reverb'] = hum
            info['choeurs'] = {'niveau_db': CHOEURS['niveau_db']}
        else:
            info['choeurs'] = None
        return pistes, info

    def rendre(self, pistes):
        """Mix final et voix seule (voix + reverb + chœurs, au niveau du mix) ; mesures."""
        mix, g, lim, info = finaliser(pistes, self.cible_lufs)
        voix = sum(v for k, v in pistes.items() if k != 'musique') * g * lim[:, None]
        return mix, voix, info


VARIANTES = {
    'A': {'nom': 'v2 actuel (témoin)'},
    'B': {'nom': 'v3 (voix +1 dB, EQ plafonnée à 3 dB, reverb mesurée)', 'v3': {}},
    'C': {'nom': 'v3 + musique transposée', 'v3': {'musique': 'transposee'}, 'gpu': True},
    'D': {'nom': 'v3 avec RIR par déconvolution', 'v3': {'reverb': 'deconvolution'}, 'exige_humide': True},
    'E': {'nom': 'v3 + 25 % de la reverb d\'origine', 'v3': {'melange_reverb_origine': 0.25}, 'exige_humide': True},
    'F': {'nom': 'v3 sans reverb', 'v3': {'reverb': None}},
    'G4': {'nom': 'v3, voix +4 dB', 'v3': {'voix_db': 4.0}},
    'G0': {'nom': 'v3, voix +0 dB', 'v3': {'voix_db': 0.0}},
    'I15': {'nom': 'v3, EQ plafonnée à 1,5 dB', 'v3': {'eq_plafond_db': 1.5}},
    'I0': {'nom': 'v3, sans EQ', 'v3': {'eq_plafond_db': 0.0}},
}


def produire(chanson, nom, sortie, variantes=None, graine=20261109, musique_transposee=None, voix_transposee=None,
             tirer=True, mp3=True):
    """Produit les variantes d'une chanson dans sortie/ecoute/<nom>/ : <lettre>.mp3 (mix), <lettre>_voix.mp3, original.mp3.
    Les lettres sont tirées au hasard (graine) ; rend {lettre: {variante, nom, mesures}} et la liste des variantes non produites."""
    sortie = Path(sortie)
    dossier = sortie / 'ecoute' / nom
    dossier.mkdir(parents=True, exist_ok=True)
    noms = [v for v in (variantes or VARIANTES)]
    produites, absentes, fiches = [], {}, {}
    for v in noms:
        spec = VARIANTES[v]
        if spec.get('exige_humide') and chanson.humide is None:
            absentes[v] = 'piste de reverb retirée absente du dossier de travail'
            continue
        if spec.get('gpu') and (musique_transposee is None or voix_transposee is None):
            absentes[v] = 'voix à reconvertir au décalage de la règle (GPU) : voir pret_C'
            continue
        t0 = time.time()
        if v == 'A':
            pistes, extra = chanson.mix_v2()
        else:
            kw = dict(spec['v3'])
            if kw.get('musique') == 'transposee':
                kw['musique'], kw['voix'] = musique_transposee, voix_transposee
            pistes, extra = chanson.mix_v3(**kw)
        mix, voix, info = chanson.rendre(pistes)
        fiches[v] = {'variante': v, 'nom': spec['nom'], 'mesures': info, 'details': extra, 'calcul_s': round(time.time() - t0, 1)}
        produites.append((v, mix, voix))
    lettres = [chr(ord('A') + i) for i in range(len(produites))]
    if tirer:
        random.Random(f'{graine}:{nom}').shuffle(lettres)   # tirage propre à chaque extrait
    correspondance = {}
    for (v, mix, voix), lettre in zip(produites, lettres):
        if mp3:
            ecrire_mp3(dossier / f'{lettre}.mp3', mix)
            ecrire_mp3(dossier / f'{lettre}_voix.mp3', voix)
        correspondance[lettre] = v
    if mp3:
        orig, _, _ = (chanson.original, None, None)
        # original au même loudness que les mix (il est la cible : gain nul)
        ecrire_mp3(dossier / 'original.mp3', orig)
    return correspondance, absentes, fiches


def preparer_pret_c(chanson, nom, sortie, regle, musique_transposee):
    """Dossier pret_C : tout ce qu'il faut pour la variante C une fois la voix reconvertie au décalage de la règle."""
    d = Path(sortie) / 'pret_C' / nom
    d.mkdir(parents=True, exist_ok=True)
    import soundfile as sf
    sf.write(str(d / 'musique_transposee.wav'), musique_transposee, SR, subtype='FLOAT')
    (d / 'regle.json').write_text(json.dumps({**regle, 'debut_s': chanson.debut_s, 'duree_s': chanson.duree_s}, ensure_ascii=False,
                                             indent=1))
    (d / 'LISEZMOI.txt').write_text(
        "Variante C : la voix doit être reconvertie (GPU, session « realisme ») avec le décalage decalage_voix_dt de regle.json,\n"
        "sur la même fenêtre (debut_s, duree_s). Ensuite : mixage_v3.py produire ... --voix-transposee <voix.flac>\n"
        "--musique-transposee musique_transposee.wav.\n")
    return d


def fusionner_correspondances(sortie):
    """<sortie>/correspondance.json (au format de l'Écoute à l'aveugle : {extrait: {lettre: variante}}), HORS du dossier
    ecoute/ : l'interface ne la lit donc pas et ne la révèle pas avant le vote."""
    sortie = Path(sortie)
    total = {'note': "Lettres tirées au hasard, indépendamment pour chaque extrait. Variantes : voir noms ci-dessous. "
                     "Même loudness intégré pour toutes (celui de l'original de l'extrait).", 'noms': {}}
    for f in sorted((sortie / 'correspondances').glob('*.json')):
        d = json.loads(f.read_text())
        total[d['extrait']] = d['lettres']
        total['noms'].update(d['noms'])
        total.setdefault('absentes', {})[d['extrait']] = d['absentes']
    (sortie / 'correspondance.json').write_text(json.dumps(total, ensure_ascii=False, indent=1))


def source_f0_depuis_rapport(rapport):
    """Centiles de F0 (Hz) de la voix principale d'origine, lus dans le rapport.json d'un doublage de doubler (décision de
    hauteur) ; None si absents."""
    vp = ((rapport.get('decision_hauteur') or {}).get('voix_principale')) or {}
    if not vp.get('f0_med_hz'):
        return None
    return {'p10': vp.get('f0_p10_hz') or vp['f0_med_hz'] * 0.85, 'p50': vp['f0_med_hz'], 'p90': vp.get('f0_p90_hz') or vp['f0_med_hz'] * 1.2}


def preparer_c(chanson, nom, sortie, tessiture, source=None, transposition=None):
    """Variante C, côté PC : règle du décalage (transposition.py) et musique transposée de la fenêtre de la chanson,
    rangées dans <sortie>/pret_C/<nom>/. Rend (règle, musique transposée, dossier)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import transposition as T
    source = source or source_f0_depuis_rapport(chanson.rapport)
    if source is None:
        raise ValueError("F0 de la voix d'origine inconnue (rapport.json sans decision_hauteur)")
    regle = T.regle_decalage(source, T.charger_tessiture(tessiture))
    regle['source_f0_hz'] = source
    n = regle['transposition_musique_dt'] if transposition is None else transposition
    mt = T.transposer(chanson.musique, n, SR, 'r3')
    return regle, mt, preparer_pret_c(chanson, nom, sortie, regle, mt)


def _principal(argv=None):
    p = argparse.ArgumentParser(description='Mixage v3 et variantes d\'écoute à l\'aveugle')
    sp = p.add_subparsers(dest='cmd', required=True)
    pr = sp.add_parser('produire', help='variantes d\'une chanson dans <sortie>/ecoute/<nom>/')
    pr.add_argument('nom')
    pr.add_argument('travail', help='dossier travail/ du doublage (instrumental.wav, voix_source.wav, original.wav)')
    pr.add_argument('voix', help='sous-dossier de la voix (p. ex. voix1) : voix_convertie.flac, rapport.json…')
    pr.add_argument('--sortie', required=True)
    pr.add_argument('--debut', type=float)
    pr.add_argument('--duree', type=float, default=60.0)
    pr.add_argument('--variantes', nargs='+', choices=sorted(VARIANTES))
    pr.add_argument('--graine', type=int, default=20261109)
    pr.add_argument('--humide', help='piste de reverb retirée par le dereverb (défaut : travail/reverb_source.wav si elle existe)')
    pr.add_argument('--choeurs', help='chœurs convertis (piste à part)')
    pr.add_argument('--musique-transposee')
    pr.add_argument('--voix-transposee')
    pr.add_argument('--tessiture', help='JSON de tessiture de la voix cible : prépare aussi pret_C (musique transposée + règle)')
    a = p.parse_args(argv)
    ch = Chanson(a.travail, a.voix, a.debut, a.duree, a.humide, a.choeurs)
    mt = lire(a.musique_transposee, ch.debut_s, ch.duree_s) if a.musique_transposee else None
    vt = lire(a.voix_transposee, ch.debut_s, ch.duree_s) if a.voix_transposee else None
    regle = None
    if a.tessiture:
        regle, mt_prete, _ = preparer_c(ch, a.nom, a.sortie, a.tessiture)
        mt = mt if mt is not None else mt_prete
    corr, absentes, fiches = produire(ch, a.nom, a.sortie, a.variantes, a.graine, mt, vt)
    sortie = Path(a.sortie)
    (sortie / 'mesures').mkdir(parents=True, exist_ok=True)
    (sortie / 'mesures' / f'{a.nom}.json').write_text(json.dumps(
        {'extrait': {'debut_s': ch.debut_s, 'duree_s': ch.duree_s}, 'cible_lufs': round(ch.cible_lufs, 2),
         'reverb_origine': ch.mesure_reverb, 'regle_decalage': regle, 'variantes': fiches}, ensure_ascii=False, indent=1, default=float))
    (sortie / 'correspondances').mkdir(parents=True, exist_ok=True)
    (sortie / 'correspondances' / f'{a.nom}.json').write_text(json.dumps(
        {'extrait': a.nom, 'lettres': corr, 'noms': {v: VARIANTES[v]['nom'] for v in corr.values()}, 'absentes': absentes,
         'graine': a.graine}, ensure_ascii=False, indent=1))
    fusionner_correspondances(sortie)
    print(json.dumps({'correspondance': corr, 'absentes': absentes}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    _principal()
