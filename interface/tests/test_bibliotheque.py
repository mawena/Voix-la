"""Bibliothèque des résultats (voixla/bibliotheque.py, sans GTK) : versions et badges, regroupement des versions d'une
même chanson, filtres et recherche, tri, compte, dates courtes, pochettes générées, choix gardé dans interface.json ;
miniatures 16:9 et pochettes intégrées des MP3 (voixla/pistes.py) ; et la page elle-même sous Xvfb
(tests/essai_bibliotheque_gui.py). Titres et voix inventés (tests/fausse_bibliotheque.py)."""

import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from voixla import bibliotheque as bib
from voixla import chemins, pistes, rapports

from . import fausse_bibliotheque as fb
from .commun import EssaiIsole
from .test_pistes import FFMPEG, EssaiAvecCache

INTERFACE = Path(__file__).resolve().parents[1]
NOMS = {"jules": "Jules", "marie": "Marie", "nora": "Nora", "sacha": "Sacha"}


class EssaiDonnees(EssaiIsole):
    """Les 14 doublages des captures, médias vides (sans ffmpeg)."""

    def setUp(self):
        super().setUp()
        fb.creer(self.donnees, videos=False)
        self.resultats = rapports.lister_resultats(self.donnees / "sorties", NOMS)
        self.versions = [bib.version(r, NOMS) for r in self.resultats]

    def version(self, titre, voix):
        return next(v for v in self.versions if v.titre == titre and voix in v.voix_ids)


class EssaiVersions(EssaiDonnees):
    def test_champs(self):
        self.assertEqual(len(self.versions), 14)
        v = self.version("Lumière d’été", "marie")
        self.assertEqual((v.voix, v.mode, v.duree_s, v.langue, v.autotune, v.duo, v.extrait, v.complet),
                         ("Marie", "chant", 214.0, "es", True, False, None, True))
        self.assertEqual(v.image, v.res.video, "l'image vient de la vidéo doublée")
        self.assertEqual(v.image_sig[0], str(v.res.video))
        self.assertIn("lumiere d", v.recherche, "sans accents ni casse")
        self.assertIn("espagnol", v.recherche, "la langue des paroles se cherche aussi")
        duo = self.version("Minuit à Lagos", "jules")
        self.assertEqual((duo.voix, duo.voix_ids, duo.duo), ("Jules et Marie", ("jules", "marie"), True))
        mp3 = self.version("Danse des lucioles", "marie")
        self.assertIsNone(mp3.res.video)
        self.assertEqual(mp3.image, mp3.res.mp3, "sans vidéo : le MP3 (sa pochette)")
        inacheve = self.version("Étoile filante", "sacha")
        self.assertFalse(inacheve.complet)
        self.assertIsNone(inacheve.image)
        extrait = self.version("Les vagues bleues", "nora")
        self.assertEqual(extrait.extrait, (62.0, 40.0))
        self.assertEqual(extrait.titre, "Les vagues bleues", "le titre sans « (extrait …) »")

    def test_badges(self):
        cles = lambda v: [b.cle for b in bib.badges(v)]
        self.assertEqual(cles(self.version("Lumière d’été", "marie")), ["autotune", "langue"])
        self.assertEqual(cles(self.version("Minuit à Lagos", "jules")), ["duo", "langue"])
        self.assertEqual(cles(self.version("Les vagues bleues", "nora")), ["extrait", "langue"])
        self.assertEqual(cles(self.version("Récit du phare", "sacha")), ["langue", "parole"])
        self.assertEqual(cles(self.version("Étoile filante", "sacha")), ["inacheve"])
        langue = bib.badges(self.version("Lumière d’été", "jules"))[0]
        self.assertEqual((langue.texte, langue.infobulle), ("ES", "Paroles en espagnol"))

    def test_langue_des_paroles(self):
        r = self.version("Chanson sans image", "nora").res
        self.assertEqual(bib.langue_paroles(r), "", "ni rapport ni paroles.json")
        (r.dossier_voix / "paroles.json").write_text(json.dumps({"format": 1, "langue": "PT", "segments": []}),
                                                     encoding="utf-8")
        self.assertEqual(bib.langue_paroles(r), "pt", "repli sur paroles.json")
        self.assertEqual(bib.langue_paroles(r, lire_fichier=False), "")
        r.rapport = {"pistes_audio": {"langue": {"paroles": "it"}}}
        self.assertEqual(bib.langue_paroles(r), "it", "le rapport d'abord")
        (r.dossier_voix / "paroles.json").write_text("{abîmé", encoding="utf-8")
        r.rapport = {}
        self.assertEqual(bib.langue_paroles(r), "")

    def test_chanson(self):
        a, b = self.version("Lumière d’été", "jules"), self.version("Lumière d’été", "marie")
        self.assertEqual(a.chanson, b.chanson)
        self.assertTrue(a.chanson.startswith("source:"))
        c, d = self.version("Le jardin des heures", "nora"), self.version("Le jardin des heures", "sacha")
        self.assertNotEqual(c.res.dossier, d.res.dossier, "deux dossiers, extrait et entier")
        self.assertEqual(c.chanson, d.chanson, "même vidéo source : même chanson")
        r = self.version("Horizon de cuivre", "jules").res
        r.source = None
        self.assertTrue(bib.cle_chanson(r).startswith("titre:"), "sans source.json : le titre dans son dossier")


class EssaiRegroupement(EssaiDonnees):
    def test_regrouper(self):
        entrees = bib.regrouper(self.versions)
        self.assertEqual(len(entrees), 12)
        groupes = {e.titre: e for e in entrees if e.n > 1}
        self.assertEqual(sorted(groupes), ["Le jardin des heures", "Lumière d’été"])
        lumiere = groupes["Lumière d’été"]
        self.assertEqual([v.voix for v in lumiere.versions], ["Jules", "Marie"], "la plus récente d'abord")
        self.assertEqual(lumiere.principale.voix, "Jules")
        self.assertEqual(lumiere.voix, "Jules, Marie")
        self.assertEqual(lumiere.date, max(v.date for v in lumiere.versions))
        self.assertTrue(lumiere.cle.startswith("chanson:"))
        self.assertIn("autotune", [b.cle for b in lumiere.badges()], "au moins une version en a")
        seul = next(e for e in entrees if e.titre == "Ciel de craie")
        self.assertTrue(seul.cle.startswith("version:"), "une version seule garde la même clé, groupée ou non")
        deplie = bib.regrouper(self.versions, actif=False)
        self.assertEqual(len(deplie), 14)
        self.assertIn(seul.cle, {e.cle for e in deplie})

    def test_libelles_des_pastilles(self):
        e = next(e for e in bib.regrouper(self.versions) if e.titre == "Lumière d’été")
        self.assertEqual([l for _v, l in bib.libelles_versions(e)], ["Jules", "Marie"])
        # deux versions de la même voix : extrait ou entier, puis la date
        v1, v2 = self.version("Le jardin des heures", "nora"), self.version("Le jardin des heures", "sacha")
        v2.voix = "Nora"
        e = bib.Entree(cle="x", versions=[v1, v2])
        self.assertEqual([l for _v, l in bib.libelles_versions(e)], ["Nora · extrait 0:45", "Nora · entier"])
        e = bib.Entree(cle="y", versions=[v1, v1])
        libelles = [l for _v, l in bib.libelles_versions(e)]
        self.assertEqual(len(libelles), 2)
        self.assertTrue(all(l.startswith("Nora · extrait 0:45 · ") for l in libelles))

    def test_signature(self):
        e = next(e for e in bib.regrouper(self.versions) if e.titre == "Ciel de craie")
        avant = e.signature()
        v = e.principale.res.video
        os.utime(v, (time.time() + 60, time.time() + 60))     # un remix réécrit la vidéo
        frais = bib.version(rapports.lire_dossier(v.parent, NOMS)[0], NOMS)
        self.assertNotEqual(bib.Entree(cle=e.cle, versions=[frais]).signature(), avant)


class EssaiFiltres(EssaiDonnees):
    def setUp(self):
        super().setUp()
        self.entrees = bib.regrouper(self.versions)

    def titres(self, filtre):
        return sorted(e.titre for e in self.entrees if filtre.garde(e))

    def test_recherche(self):
        self.assertEqual(self.titres(bib.Filtre(texte="LUMIERE")), ["Lumière d’été"])
        self.assertEqual(self.titres(bib.Filtre(texte="ete lum")), ["Lumière d’été"], "tous les mots, dans le désordre")
        self.assertEqual(self.titres(bib.Filtre(texte="sacha")), ["Le jardin des heures", "Récit du phare",
                                                                 "Étoile filante"])
        self.assertEqual(self.titres(bib.Filtre(texte="espagnol")), ["Lumière d’été", "Rêve en sépia"])
        self.assertEqual(self.titres(bib.Filtre(texte="essais")), ["Le jardin des heures"], "le sous-dossier")
        self.assertEqual(self.titres(bib.Filtre(texte="introuvable")), [])
        self.assertEqual(len(self.titres(bib.Filtre(texte="  "))), 12)

    def test_filtres(self):
        f = bib.Filtre(voix="marie")
        self.assertEqual(self.titres(f), ["Brume sur la rivière", "Danse des lucioles", "Lumière d’été",
                                          "Minuit à Lagos", "Rêve en sépia"])
        lumiere = next(e for e in self.entrees if e.titre == "Lumière d’été")
        self.assertEqual(f.premiere(lumiere).voix, "Marie", "on ouvre la version qui passe le filtre")
        self.assertEqual(bib.Filtre().premiere(lumiere).voix, "Jules", "sans filtre : la plus récente")
        self.assertEqual(self.titres(bib.Filtre(genre="parole")), ["Récit du phare"])
        self.assertEqual(len(self.titres(bib.Filtre(genre="chant"))), 10, "l'inachevé n'a pas de mode")
        self.assertEqual(self.titres(bib.Filtre(longueur="extrait")), ["Le jardin des heures", "Les vagues bleues",
                                                                       "Rêve en sépia"])
        self.assertNotIn("Les vagues bleues", self.titres(bib.Filtre(longueur="entier")))
        self.assertIn("Le jardin des heures", self.titres(bib.Filtre(longueur="entier")), "sa version entière")
        self.assertEqual(self.titres(bib.Filtre(autotune=True)), ["Brume sur la rivière", "Lumière d’été"])
        self.assertEqual(self.titres(bib.Filtre(duo=True)), ["Minuit à Lagos"])
        self.assertEqual(self.titres(bib.Filtre(voix="marie", autotune=True, texte="brume")), ["Brume sur la rivière"])

    def test_etiquettes_et_effacer(self):
        f = bib.Filtre(texte="x", voix="marie", genre="chant", longueur="extrait", autotune=True, duo=True)
        self.assertTrue(f.actif)
        self.assertEqual(f.etiquettes(NOMS), [("voix", "Voix Marie"), ("genre", "Chant"), ("longueur", "Extraits"),
                                              ("autotune", "Avec auto-tune"), ("duo", "Une voix par chanteur")])
        self.assertTrue(f.effacer("genre"))
        self.assertEqual(f.genre, "")
        self.assertFalse(f.effacer("genre"), "rien n'a changé")
        self.assertTrue(f.effacer())
        self.assertFalse(f.actif)
        self.assertEqual(f.texte, "x", "la recherche reste")
        self.assertTrue(f.quelque_chose)

    def test_voix_presentes(self):
        self.assertEqual(bib.voix_presentes(self.versions, NOMS),
                         [("jules", "Jules"), ("marie", "Marie"), ("nora", "Nora"), ("sacha", "Sacha")])
        self.assertEqual(bib.voix_presentes(self.versions[:1], {}), [(self.versions[0].voix_ids[0],
                                                                      self.versions[0].voix)])


class EssaiTri(EssaiDonnees):
    def trier(self, tri, inverse=False, regrouper=True):
        import functools
        entrees = bib.regrouper(self.versions, regrouper)
        return [e.titre for e in sorted(entrees, key=functools.cmp_to_key(
            lambda a, b: bib.comparer(a, b, tri, inverse)))]

    def test_criteres(self):
        self.assertEqual(self.trier("date")[:3], ["Lumière d’été", "Les vagues bleues", "Minuit à Lagos"],
                         "le plus récent d'abord")
        self.assertEqual(self.trier("date", inverse=True)[0], "Rêve en sépia")
        self.assertEqual(self.trier("titre")[:3], ["Brume sur la rivière", "Chanson sans image", "Ciel de craie"])
        titres = self.trier("titre")
        self.assertLess(titres.index("Étoile filante"), titres.index("Horizon de cuivre"), "É se range avec E")
        duree = self.trier("duree")
        self.assertEqual(duree[0], "Rêve en sépia", "le plus court d'abord")
        self.assertEqual(duree[-1], "Étoile filante", "durée inconnue à la fin")
        duree = self.trier("duree", inverse=True)
        self.assertEqual((duree[0], duree[-1]), ("Minuit à Lagos", "Étoile filante"), "à la fin dans les deux sens")
        self.assertEqual(self.trier("mode", inverse=True)[-1], "Étoile filante")
        voix = self.trier("voix")
        self.assertEqual(voix[:2], ["Ciel de craie", "Horizon de cuivre"], "« Jules », puis par titre")
        self.assertLess(voix.index("Minuit à Lagos"), voix.index("Lumière d’été"),
                        "« Jules et Marie » avant « Jules, Marie »")
        mode = self.trier("mode")
        self.assertEqual(mode[-2:], ["Récit du phare", "Étoile filante"], "chant, parole, puis sans mode")

    def test_ordre_et_sens(self):
        a, b = bib.regrouper(self.versions)[:2]
        self.assertEqual(bib.ordre(a, b, "titre"), -bib.ordre(b, a, "titre"))
        self.assertEqual(bib.ordre(a, a, "date"), 0)
        self.assertTrue(bib.decroissant("date") and not bib.decroissant("titre"))
        self.assertTrue(bib.decroissant("titre", inverse=True))
        self.assertFalse(bib.inverse_de("date", True))
        self.assertTrue(bib.inverse_de("titre", True))

    def test_compte(self):
        entrees = bib.regrouper(self.versions)
        self.assertEqual(bib.compte(entrees, entrees, 14), "14 doublages · 12 chansons")
        self.assertEqual(bib.compte(entrees[:2], entrees, 14), f"{entrees[0].n + entrees[1].n} sur 14 doublages")
        f = bib.Filtre(voix="marie", autotune=True)
        gardees = [e for e in entrees if f.garde(e)]
        self.assertEqual(bib.compte(gardees, entrees, 14, f), "2 sur 14 doublages",
                         "la version de Jules, montrée par la carte, ne passe pas le filtre")
        self.assertEqual(bib.compte(entrees, entrees, 14, bib.Filtre()), "14 doublages · 12 chansons")
        deplie = bib.regrouper(self.versions, False)
        self.assertEqual(bib.compte(deplie, deplie, 14), "14 doublages")
        self.assertEqual(bib.compte([], [], 0), "Aucun doublage")

    def test_description_accessible(self):
        e = next(e for e in bib.regrouper(self.versions) if e.titre == "Lumière d’été")
        texte = bib.description_accessible(e)
        self.assertTrue(texte.startswith("Lumière d’été, 2 versions : voix Jules, Marie, 3 min 34 s"), texte)


class EssaiPetitesFonctions(unittest.TestCase):
    def test_date_courte(self):
        maintenant = time.mktime((2026, 11, 6, 15, 0, 0, 0, 0, -1))
        jour = lambda j, h=10: time.mktime((2026, 11, 6 - j, h, 0, 0, 0, 0, -1))
        self.assertEqual(bib.date_courte(jour(0, 1), maintenant), "aujourd’hui")
        self.assertEqual(bib.date_courte(jour(1, 23), maintenant), "hier")
        self.assertEqual(bib.date_courte(jour(3), maintenant), "il y a 3 jours")
        self.assertEqual(bib.date_courte(time.mktime((2026, 10, 17, 9, 0, 0, 0, 0, -1)), maintenant), "17 oct.")
        self.assertEqual(bib.date_courte(time.mktime((2025, 2, 1, 9, 0, 0, 0, 0, -1)), maintenant), "1 févr. 2025")
        self.assertEqual(bib.date_courte(0), "")

    def test_normaliser(self):
        self.assertEqual(bib.normaliser("Été À Noël"), "ete a noel")
        self.assertEqual(bib.normaliser(None), "")

    def test_couleurs_pochette(self):
        a = bib.couleurs_pochette("Chanson sans image")
        self.assertEqual(a, bib.couleurs_pochette("CHANSON SANS IMAGE"), "stable, sans casse")
        self.assertIn(a[2], (45, 90, 135, 160))
        for c in a[:2]:
            self.assertTrue(all(0 <= x <= 1 for x in c))
            # assez sombre pour une note blanche, en clair comme en sombre
            self.assertLess(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2], 0.6)
        vus = {bib.couleurs_pochette(f"Titre {i}")[:2] for i in range(60)}
        self.assertGreater(len(vus), 6, "des titres différents, des dégradés différents")

    def test_preferences(self):
        self.assertEqual(bib.preferences({}), bib.PREFERENCES)
        self.assertEqual(bib.preferences({"bibliotheque": {"vue": "liste", "tri": "duree", "inverse": True,
                                                           "regrouper": False}}),
                         {"vue": "liste", "tri": "duree", "inverse": True, "regrouper": False})
        self.assertEqual(bib.preferences({"bibliotheque": {"vue": "mosaïque", "tri": 3, "inverse": "oui"}}),
                         bib.PREFERENCES, "valeurs inconnues : défauts")
        self.assertEqual(bib.preferences({"bibliotheque": "abîmé"}), bib.PREFERENCES)
        self.assertEqual(bib.preferences(None), bib.PREFERENCES)


class EssaiPreferences(EssaiIsole):
    def test_la_cle_survit_aux_autres_preferences(self):
        """chemins ne garde que les clés qu'il connaît : « bibliotheque » y est déclarée avec son défaut, et survit à
        l'écriture de toute autre préférence."""
        self.assertEqual(chemins.PREFERENCES_DEFAUT["bibliotheque"], bib.PREFERENCES)
        self.assertFalse(hasattr(bib, "declarer_preferences"), "plus de déclaration au démarrage")
        self.assertEqual(chemins.lire_preferences()["bibliotheque"], bib.PREFERENCES, "défaut sans fichier")
        chemins.modifier_preference("bibliotheque", {"vue": "liste", "tri": "titre", "inverse": False,
                                                     "regrouper": True})
        chemins.modifier_preference("vis_onde", True)
        prefs = chemins.lire_preferences()
        self.assertEqual(bib.preferences(prefs)["vue"], "liste")
        self.assertTrue(prefs["vis_onde"])
        ecrit = json.loads(chemins.fichier_preferences().read_text(encoding="utf-8"))
        self.assertEqual(ecrit["bibliotheque"]["tri"], "titre")
        bib.PREFERENCES["vue"] = "liste"
        try:
            self.assertEqual(chemins.PREFERENCES_DEFAUT["bibliotheque"]["vue"], "grille", "une copie, pas le défaut")
        finally:
            bib.PREFERENCES["vue"] = "grille"


class EssaiDeuxCents(EssaiIsole):
    def test_rapide(self):
        """200 doublages (rapports de 300 morceaux, médias vides) : lecture du disque, versions, regroupement,
        filtre et tri en bien moins d'une seconde chacun."""
        fb.creer(self.donnees, n=200, videos=False)
        debut = time.perf_counter()
        resultats = rapports.lister_resultats(self.donnees / "sorties", NOMS)
        versions = [bib.version(r, NOMS) for r in resultats]
        lecture = time.perf_counter() - debut
        self.assertEqual(len(versions), 200)
        debut = time.perf_counter()
        entrees = bib.regrouper(versions)
        import functools
        f = bib.Filtre(texte="lune", autotune=False)
        gardees = [e for e in entrees if f.garde(e)]
        sorted(entrees, key=functools.cmp_to_key(lambda a, b: bib.comparer(a, b, "titre")))
        sorted(entrees, key=functools.cmp_to_key(lambda a, b: bib.comparer(a, b, "date")))
        calcul = time.perf_counter() - debut
        self.assertLess(len(entrees), 200, "des versions regroupées")
        self.assertLessEqual(len(gardees), len(entrees))
        self.assertLess(lecture, 3.0, f"lecture de 200 doublages : {lecture:.2f} s")
        self.assertLess(calcul, 0.5, f"regroupement, filtre et deux tris : {calcul:.3f} s")


@unittest.skipUnless(FFMPEG, "ffmpeg absent")
class EssaiMiniaturesLarges(EssaiAvecCache):
    def test_video_et_pochettes(self):
        v = fb.video_de_synthese(self.tmp / "Clip - voix Jules.mp4", fb.IMAGES[1], 2.0)
        avec = fb.mp3_de_synthese(self.tmp / "Avec - voix Jules.mp3", pochette=0)
        sans = fb.mp3_de_synthese(self.tmp / "Sans - voix Jules.mp3")
        self.assertNotEqual(pistes.chemin_miniature(v, "large"), pistes.chemin_miniature(v))
        self.assertIsNone(pistes.miniature_prete(v, "large"))

        def taille(f):
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                                str(f)], capture_output=True, text=True, timeout=30)
            return r.stdout.strip()
        f = pistes.preparer_miniature(v, "large", basse=True)
        self.assertEqual((f, taille(f)), (pistes.chemin_miniature(v, "large"), "480,270"))
        self.assertEqual(pistes.miniature_prete(v, "large"), f)
        self.assertEqual(f.parent, pistes.chemin_miniature(v).parent, "le même cache que la barre de lecture")
        self.assertEqual(taille(pistes.preparer_miniature(avec, "large")), "480,270", "pochette sur fond flou")
        self.assertEqual(taille(pistes.preparer_miniature(avec)), "512,512", "et en carré pour MPRIS")
        with self.assertRaises(pistes.ErreurPistes):
            pistes.preparer_miniature(sans, "large")
        with self.assertRaises(ValueError):
            pistes.chemin_miniature(v, "rond")
        self.assertEqual([p for p in f.parent.iterdir() if p.suffix != ".jpg"], [], "pas de fichier temporaire")
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(pistes.main(["miniature", str(v), "--large"]), 0)
            self.assertEqual(pistes.main(["miniature", str(v), "--rond"]), 2)
        self.assertEqual(json.loads(sortie.getvalue().splitlines()[0])["miniature"], str(f))

    def test_commandes(self):
        cmd = pistes.commande_miniature("/v/C.mp4", "/c/x.jpg", 10.0, "large", basse=True, )
        self.assertEqual(cmd[:3], ["nice", "-n", "19"])
        if shutil.which("ionice"):
            self.assertEqual(cmd[4], "-c3")
        self.assertIn("crop=480:270", cmd[cmd.index("-vf") + 1])
        cmd = pistes.commande_miniature("/v/C.mp3", "/c/x.jpg", 10.0, "large", pochette=1)
        self.assertNotIn("-ss", cmd, "une pochette n'a pas d'instant")
        graphe = cmd[cmd.index("-filter_complex") + 1]
        self.assertTrue(graphe.startswith("[0:v:1]split"))
        self.assertIn("boxblur", graphe)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", graphe)
        cmd = pistes.commande_miniature("/v/C.mp3", "/c/x.jpg", 10.0, "carre", pochette=0)
        self.assertEqual(cmd[cmd.index("-map") + 1], "0:v:0")
        self.assertEqual(pistes.image_du_fichier({"streams": [{"codec_type": "audio"},
                                                              {"codec_type": "video",
                                                               "disposition": {"attached_pic": 1}}]}),
                         ("pochette", 0))
        self.assertEqual(pistes.image_du_fichier({"streams": [{"codec_type": "video", "disposition": {}}]}),
                         ("video", None))
        self.assertEqual(pistes.image_du_fichier({"streams": [{"codec_type": "audio"}]}), (None, None))


GUI = None
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    GUI = True
except (ImportError, ValueError):
    GUI = False


@unittest.skipUnless(FFMPEG and GUI and shutil.which("xvfb-run") and shutil.which("dbus-run-session")
                     and shutil.which("flock"), "ffmpeg, GTK, xvfb-run, dbus-run-session ou flock absent")
class EssaiPage(EssaiAvecCache):
    """La page sous Xvfb, sur un bus privé, son coupé (tests/essai_bibliotheque_gui.py) : chargement, compte, vues,
    choix gardé, tri par colonne, filtres, regroupement, sélection, lecture sans détail (son seul, puis la vidéo au
    même instant à l'ouverture), « Voir le résultat », miniatures préparées en tâche de fond."""

    def test_page(self):
        fb.creer(self.donnees, videos=True)
        env = dict(os.environ)
        env.update({"GTK_A11Y": "none", "NO_AT_BRIDGE": "1", "GIO_USE_VFS": "local", "GSK_RENDERER": "cairo",
                    "ADW_DISABLE_PORTAL": "1", "VOIXLA_APP_ID": f"io.mawena.VoixLa.Essai{os.getpid()}",
                    "GST_PLUGIN_FEATURE_RANK": "fakeaudiosink:MAX", "VOIXLA_PAGE": "resultats",
                    "XDG_STATE_HOME": str(self.tmp / "xdg-etat"),
                    "PYTHONPATH": os.pathsep.join([str(INTERFACE)] + ([os.environ["PYTHONPATH"]]
                                                                        if os.environ.get("PYTHONPATH") else []))})
        verrou = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "voix-la-xvfb.verrou"
        cmd = ["flock", str(verrou), "dbus-run-session", f"--config-file={INTERFACE / 'outils' / 'bus-essai.conf'}",
               "--", "xvfb-run", "-a", "-s", "-screen 0 1400x1000x24", "nice", "-n", "10", sys.executable, "-m",
               "tests.essai_bibliotheque_gui"]
        # un groupe de processus à lui : à la fin, ce qui reste (ffmpeg d'une miniature, préparation lancée par le
        # détail) est arrêté, et n'écrit plus dans le dossier de l'essai
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                             cwd=str(INTERFACE), stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            sortie, erreur = p.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            sortie, erreur = p.communicate()
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGTERM)
        time.sleep(0.5)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGKILL)
        r = subprocess.CompletedProcess(cmd, p.returncode, sortie, erreur)
        mesures = {}
        for ligne in r.stdout.splitlines():
            with contextlib.suppress(ValueError):
                mesures.update(json.loads(ligne))
        trace = r.stdout[-3000:] + r.stderr[-3000:]
        self.assertEqual(r.returncode, 0, trace)
        self.assertEqual(mesures.get("erreurs"), [], trace)
        self.assertTrue(mesures.get("fini"), trace)


if __name__ == "__main__":
    unittest.main()
