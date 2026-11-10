"""Transposition de la chanson côté interface (sans écran) : la ligne d'un résultat (« Chanson transposée de −3 demi-tons pour
ta voix »), le cas sans transposition, la parole, les doublages d'avant, l'avertissement si la musique n'a pas pu être
transposée ; et la page Doubler : l'interrupteur est dans la source, allumé par défaut, avec son info-bulle."""
import unittest
from pathlib import Path

from voixla import rapports

RACINE = Path(__file__).resolve().parents[1] / "voixla"


def resultat(rapport):
    return rapports.Resultat(Path("/x/Clip"), "jules", rapport=rapport)


def decision(dt, mode="chant", raison="essai"):
    return {"mode": mode, "musique_dt": dt, "applique": bool(dt), "raison": raison}


class LigneTransposition(unittest.TestCase):
    def test_chanson_transposee(self):
        phrase, detail = resultat({"transposition": decision(-3, raison="médiane 262 Hz → 220 Hz")}).resume_transposition()
        self.assertEqual(phrase, "Chanson transposée de −3 demi-tons pour ta voix")
        self.assertIn("médiane 262 Hz", detail)
        self.assertNotIn("ATTENTION", detail)

    def test_un_demi_ton_au_singulier_et_signe_plus(self):
        self.assertEqual(resultat({"transposition": decision(1)}).resume_transposition()[0],
                         "Chanson transposée de +1 demi-ton pour ta voix")
        self.assertEqual(resultat({"transposition": decision(5)}).resume_transposition()[0],
                         "Chanson transposée de +5 demi-tons pour ta voix")

    def test_sans_transposition(self):
        phrase, detail = resultat({"transposition": decision(0, raison="déjà dans la zone")}).resume_transposition()
        self.assertEqual(phrase, "Chanson non transposée")
        self.assertIn("déjà dans la zone", detail)

    def test_parole_et_doublages_d_avant(self):
        self.assertIsNone(resultat({"transposition": decision(0, mode="parole")}).resume_transposition())
        self.assertIsNone(resultat({"mode": "chant"}).resume_transposition())
        self.assertIsNone(resultat(None).resume_transposition())
        self.assertIsNone(resultat({"transposition": "illisible"}).resume_transposition())

    def test_musique_non_transposee_faute_de_bibliotheque(self):
        r = resultat({"transposition": decision(-3),
                      "mix": {"transposition": {"musique_dt": -3, "applique": False, "raison": "Rubber Band indisponible : absente"}}})
        phrase, detail = r.resume_transposition()
        self.assertEqual(phrase, "Chanson transposée de −3 demi-tons pour ta voix")
        self.assertIn("ATTENTION", detail)
        self.assertIn("fausse sur la musique d’origine", detail)
        ok = resultat({"transposition": decision(-3), "mix": {"transposition": {"musique_dt": -3, "applique": True}}})
        self.assertNotIn("ATTENTION", ok.resume_transposition()[1])


class PageDoubler(unittest.TestCase):
    """La page GTK ne se charge pas sans écran : on vérifie ce qu'elle déclare, dans sa source."""

    def source(self, nom):
        return (RACINE / "gui" / nom).read_text(encoding="utf-8")

    def test_interrupteur_allume_par_defaut_avec_info_bulle(self):
        s = self.source("page_doubler.py")
        self.assertIn('title="Transposer la chanson dans ma tessiture", active=True', s)
        self.assertIn("set_tooltip_text(INFOBULLE_TRANSPOSER)", s)
        self.assertIn('"--transposer" in self.fenetre.caps', s)
        self.assertIn('r["transposer"] = "auto" if self.r_transposer.get_active() else "non"', s)

    def test_ligne_du_detail_d_un_resultat(self):
        s = self.source("page_resultats.py")
        self.assertIn("r.resume_transposition()", s)
        self.assertIn('"Transposition"', s)


if __name__ == "__main__":
    unittest.main()
