#!/bin/bash
# Capture d'écran de l'interface sans ouvrir de fenêtre sur l'écran (Xvfb), une seule à la fois (verrou), en nice.
#   outils/capturer.sh <fichier.png> [clair|sombre] [scenario] [LARGEURxHAUTEUR] [délai_s]
# Variables utiles : VOIXLA_DONNEES, VOIXLA_ETAT, VOIXLA_CONFIG, VOIXLA_ECOUTE, VOIXLA_DOUBLER (données d'essai).
# Sur un bus de session privé (dbus-run-session, outils/bus-essai.conf, sans service activable) quand il existe : le
# lecteur MPRIS d'essai, les notifications et le nom d'application d'essai n'atteignent jamais le bureau.
set -euo pipefail
ICI=$(cd "$(dirname "$0")/.." && pwd)
SORTIE=$1; THEME=${2:-clair}; SCENARIO=${3:-}; TAILLE=${4:-1180x800}; DELAI=${5:-4}
VERROU=${VOIXLA_VERROU_XVFB:-${XDG_RUNTIME_DIR:-/tmp}/voix-la-xvfb.verrou}
mkdir -p "$(dirname "$VERROU")"
case "$THEME" in sombre) SCHEMA=prefer-dark ;; *) SCHEMA=prefer-light ;; esac
BUS=()
if command -v dbus-run-session >/dev/null; then BUS=(dbus-run-session --config-file="$ICI/outils/bus-essai.conf" --); fi
cd "$ICI"
exec flock "$VERROU" env ADW_DEBUG_COLOR_SCHEME=$SCHEMA GTK_A11Y=none NO_AT_BRIDGE=1 ADW_DISABLE_PORTAL=1 \
  GIO_USE_VFS=local VOIXLA_APP_ID=io.mawena.VoixLa.Essai$$ VOIXLA_CAPTURE="$SORTIE" VOIXLA_SCENARIO="$SCENARIO" \
  VOIXLA_TAILLE="$TAILLE" VOIXLA_CAPTURE_DELAI="$DELAI" GSK_RENDERER=${GSK_RENDERER:-cairo} \
  timeout 60 nice -n 10 "${BUS[@]}" xvfb-run -a -s "-screen 0 1800x1800x24" python3 -m voixla
