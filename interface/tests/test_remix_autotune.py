"""Essais de l'auto-tune du remix rapide (page Résultats) : réglages présentés d'après rapport.json et la configuration
de doubler, commande construite cas par cas (--autotune N [--autotune-gamme G], --sans-autotune, rien), auto-tune
impossible (doubler trop ancien, x42-autotune absent), et les remix joués par le faux doubler : version créée,
gardée à jour, refaite, mise de côté ; la bascule Auto-tune (pistes « _at ») suit."""

import json
import os
import unittest

from voixla import moteur, pistes, rapports

from .commun import EssaiIsole, creer_voix, lire_trace

TOUTES = frozenset(moteur.OPTIONS_SUIVIES)


def rapport_at(applique=True, intensite=60, demandee="auto", **gamme):
    """Un rapport.json inventé, avec sa section « autotune »."""
    return {"mode": "chant", "autotune": {"applique": applique, "intensite": intensite,
                                          "gamme": {"demandee": demandee, **gamme}}}


class EssaiReglagesPresentes(unittest.TestCase):
    """L'interrupteur, l'intensité et la gamme du remix, d'après le doublage et la configuration."""

    def test_version_existante(self):
        self.assertEqual(moteur.autotune_du_resultat(rapport_at(intensite=55)), (True, 55, "auto"))

    def test_intensite_du_rapport_meme_sans_version(self):
        # x42-autotune absent au doublage : l'intensité demandée est restée dans le rapport
        r = rapport_at(applique=False, intensite=80)
        self.assertEqual(moteur.autotune_du_resultat(r, {"autotune": 30}), (False, 80, "auto"))

    def test_intensite_de_la_configuration(self):
        retiree = {"mode": "chant", "autotune": {"applique": False, "intensite": 0, "source": "option --sans-autotune"}}
        self.assertEqual(moteur.autotune_du_resultat(retiree, {"autotune": 40}), (False, 40, "auto"))
        self.assertEqual(moteur.autotune_du_resultat({"mode": "chant"}, {"contenu": {"autotune": 25}}),
                         (False, 25, "auto"))

    def test_intensite_par_defaut(self):
        for rapport, conf in ((None, None), ({}, {}), ({"mode": "chant"}, {"autotune": 0}),
                              (rapport_at(applique=False, intensite=0), {"autotune": "illisible"})):
            self.assertEqual(moteur.autotune_du_resultat(rapport, conf), (False, 70, "auto"), (rapport, conf))
        self.assertEqual(moteur.AUTOTUNE_DEFAUT, 70)

    def test_gamme_imposee_ou_auto(self):
        self.assertEqual(moteur.gamme_du_rapport(rapport_at(demandee="la-mineur", type="tonalite")), "la-mineur")
        self.assertEqual(moteur.gamme_du_rapport(rapport_at(demandee="chromatique", type="chromatique")), "chromatique")
        for demandee in ("auto", "Auto", "", None, "détectée"):
            self.assertEqual(moteur.gamme_du_rapport(rapport_at(demandee=demandee, type="tonalite", tonique=5,
                                                                mode="majeur")), "auto", demandee)
        # autre écriture de doubler (« Am », « Sib », « C#m ») : la tonique et le mode qu'il en a lus
        self.assertEqual(moteur.gamme_du_rapport(rapport_at(demandee="Am", type="tonalite", tonique=9,
                                                            mode="mineur")), "la-mineur")
        self.assertEqual(moteur.gamme_du_rapport(rapport_at(demandee="Sib", type="tonalite", tonique=10,
                                                            mode="majeur")), "sib-majeur")
        self.assertEqual(moteur.gamme_du_rapport(rapport_at(demandee="ré mineur", type="tonalite")), "re-mineur")
        self.assertEqual(moteur.gamme_du_rapport(rapport_at(demandee="xyz", type="tonalite")), "auto")
        self.assertEqual(moteur.gamme_du_rapport({"autotune": {"applique": True}}), "auto")
        self.assertEqual(moteur.gamme_du_rapport(None), "auto")
        # chaque valeur de la liste de la page Doubler revient telle quelle
        for valeur, _libelle in moteur.GAMMES[1:]:
            self.assertEqual(moteur.gamme_du_rapport(rapport_at(demandee=valeur)), valeur)

    def test_auto_tune_possible(self):
        self.assertEqual(moteur.autotune_remix_possible(TOUTES), (True, ""))
        self.assertEqual(moteur.autotune_remix_possible(TOUTES, {"x42_autotune": "/usr/lib/lv2"}), (True, ""))
        self.assertEqual(moteur.autotune_remix_possible(TOUTES, {}), (True, ""), "doubler qui ne dit rien du greffon")
        self.assertEqual(moteur.autotune_remix_possible(TOUTES, {"x42_autotune": None}), (False, "x42"))
        self.assertEqual(moteur.autotune_remix_possible(TOUTES - {"--autotune", "--sans-autotune",
                                                                  "--autotune-gamme"}), (False, "doubler"))


class EssaiCommandeRemix(EssaiIsole):
    """La ligne de commande du remix, cas par cas."""

    def setUp(self):
        super().setUp()
        self.dossier = self.donnees / "sorties" / "Clip (extrait 1m00,0+0m40,0)"

    def argv(self, autotune, caps=TOUTES):
        return moteur.args_remix("/v/Clip.mp4", "jules", self.dossier, self.donnees, voix_db=2, reverb_db=-15,
                                 caps=caps, autotune=autotune)

    def base(self, *autotune):
        return [*moteur.commande(), "/v/Clip.mp4", "--remix", "--voix=jules", f"--sortie={self.dossier.parent}",
                "--debut=60", "--duree=40", "--voix-db=2", "--reverb-db=-15", *autotune,
                "--donnees", str(self.donnees)]

    def test_allume_gamme_auto(self):
        r = moteur.reglages_remix_autotune(True, 70.0, "auto", version=False)
        self.assertEqual(r, {"autotune": 70})
        self.assertEqual(self.argv(r), self.base("--autotune=70"))

    def test_allume_gamme_choisie(self):
        r = moteur.reglages_remix_autotune(True, 45.4, "la-mineur", version=True)
        self.assertEqual(r, {"autotune": 45, "autotune_gamme": "la-mineur"})
        self.assertEqual(self.argv(r), self.base("--autotune=45", "--autotune-gamme=la-mineur"))
        # doubler sans --autotune-gamme : l'intensité seule
        self.assertEqual(self.argv(r, TOUTES - {"--autotune-gamme"}), self.base("--autotune=45"))

    def test_eteint_avec_version(self):
        r = moteur.reglages_remix_autotune(False, 70, "la-mineur", version=True)
        self.assertEqual(r, {"sans_autotune": True})
        self.assertEqual(self.argv(r), self.base("--sans-autotune"))

    def test_eteint_sans_version(self):
        # --sans-autotune quand même : sinon doubler en referait une si la clé « autotune » de sa configuration est allumée
        r = moteur.reglages_remix_autotune(False, 70, "auto", version=False)
        self.assertEqual(r, {"sans_autotune": True})
        self.assertEqual(self.argv(r), self.base("--sans-autotune"))
        self.assertEqual(self.argv(None), self.base(), "sans réglage d'auto-tune : comme avant l'auto-tune")

    def test_grise(self):
        for allume, version in ((True, False), (True, True), (False, True)):
            r = moteur.reglages_remix_autotune(allume, 70, "do-majeur", version, possible=False)
            self.assertEqual(r, {})
            self.assertEqual(self.argv(r), self.base())
        # doubler trop ancien : jamais d'option qu'il ne connaît pas
        caps = TOUTES - {"--autotune", "--sans-autotune", "--autotune-gamme"}
        self.assertNotIn("autotune", " ".join(self.argv({"autotune": 70, "autotune_gamme": "la-mineur"}, caps)))
        self.assertNotIn("autotune", " ".join(self.argv({"sans_autotune": True}, caps)))

    def test_intensite_bornee(self):
        self.assertEqual(moteur.reglages_remix_autotune(True, 0.2, "auto", False), {"autotune": 1})
        self.assertEqual(moteur.reglages_remix_autotune(True, 140, "auto", False), {"autotune": 100})
        self.assertEqual(moteur.reglages_remix_autotune(True, None, "auto", False), {"autotune": 70})


class EssaiRemixFauxDoubler(EssaiIsole):
    """Les remix joués par le faux doubler, comme la page Résultats les lance."""

    def setUp(self):
        super().setUp()
        creer_voix(self.donnees)
        self.v = self.video("Clip.mp4")
        self.trace = self.tmp / "trace.jsonl"
        os.environ["FAUX_DOUBLER_TRACE"] = str(self.trace)
        argv = moteur.args_doublage([str(self.v)], {"voix": "jules", "mode": "chant", "pas": 30}, self.donnees,
                                    extrait=(60, 40))
        code, sortie = moteur.lancer(argv, moteur.environnement(self.donnees))
        self.assertEqual(code, 0, sortie)
        self.dossier = self.donnees / "sorties" / "Clip (extrait 1m00,0+0m40,0)"
        self.assertEqual(moteur.remix_possible(self.v, self.dossier), (True, ""))

    def resultat(self):
        return rapports.lire_dossier(self.dossier, {"jules": "Jules"})[0]

    def remixer(self, allume=None, intensite=70, gamme="auto"):
        """Un remix tel que la page le lance (allume None : sans réglage d'auto-tune, comme avant)."""
        r = self.resultat()
        at = None if allume is None else moteur.reglages_remix_autotune(allume, intensite, gamme, bool(r.autotune))
        ok, sortie = moteur.remixer(self.v, "jules", self.dossier, self.donnees, voix_db=3, autotune=at)
        self.assertTrue(ok, sortie)
        return [l for l in lire_trace(self.trace) if "argv" in l][-1]["argv"], sortie

    def fichiers_at(self):
        return sorted(f.suffix for f in self.dossier.glob("* (auto-tune).*"))

    def test_creer_garder_refaire_retirer(self):
        r = self.resultat()
        self.assertIsNone(r.autotune)
        self.assertEqual(pistes.pistes_autotune(r, "mp3"), {}, "pas de bascule Auto-tune")
        self.assertEqual(moteur.autotune_du_resultat(r.rapport), (False, 70, "auto"))

        # interrupteur allumé, gamme choisie : la version auto-tune est créée
        argv, _ = self.remixer(True, 60, "la-mineur")
        self.assertIn("--autotune=60", argv)
        self.assertIn("--autotune-gamme=la-mineur", argv)
        self.assertNotIn("--sans-autotune", argv)
        r = self.resultat()
        self.assertEqual(r.autotune["intensite"], 60)
        self.assertEqual(self.fichiers_at(), [".lrc", ".mp3", ".mp4"])
        self.assertEqual(r.video_autotune.name, "Clip - voix Jules (auto-tune).mp4")
        self.assertIn("doublage_at", pistes.pistes_autotune(r, "mp3"), "la bascule Auto-tune apparaît")
        self.assertEqual(moteur.autotune_du_resultat(r.rapport), (True, 60, "la-mineur"), "réglages pré-remplis")

        # remix sans réglage d'auto-tune (doubler trop ancien, grisé) : la version est gardée à jour
        argv, _ = self.remixer(None)
        self.assertFalse([x for x in argv if "autotune" in x])
        r = self.resultat()
        self.assertEqual((r.autotune["intensite"], moteur.gamme_du_rapport(r.rapport)), (60, "la-mineur"))

        # allumé, autre intensité, gamme Auto : refaite en auto (pas de --autotune-gamme, comme doubler)
        argv, _ = self.remixer(True, 85, "auto")
        self.assertIn("--autotune=85", argv)
        self.assertFalse([x for x in argv if x.startswith("--autotune-gamme")])
        r = self.resultat()
        self.assertEqual(moteur.autotune_du_resultat(r.rapport), (True, 85, "auto"))

        # éteint alors que la version existe : --sans-autotune, elle est retirée
        argv, sortie = self.remixer(False)
        self.assertIn("--sans-autotune", argv)
        self.assertIn("version auto-tune retirée", sortie)
        r = self.resultat()
        self.assertIsNone(r.autotune)
        self.assertIsNone(r.video_autotune)
        self.assertEqual(self.fichiers_at(), [])
        self.assertEqual(pistes.pistes_autotune(r, "video"), {}, "la bascule Auto-tune disparaît")
        self.assertEqual(sorted(r.rapport["autotune"]["retires"]["effaces"]),
                         ["Clip - voix Jules (auto-tune).lrc", "Clip - voix Jules (auto-tune).mp3",
                          "Clip - voix Jules (auto-tune).mp4"])
        self.assertEqual(moteur.autotune_du_resultat(r.rapport), (False, 70, "auto"))

        # éteint et sans version : --sans-autotune quand même (la configuration ne la refait pas), rien ne revient
        argv, _ = self.remixer(False)
        self.assertEqual([x for x in argv if "autotune" in x], ["--sans-autotune"])
        self.assertIsNone(self.resultat().autotune)
        self.assertEqual(self.fichiers_at(), [])

    def test_gamme_chromatique(self):
        argv, _ = self.remixer(True, 30, "chromatique")
        self.assertIn("--autotune-gamme=chromatique", argv)
        r = self.resultat()
        self.assertEqual(r.rapport["autotune"]["gamme"]["type"], "chromatique")
        self.assertEqual(moteur.autotune_du_resultat(r.rapport), (True, 30, "chromatique"))

    def test_greffon_absent(self):
        os.environ["FAUX_DOUBLER_X42"] = "absent"
        code, s = moteur.lancer([*moteur.commande(), "--config", "--json"], moteur.environnement(self.donnees))
        self.assertEqual(code, 0, s)
        conf = json.loads(s[s.find("{"):])
        self.assertIsNone(conf["x42_autotune"])
        possible, cause = moteur.autotune_remix_possible(moteur.capacites(), conf)
        self.assertEqual((possible, cause), (False, "x42"))
        argv, _ = self.remixer(None)      # grisé : la commande d'avant l'auto-tune
        self.assertFalse([x for x in argv if "autotune" in x])
        self.assertEqual(moteur.reglages_remix_autotune(True, 70, "auto", False, possible=possible), {})

    def test_doubler_trop_ancien(self):
        self.ancien_doubler()
        caps = moteur.capacites()
        self.assertNotIn("--autotune", caps)
        self.assertEqual(moteur.autotune_remix_possible(caps), (False, "doubler"))
        a = moteur.args_remix(self.v, "jules", self.dossier, self.donnees, caps=caps, autotune={"autotune": 70})
        self.assertFalse([x for x in a if "autotune" in x])


if __name__ == "__main__":
    unittest.main()
