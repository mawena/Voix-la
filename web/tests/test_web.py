"""Essais du service web (sans courriel ni doublage réels) : python -m pytest web/tests."""
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voixla_web import securite  # noqa: E402
from voixla_web.app import creer  # noqa: E402
from voixla_web.config import Config  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    courriels = []
    monkeypatch.setattr(securite, 'envoyer', lambda conf, a, sujet, texte: courriels.append((a, sujet, texte)))
    conf = Config(url='http://essai', secret_session='x' * 40, donnees=tmp_path, essai=True, admins=['chef@ex.fr'])
    app = creer(conf)
    return TestClient(app, follow_redirects=False), app, courriels


def csrf(cl, url='/connexion'):
    return re.search(r'name="csrf" value="([^"]+)"', cl.get(url).text).group(1)


def lien(courriels, motif):
    return re.search(motif + r'/(\S+)', courriels[-1][2]).group(1)


def inscrire(cl, courriels, email='a@ex.fr', mdp='motdepasse-long'):
    r = cl.post('/inscription', data={'email': email, 'mot_de_passe': mdp, 'nom': 'A', 'csrf': csrf(cl, '/inscription')})
    assert r.status_code == 200
    return lien(courriels, '/valider')


def test_accueil_et_pages_publiques(env):
    cl, _, _ = env
    for url in ('/', '/connexion', '/inscription', '/oubli', '/statique/style.css'):
        assert cl.get(url).status_code == 200
    assert cl.get('/tableau').headers['location'] == '/connexion'


def test_inscription_validation_connexion(env):
    cl, app, courriels = env
    jeton = inscrire(cl, courriels)
    # avant validation : connexion refusée (adresse à confirmer)
    r = cl.post('/connexion', data={'email': 'a@ex.fr', 'mot_de_passe': 'motdepasse-long', 'csrf': csrf(cl)})
    assert 'confirmer' in r.text.lower()
    jeton = lien(courriels, '/valider')   # un nouveau lien remplace l'ancien
    assert cl.get(f'/valider/{jeton}').status_code in (200, 303)
    assert cl.get(f'/valider/{jeton}').status_code == 400   # usage unique
    cl.cookies.clear()
    r = cl.post('/connexion', data={'email': 'A@EX.FR', 'mot_de_passe': 'motdepasse-long', 'csrf': csrf(cl)})
    assert r.status_code == 303
    assert cl.get('/tableau').status_code == 200


def test_mauvais_mot_de_passe_et_csrf(env):
    cl, _, courriels = env
    cl.get(f'/valider/{inscrire(cl, courriels)}')
    cl.cookies.clear()
    r = cl.post('/connexion', data={'email': 'a@ex.fr', 'mot_de_passe': 'faux-faux-faux', 'csrf': csrf(cl)})
    assert r.status_code == 401
    r = cl.post('/connexion', data={'email': 'a@ex.fr', 'mot_de_passe': 'motdepasse-long', 'csrf': 'nimporte'})
    assert r.status_code == 400


def test_inscription_double_et_mot_de_passe_court(env):
    cl, _, courriels = env
    inscrire(cl, courriels)
    r = cl.post('/inscription', data={'email': 'a@ex.fr', 'mot_de_passe': 'motdepasse-long', 'csrf': csrf(cl, '/inscription')})
    assert r.status_code == 200 and 'déjà' in courriels[-1][2]   # même réponse, courriel différent
    r = cl.post('/inscription', data={'email': 'b@ex.fr', 'mot_de_passe': 'court', 'csrf': csrf(cl, '/inscription')})
    assert r.status_code == 400


def test_oubli_et_reinitialisation(env):
    cl, _, courriels = env
    cl.get(f'/valider/{inscrire(cl, courriels)}')
    cl.cookies.clear()
    cl.post('/oubli', data={'email': 'a@ex.fr', 'csrf': csrf(cl, '/oubli')})
    j = lien(courriels, '/reinitialiser')
    r = cl.post(f'/reinitialiser/{j}', data={'mot_de_passe': 'nouveau-mot-long', 'csrf': csrf(cl, f'/reinitialiser/{j}')})
    assert r.status_code == 303
    cl.cookies.clear()
    r = cl.post('/connexion', data={'email': 'a@ex.fr', 'mot_de_passe': 'nouveau-mot-long', 'csrf': csrf(cl)})
    assert r.status_code == 303


def test_admin_et_blocage(env):
    cl, app, courriels = env
    cl.get(f'/valider/{inscrire(cl, courriels, "chef@ex.fr")}')
    autre = TestClient(app, follow_redirects=False)
    autre.get(f'/valider/{inscrire(autre, courriels, "b@ex.fr")}')
    cl.cookies.clear()
    cl.post('/connexion', data={'email': 'chef@ex.fr', 'mot_de_passe': 'motdepasse-long', 'csrf': csrf(cl)})
    r = cl.get('/admin')
    assert r.status_code == 200 and 'b@ex.fr' in r.text
    b = app.state.base.compte_par_email('b@ex.fr')['id']
    cl.post(f'/admin/comptes/{b}', data={'action': 'bloquer', 'csrf': csrf(cl, '/tableau')})
    assert app.state.base.compte(b)['bloque'] == 1
    autre.cookies.clear()
    r = autre.post('/connexion', data={'email': 'b@ex.fr', 'mot_de_passe': 'motdepasse-long', 'csrf': csrf(autre)})
    assert r.status_code == 403
    # un compte ordinaire n'a pas accès à l'administration
    cl.post(f'/admin/comptes/{b}', data={'action': 'debloquer', 'csrf': csrf(cl, '/tableau')})
    autre.post('/connexion', data={'email': 'b@ex.fr', 'mot_de_passe': 'motdepasse-long', 'csrf': csrf(autre)})
    assert autre.get('/admin').status_code == 403


def test_voix_et_doublage_en_file(env, tmp_path):
    cl, app, courriels = env
    cl.get(f'/valider/{inscrire(cl, courriels)}')
    cl.cookies.clear()
    cl.post('/connexion', data={'email': 'a@ex.fr', 'mot_de_passe': 'motdepasse-long', 'csrf': csrf(cl)})
    t = csrf(cl, '/tableau')
    r = cl.post('/voix', data={'nom': 'ma-voix', 'csrf': t}, files={'enregistrement': ('v.wav', b'RIFF0000', 'audio/wav')})
    assert r.status_code == 303
    base = app.state.base
    v = base.un('SELECT * FROM voix')
    assert v['nom'] == 'ma-voix' and v['etat'] == 'en_file'
    # la voix n'est pas prête : pas de doublage
    cl.post('/doublages', data={'voix': 'ma-voix', 'csrf': t}, files={'media': ('a.mp4', b'x', 'video/mp4')})
    assert base.un('SELECT COUNT(*) FROM doublages')[0] == 0
    base.faire("UPDATE voix SET etat = 'prete'")
    app.state.conf.duree_max_s = 10 ** 6
    import voixla_web.app as module
    module._duree = lambda chemin: 30.0
    r = cl.post('/doublages', data={'voix': 'ma-voix', 'csrf': t}, files={'media': ('a.mp4', b'x' * 100, 'video/mp4')})
    assert r.status_code == 303
    d = base.un('SELECT * FROM doublages')
    assert d['etat'] == 'en_file'
    e = cl.get(f'/doublages/{d["id"]}/etat').json()
    assert e['etat'] == 'en_file' and e['position'] == 1
    assert '<td>a<br>' in cl.get('/tableau').text
    cl.post(f'/doublages/{d["id"]}/annuler', data={'csrf': t})
    assert base.un('SELECT etat FROM doublages')['etat'] == 'annule'
    # un autre compte ne voit pas ce doublage
    autre = TestClient(app, follow_redirects=False)
    autre.get(f'/valider/{inscrire(autre, courriels, "b@ex.fr")}')
    autre.post('/connexion', data={'email': 'b@ex.fr', 'mot_de_passe': 'motdepasse-long', 'csrf': csrf(autre)})
    assert autre.get(f'/doublages/{d["id"]}/etat').status_code == 404


def test_mots_de_passe():
    h = securite.hacher('abc-def-ghi')
    assert securite.verifier('abc-def-ghi', h) and not securite.verifier('autre', h) and not securite.verifier('x', None)
