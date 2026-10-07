"""量產關卡（G5D）：Final Shot Lock 的前置檢查和指紋、整部片的關卡、H3 影片的送件前檢查和一次性通行證、例外。
規格：references/2-分鏡.md §12、references/4-生成與驗片.md §1。佇列.py 排進佇列和真的送出前都會呼叫這裡；這支只照規格實作，不自己訂規則。
命令列（在專案資料夾；不在就加 --project 專案資料夾）：
    python _腳本/檢查量產Gate.py check              關卡過不過；沒過是哪一項；例外各用掉幾條
    python _腳本/檢查量產Gate.py fingerprint        印出現在的三個指紋（不寫檔）
    python _腳本/檢查量產Gate.py ready --animatic 2-預覽/animatic_v03.mp4 --manifest 2-預覽/animatic_v03.json
        鎖定前先跑：前置都齊了沒（不寫檔），給使用者看鎖定卡之前用
    python _腳本/檢查量產Gate.py lock --animatic 2-預覽/animatic_v03.mp4 --manifest 2-預覽/animatic_v03.json --user "使用者 2026-10-07：「鎖定」"
        使用者說了「鎖定」才跑，--user 填他的原話和日期（原話裡要有「鎖定」，記進 locked_by_user）：前置沒齊就不鎖；
        齊了才算指紋、寫 量產關卡.json（state＝LOCKED）。程式不會因為檔案齊全就自己鎖
    python _腳本/檢查量產Gate.py invalidate --reason "S03 改台詞"
        使用者要改鎖定的內容：state 改成 INVALID。沒有反過來的命令，要重新鎖定就重跑 lock
    python _腳本/檢查量產Gate.py exception --shots S01 --profiles draft --takes 3 --reason "S01 景別測試" --user "使用者 2026-10-07：「例外生成 S01 草稿 3 條」"
        記一條例外，編號自動給（EXC-001…）。--user 是使用者的原話和日期，裡面要有「例外」和每個鏡號，「現在就送」「先跑看看」不算
"""
import argparse, csv, datetime, hashlib, io, json, os, re, subprocess, sys
import comfy
import h3_prompt_lint

GATE_FILE = "量產關卡.json"
SCHEMA_VERSION = 1
RUNTIME_COLUMNS = ("狀態", "重做次數", "備註")   # 執行中的欄位：照欄名排除，不看位置
DERIVED_COLUMNS = ("參考圖",)                    # 鏡頭表「參考圖」是分鏡卡「素材」的鏡像：不算指紋，鎖定前要和素材一致
HASHES = {"shot_table_hash": ("HASH_SHOT_TABLE", "鏡頭表"),
          "production_plan_hash": ("HASH_PRODUCTION_PLAN", "製作計畫（劇本、分鏡卡、風格句、美術段、資產、音色、分鏡參考圖）"),
          "animatic_manifest_hash": ("HASH_ANIMATIC", "核可的 Animatic 時間軸和它用到的檔")}
STRATEGIES = (("接力", "relay"), ("借", "borrow"), ("合成", "composite"), ("無", "none"))
GATE_RECORD = (("G3A", "LOCKED"), ("G4C", "PASS"), ("G5C", "PASS"))   # 鎖定前，關卡紀錄表要這樣寫
SPLIT = r"[、,，;；\s]+"


# ---------- 正規化 ----------

def _text(path):
    """UTF-8 讀進來；檔頭 BOM 不算，換行一律 \\n"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return f.read().replace("\r\n", "\n").replace("\r", "\n")


def _canon_text(s):
    """行尾空白、前後空行不算"""
    return "\n".join(line.rstrip() for line in s.split("\n")).strip("\n")


def _digest(obj):
    data = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def _numbers(x):
    """數字一律當小數比：3 和 3.0 是同一個值"""
    if isinstance(x, bool) or not isinstance(x, (int, float, list, dict)):
        return x
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, list):
        return [_numbers(v) for v in x]
    return {k: _numbers(v) for k, v in x.items()}


def file_sha(path):
    """檔案原始內容的 SHA-256（每次都重算：同一個時鐘刻度裡改過、大小又一樣的檔，靠修改時間分不出來）"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def canon_path(root, p):
    """路徑一律寫成相對專案資料夾、用 / 分隔：2-分鏡圖\\S01.png 和 2-分鏡圖/S01.png 是同一個檔"""
    p = p.strip().strip("`").replace("\\", "/")
    full = os.path.normpath(p if os.path.isabs(p) else os.path.join(root, p))
    try:
        rel = os.path.relpath(full, root)
    except ValueError:   # 不同磁碟
        rel = full
    return (full if rel.startswith("..") else rel).replace("\\", "/")


def _abs(root, p):
    return p if os.path.isabs(p) else os.path.join(root, p)


def _sha_if_file(root, p):
    return file_sha(_abs(root, p)) if os.path.isfile(_abs(root, p)) else None


def _result(fails, **extra):
    """fails：[(機器讀的代碼, 給人看的說明)]"""
    return {"ok": not fails, "reasons": [c for c, _ in fails], "messages": [m for _, m in fails],
            "message": "；".join(m for _, m in fails) or "通過", **extra}


# ---------- 讀來源 ----------

def load_gate(root):
    """量產關卡.json；沒有就回傳 None，寫壞了就丟 ValueError"""
    path = os.path.join(root, GATE_FILE)
    return json.loads(_text(path)) if os.path.exists(path) else None


def read_shot_table(root):
    """鏡頭表照欄名讀，一列一鏡、保留原本的順序；儲存格前後空白不算"""
    rows = csv.DictReader(io.StringIO(_text(os.path.join(root, "2-鏡頭表.csv"))))
    return [{k.strip(): (v or "").strip() for k, v in r.items() if k is not None} for r in rows]


def md_tables(md):
    """Markdown 表格：[(表頭, [一列一個 dict])]"""
    tables, head, rows = [], None, []
    for line in md.split("\n") + [""]:
        if line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if head is None:
                head = cells
            elif not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                rows.append(dict(zip(head, cells)))
        elif head is not None:
            tables.append((head, rows))
            head, rows = None, []
    return tables


def _table(md, *need):
    """表頭有 need 這幾欄的第一張表"""
    return next((rows for head, rows in md_tables(md) if all(n in head for n in need)), None)


def find_cards(md, shot_ids):
    """每一鏡的分鏡卡：從「鏡號｜」開頭的那一行起，到下一張卡、下一個標題或 ``` 為止。回傳（{鏡號: 全文}, 出現兩次以上的鏡號）"""
    ids = [s for s in shot_ids if s]
    if not ids:
        return {}, []
    start = re.compile(r"^#*\s*(" + "|".join(map(re.escape, sorted(ids, key=len, reverse=True))) + r")\s*[｜|]")
    lines = md.split("\n")
    cards, dup = {}, []
    for i, line in enumerate(lines):
        m = start.match(line)
        if not m:
            continue
        j = i + 1
        while j < len(lines) and not (lines[j].startswith(("#", "```")) or start.match(lines[j])):
            j += 1
        if m.group(1) in cards:
            dup.append(m.group(1))
        cards[m.group(1)] = _canon_text("\n".join(lines[i:j]))
    return cards, dup


def style_parts(md):
    """視覺聖經「風格句」那一列，和「美術段」整段（**美術段 那一行到下一個標題）"""
    row = re.search(r"^\|\s*風格句\s*\|.*$", md, re.M)
    art = re.search(r"^\*\*美術段.*?(?=^#|\Z)", md, re.M | re.S)
    style = "|".join(c.strip() for c in row.group(0).strip().strip("|").split("|")) if row else None
    return style, (_canon_text(art.group(0)) if art else None)


def asset_table(root, md):
    """資產表（表頭有「ID」「檔案」）：{ID: {"file": 相對路徑, "status": 狀態}}。找不到表、ID 重複都算錯。
    「用在」只是反查用的索引，製作依賴以分鏡卡的「素材」為準"""
    rows = _table(md, "ID", "檔案")
    if rows is None:
        return {}, ["找不到資產表（表頭要有「ID」「檔案」兩欄）"]
    ids = [r["ID"] for r in rows]
    return ({r["ID"]: {"file": canon_path(root, r["檔案"]), "status": r.get("狀態")} for r in rows},
            [f"資產表 ID 重複：{i}" for i in sorted({i for i in ids if ids.count(i) > 1})])


def voice_table(root, md):
    """音色資產表（表頭有「Voice ID」「核可檔」）。使用鏡頭的寫法：S05＝在 H3 片段裡出聲（對白、畫外人聲）；
    S05（心聲）、S05（旁白）＝只在後製和 Animatic 用，不掛 H3；同一鏡又說話又有心聲寫 S05、S05（心聲）。
    加註要和「核可用途」一致；其他加註、範圍寫法都算錯。回傳（[{id, file, sha, status, uses, row}], 錯誤）；沒有出聲的身份＝空表"""
    rows = _table(md, "Voice ID", "核可檔") or []
    ids = [r["Voice ID"] for r in rows]
    errors = [f"音色資產表 Voice ID 重複：{i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    has_purpose = not rows or "核可用途" in rows[0]
    if not has_purpose:
        errors.append("音色資產表要有「核可用途」欄")
    out = []
    for r in rows:
        vid, purpose, uses = r["Voice ID"], r.get("核可用途", ""), []
        for t in [t for t in re.split(SPLIT, r.get("使用鏡頭", "")) if t]:
            m = re.fullmatch(r"([^（()）]+)(?:[（(]([^）)]*)[）)])?", t)
            if not m or re.search(r"[–~〜～]|到|\w-\w", m.group(1)):
                errors.append(f"音色 {vid} 的使用鏡頭看不懂：{t}（逐一列鏡號，例：S05、S05（心聲））")
                continue
            note = m.group(2)
            if note not in (None, "心聲", "旁白"):
                errors.append(f"音色 {vid} 的使用鏡頭加註只能是心聲或旁白：{t}")
                continue
            if has_purpose and not (note in purpose if note else re.search(r"對白|畫外", purpose)):
                errors.append(f"音色 {vid} 的使用鏡頭 {t} 和核可用途「{purpose}」不一致")
            uses.append((m.group(1), note or "出聲"))
        f = canon_path(root, r["核可檔"])
        out.append({"id": vid, "file": f, "sha": _sha_if_file(root, f), "status": r.get("狀態"), "uses": uses,
                    "row": {k: x for k, x in r.items() if k not in RUNTIME_COLUMNS}})
    return out, errors


def card_materials(card):
    """分鏡卡「素材」那一行照接入順序拆開：首幀、尾幀、資產 ID、Voice ID。沒有這一行回傳 None"""
    m = re.search(r"^素材[：:](.*)$", card, re.M)
    return [t for t in re.split(SPLIT, m.group(1)) if t] if m else None


def split_materials(items, assets, voice_ids):
    """素材拆成：資產 ID（照順序＝參考圖的接入順序）、Voice ID（音色資產表的鏡像，照 <Audio N> 的順序）、看不懂的"""
    visual, voices, unknown = [], [], []
    for t in items:
        if t.startswith(("首幀", "尾幀")):
            continue
        (visual if t in assets else voices if t in voice_ids else unknown).append(t)
    return visual, voices, unknown


def _board_rows(md):
    return _table(md, "鏡號", "分鏡參考圖", "首幀資格")


# ---------- 指紋 ----------

def compute_shot_table_hash(rows):
    """鏡頭表的指紋：每一列照順序，「狀態」「重做次數」「備註」和衍生的「參考圖」以外的欄（照欄名，欄的先後不算）"""
    return _digest([{k: v for k, v in r.items() if k not in RUNTIME_COLUMNS + DERIVED_COLUMNS} for r in rows])


def compute_production_plan_hash(root, rows):
    """製作計畫的指紋：劇本（1-劇本.md 全文）、要進製作的每一鏡的分鏡卡全文、風格句那一列、美術段、這些鏡在「素材」引用到的資產
    （照資產表的 ID 找檔案、算內容，同一份只算一次）、使用鏡頭和這些鏡有交集的音色列（核可檔算內容）、每鏡用哪一張分鏡參考圖和首幀資格。
    沒被引用的資產、沒被使用的音色、敘事帳、關卡紀錄、首幀檔、各表的狀態欄不算。回傳（指紋或 None, 錯誤）"""
    md = _text(os.path.join(root, "2-分鏡.md"))
    story = _canon_text(_text(os.path.join(root, "1-劇本.md")))
    ids = [r.get("鏡號", "") for r in rows]
    errors = [f"鏡頭表的鏡號重複或空白：{s or '（空白）'}" for s in sorted({s for s in ids if not s or ids.count(s) > 1})]
    cards, dup = find_cards(md, ids)
    errors += [f"{s} 有兩張以上的分鏡卡，鎖定前只能留一張" for s in sorted(set(dup))]
    errors += [f"找不到 {s} 的分鏡卡（要有一行以「{s}｜」開頭）" for s in ids if s and s not in cards]
    style, art = style_parts(md)
    if style is None:
        errors.append("視覺聖經找不到「風格句」那一列")
    if art is None:
        errors.append("找不到美術段（以 **美術段 開頭的一段）")
    assets, e1 = asset_table(root, md)
    voices, e2 = voice_table(root, md)
    errors += e1 + e2
    boards = _board_rows(md)
    if boards is None:
        errors.append("找不到分鏡參考圖表（表頭要有「鏡號」「分鏡參考圖」「首幀資格」）")
    used = set()
    for s in ids:
        items = card_materials(cards[s]) if s in cards else []
        if items is None:
            errors.append(f"{s} 的分鏡卡沒有「素材：」那一行")
            continue
        visual, _, unknown = split_materials(items, assets, {v["id"] for v in voices})
        used |= set(visual)
        errors += [f"{s} 的素材引用不存在的資產 ID：{t}" for t in unknown]
    content = {}
    for a in sorted(used):
        sha = _sha_if_file(root, assets[a]["file"])
        content[a] = {"檔案": assets[a]["file"], "內容": sha}
        if sha is None:
            errors.append(f"資產 {a} 的檔不見了：{assets[a]['file']}")
    voice_part = []
    for v in sorted((v for v in voices if any(s in ids for s, _ in v["uses"])), key=lambda v: v["id"]):
        voice_part.append({**v["row"], "核可檔": v["file"], "內容": v["sha"]})
        if v["sha"] is None:
            errors.append(f"音色 {v['id']} 的核可檔找不到：{v['file']}")
    board_part = [{"鏡號": b["鏡號"], "分鏡參考圖": canon_path(root, b["分鏡參考圖"]), "首幀資格": b["首幀資格"]}
                  for s in ids for b in boards or [] if b.get("鏡號") == s]
    if errors:
        return None, errors
    return _digest({"劇本": story, "分鏡卡": [cards[s] for s in ids], "風格句": style, "美術段": art,
                    "資產": content, "音色": voice_part, "分鏡參考圖": board_part}), []


def compute_animatic_manifest_hash(root, manifest):
    """核可的時間軸檔（剪接設定）解析後比，加上它引用的每個圖檔、聲音檔的內容；輸出檔名 out 不算。回傳（指紋或 None, 錯誤）"""
    errors = []

    def ref(p):
        f = canon_path(root, p)
        if not os.path.isfile(_abs(root, f)):
            errors.append(f"Animatic 用到的檔不見了：{p}")
            return {"檔": f}
        return {"檔": f, "內容": file_sha(_abs(root, f))}

    cfg = json.loads(_text(_abs(root, manifest)))
    clips = []
    for c in cfg.get("clips", []):
        c = dict(zip(("file", "start", "end", "speed", "gain", "xfade"), c)) if isinstance(c, list) else dict(c)
        for k in ("image", "file"):
            if k in c:
                c[k] = ref(c[k])
        if c.get("bed") and c["bed"][0] != "roomtone":
            c["bed"] = [ref(c["bed"][0])] + c["bed"][1:]
        c["overlays"] = [[ref(o[0])] + o[1:] for o in c.get("overlays") or []]
        clips.append(c)
    top = {k: v for k, v in cfg.items() if k not in ("clips", "out") and v is not None}
    top["overlays"] = [[ref(o[0])] + o[1:] for o in top.get("overlays") or []]
    if top.get("music"):
        top["music"] = ref(top["music"])
    return (None if errors else _digest(_numbers({**top, "clips": clips}))), errors


def fingerprints(root, manifest):
    """現在的三個指紋。回傳（{名稱: 指紋或 None}, 錯誤）"""
    fp, errors = dict.fromkeys(HASHES), []
    try:
        rows = read_shot_table(root)
        fp["shot_table_hash"] = compute_shot_table_hash(rows)
        fp["production_plan_hash"], e = compute_production_plan_hash(root, rows)
        errors += e
    except FileNotFoundError as e:
        errors.append(f"找不到 {os.path.basename(e.filename)}")
    try:
        fp["animatic_manifest_hash"], e = compute_animatic_manifest_hash(root, manifest)
        errors += e
    except FileNotFoundError:
        errors.append(f"找不到時間軸檔：{manifest}")
    except ValueError as e:
        errors.append(f"時間軸檔 {manifest} 讀不懂：{e}")
    return fp, errors


# ---------- 鎖定前置 ----------

def _animatic_vs_table(root, manifest, rows, boards):
    """G5C 核可的時間軸檔要和鏡頭表一致：一鏡一段、label＝鏡號、順序同鏡頭表、image＝§10 那一鏡的分鏡參考圖、seconds＝鏡頭表的秒數
    （換成影格比，fps 照時間軸檔、預設 24）、不變速也不交叉淡化（speed 省略或 1、xfade 省略或 0，不然每段時長和下一段的起點會偏離鏡頭表）。
    漏鏡、多鏡、順序錯、錯圖、秒數不同、變速、淡化都回報；時間軸檔讀不了交給 fingerprints 報"""
    try:
        cfg = json.loads(_text(_abs(root, manifest)))
    except (FileNotFoundError, ValueError):
        return []
    fps, clips, errors = cfg.get("fps") or 24, cfg.get("clips") or [], []
    if len(clips) != len(rows):
        errors.append(f"時間軸檔有 {len(clips)} 段，鏡頭表有 {len(rows)} 鏡（要一鏡一段、順序一樣）")
    board = {b.get("鏡號"): b for b in boards}
    for i, (c, r) in enumerate(zip(clips, rows), 1):
        c, s = (c if isinstance(c, dict) else {}), r.get("鏡號", "")
        if c.get("label") != s:
            errors.append(f"時間軸檔第 {i} 段的 label 是「{c.get('label') or '沒寫'}」，鏡頭表第 {i} 鏡是 {s}（順序要一樣）")
            continue
        want_img = canon_path(root, board[s]["分鏡參考圖"]) if s in board and board[s].get("分鏡參考圖") else None
        if not c.get("image") or canon_path(root, str(c["image"])) != want_img:
            errors.append(f"時間軸檔 {s} 用的圖是 {c.get('image') or '沒寫'}，分鏡參考圖表是 {want_img or '沒登記'}")
        try:
            want_sec, got_sec = float(r.get("秒數", "")), float(c.get("seconds"))
            speed, xfade = float(c.get("speed", 1)), float(c.get("xfade", 0))
        except (TypeError, ValueError):
            errors.append(f"{s} 的秒數讀不出數字（鏡頭表「{r.get('秒數')}」、時間軸檔 seconds「{c.get('seconds')}」、speed「{c.get('speed')}」、xfade「{c.get('xfade')}」）")
            continue
        if speed != 1 or xfade != 0:
            errors.append(f"時間軸檔 {s} 有 speed {speed:g}／xfade {xfade:g}：Animatic 只用硬切、不變速（§11），這段的時長和下一段的起點會和鏡頭表對不上")
        if round(want_sec * fps) != round(got_sec * fps):
            errors.append(f"時間軸檔 {s} 是 {got_sec:g} 秒，鏡頭表是 {want_sec:g} 秒")
    return errors


def validate_lock_prerequisites(root, animatic, manifest):
    """Final Shot Lock 的前置（2-分鏡.md §12），全部從 2-分鏡.md 和鏡頭表讀，工單不當依據：
    關卡紀錄表 G3A＝LOCKED、G4C＝PASS、G5C＝PASS 而且依據就是這次要鎖的 Animatic 和時間軸檔；
    要進製作的鏡引用到的資產都核可、檔案在；用到的音色都核可、核可檔找得到；每鏡在分鏡參考圖表正好一列、參考圖核可、三項檢查都過；
    衍生欄位和正本一致（鏡頭表「參考圖」＝素材的資產 ID、素材的 Voice ID＝音色資產表在這一鏡出聲的、§10 首幀策略＝鏡頭表首幀來源）；
    時間軸檔和鏡頭表一鏡一段、順序、圖、秒數都對得上；三個指紋算得出來。回傳錯誤清單，有任何一項就不能鎖"""
    animatic, manifest = canon_path(root, animatic), canon_path(root, manifest)
    errors = [f"找不到{what}：{p}" for p, what in ((animatic, "核可的 Animatic"), (manifest, "它的時間軸檔"))
              if not os.path.isfile(_abs(root, p))]
    try:
        md, rows = _text(os.path.join(root, "2-分鏡.md")), read_shot_table(root)
    except FileNotFoundError as e:
        return errors + [f"找不到 {os.path.basename(e.filename)}"]
    record = _table(md, "關卡", "狀態")
    if record is None:
        errors.append("2-分鏡.md 找不到關卡紀錄表（表頭「關卡」「狀態」「依據」）")
    gates = {m.group(0): r for r in record or [] for m in [re.match(r"G\d+[A-D]?", r.get("關卡", "").strip())] if m}
    for gid, want in GATE_RECORD:
        if (gates.get(gid) or {}).get("狀態") != want:
            errors.append(f"關卡紀錄表的 {gid} 要是 {want}（現在是 {(gates.get(gid) or {}).get('狀態') or '沒寫'}）")
    basis = {canon_path(root, t) for t in re.split(SPLIT, (gates.get("G5C") or {}).get("依據", "")) if t}
    errors += [f"關卡紀錄表 G5C 的依據裡沒有這次要鎖的{what}：{p}" for p, what in ((animatic, " Animatic"), (manifest, "時間軸檔"))
               if p not in basis]
    ids = [r.get("鏡號", "") for r in rows]
    cards, _ = find_cards(md, ids)
    assets, e1 = asset_table(root, md)
    voices, e2 = voice_table(root, md)
    errors += e1 + e2
    voice_ids = {v["id"] for v in voices}
    boards = _board_rows(md) or []
    for row in rows:
        s = row.get("鏡號", "")
        visual, vids, _ = split_materials(card_materials(cards.get(s, "")) or [], assets, voice_ids)
        for a in visual:
            if assets[a]["status"] != "核可":
                errors.append(f"{s} 用的資產 {a} 狀態是「{assets[a]['status'] or '沒寫'}」，要是核可")
        spoken = [v["id"] for v in voices if (s, "出聲") in v["uses"]]
        if sorted(vids) != sorted(spoken):
            errors.append(f"{s} 素材寫的 Voice ID（{'、'.join(vids) or '無'}）和音色資產表在這一鏡出聲的（{'、'.join(spoken) or '無'}）不一致")
        if "參考圖" in row:
            mirror = [t for t in re.split(SPLIT, row["參考圖"]) if t in assets]
            if sorted(mirror) != sorted(visual):
                errors.append(f"{s} 鏡頭表「參考圖」（{'、'.join(mirror) or '無'}）和分鏡卡素材（{'、'.join(visual) or '無'}）不一致，以素材為準")
        mine = [b for b in boards if b.get("鏡號") == s]
        if len(mine) != 1:
            errors.append(f"分鏡參考圖表的 {s} 要正好一列（現在 {len(mine)} 列）")
            continue
        for col, want in (("參考圖狀態", "核可"), ("美術檢查", "過"), ("構圖檢查", "過"), ("敘事檢查", "過")):
            if mine[0].get(col) != want:
                errors.append(f"分鏡參考圖表 {s} 的{col}是「{mine[0].get(col) or '沒寫'}」，要是{want}")
        if mine[0].get("首幀策略", row.get("首幀來源")) != row.get("首幀來源"):
            errors.append(f"分鏡參考圖表 {s} 的首幀策略和鏡頭表「首幀來源」不一樣，以鏡頭表為準")
    for v in voices:
        if any(s in ids for s, _ in v["uses"]):
            if v["status"] != "核可":
                errors.append(f"音色 {v['id']} 狀態是「{v['status'] or '沒寫'}」，要是核可")
    errors += _animatic_vs_table(root, manifest, rows, boards)
    fp, e = fingerprints(root, manifest)
    return errors + [x for x in e if x not in errors]


# ---------- 關卡 ----------

def validate_project_gate(root=comfy.PROJ):
    """整部片的關卡：量產關卡.json 在、格式版本認得、state 是 LOCKED、核可的時間軸檔在、三個指紋都對得上現在的來源。
    只檢查不寫檔：指紋對不上就沒過，說出是哪一個（改回鎖定時的內容就又對得上）；要讓鎖失效用 invalidate。"""
    try:
        g = load_gate(root)
    except ValueError as e:
        return _result([("GATE_UNREADABLE", f"{GATE_FILE} 讀不懂：{e}")])
    if g is None:
        return _result([("GATE_MISSING", f"沒有 {GATE_FILE}：還沒有 Final Shot Lock（G5D）")])
    if g.get("schema_version") != SCHEMA_VERSION:
        return _result([("GATE_SCHEMA", f"{GATE_FILE} 的 schema_version 是 {g.get('schema_version')}，這支只認 {SCHEMA_VERSION}")])
    if g.get("state") != "LOCKED":
        return _result([("GATE_NOT_LOCKED", f"{GATE_FILE} 的 state 是 {g.get('state')}，不是 LOCKED")])
    manifest = g.get("approved_animatic_manifest") or ""
    if not os.path.isfile(_abs(root, manifest)):
        return _result([("GATE_MANIFEST_MISSING", f"核可的時間軸檔不見了：{manifest}")])
    fp, errors = fingerprints(root, manifest)
    fails = [("SOURCE_UNREADABLE", e) for e in errors]
    fails += [(code, f"{name}和鎖定時不一樣") for k, (code, name) in HASHES.items() if fp[k] and fp[k] != g.get(k)]
    return _result(fails)


def used_takes(root, exc_id):
    """例外用掉的條數＝真的送到 ComfyUI 的條數：佇列帳本和生成紀錄裡記著這個例外、而且有 prompt_id 的（排了又取消、還沒送出的不算）"""
    sent = set()
    qdir = os.path.join(root, "_腳本", "佇列")
    for n in os.listdir(qdir) if os.path.isdir(qdir) else []:
        if n.endswith(".json"):
            j = json.loads(_text(os.path.join(qdir, n)))
            if j.get("meta", {}).get("例外") == exc_id and j.get("prompt_id"):
                sent.add(j["prompt_id"])
    log = os.path.join(root, "_腳本", "生成紀錄.jsonl")
    for line in _text(log).split("\n") if os.path.exists(log) else []:
        if line.strip():
            r = json.loads(line)
            if r.get("例外") == exc_id and r.get("prompt_id"):
                sent.add(r["prompt_id"])
    return len(sent)


def user_text_problem(text, shots, word):
    """使用者的授權原話要有關鍵字（「例外」或「鎖定」）和範圍內每個鏡號，才算明確授權（2-分鏡.md §12）。
    這擋的是「現在就送」「OK 繼續」這種隨口一句被當成授權，擋不住刻意把字打進去。回傳問題說明；沒問題回傳 None"""
    text = text if isinstance(text, str) else ""
    if word not in text:
        return f"沒有「{word}」這個字（現在是「{text[:60]}」）"
    missing = [s for s in shots if s and s not in text]
    if missing:
        return f"沒有提到鏡號 {'、'.join(missing)}（現在是「{text[:60]}」）"
    return None


def validate_exception(gate, exc_id, shot, profile, used):
    """例外：鏡、檔位、剩下的條數全部符合才放行；範圍要列出鏡號，沒有整個專案關掉關卡這種例外；使用者原話要有「例外」和鏡號"""
    exc = next((e for e in (gate or {}).get("exceptions", []) if e.get("id") == exc_id), None)
    if exc is None:
        return _result([("EXC_NOT_FOUND", f"{GATE_FILE} 沒有例外 {exc_id}")])
    scope = exc.get("scope") or {}
    missing = [k for k in ("reason", "authorized_by_user", "timestamp") if not exc.get(k)]
    missing += [f"scope.{k}" for k in ("shots", "profiles", "takes") if not scope.get(k)]
    if missing:
        return _result([("EXC_INCOMPLETE", f"例外 {exc_id} 缺 {'、'.join(missing)}")])
    if any("*" in s or s.lower() in ("all", "全部") for s in scope["shots"]):
        return _result([("EXC_SCOPE_TOO_WIDE", f"例外 {exc_id} 的 shots 要列出鏡號，不能是全部")])
    bad = user_text_problem(exc.get("authorized_by_user"), scope["shots"], "例外")
    if bad:
        return _result([("EXC_USER_TEXT", f"例外 {exc_id} 的使用者原話{bad}：要像「例外生成 S05 草稿 2 條」那樣明確")])
    fails = []
    if shot not in scope["shots"]:
        fails.append(("EXC_SHOT", f"例外 {exc_id} 只涵蓋 {'、'.join(scope['shots'])}，不含 {shot}"))
    if profile not in scope["profiles"]:
        fails.append(("EXC_PROFILE", f"例外 {exc_id} 只涵蓋檔位 {'、'.join(scope['profiles'])}，這條是 {profile}"))
    if used >= int(scope["takes"]):
        fails.append(("EXC_TAKES_USED_UP", f"例外 {exc_id} 的 {scope['takes']} 條已經送出 {used} 條"))
    return _result(fails)


def parse_generation(text):
    """分鏡卡「生成」那一行：不拆段＝{"": {"mode", "frames"}}；拆段寫「A 段 …；B 段 …」，B 段沒寫模式就跟 A 段一樣，幀數兩段都要寫，
    B 段還要寫「首幀＝A 第 N 格」（first）。模式：Ref2VA／參考模式＝ref，I2VA／FL2VA＝i2v；幀數＝「N 幀」（「第 N 幀」不算）。回傳（{段: …}, 錯誤）"""
    parts = re.split(r"(?:^|[；;\s])([AB])\s*段", text)
    segs = {"": parts[0]} if len(parts) == 1 else {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}
    out, errors = {}, []
    for name, t in segs.items():
        t = t.split("成片取")[0]
        frames = re.search(r"(?<![第\d])(?<!第 )(\d+)\s*幀", t)
        first = re.search(r"首幀\s*[＝=]\s*A\s*(?:段)?\s*第\s*(\d+)\s*格", t)
        out[name] = {"mode": "i2v" if re.search(r"I2VA|FL2VA", t) else "ref" if re.search(r"Ref2VA|參考模式", t) else None,
                     "frames": int(frames.group(1)) if frames else None, "first": int(first.group(1)) if first else None}
    if "B" in out and out["B"]["mode"] is None:
        out["B"]["mode"] = out.get("A", {}).get("mode")
    for name, s in out.items():
        where = f"{name} 段" if name else "「生成」"
        if s["mode"] is None:
            errors.append(f"{where}讀不出模式（Ref2VA／參考模式／I2VA）")
        if s["frames"] is None:
            errors.append(f"{where}讀不出幀數（寫成「N 幀」）")
        if name == "B" and s["first"] is None:
            errors.append("B 段讀不出「首幀＝A 第 N 格」")
    return out, errors


def parse_first_source(src):
    """鏡頭表「首幀來源」：接力 S03 末格／借 S08 第 0 格（來源鏡有拆段可加段：接力 S03 B 段末格）／合成／無。
    回傳（策略, 來源鏡, 來源段或 None, 格；末格＝−1）；合成、無的後三項是 None；看不懂回傳 None"""
    s = (src or "").strip()
    for key, strategy in STRATEGIES:
        if s.startswith(key):
            if strategy in ("composite", "none"):
                return strategy, None, None, None
            m = re.fullmatch(re.escape(key) + r"\s*(\S+?)\s*(?:([AB])\s*段)?\s*(?:(末格)|第\s*(\d+)\s*格)", s)
            if not m:
                return None
            return strategy, m.group(1), m.group(2), -1 if m.group(3) else int(m.group(4))
    return None


GEN_LINE = re.compile(r"^生成[：:](.*(?:\n(?:\s|[AB]\s*段|成片取).*)*)", re.M)
RANGE = r"(\d+(?:\.\d+)?)\s*[–\-~～—]\s*(\d+(?:\.\d+)?)\s*秒"


def card_generation(card):
    """分鏡卡「生成：」那一行（含接下來以空白、A 段／B 段、成片取開頭的續行）；沒有回傳 None"""
    m = GEN_LINE.search(card or "")
    return m.group(1) if m else None


def parse_take(text):
    """「生成」那一行裡的成片取（2-分鏡.md §4、§5）：不拆段寫 成片取 0.5–5.5 秒 → {"": (0.5, 5.5)}；
    拆段寫 成片取 A 0–4 秒＋B 0–1.5 秒 → {"A": (0, 4), "B": (0, 1.5)}。回傳（{段: (起, 迄)}, 錯誤）"""
    if "成片取" not in (text or ""):
        return {}, ["「生成」那一行沒寫成片取（例：成片取 0.5–5.5 秒；拆段：成片取 A 0–4 秒＋B 0–1.5 秒）"]
    out, errors = {}, []
    for m in re.finditer(r"(?:([AB])\s*段?\s*)?" + RANGE, text.split("成片取", 1)[1]):
        seg, a, b = m.group(1) or "", float(m.group(2)), float(m.group(3))
        if seg in out:
            errors.append(f"成片取的 {seg or '不拆段'} 寫了兩次")
        if b <= a:
            errors.append(f"成片取 {a:g}–{b:g} 秒：迄秒要大於起秒")
        out[seg] = (a, b)
    if not out:
        errors.append("成片取讀不出秒數（寫成 a–b 秒）")
    return out, errors


def parse_dialogue_window(card):
    """分鏡卡裡機器讀的台詞窗（動作卡 J）：台詞窗 0.5–3.5 秒；拆段的鏡寫 台詞窗 B 0.3–1.3 秒。回傳 {段: (起, 迄)}；沒寫就空"""
    return {(m.group(1) or ""): (float(m.group(2)), float(m.group(3)))
            for m in re.finditer(r"台詞窗\s*(?:([AB])\s*段?\s*)?" + RANGE, card or "")}


def _segments(cards, shot):
    """這一鏡分鏡卡「生成」那一行解析出的段。回傳（{段: …}, 錯誤）"""
    gen = card_generation(cards.get(shot, ""))
    if gen is None:
        return {}, ["分鏡卡找不到「生成：」那一行"]
    return parse_generation(gen)


def dialogue_lines(cell):
    """鎖定鏡頭表「台詞(逐字)」那一格拆成一句一句：換行或「｜」分句，去掉開頭的「角色名：」；沒台詞回傳 []"""
    out = []
    for piece in re.split(r"[\n｜|]", cell or ""):
        piece = re.sub(r"^[^：:]{1,12}[：:]\s*", "", piece.strip())
        if piece:
            out.append(piece)
    return out


def _norm_line(s):
    """比對台詞前正規化：去掉內嵌標籤和 [Chinese] 這種語言標、去掉空白，半形 ,.?! 當全形"""
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"^\s*\[[A-Za-z\- ]+\]\s*", "", s)
    return re.sub(r"\s+", "", s).translate(str.maketrans(",.?!", "，。？！"))


def prompt_dialogue(text):
    """提示詞裡每個 <d>…</d> 的台詞（正規化後，照出現順序）"""
    return [_norm_line(m) for m in re.findall(r"<d>(.*?)</d>", text, re.S)]


def _match_dialogue(plan, prompt, seg):
    """提示詞的台詞照鎖定的鏡頭表（4-生成與驗片.md §1）：每句 <d> 都要是這一鏡「台詞(逐字)」裡的一句、一字不差；
    鏡頭表沒台詞的鏡不能有 <d>；不拆段的鏡每句都要在（拆段的鏡台詞可能分在 A、B 段，只查有的句子對不對）"""
    want = [_norm_line(l) for l in plan["lines"]]
    got = prompt_dialogue(prompt)
    if not want and got:
        return [("DIALOGUE_UNPLANNED", f"鎖定的鏡頭表 {plan['shot']} 沒有台詞，提示詞卻有 {len(got)} 句 <d>：台詞只有使用者能加")]
    fails = []
    for g in got:
        if g not in want:
            fails.append(("DIALOGUE_MISMATCH", f"提示詞的台詞「{g[:40]}」不在鎖定鏡頭表 {plan['shot']} 的台詞（{'／'.join(plan['lines'])}）裡：台詞照定稿一字不改"))
    if not fails and "B" not in plan["segments"] and not seg:
        missing = [l for l, w in zip(plan["lines"], want) if w not in got]
        if missing:
            fails.append(("DIALOGUE_MISSING", f"鎖定鏡頭表 {plan['shot']} 的台詞「{'／'.join(missing)}」沒寫進提示詞：少一句也是改了故事"))
    return fails


def _ledger_job(root, job_id):
    """佇列帳本裡的一條（_腳本/佇列/編號.json）；沒有就 None"""
    path = os.path.join(root, "_腳本", "佇列", f"{job_id}.json")
    return json.loads(_text(path)) if os.path.isfile(path) else None


def run_lint(path, text, facts):
    """提示詞 lint：設定的外部版本存在就用它（命令列同 3-提示詞.md §5：提示詞檔、--refs 掛進去的參考圖檔名（去掉副檔名，照接入順序）、--frames；
    離開碼 1＝有 ❌），不在就用內附的 h3_prompt_lint。排進來和送出前都走這裡。回傳 ❌ 清單"""
    refs = facts["refs"] if facts["mode"] == "ref" else facts["images"]
    names = [os.path.splitext(os.path.basename(n))[0] for _, n in refs if n]
    ext = comfy.CFG.get("lint")
    if ext and os.path.isfile(ext):
        cmd = [sys.executable, ext, path, "--frames", str(facts["frames"])] + (["--refs", ",".join(names)] if names else [])
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode == 0:
            return []
        lines = [l for l in (p.stdout + p.stderr).splitlines() if l.strip()]
        return [f"外部 lint {os.path.basename(ext)} 離開碼 {p.returncode}：{lines[-1] if lines else '沒有輸出'}"]
    errors, _ = h3_prompt_lint.lint(text, n_refs=facts["n_refs"], ref_names=tuple(names), frames=facts["frames"])
    return errors


def shot_plan(root, shot):
    """鎖定的計畫裡這一鏡怎麼生成：首幀策略和來源（鏡頭表「首幀來源」）、每一段的模式、幀數和 B 段的接點（分鏡卡「生成」）、合成鏡的首幀檔（§10）、
    素材（資產 ID 和 Voice ID，照接入順序）和每個資產現在的狀態。回傳（計畫或 None, [(代碼, 說明)]）"""
    rows = read_shot_table(root)
    row = next((r for r in rows if r.get("鏡號") == shot), None)
    if row is None:
        return None, [("SHOT_NOT_IN_PLAN", f"鎖定的鏡頭表裡沒有 {shot}")]
    src = parse_first_source(row.get("首幀來源"))
    if src is None:
        return None, [("PLAN_UNREADABLE", f"{shot} 的首幀來源「{row.get('首幀來源')}」看不懂（接力 S01 末格／借 S08 第 0 格／合成／無）")]
    strategy, src_shot, src_seg, src_frame = src
    md = _text(os.path.join(root, "2-分鏡.md"))
    cards, _ = find_cards(md, [r.get("鏡號", "") for r in rows])
    segs, errors = _segments(cards, shot)
    if errors:
        return None, [("PLAN_UNREADABLE", f"{shot} 的{e}") for e in errors]
    if src_shot and src_seg is None:   # 來源鏡有拆段、首幀來源沒寫段：末格是 B 段的，第 N 格是 A 段的
        src_seg = ("B" if src_frame == -1 else "A") if "B" in _segments(cards, src_shot)[0] else ""
    board = next((b for b in _board_rows(md) or [] if b.get("鏡號") == shot), {})
    assets, _ = asset_table(root, md)
    voices, errors = voice_table(root, md)
    if errors:
        return None, [("PLAN_UNREADABLE", e) for e in errors]
    visual, vids, _ = split_materials(card_materials(cards[shot]) or [], assets, {v["id"] for v in voices})
    return {"shot": shot, "strategy": strategy, "source": (src_shot, src_seg, src_frame),
            "lines": dialogue_lines(row.get("台詞(逐字)") or row.get("台詞") or ""),
            "first_file": canon_path(root, board["首幀檔"]) if board.get("首幀檔") else None,
            "segments": segs, "assets": [(a, assets[a]["file"]) for a in visual], "asset_status": {a: assets[a]["status"] for a in visual},
            "approved_assets": {sha: a for a, x in assets.items() if x["status"] == "核可" for sha in [_sha_if_file(root, x["file"])] if sha},
            "voices": vids, "need_voices": [v["id"] for v in voices if (shot, "出聲") in v["uses"]],
            "voice_sha": {v["id"]: v["sha"] for v in voices},
            "approved_voices": {v["sha"]: v["id"] for v in voices if v["sha"] and v["status"] == "核可"}}, []


def _planned_first(plan, seg):
    """這一段的首幀照鎖定的計畫該從哪裡來：("relay", 來源鏡, 來源段, 格)、("file", §10 的首幀檔) 或 ("none",)"""
    if seg == "B":
        return ("relay", plan["shot"], "A", plan["segments"]["B"]["first"])
    if plan["strategy"] in ("relay", "borrow"):
        return ("relay",) + plan["source"]
    if plan["strategy"] == "composite":
        return ("file", plan["first_file"])
    return ("none",)


def _where(shot, seg, frame):
    return f"{shot}{f' {seg} 段' if seg else ''}{'末格' if frame == -1 else f'第 {frame} 格'}"


def _match_first_frame(root, plan, facts, seg, input_dir, after, src_job):
    """首幀照計畫：該接力的要有 after，接的是計畫寫的那一鏡（段）那一格；合成的釘 §10 的首幀檔（比內容）；「無」不釘也不接。
    換圖節點＝釘首幀的節點、送出前釘的就是取到的那一格，在 validate_shot_preflight 對每一條（含例外）都檢查"""
    want, first = _planned_first(plan, seg), facts["first_frame"]
    if want[0] == "none":
        fails = [("FIRST_FRAME_UNEXPECTED", "首幀策略是「無」，節點圖卻釘了首幀")] if first is not None else []
        return fails + ([("RELAY_SOURCE_MISMATCH", "首幀策略是「無」，不接力，卻給了 after")] if after else [])
    if first is None:
        return [("FIRST_FRAME_MISSING", "這一鏡要首幀（首幀策略或 B 段），節點圖沒有釘首幀")]
    if want[0] == "file":
        if after:
            return [("RELAY_SOURCE_MISMATCH", "首幀策略是「合成」，不接力，卻給了 after")]
        if not want[1] or not os.path.isfile(_abs(root, want[1])):
            return [("PLAN_UNREADABLE", f"{plan['shot']} 的首幀策略是合成，分鏡參考圖表的「首幀檔」要是找得到的檔案（現在是 {want[1] or '沒寫'}）")]
        name = dict(facts["images"]).get(first)
        sha = _sha_if_file(input_dir, name) if name else None
        if sha and sha != file_sha(_abs(root, want[1])):
            return [("FIRST_FRAME_MISMATCH", f"釘的首幀不是分鏡參考圖表的首幀檔 {want[1]}（比內容）")]
        return []
    _, s_shot, s_seg, s_frame = want
    if not after:
        return [("RELAY_REQUIRED", f"這一鏡的首幀要接 {_where(s_shot, s_seg, s_frame)}，要用佇列的 after")]
    fails = []
    if src_job is None:
        fails.append(("RELAY_SOURCE_MISMATCH", f"帳本裡找不到 after 的前一條 {after[0]}"))
    else:
        m = src_job.get("meta", {})
        got = (m.get("鏡"), m.get("段") or "")
        if got != (s_shot, s_seg or ""):
            fails.append(("RELAY_SOURCE_MISMATCH", f"計畫要接 {_where(s_shot, s_seg, s_frame)}，after 的前一條 {after[0]} 是 {got[0] or '不是 H3 鏡'}{f' {got[1]} 段' if got[1] else ''}"))
    if after[1] != s_frame:
        fails.append(("RELAY_SOURCE_MISMATCH", f"計畫取{'末格' if s_frame == -1 else f'第 {s_frame} 格'}，after 取的是{'末格' if after[1] == -1 else f'第 {after[1]} 格'}"))
    return fails


def _match_plan(root, plan, facts, seg, input_dir, after, src_job):
    """節點圖和鎖定的計畫比：拆段、模式、幀數、首幀來源；參考圖照素材的資產 ID 和順序（釘幀的圖另算）、每個資產現在都要是核可；音色照音色資產表"""
    segs = plan["segments"]
    if seg not in segs:
        want = "、".join(f"{s} 段" for s in segs if s) or "不拆段"
        return [("SPLIT_MISMATCH", f"鎖定的分鏡卡是{want}，這條寫的是{f'{seg} 段' if seg else '不拆段'}")]
    s, fails = segs[seg], []
    names = {"ref": "Ref2VA", "i2v": "I2VA"}
    if s["mode"] != facts["mode"]:
        fails.append(("MODE_MISMATCH", f"鎖定的是 {names[s['mode']]}，節點圖是 {names[facts['mode']]}"))
    if s["frames"] != facts["frames"]:
        fails.append(("FRAMES_MISMATCH", f"鎖定的是 {s['frames']} 幀，節點圖是 {facts['frames']} 幀"))
    fails += _match_first_frame(root, plan, facts, seg, input_dir, after, src_job)
    for a, _ in plan["assets"]:
        if plan["asset_status"].get(a) != "核可":
            fails.append(("ASSET_UNAPPROVED", f"資產 {a} 現在的狀態是「{plan['asset_status'].get(a) or '沒寫'}」，要是核可"))

    def shas(refs):
        """掛的檔的內容；不是直接讀檔的節點算空字串（對不上任何核可檔）；檔不在的 REF_MISSING 已經報過，不重複算"""
        return [s for s in ("" if n is None else _sha_if_file(input_dir, n) for _, n in refs) if s is not None]

    # 參考圖：素材的資產 ID 要全掛、不掛別的、照順序（<Picture N> 跟著這個順序）
    want = [_sha_if_file(root, f) for _, f in plan["assets"]]
    got, n0 = shas(facts["visual_refs"]), len(fails)
    for sha in got:
        if sha not in want:
            a = plan["approved_assets"].get(sha)
            fails.append(("ASSET_WRONG_ID", f"掛了 {a}，這一鏡素材沒有它") if a else ("ASSET_UNAPPROVED", "掛了資產表裡沒有、或還沒核可的圖"))
    missing = [a for (a, _), sha in zip(plan["assets"], want) if sha not in got]
    if missing:
        fails.append(("ASSET_REQUIRED_MISSING", f"素材要掛 {'、'.join(missing)}，節點圖沒掛"))
    if len(fails) == n0 and got != want:
        fails.append(("ASSET_ORDER_MISMATCH", f"參考圖的接入順序和素材（{'、'.join(a for a, _ in plan['assets'])}）不一樣，<Picture N> 會對錯"))
    # 音色：音色資產表在這一鏡出聲的都要掛、只能掛這些、照素材的 Voice ID 順序（<Audio N>）
    got, n0 = shas(facts["audios"]), len(fails)
    for sha in got:
        vid = plan["approved_voices"].get(sha)
        if vid is None:
            fails.append(("VOICE_UNAPPROVED", "掛了音色資產表裡沒有、或還沒核可的音色檔"))
        elif vid not in plan["need_voices"]:
            fails.append(("VOICE_WRONG_ID", f"掛了 {vid}，這一鏡要的是 {'、'.join(plan['need_voices']) or '不掛音色'}"))
    missing = [v + ("（核可檔不是檔案，比對不了）" if plan["voice_sha"].get(v) is None else "")
               for v in plan["need_voices"] if plan["voice_sha"].get(v) not in got]
    if missing:
        fails.append(("VOICE_REQUIRED_MISSING", f"這一鏡要掛 {'、'.join(missing)}，節點圖沒掛"))
    if len(fails) == n0 and got != [plan["voice_sha"].get(v) for v in plan["voices"]]:
        fails.append(("VOICE_ORDER_MISMATCH", f"音色的接入順序和素材（{'、'.join(plan['voices'])}）不一樣，<Audio N> 會對錯"))
    return fails


def validate_shot_preflight(facts, meta, stage, root=comfy.PROJ, input_dir=comfy.INP, after=None, relay_frame=None, job=None):
    """H3 影片的送件前檢查（4-生成與驗片.md §1）。佇列在排進來（stage="add"）和真的送到 ComfyUI 前（stage="dispatch"）各跑一次。
    facts＝comfy.h3_video_facts(節點圖)；meta＝送件時的 {"鏡", "提示詞", "段", "例外"}；
    after＝佇列的 (前一條, 第幾格, 要換圖的 LoadImage 節點)，接力、借格、B 段一定要有；relay_frame＝接力取到的那一格（送出前才有）；job＝佇列條目編號。
    送出前那一次（stage="dispatch"、有 job）過了，才發一次性的通行證 permit（綁專案、條目、節點圖），comfy._post 只認它。
    回傳 {"ok", "reasons", "messages", "message", "graph", "exception", "stage"(, "permit")}：reasons 是機器讀的代碼，message 給人看。"""
    fails = []
    shot, seg, exc_id = meta.get("鏡"), meta.get("段") or "", meta.get("例外")
    if not shot:
        fails.append(("META_SHOT", "送件的 meta 要寫「鏡」（鏡號）"))
    # 1. 整部片的關卡，或範圍內的例外
    if exc_id:
        try:
            gate = load_gate(root)
        except ValueError as e:
            gate = None
            fails.append(("GATE_UNREADABLE", f"{GATE_FILE} 讀不懂：{e}"))
        r = validate_exception(gate, exc_id, shot, facts["profile"], used_takes(root, exc_id))
    else:
        r = validate_project_gate(root)
    fails += list(zip(r["reasons"], r["messages"]))
    # 2、3. 編譯好的提示詞在、和節點圖裡的一樣、lint 沒有 ❌
    prompt = meta.get("提示詞")
    path = _abs(root, canon_path(root, prompt)) if prompt else None
    if not path or not os.path.isfile(path):
        fails.append(("PROMPT_MISSING", f"找不到編譯好的提示詞：meta 的「提示詞」要指到 3-提示詞\\ 裡的檔（現在是 {prompt}）"))
    else:
        text = _text(path)
        if _canon_text(text) != _canon_text(facts["prompt"].replace("\r\n", "\n")):
            fails.append(("PROMPT_MISMATCH", f"節點圖裡的提示詞和 {prompt} 不一樣"))
        errors = run_lint(path, text, facts)
        if errors:
            fails.append(("LINT_FAIL", f"{prompt} 的 lint 有 {len(errors)} 個 ❌：{errors[0]}"))
    # 4. 要掛的圖和聲音都在 ComfyUI input（接力的那一格排進來時還沒取，送出前再看）
    relay_node = after[2] if after else None
    for node, name in facts["images"] + facts["audios"]:
        if name and not (stage == "add" and node == relay_node) and not os.path.isfile(os.path.join(input_dir, name)):
            fails.append(("REF_MISSING", f"ComfyUI input 裡沒有 {name}"))
    # 7. 給了 after：換圖的節點要是釘首幀的節點；送出前要取到那一格（cut 檢查過或人 release），而且釘的就是它。例外也一樣
    if after:
        if relay_node != facts["first_frame"]:
            fails.append(("RELAY_SOURCE_MISMATCH", f"after 要換圖的節點 {relay_node} 不是釘首幀的節點（{facts['first_frame'] or '沒釘首幀'}）"))
        if stage == "dispatch":
            if not (relay_frame and os.path.isfile(relay_frame)):
                fails.append(("RELAY_UNRESOLVED", "接力的那一格還沒取到"))
            else:
                name = dict(facts["images"]).get(relay_node)
                if not name or _sha_if_file(input_dir, name) != file_sha(relay_frame):
                    fails.append(("FIRST_FRAME_MISMATCH", "釘的首幀不是接力取到的那一格（比內容）"))
    # 5、6、8–10. 照鎖定的計畫比（關卡過了才比；例外是計畫以外的測試，不比計畫）
    if shot and not exc_id and r["ok"]:
        plan, errors = shot_plan(root, shot)
        fails += errors
        if plan:
            fails += _match_plan(root, plan, facts, seg, input_dir, after, _ledger_job(root, after[0]) if after else None)
            fails += _match_dialogue(plan, facts["prompt"], seg)
    res = _result(fails, graph=facts["key"], exception=exc_id, stage=stage)
    if res["ok"] and stage == "dispatch" and job:
        res["permit"] = comfy._issue_permit(job, facts["key"])
    return res


# ---------- 寫關卡檔（只有命令列會呼叫）----------

def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _write(root, g):
    path = os.path.join(root, GATE_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def lock(root, animatic, manifest, user):
    """Final Shot Lock：使用者說了「鎖定」之後才呼叫，user＝他的原話和日期（要有「鎖定」）。前置沒齊就不鎖；
    齊了才算三個指紋寫進 量產關卡.json（state＝LOCKED、locked_by_user＝原話），原有的例外留著。回傳錯誤清單（空＝鎖好了）"""
    bad = user_text_problem(user, [], "鎖定")
    if bad:
        return [f"要使用者說了「鎖定」才能鎖：--user 的原話{bad}"]
    errors = validate_lock_prerequisites(root, animatic, manifest)
    if errors:
        return errors
    animatic, manifest = canon_path(root, animatic), canon_path(root, manifest)
    fp, errors = fingerprints(root, manifest)
    if errors:
        return errors
    old = load_gate(root) or {}
    _write(root, {"schema_version": SCHEMA_VERSION, "state": "LOCKED", "locked_at": _now(), "locked_by_user": user,
                  "approved_animatic": animatic, "approved_animatic_manifest": manifest, **fp, "exceptions": old.get("exceptions", [])})
    return []


def invalidate(root, reason):
    """使用者要改鎖定的內容：state 改成 INVALID（只能單向）。回傳原本的 state"""
    g = load_gate(root)
    if g is None:
        return None
    old, g["state"] = g.get("state"), "INVALID"
    _write(root, g)
    print(f"{GATE_FILE}：state {old} → INVALID（{reason}）", file=sys.stderr)
    return old


def add_exception(root, shots, profiles, takes, reason, user):
    """記一條例外，編號自動給。範圍要列出鏡號、檔位、條數；沒有鎖的專案也能記（state＝INVALID）。回傳例外；範圍不對丟 ValueError"""
    known = list(comfy.PROFILES) + ["i2v"]
    if not shots or any("*" in s or s.lower() in ("all", "全部") for s in shots):
        raise ValueError("shots 要逐一列出鏡號，不能是全部")
    if not profiles or any(p not in known for p in profiles):
        raise ValueError(f"profiles 只能是 {'、'.join(known)}")
    if not isinstance(takes, int) or takes < 1:
        raise ValueError("takes 至少 1")
    if not reason or not user:
        raise ValueError("要寫原因（reason）和使用者授權的原話（user）")
    bad = user_text_problem(user, shots, "例外")
    if bad:
        raise ValueError(f"使用者原話{bad}：要像「例外生成 S01 草稿 3 條」那樣，有「例外」和每個鏡號；「現在就送」「先跑看看」不算授權")
    g = load_gate(root) or {"schema_version": SCHEMA_VERSION, "state": "INVALID", "exceptions": []}
    n = max([int(e["id"][4:]) for e in g.get("exceptions", []) if re.fullmatch(r"EXC-\d+", e.get("id", ""))] + [0]) + 1
    exc = {"id": f"EXC-{n:03d}", "scope": {"shots": shots, "profiles": profiles, "takes": takes},
           "reason": reason, "authorized_by_user": user, "timestamp": _now()}
    g.setdefault("exceptions", []).append(exc)
    _write(root, g)
    return exc


def main():
    ap = argparse.ArgumentParser(description="量產關卡（G5D）")
    ap.add_argument("--project", default=comfy.PROJ, help="專案資料夾（預設＝_腳本 的上一層）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("fingerprint").add_argument("--manifest", help="還沒鎖時，指定要算的時間軸檔")
    for name in ("ready", "lock"):
        p = sub.add_parser(name)
        p.add_argument("--animatic", required=True)
        p.add_argument("--manifest", required=True)
        if name == "lock":
            p.add_argument("--user", required=True, help="使用者說「鎖定」的原話和日期（要有「鎖定」）")
    sub.add_parser("invalidate").add_argument("--reason", required=True)
    p = sub.add_parser("exception")
    p.add_argument("--shots", required=True)
    p.add_argument("--profiles", required=True)
    p.add_argument("--takes", type=int, required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--user", required=True, help="使用者授權的原話和日期")
    a = ap.parse_args()
    root = os.path.abspath(a.project)

    if a.cmd == "check":
        r = validate_project_gate(root)
        print("關卡：" + ("通過" if r["ok"] else "沒過"))
        for code, msg in zip(r["reasons"], r["messages"]):
            print(f"  ❌ {code}：{msg}")
        try:
            exceptions = (load_gate(root) or {}).get("exceptions", [])
        except ValueError:
            exceptions = []
        for e in exceptions:
            s = e.get("scope", {})
            print(f"  例外 {e.get('id')}：{'、'.join(s.get('shots', []))}／{'、'.join(s.get('profiles', []))}，"
                  f"已送出 {used_takes(root, e.get('id'))}／{s.get('takes')} 條（{e.get('reason')}）")
        sys.exit(0 if r["ok"] else 1)
    if a.cmd == "fingerprint":
        manifest = a.manifest or (load_gate(root) or {}).get("approved_animatic_manifest")
        if not manifest:
            print("❌ 還沒有鎖：用 --manifest 指定時間軸檔")
            sys.exit(2)
        fp, errors = fingerprints(root, canon_path(root, manifest))
        for k, v in fp.items():
            print(f"{k}: {v}")
        for e in errors:
            print(f"❌ {e}")
        sys.exit(1 if errors else 0)
    if a.cmd in ("ready", "lock"):
        errors = (validate_lock_prerequisites(root, a.animatic, a.manifest) if a.cmd == "ready"
                  else lock(root, a.animatic, a.manifest, a.user))
        for e in errors:
            print(f"❌ {e}")
        if not errors:
            print("前置都齊了，可以給使用者看鎖定卡" if a.cmd == "ready" else f"已鎖定：{GATE_FILE}（{canon_path(root, a.animatic)}）")
        sys.exit(1 if errors else 0)
    if a.cmd == "invalidate":
        old = invalidate(root, a.reason)
        print("沒有 量產關卡.json，沒有鎖可以失效" if old is None else f"state：{old} → INVALID")
        sys.exit(1 if old is None else 0)
    if a.cmd == "exception":
        try:
            exc = add_exception(root, [s.strip() for s in a.shots.split(",") if s.strip()],
                                [p.strip() for p in a.profiles.split(",") if p.strip()], a.takes, a.reason, a.user)
        except ValueError as e:
            print(f"❌ {e}")
            sys.exit(1)
        print(f"已記例外 {exc['id']}：{json.dumps(exc['scope'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
