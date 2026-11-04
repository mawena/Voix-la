"""Barre « en cours de lecture », en bas de la fenêtre, sous la barre latérale et le contenu.

Elle apparaît quand quelque chose joue (ou est en pause) ailleurs que sur la page affichée : sur la page qui lit, ses
propres commandes suffisent. Elle montre l'image de la vidéo (ou une icône), le titre, la voix et la piste, la position,
lecture / pause et stop ; un clic sur le titre ramène à la page qui lit, dans l'état exact où on l'a laissée.
"""

from gi.repository import Gtk, Pango

from .. import lecture as regles
from .lecture import lectures

ICONES = {"resultat": "video-x-generic-symbolic", "ecoute": "audio-headphones-symbolic"}


class BarreLecture(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["barre-lecture"])
        self.progres = Gtk.ProgressBar(css_classes=["barre-lecture-progres"], hexpand=True)
        self.append(self.progres)
        rangee = Gtk.Box(spacing=6, margin_start=6, margin_end=10, margin_top=4, margin_bottom=4)
        self.append(rangee)

        contenu = Gtk.Box(spacing=12)
        self.cadre = Gtk.Stack(css_classes=["barre-lecture-image"], valign=Gtk.Align.CENTER,
                               overflow=Gtk.Overflow.HIDDEN)
        self.cadre.set_size_request(40, 40)
        self.icone = Gtk.Image(icon_name=ICONES["resultat"], pixel_size=20, css_classes=["dim-label"])
        self.cadre.add_named(self.icone, "icone")
        # Gtk.Image (et non Gtk.Picture) : sa taille est celle de pixel_size, pas celle de la miniature (512 px)
        self.image = Gtk.Image(pixel_size=40)
        self.cadre.add_named(self.image, "image")
        contenu.append(self.cadre)
        textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        self.titre = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["heading"])
        self.detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["caption", "dim-label"])
        self.position = Gtk.Label(xalign=0, css_classes=["caption", "dim-label", "chiffres"])
        bas = Gtk.Box(spacing=4)
        bas.append(self.detail)
        bas.append(self.position)
        textes.append(self.titre)
        textes.append(bas)
        contenu.append(textes)
        self.b_retour = Gtk.Button(child=contenu, css_classes=["flat", "barre-lecture-retour"], hexpand=True,
                                   tooltip_text="Revenir à ce qui joue")
        self.b_retour.connect("clicked", lambda *_: lectures().revenir())
        rangee.append(self.b_retour)

        self.b_lecture = Gtk.Button(icon_name="media-playback-start-symbolic", css_classes=["circular"],
                                    valign=Gtk.Align.CENTER, tooltip_text="Lecture")
        self.b_lecture.connect("clicked", lambda *_: lectures().lecture_pause())
        rangee.append(self.b_lecture)
        self.b_arret = Gtk.Button(icon_name="media-playback-stop-symbolic", css_classes=["circular", "flat"],
                                  valign=Gtk.Align.CENTER, tooltip_text="Arrêter")
        self.b_arret.connect("clicked", lambda *_: lectures().arreter())
        rangee.append(self.b_arret)
        self._image = None

    def montrer(self, etat):
        """Met la barre à jour (rien de coûteux : appelé une fois par seconde pendant la lecture)."""
        if not etat.session:
            return
        self.titre.set_label(etat.titre or "")
        self.detail.set_label(regles.ligne_detail(etat))
        self.position.set_label(("· " if regles.ligne_detail(etat) else "") + regles.ligne_position(etat))
        self.progres.set_fraction(regles.fraction(etat))
        lit = etat.statut == regles.LECTURE
        self.b_lecture.set_icon_name("media-playback-pause-symbolic" if lit else "media-playback-start-symbolic")
        self.b_lecture.set_tooltip_text("Pause" if lit else "Lecture")
        self.b_lecture.update_property([Gtk.AccessibleProperty.LABEL], ["Pause" if lit else "Lecture"])
        self.b_arret.update_property([Gtk.AccessibleProperty.LABEL], ["Arrêter"])
        self.b_retour.set_tooltip_text("Revenir à l’écoute à l’aveugle" if etat.genre == "ecoute" else
                                       "Revenir à ce résultat")
        if etat.image != self._image:
            self._image = etat.image
            if etat.image:
                self.image.set_from_file(etat.image)
        self.icone.set_from_icon_name(ICONES.get(etat.genre, ICONES["resultat"]))
        self.cadre.set_visible_child_name("image" if etat.image else "icone")
