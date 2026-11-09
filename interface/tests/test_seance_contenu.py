"""Le contenu fourni par la séance (étapes 1, 2 et 5) : le texte à lire mot pour mot, la tessiture guide réglée sur la voix,
les chansons du domaine public (notes, paroles, syllabes, durées, transposition, accords), la case « Fait » gardée dans
seance.json et la reconnaissance des noms de fichier. Sans GTK ; la synthèse passe par numpy et ffmpeg."""

import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from voixla import controle, paroles as P, seance, seance_chansons as CH, seance_textes as TX

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
try:
    import numpy as np
except ImportError:      # pragma: no cover
    np = None

# Le texte demandé, recopié ici à la main : le test le compare à seance_textes.TEXTE_PAROLE, mot pour mot.
TEXTE_ATTENDU = """Le matin, quand la ville se réveille, j'aime ouvrir la fenêtre et écouter les bruits de la rue. Un bus passe, un chien aboie, quelqu'un rit sur le trottoir d'en face. Je prépare un café bien chaud, je coupe une tranche de pain et je regarde le ciel : va-t-il pleuvoir, ou fera-t-il beau ?

Hier, mon voisin m'a raconté une histoire incroyable. Il avait perdu ses clés au marché, entre les oranges et les poivrons. Il a cherché partout pendant une heure ! Finalement, c'est une petite fille de six ans qui les a retrouvées, cachées sous une caisse de tomates. Il était si content qu'il lui a offert un énorme bouquet de fleurs jaunes.

Est-ce que tu connais ce sentiment ? Quand on croit avoir tout perdu, et que tout s'arrange au dernier moment… C'est rare, mais c'est magique.

Ce week-end, je voudrais partir au bord de la mer. Marcher pieds nus sur le sable, sentir le vent salé, compter les vagues : une, deux, trois, quatre, cinq… Puis rentrer le soir, fatigué mais heureux, et chanter à tue-tête dans la voiture.

Voilà. Merci de m'avoir écouté ! À bientôt, et surtout, prends soin de toi."""

COUPLETS_ATTENDUS = (
    ("Au clair de la lune, mon ami Pierrot,", "Prête-moi ta plume pour écrire un mot.",
     "Ma chandelle est morte, je n'ai plus de feu.", "Ouvre-moi ta porte pour l'amour de Dieu."),
    ("Au clair de la lune, Pierrot répondit :", "« Je n'ai pas de plume, je suis dans mon lit.",
     "Va chez la voisine, je crois qu'elle y est,", "Car dans sa cuisine on bat le briquet. »"),
)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="voixla-seance-contenu-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()


class TestTextes(unittest.TestCase):
    def test_texte_a_lire_mot_pour_mot(self):
        self.assertEqual("\n\n".join(TX.TEXTE_PAROLE), TEXTE_ATTENDU)

    def test_consigne_et_sujets(self):
        self.assertEqual(TX.CONSIGNE_PAROLE, "Lis à voix haute, à ta vitesse normale, comme si tu racontais à un ami. "
                                             "Si tu te trompes, continue.")
        self.assertEqual(TX.TITRE_LIBRE, "Parle librement (1 min)")
        self.assertEqual(TX.SUJETS_LIBRES, ("Raconte la meilleure journée de ton année.",
                                            "Décris ton plat préféré et comment on le prépare.",
                                            "Explique à quelqu'un comment venir chez toi."))

    def test_comment_ca_marche_en_cinq_lignes(self):
        self.assertEqual(len(TX.COMMENT_CA_MARCHE), 5)
        total = " ".join(TX.COMMENT_CA_MARCHE)
        for mot in ("téléphone", "AVANT", "Une étape = un fichier", "importe", "20 minutes"):
            self.assertIn(mot, total)

    def test_tessiture_consigne(self):
        self.assertIn("Imite le son avec ta voix : monte doucement du plus grave au plus aigu, puis redescends. "
                      "Ne force jamais : arrête-toi avant que ça coince.", TX.CONSIGNES_TESSITURE)

    def test_pas_de_prenom(self):
        tout = " ".join(TX.COMMENT_CA_MARCHE + TX.CONSIGNES_TESSITURE + TX.CONSIGNES_CHANSON_PUBLIQUE)
        self.assertNotRegex(tout, r"\b(?:Charles|Anick)\b")


class TestChansons(unittest.TestCase):
    def test_notes_au_clair_de_la_lune(self):
        a = CH.lire_notes(CH.PHRASE_A)
        b = CH.lire_notes(CH.PHRASE_B)
        # do4 do4 do4 ré4 mi4 ré4 do4 mi4 ré4 ré4 do4
        self.assertEqual([m for m, _d in a], [60, 60, 60, 62, 64, 62, 60, 64, 62, 62, 60])
        self.assertEqual([d for _m, d in a], [1, 1, 1, 1, 2, 2, 1, 1, 1, 1, 4])
        # ré4 ré4 ré4 ré4 la3 la3 ré4 do4 si3 la3 sol3
        self.assertEqual([m for m, _d in b], [62, 62, 62, 62, 57, 57, 62, 60, 59, 57, 55])
        self.assertEqual([d for _m, d in b], [1, 1, 1, 1, 2, 2, 1, 1, 1, 1, 4])
        self.assertEqual(CH.ORDRE_COUPLET, (CH.PHRASE_A, CH.PHRASE_A, CH.PHRASE_B, CH.PHRASE_A))

    def test_notes_frere_jacques(self):
        lignes = CH.FRERE_JACQUES.lignes
        self.assertEqual(len(lignes), 8)           # chaque groupe joué deux fois
        self.assertEqual(lignes[0].notes, lignes[1].notes)
        self.assertEqual([m for m, _d in lignes[0].notes], [60, 62, 64, 60])
        self.assertEqual([m for m, _d in lignes[2].notes], [64, 65, 67])
        self.assertEqual([(m, d) for m, d in lignes[4].notes], [(67, .5), (69, .5), (67, .5), (65, .5), (64, 1), (60, 1)])
        self.assertEqual([(m, d) for m, d in lignes[6].notes], [(60, 1), (55, 1), (60, 2)])
        self.assertEqual(CH.FRERE_JACQUES.passes, 2)          # chantée deux fois

    def test_paroles_telles_quelles(self):
        textes = [l.texte.replace(" ", " ") for l in CH.AU_CLAIR_DE_LA_LUNE.lignes]
        self.assertEqual(textes, [t for couplet in COUPLETS_ATTENDUS for t in couplet])
        self.assertEqual([l.texte.replace(" ", " ") for l in CH.FRERE_JACQUES.lignes[::2]],
                         ["Frère Jacques,", "Dormez-vous ?", "Sonnez les matines,", "Ding, dang, dong."])

    def test_une_syllabe_par_note(self):
        for c in CH.CHANSONS:
            for l in c.lignes:
                self.assertEqual(len(l.syllabes), len(l.notes), l.texte)
                # compte à la française, indépendant du découpage écrit à la main
                self.assertEqual(CH.compter_syllabes(l.texte), len(l.notes), l.texte)
                self.assertTrue(all(s.strip() for s in l.syllabes), l.texte)

    def test_compter_syllabes(self):
        for texte, n in (("Au clair de la lune, mon ami Pierrot,", 11), ("pour écrire un mot", 5),
                         ("Ma chandelle est morte, je n'ai plus de feu.", 11), ("Dieu", 1),
                         ("Va chez la voisine, je crois qu'elle y est,", 11), ("Sonnez les matines", 6)):
            self.assertEqual(CH.compter_syllabes(texte), n, texte)

    def test_durees(self):
        for c in (CH.AU_CLAIR_DE_LA_LUNE,):
            for l in c.lignes:
                self.assertEqual(l.temps, 16)                       # une phrase = 4 mesures de 4 temps
        self.assertEqual(CH.AU_CLAIR_DE_LA_LUNE.temps, 128)          # deux couplets de 4 phrases
        self.assertEqual([l.temps for l in CH.FRERE_JACQUES.lignes], [4] * 8)
        self.assertEqual(CH.FRERE_JACQUES.temps, 64)                 # 8 lignes, deux fois
        self.assertAlmostEqual(CH.AU_CLAIR_DE_LA_LUNE.duree_s(), 128 * 60 / 88)
        self.assertAlmostEqual(CH.FRERE_JACQUES.duree_s(), 64 * 60 / 88)
        # le calendrier se termine exactement à la fin de la chanson
        for c in CH.CHANSONS:
            fin = CH.calendrier(c)[-1][1][-1][1]
            self.assertAlmostEqual(fin, c.duree_s())

    def test_transposition_sur_le_centre(self):
        for c in CH.CHANSONS:
            for centre in (150.0, 190.0, 200.0, 280.0, 380.0):
                d = CH.transposition(c, centre)
                self.assertIsInstance(d, int)
                self.assertLessEqual(abs(CH.midi_de_hz(centre) - (CH.note_mediane(c) + d)), 0.5, (c.cle, centre))
        self.assertEqual(CH.note_mediane(CH.AU_CLAIR_DE_LA_LUNE), 62)
        self.assertEqual(CH.transposition(CH.AU_CLAIR_DE_LA_LUNE, CH.hauteur_hz(62)), 0)

    def test_accords_en_blanches(self):
        for c in CH.CHANSONS:
            accords = CH.choisir_accords(c)
            self.assertEqual(len(accords), int(c.temps / CH.BATTEMENTS_PAR_ACCORD))
            self.assertTrue(all(nom in ("I", "IV", "V") for _d, _f, nom in accords))
            self.assertEqual(accords[-1][2], "I")
            self.assertEqual(accords[0][2], "I")
        # « ré ré » : accord de V ; « la la » : accord de IV
        noms = [n for _d, _f, n in CH.choisir_accords(CH.AU_CLAIR_DE_LA_LUNE)]
        self.assertEqual(noms[16:20], ["V", "V", "IV", "IV"])
        for nom, classes in (("I", {0, 4, 7}), ("IV", {5, 9, 0}), ("V", {7, 11, 2})):
            notes = CH.notes_accord(nom)
            self.assertEqual({m % 12 for m in notes[1:]}, classes)         # la triade ; la basse est sa fondamentale
            self.assertIn(notes[0] % 12, classes)
            self.assertEqual([m + 3 for m in notes], CH.notes_accord(nom, 3))

    def test_paroles_synchronisees(self):
        for c in CH.CHANSONS:
            p = CH.paroles(c, 5.0)
            lignes = p.originale.lignes
            self.assertAlmostEqual(lignes[0].debut, 5.0)
            self.assertAlmostEqual(lignes[-1].fin, 5.0 + c.duree_s())
            for a, b in zip(lignes, lignes[1:]):
                self.assertLessEqual(a.fin, b.debut + 1e-9)
            for l in lignes:
                self.assertLessEqual(len(l.mots), CH.SYLLABES_PAR_LIGNE_MAX)
                _texte, mots = P.portees(l)
                self.assertEqual(len(mots), len(l.mots), l.texte)      # chaque syllabe est repérée dans le texte
        total = sum(len(l.mots) for l in CH.paroles(CH.AU_CLAIR_DE_LA_LUNE).originale.lignes)
        self.assertEqual(total, 8 * 11)

    def test_la_syllabe_en_cours_se_remplit(self):
        p = CH.paroles(CH.AU_CLAIR_DE_LA_LUNE, 5.0)
        ligne = p.originale.lignes[0]
        m = ligne.mots[0]
        self.assertEqual(P.fraction_mot(m, 5.0 - 0.1), 0.0)
        self.assertAlmostEqual(P.fraction_mot(m, (m.debut + m.fin) / 2), 0.5)
        self.assertEqual(P.fraction_mot(m, m.fin + 0.01), 1.0)


@unittest.skipUnless(np, "numpy absent")
class TestSynthese(unittest.TestCase):
    def test_piano_et_rendu(self):
        son = CH.piano(220.0, 1.0)
        self.assertGreater(len(son), CH.RATE)
        self.assertLess(float(np.max(np.abs(son))), 0.5)
        self.assertAlmostEqual(float(son[-1]), 0.0, places=2)
        sortie = CH.rendre(CH.FRERE_JACQUES, 200.0, True)
        self.assertGreater(len(sortie), CH.FRERE_JACQUES.duree_s() * CH.RATE)
        seul = CH.rendre(CH.FRERE_JACQUES, 200.0, False)
        self.assertEqual(len(seul), len(sortie))
        self.assertFalse(np.allclose(sortie, seul))            # la mélodie en plus, le métronome en moins

    @staticmethod
    def _pic_hz(signal, debut_s, longueur_s=0.25, rate=44100):
        morceau = signal[int(debut_s * rate):int((debut_s + longueur_s) * rate)]
        spectre = np.abs(np.fft.rfft(morceau * np.hanning(len(morceau))))
        return float(np.argmax(spectre)) * rate / len(morceau)

    def test_sirene(self):
        centre = 250.0
        s = seance.sirene(centre)
        self.assertEqual(len(s), int(8 * 44100))
        bas, haut = centre * 2 ** (-9 / 12), centre * 2 ** (9 / 12)
        # au début, grave ; au milieu (4 s), aiguë ; à la fin, grave à nouveau
        f0 = self._pic_hz(s, 0.3, 0.2)
        f4 = self._pic_hz(s, 3.9, 0.2)
        f8 = self._pic_hz(s, 7.5, 0.2)
        self.assertLess(abs(f0 / bas - 1), 0.12)
        self.assertLess(abs(f4 / haut - 1), 0.05)
        self.assertLess(abs(f8 / bas - 1), 0.12)
        self.assertLess(float(np.max(np.abs(s))), 0.5)
        self.assertAlmostEqual(float(s[0]), 0.0, places=3)      # enveloppe douce : rien ne claque

    def test_signal_tessiture_et_bips(self):
        centre = 200.0
        s = seance.signal_tessiture(centre)
        taux = 44100
        energie = lambda t: float(np.max(np.abs(s[int(t * taux):int((t + 0.1) * taux)])))
        for t in seance.BIPS_S:
            self.assertGreater(energie(t + 0.02), 0.1, t)
        self.assertEqual(energie(0.5), 0.0)
        self.assertEqual(energie(4.6), 0.0)
        self.assertAlmostEqual(self._pic_hz(s, 1.0, 0.1), 880.0, delta=20.0)
        self.assertGreater(energie(6.0), 0.1)                    # la première sirène démarre à 5 s


class TestTessiture(Base):
    def voix(self, **fichiers):
        d = self.tmp / "voix"
        d.mkdir(exist_ok=True)
        for nom, donnees in fichiers.items():
            (d / nom).write_text(json.dumps(donnees), encoding="utf-8")
        return d

    def test_centre_depuis_la_tessiture(self):
        d = self.voix(**{"tessiture.json": {"p50": 210.0, "confort": [150.0, 300.0]},
                         "voix.json": {"hauteur_parole_mediane_hz": 100.0}})
        c, source = seance.centre_voix(d)
        self.assertAlmostEqual(c, math.sqrt(150.0 * 300.0))
        self.assertIn("tessiture", source)

    def test_centre_p50_sans_confort(self):
        d = self.voix(**{"tessiture.json": {"p50": 210.0}, "voix.json": {"hauteur_parole_mediane_hz": 100.0}})
        self.assertEqual(seance.centre_voix(d)[0], 210.0)

    def test_centre_double_de_la_parole(self):
        d = self.voix(**{"voix.json": {"hauteur_parole_mediane_hz": 105.0}})
        self.assertEqual(seance.centre_voix(d)[0], 210.0)

    def test_centre_sans_donnees_grave_ou_aigue(self):
        d = self.voix()
        self.assertEqual(seance.centre_voix(d), (None, ""))
        self.assertEqual(seance.centre_voix(d, "grave")[0], 190.0)
        self.assertEqual(seance.centre_voix(d, "aigue")[0], 380.0)
        self.assertIsNone(seance.centre_voix(d, "autre")[0])

    def test_tonique_de_la_gamme(self):
        for centre in (190.0, 200.0, 380.0, 255.0):
            t = seance.tonique_gamme(centre)
            self.assertIsInstance(t, int)
            self.assertLessEqual(abs(CH.midi_de_hz(centre * 2 ** (-5 / 12)) - t), 0.5)
        notes = seance.notes_gamme(200.0)
        self.assertEqual([n for n, _f in notes], ["do", "ré", "mi", "fa", "sol", "la", "si", "do",
                                                   "si", "la", "sol", "fa", "mi", "ré", "do"])
        t = seance.tonique_gamme(200.0)
        self.assertAlmostEqual(notes[0][1], CH.hauteur_hz(t))
        self.assertAlmostEqual(notes[7][1], CH.hauteur_hz(t + 12))     # le do du haut : une octave plus haut
        self.assertAlmostEqual(notes[2][1], CH.hauteur_hz(t + 4))

    def test_calendrier(self):
        cal = seance.calendrier_tessiture(200.0)
        sirenes = [c for c in cal if c[3] == "sirene"]
        self.assertEqual(len(sirenes), 6)
        self.assertEqual([("aaa" in c[2]) for c in sirenes], [True] * 3 + [False] * 3)
        self.assertEqual(sirenes[0][0], 5.0)                                  # après les 4 bips
        for a, b in zip(sirenes, sirenes[1:]):
            self.assertAlmostEqual(b[0] - a[0], 8.0 + 3.0)                   # 8 s de sirène, 3 s de silence
        self.assertTrue(all(abs((c[1] - c[0]) - 8.0) < 1e-9 for c in sirenes))
        notes = [c for c in cal if c[3] == "note"]
        self.assertEqual(len(notes), 30)                                       # deux passages de 15 notes
        self.assertTrue(all(abs((c[1] - c[0]) - 1.2) < 1e-9 for c in notes))
        self.assertEqual([c[2] for c in notes[:8]], ["Do", "Ré", "Mi", "Fa", "Sol", "La", "Si", "Do"])
        self.assertEqual([("la" in c[2]) for c in cal if c[3] == "intro"], [True, False])
        for a, b in zip(cal, cal[1:]):
            self.assertAlmostEqual(a[1], b[0])                                 # sans trou ni recouvrement
        self.assertAlmostEqual(cal[-1][1], seance.TESSITURE_S - 2.0)

    def test_paroles_de_la_tessiture(self):
        p = seance.paroles_tessiture(200.0)
        self.assertEqual(len(p.originale.lignes), len(seance.calendrier_tessiture(200.0)))
        self.assertTrue(any(l.texte == "Sol" for l in p.originale.lignes))

    @unittest.skipUnless(FFMPEG, "ffmpeg absent")
    def test_pistes_mp3(self):
        cache = self.tmp / "cache"
        t = seance.preparer_tessiture(200.0, cache=cache)
        self.assertAlmostEqual(controle.duree(t), seance.TESSITURE_S, delta=3.0)
        self.assertEqual(seance.preparer_tessiture(200.0, cache=cache), t)       # déjà prête : pas refaite
        for genre in ("melodie", "accompagnement"):
            p = seance.preparer_chanson_publique("frere-jacques", genre, 200.0, cache=cache)
            self.assertAlmostEqual(controle.duree(p), 5.0 + CH.FRERE_JACQUES.duree_s(), delta=3.5)
        self.assertNotEqual(seance.chemin_chanson("frere-jacques", "melodie", 200.0),
                            seance.chemin_chanson("frere-jacques", "accompagnement", 200.0))
        self.assertNotEqual(seance.chemin_chanson("frere-jacques", "melodie", 200.0),
                            seance.chemin_chanson("frere-jacques", "melodie", 380.0))
        with self.assertRaises(seance.ErreurSeance):
            seance.chemin_chanson("inconnue", "melodie", 200.0)


class TestEtat(Base):
    def test_fait_garde_dans_seance_json(self):
        d = self.tmp / "voix" / "voixA"
        self.assertEqual(seance.lire_etat(d)["fait"], {e.cle: False for e in seance.ETAPES})
        seance.marquer_fait(d, "parole")
        seance.marquer_fait(d, "libre")
        seance.marquer_fait(d, "libre", False)
        brut = json.loads((d / "seance.json").read_text(encoding="utf-8"))
        self.assertTrue(brut["fait"]["parole"])
        self.assertFalse(brut["fait"]["libre"])
        self.assertTrue(seance.lire_etat(d)["fait"]["parole"])

    def test_taille_du_texte_et_centre(self):
        d = self.tmp / "voix" / "voixA"
        e = seance.lire_etat(d)
        self.assertEqual(e["taille_texte"], 1.0)
        self.assertIsNone(e["centre"])
        e["taille_texte"], e["centre"] = 1.5, "aigue"
        seance.ecrire_etat(d, e)
        e = seance.lire_etat(d)
        self.assertEqual((e["taille_texte"], e["centre"]), (1.5, "aigue"))

    def test_fichier_abime_ignore(self):
        d = self.tmp / "voix" / "voixA"
        d.mkdir(parents=True)
        (d / "seance.json").write_text('{"fait": {"parole": "oui", "notes": true}, "taille_texte": "grand", '
                                       '"centre": "x"}', encoding="utf-8")
        e = seance.lire_etat(d)
        self.assertEqual(e["fait"]["parole"], False)
        self.assertEqual(e["fait"]["notes"], True)
        self.assertEqual((e["taille_texte"], e["centre"]), (1.0, None))
        (d / "seance.json").write_text("pas du json", encoding="utf-8")
        self.assertFalse(any(seance.lire_etat(d)["fait"].values()))


class TestReconnaissance(Base):
    def test_noms_conseilles(self):
        for nom, attendu in (("1-parole.m4a", ("parole", "")), ("2-tessiture.mp3", ("tessiture", "")),
                             ("3-notes.wav", ("notes", "")), ("4-chanson.aac", ("chanson", "")),
                             ("5-au-clair-de-la-lune.m4a", ("libre", "au-clair-de-la-lune")),
                             ("5-frere-jacques.m4a", ("libre", "frere-jacques"))):
            self.assertEqual(seance.reconnaitre(nom), attendu, nom)
        for e in seance.ETAPES:
            for n in e.noms:
                self.assertEqual(seance.reconnaitre(n + ".m4a")[0], e.cle)

    def test_variantes(self):
        self.assertEqual(seance.reconnaitre("Frère Jacques.mp3"), ("libre", "frere-jacques"))
        self.assertEqual(seance.reconnaitre("Tessiture 2.m4a")[0], "tessiture")
        self.assertEqual(seance.reconnaitre("3.m4a")[0], "notes")
        self.assertEqual(seance.reconnaitre("5-chanson-libre.m4a")[0], "libre")
        self.assertEqual(seance.reconnaitre("20261109-153012-notes.m4a")[0], "notes")
        self.assertEqual(seance.reconnaitre("AUDIO-2026-11-09.m4a"), (None, ""))
        self.assertEqual(seance.reconnaitre("xyz.m4a"), (None, ""))

    @unittest.skipUnless(FFMPEG, "ffmpeg absent")
    def test_import_garde_la_chanson(self):
        source = self.tmp / "5-frere-jacques.wav"
        import subprocess
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=1", str(source)],
                       check=True, capture_output=True, timeout=60)
        voix = self.tmp / "voix" / "voixA"
        cle, detail = seance.reconnaitre(source.name)
        ((_s, dest, erreur),) = seance.importer([source], voix, cle, 1_800_000_000, detail)
        self.assertEqual(erreur, "")
        self.assertTrue(dest.name.endswith("-libre-frere-jacques.wav"), dest.name)
        self.assertEqual(dest.parent.name, "chant")


if __name__ == "__main__":
    unittest.main()
