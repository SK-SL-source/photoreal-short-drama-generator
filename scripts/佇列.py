"""佇列帳本：一次排很多條、不用等它跑完；用 prompt_id 追進度，完成就把輸出複製回專案、記進 生成紀錄.jsonl。
接力：等前一條完成，先檢查它有沒有片中切鏡或漏出參考圖（驗片量測 cut），沒問題才取它的一格當這一條的首幀送出。
H3 影片（h3_ref、h3_i2v）只能經這裡送：排進來時和真的送出前各過一次送件前檢查（檢查量產Gate.py），排進來先記「待送出」，由 wait 送出；送出前那一次過了才拿到一次性通行證，comfy._post 只認它。
帳本在 _腳本\\佇列\\，一條一個檔；程式中斷了，重跑 wait 就接著做。

送件腳本裡：
    import comfy, 佇列
    g = comfy.h3_ref(…, guide0=True, **comfy.PROFILES["formal"])
    佇列.add("S03_r1_s1001", g, ["4-影片/S03_r1_s1001.mp4"], meta={"鏡": "S03", "提示詞": "3-提示詞/S03.txt", "seed": 1001})
    佇列.add("S04_r1_s1001", g4, ["4-影片/S04_r1_s1001.mp4"], after=("S03_r1_s1001", -1, "100"), meta={"鏡": "S04", …})
        # after＝(前一條, 第幾格（−1＝最後一格）, 要換圖的 LoadImage 節點)；接力、借格、B 段的鏡一定要給，送件前檢查核對它接的就是計畫寫的那一鏡那一格
        # H3 影片的 meta 要有「鏡」「提示詞」；拆段的鏡加「段」（A／B），用例外加「例外」（例：EXC-001）
命令列：
    python 佇列.py                看狀態（不改帳本）
    python 佇列.py wait [秒]       每隔幾秒（預設 30）收完成的、送輪到的接力，沒有會自己往下走的才停；同一時間只開一個
    python 佇列.py release 編號…   前一條被 cut 攔下、人看過沒問題，放行這幾條
    python 佇列.py cancel 編號…    從 ComfyUI 拿掉（正在跑的會中斷）
"""
import json, os, shutil, subprocess, sys, time, urllib.error, urllib.request
import comfy
import 檢查量產Gate as gate
from 驗片量測 import cut_one

sys.stdout.reconfigure(encoding="utf-8")
DIR = os.path.join(comfy.PROJ, "_腳本", "佇列")
WAIT, HOLD, READY, QUEUED, DONE, FAILED, CANCELLED, BLOCKED = "等前一條", "等你看", "待送出", "已送出", "完成", "失敗", "取消", "關卡擋下"


def _file(job_id):
    return os.path.join(DIR, f"{job_id}.json")


def _load(job_id):
    return json.load(open(_file(job_id), encoding="utf-8"))


def _save(job):
    os.makedirs(DIR, exist_ok=True)
    with open(_file(job["id"]) + ".tmp", "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=1)
    os.replace(_file(job["id"]) + ".tmp", _file(job["id"]))


def jobs():
    if not os.path.isdir(DIR):
        return []
    js = [json.load(open(os.path.join(DIR, n), encoding="utf-8")) for n in os.listdir(DIR) if n.endswith(".json")]
    return sorted(js, key=lambda j: j["added"])


def _say(job):
    print(f"[{job['id']}] {job['state']} {job.get('error') or job.get('prompt_id') or ''}".rstrip(), flush=True)


def _submit(job):
    facts, pre = comfy.h3_video_facts(job["graph"]), None
    if facts:   # H3 影片：真的送出前再過一次送件前檢查（排進來之後關卡可能已經失效）
        pre = gate.validate_shot_preflight(facts, job["meta"], "dispatch", after=job["after"], relay_frame=job.get("relay_frame"), job=job["id"])
        if not pre["ok"]:
            job.update(state=BLOCKED, error=pre["message"], gate=pre["reasons"])
            return
    problems = comfy.check_graph(job["graph"])
    if problems:
        job.update(state=FAILED, error="節點圖有問題：" + "；".join(problems[:3]))
        return
    try:
        resp = json.loads(comfy._post("/prompt", {"prompt": job["graph"], "client_id": "short-drama", "front": job["front"]},
                                      permit=pre["permit"] if pre else None, job=job["id"]))
    except urllib.error.HTTPError as e:
        job.update(state=FAILED, error=f"送件 HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:800]}")
        return
    if resp.get("node_errors"):
        job.update(state=FAILED, error="node_errors：" + json.dumps(resp["node_errors"], ensure_ascii=False)[:800])
        return
    job.update(state=QUEUED, prompt_id=resp["prompt_id"], error=None, submitted=time.strftime("%Y-%m-%d %H:%M:%S"))


def add(job_id, graph, dests, after=None, front=False, meta=None):
    """排一條。dests：輸出依序複製到專案裡的這些路徑（相對專案資料夾）。
    after＝(前一條編號, 第幾格, LoadImage 節點編號)：等前一條完成、檢查過，再取那一格換進這個節點送出。
    H3 影片先過送件前檢查，沒過就丟 comfy.GateBlocked、不進帳本；過了記「待送出」，wait 送出前再檢查一次。其他的圖照舊直接送。"""
    if os.path.exists(_file(job_id)):
        raise ValueError(f"{job_id} 已經在帳本裡")
    if after and not (after[1] == -1 or after[1] >= 0):
        raise ValueError("接力只能取最後一格（−1）或從頭數的第幾格")
    if after and graph[after[2]]["class_type"] != "LoadImage":
        raise ValueError(f"節點 {after[2]} 不是 LoadImage")
    facts = comfy.h3_video_facts(graph)
    if facts:
        pre = gate.validate_shot_preflight(facts, meta or {}, "add", after=list(after) if after else None)
        if not pre["ok"]:
            raise comfy.GateBlocked(pre)
    job = {"id": job_id, "graph": graph, "dests": list(dests), "after": list(after) if after else None, "front": front,
           "meta": meta or {}, "state": READY if facts and not after else WAIT, "added": time.time()}
    if not after and not facts:
        _submit(job)
    _save(job)
    _say(job)
    return job


def _finish(j, entry):
    st = entry.get("status", {})
    msgs = {m[0]: m[1] for m in st.get("messages", [])}
    if "execution_start" in msgs and "execution_success" in msgs:
        j["sec"] = round((msgs["execution_success"]["timestamp"] - msgs["execution_start"]["timestamp"]) / 1000)
    if st.get("status_str") != "success":
        j.update(state=FAILED, error="生成失敗：" + json.dumps(st.get("messages", []), ensure_ascii=False)[-800:])
    else:
        got = []
        for src, dst in zip(comfy.outputs(entry), j["dests"]):
            dst = os.path.join(comfy.PROJ, dst)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            got.append(dst)
        j.update(state=DONE if got else FAILED, files=got, error=None if got else "沒有輸出")
    comfy.log({"job": j["id"], "prompt_id": j["prompt_id"], "sec": j.get("sec"), "files": j.get("files"),
               "error": j.get("error"), **j["meta"]})


def _relay(j, src, check=True):
    """取前一條第一個輸出的那一格，存進專案 資產\\ 和 ComfyUI input，換進這一條的 LoadImage 再送。
    check：先跑 cut，前一條疑似片中切鏡或漏出參考圖就停在「等你看」，壞掉的結尾不會一路接下去"""
    _, frame, node = j["after"]
    video = src["files"][0]
    if check:
        r = cut_one(video)
        if r["有問題"]:
            j.update(state=HOLD, error=f"前一條 {src['id']} 疑似片中切鏡或漏出參考圖（相鄰格差 {r['相鄰差最大']}、"
                                       f"亮度偏離 {r['亮度偏離最大']}），看過沒問題再 release")
            return
    name = f"{j['id']}_首幀_接{src['id']}.png"
    png = os.path.join(comfy.PROJ, "資產", name)
    os.makedirs(os.path.dirname(png), exist_ok=True)
    if frame == -1:
        cmd = ["-sseof", "-0.5", "-i", video, "-update", "1", png]
    else:
        cmd = ["-i", video, "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1", png]
    subprocess.run([comfy.CFG["ffmpeg"], "-v", "error", "-y"] + cmd, check=True)
    j["graph"][node]["inputs"]["image"] = comfy.stage_input(png, name)
    j["relay_frame"] = png
    _submit(j)


def advance():
    """收完成的、送輪到的接力"""
    q = json.load(urllib.request.urlopen(f"{comfy.BASE}/queue", timeout=15))
    live = {x[1] for x in q["queue_running"] + q["queue_pending"]}
    js = {j["id"]: j for j in jobs()}
    for j in js.values():
        if j["state"] != QUEUED:
            continue
        h = json.load(urllib.request.urlopen(f"{comfy.BASE}/history/{j['prompt_id']}", timeout=15))
        if j["prompt_id"] in h:
            _finish(j, h[j["prompt_id"]])
        elif j["prompt_id"] not in live:
            j.update(state=FAILED, error="ComfyUI 的佇列和紀錄裡都找不到（重開過？）")
        else:
            continue
        _save(j)
        _say(j)
    for j in js.values():
        if j["state"] == READY:
            _submit(j)
            _save(j)
            _say(j)
    for j in js.values():
        if j["state"] != WAIT:
            continue
        src = js.get(j["after"][0])
        if src is None or src["state"] in (FAILED, CANCELLED, BLOCKED):
            j.update(state=FAILED, error=f"前一條 {j['after'][0]} 沒有完成，這條沒送")
        elif src["state"] == DONE:
            _relay(j, src)
        else:
            continue
        _save(j)
        _say(j)
    return js


def _moving(js):
    """還會自己往下走的條數：已送出的，加上前一條（一路往上）還在跑的接力"""
    def alive(j):
        while j and j["state"] == WAIT:
            j = js.get(j["after"][0])
        return bool(j) and j["state"] in (READY, QUEUED)
    return sum(alive(j) for j in js.values() if j["state"] in (WAIT, READY, QUEUED))


def status():
    js = jobs()
    if not js:
        print("帳本是空的")
        return
    try:
        q = json.load(urllib.request.urlopen(f"{comfy.BASE}/queue", timeout=15))
        running = {x[1] for x in q["queue_running"]}
        pending = [x[1] for x in sorted(q["queue_pending"], key=lambda x: x[0])]
    except OSError:
        print("連不上 ComfyUI，只顯示帳本")
        running, pending = set(), []
    for j in js:
        if j["state"] == QUEUED:
            pid = j["prompt_id"]
            note = "正在跑" if pid in running else (f"排第 {pending.index(pid) + 1}" if pid in pending else "跑完了，等 wait 收")
        elif j["state"] == WAIT:
            note = f"等 {j['after'][0]}"
        elif j["state"] == READY:
            note = "等 wait 送出"
        elif j["state"] == DONE:
            note = f"{j.get('sec', '?')} 秒 → {os.path.relpath(j['files'][0], comfy.PROJ)}"
        else:
            note = j.get("error") or ""
        print(f"{j['id']}｜{j['state']}｜{note}")
    counts = {}
    for j in js:
        counts[j["state"]] = counts.get(j["state"], 0) + 1
    print("合計：" + "、".join(f"{k} {v}" for k, v in counts.items()))


def wait(interval=30):
    while True:
        try:
            js = advance()
        except OSError as e:
            print(f"連不上 ComfyUI（{e}），{interval:g} 秒後再試", flush=True)
            time.sleep(interval)
            continue
        if not _moving(js):
            break
        time.sleep(interval)
    status()
    held = [j["id"] for j in js.values() if j["state"] == HOLD]
    if held:
        print("等你看：" + "、".join(held) + "（前一條看過沒問題，用 release 放行）")
    blocked = [j["id"] for j in js.values() if j["state"] == BLOCKED]
    if blocked:
        print("關卡擋下：" + "、".join(blocked) + "（原因見上面；不會再送，問題解決後用新的編號重排）")
    return 1 if any(j["state"] in (FAILED, BLOCKED) for j in js.values()) else 0


def release(ids):
    for jid in ids:
        j = _load(jid)
        if j["state"] != HOLD:
            raise ValueError(f"{jid} 是「{j['state']}」，不是等你看")
        _relay(j, _load(j["after"][0]), check=False)
        _save(j)
        _say(j)


def cancel(ids):
    q = json.load(urllib.request.urlopen(f"{comfy.BASE}/queue", timeout=15))
    running = {x[1] for x in q["queue_running"]}
    for jid in ids:
        j = _load(jid)
        if j["state"] == QUEUED:
            if j["prompt_id"] in running:
                comfy._post("/interrupt", {"prompt_id": j["prompt_id"]})
            else:
                comfy._post("/queue", {"delete": [j["prompt_id"]]})
        if j["state"] in (WAIT, HOLD, READY, QUEUED):
            j["state"] = CANCELLED
            _save(j)
        _say(j)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        status()
    elif cmd == "wait":
        sys.exit(wait(float(sys.argv[2]) if len(sys.argv) > 2 else 30))
    elif cmd == "release":
        release(sys.argv[2:])
    elif cmd == "cancel":
        cancel(sys.argv[2:])
    else:
        print(__doc__)
        sys.exit(2)
