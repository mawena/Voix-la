"""Session GPU « réalisme » du banc : conversion YingMusic avec décalage arbitraire (musique transposée de la même façon),
références multiples par registre, résidu de style source coupé ou atténué, balayage de cfg.

Ce fichier a deux parties :
  1. le CODE DU NOYAU (entre les lignes « DEBUT/FIN DU CODE DU NOYAU ») : ajouté par le lanceur à la suite de
     kaggle/doubler_kaggle.py et de banc/noyau_banc.py (l'étape « realisme » s'ajoute à celles du banc) ;
  2. le LANCEUR LOCAL (PC, sans GPU) : construit le noyau, le simule, le lance (sans modifier banc.py) et transpose la musique.

Utilisation (rien ne part sur Kaggle sans « lancer ») :
    python3 banc/realisme/noyau_realisme.py simuler realisme       # plan, durée GPU estimée, noyau compilé ; ne lance rien
    python3 banc/realisme/noyau_realisme.py lancer realisme        # banc.py lancer, avec ce noyau (verrou Kaggle compris)
    python3 banc/realisme/noyau_realisme.py musiques realisme      # après rapatriement : musique transposée de chaque variante

Session : banc/sessions/realisme.json (étape « realisme » ; pas de « juger » : voir SESSION_MODELE). Une variante :
    nom            nom de la version (fichiers <nom>.flac et <nom>.json dans resultats/<session>/<extrait>/)
    decalage_dt    entier (demi-tons de la voix), « defaut » (celui de l'extrait dans cas.json) ou « regle » (règle de
                   transposition.py : tessiture dans reglages.tessiture, centiles de F0 mesurés sur la voix de l'extrait)
    musique_dt     transposition de la musique (« auto » : la voix moins l'octave entière ; null : aucune)
    reference      lettre de la référence (<cible>-reference-<lettre>.wav, « A » par défaut) ou nom de fichier
    registres      références multiples [{nom, reference, centre_hz?}] : celle dont le centre est le plus proche de la hauteur
                   de chaque morceau (F0 de la source + décalage) est choisie, morceau par morceau
    style_r        échelle du résidu de style de la voix SOURCE (1 : actuel ; 0,5 : atténué ; 0 : coupé)
    cfg, pas, graine, pistes, extraits, limite_s
    balayage       {champ: [valeurs]} : produit cartésien (le nom reçoit -<champ><valeur>) ; une variante identique à une
                   précédente n'est pas reconvertie (alias)
"""
import base64
import itertools
import json
import math
import os
import sys
import time
import types

# === DEBUT DU CODE DU NOYAU ==========================================================================================

import base64
import itertools
import math
import types

CHAMPS_VARIANTE = ('nom', 'decalage_dt', 'musique_dt', 'reference', 'registres', 'style_r', 'cfg', 'pas', 'graine', 'pistes',
                   'extraits', 'limite_s', 'balayage', 'description')
DEFAUTS_VARIANTE = {'decalage_dt': 'defaut', 'musique_dt': 'auto', 'reference': 'A', 'registres': None, 'style_r': 1.0,
                    'pistes': ['voix']}
SIGNATURE = ('decalage_dt', 'musique_dt', 'reference', 'registres', 'style_r', 'cfg', 'pas', 'graine', 'pistes', 'limite_s')


def valeur_nom(v):
    """Texte d'une valeur de balayage pour le nom d'une variante (0.5 -> 0.5, 1.0 -> 1, 0 -> 0)."""
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return str(v).replace(' ', '')


def developper_variantes(variantes, reglages):
    """Variantes concrètes (sans balayage) : chacune a tous ses champs (défauts de la variante, puis cfg, pas et graine des
    réglages de la session). Une variante qui a la même signature qu'une précédente est un alias (« alias_de »), pas une
    conversion de plus. Rend la liste, dans l'ordre."""
    concretes, vues = [], {}
    for v in variantes:
        inconnus = set(v) - set(CHAMPS_VARIANTE)
        if inconnus:
            raise ValueError(f"variante {v.get('nom')} : champs inconnus {sorted(inconnus)}")
        base = {k: x for k, x in v.items() if k != 'balayage'}
        balayage = v.get('balayage') or {}
        cles = list(balayage)
        for combinaison in itertools.product(*(balayage[c] for c in cles)) if cles else [()]:
            c = {**DEFAUTS_VARIANTE, **base}
            suffixe = ''
            for cle, val in zip(cles, combinaison):
                c[cle] = val
                suffixe += f'-{cle}{valeur_nom(val)}'
            c['nom'] = base['nom'] + suffixe
            c.setdefault('cfg', reglages.get('cfg'))
            if c.get('cfg') is None:
                c['cfg'] = reglages.get('cfg')
            if c.get('pas') is None:
                c['pas'] = reglages.get('pas')
            if c.get('graine') is None:
                c['graine'] = reglages.get('graine')
            signature = json.dumps({k: c.get(k) for k in SIGNATURE}, sort_keys=True)
            if signature in vues:
                c['alias_de'] = vues[signature]
            else:
                vues[signature] = c['nom']
            concretes.append(c)
    noms = [c['nom'] for c in concretes]
    if len(set(noms)) != len(noms):
        raise ValueError('deux variantes portent le même nom : ' + ', '.join(sorted({n for n in noms if noms.count(n) > 1})))
    return concretes


def choisir_reference(f0_hz, registres):
    """Registre le plus proche (en demi-tons) de f0_hz parmi registres = [{'nom', 'centre_hz', ...}] ; le premier en cas
    d'égalité. f0_hz absent ou nul (morceau sans voix) : le registre du milieu (par centre)."""
    if not registres:
        raise ValueError('aucun registre')
    if not f0_hz or f0_hz <= 0:
        return sorted(registres, key=lambda r: r['centre_hz'])[(len(registres) - 1) // 2]
    return min(registres, key=lambda r: abs(math.log2(f0_hz / r['centre_hz'])))


def centiles_f0(f0):
    """{'p10', 'p50', 'p90'} (Hz) des trames voisées (f0 > 1) d'une courbe de F0 ; None s'il n'y en a pas."""
    import numpy as np
    f = np.asarray(f0, dtype=float).ravel()
    f = f[f > 1]
    if not len(f):
        return None
    return {k: float(np.percentile(f, p)) for k, p in (('p10', 10), ('p50', 50), ('p90', 90))}


def ns_transposition():
    """Le module transposition.py : sa copie embarquée dans le noyau (TRANSPOSITION_SRC), sinon le fichier voisin."""
    g = globals()
    if g.get('TRANSPOSITION_SRC'):
        ns = {'__name__': 'transposition'}
        exec(compile(base64.b64decode(g['TRANSPOSITION_SRC']).decode(), 'transposition.py', 'exec'), ns)
        return types.SimpleNamespace(**ns)
    ici = os.path.dirname(os.path.abspath(__file__))
    if ici not in sys.path:
        sys.path.insert(0, ici)
    import transposition
    return transposition


def decalage_de_variante(v, decalage_extrait, f0_source=None, tessiture=None):
    """(décalage de la voix en demi-tons entiers, transposition de la musique ou None, info de la règle ou None).
    « defaut » : celui de l'extrait ; « regle » : règle de transposition.py (tessiture et F0 de la source requises) ;
    entier : tel quel. musique_dt « auto » : la voix moins son octave entière, ramenée dans [-6, +6] ; null : musique intacte."""
    d = v.get('decalage_dt', 'defaut')
    regle = None
    if d == 'defaut' or d is None:
        d = int(decalage_extrait)
    elif d == 'regle':
        if not tessiture or not f0_source:
            raise ValueError("décalage « regle » : tessiture (réglages) et F0 de la source nécessaires")
        regle = ns_transposition().regle_decalage(f0_source, tessiture)
        d = int(regle['decalage_voix_dt'])
    else:
        d = int(d)
    m = v.get('musique_dt', 'auto')
    if m == 'auto':
        musique = ((d + 6) % 12) - 6
    elif m is None:
        musique = None
    else:
        musique = int(m)
    return d, musique, regle


def classe_moteur_realisme():
    """MoteurYing du banc, avec : échelle du résidu de style de la source, références par registre. Définie à l'appel (le
    noyau n'a MoteurYing qu'une fois noyau_banc.py chargé)."""
    base = globals()['MoteurYing']

    class MoteurYingRealisme(base):
        echelle_style = 1.0
        usage_references = None

        def length_regulator(self, s, longueur, f0, style2):
            cond, style = super().length_regulator(s, longueur, f0, style2)
            e = self.echelle_style
            if style is None or e == 1.0:
                return cond, style
            return cond, (None if e == 0 else style * e)

        def preparer_ref(self, chemin):
            """La référence, plus son centre de hauteur (médiane du F0 RMVPE des trames voisées) pour les registres."""
            e, self.echelle_style = self.echelle_style, 1.0   # le style de la RÉFÉRENCE n'est jamais touché
            try:
                ref = super().preparer_ref(chemin)
            finally:
                self.echelle_style = e
            ref['centre_hz'] = self.centre_f0(chemin)
            return ref

        def centre_f0(self, chemin):
            import librosa
            torch = self.torch
            y = librosa.load(chemin, sr=16000)[0][:16000 * 25]
            c = centiles_f0(self.f0_fn(torch.tensor(y).float().to(self.device), thred=0.03))
            return None if c is None else c['p50']

        def f0_morceau(self, x):
            """Centiles de F0 (p10, p50, p90, Hz) d'une piste ou d'un morceau (44,1 kHz mono) ; None sans voix."""
            import numpy as np
            import torchaudio
            torch = self.torch
            with torch.no_grad():
                src = torch.from_numpy(np.ascontiguousarray(x)).float().unsqueeze(0).to(self.device)
                return centiles_f0(self.f0_fn(torchaudio.functional.resample(src, SR, 16000)[0], thred=0.03))

        def f0_global(self, x):
            return self.f0_morceau(x)

        def convertir_morceau(self, xs, ref, decalage, pas):
            registres = ref.get('registres') if isinstance(ref, dict) else None
            if not registres:
                return super().convertir_morceau(xs, ref, decalage, pas)
            c = self.f0_morceau(xs)
            cible = None if c is None else c['p50'] * 2 ** (decalage / 12)
            choix = choisir_reference(cible, registres)
            if self.usage_references is not None:
                self.usage_references[choix['nom']] = self.usage_references.get(choix['nom'], 0) + 1
            return super().convertir_morceau(xs, choix['ref'], decalage, pas)
    return MoteurYingRealisme


def creer_moteur(mot, checkpoint=None):
    """Le moteur de l'étape (point d'entrée des essais en simulation)."""
    if mot.get('type', 'yingmusic') != 'yingmusic':
        raise ValueError("l'étape realisme ne fait que YingMusic-SVC (résidu de style, cfg, registres)")
    return classe_moteur_realisme()(checkpoint, None)


def fichier_reference(nom_ou_lettre):
    """Fichier du jeu de données d'une référence : une lettre (« A » : <cible>-reference-A.wav) ou un nom de fichier
    (« {cible} » y est remplacé par l'identifiant de la voix cible)."""
    if len(nom_ou_lettre) == 1:
        return f"{B.get('voix_cible', 'cible')}-reference-{nom_ou_lettre}.wav"
    return nom_ou_lettre.replace('{cible}', str(B.get('voix_cible', 'cible')))


def etape_realisme(a):
    """Conversion des variantes (voir l'en-tête de noyau_realisme.py). Sorties comme celles de l'étape « convertir » du banc,
    donc rejouables par « juger » et « banc.py mixer » : <extrait>/<variante>.flac et <variante>.json (type « version »),
    avec en plus : decalage_dt, musique_dt (à appliquer à la musique seule), style_r, cfg, registres, f0_source_hz, regle."""
    import numpy as np
    import soundfile as sf
    variantes = developper_variantes(a['variantes'], B)
    mot = a.get('moteur') or {'type': 'yingmusic'}
    m = creer_moteur(mot, mot.get('checkpoint'))
    log(f"REALISME moteur {m.type} chargé en {m.chargement_s:.0f} s ; {len(variantes)} variantes "
        f"({sum(1 for v in variantes if v.get('alias_de'))} alias)")
    torch = m.torch
    refs = {}

    def reference(nom):
        fichier = fichier_reference(nom)
        if fichier not in refs:
            t0 = time.time()
            chemin = entree(fichier)
            if chemin is None:
                raise FileNotFoundError(f'référence {fichier} absente du jeu de données')
            refs[fichier] = m.preparer_ref(chemin)
            log(f"REALISME référence {fichier} préparée en {time.time() - t0:.1f} s ({refs[fichier]['duree']:.2f} s, "
                f"centre {refs[fichier].get('centre_hz')} Hz)")
        return refs[fichier]
    ancien_cfg = B['cfg']
    try:
        for ex, e in extraits_de(a).items():
            pistes_lues, f0_source = {}, None
            for v in variantes:
                if v.get('extraits') and ex not in v['extraits']:
                    continue
                if v.get('alias_de'):
                    ecrire_json(os.path.join(TRAVAIL, ex, f"{v['nom']}.json"), {'type': 'alias', 'version': v['nom'],
                                                                                'alias_de': v['alias_de']})
                    continue
                manquantes = [fichier_reference(r['reference']) for r in v['registres']] if v.get('registres') else [
                    fichier_reference(v['reference'])]
                manquantes = [f for f in manquantes if entree(f) is None]
                if manquantes:
                    log(f"REALISME {ex} {v['nom']} : sautée, référence(s) absente(s) du jeu : {', '.join(manquantes)}")
                    ecrire_json(os.path.join(TRAVAIL, ex, f"{v['nom']}.json"), {'type': 'sautee', 'version': v['nom'],
                                                                                'references_absentes': manquantes})
                    continue
                t0 = time.time()
                if f0_source is None:
                    x0 = mono(sf.read(piste_chemin(ex, v['pistes'][0]), dtype='float32', always_2d=True)[0])
                    f0_source = m.f0_global(x0)
                tessiture = B.get('tessiture')
                d, musique_dt, regle = decalage_de_variante(v, e['decalage'], f0_source, tessiture)
                B['cfg'] = v['cfg']
                m.echelle_style = float(v['style_r'])
                m.usage_references = {}
                if v.get('registres'):
                    registres = []
                    for r in v['registres']:
                        rf = reference(r['reference'])
                        centre = r.get('centre_hz') or rf.get('centre_hz')
                        if not centre:
                            raise ValueError(f"registre {r['nom']} : centre de hauteur inconnu (centre_hz)")
                        registres.append({'nom': r['nom'], 'centre_hz': float(centre), 'ref': rf})
                    plus_longue = max((r['ref'] for r in registres), key=lambda z: z['mel2'].size(2))
                    ref = {**plus_longue, 'registres': registres}
                else:
                    ref = reference(v['reference'])
                fiche = {'type': 'version', 'version': v['nom'], 'description': v.get('description'), 'moteur': m.type,
                         'pas': v['pas'], 'cfg': v['cfg'], 'decalage_dt': d, 'musique_dt': musique_dt, 'style_r': v['style_r'],
                         'graine': v['graine'], 'reference': v['reference'] if not v.get('registres') else 'registres',
                         'registres': [{'nom': r['nom'], 'centre_hz': r['centre_hz']} for r in registres] if v.get('registres') else None,
                         'f0_source_hz': f0_source, 'regle': regle, 'pistes': {}}
                total = None
                for piste in v['pistes']:
                    x = mono(sf.read(piste_chemin(ex, piste), dtype='float32', always_2d=True)[0])
                    if v.get('limite_s'):
                        x = x[:int(v['limite_s'] * SR)]
                    t1 = time.time()
                    y, info = convertir_piste(m, ref, x, d, v['pas'], v['graine'])
                    duree = time.time() - t1
                    g = rms_actif(x) / max(rms_actif(y, x), 1e-9)
                    y = (y * g).astype(np.float32)
                    if len(v['pistes']) > 1:
                        sf.write(os.path.join(TRAVAIL, ex, f"{v['nom']}_{piste}.flac"), np.clip(y, -1, 1), SR, subtype='PCM_24')
                    total = y if total is None else total + y
                    fiche['pistes'][piste] = {**info, 'conversion_s': round(duree, 1), 'gain_niveau_db': round(20 * np.log10(g), 2),
                                              'rtf': round(duree / max(info['secondes_converties'], 1e-3), 3)}
                    torch.cuda.empty_cache()
                crete = float(np.abs(total).max())
                if crete > 0.99:
                    total *= 0.99 / crete
                sf.write(os.path.join(TRAVAIL, ex, f"{v['nom']}.flac"), total, SR, subtype='PCM_24')
                fiche['usage_references'] = dict(m.usage_references)
                fiche.update({'conversion_s': round(sum(p['conversion_s'] for p in fiche['pistes'].values()), 1),
                              'total_s': round(time.time() - t0, 1), 'crete': round(crete, 3), 'chargement_moteur_s': m.chargement_s})
                ecrire_json(os.path.join(TRAVAIL, ex, f"{v['nom']}.json"), fiche)
                log(f"REALISME {ex} {v['nom']} : {json.dumps(fiche, ensure_ascii=False, default=float)}")
    finally:
        B['cfg'] = ancien_cfg


_principal_banc = globals().get('principal')   # celui du banc (noyau_banc.py), quand il est chargé


def principal():
    """Celui du banc, plus l'étape « realisme »."""
    if len(sys.argv) >= 3 and sys.argv[1] == 'realisme':
        if SOUCHES not in sys.path and os.path.isdir(SOUCHES):
            sys.path.insert(0, SOUCHES)
        return etape_realisme(json.loads(sys.argv[2]))
    return _principal_banc()

# === FIN DU CODE DU NOYAU ============================================================================================


# --- Lanceur local (PC, sans GPU) ----------------------------------------------------------------------------------

ICI = os.path.dirname(os.path.abspath(__file__))
BANC = os.path.dirname(ICI)
RTF_YING = 3.04   # secondes de GPU par seconde convertie (session du banc, YingMusic 30 pas, T4)
CHARGEMENT_S = 600


SESSION_MODELE = {
    "description": "Session réalisme : références, décalage par la règle (musique transposée), résidu de style, cfg. "
                   "La tessiture ci-dessous est INVENTÉE : la remplacer par celle de tessiture.py.",
    "noyau": "doublage-banc-realisme",
    "base": "depot",
    "limite_totale_s": 5400,
    "reglages": {"pas": 30, "cfg": 0.7, "fp16": True, "graine": 1234, "pad_s": 0.25, "fondu_s": 0.06,
                 "tessiture": {"p05": 98.0, "p10": 110.0, "p50": 165.0, "p90": 247.0, "p95": 277.0, "confort": [123.0, 220.0]},
                 "precharger": {"yingmusic": True}},
    "etapes": [
        {"etape": "realisme", "nom": "realisme", "limite_s": 4200, "minimum_s": 900, "moteur": {"type": "yingmusic"},
         "variantes": [
             {"nom": "R0", "description": "témoin : décalage de l'extrait (cas.json), référence A, résidu de style 1, cfg 0,7",
              "decalage_dt": "defaut", "musique_dt": None},
             {"nom": "Ra", "description": "décalage par la règle (voix) et musique transposée de la même façon",
              "decalage_dt": "regle", "musique_dt": "auto"},
             {"nom": "Rb", "description": "comme Ra, avec une référence par registre (B grave, A médian, C aigu)",
              "decalage_dt": "regle", "registres": [{"nom": "grave", "reference": "B"}, {"nom": "median", "reference": "A"},
                                                    {"nom": "aigu", "reference": "C"}]},
             {"nom": "Rs", "description": "comme Ra, résidu de style de la source atténué (0,5) ou coupé (0)",
              "decalage_dt": "regle", "balayage": {"style_r": [0.5, 0]}},
             {"nom": "Rc", "description": "comme Ra, balayage de cfg (0,7 = Ra : alias, pas reconverti)",
              "decalage_dt": "regle", "balayage": {"cfg": [0.5, 0.7, 1.0, 1.5]}}]}]}
# Pas d'étape « juger » : celle du banc compare le F0 de chaque version au décalage de l'EXTRAIT (cas.json), faux pour des
# variantes qui ont chacune leur décalage ; les .flac rapatriés se jugent avec le juge local (banc/realisme/juge.py).


def texte_noyau():
    """Le code du noyau de ce fichier (entre les repères), prêt à être concaténé."""
    t = open(os.path.join(ICI, 'noyau_realisme.py'), encoding='utf-8').read()
    a = t.index('# === DEBUT DU CODE DU NOYAU')
    b = t.index('# === FIN DU CODE DU NOYAU')
    return t[a:b].split('\n', 1)[1]


def construire_noyau_realisme(reglages, base_noyau=None, banc_py=None):
    """Comme banc.construire_noyau, avec l'étape « realisme » : noyau de doubler (sans son bloc final), noyau_banc.py (sans
    son bloc final), transposition.py embarqué, ce noyau, puis le bloc final du banc (principal redéfini ici). Rend le code du
    lanceur Kaggle."""
    banc = banc_py or charger_banc()
    base = open(base_noyau or banc.BASE_NOYAU, encoding='utf-8').read()
    i = base.rfind("\nif __name__ == '__main__':\n")
    assert i > 0, 'fin de doubler_kaggle.py inattendue'
    base = base[:i].rstrip('\n').replace('__REGLAGES__', base64.b64encode(json.dumps(reglages).encode()).decode())
    nb = open(os.path.join(BANC, 'noyau_banc.py'), encoding='utf-8').read()
    j = nb.rfind("\nif __name__ == '__main__':\n")
    assert j > 0, 'fin de noyau_banc.py inattendue'
    transpo = base64.b64encode(open(os.path.join(ICI, 'transposition.py'), 'rb').read()).decode()
    complet = (base + '\n\n\n' + nb[:j].rstrip('\n') + '\n\n\n' + f"TRANSPOSITION_SRC = '{transpo}'\n\n"
               + texte_noyau().rstrip('\n') + '\n\n\n' + nb[j + 1:])
    code = base64.b64encode(complet.encode()).decode()
    return ('"""Banc d\'essai de conversion de voix chantée, session réalisme (banc/realisme/noyau_realisme.py) : lanceur."""\n'
            'import base64, subprocess, sys\n'
            f"SRC = base64.b64decode('{code}').decode()\n"
            "open('/tmp/banc_complet.py', 'w').write(SRC)\n"
            "r = subprocess.run([sys.executable, '/tmp/banc_complet.py', 'orchestrer'])\n"
            "print('LANCEUR fin, code', r.returncode, flush=True)\n"), complet


def charger_banc(cas=None, kaggle=False):
    """banc.py comme module, configuré comme sa ligne de commande (fichier des cas ; comptes Kaggle si kaggle)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('banc_essai_realisme', os.path.join(BANC, 'banc.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    if cas != 'aucun':
        m.configurer(cas or m.CAS_DEFAUT)
    if kaggle:
        m.configurer_kaggle()
    return m


def plan(session, banc=None, cas=None):
    """Plan d'une session sans rien lancer : variantes concrètes par extrait, secondes à convertir, GPU estimé."""
    banc = banc or charger_banc(cas)
    cfg = json.loads((banc.SESSIONS / f'{session}.json').read_text())
    reglages = cfg.get('reglages', {})
    sortie = {'session': session, 'etapes': [], 'conversions': 0, 'secondes_a_convertir': 0.0}
    for etape in cfg['etapes']:
        if etape['etape'] != 'realisme':
            sortie['etapes'].append({'etape': etape['etape'], 'nom': etape.get('nom')})
            continue
        variantes = developper_variantes(etape['variantes'], reglages)
        par_extrait = {}
        for ex, e in banc.EXTRAITS.items():
            if etape.get('extraits') and ex not in etape['extraits']:
                continue
            duree = float(e['fin'] - e['debut'])
            vs = [v for v in variantes if not v.get('alias_de') and (not v.get('extraits') or ex in v['extraits'])]
            for v in vs:
                sortie['secondes_a_convertir'] += (v.get('limite_s') or duree) * len(v['pistes'])
                sortie['conversions'] += 1
            par_extrait[ex] = [v['nom'] for v in vs]
        sortie['etapes'].append({'etape': 'realisme', 'nom': etape.get('nom'), 'variantes': [
            {k: v[k] for k in ('nom', 'decalage_dt', 'musique_dt', 'reference', 'style_r', 'cfg') if k in v} | (
                {'alias_de': v['alias_de']} if v.get('alias_de') else {}) | (
                {'registres': [r['nom'] for r in v['registres']]} if v.get('registres') else {}) for v in variantes],
            'par_extrait': par_extrait})
    sortie['gpu_estime_min'] = round((sortie['secondes_a_convertir'] * RTF_YING + CHARGEMENT_S) / 60, 1)
    sortie['limite_totale_s'] = cfg.get('limite_totale_s')
    return sortie


def simuler(session, cas=None):
    """Construit et compile le noyau, affiche le plan : aucun envoi, aucune session."""
    banc = charger_banc(cas)
    p = plan(session, banc)
    cfg = json.loads((banc.SESSIONS / f'{session}.json').read_text())
    reglages = {**cfg.get('reglages', {}), 'session': session, 'etapes': cfg['etapes'],
                'extraits': {ex: {'decalage': e['decalage'], 'paroles': e['paroles']} for ex, e in banc.EXTRAITS.items()},
                'limite_totale_s': cfg.get('limite_totale_s', 3600), 'voix_cible': banc.CIBLE}
    lanceur, complet = construire_noyau_realisme(reglages, getattr(banc, 'NOYAU_DEPOT', None) if cfg.get('base') == 'depot' else None, banc)
    compile(complet, 'noyau_realisme_complet.py', 'exec')
    p['noyau'] = {'octets': len(complet), 'compile': True, 'etape_realisme': 'def etape_realisme' in complet}
    return p


def lancer(session, cas=None):
    """banc.py lancer <session>, avec le noyau réalisme (banc.construire_noyau remplacé le temps de l'appel)."""
    banc = charger_banc(cas, kaggle=True)
    original = banc.construire_noyau

    def construire(reglages, base_noyau=None):
        return construire_noyau_realisme(reglages, base_noyau, banc)[0]
    banc.construire_noyau = construire
    try:
        import argparse
        banc.lancer(argparse.Namespace(session=session))
    finally:
        banc.construire_noyau = original


def musiques(session, ex_seulement=None, cas=None):
    """Après « banc.py rapatrier » : pour chaque variante dont la fiche a musique_dt, la musique seule de l'extrait transposée
    de musique_dt demi-tons (Rubber Band R3) : resultats/<session>/<extrait>/<variante>_musique.wav."""
    import soundfile as sf
    banc = charger_banc(cas)
    sys.path.insert(0, ICI)
    import transposition
    fait = []
    for ex in banc.EXTRAITS:
        if ex_seulement and ex not in ex_seulement:
            continue
        musique = banc.TRAVAIL / ex / 'instrumental.wav'
        cache = {}
        for f in sorted((banc.RESULTATS / session / ex).glob('*.json')):
            fiche = json.loads(f.read_text())
            n = fiche.get('musique_dt')
            if fiche.get('type') != 'version' or n is None or not musique.exists():
                continue
            if n not in cache:
                x, sr = sf.read(str(musique), dtype='float32', always_2d=True)
                cache[n] = transposition.transposer(x, n, sr, 'r3')
            sortie = f.with_name(f'{f.stem}_musique.wav')
            sf.write(str(sortie), cache[n], banc.SR, subtype='FLOAT')
            fait.append(str(sortie))
    return fait


def _principal(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--cas', help='fichier des cas du banc (défaut : banc/cas.json)')
    sp = p.add_subparsers(dest='cmd', required=True)
    sp.add_parser('simuler').add_argument('session')
    sp.add_parser('lancer').add_argument('session')
    m = sp.add_parser('musiques')
    m.add_argument('session')
    m.add_argument('--extraits', nargs='+')
    sp.add_parser('session-modele', help='écrit le modèle de session sur la sortie standard (banc/sessions/realisme.json)')
    a = p.parse_args(argv)
    if a.cmd == 'simuler':
        print(json.dumps(simuler(a.session, a.cas), ensure_ascii=False, indent=1))
    elif a.cmd == 'lancer':
        lancer(a.session, a.cas)
    elif a.cmd == 'session-modele':
        print(json.dumps(SESSION_MODELE, ensure_ascii=False, indent=1))
    else:
        print('\n'.join(musiques(a.session, a.extraits, a.cas)))


if __name__ == '__main__':
    _principal()
