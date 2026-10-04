"""H3 提示詞 lint — 任何提示詞送 ComfyUI 之前必過（skill 內附版，照 references/規則核心.md 實作）。
用法：python h3_prompt_lint.py <prompt.txt> [--refs N|名稱,名稱] [--frames N] [--mode ref2va|i2va|fl2va|l2va|t2va]
離開碼 0=過 1=有 ❌
規則來源：官方 base-en/ref-en 格式（規則核心 §6）＋驗證過的寫法（❌）與測試中的假設（⚠️）（規則核心 §7）。
模式從提示詞自動判斷：有 subject_definitions 段＝參考模式（Ref2VA）；
第一行是官方對齊句＝I2VA／FL2VA／L2VA；第一行就是 integrated_multimodal_description＝T2VA。各模式只檢查該模式的官方格式。
專案登記表（SCENE_LANDMARKS、MULTI_STATE_BOARDS、SCENE_PART_OF、CHARACTER_PHRASES）預設是空的，專案要用就在自己 _腳本 的複本裡照註解的格式填。
"""
import os, re, sys

sys.stdout.reconfigure(encoding="utf-8")
SECTIONS = ["subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music"]
BASE_FIELDS = ["integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"]
# 官方 base-en 的對齊句（逐字）
I2VA_LINE = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
FL2VA_LINE = re.compile(r"^How the reference pictures align with the target video — Picture 1 \(from Shot 1\) aligns with the 0\.00-second mark of the target video; Picture 2 \(from Shot \d+\) aligns with the (\d+\.\d\d)-second mark of the target video\.$")
L2VA_LINE = re.compile(r"^How the reference pictures align with the target video — <Picture 1> \(from \[Shot \d+\]\) aligns with the (\d+\.\d\d)-second mark of the target video\.$")
# 官方 ref-en summary 的任務類型（只能從這六種組合，用 " + " 連接、不重複）
TASK_TYPES = ("keyframe completion", "reference generation", "video editing", "video continuation", "audio reuse", "audio reference")
# <Picture N> 當「具體畫格」用（首幀／尾幀／關鍵幀）→ summary 要有 keyframe completion
FRAME_ANCHOR = r"<Picture (\d+)> is the (?:first frame|last frame|final frame|end frame|keyframe)\b"
# 配樂欄只寫樂器、速度、節奏和力度的變化，不寫抽象的情緒詞（官方 base-en 的規定）
MOOD_WORDS = re.compile(r"\b(romantic|sweet|sad|happy|joyful|tense|tension|emotional|melanchol\w*|epic|cinematic|dreamy|mysterious|playful|mischie\w*|frantic|hopeful|nostalgic|uplifting|heartwarming|bittersweet|ominous|suspenseful|dramatic|whimsical|tender|heartfelt|moody)\b", re.I)
PROP_WORDS = r"\b(?:book|brush|coins?|mirror|lamp|basket|drawer|letter|cup|bowl)\b"
HAND_PROPS = r"brush|coins?|letter|cup"
SURFACE_PHRASES = r"on (?:the )?top of|on the cabinet top|on the bedside|on the bed\b|on the desk|on the stool|held (?:\w+ )?in (?:his|her) (?:both |two )?hands?|in (?:his|her) (?:both |two )?hands\b|on the countertop|on the counter|on the table|on the floor|on the floorboards|in (?:his|her) (?:right|left) hand|held in|at (?:his |her |the )?(?:left |right )?wrist|in the drawer|in the basket|on the tabletop"
PICKUP = r"reaches for|picks? (?:that |the |up )|takes up|lifts .* from the (?:basket|counter|table)"
# 場景地標登記表：掛了這張場景圖（檔名開頭＝場景代號），文字就必須交代這些會搶戲的大平面／地標在哪。
# 格式：{"L01": {"counter": r"交代位置的正則"}}
SCENE_LANDMARKS = {}
# 多態設定板：直接掛會把另一態畫成第二件。格式：{"檔名": "有哪幾態"}
MULTI_STATE_BOARDS = {}
# 場景空間關係表：X 是 Y 的一部分／緊鄰 → 「from X toward Y」這種動線不成立。格式：{"L01": {"drawer": "counter"}}
SCENE_PART_OF = {}
HAND_STATE = r"(?:held|holding|holds|already held) (?:in|the [a-z ]+? in) (?:his|her) (?:right|left) hand|(?:right|left) hand (?:still )?(?:holding|holds|grips|gripping|cupp?ing|cupped)|cupped in (?:his|her) (?:right|left) (?:palm|hand)|hands? (?:are|is|stay|stays) empty|both of (?:his|her) hands are empty|both hands empty"
# 角色寫法白名單：掛了這張臉板，subject_definitions 就必須用固定寫法，不自創名字或新寫法。
# 格式：{"檔名": ("固定寫法開頭", …)}；KNOWN_NAMES 列允許出現的角色英文名
CHARACTER_PHRASES = {}
KNOWN_NAMES = set()
# 道具互動過多：逐件操作、拍平推齊、轉動翻動道具 → 模型會複製或重畫道具。動作寫成一次完成。
PROP_OVERHANDLING = r"one by one|one piece at a time|edge-to-edge|\barrang(?:es|ing)\b (?:them|it|each|the)|rotates? (?:it|the [a-z ]+?) (?:half a turn|a quarter turn)|turns? the (?:open )?book|flips? (?:it|the [a-z ]+?|<Subject \d+>) open|stacks? [a-z ]+? one by one"
# 同一句裡是「懸空／在物件上方」的描述（指尖在空中逐一點數）不算互動
NO_CONTACT = r"hover|in the air|above the|without touching"


def split_sections(p):
    sec, cur, order = {}, None, []
    for ln in p.splitlines():
        m = re.match(r"^(%s):\s*$" % "|".join(SECTIONS), ln.strip())
        if m:
            cur = m.group(1); order.append(cur); sec[cur] = []
            continue
        if cur is not None:
            sec[cur].append(ln)
    return {k: "\n".join(v).strip() for k, v in sec.items()}, order


FLOURISH = re.compile(r"\b(faintly|trembl\w*|breath catching|catching and holding|held breath|caught breath|trained care|practiced|grazing|rooted|crisp|as if|as though|like a|the gesture|sweeps?|second pass|stunned)\b", re.I)


def detect_mode(p):
    """ref2va／i2va／fl2va／l2va／t2va；看不出來回 None"""
    if re.search(r"^subject_definitions:\s*$", p, re.M):
        return "ref2va"
    lines = p.strip().splitlines()
    first = lines[0].strip() if lines else ""
    if first.startswith("For the target video, at 0.00 seconds"):
        return "i2va"
    if first.startswith("How the reference pictures align with the target video"):
        return "fl2va" if first.count("aligns with") >= 2 else "l2va"
    if first.startswith("integrated_multimodal_description:"):
        return "t2va"
    return None


def split_base_fields(p):
    """基礎模式的三個欄位：欄位名後面直接接內容（官方 base-en 寫法），內容可以跨行"""
    fields, cur, order = {}, None, []
    for ln in p.splitlines():
        m = re.match(r"^(%s):\s?(.*)$" % "|".join(BASE_FIELDS), ln.strip())
        if m:
            cur = m.group(1); order.append(cur); fields[cur] = [m.group(2)]
            continue
        if cur is not None:
            fields[cur].append(ln)
    return {k: "\n".join(v).strip() for k, v in fields.items()}, order


def _sentences(text):
    """切句。<d>…</d> 先換成 <d/>：台詞原文不計字、不查修飾詞。
    台詞後面接大寫字母／<Subject>／[Shot] 就是新的一句（中文台詞以「。」結尾，舊版切不開，會把下一句算進說話句）；
    接小寫（while her lips remain completely closed）是同一句的延續，照算。回傳 (句子, 是不是說話句)"""
    masked = re.sub(r"<d>.*?</d>", "<d/>", text, flags=re.S)
    masked = re.sub(r"<d/>\s+(?=[A-Z<\[])", "<d/>\n", masked)
    for ln in masked.splitlines():
        for sent in re.split(r"(?<=[.!?])\s+", ln):
            if sent.strip():
                yield sent.strip(), "<d/>" in sent


def _check_sentences(text, E, W):
    # 白話英文：一句一件事、只寫看得見聽得見的；文學修飾 ❌、超 45 字 ❌、超 35 字 ⚠️
    # 說話句例外：含 <d> 的說話句，<d> 內台詞不計、35 字不警告、超過 45 字仍 ❌
    # ——官方要把說話人身分、音色、語速、口音、語氣和 (Sx) 寫在同一句
    for sent, speaking in _sentences(text):
        n = len(sent.replace("<d/>", " ").split())
        if speaking:
            if n > 45: E.append(f"說話句太長（台詞以外 {n} 字，上限 45）：{sent[:60]}…——聲音描述只留重點")
        elif n > 45: E.append(f"句子太長（{n} 字，上限 45）：{sent[:60]}…——拆成一句一件事")
        elif n > 35: W.append(f"句子偏長（{n} 字）：{sent[:60]}…")
        fm = FLOURISH.search(sent)
        if fm: E.append(f"文學修飾「{fm.group(0)}」——只寫鏡頭看得見、聽得見的東西：{sent[:60]}…")


def _common(p, dd, snd, music, frames, entry, E, W):
    """參考模式、基礎模式共用的檢查。dd＝主描述（detailed_description 或 integrated_multimodal_description）"""
    # --- 官方格式 ---
    if re.search(r"\[Shot 1\] (?:At|From) \d", dd):
        E.append("[Shot 1] 不可帶時間戳（官方）")
    ts = [float(m.group(1)) * 60 + float(m.group(2)) for m in re.finditer(r"\[Shot [2-9]\] At (\d\d):(\d\d\.\d{3})", dd)]
    if ts != sorted(ts):
        E.append("切鏡時間戳沒有遞增")
    if re.search(r"\b(do not|don't|never|must not|no one)\b", dd, re.I):
        W.append("有負面句（do not／never／no one…）——改寫成正面狀態句：負面句不加分、單用會失效")
    dcount = len(re.findall(r"<d>\[", p)); dclose = p.count("</d>")
    if dcount != dclose:
        E.append("<d> 標籤不成對")
    for m in re.finditer(r"<d>\[", dd):
        if not re.search(r"\(S\d+(?:,\s*S\d+)*\)", dd[max(0, m.start() - 400):m.start()]):
            E.append("台詞前 400 字內沒有 (Sx) 說話者標記")
    # 旁白（官方 base-en：每個 off-screen voiceover 的 <d> 後面，緊接一句畫面上那個人的嘴唇閉著）
    for m in re.finditer(r"off-screen voiceover", dd):
        end = dd.find("</d>", m.end())
        if end == -1 or not re.search(r"\blips?\s+(?:remain|remains|stay|stays|are|is|keep|keeps|kept)\b[^.]{0,40}?closed", dd[end:end + 200], re.I):
            E.append("off-screen voiceover 的 <d> 後面沒有緊接「嘴唇閉著」（官方寫法，例：while her lips remain completely closed）")
    # 聲音兩欄（官方 base-en §4.6、§4.7）
    s = snd.strip()
    if s.upper().rstrip(".") == "N/A":
        W.append("overall_soundscape 寫 N/A——官方只在明確要求全片無聲時才寫 N/A")
    elif len([x for x in re.split(r"(?<=[.!?])\s+", s) if x.strip()]) > 4:
        W.append("overall_soundscape 超過 4 句（官方：1–4 句寫成一段；和某一鏡同步的聲音事件寫在主描述）")
    if music.strip().upper().rstrip(".") != "N/A":
        mm = sorted(set(w.lower() for w in MOOD_WORDS.findall(music)))
        if mm:
            W.append(f"non_diegetic_music 有抽象情緒詞 {mm}——官方只寫樂器、速度、節奏、力度變化")
    # --- 驗證過的準則（❌）---
    for lm in re.findall(r"(?:stands|standing) at the ([a-z\- ]+?)(?:,|\.| of| with)", dd):
        head = lm.strip().split()[-1]
        if re.search(rf"walks? (?:from [a-z ]+? )?(?:toward|to) the (?:[a-z\-]+ )*{head}", dd):
            E.append(f"同一鏡人物既「stands at the {lm.strip()}」又「walks toward the …{head}」——第 0 幀狀態矛盾，人會一變二")
    if frames:
        budget = int(4.2 * (frames / 24.0 - 2.3))
        total = 0
        for m in re.finditer(r"<d>\[Chinese\](.*?)</d>", dd, re.S):
            total += len(re.sub(r"[，。！？、,.!?…；;「」『』()（）\s—–-]", "", re.sub(r"<[^>]+>", "", m.group(1))))
        if total > budget:
            E.append(f"台詞共 {total} 字，{frames}f（{frames/24:.1f}s）上限約 {budget} 字（開口延遲 2s＋尾巴 0.3s，4.2 字/秒）→ 縮短台詞或加長幀數")
    if re.search(r"\[Shot 2\]", dd):
        W.append("單次生成內含 [Shot 2]：切點時間或第二鏡內容至少一項不穩；需要準確切點與內容請拆成兩次生成")
    for word in ["brush", "cup", "bowl", "basket", "lamp"]:
        if re.search(rf"\b{word}s?\b", dd, re.I) and not re.search(rf"(?:a single|one|exactly one|the only) (?:\w+ )?{word}", dd, re.I):
            W.append(f"道具「{word}」沒寫數量（a single …／exactly one …）——同類物件會一變二")
    if entry:
        head = dd.split("[Shot 1]", 1)[-1][:900].lower()
        missing = [k for k in entry if k.lower() not in head]
        if missing:
            E.append(f"起手狀態沒接上上一鏡收尾（ENTRY）：缺 {missing}——起手 [Shot 1] 前 900 字內要寫到上一鏡結束時的位置／手上／視線")
    for m in re.finditer(PROP_OVERHANDLING, dd, re.I):
        sent = dd[dd.rfind(".", 0, m.start()) + 1: dd.find(".", m.end()) + 1]
        if re.search(NO_CONTACT, sent, re.I):
            continue
        E.append(f"道具互動過多：「{m.group(0)}」——逐件操作／排列／轉動翻動會讓道具變多或被重畫。改成一個動作一次完成；被看的道具全程不碰")
    # --- 測試中的假設（⚠️）---
    if re.search(HAND_PROPS, dd, re.I):
        if not re.search(HAND_STATE, dd, re.I):
            W.append("有手持道具，但沒寫第 0 幀在誰哪隻手／雙手是否為空（測試中）")
        if re.search(PICKUP, dd, re.I):
            if not re.search(r"leaving .*? bare|off the countertop|out of the basket|from the (?:counter|table)top", dd, re.I):
                W.append("有『拿起』轉移但沒寫『從哪拿起、原位變空』（寫法：picks that … up off the countertop, leaving … bare）")


def _lint_ref(p, n_refs=None, ref_names=(), frames=None, entry=None):
    """參考模式（Ref2VA）：官方 ref-en 六段式"""
    E, W = [], []
    sec, order = split_sections(p)
    # --- 官方格式 ---
    if order != SECTIONS:
        E.append(f"六段欄位順序不對：{order}")
    for k in SECTIONS:
        if not sec.get(k):
            E.append(f"欄位 {k} 空白")
    sd, sm, ra, dd = sec.get("subject_definitions", ""), sec.get("summary", ""), sec.get("retention_analysis", ""), sec.get("detailed_description", "")
    pics = sorted(set(int(x) for x in re.findall(r"<Picture (\d+)>", p)))
    if pics != list(range(1, len(pics) + 1)):
        E.append(f"<Picture N> 編號不連續：{pics}")
    if n_refs is not None and len(pics) != n_refs:
        E.append(f"提示詞引用 {len(pics)} 張圖，實際掛 {n_refs} 張")
    for n in pics:
        if f"<Picture {n}>" not in sd:
            E.append(f"<Picture {n}> 沒在 subject_definitions 被引用（用不到的參考不准掛）")
    subs = sorted(set(int(x) for x in re.findall(r"<Subject (\d+)>", sd)))
    for n in set(int(x) for x in re.findall(r"<Subject (\d+)>", p)):
        if n not in subs:
            E.append(f"<Subject {n}> 沒有定義")
    for n in subs:
        if not re.search(rf"<Subject {n}> \(appears in", ra):
            E.append(f"retention_analysis 缺 <Subject {n}> 那一行")
    for n in set(int(x) for x in re.findall(r"<Audio (\d+)>", p)):
        if not re.search(rf"<Audio {n}> is the voice-timbre reference", sd) and not re.search(rf"<Audio {n}>: (?:reference|fully_copy)", ra):
            E.append(f"<Audio {n}> 缺官方寫法（subject_definitions 的 voice-timbre 句或 retention 的 reference 行）")
    # summary 任務類型（官方 ref-en §3），依素材角色計算：有首幀的鏡頭要寫 keyframe completion
    tm = re.match(r"\[([^\]]+)\]", sm)
    if not tm:
        E.append("summary 必須以方括號任務類型開頭，例如 [reference generation] 或 [keyframe completion + reference generation + audio reference]")
    else:
        tags = [t.strip() for t in tm.group(1).split("+")]
        bad = [t for t in tags if t not in TASK_TYPES]
        if bad:
            E.append(f"summary 任務類型不在官方清單：{bad}（只能用：{'、'.join(TASK_TYPES)}）")
        if len(tags) != len(set(tags)):
            E.append("summary 任務類型重複")
        anchors = set(int(x) for x in re.findall(FRAME_ANCHOR, sd))
        need = []
        if anchors:
            need.append("keyframe completion")
        if [n for n in pics if n not in anchors]:
            need.append("reference generation")
        if re.search(r"<Audio \d+> is the voice-timbre reference", sd) or re.search(r"<Audio \d+>: reference", ra):
            need.append("audio reference")
        if re.search(r"<Audio \d+>: fully_copy", ra):
            need.append("audio reuse")
        miss = [t for t in need if t not in tags]
        if miss:
            E.append(f"summary 缺任務類型 {miss}——依素材角色：首幀／尾幀／關鍵幀 → keyframe completion；角色卡、場景卡 → reference generation；音色檔 → audio reference")
    if "[Shot 1]" not in dd:
        E.append("detailed_description 缺 [Shot 1]")
    first_line = dd.split("\n", 1)[0]
    if "[Shot 1]" in first_line:
        E.append("detailed_description 第一句要先寫全片風格，再進 [Shot 1]")
    # --- 驗證過的準則（❌）：參考模式才有的（定義句、場景圖、角色板）---
    for m in re.finditer(r"<Subject (\d+)> is (.+?)(?:\n|$)", sd):
        line = m.group(2)
        if re.search(PROP_WORDS, line, re.I) and not re.search(r"\b(woman|man|girl|boy|figure|interior|room|workshop|shop|bedroom|kitchen|hall|courtyard|street|house|alley|market|environment|set)\b", line, re.I):
            if not re.search(SURFACE_PHRASES, line, re.I) and not re.search(SURFACE_PHRASES, dd[:600], re.I):
                E.append(f"道具 <Subject {m.group(1)}> 沒有檯面錨：要寫在哪個檯面／誰的手上")
    for rn in ref_names:
        sid = rn.split("-")[0].upper()
        for lm, pat in SCENE_LANDMARKS.get(sid, {}).items():
            if not re.search(pat, dd, re.I):
                W.append(f"掛了場景 {sid}，文字沒交代地標「{lm}」的位置（場景裡的大平面會被拿去當道具的檯面）")
    for rn in ref_names:
        if rn in MULTI_STATE_BOARDS:
            W.append(f"參考圖 {rn} 是多態設定板（{MULTI_STATE_BOARDS[rn]}）——直接掛會把另一態畫成第二件；請裁成本鏡使用的那一態再掛")
    for rn in ref_names:
        sid = rn.split("-")[0].upper()
        for part, whole in SCENE_PART_OF.get(sid, {}).items():
            for m in re.finditer(rf"(?:walks?|moves?|steps?|goes|crosses|turns) (?:from|away from) (?:the |his |her )?{part}\b[^.]*?(?:toward|to|towards|into) (?:the |his |her )?{whole}\b", dd, re.I):
                E.append(f"動線不成立：「{m.group(0)[:60]}」——{part} 是 {whole} 的一部分／緊鄰，從它走向 {whole} 沒有距離（場景 {sid}；內容錯，模型會默默修正但別的 seed 會炸）")
            for m in re.finditer(rf"(?:walks?|moves?|steps?|goes|crosses) (?:from|away from) (?:the |his |her )?{whole}\b[^.]*?(?:toward|to|towards|into) (?:the |his |her )?{part}\b", dd, re.I):
                E.append(f"動線不成立：「{m.group(0)[:60]}」——{part} 就在 {whole} 上／旁（場景 {sid}）")
    # 代名詞必須有主體標籤：道具句裡的「她」不知道指誰。
    # 用定義句開頭六個字判主體是人還是非人（道具／場景）；非人主體的定義句與 retention 行只要出現 her/his/she/he ⇒ ❌
    # （道具句裡的「她」不知道指誰，一律寫成 <Subject N> / held in the left hand of <Subject 1>）
    PERSON = re.compile(r"\b(woman|man|girl|boy|figure|person)\b", re.I)
    PRONOUN = re.compile(r"\b(her|hers|his|him|she|he)\b")
    is_person = {}
    for ln in sd.splitlines():
        m = re.match(r"<Subject (\d+)> is\b", ln)
        if m: is_person[m.group(1)] = bool(PERSON.search(" ".join(ln[m.end():].split()[:6])))  # 只看開頭六個字：開頭是道具、後面才提到某個人的定義句，還是算道具
    for block, where in ((sd, "定義句"), (ra, "retention 行")):
        for ln in block.splitlines():
            m = re.match(r"<Subject (\d+)>", ln)
            if not m or is_person.get(m.group(1), True): continue
            pm = PRONOUN.search(ln[m.end():])
            if pm:
                E.append(f"<Subject {m.group(1)}> 是道具／場景，{where}卻出現代名詞「{pm.group(1)}」——不知道指誰；寫成 <Subject N>（例：held in the left hand of <Subject 1>）")
    for rn in ref_names:
        if rn in CHARACTER_PHRASES and not any(ph in sd for ph in CHARACTER_PHRASES[rn]):
            E.append(f"角色寫法不在白名單：掛了 {rn}，subject_definitions 必須用登記的固定寫法「{CHARACTER_PHRASES[rn][0]} N>…」（不自創名字／寫法）")
    if any(rn in CHARACTER_PHRASES for rn in ref_names):
        for m in re.finditer(r"<Subject \d+> is (?:the )?([A-Z][a-z]+ [A-Z][a-z]+)\b", sd):
            if m.group(1) not in KNOWN_NAMES:
                E.append(f"角色名「{m.group(1)}」不在白名單 {sorted(KNOWN_NAMES)}——名字只能用登記過的")
    snd, music = sec.get("overall_soundscape", ""), sec.get("non_diegetic_music", "")
    _common(p, dd, snd, music, frames, entry, E, W)
    _check_sentences(sm + "\n" + dd + "\n" + snd, E, W)
    return E, W


def _lint_base(p, mode, n_refs=None, frames=None, entry=None):
    """基礎模式（T2VA／I2VA／FL2VA／L2VA）：官方 base-en 的對齊句＋三個欄位"""
    E, W = [], []
    lines = p.strip().splitlines()
    first = lines[0].strip() if lines else ""
    fields, order = split_base_fields(p)
    expect = {"t2va": 0, "i2va": 1, "fl2va": 2, "l2va": 1}[mode]
    if mode == "i2va" and first != I2VA_LINE:
        E.append(f"I2VA 第一行要逐字照官方：{I2VA_LINE}")
    if mode == "fl2va":
        m = FL2VA_LINE.match(first)
        if not m:
            E.append("FL2VA 第一行要照官方對齊句：How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.")
        elif frames and abs(float(m.group(1)) - frames / 24.0) > 0.06:
            W.append(f"尾幀對齊在 {m.group(1)} 秒，和片長 {frames}f（{frames/24:.2f}s）對不上")
    if mode == "l2va":
        m = L2VA_LINE.match(first)
        if not m:
            E.append("L2VA 第一行要照官方對齊句：How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.")
        elif frames and float(m.group(1)) > frames / 24.0 + 0.06:
            E.append(f"對齊在 {m.group(1)} 秒，超過片長 {frames}f（{frames/24:.2f}s）")
    if order != BASE_FIELDS:
        E.append(f"三個欄位順序不對（官方：{' → '.join(BASE_FIELDS)}）：{order}")
    for k in BASE_FIELDS:
        if not fields.get(k):
            E.append(f"欄位 {k} 空白")
    dd, snd, music = (fields.get(k, "") for k in BASE_FIELDS)
    pics = sorted(set(int(x) for x in re.findall(r"<?Picture (\d+)>?", p)))  # FL2VA 官方寫法不加角括號
    if pics and pics != list(range(1, len(pics) + 1)):
        E.append(f"Picture 編號不連續：{pics}")
    if pics and max(pics) > expect:
        E.append(f"{mode.upper()} 只能有 {expect} 張圖（首幀／尾幀），提示詞卻引用到 Picture {max(pics)}")
    if n_refs is not None and n_refs != expect:
        E.append(f"{mode.upper()} 要接 {expect} 張圖，實際掛 {n_refs} 張（這個節點接不了角色卡、場景卡、音色檔）")
    if re.search(r"<(?:Subject|Audio|Video) \d+>", p):
        E.append("基礎模式不能用 <Subject N>／<Audio N>／<Video N>（那是參考模式的寫法）")
    if not dd.startswith("[Shot 1]"):
        E.append("integrated_multimodal_description 要以 [Shot 1] 開頭（官方）")
    _common(p, dd, snd, music, frames, entry, E, W)
    _check_sentences(dd + "\n" + snd, E, W)
    return E, W


def lint(p, n_refs=None, ref_names=(), frames=None, entry=None, mode=None):
    """回傳 (❌ 清單, ⚠️ 清單)。mode 不給就從提示詞自動判斷。"""
    mode = mode or detect_mode(p)
    if mode == "ref2va":
        return _lint_ref(p, n_refs, ref_names, frames, entry)
    if mode in ("t2va", "i2va", "fl2va", "l2va"):
        return _lint_base(p, mode, n_refs, frames, entry)
    return ["看不出是哪種模式：參考模式要有 subject_definitions 段；I2VA／FL2VA／L2VA 第一行要是官方對齊句；T2VA 第一行要是 integrated_multimodal_description:"], []

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    if not os.path.exists(sys.argv[1]):
        print(f"❌ 找不到提示詞檔：{sys.argv[1]}"); sys.exit(2)
    text = open(sys.argv[1], encoding="utf-8").read()
    n_refs, ref_names, frames, mode = None, (), None, None
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    if "--frames" in sys.argv:
        frames = int(sys.argv[sys.argv.index("--frames") + 1])
    if "--refs" in sys.argv:
        arg = sys.argv[sys.argv.index("--refs") + 1]
        if arg.isdigit(): n_refs = int(arg)
        else: ref_names = tuple(x.strip() for x in arg.split(",")); n_refs = len(ref_names)
    mode = mode or detect_mode(text)
    E, W = lint(text, n_refs, ref_names, frames, mode=mode)
    print("模式：", mode or "看不出來")
    for e in E: print("❌", e)
    for w in W: print("⚠️", w)
    print("LINT:", "FAIL" if E else "PASS", f"（{len(E)} ❌ / {len(W)} ⚠️）")
    sys.exit(1 if E else 0)
