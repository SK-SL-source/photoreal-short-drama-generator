"""角色板母版編譯器（Qwen-Image 2.1 文字生圖；規格 references/查表-圖像母版.md §1）。
compile(asset, spec) → 提示詞；gate(spec) → 醜衣 Gate 問題清單（空＝過）。固定段一個字不動，填空只從 spec 來。
asset：board（四排角色板 1088×1920）／full（單人全身 1088×1920）／three（三視圖 1920×1088）。
spec 寫在專案的送件腳本裡（照 PRESETS 的格式，先決定造型類型、色盤、輪廓、材質，最後才填單品）。
用法：python qwen_t2i_母版.py [preset 名稱]   印出 gate 結果和四排板提示詞
"""
import re, sys

STYLE_LINES = ["clean Korean fashion lookbook aesthetic,", "soft beauty editorial photography,", "polished e-commerce catalog style,",
               "bright airy commercial studio lighting,", "clean soft gray seamless background,", "{gender_line}",
               "refined and delicate visual tone,", "soft neutral color palette,", "high-end but understated,", "fresh, attractive, polished."]
GENDER_LINE = {"f": "elegant feminine styling,", "m": "clean understated masculine styling,"}
FINISH = ("The overall image should feel like a luxury fashion catalog: polished, clean, bright, airy, delicate, visually appealing.\n"
          "Lighting: large diffused commercial studio lighting, soft frontal key light, gentle fill light, smooth and flattering facial lighting, "
          "minimal harsh shadows, bright clean exposure, high-key but natural.\n"
          "Color and finish: soft gray background, clean color separation, low contrast, soft highlights, polished commercial retouching, smooth elegant finish.")
LAYOUT = {
    "board": ("Layout of the board, one vertical canvas divided into four rows on the same soft gray seamless background:\n"
              "Row 1: four full-body standing views of the same person side by side, front, three-quarter, side profile and back, from head to shoes.\n"
              "Row 2: on the left one large three-quarter head-and-shoulders portrait with a calm closed mouth; on the right a grid of six small "
              "head-and-shoulders expressions: calm, soft smile, thinking, chin resting on hand, speaking, slight frown.\n"
              "Row 3: the outfit laid flat in four separate panels: {flat}.\n"
              "Row 4: on the left {acc} photographed by themselves; on the right three head views: front, side profile, back of the head.\n"
              "Every panel shows the same person, the same hair and the same outfit."),
    "full": "Layout: one single full-body standing view, front three-quarter angle, from head to shoes, centered.",
    "three": "Layout: three full-body views of the same person side by side: front, three-quarter, and side profile.",
}
TASK = {"board": "A premium commercial studio fashion character board featuring {who}.",
        "full": "A premium commercial studio fashion portrait of {who}.",
        "three": "A premium commercial studio fashion reference of {who}."}
NO = ("Do not make it cinematic, gritty, documentary, raw, heavily textured, or aged.\n"
      "Do not use film grain. Do not use dramatic lighting. Do not make the skin rough or overly realistic.\n"
      "No text, no labels, no captions.")
SIZE = {"board": (1088, 1920), "full": (1088, 1920), "three": (1920, 1088)}
FORBIDDEN_ITEM_WORDS = ["glossy", " pu ", "patent", "nylon", "polyester", "sequin", "metallic fabric", "shiny"]
F, M = ("She", "Her", "her"), ("He", "His", "his")


def beauty(s):
    p = s["pronoun"]
    return (f"The {s['noun']} is {s['age']}, {s['build']}, with {s['face']}, {s['features']}, {s['eyes']}, {s['nose']}, {s['lips']}, "
            f"and clear {s.get('skin_tone', 'fair')} skin with gentle beauty retouching.\n"
            f"{p[1]} hair is {s['hair']}.\n"
            f"{p[0]} wears {s['makeup']}.")


def wardrobe(s):
    w, p = s["wardrobe"], s["pronoun"]
    pal, it = w["palette"], w["items"]
    if it.get("one_piece"):
        outfit = f"{p[0]} wears a coordinated outfit: {it['outer']}, over {it['one_piece']}, and {it['shoes']}."
    else:
        outfit = f"{p[0]} wears a coordinated outfit: {it['outer']}, over {it['top']}, with {it['bottom']}, and {it['shoes']}."
    return "\n".join([
        f"Wardrobe archetype: {w['archetype']}.",
        f"Palette: {', '.join(pal['main'][:-1])} and {pal['main'][-1]} only, with {pal['accent']} as the accent.",
        f"Silhouette: {w['silhouette']['upper']}; {w['silhouette']['lower']}; {w['silhouette']['overall']}.",
        f"Materials: {', '.join(w['materials'])}; all matte and natural-looking.",
        outfit,
        f"Accessories: {' and '.join(w['accessories'])}.",
        w["forbidden"],
    ])


def gate(s):
    """醜衣 Gate：色盤 1–3 主色系＋accent；輪廓上／下／整體；材質 ≥4；單品外／上／下／鞋（或 one_piece）；配件 1–3；禁字；主色不得是 black"""
    w, bad = s["wardrobe"], []
    pal = w["palette"]
    if not 1 <= len(pal["main"]) <= 3 or not pal.get("accent"):
        bad.append(f"色盤要 1–3 個主色系＋1 個 accent，現在 {len(pal['main'])} 個主色系")
    for k in ("upper", "lower", "overall"):
        if not w["silhouette"].get(k):
            bad.append(f"輪廓缺 {k}")
    if len(w["materials"]) < 4:
        bad.append(f"材質只列 {len(w['materials'])} 種，要 4 種以上")
    need = ("outer", "one_piece", "shoes") if w["items"].get("one_piece") else ("outer", "top", "bottom", "shoes")
    for k in need:
        if not w["items"].get(k):
            bad.append(f"單品缺 {k}")
    if not 1 <= len(w["accessories"]) <= 3:
        bad.append("配件要 1–3 件")
    text = " ".join(w["items"].values()).lower()
    for bw in FORBIDDEN_ITEM_WORDS:
        if bw in f" {text} " and not w.get("story_required"):
            bad.append(f"單品裡有禁字「{bw.strip()}」（劇情明寫才可，標 story_required）")
    if re.search(r"\bblack\b", " ".join(pal["main"]).lower()) and not w.get("story_required"):
        bad.append("主色系有 black（大面積純黑；劇情明寫才可）")
    return bad


def compile(asset, s):
    style = "Style focus:\n" + "\n".join(STYLE_LINES).format(gender_line=GENDER_LINE[s["gender"]])
    w = s["wardrobe"]
    layout = LAYOUT[asset].format(flat=w["flat"], acc=w["acc"]) if asset == "board" else LAYOUT[asset]
    return "\n\n".join([TASK[asset].format(who=s["who"]), style, beauty(s), wardrobe(s), FINISH, layout, NO]) + "\n"


# 通用範例（2026-10-11 各 2 seed 驗證過版型與造型）；專案的角色照這個格式另寫，不改這裡
PRESETS = {
    "女_都市簡約": dict(gender="f", pronoun=F, noun="woman", who="an East Asian woman", age="in her early thirties", build="slim",
                  face="a small oval face", features="refined East Asian facial features", eyes="soft almond-shaped eyes",
                  nose="a straight delicate nose", lips="soft natural pink lips",
                  hair="shoulder-length black hair with soft airy layers, tied low at the nape, with a few soft face-framing strands",
                  makeup="soft natural makeup: smooth skin, subtle blush, soft brown eye makeup, natural brows, lightly glossy natural lips",
                  wardrobe=dict(archetype="soft urban minimal, Korean-Japanese casual, understated and premium",
                                palette=dict(main=["oatmeal beige", "ivory", "washed light-blue denim"], accent="white"),
                                silhouette=dict(upper="a relaxed cardigan with a soft drape and natural shoulder line over a close-fitting crew-neck top",
                                                lower="high-rise straight-leg denim with a clean elongated line",
                                                overall="soft upper body, clean lower-body line, elongated vertical proportion, no oversized streetwear"),
                                materials=["fine-gauge knit", "fine rib knit", "washed denim", "matte leather", "brushed metal"],
                                items=dict(outer="a relaxed oatmeal-beige fine-knit cardigan", top="an ivory fine-rib crew-neck knit top",
                                           bottom="washed light-blue high-rise straight-leg denim", shoes="minimal white leather sneakers"),
                                accessories=["a medium taupe matte-leather shoulder bag with understated hardware", "a slim rectangular silver watch with a muted taupe leather strap"],
                                forbidden="No strong contrast, no dominant black, no glossy synthetic fabric, no oversized streetwear, no corporate office uniform styling.",
                                flat="the cardigan, the knit top, the jeans, the sneakers", acc="the shoulder bag and the watch")),
    "男_都市簡約": dict(gender="m", pronoun=M, noun="man", who="a young East Asian man", age="in his early thirties", build="lean",
                   face="a clean oval face", features="refined East Asian features", eyes="calm dark eyes", nose="a straight nose", lips="a gentle relaxed mouth",
                   hair="short black hair with soft natural texture, slightly tousled on top, clean at the sides",
                   makeup="no makeup: clean-shaven, well-groomed natural brows, clear skin",
                   wardrobe=dict(archetype="soft urban minimal menswear, Korean-Japanese casual, understated and premium",
                                 palette=dict(main=["olive", "cream", "warm gray"], accent="white"),
                                 silhouette=dict(upper="a relaxed overshirt with a natural shoulder over a clean-fitted knit", lower="straight-leg trousers with a long clean line",
                                                 overall="elongated and tidy, no oversized streetwear"),
                                 materials=["brushed cotton", "fine-gauge knit", "soft wool blend", "matte leather", "brushed metal"],
                                 items=dict(outer="a relaxed olive brushed-cotton overshirt worn open", top="a cream fine-knit crew-neck top",
                                            bottom="warm gray straight-leg wool-blend trousers", shoes="minimal white leather sneakers"),
                                 accessories=["a soft olive canvas tote bag", "a slim round silver watch with a brown leather strap"],
                                 forbidden="No dominant black, no glossy synthetic fabric, no corporate suit, no sportswear.",
                                 flat="the olive overshirt, the cream knit top, the gray trousers, the white sneakers", acc="the canvas tote bag and the watch")),
    "少女_可愛": dict(gender="f", pronoun=F, noun="girl", who="a teenage East Asian girl", age="sixteen", build="petite",
                  face="a small round face", features="soft youthful East Asian features", eyes="large gentle eyes", nose="a small nose", lips="soft pink lips",
                  hair="shoulder-length black hair with soft see-through bangs and airy ends, half tied up with a small cream ribbon",
                  makeup="very light natural makeup: fresh skin, a hint of blush, soft natural brows",
                  wardrobe=dict(archetype="sweet soft-girl casual, youthful and cute, Japanese-Korean campus style",
                                palette=dict(main=["pale pink", "ivory", "light oat"], accent="white"),
                                silhouette=dict(upper="a short soft cardigan over a round-collar blouse", lower="a mid-length pleated skirt at the knee",
                                                overall="small and neat proportions, nothing tight or revealing"),
                                materials=["soft brushed knit", "cotton poplin", "fine pleated chiffon", "matte leather"],
                                items=dict(outer="a pale pink soft-knit cardigan with small round buttons", top="an ivory round-collar cotton blouse",
                                           bottom="a light oat pleated chiffon skirt to the knee with white ankle socks", shoes="ivory matte-leather Mary Jane shoes"),
                                accessories=["a small ivory canvas crossbody bag with a short strap", "a tiny pearl hair clip"],
                                forbidden="No black, no glossy synthetic fabric, no mature styling, no heavy makeup.",
                                flat="the pink cardigan, the ivory blouse, the oat pleated skirt, the Mary Jane shoes", acc="the small crossbody bag and the hair clip")),
    "女學生_制服": dict(gender="f", pronoun=F, noun="girl", who="an East Asian high-school girl in a school uniform", age="seventeen", build="slim",
                    face="a clean oval face", features="bright clear East Asian features", eyes="dark attentive eyes", nose="a straight small nose", lips="natural pink lips",
                    hair="long straight black hair past the shoulders, neat, with a simple side part and no bangs",
                    makeup="no visible makeup: fresh clean skin and natural brows",
                    wardrobe=dict(archetype="neat Japanese-Korean school uniform, tidy and youthful, worn properly", story_required=True,
                                  palette=dict(main=["navy", "white", "muted gray-blue check"], accent="dark brown"),
                                  silhouette=dict(upper="a fitted blazer with a natural shoulder over a crisp buttoned shirt", lower="a knee-length pleated skirt with clean straight lines",
                                                  overall="neat and proper"),
                                  materials=["wool-blend suiting", "cotton poplin", "fine wool knit", "matte leather"],
                                  items=dict(outer="a navy wool-blend blazer with a plain crest-free lapel", top="a white cotton shirt with a thin navy ribbon tie",
                                             bottom="a gray-blue check pleated skirt to the knee with navy knee socks", shoes="dark brown matte-leather loafers"),
                                  accessories=["a navy structured school backpack", "a plain silver wristwatch"],
                                  forbidden="No logos, no printed text, no glossy fabric, no casual streetwear mixed in.",
                                  flat="the navy blazer, the white shirt with ribbon, the check pleated skirt, the loafers", acc="the school backpack and the watch")),
    "成熟_優雅": dict(gender="f", pronoun=F, noun="woman", who="an elegant East Asian woman", age="in her mid-thirties", build="slim and poised",
                  face="a refined oval face", features="elegant East Asian features", eyes="calm almond-shaped eyes", nose="a straight nose", lips="softly defined lips",
                  hair="dark brown hair, sleek, gathered into a low neat bun with a clean center part",
                  makeup="polished natural makeup: smooth skin, soft brown eye makeup, defined natural brows, a muted rose lip",
                  wardrobe=dict(archetype="quiet-luxury mature elegance, understated and refined",
                                palette=dict(main=["camel", "ivory", "dark taupe"], accent="muted gold"),
                                silhouette=dict(upper="a long straight coat with a clean shoulder over a fluid blouse tucked in", lower="tailored straight trousers with a long line",
                                                overall="elongated and composed, nothing tight"),
                                materials=["fine wool", "matte silk crepe", "smooth matte leather", "brushed gold"],
                                items=dict(outer="a long camel wool coat worn open", top="an ivory matte-silk blouse tucked in",
                                           bottom="dark taupe tailored straight trousers", shoes="pointed dark taupe matte-leather flats"),
                                accessories=["a structured dark taupe leather handbag with minimal gold hardware", "a slim gold watch"],
                                forbidden="No black-dominant outfit, no glossy fabric, no loud logos, no casual sportswear.",
                                flat="the camel coat, the ivory blouse, the taupe trousers, the pointed flats", acc="the structured handbag and the gold watch")),
    "美艷": dict(gender="f", pronoun=F, noun="woman", who="a glamorous East Asian woman", age="in her late twenties", build="slim with an elegant figure",
               face="a striking oval face", features="glamorous East Asian features", eyes="expressive almond-shaped eyes with a soft wing of eyeliner",
               nose="a straight nose", lips="full lips",
               hair="long dark brown hair with soft voluminous waves falling past the shoulders, glossy and airy",
               makeup="polished evening makeup: luminous skin, soft smoky brown eyes, defined brows, a deep rose-red lip",
               wardrobe=dict(archetype="glamorous evening elegance, sensual but tasteful",
                             palette=dict(main=["deep plum", "champagne", "nude"], accent="gold"),
                             silhouette=dict(upper="a fitted bodice with a modest square neckline and bare arms", lower="a fitted midi skirt that follows the body with a clean line",
                                             overall="long and graceful, nothing cheap or revealing"),
                             materials=["matte silk crepe", "low-sheen silk", "matte leather", "polished gold"],
                             items=dict(outer="a soft champagne silk wrap over one arm", one_piece="a deep plum matte-crepe fitted midi dress with a modest square neckline, falling below the knee",
                                        shoes="nude matte-leather strappy heels"),
                             accessories=["a small champagne matte-leather clutch", "slim gold drop earrings", "a thin gold bracelet"],
                             forbidden="No dominant black, no glossy PU, no heavy sequins, no oversized jewelry.",
                             flat="the plum dress, the champagne wrap, the strappy heels, the clutch", acc="the earrings and the bracelet")),
}

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    names = sys.argv[1:] or list(PRESETS)
    for n in names:
        bad = gate(PRESETS[n])
        print(f"== {n}：{'❌ ' + '；'.join(bad) if bad else '✅ 醜衣 Gate 過'}")
        if len(names) == 1:
            print(compile("board", PRESETS[n]))
