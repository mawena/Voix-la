"""Application FastAPI de voix-la.mawena.cloud : comptes, voix, doublages, administration."""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import config as configuration
from . import securite as s
from .base import Base
from .travaux import COUT_PAR_DEFAUT, Travaux

ICI = Path(__file__).parent
NOM_VOIX = re.compile(r'^[A-Za-zÀ-ÿ0-9_-]{2,30}$')
EXT_MEDIA = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.mp3', '.wav', '.flac', '.m4a', '.ogg', '.opus'}
EXT_SON = {'.mp3', '.wav', '.flac', '.m4a', '.ogg', '.opus', '.webm', '.mp4'}
ETATS = {'en_file': 'en file', 'en_cours': 'en cours', 'fini': 'fini', 'erreur': 'erreur', 'annule': 'annulé',
         'prete': 'prête'}


def creer(conf=None):
    conf = conf or configuration.charger()
    base = Base(conf.donnees / 'voix-la.sqlite')
    travaux = Travaux(conf, base)
    app = FastAPI(title='Voix-là', docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=conf.secret_session, session_cookie='voixla',
                       max_age=30 * 86400, same_site='lax', https_only=conf.url.startswith('https'))
    app.mount('/statique', StaticFiles(directory=ICI / 'statique'), name='statique')
    gabarits = Jinja2Templates(directory=ICI / 'gabarits')
    gabarits.env.globals.update(etats=ETATS, fournisseurs=conf.fournisseurs, inscription=conf.inscription)
    gabarits.env.filters['date'] = lambda t: time.strftime('%d/%m/%Y %H:%M', time.localtime(t)) if t else ''
    gabarits.env.filters['dollars'] = lambda x: f'{x or 0:.2f} $'.replace('.', ',')
    app.state.conf, app.state.base, app.state.travaux = conf, base, travaux

    # --- Outils ---
    def moi(req):
        cid = req.session.get('compte')
        c = base.compte(cid) if cid else None
        if c and c['bloque']:
            req.session.clear()
            return None
        return c

    def page(req, gabarit, code=200, **ctx):
        return gabarits.TemplateResponse(req, gabarit, {'moi': moi(req), 'csrf': s.jeton_csrf(req.session),
                                                    'message': req.session.pop('message', None), **ctx},
                                         status_code=code)

    def aller(url, message=None, req=None):
        if message and req is not None:
            req.session['message'] = message
        return RedirectResponse(url, status_code=303)

    def ouvrir_session(req, c):
        req.session.clear()
        req.session['compte'] = c['id']
        base.modifier_compte(c['id'], vu_le=time.time())

    def admin_auto(email):
        return email.lower() in conf.admins

    def envoyer_validation(c):
        j = base.nouveau_jeton(c['id'], 'valider', 3 * 86400)
        s.envoyer(conf, c['email'], 'Voix-là : confirmez votre adresse',
                  f"Bonjour,\n\nPour confirmer votre adresse et activer votre compte Voix-là, ouvrez ce lien "
                  f"(valable 3 jours) :\n\n{conf.url}/valider/{j}\n\nSi vous n'avez rien demandé, ignorez ce message.\n")

    class Refus(Exception):
        def __init__(self, reponse):
            self.reponse = reponse

    @app.exception_handler(Refus)
    async def _refus(req, e):
        return e.reponse

    def exiger(req, admin=False, csrf=None):
        c = moi(req)
        if not c:
            raise Refus(aller('/connexion'))
        if admin and not c['admin']:
            raise Refus(HTMLResponse('Réservé aux administrateurs.', 403))
        if csrf is not None and not s.csrf_ok(req.session, csrf):
            raise Refus(HTMLResponse('Jeton de formulaire expiré : rechargez la page.', 400))
        return c

    def verifier_csrf(req, csrf):
        if not s.csrf_ok(req.session, csrf):
            raise Refus(HTMLResponse('Jeton de formulaire expiré : rechargez la page.', 400))

    # --- Pages publiques ---
    @app.get('/', response_class=HTMLResponse)
    def accueil(req: Request):
        if moi(req):
            return aller('/tableau')
        return page(req, 'accueil.html')

    @app.get('/inscription', response_class=HTMLResponse)
    def inscription(req: Request):
        return page(req, 'inscription.html')

    @app.post('/inscription')
    def inscription_post(req: Request, email: str = Form(''), nom: str = Form(''), mot_de_passe: str = Form(''),
                         csrf: str = Form('')):
        verifier_csrf(req, csrf)
        email = email.strip().lower()
        if conf.inscription != 'libre' and not admin_auto(email):
            return page(req, 'inscription.html', 403, erreur='Les inscriptions sont fermées.')
        if not s.email_valide(email):
            return page(req, 'inscription.html', 400, erreur='Adresse e-mail invalide.', email=email, nom=nom)
        if len(mot_de_passe) < s.MDP_MIN:
            return page(req, 'inscription.html', 400, erreur=f'Mot de passe : {s.MDP_MIN} caractères au moins.',
                        email=email, nom=nom)
        c = base.compte_par_email(email)
        if c:
            # même réponse que pour une nouvelle adresse : on ne révèle pas qu'elle existe
            s.envoyer(conf, email, 'Voix-là : vous avez déjà un compte',
                      f"Bonjour,\n\nUn compte Voix-là existe déjà pour cette adresse. Connectez-vous sur {conf.url}/connexion"
                      f" ou choisissez « mot de passe oublié ».\n")
        else:
            cid = base.creer_compte(email, nom, s.hacher(mot_de_passe), admin=admin_auto(email))
            envoyer_validation(base.compte(cid))
        return page(req, 'info.html', titre='Vérifiez vos courriels',
                    texte=f'Un lien de confirmation a été envoyé à {email}. Ouvrez-le pour activer votre compte.')

    @app.get('/valider/{jeton}')
    def valider(req: Request, jeton: str):
        cid = base.prendre_jeton(jeton, 'valider')
        if not cid:
            return page(req, 'info.html', 400, titre='Lien expiré', texte='Ce lien n’est plus valable. Connectez-vous '
                        'pour en recevoir un nouveau.')
        base.modifier_compte(cid, email_valide=1)
        ouvrir_session(req, base.compte(cid))
        return aller('/tableau', 'Adresse confirmée : bienvenue sur Voix-là.', req)

    @app.get('/connexion', response_class=HTMLResponse)
    def connexion(req: Request):
        return page(req, 'connexion.html')

    @app.post('/connexion')
    def connexion_post(req: Request, email: str = Form(''), mot_de_passe: str = Form(''), csrf: str = Form('')):
        verifier_csrf(req, csrf)
        c = base.compte_par_email(email) if s.email_valide(email) else None
        if not c or not c['mot_de_passe'] or not s.verifier(mot_de_passe, c['mot_de_passe']):
            if not c:
                s.verifier(mot_de_passe, None)
            return page(req, 'connexion.html', 401, erreur='Adresse ou mot de passe incorrect.', email=email)
        if c['bloque']:
            return page(req, 'connexion.html', 403, erreur='Ce compte est bloqué.', email=email)
        if not c['email_valide']:
            envoyer_validation(c)
            return page(req, 'info.html', titre='Adresse à confirmer',
                        texte=f'Un nouveau lien de confirmation a été envoyé à {c["email"]}.')
        ouvrir_session(req, c)
        return aller('/tableau')

    @app.post('/deconnexion')
    def deconnexion(req: Request, csrf: str = Form('')):
        verifier_csrf(req, csrf)
        req.session.clear()
        return aller('/')

    @app.get('/oubli', response_class=HTMLResponse)
    def oubli(req: Request):
        return page(req, 'oubli.html')

    @app.post('/oubli')
    def oubli_post(req: Request, email: str = Form(''), csrf: str = Form('')):
        verifier_csrf(req, csrf)
        c = base.compte_par_email(email) if s.email_valide(email) else None
        if c and not c['bloque']:
            j = base.nouveau_jeton(c['id'], 'reinitialiser', 3600)
            s.envoyer(conf, c['email'], 'Voix-là : nouveau mot de passe',
                      f"Bonjour,\n\nPour choisir un nouveau mot de passe, ouvrez ce lien (valable 1 heure) :\n\n"
                      f"{conf.url}/reinitialiser/{j}\n\nSi vous n'avez rien demandé, ignorez ce message.\n")
        return page(req, 'info.html', titre='Vérifiez vos courriels',
                    texte='Si un compte existe pour cette adresse, un lien vient de lui être envoyé.')

    @app.get('/reinitialiser/{jeton}', response_class=HTMLResponse)
    def reinitialiser(req: Request, jeton: str):
        return page(req, 'reinitialiser.html', jeton=jeton)

    @app.post('/reinitialiser/{jeton}')
    def reinitialiser_post(req: Request, jeton: str, mot_de_passe: str = Form(''), csrf: str = Form('')):
        verifier_csrf(req, csrf)
        if len(mot_de_passe) < s.MDP_MIN:
            return page(req, 'reinitialiser.html', 400, jeton=jeton, erreur=f'{s.MDP_MIN} caractères au moins.')
        cid = base.prendre_jeton(jeton, 'reinitialiser')
        if not cid:
            return page(req, 'info.html', 400, titre='Lien expiré', texte='Demandez un nouveau lien.')
        base.modifier_compte(cid, mot_de_passe=s.hacher(mot_de_passe), email_valide=1)
        ouvrir_session(req, base.compte(cid))
        return aller('/tableau', 'Mot de passe changé.', req)

    # --- Google, Portier ---
    @app.get('/auth/{nom}')
    def auth(req: Request, nom: str):
        f = conf.fournisseurs.get(nom)
        if not f:
            return aller('/connexion')
        return RedirectResponse(s.debut_oidc(f, f'{conf.url}/auth/{nom}/retour', req.session), status_code=303)

    @app.get('/auth/{nom}/retour')
    async def auth_retour(req: Request, nom: str, code: str = '', state: str = ''):
        f = conf.fournisseurs.get(nom)
        if not f:
            return aller('/connexion')
        try:
            p = await s.fin_oidc(f, f'{conf.url}/auth/{nom}/retour', req.session, code, state)
        except (ValueError, OSError) as e:
            return page(req, 'connexion.html', 400, erreur=f'Connexion par {nom.capitalize()} impossible : {e}.')
        email = p['email'].strip().lower()
        sure = p.get('email_verified', nom == 'portier') in (True, 'true', 1)
        c = base.un(f'SELECT * FROM comptes WHERE {nom} = ?', p['sub'])
        if not c:
            c = base.compte_par_email(email)
            if c:
                if not sure:
                    return page(req, 'connexion.html', 400, erreur='Adresse non vérifiée par le fournisseur.')
                base.modifier_compte(c['id'], **{nom: p['sub']}, email_valide=1)   # lie le compte existant
            else:
                if conf.inscription != 'libre' and not admin_auto(email):
                    return page(req, 'connexion.html', 403, erreur='Les inscriptions sont fermées.')
                base.creer_compte(email, p.get('name') or '', email_valide=sure, admin=admin_auto(email),
                                  **{nom: p['sub']})
            c = base.un(f'SELECT * FROM comptes WHERE {nom} = ?', p['sub'])
        if c['bloque']:
            return page(req, 'connexion.html', 403, erreur='Ce compte est bloqué.')
        ouvrir_session(req, c)
        return aller('/tableau')

    # --- Tableau de bord ---
    @app.get('/tableau', response_class=HTMLResponse)
    def tableau(req: Request):
        c = exiger(req)
        voix = base.tous('SELECT * FROM voix WHERE compte = ? ORDER BY nom', c['id'])
        doublages = base.tous('SELECT * FROM doublages WHERE compte = ? ORDER BY id DESC LIMIT 50', c['id'])
        return page(req, 'tableau.html', voix=voix, doublages=doublages, restant=travaux.budget_restant(c['id']),
                    depense=base.depense_du_mois(c['id']), taille_max=conf.taille_max_mo, duree_max=conf.duree_max_s,
                    positions={d['id']: travaux.position(d['id']) for d in doublages})

    @app.post('/compte')
    def compte_post(req: Request, nom: str = Form(''), mot_de_passe: str = Form(''), csrf: str = Form('')):
        c = exiger(req, csrf=csrf)
        champs = {'nom': nom.strip()[:80]}
        if mot_de_passe:
            if len(mot_de_passe) < s.MDP_MIN:
                return aller('/tableau', f'Mot de passe : {s.MDP_MIN} caractères au moins.', req)
            champs['mot_de_passe'] = s.hacher(mot_de_passe)
        base.modifier_compte(c['id'], **champs)
        return aller('/tableau', 'Compte mis à jour.', req)

    @app.post('/voix')
    async def voix_post(req: Request, nom: str = Form(''), enregistrement: UploadFile = File(...), csrf: str = Form('')):
        c = exiger(req, csrf=csrf)
        nom = nom.strip()
        if not NOM_VOIX.match(nom):
            return aller('/tableau', 'Nom de voix : 2 à 30 lettres, chiffres, - ou _.', req)
        ext = Path(enregistrement.filename or '').suffix.lower()
        if ext not in EXT_SON:
            return aller('/tableau', 'Enregistrement : mp3, wav, flac, m4a, ogg, opus ou webm.', req)
        if base.un('SELECT 1 FROM voix WHERE compte = ? AND nom = ?', c['id'], nom):
            return aller('/tableau', f'La voix {nom} existe déjà.', req)
        if base.un('SELECT COUNT(*) FROM voix WHERE compte = ?', c['id'])[0] >= 10:
            return aller('/tableau', '10 voix au plus par compte.', req)
        vid = base.faire("INSERT INTO voix (compte, nom, etat, cree_le) VALUES (?, ?, 'en_file', ?)", c['id'], nom,
                         time.time())
        dest = travaux.dossier_compte(c['id']) / 'enregistrements' / f'{vid}{ext}'
        if not await _recevoir(enregistrement, dest, 50):
            base.faire('DELETE FROM voix WHERE id = ?', vid)
            return aller('/tableau', 'Enregistrement trop lourd (50 Mo au plus).', req)
        travaux.ajouter_voix(vid)
        return aller('/tableau', f'Voix {nom} en préparation.', req)

    @app.post('/voix/{vid}/supprimer')
    def voix_supprimer(req: Request, vid: int, csrf: str = Form('')):
        c = exiger(req, csrf=csrf)
        v = base.un('SELECT * FROM voix WHERE id = ? AND compte = ?', vid, c['id'])
        if v and v['etat'] != 'en_cours':
            base.faire('DELETE FROM voix WHERE id = ?', vid)
            shutil.rmtree(travaux.dossier_compte(c['id']) / 'donnees' / 'voix' / v['nom'], ignore_errors=True)
        return aller('/tableau')

    @app.post('/doublages')
    async def doublage_post(req: Request, voix: str = Form(''), mode: str = Form('auto'), media: UploadFile = File(...),
                            csrf: str = Form('')):
        c = exiger(req, csrf=csrf)
        if not c['email_valide']:
            return aller('/tableau', 'Confirmez d’abord votre adresse e-mail.', req)
        if not base.un("SELECT 1 FROM voix WHERE compte = ? AND nom = ? AND etat = 'prete'", c['id'], voix):
            return aller('/tableau', 'Choisissez une de vos voix prêtes.', req)
        if mode not in ('auto', 'chant', 'parole'):
            mode = 'auto'
        ext = Path(media.filename or '').suffix.lower()
        if ext not in EXT_MEDIA:
            return aller('/tableau', 'Fichier : vidéo (mp4, mkv, webm, mov) ou son (mp3, wav, flac, m4a…).', req)
        if base.un("SELECT COUNT(*) FROM doublages WHERE compte = ? AND etat IN ('en_file', 'en_cours')", c['id'])[0] >= 3:
            return aller('/tableau', '3 doublages au plus en attente par compte.', req)
        if travaux.budget_restant(c['id']) < COUT_PAR_DEFAUT:
            return aller('/tableau', 'Budget du mois atteint : réessayez le mois prochain.', req)
        titre = re.sub(r'[^\w .()-]', '_', Path(media.filename).stem)[:80] or 'video'
        did = base.faire('INSERT INTO doublages (compte, titre, fichier, voix, mode, etat, cout_estime_usd, cree_le) '
                         "VALUES (?, ?, ?, ?, ?, 'reception', ?, ?)", c['id'], titre, titre + ext, voix, mode,
                         COUT_PAR_DEFAUT, time.time())
        d = base.un('SELECT * FROM doublages WHERE id = ?', did)
        dd = travaux.dossier_doublage(d)
        if not await _recevoir(media, dd / d['fichier'], conf.taille_max_mo):
            shutil.rmtree(dd, ignore_errors=True)
            base.faire('DELETE FROM doublages WHERE id = ?', did)
            return aller('/tableau', f'Fichier trop lourd ({conf.taille_max_mo} Mo au plus).', req)
        duree = _duree(dd / d['fichier'])
        if not duree or duree > conf.duree_max_s:
            shutil.rmtree(dd, ignore_errors=True)
            base.faire('DELETE FROM doublages WHERE id = ?', did)
            return aller('/tableau', 'Fichier illisible.' if not duree else
                         f'Trop long : {conf.duree_max_s // 60} min au plus.', req)
        base.faire("UPDATE doublages SET etat = 'en_file' WHERE id = ?", did)
        travaux.ajouter_doublage(did)
        return aller('/tableau', f'« {titre} » est dans la file.', req)

    def _mien(req, did):
        c = exiger(req)
        d = base.un('SELECT * FROM doublages WHERE id = ?', did)
        if not d or (d['compte'] != c['id'] and not c['admin']):
            raise Refus(HTMLResponse('Introuvable.', 404))
        return d

    @app.get('/doublages/{did}/etat')
    def doublage_etat(req: Request, did: int):
        d = _mien(req, did)
        return JSONResponse({'etat': d['etat'], 'libelle': ETATS.get(d['etat'], d['etat']), 'message': d['message'],
                             'cout_usd': d['cout_usd'], 'position': travaux.position(did)})

    @app.get('/doublages/{did}/fichier')
    def doublage_fichier(req: Request, did: int):
        d = _mien(req, did)
        if d['etat'] != 'fini' or not d['sortie']:
            return HTMLResponse('Pas encore prêt.', 404)
        f = (travaux.dossier_doublage(d) / d['sortie']).resolve()
        if not f.is_relative_to(travaux.dossier_doublage(d).resolve()) or not f.is_file():
            return HTMLResponse('Introuvable.', 404)
        return FileResponse(f, filename=f'{d["titre"]} ({d["voix"]}){f.suffix}')

    @app.post('/doublages/{did}/annuler')
    def doublage_annuler(req: Request, did: int, csrf: str = Form('')):
        exiger(req, csrf=csrf)
        _mien(req, did)
        travaux.annuler(did)
        return aller('/tableau')

    @app.post('/doublages/{did}/supprimer')
    def doublage_supprimer(req: Request, did: int, csrf: str = Form('')):
        exiger(req, csrf=csrf)
        d = _mien(req, did)
        if d['etat'] in ('en_file', 'en_cours'):
            travaux.annuler(did)
        shutil.rmtree(travaux.dossier_doublage(d), ignore_errors=True)
        # la ligne reste (coût du mois) mais sans fichier
        base.faire("UPDATE doublages SET etat = CASE WHEN etat IN ('en_file', 'en_cours') THEN 'annule' ELSE etat END, "
                   "sortie = NULL, message = 'supprimé' WHERE id = ?", did)
        return aller('/tableau')

    # --- Administration ---
    @app.get('/admin', response_class=HTMLResponse)
    def admin(req: Request):
        exiger(req, admin=True)
        comptes = base.tous('SELECT c.*, (SELECT COUNT(*) FROM doublages d WHERE d.compte = c.id) AS n FROM comptes c '
                            'ORDER BY c.id DESC')
        depenses = {c['id']: base.depense_du_mois(c['id']) for c in comptes}
        doublages = base.tous('SELECT d.*, c.email FROM doublages d JOIN comptes c ON c.id = d.compte '
                              'ORDER BY d.id DESC LIMIT 100')
        return page(req, 'admin.html', comptes=comptes, depenses=depenses, doublages=doublages,
                    total=base.depense_du_mois(), budget=conf.budget_mensuel_usd, budget_compte=conf.budget_compte_usd)

    @app.post('/admin/comptes/{cid}')
    def admin_compte(req: Request, cid: int, action: str = Form(''), budget: str = Form(''), csrf: str = Form('')):
        moi_ = exiger(req, admin=True, csrf=csrf)
        if cid == moi_['id'] and action in ('bloquer', 'retirer_admin', 'supprimer'):
            return aller('/admin', 'Pas sur votre propre compte.', req)
        if action == 'bloquer':
            base.modifier_compte(cid, bloque=1)
        elif action == 'debloquer':
            base.modifier_compte(cid, bloque=0)
        elif action == 'admin':
            base.modifier_compte(cid, admin=1)
        elif action == 'retirer_admin':
            base.modifier_compte(cid, admin=0)
        elif action == 'valider':
            base.modifier_compte(cid, email_valide=1)
        elif action == 'budget':
            try:
                base.modifier_compte(cid, budget_usd=float(budget.replace(',', '.')) if budget.strip() else None)
            except ValueError:
                return aller('/admin', 'Budget invalide.', req)
        elif action == 'supprimer':
            for d in base.tous("SELECT id FROM doublages WHERE compte = ? AND etat IN ('en_file', 'en_cours')", cid):
                travaux.annuler(d['id'])
            base.faire('DELETE FROM comptes WHERE id = ?', cid)
            shutil.rmtree(conf.donnees / 'comptes' / str(cid), ignore_errors=True)
        return aller('/admin')

    return app


async def _recevoir(fichier, dest, max_mo):
    """Copie l'envoi par morceaux ; False (et rien sur le disque) s'il dépasse max_mo."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    n, limite = 0, max_mo * 1024 * 1024
    with open(dest, 'wb') as f:
        while morceau := await fichier.read(1024 * 1024):
            n += len(morceau)
            if n > limite:
                f.close()
                dest.unlink(missing_ok=True)
                return False
            f.write(morceau)
    return True


def _duree(chemin):
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(chemin)],
                           capture_output=True, text=True, timeout=30)
        return float(json.loads(r.stdout)['format']['duration'])
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return None
