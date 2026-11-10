#!/usr/bin/env bash
# Publie un ou plusieurs .deb dans le dépôt APT signé de Voix-là (branche gh-pages,
# https://mawena.github.io/Voix-la/apt). Idempotent ; les anciennes versions restent dans pool/.
# Usage : scripts/publier-apt.sh dist/voix-la_<version>_all.deb...
# La page de présentation (site/) est publiée à la racine de la même branche.
# Variables : VOIXLA_APT_CLE (empreinte de la clé, défaut ci-dessous), VOIXLA_APT_PUSH=0 (ne pas pousser).
set -euo pipefail

CLE="${VOIXLA_APT_CLE:-20F9E2A5B48209DEDBD396010604C23539436022}"
RACINE="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
[ $# -ge 1 ] || { echo "usage : $0 fichier.deb..." >&2; exit 2; }
for f in "$@"; do [ -f "$f" ] || { echo "introuvable : $f" >&2; exit 2; }; done
DEBS=()
for f in "$@"; do DEBS+=("$(readlink -f "$f")"); done

cd "$RACINE"
git fetch -q origin gh-pages 2>/dev/null || true
TMP="$(mktemp -d)"
WT="$TMP/pages"
trap 'git -C "$RACINE" worktree remove --force "$WT" 2>/dev/null || true; rm -rf "$TMP"' EXIT

if git rev-parse -q --verify refs/remotes/origin/gh-pages >/dev/null; then
  git worktree add -q -B gh-pages "$WT" origin/gh-pages
else
  git worktree add -q --detach "$WT" HEAD
  git -C "$WT" checkout -q --orphan gh-pages
  git -C "$WT" rm -rq --cached . 2>/dev/null || true
  find "$WT" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
fi

APT="$WT/apt"
mkdir -p "$APT/pool/main"
touch "$WT/.nojekyll"
cp "${DEBS[@]}" "$APT/pool/main/"
cp "$RACINE/data/apt/voix-la.gpg" "$APT/voix-la.gpg"
cp "$RACINE/data/apt/voix-la.sources" "$APT/voix-la.sources"
# La page de présentation : tout le contenu de site/ à la racine de gh-pages, sans jamais toucher à apt/.
# Les fichiers retirés de site/ disparaissent aussi de la branche (seuls apt/, .nojekyll et .git sont gardés).
find "$WT" -mindepth 1 -maxdepth 1 ! -name apt ! -name .git ! -name .nojekyll -exec rm -rf {} +
cp -a "$RACINE/site/." "$WT/"

# Idempotence : rien de nouveau (paquets, clé, source, page) -> on ne régénère pas les index (Date change à chaque fois)
git -C "$WT" add -A
if git -C "$WT" diff --cached --quiet && [ -f "$APT/dists/stable/InRelease" ]; then
  echo "Dépôt APT déjà à jour."
  exit 0
fi

(
  cd "$APT"
  D=dists/stable/main
  mkdir -p "$D/binary-all" "$D/binary-amd64"
  dpkg-scanpackages --multiversion pool/main /dev/null > "$D/binary-all/Packages" 2>/dev/null
  : > "$D/binary-amd64/Packages"   # le paquet est « all » ; index vide pour que apt trouve les deux
  for a in all amd64; do gzip -9nc "$D/binary-$a/Packages" > "$D/binary-$a/Packages.gz"; done
  rm -f dists/stable/Release dists/stable/InRelease dists/stable/Release.gpg
  apt-ftparchive \
    -o APT::FTPArchive::Release::Origin=Voix-là \
    -o APT::FTPArchive::Release::Label=Voix-là \
    -o APT::FTPArchive::Release::Suite=stable \
    -o APT::FTPArchive::Release::Codename=stable \
    -o APT::FTPArchive::Release::Components=main \
    -o APT::FTPArchive::Release::Architectures="all amd64" \
    -o APT::FTPArchive::Release::Description="Voix-là, doublage par voix clonée" \
    release dists/stable > "$TMP/Release"
  mv "$TMP/Release" dists/stable/Release
  gpg --batch --yes --local-user "$CLE" --clearsign -o dists/stable/InRelease dists/stable/Release
  gpg --batch --yes --local-user "$CLE" --armor --detach-sign -o dists/stable/Release.gpg dists/stable/Release
)

cd "$WT"
git add -A
if git diff --cached --quiet; then
  echo "Dépôt APT déjà à jour."
else
  VERS="$(for d in "${DEBS[@]}"; do basename "$d"; done | tr '\n' ' ')"
  git -c user.name="$(git -C "$RACINE" config user.name)" -c user.email="$(git -C "$RACINE" config user.email)" \
    commit -q -m "feat(apt): publication de ${VERS% }"
fi
if [ "${VOIXLA_APT_PUSH:-1}" = 1 ]; then git push -q origin gh-pages; echo "gh-pages poussée."; fi
