#!/bin/sh
# Installe Voix-là depuis le dépôt APT unique de mawena : https://apt.mawena.cloud/
#   curl -fsSL https://mawena.github.io/Voix-la/installer.sh | sudo sh
# Depuis la 0.20.2, Voix-là est publié dans le dépôt unique : ce script lance son installateur avec le paquet voix-la.
# Équivaut à : curl -fsSL https://apt.mawena.cloud/installer.sh | sudo sh -s -- voix-la
set -eu

INSTALLATEUR=https://apt.mawena.cloud/installer.sh

if [ "$(id -u)" -ne 0 ]; then
  echo "Voix-là : à lancer en administrateur, par exemple : curl -fsSL $INSTALLATEUR | sudo sh -s -- voix-la" >&2
  exit 1
fi
if ! command -v apt-get >/dev/null 2>&1; then
  echo "Voix-là : apt-get introuvable ; Voix-là s'installe sur Ubuntu 24.04 ou plus récent." >&2
  exit 1
fi

if command -v curl >/dev/null 2>&1; then curl -fsSL "$INSTALLATEUR" | sh -s -- voix-la
elif command -v wget >/dev/null 2>&1; then wget -qO- "$INSTALLATEUR" | sh -s -- voix-la
else echo "Voix-là : il faut curl ou wget." >&2; exit 1
fi
echo
echo "Voix-là est installé : « Voix-là » dans les applications, ou la commande doubler."
echo "Mises à jour : sudo apt update && sudo apt upgrade"
