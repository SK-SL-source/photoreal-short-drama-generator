# 仿真人短劇成片 Photoreal Short Drama Generator

**Photorealistic AI Short Drama Generator — a local MiniMax H3 + Qwen-Image 2.1 ComfyUI workflow for script, assets, storyboard, prompts, video generation, QA and the final cut.**

**Claude Code Skill · MiniMax H3 · Qwen-Image 2.1 · ComfyUI · AI Filmmaking**

在本機 ComfyUI 上，照五站把短劇劇本做成仿真人短劇的 Claude Code skill。「仿真人」是理想化、精修過的乾淨高級棚拍感，不是紀錄寫實。畫風照你給的五張基準圖定死，卡片由母版編譯，每一鏡一張第 0 幀圖過檢查器，送件只掛你核可過的檔。

[English](#english)

## 這是什麼

Claude 照這個 skill 一站一站做；每一站做完整批交給你，你只回要改的項目，沒回視為過。

1. **劇本**：簡報、故事、定稿台詞、事件表，一次交。
2. **美術與資產**：照你的五張基準圖，用母版編譯角色板（人物審美＋服裝造型系統＋四排版型）和場景卡（空間、材質、色彩、光態），過醜衣／醜景 Gate 才生成；加場景關係表、音色；全部和基準圖並排成一張對照表給你看。
3. **分鏡與參考圖**：鏡頭表、分鏡卡、每鏡一張第 0 幀圖；每張圖的提示詞過 `圖像檢查.py`（開頭句、景別、參考圖張數與順序、左右站位），附八項審圖表。
4. **生成**：H3 提示詞從分鏡卡編譯、過 lint，整批排佇列；佇列送件前只查一條——掛的檔都在 `已核可/`；每條片段量測成驗片包。
5. **成片**：粗剪、細剪、成片一次交三版，每版另存無配樂母版。

每站只有一張選項卡：生成授權。其他都用文字。

## 需要什麼

- **系統和顯卡**：目前只在 Windows 11、NVIDIA RTX 3090（24 GB）上測過。單鏡最長 294 幀（768×1344）是在這張卡上量的，換顯卡要重量。
- **ComfyUI**：在 0.37.2 測過，要有內建的 MiniMax H3 節點。
- **Claude Code**。
- **自訂節點**
  - [ComfyUI-MiniMax-H3-PDD-Acc](https://github.com/Jalen-Brunson/ComfyUI-MiniMax-H3-PDD-Acc)：正式檔位的 8 步加速。沒有的話只能用 20 步的備用檔位（較慢，已知會在片中漏出角色卡）。
  - [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)：GGUF 版 Qwen-Image 2.1 生角色板、場景卡、第 0 幀圖要用。
- **模型**（放進 ComfyUI 的 `models` 底下對應的資料夾）

| 用途 | 檔名 | 資料夾 | 來源 |
|---|---|---|---|
| 片段（必要） | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `diffusion_models` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) |
| 片段（必要） | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `text_encoders` | 同上 |
| 片段（必要） | `minimax_h3_video_vae_fp16.safetensors`、`minimax_h3_audio_vae_fp32.safetensors` | `vae` | 同上 |
| 正式檔位加速 | `MiniMax-H3-Ref2VA-Acc-8Step.safetensors` | `pdd_acc` | [alibaba-pai/MiniMax-H3-Acc-LoRAs](https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs) |
| 角色板、場景卡、第 0 幀（必要） | Qwen-Image 2.1 主模型的 GGUF 量化檔（實測用 Q4_K_M；結果跟量化等級有關，換檔要重驗；檔名寫進設定的 `models.qwen_image`）；`qwen3vl_8b_int8_convrot.safetensors`；`qwen_image_2.1_vae_bf16.safetensors` | `diffusion_models`；`text_encoders`；`vae` | 自選；[Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) |
| 生音色檔（選用） | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `diffusion_models` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) |
| 配樂（選用） | `acestep_v1.5_turbo.safetensors`；`qwen_0.6b_ace15.safetensors`、`qwen_1.7b_ace15.safetensors`；`ace_1.5_vae.safetensors` | `diffusion_models`；`text_encoders`；`vae` | [Comfy-Org/ace_step_1.5](https://huggingface.co/Comfy-Org/ace_step_1.5) |

- **Python**：用 ComfyUI 自己的 Python 跑腳本（在 ComfyUI 內附的 Python 3.11.9 測過）。需要的套件列在 `requirements.txt`：numpy、av、Pillow 是 ComfyUI 本來就有的；opencv-python 只有 `驗片量測.py drift --roi` 要用。

檔名和你的不同時，不用改程式，在 `設定.local.json` 的 `models` 改成你的檔名就好。

## 安裝

1. 放到 Claude Code 的 skill 資料夾：

   ```powershell
   git clone https://github.com/SK-SL-source/photoreal-short-drama-generator.git "$env:USERPROFILE\.claude\skills\photoreal-short-drama-generator"
   ```

2. 在 `scripts\` 建 `設定.local.json`，寫這台機器的值（這個檔不會上傳）：

   ```json
   {
     "comfy_dir": "/path/to/ComfyUI",
     "ffmpeg": "/path/to/ffmpeg.exe",
     "python": "/path/to/ComfyUI/python.exe",
     "projects_root": "/path/to/projects"
   }
   ```

   - `comfy_dir`：ComfyUI 的資料夾（裡面有 `input` 和 `output`）。
   - `ffmpeg`：在終端機打 `ffmpeg -version` 有反應，這一行可以刪掉。沒有的話，用下面那個 Python 執行 `python -m pip install imageio-ffmpeg`，這一行一樣可以刪掉。
   - `python`：ComfyUI 自己的 Python。可攜版是 `ComfyUI_windows_portable\python_embeded\python.exe`；桌面版和手動安裝通常是 ComfyUI 資料夾裡的 `.venv\Scripts\python.exe`。
   - 路徑用 `/` 或 `\\` 分隔，不能只寫一個 `\`（JSON 的規定）。寫錯時，開工檢查會說是哪一行。

   選填：`lessons`／`series_state`（經驗庫、系列狀態檔）、`local_rules`（你自己的規則檔）、`official_guides`（MiniMax 官方提示詞指南）、`lint`（外部 lint）。

3. 開著 ComfyUI，用設定裡的 Python 跑開工檢查，❌ 全部處理完：

   ```powershell
   /path/to/ComfyUI/python.exe scripts/開工檢查.py
   ```

   ✅ 正常；⚠️ 缺了只少一項功能；❌ 缺了就不能出片。開工檢查後面接專案資料夾，會列出固定清單以外的檔案。

## 怎麼用

在 Claude Code 裡說你要做的短劇，或直接打 `/photoreal-short-drama-generator`，給劇本和五張基準圖（角色板、室內、街景各幾張）。

- **一站一關**：每一站 Claude 做完整批才交；你只回有問題的項目（「C02 臉太老」「S13 她要往左看」），沒回的視為過，過的檔移進專案的 `已核可/`。
- **生成授權**：每站只有一張選項卡，寫明幾張、幾條、預估時間；批內 Claude 自己跑，不每張問。
- **已核可資料夾**：裡面的東西 Claude 不改、不重生、不改名；送件只能掛裡面的檔；要改就你自己把檔移出來，改完重新過那一站。
- **重做照字面**：只改被點名的那一句或那一格，其他字和 seed 不動；重做前先貼舊句→新句。
- **待決**：Claude 想加規則、換做法、拿不準的，寫進工單的「待決」，每站交件時一起給你看，不自己做。

## 檔案

```text
SKILL.md                      怎麼做（≤100 行）：穩定性機制、五站、固定資料夾清單、核可資料夾、維護
references/1-劇本.md          第 1 站
references/2-資產.md          第 2 站：母版 spec、場景關係表、資產表
references/3-分鏡.md          第 3 站：鏡頭表、分鏡卡、分鏡圖範本、參考圖規則、八項審圖表
references/4-影片提示詞.md    第 4 站：H3 範本 A–D、景別詞表
references/4-生成.md          第 4 站：佇列、驗片、失敗處理
references/5-結案.md          第 5 站
references/查表-H3句型.md     H3 官方格式與驗證過的句型（寫影片提示詞時翻）
references/查表-圖像母版.md   角色板、場景卡母版的槽位、Gate、證據（寫 spec 時翻）
presets/畫風.md               畫風規範（照五張基準圖）
presets/audio/dialogue-led.md 聲音模式
scripts/                      開工檢查、qwen_t2i_母版、scene_t2i_母版、檢查鏡頭表、圖像檢查、h3_prompt_lint、佇列、驗片量測、剪接、配樂組合、comfy
```

## 限制

- 只在一張 RTX 3090 上測過；數字標【本機值】的換機器要重量。
- 分鏡圖用的風格句和 H3 風格句的色調字眼是這一版新寫的，在正式製作裡還沒跑過 3 seed；CHANGELOG 有標。
- Qwen 的小字（包裝、門牌）擋不住，靠審圖；基準圖不能掛進參考槽（會把板上的人帶進新圖）。
- 旁白主導和 2D 製作沒有涵蓋。

## 維護

SKILL.md ≤100 行、每站文件 ≤150 行、查表不限；新規則要有檢查器在查或失敗樣本證明，實驗標準 3 個 seed 全過；經驗庫有進有出。每次改 skill 在 CHANGELOG 寫一行。

## MiniMax H3 的授權

這個 repo 是 MIT，但它要用的 MiniMax H3 模型不是。H3 適用 [MiniMax H3 Community License Agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)（2026-08-02 版），使用前請讀原文。

- **地區**：歐盟、英國、韓國、美國是排除地區，這份授權不涵蓋；在那裡使用要先向 MiniMax [申請授權](https://platform.minimax.io/h3-license)。
- **商用**：用到 H3 的商用產品或服務，要在使用者介面上明顯標示「MiniMax H3」；商用產品和服務年營收超過 2,000 萬美元，要先取得 MiniMax 的書面授權。
- **公開發布**：在公開環境發布生成的內容，要清楚標明是機器生成的（可接受使用政策第 12 條）。
- **真人**：不能未經同意模仿他人（第 13 條）。
- **其他模型**：不能用 H3 和它的輸出改進其他 AI 模型。

Qwen-Image、ACE-Step、PDD 加速檔和自訂節點各有自己的授權。

## 免責聲明

這是第三方的 Claude Code skill，跟 MiniMax、ComfyUI、Anthropic 都沒有關係，也不代表它們的官方立場。MiniMax H3、ComfyUI、Claude 等名稱屬於各自的擁有者。生成的內容由使用者自行負責。

## 授權

MIT，見 [LICENSE](LICENSE)。事件表、從敘事目的推鏡頭、連戲檢查改寫自 DirectorSKILL（MIT），見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。模型和自訂節點不在這個 repo 裡，各有自己的授權。

---

<a id="english"></a>
## English

### What it does

A Claude Code skill that turns a short-drama script into a photoreal episode on local ComfyUI (MiniMax H3 + Qwen-Image 2.1), one station at a time. "Photoreal" here means an idealized, beauty-retouched, clean commercial-catalog look (a digital human), not documentary realism. Each station is delivered as a single batch; you reply only to the items that need changes, and silence means approved.

1. **Script**: brief, story, final lines and the event table.
2. **Art and assets**: character boards (beauty, a wardrobe system, a four-row layout) and scene cards (space, materials, palette, light state) compiled from fixed templates against your five reference images, gated before generation; plus scene relation tables and voices, shown beside the references in one comparison sheet.
3. **Storyboard**: shot table, shot cards and one first-frame image per shot; every image prompt passes `圖像檢查.py` (opening sentence, shot size, reference count and order, left/right blocking) and comes with an eight-item review table.
4. **Generation**: H3 prompts compiled from the cards and linted, queued in one batch; the queue checks one thing before dispatch — every attached file is in `已核可/`; every clip is measured into a review packet.
5. **Final cut**: rough cut, fine cut and final delivered together, each with a no-music master.

Each station has exactly one choice card: the generation authorization. Everything else is text.

### Requirements

- Tested only on Windows 11 with one NVIDIA RTX 3090 (24 GB). The 294-frame limit per clip at 768×1344 was measured on that card.
- ComfyUI 0.37.2 or later with the built-in MiniMax H3 nodes, and Claude Code.
- Custom nodes: [ComfyUI-MiniMax-H3-PDD-Acc](https://github.com/Jalen-Brunson/ComfyUI-MiniMax-H3-PDD-Acc) for the 8-step formal profile, and [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) for the GGUF Qwen-Image 2.1 model used for boards, scene cards and first frames.
- Models: see the table above. Required: the H3 ref2va model, its text encoder and the two VAEs; the PDD file; a GGUF Qwen-Image 2.1 model with its text encoder and VAE. Optional: the fl2va model for voice assets and the ACE-Step files for music.
- Run the scripts with ComfyUI's own Python (tested with its bundled Python 3.11.9).

### Install

1. Clone into Claude Code's skill folder: `git clone https://github.com/SK-SL-source/photoreal-short-drama-generator.git "$env:USERPROFILE\.claude\skills\photoreal-short-drama-generator"` (PowerShell).
2. Create `scripts\設定.local.json` with this machine's values (never uploaded), as in the example above: `comfy_dir`, `ffmpeg`, `python`, `projects_root`.
3. With ComfyUI running, run `scripts\開工檢查.py` with that Python and fix every ❌.

### Use

Describe the short in Claude Code or type `/photoreal-short-drama-generator`, and provide the script and five reference images. One gate per station; reply only to what needs changing; approved files move into the project's `已核可/` folder, which Claude never edits, regenerates or renames, and generation only references files inside it. Rework changes only the sentence or field you named, with the seed kept.

### Limits

Measured on one RTX 3090 only. The storyboard-image style sentence and the colour wording of the H3 style sentence are new in this version and not yet validated over 3 seeds in a production run (see CHANGELOG). Qwen still draws small pseudo-text on packaging and door plates; reference images must not be attached as Qwen references (they pull their person into the new image). Narration-led and 2D productions are not covered.

### MiniMax H3 license

This repo is MIT-licensed; the MiniMax H3 model it uses is not. H3 is covered by the [MiniMax H3 Community License Agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) (license date 2026-08-02); read the original before use.

- **Territory:** the European Union, the United Kingdom, the Republic of Korea and the United States are excluded territories that the license does not cover; users there must [apply to MiniMax for a license](https://platform.minimax.io/h3-license).
- **Commercial use:** a commercial product or service that uses H3 must prominently display "MiniMax H3" in its user interface; commercial products and services with more than US$20 million in yearly revenue need MiniMax's written permission.
- **Publishing:** content published in a public environment must be clearly and prominently disclosed as machine-generated (Acceptable Use Policy, item 12).
- **Real people:** do not impersonate another person without their consent (item 13).
- **Other models:** H3 and its outputs may not be used to improve other AI models.

Qwen-Image, ACE-Step, the PDD acceleration file and the custom nodes have their own licenses.

### Disclaimer

This is an unofficial, third-party Claude Code skill. It is not affiliated with or endorsed by MiniMax, ComfyUI or Anthropic. MiniMax H3, ComfyUI and Claude are names of their respective owners. Users are responsible for the content they generate.

### License

MIT, see [LICENSE](LICENSE). Parts of the event table, purpose-first shot design and continuity checks are adapted from DirectorSKILL (MIT); see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Models and custom nodes are not part of this repo and have their own licenses.
