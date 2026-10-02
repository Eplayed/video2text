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
        "layout": "stack", "art_floor": 0.50, "art_cap": 0.74
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


def _photo_layer(photo_png, box, radius):
    """真图贴到画面上：圆角 + 顶部压暗，保证压在图上的主标题还读得清。"""
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    src = Image.open(io.BytesIO(photo_png)).convert("RGB")
    src = ImageOps.exif_transpose(src)
    im = crop_cover(src, w, h, focus_y=PHOTO_FOCUS_Y).convert("RGBA")
    im.putalpha(_rounded_mask(w, h, radius))
    # 顶部压暗：主标题就写在这条带上，不压就是"白字花底"
    scrim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(scrim)
    band = max(1, int(h * 0.42))
    for i in range(band):
        a = int(150 * (1.0 - i / float(band)))
        d.line([(0, i), (w, i)], fill=(0, 0, 0, a))
    im.alpha_composite(scrim)
    # 底部再压一道，卡片面板紧贴图下沿时不会显得硬切
    fade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(fade)
    band2 = max(1, int(h * 0.16))
    for i in range(band2):
        a = int(90 * (i / float(band2)))
        d2.line([(0, h - band2 + i), (w, h - band2 + i)], fill=(0, 0, 0, a))
    im.alpha_composite(fade)
    return im, (x0, y0)


def photo_box_for(W, H, m, cards_top, gap):
    """照片能占的那块：从顶部留白一直到卡片区上方。返回 (box, 占画面高度比例)。"""
    y0 = int(H * 0.022)
    y1 = max(y0 + 1, int(cards_top) - gap)
    box = (m, y0, W - m, y1)
    return box, (y1 - y0) / float(H)


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


def _rows_for(n, layout):
    """把卡片切成行。版式之间真正的差别就在这一步＋字号，只换颜色等于没换。

    stack / list：每条占一整行，读起来像步骤清单。
    grid2：两列并排，条数多时最省地方。
    hero：第一条通栏放大当结论，其余两列当支撑。
    """
    if n <= 0:
        return []
    if layout in ("stack", "list"):
        return [[i] for i in range(n)]
    rows, i = [], 0
    if layout == "hero":
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


def _card_boxes(cards, W, H, m, inner, gap, cards_bottom, skin):
    """按版式与内容量算卡片方框。返回 (方框 dict 列表, 卡片区顶部 y)。

    顶部 y 会换算成比例回给出图 prompt——告诉模型"下面这块只画底纹、别画具体东西"，
    两边共用同一个算法才不会错位。插画占比被皮肤的 art_floor / art_cap 夹住：
    游戏史诗风至少要一半画面给插画，清单笔记风最多只给一半，不然四套风格出一样的构图。
    """
    layout = skin.get("layout", "grid2")
    col_w = (inner - gap) / 2.0
    gutter = int(W * 0.115) if layout == "list" else 0
    rows = _rows_for(len(cards), layout)
    if not rows:
        return [], int(H * float(skin.get("art_floor", 0.5)))

    def width_of(row):
        return inner if len(row) == 1 else col_w

    needs = [max(_card_need(cards[i], H, skin, width_of(row), gutter,
                            layout == "hero" and i == 0) for i in row)
             for row in rows]
    block = sum(needs) + gap * (len(rows) - 1)
    lo = int(H * float(skin.get("art_floor", 0.50)))   # 插画至少占这么多 → 卡片区不能顶得比这更高
    hi = int(H * float(skin.get("art_cap", 0.72)))     # 插画至多占这么多 → 卡片区不能缩到比这更低
    top = cards_bottom - block
    if top < lo:                       # 卡片要的地方比插画下限还大：压行高，让字号自己回退
        room = cards_bottom - lo - gap * (len(rows) - 1)
        if room > 0 and sum(needs) > 0:
            k = room / float(sum(needs))
            needs = [max(int(H * 0.062), int(h * k)) for h in needs]
        top = lo
        block = sum(needs) + gap * (len(rows) - 1)
    elif top > hi:                     # 卡片太矮、插画会超标：多出来的高度摊成行间距，不把面板拉成空壳
        extra = top - hi
        if len(rows) > 1:
            gap = int(gap + min(gap * 1.6, extra / float(len(rows) - 1)))
        top = hi

    boxes, y = [], top
    for r, row in enumerate(rows):
        full = len(row) == 1
        for slot, i in enumerate(row):
            x0 = m if full else m + slot * (col_w + gap)
            x1 = (m + inner) if full else (x0 + col_w)
            boxes.append({"i": i, "x0": int(x0), "y0": int(y), "x1": int(x1),
                          "y1": int(y + needs[r]), "hero": layout == "hero" and i == 0,
                          "gutter": gutter, "layout": layout})
        y += needs[r] + gap
    return boxes, int(top)


def typeset(base_png, plan, ratio="9:16", skin=None, photo=None):
    """把 plan 里的中文排到 AI 底图上。返回 (png 字节, 版面自检 dict)。

    base_png 是出图模型给的**无字底图**字节；plan 走 ai_poster 的文案计划形状
    （title / subtitle / cards[{t,d}] / footer / style_key）。
    photo 非空时是用户上传的真实图片字节：它顶掉整块插画区，标题压在图上，
    卡片往下让——这条分支根本不需要 AI 画底图，所以不花钱。

    顺序是三遍：算版面 → 贴所有半透明底板 → 写所有文字。中间任何一步反过来都会
    出现"板盖字"或"字浮在板外面"。
    """
    img = Image.open(io.BytesIO(base_png)).convert("RGB")
    W, H = img.size
    skin = _resolve_skin((plan or {}).get("style_key"), skin)
    draw = ImageDraw.Draw(img, "RGBA")
    m = int(W * 0.062)
    inner = W - 2 * m
    problems = []

    title = (plan.get("title") or "").strip()
    subtitle = (plan.get("subtitle") or "").strip()
    footer = (plan.get("footer") or "").strip()
    cards = [c for c in (plan.get("cards") or []) if (c.get("t") or c.get("d"))]

    # ── 第一遍：按内容量算每张卡片的方框 ──
    # 行高跟着文字走、整块贴底，插画区就自动拿到剩下的全部高度。反过来（先划死网格
    # 再往里塞字）会让字少的卡片顶着一片空白，实测比竞品松垮的主要原因就在这。
    foot_h = int(H * 0.042) if footer else 0
    cards_bottom = H - int(H * 0.024) - foot_h
    gap = int(W * skin["gap"])
    boxes, cards_top = _card_boxes(cards, W, H, m, inner, gap, cards_bottom, skin)

    # ── 第 1.5 遍：贴用户上传的真图（顶掉插画区，必须在贴卡片板之前）──
    photo_info = None
    if photo:
        pbox, pfrac = photo_box_for(W, H, m, cards_top, gap)
        if pfrac < PHOTO_MIN_FRAC:
            problems.append("照片区只剩画面高度的 %d%%，卡片文字太多，建议减少卡片或缩短说明"
                            % round(pfrac * 100))
        try:
            layer, pos = _photo_layer(photo, pbox, radius=int(W * 0.045))
            img.paste(layer, pos, layer)
            photo_info = {"box": [int(v) for v in pbox], "frac": round(pfrac, 3),
                          "bytes": len(photo)}
        except Exception as e:
            problems.append("照片贴失败（%s），本张已退回纯底图" % str(e)[:60])

    # ── 第二遍：贴底板（清单风再补一条左侧色带，靠它而不是靠面板边框撑"第几条"）──
    for b in boxes:
        ov, pos = _panel_overlay((b["x0"], b["y0"], b["x1"], b["y1"]), skin)
        img.paste(ov, pos, ov)
        if b["gutter"]:
            draw.rectangle((b["x0"], b["y0"], b["x0"] + max(3, int(W * 0.007)), b["y1"]),
                           fill=_rgb(skin["accent"]))

    # ── 第三遍：写字。标题压在插画上靠描边保可读，卡片字压在底板上靠对比度保可读 ──
    # 主标题先按"必须一行"去缩字号，缩到下限还放不下才允许折第二行：
    # 13 字的标题直接给两行，第二行往往只剩两三个字，很难看
    t_size = int(H * 0.050)
    f_title, t_lines, t_size = _fit_lines(title, skin["title_face"], t_size, inner, 1,
                                          min_size=int(H * 0.033))
    if len(t_lines) > 1:
        f_title, t_lines, t_size = _fit_lines(title, skin["title_face"], t_size, inner, 2,
                                              min_size=int(H * 0.030))
    if len(t_lines) > 2:
        problems.append("主标题折行超过 2 行，已截断")
    y = int(H * 0.048)
    for ln in t_lines[:2]:
        _draw_text(draw, ((W - f_title.getlength(ln)) / 2.0, y), ln, f_title,
                   _rgb(skin["title"]), _rgb(skin["title_stroke"]),
                   max(2, int(t_size * 0.07)))
        y += int(t_size * 1.24)
    if subtitle:
        f_sub, s_lines, s_size = _fit_lines(subtitle, skin["body_face"], int(H * 0.0245),
                                            int(inner * 0.9), 2, min_size=int(H * 0.017))
        for ln in s_lines[:2]:
            _draw_text(draw, ((W - f_sub.getlength(ln)) / 2.0, y + int(H * 0.004)), ln, f_sub,
                       _rgb(skin["title"]), _rgb(skin["title_stroke"]),
                       max(1, int(s_size * 0.08)))
            y += int(s_size * 1.30)

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

    out = io.BytesIO()
    img.save(out, format="PNG")
    chk = {
        "ok": not problems, "problems": problems,
        "canvas": "%dx%d" % (W, H), "cards": len(cards),
        "engine": "pil-typeset", "skin": (plan or {}).get("style_key") or DEFAULT_SKIN,
    }
    if photo_info:
        chk["photo"] = photo_info
    return out.getvalue(), chk

def text_zone_ratio(plan, canvas_wh, skin=None):
    """卡片区从画面高度的哪一段开始（0~1）。出图 prompt 用同一个数留白，
    排版和画面才不会一个让了地方另一个不知道。"""
    W, H = canvas_wh
    skin = _resolve_skin((plan or {}).get("style_key"), skin)
    m = int(W * 0.062)
    inner = W - 2 * m
    gap = int(W * skin["gap"])
    cards = [c for c in (plan or {}).get("cards") or [] if (c.get("t") or c.get("d"))]
    foot_h = int(H * 0.042) if (plan or {}).get("footer") else 0
    cards_bottom = H - int(H * 0.024) - foot_h
    if not cards:
        return 0.62
    _boxes, top = _card_boxes(cards, W, H, m, inner, gap, cards_bottom, skin)
    return max(0.30, min(0.86, top / float(H)))

def _resolve_skin(style_key, skin):
    """外部显式塞了皮肤就用它——拆参考图那条路上草稿还没落盘，但得先能预览。"""
    return skin if isinstance(skin, dict) and skin.get("layout") else skin_for(style_key)


def placeholder_base(style_key, W, H, art_frac=0.6, skin=None, art_hint=True):
    """按风格预设的配色生成一张占位底图，给"免费预览"用。

    预览要值不值 5 毛钱，取决于它和成品像不像：尺寸 1:1、配色取同一套皮肤、
    分区用同一个 text_zone_ratio，所以版面上看到的就是出图后会看到的样子。
    唯一缺的是插画内容本身——那块只画一个浅色框和一句说明。
    art_hint=False 用于"用户上传真图当主视觉"那条分支：那块马上要被真图盖住，
    再画个灰框写"出图时才生成"就成了穿帮。
    """
    skin = _resolve_skin(style_key, skin)
    top, bot = _rgb(skin.get("bg_top", "#1a1a1a")), _rgb(skin.get("bg_bot", "#0a0a0a"))
    img = Image.new("RGB", (int(W), int(H)), top)
    d = ImageDraw.Draw(img, "RGBA")
    for y in range(int(H)):                      # 纵向渐变，越往下越接近卡片底
        r = y / float(max(1, H - 1))
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] * (1 - r) + bot[i] * r) for i in range(3)))
    if art_hint:
        ay = int(H * max(0.25, min(0.85, art_frac)))
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
