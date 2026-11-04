"""Rejoue l'ancienne et la nouvelle règle d'octave sur tous les rapport.json disponibles et compare à la vérité.

    python -I rejouer.py [--detail] [--sensibilite] [--kaggle] [--cas FICHIER] [--sorties DOSSIER]
    --kaggle : la nouvelle règle est la fonction intégrée (decider_chant de kaggle/doubler_kaggle.py, extraite sans
    exécuter le reste du noyau), avec les réglages de doubler.py (lus, pas exécutés) et kaggle/ancres_genre.json.

Sources : <sorties>/*/travail/*/rapport.json (--sorties, défaut <données>/sorties ; les dossiers essais-* ne sont que des copies) et octave/donnees/*.json
(F0 calculée en local, sans empreinte). La vérité de chaque cas et les cas synthétiques bâtis à partir des vrais
morceaux (voix transposée, duos recollés, pour éprouver la règle hors des cas connus) sont décrits dans le fichier des
cas : octave/cas.json, local et jamais publié (modèle : octave/cas.exemple.json), ou --cas FICHIER.
"""
import argparse
import ast
import copy
import glob
import json
import math
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
DEPOT = os.path.dirname(ICI)
CAS_DEFAUT = os.path.join(ICI, 'cas.json')   # vérité et cas synthétiques (local, jamais publié) ; modèle : cas.exemple.json


def sorties_defaut():
    """Dossier des sorties de doubler : <dépôt>/sorties s'il existe (ancienne place), sinon <données>/sorties (clé
    « donnees » de ~/.config/voix-la/config.json, sinon ~/Music/Voix-là)."""
    if os.path.isdir(os.path.join(DEPOT, 'sorties')):
        return os.path.join(DEPOT, 'sorties')
    donnees = os.path.expanduser('~/Music/Voix-là')
    try:
        donnees = json.load(open(os.path.expanduser('~/.config/voix-la/config.json'), encoding='utf-8')).get('donnees') or donnees
    except (OSError, ValueError):
        pass
    return os.path.join(donnees, 'sorties')


SORTIES = sorties_defaut()
sys.path.insert(0, ICI)
import decision  # noqa: E402

# --- Ancienne règle : decider_chant du noyau (version du 03/11, recopiée), l'empreinte remplacée par genre_score ----

def ancienne_regle(morceaux, centre, R):
    segs = copy.deepcopy(morceaux)
    sg = R.get('seuil_genre', 0.05)
    mo = decision.meilleure_octave
    dist = decision.distance_oct

    def meilleure(f, c, precedent=None, marge=0.0):
        best = mo(f, c)
        if precedent is not None and dist(f, c, precedent) - dist(f, c, best) < marge:
            return precedent
        return best
    dec_genre = {'homme': meilleure(R.get('centre_homme_hz', 250), centre), 'femme': meilleure(R.get('centre_femme_hz', 420), centre)}
    for s in segs:
        s.pop('voix', None)
        if not s['muet'] and s.get('genre_score') is not None:
            g = s['genre_score']
            s['voix'] = 'homme' if g >= sg else 'femme' if g <= -sg else '?'
    actifs = [s for s in segs if not s['muet'] and s['f0_med']]
    if not actifs:
        return [0] * len(segs)
    globale = decision.mediane_ponderee([s['f0_med'] for s in actifs], [max(s['voise_s'], 0.01) for s in actifs])
    surs = [s for s in actifs if s.get('voix') in ('homme', 'femme') and s['voise_s'] >= R.get('voise_min_s', 0.8)]
    tot = sum(s['voise_s'] for s in surs)
    part_h = sum(s['voise_s'] for s in surs if s['voix'] == 'homme') / tot if tot else None
    if part_h is None:
        seule, defaut = 'inconnue', meilleure(globale, centre)
    elif part_h >= 0.85 or part_h <= 0.15:
        seule = 'homme' if part_h >= 0.85 else 'femme'
        defaut = dec_genre[seule]
    else:
        seule, defaut = None, None
    precedent = defaut
    for s in segs:
        if s['muet']:
            s['decalage'] = precedent if precedent is not None else 0
            continue
        if seule:
            propre = meilleure(s['f0_med'], centre) if s['f0_med'] and s['voise_s'] >= 0.5 else defaut
            s['decalage'] = propre if (propre != defaut and dist(s['f0_med'], centre, defaut) > 1.0) else defaut
        elif s.get('voix') in ('homme', 'femme'):
            s['decalage'] = dec_genre[s['voix']]
        else:
            f0 = s['f0_med'] if s['voise_s'] >= 0.3 else None
            s['decalage'] = precedent if precedent is not None else (meilleure(f0, centre) if f0 else 0)
        precedent = s['decalage']
    if not seule:
        act = [s for s in segs if not s['muet']]
        for a, s, b in zip(act, act[1:], act[2:]):
            if a['decalage'] == b['decalage'] != s['decalage'] and abs(s.get('genre_score') or 0) < 2 * sg:
                s['decalage'] = a['decalage']
    return [s['decalage'] for s in segs]


# --- Nouvelle règle : decision.py, ou la fonction intégrée au noyau (--kaggle) ---------------------------------------

def charger_regle_kaggle():
    """Extrait du noyau les fonctions de décision (sans exécuter le reste du fichier)."""
    src = open(os.path.join(DEPOT, 'kaggle', 'doubler_kaggle.py'), encoding='utf-8').read()
    arbre = ast.parse(src)
    garder = [n for n in arbre.body if (isinstance(n, ast.FunctionDef) and n.name in OCTAVE_FONCTIONS)
              or (isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'OCTAVE_DEFAUT' for t in n.targets))]
    module = ast.Module(body=garder, type_ignores=[])
    espace = {'math': math}
    exec(compile(module, 'doubler_kaggle.py', 'exec'), espace)
    return espace


OCTAVE_FONCTIONS = {'mediane_ponderee', 'quantile_pondere', 'distance_oct', 'meilleure_octave', 'mesurer_voix', 'classer',
                    'deux_moyennes', 'z_suites', 'viterbi', 'sections', 'lisser_sections', 'muet_ou_sourd', 'decider_octave',
                    'decider_chant', 'frise', 'chrono'}


def nouvelle_regle_locale(morceaux, centre, coupes_son):
    res, info = decision.decider_octave(morceaux, centre, coupes_son=coupes_son)
    return [r['decalage'] for r in res], res, info


def fabrique_kaggle():
    """La fonction intégrée elle-même (decider_chant du noyau), avec les ancres et les réglages envoyés par doubler.py."""
    k = charger_regle_kaggle()
    ancres = json.load(open(os.path.join(DEPOT, 'kaggle', 'ancres_genre.json'), encoding='utf-8'))
    src = open(os.path.join(DEPOT, 'doubler.py'), encoding='utf-8').read()
    arbre = ast.parse(src)
    noyau = next(n for n in arbre.body if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'NOYAU_REGLAGES' for t in n.targets))
    reglages = ast.literal_eval(noyau.value)   # réglages tels que doubler.py les envoie (lecture seule)

    def regle(morceaux, centre, coupes_son):
        segs = copy.deepcopy(morceaux)
        R = {**reglages, 'ancres': {'homme': ancres['homme'], 'femme': ancres['femme']}}
        info = k['decider_chant'](segs, R, centre, coupes_son)
        regle.frise = k['frise'](segs)
        return [s['decalage'] for s in segs], segs, info
    return regle


# --- Vérité -------------------------------------------------------------------------------------------------------

def lire_fichier_cas(chemin):
    """Fichier des cas : centre de chant des cas synthétiques, règles de vérité (« references ») et cas synthétiques."""
    try:
        d = json.load(open(chemin, encoding='utf-8'))
    except FileNotFoundError:
        raise SystemExit(f'{chemin} absent : copier octave/cas.exemple.json en octave/cas.json et l\'adapter '
                         '(ou --cas FICHIER)')
    except ValueError as e:
        raise SystemExit(f'{chemin} illisible ({e})')
    return {'centre_chant_hz': d.get('centre_chant_hz'), 'references': d.get('references', []),
            'synthetiques': d.get('synthetiques', [])}


def verite(nom, morceaux, references):
    """(décalage attendu par morceau, douteux par morceau, note) d'après la première règle dont un motif figure dans le
    nom du cas ; None si le cas n'est pas un cas de référence.

    Règle : {motifs, decalage, note, sections (facultatif) : [{debut_s, fin_s, decalage}] (un morceau couvert à plus de
    moitié par une section prend son décalage), douteux_genre_max (facultatif) : morceau douteux si son empreinte
    |genre_score| est sous ce seuil (chant à deux probable : voix dominante incertaine)}."""
    n = len(morceaux)
    for r in references:
        if not any(m in nom for m in r['motifs']):
            continue
        sections = r.get('sections') or []
        if not sections and r.get('douteux_genre_max') is None:
            return [r['decalage']] * n, [False] * n, r['note']
        att, dout = [], []
        for s in morceaux:
            dec = next((x['decalage'] for x in sections
                        if min(x['fin_s'], s['fin_s']) - max(x['debut_s'], s['debut_s']) > 0.5 * (s['fin_s'] - s['debut_s'])),
                       r['decalage'])
            att.append(dec)
            g = s.get('genre_score')
            dout.append(r.get('douteux_genre_max') is not None and g is not None and abs(g) < r['douteux_genre_max'])
        return att, dout, r['note']
    return None


# --- Mesures ------------------------------------------------------------------------------------------------------

def chrono(t):
    return f'{int(t // 60)}:{int(t % 60):02d}'


def frise_resumee(morceaux, decs):
    lignes, cur = [], None
    for s, d in zip(morceaux, decs):
        if s['muet']:
            continue
        if cur and cur[2] == d:
            cur[1] = s['fin_s']
        else:
            cur = [s['debut_s'], s['fin_s'], d]
            lignes.append(cur)
    return ' | '.join(f'{chrono(a)}–{chrono(b)} {d:+d}' for a, b, d in lignes)


def changements(morceaux, decs):
    act = [(s, d) for s, d in zip(morceaux, decs) if not s['muet']]
    return [b[0]['debut_s'] for a, b in zip(act, act[1:]) if a[1] != b[1]]


def evaluer(morceaux, decs, att, dout):
    bon = tot = 0.0
    erreurs = []
    for s, d, a, x in zip(morceaux, decs, att, dout):
        if s['muet'] or x:
            continue
        tot += s['voise_s']
        if d == a:
            bon += s['voise_s']
        else:
            erreurs.append(s['i'] if 'i' in s else None)
    ch, ch_v = changements(morceaux, decs), changements(morceaux, att)
    parasites = [c for c in ch if not any(abs(c - v) < 9 for v in ch_v)]
    return {'juste': bon / tot if tot else 1.0, 'erreurs': erreurs, 'changements': len(ch), 'parasites': len(parasites)}


# --- Cas ----------------------------------------------------------------------------------------------------------

def charger_cas():
    cas = []
    for f in sorted(glob.glob(os.path.join(SORTIES, '*', 'travail', '*', 'rapport.json'))):
        r = json.load(open(f, encoding='utf-8'))
        nom = f.split(os.sep)[-4]
        cas.append({'nom': nom, 'mode': r.get('mode'), 'morceaux': r['morceaux'],
                    'centre': r.get('voix_cible', {}).get('centre_chant_hz') or r['decision_hauteur']['centre_chant_cible_hz'],
                    'coupes_son': r.get('coupes_dans_le_son_s'), 'R': r.get('reglages', {}), 'source': f})
    for f in sorted(glob.glob(os.path.join(ICI, 'donnees', '*.json'))):
        r = json.load(open(f, encoding='utf-8'))
        cas.append({'nom': r['id'], 'mode': 'chant', 'morceaux': r['morceaux'], 'centre': r['voix_cible']['centre_chant_hz'],
                    'coupes_son': None, 'R': {}, 'source': f})
    return cas


def retimer(listes):
    """Recolle des listes de (morceau, vérité) bout à bout (temps continus), en gardant la vérité de chaque morceau."""
    sortie, t = [], 0.0
    for liste in listes:
        t0 = liste[0][0]['debut_s']
        for s, vrai in liste:
            n = dict(s)
            n['debut_s'], n['fin_s'] = round(t + s['debut_s'] - t0, 3), round(t + s['fin_s'] - t0, 3)
            n['_verite'] = vrai
            sortie.append(n)
        t = sortie[-1]['fin_s']
    for i, s in enumerate(sortie):
        s['i'] = i
    return sortie


def partie_synthetique(p, trouve, references):
    """Morceaux d'une partie d'un cas synthétique : [(morceau copié et transformé, vérité)] ; None si sa source manque.

    Partie : {cas (motif du nom du cas source), actifs (non muets seulement), avec_hauteur (f0_med non vide seulement),
    si_verite (morceaux dont la vérité du cas source vaut cette valeur), tranche [début, fin] (après les filtres),
    verite (décalage attendu, ou « reference » : celui du cas source, morceau par morceau), transposer_dt (f0_med
    transposée de tant de demi-tons), decaler_genre {si_verite, de} (genre_score + de, là où la vérité du cas source
    vaut si_verite)}."""
    src = trouve(p['cas'])
    if src is None:
        return None
    v = verite(src['nom'], src['morceaux'], references)
    if v is None and ('si_verite' in p or p.get('verite') == 'reference' or 'decaler_genre' in p):
        return None
    att = v[0] if v else [None] * len(src['morceaux'])
    choisis = [(s, a) for s, a in zip(src['morceaux'], att)
               if not (p.get('actifs') and s['muet']) and not (p.get('avec_hauteur') and not s['f0_med'])
               and ('si_verite' not in p or a == p['si_verite'])]
    debut, fin = p.get('tranche') or [None, None]
    sortie = []
    for s, a in choisis[debut:fin]:
        s = copy.deepcopy(s)
        if p.get('transposer_dt') and s['f0_med']:
            s['f0_med'] = round(s['f0_med'] * 2 ** (p['transposer_dt'] / 12), 1)
        dg = p.get('decaler_genre')
        if dg and a == dg['si_verite'] and s.get('genre_score') is not None:
            s['genre_score'] = round(s['genre_score'] + dg['de'], 3)
        sortie.append((s, a if p['verite'] == 'reference' else p['verite']))
    return sortie


def cas_synthetiques(cas, fichier_cas):
    """Cas synthétiques du fichier des cas, bâtis à partir des vrais (ceux dont toutes les sources sont là).

    Cas : {nom, parties (voir partie_synthetique), recaler (défaut true : parties recollées bout à bout, morceaux
    renumérotés ; false : temps d'origine gardés), duo (tolérance des duos dans --sensibilite), hors_sensibilite
    (cas écarté de --sensibilite), centre_chant_hz (défaut : celui du fichier)}."""
    par = {c['nom']: c for c in cas}
    trouve = lambda m: next((c for n, c in par.items() if m in n), None)
    refs = fichier_cas['references']
    out = []
    for d in fichier_cas['synthetiques']:
        parties = [partie_synthetique(p, trouve, refs) for p in d['parties']]
        if any(p is None for p in parties):
            continue
        parties = [p for p in parties if p]
        if not parties:
            continue
        if d.get('recaler', True):
            m = retimer(parties)
        else:
            m = []
            for p in parties:
                for s, vrai in p:
                    s['_verite'] = vrai
                    m.append(s)
        centre = d.get('centre_chant_hz', fichier_cas['centre_chant_hz'])
        if centre is None:
            raise SystemExit(f"cas synthétique « {d['nom']} » : centre_chant_hz manque dans le fichier des cas")
        out.append({'nom': d['nom'], 'mode': 'chant', 'morceaux': m, 'centre': centre, 'coupes_son': None, 'R': {},
                    'source': 'synthétique', 'synthetique': True, 'duo': bool(d.get('duo')),
                    'hors_sensibilite': bool(d.get('hors_sensibilite'))})
    return out


SENSIBILITE = {
    'zone_basse_hz': [220, 230, 240, 250, 260], 'zone_haute_hz': [300, 315, 330, 345, 360],
    'genre_homme': [0.0, 0.02, 0.03, 0.05, 0.07], 'genre_femme': [-0.06, -0.08, -0.10, -0.12, -0.14],
    'femme_empreinte_min_hz': [240, 255, 265, 270, 275, 281, 285, 290, 300],   # 240 : la règle d'avant (sans plancher)
    'grave_p10_hz': [170, 180, 190, 200, 210], 'duo_ecart_genre': [0.10, 0.125, 0.15, 0.175, 0.20],
    'duo_z_suites': [-1.5, -2.0, -2.5, -3.0], 'duo_part_min': [0.10, 0.15, 0.20],
    'cout_changement': [1.5, 3.0, 4.5, 6.0], 'cout_changement_pause': [0.5, 1.0, 2.0], 'section_min_s': [3, 6, 9, 12],
}


def sensibilite(cas):
    """Chaque réglage varié seul : cas justes à 100 % sans saut parasite (hors cas « hors_sensibilite » du fichier des
    cas, p. ex. un stress ingagnable ; les cas « duo » tolèrent jusqu'à 10 % de faux)."""
    retenus = [c for c in cas if not c.get('hors_sensibilite')]
    print('\n=== Sensibilité (un réglage varié à la fois ; cas sans faute / cas, et les cas en faute)')
    for cle, valeurs in SENSIBILITE.items():
        morceaux_ligne = []
        for v in valeurs:
            fautes = []
            for c in retenus:
                res, _ = decision.decider_octave(c['morceaux'], c['centre'], reglages={cle: v}, coupes_son=c['coupes_son'])
                ev = evaluer(c['morceaux'], [r['decalage'] for r in res], c['_att'], c['_dout'])
                if ev['juste'] < 0.999 and not c.get('duo') or ev['parasites'] or ev['juste'] < 0.9:
                    fautes.append(c['nom'][:18])
            marque = '*' if v == decision.REGLAGES_DEFAUT[cle] else ' '
            morceaux_ligne.append(f"{v:g}{marque}: {len(retenus) - len(fautes)}/{len(retenus)}" + (f" ({', '.join(fautes)})" if fautes else ''))
        print(f"  {cle:22s} " + ' ; '.join(morceaux_ligne))


def main():
    global SORTIES
    p = argparse.ArgumentParser()
    p.add_argument('--detail', action='store_true')
    p.add_argument('--sensibilite', action='store_true')
    p.add_argument('--kaggle', action='store_true', help='nouvelle règle prise dans kaggle/doubler_kaggle.py')
    p.add_argument('--sorties', default=None, help=f'dossier des sorties de doubler (défaut : {SORTIES})')
    p.add_argument('--cas', default=CAS_DEFAUT, help='fichier des cas : vérité et cas synthétiques (défaut : octave/cas.json ; '
                   'modèle : octave/cas.exemple.json)')
    a = p.parse_args()
    SORTIES = a.sorties or SORTIES
    fichier_cas = lire_fichier_cas(a.cas)
    nouvelle = fabrique_kaggle() if a.kaggle else nouvelle_regle_locale
    cas = charger_cas()
    cas += cas_synthetiques(cas, fichier_cas)
    bilan = []
    for c in cas:
        m = c['morceaux']
        if c.get('synthetique'):
            att = [s.get('_verite', 0) for s in m]
            dout, note = [False] * len(m), 'synthétique'
            c['_att'], c['_dout'] = att, dout
        else:
            v = verite(c['nom'], m, fichier_cas['references'])
            if v is None:
                continue
            att, dout, note = v
        c['_att'], c['_dout'] = att, dout
        vieux = ancienne_regle(m, c['centre'], c['R'])
        neuf, res, info = nouvelle(m, c['centre'], c['coupes_son'])
        ev, en = evaluer(m, vieux, att, dout), evaluer(m, neuf, att, dout)
        bilan.append((c['nom'], ev, en))
        print(f"\n=== {c['nom']}  [mode {c['mode']}]  — vérité : {note}")
        print(f"  ancienne : {ev['juste']:6.1%} juste, {ev['changements']} changement(s), {ev['parasites']} parasite(s)")
        print(f"     {frise_resumee(m, vieux)}")
        print(f"  nouvelle : {en['juste']:6.1%} juste, {en['changements']} changement(s), {en['parasites']} parasite(s)")
        print(f"     {frise_resumee(m, neuf)}")
        vp = info['voix_principale']
        print(f"     voix principale : {vp.get('classe')} ({vp['raison']}) → {vp['decalage']:+d} ; "
              f"{'duo' if info['voix'] == 2 else 'une voix : ' + str(info.get('pourquoi_une_voix'))}")
        if 'essai_duo' in info:
            e = info['essai_duo']
            print(f"     essai duo : écart {e['ecart_genre']}, z {e['z_suites']}, parts {e['parts']}, "
                  f"médianes f0 {[x.get('f0_med_hz') for x in e['voix']]}, genre {[x.get('genre_med') for x in e['voix']]}")
        if info['voix'] == 2:
            for d in info['voix_detail']:
                print(f"     voix {d['nom']} : {d['raison']} → {d['decalage']:+d}")
            print(f"     changements : {', '.join(chrono(x['a_s']) + (' (pause)' if x['pause'] else ' (en plein son)') for x in info['changements'])}")
        if a.kaggle and getattr(nouvelle, 'frise', None):
            print('     frise du noyau :')
            for l in nouvelle.frise:
                print('       ' + l)
        if a.detail:
            for s, x, y, z, d in zip(m, vieux, neuf, att, dout):
                if not s['muet']:
                    print(f"       {s['debut_s']:7.2f}–{s['fin_s']:7.2f} f0 {s['f0_med'] or 0:5.0f} g {s.get('genre_score') if s.get('genre_score') is not None else '   —':>6} "
                          f"vrai {z:+3d}{'?' if d else ' '} ancien {x:+3d} nouveau {y:+3d}  {'' if y == z or d else '<- ERREUR'}")
    if a.sensibilite:
        sensibilite([c for c in cas if '_att' in c])
    print('\n=== Bilan (part du temps voisé juste, hors morceaux douteux ; changements d’octave ; dont parasites)')
    for nom, ev, en in bilan:
        print(f"  {nom[:62]:62s} ancienne {ev['juste']:6.1%} {ev['changements']:2d} ch. {ev['parasites']:2d} par. | "
              f"nouvelle {en['juste']:6.1%} {en['changements']:2d} ch. {en['parasites']:2d} par.")


if __name__ == '__main__':
    main()
