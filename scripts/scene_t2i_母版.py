"""場景卡母版編譯器（Qwen-Image 2.1 文字生圖，1920×1088；規格 references/查表-圖像母版.md §2）。
compile(spec) → 提示詞；gate(spec) → 醜景 Gate 問題清單（空＝過）。固定段一個字不動，填空只填五件事：空間、材質、色彩、光態、（氣質由固定段給）。
spec 寫在專案的送件腳本裡（照 PRESETS 的格式）。用法：python scene_t2i_母版.py [preset 名稱]
"""
import re, sys

FEEL = "The image should feel like high-end architectural lifestyle photography: warm, inviting, {lit}, contemporary, natural, and aesthetically curated."
LIVED = ("The environment should feel real and lived-in, but tasteful and visually polished. "
         "It should not feel sterile, empty, abandoned, or overly luxurious.")
PHOTO = ("Photographic style: premium architectural lifestyle photography, clean natural perspective, bright inviting exposure, "
         "soft warm light, polished but realistic finish.")
CAMERA = "Camera: eye-level, wide but natural angle, clear spatial depth, foreground, midground, and background all readable."
BLANK = "All signs, labels and packaging are plain and blank, with no lettering; the space is empty of people."
NO = ("Do not generate horror mood, cold fluorescent ugliness, dirty decay, abandoned atmosphere, sterile showroom styling, or CGI-looking rendering. "
      "{people} No text.")
LIGHT = {   # 光態：FEEL 的形容、SPACE 的最後一句（{via} 填光從哪來）、LIGHTING 的項目
    "sunny_afternoon": ("sunlit", "Warm late-afternoon sunlight {via}, creating bright sun patches and soft natural shadows.",
                        ["warm natural sunlight", "bright ambient light", "sun patches on surfaces", "soft shadow depth", "gentle inviting glow"]),
    "soft_morning": ("softly lit", "Soft early-morning daylight {via}, pale, clean and gentle, with long soft shadows.",
                     ["soft pale daylight", "bright clean ambient light", "gentle long shadows", "light airy glow"]),
    "overcast_after_rain": ("softly lit", "Soft even daylight after rain under a pale grey sky {via}; surfaces are clean and freshly washed with gentle reflections.",
                            ["soft diffused daylight", "even clean exposure", "subtle reflections on wet surfaces", "calm quiet glow"]),
    "night_warm": ("softly lit", "It is night: warm practical lights {via} make the space glow invitingly against a deep blue sky.",
                   ["warm practical lamps as the key light", "soft pools of warm light", "deep blue night outside", "gentle inviting glow"]),
    "night_cool_practical": ("softly lit", "It is night: one clean cool-white overhead practical light is the key, softened and even, with a warm glow from {via} in the distance.",
                             ["soft even cool-white practical light", "warm distant glow for contrast", "clean calm exposure", "no harsh glare"]),
}
BAD_WORDS = ["dirty", "decay", "abandoned", "grime", "rust", "broken", "cracked", "gloomy", "harsh", "oil stain", "faded", "derelict"]
SIZE = (1920, 1088)


def compile(s):
    lit, space_light, light_items = LIGHT[s["light"]]
    kind = "interior" if s["interior"] else "exterior"
    return "\n\n".join([
        f"Create a photorealistic premium lifestyle {kind} {s['scene_type']}.",
        FEEL.format(lit=lit),
        f"This is {s['space']}. {s['architecture']}. " + space_light.format(via=s["light_via"]),
        f"The {kind if s['interior'] else 'scene'} uses a warm material palette: {', '.join(s['materials'])}, and subtle everyday {s['scene_type']} details.",
        LIVED,
        "Color palette: " + ", ".join(s["palette"]) + ".",
        "Lighting: " + ", ".join(light_items + s.get("light_extra", [])) + ".",
        PHOTO,
        CAMERA,
        BLANK,
        NO.format(people="No people as prominent subjects." if s.get("street") else "No people."),
    ]) + "\n"


def gate(s):
    """醜景 Gate：材質 6–10；色彩 5–7；光態從表選、場景專屬最多加 1 項；空間、建築開口、光從哪來都要有；室內要寫到窗或開口；禁字"""
    bad = []
    if not 6 <= len(s["materials"]) <= 10:
        bad.append(f"材質 {len(s['materials'])} 項，要 6–10")
    if not 5 <= len(s["palette"]) <= 7:
        bad.append(f"色彩 {len(s['palette'])} 項，要 5–7")
    if s["light"] not in LIGHT:
        bad.append(f"光態 {s['light']} 不在表裡")
    if len(s.get("light_extra", [])) > 1:
        bad.append("場景專屬光線最多加一項")
    for k in ("space", "architecture", "light_via"):
        if not s.get(k):
            bad.append(f"缺 {k}")
    if s["interior"] and not re.search(r"window|opening|door|glass|skylight", s["architecture"].lower()):
        bad.append("室內要寫到窗或開口看得到什麼")
    text = " ".join([s["space"], s["architecture"], " ".join(s["materials"]), " ".join(s["palette"])]).lower()
    for w in BAD_WORDS:
        if w in text and not s.get("story_required"):
            bad.append(f"填空裡有禁字「{w}」（劇情明寫才可，標 story_required）")
    return bad


# 通用範例（2026-10-11 各 2 seed 驗證過）；專案的場景照這個格式另寫
PRESETS = {
    "外_社區公園步道_午後": dict(interior=False, scene_type="neighborhood park path", light="sunny_afternoon", street=True,
        space="a tree-lined path in a small neighborhood park in the city",
        architecture="A paved path curves between lawns and mature trees, with wooden benches, low lamp posts, a planted border and apartment buildings visible beyond the trees",
        light_via="filters through the leaves onto the path",
        materials=["warm stone paving", "wooden benches", "soft black metal lamp posts", "green lawn and foliage", "low stone edging", "planted flower border", "pale building facades"],
        palette=["warm stone", "muted green", "wood brown", "soft black metal", "golden sunlight", "cream"]),
    "外_住宅巷弄_黃昏": dict(interior=False, scene_type="residential lane", light="night_warm", street=True,
        space="a narrow residential lane between low apartment buildings in a Taiwanese city at dusk",
        architecture="Tiled facades with small balconies, potted plants along the walls, a few parked scooters, a corner shop with a lit window and warm wall lamps above the doors",
        light_via="from the shop window and wall lamps",
        materials=["small ceramic facade tiles", "concrete lane surface", "potted green plants", "soft black metal balcony railings", "warm wood door frames", "parked scooters", "glass shop window"],
        palette=["warm tile cream", "concrete grey", "muted green", "soft black metal", "amber lamp light", "deep blue dusk"]),
    "內_醫院走廊_夜": dict(interior=True, scene_type="hospital corridor", light="night_cool_practical",
        space="a long, quiet corridor of a private hospital ward at night",
        architecture="Pale walls with a wooden handrail, a row of blue seats against one wall, ward doors with small glass panels, and a tall window at the far end showing the night city",
        light_via="the night city through the far window",
        materials=["pale matte wall paint", "polished vinyl floor", "wooden handrail", "blue molded seats", "wooden doors with glass panels", "soft black metal fittings", "large glass window"],
        palette=["pale cream wall", "light grey floor", "muted blue seats", "warm wood", "soft black metal", "cool white light"],
        light_extra=["recessed ceiling lights in a soft even row"]),
    "內_小公寓臥室_早晨": dict(interior=True, scene_type="small apartment bedroom", light="soft_morning",
        space="a small, cozy city apartment bedroom in the early morning",
        architecture="A low wooden bed with cream linen, a window with sheer curtains showing rooftops and trees, a small desk with books and a plant, and a wardrobe along one wall",
        light_via="comes through the sheer curtains",
        materials=["light wood bed and desk", "cream linen bedding", "sheer cotton curtains", "warm wood floor", "a woven rug", "ceramic cup and plant pot", "paper books"],
        palette=["cream", "light wood", "soft white", "muted green", "warm grey", "pale morning light"]),
}

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    names = sys.argv[1:] or list(PRESETS)
    for n in names:
        bad = gate(PRESETS[n])
        print(f"== {n}：{'❌ ' + '；'.join(bad) if bad else '✅ 醜景 Gate 過'}")
        if len(names) == 1:
            print(compile(PRESETS[n]))
