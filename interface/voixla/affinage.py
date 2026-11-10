"""Affinage de la voix sur son chant, côté interface (sans GTK, testable).

La commande qui fait le travail est « affiner » (affiner.py, noyau Kaggle kaggle/affiner_kaggle.py) ; ce module dit à la
page Voix :
- combien de chant exploitable une voix a déjà (les prises de voix/<nom>/chant/, importées par la séance d'enregistrement ou
  enregistrées dans l'interface), et si c'est assez pour proposer le bouton (au moins 2 min, 5 à 10 min conseillées) ;
- si la voix est « affinée » (voix/<nom>/affinage/affinage.json avec son point de contrôle), ou a un affinage inachevé ;
- si le bouton est activé : la clé « affinage_pret » de config.json (faux tant que l'essai réel n'est pas fait) ;
- la durée de GPU à prévoir.
Les seuils et l'estimation sont ceux de affiner.py (il n'est pas importé ici : c'est un script, installé à part).
"""

import json
import math
import re
from pathlib import Path

from . import chemins, controle, formats, moteur

UTILE_MIN_S = 120.0        # en dessous : le bouton reste caché
UTILE_CONSEIL_S = 300.0    # 5 à 10 min conseillées
UTILE_MAX_S = 600.0
PRISE_MIN_S = 5.0          # affiner.py refuse les prises plus courtes
PAS_DEFAUT, BUDGET_H_DEFAUT = 1500, 2.0
# Estimation du temps de GPU (celle de affiner.py, à mesurer au premier essai réel) : installation, pas, rendu
INSTALLATION_S, RENDU_S, SECONDES_PAR_PAS = 420.0, 120.0, (0.8, 2.0)
CLE_PRET = "affinage_pret"
MENTION_EN_PREPARATION = "en préparation : l’essai réel n’est pas encore fait"
QUOTA_HEBDO_S = 30 * 3600

# Alertes de controle.controle_chant qui écartent une prise (celles que le noyau refuserait aussi)
_BLOQUANTES = ("Saturation", "Niveau trop faible", "Bruit de fond", "prise trop courte pour")


# --- La voix : fiche, reprise, session ------------------------------------------------------------------------------------

def dossier_affinage(dossier_voix):
    return Path(dossier_voix) / "affinage"


def fiche(dossier_voix):
    """Le affinage.json de la voix si son point de contrôle est là, sinon None (comme affiner.fiche_affinage)."""
    d = dossier_affinage(dossier_voix)
    f = chemins.lire_json(d / "affinage.json", None)
    if not isinstance(f, dict):
        return None
    point = f.get("point")
    return f if isinstance(point, str) and "/" not in point and (d / point).is_file() else None


def est_affinee(dossier_voix):
    return fiche(dossier_voix) is not None


def reprise_possible(dossier_voix):
    """Dernier point de contrôle d'un affinage inachevé (affinage/reprise/DiT_epoch_*_step_*.pth), ou None."""
    liste = []
    for f in (dossier_affinage(dossier_voix) / "reprise").glob("DiT_epoch_*_step_*.pth"):
        m = re.search(r"step_(\d+)", f.name)
        if m:
            liste.append((int(m.group(1)), f))
    return max(liste)[1] if liste else None


def session_en_cours(etat=None):
    """La session d'affinage notée par affiner (affinage-en-cours.json dans le dossier d'état) : elle se finit par
    « affiner --reprendre ». None s'il n'y en a pas."""
    d = Path(etat) if etat else chemins.dossier_etat()
    s = chemins.lire_json(d / "affinage-en-cours.json", None)
    return s if isinstance(s, dict) else None


def libelle_badge(dossier_voix):
    """(texte, style) du badge de la voix ; None si elle n'est pas affinée. « affinée » : le point est complet ;
    « affinage inachevé » : il manque des pas (« affiner --reprendre »)."""
    f = fiche(dossier_voix)
    if f is None:
        return None
    return ("affinée", "succes") if f.get("complet", True) else ("affinage inachevé", "alerte")


def resume_fiche(dossier_voix):
    """Phrase pour le détail d'une voix affinée, ou ''."""
    f = fiche(dossier_voix)
    if f is None:
        return ""
    morceaux = []
    if f.get("date"):
        morceaux.append(formats.date_iso(f["date"]))
    if f.get("pas"):
        morceaux.append(f"{int(f['pas'])} pas")
    if f.get("utile_s"):
        morceaux.append(f"{formats.duree(f['utile_s'])} de chant")
    if f.get("taille_mo"):
        morceaux.append(f"{formats.nombre(f['taille_mo'], 0)} Mo")
    return " · ".join(morceaux)


# --- Le bouton ---------------------------------------------------------------------------------------------------------

def pret(conf):
    """Vrai si l'affinage est activé : clé « affinage_pret » de config.json (faux si absente : l'essai réel n'est pas fait).
    conf : la configuration lue (chemins.lire_config) ou celle que rend moteur.config_effective."""
    conf = conf if isinstance(conf, dict) else {}
    contenu = conf.get("contenu") if isinstance(conf.get("contenu"), dict) else conf
    return moteur.booleen(contenu.get(CLE_PRET), False)


# --- Le chant exploitable ----------------------------------------------------------------------------------------------

def evaluer_prise(fichier, cache=None):
    """Ce que l'affinage tirera d'une prise : {'duree_s', 'utile_s', 'retenue', 'alertes'}. Mêmes mesures que le contrôle
    de l'interface (controle.controle_chant, quelques passes ffmpeg : à appeler hors du fil de l'interface) ; une prise
    saturée, trop faible, bruitée ou de moins de 5 s est écartée. Le résultat est gardé dans `cache` (dict) tant que le
    fichier ne change pas."""
    f = Path(fichier)
    try:
        st = f.stat()
        cle = (str(f), st.st_mtime_ns, st.st_size)
    except OSError:
        return {"duree_s": 0.0, "utile_s": 0.0, "retenue": False, "alertes": ["fichier introuvable"]}
    if cache is not None and cle in cache:
        return cache[cle]
    m = controle.controle_chant(f)
    duree = m.get("duree_s") or 0.0
    alertes = list(m.get("alertes") or [])
    bloquante = duree < PRISE_MIN_S or any(a.startswith(_BLOQUANTES) for a in alertes)
    silence = m.get("silence_pct") or 0.0
    res = {"duree_s": duree, "utile_s": 0.0 if bloquante else round(duree * (1 - silence / 100), 1),
           "retenue": not bloquante, "alertes": alertes}
    if cache is not None:
        cache[cle] = res
    return res


def evaluer(prises, cache=None):
    """Bilan de [(fichier, durée)] : {'utile_s', 'total_s', 'retenues', 'ecartees', 'prises': [évaluation + 'fichier']}."""
    detail = []
    for f, _d in prises:
        e = evaluer_prise(f, cache)
        detail.append({**e, "fichier": str(f)})
    return bilan(detail)


def bilan(detail):
    """Totaux d'une liste d'évaluations de prises (voir evaluer_prise)."""
    retenues = [e for e in detail if e["retenue"]]
    return {"utile_s": round(sum(e["utile_s"] for e in retenues), 1),
            "total_s": round(sum(e["duree_s"] for e in detail), 1),
            "retenues": len(retenues), "ecartees": len(detail) - len(retenues), "prises": detail}


def niveau(utile_s):
    """« aucun » (rien), « insuffisant » (< 2 min : pas de bouton), « possible » (2 à 5 min : ça marchera mais moins bien),
    « bon » (5 à 10 min), « large » (plus de 10 min : le gain est faible)."""
    if not utile_s or utile_s <= 0:
        return "aucun"
    if utile_s < UTILE_MIN_S:
        return "insuffisant"
    if utile_s < UTILE_CONSEIL_S:
        return "possible"
    return "bon" if utile_s <= UTILE_MAX_S else "large"


def peut_proposer(utile_s):
    """Le bouton « Affiner ma voix sur mon chant… » est montré à partir de 2 min de chant exploitable."""
    return (utile_s or 0) >= UTILE_MIN_S


def avis(utile_s):
    """Phrase sur le chant exploitable face à l'objectif (2 min au moins, 5 à 10 min conseillées)."""
    n = niveau(utile_s)
    if n == "aucun":
        return "Aucun chant importé : la séance d’enregistrement les range ici (2 min au moins, 5 à 10 min conseillées)."
    if n == "insuffisant":
        return (f"{formats.duree(utile_s)} de chant exploitable : encore au moins "
                f"{math.ceil((UTILE_MIN_S - utile_s) / 60)} min pour pouvoir affiner.")
    if n == "possible":
        return f"{formats.duree(utile_s)} de chant exploitable : ça marchera, mais 5 à 10 min le feront mieux."
    if n == "bon":
        return f"{formats.duree(utile_s)} de chant exploitable : de quoi bien affiner."
    return f"{formats.duree(utile_s)} de chant exploitable : au-delà de 10 min, le gain est faible."


# --- Durée et quota ----------------------------------------------------------------------------------------------------

def estimer_s(pas=PAS_DEFAUT):
    """Durée probable (basse, haute) de la session Kaggle, en secondes."""
    return tuple(round(INSTALLATION_S + pas * s + RENDU_S) for s in SECONDES_PAR_PAS)


def texte_estimation(pas=PAS_DEFAUT):
    bas, haut = estimer_s(pas)
    return (f"{formats.duree_courte(bas)} à {formats.duree_courte(haut)} de GPU Kaggle ({pas} pas), "
            f"soit {100 * haut / QUOTA_HEBDO_S:.0f} % au plus des 30 h hebdomadaires.")


def estimations_travail(pas=PAS_DEFAUT):
    """Secondes par étape pour le suivi du travail (le temps restant) : l'estimation haute sur « kaggle »."""
    return {"controle": 60.0, "envoi": 90.0, "kaggle": float(estimer_s(pas)[1]), "rapatriement": 60.0}


def lire_pas(texte, defaut=PAS_DEFAUT):
    """Un nombre de pas saisi (100 à 20000, les bornes de affiner), sinon le défaut."""
    try:
        n = int(str(texte).strip())
    except (TypeError, ValueError):
        return defaut
    return n if 100 <= n <= 20000 else defaut


def texte_etat(dossier_voix, utile_s, conf, session=None):
    """Ligne d'état de la voix pour la page : affinée, inachevée, ou ce qu'il manque."""
    badge = libelle_badge(dossier_voix)
    if badge and badge[0] == "affinée":
        return "Voix affinée sur votre chant : " + resume_fiche(dossier_voix)
    if badge:
        return "Affinage inachevé : « Reprendre l’affinage » finit ce qui reste. " + resume_fiche(dossier_voix)
    if session:
        return "Un affinage est parti sur Kaggle : « Reprendre l’affinage » en rapatrie le résultat."
    return avis(utile_s)
