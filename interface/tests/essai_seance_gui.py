"""Essai de la séance d'enregistrement et de la barre latérale dans la vraie fenêtre, sous Xvfb et sur un bus privé :
lancé par tests/test_seance_gui.py, avec ses variables VOIXLA_* (données inventées de tests/faux_seance.py, cache à part).
Sortie muette (fakeaudiosink). Écrit des lignes JSON : {"erreurs": [...]} et {"fini": true}.

Vérifie : la barre latérale sans « Écoute à l'aveugle » (6 lignes), la page qui reste ouverte (menu ☰, Alt+5, aucune ligne
sélectionnée), la séance dans la page Voix (étapes, consignes, chansons conseillées d'abord, tonalité conseillée,
préparation des pistes, lecteur, paroles décalées de 5 s), l'import et ses contrôles, les durées enregistrées."""

import json
import os
import sys
import time
import traceback
from pathlib import Path

from voixla import gstreamer

gstreamer.preparer()          # comme python3 -m voixla : avant GTK

import voixla.gui  # noqa: E402,F401  (fixe les versions de GTK et d'Adw)
from gi.repository import Gtk, GLib  # noqa: E402

from voixla import chemins, seance as S  # noqa: E402
from voixla.gui.seance import CHOIX_IMPORT as S_CHOIX  # noqa: E402
from voixla.gui import app as appli  # noqa: E402

from . import faux_seance as fs  # noqa: E402

erreurs = []


def verifier(condition, message):
    if not condition:
        erreurs.append(message)
    return condition


def dire(**k):
    print(json.dumps(k, ensure_ascii=False, default=str), flush=True)


def attendre(condition, delai=30.0):
    debut = time.monotonic()
    while not condition() and time.monotonic() - debut < delai:
        yield 100


def etiquettes(widget, sortie=None):
    """Tous les textes des Gtk.Label sous ce widget."""
    sortie = [] if sortie is None else sortie
    if isinstance(widget, Gtk.Label):
        sortie.append(widget.get_label())
    c = widget.get_first_child()
    while c is not None:
        etiquettes(c, sortie)
        c = c.get_next_sibling()
    return sortie


def lignes_barre(f):
    n, i = [], 0
    while (r := f.liste.get_row_at_index(i)) is not None:
        n.append(r.tag)
        i += 1
    return n


def scenario_navigation(f):
    tags = lignes_barre(f)
    verifier(tags == ["doubler", "travaux", "resultats", "karaoke", "voix", "etat"], f"barre latérale : {tags}")
    verifier("ecoute" not in f.lignes, "ligne « ecoute » encore dans la barre latérale")
    titres = [e for r in range(len(tags)) for e in etiquettes(f.liste.get_row_at_index(r))]
    verifier(not any("aveugle" in t for t in titres), f"titres : {titres}")
    verifier(appli.RACCOURCIS_SECTIONS["ecoute"] == 5, "Alt+5 perdu")
    verifier(not any(t == "ecoute" for t, *_ in appli.SECTIONS), "SECTIONS garde l'écoute")
    verifier("ecoute" in f.pages and not f.pages["ecoute"].__class__.__name__.startswith("PageProvisoire"),
             f"page écoute : {f.pages.get('ecoute')}")
    # la page reste accessible : action du menu ☰ et d'Alt+5, sans ligne sélectionnée dans la barre
    a = f.lookup_action("section-ecoute")
    verifier(a is not None, "action section-ecoute absente")
    f.aller("voix")
    yield 300
    a.activate(None)
    yield 600
    verifier(f.page_courante == "ecoute" and f.nav.get_visible_page() is f.pages["ecoute"],
             f"page courante : {f.page_courante}")
    verifier(f.liste.get_selected_row() is None, "une ligne reste sélectionnée sur la page hors barre")
    acc = f.get_application().get_accels_for_action("win.section-ecoute")
    verifier(acc == ["<Alt>5"], f"accélérateur : {acc}")
    f.aller("voix")
    yield 300
    verifier(f.liste.get_selected_row() is f.lignes["voix"], "ligne Voix non sélectionnée au retour")
    f.aller("karaoke")
    yield 200
    verifier(f.page_courante == "karaoke", f"Alt+7 : {f.page_courante}")


def scenario_seance(f):
    page_voix = f.pages["voix"]
    f.aller("voix")
    yield from attendre(lambda: page_voix.voix_chant == "voixA" or len(f.voix) == 2, 20)
    page_voix.r_voix_chant.set_selected([v["id"] for v in f.voix].index("voixA"))
    yield 300
    verifier(page_voix.voix_chant == "voixA", f"voix choisie : {page_voix.voix_chant}")
    p = page_voix.ouvrir_seance()
    if not verifier(p is not None, "séance non ouverte"):
        return
    yield 800
    verifier(f.nav.get_visible_page() is p, "séance non affichée")
    verifier(len(lignes_barre(f)) == 6, "la séance a ajouté un onglet")
    textes = etiquettes(p)
    verifier(any("20 cm" in t for t in textes), "consigne téléphone à 20 cm absente")
    verifier(any("mode avion" in t for t in textes), "mode avion absent")
    verifier(any("4e bip" in t for t in textes), "consigne du 4e bip absente")
    verifier(any("WhatsApp" in t for t in textes), "consigne WhatsApp absente")
    verifier(any("casque" in t.lower() and "volume" in t.lower() for t in textes), "consigne du casque absente")
    verifier(len(p._boutons_etape) == 5, f"étapes : {list(p._boutons_etape)}")
    verifier(p.etape.cle == "parole" and not p.b_chanson.get_visible() and not p.b_lecteur.get_visible(),
             "étape parole : pas de piste")
    # « Comment ça marche » : 5 lignes ; l'étape 1 fournit son texte en grand, sa durée, son nom de fichier
    verifier(any("Comment ça marche" in t for t in textes), "encadré « Comment ça marche » absent")
    verifier(sum(1 for t in textes if t[:3] in ("1. ", "2. ", "3. ", "4. ", "5. ") and "  " in t[:5]) >= 5,
             "les 5 lignes de « Comment ça marche »")
    verifier(p.b_texte.get_visible() and any("Le matin, quand la ville se réveille" in t for t in textes),
             "texte de l'étape 1 absent")
    verifier(any(t.startswith("Voilà. Merci de m'avoir écouté") for t in textes), "fin du texte absente")
    verifier(any("Parle librement (1 min)" in t for t in textes), "parole libre absente")
    verifier(sum(1 for t in textes if "Raconte la meilleure journée" in t or "Décris ton plat préféré" in t
                 or "comment venir chez toi" in t) == 3, "trois sujets libres")
    verifier("1-parole" in p.l_nom_fichier.get_label(), f"nom de fichier : {p.l_nom_fichier.get_label()}")
    verifier(p.b_duree.get_label().startswith("≈ 3 min"), f"durée : {p.b_duree.get_label()}")
    t0 = p.etat["taille_texte"]
    p._taille_texte(1)
    verifier(p.etat["taille_texte"] > t0, "taille du texte non augmentée")
    # la case « Fait » est gardée dans voix/voixA/seance.json
    p.c_fait.set_active(True)
    brut = chemins.lire_json(p.dossier_voix / "seance.json", {})
    verifier(brut.get("fait", {}).get("parole") is True, f"seance.json : {brut}")
    verifier(brut.get("taille_texte") == p.etat["taille_texte"], f"taille gardée : {brut}")
    verifier(p._boutons_etape["parole"].get_label().startswith("✓"), "étape faite non marquée")

    # les durées déjà enregistrées (95 + 200 s de chant, 125 s de parole)
    yield from attendre(lambda: "4 min" in p.r_parole.get_subtitle() or "2 min" in p.r_parole.get_subtitle(), 10)
    verifier("objectif de 10 min" in p.r_chant.get_subtitle() and "4 min" in p.r_chant.get_subtitle(),
             f"chant : {p.r_chant.get_subtitle()}")
    verifier("objectif de 5 min" in p.r_parole.get_subtitle() and "2 min" in p.r_parole.get_subtitle(),
             f"parole : {p.r_parole.get_subtitle()}")

    # tessiture : une piste guide, réglée sur la voix (voix A : parole 100 Hz → centre 200 Hz)
    p.choisir_etape("tessiture")
    yield from attendre(lambda: p._piste is not None, 60)
    verifier(p.centre_hz == 200.0, f"centre : {p.centre_hz}")
    verifier(p._piste and p._piste[0] == "tessiture" and p._piste[1].is_file(), f"piste de tessiture : {p._piste}")
    verifier(p.b_lecteur.get_visible() and not p.b_chanson.get_visible(), "tessiture : lecteur ou doublage mal affichés")
    verifier("2-tessiture" in p.l_nom_fichier.get_label(), p.l_nom_fichier.get_label())
    yield from attendre(lambda: p.lecteur.duree() is not None, 10)
    d = p.lecteur.duree()
    verifier(d is not None and abs(d - S.TESSITURE_S) < 3.5, f"durée de la tessiture : {d}")
    verifier(p.vue_paroles.paroles is not None and any(l.texte.startswith("Sirène sur « aaa »")
                                                       for l in p.vue_paroles.paroles.originale.lignes),
             "ce qui s'affiche pendant la tessiture")
    verifier(not p.b_choix_voix.get_visible(), "choix grave/aiguë proposé alors que la voix est mesurée")

    # notes à tenir : la piste se prépare puis se charge dans le lecteur
    p.choisir_etape("notes")
    yield from attendre(lambda: p._piste is not None, 30)
    verifier(p._piste and p._piste[0] == "notes" and p._piste[1].is_file(), f"piste des notes : {p._piste}")
    verifier(p.b_lecteur.get_visible(), "lecteur des notes caché")
    yield from attendre(lambda: p.lecteur.duree() is not None, 10)
    d = p.lecteur.duree()
    verifier(d is not None and abs(d - 96) < 1, f"durée des notes : {d}")

    # chanson : conseillées d'abord (voix A : parole 100 Hz → zone 200 Hz ; A proche, B +6, C +12)
    p.choisir_etape("chanson")
    yield from attendre(lambda: len(p.conseils) == 2, 20)
    titres = [c.doublage.titre for c in p.conseils]
    # C est chantée en anglais : cachée, la voix ne parle que français
    verifier(titres == ["Chanson d'exemple A", "Chanson d'exemple B"], f"ordre : {titres}")
    p.r_langues.set_active(True)
    yield 300
    titres = [c.doublage.titre for c in p.conseils]
    verifier(titres == ["Chanson d'exemple A", "Chanson d'exemple B", "Chanson d'exemple C"],
             f"toutes les langues : {titres}")
    verifier("anglais" in p.r_doublage.get_model().get_string(2), p.r_doublage.get_model().get_string(2))
    verifier("français" in p.r_langues.get_subtitle(), p.r_langues.get_subtitle())
    verifier(p.r_doublage.get_selected() == 0 and p.tonalite() == 0, f"tonalité conseillée : {p.tonalite()}")
    verifier(p.b_chanson.get_visible() and p.b_paroles.get_visible(), "chanson : réglages ou paroles cachés")
    p.r_doublage.set_selected(1)
    yield 300
    verifier(p.tonalite() == -6, f"tonalité conseillée pour B : {p.tonalite()}")
    verifier("conseillé : 6 demi-tons plus bas" in p.l_conseil.get_label(), p.l_conseil.get_label())
    # paroles du doublage, décalées de 5 s, avec la mention de la transcription automatique
    par = p.vue_paroles.paroles
    verifier(par is not None and len(par.originale.lignes) == 5, f"paroles : {par}")
    verifier(par and abs(par.originale.lignes[0].debut - 7.0) < 0.01, f"décalage des paroles : "
                                                                      f"{par and par.originale.lignes[0].debut}")
    verifier(any("Transcription automatique, il peut y avoir des fautes" in t for t in etiquettes(p.b_paroles)),
             "mention de la transcription automatique absente")
    # un doublage sans .lrc : message, pas de mention
    p.r_doublage.set_selected(2)
    yield 300
    verifier(p.vue_paroles.paroles is None and not p.l_note_paroles.get_visible(), "paroles d'un doublage sans .lrc")
    p.r_doublage.set_selected(1)
    yield 300
    p.r_tonalite.set_selected(S.TONALITES.index(-3))
    yield 200
    # prise : musique seule, à −3 demi-tons
    p.preparer_chanson("musique")
    yield from attendre(lambda: p._piste is not None and p._piste[0] == "musique", 90)
    verifier(p._piste and p._piste[0] == "musique" and p._piste[1].name == "musique_m3.mp3", f"piste : {p._piste}")
    yield from attendre(lambda: p.lecteur.duree() is not None, 10)
    d = p.lecteur.duree()
    verifier(d is not None and abs(d - 19) < 1.5, f"durée de la prise : {d}")   # 5 s de bips + 14 s de chanson
    yield 1500
    verifier(p.lecteur.en_lecture(), "la lecture n'a pas démarré")
    verifier(p.l_restant.get_label().startswith("reste "), f"temps restant : {p.l_restant.get_label()!r}")
    p.lecteur.pause()
    # guide avec la voix
    p.preparer_chanson("guide")
    yield from attendre(lambda: p._piste is not None and p._piste[0] == "guide", 90)
    verifier(p._piste and p._piste[0] == "guide" and p._piste[1].name == "guide_m3.mp3", f"guide : {p._piste}")
    p.lecteur.pause()

    # chanson libre : deux chansons du domaine public, dans la voix (centre 200 Hz), avec leurs paroles
    p.choisir_etape("libre")
    yield 300
    verifier(p.b_libre.get_visible() and p.b_publique.get_visible() and not p.b_chanson.get_visible(),
             "étape 5 : réglages de la chanson libre")
    verifier("5-au-clair-de-la-lune" in p.l_nom_fichier.get_label() and "5-frere-jacques" in p.l_nom_fichier.get_label(),
             p.l_nom_fichier.get_label())
    verifier(p.vue_paroles.paroles is not None and p.vue_paroles.paroles.originale.lignes[0].texte == "Au clair de la lune,",
             "paroles d'Au clair de la lune")
    verifier("7 demi-tons plus bas" in p.l_transposition.get_label(), p.l_transposition.get_label())
    p.r_libre.set_selected(1)
    yield 300
    verifier(p.chanson_libre == "frere-jacques" and p.vue_paroles.paroles.originale.lignes[0].texte == "Frère Jacques,",
             f"Frère Jacques : {p.chanson_libre}")
    p.preparer_publique("accompagnement")
    yield from attendre(lambda: p._piste is not None and p._piste[0] == "accompagnement", 90)
    verifier(p._piste and p._piste[1].name.startswith("frere-jacques-") and p._piste[1].is_file(), f"piste : {p._piste}")
    yield from attendre(lambda: p.lecteur.duree() is not None, 10)
    d = p.lecteur.duree()
    verifier(d is not None and abs(d - (5 + 64 * 60 / 88)) < 3.5, f"durée de Frère Jacques : {d}")
    p.lecteur.pause()
    # un de ses doublages : les réglages de l'étape 4
    p.r_libre.set_selected(2)
    yield 500
    verifier(p.chanson_libre == "doublage" and p.b_chanson.get_visible() and not p.b_publique.get_visible(),
             "chanson libre : un doublage")
    p.r_libre.set_selected(0)
    yield 300

    # import : l'étape de chaque fichier est reconnue à son nom
    src0 = Path(os.environ["VOIXLA_DONNEES"]).parent / "telephone"
    src0.mkdir(exist_ok=True)
    fs.son(src0 / "1-parole.m4a", 200, 3, canaux=1)
    fs.son(src0 / "5-frere-jacques.m4a", 300, 3, canaux=1)
    fs.son(src0 / "inconnu.m4a", 300, 3, canaux=1)
    p.proposer_import([src0 / "1-parole.m4a", src0 / "5-frere-jacques.m4a", src0 / "inconnu.m4a"])
    yield 300
    choix = [S_CHOIX[r.get_selected()][1:] for r in p._lignes_import]
    verifier(choix[:2] == [("parole", ""), ("libre", "frere-jacques")], f"étapes reconnues : {choix}")
    verifier(p.g_a_importer.get_visible() and p.b_valider.get_visible(), "liste d'import cachée")
    p._lignes_import[2].set_selected(2)           # corrigé à la main : « 3  Notes à tenir »
    p.valider_import()
    yield from attendre(lambda: len(p._rangees_controle) == 3, 60)
    verifier(len(p._rangees_controle) == 3, f"contrôles de l'import par noms : {len(p._rangees_controle)}")
    chant0 = sorted(x.name for x in (chemins.dossier_voix(f.donnees) / "voixA" / "chant").iterdir())
    verifier(any(n.endswith("-libre-frere-jacques.m4a") for n in chant0), f"chant/ : {chant0}")
    verifier(any(n.endswith("-notes.m4a") for n in chant0), f"chant/ : {chant0}")
    verifier(not p.g_a_importer.get_visible(), "liste d'import encore visible")

    # import : un fichier de synthèse et un qui n'est pas un son
    src = Path(os.environ["VOIXLA_DONNEES"]).parent / "telephone"
    src.mkdir(exist_ok=True)
    fs.son(src / "prise.m4a", 300, 4, canaux=1)
    (src / "faux.mp3").write_text("pas un son", encoding="utf-8")
    p.r_etape_import.set_selected(3)
    p.importer_fichiers([src / "prise.m4a", src / "faux.mp3"])
    yield from attendre(lambda: len(p._rangees_controle) == 2, 60)
    verifier(len(p._rangees_controle) == 2, f"contrôles : {len(p._rangees_controle)}")
    textes = etiquettes(p.g_controles)
    verifier(any("trop courte" in t for t in textes), f"alerte de durée : {textes}")
    verifier(any("pas un son lisible" in t for t in textes), f"fichier refusé : {textes}")
    chant = sorted(x.name for x in (chemins.dossier_voix(f.donnees) / "voixA" / "chant").iterdir())
    verifier(any(n.endswith("-chanson.m4a") and n[:4].isdigit() for n in chant), f"chant/ : {chant}")
    yield from attendre(lambda: "4 min" not in p.r_chant.get_subtitle() or "5 min" in p.r_chant.get_subtitle(), 10)
    p._quitter()


def scenario(app):
    f = app.fenetre
    yield from scenario_navigation(f)
    yield from scenario_seance(f)


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
    GLib.timeout_add_seconds(240, lambda: (dire(erreurs=["trop long"]), os._exit(3)))
    sys.exit(Essai().run(sys.argv[:1]))
