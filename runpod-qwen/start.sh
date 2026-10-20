#!/usr/bin/env bash
# Worker 開機腳本：先確認 GPU 真的能跑（不是只是驅動在，而是能算），再啟動 ComfyUI 和 handler。

TCMALLOC="$(ldconfig -p | grep -Po "libtcmalloc.so.\d" | head -n 1)"
export LD_PRELOAD="${TCMALLOC}"

echo "worker-qwen 短劇：檢查 GPU…"
if ! GPU_CHECK=$(python3 -c "
import torch
try:
    torch.cuda.init()
    name = torch.cuda.get_device_name(0)
    cap = torch.cuda.get_device_capability(0)
    _ = (torch.zeros(8, device='cuda') + 1).sum().item()
    torch.cuda.synchronize()
    print(f'OK: {name} (sm_{cap[0]}{cap[1]}), torch {torch.__version__}, cuda {torch.version.cuda}')
except Exception as e:
    print(f'FAIL: {e}')
    exit(1)
" 2>&1); then
    echo "worker-qwen 短劇：GPU 不能用或跟這個 torch build 不相容："
    echo "worker-qwen 短劇：$GPU_CHECK"
    exit 1
fi
echo "worker-qwen 短劇：GPU 可用 — $GPU_CHECK"

: "${COMFY_LOG_LEVEL:=INFO}"
COMFY_PID_FILE="/tmp/comfyui.pid"

echo "worker-qwen 短劇：啟動 ComfyUI"
python -u /comfyui/main.py --disable-auto-launch --disable-metadata --verbose "${COMFY_LOG_LEVEL}" --log-stdout &
echo $! > "$COMFY_PID_FILE"

echo "worker-qwen 短劇：啟動 RunPod handler"
python -u /handler.py
