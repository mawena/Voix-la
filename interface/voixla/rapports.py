"""Lecture des sorties de doubler (sans GTK, testable) : dossiers sorties/<titre>/, rapport.json, source.json, frise
d'octave, durées par étape, et modèle de durée pour estimer les travaux à venir.

sorties/<titre>/
  <titre> - voix <Nom>.<mp4|mkv|webm|mov>   <titre> - voix <Nom>.mp3   comparaison_<N>pas.mp3
  travail/source.json  travail/original.wav  travail/<voix>/rapport.json …
Un résultat = un dossier de sortie × une voix (travail/<voix>/). Un dossier sans rapport est « inachevé ».
"""

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from . import formats, moteur

EXT_VIDEO = (".mp4", ".mkv", ".webm", ".mov", ".m4v")
NOMS_MODELES = {"yingmusic": "YingMusic-SVC", "chant44k": "Seed-VC 44 kHz", "parole22k": "Seed-VC 22 kHz (parole)"}
NOMS_MOTEURS = {"yingmusic": "YingMusic-SVC", "seedvc": "Seed-VC"}
VOIX_DANS_NOM = re.compile(r" - voix (.+)$")
EXPORT_DANS_NOM = re.compile(r" \((?:beat|voix seule)\)$")   # même motif que voixla.pistes
AUTOTUNE_DANS_NOM = re.compile(r" \(auto-tune\)$")           # version auto-tune de doubler : « … - voix X (auto-tune) »
FRISE_LIGNE = re.compile(r"^(\d+):(\d+(?:\.\d+)?)–(\d+):(\d+(?:\.\d+)?)\s+(?:voix (\S+(?: \d)?))?\s*f0\s+(\d+) Hz\s+→\s+"
                         r"([+\-−]?\d+(?:\.\d+)?) dt\s+\((.*)\)$")


def _json(chemin):
    try:
        d = json.loads(Path(chemin).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _mtime(p):
    try:
        return p.stat().st_mtime
    except (OSError, AttributeError):
        return 0.0


@dataclass
class Resultat:
    dossier: Path
    voix: str | None                 # identifiant (travail/<voix>/)
    rapport: dict | None = None
    source: dict | None = None
    video: Path | None = None        # vidéo doublée
    mp3: Path | None = None
    voix_nom: str = ""
    comparaisons: list = field(default_factory=list)
    video_autotune: Path | None = None   # « … - voix X (auto-tune).<ext> » (doubler --autotune N)
    mp3_autotune: Path | None = None

    @property
    def cle(self):
        return f"{self.dossier}|{self.voix}"

    @property
    def voix_membres(self):
        """Les voix réelles : [« a »], ou [« a », « b »] pour une voix par chanteur (dossier « a+b »)."""
        return moteur.membres_voix(self.voix)

    @property
    def plusieurs_voix(self):
        return len(self.voix_membres) >= 2

    @property
    def voix_cibles(self):
        """[{cle, nom, …}] des voix cibles du rapport (une voix par chanteur), sinon []."""
        vc = (self.rapport or {}).get("voix_cibles")
        voix = vc.get("voix") if isinstance(vc, dict) else None
        return [v for v in voix if isinstance(v, dict)] if isinstance(voix, list) else []

    @property
    def autotune(self):
        """La section « autotune » du rapport si la version auto-tune est valide (applique vrai), sinon None."""
        at = (self.rapport or {}).get("autotune")
        return at if isinstance(at, dict) and at.get("applique") else None

    @property
    def lrc_autotune(self):
        if not self.autotune:
            return None
        nom = (self.autotune.get("fichiers") or {}).get("lrc")
        p = self.dossier / nom if nom else None
        return p if p is not None and p.is_file() else None

    @property
    def titre(self):
        return self.dossier.name

    @property
    def titre_court(self):
        return moteur.EXTRAIT_NOM.sub("", self.dossier.name)

    @property
    def groupe(self):
        """Sous-dossier de sorties/ où le résultat est rangé (« essais-mix-v2 »), ou ''."""
        return getattr(self, "_groupe", "")

    @property
    def extrait(self):
        """(debut, duree) demandés, d'après le nom du dossier ; None pour une vidéo entière."""
        return moteur.extrait_du_nom(self.dossier.name)

    @property
    def travail(self):
        return self.dossier / "travail"

    @property
    def dossier_voix(self):
        return self.travail / self.voix if self.voix else None

    @property
    def complet(self):
        return self.rapport is not None and (self.video is not None or self.mp3 is not None)

    @property
    def mode(self):
        return (self.rapport or {}).get("mode")

    @property
    def duree_s(self):
        r = self.rapport or {}
        if r.get("duree_s"):
            return float(r["duree_s"])
        if self.source and self.source.get("duree"):
            return float(self.source["duree"])
        return None

    @property
    def date(self):
        for p in (self.video, self.mp3, self.dossier_voix / "rapport.json" if self.dossier_voix else None, self.dossier):
            t = _mtime(p) if p else 0
            if t:
                return t
        return 0.0

    @property
    def video_source(self):
        """La vidéo d'origine (travail/source.json), ou None si elle n'est pas indiquée."""
        v = (self.source or {}).get("video")
        return Path(v) if v else None

    @property
    def source_presente(self):
        v = self.video_source
        return bool(v and v.is_file())

    @property
    def decalage_source_s(self):
        """Position dans la vidéo d'origine = position dans le doublage + ce décalage (début de l'extrait)."""
        s = self.source or {}
        return float(s.get("debut") or 0.0)

    @property
    def original_wav(self):
        p = self.travail / "original.wav"
        return p if p.is_file() else None

    @property
    def modele(self):
        return (self.rapport or {}).get("modele")

    @property
    def pas(self):
        r = self.rapport or {}
        v = (r.get("variantes") or {}).get("voix_convertie.flac") or {}
        return v.get("pas_diffusion") or (r.get("reglages") or {}).get("pas")

    def _variante(self):
        return ((self.rapport or {}).get("variantes") or {}).get("voix_convertie.flac") or {}

    @property
    def moteur(self):
        """Moteur réellement utilisé : yingmusic ou seedvc (None pour un rapport d'avant le choix du moteur)."""
        r = self.rapport or {}
        m = r.get("moteur") or (r.get("moteur_info") or {}).get("utilise") or self._variante().get("moteur")
        if m:
            return m
        return "seedvc" if r.get("modele") in ("chant44k", "parole22k") else None

    def libelle_moteur(self):
        """« YingMusic-SVC », « Seed-VC 44 kHz », « Seed-VC 22 kHz (parole) »…"""
        modele = self.modele
        if modele in NOMS_MODELES:
            return NOMS_MODELES[modele]
        return NOMS_MOTEURS.get(self.moteur or "", self.moteur or modele or "")

    @property
    def moteur_demande(self):
        return ((self.rapport or {}).get("moteur_info") or {}).get("chant_demande")

    @property
    def repli(self):
        """Raison du repli sur Seed-VC quand YingMusic-SVC a échoué, sinon None."""
        info = (self.rapport or {}).get("moteur_info") or {}
        return info.get("repli") or self._variante().get("repli")

    def resume_nettoyage(self):
        """(appliqué, texte) : niveaux de la voix principale, des chœurs et de la reverb retirée, ou la raison ;
        None pour un rapport d'avant le nettoyage."""
        n = (self.rapport or {}).get("nettoyage")
        if not isinstance(n, dict):
            return None
        if n.get("applique"):
            nv = n.get("niveaux_db") or {}
            morceaux = []
            for cle, nom in (("lead", "voix principale"), ("choeurs", "chœurs"), ("reverb_jetee", "reverb retirée")):
                if nv.get(cle) is not None:
                    morceaux.append(f"{nom} {formats.nombre(nv[cle])} dBFS")
            if nv.get("lead") is not None and nv.get("choeurs") is not None:
                morceaux.append(f"chœurs {formats.nombre(nv['lead'] - nv['choeurs'], 0)} dB sous la voix principale")
            return True, " · ".join(morceaux) or "appliqué"
        return False, n.get("raison") or ("non demandé" if not n.get("demande") else "non appliqué")

    def raison_hauteur(self):
        """Pourquoi cette décision d'octave (voix principale), en une phrase, ou ''."""
        d = (self.rapport or {}).get("decision_hauteur") or {}
        vp = d.get("voix_principale") or {}
        morceaux = [str(vp["raison"])] if vp.get("raison") else []
        if d.get("pourquoi_une_voix"):
            morceaux.append(f"une seule voix : {d['pourquoi_une_voix']}")
        return " ; ".join(morceaux)

    def resume_hauteur(self):
        """Décision d'octave en une phrase."""
        r = self.rapport or {}
        d = r.get("decision_hauteur") or {}
        if r.get("mode") == "parole":
            v = d.get("voix")
            return f"parole, {v} voix" if v and v > 1 else "parole"
        chanteurs = [c for c in d.get("chanteurs") or [] if isinstance(c, dict)]
        if chanteurs:   # une voix par chanteur : chaque couple chanteur → voix cible et son décalage
            couples = ", ".join(f"{c.get('nom') or c.get('classe') or 'voix'} → {c.get('voix')} "
                                f"{formats.demi_tons(c.get('decalage') or 0)} dt" for c in chanteurs)
            return ("duo : " if d.get("voix") == 2 else "voix seule : ") + couples
        par_genre = d.get("decalage_par_genre") or d.get("decalage_par_classe")
        if d.get("voix_seule"):
            dec = (par_genre or {}).get(d["voix_seule"])
            return f"voix seule ({d['voix_seule']})" + (f", {formats.demi_tons(dec)} dt"
                                                           if isinstance(dec, (int, float)) else "")
        if par_genre:
            return "duo : " + ", ".join(f"{k} {formats.demi_tons(v)} dt" for k, v in par_genre.items()
                                        if isinstance(v, (int, float)))
        if "decalage_global" in d:
            return f"décalage global {formats.demi_tons(d['decalage_global'])} dt"
        return ""

    def resume_voix_cibles(self):
        """Une voix par chanteur : [phrases] (la décision, les voix entendues, les passages en duo), sinon []."""
        r = self.rapport or {}
        vc = r.get("voix_cibles")
        if not isinstance(vc, dict):
            return []
        if vc.get("message"):
            return [str(vc["message"])]
        d = r.get("decision_hauteur") or {}
        lignes = []
        if d.get("message"):
            texte = str(d["message"])
            if d.get("voix") != 2 and d.get("pourquoi_une_voix"):
                texte += f" ({d['pourquoi_une_voix']})"
            lignes.append(texte)
        aff = r.get("affectation") or {}
        sec = aff.get("secondes_par_voix") or {}
        if sec:
            parts = [f"{v} {formats.duree(s)}" for v, s in sec.items() if v != "ensemble" and s]
            if sec.get("ensemble"):
                parts.append(f"ensemble {formats.duree(sec['ensemble'])}")
            lignes.append("voix entendues : " + ", ".join(parts))
        if aff.get("morceaux_en_duo"):
            lignes.append(f"{formats.pluriel(aff['morceaux_en_duo'], 'passage')} où voix principale et chœurs chantent "
                          "chacun dans sa voix")
        if aff.get("erreur"):
            lignes.append(f"reconnaissance des pistes impossible ({aff['erreur']}) : chaque piste a la voix du passage")
        return lignes

    def resume_autotune(self):
        """Version auto-tune : (« fa majeur (détectée) », « écart médian aux notes 32 → 4 cents ; 85 % à 10 cents »),
        ou None."""
        at = self.autotune
        if not at:
            return None
        g = at.get("gamme") or {}
        if g.get("type") == "tonalite":
            demande = str(g.get("demandee") or "auto").strip().lower()
            ton = str(g.get("tonalite") or "") + (" (détectée)" if demande in ("auto", "automatique", "detectee")
                                                  else " (imposée)")
        else:
            ton = "chromatique (les 12 notes)"
        mes = at.get("mesures") or {}
        av, ap = mes.get("avant") or {}, mes.get("apres") or {}
        morceaux = []
        if av.get("ecart_median_cents") is not None and ap.get("ecart_median_cents") is not None:
            morceaux.append(f"écart médian aux notes {formats.nombre(av['ecart_median_cents'], 0)} → "
                            f"{formats.nombre(ap['ecart_median_cents'], 0)} cents")
        if ap.get("part_a_10_cents") is not None:
            avant = (f"{formats.nombre(100 * av['part_a_10_cents'], 0)} % → "
                     if av.get("part_a_10_cents") is not None else "")
            morceaux.append(f"notes à 10 cents près : {avant}{formats.nombre(100 * ap['part_a_10_cents'], 0)} %")
        return ton, " ; ".join(morceaux)


def passages_voix(rapport):
    """Une voix par chanteur : [{debut, fin, voix: [noms]}] des passages où chaque voix chante (« voix » à deux noms :
    les deux ensemble), d'après l'affectation du rapport ; [] sinon."""
    aff = (rapport or {}).get("affectation") if isinstance(rapport, dict) else None
    sortie = []
    for p in (aff or {}).get("passages") or []:
        try:
            sortie.append({"debut": float(p["debut_s"]), "fin": float(p["fin_s"]),
                           "voix": [str(v) for v in p.get("voix") or []]})
        except (KeyError, TypeError, ValueError):
            continue
    return sortie


def _voix_des_medias(dossier):
    """{nom affiché → {'video': Path, 'mp3': Path, 'video_at': Path, 'mp3_at': Path}} d'après « <titre> - voix
    <Nom>.<ext> » (et sa version « (auto-tune) »)."""
    medias = {}
    for f in dossier.iterdir():
        if not f.is_file() or f.name.startswith("comparaison_"):
            continue
        ext = f.suffix.lower()
        if ext not in EXT_VIDEO and ext != ".mp3":
            continue
        if EXPORT_DANS_NOM.search(f.stem):
            continue    # beat ou voix seule enregistrés par l'interface (« … - voix <Nom> (beat).mp3 »)
        stem, suffixe = f.stem, ""
        if AUTOTUNE_DANS_NOM.search(stem):
            stem, suffixe = AUTOTUNE_DANS_NOM.sub("", stem), "_at"   # la version auto-tune de la même voix
        m = VOIX_DANS_NOM.search(stem)
        nom = m.group(1) if m else ""
        medias.setdefault(nom, {})[("mp3" if ext == ".mp3" else "video") + suffixe] = f
    return medias


def nom_affiche_voix(ident, noms_voix=None):
    """« Jules », ou « Jules et Marie » pour un dossier « jules+marie » (comme doubler)."""
    noms_voix = noms_voix or {}
    if not ident:
        return ""
    return " et ".join(noms_voix.get(m) or m.capitalize() for m in moteur.membres_voix(ident))


COMPARAISON_PAS = re.compile(r"^comparaison_(\d+)pas")


def _pas_des_comparaisons(rapport):
    """Pas de diffusion des extraits de comparaison annoncés par un rapport (« comparaison_pas »), ou None."""
    comp = (rapport or {}).get("comparaison_pas")
    if not isinstance(comp, dict) or not isinstance(comp.get("pas"), list):
        return None
    return {str(int(p)) for p in comp["pas"]
            if isinstance(p, (int, float)) and not isinstance(p, bool) and float(p).is_integer()}


def _proprietaire_sans_suffixe(sans_suffixe, voix, voix_defaut=None):
    """Voix des extraits de comparaison sans « (voix <Nom>) » quand un dossier a plusieurs voix, ou None.

    doubler ne met pas ce suffixe aux comparaisons de sa voix par défaut, et le nom du fichier ne dit pas laquelle
    c'était : elle se lit dans les rapports. voix : [(identifiant, rapport, a_des_comparaisons_suffixees)]. Candidates :
    les voix sans comparaison suffixée ; parmi elles, celles dont le rapport annonce ces pas (« comparaison_pas ») ;
    s'il en reste plusieurs, voix_defaut (celle de doubler) si elle en est, sinon aucune (plutôt qu'une mauvaise)."""
    candidates = [(ident, rapport) for ident, rapport, suffixees in voix if not suffixees]
    pas = {m.group(1) for m in (COMPARAISON_PAS.match(c.stem) for c in sans_suffixe) if m}
    annoncees = [(i, r) for i, r in candidates if _pas_des_comparaisons(r) is not None]
    if annoncees:
        candidates = [(i, r) for i, r in annoncees if pas <= _pas_des_comparaisons(r)] or annoncees
    ids = [i for i, _r in candidates]
    if len(ids) == 1:
        return ids[0]
    return voix_defaut if voix_defaut in ids else None


def lire_dossier(dossier, noms_voix=None, groupe="", voix_defaut=None):
    """Les résultats d'un dossier de sortie (un par voix). noms_voix : {identifiant → nom affiché} ; voix_defaut :
    la voix que doubler prend sans --voix (pour les comparaisons sans « (voix <Nom>) » d'un dossier à plusieurs voix)."""
    dossier = Path(dossier)
    noms_voix = noms_voix or {}
    travail = dossier / "travail"
    source = _json(travail / "source.json")
    try:
        voix_dossiers = sorted(p for p in travail.iterdir() if p.is_dir())
    except OSError:
        voix_dossiers = []
    medias = _voix_des_medias(dossier)
    comparaisons = sorted(dossier.glob("comparaison_*.mp3"))
    sans_suffixe = [c for c in comparaisons if " (voix " not in c.stem]
    resultats = []
    if not voix_dossiers:
        r = Resultat(dossier=dossier, voix=None, source=source)
        un = next(iter(medias.values()), {}) if len(medias) == 1 else {}
        r.video, r.mp3 = un.get("video"), un.get("mp3")
        resultats.append(r)
    for vd in voix_dossiers:
        ident = vd.name
        rapport = _json(vd / "rapport.json")
        nom = noms_voix.get(ident) or nom_affiche_voix(ident, noms_voix)
        m = medias.get(nom)
        if m is None and len(voix_dossiers) == 1 and len(medias) == 1:
            nom, m = next(iter(medias.items()))
        m = m or {}
        # doubler ajoute « (voix <Nom>) » aux comparaisons de toute voix autre que sa voix par défaut ; celles sans
        # suffixe sont attribuées après la boucle
        comp = (comparaisons if len(voix_dossiers) <= 1 else
                [c for c in comparaisons if c.stem.endswith(f" (voix {nom})")])
        resultats.append(Resultat(dossier=dossier, voix=ident, rapport=rapport, source=source, video=m.get("video"),
                                  mp3=m.get("mp3"), voix_nom=nom, comparaisons=comp, video_autotune=m.get("video_at"),
                                  mp3_autotune=m.get("mp3_at")))
    if len(voix_dossiers) > 1 and sans_suffixe:
        par_voix = {r.voix: r for r in resultats if r.voix}
        ident = _proprietaire_sans_suffixe(sans_suffixe, [(r.voix, r.rapport, bool(r.comparaisons))
                                                          for r in par_voix.values()], voix_defaut)
        if ident:
            par_voix[ident].comparaisons = sans_suffixe
    for r in resultats:
        r._groupe = groupe
        if not r.voix_nom:
            r.voix_nom = noms_voix.get(r.voix or "", r.voix or "")
    return resultats


def lister_resultats(sorties, noms_voix=None, profondeur=2, voix_defaut=None):
    """Tous les résultats sous sorties/ (y compris rangés dans un sous-dossier), du plus récent au plus ancien.
    noms_voix et voix_defaut : comme pour lire_dossier."""
    sorties = Path(sorties)
    resultats = []

    def parcourir(d, niveau, groupe):
        try:
            enfants = sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            return
        for e in enfants:
            if (e / "travail").is_dir():
                resultats.extend(lire_dossier(e, noms_voix, groupe, voix_defaut))
            elif niveau < profondeur:
                parcourir(e, niveau + 1, f"{groupe}/{e.name}".strip("/"))
    parcourir(sorties, 1, "")
    resultats.sort(key=lambda r: r.date, reverse=True)
    return resultats


# --- Frise d'octave --------------------------------------------------------------------------------------------------

def classe_voix(m):
    """homme, femme, locuteur (parole : voix n° k), inconnu ou muet."""
    if m.get("muet"):
        return "muet"
    v = m.get("voix")
    if isinstance(v, str):
        if v.startswith("homme"):
            return "homme"
        if v.startswith("femme"):
            return "femme"
    if isinstance(v, int) and not isinstance(v, bool):
        return "locuteur"
    return "inconnu"


def libelle_voix(m):
    v = m.get("voix")
    if m.get("muet"):
        return "muet"
    if isinstance(v, int) and not isinstance(v, bool):
        return f"voix {v}"
    if v in (None, "?"):
        return "inconnue"
    return str(v)


def segments_frise(rapport):
    """[{debut, fin, classe, voix, decalage, f0, base}] d'après « morceaux » (ou, à défaut, les lignes de « frise »)."""
    rapport = rapport or {}
    segs = []
    # voix seule de genre connu (décision globale) : les morceaux sans voix propre en prennent le genre
    seule = (rapport.get("decision_hauteur") or {}).get("voix_seule")
    for m in rapport.get("morceaux") or []:
        try:
            debut, fin = float(m["debut_s"]), float(m["fin_s"])
        except (KeyError, TypeError, ValueError):
            continue
        classe, voix = classe_voix(m), libelle_voix(m)
        if classe == "inconnu" and m.get("voix") is None and seule in ("homme", "femme"):
            classe, voix = seule, seule
        elif classe == "inconnu" and m.get("voix") is None and rapport.get("mode") == "parole":
            classe, voix = "locuteur", "voix seule"   # parole sans regroupement de locuteurs : une seule voix
        seg = {"debut": debut, "fin": fin, "classe": classe, "voix": voix,
               "decalage": m.get("decalage"), "f0": m.get("f0_med"), "base": m.get("base") or ""}
        if m.get("voix_cible"):   # une voix par chanteur : la voix du passage, et celle de chaque piste
            seg["voix_cible"] = str(m["voix_cible"])
            vp = m.get("voix_pistes")
            if isinstance(vp, dict):
                seg["voix_pistes"] = {str(k): str(v) for k, v in vp.items()}
        segs.append(seg)
    if segs:
        return segs
    for ligne in rapport.get("frise") or []:
        r = FRISE_LIGNE.match(str(ligne).strip())
        if not r:
            continue
        v = r.group(5)
        classe = "homme" if v and v.startswith("homme") else "femme" if v and v.startswith("femme") else "inconnu"
        segs.append({"debut": int(r.group(1)) * 60 + float(r.group(2)), "fin": int(r.group(3)) * 60 + float(r.group(4)),
                     "classe": classe, "voix": v or "", "decalage": float(r.group(7).replace("−", "-")),
                     "f0": float(r.group(6)), "base": r.group(8)})
    return segs


def fusionner_segments(segs, ecart_max=0.05):
    """Morceaux voisins de même voix et même décalage fusionnés (pour placer les étiquettes)."""
    sortie = []
    for s in segs:
        if sortie:
            p = sortie[-1]
            if (p["classe"], p["voix"], p["decalage"]) == (s["classe"], s["voix"], s["decalage"]) \
                    and abs(s["debut"] - p["fin"]) <= ecart_max:
                p["fin"] = s["fin"]
                continue
        sortie.append(dict(s))
    return sortie


def segment_a(segs, t):
    for s in segs:
        if s["debut"] <= t < s["fin"]:
            return s
    return None


def duree_frise(rapport, segs=None):
    r = rapport or {}
    if r.get("duree_s"):
        return float(r["duree_s"])
    segs = segs if segs is not None else segments_frise(r)
    return max((s["fin"] for s in segs), default=0.0)


# --- Durées par étape ------------------------------------------------------------------------------------------------

def libelle_conversion(rapport):
    """« Conversion YingMusic-SVC » ou « Conversion Seed-VC », d'après le moteur réellement utilisé."""
    r = rapport or {}
    v = (r.get("variantes") or {}).get("voix_convertie.flac") or {}
    modele = r.get("modele") or v.get("modele")
    moteur = r.get("moteur") or v.get("moteur") or ("yingmusic" if modele == "yingmusic" else "seedvc")
    return f"Conversion {NOMS_MOTEURS.get(moteur, moteur)}"


def cartes_gpu(rapport):
    """Cartes GPU utilisées par la session (temps_local.gpus de doubler : 1 ou 2) ; 1 pour un ancien rapport."""
    tl = (rapport or {}).get("temps_local") or {}
    try:
        return max(1, int(tl.get("gpus") or 1))
    except (TypeError, ValueError):
        return 1


GPU_ADDITIONNE = "temps de GPU des 2 cartes additionnés"


def durees_etapes(rapport):
    """[(libellé, secondes, [(sous-libellé, secondes)])] d'après temps_local, session_kaggle et temps.
    Deux cartes (temps_local.gpus == 2) : le nettoyage et la conversion YingMusic-SVC de la vidéo (temps.nettoyage_s,
    temps.conversion_s) additionnent le temps de GPU des deux cartes, et le disent ; les durées réelles sont celles de
    la session (« toute la session »), à côté de leur temps de GPU (nettoyage_gpu_s, yingmusic_gpu_s)."""
    r = rapport or {}
    tl = r.get("temps_local") or {}
    sk = tl.get("session_kaggle") or {}
    tv = r.get("temps") or {}
    lignes = []
    deux = cartes_gpu(r) >= 2
    additionne = f" ({GPU_ADDITIONNE})" if deux else ""
    conversion = libelle_conversion(r)
    ying = conversion.endswith("YingMusic-SVC")

    def ajouter(libelle, s, details=()):
        if s is not None:
            lignes.append((libelle, float(s), [(a, float(b)) for a, b in details if b is not None]))

    ajouter("Extraction du son", tl.get("extraction_s"))
    ajouter("Envoi sur Kaggle", tl.get("envoi_jeu_s"))
    ajouter("Démarrage du noyau", tl.get("attente_demarrage_noyau_s"))
    gpu = [("Installation des outils", sk.get("installation_s")),
           ("Chargement des modèles", sk.get("chargement_modeles_s")),
           ("Chargement du nettoyage", sk.get("chargement_nettoyage_s")),
           ("Chargement de YingMusic-SVC", sk.get("chargement_yingmusic_s")),
           ("Séparation (toute la session)", sk.get("separation_totale_s")),
           ("Nettoyage (toute la session)", sk.get("nettoyage_totale_s")),
           (f"Nettoyage, {GPU_ADDITIONNE}", sk.get("nettoyage_gpu_s") if deux else None),
           ("YingMusic-SVC (toute la session)", sk.get("yingmusic_total_s")),
           (f"YingMusic-SVC, {GPU_ADDITIONNE}", sk.get("yingmusic_gpu_s") if deux else None),
           ("Séparation de cette vidéo", tv.get("separation_s")),
           ("Analyse (mode, hauteur)", tv.get("analyse_s")),
           ("Nettoyage de cette vidéo" + additionne, tv.get("nettoyage_s")),
           ("Voix de chaque piste (une voix par chanteur)", tv.get("affectation_s")),
           (conversion + (additionne if ying else ""), tv.get("conversion_s")),
           ("Courbes de mélodie", tv.get("courbes_s")),
           ("Noyau en tout", sk.get("total_s")),
           ("Estimé par doubler", tl.get("estimation_noyau_s"))]
    cartes = " (2 cartes T4)" if deux else ""
    ajouter(f"GPU Kaggle{cartes}, depuis l’envoi", tl.get("noyau_depuis_envoi_s"), gpu)
    if tl.get("noyau_depuis_envoi_s") is None and tv:
        ajouter(f"GPU Kaggle{cartes} (cette vidéo)", tv.get("total_video_s"), gpu)
    ajouter("Rapatriement", tl.get("rapatriement_s"))
    ajouter("Décodage du retour (FLAC)", tl.get("decodage_retour_s"))
    ajouter("Reste (original − voix)", tl.get("reste_local_s"))
    at = r.get("autotune") if isinstance(r.get("autotune"), dict) else {}
    ajouter("Mixage", tl.get("mix_local_s"),
            [("dont la version auto-tune", at.get("temps_s"))] if at.get("applique") else ())
    ajouter("Remise sur la vidéo", tl.get("remise_video_s"))
    return lignes


def attente_gpu_probable(rapport):
    """Temps passé à attendre une place GPU ou le démarrage (noyau depuis l'envoi − durée du noyau), s'il est notable."""
    tl = (rapport or {}).get("temps_local") or {}
    sk = tl.get("session_kaggle") or {}
    if tl.get("noyau_depuis_envoi_s") is None or sk.get("total_s") is None:
        return None
    x = float(tl["noyau_depuis_envoi_s"]) - float(sk["total_s"])
    return x if x > 300 else None


def notes_durees(rapport):
    """Phrases sous le titre des durées : attente d'une place GPU, session de plusieurs vidéos, deux cartes."""
    r = rapport or {}
    tl = r.get("temps_local") or {}
    notes = []
    attente = attente_gpu_probable(r)
    if attente:
        notes.append(f"Dont environ {formats.duree(attente)} d’attente d’une place GPU ou de démarrage.")
    if (tl.get("videos_dans_la_session") or 1) > 1:
        notes.append(f"Session Kaggle partagée par {tl['videos_dans_la_session']} vidéos.")
    if cartes_gpu(r) >= 2:
        notes.append("2 cartes T4 : le nettoyage et la conversion YingMusic-SVC se partagent entre les deux cartes. "
                     "Leurs durées pour cette vidéo additionnent le temps de GPU des deux cartes ; les durées réelles "
                     "sont celles de toute la session.")
    return notes


# --- Estimation des durées -------------------------------------------------------------------------------------------

# durée ≈ a + b × secondes d'audio (b par vidéo pour extraction, mix, video ; sur le total pour la session)
MODELE_DEFAUT = {
    "extraction": (0.5, 0.003),
    "envoi": (35.0, 0.15),
    "attente": (0.0, 0.0),
    "kaggle": (140.0, 2.6),
    # chant nettoyé (voix principale et chœurs séparés, reverb retirée, YingMusic-SVC) : environ deux fois plus de GPU
    # par seconde de son, et deux Roformer et YingMusic à charger en plus
    "kaggle_nettoye": (230.0, 5.2),
    # le même sur les deux cartes T4 (doubler --gpu auto ou 2) : 4,1 s par seconde de son, mesuré le 05/11/2026 sur une
    # chanson de 3 min 48 (session de 19,4 min, contre 26,6 sur une carte, soit 6,0 s par seconde de son)
    "kaggle_nettoye_2cartes": (230.0, 4.1),
    "rapatriement": (10.0, 0.07),
    "mix": (1.0, 0.05),
    "video": (1.0, 0.03),
}


# Sans session passée à deux cartes (voir gpu_reel_deux_cartes) : part du temps de GPU (deux cartes additionnées) que le
# nettoyage et YingMusic-SVC prennent en temps réel, chargements à part ; les mêmes que doubler (FACTEURS_DEUX_CARTES)
FACTEURS_DEUX_CARTES = {"nettoyage": 0.70, "conversion": 0.55}


def _sans_longue_attente(k, sk):
    """Durée de la session depuis l'envoi ; une longue attente d'une place GPU ne dit rien de la vitesse : la durée du
    noyau + 40 s la remplace alors."""
    if k is not None and sk.get("total_s") is not None and float(k) - float(sk["total_s"]) > 300:
        return float(sk["total_s"]) + 40
    return k


def gpu_reel_deux_cartes(rapport):
    """GPU réel d'une vidéo doublée sur deux cartes, d'après temps.total_video_s, ou None. Ce total additionne le temps
    de GPU des deux cartes pour le nettoyage et YingMusic-SVC : il est ramené au temps réel avec la part que la session
    a mesurée (nettoyage_totale_s, chargement à part, sur nettoyage_gpu_s ; de même pour YingMusic-SVC), sinon avec
    celle de doubler (FACTEURS_DEUX_CARTES), comme son estimation. Pour les sessions de plusieurs vidéos : pour une
    vidéo seule, la durée réelle de la session (noyau_depuis_envoi_s) dit mieux."""
    r = rapport or {}
    tv = r.get("temps") or {}
    sk = (r.get("temps_local") or {}).get("session_kaggle") or {}
    if not tv.get("total_video_s"):
        return None

    def part(reel, chargement, gpu, defaut):
        try:
            f = (float(sk[reel]) - float(sk.get(chargement) or 0)) / float(sk[gpu])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return defaut
        return min(1.0, max(0.4, f))
    fn = part("nettoyage_totale_s", "chargement_nettoyage_s", "nettoyage_gpu_s", FACTEURS_DEUX_CARTES["nettoyage"])
    fc = part("yingmusic_total_s", "chargement_yingmusic_s", "yingmusic_gpu_s", FACTEURS_DEUX_CARTES["conversion"])
    ying = libelle_conversion(r).endswith("YingMusic-SVC")
    reel = (float(tv["total_video_s"]) - (1 - fn) * float(tv.get("nettoyage_s") or 0)
            - ((1 - fc) * float(tv.get("conversion_s") or 0) if ying else 0.0))
    return max(0.0, reel)


def modele_durees(resultats):
    """Ajuste les pentes b du modèle sur les doublages passés (médiane), les constantes a restant fixes.
    Chant nettoyé sur une carte (rapports sans temps_local.gpus, ou gpus à 1) : le GPU de la vidéo, temps.total_video_s,
    comme avant. Sur deux cartes (kaggle_nettoye_2cartes), ce total additionne le temps des deux cartes : on prend la
    durée réelle de la session pour une vidéo seule (noyau_depuis_envoi_s), sinon ce total ramené au temps réel
    (gpu_reel_deux_cartes)."""
    obs = {k: [] for k in MODELE_DEFAUT}
    for res in resultats:
        r = res.rapport or {}
        tl = r.get("temps_local") or {}
        d = r.get("duree_s")
        if not d or not tl:
            continue
        d = float(d)
        nettoye = bool((r.get("nettoyage") or {}).get("applique"))
        seule = tl.get("videos_dans_la_session") in (1, None)
        sk = tl.get("session_kaggle") or {}
        if nettoye and d > 5 and cartes_gpu(r) >= 2:
            k = _sans_longue_attente(tl.get("noyau_depuis_envoi_s"), sk) if seule else None
            reel = None if k is not None else gpu_reel_deux_cartes(r)
            if k is not None:
                a = MODELE_DEFAUT["kaggle_nettoye_2cartes"][0]
                obs["kaggle_nettoye_2cartes"].append(max(0.0, (float(k) - a) / d))
            elif reel:
                obs["kaggle_nettoye_2cartes"].append(reel / d)
        elif nettoye:
            # GPU de cette vidéo seule (valable aussi dans un lot de plusieurs vidéos)
            tv = (r.get("temps") or {}).get("total_video_s")
            if tv and d > 5:
                obs["kaggle_nettoye"].append(max(0.0, float(tv) / d))

        def obs_(cle, y):
            if y is not None and d > 1:
                a = MODELE_DEFAUT[cle][0]
                obs[cle].append(max(0.0, (float(y) - a) / d))
        obs_("extraction", tl.get("extraction_s"))
        obs_("mix", tl.get("mix_local_s"))
        obs_("video", tl.get("remise_video_s"))
        if seule:
            obs_("envoi", tl.get("envoi_jeu_s"))
            obs_("rapatriement", tl.get("rapatriement_s"))
            obs_("kaggle", _sans_longue_attente(tl.get("noyau_depuis_envoi_s"), sk) if not nettoye else None)
    modele = {}
    for cle, (a, b) in MODELE_DEFAUT.items():
        modele[cle] = (a, statistics.median(obs[cle]) if obs[cle] else b)
    return modele


def facteur_pas(pas):
    """50 pas ≈ 40 % de GPU en plus que 30 (README) : pente proportionnelle, partie fixe comprise."""
    return max(0.3, 1 + 0.4 * ((float(pas or 30) - 30) / 20))


def estimer(durees_audio, pas=30, modele=None, nettoyage=False, mode="auto", moteur=None, cartes=1):
    """{étape: secondes} pour un appel doubler avec ces vidéos (durées d'audio en secondes). nettoyage : chant nettoyé
    (le défaut du doubler actuel ; en mode auto, on suppose du chant, comme doubler) ; les pas ne changent la durée
    qu'avec Seed-VC (avec YingMusic, 50 ou 100 pas n'apportent rien de mesurable). cartes : cartes GPU que le noyau
    prendra (moteur.cartes_prevues) ; à deux, le chant nettoyé suit les sessions passées à deux cartes."""
    modele = {**MODELE_DEFAUT, **(modele or {})}
    durees = [float(d or 0) for d in durees_audio] or [0.0]
    total = sum(durees)
    e = {}
    for cle in ("extraction", "mix", "video"):
        a, b = modele[cle]
        e[cle] = sum(a + b * d for d in durees)
    for cle in ("envoi", "rapatriement"):
        a, b = modele[cle]
        e[cle] = a + b * total
    nettoye = bool(nettoyage) and mode != "parole"
    a, b = modele[("kaggle_nettoye_2cartes" if (cartes or 1) >= 2 else "kaggle_nettoye") if nettoye else "kaggle"]
    ying = (moteur or ("yingmusic" if nettoye else "seedvc")) == "yingmusic"
    e["kaggle"] = a + b * total * (1.0 if ying else facteur_pas(pas))
    e["attente"] = 0.0
    e["fini"] = 0.0
    return {k: round(v, 1) for k, v in e.items()}
