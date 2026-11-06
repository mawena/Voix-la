"""Essai de la page Résultats (la bibliothèque) dans la vraie fenêtre, sous Xvfb et sur un bus privé : lancé par
tests/test_bibliotheque.py (EssaiPage), avec ses variables VOIXLA_* (données des captures, cache et configuration à
part, faux doubler). Son coupé (lecteur muet, fakeaudiosink). Écrit des lignes JSON : {"erreurs": [...]}, {"fini": true}
et les mesures.

Vérifie : chargement et compte, vue gardée dans interface.json, tri du menu et tri par colonne, recherche et filtres
(puces, page « rien »), regroupement des versions, mode sélection (Échap), miniatures préparées en tâche de fond, lecture
sans ouvrir le détail (son seul, puis la vidéo au même instant à l'ouverture), « Voir le résultat », confirmation de la
corbeille (rien n'est envoyé), liste relue quand un doublage finit."""

import json
import os
import sys
import time
import traceback
from pathlib import Path

from voixla import chemins, gstreamer

gstreamer.preparer()          # comme python3 -m voixla : avant GTK

import voixla.gui  # noqa: E402,F401  (fixe les versions de GTK et d'Adw)
from gi.repository import GLib, Gtk  # noqa: E402

from voixla.gui import app as appli  # noqa: E402
from voixla.gui.bibliotheque import Carte  # noqa: E402
from voixla.gui.lecture import lectures  # noqa: E402
from voixla.gui.page_resultats import PageDetail  # noqa: E402
from voixla.gui.vignettes import miniatures  # noqa: E402

from . import fausse_bibliotheque as fb  # noqa: E402

erreurs = []


def verifier(condition, message):
    if not condition:
        erreurs.append(message)
    return condition


def dire(**k):
    print(json.dumps(k, ensure_ascii=False, default=str), flush=True)


def cartes(widget):
    """Les cartes de la grille (visibles et liées)."""
    sortie = []
    enfant = widget.get_first_child()
    while enfant is not None:
        if isinstance(enfant, Carte) and enfant.element is not None:
            sortie.append(enfant)
        sortie += cartes(enfant)
        enfant = enfant.get_next_sibling()
    return sortie


def titres(page):
    return [e.titre for e in page._visibles()]


def scenario(app):
    f = app.fenetre
    page = f.pages["resultats"]
    debut = time.monotonic()
    while not page._charge and time.monotonic() - debut < 20:
        yield 50
    yield 300
    verifier(page._charge, "la liste ne s'est pas chargée")
    verifier(len(page.versions) == 14, f"14 doublages attendus : {len(page.versions)}")
    verifier(page.m_tri.get_n_items() == 12, f"12 entrées attendues : {page.m_tri.get_n_items()}")
    verifier(page.titre_fenetre.get_subtitle() == "14 doublages · 12 chansons", page.titre_fenetre.get_subtitle())
    verifier(page.pile.get_visible_child_name() == "grille", page.pile.get_visible_child_name())
    verifier(titres(page)[0] == "Lumière d’été", f"le plus récent d'abord : {titres(page)[:3]}")

    # --- tri : menu, puis en-tête de colonne -------------------------------------------------------------------------
    page.a_tri.change_state(GLib.Variant.new_string("titre"))
    yield 100
    verifier(titres(page)[0] == "Brume sur la rivière", f"tri par titre : {titres(page)[:3]}")
    verifier(page.trieur.get_primary_sort_column() is page.col_titre, "la colonne Titre porte le tri")
    page.colonnes.sort_by_column(page.col_duree, Gtk.SortType.DESCENDING)
    yield 100
    verifier((page.choix["tri"], page.choix["inverse"]) == ("duree", True), f"tri par colonne : {page.choix}")
    verifier(page.a_tri.get_state().unpack() == "duree", "le menu suit l'en-tête")
    verifier(titres(page)[0] == "Minuit à Lagos", f"le plus long d'abord : {titres(page)[:3]}")
    page.trier("date", False)
    yield 100

    # --- vue gardée dans interface.json ------------------------------------------------------------------------------
    page.a_vue.change_state(GLib.Variant.new_string("liste"))
    yield 300
    verifier(page.pile.get_visible_child_name() == "liste", "vue liste")
    lu = chemins.lire_json(chemins.fichier_preferences(), {})
    verifier((lu.get("bibliotheque") or {}).get("vue") == "liste", f"vue gardée : {lu.get('bibliotheque')}")
    page.a_vue.change_state(GLib.Variant.new_string("grille"))
    yield 300

    # --- recherche, filtres, regroupement ----------------------------------------------------------------------------
    page.filtrer(texte="LUMIERE")
    yield 100
    verifier(titres(page) == ["Lumière d’été"], f"recherche : {titres(page)}")
    page.filtrer(texte="rien de tel")
    yield 100
    verifier(page.pile.get_visible_child_name() == "rien", "page « aucun doublage ne correspond »")
    page.effacer_tout()
    yield 100
    verifier(page.m_tri.get_n_items() == 12, "recherche effacée")
    page.a_filtre["voix"].change_state(GLib.Variant.new_string("marie"))
    yield 100
    verifier(page.m_tri.get_n_items() == 5, f"filtre voix Marie : {titres(page)}")
    verifier(page.r_puces.get_reveal_child(), "la puce du filtre apparaît")
    verifier(page.b_tri.has_css_class("filtre-actif"), "le bouton de tri le signale")
    lumiere = next(e for e in page._visibles() if e.titre == "Lumière d’été")
    verifier(page.filtre.premiere(lumiere).voix == "Marie", "on ouvrira la version de Marie")
    page.effacer_filtres()
    yield 100
    verifier(page.m_tri.get_n_items() == 12 and not page.r_puces.get_reveal_child(), "filtres effacés")
    page.a_regrouper.change_state(GLib.Variant.new_boolean(False))
    yield 200
    verifier(page.m_tri.get_n_items() == 14, "versions dépliées")
    page.a_regrouper.change_state(GLib.Variant.new_boolean(True))
    yield 200
    verifier(page.m_tri.get_n_items() == 12, "versions regroupées")

    # --- mode sélection ----------------------------------------------------------------------------------------------
    page.selectionner(True)
    yield 200
    verifier(page.grille.get_model() is page.sel_multiple, "sélection multiple dans la grille")
    verifier(page.colonnes.get_model() is None, "la liste cachée ne lit pas le modèle")
    page.a_vue.change_state(GLib.Variant.new_string("liste"))
    yield 200
    verifier(page.colonnes.get_model() is page.sel_multiple and page.col_case.get_visible(), "et dans la liste")
    verifier(page.grille.get_model() is None, "la grille cachée ne lit plus le modèle")
    page.a_vue.change_state(GLib.Variant.new_string("grille"))
    yield 200
    page._active(0)
    yield 100
    verifier(page.l_selection.get_label() == "2 doublages sélectionnés", page.l_selection.get_label())
    verifier(f.nav.get_visible_page() is page, "en mode sélection, un clic coche au lieu d'ouvrir")
    page._active(0)
    yield 100
    verifier(page.l_selection.get_label() == "Cochez des doublages", page.l_selection.get_label())
    page._tout_selectionner()
    yield 100
    verifier(page.l_selection.get_label() == "14 doublages sélectionnés", page.l_selection.get_label())
    verifier(page._echap(), "Échap quitte le mode sélection")
    yield 200
    verifier(not page.en_selection and page.grille.get_model() is page.sel_aucune, "mode sélection quitté")

    # --- miniatures, préparées en tâche de fond ----------------------------------------------------------------------
    debut = time.monotonic()
    while time.monotonic() - debut < 60 and (miniatures().occupe() or miniatures().mesures["preparees"] < 9):
        yield 200
    m = dict(miniatures().mesures)
    verifier(m["preparees"] >= 9, f"miniatures préparées : {m}")
    verifier(m["echecs"] >= 1, "le MP3 sans pochette : pochette générée")
    jpg = list((chemins.dossier_cache() / "miniatures").glob("*.jpg"))
    verifier(len(jpg) >= 9, f"{len(jpg)} miniatures en cache")
    yield 300
    vues = {c.element.entree.titre: c for c in cartes(page.grille)}
    verifier(vues.get("Ciel de craie") is not None and vues["Ciel de craie"].image.vignette.texture is not None,
             "la carte montre sa miniature")
    verifier(vues.get("Chanson sans image") is not None and
             vues["Chanson sans image"].image.vignette.texture is None and
             vues["Chanson sans image"].image.note.get_visible(), "pochette générée et note")
    verifier(vues.get("Danse des lucioles") is not None and
             vues["Danse des lucioles"].image.vignette.texture is not None, "la pochette du MP3")
    dire(miniatures=m)

    # --- clavier : la carte qui a le focus ------------------------------------------------------------------------------
    ciel = vues.get("Ciel de craie")
    if ciel is not None:
        ciel.get_parent().grab_focus()
        yield 100
        verifier(page._element_du_focus() is ciel.element, "Espace et Suppr trouvent la carte qui a le focus")
        page.grille.grab_focus()

    # --- lecture sans ouvrir le détail --------------------------------------------------------------------------------
    vagues = next(e for e in page._visibles() if e.titre == "Les vagues bleues")
    d = page.lire_rapide(vagues, muet=True)
    debut = time.monotonic()
    while time.monotonic() - debut < 15 and not (lectures().session is d and d.lecteur.en_lecture()):
        yield 100
    verifier(lectures().session is d and d.lecteur.en_lecture(), "la lecture rapide joue")
    source = str(d.lecteur.sources.get(d.lecteur.cle, ("",))[0])
    verifier(source.endswith(".mp3"), f"le son seul tant que le détail est caché : {source}")
    verifier(f.nav.get_visible_page() is page, "le détail ne s'est pas ouvert")
    yield 1500
    verifier(f.cadre.get_reveal_bottom_bars(), "la barre « en cours de lecture » apparaît")
    el = next(page.m_tri.get_item(i) for i in range(page.m_tri.get_n_items())
              if page.m_tri.get_item(i).entree is vagues)
    verifier(el.get_property("en-lecture"), "la carte sait qu'elle joue")
    page.lire_rapide(vagues)
    yield 400
    verifier(not d.lecteur.en_lecture(), "second clic : pause")
    page.lire_rapide(vagues)
    yield 1200
    avant = d.lecteur.position_ref()
    verifier(d.lecteur.en_lecture() and avant > 0.5, f"troisième clic : reprise ({avant:.2f} s)")
    pos = next(i for i in range(page.m_tri.get_n_items()) if page.m_tri.get_item(i).entree is vagues)
    page._active(pos)
    debut = time.monotonic()
    while time.monotonic() - debut < 15 and not str(d.lecteur.sources.get(d.lecteur.cle, ("",))[0]).endswith(".mp4"):
        yield 100
    yield 800
    verifier(f.nav.get_visible_page() is d, "un clic sur la carte retrouve le détail qui joue")
    source = str(d.lecteur.sources.get(d.lecteur.cle, ("",))[0])
    verifier(source.endswith(".mp4"), f"le détail montré reprend la vidéo : {source}")
    apres = d.lecteur.position_ref()
    verifier(d.lecteur.en_lecture() and apres >= avant, f"au même instant, en lecture : {avant:.2f} → {apres:.2f}")
    dire(lecture={"avant_s": round(avant, 2), "apres_s": round(apres, 2)})
    d.arreter()
    f.nav.pop()
    yield 400

    # --- « Voir le résultat » depuis les Travaux --------------------------------------------------------------------
    ciel = next(r for r in page.resultats if r.titre == "Ciel de craie")
    f.ouvrir_resultat(ciel.dossier, "jules")
    yield 600
    vue = f.nav.get_visible_page()
    verifier(isinstance(vue, PageDetail) and vue.res.titre == "Ciel de craie", "« Voir le résultat » ouvre le détail")
    f.nav.pop()
    yield 300

    # --- corbeille : la confirmation seulement -------------------------------------------------------------------------
    page._corbeille_entree(lumiere.cle)
    yield 400
    dialogue = f.get_visible_dialog() if hasattr(f, "get_visible_dialog") else None
    verifier(dialogue is not None and "2 doublages" in (dialogue.get_heading() or ""),
             f"confirmation pour les 2 versions : {dialogue and dialogue.get_heading()}")
    if dialogue is not None:
        dialogue.close()
    yield 300

    # --- un doublage finit : la liste est relue ------------------------------------------------------------------------
    fb.doublage(Path(os.environ["VOIXLA_DONNEES"]), "Nouvelle aube", "nora", "chant", 150.0, {"jours": 0},
                time.time(), videos=False)
    f.prevenir("resultats")
    debut = time.monotonic()
    while time.monotonic() - debut < 10 and len(page.versions) < 15:
        yield 100
    yield 200
    verifier(len(page.versions) == 15 and titres(page)[0] == "Nouvelle aube", f"liste relue : {titres(page)[:2]}")
    dire(page=page.mesures)
    # plus de ffmpeg de miniature en cours : rien n'écrit plus dans le dossier de l'essai après la fin
    debut = time.monotonic()
    while miniatures().occupe() and time.monotonic() - debut < 30:
        yield 100


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
