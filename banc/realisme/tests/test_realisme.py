"""Tests légers (sans téléchargement de modèle) des outils de `banc/realisme`."""
import json
import os
import shutil
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import depouiller  # noqa: E402
import fantome  # noqa: E402
import juge  # noqa: E402
import tessiture  # noqa: E402
import test_proches  # noqa: E402

SR = 16000


def voix_synthetique(f0=150.0, duree=10.0, sr=SR, graine=0):
    """Une « voix » : harmoniques de f0, enveloppe de syllabes à 4 Hz."""
    rng = np.random.default_rng(graine)
    t = np.arange(int(duree * sr)) / sr
    y = sum(np.sin(2 * np.pi * f0 * h * t) / h for h in range(1, 20))
    env = np.clip(np.sin(2 * np.pi * 2.5 * t + rng.uniform(0, 6)), 0, None) ** 0.5
    return (0.3 * y * env / np.max(np.abs(y))).astype(np.float32)


# ---------------------------------------------------------------- juge

def test_seuil_eer_separes():
    r = juge.seuil_eer([0.8, 0.85, 0.9], [0.1, 0.2, 0.3])
    assert 0.3 < r["seuil"] < 0.8 and r["eer"] == 0.0


def test_seuil_eer_recouvrement():
    r = juge.seuil_eer([0.5, 0.6, 0.7, 0.8], [0.3, 0.4, 0.55, 0.65])
    assert 0.4 < r["seuil"] < 0.7 and 0.0 < r["eer"] < 0.5


def test_rang_et_verdict():
    assert juge.rang_parmi_leurres(0.9, [0.1, 0.2]) == 1
    assert juge.rang_parmi_leurres(0.15, [0.1, 0.2]) == 2
    assert juge.rang_parmi_leurres(0.0, [0.1, 0.2]) == 3
    assert juge.verdict(0.9, 0.5, 0.05) == "oui"
    assert juge.verdict(0.1, 0.5, 0.05) == "non"
    assert juge.verdict(0.52, 0.5, 0.05) == "limite"


def test_calibrer_embeddings_provisoire_et_complete():
    rng = np.random.default_rng(1)
    cible = rng.normal(size=64)
    autre = [rng.normal(size=64) for _ in range(4)]
    pos = np.stack([cible + 0.3 * rng.normal(size=64) for _ in range(12)])
    neg = np.stack([a + 0.3 * rng.normal(size=64) for a in autre for _ in range(4)])
    prov = juge.calibrer_embeddings(None, pos, neg)
    assert prov["provisoire"] and prov["domaine_positifs"] == "parole"
    comp = juge.calibrer_embeddings(pos[:8], pos[8:], neg)
    assert not comp["provisoire"] and comp["eer"] < 0.1
    assert comp["distribution_positifs"]["moyenne"] > comp["distribution_leurres"]["moyenne"]
    assert comp["parole_au_dessus_du_seuil"] == 1.0


def test_decouper_ecarte_silence():
    y = np.concatenate([voix_synthetique(duree=8, graine=1), np.zeros(8 * SR, dtype=np.float32)])
    clips = juge.decouper(y, duree=8.0)
    assert len(clips) == 1 and clips[0][0] == 0.0
    assert juge.decouper(np.zeros(2 * SR, dtype=np.float32)) == []


# ---------------------------------------------------------------- tessiture

def test_notes():
    assert tessiture.midi_en_note(69) == "A4"
    assert tessiture.midi_en_note(tessiture.hz_en_midi(110.0)) == "A2"
    assert tessiture.midi_en_note(60.2) == "C4+20c"


def test_tessiture_sinus():
    sr = SR
    t = np.arange(3 * sr) / sr
    y = (0.5 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    f0 = tessiture.hauteurs_pyin(y, sr)
    r = tessiture.tessiture_depuis_f0(f0)
    assert abs(r["centiles"]["50"]["hz"] - 220.0) < 5.0
    assert r["centiles"]["50"]["note"].startswith("A3")


def _tess(p5, p10, p50, p90, p95):
    f = [midi for midi in (p5, p10, p50, p90, p95)]
    d = {str(c): {"midi": m, "hz": tessiture.midi_en_hz(m), "note": tessiture.midi_en_note(m)}
         for c, m in zip(tessiture.CENTILES, f)}
    return {"centiles": d, "zone_confort": {"bas_midi": p10, "haut_midi": p90, "bas_note": "x", "haut_note": "y"}}


def test_transposition_entiere_et_marge():
    cible = _tess(40, 43, 50, 57, 60)       # zone de confort 43..57
    chanson = _tess(50, 52, 57, 64, 66)     # centiles 10 à 90 : 52..64, zone : 43..57
    plan = tessiture.proposer_transposition(cible, chanson)
    assert isinstance(plan["demitons"], int)
    assert plan["demitons"] == -8 and plan["marge_demitons"] == 1.0
    # une chanson déjà dans la zone n'est pas transposée
    dedans = _tess(46, 47, 50, 54, 55)
    assert tessiture.proposer_transposition(cible, dedans)["demitons"] == 0


# ---------------------------------------------------------------- fantôme

def test_energie_fantome_detecte():
    voix = voix_synthetique(f0=300, duree=6, graine=2)
    rng = np.random.default_rng(5)
    bruit = (0.02 * rng.normal(size=len(voix))).astype(np.float32)
    ev = fantome.enveloppe_bande_db(voix)
    # musique avec fuite : le bruit est modulé par les syllabes de la voix
    fuite = bruit + 0.3 * voix
    sans = (0.05 * rng.normal(size=len(voix))).astype(np.float32)
    r_fuite = fantome.z_energie(ev, fantome.enveloppe_bande_db(fuite), np.random.default_rng(0))
    r_sans = fantome.z_energie(ev, fantome.enveloppe_bande_db(sans), np.random.default_rng(0))
    assert r_fuite["z_correlation"] > r_sans["z_correlation"]
    assert r_fuite["exces_db"] > r_sans["exces_db"] + 2


def test_fenetres_chantees():
    y = np.concatenate([voix_synthetique(duree=8), np.zeros(8 * SR, dtype=np.float32)])
    deb = fantome.fenetres_chantees(y, duree=4.0)
    assert deb == [0, 4 * SR]


# ---------------------------------------------------------------- page de test et dépouillement

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent")
def test_generer_page_et_depouiller(tmp_path):
    import soundfile as sf
    man = []
    for i, cond in enumerate(["vrai", "A", "B", "leurre", "origine"]):
        f = tmp_path / f"{cond}.wav"
        sf.write(str(f), voix_synthetique(f0=120 + 30 * i, duree=24, graine=i), SR)
        man.append({"fichier": str(f), "condition": cond, "chanson": "c1", "extraits": 2})
    sortie = tmp_path / "out"
    cle = test_proches.generer(man, sortie, duree=9.0, graine=3)
    assert len(cle["extraits"]) == 10
    html = (sortie / "test" / "index.html").read_text()
    assert "__CLIPS__" not in html and "__SESSION__" not in html
    for cond in ["vrai", "leurre", "origine"]:      # rien de la condition dans la page
        assert cond not in html
    for e in cle["extraits"]:
        assert (sortie / "test" / e["fichier"]).stat().st_size > 1000
    assert (sortie / "cles.json").exists() and not (sortie / "test" / "cles.json").exists()
    # réponses : « oui » à toutes les voix de condition vrai
    reps = {"session": cle["session"], "participant": "p1", "reponses": [
        {"id": e["id"], "reponse": "oui" if e["condition"] == "vrai" else "non", "confiance": 4, "ecoutes": 1}
        for e in cle["extraits"]]}
    res = depouiller.depouiller(cle, [reps])
    assert res["par_condition"]["vrai"]["taux_oui"] == 1.0
    assert res["par_condition"]["leurre"]["taux_oui"] == 0.0
    assert "vrai" in depouiller.afficher(res)
    with pytest.raises(ValueError):
        depouiller.depouiller(cle, [{**reps, "session": "autre"}])


def test_wilson():
    lo, hi = depouiller.wilson(5, 10)
    assert lo < 0.5 < hi
    assert depouiller.wilson(0, 0) == (0.0, 0.0)


def test_tessiture_format_plat_en_hz():
    f0 = np.concatenate([np.full(30, h) for h in (100.0, 110.0, 120.0, 130.0, 140.0, 150.0)])
    t = tessiture.tessiture_depuis_f0(f0)
    for k in ("p05", "p10", "p50", "p90", "p95"):
        assert 99.0 < t[k] < 151.0
    assert t["p05"] <= t["p10"] <= t["p50"] <= t["p90"] <= t["p95"]
    bas, haut = t["confort"]
    assert bas == pytest.approx(t["p10"]) and haut == pytest.approx(t["p90"])
    json.dumps(t)
