"""RunPod Serverless 傳輸層（scripts/設定.json 的 "backend":"runpod" 才用得到）。
只管怎麼把工作送到 RunPod worker、怎麼收結果——不碰關卡、不碰帳本，那些還是 佇列.py／檢查量產Gate.py 的事。

兩個不同的 host，不要搞混：
    JOB     https://api.runpod.ai/v2/<endpoint id>/...   送件、查狀態、取消（佇列.py 用的那些）
    CTRL    https://api.runpod.io/v2/serverless/<id>     開關 worker（workers.max），只有 rp.py up／down 用

設定：
    RUNPOD_API_KEY、RUNPOD_ENDPOINT_ID（環境變數，不寫進 設定.json）
    R2_ENDPOINT、R2_BUCKET、R2_ACCESS_KEY_ID、R2_SECRET_ACCESS_KEY（環境變數；worker 端也要設一樣的）
命令列：
    python rp.py health          endpoint 活著嗎、worker 數、GPU 資訊
    python rp.py up [n]          workers.max 設成 n（預設 1）——開工前先叫起來
    python rp.py down            workers.max 設成 0——收工後一定要跑這個，不然顯卡一直計費
    python rp.py object_info     跑一個 action=object_info 的 job，存成 scripts/object_info.runpod.json（給 comfy.check_graph 用）
"""
import hashlib, json, os, sys, time, urllib.error, urllib.request

import comfy

try:
    import boto3
except ImportError:
    boto3 = None

JOB = "https://api.runpod.ai/v2"
CTRL = "https://api.runpod.io/v2"
KEY = os.environ.get("RUNPOD_API_KEY", "")
EP = os.environ.get("RUNPOD_ENDPOINT_ID", "") or comfy.CFG.get("runpod", {}).get("endpoint_id", "")
CACHE_DIR = os.path.join(comfy.PROJ, "_腳本", "佇列", "_runpod")   # RunPod 的 status 30 分鐘後過期，解析完的結果要自己留一份


def _need_key():
    if not KEY or not EP:
        print("❌ 缺 RUNPOD_API_KEY 或 RUNPOD_ENDPOINT_ID（環境變數）")
        sys.exit(1)


def _call(base, path, method="GET", body=None, timeout=60):
    _need_key()
    req = urllib.request.Request(f"{base}/{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode("utf-8"),
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {method} {path}：{e.read()[:800].decode('utf-8', 'replace')}")


def _r2():
    if boto3 is None:
        print("❌ 沒裝 boto3：backend=runpod 要先 pip install boto3（見 requirements.txt）")
        sys.exit(1)
    rp_cfg = comfy.CFG.get("runpod", {})
    endpoint = os.environ.get("R2_ENDPOINT") or rp_cfg.get("r2_endpoint", "")   # 端點、bucket 名字不是密鑰，可以寫 設定.local.json
    bucket = os.environ.get("R2_BUCKET") or rp_cfg.get("r2_bucket", "")
    for k, v in (("R2_ENDPOINT／設定.runpod.r2_endpoint", endpoint), ("R2_BUCKET／設定.runpod.r2_bucket", bucket),
                 ("環境變數 R2_ACCESS_KEY_ID", os.environ.get("R2_ACCESS_KEY_ID")),
                 ("環境變數 R2_SECRET_ACCESS_KEY", os.environ.get("R2_SECRET_ACCESS_KEY"))):
        if not v:
            print(f"❌ 缺 {k}")
            sys.exit(1)
    return boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                         aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"]), bucket


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _input_names(graph):
    """節點圖裡所有 LoadImage／LoadAudio 讀的檔名（相對 comfy.INP，可能帶子資料夾，例如 drama/x.png）"""
    names = []
    for node in graph.values():
        if node["class_type"] == "LoadImage":
            names.append(node["inputs"]["image"])
        elif node["class_type"] == "LoadAudio":
            names.append(node["inputs"]["audio"])
    return sorted(set(names))


def upload_inputs(graph):
    """把節點圖會讀的每個檔案傳到 R2（已經有就跳過，用 head_object 核對），回傳 handler 要的 inputs 清單"""
    s3, bucket = _r2()
    out = []
    for name in _input_names(graph):
        path = os.path.join(comfy.INP, name)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"節點圖要讀的檔案不在 ComfyUI input 鏡像：{path}")
        sha = _sha256(path)
        r2_key = f"inputs/{sha}{os.path.splitext(name)[1]}"
        try:
            s3.head_object(Bucket=bucket, Key=r2_key)
        except Exception:
            s3.upload_file(path, bucket, r2_key)
        out.append({"name": name, "r2_key": r2_key, "sha256": sha})
    return out


def submit(graph, free_first=False):
    """送一個 job，立刻回傳 RunPod 的 job id（不等完成——完成與否交給 get_result 之後再查）"""
    job_input = {"action": "prompt", "workflow": graph, "inputs": upload_inputs(graph), "free_first": free_first}
    resp = _call(JOB, f"{EP}/run", method="POST", body={"input": job_input})
    return resp["id"]


def _cache_path(pid):
    return os.path.join(CACHE_DIR, f"{pid}.json")


def _download_outputs(outputs):
    """把 outputs 裡每個有 r2_key 的檔案下載進本機 comfy.OUT 鏡像；原地拿掉 r2_key、sha256，剩下跟本機 /history 一樣的形狀"""
    s3, bucket = _r2()
    for node_out in outputs.values():
        for lst in node_out.values():
            if not isinstance(lst, list):
                continue
            for item in lst:
                if not (isinstance(item, dict) and "r2_key" in item):
                    continue
                dst = os.path.join(comfy.OUT, item.get("subfolder", ""), item["filename"])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                s3.download_file(bucket, item.pop("r2_key"), dst)
                item.pop("sha256", None)


def get_result(pid):
    """回傳 pid 的 /history 形狀結果（None＝還在跑）。COMPLETED／FAILED 都只解析一次，之後都讀本機快取"""
    cp = _cache_path(pid)
    if os.path.isfile(cp):
        return json.load(open(cp, encoding="utf-8"))
    resp = _call(JOB, f"{EP}/status/{pid}")
    status = resp.get("status")
    if status in ("IN_QUEUE", "IN_PROGRESS"):
        return None
    if status == "COMPLETED":
        out = resp.get("output") or {}
        if "error" in out or "node_errors" in out:
            msg = out.get("error") or json.dumps(out.get("node_errors"), ensure_ascii=False)
            entry = {"status": {"status_str": "error", "messages": [["execution_error", {"exception_message": msg}]]},
                     "outputs": {}}
        else:
            _download_outputs(out.get("outputs", {}))
            entry = {"status": out.get("status", {}), "outputs": out.get("outputs", {})}
    else:   # FAILED／CANCELLED／TIMED_OUT
        msg = f"RunPod job {status}：{json.dumps(resp, ensure_ascii=False)[:800]}"
        entry = {"status": {"status_str": "error", "messages": [["execution_error", {"exception_message": msg}]]},
                 "outputs": {}}
    os.makedirs(CACHE_DIR, exist_ok=True)
    json.dump(entry, open(cp, "w", encoding="utf-8"), ensure_ascii=False)
    return entry


def peek_status(pid):
    """只看一眼目前狀態（IN_QUEUE／IN_PROGRESS／COMPLETED／...），不下載、不寫快取。給 佇列.status() 顯示用"""
    try:
        return _call(JOB, f"{EP}/status/{pid}").get("status")
    except RuntimeError:
        return None


def cancel(pid):
    try:
        _call(JOB, f"{EP}/cancel/{pid}", method="POST")
    except RuntimeError as e:
        print(f"⚠️ 取消 {pid} 失敗（可能已經跑完）：{e}")


def health():
    return _call(JOB, f"{EP}/health")


def workers(n):
    """workers.max 設成 n。走 CTRL（api.runpod.io/v2），不是送件用的 JOB host——兩個搞混是 tesla 那邊踩過的坑
    （CLAUDE.md：「bật worker bằng MCP update-endpoint，đường REST của script không đánh thức worker」，
    原因是他們的腳本只打過 JOB host，從來沒打過會動到 workers.max 的 CTRL host）"""
    return _call(CTRL, f"serverless/{EP}", method="PATCH", body={"workers": {"max": n}})


def _run_raw(job_input, timeout):
    resp = _call(JOB, f"{EP}/run", method="POST", body={"input": job_input})
    pid, t0 = resp["id"], time.time()
    while time.time() - t0 < timeout:
        st = _call(JOB, f"{EP}/status/{pid}")
        if st.get("status") == "COMPLETED":
            return st.get("output")
        if st.get("status") in ("FAILED", "CANCELLED", "TIMED_OUT"):
            raise RuntimeError(f"{pid} {st.get('status')}：{json.dumps(st, ensure_ascii=False)[:800]}")
        time.sleep(5)
    raise RuntimeError(f"{pid} 超過 {timeout} 秒還沒完成")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "health":
        print(json.dumps(health(), ensure_ascii=False, indent=1))
    elif cmd == "up":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        print(json.dumps(workers(n), ensure_ascii=False, indent=1))
        print(f"workers.max 設成 {n}。確認真的叫起來：python rp.py health")
    elif cmd == "down":
        print(json.dumps(workers(0), ensure_ascii=False, indent=1))
        print("workers.max 設成 0。收工了記得跑一次 python rp.py health 核對 worker 數是 0")
    elif cmd == "object_info":
        info = _run_raw({"action": "object_info"}, timeout=600)
        if not isinstance(info, dict) or "error" in info:
            print("❌", info)
            sys.exit(1)
        here = os.path.dirname(os.path.abspath(__file__))
        json.dump(info, open(os.path.join(here, "object_info.runpod.json"), "w", encoding="utf-8"), ensure_ascii=False)
        print(f"存了 {len(info)} 個節點定義到 object_info.runpod.json")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
