"""Mise en forme à la française : durées, nombres, dates, décibels, demi-tons."""

import re
import time

MOINS = "−"   # vrai signe moins


def joindre(noms):
    """« A », « A et B », « A, B et C » (les noms de plusieurs voix, comme doubler)."""
    noms = [str(n) for n in noms]
    if len(noms) <= 1:
        return noms[0] if noms else ""
    return ", ".join(noms[:-1]) + " et " + noms[-1]


def nombre(x, decimales=1):
    """1.5 → « 1,5 » ; -3 → « −3 » ; les zéros inutiles sont retirés."""
    if x is None:
        return "—"
    texte = f"{float(x):.{decimales}f}"
    if "." in texte:
        texte = texte.rstrip("0").rstrip(".")
    if texte in ("-0", ""):
        texte = "0"
    return texte.replace("-", MOINS).replace(".", ",")


def signe(x, decimales=1):
    """+4, −13, +0 (signe toujours affiché)."""
    if x is None:
        return "—"
    t = nombre(x, decimales)
    return t if t.startswith(MOINS) else "+" + t


def db(x, decimales=1):
    return f"{signe(x, decimales)} dB"


def demi_tons(x):
    """Décalage de hauteur : « +0 », « −12 », « +3,5 »."""
    return signe(x, 1)


def duree(s, precis=False):
    """0,4 s · 44 s · 3 min 53 s · 1 h 05 min."""
    if s is None:
        return "—"
    s = float(s)
    if s < 0:
        s = 0.0
    if s < 10 and precis:
        return f"{nombre(s, 1)} s"
    if s < 60:
        return f"{nombre(s, 1 if s < 10 else 0)} s"
    if s < 3600:
        m, r = divmod(int(round(s)), 60)
        return f"{m} min {r:02d} s" if r else f"{m} min"
    h, r = divmod(int(round(s)), 3600)
    return f"{h} h {r // 60:02d} min"


def duree_courte(s):
    """Estimation arrondie : « ≈ 40 s », « ≈ 6 min », « ≈ 1 h 10 min »."""
    if s is None:
        return "—"
    s = max(0.0, float(s))
    if s < 60:
        return f"≈ {max(5, int(round(s / 5.0)) * 5)} s"
    if s < 3600:
        return f"≈ {int(round(s / 60.0))} min"
    h, r = divmod(int(round(s / 60.0)), 60)
    return f"≈ {h} h {r:02d} min"


def chrono(s):
    """Position dans un média : 1:05, 12:30, 1:02:03."""
    if s is None:
        return "—"
    s = max(0, int(s))
    h, r = divmod(s, 3600)
    m, r = divmod(r, 60)
    return f"{h}:{m:02d}:{r:02d}" if h else f"{m}:{r:02d}"


def chrono_precis(s):
    """Position au dixième : 1:05,3."""
    if s is None:
        return "—"
    s = round(max(0.0, float(s)), 1)   # 59,97 s → 1:00,0 (et non 0:60,0)
    m, r = divmod(s, 60)
    return f"{int(m)}:{r:04.1f}".replace(".", ",")


def date(ts, maintenant=None):
    """« aujourd'hui 18:41 », « hier 09:02 », « 03/11/2026 18:41 »."""
    if not ts:
        return "—"
    maintenant = maintenant or time.time()
    lt, ln = time.localtime(ts), time.localtime(maintenant)
    heure = time.strftime("%H:%M", lt)
    if lt.tm_year == ln.tm_year and lt.tm_yday == ln.tm_yday:
        return f"aujourd’hui {heure}"
    hier = time.localtime(maintenant - 86400)
    if lt.tm_year == hier.tm_year and lt.tm_yday == hier.tm_yday:
        return f"hier {heure}"
    return time.strftime("%d/%m/%Y %H:%M", lt)


def date_iso(texte):
    """« 2026-11-03 » → « 03/11/2026 »."""
    if not texte or len(texte) < 10 or texte[4] != "-":
        return texte or "—"
    return f"{texte[8:10]}/{texte[5:7]}/{texte[0:4]}"


def pluriel(n, mot, mot_pluriel=None):
    return f"{n} {mot if n < 2 else (mot_pluriel or mot + 's')}"


def lire_secondes(texte):
    """« 1:05 », « 1:05,5 », « 65 », « 1 min 5 » → secondes ; None si illisible."""
    t = (texte or "").strip().replace(",", ".").replace(MOINS, "-")
    if not t:
        return None
    if re.search(r"[a-z]", t, re.I):
        morceaux = re.findall(r"(\d+(?:\.\d+)?)\s*(h|min|mn|m|s)?", t, re.I)
        if not morceaux or re.sub(r"[\d.\s]|h|min|mn|m|s", "", t, flags=re.I):
            return None
        unites = {"h": 3600, "min": 60, "mn": 60, "m": 60, "s": 1}
        total, precedente = 0.0, None
        for valeur, unite in morceaux:
            unite = unite.lower() if unite else ("s" if precedente in ("min", "mn", "m") else
                                                 "min" if precedente == "h" else "s")
            total += float(valeur) * unites[unite]
            precedente = unite
        return total
    try:
        if ":" in t:
            parties = [float(p) for p in t.split(":")]
            s = 0.0
            for p in parties:
                s = s * 60 + p
            return s if s >= 0 else None
        v = float(t)
        return v if v >= 0 else None
    except ValueError:
        return None
