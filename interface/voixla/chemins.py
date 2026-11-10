"""Emplacements et réglages : dossier de données, config.json partagé avec doubler, préférences de l'interface.

- ~/.config/voix-la/config.json : partagé avec doubler (clés « donnees », « voix », « moteur », « kaggle.* »).
  L'interface le lit directement ; elle l'écrit par « doubler --config » quand doubler le permet (voir moteur.py).
- ~/.config/voix-la/interface.json : préférences propres à l'interface (voix par défaut, réglages par défaut, micro…).
- ~/.local/state/voix-la/ : file des travaux (journaux, événements), à part des données.
Variables d'environnement (essais) : VOIXLA_DONNEES, VOIXLA_CONFIG (dossier), VOIXLA_ETAT (dossier).
"""

import copy
import json
import os
import tempfile
from pathlib import Path

# Dossier qui contient le paquet voixla, pour relancer python3 -m voixla.* : interface/ du dépôt, ou /usr/lib/voix-la
# une fois le paquet Debian installé
INTERFACE = Path(__file__).resolve().parents[1]
# Racine du code : le dépôt Voix-là (interface/voixla/ → ../..) ou, installé, /usr/lib/voix-la, où doubler.py et kaggle/
# sont à côté de voixla/
DEPOT = INTERFACE if (INTERFACE / "doubler.py").is_file() else INTERFACE.parent
DONNEES_DEFAUT = Path.home() / "Music" / "Voix-là"

# Noms par défaut (ceux de doubler) ; le compte n'a pas de défaut : il est lu dans config.json (dépôt public)
KAGGLE_DEFAUT = {
    "compte": "",
    "jeu": "doublage-voix-entrees",
    "noyau": "doublage-voix-gpu",
    "outils": "doublage-outils",
}

PREFERENCES_DEFAUT = {
    # vide : celle de doubler (clé « voix » de sa configuration), sinon la seule voix ; avec plusieurs voix et aucun
    # défaut, aucune n'est présélectionnée
    "voix_defaut": "",
    # Doubler, « Voix cible » : une voix par chanteur (vrai) ou une voix pour tous (faux), le duo (« genre » : voix
    # d'homme et voix de femme ; « ordre » : chanteur 1 et chanteur 2) et les deux voix choisies, gardés d'une
    # session à l'autre ; une voix qui n'existe plus n'est pas présélectionnée (voir choix_chanteurs)
    "voix_par_chanteur": False,
    "duo_chanteurs": "genre",
    "voix_chanteurs": [],       # [voix d'homme ou chanteur 1, voix de femme ou chanteur 2, …] ; null : pas choisie
    "nombre_voix": 2,           # de 2 à 6 voix (chanteurs ou personnes) ; « duo_chanteurs » : genre, ordre ou personnes
    # Doubler, « Comparer plusieurs voix » : les voix cochées (identifiants), gardées d'une session à l'autre
    "voix_comparees": [],
    "mode": "auto",
    "octave": "auto",
    "pas": 30,
    "voix_db": None,            # None : laissé à doubler (+4 dB en chant, 0 en parole)
    "reverb_db": -13.0,
    "micro": "",                # vide : micro par défaut du système
    # source de la dernière prise : vide, le micro du PC (clé « micro ») ; « telephone:<série adb> », le micro de ce
    # téléphone par scrcpy (s'il revient sous une autre série, le premier téléphone branché)
    "source_enregistrement": "",
    "duree_prise_chant_s": 120,
    "duree_parole_s": 60,
    # {voix: dossier} : prises de chant faites hors de l'interface (chant-*.wav, p. ex. par un script
    # d'enregistrement), comptées en lecture seule dans le total de cette voix
    "prises_externes": {},
    "chaine": "",               # defaut, avant (Seed-VC sans nettoyage) ou mesure (moteur et nettoyage ci-dessous)
    "moteur": "",               # vide : celui de doubler (clé « moteur » de config.json)
    "sans_nettoyage": False,
    # Visualisations du son (page Résultats, et Écoute à l'aveugle)
    "vis_melodie": True,
    "vis_spectre": False,
    "vis_onde": False,
    "vis_vumetre": False,
    "melodie_hz": False,        # échelle de la courbe de mélodie : notes (faux) ou Hz (vrai)
    "melodie_autotune": True,   # courbe de la version auto-tune sur la mélodie (doublages qui en ont une)
    # Frises du détail d'un résultat : frise d'octave, et « Voix de chaque passage » d'un doublage à une voix par
    # chanteur
    "frise_octave": True,
    "frise_voix": True,
    # Paroles sous le lecteur des résultats (paroles.json de doubler, sinon sous-titres de la vidéo doublée)
    "paroles": True,
    "paroles_langue": "deux",   # originale, fr ou deux
    "paroles_taille": "normale",    # petite, normale, grande ou tres-grande (gui/vue_paroles.TAILLES)
    "paroles_panneau": False,   # panneau « Toutes les paroles », à droite du détail
    "paroles_video": False,     # sous-titres sur la vidéo, hors du plein écran
    # Lecteur des résultats : volume (0 à 1) de tous ses flux, et dernière position de chaque doublage, pour la reprise
    # ({"<dossier>|<voix>": {"position": s, "date": horodatage}}, voir voixla.lecture.memoriser_position)
    "volume": 1.0,
    "positions": {},
    # {dossier de résultat: vidéo source} choisie à la main quand celle de travail/source.json est introuvable
    "sources_choisies": {},
    # taille du cache des copies vidéo des pistes séparées, en Mo ; None : 2 Go (voixla.pistes.CACHE_VIDEO_MO)
    "cache_pistes_mo": None,
    # Export pour les réseaux (gui/partage.py) : format, cadrage, fondu, traduction et texte de la mention ; la mention
    # elle-même est allumée à chaque ouverture, la piste revient au doublage
    "partage": {"format": "vertical", "cadrage": "flou", "fondu": True, "traduction": False,
                "texte_mention": "Voix générée par IA"},
    # Bibliothèque des résultats (voixla.bibliotheque) : vue (grille ou liste), tri, ordre inversé, versions regroupées
    "bibliotheque": {"vue": "grille", "tri": "date", "inverse": False, "regrouper": True},
    # Karaoké (gui/page_karaoke.py) : latence du matériel en ms (réglée à la main ou mesurée par les bips), micro
    # (None : celui des Réglages), tours d'une boucle, dernier doublage chanté
    "karaoke": {"latence_ms": 0, "micro": None, "repetitions": 3, "derniere": ""},
    # Quota GPU de Kaggle (voixla/quota.py). Le total et la remise à zéro viennent de « kaggle quota » quand il
    # répond ; sinon ceux-ci : heures par semaine, jour (lundi = 0) et heure UTC de la remise à zéro (samedi 00:00 UTC,
    # vérifié auprès de Kaggle le 06/11/2026)
    "quota_h": 30.0,
    "quota_remise_jour": 5,
    "quota_remise_heure": 0,
    "quota_alerte_h": 2.0,      # alerte quand il reste moins que cela
    # doublage qui ne tient plus dans le reste du quota : « demander » (dialogue, « Attendre » proposé d'abord),
    # « attendre » (mis en attente de la remise à zéro sans demander) ou « lancer » (lancé quand même)
    "quota_depassement": "demander",
    # secours quand le quota ne suffit plus (voixla/secours.py) : vide, aucun ; « ancien-pc », « runpod » : à venir
    "secours": "",
}


def dossier_config():
    if os.environ.get("VOIXLA_CONFIG"):
        return Path(os.environ["VOIXLA_CONFIG"]).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "voix-la"


def fichier_config():
    """config.json partagé : doubler le lit toujours dans ~/.config/voix-la (VOIXLA_CONFIG le déplace pour les essais)."""
    if os.environ.get("VOIXLA_CONFIG"):
        return Path(os.environ["VOIXLA_CONFIG"]).expanduser() / "config.json"
    return Path.home() / ".config" / "voix-la" / "config.json"


def fichier_preferences():
    return dossier_config() / "interface.json"


def dossier_cache():
    """Caches (analyses du son, ma voix seule, copies vidéo des pistes…) : ~/.cache/voix-la (VOIXLA_CACHE pour les
    essais)."""
    if os.environ.get("VOIXLA_CACHE"):
        return Path(os.environ["VOIXLA_CACHE"]).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "voix-la"


def fichier_proteges():
    return dossier_cache() / "proteges.json"


def _vivant(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def proteger(fichiers, pid=None):
    """Fichiers du cache que la purge ne doit pas effacer tant que ce processus vit (l'interface : les copies du
    doublage ouvert ou en lecture). Remplace la liste de ce processus ; celles des processus finis sont oubliées."""
    import fcntl
    verrou = dossier_cache() / "verrous" / "proteges.verrou"
    verrou.parent.mkdir(parents=True, exist_ok=True)
    with open(verrou, "a") as v:
        fcntl.flock(v, fcntl.LOCK_EX)
        lu = lire_json(fichier_proteges(), {}) or {}
        tous = {k: x for k, x in lu.items() if isinstance(x, list) and _vivant(k)} if isinstance(lu, dict) else {}
        liste = sorted({str(f) for f in fichiers if f})
        if liste:
            tous[str(pid or os.getpid())] = liste
        else:
            tous.pop(str(pid or os.getpid()), None)
        ecrire_json(fichier_proteges(), tous)


def proteges():
    """Les fichiers protégés par les processus encore en vie (voir proteger)."""
    lu = lire_json(fichier_proteges(), {}) or {}
    if not isinstance(lu, dict):
        return set()
    return {f for pid, liste in lu.items() if isinstance(liste, list) and _vivant(pid) for f in liste}


def purger_dossier(dossier, max_mo, garder=()):
    """Garde un dossier du cache sous max_mo : efface les fichiers les moins récemment utilisés (et les .tmp
    abandonnés depuis plus d'une heure), jamais ceux de garder (le fichier qu'on vient de faire) ni ceux que l'interface
    protège (copies du doublage ouvert ou en lecture, voir proteger). Rend le nombre de fichiers effacés."""
    import time
    d = Path(dossier)
    if not d.is_dir():
        return 0
    garder = {Path(g).name for g in garder} | {Path(f).name for f in proteges() if Path(f).parent == d}
    fichiers, efface = [], 0
    for f in d.iterdir():
        try:
            st = f.stat()
        except OSError:
            continue
        if not f.is_file():
            continue
        if ".tmp" in f.name and time.time() - st.st_mtime > 3600:
            f.unlink(missing_ok=True)
            efface += 1
            continue
        fichiers.append((max(st.st_atime, st.st_mtime), st.st_size, f))
    total = sum(s for _t, s, _f in fichiers)
    for _t, s, f in sorted(fichiers):
        if total <= max_mo * 1024 * 1024:
            break
        if f.name in garder:
            continue
        f.unlink(missing_ok=True)
        total -= s
        efface += 1
    return efface


def dossier_etat():
    if os.environ.get("VOIXLA_ETAT"):
        return Path(os.environ["VOIXLA_ETAT"]).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "voix-la"


# --- JSON ----------------------------------------------------------------------------------------------------------

def lire_json(chemin, defaut=None):
    """Contenu d'un fichier JSON, ou defaut s'il manque ou est illisible."""
    try:
        return json.loads(Path(chemin).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return copy.deepcopy(defaut)


def ecrire_json(chemin, donnees, indent=1):
    """Écriture atomique (fichier temporaire puis renommage) : un lecteur ne voit jamais un fichier à moitié écrit."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{chemin.name}.", dir=str(chemin.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(donnees, f, ensure_ascii=False, indent=indent)
            f.write("\n")
        os.replace(tmp, chemin)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- config.json (partagé avec doubler) ------------------------------------------------------------------------------

def lire_config():
    c = lire_json(fichier_config(), {})
    return c if isinstance(c, dict) else {}


def valeur(config, cle, defaut=None):
    """Valeur d'une clé pointée (« kaggle.compte »)."""
    v = config
    for morceau in cle.split("."):
        if not isinstance(v, dict) or morceau not in v:
            return defaut
        v = v[morceau]
    return v


def ecrire_cle_config(cle, val):
    """Écrit une clé pointée dans config.json en gardant les autres (repli quand doubler n'a pas --config)."""
    c = lire_config()
    d = c
    morceaux = cle.split(".")
    for m in morceaux[:-1]:
        if not isinstance(d.get(m), dict):
            d[m] = {}
        d = d[m]
    if val in (None, ""):
        d.pop(morceaux[-1], None)
    else:
        d[morceaux[-1]] = val
    ecrire_json(fichier_config(), c, indent=2)


def dossier_donnees(config=None):
    """VOIXLA_DONNEES, sinon la clé « donnees » de config.json, sinon ~/Music/Voix-là (même ordre que doubler)."""
    if os.environ.get("VOIXLA_DONNEES"):
        return Path(os.environ["VOIXLA_DONNEES"]).expanduser()
    c = lire_config() if config is None else config
    if c.get("donnees"):
        return Path(str(c["donnees"])).expanduser()
    return DONNEES_DEFAUT


def kaggle(config=None):
    """Compte et noms Kaggle : config.json, sinon les noms d'origine de doubler."""
    c = lire_config() if config is None else config
    k = dict(KAGGLE_DEFAUT)
    for cle in KAGGLE_DEFAUT:
        v = valeur(c, f"kaggle.{cle}")
        if v:
            k[cle] = str(v)
    return k


def ref_kaggle(compte, nom):
    """« compte/nom » complet (un nom déjà complet est gardé tel quel) ; None sans compte."""
    if "/" in (nom or ""):
        return nom
    return f"{compte}/{nom}" if compte and nom else None


# --- Préférences de l'interface --------------------------------------------------------------------------------------

def lire_preferences():
    p = copy.deepcopy(PREFERENCES_DEFAUT)
    lu = lire_json(fichier_preferences(), {})
    if isinstance(lu, dict):
        p.update({k: v for k, v in lu.items() if k in PREFERENCES_DEFAUT})
    return p


def ecrire_preferences(prefs):
    ecrire_json(fichier_preferences(), {k: prefs.get(k) for k in PREFERENCES_DEFAUT}, indent=2)


def modifier_preference(cle, val):
    return modifier_preferences({cle: val})


def modifier_preferences(valeurs):
    """Écrit plusieurs préférences d'un coup (une seule écriture) ; rend toutes les préférences."""
    p = lire_preferences()
    p.update(valeurs)
    ecrire_preferences(p)
    return p


DUOS_CHANTEURS = ("genre", "ordre", "personnes")
VOIX_MINI, VOIX_MAXI = 2, 6


def nombre_voix(prefs):
    """Nombre de voix gardé dans les préférences (de 2 à 6 ; 2 si la valeur est absente ou abîmée)."""
    n = (prefs if isinstance(prefs, dict) else {}).get("nombre_voix")
    if isinstance(n, bool) or not isinstance(n, int):
        return VOIX_MINI
    return max(VOIX_MINI, min(VOIX_MAXI, n))


def choix_chanteurs(prefs, ids=None):
    """Le choix « une voix par chanteur » gardé dans les préférences : (par_chanteur, duo, [voix 1, voix 2, …]), de 2 à 6
    voix (nombre_voix). Une valeur abîmée prend sa valeur par défaut (une voix pour tous, duo mixte, deux voix non
    choisies). Avec ids (les voix qui existent), une voix disparue devient None : rien n'est présélectionné, comme sans
    mémoire."""
    p = prefs if isinstance(prefs, dict) else {}
    par = p.get("voix_par_chanteur") is True
    duo = p.get("duo_chanteurs") if p.get("duo_chanteurs") in DUOS_CHANTEURS else DUOS_CHANTEURS[0]
    lues = p.get("voix_chanteurs") if isinstance(p.get("voix_chanteurs"), list) else []
    voix = []
    for i in range(nombre_voix(p)):
        v = lues[i] if i < len(lues) else None
        if not isinstance(v, str) or not v or (ids is not None and v not in ids):
            v = None
        voix.append(v)
    return par, duo, voix


# --- Dossiers dérivés du dossier de données ------------------------------------------------------------------------

def dossier_voix(donnees):
    return Path(donnees) / "voix"


def dossier_sorties(donnees):
    return Path(donnees) / "sorties"


def dossier_videos(donnees):
    return Path(donnees) / "Vidéos"


def fichier_verrou_doubler(donnees):
    return dossier_sorties(donnees) / ".kaggle" / "verrou"


def dossier_ecoute():
    if os.environ.get("VOIXLA_ECOUTE"):
        return Path(os.environ["VOIXLA_ECOUTE"]).expanduser()
    return DEPOT / "banc" / "ecoute"


def dossier_images(images_xdg=None):
    """Où vont les captures d'image du lecteur : VOIXLA_IMAGES (essais), sinon <Images>/Voix-là (dossier Images du
    bureau, donné par l'interface, sinon ~/Images ou ~/Pictures)."""
    if os.environ.get("VOIXLA_IMAGES"):
        return Path(os.environ["VOIXLA_IMAGES"]).expanduser()
    if images_xdg and Path(images_xdg).is_dir():
        return Path(images_xdg) / "Voix-là"
    for nom in ("Images", "Pictures"):
        if (Path.home() / nom).is_dir():
            return Path.home() / nom / "Voix-là"
    return Path.home() / "Voix-là"


def nom_capture(titre, piste, position_s):
    """« <titre> - Doublage - 1m23,45s.png » : sans caractère interdit dans un nom de fichier."""
    s = max(0.0, float(position_s or 0.0))
    m, r = divmod(s, 60)
    instant = f"{int(m)}m{r:05.2f}s".replace(".", ",")
    morceaux = [x for x in (titre, piste, instant) if x]
    nom = " - ".join(morceaux)
    for c in '/\\:*?"<>|\0':
        nom = nom.replace(c, "-")
    return nom.strip(" .")[:180] + ".png"


def dossier_telechargements():
    for nom in ("Downloads", "Téléchargements"):
        d = Path.home() / nom
        if d.is_dir():
            return d
    return Path.home()
