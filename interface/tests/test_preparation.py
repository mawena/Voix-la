"""Préparation à l'avance des pistes séparées (voixla/preparation.py, et ce qu'elle demande à voixla.pistes,
voixla.chemins, voixla.executeur) : sans file-attente, garde-fou mémoire, déclenchement sur « sortie » à la fin d'un
doublage, ce qui se prépare à l'ouverture et au démarrage, taille et protection du cache, réessai, avancement."""

import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from voixla import chemins, executeur, pistes, preparation as prep, rapports

from .test_pistes import FFMPEG, RAPPORT_CHANT, EssaiAvecCache, ecrire, ffmpeg
from .test_pistes_video import video_de_synthese

MEMINFO = "MemTotal:       14000000 kB\nMemFree:          300000 kB\nMemAvailable:     {} kB\n"


class EssaiCommandes(unittest.TestCase):
    def test_preparation_legere_sans_file_attente(self):
        for argv in (["video", "/s/d.mp4", "/t/instrumental.wav"], ["voix", "/t/jules"], ["miniature", "/s/d.mp4"]):
            c = prep.commande("/usr/bin/python3", "voixla.pistes", argv, duree_s=240.0,
                              file_attente="/bin/file-attente", ionice="/usr/bin/ionice")
            self.assertEqual(c[:6], ["nice", "-n", "19", "/usr/bin/ionice", "-c3", "/usr/bin/python3"])
            self.assertNotIn("/bin/file-attente", c, "jamais derrière les travaux lourds des autres sessions")
            self.assertEqual(c[6:8], ["-m", "voixla.pistes"])
        c = prep.commande("py", "voixla.paroles", ["sous-titres", "/s/d.mkv"], 1.0, "/bin/file-attente", "ionice")
        self.assertNotIn("/bin/file-attente", c)

    def test_sans_ionice(self):
        c = prep.commande("py", "voixla.pistes", ["voix", "/t/j"], ionice="")
        self.assertEqual(c[:4], ["nice", "-n", "19", "py"])

    def test_analyse_lourde_garde_file_attente(self):
        c = prep.commande("py", "voixla.analyse", ["pistes", "/s/a.mp3"], 240.0, "/bin/file-attente")
        self.assertEqual(c[:5], ["/bin/file-attente", "voix-la-analyse", "1", "2", "--"])
        self.assertEqual(c[5:8], ["nice", "-n", "10"])
        courte = prep.commande("py", "voixla.analyse", ["pistes", "/s/a.mp3"], 30.0, "/bin/file-attente")
        self.assertEqual(courte[:3], ["nice", "-n", "10"])
        self.assertEqual(prep.commande("py", "voixla.analyse", ["courbes", "/t/j"], None, None)[:3],
                         ["nice", "-n", "10"])

    def test_lire_avancement(self):
        self.assertEqual(prep.lire_avancement('{"avancement": 0.42, "etape": "video"}'), (0.42, "video"))
        self.assertEqual(prep.lire_avancement('{"avancement": 1.7}'), (1.0, ""))
        self.assertIsNone(prep.lire_avancement('{"video": "/cache/x.mp4", "duree_s": 3.1}'))
        self.assertIsNone(prep.lire_avancement("erreur : son introuvable"))
        self.assertIsNone(prep.lire_avancement('{"avancement": "?"}'))


class EssaiMemoireEtCalme(unittest.TestCase):
    def test_memoire(self):
        self.assertAlmostEqual(prep.memoire_disponible_mo(MEMINFO.format(2048000)), 2000.0)
        self.assertIsNone(prep.memoire_disponible_mo("MemTotal: 1 kB\n"))
        self.assertTrue(prep.memoire_suffisante(800))
        self.assertFalse(prep.memoire_suffisante(650), "sous 700 Mo : on attend")
        self.assertTrue(prep.memoire_suffisante(None), "illisible : on ne bloque pas")

    def test_garde_fou_des_demandes(self):
        legere = prep.Demande(("video",), ["video"], "voixla.pistes")
        lourde = prep.Demande(("pistes",), ["pistes"], "voixla.analyse")
        self.assertFalse(prep.peut_lancer(legere, 500))
        self.assertTrue(prep.peut_lancer(legere, 900))
        self.assertTrue(prep.peut_lancer(lourde, 500), "les analyses lourdes ont le garde-fou de file-attente")

    def test_poste_calme(self):
        self.assertTrue(prep.poste_calme(2.0, 14, 3000))
        self.assertFalse(prep.poste_calme(9.0, 14, 3000), "charge trop haute")
        self.assertFalse(prep.poste_calme(1.0, 14, 1200), "trop peu de mémoire")
        self.assertTrue(prep.poste_calme(None, 14, None))

    def test_attendre(self):
        horloge = [0.0]
        etats = iter([False, False, True])
        self.assertTrue(prep.attendre(lambda: next(etats), delai_s=60, pas_s=10,
                                      dormir=lambda s: horloge.__setitem__(0, horloge[0] + s),
                                      horloge=lambda: horloge[0]))
        self.assertEqual(horloge[0], 20.0)
        horloge[0] = 0.0
        self.assertFalse(prep.attendre(lambda: False, delai_s=30, pas_s=10,
                                       dormir=lambda s: horloge.__setitem__(0, horloge[0] + s),
                                       horloge=lambda: horloge[0]), "abandon au bout du délai")


class EssaiFile(unittest.TestCase):
    def demande(self, cle, rappels=None):
        return prep.Demande((cle,), [cle], "voixla.pistes", apres=list(rappels or []))

    def test_sans_doublon(self):
        f = prep.FilePreparations()
        vus = []
        self.assertTrue(f.ajouter(self.demande("beat", [lambda *a: vus.append(1)])))
        self.assertFalse(f.ajouter(self.demande("beat", [lambda *a: vus.append(2)])))
        d = f.prochaine()
        self.assertEqual(len(d.apres), 2, "les deux rappels attendent la même préparation")
        self.assertIsNone(f.prochaine(), "une à la fois")
        self.assertFalse(f.ajouter(self.demande("beat")), "en cours : pas de seconde préparation")
        self.assertIs(f.finir(), d)
        self.assertFalse(f.occupee())

    def test_la_piste_cliquee_passe_devant(self):
        f = prep.FilePreparations()
        for cle in ("beat", "ma_voix", "voix_origine"):
            f.ajouter(self.demande(cle))
        f.ajouter(self.demande("voix_origine"), prioritaire=True)
        self.assertEqual([d.cle for d in f.attente], [("voix_origine",), ("beat",), ("ma_voix",)])
        f.ajouter(self.demande("miniature"), prioritaire=True)
        self.assertEqual(f.prochaine().cle, ("miniature",))

    def test_reessai_apres_echec(self):
        f = prep.FilePreparations()
        f.ajouter(self.demande("beat"))
        f.prochaine()
        f.finir()                                   # échec : la demande est close
        self.assertTrue(f.ajouter(self.demande("beat")), "« Réessayer » relance vraiment")
        self.assertEqual(f.prochaine().cle, ("beat",))


class EssaiTaches(EssaiAvecCache):
    TOUT = ["original.wav", "instrumental.wav", "voix_source.wav", "jules/seche_voix_convertie.wav",
            "jules/reverb_voix_convertie.wav"]

    def test_tout_manque(self):
        r = self.doublage(fichiers=self.TOUT, rapport=RAPPORT_CHANT)
        ecrire(r.video, b"video")
        t = prep.taches(r)
        self.assertEqual([x.nom for x in t], ["beat", "ma_voix", "voix_origine", "miniature"])
        self.assertEqual(t[0].argv, ["video", str(r.video), str(r.travail / "instrumental.wav")])
        self.assertEqual(t[1].argv, ["video", str(r.video), "--voix", str(r.dossier_voix)],
                         "ma voix seule : combinée puis copiée, en une tâche")
        self.assertEqual(t[2].argv, ["video", str(r.video), str(r.travail / "voix_source.wav")])

    def test_deja_pret(self):
        r = self.doublage(fichiers=self.TOUT, rapport=RAPPORT_CHANT)
        ecrire(r.video, b"video")
        ecrire(pistes.chemin_video(r.video, pistes._signature(r.travail / "instrumental.wav")), b"copie")
        ecrire(pistes.chemin_miniature(r.video), b"jpg")
        self.assertEqual([x.nom for x in prep.taches(r)], ["ma_voix", "voix_origine"])

    def test_mp3_seul(self):
        r = self.doublage(fichiers=self.TOUT, rapport=RAPPORT_CHANT, video=False)
        self.assertEqual([(x.nom, x.argv[0]) for x in prep.taches(r)], [("ma_voix", "voix")],
                         "sans vidéo : seulement la voix combinée")

    def test_recents(self):
        for i in range(7):
            r = self.doublage(titre=f"Chanson {i}", rapport=RAPPORT_CHANT)
            os.utime(r.video, (1000 + i, 1000 + i))
        self.doublage(titre="Inachevée")              # sans rapport, le plus récent : pas complet, écarté
        rs = rapports.lister_resultats(self.donnees / "sorties", {"jules": "Jules"})
        self.assertEqual(rs[0].titre, "Inachevée")
        self.assertEqual([x.titre for x in prep.recents(rs)],
                         ["Chanson 6", "Chanson 5", "Chanson 4", "Chanson 3", "Chanson 2"], "les 5 derniers")
        self.assertEqual(len(prep.recents(rs, 2)), 2)


class EssaiSorties(EssaiAvecCache):
    def test_dossiers_des_evenements(self):
        a = self.doublage(titre="Chanson A", rapport=RAPPORT_CHANT)
        b = self.doublage(titre="Chanson B", rapport=RAPPORT_CHANT)
        ev = [{"type": "etape", "nom": "mix"}, {"type": "sortie", "mp4": str(a.video), "mp3": str(a.mp3)},
              {"type": "sortie", "chemin": str(b.mp3)}, {"type": "sortie", "mp4": str(a.video)},
              {"type": "sortie", "mp4": str(self.tmp / "ailleurs" / "x.mp4")}]
        self.assertEqual(prep.dossiers_des_sorties(ev), [str(a.dossier), str(b.dossier)])

    def test_repli_sur_la_demande(self):
        a = self.doublage(titre="Chanson A", rapport=RAPPORT_CHANT)
        self.assertEqual(prep.dossiers_des_sorties([], {"sorties": [str(a.dossier), str(self.tmp / "absent")]}),
                         [str(a.dossier)])

    def test_executeur_prepare_a_la_fin(self):
        a = self.doublage(titre="Chanson A", rapport=RAPPORT_CHANT)
        d = self.tmp / "travaux" / "1"
        d.mkdir(parents=True)
        ev = d / "evenements.jsonl"
        ev.write_text(json.dumps({"type": "sortie", "mp4": str(a.video)}) + "\n{abîmé\n", encoding="utf-8")
        lances = []
        os.environ.pop("VOIXLA_SANS_PREPARATION", None)
        with mock.patch.object(executeur, "journal"):
            r = executeur.preparer_apres(d, {"evenements": str(ev), "donnees": str(self.donnees)}, lances.append)
        self.assertEqual(r, [str(a.dossier)])
        self.assertEqual(lances, [["--donnees", str(self.donnees), str(a.dossier)]])

    def test_executeur_sans_preparation_pour_les_essais(self):
        os.environ["VOIXLA_SANS_PREPARATION"] = "1"
        lances = []
        self.assertEqual(executeur.preparer_apres(self.tmp, {"sorties": [str(self.tmp)]}, lances.append), [])
        self.assertEqual(lances, [])

    def test_lancer_en_fond(self):
        vus = {}

        def popen(cmd, **k):
            vus.update(cmd=cmd, **k)
            return "processus"
        self.assertEqual(prep.lancer_en_fond(["--recents", 5, "--si-calme"], env={"PATH": "/usr/bin"}, popen=popen),
                         "processus")
        self.assertEqual(vus["cmd"][:3], ["nice", "-n", "19"])
        self.assertEqual(vus["cmd"][-5:], ["-m", "voixla.preparation", "--recents", "5", "--si-calme"])
        self.assertTrue(vus["start_new_session"], "détaché : survit à la fenêtre")
        self.assertIn(str(chemins.INTERFACE), vus["env"]["PYTHONPATH"])
        self.assertEqual(vus["stdin"], subprocess.DEVNULL)


class EssaiPreparateur(EssaiAvecCache):
    def doublage_pret(self):
        r = self.doublage(fichiers=EssaiTaches.TOUT, rapport=RAPPORT_CHANT)
        ecrire(r.video, b"video")
        return r

    def test_une_tache_a_la_fois_dans_l_ordre(self):
        r = self.doublage_pret()
        faites = []
        n = prep.preparer([r], faire=lambda t: faites.append(t.nom) or True, etat=lambda: (1.0, 14, 4000),
                          dormir=lambda s: None)
        self.assertEqual(faites, ["beat", "ma_voix", "voix_origine", "miniature"])
        self.assertEqual(n, (4, 0))

    def test_attend_la_memoire(self):
        r = self.doublage_pret()
        memoire = iter([300, 400, 2000] + [2000] * 10)
        dodos = []
        with mock.patch.object(prep, "_dire"):
            n = prep.preparer([r], faire=lambda t: True, etat=lambda: (1.0, 14, next(memoire)), dormir=dodos.append)
        self.assertEqual(n, (4, 0))
        self.assertEqual(dodos, [prep.ATTENTE_MEMOIRE_S] * 2, "deux attentes, puis la mémoire suffit")

    def test_si_calme_attend_le_calme(self):
        r = self.doublage_pret()
        charges = iter([12.0, 12.0, 1.0] + [1.0] * 10)
        dodos = []
        with mock.patch.object(prep, "_dire"):
            prep.preparer([r], si_calme=True, faire=lambda t: True, etat=lambda: (next(charges), 14, 4000),
                          dormir=dodos.append)
        self.assertEqual(len(dodos), 2)

    def test_une_piste_ratee_n_arrete_pas_les_autres(self):
        r = self.doublage_pret()

        def faire(t):
            if t.nom == "ma_voix":
                raise pistes.ErreurPistes("son abîmé")
            return True
        with mock.patch.object(prep, "_dire"):
            self.assertEqual(prep.preparer([r], faire=faire, etat=lambda: (1.0, 14, 4000), dormir=lambda s: None),
                             (3, 1))

    @unittest.skipUnless(FFMPEG, "ffmpeg absent")
    def test_vraie_preparation(self):
        r = self.doublage(fichiers=["original.wav", "voix_source.wav"], rapport=RAPPORT_CHANT)
        video_de_synthese(r.video, duree=3.0)
        for f, freq in (("instrumental.wav", 440), ("voix_source.wav", 220), ("jules/seche_voix_convertie.wav", 330),
                        ("jules/reverb_voix_convertie.wav", 110)):
            ffmpeg(r.travail / f, f"sine=frequency={freq}:sample_rate=44100", duree=3)
        sortie = io.StringIO()
        with mock.patch("sys.stdout", sortie), mock.patch.object(prep, "etat_du_poste", lambda: (0.5, 14, 4000)):
            self.assertEqual(prep.main(["--donnees", str(self.donnees), str(r.dossier)]), 0)
        self.assertEqual(prep.taches(r), [], "tout est prêt : " + sortie.getvalue()[-300:])
        p = pistes.choisir_pistes(r, "video")
        self.assertTrue(all(p[c].video is not None for c in pistes.SANS_IMAGE))
        self.assertIsNotNone(pistes.miniature_prete(r.video))
        self.assertTrue((self.cache / "verrous" / "preparation.verrou").exists())


class EssaiCache(EssaiAvecCache):
    def test_taille_reglable(self):
        self.assertEqual(pistes.limite_cache_video_mo({}), pistes.CACHE_VIDEO_MO)
        self.assertEqual(pistes.limite_cache_video_mo({"cache_pistes_mo": 5120}), 5120)
        self.assertEqual(pistes.limite_cache_video_mo({"cache_pistes_mo": None}), 2048)
        self.assertEqual(pistes.limite_cache_video_mo({"cache_pistes_mo": "abc"}), 2048)
        chemins.modifier_preference("cache_pistes_mo", 1)
        r = self.doublage(rapport=RAPPORT_CHANT)
        ecrire(r.video, b"x" * 2 * 1024 * 1024)
        self.assertIn("trop lourde", pistes.trop_lourde(r.video))
        self.assertIn("réglable dans Réglages", pistes.trop_lourde(r.video))
        chemins.modifier_preference("cache_pistes_mo", 3)
        self.assertEqual(pistes.trop_lourde(r.video), "")

    def test_les_copies_ouvertes_ne_sont_jamais_purgees(self):
        d = self.cache / "videos"
        d.mkdir(parents=True)
        vieilles = []
        for i in range(3):
            f = d / f"copie{i}.mp4"
            f.write_bytes(b"x" * 400 * 1024)
            os.utime(f, (1000 + i, 1000 + i))
            vieilles.append(f)
        chemins.proteger([vieilles[0]])               # la copie du doublage ouvert : la plus ancienne
        self.assertEqual(chemins.proteges(), {str(vieilles[0])})
        chemins.purger_dossier(d, 0.9)
        self.assertTrue(vieilles[0].exists(), "protégée, bien que la moins récemment lue")
        self.assertEqual([f.exists() for f in vieilles], [True, False, True])
        chemins.proteger([])                          # page fermée : plus rien de protégé
        self.assertEqual(chemins.proteges(), set())
        chemins.purger_dossier(d, 0.5)
        self.assertEqual([f.exists() for f in vieilles], [False, False, True])

    def test_protection_d_un_processus_fini(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        chemins.proteger(["/cache/videos/x.mp4"], pid=p.pid)
        self.assertEqual(chemins.proteges(), set(), "processus fini : sa protection ne compte plus")
        chemins.proteger(["/cache/videos/y.mp4"])
        self.assertNotIn(str(p.pid), chemins.lire_json(chemins.fichier_proteges(), {}))

    def test_avancement_de_ffmpeg(self):
        vus = []
        pistes.lire_progression(["frame=10", "out_time_us=1500000", "out_time_ms=3000000", "out_time_us=N/A",
                                 "progress=end"], 3.0, vus.append)
        self.assertEqual(vus, [0.5, 1.0, 1.0], "« progress=end » : fini")

    def test_avancement_sur_la_sortie(self):
        os.environ["VOIXLA_AVANCEMENT"] = "1"
        self.addCleanup(os.environ.pop, "VOIXLA_AVANCEMENT", None)
        dire = pistes.avancement_cli("video")
        sortie = io.StringIO()
        with mock.patch("sys.stdout", sortie):
            for f in (0.01, 0.03, 0.06, 0.5, 1.0):
                dire(f)
        lignes = [json.loads(l) for l in sortie.getvalue().splitlines()]
        self.assertEqual([l["avancement"] for l in lignes], [0.01, 0.06, 0.5, 1.0], "tous les 5 % au plus")
        os.environ.pop("VOIXLA_AVANCEMENT")
        self.assertIsNone(pistes.avancement_cli("video"))

    @unittest.skipUnless(FFMPEG, "ffmpeg absent")
    def test_copie_avec_avancement(self):
        r = self.doublage(fichiers=["original.wav"], rapport=RAPPORT_CHANT)
        video_de_synthese(r.video, duree=3.0)
        ffmpeg(r.travail / "instrumental.wav", "sine=frequency=440:sample_rate=44100", duree=3)
        vus = []
        f = pistes.preparer_video(r.video, son=r.travail / "instrumental.wav", avancement=vus.append)
        self.assertTrue(f.is_file())
        self.assertTrue(vus and vus[-1] == 1.0 and all(0.0 <= x <= 1.0 for x in vus), vus)
        self.assertTrue(any((self.cache / "verrous").glob("videos-*.verrou")), "verrou par fichier, à part")
        self.assertFalse(list(f.parent.glob("*.verrou")), "rien d'autre que les copies dans videos/")


if __name__ == "__main__":
    unittest.main()
