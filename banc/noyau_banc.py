# ======================================================================================================================
# Banc d'essai (banc/noyau_banc.py) : banc/banc.py ajoute ce code après les fonctions de kaggle/doubler_kaggle.py
# (installer, cloner, poids_depuis_outils, decouper, enveloppe_db, lisser_db, seuil_silence, rampe…), lues telles quelles.
#
#   orchestrer : installation (montage du noyau d'outils), puis chaque étape de REGLAGES['etapes'] dans son propre
#                processus (le GPU est rendu entre deux étapes ; Seed-VC et YingMusic ont tous deux un paquet « modules »)
#   separer    : voix complète -> voix principale (Roformer karaoké) + chœurs (le reste), puis reverb retirée des deux
#                (Roformer dereverb) ; la reverb d'origine est jetée, notre reverb est remise au mix
#   convertir  : Seed-VC (zéro-shot ou affiné) ou YingMusic-SVC, même découpage aux silences et même assemblage que
#                doubler (morceaux de 8 s au plus, fondus, porte sur les silences), décalage fixe par extrait
#   entrainer  : affinage de Seed-VC (train.py, classe Trainer) sur la parole de la voix cible, points de contrôle « net »
#                seuls
#   juger      : ressemblance (WavLM-SV, ECAPA, CAMPPlus), mélodie (RMVPE), paroles (Whisper large-v3), aigus, équilibre
#                spectral (écart des bandes de tiers d'octave à la voix d'origine sèche)
# Banc « vitesse » (06/11/2026), avec le noyau actuel du dépôt (réglage base « depot » de banc.py) :
#   separer_voix       : séparation voix / reste (Roformer de doubler) en plusieurs variantes de précision (fp32,
#                        autocast, fp16 natif, torch.compile) et de recouvrement ; temps, mémoire, SDR à la variante de
#                        référence ; la référence devient la voix des étapes suivantes
#   nettoyer_variantes : nettoyage (karaoké puis dereverb des deux pistes) dans les mêmes variantes ; temps de chaque
#                        passe, SDR de chaque piste à la référence ; <piste>_<variante>.wav, la référence sans suffixe
#   deriver            : versions tirées d'une autre sans GPU (égalisation du timbre sur la voix d'origine sèche)
#   parallele          : des suites d'étapes, une par carte (CUDA_VISIBLE_DEVICES), en même temps ; une seule carte : à
#                        la suite sur la carte 0
# Sorties dans /kaggle/working/<extrait>/ : <version>.flac (voix convertie sèche, 44,1 kHz mono, même nombre
# d'échantillons que l'extrait, calée sur l'échantillon 0), <version>_<piste>.flac, <version>.json ; juge_<session>.json ;
# session.json ; modeles/ (points de contrôle affinés) ; banc vitesse : separation_voix.json, nettoyage.json, voix.flac
# (voix séparée de référence, pour le mix d'écoute). Pistes séparées et nettoyées : dans REGLAGES['dossier_pistes']
# (banc vitesse : /tmp, elles ne reviennent pas), sinon avec les sorties. Fichiers de la voix cible dans le jeu :
# <cible>-reference-A.wav (et d'autres références : <cible>-reference-<X>.wav), <cible>-ft-*.wav (affinage),
# <cible>-tenue-*.wav (prise gardée pour le juge), <cible> = REGLAGES['voix_cible'] ; extraits : extrait-<ex>-voix.wav
# (voix déjà séparée) ou extrait-<ex>-original.wav (son d'origine, séparé par separer_voix).
# ======================================================================================================================
import re
from argparse import Namespace

B = REGLAGES
TRAVAIL = SORTIE
PISTES = B.get('dossier_pistes') or TRAVAIL   # voix séparées et nettoyées (banc vitesse : /tmp, seules les mesures reviennent)
CIBLE = B.get('voix_cible', 'cible')   # identifiant de la voix cible (banc.py, fichier des cas)
COMPLET = '/tmp/banc_complet.py'
PAROLES_LOCALES = '/tmp/banc_paroles.json'   # chemins de Whisper copié du montage d'outils (orchestrer), pour le juge
# Noyau actuel du dépôt (base « depot ») : son moteur YingMusic-SVC et son installation, gardés avant que les définitions
# du banc, plus bas, ne reprennent ces noms (copie figée du 03/11 : ils n'existent pas, le banc garde les siens)
_MOTEUR_YING_NOYAU = globals().get('MoteurYing')
_INSTALLER_YING_NOYAU = globals().get('installer_ying')
YING = '/tmp/yingmusic'
YING_DEPOT = 'https://github.com/GiantAILab/YingMusic-SVC.git'
YING_COMMIT = '4974a80c6044c4557059548409379f6365129f88'   # main au 29/12/2025
YING_POIDS = ('GiantAILab/YingMusic-SVC', 'YingMusic-SVC-full.pt')
PISTES_SEPAREES = ('lead', 'choeurs', 'lead_sec', 'choeurs_sec')
SV_PRE = ('Plachta/Seed-VC', 'DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth',
          'config_dit_mel_seed_uvit_whisper_base_f0_44k.yml')
CONFIG_FT = 'configs/presets/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml'


def ecrire_json(chemin, d):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, 'w') as f:
        json.dump(d, f, ensure_ascii=False, indent=1, default=float)


def lire_json(chemin, defaut=None):
    try:
        with open(chemin) as f:
            return json.load(f)
    except (OSError, ValueError):
        return defaut


def entree(nom):
    """Un fichier du jeu de données (ou d'un autre montage), sinon du dossier de travail ; None s'il manque."""
    for motif in (f'/kaggle/input/**/{nom}', f'{TRAVAIL}/**/{nom}'):
        c = glob.glob(motif, recursive=True)
        if c:
            return c[0]
    return None


def piste_chemin(ex, piste):
    """Une piste d'un extrait : « voix » (séparée au banc par separer_voix, sinon celle du jeu de données),
    « <piste> » ou « <piste>@<variante> » (<piste>_<variante>.wav : sortie d'une variante de séparation ou de nettoyage)."""
    if '@' in piste:
        base, variante = piste.split('@', 1)
        return os.path.join(PISTES, ex, f'{base}_{variante}.wav')
    if piste == 'voix':
        local = os.path.join(PISTES, ex, 'voix.wav')
        return local if os.path.exists(local) else entree(f'extrait-{ex}-voix.wav')
    return os.path.join(PISTES, ex, f'{piste}.wav')


def extraits_de(a):
    """Les extraits d'une étape : ceux qu'elle nomme (a['extraits'], une carte n'en fait qu'une part), sinon tous."""
    return {ex: e for ex, e in B['extraits'].items() if not a.get('extraits') or ex in a['extraits']}


def charger_torch():
    os.environ['TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD'] = '1'   # points de contrôle officiels (pickle) de Seed-VC, RMVPE…
    import torch
    if not getattr(torch.load, '_banc', False):
        charger = torch.load

        def charger_complet(*a, **k):
            k.setdefault('weights_only', False)
            return charger(*a, **k)
        charger_complet._banc = True
        torch.load = charger_complet
    return torch


def mono(x):
    return x.mean(1).astype('float32') if x.ndim == 2 else x.astype('float32')


def rms_actif(x, ref=None):
    """RMS (linéaire) sur les trames où la piste de référence (par défaut x) dépasse son seuil de silence."""
    import numpy as np
    r = x if ref is None else ref
    env = enveloppe_db(r)
    lisse = lisser_db(env)
    act = lisse > seuil_silence(env)
    e = enveloppe_db(x)
    n = min(len(e), len(act))
    if not act[:n].any():
        return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-9)
    return float(np.sqrt(np.mean(10 ** (e[:n][act[:n]] / 10))) + 1e-9)


# --- Orchestration ---------------------------------------------------------------------------------------------------

def orchestrer():
    session = {'session': B.get('session'), 'debut_epoch': DEBUT, 'etapes': []}
    verifier_acces()
    outils = trouver_outils()
    fiche = lire_outils(outils)
    log(f"outils : {'montage ' + outils + ' du ' + str(fiche.get('date')) if outils else 'aucun montage, tout par internet'}")
    t = time.time()
    installer(outils)
    session['seedvc'] = cloner(outils)
    poids_depuis_outils(outils)
    # train.py (affinage) : mêmes corrections que inference.py pour transformers 5 ; OpenVoice (perturbation de timbre
    # de l'affinage) : stft réel retiré des versions récentes de torch, remplacé par le stft complexe équivalent
    corriger(os.path.join(SEEDVC, 'train.py'), [('head_mask=None,', '')])
    corriger(os.path.join(SEEDVC, 'modules/openvoice/mel_processing.py'),
             [('return_complex=False,\n    )\n\n    spec = torch.sqrt(spec.pow(2).sum(-1) + 1e-6)',
               'return_complex=True,\n    )\n\n    spec = torch.sqrt(torch.view_as_real(spec).pow(2).sum(-1) + 1e-6)')])
    precharger(outils, session)
    session['installation_s'] = round(time.time() - t, 1)
    copies = []
    for ex in B['extraits']:
        os.makedirs(os.path.join(TRAVAIL, ex), exist_ok=True)
        os.makedirs(os.path.join(PISTES, ex), exist_ok=True)
        for piste in PISTES_SEPAREES:
            dest = os.path.join(PISTES, ex, f'{piste}.wav')
            src = entree(f'stem-{ex}-{piste}.wav')
            if src and not os.path.exists(dest) and not any(e['etape'] == 'separer' for e in B['etapes']):
                shutil.copy2(src, dest)
                copies.append(dest)
    # sorties d'une session précédente (<session>-<extrait>-<fichier> dans le jeu) : rejugées avec les nouvelles versions
    for src in sorted(glob.glob('/kaggle/input/**/s[0-9]*-*', recursive=True)):
        m = re.match(r's\d+-([a-z]+)-(.+)$', os.path.basename(src))
        if m and m.group(1) in B['extraits']:
            dest = os.path.join(TRAVAIL, m.group(1), m.group(2))
            if not os.path.exists(dest):
                shutil.copy2(src, dest)
                copies.append(dest)
    if copies:
        log(f'voix séparées et sorties précédentes reprises du jeu de données : {len(copies)} fichiers')
    ecrire_json(os.path.join(TRAVAIL, 'session.json'), session)
    for etape in B['etapes']:
        if etape['etape'] == 'parallele':
            lancer_en_parallele(etape, session)
        else:
            session['etapes'].append(lancer_etape(etape))
        ecrire_json(os.path.join(TRAVAIL, 'session.json'), session)
    for c in copies:
        os.remove(c)
    session['total_s'] = round(time.time() - DEBUT, 1)
    ecrire_json(os.path.join(TRAVAIL, 'session.json'), session)
    log(f'MESURE temps total du noyau : {time.time() - DEBUT:.0f} s')


def lancer_etape(etape, env=None, prefixe=''):
    """Une étape dans son propre processus (le GPU est rendu à sa sortie), dans la limite de temps de la session."""
    nom = prefixe + etape.get('nom', etape['etape'])
    reste = B['limite_totale_s'] - (time.time() - DEBUT)
    if reste < etape.get('minimum_s', 120):
        log(f'ÉTAPE {nom} : sautée, il ne reste que {reste:.0f} s sur la limite de la session')
        return {'nom': nom, 'etape': etape['etape'], 'code': 'sautée'}
    log(f'ÉTAPE {nom} : début ({etape["etape"]})')
    t = time.time()
    try:
        r = subprocess.run([sys.executable, COMPLET, etape['etape'], json.dumps(etape)],
                           timeout=min(reste, etape.get('limite_s', reste)), env=env)
        code = r.returncode
    except subprocess.TimeoutExpired:
        code = 'limite de temps'
    d = round(time.time() - t, 1)
    log(f'ÉTAPE {nom} : fin, code {code}, {d:.0f} s')
    return {'nom': nom, 'etape': etape['etape'], 'code': code, 'duree_s': d, 'debut_s': round(t - DEBUT, 1)}


def nombre_cartes():
    """Cartes GPU de la machine (nvidia-smi -L), sans créer de contexte CUDA dans l'orchestrateur."""
    try:
        r = subprocess.run(['nvidia-smi', '-L'], capture_output=True, text=True, timeout=60)
        return max(1, sum(1 for l in r.stdout.splitlines() if l.startswith('GPU ')))
    except Exception:  # noqa: BLE001
        return 1


def lancer_en_parallele(etape, session):
    """etape['cartes'] : une suite d'étapes par carte, chaque suite dans l'ordre sur sa carte (CUDA_VISIBLE_DEVICES), les
    suites en même temps. Moins de cartes que de suites : toutes les suites l'une après l'autre sur la carte 0."""
    import threading
    suites = etape['cartes']
    n = nombre_cartes()
    if n < len(suites):
        log(f'PARALLELE {n} carte(s) pour {len(suites)} suites : tout sur la carte 0, à la suite')
        suites = [[e for suite in suites for e in suite]]
    verrou = threading.Lock()

    def suite(k, liste):
        env = {**os.environ, 'CUDA_VISIBLE_DEVICES': str(k)} if len(suites) > 1 else None
        for e in liste:
            r = lancer_etape(e, env, f'[carte {k}] ' if len(suites) > 1 else '')
            r['carte'] = k
            with verrou:
                session['etapes'].append(r)
                ecrire_json(os.path.join(TRAVAIL, 'session.json'), session)
    fils = [threading.Thread(target=suite, args=(k, liste)) for k, liste in enumerate(suites)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()


def precharger(outils, session):
    """Ce que plusieurs cartes feraient en même temps au même endroit, fait une fois avant les étapes
    (REGLAGES['precharger']) : installation de YingMusic-SVC (noyau du dépôt : depuis le montage d'outils), modèles
    audio-separator absents du montage (téléchargés ici), copie de Whisper du montage sur le disque local (dans un fil :
    le juge attend PAROLES_LOCALES, sinon il le télécharge)."""
    pre = B.get('precharger') or {}
    if pre.get('yingmusic') and _INSTALLER_YING_NOYAU is not None:
        try:
            session['yingmusic_installation'] = _INSTALLER_YING_NOYAU(outils)
        except Exception as e:  # noqa: BLE001 — chaque étape réessaiera
            log(f'PRECHARGEMENT YingMusic-SVC : {e!r}')
    for nom in pre.get('modeles_sep') or []:
        if os.path.exists(os.path.join('/tmp/modeles_sep', nom)):
            continue
        t0 = time.time()
        r = subprocess.run([sys.executable, '-c', 'from audio_separator.separator import Separator; '
                            f"Separator(model_file_dir='/tmp/modeles_sep', output_dir='/tmp').load_model(model_filename='{nom}')"])
        log(f'PRECHARGEMENT {nom} : code {r.returncode}, {time.time() - t0:.0f} s')
    if pre.get('whisper') and outils and 'copier_paroles' in globals():
        import threading
        etat = copier_paroles(outils)
        if etat.get('fil') is not None:
            def fin_copie():
                etat['fil'].join()
                ecrire_json(PAROLES_LOCALES, {**chemins_paroles(etat, attendre=False)[0], 'duree_s': etat.get('duree_s'),
                                              'erreur': etat.get('erreur')})
            open(PAROLES_LOCALES + '.attendu', 'w').close()
            threading.Thread(target=fin_copie, daemon=True).start()


# --- Séparation : voix principale / chœurs, puis reverb retirée ------------------------------------------------------

def etape_separer(a):
    import numpy as np
    import soundfile as sf
    from audio_separator.separator import Separator
    tmp = '/tmp/sep_banc'
    os.makedirs(tmp, exist_ok=True)
    rapport = {'modeles': {}, 'extraits': {}}

    def separateur(nom):
        t0 = time.time()
        s = Separator(model_file_dir='/tmp/modeles_sep', output_dir=tmp, output_format='WAV', use_soundfile=True,
                      normalization_threshold=1.0, sample_rate=44100,
                      mdxc_params={'segment_size': 256, 'override_model_segment_size': False, 'batch_size': 1,
                                   'overlap': a.get('overlap', 8), 'pitch_shift': 0})
        s.load_model(model_filename=nom)
        rapport['modeles'][nom] = {'chargement_s': round(time.time() - t0, 1)}
        log(f'SEPARATION modèle {nom} chargé en {time.time() - t0:.0f} s')
        return s

    def separer(s, x, motif, etiquette):
        """Pic ramené à 0,9 (comme doubler), séparation, remise à l'échelle ; rend la piste dont le nom suit motif."""
        for f in glob.glob(f'{tmp}/*.wav'):
            os.remove(f)
        pic = float(np.abs(x).max()) or 1.0
        g = 0.9 / pic
        fichier = f'{tmp}/{etiquette}.wav'
        sf.write(fichier, x * g, 44100, subtype='FLOAT')
        t0 = time.time()
        sorties = s.separate(fichier)
        sorties = [p if os.path.isabs(p) else os.path.join(tmp, p) for p in sorties]
        noms = [os.path.basename(p) for p in sorties]
        choisis = [p for p in sorties if re.search(motif, os.path.basename(p), re.I)]
        if len(choisis) != 1:
            raise RuntimeError(f'{etiquette} : pistes {noms}, motif {motif} : {len(choisis)} correspondance(s)')
        y = sf.read(choisis[0], dtype='float32', always_2d=True)[0]
        out = np.zeros_like(x)
        n = min(len(y), len(x))
        out[:n] = y[:n, :x.shape[1]] / g
        log(f'SEPARATION {etiquette} : {noms} -> {os.path.basename(choisis[0])} ({time.time() - t0:.1f} s)')
        return out, {'pistes': noms, 'gardee': os.path.basename(choisis[0]), 'duree_s': round(time.time() - t0, 1)}

    db = lambda v: round(20 * float(np.log10(v + 1e-12)), 1)
    voix = {}
    for ex in B['extraits']:
        x = sf.read(piste_chemin(ex, 'voix'), dtype='float32', always_2d=True)[0]
        voix[ex] = np.ascontiguousarray(x[:, :2] if x.shape[1] >= 2 else np.repeat(x, 2, axis=1))
        rapport['extraits'][ex] = {}
    s = None
    for nom in ([a['karaoke']] if isinstance(a['karaoke'], str) else a['karaoke']):
        try:
            s = separateur(nom)
            rapport['karaoke'] = nom
            break
        except Exception as e:  # noqa: BLE001
            log(f'SEPARATION {nom} : chargement impossible ({e!r}), modèle suivant')
    if s is None:
        raise RuntimeError('aucun modèle karaoké utilisable')
    lead, choeurs = {}, {}
    for ex, x in voix.items():
        lead[ex], info = separer(s, x, a.get('motif_karaoke', r'\((vocals|lead|lead vocals|karaoke)\)'), f'{ex}_karaoke')
        choeurs[ex] = x - lead[ex]   # chœurs = tout ce qui n'est pas la voix principale (reverb et fuites comprises)
        rapport['extraits'][ex]['karaoke'] = info
    del s
    torch = charger_torch()
    torch.cuda.empty_cache()
    s = separateur(a['dereverb'])
    for ex in voix:
        d = os.path.join(PISTES, ex)
        os.makedirs(d, exist_ok=True)
        lead_sec, i1 = separer(s, lead[ex], a.get('motif_dereverb', r'\((noreverb|no reverb|no_reverb|dry)\)'), f'{ex}_lead_dereverb')
        ch_sec, i2 = separer(s, choeurs[ex], a.get('motif_dereverb', r'\((noreverb|no reverb|no_reverb|dry)\)'), f'{ex}_choeurs_dereverb')
        for nom, y in (('lead', lead[ex]), ('choeurs', choeurs[ex]), ('lead_sec', lead_sec), ('choeurs_sec', ch_sec)):
            sf.write(os.path.join(d, f'{nom}.wav'), y, 44100, subtype='FLOAT')
        ref = mono(voix[ex])
        niv = {nom: db(rms_actif(mono(y), ref)) for nom, y in (('voix', voix[ex]), ('lead', lead[ex]), ('choeurs', choeurs[ex]),
                                                                ('lead_sec', lead_sec), ('choeurs_sec', ch_sec),
                                                                ('reverb_jetee', voix[ex] - lead_sec - ch_sec))}
        rapport['extraits'][ex].update({'dereverb_lead': i1, 'dereverb_choeurs': i2, 'niveaux_db': niv})
        log(f'SEPARATION {ex} : niveaux (dBFS, trames où la voix sonne) {json.dumps(niv)}')
    ecrire_json(os.path.join(TRAVAIL, 'separation.json'), rapport)


# --- Banc vitesse : variantes de précision et de recouvrement des Roformer ------------------------------------------

# Précisions d'audio-separator 0.48.0 (Separator : use_autocast, use_native_fp16, use_torch_compile ; le fp16 natif et
# torch.compile ne s'appliquent qu'aux Roformer sur CUDA, sinon fp32 avec un avertissement : effective_precision le dit)
PRECISIONS = {'fp32': {}, 'autocast': {'use_autocast': True}, 'fp16': {'use_native_fp16': True},
              'autocast+compile': {'use_autocast': True, 'use_torch_compile': True},
              'fp16+compile': {'use_native_fp16': True, 'use_torch_compile': True}}
# Piste gardée : le premier motif qui désigne une seule piste (un karaoké peut rendre « Vocals » et « karaoke », celle-ci
# étant alors la musique avec les chœurs)
MOTIF_VOIX = (r'\((vocals|vocal|voice)\)',)
MOTIF_KARAOKE_BANC = (r'\((lead|lead vocals|lead_vocals)\)', r'\((vocals|vocal)\)', r'\(karaoke\)')
MOTIF_DEREVERB_BANC = (r'\((noreverb|no reverb|no_reverb|dry)\)',)
KARAOKE_BANC = 'mel_band_roformer_karaoke_becruily.ckpt'
DEREVERB_BANC = 'dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt'


def sdr_db(ref, est):
    """Rapport signal / erreur (dB) d'une piste à sa référence : 10 log10(Σ ref² / Σ (ref - est)²), sur la longueur
    commune ; 200 dB pour deux pistes identiques."""
    import numpy as np
    n = min(len(ref), len(est))
    r, e = np.asarray(ref[:n], np.float64), np.asarray(est[:n], np.float64)
    num, den = float((r ** 2).sum()), float(((r - e) ** 2).sum())
    if den <= 0:
        return 200.0
    return round(10 * math.log10(max(num, 1e-30) / den), 2)


def memoire_max_mo(torch):
    try:
        return round(torch.cuda.max_memory_allocated() / 2 ** 20)
    except Exception:  # noqa: BLE001
        return None


def deux_canaux(x):
    import numpy as np
    return np.ascontiguousarray(x[:, :2] if x.shape[1] >= 2 else np.repeat(x, 2, axis=1))


def separateur_banc(nom, v, tmp, segment, override):
    """Separator d'audio-separator pour la variante v (precision, recouvrement), modèle chargé ; rend aussi la précision
    réellement appliquée (effective_precision, effective_torch_compile) et le temps de chargement."""
    from audio_separator.separator import Separator
    t0 = time.time()
    s = Separator(model_file_dir='/tmp/modeles_sep', output_dir=tmp, output_format='WAV', use_soundfile=True,
                  normalization_threshold=1.0, sample_rate=44100, **PRECISIONS[v.get('precision', 'fp32')],
                  mdxc_params={'segment_size': segment, 'override_model_segment_size': override, 'batch_size': 1,
                               'overlap': int(v.get('recouvrement', 4)), 'pitch_shift': 0})
    s.load_model(model_filename=nom)
    mi = getattr(s, 'model_instance', None)
    return s, {'modele': nom, 'chargement_s': round(time.time() - t0, 1),
               'precision_effective': getattr(mi, 'effective_precision', None),
               'compile_effectif': getattr(mi, 'effective_torch_compile', None)}


def separer_banc(s, x, tmp, etiquette, motifs):
    """Comme le noyau : pic ramené à 0,9, séparation, remise à l'échelle. Rend (piste gardée, durée de la séparation
    seule en s, noms des pistes) ; piste gardée : celle du premier des motifs qui n'en désigne qu'une."""
    import numpy as np
    import soundfile as sf
    for f in glob.glob(f'{tmp}/*.wav'):
        os.remove(f)
    pic = float(np.abs(x).max()) or 1.0
    g = 0.9 / pic
    fichier = f'{tmp}/{etiquette}.wav'
    sf.write(fichier, x * g, 44100, subtype='FLOAT')
    t0 = time.time()
    sorties = s.separate(fichier)
    duree = time.time() - t0
    sorties = [p if os.path.isabs(p) else os.path.join(tmp, p) for p in sorties]
    noms = [os.path.basename(p) for p in sorties]
    choisis = []
    for motif in motifs:
        choisis = [p for p in sorties if re.search(motif, os.path.basename(p), re.I)]
        if len(choisis) == 1:
            break
    if len(choisis) != 1:
        raise RuntimeError(f'{etiquette} : pistes {noms}, motifs {motifs} : aucune piste seule')
    y = sf.read(choisis[0], dtype='float32', always_2d=True)[0]
    out = np.zeros_like(x)
    n = min(len(y), len(x))
    out[:n] = y[:n, :x.shape[1]] / g
    for f in glob.glob(f'{tmp}/*.wav'):
        os.remove(f)
    return out, round(duree, 2), [*noms, '=> ' + os.path.basename(choisis[0])]


def chauffer(s, x, tmp, motif):
    """Une séparation de 12 s avant la mesure (noyaux CUDA, allocateur) : la mesure ne compte que le régime établi. Plus
    de 10 s, sans quoi audio-separator changerait la taille des segments (son court)."""
    t0 = time.time()
    separer_banc(s, x[:12 * SR], tmp, 'chauffe', motif)
    return round(time.time() - t0, 1)


def etape_separer_voix(a):
    """Séparation voix / reste des extraits (extrait-<ex>-original.wav) en plusieurs variantes, toutes avec le modèle
    et les segments du noyau (a['modele'], a['segment'], a['override']). Pour chaque variante : modèle chargé, chauffe,
    puis chaque extrait ; temps, mémoire du GPU au plus fort, SDR de la voix et du reste à la variante de référence
    (a['reference'], sinon la première). Voix de la référence : voix.wav (pistes) et voix.flac (sorties, pour le mix
    d'écoute) ; des autres : voix_<variante>.wav."""
    import gc
    import numpy as np
    import soundfile as sf
    torch = charger_torch()
    tmp = f'/tmp/sep_vitesse_{os.getpid()}'   # un dossier par processus : les deux cartes tournent en même temps
    os.makedirs(tmp, exist_ok=True)
    origs = {ex: deux_canaux(sf.read(entree(f'extrait-{ex}-original.wav'), dtype='float32', always_2d=True)[0])
             for ex in extraits_de(a)}
    ref_nom = a.get('reference') or a['variantes'][0]['nom']
    refs = {}
    res = {ex: {'modele': a['modele'], 'reference': ref_nom, 'duree_s': round(len(x) / SR, 2), 'variantes': {}}
           for ex, x in origs.items()}
    for v in sorted(a['variantes'], key=lambda v: v['nom'] != ref_nom):   # la référence d'abord
        nom = v['nom']
        try:
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
            s, info = separateur_banc(v.get('modele', a['modele']), v, tmp, a.get('segment', 801), a.get('override', True))
            info['chauffe_s'] = chauffer(s, next(iter(origs.values())), tmp, MOTIF_VOIX)
        except Exception as e:  # noqa: BLE001 — variante impossible : les autres continuent
            import traceback
            traceback.print_exc()
            for ex in origs:
                res[ex]['variantes'][nom] = {'erreur': repr(e)[:400]}
            continue
        for ex, x in origs.items():
            try:
                voix, duree, noms = separer_banc(s, x, tmp, f'{ex}_{nom}', MOTIF_VOIX)
                m = {**info, **{k: v[k] for k in ('precision', 'recouvrement') if k in v}, 'separation_s': duree,
                     'rtf': round(duree / (len(x) / SR), 3), 'memoire_gpu_max_mo': memoire_max_mo(torch), 'pistes': noms}
                if nom == ref_nom:
                    refs[ex] = voix
                    os.makedirs(os.path.join(PISTES, ex), exist_ok=True)
                    sf.write(os.path.join(PISTES, ex, 'voix.wav'), voix, SR, subtype='FLOAT')
                    sf.write(os.path.join(TRAVAIL, ex, 'voix.flac'), np.clip(voix, -1, 1), SR, subtype='PCM_24')
                else:
                    sf.write(piste_chemin(ex, f'voix@{nom}'), voix, SR, subtype='FLOAT')
                if ex in refs:
                    m['sdr_voix_db'] = sdr_db(refs[ex], voix)
                    m['sdr_reste_db'] = sdr_db(x - refs[ex], x - voix)
                res[ex]['variantes'][nom] = m
                log(f'VITESSE séparation {ex} {nom} : {json.dumps(m, ensure_ascii=False)}')
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                res[ex]['variantes'][nom] = {**info, 'erreur': repr(e)[:400]}
            ecrire_json(os.path.join(TRAVAIL, ex, 'separation_voix.json'), res[ex])
        del s
        gc.collect()


def etape_nettoyer_variantes(a):
    """Nettoyage des extraits (voix séparée de référence, ou a['source']) en plusieurs variantes, comme le noyau :
    karaoké (voix principale ; chœurs = voix - voix principale, en numpy), puis dereverb de la voix principale et des
    chœurs. Variante : precision, recouvrement, et facultativement karaoke / dereverb (un autre modèle). Pour chaque
    variante et chaque extrait : temps de chaque passe (chauffe à part), mémoire du GPU, SDR de la voix principale, de la
    voix principale sèche et des chœurs secs à la variante de référence (a['reference'], sinon la première).
    Pistes : lead_sec.wav et choeurs_sec.wav pour la référence, lead_sec_<variante>.wav… pour les autres."""
    import gc
    import soundfile as sf
    torch = charger_torch()
    tmp = f'/tmp/net_vitesse_{os.getpid()}'   # un dossier par processus : les deux cartes tournent en même temps
    os.makedirs(tmp, exist_ok=True)
    source = a.get('source', 'voix')
    voix = {ex: deux_canaux(sf.read(piste_chemin(ex, source), dtype='float32', always_2d=True)[0]) for ex in extraits_de(a)}
    ref_nom = a.get('reference') or a['variantes'][0]['nom']
    refs = {ex: {} for ex in voix}
    res = {ex: {'source': source, 'reference': ref_nom, 'duree_s': round(len(x) / SR, 2), 'variantes': {}}
           for ex, x in voix.items()}
    voix_defaut = voix
    for v in sorted(a['variantes'], key=lambda v: v['nom'] != ref_nom):
        nom = v['nom']
        suffixe = '' if nom == ref_nom else f'@{nom}'
        # une variante peut partir d'une autre voix séparée (p. ex. « voix@H2 » : séparation et nettoyage rapides
        # ensemble) ; ses SDR restent pris à la référence, de bout en bout
        voix = ({ex: deux_canaux(sf.read(piste_chemin(ex, v['source']), dtype='float32', always_2d=True)[0])
                 for ex in voix_defaut} if v.get('source') else voix_defaut)
        par_ex = {ex: {k: v[k] for k in ('precision', 'recouvrement', 'karaoke', 'dereverb', 'source') if k in v} for ex in voix}
        leads = {}
        for passe, modele, motif in (('karaoke', v.get('karaoke', a.get('karaoke', KARAOKE_BANC)), MOTIF_KARAOKE_BANC),
                                     ('dereverb', v.get('dereverb', a.get('dereverb', DEREVERB_BANC)), MOTIF_DEREVERB_BANC)):
            try:
                torch.cuda.empty_cache()
                torch.cuda.reset_peak_memory_stats()
                s, info = separateur_banc(modele, v, tmp, a.get('segment', 256), a.get('override', False))
                info['chauffe_s'] = chauffer(s, next(iter(voix.values())), tmp, motif)
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                for ex in voix:
                    par_ex[ex]['erreur'] = f'{passe} : {e!r}'[:400]
                break
            for ex, x in voix.items():
                m = par_ex[ex]
                if m.get('erreur'):
                    continue
                try:
                    m[passe] = info
                    if passe == 'karaoke':
                        lead, d, noms_k = separer_banc(s, x, tmp, f'{ex}_{nom}_karaoke', motif)
                        leads[ex] = lead
                        m['durees_s'] = {'karaoke': d}
                        m['pistes_karaoke'] = noms_k
                        if nom == ref_nom:
                            refs[ex]['lead'] = lead
                        elif 'lead' in refs[ex]:
                            m['sdr_db'] = {'lead': sdr_db(refs[ex]['lead'], lead)}
                    else:
                        for piste, y_in in (('lead', leads[ex]), ('choeurs', x - leads[ex])):
                            y, d, _ = separer_banc(s, y_in, tmp, f'{ex}_{nom}_{piste}_dereverb', motif)
                            m['durees_s'][f'dereverb_{piste}'] = d
                            cle = f'{piste}_sec'
                            sf.write(piste_chemin(ex, cle + suffixe), y, SR, subtype='FLOAT')
                            if nom == ref_nom:
                                refs[ex][cle] = y
                            elif cle in refs[ex]:
                                m.setdefault('sdr_db', {})[cle] = sdr_db(refs[ex][cle], y)
                        m['nettoyage_s'] = round(sum(m['durees_s'].values()), 2)
                        m['rtf'] = round(m['nettoyage_s'] / (len(x) / SR), 3)
                    m.setdefault('memoire_gpu_max_mo', {})[passe] = memoire_max_mo(torch)
                except Exception as e:  # noqa: BLE001
                    import traceback
                    traceback.print_exc()
                    m['erreur'] = f'{passe} : {e!r}'[:400]
            del s
            gc.collect()
        for ex in voix:
            res[ex]['variantes'][nom] = par_ex[ex]
            log(f'VITESSE nettoyage {ex} {nom} : {json.dumps(par_ex[ex], ensure_ascii=False)}')
            ecrire_json(os.path.join(TRAVAIL, ex, 'nettoyage.json'), res[ex])


# --- Banc vitesse : versions tirées d'une autre, sans GPU (égalisation du timbre) -----------------------------------

def spectre_actif(x, sr=SR):
    """Spectre de puissance moyen (STFT de 4096) des trames actives de x (à moins de 35 dB du 95e centile d'énergie)."""
    import numpy as np
    import scipy.signal
    f, _, z = scipy.signal.stft(np.asarray(x, np.float64), fs=sr, nperseg=4096, noverlap=3072)
    p = np.abs(z) ** 2
    e = p.sum(0)
    act = 10 * np.log10(e + 1e-20) > 10 * np.log10(np.percentile(e, 95) + 1e-20) - 35
    return f, (p[:, act].mean(1) if act.any() else p.mean(1))


def bandes_tiers_octave(x, sr=SR, fmin=100.0, fmax=12500.0):
    """Niveaux (dB) des bandes de tiers d'octave du spectre actif de x, de fmin à fmax."""
    import numpy as np
    f, p = spectre_actif(x, sr)
    centres = fmin * 2 ** (np.arange(0, round(math.log2(fmax / fmin) * 3) + 1) / 3)
    return np.array([10 * math.log10(float(p[(f >= c * 2 ** (-1 / 6)) & (f < c * 2 ** (1 / 6))].mean()) + 1e-20)
                     for c in centres])


def ecart_spectral_db(x, ref, sr=SR):
    """Écart moyen (dB) des bandes de tiers d'octave de x à celles de ref (100 Hz - 12,5 kHz), niveaux globaux ramenés
    l'un sur l'autre : 0 = même équilibre spectral (même « couleur »)."""
    import numpy as np
    a, b = bandes_tiers_octave(x, sr), bandes_tiers_octave(ref, sr)
    d = a - b
    return round(float(np.mean(np.abs(d - np.median(d)))), 2)


def courbe_egalisation(src, conv, lissage_oct=1.0, max_db=6.0, sr=SR):
    """Correction (dB par case de STFT) qui rapproche l'équilibre spectral moyen de conv de celui de src : spectres
    actifs lissés sur lissage_oct octave, différence centrée (niveau global inchangé, pondéré par l'énergie de conv entre
    100 Hz et 10 kHz), bornée à ±max_db ; nulle sous 60 Hz, ramenée à 0 de 16 à 20 kHz."""
    import numpy as np
    f, ps = spectre_actif(src, sr)
    _, pc = spectre_actif(conv, sr)
    lf = np.log2(np.maximum(f, 20.0))
    sigma = lissage_oct / 2.355   # largeur à mi-hauteur = lissage_oct octave
    w = np.exp(-0.5 * ((lf[:, None] - lf[None, :]) / sigma) ** 2)
    w /= w.sum(1, keepdims=True)
    d = 10 * np.log10(w @ ps + 1e-20) - 10 * np.log10(w @ pc + 1e-20)
    bande = (f >= 100) & (f <= 10000)
    poids = pc[bande] / (pc[bande].sum() + 1e-20)
    d = np.clip(d - float((d[bande] * poids).sum()), -max_db, max_db)
    d[f < 60] = 0.0
    haut = f > 16000
    d[haut] *= np.clip((20000 - f[haut]) / 4000, 0, 1)
    return f, d


def appliquer_egalisation(x, d_db, sr=SR):
    """Gain par case de STFT (phase nulle), reconstruction par recouvrement-addition ; même longueur que x."""
    import numpy as np
    import scipy.signal
    _, _, z = scipy.signal.stft(np.asarray(x, np.float64), fs=sr, nperseg=4096, noverlap=3072)
    _, y = scipy.signal.istft(z * (10 ** (d_db / 20))[:, None], fs=sr, nperseg=4096, noverlap=3072)
    y = y[:len(x)] if len(y) >= len(x) else np.pad(y, (0, len(x) - len(y)))
    return y.astype(np.float32)


def etape_deriver(a):
    """Versions tirées d'une autre sans GPU : égalisation de chaque piste convertie de <de> (<de>_<piste>.flac, sinon
    <de>.flac) sur sa piste source sèche, même niveau, puis somme comme la version d'origine. La fiche <nom>.json
    reprend celle de <de> (type version : le juge la prend) avec la correction moyenne par bande."""
    import numpy as np
    import soundfile as sf
    for ex in extraits_de(a):
        for v in a['versions']:
            de = v['de']
            fiche = lire_json(os.path.join(TRAVAIL, ex, f'{de}.json'))
            if not fiche:
                log(f'DERIVER {ex} {v["nom"]} : {de} absente')
                continue
            pistes = list((fiche.get('pistes') or {}).keys())
            total, courbes = None, {}
            for piste in pistes:
                chemin = os.path.join(TRAVAIL, ex, f'{de}_{piste}.flac')
                y = sf.read(chemin if len(pistes) > 1 else os.path.join(TRAVAIL, ex, f'{de}.flac'), dtype='float32')[0]
                src = mono(sf.read(piste_chemin(ex, piste), dtype='float32', always_2d=True)[0])
                f, d = courbe_egalisation(src, y, v.get('lissage_oct', 1.0), v.get('max_db', 6.0))
                z = appliquer_egalisation(y, d)
                z = (z * (rms_actif(y) / max(rms_actif(z, y), 1e-9))).astype(np.float32)   # niveau de la piste d'origine
                courbes[piste] = {f'{lo}-{hi} Hz': round(float(d[(f >= lo) & (f < hi)].mean()), 2)
                                  for lo, hi in ((60, 250), (250, 1000), (1000, 4000), (4000, 8000), (8000, 16000))}
                if len(pistes) > 1:
                    sf.write(os.path.join(TRAVAIL, ex, f"{v['nom']}_{piste}.flac"), np.clip(z, -1, 1), SR, subtype='PCM_24')
                total = z if total is None else total + z
            crete = float(np.abs(total).max())
            if crete > 0.99:
                total *= 0.99 / crete
            sf.write(os.path.join(TRAVAIL, ex, f"{v['nom']}.flac"), total, SR, subtype='PCM_24')
            ecrire_json(os.path.join(TRAVAIL, ex, f"{v['nom']}.json"),
                        {**fiche, 'version': v['nom'], 'description': v.get('description'), 'derivee_de': de,
                         'egalisation': {'lissage_oct': v.get('lissage_oct', 1.0), 'max_db': v.get('max_db', 6.0),
                                         'moyenne_par_bande_db': courbes}})
            log(f"DERIVER {ex} {v['nom']} (de {de}) : {json.dumps(courbes)}")


# --- Moteurs de conversion -------------------------------------------------------------------------------------------

class MoteurSeedVC:
    """Seed-VC V1, modèle chant 44,1 kHz f0 (zéro-shot ou point de contrôle affiné) : boucle de inference.py, comme doubler."""
    type = 'seedvc'

    def __init__(self, checkpoint=None, config=None):
        self.torch = charger_torch()
        os.chdir(SEEDVC)
        sys.path.insert(0, SEEDVC)
        import inference as sv
        t0 = time.time()
        (self.model, self.semantic_fn, self.f0_fn, self.vocoder_fn, self.campplus, self.mel_fn,
         args) = sv.load_models(Namespace(f0_condition=True, checkpoint=checkpoint, config=config, fp16=B['fp16']))
        self.sr, self.hop, self.device = args['sampling_rate'], args['hop_size'], sv.device
        self.chargement_s = round(time.time() - t0, 1)

    def semantique(self, w16):
        torch = self.torch
        if w16.size(-1) <= 16000 * 30:
            return self.semantic_fn(w16)
        liste, tampon, fait = [], None, 0
        while fait < w16.size(-1):
            morceau = w16[:, fait:fait + 16000 * 30] if tampon is None else torch.cat([tampon, w16[:, fait:fait + 16000 * 25]], dim=-1)
            s = self.semantic_fn(morceau)
            liste.append(s if fait == 0 else s[:, 50 * 5:])
            tampon = morceau[:, -16000 * 5:]
            fait += 30 * 16000 if fait == 0 else morceau.size(-1) - 16000 * 5
        return torch.cat(liste, dim=1)

    def style(self, w16):
        import torchaudio
        feat = torchaudio.compliance.kaldi.fbank(w16, num_mel_bins=80, dither=0, sample_frequency=16000)
        return self.campplus((feat - feat.mean(dim=0, keepdim=True)).unsqueeze(0))

    def preparer_ref(self, chemin):
        import librosa
        import torchaudio
        torch = self.torch
        with torch.no_grad():
            ref = librosa.load(chemin, sr=self.sr)[0][:self.sr * 25]
            ref_t = torch.tensor(ref).unsqueeze(0).float().to(self.device)
            ref16 = torchaudio.functional.resample(ref_t, self.sr, 16000)
            mel2 = self.mel_fn(ref_t)
            style2 = self.style(ref16)
            f0_ref = torch.from_numpy(self.f0_fn(ref16[0], thred=0.03)).to(self.device)[None]
            sortie = self.length_regulator(self.semantique(ref16), mel2.size(2), f0_ref, style2)
        return {'mel2': mel2, 'style2': style2, 'prompt': sortie[0], 'style_prompt': sortie[1], 'duree': len(ref) / self.sr}

    def length_regulator(self, s, longueur, f0, style2):
        torch = self.torch
        r = self.model.length_regulator(s, ylens=torch.LongTensor([longueur]).to(self.device), n_quantizers=3, f0=f0)
        return r[0], None

    def inference(self, cat, ref, style_cat, pas):
        torch = self.torch
        return self.model.cfm.inference(cat, torch.LongTensor([cat.size(1)]).to(self.device), ref['mel2'], ref['style2'], None,
                                        pas, inference_cfg_rate=B['cfg'])

    def convertir_morceau(self, xs, ref, decalage, pas):
        """Un morceau (numpy 44,1 kHz) : copie de convertir() de doubler_kaggle.py, décalage fixe en demi-tons."""
        import numpy as np
        import torchaudio
        torch = self.torch
        with torch.no_grad():
            src = torch.from_numpy(np.ascontiguousarray(xs)).float().unsqueeze(0).to(self.device)
            src16 = torchaudio.functional.resample(src, SR, 16000)
            s_alt = self.semantique(src16)
            mel = self.mel_fn(src)
            f0 = torch.from_numpy(self.f0_fn(src16[0], thred=0.03)).to(self.device)[None]
            voise = f0 > 1
            f0_dec = torch.exp(torch.log(f0 + 1e-5))
            f0_dec[voise] = f0_dec[voise] * 2 ** (decalage / 12)
            cond, style_cond = self.length_regulator(s_alt, mel.size(2), f0_dec, ref['style2'])
            mel2 = ref['mel2']
            fenetre = self.sr // self.hop * 30 - mel2.size(2)
            recouvre, ov = 16, 16 * self.hop
            fait, morceaux, precedent = 0, [], None
            while fait < cond.size(1):
                bout = cond[:, fait:fait + fenetre]
                dernier = fait + fenetre >= cond.size(1)
                cat = torch.cat([ref['prompt'], bout], dim=1)
                style_cat = None if style_cond is None else torch.cat([ref['style_prompt'], style_cond[:, fait:fait + fenetre]], dim=1)
                with torch.autocast(device_type=self.device.type, dtype=torch.float16 if B['fp16'] else torch.float32):
                    cible = self.inference(cat, ref, style_cat, pas)
                    cible = cible[:, :, mel2.size(-1):]
                onde = self.vocoder_fn(cible.float()).squeeze()[None, :]
                if fait == 0:
                    if dernier:
                        morceaux.append(onde[0].cpu().numpy())
                        break
                    morceaux.append(onde[0, :-ov].cpu().numpy())
                else:
                    b = onde[0].cpu().numpy() if dernier else onde[0, :-ov].cpu().numpy()
                    fo = np.cos(np.linspace(0, np.pi / 2, ov)) ** 2
                    k = min(ov, len(b))
                    b[:k] = b[:k] * fo[::-1][:k] + (precedent * fo)[:k]
                    morceaux.append(b)
                    if dernier:
                        break
                precedent = onde[0, -ov:].cpu().numpy()
                fait += cible.size(2) - recouvre
            y = np.concatenate(morceaux).astype(np.float32)
        return y[:len(xs)] if len(y) >= len(xs) else np.pad(y, (0, len(xs) - len(y)))


class MoteurYing(MoteurSeedVC):
    """YingMusic-SVC (GiantAILab, déc. 2025) : même boucle, régulateur avec résidu de style f0 et cfm.inference(style_r)."""
    type = 'yingmusic'

    def __init__(self, checkpoint=None, config=None):
        self.torch = torch = charger_torch()
        installer_ying()
        os.chdir(YING)
        os.environ['HF_HUB_CACHE'] = './checkpoints/hf_cache'   # même cache que Seed-VC (lien), avant tout import de huggingface_hub
        sys.path = [p for p in sys.path if p != SEEDVC]
        sys.path.insert(0, YING)
        import yaml
        from huggingface_hub import hf_hub_download
        from modules.commons import recursive_munch, build_model, load_checkpoint
        from modules.rmvpe import RMVPE
        from modules.campplus.DTDNN import CAMPPlus
        from modules.bigvgan import bigvgan
        from modules.audio import mel_spectrogram
        from transformers import AutoFeatureExtractor, WhisperModel
        t0 = time.time()
        self.device = torch.device('cuda')
        checkpoint = checkpoint or hf_hub_download(repo_id=YING_POIDS[0], filename=YING_POIDS[1], cache_dir='./checkpoints')
        config = config or os.path.join(YING, 'configs', 'YingMusic-SVC.yml')
        cfg = yaml.safe_load(open(config))
        mp = recursive_munch(cfg['model_params'])
        mp.dit_type = 'DiT'
        self.model = build_model(mp, stage='DiT')
        self.model, _, _, _ = load_checkpoint(self.model, None, checkpoint, load_only_params=True, ignore_modules=[], is_distributed=False)
        for k in self.model:
            self.model[k].eval()
            self.model[k].to(self.device)
        self.model.cfm.estimator.setup_caches(max_batch_size=1, max_seq_length=8192)
        self.use_style_residual = bool(cfg['model_params']['length_regulator'].get('use_style_residual', False))
        rm = hf_hub_download(repo_id='lj1995/VoiceConversionWebUI', filename='rmvpe.pt', cache_dir='./checkpoints')
        self.f0_fn = RMVPE(rm, is_half=False, device=self.device).infer_from_audio
        cp = hf_hub_download(repo_id='funasr/campplus', filename='campplus_cn_common.bin', cache_dir='./checkpoints')
        self.campplus = CAMPPlus(feat_dim=80, embedding_size=192)
        self.campplus.load_state_dict(torch.load(cp, map_location='cpu'))
        self.campplus.eval().to(self.device)
        voc = bigvgan.BigVGAN.from_pretrained(mp.vocoder.name, use_cuda_kernel=False)
        voc.remove_weight_norm()
        self.vocoder_fn = voc.eval().to(self.device)
        nom_w = mp.speech_tokenizer.name
        wm = WhisperModel.from_pretrained(nom_w, dtype=torch.float16).to(self.device)
        del wm.decoder
        fe = AutoFeatureExtractor.from_pretrained(nom_w)

        def semantic_fn(w16):
            entrees = fe([w16.squeeze(0).cpu().numpy()], return_tensors='pt', return_attention_mask=True)
            feats = wm._mask_input_features(entrees.input_features, attention_mask=entrees.attention_mask).to(self.device)
            with torch.no_grad():
                sortie = wm.encoder(feats.to(wm.encoder.dtype), output_attentions=False, output_hidden_states=False, return_dict=True)
            return sortie.last_hidden_state.to(torch.float32)[:, :w16.size(-1) // 320 + 1]
        self.semantic_fn = semantic_fn
        sp = cfg['preprocess_params']['spect_params']
        args = {'n_fft': sp['n_fft'], 'win_size': sp['win_length'], 'hop_size': sp['hop_length'], 'num_mels': sp['n_mels'],
                'sampling_rate': cfg['preprocess_params']['sr'], 'fmin': sp.get('fmin', 0),
                'fmax': None if sp.get('fmax', 'None') == 'None' else 8000, 'center': False}
        self.mel_fn = lambda x: mel_spectrogram(x, **args)
        self.sr, self.hop = args['sampling_rate'], args['hop_size']
        self.chargement_s = round(time.time() - t0, 1)
        self.poids = checkpoint

    def length_regulator(self, s, longueur, f0, style2):
        torch = self.torch
        r = self.model.length_regulator(s, ylens=torch.LongTensor([longueur]).to(self.device), n_quantizers=3, f0=f0,
                                        style=style2, return_style_residual=True)
        return r[0], (r[5] if self.use_style_residual else None)

    def inference(self, cat, ref, style_cat, pas):
        torch = self.torch
        return self.model.cfm.inference(cat, torch.LongTensor([cat.size(1)]).to(self.device), ref['mel2'], ref['style2'], None,
                                        pas, inference_cfg_rate=B['cfg'], style_r=style_cat)


def installer_ying(outils=None):
    """YingMusic-SVC au commit figé ; ses poids auxiliaires (Whisper-small, BigVGAN 44 kHz, RMVPE, CAMPPlus) sont ceux de
    Seed-VC, déjà dans le montage : son dossier checkpoints pointe sur celui de Seed-VC (même cache Hugging Face).
    Noyau actuel du dépôt (base « depot ») : son installation à lui (archive du montage d'outils), que son MoteurYing
    appelle sous ce nom."""
    if _INSTALLER_YING_NOYAU is not None:
        return _INSTALLER_YING_NOYAU(outils if outils is not None else trouver_outils())
    if os.path.exists(os.path.join(YING, '.pret')):
        return
    t0 = time.time()
    shutil.rmtree(YING, ignore_errors=True)
    os.makedirs(YING)
    lancer('git', 'init', '-q', YING)
    lancer('git', '-C', YING, 'remote', 'add', 'origin', YING_DEPOT)
    lancer('git', '-C', YING, 'fetch', '-q', '--depth', '1', 'origin', YING_COMMIT)
    lancer('git', '-C', YING, 'checkout', '-q', 'FETCH_HEAD')
    corriger(os.path.join(YING, 'modules/bigvgan/bigvgan.py'),
                  [('proxies: Optional[Dict],', 'proxies: Optional[Dict] = None,'), ('resume_download: bool,', 'resume_download: bool = None,'),
                   ('proxies=proxies,', ''), ('resume_download=resume_download,', '')])
    if not os.path.exists(os.path.join(YING, 'checkpoints')):
        os.symlink(os.path.join(SEEDVC, 'checkpoints'), os.path.join(YING, 'checkpoints'))
    open(os.path.join(YING, '.pret'), 'w').close()
    log(f'YingMusic-SVC {YING_COMMIT[:10]} installé en {time.time() - t0:.0f} s')


def convertir_piste(m, ref, x, decalage, pas, graine=None):
    """Même chaîne que traiter() de doubler_kaggle.py : découpage aux silences de la piste source (morceaux de 8 s au
    plus), 0,25 s de marge, fondus aux coupes, porte sur les silences. Décalage fixe pour tout l'extrait. graine : celle
    des réglages par défaut (le morceau i prend graine + i)."""
    import numpy as np
    torch = m.torch
    graine = B['graine'] if graine is None else graine
    n = len(x)
    env = enveloppe_db(x)
    lisse = lisser_db(env)
    seuil = seuil_silence(env)
    pad, fondu = int(B['pad_s'] * SR), int(B['fondu_s'] * SR)
    place = SR // 512 * 30 - ref['mel2'].size(2)
    coeur_max = place * 512 - 2 * pad - int(0.1 * SR)
    coupes = decouper(lisse, n, coeur_max, min(int(2 * SR), coeur_max // 2), seuil)
    fondus = {c: fondu if lisse[c // PAS] < seuil else int(0.2 * SR) for c in coupes[1:-1]}
    actif = (lisse > seuil).astype(np.float64)
    actif = (np.convolve(actif, np.ones(41), mode='same') > 0).astype(np.float64)
    gain_trames = np.convolve(actif, np.ones(5) / 5, mode='same')
    gain = np.interp(np.arange(n), np.arange(len(gain_trames)) * PAS, gain_trames).astype(np.float32)
    sortie = np.zeros(n, np.float32)
    convertis, morceaux = 0, 0
    for i, (a, b) in enumerate(zip(coupes[:-1], coupes[1:])):
        if float(lisse[a // PAS:b // PAS + 1].max()) < seuil:
            continue
        lo, hi = max(0, a - pad), min(n, b + pad)
        torch.manual_seed(graine + i)
        y = m.convertir_morceau(x[lo:hi], ref, decalage, pas)
        tt = np.arange(lo, hi)
        w = np.ones(hi - lo)
        if a > 0:
            w *= rampe(tt - a, fondus[a])
        if b < n:
            w *= 1 - rampe(tt - b, fondus[b])
        sortie[lo:hi] += (y * w).astype(np.float32)
        convertis += b - a
        morceaux += 1
    return sortie * gain, {'morceaux': morceaux, 'secondes_converties': round(convertis / SR, 2), 'coeur_max_s': round(coeur_max / SR, 2)}


def chronometrer(m):
    """Temps de GPU de chaque composant de la conversion (encodeur Whisper, RMVPE, diffusion, vocodeur), CUDA synchronisé
    autour de chaque appel. Rend (temps, rétablir) : rétablir() remet les fonctions d'origine."""
    torch = m.torch
    temps, originaux = {}, []

    def envelopper(attr, cle):
        f = getattr(m, attr)
        originaux.append((attr, f, attr in vars(m)))

        def g(*a, **k):
            torch.cuda.synchronize()
            t0 = time.time()
            r = f(*a, **k)
            torch.cuda.synchronize()
            temps[cle] = temps.get(cle, 0.0) + time.time() - t0
            return r
        setattr(m, attr, g)
    for attr, cle in (('semantic_fn', 'whisper_s'), ('f0_fn', 'rmvpe_s'), ('inference', 'diffusion_s'),
                      ('vocoder_fn', 'vocodeur_s'), ('mel_fn', 'mel_s'), ('length_regulator', 'regulateur_s')):
        envelopper(attr, cle)

    def retablir():
        for attr, f, propre in reversed(originaux):
            if propre:
                setattr(m, attr, f)
            else:
                delattr(m, attr)
    return temps, retablir


def etape_convertir(a):
    """Conversion des pistes de chaque version. Moteur : seedvc, yingmusic (classes du banc) ou yingmusic_noyau (la
    classe MoteurYing du noyau actuel du dépôt, poids du montage d'outils : la conversion de doubler, telle quelle).
    Par version, facultatif : pas, graine, reference (un fichier du jeu, préparé une fois), extraits, limite_s (seulement
    le début des pistes), profil (temps de chaque composant), vocodeur_fp16 (BigVGAN sous autocast fp16)."""
    import numpy as np
    import soundfile as sf
    mot = a['moteur']
    ck = mot.get('checkpoint')
    if ck and not os.path.isabs(ck):
        ck = os.path.join(TRAVAIL, ck) if os.path.exists(os.path.join(TRAVAIL, ck)) else entree(os.path.basename(ck))
    cfg = mot.get('config')
    if cfg and not os.path.isabs(cfg):
        cfg = os.path.join(TRAVAIL, cfg) if os.path.exists(os.path.join(TRAVAIL, cfg)) else entree(os.path.basename(cfg))
    if mot['type'] == 'yingmusic_noyau':
        outils = trouver_outils()
        m = _MOTEUR_YING_NOYAU(outils, ck or poids_ying(outils))
    else:
        m = (MoteurYing if mot['type'] == 'yingmusic' else MoteurSeedVC)(ck, cfg)
    log(f"CONVERSION moteur {mot['type']} {ck or 'zéro-shot'} chargé en {m.chargement_s:.0f} s")
    torch = m.torch
    refs = {}

    def reference(nom):
        if nom not in refs:
            t0 = time.time()
            refs[nom] = m.preparer_ref(entree(nom))
            log(f'CONVERSION référence {nom} préparée en {time.time() - t0:.1f} s ({refs[nom]["duree"]:.2f} s)')
        return refs[nom]
    ref_defaut = B.get('reference', f'{CIBLE}-reference-A.wav')
    reference(ref_defaut)
    extraits = extraits_de(a)
    for v in a['versions']:
        for ex, e in extraits.items():
            if v.get('extraits') and ex not in v['extraits']:
                continue
            t0 = time.time()
            pas = v.get('pas', B['pas'])
            ref = reference(v.get('reference') or ref_defaut)
            temps_profil, retablir = chronometrer(m) if v.get('profil') else (None, None)
            vocodeur = None
            if v.get('vocodeur_fp16'):
                vocodeur = m.vocoder_fn

                def vocodeur16(c, f=vocodeur):
                    with torch.autocast(device_type='cuda', dtype=torch.float16):
                        return f(c).float()
                m.vocoder_fn = vocodeur16
            fiche = {'type': 'version', 'version': v['nom'], 'description': v.get('description'), 'moteur': mot['type'],
                     'checkpoint': os.path.basename(ck) if ck else None, 'pas': pas, 'cfg': B['cfg'], 'decalage_dt': e['decalage'],
                     'graine': v.get('graine', B['graine']), 'reference': v.get('reference') or ref_defaut,
                     'reference_s': round(float(ref['duree']), 2), 'vocodeur_fp16': bool(v.get('vocodeur_fp16')),
                     'pistes': {}}
            total = None
            for piste in v['pistes']:
                x2 = sf.read(piste_chemin(ex, piste), dtype='float32', always_2d=True)[0]
                x = mono(x2)
                if v.get('limite_s'):
                    x = x[:int(v['limite_s'] * SR)]
                torch.cuda.reset_peak_memory_stats()
                t1 = time.time()
                y, info = convertir_piste(m, ref, x, e['decalage'], pas, v.get('graine'))
                duree = time.time() - t1
                info['memoire_gpu_max_mo'] = memoire_max_mo(torch)
                # niveau : la piste convertie reprend celui de sa source (équilibre voix principale / chœurs d'origine)
                g = rms_actif(x) / max(rms_actif(y, x), 1e-9)
                y = (y * g).astype(np.float32)
                if len(v['pistes']) > 1:
                    sf.write(os.path.join(TRAVAIL, ex, f"{v['nom']}_{piste}.flac"), np.clip(y, -1, 1), SR, subtype='PCM_24')
                total = y if total is None else total + y
                fiche['pistes'][piste] = {**info, 'conversion_s': round(duree, 1), 'gain_niveau_db': round(20 * np.log10(g), 2),
                                          'rtf': round(duree / max(info['secondes_converties'], 1e-3), 3)}
                torch_ = m.torch
                torch_.cuda.empty_cache()
            if retablir:
                retablir()
                fiche['profil_s'] = {k: round(t, 2) for k, t in sorted(temps_profil.items())}
            if vocodeur is not None:
                m.vocoder_fn = vocodeur
            crete = float(np.abs(total).max())
            if crete > 0.99:   # garde-fou : jamais d'écrêtage dans le fichier (le mix renormalise de toute façon)
                total *= 0.99 / crete
            sf.write(os.path.join(TRAVAIL, ex, f"{v['nom']}.flac"), total, SR, subtype='PCM_24')
            fiche.update({'conversion_s': round(sum(p['conversion_s'] for p in fiche['pistes'].values()), 1),
                          'total_s': round(time.time() - t0, 1), 'chargement_moteur_s': m.chargement_s, 'crete': round(crete, 3)})
            if v.get('profil') or v.get('limite_s'):
                fiche['type'] = 'mesure'   # pas une version à juger ni à écouter
            ecrire_json(os.path.join(TRAVAIL, ex, f"{v['nom']}.json"), fiche)
            log(f"CONVERSION {ex} {v['nom']} : {json.dumps(fiche, ensure_ascii=False)}")


# --- Affinage de Seed-VC sur la parole de la voix cible -------------------------------------------------------------

def etape_entrainer(a):
    os.chdir(SEEDVC)
    sys.path.insert(0, SEEDVC)
    os.environ['HF_HUB_CACHE'] = './checkpoints/hf_cache'
    torch = charger_torch()
    donnees = '/tmp/donnees_ft'
    shutil.rmtree(donnees, ignore_errors=True)
    os.makedirs(donnees)
    fichiers = sorted(glob.glob(f'/kaggle/input/**/{CIBLE}-ft-*.wav', recursive=True))
    for f in fichiers:
        shutil.copy2(f, donnees)
    assert fichiers and not glob.glob(f'{donnees}/{CIBLE}-tenue*'), 'données d\'affinage absentes ou prise de mesure mêlée'
    from hf_utils import load_custom_model_from_hf
    pre, cfg_hf = load_custom_model_from_hf(*SV_PRE)
    import yaml
    c1, c2 = yaml.safe_load(open(CONFIG_FT)), yaml.safe_load(open(cfg_hf))
    diff = sorted(k for k in set(c1['model_params']) | set(c2['model_params']) if c1['model_params'].get(k) != c2['model_params'].get(k))
    for k in ('model_params', 'preprocess_params'):
        if k in c2:
            c1[k] = c2[k]
    os.makedirs(os.path.join(TRAVAIL, 'modeles'), exist_ok=True)
    config_ft = os.path.join(TRAVAIL, 'modeles', os.path.basename(CONFIG_FT))
    with open(config_ft, 'w') as f:
        yaml.safe_dump(c1, f, allow_unicode=True, sort_keys=False)
    log(f'ENTRAINEMENT départ {os.path.basename(pre)} ; config {CONFIG_FT} avec model_params/preprocess_params de la config '
        f'Hugging Face (clés qui différaient : {diff})')
    import train
    t0 = time.time()
    tr = train.Trainer(config_path=config_ft, pretrained_ckpt_path=pre, data_dir=donnees, run_name=a.get('nom_run', CIBLE),
                       batch_size=a.get('batch', 2), num_workers=0, steps=a['pas_max'], save_interval=10 ** 9, max_epochs=10 ** 4,
                       device='cuda:0')
    log(f'ENTRAINEMENT {len(fichiers)} fichiers, lots de {a.get("batch", 2)} ; préparation {time.time() - t0:.0f} s')
    for k in tr.model:
        tr.model[k].train()
    pas, ema, pertes, sauves = 0, None, [], []
    t0 = time.time()
    limite = a.get('limite_entrainement_s', 2400)

    def sauver(etiquette):
        chemin = os.path.join(TRAVAIL, 'modeles', f'{CIBLE}_ft_{etiquette}.pth')
        torch.save({'net': {k: tr.model[k].state_dict() for k in tr.model}}, chemin)
        demi = {k: {n: (t.half() if t.is_floating_point() else t) for n, t in tr.model[k].state_dict().items()} for k in tr.model}
        torch.save({'net': demi}, chemin.replace('.pth', '_fp16.pth'))
        info = {'fichier': f'modeles/{CIBLE}_ft_{etiquette}.pth', 'pas': pas, 'perte_lissee': round(ema, 4) if ema else None,
                'secondes': round(time.time() - t0, 1), 'taille_mo': round(os.path.getsize(chemin) / 1e6, 1),
                'taille_fp16_mo': round(os.path.getsize(chemin.replace('.pth', '_fp16.pth')) / 1e6, 1)}
        sauves.append(info)
        ecrire_json(os.path.join(TRAVAIL, 'modeles', 'modeles.json'), sauves)
        log(f'ENTRAINEMENT sauvé : {json.dumps(info)}')
        for k in tr.model:
            tr.model[k].train()

    fini = False
    while not fini:
        for batch in tr.train_dataloader:
            batch = [b.to(tr.device) for b in batch]
            perte = tr.train_one_step(batch)
            pas += 1
            ema = perte if ema is None else ema * 0.99 + perte * 0.01
            pertes.append(round(perte, 4))
            if pas % 25 == 0:
                log(f'ENTRAINEMENT pas {pas}, perte {perte:.4f} (lissée {ema:.4f}), {(time.time() - t0) / pas:.2f} s/pas')
            if pas in a['sauver']:
                sauver(f'{pas}pas')
            if pas >= a['pas_max']:
                fini = True
                break
            if time.time() - t0 > limite:
                log(f'ENTRAINEMENT limite de {limite} s atteinte au pas {pas}')
                if f'{pas}pas' not in [s['fichier'] for s in sauves]:
                    sauver(f'{pas}pas')
                fini = True
                break
    ecrire_json(os.path.join(TRAVAIL, 'modeles', 'pertes.json'), {'pertes': pertes, 'duree_s': round(time.time() - t0, 1)})


# --- Juge chiffré ----------------------------------------------------------------------------------------------------

def normaliser_texte(t):
    import unicodedata
    t = unicodedata.normalize('NFKD', t.lower().replace('œ', 'oe').replace('æ', 'ae'))
    t = ''.join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[’'`´\-]", ' ', t)
    t = re.sub(r'[^a-z0-9 ]+', ' ', t)
    return t.split()


def wer(ref, hyp):
    r, h = normaliser_texte(ref), normaliser_texte(hyp)
    if not r:
        return None
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prec, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prec + (r[i - 1] != h[j - 1]))
            prec = cur
    return round(d[len(h)] / len(r), 3)


def etape_juger(a):
    os.chdir(SEEDVC)
    os.environ['HF_HUB_CACHE'] = './checkpoints/hf_cache'
    torch = charger_torch()
    import numpy as np
    import soundfile as sf
    import librosa
    import torchaudio
    import torch.nn.functional as F
    os.chdir(SEEDVC)
    sys.path.insert(0, SEEDVC)
    os.environ['HF_HUB_CACHE'] = './checkpoints/hf_cache'
    from hf_utils import load_custom_model_from_hf
    from modules.rmvpe import RMVPE
    from modules.campplus.DTDNN import CAMPPlus
    dev = torch.device('cuda')
    t0 = time.time()
    rmvpe = RMVPE(load_custom_model_from_hf('lj1995/VoiceConversionWebUI', 'rmvpe.pt', None), is_half=False, device=dev)
    camp = CAMPPlus(feat_dim=80, embedding_size=192)
    camp.load_state_dict(torch.load(load_custom_model_from_hf('funasr/campplus', 'campplus_cn_common.bin', None), map_location='cpu'))
    camp.eval().to(dev)
    from transformers import AutoFeatureExtractor, WavLMForXVector, WhisperForConditionalGeneration, WhisperProcessor
    fe_wl = AutoFeatureExtractor.from_pretrained('microsoft/wavlm-base-plus-sv')
    wl = WavLMForXVector.from_pretrained('microsoft/wavlm-base-plus-sv').to(dev).eval()
    ecapa = None
    try:
        if a.get('ecapa') is False:
            raise RuntimeError('non demandé')
        if importlib.util.find_spec('speechbrain') is None:
            subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--no-deps', 'speechbrain==1.0.3', 'hyperpyyaml==1.2.2',
                            'ruamel.yaml==0.18.10', 'ruamel.yaml.clib'], check=True, timeout=300)
        import torchaudio as _ta   # speechbrain 1.0 appelle des fonctions retirées de torchaudio 2.9+ (choix du lecteur audio)
        if not hasattr(_ta, 'list_audio_backends'):
            _ta.list_audio_backends = lambda: ['soundfile']
        if not hasattr(_ta, 'get_audio_backend'):
            _ta.get_audio_backend = lambda: 'soundfile'
        if not hasattr(_ta, 'set_audio_backend'):
            _ta.set_audio_backend = lambda *a, **k: None
        from speechbrain.inference.speaker import EncoderClassifier
        ecapa = EncoderClassifier.from_hparams(source='speechbrain/spkrec-ecapa-voxceleb', savedir='/tmp/ecapa',
                                               run_opts={'device': 'cuda'})
    except Exception as e:  # noqa: BLE001 — mesure facultative
        log(f'JUGE ECAPA indisponible : {e!r}'[:400])
    nom_w = a.get('whisper', 'openai/whisper-large-v3')
    if os.path.exists(PAROLES_LOCALES + '.attendu'):   # copie du montage d'outils (orchestrer) : plus rapide que le réseau
        attente = time.time()
        while not os.path.exists(PAROLES_LOCALES) and time.time() - attente < 600:
            time.sleep(2)
        local = (lire_json(PAROLES_LOCALES, {}) or {}).get('whisper')
        if local and os.path.isfile(os.path.join(local, 'config.json')):
            nom_w = local
        log(f'JUGE Whisper : {nom_w} (attente de la copie {time.time() - attente:.0f} s)')
    try:
        proc = WhisperProcessor.from_pretrained(nom_w)
        wh = WhisperForConditionalGeneration.from_pretrained(nom_w, dtype=torch.float16).to(dev).eval()
    except Exception as e:  # noqa: BLE001 — copie locale incomplète : Hugging Face
        log(f'JUGE Whisper {nom_w} : {e!r}'[:300] + ' ; repli sur openai/whisper-large-v3')
        nom_w = 'openai/whisper-large-v3'
        proc = WhisperProcessor.from_pretrained(nom_w)
        wh = WhisperForConditionalGeneration.from_pretrained(nom_w, dtype=torch.float16).to(dev).eval()
    log(f'JUGE modèles chargés en {time.time() - t0:.0f} s (ECAPA {"oui" if ecapa else "non"})')

    def charger16(chemin):
        return librosa.load(chemin, sr=16000, mono=True)[0].astype(np.float32)

    def actif16(y, marge=5):
        """Trames de 20 ms au-dessus du bruit (p95 - 35 dB), élargies de 0,1 s, mises bout à bout."""
        h = 320
        n = len(y) // h
        if n == 0:
            return y
        e = 10 * np.log10((y[:n * h].reshape(n, h) ** 2).mean(1) + 1e-12)
        m = e > max(float(np.percentile(e, 95)) - 35, -70)
        m = np.convolve(m.astype(float), np.ones(2 * marge + 1), mode='same') > 0
        z = y[:n * h].reshape(n, h)[m].reshape(-1)
        return z if len(z) > 16000 else y

    @torch.no_grad()
    def empreintes(y):
        z = actif16(y)
        w = torch.from_numpy(z).to(dev)
        r = {}
        entrees = fe_wl([z], sampling_rate=16000, return_tensors='pt', padding=True).to(dev)
        r['wavlm'] = F.normalize(wl(**entrees).embeddings, dim=-1)[0].float().cpu().numpy()
        feat = torchaudio.compliance.kaldi.fbank(w[None], num_mel_bins=80, dither=0, sample_frequency=16000)
        e = camp((feat - feat.mean(dim=0, keepdim=True)).unsqueeze(0))[0].float()
        r['campplus'] = (e / (e.norm() + 1e-9)).cpu().numpy()
        if ecapa is not None:
            e = ecapa.encode_batch(w[None]).reshape(-1).float()
            r['ecapa'] = (e / (e.norm() + 1e-9)).cpu().numpy()
        return r

    cos = lambda e1, e2: {k: round(float(e1[k] @ e2[k]), 4) for k in e1 if k in e2}

    @torch.no_grad()
    def transcrire(y):
        f = proc.feature_extractor(y, sampling_rate=16000, return_tensors='pt').input_features.to(dev, torch.float16)
        ids = wh.generate(f, language='fr', task='transcribe', num_beams=1, do_sample=False, max_new_tokens=220)
        return proc.batch_decode(ids, skip_special_tokens=True)[0].strip()

    def f0_10ms(x44):
        """RMVPE par tranches de 20 s, comme doubler (une trame toutes les 10 ms, calée sur l'échantillon 0)."""
        out = []
        for d in range(0, len(x44), SR * 20):
            t = x44[d:d + SR * 20]
            w = torchaudio.functional.resample(torch.from_numpy(np.ascontiguousarray(t)).float()[None].to(dev), SR, 16000)
            out.append(rmvpe.infer_from_audio(w[0], thred=0.03)[:len(t) // PAS])
        return np.concatenate(out)

    def actif_10ms(x44):
        env = enveloppe_db(x44)
        return lisser_db(env) > seuil_silence(env)

    def aigus_db(x44, fc=11000):
        import scipy.signal
        f, _, z = scipy.signal.stft(x44.astype(np.float64), fs=SR, nperseg=2048, noverlap=1536)
        p = np.abs(z) ** 2
        e = p.sum(0)
        act = 10 * np.log10(e + 1e-20) > 10 * np.log10(np.percentile(e, 95) + 1e-20) - 30
        return round(float(10 * np.log10(p[f >= fc][:, act].sum() / (p[:, act].sum() + 1e-20) + 1e-20)), 1)

    def lire44(chemin):
        return mono(sf.read(chemin, dtype='float32', always_2d=True)[0])

    resultat = {'session': B.get('session'), 'whisper': nom_w, 'sv': ['wavlm-base-plus-sv', 'campplus'] + (['ecapa-voxceleb'] if ecapa else []),
                'bases': {}, 'versions': {}}
    tenue = entree(a.get('tenue', f'{CIBLE}-tenue-*.wav'))   # la prise gardée de côté, quelle qu'elle soit
    e_cible = empreintes(charger16(tenue))
    e_ref = empreintes(charger16(entree(B.get('reference', f'{CIBLE}-reference-A.wav'))))
    resultat['bases'][CIBLE] = {'prise_tenue': os.path.basename(tenue), 'sim_reference_A_vs_prise_tenue': cos(e_ref, e_cible)}
    ft = sorted(glob.glob(f'/kaggle/input/**/{CIBLE}-ft-*.wav', recursive=True))
    if ft:
        z = np.concatenate([charger16(f) for f in ft[:12]])
        resultat['bases'][CIBLE]['sim_entrainement_vs_prise_tenue'] = cos(empreintes(z), e_cible)
    sortie_juge = os.path.join(TRAVAIL, f"juge_{B.get('session')}{a.get('suffixe', '')}.json")
    for ex, e in extraits_de(a).items():
        lead_sec = lire44(piste_chemin(ex, 'lead_sec'))
        voix = lire44(piste_chemin(ex, 'voix'))
        ch_src = piste_chemin(ex, 'choeurs_sec')
        seche = lead_sec + lire44(ch_src)[:len(lead_sec)] if os.path.exists(ch_src) else lead_sec   # voix d'origine sèche
        e_src = empreintes(charger16(piste_chemin(ex, 'lead_sec')))
        f0_src = f0_10ms(lead_sec)
        act_src = actif_10ms(voix)
        tr_src = transcrire(charger16(piste_chemin(ex, 'lead_sec')))
        tr_voix = transcrire(charger16(piste_chemin(ex, 'voix')))
        base = {f'sim_source_vs_{CIBLE}': cos(e_src, e_cible), 'transcription_source': tr_src, 'transcription_voix_complete': tr_voix,
                'aigus_db_voix_seche': aigus_db(seche),
                'wer_voix_complete_vs_source': wer(tr_src, tr_voix), 'aigus_db_source': aigus_db(lead_sec),
                'part_voisee_source': round(float((f0_src > 1)[act_src[:len(f0_src)]].mean()), 3)}
        if e.get('paroles'):
            base['wer_source_vs_paroles'] = wer(e['paroles'], tr_src)
        resultat['bases'][ex] = base
        log(f'JUGE base {ex} : {json.dumps(base, ensure_ascii=False)}')
        for v in a.get('versions') or sorted(os.path.basename(p)[:-5] for p in glob.glob(f'{TRAVAIL}/{ex}/*.json')
                                             if (lire_json(p, {}) or {}).get('type') == 'version'):
            chemin = os.path.join(TRAVAIL, ex, f'{v}.flac')
            if not os.path.exists(chemin):
                continue
            t1 = time.time()
            y = lire44(chemin)
            em = empreintes(charger16(chemin))
            f0_out = f0_10ms(y)
            n = min(len(f0_out), len(f0_src))
            act = (act_src[:n] | actif_10ms(y)[:n])
            fr = f0_src[:n] * 2 ** (e['decalage'] / 12)
            fo = f0_out[:n]
            deux = (fr > 1) & (fo > 1)
            c = 1200 * np.log2(fo[deux] / fr[deux]) if deux.any() else np.array([np.nan])
            f0m = {'ecart_median_cents': round(float(np.median(np.abs(c))), 1), 'biais_median_cents': round(float(np.median(c)), 1),
                   'part_50c': round(float(np.mean(np.abs(c) < 50)), 3), 'part_octave': round(float(np.mean(np.abs(c) > 600)), 3),
                   'accord_voise': round(float(((fr > 1) == (fo > 1))[act].mean()), 3), 'trames_comparees': int(deux.sum())}
            tr = transcrire(charger16(chemin))
            m = {'sim': {k: {CIBLE: round(float(em[k] @ e_cible[k]), 4), 'source': round(float(em[k] @ e_src[k]), 4)} for k in em},
                 'f0': f0m, 'transcription': tr, 'wer': {'source': wer(tr_src, tr)}, 'aigus_db': aigus_db(y),
                 'ecart_spectral_db': ecart_spectral_db(y, seche)}
            if e.get('paroles'):
                m['wer']['paroles'] = wer(e['paroles'], tr)
            ch = os.path.join(TRAVAIL, ex, f'{v}_choeurs_sec.flac')
            if os.path.exists(ch):
                ec = empreintes(charger16(ch))
                m['choeurs'] = {k: {CIBLE: round(float(ec[k] @ e_cible[k]), 4), 'source': round(float(ec[k] @ e_src[k]), 4)} for k in ec}
            m['juge_s'] = round(time.time() - t1, 1)
            resultat['versions'].setdefault(ex, {})[v] = m
            log(f'JUGE {ex} {v} : {json.dumps(m, ensure_ascii=False)}')
            ecrire_json(sortie_juge, resultat)
    resultat['duree_s'] = round(time.time() - t0, 1)
    ecrire_json(sortie_juge, resultat)


def principal():
    if len(sys.argv) < 2 or sys.argv[1] == 'orchestrer':
        return orchestrer()
    etape, args = sys.argv[1], json.loads(sys.argv[2])
    if SOUCHES not in sys.path and os.path.isdir(SOUCHES):
        sys.path.insert(0, SOUCHES)
    {'separer': etape_separer, 'convertir': etape_convertir, 'entrainer': etape_entrainer, 'juger': etape_juger,
     'separer_voix': etape_separer_voix, 'nettoyer_variantes': etape_nettoyer_variantes, 'deriver': etape_deriver}[etape](args)


if __name__ == '__main__':
    principal()
