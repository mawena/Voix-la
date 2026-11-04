"""Réglage de GStreamer, à faire avant que GTK ou GStreamer ne démarrent.

Décodage vidéo logiciel par défaut : les décodeurs matériels VA-API passent en rang NONE, et GStreamer choisit
alors avdec_h264 et les autres décodeurs logiciels. Raison : avec certains pilotes Intel iHD (24.1) sur des GPU Intel récents,
vah264dec fait planter toute l'interface (segfault dans iHD_drv_video.so, fil « vqueue:src ») quelques secondes
après le début de la lecture de certaines vidéos. Les mêmes images passent sans problème en logiciel, pour
environ un cœur pendant la lecture.

VOIXLA_DECODAGE_MATERIEL=1 garde le décodage matériel. Un rang que l'utilisateur a déjà fixé dans
GST_PLUGIN_FEATURE_RANK n'est jamais remplacé.
"""

import os

DECODEURS_MATERIELS = (
    # plugin « va » (GStreamer ≥ 1.22, rang PRIMARY depuis 1.24)
    "vah264dec", "vah265dec", "vaav1dec", "vavp8dec", "vavp9dec", "vampeg2dec", "vajpegdec", "vavc1dec",
    # ancien plugin « vaapi »
    "vaapih264dec", "vaapih265dec", "vaapiav1dec", "vaapivp8dec", "vaapivp9dec", "vaapimpeg2dec",
    "vaapijpegdec", "vaapivc1dec", "vaapidecodebin",
)


def preparer(env=None):
    """Écarte les décodeurs matériels dans env (os.environ par défaut), sauf demande contraire."""
    env = os.environ if env is None else env
    if env.get("VOIXLA_DECODAGE_MATERIEL", "").strip() in ("1", "oui", "true", "yes"):
        return env
    existants = [r for r in env.get("GST_PLUGIN_FEATURE_RANK", "").split(",") if r.strip()]
    deja = {r.split(":", 1)[0].strip() for r in existants}
    ajouts = [f"{nom}:NONE" for nom in DECODEURS_MATERIELS if nom not in deja]
    if ajouts:
        env["GST_PLUGIN_FEATURE_RANK"] = ",".join(existants + ajouts)
    return env
