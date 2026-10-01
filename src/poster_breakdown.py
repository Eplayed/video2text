# -*- coding: utf-8 -*-
"""拆解一张外部参考图文 → 生成我们自己的模板草稿。

分工很关键，别搞反：
  看图这步必须用视觉模型。deepseek 那档是纯文本的，看不见图，让它"拆解参考图"
  只会凭名字编配色。默认用 qwen3-vl-flash（回读校验和打分也是它），可在配置里换。
  文本模型只负责把草稿整理得好看一点，这里没用上。

模型给的东西一律当"草稿"看不当"结论"：配色 hex、插画占比、版式判断都会错。
所以这一层做完的不是一个能直接存的模板，而是一份**能改的表单**——人改完点免费预览
立刻看到结果，这才是这条链路能用的原因。另外代码里兜了一层亮度检查：万一模型给的是
浅背景又配了浅色字，排出来就是白底白字，那种图不能让人存进去。
"""
import base64
import json
import re

DEFAULT_VISION_MODEL = "qwen3-vl-flash"

# 版式只让模型从这四种里挑。想加第五种得先在 poster_typeset 里实现，
# 否则拆出来一个我们排不出来的版式，等于给用户一个坏选项。
LAYOUTS = {
    "stack": "单列通栏：每条要点占一整行，从上排到下，插画占大半张",
    "grid2": "两列网格：要点做成左右两列的小面板，条数多用这种",
    "list": "清单：单列，每条前面一个大号序号（01/02/03），插画只占上部一小条",
    "hero": "结论式：第一条通栏放大当结论，其余几条做成小卡片",
}
TITLE_FACES = {"serif_black": "标题是衬线/宋体/书法那一类，有笔画粗细变化",
               "sans_bold": "标题是黑体/无衬线，方方正正"}
PANEL_STYLES = {"solid": "卡片有不透明底板", "translucent": "卡片有半透明底板，能透出背景",
                "none": "卡片没有底板，只靠分割线或留白隔开"}

_PROMPT = """你在逆向拆解一张中文信息图海报，目的是把它的**视觉配方**复刻成可复用模板。
只观察、不要评价内容好坏。严格输出 JSON，不要任何解释文字。

要填的字段：
{
 "name": "给这套风格起个中文短名，≤10字，要能一眼认出是什么感觉（例：暗金战报、米纸清单）",
 "mood": "一句画图用的画风描述，≤90字：写实还是插画/厚涂还是扁平、主光怎么打、背景是什么材质、整体氛围。只描述画面，不要提文字排版",
 "layout": "%(layouts)s 里选一个最接近这张图的",
 "art_share": "插画/背景图占整张画面高度的比例，0.2 到 0.8 之间的小数，只算没有文字的那块图占多少",
 "title_face": "%(faces)s 里选一个",
 "panel_style": "%(panels)s 里选一个",
 "card_count": "图里有几条要点，整数",
 "palette": {
   "bg": "画面背景主色 #RRGGBB",
   "panel": "卡片底板色 #RRGGBB（没有底板就填比背景略深或略浅一点的颜色）",
   "accent": "强调色 #RRGGBB（标题下划线、序号、小图标那种最跳的颜色）",
   "ink": "正文文字色 #RRGGBB"
 },
 "confidence": "low 或 mid 或 high，你对这次提取的整体把握",
 "notes": "≤40字，说清哪里没把握、或者这张图有什么上面字段没描述到的特征"
}

注意：art_share 要按"图占多高"算，不要按"图有多好看"算；配色取你真正看到的颜色，
不要取你觉得应该配什么颜色。""" % {
    "layouts": " / ".join('"%s"＝%s' % (k, v) for k, v in LAYOUTS.items()),
    "faces": " / ".join('"%s"＝%s' % (k, v) for k, v in TITLE_FACES.items()),
    "panels": " / ".join('"%s"＝%s' % (k, v) for k, v in PANEL_STYLES.items()),
}


def _hex(v, fallback):
    s = str(v or "").strip()
    if not s.startswith("#"):
        s = "#" + s
    return s if re.fullmatch(r"#[0-9a-fA-F]{6}", s) else fallback


def _num(v, lo, hi, fallback):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, f))


def _lum(hex_str):
    """亮度 0~1。用来判断背景是深是浅——模型给浅背景时如果还照抄它的浅色字，
    排出来就是白底白字，那种模板不能让人存进去。"""
    s = (hex_str or "").lstrip("#")
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except (ValueError, IndexError):
        return 0.15
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _mix(a, b, k):
    pa, pb = (a or "").lstrip("#"), (b or "").lstrip("#")
    try:
        ca = [int(pa[i:i + 2], 16) for i in (0, 2, 4)]
        cb = [int(pb[i:i + 2], 16) for i in (0, 2, 4)]
    except (ValueError, IndexError):
        return a
    return "#%02x%02x%02x" % tuple(int(ca[i] * (1 - k) + cb[i] * k) for i in range(3))


def normalize(raw):
    """把模型给的原始 JSON 洗成一份字段齐全、能直接排版的草稿。

    缺字段回落到内置皮肤，颜色非法就丢回默认，深浅背景会强制翻转文字色——
    模型输出不能直接信，但也不能因为它漏字段就整条链路报错。
    """
    from . import poster_typeset as ts
    raw = raw if isinstance(raw, dict) else {}
    pal = raw.get("palette") if isinstance(raw.get("palette"), dict) else {}
    layout = raw.get("layout") if raw.get("layout") in LAYOUTS else "grid2"
    share = _num(raw.get("art_share"), 0.20, 0.80, 0.55)
    parent = {"stack": "game_epic", "grid2": "tool_review",
              "list": "checklist", "hero": "minimal"}.get(layout, "tool_review")
    base = dict(ts.TYPE_SKINS[parent])

    bg = _hex(pal.get("bg"), base["bg_top"])
    panel = _hex(pal.get("panel"), base["panel"])
    accent = _hex(pal.get("accent"), base["accent"])
    ink = _hex(pal.get("ink"), base["ink"])
    dark_bg = _lum(bg) < 0.45
    # 深浅翻转：背景深就必须用亮字，背景浅就必须用暗字，否则整张图读不出来
    if dark_bg:
        ink = "#f0eadd" if _lum(ink) < 0.55 else ink
        title = "#f5eddc"
    else:
        ink = "#2b2b2b" if _lum(ink) > 0.60 else ink
        title = ink
    panel_style = raw.get("panel_style") if raw.get("panel_style") in PANEL_STYLES else "translucent"
    alpha = {"solid": 235, "translucent": 168, "none": 0}[panel_style]
    face = raw.get("title_face") if raw.get("title_face") in TITLE_FACES else "sans_bold"

    skin = dict(base)
    skin.update({
        "title_face": face, "ink": ink, "title": title,
        "title_stroke": "#000000" if dark_bg else "#ffffff",
        "muted": _mix(ink, bg, 0.45), "accent": accent,
        "panel": panel, "panel_alpha": alpha,
        "stroke": accent, "stroke_alpha": 0 if panel_style == "none" else 130,
        "bg_top": bg, "bg_bot": _mix(bg, "#000000" if dark_bg else "#ffffff", 0.35),
        "layout": layout,
        "art_floor": round(max(0.18, share - 0.08), 2),
        "art_cap": round(min(0.82, share + 0.06), 2),
    })
    return {
        "name": str(raw.get("name") or "").strip()[:12] or "拆出来的模板",
        "mood": str(raw.get("mood") or "").strip()[:200],
        "layout": layout, "art_share": share, "title_face": face,
        "panel_style": panel_style,
        "card_count": int(_num(raw.get("card_count"), 1, 9, 4)),
        "palette": {"bg": bg, "panel": panel, "accent": accent, "ink": ink},
        "confidence": raw.get("confidence") if raw.get("confidence") in ("low", "mid", "high") else "mid",
        "notes": str(raw.get("notes") or "").strip()[:80],
        "parent": parent,
        "skin": skin,
    }


def breakdown(image_bytes, ai_config, model=""):
    """看一张参考图，出模板草稿。返回 (draft, 原始返回)。"""
    from .ai_poster import _require_ai, _api_root
    from .graphics.channels.toutiao import _parse_json
    import openai

    api_key, api_base, _ = _require_ai(ai_config)
    client = openai.OpenAI(api_key=api_key, base_url=api_base or "https://api.openai.com/v1",
                           timeout=180, max_retries=1)
    b64 = base64.b64encode(image_bytes).decode()
    resp = client.chat.completions.create(
        model=model or DEFAULT_VISION_MODEL,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
            {"type": "text", "text": _PROMPT}]}],
        max_tokens=900)
    raw = _parse_json(resp.choices[0].message.content or "")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("视觉模型没按 JSON 回话，换个清晰点的图再试")
    return raw   # 原样回给前端填表单；派生与兜底留给 normalize()，只此一份
