# -*- coding: utf-8 -*-
"""公众号图片消息（newspic）渠道适配器（Phase 2）。

迁移方案 §5.4：3:4 画布 1080x1440、模板白名单（classic/minimal/magazine）、
公众号版卡片 prompt（纯文本 + 合规尾注）、作者真实底稿必填（§6 强制机制）、
本地 lint、微信版发布检查清单、导出包（含文案文件）。

通用渲染能力全部委托 src.graphics 内核；卡片 JSON 沿用渠道无关骨架
（kind/title/subtitle/section/icon/items/hooks/note），公众号包级元数据
（digest/keyword_reply/source_note）放 manifest 顶层，不进卡片。
Python 3.9 兼容：不用 match / X|Y 语法。
"""
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from .. import skins, css_engine, templates, renderer, package, ir, variants, gate
from .base import ChannelAdapter
# 借用头条线的 JSON 容错解析与【标签】切段（两者均渠道无关，Phase 3 视情上移内核）
from .toutiao import _parse_json, _split_tags

# ── 公众号渠道目录 / 画布常量 ──
ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = ROOT / "output" / "wechat"
CANVAS_W, CANVAS_H = 1080, 1440  # 3:4，微头条试跑已验证同机制跑得通

# 模板白名单（wechat 信号格为默认：零背景图、与游戏资产解耦；去游戏皮味，quest/tier/guide/bold 不进）
# lilac_list/cream_gold 为 2026-09-29 公众号参考拆解新增的编辑向骨架：浅紫清单零图依赖、奶油金封面右半幅插画位
TEMPLATE_WHITELIST = ["wechat", "minimal", "classic", "magazine", "lilac_list", "cream_gold"]

CARD_TARGET_MIN, CARD_TARGET_MAX = 6, 8   # 目标张数
MAX_IMAGES = 20                            # 平台硬上限（图片消息 ≤20 张）
TITLE_MAX = 32                             # lint：卡标题上限
DIGEST_MAX = 120                           # lint：摘要上限
AUTHOR_DRAFT_MIN = 300                     # 作者真实底稿建议字数

# 合规尾注（迁移方案 §5.4-3，替代头条版措辞）
# 亲测模式：作者真实底稿在位，实测口吻
_COMPLIANCE_NOTE = "本文由 AI 辅助创作，实测由 AI 真机执行、全程截图留档，结果经作者复核确认。"
# 转载模式：无底稿，把他人已总结好的内容整理归纳，转述口吻 + 归源免责
_COMPLIANCE_NOTE_REPOST = "本文由 AI 辅助整理归纳，内容源自公开分享，观点与结论归原作者，如有侵权请联系删除。"

# 微信版发布检查清单（迁移方案 §5.4-6；2026-09-26 按平台官方口径校正合规两条）
_CHECKLIST = [
    "120 字摘要段（digest）在位且不超字",
    "「文章设置」勾选「内容由 AI 生成」（2025-09-01 起强制，平台不自动标注）",
    "图片消息无「声明原创」入口（平台未开放图片原创），别找该按钮、勿群发后补标",
    "文末声明使用合规尾注措辞（copy_text 已内置，勿删）",
    "「进化论XX」关键词自动回复已配置",
    "图片已上传微信素材库（单图 ≤10MB，图片/图文各 10 万条上限），正文禁外链",
    "5180 登记 + 发布后 72 小时回填数据提醒",
]


def _assets_dir():
    """背景垫图资产目录：优先 output/wechat/_assets/，缺 neutral_bg.jpg 时
    回退头条线共享资产（运行数据不进 git，新渠道冷启动无需手工拷图）。"""
    own = OUTPUT_DIR / "_assets"
    if (own / "neutral_bg.jpg").exists():
        return own
    shared = ROOT / "output" / "toutiao" / "_assets"
    if (shared / "neutral_bg.jpg").exists():
        return shared
    return own


# ── 四轴解析 + 整组渲染：generate_graphics 与 render_preview 共用同一套管线 ──
# 两条入口若各算一遍 CSS/配图，就会出现「预览一个样、成品另一个样」，
# 也正是 40-graphics-variants 记的那类「选了不生效」静默失效的温床。
def _resolve_axes(template="wechat", palette=None, font=None, layout=None, skin="", theme="",
                  photo_pool=None):
    """把「模板 + 配色/字体/版式三轴 + 皮」解析成渲染所需的一切。

    越界值一律回落默认（与历史行为一致）；layout 传 "random" 时在此抽定一个真实键，
    由调用方写进 manifest / 回填给前端，保证「看到的就是出的」。
    photo_pool：用户传的真图（已复制进包，file URI 列表）。非空时它会**优先当主视觉**，
    并解禁 wechat/lilac_list 那两套"零图位"模板——那两套禁图是为了防素材库里的游戏
    背景图自动渗进来，用户显式上传不在此列。
    """
    tpl_key = template if template in TEMPLATE_WHITELIST else "wechat"
    tpl = templates._TEMPLATES[tpl_key]
    assets_dir = _assets_dir()
    skin_key, skin_uri = skins.resolve_skin(skin, assets_dir)
    # 变体三轴（2026-09-29 接入，与头条线共用同一套 variants 内核与预设闭集）：
    #   配色轴＝皮调色板覆盖 + 模板 token 覆盖，wechat 信号格换的是信号色家族
    #           （微信绿/熔火暖橙/暮光紫），纸面保持浅底深字白卡；未接通模板自动 no-op；
    #   版式轴＝覆盖片段追加到构建好的 CSS 末尾（flex order 重排，HTML 结构不动）；
    #   字体轴＝必须把 tpl_key 传进 apply_font：wechat/minimal 全篇只有黑体栈，
    #           没有 Songti 可替换，靠预设的「按模板追加片段」才真实生效。
    palette_key, font_key = variants.resolve_keys(palette, font)
    if layout == variants.RANDOM_LAYOUT_KEY:
        layout_key = variants.pick_random_layout()
    else:
        layout_key = variants.resolve_layout(layout)
    pal = variants.resolve_palette(skin_key, palette_key, tpl_key)
    extra_tokens = dict(css_engine._tpl_tokens(tpl_key, skin_key))
    extra_tokens.update(variants.palette_tokens(tpl_key, palette_key))
    css = css_engine._build_css(tpl["css"], pal, skin_uri, assets_dir,
                                extra_tokens, CANVAS_W, CANVAS_H)
    layout_extra = variants.layout_css(tpl_key, layout_key)
    if layout_extra:
        css += "\n/* layout:%s */\n%s" % (layout_key, layout_extra)
    css = variants.apply_font(css, font_key, tpl_key)
    theme_imgs = skins._theme_image_paths(theme, skin_key, OUTPUT_DIR)
    return {"tpl_key": tpl_key, "tpl": tpl, "css": css,
            "palette_key": palette_key, "font_key": font_key, "layout_key": layout_key,
            "skin_key": skin_key, "skin_uri": skin_uri,
            "assets_dir": assets_dir, "theme_imgs": theme_imgs,
            "photo_pool": list(photo_pool or []),
            "cover_img": skins._pick_cover_image(theme_imgs)}


def _render_cards(cards, axes, out_dir, url_prefix, brand=""):
    """按已解析的四轴渲染整组卡片，返回 images 元数据列表（一次起浏览器跑完全部卡）。"""
    tpl_key, tpl, css = axes["tpl_key"], axes["tpl"], axes["css"]
    skin_key, skin_uri, cover_img = axes["skin_key"], axes["skin_uri"], axes["cover_img"]
    assets_dir, theme_imgs = axes["assets_dir"], axes["theme_imgs"]
    pool = axes.get("photo_pool") or []
    total = len(cards)
    # 零图片依赖模板（纯 CSS 装饰）：强制不取 hero，从根上杜绝游戏背景图兜底渗入。
    # 但用户显式上传了真图就解禁——那不再是"兜底抓来的素材"，是人主动要放进画面的东西。
    no_hero_tpls = ("wechat", "lilac_list")
    hero_used = []
    images = []
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": CANVAS_W, "height": CANVAS_H})
        for idx, card in enumerate(cards, 1):
            kind = card.get("kind") or "list"
            ctx = {"brand": brand, "idx": idx, "total": total, "skin_key": skin_key, "canvas_h": CANVAS_H}
            photo = pool[(idx - 1) % len(pool)] if pool else None   # 封面第 1 张，往后按页轮着配
            if photo is not None:
                hero = photo
            elif kind == "cover" and idx == 1:
                if tpl_key in no_hero_tpls:
                    hero = None
                elif tpl_key == "cream_gold":
                    # 奶油金封面右半幅插画位：只吃题材插画图，无素材则 None → 模板降级金色装饰块
                    hero = cover_img.as_uri() if cover_img is not None else None
                else:
                    hero = skins._hero_uri(card, cover_img, skin_uri, assets_dir)
            else:
                if tpl_key in no_hero_tpls or tpl_key == "cream_gold":
                    hero = None  # cream_gold 内页满幅，不占插画位
                else:
                    band = skins._pick_band_image(theme_imgs, idx)
                    hero = skins._hero_uri(card, band, skin_uri, assets_dir)
            hero_used.append(bool(photo))
            html_path = out_dir / ("img%d.html" % idx)
            png_path = out_dir / ("img%d.png" % idx)
            html_path.write_text(tpl["cover"](card, css, hero, ctx) if (kind == "cover" and idx == 1)
                                 else tpl["list"](card, css, hero, ctx), encoding="utf-8")
            renderer.render_card_html(page, html_path, png_path, CANVAS_W, CANVAS_H)
            images.append({"file": png_path.name, "label": "卡片%d" % idx,
                           "url": "%s/%s" % (url_prefix, png_path.name),
                           "source": "template",
                           # 这张卡的主视觉用的是哪张用户图（没有就空）——发布时能对上号
                           "hero": (photo or "").rsplit("/", 1)[-1] if photo else ""})
        browser.close()
    return images, hero_used


# 前端下拉/预览的适用场景说明从模板 hint 里抽，避免再抄一份会过期的清单
_FIT_RE = re.compile(r"专为「([^」]+)」")
_STYLE_RE = re.compile(r"——([^，]+)")


def template_choices():
    """模板下拉的唯一权威源：白名单 + 中文名 + 一句适用场景（取自模板 hint）。

    历史上前端自己抄了一份 4 项白名单，而渠道已是 6 项——选新模板被静默回落成 wechat。
    新增模板只改 TEMPLATE_WHITELIST 与 _TEMPLATES，前端不再需要同步。
    """
    out = []
    for key in TEMPLATE_WHITELIST:
        hint = templates._TEMPLATES[key].get("hint") or ""
        fit = _FIT_RE.search(hint) or _STYLE_RE.search(hint)
        out.append({"key": key, "label": templates._TEMPLATES[key]["label"],
                    "fit": fit.group(1) if fit else ""})
    return out


# ── 样式预览：固定样例文案 + 真实四轴渲染（不调 LLM、不进发布包） ──
PREVIEW_DIR = OUTPUT_DIR / "_previews"
PREVIEW_PAGES = 4

_PREV_COVER = {
    "kind": "cover", "bg": "neutral", "badge": "公众号",
    "title": "3 个工具省下半周活", "subtitle": "内容整理自公开分享",
    "timeline_label": "本期看点", "timeline": "据称搜完就能直接用",
    "hooks": [
        {"t": "一键出图", "d": "传图就出稿，不用调参数"},
        {"t": "免费额度", "d": "每天送 20 次，超出再收费"},
        {"t": "本地跑", "d": "断网也能用，素材不外传"},
        {"t": "踩坑提示", "d": "首批注册要等审核，别赶交付"},
    ],
}
_PREV_LISTS = [
    {"kind": "list", "title": "先说结论怎么选", "section": "结论", "icon": "论",
     "items": [
         {"name": "要出图", "desc": "选一键出图那个，模板最多", "tag": "强推"},
         {"name": "要保密", "desc": "选能在本地跑的版本", "tag": ""},
         {"name": "赶时间", "desc": "先用免费额度试一轮", "tag": "免费"},
     ]},
    {"kind": "list", "title": "上手三步", "section": "步骤", "icon": "步",
     "items": [
         {"name": "注册", "desc": "邮箱登录，领每日额度", "tag": ""},
         {"name": "传素材", "desc": "原图直接拖进去", "tag": ""},
         {"name": "导出", "desc": "选尺寸导出成品", "tag": ""},
     ]},
    {"kind": "list", "title": "网友提醒的坑", "section": "风险", "icon": "险",
     "items": [
         {"name": "价格", "desc": "额度政策可能调整，以官网为准", "tag": ""},
         {"name": "版权", "desc": "商用前先确认授权范围", "tag": ""},
         {"name": "效果", "desc": "复杂图仍要人工回修", "tag": ""},
     ],
     "note": {"title": "提醒", "text": "内容整理自公开分享，功能与价格可能调整，以官方最新说明为准。"}},
]
PREVIEW_SAMPLES = [_PREV_COVER] + _PREV_LISTS


def render_preview(template="wechat", palette=None, font=None, layout=None,
                   pages=1, skin="", theme=""):
    """用固定样例文案按当前四轴出预览图，结果按参数哈希缓存。

    存在的意义：旧版预览是离线假样张 JPG，换配色/字体/版式毫无变化，
    「选了没效果」只能等 3 分钟后看成品才知道。这里出的就是真实渲染结果。
    """
    axes = _resolve_axes(template, palette, font, layout, skin, theme)
    try:
        pages = int(pages)
    except (TypeError, ValueError):
        pages = 1
    pages = max(1, min(pages, PREVIEW_PAGES))
    key_src = "|".join([axes["tpl_key"], axes["palette_key"], axes["font_key"],
                        axes["layout_key"], skin or "", str(pages)])
    tag = hashlib.sha1(key_src.encode("utf-8")).hexdigest()[:16]
    out_dir = PREVIEW_DIR / tag
    url_prefix = "/media/wechat/_previews/%s" % tag
    cached = all((out_dir / ("img%d.png" % i)).exists() for i in range(1, pages + 1))
    if not cached:
        out_dir.mkdir(parents=True, exist_ok=True)
        _render_cards([dict(c) for c in PREVIEW_SAMPLES[:pages]], axes, out_dir, url_prefix,
                      brand=(theme or "").strip()[:12])
    return {
        "images": ["%s/img%d.png" % (url_prefix, i) for i in range(1, pages + 1)],
        "params": {"template": axes["tpl_key"], "palette": axes["palette_key"],
                   "font": axes["font_key"], "layout": axes["layout_key"], "pages": pages},
        "cached": cached,
    }


# ── LLM 卡片化（公众号版） ──
_CARDS_PROMPT = """你是公众号图片消息（newspic）排版师。把下面的素材排成 6-8 张竖版卡片（1080x1440，比例 3:4），供直接渲染出图。直接输出 JSON，不要思考过程、不要解释。

__THEME_LINE__
__TITLE_LINE__
__TEMPLATE_LINE__

__SOURCE_BLOCK__

要求：
- 第 1 张 kind="cover"：title 大标题（≤18字，尽量含数字）；subtitle 一句话（≤20字，可空）；timeline_label 固定填「本期看点」；timeline 一句话看点（≤22字）；timeline_note（≤28字，可空）；hooks 3-4 条，每条 t（≤14字）+ d（≤20字）
- 第 2-8 张 kind="list"：title（≤14字）；section（条目分组名，≤6字）；icon 单字（如 测/坑/省/比）；items 3-5 条，每条 name（≤8字）+ desc（≤30字）+ tag（≤4字，可空）；最后一张可带 note 对象，含 title 和 text 两个键
- bg 只允许 "neutral"；禁止输出 faction/faction_text 等游戏阵营字段
- 每张卡内容留呼吸感：条目宁少勿多，desc 一句话讲完，超长会被截断
- 所有文字精简口语化，一律纯文本，禁用 markdown 符号（* # ` > 等）
- 成品文案纪律（重要）：卡片文字是直接发布给读者看的成品，不是给编辑看的审校稿。
  严禁出现素材缺口说明与审核提示，例如「口播未给」「视频里没讲」「素材未提」「整合稿没写」
  「ASR/转写」「待核实」「请人工核对」「本地模板生成」「详见正文」「无」这类字样。
  素材信息不足时，直接少写一条或把该字段留空字符串，绝不写占位说明来交代缺口。
  需要提醒读者时，只能写读者视角的话（如「工具功能与价格可能调整，以官方最新说明为准」）。
- copy_text：图片消息配文，纯文本，结构 = 钩子开头（1-2句）+ 要点 4-6 条（每条一行）+ 收尾引导（1句，可引导关键词回复）+ 空行 + 「__COMPLIANCE__」，全文 200-320 字
- digest：公众号摘要（≤120字），单独成段可读懂，不写「见图」类字样
- keyword_reply：关注后自动回复引导语（≤30字，如「回复 工具清单 领取完整实测列表」；素材里没有可推关键词就留空字符串）
- source_note：来源说明一句话（≤50字，含数据口径截至{{DATE}}）

输出 JSON（cards 数组 6-8 个对象，第一个 kind 为 cover，其余为 list；copy_text/digest/keyword_reply/source_note 为字符串）：
{schema_placeholder}

整合稿：
{content}"""


# 来源约束块：亲测模式（底稿为唯一观点源）vs 转载模式（整合稿为观点源、转述口吻）
_SOURCE_BLOCK_TESTED = """最高优先约束——作者真实底稿（亲历事实，本组卡片唯一的观点与结论来源）：
- 你只做「把真实底稿结构化成卡片」这一件事，严禁凭空生成观点、严禁添加底稿里没有的判断；整合稿只作背景材料补充。
- 第一人称（我实测/我发现/我踩的坑）只能用于底稿里出现过的事实，其余内容一律转述口吻。
- 底稿与整合稿冲突时，以底稿为准。

作者真实底稿：
{draft}"""

_SOURCE_BLOCK_REPOST = """最高优先约束——转载整理模式（无作者亲测底稿，本组卡片的观点与结论全部来自下方整合稿）：
- 你只做「把整合稿里他人已总结好的内容结构化成卡片」这一件事，严禁凭空生成整合稿里没有的观点或数据。
- 全程转述口吻（如「据介绍」「实测反馈」「网友总结」），禁用第一人称亲历表述（我实测/我踩的坑）。
- 忠实归纳，不夸大、不添加未经证实的结论；整合稿信息不足时宁可少写，也不硬凑。

（本篇为转载整理，无作者亲测底稿）"""


def _build_cards_prompt(summary, author_draft, theme="", template="classic"):
    """构造公众号卡片化 prompt。schema 段与底稿/整合稿用 replace 注入，不走 str.format。"""
    schema = ('{\n  "cards": [\n'
              '    {"kind": "cover", "title": "...", "subtitle": "...", '
              '"timeline_label": "本期看点", "timeline": "...", "timeline_note": "...", '
              '"hooks": [{"t": "...", "d": "..."}], "bg": "neutral"},\n'
              '    {"kind": "list", "title": "...", "subtitle": "...", "section": "...", "icon": "...", '
              '"items": [{"name": "...", "desc": "...", "tag": "..."}], '
              '"note": {"title": "...", "text": "..."}}\n  ],\n'
              '  "copy_text": "...",\n  "digest": "...",\n  "keyword_reply": "...",\n  "source_note": "..."\n}')
    date_str = datetime.now().strftime("%m月%d日")
    theme_line = ("本篇题材：%s。封面标题与 copy_text 要点明题材，让读者一眼知道讲的是什么。" % theme) if theme else "本篇题材：未指定，按素材内容自行判断并在标题点明。"
    title_line = ""
    if (summary.get("title") or "").strip():
        title_line = ("推荐标题（来自素材/用户指定）：%s。第 1 张 cover 卡的 title 优先采用它，"
                      "可微调语气与字数但必须保留核心词，不要另起炉灶。" % summary["title"].strip())
    tpl_key = template if template in TEMPLATE_WHITELIST else "classic"
    tpl = templates._TEMPLATES[tpl_key]
    is_repost = not (author_draft or "").strip()
    source_block = _SOURCE_BLOCK_REPOST if is_repost else _SOURCE_BLOCK_TESTED
    compliance = _COMPLIANCE_NOTE_REPOST if is_repost else _COMPLIANCE_NOTE
    prompt = (_CARDS_PROMPT
              .replace("{schema_placeholder}", schema)
              .replace("{{DATE}}", date_str)
              .replace("__COMPLIANCE__", compliance)
              .replace("__THEME_LINE__", theme_line)
              .replace("__TITLE_LINE__", title_line)
              .replace("__TEMPLATE_LINE__", tpl["hint"])
              .replace("__SOURCE_BLOCK__", source_block))
    # 底稿与 content 放最后替换，避免正文里的花括号被误伤
    prompt = prompt.replace("{draft}", (author_draft or "").strip()[:3000])
    prompt = prompt.replace("{content}", (summary.get("content") or "")[:12000])
    return prompt


def _llm_cards(summary, author_draft, ai_config, theme="", template="classic"):
    """调 LLM 把素材转卡片 JSON。

    未配置 AI → 返回 None，由调用方走本地兜底（项目纪律：不阻断，e2e 也依赖这条路径）。
    已配置但失败 → 抛 GraphicsGateError。2026-09-30 那组「5 张垃圾卡片」就是这里
    `except Exception: return None` 把 402 欠费吞掉、再拿规则版骨架兜底出图造成的。
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
        prompt = _build_cards_prompt(summary, author_draft, theme, template)
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


def _items_from_lines(lines, limit=5):
    """把文本行转 items：支持「name|desc」与整行两种形态，剥掉 markdown/列表前缀。"""
    items = []
    for line in lines[:limit]:
        line = re.sub(r"^[-*#>\s]+", "", str(line).strip()).strip(" |")
        if not line:
            continue
        if "|" in line:
            seg = line.split("|")
            name, desc = seg[0][:8], seg[1][:34]
        else:
            name, desc = line[:8], line[:34]
        items.append({"name": name, "desc": desc, "tag": ""})
    # 兜底也不能写「详见整合稿正文」这类编辑向字样：图是直接给读者看的
    return items or [{"name": "说明", "desc": "这部分内容较少，可看其他几张配图", "tag": ""}]


def _fallback_cards(summary, author_draft):
    """本地模板兜底：按 wechat_material 固定【标签】分节拼 6 张卡。
    AI 未配置/调用失败时保底出图（项目纪律：AI 加工静默跳过、不阻断主流程）。"""
    content = summary.get("content") or ""
    tags = _split_tags(content)
    title = (summary.get("title") or "实测速览")[:18]

    def _lines(tag):
        return [l.strip() for l in (tags.get(tag) or "").splitlines() if l.strip()]

    lead = _lines("导语")
    arguments = _lines("核心论点")
    data_lines = _lines("关键数据")
    skeleton = _lines("正文骨架")
    quotes = _lines("金句摘录")
    risks = _lines("风险核查")
    draft_lines = [l.strip() for l in (author_draft or "").splitlines() if l.strip()]
    is_repost = not draft_lines

    hook_src = arguments or draft_lines or lead
    hooks = [{"t": "核心论点", "d": re.sub(r"^[-*#>\s]+", "", h)[:20]} for h in hook_src[:3]]
    if not hooks:
        hooks = [{"t": "看点", "d": "详见后面几张配图"}]

    cover_subtitle = "AI 辅助整理 · 内容源自公开分享" if is_repost else "作者实测 · AI 辅助整理"
    # 第 5-6 张：亲测模式=作者亲历+金句风险；转载模式无底稿，改为风险核查+金句摘录（均有整合稿来源）
    if is_repost:
        card5 = {"kind": "list", "title": "风险核查", "section": "风险", "icon": "险",
                 "items": _items_from_lines(risks),
                 "note": {"title": "提醒", "text": "内容整理自公开分享，工具功能与价格可能调整，以官方最新说明为准。"}}
        card6 = {"kind": "list", "title": "金句摘录", "section": "金句", "icon": "句",
                 "items": _items_from_lines(quotes)}
    else:
        card5 = {"kind": "list", "title": "作者亲历", "section": "亲历", "icon": "真",
                 "items": _items_from_lines(draft_lines),
                 "note": {"title": "提醒", "text": "实测结论基于当时版本与环境，后续更新可能有变化。"}}
        card6 = {"kind": "list", "title": "金句与风险", "section": "金句", "icon": "句",
                 "items": _items_from_lines(quotes + risks)}

    cards = [
        {"kind": "cover", "bg": "neutral", "badge": "公众号", "title": title,
         "subtitle": cover_subtitle,
         "timeline_label": "本期看点", "timeline": (lead[0][:22] if lead else title),
         "timeline_note": "",
         "hooks": hooks},
        {"kind": "list", "title": "核心论点", "section": "论点", "icon": "论",
         "items": _items_from_lines(arguments)},
        {"kind": "list", "title": "关键数据", "section": "数据", "icon": "数",
         "items": _items_from_lines(data_lines)},
        {"kind": "list", "title": "正文要点", "section": "要点", "icon": "文",
         "items": _items_from_lines(skeleton)},
        card5,
        card6,
    ]
    if is_repost:
        copy_text = (
            "%s\n\n内容整理自公开分享，6 张卡片看懂要点：\n\n"
            "📌 核心论点：见第 2 张\n📌 关键数据：见第 3 张\n"
            "📌 风险与金句：见第 5-6 张，建议收藏\n\n%s" % (title, _COMPLIANCE_NOTE_REPOST)
        )
    else:
        copy_text = (
            "%s\n\n作者真机实测，6 张卡片看懂结论：\n\n"
            "📌 核心论点：见第 2 张\n📌 关键数据：见第 3 张\n"
            "📌 作者亲历的坑：见第 5 张，建议收藏\n\n%s" % (title, _COMPLIANCE_NOTE)
        )
    digest_src = (lead[0] if lead else "") + (re.sub(r"^[-*#>\s]+", "", arguments[0]) if arguments else "")
    digest = (digest_src or title)[:DIGEST_MAX]
    source_note = ("整理归纳自公开分享内容（数据口径截至%s）" if is_repost
                   else "基于作者真机实测整理（数据口径截至%s）") % datetime.now().strftime("%m月%d日")
    return {"cards": cards, "copy_text": copy_text, "digest": digest,
            "keyword_reply": "", "source_note": source_note}


def lint_package(manifest, out_dir):
    """发布前合规/质量检查（迁移方案 §5.4-7 + 铁律）。返回问题列表，空 = 通过。
    前缀 error=阻断发布，warn=建议处理。张数/画布/单图体积/字数 + 真实截图 ≥2 张。"""
    issues = []
    images = manifest.get("images") or []
    cards = manifest.get("cards") or []
    if len(images) > MAX_IMAGES:
        issues.append("error: 共 %d 张图，超平台硬上限 %d 张" % (len(images), MAX_IMAGES))
    expect_canvas = "%dx%d" % (CANVAS_W, CANVAS_H)
    if manifest.get("canvas") != expect_canvas:
        issues.append("error: 画布 %s 非 3:4（应为 %s）" % (manifest.get("canvas"), expect_canvas))
    if len(cards) < CARD_TARGET_MIN:
        issues.append("warn: 仅 %d 张卡，低于目标下限 %d 张" % (len(cards), CARD_TARGET_MIN))
    elif len(cards) > CARD_TARGET_MAX:
        issues.append("warn: %d 张卡，超目标上限 %d 张（平台硬上限 %d）" % (len(cards), CARD_TARGET_MAX, MAX_IMAGES))
    for i, card in enumerate(cards, 1):
        t = card.get("title") or ""
        if len(t) > TITLE_MAX:
            issues.append("error: 第 %d 张卡标题 %d 字，超 %d 字上限" % (i, len(t), TITLE_MAX))
    digest = manifest.get("digest") or ""
    if not digest:
        issues.append("warn: digest 摘要缺失（发布页需要 120 字摘要段）")
    elif len(digest) > DIGEST_MAX:
        issues.append("warn: digest %d 字，超 %d 字上限" % (len(digest), DIGEST_MAX))
    # 兜底提醒只进 lint 面板（给编辑看），不再写进卡片图（给读者看）
    if manifest.get("model") == "local-template":
        issues.append("warn: 本次为本地模板兜底生成（LLM 不可用），发布前请人工核对数值与结论")
    # 转载模式（无作者真实底稿）：内容源自公开分享、无需亲测截图，跳过截图/底稿字数铁律
    is_repost = not (manifest.get("author_draft") or "").strip()
    if not is_repost:
        shots = [im for im in images if im.get("source") == "real_screenshot"]
        if len(shots) < 2:
            issues.append("warn: 真实截图 %d 张，铁律要求 ≥2 张且禁 AI 插画冒充截图" % len(shots))
        if len(manifest.get("author_draft") or "") < AUTHOR_DRAFT_MIN:
            issues.append("warn: 作者真实底稿不足 %d 字，建议补充亲历细节" % AUTHOR_DRAFT_MIN)
    pkg_dir = out_dir / str(manifest.get("summary_id"))
    for im in images:
        p = pkg_dir / (im.get("file") or "")
        if not p.exists():
            continue
        size = p.stat().st_size
        if size > 10 * 1024 * 1024:
            issues.append("error: %s 超 10MB 素材库上限" % im.get("file"))
        elif size > 1024 * 1024:
            issues.append("warn: %s 超 1MB，走 uploadImage 通道需压缩" % im.get("file"))
    return issues


def generate_graphics(summary, ai_config, author_draft="", progress_cb=None,
                      theme="", skin="", title="", template="wechat", real_screenshots=None,
                      palette=None, font=None, layout=None, ref_token=""):
    """主入口：wechat_material 整合稿 → manifest dict（落盘 output/wechat/<summary_id>/）。

    两种模式（由 author_draft 是否为空自动判定）：
      - 亲测模式（draft 非空）：底稿为唯一观点源，实测口吻，lint 要求真实截图 ≥2 张。
      - 转载模式（draft 为空）：把他人已总结好的整合稿归纳成图文，转述口吻，无需底稿/截图。

    author_draft:     str 作者真实底稿；亲测模式建议 ≥300 字，转载模式留空即可
    theme:            str 题材名，注入 prompt + 选插画素材文件夹（可空）
    skin:             str 调色板/背景垫图（可空；wechat 模板不取背景图，天然去游戏资产）
    title:            str 图文标题，非空时覆盖素材标题
    template:         str 模板，限白名单 wechat/minimal/classic/magazine/lilac_list/cream_gold，越界回落 wechat
    real_screenshots: list 真实截图路径（亲测模式铁律 ≥2 张），复制进包并标 source=real_screenshot
    palette:          str 配色预设键（variants.PALETTE_KEYS 闭集）；None/未知回落默认 gold_night。
                      只对已接通配色轴的模板生效（wechat/lilac_list/cream_gold 换信号色家族、classic 同）；
                      minimal/magazine 未接通，选了也保持皮原色（前端 hint 已如实标注）。
    font:             str 字体预设键（variants.FONT_KEYS 闭集）；None/未知回落默认 serif（现状）。
    layout:           str 版式预设键（variants.LAYOUT_KEYS 闭集）；None/未知回落默认 default（现状）；
                      传 "random"（variants.RANDOM_LAYOUT_KEY）＝本次生成随机抽一个真实预设，
                      整包统一，抽中的真实键写进 manifest.layout。
    ref_token:        str 排版参考图令牌（/api/wechat/ref-upload 返回）；只在 manifest 记 ref_images
                      指回 output/wechat/_refs/<token>/，不复制进包、不参与出图——它是下一步
                      「逆向拆解排版」的输入，不是发布素材。
    """
    draft = (author_draft or "").strip()
    is_repost = not draft
    gate.assert_draft_generatable(summary, "公众号图文")
    summary_id = int(summary["id"])
    if (title or "").strip():
        summary = dict(summary)
        summary["title"] = title.strip()[:60]
    out_dir = OUTPUT_DIR / str(summary_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _pg(msg):
        if progress_cb:
            progress_cb(msg)

    # 用户传的真图先复制进包，再当主视觉池用：渲染卡片时要以 file URI 引用它们，
    # 所以这一步必须在 _resolve_axes / _render_cards 之前完成（原来是渲染完才复制，
    # 图只是附在卡片后面，从没进过画面）。
    shots = []
    for src in (real_screenshots or []):
        sp = Path(src)
        if not sp.is_file():
            continue
        dst = out_dir / ("shot_%s" % re.sub(r"[^\w.-]", "", sp.name)[:60])
        shutil.copyfile(sp, dst)
        shots.append(dst)
    photo_pool = [p.as_uri() for p in shots]

    axes = _resolve_axes(template, palette, font, layout, skin, theme, photo_pool=photo_pool)
    tpl_key, tpl = axes["tpl_key"], axes["tpl"]
    skin_key = axes["skin_key"]
    skin_uri = axes["skin_uri"]
    palette_key = axes["palette_key"]
    font_key = axes["font_key"]
    layout_key = axes["layout_key"]

    _pg("LLM 卡片化素材（模板：%s）..." % tpl["label"])
    data = _llm_cards(summary, draft, ai_config, theme, tpl_key)
    model = "local-template"
    if data:
        model = ai_config.get("model") or "llm"
    else:
        _pg("未配置 AI，走本地模板兜底（成品需人工核对）...")
        data = _fallback_cards(summary, draft)

    cards = data.get("cards") or []
    # 渲染前清洗：剔除「素材未提」「请人工核对」等编辑向元语言（成品图直接面向读者）
    cards = ir.sanitize_cards(cards)
    if len(cards) > MAX_IMAGES:
        cards = cards[:MAX_IMAGES]  # 平台硬上限截断，lint 会再报
    if skin_uri:
        for card in cards:
            card["bg"] = "skin"
    total = len(cards)
    _pg("渲染 %d 张卡片（模板：%s，画布 %dx%d）..." % (total, tpl["label"], CANVAS_W, CANVAS_H))
    if shots:
        _pg("用户上传的 %d 张真图已排进卡片主视觉..." % len(shots))
    images, hero_used = _render_cards(cards, axes, out_dir, "/media/wechat/%d" % summary_id,
                                      brand=(theme or "").strip()[:12])

    # 原图也留在包里：卡片是"加工后的成品"，发布清单核验和回溯都得能看到原始截图。
    # 亲测模式的 ≥2 张铁律查的就是这个列表，不能因为图进了卡片就不列。
    for i, dst in enumerate(shots):
        images.append({"file": dst.name, "label": "真实截图%d" % (i + 1),
                       "url": "/media/wechat/%d/%s" % (summary_id, dst.name),
                       "source": "real_screenshot"})

    # 排版参考图：只记路径供后续「逆向拆解排版」取用，不复制进包（免得混进发布素材）
    ref_images = []
    if ref_token and re.fullmatch(r"[0-9a-f]{12}", ref_token):
        ref_dir = OUTPUT_DIR / "_refs" / ref_token
        if ref_dir.is_dir():
            ref_exts = {".jpg", ".jpeg", ".png", ".webp"}
            ref_images = ["/media/wechat/_refs/%s/%s" % (ref_token, p.name)
                          for p in sorted(ref_dir.iterdir())
                          if p.is_file() and p.suffix.lower() in ref_exts]

    manifest = {
        "id": summary_id,
        "summary_id": summary_id,
        "channel": "wechat",
        "mode": "repost" if is_repost else "tested",
        "title": summary.get("title") or "",
        "copy_text": data.get("copy_text") or "",
        "digest": data.get("digest") or "",
        "keyword_reply": data.get("keyword_reply") or "",
        "source_note": data.get("source_note") or "",
        "author_draft": draft,
        "images": images,
        # 真图进了哪几张卡：发布时能对上"卡片3 用的是我传的哪张图"，也方便回溯
        "hero_photos": [p.name for p in shots],
        "photos_in_cards": sum(1 for x in hero_used if x),
        "cards": cards,
        "model": model,
        "theme": (theme or "").strip(),
        "skin": skin_key,
        "template": tpl_key,
        "palette": palette_key,
        "font": font_key,
        "layout": layout_key,
        "ref_images": ref_images,
        "canvas": "%dx%d" % (CANVAS_W, CANVAS_H),
        "summary_type": summary.get("summary_type", ""),
        "checklist": list(_CHECKLIST),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    manifest["lint"] = lint_package(manifest, OUTPUT_DIR)

    # 导出包：manifest + 卡片图 + 真实截图 + 文案文件（人工上传素材库时直接取用）
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    copy_lines = [
        "标题：%s" % manifest["title"],
        "摘要（≤120字）：%s" % manifest["digest"],
        "",
        "【图片消息配文】",
        manifest["copy_text"],
        "",
        "【关键词自动回复】%s" % (manifest["keyword_reply"] or "（待配置）"),
        "【来源说明】%s" % manifest["source_note"],
        "",
        "【发布检查清单】",
    ]
    copy_lines.extend(["- %s" % c for c in _CHECKLIST])
    if manifest["lint"]:
        copy_lines.append("")
        copy_lines.append("【lint 结果】")
        copy_lines.extend(["- %s" % i for i in manifest["lint"]])
    (out_dir / "copy_text.txt").write_text("\n".join(copy_lines) + "\n", encoding="utf-8")
    return manifest


# ── manifest 包管理薄封装（绑定公众号 OUTPUT_DIR，形状与头条线一致，Phase 3 接 API）──
def list_packages():
    """扫描 output/wechat/*/manifest.json，倒序返回列表。"""
    return package.list_packages(OUTPUT_DIR)


def get_package(summary_id):
    """读单个包 manifest；不存在返回 None。"""
    return package.get_package(summary_id, OUTPUT_DIR)


def delete_package(summary_id):
    """删包目录（manifest + 图片 + 截图 + 残留 html）。"""
    return package.delete_package(summary_id, OUTPUT_DIR)


class WeChatChannel(ChannelAdapter):
    """公众号图片消息渠道适配器：3:4 画布 + 模板白名单 + 底稿必填 + lint/清单/导出。"""
    name = "wechat"
    canvas = (CANVAS_W, CANVAS_H)
    card_count = CARD_TARGET_MIN

    def templates(self):
        return list(TEMPLATE_WHITELIST)

    def prompt(self, summary, theme="", template="classic", author_draft=""):
        return _build_cards_prompt(summary, author_draft, theme=theme, template=template)

    def limits(self):
        return {"title": TITLE_MAX, "digest": DIGEST_MAX, "copy_text": (200, 320),
                "cards": (CARD_TARGET_MIN, CARD_TARGET_MAX), "max_images": MAX_IMAGES,
                "author_draft_min": AUTHOR_DRAFT_MIN}

    def lint(self, pkg):
        """pkg 为 manifest dict（与基类 cards 入参不同：公众号 lint 需要包级字段）。"""
        return lint_package(pkg, OUTPUT_DIR)

    def checklist(self):
        return list(_CHECKLIST)

    def export(self, summary, ai_config, **kwargs):
        return generate_graphics(summary, ai_config, **kwargs)
