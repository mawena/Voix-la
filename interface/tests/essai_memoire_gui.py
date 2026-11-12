"""Essai de la mémoire de l'interface dans la vraie fenêtre, sous Xvfb et sur un bus privé : lancé par
tests/test_memoire_interface.py, avec ses variables VOIXLA_* (données des captures, faux doubler). Son coupé (lecteur
muet, fakeaudiosink). Écrit des lignes JSON : {"erreurs": [...]}, {"fini": true} et les mesures.

Vérifie qu'une page qu'on quitte est rendue au ramasse-miettes (c'était la fuite : 20 Mo par résultat ouvert) :
- ouvrir puis quitter quelques résultats (avec lecture) ne laisse aucune PageDetail, aucun Spectre, aucun flux
  Gtk.MediaFile vivants, et le nombre d'objets Python ne grandit plus d'un tour à l'autre ;
- la page État rafraîchie par ses minuteurs, puis cachée et remontrée, garde le même nombre de widgets."""

import gc
import json
import os
import sys
import time
import traceback
import weakref

from voixla import gstreamer

gstreamer.preparer()          # comme python3 -m voixla : avant GTK

import voixla.gui  # noqa: E402,F401  (fixe les versions de GTK et d'Adw)
from gi.repository import GLib  # noqa: E402

from voixla.gui import app as appli  # noqa: E402
from voixla.gui.page_resultats import PageDetail  # noqa: E402
from voixla.gui.visualisations import Spectre  # noqa: E402

erreurs = []
TOURS = 4


def verifier(condition, message):
    if not condition:
        erreurs.append(message)
    return condition


def dire(**k):
    print(json.dumps(k, ensure_ascii=False, default=str), flush=True)


def vivants(classe):
    gc.collect()
    return sum(1 for o in gc.get_objects() if isinstance(o, classe))


def nb_widgets(w):
    n, e = 1, w.get_first_child()
    while e is not None:
        n += nb_widgets(e)
        e = e.get_next_sibling()
    return n


def scenario(app):
    f = app.fenetre
    page = f.pages["resultats"]
    debut = time.monotonic()
    while not page._charge and time.monotonic() - debut < 20:
        yield 50
    yield 500
    pris = [r for r in page.resultats if r.video][:3]
    verifier(len(pris) == 3, f"3 doublages avec vidéo attendus : {len(pris)}")
    flux = []
    objets = []
    for tour in range(TOURS):
        for r in pris:
            f.ouvrir_resultat(r.dossier, r.voix)
            yield 700
            d = f.nav.get_visible_page()
            if not isinstance(d, PageDetail):
                verifier(False, f"le détail de {r.titre} ne s'est pas ouvert")
                continue
            d.lecteur.rendre_muet()
            d.lire()
            yield 600
            flux += [weakref.ref(m) for m in {id(m): m for m in d.lecteur.flux.values()}.values()]
            d.arreter()
            f.nav.pop()
            yield 1200      # l'animation du retour finie (sinon deux pages « detail » en même temps)
        d = None
        yield 300
        gc.collect()
        objets.append(sum(1 for _ in gc.get_objects()))
        dire(tour=tour, details=vivants(PageDetail), spectres=vivants(Spectre), objets=objets[-1])
    verifier(vivants(PageDetail) == 0, f"pages de résultat gardées après leur fermeture : {vivants(PageDetail)}")
    verifier(vivants(Spectre) == 0, f"spectrogrammes gardés après la fermeture : {vivants(Spectre)}")
    verifier(flux and all(w() is None for w in flux),
             f"flux Gtk.MediaFile gardés : {sum(1 for w in flux if w() is not None)} sur {len(flux)}")
    # le premier tour remplit les caches (polices, textures, CSS) ; ensuite, plus rien ne doit s'accumuler
    verifier(objets[-1] - objets[1] < 600, f"objets Python en hausse d'un tour à l'autre : {objets}")

    # --- page État : les minuteurs ne recréent pas de widgets ---------------------------------------------------------
    f.aller("etat")
    yield 800
    etat = f.pages["etat"]
    avant = nb_widgets(etat.vue)
    for _ in range(20):
        etat._tic_local()
        etat._tic_kaggle()
        yield 30
    f.aller("doubler")
    yield 200
    f.aller("etat")
    yield 800
    apres = nb_widgets(etat.vue)
    verifier(apres == avant, f"widgets de la page État : {avant} → {apres}")
    dire(etat={"widgets": apres})


def lancer(app):
    gen = scenario(app)

    def fin():
        dire(erreurs=erreurs)
        dire(fini=True)
        app.quitter_tout()

    def suivant():
        try:
            attente = next(gen)
        except StopIteration:
            fin()
            return False
        except Exception:  # noqa: BLE001
            erreurs.append(traceback.format_exc())
            fin()
            return False
        GLib.timeout_add(int(attente), suivant)
        return False
    GLib.timeout_add(500, suivant)


class Essai(appli.Application):
    def do_activate(self):
        appli.Application.do_activate(self)
        lancer(self)


if __name__ == "__main__":
    GLib.timeout_add_seconds(200, lambda: (dire(erreurs=["trop long"]), os._exit(3)))
    sys.exit(Essai().run(sys.argv[:1]))
