"""Page Travaux : la file (un seul doubler à la fois), les étapes en direct avec le temps écoulé et une estimation,
le journal complet dépliable, l'annulation, et le lien vers le résultat."""

import sys
import time
from pathlib import Path

from gi.repository import Adw, Gdk, Gtk

from .. import formats, kaggle_file, quota, suivi, systeme, travaux
from . import commun

STATUTS = {
    "en_attente": ("En attente", "neutre"), "en_cours": ("En cours", "accent"), "fini": ("Fini", "succes"),
    "echec": ("Échec", "erreur"), "annule": ("Annulé", "neutre"), "interrompu": ("Interrompu", "alerte"),
}
CSS = b"""
.banniere-reprise { background: @accent_bg_color; color: @accent_fg_color; padding: 8px 12px; }
.banniere-reprise button { min-height: 0; }
"""
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


TEXTE_ABANDON = ("La session notée sur ce PC est mise de côté : son fichier est renommé (session-abandonnee-…json, rien "
                 "n’est effacé), doubler ne la reprend plus et un nouveau doublage peut partir.\n\n"
                 "Attention : la version du notebook envoyée à Kaggle est peut-être encore en attente là-bas. Si elle "
                 "démarre, elle consommera du quota GPU. L’outil Kaggle en ligne de commande ne sait pas l’annuler : "
                 "ouvrez la page du notebook pour l’arrêter à la main.")
ICONES_ETAPE = {
    "fait": "object-select-symbolic", "saute": "content-loading-symbolic", "a_venir": "content-loading-symbolic",
    "partiel": "view-more-horizontal-symbolic", "erreur": "dialog-error-symbolic", "annule": "process-stop-symbolic",
}


def resume_reglages(demande, nom_voix=None):
    r = demande.get("reglages") or {}
    nom = (lambda v: nom_voix(v) if nom_voix else v)
    comparees = [v for v in (r.get("comparer") or []) if v]
    if len(comparees) > 1:
        # doubler --comparer : toutes les voix comparées (la carte n'en montrait que la première)
        morceaux = [f"comparaison de {len(comparees)} voix : " + ", ".join(nom(v) for v in comparees)]
    else:
        morceaux = [f"voix {nom(r['voix'])}"] if r.get("voix") else []
    if r.get("mode") and r["mode"] != "auto":
        morceaux.append(r["mode"])
    if r.get("octave") not in (None, "auto"):
        morceaux.append(f"octave {formats.demi_tons(float(r['octave']))}")
    morceaux.append(f"{r.get('pas', 30)} pas")
    if r.get("voix_db") is not None:
        morceaux.append(f"voix {formats.db(r['voix_db'])}")
    if r.get("mix_v1"):
        morceaux.append("mixage v1")
    if r.get("moteur"):
        morceaux.append({"yingmusic": "YingMusic", "seedvc": "Seed-VC"}.get(r["moteur"], r["moteur"]))
    if r.get("sans_nettoyage"):
        morceaux.append("sans nettoyage")
    if r.get("sans_paroles"):
        morceaux.append("sans paroles")
    if r.get("autotune"):
        morceaux.append(f"auto-tune {int(r['autotune'])}" + (f" ({r['autotune_gamme']})" if r.get("autotune_gamme") else ""))
    elif r.get("sans_autotune"):
        morceaux.append("sans auto-tune")
    ex = demande.get("extrait")
    if ex:
        morceaux.append(f"extrait {formats.chrono(ex[0])}" + (f" + {formats.duree(ex[1])}" if ex[1] else ""))
    n = len(demande.get("videos") or [])
    if n > 1:
        morceaux.append(f"{n} vidéos, une session Kaggle")
    decision = (demande.get("quota") or {}).get("decision")
    if decision == "lancer":
        morceaux.append("lancé malgré le quota GPU")
    if demande.get("secours"):
        morceaux.append(f"secours : {demande['secours']}")
    return " · ".join(morceaux)


class LigneEtape(Gtk.Box):
    def __init__(self, nom):
        super().__init__(spacing=10)
        self.nom = nom
        self.pile = Gtk.Stack(css_classes=["etape-icone"])
        self.icone = Gtk.Image(icon_name="content-loading-symbolic")
        self.roue = Gtk.Spinner()
        self.pile.add_named(self.icone, "icone")
        self.pile.add_named(self.roue, "roue")
        self.append(self.pile)
        texte = commun.boite(espace=0, hexpand=True)
        self.libelle = Gtk.Label(label=suivi.LIBELLES.get(nom, nom), xalign=0)
        self.message = commun.legende("")
        self.message.set_ellipsize(3)
        self.message.set_wrap(False)
        texte.append(self.libelle)
        texte.append(self.message)
        self.append(texte)
        self.temps = Gtk.Label(css_classes=["dim-label", "chiffres", "caption"], valign=Gtk.Align.CENTER)
        self.append(self.temps)

    def maj(self, e, av, maintenant):
        en_cours = e.etat == "en_cours"
        self.pile.set_visible_child_name("roue" if en_cours else "icone")
        self.roue.set_spinning(en_cours)
        self.icone.set_from_icon_name(ICONES_ETAPE.get(e.etat, "content-loading-symbolic"))
        for c in ("dim-label", "success", "error"):
            self.icone.remove_css_class(c)
        self.icone.add_css_class({"fait": "success", "erreur": "error"}.get(e.etat, "dim-label"))
        libelle = e.libelle
        if e.par_video and av.nb_videos > 1 and e.nom != "fini":
            faites = len(e.faits - {None})
            libelle += f" ({faites}/{av.nb_videos})"
        self.libelle.set_label(libelle)
        for c in ("dim-label", "heading"):
            self.libelle.remove_css_class(c)
        if e.etat in ("a_venir", "saute"):
            self.libelle.add_css_class("dim-label")
        elif en_cours:
            self.libelle.add_css_class("heading")
        self.message.set_label(e.message if (en_cours or e.etat == "erreur") and e.message else "")
        self.message.set_visible(bool(self.message.get_label()))
        if e.etat == "saute":
            self.temps.set_label("—")
        elif e.debut is not None and e.nom != "fini":
            t = formats.duree(e.ecoule_s(maintenant))
            est = av.estimations.get(e.nom)
            if en_cours and est:
                t += f" / {formats.duree_courte(est)}"
            self.temps.set_label(t)
        else:
            est = av.estimations.get(e.nom)
            self.temps.set_label(formats.duree_courte(est) if est and e.etat == "a_venir" else "")


class CarteTravail(Gtk.Box):
    def __init__(self, page, t):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["card", "carte-travail"])
        self.page, self.t = page, t
        haut = Gtk.Box(spacing=10)
        infos = commun.boite(espace=2, hexpand=True)
        ligne_titre = Gtk.Box(spacing=8)
        self.titre = Gtk.Label(xalign=0, css_classes=["title-4"], ellipsize=3, hexpand=False)
        self.badge = commun.badge("")
        ligne_titre.append(self.titre)
        ligne_titre.append(self.badge)
        infos.append(ligne_titre)
        self.sous_titre = commun.legende("")
        infos.append(self.sous_titre)
        self.temps = Gtk.Label(xalign=0, css_classes=["chiffres"], wrap=True)
        infos.append(self.temps)
        haut.append(infos)
        self.boutons = Gtk.Box(spacing=6, valign=Gtk.Align.START)
        self.b_annuler = commun.bouton("Annuler", styles=("destructive-action",), clic=self.annuler)
        self.b_maintenant = commun.bouton("Lancer maintenant", infobulle="Sans attendre la remise à zéro du quota GPU",
                                          clic=self.lancer_maintenant)
        self.b_resultat = commun.bouton("Voir le résultat", styles=("suggested-action",), clic=self.voir)
        self.b_retirer = commun.bouton(icone="edit-clear-symbolic", infobulle="Retirer de la liste", styles=("flat",),
                                       clic=self.retirer)
        for b in (self.b_maintenant, self.b_annuler, self.b_resultat, self.b_retirer):
            self.boutons.append(b)
        haut.append(self.boutons)
        self.append(haut)
        self.barre = Gtk.ProgressBar()
        self.append(self.barre)
        self.kaggle = Gtk.Label(xalign=0, wrap=True, css_classes=["caption"])
        self.append(self.kaggle)
        # refus pour session en cours, ou attente anormale d'un GPU : explication claire et issues proposées
        self.alerte = commun.boite(espace=8)
        self.alerte_texte = Gtk.Label(xalign=0, wrap=True, css_classes=["warning"])
        self.alerte.append(self.alerte_texte)
        actions = Gtk.Box(spacing=6)
        self.b_reprendre = commun.bouton("Reprendre", styles=("suggested-action",), clic=self.page.reprendre)
        self.b_abandonner = commun.bouton("Abandonner la session", styles=("destructive-action",),
                                          clic=lambda: self.page.abandonner(self.t))
        actions.append(self.b_reprendre)
        actions.append(self.b_abandonner)
        self.alerte.append(actions)
        self.append(self.alerte)
        self.etapes = commun.boite(espace=6)
        self.lignes = {}
        for nom in t.avancement.ordre:
            le = LigneEtape(nom)
            self.lignes[nom] = le
            self.etapes.append(le)
        self.append(self.etapes)
        self.erreurs = commun.boite(espace=4)
        self.append(self.erreurs)
        self.texte = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                  css_classes=["journal"], top_margin=6, bottom_margin=6, left_margin=6,
                                  right_margin=6)
        self.texte.get_buffer().set_text(t.journal)
        defil = Gtk.ScrolledWindow(child=self.texte, min_content_height=200, max_content_height=360,
                                   propagate_natural_height=True)
        self.depliant = Gtk.Expander(label="Journal complet", child=defil)
        self.append(self.depliant)
        self.maj()

    def ajouter_journal(self, texte):
        if not texte:
            return
        tampon = self.texte.get_buffer()
        tampon.insert(tampon.get_end_iter(), texte)
        if self.depliant.get_expanded():
            self.texte.scroll_to_mark(tampon.get_insert(), 0, False, 0, 1)
            fin = tampon.get_end_iter()
            tampon.place_cursor(fin)
            self.texte.scroll_to_iter(fin, 0, False, 0, 1)

    def maj(self):
        t, av = self.t, self.t.avancement
        maintenant = time.time()
        statut = t.statut
        attend_quota = statut == "en_attente" and t.attend_le_quota
        libelle, style = ("Attend le quota", "alerte") if attend_quota else STATUTS.get(statut, (statut, "neutre"))
        self.titre.set_label(t.titre)
        self.titre.set_tooltip_text(t.titre)
        self.badge.set_label(libelle)
        for s in ("accent", "neutre", "succes", "erreur", "alerte"):
            self.badge.remove_css_class(s)
        self.badge.add_css_class(style)
        self.sous_titre.set_label(resume_reglages(t.demande, self.page.fenetre.nom_voix))
        if attend_quota:
            besoin = quota.besoin_s(t.demande)
            self.temps.set_label(
                f"En attente du quota GPU : part tout seul à la remise à zéro, {quota.jour_heure(t.reprise)} (dans "
                f"{quota.heures(t.reprise - maintenant)}), même fenêtre fermée"
                + (f" · environ {quota.heures(besoin)} de GPU" if besoin else ""))
        elif statut == "en_attente":
            avant = [x for x in self.page.fenetre.file.travaux()
                     if x.actif and x.cree < t.cree and not x.attend_le_quota]
            est = sum((av.estimations or {}).values()) if av.estimations else None
            self.temps.set_label(f"Attend son tour ({formats.pluriel(len(avant), 'travail', 'travaux')} avant lui)"
                                 + (f" · durée prévue {formats.duree_courte(est)}" if est else ""))
        elif statut == "en_cours":
            reste = av.restant_s(maintenant)
            texte = f"Commencé à {time.strftime('%H:%M', time.localtime(av.debut or maintenant))} · " \
                    f"{formats.duree(av.ecoule_s(maintenant))} écoulées"
            if reste is not None:
                texte += f" · reste {formats.duree_courte(reste)}"
            if t.annulation_demandee:
                texte += " · annulation en cours…"
            self.temps.set_label(texte)
        else:
            fin = t.etat.get("fin") or av.fin
            texte = f"{libelle} {formats.date(fin)}" if fin else libelle
            if av.debut and fin:
                texte += f" · en {formats.duree(fin - av.debut)}"
            self.temps.set_label(texte)
        # barre d'avancement
        reste = av.restant_s(maintenant)
        ecoule = av.ecoule_s(maintenant)
        if statut == "fini":
            self.barre.set_fraction(1)
        elif statut == "en_cours" and reste is not None and ecoule + reste > 0:
            self.barre.set_fraction(min(0.99, ecoule / (ecoule + reste)))
        elif statut == "en_attente":
            self.barre.set_fraction(0)
        self.barre.set_visible(statut in ("en_cours", "en_attente", "fini"))
        # Kaggle : une fois le noyau lancé, l'estimation de doubler (le temps restant la suit, voir suivi.restant_s)
        texte = av.texte_kaggle(maintenant) if statut == "en_cours" else None
        self.kaggle.set_label(texte or "")
        self.kaggle.set_visible(bool(texte))
        self._maj_alerte(statut, maintenant)
        for nom, le in self.lignes.items():
            le.maj(av.etapes[nom], av, maintenant)
        # étapes : pendant le travail, et après un échec (pour voir où) ; un travail fini se résume en une ligne
        self.etapes.set_visible(statut in ("en_cours", "echec", "interrompu"))
        self.depliant.set_visible(statut != "en_attente" or bool(t.journal))
        commun.vider(self.erreurs)
        for e in av.erreurs[-4:]:
            self.erreurs.append(commun.legende(f"⚠ {e}", erreur=True, selectionnable=True))
        if statut == "annule" and av.kaggle_parti():
            self.erreurs.append(commun.legende("La session Kaggle déjà partie a continué seule ; son résultat n’a pas "
                                               "été rapatrié."))
        self.b_maintenant.set_visible(attend_quota and not t.annulation_demandee)
        self.b_annuler.set_visible(statut in ("en_attente", "en_cours") and not t.annulation_demandee)
        self.b_annuler.set_label("Retirer de la file" if statut == "en_attente" else "Annuler")
        self.b_resultat.set_visible(statut == "fini" and bool(self._sortie()))
        self.b_retirer.set_visible(statut in travaux.TERMINES)

    def _maj_alerte(self, statut, maintenant):
        """Refus pour session en cours (doublage échoué), attente anormale d'un GPU, reprise en cours ou annulée :
        le texte clair et les boutons « Reprendre » / « Abandonner la session » (tant que la session existe)."""
        t, page = self.t, self.page
        session = page.session is not None
        texte, reprendre = "", False
        attente = travaux.attente_anormale(t.avancement, maintenant) if statut == "en_cours" else None
        if t.refus_session:
            reprendre = session
            texte = ("Doublage refusé : une session Kaggle précédente n’est pas finie, et doubler n’en lance pas une "
                     "nouvelle tant qu’elle existe (le nouveau noyau effacerait son résultat). "
                     + ("Reprenez-la pour la finir sans GPU, ou abandonnez-la."
                        if session else "Elle n’existe plus : relancez le doublage."))
        elif attente:
            texte = travaux.texte_attente_anormale(formats.duree(attente))
        elif t.est_reprise and statut == "annule" and session:
            texte = "Reprise annulée : la session Kaggle reste à finir ou à abandonner."
            reprendre = True
        peut_abandonner = session and statut != "fini" and (t.est_reprise or t.refus_session or bool(attente))
        self.alerte_texte.set_label(texte)
        self.alerte_texte.set_visible(bool(texte))
        self.b_reprendre.set_visible(reprendre)
        self.b_abandonner.set_visible(peut_abandonner)
        self.alerte.set_visible(bool(texte) or peut_abandonner)

    def _sortie(self):
        for d in self.t.demande.get("sorties") or []:
            if Path(d).is_dir():
                return Path(d)
        return None

    def voir(self):
        d = self._sortie()
        if d:
            self.page.fenetre.ouvrir_resultat(d, (self.t.demande.get("reglages") or {}).get("voix"))

    def retirer(self):
        self.page.fenetre.file.retirer(self.t)
        self.page.rendre()

    def lancer_maintenant(self):
        """Un travail en attente du quota part dès que la file le permet, sans attendre la remise à zéro."""
        f = self.page.fenetre
        e = f.quota
        besoin = quota.besoin_s(self.t.demande)
        corps = "Il partira dès que la file le permet, sans attendre la remise à zéro du quota GPU."
        if e is not None:
            corps += (f" Il prendra environ {quota.heures(besoin)} de GPU ; il en reste {quota.heures(e.reste_s)} "
                      "cette semaine.")

        def faire():
            if f.file.lancer_maintenant(self.t):
                f.toast("Le doublage part dès que la file le permet")
            f.suivre_travaux()
            self.maj()
        commun.confirmer(f, "Lancer sans attendre le quota ?", corps, "Lancer maintenant", faire, destructif=False)

    def annuler(self):
        t = self.t
        if t.statut == "en_attente":
            corps = "Ce doublage n’a pas commencé : il est simplement retiré de la file."
            libelle = "Retirer"
        elif t.avancement.kaggle_parti():
            corps = ("Le doublage s’arrête sur ce PC (le groupe de processus de doubler est arrêté). "
                     "La session Kaggle déjà partie continue SEULE jusqu’à la fin : elle occupe une des deux places GPU "
                     "du compte pendant ce temps, et son résultat ne sera pas rapatrié.")
            libelle = "Annuler le doublage"
        else:
            corps = "Le doublage s’arrête sur ce PC. Rien n’est encore parti sur le GPU de Kaggle."
            libelle = "Annuler le doublage"

        def faire():
            self.page.fenetre.file.annuler(t)
            self.page.fenetre.toast("Annulation demandée")
            self.page.fenetre.suivre_travaux()
            self.maj()
        commun.confirmer(self.page.fenetre, "Annuler ce doublage ?", corps, libelle, faire, libelle_annuler="Continuer")


class BanniereReprise(Gtk.Revealer):
    """Bannière « session Kaggle interrompue » avec ses deux issues : Reprendre, ou Abandonner la session
    (Adw.Banner n'a qu'un bouton)."""

    def __init__(self, reprendre, abandonner):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        boite = Gtk.Box(spacing=8, css_classes=["banniere-reprise"])
        self.titre = Gtk.Label(
            label="Une session Kaggle a été interrompue (coupure ?) : doubler peut la finir sans GPU (rapatriement, "
                  "mixage, vidéo), ou vous pouvez l’abandonner.", wrap=True, xalign=0, hexpand=True)
        boite.append(self.titre)
        boite.append(commun.bouton("Abandonner la session", clic=abandonner, valign=Gtk.Align.CENTER))
        boite.append(commun.bouton("Reprendre", clic=reprendre, valign=Gtk.Align.CENTER))
        self.set_child(boite)

    def set_revealed(self, v):
        self.set_reveal_child(bool(v))


class LigneKaggle(Gtk.Box):
    """Une ligne de la section « Sur Kaggle » : titre, badge « Voix-là », état en clair, depuis quand, et un bouton qui
    ouvre la page du notebook sur Kaggle (la ligne de commande ne sait pas arrêter une session)."""

    def __init__(self, page, n, maintenant):
        super().__init__(spacing=10, css_classes=["ligne-kaggle"])
        texte = commun.boite(espace=2, hexpand=True)
        haut = Gtk.Box(spacing=8)
        titre = Gtk.Label(label=n.titre, xalign=0, ellipsize=3, css_classes=["heading"] if n.actif else [])
        haut.append(titre)
        if n.voixla:
            haut.append(commun.badge("Voix-là", "accent"))
        texte.append(haut)
        detail = f"{kaggle_file.depuis(n, maintenant)} · {n.ref}"
        if n.erreur:
            detail += f" · {n.erreur}"
        sous = commun.legende(detail)
        sous.set_ellipsize(3)
        sous.set_wrap(False)
        texte.append(sous)
        self.append(texte)
        self.append(commun.badge(n.libelle, n.style))
        url = n.url
        b = commun.bouton(icone="web-browser-symbolic", valign=Gtk.Align.CENTER, styles=("flat",),
                          infobulle=f"Ouvrir la page du notebook sur Kaggle ({url})" if url else "Adresse inconnue",
                          clic=(lambda: page.ouvrir_notebook(url)) if url else None)
        b.set_sensitive(bool(url))
        self.append(b)


class SectionKaggle(Gtk.Box):
    """Section « Sur Kaggle » : ce qui attend ou tourne sur le compte (kaggle_file), les terminés récents en repli,
    une phrase d'aide, et « Actualiser » avec l'heure de la dernière lecture."""

    def __init__(self, page):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=14, margin_bottom=4,
                         margin_start=18, margin_end=18)
        self.page = page
        entete = Gtk.Box(spacing=8)
        entete.append(Gtk.Label(label="Sur Kaggle", xalign=0, css_classes=["heading"]))
        self.roue = Gtk.Spinner(visible=False)
        entete.append(self.roue)
        self.heure = commun.legende("")
        self.heure.set_hexpand(True)
        entete.append(self.heure)
        self.b_actualiser = commun.bouton("Actualiser", icone="view-refresh-symbolic", styles=("flat",),
                                          infobulle="Relire l’état des notebooks du compte (au plus toutes les "
                                                    f"{kaggle_file.AGE_MIN_S} s)", clic=page.actualiser_kaggle)
        entete.append(self.b_actualiser)
        self.append(entete)
        self.actifs = commun.boite(espace=6)
        self.append(self.actifs)
        self.rien = commun.legende("")
        self.append(self.rien)
        self.termines = commun.boite(espace=6, margin_top=6)
        self.repli = Gtk.Expander(label="Terminés récents", child=self.termines)
        self.append(self.repli)
        self.aide = commun.legende("")
        self.append(self.aide)

    def peindre(self, lecture, en_cours=False, maintenant=None):
        maintenant = time.time() if maintenant is None else maintenant
        self.roue.set_visible(en_cours)
        self.roue.set_spinning(en_cours)
        commun.vider(self.actifs)
        commun.vider(self.termines)
        if lecture is None:
            self.heure.set_label("lecture en cours…" if en_cours else "")
            self.rien.set_label("Lecture des notebooks du compte Kaggle…")
            self.rien.set_visible(True)
            self.repli.set_visible(False)
            self.aide.set_label(kaggle_file.aide(None))
            return
        self.heure.set_label(f"lu à {kaggle_file.heure_lecture(lecture)}")
        if not lecture.ok:
            self.rien.set_label(f"Kaggle indisponible : {lecture.indisponible}")
            self.rien.add_css_class("error")
            self.rien.set_visible(True)
            self.repli.set_visible(False)
            self.aide.set_label("")
            return
        self.rien.remove_css_class("error")
        actifs, termines = lecture.actifs, lecture.termines
        for n in actifs:
            self.actifs.append(LigneKaggle(self.page, n, maintenant))
        texte = ""
        if not actifs:
            texte = "Rien en attente ni en cours sur le compte Kaggle."
        if lecture.partielle:
            texte = (texte + " " if texte else "") + "Certains états n’ont pas pu être lus."
        self.rien.set_label(texte)
        self.rien.set_visible(bool(texte))
        self.repli.set_visible(bool(termines))
        self.repli.set_label(f"Terminés récents ({len(termines)})")
        for n in termines:
            self.termines.append(LigneKaggle(self.page, n, maintenant))
        self.aide.set_label(kaggle_file.aide(lecture))


class PageTravaux(commun.Page):
    def __init__(self, fenetre):
        super().__init__(fenetre, "Travaux", "travaux")
        installer_css()
        self.cartes = {}
        self.session = None   # contenu de session-en-cours.json si elle existe (relu par verifier_reprise)
        self.entete.pack_end(commun.bouton(icone="edit-clear-all-symbolic", infobulle="Retirer les travaux terminés",
                                           clic=self.retirer_termines))
        self.banniere = BanniereReprise(self.reprendre, lambda: self.abandonner(None))
        self.vue.add_top_bar(self.banniere)
        self.pile = Gtk.Stack()
        self.vide = commun.page_vide("view-list-ordered-symbolic", "Aucun travail",
                                     "Les doublages lancés depuis « Doubler » apparaissent ici, un à la fois.",
                                     commun.bouton("Doubler une vidéo", styles=("pill", "suggested-action"),
                                                   clic=lambda: fenetre.aller("doubler")))
        self.boite = commun.boite(espace=18, marges=18)
        self.pile.add_named(self.vide, "vide")
        self.pile.add_named(commun.defile(self.boite, 820), "liste")
        self.kaggle = SectionKaggle(self)
        self.kaggle_lecture = kaggle_file.SUIVI.derniere
        self._kaggle_en_cours = False
        self._tics = 0
        corps = commun.boite(espace=0)
        # même largeur et même alignement que les cartes des travaux (commun.defile(self.boite, 820))
        corps.append(Adw.Clamp(maximum_size=820, tightening_threshold=int(820 * 0.8), child=self.kaggle))
        corps.append(self.pile)
        self.contenu(corps)
        self.kaggle.peindre(self.kaggle_lecture)
        self._minuteur = None
        fenetre.ecouter("travaux", self._changement)
        self.rendre()

    def montrer(self):
        super().montrer()
        self.fenetre.suivre_travaux()
        self.rendre()
        self._demarrer_minuteur()
        self.verifier_reprise()
        self.lire_kaggle()

    # --- file de Kaggle (voir kaggle_file : au plus un tour par minute, hors du fil de l'interface) ---------------------

    def lire_kaggle(self, forcer=False):
        """Relit les notebooks du compte si le cache est trop vieux (page visible ou travail Voix-là en cours)."""
        if self._kaggle_en_cours:
            return
        actif = any(t.actif for t in self.fenetre.file.travaux())
        visible = self.visible_
        if not kaggle_file.SUIVI.a_relire(visible, actif, forcer):
            if forcer:
                lu = kaggle_file.SUIVI.derniere
                self.fenetre.toast(f"Déjà lu à {kaggle_file.heure_lecture(lu)} : patientez quelques secondes")
            return
        self._kaggle_en_cours = True
        self.kaggle.peindre(self.kaggle_lecture, en_cours=True)

        def lire():
            return kaggle_file.SUIVI.lire_si_besoin(visible, actif, forcer)

        def fini(r):
            self._kaggle_en_cours = False
            self.kaggle_lecture = r[0] if r[0] is not None else self.kaggle_lecture
            self.kaggle.peindre(self.kaggle_lecture)

        def echec(e):
            self._kaggle_en_cours = False
            print(f"file Kaggle illisible : {e}", file=sys.stderr)
            self.kaggle.peindre(self.kaggle_lecture)
        commun.en_fil(lire, fini, echec)

    def actualiser_kaggle(self):
        self.lire_kaggle(forcer=True)

    def verifier_reprise(self):
        """Montre « Reprendre » et « Abandonner la session » si doubler a laissé une session Kaggle interrompue et
        qu'aucun travail ne tourne. Relit aussi la session pour les cartes (abandon d'un travail de reprise ou refusé)."""
        def lire():
            return systeme.session_existante(), systeme.session_a_reprendre()

        def appliquer(r):
            self.session, a_reprendre = r
            actif = any(t.actif for t in self.fenetre.file.travaux())
            self.banniere.set_revealed(bool(a_reprendre) and "--reprendre" in self.fenetre.caps and not actif)
            for c in self.cartes.values():
                c.maj()
        commun.en_fil(lire, appliquer)

    def reprendre(self):
        self.banniere.set_revealed(False)
        self.fenetre.ajouter_travaux([travaux.demande_reprise(self.fenetre.donnees)])
        self.fenetre.toast("Reprise de la session Kaggle ajoutée à la file")

    def abandonner(self, t=None):
        """« Abandonner cette session » : confirmation (Adw.AlertDialog), puis annulation d'un éventuel travail de reprise
        encore actif, et renommage du fichier de session (voir systeme.abandonner_session). t : le travail d'où l'on
        vient (bannière : None)."""
        f = self.fenetre
        session = self.session or systeme.session_existante() or {}
        url = systeme.url_noyau(session)
        reprises = [x for x in f.file.travaux() if x.est_reprise and x.actif]
        if t is not None and t.actif and t not in reprises:
            reprises.append(t)
        corps = TEXTE_ABANDON
        if reprises:
            corps = "Le travail en cours qui suit cette session est d’abord annulé.\n\n" + corps
        d = Adw.AlertDialog(heading="Abandonner cette session Kaggle ?", body=corps)
        d.add_response("annuler", "Garder la session")
        d.add_response("ok", "Abandonner la session")
        d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("annuler")
        d.set_close_response("annuler")
        if url:
            ouvrir = commun.bouton("Ouvrir la page du notebook sur Kaggle", icone="web-browser-symbolic",
                                   infobulle=url, clic=lambda: self.ouvrir_notebook(url), halign=Gtk.Align.CENTER)
            d.set_extra_child(ouvrir)
        d.connect("response", lambda _d, r: r == "ok" and self._faire_abandon(reprises))
        d.present(f)
        return d

    def ouvrir_notebook(self, url):
        Gtk.UriLauncher.new(url).launch(self.fenetre, None, None, None)

    def _faire_abandon(self, reprises):
        f = self.fenetre
        for x in reprises:   # le mécanisme d'annulation habituel, dans le fil de l'interface
            if not x.annulation_demandee:
                f.file.annuler(x)
        f.suivre_travaux()
        if reprises:
            f.toast("Annulation du travail en cours, puis abandon de la session…")
        commun.en_fil(lambda: systeme.abandonner_session(attente_s=20.0 if reprises else 0), self._abandon_fait)

    def _abandon_fait(self, r):
        etat, detail = r
        f = self.fenetre
        if etat == "fait":
            self.session = None
            f.toast(f"Session abandonnée : fichier gardé sous le nom {Path(detail).name}", duree=8)
        elif etat == "suivie":
            f.toast("Un doubler suit encore cette session : rien n’a été renommé", duree=8)
        elif etat == "absente":
            self.session = None
            f.toast("Il n’y a plus de session à abandonner")
        else:
            f.toast(f"Abandon impossible : {detail}", duree=8)
        self.verifier_reprise()
        self.rendre()

    def cacher(self):
        super().cacher()

    def actualiser(self):
        self.fenetre.suivre_travaux()
        self.rendre()
        self.lire_kaggle(forcer=True)

    def _demarrer_minuteur(self):
        from gi.repository import GLib
        if self._minuteur is None:
            self._minuteur = GLib.timeout_add_seconds(1, self._tic)

    def _tic(self):
        if not self.visible_:
            self._minuteur = None
            return False
        for c in self.cartes.values():
            if c.t.actif:
                c.maj()
        self._tics += 1
        if self._tics % 30 == 0 and self.kaggle_lecture is not None and not self._kaggle_en_cours:
            self.kaggle.peindre(self.kaggle_lecture)   # remet à jour les « lancé il y a… »
        self.lire_kaggle()
        return True

    def _changement(self, t=None, nouveau=None):
        if t is not None and t.statut in travaux.TERMINES and self.visible_:
            self.verifier_reprise()
        if t is None or t.id not in self.cartes:
            self.rendre()
            return
        c = self.cartes[t.id]
        c.ajouter_journal(nouveau)
        c.maj()

    def rendre(self):
        liste = self.fenetre.file.travaux()
        self.pile.set_visible_child_name("liste" if liste else "vide")
        ids = [t.id for t in liste]
        for ident in list(self.cartes):
            if ident not in ids:
                self.cartes.pop(ident)
        commun.vider(self.boite)
        actifs = [t for t in liste if t.actif]
        termines = [t for t in reversed(liste) if not t.actif][:30]
        for titre, groupe in (("En cours et en attente", sorted(actifs, key=lambda t: (t.statut != "en_cours", t.cree))),
                              ("Terminés", termines)):
            if not groupe:
                continue
            self.boite.append(Gtk.Label(label=titre, xalign=0, css_classes=["heading"]))
            for t in groupe:
                if t.id not in self.cartes:
                    self.cartes[t.id] = CarteTravail(self, t)
                c = self.cartes[t.id]
                c.t = t
                c.maj()
                self.boite.append(c)

    def retirer_termines(self):
        n = 0
        for t in self.fenetre.file.travaux():
            if t.statut in travaux.TERMINES and self.fenetre.file.retirer(t):
                n += 1
        self.rendre()
        self.fenetre.toast(f"{formats.pluriel(n, 'travail retiré', 'travaux retirés')}" if n else "Rien à retirer")

    def scenario(self, action):
        if action == "journal":
            for c in self.cartes.values():
                c.depliant.set_expanded(True)
                break
        elif action == "kaggle":   # la section « Sur Kaggle » avec les terminés récents dépliés
            self.kaggle.repli.set_expanded(True)
        elif action == "abandon":   # le dialogue « Abandonner cette session » (rien n'est renommé sans confirmation)
            self.abandonner(None)
