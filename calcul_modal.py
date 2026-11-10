"""Doubler sur Modal (côté PC, sans le module modal) : lance calcul_modal_hote.py avec le Python du client modal.

Le client modal vit dans son propre environnement (~/modal-venv, ou $VOIXLA_MODAL_PYTHON). Le jeton (« modal setup »)
est dans ~/.modal.toml : ce module n'en lit jamais le contenu, seulement la présence. L'application est éphémère : elle
s'arrête avec ce processus (Ctrl-C, SIGTERM de l'interface, PC éteint), donc rien ne reste à facturer ni à nettoyer ; une
session Modal interrompue ne se reprend pas (« doubler --reprendre » le dit et la clôt). Voir calcul_modal_hote.py.
"""
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

HOTE = Path(__file__).resolve().parent / 'calcul_modal_hote.py'
JETON = Path.home() / '.modal.toml'


def python_modal():
    return Path(os.environ['VOIXLA_MODAL_PYTHON']).expanduser() if os.environ.get('VOIXLA_MODAL_PYTHON') \
        else Path.home() / 'modal-venv' / 'bin' / 'python'


def jeton_present():
    """Le jeton Modal est-il là ? (fichier ~/.modal.toml, ou MODAL_TOKEN_ID et MODAL_TOKEN_SECRET ; jamais lus)"""
    if os.environ.get('VOIXLA_MODAL_JETON') == 'absent':
        return False
    return JETON.exists() or bool(os.environ.get('MODAL_TOKEN_ID') and os.environ.get('MODAL_TOKEN_SECRET')) \
        or os.environ.get('VOIXLA_MODAL_JETON') == 'present'


def pret():
    """(prêt, raison) : le client modal est installé et le jeton présent."""
    if not python_modal().exists():
        return False, f'client modal absent ({python_modal()}) : python3 -m venv ~/modal-venv && ~/modal-venv/bin/pip install modal'
    if not jeton_present():
        return False, 'jeton Modal absent : lancer « ~/modal-venv/bin/modal setup »'
    return True, ''


def ecrire_journal(retour, lignes):
    """Journal du noyau au format des journaux Kaggle (liste JSON de {data}), que le rapatriement et ranger_retour lisent."""
    Path(retour).mkdir(parents=True, exist_ok=True)
    (Path(retour) / 'doublage-modal.log').write_text(
        json.dumps([{'stream_name': 'stdout', 'time': i, 'data': l + '\n'} for i, l in enumerate(lignes)]))


def lancer(ctx, dire=print, sur_etat=None):
    """Envoie, calcule et rapatrie sur Modal. ctx : script (fichier du noyau), jeu (dossier du son et des références, ou
    None), retour (dossier des sorties), gpu (« L4 »…), timeout_s, mode (« doublage » ou « outils »).
    Rend {'code': code du noyau (0 : réussi), 'journal': lignes, 'etats': {mot: heure}} ; lève RuntimeError si le client
    modal échoue avant d'avoir rendu quoi que ce soit."""
    ok, raison = pret()
    if not ok:
        raise RuntimeError('Modal : ' + raison)
    retour = Path(ctx['retour'])
    spec = {'script': str(ctx['script']), 'jeu': str(ctx['jeu']) if ctx.get('jeu') else None, 'retour': str(retour),
            'gpu': ctx['gpu'], 'timeout_s': int(ctx['timeout_s']), 'mode': ctx.get('mode', 'doublage')}
    retour.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='voixla-modal-') as tmp:
        fichier = Path(tmp) / 'spec.json'
        fichier.write_text(json.dumps(spec))
        dire(f"Modal : application éphémère, carte {ctx['gpu']}, durée maximale {spec['timeout_s'] // 60} min")
        proc = subprocess.Popen([str(python_modal()), str(HOTE), str(fichier)], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1, cwd=tmp)
        journal, etats = [], {}
        precedent = signal.getsignal(signal.SIGTERM) if _principal() else None
        if _principal():
            def arreter(*_):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, arreter)
        try:
            for ligne in proc.stdout:
                ligne = ligne.rstrip('\n')
                if ligne.startswith('@@ETAT '):
                    mot = ligne[7:].strip()
                    etats[mot] = time.time()
                    dire(f'Modal : {mot}')
                    if sur_etat:
                        sur_etat(mot)
                    continue
                journal.append(ligne)
                if ligne.strip():
                    dire('Modal : ' + ligne[:300])
            code = proc.wait()
        except BaseException:
            _arreter(proc, dire)   # l'application éphémère s'arrête avec le client : le volume éphémère est supprimé
            raise
        finally:
            proc.stdout.close()
            if precedent is not None:
                signal.signal(signal.SIGTERM, precedent)
    ecrire_journal(retour, journal)
    resultat = {}
    if (retour / 'modal.json').is_file():
        resultat = json.loads((retour / 'modal.json').read_text())
        (retour / 'modal.json').unlink()
    if code != 0 and 'code' not in resultat:
        raise RuntimeError(f'Modal : le client a échoué (code {code}) ; fin du journal :\n' + '\n'.join(journal[-15:]))
    return {'code': resultat.get('code', 1), 'journal': journal, 'etats': etats}


def _principal():
    import threading
    return threading.current_thread() is threading.main_thread()


def _arreter(proc, dire):
    """Arrête proprement le client modal : SIGINT (il stoppe l'application et supprime le volume éphémère), puis, passé
    90 s, la force."""
    if proc.poll() is not None:
        return
    dire('Modal : arrêt de l\'application éphémère')
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=90)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
