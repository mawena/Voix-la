# ======================================================================================================================
# Recherche « unisson » (recherche/unisson/noyau_unisson.py) : unisson.py ajoute ce code après les fonctions de
# kaggle/doubler_kaggle.py (installer, cloner, poids_depuis_outils, installer_ying, MoteurYing, enveloppe_db, lisser_db,
# seuil_silence, decouper, rampe…), lues telles quelles, sans leur bloc final.
#
# Question : deux chanteurs qui chantent ENSEMBLE (unisson, octave, tierce) restent tous deux dans la voix principale du
# Roformer karaoké. Peut-on les séparer pour donner à chacun sa voix convertie ?
#
#   orchestrer      installation (montage d'outils), puis les phases de REGLAGES['phases'] : chaque phase est une
#                   liste de suites d'étapes, une suite par carte, en même temps ; chaque étape dans son propre processus
#   generer         duos synthétiques à vérité connue : voix B = voix A convertie par YingMusic-SVC vers le timbre d'un
#                   autre chanteur (référence libre), décalée de 0 (unisson), ±12 (octave) ou +4 demi-tons (tierce)
#   melanger        mélanges (alternance, superposition, même air non calé, unisson, octave, tierce), sources au même
#                   niveau ; un fichier de tous les mélanges bout à bout (1 s de silence entre deux) pour les Roformer
#   separer_synth   candidats audio-separator (Roformer karaoké, Roformer homme/femme…) sur les mélanges
#   parole          candidats de séparation de parole (SepFormer, MossFormer2), dans un environnement Python à part
#   masque          masque harmonique guidé par deux hauteurs (oracle : hauteurs des sources ; estimées : hauteurs des
#                   sorties d'un Roformer homme/femme)
#   mesurer         SI-SDR de chaque sortie (meilleure permutation), amélioration sur le mélange, identification du
#                   chanteur par l'empreinte CAMPPlus (références à part), étiquette homme/femme
#   separer_reel    vrai duo : voix (Roformer de doubler), karaoké, dereverb, puis Roformer homme/femme sur la voix et
#                   sur la voix principale sèche ; temps de chaque passe ; secondes chantées à deux ; extrait d'écoute
#   demo            l'extrait du vrai duo doublé : voix principale d'un bloc dans une voix, ou chaque chanteur séparé
#                   dans la sienne (YingMusic-SVC, références libres)
# Sorties dans /kaggle/working : session.json, synth/mesures.json, reel/*.flac + reel/reel.json, demo/*.flac + demo.json.
# Rien n'est envoyé ailleurs : poids téléchargés (GitHub, Hugging Face), aucune donnée ne sort du noyau privé.
# ======================================================================================================================
import re

U = REGLAGES
TRAVAIL = SORTIE
COMPLET_U = '/tmp/unisson_complet.py'
SYNTH = os.path.join(TRAVAIL, 'synth')          # mesures (les sons des mélanges restent dans /tmp)
SYNTH_TMP = '/tmp/unisson_synth'                 # sons des mélanges et sorties des candidats
REEL = os.path.join(TRAVAIL, 'reel')
REEL_TMP = '/tmp/unisson_reel'
DEMO = os.path.join(TRAVAIL, 'demo')
VENV_PAROLE = '/tmp/venv_parole'
PAQUETS_PAROLE = '/tmp/paquets_parole'
SESSION_U = os.path.join(TRAVAIL, 'session.json')

# Candidats audio-separator : nom -> (fichier du modèle, segment, réglage « override », motifs des deux pistes rendues).
# Karaoké : (voix principale, reste) ; homme/femme : (male, female).
ROFORMERS = {
    'karaoke_becruily': ('mel_band_roformer_karaoke_becruily.ckpt', 256, False, (r'\((vocals|lead|karaoke)\)', None)),
    'karaoke_bs_frazer': ('bs_roformer_karaoke_frazer_becruily.ckpt', 256, False, (r'\((vocals|lead|karaoke)\)', None)),
    'hf_aufr33': ('bs_roformer_male_female_by_aufr33_sdr_7.2889.ckpt', 256, False, (r'\(male\)', r'\(female\)')),
    'hf_sucial': ('model_chorus_bs_roformer_ep_267_sdr_24.1275.ckpt', 256, False, (r'\(male\)', r'\(female\)')),
}
VOIX_KIM = ('vocals_mel_band_roformer.ckpt', 801, True, (r'\((vocals|vocal|voice)\)', None))
DEREVERB_U = ('dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt', 256, False, (r'\((noreverb|no reverb|no_reverb|dry)\)', None))


# --- Outils communs ---------------------------------------------------------------------------------------------------

def ecrire_json_u(chemin, d):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    tmp = chemin + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f, ensure_ascii=False, indent=1, default=float)
    os.replace(tmp, chemin)


def lire_json_u(chemin, defaut=None):
    try:
        with open(chemin) as f:
            return json.load(f)
    except (OSError, ValueError):
        return defaut


def entree_u(nom):
    """Un fichier du jeu de données privé monté (/kaggle/input/…), sinon None."""
    c = glob.glob(f'/kaggle/input/**/{nom}', recursive=True)
    return c[0] if c else None


def lire_mono_u(chemin):
    import numpy as np
    import soundfile as sf
    x, sr = sf.read(chemin, dtype='float32', always_2d=True)
    assert sr == SR, (chemin, sr)
    return np.ascontiguousarray(x.mean(1), dtype=np.float32)


def ecrire_wav_u(chemin, x, sr=SR):
    import soundfile as sf
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    sf.write(chemin, x, sr, subtype='FLOAT')


def ecrire_flac_u(chemin, x, sr=SR):
    """FLAC 24 bits sans écrêtage : crête ramenée à -1 dBFS ; rend le gain appliqué (à retirer pour le niveau d'origine)."""
    import numpy as np
    import soundfile as sf
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    pic = float(np.abs(x).max()) or 1.0
    g = 10 ** (-1 / 20) / pic
    sf.write(chemin, np.clip(x * g, -1, 1), sr, subtype='PCM_24', format='FLAC')
    return g


def rms_actif_u(x):
    """RMS sur les trames non silencieuses (enveloppe du noyau de doubler)."""
    import numpy as np
    env = enveloppe_db(x)
    act = lisser_db(env) > seuil_silence(env)
    if not act.any():
        return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-9)
    return float(np.sqrt(np.mean(10 ** (env[act] / 10))) + 1e-9)


def stereo(x):
    import numpy as np
    return np.ascontiguousarray(np.repeat(x[:, None], 2, axis=1) if x.ndim == 1 else x[:, :2], dtype=np.float32)


def session_maj(cle, valeur):
    s = lire_json_u(SESSION_U, {}) or {}
    s[cle] = valeur
    ecrire_json_u(SESSION_U, s)


def torch_u():
    os.environ['TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD'] = '1'
    import torch
    if not getattr(torch.load, '_unisson', False):
        charger = torch.load

        def charger_complet(*a, **k):
            k.setdefault('weights_only', False)
            return charger(*a, **k)
        charger_complet._unisson = True
        torch.load = charger_complet
    return torch


# --- Séparateurs audio-separator --------------------------------------------------------------------------------------

class Roformer:
    """Un modèle audio-separator chargé (autocast, recouvrement de REGLAGES) ; separer(x) rend les deux pistes voulues
    (motifs), au niveau de l'entrée (pic ramené à 0,9 puis remis à l'échelle, comme le noyau de doubler)."""

    def __init__(self, fiche, tmp, recouvrement=None):
        from audio_separator.separator import Separator
        self.nom, segment, override, self.motifs = fiche
        self.tmp = tmp
        os.makedirs(tmp, exist_ok=True)
        t0 = time.time()
        commun = dict(model_file_dir='/tmp/modeles_sep', output_dir=tmp, output_format='WAV', use_soundfile=True,
                      normalization_threshold=1.0, sample_rate=44100,
                      mdxc_params={'segment_size': segment, 'override_model_segment_size': override, 'batch_size': 1,
                                   'overlap': int(recouvrement or U.get('recouvrement', 2)), 'pitch_shift': 0})
        try:
            self.s = Separator(**commun, use_autocast=True)
            self.precision = 'autocast'
        except TypeError:
            self.s = Separator(**commun)
            self.precision = 'fp32'
        self.s.load_model(model_filename=self.nom)
        self.chargement_s = round(time.time() - t0, 1)
        log(f'ROFORMER {self.nom} chargé en {self.chargement_s:.0f} s ({self.precision})')

    def separer(self, x, etiquette='entree'):
        """x : (n,) ou (n, 2). Rend ((piste 1, piste 2), durée, noms des pistes). Piste 2 absente du modèle (karaoké,
        voix) : entrée - piste 1."""
        import numpy as np
        import soundfile as sf
        for f in glob.glob(f'{self.tmp}/*.wav'):
            os.remove(f)
        x2 = stereo(x)
        pic = float(np.abs(x2).max()) or 1.0
        g = 0.9 / pic
        fichier = f'{self.tmp}/{etiquette}.wav'
        sf.write(fichier, x2 * g, 44100, subtype='FLOAT')
        t0 = time.time()
        sorties = self.s.separate(fichier)
        duree = time.time() - t0
        sorties = [p if os.path.isabs(p) else os.path.join(self.tmp, p) for p in sorties]
        noms = [os.path.basename(p) for p in sorties]
        pistes = []
        for motif in self.motifs:
            if motif is None:
                pistes.append(None)
                continue
            choisis = [p for p in sorties if re.search(motif, os.path.basename(p), re.I)]
            if len(choisis) != 1:
                raise RuntimeError(f'{self.nom} : pistes {noms}, motif {motif} : {len(choisis)} correspondance(s)')
            y = sf.read(choisis[0], dtype='float32', always_2d=True)[0]
            out = np.zeros_like(x2)
            n = min(len(y), len(x2))
            out[:n] = y[:n, :2] / g
            pistes.append(out)
        if pistes[1] is None:
            pistes[1] = x2 - pistes[0]
        if not all(np.isfinite(p).all() for p in pistes):
            raise RuntimeError(f'{self.nom} : son non fini')
        for f in glob.glob(f'{self.tmp}/*.wav'):
            os.remove(f)
        if x.ndim == 1:
            pistes = [p.mean(1) for p in pistes]
        return pistes, round(duree, 2), noms


# --- Orchestration ----------------------------------------------------------------------------------------------------

def orchestrer():
    session = {'debut_epoch': DEBUT, 'phases': [], 'reglages': {k: v for k, v in U.items() if k != 'plan'}}
    ecrire_json_u(SESSION_U, session)
    verifier_acces()
    outils = trouver_outils()
    log(f"outils : {'montage ' + outils if outils else 'aucun montage, tout par internet'}")
    t = time.time()
    installer(outils)
    session['seedvc'] = cloner(outils)
    poids_depuis_outils(outils)
    session['yingmusic'] = installer_ying(outils)
    session['installation_s'] = round(time.time() - t, 1)
    ecrire_json_u(SESSION_U, session)
    import threading
    fil = threading.Thread(target=installer_parole, daemon=True)   # pendant les premières phases (réseau, CPU)
    fil.start()
    # Chaque Roformer est téléchargé par la première étape qui s'en sert : le vrai duo (phase 0, une carte) prend les
    # modèles homme/femme avant que les deux cartes de la phase 2 ne les chargent ensemble.
    for k, phase in enumerate(U['phases']):
        if any(e.get('etape') == 'parole' for suite in phase for e in suite):
            fil.join(timeout=180)   # au-delà, l'étape parole est sautée (lancer_etape_u)
        r = lancer_phase(phase, k)
        s = lire_json_u(SESSION_U, session) or session
        s.setdefault('phases', []).append(r)
        ecrire_json_u(SESSION_U, s)
    s = lire_json_u(SESSION_U, session) or session
    s['total_s'] = round(time.time() - DEBUT, 1)
    ecrire_json_u(SESSION_U, s)
    log(f'MESURE temps total du noyau : {time.time() - DEBUT:.0f} s')


def installer_parole():
    """SpeechBrain, asteroid et ClearerVoice dans un environnement à part (venv qui voit les paquets du système) : leurs
    dépendances ne touchent pas celles d'audio-separator et de YingMusic-SVC. torch, torchaudio, numpy et scipy y sont
    figés aux versions du système (contraintes pip) : une commande qui voudrait les changer échoue seule. Repli sans
    venv : pip --target --no-deps."""
    t0 = time.time()
    commandes = U.get('paquets_parole') or [['speechbrain']]   # une liste d'arguments de pip install par commande
    etat = {'commandes': commandes, 'codes': []}
    try:
        from importlib.metadata import PackageNotFoundError, version
        contraintes = '/tmp/contraintes_parole.txt'
        with open(contraintes, 'w') as f:
            for p in ('torch', 'torchaudio', 'numpy', 'scipy'):
                try:
                    f.write(f'{p}=={version(p)}\n')
                except PackageNotFoundError:
                    pass
        r = subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', VENV_PAROLE], capture_output=True, text=True)
        etat['mode'] = 'venv' if r.returncode == 0 else 'target'
        for args in commandes:
            if etat['mode'] == 'venv':
                cmd = [os.path.join(VENV_PAROLE, 'bin', 'python'), '-m', 'pip', 'install', '-q', '-c', contraintes, *args]
            else:   # sans venv : --no-deps, pour ne rien tirer qui remplacerait torch ou numpy
                cmd = [sys.executable, '-m', 'pip', 'install', '-q', '--no-deps', '--target', PAQUETS_PAROLE,
                       *[a for a in args if a != '--no-deps']]
            r = subprocess.run(cmd, capture_output=True, text=True)
            etat['codes'].append(r.returncode)
            if r.returncode:
                etat.setdefault('sorties', []).append((r.stderr or r.stdout)[-600:])
        etat['code'] = 0 if not any(etat['codes']) else 1
    except Exception as e:  # noqa: BLE001
        etat['erreur'] = repr(e)
    etat['duree_s'] = round(time.time() - t0, 1)
    ecrire_json_u('/tmp/unisson_parole_installation.json', etat)
    log(f"PAROLE installation ({etat.get('mode')}) : code {etat.get('code')}, {etat['duree_s']:.0f} s")


def python_parole():
    """(exécutable, environnement) pour l'étape parole."""
    etat = lire_json_u('/tmp/unisson_parole_installation.json', {}) or {}
    env = dict(os.environ)
    if etat.get('mode') == 'venv':   # même si une commande a échoué : les candidats installés tournent
        return os.path.join(VENV_PAROLE, 'bin', 'python'), env
    env['PYTHONPATH'] = PAQUETS_PAROLE + os.pathsep + env.get('PYTHONPATH', '')
    return sys.executable, env


def lancer_etape_u(etape, env=None, prefixe=''):
    nom = prefixe + etape['etape']
    reste = U['limite_totale_s'] - (time.time() - DEBUT)
    if reste < etape.get('minimum_s', 60):
        log(f'ÉTAPE {nom} : sautée, il ne reste que {reste:.0f} s')
        return {'etape': etape['etape'], 'code': 'sautée'}
    if etape['etape'] == 'parole' and not os.path.exists('/tmp/unisson_parole_installation.json'):
        log(f'ÉTAPE {nom} : sautée, installation des paquets de parole inachevée')
        return {'etape': etape['etape'], 'code': 'sautée (installation)'}
    log(f'ÉTAPE {nom} : début')
    t = time.time()
    exe = sys.executable
    if etape['etape'] == 'parole':
        exe, env_p = python_parole()
        env = {**env_p, **(env or {})}
    env = {**(env or os.environ), 'DOUBLER_DEBUT': str(DEBUT)}
    try:
        r = subprocess.run([exe, COMPLET_U, etape['etape'], json.dumps(etape)], timeout=min(reste, etape.get('limite_s', reste)),
                           env=env)
        code = r.returncode
    except subprocess.TimeoutExpired:
        code = 'limite de temps'
    d = round(time.time() - t, 1)
    log(f'ÉTAPE {nom} : fin, code {code}, {d:.0f} s')
    return {'etape': etape['etape'], 'code': code, 'duree_s': d, 'debut_s': round(t - DEBUT, 1)}


def lancer_phase(phase, k):
    """Une suite d'étapes par carte, en même temps (CUDA_VISIBLE_DEVICES). Une seule carte : tout à la suite."""
    import threading
    try:
        r = subprocess.run(['nvidia-smi', '-L'], capture_output=True, text=True, timeout=60)
        n = max(1, sum(1 for l in r.stdout.splitlines() if l.startswith('GPU ')))
    except Exception:  # noqa: BLE001
        n = 1
    suites = [s for s in phase if s]
    if n < len(suites):
        suites = [[e for s in suites for e in s]]
    res, verrou = [], threading.Lock()

    def suite(c, liste):
        env = {**os.environ, 'CUDA_VISIBLE_DEVICES': str(c)} if len(suites) > 1 else None
        for e in liste:
            x = lancer_etape_u(e, env, f'[phase {k}, carte {c}] ' if len(suites) > 1 else f'[phase {k}] ')
            x['carte'] = c
            with verrou:
                res.append(x)
    fils = [threading.Thread(target=suite, args=(c, l)) for c, l in enumerate(suites)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    return res


# --- Duos synthétiques ------------------------------------------------------------------------------------------------

def convertir_piste_u(m, ref, x, decalage, pas, graine=1234):
    """Comme convertir_piste du banc (même chaîne que doubler) : découpage aux silences, marges, fondus, porte."""
    import numpy as np
    torch = m.torch
    n = len(x)
    env = enveloppe_db(x)
    lisse = lisser_db(env)
    seuil = seuil_silence(env)
    pad, fondu = int(0.25 * SR), int(0.06 * SR)
    place = SR // 512 * 30 - ref['mel2'].size(2)
    coeur_max = place * 512 - 2 * pad - int(0.1 * SR)
    coupes = decouper(lisse, n, coeur_max, min(int(2 * SR), coeur_max // 2), seuil)
    fondus = {c: fondu if lisse[c // PAS] < seuil else int(0.2 * SR) for c in coupes[1:-1]}
    actif = (lisse > seuil).astype(np.float64)
    actif = (np.convolve(actif, np.ones(41), mode='same') > 0).astype(np.float64)
    gain_trames = np.convolve(actif, np.ones(5) / 5, mode='same')
    gain = np.interp(np.arange(n), np.arange(len(gain_trames)) * PAS, gain_trames).astype(np.float32)
    sortie = np.zeros(n, np.float32)
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
    return sortie * gain


def etape_generer(a):
    """Voix B de chaque duo synthétique : la voix A (prise libre) convertie vers le timbre du chanteur B, décalée."""
    plan = U['plan']
    outils = trouver_outils()
    m = MoteurYing(outils, poids_ying(outils))
    refs, res = {}, {'chargement_s': m.chargement_s, 'conversions': []}
    t_tout = time.time()
    for job in plan['conversions']:
        t0 = time.time()
        if job['ref'] not in refs:
            refs[job['ref']] = m.preparer_ref(entree_u(job['ref']))
        x = lire_mono_u(entree_u(job['source']))
        y = convertir_piste_u(m, refs[job['ref']], x, job['decalage'], int(U.get('pas_synth', 20)))
        ecrire_wav_u(os.path.join(SYNTH_TMP, 'conv', job['id'] + '.wav'), y)
        res['conversions'].append({'id': job['id'], 'duree_son_s': round(len(x) / SR, 2), 'duree_s': round(time.time() - t0, 1)})
        log(f"GENERER {job['id']} : {job['source']} -> timbre {job['ref']}, {job['decalage']:+d} demi-tons, "
            f'{time.time() - t0:.1f} s')
    res['total_s'] = round(time.time() - t_tout, 1)
    ecrire_json_u(os.path.join(SYNTH, 'generer.json'), res)


def etape_melanger(a):
    """Mélanges à vérité connue (deux sources au même niveau actif) et fichier de tous les mélanges bout à bout."""
    import numpy as np
    plan = U['plan']
    morceaux, index, pos = [], [], 0
    blanc = np.zeros(SR, np.float32)
    for mel in plan['melanges']:
        srcs = []
        manque = [s['conversion'] for s in mel['sources']
                  if s.get('conversion') and not os.path.exists(os.path.join(SYNTH_TMP, 'conv', s['conversion'] + '.wav'))]
        if manque:
            log(f"ÉCHEC MELANGER {mel['id']} : conversion absente {manque}")
            continue
        for s in mel['sources']:
            if s.get('conversion'):
                y = lire_mono_u(os.path.join(SYNTH_TMP, 'conv', s['conversion'] + '.wav'))
            else:
                y = lire_mono_u(entree_u(s['fichier']))
            if s.get('garder'):   # alternance : seulement [debut, fin) de la source, le reste au silence
                lo, hi = int(s['garder'][0] * SR), int(s['garder'][1] * SR)
                z = np.zeros_like(y)
                z[lo:hi] = y[lo:hi]
                fo = int(0.05 * SR)
                if lo > 0:
                    z[lo:lo + fo] *= np.linspace(0, 1, fo)
                if hi < len(z):
                    z[hi - fo:hi] *= np.linspace(1, 0, fo)
                y = z
            srcs.append(y)
        n = min(len(s) for s in srcs)
        srcs = [s[:n] for s in srcs]
        ref = rms_actif_u(srcs[0])
        srcs = [s * np.float32(ref / rms_actif_u(s)) for s in srcs]
        mix = srcs[0] + srcs[1]
        g = 0.5 / float(np.abs(mix).max())
        srcs, mix = [s * g for s in srcs], mix * g
        d = os.path.join(SYNTH_TMP, mel['id'])
        for k, s in enumerate(srcs):
            ecrire_wav_u(os.path.join(d, f's{k + 1}.wav'), s)
        ecrire_wav_u(os.path.join(d, 'mix.wav'), mix)
        index.append({'id': mel['id'], 'debut': pos, 'n': n})
        morceaux += [mix, blanc]
        pos += n + len(blanc)
    ecrire_wav_u(os.path.join(SYNTH_TMP, 'tout.wav'), np.concatenate(morceaux))
    ecrire_json_u(os.path.join(SYNTH_TMP, 'index.json'), index)
    log(f"MELANGER {len(index)} mélanges, {pos / SR:.0f} s bout à bout")


def etape_separer_synth(a):
    """Chaque candidat Roformer de a['candidats'] sur tous les mélanges bout à bout (une séparation par modèle)."""
    import numpy as np
    index = lire_json_u(os.path.join(SYNTH_TMP, 'index.json'))
    tout = lire_mono_u(os.path.join(SYNTH_TMP, 'tout.wav'))
    res = lire_json_u(os.path.join(SYNTH, f"separer_{a.get('nom', 'roformer')}.json"), {}) or {}
    for nom in a['candidats']:
        try:
            r = Roformer(ROFORMERS[nom], f"/tmp/sep_{nom}")
            (p1, p2), duree, noms = r.separer(tout, nom)
            for e in index:
                d = os.path.join(SYNTH_TMP, e['id'], nom)
                ecrire_wav_u(os.path.join(d, 'o1.wav'), p1[e['debut']:e['debut'] + e['n']])
                ecrire_wav_u(os.path.join(d, 'o2.wav'), p2[e['debut']:e['debut'] + e['n']])
            res[nom] = {'modele': r.nom, 'chargement_s': r.chargement_s, 'separation_s': duree,
                        'duree_son_s': round(len(tout) / SR, 1), 'rtf': round(duree / (len(tout) / SR), 3), 'pistes': noms,
                        'precision': r.precision}
            log(f'SEPARER_SYNTH {nom} : {duree:.1f} s pour {len(tout) / SR:.0f} s ({noms})')
            del r
        except Exception as e:  # noqa: BLE001
            res[nom] = {'erreur': repr(e)[-600:]}
            log(f'ÉCHEC SEPARER_SYNTH {nom} : {e!r}')
        ecrire_json_u(os.path.join(SYNTH, f"separer_{a.get('nom', 'roformer')}.json"), res)


# --- Séparation de parole (environnement à part) ----------------------------------------------------------------------

def etape_parole(a):
    """SepFormer (SpeechBrain) et MossFormer2 (ClearerVoice) sur chaque mélange, rééchantillonné à leur fréquence ; sorties
    remises à 44,1 kHz (sans les aigus au-delà de leur demi-fréquence). Sur le vrai duo aussi (voix principale sèche
    de l'extrait d'écoute), pour l'écoute."""
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    index = lire_json_u(os.path.join(SYNTH_TMP, 'index.json')) or []
    res = {}
    entrees = [(e['id'], os.path.join(SYNTH_TMP, e['id'], 'mix.wav'), os.path.join(SYNTH_TMP, e['id'])) for e in index]
    reel = os.path.join(REEL_TMP, 'extrait_lead_sec.wav')
    if os.path.exists(reel):
        entrees.append(('reel', reel, REEL))
    for nom in a['candidats']:
        t0 = time.time()
        try:
            sep, sr_m = charger_parole(nom)
            res[nom] = {'chargement_s': round(time.time() - t0, 1), 'sr': sr_m, 'gains_flac_reel': {}}
            t1, total = time.time(), 0.0
            for ident, chemin, dossier in entrees:
                x = lire_mono_u(chemin)
                xm = resample_poly(x.astype(np.float64), sr_m, SR).astype(np.float32)
                ys = sep(xm)
                for k, y in enumerate(ys[:2]):
                    z = resample_poly(np.asarray(y, np.float64), SR, sr_m).astype(np.float32)
                    z = z[:len(x)] if len(z) >= len(x) else np.pad(z, (0, len(x) - len(z)))
                    if ident == 'reel':   # écoute : FLAC rapatrié avec l'extrait
                        res[nom]['gains_flac_reel'][f'{nom}_o{k + 1}'] = ecrire_flac_u(os.path.join(dossier, f'{nom}_o{k + 1}.flac'), z)
                    else:
                        ecrire_wav_u(os.path.join(dossier, nom, f'o{k + 1}.wav'), z)
                if ident != 'reel':
                    total += len(x) / SR
            res[nom].update({'separation_s': round(time.time() - t1, 1), 'duree_son_s': round(total, 1)})
            log(f'PAROLE {nom} : {time.time() - t1:.1f} s')
        except Exception as e:  # noqa: BLE001
            import traceback
            res[nom] = {'erreur': repr(e)[-600:], 'trace': traceback.format_exc()[-1500:]}
            log(f'ÉCHEC PAROLE {nom} : {e!r}')
        ecrire_json_u(os.path.join(SYNTH, 'separer_parole.json'), res)


def charger_parole(nom):
    """(fonction mono -> [voix 1, voix 2], fréquence du modèle)."""
    import numpy as np
    torch = torch_u()
    if nom.startswith('sepformer'):
        try:
            from speechbrain.inference.separation import SepformerSeparation
        except ImportError:
            from speechbrain.pretrained import SepformerSeparation
        source = {'sepformer_wsj02mix': ('speechbrain/sepformer-wsj02mix', 8000),
                  'sepformer_whamr16k': ('speechbrain/sepformer-whamr16k', 16000),
                  'sepformer_libri2mix': ('speechbrain/sepformer-libri2mix', 8000)}[nom]
        m = SepformerSeparation.from_hparams(source=source[0], savedir=f'/tmp/sb_{nom}', run_opts={'device': 'cuda'})

        def sep(x):
            with torch.no_grad():
                est = m.separate_batch(torch.from_numpy(x)[None].cuda())   # [1, T, 2]
            return [est[0, :, k].float().cpu().numpy() for k in range(est.shape[-1])]
        return sep, source[1]
    if nom == 'mossformer2_ss16k':
        os.makedirs('/tmp/clearvoice', exist_ok=True)
        os.chdir('/tmp/clearvoice')   # ClearVoice range ses poids dans ./checkpoints : jamais dans /kaggle/working
        from clearvoice import ClearVoice
        cv = ClearVoice(task='speech_separation', model_names=['MossFormer2_SS_16K'])
        import soundfile as sf

        def sep(x):
            chemin = '/tmp/clearvoice/entree.wav'
            sf.write(chemin, x, 16000, subtype='FLOAT')
            out = cv(input_path=chemin, online_write=False)
            if isinstance(out, dict):
                out = next(iter(out.values()))
            if not isinstance(out, (list, tuple)):
                out = np.squeeze(np.asarray(out))
                out = out.T if out.ndim == 2 and out.shape[0] > out.shape[1] else out
            return [np.asarray(o, np.float32).reshape(-1) for o in out]
        return sep, 16000
    if nom.startswith('medleyvox'):
        return charger_medleyvox(U.get('medleyvox_modele', 'vocals 238'))
    raise ValueError(nom)


def charger_medleyvox(dossier):
    """MedleyVox (Jeon et al., ICASSP 2023) réentraîné par Cyru5 (Hugging Face Cyru5/MedleyVox, CC BY 4.0) : Conv-TasNet
    sur STFT, deux voix, 24 kHz, sortie cohérente avec le mélange. Modèle rebâti avec asteroid seul, comme
    models/define_models.py de MedleyVox-Inference-WebUI (MIT) ; poids « ema » ; entrée ramenée à -24 dB RMS (la
    WebUI : -24 LUFS), puis remise à l'échelle."""
    import numpy as np
    torch = torch_u()
    from huggingface_hub import hf_hub_download
    from asteroid_filterbanks import make_enc_dec
    from asteroid.masknn import TDConvNet
    from asteroid.models.base_models import BaseEncoderMaskerDecoder, _shape_reconstructed, _unsqueeze_to_3d
    from asteroid.utils.torch_utils import jitable_shape, pad_x_to_y
    fiche = hf_hub_download('Cyru5/MedleyVox', f'{dossier}/vocals.json', cache_dir='/tmp/hf_medleyvox')
    poids = hf_hub_download('Cyru5/MedleyVox', f'{dossier}/vocals.pth', cache_dir='/tmp/hf_medleyvox')
    a = json.load(open(fiche))['args']
    assert a['architecture'] == 'conv_tasnet_stft' and a['mixture_consistency'] == 'mixture_consistency', a['architecture']

    class Coherent(BaseEncoderMaskerDecoder):
        def forward(self, wav):
            forme = jitable_shape(wav)
            wav = _unsqueeze_to_3d(wav)
            tf = self.forward_encoder(wav)
            dec = self.forward_decoder(self.apply_masks(tf, self.forward_masker(tf)))
            rec = _shape_reconstructed(pad_x_to_y(dec, wav), forme)
            return rec + 1 / rec.shape[1] * (wav - rec.sum(dim=1, keepdim=True))
    enc, dec = make_enc_dec('torch_stft', n_filters=a['nfft'], kernel_size=a['nfft'], stride=a['nhop'],
                            sample_rate=a['sample_rate'])
    masker = TDConvNet(in_chan=enc.n_feats_out, n_src=a['n_src'], out_chan=None, n_blocks=a['n_blocks'],
                       n_repeats=a['n_repeats'], bn_chan=a['bn_chan'], hid_chan=a['hid_chan'], skip_chan=a['skip_chan'],
                       mask_act=a['mask_act'])
    model = Coherent(enc, masker, dec, encoder_activation=a.get('encoder_activation'))
    ck = torch.load(poids, map_location='cpu')
    md = model.state_dict()
    pris = {k.replace('ema_model.module.', ''): v for k, v in ck.items() if k.replace('ema_model.module.', '') in md}
    if len(pris) < len(md):
        raise RuntimeError(f'MedleyVox : {len(pris)} poids sur {len(md)} reconnus')
    md.update(pris)
    model.load_state_dict(md)
    model.eval().cuda()

    def sep(x):
        g = 10 ** (-24 / 20) / (float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) + 1e-9)
        with torch.no_grad():
            y = model(torch.from_numpy(np.ascontiguousarray(x * g, np.float32))[None, None].cuda())
        return [y[0, k].float().cpu().numpy() / g for k in range(y.shape[1])]
    return sep, int(a['sample_rate'])


# --- Masque harmonique guidé par deux hauteurs -------------------------------------------------------------------------

class Analyse:
    """RMVPE (hauteur, 10 ms) et CAMPPlus (empreinte) de YingMusic-SVC / Seed-VC, sur le GPU."""

    def __init__(self):
        torch = self.torch = torch_u()
        os.chdir(YING)
        os.environ['HF_HUB_CACHE'] = './checkpoints/hf_cache'
        sys.path.insert(0, YING)
        from huggingface_hub import hf_hub_download
        from modules.rmvpe import RMVPE
        from modules.campplus.DTDNN import CAMPPlus
        self.device = torch.device('cuda')
        rm = hf_hub_download(repo_id='lj1995/VoiceConversionWebUI', filename='rmvpe.pt', cache_dir='./checkpoints')
        self.rmvpe = RMVPE(rm, is_half=False, device=self.device)
        cp = hf_hub_download(repo_id='funasr/campplus', filename='campplus_cn_common.bin', cache_dir='./checkpoints')
        self.campplus = CAMPPlus(feat_dim=80, embedding_size=192)
        self.campplus.load_state_dict(torch.load(cp, map_location='cpu'))
        self.campplus.eval().to(self.device)

    def a16k(self, x):
        import numpy as np
        from scipy.signal import resample_poly
        return resample_poly(np.asarray(x, np.float64), 160, 441).astype(np.float32)

    def f0(self, x):
        """Hauteur (Hz, 0 = non voisé), une trame toutes les 10 ms."""
        import numpy as np
        w = self.a16k(x)
        with self.torch.no_grad():
            f = self.rmvpe.infer_from_audio(self.torch.from_numpy(w).to(self.device), thred=0.03)
        return np.asarray(f, np.float64)

    def empreinte(self, x):
        import numpy as np
        import torchaudio
        torch = self.torch
        w = self.a16k(x)
        if np.abs(w).max() > 0:
            w = w / np.abs(w).max() * 0.5
        with torch.no_grad():
            t = torch.from_numpy(w)[None].to(self.device)
            feat = torchaudio.compliance.kaldi.fbank(t, num_mel_bins=80, dither=0, sample_frequency=16000)
            e = self.campplus((feat - feat.mean(dim=0, keepdim=True)).unsqueeze(0))[0].float().cpu().numpy()
        return e / (np.linalg.norm(e) + 1e-9)


def masque_harmonique(mix, f0a, f0b, sr=SR, n_fft=4096, pas=441, fmax=12000.0):
    """Deux pistes tirées du mélange par un masque doux : chaque case temps-fréquence est partagée entre les deux voix
    au prorata de la proximité de leurs harmoniques (gaussiennes, largeur 2 % de la fréquence de l'harmonique, au moins
    deux cases). Une voix seule voisée prend tout ; aucune : moitié-moitié. Unisson : les harmoniques coïncident, le
    masque ne peut que partager."""
    import numpy as np
    import librosa
    Z = librosa.stft(mix.astype(np.float32), n_fft=n_fft, hop_length=pas, win_length=n_fft, center=True)
    nb, nt = Z.shape
    freqs = np.arange(nb) * sr / n_fft
    larg_min = 2 * sr / n_fft

    def a_trames(f):
        f = np.asarray(f, np.float64)
        return f[:nt] if len(f) >= nt else np.pad(f, (0, nt - len(f)))
    fa, fb = a_trames(f0a), a_trames(f0b)

    def peigne(f):
        """Proximité de chaque case à l'harmonique la plus proche de la hauteur de sa trame (0 hors voisement)."""
        W = np.zeros((nb, nt), np.float32)
        v = f > 40
        if not v.any():
            return W
        fv = f[v][None, :]
        h = np.maximum(np.rint(freqs[:, None] / fv), 1)
        s = np.maximum(0.02 * h * fv, larg_min)
        w = np.exp(-0.5 * ((freqs[:, None] - h * fv) / s) ** 2)
        w[h * fv > fmax] = 0
        W[:, v] = w
        return W
    Wa, Wb = peigne(fa), peigne(fb)
    eps = 1e-3
    Ma = (Wa + eps) / (Wa + Wb + 2 * eps)
    seul_a, seul_b = (fa > 40) & (fb <= 40), (fb > 40) & (fa <= 40)
    Ma[:, seul_a], Ma[:, seul_b] = 1.0, 0.0
    ya = librosa.istft(Z * Ma, hop_length=pas, win_length=n_fft, n_fft=n_fft, center=True, length=len(mix))
    yb = librosa.istft(Z * (1 - Ma), hop_length=pas, win_length=n_fft, n_fft=n_fft, center=True, length=len(mix))
    return ya.astype(np.float32), yb.astype(np.float32)


def etape_masque(a):
    """Masques harmoniques : hauteurs oracle (sources) et hauteurs des sorties de chaque Roformer homme/femme."""
    index = lire_json_u(os.path.join(SYNTH_TMP, 'index.json'))
    an = Analyse()
    res = {'variantes': {}}
    for var in ['oracle'] + list(a.get('depuis', [])):
        t0 = time.time()
        try:
            for e in index:
                d = os.path.join(SYNTH_TMP, e['id'])
                mix = lire_mono_u(os.path.join(d, 'mix.wav'))
                if var == 'oracle':
                    pa, pb = os.path.join(d, 's1.wav'), os.path.join(d, 's2.wav')
                else:
                    pa, pb = os.path.join(d, var, 'o1.wav'), os.path.join(d, var, 'o2.wav')
                f1, f2 = an.f0(lire_mono_u(pa)), an.f0(lire_mono_u(pb))
                y1, y2 = masque_harmonique(mix, f1, f2)
                ecrire_wav_u(os.path.join(d, f'masque_{var}', 'o1.wav'), y1)
                ecrire_wav_u(os.path.join(d, f'masque_{var}', 'o2.wav'), y2)
            res['variantes'][var] = {'duree_s': round(time.time() - t0, 1)}
            log(f'MASQUE {var} : {time.time() - t0:.1f} s')
        except Exception as ex:  # noqa: BLE001
            res['variantes'][var] = {'erreur': repr(ex)[-600:]}
            log(f'ÉCHEC MASQUE {var} : {ex!r}')
    ecrire_json_u(os.path.join(SYNTH, 'masque.json'), res)


# --- Mesures ----------------------------------------------------------------------------------------------------------

def si_sdr(ref, est):
    import numpy as np
    r = np.asarray(ref, np.float64) - np.mean(ref)
    e = np.asarray(est, np.float64) - np.mean(est)
    n = min(len(r), len(e))
    r, e = r[:n], e[:n]
    a = float(e @ r) / (float(r @ r) + 1e-12)
    t = a * r
    d = e - t
    return 10 * math.log10((float(t @ t) + 1e-12) / (float(d @ d) + 1e-12))


def etape_mesurer(a):
    """Pour chaque mélange et chaque candidat (deux sorties o1, o2) : SI-SDR à 44,1 kHz et à 16 kHz de chaque source à
    la sortie que lui donne la meilleure permutation, amélioration sur le mélange, identification par l'empreinte
    (chaque sortie comparée aux références à part des deux chanteurs), étiquette (Roformer homme/femme : la piste
    « male » va-t-elle au chanteur ?)."""
    import numpy as np
    from scipy.signal import resample_poly
    plan = U['plan']
    index = lire_json_u(os.path.join(SYNTH_TMP, 'index.json'))
    an = Analyse()
    emp_refs = {}
    for ch, fichier in plan['references'].items():
        emp_refs[ch] = an.empreinte(lire_mono_u(entree_u(fichier)))
    infos = {m['id']: m for m in plan['melanges']}
    a16 = lambda x: resample_poly(np.asarray(x, np.float64), 160, 441)   # noqa: E731
    lignes = []
    for e in index:
        mel = infos[e['id']]
        d = os.path.join(SYNTH_TMP, e['id'])
        s = [lire_mono_u(os.path.join(d, f's{k}.wav')) for k in (1, 2)]
        mix = lire_mono_u(os.path.join(d, 'mix.wav'))
        chanteurs = [src['chanteur'] for src in mel['sources']]
        genres = [src['genre'] for src in mel['sources']]
        base = [si_sdr(s[k], mix) for k in (0, 1)]
        base16 = [si_sdr(a16(s[k]), a16(mix)) for k in (0, 1)]
        candidats = sorted(c for c in os.listdir(d) if os.path.isdir(os.path.join(d, c))
                           and not any(c.startswith(x) for x in a.get('exclure', [])))
        for c in candidats:
            o = [os.path.join(d, c, f'o{k}.wav') for k in (1, 2)]
            if not all(os.path.exists(p) for p in o):
                continue
            try:
                o = [lire_mono_u(p)[:len(mix)] for p in o]
            except Exception as ex:  # noqa: BLE001  (piste en cours d'écriture par une autre carte)
                log(f'MESURE {e["id"]} {c} : illisible ({ex!r})')
                continue
            o = [np.pad(x, (0, len(mix) - len(x))) for x in o]
            scores = {}
            for perm in ((0, 1), (1, 0)):
                scores[perm] = [si_sdr(s[k], o[perm[k]]) for k in (0, 1)]
            perm = max(scores, key=lambda p: sum(scores[p]))
            sis = scores[perm]
            sis16 = [si_sdr(a16(s[k]), a16(o[perm[k]])) for k in (0, 1)]
            # identification : chaque sortie va au chanteur dont la référence lui ressemble le plus (score relatif)
            emp = [an.empreinte(x) if np.abs(x).max() > 1e-4 else np.zeros(192) for x in o]
            r = [emp_refs[ch] for ch in chanteurs]
            m = [[float(emp[j] @ r[k]) for k in (0, 1)] for j in (0, 1)]
            direct = (m[0][0] - m[0][1]) + (m[1][1] - m[1][0])       # o1 -> s1, o2 -> s2
            perm_emp = (0, 1) if direct >= 0 else (1, 0)
            # perm[k] = sortie de la source k ; perm_emp : (0,1) si o1 va à s1
            id_ok = (perm_emp == (0, 1)) == (perm == (0, 1))
            etiquette = None
            if c.startswith('hf_') and genres[0] != genres[1]:
                # o1 = « male » : juste si la sortie o1 est celle de la source d'homme
                k_homme = genres.index('homme')
                etiquette = perm[k_homme] == 0
            lignes.append({'melange': e['id'], 'condition': mel['condition'], 'paire': mel['paire'],
                           'mixte': genres[0] != genres[1], 'candidat': c,
                           'si_sdr': [round(x, 2) for x in sis], 'si_sdr_16k': [round(x, 2) for x in sis16],
                           'si_sdri': [round(sis[k] - base[k], 2) for k in (0, 1)],
                           'si_sdri_16k': [round(sis16[k] - base16[k], 2) for k in (0, 1)],
                           'permutation': list(perm), 'id_ok': bool(id_ok), 'id_marge': round(abs(direct), 3),
                           'etiquette_ok': etiquette})
        lignes.append({'melange': e['id'], 'condition': mel['condition'], 'paire': mel['paire'],
                       'mixte': genres[0] != genres[1], 'candidat': 'melange',
                       'si_sdr': [round(x, 2) for x in base], 'si_sdr_16k': [round(x, 2) for x in base16],
                       'si_sdri': [0.0, 0.0], 'si_sdri_16k': [0.0, 0.0]})
    ecrire_json_u(os.path.join(SYNTH, 'mesures.json'), {'lignes': lignes, 'references': plan['references']})
    resume = resumer(lignes)
    ecrire_json_u(os.path.join(SYNTH, 'resume.json'), resume)
    for c, r in sorted(resume.items()):
        log(f"MESURE {c:22s} " + ' | '.join(f"{k} {v['si_sdr']:+.1f} dB ({v['id_ok'] if v.get('id_ok') is not None else '-'})"
                                              for k, v in r.items()))


def resumer(lignes):
    """Moyennes par candidat et par condition (les deux voix de chaque mélange), et sur les duos mixtes seuls."""
    import numpy as np
    res = {}
    for l in lignes:
        for cle in (l['condition'], l['condition'] + ('_mixte' if l['mixte'] else '_meme_genre')):
            r = res.setdefault(l['candidat'], {}).setdefault(cle, {'n': 0, 'si_sdr': [], 'si_sdri': [], 'si_sdr_16k': [],
                                                                    'id': [], 'etiquette': []})
            r['n'] += 1
            r['si_sdr'] += l['si_sdr']
            r['si_sdri'] += l['si_sdri']
            r['si_sdr_16k'] += l['si_sdr_16k']
            if 'id_ok' in l:
                r['id'].append(l['id_ok'])
            if l.get('etiquette_ok') is not None:
                r['etiquette'].append(l['etiquette_ok'])
    for c in res:
        for k, r in res[c].items():
            res[c][k] = {'n': r['n'], 'si_sdr': round(float(np.mean(r['si_sdr'])), 2),
                         'si_sdri': round(float(np.mean(r['si_sdri'])), 2),
                         'si_sdr_16k': round(float(np.mean(r['si_sdr_16k'])), 2),
                         'id_ok': round(float(np.mean(r['id'])), 2) if r['id'] else None,
                         'etiquette_ok': round(float(np.mean(r['etiquette'])), 2) if r['etiquette'] else None}
    return res


# --- Vrai duo ---------------------------------------------------------------------------------------------------------

def niveaux_trames(x, lissage=15):
    return lisser_db(enveloppe_db(x), lissage)


def a_deux(xh, xf, ecart_db=15.0, sous_haut_db=25.0):
    """Trames (10 ms) où les deux pistes chantent : chacune au-dessus de son haut (95e centile) - sous_haut_db et de son
    seuil de silence, et à moins de ecart_db l'une de l'autre."""
    import numpy as np
    lh, lf = niveaux_trames(xh), niveaux_trames(xf)
    n = min(len(lh), len(lf))
    lh, lf = lh[:n], lf[:n]
    ah = (lh > max(np.percentile(lh, 95) - sous_haut_db, seuil_silence(lh)))
    af = (lf > max(np.percentile(lf, 95) - sous_haut_db, seuil_silence(lf)))
    return ah & af & (np.abs(lh - lf) < ecart_db), ah, af


def etape_separer_reel(a):
    """Vrai duo (partie de chanson du jeu privé) : voix isolée (Roformer de doubler), voix principale (karaoké) et sa
    version sèche (dereverb), puis chaque Roformer homme/femme sur la voix isolée et sur la voix principale sèche. Temps
    de chaque passe ; secondes chantées à deux d'après chaque modèle ; extrait d'écoute de a['duree_extrait'] s là où les
    deux chantent le plus ensemble (moyenne des modèles)."""
    import numpy as np
    import soundfile as sf
    x, sr = sf.read(entree_u(U['reel']['fichier']), dtype='float32', always_2d=True)
    assert sr == SR
    x = stereo(x / np.float32(U['reel'].get('gain_entree', 1.0)))
    duree = len(x) / SR
    res = {'duree_s': round(duree, 1), 'passes': {}}
    pistes = {'original': x}

    def passe(cle, fiche, entree, rec=None):
        r = Roformer(fiche, f'/tmp/reel_{cle}', rec)
        (p1, p2), d, noms = r.separer(entree, cle)
        res['passes'][cle] = {'modele': r.nom, 'chargement_s': r.chargement_s, 'separation_s': d,
                              'rtf': round(d / duree, 3), 'pistes': noms, 'precision': r.precision}
        log(f'REEL {cle} : {d:.1f} s pour {duree:.0f} s (rtf {d / duree:.2f})')
        del r
        return p1, p2

    pistes['voix'], pistes['musique'] = passe('voix', VOIX_KIM, x, U.get('recouvrement_voix', 4))
    pistes['lead'], pistes['choeurs'] = passe('karaoke', ROFORMERS['karaoke_becruily'], pistes['voix'])
    pistes['lead_sec'], _ = passe('dereverb', DEREVERB_U, pistes['lead'])
    for nom in a['candidats']:
        for src in ('voix', 'lead_sec'):
            try:
                pistes[f'{nom}_{src}_h'], pistes[f'{nom}_{src}_f'] = passe(f'{nom}_{src}', ROFORMERS[nom], pistes[src])
            except Exception as e:  # noqa: BLE001
                res['passes'][f'{nom}_{src}'] = {'erreur': repr(e)[-600:]}
                log(f'ÉCHEC REEL {nom} sur {src} : {e!r}')
    # secondes chantées à deux, sur la voix principale sèche (là où le karaoké laisse les deux chanteurs)
    res['a_deux'] = {}
    courbes = []
    for nom in a['candidats']:
        for src in ('lead_sec', 'voix'):
            cle = f'{nom}_{src}'
            if f'{cle}_h' not in pistes:
                continue
            deux, ah, af = a_deux(pistes[f'{cle}_h'].mean(1), pistes[f'{cle}_f'].mean(1))
            res['a_deux'][cle] = {'secondes_a_deux': round(float(deux.sum()) / 100, 1),
                                  'secondes_homme': round(float(ah.sum()) / 100, 1),
                                  'secondes_femme': round(float(af.sum()) / 100, 1)}
            if src == 'lead_sec':
                courbes.append(deux.astype(np.float64))
    # extrait d'écoute : fenêtre de duree_extrait s où les deux chantent le plus (moyenne des modèles)
    L = int(a.get('duree_extrait', 45) * 100)
    if courbes:
        n = min(len(c) for c in courbes)
        moy = np.mean([c[:n] for c in courbes], axis=0)
        cum = np.concatenate([[0.0], np.cumsum(moy)])
        somme = cum[L:] - cum[:-L] if n > L else np.array([cum[-1]])
        k0 = int(np.argmax(somme[::50]) * 50)
    else:
        k0 = 0
    a0, a1 = k0 * PAS, min(len(x), k0 * PAS + L * PAS)
    res['extrait'] = {'debut_s': round(a0 / SR, 2), 'fin_s': round(a1 / SR, 2),
                      'debut_dans_la_chanson_s': round(a0 / SR + U['reel'].get('debut_s', 0.0), 2)}
    gains = {}
    for cle, p in pistes.items():
        y = p[a0:a1]
        if cle not in ('original', 'musique', 'voix'):
            y = y.mean(1)
        gains[cle] = ecrire_flac_u(os.path.join(REEL, f'{cle}.flac'), y)
    res['gains_flac'] = gains
    ecrire_wav_u(os.path.join(REEL_TMP, 'extrait_lead_sec.wav'), pistes['lead_sec'][a0:a1].mean(1))
    for cle in [k for k in pistes if k.endswith('_lead_sec_h') or k.endswith('_lead_sec_f')]:
        ecrire_wav_u(os.path.join(REEL_TMP, f'{cle}.wav'), pistes[cle][a0:a1].mean(1))
    ecrire_json_u(os.path.join(REEL, 'reel.json'), res)
    log(f"REEL extrait {res['extrait']} ; à deux : {res['a_deux']}")


def etape_demo(a):
    """Extrait du vrai duo doublé, voix libres (références du jeu) : (1) voix principale sèche d'un bloc dans la voix de
    femme (chaîne actuelle quand le karaoké garde les deux chanteurs) ; (2) chaque chanteur séparé par le meilleur
    Roformer homme/femme des mesures, dans sa voix. Chaque piste convertie est ramenée au niveau de sa source."""
    import numpy as np
    resume = lire_json_u(os.path.join(SYNTH, 'resume.json'), {}) or {}

    def note(c):
        r = resume.get(c, {})
        v = [r[k]['si_sdr'] for k in ('unisson_mixte', 'octave_mixte', 'tierce_mixte', 'meme_air_mixte') if k in r]
        return float(np.mean(v)) if v else -99.0
    candidats = [c for c in a['candidats'] if os.path.exists(os.path.join(REEL_TMP, f'{c}_lead_sec_h.wav'))]
    if not candidats:
        log('ÉCHEC DEMO : aucune piste homme/femme du vrai duo')
        return
    meilleur = max(candidats, key=note)
    outils = trouver_outils()
    m = MoteurYing(outils, poids_ying(outils))
    refs = {g: m.preparer_ref(entree_u(f)) for g, f in U['plan']['cibles_demo'].items()}
    pas = int(U.get('pas_demo', 30))
    res = {'meilleur': meilleur, 'notes': {c: round(note(c), 2) for c in candidats}, 'chargement_s': m.chargement_s,
           'conversions': {}}

    def conv(cle, x, g):
        t0 = time.time()
        y = convertir_piste_u(m, refs[g], x, 0, pas)
        y = y * np.float32(rms_actif_u(x) / rms_actif_u(y)) if np.abs(y).max() > 0 else y
        res['conversions'][cle] = {'voix': g, 'duree_s': round(time.time() - t0, 1), 'duree_son_s': round(len(x) / SR, 1)}
        log(f'DEMO {cle} -> {g} : {time.time() - t0:.0f} s')
        return y
    lead = lire_mono_u(os.path.join(REEL_TMP, 'extrait_lead_sec.wav'))
    bloc = conv('bloc', lead, 'femme')
    h = conv('separe_homme', lire_mono_u(os.path.join(REEL_TMP, f'{meilleur}_lead_sec_h.wav')), 'homme')
    f = conv('separe_femme', lire_mono_u(os.path.join(REEL_TMP, f'{meilleur}_lead_sec_f.wav')), 'femme')
    gains = {'bloc': ecrire_flac_u(os.path.join(DEMO, 'bloc.flac'), bloc),
             'separe': ecrire_flac_u(os.path.join(DEMO, 'separe.flac'), h + f),
             'separe_homme': ecrire_flac_u(os.path.join(DEMO, 'separe_homme.flac'), h),
             'separe_femme': ecrire_flac_u(os.path.join(DEMO, 'separe_femme.flac'), f)}
    res['gains_flac'] = gains
    ecrire_json_u(os.path.join(DEMO, 'demo.json'), res)


ETAPES_U = {'generer': etape_generer, 'melanger': etape_melanger, 'separer_synth': etape_separer_synth,
            'parole': etape_parole, 'masque': etape_masque, 'mesurer': etape_mesurer, 'separer_reel': etape_separer_reel,
            'demo': etape_demo}


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in ETAPES_U:
        ETAPES_U[sys.argv[1]](json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})
    else:
        orchestrer()
