"""Réglage de GStreamer avant la lecture : décodage vidéo logiciel par défaut.

Le décodeur matériel Intel (élément vah264dec, certains pilotes iHD 24.1) peut faire planter l'interface
(segfault dans iHD_drv_video.so) au bout de quelques secondes de lecture ; avdec_h264 la lit.
"""

import unittest

from voixla import gstreamer


class TestDecodageLogiciel(unittest.TestCase):

    def test_ecarte_les_decodeurs_va_par_defaut(self):
        env = {}
        gstreamer.preparer(env)
        rangs = env["GST_PLUGIN_FEATURE_RANK"].split(",")
        self.assertIn("vah264dec:NONE", rangs)
        self.assertIn("vah265dec:NONE", rangs)
        self.assertIn("vaapih264dec:NONE", rangs)

    def test_garde_les_rangs_choisis_par_l_utilisateur(self):
        env = {"GST_PLUGIN_FEATURE_RANK": "vah264dec:PRIMARY,pulsesink:MAX"}
        gstreamer.preparer(env)
        rangs = env["GST_PLUGIN_FEATURE_RANK"].split(",")
        self.assertIn("pulsesink:MAX", rangs)
        self.assertIn("vah264dec:PRIMARY", rangs)       # son choix gagne
        self.assertNotIn("vah264dec:NONE", rangs)
        self.assertIn("vah265dec:NONE", rangs)          # les autres restent écartés

    def test_decodage_materiel_sur_demande(self):
        env = {"VOIXLA_DECODAGE_MATERIEL": "1"}
        gstreamer.preparer(env)
        self.assertNotIn("GST_PLUGIN_FEATURE_RANK", env)

    def test_deux_appels_ne_doublent_rien(self):
        env = {}
        gstreamer.preparer(env)
        premier = env["GST_PLUGIN_FEATURE_RANK"]
        gstreamer.preparer(env)
        self.assertEqual(env["GST_PLUGIN_FEATURE_RANK"], premier)


if __name__ == "__main__":
    unittest.main()
