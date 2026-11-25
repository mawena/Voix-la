"""File des doublages et des voix : un seul travail à la fois, doubler.py lancé en sous-processus (nice -n 10).

Coût compté prudemment : durée de l'étape « kaggle » (calcul sur Modal) x prix horaire de la carte la plus chère
de la liste par défaut (B200, 6,25 $/h). Les budgets (global et par compte) sont vérifiés avant chaque lancement.
"""
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

PRIX_MAX_USD_H = 6.25
COUT_PAR_DEFAUT = 0.5   # estimation avant le premier événement « kaggle »


class Travaux:
    def __init__(self, conf, base):
        self.conf, self.base = conf, base
        self.file = queue.Queue()
        self.actuel = None   # (sorte, id, Popen)
        self.verrou = threading.Lock()
        # un redémarrage interrompt les travaux en cours : on les remet en file
        base.faire("UPDATE doublages SET etat = 'en_file', debut = NULL WHERE etat = 'en_cours'")
        base.faire("UPDATE voix SET etat = 'en_file' WHERE etat = 'en_cours'")
        for v in base.tous("SELECT id FROM voix WHERE etat = 'en_file' ORDER BY id"):
            self.file.put(('voix', v['id']))
        for d in base.tous("SELECT id FROM doublages WHERE etat = 'en_file' ORDER BY id"):
            self.file.put(('doublage', d['id']))
        if not conf.essai:
            threading.Thread(target=self._boucle, daemon=True, name='travaux').start()

    # --- Dossiers ---
    def dossier_compte(self, compte):
        d = self.conf.donnees / 'comptes' / str(compte)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def dossier_doublage(self, d):
        return self.dossier_compte(d['compte']) / 'doublages' / str(d['id'])

    # --- Ajouts ---
    def ajouter_voix(self, vid):
        self.file.put(('voix', vid))

    def ajouter_doublage(self, did):
        self.file.put(('doublage', did))

    def annuler(self, did):
        """Annule un doublage en file ou en cours."""
        self.base.faire("UPDATE doublages SET etat = 'annule', fin = ? WHERE id = ? AND etat IN ('en_file', 'en_cours')",
                        time.time(), did)
        with self.verrou:
            if self.actuel and self.actuel[:2] == ('doublage', did):
                self.actuel[2].terminate()

    def position(self, did):
        """Rang dans la file (1 = le prochain), 0 s'il tourne, None sinon."""
        d = self.base.un('SELECT etat FROM doublages WHERE id = ?', did)
        if not d or d['etat'] not in ('en_file', 'en_cours'):
            return None
        if d['etat'] == 'en_cours':
            return 0
        return self.base.un("SELECT COUNT(*) FROM doublages WHERE etat = 'en_file' AND id <= ?", did)[0]

    def budget_restant(self, compte):
        c = self.base.compte(compte)
        perso = c['budget_usd'] if c and c['budget_usd'] is not None else self.conf.budget_compte_usd
        return round(min(perso - self.base.depense_du_mois(compte),
                         self.conf.budget_mensuel_usd - self.base.depense_du_mois()), 2)

    # --- Boucle ---
    def _boucle(self):
        while True:
            sorte, id_ = self.file.get()
            try:
                (self._voix if sorte == 'voix' else self._doublage)(id_)
            except Exception as e:   # noqa: BLE001 — la file ne doit jamais s'arrêter
                print(f'[travaux] {sorte} {id_} : {e!r}', flush=True)
                table = 'voix' if sorte == 'voix' else 'doublages'
                self.base.faire(f"UPDATE {table} SET etat = 'erreur', message = ? WHERE id = ?", str(e)[:500], id_)
            finally:
                with self.verrou:
                    self.actuel = None

    def _lancer(self, sorte, id_, args, journal):
        cmd = ['nice', '-n', '10', self.conf.python, str(self.conf.depot / 'doubler.py'), *args]
        env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
        with open(journal, 'a', encoding='utf-8') as j:
            p = subprocess.Popen(cmd, cwd=self.conf.depot, stdout=j, stderr=subprocess.STDOUT, env=env,
                                 stdin=subprocess.DEVNULL)
            with self.verrou:
                self.actuel = (sorte, id_, p)
            return p

    def _voix(self, vid):
        v = self.base.un('SELECT * FROM voix WHERE id = ?', vid)
        if not v or v['etat'] != 'en_file':
            return
        dc = self.dossier_compte(v['compte'])
        enr = next((f for f in sorted((dc / 'enregistrements').glob(f'{vid}.*')) if f.suffix != '.journal'), None)
        if not enr:
            raise RuntimeError('enregistrement introuvable')
        self.base.faire("UPDATE voix SET etat = 'en_cours' WHERE id = ?", vid)
        p = self._lancer('voix', vid, ['--donnees', str(dc / 'donnees'), '--ajouter-voix', v['nom'], str(enr)],
                         dc / 'enregistrements' / f'{vid}.journal')
        code = p.wait()
        if code == 0:
            self.base.faire("UPDATE voix SET etat = 'prete', message = NULL WHERE id = ?", vid)
            enr.unlink(missing_ok=True)
        else:
            self.base.faire("UPDATE voix SET etat = 'erreur', message = ? WHERE id = ?",
                            _fin_journal(dc / 'enregistrements' / f'{vid}.journal'), vid)

    def _doublage(self, did):
        d = self.base.un('SELECT * FROM doublages WHERE id = ?', did)
        if not d or d['etat'] != 'en_file':
            return
        # le doublage est déjà compté (estimé) dans la dépense du mois : il faut juste ne pas être en négatif
        if self.budget_restant(d['compte']) < 0:
            self.base.faire("UPDATE doublages SET etat = 'erreur', message = ?, fin = ? WHERE id = ?",
                            'budget du mois atteint', time.time(), did)
            return
        dd = self.dossier_doublage(d)
        ev = dd / 'evenements.jsonl'
        ev.unlink(missing_ok=True)
        self.base.faire("UPDATE doublages SET etat = 'en_cours', debut = ?, message = NULL WHERE id = ?", time.time(), did)
        dc = self.dossier_compte(d['compte'])
        p = self._lancer('doublage', did, ['--calcul', 'modal', '--donnees', str(dc / 'donnees'), '--voix', d['voix'],
                                           '--mode', d['mode'], '--sortie', str(dd / 'sortie'), '--evenements', str(ev),
                                           str(dd / d['fichier'])], dd / 'journal.txt')
        lu = 0
        while p.poll() is None:
            time.sleep(2)
            lu = self._suivre(did, ev, lu)
        self._suivre(did, ev, lu)
        etat = self.base.un('SELECT etat, sortie, cout_usd FROM doublages WHERE id = ?', did)
        if etat['etat'] == 'annule':
            return
        if p.returncode == 0 and etat['sortie']:
            self.base.faire("UPDATE doublages SET etat = 'fini', fin = ?, message = NULL WHERE id = ?", time.time(), did)
            (dd / d['fichier']).unlink(missing_ok=True)   # la vidéo d'origine n'est plus utile
            shutil.rmtree(dd / 'sortie' / 'travail', ignore_errors=True)
        else:
            msg = self.base.un('SELECT message FROM doublages WHERE id = ?', did)['message']
            self.base.faire("UPDATE doublages SET etat = 'erreur', fin = ?, message = ? WHERE id = ?", time.time(),
                            msg or _fin_journal(dd / 'journal.txt'), did)

    def _suivre(self, did, ev, lu):
        """Lit les nouveaux événements : étape, coût estimé puis compté, sortie, erreur."""
        if not ev.is_file():
            return lu
        with open(ev, encoding='utf-8') as f:
            f.seek(lu)
            lignes = f.readlines()
            lu = f.tell()
        for l in lignes:
            try:
                e = json.loads(l)
            except ValueError:
                continue
            t = e.get('type')
            if t == 'etape':
                txt = e.get('etape', '') + (f" : {e['message']}" if e.get('message') else '')
                self.base.faire('UPDATE doublages SET message = ? WHERE id = ?', txt[:300], did)
                if e.get('etape') == 'kaggle' and e.get('etat') in ('fin', 'erreur') and e.get('duree_s'):
                    cout = round(e['duree_s'] / 3600 * PRIX_MAX_USD_H, 2)
                    self.base.faire('UPDATE doublages SET cout_usd = COALESCE(cout_usd, 0) + ? WHERE id = ?', cout, did)
            elif t == 'kaggle' and e.get('cout_estime_usd') is not None:
                self.base.faire('UPDATE doublages SET cout_estime_usd = ? WHERE id = ?', e['cout_estime_usd'], did)
            elif t == 'sortie':
                f = e.get('mp4') or e.get('mp3')
                if f:
                    rel = os.path.relpath(f, self.dossier_doublage({'compte': self._compte(did), 'id': did}))
                    self.base.faire('UPDATE doublages SET sortie = ? WHERE id = ?', rel, did)
            elif t == 'erreur':
                self.base.faire('UPDATE doublages SET message = ? WHERE id = ?', str(e.get('message'))[:500], did)
        return lu

    def _compte(self, did):
        return self.base.un('SELECT compte FROM doublages WHERE id = ?', did)['compte']


def _fin_journal(chemin, n=3):
    try:
        lignes = [l.strip() for l in Path(chemin).read_text(errors='replace').splitlines() if l.strip()]
        return ' / '.join(lignes[-n:])[:500] or 'échec'
    except OSError:
        return 'échec'
