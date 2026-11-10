"""Transposition de la musique seule (Rubber Band R3), pour que la chanson tombe dans la tessiture de la voix cible.

Voix-là transpose la chanson ENTIÈRE d'un nombre entier de demi-tons (comme un changement de tonalité au karaoké) :
la voix convertie chante dans la tonalité de la musique transposée. Le noyau Kaggle choisit ce nombre (`musique_dt`
du rapport) ; ce module ne fait que transposer la musique seule, sur le PC, à durée exacte.

Rubber Band 3.x (bibliothèque partagée librubberband.so.2, paquet Debian librubberband2) est appelé par ctypes :
le filtre `rubberband` de ffmpeg n'expose pas le choix du moteur et ne sait faire que R2, que le banc a mesuré
nettement moins bon que R3 (moteur « finer »). L'API C est stable et s'appelle sans en-têtes. Sans la bibliothèque,
`disponible()` le dit et doubler ne transpose pas (la voix garde alors l'octave seule).

Numpy seul (pas de soundfile) : l'audio passe par ffmpeg. Fonctions utiles : disponible, transposer,
transposer_fichier (durée rendue à l'échantillon près).
"""
import ctypes
import ctypes.util
import glob
import subprocess

SR = 44100

OPT_PROCESS_OFFLINE = 0x00000000
OPT_THREADING_NEVER = 0x00010000
OPT_TRANSIENTS_CRISP = 0x00000000
OPT_PITCH_HIGH_QUALITY = 0x02000000
OPT_CHANNELS_APART = 0x00000000
OPT_CHANNELS_TOGETHER = 0x10000000
OPT_ENGINE_FASTER = 0x00000000
OPT_ENGINE_FINER = 0x20000000
OPT_FORMANT_SHIFTED = 0x00000000   # instruments : formants non préservés (la préservation vise la voix)

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
            lib.rubberband_new.restype = ctypes.c_void_p
        except (OSError, AttributeError):
            continue
        lib.rubberband_new.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_double, ctypes.c_double]
        lib.rubberband_delete.argtypes = [ctypes.c_void_p]
        lib.rubberband_set_expected_input_duration.argtypes = [ctypes.c_void_p, ctypes.c_uint]
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


def disponible():
    """(vrai, '') si Rubber Band R3 est utilisable, sinon (faux, raison)."""
    try:
        lib = bibliotheque()
        etat = lib.rubberband_new(SR, 1, OPT_PROCESS_OFFLINE | MOTEURS['r3'], 1.0, 1.0)
        if not etat:
            return False, 'rubberband_new a échoué'
        try:
            version = int(lib.rubberband_get_engine_version(etat))
        finally:
            lib.rubberband_delete(etat)
    except (OSError, AttributeError) as e:
        return False, str(e)
    if version < 3:
        return False, f'la bibliothèque Rubber Band ne propose que son moteur R{version} (R3 attendu : Rubber Band 3)'
    return True, ''


def _pointeurs(canaux):
    """Tableau de pointeurs float* vers des vecteurs numpy contigus (un par canal)."""
    t = (ctypes.POINTER(ctypes.c_float) * len(canaux))()
    for i, c in enumerate(canaux):
        t[i] = c.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    return t


def transposer(x, demitons, sr=SR, moteur='r3', bloc=8192):
    """x : tableau float (n,) ou (n, canaux) ; rend le même (float32), décalé de `demitons`, rapport de temps 1,0,
    même longueur à l'échantillon près. Rubber Band hors ligne : toute l'entrée est étudiée (study) puis traitée
    (process) ; la sortie est coupée ou complétée de zéros à la longueur d'entrée. Un seul cœur."""
    import numpy as np
    x = np.asarray(x, np.float32)
    mono = x.ndim == 1
    x2 = x[:, None] if mono else x
    n, k = x2.shape
    if demitons == 0:
        return x.copy()
    lib = bibliotheque()
    options = (OPT_PROCESS_OFFLINE | MOTEURS[moteur] | OPT_PITCH_HIGH_QUALITY | OPT_FORMANT_SHIFTED
               | OPT_THREADING_NEVER | OPT_TRANSIENTS_CRISP | (OPT_CHANNELS_TOGETHER if k > 1 else OPT_CHANNELS_APART))
    etat = lib.rubberband_new(sr, k, options, 1.0, 2.0 ** (demitons / 12.0))
    if not etat:
        raise RuntimeError('rubberband_new a échoué')
    entree = [np.ascontiguousarray(x2[:, c]) for c in range(k)]
    sortie = [np.zeros(n + 65536, np.float32) for _ in range(k)]
    ecrit = 0

    def vider(ecrit):
        while True:
            dispo = lib.rubberband_available(etat)
            if dispo <= 0:
                return ecrit
            lot = [np.zeros(dispo, np.float32) for _ in range(k)]
            obtenu = lib.rubberband_retrieve(etat, _pointeurs(lot), dispo)
            for c in range(k):
                if ecrit + obtenu > len(sortie[c]):
                    sortie[c] = np.concatenate([sortie[c], np.zeros(len(sortie[c]), np.float32)])
                sortie[c][ecrit:ecrit + obtenu] = lot[c][:obtenu]
            ecrit += obtenu

    try:
        lib.rubberband_set_expected_input_duration(etat, n)
        for a in range(0, n, bloc):
            b = min(n, a + bloc)
            lib.rubberband_study(etat, _pointeurs([c[a:b] for c in entree]), b - a, int(b == n))
        for a in range(0, n, bloc):
            b = min(n, a + bloc)
            lib.rubberband_process(etat, _pointeurs([c[a:b] for c in entree]), b - a, int(b == n))
            ecrit = vider(ecrit)
        ecrit = vider(ecrit)
    finally:
        lib.rubberband_delete(etat)
    y = np.stack([s[:ecrit] for s in sortie], axis=1)
    y = y[:n] if len(y) >= n else np.pad(y, ((0, n - len(y)), (0, 0)))
    return y[:, 0] if mono else y


def lire_stereo(chemin, sr=SR):
    """Fichier audio -> float32 (n, 2) à sr, par ffmpeg."""
    import numpy as np
    r = subprocess.run(['nice', '-n', '10', 'ffmpeg', '-v', 'error', '-i', str(chemin), '-ac', '2', '-ar', str(sr),
                        '-f', 'f32le', '-'], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.float32).reshape(-1, 2)


def ecrire_wav(chemin, x, sr=SR):
    """float32 (n, 2) -> WAV flottant 32 bits, par ffmpeg."""
    import numpy as np
    subprocess.run(['nice', '-n', '10', 'ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(sr), '-ac', '2',
                    '-i', '-', '-c:a', 'pcm_f32le', str(chemin)],
                   input=np.ascontiguousarray(x, np.float32).tobytes(), check=True)


def transposer_fichier(entree, sortie, demitons, sr=SR):
    """entree (n'importe quel audio) -> sortie (WAV stéréo flottant 44,1 kHz) transposée de `demitons`, même nombre
    d'échantillons que l'entrée relue. Rend ce nombre d'échantillons."""
    x = lire_stereo(entree, sr)
    y = transposer(x, demitons, sr)
    ecrire_wav(sortie, y, sr)
    return len(y)
