# RunPod Serverless worker — Qwen-Image 2.1 + Qwen-Image-Edit-2511

Image dùng chung cho 2 endpoint tách ra khỏi endpoint H3 cũ ngày 2026-10-19/20 (xem
dramaclaw's `runpod-h3-qwen-endpoints.md` mục 2-3 cho đầy đủ lý do/thiết kế):

- `qwen-edit-2511-relight` — Qwen-Image-Edit-2511.
- `qwen-image-t2i-fast` — Qwen-Image 2.1.

Kiến trúc/hạ tầng (R2, Network Volume, handler, start.sh) giống hệt `../runpod/README.md`
— không lặp lại ở đây, chỉ khác là image này **không có custom node nào** (xem comment
đầu `Dockerfile`), nên build/pull nhanh hơn image H3 nhiều.

## Build + push

```bash
docker buildx build --platform linux/amd64 \
  -t ghcr.io/<tài khoản GitHub của bạn>/<tên>:<tag> --push runpod-qwen/
```

Deploy image này lên CẢ 2 endpoint (Qwen-Edit lẫn Qwen-t2i) — không cần build riêng
từng cái, nội dung image giống nhau 100%.

## Model

Dùng chung Network Volume (`zyen2jmxt3`) với endpoint H3 — các file model Qwen-Image 2.1
(`qwen_image_2.1_int8_convrot.safetensors`, `qwen3vl_8b_int8_convrot.safetensors`,
`qwen_image_2.1_vae_bf16.safetensors`) và Qwen-Image-Edit-2511
(`qwen_image_edit_2511_int8_convrot.safetensors`, `qwen_2.5_vl_7b_fp8_scaled.safetensors`,
`qwen_image_vae.safetensors`) đã có sẵn trên volume đó từ trước khi tách — không cần nạp
lại.
