"""Faux module « modal » pour les essais : aucun réseau, aucun compte, aucune application créée. Il imite juste ce que
calcul_modal_hote.py utilise (App, Image, Volume, Cls avec with_options, method, enable_output) et exécute la fonction
du conteneur ICI, dans un dossier de travail ($FAUX_MODAL_DIR) qui tient lieu de racine du conteneur
($VOIXLA_MODAL_RACINE) : <racine>/echange est le volume éphémère (effacé à la sortie, comme Modal), <racine>/entrees/outils
le volume des modèles (persistant, dans $FAUX_MODAL_DIR/modeles).
Trace de ce qui a été demandé : $FAUX_MODAL_DIR/trace.json."""
import contextlib
import json
import os
import shutil
from pathlib import Path


def _racine():
    return Path(os.environ['FAUX_MODAL_DIR'])


def _trace(**champs):
    f = _racine() / 'trace.json'
    donnees = json.loads(f.read_text()) if f.exists() else {}
    for cle, valeur in champs.items():
        if isinstance(valeur, list):
            donnees.setdefault(cle, []).extend(valeur)
        else:
            donnees[cle] = valeur
    f.write_text(json.dumps(donnees))


class Image:
    @classmethod
    def debian_slim(cls, python_version=None):
        _trace(python=python_version)
        return cls()

    def apt_install(self, *paquets):
        _trace(apt=list(paquets))
        return self

    def pip_install(self, *paquets, extra_index_url=None):
        _trace(pip=list(paquets), index=extra_index_url)
        return self

    def env(self, variables):
        _trace(env=variables)
        return self


class _Lot:
    def __init__(self, dossier):
        self.dossier = dossier

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def put_file(self, local, distant):
        cible = self.dossier / distant.lstrip('/')
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, cible)


class Volume:
    def __init__(self, dossier, nom=None):
        self.dossier, self.nom = dossier, nom

    @classmethod
    def from_name(cls, nom, create_if_missing=False):
        _trace(volume_nom=nom)
        d = _racine() / 'modeles'
        d.mkdir(parents=True, exist_ok=True)
        return cls(d, nom)

    @classmethod
    @contextlib.contextmanager
    def ephemeral(cls):
        d = _racine() / 'echange'
        d.mkdir(parents=True, exist_ok=True)
        _trace(ephemere_cree=True)
        try:
            yield cls(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)   # Modal supprime le volume éphémère à la sortie
            _trace(ephemere_supprime=True)

    def batch_upload(self):
        return _Lot(self.dossier)

    def commit(self):
        _trace(commits=[self.nom or 'ephemere'])


def method():
    return lambda f: f


class _Machine:
    def __init__(self, classe, options):
        self.instance, self.options = classe(), options

    def __getattr__(self, nom):
        f = getattr(self.instance, nom)

        class Appel:
            @staticmethod
            def remote_gen(*a, **k):
                _trace(options={'gpu': self.options.get('gpu'), 'timeout': self.options.get('timeout'),
                                'volumes': sorted(self.options.get('volumes', {}))})
                os.environ['VOIXLA_MODAL_RACINE'] = str(_racine())
                return f(*a, **k)
        return Appel


class _Cls:
    def __init__(self, classe, options):
        self.classe, self.options = classe, options

    def with_options(self, **options):
        return _Cls(self.classe, {**self.options, **options})

    def __call__(self):
        return _Machine(self.classe, self.options)


class App:
    def __init__(self, nom=None):
        _trace(app=nom)

    def cls(self, **options):
        return lambda classe: _Cls(classe, options)

    @contextlib.contextmanager
    def run(self):
        _trace(app_lancee=True)
        try:
            yield self
        finally:
            _trace(app_arretee=True)


@contextlib.contextmanager
def enable_output():
    yield
