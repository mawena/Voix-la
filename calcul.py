"""Moyens de calcul de doubler : où tourne le noyau GPU (kaggle/doubler_kaggle.py), sans PyTorch ni réseau ici.

- kaggle : le GPU gratuit de Kaggle (défaut) ;
- modal  : Modal (crédit gratuit mensuel), application éphémère, modèles dans un volume public (calcul_modal.py) ;
- runpod : un pod Runpod RTX 4090 payant, toujours supprimé à la fin (calcul_runpod.py) ;
- auto   : Kaggle d'abord, puis Modal si Kaggle n'est pas disponible. Runpod n'est jamais automatique (payant).

Ce module garde ce que les moyens ont en commun : les choix, les cartes et leurs prix, l'estimation de durée et de coût,
les versions de bibliothèques du noyau (celles de Kaggle), la lecture tolérante de ce que renvoient les outils.
"""
import json
import re

CALCULS = ('kaggle', 'modal', 'runpod', 'auto')
CALCUL_DEFAUT = 'kaggle'
ATTENTE_FILE_MIN_DEFAUT = 20   # « auto » : Kaggle toujours en file (QUEUED) au-delà de cette durée -> Modal

# Bibliothèques du noyau Kaggle (relevé du 09/11/2026 : Python 3.13, torch 2.11.0+cu130) : Modal et Runpod installent les
# mêmes versions (torch en cu128, compatible avec plus de pilotes)
PYTHON_NOYAU = '3.13'
VERSIONS_NOYAU = {'torch': '2.11.0', 'torchaudio': '2.11.0', 'numpy': '2.1.3', 'librosa': '0.11.0',
                  'transformers': '5.20.2', 'huggingface_hub': '1.33.0', 'soundfile': '0.14.0'}
AUTRES_PAQUETS = ('scipy', 'einops', 'pyyaml', 'tqdm', 'matplotlib', 'munch')
INDEX_TORCH = 'https://download.pytorch.org/whl/cu128'


def paquets_noyau():
    """Spécifications pip (nom==version) des bibliothèques du noyau, puis les autres, sans version."""
    return [f'{n}=={v}' for n, v in VERSIONS_NOYAU.items()] + list(AUTRES_PAQUETS)


# --- Cartes et prix --------------------------------------------------------------------------------------------------
# (dollars par heure et par carte, vitesse relative à une T4 de Kaggle) : estimations d'après les tarifs affichés et les
# débits des cartes, PAS des mesures ; à corriger après les premiers essais réels (voir README, Temps de traitement)
CARTES_MODAL = {'T4': (0.59, 1.0), 'L4': (0.80, 1.2), 'A10': (1.10, 2.0), 'A10G': (1.10, 2.0),
                'A100-40GB': (2.10, 4.0), 'A100': (2.10, 4.0)}
GPU_MODAL_DEFAUT = 'L4'
CARTES_RUNPOD = {'NVIDIA GeForce RTX 4090': (0.69, 3.0)}
GPU_RUNPOD_DEFAUT = 'NVIDIA GeForce RTX 4090'
# Secondes avant que le noyau démarre et facturées en plus de son calcul : Modal (conteneur, montage du volume), Runpod
# (pod, SSH, installation des bibliothèques)
DEMARRAGE_S = {'modal': 60.0, 'runpod': 420.0}
FIXE_NOYAU_S = 240.0   # part de l'estimation Kaggle qui ne dépend pas de la carte (installation, chargements)
DUREE_MAX_RUNPOD_MIN = 90   # minuterie du pod, par défaut ; relevée si l'estimation est plus longue


def lire_gpu(spec, calcul):
    """(nom, cartes) d'une demande de carte : « L4 », « L4:2 » (Modal) ou un identifiant Runpod ; ValueError si illisible."""
    texte = str(spec or '').strip()
    if not texte or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,60}(:[1-8])?', texte):
        raise ValueError(f'carte GPU illisible : {spec!r}')
    nom, _, n = texte.partition(':')
    return nom, int(n or 1)


def carte(spec, calcul):
    """Description d'une carte : {'nom', 'cartes', 'prix_h' (par carte, None si inconnu), 'vitesse', 'connue'}."""
    nom, n = lire_gpu(spec, calcul)
    table = CARTES_MODAL if calcul == 'modal' else CARTES_RUNPOD
    prix, vitesse = table.get(nom, (None, 1.0))
    return {'nom': nom, 'cartes': n, 'prix_h': prix, 'vitesse': vitesse, 'connue': nom in table}


def estimer_calcul(estimation_kaggle_s, calcul, c):
    """(durée en s, coût en dollars ou None) d'une session sur la carte c, d'après l'estimation Kaggle (deux cartes T4
    pour les cartes >= 2, sinon une) : le fixe et le démarrage ne changent pas, le reste va plus vite d'autant que la
    carte est plus rapide qu'une T4."""
    fixe = min(FIXE_NOYAU_S, estimation_kaggle_s)
    gpu = (estimation_kaggle_s - fixe) / c['vitesse']
    duree = DEMARRAGE_S.get(calcul, 0.0) + fixe + gpu
    cout = None if c['prix_h'] is None else round(duree / 3600 * c['prix_h'] * c['cartes'], 2)
    return round(duree), cout


def cartes_t4_pour(c):
    """Cartes de l'estimation Kaggle à prendre pour cette carte (1 ou 2) : celle de doubler.cartes_prevues."""
    return 2 if c['cartes'] >= 2 else 1


def dollars(x):
    return 'coût inconnu' if x is None else f'{x:.2f} $'.replace('.', ',')


def lien_noyau_kaggle(noyau):
    """Page du notebook Kaggle (<compte>/<nom>), à annuler sur le site : l'outil kaggle ne sait pas l'annuler."""
    return f'https://www.kaggle.com/code/{noyau}'


# --- Lecture tolérante des sorties de runpodctl --------------------------------------------------------------------

def json_ou_none(texte):
    """Le premier objet ou tableau JSON d'un texte (runpodctl peut ajouter des lignes avant), ou None."""
    texte = texte or ''
    for i, ch in enumerate(texte):
        if ch in '[{':
            try:
                return json.JSONDecoder().raw_decode(texte[i:])[0]
            except ValueError:
                continue
    return None


def identifiant_pod(texte):
    """L'identifiant du pod créé (« id » de la sortie JSON de « runpodctl pod create »), ou None."""
    j = json_ou_none(texte)
    if isinstance(j, dict):
        for cle in ('id', 'podId', 'pod_id'):
            if j.get(cle):
                return str(j[cle])
        for v in j.values():
            if isinstance(v, dict) and v.get('id'):
                return str(v['id'])
    m = re.search(r'\b([a-z0-9]{10,})\b', texte or '') if not j else None
    return m.group(1) if m else None


def hote_ssh(texte):
    """(hôte, port) de la connexion SSH d'après « runpodctl ssh info » ou « pod get » : une commande « ssh … root@HÔTE -p
    PORT » dans le texte, ou des champs ip/host et port ; None si introuvable."""
    m = re.search(r'(?:root|[a-z_][\w-]*)@([\w.-]+)\s+(?:-p\s*(\d+)|.*?-p\s*(\d+))', texte or '')
    if m:
        return m.group(1), int(m.group(2) or m.group(3))
    m = re.search(r'-p\s*(\d+).*?@([\w.-]+)', texte or '')
    if m:
        return m.group(2), int(m.group(1))
    j = json_ou_none(texte)

    def cherche(o):
        if isinstance(o, dict):
            hote = next((o[k] for k in ('ip', 'host', 'hostname', 'publicIp', 'public_ip') if isinstance(o.get(k), str) and o[k]), None)
            port = next((o[k] for k in ('port', 'publicPort', 'public_port', 'sshPort') if str(o.get(k) or '').isdigit()), None)
            if hote and port and str(o.get('privatePort', 22)) == '22':
                return hote, int(port)
            ports = o.get('ports')
            if isinstance(ports, dict) and str(ports.get('22', '')).isdigit() and hote:
                return hote, int(ports['22'])
            for v in o.values():
                r = cherche(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = cherche(v)
                if r:
                    return r
        return None
    return cherche(j)


def pods_voix_la(texte):
    """[(id, nom)] des pods nommés « voix-la-… » dans la sortie JSON de « runpodctl pod list » : jamais les autres."""
    j = json_ou_none(texte)
    if isinstance(j, dict):
        j = next((v for v in j.values() if isinstance(v, list)), [])
    sortie = []
    for p in j or []:
        if isinstance(p, dict) and str(p.get('name') or '').startswith('voix-la-') and p.get('id'):
            sortie.append((str(p['id']), str(p['name'])))
    return sortie
