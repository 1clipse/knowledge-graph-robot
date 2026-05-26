#!/bin/bash
# LLaMA-Factory QLoRA training for KG robot entity extraction
# Run on P100 server: bash /data/finetune/llamafactory_train.sh

set -e

CONDA_ENV="qwen-api"
BASE_MODEL="/media/z/data/models/Qwen2.5-7B-Instruct"
DATASET_NAME="kg_robot"
OUTPUT_DIR="/data/finetune/llamafactory_output"
DATA_DIR="/data/finetune/data"

echo "========================================"
echo "LLaMA-Factory QLoRA Training"
echo "========================================"
echo "Model: $BASE_MODEL"
echo "Dataset: $DATASET_NAME"
echo "Output: $OUTPUT_DIR"
echo ""

# Activate conda
source ~/miniconda3/etc/profile.d/conda.sh 2>/dev/null || source ~/anaconda3/etc/profile.d/conda.sh 2>/dev/null
conda activate "$CONDA_ENV"

# Verify llamafactory is installed
if ! python -c "import llamafactory" 2>/dev/null; then
    echo "ERROR: llamafactory not installed. Run: pip install llamafactory"
    exit 1
fi

echo "Starting training..."
CUDA_VISIBLE_DEVICES=0 llamafactory-cli train \
    --model_name_or_path "$BASE_MODEL" \
    --dataset "$DATASET_NAME" \
    --template qwen \
    --finetuning_type lora \
    --lora_rank 16 \
    --lora_alpha 32 \
    --lora_dropout 0.05 \
    --lora_target all \
    --quantization_method bitsandbytes \
    --quantization_bit 4 \
    --bf16 false \
    --fp16 true \
    --per_device_train_batch_size 1 \
    --gradient_accumulation_steps 2 \
    --learning_rate 2e-4 \
    --lr_scheduler_type cosine \
    --warmup_steps 5 \
    --num_train_epochs 3 \
    --max_seq_length 1024 \
    --max_grad_norm 0.3 \
    --logging_steps 1 \
    --save_steps 50 \
    --save_total_limit 3 \
    --output_dir "$OUTPUT_DIR" \
    --report_to tensorboard \
    --overwrite_output_dir

echo ""
echo "Training complete!"
echo "Checkpoints: $OUTPUT_DIR"
ls -la "$OUTPUT_DIR"
