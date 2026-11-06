"""Mesure de la page Résultats avec beaucoup de doublages (pas un essai : à lancer à la main, sous Xvfb).

  python3 -m tests.mesure_bibliotheque DOSSIER [N]

DOSSIER : un dossier de travail neuf (données, cache, configuration et état y sont créés) ; N : 200 par défaut. Les
vidéos sont des vidéos de synthèse 1280 × 720 (20, liées en dur sous N noms). Écrit une ligne JSON par mesure :
- ouverture : lecture du disque (dans un fil), remplissage du modèle, première image de la grille ;
- miniatures : à froid, le temps pour que les cartes du premier écran aient leur image ; puis la préparation de toutes
  (deux à la fois, sous nice -n 19 et ionice -c3) ; à chaud (cache plein), le premier écran relu ;
- défilement : de haut en bas, par quarts d'écran, la durée de chaque image (mise en page et dessin), grille et liste.
"""

import json
import os
import sys
import time
from pathlib import Path


def preparer_environnement(base):
    base = Path(base).resolve()
    for d in ("donnees", "config", "etat", "cache", "ecoute", "xdg-etat"):
        (base / d).mkdir(parents=True, exist_ok=True)
    os.environ.update({"VOIXLA_DONNEES": str(base / "donnees"), "VOIXLA_CONFIG": str(base / "config"),
                       "VOIXLA_ETAT": str(base / "etat"), "VOIXLA_CACHE": str(base / "cache"),
                       "VOIXLA_ECOUTE": str(base / "ecoute"), "XDG_STATE_HOME": str(base / "xdg-etat"),
                       "VOIXLA_SANS_PREPARATION": "1", "VOIXLA_PAGE": "resultats",
                       "VOIXLA_DOUBLER": f"{sys.executable} {Path(__file__).with_name('faux_doubler.py')}",
                       "VOIXLA_APP_ID": f"io.mawena.VoixLa.Mesure{os.getpid()}"})
    return base


def main():
    base = preparer_environnement(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    from . import fausse_bibliotheque as fb
    sorties = base / "donnees" / "sorties"
    if not sorties.is_dir() or not any(sorties.iterdir()):
        fb.IMAGES[:] = [i.replace("640x360", "1280x720").replace("640:360", "1280:720") for i in fb.IMAGES]
        debut = time.monotonic()
        fb.creer(base / "donnees", n=n, videos=True)
        print(json.dumps({"donnees_creees_s": round(time.monotonic() - debut, 1)}), flush=True)

    from voixla import gstreamer
    gstreamer.preparer()
    import voixla.gui  # noqa: F401
    from gi.repository import GLib, Gtk

    from voixla.gui import app as appli
    from voixla.gui.bibliotheque import Carte
    from voixla.gui.vignettes import miniatures

    def dire(**k):
        print(json.dumps(k, ensure_ascii=False), flush=True)

    def cartes(w):
        sortie, e = [], w.get_first_child()
        while e is not None:
            if isinstance(e, Carte) and e.element is not None and e.get_mapped():
                sortie.append(e)
            sortie += cartes(e)
            e = e.get_next_sibling()
        return sortie

    def peindre(widget, action):
        """Fait action(), puis suit l'image suivante : fin["t"] à la fin du dessin, fin["mise_en_page"] au début de la
        mise en page (où la grille crée et relie ses cartes) ; le travail de l'image va de l'un à l'autre."""
        horloge = widget.get_frame_clock()
        fin = {}
        ids = [horloge.connect("layout", lambda *_: fin.setdefault("mise_en_page", time.perf_counter())),
               horloge.connect("after-paint", lambda *_: fin.setdefault("t", time.perf_counter()))]
        t0 = time.perf_counter()
        action()
        widget.queue_draw()
        return horloge, ids, t0, fin

    def scenario(app):
        f = app.fenetre
        page = f.pages["resultats"]
        t_debut = time.monotonic()
        while not page._charge:
            yield 10
        t_charge = time.monotonic()
        horloge, ids, t0, fin = peindre(page.grille, lambda: None)
        while "t" not in fin:
            yield 5
        for i in ids:
            horloge.disconnect(i)
        dire(ouverture={"doublages": len(page.versions), "entrees": page.m_tri.get_n_items(),
                        "lecture_disque_s": round(page.mesures["lecture_disque_s"], 3),
                        "remplissage_s": round(page.mesures["remplissage_s"], 3),
                        "paquets": len(page.mesures["paquets_s"]),
                        "paquet_max_s": round(page.mesures["paquet_max_s"], 3),
                        "chargement_s": round(page.mesures["chargement_s"], 3),
                        "depuis_la_page_s": round(t_charge - t_debut, 3),
                        "premiere_image_s": round(fin["t"] - t0, 3)})
        # miniatures du premier écran, à froid
        debut = time.monotonic()
        while time.monotonic() - debut < 120:
            visibles = cartes(page.grille)
            if visibles and all(c.image.vignette.texture is not None or c.image.note.get_visible() and
                                miniatures().impossible(c.element.entree.principale.image_sig) or
                                c.element.entree.principale.image is None for c in visibles):
                break
            yield 50
        dire(premier_ecran_a_froid={"cartes": len(cartes(page.grille)), "s": round(time.monotonic() - debut, 2),
                                    **{k: v for k, v in miniatures().mesures.items() if k in ("preparees", "lues")}})
        # défilement de la grille, cache vide (les miniatures se demandent au passage)
        for nom, vue, defile in (("grille", page.grille, page.defil_grille), ("liste", page.colonnes,
                                                                             page.defil_liste)):
            if nom == "liste":
                page.a_vue.change_state(GLib.Variant.new_string("liste"))
                yield 300
            adj = defile.get_vadjustment()
            adj.set_value(0)
            yield 200
            durees = []
            while adj.get_value() + adj.get_page_size() < adj.get_upper() - 1:
                cible = min(adj.get_upper() - adj.get_page_size(), adj.get_value() + adj.get_page_size() / 4)
                horloge, ids, t0, fin = peindre(vue, lambda c=cible: adj.set_value(c))
                while "t" not in fin:
                    yield 2
                for i in ids:
                    horloge.disconnect(i)
                durees.append(fin["t"] - fin.get("mise_en_page", t0))
                yield 16
            durees.sort()
            dire(defilement={"vue": nom, "pas": len(durees), "mediane_ms": round(1000 * durees[len(durees) // 2], 1),
                             "p95_ms": round(1000 * durees[int(len(durees) * 0.95)], 1),
                             "max_ms": round(1000 * durees[-1], 1)})
        page.a_vue.change_state(GLib.Variant.new_string("grille"))
        yield 300
        # toutes les miniatures (comme si l'on faisait tout défiler lentement) : préparation en tâche de fond
        m = miniatures()
        attente = [v for v in page.versions if v.image_sig and m.texture(v.image_sig) is None
                   and not m.impossible(v.image_sig)]
        avant = dict(m.mesures)
        debut = time.monotonic()
        recues = []
        for v in attente:
            m.demander(v.image_sig, v.image, lambda t: recues.append(t))
        while len(recues) < len(attente) and time.monotonic() - debut < 900:
            yield 200
        duree = time.monotonic() - debut
        faites = m.mesures["preparees"] - avant["preparees"]
        dire(toutes_les_miniatures={"demandees": len(attente), "recues": len(recues), "preparees": faites,
                                    "s": round(duree, 1), "par_miniature_s": round(duree / max(1, faites), 3),
                                    "ffmpeg_par_miniature_s": round((m.mesures["duree_preparation_s"] -
                                                                     avant["duree_preparation_s"]) / max(1, faites),
                                                                    3)})
        # à chaud (cache disque plein, rien en mémoire) : les 16 premières images relues
        m._textures.clear()
        avant = dict(m.mesures)
        premieres = [e.principale for e in page._visibles() if e.principale.image_sig
                     and not m.impossible(e.principale.image_sig)][:16]
        recues = []
        debut = time.monotonic()
        for v in premieres:
            m.demander(v.image_sig, v.image, lambda t: recues.append(t))
        while len(recues) < len(premieres) and time.monotonic() - debut < 30:
            yield 5
        lues = m.mesures["lues"] - avant["lues"]
        dire(premier_ecran_a_chaud={"images": len(recues), "s": round(time.monotonic() - debut, 3), "lues": lues,
                                    "lecture_par_image_ms": round(1000 * (m.mesures["duree_lecture_s"] -
                                                                          avant["duree_lecture_s"]) / max(1, lues),
                                                                  1)})
        # rechargement complet (F5), rien n'a changé : rien n'est refait
        page.mesures.pop("chargement_s", None)
        page.actualiser()
        while page.mesures.get("chargement_s") is None:
            yield 5
        dire(actualiser={"chargement_s": round(page.mesures["chargement_s"], 3),
                         "remplissage_s": round(page.mesures["remplissage_s"], 3)})

    class Mesure(appli.Application):
        def do_activate(self):
            appli.Application.do_activate(self)
            gen = scenario(self)

            def suivant():
                try:
                    attente = next(gen)
                except StopIteration:
                    self.quitter_tout()
                    return False
                except Exception:  # noqa: BLE001
                    import traceback
                    traceback.print_exc()
                    self.quitter_tout()
                    return False
                GLib.timeout_add(max(1, int(attente)), suivant)
                return False
            GLib.timeout_add(100, suivant)

    Gtk.Window.set_default_icon_name("io.mawena.VoixLa")
    return Mesure().run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
