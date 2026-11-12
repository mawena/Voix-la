"""Mesure de la mémoire de l'interface sur la durée (pas un essai : à lancer à la main, sous Xvfb, par la file de Gagarine).

  python3 -m tests.mesure_memoire DOSSIER [CYCLES] [PAUSE_S]

DOSSIER : un dossier de travail (données, cache, configuration et état y sont créés, avec la fausse bibliothèque des
captures). Chaque cycle : toutes les pages de la barre latérale, l'ouverture de quelques résultats (lecture muette,
puis retour), la page Travaux et la page État qui tournent un moment. Une ligne JSON par cycle : la mémoire du
processus (RSS), le nombre d'objets Python suivis par le ramasse-miettes, les types qui ont le plus grossi, le nombre
de pipelines GStreamer vivants (gui/lecteur.py ouvre un playbin par flux).
Variable : MESURE_TRACEMALLOC=1 ajoute les plus gros postes de tracemalloc (plus lent).
"""

import collections
import gc
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


def memoire_mo():
    for ligne in Path("/proc/self/status").read_text().splitlines():
        if ligne.startswith("VmRSS:"):
            return round(int(ligne.split()[1]) / 1024, 1)
    return None


def types_python():
    return collections.Counter(type(o).__name__ for o in gc.get_objects())


def main():
    base = preparer_environnement(sys.argv[1])
    cycles = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    pause = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
    from . import fausse_bibliotheque as fb
    if not (base / "donnees" / "sorties").is_dir():
        fb.creer(base / "donnees", videos=True)

    from voixla import gstreamer
    gstreamer.preparer()
    import voixla.gui  # noqa: F401
    from gi.repository import GLib, Gtk

    from voixla.gui import app as appli
    from voixla.gui.page_resultats import PageDetail

    suivi = os.environ.get("MESURE_TRACEMALLOC")
    if suivi:
        import tracemalloc
        tracemalloc.start(8)

    def dire(**k):
        print(json.dumps(k, ensure_ascii=False), flush=True)

    def scenario(app):
        f = app.fenetre
        page = f.pages["resultats"]
        while not page._charge:
            yield 100
        yield 1500
        avant = types_python()
        dire(depart={"rss_mo": memoire_mo(), "objets": sum(avant.values())})
        pris = [r for r in page.resultats if r.video][:3] or list(page.resultats)[:3]
        for cycle in range(1, cycles + 1):
            for tag in ("doubler", "travaux", "resultats", "karaoke", "voix", "etat"):
                f.aller(tag)
                yield int(pause * 1000 / 3)
            for r in pris:
                f.ouvrir_resultat(r.dossier, r.voix)
                yield 800
                d = f.nav.get_visible_page()
                if isinstance(d, PageDetail):
                    d.lecteur.rendre_muet()
                    d.lire()
                    yield int(pause * 1000)
                    d.arreter()
                f.nav.pop()
                d = None
                yield 1200      # l'animation du retour finie : le même détail peut se rouvrir
            f.aller("etat")
            yield int(pause * 1000)
            gc.collect()
            courant = types_python()
            plus = [(k, courant[k] - avant[k]) for k in courant if courant[k] - avant[k] > 200]
            plus.sort(key=lambda x: -x[1])
            ligne = {"cycle": cycle, "rss_mo": memoire_mo(), "objets": sum(courant.values()),
                     "pages_detail": sum(1 for o in gc.get_objects() if isinstance(o, PageDetail)),
                     "plus": plus[:8]}
            ligne["classes_voixla"] = dict(collections.Counter(
                type(o).__name__ for o in gc.get_objects() if type(o).__module__.startswith(("voixla", "page_", "lecteur",
                "commandes", "frise", "vue_paroles", "visualisations", "bibliotheque", "commun"))).most_common(12))
            if os.environ.get("MESURE_REFERENTS"):
                import types
                for o in [o for o in gc.get_objects() if isinstance(o, PageDetail)]:
                    dire(etat_survivant={"oubliee": o._oubliee, "dans_la_pile": o.dans_la_pile(),
                                         "session": app.fenetre.nav is not None and __import__('voixla.gui.lecture', fromlist=['x']).lectures().session is o,
                                         "ecouteur": o._maj_visualisations in app.fenetre.ecouteurs['visualisations']})
                    for x in gc.get_referrers(o):
                        if isinstance(x, types.FrameType):
                            continue
                        dire(ref=type(x).__name__, detail=(
                            x.__func__.__qualname__ if isinstance(x, types.MethodType) else
                            list(x.keys())[:8] if isinstance(x, dict) else repr(x)[:100]))
                        if isinstance(x, (types.MethodType, dict)):
                            for y in gc.get_referrers(x):
                                if not isinstance(y, types.FrameType):
                                    dire(ref2=type(y).__name__, detail=repr(y)[:140])
            if suivi:
                import tracemalloc
                ligne["tracemalloc"] = [str(s)[:160] for s in tracemalloc.take_snapshot().statistics("lineno")[:6]]
            dire(**ligne)
            if cycle == 1:
                avant = courant
        gc.collect()
        dire(fin={"rss_mo": memoire_mo()})

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
