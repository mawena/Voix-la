"""Essais de voixla.seance : étapes, préparation des pistes (ffmpeg, sur des sons de synthèse de quelques secondes),
classement des chansons conseillées, paroles décalées, import des enregistrements et contrôles (saturation, bruit,
musique du casque entendue). Voix et chansons inventées ; tout se passe dans un dossier temporaire."""

import json
import math
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from voixla import controle, seance

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def synthese(sortie, graphe, duree, canaux=2):
    """Un son de synthèse : filtre lavfi (source) → fichier (format du nom)."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", graphe, "-t", str(duree), "-ac", str(canaux),
                    str(sortie)], check=True, capture_output=True, timeout=120)


def niveau(fichier, debut, duree):
    """Niveau moyen (dBFS) d'un morceau d'un fichier."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(debut), "-t", str(duree), "-i", str(fichier), "-af",
                        "volumedetect", "-f", "null", "-"], capture_output=True, text=True, timeout=60)
    m = re.search(r"mean_volume: (-?[0-9.]+|-inf)", r.stderr)
    return -120.0 if not m or m.group(1) == "-inf" else float(m.group(1))


def pic(fichier):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(fichier), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=60)
    return float(re.search(r"max_volume: (-?[0-9.]+)", r.stderr).group(1))


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="voixla-seance-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()


class TestEtapes(unittest.TestCase):
    def test_cinq_etapes_dans_l_ordre(self):
        self.assertEqual([e.cle for e in seance.ETAPES], ["parole", "tessiture", "notes", "chanson", "libre"])
        self.assertEqual(seance.etape("parole").duree_s, 120)
        self.assertIsNone(seance.etape("parole").piste)
        self.assertIsNone(seance.etape("tessiture").piste)
        self.assertEqual(seance.etape("notes").piste, "notes")
        self.assertEqual(seance.etape("chanson").piste, "chanson")
        self.assertEqual(seance.etape("parole").dossier, "parole")
        self.assertEqual(seance.etape("libre").dossier, "chant")
        # 12 notes de 5 s suivies de 3 s de silence
        self.assertEqual(seance.etape("notes").duree_s, 96)
        for e in seance.ETAPES:
            self.assertTrue(e.consignes and all(isinstance(c, str) for c in e.consignes))

    def test_tonalites(self):
        self.assertEqual(seance.TONALITES, (0, -1, -2, -3, -4, -5, -6, -7))
        self.assertEqual(seance.nom_tonalite(0), "tonalité d’origine")
        self.assertEqual(seance.nom_tonalite(-1), "1 demi-ton plus bas")
        self.assertEqual(seance.nom_tonalite(-5), "5 demi-tons plus bas")


@unittest.skipUnless(FFMPEG, "ffmpeg ou ffprobe absent")
class TestPistes(Base):
    def travail(self):
        t = self.tmp / "sorties" / "Chanson d'exemple" / "travail"
        t.mkdir(parents=True)
        synthese(t / "instrumental.wav", "sine=frequency=220:sample_rate=44100", 6)
        synthese(t / "voix_source.wav", "sine=frequency=440:sample_rate=44100", 6, canaux=1)
        return t

    def test_notes(self):
        notes = seance.preparer_notes(cache=self.tmp / "cache")
        self.assertTrue(notes.is_file())
        self.assertAlmostEqual(controle.duree(notes), 96, delta=0.5)
        self.assertLess(niveau(notes, 0.5, 4), -6)            # une note tenue
        self.assertLess(niveau(notes, 5.3, 2.5), -60)         # le silence qui suit
        self.assertLessEqual(pic(notes), -0.9)                # limiteur à 0,89
        avant = notes.stat().st_mtime_ns
        self.assertEqual(seance.preparer_notes(cache=self.tmp / "cache"), notes)
        self.assertEqual(notes.stat().st_mtime_ns, avant)    # pas refait

    def test_guide_et_musique_avec_bips(self):
        t = self.travail()
        cache = self.tmp / "cache"
        suivi = []
        pistes = seance.preparer_pistes(t, 0, lambda f, texte: suivi.append((f, texte)), cache=cache)
        self.assertEqual(set(pistes), {"guide", "musique"})
        self.assertTrue(str(pistes["musique"]).startswith(str(cache / seance.empreinte(t))))
        for p in pistes.values():
            self.assertTrue(p.is_file())
            self.assertEqual(p.suffix, ".mp3")
            self.assertAlmostEqual(controle.duree(p), 11, delta=0.4)    # 5 s de bips + 6 s de chanson
            self.assertLessEqual(pic(p), -0.9)
            for b in seance.BIPS_S:                                     # un bip à 880 Hz à chaque seconde
                self.assertGreater(niveau(p, b - 0.02, 0.2), -35, f"pas de bip à {b} s")
            self.assertLess(niveau(p, 4.5, 0.4), -60)                   # silence avant la chanson
            self.assertGreater(niveau(p, 7, 2), -35)                    # la chanson démarre à 5 s
        self.assertGreater(niveau(pistes["guide"], 7, 2), niveau(pistes["musique"], 7, 2) - 1)   # voix en plus
        self.assertEqual(suivi[-1][0], 1.0)
        self.assertTrue(all(0 <= f <= 1 for f, _t in suivi))
        self.assertEqual(sorted(f for f, _t in suivi), [f for f, _t in suivi])        # progression qui avance

    def test_transposition(self):
        t = self.travail()
        cache = self.tmp / "cache"
        pistes = seance.preparer_pistes(t, -3, cache=cache)
        self.assertTrue(pistes["musique"].name.endswith("_m3.mp3"))
        self.assertAlmostEqual(controle.duree(pistes["musique"]), 11, delta=0.5)
        origine = seance.preparer_pistes(t, 0, cache=cache)
        self.assertNotEqual(pistes["musique"], origine["musique"])
        self.assertGreater(niveau(pistes["musique"], 7, 2), -35)

    def test_erreurs(self):
        t = self.tmp / "travail"
        t.mkdir()
        with self.assertRaises(seance.ErreurSeance):
            seance.preparer_pistes(t, 0, cache=self.tmp / "cache")
        with self.assertRaises(seance.ErreurSeance):
            seance.preparer_pistes(t, -9, cache=self.tmp / "cache")

    def test_empreinte_depend_des_sons(self):
        t = self.travail()
        a = seance.empreinte(t)
        self.assertEqual(a, seance.empreinte(t))
        (t / "instrumental.wav").write_bytes(b"autre")
        self.assertNotEqual(a, seance.empreinte(t))


def rapport(f0, p10=None, p90=None, classe="homme", extra=None):
    d = {"decision_hauteur": {"voix_principale": {"f0_p10_hz": p10 or f0 * 0.8, "f0_med_hz": f0,
                                                  "f0_p90_hz": p90 or f0 * 1.25, "classe": classe}}}
    d["decision_hauteur"].update(extra or {})
    return d


class TestConseils(Base):
    def doublage(self, nom, rap, lrc=False, travail=True):
        d = self.tmp / "sorties" / nom
        (d / "travail" / "voixA").mkdir(parents=True)
        if travail:
            (d / "travail" / "instrumental.wav").write_bytes(b"")
            (d / "travail" / "voix_source.wav").write_bytes(b"")
        if rap is not None:
            (d / "travail" / "voixA" / "rapport.json").write_text(json.dumps(rap), encoding="utf-8")
        if lrc:
            (d / f"{nom} - voix voixA.lrc").write_text("[la:en]\n[00:01.00]Un\n", encoding="utf-8")
            (d / f"{nom} - voix voixA (auto-tune).lrc").write_text("[00:02.00]Deux\n", encoding="utf-8")
        return d

    def test_chanteur_principal(self):
        r = rapport(250.0, extra={"chanteurs": [{"nom": "femme", "f0_med_hz": 300, "f0_p10_hz": 200,
                                                  "f0_p90_hz": 400, "classe": "femme"}]})
        self.assertEqual(seance.chanteur_principal(r)["f0_med_hz"], 250.0)
        # sans voix_principale : le premier dictionnaire qui porte la tessiture
        sans = {"decision_hauteur": {"chanteurs": [{"nom": "a", "f0_med_hz": 300, "f0_p10_hz": 200,
                                                     "f0_p90_hz": 400, "classe": "femme"}]}}
        self.assertEqual(seance.chanteur_principal(sans)["f0_med_hz"], 300)
        self.assertIsNone(seance.chanteur_principal({"decision_hauteur": {}}))
        self.assertIsNone(seance.chanteur_principal(None))

    def test_lister_doublages(self):
        self.doublage("Chanson d'exemple", rapport(250.0), lrc=True)
        self.doublage("Sans travail", rapport(250.0), travail=False)
        self.doublage("Sans rapport", None)
        liste = {d.titre: d for d in seance.lister_doublages(self.tmp / "sorties")}
        self.assertEqual(set(liste), {"Chanson d'exemple", "Sans rapport"})
        self.assertEqual(liste["Chanson d'exemple"].chanteur["f0_med_hz"], 250.0)
        self.assertEqual(liste["Chanson d'exemple"].lrc.name, "Chanson d'exemple - voix voixA.lrc")   # pas l'auto-tune
        self.assertIsNone(liste["Sans rapport"].chanteur)
        self.assertEqual(seance.lister_doublages(self.tmp / "absent"), [])

    def test_zone_de_chant(self):
        v = self.tmp / "voix" / "voixA"
        v.mkdir(parents=True)
        self.assertIsNone(seance.zone_de_chant(v))
        (v / "voix.json").write_text(json.dumps({"hauteur_parole_mediane_hz": 100.0}), encoding="utf-8")
        centre, bas, haut, mesuree = seance.zone_de_chant(v)
        self.assertEqual((centre, mesuree), (200.0, False))
        (v / "tessiture.json").write_text(json.dumps({"p05": 90, "p10": 100, "p50": 150, "p90": 300, "p95": 330,
                                                       "confort": [110, 220]}), encoding="utf-8")
        centre, bas, haut, mesuree = seance.zone_de_chant(v)
        self.assertTrue(mesuree)
        self.assertEqual((bas, haut), (110, 220))
        self.assertAlmostEqual(centre, math.sqrt(110 * 220), places=3)
        (v / "tessiture.json").write_text("pas du json", encoding="utf-8")
        self.assertEqual(seance.zone_de_chant(v)[0], 200.0)     # retour à la parole

    def test_classement(self):
        # zone de chant : 200 Hz (deux fois 100 Hz de parole)
        zone = (200.0, None, None, False)
        a = seance.Doublage(Path("a"), "Proche", Path("a/travail"), None, rapport(205.0)["decision_hauteur"]["voix_principale"])
        b = seance.Doublage(Path("b"), "Trop haute", Path("b/travail"), None, rapport(400.0)["decision_hauteur"]["voix_principale"])
        c = seance.Doublage(Path("c"), "Sans tessiture", Path("c/travail"), None, None)
        d = seance.Doublage(Path("d"), "Un peu haute", Path("d/travail"), None, rapport(283.0)["decision_hauteur"]["voix_principale"])
        e = seance.Doublage(Path("e"), "Grave", Path("e/travail"), None, rapport(100.0)["decision_hauteur"]["voix_principale"])
        r = seance.conseiller([c, b, e, d, a], zone)
        self.assertEqual([x.doublage.titre for x in r], ["Proche", "Un peu haute", "Trop haute", "Grave", "Sans tessiture"])
        self.assertAlmostEqual(r[0].ecart_st, 12 * math.log2(205 / 200), places=3)
        self.assertEqual(r[0].tonalite, 0)
        self.assertEqual(r[1].tonalite, -6)                          # 283 Hz : 6 demi-tons au-dessus
        self.assertAlmostEqual(r[1].reste_st, 0.0, delta=0.1)
        self.assertEqual(r[2].tonalite, -7)                          # 12 demi-tons : on s'arrête à −7
        self.assertAlmostEqual(r[2].reste_st, 5.0, delta=0.1)
        self.assertEqual(r[3].tonalite, 0)                           # trop grave : on ne monte pas
        self.assertIsNone(r[4].ecart_st)
        self.assertIn("conseillé : 6 demi-tons plus bas", seance.libelle_ecart(r[1]))
        self.assertEqual(seance.libelle_ecart(r[4]), "tessiture inconnue")

    def test_sans_zone(self):
        a = seance.Doublage(Path("a"), "A", Path("a/travail"), None, rapport(205.0)["decision_hauteur"]["voix_principale"])
        r = seance.conseiller([a], None)
        self.assertEqual((r[0].ecart_st, r[0].tonalite), (None, 0))

    def test_tessiture_mesuree_et_ambitus(self):
        zone = (math.sqrt(110 * 220), 110.0, 220.0, True)      # une octave de confort
        large = seance.Doublage(Path("l"), "Large", Path("l"), None, rapport(155.0, p10=100, p90=400)["decision_hauteur"]["voix_principale"])
        r = seance.conseiller([large], zone)
        self.assertTrue(r[0].zone_trop_etroite)                  # 24 demi-tons d'ambitus contre 12


class TestLangues(Base):
    def dossier(self, nom="Chanson d'exemple", rapport=None, srt=(), lrc=None, paroles=None):
        d = self.tmp / nom
        (d / "travail" / "voixA").mkdir(parents=True)
        for s in srt:
            (d / f"{nom} - voix voixA.{s}.srt").write_text("", encoding="utf-8")
        if lrc:
            (d / f"{nom}.lrc").write_text(f"[la:{lrc}]\n[00:01.00]x\n", encoding="utf-8")
        if paroles:
            (d / "travail" / "voixA" / "paroles.json").write_text(json.dumps({"langue": paroles}), encoding="utf-8")
        return d

    def test_langue_du_doublage(self):
        rap = {"paroles_sorties": {"langue_chantee": {"langue": "EN"}}}
        self.assertEqual(seance.langue_du_doublage(self.dossier("a"), rap), "en")
        self.assertEqual(seance.langue_du_doublage(self.dossier("b"), {"pistes_audio": {"langue": {"paroles": "fra"}}}),
                         "fr")
        self.assertEqual(seance.langue_du_doublage(self.dossier("c", paroles="es")), "es")
        self.assertEqual(seance.langue_du_doublage(self.dossier("d", lrc="pt")), "pt")
        # suffixe des .srt : un seul → celui-là ; deux → la langue qui n'est pas le français (la traduction)
        self.assertEqual(seance.langue_du_doublage(self.dossier("e", srt=("fr",))), "fr")
        self.assertEqual(seance.langue_du_doublage(self.dossier("f", srt=("fr", "en"))), "en")
        self.assertEqual(seance.langue_du_doublage(self.dossier("g")), "")

    def test_langues_de_la_voix(self):
        v = self.tmp / "voix" / "voixA"
        v.mkdir(parents=True)
        self.assertEqual(seance.langues_voix(v), ("fr",))
        (v / "voix.json").write_text(json.dumps({"langues": ["FR", "en-US"]}), encoding="utf-8")
        self.assertEqual(seance.langues_voix(v), ("fr", "en"))
        (v / "voix.json").write_text(json.dumps({"langues": []}), encoding="utf-8")
        self.assertEqual(seance.langues_voix(v), ("fr",))

    def test_filtre_par_langue(self):
        ch = rapport(205.0)["decision_hauteur"]["voix_principale"]
        liste = [seance.Doublage(Path(n), n, Path(n), None, ch, langue) for n, langue in
                 (("Fr", "fr"), ("En", "en"), ("Inconnue", ""))]
        zone = (200.0, None, None, False)
        self.assertEqual([c.doublage.titre for c in seance.conseiller(liste, zone, ("fr",))], ["Fr"])
        self.assertEqual({c.doublage.titre for c in seance.conseiller(liste, zone, ("fr", "en"))}, {"Fr", "En"})
        self.assertEqual(len(seance.conseiller(liste, zone, ("fr",), toutes=True)), 3)
        self.assertEqual(len(seance.conseiller(liste, zone)), 3)      # sans langues : pas de filtre


class TestLrc(Base):
    def test_decalage_de_cinq_secondes(self):
        f = self.tmp / "x.lrc"
        f.write_text("[ti:Chanson d'exemple]\n[la:en]\n[00:00.50]Un\n[01:02.25]Deux\n[01:05,00]\n[01:10.00]Trois\n",
                     encoding="utf-8")
        p = seance.lire_lrc(f)
        self.assertEqual([l.texte for l in p.originale.lignes], ["Un", "Deux", "Trois"])
        self.assertAlmostEqual(p.originale.lignes[0].debut, 5.5)
        self.assertAlmostEqual(p.originale.lignes[1].debut, 67.25)
        self.assertAlmostEqual(p.originale.lignes[0].fin, 67.25)
        self.assertEqual(p.originale.langue, "en")
        self.assertEqual(p.origine, "lrc")
        self.assertEqual(p.decalage_s, 0.0)
        self.assertIsNone(seance.lire_lrc(self.tmp / "absent.lrc"))
        (self.tmp / "vide.lrc").write_text("[ti:rien]\n", encoding="utf-8")
        self.assertIsNone(seance.lire_lrc(self.tmp / "vide.lrc"))


@unittest.skipUnless(FFMPEG, "ffmpeg ou ffprobe absent")
class TestImport(Base):
    def test_import_copie_telle_quelle(self):
        voix = self.tmp / "voix" / "voixA"
        voix.mkdir(parents=True)
        src = self.tmp / "telephone"
        src.mkdir()
        synthese(src / "Enregistrement 1.m4a", "sine=frequency=330", 2)
        synthese(src / "b.wav", "sine=frequency=330", 1, canaux=1)
        (src / "notes.txt").write_text("pas un son", encoding="utf-8")
        (src / "faux.mp3").write_text("echo pas un son ; rm -rf /", encoding="utf-8")
        octets = (src / "Enregistrement 1.m4a").read_bytes()
        res = seance.importer([src / "Enregistrement 1.m4a", src / "b.wav", src / "notes.txt", src / "faux.mp3",
                               src / "absent.ogg"], voix, "notes", maintenant=1_800_000_000)
        ok = [r for r in res if r[1]]
        self.assertEqual(len(ok), 2)
        self.assertEqual(len([r for r in res if not r[1]]), 3)
        self.assertEqual({r[2] for r in res if not r[1]},
                         {"format non pris en charge (.txt)", "ce fichier n’est pas un son lisible", "fichier introuvable"})
        noms = sorted(p.name for p in (voix / "chant").iterdir())
        self.assertEqual(len(noms), 2)
        for n in noms:
            self.assertRegex(n, r"^\d{8}-\d{6}(-\d)?-notes\.(m4a|wav)$")
        copie = next(p for p in (voix / "chant").iterdir() if p.suffix == ".m4a")
        self.assertEqual(copie.read_bytes(), octets)                  # tel quel
        self.assertEqual((src / "Enregistrement 1.m4a").read_bytes(), octets)   # la source n'est pas touchée
        self.assertFalse((voix / "parole").exists())

    def test_parole_va_dans_parole(self):
        voix = self.tmp / "voix" / "voixA"
        src = self.tmp / "p.opus"
        synthese(src, "sine=frequency=200", 1, canaux=1)
        r = seance.importer([src], voix, "parole")
        self.assertEqual(r[0][1].parent.name, "parole")
        self.assertTrue(r[0][1].name.endswith("-parole.opus"))
        with self.assertRaises(seance.ErreurSeance):
            seance.importer([src], voix, "inconnue")

    def test_noms_sans_collision(self):
        a = seance.nom_importe("chanson", ".M4A", maintenant=1_800_000_000)
        b = seance.nom_importe("chanson", ".m4a", {a}, maintenant=1_800_000_000)
        self.assertNotEqual(a, b)
        self.assertTrue(b.endswith("-chanson-2.m4a"))

    def test_formats_du_telephone(self):
        for ext in (".m4a", ".aac", ".mp3", ".wav", ".ogg", ".opus", ".3gp", ".amr", ".M4A"):
            self.assertTrue(seance.format_accepte("x" + ext), ext)
        for ext in (".sh", ".txt", ".mp4", ".m3u8", ""):
            self.assertFalse(seance.format_accepte("x" + ext), ext)

    def test_duree_enregistree(self):
        voix = self.tmp / "voix" / "voixA"
        (voix / "chant").mkdir(parents=True)
        (voix / "parole").mkdir()
        synthese(voix / "chant" / "a.wav", "sine=frequency=300", 3)
        synthese(voix / "chant" / "b.m4a", "sine=frequency=300", 2)
        synthese(voix / "parole" / "c.wav", "sine=frequency=300", 4)
        (voix / "chant" / "note.txt").write_text("x", encoding="utf-8")
        chant, parole = seance.duree_enregistree(voix)
        self.assertAlmostEqual(chant, 5, delta=0.3)
        self.assertAlmostEqual(parole, 4, delta=0.2)
        self.assertEqual(seance.duree_enregistree(self.tmp / "vide"), (0.0, 0.0))


MUSIQUE = ("aevalsrc='random(0)*(lt(mod(t,1.3),0.4)*0.6+lt(mod(t,2.9),0.3)*0.4+0.02)*(0.6+0.4*sin(2*PI*t/7.3))':"
           "s=22050")


@unittest.skipUnless(FFMPEG, "ffmpeg ou ffprobe absent")
class TestControles(Base):
    def prise(self, nom, graphe, duree=40, canaux=1):
        p = self.tmp / nom
        synthese(p, graphe, duree, canaux)
        return p

    def musique(self):
        return self.prise("musique.wav", MUSIQUE, 40, 2)

    def voix_chantee(self, graphe="sine=frequency=440:sample_rate=44100"):
        """Une voix de synthèse (−12 dBFS) qui respire : 1 s de pause toutes les 4 s."""
        return self.prise("voix.wav", graphe + ",volume='if(lt(mod(t,4),3),2,0.00002)':eval=frame", 40)

    def test_prise_propre(self):
        c = seance.controler_prise(self.voix_chantee(), "notes", self.musique())
        self.assertAlmostEqual(c.duree_s, 40, delta=0.5)
        self.assertLess(c.fuite, 0.4)
        self.assertEqual(c.alertes, [], c.alertes)
        self.assertTrue(c.ok)

    def test_musique_du_casque_dans_la_prise(self):
        musique = self.musique()
        voix = self.voix_chantee()
        melange = self.tmp / "fuite.wav"
        # la musique du casque, plus basse et en retard de 2,5 s, sous la voix
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(voix), "-i", str(musique), "-filter_complex",
                        "[1:a]aformat=channel_layouts=mono,adelay=2500:all=1,volume=1.5[m];"
                        "[0:a][m]amix=inputs=2:normalize=0:duration=first", "-ac", "1", str(melange)], check=True,
                       capture_output=True, timeout=60)
        c = seance.controler_prise(melange, "notes", musique)
        self.assertGreaterEqual(c.fuite, seance.SEUIL_FUITE)
        self.assertTrue(any("musique du casque" in a for a in c.alertes), c.alertes)
        # sans piste jouée, pas de recherche
        self.assertIsNone(seance.controler_prise(melange, "notes").fuite)

    def test_voix_qui_suit_le_rythme_sans_musique(self):
        # une voix dont le volume varie, mais pas comme la musique : pas de fausse alerte
        voix = self.voix_chantee("aevalsrc='random(1)*0.5*(0.5+0.5*sin(2*PI*t/3.1))':s=22050")
        c = seance.controler_prise(voix, "notes", self.musique())
        self.assertLess(c.fuite, seance.SEUIL_FUITE)
        self.assertFalse(any("musique du casque" in a for a in c.alertes))

    def test_saturation_bruit_et_duree(self):
        sature = self.prise("sature.wav", "sine=frequency=440:sample_rate=44100,volume=16", 40)
        self.assertTrue(any("Saturation" in a for a in seance.controler_prise(sature, "libre").alertes))
        bruit = self.prise("bruit.wav", "anoisesrc=duration=40:amplitude=0.1:sample_rate=44100", 40)
        self.assertTrue(any("Bruit de fond" in a for a in seance.controler_prise(bruit, "libre").alertes))
        court = self.prise("court.wav", "sine=frequency=440:sample_rate=44100,volume=0.2", 3)
        self.assertTrue(any("trop courte" in a for a in seance.controler_prise(court, "chanson").alertes))

    def test_fichier_illisible(self):
        faux = self.tmp / "faux.m4a"
        faux.write_text("rien", encoding="utf-8")
        c = seance.controler_prise(faux, "libre")
        self.assertFalse(c.ok)


if __name__ == "__main__":
    unittest.main()
