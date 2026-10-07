# 檢查鏡頭表.py — 用法：python 檢查鏡頭表.py 2-鏡頭表.csv
# 檢查項目：秒數範圍、台詞會不會講不完、連戲三欄有沒有填、參考圖有沒有指定（照 references/規則核心.md §2、§3）；
# 旁邊有 2-分鏡.md 時再照分鏡卡「生成」那一行核對：成片秒數 ≤ 生成幀數÷24、成片取在生成長度裡且合計＝秒數、台詞窗在成片取裡
# （2-分鏡.md §4、§5；第三方測試 QA-10：141 幀寫 6 秒沒人抓到）。
# 回報全部用白話。❌ = 一定要修；⚠️ = 看過確認就好。

import os, sys, csv, re
from comfy import CFG, need_file
import 檢查量產Gate as gate   # 分鏡卡「生成」「成片取」「台詞窗」的解析，和送件前檢查、剪接同一套

sys.stdout.reconfigure(encoding="utf-8")  # Windows 中文輸出必加

SEC_MIN, SEC_MAX = 4, CFG["max_frames"] / 24   # 每鏡秒數範圍；最長＝這台機器量過的單鏡上限

def 台詞上限(秒):
    # 規則核心 §2.4：字數 ≤ 4.2×（秒−2.3）。加 0.05 吸收秒數四捨五入的誤差，
    # 才對得上規範的表：124 幀（5.17 秒）12 字、141 幀 15 字、158 幀（6.58 秒）18 字。
    return max(0, int(4.2 * (秒 - 2.3) + 0.05))

TOL = 1 / 24 + 1e-6   # 一格


def 分鏡卡檢查(鏡, 秒, card):
    """照分鏡卡「生成」那一行核對（規則核心 §2.2、2-分鏡.md §4、§5）：成片秒數 ≤ 生成幀數÷24；成片取在生成長度裡、合計＝秒數；台詞窗在成片取裡"""
    問題 = []
    gen = gate.card_generation(card)
    if gen is None:
        return [f"❌ {鏡}：分鏡卡沒有「生成：」那一行（模式、幀數、首幀、成片取）。"]
    segs, e1 = gate.parse_generation(gen)
    takes, e2 = gate.parse_take(gen)
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
        問題.append(f"❌ {鏡}：成片取合計 {合計:g} 秒，鏡頭表寫 {秒:g} 秒，要一樣（Animatic 和剪接都照成片取）。")
    for seg, (wa, wb) in gate.parse_dialogue_window(card).items():
        take = takes.get(seg)
        if take is None:
            問題.append(f"❌ {鏡}：台詞窗寫在{seg + ' 段' if seg else '不拆段'}，成片取沒有這一段。")
        elif wa < take[0] - TOL or wb > take[1] + TOL:
            問題.append(f"❌ {鏡}：台詞窗 {wa:g}–{wb:g} 秒不在成片取 {take[0]:g}–{take[1]:g} 秒裡，台詞會被剪掉——改成片取或改台詞窗。")
    return 問題


def 台詞字數(text):
    # 只數實際會唸出來的字（去掉角色名、冒號、標點、空白）
    t = re.sub(r"^[^:：]*[:：]", "", text)          # 去掉「角色名:」這種前綴
    t = re.sub(r"[，。！？、,.!?…；;「」『』()（）\s]", "", t)
    return len(t)

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
            cards, _ = gate.find_cards(f.read().replace("\r\n", "\n"), [(r.get("鏡號") or "").strip() for r in rows])
    else:
        問題.append("⚠️ 旁邊沒有 2-分鏡.md：這次跳過幀數、成片取、台詞窗的核對（G4B 寫完分鏡卡要再跑一次）。")
    for r in rows:
        鏡 = (r.get("鏡號") or "").strip() or "（沒填鏡號）"
        # 1) 秒數
        try:
            秒 = float((r.get("秒數") or "").strip())
        except ValueError:
            問題.append(f"❌ {鏡}：秒數沒填或不是數字。")
            continue
        if not (SEC_MIN <= 秒 <= SEC_MAX):
            問題.append(f"❌ {鏡}：{秒:g} 秒超出範圍（每鏡要在 {SEC_MIN}–{SEC_MAX:.2f} 秒之間）。")
        if cards is not None:
            if 鏡 not in cards:
                問題.append(f"❌ {鏡}：2-分鏡.md 找不到分鏡卡（要有一行以「{鏡}｜」開頭）。")
            else:
                問題 += 分鏡卡檢查(鏡, 秒, cards[鏡])
        # 2) 台詞講不講得完
        台詞 = (r.get("台詞(逐字)") or r.get("台詞") or "").strip()
        if 台詞:
            字數 = 台詞字數(台詞)
            上限 = 台詞上限(秒)
            if 字數 > 上限:
                問題.append(f"❌ {鏡}：台詞 {字數} 字，但 {秒:g} 秒最多 {上限} 字（規則核心 §2.4）"
                            f" → 改短台詞，或把這鏡拆成兩鏡。")
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
