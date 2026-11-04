"""La lecture dans toute l'application, sans GTK (voixla/lecture.py) : un seul lecteur à la fois (coordinateur), ce
qu'on montre de la lecture en cours, la reprise sur la vidéo après le son seul, et les règles de vie de l'application
quand on ferme sa fenêtre."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from voixla import lecture as regles
from voixla import pistes

from .test_pistes import RAPPORT_CHANT, EssaiAvecCache


class Proprio:
    """Un propriétaire de lecture factice : note ce qu'on lui demande."""

    def __init__(self, nom, durable=False):
        self.nom, self.durable = nom, durable
        self.appels = []

    def couper(self):
        self.appels.append("couper")

    def quitter_session(self):
        self.appels.append("quitter_session")

    def __repr__(self):
        return f"<{self.nom}>"


class EssaiCoordinateur(unittest.TestCase):
    def setUp(self):
        self.c = regles.Coordinateur()
        self.vus = []
        self.c.observateurs.append(lambda: self.vus.append((self.c.actif, self.c.session)))

    def test_une_lecture_ailleurs_coupe_la_precedente(self):
        a, b = Proprio("résultat A", durable=True), Proprio("résultat B", durable=True)
        self.assertIsNone(self.c.commence(a))
        self.assertIs(self.c.session, a)
        self.assertIs(self.c.commence(b), a)
        self.assertEqual(a.appels, ["couper", "quitter_session"])
        self.assertIs(self.c.actif, b)
        self.assertIs(self.c.session, b)
        self.assertEqual(b.appels, [])

    def test_bascule_dans_le_meme_lecteur_ne_coupe_rien(self):
        # changer de piste (A/B, beat, voix) : le nouveau flux du même propriétaire se met à jouer
        a = Proprio("résultat", durable=True)
        self.c.commence(a)
        n = len(self.vus)
        self.assertIsNone(self.c.commence(a))
        self.assertIsNone(self.c.commence(a))
        self.assertEqual(a.appels, [])
        self.assertEqual(len(self.vus), n, "aucun changement à signaler")

    def test_une_preecoute_coupe_la_session_sans_la_remplacer(self):
        musique = Proprio("résultat", durable=True)
        voix = Proprio("préécoute d'une voix")
        self.c.commence(musique)
        self.assertIs(self.c.commence(voix), musique)
        self.assertEqual(musique.appels, ["couper"])
        self.assertIs(self.c.actif, voix)
        self.assertIs(self.c.session, musique, "la musique en pause reste dans la barre et MPRIS")
        # la musique reprend : c'est la préécoute qui est coupée
        self.assertIs(self.c.commence(musique), voix)
        self.assertEqual(voix.appels, ["couper"])
        self.assertEqual(musique.appels, ["couper"], "reprendre sa propre session ne la fait pas quitter")

    def test_nouvelle_session_apres_une_preecoute(self):
        a, b = Proprio("résultat A", durable=True), Proprio("écoute à l'aveugle", durable=True)
        apercu = Proprio("prise de chant")
        self.c.commence(a)
        self.c.commence(apercu)
        self.c.commence(b)
        # a était déjà coupé par la préécoute : coupé encore (sans effet), puis remplacé
        self.assertEqual(a.appels, ["couper", "couper", "quitter_session"])
        self.assertEqual(apercu.appels, ["couper"])
        self.assertIs(self.c.session, b)

    def test_termine(self):
        a = Proprio("résultat", durable=True)
        self.c.commence(a)
        self.assertTrue(self.c.termine(a))
        self.assertIsNone(self.c.actif)
        self.assertIsNone(self.c.session)
        self.assertFalse(self.c.termine(a), "deux fois : rien de plus")
        self.assertFalse(self.c.termine(None))
        self.assertEqual(self.vus[-1], (None, None))

    def test_termine_une_preecoute_garde_la_session(self):
        a, p = Proprio("résultat", durable=True), Proprio("préécoute")
        self.c.commence(a)
        self.c.commence(p)
        self.assertTrue(self.c.termine(p))
        self.assertIsNone(self.c.actif)
        self.assertIs(self.c.session, a)

    def test_sans_proprietaire(self):
        self.assertIsNone(self.c.commence(None))
        self.assertEqual(self.vus, [])

    def test_proprietaire_sans_methodes(self):
        # un propriétaire minimal (sans quitter_session) est accepté
        a = SimpleNamespace(durable=True, couper=lambda: None)
        b = SimpleNamespace(durable=True)
        self.c.commence(a)
        self.c.commence(b)
        self.assertIs(self.c.session, b)


class EssaiEtat(unittest.TestCase):
    def test_sans_session(self):
        e = regles.Etat()
        self.assertFalse(e.session)
        self.assertEqual(e.statut, regles.ARRET)

    def test_lignes(self):
        e = regles.Etat(statut=regles.LECTURE, position_s=83.4, duree_s=225.0, titre="Chanson", voix="Jules",
                        piste="Beat seul", genre="resultat", cle=("resultat", "/s/Chanson", "jules"))
        self.assertTrue(e.session)
        self.assertEqual(regles.ligne_detail(e), "voix Jules · Beat seul")
        self.assertEqual(regles.ligne_position(e), "1:23 / 3:45")
        self.assertAlmostEqual(regles.fraction(e), 83.4 / 225.0)

    def test_ecoute_a_l_aveugle(self):
        # la lettre seulement : jamais le nom de la version avant le vote (c'est la page qui le décide)
        e = regles.Etat(statut=regles.PAUSE, position_s=5, titre="Refrain", piste="B — mix", genre="ecoute")
        self.assertEqual(regles.ligne_detail(e), "Écoute à l’aveugle · B — mix")
        self.assertEqual(regles.ligne_position(e), "0:05")
        self.assertEqual(regles.fraction(e), 0.0)

    def test_fin(self):
        e = regles.Etat(statut=regles.ARRET, position_s=224.9, duree_s=225.0, genre="resultat")
        self.assertEqual(regles.ligne_position(e), "fini")
        self.assertEqual(regles.fraction(regles.Etat(position_s=300, duree_s=200)), 1.0)

    def test_saut(self):
        self.assertFalse(regles.saut(10.0, 11.0, 1.0, True), "une seconde de lecture")
        self.assertFalse(regles.saut(10.0, 10.0, 1.0, False), "en pause")
        self.assertTrue(regles.saut(10.0, 60.0, 1.0, True), "déplacement en avant")
        self.assertTrue(regles.saut(60.0, 10.0, 1.0, True), "retour en arrière")
        self.assertTrue(regles.saut(10.0, 13.0, 1.0, False), "déplacement pendant la pause")
        self.assertFalse(regles.saut(None, 10.0, 1.0, True))


class EssaiReprise(unittest.TestCase):
    def pistes_son(self, **fichiers):
        return {c: SimpleNamespace(fichier=f, decalage=0.0, libelle=pistes.LIBELLES[c]) for c, f in fichiers.items()}

    def test_source_son(self):
        p = self.pistes_son(doublage=Path("/s/Chanson - voix Jules.mp3"), beat=None)
        self.assertEqual(regles.source_son("doublage", p), (Path("/s/Chanson - voix Jules.mp3"), 0.0, "Doublage"))
        self.assertIsNone(regles.source_son("beat", p), "pas de fichier sans image : la vidéo reste")
        self.assertIsNone(regles.source_son("ma_voix", p), "piste absente")
        self.assertIsNone(regles.source_son("doublage", {}))

    def test_deja_le_son(self):
        f = Path("/s/travail/instrumental.wav")
        p = self.pistes_son(beat=f)
        self.assertIsNone(regles.source_son("beat", p, (f, 0.0, "Beat seul")))
        self.assertIsNotNone(regles.source_son("beat", p, (Path("/cache/videos/x.mp4"), 0.0, "Beat seul")))

    def test_mise_a_jour(self):
        r = regles.Reprise("beat", ("/s/travail/instrumental.wav", 0.0, "Beat seul"), sans_image=True)
        # la copie vidéo du beat est prête pendant qu'on écoute le son seul fenêtre fermée : on y reviendra
        r2 = r.mise_a_jour(("/cache/videos/beat.mp4", 0.0, "Beat seul"), sans_image=False, ephemere=True)
        self.assertEqual((r2.cle, r2.source[0], r2.sans_image, r2.ephemere),
                         ("beat", "/cache/videos/beat.mp4", False, True))
        self.assertEqual(r.source[0], "/s/travail/instrumental.wav", "l'ancienne reprise n'est pas modifiée")


class EssaiReprisePistes(EssaiAvecCache):
    """Le son de même contenu, pris dans les vraies pistes d'un doublage (forme « mp3 »)."""

    def test_par_piste(self):
        r = self.doublage(fichiers=["original.wav", "instrumental.wav", "voix_source.wav"], rapport=RAPPORT_CHANT)
        son = pistes.choisir_pistes(r, "mp3")
        video = pistes.choisir_pistes(r, "video")
        self.assertEqual(regles.source_son("doublage", son, (video["doublage"].a_lire, 0.0, "Doublage"))[0], r.mp3)
        o = regles.source_son("original", son, (video["original"].a_lire, video["original"].decalage, "Original"))
        self.assertEqual(o, (r.travail / "original.wav", 0.0, "Original"),
                         "la vidéo d'origine décalée → le son d'origine extrait, calé sur le doublage")
        self.assertEqual(regles.source_son("beat", son, (Path("/cache/videos/beat.mp4"), 0.0, "Beat seul"))[0],
                         r.travail / "instrumental.wav")

    def test_sans_mp3_la_video_reste(self):
        r = self.doublage(fichiers=["original.wav"], rapport=RAPPORT_CHANT, mp3=False)
        self.assertIsNone(regles.source_son("doublage", pistes.choisir_pistes(r, "mp3")))


class EssaiVie(unittest.TestCase):
    def test_a_la_fermeture(self):
        self.assertEqual(regles.a_la_fermeture(regles.LECTURE), "cacher")
        self.assertEqual(regles.a_la_fermeture(regles.PAUSE), "quitter", "en pause : on quitte comme avant")
        self.assertEqual(regles.a_la_fermeture(regles.ARRET), "quitter")

    def test_sans_fenetre(self):
        self.assertEqual(regles.sans_fenetre(regles.LECTURE), "rester")
        self.assertEqual(regles.sans_fenetre(regles.PAUSE, 10), "attendre")
        self.assertEqual(regles.sans_fenetre(regles.PAUSE, regles.DELAI_PAUSE_S + 1), "quitter")
        self.assertEqual(regles.sans_fenetre(regles.ARRET), "quitter", "fin ou stop")

    def test_fenetre_ouverte(self):
        v = regles.Vie()
        for statut in (regles.LECTURE, regles.PAUSE, regles.ARRET):
            self.assertEqual(v.decider(statut, 0.0), "rester", "fenêtre ouverte : on ne quitte jamais tout seul")

    def test_fermer_pendant_la_lecture_puis_fin(self):
        v = regles.Vie()
        self.assertEqual(v.fermer(regles.LECTURE), "cacher")
        self.assertFalse(v.fenetre_visible)
        self.assertEqual(v.decider(regles.LECTURE, 10.0), "rester")
        self.assertEqual(v.decider(regles.ARRET, 200.0), "quitter", "la lecture finit : l'application quitte")

    def test_fermer_sans_lecture(self):
        v = regles.Vie()
        self.assertEqual(v.fermer(regles.PAUSE), "quitter")
        self.assertTrue(v.fenetre_visible, "rien ne change : la fenêtre se ferme pour de bon")

    def test_pause_fenetre_cachee(self):
        v = regles.Vie(delai_s=60)
        v.fermer(regles.LECTURE)
        self.assertEqual(v.decider(regles.PAUSE, 100.0), "attendre")
        self.assertAlmostEqual(v.reste_pause(130.0), 30.0)
        self.assertEqual(v.decider(regles.PAUSE, 150.0), "attendre", "la pause compte depuis son début")
        self.assertEqual(v.decider(regles.LECTURE, 155.0), "rester", "lecture reprise (MPRIS)")
        self.assertIsNone(v.reste_pause(155.0))
        self.assertEqual(v.decider(regles.PAUSE, 160.0), "attendre", "nouvelle pause : le délai repart")
        self.assertEqual(v.decider(regles.PAUSE, 221.0), "quitter")

    def test_reouverture(self):
        v = regles.Vie(delai_s=60)
        v.fermer(regles.LECTURE)
        v.decider(regles.PAUSE, 0.0)
        v.ouvrir()
        self.assertTrue(v.fenetre_visible)
        self.assertIsNone(v.reste_pause(1000.0))
        self.assertEqual(v.decider(regles.ARRET, 1000.0), "rester", "fenêtre rouverte : la fin ne fait pas quitter")


if __name__ == "__main__":
    unittest.main()
