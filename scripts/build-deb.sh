#!/usr/bin/env bash
# Construit dist/voix-la_<version>_all.deb : tests, paquet, sommes SHA-256, lintian.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Une seule version partout : debian/changelog, l'interface (voixla.__version__) et doubler --version
deb_version="$(dpkg-parsechangelog -S Version)"
py_version="$(PYTHONPATH=interface python3 -c 'import voixla; print(voixla.__version__)')"
cli_version="$(python3 doubler.py --version | awk '{print $NF}')"
if [[ "$deb_version" != "$py_version" || "$deb_version" != "$cli_version" ]]; then
  echo "Versions différentes : debian/changelog=$deb_version, voixla=$py_version, doubler=$cli_version" >&2
  exit 1
fi

make clean >/dev/null
make test
dpkg-buildpackage -us -uc -b
make clean >/dev/null

mkdir -p dist
mv -f "../voix-la_${deb_version}_all.deb" dist/
rm -f "../voix-la_${deb_version}_"*.buildinfo "../voix-la_${deb_version}_"*.changes
(cd dist && sha256sum ./*.deb | sed 's| \./| |' > SHA256SUMS)

lintian --fail-on error "dist/voix-la_${deb_version}_all.deb"
echo "Paquet : dist/voix-la_${deb_version}_all.deb"
