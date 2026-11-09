"""Essai de « Comparer les voix » (gui/page_resultats.py, gui/page_ecoute.py, voixla/comparaison.py) dans la vraie
fenêtre, sous Xvfb et sur un bus privé : lancé par tests/test_comparer_voix.py (Page), avec ses variables VOIXLA_*
(dossier de sortie inventé par dossier_compare : trois voix, de vrais mp3 de synthèse). Son coupé (fakeaudiosink).
Écrit des lignes JSON : {"erreurs": [...]} et {"fini": true}.

Vérifie : le groupe « Comparer les voix » du détail (trois voix, bouton « Comparer »), la page Écoute ouverte sur la
comparaison (titre, retour au banc d'essai, versions A, B et C anonymes : aucun nom de voix tant qu'on n'a pas voté),
le passage d'une version à l'autre au même instant, le vote (notes, enregistrement dans le banc de la comparaison, pas
dans celui d'essai) qui dévoile les voix, puis le retour au banc d'essai."""

import json
import os
import sys
import time
import traceback

from voixla import gstreamer

gstreamer.preparer()          # comme python3 -m voixla : avant GTK

import voixla.gui  # noqa: E402,F401  (fixe les versions de GTK et d'Adw)
from gi.repository import GLib, Gtk  # noqa: E402

from voixla import chemins, comparaison, ecoute  # noqa: E402
from voixla.gui import app as appli  # noqa: E402
from voixla.gui.page_resultats import PageDetail  # noqa: E402

erreurs = []
NOMS = ("Voix A", "Voix B", "Voix C")


def verifier(condition, message):
    if not condition:
        erreurs.append(message)
    return condition


def dire(**k):
    print(json.dumps(k, ensure_ascii=False, default=str), flush=True)


def textes(widget):
    """Les libellés (Gtk.Label, Gtk.CheckButton, titres et sous-titres Adw) sous un widget."""
    sortie = []
    if widget is None:
        return sortie
    for attribut in ("get_title", "get_subtitle", "get_description"):
        f = getattr(widget, attribut, None)
        if callable(f) and not isinstance(widget, Gtk.Window):
            try:
                t = f()
            except TypeError:
                t = None
            if t:
                sortie.append(t)
    if isinstance(widget, Gtk.Label):
        sortie.append(widget.get_label())
    w = widget.get_first_child()
    while w is not None:
        sortie += textes(w)
        w = w.get_next_sibling()
    return sortie


def attendre_que(condition, delai=30.0):
    debut = time.monotonic()
    while not condition() and time.monotonic() - debut < delai:
        yield 100


def scenario_doubler(f):
    """Page Doubler : « Comparer plusieurs voix » (cases à cocher), réglages envoyés à doubler, estimation."""
    dp = f.pages["doubler"]
    f.aller("doubler")
    yield from attendre_que(lambda: "--comparer" in f.caps and len(getattr(dp, "_ids_voix", [])) == 3, 20)
    modes = [c[0] for c in dp._choix_dispo]
    if not verifier("comparer" in modes, f"choix de voix : {modes}"):
        return
    dp.r_choix_voix.set_selected(modes.index("comparer"))
    yield 400
    verifier(dp.comparer() and dp.r_comparees.get_visible() and not dp.r_voix.get_visible(),
             "« Comparer plusieurs voix » n'affiche pas la liste des voix")
    verifier(len(dp._lignes_comparees) == 3, f"cases : {list(dp._lignes_comparees)}")
    verifier(not dp.b_lancer.get_sensitive(), "Lancer actif sans vidéo ni voix")
    cases = [c for _, c in dp._lignes_comparees.values()]
    cases[0].set_active(True)
    yield 200
    verifier(dp.voix_a_comparer() == ["voixa"] and dp._voix_choisie() is None, "une seule voix : pas de comparaison")
    verifier("au moins deux voix" in dp.r_comparees.get_subtitle(), dp.r_comparees.get_subtitle())
    cases[2].set_active(True)
    yield 200
    verifier(dp.voix_a_comparer() == ["voixa", "voixc"], f"voix cochées : {dp.voix_a_comparer()}")
    verifier(f.prefs.get("voix_comparees") == ["voixa", "voixc"], f"préférence : {f.prefs.get('voix_comparees')}")
    r = dp.reglages()
    verifier(r.get("comparer") == ["voixa", "voixc"] and r.get("voix") == "voixa", f"réglages : {r}")
    video = os.path.join(os.environ["VOIXLA_DONNEES"], "Clip.mp4")
    with open(video, "wb"):
        pass
    dp.ajouter([video])
    yield 800
    verifier(dp.b_lancer.get_sensitive(), "Lancer grisé avec une vidéo et deux voix")
    verifier("2 voix" in dp.r_comparees.get_subtitle() and "Conseil" in dp.r_comparees.get_subtitle(),
             dp.r_comparees.get_subtitle())
    une = dp._estimer([30.0], {**r, "comparer": None})
    trois = dp._estimer([30.0], {**r, "comparer": ["voixa", "voixb", "voixc"]})
    verifier(trois["kaggle"] > une["kaggle"] and abs(trois["mix"] - 3 * une["mix"]) < 0.5,
             f"estimation : une voix {une}, trois voix {trois}")
    # une seule case décochée de plus, puis retour à « Une voix pour tous » : plus de comparaison dans les réglages
    dp.r_choix_voix.set_selected(modes.index("une"))
    yield 300
    verifier("comparer" not in dp.reglages() and dp.r_voix.get_visible(), f"réglages après retour : {dp.reglages()}")
    dp.retirer(dp.videos[0])


def scenario(app):
    f = app.fenetre
    yield from scenario_doubler(f)
    f.aller("resultats")
    page = f.pages["resultats"]
    yield from attendre_que(lambda: page._charge, 20)
    res = next((r for r in page.resultats if r.titre.startswith("Chanson")), None)
    if not verifier(res is not None, "dossier comparé introuvable"):
        return
    verifier("--comparer" in f.caps, "le faux doubler n'annonce pas --comparer")
    d = PageDetail(page, res)
    f.pousser(d)
    yield 1500
    g = d.g_comparer
    if not verifier(g is not None, "pas de groupe « Comparer les voix »"):
        return
    t = textes(g)
    verifier("Comparer les voix" in t, f"titre du groupe : {t}")
    for nom in NOMS:
        verifier(any(nom == x for x in t), f"voix « {nom} » absente du groupe : {t}")
    verifier(any("une seule session" in x for x in t), f"comparaison.json non prise en compte : {t}")
    verifier(d.b_comparer.get_sensitive(), "bouton « Comparer » grisé")

    # Comparer : banc préparé dans le cache, page Écoute ouverte dessus
    d.comparer_voix()
    ecoute_page = f.pages["ecoute"]
    yield from attendre_que(lambda: ecoute_page.comparaison() and ecoute_page.extraits and not ecoute_page._chargement)
    if not verifier(ecoute_page.comparaison() and ecoute_page.extraits, "page Écoute non ouverte sur la comparaison"):
        return
    yield 800
    verifier(ecoute_page.get_title() == "Comparer des voix", f"titre : {ecoute_page.get_title()}")
    verifier(ecoute_page.b_banc.get_visible(), "pas de retour au banc d'essai")
    verifier(str(ecoute_page.racine).startswith(str(chemins.dossier_cache())), f"racine : {ecoute_page.racine}")
    ext = ecoute_page._extrait()
    if not verifier(ext is not None and [v.lettre for v in ext.versions] == ["A", "B", "C"], f"versions : {ext}"):
        return
    visibles = textes(ecoute_page.vue.get_content())
    for nom in NOMS:
        verifier(not any(nom in x for x in visibles), f"« {nom} » visible avant le vote : {visibles}")
    verifier("Chanson" in " ".join(visibles), f"titre de la chanson absent : {visibles}")

    # passer d'une version à l'autre au même instant
    lecteur = ecoute_page.lecteur
    lecteur.muet = True
    a, b = ext.versions[0], ext.versions[1]
    lecteur.jouer((ext.nom, "A", "mix"), a.mix)
    yield 2500
    m = lecteur.media()
    verifier(m is not None and m.get_playing(), "la version A ne joue pas")
    pos_a = m.get_timestamp() if m is not None else 0
    verifier(pos_a > 500_000, f"la version A n'avance pas ({pos_a} µs)")
    lecteur.basculer((ext.nom, "B", "mix"), b.mix)
    yield 1000
    m = lecteur.media()
    verifier(lecteur.cle == (ext.nom, "B", "mix"), f"version en cours : {lecteur.cle}")
    pos_b = m.get_timestamp() if m is not None else 0
    verifier(pos_a * 0.8 <= pos_b <= pos_a + 4_000_000, f"position gardée : A {pos_a} µs, B {pos_b} µs")
    libelle = ecoute_page._libelle(lecteur.cle)
    verifier(not any(n in libelle for n in NOMS), f"nom de voix dans la barre de lecture : {libelle}")
    lecteur.pause()

    # vote : les voix sont dévoilées, dans le banc de la comparaison seulement
    for lettre, note in (("A", 5), ("B", 2), ("C", 3)):
        ecoute_page._noter(ext, lettre, note)
    yield 300
    ecoute_page._enregistrer()
    yield from attendre_que(lambda: ecoute_page._vote(ecoute_page._extrait()) is not None, 10)
    vote = ecoute_page._vote(ecoute_page._extrait())
    if verifier(vote is not None, "vote non enregistré"):
        yield 600
        visibles = textes(ecoute_page.vue.get_content())
        for nom in NOMS:
            verifier(any(nom in x for x in visibles), f"« {nom} » toujours caché après le vote : {visibles}")
        verifier(vote["classement"] == ["A", "C", "B"], f"classement : {vote['classement']}")
    verifier(bool(ecoute.lire_votes(ecoute_page.racine)), "vote absent du banc de la comparaison")
    verifier(not (chemins.dossier_ecoute() / "votes.json").exists(), "vote écrit dans le banc d'essai")

    # retour au banc d'essai
    ecoute_page.quitter_comparaison()
    yield 800
    verifier(not ecoute_page.b_banc.get_visible() and not ecoute_page.comparaison(), "comparaison non quittée")
    verifier(ecoute_page.get_title() == "Écoute à l’aveugle", f"titre : {ecoute_page.get_title()}")


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
