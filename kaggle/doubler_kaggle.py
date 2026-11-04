"""Doublage universel sur le GPU T4 gratuit de Kaggle : un seul noyau pour une ou plusieurs vidéos.

1. Séparation voix / reste de chaque vidéo : MelBand-Roformer de Kim (vocals_mel_band_roformer.ckpt, audio-separator
   0.48.0), mêmes réglages que sur le PC (entrée ramenée à un pic de 0,9 puis remise à l'échelle, reste = original - voix).
2. Mode : chant s'il y a une musique sous la voix et que la voix chante (notes tenues) ou est presque continue (rap),
   sinon parole.
3. Découpage aux silences de la voix (morceaux de 8 s au plus) ; décalage de hauteur de chaque morceau :
   chant  : -12, 0 ou +12 demi-tons seulement (la musique garde sa tonalité) ; un même chanteur garde un seul décalage
            sur toute la chanson (classe homme / femme d'après la hauteur, l'empreinte CAMPPlus ne départageant que la
            zone ambiguë 240-330 Hz, et seulement si doubler.py a envoyé des ancres de genre : réglage ancres,
            facultatif) ; dans un duo, un décalage par voix, changements de préférence aux pauses ;
   parole : rapprochement de la hauteur de parole de la voix cible (modèle 22 kHz : implicite ; modèle f0 : par voix).
4. Chant seulement, sauf réglage nettoyage à false : nettoyage de la voix isolée (banc d'essai du 04/11/2026,
   banc/RESULTATS.md) ; voix principale par le Roformer karaoké, chœurs = voix - voix principale (numpy), reverb
   d'origine retirée des deux par le Roformer dereverb. Le mode et l'octave restent décidés sur la voix isolée
   complète (leurs entrées calibrées ne changent pas).
5. Conversion, morceau par morceau, assemblage calé à l'échantillon près, porte sur les silences de chaque piste :
   chant  : YingMusic-SVC (réglage moteur_chant, défaut) ou Seed-VC V1 44,1 kHz f0 ; voix principale sèche et chœurs
            secs convertis à part avec les mêmes décalages, chacun ramené au niveau de sa source, puis additionnés ;
   parole : Seed-VC V1 22 kHz sur la voix isolée complète, comme avant.
   YingMusic et le nettoyage tournent chacun dans leur propre processus (script_complet : YingMusic a son propre
   paquet « modules », homonyme de celui de Seed-VC, et le GPU est rendu à la sortie). Si YingMusic échoue, Seed-VC
   44 kHz prend le relais ; si le nettoyage échoue, la voix isolée complète est convertie (chaîne d'avant).

doubler.py remplace __REGLAGES__ (JSON en base64) et SOURCE (sa propre source en base64 : le noyau tourne en carnet,
sans fichier, et en a besoin pour ses étapes à part), puis pousse ce noyau privé (GPU T4 + internet).
Entrée de chaque vidéo (jeu de données) : WAV flottant (doubler.py jusqu'au 04/11/2026) ou FLAC 24 bits, plus léger à
envoyer ; le FLAC est en entiers, doubler.py ramène donc la crête à -1 dBFS avant l'encodage et inscrit ce gain dans les
réglages (gain_entree) : le noyau le retire à la lecture, tout le reste travaille au niveau d'origine.
Sorties dans /kaggle/working/<id>/ pour chaque vidéo :
  voix_source.wav            voix isolée (44,1 kHz stéréo flottant, même longueur que l'entrée) ; si les réglages
                             demandent retour_flac : voix_source.flac (24 bits, crête à -1 dBFS) et
                             voix_source.flac.json (gain appliqué, que doubler.py retire au rapatriement)
  voix_convertie.flac        voix cible (44,1 kHz mono 24 bits, même nombre d'échantillons, calée sur l'échantillon 0)
  voix_convertie_<autre>.flac, voix_convertie_<N>pas.flac   variantes de comparaison (modèle, pas de diffusion)
  rapport.json               mode et mesures, morceaux (hauteur, voix, décalage), moteur, nettoyage, temps
  courbes.json               mélodie de la voix isolée et de la voix convertie, décalage appliqué (format 1 : une
                             valeur toutes les 20 ms ; lu par l'interface, à ne pas changer sans la prévenir)
et /kaggle/working/session.json (installation, séparation, nettoyage, chargement des modèles, temps total).
"""
import base64
import glob
import importlib.util
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import time

REGLAGES = json.loads(base64.b64decode('__REGLAGES__'))
DEPOT = 'https://github.com/Plachtaa/seed-vc.git'
COMMIT = '51383efd921027683c89e5348211d93ff12ac2a8'   # main au 20/04/2025, dernier commit du dépôt
SEEDVC = '/tmp/seed-vc'
SOUCHES = '/tmp/souches'
PAQUETS = ['audio-separator[cpu]==0.48.0', 'munch']
SORTIE = '/kaggle/working'
SR = 44100
PAS = 441   # 10 ms à 44,1 kHz (trames d'enveloppe et de F0 RMVPE)
DEBUT = float(os.environ.get('DOUBLER_DEBUT') or time.time())   # une étape à part garde l'horloge du noyau
# YingMusic-SVC (GiantAILab, déc. 2025 ; code MIT, poids CC BY-NC 4.0 : usage non commercial), comme au banc d'essai
YING = '/tmp/yingmusic'
YING_DEPOT = 'https://github.com/GiantAILab/YingMusic-SVC.git'
YING_COMMIT = '4974a80c6044c4557059548409379f6365129f88'   # main au 29/12/2025
YING_POIDS = ('GiantAILab/YingMusic-SVC', 'YingMusic-SVC-full.pt')
# Nettoyage (audio-separator 0.48.0, noms vérifiés par --list_models) : karaoké (le second en repli), puis dereverb
KARAOKE = ('mel_band_roformer_karaoke_becruily.ckpt', 'mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt')
DEREVERB = 'dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt'
MOTIF_KARAOKE = r'\((vocals|lead|lead vocals|karaoke)\)'
MOTIF_DEREVERB = r'\((noreverb|no reverb|no_reverb|dry)\)'
COMPLET = '/tmp/doubler_complet.py'   # copie de ce noyau sur disque, pour les étapes à part
SOURCE = '__SOURCE__'

SEPARER = r'''
import json, sys, time
import numpy as np
import soundfile as sf


def lire_entree(chemin, gain_entree=1.0):
    """Son d'entrée en flottant 32 bits, au niveau d'origine. Un FLAC (entiers 24 bits) a été ramené par doubler.py à
    une crête de -1 dBFS avant l'encodage : on divise par ce même gain (réglage gain_entree). Un WAV flottant (ancien
    doubler.py) arrive tel quel, avec un gain de 1."""
    x, sr = sf.read(chemin, dtype='float32', always_2d=True)
    if gain_entree != 1.0:
        x = x / np.float32(gain_entree)
    return x, sr


def ecrire_flac(chemin, y, sr):
    """FLAC 24 bits sans écrêtage (le FLAC est en entiers et la voix peut dépasser 0 dBFS) : crête ramenée à -1 dBFS,
    arrondi explicite au pas de 2^-23, puis le gain appliqué est rendu, pour que doubler.py le retire au rapatriement."""
    pic = float(np.abs(y).max())
    gain = 10 ** (-1 / 20) / pic if pic > 0 else 1.0
    q = np.clip(np.round(y.astype(np.float64) * (gain * 2 ** 23)), -2 ** 23, 2 ** 23 - 1).astype(np.int32)
    sf.write(chemin, q << 8, sr, subtype='PCM_24', format='FLAC')   # entiers 32 bits : libsndfile garde les 24 du haut
    return gain, pic


if __name__ == '__main__':
    from audio_separator.separator import Separator
    travaux, tmp = json.loads(sys.argv[1]), sys.argv[2]
    t0 = time.time()
    sep = Separator(model_file_dir='/tmp/modeles_sep', output_dir=tmp, output_format='WAV', use_soundfile=True,
                    normalization_threshold=1.0, sample_rate=44100,
                    mdxc_params={'segment_size': 801, 'override_model_segment_size': True, 'batch_size': 1, 'overlap': 4, 'pitch_shift': 0})
    sep.load_model(model_filename='vocals_mel_band_roformer.ckpt')
    print(f'SEPARATION modèle chargé en {time.time() - t0:.0f} s', flush=True)
    for t in travaux:
        t1 = time.time()
        gain_entree = float(t.get('gain_entree') or 1.0)
        x, sr = lire_entree(t['entree'], gain_entree)
        assert sr == 44100, sr
        if x.shape[1] == 1:
            x = np.repeat(x, 2, axis=1)
        x = np.ascontiguousarray(x[:, :2])
        pic = float(np.abs(x).max()) or 1.0
        gain = 0.9 / pic
        sf.write(f'{tmp}/entree.wav', x * gain, sr, subtype='FLOAT')
        sep.separate(f'{tmp}/entree.wav', custom_output_names={'Vocals': 'voix_brute', 'Other': 'autre_brut', 'Instrumental': 'autre_brut'})
        v, sr2 = sf.read(f'{tmp}/voix_brute.wav', dtype='float32', always_2d=True)
        assert sr2 == 44100, sr2
        n = min(len(v), len(x))
        if len(v) != len(x):
            print(f'attention : longueurs {len(v)} / {len(x)}', flush=True)
        voix = np.zeros_like(x)
        voix[:n] = v[:n, :2] / gain
        sf.write(t['voix'], voix, sr, subtype='FLOAT')
        retour = ''
        if t.get('voix_flac'):
            g, p = ecrire_flac(t['voix_flac'], voix, sr)
            with open(t['voix_flac'] + '.json', 'w') as f:
                json.dump({'gain_lineaire': g, 'gain_db': round(20 * np.log10(g), 4), 'pic': p, 'format': 'FLAC 24 bits',
                           'echantillons': int(len(voix)), 'canaux': int(voix.shape[1])}, f)
            retour = f', voix renvoyée en FLAC (gain {20 * np.log10(g):+.2f} dB)'
        sf.write(t['reste'], x - voix, sr, subtype='FLOAT')
        print(f"SEPARATION {t['id']} : {len(x) / sr:.1f} s séparées en {time.time() - t1:.0f} s ; entrée "
              f"{t['entree'].rsplit('.', 1)[-1].upper()} (gain {20 * np.log10(gain_entree):+.2f} dB retiré), pic entrée {pic:.3f}, "
              f'pics voix {np.abs(voix).max():.3f}, reste {np.abs(x - voix).max():.3f}' + retour, flush=True)
        with open(t['voix'] + '.duree', 'w') as f:
            f.write(f'{time.time() - t1:.1f}')
'''


def log(*a):
    print(f'[{time.time() - DEBUT:7.1f} s]', *a, flush=True)


def joignable(hote):
    try:
        socket.create_connection((hote, 443), timeout=10).close()
        return True
    except OSError:
        return False


def verifier_acces():
    """Kaggle ne donne le GPU et internet qu'aux comptes dont le numéro de téléphone est vérifié, même si le noyau les demande."""
    manque = [nom for nom, ok in (('GPU', any(f.startswith('nvidia') for f in os.listdir('/dev'))),
                                  ('internet', joignable('pypi.org') and joignable('huggingface.co'))) if not ok]
    if manque:
        log(f"ACCÈS REFUSÉ : {' et '.join(manque)} indisponible{'s' if len(manque) > 1 else ''} sur ce noyau. "
            "Vérifier le numéro de téléphone du compte (kaggle.com/settings, Phone verification), puis relancer.")
        sys.exit(3)


def lancer(*commande, **options):
    subprocess.run(commande, check=True, **options)


def pip(*paquets):
    lancer(sys.executable, '-m', 'pip', 'install', '-q', *paquets)


def installer(outils=None):
    """Le torch de Kaggle est gardé (torch/torchaudio 2.6.0 seulement si torchaudio n'a plus resample ou kaldi.fbank)."""
    essai = ("import torch, torchaudio, torchaudio.functional as F, torchaudio.compliance.kaldi as K; "
             "assert hasattr(F, 'resample') and hasattr(K, 'fbank'); "
             "print('torch', torch.__version__, 'cuda', torch.version.cuda, 'torchaudio', torchaudio.__version__, "
             "torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'SANS GPU')")
    if subprocess.run([sys.executable, '-c', essai]).returncode:
        log('torchaudio inutilisable avec le torch de Kaggle : installation de torch/torchaudio 2.6.0')
        pip('torch==2.6.0', 'torchaudio==2.6.0')
        subprocess.run([sys.executable, '-m', 'pip', 'uninstall', '-y', '-q', 'torchvision'])
        lancer(sys.executable, '-c', essai)
    # audio-separator : la même version que sur le PC ; [cpu] apporte onnxruntime, qu'il importe d'office (le Roformer
    # tourne en torch sur le GPU)
    hors_ligne = False
    if outils and os.path.isdir(os.path.join(outils, 'wheels')):
        hors_ligne = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--no-index', '--find-links',
                                     os.path.join(outils, 'wheels'), *PAQUETS]).returncode == 0
        log('outils : paquets installés depuis le montage, sans internet' if hors_ligne
            else 'outils : installation depuis le montage refusée, repli sur internet')
    if not hors_ligne:
        pip('munch')
    if not shutil.which('ffmpeg'):   # audio-separator refuse de démarrer sans la commande ffmpeg
        pip('imageio-ffmpeg')
        import imageio_ffmpeg
        os.symlink(imageio_ffmpeg.get_ffmpeg_exe(), '/usr/local/bin/ffmpeg')
    if not hors_ligne and subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'audio-separator[cpu]==0.48.0']).returncode:
        # une dépendance à compiler (samplerate, diffq) a échoué : le paquet seul et ce dont le Roformer a besoin
        log('audio-separator : installation complète refusée, reprise sans les dépendances à compiler')
        pip('--no-deps', 'audio-separator==0.48.0')
        for p in ('onnxruntime', 'beartype>=0.18.5,<0.19.0', 'ml_collections', 'rotary-embedding-torch>=0.6.1,<0.7.0',
                  'julius', 'pydub', 'resampy', 'requests', 'six', 'audioop-lts', 'onnx-weekly', 'onnx2torch-py313', 'samplerate', 'diffq'):
            if subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', p]).returncode:
                log(f'  {p} : non installé')
    manquants = [paquet for module, paquet in (('librosa', 'librosa'), ('soundfile', 'soundfile'), ('einops', 'einops'),
                                               ('yaml', 'pyyaml'), ('scipy', 'scipy'), ('transformers', 'transformers'),
                                               ('huggingface_hub', 'huggingface_hub'), ('tqdm', 'tqdm'), ('matplotlib', 'matplotlib'))
                 if importlib.util.find_spec(module) is None]
    if manquants:
        log('installation :', ' '.join(manquants))
        pip(*manquants)
    # length_regulator.py importe dac.nn.quantize.VectorQuantize sans s'en servir (vector_quantize: false) : une souche suffit
    if importlib.util.find_spec('dac') is None:
        os.makedirs(os.path.join(SOUCHES, 'dac', 'nn'), exist_ok=True)
        for f, texte in (('dac/__init__.py', ''), ('dac/nn/__init__.py', ''),
                         ('dac/nn/quantize.py', 'class VectorQuantize:\n    def __init__(self, *a, **k):\n'
                                                '        raise RuntimeError("souche dac : VectorQuantize non prévu")\n')):
            with open(os.path.join(SOUCHES, f), 'w') as fh:
                fh.write(texte)
        sys.path.insert(0, SOUCHES)
        os.environ['PYTHONPATH'] = SOUCHES + os.pathsep + os.environ.get('PYTHONPATH', '')
    from importlib.metadata import version
    log('versions :', ', '.join(f'{p} {version(p)}' for p in ('numpy', 'librosa', 'transformers', 'huggingface_hub',
                                                              'soundfile', 'audio-separator')),
        '| python', sys.version.split()[0])


def corriger(chemin, remplacements):
    with open(chemin) as f:
        texte = f.read()
    neuf = texte
    for avant, apres in remplacements:
        neuf = neuf.replace(avant, apres)
    if neuf != texte:
        with open(chemin, 'w') as f:
            f.write(neuf)
        log('corrigé :', os.path.relpath(chemin, SEEDVC))


def cloner(outils=None):
    archive = os.path.join(outils, 'seed-vc.tar') if outils else None
    fiche = lire_outils(outils)
    if archive and os.path.exists(archive) and fiche.get('commit') == COMMIT:
        lancer('tar', '-xf', archive, '-C', os.path.dirname(SEEDVC))
        sha = COMMIT
        log('outils : Seed-VC', sha, 'depuis le montage')
    else:
        if outils:
            log('outils : pas d\'archive Seed-VC au bon commit dans le montage, clonage par internet')
        os.makedirs(SEEDVC, exist_ok=True)
        lancer('git', 'init', '-q', SEEDVC)
        lancer('git', '-C', SEEDVC, 'remote', 'add', 'origin', DEPOT)
        if subprocess.run(['git', '-C', SEEDVC, 'fetch', '-q', '--depth', '1', 'origin', COMMIT]).returncode == 0:
            lancer('git', '-C', SEEDVC, 'checkout', '-q', 'FETCH_HEAD')
        else:
            log(f'commit {COMMIT[:10]} introuvable : dernier état de main')
            lancer('git', '-C', SEEDVC, 'fetch', '-q', '--depth', '1', 'origin', 'main')
            lancer('git', '-C', SEEDVC, 'checkout', '-q', 'FETCH_HEAD')
        sha = subprocess.run(['git', '-C', SEEDVC, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        log('Seed-VC', sha)
    from importlib.metadata import version
    majeur, mineur = (int(x) for x in version('transformers').split('.')[:2])
    corriger(os.path.join(SEEDVC, 'inference.py'),
             [('head_mask=None,', '')] + ([('torch_dtype=torch.float16', 'dtype=torch.float16')] if (majeur, mineur) >= (4, 56) else []))
    corriger(os.path.join(SEEDVC, 'modules/bigvgan/bigvgan.py'),
             [('proxies: Optional[Dict],', 'proxies: Optional[Dict] = None,'), ('resume_download: bool,', 'resume_download: bool = None,'),
              ('proxies=proxies,', ''), ('resume_download=resume_download,', '')])
    return sha


def trouver_outils():
    """Le dossier du noyau « outils » monté par kernel_sources (repéré par outils.json), sinon None."""
    fiches = glob.glob('/kaggle/input/**/outils.json', recursive=True)
    return os.path.dirname(fiches[0]) if fiches else None


def lire_outils(outils):
    try:
        with open(os.path.join(outils, 'outils.json')) as f:
            return json.load(f)
    except (TypeError, OSError, ValueError):
        return {}


def poids_depuis_outils(outils):
    """Poids des modèles depuis le montage : checkpoints Seed-VC (cache Hugging Face, liens compris, d'où l'archive) et
    MelBand-Roformer (fichiers liés). Ce qui manquerait sera téléchargé normalement."""
    if not outils:
        return
    archive = os.path.join(outils, 'checkpoints.tar')
    if os.path.exists(archive):
        lancer('tar', '-xf', archive, '-C', SEEDVC)
        log('outils : poids Seed-VC, Whisper, BigVGAN, RMVPE, CAMPPlus depuis le montage')
    sep = os.path.join(outils, 'modeles_sep')
    if os.path.isdir(sep):
        os.makedirs('/tmp/modeles_sep', exist_ok=True)
        for f in os.listdir(sep):
            if not os.path.exists(os.path.join('/tmp/modeles_sep', f)):
                os.symlink(os.path.join(sep, f), os.path.join('/tmp/modeles_sep', f))
        log('outils : MelBand-Roformer depuis le montage')


def preparer_outils():
    """Noyau « outils », lancé une fois : wheels de ce que pip installerait (Python de Kaggle), Seed-VC et YingMusic-SVC
    aux commits figés et poids de tous les modèles (séparation, nettoyage, conversion), dans /kaggle/working, pour être
    monté par le noyau de doublage (kernel_sources). Tout est téléchargé ici (GitHub, Hugging Face) : rien ne part du PC."""
    w = SORTIE
    rapport_pip = '/tmp/rapport_pip.json'
    lancer(sys.executable, '-m', 'pip', 'install', '-q', '--dry-run', '--report', rapport_pip, *PAQUETS)
    with open(rapport_pip) as f:
        a_installer = [(i['metadata']['name'], i['metadata']['version']) for i in json.load(f).get('install', [])]
    os.makedirs(os.path.join(w, 'wheels'), exist_ok=True)
    for nom, version in a_installer:
        if subprocess.run([sys.executable, '-m', 'pip', 'wheel', '-q', '--no-deps', '-w', os.path.join(w, 'wheels'),
                           f'{nom}=={version}']).returncode:
            log(f'outils : wheel impossible pour {nom}=={version}')
    log(f'outils : {len(a_installer)} paquets à installer, wheels : {len(os.listdir(os.path.join(w, "wheels")))}')
    installer(w)   # vérifie l'installation hors ligne depuis ces wheels
    sha = cloner()
    lancer('tar', '-cf', os.path.join(w, 'seed-vc.tar'), '--exclude=seed-vc/.git', '--exclude=seed-vc/checkpoints',
           '-C', os.path.dirname(SEEDVC), os.path.basename(SEEDVC))
    os.environ['TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD'] = '1'
    import torch
    charger = torch.load
    torch.load = lambda *a, **k: charger(*a, **{'weights_only': False, **k})
    os.chdir(SEEDVC)
    sys.path.insert(0, SEEDVC)
    import inference as sv
    from argparse import Namespace
    for f0 in (True, False):
        sv.load_models(Namespace(f0_condition=f0, checkpoint=None, config=None, fp16=True))
    # YingMusic-SVC : dépôt au commit figé (archive, bigvgan.py corrigé), poids à part (le noyau de doublage les lit dans
    # le montage, sans copie) ; ses poids auxiliaires (Whisper-small, BigVGAN 44 kHz, RMVPE, CAMPPlus) sont ceux de
    # Seed-VC. Essai de chargement dans un processus à part, avant l'archive des checkpoints (s'il y télécharge encore
    # quelque chose, l'archive l'aura).
    installer_ying()
    lancer('tar', '-cf', os.path.join(w, 'yingmusic.tar'), '--exclude=yingmusic/.git', '--exclude=yingmusic/checkpoints',
           '--exclude=yingmusic/.pret', '-C', os.path.dirname(YING), os.path.basename(YING))
    from huggingface_hub import hf_hub_download
    os.makedirs(os.path.join(w, 'yingmusic'), exist_ok=True)
    poids_ying = os.path.join(w, 'yingmusic', YING_POIDS[1])
    shutil.copy2(hf_hub_download(repo_id=YING_POIDS[0], filename=YING_POIDS[1], cache_dir='/tmp/hf_ying'), poids_ying)
    shutil.rmtree('/tmp/hf_ying', ignore_errors=True)
    try:
        essai = etape_a_part('ying', {'checkpoint': poids_ying, 'resultat': '/tmp/ying/essai.json', 'pistes': []})
    except Exception as e:  # noqa: BLE001 — les outils restent utiles ; doubler se repliera sur Seed-VC
        essai = {'erreur': repr(e)}
    log('outils : essai de YingMusic-SVC : ' + json.dumps({k: essai.get(k) for k in ('chargement_s', 'installation', 'erreur', 'code')},
                                                          ensure_ascii=False))
    lancer('tar', '-cf', os.path.join(w, 'checkpoints.tar'), '-C', SEEDVC, 'checkpoints')
    # Séparation (MelBand-Roformer) et nettoyage (karaoké, dereverb) : un processus par modèle (mémoire rendue)
    for modele in ('vocals_mel_band_roformer.ckpt', KARAOKE[0], DEREVERB):
        lancer(sys.executable, '-c', "from audio_separator.separator import Separator; "
               f"Separator(model_file_dir='/tmp/modeles_sep', output_dir='/tmp').load_model(model_filename='{modele}')")
    shutil.copytree('/tmp/modeles_sep', os.path.join(w, 'modeles_sep'))
    import platform
    sep = os.path.join(w, 'modeles_sep')
    fiche = {'commit': sha, 'python': platform.python_version(), 'torch': torch.__version__, 'paquets': PAQUETS,
             'wheels': sorted(os.listdir(os.path.join(w, 'wheels'))), 'date': time.strftime('%Y-%m-%d %H:%M'),
             'yingmusic': {'commit': YING_COMMIT, 'poids': f'yingmusic/{YING_POIDS[1]}',
                           'essai': {k: essai.get(k) for k in ('chargement_s', 'installation', 'erreur', 'code')}},
             'modeles_sep': sorted(os.listdir(sep)),
             'tailles_mo': {**{f: round(os.path.getsize(os.path.join(w, f)) / 1e6) for f in
                               ('seed-vc.tar', 'checkpoints.tar', 'yingmusic.tar', f'yingmusic/{YING_POIDS[1]}')},
                            'modeles_sep': round(sum(os.path.getsize(os.path.join(sep, f)) for f in os.listdir(sep)) / 1e6)}}
    with open(os.path.join(w, 'outils.json'), 'w') as f:
        json.dump(fiche, f, ensure_ascii=False, indent=1)
    log('OUTILS prêts : ' + json.dumps(fiche, ensure_ascii=False))


def trouver(nom):
    chemins = glob.glob(f'/kaggle/input/**/{nom}', recursive=True)
    if not chemins:
        log(f'FICHIER ABSENT du jeu de données : {nom} (présents : '
            f"{sorted(os.path.basename(p) for p in glob.glob('/kaggle/input/**/*.*', recursive=True))})")
        sys.exit(4)
    return chemins[0]


# --- Mesures sur le signal (numpy / scipy seuls : servent aussi au calibrage sur le PC) ------------------------------

def enveloppe_db(x, fen=1764):
    """Niveau RMS (dBFS) sur 40 ms, toutes les 10 ms ; la trame k est centrée sur l'échantillon k * PAS."""
    import numpy as np
    n = len(x) // PAS + 1
    p = np.pad(x.astype(np.float64) ** 2, (fen // 2, fen // 2 + PAS))
    c = np.concatenate([[0.0], np.cumsum(p)])
    i = np.arange(n) * PAS
    return 10 * np.log10((c[i + fen] - c[i]) / fen + 1e-12)


def lisser_db(env, trames=15):
    import numpy as np
    lin = 10 ** (env / 10)
    return 10 * np.log10(np.convolve(lin, np.ones(trames) / trames, mode='same') + 1e-12)


def seuil_silence(env):
    import numpy as np
    return max(float(np.percentile(env, 95)) - 40, -60.0)


def decouper(lisse, n, coeur_max, coeur_min, seuil):
    """Coupes (en échantillons) : chaque morceau fait au plus coeur_max ; la coupe tombe au milieu du dernier silence
    de la fenêtre permise, sinon au creux d'énergie le plus tardif (à 3 dB du minimum)."""
    import numpy as np
    coupes, pos = [0], 0
    while n - pos > coeur_max:
        k_lo, k_hi = -(-(pos + coeur_min) // PAS), (pos + coeur_max) // PAS
        fen = lisse[k_lo:k_hi + 1]
        muets = np.where(fen < seuil)[0]
        if len(muets):
            fin = muets[-1]
            debut = fin
            while debut > 0 and fen[debut - 1] < seuil:
                debut -= 1
            k = k_lo + (debut + fin) // 2
        else:
            k = k_lo + np.where(fen <= fen.min() + 3)[0][-1]
        pos = int(k * PAS)
        coupes.append(pos)
    coupes.append(n)
    return coupes


def rampe(d, largeur):
    import numpy as np
    r = np.clip((d + largeur / 2) / largeur, 0, 1)
    return np.sin(np.pi / 2 * r) ** 2


def score_tonal(y, sr=SR):
    """Part de l'énergie du reste portée par des raies tenues (notes) : médiane sur les trames actives.
    Spectre lissé dans le temps (médiane sur ~0,2 s : les notes tiennent, bruits et chocs s'effacent), puis raies à plus
    de 10 dB au-dessus du plancher local (médiane sur ~330 Hz), entre 80 Hz et 5 kHz."""
    import numpy as np
    import scipy.ndimage
    import scipy.signal
    y22 = scipy.signal.resample_poly(y.astype(np.float64), 1, 2) if sr == 44100 else y.astype(np.float64)
    f, _, z = scipy.signal.stft(y22, fs=22050, nperseg=2048, noverlap=1536, boundary=None, padded=False)
    bande = (f >= 80) & (f <= 5000)
    p = (np.abs(z[bande]) ** 2).astype(np.float32)
    del z
    h = scipy.ndimage.median_filter(p, size=(1, 9))
    niv = 10 * np.log10(h + 1e-12)
    plancher = scipy.ndimage.median_filter(niv, size=(31, 1))
    raies = (niv - plancher) > 10
    part = (h * raies).sum(0) / (h.sum(0) + 1e-12)
    e = 10 * np.log10(p.mean(0) + 1e-12)
    actives = e > max(float(np.percentile(e, 95)) - 40, -100)
    return float(np.median(part[actives])) if actives.any() else 0.0


def mesures_musique(voix, reste):
    """Niveau du reste pendant la voix (médiane, dB par rapport à la voix), part du temps où le reste sonne, tonalité."""
    import numpy as np
    ev, er = enveloppe_db(voix), enveloppe_db(reste)
    n = min(len(ev), len(er))
    ev, er = ev[:n], er[:n]
    voix_active = lisser_db(ev) > seuil_silence(ev)
    rel = float(np.median(er[voix_active] - ev[voix_active])) if voix_active.any() else -99.0
    reste_actif = float((lisser_db(er) > max(float(np.percentile(er, 95)) - 30, -55)).mean())
    return {'reste_sous_voix_db': round(rel, 1), 'reste_actif': round(reste_actif, 3), 'tonalite_reste': round(score_tonal(reste), 3),
            'voix_active': round(float(voix_active.mean()), 3), 'niveau_voix_dbfs': round(float(np.percentile(ev, 95)), 1),
            'niveau_reste_dbfs': round(float(np.percentile(er, 95)), 1)}


def mesures_chant(f0, actif, demi=20, tol=2.0):
    """Part des trames voisées tenues : 0,4 s entièrement voisées dont la hauteur reste dans 2 demi-tons (notes chantées,
    vibrato compris). Calibré avec RMVPE : chant 0,20-0,22 (une chanteuse, une voix d'homme chantée), parole 0,01-0,08."""
    import numpy as np
    n = min(len(f0), len(actif))
    v = (f0[:n] > 1) & actif[:n]
    if v.sum() < 50:
        return {'tenue': 0.0, 'voise_actif': 0.0}
    st = 12 * np.log2(np.maximum(f0[:n], 1) / 100)
    c = np.concatenate([[0], np.cumsum(v)])
    w = 2 * demi + 1
    plein = np.zeros(n, bool)
    plein[demi:n - demi] = (c[w:] - c[:-w]) == w
    from numpy.lib.stride_tricks import sliding_window_view
    fen = sliding_window_view(st, w)
    etendue = np.zeros(n)
    etendue[demi:n - demi] = fen.max(1) - fen.min(1)
    tenu = plein & (etendue <= tol)
    return {'tenue': round(float(tenu.sum() / v.sum()), 3), 'voise_actif': round(float(v.sum() / max(actif[:n].sum(), 1)), 3)}


def decider_mode(m, R):
    """Chant s'il y a une musique sous la voix ET que la voix chante (notes tenues) ou est presque continue (rap) ;
    sinon parole (dialogue, même sur une musique de film)."""
    musique = ((m['tonalite_reste'] >= R['seuil_tonalite'] and m['reste_sous_voix_db'] >= R['seuil_reste_db'])
               or (m['reste_sous_voix_db'] >= -6 and m['reste_actif'] >= 0.8))
    # Le rap (voix presque continue, peu de notes tenues) n'est retenu que sur un fond tonal : un dialogue dense sur un
    # fond de film (un dialogue de série : voix active 0,67, tonalité 0,30, aucune note tenue) passait sinon pour du rap.
    chante = (m['tenue'] >= R['seuil_tenue']
              or (m['voix_active'] >= R['voix_dense'] and m['tonalite_reste'] >= R['seuil_tonalite']))
    return ('chant' if musique and chante else 'parole'), {'musique': bool(musique), 'voix_chantee': bool(chante)}


def kmeans_cos(e, k, iters=60):
    """k-moyennes sphériques (lignes de e normées), initialisation par le point le plus éloigné : déterministe."""
    import numpy as np
    m = e.mean(0)
    c = [e[np.argmin(e @ (m / (np.linalg.norm(m) + 1e-9)))]]
    while len(c) < k:
        sims = np.max(np.stack([e @ ci for ci in c]), axis=0)
        c.append(e[np.argmin(sims)])
    c = np.stack(c)
    lab = None
    for _ in range(iters):
        nouv = np.argmax(e @ c.T, axis=1)
        if lab is not None and (nouv == lab).all():
            break
        lab = nouv
        for j in range(k):
            if (lab == j).any():
                mj = e[lab == j].mean(0)
                c[j] = mj / (np.linalg.norm(mj) + 1e-9)
    return lab, c


def silhouette_cos(e, lab):
    import numpy as np
    d = 1 - e @ e.T
    s = []
    for i in range(len(e)):
        meme = (lab == lab[i])
        meme[i] = False
        if not meme.any():
            s.append(0.0)
            continue
        a = d[i, meme].mean()
        b = min(d[i, lab == j].mean() for j in set(lab.tolist()) if j != lab[i])
        s.append((b - a) / max(a, b, 1e-9))
    return float(np.mean(s))


def mediane_ponderee(v, w):
    import numpy as np
    v, w = np.asarray(v, float), np.asarray(w, float)
    o = np.argsort(v)
    c = np.cumsum(w[o])
    return float(v[o][np.searchsorted(c, c[-1] / 2)])


def grouper(segs, kmax, R):
    """Regroupe les morceaux par voix (empreinte CAMPPlus). Rend k, indices retenus, centres, silhouette, essais."""
    import numpy as np
    elig = [i for i, s in enumerate(segs) if not s['muet'] and s.get('emb') is not None and s['voise_s'] >= R['voise_min_s']]
    essais = {}
    if len(elig) < 4:
        return 1, elig, None, None, essais
    e = np.stack([segs[i]['emb'] for i in elig])
    meilleur = (1, None, None, -1.0)
    for k in range(2, min(kmax, len(elig) // 2) + 1):
        lab, c = kmeans_cos(e, k)
        if len(set(lab.tolist())) < k:
            continue
        s = silhouette_cos(e, lab)
        essais[k] = round(s, 3)
        if s > meilleur[3]:
            meilleur = (k, lab, c, s)
    return meilleur[0], elig, meilleur[2], meilleur[3], essais


def distance_oct(f, centre, d):
    import numpy as np
    return abs(np.log2(f / centre) + d / 12)


def meilleure_octave(f, centre, precedent=None, marge=0.0):
    """Le décalage (-12, 0 ou +12) qui rapproche le plus la hauteur f du centre de chant de la voix cible ; avec une
    marge (hystérésis, en octaves), on garde le décalage précédent tant qu'il n'est pas nettement moins bon."""
    best = min((-12, 0, 12), key=lambda d: distance_oct(f, centre, d))
    if precedent is not None and distance_oct(f, centre, precedent) - distance_oct(f, centre, best) < marge:
        return precedent
    return best


# --- Octave en chant : une voix = un décalage (règle validée hors ligne : octave/decision.py, octave/rejouer.py,
# octave/RESULTATS.md ; les fonctions ci-dessous en sont la copie exacte, en Python seul) ---------------------------------
# Classe d'une voix : hauteur médiane < 240 Hz -> homme, > 330 Hz -> femme ; entre les deux, empreinte (genre_score
# >= +0,03 homme ; <= -0,10 femme, mais seulement à partir de 281 Hz : plus bas, une voix d'homme éraillée a aussi une
# empreinte de femme), puis notes graves (10e centile < 190 Hz -> homme), sinon la hauteur seule (homme sous 281 Hz,
# milieu géométrique de la zone, femme au-dessus : frontière fixe, qui ne dépend plus de la voix cible depuis la v2.2).
# Duo seulement si deux groupes d'empreintes nettement séparés, chantant chacun en sections suivies, et de décalages
# différents ; changement de voix par Viterbi, moins cher à une pause, sections de 6 s au moins.

OCTAVE_DEFAUT = {
    'zone_basse_hz': 240.0,          # en dessous : voix d'homme d'après la hauteur seule
    'zone_haute_hz': 330.0,          # au-dessus : voix de femme d'après la hauteur seule
    'genre_homme': 0.03,             # genre_score médian d'une voix, à partir duquel on la tient pour un homme
    'genre_femme': -0.10,            # ... en deçà duquel on la tient pour une femme
    'femme_empreinte_min_hz': 281.0,  # ... et seulement à partir de cette hauteur (√(240 x 330)) : en dessous, une
                                      # empreinte de femme peut venir d'une voix d'homme éraillée
    'grave_p10_hz': 190.0,           # 10e centile des hauteurs de morceaux sous lequel la voix est d'homme
    'frontiere_hz': 281.0,           # faute d'indice sûr : homme en dessous, femme au-dessus (√(240 x 330), milieu
                                     # géométrique de la zone ; ne dépend pas de la voix cible)
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


def quantile_pondere(v, w, q):
    paires = sorted(zip(v, w))
    tot = sum(b for _, b in paires)
    c = 0.0
    for a, b in paires:
        c += b
        if c >= q * tot:
            return a
    return paires[-1][0]


def classe_hauteur(f, R):
    """Classe d'une voix sans indice sûr, d'après sa hauteur médiane seule (frontière fixe, hors voix cible)."""
    return 'homme' if f < R['frontiere_hz'] else 'femme'


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
    cote = '<' if classe_hauteur(f, R) == 'homme' else '>='
    return None, (f"{zone}, {empreinte} : d'après la hauteur seule ({f:.0f} Hz {cote} {R['frontiere_hz']:.0f}, "
                  f"{classe_hauteur(f, R)})")


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
    R = {**OCTAVE_DEFAUT, **(reglages or {})}
    dec_classe = {'homme': meilleure_octave(R['centre_homme_hz'], centre),
                  'femme': meilleure_octave(R['centre_femme_hz'], centre)}
    info = {'regle': 'v2.2 (une voix = un décalage ; empreinte de femme ignorée sous 281 Hz ; hauteur seule : '
                     'frontière fixe de 281 Hz)', 'centre_chant_cible_hz': round(centre, 1),
            'decalage_par_classe': dec_classe,
            'zone_ambigue_hz': [R['zone_basse_hz'], R['zone_haute_hz']], 'frontiere_hauteur_seule_hz': R['frontiere_hz'],
            'seuils_genre': {'homme': R['genre_homme'], 'femme': R['genre_femme']}}
    actifs_i = [i for i, s in enumerate(morceaux) if not s.get('muet') and s.get('voise_s', 0) > 0]
    if not actifs_i:
        return ([{'decalage': 0, 'voix': None, 'base': muet_ou_sourd(s)} for s in morceaux],
                {**info, 'voix': 0, 'voix_principale': {'classe': None, 'raison': 'aucun morceau chanté', 'decalage': 0}})

    # 1. La chanson entière comme une seule voix
    tout = [morceaux[i] for i in actifs_i]
    mg = mesurer_voix(tout, R)
    classe_g, raison_g = classer(mg, R)
    dec_g = dec_classe[classe_g or classe_hauteur(mg['f0_med_hz'], R)] if mg.get('f0_med_hz') else 0
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
                    d = dec_classe[classe_hauteur(m['f0_med_hz'], R)] if m.get('f0_med_hz') else dec_g
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


def decider_chant(segs, R, centre, coupes_son=None):
    """Octave en chant (-12, 0, +12 : la musique garde sa tonalité) : un même chanteur garde un seul décalage sur toute
    la chanson ; on ne change d'octave que lorsque la voix change vraiment (duo), de préférence à une pause.
    Le genre de chaque passage (genre_score) est la projection de son empreinte CAMPPlus sur l'axe homme - femme de deux
    ancres (octave/ancres.py) ; il ne départage que la zone ambiguë (240-330 Hz : dans un duo homme/femme, le
    chanteur et la chanteuse peuvent y chanter tous deux ; une voix d'homme éraillée y passe pour une femme). Les
    ancres sont facultatives (R['ancres'] absent ou None) : sans elles, aucun genre_score, donc ni empreinte dans la
    zone ambiguë (notes graves, sinon la hauteur seule : homme sous 281 Hz, femme au-dessus) ni recherche de duo.
    Décalage d'une classe : celui qui rapproche son centre de chant typique (homme 250 Hz, femme 420 Hz) du centre de
    chant de la voix cible (2 x sa hauteur de parole) : pour une voix cible d'homme (centre vers 200 Hz), 0 pour un
    chanteur et -12 pour une chanteuse.
    coupes_son : instants (s) des coupes tombées en plein son (les autres sont des pauses)."""
    import numpy as np
    ancres = R.get('ancres')
    if ancres:
        h, f = np.asarray(ancres['homme']), np.asarray(ancres['femme'])
        for s in segs:
            if not s['muet'] and s.get('emb') is not None:
                s['genre_score'] = round(float(s['emb'] @ h - s['emb'] @ f), 3)
    res, info = decider_octave(segs, centre, {k: R[k] for k in OCTAVE_DEFAUT if k in R}, coupes_son)
    if not ancres:
        info['empreinte'] = ('sans ancres de genre : décision sans empreinte (hauteur, notes graves, sinon frontière '
                             "d'octave de la hauteur seule), sans recherche de duo")
    for s, r in zip(segs, res):
        s['decalage'], s['base'] = r['decalage'], r['base']
        if r['voix'] is not None:
            s['voix'] = r['voix']
        else:
            s.pop('voix', None)
    vp = info['voix_principale']   # anciennes clés, pour comparer avec les rapports d'avant le 04/11
    info.update({'decalage_par_genre': info['decalage_par_classe'], 'f0_mediane_globale_hz': vp.get('f0_med_hz'),
                 'voix_seule': vp.get('classe') if info['voix'] == 1 else None})
    return info


def decider_parole(segs, f0_cible, R):
    """Décalage libre (demi-tons, au demi-ton près) par voix : la médiane de chaque voix rejoint la hauteur de parole
    de la voix cible."""
    import numpy as np
    actifs = [s for s in segs if not s['muet'] and s['f0_med']]
    info = {'f0_parole_cible_hz': round(f0_cible, 1)}
    if not actifs:
        for s in segs:
            s['decalage'], s['base'] = 0.0, 'muet'
        return info
    globale = mediane_ponderee([s['f0_med'] for s in actifs], [max(s['voise_s'], 0.01) for s in actifs])
    dec = lambda f: float(np.clip(np.round(2 * 12 * np.log2(f0_cible / f)) / 2, -24, 24))
    info['f0_mediane_globale_hz'] = round(globale, 1)
    k, elig, centres, sil, essais = grouper(segs, 4, R)
    info.update({'silhouettes': {str(a): b for a, b in essais.items()}, 'voix': 1})
    if k >= 2 and sil is not None and sil >= R['sil_parole']:
        lab = np.argmax(np.stack([segs[i]['emb'] for i in elig]) @ centres.T, axis=1)
        meds = []
        for j in range(k):
            ii = [elig[t] for t in range(len(elig)) if lab[t] == j]
            meds.append(mediane_ponderee([segs[i]['f0_med'] for i in ii], [segs[i]['voise_s'] for i in ii]) if ii else globale)
        info.update({'voix': k, 'voix_f0_medianes_hz': [round(m, 1) for m in meds], 'voix_decalages': [dec(m) for m in meds]})
    for s in segs:
        if s['muet']:
            s['decalage'], s['base'] = 0.0, 'muet'
        elif info['voix'] >= 2 and s.get('emb') is not None:
            j = int(np.argmax(centres @ s['emb']))
            s['voix'], s['decalage'], s['base'] = j + 1, info['voix_decalages'][j], 'voix'
        else:
            s['decalage'], s['base'] = dec(globale), 'voix seule'
    return info


def chrono(t):
    return f'{int(t // 60):02d}:{t % 60:04.1f}'


def frise(segs):
    """Lignes lisibles : morceaux consécutifs de même décalage et de même voix fusionnés."""
    lignes, cur = [], None
    for s in segs:
        if s['muet']:
            continue
        cle = (s['decalage'], s.get('voix'))
        if cur and cur['cle'] == cle and abs(cur['fin'] - s['debut_s']) < 1.5:
            cur['fin'] = s['fin_s']
            cur['f0'].append((s['f0_med'], s['voise_s']))
            cur['bases'].add(s['base'])
        else:
            cur = {'cle': cle, 'debut': s['debut_s'], 'fin': s['fin_s'], 'f0': [(s['f0_med'], s['voise_s'])], 'bases': {s['base']}}
            lignes.append(cur)
    sortie = []
    for l in lignes:
        f = [(a, b) for a, b in l['f0'] if a]
        med = mediane_ponderee([a for a, _ in f], [max(b, 0.01) for _, b in f]) if f else 0
        voix = f"voix {l['cle'][1]}" if l['cle'][1] else '       '
        sortie.append(f"{chrono(l['debut'])}–{chrono(l['fin'])}  {voix}  f0 {med:4.0f} Hz  → {l['cle'][0]:+g} dt  ({', '.join(sorted(l['bases']))})")
    return sortie


def fenetre_comparaison(segs, duree):
    """La fenêtre d'environ `duree` s la plus chantée (bonus si deux voix y passent), bornée par des coupes."""
    act = [s for s in segs if not s['muet']]
    meilleur = None
    for i, a in enumerate(act):
        choisis = [b for b in act[i:] if b['fin_s'] - a['debut_s'] <= duree + 1]
        if not choisis:
            continue
        score = sum(b['voise_s'] for b in choisis) * (1.5 if len({b.get('voix') for b in choisis} - {None}) >= 2 else 1)
        if meilleur is None or score > meilleur[0]:
            meilleur = (score, choisis)
    return meilleur[1] if meilleur else []


# --- Assemblage des morceaux convertis (commun à Seed-VC, dans le noyau, et à YingMusic, dans son étape à part) -----

def porte(lisse, seuil, n):
    """Porte sur les silences d'une piste : 1 là où elle sonne (élargi de 0,2 s de chaque côté), 0 ailleurs, flancs de
    50 ms. Rend le gain par échantillon et par trame de 10 ms."""
    import numpy as np
    actif = (lisse > seuil).astype(np.float64)
    actif = (np.convolve(actif, np.ones(41), mode='same') > 0).astype(np.float64)
    gain_trames = np.convolve(actif, np.ones(5) / 5, mode='same')
    gain = np.interp(np.arange(n), np.arange(len(gain_trames)) * PAS, gain_trames).astype(np.float32)
    return gain, gain_trames


def assembler(x, morceaux, pad, convertir_morceau, graine, torch):
    """Piste convertie, calée à l'échantillon près sur x : chaque morceau (i, début, fin, décalage, fondu au début,
    fondu à la fin ; en échantillons) est converti avec 0,25 s de marge de chaque côté, puis fondu aux coupes.
    convertir_morceau(xs, décalage) rend autant d'échantillons que xs. La porte des silences reste à l'appelant."""
    import numpy as np
    n = len(x)
    sortie = np.zeros(n, np.float32)
    for i, a, b, dec, fa, fb in morceaux:
        lo, hi = max(0, a - pad), min(n, b + pad)
        torch.manual_seed(graine + i)
        y = convertir_morceau(x[lo:hi], dec)
        tt = np.arange(lo, hi)
        w = np.ones(hi - lo)
        if a > 0:
            w *= rampe(tt - a, fa)
        if b < n:
            w *= 1 - rampe(tt - b, fb)
        sortie[lo:hi] += (y * w).astype(np.float32)
    return sortie


def lire_mono(chemin):
    import soundfile as sf
    x = sf.read(chemin, dtype='float32', always_2d=True)[0]
    return x.mean(1).astype('float32')


def rms_actif(x, ref=None):
    """RMS (linéaire) sur les trames où la piste de référence (par défaut x) dépasse son seuil de silence (banc d'essai)."""
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


def ecrire_json(chemin, d):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    tmp = chemin + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f, ensure_ascii=False, indent=1, default=float)
    os.replace(tmp, chemin)


def lire_json(chemin, defaut=None):
    try:
        with open(chemin) as f:
            return json.load(f)
    except (OSError, ValueError):
        return defaut


# --- Étapes à part : nettoyage de la voix et conversion YingMusic-SVC -------------------------------------------------

def script_complet():
    """Chemin d'une copie de ce noyau sur disque. Kaggle exécute le script en carnet (nbconvert), sans fichier :
    doubler.py y a glissé sa propre source (SOURCE, base64)."""
    if not os.path.exists(COMPLET):
        if not SOURCE.startswith('__'):
            texte = base64.b64decode(SOURCE).decode()
        elif '__file__' in globals() and os.path.exists(globals()['__file__']):
            with open(globals()['__file__']) as f:
                texte = f.read()
        else:
            raise RuntimeError('source du noyau absente (SOURCE non remplacée par doubler.py) : étape à part impossible')
        with open(COMPLET, 'w') as f:
            f.write(texte)
    return COMPLET


def etape_a_part(etape, travail):
    """Lance une étape (nettoyer, ying) dans son propre processus : le GPU est rendu à sa sortie, et YingMusic a son
    propre paquet « modules », homonyme de celui de Seed-VC. Rend le JSON écrit par l'étape (même partiel si elle a
    planté en route), plus son code de sortie et sa durée."""
    resultat = travail['resultat']
    os.makedirs(os.path.dirname(resultat), exist_ok=True)
    if os.path.exists(resultat):
        os.remove(resultat)
    chemin = resultat + '.travail.json'
    ecrire_json(chemin, travail)
    t0 = time.time()
    r = subprocess.run([sys.executable, script_complet(), etape, chemin], env={**os.environ, 'DOUBLER_DEBUT': repr(DEBUT)})
    res = lire_json(resultat, {}) or {}
    res.update({'code': r.returncode, 'duree_s': round(time.time() - t0, 1)})
    if r.returncode:
        log(f'ÉTAPE {etape} : arrêtée avec le code {r.returncode}')
    return res


def charger_torch():
    os.environ['TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD'] = '1'   # points de contrôle officiels (pickle) de Seed-VC, RMVPE…
    import torch
    if not getattr(torch.load, '_doubler', False):
        charger = torch.load

        def charger_complet(*a, **k):
            k.setdefault('weights_only', False)
            return charger(*a, **k)
        charger_complet._doubler = True
        torch.load = charger_complet
    return torch


def etape_nettoyer(travail):
    """Voix principale et chœurs sans reverb, pour toutes les vidéos en chant (code du banc d'essai, etape_separer) :
    1. Roformer karaoké sur la voix isolée : piste « Vocals » = voix principale ; chœurs = voix - voix principale, en numpy
       (reverb et fuites comprises) ;
    2. Roformer dereverb sur les deux pistes : piste « noreverb » gardée ; la reverb d'origine est jetée (le mix remet
       la sienne).
    Chaque modèle n'est chargé qu'une fois. Entrée ramenée à un pic de 0,9 puis remise à l'échelle, comme la séparation.
    Sorties : <dossier>/lead_sec.wav et choeurs_sec.wav (44,1 kHz stéréo flottant, même longueur que la voix)."""
    import numpy as np
    import soundfile as sf
    from audio_separator.separator import Separator
    torch = charger_torch()
    tmp = '/tmp/net_sep'
    os.makedirs(tmp, exist_ok=True)
    chemin = travail['resultat']
    ov = int(travail.get('recouvrement') or 4)
    res = {'karaoke': None, 'dereverb': travail['dereverb'], 'recouvrement': ov, 'chargements_s': {}, 'videos': {}}

    def separateur(nom):
        t0 = time.time()
        s = Separator(model_file_dir='/tmp/modeles_sep', output_dir=tmp, output_format='WAV', use_soundfile=True,
                      normalization_threshold=1.0, sample_rate=44100,
                      mdxc_params={'segment_size': 256, 'override_model_segment_size': False, 'batch_size': 1,
                                   'overlap': ov, 'pitch_shift': 0})
        s.load_model(model_filename=nom)
        res['chargements_s'][nom] = round(time.time() - t0, 1)
        log(f'NETTOYAGE modèle {nom} chargé en {time.time() - t0:.0f} s')
        return s

    def separer(s, x, motif, etiquette):
        """Pic ramené à 0,9, séparation, remise à l'échelle ; rend la piste dont le nom suit motif."""
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
        for f in glob.glob(f'{tmp}/*.wav'):
            os.remove(f)
        log(f'NETTOYAGE {etiquette} : {noms} -> {os.path.basename(choisis[0])} ({time.time() - t0:.1f} s)')
        return out, {'pistes': noms, 'gardee': os.path.basename(choisis[0]), 'duree_s': round(time.time() - t0, 1)}

    def lire2(f):
        x = sf.read(f, dtype='float32', always_2d=True)[0]
        return np.ascontiguousarray(x[:, :2] if x.shape[1] >= 2 else np.repeat(x, 2, axis=1))

    db = lambda v: round(20 * float(np.log10(v + 1e-12)), 1)
    s = None
    for nom in travail['karaoke']:
        try:
            s = separateur(nom)
            res['karaoke'] = nom
            break
        except Exception as e:  # noqa: BLE001
            log(f'NETTOYAGE {nom} : chargement impossible ({e!r}), modèle suivant')
    if s is None:
        res['erreur'] = 'aucun modèle karaoké utilisable'
        ecrire_json(chemin, res)
        return
    for v in travail['videos']:
        r = res['videos'][v['id']] = {'durees_s': {}}
        t0 = time.time()
        try:
            os.makedirs(v['dossier'], exist_ok=True)
            x = lire2(v['voix'])
            lead, info = separer(s, x, MOTIF_KARAOKE, f"{v['id']}_karaoke")
            sf.write(os.path.join(v['dossier'], 'lead.wav'), lead, 44100, subtype='FLOAT')
            sf.write(os.path.join(v['dossier'], 'choeurs.wav'), x - lead, 44100, subtype='FLOAT')
            r['durees_s']['karaoke'] = info['duree_s']
            r['karaoke'] = info
        except Exception as e:  # noqa: BLE001 — une vidéo ratée n'empêche pas les suivantes
            import traceback
            traceback.print_exc()
            r['erreur'] = f'karaoké : {e!r}'[:400]
        r['duree_s'] = round(time.time() - t0, 1)
        ecrire_json(chemin, res)
    del s
    torch.cuda.empty_cache()
    try:
        s = separateur(travail['dereverb'])
    except Exception as e:  # noqa: BLE001
        res['erreur'] = f"dereverb {travail['dereverb']} indisponible : {e!r}"[:400]
        ecrire_json(chemin, res)
        return
    for v in travail['videos']:
        r = res['videos'][v['id']]
        if r.get('erreur'):
            continue
        t0 = time.time()
        try:
            d = v['dossier']
            x, lead, choeurs = lire2(v['voix']), lire2(os.path.join(d, 'lead.wav')), lire2(os.path.join(d, 'choeurs.wav'))
            lead_sec, i1 = separer(s, lead, MOTIF_DEREVERB, f"{v['id']}_lead_dereverb")
            ch_sec, i2 = separer(s, choeurs, MOTIF_DEREVERB, f"{v['id']}_choeurs_dereverb")
            for nom, y in (('lead_sec', lead_sec), ('choeurs_sec', ch_sec)):
                sf.write(os.path.join(d, f'{nom}.wav'), y, 44100, subtype='FLOAT')
                r[nom] = os.path.join(d, f'{nom}.wav')
            ref = x.mean(1)
            r['niveaux_db'] = {nom: db(rms_actif(y.mean(1), ref)) for nom, y in (
                ('voix', x), ('lead', lead), ('choeurs', choeurs), ('lead_sec', lead_sec), ('choeurs_sec', ch_sec),
                ('reverb_jetee', x - lead_sec - ch_sec))}
            r['durees_s'].update({'dereverb_lead': i1['duree_s'], 'dereverb_choeurs': i2['duree_s']})
            r['dereverb'] = {'lead': i1, 'choeurs': i2}
            for f in ('lead.wav', 'choeurs.wav'):
                os.remove(os.path.join(d, f))
            log(f"NETTOYAGE {v['id']} : niveaux (dBFS, trames où la voix sonne) {json.dumps(r['niveaux_db'])}")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            r['erreur'] = f'dereverb : {e!r}'[:400]
        r['duree_s'] = round(r.get('duree_s', 0) + time.time() - t0, 1)
        ecrire_json(chemin, res)


def installer_ying(outils=None):
    """YingMusic-SVC au commit figé : archive du montage d'outils, sinon clonage par internet ; même correction de
    bigvgan.py que pour Seed-VC. Son dossier checkpoints pointe sur celui de Seed-VC (mêmes Whisper-small, BigVGAN
    44 kHz, RMVPE, CAMPPlus, même cache Hugging Face). Rend {'origine', 'duree_s'}."""
    if os.path.exists(os.path.join(YING, '.pret')):
        return {'origine': 'déjà installé', 'duree_s': 0.0}
    t0 = time.time()
    shutil.rmtree(YING, ignore_errors=True)
    archive = os.path.join(outils, 'yingmusic.tar') if outils else None
    if archive and os.path.exists(archive) and (lire_outils(outils).get('yingmusic') or {}).get('commit') == YING_COMMIT:
        lancer('tar', '-xf', archive, '-C', os.path.dirname(YING))
        origine = 'montage'
    else:
        if outils:
            log('outils : pas d\'archive YingMusic-SVC au bon commit dans le montage, clonage par internet')
        os.makedirs(YING)
        lancer('git', 'init', '-q', YING)
        lancer('git', '-C', YING, 'remote', 'add', 'origin', YING_DEPOT)
        lancer('git', '-C', YING, 'fetch', '-q', '--depth', '1', 'origin', YING_COMMIT)
        lancer('git', '-C', YING, 'checkout', '-q', 'FETCH_HEAD')
        origine = 'internet'
    corriger(os.path.join(YING, 'modules/bigvgan/bigvgan.py'),
             [('proxies: Optional[Dict],', 'proxies: Optional[Dict] = None,'), ('resume_download: bool,', 'resume_download: bool = None,'),
              ('proxies=proxies,', ''), ('resume_download=resume_download,', '')])
    if not os.path.lexists(os.path.join(YING, 'checkpoints')):
        os.symlink(os.path.join(SEEDVC, 'checkpoints'), os.path.join(YING, 'checkpoints'))
    open(os.path.join(YING, '.pret'), 'w').close()
    log(f'YingMusic-SVC {YING_COMMIT[:10]} installé ({origine}) en {time.time() - t0:.0f} s')
    return {'origine': origine, 'duree_s': round(time.time() - t0, 1)}


def poids_ying(outils):
    """Poids YingMusic-SVC du montage d'outils, s'il les a ; sinon None (téléchargés depuis Hugging Face)."""
    fiche = lire_outils(outils).get('yingmusic') or {}
    if outils and fiche.get('commit') == YING_COMMIT and fiche.get('poids'):
        chemin = os.path.join(outils, fiche['poids'])
        if os.path.exists(chemin):
            return chemin
    return None


class MoteurYing:
    """YingMusic-SVC, zéro-shot : MoteurSeedVC et MoteurYing du banc d'essai (banc/noyau_banc.py), réunis. Même boucle
    que la conversion Seed-VC du noyau ; régulateur de longueur avec résidu de style f0 et cfm.inference(style_r=…).
    my_inference.py du dépôt n'est pas importé : il dépend de torchaudio.sox_effects, absent de torchaudio 2.11."""
    type = 'yingmusic'

    def __init__(self, outils=None, checkpoint=None, config=None):
        self.torch = torch = charger_torch()
        self.installation = installer_ying(outils)
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
        r = self.model.length_regulator(s, ylens=torch.LongTensor([longueur]).to(self.device), n_quantizers=3, f0=f0,
                                        style=style2, return_style_residual=True)
        return r[0], (r[5] if self.use_style_residual else None)

    def inference(self, cat, ref, style_cat, pas):
        torch = self.torch
        return self.model.cfm.inference(cat, torch.LongTensor([cat.size(1)]).to(self.device), ref['mel2'], ref['style2'], None,
                                        pas, inference_cfg_rate=REGLAGES['cfg'], style_r=style_cat)

    def convertir_morceau(self, xs, ref, decalage, pas):
        """Un morceau (numpy 44,1 kHz), décalage fixe en demi-tons : la boucle de conversion du noyau."""
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
                with torch.autocast(device_type=self.device.type, dtype=torch.float16 if REGLAGES['fp16'] else torch.float32):
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


def etape_ying(travail):
    """Conversion YingMusic-SVC de toutes les pistes demandées (toutes vidéos et variantes), modèle chargé une fois.
    Chaque piste : son morceaux à convertir (mêmes coupes et décalages que la voix complète), assemblée sans porte ni
    mise au niveau (le noyau les applique), écrite en WAV flottant mono. Sans pistes : simple essai de chargement."""
    import soundfile as sf
    chemin = travail['resultat']
    res = {'commit': YING_COMMIT, 'poids': os.path.basename(travail.get('checkpoint') or YING_POIDS[1]),
           'poids_origine': 'montage' if travail.get('checkpoint') else 'Hugging Face', 'pistes': {}}
    try:
        m = MoteurYing(travail.get('outils'), travail.get('checkpoint'))
        res.update({'installation': m.installation, 'chargement_s': m.chargement_s})
        ref = m.preparer_ref(travail['reference']) if travail.get('reference') else None
    except Exception as e:  # noqa: BLE001 — le noyau se repliera sur Seed-VC
        import traceback
        traceback.print_exc()
        res['erreur'] = f'YingMusic-SVC indisponible : {e!r}'[:500]
        ecrire_json(chemin, res)
        return
    log(f"YINGMUSIC chargé en {m.chargement_s:.0f} s (installation {m.installation['origine']}, poids {res['poids_origine']}) ; "
        f"{len(travail.get('pistes', []))} piste(s) à convertir")
    ecrire_json(chemin, res)
    for p in travail.get('pistes', []):
        t0 = time.time()
        try:
            x = lire_mono(p['entree'])
            y = assembler(x, p['morceaux'], travail['pad'], lambda xs, d: m.convertir_morceau(xs, ref, d, p['pas']),
                          REGLAGES['graine'], m.torch)
            sf.write(p['sortie'], y, SR, subtype='FLOAT')
            res['pistes'][p['cle']] = {'sortie': p['sortie'], 'conversion_s': round(time.time() - t0, 1), 'morceaux': len(p['morceaux'])}
            log(f"YINGMUSIC {p['cle']} : {len(p['morceaux'])} morceaux en {time.time() - t0:.0f} s")
        except Exception as e:  # noqa: BLE001 — cette piste se repliera sur Seed-VC
            import traceback
            traceback.print_exc()
            res['pistes'][p['cle']] = {'erreur': repr(e)[:500]}
        m.torch.cuda.empty_cache()
        ecrire_json(chemin, res)


def etape_isolee(etape, chemin):
    """Point d'entrée d'une étape à part (python doubler_complet.py <étape> <travail.json>)."""
    if os.path.isdir(SOUCHES) and SOUCHES not in sys.path:
        sys.path.insert(0, SOUCHES)
    {'nettoyer': etape_nettoyer, 'ying': etape_ying}[etape](lire_json(chemin))


def main():
    session = {'debut_epoch': DEBUT, 'temps': {}}
    verifier_acces()
    R = REGLAGES
    if R.get('outils'):
        return preparer_outils()
    outils = trouver_outils()
    fiche = lire_outils(outils)
    session['outils'] = {'monte': bool(outils), 'date': fiche.get('date'), 'python_outils': fiche.get('python'),
                         'python': sys.version.split()[0]}
    log(f"outils : {'montage ' + outils + ' du ' + str(fiche.get('date')) if outils else 'aucun montage, tout par internet'}")
    t = time.time()
    installer(outils)
    sha = cloner(outils)
    poids_depuis_outils(outils)
    session['temps']['installation_s'] = round(time.time() - t, 1)
    os.environ['TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD'] = '1'   # points de contrôle officiels (pickle) de Seed-VC et RMVPE
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio
    charger = torch.load

    def charger_complet(*a, **k):
        k.setdefault('weights_only', False)
        return charger(*a, **k)
    torch.load = charger_complet
    session.update({'seedvc': sha, 'torch': torch.__version__, 'gpu': torch.cuda.get_device_name(0)})

    def ecrire_session():
        with open(os.path.join(SORTIE, 'session.json'), 'w') as f:
            json.dump(session, f, ensure_ascii=False, indent=1, default=float)

    # 1. Séparation de toutes les vidéos (un seul chargement du Roformer ; processus à part : il libère le GPU en sortant)
    travaux = []
    for tr in R['travaux']:
        d = os.path.join(SORTIE, tr['id'])
        os.makedirs(d, exist_ok=True)
        # retour_flac (doubler.py récent) : la voix isolée revient en FLAC 24 bits, le WAV flottant reste dans /tmp ;
        # sans lui (ancien doubler.py), voix_source.wav comme avant
        flac = bool(tr.get('retour_flac'))
        travaux.append({**tr, 'dossier': d, 'chemin': trouver(tr['entree']),
                        'voix': f"/tmp/sep/{tr['id']}_voix.wav" if flac else os.path.join(d, 'voix_source.wav'),
                        'voix_flac': os.path.join(d, 'voix_source.flac') if flac else None,
                        'reste': f"/tmp/sep/{tr['id']}_reste.wav"})
    ref_chemin = trouver(R['voix']['fichier'])
    t = time.time()
    os.makedirs('/tmp/sep', exist_ok=True)
    with open('/tmp/separer.py', 'w') as f:
        f.write(SEPARER)
    lancer(sys.executable, '/tmp/separer.py', json.dumps([{'id': tr['id'], 'entree': tr['chemin'], 'voix': tr['voix'],
                                                          'reste': tr['reste'], 'gain_entree': float(tr.get('gain_entree') or 1.0),
                                                          'voix_flac': tr['voix_flac']} for tr in travaux]), '/tmp/sep')
    session['temps']['separation_totale_s'] = round(time.time() - t, 1)

    # 2. Modèles (une fois pour toutes les vidéos)
    os.chdir(SEEDVC)
    sys.path.insert(0, SEEDVC)
    import inference as sv
    from argparse import Namespace
    t = time.time()

    def charger_modele(f0):
        t0 = time.time()
        model, semantic_fn, f0_fn, vocoder_fn, campplus, mel_fn, mel_args = sv.load_models(
            Namespace(f0_condition=f0, checkpoint=None, config=None, fp16=R['fp16']))
        session['temps'][f"chargement_{'chant44k' if f0 else 'parole22k'}_s"] = round(time.time() - t0, 1)
        return {'nom': 'chant44k' if f0 else 'parole22k', 'f0': f0, 'model': model, 'semantic_fn': semantic_fn,
                'f0_fn': f0_fn, 'vocoder_fn': vocoder_fn, 'campplus': campplus, 'mel_fn': mel_fn,
                'sr': mel_args['sampling_rate'], 'hop': mel_args['hop_size']}
    modeles = {'chant44k': charger_modele(True)}   # toujours : RMVPE (hauteur) et CAMPPlus (empreinte) servent à l'analyse
    chant = modeles['chant44k']
    device = sv.device
    f0_fn, campplus = chant['f0_fn'], chant['campplus']

    @torch.no_grad()
    def empreinte(w16):
        feat = torchaudio.compliance.kaldi.fbank(w16, num_mel_bins=80, dither=0, sample_frequency=16000)
        e = campplus((feat - feat.mean(dim=0, keepdim=True)).unsqueeze(0))[0].float()
        return (e / (e.norm() + 1e-9)).cpu().numpy()

    def en16(y):
        return torchaudio.functional.resample(torch.from_numpy(np.ascontiguousarray(y)).float().unsqueeze(0).to(device), SR, 16000)

    @torch.no_grad()
    def semantique(md, w16):
        if w16.size(-1) <= 16000 * 30:
            return md['semantic_fn'](w16)
        liste, tampon, fait = [], None, 0
        while fait < w16.size(-1):
            morceau = w16[:, fait:fait + 16000 * 30] if tampon is None else torch.cat([tampon, w16[:, fait:fait + 16000 * 25]], dim=-1)
            s = md['semantic_fn'](morceau)
            liste.append(s if fait == 0 else s[:, 50 * 5:])
            tampon = morceau[:, -16000 * 5:]
            fait += 30 * 16000 if fait == 0 else morceau.size(-1) - 16000 * 5
        return torch.cat(liste, dim=1)

    @torch.no_grad()
    def preparer_ref(md):
        import librosa
        ref = librosa.load(ref_chemin, sr=md['sr'])[0][:md['sr'] * 25]
        ref_t = torch.tensor(ref).unsqueeze(0).float().to(device)
        ref16 = torchaudio.functional.resample(ref_t, md['sr'], 16000)
        mel2 = md['mel_fn'](ref_t)
        feat2 = torchaudio.compliance.kaldi.fbank(ref16, num_mel_bins=80, dither=0, sample_frequency=16000)
        style2 = md['campplus']((feat2 - feat2.mean(dim=0, keepdim=True)).unsqueeze(0))
        f0_ref = torch.from_numpy(f0_fn(ref16[0], thred=0.03)).to(device)[None]
        prompt = md['model'].length_regulator(semantique(md, ref16), ylens=torch.LongTensor([mel2.size(2)]).to(device),
                                              n_quantizers=3, f0=f0_ref if md['f0'] else None)[0]
        voise = f0_ref[f0_ref > 1]
        return {'mel2': mel2, 'style2': style2, 'prompt': prompt, 'duree': len(ref) / md['sr'],
                'f0_mediane': float(voise.median()) if voise.numel() else None, 'emb': empreinte(ref16)}

    refs = {'chant44k': preparer_ref(chant)}
    session['temps']['chargement_modeles_s'] = round(time.time() - t, 1)
    ref_emb = refs['chant44k']['emb']
    f0_parole = float(R['voix'].get('f0_parole_hz') or refs['chant44k']['f0_mediane'])
    centre = R['voix']['facteur_chant'] * f0_parole
    session['voix'] = {**R['voix'], 'f0_rmvpe_reference_hz': round(refs['chant44k']['f0_mediane'], 1),
                       'duree_reference_s': round(refs['chant44k']['duree'], 2), 'centre_chant_hz': round(centre, 1)}
    log(f"voix {R['voix']['nom']} : référence {refs['chant44k']['duree']:.2f} s, parole {f0_parole:.0f} Hz "
        f"(RMVPE sur la référence {refs['chant44k']['f0_mediane']:.0f} Hz), centre de chant {centre:.0f} Hz")
    ecrire_session()

    @torch.no_grad()
    def convertir(xs, md, ref, decalage, pas):
        """Un morceau (numpy 44,1 kHz) : la boucle de inference.py, décalage fixe en demi-tons (modèle f0)."""
        src = torch.from_numpy(np.ascontiguousarray(xs)).float().unsqueeze(0).to(device)
        src_m = src if md['sr'] == SR else torchaudio.functional.resample(src, SR, md['sr'])
        src16 = torchaudio.functional.resample(src, SR, 16000)
        s_alt = semantique(md, src16)
        mel = md['mel_fn'](src_m)
        f0_dec = None
        if md['f0']:
            f0 = torch.from_numpy(f0_fn(src16[0], thred=0.03)).to(device)[None]
            voise = f0 > 1
            f0_dec = torch.exp(torch.log(f0 + 1e-5))
            f0_dec[voise] = f0_dec[voise] * 2 ** (decalage / 12)
        cond = md['model'].length_regulator(s_alt, ylens=torch.LongTensor([mel.size(2)]).to(device), n_quantizers=3, f0=f0_dec)[0]
        mel2 = ref['mel2']
        fenetre = md['sr'] // md['hop'] * 30 - mel2.size(2)
        recouvre, ov = 16, 16 * md['hop']
        fait, morceaux, precedent = 0, [], None
        while fait < cond.size(1):
            bout = cond[:, fait:fait + fenetre]
            dernier = fait + fenetre >= cond.size(1)
            cat = torch.cat([ref['prompt'], bout], dim=1)
            with torch.autocast(device_type=device.type, dtype=torch.float16 if R['fp16'] else torch.float32):
                cible = md['model'].cfm.inference(cat, torch.LongTensor([cat.size(1)]).to(device), mel2, ref['style2'], None,
                                                  pas, inference_cfg_rate=R['cfg'])
                cible = cible[:, :, mel2.size(-1):]
            onde = md['vocoder_fn'](cible.float()).squeeze()[None, :]
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
        y = torch.from_numpy(np.concatenate(morceaux).astype(np.float32))
        if md['sr'] != SR:
            y = torchaudio.functional.resample(y.unsqueeze(0), md['sr'], SR)[0]
        y = y.numpy()
        return y[:len(xs)] if len(y) >= len(xs) else np.pad(y, (0, len(xs) - len(y)))

    moteur_chant = R.get('moteur_chant') or 'yingmusic'
    nettoyage_demande = bool(R.get('nettoyage', True))
    pad, fondu = int(R['pad_s'] * SR), int(R['fondu_s'] * SR)

    def analyser(tr):
        """Mode, découpage aux silences et décalage de chaque morceau, sur la voix isolée complète : la détection du mode
        et la règle d'octave ont été calibrées sur elle, le nettoyage ne change donc pas leurs entrées."""
        debut_tr = time.time()
        rapport = {'id': tr['id'], 'reglages': {k: v for k, v in R.items() if k != 'travaux'}, 'travail': tr,
                   'voix_cible': session['voix'], 'temps': {}}
        dur_sep = tr['voix'] + '.duree'
        if os.path.exists(dur_sep):
            rapport['temps']['separation_s'] = float(open(dur_sep).read())
            os.remove(dur_sep)
        rapport['transport'] = {'entree': os.path.basename(tr['chemin']), 'gain_entree': float(tr.get('gain_entree') or 1.0)}
        if tr.get('voix_flac') and os.path.exists(tr['voix_flac'] + '.json'):
            with open(tr['voix_flac'] + '.json') as f:
                rapport['transport']['voix_source_flac'] = json.load(f)
        voix2 = sf.read(tr['voix'], dtype='float32', always_2d=True)[0]
        reste2 = sf.read(tr['reste'], dtype='float32', always_2d=True)[0]
        x = voix2.mean(1).astype(np.float32)
        reste = reste2.mean(1).astype(np.float32)
        del voix2, reste2
        n = len(x)
        # Analyse : hauteur RMVPE sur toute la voix, découpage aux silences, empreinte par morceau
        t = time.time()
        # RMVPE rend une trame de plus par tranche (centrage) : chaque tranche est recoupée à 10 ms près pour rester calée
        f0_tout = np.concatenate([f0_fn(en16(x[d:d + SR * 20])[0], thred=0.03)[:len(x[d:d + SR * 20]) // PAS]
                                  for d in range(0, n, SR * 20)])
        env = enveloppe_db(x)
        lisse = lisser_db(env)
        seuil = seuil_silence(env)
        m = mesures_musique(x, reste)
        m.update(mesures_chant(f0_tout, lisse > seuil))
        mode, raisons = decider_mode(m, R)
        if tr['mode'] in ('chant', 'parole'):
            mode = tr['mode']
        rapport.update({'mesures_musique': m, 'mode': mode, 'mode_raisons': raisons, 'mode_force': tr['mode'] in ('chant', 'parole')})
        log(f"[{tr['id']}] MODE {mode} ({'imposé' if rapport['mode_force'] else 'détecté'}) : {json.dumps(m, ensure_ascii=False)}")
        place = SR // 512 * 30 - refs['chant44k']['mel2'].size(2)
        coeur_max = place * 512 - 2 * pad - int(0.1 * SR)
        coupes = decouper(lisse, n, coeur_max, min(int(2 * SR), coeur_max // 2), seuil)
        segs = []
        for i, (a, b) in enumerate(zip(coupes[:-1], coupes[1:])):
            s = {'i': i, 'debut': a, 'fin': b, 'debut_s': round(a / SR, 3), 'fin_s': round(b / SR, 3),
                 'muet': float(lisse[a // PAS:b // PAS + 1].max()) < seuil, 'f0_med': None, 'voise_s': 0.0}
            if not s['muet']:
                coeur = f0_tout[a // PAS:b // PAS]
                v = coeur[coeur > 1]
                s['voise_s'] = round(len(v) * 0.01, 2)
                s['f0_med'] = round(float(np.median(v)), 1) if len(v) else None
                s['emb'] = empreinte(en16(x[a:b])) if b - a >= SR // 2 else None
            segs.append(s)
        if mode == 'chant':
            info = decider_chant(segs, R, centre, [round(c / SR, 3) for c in coupes[1:-1] if lisse[c // PAS] >= seuil])
        else:
            info = decider_parole(segs, f0_parole, R)
            if R['modele_parole'] == 'parole22k':
                info['note'] = ('modèle parole 22 kHz : la hauteur suit la voix cible d\'elle-même ; les décalages par voix '
                                'ne servent qu\'à la variante f0')
        if tr['octave'] in (-12, 0, 12):
            info['octave_imposee'] = tr['octave']   # la décision ci-dessus reste pour mémoire
            for s in segs:
                s['decalage'], s['base'] = tr['octave'], 'imposé'
        rapport['decision_hauteur'] = info
        rapport['temps']['analyse_s'] = round(time.time() - t, 1)
        log(f"[{tr['id']}] {len(segs)} morceaux de {coeur_max / SR:.2f} s au plus, {sum(s['muet'] for s in segs)} muets ; "
            f"hauteur : {json.dumps(info, ensure_ascii=False)}")
        for l in frise(segs):
            log(f"[{tr['id']}] FRISE " + l)
        fondus = {c: fondu if lisse[c // PAS] < seuil else int(0.2 * SR) for c in coupes[1:-1]}
        gain, gain_trames = porte(lisse, seuil, n)
        os.remove(tr['reste'])
        return {'tr': tr, 'rapport': rapport, 'x': x, 'n': n, 'mode': mode, 'segs': segs, 'coupes': coupes, 'lisse': lisse,
                'seuil': seuil, 'fondus': fondus, 'gain': gain, 'gain_trames': gain_trames, 'coeur_max': coeur_max,
                'f0_tout': f0_tout, 'duree_s': time.time() - debut_tr}

    def f0_piste(y):
        """F0 RMVPE de toute une piste, une trame toutes les 10 ms calée sur l'échantillon 0 (comme f0_tout)."""
        return np.concatenate([f0_fn(en16(y[d:d + SR * 20])[0], thred=0.03)[:len(y[d:d + SR * 20]) // PAS]
                               for d in range(0, len(y), SR * 20)])

    def ecrire_courbes(e, sortie, f0_modele):
        """courbes.json (pour l'interface ; format 1, à ne pas changer sans prévenir) : mélodie de la voix isolée complète
        (celle de la décision d'octave, F0 de l'analyse) et de la voix convertie finale (voix principale + chœurs, une
        passe RMVPE de plus), une valeur toutes les 20 ms arrondie à 0,1 Hz (0 : non voisé), et le décalage appliqué à
        chaque trame (0 avec le modèle parole 22 kHz, qui suit la hauteur de la voix cible de lui-même). Temps 0 : début
        de l'audio doublé (de l'extrait s'il y en a un)."""
        t0 = time.time()
        f_src, f_out = e['f0_tout'], f0_piste(sortie)
        k = min(len(f_src), len(f_out))
        f_src, f_out = f_src[:k:2], f_out[:k:2]
        temps = np.arange(len(f_src)) * 0.02
        dec = np.zeros(len(f_src))
        if f0_modele:
            for s in e['segs']:
                dec[(temps >= s['debut_s']) & (temps < s['fin_s'])] = s['decalage']
        hz = lambda f: [round(float(v), 1) if v > 1 else 0 for v in f]
        courbes = {'format': 1, 'pas_s': 0.02, 'source_hz': hz(f_src), 'convertie_hz': hz(f_out),
                   'decalage_dt': [int(v) if float(v).is_integer() else round(float(v), 1) for v in dec]}
        with open(os.path.join(e['tr']['dossier'], 'courbes.json'), 'w') as f:
            json.dump(courbes, f, separators=(',', ':'))
        e['rapport']['courbes'] = {'fichier': 'courbes.json', 'trames': len(f_src), 'pas_s': 0.02,
                                   'taille_ko': round(os.path.getsize(os.path.join(e['tr']['dossier'], 'courbes.json')) / 1024, 1)}
        e['rapport']['temps']['courbes_s'] = round(time.time() - t0, 1)

    def nettoyer(videos_chant):
        """Voix principale et chœurs secs de toutes les vidéos en chant, dans une étape à part (chaque Roformer chargé une
        fois). Une vidéo dont le nettoyage échoue garde la voix isolée complète (chaîne d'avant)."""
        t = time.time()
        torch.cuda.empty_cache()
        travail = {'videos': [{'id': e['tr']['id'], 'voix': e['tr']['voix'], 'dossier': f"/tmp/net/{e['tr']['id']}"} for e in videos_chant],
                   'karaoke': list(R.get('karaoke') or KARAOKE), 'dereverb': R.get('dereverb') or DEREVERB,
                   'recouvrement': int(R.get('nettoyage_recouvrement') or 4), 'resultat': '/tmp/net/resultat.json'}
        try:
            res = etape_a_part('nettoyer', travail)
        except Exception as ex:  # noqa: BLE001
            res = {'erreur': repr(ex)}
        session['temps']['nettoyage_totale_s'] = round(time.time() - t, 1)
        session['temps']['chargement_nettoyage_s'] = round(sum((res.get('chargements_s') or {}).values()), 1)
        session['nettoyage'] = {k: res.get(k) for k in ('karaoke', 'dereverb', 'recouvrement', 'chargements_s', 'erreur', 'code')}
        for e in videos_chant:
            v = (res.get('videos') or {}).get(e['tr']['id']) or {}
            info = {'demande': True, 'applique': False, 'karaoke': res.get('karaoke'), 'dereverb': res.get('dereverb'),
                    'recouvrement': res.get('recouvrement')}
            if not v.get('erreur') and all(v.get(k) and os.path.exists(v[k]) for k in ('lead_sec', 'choeurs_sec')):
                e['net'] = {k: v[k] for k in ('lead_sec', 'choeurs_sec')}
                info.update({'applique': True, 'niveaux_db': v.get('niveaux_db'), 'durees_s': v.get('durees_s')})
                e['rapport']['temps']['nettoyage_s'] = v.get('duree_s')
            else:
                pourquoi = v.get('erreur') or res.get('erreur') or f"étape arrêtée (code {res.get('code')})"
                info['raison'] = f'échec du nettoyage ({pourquoi}) : voix isolée complète convertie'
                log(f"[{e['tr']['id']}] NETTOYAGE impossible : {pourquoi}")
            e['rapport']['nettoyage'] = info
        ecrire_session()

    def preparer_pistes(e):
        """Pistes à convertir : voix principale et chœurs secs (voix nettoyée), sinon la voix isolée complète. Chaque piste
        a sa porte des silences (et, nettoyée, son propre seuil : des chœurs absents d'un morceau n'y sont pas convertis)."""
        if not e.get('net'):
            return [{'nom': 'voix', 'chemin': e['tr']['voix'], 'x': e['x'], 'lisse': e['lisse'], 'seuil': e['seuil'],
                     'gain': e['gain']}]
        pistes = []
        for nom in ('lead_sec', 'choeurs_sec'):
            xt = lire_mono(e['net'][nom])
            xt = xt[:e['n']] if len(xt) >= e['n'] else np.pad(xt, (0, e['n'] - len(xt)))
            env_t = enveloppe_db(xt)
            lisse_t, seuil_t = lisser_db(env_t), seuil_silence(env_t)
            pistes.append({'nom': nom, 'chemin': e['net'][nom], 'x': xt, 'lisse': lisse_t, 'seuil': seuil_t,
                           'gain': porte(lisse_t, seuil_t, e['n'])[0]})
        return pistes

    def plan(e, p, liste):
        """Morceaux d'une piste : ceux de la voix complète (mêmes coupes, même décalage), sauf les muets et ceux où cette
        piste se tait. [(i, début, fin, décalage, fondu au début, fondu à la fin)]."""
        out = []
        for s in liste:
            if s['muet']:
                continue
            a, b = s['debut'], s['fin']
            if p['nom'] != 'voix' and float(p['lisse'][a // PAS:b // PAS + 1].max()) < p['seuil']:
                continue
            out.append((s['i'], a, b, s['decalage'], e['fondus'].get(a, 0), e['fondus'].get(b, 0)))
        return out

    def variantes_de(e):
        tr, segs = e['tr'], e['segs']
        if e['mode'] == 'chant':
            principal = 'yingmusic' if moteur_chant == 'yingmusic' else 'chant44k'
        else:
            principal = R['modele_parole']
        variantes = [(principal, R['pas'], segs, 'voix_convertie.flac')]
        if e['mode'] == 'parole' and tr.get('comparer_modeles'):
            autre = 'chant44k' if principal == 'parole22k' else 'parole22k'
            variantes.append((autre, R['pas'], segs, f'voix_convertie_{autre}.flac'))
        if tr.get('comparer_pas'):
            fen = fenetre_comparaison(segs, 30)
            if fen:
                e['rapport']['comparaison_pas'] = {'debut_s': fen[0]['debut_s'], 'fin_s': fen[-1]['fin_s'],
                                                   'pas': [R['pas'], tr['comparer_pas']],
                                                   'fichier': f"voix_convertie_{tr['comparer_pas']}pas.flac"}
                variantes.append((principal, tr['comparer_pas'], fen, f"voix_convertie_{tr['comparer_pas']}pas.flac"))
        return variantes

    def cle_ying(e, fichier, p):
        return f"{e['tr']['id']}|{fichier}|{p['nom']}"

    def convertir_ying(etats_v):
        """Toutes les pistes à convertir par YingMusic-SVC (toutes vidéos et variantes), dans une seule étape à part."""
        pistes = []
        for e in etats_v:
            for k, (nom, pas, liste, fichier) in enumerate(e['variantes']):
                if nom == 'yingmusic':
                    for p in e['pistes']:
                        pistes.append({'cle': cle_ying(e, fichier, p), 'entree': p['chemin'], 'pas': pas,
                                       'sortie': f"/tmp/ying/{e['tr']['id']}_{k}_{p['nom']}.wav", 'morceaux': plan(e, p, liste)})
        if not pistes:
            return {}
        t = time.time()
        torch.cuda.empty_cache()
        ck = poids_ying(outils)
        try:
            res = etape_a_part('ying', {'outils': outils, 'checkpoint': ck, 'reference': ref_chemin, 'pad': pad,
                                        'pistes': pistes, 'resultat': '/tmp/ying/resultat.json'})
        except Exception as ex:  # noqa: BLE001 — repli sur Seed-VC
            res = {'erreur': repr(ex)}
        session['temps']['yingmusic_total_s'] = round(time.time() - t, 1)
        session['temps']['chargement_yingmusic_s'] = round((res.get('chargement_s') or 0) + ((res.get('installation') or {}).get('duree_s') or 0), 1)
        session['yingmusic'] = {k: res.get(k) for k in ('commit', 'poids', 'poids_origine', 'installation', 'chargement_s', 'erreur', 'code')}
        ecrire_session()
        return res

    def finir_video(e, ying):
        """Conversion de chaque variante (Seed-VC ici, YingMusic déjà fait à part), porte, mise au niveau de chaque piste
        nettoyée, somme, mesures, rapport."""
        tr, rapport, segs, n = e['tr'], e['rapport'], e['segs'], e['n']
        t_video, ying_s = time.time(), 0.0
        rapport['modele'], rapport['variantes'] = None, {}
        for nom_demande, pas, liste, fichier in e['variantes']:
            nom, repli, infos = nom_demande, None, {}
            if nom == 'yingmusic':
                for p in e['pistes']:
                    i = (ying.get('pistes') or {}).get(cle_ying(e, fichier, p)) or {}
                    infos[p['nom']] = i
                    if i.get('erreur') or not i.get('sortie') or not os.path.exists(i['sortie']):
                        repli = repli or i.get('erreur') or ying.get('erreur') or f"conversion absente (étape YingMusic : code {ying.get('code')})"
                if repli:
                    log(f"[{tr['id']}] YingMusic-SVC indisponible pour {fichier} ({repli}) : repli sur Seed-VC 44 kHz")
                    nom = 'chant44k'
            if nom != 'yingmusic':
                if nom not in modeles:
                    modeles[nom] = charger_modele(nom == 'chant44k')
                if nom not in refs:
                    refs[nom] = preparer_ref(modeles[nom])
            f0 = nom != 'parole22k'
            total, details, duree = np.zeros(n, np.float32), {}, 0.0
            for p in e['pistes']:
                morceaux = plan(e, p, liste)
                if nom == 'yingmusic':
                    y, conv = lire_mono(infos[p['nom']]['sortie']), float(infos[p['nom']]['conversion_s'])
                    os.remove(infos[p['nom']]['sortie'])
                    ying_s += conv
                elif morceaux:
                    md, ref = modeles[nom], refs[nom]
                    t = time.time()
                    y = assembler(p['x'], morceaux, pad, lambda xs, d: convertir(xs, md, ref, d if md['f0'] else 0, pas),
                                  R['graine'], torch)
                    conv = time.time() - t
                else:
                    y, conv = np.zeros(n, np.float32), 0.0
                y = y[:n] if len(y) >= n else np.pad(y, (0, n - len(y)))
                y = (y * p['gain']).astype(np.float32)
                secondes_p = sum(b - a for _, a, b, *_ in morceaux) / SR
                d = {'morceaux': len(morceaux), 'secondes_converties': round(secondes_p, 1), 'conversion_s': round(conv, 1),
                     'rtf': round(conv / max(secondes_p, 1e-3), 3)}
                if p['nom'] != 'voix' and morceaux:
                    # chaque piste nettoyée reprend le niveau de sa source : l'équilibre voix principale / chœurs d'origine
                    g = rms_actif(p['x']) / max(rms_actif(y, p['x']), 1e-9)
                    y = (y * g).astype(np.float32)
                    d['gain_niveau_db'] = round(20 * math.log10(g), 2)
                details[p['nom']] = d
                total += y
                duree += conv
            crete = float(np.abs(total).max())
            garde = 0.99 / crete if crete > 0.99 else 1.0   # jamais d'écrêtage dans le fichier (le mix remet au niveau)
            total *= garde
            secondes = sum(s['fin'] - s['debut'] for s in liste if not s['muet']) / SR
            mes = {'modele': nom, 'moteur': 'yingmusic' if nom == 'yingmusic' else 'seedvc', 'pas_diffusion': pas,
                   'conversion_s': round(duree, 1), 'rtf': round(duree / max(secondes, 1e-3), 3),
                   'secondes_converties': round(secondes, 1), 'crete': crete, 'echantillons_sortie': int(n), 'pistes': details}
            if garde < 1:
                mes['garde_crete_db'] = round(20 * math.log10(garde), 2)
            if repli:
                mes['repli'] = repli
            if liste is segs:
                # Mesures : ressemblance de timbre (CAMPPlus, cosinus avec la référence), hauteur obtenue
                sims, sims_src, ecarts = [], [], []
                for s in segs:
                    if s['muet'] or s['fin'] - s['debut'] < SR // 2:
                        continue
                    o16 = en16(total[s['debut']:s['fin']])
                    w = s['fin'] - s['debut']
                    sims.append((float(empreinte(o16) @ ref_emb), w))
                    if s.get('emb') is not None:
                        sims_src.append((float(s['emb'] @ ref_emb), w))
                    if s['f0_med'] and s['voise_s'] >= 0.5:
                        fo = f0_fn(o16[0], thred=0.03)
                        fo = fo[fo > 1]
                        if len(fo):
                            ecarts.append((12 * np.log2(np.median(fo) / s['f0_med']), s['decalage']))
                moy = lambda l: round(sum(a * w for a, w in l) / max(sum(w for _, w in l), 1), 3) if l else None
                mes.update({'ressemblance_timbre_sortie': moy(sims), 'ressemblance_timbre_source': moy(sims_src)})
                if ecarts:
                    mes['ecart_hauteur_obtenu_median_dt'] = round(float(np.median([e_ for e_, _ in ecarts])), 2)
                    if f0:
                        mes['ecart_a_la_consigne_median_dt'] = round(float(np.median([e_ - d_ for e_, d_ in ecarts])), 2)
                        mes['morceaux_hors_consigne_6dt'] = int(sum(abs(e_ - d_) > 6 for e_, d_ in ecarts))
            rapport['variantes'][fichier] = mes
            if fichier == 'voix_convertie.flac':
                rapport['modele'] = nom
                try:
                    ecrire_courbes(e, total, f0)
                except Exception as ex:  # noqa: BLE001 — les courbes ne servent qu'à l'affichage
                    log(f"[{tr['id']}] courbes.json impossible : {ex!r}")
            sf.write(os.path.join(tr['dossier'], fichier), np.clip(total, -1, 1), SR, subtype='PCM_24')
            log(f"[{tr['id']}] conversion {nom}, {pas} pas → {fichier} : {json.dumps(mes, ensure_ascii=False)}")
            torch.cuda.empty_cache()
        principale = rapport['variantes'].get('voix_convertie.flac', {})
        rapport['moteur'] = principale.get('moteur')
        rapport['moteur_info'] = {'chant_demande': moteur_chant, 'utilise': principale.get('moteur'), 'modele': rapport['modele'],
                                  'repli': principale.get('repli')}
        if e['mode'] != 'chant':
            rapport['moteur_info']['note'] = 'parole : Seed-VC 22 kHz (YingMusic-SVC ne fait que le chant)'
        if principale.get('moteur') == 'yingmusic':
            rapport['moteur_info']['yingmusic'] = {k: ying.get(k) for k in ('commit', 'poids', 'poids_origine', 'installation', 'chargement_s')}
        rapport['temps']['conversion_s'] = round(sum(v['conversion_s'] for v in rapport['variantes'].values()), 1)
        rapport['empreintes'] = {'format': 'CAMPPlus normée : 192 float16 petit-boutistes en base64, par numéro de morceau',
                                 'morceaux': {str(s['i']): base64.b64encode(np.asarray(s['emb'], '<f2').tobytes()).decode()
                                              for s in segs if s.get('emb') is not None}}
        coupes, lisse, seuil = e['coupes'], e['lisse'], e['seuil']
        for s in segs:
            s.pop('emb', None)
            s.pop('debut', None)
            s.pop('fin', None)
        rapport.update({'echantillons': n, 'duree_s': round(n / SR, 3), 'seuil_silence_dbfs': round(seuil, 1),
                        'coeur_max_s': round(e['coeur_max'] / SR, 2), 'morceaux': segs, 'frise': frise(segs),
                        'coupes_dans_le_son_s': [round(c / SR, 2) for c in coupes[1:-1] if lisse[c // PAS] >= seuil],
                        'porte_part_coupee': round(float((e['gain_trames'] < 0.5).mean()), 3)})
        rapport['temps']['total_video_s'] = round(rapport['temps'].get('separation_s', 0) + e['duree_s']
                                                  + (rapport['temps'].get('nettoyage_s') or 0) + ying_s + time.time() - t_video, 1)
        return rapport

    def nettoyer_fichiers(e):
        tr = e['tr']
        if tr.get('voix_flac') and os.path.exists(tr['voix']):
            os.remove(tr['voix'])   # WAV flottant de travail (/tmp) : c'est le FLAC qui revient
        shutil.rmtree(f"/tmp/net/{tr['id']}", ignore_errors=True)

    def ecrire_rapport(tr, rapport):
        with open(os.path.join(tr['dossier'], 'rapport.json'), 'w') as f:
            json.dump(rapport, f, ensure_ascii=False, indent=1, default=float)

    def echec(tr, ex):
        import traceback
        traceback.print_exc()
        log(f"[{tr['id']}] ÉCHEC : {ex!r}")
        ecrire_rapport(tr, {'id': tr['id'], 'erreur': repr(ex)})

    # 3. Analyse de chaque vidéo (mode, morceaux, décalages), sur la voix isolée complète
    etats = []
    for tr in travaux:
        try:
            etats.append(analyser(tr))
        except Exception as ex:  # noqa: BLE001 — une vidéo ratée n'empêche pas les suivantes
            echec(tr, ex)
    # 4. Nettoyage des vidéos en chant
    en_chant = [e for e in etats if e['mode'] == 'chant']
    for e in etats:
        if e['mode'] != 'chant':
            e['rapport']['nettoyage'] = {'demande': nettoyage_demande, 'applique': False,
                                         'raison': 'parole : voix isolée complète, sans nettoyage (Seed-VC 22 kHz)'}
        elif not nettoyage_demande:
            e['rapport']['nettoyage'] = {'demande': False, 'applique': False,
                                         'raison': 'nettoyage désactivé (--sans-nettoyage) : voix isolée complète convertie'}
    if nettoyage_demande and en_chant:
        log(f"NETTOYAGE de {len(en_chant)} vidéo(s) en chant : voix principale et chœurs, reverb d'origine retirée")
        try:
            nettoyer(en_chant)
        except Exception as ex:  # noqa: BLE001 — sans nettoyage : la chaîne d'avant
            import traceback
            traceback.print_exc()
            for e in en_chant:
                e['net'] = None
                e['rapport']['nettoyage'] = {'demande': True, 'applique': False,
                                             'raison': f'échec du nettoyage ({ex!r}) : voix isolée complète convertie'}
    # 5. Conversion : YingMusic-SVC d'un coup pour toutes les vidéos (étape à part), puis chaque vidéo
    for e in etats:
        try:
            e['pistes'] = preparer_pistes(e)
        except Exception as ex:  # noqa: BLE001 — pistes nettoyées illisibles : la voix complète
            log(f"[{e['tr']['id']}] pistes nettoyées illisibles ({ex!r}) : voix isolée complète convertie")
            e['net'] = None
            e['rapport']['nettoyage'].update({'applique': False, 'raison': f'pistes nettoyées illisibles ({ex!r})'})
            e['pistes'] = preparer_pistes(e)
        e['variantes'] = variantes_de(e)
    try:
        ying = convertir_ying(etats)
    except Exception as ex:  # noqa: BLE001 — repli sur Seed-VC 44 kHz
        import traceback
        traceback.print_exc()
        ying = {'erreur': repr(ex)}
    for e in etats:
        try:
            ecrire_rapport(e['tr'], finir_video(e, ying))
        except Exception as ex:  # noqa: BLE001
            echec(e['tr'], ex)
        nettoyer_fichiers(e)
        e.clear()
    shutil.rmtree('/tmp/ying', ignore_errors=True)
    session['temps']['total_s'] = round(time.time() - DEBUT, 1)
    ecrire_session()
    log(f'MESURE temps total du noyau : {time.time() - DEBUT:.0f} s ({len(travaux)} vidéo(s))')


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] in ('nettoyer', 'ying'):
        etape_isolee(sys.argv[1], sys.argv[2])
    else:
        main()
