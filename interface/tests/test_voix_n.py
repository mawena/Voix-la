"""De deux à six voix, côté interface (sans écran) : syntaxe et arguments de doubler, préférences (nombre de voix,
voix gardées), noms affichés (« A, B et C »), lecture d'un doublage à quatre voix par le faux doubler (passages par voix,
passages corrigeables, affectation voulue avec « Toutes ensemble »), estimation. Voix inventées (jules, marie, luc, lea)."""

import json

from voixla import chemins, formats, moteur, rapports

from .commun import EssaiIsole, creer_voix

NOMS = {"jules": "Jules", "marie": "Marie", "luc": "Luc", "lea": "Léa"}


class Syntaxe(EssaiIsole):
    def test_specification_de_trois_a_six(self):
        self.assertEqual(moteur.lire_spec_voix("chanteur3:luc,chanteur1:jules,chanteur2:marie"),
                         ("ordre", [("chanteur1", "jules"), ("chanteur2", "marie"), ("chanteur3", "luc")]))
        self.assertEqual(moteur.lire_spec_voix("personne2:marie,personne1:jules")[0], "personnes")
        self.assertIsNone(moteur.lire_spec_voix("personne1:jules,chanteur2:marie"))
        self.assertEqual(moteur.spec_voix("ordre", ["jules", "marie", "luc", "lea"]),
                         "chanteur1:jules,chanteur2:marie,chanteur3:luc,chanteur4:lea")
        self.assertEqual(moteur.spec_voix("personnes", ["jules", "marie", "luc"]),
                         "personne1:jules,personne2:marie,personne3:luc")
        self.assertEqual(moteur.id_voix("chanteur2:marie,chanteur1:jules,chanteur3:luc"), "jules+marie+luc")
        self.assertEqual(moteur.membres_voix("jules+marie+luc"), ["jules", "marie", "luc"])
        rapport = {"voix_cibles": {"voix": [{"cle": f"personne{k + 1}", "nom": n} for k, n in enumerate(("jules", "marie", "luc"))]}}
        self.assertEqual(moteur.spec_de_rapport("jules+marie+luc", rapport), "personne1:jules,personne2:marie,personne3:luc")

    def test_capacites_et_arguments(self):
        caps = moteur.capacites()
        self.assertIn("voix_par_chanteur", caps)
        self.assertIn("voix_par_personne", caps)
        v = self.video()
        a = moteur.args_doublage([v], {"voix": "chanteur1:jules,chanteur2:marie,chanteur3:luc", "pas": 30}, self.donnees, caps=caps)
        self.assertIn("--voix=chanteur1:jules,chanteur2:marie,chanteur3:luc", a)

    def test_nom_affiche(self):
        self.assertEqual(formats.joindre(["A"]), "A")
        self.assertEqual(formats.joindre(["A", "B"]), "A et B")
        self.assertEqual(formats.joindre(["A", "B", "C"]), "A, B et C")
        self.assertEqual(rapports.nom_affiche_voix("jules+marie+luc", NOMS), "Jules, Marie et Luc")
        self.assertEqual(rapports.nom_affiche_voix("jules+marie", NOMS), "Jules et Marie")


class Preferences(EssaiIsole):
    def test_nombre_de_voix(self):
        self.assertEqual(chemins.nombre_voix({}), 2)
        self.assertEqual(chemins.nombre_voix({"nombre_voix": 4}), 4)
        self.assertEqual(chemins.nombre_voix({"nombre_voix": 9}), 6)
        self.assertEqual(chemins.nombre_voix({"nombre_voix": 1}), 2)
        self.assertEqual(chemins.nombre_voix({"nombre_voix": "x"}), 2)
        self.assertEqual(chemins.nombre_voix({"nombre_voix": True}), 2)

    def test_voix_gardees(self):
        p = {"voix_par_chanteur": True, "duo_chanteurs": "personnes", "nombre_voix": 4,
             "voix_chanteurs": ["jules", "marie", "luc", "disparue", "x", "y"]}
        par, duo, voix = chemins.choix_chanteurs(p)
        self.assertEqual((par, duo, voix), (True, "personnes", ["jules", "marie", "luc", "disparue"]))
        self.assertEqual(chemins.choix_chanteurs(p, ["jules", "marie", "luc"])[2], ["jules", "marie", "luc", None])
        # sans nombre : deux voix, comme avant
        self.assertEqual(chemins.choix_chanteurs({"voix_chanteurs": ["jules", "marie", "luc"]})[2], ["jules", "marie"])
        self.assertEqual(chemins.choix_chanteurs({"duo_chanteurs": "hasard"})[1], "genre")

    def test_gardees_sur_disque(self):
        p = chemins.modifier_preferences({"nombre_voix": 5, "duo_chanteurs": "personnes",
                                          "voix_chanteurs": ["jules", "marie", "luc", "lea", "jules", None]})
        self.assertEqual((p["nombre_voix"], p["duo_chanteurs"]), (5, "personnes"))
        self.assertEqual(chemins.lire_preferences()["nombre_voix"], 5)


class QuatreVoix(EssaiIsole):
    """Le faux doubler fait un doublage à quatre voix ; l'interface le lit."""

    def test_lecture(self):
        for k, (ident, nom) in enumerate((("jules", "Jules"), ("marie", "Marie"), ("luc", "Luc"), ("lea", "Léa"))):
            creer_voix(self.donnees, ident, nom, 110.0 + 20 * k)
        v = self.video("Chanson.mp4")
        spec = moteur.spec_voix("ordre", ["jules", "marie", "luc", "lea"])
        code, sortie = moteur.lancer(moteur.args_doublage([{"chemin": str(v), "extrait": (10, 20)}],
                                                          {"voix": spec, "pas": 30, "mode": "chant"}, self.donnees),
                                     moteur.environnement(self.donnees), timeout=60)
        self.assertEqual(code, 0, sortie)
        res = rapports.lister_resultats(chemins.dossier_sorties(self.donnees), NOMS)[0]
        self.assertEqual((res.voix, res.voix_nom), ("jules+marie+luc+lea", "Jules, Marie, Luc et Léa"))
        self.assertEqual(rapports.voix_cibles_noms(res.rapport), ["jules", "marie", "luc", "lea"])
        passages = rapports.passages_voix(res.rapport)
        self.assertEqual([p["voix"] for p in passages][:4], [["jules"], ["marie"], ["luc"], ["lea"]])
        self.assertEqual(passages[-1]["voix"], ["jules", "marie"])
        self.assertEqual(res.versions_duo["mode"], "utiles")
        lignes = res.resume_voix_cibles()
        self.assertTrue(any("voix entendues" in l for l in lignes))
        self.assertTrue(any("4 voix" in l for l in lignes))
        json.dumps(res.rapport)


class AffectationVoulue(EssaiIsole):
    def test_choix_pour_quatre_voix(self):
        noms = ["jules", "marie", "luc", "lea"]
        a = rapports.affectation_voulue(None, {3: "luc", 5: rapports.CHOIX_DEUX, 6: rapports.CHOIX_AUTO}, noms)
        self.assertEqual(a["voix_cibles"], noms)
        self.assertEqual(a["passages"], {"3": "luc", "5": "deux"})
