#!/usr/bin/env bash
# Overnight full training that shares the GB10 with other agents/services: nothing else is stopped.
# Each step sizes itself to the memory available when it starts, keeping RESERVE_GB free for everyone else,
# and runs at lower CPU/IO priority.
#   1. PatchCore, all 8 design+side categories (40 sheets; patch cap scaled to free memory)   ~1.5-3 h
#   2. RF-DETR-Seg Small on all 11.3k tiles, <= 16 epochs, early stopping (batch scaled)      ~6-10 h (cap 11 h)
#   3. Detections for the webapp's 160 sheets with the full detector -> webapp/data/cv/manifest.json
#      (the container is NOT rebuilt unless REBUILD=1 — other agents may be editing webapp/)
#
#   cd cv_model && nohup scripts/overnight.sh > logs/overnight.log 2>&1 &
#   RESERVE_GB=20 REBUILD=1 nohup scripts/overnight.sh > logs/overnight.log 2>&1 &
set -u
cd "$(dirname "$0")/.."
source env.sh
RESERVE_GB=${RESERVE_GB:-15}     # always left free for other agents
REBUILD=${REBUILD:-0}
NICE="nice -n 10 ionice -c2 -n7"
stamp() { echo "[$(date +%F' '%T)] $*"; }
avail_gb() { awk '/MemAvailable/{printf "%d", $2/1048576}' /proc/meminfo; }

# wait (up to 1 h) until at least $1 GB can be used without eating into the reserve
wait_for() {
  local need=$1 t=0
  while [ $(( $(avail_gb) - RESERVE_GB )) -lt "$need" ] && [ $t -lt 3600 ]; do
    [ $((t % 600)) -eq 0 ] && stamp "waiting: need ${need} GB beyond the ${RESERVE_GB} GB reserve, available $(avail_gb) GB"
    sleep 60; t=$((t + 60))
  done
}

stamp "start; available $(avail_gb) GB, reserve ${RESERVE_GB} GB"

# ---- 1. PatchCore: peak ~ 20 GB per 1M patches (measured), cap 2M, floor 0.5M
wait_for 10
budget=$(( $(avail_gb) - RESERVE_GB ))
patches=$(( budget * 1000000 / 20 )); [ $patches -gt 2000000 ] && patches=2000000; [ $patches -lt 500000 ] && patches=500000
stamp "1/3 PatchCore: budget ${budget} GB -> --max-patches ${patches}"
$NICE python scripts/train_anomaly.py --train-sheets 40 --coreset 0.01 --max-patches $patches --batch 4 2>&1 \
  | grep --line-buffered -vE 'Warning|warn\(|Coreset Indices' > logs/overnight_anomaly.log
stamp "PatchCore exit ${PIPESTATUS[0]}: $(grep -c '^{' logs/overnight_anomaly.log) of 8 categories done"

# ---- 2. Detector: peak ~12 GB at batch 8 (measured), ~22 GB at batch 16
wait_for 8
budget=$(( $(avail_gb) - RESERVE_GB ))
if   [ $budget -ge 30 ]; then batch=16; accum=1
elif [ $budget -ge 16 ]; then batch=8;  accum=2
else                          batch=4;  accum=4; fi
stamp "2/3 RF-DETR-Seg Small: budget ${budget} GB -> batch ${batch} x accum ${accum}"
timeout --signal=INT --kill-after=5m 11h \
  $NICE python scripts/train_detector.py --size small --epochs 16 --batch $batch --accum $accum --workers 8 \
  > logs/overnight_detector.log 2>&1
stamp "detector exit $? (124 = hit the 11 h cap; the best checkpoint so far is used)"

# ---- 3. Webapp detections
W=models/detector/small/checkpoint_best_ema.pth
if [ -f "$W" ]; then
  stamp "3/3 webapp detections with $W"
  $NICE python scripts/poc_webapp.py --weights "$W" > logs/overnight_webapp.log 2>&1
  stamp "poc_webapp exit $? (manifest written; see logs/overnight_webapp.log for the sheet-level table)"
  if [ "$REBUILD" = "1" ]; then
    (cd ../webapp && sg docker -c "docker compose up -d --build webapp") >> logs/overnight_webapp.log 2>&1
    stamp "webapp rebuild exit $?"
  else
    stamp "container not rebuilt (REBUILD=0); run: cd webapp && docker compose up -d --build webapp"
  fi
else
  stamp "no detector checkpoint at $W; webapp left on the POC model"
fi
stamp "done"
