"""Essai de la page Karaoké dans la vraie fenêtre, sous Xvfb et sur un bus privé : lancé par tests/test_karaoke.py
(EssaiPage), avec ses variables VOIXLA_* (données inventées de tests/faux_karaoke.py, cache et configuration à part).
VOIXLA_MICRO_ESSAI=1 : micro de synthèse (audiotestsrc) et sortie fakeaudiosink, aucun son ni micro réel ; la réécoute
est muette. Écrit des lignes JSON : {"erreurs": [...]}, {"fini": true} et des mesures.

Vérifie : liste des doublages qui ont un beat, infos (paroles, mélodie), passage par lignes et en boucle, une vraie
prise de synthèse (décompte, beat et micro dans le même pipeline, mesures de calage, arrêt seul à la fin du beat),
la note et le mix en tâche de fond, la prise gardée puis rouverte, le MP3, l'annulation pendant le décompte (rien ne
reste), une prise imitée (VOIXLA_KARAOKE_PRISE) notée comme une vraie, la prise jetée, la latence gardée dans
interface.json, « Chanter… » depuis la bibliothèque, et Alt+7."""

import json
import os
import sys
import time
import traceback
from pathlib import Path

from voixla import chemins, gstreamer

gstreamer.preparer()          # comme python3 -m voixla : avant GTK

import voixla.gui  # noqa: E402,F401  (fixe les versions de GTK et d'Adw)
from gi.repository import GLib  # noqa: E402

from voixla import karaoke as K  # noqa: E402
from voixla.gui import app as appli  # noqa: E402
from voixla.gui import page_karaoke as PK  # noqa: E402

erreurs = []


def verifier(condition, message):
    if not condition:
        erreurs.append(message)
    return condition


def dire(**k):
    print(json.dumps(k, ensure_ascii=False, default=str), flush=True)


def attendre(condition, delai=30.0):
    """Générateur : rend la main toutes les 50 ms jusqu'à condition() (ou le délai)."""
    debut = time.monotonic()
    while not condition() and time.monotonic() - debut < delai:
        yield 50


def scenario(app):
    f = app.fenetre
    page = f.aller("karaoke")
    yield from attendre(lambda: page._charge and page.chanson is not None and "paroles" in page.chanson
                        and page.chanson.get("lignes"))
    verifier(len(page.chansons) == 1, f"un doublage avec un beat : {len(page.chansons)}")
    c = page.chanson
    verifier(c and c.get("duree") and abs(c["duree"] - 31.8) < 0.1, f"durée du beat : {c and c.get('duree')}")
    verifier(c and len(c.get("lignes") or []) == 8, f"8 lignes : {len(c.get('lignes') or [])}")
    verifier(c and c.get("reference") == "courbes.json", f"référence : {c and c.get('reference')}")
    verifier(page.pile.get_visible_child_name() == "preparer", page.pile.get_visible_child_name())
    verifier(page.titre.get_subtitle() == "Brise de mer", page.titre.get_subtitle())

    # --- passage : lignes 1 et 2, en boucle de 2 tours ---------------------------------------------------------------
    page.r_mode.set_selected(2)
    page.r_tours.set_value(2)
    page.r_de.set_selected(0)
    page.r_a.set_selected(1)
    yield 100
    p = page._passage()
    verifier(abs(p.debut - 0.0) < 1e-6 and abs(p.fin - 9.6) < 1e-6 and p.repetitions == 2, f"passage : {p}")
    verifier("2 fois" in page.l_passage.get_label(), page.l_passage.get_label())
    verifier(page.r_tours.get_visible() and page.r_de.get_visible(), "tours et lignes visibles en boucle")
    page.r_mode.set_selected(1)
    yield 100
    verifier(not page.r_tours.get_visible(), "pas de tours pour un extrait")
    page.regler_latence(120)
    verifier(chemins.lire_preferences()["karaoke"]["latence_ms"] == 120, "latence gardée dans interface.json")

    # --- une vraie prise de synthèse : lignes 1 et 2, sans boucle ----------------------------------------------------
    page.commencer()
    yield from attendre(lambda: page.etat == "decompte" and page.enr is not None, 10)
    verifier(page.pile.get_visible_child_name() == "prise", "vue de la prise")
    verifier(page.l_decompte.get_visible(), "décompte visible")
    pendante = page.prise
    verifier(pendante is not None and pendante.name.startswith(".prise-"), f"prise cachée : {pendante}")
    yield from attendre(lambda: page.etat == "prise", 8)
    verifier(not page.l_decompte.get_visible(), "décompte fini")
    yield 1500
    u = page.enr.position_beat() if page.enr else None
    verifier(u is not None and 0.5 < u < 3.0, f"position du beat après 1,5 s : {u}")
    t, lecture = page._position()
    verifier(lecture and 0.5 < t < 3.0, f"position des paroles : {t}")
    yield from attendre(lambda: page.etat == "resultat", 45)
    verifier(page.etat == "resultat", f"prise notée (état {page.etat}) : {page.l_commentaire.get_label()}")
    plan = K.lire_plan(page.prise)
    m = plan.get("mesure") or {}
    verifier(m.get("releves", 0) > 5, f"relevés du beat : {m}")
    verifier(abs(m.get("prise_debut_s", 9) - m.get("beat_debut_s", 0)) < 0.5, f"mesures de calage : {m}")
    verifier(plan.get("latence_ms") == 120, f"latence de la prise : {plan.get('latence_ms')}")
    res = plan.get("resultat") or {}
    dire(prise_synthese={"mesure": m, "score": res.get("score"), "decalage_s": res.get("decalage_s")})
    verifier(isinstance(res.get("score"), int), f"note de la prise : {res.get('score')}")
    verifier(abs(res.get("decalage_s", 9) - K.decalage(plan)) < 1e-3, "décalage du plan")
    mix = Path(res.get("mix") or "/")
    verifier(mix.is_file() and abs(K.duree_audio(mix) - 9.6) < 0.05, f"mix : {mix} ({K.duree_audio(mix)} s)")
    verifier(page.t_mix.get_sensitive() and page.b_garder.get_sensitive(), "réécoute du mix et garder possibles")
    prise_wav = page.prise
    # la prise s'arrête seule : la fin du beat, plus la latence et un peu plus
    verifier(9.6 < K.duree_audio(prise_wav) < 11.5, f"durée de la prise : {K.duree_audio(prise_wav)}")

    # réécoute : ta voix, puis avec le beat (muettes)
    page.t_mix.set_active(True)
    yield 400
    verifier(page.ecoute == "mix" and page.lecture is not None, "écoute du mix")
    verifier(page.lecture is not None and page.lecture.get_muted(), "réécoute muette dans les essais")
    page.t_voix.set_active(True)
    yield 200

    # garder, puis rouvrir depuis la liste des prises gardées
    page.garder()
    verifier(page.prise is not None and page.prise.name.startswith("prise-") and page.prise.is_file(),
             f"prise gardée : {page.prise}")
    verifier(K.plan_de(page.prise).is_file() and not pendante.exists(), "JSON suivi, plus de prise cachée")
    gardee = page.prise
    page.nouvelle_prise()
    yield 200
    verifier(page.etat == "preparer" and len(page._lignes_gardees) == 1,
             f"prises gardées : {len(page._lignes_gardees)}")
    page.rouvrir(gardee)
    yield 300
    verifier(page.etat == "resultat" and not page.b_garder.get_visible(), "prise gardée rouverte")

    # MP3 du mix
    page.exporter_mp3()
    dossier = Path(c["dossier"])
    yield from attendre(lambda: any((dossier / "karaoke").glob("*.mp3")), 30)
    mp3 = sorted((dossier / "karaoke").glob("*.mp3"))
    verifier(len(mp3) == 1 and "karaoké" in mp3[0].name, f"MP3 : {mp3}")
    page.nouvelle_prise()
    yield 200

    # --- annulation pendant le décompte : rien ne reste -------------------------------------------------------------
    page.commencer()
    yield from attendre(lambda: page.etat == "decompte" and page.enr is not None, 10)
    page.annuler()
    yield 300
    verifier(page.etat == "preparer", f"annulée : {page.etat}")
    verifier(not list((dossier / "karaoke").glob(".prise-*")), "rien de caché après l'annulation")

    # --- prise imitée : chante toute la chanson, une octave plus bas, une ligne trop haute ----------------------------
    page._scenario("resultat")
    yield from attendre(lambda: page.etat == "resultat", 45)
    res = page.resultat or {}
    tour = (res.get("tours") or [{}])[0]
    faux = [l for l in tour.get("lignes") or [] if l.get("score") is not None and l["score"] < 60]
    dire(prise_imitee={"score": res.get("score"), "lignes": [l.get("score") for l in tour.get("lignes") or []]})
    verifier(res.get("score") is not None and 75 <= res["score"] <= 92, f"note de la prise imitée : {res.get('score')}")
    verifier(res.get("octave") == -1, f"octave : {res.get('octave')}")
    verifier(len(faux) == 1 and faux[0]["i"] == 2 and "trop haut" in faux[0]["conseil"], f"ligne fausse : {faux}")
    verifier("1 ligne fausse" in page.l_details.get_label(), page.l_details.get_label())
    verifier(len(page._rangees) == 8, f"8 lignes affichées : {len(page._rangees)}")
    imitee = page.prise
    page.jeter()
    yield 200
    verifier(not imitee.exists() and not K.plan_de(imitee).exists(), "prise jetée")
    verifier(page.etat == "preparer", page.etat)

    # --- calibration : au micro de synthèse (un son continu), les bips ne sont pas entendus ---------------------------
    d = PK.DialogueCalibration(page)
    d.present(f)
    d.lancer()
    yield from attendre(lambda: "Mesure" in d.etat.get_label() or "Latence :" in d.etat.get_label(), 20)
    verifier("bips mal entendus" in d.etat.get_label(), f"calibration : {d.etat.get_label()}")
    verifier(d.b_lancer.get_sensitive() and not d.b_garder.get_sensitive(), "calibration à refaire, rien à garder")
    verifier(not d.fichier.exists(), "enregistrement des bips effacé")
    d.close()
    yield 200
    verifier(chemins.lire_preferences()["karaoke"]["latence_ms"] == 120, "latence inchangée")

    # --- « Chanter… » depuis la bibliothèque, et Alt+7 ---------------------------------------------------------------
    biblio = f.aller("resultats")
    yield from attendre(lambda: biblio._charge, 20)
    e = next(iter(biblio.entrees.values()), None)
    menu = biblio.menu_pour(e) if e is not None else None
    libelles = []
    if menu is not None:
        for i in range(menu.get_n_items()):
            section = menu.get_item_link(i, "section")
            for j in range(section.get_n_items() if section else 0):
                v = section.get_item_attribute_value(j, "label", None)
                libelles.append(v.get_string() if v is not None else "")
    verifier("Chanter…" in libelles, f"menu « ⋮ » : {libelles}")
    page.chanson = None
    biblio._chanter_entree(e.cle)
    yield from attendre(lambda: page.chanson is not None, 10)
    verifier(f.page_courante == "karaoke", f"page : {f.page_courante}")
    verifier(page.chanson and page.chanson["dossier"] == e.principale.res.dossier, "doublage choisi")
    verifier(app.get_accels_for_action("win.section-karaoke") == ["<Alt>7"],
             f"Alt+7 : {app.get_accels_for_action('win.section-karaoke')}")
    verifier(app.get_accels_for_action("win.section-etat") == ["<Alt>6"], "État garde Alt+6")
    r = PK.reglages({"karaoke": {"latence_ms": 80}})
    verifier(r["latence_ms"] == 80 and r["repetitions"] == 3, f"réglages complétés : {r}")
    # la bibliothèque ne prend pas karaoke/ pour un doublage
    verifier(len(biblio.versions) == 1, f"un seul doublage dans la bibliothèque : {len(biblio.versions)}")


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
    os.environ["VOIXLA_MICRO_ESSAI"] = "1"
    GLib.timeout_add_seconds(240, lambda: (dire(erreurs=["trop long"]), os._exit(3)))
    sys.exit(Essai().run(sys.argv[:1]))
