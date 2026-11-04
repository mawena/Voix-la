"""Page « Écoute à l'aveugle » : les versions A, B, C… d'un extrait du banc d'essai, écoutées et notées sans savoir
laquelle est laquelle.

La correspondance lettre → version ne s'affiche qu'après un vote sur l'extrait (ecoute.dernier_vote) ; avant, seule la
lettre est visible : ni nom de version, ni nom de fichier, ni infobulle, ni titre qui les trahirait.
Un seul lecteur pour toute la page ; passer d'une version à l'autre garde la position, pour comparer au même endroit.
"""

import json
import os
import time
from datetime import datetime

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .. import chemins, ecoute, formats
from . import commun, lecteur
from . import visualisations as vis
from .analyses import analyses

CSS = b"""
.ecoute-barre { padding: 6px 12px; }
.ecoute-joue { font-weight: bold; }
button.ecoute-active { color: @accent_color; }
"""

# Touches 1 à 5 (rangée du haut en AZERTY ou QWERTY, pavé numérique) ; 0, retour arrière ou suppr. effacent la note
TOUCHES = {}
for _note, _noms in ((1, ("1", "KP_1", "ampersand")), (2, ("2", "KP_2", "eacute")), (3, ("3", "KP_3", "quotedbl")),
                     (4, ("4", "KP_4", "apostrophe")), (5, ("5", "KP_5", "parenleft"))):
    for _nom in _noms:
        TOUCHES[Gdk.keyval_from_name(_nom)] = _note
EFFACER = {Gdk.KEY_0, Gdk.KEY_KP_0, Gdk.KEY_agrave, Gdk.KEY_BackSpace, Gdk.KEY_Delete}
NOTES_SCENARIO = (4, 2, 5, 3, 1)

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


def rang(n):
    return "1re" if n == 1 else f"{n}e"


def rangs(notes):
    """{lettre: (rang, ex_aequo)} : à note égale, même rang."""
    r = {}
    for l, n in notes.items():
        mieux = sum(1 for x in notes.values() if int(x) > int(n))
        egaux = sum(1 for x in notes.values() if int(x) == int(n))
        r[l] = (mieux + 1, egaux > 1)
    return r


def date_vote(texte):
    try:
        return formats.date(datetime.strptime(texte, "%Y-%m-%dT%H:%M:%S%z").timestamp())
    except (TypeError, ValueError):
        return texte or "—"


def structure(extraits, correspondance):
    """Empreinte de ce qui est sur le disque (pour ne reconstruire la page que si le banc a changé)."""
    if extraits is None:
        return None
    return json.dumps([[e.nom, e.titre, [(v.lettre, bool(v.voix)) for v in e.versions], bool(e.original),
                        ecoute.correspondance_de(correspondance, e.nom)] for e in extraits], ensure_ascii=False)


class BancChange(Exception):
    pass


class LecteurEcoute:
    """Un seul son à la fois. jouer(clé) sur la source déjà chargée : lecture / pause ; sur une autre source : bascule
    à la même position (sauf si la précédente était finie) et lecture."""

    def __init__(self, sur_changement, sur_erreur=None):
        self.commandes = Gtk.MediaControls(hexpand=True)
        self.flux = {}
        self.chemins = {}             # clé → fichier (pour les visualisations)
        self.cle = None
        self.sur_changement = sur_changement
        self.sur_erreur = sur_erreur
        self.sur_position = None

    def media(self):
        return self.flux.get(self.cle) if self.cle else None

    def joue(self, cle):
        m = self.media()
        return bool(m and cle == self.cle and m.get_playing())

    def _flux(self, cle, chemin):
        m = self.flux.get(cle)
        if m is None:
            m = lecteur.media(chemin)
            m.connect("notify::playing", lambda *_: self.sur_changement())
            m.connect("notify::error", self._erreur)
            m.connect("notify::timestamp", lambda *_: self.sur_position and self.sur_position())
            self.flux[cle] = m
            self.chemins[cle] = str(chemin)
        return m

    def _erreur(self, m, *_):
        e = m.get_error()
        if e and self.sur_erreur:
            self.sur_erreur(e.message)

    def charger(self, cle, chemin):
        """Rend cette source courante sans la lire (en pause, à sa position)."""
        ancien = self.media()
        if ancien is not None and cle != self.cle:
            ancien.pause()
        self.cle = cle
        self.commandes.set_media_stream(self._flux(cle, chemin))
        self.sur_changement()

    def jouer(self, cle, chemin):
        actuel = self.media()
        if cle == self.cle and actuel is not None:
            if actuel.get_playing():
                actuel.pause()
            else:
                self._lire(actuel, 0 if actuel.get_ended() else None)
            return
        position = 0
        if actuel is not None:
            if not actuel.get_ended():
                position = actuel.get_timestamp()
            actuel.pause()
        m = self._flux(cle, chemin)
        self.cle = cle
        self.commandes.set_media_stream(m)
        self._lire(m, position)
        self.sur_changement()

    @staticmethod
    def _lire(m, position):
        def placer():
            if position is not None:
                duree = m.get_duration()
                m.seek(position if position and (not duree or position < duree) else 0)
            m.play()
        lecteur.quand_pret(m, placer)

    def pause(self):
        for m in self.flux.values():
            m.pause()

    def liberer(self):
        for m in self.flux.values():
            try:
                m.pause()
                m.clear()
            except Exception:  # noqa: BLE001
                pass
        self.flux = {}
        self.cle = None
        self.commandes.set_media_stream(None)
        self.sur_changement()


class PageEcoute(commun.Page):
    def __init__(self, fenetre):
        super().__init__(fenetre, "Écoute à l’aveugle", "ecoute")
        installer_css()
        self.extraits = None          # None : pas encore lu
        self.correspondance = {}
        self.votes = []
        self.racine = chemins.dossier_ecoute()
        self.courant = None           # nom de l'extrait affiché
        self.notes = {}               # extrait → {lettre: 1–5} (en mémoire jusqu'au vote)
        self.commentaires = {}        # extrait → texte
        self.revote = set()           # extraits où l'on a demandé « Voter à nouveau »
        self.votes_factices = []      # scénario « apres-vote » : jamais écrits
        self._structure = "?"
        self._votes_lus = None
        self._chargement = False
        self._relire = False
        self._en_attente = []         # actions de scénario à jouer après le premier chargement
        self._enregistrement = False
        self.boutons = {}
        self.etoiles = {}
        self.b_enregistrer = self.l_enregistrer = None

        # Sélecteur d'extrait (en-tête)
        self.modele = Gtk.StringList()
        self.choix = Gtk.DropDown(model=self.modele, visible=False, tooltip_text="Extrait à écouter")
        self.choix.set_factory(self._fabrique(28))
        self.choix.set_list_factory(self._fabrique(None))
        self._id_choix = self.choix.connect("notify::selected", self._extrait_choisi)
        self.entete.pack_start(self.choix)

        # Lecteur partagé (barre du bas, toujours visible quand il y a des versions)
        self.lecteur = LecteurEcoute(self._maj_lecture, lambda m: self.fenetre.toast(f"Lecture impossible : {m}"))
        barre = Gtk.Box(spacing=12, css_classes=["ecoute-barre"])
        barre.append(Gtk.Image(icon_name="audio-headphones-symbolic", css_classes=["dim-label"]))
        self.joue = Gtk.Label(xalign=0, width_chars=16, max_width_chars=26, ellipsize=Pango.EllipsizeMode.END)
        barre.append(self.joue)
        barre.append(self.lecteur.commandes)
        # Visualisations (vu-mètre, spectrogramme, forme d'onde) de ce qui joue : interrupteurs du menu « œil »
        self.vis = {}
        self.pistes_vis = {}
        self.boite_vis = commun.boite(espace=6, margin_start=12, margin_end=12, margin_top=6)
        self.vue.add_bottom_bar(self.boite_vis)
        self.vue.add_bottom_bar(barre)
        menu_vis = Gio.Menu()
        for action, libelle in (("vis-vumetre", "Vu-mètre"), ("vis-spectre", "Spectrogramme"),
                                ("vis-onde", "Forme d’onde")):
            menu_vis.append(libelle, f"win.{action}")
        self.entete.pack_end(Gtk.MenuButton(icon_name="view-reveal-symbolic", menu_model=menu_vis,
                                            tooltip_text="Visualisations du son"))
        self.lecteur.sur_position = self._position_vis
        fenetre.ecouter("visualisations", self._maj_vis)
        self._maj_vis()
        self.vue.set_bottom_bar_style(Adw.ToolbarStyle.RAISED)
        self.vue.set_reveal_bottom_bars(False)

        self.contenu(Gtk.Box())
        self._maj_lecture()
        self._charger()

    @staticmethod
    def _fabrique(largeur):
        f = Gtk.SignalListItemFactory()
        f.connect("setup", lambda _f, item: item.set_child(
            Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END if largeur else Pango.EllipsizeMode.NONE,
                      max_width_chars=largeur or -1)))
        f.connect("bind", lambda _f, item: item.get_child().set_label(item.get_item().get_string()))
        return f

    # --- cycle de vie ------------------------------------------------------------------------------------------------

    def montrer(self):
        super().montrer()
        self._charger()   # le banc a pu être (re)fait pendant ce temps

    def cacher(self):
        super().cacher()
        self.lecteur.pause()

    def actualiser(self):
        self._charger()

    def fermer(self):
        self.lecteur.liberer()

    # --- lecture du banc ---------------------------------------------------------------------------------------------

    def _charger(self):
        if self._chargement:
            self._relire = True
            return
        self._chargement = True
        racine = chemins.dossier_ecoute()

        def lire():
            extraits, corr = ecoute.charger(racine)
            return racine, extraits, corr, (ecoute.lire_votes(racine) if extraits is not None else [])
        en_fil(lire, self._charge, self._echec_lecture)

    def _echec_lecture(self, e):
        self._chargement = False
        self.fenetre.toast(f"Banc d’écoute illisible : {e}")

    def _charge(self, r):
        self._chargement = False
        racine, extraits, corr, votes = r
        empreinte = structure(extraits, corr)
        reconstruire = empreinte != self._structure or json.dumps(votes) != self._votes_lus
        if empreinte != self._structure:
            if self._structure != "?":
                self.lecteur.liberer()   # fichiers peut-être refaits : on ne garde pas un son ouvert dessus
            for e in extraits or []:
                if self.extraits and ecoute.correspondance_de(self.correspondance, e.nom) != \
                        ecoute.correspondance_de(corr, e.nom):
                    # nouvelle série de lettres : les notes prises sur l'ancienne n'ont plus de sens
                    self.notes.pop(e.nom, None)
                    self.commentaires.pop(e.nom, None)
                    self.revote.discard(e.nom)
        self.racine, self.extraits, self.correspondance, self.votes = racine, extraits, corr, votes
        self._structure, self._votes_lus = empreinte, json.dumps(votes)
        if reconstruire:
            self._maj_choix()
            self._construire()
        attente, self._en_attente = self._en_attente, []
        for action in attente:
            action()
        if self._relire:
            self._relire = False
            self._charger()

    def _maj_choix(self):
        noms = [e.nom for e in self.extraits or []]
        if self.courant not in noms:
            self.courant = noms[0] if noms else None
        self.choix.handler_block(self._id_choix)
        self.modele.splice(0, self.modele.get_n_items(), [e.titre for e in self.extraits or []])
        if self.courant:
            self.choix.set_selected(noms.index(self.courant))
        self.choix.handler_unblock(self._id_choix)
        self.choix.set_visible(len(noms) > 1)

    def _extrait_choisi(self, *_):
        i = self.choix.get_selected()
        if not self.extraits or i >= len(self.extraits) or self.extraits[i].nom == self.courant:
            return
        self.lecteur.liberer()
        self.courant = self.extraits[i].nom
        self._construire()

    def _extrait(self):
        return next((e for e in self.extraits or [] if e.nom == self.courant), None)

    def _corr(self, ext):
        return ecoute.correspondance_de(self.correspondance, ext.nom)

    def _vote(self, ext):
        """Le vote qui révèle la correspondance de cet extrait, ou None (pas encore voté, ou « Voter à nouveau »)."""
        if ext.nom in self.revote:
            return None
        return ecoute.dernier_vote(self.votes + self.votes_factices, ext.nom, self._corr(ext))

    # --- construction ------------------------------------------------------------------------------------------------

    def _construire(self):
        self.boutons, self.etoiles, self.b_enregistrer, self.l_enregistrer = {}, {}, None, None
        ext = self._extrait()
        if ext is None:
            self.vue.set_reveal_bottom_bars(False)
            self.contenu(self._page_vide())
            self._maj_lecture()
            return
        self.vue.set_reveal_bottom_bars(True)
        vote = self._vote(ext)
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=18, margin_bottom=24,
                    margin_start=12, margin_end=12)
        b.append(self._groupe_extrait(ext, vote))
        b.append(self._groupe_versions(ext, vote))
        if vote:
            b.append(self._groupe_vote(ext, vote))
            b.append(self._pied(commun.bouton("Voter à nouveau", "view-refresh-symbolic", styles=("pill",),
                                              clic=lambda: self._voter_a_nouveau(ext)),
                                "Les notes repartent de zéro et les versions redeviennent anonymes ; "
                                "ce vote reste dans l’historique (votes.json)."))
        else:
            b.append(self._groupe_commentaire(ext))
            self.b_enregistrer = commun.bouton("Enregistrer mon vote", "document-save-symbolic",
                                               styles=("suggested-action", "pill"), clic=self._enregistrer)
            self.l_enregistrer = commun.legende("")
            self.l_enregistrer.set_xalign(0.5)
            self.l_enregistrer.set_justify(Gtk.Justification.CENTER)
            b.append(self._pied(self.b_enregistrer, label=self.l_enregistrer))
            self._maj_enregistrer()
        self.contenu(commun.defile(b, 760))
        self._maj_lecture()

    def _page_vide(self):
        absent = self.extraits is None
        description = ("Le banc d’essai (banc/banc.py mixer) prépare des versions A, B, C… de chaque extrait, mixées "
                       "de la même façon. Elles apparaîtront ici, anonymes, pour être écoutées et notées à l’aveugle. "
                       "Un agent est en train de les produire.")
        p = commun.page_vide("audio-headphones-symbolic",
                             "Pas encore de versions à écouter" if absent else "Aucune version dans le banc d’écoute",
                             commun.echapper(description),
                             commun.bouton("Actualiser", "view-refresh-symbolic", styles=("pill",),
                                           clic=self._charger))
        boite = p.get_child()
        if boite is not None:
            enfant = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
            p.set_child(enfant)
            enfant.append(boite)
            lu = commun.legende(f"Dossier lu : {self.racine}", selectionnable=True)
            lu.set_xalign(0.5)
            lu.set_justify(Gtk.Justification.CENTER)
            enfant.append(lu)
        return p

    def _pied(self, bouton, texte=None, label=None):
        bouton.set_halign(Gtk.Align.CENTER)
        p = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        p.append(bouton)
        if label is None and texte:
            label = commun.legende(texte)
            label.set_xalign(0.5)
            label.set_justify(Gtk.Justification.CENTER)
        if label is not None:
            p.append(label)
        return p

    def _bouton_lecture(self, cle, chemin, libelle):
        contenu = Adw.ButtonContent(label=libelle, icon_name="media-playback-start-symbolic", can_shrink=True)
        b = Gtk.Button(child=contenu, valign=Gtk.Align.CENTER)
        b.connect("clicked", lambda *_: self.lecteur.jouer(cle, chemin))
        self.boutons[cle] = (b, contenu)
        return b

    def _groupe_extrait(self, ext, vote):
        n = len(ext.versions)
        if vote:
            description = "Vous avez voté sur cet extrait : les versions sont maintenant dévoilées."
        else:
            description = (f"{formats.pluriel(n, 'version anonyme', 'versions anonymes')}. Écoutez, notez chacune "
                           "de 1 à 5, puis enregistrez votre vote : les noms ne s’affichent qu’après.")
        g = Adw.PreferencesGroup(title=commun.echapper(ext.titre), description=commun.echapper(description))
        if ext.original:
            r = commun.ligne("Original", "L’extrait d’origine, pour comparer", "audio-x-generic-symbolic")
            r.add_suffix(self._bouton_lecture((ext.nom, "original", None), ext.original, "Écouter l’original"))
            g.add(r)
        return g

    def _groupe_versions(self, ext, vote):
        aide = "Changer de version garde la position, pour comparer au même endroit."
        if not vote:
            aide += " Touches 1 à 5 sur une ligne pour la noter."
        g = Adw.PreferencesGroup(title="Versions", description=commun.echapper(aide))
        notes = {str(k): int(v) for k, v in (vote or {}).get("notes", {}).items()} if vote else \
            self.notes.setdefault(ext.nom, {})
        corr = (vote.get("correspondance") or self._corr(ext)) if vote else {}
        places = rangs(notes) if vote else {}
        for v in ext.versions:
            g.add(self._ligne_version(ext, v, notes.get(v.lettre, 0), vote, corr.get(v.lettre),
                                      places.get(v.lettre), len(ext.versions)))
        return g

    def _ligne_version(self, ext, v, note, vote, version, place, total):
        # Adw.PreferencesRow (et non Gtk.ListBoxRow) : seule une ligne de ce type va dans la carte du groupe
        rangee = Adw.PreferencesRow(activatable=False, selectable=False, title=f"Version {v.lettre}")
        h = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=12, margin_end=8)
        h.append(Gtk.Label(label=v.lettre, css_classes=["lettre"], valign=Gtk.Align.CENTER))
        milieu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True, valign=Gtk.Align.CENTER)
        boutons = Gtk.Box(spacing=6)
        boutons.append(self._bouton_lecture((ext.nom, v.lettre, "mix"), v.mix, "Écouter le mix"))
        if v.voix:
            boutons.append(self._bouton_lecture((ext.nom, v.lettre, "voix"), v.voix, "Voix seule"))
        milieu.append(boutons)
        if vote:
            info = Gtk.Box(spacing=8)
            nom = commun.badge(version or "version inconnue")
            nom.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            info.append(nom)
            if place:
                texte = f"{rang(place[0])}{' ex æquo' if place[1] else ''} sur {total}"
                info.append(commun.badge(texte, "succes") if place[0] == 1 else commun.legende(texte))
            milieu.append(info)
        h.append(milieu)
        etoiles = Gtk.Box(spacing=0, valign=Gtk.Align.CENTER)
        liste = []
        for i in range(1, 6):
            e = Gtk.Button(icon_name="non-starred-symbolic", css_classes=["flat", "etoile"],
                           tooltip_text=None if vote else formats.pluriel(i, "étoile"))
            if vote:
                e.set_can_target(False)
                e.set_focusable(False)
            else:
                e.connect("clicked", lambda _b, n=i, l=v.lettre: self._noter(ext, l, n))
            etoiles.append(e)
            liste.append(e)
        self.etoiles[v.lettre] = liste
        self._peindre(v.lettre, note)
        h.append(etoiles)
        rangee.set_child(h)
        if not vote:
            touches = Gtk.EventControllerKey()
            touches.connect("key-pressed", lambda _c, val, _code, _mod, l=v.lettre: self._touche(ext, l, val))
            rangee.add_controller(touches)
        return rangee

    def _groupe_commentaire(self, ext):
        g = Adw.PreferencesGroup()
        e = Adw.EntryRow(title="Commentaire (facultatif)", text=self.commentaires.get(ext.nom, ""))
        e.connect("changed", lambda w: self.commentaires.__setitem__(ext.nom, w.get_text()))
        g.add(e)
        return g

    def _groupe_vote(self, ext, vote):
        g = Adw.PreferencesGroup(title="Votre vote")
        g.add(commun.ligne_valeur("Enregistré", date_vote(vote.get("date"))))
        notes = {str(k): int(x) for k, x in (vote.get("notes") or {}).items()}
        corr = vote.get("correspondance") or self._corr(ext)
        if notes:
            meilleure = max(notes.values())
            tete = [l for l in ecoute.classement(notes) if notes[l] == meilleure]
            g.add(commun.ligne("En tête", " · ".join(f"{l} — {corr.get(l, 'version inconnue')}" for l in tete),
                               "starred-symbolic"))
        if (vote.get("commentaire") or "").strip():
            r = commun.ligne("Commentaire", vote["commentaire"].strip())
            r.set_subtitle_selectable(True)
            g.add(r)
        n = sum(1 for x in self.votes if x.get("extrait") == ext.nom)
        if n > 1:
            g.add(commun.ligne_valeur("Votes sur cet extrait", n, "historique gardé dans votes.json"))
        return g

    # --- notes -------------------------------------------------------------------------------------------------------

    def _peindre(self, lettre, note):
        for i, e in enumerate(self.etoiles.get(lettre, []), start=1):
            allumee = i <= note
            e.set_icon_name("starred-symbolic" if allumee else "non-starred-symbolic")
            if allumee:
                e.add_css_class("allumee")
            else:
                e.remove_css_class("allumee")

    def _noter(self, ext, lettre, note):
        if self._vote(ext):
            return
        notes = self.notes.setdefault(ext.nom, {})
        if note:
            notes[lettre] = note
        else:
            notes.pop(lettre, None)
        self._peindre(lettre, note)
        self._maj_enregistrer()

    def _touche(self, ext, lettre, val):
        if val in TOUCHES:
            self._noter(ext, lettre, TOUCHES[val])
            return True
        if val in EFFACER:
            self._noter(ext, lettre, 0)
            return True
        return False

    def _maj_enregistrer(self):
        ext = self._extrait()
        if not ext or self.b_enregistrer is None:
            return
        notees = len([v for v in ext.versions if self.notes.get(ext.nom, {}).get(v.lettre)])
        reste = len(ext.versions) - notees
        self.b_enregistrer.set_sensitive(reste == 0 and not self._enregistrement)
        self.l_enregistrer.set_label(f"Encore {formats.pluriel(reste, 'version')} à noter" if reste else
                                     "Toutes les versions sont notées.")

    # --- vote --------------------------------------------------------------------------------------------------------

    def _enregistrer(self):
        ext = self._extrait()
        if not ext or self._vote(ext) or self._enregistrement:
            return
        notes = {v.lettre: self.notes.get(ext.nom, {}).get(v.lettre) for v in ext.versions}
        if not all(notes.values()):
            return
        corr = self._corr(ext)
        commentaire = self.commentaires.get(ext.nom, "")
        racine = self.racine
        self._enregistrement = True
        self._maj_enregistrer()

        def ecrire():
            # le banc a-t-il été refait pendant l'écoute ? alors les lettres ne désignent plus les mêmes versions
            sur_disque = chemins.lire_json(racine / "correspondance.json", {}) or {}
            if ecoute.correspondance_de(sur_disque, ext.nom) != corr:
                raise BancChange()
            return ecoute.enregistrer_vote(ext.nom, notes, commentaire, corr, racine)

        def fini(vote):
            self._enregistrement = False
            self.votes.append(vote)
            self._votes_lus = json.dumps(self.votes)
            self.revote.discard(ext.nom)
            self.notes.pop(ext.nom, None)
            self.commentaires.pop(ext.nom, None)
            self.fenetre.toast(f"Vote enregistré : {ext.titre}")
            if ext.nom == self.courant:
                self._construire()

        def echec(e):
            self._enregistrement = False
            if isinstance(e, BancChange):
                self.fenetre.toast("Le banc a été refait pendant l’écoute : réécoutez les nouvelles versions",
                                   duree=8)
                self._charger()
            else:
                self.fenetre.toast(f"Vote non enregistré : {e}")
            self._maj_enregistrer()
        en_fil(ecrire, fini, echec)

    def _voter_a_nouveau(self, ext):
        self.revote.add(ext.nom)
        self.votes_factices = [v for v in self.votes_factices if v.get("extrait") != ext.nom]
        self.notes[ext.nom] = {}
        self.commentaires.pop(ext.nom, None)
        self._construire()

    # --- lecteur -----------------------------------------------------------------------------------------------------

    def _libelle(self, cle):
        nom, lettre, sorte = cle
        if lettre == "original":
            return "Original"
        texte = f"{lettre} — {'voix seule' if sorte == 'voix' else 'mix'}"
        ext = self._extrait()
        vote = self._vote(ext) if ext and ext.nom == nom else None
        if vote:
            version = (vote.get("correspondance") or self._corr(ext)).get(lettre)
            if version:
                texte += f" · {version}"
        return texte

    # --- visualisations ----------------------------------------------------------------------------------------------

    def _fournisseur(self):
        m = self.lecteur.media()
        return (m.get_timestamp() / 1e6 if m else 0.0), bool(m and m.get_playing())

    def _maj_vis(self):
        if not vis.disponible():
            return
        p = self.fenetre.prefs
        for nom, cle, classe in (("vumetre", "vis_vumetre", vis.VuMetre), ("spectre", "vis_spectre", vis.Spectre),
                                 ("onde", "vis_onde", vis.Onde)):
            if p.get(cle) and nom not in self.vis:
                w = classe() if classe is vis.VuMetre else classe(sur_clic=self._aller_vis)
                w.suivre(self._fournisseur)
                if classe is vis.VuMetre:
                    w.set_content_height(44)
                elif classe is vis.Spectre:
                    w.set_content_height(96)
                else:
                    w.set_content_height(40)
                self.vis[nom] = w
                self.boite_vis.append(w)
            elif not p.get(cle) and nom in self.vis:
                self.boite_vis.remove(self.vis.pop(nom))
        # ordre : vu-mètre, spectrogramme, onde
        precedent = None
        for nom in ("vumetre", "spectre", "onde"):
            if nom in self.vis:
                self.boite_vis.reorder_child_after(self.vis[nom], precedent)
                precedent = self.vis[nom]
        self.boite_vis.set_visible(bool(self.vis) and self.lecteur.cle is not None)
        self._appliquer_vis()

    def _aller_vis(self, t):
        m = self.lecteur.media()
        if m:
            lecteur.quand_pret(m, lambda: m.seek(int(t * 1_000_000)))

    def _position_vis(self):
        t, lecture = self._fournisseur()
        for w in self.vis.values():
            w.placer(t, lecture)

    def _appliquer_vis(self):
        if not self.vis:
            return
        cle = self.lecteur.cle
        chemin = self.lecteur.chemins.get(cle) if cle else None
        self.boite_vis.set_visible(bool(chemin))
        if not chemin:
            return
        produits = [pr for nom, pr in (("spectre", "spectre"), ("onde", "onde"), ("vumetre", "bandes"))
                    if nom in self.vis]
        d = self.pistes_vis.get(chemin)
        if d is not None and all(any(k == pr for k in d) for pr in produits):
            for w in self.vis.values():
                w.definir(d)
            self._position_vis()
            return

        def lu(r, message):
            if r.get(chemin) is not None:
                self.pistes_vis[chemin] = {**self.pistes_vis.get(chemin, {}), **r[chemin]}
                if self.lecteur.chemins.get(self.lecteur.cle) == chemin:
                    for w in self.vis.values():
                        w.definir(self.pistes_vis[chemin])
                    self._position_vis()
            elif message:
                for w in self.vis.values():
                    w.dire("Analyse du son…" if message == "attente" else message)
        analyses().pistes([chemin], produits, lu)

    def _maj_lecture(self):
        if getattr(self, "vis", None) is not None and self.lecteur.cle != getattr(self, "_cle_vis", None):
            self._cle_vis = self.lecteur.cle
            self._appliquer_vis()
        if getattr(self, "vis", None):
            self._position_vis()
        cle = self.lecteur.cle
        if cle:
            self.joue.set_label(self._libelle(cle))
            self.joue.set_css_classes(["ecoute-joue"])
        else:
            self.joue.set_label("Rien en lecture")
            self.joue.set_css_classes(["dim-label"])
        for k, (b, contenu) in self.boutons.items():
            joue = self.lecteur.joue(k)
            contenu.set_icon_name("media-playback-pause-symbolic" if joue else "media-playback-start-symbolic")
            if k == cle:
                b.add_css_class("ecoute-active")
            else:
                b.remove_css_class("ecoute-active")

    # --- essais ------------------------------------------------------------------------------------------------------

    def scenario(self, action):
        """« vote » : notes remplies ; « apres-vote » : correspondance dévoilée par un vote factice (en mémoire) ;
        « enregistrer » : vrai vote, seulement avec VOIXLA_ECOUTE ; « extraitN » : N-ième extrait ; suffixe « +bas » :
        page défilée jusqu'en bas."""
        if self.extraits is None or self._chargement:
            self._en_attente.append(lambda: self.scenario(action))
            return
        if action.endswith("+bas"):
            action = action[:-4]
            GLib.timeout_add(1000, self._en_bas)
        ext = self._extrait()
        if not ext:
            return
        notes = {v.lettre: NOTES_SCENARIO[i % len(NOTES_SCENARIO)] for i, v in enumerate(ext.versions)}
        commentaire = "B un peu sourd dans les aigus ; C la plus naturelle."
        if action == "vote":
            self.revote.add(ext.nom)
            self.notes[ext.nom] = notes
            self.commentaires[ext.nom] = commentaire
            self._construire()
            v = ext.versions[1] if len(ext.versions) > 1 else ext.versions[0]
            self.lecteur.charger((ext.nom, v.lettre, "mix"), v.mix)
        elif action == "apres-vote":
            # vote factice, en mémoire seulement : rien n'est écrit dans votes.json
            self.revote.discard(ext.nom)
            self.votes_factices.append({"extrait": ext.nom, "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                        "notes": notes, "classement": ecoute.classement(notes),
                                        "commentaire": commentaire, "correspondance": self._corr(ext)})
            self._construire()
            v = ext.versions[1] if len(ext.versions) > 1 else ext.versions[0]
            self.lecteur.charger((ext.nom, v.lettre, "mix"), v.mix)
        elif action == "enregistrer" and os.environ.get("VOIXLA_ECOUTE"):
            # écrit un vrai vote : seulement dans un banc d'essai (VOIXLA_ECOUTE), jamais dans celui du dépôt
            self.revote.add(ext.nom)
            self.notes[ext.nom] = notes
            self.commentaires[ext.nom] = commentaire
            self._construire()
            self._enregistrer()
        elif action == "joue":
            # lecture muette de la version B (captures des visualisations)
            v = ext.versions[1] if len(ext.versions) > 1 else ext.versions[0]
            self.lecteur.jouer((ext.nom, v.lettre, "mix"), v.mix)
            m = self.lecteur.media()
            if m:
                m.set_muted(True)
        elif action.startswith("extrait"):
            i = int(action[len("extrait"):] or 1) - 1
            if 0 <= i < len(self.extraits):
                self.choix.set_selected(i)

    def _en_bas(self):
        fen = self.vue.get_content()
        if isinstance(fen, Gtk.ScrolledWindow):
            a = fen.get_vadjustment()
            a.set_value(a.get_upper() - a.get_page_size())
        return False
