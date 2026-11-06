"""Essai du dialogue « Partager un extrait » (gui/partage.py) dans la vraie fenêtre, sous Xvfb et sur un bus privé :
lancé par tests/test_export_social.py (EssaiDialogue), avec ses variables VOIXLA_* (doublage inventé de
tests/faux_partage.py, cache et configuration à part). Son coupé (fakeaudiosink). Écrit des lignes JSON :
{"erreurs": [...]} et {"fini": true}.

Vérifie : depuis le détail, l'extrait pris sur la boucle A-B du lecteur, la mention allumée, les pistes (auto-tune
comprise), la proposition du refrain (qui ne remplace pas la boucle), l'aperçu (1080 × 1920, puis 1080 × 1080 en
carré), « Écouter » (boucle A-B du lecteur posée sur l'extrait), l'export (commande imitée : avancement, « Prête »),
l'annulation (le groupe du processus arrêté), les choix gardés dans interface.json, l'image d'aperçu effacée à la
fermeture ; depuis le menu « ⋮ » de la bibliothèque : « Partager… », dialogue sans lecteur."""

import json
import os
import sys
import time
import traceback
from pathlib import Path

from voixla import gstreamer

gstreamer.preparer()          # comme python3 -m voixla : avant GTK

import voixla.gui  # noqa: E402,F401  (fixe les versions de GTK et d'Adw)
from gi.repository import GLib  # noqa: E402

from voixla import chemins  # noqa: E402
from voixla.gui import app as appli  # noqa: E402
from voixla.gui import partage  # noqa: E402
from voixla.gui.page_resultats import PageDetail  # noqa: E402

erreurs = []


def verifier(condition, message):
    if not condition:
        erreurs.append(message)
    return condition


def dire(**k):
    print(json.dumps(k, ensure_ascii=False, default=str), flush=True)


def fausse_commande(sortie, attente_s=0.0):
    """Un faux export : avancement en lignes JSON, puis (sauf annulation) une vidéo vide et le JSON final."""
    script = (
        "import json, sys, time, pathlib\n"
        f"for k in range(1, 11):\n"
        f"    time.sleep({attente_s / 10:.3f})\n"
        "    print(json.dumps({'avancement': k / 10, 'etape': 'export'}), flush=True)\n"
        f"f = pathlib.Path({str(sortie)!r}); f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b'x' * 1000)\n"
        "print(json.dumps({'sortie': str(f), 'taille_o': 1000, 'duree_s': 25.0, 'temps_s': 0.5, 'notes': []}),"
        " flush=True)\n")
    return [sys.executable, "-c", script]


def libelles(menu):
    sortie = []
    for i in range(menu.get_n_items()):
        v = menu.get_item_attribute_value(i, "label", None)
        if v is not None:
            sortie.append(v.get_string())
        for lien in ("section", "submenu"):
            sous = menu.get_item_link(i, lien)
            if sous is not None:
                sortie += libelles(sous)
    return sortie


def scenario(app):
    f = app.fenetre
    page = f.pages["resultats"]
    debut = time.monotonic()
    while not page._charge and time.monotonic() - debut < 20:
        yield 50
    res = next((r for r in page.resultats if r.titre == "Lumière du soir"), None)
    if not verifier(res is not None, "doublage inventé introuvable"):
        return

    # --- depuis le détail, avec une boucle A-B --------------------------------------------------------------------
    detail = PageDetail(page, res)
    f.pousser(detail)
    detail.lecteur.rendre_muet()
    yield 800
    detail.lecteur.definir_boucle((20.0, 45.0))
    d = detail.partager()
    yield 300
    r = d.reglages
    verifier((r.debut, r.duree) == (20.0, 25.0), f"extrait de la boucle : {(r.debut, r.duree)}")
    verifier(r.mention and d.r_mention.get_active(), "mention éteinte à l'ouverture")
    verifier(d.e_mention.get_text() == "Voix générée par IA", d.e_mention.get_text())
    verifier(d.pistes == ["doublage", "autotune", "beat", "ma_voix"], f"pistes : {d.pistes}")
    verifier(d.b_boucle.get_sensitive(), "« Prendre la boucle » grisé")
    verifier(d.e_debut.get_text() == "0:20,0" and d.e_fin.get_text() == "0:45,0",
             f"champs : {d.e_debut.get_text()} {d.e_fin.get_text()}")
    debut = time.monotonic()
    while not d.recherche_finie and time.monotonic() - debut < 30:
        yield 100
    p = d.proposition
    verifier(p is not None and p.raison == "refrain", f"proposition : {p}")
    verifier((d.reglages.debut, d.reglages.duree) == (20.0, 25.0), "la proposition a remplacé la boucle")
    verifier(d.r_traduction.get_visible(), "pas d'interrupteur de traduction")
    # aperçu : une image 1080 × 1920
    debut = time.monotonic()
    while (d.image.get_paintable() is None or d._apercu_en_cours) and time.monotonic() - debut < 30:
        yield 100
    t = d.image.get_paintable()
    verifier(t is not None and (t.get_width(), t.get_height()) == (1080, 1920),
             f"aperçu : {t and (t.get_width(), t.get_height())} {d.l_apercu.get_label()}")
    # carré : l'aperçu suit
    d.r_format.set_selected(1)
    yield 700
    debut = time.monotonic()
    while d._apercu_en_cours and time.monotonic() - debut < 30:
        yield 100
    yield 200
    t = d.image.get_paintable()
    verifier(t is not None and (t.get_width(), t.get_height()) == (1080, 1080), "aperçu carré")
    verifier(d.reglages.format == "carre", d.reglages.format)
    # « Prendre » la proposition, puis « Écouter » : la boucle du lecteur posée sur l'extrait
    d._prendre_proposition()
    verifier(abs(d.reglages.debut - p.debut) < 0.01, f"proposition prise : {d.reglages.debut}")
    d._ecouter()
    yield 300
    b = detail.lecteur.boucle
    verifier(b and abs(b[0] - d.reglages.debut) < 0.01 and abs(b[1] - d.reglages.fin) < 0.01, f"boucle : {b}")
    detail.mettre_en_pause()
    # mention éteinte : son texte grisé
    d.r_mention.set_active(False)
    verifier(not d.e_mention.get_sensitive(), "texte de la mention modifiable sans mention")
    d.r_mention.set_active(True)

    # --- export (commande imitée) ----------------------------------------------------------------------------------
    vraie = partage.es.commande_interface
    sortie = Path(os.environ["VOIXLA_DONNEES"]) / "sorties" / "Lumière du soir" / "partage" / "essai.mp4"
    partage.es.commande_interface = lambda *a, **k: fausse_commande(sortie, 0.5)
    try:
        d._exporter()
        yield 100
        verifier(d.b_annuler.get_visible() and not d.b_exporter.get_sensitive(), "boutons pendant l'export")
        debut = time.monotonic()
        while partage.export_courant() is not None and time.monotonic() - debut < 20:
            yield 100
        verifier(d.dernier == sortie, f"dernier : {d.dernier} ; état : {d.l_etat.get_label()}")
        verifier(d.l_etat.get_label().startswith("Prête : essai.mp4"), d.l_etat.get_label())
        verifier(d.b_dossier.has_css_class("suggested-action"), "« Ouvrir le dossier » pas mis en avant")
        verifier(not d.b_annuler.get_visible() and d.b_exporter.get_sensitive(), "boutons après l'export")
        # annulation : un faux export qui dure ; tout son groupe s'arrête
        partage.es.commande_interface = lambda *a, **k: fausse_commande(sortie.with_name("long.mp4"), 30.0)
        d._exporter()
        debut = time.monotonic()
        while (d.export.processus is None or d.export.fraction == 0) and time.monotonic() - debut < 20:
            yield 100
        e = d.export
        d._annuler()
        debut = time.monotonic()
        while not e.fini and time.monotonic() - debut < 15:
            yield 100
        verifier(e.fini and e.annulee and not e.ok, f"annulation : fini {e.fini}, ok {e.ok}")
        verifier(e.processus.poll() is not None, "processus encore en vie")
        verifier(d.l_etat.get_label() == "Export annulé.", d.l_etat.get_label())
        verifier(not sortie.with_name("long.mp4").exists(), "vidéo écrite malgré l'annulation")
    finally:
        partage.es.commande_interface = vraie
    apercu = d._fichier_apercu
    d.close()
    yield 300
    verifier(apercu is None or not apercu.exists(), "image d'aperçu laissée dans le cache")
    prefs = chemins.lire_preferences().get("partage") or {}
    verifier(prefs.get("format") == "carre" and prefs.get("texte_mention") == "Voix générée par IA",
             f"préférences : {prefs}")

    # --- depuis la bibliothèque (menu « ⋮ ») ------------------------------------------------------------------------
    detail.arreter()
    f.nav.pop()
    yield 400
    e = next((x for x in page.entrees.values() if x.principale.res.titre == "Lumière du soir"), None)
    verifier(e is not None and "Partager…" in libelles(page.menu_pour(e)), "pas de « Partager… » dans le menu")
    if e is not None:
        page._partager_entree(e.cle)
        yield 500
        dlg = f.get_visible_dialog() if hasattr(f, "get_visible_dialog") else None
        verifier(isinstance(dlg, partage.DialoguePartage), f"dialogue ouvert depuis la bibliothèque : {dlg}")
        if isinstance(dlg, partage.DialoguePartage):
            verifier(dlg.detail is None and not dlg.b_boucle.get_sensitive(), "boucle sans lecteur")
            verifier(dlg.reglages.format == "carre", "format gardé d'une ouverture à l'autre")
            verifier(dlg.reglages.mention, "mention éteinte à la réouverture")
            debut = time.monotonic()
            while not dlg.recherche_finie and time.monotonic() - debut < 30:
                yield 100
            yield 300
            verifier(abs(dlg.reglages.debut - dlg.proposition.debut) < 0.01, "la proposition n'a pas été prise")
            while dlg._apercu_en_cours and time.monotonic() - debut < 30:
                yield 100
            dlg.close()
            yield 200


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
