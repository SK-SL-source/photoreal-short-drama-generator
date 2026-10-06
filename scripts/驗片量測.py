"""驗片量測：把「用眼睛看」的驗片項目變成可以重跑、可以比較的數字。
只讀素材、不碰 ComfyUI、不改任何素材；圖和試看片預設存到影片旁邊的「驗片」資料夾，可用 --out 改。
多數子指令可以一次給多個檔案（也可以給資料夾），加 --json 改成輸出 JSON。門檻的由來寫在門檻旁邊的註解。
用設定裡的 python 跑（放在專案的 _腳本\\ 裡）。

用法（一行一個例子，在專案資料夾裡跑）：
  python _腳本/驗片量測.py pack 4-影片/S03_r1_s1001.mp4 --first 資產/S03_首幀.png --prev 4-影片/S02_r2_s1001.mp4   （驗片包：下面全部一次跑）
  python _腳本/驗片量測.py frame0 4-影片/S03_r1_s1001.mp4 資產/S03_首幀.png [資產/S03_尾幀.png]   （第二張圖＝尾幀）
  python _腳本/驗片量測.py cut 4-影片                       （片中硬切、淡入白底角色卡）
  python _腳本/驗片量測.py drift 4-影片/S03_r1_s1001.mp4 [--roi x,y,寬,高]
  python _腳本/驗片量測.py dup 4-影片
  python _腳本/驗片量測.py speech 4-影片/S03_r1_s1001.mp4    （台詞時間軸）
  python _腳本/驗片量測.py motion 4-影片/S03_r1_s1001.mp4    （左中右各在什麼時候動）
  python _腳本/驗片量測.py strip 4-影片/S03_r1_s1001.mp4 1.0 1.5 --span 0.5
  python _腳本/驗片量測.py sheet 4-影片/S03_r1_s1001.mp4 --every 0.5
  python _腳本/驗片量測.py zoom 4-影片/S03_r1_s1001.mp4 300,200,200,200 2.0 3.5   （放大臉，每 3 格一張）
  python _腳本/驗片量測.py join 4-影片/S02.mp4 4-影片/S03.mp4 4-影片/S04.mp4   （接點試看片）
  python _腳本/驗片量測.py av 5-成片/粗剪_v1.mp4               （畫面和聲音一樣長嗎）
  python _腳本/驗片量測.py loud 4-影片
  python _腳本/驗片量測.py tail 5-成片/配樂.flac --want 20
"""
import argparse, json, math, os, re, subprocess, sys
import numpy as np
from comfy import CFG, need_file

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
FF = CFG["ffmpeg"]
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".avi")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
AUDIO_EXT = (".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg")

# ---- 門檻（改之前先在有已知答案的測試片上重驗）----
DRIFT_THR = 20.0     # 自動模式「鏡頭有動」門檻（原尺寸像素）：基準固定鏡最大 9.5（人物動作造成），有動的兩條最小 122（在測試片上量的）
ROI_THR = 5.0        # 方塊模式門檻：框住不動的背景時，基準固定鏡量到最多 1.1
SPEED_THR = 0.5      # 「移動時段」：平滑後每幀移動超過 0.5 像素（= 每秒 12 像素）
STILL, MOVING = 0.6, 2.0   # 重複幀：相鄰兩幀灰階平均差 <0.6 幾乎沒變、>2.0 在動
SILENCE_DB = -50.0   # 音訊「有聲」：100 毫秒視窗 RMS 高於 −50 dBFS
VOICE_DB = -35.0     # 台詞時間軸「有聲」：20 毫秒視窗 RMS 高於 −35 dBFS（人聲用 −35 比較準；−50 會把環境聲也算進來）
CUT_THR = 45.0       # 片中硬切：縮成 64×112 灰階後相鄰兩格的平均差。正常片 129 條最大 35（快速運鏡），切到角色卡或別的機位 57–131
BRIGHT_THR = 50.0    # 淡入白底角色卡：亮度偏離第 0 格多少。正常片最大 32（運鏡帶到亮窗），漏卡 57–140


# ============================================================ 共用小工具

def expand(items, exts):
    """把資料夾展開成裡面符合副檔名的檔案（照檔名排序）；檔案原樣保留"""
    out = []
    for it in items:
        if os.path.isdir(it):
            out += [os.path.join(it, f) for f in sorted(os.listdir(it)) if f.lower().endswith(exts)]
        else:
            out.append(it)
    return out


def name(p):
    return os.path.basename(p)


def out_dir_for(video, out):
    d = out or os.path.join(os.path.dirname(os.path.abspath(video)), "驗片")
    os.makedirs(d, exist_ok=True)
    return d


def video_fps(path):
    import av
    with av.open(path) as c:
        s = c.streams.video[0]
        return float(s.average_rate) if s.average_rate else 24.0


def iter_frames(path, fmt="rgb24"):
    """一幀一幀解碼（不一次全部放進記憶體）"""
    import av
    with av.open(path) as c:
        for f in c.decode(c.streams.video[0]):
            yield f.to_ndarray(format=fmt)


def pick_frames(path, wanted, fmt="rgb24"):
    """只留下指定幀號（負數＝從尾巴數）；回傳 {幀號: 畫面}、總幀數"""
    keep, n = {}, 0
    want_pos = {w for w in wanted if w >= 0}
    buf = []   # 為了負數幀號，保留最後 30 幀
    for i, f in enumerate(iter_frames(path, fmt)):
        n = i + 1
        if i in want_pos:
            keep[i] = f
        buf.append((i, f))
        if len(buf) > 30:
            buf.pop(0)
    for w in wanted:
        if w < 0:
            j = n + w
            for i, f in buf:
                if i == j:
                    keep[w] = f
    return keep, n


def font(size):
    """中文字型（微軟正黑體）；找不到就用內建字型（只能顯示英數）"""
    from PIL import ImageFont
    for f in ("msjh.ttc", "msjhbd.ttc", "mingliu.ttc", "msyh.ttc"):
        p = os.path.join(os.environ.get("WINDIR", ""), "Fonts", f)
        if os.path.exists(p):
            return ImageFont.truetype(p, size), True
    return ImageFont.load_default(), False


def emit(args, text_lines, data):
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        print("\n".join(text_lines))


# ============================================================ 1. frame0：第 0 幀和首幀圖差多少

def load_ref(img_path, w, h, mode):
    """首幀圖讀成和影片一樣大小的 RGB。
    mode="stretch"：照 H3 I2VA 節點的首幀做法——不保比例直接拉伸（Lanczos，ComfyUI 也是用 PIL 的 Lanczos）。
    mode="cover"：照尾幀／AddGuide 的做法——先從中間裁成影片比例再縮放。透明通道直接丟掉（和 LoadImage 一樣）。"""
    from PIL import Image
    im = Image.open(img_path).convert("RGB")
    if mode == "cover":
        W, H = im.size
        old, new = W / H, w / h
        x = y = 0
        if old > new:
            x = round((W - W * (new / old)) / 2)
        elif old < new:
            y = round((H - H * (old / new)) / 2)
        im = im.crop((x, y, W - x, H - y))
    if im.size != (w, h):
        im = im.resize((w, h), Image.Resampling.LANCZOS)
    return np.asarray(im, dtype=np.float64)


def luma(rgb):
    """灰階（BT.601，和影片的亮度訊號一樣）"""
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def _gauss_filter(img, sigma=1.5, truncate=3.5):
    """高斯模糊（11×11，σ=1.5，邊緣鏡射）——只用 numpy，拆成橫、直兩次一維卷積"""
    r = int(truncate * sigma + 0.5)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    p = np.pad(img, r, mode="symmetric")
    tmp = sum(k[i] * p[:, i:i + img.shape[1]] for i in range(2 * r + 1))
    return sum(k[i] * tmp[i:i + img.shape[0], :] for i in range(2 * r + 1))


def ssim(a, b, L=255.0):
    """SSIM（結構相似度，1＝完全一樣）：灰階、高斯視窗 σ=1.5（11×11）、K1=0.01、K2=0.03，
    外圈 5 像素不計——和 Wang 2004 原論文、skimage 的 gaussian_weights 版本同一套算法"""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    ma, mb = _gauss_filter(a), _gauss_filter(b)
    saa = _gauss_filter(a * a) - ma * ma
    sbb = _gauss_filter(b * b) - mb * mb
    sab = _gauss_filter(a * b) - ma * mb
    c1, c2 = (0.01 * L) ** 2, (0.03 * L) ** 2
    s = ((2 * ma * mb + c1) * (2 * sab + c2)) / ((ma * ma + mb * mb + c1) * (saa + sbb + c2))
    return float(s[5:-5, 5:-5].mean())


def compare(ref, frame):
    """平均絕對差（0–255，RGB 三色平均）、PSNR（dB，越高越像）、SSIM、差超過 32 的面積比例"""
    frame = frame.astype(np.float64)
    diff = np.abs(ref - frame)
    mse = float((diff ** 2).mean())
    return {"平均差": round(float(diff.mean()), 2),
            "PSNR": round(10 * math.log10(255.0 ** 2 / mse), 2) if mse > 0 else float("inf"),
            "SSIM": round(ssim(luma(ref), luma(frame)), 4),
            "大差異面積%": round(float((diff.mean(axis=2) > 32).mean() * 100), 1)}


_INFERNO = [(0, 0, 4), (87, 16, 110), (188, 55, 84), (249, 142, 9), (252, 255, 164)]


def heat(diff_gray, top=64.0):
    """差異熱圖：黑＝一樣，紫→紅→黃白＝差越多（差 64 以上就是最亮）"""
    t = np.clip(diff_gray / top, 0, 1) * (len(_INFERNO) - 1)
    i = np.minimum(t.astype(int), len(_INFERNO) - 2)
    f = (t - i)[..., None]
    lut = np.array(_INFERNO, dtype=np.float64)
    return (lut[i] * (1 - f) + lut[i + 1] * f).astype(np.uint8)


def save_triptych(path, ref, frame, title_left, title_mid, metrics, scale=0.5):
    from PIL import Image, ImageDraw
    diff = np.abs(ref - frame.astype(np.float64)).mean(axis=2)
    panels = [ref.astype(np.uint8), frame.astype(np.uint8), heat(diff)]
    h, w = ref.shape[:2]
    pw, ph = int(w * scale), int(h * scale)
    head = 64
    canvas = Image.new("RGB", (pw * 3 + 8, ph + head), (20, 20, 20))
    for k, p in enumerate(panels):
        canvas.paste(Image.fromarray(p).resize((pw, ph), Image.Resampling.BILINEAR), (k * (pw + 4), head))
    d = ImageDraw.Draw(canvas)
    f, zh = font(18)
    f2, _ = font(15)
    titles = [title_left, title_mid, "差異熱圖（黑＝一樣，亮＝差 64 以上）"] if zh else ["keyframe", "video frame", "diff (black=same)"]
    for k, t in enumerate(titles):
        d.text((k * (pw + 4) + 6, 6), t, fill=(255, 220, 90), font=f)
    m = metrics
    d.text((2 * (pw + 4) + 6, 34), f"平均差 {m['平均差']}｜PSNR {m['PSNR']}｜SSIM {m['SSIM']}" if zh else
           f"MAD {m['平均差']} PSNR {m['PSNR']} SSIM {m['SSIM']}", fill=(230, 230, 230), font=f2)
    canvas.save(path)


def frame0_one(video, first_img, end_img=None, out=None, png=True):
    fps = video_fps(video)
    idx_first = [0, 6, 12, 24]
    idx_last = [-1, -7, -13, -25] if end_img else []
    frames, n = pick_frames(video, idx_first + idx_last)
    h, w = frames[0].shape[:2]
    res = {"影片": video, "總幀數": n, "fps": fps}
    ref = load_ref(first_img, w, h, "stretch")
    res["首幀圖"] = first_img
    res["首幀比對"] = {str(i): compare(ref, frames[i]) for i in idx_first if i in frames}
    if png:
        p = os.path.join(out_dir_for(video, out), os.path.splitext(name(video))[0] + "_第0幀比對.png")
        save_triptych(p, ref, frames[0], "首幀圖（拉伸到影片大小）", "影片第 0 幀", res["首幀比對"]["0"])
        res["並排圖"] = p
    if end_img:
        ref2 = load_ref(end_img, w, h, "cover")
        res["尾幀圖"] = end_img
        res["尾幀比對"] = {str(n + i): compare(ref2, frames[i]) for i in idx_last if i in frames}
        if png:
            p2 = os.path.join(out_dir_for(video, out), os.path.splitext(name(video))[0] + "_尾幀比對.png")
            save_triptych(p2, ref2, frames[-1], "尾幀圖（置中裁切後縮放）", f"影片最後一幀（第 {n - 1} 幀）",
                          res["尾幀比對"][str(n - 1)])
            res["尾幀並排圖"] = p2
    return res


def cmd_frame0(args):
    groups, cur = [], None
    for it in args.items:
        low = it.lower()
        if low.endswith(VIDEO_EXT):
            cur = [it, None, None]
            groups.append(cur)
        elif low.endswith(IMAGE_EXT):
            if cur is None:
                sys.exit(f"先給影片再給首幀圖：{it}")
            if cur[1] is None:
                cur[1] = it
            elif cur[2] is None:
                cur[2] = it
            else:
                sys.exit(f"一條影片最多兩張圖（首幀、尾幀）：{it}")
        else:
            sys.exit(f"看不懂的檔案類型：{it}")
    lines, data = [], []
    for v, a, b in groups:
        if a is None:
            sys.exit(f"{name(v)} 沒有給首幀圖")
        r = frame0_one(v, a, b, args.out, png=not args.no_png)
        data.append(r)
        lines.append(f"{name(v)} ↔ 首幀 {name(a)}（首幀圖直接拉伸到影片大小再比）")
        for i, m in r["首幀比對"].items():
            lines.append(f"  第 {i:>3} 幀（{int(i) / r['fps']:.2f} 秒）：平均差 {m['平均差']:6.2f}｜PSNR {m['PSNR']:6.2f} dB｜"
                         f"SSIM {m['SSIM']:.4f}｜差超過 32 的面積 {m['大差異面積%']}%")
        if b:
            lines.append(f"  ↔ 尾幀 {name(b)}（置中裁切後縮放，和 AddGuide 一樣）")
            for i, m in r["尾幀比對"].items():
                lines.append(f"  第 {i:>3} 幀（{int(i) / r['fps']:.2f} 秒）：平均差 {m['平均差']:6.2f}｜PSNR {m['PSNR']:6.2f} dB｜"
                             f"SSIM {m['SSIM']:.4f}｜差超過 32 的面積 {m['大差異面積%']}%")
        for k in ("並排圖", "尾幀並排圖"):
            if k in r:
                lines.append(f"  {k}：{r[k]}")
    lines.append("（平均差 0＝完全一樣、255＝全反；PSNR 越高越像，30 dB 以上肉眼很難分；SSIM 1＝結構完全一樣。這幾個數字沒有及格線，拿來和基準比。）")
    emit(args, lines, data)


# ============================================================ 2. drift：鏡頭有沒有自己移動

BLOCK, STRIDE, DOWN = 64, 32, 2          # 畫面先縮成一半，切成 64×64 的小塊（原尺寸 128×128），每隔 32 取一塊
RESP_MIN, TEX_MIN, MIN_BLOCKS = 0.08, 2.0, 10


def _hann(n):
    w = np.hanning(n)
    return np.outer(w, w)


def block_fft(blocks, win):
    """小塊先扣掉平均、乘上漢寧窗（避免邊緣效應），再做 FFT"""
    return np.fft.fft2((blocks - blocks.mean(axis=(1, 2), keepdims=True)) * win)


def phase_corr(FA, FB):
    """一次算很多小塊的相位相關（輸入是 block_fft 的結果）。
    回傳每塊的位移 (dx, dy)（B 相對 A，往右／往下為正）和可信度（相關峰值 0–1，越高越可信）"""
    n = FA.shape[-1]
    R = FB * np.conj(FA)
    R /= np.abs(R) + 1e-9
    r = np.fft.ifft2(R).real
    N = r.shape[0]
    flat = r.reshape(N, -1)
    idx = flat.argmax(axis=1)
    py, px = np.unravel_index(idx, (n, n))
    ar = np.arange(N)

    def sub(l, c, rr):
        """小數點位移：用峰值和較大那一側鄰居的比例（Foroosh 2002 的相位相關公式）。
        不用拋物線內插——實測拋物線會把 0.1 像素量成 0.05（往整數靠），慢慢累積的小漂移會被低估一半"""
        l, rr = np.maximum(l, 0), np.maximum(rr, 0)
        right = rr / np.maximum(rr + c, 1e-12)
        left = -l / np.maximum(l + c, 1e-12)
        return np.clip(np.where(rr >= l, right, left), -0.5, 0.5)
    oy = sub(r[ar, (py - 1) % n, px], r[ar, py, px], r[ar, (py + 1) % n, px])
    ox = sub(r[ar, py, (px - 1) % n], r[ar, py, px], r[ar, py, (px + 1) % n])
    dy = np.where(py > n // 2, py - n, py) + oy
    dx = np.where(px > n // 2, px - n, px) + ox
    return np.stack([dx, dy], axis=1), flat[ar, idx]


def _half(g):
    g = g.astype(np.float32)
    h, w = g.shape
    g = g[: h // DOWN * DOWN, : w // DOWN * DOWN]
    return g.reshape(h // DOWN, DOWN, w // DOWN, DOWN).mean(axis=(1, 3))


REF_LIM = 8.0    # 半解析度像素：和參考幀差超過這麼多（平移，或推拉讓四角移動）就換參考幀
REF_RESP = 0.15  # 和參考幀比的相關峰值中位數低於這個＝參考幀已經對不上（畫面變太多），改用和前一幀比的結果


def _motion(s, resp, tex_ref, cen, rad, far):
    """由各小塊位移估這一步的 平移 t（半解析度像素）、推拉 k（0.01＝放大 1%）；回傳 t, k, 有效塊數, 峰值中位數"""
    ok = (resp > RESP_MIN) & (tex_ref > TEX_MIN)       # 太平（沒紋理）或對不上的小塊不算
    t, k, m = np.zeros(2), 0.0, ok & far
    if ok.sum() >= MIN_BLOCKS:
        t = np.median(s[ok], axis=0)
        for _ in range(3):   # 平移和推拉輪流估三次：推拉時四周的小塊往外跑、方向各不同，先扣掉才不會拖歪平移
            if m.sum() >= MIN_BLOCKS:
                sr = s[m] - t
                k = float(np.median((sr[:, 0] * cen[m, 0] + sr[:, 1] * cen[m, 1]) / rad[m] ** 2))
            t = np.median(s[ok] - k * cen[ok], axis=0)
    return t, k, int(ok.sum()), float(np.median(resp))


def track_auto(gray_frames):
    """自動模式：畫面切成小塊，每塊用相位相關算位移。
    平移＝所有小塊位移的「中位數」（人物只佔少數小塊，會被中位數排除）；
    推拉（縮放）＝扣掉平移後，離畫面中心較遠的小塊「往外／往內」那個分量的中位數（推近時四周的東西會往外跑）。
    每一幀都和「參考幀」比（一開始＝第 0 幀）：固定鏡時等於一直直接和第 0 幀比，誤差不會一幀一幀越加越多；
    和參考幀差超過 REF_LIM、或已經對不上（峰值中位數 < REF_RESP，例如換景、溶接）時，改用和前一幀比的結果接下去，並把參考幀換成這一幀。
    回傳 平移 (幀數, 2)（原尺寸像素，相對第 0 幀）、縮放倍率 (幀數,)、每幀有效塊數、塊總數、半對角線長（原尺寸像素）、換參考幀次數"""
    win = _hann(BLOCK).astype(np.float32)
    pos, scale, used = [np.zeros(2)], [1.0], []
    ref = prev = grid = None
    half_diag, n_ref = 0.0, 0
    for g in gray_frames:
        g = _half(g)
        if grid is None:
            H, W = g.shape
            grid = [(y, x) for y in range(0, H - BLOCK + 1, STRIDE) for x in range(0, W - BLOCK + 1, STRIDE)]
            cen = np.array([(x + BLOCK / 2 - W / 2, y + BLOCK / 2 - H / 2) for y, x in grid])   # 小塊中心（相對畫面中心）
            rad = np.hypot(cen[:, 0], cen[:, 1])
            far = rad > 0.35 * math.hypot(W / 2, H / 2)     # 太靠中心的小塊算縮放不準，不用
            half_diag = math.hypot(W / 2, H / 2) * DOWN
        cur = np.stack([g[y:y + BLOCK, x:x + BLOCK] for y, x in grid])
        f_cur, tex = block_fft(cur, win), cur.std(axis=(1, 2))
        if ref is None:
            ref = (f_cur, tex, np.zeros(2), 1.0)          # 參考幀：FFT、紋理、相對第 0 幀的平移（半解析度）、縮放
            prev = ref
            continue
        f_ref, tex_ref, r_pos, r_scale = ref
        t, k, n_ok, med = _motion(*phase_corr(f_ref, f_cur), tex_ref, cen, rad, far)
        if n_ok >= MIN_BLOCKS and med >= REF_RESP:        # 參考幀還對得上：直接用
            c_pos, c_scale = (1 + k) * r_pos + t, (1 + k) * r_scale
            renew = math.hypot(*t) > REF_LIM or abs(k) * rad.max() > REF_LIM
        else:                                             # 對不上了：用和前一幀比的結果接下去
            f_p, tex_p, p_pos, p_scale = prev
            t, k, n_ok, _ = _motion(*phase_corr(f_p, f_cur), tex_p, cen, rad, far)
            c_pos, c_scale = (1 + k) * p_pos + t, (1 + k) * p_scale
            renew = True
        pos.append(c_pos * DOWN)
        scale.append(c_scale)
        used.append(n_ok)
        prev = (f_cur, tex, c_pos, c_scale)
        if renew:
            ref = prev
            n_ref += 1
    return np.array(pos), np.array(scale), used, len(grid or []), half_diag, n_ref


def track_roi(gray_frames, roi):
    """方塊模式：第 0 幀的那個方塊當模板，之後每一幀在上一幀找到的位置附近找最像的地方（正規化相關），
    位移＝找到的位置 − 原位置（直接相對第 0 幀，不累加）。分數 1＝一模一樣，低於 0.6 表示被擋住或長得不一樣了"""
    import cv2
    x, y, w, h = roi
    tpl = None
    cx, cy = float(x), float(y)
    margin = max(40, max(w, h) // 2)
    pos, score = [], []
    for g in gray_frames:
        g = g.astype(np.float32)
        H, W = g.shape
        if tpl is None:
            if x < 0 or y < 0 or x + w > W or y + h > H:
                sys.exit(f"--roi 超出畫面（畫面 {W}×{H}）")
            tpl = g[y:y + h, x:x + w].copy()
        x0 = int(max(0, round(cx) - margin)); y0 = int(max(0, round(cy) - margin))
        x1 = int(min(W, round(cx) + w + margin)); y1 = int(min(H, round(cy) + h + margin))
        res = cv2.matchTemplate(g[y0:y1, x0:x1], tpl, cv2.TM_CCOEFF_NORMED)
        _, best, _, (bx, by) = cv2.minMaxLoc(res)
        ox = oy = 0.0
        if 0 < bx < res.shape[1] - 1:
            l, c, r = res[by, bx - 1], res[by, bx], res[by, bx + 1]
            den = l - 2 * c + r
            ox = float(np.clip(0.5 * (l - r) / den, -0.5, 0.5)) if abs(den) > 1e-12 else 0.0
        if 0 < by < res.shape[0] - 1:
            l, c, r = res[by - 1, bx], res[by, bx], res[by + 1, bx]
            den = l - 2 * c + r
            oy = float(np.clip(0.5 * (l - r) / den, -0.5, 0.5)) if abs(den) > 1e-12 else 0.0
        cx, cy = x0 + bx + ox, y0 + by + oy
        pos.append((cx - x, cy - y))
        score.append(float(best))
    return np.array(pos), score


def moving_spans(step, fps, thr=SPEED_THR, min_path=5.0):
    """找出「鏡頭在動」的時段。step＝每一步（第 k 幀→第 k+1 幀）畫面移動了幾像素。
    先做 5 幀平均，超過門檻的連續段就是在動
    （中間只斷 2 幀以內的合併；短於 3 幀、或整段累計移動不到 5 像素的丟掉——那通常是人物晃動）"""
    step = np.asarray(step, dtype=np.float64)
    if not len(step):
        return []
    sm = np.convolve(step, np.ones(5) / 5, mode="same")
    on = sm > thr
    spans, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j + 1 < len(on) and on[j + 1]:
                j += 1
            spans.append([i, j])
            i = j + 1
        else:
            i += 1
    merged = []
    for s in spans:
        if merged and s[0] - merged[-1][1] <= 3:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    # 第 k 步＝第 k 幀到第 k+1 幀
    return [(round(a / fps, 2), round((b + 1) / fps, 2)) for a, b in merged
            if b - a + 1 >= 3 and step[a:b + 1].sum() >= min_path]


def drift_frames(gray_frames, fps, roi=None, thr=None):
    """drift 的核心（輸入一串灰階畫面，假影片測試也走這裡）。
    判定：平移最大值 或 推拉造成的四角位移最大值，任一個超過門檻就是「鏡頭有動」。門檻不給就用預設（自動 20、方塊 5）"""
    thr = thr if thr is not None else (ROI_THR if roi else DRIFT_THR)
    if roi:
        pos, score = track_roi(gray_frames, roi)
        zoom_px = np.zeros(len(pos))
        scale = np.ones(len(pos))
        used = None
    else:
        pos, scale, used, nblk, half_diag, n_ref = track_auto(gray_frames)
        zoom_px = np.abs(scale - 1) * half_diag    # 推拉讓畫面四個角跑了幾像素
        score = None
    mag = np.hypot(pos[:, 0], pos[:, 1])
    k, kz = int(mag.argmax()), int(zoom_px.argmax())
    step = np.hypot(*np.diff(pos, axis=0).T) + np.abs(np.diff(zoom_px))
    every = [int(round(t * fps)) for t in np.arange(0, (len(pos) - 1) / fps + 1e-9, 0.25)]
    moved = bool(mag[k] > thr or zoom_px[kz] > thr)
    r = {"模式": f"方塊 {roi}" if roi else "自動（小塊相位相關＋中位數）", "fps": fps, "門檻px": thr,
         "最大平移px": round(float(mag[k]), 1), "最大平移在秒": round(k / fps, 2),
         "最大推拉px": round(float(zoom_px[kz]), 1), "最大推拉在秒": round(kz / fps, 2),
         "判定": "鏡頭有動" if moved else "沒動",
         "移動時段": moving_spans(step, fps) if moved else [],   # 沒動的片段不列（那些小波動是人物）
         "每0.25秒": [{"秒": round(i / fps, 2), "dx": round(float(pos[i, 0]), 1), "dy": round(float(pos[i, 1]), 1),
                     "平移": round(float(mag[i]), 1), "縮放%": round(float(scale[i] - 1) * 100, 2)} for i in every],
         "逐幀dxdy": [[round(float(a), 2), round(float(b), 2)] for a, b in pos],
         "逐幀縮放": [round(float(v), 5) for v in scale]}
    if roi:
        r["最低比對分數"] = round(min(score), 3)
        r["最低分在秒"] = round(int(np.argmin(score)) / fps, 2)
        r["分數低於0.6的幀數"] = int(sum(s < 0.6 for s in score))
        r["逐幀分數"] = [round(s, 3) for s in score]
    else:
        r["每幀有效小塊中位數"] = f"{int(np.median(used))}/{nblk}" if used else "—"
        r["追蹤不到的幀數"] = int(sum(u < MIN_BLOCKS for u in used)) if used else 0
        r["換參考幀次數"] = n_ref
    return r


def drift_one(video, roi=None, thr=None):
    return {"影片": video, **drift_frames(iter_frames(video, "gray"), video_fps(video), roi, thr)}


def cmd_drift(args):
    roi = None
    if args.roi:
        try:
            roi = tuple(int(v) for v in args.roi.split(","))
            assert len(roi) == 4 and roi[2] > 8 and roi[3] > 8
        except Exception:
            sys.exit("--roi 要寫成 x,y,寬,高（原尺寸像素），例如 60,20,240,140")
    lines, data = [], []
    for v in expand(args.videos, VIDEO_EXT):
        r = drift_one(v, roi, args.thr)
        data.append(r)
        s = (f"{name(v)}：{r['判定']}（平移最大 {r['最大平移px']} 像素，在 {r['最大平移在秒']} 秒"
             + ("" if roi else f"；推拉讓四角最多移 {r['最大推拉px']} 像素，在 {r['最大推拉在秒']} 秒")
             + f"；門檻 {r['門檻px']} 像素）｜{r['模式']}")
        lines.append(s)
        if r["判定"] == "鏡頭有動":
            lines.append("  鏡頭在動的時段：" + ("、".join(f"{a:.2f}–{b:.2f} 秒" for a, b in r["移動時段"])
                                            or "沒有明顯的快速移動，是整段慢慢漂"))
        if roi:
            lines.append(f"  比對分數最低 {r['最低比對分數']}（{r['最低分在秒']} 秒）；低於 0.6 的有 {r['分數低於0.6的幀數']} 幀"
                         + ("——那幾幀方塊被擋住或變形，位移不可信" if r["分數低於0.6的幀數"] else ""))
            cells = [f"{c['秒']:.2f}s {c['平移']:5.1f}({c['dx']:+.0f},{c['dy']:+.0f})" for c in r["每0.25秒"]]
        else:
            lines.append(f"  追蹤品質：每幀有效小塊 {r['每幀有效小塊中位數']}；追蹤不到的幀數 {r['追蹤不到的幀數']}；"
                         f"換參考幀 {r['換參考幀次數']} 次（0 次＝全程直接和第 0 幀比）")
            cells = [f"{c['秒']:.2f}s {c['平移']:5.1f}({c['dx']:+.0f},{c['dy']:+.0f}){c['縮放%']:+.1f}%" for c in r["每0.25秒"]]
        lines.append("  每 0.25 秒相對第 0 幀：平移像素（括號＝往右、往下為正）" + ("" if roi else "、縮放（＋＝推近）") + "：")
        for i in range(0, len(cells), 6):
            lines.append("    " + "  ".join(cells[i:i + 6]))
    emit(args, lines, data)


# ============================================================ 3. strip：事件條（某個瞬間前後每一幀）

def strip_one(video, t, span=0.5, out=None, cols=8, scale=1 / 3):
    from PIL import Image, ImageDraw
    fps = video_fps(video)
    i0 = max(0, math.ceil((t - span) * fps - 1e-6))
    i1 = math.floor((t + span) * fps + 1e-6)
    center = int(round(t * fps))
    tiles = []
    for i, f in enumerate(iter_frames(video, "rgb24")):
        if i > i1:
            break
        if i >= i0:
            tiles.append((i, f))
    if not tiles:
        sys.exit(f"{name(video)} 沒有 {t} 秒這段")
    i1 = tiles[-1][0]
    h, w = tiles[0][1].shape[:2]
    tw, th, lab, gap, head = int(w * scale), int(h * scale), 24, 4, 34
    cols = max(1, min(cols, len(tiles)))
    rows = math.ceil(len(tiles) / cols)
    canvas = Image.new("RGB", (cols * (tw + gap) + gap, head + rows * (th + lab + gap) + gap), (18, 18, 18))
    d = ImageDraw.Draw(canvas)
    f, zh = font(16)
    f2, _ = font(20)
    title = (f"{name(video)}｜{t:.2f} 秒前後 {span} 秒（第 {i0}–{i1} 幀，共 {len(tiles)} 幀；黃框＝{t:.2f} 秒那一幀）" if zh
             else f"{t:.2f}s +-{span}s frames {i0}-{i1}")
    d.text((gap + 2, 6), title, fill=(255, 220, 90), font=f2)
    for k, (i, fr) in enumerate(tiles):
        r_, c_ = divmod(k, cols)
        x = gap + c_ * (tw + gap)
        y = head + r_ * (th + lab + gap)
        d.text((x + 3, y + 3), f"{i / fps:.2f}s  #{i}", fill=(255, 255, 255), font=f)
        canvas.paste(Image.fromarray(fr).resize((tw, th), Image.Resampling.BILINEAR), (x, y + lab))
        if i == center:
            d.rectangle([x - 2, y + lab - 2, x + tw + 1, y + lab + th + 1], outline=(255, 210, 0), width=3)
    p = os.path.join(out_dir_for(video, out), f"{os.path.splitext(name(video))[0]}_事件條_{t:.2f}秒.png")
    canvas.save(p)
    return {"影片": video, "秒": t, "前後秒": span, "幀範圍": [i0, i1], "幀數": len(tiles), "圖": p}


def cmd_strip(args):
    jobs, cur = [], None
    for it in args.items:
        if it.lower().endswith(VIDEO_EXT):
            cur = it
        else:
            try:
                t = float(it.rstrip("s秒"))
            except ValueError:
                sys.exit(f"看不懂：{it}（要給影片，後面接秒數）")
            if cur is None:
                sys.exit("秒數前面要先給影片")
            jobs.append((cur, t))
    lines, data = [], []
    for v, t in jobs:
        r = strip_one(v, t, args.span, args.out, args.cols)
        data.append(r)
        lines.append(f"{name(v)} {t:.2f} 秒 ±{args.span}：第 {r['幀範圍'][0]}–{r['幀範圍'][1]} 幀（{r['幀數']} 幀）→ {r['圖']}")
    emit(args, lines, data)


# ============================================================ 4. loud：整體響度

def loud_one(path):
    """ffmpeg ebur128（peak=true）：整體響度 LUFS、響度範圍 LRA、true peak（dBTP，4 倍超取樣的真峰值）。影片取第一條音軌"""
    r = subprocess.run([FF, "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0", "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    e = r.stderr
    if r.returncode or "Summary:" not in e:
        return {"檔案": path, "錯誤": "沒有音軌或讀不到" + (f"：{e.strip().splitlines()[-1]}" if e.strip() else "")}
    s = e[e.rfind("Summary:"):]
    get = lambda pat: (lambda m: float(m.group(1)) if m else None)(re.search(pat, s, re.S))
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", e)
    return {"檔案": path, "LUFS": get(r"I:\s+(-?[\d.]+|-inf) LUFS"), "LRA": get(r"LRA:\s+(-?[\d.]+) LU"),
            "truepeak_dBTP": get(r"True peak:\s+Peak:\s+(-?[\d.]+|-inf) dBFS"),
            "長度秒": round(int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)), 2) if dur else None}


def cmd_loud(args):
    lines, data = [], []
    for p in expand(args.files, VIDEO_EXT + AUDIO_EXT):
        r = loud_one(p)
        data.append(r)
        if "錯誤" in r:
            lines.append(f"{name(p)}：{r['錯誤']}")
        else:
            lines.append(f"{name(p)}：整體響度 {r['LUFS']} LUFS｜true peak {r['truepeak_dBTP']} dBTP｜響度範圍 {r['LRA']} LU｜長 {r['長度秒']} 秒")
    lines.append("（LUFS 越接近 0 越大聲；一般短影音人聲對到 −16 LUFS 左右；true peak 超過 −1 dBTP 轉檔後可能破音）")
    emit(args, lines, data)


# ============================================================ 5. tail：音樂尾端（最後有聲在幾秒）

def read_audio(path):
    """讀成 float（−1～1），形狀 (聲道, 取樣數)；影片取第一條音軌"""
    import av
    with av.open(path) as c:
        if not c.streams.audio:
            return None, None
        st = c.streams.audio[0]
        rs = av.AudioResampler(format="fltp", layout=st.codec_context.layout, rate=st.rate)
        chunks = []
        for fr in c.decode(st):
            chunks += [o.to_ndarray() for o in rs.resample(fr)]
        chunks += [o.to_ndarray() for o in rs.resample(None)]
        return np.concatenate(chunks, axis=1).astype(np.float64), st.rate


def tail_one(path, want=None, db=SILENCE_DB, win=0.1, hop=0.01):
    """100 毫秒視窗（每 10 毫秒移一格）算 RMS（各聲道功率平均），高於門檻＝有聲；時間取視窗中心"""
    x, sr = read_audio(path)
    if x is None:
        return {"檔案": path, "錯誤": "沒有音軌"}
    n = x.shape[1]
    total = n / sr
    peak = float(np.abs(x).max()) if n else 0.0
    r = {"檔案": path, "總長秒": round(total, 3), "門檻dBFS": db, "視窗秒": win,
         "峰值dBFS": round(20 * math.log10(peak), 2) if peak > 0 else float("-inf")}
    p = (x ** 2).mean(axis=0)
    W, H = max(1, int(round(win * sr))), max(1, int(round(hop * sr)))
    if n < W:
        starts = np.array([0]); W = n
    else:
        starts = np.arange(0, n - W + 1, H)
    c = np.concatenate([[0.0], np.cumsum(p)])
    rms = np.sqrt(np.maximum(c[starts + W] - c[starts], 0) / max(W, 1))
    lvl = 20 * np.log10(np.maximum(rms, 1e-12))
    loud = np.nonzero(lvl > db)[0]
    if len(loud):
        r["第一次有聲秒"] = round((starts[loud[0]] + W / 2) / sr, 3)
        r["最後有聲秒"] = round((starts[loud[-1]] + W / 2) / sr, 3)
        r["尾端沒聲秒"] = round(total - r["最後有聲秒"], 3)
    else:
        r["第一次有聲秒"] = r["最後有聲秒"] = None
        r["尾端沒聲秒"] = round(total, 3)
    if want is not None:
        r["要求秒"] = want
        r["通過"] = bool(r["最後有聲秒"] is not None and r["最後有聲秒"] >= want)
    return r


def cmd_tail(args):
    lines, data = [], []
    for p in expand(args.files, AUDIO_EXT + VIDEO_EXT):
        r = tail_one(p, args.want, args.db, args.win)
        data.append(r)
        if "錯誤" in r:
            lines.append(f"{name(p)}：{r['錯誤']}")
            continue
        if r["最後有聲秒"] is None:
            s = f"{name(p)}：總長 {r['總長秒']:.2f} 秒｜整條都低於 {args.db} dBFS（等於沒有聲音）｜峰值 {r['峰值dBFS']} dBFS"
        else:
            s = (f"{name(p)}：總長 {r['總長秒']:.2f} 秒｜有聲 {r['第一次有聲秒']:.2f}–{r['最後有聲秒']:.2f} 秒"
                 f"（最後 {r['尾端沒聲秒']:.2f} 秒沒聲音）｜峰值 {r['峰值dBFS']} dBFS")
        if args.want is not None:
            gap = (r["最後有聲秒"] or 0) - args.want
            s += f"｜要 {args.want} 秒 → {'通過' if r['通過'] else '不通過'}（{'多' if gap >= 0 else '少'} {abs(gap):.2f} 秒）"
        lines.append(s)
    lines.append(f"（「有聲」＝{args.win * 1000:.0f} 毫秒視窗 RMS 高於 {args.db} dBFS；時間取視窗中心；峰值是取樣點峰值）")
    emit(args, lines, data)


# ============================================================ 6. dup：動作中突然重複的幀

def dup_one(path):
    """灰階相鄰幀平均絕對差 d；d 前後都 >2.0（在動）、中間 <0.6（幾乎沒變）才算一次"""
    fps = video_fps(path)
    prev, d = None, []
    for g in iter_frames(path, "gray"):
        g = g.astype(np.int16)
        if prev is not None:
            d.append(float(np.abs(g - prev).mean()))
        prev = g
    d = np.array(d)
    hits = [i for i in range(1, len(d) - 1) if d[i] < STILL and d[i - 1] > MOVING and d[i + 1] > MOVING]
    return {"影片": path, "動態步": int((d > MOVING).sum()), "總步數": len(d), "重複次數": len(hits),
            "重複在秒": [round((i + 1) / fps, 2) for i in hits]}


def cmd_dup(args):
    lines, data = [], []
    for v in expand(args.videos, VIDEO_EXT):
        r = dup_one(v)
        data.append(r)
        secs = ", ".join(f"{s:.2f}" for s in r["重複在秒"])
        lines.append(f"{name(v)}：動態步 {r['動態步']}/{r['總步數']}｜動作中突然重複 {r['重複次數']} 次"
                     + (f"（秒：{secs}）→ 看起來會卡" if r["重複次數"] else "→ 沒有卡頓"))
    emit(args, lines, data)


# ============================================================ 7. cut：片中硬切、淡入白底角色卡

def cut_one(path):
    """縮成 64×112 灰階（和解析度無關）：相鄰兩格平均差抓硬切，亮度偏離第 0 格抓淡入白底的參考圖"""
    raw = subprocess.run([FF, "-v", "error", "-i", path, "-vf", "scale=64:112,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, 112, 64).astype(np.float32)
    fps = video_fps(path)
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
    lum = f.mean(axis=(1, 2))
    dev = np.abs(lum - lum[0])
    i, j = int(d.argmax()), int(dev.argmax())
    return {"影片": path, "相鄰差最大": round(float(d.max()), 1), "在秒": round((i + 1) / fps, 2),
            "亮度偏離最大": round(float(dev.max()), 1), "偏離在秒": round(j / fps, 2),
            "有問題": bool(d.max() > CUT_THR or dev.max() > BRIGHT_THR)}


def cmd_cut(args):
    lines, data = [], []
    for v in expand(args.videos, VIDEO_EXT):
        r = cut_one(v)
        data.append(r)
        lines.append(f"{name(v)}：相鄰格差最大 {r['相鄰差最大']}（{r['在秒']} 秒）｜亮度偏離第 0 格最大 {r['亮度偏離最大']}（{r['偏離在秒']} 秒）"
                     + ("→ 疑似片中切鏡或漏出參考圖，要看" if r["有問題"] else "→ 沒有切鏡"))
    emit(args, lines, data)


# ============================================================ 8. speech：台詞時間軸（每段有聲的起訖）

def speech_one(path, db=VOICE_DB, gap=0.25, min_len=0.1, win=0.02, hop=0.01):
    """20 毫秒視窗 RMS 高於門檻算有聲；中間沒聲短於 gap 秒就併成一段，短於 min_len 秒的丟掉。
    片段裡的台詞通常比環境聲大很多，所以這就是台詞時間軸；大的動作聲也會算進來，要對照畫面"""
    x, sr = read_audio(path)
    if x is None:
        return {"檔案": path, "錯誤": "沒有音軌"}
    p = (x ** 2).mean(axis=0)
    W, H = int(round(win * sr)), int(round(hop * sr))
    starts = np.arange(0, max(len(p) - W, 0) + 1, H)
    c = np.concatenate([[0.0], np.cumsum(p)])
    lvl = 10 * np.log10(np.maximum((c[starts + W] - c[starts]) / W, 1e-24))
    t = (starts + W / 2) / sr
    segs = []
    for i in np.flatnonzero(lvl > db):
        if segs and t[i] - segs[-1][1] <= gap:
            segs[-1][1] = t[i]
        else:
            segs.append([t[i], t[i]])
    segs = [(round(a - win / 2, 2), round(b + win / 2, 2)) for a, b in segs if b - a + win >= min_len]
    total = len(p) / sr
    return {"檔案": path, "門檻dBFS": db, "有聲段": segs, "總長秒": round(total, 2),
            "段間空白": [round(b[0] - a[1], 2) for a, b in zip(segs, segs[1:])],
            "最後一段到片尾": round(total - segs[-1][1], 2) if segs else None}


def speech_text(r):
    if "錯誤" in r:
        return r["錯誤"]
    if not r["有聲段"]:
        return f"整條沒有高於 {r['門檻dBFS']} dBFS 的聲音"
    segs = "、".join(f"{a:.2f}–{b:.2f}" for a, b in r["有聲段"])
    gaps = "、".join(f"{g:.2f}" for g in r["段間空白"])
    head = "｜第一段從檔頭就開始，第一個字可能被切到，要聽" if r["有聲段"][0][0] < 0.05 else ""
    return f"有聲 {segs} 秒" + (f"｜段間空白 {gaps} 秒" if gaps else "") + f"｜最後一段到片尾 {r['最後一段到片尾']:.2f} 秒" + head


def cmd_speech(args):
    lines, data = [], []
    for p in expand(args.files, VIDEO_EXT + AUDIO_EXT):
        r = speech_one(p, args.db, args.gap)
        data.append(r)
        lines.append(f"{name(p)}：{speech_text(r)}")
    lines.append(f"（有聲＝20 毫秒視窗 RMS 高於 {args.db} dBFS；空白短於 {args.gap} 秒的併成一段；大的動作聲也會算進來）")
    emit(args, lines, data)


# ============================================================ 9. motion：畫面分區動量（誰在什麼時候動）

BARS = "▁▂▃▄▅▆▇█"


def motion_one(path, cols=3, rows=1, step=0.25):
    """縮成 96×168 灰階，切成 cols×rows 區；每區相鄰兩格的平均差，每 step 秒取平均"""
    raw = subprocess.run([FF, "-v", "error", "-i", path, "-vf", "scale=96:168,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    d = np.abs(np.diff(np.frombuffer(raw, np.uint8).reshape(-1, 168, 96).astype(np.float32), axis=0))
    per = max(1, int(round(step * video_fps(path))))
    col_names = {1: [""], 2: ["左", "右"], 3: ["左", "中", "右"]}.get(cols, [str(k + 1) for k in range(cols)])
    row_names = {1: [""], 2: ["上", "下"], 3: ["上", "中", "下"]}.get(rows, [str(k + 1) for k in range(rows)])
    hs, ws = 168 // rows, 96 // cols
    regions = {}
    for r_ in range(rows):
        for c_ in range(cols):
            m = d[:, r_ * hs:(r_ + 1) * hs, c_ * ws:(c_ + 1) * ws].mean(axis=(1, 2))
            regions[(row_names[r_] + col_names[c_]) or "全"] = [round(float(m[i:i + per].mean()), 2) for i in range(0, len(m), per)]
    top = max(max(v) for v in regions.values()) or 1.0
    # 明顯動＝到全片最大值的四成；靜止處的底噪約 0.01–0.05，只動頭和臉的細微表演全片最大約 0.3，所以下限設 0.1
    first = {k: next((round(i * step, 2) for i, x in enumerate(v) if x >= max(0.4 * top, 0.1)), None) for k, v in regions.items()}
    return {"影片": path, "每格秒": step, "分區": regions, "最大": top, "第一次明顯動": first}


def motion_text(r):
    lines = []
    for k, v in r["分區"].items():
        bar = "".join(BARS[min(7, int(x / r["最大"] * 7.999))] for x in v)
        t = r["第一次明顯動"][k]
        lines.append(f"  {k:<2} {bar}  " + (f"{t:.2f} 秒開始明顯動" if t is not None else "沒有明顯動作"))
    ticks = "".join("|" if (i * r["每格秒"]) % 1 < 1e-6 else " " for i in range(len(next(iter(r["分區"].values())))))
    lines.append(f"     {ticks}  （每格 {r['每格秒']} 秒，| 是整數秒；條越高動得越多，全片最大＝█）")
    return lines


def cmd_motion(args):
    lines, data = [], []
    cols, rows = (int(x) for x in args.grid.lower().split("x"))
    for v in expand(args.videos, VIDEO_EXT):
        r = motion_one(v, cols, rows, args.step)
        data.append(r)
        lines.append(f"{name(v)}：")
        lines += motion_text(r)
    emit(args, lines, data)


# ============================================================ 10. sheet：整條縮圖表

def sheet_one(video, every=0.5, out=None, cols=6, scale=0.25):
    from PIL import Image, ImageDraw
    fps = video_fps(video)
    stride = max(1, int(round(every * fps)))
    tiles = [(i, f) for i, f in enumerate(iter_frames(video, "rgb24")) if i % stride == 0]
    h, w = tiles[0][1].shape[:2]
    tw, th, lab, gap = int(w * scale), int(h * scale), 22, 4
    cols = max(1, min(cols, len(tiles)))
    rows = math.ceil(len(tiles) / cols)
    canvas = Image.new("RGB", (cols * (tw + gap) + gap, rows * (th + lab + gap) + gap), (18, 18, 18))
    d = ImageDraw.Draw(canvas)
    f, _ = font(15)
    for n, (i, fr) in enumerate(tiles):
        r_, c_ = divmod(n, cols)
        x, y = gap + c_ * (tw + gap), gap + r_ * (th + lab + gap)
        d.text((x + 3, y + 2), f"{i / fps:.2f}s  #{i}", fill=(255, 255, 255), font=f)
        canvas.paste(Image.fromarray(fr).resize((tw, th), Image.Resampling.BILINEAR), (x, y + lab))
    p = os.path.join(out_dir_for(video, out), f"{os.path.splitext(name(video))[0]}_縮圖_每{every:g}秒.png")
    canvas.save(p)
    return {"影片": video, "每幾秒": every, "張數": len(tiles), "圖": p}


def cmd_sheet(args):
    lines, data = [], []
    for v in expand(args.videos, VIDEO_EXT):
        r = sheet_one(v, args.every, args.out, args.cols)
        data.append(r)
        lines.append(f"{name(v)}：每 {args.every:g} 秒一張，共 {r['張數']} 張 → {r['圖']}")
    emit(args, lines, data)


# ============================================================ 11. zoom：放大一塊（看表情、嘴、眼睛）

def zoom_one(video, roi, t0, t1, every=0.125, out=None, cols=8, scale=2.0):
    from PIL import Image, ImageDraw
    x0, y0, rw, rh = roi
    fps = video_fps(video)
    want = {int(round(t * fps)) for t in np.arange(t0, t1 + 1e-6, every)}
    tiles = [(i, f[y0:y0 + rh, x0:x0 + rw]) for i, f in enumerate(iter_frames(video, "rgb24")) if i in want]
    if not tiles:
        sys.exit(f"{name(video)} 沒有 {t0}–{t1} 秒這段")
    tw, th, lab, gap = int(rw * scale), int(rh * scale), 22, 4
    cols = max(1, min(cols, len(tiles)))
    rows = math.ceil(len(tiles) / cols)
    canvas = Image.new("RGB", (cols * (tw + gap) + gap, rows * (th + lab + gap) + gap), (18, 18, 18))
    d = ImageDraw.Draw(canvas)
    f, _ = font(15)
    for n, (i, fr) in enumerate(tiles):
        r_, c_ = divmod(n, cols)
        x, y = gap + c_ * (tw + gap), gap + r_ * (th + lab + gap)
        d.text((x + 3, y + 2), f"{i / fps:.2f}s", fill=(255, 255, 255), font=f)
        canvas.paste(Image.fromarray(np.ascontiguousarray(fr)).resize((tw, th), Image.Resampling.LANCZOS), (x, y + lab))
    p = os.path.join(out_dir_for(video, out), f"{os.path.splitext(name(video))[0]}_放大_{x0}_{y0}_{t0:g}-{t1:g}秒.png")
    canvas.save(p)
    return {"影片": video, "範圍": roi, "秒": [t0, t1], "張數": len(tiles), "圖": p}


def cmd_zoom(args):
    roi = tuple(int(v) for v in args.roi.split(","))
    r = zoom_one(args.video, roi, args.t0, args.t1, args.every, args.out)
    emit(args, [f"{name(args.video)}：{args.t0:g}–{args.t1:g} 秒、每 {args.every:g} 秒一張，共 {r['張數']} 張 → {r['圖']}"], r)


# ============================================================ 12. join：接點試看片（前一鏡結尾＋本鏡＋後一鏡開頭）

def media_seconds(path):
    """畫面格數÷fps、聲音長度（解碼後實算，不信檔頭）"""
    raw = subprocess.run([FF, "-v", "error", "-i", path, "-map", "0:v:0", "-vf", "scale=16:16,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    aud = subprocess.run([FF, "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", "48000",
                          "-f", "s16le", "-"], capture_output=True).stdout
    return len(raw) // 256, video_fps(path), len(aud) / 2 / 48000


def join_one(prev, cur, nxt, tail=2.0, head=2.0, out=None):
    """前一鏡最後 tail 秒＋本鏡整條＋後一鏡最前 head 秒，剪點對齊影格，接成一條試看片（跟成片一樣是硬切）"""
    fps = video_fps(cur)
    parts = []
    if prev:
        n, _, _ = media_seconds(prev)
        parts.append((prev, max(0, n - int(round(tail * fps))) / fps, n / fps))
    n, _, _ = media_seconds(cur)
    parts.append((cur, 0.0, n / fps))
    if nxt:
        n, _, _ = media_seconds(nxt)
        parts.append((nxt, 0.0, min(n, int(round(head * fps))) / fps))
    import av
    with av.open(cur) as c:
        w, h = c.streams.video[0].codec_context.width, c.streams.video[0].codec_context.height
    args, fc = [FF, "-hide_banner", "-y"], []
    for k, (p, s, e) in enumerate(parts):
        args += ["-i", p]
        fc.append(f"[{k}:v]trim=start={s}:end={e},setpts=PTS-STARTPTS,fps={fps},scale={w}:{h},format=yuv420p[v{k}]")
        fc.append(f"[{k}:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a{k}]")
    fc.append("".join(f"[v{k}][a{k}]" for k in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=1[v][a]")
    dst = os.path.join(out_dir_for(cur, out), f"{os.path.splitext(name(cur))[0]}_接點試看.mp4")
    r = subprocess.run(args + ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18",
                               "-c:a", "aac", "-b:a", "192k", dst], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        sys.exit(r.stderr[-2000:])
    cuts, t = [], 0.0
    for p, s, e in parts[:-1]:
        t += e - s
        cuts.append(round(t, 2))
    return {"本鏡": cur, "前一鏡": prev, "後一鏡": nxt, "接點在秒": cuts, "試看片": dst}


def cmd_join(args):
    r = join_one(None if args.prev == "-" else args.prev, args.video, None if args.next == "-" else args.next,
                 args.tail, args.head, args.out)
    emit(args, [f"{name(args.video)} 接點試看：接點在 {', '.join(f'{c:.2f}' for c in r['接點在秒'])} 秒 → {r['試看片']}"], r)


# ============================================================ 13. av：畫面和聲音一樣長嗎

def av_one(path):
    n, fps, a = media_seconds(path)
    return {"檔案": path, "格數": n, "fps": fps, "畫面秒": round(n / fps, 3), "聲音秒": round(a, 3),
            "差秒": round(n / fps - a, 3), "超過一格": abs(n / fps - a) > 1 / fps}


def cmd_av(args):
    lines, data = [], []
    for p in expand(args.videos, VIDEO_EXT):
        r = av_one(p)
        data.append(r)
        lines.append(f"{name(p)}：畫面 {r['格數']} 格＝{r['畫面秒']:.3f} 秒｜聲音 {r['聲音秒']:.3f} 秒｜差 {r['差秒']:+.3f} 秒"
                     + ("→ 超過一格，嘴型會對不上" if r["超過一格"] else ""))
    emit(args, lines, data)


# ============================================================ 14. pack：驗片包（一次跑完量測、出縮圖和驗片紀錄草稿）

CHECKLIST = [   # 照 references/4-生成與驗片.md §3 的判片必檢
    "人數；每件道具的數量和外觀前後一致；穿戴態",
    "頭和臉在畫面裡；臉像角色卡（正式檔位）",
    "第 0 幀接得上首幀，起手接得上上一鏡",
    "只有一個主要變化；事件發生在動作卡寫的時間（容許 ±1 秒）",
    "鏡頭沒有自己動；沒有慢動作感；動作中不卡",
    "台詞：只有說話人的嘴在動、說完閉上、沒有多出的人聲；字對不對由使用者聽；心聲鏡整段嘴不動",
    "沒有片中切鏡或漏出參考圖",
    "1 倍速完整看一遍",
]


def cmd_pack(args):
    v = args.video
    d = args.out or os.path.join(os.path.dirname(os.path.abspath(v)), "驗片", os.path.splitext(name(v))[0])
    os.makedirs(d, exist_ok=True)
    av_ = av_one(v)
    cut = cut_one(v)
    dr = drift_one(v)
    dp = dup_one(v)
    sp_ = speech_one(v)
    mo = motion_one(v)
    ld = loud_one(v)
    sh = sheet_one(v, out=d)
    f0 = frame0_one(v, args.first, args.end, d) if args.first else None
    jn = join_one(args.prev, v, args.next, out=d) if (args.prev or args.next) else None

    rel = lambda p: os.path.relpath(p, d).replace("\\", "/")
    md = [f"# 驗片包：{name(v)}", "",
          f"{av_['格數']} 格（{av_['畫面秒']:.2f} 秒，{av_['fps']:g} fps）。這份是量測和草稿，判定寫進 `{os.path.splitext(name(v))[0]}_驗片.md`。", "",
          "## 量測", "", "| 項目 | 結果 |", "|---|---|"]
    if f0:
        m = f0["首幀比對"]["0"]
        md.append(f"| 第 0 幀對首幀 | 平均差 {m['平均差']}、SSIM {m['SSIM']}（釘了首幀通常 3–5，沒釘 10–18；50 以上＝首幀被蓋掉） |")
        if "尾幀比對" in f0:
            m2 = f0["尾幀比對"][str(f0["總幀數"] - 1)]
            md.append(f"| 最後一格對尾幀 | 平均差 {m2['平均差']}、SSIM {m2['SSIM']} |")
    md += [f"| 片中切鏡（cut） | 相鄰格差最大 {cut['相鄰差最大']}（{cut['在秒']} 秒）、亮度偏離最大 {cut['亮度偏離最大']}"
           f" → {'疑似切鏡或漏出參考圖，要看' if cut['有問題'] else '沒有'} |",
           f"| 鏡頭位移（drift） | {dr['判定']}（平移最大 {dr['最大平移px']} px、推拉最大 {dr['最大推拉px']} px） |",
           f"| 重複幀（dup） | 動作中突然重複 {dp['重複次數']} 次" + (f"（{', '.join(f'{s:.2f}' for s in dp['重複在秒'])} 秒）" if dp["重複次數"] else "") + " |",
           f"| 響度 | {ld.get('LUFS')} LUFS、true peak {ld.get('truepeak_dBTP')} dBTP |",
           f"| 聲畫長度 | 畫面 {av_['畫面秒']:.3f} 秒、聲音 {av_['聲音秒']:.3f} 秒（差 {av_['差秒']:+.3f}）"
           + ("→ 超過一格" if av_["超過一格"] else "") + " |",
           "", "## 時間軸", "", f"聲音：{speech_text(sp_)}", "", "畫面分區動量：", "```text"] + motion_text(mo) + ["```", "",
           "## 圖", "", f"- 縮圖：`{rel(sh['圖'])}`"]
    if f0:
        md += [f"- 第 0 幀比對：`{rel(f0['並排圖'])}`"] + ([f"- 尾幀比對：`{rel(f0['尾幀並排圖'])}`"] if "尾幀並排圖" in f0 else [])
    if jn:
        md.append(f"- 接點試看：`{rel(jn['試看片'])}`（接點在 {', '.join(f'{c:.2f}' for c in jn['接點在秒'])} 秒）")
    md += ["", "## 判片必檢（人看過才改；缺證據就是未核驗）", ""] + [f"{k + 1}. {c}：未核驗" for k, c in enumerate(CHECKLIST)]
    p = os.path.join(d, "驗片包.md")
    open(p, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("\n".join(md))
    print(f"\n→ {p}")


# ============================================================ 指令列

def _paths(args):
    """命令列給的檔案和資料夾（strip 的秒數、join 的 - 不算）"""
    for key in ("items", "videos", "files", "video", "prev", "next", "first", "end"):
        v = getattr(args, key, None)
        for p in v if isinstance(v, list) else [v]:
            if p and p != "-" and not re.fullmatch(r"[\d.]+[s秒]?", p):
                yield p


def main():
    ap = argparse.ArgumentParser(description="驗片量測：H3 片段與音訊的客觀量測，只讀素材、不碰 ComfyUI")
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("frame0", help="影片第 0 幀（和第 6、12、24 幀）和首幀圖的差異；可再給尾幀圖比最後一幀")
    p.add_argument("items", nargs="+", help="影片 首幀圖 [尾幀圖] [影片 首幀圖 ...]")
    p.add_argument("--out", help="並排圖存哪（預設：影片旁邊的 驗片 資料夾）")
    p.add_argument("--no-png", action="store_true", help="只算數字、不存圖")
    p.set_defaults(fn=cmd_frame0)

    p = sp.add_parser("drift", help="鏡頭有沒有自己移動")
    p.add_argument("videos", nargs="+", help="影片或資料夾")
    p.add_argument("--roi", help="改用方塊追蹤：x,y,寬,高（原尺寸像素），框一塊不會動、有獨特形狀的背景")
    p.add_argument("--thr", type=float, help=f"判定門檻（像素；預設自動模式 {DRIFT_THR}、方塊模式 {ROI_THR}）")
    p.set_defaults(fn=cmd_drift)

    p = sp.add_parser("strip", help="事件條：某秒前後每一幀排成一張圖")
    p.add_argument("items", nargs="+", help="影片 秒數 [秒數 ...] [影片 秒數 ...]")
    p.add_argument("--span", type=float, default=0.5, help="前後各幾秒（預設 0.5）")
    p.add_argument("--cols", type=int, default=8, help="一列幾張（預設 8）")
    p.add_argument("--out", help="圖存哪（預設：影片旁邊的 驗片 資料夾）")
    p.set_defaults(fn=cmd_strip)

    p = sp.add_parser("loud", help="整體響度 LUFS 與 true peak（ffmpeg ebur128）")
    p.add_argument("files", nargs="+", help="音檔、影片或資料夾")
    p.set_defaults(fn=cmd_loud)

    p = sp.add_parser("tail", help="音樂尾端：總長、最後有聲時間、峰值")
    p.add_argument("files", nargs="+", help="音檔、影片或資料夾")
    p.add_argument("--want", type=float, help="要求長度（秒）：最後有聲時間 ≥ 這個才通過")
    p.add_argument("--db", type=float, default=SILENCE_DB, help=f"有聲門檻 dBFS（預設 {SILENCE_DB}）")
    p.add_argument("--win", type=float, default=0.1, help="RMS 視窗秒數（預設 0.1）")
    p.set_defaults(fn=cmd_tail)

    p = sp.add_parser("dup", help="動作中突然重複的幀")
    p.add_argument("videos", nargs="+", help="影片或資料夾")
    p.set_defaults(fn=cmd_dup)

    p = sp.add_parser("cut", help="片中硬切、淡入白底角色卡（參考圖漏進影片）")
    p.add_argument("videos", nargs="+", help="影片或資料夾")
    p.set_defaults(fn=cmd_cut)

    p = sp.add_parser("speech", help="台詞時間軸：每段有聲的起訖、段間空白、最後一段到片尾")
    p.add_argument("files", nargs="+", help="影片、音檔或資料夾")
    p.add_argument("--db", type=float, default=VOICE_DB, help=f"有聲門檻 dBFS（預設 {VOICE_DB}）")
    p.add_argument("--gap", type=float, default=0.25, help="空白短於幾秒就併成同一段（預設 0.25）")
    p.set_defaults(fn=cmd_speech)

    p = sp.add_parser("motion", help="畫面分區動量：每一區在什麼時候動")
    p.add_argument("videos", nargs="+", help="影片或資料夾")
    p.add_argument("--grid", default="3x1", help="切成幾欄×幾列（預設 3x1＝左中右）")
    p.add_argument("--step", type=float, default=0.25, help="每格幾秒（預設 0.25）")
    p.set_defaults(fn=cmd_motion)

    p = sp.add_parser("sheet", help="整條縮圖表：每隔幾秒一張")
    p.add_argument("videos", nargs="+", help="影片或資料夾")
    p.add_argument("--every", type=float, default=0.5, help="每幾秒一張（預設 0.5）")
    p.add_argument("--cols", type=int, default=6, help="一列幾張（預設 6）")
    p.add_argument("--out", help="圖存哪（預設：影片旁邊的 驗片 資料夾）")
    p.set_defaults(fn=cmd_sheet)

    p = sp.add_parser("zoom", help="放大畫面的一塊（表情、嘴、眼睛），某段時間每隔幾秒一張")
    p.add_argument("video", help="影片")
    p.add_argument("roi", help="x,y,寬,高（原尺寸像素）")
    p.add_argument("t0", type=float, help="起秒")
    p.add_argument("t1", type=float, help="迄秒")
    p.add_argument("--every", type=float, default=0.125, help="每幾秒一張（預設 0.125＝3 格）")
    p.add_argument("--out", help="圖存哪（預設：影片旁邊的 驗片 資料夾）")
    p.set_defaults(fn=cmd_zoom)

    p = sp.add_parser("join", help="接點試看片：前一鏡結尾＋本鏡＋後一鏡開頭")
    p.add_argument("prev", help="前一鏡（沒有就寫 -）")
    p.add_argument("video", help="本鏡")
    p.add_argument("next", help="後一鏡（沒有就寫 -）")
    p.add_argument("--tail", type=float, default=2.0, help="前一鏡取最後幾秒（預設 2）")
    p.add_argument("--head", type=float, default=2.0, help="後一鏡取最前幾秒（預設 2）")
    p.add_argument("--out", help="試看片存哪（預設：影片旁邊的 驗片 資料夾）")
    p.set_defaults(fn=cmd_join)

    p = sp.add_parser("av", help="畫面和聲音一樣長嗎（解碼實算）")
    p.add_argument("videos", nargs="+", help="影片或資料夾")
    p.set_defaults(fn=cmd_av)

    p = sp.add_parser("pack", help="驗片包：一次跑完量測，出縮圖、接點試看片和驗片紀錄草稿")
    p.add_argument("video", help="影片")
    p.add_argument("--first", help="首幀圖（有就比第 0 幀）")
    p.add_argument("--end", help="尾幀圖（有就比最後一格）")
    p.add_argument("--prev", help="前一鏡（有就做接點試看片）")
    p.add_argument("--next", help="後一鏡（有就做接點試看片）")
    p.add_argument("--out", help="存哪（預設：影片旁邊的 驗片\\片段名\\）")
    p.set_defaults(fn=cmd_pack)

    for sub in sp.choices.values():
        sub.add_argument("--json", action="store_true", help="改輸出 JSON")
    args = ap.parse_args()
    for p in _paths(args):
        need_file(p, "檔案或資料夾")
    args.fn(args)


if __name__ == "__main__":
    main()
