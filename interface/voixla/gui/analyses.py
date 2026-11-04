"""Analyses du son demandées par les pages : lues en cache si possible, sinon calculées dans un processus à part
(python3 -m voixla.analyse, sous nice), une à la fois. Au-delà d'une minute de son (plus que quelques secondes de
calcul), le calcul passe par file-attente, la file des travaux lourds du poste.

La même file prépare « ma voix seule » et les copies vidéo des pistes sans image, enregistre le beat ou la voix
(python3 -m voixla.pistes), et extrait les sous-titres de la vidéo doublée (python3 -m voixla.paroles). Ces
préparations-là sont légères : jamais par file-attente (partagée avec les autres sessions, où elles attendraient
derrière des travaux lourds), mais tout de suite, sous nice -n 19 et ionice -c3, avec un garde-fou mémoire (elles
attendent tant que MemAvailable est sous 700 Mo, sans bloquer l'interface). Voir voixla/preparation.py. La piste qu'on
vient de cliquer passe devant ce qui se prépare à l'avance ; l'avancement des copies (ffmpeg -progress) remonte aux
pages."""

import os
import shutil
import sys

from gi.repository import Gio, GLib

from .. import chemins
from .. import preparation as prep
from . import commun

SEUIL_FILE_S = prep.SEUIL_FILE_S     # secondes de son au-delà desquelles une analyse passe par file-attente
ATTENTE_MEMOIRE_S = 5


def _analyse():
    from .. import analyse   # numpy n'est importé qu'au premier besoin
    return analyse


class Analyses:
    def __init__(self):
        self.file = prep.FilePreparations()
        self._attente_memoire = None

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

    # --- pistes séparées : ma voix seule, enregistrements -------------------------------------------------------------

    def ma_voix(self, dossier_voix, rappel, duree_s=None, prioritaire=False, avancement=None, autotune=False):
        """rappel(fichier ou None, message) : « ma voix seule » en cache, sinon préparée (ffmpeg, priorité basse).
        avancement(fraction, étape) pendant le calcul. autotune : celle de la version auto-tune."""
        dossier_voix = str(dossier_voix)
        at = ["--autotune"] if autotune else []

        def lire():
            from .. import pistes
            return pistes.ma_voix_prete(dossier_voix, autotune=autotune)

        def lu(f):
            if f is not None:
                rappel(f, "")
                return
            rappel(None, "attente")
            self._demander(("ma_voix", dossier_voix, autotune), ["voix", dossier_voix, *at], duree_s,
                           lambda ok, sortie: commun.en_fil(lire, lambda f2: rappel(
                               f2, "" if f2 is not None else
                               f"préparation impossible : {derniere_ligne(sortie) or 'sans message'}")),
                           module="voixla.pistes", prioritaire=prioritaire, avancement=avancement)
        commun.en_fil(lire, lu, lambda e: rappel(None, f"préparation impossible : {e}"))

    def video(self, video, rappel, son=None, voix=None, duree_s=None, prioritaire=False, avancement=None,
              autotune=False):
        """rappel(fichier ou None, message) : la copie vidéo d'une piste sans image (l'image de la vidéo doublée
        copiée telle quelle, le son de la piste), en cache, sinon préparée (ffmpeg, sous nice). son : le fichier de la
        piste ; voix : le dossier d'une voix, pour « ma voix seule ». Message « a_refaire » : la copie faite ne
        correspond plus aux fichiers (remix pendant la préparation), à redemander. prioritaire : la piste qu'on vient
        de cliquer (passe devant les préparations à l'avance) ; avancement(fraction, étape) pendant la copie.
        autotune (avec voix) : la voix seule de la version auto-tune."""
        video, son, voix = str(video), (str(son) if son else None), (str(voix) if voix else None)
        autotune = bool(autotune and voix)

        def lire():
            from .. import pistes
            return pistes.video_prete(video, son=son, voix=voix, autotune=autotune)

        def fini(ok, sortie):
            raison = derniere_ligne(sortie).removeprefix("erreur :").strip()

            def lu(f2):
                if f2 is not None:
                    rappel(f2, "")
                elif ok:
                    rappel(None, "a_refaire")
                else:
                    rappel(None, raison or "copie vidéo impossible (sans message)")
            commun.en_fil(lire, lu, lambda e: rappel(None, f"copie vidéo impossible : {e}"))

        def lu(f):
            if f is not None:
                rappel(f, "")
                return
            rappel(None, "attente")
            argv = ["video", video, *(["--voix", voix] if voix else [son or ""]), *(["--autotune"] if autotune else [])]
            self._demander(("video", video, voix or son, autotune), argv, duree_s, fini, module="voixla.pistes",
                           prioritaire=prioritaire, avancement=avancement)
        commun.en_fil(lire, lu, lambda e: rappel(None, f"copie vidéo impossible : {e}"))

    def exporter(self, source, sortie, rappel, duree_s=None, voix=False, autotune=False):
        """Enregistre source (un fichier, ou le dossier d'une voix si voix=True, sa version auto-tune si autotune) dans
        sortie ; rappel(ok, message)."""
        argv = ["exporter", *(["--voix"] if voix else []), str(source), str(sortie),
                *(["--autotune"] if voix and autotune else [])]
        self._demander(("exporter", str(sortie)), argv, duree_s,
                       lambda ok, s: rappel(ok, "" if ok else (s.strip()[-300:] or "sans message")),
                       module="voixla.pistes")

    def miniature(self, video, rappel):
        """rappel(fichier ou None, message) : une image de la vidéo doublée (MPRIS, barre « en cours de lecture »), en
        cache, sinon prise par ffmpeg (une image, sous nice)."""
        video = str(video)

        def lire():
            from .. import pistes
            return pistes.miniature_prete(video)

        def lu(f):
            if f is not None:
                rappel(f, "")
                return
            rappel(None, "attente")
            self._demander(("miniature", video), ["miniature", video], 1.0,
                           lambda ok, sortie: commun.en_fil(lire, lambda f2: rappel(
                               f2, "" if f2 is not None else
                               f"miniature impossible : {sortie.strip()[-200:] or 'sans message'}")),
                           module="voixla.pistes")
        commun.en_fil(lire, lu, lambda e: rappel(None, f"miniature impossible : {e}"))

    # --- paroles : paroles.json, sinon sous-titres de la vidéo doublée ------------------------------------------------

    def paroles(self, dossier_voix, video, rappel):
        """rappel(paroles ou None, message) : paroles.json, sinon les sous-titres de la vidéo (extraits au besoin)."""
        def lire():
            from .. import paroles
            if dossier_voix is None:
                return paroles.sous_titres_en_cache(video) if video else (None, "doublage inachevé : pas de paroles")
            return paroles.paroles_pour(dossier_voix, video)

        def lu(r):
            p, raison = r
            if p is not None:
                rappel(p, "")
            elif raison == "a_extraire" and video:
                rappel(None, "attente")
                self._demander(("sous-titres", str(video)), ["sous-titres", str(video)], 1.0,
                               lambda ok, sortie: commun.en_fil(lire, lambda r2: rappel(
                                   r2[0], "" if r2[0] is not None else
                                   (r2[1] if ok and r2[1] != "a_extraire" else
                                    f"sous-titres illisibles : {sortie.strip()[-200:] or 'sans message'}"))),
                               module="voixla.paroles")
            else:
                rappel(None, raison or "pas de paroles")
        commun.en_fil(lire, lu, lambda e: rappel(None, f"paroles illisibles : {e}"))

    # --- processus ---------------------------------------------------------------------------------------------------

    def _demander(self, cle, argv, duree_s, apres, module="voixla.analyse", prioritaire=False, avancement=None):
        self.file.ajouter(prep.Demande(cle, argv, module, duree_s, [apres], [avancement] if avancement else []),
                          prioritaire)
        self._suivant()

    def _suivant(self):
        if self.file.en_cours is not None or not self.file.attente or self._attente_memoire is not None:
            return
        if not prep.peut_lancer(self.file.attente[0], prep.memoire_disponible_mo()):
            # garde-fou mémoire : on réessaie un peu plus tard, l'interface continue
            self._attente_memoire = GLib.timeout_add_seconds(ATTENTE_MEMOIRE_S, self._apres_attente_memoire)
            return
        d = self.file.prochaine()
        commande = prep.commande(sys.executable, d.module, d.argv, d.duree_s, shutil.which("file-attente"))
        lanceur = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        chemin = os.pathsep.join([str(chemins.INTERFACE)] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH")
                                                             else []))
        lanceur.setenv("PYTHONPATH", chemin, True)
        lanceur.setenv("VOIXLA_AVANCEMENT", "1", True)
        try:
            p = lanceur.spawnv(commande)
        except GLib.Error as e:
            self._fini(d, False, e.message)
            return
        flux = Gio.DataInputStream.new(p.get_stdout_pipe())
        self._lire(d, p, flux, [])

    def _apres_attente_memoire(self):
        self._attente_memoire = None
        self._suivant()
        return False

    def _lire(self, d, p, flux, lignes):
        """Lit la sortie ligne à ligne : l'avancement remonte aux pages, le reste sert au message final."""
        def lu(f, res):
            try:
                ligne, _n = f.read_line_finish_utf8(res)
            except GLib.Error:
                ligne = None
            if ligne is None:
                p.wait_async(None, lambda proc, r: self._termine(d, proc, r, "\n".join(lignes)))
                return
            av = prep.lire_avancement(ligne)
            if av is not None:
                for rappel in list(d.avancement):
                    try:
                        rappel(*av)
                    except Exception:  # noqa: BLE001
                        import traceback
                        traceback.print_exc()
            else:
                lignes.append(ligne)
            f.read_line_async(GLib.PRIORITY_DEFAULT, None, lu)
        flux.read_line_async(GLib.PRIORITY_DEFAULT, None, lu)

    def _termine(self, d, proc, res, sortie):
        try:
            proc.wait_finish(res)
        except GLib.Error as e:
            sortie = sortie or e.message
        self._fini(d, proc.get_successful(), sortie or "")

    def _fini(self, d, ok, sortie):
        self.file.finir()
        for apres in d.apres:
            try:
                apres(ok, sortie)
            except Exception:  # noqa: BLE001
                import traceback
                traceback.print_exc()
        self._suivant()

    def occupe(self):
        return self.file.occupee()


def derniere_ligne(sortie, n=200):
    lignes = [l.strip() for l in (sortie or "").splitlines() if l.strip()]
    return lignes[-1][-n:] if lignes else ""


_instance = None


def analyses():
    global _instance
    if _instance is None:
        _instance = Analyses()
    return _instance
