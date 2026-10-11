# 檢查鏡頭表.py — 用法：python 檢查鏡頭表.py 2-鏡頭表.csv
# 檢查項目：秒數、台詞會不會講不完、連戲三欄有沒有填、參考圖有沒有指定（照 references/3-分鏡.md §2）；
# 旁邊有 2-分鏡.md 時再照分鏡卡「生成」那一行核對：成片秒數 ≤ 生成幀數÷24、成片取在生成長度裡且合計＝秒數、台詞窗在成片取裡
# （3-分鏡.md §3；第三方測試 QA-10：141 幀寫 6 秒沒人抓到）。
# 成片秒數沒有規則下限（3-分鏡.md §3 只限制生成長度：最短 124 幀、最長看 max_frames），台詞字數上限也是對生成片長（§2.4），
# 所以有分鏡卡就照卡上的生成幀數核對；還沒有卡只能用成片秒估，估的只給 ⚠️。
# 回報全部用白話。❌ = 一定要修；⚠️ = 看過確認就好。

import os, sys, csv, re
from comfy import CFG, need_file

sys.stdout.reconfigure(encoding="utf-8")  # Windows 中文輸出必加

# ---- 分鏡卡解析（原本在 檢查量產Gate.py，2.0.0 搬來這裡；剪接.py 也用）----
def _canon_text(s):
    """行尾空白、前後空行不算"""
    return "\n".join(line.rstrip() for line in s.split("\n")).strip("\n")


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


SEC_MAX = CFG["max_frames"] / 24   # 單次生成的上限（這台機器量過的）；成片秒數本身沒有下限，只要大於 0

def 台詞上限(秒):
    # 3-分鏡.md §3 的字數公式：字數 ≤ 4.2×（秒−2.3）。加 0.05 吸收秒數四捨五入的誤差，
    # 才對得上規範的表：124 幀（5.17 秒）12 字、141 幀 15 字、158 幀（6.58 秒）18 字。
    return max(0, int(4.2 * (秒 - 2.3) + 0.05))

TOL = 1 / 24 + 1e-6   # 一格


def 分鏡卡檢查(鏡, 秒, card):
    """照分鏡卡「生成」那一行核對（3-分鏡.md §3、3-分鏡.md §3）：成片秒數 ≤ 生成幀數÷24；成片取在生成長度裡、合計＝秒數；台詞窗在成片取裡"""
    問題 = []
    gen = card_generation(card)
    if gen is None:
        return [f"❌ {鏡}：分鏡卡沒有「生成：」那一行（模式、幀數、首幀、成片取）。"]
    segs, e1 = parse_generation(gen)
    takes, e2 = parse_take(gen)
    問題 += [f"❌ {鏡}：{e}。" for e in e1 + e2]
    if e1:
        return 問題
    總幀 = sum(s["frames"] for s in segs.values())
    if 秒 > 總幀 / 24 + TOL:
        問題.append(f"❌ {鏡}：鏡頭表 {秒:g} 秒，但分鏡卡生成 {總幀} 幀只有 {總幀 / 24:.3f} 秒——成片秒數不能超過生成長度，改秒數或加幀數。")
    if e2 or not takes:
        return 問題
    if set(takes) != set(segs):
        問題.append(f"❌ {鏡}：成片取寫的段（{'、'.join(k or '不拆段' for k in takes)}）和生成的段（{'、'.join(k or '不拆段' for k in segs)}）對不上。")
        return 問題
    for seg, (a, b) in takes.items():
        長 = segs[seg]["frames"] / 24
        if a < 0 or b > 長 + TOL:
            問題.append(f"❌ {鏡}：成片取 {seg + ' 段 ' if seg else ''}{a:g}–{b:g} 秒超出生成長度 {長:.3f} 秒（{segs[seg]['frames']} 幀）。")
    合計 = sum(b - a for a, b in takes.values())
    if abs(合計 - 秒) > TOL:
        問題.append(f"❌ {鏡}：成片取合計 {合計:g} 秒，鏡頭表寫 {秒:g} 秒，要一樣。")
    for seg, (wa, wb) in parse_dialogue_window(card).items():
        take = takes.get(seg)
        if take is None:
            問題.append(f"❌ {鏡}：台詞窗寫在{seg + ' 段' if seg else '不拆段'}，成片取沒有這一段。")
        elif wa < take[0] - TOL or wb > take[1] + TOL:
            問題.append(f"❌ {鏡}：台詞窗 {wa:g}–{wb:g} 秒不在成片取 {take[0]:g}–{take[1]:g} 秒裡，台詞會被剪掉——改成片取或改台詞窗。")
    return 問題


_角色名 = r"[^：:。！？!?；;\n｜|]{1,12}[：:]"   # 「角色名：」——每句最多 12 字的角色名


def 台詞字數(text):
    # 只數實際會唸出來的字：換行或「｜」分句（和送件前檢查同一套），每句去掉開頭的「角色名：」；
    # 同一句裡在句號後又出現的第二個「角色名：」（兩人寫在同一格、沒分句）也去掉，角色名和冒號都不算字
    n = 0
    for piece in re.split(r"[\n｜|]", text or ""):
        piece = re.sub(r"^\s*" + _角色名 + r"\s*", "", piece.strip())
        piece = re.sub(r"(?<=[。！？!?；;])\s*" + _角色名 + r"\s*", "", piece)
        n += len(re.sub(r"[，。！？、,.!?…；;「」『』()（）\s]", "", piece))
    return n


def 同格兩人沒分句(text):
    # 同一句（沒換行、沒「｜」）裡出現兩個以上「角色名：」：鎖定後送件前檢查會把整句當一句台詞比對，提示詞的 <d> 會對不上
    for piece in re.split(r"[\n｜|]", text or ""):
        if len(re.findall(r"(?:^|(?<=[。！？!?；;]))\s*" + _角色名, piece.strip())) >= 2:
            return True
    return False


def 生成幀數(card):
    # 分鏡卡「生成」那一行的幀數合計（拆段相加）；沒有那一行或寫法有錯回 None（錯誤由 分鏡卡檢查 回報）
    gen = card_generation(card)
    if gen is None:
        return None
    segs, errs = parse_generation(gen)
    return None if errs else sum(s["frames"] for s in segs.values())

def main(path):
    問題 = []
    need_file(path, "鏡頭表")
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("❌ 這份鏡頭表是空的，或第一列的欄位名稱對不上。")
        return 1
    md_path = os.path.join(os.path.dirname(os.path.abspath(path)), "2-分鏡.md")
    cards = None
    if os.path.isfile(md_path):
        with open(md_path, encoding="utf-8-sig") as f:
            cards, _ = find_cards(f.read().replace("\r\n", "\n"), [(r.get("鏡號") or "").strip() for r in rows])
    else:
        問題.append("⚠️ 旁邊沒有 2-分鏡.md：這次跳過幀數、成片取、台詞窗的核對（第 3 站 寫完分鏡卡要再跑一次）。")
    for r in rows:
        鏡 = (r.get("鏡號") or "").strip() or "（沒填鏡號）"
        # 1) 秒數
        try:
            秒 = float((r.get("秒數") or "").strip())
        except ValueError:
            問題.append(f"❌ {鏡}：秒數沒填或不是數字。")
            continue
        if 秒 <= 0:
            問題.append(f"❌ {鏡}：秒數要大於 0。")
            continue
        if cards is None and 秒 > SEC_MAX + TOL:
            問題.append(f"⚠️ {鏡}：{秒:g} 秒超過單次生成的上限 {SEC_MAX:.2f} 秒（{CFG['max_frames']} 幀）——不拆段做不到；"
                        f"第 3 站分鏡卡寫完再跑一次，照卡上的生成幀數核對。")
        if cards is not None:
            if 鏡 not in cards:
                問題.append(f"❌ {鏡}：2-分鏡.md 找不到分鏡卡（要有一行以「{鏡}｜」開頭）。")
            else:
                問題 += 分鏡卡檢查(鏡, 秒, cards[鏡])
        # 2) 台詞講不講得完：3-分鏡.md §3 的字數公式 算的是生成片長，所以有分鏡卡就用卡上的生成幀數；沒有卡只能用成片秒估
        台詞 = (r.get("台詞(逐字)") or r.get("台詞") or "").strip()
        if 台詞:
            字數 = 台詞字數(台詞)
            幀 = 生成幀數(cards[鏡]) if cards is not None and 鏡 in cards else None
            if 幀 is not None:
                上限 = 台詞上限(幀 / 24)
                if 字數 > 上限:
                    問題.append(f"❌ {鏡}：台詞 {字數} 字，但分鏡卡生成 {幀} 幀（{幀 / 24:.2f} 秒）最多 {上限} 字（3-分鏡.md §3 的字數公式）"
                                f" → 改短台詞、加幀數，或把這鏡拆成兩鏡。")
            else:
                上限 = 台詞上限(秒)
                if 字數 > 上限:
                    問題.append(f"⚠️ {鏡}：台詞 {字數} 字，用成片 {秒:g} 秒估最多 {上限} 字——3-分鏡.md §3 的字數公式 算的是生成片長，"
                                f"第 3 站分鏡卡寫完再跑一次，照生成幀數核對。")
            if 同格兩人沒分句(台詞):
                問題.append(f"⚠️ {鏡}：同一格有兩個人的台詞但沒分句——一句一行或用「｜」隔開，鎖定後送件前檢查才對得上提示詞的 <d>。")
        # 3) 連戲三欄
        for 欄 in ["站位(誰左誰右)", "視線(看著什麼)", "手上道具"]:
            值 = (r.get(欄) or "").strip()
            if not 值:
                問題.append(f"⚠️ {鏡}：「{欄.split('(')[0]}」沒填——不填的話 AI 會自己亂決定。")
        # 4) 參考圖
        if not (r.get("參考圖") or "").strip():
            問題.append(f"⚠️ {鏡}：沒指定參考圖——角色的臉會不穩。")
        if "首幀來源" in r and not (r.get("首幀來源") or "").strip():
            問題.append(f"⚠️ {鏡}：「首幀來源」沒填——接力、借哪一格、合成或無，先定好。")

    print(f"共檢查 {len(rows)} 鏡。")
    if not 問題:
        print("✅ 全部通過，可以進下一站。")
        return 0
    for p in 問題:
        print(p)
    有紅 = any(p.startswith("❌") for p in 問題)
    print()
    print("❌ 的一定要修完再往下走；⚠️ 的看過、確認沒問題就可以放行。" if 有紅
          else "只有 ⚠️，看過確認就可以放行。")
    return 1 if 有紅 else 0

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python 檢查鏡頭表.py 2-鏡頭表.csv")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
