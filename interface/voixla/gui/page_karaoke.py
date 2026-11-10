"""Page Karaoké : chanter soi-même sur le beat seul d'un doublage, les paroles qui défilent comme dans le lecteur, puis
la note de justesse, la courbe « ta voix contre l'original », les lignes fausses, la réécoute (ta voix seule ou avec le
beat), garder ou jeter la prise, et le mix en MP3.

- La prise (PriseKaraoke) réutilise l'enregistreur des voix (gui/enregistreur.Enregistrement : micro du PC, niveau en
  direct, WAV 48 kHz 24 bits) ; le beat est ajouté au même pipeline GStreamer, pour qu'une seule horloge règle les deux.
  Le beat est d'abord mis en place (PAUSED), puis tout part ensemble à la fin du décompte. Deux mesures de calage sont
  prises (voir voixla/karaoke.py) ; la latence du matériel se règle à la main ou par les bips (DialogueCalibration).
- Le micro du téléphone n'est pas proposé : scrcpy ne commence la prise qu'une fois le téléphone joint, avec un retard
  qui change d'une fois à l'autre ; le chant ne tomberait pas sur le beat.
- La note et le mix tournent hors de l'interface (python3 -m voixla.karaoke, sous nice, par file-attente au-delà d'une
  minute de son), dans la file des analyses (gui/analyses.py), comme les autres analyses du son.
- Essais : VOIXLA_MICRO_ESSAI=1 remplace le micro par un son de synthèse et le haut-parleur par fakeaudiosink (aucun son
  joué) ; les captures (VOIXLA_CAPTURE) le posent d'office.
"""

import math
import os
import shutil
import time
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Adw, Gdk, GLib, Gst, Gtk, Pango, PangoCairo  # noqa: E402

from .. import analyse, chemins, formats, justesse, moteur, quota, rapports, travaux  # noqa: E402
from .. import karaoke as K  # noqa: E402
from . import commun  # noqa: E402
from .analyses import analyses, derniere_ligne  # noqa: E402
from .enregistreur import Enregistrement, sources_micro  # noqa: E402
from .lecteur import media  # noqa: E402
from .lecture import Apercu  # noqa: E402
from .visualisations import accent  # noqa: E402
from .vue_paroles import Karaoke  # noqa: E402

DECOMPTE_S = 3
QUEUE_S = 0.6                 # après la fin du beat, on enregistre encore la latence et un peu plus
MODES = (("entier", "Chanson entière"), ("extrait", "Extrait"), ("boucle", "Extrait en boucle"))
DEFAUT = {"latence_ms": 0, "micro": None, "repetitions": 3, "derniere": ""}
ROUGE = (0.88, 0.11, 0.14)

CSS = b"""
.score-karaoke { font-size: 4.2em; font-weight: 800; font-feature-settings: "tnum"; }
.score-sur { font-size: 1.4em; font-weight: 700; opacity: 0.55; }
.score-karaoke.bon { color: @success_color; }
.score-karaoke.moyen { color: @warning_color; }
.score-karaoke.faible { color: @error_color; }
.decompte-karaoke { font-size: 7em; font-weight: 800; padding: 4px 36px; border-radius: 24px; }
.ligne-fausse .title { color: @error_color; }
.carte-karaoke { padding: 14px 18px; }
"""


def reglages(prefs):
    """Les préférences du karaoké (clé « karaoke » d'interface.json), complétées des valeurs par défaut."""
    lu = prefs.get("karaoke") if isinstance(prefs.get("karaoke"), dict) else {}
    return {**DEFAUT, **{k: v for k, v in lu.items() if k in DEFAUT}}


def _installer_css():
    if getattr(_installer_css, "fait", False) or Gdk.Display.get_default() is None:
        return
    f = Gtk.CssProvider()
    f.load_from_data(CSS, -1)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), f, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _installer_css.fait = True


def _muet():
    return bool(os.environ.get("VOIXLA_MICRO_ESSAI"))


# --- La prise : le beat et le micro dans un même pipeline -------------------------------------------------------------

class PriseKaraoke(Enregistrement):
    """L'enregistrement du micro (Enregistrement, inchangé) avec, dans le même pipeline, la lecture du beat préparé
    (filesrc → wavparse → sortie son). Mesures : prise_debut_s, l'instant (horloge du pipeline) du premier échantillon
    enregistré ; beat_debut_s, l'instant où le début du beat sort de GStreamer (médiane des positions relevées pendant
    la prise). sur_fin_beat() quand le beat est fini."""

    def __init__(self, fichier, beat, micro="", sur_niveau=None, sur_fin=None, sur_fin_beat=None):
        super().__init__(fichier, micro, 24, sur_niveau=sur_niveau, sur_fin=sur_fin)
        sortie = "fakeaudiosink sync=true" if _muet() else "autoaudiosink"
        self.branche = Gst.parse_bin_from_description(
            f"filesrc name=beat-fichier ! wavparse ! audioconvert ! audioresample ! {sortie} name=beat-sortie", False)
        self.branche.get_by_name("beat-fichier").set_property("location", str(beat))
        self.pipeline.add(self.branche)
        self.sortie = self.branche.get_by_name("beat-sortie")
        self.sur_fin_beat = sur_fin_beat
        self.prise_debut_s = None
        self.beat_pret = False
        self.beat_fini = False
        self._ecarts = []
        self._sonder()

    def _sonder(self):
        fichier = self.pipeline.get_by_name("fichier")
        pair = fichier.get_static_pad("sink").get_peer() if fichier is not None else None
        wavenc = pair.get_parent_element() if pair is not None else None
        pad = wavenc.get_static_pad("sink") if wavenc is not None else None
        if pad is not None:
            pad.add_probe(Gst.PadProbeType.BUFFER, self._premier_echantillon)
        entree = self.sortie.get_static_pad("sink")
        if entree is not None:
            entree.add_probe(Gst.PadProbeType.BUFFER, self._premier_beat)
            entree.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, self._evenement_beat)

    @staticmethod
    def _temps_courant(pad, pts):
        ev = pad.get_sticky_event(Gst.EventType.SEGMENT, 0)
        if ev is None:
            return pts
        r = ev.parse_segment().to_running_time(Gst.Format.TIME, pts)
        return pts if r in (-1, Gst.CLOCK_TIME_NONE) else r

    def _premier_echantillon(self, pad, info):
        buf = info.get_buffer()
        if buf is None or buf.pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK
        self.prise_debut_s = self._temps_courant(pad, buf.pts) / Gst.SECOND
        return Gst.PadProbeReturn.REMOVE

    def _premier_beat(self, _pad, _info):
        self.beat_pret = True
        return Gst.PadProbeReturn.REMOVE

    def _evenement_beat(self, _pad, info):
        ev = info.get_event()
        if ev is not None and ev.type == Gst.EventType.EOS and not self.beat_fini:
            self.beat_fini = True
            GLib.idle_add(lambda: (self.sur_fin_beat() if self.sur_fin_beat and not self.termine else None,
                                   False)[1])
        return Gst.PadProbeReturn.OK

    def preparer(self):
        """Le beat se met en place (PAUSED : son premier morceau attend dans la sortie), sans rien enregistrer."""
        if self.pipeline.set_state(Gst.State.PAUSED) == Gst.StateChangeReturn.FAILURE:
            self._finir("la sortie son ou le micro n’a pas pu s’ouvrir")
            return False
        return True

    def position_beat(self):
        """Ce qu'on entend du beat (s, temps du beat lu), ou None."""
        ok, pos = self.sortie.query_position(Gst.Format.TIME)
        return pos / Gst.SECOND if ok and 0 <= pos < Gst.CLOCK_TIME_NONE else None

    def mesurer(self):
        """Relève l'écart entre l'horloge du pipeline et la position du beat (l'instant où son début est sorti)."""
        horloge = self.pipeline.get_clock()
        ok, pos = self.sortie.query_position(Gst.Format.TIME)
        if horloge is None or not ok or pos <= 0.2 * Gst.SECOND or pos >= Gst.CLOCK_TIME_NONE:
            return
        rt = horloge.get_time() - self.pipeline.get_base_time()
        self._ecarts.append((rt - pos) / Gst.SECOND)

    @property
    def beat_debut_s(self):
        if self._ecarts:
            e = sorted(self._ecarts)
            return e[len(e) // 2]
        q = Gst.Query.new_latency()
        if self.pipeline.query(q):
            _live, minimum, _max = q.parse_latency()
            return minimum / Gst.SECOND
        return 0.0

    def mesure(self):
        return {"prise_debut_s": round(self.prise_debut_s or 0.0, 5), "beat_debut_s": round(self.beat_debut_s, 5),
                "releves": len(self._ecarts)}


# --- Courbe « ta voix contre l'original » ----------------------------------------------------------------------------

class CourbeJustesse(Gtk.DrawingArea):
    """La mélodie de l'original (trait fin) et la tienne (trait épais, couleur d'accent ; rouge là où c'est faux), sur
    une échelle de notes ; les lignes en bandes alternées ; une fenêtre de temps (tout le passage, ou une ligne) ; un
    curseur pendant la réécoute ; un clic va à cet instant (sur_clic(temps de la chanson))."""

    def __init__(self, sur_clic=None):
        super().__init__(content_height=230, hexpand=True)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Ta voix contre l’original"])
        self.sur_clic = sur_clic
        self.res = None
        self.tour = None
        self.t0 = self.t1 = 0.0
        self.position = None
        self.set_draw_func(self._dessiner)
        if sur_clic is not None:
            g = Gtk.GestureClick()
            g.connect("pressed", lambda _g, _n, x, _y: self._clic(x))
            self.add_controller(g)
            self.set_cursor_from_name("pointer")

    def definir(self, res, tour):
        self.res, self.tour = res, tour
        self.ref = [float(x) for x in res.get("reference_hz") or []]
        self.prise = [float(x) for x in (tour or {}).get("prise_hz") or []]
        self.pas = float(res.get("pas_s") or justesse.PAS_S)
        self.debut = float(res.get("debut_s") or 0.0)
        voisees = [x for x in self.ref + self.prise if x > 0]
        if len(voisees) > 10:
            voisees.sort()
            lo, hi = voisees[int(0.02 * len(voisees))], voisees[int(0.98 * (len(voisees) - 1))]
        else:
            lo, hi = 150.0, 450.0
        self.m_lo = 69 + 12 * math.log2(max(40.0, lo) / 440) - 2
        self.m_hi = 69 + 12 * math.log2(max(lo * 1.1, hi) / 440) + 2
        if self.m_hi - self.m_lo < 12:
            milieu = (self.m_lo + self.m_hi) / 2
            self.m_lo, self.m_hi = milieu - 6, milieu + 6
        self.tout()

    def tout(self):
        if self.res is not None:
            self.zoomer(float(self.res.get("debut_s") or 0.0), float(self.res.get("fin_s") or 0.0))

    def zoomer(self, debut, fin):
        self.t0, self.t1 = debut, max(debut + 0.5, fin)
        self.queue_draw()

    def placer(self, t):
        self.position = t
        self.queue_draw()

    def _x(self, t, g, w):
        return g + (t - self.t0) / (self.t1 - self.t0) * w

    def _y(self, hz, haut, h):
        m = 69 + 12 * math.log2(max(hz, 1e-3) / 440)
        return haut + h - (m - self.m_lo) / (self.m_hi - self.m_lo) * h

    def _clic(self, x):
        g = 42
        w = max(1, self.get_width() - g - 6)
        if self.sur_clic is not None and self.res is not None:
            self.sur_clic(self.t0 + max(0.0, min(1.0, (x - g) / w)) * (self.t1 - self.t0))

    def _texte(self, cr, texte, taille=7.5):
        lay = self.create_pango_layout(texte)
        d = (self.get_pango_context().get_font_description() or Pango.FontDescription()).copy()
        d.set_size(int(taille * Pango.SCALE))
        lay.set_font_description(d)
        l, h = lay.get_pixel_size()
        return lay, l, h

    def _dessiner(self, _z, cr, largeur, hauteur):
        fg = self.get_color()
        texte = (fg.red, fg.green, fg.blue)
        if self.res is None:
            return
        g, haut, bas = 42, 14, hauteur - 18
        h, w = bas - haut, largeur - g - 6
        # bandes des lignes (alternées) et leur numéro
        for k, l in enumerate((self.tour or {}).get("lignes") or []):
            if l.get("fin", 0) < self.t0 or l.get("debut", 0) > self.t1:
                continue
            x0, x1 = self._x(max(self.t0, l["debut"]), g, w), self._x(min(self.t1, l["fin"]), g, w)
            faux = l.get("score") is not None and l["score"] < justesse.SEUIL_LIGNE_FAUSSE
            cr.set_source_rgba(*(ROUGE if faux else texte), 0.10 if faux else (0.05 if k % 2 else 0.025))
            cr.rectangle(x0, haut, x1 - x0, h)
            cr.fill()
            lay, lt, ht = self._texte(cr, str(l.get("i", k) + 1))
            cr.set_source_rgba(*texte, 0.45)
            cr.move_to(x0 + 3, haut - ht + 1)
            PangoCairo.show_layout(cr, lay)
        # échelle des notes : do, mi, sol
        cr.set_line_width(1)
        for m in range(int(math.ceil(self.m_lo)), int(self.m_hi) + 1):
            if m % 12 not in (0, 4, 7):
                continue
            f = analyse.hz_de_midi(m)
            y = self._y(f, haut, h)
            cr.set_source_rgba(*texte, 0.16 if m % 12 == 0 else 0.06)
            cr.move_to(g, y)
            cr.line_to(g + w, y)
            cr.stroke()
            if m % 12 == 0 or self.m_hi - self.m_lo < 20:
                lay, lt, ht = self._texte(cr, analyse.note_fr(f))
                cr.set_source_rgba(*texte, 0.6)
                cr.move_to(g - lt - 5, y - ht / 2)
                PangoCairo.show_layout(cr, lay)
        # temps
        duree = self.t1 - self.t0
        pas_t = next((p for p in (1, 2, 5, 10, 15, 30, 60) if duree / p <= 10), 60)
        t = math.ceil(self.t0 / pas_t) * pas_t
        while t <= self.t1:
            lay, lt, ht = self._texte(cr, formats.chrono(t))
            cr.set_source_rgba(*texte, 0.5)
            cr.move_to(self._x(t, g, w) - lt / 2, hauteur - ht - 1)
            PangoCairo.show_layout(cr, lay)
            t += pas_t
        cr.save()
        cr.rectangle(g, haut - 2, w, h + 4)
        cr.clip()
        faux = [(p["debut"], p["fin"]) for p in (self.tour or {}).get("passages_faux") or []]
        # l'original en bandes larges et pâles (la note à tenir), ta voix en trait par-dessus
        bande = max(5.0, min(10.0, h / max(1.0, self.m_hi - self.m_lo)))
        self._tracer(cr, self.ref, g, w, haut, h, lambda _t: (*texte, 0.22), bande, rond=False)
        couleur = accent(self)
        self._tracer(cr, self.prise, g, w, haut, h,
                     lambda t: (*ROUGE, 0.95) if any(a <= t < b for a, b in faux) else (*couleur, 0.95), 2.2)
        cr.restore()
        if self.position is not None and self.t0 <= self.position <= self.t1:
            x = self._x(self.position, g, w)
            cr.set_source_rgba(*texte, 0.9)
            cr.rectangle(x - 1, haut - 2, 2, h + 4)
            cr.fill()

    def _tracer(self, cr, hz, g, w, haut, h, couleur, epaisseur, rond=True):
        if not hz:
            return
        i0 = max(0, int((self.t0 - self.debut) / self.pas) - 1)
        i1 = min(len(hz), int((self.t1 - self.debut) / self.pas) + 2)
        cr.set_line_width(epaisseur)
        cr.set_line_join(1 if rond else 0)
        cr.set_line_cap(1 if rond else 0)
        # un tracé par morceau de même couleur ; on ne relie que deux trames voisines à moins de 3 demi-tons
        trace, precedent = None, None      # trace : couleur du tracé ouvert ; precedent : (x, y, f) d'avant
        for i in range(i0, i1):
            f = hz[i]
            if f <= 0:
                precedent = None
                continue
            t = self.debut + i * self.pas
            x, y, c = self._x(t, g, w), self._y(f, haut, h), couleur(t)
            relie = precedent is not None and abs(12 * math.log2(f / precedent[2])) <= 3
            if trace is not None and (not relie or c != trace):
                cr.set_source_rgba(*trace)
                cr.stroke()
                trace = None
            if trace is None:
                trace = c
                if relie:
                    cr.move_to(precedent[0], precedent[1])
                    cr.line_to(x, y)
                else:
                    cr.move_to(x, y)
                    cr.line_to(x + 0.5, y)
            else:
                cr.line_to(x, y)
            precedent = (x, y, f)
        if trace is not None:
            cr.set_source_rgba(*trace)
            cr.stroke()


# --- Calibration de la latence -----------------------------------------------------------------------------------

class DialogueCalibration(Adw.Dialog):
    """Quatre bips joués et enregistrés (sans casque, ou un écouteur contre le micro) : la latence du matériel."""

    def __init__(self, page):
        super().__init__(title="Mesurer la latence", content_width=520, content_height=420)
        self.page = page
        self.enr = None
        self.latence = None
        vue = Adw.ToolbarView()
        vue.add_top_bar(Adw.HeaderBar())
        b = commun.boite(espace=14, marges=20)
        b.append(Gtk.Label(label="Quatre bips vont être joués et enregistrés. Retirez le casque et montez un peu le "
                                 "volume, ou tenez un écouteur tout contre le micro, puis ne faites pas de bruit "
                                 "pendant 6 secondes.", wrap=True, xalign=0))
        b.append(commun.legende("Un casque Bluetooth ajoute souvent 150 à 300 ms : mesurez avec lui si vous chantez "
                                "avec lui."))
        self.etat = Gtk.Label(wrap=True, css_classes=["title-3"], vexpand=True, valign=Gtk.Align.CENTER)
        b.append(self.etat)
        boutons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.b_lancer = commun.bouton("Lancer la mesure", "media-record-symbolic", styles=("pill",),
                                      clic=self.lancer)
        self.b_garder = commun.bouton("Garder cette latence", styles=("suggested-action", "pill"), clic=self.garder,
                                      sensitive=False)
        boutons.append(self.b_lancer)
        boutons.append(self.b_garder)
        b.append(boutons)
        vue.set_content(b)
        self.set_child(vue)
        self.connect("closed", lambda *_: self.enr is not None and not self.enr.termine and self.enr.abandonner())

    def lancer(self):
        self.b_lancer.set_sensitive(False)
        self.b_garder.set_sensitive(False)
        self.etat.set_label("Écoutez les bips…")
        d = K.dossier_cache()
        d.mkdir(parents=True, exist_ok=True)
        bips = d / "calibration-bips.wav"
        self.fichier = d / f"calibration-{os.getpid()}.wav"
        self.instants = K.ecrire_bips(bips)
        self.enr = PriseKaraoke(self.fichier, bips, self.page.micro_choisi(), sur_fin=self._fini,
                                sur_fin_beat=lambda: GLib.timeout_add(900, lambda: (self.enr.arreter(), False)[1]))
        if self.enr.preparer():
            GLib.timeout_add(300, lambda: (self.enr.demarrer(), False)[1])
            GLib.timeout_add(250, self._mesurer)

    def _mesurer(self):
        if self.enr is None or self.enr.termine:
            return False
        self.enr.mesurer()
        return True

    def _fini(self, erreur):
        self.b_lancer.set_sensitive(True)
        if erreur:
            self.etat.set_label(f"Mesure impossible : {erreur}")
            return
        m = self.enr.mesure()
        decal = m["prise_debut_s"] - m["beat_debut_s"]
        f = self.fichier

        def calculer():
            try:
                return K.mesurer_latence(f, self.instants, decal)
            finally:
                f.unlink(missing_ok=True)

        def mesure(r):
            self.latence = max(0, r["latence_ms"])
            self.etat.set_label(f"Latence : {self.latence} ms ({r['entendus']} bips sur {r['total']}, "
                                f"écart {formats.nombre(r['ecart_ms'], 0)} ms)")
            self.b_garder.set_sensitive(True)
        commun.en_fil(calculer, mesure, lambda e: self.etat.set_label(f"Mesure impossible : {e}"))

    def garder(self):
        if self.latence is not None:
            self.page.regler_latence(self.latence)
            self.page.fenetre.toast(f"Latence gardée : {self.latence} ms")
        self.close()


# --- La page ----------------------------------------------------------------------------------------------------------

INFO_DOUBLER = "Ta voix et le beat de cette prise, doublés dans une voix clonée (travail « doubler »)"


class PageKaraoke(commun.Page):
    def __init__(self, fenetre):
        super().__init__(fenetre, "Karaoké", "karaoke")
        _installer_css()
        self.reglages = reglages(fenetre.prefs)
        self.chansons = []            # [{titre, dossier, res}] : un doublage par dossier, avec un beat seul
        self.chanson = None           # la chanson choisie : {…, beat, duree, paroles, lignes, reference}
        self._charge = False
        self._a_choisir = None        # dossier demandé avant que la liste soit lue (« Chanter… » de la bibliothèque)
        self._remplissage = False
        self.micros = [("", "Micro par défaut du système")]
        self.enr = None
        self.prise = None             # la prise en cours de décision (ou rouverte)
        self.plan = None
        self.passage = None
        self.etat = "preparer"
        self._compte = 0
        self._minuteur = None
        self._t_fixe = 0.0
        self.lecture = None           # réécoute : Gtk.MediaFile
        self.ecoute = "voix"          # « voix » (ta voix seule) ou « mix » (avec le beat)
        self._apercu = Apercu(lambda: self.lecture is not None and self.lecture.pause())
        self.resultat = None
        self.chanson_prise = None     # la chanson de la prise en cours (la liste peut être relue pendant ce temps)
        self.beat_prepare = None
        self.tour = 0
        self._suivi_lecture = None
        self._ferme = False

        self.titre = Adw.WindowTitle(title="Karaoké", subtitle="")
        self.entete.set_title_widget(self.titre)
        self.pile = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.pile.add_named(commun.page_vide("content-loading-symbolic", "Chargement des doublages…", ""), "chargement")
        self.pile.add_named(commun.page_vide("audio-input-microphone-symbolic", "Rien à chanter pour l’instant",
                                             "Le karaoké chante sur le beat seul d’un doublage "
                                             "(travail/instrumental.wav) : faites d’abord un doublage."), "vide")
        self.pile.add_named(self._vue_preparer(), "preparer")
        self.pile.add_named(self._vue_prise(), "prise")
        self.pile.add_named(self._vue_resultat(), "resultat")
        self.contenu(self.pile)
        clavier = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        clavier.connect("key-pressed", self._touche)
        self.add_controller(clavier)
        fenetre.ecouter("resultats", self._liste_changee)
        fenetre.ecouter("donnees", self._liste_changee)

    # --- vues --------------------------------------------------------------------------------------------------------

    def _vue_preparer(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Chanson", description="Chantez sur le beat seul d’un doublage, les paroles "
                                                              "défilent ; une note de justesse suit la prise.")
        self.r_chanson = Adw.ComboRow(title="Doublage", model=Gtk.StringList.new([]), enable_search=True)
        self.r_chanson.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        self.r_chanson.connect("notify::selected", lambda *_: self._chanson_choisie())
        g.add(self.r_chanson)
        self.r_infos = Adw.ActionRow(title="Paroles et mélodie", subtitle="…", subtitle_lines=0)
        self.r_infos.add_prefix(Gtk.Image(icon_name="audio-x-generic-symbolic"))
        g.add(self.r_infos)
        page.add(g)

        g = Adw.PreferencesGroup(title="Passage")
        self.r_mode = Adw.ComboRow(title="Chanter", model=Gtk.StringList.new([l for _c, l in MODES]))
        self.r_mode.connect("notify::selected", lambda *_: self._passage_change())
        g.add(self.r_mode)
        self.r_de = Adw.ComboRow(title="De la ligne", model=Gtk.StringList.new([]))
        self.r_a = Adw.ComboRow(title="À la ligne", model=Gtk.StringList.new([]))
        for r in (self.r_de, self.r_a):
            r.connect("notify::selected", lambda *_: self._passage_change())
            g.add(r)
        self.e_de = Adw.EntryRow(title="Début (1:05)")
        self.e_a = Adw.EntryRow(title="Fin (1:35)")
        for e in (self.e_de, self.e_a):
            e.connect("changed", lambda *_: self._passage_change())
            g.add(e)
        self.r_tours = Adw.SpinRow.new_with_range(2, K.REPETITIONS_MAX, 1)
        self.r_tours.set_title("Nombre de tours")
        self.r_tours.set_value(max(2, int(self.reglages["repetitions"] or 3)))
        self.r_tours.connect("notify::value", lambda *_: self._passage_change())
        g.add(self.r_tours)
        self.l_passage = commun.legende("")
        self.l_passage.set_margin_top(8)
        g.add(self.l_passage)
        page.add(g)

        g = Adw.PreferencesGroup(title="Micro et casque")
        casque = Adw.ActionRow(title="Mettez un casque", subtitle="Sans casque, le beat repasse dans le micro : il "
                                                                  "s’entend dans la prise et trompe la note.",
                               subtitle_lines=0)
        casque.add_prefix(Gtk.Image(icon_name="audio-headphones-symbolic"))
        g.add(casque)
        self.r_micro = Adw.ComboRow(title="Micro", model=Gtk.StringList.new(["Micro par défaut du système"]))
        self.r_micro.connect("notify::selected", lambda *_: self._micro_choisi())
        g.add(self.r_micro)
        tel = Adw.ActionRow(title="Micro du téléphone : pas en karaoké",
                            subtitle="scrcpy commence la prise avec un retard qui change d’une fois à l’autre : le "
                                     "chant ne tomberait pas sur le beat.", subtitle_lines=0)
        tel.add_prefix(Gtk.Image(icon_name="phone-symbolic"))
        g.add(tel)
        self.r_latence = Adw.SpinRow.new_with_range(0, 800, 5)
        self.r_latence.set_title("Latence (ms)")
        self.r_latence.set_subtitle("Retard entre ce que vous entendez et ce que le micro enregistre ; gardée dans "
                                    "les préférences")
        self.r_latence.set_value(int(self.reglages["latence_ms"] or 0))
        self.r_latence.connect("notify::value", lambda *_: self._latence_changee())
        self.r_latence.add_suffix(commun.bouton("Mesurer…", valign=Gtk.Align.CENTER, clic=self.calibrer,
                                                infobulle="Des bips joués et enregistrés"))
        g.add(self.r_latence)
        page.add(g)

        g = Adw.PreferencesGroup()
        self.b_chanter = commun.bouton("Chanter", "media-record-symbolic", styles=("suggested-action", "pill"),
                                       clic=self.commencer, halign=Gtk.Align.CENTER, infobulle="Chanter (Espace)")
        g.add(self.b_chanter)
        page.add(g)
        self.g_gardees = Adw.PreferencesGroup(title="Prises gardées", visible=False)
        page.add(self.g_gardees)
        self._lignes_gardees = []
        return page

    def _vue_prise(self):
        b = commun.boite(espace=12, marges=18)
        tete = Gtk.Box(spacing=12)
        self.i_rec = Gtk.Image(icon_name="media-record-symbolic", css_classes=["error"], pixel_size=22)
        tete.append(self.i_rec)
        self.l_temps = Gtk.Label(css_classes=["title-2", "chiffres"], xalign=0)
        tete.append(self.l_temps)
        self.l_prise = Gtk.Label(css_classes=["dim-label"], xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        tete.append(self.l_prise)
        self.niveau = Gtk.LevelBar(min_value=0, max_value=1, mode=Gtk.LevelBarMode.CONTINUOUS, width_request=160,
                                   valign=Gtk.Align.CENTER)
        self.niveau.add_offset_value("bas", 0.55)
        self.niveau.add_offset_value("haut", 0.9)
        self.niveau.add_offset_value("plein", 1.0)
        tete.append(self.niveau)
        b.append(tete)
        self.karaoke = Karaoke("page", "tres-grande", precedente=True, langue=self._langue())
        self.karaoke.set_vexpand(True)
        self.karaoke.set_valign(Gtk.Align.CENTER)
        self.karaoke.suivre(self._position)
        cadre = Gtk.Overlay(child=self.karaoke, vexpand=True)
        self.l_decompte = Gtk.Label(label="3", css_classes=["decompte-karaoke", "osd"], halign=Gtk.Align.CENTER,
                                    valign=Gtk.Align.START, margin_top=12, visible=False)
        cadre.add_overlay(self.l_decompte)
        b.append(cadre)
        self.l_conseil_prise = commun.legende("Casque sur les oreilles ? Les paroles défilent avec le beat.")
        self.l_conseil_prise.set_halign(Gtk.Align.CENTER)
        b.append(self.l_conseil_prise)
        boutons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        boutons.append(commun.bouton("Annuler", "edit-undo-symbolic", styles=("pill",), clic=self.annuler,
                                     infobulle="Annuler la prise (Échap)"))
        self.b_arreter = commun.bouton("Arrêter", "media-playback-stop-symbolic", styles=("destructive-action", "pill"),
                                       clic=self.arreter, infobulle="Arrêter et noter la prise (Espace)")
        boutons.append(self.b_arreter)
        b.append(boutons)
        return b

    def _vue_resultat(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup()
        carte = Gtk.Box(spacing=18, css_classes=["card", "carte-karaoke"])
        bloc = Gtk.Box(valign=Gtk.Align.CENTER, spacing=2)
        ligne_de_base = getattr(Gtk.Align, "BASELINE_FILL", Gtk.Align.BASELINE)
        self.l_score = Gtk.Label(label="—", css_classes=["score-karaoke"], valign=ligne_de_base)
        bloc.append(self.l_score)
        bloc.append(Gtk.Label(label="/100", css_classes=["score-sur"], valign=ligne_de_base))
        carte.append(bloc)
        droite = commun.boite(espace=4, valign=Gtk.Align.CENTER, hexpand=True)
        self.l_commentaire = Gtk.Label(xalign=0, wrap=True, css_classes=["title-3"])
        droite.append(self.l_commentaire)
        self.l_details = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
        droite.append(self.l_details)
        self.s_analyse = Gtk.Spinner(spinning=True, halign=Gtk.Align.START, visible=False)
        droite.append(self.s_analyse)
        carte.append(droite)
        g.add(carte)
        self.b_tours = Gtk.Box(css_classes=["linked"], halign=Gtk.Align.CENTER, margin_top=10, visible=False)
        g.add(self.b_tours)
        page.add(g)

        self.g_courbe = Adw.PreferencesGroup(title="Ta voix contre l’original",
                                             description="Bandes pâles : les notes de l’original. Trait : ta voix, "
                                                         "ramenée à l’octave de l’original ; en rouge, les passages "
                                                         "faux. Un clic sur une ligne la montre de près.")
        self.b_tout = commun.bouton("Tout le passage", styles=("flat",), valign=Gtk.Align.CENTER,
                                    clic=lambda: self.courbe.tout())
        self.g_courbe.set_header_suffix(self.b_tout)
        self.courbe = CourbeJustesse(sur_clic=self._aller_chanson)
        self.g_courbe.add(self.courbe)
        page.add(self.g_courbe)

        g = Adw.PreferencesGroup(title="Réécouter")
        choix = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
        self.t_voix = Gtk.ToggleButton(label="Ta voix seule", active=True)
        self.t_mix = Gtk.ToggleButton(label="Avec le beat", group=self.t_voix)
        self.t_voix.connect("toggled", lambda b: b.get_active() and self._ecouter("voix"))
        self.t_mix.connect("toggled", lambda b: b.get_active() and self._ecouter("mix"))
        choix.append(self.t_voix)
        choix.append(self.t_mix)
        g.set_header_suffix(choix)
        self.controles = Gtk.MediaControls(hexpand=True)
        g.add(self.controles)
        self.l_ecoute = commun.legende("")
        g.add(self.l_ecoute)
        page.add(g)

        self.g_lignes = Adw.PreferencesGroup(title="Lignes")
        page.add(self.g_lignes)
        self._rangees = []

        self.g_calage = Adw.PreferencesGroup(title="Calage de cette prise")
        self.r_recaler = Adw.SpinRow.new_with_range(0, 800, 5)
        self.r_recaler.set_title("Latence utilisée (ms)")
        self.r_recaler.set_subtitle("Ta voix décalée d’autant pour tomber sur le beat")
        self.r_recaler.add_suffix(commun.bouton("Recalculer", valign=Gtk.Align.CENTER, clic=self.recaler))
        self.g_calage.add(self.r_recaler)
        self.r_suggestion = Adw.ActionRow(title="", subtitle_lines=0, visible=False)
        self.b_suggestion = commun.bouton("Appliquer", valign=Gtk.Align.CENTER, clic=self._appliquer_suggestion)
        self.r_suggestion.add_suffix(self.b_suggestion)
        self.g_calage.add(self.r_suggestion)
        page.add(self.g_calage)

        g = Adw.PreferencesGroup()
        boutons = Gtk.Box(spacing=8, margin_top=6)
        boutons.append(commun.bouton("Nouvelle prise", "media-record-symbolic", clic=self.nouvelle_prise))
        boutons.append(Gtk.Box(hexpand=True))
        self.b_doubler = commun.bouton("Doubler ma prise…", "audio-input-microphone-symbolic",
                                       clic=self.doubler_ma_prise, infobulle=INFO_DOUBLER)
        boutons.append(self.b_doubler)
        self.b_mp3 = commun.bouton("Enregistrer en MP3", "document-save-symbolic", clic=self.exporter_mp3,
                                   infobulle="Ta voix et le beat, dans le dossier karaoke/ du doublage")
        boutons.append(self.b_mp3)
        self.b_jeter = commun.bouton("Jeter la prise", styles=("destructive-action",), clic=self.jeter)
        boutons.append(self.b_jeter)
        self.b_garder = commun.bouton("Garder la prise", styles=("suggested-action",), clic=self.garder)
        boutons.append(self.b_garder)
        g.add(boutons)
        page.add(g)
        self.page_resultat = page
        return page

    # --- liste des chansons ----------------------------------------------------------------------------------------

    def montrer(self):
        super().montrer()
        if not self._charge:
            self.charger()
        commun.en_fil(sources_micro, self._micros)

    def _liste_changee(self, *_):
        if self._charge:
            self.charger()

    def charger(self):
        donnees, noms = self.fenetre.donnees, {v["id"]: v["nom"] for v in self.fenetre.voix}
        if not self.chansons:
            self.pile.set_visible_child_name("chargement")

        def lire():
            vus, sortie = set(), []
            for r in rapports.lister_resultats(chemins.dossier_sorties(donnees), noms):
                if r.dossier in vus or K.fichier_beat(r.travail) is None:
                    continue
                vus.add(r.dossier)
                sortie.append({"titre": r.titre, "dossier": r.dossier, "res": r,
                               "groupe": r.groupe})
            return sortie
        commun.en_fil(lire, self._liste_lue, lambda e: self._liste_lue([]))

    def _liste_lue(self, chansons):
        self._charge = True
        avant = self.chanson["dossier"] if self.chanson else None
        self.chansons = chansons
        if self.etat == "preparer":
            self.pile.set_visible_child_name("preparer" if chansons else "vide")
        voulu = self._a_choisir or avant or self.reglages.get("derniere")
        self._a_choisir = None
        self._remplissage = True
        try:
            self.r_chanson.set_model(Gtk.StringList.new(
                [f"{c['titre']}" + (f" · {c['groupe']}" if c["groupe"] else "") for c in chansons]))
            i = next((k for k, c in enumerate(chansons) if str(c["dossier"]) == str(voulu)), 0)
            self.r_chanson.set_selected(i if chansons else Gtk.INVALID_LIST_POSITION)
        finally:
            self._remplissage = False
        if chansons and self.etat == "preparer":
            self._chanson_choisie()

    def choisir(self, res):
        """« Chanter… » de la bibliothèque : ce doublage, sur la page de préparation."""
        if self.etat in ("prise",):
            self.fenetre.toast("Une prise est en cours")
            return
        if self.etat == "resultat":
            self.nouvelle_prise(confirmer=False)
        k = next((i for i, c in enumerate(self.chansons) if c["dossier"] == res.dossier), None)
        if k is None:
            self._a_choisir = res.dossier
            self.charger()
            return
        if self.r_chanson.get_selected() == k:
            self._chanson_choisie()
        else:
            self.r_chanson.set_selected(k)

    def _chanson_choisie(self, force=False):
        if self._remplissage:
            return
        i = self.r_chanson.get_selected()
        if not 0 <= i < len(self.chansons):
            return
        c = dict(self.chansons[i])
        if not force and self.chanson and self.chanson["dossier"] == c["dossier"]:
            return
        self.chanson = c
        self.titre.set_subtitle(c["titre"])
        self._sauver(derniere=str(c["dossier"]))
        self.r_infos.set_subtitle("Lecture des paroles et de la mélodie…")
        res = c["res"]

        def lire():
            beat = K.fichier_beat(res.travail)
            dv = res.dossier_voix
            ref = None
            if dv is not None and any((dv / n).is_file() for n in ("courbes.json", "courbes-locales.json")):
                ref = "courbes.json"
            elif analyse.fichier_voix_source(res.travail) is not None:
                ref = "voix d’origine (calculée à la note)"
            return {"beat": beat, "duree": K.duree_audio(beat) if beat else None, "reference": ref}

        def lu(infos):
            if self.chanson is not c:
                return
            c.update(infos)
            c.setdefault("paroles", None)
            c.setdefault("lignes", [])
            self._maj_infos()
            self._passage_change()
            self._maj_gardees()

        def paroles(p, message):
            if self.chanson is not c or message == "attente":
                return
            c["paroles"] = p
            c["lignes"] = K.lignes_paroles(p)
            c["message_paroles"] = message
            self._remplir_lignes()
            self._maj_infos()
            self._passage_change()
        commun.en_fil(lire, lu)
        analyses().paroles(res.dossier_voix, res.video, paroles)

    def _maj_infos(self):
        c = self.chanson
        if c is None:
            return
        morceaux = []
        if c.get("duree"):
            morceaux.append(f"beat de {formats.duree(c['duree'])}")
        n = len(c.get("lignes") or [])
        morceaux.append(f"{n} lignes de paroles" if n else (c.get("message_paroles") or "paroles…"))
        morceaux.append(f"mélodie de référence : {c['reference']}" if c.get("reference") else
                        "pas de mélodie de référence : pas de note")
        self.r_infos.set_subtitle(commun.echapper(" · ".join(morceaux)))

    def _remplir_lignes(self):
        lignes = (self.chanson or {}).get("lignes") or []
        textes = [f"{formats.chrono(d)}  {t}" for d, _f, t in lignes]
        self._remplissage = True
        try:
            for r in (self.r_de, self.r_a):
                r.set_model(Gtk.StringList.new(textes))
            if lignes:
                self.r_de.set_selected(0)
                self.r_a.set_selected(min(len(lignes) - 1, 3))
        finally:
            self._remplissage = False

    # --- passage ---------------------------------------------------------------------------------------------------

    def _mode(self):
        i = self.r_mode.get_selected()
        return MODES[i][0] if 0 <= i < len(MODES) else "entier"

    def _passage(self):
        """Le passage choisi (justesse.Passage) ; ErreurKaraoke s'il est impossible."""
        c = self.chanson
        if c is None or not c.get("duree"):
            raise K.ErreurKaraoke("choisissez un doublage avec un beat seul")
        mode = self._mode()
        if mode == "entier":
            return K.passage_entier(c["duree"])
        tours = int(self.r_tours.get_value()) if mode == "boucle" else 1
        lignes = c.get("lignes") or []
        if lignes:
            return K.passage_lignes(lignes, self.r_de.get_selected(), self.r_a.get_selected(), c["duree"], tours)
        d, f = formats.lire_secondes(self.e_de.get_text()), formats.lire_secondes(self.e_a.get_text())
        if d is None or f is None:
            raise K.ErreurKaraoke("indiquez le début et la fin (1:05, 65…)")
        return K.passage_temps(d, f, c["duree"], tours)

    def _passage_change(self):
        if self._remplissage:
            return
        mode = self._mode()
        lignes = bool((self.chanson or {}).get("lignes"))
        for r in (self.r_de, self.r_a):
            r.set_visible(mode != "entier" and lignes)
        for e in (self.e_de, self.e_a):
            e.set_visible(mode != "entier" and not lignes)
        self.r_tours.set_visible(mode == "boucle")
        try:
            p = self._passage()
        except K.ErreurKaraoke as e:
            self.l_passage.set_label(str(e))
            self.b_chanter.set_sensitive(False)
            return
        texte = f"De {formats.chrono(p.debut)} à {formats.chrono(p.fin)} ({formats.duree(p.duree)})"
        if p.repetitions > 1:
            texte += f", {p.repetitions} fois : {formats.duree(p.total)}"
            self._sauver(repetitions=p.repetitions)
        self.l_passage.set_label(texte)
        self.b_chanter.set_sensitive(self.etat == "preparer")

    # --- micro et latence ----------------------------------------------------------------------------------------

    def _micros(self, liste):
        self.micros = liste or [("", "Micro par défaut du système")]
        voulu = self.reglages.get("micro")
        voulu = self.fenetre.prefs.get("micro") or "" if voulu is None else voulu
        cles = [c for c, _d in self.micros]
        self._remplissage = True
        try:
            self.r_micro.set_model(Gtk.StringList.new([d for _c, d in self.micros]))
            self.r_micro.set_selected(cles.index(voulu) if voulu in cles else 0)
        finally:
            self._remplissage = False

    def micro_choisi(self):
        i = self.r_micro.get_selected()
        return self.micros[i][0] if 0 <= i < len(self.micros) else ""

    def _micro_choisi(self):
        if not self._remplissage:
            self._sauver(micro=self.micro_choisi())

    def _latence_changee(self):
        self._sauver(latence_ms=int(self.r_latence.get_value()))

    def regler_latence(self, ms):
        self.r_latence.set_value(int(ms))
        self._sauver(latence_ms=int(ms))

    def calibrer(self):
        if self.etat != "preparer":
            return
        DialogueCalibration(self).present(self.fenetre)

    def _sauver(self, **valeurs):
        nouveau = {**self.reglages, **valeurs}
        if nouveau != self.reglages:
            self.reglages = nouveau
            self.fenetre.prefs = chemins.modifier_preference("karaoke", nouveau)

    def _langue(self):
        lg = self.fenetre.prefs.get("paroles_langue") or "deux"
        return "deux" if lg == "fr" else lg      # on chante l'original : la traduction, s'il le faut, dessous

    # --- la prise --------------------------------------------------------------------------------------------------

    def commencer(self):
        if self.etat != "preparer" or self.chanson is None:
            return
        try:
            passage = self._passage()
        except K.ErreurKaraoke as e:
            self.fenetre.toast(str(e))
            return
        self._arreter_ecoute()
        self.passage = passage
        self.etat = "decompte"
        self.b_chanter.set_sensitive(False)
        c = self.chanson_prise = self.chanson
        source = c["beat"]
        self.l_prise.set_label(f"{c['titre']} · {formats.chrono(passage.debut)} → {formats.chrono(passage.fin)}"
                               + (f" · {passage.repetitions} tours" if passage.repetitions > 1 else ""))
        self.l_temps.set_label(f"0:00 / {formats.chrono(passage.total)}")
        self.niveau.set_value(0)
        self.niveau.set_visible(False)
        self.i_rec.set_opacity(0.3)
        self.l_conseil_prise.set_label("Préparez-vous : le beat part à la fin du décompte, et la prise avec lui.")
        self._t_fixe = passage.debut
        # pendant la prise, seulement les lignes du passage
        pp = K.paroles_du_passage(c.get("paroles"), passage)
        if pp is not None and pp.originale.lignes:
            self.karaoke.definir(pp)
        else:
            self.karaoke.dire(c.get("message_paroles") or "Pas de paroles pour ce passage : chantez la mélodie")
        self.karaoke.placer(passage.debut)
        self.l_decompte.set_label("…")
        self.l_decompte.set_visible(True)
        self.pile.set_visible_child_name("prise")
        commun.en_fil(lambda: K.preparer_beat(source, passage), self._beat_pret,
                      lambda e: self._echec(f"beat impossible à préparer : {e}"))

    def _beat_pret(self, beat):
        if self.etat != "decompte":
            return
        c = self.chanson_prise
        self.beat_prepare = beat
        self.prise = K.nouvelle_prise(c["dossier"])
        self.enr = PriseKaraoke(self.prise, beat, self.micro_choisi(), sur_niveau=self._niveau,
                                sur_fin=self._fin_prise, sur_fin_beat=self._fin_beat)
        if not self.enr.preparer():
            return
        self._compte = DECOMPTE_S
        self.l_decompte.set_label(str(self._compte))
        GLib.timeout_add(1000, self._decompter)

    def _decompter(self):
        if self.etat != "decompte" or self._ferme:
            return False
        self._compte -= 1
        if self._compte > 0:
            self.l_decompte.set_label(str(self._compte))
            return True
        self.l_decompte.set_visible(False)
        self.etat = "prise"
        self.i_rec.set_opacity(1.0)
        self.niveau.set_visible(True)
        self.l_conseil_prise.set_label("Casque sur les oreilles ? Les paroles défilent avec le beat. Espace : arrêter "
                                       "et noter, Échap : annuler.")
        self.enr.demarrer()
        self._minuteur = GLib.timeout_add(250, self._tic)
        self.karaoke.placer(self.passage.debut, lecture=True)
        return False

    def _position(self):
        """(temps de la chanson, en lecture) pour les paroles : ce qu'on entend du beat."""
        p = self.passage
        if self.etat == "prise" and self.enr is not None and p is not None and not self.enr.termine:
            u = self.enr.position_beat()
            if u is not None:
                d = max(1e-6, p.duree)
                k = min(p.repetitions - 1, int(u // d))
                return p.debut + (u - k * d), True
        return self._t_fixe, False

    def _tic(self):
        if self.etat != "prise" or self.enr is None or self.enr.termine:
            self._minuteur = None
            return False
        self.enr.mesurer()
        u = self.enr.position_beat() or 0.0
        p = self.passage
        texte = f"{formats.chrono(min(u, p.total))} / {formats.chrono(p.total)}"
        if p.repetitions > 1:
            texte += f" · tour {min(p.repetitions, int(u // max(1e-6, p.duree)) + 1)}/{p.repetitions}"
        self.l_temps.set_label(texte)
        if not self.karaoke.animateur or self.karaoke.animateur.ident is None:
            t, lecture = self._position()
            self.karaoke.placer(t, lecture)
        return True

    def _niveau(self, db):
        self.niveau.set_value(max(0.0, min(1.0, (db + 60) / 60)))

    def _fin_beat(self):
        """Le beat est fini : on enregistre encore la latence et un peu plus, puis on arrête."""
        attente = int((self.reglages["latence_ms"] or 0) + QUEUE_S * 1000)
        GLib.timeout_add(attente, lambda: (self.arreter(), False)[1])

    def arreter(self):
        if self.etat == "decompte":
            self.annuler()
        elif self.etat == "prise" and self.enr is not None and not self.enr.termine:
            self.b_arreter.set_sensitive(False)
            self.enr.arreter()

    def annuler(self):
        """Échap : la prise (ou le décompte) s'arrête, rien n'est gardé."""
        if self.etat not in ("decompte", "prise"):
            return
        self.etat = "preparer"
        if self.enr is not None and not self.enr.termine:
            self.enr.abandonner()
        if self.prise is not None:
            K.jeter(self.prise)
        self.enr = self.prise = None
        self.l_decompte.set_visible(False)
        self.pile.set_visible_child_name("preparer")
        self._passage_change()

    def _echec(self, message):
        self.fenetre.toast(message)
        self.etat = "preparer"
        self.pile.set_visible_child_name("preparer")
        self._passage_change()

    def _fin_prise(self, erreur):
        self.b_arreter.set_sensitive(True)
        if self.etat not in ("prise", "decompte"):
            return
        enr = self.enr
        if erreur:
            if self.prise is not None:
                K.jeter(self.prise)
            self.prise = None
            self._echec(f"Prise impossible : {erreur}")
            return
        c, p = self.chanson_prise, self.passage
        mesure = enr.mesure()
        duree = time.time() - (enr.debut or time.time())
        self.plan = {"format": 1, "titre": c["titre"], "dossier": str(c["dossier"]), "voix": c["res"].voix,
                     "dossier_voix": str(c["res"].dossier_voix) if c["res"].dossier_voix else None,
                     "travail": str(c["res"].travail), "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
                     "passage": {"debut": p.debut, "fin": p.fin, "repetitions": p.repetitions, "mode": self._mode()},
                     "lignes": [list(l) for l in c.get("lignes") or []],
                     "beat": {"source": str(c["beat"]), "fichier": str(self.beat_prepare)},
                     "latence_ms": int(self.r_latence.get_value()), "mesure": mesure, "micro": self.micro_choisi(),
                     "duree_prise_s": round(duree, 2)}
        K.ecrire_plan(self.prise, self.plan)
        self.enr = None
        self.analyser()

    # --- note et mix -----------------------------------------------------------------------------------------------

    def analyser(self):
        """La note et le mix, hors de l'interface (python3 -m voixla.karaoke, file des analyses)."""
        self.etat = "analyse"
        self.resultat = None
        self.pile.set_visible_child_name("resultat")
        self._montrer_attente("Analyse de la prise…")
        prise = self.prise
        duree = (self.passage.total if self.passage else None) or (self.plan or {}).get("duree_prise_s")

        def fini(ok, sortie):
            if prise != self.prise or self._ferme:
                return
            plan = K.lire_plan(prise)
            if not ok or not isinstance(plan.get("resultat"), dict):
                self._montrer_attente(f"Analyse impossible : {derniere_ligne(sortie) or 'sans message'}", fin=True)
                return
            self.plan = plan
            self.montrer_resultat(plan["resultat"])

        def avancement(fraction, etape):
            if prise == self.prise and self.etat == "analyse":
                self.l_details.set_label(f"{etape} · {formats.nombre(100 * fraction, 0)} %")
        analyses()._demander(("karaoke", str(prise), time.time()), ["analyser", str(prise)], duree, fini,
                             module="voixla.karaoke", prioritaire=True, avancement=avancement)
        if analyses().file.en_cours is not None and analyses().file.en_cours.cle[:2] != ("karaoke", str(prise)):
            self.l_details.set_label("en attente d’une autre analyse…")

    def _montrer_attente(self, texte, fin=False):
        self.l_score.set_label("—")
        for c in ("bon", "moyen", "faible"):
            self.l_score.remove_css_class(c)
        self.l_commentaire.set_label(texte)
        self.l_details.set_label("" if fin else "La file des analyses passe par file-attente au-delà d’une minute "
                                                 "de son.")
        self.s_analyse.set_visible(not fin)
        self.g_courbe.set_visible(False)
        self.g_lignes.set_visible(False)
        self.g_calage.set_visible(False)
        self.b_tours.set_visible(False)
        self._sensibles(False, fin)

    def _sensibles(self, ecoute_mix, decision):
        self.t_mix.set_sensitive(ecoute_mix)
        self.b_mp3.set_sensitive(ecoute_mix)
        raison = self._raison_doubler(ecoute_mix)
        self.b_doubler.set_sensitive(raison == "")
        self.b_doubler.set_tooltip_text(INFO_DOUBLER if raison == "" else raison)
        enattente = self.prise is not None and K.en_attente(self.prise)
        self.b_garder.set_sensitive(decision and enattente)
        self.b_garder.set_visible(enattente)
        self.b_jeter.set_sensitive(decision)
        self.b_jeter.set_label("Jeter la prise" if enattente else "Mettre à la corbeille…")

    def montrer_resultat(self, res):
        self.etat = "resultat"
        self.resultat = res
        self.s_analyse.set_visible(False)
        self.pile.set_visible_child_name("resultat")
        score = res.get("score")
        self.l_score.set_label("—" if score is None else str(score))
        for c in ("bon", "moyen", "faible"):
            self.l_score.remove_css_class(c)
        if score is not None:
            self.l_score.add_css_class("bon" if score >= 75 else "moyen" if score >= 60 else "faible")
        self.l_commentaire.set_label(res.get("commentaire") or "")
        self.tour = res.get("meilleur_tour") or 0
        self._tours(res)
        self._rendre_tour()
        self.r_recaler.set_value(int((self.plan or {}).get("latence_ms") or 0))
        self.g_calage.set_visible(True)
        self._suggestion(res)
        self._sensibles(bool(res.get("mix")), True)
        self.t_voix.set_active(True)
        self._ecouter("voix", lire=False)
        self._maj_gardees()

    def _details(self, res, tour):
        morceaux = []
        if res.get("score") is None:
            morceaux.append(res.get("raison") or "pas de mélodie de référence")
        else:
            morceaux.append(f"Tu chantes {justesse.phrase_octave(tour.get('octave', 0))}")
            n, faux = res.get("lignes_notees") or 0, sum(1 for l in tour.get("lignes") or []
                                                         if l.get("score") is not None
                                                         and l["score"] < justesse.SEUIL_LIGNE_FAUSSE)
            morceaux.append(f"{formats.pluriel(faux, 'ligne fausse', 'lignes fausses')} sur {n}" if n else
                            "aucune ligne notée")
            r = justesse.phrase_retard(tour.get("retard_s"))
            if r:
                morceaux.append(r[:1].upper() + r[1:])
        return ". ".join(morceaux) + "."

    def _tours(self, res):
        commun.vider(self.b_tours)
        tours = res.get("tours") or []
        self.b_tours.set_visible(len(tours) > 1)
        premier = None
        for k, t in enumerate(tours):
            note = "—" if t.get("score") is None else formats.nombre(t["score"], 0)
            b = Gtk.ToggleButton(label=f"Tour {t['tour'] + 1} · {note}", group=premier, active=k == self.tour)
            premier = premier or b
            b.connect("toggled", lambda x, i=k: x.get_active() and self._choisir_tour(i))
            self.b_tours.append(b)

    def _choisir_tour(self, k):
        self.tour = k
        self._rendre_tour()

    def _tour(self):
        tours = (self.resultat or {}).get("tours") or []
        return tours[self.tour] if 0 <= self.tour < len(tours) else None

    def _rendre_tour(self):
        res, tour = self.resultat, self._tour()
        self.l_details.set_label(self._details(res, tour or {}))
        self.g_courbe.set_visible(tour is not None)
        if tour is not None:
            self.courbe.definir(res, tour)
        for r in self._rangees:
            self.g_lignes.remove(r)
        self._rangees = []
        lignes = (tour or {}).get("lignes") or []
        self.g_lignes.set_visible(bool(lignes))
        faux = sum(1 for l in lignes if l.get("score") is not None and l["score"] < justesse.SEUIL_LIGNE_FAUSSE)
        self.g_lignes.set_description(f"{formats.pluriel(faux, 'ligne fausse', 'lignes fausses')}, en rouge : un clic "
                                      "la montre sur la courbe et la fait entendre." if lignes else None)
        for l in lignes:
            r = Adw.ActionRow(title=commun.echapper(l.get("texte") or ""), activatable=True, title_lines=2)
            if l.get("score") is None:
                quoi = "hors de la prise" if l.get("hors_prise") else "trop courte pour être notée"
                r.set_subtitle(f"{formats.chrono(l['debut'])} · {quoi}")
                r.add_suffix(commun.badge("—"))
            else:
                bas = l["score"] < justesse.SEUIL_LIGNE_FAUSSE
                sous = formats.chrono(l["debut"]) + (f" · {l['conseil']}" if l.get("conseil") else "")
                r.set_subtitle(commun.echapper(sous))
                if bas:
                    r.add_css_class("ligne-fausse")
                style = "succes" if l["score"] >= 75 else "alerte" if not bas else "erreur"
                r.add_suffix(commun.badge(formats.nombre(l["score"], 0), style))
            r.connect("activated", lambda _r, x=l: self._ligne(x))
            self.g_lignes.add(r)
            self._rangees.append(r)

    def _ligne(self, l):
        self.courbe.zoomer(max(self.courbe.debut, l["debut"] - 0.5), l["fin"] + 0.5)
        self._aller_chanson(l["debut"], lire=True)

    def _suggestion(self, res):
        """Si ta voix arrive nettement après (ou avant) l'original partout, c'est peut-être la latence."""
        tour = self._tour() or {}
        r = tour.get("retard_s") or 0.0
        if res.get("score") is None or abs(r) < 0.06:
            self.r_suggestion.set_visible(False)
            return
        actuelle = int((self.plan or {}).get("latence_ms") or 0)
        self._latence_suggeree = max(0, int(round((actuelle + r * 1000) / 5.0) * 5))
        self.r_suggestion.set_title(commun.echapper(justesse.phrase_retard(r).capitalize() + "."))
        self.r_suggestion.set_subtitle(f"Si ce n’est pas toi qui es en {'retard' if r > 0 else 'avance'}, la latence "
                                       f"vaut plutôt {self._latence_suggeree} ms.")
        self.r_suggestion.set_visible(True)

    def _appliquer_suggestion(self):
        self.r_recaler.set_value(self._latence_suggeree)
        self.regler_latence(self._latence_suggeree)
        self.recaler()

    def recaler(self):
        """La même prise, recalée avec une autre latence : note et mix refaits."""
        if self.prise is None or self.etat != "resultat":
            return
        self._arreter_ecoute()
        plan = K.lire_plan(self.prise)
        plan["latence_ms"] = int(self.r_recaler.get_value())
        plan.pop("resultat", None)
        K.ecrire_plan(self.prise, plan)
        self.plan = plan
        self.passage = K.passage_du_plan(plan)
        self.analyser()

    # --- réécoute ----------------------------------------------------------------------------------------------------

    def _ecouter(self, quoi, lire=None):
        """Ta voix seule (la prise) ou avec le beat (le mix en cache), à la même position de la chanson."""
        if self.prise is None:
            return
        res = self.resultat or {}
        mix = Path(res["mix"]) if res.get("mix") else None
        if quoi == "mix" and (mix is None or not mix.is_file()):
            self._refaire_mix()
            return
        t_chanson = self._position_chanson()
        lisait = lire if lire is not None else bool(self.lecture is not None and self.lecture.get_playing())
        self._arreter_ecoute()
        self.ecoute = quoi
        self.lecture = media(mix if quoi == "mix" else self.prise, self._apercu)
        if _muet() or os.environ.get("VOIXLA_CAPTURE"):
            self.lecture.set_muted(True)
        self.controles.set_media_stream(self.lecture)
        self.l_ecoute.set_label("Ta voix et le beat, calés, au niveau du mix" if quoi == "mix" else
                                "Ta prise telle qu’enregistrée")
        if t_chanson is not None:
            self._aller_chanson(t_chanson, lire=lisait)
        elif lisait:
            self.lecture.play()
        if self._suivi_lecture is None:
            self._suivi_lecture = GLib.timeout_add(100, self._suivre_lecture)

    def _refaire_mix(self):
        """Le mix a été purgé du cache (prise gardée rouverte) : refait en tâche de fond."""
        prise = self.prise
        self.l_ecoute.set_label("Préparation du mix…")
        self.t_voix.set_active(True)

        def fini(ok, sortie):
            if prise != self.prise:
                return
            plan = K.lire_plan(prise)
            if ok and isinstance(plan.get("resultat"), dict):
                self.plan, self.resultat = plan, plan["resultat"]
                self.t_mix.set_active(True)
            else:
                self.l_ecoute.set_label(f"Mix impossible : {derniere_ligne(sortie)}")
        analyses()._demander(("karaoke-mix", str(prise)), ["analyser", str(prise), "--mix-seulement"],
                             (self.passage.total if self.passage else None), fini, module="voixla.karaoke",
                             prioritaire=True)

    def _decalage(self):
        return K.decalage(self.plan or {})

    def _temps_beat(self):
        """Position de la réécoute en temps du beat lu."""
        if self.lecture is None:
            return None
        t = self.lecture.get_timestamp() / 1e6
        return t if self.ecoute == "mix" else t + self._decalage()

    def _position_chanson(self):
        u = self._temps_beat()
        p = self.passage
        if u is None or p is None:
            return None
        d = max(1e-6, p.duree)
        k = max(0, min(p.repetitions - 1, int(u // d)))
        return p.debut + (u - k * d)

    def _aller_chanson(self, t, lire=False):
        """Va à l'instant t de la chanson, dans le tour affiché."""
        if self.lecture is None or self.passage is None:
            return
        u = self.tour * self.passage.duree + (t - self.passage.debut)
        pos = u if self.ecoute == "mix" else u - self._decalage()
        m = self.lecture

        def placer():
            m.seek(int(max(0.0, pos) * 1e6))
            if lire:
                m.play()
        if m.is_prepared():
            placer()
        else:
            ident = []

            def pret(*_):
                if m.is_prepared():
                    m.disconnect(ident[0])
                    placer()
            ident.append(m.connect("notify::prepared", pret))

    def _suivre_lecture(self):
        if self.lecture is None or self.etat != "resultat":
            self._suivi_lecture = None
            self.courbe.placer(None)
            return False
        if self.lecture.get_playing():
            self.courbe.placer(self._position_chanson())
        return True

    def _arreter_ecoute(self):
        if self.lecture is not None:
            self.lecture.pause()
            self.controles.set_media_stream(None)
            self.lecture = None

    # --- garder, jeter, MP3 --------------------------------------------------------------------------------------

    def garder(self):
        if self.prise is None or not K.en_attente(self.prise):
            return
        self._arreter_ecoute()
        self.prise = K.garder(self.prise)
        self.fenetre.toast(f"Prise gardée dans karaoke/ : {self.prise.name}", "Ouvrir le dossier",
                           lambda: commun.ouvrir_dossier(self.fenetre, self.prise.parent))
        self._sensibles(bool((self.resultat or {}).get("mix")), True)
        self._maj_gardees()

    def jeter(self):
        if self.prise is None:
            return
        if K.en_attente(self.prise):
            self._arreter_ecoute()
            K.jeter(self.prise)
            self.prise = None
            self.fenetre.toast("Prise jetée")
            self._retour()
            return
        prise = self.prise

        def faire():
            self._arreter_ecoute()

            def fini(ok, message):
                if ok:
                    commun.corbeille(K.plan_de(prise), lambda *_: None)
                    self.fenetre.toast("Prise mise à la corbeille")
                    self.prise = None
                    self._retour()
                else:
                    self.fenetre.toast(f"Corbeille impossible : {message}")
            commun.corbeille(prise, fini)
        commun.confirmer(self.fenetre, "Mettre cette prise à la corbeille ?", prise.name, "Mettre à la corbeille",
                         faire)

    def nouvelle_prise(self, confirmer=True):
        if self.prise is not None and K.en_attente(self.prise) and confirmer:
            commun.confirmer(self.fenetre, "Jeter cette prise ?", "Elle n’a pas été gardée.", "Jeter",
                             lambda: (K.jeter(self.prise), setattr(self, "prise", None), self._retour()))
            return
        if self.prise is not None and K.en_attente(self.prise):
            K.jeter(self.prise)
        self.prise = None
        self._retour()

    def _retour(self):
        self._arreter_ecoute()
        self.etat = "preparer"
        self.resultat = None
        self.pile.set_visible_child_name("preparer" if self.chansons else "vide")
        self._passage_change()
        self._maj_gardees()

    # --- doubler ma prise : la prise (ta voix + le beat) devient la source d'un doublage dans une voix clonée ----------

    def _raison_doubler(self, mix_pret):
        """Pourquoi « Doubler ma prise » est grisé (texte vide : possible)."""
        f = self.fenetre
        if not mix_pret:
            return "Le mix de la prise n’est pas prêt"
        if "son_seul" not in getattr(f, "caps", ()):
            return "Ce doubler ne sait pas encore doubler un fichier audio seul : mettez-le à jour"
        if not f.voix:
            return "Aucune voix clonée : ajoutez-en une dans la page Voix"
        return ""

    def doubler_ma_prise(self):
        """Choix de la voix cible, puis un travail « doubler » sur le mix de la prise (voix + beat)."""
        if self.prise is None or self.chanson is None or self._raison_doubler(bool((self.resultat or {}).get("mix"))):
            return
        f = self.fenetre
        voix = list(f.voix)
        choix = Adw.ComboRow(title="Voix cible", model=Gtk.StringList.new([v["nom"] for v in voix]))
        defaut = f.voix_defaut()
        choix.set_selected(next((i for i, v in enumerate(voix) if v["id"] == defaut), 0))
        liste = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        liste.append(choix)
        d = Adw.AlertDialog(heading="Doubler ma prise",
                            body="Ta voix et le beat de cette prise passent par « doubler » : ta voix devient la voix "
                                 "choisie, le beat reste. Le résultat arrive dans la bibliothèque, comme un doublage "
                                 "(quelques minutes de GPU Kaggle).")
        d.set_extra_child(liste)
        d.add_response("annuler", "Annuler")
        d.add_response("lancer", "Doubler")
        d.set_response_appearance("lancer", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("lancer")
        d.set_close_response("annuler")
        d.connect("response", lambda _d, r: r == "lancer" and self._lancer_doubler(voix[choix.get_selected()]["id"]))
        d.present(f)
        self._dialogue_doubler = d

    def _lancer_doubler(self, voix):
        prise, c = self.prise, self.chanson
        dossier = Path((self.plan or {}).get("dossier") or c["dossier"])
        titre = (self.plan or {}).get("titre") or c["titre"]
        f = self.fenetre
        self.b_doubler.set_sensitive(False)
        f.toast("Préparation de ta prise…")

        def prepare():
            return K.preparer_entree_doubler(prise, titre, dossier)

        def pret(r):
            self.b_doubler.set_sensitive(True)
            entree, duree = r
            caps = f.caps
            est = rapports.estimer([duree or 0], 30, None, nettoyage="--sans-nettoyage" in caps, mode="chant",
                                   cartes=moteur.cartes_prevues({}, caps), paroles="--sans-paroles" in caps,
                                   sorties=chemins.dossier_sorties(f.donnees))
            demande = travaux.demande_doubler_prise(entree, duree, voix, f.donnees, estimations=est)
            demande, attend = travaux.juger_quota(demande, f.quota, f.file.travaux(), quota.reglages(f.prefs)[4])
            f.ajouter_travaux([demande])
            message = ("Doublage de ta prise en attente du quota GPU : départ tout seul à la remise à zéro" if attend
                       else "Doublage de ta prise dans la file")
            f.toast(message, "Voir", lambda: f.aller("travaux"))
            f.aller("travaux")

        def echec(e):
            self.b_doubler.set_sensitive(True)
            f.toast(f"Doubler ma prise impossible : {e}")
        commun.en_fil(prepare, pret, echec)

    def exporter_mp3(self):
        if self.prise is None or self.chanson is None:
            return
        prise, dossier = self.prise, Path((self.plan or {}).get("dossier") or self.chanson["dossier"])
        titre = (self.plan or {}).get("titre") or self.chanson["titre"]
        sortie = K.nom_mp3(dossier, titre)
        self.b_mp3.set_sensitive(False)
        self.fenetre.toast("Enregistrement du MP3…")

        def fini(ok, sortie_txt):
            self.b_mp3.set_sensitive(True)
            if ok and sortie.is_file():
                self.fenetre.toast(f"MP3 enregistré : {sortie.name}", "Ouvrir le dossier",
                                   lambda: commun.montrer_fichier(self.fenetre, sortie))
            else:
                self.fenetre.toast(f"MP3 impossible : {derniere_ligne(sortie_txt) or 'sans message'}")
        analyses()._demander(("karaoke-mp3", str(sortie)), ["mp3", str(prise), str(sortie), "--titre",
                                                             f"{titre} (karaoké)"],
                             (self.passage.total if self.passage else None), fini, module="voixla.karaoke")

    # --- prises gardées ----------------------------------------------------------------------------------------------

    def _maj_gardees(self):
        c = self.chanson
        for r in self._lignes_gardees:
            self.g_gardees.remove(r)
        self._lignes_gardees = []
        prises = K.prises_gardees(c["dossier"]) if c else []
        self.g_gardees.set_visible(bool(prises))
        self.g_gardees.set_description(f"Dans {K.DOSSIER}/ du doublage" if prises else None)
        for f, plan in prises[:20]:
            res = plan.get("resultat") or {}
            p = plan.get("passage") or {}
            note = res.get("score")
            date = plan.get("date") or ""
            quand = f"{formats.date_iso(date[:10])} {date[11:16]}" if len(date) >= 16 else f.stem
            r = Adw.ActionRow(title=quand, activatable=True,
                              subtitle=f"{formats.chrono(p.get('debut'))} → {formats.chrono(p.get('fin'))}"
                                       + (f" · {p.get('repetitions')} tours" if (p.get("repetitions") or 1) > 1
                                          else ""))
            r.add_prefix(Gtk.Image(icon_name="audio-x-generic-symbolic"))
            r.add_suffix(commun.badge("—" if note is None else f"{note}/100",
                                      "neutre" if note is None else "succes" if note >= 75 else
                                      "alerte" if note >= 60 else "erreur"))
            r.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            r.connect("activated", lambda _r, x=f: self.rouvrir(x))
            self.g_gardees.add(r)
            self._lignes_gardees.append(r)

    def rouvrir(self, prise):
        """Une prise gardée : sa note, sa courbe et sa réécoute."""
        plan = K.lire_plan(prise)
        if not isinstance(plan.get("resultat"), dict):
            self.prise, self.plan = Path(prise), plan
            self.passage = K.passage_du_plan(plan)
            self.analyser()
            return
        self.prise, self.plan = Path(prise), plan
        self.passage = K.passage_du_plan(plan)
        self.montrer_resultat(plan["resultat"])

    # --- clavier, fermeture ----------------------------------------------------------------------------------------

    def _touche(self, _c, touche, _code, etat):
        if etat & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        focus = self.fenetre.get_focus()
        saisie = isinstance(focus, (Gtk.Text, Gtk.Entry, Gtk.SpinButton))
        if touche == Gdk.KEY_Escape and self.etat in ("decompte", "prise"):
            self.annuler()
            return True
        if touche != Gdk.KEY_space or saisie:
            return False
        if self.etat in ("decompte", "prise"):
            self.arreter()
            return True
        if isinstance(focus, (Gtk.Button, Gtk.CheckButton, Gtk.ListBoxRow)):
            return False                  # Espace appartient à ce qui a le focus
        if self.etat == "preparer" and self.b_chanter.get_sensitive():
            self.commencer()
            return True
        if self.etat == "resultat" and self.lecture is not None:
            (self.lecture.pause if self.lecture.get_playing() else self.lecture.play)()
            return True
        return False

    def cacher(self):
        super().cacher()
        if self.lecture is not None:
            self.lecture.pause()

    def fermer(self):
        self._ferme = True
        if self.enr is not None and not self.enr.termine:
            self.enr.abandonner()
        self._arreter_ecoute()

    # --- essais ----------------------------------------------------------------------------------------------------

    def scenario(self, action):
        """Captures et essais (son de synthèse, aucun son joué) : « prise » (une prise au micro de synthèse, sur les
        lignes 2 à 5), « decompte » (pareil, arrêtée au décompte), « resultat » (la prise VOIXLA_KARAOKE_PRISE,
        un WAV qui chante la chanson entière, notée comme une vraie), « calibrer », « doubler » (le dialogue « Doubler ma prise… » après « resultat »), « doubler-lancer ». Préfixe « chanson-<mot> » :
        choisir le doublage dont le titre contient ce mot."""
        if os.environ.get("VOIXLA_CAPTURE"):
            os.environ["VOIXLA_MICRO_ESSAI"] = "1"
        etapes = [a for a in action.split("+") if a]

        def jouer():
            if not self._charge or (self.chanson is None and self.chansons) or \
                    (self.chanson is not None and "paroles" not in self.chanson):
                GLib.timeout_add(200, jouer)
                return False
            for a in etapes:
                self._scenario(a)
            return False
        GLib.timeout_add(300, jouer)

    def _scenario(self, a):
        if a.startswith("chanson-"):
            mot = a.split("-", 1)[1].lower()
            k = next((i for i, c in enumerate(self.chansons) if mot in c["titre"].lower()), None)
            if k is not None:
                self.r_chanson.set_selected(k)
        elif a in ("prise", "decompte"):
            self.r_mode.set_selected(1)
            if self.chanson and self.chanson.get("lignes"):
                self.r_de.set_selected(1)
                self.r_a.set_selected(min(4, len(self.chanson["lignes"]) - 1))
            self.commencer()
        elif a == "boucle":
            self.r_mode.set_selected(2)
        elif a == "resultat":
            self._scenario_resultat()
        elif a.startswith("ligne-"):
            n = int(a.split("-", 1)[1]) - 1

            def zoomer():
                if self.etat != "resultat":
                    return True
                lignes = [l for l in (self._tour() or {}).get("lignes") or [] if l.get("score") is not None]
                if 0 <= n < len(lignes):
                    self.courbe.zoomer(max(self.courbe.debut, lignes[n]["debut"] - 0.5), lignes[n]["fin"] + 0.5)
                return False
            GLib.timeout_add(200, zoomer)
        elif a == "calibrer":
            self.calibrer()
        elif a in ("doubler", "doubler-lancer"):
            # « Doubler ma prise… » : le dialogue (captures), ou le travail lancé dans la première voix (essais)
            def attendre():
                if self.etat != "resultat":
                    return True
                if a == "doubler":
                    self.doubler_ma_prise()
                elif self.fenetre.voix:
                    self._lancer_doubler(self.fenetre.voix[0]["id"])
                return False
            GLib.timeout_add(300, attendre)

    def _scenario_resultat(self):
        source = os.environ.get("VOIXLA_KARAOKE_PRISE")
        c = self.chanson
        if not source or c is None or not c.get("beat"):
            return
        self.r_mode.set_selected(0)
        p = self._passage()
        self.passage = p
        self.prise = K.nouvelle_prise(c["dossier"])
        shutil.copyfile(source, self.prise)
        self.plan = {"format": 1, "titre": c["titre"], "dossier": str(c["dossier"]), "voix": c["res"].voix,
                     "dossier_voix": str(c["res"].dossier_voix) if c["res"].dossier_voix else None,
                     "travail": str(c["res"].travail), "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
                     "passage": {"debut": p.debut, "fin": p.fin, "repetitions": p.repetitions, "mode": "entier"},
                     "lignes": [list(l) for l in c.get("lignes") or []],
                     "beat": {"source": str(c["beat"]), "fichier": ""},
                     "latence_ms": 0, "mesure": {"prise_debut_s": 0.0, "beat_debut_s": 0.0}, "micro": "essai"}
        K.ecrire_plan(self.prise, self.plan)
        self.analyser()
