"""Choix d'octave en chant (-12, 0, +12) : nouvelle règle, fonction pure (Python seul, sans numpy).

Principe : un même chanteur garde un seul décalage sur toute la chanson. On ne change d'octave que lorsque la voix
change vraiment (duo), et de préférence à une pause.

1. Classe d'une voix (homme / femme), d'après des indices sûrs, dans cet ordre :
   - la hauteur médiane F (médiane pondérée par le temps voisé des hauteurs des morceaux) :
     F < 240 Hz -> homme ; F > 330 Hz -> femme ;
   - entre les deux (zone ambiguë, où chantent aussi bien des hommes que des femmes), l'empreinte CAMPPlus
     (genre_score, projection sur l'axe homme - femme des ancres) : >= +0,03 -> homme ; <= -0,10 -> femme, mais
     seulement dans la moitié haute de la zone (F >= 281 Hz, moyenne géométrique de 240 et 330) : plus bas, une voix
     d'homme éraillée a aussi une empreinte de femme, parfois plus marquée que celle d'une voix de femme de la zone ;
   - sinon, les notes graves : 10e centile des hauteurs des morceaux < 190 Hz -> homme (une chanteuse descend
     rarement si bas sur une phrase entière) ;
   - sinon, faute d'indice sûr, la hauteur seule : le décalage qui rapproche le plus F du centre de chant de la voix
     cible (p. ex. centre de 200 Hz : frontière 0 / -12 à 283 Hz, √2 fois le centre).
   Décalage d'une classe : celui qui rapproche le centre de chant typique de la classe (homme 250 Hz, femme 420 Hz)
   du centre de chant de la voix cible (voix cible d'homme : homme 0, femme -12 ; voix de femme : homme +12, femme 0).
2. Une voix ou deux ? On regroupe les morceaux en deux par leur empreinte (k-moyennes à une dimension sur genre_score,
   chaque groupe pesant au moins 15 % du temps voisé).
   On ne retient un duo que si les deux groupes sont nettement séparés (écart des médianes >= 0,15), chantent chacun
   au moins 15 % du temps voisé (et 12 s), en sections suivies (test des suites : z <= -2), et si chaque groupe a
   assez d'indices sûrs pour mériter un décalage différent (un groupe sans indice sûr suit la voix principale).
3. Voix seule (ou deux voix au même décalage) : un seul décalage pour toute la chanson.
4. Duo : chaque morceau est attribué à une voix par un Viterbi à deux états (vraisemblance de l'empreinte, pondérée
   par le temps voisé ; changement de voix coûteux en plein son, moins cher à une pause) ; une section de moins de
   6 s rejoint ses voisines. Un chant à deux suit la voix dominante (l'empreinte la plus proche, sinon les voisins).
"""
import math

REGLAGES_DEFAUT = {
    'zone_basse_hz': 240.0,          # en dessous : voix d'homme d'après la hauteur seule
    'zone_haute_hz': 330.0,          # au-dessus : voix de femme d'après la hauteur seule
    'genre_homme': 0.03,             # genre_score médian d'une voix, à partir duquel on la tient pour un homme
    'genre_femme': -0.10,            # ... en deçà duquel on la tient pour une femme
    'femme_empreinte_min_hz': 281.0,  # ... et seulement à partir de cette hauteur (√(240 x 330)) : en dessous, une
                                      # empreinte de femme peut venir d'une voix d'homme éraillée
    'grave_p10_hz': 190.0,           # 10e centile des hauteurs de morceaux sous lequel la voix est d'homme
    'centre_homme_hz': 250.0,        # centre de chant typique d'un chanteur, d'une chanteuse
    'centre_femme_hz': 420.0,
    'voise_min_s': 0.8,              # temps voisé minimal d'un morceau pour compter dans les empreintes
    'voise_hauteur_s': 0.3,          # ... pour compter dans la hauteur
    'duo_ecart_genre': 0.15,         # écart minimal des médianes d'empreinte des deux groupes
    'duo_part_min': 0.15,            # part minimale du temps voisé de chaque voix
    'duo_voise_min_s': 12.0,         # temps voisé minimal de chaque voix
    'duo_z_suites': -2.0,            # les morceaux de chaque voix forment des sections suivies (test des suites)
    'duo_morceaux_min': 6,
    'sigma_min': 0.04,               # écart-type plancher de l'empreinte d'une voix (Viterbi)
    'cout_changement': 3.0,          # coût d'un changement de voix en plein son (log-vraisemblance)
    'cout_changement_pause': 1.0,    # ... à une pause (coupe dans un silence, ou morceau muet entre les deux)
    'section_min_s': 6.0,            # une section de voix plus courte rejoint ses voisines
}


def mediane_ponderee(v, w):
    return quantile_pondere(v, w, 0.5)


def quantile_pondere(v, w, q):
    paires = sorted(zip(v, w))
    tot = sum(b for _, b in paires)
    c = 0.0
    for a, b in paires:
        c += b
        if c >= q * tot:
            return a
    return paires[-1][0]


def distance_oct(f, centre, d):
    return abs(math.log2(f / centre) + d / 12)


def meilleure_octave(f, centre):
    """Le décalage (-12, 0 ou +12) qui rapproche le plus la hauteur f du centre de chant de la voix cible."""
    return min((-12, 0, 12), key=lambda d: distance_oct(f, centre, d))


def mesurer_voix(morceaux, R):
    """Hauteur médiane, 10e centile, empreinte médiane et temps voisé d'un ensemble de morceaux."""
    h = [s for s in morceaux if s.get('f0_med') and s.get('voise_s', 0) >= R['voise_hauteur_s']]
    g = [s for s in morceaux if s.get('genre_score') is not None and s.get('voise_s', 0) >= R['voise_min_s']]
    m = {'voise_s': round(sum(s['voise_s'] for s in h), 2), 'morceaux': len(h)}
    if h:
        f, w = [s['f0_med'] for s in h], [s['voise_s'] for s in h]
        m.update({'f0_med_hz': round(mediane_ponderee(f, w), 1), 'f0_p10_hz': round(quantile_pondere(f, w, 0.1), 1),
                  'f0_p90_hz': round(quantile_pondere(f, w, 0.9), 1)})
    if g:
        m['genre_med'] = round(mediane_ponderee([s['genre_score'] for s in g], [s['voise_s'] for s in g]), 3)
    return m


def classer(m, R, sur_seulement=False):
    """(classe, raison) d'une voix mesurée ; classe None si sur_seulement et aucun indice sûr."""
    f, g, p10 = m.get('f0_med_hz'), m.get('genre_med'), m.get('f0_p10_hz')
    if not f:
        return None, 'pas de hauteur'
    if f < R['zone_basse_hz']:
        return 'homme', f"hauteur {f:.0f} Hz < {R['zone_basse_hz']:.0f}"
    if f > R['zone_haute_hz']:
        return 'femme', f"hauteur {f:.0f} Hz > {R['zone_haute_hz']:.0f}"
    zone = f"hauteur {f:.0f} Hz dans la zone ambiguë"
    if g is not None and g >= R['genre_homme']:
        return 'homme', f"{zone}, empreinte {g:+.3f} (homme)"
    if g is not None and g <= R['genre_femme'] and f >= R['femme_empreinte_min_hz']:
        return 'femme', f"{zone}, empreinte {g:+.3f} (femme)"
    if p10 and p10 < R['grave_p10_hz']:
        return 'homme', f"{zone}, notes graves (10e centile {p10:.0f} Hz)"
    if g is not None and g <= R['genre_femme']:
        empreinte = (f"empreinte {g:+.3f} (femme) peu sûre sous {R['femme_empreinte_min_hz']:.0f} Hz "
                     "(voix d'homme éraillée possible)")
    else:
        empreinte = f"empreinte {'absente' if g is None else f'{g:+.3f}'} peu marquée"
    if sur_seulement:
        return None, f"{zone}, {empreinte}"
    return None, f"{zone}, {empreinte} : d'après la hauteur seule"


def deux_moyennes(vals, poids, part_min=0.0):
    """k-moyennes exactes à une dimension (k = 2), pondérées : rend le seuil et le coût. Chaque groupe garde au moins
    part_min du poids total (sinon quelques morceaux très marqués feraient à eux seuls un groupe), si c'est possible."""
    o = sorted(zip(vals, poids))
    v, w = [a for a, _ in o], [b for _, b in o]
    tot = sum(w)
    best = None
    coupes = [k for k in range(1, len(v)) if v[k - 1] < v[k]]   # jamais entre deux valeurs égales
    coupes = [k for k in coupes if min(sum(w[:k]), sum(w[k:])) >= part_min * tot] or coupes
    if not coupes:
        return None, None
    for k in coupes:
        sa, sb = sum(w[:k]), sum(w[k:])
        ma = sum(a * b for a, b in zip(v[:k], w[:k])) / sa
        mb = sum(a * b for a, b in zip(v[k:], w[k:])) / sb
        cout = sum(b * (a - ma) ** 2 for a, b in zip(v[:k], w[:k])) + sum(b * (a - mb) ** 2 for a, b in zip(v[k:], w[k:]))
        if best is None or cout < best[1]:
            best = ((v[k - 1] + v[k]) / 2, cout)
    return best


def z_suites(lab):
    """Test des suites de Wald-Wolfowitz : z très négatif = étiquettes groupées en sections suivies."""
    n1, n2 = lab.count(0), lab.count(1)
    n = n1 + n2
    if n1 == 0 or n2 == 0 or n < 3:
        return 0.0
    suites = 1 + sum(a != b for a, b in zip(lab, lab[1:]))
    mu = 1 + 2 * n1 * n2 / n
    var = 2 * n1 * n2 * (2 * n1 * n2 - n) / (n * n * (n - 1))
    return (suites - mu) / math.sqrt(var) if var > 0 else 0.0


def viterbi(actifs, mu, sigma, pause, R):
    """Voix (0 / 1) de chaque morceau actif. pause[j] : la frontière entre actifs[j-1] et actifs[j] est un silence."""
    def ll(s, k):
        g = s.get('genre_score')
        if g is None or s.get('voise_s', 0) < R['voise_hauteur_s']:
            return 0.0
        w = min(s['voise_s'] / 3.0, 1.0)
        return -w * (g - mu[k]) ** 2 / (2 * sigma[k] ** 2) - w * math.log(sigma[k])
    n = len(actifs)
    score = [[ll(actifs[0], 0), ll(actifs[0], 1)]]
    retour = [[0, 1]]
    for j in range(1, n):
        c = R['cout_changement_pause'] if pause[j] else R['cout_changement']
        ligne, arr = [], []
        for k in (0, 1):
            rester, changer = score[-1][k], score[-1][1 - k] - c
            arr.append(k if rester >= changer else 1 - k)
            ligne.append(max(rester, changer) + ll(actifs[j], k))
        score.append(ligne)
        retour.append(arr)
    k = 0 if score[-1][0] >= score[-1][1] else 1
    lab = [k]
    for j in range(n - 1, 0, -1):
        k = retour[j][k]
        lab.append(k)
    return lab[::-1]


def sections(actifs, lab):
    """Suites de morceaux actifs consécutifs de même voix : [(début, fin_exclue, voix, durée_s)]."""
    res, d = [], 0
    for j in range(1, len(lab) + 1):
        if j == len(lab) or lab[j] != lab[d]:
            res.append((d, j, lab[d], actifs[j - 1]['fin_s'] - actifs[d]['debut_s']))
            d = j
    return res


def lisser_sections(actifs, lab, R):
    """Une section plus courte que section_min_s rejoint ses voisines (la plus courte d'abord)."""
    lab = list(lab)
    while True:
        sec = sections(actifs, lab)
        if len(sec) <= 1:
            return lab
        courtes = [t for t in sec if t[3] < R['section_min_s']]
        if not courtes:
            return lab
        d, f, k, _ = min(courtes, key=lambda t: t[3])
        for j in range(d, f):
            lab[j] = 1 - k


def muet_ou_sourd(s):
    return 'muet' if s.get('muet') else 'sans hauteur, comme ses voisins'


def decider_octave(morceaux, centre, reglages=None, coupes_son=None):
    """Décalage par morceau (chant).

    morceaux : liste de dicts {debut_s, fin_s, muet, f0_med, voise_s, genre_score (None si absent)}, dans l'ordre.
    centre : centre de chant de la voix cible (Hz). coupes_son : instants (s) des coupes tombées en plein son ; les
    autres coupes sont des pauses (None : toutes en plein son, sauf autour d'un morceau muet).
    Rend (resultats, info) : resultats[i] = {'decalage', 'voix', 'base'} pour chaque morceau ; info = la décision.
    """
    R = {**REGLAGES_DEFAUT, **(reglages or {})}
    dec_classe = {'homme': meilleure_octave(R['centre_homme_hz'], centre),
                  'femme': meilleure_octave(R['centre_femme_hz'], centre)}
    info = {'regle': 'v2.1 (une voix = un décalage ; empreinte de femme ignorée sous 281 Hz)', 'centre_chant_cible_hz': round(centre, 1),
            'decalage_par_classe': dec_classe,
            'zone_ambigue_hz': [R['zone_basse_hz'], R['zone_haute_hz']],
            'seuils_genre': {'homme': R['genre_homme'], 'femme': R['genre_femme']}}
    actifs_i = [i for i, s in enumerate(morceaux) if not s.get('muet') and s.get('voise_s', 0) > 0]
    if not actifs_i:
        return ([{'decalage': 0, 'voix': None, 'base': muet_ou_sourd(s)} for s in morceaux],
                {**info, 'voix': 0, 'voix_principale': {'classe': None, 'raison': 'aucun morceau chanté', 'decalage': 0}})

    # 1. La chanson entière comme une seule voix
    tout = [morceaux[i] for i in actifs_i]
    mg = mesurer_voix(tout, R)
    classe_g, raison_g = classer(mg, R)
    dec_g = dec_classe[classe_g] if classe_g else (meilleure_octave(mg['f0_med_hz'], centre) if mg.get('f0_med_hz') else 0)
    info['voix_principale'] = {**mg, 'classe': classe_g, 'raison': raison_g, 'decalage': dec_g}

    # 2. Deux voix ?
    duo, pourquoi, groupes = None, None, None
    elig = [i for i in actifs_i if morceaux[i].get('genre_score') is not None and morceaux[i]['voise_s'] >= R['voise_min_s']]
    if len(elig) < R['duo_morceaux_min']:
        pourquoi = f"{len(elig)} morceaux avec empreinte : trop peu pour chercher un duo"
    else:
        seuil, _ = deux_moyennes([morceaux[i]['genre_score'] for i in elig], [morceaux[i]['voise_s'] for i in elig],
                                 R['duo_part_min'])
        if seuil is None:
            pourquoi = 'empreintes toutes égales'
        else:
            groupes = [[morceaux[i] for i in elig if (morceaux[i]['genre_score'] > seuil) == bool(k)] for k in (0, 1)]
    if groupes:
        mes = [mesurer_voix(gr, R) for gr in groupes]
        tot = sum(m['voise_s'] for m in mes)
        ecart = mes[1]['genre_med'] - mes[0]['genre_med']
        z = z_suites([int(morceaux[i]['genre_score'] > seuil) for i in elig])
        essai = {'seuil_genre': round(seuil, 3), 'ecart_genre': round(ecart, 3), 'z_suites': round(z, 2),
                 'parts': [round(m['voise_s'] / tot, 3) for m in mes], 'voix': mes}
        info['essai_duo'] = essai
        if ecart < R['duo_ecart_genre']:
            pourquoi = f"empreintes trop proches (écart {ecart:.3f} < {R['duo_ecart_genre']})"
        elif min(m['voise_s'] for m in mes) < max(R['duo_part_min'] * tot, R['duo_voise_min_s']):
            pourquoi = (f"une des deux voix chante trop peu ({min(m['voise_s'] for m in mes):.0f} s voisées, "
                        f"{min(essai['parts']):.0%} ; il en faut {R['duo_part_min']:.0%} et {R['duo_voise_min_s']:.0f} s)")
        elif z > R['duo_z_suites']:
            pourquoi = f"empreintes mêlées dans le temps, pas en sections (z des suites {z:.1f} > {R['duo_z_suites']})"
        else:
            cl = [classer(m, R, sur_seulement=True) for m in mes]
            decs = []
            for k, (m, (c, r)) in enumerate(zip(mes, cl)):
                if c:
                    d = dec_classe[c]
                else:
                    # Sans indice sûr : la hauteur seule, si elle va dans le sens des empreintes (le groupe à l'empreinte
                    # la plus basse est le plus « femme » des deux) ; sinon, comme la voix principale.
                    attendue = 'femme' if k == 0 else 'homme'
                    d = meilleure_octave(m['f0_med_hz'], centre) if m.get('f0_med_hz') else dec_g
                    if d == dec_classe[attendue]:
                        c, r = attendue, r + f", d'après la hauteur seule (empreinte plus {'basse' if k == 0 else 'haute'} que l'autre voix)"
                    else:
                        c, d, r = classe_g, dec_g, r + ' : comme la voix principale'
                m.update({'classe': c, 'raison': r, 'decalage': d})
                decs.append(d)
            if decs[0] == decs[1]:
                pourquoi = f"deux voix probables, même décalage ({decs[0]:+d})"
            else:
                duo = {'seuil': seuil, 'mes': mes, 'decs': decs}

    resultats = [None] * len(morceaux)
    if duo is None:
        info.update({'voix': 1, 'pourquoi_une_voix': pourquoi})
        nom = f"voix seule ({classe_g or 'd’après la hauteur'})"
        for i, s in enumerate(morceaux):
            resultats[i] = {'decalage': dec_g, 'voix': classe_g, 'base': nom if i in actifs_i else muet_ou_sourd(s)}
        return resultats, info

    # 3. Duo : voix de chaque morceau (Viterbi), sections minimales
    mes, decs = duo['mes'], duo['decs']
    mu = [m['genre_med'] for m in mes]
    groupes = [[morceaux[i]['genre_score'] for i in elig if (morceaux[i]['genre_score'] > duo['seuil']) == bool(k)] for k in (0, 1)]
    sigma = []
    for k, gr in enumerate(groupes):
        e = math.sqrt(sum((g - mu[k]) ** 2 for g in gr) / len(gr)) if gr else 0.0
        sigma.append(max(e, R['sigma_min']))
    pause = [False]
    for a, b in zip(actifs_i, actifs_i[1:]):
        en_son = coupes_son is None or any(abs(morceaux[b]['debut_s'] - c) < 0.006 for c in coupes_son)
        pause.append(b - a > 1 or not en_son)
    act = [morceaux[i] for i in actifs_i]
    lab = lisser_sections(act, viterbi(act, mu, sigma, pause, R), R)
    voise = [sum(s['voise_s'] for s, k in zip(act, lab) if k == j) for j in (0, 1)]
    if min(voise) < max(R['duo_part_min'] * sum(voise), R['duo_voise_min_s']):
        info.update({'voix': 1, 'pourquoi_une_voix': f"après attribution, une voix chante trop peu ({min(voise):.0f} s voisées)"})
        nom = f"voix seule ({classe_g or 'd’après la hauteur'})"
        for i, s in enumerate(morceaux):
            resultats[i] = {'decalage': dec_g, 'voix': classe_g, 'base': nom if i in actifs_i else muet_ou_sourd(s)}
        return resultats, info
    cls = [mes[k]['classe'] or 'voix' for k in (0, 1)]
    noms = cls if cls[0] != cls[1] else [f'{cls[k]} {k + 1}' for k in (0, 1)]
    for j, i in enumerate(actifs_i):
        k = lab[j]
        g = morceaux[i].get('genre_score')
        nette = g is not None and abs(g - mu[k]) <= abs(g - mu[1 - k])
        resultats[i] = {'decalage': decs[k], 'voix': noms[k],
                        'base': f"duo, voix {noms[k]}" + ('' if nette else ' (d’après les voisins)')}
    # muets : comme le morceau actif précédent (ou suivant au début)
    dernier = None
    for i in range(len(morceaux)):
        if resultats[i] is None:
            if dernier is None:
                dernier = resultats[actifs_i[0]]
            resultats[i] = {'decalage': dernier['decalage'], 'voix': None, 'base': muet_ou_sourd(morceaux[i])}
        else:
            dernier = resultats[i]
    secs = sections(act, lab)
    info.update({'voix': 2, 'sigma_genre': [round(x, 3) for x in sigma],
                 'voix_detail': [{**mes[k], 'nom': noms[k], 'voise_attribue_s': round(voise[k], 1)} for k in (0, 1)],
                 'changements': [{'a_s': act[d]['debut_s'], 'vers': noms[k], 'pause': pause[d]} for d, _, k, _ in secs[1:]]})
    return resultats, info
