"""Essais de l'export pour les réseaux (voixla.export_social) : extrait (bornes, boucle A-B), proposition du refrain ou
du passage le plus chanté, mise en page des formats, sous-titres ASS à effet karaoké (mêmes lignes à l'écran et même
calage que le lecteur), choix de la piste son dans la vidéo à cinq pistes, commandes ffmpeg et file-attente, noms de
sortie et corbeille ; de bout en bout, sur un petit doublage inventé (tests/faux_partage.py) : vidéo H.264 et AAC
« faststart », piste son reconnue à sa fréquence, mot en cours de remplissage en jaune sur l'image, aperçu, ligne de
commande et annulation.

Titres, voix et paroles inventés ; tout dans des dossiers temporaires (VOIXLA_DONNEES, VOIXLA_CACHE). Les encodages
de bout en bout prennent x264 « ultrafast » pour rester légers."""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from voixla import chemins, corbeille, export_social as es, paroles as P, pistes, rapports

from . import faux_partage as fp
from .commun import EssaiIsole

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
INTERFACE = Path(__file__).resolve().parents[1]
RAPIDE = [("ultrafast" if x == "medium" else x) for x in es.X264]


def ligne(debut, fin, texte, mots=None):
    """Une ligne aux mots répartis selon leur longueur (ou donnés : [(début, fin, mot)])."""
    if mots is None:
        mots, u, total = [], debut, sum(len(m) + 1 for m in texte.split())
        for m in texte.split():
            d = (fin - debut) * (len(m) + 1) / total
            mots.append((u, u + d * 0.9, m.strip(",.!?¿¡")))
            u += d
    return P.Ligne(debut, fin, texte, [P.Mot(a, b, m) for a, b, m in mots])


def couche(lignes, langue="es"):
    return P.Couche(langue, "essai", lignes)


def paroles_de(donnees_json, tmp):
    f = Path(tmp) / "paroles.json"
    f.write_text(json.dumps(donnees_json, ensure_ascii=False), encoding="utf-8")
    return P.lire_paroles(f)


def syllabes(texte):
    """[(type, centièmes, texte)] d'un texte ASS à effet karaoké ({\\k…} ou {\\kf…} suivis de leur texte)."""
    return [(t, int(n), s) for t, n, s in re.findall(r"\{\\(kf|k)(\d+)\}([^{]*)", texte)]


# --- Extrait ---------------------------------------------------------------------------------------------------------

class EssaiExtrait(unittest.TestCase):
    def test_bornes_de_15_a_60_s_dans_le_son(self):
        self.assertEqual(es.borner_extrait(10, 30, 200), (10, 30))
        self.assertEqual(es.borner_extrait(10, 5, 200), (10, 15))          # trop court : 15 s
        self.assertEqual(es.borner_extrait(10, 95, 200), (10, 60))         # trop long : 60 s
        self.assertEqual(es.borner_extrait(190, 30, 200), (170, 30))       # au-delà de la fin : l'extrait recule
        self.assertEqual(es.borner_extrait(-4, 30, None), (0, 30))
        self.assertEqual(es.borner_extrait(3, 30, 12), (0, 12))            # son plus court que 15 s : tout le son

    def test_boucle_ab_du_lecteur(self):
        self.assertEqual(es.extrait_de_boucle((48.5, 18.5), 200), (18.5, 30))   # A et B remis dans l'ordre
        self.assertEqual(es.extrait_de_boucle((20, 28), 200), (20, 15))         # 8 s : allongée après B
        self.assertEqual(es.extrait_de_boucle((20, 100), 200), (20, 60))        # 80 s : coupée 60 s après A
        self.assertIsNone(es.extrait_de_boucle((20, None)))
        self.assertIsNone(es.extrait_de_boucle(()))
        self.assertIsNone(es.extrait_de_boucle(None))


# --- Proposition -----------------------------------------------------------------------------------------------------

class EssaiProposition(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="voixla-essai-")
        self.addCleanup(self._tmp.cleanup)

    def test_le_refrain_chante_deux_fois(self):
        p = paroles_de(fp.paroles(75.0), self._tmp.name)
        prop = es.proposer_extrait(p, 0.0, total=75.0)
        self.assertEqual(prop.raison, "refrain")
        self.assertEqual(prop.libelle, "le refrain")
        # l'extrait part juste avant le premier refrain (19 s) et le contient en entier
        self.assertAlmostEqual(prop.debut, 19.0 - es.AMORCE_S, places=2)
        refrain = [l for l in p.originale.lignes if 19.0 <= l.debut < 33.5]
        self.assertTrue(all(prop.debut <= l.debut and l.fin <= prop.debut + prop.duree for l in refrain))
        # sa fin ne coupe pas une ligne
        fin = prop.debut + prop.duree
        self.assertFalse(any(l.debut < fin < l.fin for l in p.originale.lignes), fin)
        self.assertTrue(es.DUREE_MIN_S <= prop.duree <= es.DUREE_MAX_S)

    def test_en_temps_du_son_quand_les_paroles_sont_decalees(self):
        # paroles.json en temps de la vidéo doublée (son en retard de 1,5 s) : la proposition est en temps du son
        p = paroles_de(fp.paroles(75.0, decalage_s=1.5), self._tmp.name)
        ecart = P.ecart_lecture(p, False, None)
        self.assertEqual(ecart, 1.5)
        prop = es.proposer_extrait(p, ecart, total=75.0)
        self.assertAlmostEqual(prop.debut, 19.0 - es.AMORCE_S, places=2)

    def test_sans_refrain_le_passage_le_plus_chante(self):
        # deux lignes isolées au début, puis six lignes serrées à partir de 60 s
        lignes = [ligne(2, 4, "una sola vez"), ligne(20, 22, "otra cosa distinta")]
        textes = ["el puerto duerme", "la luna camina", "nadie contesta ya", "viento de papel", "agua sin dueño",
                  "fuego en la orilla"]
        lignes += [ligne(60 + 3.2 * k, 63 + 3.2 * k, t) for k, t in enumerate(textes)]
        prop = es.proposer_extrait(P.Paroles(couche(lignes), {}, "paroles.json"), 0.0, total=120.0, duree=20)
        self.assertEqual(prop.raison, "passage le plus chanté")
        self.assertAlmostEqual(prop.debut, 60 - es.AMORCE_S, places=2)

    def test_repli_sur_la_courbe_de_la_voix(self):
        pas = 0.02
        voise = [0.0] * int(100 / pas)
        for i in range(int(50 / pas), int(75 / pas)):       # la voix chante de 50 à 75 s
            voise[i] = 220.0
        prop = es.proposer_extrait(None, 0.0, voise, pas, total=100.0, duree=30)
        self.assertEqual(prop.raison, "passage le plus chanté")
        self.assertTrue(49.0 <= prop.debut <= 50.0, prop.debut)
        self.assertEqual(prop.duree, 30)

    def test_rien_a_proposer(self):
        self.assertIsNone(es.proposer_extrait(None, 0.0, None, total=100.0))
        self.assertIsNone(es.proposer_extrait(None, 0.0, [0.0] * 500, 0.02, total=100.0))

    def test_normaliser(self):
        self.assertEqual(es.normaliser("¡Lumbre del ALBA, quédate aquí!"), "lumbre del alba quedate aqui")
        self.assertEqual(es.repetees([(0, 1, "a b"), (2, 3, "a b"), (4, 5, "c"), (6, 7, "c")]), {"a b"})
        # une transcription presque identique (un mot entendu autrement) est la même ligne
        self.assertEqual(es.repetees([(0, 1, "lumbre del alba quedate aqui"), (2, 3, "lumbre del alba quedate asi"),
                                      (4, 5, "otra cosa muy distinta")]),
                         {"lumbre del alba quedate aqui", "lumbre del alba quedate asi"})

    def test_voisement_lu_dans_courbes_json(self):
        d = Path(self._tmp.name)
        (d / "courbes.json").write_text(json.dumps(fp.courbes(20.0)))
        hz, pas = es.lire_voisement(d)
        self.assertEqual((len(hz), pas), (1000, 0.02))
        self.assertEqual(es.lire_voisement(d / "absent"), (None, None))
        self.assertEqual(es.lire_voisement(None), (None, None))


# --- Mise en page --------------------------------------------------------------------------------------------------

class EssaiMiseEnPage(unittest.TestCase):
    def test_vertical_image_entiere_sur_fond_flou(self):
        m = es.mise_en_page("vertical", "flou", 1920, 1080)
        self.assertEqual((m.largeur, m.hauteur), (1080, 1920))
        x, y, l, h = m.image
        self.assertEqual((x, l, h), (0, 1080, 608))
        self.assertTrue(m.fond_flou)
        self.assertAlmostEqual(y + h / 2, 0.42 * 1920, delta=2)
        # paroles sous l'image, sur le fond flou, au-dessus des légendes des applications (bas de l'écran)
        self.assertEqual(m.alignement, 8)
        self.assertGreater(m.marge_v, y + h)
        self.assertLess(m.marge_v + 3 * m.taille, 0.8 * 1920)
        self.assertEqual((m.taille, m.taille_traduction), (67, 42))
        self.assertLess(m.mention_y, 0.1 * 1920)

    def test_vertical_deja_verticale_ou_recadree(self):
        for m in (es.mise_en_page("vertical", "flou", 1080, 1920), es.mise_en_page("vertical", "recadre", 1920, 1080)):
            self.assertEqual(m.image, (0, 0, 1080, 1920))
            self.assertFalse(m.fond_flou)
            self.assertEqual(m.alignement, 2)          # sur l'image, le bas aux trois quarts de la hauteur
            self.assertAlmostEqual(m.marge_v, 0.235 * 1920, delta=1)

    def test_carre(self):
        m = es.mise_en_page("carre", "flou", 1280, 720)
        self.assertEqual((m.largeur, m.hauteur), (1080, 1080))
        self.assertEqual(m.image[2:], (1080, 608))
        self.assertEqual(m.alignement, 8)
        m = es.mise_en_page("carre", "recadre", 1280, 720)
        self.assertEqual((m.image, m.alignement), ((0, 0, 1080, 1080), 2))

    def test_format_d_origine(self):
        self.assertEqual(es.taille_origine(3840, 2160), (1920, 1080))
        self.assertEqual(es.taille_origine(1281, 721), (1280, 720))       # pair
        self.assertEqual(es.taille_origine(720, 1280), (720, 1280))
        m = es.mise_en_page("origine", "flou", 1280, 720)
        self.assertEqual((m.largeur, m.hauteur, m.fond_flou, m.alignement), (1280, 720, False, 2))
        self.assertEqual(m.taille, round(0.062 * 720))


# --- Sous-titres ASS -----------------------------------------------------------------------------------------------

class EssaiASS(unittest.TestCase):
    def test_couleurs_et_temps(self):
        self.assertEqual(es.couleur_ass("#f6d32d"), "&H002DD3F6")
        self.assertEqual(es.couleur_ass("#000000", 0.5), "&H80000000")
        self.assertEqual(es.temps_ass(3723.456), "1:02:03.46")
        self.assertEqual(es.temps_ass(-1), "0:00:00.00")
        self.assertEqual(es.echapper_ass("a{b}c\\N"), "a(b)c∖N")

    def test_memes_lignes_a_l_ecran_que_le_karaoke_du_lecteur(self):
        lignes = [ligne(1.0, 3.0, "uno dos"), ligne(3.5, 6.0, "tres cuatro"), ligne(12.0, 14.0, "cinco seis"),
                  ligne(13.5, 16.0, "siete ocho")]
        fen = es.fenetres(lignes)
        for k in range(0, 200):
            t = k * 0.1
            _p, focale, _s, _a = P.voisines(lignes, t)
            montree = next((i for a, b, i in fen if a <= t < b), None)
            self.assertEqual(montree, focale, f"t = {t:.1f}")

    def test_mots_remplis_au_fil_du_chant(self):
        l = ligne(10.0, 13.0, "¿Dónde estás, amor?", [(10.5, 11.0, "Dónde"), (11.2, 11.8, "estás"),
                                                         (12.0, 12.9, "amor")])
        s = syllabes(es.karaoke(l, 9.0))
        # attente de 1,5 s avant le premier mot, qui porte le « ¿ » de tête
        self.assertEqual(s[0], ("k", 150, ""))
        self.assertEqual(s[1], ("kf", 50, "¿Dónde"))
        # l'espace après un mot prend sa couleur dès la fin du mot et porte l'attente jusqu'au suivant
        self.assertEqual(s[2], ("k", 20, " "))
        self.assertEqual(s[3], ("kf", 60, "estás"))
        self.assertEqual(s[4], ("k", 20, ", "))
        self.assertEqual(s[5], ("kf", 90, "amor"))
        self.assertEqual(s[6], ("k", 0, "?"))
        # chaque mot commence à son instant, sans dérive
        debut = 0
        for t, n, texte in s:
            if t == "kf":
                mot = next(m for m in l.mots if m.mot in texte)
                self.assertEqual(debut, round((mot.debut - 9.0) * 100))
            debut += n

    def test_ligne_commencee_avant_l_extrait(self):
        l = ligne(10.0, 13.0, "uno dos tres", [(10.0, 10.5, "uno"), (10.6, 11.5, "dos"), (11.6, 12.5, "tres")])
        s = syllabes(es.karaoke(l, 11.0))
        self.assertEqual(s[0], ("kf", 0, "uno"))          # déjà chanté : rempli d'emblée
        self.assertEqual(s[2], ("kf", 50, "dos"))         # en cours : sa fin
        self.assertEqual(sum(n for _t, n, _s in s[:4]), 60)

    def test_ligne_sans_mots_en_blanc(self):
        texte = es.karaoke(P.Ligne(1, 2, "sous-titre {brut}"), 0.5)
        self.assertEqual(texte, "{\\1c&HFFFFFF&}sous-titre (brut)")

    def test_fichier_ass(self):
        mise = es.mise_en_page("vertical", "flou", 1280, 720)
        lignes = [ligne(5.0, 8.0, "antes del extracto"), ligne(9.0, 12.0, "cruza el inicio"),
                  ligne(14.0, 17.0, "dentro del extracto"), ligne(40.5, 42.0, "despues del final")]
        trad = couche([P.Ligne(9.0, 12.0, "traverse le début"), P.Ligne(14.0, 17.0, "dans l’extrait")], "fr")
        texte = es.texte_ass(mise, couche(lignes), trad, debut_p=10.0, duree=30.0, mention="Voix générée par IA")
        self.assertIn("PlayResX: 1080", texte)
        self.assertIn("PlayResY: 1920", texte)
        evts = [l for l in texte.splitlines() if l.startswith("Dialogue:")]
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:30.00,Mention,,0,0,0,,Voix générée par IA", evts)
        paroles_evts = [e for e in evts if ",Paroles," in e]
        self.assertEqual(len(paroles_evts), 2)            # avant l'extrait et après sa fin : absentes
        # la ligne qui chevauche le début de l'extrait part à 0, la suivante dès la fin de la précédente (12 s → 2 s),
        # moins de 3 s avant son début
        self.assertTrue(paroles_evts[0].startswith("Dialogue: 1,0:00:00.00,0:00:02.00,Paroles"))
        self.assertTrue(paroles_evts[1].startswith("Dialogue: 1,0:00:02.00,0:00:07.00,Paroles"))
        self.assertIn("\\N{\\rTraduction}traverse le début", paroles_evts[0])
        self.assertIn("\\N{\\rTraduction}dans l’extrait", paroles_evts[1])
        # sans mention ni traduction
        texte = es.texte_ass(mise, couche(lignes), None, 10.0, 30.0, None)
        self.assertNotIn(",Mention,,", texte)
        self.assertNotIn("Traduction}", texte)

    def test_instant_d_apercu_au_milieu_d_un_mot(self):
        c = couche([ligne(0.5, 2.0, "trop tot pour"), ligne(5.0, 8.0, "uno dos tres cuatro")])
        t = es.instant_apercu(c, 0.0, 30.0)
        mot = c.lignes[1].mots[2]
        self.assertAlmostEqual(t, (mot.debut + mot.fin) / 2, places=3)
        self.assertAlmostEqual(es.instant_apercu(None, 0.0, 30.0), 12.0)


# --- Pistes, image, commandes ----------------------------------------------------------------------------------------

def resultat(tmp, rapport=None, video=True, mp3=True, fichiers=(), autotune=False):
    d = Path(tmp) / "donnees" / "sorties" / "Chanson inventée"
    tv = d / "travail" / "jules"
    tv.mkdir(parents=True, exist_ok=True)
    base = "Chanson inventée - voix Jules"
    if video:
        (d / f"{base}.mp4").write_bytes(b"")
    if mp3:
        (d / f"{base}.mp3").write_bytes(b"")
    if autotune:
        (d / f"{base} (auto-tune).mp4").write_bytes(b"")
    for f in fichiers:
        (d / "travail" / f).write_bytes(b"")
    (tv / "rapport.json").write_text(json.dumps(rapport or {"mode": "chant"}))
    return rapports.lire_dossier(d, {"jules": "Jules"})[0]


class EssaiChoixDuSon(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="voixla-essai-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def test_pistes_de_la_video_a_cinq_pistes(self):
        r = resultat(self.tmp, {"mode": "chant", "pistes_audio": {"video": fp.TITRES_PISTES,
                                                                  "video_autotune": fp.TITRES_PISTES},
                                "autotune": {"applique": True}}, autotune=True)
        s = es.choisir_son(r, "doublage", sonder=False)
        self.assertEqual((s.fichier, s.piste_audio), (r.video, 0))
        s = es.choisir_son(r, "beat", sonder=False)
        self.assertEqual((s.fichier, s.piste_audio, s.libelle), (r.video, 2, "Beat seul"))
        s = es.choisir_son(r, "ma_voix", sonder=False)
        self.assertEqual((s.fichier, s.piste_audio), (r.video, 3))
        s = es.choisir_son(r, "autotune", sonder=False)
        self.assertEqual((s.fichier, s.piste_audio), (r.video_autotune, 0))
        self.assertEqual(es.pistes_possibles(r), {"doublage": "", "autotune": "", "beat": "", "ma_voix": ""})

    def test_calage_de_la_video_doublee(self):
        r = resultat(self.tmp, {"mode": "chant", "pistes_audio": {"video": fp.TITRES_PISTES}})
        (r.dossier_voix / "paroles.json").write_text(json.dumps(fp.paroles(30.0, decalage_s=0.24)))
        s = es.choisir_son(r, "beat", sonder=False)
        self.assertAlmostEqual(s.avance, 0.24)        # le son commence à decalage_s dans la vidéo doublée

    def test_anciens_doublages_a_une_piste(self):
        r = resultat(self.tmp, {"mode": "chant", "pistes_audio": {"video": ["Doublage"]}},
                     fichiers=("instrumental.wav",))
        s = es.choisir_son(r, "beat", sonder=False)
        self.assertEqual((s.fichier.name, s.piste_audio, s.avance), ("instrumental.wav", 0, 0.0))
        with self.assertRaises(es.ErreurExport):
            es.choisir_son(r, "autotune", sonder=False)
        possibles = es.pistes_possibles(r)
        self.assertEqual(possibles["beat"], "")
        self.assertTrue(possibles["autotune"])
        self.assertTrue(possibles["ma_voix"])           # ni piste ni voix sèche à combiner

    def test_mp3_seul(self):
        r = resultat(self.tmp, video=False)
        s = es.choisir_son(r, "doublage", sonder=False)
        self.assertEqual((s.fichier, s.avance), (r.mp3, 0.0))
        with self.assertRaises(es.ErreurExport):
            es.choisir_son(r, "beat", sonder=False)

    def test_image_d_un_fichier(self):
        info = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001",
                             "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}]}
        im = es.image_de(info)
        self.assertEqual((im.genre, im.largeur, im.hauteur, im.rotation), ("video", 1080, 1920, 270))
        self.assertAlmostEqual(im.cadence, 29.97, places=2)
        im = es.image_de({"streams": [{"codec_type": "video", "width": 720, "height": 576,
                                       "sample_aspect_ratio": "16:11", "r_frame_rate": "25/1"}]})
        self.assertEqual((im.largeur, im.hauteur), (1047, 576))
        im = es.image_de({"streams": [{"codec_type": "audio"},
                                      {"codec_type": "video", "width": 500, "height": 500,
                                       "disposition": {"attached_pic": 1}}]})
        self.assertEqual((im.genre, im.pochette), ("pochette", 0))
        self.assertIsNone(es.image_de({"streams": [{"codec_type": "audio"}]}))


class EssaiCommandes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="voixla-essai-")
        self.addCleanup(self._tmp.cleanup)
        self.res = resultat(self._tmp.name, {"mode": "chant", "pistes_audio": {"video": fp.TITRES_PISTES}},
                            fichiers=("instrumental.wav",))

    def plan(self, **k):
        r = es.Reglages(debut=20.0, duree=30.0, **k)
        son = es.choisir_son(self.res, r.piste, sonder=False)
        image = es.Image("video", self.res.video, 0.0, 1280, 720, 25.0)
        return es.Plan(self.res, r, son, image, es.mise_en_page(r.format, r.cadrage, 1280, 720), titre="Chanson")

    def test_export_h264_aac_faststart(self):
        cmd = es.commande(self.plan(piste="beat"), "/tmp/x.mp4", "/tmp/p.ass")
        texte = " ".join(cmd)
        for attendu in ("-c:v libx264", "-profile:v high", "-pix_fmt yuv420p", "-maxrate 5M", "-c:a aac",
                        "-b:a 192k", "-movflags +faststart", "-t 30.000", "-f mp4"):
            self.assertIn(attendu, texte)
        self.assertEqual(cmd[:3], ["nice", "-n", "10"])
        # une seule entrée : l'image et la piste du beat (a:2) de la vidéo doublée, au début de l'extrait
        self.assertEqual(cmd.count("-i"), 1)
        self.assertIn("-ss 20.000 -t 30.000 -i", texte)
        graphe = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[0:a:2]", graphe)
        self.assertIn("boxblur", graphe)
        self.assertIn("overlay=0:", graphe)
        self.assertIn("ass=filename=/tmp/p.ass", graphe)
        self.assertIn("fade=t=in:st=0:d=0.5", graphe)
        self.assertIn("afade=t=out:st=29.000:d=1", graphe)
        self.assertIn("-metadata", cmd)
        self.assertIn("comment=Voix générée par IA (Voix-là)", cmd)

    def test_son_d_un_autre_fichier_recadrage_sans_fondu(self):
        self.res.rapport["pistes_audio"]["video"] = ["Doublage"]       # ancien doublage : le beat est un WAV
        cmd = es.commande(self.plan(piste="beat", cadrage="recadre", fondu=False, mention=False), "x.mp4", "p.ass")
        self.assertEqual(cmd.count("-i"), 2)
        graphe = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[1:a:0]", graphe)
        self.assertIn("crop=1080:1920", graphe)
        self.assertNotIn("boxblur", graphe)
        self.assertNotIn("fade", graphe)
        self.assertNotIn("comment=Voix générée par IA (Voix-là)", cmd)

    def test_apercu_une_image(self):
        cmd = es.commande(self.plan(), "a.png", "p.ass", apercu=7.25)
        self.assertIn("-ss 27.250 -t 0.500 -i", " ".join(cmd))
        graphe = cmd[cmd.index("-filter_complex") + 1]
        self.assertTrue(graphe.startswith("[0:v:0]setpts=PTS-STARTPTS+7.250/TB"))
        self.assertNotIn("[a]", graphe)
        self.assertEqual(cmd[-7:], ["-frames:v", "1", "-f", "image2", "-c:v", "png", "a.png"])

    def test_cadence_plafonnee_a_30(self):
        p = self.plan()
        p.image.cadence = 59.94
        self.assertIn("fps=30", es.graphe_image(p, "p.ass"))
        p.image.cadence = 25.0
        self.assertNotIn("fps=", es.graphe_image(p, "p.ass"))

    def test_commande_de_l_interface_et_file_attente(self):
        r = es.Reglages(debut=12.5, duree=30, piste="beat", traduction=True, texte_mention="-IA-")
        cmd = es.commande_interface("/usr/bin/python3", self.res, r, "/x/s.mp4", "/bin/file-attente")
        self.assertEqual(cmd[:5], ["/bin/file-attente", "voix-la export", "1", "2", "--"])
        self.assertIn("--mention=-IA-", cmd)          # un texte qui commence par « - » reste une valeur
        for a in ("--traduction", "--piste", "beat", "--voix", "jules", "--sortie", "/x/s.mp4"):
            self.assertIn(a, cmd)
        r = es.Reglages(duree=4, format="origine", mention=False, paroles=False, fondu=False)
        self.assertLess(es.estimation_calcul_s(r, es.mise_en_page("origine", "flou", 320, 180)), es.SEUIL_FILE_S)
        cmd = es.commande_interface("python3", self.res, r, "s.mp4", None)
        self.assertEqual(cmd[:4], ["nice", "-n", "10", "python3"])
        for a in ("--sans-mention", "--sans-paroles", "--sans-fondu"):
            self.assertIn(a, cmd)

    def test_echappement_d_un_chemin_dans_le_graphe(self):
        self.assertEqual(es.echapper_filtre("/a b/c:d'e,[f];g\\h"),
                         "/a b/c\\\\:d\\\\\\'e\\,\\[f\\]\\;g\\\\\\\\h")

    @unittest.skipUnless(FFMPEG, "ffmpeg absent")
    def test_ffmpeg_lit_les_sous_titres_d_un_chemin_difficile(self):
        d = Path(self._tmp.name) / "dossier: l'essai, [1]; ok"
        d.mkdir()
        ass = d / "p.ass"
        mise = es.mise_en_page("carre", "recadre", 0, 0)
        ass.write_text(es.texte_ass(mise, None, None, 0.0, 2.0, "Voix générée par IA"), encoding="utf-8")
        sortie = d / "image.png"
        r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "color=s=320x320:d=1",
                            "-vf", f"ass=filename={es.echapper_filtre(ass)}", "-frames:v", "1", str(sortie)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(sortie.is_file())


# --- Noms et corbeille -----------------------------------------------------------------------------------------------

class EssaiSortie(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="voixla-essai-")
        self.addCleanup(self._tmp.cleanup)
        self.res = resultat(self._tmp.name)

    def test_nom_et_dossier(self):
        r = es.Reglages(debut=65.0, duree=30.0)
        self.assertEqual(es.nom_sortie(self.res, r), "Chanson inventée - voix Jules - partage 1m05-1m35 9x16.mp4")
        r = es.Reglages(debut=5, duree=20, format="carre", piste="beat")
        self.assertEqual(es.nom_sortie(self.res, r), "Chanson inventée - voix Jules - partage 0m05-0m25 1x1 (beat).mp4")
        self.assertEqual(es.dossier_partage(self.res), self.res.dossier / "partage")
        f = es.sortie_par_defaut(self.res, r)
        self.assertEqual(f.parent, self.res.dossier / "partage")
        f.parent.mkdir()
        f.write_bytes(b"x")
        self.assertEqual(es.sortie_par_defaut(self.res, r).name, f"{f.stem} (2).mp4")

    def test_les_videos_a_partager_partent_avec_leur_voix(self):
        d = self.res.dossier
        (d / "travail" / "marie").mkdir()
        (d / "Chanson inventée - voix Marie.mp4").write_bytes(b"")
        partage = d / "partage"
        partage.mkdir()
        a = partage / "Chanson inventée - voix Jules - partage 0m05-0m35 9x16.mp4"
        b = partage / "Chanson inventée - voix Marie - partage 0m05-0m35 9x16.mp4"
        for f in (a, b, partage / "autre.mp4"):
            f.write_bytes(b"")
        self.assertEqual(es.fichiers_partages(self.res), [a])
        plan, = corbeille.plans([self.res])
        self.assertFalse(plan.tout_le_dossier)
        self.assertIn(a, plan.fichiers)
        self.assertNotIn(b, plan.fichiers)
        # le dossier des sorties ne voit pas partage/ comme un doublage
        self.assertEqual(len(rapports.lire_dossier(d)), 2)


# --- De bout en bout -------------------------------------------------------------------------------------------------

def sonder(f):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(f)],
                       capture_output=True, text=True, timeout=60, check=True)
    return json.loads(r.stdout)


def frequence(f, carte="0:a:0"):
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(f), "-map", carte, "-ac", "1", "-ar", "44100",
                        "-f", "f32le", "-"], capture_output=True, check=True, timeout=60)
    x = np.frombuffer(r.stdout, np.float32)[44100:44100 * 3]
    spectre = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / 44100)[int(np.argmax(spectre))])


def pixels_jaunes(png, zone=None):
    """Nombre de pixels proches du jaune du karaoké (#f6d32d) dans l'image (zone : (y0, y1) en fraction de la
    hauteur)."""
    import numpy as np
    info = sonder(png)["streams"][0]
    l, h = int(info["width"]), int(info["height"])
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(png), "-f", "rawvideo", "-pix_fmt", "rgb24",
                        "-"], capture_output=True, check=True, timeout=60)
    a = np.frombuffer(r.stdout, np.uint8).reshape(h, l, 3).astype(int)
    if zone:
        a = a[int(zone[0] * h):int(zone[1] * h)]
    return int(((a[..., 0] > 200) & (a[..., 1] > 165) & (a[..., 1] < 235) & (a[..., 2] < 110)).sum())


def atomes(f):
    """Les atomes de premier niveau d'un mp4, dans l'ordre (moov avant mdat : « faststart »)."""
    noms, data = [], Path(f).read_bytes()
    i = 0
    while i + 8 <= len(data):
        taille = int.from_bytes(data[i:i + 4], "big")
        noms.append(data[i + 4:i + 8].decode("latin-1"))
        if taille == 1:
            taille = int.from_bytes(data[i + 8:i + 16], "big")
        if taille < 8:
            break
        i += taille
    return noms


@unittest.skipUnless(FFMPEG, "ffmpeg absent")
class EssaiDeBoutEnBout(EssaiIsole):
    """Un doublage inventé de 24 s (320 × 180, cinq pistes, paroles avec traduction), fait une fois pour la classe."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._donnees = tempfile.TemporaryDirectory(prefix="voixla-essai-partage-")
        cls.dossier = fp.creer(Path(cls._donnees.name) / "donnees", duree=24.0, taille=(320, 180))

    @classmethod
    def tearDownClass(cls):
        cls._donnees.cleanup()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        os.environ["VOIXLA_CACHE"] = str(self.tmp / "cache")
        self.res = es.trouver_resultat(self.dossier)
        self.addCleanup(shutil.rmtree, self.dossier / "partage", True)
        rustine = mock.patch.object(es, "X264", RAPIDE)
        rustine.start()
        self.addCleanup(rustine.stop)

    def test_video_a_partager(self):
        av = []
        r = es.Reglages(debut=3.5, duree=15.0, piste="beat", traduction=True)
        info = es.exporter(self.res, r, avancement=av.append)
        f = Path(info["sortie"])
        self.assertEqual(f.parent, self.dossier / "partage")
        self.assertEqual(f.name, "Lumière du soir - voix Jules - partage 0m03-0m19 9x16 (beat).mp4")
        self.assertEqual((info["largeur"], info["hauteur"], info["duree_s"]), (1080, 1920, 15.0))
        self.assertTrue(av and av[-1] == 1.0)
        d = sonder(f)
        v = next(s for s in d["streams"] if s["codec_type"] == "video")
        a = next(s for s in d["streams"] if s["codec_type"] == "audio")
        self.assertEqual((v["codec_name"], v["profile"], v["pix_fmt"], v["width"], v["height"]),
                         ("h264", "High", "yuv420p", 1080, 1920))
        self.assertEqual((a["codec_name"], int(a["channels"])), ("aac", 2))
        self.assertAlmostEqual(float(d["format"]["duration"]), 15.0, delta=0.1)
        self.assertLess(f.stat().st_size, es.taille_max_mo(15) * 1e6)
        n = atomes(f)
        self.assertLess(n.index("moov"), n.index("mdat"))         # lisible avant d'être tout téléchargé
        self.assertAlmostEqual(frequence(f), fp.FREQUENCES["beat"], delta=3)
        self.assertEqual(d["format"]["tags"].get("comment"), "Voix générée par IA (Voix-là)")
        self.assertEqual([x.name for x in f.parent.iterdir()], [f.name])     # pas de fichier temporaire laissé

    def test_doublage_auto_tune_et_carre(self):
        r = es.Reglages(debut=0.0, duree=15.0, piste="autotune", format="carre", mention=False)
        f = Path(es.exporter(self.res, r)["sortie"])
        v = next(s for s in sonder(f)["streams"] if s["codec_type"] == "video")
        self.assertEqual((v["width"], v["height"]), (1080, 1080))
        self.assertAlmostEqual(frequence(f), fp.FREQUENCES["doublage_at"], delta=3)

    def test_mot_en_cours_de_remplissage_sur_l_image(self):
        r = es.Reglages(debut=3.5, duree=15.0)
        png = self.tmp / "apercu.png"
        f, t = es.apercu(self.res, r, png)
        p = es.preparer_plan(self.res, r)
        mot = next(m for l in p.paroles.originale.lignes for m in l.mots if m.debut <= t + r.debut < m.fin)
        self.assertTrue(mot.debut < t + 3.5 < mot.fin)
        self.assertGreater(pixels_jaunes(f, (0.55, 0.8)), 500)     # paroles sous l'image, mot rempli en jaune
        # 3 s avant la première ligne, elle est déjà là mais rien n'est chanté : pas de jaune
        f2, _t = es.apercu(self.res, es.Reglages(debut=1.0, duree=15.0), self.tmp / "avant.png", instant=1.2)
        self.assertEqual(pixels_jaunes(f2, (0.55, 0.8)), 0)
        self.assertEqual(png.parent, f.parent)

    def test_ligne_de_commande_ass_proposer_apercu(self):
        env = {**os.environ, "PYTHONPATH": str(INTERFACE)}
        r = subprocess.run([sys.executable, "-m", "voixla.export_social", str(self.dossier), "--proposer"],
                           capture_output=True, text=True, env=env, timeout=120, cwd=INTERFACE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["raison"], "passage le plus chanté")
        ass = self.tmp / "p.ass"
        r = subprocess.run([sys.executable, "-m", "voixla.export_social", str(self.dossier), "--debut", "0:03,5",
                            "--fin", "0:20", "--traduction", "--ass", str(ass)], capture_output=True, text=True,
                           env=env, timeout=120, cwd=INTERFACE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["duree_s"], 16.5)
        self.assertIn("Lueur de l’aube", ass.read_text(encoding="utf-8"))
        r = subprocess.run([sys.executable, "-m", "voixla.export_social", str(self.dossier), "--debut", "x"],
                           capture_output=True, text=True, env=env, timeout=120, cwd=INTERFACE)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--debut : temps illisible", r.stderr)

    def test_annulation(self):
        env = {**os.environ, "PYTHONPATH": str(INTERFACE), "VOIXLA_AVANCEMENT": "1"}
        p = subprocess.Popen([sys.executable, "-m", "voixla.export_social", str(self.dossier), "--debut", "0",
                              "--duree", "20"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                             cwd=INTERFACE, start_new_session=True)
        try:
            ligne1 = p.stdout.readline()          # le premier avancement : ffmpeg encode
            self.assertIn("avancement", ligne1)
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=30)
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
            p.stdout.close()
            p.stderr.close()
        self.assertIn(p.returncode, (130, -signal.SIGTERM))
        partage = self.dossier / "partage"
        restes = list(partage.iterdir()) if partage.is_dir() else []
        self.assertEqual(restes, [])              # ni vidéo à moitié faite ni fichier temporaire
        self.assertEqual(list((self.tmp / "cache" / "partage").glob("export-*")), [])


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
class EssaiDialogue(EssaiIsole):
    """Le dialogue « Partager un extrait » sous Xvfb, sur un bus privé, son coupé (tests/essai_partage_gui.py) : boucle
    A-B, mention, pistes, proposition, aperçu, « Écouter », export imité et annulation, préférences, menu « ⋮ »."""

    def test_dialogue(self):
        import contextlib
        os.environ["VOIXLA_CACHE"] = str(self.tmp / "cache")
        fp.creer(self.donnees, duree=75.0, taille=(160, 90))
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
               "tests.essai_partage_gui"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                             cwd=str(INTERFACE), stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            sortie, erreur = p.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            sortie, erreur = p.communicate()
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGKILL)
        mesures = {}
        for l in sortie.splitlines():
            with contextlib.suppress(ValueError):
                mesures.update(json.loads(l))
        trace = sortie[-3000:] + erreur[-3000:]
        self.assertEqual(p.returncode, 0, trace)
        self.assertEqual(mesures.get("erreurs"), [], trace)
        self.assertTrue(mesures.get("fini"), trace)


if __name__ == "__main__":
    unittest.main()
