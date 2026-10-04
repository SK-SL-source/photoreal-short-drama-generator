# 檢查鏡頭表.py — 用法：python 檢查鏡頭表.py 2-鏡頭表.csv
# 檢查項目：秒數範圍、台詞會不會講不完、連戲三欄有沒有填、參考圖有沒有指定（照 references/規則核心.md §2、§3）。
# 回報全部用白話。❌ = 一定要修；⚠️ = 看過確認就好。

import sys, csv, re
from comfy import CFG, need_file

sys.stdout.reconfigure(encoding="utf-8")  # Windows 中文輸出必加

SEC_MIN, SEC_MAX = 4, CFG["max_frames"] / 24   # 每鏡秒數範圍；最長＝這台機器量過的單鏡上限

def 台詞上限(秒):
    # 規則核心 §2.4：字數 ≤ 4.2×（秒−2.3）。加 0.05 吸收秒數四捨五入的誤差，
    # 才對得上規範的表：124 幀（5.17 秒）12 字、141 幀 15 字、158 幀（6.58 秒）18 字。
    return max(0, int(4.2 * (秒 - 2.3) + 0.05))

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
