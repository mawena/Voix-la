"""Ancres de genre (ancres_genre.json) bâties sur un corpus public de parole, et empreintes locales des rapports.

    python -I ancres.py parole   --corpus DOSSIER --locuteurs SPEAKERS.TXT --modules DOSSIER --poids FICHIER
                                 [--enonces 25] --sortie corpus.npz
    python -I ancres.py batir    --corpus corpus.npz [--echelle 2] --sortie ancres_genre.json
                                 [--cas octave/cas.json --sorties DOSSIER [--empreintes DOSSIER]]
    python -I ancres.py rapports --sorties DOSSIER --modules DOSSIER --poids FICHIER --sortie DOSSIER

Pourquoi un corpus de parole : l'empreinte CAMPPlus marque d'abord le locuteur, ensuite son genre. Des ancres bâties sur
une poignée de voix (une voix d'homme et une voix de femme, ou les quelques chanteurs des cas) donnent un axe de
locuteur plus que de genre : validées en laissant de côté un chanteur à la fois, elles classent mal les voix qu'elles
n'ont pas vues. Sur un corpus équilibré de dizaines de locuteurs, la moyenne des centroïdes par locuteur de chaque genre
efface les locuteurs et garde le genre ; aucun chanteur des cas n'y entre, donc tous restent des cas de validation.

parole   : empreintes de chaque énoncé (coupé à 8 s, comme un morceau chanté) d'un corpus rangé comme LibriSpeech
           (<corpus>/<locuteur>/<chapitre>/*.flac, 16 kHz), au plus --enonces par locuteur ; genre lu dans SPEAKERS.TXT
           (« id | F ou M | ... »). Modèle : CAMPPlus de Seed-VC (--modules : dossier qui contient modules/campplus)
           et ses poids funasr/campplus campplus_cn_common.bin (--poids), comme le noyau.
batir    : ancres = --echelle x (centroïde moyen, normé, des locuteurs de chaque genre ; chaque locuteur pèse autant).
           L'échelle étire l'axe sans le changer : genre_score = échelle x (cos(e, hommes) - cos(e, femmes)), pour que
           les seuils de la règle (+0,03, -0,10, écart de duo 0,15) gardent leur sens. Contrôles : chaque locuteur
           noté par des ancres bâties sans lui ; tirages de sous-corpus ; avec --cas, empreinte médiane de chaque voix
           des cas de référence (vérité du fichier des cas). La règle entière se rejoue ensuite avec
           « rejouer.py --kaggle --ancres <sortie> ».
rapports : empreintes, calculées comme le noyau, des morceaux des rapports qui n'en ont pas gardé (rapports d'avant
           l'ajout de rapport['empreintes']), à partir de travail/voix_source.wav ; un fichier JSON par cas, à donner
           à rejouer.py --empreintes (ou à ranger dans donnees/empreintes/ à côté du fichier des cas).

Le corpus, les empreintes et les ancres restent sur le poste : rien de tout cela n'entre dans le dépôt.
"""
import argparse
import base64
import glob
import hashlib
import json
import os
import sys

import numpy as np

ICI = os.path.dirname(os.path.abspath(__file__))
SR = 44100


def campplus(modules, poids):
    """Fonction empreinte(signal 16 kHz) -> vecteur normé (192), comme empreinte() du noyau."""
    import torch
    import torchaudio
    torch.set_num_threads(4)
    sys.path.insert(0, modules)
    from modules.campplus.DTDNN import CAMPPlus
    cp = CAMPPlus(feat_dim=80, embedding_size=192)
    cp.load_state_dict(torch.load(poids, map_location='cpu', weights_only=True))
    cp.eval()

    def empreinte(w16):
        w = torch.from_numpy(np.ascontiguousarray(w16)).float()[None]
        feat = torchaudio.compliance.kaldi.fbank(w, num_mel_bins=80, dither=0, sample_frequency=16000)
        with torch.no_grad():
            e = cp((feat - feat.mean(dim=0, keepdim=True))[None])[0].float()
        return (e / (e.norm() + 1e-9)).numpy()

    def en16(y):
        return torchaudio.functional.resample(torch.from_numpy(np.ascontiguousarray(y)).float()[None], SR, 16000)[0].numpy()
    return empreinte, en16


def lire_genres(chemin):
    """{locuteur: 'homme' | 'femme'} d'un SPEAKERS.TXT de LibriSpeech (lignes « id | F | ... », « ; » : commentaire)."""
    g = {}
    for ligne in open(chemin, encoding='utf-8', errors='replace'):
        if ligne.startswith(';') or '|' not in ligne:
            continue
        champs = [x.strip() for x in ligne.split('|')]
        if champs[1] in ('F', 'M'):
            g[champs[0]] = 'femme' if champs[1] == 'F' else 'homme'
    return g


def cmd_parole(a):
    import soundfile as sf
    empreinte, _ = campplus(a.modules, a.poids)
    genres = lire_genres(a.locuteurs)
    out, sexe = {}, {}
    for d in sorted(glob.glob(os.path.join(a.corpus, '*'))):
        loc = os.path.basename(d)
        if loc not in genres:
            continue
        E = []
        for f in sorted(glob.glob(os.path.join(d, '*', '*.flac')))[:a.enonces]:
            y, sr = sf.read(f, dtype='float32')
            if sr != 16000:
                raise SystemExit(f'{f} : {sr} Hz, 16 kHz attendus')
            y = y[:8 * sr]
            if len(y) >= sr:
                E.append(empreinte(y))
        if E:
            out[loc], sexe[loc] = np.stack(E), genres[loc]
            print(f'{loc} ({sexe[loc]}) : {len(E)} énoncés', flush=True)
    np.savez(a.sortie, **out, _genres=np.array(json.dumps(sexe)))
    print(f"{a.sortie} : {sum(v == 'homme' for v in sexe.values())} hommes, {sum(v == 'femme' for v in sexe.values())} femmes")


def nrm(v):
    return v / np.linalg.norm(v)


def axe(centroides):
    """centroides : {locuteur: (centroïde normé, genre)} -> (homme, femme) normés, chaque locuteur pesant autant."""
    h = nrm(np.mean([c for c, g in centroides.values() if g == 'homme'], 0))
    f = nrm(np.mean([c for c, g in centroides.values() if g == 'femme'], 0))
    return h, f


def cmd_batir(a):
    z = np.load(a.corpus)
    sexe = json.loads(str(z['_genres']))
    E = {k: z[k] for k in z.files if k != '_genres'}
    C = {k: (nrm(E[k].mean(0)), sexe[k]) for k in E}
    nh, nf = sum(g == 'homme' for _, g in C.values()), sum(g == 'femme' for _, g in C.values())
    if min(nh, nf) < 5:
        raise SystemExit(f'{nh} hommes et {nf} femmes : trop peu de locuteurs pour un axe de genre')
    h, f = axe(C)
    k = a.echelle
    print(f'{nh} hommes, {nf} femmes ; cos(homme, femme) {h @ f:.3f} ; échelle {k:g}')
    # chaque locuteur noté par des ancres bâties sans lui
    notes = {}
    for loc in C:
        hh, ff = axe({x: v for x, v in C.items() if x != loc})
        notes[loc] = float(np.median(k * (E[loc] @ hh - E[loc] @ ff)))
    for g in ('homme', 'femme'):
        v = sorted(round(notes[x], 3) for x in C if C[x][1] == g)
        print(f'  {g}s, chacun laissé de côté : {v}')
    mal = [x for x in C if (notes[x] > 0) != (C[x][1] == 'homme')]
    print(f'  du mauvais côté de 0 : {len(mal)} / {len(C)}')
    if a.cas:
        verifier_cas(a, k * h, k * f, C, k)
    n_max = max(len(e) for e in E.values())
    note = (f'Ancres de genre bâties sur un corpus de parole lue ({nh} hommes, {nf} femmes, au plus {n_max} énoncés '
            f'chacun) : {k:g} x centroïde moyen normé des empreintes CAMPPlus des locuteurs de chaque genre '
            '(chaque locuteur pèse autant). Aucune voix chantée, aucune voix cible. Construites par octave/ancres.py.')
    json.dump({'homme': [round(float(x), 6) for x in k * h], 'femme': [round(float(x), 6) for x in k * f], 'note': note,
               'construction': {'locuteurs_hommes': nh, 'locuteurs_femmes': nf, 'echelle': k}},
              open(a.sortie, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'{a.sortie} écrit ; à valider avec : rejouer.py --kaggle --ancres {a.sortie} [--sensibilite]')


def verifier_cas(a, h, f, C, k):
    """Empreinte médiane de chaque voix des cas de référence (une voix = les morceaux d'une même vérité), et stabilité :
    300 tirages de deux tiers des locuteurs de chaque genre."""
    sys.path.insert(0, ICI)
    import decision
    import rejouer
    rejouer.SORTIES = a.sorties or rejouer.SORTIES
    fc = rejouer.lire_fichier_cas(a.cas)
    donnees = os.path.join(os.path.dirname(os.path.abspath(a.cas)), 'donnees')
    emp = a.empreintes or (os.path.join(donnees, 'empreintes') if os.path.isdir(os.path.join(donnees, 'empreintes')) else None)
    cas = [c for c in rejouer.charger_cas(donnees, emp) if c['mode'] == 'chant']
    R = decision.REGLAGES_DEFAUT
    voix = []
    for c in cas:
        v = rejouer.verite(c['nom'], c['morceaux'], fc['references'])
        if v is None:
            continue
        for d in sorted(set(v[0])):
            ms = [s for s, x, dt in zip(c['morceaux'], v[0], v[1]) if x == d and not dt and s.get('emb') is not None
                  and not s['muet'] and s['voise_s'] >= R['voise_min_s']]
            if ms:
                voix.append((c['nom'], d, ms))
    if not voix:
        print('  cas : aucune voix avec empreintes')
        return

    def mediane(ms, hh, ff):
        return decision.mediane_ponderee([float(np.asarray(s['emb']) @ (hh - ff)) for s in ms], [s['voise_s'] for s in ms])
    print('  voix des cas de référence (décalage attendu, hauteur médiane, empreinte médiane) :')
    for nom, d, ms in voix:
        m = decision.mesurer_voix(ms, R)
        print(f"    {nom[:44]:44s} {d:+3d}  {m.get('f0_med_hz', 0):5.0f} Hz  {mediane(ms, h, f):+.3f}")
    rng = np.random.default_rng(0)
    hs = [x for x in C if C[x][1] == 'homme']
    fs = [x for x in C if C[x][1] == 'femme']
    marges = []
    for _ in range(300):
        sous = {x: C[x] for x in list(rng.choice(hs, max(2, 2 * len(hs) // 3), replace=False))
                + list(rng.choice(fs, max(2, 2 * len(fs) // 3), replace=False))}
        hh, ff = axe(sous)
        meds = [(d, mediane(ms, k * hh, k * ff)) for _, d, ms in voix]
        marges.append(min((m for d, m in meds if d == 0), default=np.nan) - max((m for d, m in meds if d != 0), default=np.nan))
    print(f'  tirages de 2/3 des locuteurs : marge (voix à 0 la plus basse - voix à -12 la plus haute) 5e centile '
          f'{np.nanpercentile(marges, 5):+.3f}, médiane {np.nanmedian(marges):+.3f} ; marge <= 0 : {sum(m <= 0 for m in marges)} / 300')


def cmd_rapports(a):
    import soundfile as sf
    empreinte, en16 = campplus(a.modules, a.poids)
    os.makedirs(a.sortie, exist_ok=True)
    for f in sorted(glob.glob(os.path.join(a.sorties, '*', 'travail', '*', 'rapport.json'))):
        r = json.load(open(f, encoding='utf-8'))
        nom = f.split(os.sep)[-4]
        if r.get('mode') != 'chant' or (r.get('empreintes') or {}).get('morceaux'):
            continue
        src = os.path.join(os.path.dirname(os.path.dirname(f)), 'voix_source.wav')
        if not os.path.exists(src):
            print(f'{nom} : pas de voix_source.wav, passé')
            continue
        x = sf.read(src, dtype='float32', always_2d=True)[0].mean(1)
        out = {}
        for s in r['morceaux']:
            d, fin = int(round(s['debut_s'] * SR)), int(round(s['fin_s'] * SR))
            if s['muet'] or fin - d < SR // 2:
                continue
            out[str(s['i'])] = base64.b64encode(empreinte(en16(x[d:fin])).astype('<f2').tobytes()).decode()
        chemin = os.path.join(a.sortie, hashlib.md5(nom.encode()).hexdigest()[:10] + '.json')
        json.dump({'nom': nom, 'format': 'CAMPPlus normée : 192 float16 petit-boutistes en base64, par numéro de morceau',
                   'morceaux': out}, open(chemin, 'w', encoding='utf-8'), ensure_ascii=False)
        print(f'{nom[:50]} : {len(out)} empreintes -> {chemin}', flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sp = p.add_subparsers(dest='cmd', required=True)
    q = sp.add_parser('parole', help='empreintes d\'un corpus de parole (genre connu)')
    q.add_argument('--corpus', required=True)
    q.add_argument('--locuteurs', required=True, help='SPEAKERS.TXT (genre de chaque locuteur)')
    q.add_argument('--modules', required=True, help='dossier qui contient modules/campplus (Seed-VC)')
    q.add_argument('--poids', required=True, help='campplus_cn_common.bin')
    q.add_argument('--enonces', type=int, default=25)
    q.add_argument('--sortie', required=True)
    q = sp.add_parser('batir', help='ancres à partir des empreintes du corpus')
    q.add_argument('--corpus', required=True, help='sortie de « parole » (.npz)')
    q.add_argument('--echelle', type=float, default=2.0)
    q.add_argument('--sortie', required=True)
    q.add_argument('--cas', default=None, help='fichier des cas, pour noter leurs voix')
    q.add_argument('--sorties', default=None)
    q.add_argument('--empreintes', default=None)
    q = sp.add_parser('rapports', help='empreintes locales des rapports qui n\'en ont pas')
    q.add_argument('--sorties', required=True)
    q.add_argument('--modules', required=True)
    q.add_argument('--poids', required=True)
    q.add_argument('--sortie', required=True)
    a = p.parse_args()
    {'parole': cmd_parole, 'batir': cmd_batir, 'rapports': cmd_rapports}[a.cmd](a)


if __name__ == '__main__':
    main()
