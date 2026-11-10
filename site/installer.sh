#!/bin/sh
# Installe Voix-là depuis son dépôt APT signé : https://mawena.github.io/Voix-la/
#   curl -fsSL https://mawena.github.io/Voix-la/installer.sh | sudo sh
# Il fait exactement les étapes de la page : clé du dépôt, dépôt, installation. Ensuite,
# `sudo apt update && sudo apt upgrade` apporte les nouvelles versions.
set -eu

DEPOT=https://mawena.github.io/Voix-la/apt
CLE=/usr/share/keyrings/voix-la.gpg
SOURCE=/etc/apt/sources.list.d/voix-la.sources

if [ "$(id -u)" -ne 0 ]; then
  echo "Voix-là : à lancer en administrateur, par exemple : curl -fsSL https://mawena.github.io/Voix-la/installer.sh | sudo sh" >&2
  exit 1
fi
if ! command -v apt-get >/dev/null 2>&1; then
  echo "Voix-là : apt-get introuvable ; Voix-là s'installe sur Ubuntu 24.04 ou plus récent." >&2
  exit 1
fi

telecharger() {   # adresse fichier
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then wget -qO "$2" "$1"
  else echo "Voix-là : il faut curl ou wget." >&2; exit 1
  fi
}

echo "1/3 Clé du dépôt -> $CLE"
telecharger "$DEPOT/voix-la.gpg" "$CLE.tmp" && chmod 644 "$CLE.tmp" && mv "$CLE.tmp" "$CLE"
echo "2/3 Dépôt -> $SOURCE"
telecharger "$DEPOT/voix-la.sources" "$SOURCE.tmp" && chmod 644 "$SOURCE.tmp" && mv "$SOURCE.tmp" "$SOURCE"
echo "3/3 Installation"
apt-get update
apt-get install -y voix-la
echo
echo "Voix-là est installé : « Voix-là » dans les applications, ou la commande doubler."
echo "Mises à jour : sudo apt update && sudo apt upgrade"
