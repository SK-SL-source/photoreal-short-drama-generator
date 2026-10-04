"""第 5 站：把照曲目單分段生成的配樂，放到成片的時間點上，組成一條跟成片一樣長的配樂軌，再交給 剪接.py 的 "music"（music_db 設 0）。
用法：python 配樂組合.py <組合設定.json>（例：配樂組合_v1.json）
組合設定：
{
  "total": 成片秒數,
  "cues":  [["配樂檔", 取用起秒, 放在成片秒, 長度秒, 目標LUFS, 淡入秒, 淡出秒], ...],
  "dips":  [[起秒, 迄秒, 降多少dB], ...],   成片時間；前後各 0.3 秒漸變（例：關鍵台詞時停一拍）
  "out":   "輸出 wav"
}
- 每段各自對到目標響度：同一首主題在不同段落要多大聲，在這裡定。
- 最後一段想讓收尾落在片尾，就放在「成片秒數 − 長度」。
- 沒放配樂的時間就是沒有配樂（曲目單寫「不放」的段落）。
"""
import os, re, subprocess, sys
from comfy import CFG, need_file, read_json
sys.stdout.reconfigure(encoding="utf-8")
FF = CFG["ffmpeg"]


def lufs(path, start, length):
    r = subprocess.run([FF, "-hide_banner", "-ss", str(start), "-t", str(length), "-i", path, "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])


if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)
cfg = read_json(sys.argv[1])
total = cfg["total"]
for path, *_ in cfg["cues"]:
    need_file(path, "配樂檔")
args = [FF, "-hide_banner", "-y"]
fc = []
for k, (path, src, at, length, target, fin, fout) in enumerate(cfg["cues"]):
    args += ["-i", path]
    gain = round(target - lufs(path, src, length), 1)
    ms = int(round(at * 1000))
    fc.append(f"[{k}:a]atrim=start={src}:duration={length},asetpts=PTS-STARTPTS,aresample=48000,"
              f"aformat=channel_layouts=stereo,volume={gain}dB,afade=t=in:d={fin},"
              f"afade=t=out:st={length - fout}:d={fout},adelay={ms}|{ms}[c{k}]")
    print(f"{path}：取 {src}–{src + length:.2f} 秒，放在 {at} 秒，增益 {gain} dB")
n = len(cfg["cues"])
fc.append("".join(f"[c{k}]" for k in range(n)) + f"amix=inputs={n}:duration=longest:normalize=0,apad=whole_dur={total},atrim=0:{total}[m]")
expr = "1"
for t1, t2, db in cfg.get("dips", []):
    g = 10 ** (-abs(db) / 20)
    expr += f"-{1 - g:.4f}*min(1,max(0,(t-{t1 - 0.3})/0.3))*min(1,max(0,({t2 + 0.3}-t)/0.3))"
fc.append(f"[m]volume='{expr}':eval=frame[out]")
args += ["-filter_complex", ";".join(fc), "-map", "[out]", "-c:a", "pcm_s24le", cfg["out"]]
os.makedirs(os.path.dirname(os.path.abspath(cfg["out"])), exist_ok=True)
r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
if r.returncode:
    print(r.stderr[-3000:]); sys.exit(1)
print(f"完成：{cfg['out']}（{total} 秒）")
