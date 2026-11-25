"""Configuration du service web : ~/.config/voix-la-web/config.toml (600), jamais dans le dépôt.

Les secrets (session, SMTP, Google, Portier) ne sont lus qu'ici. Google peut aussi venir du fichier
~/.config/voix-la-web/google (première ligne : identifiant du client, deuxième : secret).
"""
import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DOSSIER = Path(os.environ.get('VOIXLA_WEB_CONFIG', '~/.config/voix-la-web')).expanduser()


@dataclass
class Fournisseur:
    """Un fournisseur OpenID Connect (Google, Portier)."""
    nom: str
    client_id: str
    secret: str
    autoriser: str
    jeton: str
    utilisateur: str
    portees: str = 'openid email profile'


@dataclass
class Config:
    url: str = 'https://voix-la.mawena.cloud'
    ecoute: str = '127.0.0.1'
    port: int = 8742
    secret_session: str = ''
    donnees: Path = Path('~/.local/share/voix-la-web').expanduser()
    depot: Path = Path(__file__).resolve().parents[2]   # racine de Voix-là (doubler.py)
    python: str = 'python3'
    admins: list = field(default_factory=list)
    inscription: str = 'libre'   # libre | fermee
    budget_mensuel_usd: float = 25.0   # tous comptes confondus (crédit Modal : 30 $ par mois)
    budget_compte_usd: float = 2.0   # par compte et par mois (sauf réglage propre au compte)
    taille_max_mo: int = 300
    duree_max_s: int = 600   # durée maximale d'une vidéo ou d'un son à doubler
    smtp: dict = field(default_factory=dict)
    fournisseurs: dict = field(default_factory=dict)
    essai: bool = False   # tests : pas d'envoi de courriel ni de doublage réel


def _google(brut):
    g = dict(brut.get('google') or {})
    f = DOSSIER / 'google'
    if not g.get('client_id') and f.is_file():
        lignes = [l.strip() for l in f.read_text().splitlines() if l.strip()]
        if len(lignes) >= 2:
            g = {'client_id': lignes[0], 'secret': lignes[1]}
    if not g.get('client_id'):
        return None
    return Fournisseur('google', g['client_id'], g['secret'], 'https://accounts.google.com/o/oauth2/v2/auth',
                       'https://oauth2.googleapis.com/token', 'https://openidconnect.googleapis.com/v1/userinfo')


def _portier(brut):
    p = brut.get('portier') or {}
    if not p.get('client_id'):
        return None
    base = p.get('url', 'https://portier.mawena.cloud').rstrip('/')
    return Fournisseur('portier', p['client_id'], p['secret'], f'{base}/oidc/autoriser', f'{base}/oidc/jeton',
                       f'{base}/oidc/utilisateur')


def charger(chemin=None):
    """Lit la configuration ; crée le secret de session au premier lancement."""
    chemin = Path(chemin) if chemin else DOSSIER / 'config.toml'
    brut = tomllib.loads(chemin.read_text()) if chemin.is_file() else {}
    c = Config()
    for cle in ('url', 'ecoute', 'port', 'python', 'admins', 'inscription', 'budget_mensuel_usd', 'budget_compte_usd',
                'taille_max_mo', 'duree_max_s', 'essai'):
        if cle in brut:
            setattr(c, cle, brut[cle])
    for cle in ('donnees', 'depot'):
        if cle in brut:
            setattr(c, cle, Path(brut[cle]).expanduser())
    c.url = c.url.rstrip('/')
    c.admins = [a.lower() for a in c.admins]
    c.smtp = dict(brut.get('smtp') or {})
    c.fournisseurs = {f.nom: f for f in (_google(brut), _portier(brut)) if f}
    c.secret_session = brut.get('secret_session') or _secret_local()
    c.donnees.mkdir(parents=True, exist_ok=True)
    return c


def _secret_local():
    f = DOSSIER / 'secret_session'
    if not f.is_file():
        f.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        f.write_text(secrets.token_urlsafe(48))
        f.chmod(0o600)
    return f.read_text().strip()
