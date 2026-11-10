#!/usr/bin/env bash
# Construit dist/voix-la_<version>_all.deb : tests, paquet, sommes SHA-256, lintian.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Une seule version partout : debian/changelog, l'interface (voixla.__version__) et doubler --version
deb_version="$(dpkg-parsechangelog -S Version)"
py_version="$(PYTHONPATH=interface python3 -c 'import voixla; print(voixla.__version__)')"
cli_version="$(python3 doubler.py --version | awk '{print $NF}')"
aff_version="$(python3 affiner.py --version | awk '{print $NF}')"
if [[ "$deb_version" != "$py_version" || "$deb_version" != "$cli_version" || "$deb_version" != "$aff_version" ]]; then
  echo "Versions différentes : debian/changelog=$deb_version, voixla=$py_version, doubler=$cli_version, affiner=$aff_version" >&2
  exit 1
fi

make clean >/dev/null
# VOIXLA_SANS_TESTS=1 : seulement quand les tests viennent de passer sur le même code (reconstruction du paquet)
[[ "${VOIXLA_SANS_TESTS:-0}" == 1 ]] || make test
dpkg-buildpackage -us -uc -b
make clean >/dev/null

mkdir -p dist
mv -f "../voix-la_${deb_version}_all.deb" dist/
rm -f "../voix-la_${deb_version}_"*.buildinfo "../voix-la_${deb_version}_"*.changes
(cd dist && sha256sum ./*.deb | sed 's| \./| |' > SHA256SUMS)

# Les fichiers de l'affinage (commande affiner, noyau Kaggle, page de manuel) doivent être dans le paquet
contenu="$(dpkg-deb -c "dist/voix-la_${deb_version}_all.deb")"
for f in usr/bin/affiner usr/lib/voix-la/affiner.py usr/lib/voix-la/kaggle/affiner_kaggle.py usr/share/man/man1/affiner.1.gz \
         etc/apt/sources.list.d/voix-la.sources usr/share/keyrings/voix-la.gpg; do
  grep -q " \./${f}\$" <<<"$contenu" || { echo "Manquant dans le paquet : $f" >&2; exit 1; }
done

lintian --fail-on error "dist/voix-la_${deb_version}_all.deb"
echo "Paquet : dist/voix-la_${deb_version}_all.deb"
