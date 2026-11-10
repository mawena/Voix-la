"""Avancement d'un travail doubler, reconstruit à partir de ses événements (sans GTK, testable).

Étapes d'un doublage : extraction, envoi, attente, kaggle, rapatriement, mix, video, fini. Les étapes de session
(envoi → rapatriement) sont communes à toutes les vidéos de l'appel ; extraction, mix et video sont faites vidéo par
vidéo (compteur k/N). Les estimations sont celles de l'interface, faites avant le lancement (rapports.estimer, la
session Kaggle calculée par doubler.py) ; une fois le noyau lancé, celle de doubler pour la session (estimation_s de
ses événements « kaggle ») la remplace, et le temps restant la suit.
"""

import time

ETAPES = [
    ("extraction", "Extraction du son", "par_video"),
    ("envoi", "Envoi sur Kaggle", "session"),
    ("attente", "Attente d’un GPU", "session"),
    ("kaggle", "Conversion sur le GPU", "session"),
    ("rapatriement", "Rapatriement", "session"),
    ("mix", "Mixage", "par_video"),
    ("video", "Remise sur la vidéo", "par_video"),
    ("fini", "Fini", "par_video"),
]
NOMS = [e[0] for e in ETAPES]
LIBELLES = {e[0]: e[1] for e in ETAPES}
PAR_VIDEO = {e[0] for e in ETAPES if e[2] == "par_video"}
ETAPES_REMIX = ["mix", "video", "fini"]
# Affinage de la voix sur son chant (commande affiner) : contrôle des prises sur le PC, puis la session Kaggle, sans mixage
ETAPES_AFFINAGE = ["controle", "envoi", "attente", "kaggle", "rapatriement", "fini"]
LIBELLES["controle"] = "Contrôle des prises de chant"
LIBELLES_AFFINAGE = {"kaggle": "Affinage sur le GPU", "rapatriement": "Rapatriement du point de contrôle"}

STATUTS_KAGGLE = {
    "queued": "en file d’attente", "new": "créé", "pending": "en préparation", "running": "en cours",
    "complete": "terminé", "error": "en erreur", "cancelacknowledged": "annulé", "cancelrequested": "annulation demandée",
    "cancelled": "annulé", "canceled": "annulé",
}


class Etape:
    def __init__(self, nom):
        self.nom = nom
        self.libelle = LIBELLES.get(nom, nom)
        self.par_video = nom in PAR_VIDEO
        self.etat = "a_venir"        # a_venir, en_cours, partiel, fait, saute, erreur, annule
        self.debut = None            # premier début
        self.fin = None              # dernière fin
        self.ouverts = {}            # vidéo (ou None) → instant de début
        self.faits = set()
        self.cumul_s = 0.0           # temps passé (somme des intervalles)
        self.duree_annoncee = None   # duree_s donnée par doubler à la fin
        self.message = ""

    def ecoule_s(self, maintenant):
        if self.duree_annoncee is not None and not self.ouverts:
            return self.duree_annoncee
        en_cours = sum(max(0.0, maintenant - t0) for t0 in self.ouverts.values())
        return self.cumul_s + en_cours


class Avancement:
    def __init__(self, nb_videos=1, estimations=None, type_travail="doublage", debut=None):
        self.nb_videos = max(1, int(nb_videos or 1))
        self.type = type_travail
        noms = ETAPES_REMIX if type_travail == "remix" else ETAPES_AFFINAGE if type_travail == "affinage" else NOMS
        self.etapes = {n: Etape(n) for n in noms}
        if type_travail == "affinage":
            for n, libelle in LIBELLES_AFFINAGE.items():
                self.etapes[n].libelle = libelle
        self.ordre = list(noms)
        self.estimations = dict(estimations or {})
        self.kaggle = {}              # statut, depuis_s, estimation_s, t
        self.sorties = []
        self.courbes = []
        self.paroles = []
        self.erreurs = []
        self.messages = []
        self.debut = debut
        self.fin = None
        self.code = None
        self.termine = False
        self.dernier_t = debut

    # --- événements ------------------------------------------------------------------------------------------------

    def appliquer(self, ev):
        t = float(ev.get("t") or time.time())
        self.dernier_t = max(self.dernier_t or t, t)
        if self.debut is None:
            self.debut = t
        type_ = ev.get("type")
        if type_ == "etape":
            self._etape(ev, t)
        elif type_ == "kaggle":
            self.kaggle.update({k: ev[k] for k in ("statut", "depuis_s", "estimation_s") if ev.get(k) is not None})
            self.kaggle["t"] = t
            if ev.get("message"):
                self.kaggle["message"] = ev["message"]
            if ev.get("estimation_s") is not None:
                try:
                    self.estimations["kaggle"] = float(ev["estimation_s"])
                except (TypeError, ValueError):
                    pass
        elif type_ == "sortie":
            # doubler : mp4 (vidéo doublée, quel que soit son conteneur), mp3, rapport ; repli texte : chemin
            for cle in ("mp4", "mp3", "chemin", "fichier"):
                chemin = ev.get(cle)
                if chemin and chemin not in self.sorties:
                    self.sorties.append(chemin)
            if ev.get("courbes"):
                self.courbes.append(ev["courbes"])     # travail/<voix>/courbes.json (courbe de mélodie)
            if ev.get("paroles"):
                self.paroles.append(ev["paroles"])     # travail/<voix>/paroles.json (paroles synchronisées)
        elif type_ == "erreur":
            msg = ev.get("message") or "erreur"
            if ev.get("video"):
                msg = f"{ev['video']} : {msg}"
            if msg not in self.erreurs:
                self.erreurs.append(msg)
        elif type_ == "fin":
            self.terminer(ev.get("code", 0 if not self.erreurs else 1), t=t, par_evenement=True)
        elif type_ == "message" and ev.get("message"):
            self.messages.append((t, ev["message"]))

    def _etape(self, ev, t):
        nom = ev.get("etape")
        if nom not in self.etapes:
            return
        e = self.etapes[nom]
        video = ev.get("video") if e.par_video else None
        etat = ev.get("etat")
        if ev.get("message"):
            e.message = ev["message"]
        if etat == "debut":
            self._fermer_precedentes(nom, video, t)
            if video not in e.ouverts:
                e.ouverts[video] = t
            e.debut = e.debut if e.debut is not None else t
            e.etat = "en_cours"
            if nom == "fini":
                self._finir_etape(e, video, t)
        elif etat == "fin":
            if video not in e.ouverts and not e.ouverts and e.debut is None:
                e.debut = t   # fin sans début connu : intervalle nul
            self._finir_etape(e, video, t, ev.get("duree_s"))
        elif etat == "erreur":
            e.ouverts.pop(video, None)
            e.etat = "erreur"
            e.fin = t
            if ev.get("message"):
                msg = f"{e.libelle} : {ev['message']}"
                if msg not in self.erreurs:
                    self.erreurs.append(msg)

    def _finir_etape(self, e, video, t, duree_s=None):
        t0 = e.ouverts.pop(video, None)
        if t0 is None and e.ouverts and video is None:
            # fin globale d'une étape par vidéo : ferme tout
            for v in list(e.ouverts):
                e.cumul_s += max(0.0, t - e.ouverts.pop(v))
                e.faits.add(v)
        elif t0 is not None:
            e.cumul_s += max(0.0, t - t0)
        if duree_s is not None and not e.par_video:
            try:
                e.duree_annoncee = float(duree_s)
            except (TypeError, ValueError):
                pass
        e.faits.add(video)
        e.fin = t
        if e.etat != "erreur":
            if e.ouverts:
                e.etat = "en_cours"
            elif e.par_video and len(e.faits - {None}) < self.nb_videos and self.type != "remix":
                e.etat = "partiel"
            else:
                e.etat = "fait"

    def _fermer_precedentes(self, nom, video, t):
        """Une étape qui commence clôt les étapes précédentes restées ouvertes (même vidéo, ou de session)."""
        i = self.ordre.index(nom)
        for avant in self.ordre[:i]:
            e = self.etapes[avant]
            if e.etat == "erreur":
                continue
            if e.par_video:
                if video is not None and video in e.ouverts:
                    self._finir_etape(e, video, t)
                elif self.etapes[nom].par_video is False and e.ouverts:
                    self._finir_etape(e, None, t)
            elif e.ouverts:
                self._finir_etape(e, None, t)

    def terminer(self, code, t=None, annule=False, par_evenement=False):
        """Fin du processus (code de sortie), ou annulation."""
        if self.termine and not par_evenement:
            if self.code is None:
                self.code = code
            return
        t = t or self.dernier_t or time.time()
        self.termine = True
        self.code = code
        self.fin = t
        reussi = code == 0 and not annule
        for nom in self.ordre:
            e = self.etapes[nom]
            if e.ouverts:
                if reussi:
                    self._finir_etape(e, None, t)
                else:
                    for v in list(e.ouverts):
                        e.cumul_s += max(0.0, t - e.ouverts.pop(v))
                    e.etat = "annule" if annule else "erreur"
                    e.fin = t
            elif reussi and e.etat in ("a_venir",):
                e.etat = "saute" if nom not in ("fini",) else "fait"
            elif reussi and e.etat == "partiel":
                e.etat = "fait"
        if reussi:
            self.etapes["fini"].etat = "fait"
            self.etapes["fini"].debut = self.etapes["fini"].fin = t

    # --- lecture -----------------------------------------------------------------------------------------------------

    def etape_courante(self):
        for nom in self.ordre:
            if self.etapes[nom].etat == "en_cours":
                return nom
        return None

    def derniere_atteinte(self):
        """La dernière étape commencée (pour savoir si les sons sont déjà partis sur Kaggle)."""
        derniere = None
        for nom in self.ordre:
            if self.etapes[nom].debut is not None:
                derniere = nom
        return derniere

    def kaggle_parti(self):
        """Vrai si la session Kaggle a pu démarrer (envoi commencé) : elle continuerait seule après une annulation."""
        return any(self.etapes[n].debut is not None for n in ("attente", "kaggle", "rapatriement") if n in self.etapes) \
            or (self.etapes.get("envoi") and self.etapes["envoi"].etat == "fait")

    def ecoule_s(self, maintenant=None):
        if self.debut is None:
            return 0.0
        fin = self.fin if self.termine else (maintenant or time.time())
        return max(0.0, fin - self.debut)

    def restant_s(self, maintenant=None):
        """Estimation du temps restant (None si on ne sait pas)."""
        if self.termine:
            return 0.0
        maintenant = maintenant or time.time()
        if not self.estimations:
            return None
        total = 0.0
        for nom in self.ordre:
            e = self.etapes[nom]
            est = float(self.estimations.get(nom) or 0.0)
            if e.etat in ("fait", "saute", "erreur", "annule"):
                continue
            if e.par_video and self.nb_videos > 1:
                faites = len(e.faits - {None})
                par_video = est / self.nb_videos
                reste_videos = max(0, self.nb_videos - faites - len(e.ouverts))
                encours = sum(max(0.0, par_video - (maintenant - t0)) for t0 in e.ouverts.values())
                total += reste_videos * par_video + encours
            elif e.etat == "en_cours":
                if nom == "kaggle" and self.kaggle.get("estimation_s") is not None and self.kaggle.get("depuis_s") is not None:
                    # doubler estime la durée totale du noyau : reste = estimation − temps déjà passé
                    passe = float(self.kaggle["depuis_s"]) + (maintenant - float(self.kaggle.get("t") or maintenant))
                    total += max(0.0, float(self.kaggle["estimation_s"]) - passe)
                else:
                    total += max(0.0, est - e.ecoule_s(maintenant))
            else:
                total += est
        return total

    def resume(self, maintenant=None):
        """Phrase courte de l'état : « Conversion sur le GPU · 3 min 10 s »."""
        from . import formats
        maintenant = maintenant or time.time()
        if self.termine:
            return "Fini" if self.code == 0 else "Échec"
        c = self.etape_courante()
        if not c:
            return "Démarrage…"
        e = self.etapes[c]
        texte = e.libelle
        if e.par_video and self.nb_videos > 1:
            texte += f" ({min(self.nb_videos, len(e.faits - {None}) + 1)}/{self.nb_videos})"
        return f"{texte} · {formats.duree(e.ecoule_s(maintenant))}"

    def statut_kaggle(self):
        s = (self.kaggle.get("statut") or "").lower()
        return STATUTS_KAGGLE.get(s, s)

    def estimation_doubler_s(self):
        """Durée de la session Kaggle estimée par doubler (estimation_s de ses événements « kaggle »), connue une fois
        le noyau lancé ; elle remplace alors celle de l'interface (estimations["kaggle"]). None avant."""
        try:
            e = self.kaggle.get("estimation_s")
            return float(e) if e is not None and float(e) > 0 else None
        except (TypeError, ValueError):
            return None

    def texte_kaggle(self, maintenant=None):
        """Ligne d'état du noyau : « GPU Kaggle : en cours depuis 5 min 10 s · durée prévue par doubler ≈ 17 min »
        (« … · au-delà de la durée prévue par doubler (≈ 17 min) » si le noyau la dépasse) ; None avant son premier
        état."""
        from . import formats
        if not self.kaggle.get("statut"):
            return None
        maintenant = maintenant or time.time()
        depuis = float(self.kaggle.get("depuis_s") or 0) + max(0.0, maintenant - float(self.kaggle.get("t") or maintenant))
        texte = f"GPU Kaggle : {self.statut_kaggle()} depuis {formats.duree(depuis)}"
        est = self.estimation_doubler_s()
        if est:
            en_cours = self.etapes.get("kaggle") is not None and self.etapes["kaggle"].etat == "en_cours"
            if en_cours and depuis > est:
                texte += f" · au-delà de la durée prévue par doubler ({formats.duree_courte(est)})"
            else:
                texte += f" · durée prévue par doubler {formats.duree_courte(est)}"
        return texte
