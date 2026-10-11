"""圖像檢查：分鏡圖提示詞送件前必過（references/3-分鏡.md §4）。有 ❌ 就不送（結束碼 1）；⚠️ 列出那一句，人看過再送。
用法：python 圖像檢查.py 提示詞檔 --refs 檔1 檔2 … --row 鏡號 [--project 專案資料夾] [--table 2-鏡頭表.csv] [--assets 2-資產.md]
  --refs：實際要掛的檔，專案相對路徑、照掛的順序（第一個＝<image1> 畫布）
查什麼：
  ❌ 開頭不是畫風規範那一句（只能差光態）
  ❌ 提示詞的景別詞 ≠ 鏡頭表「景別」
  ❌ <imageN> 最大編號 ≠ 掛的張數；張數不在 2–4；有圖沒被引用
  ❌ 同一角色的檔掛兩次（ID 的底名相同，例：C01_body 和 C01_face）
  ❌ 掛的檔不在 已核可/ 底下，或不存在；掛的 ID 順序 ≠ 鏡頭表「參考圖」
  ❌ 提示詞裡每個人的左右 ≠ 鏡頭表「站位」
  ⚠️ 開頭句之後超過 120 個英文字；含補丁句；開頭句之後又出現畫質形容
"""
import argparse, csv, os, re, sys

sys.stdout.reconfigure(encoding="utf-8")

STYLE = re.compile(r"^Premium lifestyle photoreal frame, (.+?), clean polished finish, warm neutral low-saturation colors with soft contrast, "
                   r"smooth retouched skin with subtle texture, soft airy hair, no film grain\.")   # presets/畫風.md 分鏡圖風格句
SIZES = [("遠景", "extreme wide shot"), ("特寫", "extreme close-up"), ("中近景", "medium close-up"),
         ("全景", "full shot"), ("中景", "medium shot"), ("近景", "close-up")]   # 長的先比，close-up 才不會誤吃 extreme close-up
PATCH = ["five fingers", "five natural fingers", "appears only once", "only once", "create one new", "both eyes",
         "same direction", "only person", "the only two people", "nobody else", "exactly one person"]
QUALITY = ["cinematic", "color grading", "colour grading", "pores", "high quality", "masterpiece", "8k", "ultra", "film grain",
           "highly detailed", "realistic detail", "3d animation", "matte skin", "visible texture", "dramatic lighting", "shallow depth of field"]
WORD_MAX = 120


def read_row(table, shot):
    with open(table, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if (r.get("鏡號") or "").strip() == shot:
                return {k.strip(): (v or "").strip() for k, v in r.items() if k}
    return None


def asset_ids(assets_md):
    """資產表：檔案路徑（正規化）和檔名 → ID"""
    by_path, by_name = {}, {}
    if not assets_md or not os.path.isfile(assets_md):
        return by_path, by_name
    for ln in open(assets_md, encoding="utf-8-sig"):
        c = [x.strip() for x in ln.strip().strip("|").split("|")] if ln.startswith("|") else []
        if len(c) >= 2 and c[0] not in ("ID", "Voice ID") and not set(c[0]) <= set("-: "):
            p = c[1].replace("\\", "/")
            by_path[p] = c[0]
            by_name[os.path.basename(p)] = c[0]
    return by_path, by_name


def ref_id(path, by_path, by_name):
    p = path.replace("\\", "/")
    return by_path.get(p) or by_name.get(os.path.basename(p)) or os.path.splitext(os.path.basename(p))[0]


def base(i):
    return i.split("_")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--refs", nargs="+", required=True)
    ap.add_argument("--row", required=True)
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--table", default=None)
    ap.add_argument("--assets", default=None)
    a = ap.parse_args()
    P = a.project
    table = a.table or os.path.join(P, "2-鏡頭表.csv")
    assets = a.assets or os.path.join(P, "已核可", "2-資產", "2-資產.md")
    if not os.path.isfile(assets):
        assets = os.path.join(P, "2-資產.md")
    text = open(a.prompt, encoding="utf-8").read().strip()
    E, W = [], []

    # 1) 開頭
    if not STYLE.match(text):
        E.append("開頭不是 presets/畫風.md 的風格句（只能換 {光態}，一個字都不能多）")
    # 2) 景別
    row = read_row(table, a.row) if os.path.isfile(table) else None
    if row is None:
        E.append(f"鏡頭表 {table} 找不到 {a.row} 這一列")
        row = {}
    low = text.lower()
    found = []
    rest = low
    for zh, en in SIZES:
        if en in rest:
            found.append(zh)
            rest = rest.replace(en, " ")
    want = row.get("景別", "")
    if not want:
        E.append("鏡頭表這一列沒寫「景別」（遠景／全景／中景／中近景／近景／特寫）")
    elif len(found) != 1:
        E.append(f"提示詞的景別詞要正好一個，現在是 {found or '沒有'}（鏡頭表是 {want}）")
    elif found[0] != want:
        E.append(f"景別不符：提示詞是 {found[0]}（{dict(SIZES)[found[0]]}），鏡頭表是 {want}")
    # 3) 張數與引用
    refs = [r.replace("\\", "/") for r in a.refs]
    n = len(refs)
    nums = sorted({int(m) for m in re.findall(r"<image(\d+)>", text)})
    if not 2 <= n <= 4:
        E.append(f"掛了 {n} 張，要 2–4 張")
    if nums and nums[-1] != n:
        E.append(f"提示詞引用到 <image{nums[-1]}>，實際掛 {n} 張，要一樣")
    for k in range(1, n + 1):
        if not re.search(rf"<image{k}> is\b", text):
            E.append(f"<image{k}> 沒有用途句（<image{k}> is …）")
    # 4) 同一角色兩張；5) 已核可、順序
    by_path, by_name = asset_ids(assets)
    ids = [ref_id(r, by_path, by_name) for r in refs]
    seen = {}
    for i in ids:
        seen.setdefault(base(i), []).append(i)
    for b, lst in seen.items():
        if len(lst) > 1:
            E.append(f"同一資產掛了兩次：{'、'.join(lst)}（每個角色只掛一張）")
    for r in refs:
        if not r.startswith("已核可/"):
            E.append(f"{r} 不在 已核可/ 底下")
        elif not os.path.isfile(os.path.join(P, r)):
            E.append(f"{r} 不存在")
    want_refs = [x.strip() for x in re.split(r"[;；]", row.get("參考圖", "")) if x.strip()]
    if want_refs and want_refs != ids:
        E.append(f"掛的順序 {ids} 和鏡頭表「參考圖」{want_refs} 不一樣")
    # 6) 左右
    sides = {"left": "左", "right": "右", "center": "中", "centre": "中"}
    got = {}
    for m in re.finditer(r"from <image(\d+)> is on the (left|right|center|centre)", text):
        k = int(m.group(1))
        if 1 <= k <= n:
            got[base(ids[k - 1])] = sides[m.group(2)]
    want_pos = {base(m.group(1)): m.group(2) for m in re.finditer(r"([A-Za-z0-9_]+)\s*[:：]\s*(左|右|中)", row.get("站位", ""))}
    if got and not want_pos:
        E.append("提示詞寫了人的左右，鏡頭表「站位」沒寫（格式 ID:左｜ID:右）")
    for who, side in want_pos.items():
        if who not in got:
            E.append(f"鏡頭表站位有 {who}:{side}，提示詞沒有「The person from <imageN> is on the …」這一句")
        elif got[who] != side:
            E.append(f"左右不符：{who} 鏡頭表 {side}，提示詞 {got[who]}")
    for who in got:
        if want_pos and who not in want_pos:
            E.append(f"提示詞多了一個人 {who}，鏡頭表站位沒有")
    # 7) 長度、補丁句、畫質詞
    m0 = STYLE.match(text)
    body_text = text[m0.end():] if m0 else text   # 字數從開頭句之後算（開頭句固定 31 字）
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", re.sub(r"<image\d+>", " ", body_text))
    if len(words) > WORD_MAX:
        W.append(f"開頭句之後 {len(words)} 個英文字，超過 {WORD_MAX}")
    sents = re.split(r"(?<=[.!?])\s+", text)
    body = sents[1:] if STYLE.match(text) else sents
    for s in sents:
        if any(p in s.lower() for p in PATCH):
            W.append(f"補丁句：{s.strip()}")
    for s in body:
        if any(q in s.lower() for q in QUALITY):
            W.append(f"開頭句之後又寫畫質形容：{s.strip()}")

    for e in E:
        print("❌", e)
    for w in W:
        print("⚠️", w)
    if not E:
        print(f"✅ 通過（開頭句之後 {len(words)} 字，{n} 張）" + ("，有 ⚠️ 要看" if W else ""))
    sys.exit(1 if E else 0)


if __name__ == "__main__":
    main()
