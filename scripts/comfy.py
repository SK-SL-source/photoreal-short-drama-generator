"""本機 ComfyUI 送件小工具（仿真人短劇 skill 共用）。
一次送一個圖、等它跑完、把輸出複製回專案資料夾、記一筆生成紀錄。
用法：scripts 資料夾整個複製到專案的 _腳本\\，專案自己的送件腳本 import comfy；PROJ 自動取 _腳本 的上一層。
設定：同一個資料夾的 設定.json 是 skill 附的預設；這台機器的路徑寫在 設定.local.json，有寫到的項目覆寫預設（models 逐項覆寫）。
ffmpeg 沒有設定、系統也找不到時，改用這個 Python 環境裡 imageio-ffmpeg 附的那一支。
其他腳本都從這裡拿設定（from comfy import CFG）。用設定裡的 python 跑。
"""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request, urllib.error
try:
    import imageio_ffmpeg   # 選用：系統沒有 ffmpeg 時用它附的那一支
except ImportError:
    imageio_ffmpeg = None

sys.stdout.reconfigure(encoding="utf-8")   # 輸出被其他程式接走時（管線），中文訊息才不會變亂碼
sys.stderr.reconfigure(encoding="utf-8")


def read_json(path):
    """讀 JSON 設定檔；找不到或寫錯時，白話說明是哪一行、怎麼修，然後結束（不丟 traceback）"""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        msg = f"找不到設定檔：{path}"
    except json.JSONDecodeError as e:
        hint = ("Windows 路徑要寫成 C:/ComfyUI 或 C:\\\\ComfyUI，不能只有一個反斜線" if "escape" in e.msg
                else "常見原因：最後一項後面多了逗號，或少了引號、逗號、括號")
        msg = f"{os.path.basename(path)} 第 {e.lineno} 行第 {e.colno} 個字寫錯了（{e.msg}）。{hint}"
    print("❌ " + msg)
    sys.exit(1)


def need_file(path, what):
    """檔案或資料夾不存在時，白話說是哪一個，然後結束"""
    if not os.path.exists(path):
        print(f"❌ 找不到{what}：{path}")
        sys.exit(1)


HERE = os.path.dirname(os.path.abspath(__file__))
CFG = read_json(os.path.join(HERE, "設定.json"))
if os.path.exists(os.path.join(HERE, "設定.local.json")):
    _local = read_json(os.path.join(HERE, "設定.local.json"))
    CFG["models"].update(_local.pop("models", {}))
    CFG.update(_local)
if CFG["ffmpeg"] == "ffmpeg" and not shutil.which("ffmpeg") and imageio_ffmpeg:
    try:
        CFG["ffmpeg"] = imageio_ffmpeg.get_ffmpeg_exe()
    except RuntimeError:   # 這個平台沒有附 ffmpeg：維持原設定，開工檢查會報 ❌ 並說明怎麼補
        pass
M = CFG["models"]
BASE = CFG["comfy_url"]
OUT = os.path.join(CFG["comfy_dir"], "output")
INP = os.path.join(CFG["comfy_dir"], "input")
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # _腳本 的上一層＝專案資料夾
LOG = os.path.join(PROJ, "_腳本", "生成紀錄.jsonl")

# 參考模式的檔位，用法 h3_ref(…, **PROFILES["formal"])。同一個 seed 換檔位，畫面會不一樣。
# formal＝出片：PDD 8 步加速。base20＝PDD 不能用時才用：基礎模型 20 步，已知會在片中硬切成白底角色卡，要量亮度跳動。
# draft＝只篩動作和構圖：PDD 8 步、416×736。I2VA 一律照官方範本用 simple。
PROFILES = {
    "formal": {"w": 768, "h": 1344, "pdd": 8, "ref_image_size": "match"},
    "base20": {"w": 768, "h": 1344, "steps": 20, "scheduler": "beta", "ref_image_size": "match"},
    "draft":  {"w": 416, "h": 736, "pdd": 8, "ref_image_size": "match"},
}


def open_chrome_once(url=BASE):
    """只開一個 ComfyUI 分頁：Chrome 在跑且標記檔存在就不再開（分頁太多會吃記憶體）。只支援 Windows＋Chrome"""
    marker = os.path.join(tempfile.gettempdir(), "h3_comfy_tab_opened")
    try:
        running = "chrome.exe" in subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                                                 capture_output=True, text=True).stdout
    except Exception:
        running = False
    if running and os.path.exists(marker):
        return
    subprocess.Popen(["cmd", "/c", "start", "chrome", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    open(marker, "w").write("opened")


def _post(path, obj):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=60).read()


def free():
    """換模型家族前清顯存（佇列空的時候才呼叫）"""
    _post("/free", {"unload_models": True, "free_memory": True})


def log(rec):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run(job_id, graph, dests, timeout=3600, meta=None):
    """送一個圖並等完成；輸出依順序複製到 dests。回傳 (複製後路徑, 錯誤, 秒數)"""
    try:
        resp = json.loads(_post("/prompt", {"prompt": graph, "client_id": "short-drama"}))
    except urllib.error.HTTPError as e:
        err = f"submit HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:1500]}"
        log({"job": job_id, "error": err, **(meta or {})}); return [], err, 0
    if resp.get("node_errors"):
        err = "node_errors: " + json.dumps(resp["node_errors"], ensure_ascii=False)[:1500]
        log({"job": job_id, "error": err, **(meta or {})}); return [], err, 0
    pid = resp["prompt_id"]
    print(f"[{job_id}] 已送出 {pid}", flush=True)
    return wait_for(job_id, pid, dests, timeout, meta)


def outputs(entry):
    """/history 一筆紀錄裡存到 output 的檔案（照節點輸出順序）"""
    files = []
    for o in entry.get("outputs", {}).values():
        for lst in o.values():
            if isinstance(lst, list):
                for it in lst:
                    if isinstance(it, dict) and "filename" in it and it.get("type", "output") == "output":
                        files.append(os.path.join(OUT, it.get("subfolder", ""), it["filename"]))
    return files


def wait_for(job_id, pid, dests, timeout=3600, meta=None):
    """等一個已經在 ComfyUI 佇列裡的工作（用 prompt_id）跑完，輸出依順序複製到 dests"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            h = json.load(urllib.request.urlopen(f"{BASE}/history/{pid}", timeout=15))
        except Exception:
            h = {}
        if pid in h:
            el = round(time.time() - t0)
            st = h[pid].get("status", {})
            if st.get("status_str") != "success":
                err = "gen error: " + json.dumps(st.get("messages", []), ensure_ascii=False)[-1500:]
                log({"job": job_id, "prompt_id": pid, "error": err, "sec": el, **(meta or {})}); return [], err, el
            files = outputs(h[pid])
            got = []
            for src, dst in zip(files, dests):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst); got.append(dst)
            log({"job": job_id, "prompt_id": pid, "sec": el, "src": files, "files": got, **(meta or {})})
            return got, (None if got else "no output"), el
        time.sleep(5)
    log({"job": job_id, "prompt_id": pid, "error": "timeout", **(meta or {})})
    return [], "timeout", round(time.time() - t0)


# ---------- 圖 ----------

def qwen_t2i(prompt, seed, prefix, w=1920, h=1088, steps=25):
    """Qwen-Image 2.1 文字生圖（照官方範本：euler、simple、cfg 1；主模型用 GGUF 檔，ComfyUI-GGUF 載入）"""
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": M["qwen_image"]}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": M["qwen_text_encoder"],
                                                     "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": M["qwen_vae"]}},
        "4": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["2", 0], "prompt": prompt,
                                                                "negative_prompt": "", "resolution": 1024}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": seed, "steps": steps, "cfg": 1.0,
                                                   "sampler_name": "euler", "scheduler": "simple",
                                                   "positive": ["4", 0], "negative": ["4", 1],
                                                   "latent_image": ["5", 0], "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": prefix}},
    }


def stage_input(src, name, sub="drama"):
    """把檔案複製進 ComfyUI input（ComfyUI 只讀那裡），回傳 LoadImage／LoadAudio 用的名稱"""
    dst_dir = os.path.join(INP, sub)
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copy2(src, os.path.join(dst_dir, name))
    return f"{sub}/{name}"


def qwen_edit(prompt, image_names, seed, prefix, size=None, resolution=0, steps=25):
    """Qwen-Image 2.1 編輯模式（照官方範本，主模型用 GGUF 檔）。image_names 依序＝<image1>、<image2>…；
    size=None 時輸出跟著 image_1；size=(w,h) 時等於範本的 custom_size 開啟。"""
    g = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": M["qwen_image"]}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": M["qwen_text_encoder"],
                                                     "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": M["qwen_vae"]}},
        "9": {"class_type": "QwenImage21Cache", "inputs": {"model": ["1", 0], "device": "auto", "dtype": "default"}},
        "4": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt,
                                                                "negative_prompt": "", "resolution": resolution}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["9", 0], "seed": seed, "steps": steps, "cfg": 1.0,
                                                   "sampler_name": "euler", "scheduler": "simple",
                                                   "positive": ["4", 0], "negative": ["4", 1],
                                                   "latent_image": ["4", 2], "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": prefix}},
    }
    for k, name in enumerate(image_names):
        g[str(100 + k)] = {"class_type": "LoadImage", "inputs": {"image": name}}
        g["4"]["inputs"][f"images.image_{k + 1}"] = [str(100 + k), 0]
    if size:
        g["5"] = {"class_type": "EmptyLatentImage", "inputs": {"width": size[0], "height": size[1], "batch_size": 1}}
        g["6"]["inputs"]["latent_image"] = ["5", 0]
    return g


# ---------- H3 片段（參考模式）----------

def h3_ref(prompt, image_names, audio_names, seed, prefix, frames=158, w=768, h=1344, steps=20, end_frame=None,
           guide0=False, guides=(), scheduler="beta", ref_image_size="match", pdd=None):
    """image_names 依序接 ref_image_0..（首幀一定是第一張＝<Picture 1>）；audio_names 依序接 ref_audio_0..（<Audio 1>…）
    guide0：True＝把第一張（首幀）用 MiniMaxH3AddGuide 釘在第 0 幀。參考圖本身不在時間軸上，不釘的話第 0 幀只是像首幀；有首幀就釘。釘是軟約束：首幀和提示詞的景別、內容衝突時會被蓋掉（第 0 幀差 50 以上）
    end_frame：尾幀圖名，接在最後一個 ref_image，再釘在最後一格（frame_idx −1）。首尾都釘靜止畫面，片子幾乎不會動
    guides：[(圖名, frame_idx)] 片中關鍵幀；圖名必須也在 image_names 裡，文字才有 <Picture N> 可以指（官方多幀範本的接法）
    pdd：4／6／8＝PDD 加速步數（SigmaShift 12／3、euler、用加速節點給的 sigmas）；None＝基礎模型照 steps、scheduler 跑"""
    if end_frame:
        image_names = list(image_names) + [end_frame]
    for name, idx in guides:
        assert name in image_names, f"片中引導圖 {name} 也要接進 ref_images"
        assert -frames <= idx < frames, f"引導幀 {idx} 超出片長 {frames}"
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": M["h3_ref2va"],
                                                     "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": M["h3_text_encoder"], "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": M["h3_video_vae"]}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": M["h3_audio_vae"]}},
        "7": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0], "prompt": prompt,
            "width": w, "height": h, "length": frames, "ref_image_size": ref_image_size}},
        "8": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["7", 0]}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "10": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": scheduler, "steps": steps,
                                                          "denoise": 1.0}},
        "11": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "12": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["11", 0], "guider": ["8", 0],
                                                                 "sampler": ["9", 0], "sigmas": ["10", 0],
                                                                 "latent_image": ["7", 1]}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
        "14": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "15": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "audio": ["14", 0], "fps": 24.0,
                                                       "bit_depth": "auto", "color_space": "sRGB", "codec": "none"}},
        "16": {"class_type": "SaveVideo", "inputs": {"video": ["15", 0], "filename_prefix": prefix, "format": "mp4",
                                                     "format.codec": "h264", "format.codec.encoding": "re-encode",
                                                     "format.codec.encoding.crf": 14.0, "codec": "auto"}},
    }
    if pdd:
        # 加速檔只有 4／6／8 步（7 步要另給 partition，沒測過）；取樣器只能 euler、sigmas 只能用它給的，否則出雜訊
        assert pdd in (4, 6, 8), f"PDD 只能 4／6／8 步，不能 {pdd} 步"
        g["5"] = {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["1", 0], "shift_video": 12.0, "shift_audio": 3.0}}
        g["6"] = {"class_type": "MiniMaxH3PDDAccApply", "inputs": {"model": ["5", 0], "pdd_file": M["pdd_ref2va"], "nfe": str(pdd),
                                                                  "lora_strength": 1.0, "head_strength": 1.0, "on_off_grid": "error"}}
        g["8"]["inputs"]["model"] = ["6", 0]
        g["9"]["inputs"]["sampler_name"] = "euler"
        g["12"]["inputs"]["sigmas"] = ["6", 1]
        del g["10"]
    for k, name in enumerate(image_names):
        g[str(100 + k)] = {"class_type": "LoadImage", "inputs": {"image": name}}
        g["7"]["inputs"][f"ref_images.ref_image_{k}"] = [str(100 + k), 0]
    for k, name in enumerate(audio_names):
        g[str(200 + k)] = {"class_type": "LoadAudio", "inputs": {"audio": name}}
        g["7"]["inputs"][f"ref_audios.ref_audio_{k}"] = [str(200 + k), 0]
    chain = []  # (LoadImage 節點, frame_idx)，依序串成 AddGuide
    if guide0:
        chain.append(("100", 0))
    for name, idx in guides:
        chain.append((str(100 + list(image_names).index(name)), idx))
    if end_frame:
        chain.append((str(100 + len(image_names) - 1), -1))
    _chain_guides(g, chain, cond=["7", 0], latent=["7", 1], vae="3", audio_vae="4", guider="8")
    return g


def _chain_guides(g, chain, cond, latent, vae, audio_vae, guider):
    """把 MiniMaxH3AddGuide 一個接一個串在條件上，最後接進 BasicGuider。節點編號 40 起。"""
    for k, (img, idx) in enumerate(chain):
        nid = str(40 + k)
        g[nid] = {"class_type": "MiniMaxH3AddGuide", "inputs": {"positive": cond, "latent": latent, "frame_idx": idx,
                                                                "vae": [vae, 0], "audio_vae": [audio_vae, 0],
                                                                "image": [img, 0]}}
        cond = [nid, 0]
    g[guider]["inputs"]["conditioning"] = cond


def h3_i2v(prompt, first_frame, seed, prefix, frames=124, w=768, h=1344, steps=20, last_frame=None, guides=(),
           scheduler="simple"):
    """I2VA／FL2VA：照官方 video_minimax_h3_i2v 範本（不開 turbo LoRA）——fl2va 權重、MiniMaxH3ImageToVideo、
    res_multistep、simple 20 步。first_frame 在節點裡被直接拉伸成畫布、釘在第 0 幀（原始碼確認）→ 圖要先用 fit_canvas
    裁成剛好 w×h；last_frame 等比裁切、釘在最後一格。這個節點接不了角色卡、場景卡、音色檔。
    guides：[(圖名, frame_idx)] 片中引導，只當時間錨（文字編碼器看不到它）。"""
    for name, idx in guides:
        assert -frames <= idx < frames, f"引導幀 {idx} 超出片長 {frames}"
    g = {
        "20": {"class_type": "UNETLoader", "inputs": {"unet_name": M["h3_fl2va"],
                                                      "weight_dtype": "default"}},
        "21": {"class_type": "CLIPLoader", "inputs": {"clip_name": M["h3_text_encoder"], "type": "minimax", "device": "default"}},
        "22": {"class_type": "VAELoader", "inputs": {"vae_name": M["h3_video_vae"]}},
        "23": {"class_type": "VAELoader", "inputs": {"vae_name": M["h3_audio_vae"]}},
        "24": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["21", 0], "vae": ["22", 0], "prompt": prompt,
                                                                 "width": w, "height": h, "length": frames}},
        "25": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "26": {"class_type": "BasicGuider", "inputs": {"model": ["20", 0], "conditioning": ["24", 0]}},
        "27": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "28": {"class_type": "BasicScheduler", "inputs": {"model": ["20", 0], "scheduler": scheduler, "steps": steps,
                                                          "denoise": 1.0}},
        "29": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["25", 0], "guider": ["26", 0],
                                                                 "sampler": ["27", 0], "sigmas": ["28", 0],
                                                                 "latent_image": ["24", 1]}},
        "31": {"class_type": "VAEDecode", "inputs": {"samples": ["29", 0], "vae": ["22", 0]}},
        "32": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["29", 0], "vae": ["23", 0]}},
        "34": {"class_type": "CreateVideo", "inputs": {"images": ["31", 0], "audio": ["32", 0], "fps": 24.0,
                                                       "bit_depth": "auto", "color_space": "sRGB", "codec": "none"}},
        "35": {"class_type": "SaveVideo", "inputs": {"video": ["34", 0], "filename_prefix": prefix, "format": "mp4",
                                                     "format.codec": "h264", "format.codec.encoding": "re-encode",
                                                     "format.codec.encoding.crf": 14.0, "codec": "auto"}},
        "100": {"class_type": "LoadImage", "inputs": {"image": first_frame}},
    }
    g["24"]["inputs"]["first_frame"] = ["100", 0]
    if last_frame:
        g["101"] = {"class_type": "LoadImage", "inputs": {"image": last_frame}}
        g["24"]["inputs"]["last_frame"] = ["101", 0]
    chain = []
    for k, (name, idx) in enumerate(guides):
        g[str(110 + k)] = {"class_type": "LoadImage", "inputs": {"image": name}}
        chain.append((str(110 + k), idx))
    if chain:
        _chain_guides(g, chain, cond=["24", 0], latent=["24", 1], vae="22", audio_vae="23", guider="26")
    return g


def fit_canvas(src, dst, w, h):
    """等比放大到蓋滿 w×h 再置中裁切，存成剛好 w×h。I2VA 的 first_frame 會被直接拉伸（不保比例），所以先裁好。"""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    s = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    left, top = (im.width - w) // 2, (im.height - h) // 2
    im.crop((left, top, left + w, top + h)).save(dst)
    return dst


def check_graph(g):
    """不送件，只檢查節點圖：連線指到存在的節點；ComfyUI 開著時，再照 /object_info 核對每個輸入名稱和必填輸入。
    回傳問題清單（空＝沒問題）。"""
    problems, n_out = [], {}
    for nid, node in g.items():
        for key, v in node["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                if v[0] not in g:
                    problems.append(f"#{nid} {node['class_type']}.{key} 指到不存在的節點 #{v[0]}")
    for ct in sorted({n["class_type"] for n in g.values()}):
        try:
            info = json.load(urllib.request.urlopen(f"{BASE}/object_info/{ct}", timeout=10))[ct]
        except Exception as e:
            problems.append(f"查不到 {ct} 的定義（ComfyUI 沒開？）：{e}")
            continue
        req, opt = info["input"].get("required", {}), info["input"].get("optional", {})
        n_out[ct] = len(info.get("output", []))
        for nid, node in g.items():
            if node["class_type"] != ct:
                continue
            have = {k.split(".")[0] for k in node["inputs"]}
            for k in have - set(req) - set(opt):
                problems.append(f"#{nid} {ct} 沒有輸入「{k}」")
            for k in set(req) - have:
                spec = req[k]
                if (isinstance(spec, list) and len(spec) > 1 and spec[0] == "COMFY_AUTOGROW_V3"
                        and spec[1].get("template", {}).get("min", 1) == 0):
                    continue   # 可以一個都不接的自動增長輸入（例：Qwen 文字生圖不接參考圖）
                problems.append(f"#{nid} {ct} 缺必填輸入「{k}」")
    for nid, node in g.items():
        for key, v in node["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] in g:
                src_ct = g[v[0]]["class_type"]
                if src_ct in n_out and v[1] >= n_out[src_ct]:
                    problems.append(f"#{nid}.{key} 接到 #{v[0]} {src_ct} 的第 {v[1]} 個輸出，但它只有 {n_out[src_ct]} 個")
    return problems


# ---------- 音色 ----------

VOICE_TMPL = (
    "integrated_multimodal_description: [Shot 1] Realistic live-action footage in vertical 9:16 portrait framing, "
    "as one single continuous shot. A static close shot of one person sitting alone in a small quiet room, facing "
    "the camera, the head and shoulders filling the picture. Within the first half second the mouth opens on the "
    "first syllable. @VOICE@ (S1), says: <d>[Chinese] @LINE@</d> Exactly as the voice stops, the lips close and the "
    "jaw ceases all speaking motion. The jaw and lips move naturally with every syllable, and the picture holds on "
    "the speaker through the end of the video.\n\n"
    "overall_soundscape: A quiet, dry indoor room tone continues evenly throughout, with one soft breath just "
    "before the line.\n\n"
    "non_diegetic_music: N/A"
)


def h3_voice(voice_desc, line, seed, prefix):
    """還沒有核可的對白可以剪音色時才用：fl2va、416×736、158 幀、simple 20 步，只解音訊（開頭不切，第一個字才不會被切掉）"""
    prompt = VOICE_TMPL.replace("@VOICE@", voice_desc).replace("@LINE@", line)
    return {
        "20": {"class_type": "UNETLoader", "inputs": {"unet_name": M["h3_fl2va"],
                                                      "weight_dtype": "default"}},
        "21": {"class_type": "CLIPLoader", "inputs": {"clip_name": M["h3_text_encoder"], "type": "minimax", "device": "default"}},
        "22": {"class_type": "VAELoader", "inputs": {"vae_name": M["h3_video_vae"]}},
        "23": {"class_type": "VAELoader", "inputs": {"vae_name": M["h3_audio_vae"]}},
        "24": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["21", 0], "vae": ["22", 0], "prompt": prompt,
                                                                 "width": 416, "height": 736, "length": 158}},
        "25": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "26": {"class_type": "BasicGuider", "inputs": {"model": ["20", 0], "conditioning": ["24", 0]}},
        "27": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "28": {"class_type": "BasicScheduler", "inputs": {"model": ["20", 0], "scheduler": "simple", "steps": 20,
                                                          "denoise": 1.0}},
        "29": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["25", 0], "guider": ["26", 0],
                                                                 "sampler": ["27", 0], "sigmas": ["28", 0],
                                                                 "latent_image": ["24", 1]}},
        "32": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["29", 0], "vae": ["23", 0]}},
        "30": {"class_type": "SaveAudioAdvanced", "inputs": {"audio": ["32", 0], "filename_prefix": prefix,
                                                             "format": "flac"}},
    }


def h3_vo(prompt, voice, seed, prefix, frames=124):
    """旁白／心聲的音訊 pass：參考模式、416×736、只解音訊。voice＝音色檔（<Audio 1>）。
    開頭不切：起音常常就貼在檔頭，切掉 0.25 秒會切到第一個字；前面多出的空白在剪接時處理"""
    g = h3_ref(prompt, [], [voice], seed, prefix, frames=frames, w=416, h=736)
    for k in ("13", "15", "16"):
        g.pop(k)
    g["18"] = {"class_type": "SaveAudioAdvanced", "inputs": {"audio": ["14", 0], "filename_prefix": prefix, "format": "flac"}}
    return g


# ---------- 配樂 ----------

def ace(tags, lyrics, seed, prefix, seconds=30.0, bpm=100, key="D minor"):
    """ACE-Step 1.5 turbo；BPM、調性用參數，不寫進 tags；兩個節點的秒數一致。
    seconds 抓「需要的長度 ÷ 0.75」：實際有聲的長度只有設定的 76–96%。解碼會超過滿格，存檔前先降 6 dB"""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": M["ace"], "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": M["ace_text_encoder_1"],
                                                         "clip_name2": M["ace_text_encoder_2"], "type": "ace",
                                                         "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": M["ace_vae"]}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "5": {"class_type": "TextEncodeAceStepAudio1.5", "inputs": {
            "clip": ["2", 0], "tags": tags, "lyrics": lyrics, "seed": seed, "bpm": bpm, "duration": seconds,
            "timesignature": "4", "language": "en", "keyscale": key, "generate_audio_codes": True,
            "cfg_scale": 2.0, "temperature": 0.85, "top_p": 0.9, "top_k": 0, "min_p": 0.0}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {"seconds": seconds, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "seed": seed, "steps": 8, "cfg": 1.0,
                                                   "sampler_name": "euler", "scheduler": "simple",
                                                   "positive": ["5", 0], "negative": ["6", 0],
                                                   "latent_image": ["7", 0], "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "11": {"class_type": "AudioAdjustVolume", "inputs": {"audio": ["9", 0], "volume": -6}},
        "10": {"class_type": "SaveAudioAdvanced", "inputs": {"audio": ["11", 0], "filename_prefix": prefix, "format": "flac"}},
    }
