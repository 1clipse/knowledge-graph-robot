#!/bin/bash
# Merge LoRA adapter into full model and deploy
# Run on P100: bash /data/finetune/llamafactory_export.sh

set -e

CONDA_ENV="qwen-api"
BASE_MODEL="/media/z/data/models/Qwen2.5-7B-Instruct"
ADAPTER_DIR="/data/finetune/llamafactory_output"
# Auto-detect latest checkpoint
CHECKPOINT=$(ls -d "$ADAPTER_DIR"/checkpoint-* 2>/dev/null | sort -V | tail -1)
EXPORT_DIR="/data/finetune/output/qwen2.5-7b-kg-robot-merged-v2"
API_SCRIPT="/data/qwen_api.py"

if [ -z "$CHECKPOINT" ]; then
    echo "ERROR: No checkpoint found in $ADAPTER_DIR"
    exit 1
fi

echo "========================================"
echo "LLaMA-Factory Export & Deploy"
echo "========================================"
echo "Checkpoint: $CHECKPOINT"
echo "Export to:  $EXPORT_DIR"
echo ""

source ~/miniconda3/etc/profile.d/conda.sh 2>/dev/null || source ~/anaconda3/etc/profile.d/conda.sh 2>/dev/null
conda activate "$CONDA_ENV"

# Step 1: Export merged model
echo "[1/3] Merging LoRA into full model..."
llamafactory-cli export \
    --model_name_or_path "$BASE_MODEL" \
    --adapter_name_or_path "$CHECKPOINT" \
    --template qwen \
    --finetuning_type lora \
    --export_dir "$EXPORT_DIR" \
    --export_size 2 \
    --export_legacy_format false

echo "Merged model saved to: $EXPORT_DIR"

# Step 2: Update API config
echo "[2/3] Updating API config..."
if [ -f "$API_SCRIPT" ]; then
    # Backup original
    cp "$API_SCRIPT" "${API_SCRIPT}.bak.$(date +%Y%m%d_%H%M%S)"
    # Update MODEL_PATH
    sed -i "s|MODEL_PATH = .*|MODEL_PATH = \"$EXPORT_DIR\"|" "$API_SCRIPT"
    echo "MODEL_PATH updated in $API_SCRIPT"
else
    echo "WARNING: $API_SCRIPT not found, skip config update"
fi

# Step 3: Kill old API if running and restart
echo "[3/3] Restarting API..."
pkill -f "python.*qwen_api.py" 2>/dev/null || true
sleep 2

echo ""
echo "Done! To restart API manually:"
echo "  CUDA_VISIBLE_DEVICES=0 python /data/qwen_api.py"
echo ""
echo "Verify: curl http://10.117.29.24:5200/health"
