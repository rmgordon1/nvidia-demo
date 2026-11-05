#!/usr/bin/env bash
# Download the trained CV models made by scripts/bundle_models.sh, verify them and unpack into models/.
# Usage: scripts/fetch_models.sh https://github.com/<owner>/<repo>/releases/download/<tag>
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: $0 <release download URL>" >&2; exit 2; }
cd "$(dirname "$0")/.."
BASE="${1%/}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
for f in SHA256SUMS cv-detector.tar cv-anomaly.tar; do
  curl -fL --retry 3 -o "$TMP/$f" "$BASE/$f"
done
( cd "$TMP" && sha256sum -c SHA256SUMS )
for f in cv-detector.tar cv-anomaly.tar; do tar -xf "$TMP/$f"; done
echo "unpacked into $(pwd)/models"
