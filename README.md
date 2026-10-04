# 仿真人短劇成片 Photoreal Short Drama Generator

在本機 ComfyUI（MiniMax H3）上，照五站把短劇創意或劇本做成仿真人短劇（寫實 3D 或實拍電影感）的 Claude Code skill。

[English](#english)

## 這是什麼

Claude 照這個 skill 一站一站做，每一站停下來給你看、等你核可：

1. **劇本**：簡報、故事、定稿台詞，拆成一個事件一鏡的事件表。
2. **分鏡**：視覺聖經、鏡頭表、分鏡卡（敘事目的、鏡頭、動作卡、首幀從哪裡來、每個切點的連戲交接）。
3. **提示詞**：從核可的分鏡卡編譯成 H3 提示詞，送件前一定過 lint。
4. **生成與驗片**：經佇列帳本送件；每條片段先量測、產出驗片包，你在聊天裡逐條回「檔名＋過」或「檔名＋改法」。
5. **結案**：旁白、分段配樂、粗剪、細剪、成片，每一版都另存無配樂母版。

skill 內附規則核心、提示詞 lint、鏡頭表檢查、開工檢查、佇列帳本、驗片量測，以及剪接和配樂組合的腳本。

## 需要什麼

- **系統和顯卡**：目前只在 Windows 11、NVIDIA RTX 3090（24 GB）上測過。單鏡最長 294 幀（768×1344）是在這張卡上量的，換顯卡要重量。
- **ComfyUI**：在 0.37.2 測過，要有內建的 MiniMax H3 節點。
- **Claude Code**。
- **自訂節點**
  - [ComfyUI-MiniMax-H3-PDD-Acc](https://github.com/Jalen-Brunson/ComfyUI-MiniMax-H3-PDD-Acc)：正式檔位的 8 步加速。沒有的話只能用 20 步的備用檔位（較慢，已知會在片中漏出角色卡）。
  - [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)：只有要用 GGUF 版 Qwen-Image 生角色卡、場景卡、首幀時才需要。
- **模型**（放進 ComfyUI 的 `models` 底下對應的資料夾）

| 用途 | 檔名 | 資料夾 | 來源 |
|---|---|---|---|
| 片段（必要） | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `diffusion_models` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) |
| 片段（必要） | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `text_encoders` | 同上 |
| 片段（必要） | `minimax_h3_video_vae_fp16.safetensors`、`minimax_h3_audio_vae_fp32.safetensors` | `vae` | 同上 |
| 正式檔位加速 | `MiniMax-H3-Ref2VA-Acc-8Step.safetensors` | `pdd_acc` | [alibaba-pai/MiniMax-H3-Acc-LoRAs](https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs) |
| 生音色檔、I2VA（選用） | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `diffusion_models` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) |
| 配樂（選用） | `acestep_v1.5_turbo.safetensors`；`qwen_0.6b_ace15.safetensors`、`qwen_1.7b_ace15.safetensors`；`ace_1.5_vae.safetensors` | `diffusion_models`；`text_encoders`；`vae` | [Comfy-Org/ace_step_1.5_ComfyUI_files](https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files) |
| 生角色卡、首幀（選用） | `qwen3vl_8b_int8_convrot.safetensors`；`qwen_image_2.1_vae_bf16.safetensors` | `text_encoders`；`vae` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) |
| 生角色卡、首幀（選用） | Qwen-Image 2.1 主模型的 GGUF 量化檔，檔名寫進設定的 `models.qwen_image` | `diffusion_models` | 自選 |

- **Python**：用 ComfyUI 自己的 Python 跑腳本（在 ComfyUI 內附的 Python 3.11.9 測過）。需要的套件列在 `requirements.txt`：numpy、av、Pillow 是 ComfyUI 本來就有的；opencv-python 只有 `驗片量測.py drift --roi` 要用，沒有的話照 `requirements.txt` 補裝。ffmpeg 用系統裝的；系統沒有的話，在 ComfyUI 的 Python 裡裝 imageio-ffmpeg，腳本會自動用它附的那一支（見安裝第 2 步）。

檔名和你的不同時，不用改程式，在 `設定.local.json` 的 `models` 改成你的檔名就好。

## 安裝

1. 放到 Claude Code 的 skill 資料夾：

   ```powershell
   git clone <這個 repo 的網址> "$env:USERPROFILE\.claude\skills\photoreal-short-drama-generator"
   ```

2. 在 `scripts\` 建 `設定.local.json`，寫這台機器的值（這個檔不會上傳）：

   ```json
   {
     "comfy_dir": "/path/to/ComfyUI",
     "ffmpeg": "/path/to/ffmpeg.exe",
     "python": "/path/to/ComfyUI/python.exe"
   }
   ```

   - `comfy_dir`：ComfyUI 的資料夾（裡面有 `input` 和 `output`）。
   - `ffmpeg`：在終端機打 `ffmpeg -version` 有反應，這一行可以刪掉。沒有的話，用下面那個 Python 執行 `python -m pip install imageio-ffmpeg`，這一行一樣可以刪掉，腳本會自動用它附的那一支；不然就寫 ffmpeg.exe 的完整路徑。
   - `python`：ComfyUI 自己的 Python。可攜版是 `ComfyUI_windows_portable\python_embeded\python.exe`；桌面版和手動安裝通常是 ComfyUI 資料夾裡的 `.venv\Scripts\python.exe`。
   - 路徑用 `/` 或 `\\` 分隔，不能只寫一個 `\`（JSON 的規定）。寫錯時，開工檢查會說是哪一行。

   選填：`projects_root`（專案放哪）、`lessons`／`series_state`（經驗庫、系列狀態檔）、`local_rules`（你自己的規則檔，優先於規則核心）、`official_guides`（官方提示詞指南）、`lint`（外部 lint）、`models`（逐項改檔名）、`max_frames`（換顯卡後重量的單鏡上限）。

3. 開著 ComfyUI，用設定裡的 Python 跑開工檢查，❌ 全部處理完：

   ```powershell
   /path/to/ComfyUI/python.exe scripts/開工檢查.py
   ```

   ✅ 正常；⚠️ 缺了只少一項功能（例如沒有配樂模型就不能生配樂）；❌ 缺了就不能出片。

## 怎麼用

在 Claude Code 裡說你要做的短劇（例：「用仿真人短劇做一支 60 秒、三個角色的感情戲」），或直接打 `/photoreal-short-drama-generator`。Claude 會先用一張收件卡問畫幅、長度、配樂、參考圖來源和畫風，再一站一站往下做。

- 所有生成（圖、影片、聲音、配樂）都要你同意才會送出。
- 審片在聊天裡逐條回：「檔名＋過」是核可，「檔名＋改法」是重做。重做照字面做，只改你指定的地方。

## 檔案

```text
SKILL.md / SKILL.cn.md     流程（英文／繁中）
requirements.txt           Python 套件（OpenCV 只有 drift --roi 要用）
references/                規則核心和五站說明
presets/                   畫風（寫實 3D、實拍電影感）和聲音模式預設
scripts/
  設定.json                預設設定（本機值寫在你自己建的 設定.local.json，不在 repo 裡）
  開工檢查.py              檢查 ComfyUI、節點、模型、規則檔
  comfy.py                 節點圖產生器（PDD 8 步正式、草稿、20 步備用）
  佇列.py                  佇列帳本：送件、追進度、首幀接力
  h3_prompt_lint.py        提示詞 lint
  檢查鏡頭表.py            鏡頭表檢查
  驗片量測.py              量測和驗片包
  剪接.py、配樂組合.py     剪接混音、配樂組合
```

## 規則從哪裡來

- 規則核心整理自一份工作室製作規範，和一個提示詞實驗室的對照實驗（一次一個變數、三個 seed），再經過兩個完整的測試製作驗證。標【本機值】的數字是在 RTX 3090 上量的。
- 只在一個完整製作裡驗證過、沒做三 seed 對照的寫法，另外標成「實戰驗證」。
- MiniMax 官方的 H3 提示詞指南（MiniMax-H3 的 h3-prompt-writing：`base-en.txt`、`ref-en.txt`）沒有附在這裡；有的話在設定的 `official_guides` 指過去，沒有的話照規則核心 §6 的格式摘要。

## 限制

- 只在 Windows、單張 RTX 3090、中文對白上測過。
- 不適用單張圖、單一片段、簡單剪輯、旁白主導或 2D 製作。
- 有些地方還是要人看：台詞字對不對、眨眼、表演自不自然。

## 維護

這個專案由一個人盡力維護（best-effort），不提供 SLA、固定的更新週期或個人技術支援。相容性只在本文件寫明的版本上測過，不保證和之後的 MiniMax H3、ComfyUI、自訂節點、GPU 設定或作業系統相容。

## MiniMax H3 的授權

這個 repo 是 MIT，但它要用的 MiniMax H3 模型不是。H3 適用 [MiniMax H3 Community License Agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)（2026-08-02 版），使用前請讀原文。跟短劇製作最相關的幾條（摘要，不是法律意見）：

- **地區**：歐盟、英國、韓國、美國是排除地區，這份授權不涵蓋；在那裡使用要先向 MiniMax [申請授權](https://platform.minimax.io/h3-license)。
- **商用**：用到 H3 的商用產品或服務，要在使用者介面上明顯標示「MiniMax H3」；商用產品和服務年營收超過 2,000 萬美元，要先取得 MiniMax 的書面授權。
- **公開發布**：在公開環境發布生成的內容，要清楚標明是機器生成的（可接受使用政策第 12 條）。
- **真人**：不能未經同意模仿他人（第 13 條）。
- **其他模型**：不能用 H3 和它的輸出改進其他 AI 模型。

Qwen-Image、ACE-Step、PDD 加速檔和自訂節點各有自己的授權。

## 免責聲明

這是第三方的 Claude Code skill，跟 MiniMax、ComfyUI、Anthropic 都沒有關係，也不代表它們的官方立場。MiniMax H3、ComfyUI、Claude 等名稱屬於各自的擁有者。生成的內容由使用者自己負責。

## 授權

MIT，見 [LICENSE](LICENSE)。事件表、從敘事目的推鏡頭、連戲檢查改寫自 DirectorSKILL（MIT），見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。模型和自訂節點不在這個 repo 裡，各依它們自己的授權。

---

<a id="english"></a>

## English

A Claude Code skill that turns a short-drama idea or script into a photoreal short (3D or live-action look) on a local ComfyUI with MiniMax H3, one station at a time.

### What it does

Claude works through five stations and stops at each one for your review:

1. **Script**: brief, story, final lines, and an event table with one event per shot.
2. **Storyboard**: visual bible, shot table and shot cards (narrative purpose, camera, action card, first-frame source, continuity handoff at each cut).
3. **Prompts**: H3 prompts compiled from the approved cards and linted before any render.
4. **Generate and review**: jobs go through a queue ledger; every clip is measured into a review packet, and you approve or redirect clips one by one in chat.
5. **Close**: narration, music cues, rough cut, fine cut and the final cut, with a no-music master every time.

The skill bundles a rules core, a prompt lint, a shot-table check, a setup check, the queue ledger, clip measurement tools, and edit and music-assembly scripts. The station guides and the rules core are written in Traditional Chinese; `SKILL.md` is in English.

### Requirements

- Tested only on Windows 11 with one NVIDIA RTX 3090 (24 GB). The 294-frame limit per clip at 768×1344 was measured on that card.
- ComfyUI 0.37.2 or later with the built-in MiniMax H3 nodes, and Claude Code.
- Custom nodes: [ComfyUI-MiniMax-H3-PDD-Acc](https://github.com/Jalen-Brunson/ComfyUI-MiniMax-H3-PDD-Acc) for the 8-step formal profile (without it, only the slower 20-step fallback works), and [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) only if you generate character sheets and keyframes with a GGUF Qwen-Image model.
- Models: see the table above. Required: the H3 ref2va model, its text encoder and the two VAEs from [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3); the PDD file from [alibaba-pai/MiniMax-H3-Acc-LoRAs](https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs) for the formal profile. ACE-Step 1.5 (music), Qwen-Image 2.1 (sheets and keyframes) and the H3 fl2va model (voice files, I2VA) are optional.
- Run the scripts with ComfyUI's own Python (tested with its bundled Python 3.11.9). numpy, av and Pillow from `requirements.txt` already come with ComfyUI; opencv-python is needed only for `驗片量測.py drift --roi`. For ffmpeg, use a system install, or install `imageio-ffmpeg` into ComfyUI's Python and the scripts use the ffmpeg it bundles.

### Install

1. Clone into Claude Code's skill folder: `git clone <repo URL> "$env:USERPROFILE\.claude\skills\photoreal-short-drama-generator"` (PowerShell).
2. Create `scripts\設定.local.json` with this machine's values (it is never uploaded), as in the example above. `comfy_dir` is the ComfyUI folder (it contains `input` and `output`). `python` is ComfyUI's own Python: `python_embeded\python.exe` in the portable build, usually `.venv\Scripts\python.exe` inside the ComfyUI folder for the desktop app and manual installs. `ffmpeg` can be left out when `ffmpeg -version` works in a terminal, or after running `python -m pip install imageio-ffmpeg` with ComfyUI's Python; otherwise give the full path to ffmpeg.exe. Use `/` or `\\` in paths; a single `\` is invalid JSON, and the setup check names the line if the file has an error. Model file names that differ from the defaults go under `models`.
3. With ComfyUI running, run `scripts\開工檢查.py` with that Python and fix every ❌. ⚠️ means one feature is unavailable; ❌ means clips cannot be made.

### Use

Describe the short you want in Claude Code, or type `/photoreal-short-drama-generator`. Every generation waits for your approval. Review in chat: "file + 過 (pass)" approves a clip; "file + change" asks for a redo, and a redo changes only what you named.

### Where the rules come from

The rules core is distilled from a studio's production rules and a prompt lab's controlled tests (one variable per test, three seeds), then checked in two end-to-end test productions. Numbers marked 【本機值】 were measured on an RTX 3090. Patterns verified only in one production are marked as such. MiniMax's official H3 prompt guides are not bundled; point `official_guides` to them if you have them.

### Limits

Tested only on Windows, one RTX 3090 and Chinese dialogue. Not for single images, single clips, simple edits, narration-led or 2D productions. Some checks still need a person: whether the spoken words are right, blinking, and how natural the acting is.

### Maintenance

This project is maintained on a best-effort basis.

Compatibility is tested against the versions documented by the project.
Compatibility with future versions of MiniMax H3, ComfyUI, custom nodes,
GPU configurations, or operating systems is not guaranteed.

### MiniMax H3 license

This repo is MIT-licensed; the MiniMax H3 model it uses is not. H3 is covered by the [MiniMax H3 Community License Agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) (license date 2026-08-02); read it before use. The points most relevant to short-drama production (a summary, not legal advice):

- **Territory:** the European Union, the United Kingdom, the Republic of Korea and the United States are excluded territories that the license does not cover; users there must [apply to MiniMax for a license](https://platform.minimax.io/h3-license).
- **Commercial use:** a commercial product or service that uses H3 must prominently display "MiniMax H3" in its user interface; commercial products and services with more than US$20 million in yearly revenue need MiniMax's prior written authorization.
- **Publishing:** content published in a public environment must be clearly and prominently disclosed as machine-generated (Acceptable Use Policy, item 12).
- **Real people:** do not impersonate another person without their consent (item 13).
- **Other models:** H3 and its outputs may not be used to improve other AI models.

Qwen-Image, ACE-Step, the PDD acceleration file and the custom nodes have their own licenses.

### Disclaimer

This is an unofficial, third-party Claude Code skill. It is not affiliated with or endorsed by MiniMax, ComfyUI or Anthropic. MiniMax H3, ComfyUI and Claude are names of their respective owners. Users are responsible for the content they generate.

### License

MIT, see [LICENSE](LICENSE). Parts of the event table, purpose-first shot design and continuity checks are adapted from DirectorSKILL (MIT); see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Models and custom nodes are not included and keep their own licenses.
