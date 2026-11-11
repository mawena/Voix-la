"""Doublage universel sur le GPU T4 gratuit de Kaggle : un seul noyau pour une ou plusieurs vidéos.

1. Séparation voix / reste de chaque vidéo : MelBand-Roformer de Kim (vocals_mel_band_roformer.ckpt, audio-separator
   0.48.0), mêmes réglages que sur le PC (entrée ramenée à un pic de 0,9 puis remise à l'échelle, reste = original - voix).
   Les Roformer (séparation et nettoyage) tournent sous autocast fp16 (PRECISIONS_ROFORMER ; réglages
   separation_precision, nettoyage_precision ; fp32 : comme avant), recouvrement 4 (separation_recouvrement,
   nettoyage_recouvrement).
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
   Passages perdus au nettoyage (zones_perdues : la voix complète sonne, voix principale et chœurs secs se taisent ;
   typiquement un « oooh » de fond très réverbéré, jeté par le dereverb) : repris de la voix complète, mêmes coupes et
   mêmes décalages, par une troisième piste « secours » dont la porte n'est ouverte que sur eux (rapport : secours).
6. Paroles, sauf réglage paroles à false : Whisper large-v3 (transformers) sur la voix principale sèche, sinon la voix
   isolée, seulement sur les phrases chantées, langue détectée phrase par phrase, horodatage par mot, garde-fous contre
   les hallucinations ; traduction française (NLLB-200 distillé 600M) des lignes qui ne sont pas en français. Étape à
   part, comme le nettoyage ; un échec n'empêche jamais le doublage. Mémoire du GPU (le mot à mot pouvait en manquer sur
   un morceau dense, repassé alors deux fois en vain avant les segments) : Whisper ne garde plus les poids d'attention
   inutiles aux horodatages (alleger_whisper, mêmes paroles au bit près), allocateur à segments extensibles, mémoire
   libre vérifiée avant le chargement ; un morceau qui en manque encore est coupé en deux plutôt que repassé tel quel.
7. Plusieurs voix cibles, une par chanteur (réglage voix_cibles, chant seulement ; voir decider_chanteurs) : homme /
   femme d'un duo mixte, ou chanteur 1 / chanteur 2 (ordre d'apparition) d'un duo de même genre ; décalage d'octave de
   chaque couple chanteur -> voix cible ; chaque piste nettoyée de chaque morceau prend la voix du chanteur qu'on y
   reconnaît (hauteur et empreinte de la piste), si bien que voix principale et chœurs peuvent chanter en duo. Chaque
   modèle n'est chargé qu'une fois, chaque référence préparée une fois. Sans ce réglage, rien ne change.
8. Deux GPU (machine Kaggle NvidiaTeslaT4 : deux cartes T4 ; réglage gpus : auto, 1 ou 2) : un processus par carte,
   chacun ne voyant que la sienne (CUDA_VISIBLE_DEVICES). Nettoyage : le karaoké d'une vidéo sur une carte, ses deux
   dereverb (voix principale, chœurs) chacun sur la sienne (plan_nettoyage) ; YingMusic-SVC : les morceaux de toutes les
   pistes et de toutes les vidéos répartis entre les cartes (repartir_morceaux), puis chaque piste assemblée ici dans
   l'ordre de ses morceaux ; séparation d'un lot : les vidéos réparties. Chaque tâche ne dépend que de ses entrées et de
   sa graine (une par morceau) : le son rendu est celui d'une carte seule, au bit près (essai du 05/11/2026 : voix
   convertie, mix et mp3 identiques ; session de 26,6 à 19,4 min sur une chanson de 3 min 48). Les modèles se chargent
   l'un après l'autre (TourDeChargement) : chargés ensemble, ils prennent deux à trois fois plus longtemps. Paroles : leur
   piste (voix principale sèche) est prête dès la fin du nettoyage ; la dernière carte les fait d'abord (Whisper chargé
   après YingMusic-SVC sur la carte 0), pendant que les autres convertissent, puis rejoint la conversion et prend les
   morceaux encore libres (aucun : elle ne charge rien). Jamais Whisper et YingMusic-SVC ensemble sur une carte : l'étape
   paroles rend la mémoire de la carte en sortant. Une seule carte visible : la chaîne d'avant (paroles après la
   conversion) ; une carte arrêtée en route : ce qu'elle n'a pas fait est repris sur la carte 0, paroles comprises ; rien
   à convertir par YingMusic-SVC : paroles après la conversion, sur la dernière carte (le noyau garde Seed-VC chargé sur
   la carte 0).

doubler.py remplace __REGLAGES__ (JSON en base64) et SOURCE (sa propre source en base64 : le noyau tourne en carnet,
sans fichier, et en a besoin pour ses étapes à part), puis pousse ce noyau privé (GPU T4 + internet).
Entrée de chaque vidéo (jeu de données) : WAV flottant (doubler.py jusqu'au 04/11/2026), FLAC 24 bits (jusqu'au
06/11/2026) ou FLAC 16 bits (deux fois plus léger à envoyer ; clé envoi_flac de doubler.py pour revenir à 24 bits) ; le
FLAC est en entiers, doubler.py ramène donc la crête à -1 dBFS avant l'encodage et inscrit ce gain dans les réglages
(gain_entree) : le noyau le retire à la lecture, tout le reste travaille au niveau d'origine. soundfile lit les deux
FLAC en flottant : rien ne change dans le noyau.
Entrées dans /kaggle/input et sorties dans /kaggle/working ; Modal et Runpod les déplacent par DOUBLER_ENTREES et
DOUBLER_SORTIE (le même script, sans autre changement).
Sorties dans /kaggle/working/<id>/ pour chaque vidéo :
  voix_source.wav            voix isolée (44,1 kHz stéréo flottant, même longueur que l'entrée) ; si les réglages
                             demandent retour_flac : voix_source.flac (24 bits, crête à -1 dBFS) et
                             voix_source.flac.json (gain appliqué, que doubler.py retire au rapatriement)
  voix_convertie.flac        voix cible (44,1 kHz mono 24 bits, même nombre d'échantillons, calée sur l'échantillon 0)
  voix_convertie_<autre>.flac, voix_convertie_<N>pas.flac   variantes de comparaison (modèle, pas de diffusion)
  rapport.json               mode et mesures, morceaux (hauteur, voix, décalage), moteur, nettoyage, secours (zones
                             récupérées : début, fin, durée, écart), paroles, temps ; plusieurs voix cibles :
                             voix_cibles, décision par chanteur (decision_hauteur.chanteurs : voix cible et décalage de
                             chaque couple), affectation (chaque piste sur chaque morceau, sa raison, passages par voix)
  courbes.json               mélodie de la voix isolée et de la voix convertie, décalage appliqué (format 1 : une
                             valeur toutes les 20 ms ; lu par l'interface, à ne pas changer sans la prévenir) ;
                             plusieurs voix cibles : voix_cibles et voix_par_trame en plus
  paroles.json               paroles horodatées par ligne et par mot, langue, traduction française (format 1 ; contrat
                             avec l'interface) ; temps du son doublé, doubler.py les cale sur la vidéo de sortie
et /kaggle/working/session.json (installation, séparation, nettoyage, chargement des modèles, temps total ; gpus : cartes
vues et utilisées ; nettoyage_gpu_s et yingmusic_gpu_s : temps de GPU de ces étapes, toutes cartes additionnées ;
paroles.pendant_conversion et temps.paroles_pendant_conversion_s : paroles faites sur la dernière carte pendant
YingMusic-SVC, et leur durée, que yingmusic_total_s comprend alors ; paroles_total_s : attente de la copie des modèles et
durée de l'étape paroles seule).
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
# Chemins d'entrée et de sortie : ceux de Kaggle par défaut ; Modal et Runpod les déplacent par ces variables
# d'environnement (DOUBLER_ENTREES : jeu de données et outils montés ; DOUBLER_SORTIE : dossier des sorties)
ENTREES = os.environ.get('DOUBLER_ENTREES') or '/kaggle/input'
SORTIE = os.environ.get('DOUBLER_SORTIE') or '/kaggle/working'
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
# Précision des Roformer (séparation, karaoké, dereverb) : options du Separator d'audio-separator 0.48.0. autocast
# (défaut) : torch.autocast autour du modèle, les cœurs fp16 du T4 ; fp16 : poids en demi-précision (use_native_fp16,
# vérifié par audio-separator pour les MelBand/BS-Roformer sur CUDA), même vitesse, un peu moins près du fp32 ;
# fp16+compile : en plus torch.compile des blocs du Roformer (environ 20 s de compilation par modèle et par carte,
# rentable au-delà de 4 min de son environ) ; fp32 : la chaîne d'avant. Un son non fini en demi-précision est refait
# en fp32. Banc vitesse du 06/11/2026 (deux extraits de 45 s, T4) : séparation 2,4 fois plus rapide, nettoyage 2,5 fois,
# pistes à 50-75 dB du fp32 (SDR), chœurs secs à 38-50 dB ; mélodie, timbre et paroles convertis dans le bruit de mesure.
PRECISIONS_ROFORMER = {'fp32': {}, 'autocast': {'use_autocast': True}, 'fp16': {'use_native_fp16': True},
                       'fp16+compile': {'use_native_fp16': True, 'use_torch_compile': True}}
SEPARATION_DEFAUT = {'precision': 'autocast', 'recouvrement': 4}   # réglages separation_precision, separation_recouvrement
NETTOYAGE_PRECISION_DEFAUT = 'autocast'                             # réglage nettoyage_precision


def reglage_separation(R):
    """Précision et recouvrement du Roformer de séparation (réglages separation_precision et separation_recouvrement,
    sinon SEPARATION_DEFAUT), avec les options du Separator qui vont avec."""
    precision = R.get('separation_precision') or SEPARATION_DEFAUT['precision']
    if precision not in PRECISIONS_ROFORMER:
        precision = 'fp32'
    return {'precision': precision, 'options': dict(PRECISIONS_ROFORMER[precision]),
            'recouvrement': int(R.get('separation_recouvrement') or SEPARATION_DEFAUT['recouvrement'])}
COMPLET = '/tmp/doubler_complet.py'   # copie de ce noyau sur disque, pour les étapes à part
SOURCE = '__SOURCE__'

SEPARER = r'''
import json, sys, time
import numpy as np
import soundfile as sf


def lire_entree(chemin, gain_entree=1.0):
    """Son d'entrée en flottant 32 bits, au niveau d'origine. Un FLAC (entiers 16 ou 24 bits) a été ramené par doubler.py
    à une crête de -1 dBFS avant l'encodage : on divise par ce même gain (réglage gain_entree). Un WAV flottant (ancien
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


def charger_separateur(tmp, reglage):
    """Roformer de séparation chargé. reglage (du noyau) : options de précision d'audio-separator (use_native_fp16,
    use_autocast) et recouvrement ; une option refusée (audio-separator plus ancien) : fp32, comme avant. Rend
    (séparateur, précision appliquée)."""
    from audio_separator.separator import Separator
    options = dict(reglage.get('options') or {})
    mdxc = {'segment_size': 801, 'override_model_segment_size': True, 'batch_size': 1,
            'overlap': int(reglage.get('recouvrement') or 4), 'pitch_shift': 0}
    try:
        sep = Separator(model_file_dir='/tmp/modeles_sep', output_dir=tmp, output_format='WAV', use_soundfile=True,
                        normalization_threshold=1.0, sample_rate=44100, mdxc_params=mdxc, **options)
    except TypeError as e:
        print(f'SEPARATION options {options} refusées ({e}) : fp32', flush=True)
        sep = Separator(model_file_dir='/tmp/modeles_sep', output_dir=tmp, output_format='WAV', use_soundfile=True,
                        normalization_threshold=1.0, sample_rate=44100, mdxc_params=mdxc)
    sep.load_model(model_filename='vocals_mel_band_roformer.ckpt')
    return sep, getattr(getattr(sep, 'model_instance', None), 'effective_precision', 'fp32')


if __name__ == '__main__':
    import os
    travaux, tmp = json.loads(sys.argv[1]), sys.argv[2]
    # deux cartes : la seconde attend que la première ait chargé le modèle (chargés ensemble, ils sont bien plus lents)
    attendre, signaler = (sys.argv[3:5] + ['', ''])[:2]
    reglage = (travaux[0].get('separation') if travaux else None) or {}
    t_attente = time.time()
    while attendre and not os.path.exists(attendre) and time.time() - t_attente < 300:
        time.sleep(0.5)
    t0 = time.time()
    sep, precision = charger_separateur(tmp, reglage)
    sep_fp32 = None   # repli si la demi-précision rend un son non fini (débordement) : la vidéo est refaite en fp32
    if signaler:
        open(signaler, 'w').close()
    print(f'SEPARATION modèle chargé en {time.time() - t0:.0f} s (précision {precision}, recouvrement '
          f"{int(reglage.get('recouvrement') or 4)})", flush=True)
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
        if not np.isfinite(v).all() and precision != 'fp32':
            print(f"SEPARATION {t['id']} : son non fini en {precision}, refait en fp32", flush=True)
            if sep_fp32 is None:
                sep_fp32 = charger_separateur(tmp, {'recouvrement': reglage.get('recouvrement')})[0]
            sep_fp32.separate(f'{tmp}/entree.wav', custom_output_names={'Vocals': 'voix_brute', 'Other': 'autre_brut',
                                                                        'Instrumental': 'autre_brut'})
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
    fiches = glob.glob(f'{ENTREES}/**/outils.json', recursive=True)
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
    paroles = preparer_paroles_outils(w)
    import platform
    sep = os.path.join(w, 'modeles_sep')
    fiche = {'commit': sha, 'python': platform.python_version(), 'torch': torch.__version__, 'paquets': PAQUETS,
             'paroles': paroles,
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


def copier_paroles(outils, dest='/tmp/paroles_modeles'):
    """Copie, dans un fil à part, des modèles de paroles du montage d'outils vers le disque local. from_pretrained lit
    les poids en mmap : sur /kaggle/input, les 3 Go de Whisper large-v3 ont mis 310 s à se charger (extrait d'essai du
    04/11/2026), quand une lecture d'un trait (tar des autres poids) va dix fois plus vite. Lancée dès l'installation,
    la copie se fait pendant la séparation, le nettoyage et la conversion. Rend l'état à passer à transcrire."""
    import threading
    fiche = (lire_outils(outils).get('paroles') or {}) if outils else {}
    paires = {}
    for cle in ('whisper', 'traducteur'):
        rel = fiche.get(cle)
        src = os.path.join(outils, rel) if outils and rel else None
        if src and os.path.isfile(os.path.join(src, 'config.json')):
            paires[cle] = (src, os.path.join(dest, os.path.basename(src.rstrip('/'))))
    etat = {'paires': paires, 'copies': {}, 'duree_s': None, 'erreur': None, 'fil': None}
    if not paires:
        return etat

    def copier():
        t = time.time()
        for cle, (src, dst) in paires.items():
            try:
                if not os.path.isfile(os.path.join(dst, 'config.json')):
                    shutil.rmtree(dst + '.part', ignore_errors=True)
                    shutil.copytree(src, dst + '.part')
                    os.replace(dst + '.part', dst)
                etat['copies'][cle] = dst
            except Exception as e:  # noqa: BLE001 — disque plein… : le montage, plus lent
                shutil.rmtree(dst + '.part', ignore_errors=True)
                etat['erreur'] = f'{cle} : {e!r}'[:300]
        etat['duree_s'] = round(time.time() - t, 1)
    etat['fil'] = threading.Thread(target=copier, daemon=True)
    etat['fil'].start()
    return etat


def chemins_paroles(etat, attendre=True):
    """{'whisper': chemin, 'traducteur': chemin} à charger : la copie locale si elle est finie et complète, sinon le
    montage (None : téléchargé depuis Hugging Face). Attend la fin de la copie ; rend aussi l'attente (s)."""
    t = time.time()
    if attendre and etat.get('fil') is not None:
        etat['fil'].join()
    chemins = {cle: etat['copies'].get(cle) or src for cle, (src, _) in etat.get('paires', {}).items()}
    return chemins, round(time.time() - t, 1)


def preparer_paroles_outils(w):
    """Noyau « outils » : dossiers complets de Whisper large-v3 et de NLLB-200 distillé 600M (from_pretrained les lit
    tels quels dans le montage ; sinon le noyau de doublage les télécharge), puis essai dans un processus à part :
    chargement, transcription d'un exemple de Seed-VC (ou d'une note de synthèse) et traduction. Rend la fiche."""
    from huggingface_hub import HfApi, snapshot_download
    fiche = {}
    for cle, (depot, nom) in (('whisper', WHISPER), ('traducteur', TRADUCTEUR)):
        dest = os.path.join(w, 'paroles', nom)
        t0 = time.time()
        try:
            fichiers = HfApi().list_repo_files(depot)
            poids = 'model.safetensors' if 'model.safetensors' in fichiers else 'pytorch_model.bin'
            garder = [f for f in fichiers if '/' not in f and f.endswith(('.json', '.txt', '.model'))
                      and 'index' not in f] + [poids]
            snapshot_download(depot, local_dir=dest, allow_patterns=garder)
            fiche[cle] = f'paroles/{nom}'
            fiche[f'{cle}_mo'] = round(sum(os.path.getsize(os.path.join(dest, f)) for f in os.listdir(dest)
                                           if os.path.isfile(os.path.join(dest, f))) / 1e6)
            log(f"outils : {nom} ({poids}, {fiche[f'{cle}_mo']} Mo) en {time.time() - t0:.0f} s")
        except Exception as e:  # noqa: BLE001 — le noyau de doublage les téléchargera
            fiche[f'{cle}_erreur'] = repr(e)[:300]
            log(f'outils : {nom} indisponible ({e!r})')
    import numpy as np
    import soundfile as sf
    exemples = sorted(glob.glob(os.path.join(SEEDVC, 'examples', '**', '*.wav'), recursive=True))
    os.makedirs('/tmp/paroles', exist_ok=True)
    audio = '/tmp/paroles/essai.wav'
    try:
        import librosa
        x = librosa.load(exemples[0], sr=SR, mono=True)[0][:SR * 20]
        fiche['essai_audio'] = os.path.relpath(exemples[0], SEEDVC)
    except Exception:  # noqa: BLE001 — pas d'exemple : une note tenue suffit à éprouver le chargement et l'appel
        x = (0.1 * np.sin(2 * np.pi * 220 * np.arange(SR * 4) / SR)).astype(np.float32)
        fiche['essai_audio'] = 'note de synthèse'
    sf.write(audio, x, SR, subtype='FLOAT')
    d = round(len(x) / SR, 2)
    travail = {'whisper': os.path.join(w, fiche['whisper']) if fiche.get('whisper') else None,
               'traducteur': os.path.join(w, fiche['traducteur']) if fiche.get('traducteur') else None,
               'cache': '/tmp/hf_paroles', 'resultat': '/tmp/paroles/essai_resultat.json', 'traduire_toujours': True,
               'videos': [{'id': 'essai', 'audio': audio, 'piste': 'essai', 'phrases': [[0.0, d]], 'chant': [[0.0, d]],
                           'sortie': '/tmp/paroles/essai_paroles.json'}]}
    try:
        essai = etape_a_part('paroles', travail)
    except Exception as e:  # noqa: BLE001
        essai = {'erreur': repr(e)}
    sortie = lire_json('/tmp/paroles/essai_paroles.json', {}) or {}
    fiche['essai'] = {k: essai.get(k) for k in ('implementation', 'whisper_origine', 'traducteur_origine', 'chargements_s',
                                                'temps', 'erreur', 'erreur_traduction', 'code')}
    fiche['essai'].update({'video': (essai.get('videos') or {}).get('essai'),
                           'texte': ' / '.join(s['texte'] for s in sortie.get('segments', [])[:3])[:300],
                           'traduction': ' / '.join(s['texte'] for s in ((sortie.get('traductions') or {}).get('fr') or {})
                                                    .get('segments', [])[:3])[:300]})
    shutil.rmtree('/tmp/hf_paroles', ignore_errors=True)
    log('outils : essai des paroles : ' + json.dumps(fiche['essai'], ensure_ascii=False)[:1500])
    return fiche


def trouver(nom):
    chemins = glob.glob(f'{ENTREES}/**/{nom}', recursive=True)
    if not chemins:
        log(f'FICHIER ABSENT du jeu de données : {nom} (présents : '
            f"{sorted(os.path.basename(p) for p in glob.glob(f'{ENTREES}/**/*.*', recursive=True))})")
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
# >= +0,03 homme ; <= -0,10 femme, mais seulement à partir de 255 Hz : plus bas, une voix d'homme éraillée peut avoir
# une empreinte de femme), puis notes graves (10e centile < 190 Hz -> homme), sinon la hauteur seule (homme sous 281 Hz,
# milieu géométrique de la zone, femme au-dessus : frontière fixe, qui ne dépend plus de la voix cible depuis la v2.2).
# Duo seulement si deux groupes d'empreintes nettement séparés, chantant chacun en sections suivies, et de décalages
# différents ; changement de voix par Viterbi, moins cher à une pause, sections de 6 s au moins.

OCTAVE_DEFAUT = {
    'zone_basse_hz': 240.0,          # en dessous : voix d'homme d'après la hauteur seule
    'zone_haute_hz': 330.0,          # au-dessus : voix de femme d'après la hauteur seule
    'genre_homme': 0.03,             # genre_score médian d'une voix, à partir duquel on la tient pour un homme
    'genre_femme': -0.10,            # ... en deçà duquel on la tient pour une femme
    'femme_empreinte_min_hz': 255.0,  # ... et seulement à partir de cette hauteur : en dessous, une empreinte de
                                      # femme peut venir d'une voix d'homme éraillée (281 Hz jusqu'à la v2.2, pour
                                      # des ancres faites d'une seule voix par genre ; 255 avec des ancres de parole)
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
    info = {'regle': 'v2.3 (une voix = un décalage ; empreinte de femme ignorée sous 255 Hz ; hauteur seule : '
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


# --- Plusieurs voix cibles : une par chanteur (chant seulement) -------------------------------------------------------
# doubler.py envoie, en plus de R['voix'] (la première voix : analyse, parole), R['voix_cibles'] = {'mode': 'genre' ou
# 'ordre', 'voix': [{'cle', 'nom', 'fichier', 'f0_parole_hz', 'facteur_chant', 'affichage'}, …]}, avec au moins deux
# voix différentes ; sans lui (ou avec une seule voix), tout reste comme avant, au bit près.
#   genre : cle homme / femme. Le chanteur et la chanteuse d'un duo mixte sont trouvés par la règle d'octave v2.3 (axe
#           homme - femme des ancres de genre) ; chacun prend la voix de sa classe.
#   ordre : cle chanteur1 / chanteur2, pour un duo de même genre. Duo cherché d'abord sur l'axe du genre (un duo mixte y
#           est bien plus sûr), sinon sur l'axe des empreintes de la chanson elle-même : les deux groupes des k-moyennes
#           sphériques des empreintes CAMPPlus, score cos(e, c2) - cos(e, c1). Chanteur 1 : celui qui chante le premier.
#           Calibrage (rapports locaux, 04/11/2026) : silhouette des deux groupes 0,18 à 0,24 pour une chanteuse seule
#           (le style, couplet contre refrain, sépare aussi les empreintes : écart et sections n'y suffisent pas), 0,30
#           à 0,46 pour quatre vrais duos ; seuil 0,27.
# Une seule voix trouvée : tout prend la voix de sa classe (genre), sinon la première (ordre), avec un message.
# Décalage de chaque couple chanteur -> voix cible : la règle d'octave (centre de chant typique de la classe du chanteur
# rapproché du centre de chant de SA voix cible).
# Ensuite (affecter_pistes), chaque piste nettoyée de chaque morceau prend la voix du chanteur qu'on y reconnaît, par la
# hauteur et l'empreinte de la piste sur ce morceau (vraisemblances des deux chanteurs) ; incertain, ou piste trop faible
# (reste du karaoké) : le chanteur du morceau.

CHANTEURS_DEFAUT = {
    'axe_silhouette_min': 0.27,      # ordre, axe des empreintes : silhouette minimale des deux groupes
    'axe_ecart_min': 0.15,           # ... écart minimal des médianes des deux groupes (cos(e, c2) - cos(e, c1))
    'marge_reconnaissance': 2.0,     # écart de log-vraisemblance pour reconnaître le chanteur d'une piste (e² ≈ 7 fois
                                     # plus probable) ; en deçà : incertain, chanteur du morceau
    'piste_min_db': -20.0,           # piste plus faible que la voix complète sur le morceau : chanteur du morceau
    'sigma_hauteur_min_oct': 0.1,    # écart-type plancher de la hauteur d'un chanteur (octaves, hauteur par morceau)
    # Mode genre (l'utilisateur annonce un duo mixte) : sur l'axe du genre, les chanteurs peuvent alterner phrase à
    # phrase (essai réel du 04/11/2026 : une fin de duo où ils se répondent toutes les 3 à 7 s, z des suites +1,0, la
    # voix la moins présente 11 s) ; pas d'épreuve des sections, 6 s voisées au moins par voix, sections de 2,5 s.
    # Garde-fous : deux groupes de même classe ne font qu'une voix ; l'empreinte ne tranche la classe contre la
    # hauteur (un chanteur à 345 Hz) que si un groupe est nettement d'homme (≥ genre_homme) et l'autre nettement de
    # femme (≤ genre_femme) : une chanteuse seule dont les empreintes se partagent reste une voix (rapports locaux).
    'genre_z_suites': None,
    'genre_voise_min_s': 6.0,
    'genre_section_min_s': 2.5,
    # Trois voix cibles ou plus (decider_chanteurs_n)
    'n_temperature': 0.05,           # une différence de 0,05 de cosinus d'empreinte = 1 de log-vraisemblance (Viterbi à N états)
    'n_section_min_s': 3.0,          # les N chanteurs sont annoncés : sections plus courtes qu'avec la règle d'octave
    'n_part_min': 0.05,              # part minimale du temps voisé de chaque chanteur
    'n_voise_min_s': 8.0,            # ... et temps voisé minimal
}


def lire_cibles(R):
    """Les voix cibles de R['voix_cibles'] ({'mode', 'voix': [...]}), ou None : une seule voix (cas d'avant). Deux voix
    de même nom n'en font qu'une."""
    vc = R.get('voix_cibles') or None
    if not vc:
        return None
    voix, noms = [], set()
    for v in vc.get('voix') or []:
        if v.get('nom') and v.get('fichier') and v['nom'] not in noms:
            voix.append(dict(v))
            noms.add(v['nom'])
    if len(voix) < 2:
        return None
    mode = vc.get('mode') or ('genre' if {v.get('cle') for v in voix} <= {'homme', 'femme'} else
                              'personnes' if all(str(v.get('cle')).startswith('personne') for v in voix) else 'ordre')
    if mode not in ('genre', 'ordre', 'personnes'):
        raise ValueError(f'voix_cibles : mode {mode!r} inconnu (genre, ordre ou personnes)')
    if mode == 'genre' and len(voix) > 2:
        raise ValueError('voix_cibles : homme et femme, deux voix au plus')
    return {'mode': mode, 'voix': voix}


def lire_comparees(R):
    """Les voix à comparer de R['comparer'] ({'voix': [...]}), ou None : un doublage à une voix (cas d'avant). Le même
    extrait est converti dans chacune, en une seule session (séparation, nettoyage et paroles faits une fois). Deux voix de
    même nom n'en font qu'une ; moins de deux voix : None. La première est la voix d'analyse (R['voix'])."""
    c = R.get('comparer') or None
    if not c:
        return None
    voix, noms = [], set()
    for v in c.get('voix') or []:
        if v.get('nom') and v.get('fichier') and v['nom'] not in noms:
            voix.append(dict(v))
            noms.add(v['nom'])
    return voix if len(voix) >= 2 else None


def mediane_axe(morceaux, cle, R):
    v = [(s[cle], s['voise_s']) for s in morceaux if s.get(cle) is not None and s.get('voise_s', 0) >= R['voise_min_s']]
    return mediane_ponderee([a for a, _ in v], [b for _, b in v]) if v else None


def chercher_duo(morceaux, actifs_i, cle, R, ecart_min):
    """Essai de duo sur un axe (cle : genre_score, ou score_empreinte), mêmes épreuves que decider_octave : assez de
    morceaux, deux groupes (k-moyennes à une dimension) d'écart suffisant, chacun chantant assez, en sections suivies.
    Rend (duo, pourquoi, essai) ; duo = {'seuil', 'mu', 'sigma', 'groupes'} ou None."""
    elig = [i for i in actifs_i if morceaux[i].get(cle) is not None and morceaux[i]['voise_s'] >= R['voise_min_s']]
    if len(elig) < R['duo_morceaux_min']:
        return None, f"{len(elig)} morceaux avec empreinte : trop peu pour chercher un duo", None
    seuil, _ = deux_moyennes([morceaux[i][cle] for i in elig], [morceaux[i]['voise_s'] for i in elig], R['duo_part_min'])
    if seuil is None:
        return None, 'empreintes toutes égales', None
    groupes = [[morceaux[i] for i in elig if (morceaux[i][cle] > seuil) == bool(k)] for k in (0, 1)]
    mu = [mediane_axe(gr, cle, R) for gr in groupes]
    voise = [sum(s['voise_s'] for s in gr) for gr in groupes]
    tot = sum(voise)
    ecart = mu[1] - mu[0]
    z = z_suites([int(morceaux[i][cle] > seuil) for i in elig])
    essai = {'axe': cle, 'seuil': round(seuil, 3), 'ecart': round(ecart, 3), 'z_suites': round(z, 2),
             'parts': [round(v / tot, 3) for v in voise], 'mu': [round(m, 3) for m in mu]}
    if ecart < ecart_min:
        return None, f"empreintes trop proches (écart {ecart:.3f} < {ecart_min})", essai
    if min(voise) < max(R['duo_part_min'] * tot, R['duo_voise_min_s']):
        return None, (f"une des deux voix chante trop peu ({min(voise):.0f} s voisées, {min(essai['parts']):.0%} ; il en "
                      f"faut {R['duo_part_min']:.0%} et {R['duo_voise_min_s']:.0f} s)"), essai
    if R['duo_z_suites'] is not None and z > R['duo_z_suites']:
        return None, f"empreintes mêlées dans le temps, pas en sections (z des suites {z:.1f} > {R['duo_z_suites']})", essai
    sigma = []
    for k, gr in enumerate(groupes):
        v = [s[cle] for s in gr]
        sigma.append(max(math.sqrt(sum((g - mu[k]) ** 2 for g in v) / len(v)) if v else 0.0, R['sigma_min']))
    essai['sigma'] = [round(x, 3) for x in sigma]
    return {'seuil': seuil, 'mu': mu, 'sigma': sigma, 'groupes': groupes}, None, essai


def axe_empreintes(morceaux, actifs_i, R):
    """Axe des chanteurs d'après les empreintes de la chanson (mode ordre, duo de même genre) : k-moyennes sphériques
    (k = 2) des empreintes normées des morceaux assez voisés ; rend (vecteur c2 - c1, silhouette, morceaux) ou
    (None, silhouette, n) s'il n'y a pas assez de morceaux."""
    import numpy as np
    elig = [i for i in actifs_i if morceaux[i].get('emb') is not None and morceaux[i]['voise_s'] >= R['voise_min_s']]
    if len(elig) < R['duo_morceaux_min']:
        return None, None, len(elig)
    e = np.stack([np.asarray(morceaux[i]['emb'], np.float64) for i in elig])
    lab, c = kmeans_cos(e, 2)
    if len(set(lab.tolist())) < 2:
        return None, None, len(elig)
    return c[1] - c[0], silhouette_cos(e, lab), len(elig)


def ordre_apparition(lab):
    """Rang (0 : premier, 1 : second) de chaque groupe de voix (0 / 1) dans l'ordre d'apparition."""
    premier = lab[0] if lab else 0
    return {premier: 0, 1 - premier: 1}


def decider_chanteurs(morceaux, cibles, reglages=None, coupes_son=None):
    """Chanteur de chaque morceau et voix cible de chaque chanteur (plusieurs voix cibles, chant).

    morceaux : comme pour decider_octave (genre_score si des ancres de genre), plus 'emb' (empreinte normée ou None).
    cibles : {'mode': 'genre' | 'ordre', 'voix': [{'cle', 'nom', 'centre_hz', ...}, ...]} (centre de chant de chaque voix).
    Rend (resultats, info, modele) : resultats[i] = {'decalage', 'voix' (nom du chanteur), 'base', 'k' (groupe 0 / 1 ou
    None), 'cible' (indice de la voix cible), 'chanteur' (clé de la voix cible : homme, chanteur1…)} ; info pour le
    rapport ; modele : de quoi reconnaître les chanteurs sur les pistes (None sans duo)."""
    import numpy as np
    R = {**OCTAVE_DEFAUT, **CHANTEURS_DEFAUT, **(reglages or {})}
    mode, V = cibles['mode'], cibles['voix']
    if mode == 'personnes':
        mode = 'ordre'   # personneN : en chant, comme chanteurN
    if len(V) >= 3:   # trois voix ou plus : N chanteurs ; moins de trois trouvés : le duo ou la voix seule, ci-dessous
        n = decider_chanteurs_n(morceaux, cibles, reglages, coupes_son)
        if n is not None:
            return n
    index_cle = {v.get('cle'): j for j, v in enumerate(V)}

    def decalage(classe, j):
        return meilleure_octave(R[f'centre_{classe}_hz'], V[j]['centre_hz'])
    info = {'regle': 'v2.3, une voix cible par chanteur', 'mode': mode,
            'voix_cibles': [{'cle': v.get('cle'), 'nom': v['nom'], 'centre_chant_hz': round(v['centre_hz'], 1)} for v in V],
            'decalage_par_voix_et_classe': {v['nom']: {c: decalage(c, j) for c in ('homme', 'femme')} for j, v in enumerate(V)},
            'zone_ambigue_hz': [R['zone_basse_hz'], R['zone_haute_hz']], 'frontiere_hauteur_seule_hz': R['frontiere_hz']}
    actifs_i = [i for i, s in enumerate(morceaux) if not s.get('muet') and s.get('voise_s', 0) > 0]
    tout = [morceaux[i] for i in actifs_i]
    mg = mesurer_voix(tout, R) if tout else {}
    classe_g, raison_g = classer(mg, R) if tout else (None, 'aucun morceau chanté')
    co_g = classe_g or (classe_hauteur(mg['f0_med_hz'], R) if mg.get('f0_med_hz') else None)
    info['voix_principale'] = {**mg, 'classe': classe_g, 'raison': raison_g}

    # 1. Deux chanteurs ? Axe du genre d'abord (ancres), puis, en mode ordre, axe des empreintes de la chanson
    duo, axe, pourquoi, essais = None, None, None, {}
    # duo mixte annoncé (mode genre) : alternance phrase à phrase permise sur l'axe du genre (CHANTEURS_DEFAUT)
    Rg = ({**R, 'duo_z_suites': R['genre_z_suites'], 'duo_voise_min_s': R['genre_voise_min_s'],
           'section_min_s': R['genre_section_min_s']} if mode == 'genre' else R)
    Rd = R
    if not actifs_i:
        pourquoi = 'aucun morceau chanté'
    elif any(morceaux[i].get('genre_score') is not None for i in actifs_i):
        duo, pourquoi, essais['genre'] = chercher_duo(morceaux, actifs_i, 'genre_score', Rg, R['duo_ecart_genre'])
        axe, Rd = ('genre_score', Rg) if duo else (None, R)
    else:
        pourquoi = 'sans ancres de genre : pas de duo sur l\'axe du genre'
    vecteur = None
    if duo is None and mode == 'ordre' and actifs_i:
        vecteur, sil, nb = axe_empreintes(morceaux, actifs_i, R)
        essais['empreintes'] = {'morceaux': nb, 'silhouette': None if sil is None else round(sil, 3)}
        if vecteur is None:
            pourquoi += f" ; axe des empreintes : {nb} morceaux, trop peu"
        elif sil < R['axe_silhouette_min']:
            pourquoi += f" ; axe des empreintes : groupes trop peu séparés (silhouette {sil:.2f} < {R['axe_silhouette_min']})"
            vecteur = None
        else:
            for i in actifs_i:
                e = morceaux[i].get('emb')
                morceaux[i]['score_empreinte'] = None if e is None else round(float(np.asarray(e, np.float64) @ vecteur), 3)
            duo, p2, e2 = chercher_duo(morceaux, actifs_i, 'score_empreinte', R, R['axe_ecart_min'])
            essais['empreintes'].update(e2 or {})
            if duo:
                axe = 'score_empreinte'
            else:
                pourquoi += f' ; axe des empreintes : {p2}'
                vecteur = None
    info['essai_duo'] = essais

    # 2. Duo : chanteur de chaque morceau (Viterbi sur l'axe retenu, sections minimales), classe de chaque chanteur
    lab, act, mes = None, None, None
    if duo:
        mu, sigma = duo['mu'], duo['sigma']
        pause = [False]
        for a, b in zip(actifs_i, actifs_i[1:]):
            en_son = coupes_son is None or any(abs(morceaux[b]['debut_s'] - c) < 0.006 for c in coupes_son)
            pause.append(b - a > 1 or not en_son)
        act = [morceaux[i] for i in actifs_i]
        sur_axe = [{**s, 'genre_score': s.get(axe)} for s in act]
        lab = lisser_sections(sur_axe, viterbi(sur_axe, mu, sigma, pause, Rd), Rd)
        voise = [sum(s['voise_s'] for s, k in zip(act, lab) if k == j) for j in (0, 1)]
        if min(voise) < max(Rd['duo_part_min'] * sum(voise), Rd['duo_voise_min_s']):
            pourquoi, duo = f"après attribution, une voix chante trop peu ({min(voise):.0f} s voisées)", None
    if duo:
        mes = [mesurer_voix(gr, R) for gr in duo['groupes']]
        # duo mixte annoncé, empreintes nettement d'homme et de femme : l'empreinte donne la classe, même contre la
        # hauteur (un chanteur qui monte à 345 Hz, une chanteuse grave)
        g_med = [m.get('genre_med') for m in mes]
        par_empreinte = (mode == 'genre' and axe == 'genre_score' and None not in g_med
                         and g_med[1] >= R['genre_homme'] and g_med[0] <= R['genre_femme'])
        for k, m in enumerate(mes):
            c, r = classer(m, R, sur_seulement=True)
            if par_empreinte:
                c = 'femme' if k == 0 else 'homme'
                r = (f"duo mixte d'après l'empreinte : {g_med[k]:+.3f} (" + ('femme' if k == 0 else 'homme')
                     + (f", hauteur {m['f0_med_hz']:.0f} Hz" if m.get('f0_med_hz') else '') + ')')
            elif c is None:
                ch = classe_hauteur(m['f0_med_hz'], R) if m.get('f0_med_hz') else None
                attendue = ('femme' if k == 0 else 'homme') if axe == 'genre_score' else ch
                if ch is not None and ch == attendue:
                    r += (", d'après la hauteur seule" + (f" (empreinte plus {'basse' if k == 0 else 'haute'} que l'autre "
                                                          'voix)' if axe == 'genre_score' else ''))
                    c = ch
                else:
                    c, r = co_g, r + ' : comme la voix principale'
            m.update({'classe': c, 'raison': r, 'voise_attribue_s': round(voise[k], 1)})
        if mode == 'genre' and mes[0]['classe'] == mes[1]['classe']:
            pourquoi = (f"deux voix probables, toutes deux de classe {mes[0]['classe']} : une seule voix cible (pour une voix "
                        'par chanteur dans un duo de même genre : chanteur1:…,chanteur2:…)')
            duo = None

    resultats = [None] * len(morceaux)
    if duo is None:
        j = index_cle.get(co_g, 0) if mode == 'genre' else 0
        d = decalage(co_g, j) if co_g else 0
        cle = V[j].get('cle')
        if not actifs_i:
            msg = f"aucun morceau chanté : voix {V[j]['nom']}"
        elif mode == 'genre':
            msg = (f"une seule voix détectée ({co_g or 'classe inconnue'}{'' if classe_g else ', d’après la hauteur'}) : "
                   f"tout prend la voix {V[j]['nom']}" + ('' if co_g in index_cle else ' (la première : aucune voix pour cette classe)'))
        else:
            msg = f"une seule voix détectée : tout prend la voix du chanteur 1, {V[j]['nom']}"
        info.update({'voix': 1 if actifs_i else 0, 'pourquoi_une_voix': pourquoi, 'message': msg,
                     'chanteurs': [{'nom': co_g or 'voix', 'classe': co_g, 'raison': raison_g, 'cible': j, 'voix': V[j]['nom'],
                                    'cle': cle, 'centre_chanteur_hz': R[f'centre_{co_g}_hz'] if co_g else None,
                                    'centre_cible_hz': round(V[j]['centre_hz'], 1), 'decalage': d}]})
        nom = f"voix seule ({co_g or 'd’après la hauteur'}) → {V[j]['nom']}"
        for i, s in enumerate(morceaux):
            resultats[i] = {'decalage': d, 'voix': classe_g, 'base': nom if i in actifs_i else muet_ou_sourd(s), 'k': None,
                            'cible': j, 'chanteur': cle}
        return resultats, info, None

    # 3. Voix cible et décalage de chaque chanteur (couple chanteur -> voix cible)
    rang = ordre_apparition(lab)
    cls = [mes[k]['classe'] for k in (0, 1)]
    if mode == 'genre':
        cible = [index_cle.get(cls[k], 0) for k in (0, 1)]
        noms = cls
    else:
        cible = [rang[k] for k in (0, 1)]
        noms = cls if cls[0] != cls[1] else [f'{cls[k] or "voix"} {rang[k] + 1}' for k in (0, 1)]
    decs = [decalage(cls[k], cible[k]) if cls[k] else 0 for k in (0, 1)]
    chanteurs = []
    for k in (0, 1):
        j = cible[k]
        chanteurs.append({**mes[k], 'nom': noms[k], 'rang': rang[k] + 1, 'cible': j, 'voix': V[j]['nom'], 'cle': V[j].get('cle'),
                          'centre_chanteur_hz': R[f'centre_{cls[k]}_hz'] if cls[k] else None,
                          'centre_cible_hz': round(V[j]['centre_hz'], 1), 'decalage': decs[k],
                          'mu_axe': round(duo['mu'][k], 3), 'sigma_axe': round(duo['sigma'][k], 3)})
    for j, i in enumerate(actifs_i):
        k = lab[j]
        g = morceaux[i].get(axe)
        nette = g is not None and abs(g - duo['mu'][k]) <= abs(g - duo['mu'][1 - k])
        resultats[i] = {'decalage': decs[k], 'voix': noms[k], 'k': k, 'cible': cible[k], 'chanteur': V[cible[k]].get('cle'),
                        'base': f"duo, voix {noms[k]} → {V[cible[k]]['nom']}" + ('' if nette else ' (d’après les voisins)')}
    dernier = None
    for i in range(len(morceaux)):
        if resultats[i] is None:
            if dernier is None:
                dernier = resultats[actifs_i[0]]
            resultats[i] = {'decalage': dernier['decalage'], 'voix': None, 'base': muet_ou_sourd(morceaux[i]), 'k': dernier['k'],
                            'cible': dernier['cible'], 'chanteur': dernier['chanteur']}
        else:
            dernier = resultats[i]
    secs = sections(act, lab)
    info.update({'voix': 2, 'axe': 'genre' if axe == 'genre_score' else 'empreintes', 'chanteurs': chanteurs,
                 'message': 'duo : ' + ', '.join(f"{c['nom']} → {c['voix']} ({c['decalage']:+d})" for c in chanteurs),
                 'changements': [{'a_s': act[d]['debut_s'], 'vers': noms[k], 'voix': V[cible[k]]['nom']} for d, _, k, _ in secs[1:]]})
    # Modèle de chaque chanteur pour reconnaître les pistes : axe (moyenne, écart-type) et hauteur (log2, en octaves)
    hauteur = []
    for k in (0, 1):
        f = [(math.log2(s['f0_med']), s['voise_s']) for s, kk in zip(act, lab)
             if kk == k and s.get('f0_med') and s['voise_s'] >= R['voise_hauteur_s']]
        if f:
            m = mediane_ponderee([a for a, _ in f], [b for _, b in f])
            sd = math.sqrt(sum(b * (a - m) ** 2 for a, b in f) / sum(b for _, b in f))
            hauteur.append((m, max(sd, R['sigma_hauteur_min_oct'])))
        else:
            hauteur.append(None)
    modele = {'axe': axe, 'vecteur': vecteur, 'mu': list(duo['mu']), 'sigma': list(duo['sigma']), 'hauteur': hauteur,
              'cible': cible, 'decalage': decs, 'noms': noms}
    info['modele_pistes'] = {'axe': info['axe'], 'mu': [round(x, 3) for x in duo['mu']],
                             'sigma': [round(x, 3) for x in duo['sigma']],
                             'hauteur_hz': [None if h is None else round(2 ** h[0], 1) for h in hauteur],
                             'hauteur_sigma_oct': [None if h is None else round(h[1], 3) for h in hauteur]}
    return resultats, info, modele


# --- De trois à six voix cibles (chant) : N chanteurs repérés par leurs empreintes ---------------------------------------
# Avec trois voix cibles ou plus (--voix chanteur1:a,chanteur2:b,chanteur3:c…, ou personne1:…), le duo à deux groupes ne
# suffit plus : les empreintes CAMPPlus des morceaux assez voisés sont regroupées en N groupes (k-moyennes sphériques,
# comme l'axe des empreintes du duo), l'étiquette de chaque morceau est lissée dans le temps (Viterbi à N états : un
# changement de chanteur coûte moins à une pause qu'en plein son ; une section trop courte rejoint la plus longue de ses
# voisines), et les chanteurs sont pris dans l'ordre d'apparition : le premier à chanter reçoit la première voix, etc.
# Un groupe est retenu s'il ressemble à une voix à part : silhouette suffisante (comme l'axe des empreintes du duo), et
# chaque chanteur chante assez (part et durée minimales). Sinon N - 1 groupes sont essayés, jusqu'à 3 ; en dessous, le
# duo ou la voix seule d'avant (decider_chanteurs, avec les deux premières voix). Les voix qu'aucun chanteur n'a reçues ne
# servent qu'aux autres versions des passages (versions_duo) : leur décalage est celui de la classe du chanteur.
# Reconnaissance des pistes (chœurs, voix principale, secours) : la ressemblance de l'empreinte de la piste à chacun des N
# centres, plus la hauteur de la piste, donne la vraisemblance de chaque chanteur (reconnaitre). Limite : plus il y a de
# chanteurs, plus leurs empreintes se rapprochent ; à cinq ou six, des voix de même genre et de même timbre se confondent.

def viterbi_n(actifs, centres, pause, R):
    """Chanteur (0 à N - 1) de chaque morceau actif. centres : matrice N x 192 des empreintes moyennes (normées) ;
    la vraisemblance d'un chanteur est sa ressemblance (cosinus) avec l'empreinte du morceau, pondérée par le temps voisé
    (comme viterbi). pause[j] : la frontière entre actifs[j - 1] et actifs[j] est un silence."""
    import numpy as np
    nv = len(centres)

    def ll(s):
        e = s.get('emb')
        if e is None or s.get('voise_s', 0) < R['voise_hauteur_s']:
            return np.zeros(nv)
        return min(s['voise_s'] / 3.0, 1.0) * (centres @ np.asarray(e, np.float64)) / R['n_temperature']
    score, retour = ll(actifs[0]), []
    rang = np.arange(nv)
    for j in range(1, len(actifs)):
        c = R['cout_changement_pause'] if pause[j] else R['cout_changement']
        changer = score.max() - c
        retour.append(np.where(score >= changer, rang, int(np.argmax(score))))
        score = np.maximum(score, changer) + ll(actifs[j])
    k = int(np.argmax(score))
    lab = [k]
    for arr in reversed(retour):
        k = int(arr[k])
        lab.append(k)
    return lab[::-1]


def lisser_sections_n(actifs, lab, R):
    """Une section plus courte que n_section_min_s rejoint la plus longue de ses voisines (la plus courte d'abord)."""
    lab = list(lab)
    while True:
        sec = sections(actifs, lab)
        courtes = [i for i, t in enumerate(sec) if t[3] < R['n_section_min_s']]
        if len(sec) <= 1 or not courtes:
            return lab
        i = min(courtes, key=lambda x: sec[x][3])
        d, f, _, _ = sec[i]
        voisines = [sec[x] for x in (i - 1, i + 1) if 0 <= x < len(sec)]
        k = max(voisines, key=lambda t: t[3])[2]
        for j in range(d, f):
            lab[j] = k


def essai_n_chanteurs(morceaux, actifs_i, coupes_son, R, k):
    """Essai de k chanteurs sur les empreintes. Rend (résultat, pourquoi, essai) ; résultat = {'lab' (rang de chaque
    morceau actif, dans l'ordre d'apparition), 'centres' (rangés de même), 'actifs'} ou None."""
    import numpy as np
    elig = [i for i in actifs_i if morceaux[i].get('emb') is not None and morceaux[i]['voise_s'] >= R['voise_min_s']]
    essai = {'k': k, 'morceaux': len(elig)}
    if len(elig) < max(R['duo_morceaux_min'], 2 * k):
        return None, f'{len(elig)} morceaux avec empreinte : trop peu pour {k} chanteurs', essai
    e = np.stack([np.asarray(morceaux[i]['emb'], np.float64) for i in elig])
    lab, c = kmeans_cos(e, k)
    if len(set(lab.tolist())) < k:
        return None, f'moins de {k} groupes distincts', essai
    sil = silhouette_cos(e, lab)
    essai['silhouette'] = round(sil, 3)
    if sil < R['axe_silhouette_min']:
        return None, f"groupes trop peu séparés (silhouette {sil:.2f} < {R['axe_silhouette_min']})", essai
    pause = [False]
    for a, b in zip(actifs_i, actifs_i[1:]):
        en_son = coupes_son is None or any(abs(morceaux[b]['debut_s'] - x) < 0.006 for x in coupes_son)
        pause.append(b - a > 1 or not en_son)
    act = [morceaux[i] for i in actifs_i]
    lab_t = lisser_sections_n(act, viterbi_n(act, c, pause, R), R)
    voise = [sum(s['voise_s'] for s, g in zip(act, lab_t) if g == j) for j in range(k)]
    essai['voise_s'] = [round(v, 1) for v in voise]
    if min(voise) < max(R['n_part_min'] * sum(voise), R['n_voise_min_s']):
        return None, f"après attribution, un chanteur chante trop peu ({min(voise):.0f} s voisées sur {sum(voise):.0f} s)", essai
    ordre = []
    for g in lab_t:
        if g not in ordre:
            ordre.append(g)
    rang = {g: r for r, g in enumerate(ordre)}
    return {'lab': [rang[g] for g in lab_t], 'centres': np.stack([c[g] for g in ordre]), 'actifs': act}, None, essai


def decider_chanteurs_n(morceaux, cibles, reglages=None, coupes_son=None):
    """decider_chanteurs pour trois voix cibles ou plus (voir plus haut). Rend (resultats, info, modele) comme lui, ou
    None si moins de trois chanteurs sont trouvés (alors decider_chanteurs refait le duo ou la voix seule)."""
    R = {**OCTAVE_DEFAUT, **CHANTEURS_DEFAUT, **(reglages or {})}
    V = cibles['voix']
    nv = len(V)
    actifs_i = [i for i, s in enumerate(morceaux) if not s.get('muet') and s.get('voise_s', 0) > 0]
    if not actifs_i:
        return None
    essais, trouve = [], None
    for k in range(nv, 2, -1):
        r, pourquoi, essai = essai_n_chanteurs(morceaux, actifs_i, coupes_son, R, k)
        essai['pourquoi'] = pourquoi
        essais.append(essai)
        if r:
            trouve = r
            break
    if trouve is None:
        return None
    lab, centres, act = trouve['lab'], trouve['centres'], trouve['actifs']
    k = len(centres)

    def decalage(classe, j):
        return meilleure_octave(R[f'centre_{classe}_hz'], V[j]['centre_hz'])
    mg = mesurer_voix([morceaux[i] for i in actifs_i], R)
    classe_g, raison_g = classer(mg, R)
    co_g = classe_g or (classe_hauteur(mg['f0_med_hz'], R) if mg.get('f0_med_hz') else None)
    info = {'regle': f"v2.4, une voix cible par chanteur, jusqu'à {nv}", 'mode': 'ordre', 'demandees': nv,
            'voix_cibles': [{'cle': v.get('cle'), 'nom': v['nom'], 'centre_chant_hz': round(v['centre_hz'], 1)} for v in V],
            'decalage_par_voix_et_classe': {v['nom']: {c: decalage(c, j) for c in ('homme', 'femme')} for j, v in enumerate(V)},
            'zone_ambigue_hz': [R['zone_basse_hz'], R['zone_haute_hz']], 'frontiere_hauteur_seule_hz': R['frontiere_hz'],
            'voix_principale': {**mg, 'classe': classe_g, 'raison': raison_g}, 'essai_duo': {'n_chanteurs': essais}}
    # classe de chaque chanteur (hauteur, empreinte de genre si des ancres, notes graves), décalage de son couple
    mes, classes, noms, decs = [], [], [], []
    for g in range(k):
        m = mesurer_voix([s for s, x in zip(act, lab) if x == g], R)
        c, r = classer(m, R, sur_seulement=True)
        if c is None:
            ch = classe_hauteur(m['f0_med_hz'], R) if m.get('f0_med_hz') else None
            c, r = (ch, r + ", d'après la hauteur seule") if ch else (co_g, r + ' : comme la voix principale')
        m.update({'classe': c, 'raison': r,
                  'voise_attribue_s': round(sum(s['voise_s'] for s, x in zip(act, lab) if x == g), 1)})
        mes.append(m)
        classes.append(c)
        noms.append(f'{c or "voix"} {g + 1}')
        decs.append(decalage(c, g) if c else 0)
    resultats = [None] * len(morceaux)
    for j, i in enumerate(actifs_i):
        g = lab[j]
        resultats[i] = {'decalage': decs[g], 'voix': noms[g], 'k': g, 'cible': g, 'chanteur': V[g].get('cle'),
                        'base': f"{k} voix, chanteur {g + 1} ({noms[g]}) → {V[g]['nom']}"}
    dernier = None
    for i in range(len(morceaux)):
        if resultats[i] is None:
            dernier = dernier or resultats[actifs_i[0]]
            resultats[i] = {'decalage': dernier['decalage'], 'voix': None, 'base': muet_ou_sourd(morceaux[i]),
                            'k': dernier['k'], 'cible': dernier['cible'], 'chanteur': dernier['chanteur']}
        else:
            dernier = resultats[i]
    chanteurs = [{**mes[g], 'nom': noms[g], 'rang': g + 1, 'cible': g, 'voix': V[g]['nom'], 'cle': V[g].get('cle'),
                  'centre_chanteur_hz': R[f'centre_{classes[g]}_hz'] if classes[g] else None,
                  'centre_cible_hz': round(V[g]['centre_hz'], 1), 'decalage': decs[g]} for g in range(k)]
    secs = sections(act, lab)
    info.update({'voix': k, 'axe': 'empreintes', 'chanteurs': chanteurs,
                 'message': f'{k} voix : ' + ', '.join(f"{c['nom']} → {c['voix']} ({c['decalage']:+d})" for c in chanteurs)
                            + (f' ({nv - k} voix cible(s) sans chanteur)' if k < nv else ''),
                 'changements': [{'a_s': act[d]['debut_s'], 'vers': noms[x], 'voix': V[x]['nom']} for d, _, x, _ in secs[1:]]})
    hauteur = []
    for g in range(k):
        f = [(math.log2(s['f0_med']), s['voise_s']) for s, x in zip(act, lab)
             if x == g and s.get('f0_med') and s['voise_s'] >= R['voise_hauteur_s']]
        if f:
            m = mediane_ponderee([a for a, _ in f], [b for _, b in f])
            sd = math.sqrt(sum(b * (a - m) ** 2 for a, b in f) / sum(b for _, b in f))
            hauteur.append((m, max(sd, R['sigma_hauteur_min_oct'])))
        else:
            hauteur.append(None)
    modele = {'axe': 'empreintes', 'vecteur': centres, 'centres': centres, 'hauteur': hauteur, 'cible': list(range(k)),
              'decalage': decs, 'noms': noms}
    info['modele_pistes'] = {'axe': 'empreintes', 'chanteurs': k,
                             'hauteur_hz': [None if h is None else round(2 ** h[0], 1) for h in hauteur],
                             'hauteur_sigma_oct': [None if h is None else round(h[1], 3) for h in hauteur]}
    return resultats, info, modele


# --- Parole à plusieurs voix : qui parle et quand (diarisation par empreintes) ----------------------------------------------
# --voix personne1:a,personne2:b,… avec une vidéo en parole (film, interview, sketch) : la voix isolée complète est
# découpée en régions de parole (enveloppe, comme les morceaux), chaque région en fenêtres glissantes de 1,5 s (pas de
# 0,75 s) dont l'empreinte CAMPPlus (déjà dans le noyau, pas de jeton ni de modèle de plus) est regroupée en N personnes
# (k-moyennes sphériques ; N, N - 1… 2 essayés, retenus si les groupes sont assez séparés (silhouette) et assez présents),
# étiquette lissée dans le temps (tour de parole d'au moins 1,5 s), changement de personne placé au creux d'énergie le
# plus proche. Les changements tombant en pleine parole ajoutent une coupe : chaque morceau n'a qu'une personne.
# La première personne entendue reçoit la première voix, etc. (comme les chanteurs). Limites (RAPPORT.md de
# recherche/unisson : ce qui se sépare ou non) : deux personnes qui parlent en même temps restent un seul flux, converti
# dans la voix de la personne dominante ; un tour de moins de 1,5 s (un « oui ») prend la voix de ses voisins ; deux voix
# de timbre très proche (frère et sœur, deux hommes de même âge) se confondent plus qu'avec un modèle entraîné à la
# tâche (pyannote : 17 à 20 % d'erreur sur la réunion AMI, sous conditions et jeton HF ; les empreintes seules font
# davantage d'erreurs sur la parole entremêlée, mais restent exactes sur des tours longs et des voix différentes).

DIARISATION_DEFAUT = {
    'fenetre_s': 1.5,           # fenêtre d'empreinte
    'pas_s': 0.75,              # pas des fenêtres
    'trou_s': 0.3,              # un silence plus long sépare deux régions de parole
    'region_min_s': 0.5,        # région plus courte : sans empreinte propre (étiquette de sa voisine)
    'tour_min_s': 1.5,          # un tour de parole plus court rejoint ses voisins
    'silhouette_min': 0.15,     # groupes d'empreintes assez séparés (la parole est plus bruitée que le chant : 0,15)
    'part_min': 0.03,           # part minimale de chaque personne (fenêtres)
    'parole_min_s': 3.0,        # parole minimale de chaque personne
    'creux_s': 0.4,             # le changement est déplacé au creux d'énergie à ± ce temps
}


def lisser_tours(lab, pas_s, tour_min_s):
    """Étiquettes de fenêtres lissées : un tour plus court que tour_min_s rejoint le plus long de ses voisins (le plus
    court d'abord)."""
    lab = list(lab)
    n_min = max(1, int(round(tour_min_s / pas_s)))
    while True:
        tours, d = [], 0
        for j in range(1, len(lab) + 1):
            if j == len(lab) or lab[j] != lab[d]:
                tours.append((d, j, lab[d]))
                d = j
        courts = [i for i, t in enumerate(tours) if t[1] - t[0] < n_min]
        if len(tours) <= 1 or not courts:
            return lab
        i = min(courts, key=lambda x: tours[x][1] - tours[x][0])
        voisins = [tours[x] for x in (i - 1, i + 1) if 0 <= x < len(tours)]
        k = max(voisins, key=lambda t: t[1] - t[0])[2]
        for j in range(tours[i][0], tours[i][1]):
            lab[j] = k


def diariser(x, lisse, seuil, nv, empreinte_fn, reglages=None):
    """Qui parle quand (voix isolée complète x, SR ; lisse : enveloppe en dB par trame de 10 ms ; seuil de silence ;
    nv : nombre de personnes demandées ; empreinte_fn(y 44,1 kHz) -> empreinte normée ou None). Rend {'k' (personnes
    trouvées), 'trames' (rang de la personne à chaque trame de 10 ms, -1 sans parole ; première entendue : 0),
    'coupes' (échantillons des changements de personne en pleine parole), 'essais', 'parts' (secondes par personne)}."""
    import numpy as np
    D = {**DIARISATION_DEFAUT, **(reglages or {})}
    n_tr = len(lisse)
    trames = np.full(n_tr, -1, np.int64)
    parole = np.asarray(lisse) > seuil
    # comble les silences courts : une région de parole
    regions = []
    for a, b in suites(parole):
        if regions and (a - regions[-1][1]) * 0.01 <= D['trou_s']:
            regions[-1] = (regions[-1][0], b)
        else:
            regions.append((a, b))
    fen, pas = int(D['fenetre_s'] * SR), int(D['pas_s'] * SR)
    fenetres = []   # (région, début, fin en échantillons, empreinte)
    for r, (a, b) in enumerate(regions):
        d0, d1 = a * PAS, min(b * PAS, len(x))
        if (d1 - d0) / SR < D['region_min_s']:
            continue
        debuts = list(range(d0, max(d0, d1 - fen) + 1, pas)) or [d0]
        for d in debuts:
            f = min(d + fen, d1)
            e = empreinte_fn(x[d:f]) if f - d >= SR // 2 else None
            if e is not None:
                fenetres.append((r, d, f, np.asarray(e, np.float64)))
    sortie = {'k': 1, 'trames': trames, 'coupes': [], 'essais': [], 'parts': {}, 'fenetres': len(fenetres)}
    if len(fenetres) < 4:
        for a, b in regions:
            trames[a:b] = 0
        sortie['parts'] = {0: round(float(parole.sum()) * 0.01, 1)}
        return sortie
    E = np.stack([f[3] for f in fenetres])
    choix = None
    for k in range(min(nv, len(fenetres) // 2), 1, -1):
        lab, c = kmeans_cos(E, k)
        essai = {'k': k}
        if len(set(lab.tolist())) < k:
            essai['pourquoi'] = f'moins de {k} groupes distincts'
        else:
            sil = silhouette_cos(E, lab)
            essai['silhouette'] = round(sil, 3)
            parts = [(lab == g).sum() / len(lab) for g in range(k)]
            parole_s = [(lab == g).sum() * D['pas_s'] for g in range(k)]
            if sil < D['silhouette_min']:
                essai['pourquoi'] = f"groupes trop peu séparés (silhouette {sil:.2f} < {D['silhouette_min']})"
            elif min(parts) < D['part_min'] or min(parole_s) < D['parole_min_s']:
                essai['pourquoi'] = 'une personne parle trop peu'
            else:
                choix = (k, lab, c)
        sortie['essais'].append(essai)
        if choix:
            break
    if choix is None:
        for a, b in regions:
            trames[a:b] = 0
        sortie['parts'] = {0: round(float(parole.sum()) * 0.01, 1)}
        return sortie
    k, lab, c = choix
    # étiquettes lissées par région (un tour trop court rejoint ses voisins), rangées par ordre d'apparition
    lab = list(lab)
    par_region = {}
    for j, f in enumerate(fenetres):
        par_region.setdefault(f[0], []).append(j)
    for js in par_region.values():
        lissees = lisser_tours([lab[j] for j in js], D['pas_s'], D['tour_min_s'])
        for j, v in zip(js, lissees):
            lab[j] = v
    ordre = []
    for g in lab:
        if g not in ordre:
            ordre.append(g)
    rang = {g: i for i, g in enumerate(ordre)}
    k_reel = len(ordre)
    coupes, extremes = [], {}
    for r, (a, b) in enumerate(regions):
        js = par_region.get(r)
        if not js:
            continue
        centres = [(fenetres[j][1] + fenetres[j][2]) / 2 for j in js]
        marques = [rang[lab[j]] for j in js]
        # frontières : milieu entre deux centres de fenêtres d'étiquettes différentes, au creux d'énergie le plus proche
        bornes = [a * PAS]
        for j in range(1, len(js)):
            if marques[j] != marques[j - 1]:
                t = (centres[j - 1] + centres[j]) / 2
                k0 = int(t // PAS)
                fenetre_k = int(D['creux_s'] / 0.01)
                lo, hi = max(a, k0 - fenetre_k), min(b - 1, k0 + fenetre_k)
                k_c = lo + int(np.argmin(np.asarray(lisse[lo:hi + 1]))) if hi >= lo else k0
                bornes.append(k_c * PAS)
                coupes.append(k_c * PAS)
        bornes.append(b * PAS)
        for i, g in enumerate(marques[:1] + [m for m, p in zip(marques[1:], marques) if m != p]):
            trames[bornes[i] // PAS:bornes[i + 1] // PAS] = g
        extremes[r] = (marques[0], marques[-1])
    # changement de personne d'une région à la suivante (à travers un silence) : coupe au milieu du silence
    pour_regions = sorted(extremes)
    for r0, r1 in zip(pour_regions, pour_regions[1:]):
        if extremes[r0][1] != extremes[r1][0]:
            coupes.append(((regions[r0][1] + regions[r1][0]) // 2) * PAS)
    # les régions trop courtes pour une empreinte prennent la personne de la trame parlée précédente
    dernier = 0
    for t in range(n_tr):
        if trames[t] >= 0:
            dernier = trames[t]
        elif parole[t]:
            trames[t] = dernier
    parts = {g: round(float((trames == g).sum()) * 0.01, 1) for g in range(k_reel)}
    sortie.update({'k': k_reel, 'coupes': sorted(set(int(c_) for c_ in coupes)), 'parts': parts,
                   'silhouette': next((e_['silhouette'] for e_ in sortie['essais'] if e_['k'] == k), None)})
    return sortie


def fusionner_coupes(coupes, tours, n, ecart_min_s=0.5):
    """Coupes du découpage aux silences plus les changements de personne (échantillons) ; un changement à moins de
    ecart_min_s d'une coupe existante est ignoré (le morceau voisin garde sa personne dominante)."""
    sortie = list(coupes)
    for t in sorted(tours):
        if all(abs(t - c) >= ecart_min_s * SR for c in sortie) and 0 < t < n:
            sortie.append(t)
    return sorted(sortie)


def decider_personnes(segs, diar, cibles, reglages=None):
    """Parole à plusieurs voix : la personne de chaque morceau (celle qui occupe le plus de trames du morceau), la voix
    cible de cette personne (la première entendue : première voix…), le décalage de hauteur (pour la variante à f0 ;
    Seed-VC 22 kHz suit la voix cible de lui-même). Rend l'info du rapport (decision_hauteur), format de celle du chant."""
    import numpy as np
    V = cibles['voix']
    k = min(diar['k'], len(V))
    trames = diar['trames']
    for s in segs:
        if s['muet']:
            s['decalage'], s['base'] = 0.0, 'muet'
            continue
        t = trames[s['debut'] // PAS:max(s['fin'] // PAS, s['debut'] // PAS + 1)]
        t = t[t >= 0]
        g = int(np.bincount(t).argmax()) if len(t) else None
        s['personne'] = g
    dernier = 0
    for s in segs:   # morceau sans parole repérée : la personne précédente
        if s['muet']:
            continue
        if s['personne'] is None:
            s['personne'] = dernier
        dernier = s['personne'] = min(s['personne'], k - 1)
    for s in segs:
        if s['muet']:
            continue
        g = s['personne']
        j = g   # première personne entendue : première voix cible
        f0_c = float(V[j].get('f0_parole') or 0)
        if f0_c > 0 and s.get('f0_med'):
            s['decalage'] = float(np.clip(np.round(24 * np.log2(f0_c / s['f0_med'])) / 2, -24, 24))
        else:
            s['decalage'] = 0.0
        s.update({'cible': j, 'voix': g + 1, 'k': g, 'base': f'personne {g + 1} → {V[j]["nom"]}'})
    parts = diar['parts']
    chanteurs = [{'nom': f'personne {g + 1}', 'rang': g + 1, 'cible': g, 'voix': V[g]['nom'], 'cle': V[g].get('cle'),
                  'parole_s': parts.get(g, 0.0)} for g in range(k)]
    actifs = [s for s in segs if not s['muet']]
    changements = [{'a_s': b['debut_s'], 'vers': f"personne {b['personne'] + 1}", 'voix': V[b['personne']]['nom']}
                   for a, b in zip(actifs, actifs[1:]) if a['personne'] != b['personne']]
    if k >= 2:
        msg = (f'{k} personnes repérées : ' + ', '.join(f"{c['nom']} → {c['voix']}" for c in chanteurs)
               + (f' ({len(V) - k} voix cible(s) sans personne)' if k < len(V) else ''))
    else:
        msg = f"une seule personne repérée : tout prend la voix {V[0]['nom']}"
    return {'regle': 'parole, une voix par personne (empreintes CAMPPlus, fenêtres glissantes)', 'mode': 'personnes',
            'voix': k, 'axe': 'diarisation', 'demandees': len(V), 'chanteurs': chanteurs, 'message': msg,
            'changements': changements,
            'diarisation': {'personnes': diar['k'], 'fenetres': diar.get('fenetres'), 'essais': diar['essais'],
                            'parole_par_personne_s': {str(g + 1): s for g, s in parts.items()},
                            'silhouette': diar.get('silhouette'), 'changements_de_personne_en_pleine_parole': len(diar['coupes'])},
            'voix_seule': None}


def decider_chant_cibles(segs, R, cibles, coupes_son=None):
    """decider_chant avec plusieurs voix cibles : genre_score (ancres) comme decider_chant, puis decider_chanteurs ;
    chaque morceau reçoit decalage, base, voix (chanteur), cible, voix_cible et chanteur (clé). Rend (info, modele), le
    modèle servant à reconnaître les chanteurs sur les pistes (vecteur de l'axe ajouté : ancres ou empreintes)."""
    import numpy as np
    ancres = R.get('ancres')
    if ancres:
        h, f = np.asarray(ancres['homme']), np.asarray(ancres['femme'])
        for s in segs:
            if not s['muet'] and s.get('emb') is not None:
                s['genre_score'] = round(float(s['emb'] @ h - s['emb'] @ f), 3)
    res, info, modele = decider_chanteurs(segs, cibles, {k: R[k] for k in {**OCTAVE_DEFAUT, **CHANTEURS_DEFAUT} if k in R},
                                          coupes_son)
    if not ancres:
        info['empreinte'] = 'sans ancres de genre : pas de duo sur l’axe du genre (ni, en mode genre, de duo du tout)'
    if modele is not None and modele['axe'] == 'genre_score':
        modele['vecteur'] = np.asarray(ancres['homme'], np.float64) - np.asarray(ancres['femme'], np.float64)
    for s, r in zip(segs, res):
        s['decalage'], s['base'], s['cible'] = r['decalage'], r['base'], r['cible']
        s['voix_cible'], s['chanteur'] = cibles['voix'][r['cible']]['nom'], r['chanteur']
        s['k'] = r['k']
        if r['voix'] is not None:
            s['voix'] = r['voix']
        else:
            s.pop('voix', None)
    vp = info['voix_principale']
    info.update({'f0_mediane_globale_hz': vp.get('f0_med_hz'), 'voix_seule': vp.get('classe') if info['voix'] == 1 else None})
    return info, modele


def vraisemblances(m, modele, R):
    """Log-vraisemblance de chaque chanteur (0, 1) pour une piste mesurée sur un morceau : score sur l'axe du duo
    (empreinte de la piste) et hauteur médiane, chacun pondéré par le temps voisé (comme viterbi). Rend ([l0, l1], indices
    utilisés)."""
    if modele.get('centres') is not None:   # N chanteurs (decider_chanteurs_n) : empreinte = cosinus avec chaque centre
        nk = len(modele['centres'])
        l, indices = [0.0] * nk, []
        if m.get('voise_s', 0) < R['voise_hauteur_s']:
            return l, indices
        w = min(m.get('voise_s', 0) / 3.0, 1.0)
        if m.get('score') is not None:
            indices.append('empreinte')
            for k in range(nk):
                l[k] += w * m['score'][k] / R['n_temperature']
        if m.get('f0_med') and any(h is not None for h in modele['hauteur']):
            indices.append('hauteur')
            lf = math.log2(m['f0_med'])
            for k, h in enumerate(modele['hauteur']):
                if h is not None:
                    l[k] += -w * (lf - h[0]) ** 2 / (2 * h[1] ** 2) - w * math.log(h[1])
        return l, indices
    l, indices = [0.0, 0.0], []
    w = min(m.get('voise_s', 0) / 3.0, 1.0)
    if m.get('voise_s', 0) < R['voise_hauteur_s']:
        return l, indices
    if m.get('score') is not None:
        indices.append('empreinte')
        for k in (0, 1):
            mu, sg = modele['mu'][k], modele['sigma'][k]
            l[k] += -w * (m['score'] - mu) ** 2 / (2 * sg ** 2) - w * math.log(sg)
    if m.get('f0_med') and all(h is not None for h in modele['hauteur']):
        indices.append('hauteur')
        lf = math.log2(m['f0_med'])
        for k in (0, 1):
            mu, sg = modele['hauteur'][k]
            l[k] += -w * (lf - mu) ** 2 / (2 * sg ** 2) - w * math.log(sg)
    return l, indices


def reconnaitre(m, modele, k_morceau, R):
    """(chanteur 0 / 1, état, raison, marge) d'une piste sur un morceau ; k_morceau : chanteur du morceau (voix
    complète) ; marge : log-vraisemblance de l'autre chanteur moins celle du chanteur du morceau. États : autre (l'autre
    chanteur reconnu), meme (le chanteur du morceau reconnu), incertaine, faible (piste trop faible devant la voix
    complète : un reste du karaoké, pas une voix à part), sans_indice ; les trois derniers gardent le chanteur du morceau."""
    if k_morceau is None:
        k_morceau = 0
    if m is None:
        return k_morceau, 'sans_indice', 'sans mesure : chanteur du morceau', None
    if m.get('niveau_db') is not None and m['niveau_db'] < R['piste_min_db']:
        return k_morceau, 'faible', (f"piste faible ({m['niveau_db']:.0f} dB devant la voix complète, seuil "
                                     f"{R['piste_min_db']:.0f}) : chanteur du morceau"), None
    l, indices = vraisemblances(m, modele, R)
    if not indices:
        return k_morceau, 'sans_indice', 'trop peu voisée pour être reconnue : chanteur du morceau', None
    autre = max((x for x in range(len(l)) if x != k_morceau), key=lambda x: l[x])   # deux chanteurs : l'autre
    marge = l[autre] - l[k_morceau]
    par = ' et '.join(indices)
    if marge >= R['marge_reconnaissance']:
        return autre, 'autre', (f"reconnue ({par}) : {modele['noms'][autre]}, l'autre chanteur que le morceau "
                                f"(marge {marge:.1f})"), round(marge, 2)
    if marge <= -R['marge_reconnaissance']:
        return k_morceau, 'meme', f"reconnue ({par}) : {modele['noms'][k_morceau]}, comme le morceau (marge {-marge:.1f})", round(marge, 2)
    return k_morceau, 'incertaine', (f"incertaine ({par}, marge {abs(marge):.1f} < {R['marge_reconnaissance']}) : "
                                     'chanteur du morceau'), round(marge, 2)


def mesurer_piste(xp, f0p, x, morceaux, empreinte_fn=None, vecteur=None, porte_g=None):
    """Hauteur, temps voisé, niveau (dB devant la voix complète x) et score d'empreinte d'une piste (xp, F0 f0p par
    trame de 10 ms) sur chaque morceau [(i, début, fin)] (échantillons). porte_g (piste de secours) : seuls comptent les
    échantillons où sa porte est ouverte. empreinte_fn(y) -> empreinte normée ; vecteur : axe du duo. Rend {i: mesure}."""
    import numpy as np
    xp, x, f0p = np.asarray(xp), np.asarray(x), np.asarray(f0p)
    out = {}
    for i, a, b in morceaux:
        sel = slice(a, b)
        yp, yx = xp[sel], x[sel]
        f = f0p[a // PAS:b // PAS]
        if porte_g is not None:
            ouvert = np.asarray(porte_g[sel]) > 0.5
            yp, yx = yp[ouvert], yx[ouvert]
            g_t = np.asarray(porte_g[a:b:PAS]) > 0.5
            f = f[:len(g_t)][g_t[:len(f)]]
        v = f[f > 1]
        m = {'voise_s': round(len(v) * 0.01, 2), 'f0_med': round(float(np.median(v)), 1) if len(v) else None}
        ep, ex = float(np.sqrt(np.mean(np.asarray(yp, np.float64) ** 2))) if len(yp) else 0.0, \
            float(np.sqrt(np.mean(np.asarray(yx, np.float64) ** 2))) if len(yx) else 0.0
        m['niveau_db'] = round(20 * math.log10(ep / ex), 1) if ep > 1e-9 and ex > 1e-9 else (-120.0 if ex > 1e-9 else None)
        if empreinte_fn is not None and vecteur is not None and len(yp) >= SR // 2 and m['voise_s'] > 0:
            sc = np.asarray(empreinte_fn(np.ascontiguousarray(yp, np.float32)), np.float64) @ (
                vecteur if np.ndim(vecteur) == 1 else np.asarray(vecteur).T)
            m['score'] = round(float(sc), 3) if np.ndim(vecteur) == 1 else [round(float(x), 3) for x in sc]
        out[i] = m
    return out


# --- Transposition de la chanson (chant seulement) : la musique seule est transposée de musique_dt demi-tons ENTIERS, un
# seul pour toute la chanson, et chaque voix reçoit musique_dt + son octave (la règle d'octave ci-dessus, inchangée). Les
# proches de la voix cible ne la reconnaissent que près de sa zone de chant naturelle (banc : reconnue à 174 et 219 Hz,
# pas de 246 à 295 Hz, pour une zone vers 190 Hz). Règle de banc/realisme/transposition.py (regle_decalage), copiée ici :
# le noyau est un script seul. La musique est transposée sur le PC (Rubber Band R3) ; le noyau décide et rapporte.
# Parole : jamais de transposition.

TRANSPOSITION_DEFAUT = {
    'max_dt': 5,                    # au-delà de ±5 demi-tons, la musique transposée s'entend
    'confort_dt': 4.0,              # zone de confort sans tessiture mesurée : centre de chant x 2^(±4/12)
    'poids_hors_confort': 3.0,      # par demi-ton de p10 ou de p90 hors de la zone
    'poids_centre': 0.5,            # par demi-ton d'écart de la médiane au centre (géométrique) de la zone
    'poids_transposition': 0.15,    # par demi-ton de transposition de la musique
}


def zone_de_voix(v, centre):
    """Zone de confort [bas, haut] (Hz) d'une voix cible : celle que doubler.py envoie (v['zone'], de tessiture.json), sinon
    centre de chant x 2^(±confort_dt/12). Rend {'confort': [bas, haut], 'origine': 'tessiture' ou 'parole'}."""
    z = (v or {}).get('zone')
    if isinstance(z, dict) and isinstance(z.get('confort'), (list, tuple)) and len(z['confort']) == 2:
        try:
            bas, haut = sorted(float(x) for x in z['confort'])
        except (TypeError, ValueError):
            bas = haut = 0.0
        if bas > 0 and haut > bas:
            return {'confort': [bas, haut], 'origine': z.get('origine') or 'tessiture'}
    c = TRANSPOSITION_DEFAUT['confort_dt']
    return {'confort': [centre * 2 ** (-c / 12), centre * 2 ** (c / 12)], 'origine': 'parole'}


def _dt_hz(f, ref):
    return 12.0 * math.log2(f / ref)


def cout_transposition_voix(src, confort, d, poids):
    """Coût d'un décalage d de la voix (demi-tons) : débordement de ses p10 et p90 hors de la zone de confort, plus l'écart de sa
    médiane au centre géométrique de la zone. src : {'p10', 'p50', 'p90'} en Hz, voix convertie avant transposition."""
    bas, haut = confort
    centre = math.sqrt(bas * haut)
    deborde = 0.0
    for cle in ('p10', 'p90'):
        if src.get(cle):
            f = src[cle] * 2 ** (d / 12)
            deborde += max(0.0, _dt_hz(bas, f)) + max(0.0, _dt_hz(f, haut))
    med = src['p50'] * 2 ** (d / 12)
    return poids['poids_hors_confort'] * deborde + poids['poids_centre'] * abs(_dt_hz(med, centre))


def choisir_transposition(voix, reglages=None):
    """Le nombre entier de demi-tons dont transposer la chanson (un seul pour toutes les voix), dans [-max_dt, +max_dt] :
    celui qui minimise, sur toutes les voix, le coût de cout_transposition_voix plus poids_transposition x |dt| (à coût égal, le
    plus petit |dt|). voix : [{'nom', 'p10', 'p50', 'p90', 'confort': [bas, haut]}] (hauteurs de la voix convertie, octave
    comprise, avant transposition). Un duo (deux voix) : un seul dt qui minimise l'écart total des deux voix à leur zone.
    Rend {'musique_dt', 'cout', 'voix': [par voix : avant/après (Hz), dans_zone, ecart_zone_dt]}."""
    P = {**TRANSPOSITION_DEFAUT, **{k: v for k, v in (reglages or {}).items() if k in TRANSPOSITION_DEFAUT}}
    m = int(P['max_dt'])
    essais = []
    for dt in range(-m, m + 1):
        c = sum(cout_transposition_voix(v, v['confort'], dt, P) for v in voix) + P['poids_transposition'] * abs(dt)
        essais.append((round(c, 9), abs(dt), dt))
    essais.sort()
    dt = essais[0][2] if voix else 0
    return {'musique_dt': dt, 'cout': essais[0][0] if voix else 0.0, 'voix': detail_transposition(voix, dt)}


def detail_transposition(voix, dt):
    """Par voix : hauteurs p10/p50/p90 (Hz) avant et après la transposition de dt demi-tons, zone, p10 et p90 dedans, et
    débordement cumulé hors de la zone (demi-tons)."""
    out = []
    for v in voix:
        bas, haut = v['confort']
        apres = {c: round(v[c] * 2 ** (dt / 12), 1) for c in ('p10', 'p50', 'p90') if v.get(c)}
        dehors = sum(max(0.0, _dt_hz(bas, apres[c])) + max(0.0, _dt_hz(apres[c], haut)) for c in ('p10', 'p90') if c in apres)
        out.append({'voix': v.get('nom'), 'zone_hz': [round(bas, 1), round(haut, 1)],
                    'avant_hz': {c: round(v[c], 1) for c in ('p10', 'p50', 'p90') if v.get(c)}, 'apres_hz': apres,
                    'dans_zone': dehors < 1e-9, 'ecart_zone_dt': round(dehors, 2)})
    return out


def hauteurs_des_voix(segs, f0_tout, nb_voix):
    """Hauteurs (p10, p50, p90, en Hz, F0 RMVPE voisée) de la voix convertie de chaque voix cible, avant transposition : les
    trames voisées de ses morceaux (celui de la cible s['cible'], 0 sans voix par chanteur), chacune décalée du décalage
    du morceau (octave comprise). Liste de nb_voix éléments, None pour une voix qui ne chante pas."""
    import numpy as np
    parts = [[] for _ in range(nb_voix)]
    for s in segs:
        if s.get('muet'):
            continue
        j = s.get('cible') or 0
        if j >= nb_voix:
            continue
        coeur = f0_tout[s['debut'] // PAS:s['fin'] // PAS]
        v = coeur[coeur > 1]
        if len(v):
            parts[j].append(v * 2 ** (s['decalage'] / 12))
    out = []
    for p in parts:
        v = np.concatenate(p) if p else np.zeros(0)
        out.append(None if len(v) < 20 else {c: float(np.percentile(v, q)) for c, q in (('p10', 10), ('p50', 50), ('p90', 90))})
    return out


def lire_transposer(valeur):
    """Valeur de l'option transposer du travail : 'auto' (défaut), 'non', ou un entier de demi-tons (au plus ±12)."""
    if valeur in (None, '', 'auto'):
        return 'auto'
    if valeur in ('non', False, 'off', 'aucune'):
        return 'non'
    try:
        n = int(valeur)
    except (TypeError, ValueError):
        return 'auto'
    return max(-12, min(12, n))


def decider_transposition(segs, f0_tout, voix, demande='auto', mode='chant', reglages=None):
    """Décision de transposition de la chanson. voix : [{'nom', 'centre_hz', 'zone' (facultative)}], une par voix cible (une
    seule : la voix de l'analyse) ; segs : morceaux décidés (decalage = octave, après un éventuel --octave) ; demande : 'auto',
    'non' ou un entier. Rend le dict du rapport (rapport['transposition']), dont musique_dt, sans rien modifier."""
    demande = lire_transposer(demande)
    zones = [zone_de_voix(v, v['centre_hz']) for v in voix]
    rap = {'demande': demande, 'mode': mode, 'musique_dt': 0, 'applique': False,
           'zone_origine': [z['origine'] for z in zones],
           'max_dt': int((reglages or {}).get('max_dt', TRANSPOSITION_DEFAUT['max_dt']))}
    hauts = hauteurs_des_voix(segs, f0_tout, len(voix)) if mode == 'chant' else [None] * len(voix)
    mesures = [{'nom': v['nom'], 'confort': z['confort'], **h} for v, z, h in zip(voix, zones, hauts) if h]
    rap['voix'] = detail_transposition(mesures, 0)
    if mode != 'chant':
        rap['raison'] = 'parole : pas de transposition (la voix suit la hauteur de la voix cible, la musique reste)'
        return rap
    if demande == 'non':
        rap['raison'] = 'transposition désactivée (--transposer non) : octave seule'
        return rap
    if not mesures:
        rap['raison'] = 'aucune voix chantée mesurée : pas de transposition'
        return rap
    dt = choisir_transposition(mesures, reglages)['musique_dt'] if demande == 'auto' else int(demande)
    rap['musique_dt'] = dt
    rap['applique'] = dt != 0
    rap['voix'] = detail_transposition(mesures, dt)
    meds = ', '.join(f"{d['voix']} {d['avant_hz']['p50']:.0f} Hz → {d['apres_hz']['p50']:.0f} Hz "
                     f"(zone {d['zone_hz'][0]:.0f}–{d['zone_hz'][1]:.0f} Hz)" for d in rap['voix'])
    if demande != 'auto':
        rap['raison'] = f'imposée (--transposer {dt:+d}) : médiane de la voix convertie {meds}'
    elif dt == 0:
        rap['raison'] = f'la voix convertie est déjà au mieux dans la zone de la voix cible : {meds}'
    else:
        rap['raison'] = f'chanson transposée de {dt:+d} demi-ton(s) pour que la voix convertie tombe dans la zone : {meds}'
    return rap


def appliquer_transposition(segs, info, modele, dt):
    """Ajoute dt (demi-tons) au décalage de chaque morceau chanté et à tout ce que la décision d'octave a rapporté ou que les
    étapes suivantes relisent (modele de reconnaissance des pistes, décalages par classe, par voix, par chanteur). Octave et
    transposition restent séparées dans le rapport (rapport['transposition'])."""
    if not dt:
        return
    for s in segs:
        if not s.get('muet'):
            s['decalage'] += dt
    if modele is not None:
        modele['decalage'] = [d + dt for d in modele['decalage']]
    for cle in ('decalage_par_classe', 'decalage_par_genre'):
        if isinstance(info.get(cle), dict):
            info[cle] = {k: (v + dt if isinstance(v, (int, float)) else v) for k, v in info[cle].items()}
    dpvc = info.get('decalage_par_voix_et_classe')
    if isinstance(dpvc, dict):
        info['decalage_par_voix_et_classe'] = {n: {c: d + dt for c, d in pc.items()} for n, pc in dpvc.items()}
    vp = info.get('voix_principale')
    if isinstance(vp, dict) and isinstance(vp.get('decalage'), (int, float)):
        vp['decalage'] += dt
    for c in info.get('chanteurs') or []:
        if isinstance(c, dict) and isinstance(c.get('decalage'), (int, float)):
            c['decalage'] += dt
    info['transposition_dt'] = dt


def decalages_par_voix_cible(segs, nb_voix):
    """Par voix cible : le décalage (octave + dt) de son morceau le plus long en voisé, pour le rapport ; None sans morceau."""
    out = [None] * nb_voix
    poids = [0.0] * nb_voix
    for s in segs:
        if s.get('muet') or not s.get('f0_med'):
            continue
        j = s.get('cible') or 0
        if j < nb_voix and s['voise_s'] > poids[j]:
            out[j], poids[j] = s['decalage'], s['voise_s']
    return out


def affecter_pistes(segs, noms_pistes, mesures, modele, cibles, octave_imposee=None, reglages=None, transposition_dt=0):
    """Voix cible et décalage de chaque piste sur chaque morceau (octave imposée : elle, plus transposition_dt, le décalage de
    la chanson transposée). segs : morceaux décidés (k, cible, decalage ; dt déjà compris) ; mesures :
    {piste: {i: mesure}} (mesurer_piste ; la piste « voix », voix complète, n'en a pas besoin) ; modele : decider_chanteurs
    (None : une seule voix trouvée, tout suit le morceau). Rend {piste: {i: {'chanteur', 'cible', 'voix', 'decalage',
    'etat', 'raison', 'marge', 'mesure'}}} ; états : ceux de reconnaitre, plus voix_seule (une seule voix trouvée) et
    voix_complete (piste « voix » : la décision du morceau elle-même)."""
    R = {**OCTAVE_DEFAUT, **CHANTEURS_DEFAUT, **(reglages or {})}
    V = cibles['voix']
    res = {}
    for nom in noms_pistes:
        res[nom] = {}
        for s in segs:
            if s.get('muet'):
                continue
            i, k_m = s['i'], s.get('k')
            m = (mesures.get(nom) or {}).get(i)
            if modele is None:
                k, etat, raison, marge = None, 'voix_seule', (
                    'parole : voix de la personne du passage' if s.get('personne') is not None
                    else 'une seule voix trouvée : voix du morceau'), None
                j, d = s['cible'], s['decalage']
            elif nom == 'voix':
                k, etat, raison, marge = k_m, 'voix_complete', 'voix complète : chanteur du morceau', None
                j, d = s['cible'], s['decalage']
            else:
                k, etat, raison, marge = reconnaitre(m, modele, k_m, R)
                j, d = modele['cible'][k], modele['decalage'][k]
            if octave_imposee in (-12, 0, 12):
                d = octave_imposee + transposition_dt
            res[nom][i] = {'chanteur': None if k is None else modele['noms'][k], 'cible': j, 'voix': V[j]['nom'],
                           'decalage': d, 'etat': etat, 'raison': raison, 'marge': marge, 'mesure': m}
    return res


def rapport_affectation(segs, aff, plans, cibles, info):
    """Partie « affectation » du rapport (plusieurs voix cibles) : message, voix utilisées, et, pour chaque piste, chacun
    des morceaux qu'elle convertit (plans : {piste: indices}) : voix, chanteur, décalage, état, raison, mesures ; un
    résumé par piste (morceaux par voix, par état). Chaque morceau de segs reçoit voix_pistes ({piste: voix})."""
    temps = {s['i']: (s['debut_s'], s['fin_s']) for s in segs}
    pistes, resume, utilisees = {}, {}, []
    for nom, par_i in aff.items():
        lignes, r = [], {'morceaux': 0, 'par_voix': {}, 'etats': {}}
        for i in sorted(par_i):
            if i not in plans.get(nom, ()):
                continue
            a, m = par_i[i], par_i[i].get('mesure') or {}
            lignes.append({'i': i, 'debut_s': temps[i][0], 'fin_s': temps[i][1], 'chanteur': a['chanteur'], 'voix': a['voix'],
                           'decalage': a['decalage'], 'etat': a['etat'], 'raison': a['raison'], 'marge': a['marge'],
                           **{k: m[k] for k in ('f0_med', 'voise_s', 'niveau_db', 'score') if m.get(k) is not None}})
            r['morceaux'] += 1
            r['par_voix'][a['voix']] = r['par_voix'].get(a['voix'], 0) + 1
            r['etats'][a['etat']] = r['etats'].get(a['etat'], 0) + 1
            if a['voix'] not in utilisees:
                utilisees.append(a['voix'])
        pistes[nom], resume[nom] = lignes, r
    for s in segs:
        vp = {nom: aff[nom][s['i']]['voix'] for nom in aff if s['i'] in plans.get(nom, ()) and s['i'] in aff[nom]}
        if vp:
            s['voix_pistes'] = vp
    ordre = [v['nom'] for v in cibles['voix']]
    duo_pistes = sum(1 for s in segs if len(set((s.get('voix_pistes') or {}).values())) >= 2)
    return {'mode': cibles['mode'], 'message': info.get('message'), 'voix_utilisees': sorted(utilisees, key=ordre.index),
            'morceaux_en_duo': duo_pistes, 'resume': resume, 'pistes': pistes}


def voix_par_trames(pistes, lisse_voix, k, nv, piste_min_db=CHANTEURS_DEFAUT['piste_min_db']):
    """Voix cible entendue à chaque trame de 10 ms (k trames) : -1 aucune voix convertie, j la seule voix j, nv (nombre de
    voix cibles) plusieurs voix ensemble ; et le décalage de la piste dominante (la première de la liste qui sonne : voix
    principale, chœurs, secours). pistes : [{'morceaux': [(i, a, b, dec, fa, fb, v)], 'gain_trames', 'lisse', 'secours'}].
    Une piste compte là où sa porte est ouverte et, hors secours, à moins de piste_min_db sous la voix complète."""
    import numpy as np
    lisse_voix = a_longueur(lisse_voix, k, -120.0)
    masque = []
    dom_v, dom_d = np.full(k, -1, np.int64), np.zeros(k)
    pris = np.zeros(k, bool)
    for p in pistes:
        v_t, d_t, couvert = np.full(k, -1, np.int64), np.zeros(k), np.zeros(k, bool)
        for m in p['morceaux']:
            a, b = m[1] // PAS, min(-(-m[2] // PAS), k)
            v_t[a:b], d_t[a:b], couvert[a:b] = (m[6] if len(m) > 6 else 0), m[3], True
        ouvert = (a_longueur(p['gain_trames'], k) > 0.5) & couvert
        if not p.get('secours'):
            ouvert &= a_longueur(p['lisse'], k, -120.0) >= lisse_voix + piste_min_db
        masque.append((ouvert, v_t))
        neuf = ouvert & ~pris
        dom_v[neuf], dom_d[neuf] = v_t[neuf], d_t[neuf]
        pris |= ouvert
    present = np.zeros((max(nv, 1), k), bool)
    for ouvert, v_t in masque:
        for j in range(nv):
            present[j] |= ouvert & (v_t == j)
    combien = present.sum(0)
    code = np.where(combien == 0, -1, np.where(combien >= 2, nv, np.argmax(present, axis=0)))
    return code, dom_d


def passages_voix(code, noms, pas_s=0.01, duree_min_s=0.0):
    """Suites de trames de même code (voix_par_trames) : [{'debut_s', 'fin_s', 'voix': [noms]}], sans les silences (-1)."""
    out = []
    d = 0
    for t in range(1, len(code) + 1):
        if t == len(code) or code[t] != code[d]:
            c = int(code[d])
            if c >= 0 and (t - d) * pas_s >= duree_min_s:
                out.append({'debut_s': round(d * pas_s, 2), 'fin_s': round(t * pas_s, 2),
                            'voix': list(noms) if c >= len(noms) else [noms[c]]})
            d = t
    return out


# --- Deux versions de chaque passage (une voix par chanteur, réglage versions_duo) ------------------------------------
# L'attribution d'un passage à un chanteur se trompe parfois (une chanteuse grave prise pour le chanteur, un refrain à
# deux). Pour la corriger à la main sur le PC, sans repasser sur le GPU (doubler --remix --passage N=voix), chaque
# morceau de chaque piste (voix principale, chœurs, secours, ou voix complète) est converti en plus dans l'autre voix
# cible, avec le décalage du couple de cette voix ; la conversion automatique, elle, ne change pas (mêmes morceaux, même
# graine par morceau, même assemblage). Les deux versions reviennent par piste et par voix, en couches de parité
# (poser_morceaux), avec ce qu'il faut au PC pour refaire exactement ce que fait le noyau : porte, mise au niveau de
# chaque piste, voix entendue à chaque trame, mélodie de chaque voix (versions/versions.json).
# versions_duo : « toutes » (défaut de doubler.py en duo), « utiles » (passages incertains, voisins d'un changement de
# voix, chantés ensemble, de hauteur ambiguë ; tous si une seule voix a été trouvée), « non » (rien, comme avant).
VERSIONS_DUO = ('toutes', 'utiles', 'non')


def nouvelles_couches(p, n):
    """Couches de parité d'une piste à convertir qui les demande (p['couches'] : où les écrire ; p['voix'] : nombre de
    voix cibles), sinon None."""
    import numpy as np
    return np.zeros((2 * int(p.get('voix') or 1), n), np.float32) if p.get('couches') else None


def ecrire_couches(p, couches):
    if couches is not None:
        import numpy as np
        np.save(p['couches'], couches)


def charger_couches(info):
    """Couches d'une piste convertie par l'étape YingMusic (résultat de la piste : 'couches'), relues puis effacées ;
    None si elles manquent."""
    import numpy as np
    chemin = (info or {}).get('couches')
    if not chemin or not os.path.exists(chemin):
        return None
    c = np.load(chemin)
    os.remove(chemin)
    return c


def decalages_par_voix(info, cibles, octave_imposee=None, transposition_dt=0):
    """Décalage de chaque voix cible, pour la version d'un passage dans cette voix : celui du chanteur qui va à cette voix
    (decision_hauteur.chanteurs) ; une seule voix trouvée, pour l'autre voix : celui de la classe de sa clé (homme,
    femme), sinon de la classe de la voix trouvée (decalage_par_voix_et_classe) ; octave imposée : elle partout. Ces
    décalages comprennent déjà la transposition de la chanson (appliquer_transposition), sauf l'octave imposée, à laquelle
    transposition_dt s'ajoute, et le repli sans décision (transposition_dt seul)."""
    V = cibles['voix']
    if octave_imposee in (-12, 0, 12):
        return [octave_imposee + transposition_dt] * len(V)
    dpvc = info.get('decalage_par_voix_et_classe') or {}
    chanteurs = [c for c in info.get('chanteurs') or [] if isinstance(c, dict)]
    classe_trouvee = (chanteurs[0].get('classe') if chanteurs else None) or (info.get('voix_principale') or {}).get('classe')
    out = []
    for j, v in enumerate(V):
        c = next((c for c in chanteurs if c.get('cible') == j and c.get('decalage') is not None), None)
        if c is not None:
            out.append(c['decalage'])
            continue
        classe = v.get('cle') if v.get('cle') in ('homme', 'femme') else classe_trouvee
        d = (dpvc.get(v['nom']) or {}).get(classe) if classe else None
        out.append(d if d is not None else transposition_dt)
    return out


def morceaux_utiles(segs, aff, plans, voix_trouvees=2, reglages=None):
    """Passages dont l'autre version compte le plus (versions_duo « utiles ») : {i: [raisons]}. Raisons : incertain (une
    piste mal reconnue, ou le chanteur du morceau choisi d'après ses voisins), hauteur ambiguë (240-330 Hz, où chanteur
    et chanteuse se croisent), ensemble (pistes dans des voix différentes), changement (voisin d'un changement de voix) ;
    une seule voix trouvée : tous les passages chantés (c'est la décision entière qui peut être fausse)."""
    R = {**OCTAVE_DEFAUT, **(reglages or {})}
    actifs = [s for s in segs if not s.get('muet')]
    voix_de = {}
    for s in actifs:
        voix_de[s['i']] = {par_i[s['i']]['cible'] for nom, par_i in (aff or {}).items()
                          if s['i'] in par_i and s['i'] in (plans or {}).get(nom, ())} or {s.get('cible')}
    out = {}
    for k, s in enumerate(actifs):
        i, r = s['i'], []
        if voix_trouvees < 2:
            r.append('une seule voix trouvée')
        etats = {par_i[i]['etat'] for nom, par_i in (aff or {}).items() if i in par_i and i in (plans or {}).get(nom, ())}
        if etats & {'incertaine', 'sans_indice'} or 'voisins' in str(s.get('base') or ''):
            r.append('incertain')
        if s.get('f0_med') and R['zone_basse_hz'] <= s['f0_med'] <= R['zone_haute_hz']:
            r.append('hauteur ambiguë')
        if len(voix_de[i]) > 1:
            r.append('ensemble')
        voisins = [actifs[x]['i'] for x in (k - 1, k + 1) if 0 <= x < len(actifs)]
        if any(voix_de[v] != voix_de[i] for v in voisins):
            r.append('changement')
        if r:
            out[i] = r
    return out


def versions_autres(morceaux, decalages, garder=None):
    """Morceaux de chaque autre voix d'une piste : [(rang, [(i, début, fin, décalage de la voix, fa, fb, voix)])], rang
    1 à nv - 1 (voix = voix du morceau + rang, modulo nv) ; garder : numéros des passages à convertir (None : tous)."""
    nv = len(decalages)
    out = []
    for r in range(1, nv):
        ms = []
        for m in morceaux:
            if garder is not None and m[0] not in garder:
                continue
            v = ((m[6] if len(m) > 6 else 0) + r) % nv
            ms.append((m[0], m[1], m[2], decalages[v], m[4], m[5], v))
        out.append((r, ms))
    return out


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


def assembler(x, morceaux, pad, convertir_morceau, graine, torch, couches=None):
    """Piste convertie, calée à l'échantillon près sur x : chaque morceau (i, début, fin, décalage, fondu au début,
    fondu à la fin ; en échantillons) est converti avec 0,25 s de marge de chaque côté, puis fondu aux coupes.
    convertir_morceau(xs, décalage) rend autant d'échantillons que xs. La porte des silences reste à l'appelant.
    Plusieurs voix cibles : un 7e élément, l'indice de la voix cible du morceau, passé en 3e argument
    (convertir_morceau(xs, décalage, voix)). couches : voir poser_morceaux."""
    def convertir(k, m, lo, hi):
        torch.manual_seed(graine + m[0])
        return convertir_morceau(x[lo:hi], m[3]) if len(m) < 7 else convertir_morceau(x[lo:hi], m[3], m[6])
    return poser_morceaux(len(x), morceaux, pad, convertir, couches)


def bornes_morceau(m, pad, n):
    """Échantillons [lo, hi) convertis pour le morceau m : ses bornes élargies de pad, dans la piste de n échantillons."""
    return max(0, m[1] - pad), min(n, m[2] + pad)


def poser_morceaux(n, morceaux, pad, morceau_converti, couches=None):
    """Assemblage d'assembler(), dans l'ordre des morceaux : morceau_converti(k, m, lo, hi) rend les hi - lo
    échantillons convertis du k-ième morceau (convertis ici, ou relus d'une étape qui les a convertis sur une autre carte :
    les mêmes poids, la même somme, dans le même ordre, donc la même piste au bit près).
    couches (deux versions de chaque passage, une voix par chanteur) : tableau (2 x voix, n) qui reçoit en plus la part de
    chaque morceau, fondus compris, dans la couche 2 x sa voix (7e élément) + la parité de son numéro. Deux morceaux de
    même parité ne se touchent jamais (un morceau d'au moins 2 s les sépare, plus long que les deux marges) : chaque
    couche garde chaque morceau à part, et le PC peut recomposer la piste avec une autre voix sur un passage, au bit près
    de ce qu'aurait rendu le noyau (couches_par_voix). La piste rendue ne change pas."""
    import numpy as np
    sortie = np.zeros(n, np.float32)
    for k, m in enumerate(morceaux):
        i, a, b, dec, fa, fb = m[:6]
        lo, hi = bornes_morceau(m, pad, n)
        y = morceau_converti(k, m, lo, hi)
        tt = np.arange(lo, hi)
        w = np.ones(hi - lo)
        if a > 0:
            w *= rampe(tt - a, fa)
        if b < n:
            w *= 1 - rampe(tt - b, fb)
        part = (y * w).astype(np.float32)
        sortie[lo:hi] += part
        if couches is not None:
            couches[2 * (m[6] if len(m) > 6 else 0) + i % 2, lo:hi] += part
    return sortie


# --- Deux GPU (machine Kaggle « GPU T4 x2 ») ---------------------------------------------------------------------------
# La machine NvidiaTeslaT4 de Kaggle a deux cartes T4. Les étapes à part lourdes (nettoyage, YingMusic-SVC) s'y
# partagent le travail : un processus par carte, chacun ne voyant que la sienne (CUDA_VISIBLE_DEVICES). Chaque tâche
# (une séparation, un morceau converti) ne dépend que de ses entrées et de sa graine : le résultat ne dépend ni de la
# carte ni de l'ordre. Une seule carte visible (ou le réglage gpus à 1) : la chaîne d'avant, inchangée.
CARTES_MAXI = 2


def compter_cartes(torch):
    """Cartes vues par le noyau : (nombre, noms). Une erreur de CUDA compte pour une carte (la chaîne d'avant)."""
    try:
        n = int(torch.cuda.device_count())
        return n, [str(torch.cuda.get_device_name(i)) for i in range(n)]
    except Exception:  # noqa: BLE001
        return 1, []


def cartes_a_utiliser(demande, visibles):
    """Nombre de cartes à utiliser : réglage gpus 'auto' (ou absent) → toutes celles qu'on voit, deux au plus ; 1 ou 2 → ce
    nombre, borné par les cartes vues. Rend (nombre, repli) : repli dit pourquoi on en prend moins que demandé, ou None."""
    visibles = max(int(visibles or 0), 0)
    auto = demande in (None, '', 'auto')
    voulu = CARTES_MAXI if auto else max(1, min(int(demande), CARTES_MAXI))
    n = max(1, min(voulu, visibles))
    if n >= voulu:
        return n, None
    return n, (f"{visibles} carte(s) GPU sur cette machine{'' if auto else f' au lieu de {voulu}'} : étapes une à une, "
               'sur une carte')


def env_carte(k, cartes):
    """Variables d'environnement d'une étape à part sur la k-ième carte : CUDA_VISIBLE_DEVICES réduit à elle (avec une
    seule carte utilisée, rien ne change : celle de toujours)."""
    if cartes <= 1:
        return {}
    vues = [c.strip() for c in (os.environ.get('CUDA_VISIBLE_DEVICES') or '').split(',') if c.strip()]
    if len(vues) < cartes:
        vues = [str(i) for i in range(cartes)]
    return {'CUDA_VISIBLE_DEVICES': vues[k]}


def plan_nettoyage(videos, cartes):
    """Tâches du nettoyage, par carte et dans l'ordre d'exécution. videos : [(id, durée en s)] dans l'ordre des vidéos.
    Tâches : ['karaoke', id], puis ['dereverb', id, 'lead'] et ['dereverb', id, 'choeurs'], qui attendent le karaoké de
    leur vidéo (karaoké et dereverb vont à peu près au même rythme, proportionnel à la durée).
    Une carte : l'ordre d'avant (karaoké de toutes les vidéos, puis voix principale et chœurs de chacune). Deux cartes :
    les karaokés d'abord, la plus longue vidéo sur la carte la moins chargée ; puis chaque dereverb, dans l'ordre où les
    karaokés finissent, sur la carte où il finirait le plus tôt : les deux dereverb d'une vidéo seule partent chacun sur
    sa carte. Chaque carte fait ses karaokés avant ses dereverb (un modèle chargé à la fois)."""
    if cartes <= 1:
        return [[['karaoke', v] for v, _ in videos]
                + [['dereverb', v, p] for v, _ in videos for p in ('lead', 'choeurs')]]
    duree = {v: float(d) for v, d in videos}
    charge, fin_k = [0.0] * cartes, {}
    karaokes, dereverbs = [[] for _ in range(cartes)], [[] for _ in range(cartes)]
    for v, d in sorted(videos, key=lambda vd: -float(vd[1])):
        k = min(range(cartes), key=lambda c: charge[c])
        charge[k] += duree[v]
        fin_k[v] = charge[k]
        karaokes[k].append(['karaoke', v])
    for v, _ in sorted(videos, key=lambda vd: fin_k[vd[0]]):
        for piste in ('lead', 'choeurs'):
            fins = [max(charge[c], fin_k[v]) + duree[v] for c in range(cartes)]
            k = min(range(cartes), key=lambda c: fins[c])
            dereverbs[k].append((fins[k] - duree[v], ['dereverb', v, piste]))
            charge[k] = fins[k]
    return [karaokes[k] + [t for _, t in sorted(dereverbs[k], key=lambda dt: dt[0])] for k in range(cartes)]


class TourDeChargement:
    """Chargement d'un modèle par une étape à part, quand plusieurs cartes travaillent : un chargement à la fois (verrou
    commun dans le dossier d'état), et la carte k ne fait son premier chargement qu'après celui de la carte k-1 (ou sa
    fin). Chargés en même temps, les modèles prennent deux à trois fois plus longtemps (essai du 05/11/2026 sur Kaggle :
    dereverb 50 s au lieu de 9, YingMusic-SVC 73 s au lieu de 24) ; l'un après l'autre, la carte 1 charge pendant que la
    carte 0 calcule. Le chargement ne change rien au résultat. Sans dossier d'état (une carte) : rien ne change."""
    ATTENTE_MAXI_S = 600

    def __init__(self, travail):
        self.etat, self.carte = travail.get('etat'), int(travail.get('carte') or 0)
        self.premier, self.verrou = True, None

    def __enter__(self):
        if not self.etat:
            return self
        if self.premier and self.carte > 0:
            debut = time.time()
            while (not any(os.path.exists(os.path.join(self.etat, f'{n}_{self.carte - 1}')) for n in ('charge', 'fin'))
                   and time.time() - debut < self.ATTENTE_MAXI_S):
                time.sleep(0.5)
        import fcntl
        self.verrou = open(os.path.join(self.etat, 'chargement.verrou'), 'w')
        fcntl.flock(self.verrou, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self.verrou:
            self.verrou.close()   # verrou rendu
            self.verrou = None
        if self.etat and self.premier:
            open(os.path.join(self.etat, f'charge_{self.carte}'), 'w').close()
        self.premier = False
        return False


def nom_tache(t):
    """Nom de fichier d'une tâche du nettoyage (témoins de fin) : karaoke_v1, dereverb_v1_lead…"""
    return '_'.join(str(x) for x in t)


def taches_a_reprendre(plan_une_carte, etat):
    """Tâches du nettoyage à refaire sur la carte 0 après les deux cartes : celles sans témoin de réussite, sauf celles
    qui ont échoué sur la carte 0 elle-même (une erreur de contenu s'y répéterait) et les dereverb dont le karaoké a
    échoué pour de bon. Dans l'ordre d'une carte seule (un karaoké avant ses dereverb)."""
    def lire(t):
        for fin in ('ok', 'erreur'):
            chemin = os.path.join(etat, f'{nom_tache(t)}.{fin}')
            if os.path.exists(chemin):
                with open(chemin) as f:
                    return fin, f.read().strip()
        return None, None
    reste, karaoke_perdu = [], set()
    for t in plan_une_carte:
        fin, carte = lire(t)
        if fin == 'ok':
            continue
        if fin == 'erreur' and carte == '0':
            if t[0] == 'karaoke':
                karaoke_perdu.add(t[1])
            continue
        if t[0] == 'dereverb' and t[1] in karaoke_perdu:
            continue
        reste.append(t)
    return reste


def fusionner_nettoyage(parts):
    """Résultats des étapes de nettoyage (une par carte, plus la reprise) réunis dans la forme d'une étape seule :
    modèles, chargements (le plus long de chaque modèle : ils se chargent en même temps), et, pour chaque vidéo, ses
    durées, ses pistes sèches, ses infos de séparation et sa première erreur ; durée de nettoyage d'une vidéo : la somme
    de ses tâches (temps de GPU, quelle que soit la carte). La carte de chaque tâche va dans cartes."""
    res = {'karaoke': None, 'dereverb': None, 'recouvrement': None, 'chargements_s': {}, 'videos': {}, 'cartes': []}
    erreurs = []
    for k, r in enumerate(parts):
        for c in ('karaoke', 'dereverb', 'recouvrement'):
            res[c] = res[c] or r.get(c)
        if r.get('precision'):
            res['precision'] = res.get('precision') or r['precision']
        if r.get('repli_fp32'):
            res.setdefault('repli_fp32', []).extend(r['repli_fp32'])
        for nom, s in (r.get('chargements_s') or {}).items():
            res['chargements_s'][nom] = max(res['chargements_s'].get(nom, 0.0), s)
        if r.get('erreur'):
            erreurs.append(r['erreur'])
        res['cartes'].append({'carte': r.get('carte', k), 'taches': r.get('taches_faites') or [],
                              'duree_s': r.get('duree_s'), 'code': r.get('code')})
        for vid, rv in (r.get('videos') or {}).items():
            v = res['videos'].setdefault(vid, {'durees_s': {}})
            v['durees_s'].update(rv.get('durees_s') or {})
            if rv.get('dereverb'):
                v.setdefault('dereverb', {}).update(rv['dereverb'])
            for cle, val in rv.items():
                if cle not in ('durees_s', 'dereverb', 'duree_s', 'erreur'):
                    v.setdefault(cle, val)
            if rv.get('erreur') and not v.get('erreur'):
                v['erreur'] = rv['erreur']
            v['duree_s'] = round(v.get('duree_s', 0.0) + (rv.get('duree_s') or 0.0), 1)
    for v in res['videos'].values():
        if v.get('erreur') and v.get('lead_sec') and v.get('choeurs_sec'):
            del v['erreur']   # échec sur une carte, réussite à la reprise
    if erreurs:
        res['erreur'] = ' ; '.join(erreurs)[:400]
    res['code'] = next((r.get('code') for r in parts if r.get('code')), 0)
    return res


def niveaux_nettoyage(x, lead, choeurs, lead_sec, ch_sec):
    """Niveaux (dBFS, sur les trames où la voix sonne) de la voix et des pistes du nettoyage, comme au banc d'essai."""
    import numpy as np
    db = lambda v: round(20 * float(np.log10(v + 1e-12)), 1)
    ref = x.mean(1)
    return {nom: db(rms_actif(y.mean(1), ref)) for nom, y in (
        ('voix', x), ('lead', lead), ('choeurs', choeurs), ('lead_sec', lead_sec), ('choeurs_sec', ch_sec),
        ('reverb_jetee', x - lead_sec - ch_sec))}


def ordre_morceaux(pistes, pad, invite_s=25.0):
    """Morceaux à convertir par YingMusic-SVC (toutes pistes, toutes vidéos), [[piste, morceau], …], le plus coûteux
    d'abord : chaque carte prend le premier libre (ying_morceaux), les derniers pris sont donc les plus courts et les
    cartes finissent ensemble. Coût d'un morceau : sa durée marge comprise plus l'invite de la référence (jusqu'à 25 s,
    convertie avec chaque fenêtre), fois les pas de diffusion. À coût égal, l'ordre des pistes puis des morceaux."""
    taches = [(((m[2] - m[1] + 2 * pad) / SR + invite_s) * float(p.get('pas') or 30), pi, mi)
              for pi, p in enumerate(pistes) for mi, m in enumerate(p['morceaux'])]
    return [[pi, mi] for _, pi, mi in sorted(taches, key=lambda t: (-t[0], t[1], t[2]))]


def tous_pris(travail):
    """Étape YingMusic sur une carte parmi plusieurs : tous ses morceaux sont-ils déjà pris par les autres cartes ? (une
    carte qui rejoint la conversion après les paroles n'a alors rien à faire, pas même à charger le modèle)"""
    reclamations, taches = travail.get('reclamations'), travail.get('taches')
    return bool(reclamations and taches and all(os.path.exists(os.path.join(reclamations, f'{pi}_{mi}')) for pi, mi in taches))


def memoire_gpu_max_mo(torch):
    """Mémoire du GPU réservée au plus fort par ce processus (Mo), pour mémoire ; None si CUDA ne la donne pas."""
    try:
        return round(torch.cuda.max_memory_reserved() / 2 ** 20)
    except Exception:  # noqa: BLE001
        return None


def paroles_a_reprendre(res):
    """Paroles faites sur une autre carte pendant la conversion : à refaire sur la carte 0 si elles n'ont pas tourné, si
    leur processus s'est arrêté en échec (carte arrêtée en route) ou si CUDA a refusé (carte en panne). Une erreur de
    contenu (modèle introuvable…) se répéterait : elle reste."""
    if not res:
        return True
    return bool(res.get('code')) or 'cuda' in str(res.get('erreur') or '').lower()


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


# --- Passages perdus au nettoyage : piste de secours (numpy seul : sert aussi sur le PC, kaggle/secours_local.py) -----
# Le dereverb prend parfois une voix de fond très réverbérée (un « oooh » de chœur) pour de la réverbération et la jette :
# ni la voix principale sèche ni les chœurs secs ne la contiennent, rien n'est converti, et comme la voix complète est
# retirée de la musique, il reste un trou. Ces zones sont reprises de la voix complète, avec les mêmes coupes et les
# mêmes décalages, dans une troisième piste « secours » qui n'ouvre sa porte que sur elles.
# Seuils (premier cas réel, 04/11/2026 : une chanson dont chaque refrain a un « oooh » de fond vers 263 Hz, perdu) :
# - écart : sur les trames chantées, la somme des pistes nettoyées reste à moins de 1 dB de la voix complète (médiane
#   0,03 dB, 90e centile 5,5 dB ; la reverb jetée est 8 dB sous la voix) ; les zones perdues ont 30 à 93 dB d'écart.
#   15 dB : très au-delà de la reverb retirée d'une note chantée, et une voix 15 dB plus basse est inaudible sous la
#   musique, donc perdue. Mêmes zones trouvées à 10, 15 et 20 dB ;
# - niveau : la voix complète sonne à moins de 20 dB de son haut (95e centile de l'enveloppe) ; les zones perdues sont 5
#   à 10 dB sous ce haut. Plus bas, le passage s'entendait à peine dans l'original, et c'est là que traînent les reverbs ;
# - passage : les trames perdues séparées de moins de 0,4 s forment un seul passage (un « oooh » coupé par une syllabe
#   que les pistes nettoyées ont gardée), jugé en entier ; durée 0,4 s au moins, mesurée sur l'enveloppe lissée (150 ms),
#   soit environ 0,25 s de note : plus court, la porte des pistes nettoyées (élargie de 0,2 s) et les fondus couvrent
#   déjà le trou (cas réel : passages perdus de 0,9 à 2,6 s) ;
# - pas de doublon : dans un passage, la porte de secours reste fermée là où les pistes nettoyées portent la voix (moins
#   de 6 dB d'écart : au moins la moitié de son amplitude), fondus de 80 ms ;
# - voisement : 30 % des trames du passage au moins ont une hauteur RMVPE (passages perdus : 55 à 100 %) ; un bruit, un
#   souffle ou une fuite de percussion n'en ont pas, et convertis ne rendraient que du bruit ;
# - pente : une traîne de réverbération est voisée elle aussi (55 à 100 % des fins de phrase), mais décroît : -18 dB/s
#   en médiane après une phrase, -39 dB/s au 10e centile ; une voix tenue reste stable (-3 à +8 dB/s dans les zones
#   perdues). Une zone qui décroît de plus de 15 dB/s est une traîne, laissée de côté ;
# - si plus de 40 % du temps où la voix sonne serait repris, le nettoyage a échoué : la voix complète est convertie
#   seule (chaîne d'avant) plutôt que trois pistes.
SECOURS_DEFAUT = {
    'ecart_db': 15.0,
    'sous_haut_db': 20.0,
    'trou_max_s': 0.4,
    'duree_min_s': 0.4,
    'voise_min': 0.3,
    'pente_min_db_s': -15.0,
    'ecart_porte_db': 6.0,    # dans un passage, porte fermée là où les pistes nettoyées sonnent déjà
    'fondu_s': 0.08,          # fondu (sinus²) centré sur chaque bord de porte
    'ouvert_min_s': 0.05,     # ouvertures plus brèves ignorées, fermetures plus brèves que 0,1 s comblées
    'part_max': 0.4,
}


def suites(masque):
    """[(début, fin exclue)] des suites de True d'un masque."""
    import numpy as np
    m = np.asarray(masque, bool)
    if not m.size or not m.any():
        return []
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    return list(zip(np.where(d == 1)[0].tolist(), np.where(d == -1)[0].tolist()))


def a_longueur(v, k, remplissage=0.0):
    """v recoupé ou complété (remplissage) à k valeurs."""
    import numpy as np
    v = np.asarray(v, np.float64)
    return v[:k] if len(v) >= k else np.concatenate([v, np.full(k - len(v), remplissage)])


def niveau_nettoye_db(nettes, k):
    """Niveau (dBFS par trame de 10 ms, lissé comme lisser_db) de la somme des pistes nettoyées telles qu'elles sonneront :
    nettes = [(lisse, gain_trames)] de chaque piste, porte comprise (une piste sous sa porte ne compte pas). Pistes
    supposées sans corrélation : puissances additionnées."""
    import numpy as np
    lin = np.zeros(k)
    for lisse_t, g_t in nettes:
        lin += a_longueur(g_t, k) ** 2 * 10 ** (a_longueur(lisse_t, k, -120.0) / 10)
    return 10 * np.log10(lin + 1e-12)


def zones_perdues(env, lisse, seuil, f0, nettes, reglages=None):
    """Zones où la voix complète sonne alors que voix principale sèche et chœurs secs ensemble sont bien plus bas (seuils :
    SECOURS_DEFAUT). env, lisse, seuil : enveloppe, enveloppe lissée et seuil de silence de la voix complète ; f0 : sa
    hauteur RMVPE (0 = non voisé), une trame toutes les 10 ms ; nettes : [(lisse, gain_trames)] des pistes nettoyées.
    Rend (zones gardées, info) ; chaque zone : début, fin, durée, écart médian (dB), niveaux, voisement, hauteur, pente ;
    info['ecartees'] : les candidates laissées de côté, avec la raison."""
    import numpy as np
    R = {**SECOURS_DEFAUT, **(reglages or {})}
    lisse = np.asarray(lisse, np.float64)
    k = len(lisse)
    f0 = a_longueur(f0, k)
    ln = niveau_nettoye_db(nettes, k)
    ecart = lisse - ln
    haut = float(np.percentile(env, 95)) if len(env) else -120.0
    plancher = max(float(seuil), haut - R['sous_haut_db'])
    brut = (lisse > plancher) & (ecart >= R['ecart_db'])
    masque = brut.copy()
    trou = int(round(R['trou_max_s'] / 0.01))
    reg = suites(brut)
    for (_, b0), (a1, _) in zip(reg, reg[1:]):
        if a1 - b0 <= trou:
            masque[b0:a1] = True
    gardees, ecartees = [], []
    for a, b in suites(masque):
        v = f0[a:b] > 1
        t = np.arange(b - a) * 0.01
        pente = float(np.polyfit(t, lisse[a:b], 1)[0]) if b - a >= 3 else 0.0
        perdues = brut[a:b]
        # porte : ouverte là où les pistes nettoyées ne portent pas la voix ; fermetures brèves comblées, ouvertures
        # brèves ignorées
        ouvert = ecart[a:b] >= R['ecart_porte_db']
        ferme = suites(~ouvert)
        for c, d in ferme:
            if 0 < c and d < b - a and d - c < 10:
                ouvert[c:d] = True
        ouvert_s = [[round((a + c) * 0.01, 2), round((a + d) * 0.01, 2)] for c, d in suites(ouvert)
                    if (d - c) * 0.01 >= R['ouvert_min_s']]
        z = {'debut_s': round(a * 0.01, 2), 'fin_s': round(b * 0.01, 2), 'duree_s': round((b - a) * 0.01, 2),
             'ecart_db': round(float(np.median(ecart[a:b][perdues])), 1),
             'niveau_voix_dbfs': round(float(np.median(lisse[a:b])), 1),
             'niveau_nettoye_dbfs': round(float(np.median(ln[a:b])), 1),
             'voise': round(float(v.mean()), 2), 'f0_hz': round(float(np.median(f0[a:b][v])), 1) if v.any() else None,
             'pente_db_s': round(pente, 1), 'ouvert': ouvert_s,
             'repris_s': round(sum(d - c for c, d in ouvert_s), 2)}
        if z['duree_s'] < R['duree_min_s']:
            z['raison'] = f"trop courte ({z['duree_s']:.2f} s < {R['duree_min_s']} s)"
        elif z['voise'] < R['voise_min']:
            z['raison'] = f"pas assez voisée ({z['voise']:.0%} < {R['voise_min']:.0%})"
        elif pente < R['pente_min_db_s']:
            z['raison'] = f"décroît comme une traîne de réverbération ({pente:.0f} dB/s < {R['pente_min_db_s']:.0f})"
        (ecartees if z.get('raison') else gardees).append(z)
    info = {'reglages': R, 'haut_voix_dbfs': round(haut, 1), 'plancher_dbfs': round(plancher, 1),
            'trames_perdues_s': round(float(brut.sum()) * 0.01, 2), 'ecartees': ecartees}
    return gardees, info


def decider_secours(zones, voix_active_s, reglages=None):
    """'secours' (piste de secours sur les zones), 'aucun' (rien de perdu : le mix ne change pas) ou 'voix_complete' (plus
    de part_max du temps chanté perdu : nettoyage raté, la voix complète est convertie seule). Rend (décision, secondes)."""
    R = {**SECOURS_DEFAUT, **(reglages or {})}
    secondes = round(sum(z['duree_s'] for z in zones), 2)
    if not zones:
        return 'aucun', 0.0
    if secondes > R['part_max'] * max(voix_active_s, 1e-9):
        return 'voix_complete', secondes
    return 'secours', secondes


def porte_zones(zones, n, fondu_s=SECOURS_DEFAUT['fondu_s']):
    """Porte de la piste de secours : 1 sur les parties ouvertes des zones (toute la zone si elle n'en précise pas), 0
    ailleurs, fondus en sinus² de fondu_s centrés sur les bords. Rend le gain par échantillon et par trame de 10 ms."""
    import numpy as np
    g = np.zeros(n, np.float32)
    f = max(int(fondu_s * SR), 2)
    for z in zones:
        for oa, ob in (z.get('ouvert') or [[z['debut_s'], z['fin_s']]]):
            a, b = int(round(oa * SR)), int(round(ob * SR))
            lo, hi = max(0, a - f), min(n, b + f)
            if hi <= lo:
                continue
            tt = np.arange(lo, hi)
            w = rampe(tt - a, f) * (1 - rampe(tt - b, f))
            g[lo:hi] = np.maximum(g[lo:hi], w.astype(np.float32))
    return g, g[::PAS].astype(np.float64)


def morceaux_piste(segs, piste, fondus):
    """Morceaux à convertir pour une piste : ceux de la voix complète (mêmes coupes, même décalage), sauf les muets ; une
    piste nettoyée saute ceux où elle se tait, la piste de secours ne garde que ceux qui touchent une de ses zones (fondus
    compris). [(i, début, fin, décalage, fondu au début, fondu à la fin)], en échantillons.
    Plusieurs voix cibles : piste['affectation'] = {i: (voix cible, décalage)} (affecter_pistes) ; chaque morceau prend
    alors le décalage de sa piste et un 7e élément, l'indice de sa voix cible."""
    out = []
    marge = SECOURS_DEFAUT['fondu_s']
    aff = piste.get('affectation')
    for s in segs:
        if s['muet']:
            continue
        a, b = s['debut'], s['fin']
        if piste['nom'] == 'secours':
            if not any(z['debut_s'] - marge < b / SR and z['fin_s'] + marge > a / SR for z in piste['zones']):
                continue
        elif piste['nom'] != 'voix' and float(piste['lisse'][a // PAS:b // PAS + 1].max()) < piste['seuil']:
            continue
        if aff is None:
            out.append((s['i'], a, b, s['decalage'], fondus.get(a, 0), fondus.get(b, 0)))
        else:
            v, dec = aff.get(s['i'], (s.get('cible', 0), s['decalage']))
            out.append((s['i'], a, b, dec, fondus.get(a, 0), fondus.get(b, 0), v))
    return out


def gain_secours(x, y, gain, borne_db=20.0):
    """Gain qui ramène la piste de secours convertie (y, porte appliquée) au niveau de la voix complète (x) dans ses
    zones (là où la porte est ouverte) ; borné à ±borne_db. 1 si la porte n'est ouverte nulle part."""
    import numpy as np
    m = np.asarray(gain) > 0.5
    if not m.any():
        return 1.0
    sx = float(np.sqrt(np.mean(np.asarray(x, np.float64)[m] ** 2)))
    sy = float(np.sqrt(np.mean(np.asarray(y, np.float64)[m] ** 2)))
    if sy <= 1e-9 or sx <= 1e-9:
        return 1.0
    return float(np.clip(sx / sy, 10 ** (-borne_db / 20), 10 ** (borne_db / 20)))


# --- Paroles : zones à transcrire, garde-fous contre les hallucinations, lignes (sans torch : testé sur le PC) --------
# Transcription : Whisper large-v3 par transformers (déjà installé dans l'image Kaggle, avec le torch de Kaggle :
# faster-whisper demanderait ctranslate2, construit pour CUDA 12 et cuDNN 9, quand l'image a CUDA 13), une seule fois par
# phrase chantée détectée (hauteur RMVPE), jamais sur les silences ni les ponts instrumentaux. Traduction vers le
# français : NLLB-200 distillé 600M (une seule maquette pour toutes les langues ; poids CC BY-NC 4.0, comme ceux de
# YingMusic-SVC : usage non commercial), ligne par ligne, mêmes horodatages.
WHISPER = ('openai/whisper-large-v3', 'whisper-large-v3')                 # poids Apache-2.0
TRADUCTEUR = ('facebook/nllb-200-distilled-600M', 'nllb-200-distilled-600M')  # poids CC BY-NC 4.0
PAROLES_DEFAUT = {
    'marge_voise_s': 0.3,     # une trame qui sonne est chantée si une hauteur est trouvée à moins de 0,3 s (consonnes)
    'trou_max_s': 0.6,        # pause plus courte : même phrase
    'marge_s': 0.25,          # marge autour de chaque phrase (attaques, fins de mots)
    'duree_min_s': 0.4,       # phrase plus courte : ignorée (un souffle, un « ah »)
    'morceau_max_s': 25.0,    # Whisper lit 30 s à la fois
    'pause_morceau_s': 2.0,   # pause plus longue (pont instrumental) : nouveau morceau
    'langue_proba_min': 0.5,  # langue d'une phrase moins sûre, ou phrase de moins de 1,5 s : celle de ses voisines
    'pause_ligne_s': 0.5,     # lignes : coupées aux pauses, à la ponctuation, à 50 caractères ou 7 s
    'ligne_car_max': 50,
    'ligne_duree_max_s': 7.0,
    'mot_voise_min': 0.25,    # un mot dont moins d'un quart tombe dans le chant est inventé
    'boucle_mot_min_s': 0.12,  # une suite répétée 3 fois ou plus, chantée à moins de 0,12 s par mot : inventée
    'compression_max': 2.4,   # texte d'un morceau plus répétitif (zlib, critère d'OpenAI) : boucles retirées d'office
    'coupe_memoire_min_s': 4.0,  # morceau à court de mémoire GPU : coupé en deux, tant que chaque moitié fait 4 s ou plus
    'memoire_min_mo': 8000,   # mémoire libre voulue sur la carte avant de charger Whisper (allégé : 7,3 Gio au pire)
    'attente_memoire_max_s': 60.0,  # attente au plus de cette mémoire (une étape qui vient de finir rend encore la sienne)
    'bilingue': True,         # phrase ambiguë (refrain mêlant deux langues) : essayée dans ses deux langues, la plus sûre gagne
    'bilingue_proba_sure': 0.8,  # langue détectée moins sûre que cela : phrase ambiguë (comme une langue isolée ou lissée)
    'bilingue_alt_proba_min': 0.1,  # seconde langue essayée si Whisper lui donne au moins cela (sauf langue des voisines)
    'bilingue_duree_min_s': 0.8,  # phrase plus courte : pas essayée (trop peu de son pour départager)
    'bilingue_max_phrases': 12,   # plafond : au plus ce nombre de phrases essayées par vidéo…
    'bilingue_max_part': 0.2,     # … et au plus cette part de la durée chantée (surcoût GPU : 2 passes courtes par phrase)
    'bilingue_marge': 0.15,       # l'autre langue doit gagner d'au moins 0,15 (log-probabilité par jeton) pour l'emporter
    'bilingue_penalite': 0.25,    # points retirés par point de taux de compression au-delà de compression_max (boucles)
}
# Mémoire du GPU de Whisper en horodatage par mot (calculée d'après le code de transformers 5.20). Le pipeline cherche en
# faisceau (num_beams=5 par défaut : la configuration de large-v3 n'en fixe pas) et le mot à mot lui fait garder tous les
# poids d'attention : ceux de l'encodeur (32 couches × 20 têtes × 1 500² en float16 : 2,7 Gio, inutiles ici), puis, à
# chaque jeton, ceux des 32 couches du décodeur pour les 5 faisceaux (attention croisée : 9,6 Mo), recopiés une seconde
# fois à la fin pour les horodatages. Une passe coûte ainsi 6,7 Gio (modèle, attentions de l'encodeur, cache croisé des
# 5 faisceaux) plus 21 Mo par jeton : 8,7 Gio à 100 jetons, 10,7 à 200, 16 aux 448 d'une boucle. Les horodatages ne
# lisent que les 10 têtes d'alignement, dans 10 couches : alleger_whisper laisse l'encodeur sans ses attentions et vide
# les autres à leur sortie (le calcul est le même, seuls les poids gardés changent) ; une passe coûte alors 4 Gio plus
# 8 Mo par jeton, 7,3 Gio au pire.
GARDE_FOUS_WHISPER = {'condition_on_prev_tokens': False, 'compression_ratio_threshold': 1.35,   # 1,35 : calculé par
                      'no_speech_threshold': 0.6, 'logprob_threshold': -1.0,                   # transformers sur les
                      'temperature': (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)}                           # jetons, pas le texte
# Phrases que Whisper invente sur la musique ou le silence (génériques de sous-titres, appels à s'abonner…)
HALLUCINATIONS = re.compile(r"amara\.org|sous-titr|subt[ií]tul|sottotitol|untertitel|legendas? (por|pela)|subtitles? by|"
                            r"thanks? (you )?for watching|merci d'avoir regard|gracias por ver|suscr[ií]bete|"
                            r"abonnez[- ]vous|like and subscribe|www\.|https?://|©|transcri(pt|bed) by|продолжение следует",
                            re.I)
MUSIQUE_SEULE = re.compile(r"^\W*(♪|musique|music|música|musica|instrumental|applaudissements|rires)?\W*$", re.I)
NLLB = {'af': 'afr_Latn', 'am': 'amh_Ethi', 'ar': 'arb_Arab', 'az': 'azj_Latn', 'be': 'bel_Cyrl', 'bg': 'bul_Cyrl',
        'bn': 'ben_Beng', 'bs': 'bos_Latn', 'ca': 'cat_Latn', 'cs': 'ces_Latn', 'cy': 'cym_Latn', 'da': 'dan_Latn',
        'de': 'deu_Latn', 'el': 'ell_Grek', 'en': 'eng_Latn', 'es': 'spa_Latn', 'et': 'est_Latn', 'eu': 'eus_Latn',
        'fa': 'pes_Arab', 'fi': 'fin_Latn', 'fr': 'fra_Latn', 'gl': 'glg_Latn', 'gu': 'guj_Gujr', 'ha': 'hau_Latn',
        'he': 'heb_Hebr', 'hi': 'hin_Deva', 'hr': 'hrv_Latn', 'ht': 'hat_Latn', 'hu': 'hun_Latn', 'hy': 'hye_Armn',
        'id': 'ind_Latn', 'is': 'isl_Latn', 'it': 'ita_Latn', 'ja': 'jpn_Jpan', 'jw': 'jav_Latn', 'ka': 'kat_Geor',
        'kk': 'kaz_Cyrl', 'km': 'khm_Khmr', 'kn': 'kan_Knda', 'ko': 'kor_Hang', 'lb': 'ltz_Latn', 'ln': 'lin_Latn',
        'lo': 'lao_Laoo', 'lt': 'lit_Latn', 'lv': 'lvs_Latn', 'mg': 'plt_Latn', 'mk': 'mkd_Cyrl', 'ml': 'mal_Mlym',
        'mr': 'mar_Deva', 'ms': 'zsm_Latn', 'mt': 'mlt_Latn', 'my': 'mya_Mymr', 'ne': 'npi_Deva', 'nl': 'nld_Latn',
        'nn': 'nno_Latn', 'no': 'nob_Latn', 'oc': 'oci_Latn', 'pa': 'pan_Guru', 'pl': 'pol_Latn', 'ps': 'pbt_Arab',
        'pt': 'por_Latn', 'ro': 'ron_Latn', 'ru': 'rus_Cyrl', 'sd': 'snd_Arab', 'si': 'sin_Sinh', 'sk': 'slk_Latn',
        'sl': 'slv_Latn', 'sn': 'sna_Latn', 'so': 'som_Latn', 'sq': 'als_Latn', 'sr': 'srp_Cyrl', 'su': 'sun_Latn',
        'sv': 'swe_Latn', 'sw': 'swh_Latn', 'ta': 'tam_Taml', 'te': 'tel_Telu', 'tg': 'tgk_Cyrl', 'th': 'tha_Thai',
        'tk': 'tuk_Latn', 'tl': 'tgl_Latn', 'tr': 'tur_Latn', 'tt': 'tat_Cyrl', 'uk': 'ukr_Cyrl', 'ur': 'urd_Arab',
        'uz': 'uzn_Latn', 'vi': 'vie_Latn', 'yi': 'ydd_Hebr', 'yo': 'yor_Latn', 'yue': 'yue_Hant', 'zh': 'zho_Hans'}


def intervalles_s(masque, marge=0.0, trou=0.0, duree_min=0.0, fin=None):
    """Suites de True d'un masque par trames de 10 ms -> [[début, fin]] en secondes : trous plus courts que trou
    comblés, puis élargies de marge (bornées à [0, fin]) et fusionnées, enfin celles de moins de duree_min retirées."""
    reg = [[a * 0.01, b * 0.01] for a, b in suites(masque)]
    fus = []
    for a, b in reg:
        if fus and a - fus[-1][1] <= trou:
            fus[-1][1] = b
        else:
            fus.append([a, b])
    fus = [z for z in fus if z[1] - z[0] >= duree_min]
    fin = fin if fin is not None else len(masque) * 0.01
    out = []
    for a, b in fus:
        a, b = max(0.0, a - marge), min(fin, b + marge)
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[round(a, 2), round(b, 2)] for a, b in out]


def zones_a_transcrire(lisse, seuil, f0, reglages=None):
    """Phrases chantées d'une piste à transcrire (lisse et seuil : son enveloppe lissée et son seuil de silence ; f0 :
    hauteur RMVPE de la voix complète, une trame par 10 ms) : là où elle sonne avec une hauteur à moins de marge_voise_s.
    Rend (phrases, chant) en secondes : phrases (élargies, fusionnées, au plus morceau_max_s, coupées au creux le plus
    bas), chant (trames chantées brutes, pour écarter ensuite les mots inventés hors du chant)."""
    import numpy as np
    R = {**PAROLES_DEFAUT, **(reglages or {})}
    lisse = np.asarray(lisse, np.float64)
    k = len(lisse)
    voise = a_longueur(f0, k) > 1
    m = int(round(R['marge_voise_s'] / 0.01))
    pres = np.convolve(voise.astype(np.float64), np.ones(2 * m + 1), mode='same') > 0
    chant = (lisse > seuil) & pres
    fin = k * 0.01
    phrases = intervalles_s(chant, R['marge_s'], R['trou_max_s'], R['duree_min_s'], fin)
    coupees = []
    for a, b in phrases:
        while b - a > R['morceau_max_s']:
            lo, hi = int((a + 0.6 * R['morceau_max_s']) / 0.01), int((a + R['morceau_max_s']) / 0.01)
            fen = lisse[lo:hi]   # le creux le plus tardif (à 1 dB du plus bas), comme decouper
            c = (lo + int(np.where(fen <= fen.min() + 1)[0][-1])) * 0.01 if hi > lo else a + R['morceau_max_s']
            coupees.append([round(a, 2), round(c, 2)])
            a = c
        coupees.append([round(a, 2), round(b, 2)])
    return coupees, intervalles_s(chant, fin=fin)


def lisser_langues(langues, reglages=None):
    """Langue de chaque phrase : [(code, probabilité, durée_s)] -> [code]. Une phrase peu sûre ou courte prend la langue
    de la voisine sûre la plus proche (sinon celle qui domine la chanson) : une langue par passage chanté."""
    R = {**PAROLES_DEFAUT, **(reglages or {})}
    sures = [i for i, (c, p, d) in enumerate(langues) if c and p >= R['langue_proba_min'] and d >= 1.5]
    poids = {}
    for c, p, d in langues:
        if c:
            poids[c] = poids.get(c, 0.0) + p * d
    dominante = max(poids, key=poids.get) if poids else None
    out = []
    for i, (c, p, d) in enumerate(langues):
        if i in sures or not sures:
            out.append(c or dominante)
        else:
            j = min(sures, key=lambda s: (abs(s - i), s))
            out.append(langues[j][0])
    return out


def grouper_morceaux(phrases, langues, reglages=None):
    """Phrases consécutives de même langue réunies en morceaux d'au plus morceau_max_s, coupés aux longues pauses.
    Rend [(début, fin, langue, [indices des phrases])]."""
    R = {**PAROLES_DEFAUT, **(reglages or {})}
    out = []
    for i, ((a, b), lg) in enumerate(zip(phrases, langues)):
        if out:
            d, f, l2, idx = out[-1]
            if l2 == lg and a - f <= R['pause_morceau_s'] and b - d <= R['morceau_max_s']:
                out[-1] = (d, b, lg, idx + [i])
                continue
        out.append((a, b, lg, [i]))
    return out


def phrases_ambigues(phrases, langues, choisies, tops=None, reglages=None):
    """Phrases dont la langue retenue est douteuse (refrain mêlant deux langues), à essayer dans une seconde langue :
    langue détectée peu sûre (moins de bilingue_proba_sure), langue changée par le lissage (lisser_langues), ou langue
    retenue seule contre ses deux voisines qui partagent une autre. langues : [(code, proba, durée)] ; choisies : langues
    retenues ; tops : trois langues les plus probables de chaque phrase ([[code, proba]…] ou None). Rend
    [(indice, langue retenue, autre langue, doute)] du plus douteux au moins douteux, dans le plafond
    (bilingue_max_phrases, bilingue_max_part de la durée chantée) ; [] si bilingue est à False."""
    R = {**PAROLES_DEFAUT, **(reglages or {})}
    if not R['bilingue']:
        return []
    tops = tops or [None] * len(phrases)
    trouvees = []
    for i, ((a, b), (c, p, d), lg) in enumerate(zip(phrases, langues, choisies)):
        if not lg or b - a < R['bilingue_duree_min_s']:
            continue
        proba = {code: q for code, q in (tops[i] or [])}
        sure_lg = p if c == lg else proba.get(lg, 0.0)
        voisines = {choisies[j] for j in (i - 1, i + 1) if 0 <= j < len(choisies)}
        seule = len(voisines) == 1 and i > 0 and i + 1 < len(choisies) and next(iter(voisines)) not in (lg, None)
        if sure_lg >= R['bilingue_proba_sure'] and not seule:
            continue
        alt = next(iter(voisines)) if seule else None
        if alt is None:
            for code, q in sorted(proba.items(), key=lambda t: -t[1]):
                if code != lg and q >= R['bilingue_alt_proba_min']:
                    alt = code
                    break
        if alt is None and c and c != lg:
            alt = c
        if alt:
            trouvees.append((i, lg, alt, round(1.0 - sure_lg + (0.5 if seule else 0.0), 3)))
    trouvees.sort(key=lambda t: (-t[3], t[0]))
    plafond_s = R['bilingue_max_part'] * sum(b - a for a, b in phrases)
    out, total = [], 0.0
    for t in trouvees:
        d = phrases[t[0]][1] - phrases[t[0]][0]
        if len(out) >= R['bilingue_max_phrases'] or total + d > plafond_s:
            continue
        out.append(t)
        total += d
    return out


def note_transcription(texte, logprob, reglages=None):
    """Sûreté d'une transcription : log-probabilité moyenne par jeton (comme avg_logprob de Whisper), moins une pénalité
    pour le taux de compression au-delà de compression_max (texte répétitif, boucle). Texte vide ou sans mesure :
    -inf (jamais retenue)."""
    R = {**PAROLES_DEFAUT, **(reglages or {})}
    if not (texte or '').strip() or logprob is None:
        return float('-inf')
    return logprob - R['bilingue_penalite'] * max(0.0, taux_compression(texte) - R['compression_max'])


def departager_langue(audio, lg, alt, transcrire_phrase, noter, reglages=None):
    """Une phrase ambiguë transcrite dans sa langue retenue (lg) puis dans l'autre (alt) : transcrire_phrase(audio,
    langue) -> texte ; noter(audio, langue, texte) -> log-probabilité moyenne par jeton. L'autre langue l'emporte si sa
    note dépasse celle de lg de bilingue_marge. Rend (langue gagnante, note lg, note alt, texte gagnant) ; un essai qui
    échoue (exception, texte vide) ne gagne jamais."""
    R = {**PAROLES_DEFAUT, **(reglages or {})}
    notes, textes = {}, {}
    for code in (lg, alt):
        try:
            textes[code] = transcrire_phrase(audio, code) or ''
            notes[code] = note_transcription(textes[code], noter(audio, code, textes[code]), R)
        except Exception:  # noqa: BLE001
            textes[code], notes[code] = '', float('-inf')
    if notes[alt] > notes[lg] + R['bilingue_marge']:
        return alt, notes[lg], notes[alt], textes[alt]
    return lg, notes[lg], notes[alt], textes[lg]


def manque_de_memoire(e):
    """L'erreur est-elle un manque de mémoire du GPU (torch.OutOfMemoryError, ou son message) ?"""
    return type(e).__name__ == 'OutOfMemoryError' or 'out of memory' in str(e).lower()


def transcrire_avec_repli(appeler, langue, mots=True, liberer=lambda: None):
    """Un morceau transcrit des garde-fous complets à la transcription la plus simple : appeler(return_timestamps,
    generate_kwargs) -> sortie du pipeline. Rend (sortie, mode, erreurs). Le mot à mot à court de mémoire GPU rend le
    mode 'mémoire' sans essayer ses garde-fous réduits (la même passe y manquerait encore : transcrire_video coupe le
    morceau) ; liberer() rend d'abord la mémoire, hors de l'erreur (sa pile lâchée). mots à False : sans horodatage par
    mot (morceau trop court pour être coupé)."""
    base = {'language': langue, 'task': 'transcribe'}
    erreurs, manque = [], False
    for mode, ts, gk in (('mots', 'word', {**base, **GARDE_FOUS_WHISPER}), ('mots, garde-fous réduits', 'word', base),
                         ('segments', True, {**base, **GARDE_FOUS_WHISPER}), ('segments, garde-fous réduits', True, base)):
        if ts == 'word' and not mots:
            continue
        try:
            return appeler(ts, dict(gk)), mode, erreurs
        except Exception as e:  # noqa: BLE001
            erreurs.append(f'{mode} : {e!r}'[:300])
            manque = manque_de_memoire(e)
        if manque:
            liberer()
            if ts == 'word':
                return None, 'mémoire', erreurs
    return None, None, erreurs


def couper_morceau(morceau, phrases, x16, reglages=None, sr=16000):
    """Morceau (début, fin, langue, [indices des phrases]) à court de mémoire GPU, coupé en deux plus courts (moins de
    jetons à générer) : entre deux de ses phrases, au plus près du milieu ; une seule phrase : au creux le plus bas du son
    (trames de 20 ms) dans son tiers du milieu. None s'il dure moins de deux fois coupe_memoire_min_s."""
    import numpy as np
    R = {**PAROLES_DEFAUT, **(reglages or {})}
    a, b, lg, idx = morceau
    if b - a < 2 * R['coupe_memoire_min_s']:
        return None
    if len(idx) >= 2:
        milieu = (a + b) / 2
        k = min(range(1, len(idx)), key=lambda j: (abs((phrases[idx[j - 1]][1] + phrases[idx[j]][0]) / 2 - milieu), j))
        return [(a, phrases[idx[k - 1]][1], lg, idx[:k]), (phrases[idx[k]][0], b, lg, idx[k:])]
    fen = int(0.02 * sr)
    lo, hi = int((a + (b - a) / 3) * sr), min(int((a + 2 * (b - a) / 3) * sr), len(x16))
    n = (hi - lo) // fen
    if n >= 1:
        e = (np.asarray(x16[lo:lo + n * fen], np.float64).reshape(n, fen) ** 2).mean(1)
        c = round((lo + int(np.argmin(e)) * fen + fen // 2) / sr, 2)
    else:
        c = round((a + b) / 2, 2)
    return [(a, c, lg, list(idx)), (c, b, lg, list(idx))]


def taux_compression(texte):
    """Longueur du texte sur sa longueur compressée (zlib) : au-delà de 2,4, une boucle de Whisper (critère d'OpenAI)."""
    import zlib
    b = texte.encode('utf-8')
    return len(b) / len(zlib.compress(b)) if b else 0.0


def mot_normal(m):
    return re.sub(r"[^\w']+", '', m.lower())


def recouvrement(a, b, intervalles):
    """Part de [a, b] qui tombe dans les intervalles (triés, disjoints)."""
    if b <= a:
        return float(any(x <= a <= y for x, y in intervalles))
    return sum(max(0.0, min(b, y) - max(a, x)) for x, y in intervalles) / (b - a)


def retirer_boucles(mots, forcer=False, mot_min_s=PAROLES_DEFAUT['boucle_mot_min_s']):
    """Une suite de 1 à 8 mots répétée au moins 3 fois d'affilée n'est gardée qu'une fois si elle est inventée : chantée
    trop vite (moins de mot_min_s par mot) ou, avec forcer (texte trop répétitif), dans tous les cas. Un refrain chanté
    (« no, no, no », un vers repris) garde ses répétitions. Rend (mots, nombre de mots retirés)."""
    norm = [mot_normal(m['mot']) for m in mots]
    out, i, retires = [], 0, 0
    while i < len(mots):
        meilleur = None
        for n in range(1, 9):
            motif = norm[i:i + n]
            if len(motif) < n or not all(motif):
                break
            k = 1
            while norm[i + k * n:i + (k + 1) * n] == motif:
                k += 1
            if k >= 3 and (meilleur is None or k * n > meilleur[0] * meilleur[1]):
                meilleur = (k, n)
        if meilleur:
            k, n = meilleur
            bloc = mots[i:i + k * n]
            par_mot = (bloc[-1]['fin'] - bloc[0]['debut']) / len(bloc)
            if forcer or par_mot < mot_min_s:
                out += mots[i:i + n]
                retires += (k - 1) * n
                i += k * n
                continue
        out.append(mots[i])
        i += 1
    return out, retires


def nettoyer_mots(mots, chant, texte_morceau='', reglages=None):
    """Garde-fous après Whisper, sur les mots d'un morceau ([{debut, fin, mot, brut}] en secondes de la piste) :
    mots vides ou hors du chant retirés, puis boucles inventées (retirer_boucles ; d'office si le texte du morceau est trop
    répétitif). Rend (mots, compte des retraits)."""
    R = {**PAROLES_DEFAUT, **(reglages or {})}
    compte = {'hors_chant': 0, 'boucles': 0}
    garde = []
    for m in mots:
        if not mot_normal(m['mot']):
            continue
        if recouvrement(m['debut'], m['fin'], chant) < R['mot_voise_min']:
            compte['hors_chant'] += 1
            continue
        garde.append(m)
    garde, compte['boucles'] = retirer_boucles(garde, taux_compression(texte_morceau) > R['compression_max'],
                                               R['boucle_mot_min_s'])
    return garde, compte


def lignes_de_mots(mots, reglages=None):
    """Mots -> lignes de paroles [{debut, fin, texte, langue, mots}] : nouvelle ligne à une pause, après une fin de phrase,
    au-delà de ligne_car_max caractères ou de ligne_duree_max_s, ou quand la langue change."""
    R = {**PAROLES_DEFAUT, **(reglages or {})}
    lignes, cur = [], []

    def texte(ms):
        return re.sub(r'\s+', ' ', ''.join(m.get('brut', ' ' + m['mot']) for m in ms)).strip()
    for m in mots:
        if cur:
            p = cur[-1]
            if (m['debut'] - p['fin'] > R['pause_ligne_s'] or m.get('langue') != p.get('langue')
                    or (re.search(r'[.!?…]["»”)]*$', p['mot']) and len(cur) >= 2)
                    or len(texte(cur + [m])) > R['ligne_car_max'] or m['fin'] - cur[0]['debut'] > R['ligne_duree_max_s']):
                lignes.append(cur)
                cur = []
        cur.append(m)
    if cur:
        lignes.append(cur)
    return [{'debut': round(l[0]['debut'], 2), 'fin': round(max(m['fin'] for m in l), 2), 'texte': texte(l),
             'langue': l[0].get('langue'),
             'mots': [{'debut': round(m['debut'], 2), 'fin': round(m['fin'], 2), 'mot': m['mot'].strip()} for m in l]}
            for l in lignes]


def filtrer_lignes(lignes):
    """Lignes inventées retirées (génériques de sous-titres, « musique » seule…), doublons exacts fusionnés. Rend (lignes,
    nombre retiré)."""
    out, retirees = [], 0
    for l in lignes:
        if not l['texte'] or HALLUCINATIONS.search(l['texte']) or MUSIQUE_SEULE.match(l['texte']):
            retirees += 1
            continue
        if out and mot_normal(out[-1]['texte']) == mot_normal(l['texte']) and l['debut'] < out[-1]['fin']:
            out[-1]['fin'] = max(out[-1]['fin'], l['fin'])
            retirees += 1
            continue
        out.append(l)
    return out, retirees


def langue_principale(lignes):
    """La langue chantée le plus longtemps (None sans paroles)."""
    t = {}
    for l in lignes:
        if l.get('langue'):
            t[l['langue']] = t.get(l['langue'], 0.0) + max(l['fin'] - l['debut'], 0.01)
    return max(t, key=t.get) if t else None


def mots_proportionnels(texte, debut, fin):
    """Mots d'un segment sans horodatage par mot : durée partagée au prorata des lettres (repli)."""
    brut = re.findall(r'\s*\S+', texte)
    tot = sum(len(b.strip()) for b in brut) or 1
    out, t = [], debut
    for b in brut:
        d = (fin - debut) * len(b.strip()) / tot
        out.append({'debut': t, 'fin': t + d, 'mot': b.strip(), 'brut': b})
        t += d
    return out


def paroles_json(lignes, traduction=None, source_traduction=None, piste=None):
    """Contenu de paroles.json (format 1 ; contrat avec l'interface, à ne pas changer sans la prévenir). Temps en secondes
    du son doublé ; doubler.py les cale ensuite sur la vidéo de sortie (decalage_s)."""
    langue = langue_principale(lignes)
    d = {'format': 1, 'langue': langue, 'source': WHISPER[1], 'segments': lignes, 'traductions': {}, 'decalage_s': 0.0}
    if piste:
        d['piste'] = piste
    if traduction is not None:
        d['traductions']['fr'] = {'source': source_traduction or TRADUCTEUR[1],
                                  'segments': [{'debut': l['debut'], 'fin': l['fin'], 'texte': t}
                                               for l, t in zip(lignes, traduction)]}
    return d


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


def etape_a_part(etape, travail, cartes=1):
    """Lance une étape (nettoyer, ying, paroles) dans son propre processus : le GPU est rendu à sa sortie, et YingMusic a
    son propre paquet « modules », homonyme de celui de Seed-VC. Rend le JSON écrit par l'étape (même partiel si elle a
    planté en route), plus son code de sortie et sa durée. Avec plusieurs cartes et travail['carte'] : sur cette carte
    seule (env_carte) ; sinon comme toujours (la carte 0)."""
    resultat = travail['resultat']
    os.makedirs(os.path.dirname(resultat), exist_ok=True)
    if os.path.exists(resultat):
        os.remove(resultat)
    chemin = resultat + '.travail.json'
    ecrire_json(chemin, travail)
    t0 = time.time()
    carte = env_carte(int(travail['carte']), cartes) if cartes > 1 and travail.get('carte') is not None else {}
    r = subprocess.run([sys.executable, script_complet(), etape, chemin],
                       env={**os.environ, 'DOUBLER_DEBUT': repr(DEBUT), **carte})
    res = lire_json(resultat, {}) or {}
    res.update({'code': r.returncode, 'duree_s': round(time.time() - t0, 1)})
    if r.returncode:
        log(f'ÉTAPE {etape} : arrêtée avec le code {r.returncode}')
    return res


def etapes_a_part(etape, travaux, cartes, avant=None):
    """Plusieurs étapes à part en même temps, la k-ième sur la k-ième carte (env_carte), chacune dans son processus comme
    etape_a_part. Quand l'une s'arrête, le témoin <etat>/fin_<k> le dit aux autres (une dereverb qui attend un karaoké de
    cette carte cesse d'attendre ; la reprise s'en chargera). Rend leurs résultats, dans l'ordre des travaux.
    avant : {k: (étape, travail)}, une étape que la k-ième carte fait d'abord, dans son propre processus (les paroles
    pendant que les autres cartes convertissent) : la sienne part à sa fin, sur la même carte, une fois la mémoire du GPU
    rendue. Résultat de l'étape préalable dans « avant » ; depart_s : quand la sienne est partie, duree_s : sa durée."""
    avant = avant or {}

    def lancer_etape(k, nom, travail):
        resultat = travail['resultat']
        os.makedirs(os.path.dirname(resultat), exist_ok=True)
        if os.path.exists(resultat):
            os.remove(resultat)
        chemin = resultat + '.travail.json'
        ecrire_json(chemin, travail)
        env = {**os.environ, 'DOUBLER_DEBUT': repr(DEBUT), **env_carte(k, cartes)}
        return subprocess.Popen([sys.executable, script_complet(), nom, chemin], env=env)
    prealables = {k: {'proc': lancer_etape(k, *avant[k]), 'duree_s': None} for k in range(len(travaux)) if k in avant}
    procs = [None if k in prealables else lancer_etape(k, etape, travail) for k, travail in enumerate(travaux)]
    t0, durees, departs = time.time(), [None] * len(procs), [0.0] * len(procs)
    while any(d is None for d in durees):
        for k, p in enumerate(procs):
            if p is None:
                if prealables[k]['proc'].poll() is not None:   # étape préalable finie : la carte passe à la sienne
                    prealables[k]['duree_s'] = departs[k] = round(time.time() - t0, 1)
                    procs[k] = lancer_etape(k, etape, travaux[k])
                continue
            if durees[k] is None and p.poll() is not None:
                durees[k] = round(time.time() - t0 - departs[k], 1)
                if travaux[k].get('etat'):
                    with open(os.path.join(travaux[k]['etat'], f'fin_{k}'), 'w') as f:
                        f.write(str(p.returncode))
        time.sleep(0.5)
    res = []
    for k, (travail, p) in enumerate(zip(travaux, procs)):
        r = lire_json(travail['resultat'], {}) or {}
        r.update({'code': p.returncode, 'duree_s': durees[k], 'carte': k})
        if p.returncode:
            log(f'ÉTAPE {etape} (carte {k}) : arrêtée avec le code {p.returncode}')
        if k in prealables:
            nom, tp = avant[k]
            pp = prealables[k]['proc']
            r['depart_s'] = departs[k]
            r['avant'] = {**(lire_json(tp['resultat'], {}) or {}), 'code': pp.returncode,
                          'duree_s': prealables[k]['duree_s'], 'carte': k}
            if pp.returncode:
                log(f'ÉTAPE {nom} (carte {k}, avant {etape}) : arrêtée avec le code {pp.returncode}')
        res.append(r)
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


def lire_deux_canaux(f):
    """Son en flottant 32 bits, deux canaux (un son mono est doublé, au-delà de deux canaux les deux premiers)."""
    import numpy as np
    import soundfile as sf
    x = sf.read(f, dtype='float32', always_2d=True)[0]
    return np.ascontiguousarray(x[:, :2] if x.shape[1] >= 2 else np.repeat(x, 2, axis=1))


def etape_nettoyer(travail):
    """Voix principale et chœurs sans reverb, pour toutes les vidéos en chant (code du banc d'essai, etape_separer) :
    1. Roformer karaoké sur la voix isolée : piste « Vocals » = voix principale ; chœurs = voix - voix principale, en numpy
       (reverb et fuites comprises) ;
    2. Roformer dereverb sur les deux pistes : piste « noreverb » gardée ; la reverb d'origine est jetée (le mix remet
       la sienne).
    Chaque modèle n'est chargé qu'une fois. Entrée ramenée à un pic de 0,9 puis remise à l'échelle, comme la séparation.
    Sorties : <dossier>/lead_sec.wav et choeurs_sec.wav (44,1 kHz stéréo flottant, même longueur que la voix).
    Deux cartes (travail['taches'] de plan_nettoyage, travail['carte']) : l'étape ne fait que ses tâches, dans leur
    ordre ; un dereverb dont le karaoké revient à l'autre carte attend son témoin (<etat>/karaoke_<id>.ok, ou .erreur,
    ou la fin de cette carte) ; chaque tâche finie laisse un témoin (.ok, ou .erreur avec le numéro de la carte). Les
    niveaux d'une vidéo sont mesurés par la carte qui a fait ses deux dereverb, sinon par le noyau
    (nettoyer_sur_cartes). Sans tâches : toutes, dans l'ordre d'avant."""
    import numpy as np
    import soundfile as sf
    from audio_separator.separator import Separator
    torch = charger_torch()
    carte = travail.get('carte')
    tmp = '/tmp/net_sep' if carte is None else f'/tmp/net_sep{carte}'
    os.makedirs(tmp, exist_ok=True)
    chemin = travail['resultat']
    ov = int(travail.get('recouvrement') or 4)
    precision = travail.get('precision') if travail.get('precision') in PRECISIONS_ROFORMER else 'fp32'
    res = {'karaoke': None, 'dereverb': travail['dereverb'], 'recouvrement': ov, 'chargements_s': {}, 'videos': {}}
    if precision != 'fp32':
        res['precision'] = precision
    videos = {v['id']: v for v in travail['videos']}
    taches = (travail['taches'] if travail.get('taches') is not None
              else plan_nettoyage([(v['id'], 0.0) for v in travail['videos']], 1)[0])
    etat, auteurs = travail.get('etat'), travail.get('auteurs') or {}
    if carte is not None:
        res.update({'carte': carte, 'taches_faites': []})

    def separateur(nom, prec=precision):
        """Roformer nom chargé, dans la précision demandée (PRECISIONS_ROFORMER ; une option refusée : fp32)."""
        t0 = time.time()
        commun = dict(model_file_dir='/tmp/modeles_sep', output_dir=tmp, output_format='WAV', use_soundfile=True,
                      normalization_threshold=1.0, sample_rate=44100,
                      mdxc_params={'segment_size': 256, 'override_model_segment_size': False, 'batch_size': 1,
                                   'overlap': ov, 'pitch_shift': 0})
        try:
            s = Separator(**commun, **PRECISIONS_ROFORMER[prec])
        except TypeError as e:
            log(f'NETTOYAGE précision {prec} refusée ({e}) : fp32')
            s, prec = Separator(**commun), 'fp32'
        s.load_model(model_filename=nom)
        s.modele_doubler, s.precision_doubler = nom, prec
        res['chargements_s'][nom] = round(time.time() - t0, 1)
        log(f'NETTOYAGE modèle {nom} chargé en {time.time() - t0:.0f} s' + ('' if prec == 'fp32' else f' ({prec})')
            + ('' if carte is None else f' (carte {carte})'))
        return s

    def separer(s, x, motif, etiquette):
        """Pic ramené à 0,9, séparation, remise à l'échelle ; rend la piste dont le nom suit motif. Un son non fini en
        demi-précision (débordement) : refait en fp32, modèle rechargé pour cette fois."""
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
        if not np.isfinite(y).all() and getattr(s, 'precision_doubler', 'fp32') != 'fp32':
            log(f'NETTOYAGE {etiquette} : son non fini en {s.precision_doubler}, refait en fp32')
            res.setdefault('repli_fp32', []).append(etiquette)
            return separer(separateur(s.modele_doubler, 'fp32'), x, motif, etiquette)
        out = np.zeros_like(x)
        n = min(len(y), len(x))
        out[:n] = y[:n, :x.shape[1]] / g
        for f in glob.glob(f'{tmp}/*.wav'):
            os.remove(f)
        log(f'NETTOYAGE {etiquette} : {noms} -> {os.path.basename(choisis[0])} ({time.time() - t0:.1f} s)'
            + ('' if carte is None else f' (carte {carte})'))
        return out, {'pistes': noms, 'gardee': os.path.basename(choisis[0]), 'duree_s': round(time.time() - t0, 1)}

    def temoin(t, fin):
        if etat:
            with open(os.path.join(etat, f'{nom_tache(t)}.{fin}'), 'w') as f:
                f.write(str(carte or 0))

    karaoke_ici, secs = {}, {}   # karaoké fait ici (réussi ou non) ; pistes sèches faites ici, par vidéo
    paires_ici = {vid for vid in videos if all(['dereverb', vid, p] in taches for p in ('lead', 'choeurs'))}

    def attendre_karaoke(vid):
        """Karaoké de la vidéo prêt : fait ici, sinon témoin de l'autre carte ; False s'il a échoué, ou si la carte qui
        devait le faire s'est arrêtée sans lui (la reprise sur la carte 0 s'en chargera)."""
        if vid in karaoke_ici:
            return karaoke_ici[vid]
        if not etat:
            return False
        ok, ko = (os.path.join(etat, f"{nom_tache(['karaoke', vid])}.{fin}") for fin in ('ok', 'erreur'))
        auteur = auteurs.get(vid)
        attente, dit = time.time(), False
        while True:
            if os.path.exists(ok):
                return True
            if os.path.exists(ko) or auteur is None or auteur == carte:
                return False
            if os.path.exists(os.path.join(etat, f'fin_{auteur}')):
                return os.path.exists(ok)
            if not dit and time.time() - attente > 5:
                log(f'NETTOYAGE carte {carte} : attente du karaoké de {vid} (carte {auteur})')
                dit = True
            time.sleep(0.5)

    s, charge = None, None   # séparateur en mémoire, et son modèle : 'karaoke', 'dereverb' (None : aucun utilisable)
    tour = TourDeChargement(travail)
    for t in taches:
        v = videos[t[1]]
        r = res['videos'].setdefault(v['id'], {'durees_s': {}})
        t0 = time.time()
        if t[0] == 'karaoke':
            if charge != 'karaoke':
                s, charge = None, 'karaoke'
                with tour:
                    for nom in travail['karaoke']:
                        try:
                            s = separateur(nom)
                            res['karaoke'] = nom
                            break
                        except Exception as e:  # noqa: BLE001
                            log(f'NETTOYAGE {nom} : chargement impossible ({e!r}), modèle suivant')
                if s is None:
                    res['erreur'] = 'aucun modèle karaoké utilisable'
            if s is None:
                r['erreur'], karaoke_ici[v['id']] = res['erreur'], False
                temoin(t, 'erreur')
                ecrire_json(chemin, res)
                continue
            t0 = time.time()   # durée de la vidéo : sans le chargement du modèle, comme avant
            try:
                os.makedirs(v['dossier'], exist_ok=True)
                x = lire_deux_canaux(v['voix'])
                lead, info = separer(s, x, MOTIF_KARAOKE, f"{v['id']}_karaoke")
                sf.write(os.path.join(v['dossier'], 'lead.wav'), lead, 44100, subtype='FLOAT')
                sf.write(os.path.join(v['dossier'], 'choeurs.wav'), x - lead, 44100, subtype='FLOAT')
                r['durees_s']['karaoke'] = info['duree_s']
                r['karaoke'] = info
                karaoke_ici[v['id']] = True
                temoin(t, 'ok')
            except Exception as e:  # noqa: BLE001 — une vidéo ratée n'empêche pas les suivantes
                import traceback
                traceback.print_exc()
                r['erreur'], karaoke_ici[v['id']] = f'karaoké : {e!r}'[:400], False
                temoin(t, 'erreur')
        else:
            piste = t[2]
            if r.get('erreur'):
                continue   # karaoké ou voix principale ratés ici : vidéo en échec
            if charge != 'dereverb':   # chargé avant d'attendre le karaoké de l'autre carte
                s, charge = None, 'dereverb'
                torch.cuda.empty_cache()
                with tour:
                    try:
                        s = separateur(travail['dereverb'])
                    except Exception as e:  # noqa: BLE001
                        res['erreur'] = f"dereverb {travail['dereverb']} indisponible : {e!r}"[:400]
            if not attendre_karaoke(v['id']):
                continue   # karaoké raté, ou carte arrêtée sans lui (reprise sur la carte 0)
            t0 = time.time()
            if s is None:
                temoin(t, 'erreur')
                ecrire_json(chemin, res)
                continue
            try:
                d = v['dossier']
                nom = 'lead_sec' if piste == 'lead' else 'choeurs_sec'
                y, info = separer(s, lire_deux_canaux(os.path.join(d, f'{piste}.wav')), MOTIF_DEREVERB,
                                  f"{v['id']}_{piste}_dereverb")
                sf.write(os.path.join(d, f'{nom}.wav'), y, 44100, subtype='FLOAT')
                r[nom] = os.path.join(d, f'{nom}.wav')
                r['durees_s'][f'dereverb_{piste}'] = info['duree_s']
                r.setdefault('dereverb', {})[piste] = info
                if v['id'] in paires_ici:
                    secs.setdefault(v['id'], {})[nom] = y
                if len(secs.get(v['id']) or {}) == 2:   # les deux dereverb de la vidéo faits ici : niveaux, fichiers de travail effacés
                    x, lead, choeurs = (lire_deux_canaux(f) for f in (v['voix'], os.path.join(d, 'lead.wav'),
                                                                       os.path.join(d, 'choeurs.wav')))
                    r['niveaux_db'] = niveaux_nettoyage(x, lead, choeurs, secs[v['id']]['lead_sec'], secs[v['id']]['choeurs_sec'])
                    del secs[v['id']]
                    for f in ('lead.wav', 'choeurs.wav'):
                        os.remove(os.path.join(d, f))
                    log(f"NETTOYAGE {v['id']} : niveaux (dBFS, trames où la voix sonne) {json.dumps(r['niveaux_db'])}")
                temoin(t, 'ok')
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                r['erreur'] = f'dereverb : {e!r}'[:400]
                temoin(t, 'erreur')
        r['duree_s'] = round(r.get('duree_s', 0) + time.time() - t0, 1)
        if carte is not None:
            res['taches_faites'].append([nom_tache(t), round(time.time() - t0, 1)])
        ecrire_json(chemin, res)


def separer_videos(liste, cartes):
    """Séparation voix / reste de toutes les vidéos (SEPARER : un processus, un seul chargement du Roformer). Plusieurs
    vidéos et deux cartes : partagées entre elles (la plus lourde d'abord, sur la carte la moins chargée), un processus
    par carte, chacun dans son dossier de travail ; une carte arrêtée en échec : ses vidéos sont reprises sur la carte 0.
    Une vidéo, une carte, ou un modèle encore à télécharger : un seul processus, comme avant."""
    os.makedirs('/tmp/sep', exist_ok=True)
    with open('/tmp/separer.py', 'w') as f:
        f.write(SEPARER)
    if cartes <= 1 or len(liste) < 2 or not modeles_presents('vocals_mel_band_roformer.ckpt'):
        lancer(sys.executable, '/tmp/separer.py', json.dumps(liste), '/tmp/sep')
        return
    poids = {t['id']: os.path.getsize(t['entree']) for t in liste}
    charge, parts = [0] * cartes, [[] for _ in range(cartes)]
    for t in sorted(liste, key=lambda t: -poids[t['id']]):
        k = min(range(cartes), key=lambda c: charge[c])
        charge[k] += poids[t['id']]
        parts[k].append(t)
    log('SEPARATION sur deux cartes : ' + ' ; '.join(f"carte {k} : {', '.join(t['id'] for t in p)}" for k, p in enumerate(parts)))
    procs = []
    for k, part in enumerate(parts):
        if part:
            os.makedirs(f'/tmp/sep/carte{k}', exist_ok=True)
            charge = f'/tmp/sep/carte{k}/charge'
            if os.path.exists(charge):
                os.remove(charge)
            # la carte k charge le modèle après la carte k-1 (témoin « charge »)
            procs.append((k, part, subprocess.Popen([sys.executable, '/tmp/separer.py', json.dumps(part), f'/tmp/sep/carte{k}',
                                                     f'/tmp/sep/carte{k - 1}/charge' if k else '', charge],
                                                    env={**os.environ, **env_carte(k, cartes)})))
    for k, part, p in procs:
        if p.wait():
            log(f'SEPARATION carte {k} : arrêtée avec le code {p.returncode} ; ses vidéos reprises sur la carte 0')
            lancer(sys.executable, '/tmp/separer.py', json.dumps(part), '/tmp/sep')


def modeles_presents(*noms):
    """Les modèles audio-separator sont-ils déjà sur le disque (montage d'outils) ? Sinon, deux étapes les
    téléchargeraient en même temps au même endroit : elles se font alors sur une carte."""
    return all(os.path.exists(os.path.join('/tmp/modeles_sep', n)) for n in noms)


def nettoyer_sur_cartes(base, durees, cartes):
    """Nettoyage sur plusieurs cartes (plan_nettoyage, une étape par carte), reprise sur la carte 0 de ce qui n'a pas été
    fait (taches_a_reprendre), puis les niveaux des vidéos dont les deux dereverb se sont faits sur deux cartes.
    base : le travail d'une étape seule, sans resultat. Rend le résultat d'une étape seule (fusionner_nettoyage)."""
    t0 = time.time()
    etat = '/tmp/net/etat'
    shutil.rmtree(etat, ignore_errors=True)
    os.makedirs(etat)
    plan = plan_nettoyage(durees, cartes)
    auteurs = {t[1]: k for k, taches in enumerate(plan) for t in taches if t[0] == 'karaoke'}
    log(f'NETTOYAGE sur {cartes} cartes : ' + ' ; '.join(f"carte {k} : {', '.join(nom_tache(t) for t in taches) or 'rien'}"
                                                     for k, taches in enumerate(plan)))
    travaux = [{**base, 'taches': taches, 'etat': etat, 'auteurs': auteurs, 'carte': k, 'resultat': f'/tmp/net/resultat_{k}.json'}
               for k, taches in enumerate(plan)]
    try:
        parts = etapes_a_part('nettoyer', travaux, cartes)
    except Exception as ex:  # noqa: BLE001 — tout sera repris sur la carte 0
        import traceback
        traceback.print_exc()
        parts = []
    reste = taches_a_reprendre(plan_nettoyage(durees, 1)[0], etat)
    if reste:
        log(f"NETTOYAGE reprise sur la carte 0 : {', '.join(nom_tache(t) for t in reste)}")
        for t in reste:
            for fin in ('ok', 'erreur'):
                if os.path.exists(os.path.join(etat, f'{nom_tache(t)}.{fin}')):
                    os.remove(os.path.join(etat, f'{nom_tache(t)}.{fin}'))
        r = etape_a_part('nettoyer', {**base, 'taches': reste, 'etat': etat, 'auteurs': {v: 0 for v in auteurs}, 'carte': 0,
                                      'resultat': '/tmp/net/resultat_reprise.json'})
        r['carte'] = 0
        parts.append(r)
    res = fusionner_nettoyage(parts)
    for v in base['videos']:
        rv = res['videos'].get(v['id']) or {}
        if rv.get('erreur') or rv.get('niveaux_db') or not (rv.get('lead_sec') and rv.get('choeurs_sec')):
            continue
        try:
            d = v['dossier']
            x, lead, choeurs, lead_sec, ch_sec = (lire_deux_canaux(f) for f in (
                v['voix'], os.path.join(d, 'lead.wav'), os.path.join(d, 'choeurs.wav'), rv['lead_sec'], rv['choeurs_sec']))
            rv['niveaux_db'] = niveaux_nettoyage(x, lead, choeurs, lead_sec, ch_sec)
            for f in ('lead.wav', 'choeurs.wav'):
                os.remove(os.path.join(d, f))
            log(f"NETTOYAGE {v['id']} : niveaux (dBFS, trames où la voix sonne) {json.dumps(rv['niveaux_db'])}")
        except Exception as ex:  # noqa: BLE001 — comme sur une carte : vidéo en échec, voix isolée complète convertie
            import traceback
            traceback.print_exc()
            rv['erreur'] = f'dereverb : {ex!r}'[:400]
    res['duree_s'] = round(time.time() - t0, 1)
    return res


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
    mise au niveau (le noyau les applique), écrite en WAV flottant mono. Sans pistes : simple essai de chargement.
    Plusieurs voix cibles (travail['references'], la première étant la voix d'analyse) : le modèle est chargé une fois,
    chaque référence préparée une fois (mel, style CAMPPlus, invite du régulateur de longueur), et chaque morceau prend
    celle de sa voix (7e élément du morceau)."""
    import soundfile as sf
    chemin = travail['resultat']
    res = {'commit': YING_COMMIT, 'poids': os.path.basename(travail.get('checkpoint') or YING_POIDS[1]),
           'poids_origine': 'montage' if travail.get('checkpoint') else 'Hugging Face', 'pistes': {}}
    if tous_pris(travail):
        # carte arrivée après ses paroles, quand les autres ont déjà pris tous les morceaux : rien à charger
        res.update({'carte': travail.get('carte', 0), 'morceaux_faits': {}, 'morceaux_erreurs': {}, 'saute': True})
        log(f"YINGMUSIC carte {travail.get('carte', 0)} : tous les morceaux déjà pris par les autres cartes, modèle non chargé")
        ecrire_json(chemin, res)
        return
    try:
        with TourDeChargement(travail):   # plusieurs cartes : une à la fois (une carte : rien ne change)
            m = MoteurYing(travail.get('outils'), travail.get('checkpoint'))
            res.update({'installation': m.installation, 'chargement_s': m.chargement_s})
            if travail.get('references'):
                refs, res['references_s'] = [], []
                for c in travail['references']:
                    t0 = time.time()
                    refs.append(m.preparer_ref(c))
                    res['references_s'].append(round(time.time() - t0, 2))
                ref = refs[0]
            else:
                ref = m.preparer_ref(travail['reference']) if travail.get('reference') else None
                refs = [ref]
    except Exception as e:  # noqa: BLE001 — le noyau se repliera sur Seed-VC
        import traceback
        traceback.print_exc()
        res['erreur'] = f'YingMusic-SVC indisponible : {e!r}'[:500]
        ecrire_json(chemin, res)
        return
    if travail.get('taches') is not None:
        return ying_morceaux(travail, m, refs, res)
    log(f"YINGMUSIC chargé en {m.chargement_s:.0f} s (installation {m.installation['origine']}, poids {res['poids_origine']}) ; "
        f"{len(travail.get('pistes', []))} piste(s) à convertir")
    ecrire_json(chemin, res)
    for p in travail.get('pistes', []):
        t0 = time.time()
        try:
            x = lire_mono(p['entree'])
            couches = nouvelles_couches(p, len(x))
            y = assembler(x, p['morceaux'], travail['pad'], lambda xs, d, v=0: m.convertir_morceau(xs, refs[v], d, p['pas']),
                          REGLAGES['graine'], m.torch, couches)
            sf.write(p['sortie'], y, SR, subtype='FLOAT')
            ecrire_couches(p, couches)
            res['pistes'][p['cle']] = {'sortie': p['sortie'], 'conversion_s': round(time.time() - t0, 1), 'morceaux': len(p['morceaux']),
                                       **({'couches': p['couches']} if couches is not None else {})}
            log(f"YINGMUSIC {p['cle']} : {len(p['morceaux'])} morceaux en {time.time() - t0:.0f} s")
        except Exception as e:  # noqa: BLE001 — cette piste se repliera sur Seed-VC
            import traceback
            traceback.print_exc()
            res['pistes'][p['cle']] = {'erreur': repr(e)[:500]}
        m.torch.cuda.empty_cache()
        ecrire_json(chemin, res)


def ying_morceaux(travail, m, refs, res):
    """etape_ying sur une carte parmi plusieurs : les morceaux de travail['taches'] ([piste, morceau]), chacun avec sa
    graine, sa marge et sa référence comme dans assembler(), rendu tel quel dans <morceaux_dir>/<piste>_<morceau>.npy ;
    le noyau assemble ensuite chaque piste (ying_sur_cartes). Avec travail['reclamations'] (plusieurs cartes), toutes
    les cartes parcourent la même liste et chacune prend le premier morceau libre (fichier réservé à son nom, créé une
    seule fois) : la carte prête la première, ou la plus rapide, en fait davantage. res['morceaux_faits'] : durée de
    conversion de chacun ; res['morceaux_erreurs'] : erreur et carte de ceux qui ont échoué."""
    import numpy as np
    chemin, dossier, carte = travail['resultat'], travail['morceaux_dir'], travail.get('carte', 0)
    reclamations = travail.get('reclamations')
    os.makedirs(dossier, exist_ok=True)
    res.update({'carte': carte, 'morceaux_faits': {}, 'morceaux_erreurs': {}})
    log(f"YINGMUSIC carte {carte} : chargé en {m.chargement_s:.0f} s ; "
        + (f"morceaux pris parmi {len(travail['taches'])}" if reclamations else f"{len(travail['taches'])} morceau(x) à convertir"))
    ecrire_json(chemin, res)
    t_carte, x_par_piste = time.time(), {}
    for pi, mi in travail['taches']:
        p = travail['pistes'][pi]
        mo = p['morceaux'][mi]
        cle, t0 = f'{pi}_{mi}', time.time()
        if reclamations:
            try:
                os.close(os.open(os.path.join(reclamations, cle), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            except FileExistsError:
                continue   # pris par une autre carte
        try:
            if pi not in x_par_piste:
                x_par_piste[pi] = lire_mono(p['entree'])
            x = x_par_piste[pi]
            lo, hi = bornes_morceau(mo, travail['pad'], len(x))
            m.torch.manual_seed(REGLAGES['graine'] + mo[0])
            y = m.convertir_morceau(x[lo:hi], refs[mo[6] if len(mo) >= 7 else 0], mo[3], p['pas'])
            np.save(os.path.join(dossier, f'{cle}.npy'), y)
            res['morceaux_faits'][cle] = round(time.time() - t0, 2)
        except Exception as e:  # noqa: BLE001 — sa piste se repliera sur Seed-VC (après une reprise sur la carte 0)
            import traceback
            traceback.print_exc()
            res['morceaux_erreurs'][cle] = {'erreur': repr(e)[:500], 'carte': carte}
        ecrire_json(chemin, res)
    res['memoire_gpu_max_mo'] = memoire_gpu_max_mo(m.torch)
    m.torch.cuda.empty_cache()
    res['conversion_s'] = round(time.time() - t_carte, 1)
    log(f"YINGMUSIC carte {carte} : {len(res['morceaux_faits'])} morceau(x) en {time.time() - t_carte:.0f} s"
        + (f", {len(res['morceaux_erreurs'])} en échec" if res['morceaux_erreurs'] else '')
        + f" ; mémoire du GPU au plus fort {res['memoire_gpu_max_mo']} Mo")
    ecrire_json(chemin, res)


def ying_sur_cartes(travail, cartes, invite_s=25.0, avant=None):
    """YingMusic-SVC sur plusieurs cartes. L'installation se fait ici, une fois (deux étapes la feraient en même temps
    dans le même dossier) ; les morceaux de toutes les pistes sont répartis (repartir_morceaux), une étape par carte ; ce
    qui n'a pas été converti (carte arrêtée, erreur sur une autre carte que la 0) est repris sur la carte 0 ; enfin
    chaque piste est assemblée dans l'ordre de ses morceaux (poser_morceaux : la piste qu'aurait rendue une carte seule)
    et écrite comme etape_ying l'écrit. Rend le résultat d'une étape seule, plus cartes (morceaux, chargement et durée
    de chacune) ; conversion_s d'une piste : la somme de ses morceaux (temps de GPU).
    avant : (étape, travail) que la dernière carte fait d'abord (les paroles), pendant que les autres convertissent ; elle
    prend ensuite les morceaux encore libres. L'étape charge son modèle après YingMusic-SVC sur la carte 0 (même tour de
    chargement) ; son résultat revient dans « avant » (absent si rien n'a été lancé)."""
    import numpy as np
    import soundfile as sf
    try:
        installation = installer_ying(travail.get('outils'))
    except Exception as e:  # noqa: BLE001 — le noyau se repliera sur Seed-VC
        import traceback
        traceback.print_exc()
        return {'erreur': f'YingMusic-SVC indisponible : {e!r}'[:500]}
    pistes, dossier, etat, reclamations = travail['pistes'], '/tmp/ying/morceaux', '/tmp/ying/etat', '/tmp/ying/reclamations'
    for d in (dossier, etat, reclamations):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    ordre = ordre_morceaux(pistes, travail['pad'], invite_s)
    log(f'YINGMUSIC sur {cartes} cartes : {len(ordre)} morceau(x), pris par la première carte libre, le plus long d\'abord'
        + (f" ; la carte {cartes - 1} fait d'abord l'étape {avant[0]}, puis rejoint la conversion" if avant and ordre else ''))
    travaux = [{**travail, 'taches': ordre, 'morceaux_dir': dossier, 'reclamations': reclamations, 'etat': etat, 'carte': k,
                'resultat': f'/tmp/ying/resultat_{k}.json'} for k in range(cartes)] if ordre else []
    prealable = {cartes - 1: (avant[0], {**avant[1], 'etat': etat, 'carte': cartes - 1})} if avant and travaux else None
    parts = ((etapes_a_part('ying', travaux, cartes, avant=prealable) if prealable else etapes_a_part('ying', travaux, cartes))
             if travaux else [])
    faits, erreurs = {}, {}

    def recueillir(r):
        faits.update(r.get('morceaux_faits') or {})
        erreurs.update(r.get('morceaux_erreurs') or {})
        for c in faits:
            erreurs.pop(c, None)
    for r in parts:
        recueillir(r)
    reste = [[pi, mi] for pi, p in enumerate(pistes) for mi in range(len(p['morceaux']))
             if f'{pi}_{mi}' not in faits and (erreurs.get(f'{pi}_{mi}') or {}).get('carte') != 0]
    if reste and parts and not parts[0].get('erreur'):
        log(f'YINGMUSIC reprise sur la carte 0 : {len(reste)} morceau(x)')
        r = etape_a_part('ying', {**travail, 'taches': reste, 'morceaux_dir': dossier, 'carte': 0,
                                  'resultat': '/tmp/ying/resultat_reprise.json'})
        r['reprise'] = True
        parts.append(r)
        recueillir(r)
    premier = next((r for r in parts if not r.get('erreur')), parts[0] if parts else {})
    res = {k: premier.get(k) for k in ('commit', 'poids', 'poids_origine', 'references_s') if premier.get(k) is not None}
    res.update({'installation': installation, 'chargement_s': max([r.get('chargement_s') or 0.0 for r in parts] or [0.0]),
                'pistes': {}, 'cartes': [{'carte': r.get('carte'), 'reprise': bool(r.get('reprise')),
                                          'morceaux': len(r.get('morceaux_faits') or {}),
                                          'erreurs': len(r.get('morceaux_erreurs') or {}), 'chargement_s': r.get('chargement_s'),
                                          'conversion_s': r.get('conversion_s'), 'duree_s': r.get('duree_s'),
                                          'code': r.get('code'), 'erreur': r.get('erreur'),
                                          **{c: r[c] for c in ('depart_s', 'saute', 'memoire_gpu_max_mo') if c in r},
                                          **({'avant': avant[0]} if 'avant' in r else {})} for r in parts]})
    if prealable:
        res['avant'] = next((r['avant'] for r in parts if 'avant' in r), None)
    if parts and all(r.get('erreur') for r in parts):
        res['erreur'] = parts[0]['erreur']
    for pi, p in enumerate(pistes):
        cles = [f'{pi}_{mi}' for mi in range(len(p['morceaux']))]
        manque = [c for c in cles if c not in faits]
        if manque:
            res['pistes'][p['cle']] = {'erreur': (erreurs.get(manque[0]) or {}).get('erreur') or res.get('erreur')
                                       or f'{len(manque)} morceau(x) non converti(s)'}
            continue
        try:
            n = len(lire_mono(p['entree']))
            couches = nouvelles_couches(p, n)
            y = poser_morceaux(n, p['morceaux'], travail['pad'],
                               lambda k, mo, lo, hi: np.load(os.path.join(dossier, f'{pi}_{k}.npy')), couches)
            sf.write(p['sortie'], y, SR, subtype='FLOAT')
            ecrire_couches(p, couches)
            res['pistes'][p['cle']] = {'sortie': p['sortie'], 'conversion_s': round(sum(faits[c] for c in cles), 1),
                                       'morceaux': len(cles), **({'couches': p['couches']} if couches is not None else {})}
        except Exception as e:  # noqa: BLE001 — cette piste se repliera sur Seed-VC
            import traceback
            traceback.print_exc()
            res['pistes'][p['cle']] = {'erreur': repr(e)[:500]}
    shutil.rmtree(dossier, ignore_errors=True)
    return res


def depuis_hf(classe, sources, cache, demi=True, **options):
    """classe.from_pretrained sur la première source qui se charge (montage d'outils, puis Hugging Face par internet),
    en float16 (dtype=, ou torch_dtype= des transformers d'avant la 4.56). Une option refusée (TypeError ou ValueError
    qui la nomme : API changée) est retirée avant l'essai suivant. Rend (objet, source utilisée, erreurs)."""
    import torch
    erreurs = []
    essais = [{**options, 'dtype': torch.float16}, {**options, 'torch_dtype': torch.float16}, {'dtype': torch.float16},
              {'torch_dtype': torch.float16}, {}] if demi else [dict(options), {}]
    for src in [s for s in sources if s]:
        for k in essais:
            try:
                return classe.from_pretrained(src, **k, cache_dir=cache), src, erreurs
            except (TypeError, ValueError) as e:
                erreurs.append(f'{src} ({", ".join(k) or "sans option"}) : {e!r}'[:300])
                if not any(o in str(e) for o in [*k, 'keyword', 'argument']):
                    break   # pas une option refusée : source suivante
            except Exception as e:  # noqa: BLE001 — source suivante (montage incomplet : internet)
                erreurs.append(f'{src} : {e!r}'[:300])
                break
    raise RuntimeError('chargement impossible : ' + ' | '.join(erreurs))


def origine_modele(src, depot):
    """D'où vient un modèle chargé : Hugging Face, copie locale du montage, ou montage."""
    if not src or src == depot:
        return 'Hugging Face'
    return 'copie locale du montage' if str(src).startswith('/tmp/') else 'montage'


def alleger_whisper(modele):
    """Whisper sans les poids d'attention que le mot à mot fait garder pour rien (voir PAROLES_DEFAUT) : l'encodeur tourne
    sans rendre les siens (output_attentions retiré à l'entrée), et dans le décodeur ceux de l'auto-attention de chaque
    couche et de l'attention croisée des couches sans tête d'alignement sont remplacés, à la sortie du module, par un
    tableau vide. Les couches jetaient déjà ces poids : le calcul et le texte sont les mêmes, au bit près ; seuls les
    enregistreurs de transformers (output_attentions) ne les gardent plus. Rend ce qui est gardé, pour le rapport."""
    def sans_attentions(module, args, kwargs):
        if kwargs.get('output_attentions'):
            kwargs = {**kwargs, 'output_attentions': False}
        return args, kwargs

    def vider(module, args, sortie):
        if isinstance(sortie, tuple) and len(sortie) > 1 and hasattr(sortie[1], 'new_empty') and sortie[1].numel():
            return (sortie[0], sortie[1].new_empty((*sortie[1].shape[:-1], 0)), *sortie[2:])
        return None
    couches = list(modele.model.decoder.layers)
    tetes = getattr(modele.generation_config, 'alignment_heads', None) or []
    gardees = sorted({int(c) for c, _ in tetes}) if tetes else list(range(len(couches)))
    modele.model.encoder.register_forward_pre_hook(sans_attentions, with_kwargs=True)
    for i, couche in enumerate(couches):
        # en tête des crochets de sortie : avant ceux qui enregistrent les poids (posés par transformers au premier appel)
        couche.self_attn.register_forward_hook(vider, prepend=True)
        if i not in gardees:
            couche.encoder_attn.register_forward_hook(vider, prepend=True)
    return {'attention_croisee_gardee': f'{len(gardees)} couches sur {len(couches)}', 'encodeur': 'sans attentions'}


def attendre_memoire(torch, besoin_mo, attente_max_s, pas_s=1.0):
    """Mémoire libre de la carte avant de charger Whisper : si elle manque (une étape qui vient de finir n'a pas encore
    rendu la sienne), attend qu'elle revienne, au plus attente_max_s. Rend (libre, totale) en Mo et la durée d'attente ;
    (None, None, 0.0) si CUDA ne la donne pas."""
    t0 = time.time()
    while True:
        try:
            libre, totale = (int(v) for v in torch.cuda.mem_get_info())
        except Exception:  # noqa: BLE001
            return None, None, 0.0
        attente = time.time() - t0
        if libre >= besoin_mo * 2 ** 20 or attente >= attente_max_s:
            return round(libre / 2 ** 20), round(totale / 2 ** 20), round(attente, 1)
        time.sleep(pas_s)


def etape_paroles(travail):
    """Paroles de toutes les vidéos, dans un processus à part (le GPU est rendu à la sortie) : Whisper large-v3 chargé une
    fois, langue détectée phrase par phrase, transcription par morceaux de phrases de même langue (au plus 25 s, hors chant
    rendu muet) avec horodatage par mot et garde-fous, puis NLLB-200 (chargé une fois) traduit vers le français les lignes
    qui ne sont pas en français. Écrit paroles.json pour chaque vidéo ; rend les mesures et les temps dans le résultat.
    Mémoire du GPU : Whisper allégé (alleger_whisper : mêmes paroles, 2,7 Gio de moins et 13 Mo de moins par jeton),
    allocateur à segments extensibles, mémoire libre attendue avant le chargement (attendre_memoire) ; un morceau qui en
    manque encore est coupé en deux (transcrire_video) au lieu d'être repassé tel quel."""
    import numpy as np
    # avant le premier appel à CUDA de ce processus (l'allocateur lit ce réglage en démarrant) : moins de mémoire perdue
    # entre les blocs ; les calculs ne changent pas, seulement la façon de réserver la mémoire
    alloc = os.environ.get('PYTORCH_CUDA_ALLOC_CONF') or ''
    if 'expandable_segments' not in alloc:
        os.environ['PYTORCH_CUDA_ALLOC_CONF'] = ','.join(filter(None, [alloc, 'expandable_segments:True']))
    torch = charger_torch()
    import gc as ramasse
    import torchaudio
    import transformers
    from transformers import WhisperForConditionalGeneration, WhisperProcessor, pipeline
    chemin, cache = travail['resultat'], travail.get('cache') or '/tmp/hf_paroles'
    res = {'implementation': f'transformers {transformers.__version__} (pipeline, horodatage par mot)',
           'modele': WHISPER[1], 'traducteur': TRADUCTEUR[1], 'chargements_s': {}, 'videos': {}, 'temps': {}}
    if travail.get('etat') or travail.get('carte') is not None:
        res['carte'] = travail.get('carte')
    libre, totale, attente = attendre_memoire(torch, PAROLES_DEFAUT['memoire_min_mo'], PAROLES_DEFAUT['attente_memoire_max_s'])
    res['memoire'] = {'libre_mo': libre, 'totale_mo': totale, 'allocateur': os.environ['PYTORCH_CUDA_ALLOC_CONF']}
    if attente:
        res['memoire']['attente_s'] = attente
        log(f'PAROLES {attente:.0f} s d\'attente de la mémoire du GPU : {libre} Mo libres sur {totale}')
    t0 = time.time()
    try:
        # sur une carte parmi plusieurs, pendant la conversion : Whisper se charge après YingMusic-SVC sur la carte 0
        # (tour de chargement commun) ; une carte seule : rien ne change
        with TourDeChargement(travail):
            if travail.get('etat'):
                res['attente_chargement_s'] = round(time.time() - t0, 1)
                t0 = time.time()
            proc, src_w, err = depuis_hf(WhisperProcessor, [travail.get('whisper'), WHISPER[0]], cache, demi=False)
            # attention « eager » : les horodatages par mot lisent les poids d'attention croisée (sdpa ne les rend pas)
            modele, src_w, err2 = depuis_hf(WhisperForConditionalGeneration, [src_w, WHISPER[0]], cache,
                                            attn_implementation='eager')
            modele = modele.to('cuda').eval()
        try:
            res['memoire']['allegement'] = alleger_whisper(modele)
        except Exception as e:  # noqa: BLE001 — transformers changé : Whisper tel quel, les morceaux trop gourmands coupés
            res['memoire']['allegement'] = {'erreur': repr(e)[:300]}
        pipe = pipeline('automatic-speech-recognition', model=modele, tokenizer=proc.tokenizer,
                        feature_extractor=proc.feature_extractor, device=modele.device)
        res.update({'whisper_origine': origine_modele(src_w, WHISPER[0]), 'erreurs_chargement': err + err2})
    except Exception as e:  # noqa: BLE001 — pas de paroles, le doublage continue
        import traceback
        traceback.print_exc()
        res['erreur'] = f'Whisper indisponible : {e!r}'[:500]
        ecrire_json(chemin, res)
        return
    res['chargements_s']['whisper'] = round(time.time() - t0, 1)
    m = res['memoire']
    log(f"PAROLES Whisper large-v3 chargé en {time.time() - t0:.0f} s ({res['whisper_origine']}) ; "
        f"{res['implementation']} ; mémoire du GPU libre au départ {m.get('libre_mo')} Mo sur {m.get('totale_mo')}, "
        f"allègement {m.get('allegement')}, {m.get('allocateur')}")
    ecrire_json(chemin, res)
    gc = modele.generation_config
    l2i = dict(getattr(gc, 'lang_to_id', None) or {})

    @torch.no_grad()
    def langue(audio):
        feats = proc.feature_extractor(audio, sampling_rate=16000, return_tensors='pt').input_features
        feats = feats.to(modele.device, modele.dtype)
        dec = torch.tensor([[gc.decoder_start_token_id]], device=modele.device)
        logits = modele(input_features=feats, decoder_input_ids=dec).logits[0, -1].float()
        codes, ids = list(l2i), torch.tensor(list(l2i.values()), device=logits.device)
        p = torch.softmax(logits[ids], -1)
        ordre = torch.argsort(p, descending=True)[:3].tolist()
        return (codes[ordre[0]].strip('<|>'), float(p[ordre[0]]),
                [[codes[j].strip('<|>'), round(float(p[j]), 3)] for j in ordre])

    @torch.no_grad()
    def noter(audio, lg, texte):
        """Log-probabilité moyenne par jeton du texte sous Whisper, langue imposée (décodage forcé : un seul passage du
        décodeur, sans génération) ; None si le texte n'a pas de jeton."""
        tok = proc.tokenizer
        # jetons de tête construits ici : set_prefix_tokens changerait le tokeniseur partagé avec le pipeline
        prefixe = tok.convert_tokens_to_ids(['<|startoftranscript|>', f'<|{lg}|>', '<|transcribe|>', '<|notimestamps|>'])
        if tok.unk_token_id is not None and tok.unk_token_id in prefixe:
            return None   # langue inconnue du tokeniseur : pas de note, la langue retenue reste
        ids = tok.encode(texte.strip(), add_special_tokens=False)[:200]
        if not ids:
            return None
        feats = proc.feature_extractor(audio, sampling_rate=16000, return_tensors='pt').input_features
        feats = feats.to(modele.device, modele.dtype)
        dec = torch.tensor([prefixe + ids], device=modele.device)
        logits = modele(input_features=feats, decoder_input_ids=dec).logits[0].float()
        lp = torch.log_softmax(logits[len(prefixe) - 1:-1], -1)
        cible = torch.tensor(ids, device=lp.device)
        return float(lp.gather(1, cible[:, None]).mean())

    pics, pic_reserve = [], [0]   # mémoire du GPU au plus fort de chaque passe réussie (Mo) ; réservée, sur l'étape

    def pic_a_zero():
        try:
            pic_reserve[0] = max(pic_reserve[0], int(torch.cuda.max_memory_reserved()))
            torch.cuda.reset_peak_memory_stats()
        except Exception:  # noqa: BLE001
            pass

    def passe(audio, ts, gk):
        """Une passe du pipeline, sa mémoire au plus fort notée dans pics."""
        pic_a_zero()
        sortie = pipe(audio.copy(), return_timestamps=ts, generate_kwargs=gk)
        try:
            pics.append(round(torch.cuda.max_memory_allocated() / 2 ** 20))
        except Exception:  # noqa: BLE001
            pass
        return sortie

    def liberer():
        ramasse.collect()
        torch.cuda.empty_cache()

    def transcrire(audio, lg, mots=True):
        return transcrire_avec_repli(lambda ts, gk: passe(audio, ts, gk), lg, mots, liberer)

    def lot_nllb(lg, lignes):
        """Traduction vers le français d'un lot de lignes d'une langue (NLLB-200) ; None si la langue manque à NLLB."""
        code = NLLB.get(lg)
        if not code:
            return None
        if code not in toks:
            toks[code] = AutoTokenizer.from_pretrained(src_t, src_lang=code, cache_dir=cache)
        tok = toks[code]
        enc = tok(lignes, return_tensors='pt', padding=True, truncation=True, max_length=200).to(mt.device)
        with torch.no_grad():
            out = mt.generate(**enc, forced_bos_token_id=tok.convert_tokens_to_ids('fra_Latn'), max_new_tokens=160,
                              num_beams=4)
        return tok.batch_decode(out, skip_special_tokens=True)

    def seize_khz(chemin_audio):
        x = lire_mono(chemin_audio)
        return torchaudio.functional.resample(torch.from_numpy(x).float(), SR, 16000).numpy().astype(np.float32)

    textes = {}
    for v in travail['videos']:
        t1 = time.time()
        r = res['videos'][v['id']] = {}
        pics.clear()
        try:
            textes[v['id']] = transcrire_video(v, seize_khz(v['audio']), langue, transcrire, r,
                                               noter if travail.get('bilingue', True) else None,
                                               {'bilingue': bool(travail.get('bilingue', True))})
            if pics:
                r['memoire_max_mo'], r['memoire_passes_mo'] = max(pics), list(pics)
            log(f"PAROLES {v['id']} : {r['lignes']} lignes, langue {r['langue']} {r['langues_s']}, modes {r['modes']}, "
                f"retraits {r['retraits']}, en {time.time() - t1:.0f} s ; mémoire du GPU au plus fort "
                f"{r.get('memoire_max_mo')} Mo" + (f", {r['coupes_memoire']} morceau(x) coupé(s) faute de mémoire"
                                                   if r.get('coupes_memoire') else ''))
        except Exception as e:  # noqa: BLE001 — une vidéo ratée n'empêche pas les suivantes
            import traceback
            traceback.print_exc()
            r['erreur'] = f'transcription : {e!r}'[:500]
        ecrire_json(chemin, res)
    res['temps']['transcription_s'] = round(time.time() - t0 - res['chargements_s']['whisper'], 1)
    pic_a_zero()
    pipe = modele = None   # mémoire du GPU rendue avant le traducteur
    ramasse.collect()
    torch.cuda.empty_cache()

    # Traduction vers le français : seulement les lignes qui ne sont pas en français
    traductions, toujours = None, bool(travail.get('traduire_toujours'))
    if any(l['langue'] != 'fr' or toujours for lignes in textes.values() for l in lignes):
        t2 = time.time()
        try:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
            mt, src_t, err = depuis_hf(AutoModelForSeq2SeqLM, [travail.get('traducteur'), TRADUCTEUR[0]], cache)
            res['erreurs_chargement_traducteur'] = err
            mt, toks = mt.to('cuda').eval(), {}
            res['traducteur_origine'] = origine_modele(src_t, TRADUCTEUR[0])
            res['chargements_s']['traducteur'] = round(time.time() - t2, 1)
            log(f"PAROLES traducteur {TRADUCTEUR[1]} chargé en {time.time() - t2:.0f} s ({res['traducteur_origine']})")
            t3 = time.time()
            traductions, info = traduire_lignes(textes, lot_nllb, toujours)
            res.update(info)
            res['temps']['traduction_s'] = round(time.time() - t3, 1)
            mt = None
            torch.cuda.empty_cache()
        except Exception as e:  # noqa: BLE001 — les paroles restent, sans traduction
            import traceback
            traceback.print_exc()
            res['erreur_traduction'] = f'traduction : {e!r}'[:500]
            traductions = None
    for v in travail['videos']:
        if v['id'] not in textes:
            continue
        lignes = textes[v['id']]
        traduites = traductions is not None and (any(l['langue'] != 'fr' for l in lignes) or toujours)
        d = paroles_json(lignes, traductions[v['id']] if traduites else None, TRADUCTEUR[1], v.get('piste'))
        ecrire_json(v['sortie'], d)
        res['videos'][v['id']].update({'fichier': os.path.basename(v['sortie']), 'traduction': bool(d['traductions'])})
    fin = memoire_gpu_max_mo(torch)   # depuis la dernière remise à zéro : le traducteur
    res['memoire_gpu_max_mo'] = None if fin is None else max(fin, round(pic_reserve[0] / 2 ** 20))
    res['temps']['total_s'] = round(time.time() - t0, 1)
    ecrire_json(chemin, res)


def transcrire_video(v, x16, detecter, transcrire_morceau, r, noter=None, reglages=None):
    """Paroles d'une vidéo (sans torch : les modèles sont passés en fonctions). v : {phrases, chant, piste} ; x16 : sa
    piste à 16 kHz ; detecter(audio) -> (langue, probabilité) ; transcrire_morceau(audio, langue) -> (sortie du pipeline
    {text, chunks: [{text, timestamp}]}, mode, erreurs), mode 'mémoire' si le mot à mot a manqué de mémoire GPU : le
    morceau est alors coupé en deux (couper_morceau), ou, trop court, redemandé sans horodatage par mot
    (transcrire_morceau(audio, langue, False)). Langue de chaque phrase, morceaux de même langue (hors des phrases :
    muet), mots horodatés, garde-fous, lignes. Remplit r (mesures) et rend les lignes. noter(audio, langue, texte) ->
    log-probabilité moyenne par jeton : avec lui (et bilingue), les phrases à la langue douteuse (phrases_ambigues) sont
    transcrites dans leurs deux langues et gardent la plus sûre (departager_langue) avant les morceaux ; sans lui, rien
    ne change."""
    import numpy as np
    R = {**PAROLES_DEFAUT, **(reglages or {})}
    t1 = time.time()
    r.update({'piste': v.get('piste'), 'phrases': len(v['phrases']),
              'secondes_transcrites': round(sum(b - a for a, b in v['phrases']), 1), 'modes': {},
              'retraits': {'hors_chant': 0, 'boucles': 0, 'lignes': 0}})
    langues, tops = [], []
    for a, b in v['phrases']:
        seg = x16[int(a * 16000):int(b * 16000)]
        d = detecter(seg) if len(seg) >= 1600 else (None, 0.0)
        langues.append((d[0], d[1], b - a))
        tops.append(d[2] if len(d) > 2 else None)   # les trois langues les plus probables, pour mémoire
    choisies = lisser_langues(langues)
    if noter is not None and R['bilingue']:
        t2 = time.time()
        essais = phrases_ambigues(v['phrases'], langues, choisies, tops, R)

        def transcrire_phrase(audio, lg):
            sortie, _, _ = transcrire_morceau(audio, lg, False)
            return (sortie or {}).get('text') or ''
        details = []
        for i, lg, alt, doute in essais:
            a, b = v['phrases'][i]
            seg = x16[int(a * 16000):int(b * 16000)]
            gagnante, n1, n2, _ = departager_langue(seg, lg, alt, transcrire_phrase, noter, R)
            choisies[i] = gagnante
            details.append([a, b, lg, alt, gagnante, round(n1, 3) if n1 > float('-inf') else None,
                            round(n2, 3) if n2 > float('-inf') else None])
        r['bilingue'] = {'essayees': len(essais), 'changees': sum(1 for d in details if d[4] != d[2]),
                         'secondes': round(sum(d[1] - d[0] for d in details), 1), 'duree_s': round(time.time() - t2, 1),
                         'details': details}
    r['langues_phrases'] = [[a, b, c, round(p, 2), lg] + ([top] if top else [])
                            for (a, b), (c, p, _), lg, top in zip(v['phrases'], langues, choisies, tops)]
    r['temps_langue_s'] = round(time.time() - t1, 1)
    mots, file = [], list(grouper_morceaux(v['phrases'], choisies))
    while file:
        a, b, lg, idx, *sans_mots = file.pop(0)
        lo, hi = int(a * 16000), int(b * 16000)
        audio = np.zeros(hi - lo, np.float32)
        for i in idx:   # hors des phrases (bruits, fuites de musique) : muet, avec fondus de 20 ms
            pa = max(int(v['phrases'][i][0] * 16000), lo)
            pb = min(int(v['phrases'][i][1] * 16000), hi, len(x16))
            if pb <= pa:
                continue
            w = np.ones(pb - pa, np.float32)
            f = min(320, len(w) // 2)
            if f:
                w[:f], w[-f:] = np.linspace(0, 1, f), np.linspace(1, 0, f)
            audio[pa - lo:pb - lo] = x16[pa:pb] * w
        sortie, mode, erreurs = transcrire_morceau(audio, lg, False) if sans_mots else transcrire_morceau(audio, lg)
        if erreurs:
            r.setdefault('erreurs', []).extend(erreurs[:2])
        if mode == 'mémoire' and not sans_mots:
            # à court de mémoire GPU en mot à mot (la même passe y manquerait encore) : deux morceaux plus courts, ou,
            # trop court pour être coupé, sans horodatage par mot
            moities = couper_morceau((a, b, lg, idx), v['phrases'], x16)
            if moities:
                r['coupes_memoire'] = r.get('coupes_memoire', 0) + 1
            file[0:0] = moities or [(a, b, lg, idx, True)]
            continue
        r['modes'][mode or 'échec'] = r['modes'].get(mode or 'échec', 0) + 1
        if not sortie:
            continue
        bruts = []
        for c in sortie.get('chunks') or []:
            d, f = (list(c.get('timestamp') or (None, None)) + [None, None])[:2]
            if d is None or not isinstance(c.get('text'), str):
                continue
            f = f if f is not None and f >= d else min(d + 0.5, max(b - a, d))
            if mode.startswith('mots'):
                bruts.append({'debut': a + d, 'fin': a + f, 'mot': c['text'].strip(), 'brut': c['text']})
            else:
                bruts += [{**m, 'debut': a + m['debut'], 'fin': a + m['fin']} for m in mots_proportionnels(c['text'], d, f)]
        for m in bruts:
            m['langue'] = lg
        garde, compte = nettoyer_mots(bruts, v['chant'], sortie.get('text') or '')
        for cle, n in compte.items():
            r['retraits'][cle] += n
        mots += garde
    lignes, r['retraits']['lignes'] = filtrer_lignes(lignes_de_mots(sorted(mots, key=lambda m: m['debut'])))
    r.update({'lignes': len(lignes), 'mots': sum(len(l['mots']) for l in lignes), 'langue': langue_principale(lignes),
              'langues_s': {}, 'transcription_s': round(time.time() - t1, 1)})
    for l in lignes:
        r['langues_s'][l['langue']] = round(r['langues_s'].get(l['langue'], 0) + l['fin'] - l['debut'], 1)
    return lignes


def traduire_lignes(textes, traduire_lot, toujours=False, taille_lot=16):
    """Traduction vers le français, ligne par ligne, de toutes les vidéos ({id: lignes}) : les lignes en français sont
    gardées telles quelles (sauf toujours), les autres traduites par lots d'une même langue (traduire_lot(langue,
    [textes]) -> [traductions], ou None si la langue est inconnue du traducteur : la ligne reste dans sa langue). Rend
    ({id: [texte de chaque ligne]}, mesures)."""
    places = {}
    for vid, lignes in textes.items():
        for j, l in enumerate(lignes):
            if l['langue'] != 'fr' or toujours:
                places.setdefault(l['langue'], []).append((vid, j))
    traductions = {vid: [l['texte'] for l in lignes] for vid, lignes in textes.items()}
    info = {'lignes_traduites': 0}
    for lg, liste in places.items():
        for d in range(0, len(liste), taille_lot):
            lot = liste[d:d + taille_lot]
            sortie = traduire_lot(lg, [textes[vid][j]['texte'] for vid, j in lot])
            if sortie is None:
                info.setdefault('langues_sans_traduction', []).append(lg)
                break
            for (vid, j), t in zip(lot, sortie):
                if isinstance(t, str) and t.strip():
                    traductions[vid][j] = t.strip()
                    info['lignes_traduites'] += 1
    return traductions, info


def etape_isolee(etape, chemin):
    """Point d'entrée d'une étape à part (python doubler_complet.py <étape> <travail.json>)."""
    if os.path.isdir(SOUCHES) and SOUCHES not in sys.path:
        sys.path.insert(0, SOUCHES)
    {'nettoyer': etape_nettoyer, 'ying': etape_ying, 'paroles': etape_paroles}[etape](lire_json(chemin))


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
    # modèles de paroles copiés sur le disque local pendant tout le reste (lus en mmap sur le montage : très lent)
    copie_paroles = copier_paroles(outils) if R.get('paroles', True) else {}
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
    # Cartes GPU : la machine NvidiaTeslaT4 de Kaggle en a deux ; le nettoyage et YingMusic-SVC (et la séparation d'un
    # lot) s'y partagent le travail, sans changer le résultat. Une seule carte : la chaîne d'avant (réglage gpus)
    visibles, noms_cartes = compter_cartes(torch)
    cartes, repli_cartes = cartes_a_utiliser(R.get('gpus'), visibles)
    session['gpus'] = {'visibles': visibles, 'noms': noms_cartes, 'demande': R.get('gpus', 'auto'), 'utilisees': cartes,
                       'repli': repli_cartes}
    log(f"GPU : {visibles} carte(s) ({', '.join(noms_cartes) or '?'}), {cartes} utilisée(s)"
        + (f' ; {repli_cartes}' if repli_cartes else ''))

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
    cibles = lire_cibles(R)   # plusieurs voix cibles, une par chanteur (None : une seule voix, comme avant)
    if cibles:
        for v in cibles['voix']:
            v['chemin'] = ref_chemin if v['fichier'] == R['voix']['fichier'] else trouver(v['fichier'])
    # voix à comparer : le même extrait dans chacune, en une session (jamais avec une voix par chanteur)
    comparees = None if cibles else lire_comparees(R)
    if comparees:
        for v in comparees:
            v['chemin'] = ref_chemin if v['fichier'] == R['voix']['fichier'] else trouver(v['fichier'])
    t = time.time()
    sep_reglage = reglage_separation(R)
    session['separation'] = {k: sep_reglage[k] for k in ('precision', 'recouvrement')}
    separer_videos([{'id': tr['id'], 'entree': tr['chemin'], 'voix': tr['voix'], 'reste': tr['reste'],
                     'gain_entree': float(tr.get('gain_entree') or 1.0), 'voix_flac': tr['voix_flac'],
                     'separation': sep_reglage} for tr in travaux], cartes)
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
    def preparer_ref(md, chemin=None):
        """Ce qui ne dépend que de la référence (une fois par voix et par modèle) : mel, style CAMPPlus, invite du
        régulateur de longueur, hauteur médiane, empreinte."""
        import librosa
        ref = librosa.load(chemin or ref_chemin, sr=md['sr'])[0][:md['sr'] * 25]
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
    # Plusieurs voix cibles : la référence de chaque voix préparée une fois par modèle (refs_cibles[modèle][indice]),
    # la première étant celle de l'analyse ; centre de chant de chaque voix (même règle que la voix seule)
    refs_cibles = {}
    if cibles:
        t = time.time()
        refs_cibles['chant44k'] = [refs['chant44k'] if v['chemin'] == ref_chemin else preparer_ref(chant, v['chemin'])
                                   for v in cibles['voix']]
        session['voix_cibles'] = {'mode': cibles['mode'], 'voix': []}
        for v, rv in zip(cibles['voix'], refs_cibles['chant44k']):
            f0_v = float(v.get('f0_parole_hz') or rv['f0_mediane'])
            v['centre_hz'] = float(v.get('facteur_chant') or R['voix']['facteur_chant']) * f0_v
            v['f0_parole'] = f0_v
            session['voix_cibles']['voix'].append({k: v.get(k) for k in ('cle', 'nom', 'affichage', 'fichier', 'f0_parole_hz')}
                                                  | {'f0_rmvpe_reference_hz': round(rv['f0_mediane'], 1),
                                                     'duree_reference_s': round(rv['duree'], 2),
                                                     'centre_chant_hz': round(v['centre_hz'], 1)})
            log(f"voix cible {v.get('cle')} : {v['nom']}, référence {rv['duree']:.2f} s, parole {f0_v:.0f} Hz, centre de "
                f"chant {v['centre_hz']:.0f} Hz")
        session['temps']['references_cibles_s'] = round(time.time() - t, 1)
    # Voix à comparer : la référence de chaque voix préparée une fois (refs_comp[modèle][indice]) ; chaque voix a son
    # contexte (centre de chant, hauteur de parole, longueur de la référence), car la décision d'octave et la taille des
    # morceaux en dépendent. Une seule voix : un contexte, celui d'avant.
    refs_comp = {}
    contextes = [{'j': None, 'nom': R['voix']['nom'], 'centre': centre, 'f0_parole': f0_parole, 'zone': R['voix'].get('zone'),
                  'mel_len': refs['chant44k']['mel2'].size(2), 'session_voix': session['voix'], 'emb': ref_emb}]
    if comparees:
        t = time.time()
        refs_comp['chant44k'] = [refs['chant44k'] if v['chemin'] == ref_chemin else preparer_ref(chant, v['chemin'])
                                 for v in comparees]
        session['comparer'] = {'voix': []}
        contextes = []
        for j, (v, rv) in enumerate(zip(comparees, refs_comp['chant44k'])):
            f0_v = float(v.get('f0_parole_hz') or rv['f0_mediane'])
            centre_v = float(v.get('facteur_chant') or R['voix']['facteur_chant']) * f0_v
            fiche_v = {k: v.get(k) for k in ('nom', 'fichier', 'f0_parole_hz', 'facteur_chant', 'affichage')} | {
                'f0_rmvpe_reference_hz': round(rv['f0_mediane'], 1), 'duree_reference_s': round(rv['duree'], 2),
                'centre_chant_hz': round(centre_v, 1)}
            session['comparer']['voix'].append(fiche_v)
            contextes.append({'j': j, 'nom': v['nom'], 'centre': centre_v, 'f0_parole': f0_v, 'zone': v.get('zone'),
                              'mel_len': rv['mel2'].size(2), 'session_voix': fiche_v, 'emb': rv['emb']})
            log(f"voix à comparer {j + 1}/{len(comparees)} : {v['nom']}, référence {rv['duree']:.2f} s, parole {f0_v:.0f} Hz, "
                f"centre de chant {centre_v:.0f} Hz")
        session['temps']['references_cibles_s'] = round(time.time() - t, 1)
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
    # deux versions de chaque passage (une voix par chanteur) : absent (ancien doubler.py) ou non : rien ne change
    versions_mode = R.get('versions_duo') if R.get('versions_duo') in VERSIONS_DUO else 'non'
    pad, fondu = int(R['pad_s'] * SR), int(R['fondu_s'] * SR)

    memo_analyse = {}   # par vidéo : ce que l'analyse ne tire pas de la voix cible (voix à comparer : faite une fois)

    def analyse_commune(tr):
        """Voix isolée, hauteur RMVPE, enveloppe, mesures de la musique et mode : rien n'y dépend de la voix cible."""
        id_video = tr.get('id_video', tr['id'])
        if id_video in memo_analyse:
            return memo_analyse[id_video]
        c = {'temps': {}}
        dur_sep = tr['voix'] + '.duree'
        if os.path.exists(dur_sep):
            with open(dur_sep) as f:
                c['temps']['separation_s'] = float(f.read())
            os.remove(dur_sep)
        c['transport'] = {'entree': os.path.basename(tr['chemin']), 'gain_entree': float(tr.get('gain_entree') or 1.0)}
        if tr.get('voix_flac') and os.path.exists(tr['voix_flac'] + '.json'):
            with open(tr['voix_flac'] + '.json') as f:
                c['transport']['voix_source_flac'] = json.load(f)
        voix2 = sf.read(tr['voix'], dtype='float32', always_2d=True)[0]
        reste2 = sf.read(tr['reste'], dtype='float32', always_2d=True)[0]
        x = voix2.mean(1).astype(np.float32)
        reste = reste2.mean(1).astype(np.float32)
        del voix2, reste2
        n = len(x)
        # Analyse : hauteur RMVPE sur toute la voix, découpage aux silences, empreinte par morceau
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
        os.remove(tr['reste'])
        c.update(x=x, n=n, f0_tout=f0_tout, env=env, lisse=lisse, seuil=seuil, m=m, mode=mode, raisons=raisons)
        memo_analyse[id_video] = c
        return c

    def analyser(tr, ctx=None):
        """Mode, découpage aux silences et décalage de chaque morceau, sur la voix isolée complète : la détection du mode
        et la règle d'octave ont été calibrées sur elle, le nettoyage ne change donc pas leurs entrées. ctx : la voix
        cible (centre de chant, hauteur de parole, référence) ; voix à comparer : une analyse par voix, sur la même voix
        isolée."""
        ctx = ctx or contextes[0]
        debut_tr = time.time()
        rapport = {'id': tr['id'], 'reglages': {k: v for k, v in R.items() if k != 'travaux'}, 'travail': tr,
                   'voix_cible': ctx['session_voix'], 'temps': {}}
        t = time.time()
        c = analyse_commune(tr)
        rapport['temps'].update(c['temps'])
        rapport['transport'] = c['transport']
        x, n, f0_tout, env, lisse, seuil, m, mode, raisons = (c[k] for k in (
            'x', 'n', 'f0_tout', 'env', 'lisse', 'seuil', 'm', 'mode', 'raisons'))
        rapport.update({'mesures_musique': m, 'mode': mode, 'mode_raisons': raisons, 'mode_force': tr['mode'] in ('chant', 'parole')})
        log(f"[{tr['id']}] MODE {mode} ({'imposé' if rapport['mode_force'] else 'détecté'}) : {json.dumps(m, ensure_ascii=False)}")
        # plusieurs voix cibles : en chant (une par chanteur) ; en parole seulement avec personne1:…, personne2:… (qui parle
        # quand est repéré) ; avec chanteurN en parole : la première voix seule, comme avant
        personnes_parole = bool(cibles) and mode != 'chant' and cibles['mode'] == 'personnes'
        plusieurs = bool(cibles) and (mode == 'chant' or personnes_parole)
        # un morceau doit tenir, avec la référence, dans la fenêtre de 30 s du modèle : la plus longue des références
        place = SR // 512 * 30 - (max(r['mel2'].size(2) for r in refs_cibles['chant44k']) if plusieurs
                                  else ctx['mel_len'])
        coeur_max = place * 512 - 2 * pad - int(0.1 * SR)
        coupes = decouper(lisse, n, coeur_max, min(int(2 * SR), coeur_max // 2), seuil)
        diar = None
        if personnes_parole:   # qui parle quand : les changements de personne en pleine parole deviennent des coupes
            t_d = time.time()
            diar = diariser(x, lisse, seuil, len(cibles['voix']), lambda y: (empreinte(en16(y)) if len(y) >= SR // 2 else None))
            coupes = fusionner_coupes(coupes, diar['coupes'], n)
            rapport['temps']['diarisation_s'] = round(time.time() - t_d, 1)
            log(f"[{tr['id']}] DIARISATION en {time.time() - t_d:.1f} s : {diar['k']} personne(s) pour {len(cibles['voix'])} "
                f"voix, {len(diar['coupes'])} changement(s) en pleine parole, essais {json.dumps(diar['essais'], ensure_ascii=False)}")
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
        chanteurs = None
        if personnes_parole:
            info = decider_personnes(segs, diar, cibles)
            rapport['voix_cibles'] = session['voix_cibles']
            log(f"[{tr['id']}] VOIX CIBLES (personnes, parole) : {info['message']}")
        elif plusieurs:
            info, chanteurs = decider_chant_cibles(segs, R, cibles, [round(c / SR, 3) for c in coupes[1:-1]
                                                                     if lisse[c // PAS] >= seuil])
            rapport['voix_cibles'] = session['voix_cibles']
            log(f"[{tr['id']}] VOIX CIBLES ({cibles['mode']}) : {info['message']}")
        elif mode == 'chant':
            info = decider_chant(segs, R, ctx['centre'], [round(c / SR, 3) for c in coupes[1:-1] if lisse[c // PAS] >= seuil])
        else:
            info = decider_parole(segs, ctx['f0_parole'], R)
            if R['modele_parole'] == 'parole22k':
                info['note'] = ('modèle parole 22 kHz : la hauteur suit la voix cible d\'elle-même ; les décalages par voix '
                                'ne servent qu\'à la variante f0')
            if cibles:
                rapport['voix_cibles'] = {**session['voix_cibles'], 'message': (
                    f"parole : une seule voix cible, la première ({R['voix']['nom']}) ; la répartition par chanteur ne "
                    'concerne que le chant')}
        if tr['octave'] in (-12, 0, 12):
            info['octave_imposee'] = tr['octave']   # la décision ci-dessus reste pour mémoire
            for s in segs:
                s['decalage'], s['base'] = tr['octave'], 'imposé'
        # transposition de la chanson (chant) : un décalage entier pour la musique, ajouté à l'octave de chaque voix.
        # Réglage absent (doubler d'avant la 0.15.0, qui ne transposerait pas la musique) : la chaîne d'avant, telle quelle.
        if 'transposer' in tr:
            voix_t = ([{'nom': v['nom'], 'centre_hz': v['centre_hz'], 'zone': v.get('zone')} for v in cibles['voix']]
                      if plusieurs else [{'nom': ctx['nom'], 'centre_hz': ctx['centre'], 'zone': ctx.get('zone')}])
            transp = decider_transposition(segs, f0_tout, voix_t, tr['transposer'], mode, R)
            appliquer_transposition(segs, info, chanteurs, transp['musique_dt'])
            decs_voix = decalages_par_voix_cible(segs, len(voix_t))
            for d in transp['voix']:
                dec = decs_voix[[v['nom'] for v in voix_t].index(d['voix'])]
                d['decalage_voix_dt'] = dec
                d['octave_dt'] = None if dec is None else dec - transp['musique_dt']
            rapport['transposition'] = transp
            log(f"[{tr['id']}] TRANSPOSITION {transp['musique_dt']:+d} dt : {transp['raison']}")
        rapport['decision_hauteur'] = info
        rapport['temps']['analyse_s'] = round(time.time() - t, 1)
        log(f"[{tr['id']}] {len(segs)} morceaux de {coeur_max / SR:.2f} s au plus, {sum(s['muet'] for s in segs)} muets ; "
            f"hauteur : {json.dumps(info, ensure_ascii=False)}")
        for l in frise(segs):
            log(f"[{tr['id']}] FRISE " + l)
        fondus = {c: fondu if lisse[c // PAS] < seuil else int(0.2 * SR) for c in coupes[1:-1]}
        gain, gain_trames = porte(lisse, seuil, n)
        return {'tr': tr, 'voix_j': ctx['j'], 'emb_ref': ctx['emb'], 'rapport': rapport, 'x': x, 'n': n, 'mode': mode, 'segs': segs, 'coupes': coupes, 'lisse': lisse,
                'seuil': seuil, 'fondus': fondus, 'gain': gain, 'gain_trames': gain_trames, 'coeur_max': coeur_max,
                'f0_tout': f0_tout, 'duree_s': time.time() - debut_tr, 'plusieurs': plusieurs, 'chanteurs': chanteurs}

    def f0_piste(y):
        """F0 RMVPE de toute une piste, une trame toutes les 10 ms calée sur l'échantillon 0 (comme f0_tout)."""
        return np.concatenate([f0_fn(en16(y[d:d + SR * 20])[0], thred=0.03)[:len(y[d:d + SR * 20]) // PAS]
                               for d in range(0, len(y), SR * 20)])

    def ecrire_courbes(e, sortie, f0_modele):
        """courbes.json (pour l'interface ; format 1, à ne pas changer sans prévenir) : mélodie de la voix isolée complète
        (celle de la décision d'octave, F0 de l'analyse) et de la voix convertie finale (voix principale + chœurs, une
        passe RMVPE de plus), une valeur toutes les 20 ms arrondie à 0,1 Hz (0 : non voisé), et le décalage appliqué à
        chaque trame (0 avec le modèle parole 22 kHz, qui suit la hauteur de la voix cible de lui-même). Temps 0 : début
        de l'audio doublé (de l'extrait s'il y en a un).
        Plusieurs voix cibles (même format 1, deux clés de plus) : voix_cibles (noms, dans l'ordre des indices) et
        voix_par_trame (-1 : aucune voix convertie ; j : la voix voix_cibles[j] seule ; len(voix_cibles) : plusieurs
        voix ensemble, un duo) ; decalage_dt est alors celui de la piste dominante (voix principale, sinon chœurs, sinon
        secours)."""
        t0 = time.time()
        f_src, f_out = e['f0_tout'], f0_piste(sortie)
        k = min(len(f_src), len(f_out))
        f_src, f_out = f_src[:k:2], f_out[:k:2]
        temps = np.arange(len(f_src)) * 0.02
        dec = np.zeros(len(f_src))
        if f0_modele:
            for s in e['segs']:
                dec[(temps >= s['debut_s']) & (temps < s['fin_s'])] = s['decalage']
        voix_t = None
        if e.get('trames_voix') is not None:
            code, dom_d = e['trames_voix']
            voix_t = [int(v) for v in a_longueur(code, k, -1)[:k:2]]
            if f0_modele:
                d10 = a_longueur(dom_d, k)[:k:2]
                dec = np.where(np.asarray(voix_t) >= 0, d10, dec)
        hz = lambda f: [round(float(v), 1) if v > 1 else 0 for v in f]
        courbes = {'format': 1, 'pas_s': 0.02, 'source_hz': hz(f_src), 'convertie_hz': hz(f_out),
                   'decalage_dt': [int(v) if float(v).is_integer() else round(float(v), 1) for v in dec]}
        if voix_t is not None:
            courbes.update({'voix_cibles': [v['nom'] for v in cibles['voix']], 'voix_par_trame': voix_t})
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
                   'recouvrement': int(R.get('nettoyage_recouvrement') or 4),
                   'precision': R.get('nettoyage_precision') or NETTOYAGE_PRECISION_DEFAUT}
        sur_cartes = cartes > 1 and modeles_presents(travail['karaoke'][0], travail['dereverb'])
        if cartes > 1 and not sur_cartes:
            log('NETTOYAGE sur une carte : modèles absents du montage d\'outils (deux cartes les téléchargeraient en même temps)')
        try:
            res = (nettoyer_sur_cartes(travail, [(e['tr']['id'], e['n'] / SR) for e in videos_chant], cartes) if sur_cartes
                   else etape_a_part('nettoyer', {**travail, 'resultat': '/tmp/net/resultat.json'}))
        except Exception as ex:  # noqa: BLE001
            res = {'erreur': repr(ex)}
        session['temps']['nettoyage_totale_s'] = round(time.time() - t, 1)
        session['temps']['chargement_nettoyage_s'] = round(sum((res.get('chargements_s') or {}).values()), 1)
        session['temps']['nettoyage_gpu_s'] = round(sum(v.get('duree_s') or 0 for v in (res.get('videos') or {}).values()), 1)
        session['nettoyage'] = {k: res.get(k) for k in ('karaoke', 'dereverb', 'recouvrement', 'chargements_s', 'erreur', 'code')}
        session['nettoyage']['precision'] = res.get('precision') or 'fp32'
        if res.get('repli_fp32'):
            session['nettoyage']['repli_fp32'] = res['repli_fp32']
        if res.get('cartes'):
            session['nettoyage']['cartes'] = res['cartes']
        for e in videos_chant:
            v = (res.get('videos') or {}).get(e['tr']['id']) or {}
            info = {'demande': True, 'applique': False, 'karaoke': res.get('karaoke'), 'dereverb': res.get('dereverb'),
                    'recouvrement': res.get('recouvrement'), 'precision': res.get('precision') or 'fp32'}
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

    def piste_voix(e):
        return {'nom': 'voix', 'chemin': e['tr']['voix'], 'x': e['x'], 'lisse': e['lisse'], 'seuil': e['seuil'],
                'gain': e['gain']}

    def preparer_pistes(e):
        """Pistes à convertir : voix principale et chœurs secs (voix nettoyée), sinon la voix isolée complète. Chaque piste
        a sa porte des silences (et, nettoyée, son propre seuil : des chœurs absents d'un morceau n'y sont pas convertis).
        Voix nettoyée : les passages perdus au nettoyage (zones_perdues) sont repris de la voix complète par une
        troisième piste, « secours », dont la porte n'est ouverte que sur eux ; rien de perdu, rien ne change."""
        if not e.get('net'):
            return [piste_voix(e)]
        pistes = []
        for nom in ('lead_sec', 'choeurs_sec'):
            xt = lire_mono(e['net'][nom])
            xt = xt[:e['n']] if len(xt) >= e['n'] else np.pad(xt, (0, e['n'] - len(xt)))
            env_t = enveloppe_db(xt)
            lisse_t, seuil_t = lisser_db(env_t), seuil_silence(env_t)
            gain_t, trames_t = porte(lisse_t, seuil_t, e['n'])
            pistes.append({'nom': nom, 'chemin': e['net'][nom], 'x': xt, 'lisse': lisse_t, 'seuil': seuil_t,
                           'gain': gain_t, 'gain_trames': trames_t})
        if R.get('secours', True) is False:
            e['rapport']['secours'] = {'demande': False}
            return pistes
        t = time.time()
        try:
            zones, info = zones_perdues(enveloppe_db(e['x']), e['lisse'], e['seuil'], e['f0_tout'],
                                        [(p['lisse'], p['gain_trames']) for p in pistes], R.get('secours_reglages'))
        except Exception as ex:  # noqa: BLE001 — sans piste de secours : la voix nettoyée comme avant
            import traceback
            traceback.print_exc()
            e['rapport']['secours'] = {'demande': True, 'decision': 'aucun', 'erreur': repr(ex)[:300]}
            return pistes
        voix_active_s = round(float((e['lisse'] > e['seuil']).sum()) * 0.01, 1)
        decision, secondes = decider_secours(zones, voix_active_s, R.get('secours_reglages'))
        e['rapport']['secours'] = {'demande': True, 'decision': decision, 'zones': zones, 'secondes': secondes,
                                   'voix_active_s': voix_active_s, 'detection_s': round(time.time() - t, 2), **info}
        if zones:
            log(f"[{e['tr']['id']}] SECOURS {decision} : {len(zones)} zone(s) perdue(s) au nettoyage, {secondes:.1f} s : "
                + ', '.join(f"{z['debut_s']:.2f}-{z['fin_s']:.2f} s (écart {z['ecart_db']:.0f} dB)" for z in zones))
        if decision == 'voix_complete':
            log(f"[{e['tr']['id']}] NETTOYAGE trop destructeur ({secondes:.0f} s perdues sur {voix_active_s:.0f} s de voix) : "
                'voix isolée complète convertie seule')
            e['net'] = None
            e['rapport']['nettoyage'].update({'applique': False, 'raison': (
                f'nettoyage trop destructeur : {secondes:.0f} s de voix perdues sur {voix_active_s:.0f} s (plus de '
                f"{SECOURS_DEFAUT['part_max']:.0%}) : voix isolée complète convertie")})
            return [piste_voix(e)]
        if decision == 'secours':
            gain_s, trames_s = porte_zones(zones, e['n'])
            pistes.append({'nom': 'secours', 'chemin': e['tr']['voix'], 'x': e['x'], 'lisse': e['lisse'],
                           'seuil': e['seuil'], 'gain': gain_s, 'gain_trames': trames_s, 'zones': zones})
        return pistes

    def affecter(e):
        """Plusieurs voix cibles, chant : voix et décalage de chaque piste sur chaque morceau. Duo : chaque piste nettoyée
        est reconnue sur chaque morceau qu'elle convertit, par sa hauteur (RMVPE sur la piste) et son empreinte
        (CAMPPlus sur la piste, projetée sur l'axe du duo) ; la piste de secours, sur ses seules zones ; la voix complète
        suit la décision du morceau. Une seule voix trouvée : tout suit le morceau."""
        t = time.time()
        modele, segs = e['chanteurs'], e['segs']
        mesures, plans = {}, {}
        for p in e['pistes']:
            liste = [(m[0], m[1], m[2]) for m in morceaux_piste(segs, p, e['fondus'])]
            plans[p['nom']] = {i for i, _, _ in liste}
            if modele is None or p['nom'] == 'voix':
                continue
            emb_fn = lambda y: empreinte(en16(y))
            if p['nom'] == 'secours':
                mesures[p['nom']] = mesurer_piste(e['x'], e['f0_tout'], e['x'], liste, emb_fn, modele['vecteur'], p['gain'])
            else:
                mesures[p['nom']] = mesurer_piste(p['x'], f0_piste(p['x']), e['x'], liste, emb_fn, modele['vecteur'])
        octave = e['tr']['octave'] if e['tr']['octave'] in (-12, 0, 12) else None
        aff = affecter_pistes(segs, [p['nom'] for p in e['pistes']], mesures, modele, cibles, octave, R,
                              (e['rapport'].get('transposition') or {}).get('musique_dt', 0))
        for p in e['pistes']:
            p['affectation'] = {i: (a['cible'], a['decalage']) for i, a in aff[p['nom']].items()}
        e['aff'], e['plans'] = aff, plans
        e['rapport']['affectation'] = rapport_affectation(segs, aff, plans, cibles, e['rapport']['decision_hauteur'])
        e['rapport']['affectation']['duree_s'] = round(time.time() - t, 2)
        e['rapport']['temps']['affectation_s'] = round(time.time() - t, 2)
        log(f"[{e['tr']['id']}] AFFECTATION en {time.time() - t:.1f} s : "
            + json.dumps({nom: r for nom, r in e['rapport']['affectation']['resume'].items()}, ensure_ascii=False))

    def plan(e, p, liste):
        """Morceaux d'une piste : ceux de la voix complète (mêmes coupes, même décalage), sauf les muets et ceux où cette
        piste se tait (secours : ceux qui touchent ses zones). [(i, début, fin, décalage, fondu au début, à la fin)] ;
        voix à comparer : plus l'indice de la voix, en 7e élément."""
        ms = morceaux_piste(liste, p, e['fondus'])
        j = e.get('voix_j')
        return ms if j is None else [m[:6] + (j,) for m in ms]   # voix à comparer : sa référence (7e élément, comme en duo)

    def preparer_versions(e):
        """Une voix par chanteur, versions_duo toutes ou utiles : décalage de chaque voix cible, passages dont on veut
        aussi la version dans l'autre voix (tous, ou les utiles)."""
        info = e['rapport']['decision_hauteur']
        octave = e['tr']['octave'] if e['tr']['octave'] in (-12, 0, 12) else None
        decs = decalages_par_voix(info, cibles, octave, (e['rapport'].get('transposition') or {}).get('musique_dt', 0))
        utiles = (morceaux_utiles(e['segs'], e.get('aff'), e.get('plans'), info.get('voix') or 1, R)
                  if versions_mode == 'utiles' else None)
        e['versions'] = {'mode': versions_mode, 'decalages': decs, 'nv': len(cibles['voix']),
                         'garder': None if utiles is None else set(utiles), 'raisons': utiles}

    def plan_versions(e, p, liste):
        """[(rang, morceaux)] d'une piste dans chaque autre voix cible (versions_autres)."""
        v = e['versions']
        return versions_autres(plan(e, p, liste), v['decalages'], v['garder'])

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

    def convertir_ying(etats_v, paroles=None):
        """Toutes les pistes à convertir par YingMusic-SVC (toutes vidéos et variantes), dans une seule étape à part.
        paroles : travail de l'étape paroles ; sur plusieurs cartes, la dernière le fait d'abord, pendant que les autres
        convertissent, puis rejoint la conversion (son résultat revient dans « avant », absent s'il n'a pas tourné)."""
        pistes = []
        for e in etats_v:
            for k, (nom, pas, liste, fichier) in enumerate(e['variantes']):
                if nom == 'yingmusic':
                    vers = e.get('versions') if fichier == 'voix_convertie.flac' else None
                    for p in e['pistes']:
                        base = f"/tmp/ying/{e['tr']['id']}_{k}_{p['nom']}"
                        pistes.append({'cle': cle_ying(e, fichier, p), 'entree': p['chemin'], 'pas': pas,
                                       'sortie': f'{base}.wav', 'morceaux': plan(e, p, liste),
                                       **({'couches': f'{base}_couches.npy', 'voix': vers['nv']} if vers else {})})
                        # deux versions : chaque morceau aussi dans l'autre voix (pistes de plus, même graine par
                        # morceau : la piste automatique ci-dessus n'en dépend pas)
                        for r, ms in (plan_versions(e, p, liste) if vers else ()):
                            pistes.append({'cle': f'{cle_ying(e, fichier, p)}|autre{r}', 'entree': p['chemin'], 'pas': pas,
                                           'sortie': f'{base}_autre{r}.wav', 'morceaux': ms,
                                           'couches': f'{base}_autre{r}_couches.npy', 'voix': vers['nv']})
        if not pistes:
            return {}
        t = time.time()
        torch.cuda.empty_cache()
        ck = poids_ying(outils)
        travail = {'outils': outils, 'checkpoint': ck, 'reference': ref_chemin, 'pad': pad, 'pistes': pistes,
                   'resultat': '/tmp/ying/resultat.json'}
        if any(e.get('plusieurs') for e in etats_v):
            travail['references'] = [v['chemin'] for v in cibles['voix']]   # indice = 7e élément de chaque morceau
        elif comparees:
            travail['references'] = [v['chemin'] for v in comparees]        # idem : une référence par voix à comparer
        try:
            # deux cartes : morceaux partagés, piste assemblée ici au bit près (invite : la référence, 25 s au plus)
            res = (ying_sur_cartes(travail, cartes, min(25.0, float(refs['chant44k']['duree'])),
                                   ('paroles', paroles) if paroles else None) if cartes > 1
                   else etape_a_part('ying', travail))
        except Exception as ex:  # noqa: BLE001 — repli sur Seed-VC
            res = {'erreur': repr(ex)}
        session['temps']['yingmusic_total_s'] = round(time.time() - t, 1)
        session['temps']['chargement_yingmusic_s'] = round((res.get('chargement_s') or 0) + ((res.get('installation') or {}).get('duree_s') or 0), 1)
        session['temps']['yingmusic_gpu_s'] = round(sum(p.get('conversion_s') or 0 for p in (res.get('pistes') or {}).values()), 1)
        session['yingmusic'] = {k: res.get(k) for k in ('commit', 'poids', 'poids_origine', 'installation', 'chargement_s', 'erreur', 'code')}
        if res.get('cartes'):
            session['yingmusic']['cartes'] = res['cartes']
        if res.get('references_s'):
            session['yingmusic']['references_s'] = res['references_s']   # préparation de chaque référence (voix cibles)
        ecrire_session()
        return res

    def ecrire_versions(e, nom, pas, liste, fichier, plans_v, couches_v, gains_v, garde, ying, mes):
        """Deux versions de chaque passage (une voix par chanteur) : l'autre voix de chaque morceau (relue de l'étape
        YingMusic, ou convertie ici par Seed-VC), puis dans versions/ :
          <piste>_<j>.flac   la piste dans la voix j : deux canaux, morceaux pairs et impairs (poser_morceaux), fondus
                             et porte des silences compris, avant la mise au niveau ; crête ramenée à -1 dBFS (gain noté)
          versions.json      la recette : pour chaque piste, dans l'ordre du noyau, ses morceaux (numéro, bornes,
                             fondus, voix et décalage automatiques, voix disponibles), sa mise au niveau (rms de la
                             source et trames actives, ou porte et rms de la voix complète pour le secours), ses trames
                             ouvertes pour la voix entendue (voix_par_trames) ; le décalage de chaque voix, la mélodie de
                             chaque voix (toute la chanson dans cette voix, 20 ms), le garde-crête de l'automatique.
        doubler.py en refait, sur le PC, la voix convertie de n'importe quelle affectation (même assemblage, même porte,
        même mise au niveau, même garde-crête) : l'automatique redonne voix_convertie.flac. Rend la section « versions »
        du rapport."""
        vers, tr, n = e['versions'], e['tr'], e['n']
        nv = vers['nv']
        conv_s, secondes = 0.0, 0.0
        for p, _ in plans_v:
            if couches_v.get(p['nom']) is None:
                raise RuntimeError(f"couches de la piste {p['nom']} absentes")
            for r, ms in plan_versions(e, p, liste):
                if not ms:
                    continue
                secondes += sum(b - a for _, a, b, *_ in ms) / SR
                if nom == 'yingmusic':
                    i = (ying.get('pistes') or {}).get(f'{cle_ying(e, fichier, p)}|autre{r}') or {}
                    c = charger_couches(i)
                    if i.get('sortie') and os.path.exists(i['sortie']):
                        os.remove(i['sortie'])
                    if c is None:
                        raise RuntimeError(f"autre voix de la piste {p['nom']} absente : "
                                           f"{i.get('erreur') or ying.get('erreur') or 'étape YingMusic'}")
                    couches_v[p['nom']] += c
                    conv_s += float(i.get('conversion_s') or 0)
                else:
                    md = modeles[nom]
                    refs_v = refs_cibles[nom] if nom in refs_cibles else [refs[nom]]
                    t = time.time()
                    assembler(p['x'], ms, pad, lambda xs, d, v=0: convertir(xs, md, refs_v[v], d if md['f0'] else 0, pas),
                              R['graine'], torch, couches_v[p['nom']])
                    conv_s += time.time() - t
        t0 = time.time()
        d = os.path.join(tr['dossier'], 'versions')
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        k10 = len(e['f0_tout'])
        lisse_voix = a_longueur(e['lisse'], k10, -120.0)
        totaux = np.zeros((nv, n), np.float32)
        garder, pistes_r, dispo = vers['garder'], [], {}
        for p, morceaux in plans_v:
            c = couches_v[p['nom']]
            c *= np.asarray(p['gain'], np.float32)[None, :c.shape[1]]
            fichiers, gains_f = [], []
            for j in range(nv):
                paire = c[2 * j:2 * j + 2]
                pic = float(np.abs(paire).max())
                g = 10 ** (-1 / 20) / pic if pic > 0 else 1.0
                f = f"{p['nom']}_{j}.flac"
                sf.write(os.path.join(d, f), np.ascontiguousarray((paire * np.float32(g)).T), SR, subtype='PCM_24')
                fichiers.append(f)
                gains_f.append(g)
                totaux[j, :c.shape[1]] += (paire[0] + paire[1]) * np.float32(gains_v[p['nom']])
            ouvert = a_longueur(p.get('gain_trames', e['gain_trames']), k10) > 0.5
            if p['nom'] != 'secours':
                ouvert &= a_longueur(p['lisse'], k10, -120.0) >= lisse_voix + CHANTEURS_DEFAUT['piste_min_db']
            if p['nom'] == 'secours':
                porte_o = np.asarray(p['gain']) > 0.5
                sx = float(np.sqrt(np.mean(np.asarray(p['x'], np.float64)[porte_o] ** 2))) if porte_o.any() else 0.0
                niveau = {'type': 'secours', 'rms_source': sx, 'porte': [[int(a), int(b)] for a, b in suites(porte_o)],
                          'borne_db': 20.0}
            elif p['nom'] == 'voix':
                niveau = {'type': 'aucun'}
            else:
                env = enveloppe_db(p['x'])
                actif = lisser_db(env) > seuil_silence(env)
                niveau = {'type': 'source', 'rms_source': rms_actif(p['x']),
                          'actif': [[int(a), int(b)] for a, b in suites(actif)]}
            ms = []
            for m in morceaux:
                v = int(m[6]) if len(m) > 6 else 0
                voix_m = list(range(nv)) if garder is None or m[0] in garder else [v]
                ms.append([int(m[0]), int(m[1]), int(m[2]), int(m[4]), int(m[5]), v, m[3], voix_m])
                dispo[int(m[0])] = dispo.get(int(m[0]), True) and len(voix_m) == nv
            pistes_r.append({'nom': p['nom'], 'fichiers': fichiers, 'gains_fichiers': gains_f,
                             'gain_auto': float(gains_v[p['nom']]), 'niveau': niveau,
                             'ouvert': [[int(a), int(b)] for a, b in suites(ouvert)], 'morceaux': ms})
        hz = lambda f: [round(float(v), 1) if v > 1 else 0 for v in f]
        f0_voix = [hz(f0_piste(totaux[j])[::2]) for j in range(nv)]
        noms_v = [v['nom'] for v in cibles['voix']]
        raisons = {str(i): r for i, r in sorted((vers['raisons'] or {}).items())} if vers['raisons'] is not None else None
        recette = {'format': 1, 'sr': SR, 'echantillons': int(n), 'trames': int(k10), 'pad': int(pad),
                   'trame_echantillons': PAS, 'voix_cibles': noms_v, 'decalages': list(vers['decalages']),
                   'mode': vers['mode'], 'garde_auto': float(garde), 'pistes': pistes_r, 'f0_voix_hz': f0_voix,
                   'raisons': raisons}
        with open(os.path.join(d, 'versions.json'), 'w') as fj:
            json.dump(recette, fj, ensure_ascii=False, separators=(',', ':'), default=float)
        conv_auto = float(mes.get('conversion_s') or 0)
        taille = sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d))
        resume = {'mode': vers['mode'], 'dossier': 'versions', 'morceaux': sorted(i for i, ok in dispo.items() if ok),
                  'voix_cibles': noms_v, 'decalages': dict(zip(noms_v, vers['decalages'])), 'secondes': round(secondes, 1),
                  'conversion_s': round(conv_s, 1), 'conversion_auto_s': round(conv_auto, 1),
                  'surcout': round(conv_s / conv_auto, 3) if conv_auto > 0 else None,
                  'taille_mo': round(taille / 1e6, 1), 'ecriture_s': round(time.time() - t0, 1)}
        if raisons is not None:
            resume['raisons'] = raisons
        session['temps']['versions_gpu_s'] = round((session['temps'].get('versions_gpu_s') or 0) + conv_s, 1)
        log(f"[{tr['id']}] DEUX VERSIONS ({vers['mode']}) : {len(resume['morceaux'])} passage(s) dans chaque voix, "
            f"{secondes:.0f} s converties en plus en {conv_s:.0f} s de GPU (automatique : {conv_auto:.0f} s), "
            f"{resume['taille_mo']} Mo")
        return resume

    def refs_de(e, nom):
        """Références du modèle nom pour les morceaux de e : une par voix cible (duo), une par voix à comparer, sinon la
        seule (choisie par le 7e élément du morceau)."""
        if e.get('plusieurs') and nom in refs_cibles:
            return refs_cibles[nom]
        if e.get('plusieurs'):   # parole à plusieurs voix (Seed-VC 22 kHz) : la référence de chaque voix, préparée une fois
            refs_cibles[nom] = [refs[nom] if v['chemin'] == ref_chemin else preparer_ref(modeles[nom], v['chemin'])
                                for v in cibles['voix']]
            return refs_cibles[nom]
        if comparees:
            if nom not in refs_comp:
                refs_comp[nom] = [refs[nom] if v['chemin'] == ref_chemin else preparer_ref(modeles[nom], v['chemin'])
                                  for v in comparees]
            return refs_comp[nom]
        return [refs[nom]]

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
            plans_v = []   # (piste, morceaux) de cette variante : voix entendue à chaque trame (plusieurs voix cibles)
            # deux versions (voix principale seulement) : couches de parité de chaque piste, gain de mise au niveau
            vers = e.get('versions') if fichier == 'voix_convertie.flac' else None
            couches_v, gains_v = {}, {}
            for p in e['pistes']:
                morceaux = plan(e, p, liste)
                plans_v.append((p, morceaux))
                if nom == 'yingmusic':
                    y, conv = lire_mono(infos[p['nom']]['sortie']), float(infos[p['nom']]['conversion_s'])
                    os.remove(infos[p['nom']]['sortie'])
                    ying_s += conv
                    if vers:
                        couches_v[p['nom']] = charger_couches(infos[p['nom']])
                elif morceaux:
                    md = modeles[nom]
                    # une référence par voix cible (préparée une fois), choisie par le 7e élément de chaque morceau
                    refs_v = refs_de(e, nom)
                    couches = np.zeros((2 * vers['nv'], len(p['x'])), np.float32) if vers else None
                    t = time.time()
                    y = assembler(p['x'], morceaux, pad,
                                  lambda xs, d, v=0: convertir(xs, md, refs_v[v], d if md['f0'] else 0, pas),
                                  R['graine'], torch, couches)
                    conv = time.time() - t
                    if vers:
                        couches_v[p['nom']] = couches
                else:
                    y, conv = np.zeros(n, np.float32), 0.0
                    if vers:
                        couches_v[p['nom']] = np.zeros((2 * vers['nv'], n), np.float32)
                gains_v[p['nom']] = 1.0
                y = y[:n] if len(y) >= n else np.pad(y, (0, n - len(y)))
                y = (y * p['gain']).astype(np.float32)
                secondes_p = sum(b - a for _, a, b, *_ in morceaux) / SR
                d = {'morceaux': len(morceaux), 'secondes_converties': round(secondes_p, 1), 'conversion_s': round(conv, 1),
                     'rtf': round(conv / max(secondes_p, 1e-3), 3)}
                if p['nom'] == 'secours' and morceaux:
                    # la piste de secours reprend le niveau de la voix complète dans ses zones
                    g = gain_secours(p['x'], y, p['gain'])
                    y = (y * g).astype(np.float32)
                    gains_v[p['nom']] = g
                    d.update({'gain_niveau_db': round(20 * math.log10(g), 2), 'zones': len(p['zones']),
                              'secondes_zones': round(sum(z['duree_s'] for z in p['zones']), 2)})
                elif p['nom'] != 'voix' and morceaux:
                    # chaque piste nettoyée reprend le niveau de sa source : l'équilibre voix principale / chœurs d'origine
                    g = rms_actif(p['x']) / max(rms_actif(y, p['x']), 1e-9)
                    y = (y * g).astype(np.float32)
                    gains_v[p['nom']] = g
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
                # Mesures : ressemblance de timbre (CAMPPlus, cosinus avec la référence), hauteur obtenue. Plusieurs voix
                # cibles : la voix et le décalage de la piste dominante du morceau (la première qui le convertit : voix
                # principale, chœurs, secours), et la ressemblance avec chaque voix sur ses morceaux
                dominante = {}
                if e.get('plusieurs'):
                    for p, morceaux in plans_v:
                        for m in morceaux:
                            dominante.setdefault(m[0], (m[6] if len(m) > 6 else 0, m[3]))
                embs_v = [r['emb'] for r in refs_cibles['chant44k']] if e.get('plusieurs') else None
                sims, sims_src, ecarts, par_voix = [], [], [], {}
                for s in segs:
                    if s['muet'] or s['fin'] - s['debut'] < SR // 2:
                        continue
                    o16 = en16(total[s['debut']:s['fin']])
                    w = s['fin'] - s['debut']
                    j, dec_s = dominante.get(s['i'], (s.get('cible', 0), s['decalage']))
                    emb_ref = embs_v[j] if embs_v is not None else e.get('emb_ref', ref_emb)
                    sims.append((float(empreinte(o16) @ emb_ref), w))
                    if embs_v is not None:
                        par_voix.setdefault(cibles['voix'][j]['nom'], []).append(sims[-1])
                    if s.get('emb') is not None:
                        sims_src.append((float(s['emb'] @ emb_ref), w))
                    if s['f0_med'] and s['voise_s'] >= 0.5:
                        fo = f0_fn(o16[0], thred=0.03)
                        fo = fo[fo > 1]
                        if len(fo):
                            ecarts.append((12 * np.log2(np.median(fo) / s['f0_med']), dec_s))
                moy = lambda l: round(sum(a * w for a, w in l) / max(sum(w for _, w in l), 1), 3) if l else None
                mes.update({'ressemblance_timbre_sortie': moy(sims), 'ressemblance_timbre_source': moy(sims_src)})
                if embs_v is not None:
                    mes['ressemblance_timbre_par_voix'] = {v: moy(l) for v, l in par_voix.items()}
                if ecarts:
                    mes['ecart_hauteur_obtenu_median_dt'] = round(float(np.median([e_ for e_, _ in ecarts])), 2)
                    if f0:
                        mes['ecart_a_la_consigne_median_dt'] = round(float(np.median([e_ - d_ for e_, d_ in ecarts])), 2)
                        mes['morceaux_hors_consigne_6dt'] = int(sum(abs(e_ - d_) > 6 for e_, d_ in ecarts))
            rapport['variantes'][fichier] = mes
            if fichier == 'voix_convertie.flac':
                rapport['modele'] = nom
                if e.get('plusieurs'):
                    try:
                        # voix entendue à chaque trame de 10 ms (et décalage de la piste dominante) : courbes.json, frise
                        k10 = len(e['f0_tout'])
                        code, dom_d = voix_par_trames(
                            [{'morceaux': m, 'gain_trames': p.get('gain_trames', e['gain_trames']), 'lisse': p['lisse'],
                              'secours': p['nom'] == 'secours'} for p, m in plans_v], e['lisse'], k10, len(cibles['voix']))
                        e['trames_voix'] = (code, dom_d)
                        noms_v = [v['nom'] for v in cibles['voix']]
                        aff_r = rapport.setdefault('affectation', {})
                        aff_r['passages'] = passages_voix(code, noms_v, 0.01, 0.2)
                        aff_r['secondes_par_voix'] = {**{v: round(float((code == j).sum()) * 0.01, 1) for j, v in enumerate(noms_v)},
                                                      'ensemble': round(float((code == len(noms_v)).sum()) * 0.01, 1)}
                    except Exception as ex:  # noqa: BLE001 — ne sert qu'à l'affichage
                        log(f"[{tr['id']}] voix par trame impossible : {ex!r}")
                try:
                    ecrire_courbes(e, total, f0)
                except Exception as ex:  # noqa: BLE001 — les courbes ne servent qu'à l'affichage
                    log(f"[{tr['id']}] courbes.json impossible : {ex!r}")
                if vers:
                    try:
                        rapport['versions'] = ecrire_versions(e, nom, pas, liste, fichier, plans_v, couches_v, gains_v,
                                                              garde, ying, mes)
                    except Exception as ex:  # noqa: BLE001 — sans deux versions : le doublage automatique reste
                        import traceback
                        traceback.print_exc()
                        shutil.rmtree(os.path.join(tr['dossier'], 'versions'), ignore_errors=True)
                        rapport['versions'] = {'mode': vers['mode'], 'erreur': repr(ex)[:300]}
                        log(f"[{tr['id']}] DEUX VERSIONS impossibles ({ex!r}) : le doublage automatique reste")
                    couches_v.clear()
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
            s.pop('k', None)   # groupe interne du duo (plusieurs voix cibles) : le nom du chanteur est dans « voix »
        rapport.update({'echantillons': n, 'duree_s': round(n / SR, 3), 'seuil_silence_dbfs': round(seuil, 1),
                        'coeur_max_s': round(e['coeur_max'] / SR, 2), 'morceaux': segs, 'frise': frise(segs),
                        'coupes_dans_le_son_s': [round(c / SR, 2) for c in coupes[1:-1] if lisse[c // PAS] >= seuil],
                        'porte_part_coupee': round(float((e['gain_trames'] < 0.5).mean()), 3)})
        rapport['temps']['total_video_s'] = round(rapport['temps'].get('separation_s', 0) + e['duree_s']
                                                  + (rapport['temps'].get('nettoyage_s') or 0) + ying_s + time.time() - t_video, 1)
        return rapport

    def preparer_paroles(etats_v):
        """Travail de l'étape paroles pour toutes les vidéos (Whisper et le traducteur chargés une fois) : voix principale
        sèche quand elle existe, sinon voix isolée complète ; seulement ses phrases chantées (hauteur RMVPE). Attend la
        copie locale des modèles. Rend (début, travail), travail None s'il n'y a rien à transcrire."""
        t = time.time()
        chemins, attente = chemins_paroles(copie_paroles)
        session['temps']['paroles_attente_copie_s'] = attente
        session['temps']['paroles_copie_s'] = copie_paroles.get('duree_s')
        if copie_paroles.get('erreur'):
            log(f"PAROLES copie locale des modèles impossible ({copie_paroles['erreur']}) : lus sur le montage")
        travail = {'whisper': chemins.get('whisper'), 'traducteur': chemins.get('traducteur'), 'cache': '/tmp/hf_paroles',
                   'resultat': '/tmp/paroles/resultat.json', 'videos': [],
                   'bilingue': bool(REGLAGES.get('paroles_bilingue', True))}
        for e in etats_v:
            p = next((q for q in e['pistes'] if q['nom'] == 'lead_sec'), None) or piste_voix(e)
            phrases, chant = zones_a_transcrire(p['lisse'], p['seuil'], e['f0_tout'])
            e['rapport']['paroles'] = {'piste': p['nom'], 'phrases': len(phrases),
                                       'secondes_a_transcrire': round(sum(b - a for a, b in phrases), 1)}
            if phrases:
                travail['videos'].append({'id': e['tr']['id'], 'audio': p['chemin'], 'piste': p['nom'], 'phrases': phrases,
                                          'chant': chant, 'sortie': os.path.join(e['tr']['dossier'], 'paroles.json')})
            else:
                ecrire_json(os.path.join(e['tr']['dossier'], 'paroles.json'), paroles_json([], piste=p['nom']))
        if not travail['videos']:
            return t, None
        log(f"PAROLES de {len(travail['videos'])} vidéo(s) : Whisper large-v3 "
            f"({origine_modele(travail['whisper'], None)}), traduction {TRADUCTEUR[1]} "
            f"({origine_modele(travail['traducteur'], None)}) ; attente de la copie locale {attente:.0f} s")
        return t, travail

    def recueillir_paroles(etats_v, res, total_s, pendant_conversion=False):
        """Mesures et temps de l'étape paroles dans la session et dans le rapport de chaque vidéo."""
        session['temps']['paroles_total_s'] = round(total_s, 1)
        session['temps']['chargement_paroles_s'] = round(sum((res.get('chargements_s') or {}).values()), 1)
        session['paroles'] = {k: res.get(k) for k in ('implementation', 'modele', 'traducteur', 'whisper_origine',
                                                      'traducteur_origine', 'chargements_s', 'temps', 'erreur',
                                                      'erreur_traduction', 'langues_sans_traduction', 'code', 'duree_s')}
        # carte et mémoire du GPU de l'étape (mémoire libre au départ, allègement de Whisper, plus fort de l'étape)
        session['paroles'].update({k: res[k] for k in ('carte', 'memoire', 'memoire_gpu_max_mo') if res.get(k) is not None})
        if pendant_conversion:
            session['paroles'].update({k: res.get(k) for k in ('carte', 'attente_chargement_s', 'memoire_gpu_max_mo', 'reprise')},
                                      pendant_conversion=pendant_conversion)
        for e in etats_v:
            v = (res.get('videos') or {}).get(e['tr']['id'])
            if v is not None:
                e['rapport']['paroles'].update(v)
            elif res.get('erreur') and e['rapport']['paroles'].get('phrases'):
                e['rapport']['paroles']['erreur'] = res['erreur']
            e['rapport']['temps']['paroles_s'] = (v or {}).get('transcription_s')
        ecrire_session()

    def transcrire(etats_v):
        """Paroles après la conversion, sur la carte 0 (une carte : la chaîne d'avant)."""
        t, travail = preparer_paroles(etats_v)
        if travail is None:
            return
        torch.cuda.empty_cache()
        try:
            res = etape_a_part('paroles', travail)
        except Exception as ex:  # noqa: BLE001 — sans paroles : le doublage continue
            res = {'erreur': repr(ex)}
        recueillir_paroles(etats_v, res, time.time() - t)

    def finir_paroles(etats_v, travail, res):
        """Paroles lancées sur une autre carte pendant la conversion (res : leur résultat, None si elles n'ont pas tourné :
        pas de morceau YingMusic-SVC, étape YingMusic en échec) ; faites ici, après la conversion, si elles n'ont pas
        tourné ou si leur carte a lâché (paroles_a_reprendre). Pas tournées : sur la dernière carte, libre (le noyau garde
        Seed-VC 44 kHz chargé sur la carte 0 : 2 Gio de moins pour Whisper) ; carte en échec : reprises sur la carte 0.
        paroles_total_s : attente de la copie et durée de l'étape, pas la conversion qui tournait en même temps."""
        total, en_parallele = session['temps'].get('paroles_attente_copie_s') or 0.0, res is not None
        if res is not None:
            total += res.get('duree_s') or 0.0
            # durée de l'étape sur sa carte, comprise dans yingmusic_total_s (estimation de doubler)
            session['temps']['paroles_pendant_conversion_s'] = res.get('duree_s')
        if paroles_a_reprendre(res):
            carte = cartes - 1 if res is None and cartes > 1 else 0
            if res is not None:
                log(f"PAROLES sur la carte {res.get('carte')} en échec (code {res.get('code')}, {res.get('erreur')}) : "
                    'reprises sur la carte 0')
            torch.cuda.empty_cache()
            t = time.time()
            try:
                res = {**etape_a_part('paroles', {**travail, 'carte': carte} if carte else travail, cartes),
                       'reprise': res is not None}
            except Exception as ex:  # noqa: BLE001 — sans paroles : le doublage continue
                res = {'erreur': repr(ex)}
            total += time.time() - t
        recueillir_paroles(etats_v, res, total, pendant_conversion=en_parallele)
        if res.get('carte') is not None:
            log(f"PAROLES sur la carte {res['carte']}" + (' pendant la conversion' if en_parallele and not res.get('reprise')
                                                         else ' après la conversion')
                + f" : {res.get('duree_s')} s, dont {res.get('attente_chargement_s') or 0} s d'attente du chargement de "
                f"YingMusic-SVC, Whisper chargé en {(res.get('chargements_s') or {}).get('whisper')} s, transcription "
                f"{(res.get('temps') or {}).get('transcription_s')} s ; mémoire du GPU au plus fort "
                f"{res.get('memoire_gpu_max_mo')} Mo, {(res.get('memoire') or {}).get('libre_mo')} Mo libres au départ")

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

    def tr_voix(tr, ctx):
        """Travail d'une vidéo pour une voix à comparer : sa sortie dans <vidéo>/<voix>/, son identifiant à lui (la voix
        isolée, restée dans <vidéo>/, et le nettoyage sont communs). Une seule voix : le travail tel quel."""
        if ctx['j'] is None:
            return tr
        d = os.path.join(tr['dossier'], ctx['nom'])
        os.makedirs(d, exist_ok=True)
        return {**tr, 'id': f"{tr['id']}~{ctx['j']}", 'id_video': tr['id'], 'dossier': d}

    # 3. Analyse de chaque vidéo (mode, morceaux, décalages), sur la voix isolée complète ; voix à comparer : une analyse
    # par voix (octave et taille des morceaux dépendent de la voix), le reste de l'analyse n'est fait qu'une fois
    etats = []
    for tr in travaux:
        for ctx in contextes:
            try:
                etats.append(analyser(tr_voix(tr, ctx), ctx))
            except Exception as ex:  # noqa: BLE001 — une vidéo ratée n'empêche pas les suivantes
                echec(tr_voix(tr, ctx), ex)
    # Nettoyage et paroles : une fois par vidéo, faits sur le premier état de chaque vidéo puis recopiés aux autres voix
    principal = {}
    for e in etats:
        principal.setdefault(e['tr'].get('id_video', e['tr']['id']), e)
    principaux = list(principal.values())

    def partager(cle_rapport, avec_temps):
        """Recopie le résultat d'une étape commune (rapport[cle_rapport] et ses temps) du premier état d'une vidéo aux autres."""
        for e in etats:
            p = principal[e['tr'].get('id_video', e['tr']['id'])]
            if p is e:
                continue
            if p['rapport'].get(cle_rapport) is not None:
                e['rapport'][cle_rapport] = json.loads(json.dumps(p['rapport'][cle_rapport], default=float))
            for k in avec_temps:
                if k in p['rapport']['temps']:
                    e['rapport']['temps'][k] = p['rapport']['temps'][k]
    # 4. Nettoyage des vidéos en chant
    en_chant = [e for e in etats if e['mode'] == 'chant']
    principaux_chant = [e for e in principaux if e['mode'] == 'chant']
    for e in etats:
        if e['mode'] != 'chant':
            e['rapport']['nettoyage'] = {'demande': nettoyage_demande, 'applique': False,
                                         'raison': 'parole : voix isolée complète, sans nettoyage (Seed-VC 22 kHz)'}
        elif not nettoyage_demande:
            e['rapport']['nettoyage'] = {'demande': False, 'applique': False,
                                         'raison': 'nettoyage désactivé (--sans-nettoyage) : voix isolée complète convertie'}
    if nettoyage_demande and en_chant:
        log(f"NETTOYAGE de {len(principaux_chant)} vidéo(s) en chant : voix principale et chœurs, reverb d'origine retirée")
        try:
            nettoyer(principaux_chant)
            for e in en_chant:   # voix à comparer : la même voix nettoyée pour toutes
                p = principal[e['tr'].get('id_video', e['tr']['id'])]
                if p is not e:
                    e['net'] = p.get('net')
            partager('nettoyage', ('nettoyage_s',))
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
        if e.get('plusieurs'):
            try:
                affecter(e)
            except Exception as ex:  # noqa: BLE001 — sans reconnaissance des pistes : chaque piste suit le morceau
                import traceback
                traceback.print_exc()
                log(f"[{e['tr']['id']}] AFFECTATION des pistes impossible ({ex!r}) : chaque piste prend la voix du morceau")
                for p in e['pistes']:
                    p['affectation'] = {s['i']: (s['cible'], s['decalage']) for s in e['segs'] if not s['muet']}
                e['rapport']['affectation'] = {'erreur': repr(ex)[:300], 'message': 'chaque piste prend la voix du morceau'}
        if e.get('plusieurs') and versions_mode != 'non' and e['mode'] == 'chant':   # parole : pas de versions (N conversions)
            try:
                preparer_versions(e)
            except Exception as ex:  # noqa: BLE001 — sans deux versions : le doublage automatique, comme avant
                import traceback
                traceback.print_exc()
                e['versions'] = None
                e['rapport']['versions'] = {'mode': versions_mode, 'erreur': repr(ex)[:300]}
        e['variantes'] = variantes_de(e)
    # 6. Paroles (avant la fin de chaque vidéo : la voix principale sèche est effacée avec ses fichiers de travail). Leur
    # piste est prête dès le nettoyage : sur plusieurs cartes, la dernière les fait pendant que les autres convertissent,
    # puis rejoint la conversion ; une carte : après la conversion, comme avant
    paroles_avant = None
    if R.get('paroles', True) and cartes > 1:
        try:
            paroles_avant = preparer_paroles(principaux)
        except Exception as ex:  # noqa: BLE001 — sans paroles : le doublage continue
            import traceback
            traceback.print_exc()
            log(f'PAROLES impossibles : {ex!r}')
            paroles_avant = (time.time(), None)
    try:
        ying = convertir_ying(etats, paroles_avant and paroles_avant[1])
    except Exception as ex:  # noqa: BLE001 — repli sur Seed-VC 44 kHz
        import traceback
        traceback.print_exc()
        ying = {'erreur': repr(ex)}
    if R.get('paroles', True):
        try:
            if paroles_avant is None:
                transcrire(principaux)
            elif paroles_avant[1] is not None:
                finir_paroles(principaux, paroles_avant[1], ying.pop('avant', None))
            partager('paroles', ('paroles_s',))
            for e in etats:   # voix à comparer : les mêmes paroles dans le dossier de chaque voix
                p = principal[e['tr'].get('id_video', e['tr']['id'])]
                src = os.path.join(p['tr']['dossier'], 'paroles.json')
                if p is not e and os.path.exists(src):
                    shutil.copy2(src, os.path.join(e['tr']['dossier'], 'paroles.json'))
        except Exception as ex:  # noqa: BLE001 — sans paroles : le doublage continue
            import traceback
            traceback.print_exc()
            log(f'PAROLES impossibles : {ex!r}')
    else:
        for e in etats:
            e['rapport']['paroles'] = {'demande': False}
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
    if len(sys.argv) >= 3 and sys.argv[1] in ('nettoyer', 'ying', 'paroles'):
        etape_isolee(sys.argv[1], sys.argv[2])
    else:
        main()
