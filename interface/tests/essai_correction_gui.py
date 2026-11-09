"""Essai de la frise corrigeable « Voix de chaque passage » (gui/page_resultats.py, gui/frise.py) dans la vraie
fenêtre, sous Xvfb et sur un bus privé : lancé par tests/test_correction_duo.py (Page), avec ses variables VOIXLA_*
(duo inventé de tests/faux_duo.py, faux doubler). Son coupé (fakeaudiosink). Écrit des lignes JSON : {"erreurs": [...]}
et {"fini": true}.

Vérifie : la frise corrigeable (passages délimités, Appliquer grisé sans choix), le menu d'un passage (chanté par
Jules, par Marie, les deux, automatique, écouter), deux choix en attente, « Appliquer » (remix du faux doubler en tâche
de fond : rapport relu, passages corrigés marqués, frise des voix redessinée, « Revenir à l'automatique » visible),
puis « Revenir à l'automatique » (confirmation, remix --affectation auto)."""

import json
import os
import sys
import time
import traceback

from voixla import gstreamer

gstreamer.preparer()          # comme python3 -m voixla : avant GTK

import voixla.gui  # noqa: E402,F401  (fixe les versions de GTK et d'Adw)
from gi.repository import GLib, Gtk  # noqa: E402

from voixla import rapports  # noqa: E402
from voixla.gui import app as appli  # noqa: E402
from voixla.gui.page_resultats import PageDetail  # noqa: E402

erreurs = []


def verifier(condition, message):
    if not condition:
        erreurs.append(message)
    return condition


def dire(**k):
    print(json.dumps(k, ensure_ascii=False, default=str), flush=True)


def textes(widget):
    """Les libellés (Gtk.Label, Gtk.CheckButton) sous un widget."""
    sortie = []
    w = widget.get_first_child() if widget is not None else None
    while w is not None:
        if isinstance(w, Gtk.CheckButton):
            sortie.append(w.get_label() or "")
        elif isinstance(w, Gtk.Label):
            sortie.append(w.get_label())
        else:
            sortie += textes(w)
        w = w.get_next_sibling()
    return sortie


def cases(widget):
    sortie = []
    w = widget.get_first_child() if widget is not None else None
    while w is not None:
        if isinstance(w, Gtk.CheckButton):
            sortie.append(w)
        sortie += cases(w)
        w = w.get_next_sibling()
    return sortie


def scenario(app):
    f = app.fenetre
    page = f.pages["resultats"]
    debut = time.monotonic()
    while (not page._charge or "--affectation" not in f.caps) and time.monotonic() - debut < 20:
        yield 50
    verifier("--affectation" in f.caps, "le faux doubler n'annonce pas --affectation")
    res = next((r for r in page.resultats if r.titre == "Valse des lanternes"), None)
    if not verifier(res is not None, "duo inventé introuvable"):
        return
    d = PageDetail(page, res)
    f.pousser(d)
    yield 3500     # sous Xvfb, GDK ne connaît le moniteur de la fenêtre qu'après quelques secondes (popover)
    fv = d.frise_voix
    if not verifier(fv is not None, "pas de frise des voix"):
        return
    verifier(fv.corrigeable, f"frise non corrigeable : {d.l_correction.get_label()}")
    verifier(len(fv.morceaux) == 14, f"{len(fv.morceaux)} passages au lieu de 14")
    verifier(d.b_appliquer.get_visible() and not d.b_appliquer.get_sensitive(), "Appliquer actif sans choix")
    verifier(not d.b_auto.get_visible(), "« Revenir à l'automatique » sans correction")
    verifier("Un clic sur un passage" in d.l_correction.get_label(), d.l_correction.get_label())

    # menu du passage 7 (un refrain de la chanteuse, mis dans la voix de Jules)
    m7 = fv.morceau(7)
    pop = d._menu_passage(m7)
    yield 400
    # diagnostic : sous Xvfb sans gestionnaire de fenêtres, le popover peut ne pas s'afficher (moniteur inconnu de
    # GDK) ; son contenu et les choix se vérifient quand même (la capture d'écran le montre affiché)
    dire(menu={"visible": bool(pop and pop.get_visible()), "frise": fv.get_mapped(), "largeur": fv.get_width(),
               "fenetre": f.get_mapped()})
    if verifier(pop is not None, "menu du passage non créé"):
        t = textes(pop.get_child())
        for attendu in ("Passage 7 · 0:22,7 – 0:26,5", "Chanté par Jules", "Chanté par Marie", "Les deux",
                        "Automatique (Jules)", "Écouter ce passage"):
            verifier(any(attendu in x for x in t), f"menu : « {attendu} » manque ({t})")
        verifier(fv.selection == 7 or not pop.get_visible(), "passage du menu non sélectionné")
        c = cases(pop.get_child())
        actif = [x.get_label() for x in c if x.get_active()]
        verifier(actif == ["Automatique (Jules)"], f"choix actif : {actif}")
        marie = next((x for x in c if x.get_label() == "Chanté par Marie"), None)
        if marie is not None:
            marie.set_active(True)
    yield 400
    verifier(d.choix_attente == {7: "marie"}, f"choix en attente : {d.choix_attente}")
    verifier(d.pop_passage is None, "menu resté ouvert après le choix")
    d._choisir(8, "marie")
    d._choisir(13, "deux")
    d._choisir(13, "auto")     # revenu à ce que doubler garde : plus en attente
    yield 200
    verifier(d.choix_attente == {7: "marie", 8: "marie"}, f"choix en attente : {d.choix_attente}")
    verifier(fv.attente == {7: "marie", 8: "marie"}, "frise : choix en attente non dessinés")
    verifier(d.b_appliquer.get_sensitive(), "Appliquer grisé malgré deux choix")
    verifier("2 choix en attente" in d.l_correction.get_label(), d.l_correction.get_label())

    # Appliquer : remix du faux doubler en tâche de fond
    d.appliquer_correction()
    verifier(d.correction_en_cours and not fv.corrigeable, "pas de remix en cours")
    debut = time.monotonic()
    while d.correction_en_cours and time.monotonic() - debut < 40:
        yield 100
    yield 300
    verifier(not d.correction_en_cours, "remix trop long")
    verifier(rapports.passages_corriges(d.res.rapport) == {7, 8},
             f"passages corrigés : {rapports.passages_corriges(d.res.rapport)}")
    verifier((d.res.affectation_gardee or {}).get("passages") == {"7": "marie", "8": "marie"},
             f"affectation gardée : {d.res.affectation_gardee}")
    fv = d.frise_voix
    verifier(fv.morceau(7)["corrige"] and fv.morceau(8)["corrige"] and not fv.morceau(6)["corrige"],
             "repères des passages corrigés")
    verifier(not d.choix_attente and not fv.attente, "choix en attente restés après Appliquer")
    p = fv.passage_a(24.0)
    verifier(p is not None and p["voix"] == ["marie"], f"frise des voix non redessinée : {p}")
    verifier(d.b_auto.get_visible() and d.b_auto.get_sensitive(), "« Revenir à l'automatique » absent")
    verifier("Corrigé à la main : 2 passages" in d.l_correction.get_label(), d.l_correction.get_label())
    verifier(d.b_remix.get_sensitive(), "remix rapide resté grisé")
    pop = d._menu_passage(fv.morceau(7))
    yield 300
    if verifier(pop is not None, "menu d'un passage corrigé non créé"):
        t = textes(pop.get_child())
        verifier(any("corrigé à la main" in x for x in t), f"menu d'un passage corrigé : {t}")
        actif = [x.get_label() for x in cases(pop.get_child()) if x.get_active()]
        verifier(actif == ["Chanté par Marie"], f"choix actif après correction : {actif}")
        d._choisir(7, "marie")     # le même choix que celui gardé : rien en attente, le menu se ferme
        verifier(not d.choix_attente and d.pop_passage is None, "choix identique mis en attente")
    yield 300

    # Revenir à l'automatique (confirmation)
    dlg = d.revenir_a_l_automatique()
    yield 300
    dlg.emit("response", "ok")
    dlg.close()
    debut = time.monotonic()
    yield 200
    while d.correction_en_cours and time.monotonic() - debut < 40:
        yield 100
    yield 300
    verifier(rapports.passages_corriges(d.res.rapport) == set(), "corrections restées")
    verifier(d.res.affectation_gardee is None, "affectation.json resté")
    verifier(not d.b_auto.get_visible(), "« Revenir à l'automatique » resté visible")
    p = d.frise_voix.passage_a(24.0)
    verifier(p is not None and p["voix"] == ["jules"], f"automatique non retrouvé : {p}")


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
    GLib.timeout_add_seconds(150, lambda: (dire(erreurs=["trop long"]), os._exit(3)))
    sys.exit(Essai().run(sys.argv[:1]))
