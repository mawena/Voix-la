#!/usr/bin/python3
"""Rejoue sur le PC, sans GPU, la détection des passages perdus au nettoyage (zones_perdues du noyau) sur un doublage
déjà fait avec la voix nettoyée, avant la piste de secours.

Les pistes nettoyées ne reviennent pas de Kaggle : leur somme est reconstituée d'après la voix convertie sèche du mix
(travail/<voix>/seche_voix_convertie.wav), qui suit la voix d'origine là où quelque chose a été converti et se tait
ailleurs. Sur chaque trame de 10 ms, la voix complète (travail/voix_source.wav) est atténuée de l'écart mesuré entre
elle et la voix convertie (ramenées au même niveau médian). La hauteur RMVPE vient de courbes.json (une valeur toutes
les 20 ms). Lecture seule : rien n'est écrit.

  python3 kaggle/secours_local.py "<dossier de sortie d'un doublage>" [--voix NOM] [--debut S --duree S] [--json]

--debut/--duree rejouent la détection sur une fenêtre seulement, comme le ferait le noyau sur un extrait (seuils
calculés sur l'extrait).
"""
import argparse
import base64
import json
import subprocess
import sys
import types
from pathlib import Path

import numpy as np

NOYAU = Path(__file__).resolve().parent / 'doubler_kaggle.py'


def charger_noyau(chemin=NOYAU):
    """Le noyau comme module, sans rien lancer : ses réglages (remplacés par doubler.py avant l'envoi) valent {}."""
    src = Path(chemin).read_text(encoding='utf-8').replace("'__REGLAGES__'", repr(base64.b64encode(b'{}').decode()))
    m = types.ModuleType('doubler_kaggle')
    m.__file__ = str(chemin)
    exec(compile(src, str(chemin), 'exec'), m.__dict__)
    return m


def lire(fichier, debut=None, duree=None, sr=44100):
    """Son mono flottant lu par ffmpeg (aucune bibliothèque audio à installer)."""
    cmd = ['ffmpeg', '-v', 'error', *(['-ss', f'{debut:.3f}'] if debut else []), *(['-t', f'{duree:.3f}'] if duree else []),
           '-i', str(fichier), '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-']
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode or not r.stdout:
        raise SystemExit(f'son illisible : {fichier}\n{r.stderr.decode(errors="replace")[-400:]}')
    return np.frombuffer(r.stdout, np.float32).astype(np.float64)


def reconstituer(N, x, seche):
    """Somme des pistes nettoyées reconstituée : la voix complète atténuée, trame par trame, de son écart à la voix
    convertie sèche (ramenée au même niveau médian sur les trames où les deux sonnent)."""
    n = min(len(x), len(seche))
    x, seche = x[:n], seche[:n]
    lx, ls = N.lisser_db(N.enveloppe_db(x)), N.lisser_db(N.enveloppe_db(seche))
    deux = (lx > N.seuil_silence(N.enveloppe_db(x))) & (ls > N.seuil_silence(N.enveloppe_db(seche)))
    decal = float(np.median(lx[deux] - ls[deux])) if deux.any() else 0.0
    ecart = np.clip(lx - (ls + decal), 0, 120)
    g = 10 ** (-np.interp(np.arange(n), np.arange(len(ecart)) * N.PAS, ecart) / 20)
    return x, x * g, decal


def detecter(N, x, nette, f0):
    """La détection du noyau, telle que preparer_pistes l'appelle (piste nettoyée : sa porte et son seuil)."""
    env = N.enveloppe_db(x)
    lisse, seuil = N.lisser_db(env), N.seuil_silence(env)
    env_t = N.enveloppe_db(nette)
    lisse_t, seuil_t = N.lisser_db(env_t), N.seuil_silence(env_t)
    _, trames_t = N.porte(lisse_t, seuil_t, len(nette))
    zones, info = N.zones_perdues(env, lisse, seuil, f0, [(lisse_t, trames_t)])
    voix_active_s = float((lisse > seuil).sum()) * 0.01
    decision, secondes = N.decider_secours(zones, voix_active_s)
    return zones, info, decision, secondes, voix_active_s


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split('\n', 1)[1])
    p.add_argument('dossier', help='dossier de sortie d\'un doublage (celui qui contient travail/)')
    p.add_argument('--voix', default=None, help='sous-dossier de travail/ (défaut : le seul qui a courbes.json)')
    p.add_argument('--debut', type=float, default=None)
    p.add_argument('--duree', type=float, default=None)
    p.add_argument('--json', action='store_true')
    a = p.parse_args()
    t = Path(a.dossier).expanduser() / 'travail'
    voix = [d for d in sorted(t.glob('*/courbes.json'))] if a.voix is None else [t / a.voix / 'courbes.json']
    if len(voix) != 1 or not voix[0].exists():
        raise SystemExit(f'courbes.json introuvable ou ambigu sous {t} : préciser --voix')
    tv = voix[0].parent
    N = charger_noyau()
    x = lire(t / 'voix_source.wav', a.debut, a.duree)
    seche = lire(tv / 'seche_voix_convertie.wav', a.debut, a.duree)
    x, nette, decal = reconstituer(N, x, seche)
    c = json.loads((tv / 'courbes.json').read_text())
    f0 = np.repeat(np.asarray(c['source_hz'], np.float64), int(round(c['pas_s'] / 0.01)))
    k0 = int(round((a.debut or 0) / 0.01))
    f0 = f0[k0:k0 + len(x) // N.PAS + 1]
    zones, info, decision, secondes, active = detecter(N, x, nette, f0)
    origine = a.debut or 0.0
    for z in zones + info['ecartees']:
        z['debut_s'], z['fin_s'] = round(z['debut_s'] + origine, 2), round(z['fin_s'] + origine, 2)
        z['ouvert'] = [[round(a + origine, 2), round(b + origine, 2)] for a, b in z.get('ouvert', [])]
    if a.json:
        print(json.dumps({'decision': decision, 'secondes': secondes, 'voix_active_s': round(active, 1), 'zones': zones,
                          'ecartees': info['ecartees'], 'haut_voix_dbfs': info['haut_voix_dbfs'],
                          'plancher_dbfs': info['plancher_dbfs'], 'decalage_seche_db': round(decal, 2)},
                         ensure_ascii=False, indent=1))
        return 0
    print(f"voix complète : haut {info['haut_voix_dbfs']} dBFS, plancher {info['plancher_dbfs']} dBFS ; voix active "
          f"{active:.1f} s ; voix convertie {decal:+.1f} dB sous la voix (médiane)")
    print(f'décision : {decision} ({len(zones)} zone(s), {secondes:.2f} s)')
    for z in zones:
        print(f"  gardée   {z['debut_s']:8.2f}-{z['fin_s']:8.2f} s ({z['duree_s']:.2f} s)  écart {z['ecart_db']:5.1f} dB  "
              f"voix {z['niveau_voix_dbfs']:6.1f} dBFS  voisée {z['voise']:.0%}  f0 {z['f0_hz'] or 0:5.0f} Hz  "
              f"pente {z['pente_db_s']:+.1f} dB/s")
    for z in info['ecartees']:
        print(f"  écartée  {z['debut_s']:8.2f}-{z['fin_s']:8.2f} s ({z['duree_s']:.2f} s)  écart {z['ecart_db']:5.1f} dB  "
              f"{z['raison']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
