"""Justesse d'une prise de karaoké (sans GTK, testable) : la mélodie chantée comparée à celle du chanteur d'origine,
ligne par ligne et au total, sur 100.

Données : deux hauteurs (F0 en Hz, 0 = non voisé) toutes les 20 ms, celle de l'original sur le temps du son doublé
(courbes.json « source_hz », sinon YIN sur travail/voix_source.wav), celle de la prise sur son propre temps (YIN de
voixla.analyse). Le calage (decalage_s, voir voixla.karaoke) dit à quel instant du beat lu tombe le début de la prise ;
une boucle rejoue le même passage plusieurs fois, chaque tour est noté à part.

Le calcul, trame par trame puis ligne par ligne :
- écart en cents entre la prise et l'original, là où les deux chantent, **ramené dans l'octave** (−600 à +600 cents) :
  chanter une ou deux octaves plus bas ou plus haut n'est pas faux (un homme sur une chanson de femme) ;
- l'écart est lissé par une médiane sur 5 trames (100 ms) : un raté du suivi de hauteur ou une attaque ne compte pas ;
- crédit d'une trame : 1 jusqu'à JUSTE_CENTS (35 cents, un tiers de demi-ton), 0 dès FAUX_CENTS (100 cents, un
  demi-ton), linéaire entre les deux ;
- chaque ligne cherche le meilleur calage dans ±RETARD_MAX_S (160 ms), pour ne pas compter faux un chanteur un peu en
  avance ou en retard (le retard moyen est rapporté à part) ; à note égale, l'écart brut et le chant simultané
  départagent, puis le calage nul ;
- note d'une ligne = 100 × crédit moyen × présence, où la présence (part des trames chantées par l'original que la
  prise chante aussi) ne pénalise qu'en dessous de PRESENCE_PLEINE (60 %) : souffles, consonnes et chœurs de
  l'original ne coûtent rien ;
- note totale = moyenne des lignes, pondérée par le temps chanté de l'original. Une ligne où l'original chante moins de
  0,2 s n'est pas notée ; une ligne hors de la prise (arrêtée plus tôt) non plus.
Sans paroles, les « lignes » sont les phrases de la mélodie d'origine (silences de plus d'une demi-seconde).
"""

import warnings
from dataclasses import dataclass

import numpy as np

PAS_S = 0.02
JUSTE_CENTS = 35.0
FAUX_CENTS = 100.0
LISSAGE = 5                  # trames de la médiane glissante de l'écart
RETARD_MAX_S = 0.16
PENALITE_RETARD = 0.15       # points retirés par trame de calage, pour départager
POIDS_DEPARTAGE = 0.3        # départage du calage : écart brut (non lissé) et chant simultané
PRESENCE_PLEINE = 0.6
TRAMES_MIN = 10              # 0,2 s chantées par l'original au moins pour noter une ligne
COUVERTURE_MIN = 0.5         # part de la ligne que la prise doit couvrir (prise arrêtée plus tôt)
SEUIL_LIGNE_FAUSSE = 60
FAUX_PASSAGE_CENTS = 70.0    # passage faux : écart lissé au-delà, pendant FAUX_PASSAGE_S au moins
FAUX_PASSAGE_S = 0.25
SILENCE_PHRASE_S = 0.5       # sans paroles : une phrase s'arrête à un silence plus long
PHRASE_MIN_S = 1.0


@dataclass
class Passage:
    """Ce qu'on chante, en temps du son doublé : de debut à fin, repetitions fois de suite (boucle)."""
    debut: float
    fin: float
    repetitions: int = 1

    @property
    def duree(self):
        return max(0.0, self.fin - self.debut)

    @property
    def total(self):
        return self.duree * max(1, self.repetitions)


# --- Écarts -----------------------------------------------------------------------------------------------------------

def cents(f, ref):
    """Écart de f à ref en cents (100 = un demi-ton) ; f et ref > 0."""
    return 1200.0 * np.log2(np.asarray(f, np.float64) / np.asarray(ref, np.float64))


def plier(c):
    """Écart ramené dans l'octave : −600 ≤ c < 600 (une octave d'écart vaut 0)."""
    return np.mod(np.asarray(c, np.float64) + 600.0, 1200.0) - 600.0


def credit(ecart_abs):
    """Crédit d'une trame d'après son écart absolu (cents) : 1 jusqu'à JUSTE_CENTS, 0 dès FAUX_CENTS."""
    return np.clip((FAUX_CENTS - np.asarray(ecart_abs, np.float64)) / (FAUX_CENTS - JUSTE_CENTS), 0.0, 1.0)


def mediane_glissante(x, k=LISSAGE):
    """Médiane sur k trames centrées, en ignorant les NaN (trames où l'un des deux ne chante pas) ; NaN reste NaN."""
    x = np.asarray(x, np.float64)
    if len(x) == 0 or k <= 1:
        return x.copy()
    p = np.pad(x, (k // 2, k // 2), constant_values=np.nan)
    fen = np.lib.stride_tricks.sliding_window_view(p, k)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        m = np.nanmedian(fen, axis=1)
    return np.where(np.isnan(x), np.nan, m)


# --- Projection de la prise sur le temps de la chanson ----------------------------------------------------------------

def projeter(prise, decalage_s, passage, tour, marge, pas=PAS_S):
    """La hauteur de la prise sur la grille du passage (trame j : debut + j × pas), pour un tour de boucle, avec marge
    trames de part et d'autre (pour chercher le calage). Rend (hauteurs, couvert) de longueur n + 2 × marge : couvert
    dit si la prise a été enregistrée à cet instant.

    Temps du beat lu u = temps de la prise + decalage_s ; au tour k, u = k × durée du passage + (temps de la
    chanson − debut)."""
    prise = np.asarray(prise, np.float64)
    n = int(round(passage.duree / pas))
    j = np.arange(-marge, n + marge)
    u = tour * passage.duree + j * pas
    i = np.round((u - decalage_s) / pas).astype(np.int64)
    couvert = (i >= 0) & (i < len(prise))
    h = np.where(couvert, prise[np.clip(i, 0, max(0, len(prise) - 1))] if len(prise) else 0.0, 0.0)
    return h, couvert


def reference_du_passage(reference, passage, pas=PAS_S, pas_ref=PAS_S):
    """La hauteur de l'original sur la grille du passage (plus proche trame de la courbe d'origine)."""
    reference = np.asarray(reference, np.float64)
    n = int(round(passage.duree / pas))
    t = passage.debut + np.arange(n) * pas
    i = np.round(t / pas_ref).astype(np.int64)
    ok = (i >= 0) & (i < len(reference))
    return np.where(ok, reference[np.clip(i, 0, max(0, len(reference) - 1))] if len(reference) else 0.0, 0.0)


# --- Lignes -----------------------------------------------------------------------------------------------------------

def phrases(reference, pas=PAS_S, silence_s=SILENCE_PHRASE_S, min_s=PHRASE_MIN_S):
    """Sans paroles : les phrases de la mélodie d'origine, [(debut, fin, texte)] en temps du son."""
    v = np.asarray(reference) > 0
    sortie, i, n = [], 0, len(v)
    trou = int(round(silence_s / pas))
    while i < n:
        if not v[i]:
            i += 1
            continue
        j, dernier = i, i
        while j < n:
            if v[j]:
                dernier = j
            elif j - dernier > trou:
                break
            j += 1
        if (dernier + 1 - i) * pas >= min_s:
            sortie.append((round(i * pas, 3), round((dernier + 1) * pas, 3)))
        i = dernier + 1
    return [(d, f, f"Phrase {k + 1}") for k, (d, f) in enumerate(sortie)]


def lignes_du_passage(lignes, passage):
    """Les lignes [(debut, fin, texte)] (temps du son) qui tombent dans le passage pour plus de la moitié, avec leurs
    trames [a, b) sur la grille du passage."""
    sortie = []
    for k, (d, f, texte) in enumerate(lignes):
        if f <= d:
            continue
        dans = min(f, passage.fin) - max(d, passage.debut)
        if dans < 0.5 * (f - d):
            continue
        a = max(0, int(round((d - passage.debut) / PAS_S)))
        b = min(int(round(passage.duree / PAS_S)), int(round((f - passage.debut) / PAS_S)))
        if b > a:
            sortie.append({"i": k, "debut": round(d, 3), "fin": round(f, 3), "texte": texte, "a": a, "b": b})
    return sortie


def _mesures(r, t):
    """(crédit moyen, présence, écarts pliés lissés (NaN hors du chant commun), écarts non pliés, crédit moyen sans
    lissage) de la prise t face à l'original r, trame à trame."""
    vr, vt = r > 0, t > 0
    commun = vr & vt
    brut = np.full(len(r), np.nan)
    if commun.any():
        brut[commun] = cents(t[commun], r[commun])
    plie_brut = np.where(commun, plier(np.nan_to_num(brut)), np.nan)
    plie = mediane_glissante(plie_brut)
    n_ref = int(vr.sum())
    presence = commun.sum() / n_ref if n_ref else 0.0
    ok = ~np.isnan(plie)
    moyen = float(credit(np.abs(plie[ok])).mean()) if ok.sum() >= 3 else 0.0
    sans_lissage = float(credit(np.abs(plie_brut[commun])).mean()) if commun.sum() >= 3 else 0.0
    return moyen, float(presence), plie, brut, sans_lissage


def noter_ligne(r, t_ext, a, b, marge, pas=PAS_S):
    """Note d'une ligne [a, b) : r, l'original sur la grille du passage ; t_ext, la prise projetée avec marge trames de
    chaque côté. Cherche le calage (en trames) qui donne la meilleure note dans ±marge ; à note égale (le lissage
    efface un petit décalage aux changements de note), celui où l'écart brut est le plus petit et où les deux chantent
    le plus souvent ensemble, puis le calage nul."""
    ref = r[a:b]
    meilleur = None
    for lag in range(-marge, marge + 1):
        t = t_ext[a + marge + lag:b + marge + lag]
        moyen, presence, plie, brut, sans_lissage = _mesures(ref, t)
        note = 100.0 * moyen * min(1.0, presence / PRESENCE_PLEINE)
        cle = (note + POIDS_DEPARTAGE * 100.0 * (sans_lissage + presence) - PENALITE_RETARD * abs(lag))
        if meilleur is None or cle > meilleur[0] + 1e-9:
            meilleur = (cle, note, moyen, presence, lag, plie, brut)
    _c, note, moyen, presence, lag, plie, brut = meilleur
    ok = ~np.isnan(plie)
    ecart = float(np.median(plie[ok])) if ok.any() else 0.0
    octave = int(np.round(np.median(brut[~np.isnan(brut)]) / 1200.0)) if (~np.isnan(brut)).any() else 0
    return {"score": round(note, 1), "justesse": round(moyen, 3), "presence": round(presence, 3),
            "retard_s": round(lag * pas, 3), "ecart_cents": round(ecart, 1), "octave": octave,
            "n_ref": int((ref > 0).sum()), "_lag": lag, "_plie": plie}


def conseil(ligne, retard_max_s=RETARD_MAX_S):
    """Ce qui cloche dans une ligne, en une courte phrase (vide si elle est juste)."""
    if ligne["presence"] < 0.3:
        return "pas assez chantée"
    morceaux = []
    e = ligne["ecart_cents"]
    if e <= -40:
        morceaux.append(f"trop bas ({_demi_tons(e)})")
    elif e >= 40:
        morceaux.append(f"trop haut ({_demi_tons(e)})")
    elif ligne["score"] < SEUIL_LIGNE_FAUSSE:
        morceaux.append("notes instables")
    # le calage n'est en cause que si la hauteur, elle, est bonne
    if abs(e) < 40 and abs(ligne["retard_s"]) >= retard_max_s - 1e-6:
        morceaux.append("en retard" if ligne["retard_s"] > 0 else "en avance")
    return ", ".join(morceaux)


def _demi_tons(c):
    v = c / 100.0
    texte = f"{abs(v):.1f}".replace(".", ",")
    return f"{'−' if v < 0 else '+'}{texte} demi-ton{'s' if abs(v) >= 1.95 else ''}"


def commentaire(score):
    if score is None:
        return "Pas de note : rien à comparer"
    if score >= 90:
        return "Excellent, très juste !"
    if score >= 75:
        return "Très bien, quelques notes à peine à côté"
    if score >= 60:
        return "Bien, quelques passages à reprendre"
    if score >= 40:
        return "À travailler : plusieurs passages faux"
    return "Beaucoup de notes fausses"


def _passages_faux(plie_total, pas, debut):
    """Passages où l'écart lissé dépasse FAUX_PASSAGE_CENTS pendant FAUX_PASSAGE_S : [{debut, fin, ecart_cents}]."""
    faux = np.abs(np.nan_to_num(plie_total, nan=0.0)) > FAUX_PASSAGE_CENTS
    sortie, i, n = [], 0, len(faux)
    while i < n:
        if not faux[i]:
            i += 1
            continue
        j = i
        while j < n and faux[j]:
            j += 1
        if (j - i) * pas >= FAUX_PASSAGE_S:
            sortie.append({"debut": round(debut + i * pas, 2), "fin": round(debut + j * pas, 2),
                           "ecart_cents": round(float(np.nanmedian(plie_total[i:j])), 0)})
        i = j
    return sortie


def noter_tour(r, prise, decalage_s, passage, tour, lignes, pas=PAS_S):
    """Un tour du passage : les lignes, la note totale, la courbe de la prise (calée, ramenée à l'octave de l'original)
    et les passages faux."""
    marge = int(round(RETARD_MAX_S / pas))
    t_ext, couvert = projeter(prise, decalage_s, passage, tour, marge, pas)
    n = len(r)
    affichee = t_ext[marge:marge + n].copy()
    plie_total = np.full(n, np.nan)
    notes, poids, sortie_lignes = [], [], []
    for l in lignes_du_passage(lignes, passage):
        a, b = l["a"], l["b"]
        part = couvert[marge + a:marge + b].mean() if b > a else 0.0
        info = {k: v for k, v in l.items() if k not in ("a", "b")}
        if part < COUVERTURE_MIN:
            sortie_lignes.append({**info, "score": None, "hors_prise": True})
            continue
        if (r[a:b] > 0).sum() < TRAMES_MIN:
            sortie_lignes.append({**info, "score": None, "trop_court": True})
            continue
        m = noter_ligne(r, t_ext, a, b, marge, pas)
        lag, plie = m.pop("_lag"), m.pop("_plie")
        plie_total[a:b] = plie
        t = t_ext[a + marge + lag:b + marge + lag]
        affichee[a:b] = np.where(t > 0, t * 2.0 ** (-m["octave"]), 0.0)
        notes.append(m["score"])
        poids.append(m["n_ref"])
        ligne = {**info, **m}
        ligne["conseil"] = conseil(ligne)
        sortie_lignes.append(ligne)
    # hors des lignes : la prise ramenée à l'octave la plus fréquente
    octaves = [l["octave"] for l in sortie_lignes if l.get("score") is not None]
    octave = int(np.median(octaves)) if octaves else 0
    hors = np.ones(n, bool)
    for l in sortie_lignes:
        if l.get("score") is not None:
            a = max(0, int(round((l["debut"] - passage.debut) / pas)))
            b = min(n, int(round((l["fin"] - passage.debut) / pas)))
            hors[a:b] = False
    affichee[hors] = np.where(affichee[hors] > 0, affichee[hors] * 2.0 ** (-octave), 0.0)
    score = float(np.average(notes, weights=poids)) if notes and sum(poids) else None
    retards = [l["retard_s"] for l in sortie_lignes if l.get("score") is not None]
    return {"tour": tour, "score": None if score is None else round(score, 1),
            "lignes": sortie_lignes, "octave": octave,
            "retard_s": round(float(np.median(retards)), 3) if retards else 0.0,
            "presence": round(float(np.average([l["presence"] for l in sortie_lignes if l.get("score") is not None],
                                               weights=poids)), 3) if notes and sum(poids) else 0.0,
            "couverture": round(float(couvert[marge:marge + n].mean()), 3) if n else 0.0,
            "prise_hz": [round(float(x), 1) if x > 0 else 0 for x in affichee],
            "passages_faux": _passages_faux(plie_total, pas, passage.debut)}


def noter(reference, prise, lignes, passage, decalage_s, pas=PAS_S, pas_reference=PAS_S):
    """La note d'une prise. reference : F0 de l'original (Hz) sur le temps du son, trame i à i × pas_reference ; prise :
    F0 de la prise, trame i à i × pas ; lignes : [(debut, fin, texte)] en temps du son (paroles, sinon phrases) ;
    passage : Passage ; decalage_s : temps du beat lu où tombe le début de la prise.

    Rend un dict prêt pour JSON : score (moyenne des tours chantés), meilleur tour, commentaire, octave, retard moyen,
    la courbe de l'original sur le passage et, par tour, les lignes, la courbe de la prise et les passages faux."""
    r = reference_du_passage(reference, passage, pas, pas_reference)
    if not lignes:
        lignes = [(passage.debut + d, passage.debut + f, t) for d, f, t in phrases(r, pas)]
    tours = []
    for k in range(max(1, passage.repetitions)):
        t = noter_tour(r, prise, decalage_s, passage, k, lignes, pas)
        if t["couverture"] > 0:
            tours.append(t)
    notes = [t["score"] for t in tours if t["score"] is not None and t["couverture"] >= COUVERTURE_MIN]
    score = round(float(np.mean(notes)), 1) if notes else None
    meilleur = max(range(len(tours)), key=lambda k: tours[k]["score"] or -1) if tours else None
    principal = tours[meilleur] if meilleur is not None else None
    lignes_notees = [l for l in (principal["lignes"] if principal else []) if l.get("score") is not None]
    return {"format": 1, "score": None if score is None else int(round(score)),
            "score_exact": score, "commentaire": commentaire(score),
            "meilleur_tour": meilleur, "octave": principal["octave"] if principal else 0,
            "retard_s": principal["retard_s"] if principal else 0.0,
            "lignes_notees": len(lignes_notees),
            "lignes_fausses": sum(1 for l in lignes_notees if l["score"] < SEUIL_LIGNE_FAUSSE),
            "pas_s": pas, "debut_s": round(passage.debut, 3), "fin_s": round(passage.fin, 3),
            "repetitions": max(1, passage.repetitions),
            "reference_hz": [round(float(x), 1) if x > 0 else 0 for x in r],
            "tours": tours,
            "reglages": {"juste_cents": JUSTE_CENTS, "faux_cents": FAUX_CENTS, "retard_max_s": RETARD_MAX_S,
                         "presence_pleine": PRESENCE_PLEINE}}


def phrase_octave(octave):
    """« une octave plus bas que l'original », « à la hauteur de l'original »…"""
    if not octave:
        return "à la hauteur de l’original"
    n = abs(octave)
    quoi = "une octave" if n == 1 else f"{n} octaves"
    return f"{quoi} plus {'bas' if octave < 0 else 'haut'} que l’original (compté juste)"


def phrase_retard(retard_s, seuil=0.06):
    """« ta voix arrive en moyenne 80 ms après l'original » ; vide sous le seuil."""
    if retard_s is None or abs(retard_s) < seuil:
        return ""
    ms = int(round(abs(retard_s) * 1000 / 10.0) * 10)
    return f"ta voix arrive en moyenne {ms} ms {'après' if retard_s > 0 else 'avant'} l’original"
