#!/usr/bin/env bash
# Overnight full training on a dedicated GB10 (other GPU services stopped beforehand).
#   1. PatchCore, all 8 design+side categories (40 sheets, 2M-patch cap)        ~1.5 h
#   2. RF-DETR-Seg Small on all 11.3k tiles, <= 16 epochs, early stopping       ~6-8 h (hard cap 10 h)
#   3. Detections for the webapp's 160 sheets with the full detector + container rebuild
# Each step logs to logs/overnight_*.log; a failed step does not stop the next one.
#   cd cv_model && nohup scripts/overnight.sh > logs/overnight.log 2>&1 &
set -u
cd "$(dirname "$0")/.."
source env.sh
stamp() { echo "[$(date +%F' '%T)] $*"; }

stamp "free memory: $(free -g | awk '/Mem:/{print $7}') GB"

stamp "1/3 PatchCore (8 categories)"
python scripts/train_anomaly.py --train-sheets 40 --coreset 0.01 --max-patches 2000000 --batch 8 2>&1 \
  | grep --line-buffered -vE 'Warning|warn\(|Coreset Indices' > logs/overnight_anomaly.log
stamp "PatchCore exit ${PIPESTATUS[0]}: $(grep -c '^{' logs/overnight_anomaly.log) categories done"

stamp "2/3 RF-DETR-Seg Small, full data"
timeout --signal=INT --kill-after=5m 10h \
  python scripts/train_detector.py --size small --epochs 16 --batch 16 --accum 1 --workers 16 \
  > logs/overnight_detector.log 2>&1
stamp "detector exit $? (124 = hit the 10 h cap; the best checkpoint so far is used)"

W=models/detector/small/checkpoint_best_ema.pth
if [ -f "$W" ]; then
  stamp "3/3 webapp detections + rebuild"
  python scripts/poc_webapp.py --weights "$W" > logs/overnight_webapp.log 2>&1
  stamp "poc_webapp exit $?"
  (cd ../webapp && sg docker -c "docker compose up -d --build webapp") >> logs/overnight_webapp.log 2>&1
  stamp "webapp rebuild exit $?"
else
  stamp "no detector checkpoint at $W; webapp left on the POC model"
fi
stamp "done"
