"""Mots de passe (scrypt), jetons anti-CSRF, OpenID Connect (Google, Portier : code d'autorisation + PKCE) et courriels."""
import base64
import hashlib
import hmac
import re
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx

# scrypt : n=2**14, r=8, p=1 (16 Mo de mémoire par calcul)
N, R, P = 2 ** 14, 8, 1
EMAIL = re.compile(r'^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$')
MDP_MIN = 10


def hacher(mdp):
    sel = secrets.token_bytes(16)
    h = hashlib.scrypt(mdp.encode(), salt=sel, n=N, r=R, p=P, dklen=32)
    return f'scrypt${N}${R}${P}${_b64(sel)}${_b64(h)}'


def verifier(mdp, stocke):
    try:
        _, n, r, p, sel, h = (stocke or '').split('$')
        calc = hashlib.scrypt(mdp.encode(), salt=_d64(sel), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, _d64(h))
    except (ValueError, TypeError):
        hacher(mdp)   # même durée qu'une vraie vérification
        return False


def email_valide(email):
    return bool(EMAIL.match((email or '').strip()))


def jeton_csrf(session):
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(24)
    return session['csrf']


def csrf_ok(session, recu):
    return bool(recu) and hmac.compare_digest(str(session.get('csrf', '')), str(recu))


# --- OpenID Connect ---

def debut_oidc(fournisseur, retour, session):
    """Adresse vers laquelle renvoyer le navigateur ; l'état et le vérificateur PKCE restent dans la session."""
    etat, verif = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    session['oidc'] = {'fournisseur': fournisseur.nom, 'etat': etat, 'verif': verif}
    defi = _b64(hashlib.sha256(verif.encode()).digest())
    q = {'response_type': 'code', 'client_id': fournisseur.client_id, 'redirect_uri': retour,
         'scope': fournisseur.portees, 'state': etat, 'code_challenge': defi, 'code_challenge_method': 'S256'}
    if fournisseur.nom == 'google':
        q['prompt'] = 'select_account'
    return f'{fournisseur.autoriser}?{urlencode(q)}'


async def fin_oidc(fournisseur, retour, session, code, etat):
    """Profil {sub, email, email_verified, name} après le retour du fournisseur ; ValueError si refusé."""
    attendu = session.pop('oidc', None) or {}
    if attendu.get('fournisseur') != fournisseur.nom or not etat or not hmac.compare_digest(attendu.get('etat', ''), etat):
        raise ValueError('état OIDC inattendu')
    if not code:
        raise ValueError('connexion refusée')
    async with httpx.AsyncClient(timeout=15) as cl:
        r = await cl.post(fournisseur.jeton, data={
            'grant_type': 'authorization_code', 'code': code, 'redirect_uri': retour,
            'code_verifier': attendu['verif'], 'client_id': fournisseur.client_id,
            'client_secret': fournisseur.secret})
        if r.status_code != 200:
            raise ValueError(f'échange du code refusé ({r.status_code})')
        acces = r.json().get('access_token')
        r = await cl.get(fournisseur.utilisateur, headers={'Authorization': f'Bearer {acces}'})
        if r.status_code != 200:
            raise ValueError(f'profil refusé ({r.status_code})')
        profil = r.json()
    if not profil.get('sub') or not email_valide(profil.get('email')):
        raise ValueError('profil sans adresse e-mail')
    return profil


# --- Courriels ---

def envoyer(conf, a, sujet, texte):
    """Envoie un courriel texte par le SMTP de la configuration (587 STARTTLS ou 465 TLS). En essai : rien."""
    if conf.essai or not conf.smtp.get('hote'):
        print(f'[courriel non envoyé] à {a} : {sujet}\n{texte}', flush=True)
        return
    m = EmailMessage()
    m['From'] = conf.smtp.get('expediteur') or f"Voix-là <{conf.smtp['utilisateur']}>"
    m['To'] = a
    m['Subject'] = sujet
    m.set_content(texte)
    port = int(conf.smtp.get('port', 587))
    ctx = ssl.create_default_context()
    if port == 465:
        s = smtplib.SMTP_SSL(conf.smtp['hote'], port, context=ctx, timeout=20)
    else:
        s = smtplib.SMTP(conf.smtp['hote'], port, timeout=20)
        s.starttls(context=ctx)
    with s:
        s.login(conf.smtp['utilisateur'], conf.smtp['mot_de_passe'])
        s.send_message(m)


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _d64(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))
