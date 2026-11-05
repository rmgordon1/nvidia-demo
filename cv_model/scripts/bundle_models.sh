#!/usr/bin/env bash
# Pack the trained CV models for a GitHub release (each release file must be < 2 GiB, so two tarballs):
#   cv-detector.tar   models/detector/small: best EMA checkpoint + training config + metrics   (~130 MB)
#   cv-anomaly.tar    models/anomaly: one PatchCore model per banknote design/side             (~1.8 GB)
#   SHA256SUMS
# Usage: scripts/bundle_models.sh [out_dir]   (default dist/). Upload all three files to one release,
# then on the other machine run scripts/fetch_models.sh with that release's download URL.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-dist}"
mkdir -p "$OUT"
# -h: models/ is a symlink into ~/gb10-lab-data on the GB10
tar -chf "$OUT/cv-detector.tar" models/detector/small/checkpoint_best_ema.pth \
    models/detector/small/training_config.json models/detector/small/metrics.csv
tar -chf "$OUT/cv-anomaly.tar" models/anomaly
( cd "$OUT" && sha256sum cv-detector.tar cv-anomaly.tar > SHA256SUMS && ls -lh cv-*.tar && cat SHA256SUMS )
