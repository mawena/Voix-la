"""F0 locale (librosa pyin) d'une voix isolée, morceau par morceau, au format des `morceaux` de rapport.json.

Sert aux cas de référence qui n'ont pas de rapport `doubler` (p. ex. une voix de chanteuse séparée sur le PC). Les
coupes viennent d'un rapport existant (`coupes_s`, `morceaux_muets`) ; sinon morceaux de 8 s. Pas d'empreinte CAMPPlus
en local : genre_score reste vide. Centre de chant de la voix cible : --centre, sinon « centre_chant_hz » du fichier
des cas (--cas, défaut octave/cas.json ; modèle : octave/cas.exemple.json).

    python -I f0_locale.py <voix.wav> <sortie.json> [--coupes rapport_conversion.json] [--centre HZ] [--cas FICHIER] [--nom ...]
"""
import argparse
import json
import os
import time

import librosa
import numpy as np
import soundfile as sf

p = argparse.ArgumentParser()
p.add_argument('voix')
p.add_argument('sortie')
p.add_argument('--coupes')
p.add_argument('--centre', type=float, help='centre de chant de la voix cible (Hz) ; défaut : centre_chant_hz du fichier des cas')
p.add_argument('--cas', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cas.json'))
p.add_argument('--nom', default='')
a = p.parse_args()
if a.centre is None:
    try:
        a.centre = float(json.load(open(a.cas, encoding='utf-8'))['centre_chant_hz'])
    except (OSError, ValueError, KeyError, TypeError) as e:
        p.error(f'--centre manque, et pas de centre_chant_hz lisible dans {a.cas} ({e!r})')

t0 = time.time()
x, sr = sf.read(a.voix, dtype='float32', always_2d=True)
x = x.mean(1)
y = librosa.resample(x, orig_sr=sr, target_sr=16000)
del x
f0, vf, _ = librosa.pyin(y, fmin=65, fmax=1000, sr=16000, frame_length=1024, hop_length=160)
f0 = np.where(vf, f0, 0.0)
duree = len(y) / 16000
muets = set()
if a.coupes:
    r = json.load(open(a.coupes))
    coupes = r['coupes_s']
    muets = set(r.get('morceaux_muets', []))
else:
    coupes = list(np.arange(0, duree, 8.0)) + [duree]
morceaux = []
for i, (d, f) in enumerate(zip(coupes[:-1], coupes[1:])):
    c = f0[int(d * 100):int(f * 100)]
    v = c[c > 1]
    muet = i in muets or len(v) < 10
    morceaux.append({'i': i, 'debut_s': round(d, 3), 'fin_s': round(f, 3), 'muet': bool(muet),
                     'f0_med': None if muet else round(float(np.median(v)), 1),
                     'voise_s': 0.0 if muet else round(len(v) * 0.01, 2), 'genre_score': None})
v = f0[f0 > 1]
sortie = {'id': a.nom, 'source': a.voix, 'mode': 'chant', 'origine': 'f0_locale.py (librosa pyin 16 kHz, pas 10 ms)',
          'voix_cible': {'centre_chant_hz': a.centre}, 'duree_s': round(duree, 3),
          'f0_mediane_trames_hz': round(float(np.median(v)), 1),
          'f0_p10_p90_trames_hz': [round(float(np.percentile(v, 10)), 1), round(float(np.percentile(v, 90)), 1)],
          'morceaux': morceaux, 'calcul_s': round(time.time() - t0, 1)}
json.dump(sortie, open(a.sortie, 'w'), ensure_ascii=False, indent=1)
print(f"{a.sortie} : {len(morceaux)} morceaux, médiane {sortie['f0_mediane_trames_hz']} Hz, {sortie['calcul_s']} s")
