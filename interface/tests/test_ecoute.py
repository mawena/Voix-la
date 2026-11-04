"""Essais de voixla.ecoute : banc d'écoute à l'aveugle (extraits, lettres, correspondance, votes.json)."""

import json
import unittest
from pathlib import Path

from voixla import chemins, ecoute

from .commun import EssaiIsole

CORRESPONDANCE = {
    "refrain-chanteuse": {"A": "seedvc-50pas", "B": "yingmusic", "C": 3},
    "voix-homme": {"A": "yingmusic", "B": "seedvc-50pas"},
    "note": "lettres tirées au hasard", "graine": 1234, "gains_db": {"A": 0.0},
    "titres": {"refrain-chanteuse": "Refrain de « Chanson d'essai »"},
}


class EssaiBanc(EssaiIsole):
    def setUp(self):
        super().setUp()
        self.banc = chemins.dossier_ecoute()              # VOIXLA_ECOUTE : dossier temporaire de l'essai
        self.assertTrue(str(self.banc).startswith(str(self.tmp)))

    def fabriquer(self):
        for nom, fichiers in (("refrain-chanteuse", ("A.mp3", "B.mp3", "C.mp3", "A_voix.mp3", "B_voix.mp3", "original.mp3")),
                              ("voix-homme", ("A.mp3", "B.mp3", "a.mp3", "AB.mp3", "notes.txt")),
                              ("vide", ("original.mp3",)), (".cache", ("A.mp3",))):
            d = self.banc / nom
            d.mkdir(parents=True)
            for f in fichiers:
                (d / f).write_bytes(b"")
        (self.banc / "A.mp3").write_bytes(b"")              # à la racine : ignoré
        (self.banc / "correspondance.json").write_text(json.dumps(CORRESPONDANCE, ensure_ascii=False), encoding="utf-8")

    def test_banc_absent(self):
        self.assertEqual(ecoute.charger(), (None, {}))
        self.assertEqual(ecoute.charger(self.tmp / "nulle-part"), (None, {}))
        self.assertEqual(ecoute.lire_votes(), [])

    def test_banc_fabrique(self):
        self.fabriquer()
        extraits, corr = ecoute.charger()
        self.assertEqual(corr, CORRESPONDANCE)
        self.assertEqual([e.nom for e in extraits], ["refrain-chanteuse", "voix-homme"])   # sans « vide » ni « .cache »
        chanteuse, homme = extraits
        self.assertEqual([v.lettre for v in chanteuse.versions], ["A", "B", "C"])
        self.assertEqual([v.mix.name for v in chanteuse.versions], ["A.mp3", "B.mp3", "C.mp3"])
        self.assertEqual([v.voix.name if v.voix else None for v in chanteuse.versions], ["A_voix.mp3", "B_voix.mp3", None])
        self.assertEqual(chanteuse.original, self.banc / "refrain-chanteuse" / "original.mp3")
        self.assertEqual(chanteuse.titre, "Refrain de « Chanson d'essai »")
        self.assertEqual(chanteuse.dossier, self.banc / "refrain-chanteuse")
        self.assertEqual([v.lettre for v in homme.versions], ["A", "B"])       # ni « a », ni « AB »
        self.assertIsNone(homme.original)
        self.assertEqual(homme.titre, "Voix homme")
        self.assertEqual(ecoute.charger(self.banc)[0][0].nom, "refrain-chanteuse")

    def test_correspondance(self):
        self.assertEqual(ecoute.correspondance_de(CORRESPONDANCE, "refrain-chanteuse"),
                         {"A": "seedvc-50pas", "B": "yingmusic", "C": "3"})
        self.assertEqual(ecoute.correspondance_de(CORRESPONDANCE, "note"), {})
        self.assertEqual(ecoute.correspondance_de(CORRESPONDANCE, "absent"), {})
        self.assertEqual(ecoute.correspondance_de(None, "refrain-chanteuse"), {})

    def test_classement(self):
        self.assertEqual(ecoute.classement({"A": 3, "B": 5, "C": 1}), ["B", "A", "C"])
        self.assertEqual(ecoute.classement({"C": 4, "A": 4, "B": 2}), ["A", "C", "B"])    # égalité : ordre alphabétique
        self.assertEqual(ecoute.classement({"A": "2", "B": "4"}), ["B", "A"])
        self.assertEqual(ecoute.classement({}), [])

    def test_vote(self):
        self.fabriquer()
        _, corr = ecoute.charger()
        c = ecoute.correspondance_de(corr, "refrain-chanteuse")
        vote = ecoute.enregistrer_vote("refrain-chanteuse", {"A": 4, "B": 5, "C": 0}, "  B plus naturel  ", c)
        self.assertEqual(vote["notes"], {"A": 4, "B": 5})            # une note nulle : pas notée
        self.assertEqual(vote["classement"], ["B", "A"])
        self.assertEqual(vote["commentaire"], "B plus naturel")
        self.assertEqual(vote["correspondance"], c)
        self.assertRegex(vote["date"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}$")
        d = json.loads((self.banc / "votes.json").read_text(encoding="utf-8"))
        self.assertEqual(d["format"], 1)
        self.assertEqual(d["votes"], [vote])
        ecoute.enregistrer_vote("voix-homme", {"A": "3", "B": 2}, racine=self.banc)
        votes = ecoute.lire_votes()
        self.assertEqual([v["extrait"] for v in votes], ["refrain-chanteuse", "voix-homme"])
        self.assertEqual(votes[1]["notes"], {"A": 3, "B": 2})
        self.assertEqual(votes[1]["correspondance"], {})

    def test_vote_sans_note(self):
        with self.assertRaises(ValueError):
            ecoute.enregistrer_vote("refrain-chanteuse", {"A": 0, "B": None}, racine=self.banc)
        self.assertFalse((self.banc / "votes.json").exists())

    def test_votes_existants_gardes(self):
        self.banc.mkdir()
        (self.banc / "votes.json").write_text(json.dumps(
            {"format": 1, "auteur": "Jules", "votes": [{"extrait": "x", "notes": {"A": 1}}, "abîmé", 3]}),
            encoding="utf-8")
        self.assertEqual(len(ecoute.lire_votes()), 1)                    # entrées abîmées ignorées
        ecoute.enregistrer_vote("x", {"A": 5})
        d = json.loads((self.banc / "votes.json").read_text(encoding="utf-8"))
        self.assertEqual(d["auteur"], "Jules")
        self.assertEqual(len(d["votes"]), 2)
        (self.banc / "votes.json").write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(ecoute.lire_votes(), [])
        ecoute.enregistrer_vote("x", {"A": 5})                           # fichier illisible : recommencé
        self.assertEqual(len(ecoute.lire_votes()), 1)

    def test_correspondance_cachee_tant_que_pas_de_vote(self):
        self.fabriquer()
        _, corr = ecoute.charger()
        c = ecoute.correspondance_de(corr, "refrain-chanteuse")
        self.assertIsNone(ecoute.dernier_vote(ecoute.lire_votes(), "refrain-chanteuse", c))
        ecoute.enregistrer_vote("voix-homme", {"A": 2}, correspondance=ecoute.correspondance_de(corr, "voix-homme"))
        self.assertIsNone(ecoute.dernier_vote(ecoute.lire_votes(), "refrain-chanteuse", c))   # vote sur un autre extrait
        ecoute.enregistrer_vote("refrain-chanteuse", {"A": 2, "B": 4}, correspondance=c)
        v = ecoute.dernier_vote(ecoute.lire_votes(), "refrain-chanteuse", c)
        self.assertEqual(v["notes"], {"A": 2, "B": 4})
        ecoute.enregistrer_vote("refrain-chanteuse", {"A": 5}, correspondance=c)
        self.assertEqual(ecoute.dernier_vote(ecoute.lire_votes(), "refrain-chanteuse", c)["notes"], {"A": 5})
        self.assertEqual(ecoute.dernier_vote(ecoute.lire_votes(), "refrain-chanteuse")["notes"], {"A": 5})

    def test_vote_perime_si_la_correspondance_change(self):
        ancienne = {"A": "seedvc-50pas", "B": "yingmusic"}
        nouvelle = {"A": "yingmusic", "B": "seedvc-50pas"}               # banc refait : lettres retirées
        ecoute.enregistrer_vote("refrain-chanteuse", {"A": 5, "B": 1}, correspondance=ancienne, racine=self.banc)
        votes = ecoute.lire_votes()
        self.assertIsNotNone(ecoute.dernier_vote(votes, "refrain-chanteuse", ancienne))
        self.assertIsNone(ecoute.dernier_vote(votes, "refrain-chanteuse", nouvelle))
        ecoute.enregistrer_vote("refrain-chanteuse", {"A": 3, "B": 3}, correspondance=nouvelle, racine=self.banc)
        votes = ecoute.lire_votes()
        self.assertEqual(ecoute.dernier_vote(votes, "refrain-chanteuse", nouvelle)["notes"], {"A": 3, "B": 3})


class EssaiInterrupteurVoixSeule(unittest.TestCase):
    """ecoute.autre_forme : l'interrupteur « voix seule » passe du mix d'une version à sa voix seule, et retour."""

    def setUp(self):
        d = Path("/banc/refrain")
        self.versions = [ecoute.Version("A", d / "A.mp3", d / "A_voix.mp3"), ecoute.Version("B", d / "B.mp3")]

    def test_mix_vers_voix_et_retour(self):
        self.assertEqual(ecoute.autre_forme(("refrain", "A", "mix"), self.versions),
                         (("refrain", "A", "voix"), Path("/banc/refrain/A_voix.mp3")))
        self.assertEqual(ecoute.autre_forme(("refrain", "A", "voix"), self.versions),
                         (("refrain", "A", "mix"), Path("/banc/refrain/A.mp3")))

    def test_impossible(self):
        for cle in (("refrain", "B", "mix"),                 # pas de B_voix.mp3
                    ("refrain", "original", None),           # l'original
                    ("refrain", "Z", "mix"),                 # lettre inconnue
                    ("refrain", "A", "autre"), None, ("refrain", "A")):
            self.assertIsNone(ecoute.autre_forme(cle, self.versions), cle)
        self.assertIsNone(ecoute.autre_forme(("refrain", "A", "mix"), []))


if __name__ == "__main__":
    unittest.main()
