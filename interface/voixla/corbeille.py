"""Mise à la corbeille de doublages (sans GTK, testable) : ce qui part, le texte de la confirmation, et l'envoi un par
un à la corbeille. Une seule logique pour le détail d'un résultat et pour la liste des Résultats (une ligne, ou
plusieurs en mode sélection).

Un dossier de sortie peut contenir plusieurs voix (travail/<voix>/) :
- si toutes ses voix partent (ou s'il n'en a qu'une), c'est le dossier entier qui va à la corbeille, avec travail/ ;
- sinon, seulement les fichiers des voix choisies : vidéo et MP3 doublés, leur version « (auto-tune) » (vidéo, MP3,
  .lrc), beat et voix seule enregistrés par l'interface, vidéos à partager de partage/, travail/<voix>/. Les sons communs de travail/ restent pour
  les autres voix. Une voix par chanteur (« a+b ») est une voix comme une autre : ses fichiers « voix A et B ».
Les caches (copies vidéo des pistes, ma voix seule, miniatures, analyses) ne sont pas touchés : ils sont nommés d'après
les fichiers, ne servent plus, et leur purge les efface le moment venu. Rien n'en dépend ici.
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import export_social, pistes


@dataclass
class Plan:
    """Ce qui part d'un dossier de sortie : le dossier entier, ou les fichiers de certaines voix."""
    dossier: Path
    titre: str
    voix: list = field(default_factory=list)          # noms affichés des voix qui partent
    fichiers: list = field(default_factory=list)      # dans l'ordre d'envoi
    autres_voix: list = field(default_factory=list)   # identifiants des voix qui restent
    cles: set = field(default_factory=set)            # (dossier, voix) des résultats concernés

    @property
    def tout_le_dossier(self):
        return not self.autres_voix


def voix_du_dossier(dossier):
    """Identifiants des voix d'un dossier de sortie (sous-dossiers de travail/)."""
    try:
        return sorted(p.name for p in (Path(dossier) / "travail").iterdir() if p.is_dir())
    except OSError:
        return []


def fichiers_autotune(res):
    """La version auto-tune d'une voix : « <titre> - voix <Nom> (auto-tune) » en vidéo, MP3 et .lrc (doubler)."""
    base = f"{pistes.base_sorties(res)} (auto-tune)"
    vus = [p for p in (getattr(res, "video_autotune", None), getattr(res, "mp3_autotune", None)) if p]
    for ext in (*pistes.CONTENEURS_VIDEO, ".mp3", ".lrc"):
        f = res.dossier / f"{base}{ext}"
        if f.is_file() and f not in vus:
            vus.append(f)
    return vus


def _fichiers_de_la_voix(res):
    return [p for p in (res.video, res.mp3) if p] + fichiers_autotune(res) + pistes.exports_existants(res) + \
        export_social.fichiers_partages(res) + ([res.dossier_voix] if res.dossier_voix else [])


def plans(resultats):
    """Un plan par dossier de sortie, dans l'ordre des résultats donnés ; les voix d'un même dossier sont regroupées
    (toutes choisies : le dossier entier part)."""
    par_dossier = {}
    for r in resultats:
        par_dossier.setdefault(Path(r.dossier), []).append(r)
    sortie = []
    for dossier, rs in par_dossier.items():
        choisies = {r.voix for r in rs if r.voix}
        autres = [v for v in voix_du_dossier(dossier) if v not in choisies]
        p = Plan(dossier=dossier, titre=rs[0].titre_court, voix=[r.voix_nom or r.voix or "" for r in rs],
                 autres_voix=autres, cles={(str(dossier), r.voix) for r in rs})
        if autres:
            vus = set()
            for r in rs:
                for f in _fichiers_de_la_voix(r):
                    if f not in vus:
                        vus.add(f)
                        p.fichiers.append(f)
        else:
            p.fichiers = [dossier]
        sortie.append(p)
    return sortie


def confirmation(liste):
    """(titre, corps, bouton) de la confirmation pour ces plans. Pour un seul doublage, le texte du détail d'un
    résultat ; pour plusieurs, la liste des titres."""
    if len(liste) == 1 and len(liste[0].voix) <= 1:
        p = liste[0]
        if p.tout_le_dossier:
            corps = (f"Le dossier « {p.dossier.name} » va à la corbeille, avec son travail/ (sons extraits, voix "
                     "convertie). On peut le récupérer dans la corbeille.")
        else:
            corps = (f"Le dossier contient aussi les voix {', '.join(p.autres_voix)} : seuls les fichiers de la voix "
                     f"{p.voix[0] if p.voix else ''} vont à la corbeille ({', '.join(f.name for f in p.fichiers)}).")
        return "Mettre ce doublage à la corbeille ?", corps, "Mettre à la corbeille"
    n = sum(max(1, len(p.voix)) for p in liste)
    lignes = []
    for p in liste:
        voix = ", ".join(v for v in p.voix if v)
        texte = f"• {p.titre}" + (f" (voix {voix})" if voix else "")
        if not p.tout_le_dossier:
            texte += f" : fichiers de cette voix seulement, le dossier garde {', '.join(p.autres_voix)}"
        lignes.append(texte)
    corps = "\n".join(lignes) + "\n\nLes dossiers entiers partent avec leur travail/. On peut tout récupérer dans " \
                                "la corbeille."
    return f"Mettre ces {n} doublages à la corbeille ?", corps, "Tout mettre à la corbeille"


def concerne(liste, dossier, voix):
    """Vrai si le résultat (dossier, voix) perd ses fichiers avec ces plans : pour arrêter d'abord sa lecture."""
    for p in liste:
        if str(p.dossier) == str(dossier) and (p.tout_le_dossier or (str(dossier), voix) in p.cles):
            return True
    return False


def occupes(liste, travaux):
    """Les titres des dossiers où un doublage en cours écrit encore (on ne les met pas à la corbeille)."""
    pris = {s for t in travaux if t.actif for s in (t.demande.get("sorties") or [])}
    return [p.titre for p in liste if str(p.dossier) in pris]


def enchainer(liste, jeter, fini, existe=None):
    """Envoie les fichiers des plans à la corbeille, un par un : jeter(chemin, rappel) puis rappel(ok, message).
    S'arrête au premier échec. fini(ok, message, faits) : faits, le nombre de plans entièrement partis. Un fichier
    déjà absent est passé (rien ne bloque)."""
    existe = existe or (lambda f: Path(f).exists() or Path(f).is_symlink())
    file = [(i, f) for i, p in enumerate(liste) for f in p.fichiers]
    faits = [0]

    def suivant(ok=True, message=""):
        if not ok:
            fini(False, message, faits[0])
            return
        while file and not existe(file[0][1]):
            _avancer()
        if not file:
            faits[0] = len(liste)
            fini(True, "", faits[0])
            return
        jeter(file[0][1], lambda ok_, msg="": (_avancer() if ok_ else None, suivant(ok_, msg)))

    def _avancer():
        i, _f = file.pop(0)
        if not any(j == i for j, _ in file):
            faits[0] = i + 1
    suivant()
