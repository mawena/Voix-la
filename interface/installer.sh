#!/bin/bash
# Installe l'interface Voix-là pour l'utilisateur courant (rien en dehors de ~/.local) :
#   ~/.local/bin/voix-la                                          lanceur (python3 -m voixla depuis ce dépôt)
#   ~/.local/share/applications/io.mawena.VoixLa.desktop           entrée du menu des applications
#   ~/.local/share/icons/hicolor/scalable/apps/io.mawena.VoixLa.svg  icône
# Usage : interface/installer.sh            (installer ou mettre à jour)
#         interface/installer.sh --retirer   (tout enlever)
set -euo pipefail
ICI=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
BIN=$HOME/.local/bin/voix-la
APPS=$HOME/.local/share/applications
ICONES=$HOME/.local/share/icons/hicolor/scalable/apps
DESKTOP=$APPS/io.mawena.VoixLa.desktop
ICONE=$ICONES/io.mawena.VoixLa.svg

if [ "${1:-}" = "--retirer" ]; then
  rm -f "$BIN" "$DESKTOP" "$ICONE"
  command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS" || true
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" || true
  echo "Voix-là retiré (le code et les données restent)."
  exit 0
fi

python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_version("Gst", "1.0")' \
  || { echo "Il manque PyGObject, GTK 4, libadwaita ou GStreamer (paquets python3-gi, gir1.2-adw-1, gir1.2-gst-plugins-base-1.0)."; exit 1; }

mkdir -p "$(dirname "$BIN")" "$APPS" "$ICONES"
cat > "$BIN" <<LANCEUR
#!/bin/bash
# Lanceur de l'interface Voix-là (créé par $ICI/installer.sh)
export PYTHONPATH="$ICI\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m voixla "\$@"
LANCEUR
chmod +x "$BIN"
sed "s|@LANCEUR@|$BIN|" "$ICI/donnees/io.mawena.VoixLa.desktop" > "$DESKTOP"
install -m 644 "$ICI/donnees/io.mawena.VoixLa.svg" "$ICONE"
command -v desktop-file-validate >/dev/null && desktop-file-validate "$DESKTOP" || true
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" || true
echo "Installé : $BIN, $DESKTOP, $ICONE"
