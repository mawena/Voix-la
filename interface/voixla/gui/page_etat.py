"""Page « État » : Kaggle (jeton, commande, compte, noyaux), doubler (commande, options, verrou, données) et le poste
(mémoire, charge, file des travaux lourds).

Rafraîchissement sobre, seulement pendant que la page est visible : le poste toutes les 10 s ; Kaggle à la demande ou
toutes les 60 s au plus, une vérification à la fois (un noyau après l'autre). Tout appel lent passe par commun.en_fil.
Le jeton Kaggle n'est jamais lu : seule la présence du fichier est vérifiée (systeme.jeton_kaggle_present).
"""

import shlex
import time

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from .. import chemins, formats, moteur, quota, systeme
from . import commun

LOCAL_S = 10
KAGGLE_S = 60
SESSIONS_MAX = 2

CSS = b"""
progressbar.etat-niveau trough, progressbar.etat-niveau progress { min-height: 8px; border-radius: 99px; }
progressbar.etat-niveau.ok progress { background-color: @success_color; }
progressbar.etat-niveau.alerte progress { background-color: @warning_color; }
progressbar.etat-niveau.critique progress { background-color: @error_color; }
row.etat-file .subtitle { font-family: monospace; font-size: 0.9em; }
"""

# Statut de « kaggle kernels status » (en minuscules) → (libellé, style du badge)
STATUTS = {
    "running": ("en cours", "accent"),
    "queued": ("en file", "alerte"),
    "new": ("en file", "alerte"),
    "pending": ("en file", "alerte"),
    "complete": ("terminé", "succes"),
    "error": ("erreur", "erreur"),
    "cancel_requested": ("annulé", "neutre"),
    "cancel_acknowledged": ("annulé", "neutre"),
    "cancelrequested": ("annulé", "neutre"),
    "cancelacknowledged": ("annulé", "neutre"),
    "cancelled": ("annulé", "neutre"),
}

OPTIONS = (
    ("--evenements", "Évènements", "suivi détaillé d’un doublage ; sinon, lecture de la sortie texte"),
    ("--donnees", "Données", "dossier de données ; sinon --sortie et DOUBLER_VOIX"),
    ("--config", "Config", "écriture de config.json ; sinon, écriture directe du fichier"),
    ("--json", "JSON", "liste des voix et configuration lisibles par l’interface"),
    ("--mix-v1", "Mix v1", "ancien mixage, pour comparer"),
)

VERROU = {
    "libre": ("libre", "succes", "aucun doublage ne tourne sur ce dossier"),
    "pris": ("pris par un doublage", "accent", "un doublage tourne sur ce dossier"),
    "absent": ("jamais utilisé", "neutre", "doubler n’a encore jamais tourné sur ce dossier"),
}

_css_installe = False


def installer_css():
    global _css_installe
    if _css_installe:
        return
    fournisseur = Gtk.CssProvider()
    fournisseur.load_from_data(CSS, -1)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), fournisseur,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_installe = True


def en_fil(travail, fini, echec):
    """commun.en_fil, l'erreur revenant elle aussi par fini (un seul rappel à gérer dans la page)."""
    def protege():
        try:
            return True, travail()
        except Exception as e:  # noqa: BLE001 — rendue à l'interface
            return False, e
    commun.en_fil(protege, lambda r: fini(r[1]) if r[0] else echec(r[1]))


def heure(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "—"


def go(x):
    return f"{formats.nombre(x, 1)} Go" if x is not None else "—"


class Valeur:
    """Suffixe d'une ligne : badge ou texte (en chiffres alignés), qu'on change sur place."""

    def __init__(self, ligne):
        self.badge = commun.badge("")
        self.texte = Gtk.Label(css_classes=["dim-label", "chiffres"], valign=Gtk.Align.CENTER, selectable=True,
                               ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=34)
        ligne.add_suffix(self.texte)
        ligne.add_suffix(self.badge)
        self.mettre("…")

    def mettre(self, texte=None, style=None):
        """style donné : badge ; sinon : texte."""
        if style:
            self.badge.set_label(texte or "")
            self.badge.set_css_classes(["badge", style])
        self.badge.set_visible(bool(style))
        self.texte.set_label("" if style else (texte or ""))
        self.texte.set_visible(not style)


def niveau():
    return Gtk.ProgressBar(css_classes=["etat-niveau", "ok"], valign=Gtk.Align.CENTER, width_request=120)


def regler_niveau(barre, fraction, style):
    barre.set_fraction(max(0.0, min(1.0, fraction)))
    barre.set_css_classes(["etat-niveau", style])


class PageEtat(commun.Page):
    def __init__(self, fenetre):
        super().__init__(fenetre, "État", "etat")
        installer_css()
        self._ferme = False
        self._minuteur_local = None
        self._minuteur_kaggle = None
        self._local_en_cours = False
        self._config_en_cours = False
        self._suites_config = []        # rappels en attente de la lecture de configuration en cours
        self._verif = False             # une vérification Kaggle à la fois
        self._derniere_verif = 0.0
        self._config = None
        self._noyaux = []               # [(libellé, réf)]
        self._statuts = {}              # réf → (statut ou None, texte, heure)
        self._lignes_noyaux = {}
        self._a_relire = False

        self.b_actualiser = commun.bouton(icone="view-refresh-symbolic", infobulle="Actualiser (F5)",
                                          clic=self.actualiser)
        self.entete.pack_end(self.b_actualiser)

        # alerte : moins de quota_alerte_h heures de GPU restantes cette semaine
        self.banniere_quota = Adw.Banner()
        self.vue.add_top_bar(self.banniere_quota)
        self._lignes_sessions = []

        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=18, margin_bottom=24,
                    margin_start=12, margin_end=12)
        b.append(self._groupe_quota())
        b.append(self._groupe_kaggle())
        b.append(self._groupe_noyaux())
        b.append(self._groupe_doubler())
        b.append(self._groupe_poste())
        self.contenu(commun.defile(b, 760))
        fenetre.ecouter("donnees", self._donnees_changees)
        fenetre.ecouter("quota", self._peindre_quota)
        fenetre.ecouter("travaux", lambda *_: self.visible_ and self._peindre_quota())

    # --- construction (une fois ; les valeurs changent sur place) -------------------------------------------------

    def _groupe_quota(self):
        g = Adw.PreferencesGroup(title="Quota GPU Kaggle", description=commun.echapper(
            "Environ 30 h de GPU par semaine et par compte ; une session sur deux cartes compte une fois. Lu par "
            "« kaggle quota » (le jeton n’est pas ouvert par l’interface), sinon compté d’après les sessions "
            "enregistrées sur ce poste."))
        self.sablier_quota = Gtk.Spinner(visible=False, valign=Gtk.Align.CENTER, tooltip_text="Lecture du quota")
        g.set_header_suffix(self.sablier_quota)
        self.r_q_utilise = commun.ligne("Utilisées cette semaine")
        self.v_q_utilise = Valeur(self.r_q_utilise)
        self.n_quota = niveau()
        self.n_quota.set_size_request(160, -1)
        self.r_q_utilise.add_suffix(self.n_quota)
        self.r_q_reste = commun.ligne("Restantes")
        self.v_q_reste = Valeur(self.r_q_reste)
        self.r_q_remise = commun.ligne("Remise à zéro")
        self.v_q_remise = Valeur(self.r_q_remise)
        self.r_q_file = commun.ligne("File des travaux")
        self.v_q_file = Valeur(self.r_q_file)
        self.r_q_sessions = Adw.ExpanderRow(title="Sessions comptées sur ce poste")
        self.r_q_limites = Adw.ActionRow(title="Ce qui échappe au compte local", subtitle_lines=0,
                                         subtitle=commun.echapper("\n".join("• " + l for l in quota.LIMITES)))
        self.r_q_sessions.add_row(self.r_q_limites)
        for r in (self.r_q_utilise, self.r_q_reste, self.r_q_remise, self.r_q_file, self.r_q_sessions):
            g.add(r)
        self.v_q_utilise.mettre("lecture…")
        for v in (self.v_q_reste, self.v_q_remise, self.v_q_file):
            v.mettre("")
        return g

    def _groupe_kaggle(self):
        g = Adw.PreferencesGroup(title="Kaggle", description=commun.echapper(
            "Les doublages tournent sur le GPU gratuit de Kaggle. Le jeton n’est jamais lu : seule la présence du "
            "fichier est vérifiée."))
        self.r_jeton = commun.ligne("Jeton d’accès", "~/.kaggle/access_token ou ~/.kaggle/kaggle.json")
        self.v_jeton = Valeur(self.r_jeton)
        self.r_cmd_kaggle = commun.ligne("Commande kaggle")
        self.v_cmd_kaggle = Valeur(self.r_cmd_kaggle)
        self.r_compte = commun.ligne("Compte")
        self.v_compte = Valeur(self.r_compte)
        self.b_reglages = commun.bouton("Réglages…", styles=("flat",), valign=Gtk.Align.CENTER, visible=False,
                                        clic=self.fenetre.ouvrir_reglages)
        self.r_compte.add_suffix(self.b_reglages)
        self.r_jeu = commun.ligne("Jeu de données")
        self.v_jeu = Valeur(self.r_jeu)
        for r in (self.r_jeton, self.r_cmd_kaggle, self.r_compte, self.r_jeu):
            g.add(r)
        return g

    def _groupe_noyaux(self):
        self.g_noyaux = Adw.PreferencesGroup(title="Noyaux Kaggle", description=commun.echapper(
            "Statut lu par « kaggle kernels status », à la demande (bouton Actualiser) ou toutes les minutes au plus "
            "tant que cette page est ouverte ; une vérification à la fois."))
        self.sablier = Gtk.Spinner(visible=False, valign=Gtk.Align.CENTER, tooltip_text="Vérification en cours")
        self.g_noyaux.set_header_suffix(self.sablier)
        self.r_sessions = commun.ligne("Sessions GPU en cours", f"{SESSIONS_MAX} au plus par compte")
        self.v_sessions = Valeur(self.r_sessions)
        self.v_sessions.mettre("non vérifié")
        self.g_noyaux.add(self.r_sessions)
        self.r_aucun = commun.ligne("Aucun noyau connu", "le compte Kaggle n’est pas configuré")
        self.r_aucun.set_visible(False)
        self.g_noyaux.add(self.r_aucun)
        return self.g_noyaux

    def _groupe_doubler(self):
        g = Adw.PreferencesGroup(title="doubler")
        self.r_cmd = commun.ligne("Commande")
        self.r_cmd.set_subtitle_selectable(True)
        self.v_cmd = Valeur(self.r_cmd)
        self.r_options = Adw.ExpanderRow(title="Options récentes reconnues")
        self.v_options = {}
        for opt, titre, aide in OPTIONS:
            r = commun.ligne(f"{titre} ({opt})", aide)
            self.v_options[opt] = Valeur(r)
            self.r_options.add_row(r)
        self.r_verrou = commun.ligne("Verrou de doubler")
        self.v_verrou = Valeur(self.r_verrou)
        self.r_config = commun.ligne("Configuration")
        self.r_config.set_subtitle_selectable(True)
        self.r_donnees = commun.ligne("Dossier de données")
        self.r_donnees.set_subtitle_selectable(True)
        self.b_ouvrir = commun.bouton(icone="folder-open-symbolic", infobulle="Ouvrir le dossier", styles=("flat",),
                                      valign=Gtk.Align.CENTER, clic=self._ouvrir_donnees)
        self.r_donnees.add_suffix(self.b_ouvrir)
        for r in (self.r_cmd, self.r_options, self.r_verrou, self.r_config, self.r_donnees):
            g.add(r)
        return g

    def _groupe_poste(self):
        g = Adw.PreferencesGroup(title="Poste", description=commun.echapper(
            f"Relu toutes les {LOCAL_S} s tant que cette page est ouverte. Un seul travail lourd à la fois sur ce "
            "poste : les autres attendent leur tour dans la file."))
        self.l_local = commun.legende("")
        self.l_local.set_valign(Gtk.Align.CENTER)
        g.set_header_suffix(self.l_local)
        self.r_ram = commun.ligne("Mémoire vive")
        self.n_ram = niveau()
        self.r_ram.add_suffix(self.n_ram)
        self.r_charge = commun.ligne("Charge sur 1 min")
        self.n_charge = niveau()
        self.r_charge.add_suffix(self.n_charge)
        self.r_file = Adw.ActionRow(title="File des travaux lourds", subtitle_selectable=True, subtitle_lines=0)
        self.r_file.add_css_class("etat-file")
        for r in (self.r_ram, self.r_charge, self.r_file):
            g.add(r)
        return g

    # --- cycle de vie ------------------------------------------------------------------------------------------------

    def montrer(self):
        super().montrer()
        self._lire_local()
        self._lire_quota()
        if time.time() - self._derniere_verif >= KAGGLE_S:
            self.verifier_kaggle()   # relit aussi la configuration
        else:
            self._lire_config()
        if self._minuteur_local is None:
            self._minuteur_local = GLib.timeout_add_seconds(LOCAL_S, self._tic_local)
        if self._minuteur_kaggle is None:
            self._minuteur_kaggle = GLib.timeout_add_seconds(KAGGLE_S, self._tic_kaggle)

    def cacher(self):
        super().cacher()
        self._arreter_minuteurs()

    def fermer(self):
        self._ferme = True
        self._arreter_minuteurs()

    def actualiser(self):
        self._lire_local()
        self._lire_quota(age_max=0)
        if self._verif:
            self.fenetre.toast("Vérification Kaggle déjà en cours")
        else:
            self.verifier_kaggle()   # relit aussi la configuration

    def _arreter_minuteurs(self):
        for nom in ("_minuteur_local", "_minuteur_kaggle"):
            ident = getattr(self, nom)
            if ident is not None:
                GLib.source_remove(ident)
                setattr(self, nom, None)

    def _tic_local(self):
        if self._ferme or not self.visible_:
            self._minuteur_local = None
            return False
        self._lire_local()
        return True

    def _tic_kaggle(self):
        if self._ferme or not self.visible_:
            self._minuteur_kaggle = None
            return False
        if not self._verif and time.time() - self._derniere_verif >= KAGGLE_S - 1:
            self.verifier_kaggle()
        self._lire_quota()
        return True

    def _donnees_changees(self):
        if self.visible_:
            self._lire_local()
            self._lire_config()

    def _ouvrir_donnees(self):
        d = (self._config or {}).get("donnees") or self.fenetre.donnees
        commun.ouvrir_dossier(self.fenetre, d)

    # --- quota GPU ---------------------------------------------------------------------------------------------------

    def _lire_quota(self, age_max=KAGGLE_S):
        """« kaggle quota » relu au plus toutes les KAGGLE_S secondes tant que la page est visible (age_max=0 : tout
        de suite), et le compte local ; la fenêtre garde le résultat pour toutes les pages."""
        if self._ferme:
            return
        self.sablier_quota.set_visible(True)
        self.sablier_quota.start()

        def fini(_etat):
            if self._ferme:
                return
            self.sablier_quota.stop()
            self.sablier_quota.set_visible(False)
        self.fenetre.actualiser_quota(age_max=age_max, rappel=fini)

    def _peindre_quota(self, *_):
        e = self.fenetre.quota
        if self._ferme or e is None:
            return
        maintenant = time.time()
        _q, jour, heure_, seuil, _dep = quota.reglages(self.fenetre.prefs)
        critique = e.alerte(seuil)
        self.v_q_utilise.mettre(f"{quota.heures(e.utilise_s)} sur {quota.heures(e.total_s)}")
        regler_niveau(self.n_quota, e.fraction, "critique" if critique else "alerte" if e.fraction >= 0.75 else "ok")
        if e.source == "kaggle":
            source = f"d’après Kaggle (« kaggle quota »), lu à {heure(e.lu_le)}"
            if e.ajoute_s >= 60:
                source += f", plus {quota.heures(e.ajoute_s)} tournées depuis"
        else:
            source = "compté sur ce poste : " + (f"Kaggle ne répond pas ({e.raison})" if e.raison else
                                                 "pas de lecture de Kaggle pour cette semaine")
        if e.en_cours_s:
            source += f" · session en cours : {quota.heures(e.en_cours_s)}"
        self.r_q_utilise.set_subtitle(commun.echapper(source))
        self.v_q_reste.mettre(quota.heures(e.reste_s), "erreur" if critique else "succes")
        self.r_q_reste.set_subtitle(commun.echapper(
            f"alerte sous {quota.heures(seuil)}" + (" : il en reste moins" if critique else "")))
        self.v_q_remise.mettre(quota.jour_heure(e.remise, maintenant))
        regle = (f"chaque {quota.regle_remise()} (donnée par Kaggle)" if e.remise_verifiee else
                 f"chaque {quota.regle_remise(jour, heure_)} : réglage, non vérifié auprès de Kaggle")
        self.r_q_remise.set_subtitle(commun.echapper(f"dans {quota.heures(e.remise - maintenant)} · {regle}"))
        # la file : ce qu'elle prendra encore, et les travaux en attente de la remise à zéro
        liste = self.fenetre.file.travaux()
        a_venir = quota.gpu_a_venir(liste, maintenant)
        attente = [t for t in liste if t.statut == "en_attente" and t.attend_le_quota]
        if attente:
            self.v_q_file.mettre(f"{len(attente)} en attente du quota", "alerte")
            self.r_q_file.set_subtitle(commun.echapper(
                f"{'part' if len(attente) == 1 else 'partent'} tout seul{'s' if len(attente) > 1 else ''} "
                f"{quota.jour_heure(min(t.reprise for t in attente), maintenant)}, même fenêtre fermée"
                + (f" · la file prendra encore environ {quota.heures(a_venir)}" if a_venir >= 60 else "")))
        else:
            self.v_q_file.mettre(f"environ {quota.heures(a_venir)} à venir" if a_venir >= 60 else "rien en attente")
            self.r_q_file.set_subtitle(commun.echapper("GPU que les travaux de la file prendront encore cette semaine"))
        # sessions comptées sur ce poste (la semaine en cours)
        for r in self._lignes_sessions:
            self.r_q_sessions.remove(r)
        self._lignes_sessions = []
        semaine = [s for s in e.sessions if s.part(e.debut_semaine, maintenant) > 0]
        for s in sorted(semaine, key=lambda x: x.debut, reverse=True)[:30]:
            origines = ", ".join(quota.LIBELLES_ORIGINE.get(o, o) for o in (s.origines or [s.origine]))
            duree = quota.heures(s.part(e.debut_semaine, maintenant))
            r = commun.ligne(s.titre or s.genre, f"{formats.date(s.debut, maintenant)} · {origines}")
            r.add_suffix(Gtk.Label(label=("≈ " if not s.mesuree else "") + duree + (" (en cours)" if s.en_cours else ""),
                                   css_classes=["dim-label", "chiffres"], valign=Gtk.Align.CENTER))
            self.r_q_sessions.add_row(r)
            self._lignes_sessions.append(r)
        # la ligne des limites reste la dernière
        self.r_q_sessions.remove(self.r_q_limites)
        self.r_q_sessions.add_row(self.r_q_limites)
        self.r_q_sessions.set_subtitle(commun.echapper(
            f"{formats.pluriel(len(semaine), 'session')}, {quota.heures(e.local_s)} cette semaine"
            + (" ; Kaggle compte aussi les autres postes et le site" if e.source == "kaggle" else "")))
        self.banniere_quota.set_title(commun.echapper(
            f"Quota GPU Kaggle : plus que {quota.heures(e.reste_s)} cette semaine, remise à zéro "
            f"{quota.jour_heure(e.remise, maintenant)}"))
        self.banniere_quota.set_revealed(critique)

    # --- poste -------------------------------------------------------------------------------------------------------

    def _lire_local(self):
        if self._local_en_cours or self._ferme:
            return
        self._local_en_cours = True
        donnees = self.fenetre.donnees

        def lire():
            return {"memoire": systeme.memoire(), "charge": systeme.charge(), "file": systeme.file_attente(),
                    "verrou": systeme.verrou_doubler(donnees),
                    "fichier_verrou": chemins.fichier_verrou_doubler(donnees), "t": time.time()}

        def echec(e):
            self._local_en_cours = False
            self.l_local.set_label(f"lecture impossible : {e}")
        en_fil(lire, self._local_lu, echec)

    def _local_lu(self, d):
        self._local_en_cours = False
        if self._ferme:
            return
        dispo, total = d["memoire"]
        if dispo is not None and total:
            utilise = 1 - dispo / total
            self.r_ram.set_subtitle(commun.echapper(
                f"{go(dispo)} disponibles sur {go(total)} ({formats.nombre(utilise * 100, 0)} % utilisés)"))
            regler_niveau(self.n_ram, utilise, "ok" if dispo >= 6 else "alerte" if dispo >= 3 else "critique")
            self.n_ram.set_visible(True)
        else:
            self.r_ram.set_subtitle("illisible (/proc/meminfo)")
            self.n_ram.set_visible(False)
        charge, coeurs = d["charge"]
        if charge is not None:
            self.r_charge.set_subtitle(commun.echapper(
                f"{formats.nombre(charge, 1)} pour {formats.pluriel(coeurs, 'cœur')}"))
            part = charge / max(1, coeurs)
            regler_niveau(self.n_charge, part, "ok" if part < 0.5 else "alerte" if part < 0.85 else "critique")
            self.n_charge.set_visible(True)
        else:
            self.r_charge.set_subtitle("illisible")
            self.n_charge.set_visible(False)
        texte = d["file"]
        self.r_file.set_subtitle(commun.echapper(texte if texte else
                                                 "file vide" if texte == "" else
                                                 "outil file-attente introuvable"))
        libelle, style, aide = VERROU.get(d["verrou"], (d["verrou"], "neutre", ""))
        self.v_verrou.mettre(libelle, style)
        self.r_verrou.set_subtitle(commun.echapper(f"{aide} · {d['fichier_verrou']}"))
        self.l_local.set_label(heure(d["t"]))

    # --- configuration (doubler, Kaggle) -----------------------------------------------------------------------------

    def _lire_config(self, suite=None):
        """config_effective (peut lancer « doubler --config --json »), options de doubler, jeton et commande kaggle."""
        if suite:
            self._suites_config.append(suite)
        if self._ferme:
            self._appeler_suites()
            return
        if self._config_en_cours:
            return
        self._config_en_cours = True

        def lire():
            conf = moteur.config_effective()
            caps = moteur.capacites()
            k = systeme.commande_kaggle()
            return {**conf, "_caps": caps, "_commande": moteur.commande(), "_disponible": moteur.disponible(),
                    "_jeton": systeme.jeton_kaggle_present(), "_kaggle": k, "_kaggle_ok": k.exists(),
                    "_noyaux": systeme.noyaux_connus(conf)}

        def fini(conf):
            self._config_en_cours = False
            self._config_lue(conf)
            self._appeler_suites()

        def echec(e):
            self._config_en_cours = False
            self.r_config.set_subtitle(commun.echapper(f"lecture impossible : {e}"))
            self._appeler_suites()
        en_fil(lire, fini, echec)

    def _appeler_suites(self):
        suites, self._suites_config = self._suites_config, []
        for s in suites:
            s(self._config)

    def _config_lue(self, c):
        if self._ferme:
            return
        self._config = c
        # Kaggle
        self.v_jeton.mettre(*(("présent", "succes") if c["_jeton"] else ("absent", "erreur")))
        self.r_cmd_kaggle.set_subtitle(commun.echapper(str(c["_kaggle"])))
        self.v_cmd_kaggle.mettre(*(("présente", "succes") if c["_kaggle_ok"] else ("absente", "erreur")))
        k = c.get("kaggle") or {}
        compte = k.get("compte")
        if compte:
            self.v_compte.mettre(str(compte))
            self.r_compte.set_subtitle("")
            self.b_reglages.set_visible(False)
        else:
            self.v_compte.mettre("non configuré", "alerte")
            self.r_compte.set_subtitle(commun.echapper(
                "À écrire avec « doubler --config kaggle.compte <nom> », ou dans les Réglages"))
            self.b_reglages.set_visible(True)
        self.v_jeu.mettre(str(k.get("jeu") or "—"))
        self._maj_noyaux(c["_noyaux"])
        # doubler
        self.r_cmd.set_subtitle(commun.echapper(shlex.join(c["_commande"])))
        self.v_cmd.mettre(*(("répond", "succes") if c["_disponible"] else ("ne répond pas", "erreur")))
        caps = c["_caps"]
        oui = [titre for opt, titre, _a in OPTIONS if opt in caps]
        non = [titre for opt, titre, _a in OPTIONS if opt not in caps]
        for opt, _t, _a in OPTIONS:
            self.v_options[opt].mettre(*(("oui", "succes") if opt in caps else ("non", "neutre")))
        resume = f"{len(oui)} sur {len(OPTIONS)}"
        if non:
            resume += f" · manque : {', '.join(non)}"
        self.r_options.set_subtitle(commun.echapper(resume))
        self.r_config.set_subtitle(commun.echapper(
            f"{c.get('config') or chemins.fichier_config()} · "
            + ("lue par doubler (--config --json)" if c.get("par_doubler") else
               "lue directement (doubler n’a pas --config --json)")))
        self.r_donnees.set_subtitle(commun.echapper(
            f"{c.get('donnees') or self.fenetre.donnees}\nsource : {c.get('source_donnees') or '—'}"))

    # --- noyaux ------------------------------------------------------------------------------------------------------

    def _maj_noyaux(self, noyaux):
        if noyaux != self._noyaux:
            for r in self._lignes_noyaux.values():
                self.g_noyaux.remove(r)
            self._lignes_noyaux = {}
            for libelle, ref in noyaux:
                r = commun.ligne(libelle, ref)
                r.set_subtitle_selectable(True)
                r.valeur = Valeur(r)
                self._lignes_noyaux[ref] = r
                self.g_noyaux.add(r)
            self._noyaux = list(noyaux)
        self.r_aucun.set_visible(not noyaux)
        for _l, ref in noyaux:
            self._peindre_noyau(ref)
        self._maj_sessions()

    def _peindre_noyau(self, ref):
        r = self._lignes_noyaux.get(ref)
        if r is None:
            return
        statut, texte, t = self._statuts.get(ref, (None, None, None))
        if t is None:
            r.valeur.mettre("non vérifié", "neutre")
            r.set_subtitle(commun.echapper(ref))
            return
        libelle, style = STATUTS.get(statut, (statut or "inconnu", "neutre")) if statut else ("inconnu", "neutre")
        r.valeur.mettre(libelle, style)
        detail = f"vérifié à {heure(t)}"
        if not statut and texte:
            detail += f" · {texte}"
        r.set_subtitle(commun.echapper(f"{ref}\n{detail}"))

    def _maj_sessions(self):
        verifies = [ref for _l, ref in self._noyaux if ref in self._statuts]
        if not verifies:
            self.v_sessions.mettre("non vérifié", "neutre")
            return
        n = sum(1 for ref in verifies if self._statuts[ref][0] in systeme.EN_COURS)
        sans = sum(1 for ref in verifies if self._statuts[ref][0] is None)
        self.v_sessions.mettre(f"{n} sur {SESSIONS_MAX}", "alerte" if n >= SESSIONS_MAX else "neutre")
        aide = f"{SESSIONS_MAX} au plus par compte ; comptées parmi les noyaux ci-dessous"
        if sans:
            aide += f" ({formats.pluriel(sans, 'noyau', 'noyaux')} sans réponse)"
        self.r_sessions.set_subtitle(commun.echapper(aide))

    def verifier_kaggle(self):
        """Une vérification à la fois : configuration relue, puis chaque noyau l'un après l'autre (≤ 120 s chacun)."""
        if self._verif or self._ferme:
            return
        self._verif = True
        self.sablier.set_visible(True)
        self.sablier.start()
        self._lire_config(lambda conf: self._verifier_suivant(list((conf or {}).get("_noyaux") or []),
                                                              bool((conf or {}).get("_jeton"))))

    def _verifier_suivant(self, restants, jeton):
        if not restants or self._ferme or not self.visible_:
            self._fin_verif(complete=not restants)
            return
        _libelle, ref = restants[0]
        r = self._lignes_noyaux.get(ref)
        if r is not None:
            r.valeur.mettre("vérification…", "neutre")

        def lire():
            if not jeton:
                return None, "jeton Kaggle absent"
            return systeme.statut_noyau(ref, timeout=120)

        def fini(res):
            self._statuts[ref] = (res[0], res[1], time.time())
            self._peindre_noyau(ref)
            self._maj_sessions()
            self._verifier_suivant(restants[1:], jeton)

        def echec(e):
            self._statuts[ref] = (None, str(e), time.time())
            self._peindre_noyau(ref)
            self._verifier_suivant(restants[1:], jeton)
        en_fil(lire, fini, echec)

    def _fin_verif(self, complete):
        self._verif = False
        if complete:
            self._derniere_verif = time.time()   # interrompue (page quittée) : refaite au prochain affichage
        if self._ferme:
            return
        self.sablier.stop()
        self.sablier.set_visible(False)
        for _l, ref in self._noyaux:
            self._peindre_noyau(ref)   # une ligne laissée « vérification… » par un arrêt en route

    # --- essais ------------------------------------------------------------------------------------------------------

    def scenario(self, action):
        """« kaggle » : vérification Kaggle ; « options » : options de doubler dépliées ; « sessions » : sessions du
        quota GPU dépliées ; suffixe « +bas » : page
        défilée jusqu'en bas (pour voir les groupes doubler et Poste sur une capture)."""
        action, bas = (action[:-4], True) if action.endswith("+bas") else (action, False)
        if action == "kaggle":
            self.verifier_kaggle()
        elif action == "options":
            self.r_options.set_expanded(True)
        elif action == "sessions":
            self.r_q_sessions.set_expanded(True)   # sessions du quota comptées sur ce poste
        if bas:
            GLib.timeout_add(1500, self._en_bas)

    def _en_bas(self):
        fen = self.vue.get_content()
        if isinstance(fen, Gtk.ScrolledWindow):
            a = fen.get_vadjustment()
            a.set_value(a.get_upper() - a.get_page_size())
        return False
