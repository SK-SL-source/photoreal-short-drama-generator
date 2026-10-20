"""RunPod serverless handler：啟動 ComfyUI、收一個 job、送件、等完成、把輸出（圖／影片／音訊，不限 images 這個 key）
傳回 R2，再把結果整理成跟 ComfyUI 自己 /history/{prompt_id} 一樣的形狀（外加每個檔案的 r2_key、sha256）。
這樣 Mac 端的 scripts/comfy.py、scripts/佇列.py 幾乎不用改：它們本來就是照這個形狀讀 outputs。

輸入（job["input"]）：
  {"action": "health"}
  {"action": "object_info"}                                           # 回傳整份 /object_info，給 Mac 端快取
  {"action": "prompt", "workflow": {...}, "inputs": [{"name", "r2_key", "sha256"}], "free_first": bool}
      inputs：要放進 ComfyUI input/ 的檔案（LoadImage、LoadAudio 用的名字，可以帶子資料夾，例如 "drama/x.png"）
      free_first：True 就在送件前先呼叫本機 ComfyUI 的 /free（換模型家族前清顯存，見 comfy.free()）

輸出（job["output"]，對應 action）：
  health       {"reachable": bool, "gpu": "...", "comfyui_version": "..."} 或 {"error": "..."}
  object_info  ComfyUI /object_info 原始內容
  prompt       成功：{"prompt_id", "status": {...}, "outputs": {...}}（outputs 裡每個有 filename 的項目多了 r2_key、sha256）
               送件就被拒（node_errors）：{"node_errors": {...}}
               其他錯誤：{"error": "..."}
"""
import base64, hashlib, json, os, sys, time, traceback, uuid

import boto3
import requests
import runpod
import websocket
from botocore.config import Config as BotoConfig

COMFY_HOST = "127.0.0.1:8188"
COMFY_API_AVAILABLE_INTERVAL_MS = int(os.environ.get("COMFY_API_AVAILABLE_INTERVAL_MS", 250))
COMFY_API_FALLBACK_MAX_RETRIES = 1200   # 250ms * 1200 = 5 分鐘，夠冷啟動載完模型
VOLUME_CACHE = "/runpod-volume/cache/inputs"
WS_RECV_TIMEOUT_S = 15


def _r2():
    cfg = BotoConfig(signature_version="s3v4")
    return boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
                         aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                         aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], config=cfg)


def check_server(url, max_retries, interval_ms):
    for attempt in range(max_retries):
        try:
            if requests.get(url, timeout=5).status_code == 200:
                return True
        except requests.RequestException:
            pass
        if attempt % 40 == 0 and attempt:
            print(f"worker-h3 短劇：還在等 ComfyUI 起來…（{attempt * interval_ms / 1000:.0f}s）", flush=True)
        time.sleep(interval_ms / 1000)
    return False


def _download_input(item, s3, bucket_name):
    """把一個輸入檔從 R2 放進 ComfyUI 的 input/<name>；同一個 sha256 已經在 volume 快取就不重下。
    name 可以帶子資料夾（例如 drama/x.png），對應本機 comfy.stage_input 的那個名字。"""
    name, sha = item["name"], item["sha256"]
    dst = os.path.join("/comfyui/input", name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    cache = os.path.join(VOLUME_CACHE, sha + os.path.splitext(name)[1])
    if os.path.isfile(cache) and _sha256(cache) == sha:
        with open(cache, "rb") as f, open(dst, "wb") as g:
            g.write(f.read())
        return
    os.makedirs(VOLUME_CACHE, exist_ok=True)
    s3.download_file(bucket_name, item["r2_key"], dst)
    got = _sha256(dst)
    if got != sha:
        raise ValueError(f"輸入檔 {name} 下載後 sha256 不符（要 {sha}，拿到 {got}），R2 key：{item['r2_key']}")
    try:
        with open(dst, "rb") as f, open(cache, "wb") as g:
            g.write(f.read())
    except OSError as e:
        print(f"worker-h3 短劇：寫快取失敗（不影響這個 job）：{e}", flush=True)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def queue_workflow(workflow, client_id):
    resp = requests.post(f"http://{COMFY_HOST}/prompt",
                         data=json.dumps({"prompt": workflow, "client_id": client_id}).encode("utf-8"),
                         headers={"Content-Type": "application/json"}, timeout=30)
    if resp.status_code == 400:
        try:
            body = resp.json()
        except ValueError:
            body = {"error": resp.text[:1500]}
        return None, body
    resp.raise_for_status()
    return resp.json(), None


def wait_execution(ws, prompt_id):
    """等 ComfyUI 跑完這個 prompt_id；回傳 (ok, error_or_None)"""
    while True:
        try:
            out = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        if not isinstance(out, str):
            continue
        msg = json.loads(out)
        t = msg.get("type")
        if t == "executing":
            d = msg.get("data", {})
            if d.get("node") is None and d.get("prompt_id") == prompt_id:
                return True, None
        elif t == "execution_error":
            d = msg.get("data", {})
            if d.get("prompt_id") == prompt_id:
                return False, f"{d.get('node_type')} #{d.get('node_id')}：{d.get('exception_message')}"


def collect_outputs(prompt_id, job_id, s3, bucket_name):
    """抓 /history/{prompt_id}，把 outputs 裡每個有 filename 的項目上傳到 R2，原地補 r2_key、sha256。
    保留原本的 status／outputs 形狀（跟本機 /history 一樣），這樣 comfy.outputs() 不用改。"""
    history = requests.get(f"http://{COMFY_HOST}/history/{prompt_id}", timeout=30).json()
    entry = history.get(prompt_id, {})
    for node_id, node_out in entry.get("outputs", {}).items():
        for key, lst in node_out.items():
            if not isinstance(lst, list):
                continue
            for item in lst:
                if not (isinstance(item, dict) and "filename" in item and item.get("type", "output") == "output"):
                    continue
                params = {"filename": item["filename"], "subfolder": item.get("subfolder", ""), "type": "output"}
                r = requests.get(f"http://{COMFY_HOST}/view", params=params, timeout=120)
                r.raise_for_status()
                sha = hashlib.sha256(r.content).hexdigest()
                sub = item.get("subfolder", "")
                r2_key = f"jobs/{job_id}/" + (f"{sub}/" if sub else "") + item["filename"]
                s3.put_object(Bucket=bucket_name, Key=r2_key, Body=r.content)
                item["r2_key"], item["sha256"] = r2_key, sha
                print(f"worker-h3 短劇：{node_id}.{key} → {r2_key}（{len(r.content)} bytes）", flush=True)
    return entry


def handler(job):
    job_input, job_id = job["input"], job["id"]
    action = job_input.get("action", "prompt")

    if not check_server(f"http://{COMFY_HOST}/", COMFY_API_FALLBACK_MAX_RETRIES, COMFY_API_AVAILABLE_INTERVAL_MS):
        return {"error": f"ComfyUI（{COMFY_HOST}）冷啟動後還是連不上"}

    if action == "health":
        try:
            st = requests.get(f"http://{COMFY_HOST}/system_stats", timeout=10).json()
            dev = st["devices"][0]
            return {"reachable": True, "comfyui_version": st["system"].get("comfyui_version", ""),
                    "gpu": dev["name"], "vram_total_gb": round(dev["vram_total"] / 2**30, 1)}
        except Exception as e:
            return {"error": str(e)}

    if action == "object_info":
        try:
            return requests.get(f"http://{COMFY_HOST}/object_info", timeout=30).json()
        except Exception as e:
            return {"error": str(e)}

    if action != "prompt":
        return {"error": f"不認識的 action：{action}"}

    workflow = job_input.get("workflow")
    if not isinstance(workflow, dict):
        return {"error": "缺 workflow（或不是物件）"}

    bucket_name = os.environ["R2_BUCKET"]
    s3 = _r2()
    try:
        for item in job_input.get("inputs", []):
            _download_input(item, s3, bucket_name)
    except Exception as e:
        return {"error": f"輸入檔準備失敗：{e}"}

    if job_input.get("free_first"):
        try:
            requests.post(f"http://{COMFY_HOST}/free", json={"unload_models": True, "free_memory": True}, timeout=30)
        except requests.RequestException as e:
            print(f"worker-h3 短劇：/free 失敗（繼續送件）：{e}", flush=True)

    client_id = str(uuid.uuid4())
    ws = websocket.WebSocket()
    ws.settimeout(WS_RECV_TIMEOUT_S)
    try:
        ws.connect(f"ws://{COMFY_HOST}/ws?clientId={client_id}", timeout=10)
        resp, err_body = queue_workflow(workflow, client_id)
        if err_body is not None:
            return {"node_errors": err_body.get("node_errors", err_body)}
        prompt_id = resp.get("prompt_id")
        if not prompt_id:
            return {"error": f"/prompt 沒有回 prompt_id：{resp}"}
        print(f"worker-h3 短劇：送出 {prompt_id}", flush=True)

        ok, exec_err = wait_execution(ws, prompt_id)
    except websocket.WebSocketException as e:
        return {"error": f"websocket 錯誤：{e}"}
    finally:
        if ws.connected:
            ws.close()

    try:
        entry = collect_outputs(prompt_id, job_id, s3, bucket_name)
    except Exception as e:
        print(traceback.format_exc(), flush=True)
        return {"error": f"收輸出失敗：{e}", "prompt_id": prompt_id}

    if not ok and exec_err:
        status = entry.get("status", {})
        msgs = status.get("messages", [])
        if not any(m[0] == "execution_error" for m in msgs):
            msgs.append(["execution_error", {"exception_message": exec_err}])
        status["status_str"], status["messages"] = "error", msgs
        entry["status"] = status

    return {"prompt_id": prompt_id, "status": entry.get("status", {}), "outputs": entry.get("outputs", {})}


if __name__ == "__main__":
    print("worker-h3 短劇：啟動 handler", flush=True)
    runpod.serverless.start({"handler": handler})
