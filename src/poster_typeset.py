# -*- coding: utf-8 -*-
"""把中文用真字体排到 AI 底图上——海报产线的排版层。

为什么要单独一层：出图模型同时干"画插画"和"写中文"两件事时，两者抢同一份算力。
实测过四次，曲线很清楚——不要求画插画时 79 字逐字全对；一开始要求画，79 字整段丢；
压到 62 字连出两张全对；63 字那张插画最好，但把"比上赛季"多写成一个"个"、
"10月8开启"改写成"10月8日"、赛字笔画画错、连提示词里的「」框都画到图上了。
只要字是模型写的，就做不到"每次都能发"，只能靠多出几张碰运气。

所以这里把分工拆开：模型只画没有字的底图，字由程序用系统中文字体落上去。
错字、多字、漏字、乱码、符号泄漏这五类问题一次性消失，字数上限也不再是正确性问题，
只受版面大小约束，可以放开到 190 字，把竞品那种信息密度做出来。

Python 3.9 兼容：不用 match / X|Y 语法。
"""
import io
import os
import re
from PIL import Image, ImageDraw, ImageFont, ImageOps

# 字体候选按"找不到就往后退"排列：这台是 Intel Mac，另一台机器字体不一定齐，
# 写死单一路径会让整条产线在新机器上直接崩。
FONT_CANDIDATES = {
    "serif_black": [("/System/Library/Fonts/Supplemental/Songti.ttc", 0),
                    ("/System/Library/Fonts/STHeiti Medium.ttc", 1)],
    "sans_bold":   [("/System/Library/Fonts/Hiragino Sans GB.ttc", 2),
                    ("/System/Library/Fonts/STHeiti Medium.ttc", 1)],
    "sans_book":   [("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
                    ("/System/Library/Fonts/STHeiti Light.ttc", 1)],
}
_FONT_CACHE = {}

# 不能出现在行首的收尾标点：中文按字断行时把「。」甩到行首最难看
_NO_HEAD = "。，、；：？！）］》」』…—％%"
_NO_TAIL = "（［《「『"

# 用户上传真图当主视觉时的取景参数：坐骑/角色展示图的主体通常偏上，
# 居中裁容易把腿或底座切掉，所以取景位置稍微上偏。
PHOTO_FOCUS_Y = 0.42
PHOTO_MIN_FRAC = 0.22        # 低于这个高度，贴真图就没意义了，只报版面提示


def _font(face, size):
    """按 (字面, 字号) 取字体对象，命中缓存就不重复开文件。"""
    key = (face, int(size))
    f = _FONT_CACHE.get(key)
    if f:
        return f
    for path, index in FONT_CANDIDATES.get(face, []):
        if not os.path.exists(path):
            continue
        try:
            f = ImageFont.truetype(path, int(size), index=index)
            break
        except Exception:
            continue
    if f is None:
        f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _rgb(hex_str):
    s = (hex_str or "#000000").lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


# 每套风格预设的排版皮肤：字色、面板材质、圆角、描边。
# 底图是模型画的，这层是程序画的，两边必须配套，否则"清单笔记风"排出来是科技感的字。
TYPE_SKINS = {
    "game_epic": {
        "title_face": "serif_black", "body_face": "sans_book",
        "title": "#f2dfa6", "title_stroke": "#1a1108",
        "ink": "#f0eadd", "muted": "#b3a894", "accent": "#c8963c",
        "panel": "#0d131e", "panel_alpha": 168, "stroke": "#c8963c", "stroke_alpha": 165,
        "bg_top": "#1c2434", "bg_bot": "#0a0e16",
        "radius": 12, "gap": 0.020,
        "bottom": "暗色云雾与极淡的石纹底纹，四角有微弱余烬光点",
        "layout": "top_art", "art_floor": 0.50, "art_cap": 0.74
    },
    "tool_review": {
        "title_face": "sans_bold", "body_face": "sans_book",
        "title": "#eaf6ff", "title_stroke": "#0a1020",
        "ink": "#e9f2ff", "muted": "#9fb2cc", "accent": "#4fd1ff",
        "panel": "#101a33", "panel_alpha": 150, "stroke": "#4fd1ff", "stroke_alpha": 120,
        "bg_top": "#141a3c", "bg_bot": "#070a1c",
        "radius": 22, "gap": 0.020,
        "bottom": "深蓝紫渐变与极淡的网格光点，越往下越暗",
        "layout": "grid2", "art_floor": 0.42, "art_cap": 0.70
    },
    "checklist": {
        "title_face": "sans_bold", "body_face": "sans_book",
        "title": "#2b2b2b", "title_stroke": "#f7f2e6",
        "ink": "#2f2d29", "muted": "#7d766b", "accent": "#d9694a",
        "panel": "#fffdf6", "panel_alpha": 214, "stroke": "#d9694a", "stroke_alpha": 90,
        "bg_top": "#f7f2e6", "bg_bot": "#e8dfcb",
        "radius": 8, "gap": 0.022,
        "bottom": "暖米色纸纹底，带极淡的纤维质感",
        "layout": "list", "art_floor": 0.26, "art_cap": 0.52
    },
    "minimal": {
        "title_face": "sans_bold", "body_face": "sans_book",
        "title": "#1a1a1a", "title_stroke": "#ffffff",
        "ink": "#1a1a1a", "muted": "#8b8b8b", "accent": "#d92b2b",
        "panel": "#ffffff", "panel_alpha": 226, "stroke": "#d92b2b", "stroke_alpha": 0,
        "bg_top": "#fbfbfa", "bg_bot": "#ececea",
        "radius": 0, "gap": 0.026,
        "bottom": "干净的纯色留白，不要任何纹理",
        "layout": "hero", "art_floor": 0.32, "art_cap": 0.58
    },
}
DEFAULT_SKIN = "tool_review"

# ── 布局轴：和皮肤分开的第二个维度 ──
# 原来 layout 是写死在皮肤里的（选完风格构图就定了），而且四种全是"图在上、字在下半截"。
# 现在拆成独立一轴：皮肤只管配色/字体/面板材质，这里管"图放哪、文字放哪、卡片怎么分行"。
# 每套皮肤仍保留一个默认布局（skin["layout"]），模型没推荐或推荐了非法值时回落到它。
#
# 字段说明：
#   rows   卡片怎么分行：stack 单列通栏 / grid2 两列成对 / hero 首条通栏放大 /
#          list 单列带序号 / split 左右两栏各放一半（对比用）
#   art    插画或真图占哪块：band_top 上部横带 / band_bottom 下部横带 /
#          side_left 左竖栏 / side_right 右竖栏 / full 整幅
#   needs_photo 这块没有图就站不住（满版、左右分栏），模型侧不推荐、后端也会回落
LAYOUTS = {
    "top_art":    {"label": "图上文下", "rows": "stack", "art": "band_top",
                   "min_cards": 3, "max_cards": 5, "needs_photo": False,
                   "when": "插画或真图占上半，要点从上往下读；条数多也稳"},
    "grid2":      {"label": "双列网格", "rows": "grid2", "art": "band_top",
                   "min_cards": 2, "max_cards": 6, "needs_photo": False,
                   "when": "要点条数多、每条都短，两列并排最省地方"},
    "list":       {"label": "清单长条", "rows": "list", "art": "band_top",
                   "min_cards": 3, "max_cards": 5, "needs_photo": False,
                   "when": "避坑清单、步骤；序号比画面重要"},
    "hero":       {"label": "首条放大", "rows": "hero", "art": "band_top",
                   "min_cards": 2, "max_cards": 5, "needs_photo": False,
                   "when": "有一条结论特别重要，其余几条当支撑"},
    "bottom_art": {"label": "图下文上", "rows": "stack", "art": "band_bottom",
                   "min_cards": 2, "max_cards": 4, "needs_photo": False,
                   "art_floor": 0.24, "art_cap": 0.42,
                   "when": "标题要第一眼抓到，图当收尾氛围"},
    "big_type":   {"label": "大字报", "rows": "stack", "art": "band_bottom",
                   "min_cards": 2, "max_cards": 4, "needs_photo": False,
                   "title_scale": 1.5, "art_floor": 0.16, "art_cap": 0.30,
                   "when": "单观点、结论先行；字少事大，没图也成立"},
    "compare":    {"label": "双列对比", "rows": "split", "art": "band_bottom",
                   "min_cards": 2, "max_cards": 6, "needs_photo": False, "divider": True,
                   "art_floor": 0.20, "art_cap": 0.36,
                   "when": "二选一、A vs B、价格档位；左右各一栏对着看"},
    "left_text":  {"label": "左文右图", "rows": "stack", "art": "side_right",
                   "min_cards": 2, "max_cards": 4, "needs_photo": True, "col_frac": 0.40,
                   "when": "有竖版展示图或界面截图，图和文字各占一半"},
    "right_text": {"label": "右文左图", "rows": "stack", "art": "side_left",
                   "min_cards": 2, "max_cards": 4, "needs_photo": True, "col_frac": 0.40,
                   "when": "同上一版换口气，连发多条不至于全是一个样"},
    "full_bleed": {"label": "满版大图", "rows": "stack", "art": "full",
                   "min_cards": 1, "max_cards": 2, "needs_photo": True, "overlay": True,
                   "when": "单主角内容（一只坐骑、一个界面），图就是全部"},
}
# 老数据里存的键（内置皮肤原来写的是 stack，用户模板 JSON 里也是）照样认，不做迁移
LAYOUT_ALIAS = {"stack": "top_art"}
DEFAULT_LAYOUT = "top_art"
# 界面上给的下拉顺序：七种新走向在前，四套历史布局在后（默认值不会变）
LAYOUT_ORDER = ["top_art", "bottom_art", "left_text", "right_text", "full_bleed",
                "big_type", "compare", "list", "grid2", "hero"]


def _norm_layout(key):
    k = LAYOUT_ALIAS.get(str(key or "").strip(), str(key or "").strip())
    return k if k in LAYOUTS else ""


def resolve_layout(plan=None, skin=None):
    """这轮排版用哪个布局：plan.layout_key 优先，其次皮肤自带默认，最后兜 top_art。"""
    key = _norm_layout((plan or {}).get("layout_key")) if isinstance(plan, dict) else ""
    if key:
        return key
    return _norm_layout((skin or {}).get("layout")) or DEFAULT_LAYOUT


def layout_spec(key):
    return dict(LAYOUTS.get(_norm_layout(key) or DEFAULT_LAYOUT))


def layout_choices():
    """给界面用：键 + 中文名 + 适用场景 + 要不要图 + 卡片数区间。"""
    out = []
    for k in LAYOUT_ORDER:
        v = LAYOUTS[k]
        out.append({"key": k, "label": v["label"], "when": v.get("when", ""),
                    "needs_photo": bool(v.get("needs_photo")),
                    "min_cards": v["min_cards"], "max_cards": v["max_cards"]})
    return out


def skin_for(style_key):
    """内置四套查不到就当用户模板查一遍——模板和内置走同一条排版路径，
    不然"我的模板"只能预览、出图时又掉回默认皮肤。"""
    s = TYPE_SKINS.get(style_key)
    if s:
        return s
    if style_key:
        try:
            from . import poster_templates
            s = poster_templates.skin(style_key)
            if s:
                return s
        except Exception:
            pass
    return TYPE_SKINS[DEFAULT_SKIN]


_ASCII_RUN = re.compile(r"[0-9A-Za-z]+(?:\s*[.%+\-/：:]\s*[0-9A-Za-z]+)*")


def _tokens(text):
    """把文本切成"不可拆单元"：一串数字/字母算一个整体，中文一个字算一个整体。

    不这么做的话 3600 会被折成"3"+"600"，读者会当成两个数——实测踩过。
    """
    out, pos = [], 0
    for mt in _ASCII_RUN.finditer(str(text or "")):
        out.extend(list(str(text or "")[pos:mt.start()]))
        out.append(mt.group())
        pos = mt.end()
    out.extend(list(str(text or "")[pos:]))
    return [tk for tk in out if tk != ""]


def _wrap(text, font, max_w):
    """按不可拆单元断行：不把收尾标点放到行首、不把起始标点留在行尾。"""
    lines, cur = [], ""
    for tk in _tokens(text):
        if tk == "\n":
            lines.append(cur)
            cur = ""
            continue
        if cur and font.getlength(cur + tk) > max_w:
            if len(tk) > 1 and font.getlength(tk) <= max_w:
                lines.append(cur)
                cur = tk
            elif tk in _NO_HEAD and cur:
                lines.append(cur[:-1])
                cur = cur[-1] + tk
            else:
                lines.append(cur)
                cur = tk
        else:
            cur += tk
    if cur:
        lines.append(cur)
    return [l for l in lines if l.strip()] or [""]


def _fit_lines(text, face, size, max_w, max_lines, min_size=15, max_h=None,
               ratio=1.30):
    """从大字号往下试，找到"行数不超、总高度也不超"的最大字号。

    两个约束都得查：只查行数时，字号停在偏大的一档，四行字就会顶出卡片底板、
    压到下一张卡片上（实测第一版就溢出过）。
    """
    size = int(size)
    while size >= min_size:
        f = _font(face, size)
        lines = _wrap(text, f, max_w)
        fits = len(lines) <= max_lines and (max_h is None or len(lines) * size * ratio <= max_h)
        if fits:
            return f, lines, size
        size -= 2
    f = _font(face, min_size)
    lines = _wrap(text, f, max_w)
    if max_h:
        lines = lines[:max(1, int(max_h // (min_size * ratio)))]
    return f, lines[:max_lines] if lines else lines, min_size


def _draw_text(draw, xy, text, font, fill, stroke=None, stroke_w=0):
    if stroke and stroke_w:
        draw.text(xy, text, font=font, fill=fill, stroke_width=stroke_w, stroke_fill=stroke)
    else:
        draw.text(xy, text, font=font, fill=fill)


def _rounded_mask(w, h, radius):
    r = max(0, min(int(radius), min(w, h) // 2))
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    return m


def crop_cover(im, w, h, focus_y=0.5):
    """把一张真实图片按"填满不拉伸"裁成 w×h（center crop）。

    focus_y 是竖直取景位置：0.5 居中，0 贴顶。坐骑/角色展示图主体通常偏上，
    居中裁会把腿切掉，所以默认给一点点上偏（调用方传 0.42 左右）。
    """
    w, h = max(1, int(w)), max(1, int(h))
    iw, ih = im.size
    scale = max(w / float(iw), h / float(ih))
    nw, nh = max(w, int(round(iw * scale))), max(h, int(round(ih * scale)))
    im = im.resize((nw, nh), Image.LANCZOS)
    x = (nw - w) // 2
    y = int((nh - h) * max(0.0, min(1.0, focus_y)))
    return im.crop((x, y, x + w, y + h))


def _photo_layer(photo_png, box, radius, scrim="top"):
    """真图贴到画面上：圆角 + 压暗，保证压在图上的主标题还读得清。

    scrim="top" 是历史那条：主标题写在图的上方，所以顶部压暗、底部再收一道。
    scrim="none" 给满版大图用——那块标题在**下沿**，压暗由 _bottom_scrim 统一画，
    这里再压一次就是两层黑。
    """
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    src = Image.open(io.BytesIO(photo_png)).convert("RGB")
    src = ImageOps.exif_transpose(src)
    im = crop_cover(src, w, h, focus_y=PHOTO_FOCUS_Y).convert("RGBA")
    im.putalpha(_rounded_mask(w, h, radius))
    if scrim == "none":
        return im, (x0, y0)
    # 顶部压暗：主标题就写在这条带上，不压就是"白字花底"
    scr = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(scr)
    band = max(1, int(h * 0.42))
    for i in range(band):
        a = int(150 * (1.0 - i / float(band)))
        d.line([(0, i), (w, i)], fill=(0, 0, 0, a))
    im.alpha_composite(scr)
    # 底部再压一道，卡片面板紧贴图下沿时不会显得硬切
    fade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(fade)
    band2 = max(1, int(h * 0.16))
    for i in range(band2):
        a = int(90 * (i / float(band2)))
        d2.line([(0, h - band2 + i), (w, h - band2 + i)], fill=(0, 0, 0, a))
    im.alpha_composite(fade)
    return im, (x0, y0)


def _art_bounds(key, spec, skin, H):
    """这块画面留给插画/真图的高度区间（像素，下限、上限）。

    历史那四种沿用皮肤自带的 art_floor/art_cap，一个字都不改，保证老素材重排
    出来跟以前一样；新加的几种由布局自己定（大字报的图只能是一条窄带，
    满版大图则整幅都是图，套皮肤的上下限反而不成立）。
    """
    if key in ("top_art", "grid2", "list", "hero"):
        return (int(H * float(skin.get("art_floor", 0.50))),
                int(H * float(skin.get("art_cap", 0.72))))
    return (int(H * float(spec.get("art_floor", 0.26))),
            int(H * float(spec.get("art_cap", 0.52))))


def art_zone(plan, canvas_wh, skin=None, layout=None):
    """算出"图在哪一块"，给出图 prompt 和免费预览共用。

    返回 dict：slot（band_top/band_bottom/side_left/side_right/full）、
    frac（横带时占画面高度的比例）、col（竖栏时占画面宽度的比例）、
    top（横带在下方时，从画面顶部算起的起点比例）。
    """
    W, H = canvas_wh
    skin = _resolve_skin((plan or {}).get("style_key"), skin)
    key = _norm_layout(layout) or resolve_layout(plan, skin)
    spec = LAYOUTS[key]
    m = int(W * 0.062)
    inner = W - 2 * m
    gap = int(W * skin["gap"])
    cards = [c for c in (plan or {}).get("cards") or [] if (c.get("t") or c.get("d"))]
    foot_h = int(H * 0.042) if (plan or {}).get("footer") else 0
    cards_bottom = H - int(H * 0.024) - foot_h
    zone = {"layout": key, "slot": spec["art"], "frac": 0.0, "col": 0.0, "top": 0.0}
    if spec["art"] == "full":
        zone["frac"] = 1.0
        return zone
    tx0, txw = _text_column(spec, m, inner, gap)
    head = _head_metrics((plan or {}).get("title") or "",
                         (plan or {}).get("subtitle") or "", skin, H, txw, spec)
    boxes, top = _card_boxes(cards, W, H, m, inner, gap, cards_bottom, skin,
                             layout=key, text_x0=tx0, text_w=txw,
                             top_limit=head["top_limit"])
    if spec["art"] in ("side_left", "side_right"):
        zone["col"] = float(spec.get("col_frac", 0.40))
        zone["top"] = max(0.0, min(0.9, top / float(H)))
        return zone
    if spec["art"] == "band_bottom":
        # 卡片区下沿就是插画区上沿——注意别拿 cards_bottom 当块底，那是整块的天花板
        bb = boxes[-1]["y1"] if boxes else head["top_limit"]
        zone["frac"] = max(0.05, min(0.8, (cards_bottom - bb) / float(H)))
        zone["top"] = max(0.0, min(0.9, bb / float(H)))
        return zone
    zone["frac"] = max(0.30, min(0.86, top / float(H)))
    zone["cards_top"] = int(top)
    return zone


def photo_box_for(zone, W, H, m, gap):
    """按 art_zone 的结果决定真图贴在哪一块。返回 (box, 占画面高度比例)。"""
    z = zone or {}
    slot = z.get("slot", "band_top")
    if slot == "full":
        return (0, 0, W, H), 1.0
    if slot in ("side_left", "side_right"):
        y0 = int(H * max(0.02, float(z.get("top", 0.16))))
        y1 = int(H * 0.975)
        cw = int(W * (1 - float(z.get("col", 0.40))) - 2 * m)
        if slot == "side_right":
            return (m + cw + gap, y0, W - m, y1), (y1 - y0) / float(H)
        return (m, y0, m + cw, y1), (y1 - y0) / float(H)
    if slot == "band_bottom":
        y0 = int(H * max(0.05, float(z.get("top", 0.6)))) + gap
        y1 = int(H * 0.975)
        return (m, y0, W - m, max(y0 + 1, y1)), min(1.0, max(0.0, (y1 - y0) / float(H)))
    y0 = int(H * 0.022)
    y1 = max(y0 + 1, int(z.get("cards_top", H * float(z.get("frac", 0.6)))) - gap)
    return (m, y0, W - m, y1), (y1 - y0) / float(H)


def _panel_overlay(box, skin):
    """卡片底板的半透明层。必须**先贴板再写字**——反过来板会把字糊掉。"""
    x0, y0, x1, y1 = [int(v) for v in (box[0], box[1], box[2], box[3])]
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    sa = skin["stroke_alpha"]
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=skin["radius"],
                        fill=_rgb(skin["panel"]) + (skin["panel_alpha"],),
                        outline=(_rgb(skin["stroke"]) + (sa,) if sa else None),
                        width=max(1, int(min(w, h) * 0.012)) if sa else 1)
    return ov, (x0, y0)


def _rows_for(n, kind):
    """把卡片切成行。版式之间真正的差别就在这一步＋字号，只换颜色等于没换。

    stack / list：每条占一整行，读起来像步骤清单。
    grid2：两列并排（成对往下排），条数多时最省地方。
    hero：第一条通栏放大当结论，其余两列当支撑。
    split：左右两栏各放一半，用来做"不换 vs 换"这种对着看；条数为奇数时右栏
            会有一格是空的（用 None 占位，下面算框和写字都会跳过）。
    """
    if n <= 0:
        return []
    if kind in ("stack", "list"):
        return [[i] for i in range(n)]
    if kind == "split":
        half = (n + 1) // 2
        right = list(range(half, n))
        return [[j, right[j - half] if j - half < len(right) else None]
                for j in range(half)]
    rows, i = [], 0
    if kind == "hero":
        rows.append([0])
        i = 1
    while i < n:
        if i + 1 < n:
            rows.append([i, i + 1])
            i += 2
        else:
            rows.append([i])
            i += 1
    return rows


def _card_need(c, H, skin, cw, gutter, hero):
    """量一张卡片要多高：先按标称字号折行，行高＝标题 + 说明各行。"""
    pad = int(min(cw, H * 0.16) * 0.115)
    aw = max(40, cw - 2 * pad - gutter)
    t_size = int(H * (0.036 if hero else 0.0255))
    d_size = int(H * (0.0255 if hero else 0.0205))
    h = pad
    ct = (c.get("t") or "").strip()
    cd = (c.get("d") or "").strip()
    if ct:
        _, _, s = _fit_lines(ct, skin["title_face"], t_size, aw, 1, min_size=int(H * 0.017))
        h += int(s * 1.45)
    if cd:
        _, lines, s = _fit_lines(cd, skin["body_face"], d_size, aw, 6, min_size=int(H * 0.0150))
        h += len(lines) * int(s * 1.30)
    return h + pad


def _text_column(spec, m, inner, gap):
    """文字区的起点与可用宽：分栏布局要挤到没图的那一侧。"""
    col_w = int(inner * float(spec.get("col_frac", 0.40)))
    if spec["art"] == "side_right":
        return m, inner - col_w - gap
    if spec["art"] == "side_left":
        return m + col_w + gap, inner - col_w - gap
    return m, inner


def _head_metrics(title, subtitle, skin, H, text_w, spec):
    """量标题区（只算不画）：标题/副标的字体、行、字号，以及卡片区该从哪儿起。

    排版和算插画区（art_zone）必须走这一个函数——底图让出来的地方跟真正落字的地方
    错开半行，换布局时就会出现"图上空一块、字压在马上要留白的地方"。
    """
    scale = float(spec.get("title_scale", 1.0))
    big = scale > 1.05
    t_size = int(H * 0.050 * scale)
    if big:
        # 大字报：标题就是要大。先按一行去缩会一路缩到跟正文一样，那就没意义了，
        # 所以这种布局一开始就允许折两行，字号下限也抬高
        f_title, t_lines, t_size = _fit_lines(title, skin["title_face"], t_size, text_w, 2,
                                              min_size=int(H * 0.055))
    else:
        f_title, t_lines, t_size = _fit_lines(title, skin["title_face"], t_size, text_w, 1,
                                              min_size=int(H * 0.033))
        if len(t_lines) > 1:
            f_title, t_lines, t_size = _fit_lines(title, skin["title_face"], t_size, text_w, 2,
                                                  min_size=int(H * 0.030))
    overflow = len(t_lines) > 2
    t_lines = t_lines[:2]
    title_h = len(t_lines) * int(t_size * 1.24)
    f_sub, s_lines, s_size = None, [], 0
    if subtitle:
        f_sub, s_lines, s_size = _fit_lines(subtitle, skin["body_face"], int(H * 0.0245),
                                            int(text_w * 0.9), 2, min_size=int(H * 0.017))
        s_lines = s_lines[:2]
    sub_h = len(s_lines) * int(s_size * 1.30) if f_sub else 0
    title_top = int(H * 0.048)
    block_h = title_h + sub_h
    return {"f_title": f_title, "t_lines": t_lines, "t_size": t_size, "overflow": overflow,
            "f_sub": f_sub, "s_lines": s_lines, "s_size": s_size,
            "title_top": title_top, "block_h": block_h,
            "top_limit": title_top + block_h + int(H * 0.014)}


def _card_boxes(cards, W, H, m, inner, gap, cards_bottom, skin, layout=None,

                text_x0=None, text_w=None, top_limit=None):
    """按布局与内容量算卡片方框。返回 (方框 dict 列表, 卡片区顶部 y)。

    顶部 y 会换算成比例回给出图 prompt——告诉模型"下面这块只画底纹、别画具体东西"，
    两边共用同一个算法才不会错位。插画占比被 art_floor / art_cap 夹住：
    游戏史诗风至少要一半画面给插画，清单笔记风最多只给一半，不然四套风格出一样的构图。

    四种锚法（这才是"布局"真正在改的东西）：
      band_top     卡片贴底、插画拿剩下的上半 —— 历史行为，老四种走这条，算法没动
      band_bottom  卡片从标题下方开始往下排、插画拿剩下的下半
      side_left/…  卡片挤在文字栏里，插画是另一侧一整条竖栏
      full         卡片直接叠在整幅图上，贴底，不做上下限
    """
    key = _norm_layout(layout) or _norm_layout(skin.get("layout")) or DEFAULT_LAYOUT
    spec = LAYOUTS[key]
    kind = spec["rows"]
    slot = spec["art"]
    ox = m if text_x0 is None else int(text_x0)          # 文字区左边界（分栏布局会缩）
    ow = inner if text_w is None else int(text_w)        # 文字区可用宽
    gutter = int(W * 0.115) if kind == "list" else 0
    rows = _rows_for(len(cards), kind)
    if not rows:
        return [], int(H * float(skin.get("art_floor", 0.5)))

    col_w = (ow - gap) / 2.0

    def filled(row):
        return [i for i in row if i is not None]

    def width_of(row):
        return ow if len(filled(row)) == 1 else col_w

    needs = [max(_card_need(cards[i], H, skin, width_of(row), gutter,
                            kind == "hero" and i == 0) for i in filled(row))
             for row in rows]
    block = sum(needs) + gap * (len(rows) - 1)
    lo, hi = _art_bounds(key, spec, skin, H)
    tl = int(top_limit if top_limit is not None else H * 0.16)

    def shrink(avail):
        """地方不够就把行高等比压下去，让字号自己回退（不删内容、不重叠）。"""
        room = avail - gap * (len(rows) - 1)
        if room > 0 and sum(needs) > 0:
            k = room / float(sum(needs))
            return [max(int(H * 0.062), int(h * k)) for h in needs]
        return needs

    if slot == "band_bottom":
        top = tl
        if top + block > cards_bottom - lo:
            needs = shrink(cards_bottom - lo - top)
            block = sum(needs) + gap * (len(rows) - 1)
        # 卡片少的时候不往上下摊：宁可让下面那幅图大一点，也别在标题和卡片之间
        # 留一条空带——实测那样看着像"没排完"，比图大更难看
    elif slot in ("side_left", "side_right"):
        top = tl
        if top + block > cards_bottom:
            needs = shrink(cards_bottom - top)
            block = sum(needs) + gap * (len(rows) - 1)
    elif slot == "full":
        if block > cards_bottom - tl:
            needs = shrink(cards_bottom - tl)
            block = sum(needs) + gap * (len(rows) - 1)
        top = cards_bottom - block
    else:
        top = cards_bottom - block
        if top < lo:                      # 卡片要的地方比插画下限还大：压行高
            needs = shrink(cards_bottom - lo)
            top = lo
            block = sum(needs) + gap * (len(rows) - 1)
        elif top > hi:                    # 卡片太矮、插画会超标：多出来的高度摊成行间距
            extra = top - hi
            if len(rows) > 1:
                gap = int(gap + min(gap * 1.6, extra / float(len(rows) - 1)))
            top = hi

    boxes, y = [], top
    for r, row in enumerate(rows):
        single = len(filled(row)) == 1
        for pos, i in enumerate(row):
            if i is None:
                continue
            x0 = ox if single else ox + pos * (col_w + gap)
            x1 = (ox + ow) if single else (x0 + col_w)
            boxes.append({"i": i, "x0": int(x0), "y0": int(y), "x1": int(x1),
                          "y1": int(y + needs[r]), "hero": kind == "hero" and i == 0,
                          "gutter": gutter, "layout": key})
        y += needs[r] + gap
    return boxes, int(top)


def _bottom_scrim(W, H, top_y, strength=205):
    """满版大图那块下沿渐变：图铺满整幅后，标题和卡片都压在图上，不压暗就是白字花底。"""
    top_y = max(0, min(H - 2, int(top_y)))
    ov = Image.new("RGBA", (W, H - top_y), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    h = H - top_y
    for i in range(h):
        r = i / float(max(1, h - 1))
        a = int(strength * min(1.0, r * 1.55))
        d.line([(0, i), (W, i)], fill=(6, 8, 12, a))
    return ov, (0, top_y)


def _page_pill(draw, W, H, skin, page):
    """右上角那颗「k / N」：读者知道后面还有页，才不会被"这页没说完"当成讲完了。

    放在最后画，所以不会被卡片底板或真图盖住；深浅两套皮肤都靠面板色垫一层底，
    不直接压在图上写白字。
    """
    try:
        k, total = int(page[0]), int(page[1])
    except (TypeError, ValueError, IndexError):
        return
    if total < 2:
        return
    txt = "%d / %d" % (k, total)
    f = _font(skin["body_face"], max(16, int(H * 0.0175)))
    tw = f.getlength(txt)
    px, py = int(H * 0.0075), int(H * 0.0045)
    w, h = int(tw + px * 2), int(f.size + py * 2)
    x0 = W - int(W * 0.045) - w
    y0 = int(H * 0.022)
    draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=int(h / 2.0),
                           fill=_rgb(skin["panel"]) + (215,),
                           outline=_rgb(skin["stroke"]) + (int(skin["stroke_alpha"] or 0),),
                           width=2)
    _draw_text(draw, (x0 + px, y0 + py - int(f.size * 0.06)), txt, f, _rgb(skin["title"]))


def typeset(base_png, plan, ratio="9:16", skin=None, photo=None, page=None):
    """把 plan 里的中文排到 AI 底图上。返回 (png 字节, 版面自检 dict)。

    base_png 是出图模型给的**无字底图**字节；plan 走 ai_poster 的文案计划形状
    （title / subtitle / cards[{t,d}] / footer / style_key / layout_key）。
    photo 非空时是用户上传的真实图片字节：它顶掉整块插画区，标题压在图上，
    卡片往下让——这条分支根本不需要 AI 画底图，所以不花钱。

    顺序是四遍：量标题 → 算版面 → 贴所有半透明底板 → 写所有文字。中间任何一步
    反过来都会出现"板盖字"或"字浮在板外面"。
    """
    img = Image.open(io.BytesIO(base_png)).convert("RGB")
    W, H = img.size
    skin = _resolve_skin((plan or {}).get("style_key"), skin)
    draw = ImageDraw.Draw(img, "RGBA")
    m = int(W * 0.062)
    inner = W - 2 * m
    problems = []

    layout = resolve_layout(plan, skin)
    spec = LAYOUTS[layout]
    overlay = bool(spec.get("overlay"))
    side = spec["art"] in ("side_left", "side_right")
    col_frac = float(spec.get("col_frac", 0.40))

    title = (plan.get("title") or "").strip()
    subtitle = (plan.get("subtitle") or "").strip()
    footer = (plan.get("footer") or "").strip()
    cards = [c for c in (plan.get("cards") or []) if (c.get("t") or c.get("d"))]

    foot_h = int(H * 0.042) if footer else 0
    cards_bottom = H - int(H * 0.024) - foot_h
    gap = int(W * skin["gap"])

    # 分栏布局：标题和卡片都挤到没有图的那一侧
    text_x0, text_w = _text_column(spec, m, inner, gap)

    # ── 第 0 遍：先把标题量出来（只算不画）──
    # 顶部锚的布局（图下文上、双列对比、左右分栏）得先知道标题占多高，
    # 卡片才知道从哪儿开始排；贴底的老布局用不到这个数，但量的过程完全一样。
    head = _head_metrics(title, subtitle, skin, H, text_w, spec)
    if head["overflow"]:
        problems.append("主标题折行超过 2 行，已截断")
    f_title, t_lines, t_size = head["f_title"], head["t_lines"], head["t_size"]
    f_sub, s_lines, s_size = head["f_sub"], head["s_lines"], head["s_size"]
    title_top, block_h, top_limit = head["title_top"], head["block_h"], head["top_limit"]

    # ── 第一遍：按内容量算每张卡片的方框 ──
    # 行高跟着文字走、整块按布局锚定，插画区就自动拿到剩下的地方。反过来
    # （先划死网格再往里塞字）会让字少的卡片顶着一片空白，实测比竞品松垮的主要原因就在这。
    boxes, cards_top = _card_boxes(cards, W, H, m, inner, gap, cards_bottom, skin,
                                   layout=layout, text_x0=text_x0, text_w=text_w,
                                   top_limit=top_limit)

    # 这布局能装几条是写死的：满版大图塞 4 条就把图压成一条缝。不拦出图（预览的意义
    # 就是让人看见会爆），只把话说明白——往哪个布局退、或者干脆拆成多页。
    lo_n, hi_n = int(spec.get("min_cards", 1)), int(spec.get("max_cards", 99))
    if len(cards) > hi_n:
        problems.append("「%s」最多放 %d 条要点，现在 %d 条 → 改用「图上文下」，或把多出来的拆到下一页"
                        % (spec["label"], hi_n, len(cards)))
    elif cards and len(cards) < lo_n:
        problems.append("「%s」至少要 %d 条才站得住，现在 %d 条，版面会空一块"
                        % (spec["label"], lo_n, len(cards)))

    # 图/真图占的那一块：跟排版同一个算法，出图 prompt 与免费预览共用
    zone = {"layout": layout, "slot": spec["art"], "col": col_frac,
            "frac": 1.0 if overlay else 0.0, "cards_top": int(cards_top), "top": 0.0}
    if spec["art"] == "band_bottom":
        bb = boxes[-1]["y1"] if boxes else top_limit
        zone["frac"] = max(0.05, min(0.8, (cards_bottom - bb) / float(H)))
        zone["top"] = max(0.0, min(0.9, bb / float(H)))
    elif side:
        zone["top"] = max(0.0, min(0.9, top_limit / float(H)))
    elif not overlay:
        zone["frac"] = max(0.30, min(0.86, cards_top / float(H)))

    # ── 第 1.5 遍：贴主视觉（真图顶掉插画区，必须在贴卡片板之前）──
    photo_info = None
    if photo:
        pbox, pfrac = photo_box_for(zone, W, H, m, gap)
        floor = PHOTO_MIN_FRAC if spec["art"] != "full" else 0.9
        if pfrac < floor:
            problems.append("照片区只剩画面高度的 %d%%，卡片文字太多，建议减少卡片或缩短说明"
                            % round(pfrac * 100))
        try:
            layer, pos = _photo_layer(photo, pbox,
                                       radius=0 if overlay else int(W * 0.045),
                                       scrim="none" if overlay else "top")
            img.paste(layer, pos, layer)
            photo_info = {"box": [int(v) for v in pbox], "frac": round(pfrac, 3),
                          "bytes": len(photo)}
        except Exception as e:
            problems.append("照片贴失败（%s），本张已退回纯底图" % str(e)[:60])
    if overlay:
        scr_top = min(cards_top, H - int(H * 0.30)) - int(block_h + H * 0.03)
        ov, pos = _bottom_scrim(W, H, max(0, scr_top))
        img.paste(ov, pos, ov)

    # ── 第二遍：贴底板（清单风再补一条左侧色带，靠它而不是靠面板边框撑"第几条"）──
    for b in boxes:
        ov, pos = _panel_overlay((b["x0"], b["y0"], b["x1"], b["y1"]), skin)
        img.paste(ov, pos, ov)
        if b["gutter"]:
            draw.rectangle((b["x0"], b["y0"], b["x0"] + max(3, int(W * 0.007)), b["y1"]),
                           fill=_rgb(skin["accent"]))
    if spec.get("divider") and len(boxes) > 1:
        # 双列对比的中缝：没有这条线，两栏就只是"两栏"，读不出"对着比"
        cx = int(text_x0 + text_w / 2.0)
        y0 = min(b["y0"] for b in boxes)
        y1 = max(b["y1"] for b in boxes)
        draw.line([(cx, y0), (cx, y1)], fill=_rgb(skin["accent"]) + (int(skin["stroke_alpha"] * 0.8),),
                  width=max(2, int(W * 0.004)))

    # ── 第三遍：写字。标题压在插画上靠描边保可读，卡片字压在底板上靠对比度保可读 ──
    # 主标题先按"必须一行"去缩字号，缩到下限还放不下才允许折第二行：
    # 13 字的标题直接给两行，第二行往往只剩两三个字，很难看
    if overlay and boxes:
        ty = max(int(H * 0.03), boxes[0]["y0"] - int(block_h + H * 0.022))
    elif overlay:
        ty = cards_bottom - block_h - int(H * 0.03)
    else:
        ty = title_top
    for ln in t_lines:
        tx = ((W - f_title.getlength(ln)) / 2.0 if not side else float(text_x0))
        _draw_text(draw, (tx, ty), ln, f_title,
                   _rgb(skin["title"]), _rgb(skin["title_stroke"]),
                   max(2, int(t_size * 0.07)))
        ty += int(t_size * 1.24)
    for ln in s_lines:
        sx = ((W - f_sub.getlength(ln)) / 2.0 if not side else float(text_x0))
        _draw_text(draw, (sx, ty + int(H * 0.004)), ln, f_sub,
                   _rgb(skin["title"]), _rgb(skin["title_stroke"]),
                   max(1, int(s_size * 0.08)))
        ty += int(s_size * 1.30)

    for b in boxes:
        c = cards[b["i"]]
        pad = int(min(b["x1"] - b["x0"], b["y1"] - b["y0"]) * 0.115)
        cx, cy = b["x0"] + pad + b["gutter"], b["y0"] + pad
        cw = (b["x1"] - b["x0"]) - 2 * pad - b["gutter"]
        hero = b["hero"]
        if b["gutter"]:
            nf = _font(skin["title_face"], int(H * 0.030))
            _draw_text(draw, (b["x0"] + pad, cy), "%02d" % (b["i"] + 1), nf, _rgb(skin["accent"]))
        ct = (c.get("t") or "").strip()
        cd = (c.get("d") or "").strip()
        if ct:
            tf, tfl, ts = _fit_lines(ct, skin["title_face"],
                                     int(H * (0.036 if hero else 0.0255)), cw, 1,
                                     min_size=int(H * 0.017))
            _draw_text(draw, (cx, cy), tfl[0], tf, _rgb(skin["accent"]))
            cy += int(ts * 1.45)
        if cd:
            avail = max(int(H * 0.026), b["y1"] - pad - cy)
            df, dfl, ds = _fit_lines(cd, skin["body_face"],
                                     int(H * (0.0255 if hero else 0.0205)), cw,
                                     5, min_size=int(H * 0.0150), max_h=avail)
            for ln in dfl:
                if cy + ds * 1.30 > b["y1"] - int(pad * 0.35):
                    problems.append("第 %d 张卡片说明放不下，已截到最后可读的一行" % (b["i"] + 1))
                    break
                _draw_text(draw, (cx, cy), ln, df, _rgb(skin["ink"]))
                cy += int(ds * 1.30)

    if footer:
        ff, ffl, fs = _fit_lines(footer, skin["body_face"], int(H * 0.0165), inner, 1,
                                 min_size=int(H * 0.0135))
        _draw_text(draw, ((W - ff.getlength(ffl[0])) / 2.0, H - foot_h + int(H * 0.006)),
                   ffl[0], ff, _rgb(skin["muted"]))

    _page_pill(draw, W, H, skin, page)

    out = io.BytesIO()
    img.save(out, format="PNG")
    chk = {
        "ok": not problems, "problems": problems,
        "canvas": "%dx%d" % (W, H), "cards": len(cards),
        "engine": "pil-typeset", "skin": (plan or {}).get("style_key") or DEFAULT_SKIN,
        "layout": layout, "layout_label": spec["label"],
    }
    if page and int(page[1] or 1) > 1:
        chk["page"] = [int(page[0]), int(page[1])]
    if photo_info:
        chk["photo"] = photo_info
    return out.getvalue(), chk


def text_zone_ratio(plan, canvas_wh, skin=None):
    """卡片区从画面高度的哪一段开始（0~1）。出图 prompt 用同一个数留白，
    排版和画面才不会一个让了地方另一个不知道。

    只对上/下横带成立。分栏和满版那种"图不在上半"的布局，prompt 要改走
    art_zone_hint，别再拿这一个数硬说"上部是插画"。
    """
    z = art_zone(plan, canvas_wh, skin)
    if not [c for c in (plan or {}).get("cards") or [] if (c.get("t") or c.get("d"))]:
        return 0.62
    return float(z.get("frac") or 0.62)


def art_zone_hint(zone, skin):
    """把 art_zone 的结果翻成出图 prompt 里那句构图要求。

    排版让出来的地方和模型画的地方必须同一个算法算出来，否则换布局时底图还是
    老构图（上半一幅大插画），文字却挪到了下面，中间空一条白带。
    返回 (构图那一行, 过渡那一行, 底部区域怎么描述)。
    """
    slot = (zone or {}).get("slot", "band_top")
    pct = int(round(float((zone or {}).get("frac") or 0.6) * 100))
    cpct = int(round(float((zone or {}).get("col") or 0.4) * 100))
    rest = (skin or {}).get("bottom", "暗色氛围底纹")
    if slot == "full":
        return ("整幅都是这幅主视觉，铺满、四边不留边；下沿约三成自然变暗变简单，"
                "**那里要放标题和两行文字，别画任何具体物体。**",
                "从上到下用光影自然过渡，不要出现硬边横线",
                "下沿那块较暗的连续背景")
    if slot == "side_right":
        return ("画面右侧 %d%% 宽是一条通高的竖向插画带，主体画在这条带里；"
                "左侧 %d%% 只画%s，**不要画任何具体物体、人物、边框或卡片**，那块是留给文字的。"
                % (cpct, 100 - cpct, rest),
                "左右两区之间用光影自然过渡，不要出现硬边竖线",
                "左侧那块连续背景")
    if slot == "side_left":
        return ("画面左侧 %d%% 宽是一条通高的竖向插画带，主体画在这条带里；"
                "右侧 %d%% 只画%s，**不要画任何具体物体、人物、边框或卡片**，那块是留给文字的。"
                % (cpct, 100 - cpct, rest),
                "左右两区之间用光影自然过渡，不要出现硬边竖线",
                "右侧那块连续背景")
    if slot == "band_bottom":
        return ("构图倒过来：上部 %d%% 是文字区，只画%s，**不要画任何具体物体、人物、边框或卡片**；"
                "下部 %d%% 才是这幅主视觉，占满整个宽度、细节画满。"
                % (100 - pct, rest, pct),
                "上下两区之间用光影或雾气自然过渡，不要出现硬边横线",
                "上部那块连续背景")
    return ("上部 %d%% 是这幅主视觉插画，占满整个宽度、细节画满；下部 %d%% 只画%s，"
            "**不要画任何具体物体、人物、边框或卡片**，那块地方是留给文字的。"
            % (pct, 100 - pct, rest),
            "上下两区之间用光影或雾气自然过渡，不要出现硬边横线",
            "下部区域是一块连续的背景")


def _resolve_skin(style_key, skin):
    """外部显式塞了皮肤就用它——拆参考图那条路上草稿还没落盘，但得先能预览。"""
    return skin if isinstance(skin, dict) and skin.get("layout") else skin_for(style_key)


def _art_glow(img, box, skin):
    """给"这块要放图、但这次不出图"的那块补一层氛围光。

    为什么补：真图只当第一页主视觉，第 2、3 页在 0 元这条路上就是纯色渐变，
    空着六成画面看着像没排完。这里按皮肤自己的强调色压一层极淡的光，
    让它读起来是"背景氛围"而不是"缺一张图"。alpha 全程 ≤26，不会盖住文字。
    """
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = x1 - x0, y1 - y0
    if w < 12 or h < 12:
        return
    acc = _rgb(skin.get("accent", "#ffffff"))
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    step = max(1, h // 160)
    for y in range(0, h, step):
        t = y / float(max(1, h - 1))
        a = int(26 * max(0.0, 1.0 - abs(t - 0.34) * 1.9))
        if a <= 0:
            continue
        inset = int(w * 0.05 * abs(t - 0.5) * 2)
        od.line([(inset, y), (w - inset, y)], fill=acc + (a,))
    img.paste(ov, (x0, y0), ov)


def placeholder_base(style_key, W, H, art_frac=0.6, skin=None, art_hint=True,
                     plan=None, zone=None):
    """按风格预设的配色生成一张占位底图，给"免费预览"用。

    预览要值不值 5 毛钱，取决于它和成品像不像：尺寸 1:1、配色取同一套皮肤、
    分区用同一个算法，所以版面上看到的就是出图后会看到的样子。
    唯一缺的是插画内容本身——那块只画一个浅色框和一句说明。
    art_hint=False 用于"用户上传真图当主视觉"那条分支：那块马上要被真图盖住，
    再画个灰框写"出图时才生成"就成了穿帮。
    传了 plan（或 zone）时灰框跟着布局走：图在下就画在下、分左右栏就画成竖栏、
    满版就不画框——不这么做的话换布局时预览看不出换了。
    """
    skin = _resolve_skin(style_key, skin)
    top, bot = _rgb(skin.get("bg_top", "#1a1a1a")), _rgb(skin.get("bg_bot", "#0a0a0a"))
    img = Image.new("RGB", (int(W), int(H)), top)
    d = ImageDraw.Draw(img, "RGBA")
    for y in range(int(H)):                      # 纵向渐变，越往下越接近卡片底
        r = y / float(max(1, H - 1))
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] * (1 - r) + bot[i] * r) for i in range(3)))
    z = zone or (art_zone(plan, (W, H), skin) if plan is not None else None)
    if not art_hint:
        # 这张是要真落盘的底图（真图模式、以及多页里没轮到插页图的那几页）：
        # 不画灰框和说明，但按同一块版面给"该放图的那一块"补一层氛围光，
        # 否则纯色底会空着六成，看着像没排完
        if plan is not None:
            box, _frac = photo_box_for(z, W, H, int(W * 0.062), int(W * skin["gap"]))
            _art_glow(img, box, skin)
        b = io.BytesIO()
        img.save(b, format="PNG")
        return b.getvalue()
    slot = (z or {}).get("slot", "band_top")
    ay = int(H * max(0.25, min(0.85, art_frac)))
    if slot == "full":
        return _art_placeholder(img, d, (0, 0, int(W), int(H)), skin,
                                "整幅都是这张图", draw_box=False)
    m = int(W * 0.062)
    if slot in ("side_left", "side_right"):
        col_w = int((W - 2 * m) * float(z.get("col", 0.40)))
        y0 = int(H * max(0.06, float(z.get("top", 0.2))))
        box = (m, y0, m + col_w, int(H * 0.97)) if slot == "side_left" \
            else (W - m - col_w, y0, W - m, int(H * 0.97))
        return _art_placeholder(img, d, box, skin, "这条竖栏是插画")
    if slot == "band_bottom":
        y0 = int(H * max(0.2, float(z.get("top", 0.6))))
        return _art_placeholder(img, d, (int(W * 0.05), y0, int(W * 0.95),
                                         int(H * 0.955)), skin, "这块是 AI 插画区")
    # 图上文下（历史那条）：框的位置、字号、那句说明都保持原样，
    # 老素材重排出来的预览不能因为这次重构而变了样子
    box = (int(W * 0.05), int(H * 0.03), int(W * 0.95), ay - int(H * 0.02))
    d.rounded_rectangle(box, radius=18, outline=(128, 128, 128, 90), width=2)
    f = _font(skin["body_face"], max(14, int(H * 0.018)))
    tip = "这块是 AI 插画区 · 出图时才生成"
    tw = f.getlength(tip)
    d.text(((W - tw) / 2.0, (box[1] + box[3]) / 2.0 - H * 0.01), tip, font=f,
           fill=_rgb(skin["muted"]))
    b = io.BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()



def _art_placeholder(img, d, box, skin, tip_text, draw_box=True):
    """灰框 + 一句说明：位置由布局决定，样式沿用同一套皮肤。

    字号必须同时被框的宽和高夹住——竖栏那种窄框按高度算字号，一句话会横着
    溢出到文字区里去（第一版就这么穿帮过）。
    """
    x0, y0, x1, y1 = [int(v) for v in box]
    bw, bh = x1 - x0, y1 - y0
    if bw < 8 or bh < 8:
        b = io.BytesIO()
        img.save(b, format="PNG")
        return b.getvalue()
    if draw_box:
        d.rounded_rectangle((x0, y0, x1, y1), radius=18, outline=(128, 128, 128, 90), width=2)
    tip = tip_text
    size = max(12, min(int(bh * 0.05), int(bw * 0.13)))
    f = _font(skin["body_face"], size)
    while f.getlength(tip) > bw * 0.9 and len(tip) > 2:
        tip = tip[:len(tip) - 2]
    if f.getlength(tip) > bw * 0.9:
        size = max(11, int(size * bw * 0.9 / f.getlength(tip)))
        f = _font(skin["body_face"], size)
    tw = f.getlength(tip)
    d.text(((x0 + x1 - tw) / 2.0, (y0 + y1) / 2.0 - f.size * 0.6), tip, font=f,
           fill=_rgb(skin["muted"]))
    b = io.BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()


