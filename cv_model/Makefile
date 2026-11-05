# CV defect model — one-command entry points (see PLAN.md). Run from cv_model/.
SHELL := /bin/bash
ENV := source env.sh &&

.PHONY: weights data fiftyone fiftyone-app ds-image train-anomaly poc-train poc-webapp poc-revert train-detector overnight

weights:        ## phase 0: download pretrained weights (online)
	CV_ONLINE=1 $(ENV) python scripts/fetch_weights.py

ds-image:       ## phase 0: DeepStream 9.0 + pyds image
	sg docker -c "docker build -t cv-defect/deepstream:9.0 docker/deepstream"

data:           ## phase 1: tiles + COCO / anomalib / VLM exports
	$(ENV) python scripts/prep_data.py

fiftyone:       ## phase 1: load sheets into FiftyOne
	$(ENV) python scripts/load_fiftyone.py

fiftyone-app:   ## browse in FiftyOne at :5151
	$(ENV) python scripts/load_fiftyone.py --app

train-anomaly:  ## phase 2: PatchCore per design + side (40 sheets, 1M-patch cap ≈ 20 GB peak, 1% coreset)
	$(ENV) python scripts/train_anomaly.py --train-sheets 40 --coreset 0.01 --max-patches 1000000 2>&1 | grep --line-buffered -vE 'Warning|warn\(|Coreset Indices' | tee logs/train_anomaly.log

# ---- quick POC (detector only, precomputed results in the webapp) ------------------------------
poc-train:      ## ~30 min: RF-DETR-Seg Small, 2 epochs on a class-balanced 3k-tile subset
	$(ENV) python scripts/make_poc_subset.py
	$(ENV) DET_DATA=$$CV_ROOT/data/tiles_coco_poc DET_OUT=$$CV_ROOT/models/detector/poc python scripts/train_detector.py --size small --epochs 2 --batch 8 --accum 1 2>&1 | tee logs/train_detector_poc.log

poc-webapp:     ## detections for the 160 webapp sheets -> webapp/data/cv/manifest.json, then rebuild the container
	$(ENV) python scripts/poc_webapp.py --weights models/detector/poc/checkpoint_best_ema.pth
	cd ../webapp && sg docker -c "docker compose up -d --build webapp"

poc-revert:     ## restore the placeholder manifest
	cp ../webapp/data/cv/manifest.placeholder.json ../webapp/data/cv/manifest.json

# ---- overnight: full training (each ~1-5 h on the GB10; check memory with the ITSM session first) ---------
train-detector: ## phase 3: RF-DETR-Seg Small on all 11.3k tiles, up to 20 epochs (early stopping)
	$(ENV) python scripts/train_detector.py --size small --epochs 20 --batch 8 --accum 2 2>&1 | tee logs/train_detector.log

overnight:      ## full training sized to free memory, nothing else stopped (scripts/overnight.sh; ~8-12 h)
	setsid nohup scripts/overnight.sh > logs/overnight.log 2>&1 < /dev/null &
	@echo "started; follow with: tail -f cv_model/logs/overnight.log"