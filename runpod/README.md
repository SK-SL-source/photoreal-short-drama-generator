# RunPod Serverless worker (`設定.json` có `"backend": "runpod"`)

Giúp phần sinh của skill này không cần giữ GPU chạy suốt: lúc không có việc, số worker = 0 (chỉ tốn phí lưu
trữ Network Volume), có việc mới đánh thức, xong tự thu về 0. Sổ hàng đợi, gate, kiểm SHA, permit vẫn chạy
y như cũ trên Mac (xem `scripts/佇列.py`, `scripts/檢查量產Gate.py`); thư mục này chỉ thay lớp "gửi việc đi
GPU bằng cách nào".

**2026-10-11 đã chạy pilot thật (không chỉ là thiết kế trên giấy):** triển khai ở CA-MTL-1, build + push
image, nạp model, tạo endpoint, gửi 5 job thật (object_info, nhạc ACE, H3 draft, H3 formal × 2) đều thành
công, chi phí đo thật khoảng $0.28 (GPU $0.05 + volume ngày hôm đó $0.23). Thời gian đo được và các lỗi gặp
phải ghi ở §7 và §6.

## 0. Vì sao không dùng `runpod/worker-comfyui`

README §Cần gì yêu cầu ComfyUI 0.37.2 + node MiniMax H3 có sẵn, cộng custom node
ComfyUI-MiniMax-H3-PDD-Acc và ComfyUI-GGUF. Image chính thức `runpod/worker-comfyui` hiện đóng gói ComfyUI
0.34.0 (chưa có node H3), và handler của nó chỉ thu key `images`, sẽ bỏ sót file flac mà `h3_voice`/`h3_vo`
lưu qua `SaveAudioAdvanced`. Vì vậy ở đây tự dựng một image riêng (`Dockerfile`), pin cứng phiên bản.

## 1. Kiến trúc

```
Mac (scripts/comfy.py, scripts/佇列.py)          RunPod CA-MTL-1
  scripts/rp.py ──POST /run──►  Endpoint (image này, workers.max bình thường = 0)
    │ upload input (đặt tên theo sha256) ─► Cloudflare R2 ◄── tải input ──┤ handler.py → ComfyUI (127.0.0.1:8188)
    │ ◄── tải output ──────────────────── Cloudflare R2 ◄── upload output ┤ /runpod-volume/models (Network Volume)
```

CA-MTL-1 không có S3 API cho Network Volume (đã kiểm danh sách datacenter tại
https://docs.runpod.io/storage/s3-api), nên input/output đi qua Cloudflare R2 chứ không qua endpoint S3 của
volume.

## 2. Giấy phép MiniMax H3 và chọn datacenter (đọc trước)

Giấy phép H3 loại trừ EU, Anh, Hàn Quốc, Mỹ (README §Giấy phép MiniMax H3). Bản triển khai này chọn
**CA-MTL-1 (Canada)**: nằm ngoài vùng loại trừ, GPU là A40 48GB (Ampere, cùng đời với RTX 3090 đã test
local). Chi tiết cân nhắc xem `../CLAUDE.md` hoặc lịch sử hội thoại — đổi datacenter thì phải kiểm lại nó
không nằm trong vùng loại trừ, và có S3 hoặc ít nhất dùng được R2.

## 3. Thiết lập một lần

### 3.1 Build image, push lên registry

Mac là arm64, image này cần linux/amd64 + CUDA. CI (GitHub Actions) nhanh nhất, nhưng cần push được workflow
lên một repo mình có quyền push — repo gốc của skill này thường chỉ có quyền pull (skill cài về, không phải
repo của mình), muốn dùng CI phải fork trước. Không fork thì build local bằng Docker Desktop's buildx (có
sẵn QEMU giả lập amd64):

```bash
docker buildx build --platform linux/amd64 \
  -t ghcr.io/<tài khoản GitHub của bạn>/<tên>:<tag> --push runpod/
```

**Đo thật** (2026-10-11, mạng nhà, build local bằng Docker Desktop): image **14.7GB**, toàn bộ build + push
mất **khoảng 2 giờ 45 phút** (tải layer + build ~1.5 giờ, push upload ~1.25 giờ). Đây là trường hợp cực đoan
nhất (giả lập local + băng thông upload mạng nhà), có CI (amd64 gốc, băng thông datacenter) sẽ nhanh hơn
nhiều. Lúc push trông như đứng im: `buildx` ghi log ra file không in % tiến trình (không có TTY), dùng
`nettop -l 2 -J bytes_in,bytes_out -x | grep docker.back` để xem tốc độ upload tức thời, xác nhận không thật
sự bị kẹt.

Image private (GHCR mặc định tạo mới là private, đổi sang public cần quyền PAT, token OAuth của `gh` hay
không đổi được, trả về 404) thì **không cần cố đổi public**: dùng MCP `create-registry` của RunPod
(username/password, password dùng `gh auth token`, cần `gh auth refresh -h github.com -s write:packages`
trước) lưu một bộ đăng nhập, lúc `create-endpoint` gắn `registry: <credential id>` là kéo được image private
— token có `write:packages` đã đủ quyền pull (không cần thêm `read:packages`).

### 3.2 Network Volume (CA-MTL-1)

Dùng MCP `create-network-volume`: `dataCenter: "CA-MTL-1"`, `type: "HIGH_PERFORMANCE"` (CA-MTL-1 chỉ có
tier này, không phải lựa chọn). Báo giá mỗi GB trước khi tạo ($0.07/GB/tháng, dưới 1TB).

Model bắt buộc + khuyến nghị (chưa gồm Qwen-Image GGUF tự chọn) đo thật tổng khoảng **85GB**, đặt `size`
**120** (85GB cộng đệm, không cần tới 150) là đủ; sau này thêm Qwen-Image GGUF thì tính lại có cần mở rộng
không.

### 3.3 Nạp model

1. Mở một pod rẻ nhất (CPU cũng được) ở đúng datacenter (CA-MTL-1), gắn volume vừa tạo (mount ở `/workspace`).
2. Web Terminal: upload `runpod/populate_volume.sh`, chạy `bash populate_volume.sh`
   (HuggingFace chậm hoặc cần đăng nhập: `HF_TOKEN=hf_xxx bash populate_volume.sh`).
3. Xác nhận danh sách file in ra (ít nhất 4 file bắt buộc: model chính H3, text encoder, video vae, audio
   vae), rồi **xoá pod này**.

### 3.4 Bucket R2 và key

Tạo một bucket R2 trên Cloudflare (free tier 10GB, nên đặt lifecycle rule: file trong `jobs/` tự xoá sau 7
ngày). Lấy Account ID, Access Key ID, Secret Access Key; endpoint R2 là
`https://<account id>.r2.cloudflarestorage.com`.

### 3.5 Endpoint

Dùng MCP `create-endpoint`. Báo giá GPU trước khi tạo (`list-gpu-types` tra giá serverless của A40). Cấu
hình chính:

| Mục | Giá trị |
|---|---|
| image | ảnh đã push ở 3.1 |
| GPU | `gpu.pools` chọn pool serverless có A40 (`list-gpu-types` tra pool id), `count: 1` |
| networkVolumes | id volume tạo ở 3.2 |
| dataCenterIds | `["CA-MTL-1"]` |
| workers | `{"min": 0, "max": 0, "idleTimeout": 90}` (đặt 0 trước, cần dùng mới tăng, xem "Dùng hằng ngày" bên dưới) |
| timeout (thời gian chạy tối đa) | 5400 (giây; formal 158 khung đo thật trên A40 là 731 giây, 277 khung theo cùng tỷ lệ tối đa khoảng 21 phút, 5400 giây vẫn dư nhiều, xem §7) |
| flashboot | `"FLASHBOOT"` |
| env | `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` (giá trị ở 3.4; dùng `create-secret` + `{{ RUNPOD_SECRET_xxx }}` để lưu, không ghi thành chữ thường) |

Lấy endpoint id, điền vào `scripts/設定.local.json` phía Mac, mục `runpod.endpoint_id` (hoặc biến môi trường
`RUNPOD_ENDPOINT_ID`).

### 3.6 Biến môi trường phía Mac

```bash
export RUNPOD_API_KEY=...         # RunPod console → Settings → API Keys
export R2_ACCESS_KEY_ID=...
export R2_SECRET_ACCESS_KEY=...
```

`RUNPOD_ENDPOINT_ID`, `R2_ENDPOINT`, `R2_BUCKET` có thể để biến môi trường, hoặc ghi vào `scripts/設定.local.json`
mục `runpod.*` (3 cái này không phải khóa bí mật, ghi vào file đỡ phải set lại mỗi lần mở terminal mới):

```json
{
  "backend": "runpod",
  "runpod": {"endpoint_id": "...", "r2_endpoint": "https://<account>.r2.cloudflarestorage.com", "r2_bucket": "...", "rate_usd_per_hour": 1.22}
}
```

Lúc này `comfy_dir` không phải thư mục ComfyUI nữa, mà là một thư mục rỗng bất kỳ trên máy, tự tạo 2 thư mục
con `input/`, `output/` bên trong (`stage_input`, kiểm SHA, copy output vẫn đi qua 2 thư mục này, giống hệt
backend=local).

### 3.7 Lưu một bản cache object_info

`check_graph`, `開工檢查.py` cần biết ComfyUI có những node nào, menu model có gì, nhưng worker RunPod không
thường trực, không hỏi trực tiếp được. Tạo cache một lần (đổi version image, đổi model trên volume thì phải
chạy lại):

```bash
python rp.py up 1       # đánh thức một worker
python rp.py object_info
python rp.py down       # lưu xong tắt ngay
```

### 3.8 Chạy một lần khai công kiểm tra

```bash
python 開工檢查.py
```

## 4. Dùng hằng ngày

```bash
python rp.py up [n]     # đặt workers.max = n (mặc định 1). Chạy trước khi bắt đầu việc
python 佇列.py wait      # như cũ: thu việc xong, gửi tiếp nối (xem scripts/佇列.py)
python rp.py down       # xong việc nhất định phải chạy: đặt workers.max về 0, sau đó chỉ còn tốn phí volume
python rp.py health     # xem endpoint có sống không, bao nhiêu worker, bất cứ lúc nào
```

`rp.py up/down` gọi tới `https://api.runpod.io/v2` (host điều khiển số worker), khác với host gửi việc
`https://api.runpod.ai/v2/<endpoint id>` — nếu `up` xong mà worker vẫn chưa dậy, dùng `python rp.py health`
và đối chiếu RunPod console xem `workers.max` có thật sự được đặt đúng số không, không phải gọi nhầm host.

## 5. Quy tắc chi phí

- **Không dùng thì `workers.max` bắt buộc phải là 0.** Mỗi lần xong việc chạy `python rp.py down`, rồi
  `python rp.py health` để xác nhận.
- Chi phí cố định chỉ có phí lưu trữ Network Volume (GB/tháng). R2 dùng trong free tier, đặt lifecycle rule
  dọn file `jobs/` cũ.
- GPU chỉ tính tiền lúc worker thật sự chạy (`IN_PROGRESS`); cold start (tải model, nạp) cũng tính vào, nên
  gửi nhiều việc liên tiếp trong cùng một đợt, tránh để trống quá lâu khiến worker thu về rồi phải cold start
  lại.

## 6. Xử lý sự cố

- **`python 開工檢查.py` báo không đọc được cache object_info**: chạy `python rp.py up` trước, rồi
  `python rp.py object_info`.
- **Cold start cứ bị timeout**: worker phải tải ~40GB model từ volume, lần đầu có thể mất vài phút;
  `COMFY_API_FALLBACK_MAX_RETRIES` trong `handler.py` cho 5 phút, không đủ thì tăng lên, đồng thời tăng luôn
  `timeout` (thời gian chạy tối đa) của endpoint. Đo thật cold start (xếp hàng + nạp model, không gồm lúc
  sinh) trung bình khoảng **6 phút** (xem §7).
- **A40 chậm hơn 3090, nhưng chậm bao nhiêu tùy profile**: draft (ngắn, độ phân giải nhỏ) chậm **2.7 lần**,
  formal (dài, độ phân giải chính thức) chỉ chậm **1.5 lần** — việc giải nén text encoder nvfp4 sang bf16 là
  chi phí cố định mỗi job, không phụ thuộc số khung, nên job ngắn chịu tỷ trọng cao hơn, job dài bị pha
  loãng. **Sản xuất chính thức dùng formal, cứ theo tỷ lệ 1.5 lần mà tính thời gian/chi phí, đừng áp tỷ lệ
  2.7 lần đo từ draft**. Muốn nhanh hơn nữa thì cần Blackwell mới có nvfp4 gốc, nhưng hiện ngoài vùng
  EU/Anh gần như không tìm được, tạm thời đừng đổi.
- **Vừa `rp.py up` xong gửi job ngay, báo `409 ENDPOINT_PAUSED`**: `workers.max` lan truyền từ control plane
  (`api.runpod.io`) sang host gửi việc (`api.runpod.ai`) có độ trễ vài giây tới hơn chục giây, `up` báo thành
  công chưa chắc gửi được ngay. `佇列.py`/`rp.submit` chưa tự động retry việc này, test tay thì đợi 10 giây
  thử lại vài lần là qua; viết script tự động thì nên thêm retry.
- **Python local báo `ModuleNotFoundError: No module named 'boto3'`**: backend=runpod phải cài boto3 đúng
  **Python thật sự dùng để chạy các script này** (mục `python` trong `設定.local.json`, thường là Python
  riêng của ComfyUI), không phải Python hệ thống; cài sai môi trường là lỗi hay gặp nhất.
- **`rp.py up`/`down` báo `HTTP 403`, nội dung có `error code: 1010`**: đây là Cloudflare chặn User-Agent mặc
  định của `urllib` (coi là bot), không phải lỗi quyền — `health` (gọi `api.runpod.ai`) không bị, chỉ
  `up`/`down` (gọi `api.runpod.io`) mới dính. `rp.py` đã sửa (gắn cố định User-Agent giống trình duyệt), lên
  bản mới là hết lỗi.
- **`佇列.py wait` đứng im không nhúc nhích**: `python rp.py health` xem worker có bị kẹt ở `UNHEALTHY`/
  `THROTTLED` không; cũng có thể image tự nó không khởi động được, xem log worker trên RunPod console.
- **Video H3 gửi đi cứ bị `GateBlocked` chặn**: đây là bình thường — gate (量產關卡.json, preflight, permit)
  ở backend này hoàn toàn không đổi, vấn đề nằm ở kịch bản/phân cảnh chưa khóa hoặc không khớp, không phải
  do RunPod (xem `../CLAUDE.md` mục "Vì sao video H3 chính thức có thể bị chặn").
- **Job rõ ràng đã chạy xong, nhưng `rp.py get_result`/`佇列.py status` báo vẫn đang chạy hoặc không tìm
  thấy**: trạng thái job của RunPod khoảng **30 phút sau là tra không ra** (`job not found`), nhưng worker
  xong việc là đã upload output lên R2 ngay, không liên quan gì tới việc có ai poll hay không. Trạng thái
  hết hạn thì vào thẳng `jobs/<job id>/` trên R2 tìm lại file là được — đây cũng chính là lý do `handler.py`
  thiết kế upload ngay khi sinh xong, không chờ client tới lấy. Dùng bình thường thì `佇列.py wait` luôn
  đang chạy nên không gặp trường hợp này; chỉ khi test tay bị ngắt quãng lâu rồi mới nối lại mới dính.

## 7. Số liệu đo thật (2026-10-11, CA-MTL-1, A40, endpoint `a31613hf7l83r5`)

Một lượt pilot: `object_info` → nhạc ACE 13 giây → H3 draft (124 khung) → H3 formal (158 khung, đo 2 lần, lần
đầu do mạng Mac bị mất DNS tạm thời nên không đọc được timestamp, lần sau đo bù). Toàn bộ 5 job, 0 lỗi.

| Job | Profile | Thời gian | So với 3090 |
|---|---|---|---|
| object_info | — | cold start + truy vấn | Lần đầu cold start, không có mốc so sánh |
| Nhạc ACE 13 giây | — | 36.6 giây (worker đang ấm, không cold start) | Không có số đo 3090 đối chiếu |
| H3 draft, 124 khung (416×736, PDD 8 bước) | draft | thực thi **164.1 giây**; tổng cả cold start **568 giây** | 3090 khoảng 60 giây → **chậm 2.70 lần** |
| H3 formal, 158 khung (768×1344, PDD 8 bước) | formal | thực thi **731.1 giây**; tổng cả cold start **1071 giây** | 3090 khoảng 480 giây (8 phút) → **chậm 1.52 lần** |

Cold start (xếp hàng + nạp model từ volume, không gồm lúc sinh) đo được 2 lần là 404 giây và 340 giây, trung
bình khoảng **370 giây (6.2 phút)**.

**Chi phí** (số thật từ RunPod billing, không phải ước tính): cả lượt pilot này tổng GPU **$0.0504**, volume
ngày hôm đó **$0.233**.

**Chi phí 1 clip chính thức** (formal, dùng giá đo thật $1.22/h của A40): 158 khung (đoạn clip 6.58 giây)
thực thi 731.1 giây ≈ **$0.248**, quy ra **$0.0376/giây clip**. Phần video của 1 tập 60 giây (chưa gồm thẻ
nhân vật, khung đầu, nhạc, lời bình) khoảng **$2.26**; tính đủ mọi khâu cho cả tập, ước khoảng **$3.3/tập**
(chi tiết xem lịch sử hội thoại hoặc hỏi Claude, file này chỉ ghi số liệu cứng đã đo được).

**Kiểm video**: cả 2 clip draft và formal đều tải về, đối chiếu bằng `ffprobe` — độ dài (số khung ÷ 24fps),
độ phân giải (416×736 / 768×1344), track audio (AAC stereo) đều khớp đúng yêu cầu; clip formal còn chụp
khung hình xem bằng mắt, nhân vật và bố cục bình thường.
