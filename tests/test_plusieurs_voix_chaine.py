"""Chaîne entière du noyau (main), sans GPU ni modèle ni réseau : torch, torchaudio, soundfile, librosa, Seed-VC et
YingMusic-SVC remplacés par des imitations en numpy, la séparation et le nettoyage par des pistes de synthèse connues.

Les faux moteurs « convertissent » en un son simple : la hauteur de la source décalée de la consigne, plus une note
témoin dont la hauteur dit quelle référence a servi (2000 Hz + 10 x la hauteur de la référence) : on lit dans la voix
convertie quelle voix cible chante chaque passage, et à quelle octave.

Scène (chanson inventée, 80 s) : un chanteur (200 Hz) de 0 à 27 s, avec un « oooh » de chanteuse (450 Hz) à 10-12 s que
le nettoyage a perdu (piste de secours), une chanteuse de 27 à 54 s, puis les deux ensemble (le chanteur en voix
principale, la chanteuse aux chœurs). Voix cibles inventées : jules (référence vers 120 Hz) et marie (vers 230 Hz).

Une seule voix : le noyau d'avant (historique git) et celui-ci rendent la même voix convertie, les mêmes courbes et le
même rapport, au bit près. Tous les chemins du noyau (/tmp, /kaggle) sont déplacés dans un dossier temporaire."""
import base64
import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import types
import unittest
import warnings
from unittest import mock

import numpy as np

from commun import DEPOT, NOYAU, SR, Dossier, note

AVANT = '048b423'   # develop juste avant les voix cibles multiples
DIM = 192
HISTORIQUE = subprocess.run(['git', '-C', str(DEPOT), 'cat-file', '-e', f'{AVANT}:kaggle/doubler_kaggle.py'],
                            capture_output=True).returncode == 0


# --- Faux torch : un tableau numpy enveloppé, juste ce dont le noyau se sert -------------------------------------------

def _a(x):
    return x.a if isinstance(x, T) else x


def _cle(k):
    return tuple(_a(x) for x in k) if isinstance(k, tuple) else _a(k)


class T:
    def __init__(self, a):
        self.a = np.asarray(a)

    def __add__(self, o):
        return T(self.a + _a(o))
    __radd__ = __add__

    def __sub__(self, o):
        return T(self.a - _a(o))

    def __rsub__(self, o):
        return T(_a(o) - self.a)

    def __mul__(self, o):
        return T(self.a * _a(o))
    __rmul__ = __mul__

    def __truediv__(self, o):
        return T(self.a / _a(o))

    def __gt__(self, o):
        return T(self.a > _a(o))

    def __getitem__(self, k):
        return T(self.a[_cle(k)])

    def __setitem__(self, k, v):
        self.a[_cle(k)] = _a(v)

    def __float__(self):
        return float(self.a)

    def size(self, d=None):
        return self.a.shape if d is None else self.a.shape[d]

    def float(self):
        return T(self.a.astype(np.float32))

    def to(self, *a, **k):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.a

    def unsqueeze(self, d):
        return T(np.expand_dims(self.a, d))

    def squeeze(self):
        return T(np.squeeze(self.a))

    def mean(self, dim=None, keepdim=False):
        return T(self.a.mean(axis=dim, keepdims=keepdim))

    def norm(self):
        return T(np.linalg.norm(self.a))

    def median(self):
        return T(np.median(self.a))

    def numel(self):
        return int(self.a.size)


class SansGrad:
    def __call__(self, f=None):
        return f

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def faux_torch():
    t = types.ModuleType('torch')
    t.__version__ = '0-essai'
    t.load = lambda *a, **k: None
    t.no_grad = SansGrad
    t.from_numpy = lambda a: T(a)
    t.tensor = lambda a: T(np.asarray(a))
    t.LongTensor = lambda l: T(np.asarray(l, np.int64))
    t.exp = lambda x: T(np.exp(_a(x)))
    t.log = lambda x: T(np.log(_a(x)))
    t.cat = lambda l, dim=0: T(np.concatenate([_a(x) for x in l], axis=dim))
    t.autocast = lambda **k: contextlib.nullcontext()
    t.float16, t.float32 = 'float16', 'float32'
    t.graines = []
    t.manual_seed = t.graines.append
    t.cuda = types.SimpleNamespace(get_device_name=lambda i: 'faux GPU', empty_cache=lambda: None, is_available=lambda: False)
    t.device = lambda n: n
    return t


def reechantillonner(x, de, vers):
    a = np.asarray(_a(x), np.float64)
    n = a.shape[-1]
    m = int(round(n * vers / de))
    pos = np.arange(m) * (de / vers)
    lignes = a.reshape(-1, n)
    return np.stack([np.interp(pos, np.arange(n), l) for l in lignes]).reshape(a.shape[:-1] + (m,)).astype(np.float32)


def hauteur_trames(w, sr=16000, pas=160, fen=1024):
    """Faux RMVPE : pic du spectre entre 60 et 1000 Hz (interpolé), une trame toutes les pas échantillons (len // pas + 1
    trames, comme RMVPE) ; 0 sur le silence."""
    w = np.asarray(_a(w), np.float64).ravel()
    n = len(w) // pas + 1
    out = np.zeros(n)
    p = np.pad(w, (fen // 2, fen // 2 + pas))
    f = np.fft.rfftfreq(fen, 1 / sr)
    bande = np.where((f >= 60) & (f <= 1000))[0]
    fenetre = np.hanning(fen)
    for d in range(0, n, 2000):
        idx = (np.arange(d, min(n, d + 2000))[:, None] * pas + np.arange(fen)[None, :])
        tr = p[idx] * fenetre
        s = np.abs(np.fft.rfft(tr, axis=1))[:, bande]
        k = np.clip(np.argmax(s, axis=1), 1, len(bande) - 2)
        r = np.arange(len(k))
        a, b, c = s[r, k - 1], s[r, k], s[r, k + 1]
        den = a - 2 * b + c
        dk = np.where(den < 0, 0.5 * (a - c) / np.where(den < 0, den, -1.0), 0.0)
        hz = f[bande][k] + dk * (f[1] - f[0])
        hz[(tr ** 2).mean(1) < 1e-7] = 0
        out[d:d + len(k)] = hz
    return out


def hauteur_dominante(w, sr=16000):
    w = np.asarray(_a(w), np.float64).ravel()
    s = np.abs(np.fft.rfft(w * np.hanning(len(w))))
    f = np.fft.rfftfreq(len(w), 1 / sr)
    b = (f >= 60) & (f <= 1000)
    return float(f[b][np.argmax(s[b])]) if len(w) and s[b].max() > 0 else 0.0


class Timbres:
    """Faux CAMPPlus : empreinte d'homme sous 300 Hz, de femme au-dessus (axe d des ancres), une part propre à chaque
    bande de 25 Hz (le chanteur), et la hauteur elle-même, discrète, en dernière composante (pour la note témoin des
    faux moteurs)."""

    def __init__(self):
        rng = np.random.default_rng(42)
        self.commun = rng.standard_normal(DIM)
        self.commun[-1] = 0
        self.commun /= np.linalg.norm(self.commun)
        d = rng.standard_normal(DIM)
        d[-1] = 0
        d -= (d @ self.commun) * self.commun
        self.d = d / np.linalg.norm(d)
        self.bruit = rng.standard_normal((64, DIM)) * 0.04
        self.bruit[:, -1] = 0
        self.ancres = {'homme': (0.5 * self.d).tolist(), 'femme': (-0.5 * self.d).tolist()}

    def brute(self, f):
        e = self.commun + (0.35 if f < 300 else -0.35) * self.d + self.bruit[int(f // 25) % 64]
        e[-1] = f * 1e-4
        return e

    def fbank(self, w16, **k):
        enc = np.zeros(80)
        enc[0] = hauteur_dominante(w16)
        return T(np.stack([enc, -enc]))

    def campplus(self, x):
        return T(self.brute(float(_a(x)[0, 0, 0]))[None])


def synthese(f0_s, tag, sr):
    """Son des faux moteurs : la hauteur f0_s (par échantillon ; 0 = silence) et la note témoin de la référence."""
    f0_s = np.asarray(f0_s, np.float64)
    phase = 2 * np.pi * np.cumsum(f0_s) / sr
    t = np.arange(len(f0_s)) / sr
    temoin = 0.03 * np.sin(2 * np.pi * (2000 + 10 * tag) * t) if tag > 0 else 0
    return ((0.1 * np.sin(phase) + temoin) * (f0_s > 1)).astype(np.float32)


class FauxSeedVC:
    """Module inference de Seed-VC : load_models rend un modèle dont le décodeur recopie la hauteur conditionnée
    (régulateur de longueur) et la hauteur de la référence (style), et un vocodeur qui en fait un son."""

    def __init__(self, timbres):
        self.timbres = timbres
        self.device = types.SimpleNamespace(type='cpu')

    def load_models(self, ns):
        sr, hop = (44100, 512) if ns.f0_condition else (22050, 256)

        def length_regulator(s, ylens, n_quantizers=3, f0=None, **k):
            L = int(_a(ylens)[0])
            cond = np.zeros((1, L, 2), np.float32)
            if f0 is not None:
                f = np.asarray(_a(f0), np.float64)[0]
                cond[0, :, 0] = f[np.minimum((np.arange(L) * len(f)) // max(L, 1), len(f) - 1)]
            cond[0, :, 1] = 1
            return (T(cond), None, None, None, None)

        def inference(cat, longueurs, mel2, style2, _, pas, inference_cfg_rate=0.7):
            c = _a(cat)
            out = np.zeros((1, 80, c.shape[1]), np.float32)
            out[0, 0] = c[0, :, 0]
            out[0, 1] = float(_a(style2)[0, -1]) * 1e4
            return T(out)

        def vocodeur(mel):
            m = _a(mel)
            f0_s = np.repeat(m[0, 0], hop)
            tag = float(np.median(m[0, 1])) if m.shape[2] else 0
            return T(synthese(f0_s, tag, sr)[None])
        modele = types.SimpleNamespace(length_regulator=length_regulator, cfm=types.SimpleNamespace(inference=inference))
        return (modele, lambda w16: T(np.zeros((1, _a(w16).shape[-1] // 320 + 1, 4), np.float32)),
                lambda w, thred=0.03: hauteur_trames(w), vocodeur, self.timbres.campplus,
                lambda x: T(np.zeros((1, 80, _a(x).shape[-1] // hop), np.float32)),
                {'sampling_rate': sr, 'hop_size': hop})


# --- Faux fichiers sons : numpy dans le fichier demandé ---------------------------------------------------------------

def ecrire_son(chemin, data, sr, subtype=None, format=None):
    with open(chemin, 'wb') as f:
        np.save(f, np.asarray(data, np.float32))
        np.save(f, np.int64(sr))


def lire_son(chemin, dtype='float32', always_2d=False):
    with open(chemin, 'rb') as f:
        d, sr = np.load(f), int(np.load(f))
    d = d.astype(dtype)
    return (d[:, None] if always_2d and d.ndim == 1 else d), sr


def faux_librosa():
    def load(chemin, sr=None):
        d, s = lire_son(chemin)
        d = d.mean(1) if d.ndim == 2 else d
        return (reechantillonner(d, s, sr) if sr and sr != s else d), (sr or s)
    return types.SimpleNamespace(load=load)


# --- Scène ------------------------------------------------------------------------------------------------------------

def phrases(f0, debut, fin, niveau, duree, trous=()):
    """Phrases de 2,25 s séparées de 0,25 s de silence (le découpage y coupe), sauf dans les trous [(a, b)]."""
    x = np.zeros(int(duree * SR))
    t = debut
    while t < fin - 0.5:
        b = min(t + 2.25, fin)
        morceaux = [(t, b)]
        for a0, b0 in trous:
            morceaux = [p for q in morceaux for p in ((q[0], min(q[1], a0)), (max(q[0], b0), q[1])) if p[1] - p[0] > 0.3]
        for a, b2 in morceaux:
            x += note(f0, a, b2, niveau, duree)
        t = b + 0.25
    return x


def scene(f1=200, f2=450, oooh=True):
    """Chanteur 1 (f1) de 0 à 27 s, chanteur 2 (f2) de 27 à 54 s, puis les deux (chanteur 1 en voix principale,
    chanteur 2 aux chœurs) ; « oooh » du chanteur 2 à 10-12 s, perdu au nettoyage."""
    duree = 80.0
    homme = phrases(f1, 0.0, 27.0, -20, duree, trous=[(10.0, 12.0)]) + phrases(f1, 55.0, 80.0, -20, duree)
    femme = phrases(f2, 27.0, 54.0, -20, duree)
    choeurs = phrases(f2, 55.0, 80.0, -26, duree)   # une seconde de silence avant le duo
    oooh = note(f2, 10.1, 11.9, -22, duree) if oooh else 0
    lead = homme + femme
    return {'voix': lead + choeurs + oooh, 'lead': lead, 'choeurs': choeurs,
            'reste': 0.05 * np.sin(2 * np.pi * 110 * np.arange(int(duree * SR)) / SR)}


SCENE = scene()
SCENE_DEUX_HOMMES = scene(180, 230, oooh=False)
VOIX = {'jules': (120, 24.0, 110.0), 'marie': (230, 22.0, 210.0)}   # hauteur de la référence, durée, parole (voix.json)


def reglages(timbres, moteur='seedvc', cibles=None, octave='auto'):
    r = {'cfg': 0.7, 'fp16': True, 'pad_s': 0.25, 'fondu_s': 0.06, 'graine': 1234, 'modele_parole': 'parole22k',
         'seuil_tonalite': 0.35, 'seuil_reste_db': -12, 'seuil_tenue': 0.12, 'voix_dense': 0.6, 'voise_min_s': 0.8,
         'centre_homme_hz': 250, 'centre_femme_hz': 420, 'sil_parole': 0.2, 'nettoyage_recouvrement': 4, 'pas': 30,
         'ancres': timbres.ancres, 'paroles': False, 'moteur_chant': moteur, 'nettoyage': True,
         'voix': {'nom': 'jules', 'fichier': 'voix-jules.wav', 'f0_parole_hz': 110.0, 'facteur_chant': 2.0},
         'travaux': [{'id': 'v1', 'entree': 'v1.flac', 'mode': 'chant', 'octave': octave, 'comparer_modeles': False,
                      'comparer_pas': None, 'gain_entree': 1.0, 'gain_entree_db': 0.0, 'retour_flac': False}]}
    if cibles:
        voix = [{'cle': c, 'nom': n, 'fichier': f'voix-{n}.wav', 'f0_parole_hz': VOIX[n][2], 'facteur_chant': 2.0,
                 'affichage': n.capitalize()} for c, n in cibles]
        r['voix_cibles'] = {'mode': 'genre' if cibles[0][0] in ('homme', 'femme') else 'ordre', 'voix': voix}
        r['voix'] = {k: voix[0][k] for k in ('nom', 'fichier', 'f0_parole_hz', 'facteur_chant')}
    return r


def lancer_noyau(dossier, R, source=None, scene=SCENE, cartes=1, compteurs=None):
    """Charge le noyau (chemins déplacés dans dossier), remplace ce qui touche au GPU, au réseau et aux modèles, lance
    main() ; rend le dossier de sortie de la vidéo. cartes=2 : une machine à deux GPU, dont les étapes tournent ici en
    même temps, une par fil (le résultat ne doit pas dépendre de la carte qui fait chaque morceau). compteurs (dict) :
    reçoit le nombre de vidéos séparées (« separation »), nettoyées (« nettoyage ») et transcrites (« paroles »)."""
    src = source if source is not None else NOYAU.read_text(encoding='utf-8')
    src = src.replace("'__REGLAGES__'", repr(base64.b64encode(b'{}').decode()))
    src = src.replace('/tmp/', f'{dossier}/t/').replace('/kaggle/', f'{dossier}/kaggle/')   # dossier est lui-même sous /tmp
    m = types.ModuleType('noyau_chaine')
    exec(compile(src, 'noyau_chaine.py', 'exec'), m.__dict__)
    m.REGLAGES = R
    timbres = Timbres()
    torch = faux_torch()
    seedvc = FauxSeedVC(timbres)
    entrees = dossier / 'kaggle' / 'input' / 'jeu'
    entrees.mkdir(parents=True, exist_ok=True)
    (entrees / 'v1.flac').write_bytes(b'')
    for nom, (f, d, _) in VOIX.items():
        ecrire_son(entrees / f'voix-{nom}.wav', note(f, 0.0, d, -20, d), SR)
    (dossier / 't' / 'seed-vc').mkdir(parents=True, exist_ok=True)

    def lancer(*commande, **options):
        assert commande[1].endswith('separer.py'), commande
        for t in json.loads(commande[2]):
            if compteurs is not None:
                compteurs['separation'] = compteurs.get('separation', 0) + 1
            ecrire_son(t['voix'], np.stack([scene['voix']] * 2, 1), SR)
            ecrire_son(t['reste'], np.stack([scene['reste']] * 2, 1), SR)
            with open(t['voix'] + '.duree', 'w') as f:
                f.write('1.0')

    class FauxYing:
        def __init__(self, *a, **k):
            self.torch, self.installation, self.chargement_s = torch, {'origine': 'essai', 'duree_s': 0.0}, 0.0

        def preparer_ref(self, chemin):
            d, s = lire_son(chemin)
            return {'tag': hauteur_dominante(reechantillonner(d, s, 16000))}

        def convertir_morceau(self, xs, ref, decalage, pas):
            f = hauteur_trames(reechantillonner(xs, SR, 16000))
            f = np.where(f > 1, f * 2 ** (decalage / 12), 0)
            f_s = f[np.minimum(np.arange(len(xs)) // 441, len(f) - 1)]
            return synthese(f_s, ref['tag'], SR)

    def etape(nom, travail):
        if compteurs is not None and nom in ('nettoyer', 'paroles'):
            cle = {'nettoyer': 'nettoyage', 'paroles': 'paroles'}[nom]
            compteurs[cle] = compteurs.get(cle, 0) + len(travail['videos'])
        if nom == 'paroles':   # paroles de synthèse : une ligne par vidéo, écrite où le noyau les attend
            for v in travail['videos']:
                m.ecrire_json(v['sortie'], {'format': 1, 'langue': 'fr', 'source': 'faux', 'traductions': {},
                                            'decalage_s': 0.0, 'piste': v['piste'],
                                            'segments': [{'debut': 1.0, 'fin': 2.0, 'texte': 'la la la'}]})
            return {'videos': {v['id']: {'transcription_s': 0.1, 'lignes': 1} for v in travail['videos']}, 'code': 0,
                    'duree_s': 0.1}
        if nom == 'nettoyer':
            res = {'karaoke': 'faux', 'dereverb': 'faux', 'recouvrement': 4, 'chargements_s': {}, 'videos': {}}
            for v in travail['videos']:
                os.makedirs(v['dossier'], exist_ok=True)
                r = res['videos'][v['id']] = {'niveaux_db': {}, 'durees_s': {}, 'duree_s': 0.5}
                for cle, piste in (('lead_sec', 'lead'), ('choeurs_sec', 'choeurs')):
                    r[cle] = os.path.join(v['dossier'], f'{cle}.wav')
                    ecrire_son(r[cle], np.stack([scene[piste]] * 2, 1), SR)
            return {**res, 'code': 0, 'duree_s': 0.5}
        assert nom == 'ying', nom
        m.etape_ying(travail)
        return {**(m.lire_json(travail['resultat'], {}) or {}), 'code': 0, 'duree_s': 0.1}

    def etapes(nom, travaux, n):
        res = [None] * len(travaux)

        def carte(k):
            try:
                res[k] = {**etape(nom, travaux[k]), 'carte': k}
            finally:
                if travaux[k].get('etat'):
                    with open(os.path.join(travaux[k]['etat'], f'fin_{k}'), 'w') as f:
                        f.write('0')
        fils = [threading.Thread(target=carte, args=(k,), daemon=True) for k in reversed(range(len(travaux)))]
        for f in fils:
            f.start()
        for f in fils:
            f.join(120)
            assert not f.is_alive(), 'une carte attend sans fin'
        return res

    if cartes > 1:
        torch.cuda.device_count = lambda: cartes
        m.etapes_a_part = etapes
        m.installer_ying = lambda o=None: {'origine': 'essai', 'duree_s': 0.0}

    for nom, valeur in {'verifier_acces': lambda: None, 'trouver_outils': lambda: None, 'installer': lambda o=None: None,
                        'cloner': lambda o=None: 'faux', 'poids_depuis_outils': lambda o: None,
                        'copier_paroles': lambda o: {}, 'log': lambda *a: None, 'lancer': lancer, 'etape_a_part': etape,
                        'MoteurYing': FauxYing,
                        'mesures_musique': lambda v, r: {'reste_sous_voix_db': -5.0, 'reste_actif': 0.9,
                                                         'tonalite_reste': 0.5, 'voix_active': 0.8,
                                                         'niveau_voix_dbfs': -20.0, 'niveau_reste_dbfs': -26.0}}.items():
        setattr(m, nom, valeur)
    modules = {'soundfile': types.SimpleNamespace(write=ecrire_son, read=lire_son), 'torch': torch,
               'torchaudio': types.SimpleNamespace(
                   functional=types.SimpleNamespace(resample=lambda x, de, vers: T(reechantillonner(x, de, vers))),
                   compliance=types.SimpleNamespace(kaldi=types.SimpleNamespace(fbank=timbres.fbank))),
               'librosa': faux_librosa(), 'inference': seedvc}
    ici, chemin = os.getcwd(), list(sys.path)
    try:
        with mock.patch.dict(sys.modules, modules), warnings.catch_warnings():
            warnings.simplefilter('ignore', ResourceWarning)   # le noyau d'avant laisse un fichier ouvert
            m.main()
    finally:
        os.chdir(ici)
        sys.path[:] = chemin
    return dossier / 'kaggle' / 'working' / 'v1'


def temoins(y, a, b):
    """Énergie (dB) des notes témoins de jules (3200 Hz) et de marie (4300 Hz) dans y entre a et b secondes."""
    seg = np.asarray(y[int(a * SR):int(b * SR)], np.float64)
    s = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    tot = s.sum() + 1e-20
    return {n: 10 * np.log10(s[(f > c - 60) & (f < c + 60)].sum() / tot + 1e-20) for n, c in (('jules', 3200), ('marie', 4300))}


def hauteur_sortie(y, a, b):
    seg = np.asarray(y[int(a * SR):int(b * SR)], np.float64)
    s = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    bnd = (f > 60) & (f < 1500)
    return float(f[bnd][np.argmax(s[bnd])])


def sans_durees(x):
    """Le rapport sans ce qui dépend de l'horloge (durées, rapports temps réel)."""
    if isinstance(x, dict):
        return {k: sans_durees(v) for k, v in x.items() if k != 'temps' and not re.search(r'(_s|rtf)$', k)}
    if isinstance(x, list):
        return [sans_durees(v) for v in x]
    return x


class ChaineDeuxVoix(unittest.TestCase):
    """homme:jules,femme:marie sur la scène : chaque passage chante dans la voix de son chanteur, le duo en deux voix."""

    @classmethod
    def setUpClass(cls):
        cls.res = {}
        for moteur in ('seedvc', 'yingmusic'):
            with Dossier() as d:
                sortie = lancer_noyau(d, reglages(Timbres(), moteur, [('homme', 'jules'), ('femme', 'marie')]))
                cls.res[moteur] = (lire_son(sortie / 'voix_convertie.flac')[0],
                                   json.loads((sortie / 'rapport.json').read_text()),
                                   json.loads((sortie / 'courbes.json').read_text()))

    def test_voix_de_chaque_passage(self):
        for moteur, (y, rapport, _) in self.res.items():
            with self.subTest(moteur=moteur):
                self.assertEqual(rapport['moteur'], moteur)
                for (a, b), attendu in (((1, 9), {'jules'}), ((10.4, 11.6), {'marie'}), ((13, 26), {'jules'}),
                                        ((28, 53), {'marie'}), ((56, 79), {'jules', 'marie'})):
                    t = temoins(y, a, b)
                    entendues = {n for n, db in t.items() if db > -30}
                    self.assertEqual(entendues, attendu, f'{a}-{b} s : {t}')

    def test_octaves(self):
        y = self.res['seedvc'][0]
        self.assertAlmostEqual(hauteur_sortie(y, 1, 9), 200, delta=8)    # chanteur -> jules : 0
        self.assertAlmostEqual(hauteur_sortie(y, 28, 53), 450, delta=12)  # chanteuse -> marie : 0

    def test_rapport(self):
        rapport = self.res['seedvc'][1]
        self.assertEqual([v['nom'] for v in rapport['voix_cibles']['voix']], ['jules', 'marie'])
        dh = rapport['decision_hauteur']
        self.assertEqual(dh['voix'], 2)
        self.assertEqual({(c['classe'], c['voix'], c['decalage']) for c in dh['chanteurs']},
                         {('homme', 'jules', 0), ('femme', 'marie', 0)})
        aff = rapport['affectation']
        self.assertEqual(aff['voix_utilisees'], ['jules', 'marie'])
        self.assertGreaterEqual(aff['morceaux_en_duo'], 3)
        self.assertEqual(set(aff['pistes']), {'lead_sec', 'choeurs_sec', 'secours'})
        self.assertEqual({l['voix'] for l in aff['pistes']['choeurs_sec']}, {'marie'})
        self.assertEqual({l['voix'] for l in aff['pistes']['secours']}, {'marie'})
        self.assertEqual({l['etat'] for l in aff['pistes']['secours']}, {'autre'})
        # voix principale : la voix du chanteur de chaque section (un morceau à cheval sur deux sections, coupé au
        # silence, prend celle que l'on y reconnaît le mieux)
        for l in aff['pistes']['lead_sec']:
            if l['fin_s'] <= 27.2 or l['debut_s'] >= 54.0:
                self.assertEqual(l['voix'], 'jules', l)
            elif l['debut_s'] >= 26.8 and l['fin_s'] <= 54.8:
                self.assertEqual(l['voix'], 'marie', l)
        self.assertGreater(aff['secondes_par_voix']['ensemble'], 15)
        duo = [m for m in rapport['morceaux'] if m['debut_s'] >= 54.5]
        self.assertTrue(all(m.get('voix_pistes', {}).get('choeurs_sec') == 'marie' for m in duo))
        self.assertTrue(all(m['voix_cible'] == 'jules' for m in duo))
        self.assertNotIn('k', rapport['morceaux'][0])
        self.assertEqual(set(rapport['variantes']['voix_convertie.flac']['ressemblance_timbre_par_voix']), {'jules', 'marie'})

    def test_courbes(self):
        _, rapport, courbes = self.res['seedvc']
        self.assertEqual(courbes['format'], 1)
        self.assertEqual(courbes['voix_cibles'], ['jules', 'marie'])
        v = courbes['voix_par_trame']
        self.assertEqual(len(v), len(courbes['source_hz']))
        self.assertEqual(v[int(5 / 0.02)], 0)
        self.assertEqual(v[int(11 / 0.02)], 1)
        self.assertEqual(v[int(40 / 0.02)], 1)
        self.assertEqual(v[int(65 / 0.02)], 2, 'les deux voix ensemble')
        self.assertEqual(v[int(54.5 / 0.02)], -1, 'silence avant le duo')


class ChaineCouplesCroises(unittest.TestCase):
    """homme:marie,femme:jules : le chanteur monte d'une octave vers marie, la chanteuse descend vers jules."""

    def test_octave_de_chaque_couple(self):
        with Dossier() as d:
            sortie = lancer_noyau(d, reglages(Timbres(), 'seedvc', [('homme', 'marie'), ('femme', 'jules')]))
            y = lire_son(sortie / 'voix_convertie.flac')[0]
            rapport = json.loads((sortie / 'rapport.json').read_text())
        self.assertEqual({(c['classe'], c['voix'], c['decalage']) for c in rapport['decision_hauteur']['chanteurs']},
                         {('homme', 'marie', 12), ('femme', 'jules', -12)})
        self.assertAlmostEqual(hauteur_sortie(y, 1, 9), 400, delta=15)
        self.assertAlmostEqual(hauteur_sortie(y, 28, 53), 225, delta=10)
        self.assertEqual({n for n, db in temoins(y, 28, 53).items() if db > -30}, {'jules'})


class ChaineDeuxHommes(unittest.TestCase):
    """chanteur1:jules,chanteur2:marie sur deux chanteurs (180 et 230 Hz) : départagés par l'empreinte, dans l'ordre
    d'apparition ; le chanteur 2 monte d'une octave vers marie."""

    def test_ordre_d_apparition(self):
        with Dossier() as d:
            sortie = lancer_noyau(d, reglages(Timbres(), 'yingmusic', [('chanteur1', 'jules'), ('chanteur2', 'marie')]),
                                  scene=SCENE_DEUX_HOMMES)
            y = lire_son(sortie / 'voix_convertie.flac')[0]
            rapport = json.loads((sortie / 'rapport.json').read_text())
        dh = rapport['decision_hauteur']
        self.assertEqual(dh['axe'], 'empreintes')
        self.assertEqual({(c['nom'], c['voix'], c['decalage']) for c in dh['chanteurs']},
                         {('homme 1', 'jules', 0), ('homme 2', 'marie', 12)})
        for (a, b), attendu in (((1, 26), {'jules'}), ((28, 53), {'marie'}), ((56, 79), {'jules', 'marie'})):
            self.assertEqual({n for n, db in temoins(y, a, b).items() if db > -30}, attendu, (a, b))
        self.assertAlmostEqual(hauteur_sortie(y, 28, 53), 460, delta=15)
        self.assertEqual({l['voix'] for l in rapport['affectation']['pistes']['choeurs_sec']}, {'marie'})


@unittest.skipUnless(HISTORIQUE, 'historique git absent')
class ChaineUneVoixAuBitPres(unittest.TestCase):
    """Une seule voix : la chaîne entière rend exactement ce que rendait le noyau d'avant les voix multiples."""

    def test_comme_avant(self):
        ancien = subprocess.run(['git', '-C', str(DEPOT), 'show', f'{AVANT}:kaggle/doubler_kaggle.py'],
                                capture_output=True, text=True, check=True).stdout
        for moteur in ('seedvc', 'yingmusic'):
            sorties = []
            for source in (None, ancien):
                with Dossier() as d:
                    s = lancer_noyau(d, reglages(Timbres(), moteur), source)
                    rapport = (s / 'rapport.json').read_text().replace(str(d), '<dossier>')
                    sorties.append((lire_son(s / 'voix_convertie.flac')[0], (s / 'courbes.json').read_text(),
                                    sans_durees(json.loads(rapport))))
            with self.subTest(moteur=moteur):
                (y1, c1, r1), (y0, c0, r0) = sorties
                # précision des Roformer du nettoyage : notée dans le rapport depuis le banc vitesse du 06/11/2026
                # (ici celle du faux nettoyage, qui n'en dit rien : fp32)
                self.assertEqual((r1.get('nettoyage') or {}).pop('precision', None), 'fp32')
                self.assertTrue(np.array_equal(y1, y0), 'voix convertie')
                self.assertEqual(c1, c0, 'courbes.json')
                self.assertEqual(json.dumps(r1, sort_keys=True), json.dumps(r0, sort_keys=True), 'rapport.json')
                self.assertNotIn('voix_cibles', r1)
                self.assertNotIn('affectation', r1)


class ChaineDeuxCartes(unittest.TestCase):
    """Machine à deux GPU : YingMusic-SVC partagé entre les cartes (morceaux répartis, piste assemblée dans le noyau)
    rend la même voix convertie, les mêmes courbes et le même rapport qu'une carte, au bit près, avec une voix comme avec
    une voix par chanteur ; réglage gpus à 1 : une seule carte, même sur la machine à deux."""

    def sorties(self, R, cartes):
        with Dossier() as d:
            s = lancer_noyau(d, R, cartes=cartes)
            rapport = (s / 'rapport.json').read_text().replace(str(d), '<dossier>')
            session = json.loads((s.parent / 'session.json').read_text())
            return (lire_son(s / 'voix_convertie.flac')[0], (s / 'courbes.json').read_text(),
                    sans_durees(json.loads(rapport)), session)

    def test_meme_resultat(self):
        for cibles in (None, [('homme', 'jules'), ('femme', 'marie')]):
            (y1, c1, r1, s1), (y2, c2, r2, s2) = (self.sorties(reglages(Timbres(), 'yingmusic', cibles), n) for n in (1, 2))
            with self.subTest(cibles=bool(cibles)):
                self.assertEqual((s1['gpus']['utilisees'], s2['gpus']['utilisees']), (1, 2))
                self.assertEqual(len(s2['yingmusic']['cartes']), 2, 'les deux cartes ont converti')
                self.assertTrue(np.array_equal(y1, y2), 'voix convertie')
                self.assertEqual(c1, c2, 'courbes.json')
                self.assertEqual(json.dumps(r1, sort_keys=True), json.dumps(r2, sort_keys=True), 'rapport.json')

    def test_une_carte_demandee(self):
        R = {**reglages(Timbres(), 'yingmusic'), 'gpus': 1}
        s = self.sorties(R, 2)[3]
        self.assertEqual((s['gpus']['visibles'], s['gpus']['utilisees']), (2, 1))
        self.assertNotIn('cartes', s['yingmusic'])


if __name__ == '__main__':
    unittest.main()
