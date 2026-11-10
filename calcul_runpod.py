"""Doubler sur un pod Runpod (RTX 4090, payant) : runpodctl 3 pour le pod, ssh et scp pour le reste.

Le pod coûte tant qu'il existe : il est donc TOUJOURS supprimé.
1. « finally » côté PC : le pod créé est supprimé quoi qu'il arrive (erreur, Ctrl-C, SIGTERM de l'interface), avec
   plusieurs essais et une vérification ;
2. minuterie dans le pod, posée avant tout envoi : après une durée maximale, le pod se supprime lui-même
   (runpodctl et $RUNPOD_POD_ID), même si le PC est éteint ;
3. les pods s'appellent « voix-la-<date>-<hasard> » et « doubler --nettoyer-runpod » supprime les restes de ce nom,
   jamais d'autres pods.
La référence de la voix et le son restent sur le pod seulement ; ils sont effacés à la fin, puis le pod est supprimé.
Ni la clé Runpod (~/.runpod/config.toml, lue par runpodctl seul), ni la clé privée SSH ne sont lues ici.
"""
import datetime
import json
import os
import secrets
import shlex
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import calcul

RUNPODCTL = Path(os.environ['VOIXLA_RUNPODCTL']).expanduser() if os.environ.get('VOIXLA_RUNPODCTL') \
    else Path.home() / '.local' / 'bin' / 'runpodctl'
CONFIG_RUNPOD = Path.home() / '.runpod' / 'config.toml'
CLE_PUBLIQUE = Path.home() / '.ssh' / 'id_rsa.pub'
CLE_PRIVEE = Path.home() / '.ssh' / 'id_rsa'
IMAGE = 'runpod/pytorch:1.0.3-cu1281-torch291-ubuntu2404'
ESPACE = '/workspace/voixla'
DISQUE_GO = 80
SUIVI_S = 30
PANNE_SSH_MAXI_S = 20 * 60

# Dans le pod (sh) : sous-commandes minuterie, demarrer, etat, effacer. VOIXLA_POD déplace l'espace de travail et
# VOIXLA_POD_SANS_INSTALL saute l'installation (essais seulement).
SCRIPT_POD = r'''#!/bin/sh
W="${VOIXLA_POD:-/workspace/voixla}"
cmd="$1"; shift
case "$cmd" in
minuterie)
  cat > "$W/minuterie.sh" <<'EOT'
#!/bin/sh
[ -r /etc/rp_environment ] && . /etc/rp_environment
for v in RUNPOD_POD_ID RUNPOD_API_KEY; do
  eval "val=\${$v}"
  if [ -z "$val" ]; then val=$(tr '\0' '\n' < /proc/1/environ | sed -n "s/^$v=//p"); export "$v=$val"; fi
done
sleep "$1"
runpodctl pod delete "$RUNPOD_POD_ID" || runpodctl remove pod "$RUNPOD_POD_ID"
EOT
  setsid nohup sh "$W/minuterie.sh" "$1" > "$W/minuterie.log" 2>&1 < /dev/null &
  echo "minuterie : suppression du pod dans $1 s" ;;
demarrer)
  index="$1"; shift
  rm -f "$W/fini" "$W/fini.tmp"; mkdir -p "$W/sortie" "$W/entrees"
  mv "$W"/jeu/* "$W/entrees/" 2>/dev/null
  setsid nohup sh -c '
    W="$1"; index="$2"; shift 2
    {
      if [ -z "$VOIXLA_POD_SANS_INSTALL" ]; then
        echo "installation : ffmpeg, git, environnement Python"
        apt-get update -qq && apt-get install -y -qq ffmpeg git libsndfile1 python3-venv
        python3 -m venv "$W/venv" && "$W/venv/bin/pip" install -q --extra-index-url "$index" "$@"
        PY="$W/venv/bin/python"
      else
        PY="${VOIXLA_POD_PYTHON:-python3}"
      fi
      DOUBLER_ENTREES="$W/entrees" DOUBLER_SORTIE="$W/sortie" DOUBLER_DEBUT="$(date +%s)" PYTHONUNBUFFERED=1 \
        "$PY" "$W/noyau.py"
      echo $? > "$W/fini.tmp"
    } >> "$W/noyau.log" 2>&1
    [ -f "$W/fini.tmp" ] || echo 1 > "$W/fini.tmp"
    mv "$W/fini.tmp" "$W/fini"
  ' sh "$W" "$index" "$@" > /dev/null 2>&1 < /dev/null &
  echo "noyau lancé" ;;
etat)
  if [ -f "$W/fini" ]; then echo "fini $(cat "$W/fini")"; else echo en_cours; fi
  tail -n 5 "$W/noyau.log" 2>/dev/null ;;
effacer)
  rm -rf "$W/entrees" "$W/jeu" "$W/noyau.py" "$W/sortie" ; echo effacé ;;
*) echo "commande inconnue : $cmd" >&2; exit 2 ;;
esac
'''


def ssh_cmd():
    return os.environ.get('VOIXLA_SSH') or 'ssh'


def scp_cmd():
    return os.environ.get('VOIXLA_SCP') or 'scp'


def pret():
    """(prêt, raison) : runpodctl installé, clé Runpod présente (fichier de runpodctl, jamais lu ici), clé SSH."""
    if not RUNPODCTL.exists():
        return False, f'runpodctl absent ({RUNPODCTL})'
    if not (CONFIG_RUNPOD.exists() or os.environ.get('RUNPOD_API_KEY') or os.environ.get('VOIXLA_RUNPOD_CLE') == 'present'):
        return False, 'clé Runpod absente : lancer « runpodctl doctor »'
    if not CLE_PUBLIQUE.exists() and not os.environ.get('VOIXLA_SSH_CLE'):
        return False, f'clé SSH publique absente ({CLE_PUBLIQUE})'
    return True, ''


def runpodctl(*args, timeout=300):
    """(code, texte) d'une commande runpodctl ; 124 si elle ne répond pas."""
    try:
        r = subprocess.run([str(RUNPODCTL), *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f'runpodctl {" ".join(args[:2])} : pas de réponse en {timeout} s'
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def nom_pod():
    return 'voix-la-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(2)


def options_ssh(port):
    cle = os.environ.get('VOIXLA_SSH_CLE') or str(CLE_PRIVEE)
    return ['-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR',
            '-o', 'ServerAliveInterval=30', '-o', 'ConnectTimeout=20', '-o', 'BatchMode=yes', '-i', cle, *port]


def ssh(hote, port, commande, timeout=120):
    """(code, texte) d'une commande dans le pod ; 124 si elle ne répond pas."""
    try:
        r = subprocess.run([ssh_cmd(), *options_ssh(['-p', str(port)]), f'root@{hote}', commande], capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, 'ssh : pas de réponse'
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def scp(hote, port, sources, destination, timeout=3600, recursif=True):
    """scp vers le pod (destination « pod:… ») ou depuis lui (sources « pod:… ») ; (code, texte)."""
    cmd = [scp_cmd(), *options_ssh(['-P', str(port)]), *(['-r'] if recursif else []),
           *[s.replace('pod:', f'root@{hote}:', 1) for s in sources], destination.replace('pod:', f'root@{hote}:', 1)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, 'scp : pas de réponse'
    return r.returncode, (r.stdout or '') + (r.stderr or '')


# --- Vie du pod ------------------------------------------------------------------------------------------------------

def creer_pod(nom, gpu, cartes, dire):
    """Crée le pod (image PyTorch officielle, SSH sur 22/tcp) et attend son SSH. Rend (id, hôte, port). L'id est cherché
    aussi dans l'erreur : un pod créé mais pas prêt à temps existe, et doit être supprimé."""
    pub = os.environ.get('VOIXLA_SSH_PUB') or (CLE_PUBLIQUE.read_text().strip() if CLE_PUBLIQUE.exists() else '')
    args = ['pod', 'create', '--name', nom, '--image', IMAGE, '--gpu-id', gpu, '--gpu-count', str(cartes),
            '--ports', '22/tcp', '--container-disk-in-gb', str(DISQUE_GO), '--ssh', '--min-cuda-version', '12.8',
            '--env', json.dumps({'PUBLIC_KEY': pub}), '--wait', '--wait-timeout', '15m']
    dire(f'Runpod : création du pod {nom} ({gpu} x {cartes})')
    code, texte = runpodctl(*args, timeout=1200)
    pod_id = calcul.identifiant_pod(texte)
    if code or not pod_id:
        raise PodCree(pod_id, f'création du pod refusée ou trop longue (code {code}) : {texte.strip()[-300:]}')
    hote_port = calcul.hote_ssh(texte)
    if not hote_port:
        code, info = runpodctl('ssh', 'info', pod_id, timeout=60)
        hote_port = calcul.hote_ssh(info)
    if not hote_port:
        raise PodCree(pod_id, 'adresse SSH du pod introuvable (runpodctl ssh info)')
    return pod_id, hote_port[0], hote_port[1]


class PodCree(RuntimeError):
    """Création en échec ; pod_id : le pod peut quand même exister (à supprimer)."""

    def __init__(self, pod_id, message):
        super().__init__('Runpod : ' + message)
        self.pod_id = pod_id


def supprimer_pod(pod_id, dire, essais=4):
    """Supprime le pod (plusieurs essais) puis vérifie qu'il n'est plus listé. Rend True si c'est fait ; sinon dit
    comment le faire à la main."""
    for i in range(essais):
        code, texte = runpodctl('pod', 'delete', pod_id, timeout=120)
        if code == 0 or 'not found' in texte.lower() or 'introuvable' in texte.lower():
            code2, liste = runpodctl('pod', 'list', '--all', timeout=120)
            if code2 != 0 or pod_id not in [p for p, _ in _tous(liste)]:
                dire(f'Runpod : pod {pod_id} supprimé')
                return True
        time.sleep(min(30, 3 * (i + 1)) if not os.environ.get('VOIXLA_RUNPOD_VITE') else 0)
    dire(f'ATTENTION : le pod Runpod {pod_id} n\'a pas pu être supprimé : IL COÛTE TANT QU\'IL EXISTE. Lancer '
         f'« doubler --nettoyer-runpod », ou « runpodctl pod delete {pod_id} », ou la console Runpod ; sa minuterie '
         'le supprimera aussi.')
    return False


def _tous(liste):
    j = calcul.json_ou_none(liste)
    if isinstance(j, dict):
        j = next((v for v in j.values() if isinstance(v, list)), [])
    return [(str(p.get('id')), str(p.get('name') or '')) for p in j or [] if isinstance(p, dict) and p.get('id')]


def nettoyer(dire=print):
    """Supprime les pods « voix-la-… » restants (jamais les autres) ; rend la liste des identifiants supprimés."""
    code, texte = runpodctl('pod', 'list', '--all', timeout=120)
    if code:
        raise RuntimeError('Runpod : liste des pods impossible : ' + texte.strip()[-300:])
    restes = calcul.pods_voix_la(texte)
    if not restes:
        dire('Runpod : aucun pod voix-la-… à supprimer')
    supprimes = []
    for pod_id, nom in restes:
        dire(f'Runpod : suppression de {nom} ({pod_id})')
        if supprimer_pod(pod_id, dire):
            supprimes.append(pod_id)
    return supprimes


# --- Le calcul -------------------------------------------------------------------------------------------------------

def poser_minuterie(hote, port, duree_max_s, dire):
    """Le pod se supprimera seul après duree_max_s (posée avant tout envoi)."""
    with tempfile.TemporaryDirectory(prefix='voixla-pod-') as tmp:
        f = Path(tmp) / 'pod.sh'
        f.write_text(SCRIPT_POD)
        code, texte = ssh(hote, port, f'mkdir -p {ESPACE}')
        if code:
            raise RuntimeError('Runpod : SSH refusé par le pod : ' + texte.strip()[-300:])
        code, texte = scp(hote, port, [str(f)], f'pod:{ESPACE}/', recursif=False)
        if code:
            raise RuntimeError('Runpod : envoi du script au pod refusé : ' + texte.strip()[-300:])
    code, texte = ssh(hote, port, f'sh {ESPACE}/pod.sh minuterie {int(duree_max_s)}')
    if code:
        raise RuntimeError('Runpod : minuterie du pod non posée : ' + texte.strip()[-300:])
    dire(f'Runpod : minuterie posée, le pod se supprimera seul après {int(duree_max_s) // 60} min')


def envoyer(hote, port, script, jeu, dire):
    code, texte = scp(hote, port, [str(script)], f'pod:{ESPACE}/noyau.py', recursif=False)
    if code:
        raise RuntimeError('Runpod : envoi du noyau refusé : ' + texte.strip()[-300:])
    if jeu and any(Path(jeu).iterdir()):
        code, texte = scp(hote, port, [str(jeu)], f'pod:{ESPACE}/')
        if code:
            raise RuntimeError('Runpod : envoi du son refusé : ' + texte.strip()[-300:])


def demarrer(hote, port):
    code, texte = ssh(hote, port, f"sh {ESPACE}/pod.sh demarrer {shlex.quote(calcul.INDEX_TORCH)} "
                                  + ' '.join(shlex.quote(p) for p in calcul.paquets_noyau()))
    if code:
        raise RuntimeError('Runpod : noyau non lancé : ' + texte.strip()[-300:])


def suivre(hote, port, duree_max_s, dire, sur_etat=None, pas_s=None):
    """Suit le noyau dans le pod jusqu'à sa fin ; rend (code du noyau, dernières lignes). Coupure SSH tolérée
    PANNE_SSH_MAXI_S ; au-delà de duree_max_s : RuntimeError (le pod sera supprimé)."""
    pas = SUIVI_S if pas_s is None else pas_s
    debut, panne, vu = time.time(), None, ''
    while True:
        code, texte = ssh(hote, port, f'sh {ESPACE}/pod.sh etat', timeout=90)
        maintenant = time.time()
        if code:
            panne = panne or maintenant
            if maintenant - panne > PANNE_SSH_MAXI_S:
                raise RuntimeError('Runpod : pod injoignable par SSH depuis plus de 20 min')
        else:
            panne = None
            premiere, _, reste = texte.partition('\n')
            if reste.strip() and reste != vu:
                vu = reste
                dire('Runpod : ' + reste.strip().splitlines()[-1][:300])
            if premiere.startswith('fini'):
                return int(premiere.split()[1]) if premiere.split()[1:] and premiere.split()[1].isdigit() else 1, reste
            if sur_etat:
                sur_etat('en_cours')
        if maintenant - debut > duree_max_s:
            raise RuntimeError(f'Runpod : noyau toujours en cours après {duree_max_s // 60} min : pod supprimé')
        time.sleep(pas)


def rapatrier(hote, port, retour, dire):
    """Sorties du noyau (<espace>/sortie/ et son journal) dans retour, avec le journal au format des journaux Kaggle."""
    Path(retour).mkdir(parents=True, exist_ok=True)
    code, texte = scp(hote, port, [f'pod:{ESPACE}/sortie/.'], str(retour))
    if code:
        raise RuntimeError('Runpod : rapatriement des sorties refusé : ' + texte.strip()[-300:])
    journal = []
    with tempfile.TemporaryDirectory(prefix='voixla-pod-') as tmp:
        code, _ = scp(hote, port, [f'pod:{ESPACE}/noyau.log'], str(Path(tmp) / 'noyau.log'), recursif=False)
        if code == 0:
            journal = (Path(tmp) / 'noyau.log').read_text(errors='replace').splitlines()
    (Path(retour) / 'doublage-runpod.log').write_text(
        json.dumps([{'stream_name': 'stdout', 'time': i, 'data': l + '\n'} for i, l in enumerate(journal)]))
    return journal


def lancer(ctx, dire=print, sur_etat=None, apres_creation=None):
    """Pod, envoi, calcul, rapatriement, suppression. ctx : script, jeu, retour, gpu (identifiant Runpod), cartes,
    duree_max_min. Rend {'code', 'journal', 'pod'}. Le pod est supprimé dans tous les cas."""
    ok, raison = pret()
    if not ok:
        raise RuntimeError('Runpod : ' + raison)
    nom, pod_id = nom_pod(), None
    duree_max_s = int(ctx['duree_max_min']) * 60
    precedent = signal.getsignal(signal.SIGTERM) if threading.current_thread() is threading.main_thread() else None
    if precedent is not None:
        def arreter(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, arreter)
    try:
        try:
            pod_id, hote, port = creer_pod(nom, ctx['gpu'], ctx.get('cartes', 1), dire)
        except PodCree as e:
            pod_id = e.pod_id
            raise
        if apres_creation:
            apres_creation({'pod_id': pod_id, 'pod_nom': nom, 'hote': hote, 'port': port,
                            'duree_max_s': duree_max_s, 'cree_epoch': time.time()})
        poser_minuterie(hote, port, duree_max_s, dire)
        envoyer(hote, port, ctx['script'], ctx.get('jeu'), dire)
        demarrer(hote, port)
        if sur_etat:
            sur_etat('en_cours')
        code, _ = suivre(hote, port, duree_max_s, dire, sur_etat, ctx.get('suivi_s'))
        journal = rapatrier(hote, port, ctx['retour'], dire)
        ssh(hote, port, f'sh {ESPACE}/pod.sh effacer')   # le son et les références ne restent pas sur le pod
        return {'code': code, 'journal': journal, 'pod': pod_id}
    finally:
        try:
            if pod_id:
                supprimer_pod(pod_id, dire)
            else:
                _nettoyer_nom(nom, dire)
        finally:
            if precedent is not None:
                signal.signal(signal.SIGTERM, precedent)


def _nettoyer_nom(nom, dire):
    """Création sans identifiant lisible : le pod de ce nom, s'il existe, est supprimé."""
    try:
        code, texte = runpodctl('pod', 'list', '--all', timeout=120)
        for pod_id, n in _tous(texte):
            if n == nom:
                supprimer_pod(pod_id, dire)
    except Exception as e:  # noqa: BLE001
        dire(f'ATTENTION : vérification du pod {nom} impossible ({e}) : lancer « doubler --nettoyer-runpod »')


def reprendre(info, retour, dire=print, sur_etat=None):
    """Reprend la session d'un pod noté (pod_id, hote, port, duree_max_s, cree_epoch) : si le noyau tourne encore ou
    a fini, le suit, rapatrie et supprime le pod ; si le pod n'existe plus (minuterie), lève RuntimeError. Le pod est
    supprimé dans tous les cas."""
    pod_id = info['pod_id']
    code, texte = runpodctl('pod', 'get', pod_id, timeout=120)
    if code:
        raise RuntimeError(f'Runpod : le pod {pod_id} n\'existe plus (supprimé par sa minuterie ?) : la session est perdue ; '
                           'refaire le doublage')
    reste = max(300, int(info['cree_epoch'] + info['duree_max_s'] - time.time()))
    try:
        code, _ = suivre(info['hote'], info['port'], reste, dire, sur_etat)
        journal = rapatrier(info['hote'], info['port'], retour, dire)
        ssh(info['hote'], info['port'], f'sh {ESPACE}/pod.sh effacer')
        return {'code': code, 'journal': journal, 'pod': pod_id}
    finally:
        supprimer_pod(pod_id, dire)
