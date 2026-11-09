"""Essais de l'estimation de durée d'une session Kaggle avant son lancement : celle de doubler lui-même
(moteur.estimation_noyau_s, doubler.py chargé sans être lancé), le repli de l'interface quand doubler.py manque ou ne
répond pas, et les paroles comptées comme doubler (une carte, deux cartes pendant la conversion, --sans-paroles).
Une fois la session lancée : voir test_suivi (EssaiEstimationDeDoublerLancee). Rapports inventés, dans un dossier
temporaire."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voixla import moteur, rapports
from voixla.rapports import Resultat

from .commun import VRAI_DOUBLER

D = 200.0   # une chanson inventée de 3 min 20


def oublier_doubler():
    moteur._doubler.update(cle=None, module=None)


def rapport(d=D, cartes=2, noyau=1000.0, paroles_total=None, pendant=None, copie=True, videos=1, nettoye=True,
            moteur_="yingmusic", ying_total=300.0, ying_gpu=500.0, charg_ying=20.0):
    """Rapport inventé d'une vidéo doublée : session Kaggle (deux cartes : temps de GPU additionnés à part), paroles
    après la conversion (paroles_total) ou pendant (pendant : leur durée sur la carte 1)."""
    sk = {"installation_s": 40.0, "chargement_modeles_s": 35.0, "chargement_nettoyage_s": 30.0,
          "chargement_yingmusic_s": charg_ying, "separation_totale_s": 110.0, "nettoyage_totale_s": 330.0,
          "yingmusic_total_s": ying_total, "total_s": noyau - 30.0}
    if cartes == 2:
        sk.update(nettoyage_gpu_s=430.0, yingmusic_gpu_s=ying_gpu)
    if paroles_total is not None:
        sk["paroles_total_s"] = paroles_total
        if copie:
            sk.update(paroles_copie_s=30.0, paroles_attente_copie_s=0.0)
    if pendant is not None:
        sk["paroles_pendant_conversion_s"] = pendant
    tl = {"extraction_s": 1.0, "envoi_jeu_s": 60.0, "attente_demarrage_noyau_s": 4.0, "noyau_depuis_envoi_s": noyau,
          "rapatriement_s": 30.0, "videos_dans_la_session": videos, "mix_local_s": 10.0, "remise_video_s": 5.0,
          "session_kaggle": sk}
    if cartes:
        tl["gpus"] = cartes
    modele = "yingmusic" if moteur_ == "yingmusic" else "chant44k"
    temps = {"separation_s": 100.0, "analyse_s": 6.0, "nettoyage_s": 430.0 if nettoye else None,
             "conversion_s": ying_gpu, "paroles_s": (paroles_total or 0) * 0.8 or None, "total_video_s": 1100.0}
    return {"mode": "chant", "duree_s": d, "modele": modele, "moteur": moteur_,
            "nettoyage": {"demande": nettoye, "applique": nettoye},
            "variantes": {"voix_convertie.flac": {"modele": modele, "moteur": moteur_, "pas_diffusion": 30,
                                                  "conversion_s": ying_gpu}},
            "temps": {k: v for k, v in temps.items() if v is not None}, "temps_local": tl}


def res(r):
    return Resultat(dossier=Path("/s/x"), voix="jules", rapport=r)


class Dossier(unittest.TestCase):
    def setUp(self):
        super().setUp()
        t = tempfile.TemporaryDirectory(prefix="voixla-estimation-")
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name).resolve()
        self.sorties = self.tmp / "sorties"
        self.sorties.mkdir()
        oublier_doubler()
        self.addCleanup(oublier_doubler)

    def ecrire(self, titre, r, sorties=None, voix="jules"):
        d = (sorties or self.sorties) / titre / "travail" / voix
        d.mkdir(parents=True)
        (d / "rapport.json").write_text(json.dumps(r), encoding="utf-8")

    def historique(self):
        """Trois sessions : une carte (paroles après), deux cartes (paroles après), deux cartes (paroles pendant)."""
        self.ecrire("Essai un", rapport(cartes=1, noyau=1500.0, paroles_total=110.0))
        self.ecrire("Essai deux", rapport(noyau=1050.0, paroles_total=120.0, d=180.0))
        self.ecrire("Essai trois", rapport(noyau=950.0, paroles_total=140.0, pendant=140.0, ying_total=420.0, d=210.0))

    def faux_doubler_py(self, source):
        """Un doubler.py à la place de celui livré avec l'interface."""
        f = self.tmp / "depot" / "doubler.py"
        f.parent.mkdir(exist_ok=True)
        f.write_text(source, encoding="utf-8")
        rustine = mock.patch.object(moteur, "fichier_doubler", return_value=f)
        rustine.start()
        self.addCleanup(rustine.stop)
        return f


# --- L'estimation de doubler lui-même ----------------------------------------------------------------------------------

@unittest.skipUnless(VRAI_DOUBLER.is_file(), "doubler.py absent")
class EssaiEstimationDeDoubler(Dossier):
    def setUp(self):
        super().setUp()
        spec = importlib.util.spec_from_file_location("doubler_estimation", VRAI_DOUBLER)
        self.doubler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.doubler)

    def selon_doubler(self, durees, pas=30, sorties=None, nettoyage=True, mode="auto", moteur_="yingmusic",
                      paroles=True, voix_cibles=1, cartes=1):
        s = self.doubler.estimer_noyau_s(durees, pas, sorties or [self.sorties], nettoyage, mode, moteur_, paroles,
                                         voix_cibles, cartes, "toutes" if voix_cibles > 1 else "non")   # son défaut en duo
        return round(float(s), 1)

    def test_la_session_est_celle_de_doubler(self):
        self.historique()
        cas = [dict(), dict(cartes=2), dict(cartes=2, paroles=False), dict(paroles=False), dict(cartes=2, voix_cibles=2),
               dict(cartes=2, mode="parole"), dict(cartes=2, moteur_="seedvc", pas=50), dict(nettoyage=False, cartes=2),
               dict(cartes=2, durees=[100.0, 80.0]), dict(durees=[12.0], cartes=2)]
        for k in cas:
            with self.subTest(**k):
                durees = k.pop("durees", [228.0])
                attendu = self.selon_doubler(durees, **k)
                e = rapports.estimer(durees, k.get("pas", 30), rapports.modele_durees([]),
                                     nettoyage=k.get("nettoyage", True), mode=k.get("mode", "auto"),
                                     moteur=k.get("moteur_", "yingmusic"), cartes=k.get("cartes", 1),
                                     paroles=k.get("paroles", True), voix_cibles=k.get("voix_cibles", 1),
                                     sorties=self.sorties)
                self.assertEqual(e["kaggle"], attendu)
        # paroles pendant la conversion à deux cartes : elles coûtent moins qu'à une carte, et rien sans elles
        une = self.selon_doubler([228.0]) - self.selon_doubler([228.0], paroles=False)
        deux = self.selon_doubler([228.0], cartes=2) - self.selon_doubler([228.0], cartes=2, paroles=False)
        self.assertGreater(une, deux)
        self.assertGreater(deux, 0)

    def test_sans_historique(self):
        e = rapports.estimer([228.0], 30, None, nettoyage=True, moteur="yingmusic", cartes=2, sorties=self.sorties)
        self.assertEqual(e["kaggle"], self.selon_doubler([228.0], cartes=2))
        self.assertLess(e["kaggle"], rapports.estimer([228.0], 30, None, nettoyage=True, moteur="yingmusic", cartes=1,
                                                      sorties=self.sorties)["kaggle"])

    def test_dossier_ou_liste_de_dossiers(self):
        autre = self.tmp / "autres sorties"
        autre.mkdir()
        self.ecrire("Essai quatre", rapport(noyau=2400.0, paroles_total=300.0), sorties=autre)
        k = dict(nettoyage=True, moteur="yingmusic", cartes=1)
        seul = rapports.estimer([228.0], 30, **k, sorties=self.sorties)["kaggle"]
        self.assertEqual(rapports.estimer([228.0], 30, **k, sorties=str(self.sorties))["kaggle"], seul)
        les_deux = rapports.estimer([228.0], 30, **k, sorties=[self.sorties, autre])["kaggle"]
        self.assertEqual(les_deux, self.selon_doubler([228.0], sorties=[self.sorties, autre]))
        self.assertNotEqual(les_deux, seul)

    def test_facteurs_deux_cartes_comme_doubler(self):
        self.assertEqual(rapports.FACTEURS_DEUX_CARTES, self.doubler.FACTEURS_DEUX_CARTES)
        self.assertEqual(rapports.FACTEURS_DEUX_CARTES, moteur.module_doubler().FACTEURS_DEUX_CARTES)

    def test_repli_sans_historique_proche_de_doubler(self):
        # sans historique, le modèle de l'interface rejoint à peu près les défauts de doubler (paroles comprises)
        for cartes in (1, 2):
            with self.subTest(cartes=cartes):
                repli = rapports.estimer([228.0], 30, None, nettoyage=True, moteur="yingmusic", cartes=cartes)["kaggle"]
                selon = self.selon_doubler([228.0], cartes=cartes)
                self.assertLess(abs(repli - selon) / selon, 0.05, (repli, selon))

    def test_chargement_sans_pyc_ni_module_inscrit(self):
        module = moteur.module_doubler()
        self.assertIsNotNone(module)
        self.assertEqual(Path(module.__file__), VRAI_DOUBLER)
        self.assertIs(moteur.module_doubler(), module)                  # gardé tant que le fichier ne change pas
        self.assertNotIn("voixla_doubler", sys.modules)


# --- Repli de l'interface quand doubler.py ne répond pas ---------------------------------------------------------------

class EssaiRepli(Dossier):
    K = dict(nettoyage=True, moteur="yingmusic", cartes=2)

    def repli(self):
        return rapports.estimer([228.0], 30, None, **self.K)["kaggle"]

    def selon_interface(self):
        return rapports.estimer([228.0], 30, None, **self.K, sorties=self.sorties)["kaggle"]

    def test_doubler_absent(self):
        with mock.patch.object(moteur, "fichier_doubler", return_value=self.tmp / "nulle part" / "doubler.py"):
            self.assertIsNone(moteur.module_doubler())
            self.assertIsNone(moteur.estimation_noyau_s([228.0], 30, [self.sorties]))
            self.assertEqual(self.selon_interface(), self.repli())

    def test_doubler_illisible_ou_qui_s_arrete(self):
        for source in ("def estimer_noyau_s(:\n", "import sys\nsys.exit(3)\n", "raise RuntimeError('cassé')\n"):
            with self.subTest(source=source):
                oublier_doubler()
                self.faux_doubler_py(source)
                self.assertIsNone(moteur.module_doubler())
                self.assertEqual(self.selon_interface(), self.repli())

    def test_doubler_trop_ancien_ou_en_erreur(self):
        sources = [
            # avant les paroles et les deux cartes : paramètres refusés
            "def estimer_noyau_s(durees_s, pas, sorties, nettoyage=True, mode='auto', moteur='yingmusic'):\n"
            "    return 1234\n",
            "def estimer_noyau_s(*a, **k):\n    raise ZeroDivisionError\n",
            "def estimer_noyau_s(*a, **k):\n    return -5\n",
            "ESTIMER = 1\n",
        ]
        for source in sources:
            with self.subTest(source=source):
                oublier_doubler()
                self.faux_doubler_py(source)
                self.assertIsNone(moteur.estimation_noyau_s([228.0], 30, [self.sorties]))
                self.assertEqual(self.selon_interface(), self.repli())

    def test_parametres_transmis(self):
        oublier_doubler()
        self.faux_doubler_py(
            "import json\n"
            "def estimer_noyau_s(durees_s, pas, sorties, nettoyage=True, mode='auto', moteur='yingmusic', paroles=True,\n"
            "                    voix_cibles=1, cartes=1):\n"
            "    global VU\n"
            "    VU = [durees_s, pas, [str(s) for s in sorties], nettoyage, mode, moteur, paroles, voix_cibles, cartes]\n"
            "    return 777\n")
        e = rapports.estimer([60, None], 50, None, nettoyage=True, mode="chant", moteur="seedvc", cartes=2,
                             paroles=False, voix_cibles=2, sorties=self.sorties)
        self.assertEqual(e["kaggle"], 777.0)
        self.assertEqual(moteur.module_doubler().VU,
                         [[60.0, 0.0], 50, [str(self.sorties)], True, "chant", "seedvc", False, 2, 2])
        # sans moteur choisi (ancien doubler sans --moteur) : YingMusic-SVC en chant nettoyé, Seed-VC sinon
        rapports.estimer([60], 30, None, nettoyage=False, sorties=self.sorties)
        self.assertEqual(moteur.module_doubler().VU[3:6], [False, "auto", "seedvc"])

    def test_doubler_charge_sans_etre_lance(self):
        marque = self.tmp / "lance"
        f = self.faux_doubler_py(
            "from pathlib import Path\n"
            "def estimer_noyau_s(*a, **k):\n    return 600\n"
            f"if __name__ == '__main__':\n    Path({str(marque)!r}).write_text('lancé')\n")
        self.assertEqual(self.selon_interface(), 600.0)
        self.assertFalse(marque.exists())
        self.assertFalse((f.parent / "__pycache__").exists())

    def test_rechargement_quand_doubler_change(self):
        f = self.faux_doubler_py("def estimer_noyau_s(*a, **k):\n    return 600\n")
        premier = moteur.module_doubler()
        self.assertEqual(self.selon_interface(), 600.0)
        self.assertIs(moteur.module_doubler(), premier)
        f.write_text("def estimer_noyau_s(*a, **k):\n    return 900\n", encoding="utf-8")
        t = f.stat().st_mtime + 5
        os.utime(f, (t, t))
        self.assertEqual(self.selon_interface(), 900.0)


# --- Les paroles dans le modèle de l'interface (repli) ------------------------------------------------------------------

class EssaiParolesDuRepli(unittest.TestCase):
    def test_part_des_paroles_dans_une_session(self):
        self.assertEqual(rapports.paroles_dans_session_s(rapport(paroles_total=120.0)), 120.0)       # après la conversion
        self.assertEqual(rapports.paroles_dans_session_s(rapport(paroles_total=150.0, pendant=140.0)), 70.0)
        self.assertEqual(rapports.paroles_dans_session_s(rapport()), 0.0)                            # --sans-paroles
        self.assertEqual(rapports.paroles_dans_session_s({"temps_local": {"session_kaggle": {"paroles_total_s": "?"}}}),
                         0.0)
        self.assertEqual(rapports.paroles_dans_session_s(None), 0.0)

    def test_sessions_sans_les_paroles(self):
        a = rapports.MODELE_DEFAUT["kaggle_nettoye_2cartes"][0]
        for r, paroles in ((rapport(noyau=1000.0, paroles_total=120.0), 120.0),
                           (rapport(noyau=1000.0, paroles_total=150.0, pendant=140.0), 70.0),
                           (rapport(noyau=1000.0), 0.0)):
            with self.subTest(paroles=paroles):
                m = rapports.modele_durees([res(r)])
                self.assertAlmostEqual(m["kaggle_nettoye_2cartes"][1], (1000.0 - paroles - a) / D, places=9)
        # une carte, sans nettoyage : de même
        r = rapport(cartes=1, noyau=700.0, paroles_total=100.0, nettoye=False, moteur_="seedvc")
        m = rapports.modele_durees([res(r)])
        self.assertAlmostEqual(m["kaggle"][1], (700.0 - 100.0 - rapports.MODELE_DEFAUT["kaggle"][0]) / D, places=9)

    def test_modele_des_paroles(self):
        a = rapports.MODELE_DEFAUT["paroles"][0]
        m = rapports.modele_durees([res(rapport(paroles_total=105.0))])
        self.assertAlmostEqual(m["paroles"][1], (105.0 - a) / D, places=9)
        # pendant la conversion : l'attente du chargement de la carte 0 est à part
        m = rapports.modele_durees([res(rapport(paroles_total=130.0, pendant=130.0))])
        self.assertAlmostEqual(m["paroles"][1], (130.0 - rapports.ATTENTE_CARTE0_S - a) / D, places=9)
        # modèles lus sur le montage (ancien noyau, sans copie locale), lot de vidéos, extrait de 5 s : écartés
        for r in (rapport(paroles_total=400.0, copie=False), rapport(paroles_total=400.0, videos=2),
                  rapport(paroles_total=400.0, d=5.0)):
            self.assertEqual(rapports.modele_durees([res(r)])["paroles"], rapports.MODELE_DEFAUT["paroles"])

    def test_conversion_a_deux_cartes(self):
        m = rapports.modele_durees([res(rapport(ying_total=320.0, charg_ying=20.0))])
        self.assertAlmostEqual(m["conversion_2cartes"][1], 300.0 / D, places=9)
        m = rapports.modele_durees([res(rapport(ying_total=380.0, charg_ying=20.0, paroles_total=120.0, pendant=120.0))])
        self.assertAlmostEqual(m["conversion_2cartes"][1], 300.0 / D, places=9)            # moitié des paroles retirée
        self.assertEqual(rapports.modele_durees([res(rapport(cartes=1))])["conversion_2cartes"],
                         rapports.MODELE_DEFAUT["conversion_2cartes"])

    def test_gpu_reel_d_un_lot_sans_les_paroles(self):
        # lot de deux vidéos à deux cartes, paroles pendant la conversion : part de YingMusic-SVC
        # (380 − 20 − 0,5 × 120) / 500 = 0,6, au lieu de (380 − 20) / 500 = 0,72 avec les paroles
        r = rapport(videos=2, ying_total=380.0, charg_ying=20.0, paroles_total=120.0, pendant=120.0)
        fn = (330.0 - 30.0) / 430.0
        self.assertAlmostEqual(rapports.gpu_reel_deux_cartes(r), 1100.0 - (1 - fn) * 430.0 - (1 - 0.6) * 500.0, places=6)

    def test_paroles_ajoutees_comme_doubler(self):
        m = rapports.MODELE_DEFAUT
        p = 25.0 + 0.45 * D

        def kaggle(**k):
            return rapports.estimer([k.pop("d", D)], 30, None, **{"nettoyage": True, "moteur": "yingmusic", **k})["kaggle"]
        une = kaggle(cartes=1, paroles=False)
        self.assertEqual(kaggle(cartes=1), round(une + p, 1))                     # une carte : en entier
        deux = kaggle(cartes=2, paroles=False)
        p2 = p + rapports.ATTENTE_CARTE0_S
        y = m["conversion_2cartes"][1] * D
        self.assertGreater(y, 0.5 * p2)
        self.assertEqual(kaggle(cartes=2), round(deux + 0.5 * p2, 1))             # deux cartes : la moitié
        # extrait très court : les paroles dépassent la conversion, ce qui dépasse compte (P − Y)
        court = 10.0
        p_court = 25.0 + 0.45 * court + rapports.ATTENTE_CARTE0_S
        y_court = m["conversion_2cartes"][1] * court
        self.assertGreater(p_court - y_court, 0.5 * p_court)
        self.assertEqual(kaggle(cartes=2, d=court), round(kaggle(cartes=2, d=court, paroles=False) + p_court - y_court, 1))
        # Seed-VC, ou la parole : pas de paroles pendant la conversion, P en entier même à deux cartes
        self.assertEqual(kaggle(cartes=2, moteur="seedvc"), round(kaggle(cartes=2, moteur="seedvc", paroles=False) + p, 1))
        self.assertEqual(kaggle(cartes=2, mode="parole"), round(kaggle(cartes=2, mode="parole", paroles=False) + p, 1))

    def test_paroles_mesurees_dans_le_repli(self):
        # deux sessions à deux cartes dont les paroles, longues (panne de mémoire inventée), passaient après la
        # conversion : elles ne pèsent plus sur la pente à deux cartes ; les paroles reviennent avec leur propre
        # modèle, que trois sessions à une carte aux paroles ordinaires tiennent
        hist = [res(rapport(d=170.0, noyau=1150.0, paroles_total=270.0)),
                res(rapport(d=170.0, noyau=1160.0, paroles_total=280.0)),
                *[res(rapport(cartes=1, d=220.0, noyau=1400.0, paroles_total=100.0)) for _ in range(3)]]
        m = rapports.modele_durees(hist)
        pente = (1150.0 - 270.0 - 230.0) / 170.0
        self.assertAlmostEqual(m["kaggle_nettoye_2cartes"][1], pente, places=9)
        self.assertAlmostEqual(m["paroles"][1], (100.0 - 25.0) / 220.0, places=9)
        p = 25.0 + m["paroles"][1] * 228.0 + rapports.ATTENTE_CARTE0_S
        apres = rapports.estimer([228.0], 30, m, nettoyage=True, moteur="yingmusic", cartes=2)["kaggle"]
        self.assertAlmostEqual(apres, 230.0 + pente * 228.0 + 0.5 * p, places=1)
        avant = 230.0 + (1150.0 - 230.0) / 170.0 * 228.0       # la pente d'avant, paroles comprises
        self.assertLess(apres, 0.85 * avant)


if __name__ == "__main__":
    unittest.main()
