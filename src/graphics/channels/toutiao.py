# -*- coding: utf-8 -*-
"""头条图文渠道适配器（Phase 1）。

承载头条业务专属内容：目录/画布常量、卡片化 prompt、LLM 调用、本地兜底、
generate_graphics 主流程、manifest 包管理薄封装、ToutiaoChannel 适配器。
通用渲染能力委托给 src.graphics 内核（skins/css_engine/templates/renderer/package）。

业务专属（不进通用内核）：output/toutiao/ 目录、/media/toutiao/ URL 前缀、
prompt 文案、【固定标签】兜底解析。Python 3.9 兼容：不用 match / X|Y 语法。
"""
import json
import random
import re
from datetime import datetime
from pathlib import Path

from .. import skins, css_engine, templates, renderer, package, ir, variants, gate
from .base import ChannelAdapter

# ── 头条渠道目录 / 画布常量（原 toutiao_graphics.py L22-26）──
# ROOT 由 parent.parent 改为 parents[3]：本文件位于 src/graphics/channels/toutiao.py，
# parents[0]=channels [1]=graphics [2]=src [3]=仓库根，取值与原实现一致。
ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = ROOT / "output" / "toutiao"
CANVAS_W, CANVAS_H = 1080, 1920

_ASSETS_DIR = OUTPUT_DIR / "_assets"

# 头条弹窗可选的模板（顺序即下拉顺序）。名单以前只写在前端 HTML 里，内核加了模板这里不会自动出现；
# 现在收进后端一处，前端走 /api/toutiao/templates 拿。
# 不含 wechat / lilac_list / cream_gold：那三套是公众号竖版（3:4）的身份模板，
# 头条是 1080x1920 九比十六，版式与卡数都按公众号定的，硬塞进来是给用户一个出坏图的选项。
TEMPLATE_WHITELIST = ["classic", "magazine", "minimal", "bold", "guide", "tier", "quest"]


def template_choices():
    """头条模板下拉的权威源（与公众号共用 templates.choice_list 的构造逻辑）。"""
    return templates.choice_list(TEMPLATE_WHITELIST)

# 素材选图随机种子（2026-09-29 素材随机化引入）：
# 生产为 None —— 每次生成新建随机源，同一素材库多次出图会换封面/换横带；
# 测试或需要复现某次选图时，把它设成整数即可让选图完全确定（e2e 用它保证字节级断言可复现）。
_RNG_SEED = None


def _make_rng():
    """按 _RNG_SEED 建随机源；None 表示真随机。"""
    return random.Random(_RNG_SEED)

# ── LLM 卡片化 ──
_CARDS_PROMPT = """你是微头条竖版信息图排版师。把下面的整合稿排成 4 张竖版卡片（1080x1920 竖屏 9:16），供直接渲染出图。直接输出 JSON，不要思考过程、不要解释。

__THEME_LINE__
__TITLE_LINE__
__TEMPLATE_LINE__

要求：
- 第 1 张 kind="cover"：title 大标题（≤18字，含数字）；subtitle 一句话（≤20字，可空）；timeline（≤22字，如「11月4日开服 → 12月7日开团本」）；timeline_note（≤28字）；timeline_label（如「关键时间线」）；hooks 3-4 条，每条 t（≤14字）+ d（≤20字）；bg 可选，"neutral"=通用史诗背景
- 第 2-4 张 kind="list"：title（≤14字）；section（条目分组名，≤6字）；icon 单字（如 盾/火/剑）；items 3-5 条，每条 name（≤8字）+ desc（≤30字）+ tag（≤4字，可空，如"最稳""T0"）+ faction（可选，"alliance"或"horde"，仅当该条内容阵营专属时填）；不要输出 note 字段（成品图已不再渲染「提醒」小结块，写了也会被丢弃）
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
              '"items": [{{"name": "...", "desc": "...", "tag": "..."}}]}}\n  ],\n'
              '  "copy_text": "..."\n}}')
    date_str = datetime.now().strftime("%m月%d日")
    theme_line = ("本篇题材：%s。封面标题、各卡标题和 copy_text 都要点明该题材（游戏名/模式名），让读者一眼知道这是哪个游戏的内容。" % theme) if theme else "本篇题材：未指定，按整合稿内容自行判断题材并在标题点明。"
    title_line = ""
    if (summary.get("title") or "").strip():
        # 措辞收紧（2026-09-29）：旧版「优先采用、可微调」实测仍被模型加词改写；
        # 生成侧已有代码级强制覆盖兜底，这里同步要求逐字使用，保证 subtitle/copy_text 语境一致。
        title_line = ("封面标题（来自整理稿/用户指定）：%s。第 1 张 cover 卡的 title 必须逐字使用该标题，"
                      "不得增删改写任何字词；subtitle 和 copy_text 围绕它展开。" % summary["title"].strip())
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
    """调 LLM 把整合稿转卡片 JSON。

    未配置 AI → 返回 None，由调用方走本地兜底（项目纪律：不阻断，e2e 也依赖这条路径）。
    已配置但失败 → 抛 GraphicsGateError，不许拿兜底图冒充成品
    （2026-09-30 公众号线就因吞掉 402 欠费而产出 5 张垃圾卡片）。
    """
    if not gate.ai_usable(ai_config):
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
    except Exception as e:
        raise gate.GraphicsGateError("卡片化模型调用失败（模型 %s）：%s"
                                     % (ai_config.get("model") or "?", gate.brief_error(e)))
    cards = (data or {}).get("cards") or []
    if len(cards) < 2 or not (data or {}).get("copy_text"):
        raise gate.GraphicsGateError("模型返回的卡片不合格（cards=%d、copy_text 缺失），已拒绝出图"
                                     % len(cards))
    return data


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
    layout:    str 版式预设键（variants.LAYOUT_KEYS 闭集）；None/未知回落默认 default（现状）；
               传 "random"（variants.RANDOM_LAYOUT_KEY）＝本次生成随机抽一个真实预设，整包统一，
               抽中键写进 manifest.layout（可追溯，不会落 "random"）
    """
    tpl_key = template if template in templates._TEMPLATES else "classic"
    tpl = templates._TEMPLATES[tpl_key]
    gate.assert_draft_generatable(summary, "头条图文")
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
    # 版式：random＝本次生成随机抽一个真实预设（整包统一，抽中键写进 manifest），其余走闭集回落
    if layout == variants.RANDOM_LAYOUT_KEY:
        layout_key = variants.pick_random_layout()
    else:
        layout_key = variants.resolve_layout(layout)
    palette = variants.resolve_palette(skin_key, palette_key, tpl_key)
    extra_tokens = dict(css_engine._tpl_tokens(tpl_key, skin_key))
    extra_tokens.update(variants.palette_tokens(tpl_key, palette_key))
    css = css_engine._build_css(tpl["css"], palette, skin_uri, _ASSETS_DIR, extra_tokens, CANVAS_W, CANVAS_H)
    layout_extra = variants.layout_css(tpl_key, layout_key)
    if layout_extra:
        css += "\n/* layout:%s */\n%s" % (layout_key, layout_extra)
    # tpl_key 必传：minimal 全篇只有黑体栈，没有 Songti 可替换，靠预设的按模板追加片段才生效
    css = variants.apply_font(css, font_key, tpl_key)
    # 素材选图随机化（2026-09-29 用户要求「素材图片也可以是随机的，并不是第一张图片就是
    # 第一张图片素材」）：此前封面永远取比例最大的竖图、横带按固定顺序轮换，同一素材库
    # 生成多少次都是同一批图。现在每次生成建一个新随机源（_make_rng，_RNG_SEED 可固定复现），
    # 封面在竖构图池里随机抽、横带在「本包还没用过的素材」里随机抽（不撞封面、包内不重复）。
    # guide 模板不走随机——攻略图目录是用户上传的有序素材，顺序有意义，保持确定取法。
    rng = _make_rng()
    if tpl_key == "guide":
        if guide_images:
            theme_imgs = [Path(p) for p in guide_images]
        else:
            theme_imgs = skins._guide_image_paths(theme, skin_key, OUTPUT_DIR)
        cover_img = theme_imgs[0] if theme_imgs else None
    else:
        theme_imgs = skins._theme_image_paths(theme, skin_key, OUTPUT_DIR)
        cover_img = skins._pick_cover_image(theme_imgs, rng)

    _pg("LLM 卡片化整合稿（模板：%s）..." % tpl["label"])
    data = _llm_cards(summary, ai_config, theme, tpl_key)
    model = "local-template"
    if data:
        model = ai_config.get("model") or "llm"
    else:
        _pg("未配置 AI，走本地模板兜底（成品需人工核对）...")
        data = _fallback_cards(summary)

    cards = data.get("cards") or []
    # 渲染前清洗：剔除「口播未给」「请人工核对」等编辑向元语言（成品图直接面向读者）
    cards = ir.sanitize_cards(cards)
    # 用户显式指定的标题必须逐字上封面（2026-09-29 修复：此前只在 prompt 里说「优先采用」，
    # LLM 仍会自行加词改写——实测把「魔兽无限：…」改成「魔兽无限B测：…」印上封面。
    # 承诺「图文标题决定封面主标题」是产品契约，改为代码级强制，不依赖模型自觉）。
    user_title = (title or "").strip()
    if user_title and cards:
        cards[0]["title"] = summary.get("title") or user_title[:60]
    # 头条成品图不再渲染「提醒」小结块（2026-09-29 用户要求「图文中移除提醒」）：
    # prompt / schema 已同步禁止生成 note，但 LLM 未必听话、本地兜底卡 _fallback_cards 也仍带
    # 「提醒」——与标题强制同一原则：渲染前代码级剔除，不留侥幸。
    # 模板渲染函数里的 note_html 分支保留（公众号等其他渠道不受影响）。
    for card in cards:
        if isinstance(card, dict):
            card.pop("note", None)
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
        # 本包已用素材（封面 + 各页横带），随机选图时作为排除集，避免同图重复出现
        used_assets = [cover_img] if cover_img is not None else []
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
                    band = skins._pick_band_image(theme_imgs, idx, rng, exclude=used_assets)
                    if band is not None:
                        used_assets.append(band)
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
        # 素材选图已随机化（2026-09-29）：记录本包实际用到的素材文件名（首个为封面主视觉），
        # 便于事后追溯「这张成品图用的是哪张素材」——随机后不能再靠目录顺序倒推。
        "assets": [p.name for p in used_assets],
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
