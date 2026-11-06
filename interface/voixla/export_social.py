"""Export pour les réseaux (sans GTK, testable) : une courte vidéo prête à partager (TikTok, Reels, statut WhatsApp,
Shorts), tirée d'un doublage.

- **Extrait** de 15 à 60 s, en temps du son doublé : celui du lecteur d'un résultat et de sa boucle A-B. Proposition
  automatique (proposer_extrait) : le refrain, c'est-à-dire la fenêtre qui chante le plus de lignes répétées de
  paroles.json, sinon le passage le plus chanté (paroles, sinon voisement de la voix convertie dans courbes.json).
- **Format** : vertical 9:16 (1080 × 1920), carré 1:1 (1080 × 1080) ou format d'origine (1920 au plus sur le grand
  côté) ; image entière sur un fond flou tiré d'elle-même, ou recadrée au centre (mise_en_page).
- **Paroles karaoké** incrustées par des sous-titres ASS à effet \\kf : le mot se remplit au fil de sa durée, comme
  dans le lecteur (texte blanc cerné de sombre, remplissage jaune #f6d32d), avec le même calage
  (voixla.paroles.ecart_lecture) et les mêmes lignes à l'écran (voixla.paroles.voisines : la suivante prend la place
  3 s avant de commencer). Traduction française sous la ligne, en option. Rendu par le filtre « ass » de ffmpeg.
- **Piste son** : le doublage (par défaut), sa version auto-tune, le beat seul ou ma voix seule. Dans une vidéo doublée
  à cinq pistes, la piste elle-même (-map 0:a:N) ; sinon le MP3, travail/instrumental.wav ou ma voix seule combinée.
- **Mention** discrète en surimpression, en haut à gauche, allumée par défaut : « Voix générée par IA ».
- **Fondu** d'entrée et de sortie, du son et de l'image.
- **Encodage** H.264 (High, niveau 4.1, yuv420p) et AAC 192 kb/s en mp4 « faststart », lu par les applications des
  téléphones ; débit vidéo plafonné à 5 Mb/s : 40 Mo au plus pour 60 s.
- **Sortie** : <dossier du doublage>/partage/<titre> - voix <Nom> - partage 1m05-1m35 9x16.mp4 (« (2) » si le nom
  est pris), écrit d'un coup (fichier temporaire caché, puis renommage).

Temps : t_son = temps du son doublé (MP3, lecteur, boucle A-B) ; la vidéo doublée compte en t_son + debut_son_video
(voixla.pistes.avance_piste) ; paroles.json en t_son + ecart (voixla.paroles.ecart_lecture) ; la vidéo produite en
t_son − début de l'extrait.

En ligne de commande (l'interface la lance sous nice, par file-attente au-delà de quelques secondes de calcul ;
VOIXLA_AVANCEMENT=1 : avancement en lignes JSON) :
  python3 -m voixla.export_social DOSSIER|FICHIER [--voix ID] [--debut T] [--fin T | --duree S] [--refrain]
      [--format vertical|carre|origine] [--cadrage flou|recadre] [--piste doublage|autotune|beat|ma_voix]
      [--sans-paroles] [--traduction] [--sans-mention | --mention TEXTE] [--sans-fondu] [--sortie FICHIER.mp4]
      [--apercu T IMAGE.png] [--ass FICHIER.ass] [--proposer]
  (DOSSIER : un dossier de sorties/ ; FICHIER : la vidéo doublée ou son MP3 ; T : « 65 », « 1:05 » ou « 1:05,5 »
   en temps du son doublé ; --refrain : l'extrait proposé ; --proposer : la proposition seule, en JSON ;
   --apercu : une image de la vidéo à T s du début de l'extrait, au lieu de l'export ; --ass : les sous-titres seuls)
"""

import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import chemins, formats, pistes, rapports
from . import paroles as P

DUREE_MIN_S = 15.0
DUREE_MAX_S = 60.0
DUREE_DEFAUT_S = 30.0
AMORCE_S = 0.5               # proposition : l'extrait part un peu avant la première ligne chantée
FIN_LIGNE_S = 0.4            # proposition : et finit un peu après la dernière
MENTION_DEFAUT = "Voix générée par IA"
FORMATS = ("vertical", "carre", "origine")
LIBELLES_FORMATS = {"vertical": "Vertical 9:16", "carre": "Carré 1:1", "origine": "Format d’origine"}
TAILLES_FORMATS = {"vertical": (1080, 1920), "carre": (1080, 1080)}
NOMS_FORMATS = {"vertical": "9x16", "carre": "1x1", "origine": "origine"}
CADRAGES = ("flou", "recadre")
LIBELLES_CADRAGES = {"flou": "Image entière sur fond flou", "recadre": "Recadrée au centre"}
PISTES = ("doublage", "autotune", "beat", "ma_voix")
LIBELLES_PISTES = {"doublage": "Doublage", "autotune": "Doublage (auto-tune)", "beat": "Beat seul",
                   "ma_voix": "Ma voix seule"}
SUFFIXES_PISTES = {"doublage": "", "autotune": " (auto-tune)", "beat": " (beat)", "ma_voix": " (voix seule)"}
GRAND_COTE_MAX = 1920        # format d'origine : au-delà, ramené à 1920 (taille raisonnable, lecture fluide)
CADENCE_MAX = 30.0           # au-delà (vidéo à 50 ou 60 i/s), 30 i/s
FONDU_IMAGE_S = 0.5
FONDU_SON_ENTREE_S = 0.5
FONDU_SON_SORTIE_S = 1.0
# Encodage : x264 « medium » à qualité constante, débit plafonné (5 Mb/s + 192 kb/s : 40 Mo au plus pour 60 s)
X264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "21", "-maxrate", "5M", "-bufsize", "10M",
        "-profile:v", "high", "-level:v", "4.1", "-pix_fmt", "yuv420p"]
AAC = ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
FILS_X264 = 4                # le poste est partagé : x264 n'en prend pas plus (sous nice)
DEBIT_MAX_MO_S = (5000 + 192) / 8 / 1000   # Mo par seconde au plus (plafond vidéo + son)
# Estimation du calcul (s de calcul par s de vidéo à 1080 × 1920 : 64 s pour 60 s mesurées sur le poste chargé, x264 à
# 4 fils sous nice) et seuil au-delà duquel l'export passe par file-attente, la file des travaux lourds du poste
FACTEUR_CALCUL = 1.1
SEUIL_FILE_S = 5.0
TICKET_FILE = ("voix-la export", "1", "2")      # nom, Go de RAM, cœurs libres
# Style des paroles, comme sur l'image du lecteur (gui/vue_paroles.py, style « video »)
POLICE = "Noto Sans"         # repli de fontconfig si elle manque (libass prend les glyphes ailleurs au besoin)
JAUNE = "#f6d32d"
BLANC = "#ffffff"
GRIS_TRADUCTION = "#f0f0f0"
ECHELLE_TRADUCTION = 0.62


class ErreurExport(RuntimeError):
    pass


# --- Réglages et extrait ---------------------------------------------------------------------------------------------

@dataclass
class Reglages:
    debut: float = 0.0              # début de l'extrait, en temps du son doublé (s)
    duree: float = DUREE_DEFAUT_S   # 15 à 60 s
    format: str = "vertical"        # FORMATS
    cadrage: str = "flou"           # CADRAGES (sans effet pour le format d'origine)
    piste: str = "doublage"         # PISTES
    paroles: bool = True            # paroles karaoké incrustées (s'il y en a)
    traduction: bool = False        # traduction française sous la ligne (s'il y en a une)
    mention: bool = True            # mention en surimpression
    texte_mention: str = MENTION_DEFAUT
    fondu: bool = True

    @property
    def fin(self):
        return self.debut + self.duree


def borner_extrait(debut, duree, total=None):
    """(début, durée) ramenés à un extrait possible : durée de 15 à 60 s (tout le son s'il est plus court), dans le
    son. Le début reste où il est tant que l'extrait tient ; sinon l'extrait recule pour finir à la fin du son."""
    d = min(max(float(duree or 0.0), DUREE_MIN_S), DUREE_MAX_S)
    debut = max(0.0, float(debut or 0.0))
    if total is not None and total > 0:
        d = min(d, total)
        debut = min(debut, max(0.0, total - d))
    return round(debut, 3), round(d, 3)


def extrait_de_boucle(boucle, total=None):
    """La boucle A-B du lecteur ((a, b), en temps du son doublé) en extrait (début, durée), ou None sans boucle
    complète. Plus courte que 15 s, elle est allongée après B ; plus longue que 60 s, coupée à 60 s après A."""
    if not boucle or len(boucle) != 2 or boucle[0] is None or boucle[1] is None:
        return None
    a, b = sorted(float(x) for x in boucle)
    return borner_extrait(a, b - a, total)


# --- Proposition automatique : refrain, ou passage le plus chanté ----------------------------------------------------

@dataclass
class Proposition:
    debut: float
    duree: float
    raison: str                 # « refrain » ou « passage le plus chanté »

    @property
    def libelle(self):
        return "le refrain" if self.raison == "refrain" else "le passage le plus chanté"


def normaliser(texte):
    """Texte comparable d'une ligne : minuscules, sans accents ni ponctuation, espaces réduites."""
    t = unicodedata.normalize("NFKD", texte or "").casefold()
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def _recouvrement(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def lignes_du_son(paroles, ecart=0.0):
    """[(début, fin, texte normalisé)] des lignes chantées, en temps du son doublé (temps des paroles − ecart)."""
    if paroles is None:
        return []
    return [(l.debut - ecart, l.fin - ecart, normaliser(l.texte)) for l in paroles.originale.lignes if l.fin > l.debut]


SEMBLABLES = 0.85            # deux lignes au moins aussi proches (difflib) sont la même ligne chantée deux fois


def repetees(lignes):
    """Les textes de ligne chantés au moins deux fois (deux mots au moins) : le refrain, d'ordinaire. Deux
    transcriptions presque identiques (un mot entendu autrement) comptent comme la même ligne."""
    import difflib
    textes = [t for _d, _f, t in lignes if len(t.split()) >= 2]
    sortie = set()
    for i, a in enumerate(textes):
        if a in sortie:
            continue
        for b in textes[i + 1:]:
            if a == b or difflib.SequenceMatcher(None, a, b).quick_ratio() >= SEMBLABLES and \
                    difflib.SequenceMatcher(None, a, b).ratio() >= SEMBLABLES:
                sortie.update((a, b))
    return sortie


def _ajuster_fin(debut, duree, lignes, total):
    """Une fin qui ne coupe pas une ligne : prolongée jusqu'au bout de la ligne coupée (60 s au plus, 8 s de plus au
    plus), sinon ramenée à la fin de la ligne d'avant (15 s au moins) ; telle quelle sinon."""
    fin = debut + duree
    coupee = next((l for l in lignes if l[0] < fin < l[1]), None)
    if coupee is None:
        return duree
    longue = coupee[1] + FIN_LIGNE_S - debut
    if longue <= min(DUREE_MAX_S, duree + 8.0) and (total is None or debut + longue <= total):
        return longue
    avant = [l[1] for l in lignes if l[1] <= coupee[0] and l[1] > debut]
    if avant:
        courte = max(avant) + FIN_LIGNE_S - debut
        if courte >= DUREE_MIN_S:
            return min(courte, duree)
    return duree


def proposer_extrait(paroles=None, ecart=0.0, voise=None, pas_s=0.02, total=None, duree=DUREE_DEFAUT_S):
    """L'extrait à proposer, en temps du son doublé : Proposition, ou None sans paroles ni courbe de voix.

    Avec paroles (et ecart, de voixla.paroles.ecart_lecture, pour passer en temps du son) : parmi les fenêtres de
    « duree » s qui commencent un peu avant une ligne (une ligne répétée, s'il y en a : l'extrait part sur le
    refrain), celle qui chante le plus, les lignes répétées comptant 2,5 fois ; la plus tôt en cas d'égalité. Sa fin
    est ensuite posée après une ligne, pas au milieu. Raison « refrain » si les lignes répétées font au moins le tiers
    du chant de l'extrait.
    Sinon, avec voise (une liste de valeurs de hauteur, 0 quand la voix ne chante pas, une toutes les pas_s, comme
    convertie_hz de courbes.json) : la fenêtre où la voix chante le plus longtemps, qui part juste avant la phrase
    chantée (au début de la phrase en cours, ou à la première qui suit)."""
    lignes = lignes_du_son(paroles, ecart)
    if total is None:
        fins = [l[1] for l in lignes] + ([len(voise) * pas_s] if voise else [])
        total = max(fins) + 2.0 if fins else None
    debut0, d = borner_extrait(0.0, duree, total)
    if lignes:
        refrain = repetees(lignes)
        meilleur = None
        for l in [l for l in lignes if l[2] in refrain] or lignes:
            debut = min(max(0.0, l[0] - AMORCE_S), max(0.0, (total or l[0] + d) - d))
            chante = sum(_recouvrement(a, b, debut, debut + d) for a, b, _t in lignes)
            rep = sum(_recouvrement(a, b, debut, debut + d) for a, b, t in lignes if t in refrain)
            score = chante + 1.5 * rep
            if meilleur is None or score > meilleur[0] + 1e-6:
                meilleur = (score, debut, chante, rep)
        _s, debut, chante, rep = meilleur
        d2 = _ajuster_fin(debut, d, lignes, total)
        raison = "refrain" if chante > 0 and rep >= chante / 3 else "passage le plus chanté"
        return Proposition(round(debut, 2), round(d2, 2), raison)
    if voise:
        n = len(voise)
        cumul = [0]
        for v in voise:
            cumul.append(cumul[-1] + (1 if isinstance(v, (int, float)) and v > 0 else 0))
        if cumul[-1] == 0:
            return None
        largeur = max(1, int(round(d / pas_s)))
        pas_fenetre = max(1, int(round(0.5 / pas_s)))
        meilleur = None
        for i in range(0, max(1, n - largeur + 1), pas_fenetre):
            v = cumul[min(n, i + largeur)] - cumul[i]
            if meilleur is None or v > meilleur[0]:
                meilleur = (v, i)
        i = meilleur[1]
        if _voise(voise, i):
            # au début de la phrase en cours (3 s au plus en arrière)
            j, limite = i, max(0, i - int(3.0 / pas_s))
            while j > limite and _voise(voise, j - 1):
                j -= 1
        else:
            # à la première phrase chantée de la fenêtre (elle n'y perd rien : la voix s'y tait avant)
            j = next((k for k in range(i, min(n, i + largeur)) if _voise(voise, k)), i)
        debut = max(0.0, j * pas_s - 0.3)
        debut, d = borner_extrait(debut, d, total)
        return Proposition(round(debut, 2), round(d, 2), "passage le plus chanté")
    return None


def _voise(voise, i):
    v = voise[i]
    return isinstance(v, (int, float)) and v > 0


def lire_voisement(dossier_voix):
    """(convertie_hz, pas_s) de courbes.json (sinon courbes-locales.json) d'une voix, ou (None, None)."""
    if dossier_voix is None:
        return None, None
    for nom in ("courbes.json", "courbes-locales.json"):
        d = chemins.lire_json(Path(dossier_voix) / nom)
        if isinstance(d, dict) and d.get("format") == 1 and isinstance(d.get("convertie_hz"), list):
            pas = d.get("pas_s")
            if isinstance(pas, (int, float)) and not isinstance(pas, bool) and pas > 0:
                return d["convertie_hz"], float(pas)
    return None, None


# --- Mise en page --------------------------------------------------------------------------------------------------

def _pair(x):
    return max(2, int(round(x / 2.0)) * 2)


def taille_origine(largeur, hauteur, grand_cote=GRAND_COTE_MAX):
    """La taille de sortie du format d'origine : celle de l'image, ramenée à 1920 au plus sur le grand côté, paire."""
    largeur, hauteur = max(2, int(largeur or 0)), max(2, int(hauteur or 0))
    k = min(1.0, grand_cote / max(largeur, hauteur))
    return _pair(largeur * k), _pair(hauteur * k)


def ajuster(largeur, hauteur, cadre_l, cadre_h):
    """La taille (paire) de l'image entière dans le cadre, proportions gardées."""
    k = min(cadre_l / max(1, largeur), cadre_h / max(1, hauteur))
    return min(cadre_l, _pair(largeur * k)), min(cadre_h, _pair(hauteur * k))


@dataclass
class MiseEnPage:
    largeur: int
    hauteur: int
    image: tuple                # (x, y, largeur, hauteur) de l'image dans le cadre
    fond_flou: bool             # l'image entière sur un fond flou tiré d'elle-même (sinon elle remplit le cadre)
    alignement: int             # des paroles (ASS) : 2 en bas au centre, 8 en haut au centre
    marge_v: int                # des paroles : depuis le bas (2) ou depuis le haut (8)
    marge_lr: int
    taille: int                 # police des paroles (px de la vidéo)
    taille_traduction: int
    taille_mention: int
    mention_x: int              # coin haut gauche de la mention
    mention_y: int


def mise_en_page(format_, cadrage, src_l, src_h):
    """Où vont l'image, les paroles et la mention, pour une image source de src_l × src_h (taille affichée).

    - Vertical : en image entière sur fond flou, l'image est un peu au-dessus du milieu (centre à 42 % de la hauteur)
      et les paroles juste dessous, sur le fond flou ; si la place manque (image déjà verticale) ou recadrée, les
      paroles sont sur l'image, leur bas aux trois quarts de la hauteur : au-dessus des légendes et des boutons que les
      applications posent en bas. La mention est en haut à gauche, sous leurs onglets (8,5 % de la hauteur).
    - Carré : de même, paroles en bas (7 % de marge) quand elles vont sur l'image.
    - Format d'origine : l'image telle quelle, paroles en bas comme des sous-titres.
    Police des paroles : 6,2 % du petit côté (67 px en 1080), traduction à 62 %, mention à 2,8 %."""
    if format_ == "vertical" or format_ == "carre":
        L, H = TAILLES_FORMATS[format_]
    else:
        L, H = taille_origine(src_l, src_h)
    base = min(L, H)
    taille = int(round(0.062 * base))
    vertical = format_ == "vertical"
    marge_bas = int(round((0.235 if vertical else 0.07) * H))
    sortie = MiseEnPage(L, H, (0, 0, L, H), False, 2, marge_bas, int(round(0.07 * L)), taille,
                        int(round(taille * ECHELLE_TRADUCTION)), max(14, int(round(0.028 * base))),
                        int(round(0.055 * L)), int(round((0.085 if vertical else 0.045) * H)))
    if format_ == "origine" or cadrage != "flou" or not src_l or not src_h:
        return sortie
    il, ih = ajuster(src_l, src_h, L, H)
    y = min(max(0, _pair(0.42 * H - ih / 2) if ih < H else 0), H - ih)
    x = (L - il) // 2
    sortie.image, sortie.fond_flou = (x, y, il, ih), (il, ih) != (L, H)
    if H - (y + ih) >= 0.24 * H:
        sortie.alignement, sortie.marge_v = 8, y + ih + int(round(0.035 * H))
    return sortie


# --- Sous-titres ASS à effet karaoké ---------------------------------------------------------------------------------

def couleur_ass(hexa, opacite=1.0):
    """« #f6d32d » → « &H002DD3F6 » (ASS : transparence, bleu, vert, rouge)."""
    h = hexa.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = max(0, min(255, int(round((1.0 - opacite) * 255))))
    return f"&H{a:02X}{b}{g}{r}".upper()


def temps_ass(s):
    """Secondes → « H:MM:SS.cc » (centièmes arrondis)."""
    cs = max(0, int(round(s * 100)))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    sec, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


def echapper_ass(texte):
    """Texte sans balise : accolades et barre oblique inverse (qui ouvrent les balises et \\N) remplacées."""
    return (texte or "").replace("\\", "∖").replace("{", "(").replace("}", ")").replace("\n", " ")


def fenetres(lignes, avance_s=P.AVANCE_FOCALE_S):
    """[(début, fin, indice)] : quand chaque ligne est à l'écran, en temps des paroles, comme au centre du karaoké du
    lecteur (voixla.paroles.voisines) : de 3 s avant son début (pas avant la fin de la précédente) jusqu'à sa fin, ou
    jusqu'au début de la suivante si celle-ci commence avant."""
    sortie, fin_prec = [], None
    for i, l in enumerate(lignes):
        fin = l.fin if i + 1 >= len(lignes) else min(l.fin, lignes[i + 1].debut)
        debut = l.debut - avance_s
        if fin_prec is not None:
            debut = max(debut, fin_prec)
        debut = min(debut, l.debut)
        if fin > debut:
            sortie.append((debut, fin, i))
        fin_prec = max(fin, fin_prec) if fin_prec is not None else fin
    return sortie


def karaoke(ligne, origine):
    """Le texte ASS d'une ligne affichée à partir de l'instant « origine » (temps des paroles) : chaque mot se remplit
    au fil de sa durée (\\kf), l'attente avant un mot est une syllabe vide (\\k) ; la ponctuation et l'espace qui
    suivent un mot prennent sa couleur dès qu'il est chanté, comme dans le lecteur. Un mot déjà chanté avant
    l'origine (ligne commencée avant l'extrait) est rempli d'emblée. Ligne sans mots : texte blanc, sans remplissage."""
    texte, mots = P.portees(ligne)
    if not mots:
        return "{\\1c&HFFFFFF&}" + echapper_ass(texte)

    def cs(t):
        return max(0, int(round((t - origine) * 100)))
    morceaux, pos = [], 0
    for k, (d, f, s, i) in enumerate(mots):
        m = ligne.mots[i]
        a, b = cs(m.debut), cs(m.fin)
        if a > pos:
            morceaux.append(f"{{\\k{a - pos}}}")
            pos = a
        duree = max(0, b - pos)
        prefixe = texte[:d] if k == 0 else ""
        morceaux.append(f"{{\\kf{duree}}}{echapper_ass(prefixe + texte[d:f])}")
        pos += duree
        sep = texte[f:s]
        if sep:
            suivant = cs(ligne.mots[mots[k + 1][3]].debut) if k + 1 < len(mots) else pos
            attente = max(0, suivant - pos)
            morceaux.append(f"{{\\k{attente}}}{echapper_ass(sep)}")
            pos += attente
    return "".join(morceaux)


def texte_ass(mise, couche=None, traduction=None, debut_p=0.0, duree=DUREE_DEFAUT_S, mention=None):
    """Le fichier ASS de l'extrait : la couche de paroles (voixla.paroles.Couche) en karaoké, la traduction sous la
    ligne (Couche, ou None), la mention (texte, ou None) pendant toute la durée. debut_p : le début de l'extrait en
    temps des paroles (temps du son doublé + ecart) ; la vidéo produite commence à 0."""
    t = mise.taille
    tr = mise.taille_traduction
    tm = mise.taille_mention
    ombre = couleur_ass("#000000", 0.5)
    contour = couleur_ass("#000000", 0.85)
    styles = [
        # Paroles : remplies en jaune (couleur primaire) depuis le blanc (secondaire), gras, contour sombre et ombre
        f"Style: Paroles,{POLICE},{t},{couleur_ass(JAUNE)},{couleur_ass(BLANC)},{contour},{ombre},-1,0,0,0,100,100,0,0,"
        f"1,{max(2.0, round(0.067 * t, 1))},{max(1.0, round(0.03 * t, 1))},{mise.alignement},{mise.marge_lr},"
        f"{mise.marge_lr},{mise.marge_v},1",
        f"Style: Traduction,{POLICE},{tr},{couleur_ass(GRIS_TRADUCTION)},{couleur_ass(GRIS_TRADUCTION)},{contour},"
        f"{ombre},0,-1,0,0,100,100,0,0,1,{max(1.5, round(0.075 * tr, 1))},{max(1.0, round(0.035 * tr, 1))},"
        f"{mise.alignement},{mise.marge_lr},{mise.marge_lr},{mise.marge_v},1",
        f"Style: Mention,{POLICE},{tm},{couleur_ass(BLANC, 0.82)},{couleur_ass(BLANC, 0.82)},"
        f"{couleur_ass('#000000', 0.55)},{couleur_ass('#000000', 0.35)},0,0,0,0,100,100,0.5,0,1,"
        f"{max(1.0, round(0.055 * tm, 1))},{max(0.5, round(0.03 * tm, 1))},7,{mise.mention_x},{mise.mention_x},"
        f"{mise.mention_y},1",
    ]
    evenements = []
    if mention:
        evenements.append(f"Dialogue: 0,{temps_ass(0)},{temps_ass(duree)},Mention,,0,0,0,,{echapper_ass(mention)}")
    if couche is not None and couche.lignes:
        lignes = couche.lignes
        fin_p = debut_p + duree
        for a, b, i in fenetres(lignes):
            # une ligne qui ne commence pas dans l'extrait n'y paraît pas (pas d'annonce juste avant la fin)
            if b <= debut_p or a >= fin_p or lignes[i].debut >= fin_p:
                continue
            a2, b2 = max(a, debut_p), min(b, fin_p)
            if b2 - a2 < 0.05:
                continue
            l = lignes[i]
            texte = karaoke(l, a2)
            if traduction is not None:
                c = P.correspondante(traduction.lignes, l)
                if c is not None and c.texte and normaliser(c.texte) != normaliser(l.texte):
                    texte += "\\N{\\rTraduction}" + echapper_ass(c.texte)
            evenements.append(f"Dialogue: 1,{temps_ass(a2 - debut_p)},{temps_ass(b2 - debut_p)},Paroles,,0,0,0,,"
                              f"{texte}")
    return "\n".join([
        "[Script Info]", "; Voix-là : export pour les réseaux", "ScriptType: v4.00+",
        f"PlayResX: {mise.largeur}", f"PlayResY: {mise.hauteur}", "WrapStyle: 0", "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        *styles, "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *evenements, ""])


def instant_apercu(couche, debut_p, duree):
    """Un instant de la vidéo produite (s depuis son début) où un mot se remplit : le milieu du mot du milieu de la
    première ligne d'au moins trois mots, chantée en entier dans l'extrait après le fondu d'entrée ; sinon le
    milieu du premier mot chanté de l'extrait ; sinon 40 % de la durée."""
    if couche is not None:
        candidats = []
        for l in couche.lignes:
            mots = [m for m in l.mots if debut_p + 1.0 <= m.debut and m.fin <= debut_p + duree - 1.0]
            if mots:
                candidats.append((len(mots) >= 3, mots))
        complets = [m for ok, m in candidats if ok]
        mots = complets[0] if complets else (candidats[0][1] if candidats else None)
        if mots:
            m = mots[len(mots) // 2] if complets else mots[0]
            return round((m.debut + m.fin) / 2 - debut_p, 3)
    return round(duree * 0.4, 3)


# --- Sources : le son de la piste choisie, l'image ------------------------------------------------------------------

@dataclass
class Son:
    fichier: Path
    piste_audio: int            # a:N dans ce fichier
    avance: float               # position dans le fichier = temps du son doublé + avance
    libelle: str


@dataclass
class Image:
    genre: str                  # « video », « pochette » (image fixe d'un MP3) ou « aucune » (dégradé du titre)
    fichier: Path | None = None
    avance: float = 0.0         # position dans le fichier = temps du son doublé + avance (vidéo)
    largeur: int = 0            # taille affichée (rotation et pixels non carrés compris)
    hauteur: int = 0
    cadence: float | None = None
    rotation: int = 0
    rapport_pixel: float = 1.0  # largeur d'un pixel / hauteur (sample_aspect_ratio)
    pochette: int | None = None # index de la pochette parmi les flux vidéo (genre « pochette »)


def _pistes_video(res, autotune, sonder):
    media = res.video_autotune if autotune else res.video
    if media is None:
        return {}
    titres = pistes._titres(media, res, sonder)
    return pistes.pistes_de_la_video(res, autotune, titres)


def pistes_possibles(res, sonder=False):
    """{piste: raison} des pistes son de l'export : "" si on peut la prendre, sinon pourquoi pas. Sans ffprobe
    (sonder faux) : les pistes de la vidéo d'après rapport.json, ou les titres déjà lus ; sinon les fichiers de
    travail/."""
    sortie = {}
    sortie["doublage"] = "" if (res.video or res.mp3) else "ni vidéo doublée ni MP3"
    if not res.autotune:
        sortie["autotune"] = "pas de version auto-tune"
    else:
        sortie["autotune"] = "" if (res.video_autotune or res.mp3_autotune) else "fichier auto-tune absent"
    dans = _pistes_video(res, False, sonder)
    beat = "beat" in dans or (res.travail / "instrumental.wav").is_file()
    sortie["beat"] = "" if beat else "travail/instrumental.wav manque"
    if "ma_voix" in dans:
        sortie["ma_voix"] = ""
    else:
        plan = pistes.plan_ma_voix(res.dossier_voix, res.rapport) if res.dossier_voix else pistes.plan_ma_voix(None)
        sortie["ma_voix"] = "" if plan.possible else (plan.explication or "pas de voix seule")
    return sortie


def choisir_son(res, piste, sonder=True, preparer=True):
    """Le son de la piste choisie (Son). Dans la vidéo doublée à plusieurs pistes : la piste elle-même, au temps de la
    vidéo (avance debut_son_video) ; sinon le MP3 (version auto-tune), travail/instrumental.wav ou ma voix seule
    (combinée en cache si preparer), au temps du son."""
    if piste not in PISTES:
        raise ErreurExport(f"piste inconnue : {piste} ({', '.join(PISTES)})")
    debut_son = pistes.debut_son_video(res)
    libelle = LIBELLES_PISTES[piste]
    if piste in ("doublage", "autotune"):
        at = piste == "autotune"
        if at and not res.autotune:
            raise ErreurExport("ce doublage n’a pas de version auto-tune")
        video, mp3 = (res.video_autotune, res.mp3_autotune) if at else (res.video, res.mp3)
        if video is not None:
            dans = _pistes_video(res, at, sonder)
            return Son(Path(video), int(dans.get("doublage", 0)), debut_son, libelle)
        if mp3 is not None:
            return Son(Path(mp3), 0, 0.0, libelle)
        raise ErreurExport("fichier auto-tune absent du dossier" if at else "ni vidéo doublée ni MP3 dans ce dossier")
    dans = _pistes_video(res, False, sonder)
    if piste in dans and res.video is not None:
        return Son(Path(res.video), int(dans[piste]), debut_son, libelle)
    if piste == "beat":
        f = res.travail / "instrumental.wav"
        if not f.is_file():
            raise ErreurExport("travail/instrumental.wav manque : pas de beat seul")
        return Son(f, 0, 0.0, libelle)
    if not preparer:
        f = pistes.ma_voix_prete(res.dossier_voix, res.rapport) if res.dossier_voix else None
        if f is None:
            raise ErreurExport("ma voix seule n’est pas encore combinée")
        return Son(Path(f), 0, 0.0, libelle)
    if res.dossier_voix is None:
        raise ErreurExport("doublage inachevé : pas de voix seule")
    try:
        return Son(Path(pistes.preparer_ma_voix(res.dossier_voix, res.rapport)), 0, 0.0, libelle)
    except pistes.ErreurPistes as e:
        raise ErreurExport(f"ma voix seule impossible : {e}") from e


def _fraction(v):
    try:
        if isinstance(v, str) and ("/" in v or ":" in v):
            n, d = re.split("[/:]", v, maxsplit=1)
            return float(n) / float(d) if float(d) else None
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def image_de(info):
    """L'image d'un fichier sondé par ffprobe (voixla.pistes.sonder) : Image « video » (première image animée), ou
    « pochette » (pochette intégrée d'un MP3), sinon None. Taille affichée : rotation (matrice d'affichage, ou tag
    rotate) et pixels non carrés pris en compte."""
    pochette, n = None, 0
    for f in info.get("streams") or []:
        if f.get("codec_type") != "video":
            continue
        if (f.get("disposition") or {}).get("attached_pic"):
            if pochette is None:
                pochette = (n, f)
            n += 1
            continue
        largeur, hauteur = int(f.get("width") or 0), int(f.get("height") or 0)
        rotation = 0
        for sd in f.get("side_data_list") or []:
            if isinstance(sd, dict) and _fraction(sd.get("rotation")) is not None:
                rotation = int(round(_fraction(sd.get("rotation")))) % 360
        tags = f.get("tags") or {}
        if not rotation and _fraction(tags.get("rotate")) is not None:
            rotation = int(round(_fraction(tags.get("rotate")))) % 360
        sar = _fraction(f.get("sample_aspect_ratio")) or 1.0
        sar = sar if 0.2 < sar < 5 else 1.0
        l_aff = int(round(largeur * sar))
        if rotation in (90, 270):
            l_aff, hauteur = hauteur, l_aff
        cadence = _fraction(f.get("avg_frame_rate")) or _fraction(f.get("r_frame_rate"))
        return Image("video", largeur=l_aff, hauteur=hauteur, cadence=cadence if cadence and cadence < 500 else None,
                     rotation=rotation, rapport_pixel=sar)
    if pochette is not None:
        i, f = pochette
        return Image("pochette", largeur=int(f.get("width") or 0), hauteur=int(f.get("height") or 0), pochette=i)
    return None


def choisir_image(res, son):
    """L'image de la vidéo produite : celle du fichier du son quand c'est une vidéo, sinon la vidéo doublée, sinon la
    pochette du MP3, sinon le dégradé de la pochette générée du titre (Image « aucune »)."""
    for f, avance in ((son.fichier, son.avance), (res.video, pistes.debut_son_video(res)),
                      (res.video_autotune, pistes.debut_son_video(res))):
        if f is None or Path(f).suffix.lower() not in pistes.CONTENEURS_VIDEO + (".avi", ".ts", ".flv", ".wmv"):
            continue
        try:
            im = image_de(pistes.sonder(f))
        except pistes.ErreurPistes:
            im = None
        if im is not None and im.genre == "video":
            im.fichier, im.avance = Path(f), avance
            return im
    for f in (son.fichier, res.mp3, res.mp3_autotune):
        if f is None or Path(f).suffix.lower() != ".mp3":
            continue
        try:
            im = image_de(pistes.sonder(f))
        except pistes.ErreurPistes:
            im = None
        if im is not None and im.genre == "pochette":
            im.fichier = Path(f)
            return im
    return Image("aucune")


def duree_du_son(son):
    """Durée du son doublé lisible dans ce fichier (s) : la durée du fichier, moins son avance ; None si inconnue."""
    d = pistes.duree_audio(son.fichier)
    return max(0.0, d - son.avance) if d else None


def charger_paroles(res, extraire=True):
    """Les paroles du doublage (voixla.paroles.Paroles) : paroles.json, sinon les sous-titres de la vidéo doublée
    (extraits dans le cache au besoin, si extraire) ; None s'il n'y en a pas."""
    p, raison = P.paroles_pour(res.dossier_voix, res.video)
    if p is None and raison == "a_extraire" and extraire and res.video is not None:
        try:
            P.extraire_sous_titres(res.video)
        except (P.ErreurParoles, OSError):
            return None
        p, _r = P.sous_titres_en_cache(res.video)
    return p


def ecart_paroles(res, paroles):
    """Temps des paroles = temps du son doublé + cet écart (voixla.paroles.ecart_lecture, piste sans image)."""
    return P.ecart_lecture(paroles, False, pistes.debut_son_video(res))


# --- Plan et commande ffmpeg -----------------------------------------------------------------------------------------

@dataclass
class Plan:
    res: object
    reglages: Reglages
    son: Son
    image: Image
    mise: MiseEnPage
    paroles: object = None              # voixla.paroles.Paroles, ou None (sans paroles ou paroles éteintes)
    ecart: float = 0.0
    total: float | None = None          # durée du son doublé, si connue
    titre: str = ""
    notes: list = field(default_factory=list)   # ce qui a changé ou manque (« pas de traduction »…)

    @property
    def debut_p(self):
        """Début de l'extrait en temps des paroles."""
        return self.reglages.debut + self.ecart

    def couches(self):
        """(couche des paroles, couche de la traduction) à incruster ; (None, None) sans paroles."""
        if self.paroles is None:
            return None, None
        deux = self.paroles.couches("deux")
        return deux[0], (deux[1] if self.reglages.traduction and len(deux) > 1 else None)

    def ass(self):
        couche, trad = self.couches()
        r = self.reglages
        return texte_ass(self.mise, couche, trad, self.debut_p, r.duree,
                         (r.texte_mention.strip() or MENTION_DEFAUT) if r.mention else None)


def preparer_plan(res, reglages, paroles=None, sonder=True, preparer=True):
    """Le plan d'un export : son et image choisis (ffprobe), extrait borné à la durée du son, mise en page, paroles.
    paroles : déjà lues (sinon lues ici, sous-titres extraits au besoin)."""
    r = replace(reglages)
    if r.format not in FORMATS:
        raise ErreurExport(f"format inconnu : {r.format} ({', '.join(FORMATS)})")
    if r.cadrage not in CADRAGES:
        raise ErreurExport(f"cadrage inconnu : {r.cadrage} ({', '.join(CADRAGES)})")
    try:
        son = choisir_son(res, r.piste, sonder=sonder, preparer=preparer)
    except ErreurExport:
        if preparer or r.piste != "ma_voix":
            raise
        son = choisir_son(res, "doublage", sonder=sonder, preparer=False)   # aperçu : l'image seule compte
    total = duree_du_son(son) if sonder else None
    total = total or res.duree_s
    notes = []
    debut, duree = borner_extrait(r.debut, r.duree, total)
    if abs(debut - r.debut) > 0.01 or abs(duree - r.duree) > 0.01:
        notes.append(f"extrait ramené à {formats.chrono_precis(debut)} – {formats.chrono_precis(debut + duree)}")
    r.debut, r.duree = debut, duree
    image = choisir_image(res, son) if sonder else Image("aucune")
    if image.genre == "video":
        mise = mise_en_page(r.format, r.cadrage, image.largeur, image.hauteur)
    elif image.genre == "pochette":
        mise = mise_en_page(r.format if r.format != "origine" else "carre", r.cadrage, image.largeur, image.hauteur)
    else:
        # ni image ni pochette (MP3 seul) : le dégradé de la pochette générée, les paroles au milieu
        mise = mise_en_page(r.format if r.format != "origine" else "carre", "recadre", 0, 0)
        mise.alignement, mise.marge_v = 5, 0
    p = paroles
    if r.paroles and p is None:
        p = charger_paroles(res)
    if not r.paroles:
        p = None
    elif p is None:
        notes.append("pas de paroles pour ce doublage")
    if p is not None and r.traduction and not p.a_deux_langues():
        notes.append("pas de traduction française")
    ecart = ecart_paroles(res, p) if p is not None else 0.0
    return Plan(res, r, son, image, mise, p, ecart, total, pistes.base_sorties(res), notes)


def echapper_filtre(chemin):
    """Un chemin de fichier en valeur d'option d'un filtre de ffmpeg (deux niveaux d'échappement : l'option, puis le
    graphe de filtres)."""
    v = str(chemin)
    for c in "\\':":
        v = v.replace(c, "\\" + c)
    for c in "\\'[],;":
        v = v.replace(c, "\\" + c)
    return v


def _hexa_ffmpeg(rgb):
    return "0x" + "".join(f"{int(round(x * 255)):02x}" for x in rgb)


def graphe_image(plan, ass, entree="0:v", apercu=None):
    """Le graphe de filtres de l'image (sortie [v]) : cadence, image entière sur fond flou ou recadrée, paroles et
    mention (filtre ass), fondus. apercu : l'instant (s depuis le début de l'extrait) de l'image d'aperçu, que les
    sous-titres et les fondus doivent voir à sa place (setpts)."""
    m, im, r = plan.mise, plan.image, plan.reglages
    L, H = m.largeur, m.hauteur
    etapes = []
    if apercu:
        etapes.append(f"setpts=PTS-STARTPTS+{apercu:.3f}/TB")
    if im.genre == "video":
        if abs(im.rapport_pixel - 1.0) > 0.01:
            etapes.append("scale=trunc(iw*sar/2)*2:ih,setsar=1")
        if not apercu and (im.cadence is None or im.cadence > CADENCE_MAX + 0.5):
            etapes.append(f"fps={CADENCE_MAX:g}")
    elif not apercu:
        etapes.append(f"fps={CADENCE_MAX:g}")
    tete = f"[{entree}]" + (",".join(etapes) if etapes else "null")
    x, y, il, ih = m.image
    if m.fond_flou:
        # fond : l'image remplissant le cadre, réduite au sixième, floutée et un peu assombrie, puis agrandie (peu de
        # calcul) ; dessus, l'image entière
        pl, ph = _pair(L / 6), _pair(H / 6)
        graphe = (f"{tete},split=2[fond0][avant0];"
                  f"[fond0]scale={pl}:{ph}:force_original_aspect_ratio=increase,crop={pl}:{ph},boxblur=8:2,"
                  f"eq=brightness=-0.13:saturation=1.1,scale={L}:{H}:flags=bicubic,setsar=1[fond];"
                  f"[avant0]scale={il}:{ih}:flags=lanczos,setsar=1[avant];"
                  f"[fond][avant]overlay={x}:{y}:format=auto")
    else:
        graphe = f"{tete},scale={L}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={L}:{H},setsar=1"
    graphe += f",format=yuv420p,ass=filename={echapper_filtre(ass)}"
    if r.fondu:
        graphe += (f",fade=t=in:st=0:d={FONDU_IMAGE_S:g},"
                   f"fade=t=out:st={max(0.0, r.duree - FONDU_IMAGE_S):.3f}:d={FONDU_IMAGE_S:g}")
    return graphe + "[v]"


def graphe_son(plan, entree):
    """Le graphe du son (sortie [a]) : la piste choisie, fondus d'entrée et de sortie."""
    r = plan.reglages
    etapes = ["aresample=async=1:first_pts=0"]
    if r.fondu:
        etapes += [f"afade=t=in:st=0:d={FONDU_SON_ENTREE_S:g}",
                   f"afade=t=out:st={max(0.0, r.duree - FONDU_SON_SORTIE_S):.3f}:d={FONDU_SON_SORTIE_S:g}"]
    return f"[{entree}]" + ",".join(etapes) + "[a]"


def entrees(plan, apercu=None, pochette_png=None):
    """Les entrées de ffmpeg ([options…, -i, fichier]…) et les étiquettes de l'image et du son dans le graphe."""
    r, im, son = plan.reglages, plan.image, plan.son
    debut, duree = r.debut + (apercu or 0.0), (0.5 if apercu is not None else r.duree)
    args, n = [], 0
    if im.genre == "video":
        args += ["-ss", f"{max(0.0, debut + im.avance):.3f}", "-t", f"{duree:.3f}", "-i", str(im.fichier)]
        image, n = f"{n}:v:0", n + 1
    elif im.genre == "pochette" and pochette_png is not None:
        args += ["-loop", "1", "-framerate", f"{CADENCE_MAX:g}", "-t", f"{duree:.3f}", "-i", str(pochette_png)]
        image, n = f"{n}:v:0", n + 1
    else:
        c0, c1, angle = _couleurs_titre(plan)
        L, H = plan.mise.largeur, plan.mise.hauteur
        rad = math.radians(angle)
        x0, y0 = int(L / 2 - math.cos(rad) * L / 2), int(H / 2 - math.sin(rad) * H / 2)
        x1, y1 = L - x0, H - y0
        args += ["-f", "lavfi", "-t", f"{duree:.3f}", "-i",
                 f"gradients=s={L}x{H}:c0={_hexa_ffmpeg(c0)}:c1={_hexa_ffmpeg(c1)}:x0={x0}:y0={y0}:x1={x1}:y1={y1}"
                 f":n=2:speed=0.006:r={CADENCE_MAX:g}"]
        image, n = f"{n}:v:0", n + 1
    son = None
    if apercu is None:
        if im.genre == "video" and Path(plan.son.fichier) == Path(im.fichier) and \
                abs(plan.son.avance - im.avance) < 1e-6:
            son = f"0:a:{plan.son.piste_audio}"
        else:
            args += ["-ss", f"{max(0.0, r.debut + plan.son.avance):.3f}", "-t", f"{r.duree:.3f}", "-i",
                     str(plan.son.fichier)]
            son = f"{n}:a:{plan.son.piste_audio}"
    return args, image, son


def _couleurs_titre(plan):
    from . import bibliotheque
    return bibliotheque.couleurs_pochette(getattr(plan.res, "titre_court", "") or plan.titre)


def commande(plan, sortie, ass, apercu=None, pochette_png=None, fils=FILS_X264):
    """La commande ffmpeg (sous nice) de l'export (ou de l'image d'aperçu à l'instant apercu, en PNG)."""
    args, image, son = entrees(plan, apercu, pochette_png)
    graphe = graphe_image(plan, ass, image, apercu)
    cmd = ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args]
    if apercu is not None:
        return cmd + ["-filter_complex", graphe, "-map", "[v]", "-frames:v", "1", "-f", "image2", "-c:v", "png",
                      str(sortie)]
    r = plan.reglages
    cadence = plan.image.cadence if plan.image.genre == "video" and plan.image.cadence and \
        plan.image.cadence <= CADENCE_MAX + 0.5 else CADENCE_MAX
    titre = f"{plan.titre} - extrait {formats.chrono(r.debut)}"
    meta = ["-map_metadata", "-1", "-metadata", f"title={titre}"]
    if r.mention:
        meta += ["-metadata", f"comment={r.texte_mention.strip() or MENTION_DEFAUT} (Voix-là)"]
    return cmd + ["-filter_complex", f"{graphe};{graphe_son(plan, son)}", "-map", "[v]", "-map", "[a]",
                  *X264, "-g", str(max(1, int(round(2 * cadence)))), "-threads", str(fils), *AAC,
                  *meta, "-movflags", "+faststart", "-t", f"{r.duree:.3f}", "-f", "mp4", str(sortie)]


def extraire_pochette(fichier, index, sortie):
    """La pochette intégrée d'un MP3 (flux vidéo n° index) en PNG."""
    r = subprocess.run(["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                        "-i", str(fichier), "-map", f"0:v:{int(index or 0)}", "-frames:v", "1", "-f", "image2",
                        "-c:v", "png", str(sortie)], capture_output=True, text=True, timeout=60,
                       stdin=subprocess.DEVNULL)
    if r.returncode or not Path(sortie).is_file():
        raise ErreurExport(f"pochette illisible : {(r.stderr or '').strip()[-200:] or r.returncode}")
    return Path(sortie)


# --- Sortie --------------------------------------------------------------------------------------------------------

def dossier_partage(res):
    """<dossier du doublage>/partage/ : les vidéos à partager (hors de la liste des sorties de doubler)."""
    return Path(res.dossier) / "partage"


def _instant_nom(s, haut=False):
    s = max(0.0, s)
    s = int(math.ceil(s - 1e-6)) if haut else int(math.floor(s + 1e-6))
    m, r = divmod(s, 60)
    return f"{m}m{r:02d}"


def nom_sortie(res, reglages):
    """« <titre> - voix <Nom> - partage 1m05-1m35 9x16.mp4 » (« (beat) », « (auto-tune) », « (voix seule) » pour une
    autre piste que le doublage)."""
    r = reglages
    base = pistes.base_sorties(res)
    nom = (f"{base} - partage {_instant_nom(r.debut)}-{_instant_nom(r.debut + r.duree, haut=True)} "
           f"{NOMS_FORMATS.get(r.format, r.format)}{SUFFIXES_PISTES.get(r.piste, '')}")
    for c in '/\\:*?"<>|\0':
        nom = nom.replace(c, "-")
    return nom.strip(" .")[:200] + ".mp4"


def sortie_libre(chemin):
    """Le chemin lui-même s'il est libre, sinon « … (2).mp4 », « … (3).mp4 »…"""
    chemin = Path(chemin)
    if not chemin.exists():
        return chemin
    for k in range(2, 1000):
        autre = chemin.with_name(f"{chemin.stem} ({k}){chemin.suffix}")
        if not autre.exists():
            return autre
    raise ErreurExport(f"trop de fichiers déjà nommés {chemin.name}")


def sortie_par_defaut(res, reglages):
    return sortie_libre(dossier_partage(res) / nom_sortie(res, reglages))


def fichiers_partages(res):
    """Les vidéos à partager déjà faites pour ce doublage (cette voix), dans partage/ : pour la corbeille."""
    d = dossier_partage(res)
    prefixe = f"{pistes.base_sorties(res)} - partage "
    try:
        return sorted(f for f in d.iterdir() if f.is_file() and f.name.startswith(prefixe) and f.suffix == ".mp4")
    except OSError:
        return []


def estimation_calcul_s(reglages, mise=None):
    """Temps de calcul probable de l'export (s), d'après la taille de la vidéo et sa durée."""
    l, h = (mise.largeur, mise.hauteur) if mise else TAILLES_FORMATS.get(reglages.format, (1920, 1080))
    return reglages.duree * FACTEUR_CALCUL * (l * h) / (1080 * 1920)


def taille_max_mo(duree):
    """La taille que l'export ne dépasse pas (plafond de débit), en Mo."""
    return duree * DEBIT_MAX_MO_S


def commande_interface(python, res, reglages, sortie, file_attente=None):
    """La commande que lance l'interface : ce module en ligne de commande, sous nice ; par file-attente (la file des
    travaux lourds du poste) quand le calcul dépasse SEUIL_FILE_S."""
    r = reglages
    cmd = ["nice", "-n", "10", python, "-m", "voixla.export_social", str(res.dossier),
           *(["--voix", res.voix] if res.voix else []), "--debut", f"{r.debut:.3f}", "--duree", f"{r.duree:.3f}",
           "--format", r.format, "--cadrage", r.cadrage, "--piste", r.piste,
           *([] if r.paroles else ["--sans-paroles"]), *(["--traduction"] if r.traduction else []),
           *([f"--mention={r.texte_mention.strip() or MENTION_DEFAUT}"] if r.mention else ["--sans-mention"]),
           *([] if r.fondu else ["--sans-fondu"]), "--sortie", str(sortie)]
    if file_attente and estimation_calcul_s(r) > SEUIL_FILE_S:
        cmd = [file_attente, *TICKET_FILE, "--", *cmd]
    return cmd


# --- Exécution -----------------------------------------------------------------------------------------------------

def _tmp(sortie):
    sortie = Path(sortie)
    return sortie.with_name(f".{sortie.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp{sortie.suffix}")


def _lancer(cmd, duree=None, avancement=None):
    """ffmpeg, avec son avancement (-progress) ; ErreurExport s'il échoue. Arrêté avec le processus (SIGTERM)."""
    if avancement is not None and duree:
        cmd = [*cmd[:-1], "-progress", "pipe:1", "-nostats", cmd[-1]]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
    try:
        if avancement is not None and duree:
            pistes.lire_progression(p.stdout, duree, avancement)
        _sortie, erreur = p.communicate()
    except BaseException:
        p.kill()
        p.wait()
        raise
    if p.returncode:
        raise ErreurExport((erreur or "").strip()[-600:] or f"ffmpeg a échoué ({p.returncode})")


class _Temporaire:
    """Un dossier temporaire pour les sous-titres et la pochette, dans le cache (VOIXLA_CACHE), effacé à la fin."""

    def __enter__(self):
        d = chemins.dossier_cache() / "partage"
        d.mkdir(parents=True, exist_ok=True)
        self.chemin = Path(tempfile.mkdtemp(prefix="export-", dir=d))
        return self.chemin

    def __exit__(self, *_):
        shutil.rmtree(self.chemin, ignore_errors=True)


def _pochette(plan, dossier):
    if plan.image.genre != "pochette":
        return None
    return extraire_pochette(plan.image.fichier, plan.image.pochette, dossier / "pochette.png")


def exporter(res, reglages, sortie=None, avancement=None, paroles=None):
    """Fait la vidéo à partager. sortie : le fichier (sinon partage/<nom>, « (2) » si pris). avancement(fraction)
    pendant l'encodage. Écriture d'un coup : fichier temporaire caché à côté, puis renommage. Rend un dict :
    sortie, taille_o, duree_s (de la vidéo), temps_s (du calcul), largeur, hauteur, notes."""
    t0 = time.monotonic()
    plan = preparer_plan(res, reglages, paroles=paroles)
    sortie = Path(sortie) if sortie else sortie_par_defaut(res, plan.reglages)
    if sortie.suffix.lower() != ".mp4":
        raise ErreurExport(f"la vidéo à partager est un .mp4 (pas « {sortie.suffix or 'sans extension'} »)")
    sortie.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(sortie)
    try:
        with _Temporaire() as d:
            ass = d / "paroles.ass"
            ass.write_text(plan.ass(), encoding="utf-8")
            _lancer(commande(plan, tmp, ass, pochette_png=_pochette(plan, d)), plan.reglages.duree, avancement)
        os.replace(tmp, sortie)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"sortie": str(sortie), "taille_o": sortie.stat().st_size, "duree_s": plan.reglages.duree,
            "debut_s": plan.reglages.debut, "temps_s": round(time.monotonic() - t0, 2),
            "largeur": plan.mise.largeur, "hauteur": plan.mise.hauteur, "notes": plan.notes}


def apercu(res, reglages, sortie_png, instant=None, paroles=None):
    """Une image de la vidéo à partager, en PNG, à instant (s depuis le début de l'extrait ; par défaut un instant où
    un mot se remplit, instant_apercu). Léger (une image) : jamais par file-attente, et ma voix seule n'y est pas
    combinée (seule l'image compte). Rend (fichier, instant)."""
    plan = preparer_plan(res, reglages, paroles=paroles, preparer=False)
    couche, _t = plan.couches()
    if instant is None:
        instant = instant_apercu(couche, plan.debut_p, plan.reglages.duree)
    instant = max(0.0, min(float(instant), max(0.0, plan.reglages.duree - 0.05)))
    sortie_png = Path(sortie_png)
    sortie_png.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(sortie_png)
    try:
        with _Temporaire() as d:
            ass = d / "paroles.ass"
            ass.write_text(plan.ass(), encoding="utf-8")
            r = subprocess.run(commande(plan, tmp, ass, apercu=instant, pochette_png=_pochette(plan, d)),
                               capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        if r.returncode or not tmp.is_file():
            raise ErreurExport((r.stderr or "").strip()[-400:] or f"aperçu impossible ({r.returncode})")
        os.replace(tmp, sortie_png)
    finally:
        if tmp.exists():
            tmp.unlink()
    return sortie_png, instant


# --- Ligne de commande -----------------------------------------------------------------------------------------------

def trouver_resultat(chemin, voix=None):
    """Le doublage d'un dossier de sorties/ (la voix donnée, sinon la seule), ou celui d'une vidéo doublée ou d'un
    MP3 (sa voix)."""
    chemin = Path(chemin).expanduser()
    dossier = chemin if chemin.is_dir() else chemin.parent
    resultats = [r for r in rapports.lire_dossier(dossier) if r.video or r.mp3]
    if chemin.is_file():
        resultats = [r for r in resultats if chemin.resolve() in
                     {Path(f).resolve() for f in (r.video, r.mp3, r.video_autotune, r.mp3_autotune) if f}] or resultats
    if voix:
        resultats = [r for r in resultats if r.voix == voix or (r.voix_nom or "").casefold() == voix.casefold()]
    if not resultats:
        raise ErreurExport(f"aucun doublage dans {dossier}" + (f" pour la voix {voix}" if voix else ""))
    if len(resultats) > 1:
        raise ErreurExport("plusieurs voix dans ce dossier : --voix " + " | ".join(r.voix or "?" for r in resultats))
    return resultats[0]


def _lire_temps(texte, option):
    v = formats.lire_secondes(texte)
    if v is None:
        raise ErreurExport(f"{option} : temps illisible « {texte} » (65, 1:05 ou 1:05,5)")
    return v


def _arguments(argv):
    import argparse
    a = argparse.ArgumentParser(prog="python3 -m voixla.export_social", description="Vidéo courte à partager, tirée "
                                "d'un doublage (TikTok, Reels, statut WhatsApp, Shorts).")
    a.add_argument("chemin", help="dossier de sorties/<titre>/, ou la vidéo doublée, ou son MP3")
    a.add_argument("--voix", help="identifiant (ou nom) de la voix, si le dossier en a plusieurs")
    a.add_argument("--debut", help="début de l'extrait, en temps du son doublé (65, 1:05, 1:05,5)")
    fin = a.add_mutually_exclusive_group()
    fin.add_argument("--fin", help="fin de l'extrait")
    fin.add_argument("--duree", help="durée de l'extrait (15 à 60 s, 30 par défaut)")
    a.add_argument("--refrain", action="store_true", help="l'extrait proposé : le refrain, ou le passage le plus chanté")
    a.add_argument("--format", choices=FORMATS, default="vertical")
    a.add_argument("--cadrage", choices=CADRAGES, default="flou")
    a.add_argument("--piste", choices=PISTES, default="doublage")
    a.add_argument("--sans-paroles", action="store_true")
    a.add_argument("--traduction", action="store_true", help="traduction française sous la ligne")
    m = a.add_mutually_exclusive_group()
    m.add_argument("--sans-mention", action="store_true")
    m.add_argument("--mention", help=f"texte de la mention (« {MENTION_DEFAUT} » par défaut)")
    a.add_argument("--sans-fondu", action="store_true")
    a.add_argument("--sortie", help="fichier .mp4 (sinon <dossier>/partage/…)")
    a.add_argument("--apercu", nargs=2, metavar=("T", "IMAGE"), help="une image à T s du début de l'extrait (« auto » "
                   ": un mot qui se remplit), en PNG, au lieu de l'export")
    a.add_argument("--ass", metavar="FICHIER", help="écrire les sous-titres ASS seuls, sans encoder")
    a.add_argument("--proposer", action="store_true", help="donner la proposition d'extrait, en JSON, sans encoder")
    return a.parse_args(argv)


def _proposition(res, paroles):
    voise, pas = lire_voisement(res.dossier_voix)
    return proposer_extrait(paroles, ecart_paroles(res, paroles) if paroles is not None else 0.0, voise, pas or 0.02,
                            res.duree_s)


def _interrompre(*_):
    raise KeyboardInterrupt


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = _arguments(argv)
    # SIGTERM (« Annuler » de l'interface, au groupe de processus) : on sort proprement (fichier temporaire effacé)
    signal.signal(signal.SIGTERM, _interrompre)
    debut = time.time()
    try:
        res = trouver_resultat(args.chemin, args.voix)
        paroles = charger_paroles(res) if not args.sans_paroles or args.refrain or args.proposer else None
        if args.proposer:
            p = _proposition(res, paroles)
            print(json.dumps({"debut_s": p.debut, "duree_s": p.duree, "raison": p.raison} if p else
                             {"debut_s": None, "raison": "ni paroles ni courbe de voix"}, ensure_ascii=False))
            return 0
        r = Reglages(format=args.format, cadrage=args.cadrage, piste=args.piste, paroles=not args.sans_paroles,
                     traduction=args.traduction, mention=not args.sans_mention,
                     texte_mention=args.mention or MENTION_DEFAUT, fondu=not args.sans_fondu)
        if args.refrain and args.debut is None:
            p = _proposition(res, paroles)
            if p is None:
                raise ErreurExport("pas de proposition : ni paroles ni courbe de voix ; donnez --debut")
            r.debut, r.duree = p.debut, p.duree
            print(json.dumps({"proposition": p.raison, "debut_s": p.debut, "duree_s": p.duree}, ensure_ascii=False),
                  flush=True)
        if args.debut is not None:
            r.debut = _lire_temps(args.debut, "--debut")
        if args.fin is not None:
            r.duree = _lire_temps(args.fin, "--fin") - r.debut
        elif args.duree is not None:
            r.duree = _lire_temps(args.duree, "--duree")
        if args.ass:
            plan = preparer_plan(res, r, paroles=paroles if r.paroles else None)
            Path(args.ass).write_text(plan.ass(), encoding="utf-8")
            print(json.dumps({"ass": args.ass, "debut_s": plan.reglages.debut, "duree_s": plan.reglages.duree},
                             ensure_ascii=False))
            return 0
        if args.apercu:
            t = None if args.apercu[0] == "auto" else _lire_temps(args.apercu[0], "--apercu")
            f, t = apercu(res, r, args.apercu[1], t, paroles=paroles if r.paroles else None)
            print(json.dumps({"apercu": str(f), "instant_s": t, "temps_s": round(time.time() - debut, 2)},
                             ensure_ascii=False))
            return 0
        av = pistes.avancement_cli("export")
        info = exporter(res, r, args.sortie, av, paroles=paroles if r.paroles else None)
        print(json.dumps(info, ensure_ascii=False), flush=True)
        return 0
    except KeyboardInterrupt:
        print("export annulé", file=sys.stderr)
        return 130
    except (ErreurExport, pistes.ErreurPistes, OSError) as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
