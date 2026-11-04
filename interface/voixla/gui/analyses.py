"""Analyses du son demandées par les pages : lues en cache si possible, sinon calculées dans un processus à part
(python3 -m voixla.analyse, sous nice), une à la fois. Au-delà d'une minute de son (plus que quelques secondes de
calcul), le calcul passe par file-attente, la file des travaux lourds du poste."""

import os
import shutil
import sys

from gi.repository import Gio, GLib

from .. import chemins
from . import commun

SEUIL_FILE_S = 60.0     # secondes de son au-delà desquelles on passe par file-attente


def _analyse():
    from .. import analyse   # numpy n'est importé qu'au premier besoin
    return analyse


class Analyses:
    def __init__(self):
        self.file = []          # demandes en attente : dict(cle, argv, apres)
        self.en_cours = None
        self.attentes = {}      # cle → [rappels]

    # --- pistes : spectre, onde, bandes ----------------------------------------------------------------------------

    def pistes(self, fichiers, produits, rappel, duree_s=None):
        """rappel({fichier: données ou None}, message) : tout de suite si le cache suffit, sinon après le calcul."""
        fichiers = [str(f) for f in fichiers if f]
        produits = tuple(produits)

        def lire():
            a = _analyse()
            return {f: a.en_cache(f, produits) for f in fichiers}

        def lu(r):
            manque = [f for f, d in r.items() if d is None]
            if not manque:
                rappel(r, "")
                return
            cle = ("pistes", tuple(sorted(manque)), produits)
            argv = ["pistes", *manque, "--produits", ",".join(produits)]
            self._demander(cle, argv, duree_s * len(manque) if duree_s else None,
                           lambda ok, sortie: commun.en_fil(lire, lambda r2: rappel(
                               r2, "" if ok else f"analyse impossible : {sortie.strip()[-200:]}")))
            rappel(r, "attente")
        commun.en_fil(lire, lu, lambda e: rappel({f: None for f in fichiers}, f"analyse impossible : {e}"))

    # --- courbes de mélodie ----------------------------------------------------------------------------------------

    def courbes(self, dossier_voix, rappel, duree_s=None):
        """rappel(courbes ou None, message) : courbes.json, sinon courbes-locales.json (calculées au besoin)."""
        dossier_voix = str(dossier_voix)

        def lire():
            return _analyse().courbes_pour(dossier_voix)

        def lu(r):
            c, raison = r
            if c is not None:
                rappel(c, "")
            elif raison == "a_calculer":
                rappel(None, "attente")
                self._demander(("courbes", dossier_voix), ["courbes", dossier_voix], duree_s,
                               lambda ok, sortie: commun.en_fil(lire, lambda r2: rappel(
                                   r2[0], "" if r2[0] is not None else
                                   f"courbe impossible : {(sortie.strip() or r2[1])[-200:]}")))
            else:
                rappel(None, raison)
        commun.en_fil(lire, lu, lambda e: rappel(None, f"courbe impossible : {e}"))

    # --- processus ---------------------------------------------------------------------------------------------------

    def _demander(self, cle, argv, duree_s, apres):
        if cle in self.attentes:
            self.attentes[cle].append(apres)
            return
        self.attentes[cle] = [apres]
        self.file.append({"cle": cle, "argv": argv, "duree_s": duree_s})
        self._suivant()

    def _suivant(self):
        if self.en_cours or not self.file:
            return
        d = self.file.pop(0)
        self.en_cours = d
        commande = ["nice", "-n", "10", sys.executable, "-m", "voixla.analyse", *d["argv"]]
        attente = shutil.which("file-attente")
        if attente and (d["duree_s"] is None or d["duree_s"] > SEUIL_FILE_S):
            commande = [attente, "voix-la-analyse", "1", "2", "--", *commande]
        lanceur = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        chemin = os.pathsep.join([str(chemins.INTERFACE)] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH")
                                                             else []))
        lanceur.setenv("PYTHONPATH", chemin, True)
        try:
            p = lanceur.spawnv(commande)
        except GLib.Error as e:
            self._fini(d, False, e.message)
            return
        p.communicate_utf8_async(None, None, lambda proc, res: self._termine(d, proc, res))

    def _termine(self, d, proc, res):
        try:
            _ok, sortie, _err = proc.communicate_utf8_finish(res)
        except GLib.Error as e:
            sortie = e.message
        self._fini(d, proc.get_successful(), sortie or "")

    def _fini(self, d, ok, sortie):
        self.en_cours = None
        for apres in self.attentes.pop(d["cle"], []):
            try:
                apres(ok, sortie)
            except Exception:  # noqa: BLE001
                import traceback
                traceback.print_exc()
        self._suivant()

    def occupe(self):
        return self.en_cours is not None or bool(self.file)


_instance = None


def analyses():
    global _instance
    if _instance is None:
        _instance = Analyses()
    return _instance
