#!/usr/bin/env bash
# Nạp model vào Network Volume cho endpoint RunPod Serverless (chạy MỘT LẦN, hoặc khi đổi model).
# Thư mục khớp với runpod/extra_model_paths.yaml của chính image này (diffusion_models/text_encoders/vae/pdd_acc),
# không phải tên cũ (unet/clip) mà worker-comfyui dùng — image này không cần tương thích với nó.
#
# Cách chạy:
#   1. Tạo Network Volume ở ĐÚNG datacenter sẽ chạy endpoint (volume khóa theo datacenter, xem runpod/README.md).
#   2. Deploy một pod RẺ NHẤT (CPU cũng được) trong CHÍNH datacenter đó, gắn volume (mount /workspace).
#   3. Web Terminal của pod: bash populate_volume.sh   (Hugging Face chậm/bị khóa: HF_TOKEN=hf_xxx bash populate_volume.sh)
#   4. Xong thì Terminate pod. Endpoint serverless sẽ thấy volume ở /runpod-volume.
#
# QWEN_IMAGE_GGUF (tùy chọn, tự chọn file GGUF của Qwen-Image 2.1 chính, dùng sinh thẻ nhân vật/场景/首幀):
#   2026-10-11: repo "Comfy-Org/Qwen-Image-2.1-GGUF" KHÔNG tồn tại (đã kiểm, 401/not found) — đừng dùng.
#   Đã thử "unsloth/Qwen-Image-2.1-GGUF" (file đúng version 2.1, tồn tại thật) nhưng GGUF của repo đó
#   KHÔNG tương thích ComfyUI-GGUF: worker báo "Unknown model architecture!" ở detect_arch (xem
#   movies/CuChiTunnel/工單.md "問題與經驗" nếu còn trong repo, hoặc lịch sử hội thoại 2026-10-11).
#   city96/Qwen-Image-gguf và QuantStack/Qwen-Image-GGUF là bản Qwen-Image GỐC (không phải 2.1), không
#   khớp text_encoder/vae 2.1 đã nạp ở trên — đừng trộn.
#   => Chưa tìm được nguồn GGUF Qwen-Image 2.1 nào xác nhận chạy được với ComfyUI-GGUF. Trước khi thử file
#   mới, xác nhận tương thích ở môi trường rẻ/miễn phí trước khi tốn pod-time tải+chạy thật.
#   QWEN_IMAGE_GGUF="<repo> <path-trong-repo>" bash populate_volume.sh
set -euo pipefail

VOL="${VOL:-/workspace}"
M="$VOL/models"
[[ -d "$VOL" ]] || { echo "Không thấy $VOL. Pod đã gắn Network Volume chưa?"; exit 1; }
mkdir -p "$M"/{diffusion_models,text_encoders,vae,pdd_acc}

avail_gb=$(df -BG --output=avail "$VOL" | tail -1 | tr -dc '0-9')
echo "Volume còn trống: ${avail_gb} GB (cần ~90 GB cho bộ bắt buộc + chọn)"
(( avail_gb >= 90 )) || { echo "Không đủ chỗ, tăng dung lượng volume"; exit 1; }

if ! command -v aria2c >/dev/null; then
  (apt-get update -qq && apt-get install -y -qq aria2) >/dev/null 2>&1 || echo "Không cài được aria2, dùng wget"
fi
AUTH=()
[[ -n "${HF_TOKEN:-}" ]] && AUTH=(--header="Authorization: Bearer ${HF_TOKEN}")

fetch() {  # fetch <repo> <đường dẫn trong repo> <thư mục trong models/> [tên file lưu]
  local repo=$1 path=$2 dest="$M/$3" name=${4:-$(basename "$2")}
  if [[ -f "$dest/$name" && ! -f "$dest/$name.aria2" ]]; then echo "đã có  $3/$name"; return; fi
  echo "tải    $3/$name"
  if command -v aria2c >/dev/null; then
    aria2c -q -x 16 -s 16 -k 1M -c "${AUTH[@]}" -d "$dest" -o "$name" "https://huggingface.co/$repo/resolve/main/$path"
  else
    wget -q -c "${AUTH[@]}" -O "$dest/$name" "https://huggingface.co/$repo/resolve/main/$path"
  fi
}

H3=Comfy-Org/MiniMax-H3
ACC=alibaba-pai/MiniMax-H3-Acc-LoRAs
ACE=Comfy-Org/ace_step_1.5_ComfyUI_files
QI=Comfy-Org/Qwen-Image-2.1

# 片段（必要）
fetch $H3 diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors diffusion_models
fetch $H3 text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors         text_encoders
fetch $H3 vae/minimax_h3_video_vae_fp16.safetensors                          vae
fetch $H3 vae/minimax_h3_audio_vae_fp32.safetensors                          vae

# 正式檔位加速（PDD 8 步，沒有就只能用較慢、已知會漏角色卡的 base20）
fetch $ACC MiniMax-H3-Ref2VA-Acc-8Step.safetensors pdd_acc

# 生音色檔、I2VA（選用）
fetch $H3 diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors diffusion_models

# 配樂（選用）
fetch $ACE split_files/diffusion_models/acestep_v1.5_turbo.safetensors diffusion_models
fetch $ACE split_files/text_encoders/qwen_0.6b_ace15.safetensors       text_encoders
fetch $ACE split_files/text_encoders/qwen_1.7b_ace15.safetensors       text_encoders
fetch $ACE split_files/vae/ace_1.5_vae.safetensors                     vae

# 生角色卡、首幀（選用，GGUF 主模型另外設定，見上面 QWEN_IMAGE_GGUF）
fetch $QI text_encoders/qwen3vl_8b_int8_convrot.safetensors text_encoders
fetch $QI vae/qwen_image_2.1_vae_bf16.safetensors            vae
if [[ -n "${QWEN_IMAGE_GGUF:-}" ]]; then
  read -r repo path <<< "$QWEN_IMAGE_GGUF"
  fetch "$repo" "$path" diffusion_models
else
  echo "跳過 Qwen-Image 2.1 GGUF 主模型（沒設 QWEN_IMAGE_GGUF）：角色卡/場景卡/首幀功能停用，設定.json 的 models.qwen_image 留空"
fi

echo; echo "Kiểm tra:"; ls -lh "$M"/diffusion_models "$M"/text_encoders "$M"/vae "$M"/pdd_acc
du -shL "$M"
