"""開工檢查：確認設定（設定.json＋設定.local.json）寫的路徑、ComfyUI、節點、模型檔、規則檔都在。每次開工、換電腦、改設定之後跑一次。
用法：python 開工檢查.py [專案資料夾]（不給就掃目前資料夾，前提是裡面有 工單.md）
✅ 正常；⚠️ 有退路（例：在地規則檔找不到，就只用 skill 內附的查表）；❌ 要先處理才能開工（結束碼 1）。
"""
import json, os, shutil, subprocess, sys, urllib.request
import comfy

sys.stdout.reconfigure(encoding="utf-8")
C, M = comfy.CFG, comfy.M
HERE = os.path.dirname(os.path.abspath(__file__))

# models 的每一項接在哪個載入節點的哪個輸入，用來核對模型檔有沒有在 ComfyUI 的選單裡
MODEL_SLOTS = {
    "h3_ref2va": ("UNETLoader", "unet_name"), "h3_fl2va": ("UNETLoader", "unet_name"), "ace": ("UNETLoader", "unet_name"),
    "pdd_ref2va": ("MiniMaxH3PDDAccApply", "pdd_file"),
    "qwen_image": ("UnetLoaderGGUF", "unet_name"),
    "h3_text_encoder": ("CLIPLoader", "clip_name"), "qwen_text_encoder": ("CLIPLoader", "clip_name"),
    "ace_text_encoder_1": ("DualCLIPLoader", "clip_name1"), "ace_text_encoder_2": ("DualCLIPLoader", "clip_name2"),
    "h3_video_vae": ("VAELoader", "vae_name"), "h3_audio_vae": ("VAELoader", "vae_name"),
    "qwen_vae": ("VAELoader", "vae_name"), "ace_vae": ("VAELoader", "vae_name"),
}
# 缺了只少一項功能、還能出片的（報 ⚠️，寫明少了什麼）；其他缺了就不能出片（報 ❌）
PDD_MISSING = "正式檔位（PDD 8 步）跑不了，只能用備用的 base20：較慢、已知會漏角色卡，先問使用者"
QWEN = "skill 不能自己生角色卡、場景卡、首幀，參考圖要自己準備"
ACE = "不能生配樂"
OPTIONAL = {"pdd_ref2va": PDD_MISSING, "MiniMaxH3PDDAccApply": PDD_MISSING,
            "qwen_image": QWEN, "qwen_text_encoder": QWEN, "qwen_vae": QWEN, "UnetLoaderGGUF": QWEN,
            "ace": ACE, "ace_text_encoder_1": ACE, "ace_text_encoder_2": ACE, "ace_vae": ACE,
            "h3_fl2va": "不能用 h3_voice 生音色檔（從核可的對白剪就不需要），也不能用 I2VA"}
COMFY_PY = "ComfyUI 可攜版是 python_embeded\\python.exe，桌面版和手動安裝通常是 ComfyUI 資料夾裡的 .venv\\Scripts\\python.exe"
n_bad = 0


def say(mark, msg):
    global n_bad
    n_bad += mark == "❌"
    print(mark, msg)


def options(spec):
    """下拉選單的選項：舊格式 [[選項…], {…}]，新格式 ["COMBO", {"options": [選項…]}]"""
    return spec[0] if isinstance(spec[0], list) else spec[1].get("options", [])


def check_file(path, what, missing):
    if not path:
        print("  ", f"{what}：沒有設定")
    elif os.path.exists(path):
        say("✅", f"{what}：{path}")
    else:
        say(missing, f"{what}找不到：{path}")


# ---- 本機程式 ----
if os.path.exists(os.path.join(HERE, "設定.local.json")):
    print("  ", "本機設定：設定.local.json（有寫到的項目覆寫 設定.json 的預設值）")
else:
    say("⚠️", "沒有 設定.local.json，全部用預設值：第一次在這台用，照 README 建一個，寫上這台的 comfy_dir 等路徑")
py = shutil.which(C["python"]) or C["python"]
if os.path.exists(py):
    say("✅", f"python：{py}")
else:
    say("❌", f"設定的 python 找不到：{py}（{COMFY_PY}）")
if os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(os.path.abspath(py)):
    say("⚠️", f"這次是用 {sys.executable} 跑的，不是設定裡的 python，下面的套件檢查只代表這一個")
missing = []
for mod, why in (("numpy", "驗片量測"), ("av", "驗片量測"), ("PIL", "首幀裁切")):
    try:
        __import__(mod)
    except ImportError:
        missing.append(f"{mod}（{why}要用）")
if missing:
    say("❌", f"缺 {'、'.join(missing)}：腳本要用 ComfyUI 自己的 Python 跑（{COMFY_PY}），並把它寫進 設定.local.json 的 python")
try:
    ver = subprocess.run([C["ffmpeg"], "-version"], capture_output=True, text=True).stdout.split("\n")[0]
    say("✅", f"ffmpeg：{C['ffmpeg']}（{ver[:40]}）")
except OSError:
    say("❌", f"ffmpeg 跑不起來：{C['ffmpeg']}。系統沒有 ffmpeg 的話，用 ComfyUI 的 Python 執行 python -m pip install imageio-ffmpeg"
              "（之後會自動用它附的那一支），或在 設定.local.json 的 ffmpeg 寫 ffmpeg.exe 的完整路徑")
if not C["comfy_dir"]:
    say("❌", "設定沒有 comfy_dir：在 設定.local.json 寫上 ComfyUI 的資料夾（送件要把參考圖放進它的 input、從 output 拿成品）")
else:
    for sub in ("input", "output"):
        check_file(os.path.join(C["comfy_dir"], sub), f"ComfyUI {sub} 資料夾", "❌")
if (C["max_frames"] - 5) % 17:
    say("❌", f"max_frames {C['max_frames']} 不在 17k+5 幀格上")
else:
    print("  ", f"單鏡最長 {C['max_frames']} 幀（{C['max_frames'] / 24:.2f} 秒）：正式解析度在這張顯卡量過的上限，換顯卡要重量")

# ---- ComfyUI、節點、模型 ----
try:
    st = json.load(urllib.request.urlopen(f"{comfy.BASE}/system_stats", timeout=10))
    dev = st["devices"][0]
    say("✅", f"ComfyUI {st['system'].get('comfyui_version', '')}｜{dev['name']}｜顯存 {dev['vram_total'] / 2**30:.0f} GB")
except OSError as e:
    say("❌", f"連不上 ComfyUI {comfy.BASE}（沒開？）：{e}")
else:
    graphs = [comfy.h3_ref("", ["a.png"], ["a.wav"], 1, "x", end_frame="b.png", guide0=True, **comfy.PROFILES["formal"]),
              comfy.h3_ref("", ["a.png"], [], 1, "x", **comfy.PROFILES["base20"]), comfy.h3_i2v("", "a.png", 1, "x"),
              comfy.h3_voice("", "", 1, "x"), comfy.h3_vo("", "a.wav", 1, "x"), comfy.qwen_t2i("", 1, "x"),
              comfy.qwen_edit("", ["a.png"], 1, "x"), comfy.ace("", "", 1, "x")]
    info = {}
    for ct in sorted({n["class_type"] for g in graphs for n in g.values()}):
        info.update(json.load(urllib.request.urlopen(f"{comfy.BASE}/object_info/{ct}", timeout=10)))
        if ct not in info:
            if ct in OPTIONAL:
                say("⚠️", f"ComfyUI 少了節點 {ct}：{OPTIONAL[ct]}")
            else:
                say("❌", f"ComfyUI 少了節點 {ct}（裝對應的自訂節點，或更新 ComfyUI）")
    for key, (ct, inp) in MODEL_SLOTS.items():
        if not M.get(key):
            say("⚠️" if key in OPTIONAL else "❌", f"設定的 models 沒有寫 {key}" + (f"：{OPTIONAL[key]}" if key in OPTIONAL else ""))
        elif ct in info:
            if M[key] in options(info[ct]["input"]["required"][inp]):
                say("✅", f"{key}：{M[key]}")
            elif key in OPTIONAL:
                say("⚠️", f"ComfyUI 找不到 {key} 的 {M[key]}：{OPTIONAL[key]}")
            else:
                say("❌", f"{key}：ComfyUI 的 {ct} 選單裡沒有 {M[key]}（模型檔放進對應資料夾，或在 設定.local.json 的 models 改檔名）")
    for key in sorted(set(M) - set(MODEL_SLOTS)):
        say("⚠️", f"models 的 {key} 沒有檢查（開工檢查.py 的 MODEL_SLOTS 補一行）")

# ---- 規則、lint、專案位置 ----
if not C["local_rules"]:
    print("  ", "沒有在地規則：只用 skill 內附的 references/查表-H3句型.md")
for p in C["local_rules"]:
    check_file(p, "在地規則", "⚠️")
for p in C["official_guides"]:
    check_file(p, "官方格式指南", "⚠️")
if not C["official_guides"]:
    print("  ", "沒有官方格式指南：格式照 查表-H3句型.md §6")
if C["lint"] and os.path.exists(C["lint"]):
    say("✅", f"lint 用設定的外部版本：{C['lint']}")
else:
    if C["lint"]:
        say("⚠️", f"設定的 lint 找不到：{C['lint']}，改用 skill 內附的")
    check_file(os.path.join(HERE, "h3_prompt_lint.py"), "lint（skill 內附）", "❌")
check_file(C["projects_root"], "專案資料夾的上層", "⚠️")
check_file(C["lessons"], "經驗庫", "⚠️")
check_file(C["series_state"], "系列狀態", "⚠️")

# ---- 專案資料夾：固定清單（SKILL.md）以外的檔案列出來 ----
PROJECT_ITEMS = {"0-原始資料", "1-劇本.md", "2-資產.md", "2-鏡頭表.csv", "2-分鏡.md", "2-分鏡圖", "資產",
                 "3-提示詞", "4-影片", "5-成片", "已核可", "工單.md", "_腳本"}
proj = sys.argv[1] if len(sys.argv) > 1 else (os.getcwd() if os.path.isfile(os.path.join(os.getcwd(), "工單.md")) else None)
if proj:
    extra = sorted(n for n in os.listdir(proj) if n not in PROJECT_ITEMS and not n.startswith("."))
    if extra:
        say("⚠️", f"專案 {proj} 有固定清單以外的檔案或資料夾：{'、'.join(extra)}（清單見 SKILL.md；多出來的要嘛刪、要嘛寫進待決）")
    else:
        say("✅", f"專案 {proj}：沒有清單以外的檔案")

print()
print("開工檢查：" + (f"{n_bad} 個 ❌，先處理再開工" if n_bad else "可以開工"))
sys.exit(1 if n_bad else 0)
