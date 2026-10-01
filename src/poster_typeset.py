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
from PIL import Image, ImageDraw, ImageFont

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
        "radius": 12, "gap": 0.020,
        "bottom": "暗色云雾与极淡的石纹底纹，四角有微弱余烬光点"
    },
    "tool_review": {
        "title_face": "sans_bold", "body_face": "sans_book",
        "title": "#eaf6ff", "title_stroke": "#0a1020",
        "ink": "#e9f2ff", "muted": "#9fb2cc", "accent": "#4fd1ff",
        "panel": "#101a33", "panel_alpha": 150, "stroke": "#4fd1ff", "stroke_alpha": 120,
        "radius": 22, "gap": 0.020,
        "bottom": "深蓝紫渐变与极淡的网格光点，越往下越暗"
    },
    "checklist": {
        "title_face": "sans_bold", "body_face": "sans_book",
        "title": "#2b2b2b", "title_stroke": "#f7f2e6",
        "ink": "#2f2d29", "muted": "#7d766b", "accent": "#d9694a",
        "panel": "#fffdf6", "panel_alpha": 214, "stroke": "#d9694a", "stroke_alpha": 90,
        "radius": 8, "gap": 0.022,
        "bottom": "暖米色纸纹底，带极淡的纤维质感"
    },
    "minimal": {
        "title_face": "sans_bold", "body_face": "sans_book",
        "title": "#1a1a1a", "title_stroke": "#ffffff",
        "ink": "#1a1a1a", "muted": "#8b8b8b", "accent": "#d92b2b",
        "panel": "#ffffff", "panel_alpha": 226, "stroke": "#d92b2b", "stroke_alpha": 0,
        "radius": 0, "gap": 0.026,
        "bottom": "干净的纯色留白，不要任何纹理"
    },
}
DEFAULT_SKIN = "tool_review"


def skin_for(style_key):
    return TYPE_SKINS.get(style_key) or TYPE_SKINS[DEFAULT_SKIN]


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


def _card_boxes(cards, W, H, m, inner, gap, cards_bottom, skin):
    """按内容量排卡片网格：2 列、奇数末张通栏、行高取该行最需要的那张、整块贴底。

    返回 (方框列表, 卡片区顶部 y)。顶部 y 会换算成比例回给出图 prompt——
    告诉模型"下面这块只画暗色氛围、别画具体东西"，两边共用同一个算法才不会错位。
    """
    n = len(cards)
    two_col = n > 2
    rows = ((n + 1) // 2) if two_col else n
    col_w = (inner - gap) / 2.0
    widths = []
    for i in range(n):
        full = (not two_col) or (n % 2 == 1 and i == n - 1)
        widths.append(inner if full else col_w)
    # 先按标称字号量每张卡片要多高
    needs = []
    for c, cw in zip(cards, widths):
        pad = int(min(cw, H * 0.16) * 0.115)
        avail_w = cw - 2 * pad
        ct, cd = (c.get("t") or "").strip(), (c.get("d") or "").strip()
        h = pad
        if ct:
            _, lines, size = _fit_lines(ct, skin["title_face"], int(H * 0.0255),
                                        avail_w, 1, min_size=int(H * 0.017))
            h += int(size * 1.45)
        if cd:
            _, lines, size = _fit_lines(cd, skin["body_face"], int(H * 0.0205),
                                        avail_w, 6, min_size=int(H * 0.0150))
            h += len(lines) * int(size * 1.30)
        needs.append(h + pad)
    row_need = []
    for r in range(rows):
        idxs = [r * 2, r * 2 + 1] if two_col else [r]
        row_need.append(max(needs[i] for i in idxs if i < n))
    block = sum(row_need) + gap * (rows - 1)
    avail = cards_bottom - int(H * 0.40)
    if block > avail and block > 0:
        k = avail / float(block)
        row_need = [max(int(H * 0.075), int(h * k)) for h in row_need]
        block = sum(row_need) + gap * (rows - 1)
    top = cards_bottom - block
    boxes, y = [], top
    for r in range(rows):
        idxs = [r * 2, r * 2 + 1] if two_col else [r]
        for i in idxs:
            if i >= n:
                continue
            full = (not two_col) or (n % 2 == 1 and i == n - 1)
            x0 = m if full else m + (i % 2) * (col_w + gap)
            x1 = (m + inner) if full else (x0 + col_w)
            boxes.append((int(x0), int(y), int(x1), int(y + row_need[r])))
        y += row_need[r] + gap
    return boxes, int(top)


def typeset(base_png, plan, ratio="9:16"):
    """把 plan 里的中文排到 AI 底图上。返回 (png 字节, 版面自检 dict)。

    base_png 是出图模型给的**无字底图**字节；plan 走 ai_poster 的文案计划形状
    （title / subtitle / cards[{t,d}] / footer / style_key）。

    顺序是三遍：算版面 → 贴所有半透明底板 → 写所有文字。中间任何一步反过来都会
    出现"板盖字"或"字浮在板外面"。
    """
    img = Image.open(io.BytesIO(base_png)).convert("RGB")
    W, H = img.size
    skin = skin_for((plan or {}).get("style_key"))
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

    # ── 第二遍：贴底板 ──
    for box in boxes:
        ov, pos = _panel_overlay(box, skin)
        img.paste(ov, pos, ov)

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

    for idx, ((x0, y0, x1, y1), c) in enumerate(zip(boxes, cards)):
        pad = int(min(x1 - x0, y1 - y0) * 0.115)
        cx, cy = x0 + pad, y0 + pad
        cw = (x1 - x0) - 2 * pad
        ct = (c.get("t") or "").strip()
        cd = (c.get("d") or "").strip()
        if ct:
            tf, tfl, ts = _fit_lines(ct, skin["title_face"], int(H * 0.0255), cw, 1,
                                     min_size=int(H * 0.017))
            _draw_text(draw, (cx, cy), tfl[0], tf, _rgb(skin["accent"]))
            cy += int(ts * 1.45)
        if cd:
            avail = max(int(H * 0.026), y1 - pad - cy)
            df, dfl, ds = _fit_lines(cd, skin["body_face"], int(H * 0.0205), cw,
                                     5, min_size=int(H * 0.0150), max_h=avail)
            if len(_wrap(cd, _font(skin["body_face"], int(H * 0.0150)), cw)) * H * 0.0150 * 1.3 > avail:
                problems.append("第 %d 张卡片说明放不下，已按最小字号截断" % (idx + 1))
            for ln in dfl:
                if cy + ds * 1.30 > y1 - int(pad * 0.35):
                    problems.append("第 %d 张卡片说明放不下，已截到最后可读的一行" % (idx + 1))
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
    return out.getvalue(), {
        "ok": not problems, "problems": problems,
        "canvas": "%dx%d" % (W, H), "cards": len(cards),
        "engine": "pil-typeset", "skin": (plan or {}).get("style_key") or DEFAULT_SKIN,
    }

def text_zone_ratio(plan, canvas_wh):
    """卡片区从画面高度的哪一段开始（0~1）。出图 prompt 用同一个数留白，
    排版和画面才不会一个让了地方另一个不知道。"""
    W, H = canvas_wh
    skin = skin_for((plan or {}).get("style_key"))
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
