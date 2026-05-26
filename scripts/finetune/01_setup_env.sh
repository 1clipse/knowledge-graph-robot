#!/bin/bash
# ============================================================
# 在 P100 服务器上执行
# 安装 QLoRA 微调所需的全部依赖
# 所有链接已替换为国内镜像
# ============================================================
set -e

echo "=== 安装 CUDA 版 PyTorch ==="
# 优先使用清华镜像，失败回退官方
pip install torch torchvision torchaudio \
    --index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
    || pip install torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu121

echo "=== 安装 微调核心库 ==="
pip install transformers==4.46.0 \
            peft==0.13.0 \
            accelerate==1.0.0 \
            bitsandbytes==0.44.0 \
            datasets==2.21.0

echo "=== 安装训练辅助 ==="
pip install sentencepiece \
            scipy \
            tensorboard \
            fire \
            packaging

echo "=== 验证 bitsandbytes CUDA 支持 ==="
python -c "
import torch
import bitsandbytes as bnb
print(f'CUDA available: {torch.cuda.is_available()}')
print(f'GPU: {torch.cuda.get_device_name(0)}')
print(f'VRAM: {torch.cuda.get_device_properties(0).total_mem / 1024**3:.1f} GB')
print(f'bitsandbytes version: {bnb.__version__}')
"

echo "=== 安装完成 ==="
