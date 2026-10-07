"""第 5 站：剪接＋混音（ffmpeg）。依分鏡順序裁切片段、可單鏡加速、疊後製旁白、墊一條連續配樂（有人聲或音效時自動壓低），不上字幕。
G5C Animatic 也用這支：分鏡參考圖照鏡頭表的秒數硬切接起來，疊上預覽語音（`2-分鏡.md` §11）。
用法：python 剪接.py <剪接設定.json> [--dry-run]（例：粗剪_v1.json）；--dry-run 只印時間軸和每條語音在鏡內的起迄，不出片
剪接設定：
{
  "clips":    [["片段檔", 起秒, 迄秒, 倍速, 增益dB, 交叉淡化秒], ...], 起迄是片段原始時間；倍速 1＝原速（畫面和聲音一起加速，口型不跑）；
                                                        增益用來把各條片段的響度對齊（先用 驗片量測.py loud 量）；
                                                        交叉淡化＝跟下一條重疊淡化幾秒，其他接縫是硬切。後面三項可省略。
                                                        一鏡拆 A、B 段時：A 的迄秒寫到接點再加 0.25、淡化 0.25，B 從第 0 格起
                                                        （B 的第 0 格＝A 的接點那格，淡化時兩邊是同一刻，看不出接縫）
              一條也可以寫成 {"file", "start", "end", "speed", "gain", "xfade", "bed", "label", "overlays"}；
              "bed": ["環境聲檔或 roomtone", 目標LUFS]＝幾乎沒聲音的鏡墊一層環境聲（roomtone＝內建的室內空氣聲，約 −50 LUFS 剛好）
              "image" 加 "seconds" 代替 "file"、"start"、"end"＝一張靜態圖放幾秒（分鏡參考圖），縮放到正式檔位的畫布，配一段靜音
              "label": "S03"＝鏡號，QC 疊字和 --dry-run 的時間軸用
              "overlays": [["聲音檔", 鏡內時間點秒, 增益dB], ...]＝時間點從這一條在成片上的起點算，跟著這一條移動；
                          聲音比這一條剩下的長會警告，不自動變速、不剪
  "overlays": [["聲音檔", 成片時間點秒, 增益dB], ...],      後製旁白等，放在成片時間軸上
  "music":    "配樂檔或 null",
  "music_db": -14,                                        用 配樂組合.py 組好的配樂軌時設 0
  "fade_out": 1.5,                                        片尾畫面淡到黑、聲音淡出幾秒，可省略＝不淡出
  "qc_overlay": true,                                     在畫面上印每一條的 label 和時間碼，給審看的版本用；可省略＝不印
  "fps":      24,                                         可省略＝24
  "out":      "成片檔"
}
- 剪點自動對齊影格：起點取最近的一格，長度取整數格（變速時以輸出格數算）。沒對齊時每段畫面會多出半格，聲音和畫面越剪越歪。
- 限幅先升取樣到 192k 再壓（壓得到取樣點之間的峰值），並關掉自動拉大音量；直接 alimiter 時成片 true peak 會超過 0 dBTP。
- 剪完自動比對畫面和聲音的長度，差超過一格就報錯（結束碼 2）。
"""
import os, re, subprocess, sys
from comfy import CFG, PROFILES, need_file, read_json
sys.stdout.reconfigure(encoding="utf-8")
FF = CFG["ffmpeg"]
W, H = PROFILES["formal"]["w"], PROFILES["formal"]["h"]   # 靜態圖縮放到正式檔位的畫布
QC_STYLE = f"fontsize={H // 32}:fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=8"

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)
cfg = read_json(sys.argv[1])
DRY = "--dry-run" in sys.argv[2:]
FPS = int(cfg.get("fps", 24))
overlays = cfg.get("overlays") or []
music, out = cfg.get("music"), cfg["out"]


ROOMTONE_LUFS = -17.2   # 內建室內空氣聲（棕噪音、60–900 Hz、振幅 1）實測的響度


def duration(path):
    r = subprocess.run([FF, "-hide_banner", "-i", path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not d:
        print(f"❌ 讀不到長度，可能不是影片或聲音檔：{path}")
        sys.exit(1)
    h, m, s = d.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def lufs(path):
    r = subprocess.run([FF, "-hide_banner", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])


clips, gains, xfades, beds, stills, labels, clip_ovs = [], [], [], [], [], [], []
for c in cfg["clips"]:
    c = dict(zip(("file", "start", "end", "speed", "gain", "xfade"), c)) if isinstance(c, list) else c
    still = "image" in c   # 分鏡參考圖：一張靜態圖放 seconds 秒
    path, s, e = (c["image"], 0.0, float(c["seconds"])) if still else (c["file"], float(c["start"]), float(c["end"]))
    need_file(path, "分鏡參考圖" if still else "片段檔")
    if c.get("bed") and c["bed"][0] != "roomtone":
        need_file(c["bed"][0], "墊底的環境聲檔")
    sp = float(c.get("speed", 1))
    s2 = round(s * FPS) / FPS
    n = round(((e if still else min(e, duration(path))) - s2) / sp * FPS)
    e2 = s2 + n * sp / FPS
    if abs(s2 - s) > 0.001 or abs(e2 - e) > 0.001:
        print(f"剪點對齊影格：{os.path.basename(path)} {s:g}–{e:g} → {s2:.4f}–{e2:.4f}（{n} 格）")
    clips.append((path, s2, e2, sp, n))
    gains.append(float(c.get("gain", 0)))
    xfades.append(round(float(c.get("xfade", 0)) * FPS))   # 格數
    beds.append(c.get("bed"))
    stills.append(still)
    labels.append(c.get("label"))
    clip_ovs.append(c.get("overlays") or [])
xfades[-1] = 0
total = round((sum(n for *_, n in clips) - sum(xfades)) / FPS, 3)
starts = [0]   # 每一條在成片上的起點（格）
for i in range(1, len(clips)):
    starts.append(starts[-1] + clips[i - 1][4] - xfades[i - 1])

# 片段裡的 overlays（例：預覽語音）換成成片時間點，併進後製旁白；比這一條剩下的長就警告，不自動變速、不剪
for i, (path, *_, n) in enumerate(clips):
    name = labels[i] or os.path.basename(path)
    if DRY:
        print(f"{name}  {starts[i] / FPS:.3f}–{(starts[i] + n) / FPS:.3f} 秒（{n / FPS:.3f} 秒）  {path}")
    for p, t, g in clip_ovs[i]:
        need_file(p, "聲音檔")
        t, d = float(t), duration(p)
        if DRY:
            print(f"    {os.path.basename(p)}：鏡內 {t:.3f}–{t + d:.3f} 秒")
        if t + d > (n + 1) / FPS:
            print(f"⚠️ {name}：{os.path.basename(p)} 從鏡內 {t:g} 秒放、長 {d:.2f} 秒，超過這一條 {t + d - n / FPS:.2f} 秒")
        overlays.append([p, starts[i] / FPS + t, g])
if DRY:
    print(f"總長 {total} 秒；--dry-run 不出片")
    sys.exit(0)

for path, _, _ in overlays:
    need_file(path, "旁白檔")
if music:
    need_file(music, "配樂檔")
args = [FF, "-hide_banner", "-y"]
for (path, _, e, _, _), still in zip(clips, stills):
    # 靜態圖多給一格，結尾交給 trim 切：輸入剛好等長時 fps 會少吐最後一格
    args += ["-loop", "1", "-framerate", str(FPS), "-t", str(e + 1 / FPS), "-i", path] if still else ["-i", path]
for path, _, _ in overlays:
    args += ["-i", path]
if music:
    args += ["-i", music]
n_in = len(clips) + len(overlays) + bool(music)

fc, alab = [], []
for i, (_, s, e, sp, n) in enumerate(clips):
    fit = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1," if stills[i] else ""
    tag = f",drawtext=text='{labels[i]}':x=24:y=24:{QC_STYLE}" if cfg.get("qc_overlay") and labels[i] else ""
    fc.append(f"[{i}:v]{fit}trim=start={s}:end={e},setpts=(PTS-STARTPTS)/{sp},fps={FPS},format=yuv420p{tag}[v{i}]")
    if stills[i]:   # 靜態圖沒有聲音：配一段一樣長的靜音
        fc.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=end={n / FPS}[a{i}]")
    else:
        tempo = f",atempo={sp}" if sp != 1.0 else ""
        fc.append(f"[{i}:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS{tempo},aresample=48000,"
                  f"aformat=channel_layouts=stereo,volume={gains[i]}dB[a{i}]")
    alab.append(f"a{i}")
    if beds[i]:   # 墊底的環境聲剪成和這條一樣長，對到目標響度後混進去
        src, target = beds[i]
        if src == "roomtone":
            fc.append(f"anoisesrc=color=brown:sample_rate=48000:amplitude=1:seed={i}:duration={n / FPS},highpass=f=60,"
                      f"lowpass=f=900,aformat=channel_layouts=stereo,volume={target - ROOMTONE_LUFS:.1f}dB[b{i}]")
        else:
            args += ["-stream_loop", "-1", "-i", src]
            fc.append(f"[{n_in}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{n / FPS},asetpts=PTS-STARTPTS,"
                      f"volume={target - lufs(src):.1f}dB[b{i}]")
            n_in += 1
        fc.append(f"[a{i}][b{i}]amix=inputs=2:duration=first:normalize=0[a{i}b]")
        alab[i] = f"a{i}b"
# 一條一條接上去：有交叉淡化的接縫用 xfade／acrossfade（重疊的格數從總長扣掉），其他硬切
vout, bus, nf = "v0", alab[0], clips[0][4]
for i in range(1, len(clips)):
    x = xfades[i - 1]
    if x:
        fc.append(f"[{vout}][v{i}]xfade=transition=fade:duration={x / FPS}:offset={(nf - x) / FPS}[vj{i}]")
        fc.append(f"[{bus}][{alab[i]}]acrossfade=d={x / FPS}[aj{i}]")
    else:
        fc.append(f"[{vout}][{bus}][v{i}][{alab[i]}]concat=n=2:v=1:a=1[vj{i}][aj{i}]")
    vout, bus, nf = f"vj{i}", f"aj{i}", nf + clips[i][4] - x
fo = float(cfg.get("fade_out", 0))
if fo:
    fc.append(f"[{vout}]fade=t=out:st={total - fo}:d={fo}[vfo]")
    vout = "vfo"
if cfg.get("qc_overlay"):   # 時間碼＝成片時間（時:分:秒.毫秒）
    fc.append(f"[{vout}]drawtext=text='%{{pts\\:hms}}':x=w-tw-24:y=24:{QC_STYLE}[vqc]")
    vout = "vqc"

# 後製旁白：延遲到成片時間點後疊進人聲軌
if overlays:
    labels = []
    for k, (_, t, g) in enumerate(overlays):
        idx = len(clips) + k
        ms = int(round(float(t) * 1000))
        fc.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms},volume={g}dB[o{k}]")
        labels.append(f"[o{k}]")
    fc.append(f"[{bus}]{''.join(labels)}amix=inputs={1 + len(overlays)}:duration=first:normalize=0[dia]")
    bus = "dia"

if music:
    m = len(clips) + len(overlays)
    fade_out = max(total - 1.5, 0)
    fc.append(f"[{bus}]asplit=2[dia1][dia2]")
    fc.append(f"[{m}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total},asetpts=PTS-STARTPTS,"
              f"volume={cfg.get('music_db', -14)}dB,afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out}:d=1.5[mus]")
    # 片段本身有聲音（台詞、旁白、撞擊）時把配樂壓下去
    fc.append("[mus][dia2]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=350[musd]")
    fc.append("[dia1][musd]amix=inputs=2:duration=first:normalize=0[mix]")
    bus = "mix"
if fo:
    fc.append(f"[{bus}]afade=t=out:st={total - fo}:d={fo}[afo]")
    bus = "afo"
fc.append(f"[{bus}]aresample=192000,alimiter=limit=0.85:level=false,aresample=48000[aout]")

args += ["-filter_complex", ";".join(fc), "-map", f"[{vout}]", "-map", "[aout]",
         "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
if r.returncode:
    print(r.stderr[-3000:]); sys.exit(1)

video = subprocess.run([FF, "-v", "error", "-i", out, "-map", "0:v:0", "-vf", "scale=16:16,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True).stdout
audio = subprocess.run([FF, "-v", "error", "-i", out, "-map", "0:a:0", "-ac", "1", "-ar", "48000",
                        "-f", "s16le", "-"], capture_output=True).stdout
frames, a_sec = len(video) // 256, len(audio) / 2 / 48000
gap = frames / FPS - a_sec
print(f"完成：{out}（{total} 秒）｜畫面 {frames} 格＝{frames / FPS:.3f} 秒｜聲音 {a_sec:.3f} 秒｜差 {gap:+.3f} 秒")
if abs(gap) > 1 / FPS:
    print("聲音和畫面的長度差超過一格，嘴型會對不上：檢查片段檔本身的聲畫長度")
    sys.exit(2)
