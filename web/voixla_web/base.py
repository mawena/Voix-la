"""Base SQLite : comptes, jetons (validation d'adresse, mot de passe oublié), voix et doublages."""
import datetime
import hashlib
import secrets
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS comptes (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nom TEXT NOT NULL DEFAULT '',
    mot_de_passe TEXT,              -- scrypt, NULL si connexion par Google ou Portier seulement
    email_valide INTEGER NOT NULL DEFAULT 0,
    google TEXT UNIQUE,             -- « sub » Google
    portier TEXT UNIQUE,            -- « sub » Portier
    admin INTEGER NOT NULL DEFAULT 0,
    bloque INTEGER NOT NULL DEFAULT 0,
    budget_usd REAL,                -- budget mensuel propre au compte (NULL : celui de la configuration)
    cree_le REAL NOT NULL,
    vu_le REAL
);
CREATE TABLE IF NOT EXISTS jetons (
    empreinte TEXT PRIMARY KEY,     -- sha256 du jeton envoyé par courriel
    compte INTEGER NOT NULL REFERENCES comptes(id) ON DELETE CASCADE,
    usage TEXT NOT NULL,            -- valider | reinitialiser
    expire REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS voix (
    id INTEGER PRIMARY KEY,
    compte INTEGER NOT NULL REFERENCES comptes(id) ON DELETE CASCADE,
    nom TEXT NOT NULL,
    etat TEXT NOT NULL DEFAULT 'prete',   -- en_cours | prete | erreur
    message TEXT,
    cree_le REAL NOT NULL,
    UNIQUE (compte, nom)
);
CREATE TABLE IF NOT EXISTS doublages (
    id INTEGER PRIMARY KEY,
    compte INTEGER NOT NULL REFERENCES comptes(id) ON DELETE CASCADE,
    titre TEXT NOT NULL,
    fichier TEXT NOT NULL,          -- nom du fichier envoyé, dans le dossier du doublage
    voix TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'auto',
    etat TEXT NOT NULL DEFAULT 'en_file',   -- en_file | en_cours | fini | erreur | annule
    message TEXT,
    cout_estime_usd REAL,
    cout_usd REAL,                  -- coût compté (durée du calcul x prix de la carte la plus chère)
    sortie TEXT,                    -- chemin relatif du résultat (mp4 ou mp3)
    cree_le REAL NOT NULL,
    debut REAL,
    fin REAL,
    supprime_le REAL                -- supprimé par l'utilisateur : caché, mais compté dans le mois
);
CREATE INDEX IF NOT EXISTS doublages_compte ON doublages(compte);
"""


class Base:
    def __init__(self, chemin):
        self.chemin = Path(chemin)
        with self.cx() as cx:
            cx.executescript(SCHEMA)
            colonnes = {r[1] for r in cx.execute('PRAGMA table_info(doublages)')}
            if 'supprime_le' not in colonnes:
                cx.execute('ALTER TABLE doublages ADD COLUMN supprime_le REAL')

    def cx(self):
        cx = sqlite3.connect(self.chemin, timeout=10)
        cx.row_factory = sqlite3.Row
        cx.execute('PRAGMA foreign_keys = ON')
        cx.execute('PRAGMA journal_mode = WAL')
        return cx

    def un(self, sql, *args):
        with self.cx() as cx:
            return cx.execute(sql, args).fetchone()

    def tous(self, sql, *args):
        with self.cx() as cx:
            return cx.execute(sql, args).fetchall()

    def faire(self, sql, *args):
        with self.cx() as cx:
            return cx.execute(sql, args).lastrowid

    # --- Comptes ---
    def compte(self, id_):
        return self.un('SELECT * FROM comptes WHERE id = ?', id_)

    def compte_par_email(self, email):
        return self.un('SELECT * FROM comptes WHERE email = ?', email.strip().lower())

    def creer_compte(self, email, nom='', mot_de_passe=None, email_valide=False, admin=False, **externes):
        cols = {'email': email.strip().lower(), 'nom': nom.strip()[:80], 'mot_de_passe': mot_de_passe,
                'email_valide': int(email_valide), 'admin': int(admin), 'cree_le': time.time(), **externes}
        return self.faire(f'INSERT INTO comptes ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})',
                          *cols.values())

    def modifier_compte(self, id_, **champs):
        if champs:
            self.faire(f'UPDATE comptes SET {", ".join(f"{k} = ?" for k in champs)} WHERE id = ?',
                       *champs.values(), id_)

    # --- Jetons à usage unique ---
    def nouveau_jeton(self, compte, usage, duree_s):
        jeton = secrets.token_urlsafe(32)
        self.faire('DELETE FROM jetons WHERE compte = ? AND usage = ?', compte, usage)
        self.faire('INSERT INTO jetons VALUES (?, ?, ?, ?)', _h(jeton), compte, usage, time.time() + duree_s)
        return jeton

    def prendre_jeton(self, jeton, usage):
        """Compte du jeton s'il est valide (et le jeton est consommé), sinon None."""
        with self.cx() as cx:
            r = cx.execute('SELECT compte, expire FROM jetons WHERE empreinte = ? AND usage = ?',
                           (_h(jeton), usage)).fetchone()
            if not r:
                return None
            cx.execute('DELETE FROM jetons WHERE empreinte = ?', (_h(jeton),))
            return r['compte'] if r['expire'] > time.time() else None

    # --- Coûts ---
    def depense_du_mois(self, compte=None):
        """Dollars comptés ce mois-ci (doublages finis, coût réel ; en cours ou en file : coût estimé)."""
        debut = debut_du_mois()
        sql = ("SELECT COALESCE(SUM(COALESCE(cout_usd, CASE WHEN etat IN ('en_file', 'en_cours') "
               "THEN cout_estime_usd END, 0)), 0) FROM doublages WHERE cree_le >= ?")
        args = [debut]
        if compte is not None:
            sql += ' AND compte = ?'
            args.append(compte)
        return round(self.un(sql, *args)[0], 2)


def debut_du_mois():
    m = datetime.date.today().replace(day=1)
    return datetime.datetime(m.year, m.month, 1).timestamp()


def _h(jeton):
    return hashlib.sha256(jeton.encode()).hexdigest()
