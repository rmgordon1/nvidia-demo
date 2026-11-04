# source cv_model/env.sh  — project environment (venv, caches, offline switches)
CV_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CV_ROOT
source "$CV_ROOT/.venv/bin/activate"
export WORKSHOP_HF_HUB="${HF_HUB_CACHE:-/opt/workshop/models/hf/hub}"   # shared, read-only (Cosmos-Reason2 etc.)
export HF_HOME="$CV_ROOT/.cache/hf"            # project cache for our own downloads
export HF_HUB_CACHE="$HF_HOME/hub"
export TORCH_HOME="$CV_ROOT/.cache/torch"
export FIFTYONE_DATABASE_DIR="$CV_ROOT/.cache/fiftyone_db"
export FIFTYONE_DO_NOT_TRACK=true
export MLFLOW_TRACKING_URI="sqlite:///$CV_ROOT/mlflow.db"   # local; artifacts under mlruns/
export MLFLOW_ARTIFACTS_DESTINATION="$CV_ROOT/mlruns"
export MLFLOW_DISABLE_AGENT_HINT=1
export DATASET_DIR="$CV_ROOT/../synthetic_print/dataset"
# Offline by default; `CV_ONLINE=1 source env.sh` during setup downloads.
if [ "${CV_ONLINE:-0}" = "1" ]; then
  unset HF_HUB_OFFLINE TRANSFORMERS_OFFLINE HF_DATASETS_OFFLINE  # the login profile sets these globally
else
  export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
fi
