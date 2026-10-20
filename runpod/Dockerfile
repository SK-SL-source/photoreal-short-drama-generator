# Worker image cho RunPod Serverless — endpoint `h3-i2v-ref2va` (id a31613hf7l83r5),
# CHỈ còn MiniMax H3 (ref2va/fl2va, kể cả two-stage upscale + audio-refine) kể từ lần
# tách 2026-10-19/20 (xem dramaclaw's runpod-h3-qwen-endpoints.md mục 1 + 1.2/1.3): trước
# đó cùng image này còn chạy chung Qwen-Image 2.1 + Qwen-Image-Edit-2511 — 2 cái đó giờ
# dùng image riêng, xem ../runpod-qwen/Dockerfile.
#
# KHÔNG dùng runpod/worker-comfyui (xem runpod/README.md §1): image đó đi kèm ComfyUI 0.34.0,
# chưa có node MiniMax H3 (cần 0.37.2+), và handler chỉ gom output ở key "images" nên bỏ sót
# flac từ SaveAudioAdvanced (h3_voice、h3_vo 用). Image này tự dựng, ComfyUI pin đúng bản.
#
# Build (máy Mac arm64 không build được image amd64/cuda; build bằng CI, xem README §2):
#   docker buildx build --platform linux/amd64 -t <registry>/<tên>:<tag> --push runpod/

FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ARG COMFYUI_VERSION=0.37.2
ARG H3_PDD_ACC_COMMIT=311a65dd53832d8a5f8177a9d5fb923c09e35a90
ARG KJNODES_COMMIT=17195fcfa8a05f601b766a0f09c9733284f47416
# MiniMax H3 Chain Audio Refine Sampler (xem dramaclaw's runpod-h3-qwen-endpoints.md
# mục 1.3) — pack KHÁC PDD-Acc, cần thêm riêng cho tính năng audio-refine (env-gated,
# mặc định tắt). Không dùng theo nhánh, ghim đúng commit như 2 pack kia.
ARG CONTEXT_LOOP_COMMIT=43afeffcc570beb5f7382593e79e4b211c7e02f6

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_PREFER_BINARY=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_RETRIES=10 \
    PYTHONUNBUFFERED=1 \
    CMAKE_BUILD_PARALLEL_LEVEL=8

RUN apt-get update && apt-get install -y \
    python3.12 python3.12-venv git wget \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 ffmpeg \
    && ln -sf /usr/bin/python3.12 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip \
    && apt-get autoremove -y && apt-get clean -y && rm -rf /var/lib/apt/lists/*

RUN wget -qO- https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv \
    && ln -s /root/.local/bin/uvx /usr/local/bin/uvx \
    && uv venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# comfy-cli 版本固定：它決定怎麼裝 ComfyUI 和選 torch index，不固定版本會讓每次 build 的結果不一樣
RUN uv pip install comfy-cli==1.13.0 pip setuptools wheel
RUN /usr/bin/yes | comfy --workspace /comfyui install --version "${COMFYUI_VERSION}" --cuda-version 12.8 --nvidia

# 自訂節點：PDD 加速（正式檔位用）、KJNodes（SageAttention 的 PathchSageAttentionKJ patch
# node，見 dramaclaw's generators/comfy_sage.py）、Context-Loop（MiniMax H3 Chain Audio
# Refine Sampler，audio-refine 用）。照 commit 釘住，不要跟著分支漂。
# ComfyUI-GGUF 已拿掉 (2026-10-19/20 拆分時確認)：dramaclaw 的 H3 graph builder
# (runpod_minimax_h3_video.py) 完全沒用到任何 GGUF node/model 檔，這個 pack 在舊的共用
# image 上其實是 orphan（當初裝只是因為跟 Qwen 共用一個 image，Qwen 那邊才用得到量化檔）。
RUN git clone https://github.com/Jalen-Brunson/ComfyUI-MiniMax-H3-PDD-Acc /comfyui/custom_nodes/ComfyUI-MiniMax-H3-PDD-Acc \
    && git -C /comfyui/custom_nodes/ComfyUI-MiniMax-H3-PDD-Acc checkout "${H3_PDD_ACC_COMMIT}" \
    && git clone https://github.com/kijai/ComfyUI-KJNodes /comfyui/custom_nodes/ComfyUI-KJNodes \
    && git -C /comfyui/custom_nodes/ComfyUI-KJNodes checkout "${KJNODES_COMMIT}" \
    && git clone https://github.com/ethanfel/ComfyUI-MiniMaxH3-Context-Loop /comfyui/custom_nodes/ComfyUI-MiniMaxH3-Context-Loop \
    && git -C /comfyui/custom_nodes/ComfyUI-MiniMaxH3-Context-Loop checkout "${CONTEXT_LOOP_COMMIT}"

# torch 先裝、釘 cu128：ComfyUI requirements.txt 寫沒上限的 torch，預設可能抓到需要新驅動的 cu13 wheel
# sageattention==1.0.6 (PyPI, pure Triton kernel — no nvcc/devel image needed, works on Ampere+ incl. A40)
# đặt sau torch để dùng chung triton của torch, trước bước requirements loop (không có requirements.txt riêng)
# UV_HTTP_TIMEOUT cao hơn default (30s): mạng build local (QEMU amd64 trên Mac) đo được rất chậm
# (~1.2MB/s) và nhiều gói CUDA to (500-600MB) tải đồng thời, default timeout làm uv báo lỗi giữa chừng
ENV UV_HTTP_TIMEOUT=300
RUN uv pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 \
      --index-url https://download.pytorch.org/whl/cu128 \
    && uv pip install sageattention==1.0.6 \
    && uv pip install -r /comfyui/requirements.txt \
    && for r in /comfyui/custom_nodes/*/requirements.txt; do \
         [ -f "$r" ] && uv pip install -r "$r" || true; \
       done

# build 時就把 ComfyUI 整個 import 一次（CPU 跑就好）：啟動會死的相依問題在這裡就爆出來，
# 不要等到正式 worker 才看到一句看不出原因的「server not reachable」
RUN cd /comfyui && timeout 300 python main.py --quick-test-for-ci --cpu

WORKDIR /comfyui
ADD extra_model_paths.yaml ./
WORKDIR /

RUN uv pip install runpod requests websocket-client boto3

ADD start.sh handler.py ./
RUN chmod +x /start.sh

CMD ["/start.sh"]
