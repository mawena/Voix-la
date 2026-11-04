"""Mise à la corbeille de doublages (voixla/corbeille.py) : ce qui part avec une ou plusieurs voix, la confirmation,
la lecture à arrêter d'abord, les doublages en cours d'écriture, et l'envoi un par un (succès, fichier déjà absent,
échec). La corbeille est simulée : aucun essai ne touche à la vraie corbeille."""

import shutil
from pathlib import Path
from types import SimpleNamespace

from voixla import corbeille, pistes, rapports

from .test_pistes import RAPPORT_CHANT, EssaiAvecCache, ecrire

NOMS = {"jules": "Jules", "lea": "Léa"}


def jeter_pour_de_faux(journal, echec_sur=None):
    """Remplace commun.corbeille : efface le chemin (dans le dossier temporaire) ; échoue sur echec_sur."""
    def jeter(chemin, rappel):
        journal.append(Path(chemin))
        if echec_sur is not None and Path(chemin).name == echec_sur:
            rappel(False, "Permission non accordée")
            return
        p = Path(chemin)
        shutil.rmtree(p) if p.is_dir() else p.unlink()
        rappel(True, "")
    return jeter


class EssaiCorbeille(EssaiAvecCache):
    def deux_voix(self, titre="Chanson fictive"):
        self.doublage(titre=titre, voix="jules", nom="Jules", fichiers=["original.wav", "instrumental.wav"],
                      rapport=RAPPORT_CHANT)
        self.doublage(titre=titre, voix="lea", nom="Léa", rapport=RAPPORT_CHANT)
        rs = rapports.lire_dossier(self.donnees / "sorties" / titre, NOMS)
        return {r.voix: r for r in rs}

    def fini(self):
        r = []
        return r, lambda ok, message, faits: r.append((ok, message, faits))

    # --- ce qui part ------------------------------------------------------------------------------------------------

    def test_une_voix_tout_le_dossier(self):
        r = self.doublage(fichiers=["original.wav"], rapport=RAPPORT_CHANT)
        (p,) = corbeille.plans([r])
        self.assertTrue(p.tout_le_dossier)
        self.assertEqual(p.fichiers, [r.dossier])
        titre, corps, bouton = corbeille.confirmation([p])
        self.assertEqual(titre, "Mettre ce doublage à la corbeille ?")
        self.assertIn("« Chanson fictive » va à la corbeille, avec son travail/", corps)
        self.assertEqual(bouton, "Mettre à la corbeille")

    def test_plusieurs_voix_seulement_les_fichiers_de_la_voix(self):
        rs = self.deux_voix()
        ecrire(rs["jules"].dossier / pistes.nom_export(rs["jules"], "beat", ".mp3"))
        (p,) = corbeille.plans([rs["jules"]])
        self.assertFalse(p.tout_le_dossier)
        self.assertEqual(p.autres_voix, ["lea"])
        noms = [f.name for f in p.fichiers]
        self.assertEqual(noms, ["Chanson fictive - voix Jules.mp4", "Chanson fictive - voix Jules.mp3",
                                "Chanson fictive - voix Jules (beat).mp3", "jules"])
        self.assertNotIn(rs["jules"].dossier, p.fichiers)
        _titre, corps, _b = corbeille.confirmation([p])
        self.assertIn("contient aussi les voix lea : seuls les fichiers de la voix Jules", corps)
        self.assertIn("(beat).mp3", corps)

    def test_toutes_les_voix_choisies_tout_le_dossier(self):
        rs = self.deux_voix()
        (p,) = corbeille.plans([rs["jules"], rs["lea"]])
        self.assertTrue(p.tout_le_dossier, "rien ne reste : le dossier entier, travail/ compris")
        self.assertEqual(p.fichiers, [rs["jules"].dossier])
        titre, corps, bouton = corbeille.confirmation([p])
        self.assertEqual(titre, "Mettre ces 2 doublages à la corbeille ?")
        self.assertIn("• Chanson fictive (voix Jules, Léa)", corps)
        self.assertEqual(bouton, "Tout mettre à la corbeille")

    def test_selection_de_plusieurs_doublages(self):
        a = self.doublage(titre="Chanson fictive", rapport=RAPPORT_CHANT)
        rs = self.deux_voix("Duo fictif")
        liste = corbeille.plans([a, rs["lea"]])
        self.assertEqual([p.titre for p in liste], ["Chanson fictive", "Duo fictif"])
        titre, corps, _b = corbeille.confirmation(liste)
        self.assertEqual(titre, "Mettre ces 2 doublages à la corbeille ?")
        self.assertIn("• Chanson fictive (voix Jules)", corps)
        self.assertIn("• Duo fictif (voix Léa) : fichiers de cette voix seulement, le dossier garde jules", corps)

    # --- lecture en cours, doublage en cours d'écriture -------------------------------------------------------------

    def test_lecture_a_arreter(self):
        rs = self.deux_voix()
        seule = corbeille.plans([rs["jules"]])
        self.assertTrue(corbeille.concerne(seule, rs["jules"].dossier, "jules"), "celui qui joue part : on l'arrête")
        self.assertFalse(corbeille.concerne(seule, rs["lea"].dossier, "lea"), "l'autre voix du dossier reste")
        tout = corbeille.plans([rs["jules"], rs["lea"]])
        self.assertTrue(corbeille.concerne(tout, rs["lea"].dossier, "lea"))
        self.assertFalse(corbeille.concerne(tout, self.donnees / "sorties" / "Autre", "jules"))

    def test_doublage_en_cours_d_ecriture(self):
        r = self.doublage(rapport=RAPPORT_CHANT)
        liste = corbeille.plans([r])
        actif = SimpleNamespace(actif=True, demande={"sorties": [str(r.dossier)]})
        fini_ = SimpleNamespace(actif=False, demande={"sorties": [str(r.dossier)]})
        self.assertEqual(corbeille.occupes(liste, [fini_]), [])
        self.assertEqual(corbeille.occupes(liste, [fini_, actif]), ["Chanson fictive"])
        self.assertEqual(corbeille.occupes(liste, [SimpleNamespace(actif=True, demande={})]), [])

    # --- envoi --------------------------------------------------------------------------------------------------

    def test_envoi_dossier_entier(self):
        r = self.doublage(fichiers=["original.wav"], rapport=RAPPORT_CHANT)
        journal, (vus, fini) = [], self.fini()
        corbeille.enchainer(corbeille.plans([r]), jeter_pour_de_faux(journal), fini)
        self.assertEqual(vus, [(True, "", 1)])
        self.assertEqual(journal, [r.dossier])
        self.assertFalse(r.dossier.exists())

    def test_envoi_une_voix_garde_l_autre(self):
        rs = self.deux_voix()
        journal, (vus, fini) = [], self.fini()
        corbeille.enchainer(corbeille.plans([rs["jules"]]), jeter_pour_de_faux(journal), fini)
        self.assertEqual(vus, [(True, "", 1)])
        self.assertFalse(rs["jules"].video.exists() or rs["jules"].dossier_voix.exists())
        self.assertTrue(rs["lea"].video.exists() and rs["lea"].dossier_voix.exists())
        self.assertTrue((rs["jules"].travail / "original.wav").exists(), "les sons communs restent pour Léa")
        self.assertEqual([r.voix for r in rapports.lire_dossier(rs["lea"].dossier, NOMS)], ["lea"])

    def test_fichier_deja_absent(self):
        # un fichier parti entre la confirmation et l'envoi (ou un cache purgé) ne bloque rien
        rs = self.deux_voix()
        liste = corbeille.plans([rs["jules"]])
        rs["jules"].mp3.unlink()
        journal, (vus, fini) = [], self.fini()
        corbeille.enchainer(liste, jeter_pour_de_faux(journal), fini)
        self.assertEqual(vus, [(True, "", 1)])
        self.assertNotIn(rs["jules"].mp3, journal)

    def test_echec_de_la_corbeille(self):
        a = self.doublage(titre="Chanson fictive", rapport=RAPPORT_CHANT)
        rs = self.deux_voix("Duo fictif")
        liste = corbeille.plans([a, rs["jules"]])
        journal, (vus, fini) = [], self.fini()
        corbeille.enchainer(liste, jeter_pour_de_faux(journal, echec_sur="Duo fictif - voix Jules.mp3"), fini)
        self.assertEqual(vus, [(False, "Permission non accordée", 1)], "le premier doublage est parti, pas le second")
        self.assertFalse(a.dossier.exists())
        self.assertFalse(rs["jules"].video.exists(), "la vidéo était partie avant l'échec")
        self.assertTrue(rs["jules"].mp3.exists())
        self.assertTrue(rs["jules"].dossier_voix.exists(), "on s'arrête au premier échec")

    def test_rien_a_envoyer(self):
        vus, fini = self.fini()
        corbeille.enchainer([], jeter_pour_de_faux([]), fini)
        self.assertEqual(vus, [(True, "", 0)])
        self.assertEqual(corbeille.plans([]), [])
