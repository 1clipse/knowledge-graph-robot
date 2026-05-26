#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# LLaMA-Factory 一键训练 + 导出 + 部署
# 在 P100 上运行: bash /data/finetune/run_llamafactory.sh
# ═══════════════════════════════════════════════════════════════
set -e
export PATH=/data/envs/qwen-api/bin:$PATH

echo "========================================"
echo "LLaMA-Factory 全自动训练管线"
echo "========================================"
echo ""

# ── 检查 ──
echo "[检查] LLaMA-Factory 版本..."
llamafactory-cli version 2>/dev/null || echo "(版本检测跳过)"
echo ""

echo "[检查] GPU 状态..."
nvidia-smi --query-gpu=index,name,memory.used --format=csv,noheader
echo ""

echo "[检查] 训练数据..."
python -c "
import json
with open('/data/finetune/data/kg_robot_llamafactory.json') as f:
    data = json.load(f)
print(f'  样本数: {len(data)}')
print(f'  第1条 query 长度: {len(data[0][\"query\"])} 字符')
"
echo ""

# ── 训练 ──
echo "========================================"
echo "第1步: QLoRA 训练"
echo "========================================"
CUDA_VISIBLE_DEVICES=0 llamafactory-cli train /data/finetune/train_config.yaml

echo ""
echo "训练完成！Checkpoint 列表:"
ls -d /data/finetune/llamafactory_output/checkpoint-* 2>/dev/null || echo "  (未找到 checkpoint)"
echo ""

# ── 导出 ──
echo "========================================"
echo "第2步: 合并导出模型"
echo "========================================"
CHECKPOINT=$(ls -d /data/finetune/llamafactory_output/checkpoint-* 2>/dev/null | sort -V | tail -1)

if [ -z "$CHECKPOINT" ]; then
    echo "错误: 没有找到 checkpoint"
    exit 1
fi

echo "使用 checkpoint: $CHECKPOINT"
EXPORT_DIR="/data/finetune/output/qwen2.5-7b-kg-robot-merged-v2"

llamafactory-cli export \
    --model_name_or_path /media/z/data/models/Qwen2.5-7B-Instruct \
    --adapter_name_or_path "$CHECKPOINT" \
    --template qwen \
    --finetuning_type lora \
    --export_dir "$EXPORT_DIR" \
    --export_size 2 \
    --export_legacy_format false

echo "合并模型已保存到: $EXPORT_DIR"
echo ""

# ── 部署 ──
echo "========================================"
echo "第3步: 更新 API 配置"
echo "========================================"

API_SCRIPT="/data/qwen_api.py"
if [ -f "$API_SCRIPT" ]; then
    # 备份
    cp "$API_SCRIPT" "${API_SCRIPT}.bak.$(date +%Y%m%d_%H%M%S)"
    # 更新 MODEL_PATH
    sed -i "s|^MODEL_PATH = .*|MODEL_PATH = \"$EXPORT_DIR\"|" "$API_SCRIPT"
    echo "MODEL_PATH 已更新为: $EXPORT_DIR"
    echo ""
    echo "========================================"
    echo "完成！手动启动 API:"
    echo "  CUDA_VISIBLE_DEVICES=0 python /data/qwen_api.py"
    echo ""
    echo "验证: curl http://10.117.29.24:5200/health"
    echo "========================================"
else
    echo "警告: 未找到 /data/qwen_api.py，跳过配置更新"
fi
