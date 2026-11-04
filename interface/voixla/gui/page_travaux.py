"""Page Travaux : la file (un seul doubler à la fois), les étapes en direct avec le temps écoulé et une estimation,
le journal complet dépliable, l'annulation, et le lien vers le résultat."""

import time
from pathlib import Path

from gi.repository import Gtk

from gi.repository import Adw

from .. import formats, suivi, systeme, travaux
from . import commun

STATUTS = {
    "en_attente": ("En attente", "neutre"), "en_cours": ("En cours", "accent"), "fini": ("Fini", "succes"),
    "echec": ("Échec", "erreur"), "annule": ("Annulé", "neutre"), "interrompu": ("Interrompu", "alerte"),
}
ICONES_ETAPE = {
    "fait": "object-select-symbolic", "saute": "content-loading-symbolic", "a_venir": "content-loading-symbolic",
    "partiel": "view-more-horizontal-symbolic", "erreur": "dialog-error-symbolic", "annule": "process-stop-symbolic",
}


def resume_reglages(demande, nom_voix=None):
    r = demande.get("reglages") or {}
    morceaux = [f"voix {nom_voix(r['voix']) if nom_voix else r['voix']}"] if r.get("voix") else []
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
    ex = demande.get("extrait")
    if ex:
        morceaux.append(f"extrait {formats.chrono(ex[0])}" + (f" + {formats.duree(ex[1])}" if ex[1] else ""))
    n = len(demande.get("videos") or [])
    if n > 1:
        morceaux.append(f"{n} vidéos, une session Kaggle")
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
        self.b_resultat = commun.bouton("Voir le résultat", styles=("suggested-action",), clic=self.voir)
        self.b_retirer = commun.bouton(icone="edit-clear-symbolic", infobulle="Retirer de la liste", styles=("flat",),
                                       clic=self.retirer)
        for b in (self.b_annuler, self.b_resultat, self.b_retirer):
            self.boutons.append(b)
        haut.append(self.boutons)
        self.append(haut)
        self.barre = Gtk.ProgressBar()
        self.append(self.barre)
        self.kaggle = Gtk.Label(xalign=0, wrap=True, css_classes=["caption"])
        self.append(self.kaggle)
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
        libelle, style = STATUTS.get(statut, (statut, "neutre"))
        self.titre.set_label(t.titre)
        self.titre.set_tooltip_text(t.titre)
        self.badge.set_label(libelle)
        for s in ("accent", "neutre", "succes", "erreur", "alerte"):
            self.badge.remove_css_class(s)
        self.badge.add_css_class(style)
        self.sous_titre.set_label(resume_reglages(t.demande, self.page.fenetre.nom_voix))
        if statut == "en_attente":
            avant = [x for x in self.page.fenetre.file.travaux() if x.actif and x.cree < t.cree]
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
        # Kaggle
        k = av.kaggle
        if k.get("statut") and statut == "en_cours":
            depuis = float(k.get("depuis_s") or 0) + max(0.0, maintenant - float(k.get("t") or maintenant))
            texte = f"GPU Kaggle : {av.statut_kaggle()} depuis {formats.duree(depuis)}"
            if k.get("estimation_s"):
                texte += f" (le noyau devrait prendre {formats.duree_courte(k['estimation_s'])})"
            self.kaggle.set_label(texte)
            self.kaggle.set_visible(True)
        else:
            self.kaggle.set_visible(False)
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
        self.b_annuler.set_visible(statut in ("en_attente", "en_cours") and not t.annulation_demandee)
        self.b_annuler.set_label("Retirer de la file" if statut == "en_attente" else "Annuler")
        self.b_resultat.set_visible(statut == "fini" and bool(self._sortie()))
        self.b_retirer.set_visible(statut in travaux.TERMINES)

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


class PageTravaux(commun.Page):
    def __init__(self, fenetre):
        super().__init__(fenetre, "Travaux", "travaux")
        self.cartes = {}
        self.entete.pack_end(commun.bouton(icone="edit-clear-all-symbolic", infobulle="Retirer les travaux terminés",
                                           clic=self.retirer_termines))
        self.banniere = Adw.Banner(button_label="Reprendre",
                                   title="Une session Kaggle a été interrompue (coupure ?) : doubler peut la finir sans "
                                         "GPU (rapatriement, mixage, vidéo)")
        self.banniere.connect("button-clicked", lambda *_: self.reprendre())
        self.vue.add_top_bar(self.banniere)
        self.pile = Gtk.Stack()
        self.vide = commun.page_vide("view-list-ordered-symbolic", "Aucun travail",
                                     "Les doublages lancés depuis « Doubler » apparaissent ici, un à la fois.",
                                     commun.bouton("Doubler une vidéo", styles=("pill", "suggested-action"),
                                                   clic=lambda: fenetre.aller("doubler")))
        self.boite = commun.boite(espace=18, marges=18)
        self.pile.add_named(self.vide, "vide")
        self.pile.add_named(commun.defile(self.boite, 820), "liste")
        self.contenu(self.pile)
        self._minuteur = None
        fenetre.ecouter("travaux", self._changement)
        self.rendre()

    def montrer(self):
        super().montrer()
        self.fenetre.suivre_travaux()
        self.rendre()
        self._demarrer_minuteur()
        self.verifier_reprise()

    def verifier_reprise(self):
        """Montre « Reprendre » si doubler a laissé une session Kaggle interrompue et qu'aucun travail ne tourne."""
        if "--reprendre" not in self.fenetre.caps or any(t.actif for t in self.fenetre.file.travaux()):
            self.banniere.set_revealed(False)
            return
        commun.en_fil(systeme.session_a_reprendre, lambda s: self.banniere.set_revealed(bool(s)))

    def reprendre(self):
        self.banniere.set_revealed(False)
        self.fenetre.ajouter_travaux([travaux.demande_reprise(self.fenetre.donnees)])
        self.fenetre.toast("Reprise de la session Kaggle ajoutée à la file")

    def cacher(self):
        super().cacher()

    def actualiser(self):
        self.fenetre.suivre_travaux()
        self.rendre()

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
