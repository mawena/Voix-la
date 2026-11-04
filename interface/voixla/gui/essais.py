"""Outils d'essai (développement) : scénarios et capture d'écran de la fenêtre sans rien ouvrir sur l'écran.

VOIXLA_CAPTURE=/chemin.png : après VOIXLA_CAPTURE_DELAI secondes (défaut 4), la fenêtre est rendue dans ce PNG
(Gtk.WidgetPaintable → Gsk ; un peu plus tard si le rendu est vide, pris pendant une mise en page), puis
l'application se ferme. VOIXLA_SCENARIO=nom joue un scénario avant la capture
(voir SCENARIOS). VOIXLA_TAILLE=1180x800 fixe la taille de la fenêtre. À lancer sous xvfb-run (outils/capturer.sh).
"""

import os
import traceback

import gi

gi.require_version("Graphene", "1.0")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402


def capturer(fenetre, chemin):
    largeur, hauteur = fenetre.get_width(), fenetre.get_height()
    peinture = Gtk.WidgetPaintable.new(fenetre)
    instantane = Gtk.Snapshot()
    peinture.snapshot(instantane, largeur, hauteur)
    noeud = instantane.to_node()
    if noeud is None:
        raise RuntimeError("fenêtre vide")
    rendu = fenetre.get_native().get_renderer()
    texture = rendu.render_texture(noeud, Graphene.Rect().init(0, 0, largeur, hauteur))
    texture.save_to_png(chemin)


def scenario(fenetre, nom):
    """Actions avant la capture : « page:detail », « voix:enregistrer », etc. (définies par les pages)."""
    if not nom:
        return
    page, _, action = nom.partition(":")
    if page == "reglages":
        fenetre.ouvrir_reglages(action or None)
        return
    p = fenetre.aller(page)
    if action and hasattr(p, "scenario"):
        p.scenario(action)


def preparer_capture(app, fenetre):
    taille = os.environ.get("VOIXLA_TAILLE")
    if taille:
        l, h = (int(x) for x in taille.lower().split("x"))
        fenetre.set_default_size(l, h)
    delai = float(os.environ.get("VOIXLA_CAPTURE_DELAI", "4"))
    nom = os.environ.get("VOIXLA_SCENARIO", "")

    def jouer():
        try:
            scenario(fenetre, nom)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        return False

    essais = [0]

    def prendre():
        try:
            capturer(fenetre, os.environ["VOIXLA_CAPTURE"])
            print("capture :", os.environ["VOIXLA_CAPTURE"], flush=True)
        except RuntimeError:
            # fenêtre vide : rendu pris pendant une mise en page (bascule de piste…) ; on réessaie à l'image suivante
            essais[0] += 1
            if essais[0] < 10:
                return True
            traceback.print_exc()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        app.quit()
        return False

    def commencer():
        if prendre():
            GLib.timeout_add(50, prendre)
        return False
    GLib.timeout_add(800, jouer)
    GLib.timeout_add(int(delai * 1000), commencer)
