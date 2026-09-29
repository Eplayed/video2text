# -*- coding: utf-8 -*-
"""头条图文渠道适配器（Phase 1）。

承载头条业务专属内容：目录/画布常量、卡片化 prompt、LLM 调用、本地兜底、
generate_graphics 主流程、manifest 包管理薄封装、ToutiaoChannel 适配器。
通用渲染能力委托给 src.graphics 内核（skins/css_engine/templates/renderer/package）。

业务专属（不进通用内核）：output/toutiao/ 目录、/media/toutiao/ URL 前缀、
prompt 文案、【固定标签】兜底解析。Python 3.9 兼容：不用 match / X|Y 语法。
"""
import json
import re
from datetime import datetime
from pathlib import Path

from .. import skins, css_engine, templates, renderer, package, ir, variants
from .base import ChannelAdapter

# ── 头条渠道目录 / 画布常量（原 toutiao_graphics.py L22-26）──
# ROOT 由 parent.parent 改为 parents[3]：本文件位于 src/graphics/channels/toutiao.py，
# parents[0]=channels [1]=graphics [2]=src [3]=仓库根，取值与原实现一致。
ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = ROOT / "output" / "toutiao"
CANVAS_W, CANVAS_H = 1080, 1920

_ASSETS_DIR = OUTPUT_DIR / "_assets"

# ── LLM 卡片化 ──
_CARDS_PROMPT = """你是微头条竖版信息图排版师。把下面的整合稿排成 4 张竖版卡片（1080x1920 竖屏 9:16），供直接渲染出图。直接输出 JSON，不要思考过程、不要解释。

__THEME_LINE__
__TITLE_LINE__
__TEMPLATE_LINE__

要求：
- 第 1 张 kind="cover"：title 大标题（≤18字，含数字）；subtitle 一句话（≤20字，可空）；timeline（≤22字，如「11月4日开服 → 12月7日开团本」）；timeline_note（≤28字）；timeline_label（如「关键时间线」）；hooks 3-4 条，每条 t（≤14字）+ d（≤20字）；bg 可选，"neutral"=通用史诗背景
- 第 2-4 张 kind="list"：title（≤14字）；section（条目分组名，≤6字）；icon 单字（如 盾/火/剑）；items 3-5 条，每条 name（≤8字）+ desc（≤30字）+ tag（≤4字，可空，如"最稳""T0"）+ faction（可选，"alliance"或"horde"，仅当该条内容阵营专属时填）；最后一张可带 note 对象，含 title 和 text 两个键
- 阵营主题背景：整卡内容阵营专属时 card 加 bg 字段（"alliance"=联盟蓝金大教堂 / "horde"=部落暗红峡谷）+ faction 字段 + faction_text（如"联盟专属"）；混合阵营内容不要设 bg，用条目级 faction 区分
- 每张卡内容要留呼吸感：条目宁少勿多，desc 一句话讲完，超长会被截断
- 所有文字精简口语化；只能用整合稿里的事实，严禁编造
- 成品文案纪律（重要）：卡片文字是直接发布给读者看的成品，不是给编辑看的审校稿。
  严禁出现素材缺口说明与审核提示，例如「口播未给」「视频里没讲」「素材未提」「整合稿没写」
  「ASR/转写」「待核实」「请人工核对」「本地模板生成」「详见正文」「无」这类字样。
  整合稿信息不足时，直接少写一条或把该字段留空字符串，绝不写占位说明来交代缺口。
  需要提醒读者时，只能写读者视角的话（如「测试服数值随时可能调整，以正式服为准」）。
- copy_text：微头条文案，结构 = 钩子开头（1-2句）+ 📌 要点 4 条（每条一行，冒号+短解释）+ 收尾引导（1句）+ 空行 + 「信息来源：多位UP主公开视频内容整理（口径截至{{DATE}}，可能有调整）」+「本文图为AI辅助生成，发布时勾选AI辅助声明。」，全文 200-320 字

输出 JSON（cards 数组 4 个对象，第一个 kind 为 cover，其余为 list；copy_text 为字符串）：
{schema_placeholder}

整合稿：
{content}"""


def _build_cards_prompt(summary, theme="", template="classic"):
    """构造卡片化 prompt：schema 段含双花括号 JSON 示例，不能走 str.format，逐段拼接。"""
    schema = ('{{\n  "cards": [\n    {{"kind": "cover", "title": "...", "subtitle": "...", '
              '"timeline_label": "...", "timeline": "...", "timeline_note": "...", '
              '"hooks": [{{"t": "...", "d": "..."}}]}},\n'
              '    {{"kind": "list", "title": "...", "subtitle": "...", "section": "...", "icon": "...", '
              '"items": [{{"name": "...", "desc": "...", "tag": "..."}}], "note": {{"title": "...", "text": "..."}}}}\n  ],\n'
              '  "copy_text": "..."\n}}')
    date_str = datetime.now().strftime("%m月%d日")
    theme_line = ("本篇题材：%s。封面标题、各卡标题和 copy_text 都要点明该题材（游戏名/模式名），让读者一眼知道这是哪个游戏的内容。" % theme) if theme else "本篇题材：未指定，按整合稿内容自行判断题材并在标题点明。"
    title_line = ""
    if (summary.get("title") or "").strip():
        title_line = ("推荐标题（来自整理稿/用户指定）：%s。第 1 张 cover 卡的 title 优先采用它，"
                      "可微调语气与字数但必须保留核心词，不要另起炉灶。" % summary["title"].strip())
    tpl = templates._TEMPLATES.get(template) or templates._TEMPLATES["classic"]
    prompt = (_CARDS_PROMPT
              .replace("{schema_placeholder}", schema)
              .replace("{{DATE}}", date_str)
              .replace("__THEME_LINE__", theme_line)
              .replace("__TITLE_LINE__", title_line)
              .replace("__TEMPLATE_LINE__", tpl["hint"]))
    # content 放最后替换，避免整合稿里的花括号被后续 replace 误伤
    prompt = prompt.replace("{content}", (summary.get("content") or "")[:12000])
    return prompt


def _llm_cards(summary, ai_config, theme="", template="classic"):
    """调 LLM 把整合稿转卡片 JSON；失败返回 None。"""
    if not ai_config or not ai_config.get("api_key") or ai_config.get("method") == "skip":
        return None
    try:
        import openai

        client = openai.OpenAI(
            api_key=ai_config["api_key"],
            base_url=ai_config.get("api_base") or "https://api.openai.com/v1",
            timeout=300, max_retries=1,
        )
        prompt = _build_cards_prompt(summary, theme, template)
        resp = client.chat.completions.create(
            model=ai_config.get("model") or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content
        data = _parse_json(text)
        cards = data.get("cards") or []
        if len(cards) >= 2 and data.get("copy_text"):
            return data
        return None
    except Exception:
        return None


def _parse_json(text):
    text = str(text or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        return {}
    depth, in_str, escape = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        return {}
    return {}


def _split_tags(content):
    """按【固定标签】切段：{标签名: 文本}。"""
    tags = {}
    parts = re.split(r"【([^】]{2,8})】", content or "")
    for i in range(1, len(parts) - 1, 2):
        tags[parts[i]] = parts[i + 1].strip()
    return tags


def _fallback_cards(summary):
    """本地模板兜底：解析固定标签拼 4 张卡。AI 未配置/调用失败时保底出图。"""
    content = summary.get("content") or ""
    tags = _split_tags(content)
    title = (summary.get("title") or "整合速览")[:18]
    compare = tags.get("变化对比", "")[:400]
    impact = tags.get("对你的影响", "")[:400]
    action = tags.get("行动建议", "")[:400]
    timeline = tags.get("时间线", "").strip().splitlines()
    tl_text = timeline[0][:22] if timeline else ""

    def _rows(text, limit=5):
        rows = []
        for line in [l.strip() for l in text.splitlines() if l.strip()][:limit]:
            line = re.sub(r"^[-*|]+", "", line).strip(" |")
            seg = line.split("|") if "|" in line else [line[:8], line]
            name = seg[0][:8] if len(seg) > 1 else "要点"
            desc = (seg[1] if len(seg) > 1 else line)[:34]
            rows.append({"name": name, "desc": desc, "tag": ""})
        # 兜底也不能写「详见整合稿正文」这类编辑向字样：图是直接给读者看的
        return rows or [{"name": "说明", "desc": "这部分内容较少，可看其他几张配图", "tag": ""}]

    cards = [
        {"kind": "cover", "bg": "neutral", "badge": "整合", "title": title, "subtitle": "多源信息 · 决策化整合",
         "timeline_label": "关键时间线", "timeline": tl_text, "timeline_note": "",
         "hooks": [{"t": "变化对比", "d": "见第 2 张"}, {"t": "对你的影响", "d": "见第 3 张"},
                   {"t": "行动建议", "d": "见第 4 张"}]},
        {"kind": "list", "title": "核心变化对比", "section": "变化", "icon": "变",
         "items": _rows(compare)},
        {"kind": "list", "title": "对你的影响", "section": "影响", "icon": "响",
         "items": _rows(impact)},
        {"kind": "list", "title": "行动建议", "section": "行动", "icon": "行",
         "items": _rows(action),
         "note": {"title": "提醒", "text": "数值与机制可能随版本调整，以官方最新公告为准。"}},
    ]
    copy_text = (
        "%s\n\n多源信息决策化整合，4 张卡片看懂变化、影响与行动建议：\n\n📌 变化对比：见配图 2\n"
        "📌 对你的影响：见配图 3\n📌 行动建议：见配图 4，建议收藏\n\n信息来源：多位UP主公开视频内容整理。\n"
        "本文图为AI辅助生成，发布时勾选AI辅助声明。" % title
    )
    return {"cards": cards, "copy_text": copy_text}


def generate_graphics(summary, ai_config, progress_cb=None, theme="", skin="wow", title="", template="classic", guide_images=None, palette=None, font=None, layout=None):
    """主入口：整合稿 → manifest dict（图片落盘 output/toutiao/<summary_id>/）。

    theme:     str 题材/游戏名，注入 LLM 卡片化 + 选插画素材文件夹
    skin:      str 皮——调色板+背景垫图（wow/d4/poe/poe2/自定义 _assets/<名称>_bg.jpg）
    title:     str 图文标题；非空时覆盖整理稿标题，注入 LLM 封面标题指令
    template:  str 模板——classic 经典卡片 / magazine 杂志大片 / minimal 极简清单 / bold 大字报，
               决定整体版式/字体/装饰（背景、布局、文字大小样式）
    guide_images: list 攻略图解模板的上传攻略图路径（弹窗上传，优先于素材文件夹）
    palette:   str 配色预设键（variants.PALETTE_KEYS 闭集）；None/未知回落默认 gold_night（现状）
    font:      str 字体预设键（variants.FONT_KEYS 闭集）；None/未知回落默认 serif（现状）
    layout:    str 版式预设键（variants.LAYOUT_KEYS 闭集）；None/未知回落默认 default（现状）
    """
    tpl_key = template if template in templates._TEMPLATES else "classic"
    tpl = templates._TEMPLATES[tpl_key]
    summary_id = int(summary["id"])
    if (title or "").strip():
        summary = dict(summary)
        summary["title"] = title.strip()[:60]
    out_dir = OUTPUT_DIR / str(summary_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _pg(msg):
        if progress_cb:
            progress_cb(msg)

    skin_key, skin_uri = skins.resolve_skin(skin, _ASSETS_DIR)
    palette_key, font_key = variants.resolve_keys(palette, font)
    layout_key = variants.resolve_layout(layout)
    palette = variants.resolve_palette(skin_key, palette_key, tpl_key)
    extra_tokens = dict(css_engine._tpl_tokens(tpl_key, skin_key))
    extra_tokens.update(variants.palette_tokens(tpl_key, palette_key))
    css = css_engine._build_css(tpl["css"], palette, skin_uri, _ASSETS_DIR, extra_tokens, CANVAS_W, CANVAS_H)
    layout_extra = variants.layout_css(tpl_key, layout_key)
    if layout_extra:
        css += "\n/* layout:%s */\n%s" % (layout_key, layout_extra)
    css = variants.apply_font(css, font_key)
    if tpl_key == "guide":
        if guide_images:
            theme_imgs = [Path(p) for p in guide_images]
        else:
            theme_imgs = skins._guide_image_paths(theme, skin_key, OUTPUT_DIR)
        cover_img = theme_imgs[0] if theme_imgs else None
    else:
        theme_imgs = skins._theme_image_paths(theme, skin_key, OUTPUT_DIR)
        cover_img = skins._pick_cover_image(theme_imgs)

    _pg("LLM 卡片化整合稿（模板：%s）..." % tpl["label"])
    data = _llm_cards(summary, ai_config, theme, tpl_key)
    model = "local-template"
    if data:
        model = ai_config.get("model") or "llm"
    else:
        _pg("LLM 不可用，走本地模板兜底...")
        data = _fallback_cards(summary)

    cards = data.get("cards") or []
    # 渲染前清洗：剔除「口播未给」「请人工核对」等编辑向元语言（成品图直接面向读者）
    cards = ir.sanitize_cards(cards)
    if skin_uri:
        for card in cards:
            card["bg"] = "skin"
    total = len(cards)
    images = []
    _pg("渲染 %d 张信息图（模板：%s）..." % (total, tpl["label"]))
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": CANVAS_W, "height": CANVAS_H})
        brand = (theme or "").strip()[:12]
        cover_ratio = skins._img_ratio(cover_img) if cover_img is not None else None
        for idx, card in enumerate(cards, 1):
            kind = card.get("kind") or "list"
            ctx = {"brand": brand, "idx": idx, "total": total, "skin_key": skin_key,
                   "canvas_h": CANVAS_H, "cover_ratio": cover_ratio}
            if kind == "cover" and idx == 1:
                hero = skins._hero_uri(card, cover_img, skin_uri, _ASSETS_DIR)
                html = tpl["cover"](card, css, hero, ctx)
            else:
                if tpl_key == "guide":
                    band = theme_imgs[(idx - 1) % len(theme_imgs)] if theme_imgs else None
                else:
                    band = skins._pick_band_image(theme_imgs, idx)
                hero = skins._hero_uri(card, band, skin_uri, _ASSETS_DIR)
                html = tpl["list"](card, css, hero, ctx)
            html_path = out_dir / ("img%d.html" % idx)
            png_path = out_dir / ("img%d.png" % idx)
            html_path.write_text(html, encoding="utf-8")
            renderer.render_card_html(page, html_path, png_path, CANVAS_W, CANVAS_H)
            images.append({"file": png_path.name, "label": "图%d" % idx, "url": "/media/toutiao/%d/%s" % (summary_id, png_path.name)})
        browser.close()

    manifest = {
        "id": summary_id,
        "summary_id": summary_id,
        "title": summary.get("title") or "",
        "copy_text": data.get("copy_text") or "",
        "images": images,
        "cards": cards,
        "model": model,
        "theme": (theme or "").strip(),
        "skin": skin_key,
        "template": tpl_key,
        "palette": palette_key,
        "font": font_key,
        "layout": layout_key,
        "canvas": "%dx%d" % (CANVAS_W, CANVAS_H),
        "summary_type": summary.get("summary_type", ""),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


# ── manifest 包管理薄封装（绑定头条 OUTPUT_DIR，对外签名与原 toutiao_graphics 一致）──
def list_packages():
    """扫描 output/toutiao/*/manifest.json，倒序返回列表。"""
    return package.list_packages(OUTPUT_DIR)


def get_package(summary_id):
    """读单个包 manifest；不存在返回 None。"""
    return package.get_package(summary_id, OUTPUT_DIR)


def delete_package(summary_id):
    """删包目录（manifest + 图片 + 残留 html）。"""
    return package.delete_package(summary_id, OUTPUT_DIR)


class ToutiaoChannel(ChannelAdapter):
    """头条图文渠道适配器：把内核能力按头条目录/画布/模板组装。

    Phase 1 仅落已有能力；lint/checklist/export 为最小实现，Phase 2 补全。
    """
    name = "toutiao"
    canvas = (CANVAS_W, CANVAS_H)
    card_count = 4

    def templates(self):
        return list(templates._TEMPLATES.keys())

    def prompt(self, summary, theme="", template="classic"):
        return _build_cards_prompt(summary, theme=theme, template=template)

    def limits(self):
        return {"title": 18, "subtitle": 20, "copy_text": (200, 320)}

    def checklist(self):
        return ["发布时勾选 AI 辅助声明", "人工核对数值与时间线"]

    def export(self, summary, ai_config, **kwargs):
        return generate_graphics(summary, ai_config, **kwargs)
