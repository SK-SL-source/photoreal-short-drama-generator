# RunPod Serverless worker（`設定.json` 的 `"backend": "runpod"`）

讓這個 skill 的生成不用一直開著一台 GPU 機器：沒有工作時 worker 數是 0（只收 Network Volume 的存放費），
有工作才叫醒，跑完自動縮回 0。帳本、關卡、SHA 比對、permit 這些照舊在 Mac 上跑（見 `scripts/佇列.py`、
`scripts/檢查量產Gate.py`）；這個資料夾只是換掉「怎麼把工作送去 GPU」那一層。

## 0. 為什麼不用 `runpod/worker-comfyui`

README §需要什麼 要 ComfyUI 0.37.2＋內建的 MiniMax H3 節點，還要自訂節點 ComfyUI-MiniMax-H3-PDD-Acc、ComfyUI-GGUF。
官方 `runpod/worker-comfyui` 目前包的是 ComfyUI 0.34.0（沒有 H3 節點），而且它的 handler 只收 `images` 這個 key，
會漏掉 `h3_voice`／`h3_vo` 用 `SaveAudioAdvanced` 存的 flac。所以這裡自己起一個 image（`Dockerfile`），固定版本。

## 1. 架構

```
Mac（scripts/comfy.py、scripts/佇列.py）          RunPod CA-MTL-1
  scripts/rp.py ──POST /run──►  Endpoint（這個 image，workers.max 平時 0）
    │ 上傳輸入（sha256 命名）─► Cloudflare R2 ◄── 下載輸入 ──┤ handler.py → ComfyUI（本機 127.0.0.1:8188）
    │ ◄── 下載輸出 ─────────── Cloudflare R2 ◄── 上傳輸出 ──┤ /runpod-volume/models（Network Volume）
```

CA-MTL-1 沒有 Network Volume 的 S3-相容 API（已查 https://docs.runpod.io/storage/s3-api 的資料中心清單），
所以輸入/輸出走 Cloudflare R2，不是 volume 自己的 S3 端點。

## 2. MiniMax H3 授權和機房（先讀）

H3 的授權排除歐盟、英國、韓國、美國（README §MiniMax H3 的授權）。這個部署選 **CA-MTL-1（加拿大）**：
不在排除地區，GPU 是 A40 48 GB（Ampere，跟本機測過的 RTX 3090 同代）。詳細取捨見 `../CLAUDE.md`
或對話紀錄——換機房要重新確認那個機房不在排除清單，而且有 S3 或至少能用 R2。

## 3. 一次性設定

### 3.1 Build image、推上 registry

Mac 是 arm64，這個 image 要 linux/amd64＋CUDA，建議用 CI（GitHub Actions）build：

```bash
docker buildx build --platform linux/amd64 -t <registry>/<名字>:<tag> --push runpod/
```

Image 私有的話，用 RunPod MCP 的 `create-registry` 存登入資訊，`create-endpoint` 時帶 `registry`。

### 3.2 Network Volume（CA-MTL-1）

用 MCP `create-network-volume`：`dataCenter: "CA-MTL-1"`，`size` 先給 150（GB，HIGH_PERFORMANCE 看該機房有沒有這個 tier，
`list-data-centers` 查）。建立前先報一次 GB 單價。

### 3.3 灌模型

1. 在同一個機房（CA-MTL-1）開一個最便宜的 pod（CPU 也可以），把剛建的 volume 掛上去（mount 在 `/workspace`）。
2. Web Terminal：上傳 `runpod/populate_volume.sh`，跑 `bash populate_volume.sh`
   （HuggingFace 慢或要登入：`HF_TOKEN=hf_xxx bash populate_volume.sh`）。
3. 確認印出的檔案清單（至少 4 個必要檔：H3 主模型、text encoder、video vae、audio vae），然後 **刪掉這個 pod**。

### 3.4 R2 bucket 和金鑰

Cloudflare 開一個 R2 bucket（免費額度 10 GB，建議設 lifecycle 規則：`jobs/` 底下的檔案 7 天後自動刪）。
拿到 Account ID、Access Key ID、Secret Access Key；R2 endpoint 是 `https://<account id>.r2.cloudflarestorage.com`。

### 3.5 Endpoint

用 MCP `create-endpoint`。建立前先報 GPU 單價（`list-gpu-types` 查 A40 的 serverless 單價）。關鍵設定：

| 項目 | 值 |
|---|---|
| image | 3.1 推上去的那個 |
| GPU | `gpu.pools` 選含 A40 的 serverless pool（`list-gpu-types` 查 pool id），`count: 1` |
| networkVolumes | 3.2 建的 volume id |
| dataCenterIds | `["CA-MTL-1"]` |
| workers | `{"min": 0, "max": 0, "idleTimeout": 90}`（先設 0，要用才調大，見下面「每天怎麼用」） |
| timeout（執行逾時） | 5400（秒；formal 277 幀在 3090 上要 19 分鐘，A40 沒重量過，先給寬一點） |
| flashboot | `"FLASHBOOT"` |
| env | `R2_ENDPOINT`、`R2_BUCKET`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`（3.4 的值；用 `create-secret`＋`{{ RUNPOD_SECRET_xxx }}` 存，不要存明碼） |

拿到 endpoint id，填進 Mac 端 `scripts/設定.local.json` 的 `runpod.endpoint_id`（或環境變數 `RUNPOD_ENDPOINT_ID`）。

### 3.6 Mac 端環境變數

```bash
export RUNPOD_API_KEY=...         # RunPod console → Settings → API Keys
export R2_ACCESS_KEY_ID=...
export R2_SECRET_ACCESS_KEY=...
```

`RUNPOD_ENDPOINT_ID`、`R2_ENDPOINT`、`R2_BUCKET` 可以寫環境變數，也可以寫 `scripts/設定.local.json` 的 `runpod.*`
（這三個不是密鑰，寫設定檔比較不會每次開新終端機就要重設）：

```json
{
  "backend": "runpod",
  "runpod": {"endpoint_id": "...", "r2_endpoint": "https://<account>.r2.cloudflarestorage.com", "r2_bucket": "...", "rate_usd_per_hour": 0.79}
}
```

`comfy_dir` 這時不是 ComfyUI 的資料夾，是本機隨便一個空資料夾，裡面自己建 `input/`、`output/` 兩個子資料夾
（`stage_input`、SHA 比對、輸出複製都還是走這兩個資料夾，跟 backend=local 一樣）。

### 3.7 存一份 object_info 快取

`check_graph`、`開工檢查.py` 要知道 ComfyUI 有哪些節點、模型選單裡有什麼，但 RunPod 的 worker 不是常駐、不能
直接問。建一次快取（image 換版本、volume 模型換了都要重跑）：

```bash
python rp.py up 1       # 叫醒一個 worker
python rp.py object_info
python rp.py down       # 存完立刻關
```

### 3.8 跑一次開工檢查

```bash
python 開工檢查.py
```

## 4. 每天怎麼用

```bash
python rp.py up [n]     # workers.max 設成 n（預設 1）。開工前先跑
python 佇列.py wait      # 照舊：收完成、送接力（見 scripts/佇列.py）
python rp.py down       # 收工一定要跑：workers.max 設回 0，之後只收 volume 的錢
python rp.py health     # 隨時確認 endpoint 活著、worker 數多少
```

`rp.py up/down` 打的是 `https://api.runpod.io/v2`（控制 worker 數的那個 host），跟送件用的
`https://api.runpod.ai/v2/<endpoint id>` 是兩個不同的 host——如果 `up` 之後 worker 還是沒醒，
先用 `python rp.py health` 和 RunPod console 核對 workers.max 真的被設成你要的數字，不是打錯 host。

## 5. 成本規則

- **不用的時候 `workers.max` 一定要是 0。** 每次收工用 `python rp.py down`，然後 `python rp.py health` 核對。
- 固定支出只有 Network Volume 的存放費（GB／月）。R2 免費額度內用，lifecycle 規則清掉舊的 `jobs/` 檔案。
- GPU 只在 worker 真的在跑（`IN_PROGRESS`）才計費；冷啟動（下載模型、載入）也算在內，所以同一批工作盡量連著送，
  不要中間空等太久讓 worker 縮回去又要重新冷啟動。

## 6. 疑難排解

- **`python 開工檢查.py` 說 object_info 快取讀不到**：先 `python rp.py up`，再 `python rp.py object_info`。
- **冷啟動一直逾時**：worker 要從 volume 載入 ~40 GB 模型，第一次可能要數分鐘；`handler.py` 的
  `COMFY_API_FALLBACK_MAX_RETRIES` 給了 5 分鐘，不夠就調大，同時把 endpoint 的 `timeout`（執行逾時）也調大。
- **A40 比 3090 慢很多**：text encoder 是 nvfp4 量化檔，A40（Ampere）沒有原生 nvfp4，會被 dequant 成 bf16 跑；
  3090 也是 Ampere、也有這個情況，所以預期跟本機差不多，不會差到跑不動。真的差很多再考慮換機房／換 GPU 池
  （Blackwell 才有原生 nvfp4，但目前只在歐盟/英國以外幾乎找不到，先別換）。
- **`佇列.py wait` 卡住不動**：`python rp.py health` 看 worker 是不是卡在 `UNHEALTHY`／`THROTTLED`；
  也可能是 image 本身起不來，看 RunPod console 的 worker log。
- **送出的 H3 影片一直被 `GateBlocked` 擋**：這是正常的——關卡（量產關卡.json、preflight、permit）在這個 backend
  下完全沒有變，問題在劇本/分鏡那邊沒鎖定或對不上，不是 RunPod 的事（見 `../CLAUDE.md` 的「為什麼正式 H3 影片會被擋」）。
