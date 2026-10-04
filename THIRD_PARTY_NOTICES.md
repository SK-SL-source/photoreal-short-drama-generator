# 第三方來源與授權

## DirectorSKILL（cinematic-director v2.1.0）

- 來源：https://github.com/wuwangzhang1216/DirectorSKILL
- 本 skill 改寫、翻譯了其中的做法，沒有逐字複製：
  - `references/1-劇本.md`：事件表的欄位（開始與結束狀態、誰引起、一列只放一件「多懂了什麼」）。
  - `references/2-分鏡.md`：從敘事目的推出鏡頭、先定站位再擺機位、軸線、30° 規則、視線與移動方向、紅燈三問、焦段意圖與景深推理、直式畫面的縱深調度。
  - `references/4-生成與驗片.md`：失敗時多了「剪接補」「刪鏡」兩條路。
  - `references/5-結案.md`：曲目單的「不要什麼」。
- 它的提示詞範本、工具轉接、導演風格、分數門檻都沒有採用（和 MiniMax H3 官方格式、本機實測衝突）。
- 授權全文照錄如下：

```text
MIT License

Copyright (c) 2026 wangzhang-wu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 沒有收錄、只是會用到的

這個 repo 只放 skill 本身。下面這些要使用者自己安裝，各依它們自己的授權：

- MiniMax H3 模型（Comfy-Org/MiniMax-H3）：適用 MiniMax H3 Community License Agreement（不是 MIT），有地區、商用標示等限制，摘要見 README 的「MiniMax H3 的授權」。
- Qwen-Image 2.1、ACE-Step 1.5。
- ComfyUI-MiniMax-H3-PDD-Acc 節點，和 alibaba-pai 的 PDD 加速檔（MiniMax-H3-Acc-LoRAs）。
- ComfyUI-GGUF。
- MiniMax 官方的 H3 提示詞指南（`base-en.txt`、`ref-en.txt`）：沒有附原文，`references/規則核心.md` §6 和各站說明都用自己的話寫。只有模型要求一字不差的格式用語照官方逐字使用：I2VA／FL2VA／L2VA 的開頭對齊句；標籤的定義句型（`<Audio N> is the voice-timbre reference for …`、`<Picture N> is the first frame of [Shot 1]`）；retention 行的格式（`<Subject N> (appears in [Shot 1])` 和 fully_preserved 等標記）；指定要用的 `says in an off-screen voiceover`；以及欄位名、標籤、任務類型、運鏡名稱（例：Static Shot）。
- 寫實人物皮膚的用詞 `natural matte skin, normal skin tones, realistic detail` 取自 MiniMax 官方 video-deconstruct skill。
