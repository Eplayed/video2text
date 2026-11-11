# -*- coding: utf-8 -*-
"""公众号图片消息（newspic）渠道适配器（Phase 2 → 2026-11-03 并入头条渲染循环）。

2026-11-03 站主拍板「公众号产线正式合并到头条渲染循环」（双渠道样稿两轮验证通过，
样稿记录见 output/角色交接/2026-11-03-wechat-dev.md）：公众号图片消息与头条共用同一套
渲染循环（toutiao.render_cards，模板白名单同源、默认 classic 暗金），画布仍 3:4
（1080x1440）。公众号侧保留自己的输入与 manifest 契约：作者真实底稿（亲测/转载双模式）、
真实截图、digest/keyword_reply/source_note、合规尾注、发布检查清单、lint。
文字攻略页（kind="text"）是本次新增页型，出图前过机制断言闸（_claims_gate）。

Python 3.9 兼容：不用 match / X|Y 语法。
"""
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from .. import skins, templates, package, ir, variants, gate
from . import toutiao
from .base import ChannelAdapter
# 借用头条线的 JSON 容错解析与【标签】切段（两者均渠道无关，Phase 3 视情上移内核）
from .toutiao import _parse_json, _split_tags

# ── 公众号渠道目录 / 画布常量 ──
ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = ROOT / "output" / "wechat"
CANVAS_W, CANVAS_H = 1080, 1440  # 3:4，微头条试跑已验证同机制跑得通

# 模板白名单：合并后与头条同源（2026-11-03）。旧的 6 套公众号身份模板（wechat 信号格、
# lilac_list、cream_gold 等）随本次合并退役出下拉——它们是合并前按「公众号独立版式」设计的，
# 样稿验证的合并方案就是「公众号直接用头条主题」。
TEMPLATE_WHITELIST = list(toutiao.TEMPLATE_WHITELIST)
TEMPLATE_DEFAULT = "classic"

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
    """背景垫图资产目录（2026-11-03 合并后仅剩参照用途）：优先 output/wechat/_assets/，
    缺 neutral_bg.jpg 时回退头条线共享资产（运行数据不进 git，新渠道冷启动无需手工拷图）。
    渲染已统一走头条循环（垫图由 toutiao._ASSETS_DIR 提供），本函数保留给包管理回看。"""
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
# 2026-11-03 合并后：CSS 与截图统一走头条循环（toutiao._page_css / render_cards），
# 这里只做四轴闭集校验与渲染上下文组装，公众号侧钉死 3:4 画布与 /media/wechat 前缀。
def _resolve_axes(template=TEMPLATE_DEFAULT, palette=None, font=None, layout=None,
                  skin="wow", theme=""):
    """把「模板 + 配色/字体/版式三轴 + 皮」解析成渲染上下文（不含 CSS，CSS 由渲染壳现拼）。

    越界值一律回落默认（与历史行为一致）；layout 传 "random" 时在此抽定一个真实键，
    由调用方写进 manifest / 回填给前端，保证「看到的就是出的」。
    skin 默认 "wow"：合并方案就是「公众号用头条同一套暗金皮」，空值不再是无皮状态。
    theme_imgs：题材插画作候选素材（旧契约保留）；用户真实截图在 generate_graphics 里
    优先级更高，会直接覆盖它。
    """
    tpl_key = template if template in TEMPLATE_WHITELIST else TEMPLATE_DEFAULT
    palette_key, font_key = variants.resolve_keys(palette, font)
    if layout == variants.RANDOM_LAYOUT_KEY:
        layout_key = variants.pick_random_layout()
    else:
        layout_key = variants.resolve_layout(layout)
    skin_key, skin_uri = skins.resolve_skin(skin or "wow", toutiao._ASSETS_DIR)
    theme_imgs = skins._theme_image_paths(theme, skin_key, OUTPUT_DIR)
    return {"tpl_key": tpl_key, "palette_key": palette_key, "font_key": font_key,
            "layout_key": layout_key, "skin_key": skin_key, "skin_uri": skin_uri,
            "theme_imgs": theme_imgs}


def _render_cards(cards, axes, out_dir, url_prefix, brand="", on_card=None):
    """整组渲染：合并后只是**头条渲染循环的一层薄壳**（toutiao.render_cards）。

    为什么留这层壳不让调用方直接调头条：渠道契约要钉在一处——3:4 画布（CSS 与截图视口
    必须同源）、/media/wechat URL 前缀、确定取图（rng=None：公众号素材随机**有意不接**，
    主视觉是用户真图或无图，随机换图无意义）。返回头条循环的五元组原样上抛
    （images / used_assets / card_styles / icon_report / term_report），manifest 字段更全。
    `on_card(idx, total)` 每出完一张回调一次，界面进度条要的是真实步数（老规矩不变）。
    """
    tpl_key = axes["tpl_key"]
    css = toutiao._page_css(tpl_key, axes["palette_key"], axes["font_key"],
                            axes["layout_key"], axes["skin_key"], axes["skin_uri"],
                            canvas=(CANVAS_W, CANVAS_H))
    theme_imgs = axes["theme_imgs"]
    cover_img = skins._pick_cover_image(theme_imgs) if theme_imgs else None
    st = {"tpl_key": tpl_key, "palette_key": axes["palette_key"],
          "font_key": axes["font_key"], "layout_key": axes["layout_key"],
          "skin_key": axes["skin_key"], "skin_uri": axes["skin_uri"],
          "theme_imgs": theme_imgs, "cover_img": cover_img, "rng": None,
          "brand": brand, "summary_id": 0,
          "css_cache": {(tpl_key, axes["palette_key"]): css},
          "canvas": (CANVAS_W, CANVAS_H), "url_prefix": url_prefix}
    if on_card:
        st["on_card"] = on_card
    return toutiao.render_cards(cards, out_dir, st)


def template_choices():
    """模板下拉的唯一权威源：白名单 + 中文名 + 一句适用场景（取自模板 hint）。

    历史上前端自己抄了一份 4 项白名单，而渠道已是 6 项——选新模板被静默回落成 wechat。
    新增模板只改 TEMPLATE_WHITELIST 与 _TEMPLATES，前端不再需要同步。
    名单构造逻辑在 `templates.choice_list`，与头条渠道共用。
    """
    return templates.choice_list(TEMPLATE_WHITELIST)


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


def render_preview(template=TEMPLATE_DEFAULT, palette=None, font=None, layout=None,
                   pages=1, skin="wow", theme=""):
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
_CARDS_PROMPT = """你是公众号图片消息（newspic）排版师。把下面的素材按信息量排成竖版卡片（1080x1440，比例 3:4），供直接渲染出图。直接输出 JSON，不要思考过程、不要解释。

__THEME_LINE__
__TITLE_LINE__
__TEMPLATE_LINE__

__SOURCE_BLOCK__

要求：
- 第 1 张 kind="cover"：title 大标题（≤18字，素材确有关键数值才写数值）；subtitle 一句话（≤20字，可空）；timeline_label 可填「本期看点」；timeline 一句话看点（≤22字）；timeline_note（≤28字，可空）；hooks 按内容填写，每条 t（≤14字）+ d（≤20字）
- 后续按内容选择 kind="list"：title（≤14字）；section（条目分组名，≤6字）；icon 单字（如 测/坑/省/比）；items 按需分卡，每条 name（≤8字）+ desc（≤30字）+ tag（≤4字，可空）。3:4 画布单卡最多 5 条，超出就加页，不删条目
- 只有操作步骤需要整页展开时才用 kind="text" 文字攻略页，一页只讲清一个动作：
  title（≤14字）；subtitle（≤24字）；precondition（≤40字，读者要先知道的前提，口语化不写机器腔如「本文适用版本快照」）；
  steps 3-4 条，每条 h（≤10字小标题）+ p（≤55字，一到两句）；footer（≤30字，数值/机制的出处，如「数值出处：官方说明 2026-10-26」）。
  steps 里出现的每个数值、机制名都必须能在整合稿里找到出处，严禁编造、凑整或挪用其他条目的数字；概括性说法要能落到具体条目，不许含糊带过
- 禁止输出 note 字段（成品图不再渲染「提醒」块）
- bg 只允许 "neutral"；禁止输出 faction/faction_text 等游戏阵营字段
- 每张卡内容留呼吸感；保留主体、条件和否定词。说明超长时拆页，不截断原意
- 所有文字精简口语化，一律纯文本，禁用 markdown 符号（* # ` > 等）
- 成品文案纪律（重要）：卡片文字是直接发布给读者看的成品，不是给编辑看的审校稿。
  严禁出现素材缺口说明与审核提示，例如「口播未给」「视频里没讲」「素材未提」「整合稿没写」
  「ASR/转写」「待核实」「请人工核对」「本地模板生成」「详见正文」「无」这类字样。
  素材信息不足时，不添加新事实；已有核心信息和条件仍须保留，不能为了版面删条目。
  需要提醒读者时，只能写读者视角的话（如「工具功能与价格可能调整，以官方最新说明为准」）。
- copy_text：图片消息配文，纯文本，开头直接给核心信息，再写必要要点与条件；不强制钩子或互动；末尾按需附「__COMPLIANCE__」，全文以完整表达为先
- digest：公众号摘要（≤120字），单独成段可读懂，不写「见图」类字样
- keyword_reply：关注后自动回复引导语（≤30字，如「回复 工具清单 领取完整实测列表」；素材里没有可推关键词就留空字符串）
- source_note：来源说明一句话（≤50字，含数据口径截至{{DATE}}）

输出 JSON（cards 数组按内容量确定，第一个 kind 为 cover，其余按需为 list 或 text；copy_text/digest/keyword_reply/source_note 为字符串）。卡片数量受生成接口容量限制，装不下就指出缺口，不要丢条目：
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


def _build_cards_prompt(summary, author_draft, theme="", template="classic", mother=None):
    """构造公众号卡片化 prompt。schema 段与底稿/整合稿用 replace 注入，不走 str.format。"""
    schema = ('{\n  "cards": [\n'
              '    {"kind": "cover", "title": "...", "subtitle": "...", '
              '"timeline_label": "本期看点", "timeline": "...", "timeline_note": "...", '
              '"hooks": [{"t": "...", "d": "..."}], "bg": "neutral"},\n'
              '    {"kind": "list", "title": "...", "subtitle": "...", "section": "...", "icon": "...", '
              '"items": [{"name": "...", "desc": "...", "tag": "..."}]},\n'
              '    {"kind": "text", "title": "...", "subtitle": "...", "precondition": "...", '
              '"steps": [{"h": "...", "p": "..."}], "footer": "..."}\n  ],\n'
              '  "copy_text": "...",\n  "digest": "...",\n  "keyword_reply": "...",\n  "source_note": "..."\n}')
    if mother:
        schema = schema.replace('"bg": "neutral"}',
                                '"bg": "neutral", "point_ids": ["逐字复制的母稿 point_id"]}')
        schema = schema.replace('"tag": "..."}]}',
                                '"tag": "..."}], "point_ids": ["逐字复制的母稿 point_id"]}')
        schema = schema.replace('"footer": "..."}',
                                '"footer": "...", "point_ids": ["逐字复制的母稿 point_id"]}')
    date_str = datetime.now().strftime("%m月%d日")
    theme_line = ("本篇题材：%s。封面标题与 copy_text 要点明题材，让读者一眼知道讲的是什么。" % theme) if theme else "本篇题材：未指定，按素材内容自行判断并在标题点明。"
    title_line = ""
    if (summary.get("title") or "").strip():
        title_line = ("推荐标题（来自素材/用户指定）：%s。第 1 张 cover 卡的 title 优先采用它，"
                      "可微调语气与字数但必须保留核心词，不要另起炉灶。" % summary["title"].strip())
    if mother:
        title_line = "封面标题只取原意母稿中可定位的核心答案；旧整合稿标题不作事实依据。"
    tpl_key = template if template in TEMPLATE_WHITELIST else "classic"
    tpl = templates._TEMPLATES[tpl_key]
    is_repost = not (author_draft or "").strip()
    source_block = _SOURCE_BLOCK_REPOST if is_repost else _SOURCE_BLOCK_TESTED
    if mother:
        source_block = ("最高优先约束——原意母稿模式：只从下方逐字定位到原始转写的要点制作卡片。"
                        "旧整合稿不作事实依据；保留原句中的主体、数量、阶段、条件和不确定性。"
                        "不得把视频口播说成官方公告，也不得补游戏常识。")
    compliance = _COMPLIANCE_NOTE_REPOST if is_repost else _COMPLIANCE_NOTE
    if mother:
        compliance = ""
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
    content = (summary.get("content") or "")[:12000]
    if mother:
        from src import guide_mother
        content = guide_mother.mother_prompt(mother)
        prompt = prompt.replace("整合稿：\n{content}", "原意母稿：\n{content}")
        prompt = prompt.replace("需要提醒读者时，只能写读者视角的话（如「工具功能与价格可能调整，以官方最新说明为准」）。",
                                "提醒只写原始转写中确有的条件或限制，不添加与题材无关的免责声明。")
        prompt = prompt.replace("末尾按需附「」，", "末尾无需附 AI 声明或侵权声明，")
        prompt = prompt.replace("如「回复 工具清单 领取完整实测列表」；素材里没有可推关键词就留空字符串",
                                "源文没有可提供的清单就留空字符串")
        prompt = re.sub(r"来源说明一句话（≤50字，含数据口径截至[^）]+）",
                        "来源说明一句话（≤50字，只写来源归属，不补核验日期）", prompt)
        prompt += ("\n【原意母稿约束】上面的整合稿正文已替换成逐字可定位的原意点。"
                   "只用这些点写卡片；每张卡片（含封面）增加 point_ids 数组，"
                   "列出真正支撑本张标题、条目和配文的 ID。所有 must 点必须出现在卡片。"
                   "不得改变主体数量、谁取得点数、适用阶段、条件或否定词。"
                   "原文给出具体专名时逐个保留，不压成泛称；原文只列流派时不要写成推荐。")
    prompt = prompt.replace("{content}", content)
    return prompt


def _llm_cards(summary, author_draft, ai_config, theme="", template="classic", mother=None):
    """调 LLM 把素材转卡片 JSON。

    未配置 AI → 返回 None，由调用方走本地兜底（项目纪律：不阻断，e2e 也依赖这条路径）。
    已配置但失败 → 抛 GraphicsGateError。2026-09-30 那组「5 张垃圾卡片」就是这里
    `except Exception: return None` 把 402 欠费吞掉、再拿规则版骨架兜底出图造成的。
    """
    if not gate.ai_usable(ai_config):
        return None
    try:
        import openai
        from ...editorial_model import EVIDENCE_RULES, chat_options

        client = openai.OpenAI(
            api_key=ai_config["api_key"],
            base_url=ai_config.get("api_base") or "https://api.openai.com/v1",
            timeout=300, max_retries=0 if ai_config.get("editorial") else 1,
        )
        prompt = _build_cards_prompt(summary, author_draft, theme, template, mother=mother)
        if ai_config.get("editorial") and mother:
            prompt += ("\n【逐项校验规则】" + EVIDENCE_RULES
                       + "\n列表卡最多 5 项，步骤卡最多 4 步，超出就拆卡。"
                         "封面副标题缺证据可留空；配文与摘要逐句和母稿比对。")
        resp = client.chat.completions.create(
            model=ai_config.get("model") or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            **chat_options(ai_config, 0.3),
        )
        text = resp.choices[0].message.content
        data = _parse_json(text)
    except Exception as e:
        raise gate.GraphicsGateError("卡片化模型调用失败（模型 %s）：%s"
                                     % (ai_config.get("model") or "?", gate.brief_error(e)))
    cards = (data or {}).get("cards") or []
    if not cards or not (data or {}).get("copy_text"):
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
    # 2026-10-26 起这一档的【正文】是完整分节文章（不再只有骨架）。配图取**小节标题行**：
    # 整段正文塞进卡片会被 _items_from_lines 切成 8/34 字的碎句，那既不像图也读不通。
    # 存量老稿还是【正文骨架】那一节，读不到新节就退回它，不然这 8 篇一点图都出不来。
    body_lines = _lines("正文") or _lines("正文骨架")
    skeleton = [l for l in body_lines if l.startswith("##")] or body_lines
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


def _claims_gate(summary, cards):
    """文字攻略页（kind="text"）的机制断言闸：出图前对**当前**文本逐句判。

    口径与海报线同源（claim_check.check_fields），按样稿两轮的经验收口：
    - 登记表读不到 → 跳过并如实记录，不拦（没有判据就不能硬拦）；
    - 题材不在登记表覆盖范围（非魔兽无限种族机制类）→ 跳过，由既有人工核对流程兜住；
    - 在范围内且判出 hard → 抛 GraphicsGateError 拒绝出图。概括句要么拆成原子断言
      要么改成导航句，一行混两条断言会因锚点不全被拦（样稿第一轮 hard=8 全是真问题）。
    只闸文字攻略页：list 卡是头条线多年现状，本轮不扩（记进 manifest 供追溯）。
    """
    from src import claim_check
    report = {"checked": 0, "hard": [], "soft": [], "skipped": ""}
    pages = [c for c in cards if isinstance(c, dict) and c.get("kind") == "text"]
    if not pages:
        report["skipped"] = "本包无文字攻略页，机制断言闸未跑"
        return report
    reg = claim_check.load()
    if not (reg or {}).get("claims"):
        report["skipped"] = "机制断言登记表读不到（config/wow_claims.json），这一项没判"
        return report
    scope = "%s\n%s" % (summary.get("title") or "", (summary.get("content") or "")[:4000])
    if not claim_check.in_scope(scope, reg):
        report["skipped"] = "登记表只覆盖魔兽无限服种族机制，本篇不在覆盖范围，按既有人工核对流程"
        return report
    fields = []
    for i, c in enumerate(pages, 1):
        label = "文字页%d.%s" % (i, c.get("title") or "")
        fields.append((label, "。".join([c.get("title") or "", c.get("subtitle") or "",
                                         c.get("precondition") or "", c.get("footer") or ""])))
        for st in c.get("steps") or []:
            fields.append((label + "." + (st.get("h") or ""),
                           "%s。%s" % (st.get("h") or "", st.get("p") or "")))
    res = claim_check.check_fields(fields)
    report.update({"checked": res.get("checked", 0),
                   "hard": res.get("hard") or [], "soft": res.get("soft") or []})
    if report["hard"]:
        raise gate.GraphicsGateError(
            "文字攻略页机制断言未过事实闸（hard %d 条，如：%s）。概括句拆成原子断言或改成导航句，"
            "数值补登记后再出图" % (len(report["hard"]), str(report["hard"][0])[:80]))
    return report


def lint_package(manifest, out_dir):
    """发布前合规/质量检查（迁移方案 §5.4-7 + 铁律）。返回问题列表，空 = 通过。
    前缀 error=阻断发布，warn=建议处理。张数/画布/单图体积/字数 + 真实截图 ≥2 张
    + 3:4 排版硬约束（2026-11-03 样稿实测：list 卡 6 条溢出画布）。"""
    issues = []
    images = manifest.get("images") or []
    cards = manifest.get("cards") or []
    if len(images) > MAX_IMAGES:
        issues.append("error: 共 %d 张图，超平台硬上限 %d 张" % (len(images), MAX_IMAGES))
    expect_canvas = "%dx%d" % (CANVAS_W, CANVAS_H)
    if manifest.get("canvas") != expect_canvas:
        issues.append("error: 画布 %s 非 3:4（应为 %s）" % (manifest.get("canvas"), expect_canvas))
    for i, card in enumerate(cards, 1):
        t = card.get("title") or ""
        if len(t) > TITLE_MAX:
            issues.append("error: 第 %d 张卡标题 %d 字，超 %d 字上限" % (i, len(t), TITLE_MAX))
        kind = card.get("kind") or "list"
        if kind == "list" and len(card.get("items") or []) > 5:
            issues.append("error: 第 %d 张卡 %d 条条目——3:4 画布单卡上限 5 条"
                          "（样稿实测 6 条溢出：标题被截、末条叠页脚），拆成两卡或删减"
                          % (i, len(card.get("items"))))
        if kind == "text" and len(card.get("steps") or []) > 4:
            issues.append("warn: 第 %d 张文字攻略页 %d 个步骤，3:4 紧凑档建议 ≤4 条"
                          % (i, len(card.get("steps"))))
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
                      theme="", skin="", title="", template=TEMPLATE_DEFAULT, real_screenshots=None,
                      palette=None, font=None, layout=None, ref_token="", mother=None,
                      source_text="", output_root=None, prepared_data=None,
                      prechecked_fidelity=None, evidence_sources=None):
    """主入口：wechat_material 整合稿 → manifest dict（落盘 output/wechat/<summary_id>/）。

    2026-11-03 合并后：渲染统一走头条循环（toutiao.render_cards），模板白名单与头条同源、
    默认 classic 暗金，画布仍 3:4；skin 默认 "wow"（头条同一套皮）。两种模式不变：
      - 亲测模式（draft 非空）：底稿为唯一观点源，实测口吻，lint 要求真实截图 ≥2 张。
      - 转载模式（draft 为空）：把他人已总结好的整合稿归纳成图文，转述口吻，无需底稿/截图。

    author_draft:     str 作者真实底稿；亲测模式建议 ≥300 字，转载模式留空即可
    theme:            str 题材名，注入 prompt + 选插画素材文件夹（可空）
    skin:             str 皮（可空；空值回落 "wow"，与头条同一套暗金垫图）
    title:            str 图文标题，非空时覆盖素材标题
    template:         str 模板，白名单与头条同源（toutiao.TEMPLATE_WHITELIST），越界回落 classic
    real_screenshots: list 真实截图路径（亲测模式铁律 ≥2 张），复制进包并标 source=real_screenshot；
                      有真图时**优先当主视觉**（封面取竖图，其余按页轮换，确定取法不随机）
    palette:          str 配色预设键（variants.PALETTE_KEYS 闭集）；None/未知回落默认 gold_night。
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
    package_root = Path(output_root) if output_root is not None else OUTPUT_DIR
    out_dir = package_root / str(summary_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _pg(msg, done=None, total=None):
        # done/total 是**真实步数**（渲染第几张），不是估的百分比：拿不到步数的阶段就留 None，
        # 界面画成流动条。编一个"已完成 40%"比不画更糟——号主会拿它判断还要等多久。
        if progress_cb:
            progress_cb(msg, done, total)

    # 用户传的真图先复制进包，再当主视觉用：渲染卡片时要以 file URI 引用它们，
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

    axes = _resolve_axes(template, palette, font, layout, skin, theme)
    if shots:
        # 用户真图优先当主视觉，覆盖题材插画（老契约：真图是人主动要放进画面的东西）
        axes["theme_imgs"] = shots
    tpl_key = axes["tpl_key"]
    tpl = templates._TEMPLATES[tpl_key]

    if prepared_data is not None:
        if not mother or draft or not isinstance(prepared_data, dict):
            raise gate.GraphicsGateError("已编辑卡片只支持当前原意母稿的转载模式")
        _pg("沿用已编辑卡片渲染（模板：%s），不重新拆卡..." % tpl["label"])
        data = prepared_data
    else:
        _pg("LLM 卡片化素材（模板：%s）..." % tpl["label"])
        data = (_llm_cards(summary, draft, ai_config, theme, tpl_key, mother=mother)
                if mother else _llm_cards(summary, draft, ai_config, theme, tpl_key))
    model = "edited-draft" if prepared_data is not None else "local-template"
    if data:
        if prepared_data is None:
            model = ai_config.get("model") or "llm"
    else:
        _pg("未配置 AI，走本地模板兜底（成品需人工核对）...")
        data = _fallback_cards(summary, draft)

    def checked_cards(candidate):
        cards = candidate.get("cards") or []
        if prepared_data is not None and (not cards or not candidate.get("copy_text")):
            raise gate.GraphicsGateError("已编辑草稿缺卡片或配文，不能渲染")
        raw_counts = [(len(c.get("items") or []), len(c.get("steps") or []))
                      for c in cards if isinstance(c, dict)]
        # 渲染前清洗：剔除「素材未提」「请人工核对」等编辑向元语言（成品图直接面向读者）
        cards = ir.sanitize_cards(cards)
        if model != "local-template":
            clean_counts = [(len(c.get("items") or []), len(c.get("steps") or []))
                            for c in cards if isinstance(c, dict)]
            if raw_counts != clean_counts:
                raise gate.GraphicsGateError("卡片清洗发现内容被删除；原始结果未出图，请回到拆稿核对缺口")
        if len(cards) > MAX_IMAGES:
            raise gate.GraphicsGateError(
                "卡片 %d 张，超过图片消息 %d 张上限；原始卡片未裁剪，请调整后重试"
                % (len(cards), MAX_IMAGES))
        if len(cards) + len(shots) > MAX_IMAGES:
            raise gate.GraphicsGateError("卡片与截图合计 %d 张，超过平台 %d 张上限；请拆成两组"
                                         % (len(cards) + len(shots), MAX_IMAGES))
        for i, card in enumerate(cards, 1):
            if (card.get("kind") or "list") == "list" and len(card.get("items") or []) > 5:
                raise gate.GraphicsGateError("第 %d 张列表卡有 %d 条，单卡最多 5 条；请拆卡"
                                             % (i, len(card.get("items") or [])))
            if card.get("kind") == "text" and len(card.get("steps") or []) > 4:
                raise gate.GraphicsGateError("第 %d 张步骤卡有 %d 步，单卡最多 4 步；请拆卡"
                                             % (i, len(card.get("steps") or [])))
        return cards

    cards = checked_cards(data)
    fidelity = {}
    if mother:
        from src import guide_mother
        check_plan = guide_mother.wechat_plan(cards, mother)
        publish_text = guide_mother.wechat_publish_fields(data)
        revision = guide_mother.audit_revision(mother, check_plan, publish_text, source_text)
        previous = prechecked_fidelity or {}
        if (previous.get("ready") and previous.get("source_revision") == mother["source_revision"]
                and previous.get("mother_revision") == mother["mother_revision"]
                and (previous.get("coverage") or {}).get("input_revision") == revision
                and (previous.get("semantic") or {}).get("input_revision") == revision):
            fidelity = previous
        elif prepared_data is not None:
            raise gate.GraphicsGateError("已编辑草稿的审核版本与渲染文字不一致，请重新核对")
        else:
            fidelity = guide_mother.audit_output(mother, check_plan, publish_text, ai_config,
                                                  source_text=source_text)
        semantic = fidelity["semantic"]
        source_review = any(issue.get("kind") == "source_review" for issue in fidelity["coverage"]["issues"])
        if (not fidelity["ready"] and prepared_data is None and evidence_sources and not source_review
                and ai_config.get("editorial") and (semantic.get("deferred") or semantic["status"] != "unknown")):
            from ...editorial_model import ground_wechat_cards

            _pg("按已定位原句重排卡片与配文，重新核对...")
            try:
                corrected = ground_wechat_cards(data, mother, evidence_sources)
                corrected_cards = checked_cards(corrected)
                corrected_fidelity = guide_mother.audit_output(
                    mother, guide_mother.wechat_plan(corrected_cards, mother),
                    guide_mother.wechat_publish_fields(corrected),
                    ai_config, source_text=source_text)
                if corrected_fidelity["ready"]:
                    data, cards, fidelity = corrected, corrected_cards, corrected_fidelity
            except gate.GraphicsGateError as exc:
                _pg("原句重排超出版面容量，保留原草稿：%s" % exc)
        if not fidelity["ready"]:
            draft_dir = OUTPUT_DIR / "_drafts" / str(summary_id)
            draft_dir.mkdir(parents=True, exist_ok=True)
            draft_key = hashlib.sha256(json.dumps(
                [mother["mother_revision"], cards, data.get("copy_text")],
                ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
            draft_file = draft_dir / (draft_key + ".json")
            draft_file.write_text(json.dumps({"cards": cards, "copy_text": data.get("copy_text") or "",
                                             "digest": data.get("digest") or "",
                                             "source_note": data.get("source_note") or "",
                                             "keyword_reply": data.get("keyword_reply") or "",
                                             "fidelity": fidelity},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
            raise gate.GraphicsGateError("公众号原意核对未通过：%s；卡片草稿已保留在 %s" %
                                         ("；".join(guide_mother.audit_problems(fidelity)[:8]), draft_file))
    # 文字攻略页机制断言闸：出图前对当前文本重判，hard 拒绝出图（样稿纪律落成代码）
    claims = _claims_gate(summary, cards)
    total = len(cards)
    _pg("渲染 %d 张卡片（模板：%s，画布 %dx%d）..." % (total, tpl["label"], CANVAS_W, CANVAS_H))
    if shots:
        _pg("用户上传的 %d 张真图已排进卡片主视觉..." % len(shots))
    images, used_assets, card_styles, icon_report, term_report = _render_cards(
        cards, axes, out_dir, "/media/wechat/%d" % summary_id,
        brand=(theme or "").strip()[:12],
        on_card=lambda i, n: _pg("渲染第 %d/%d 张卡片..." % (i, n), i, n))

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
        "title": (cards[0].get("title") or "") if mother and cards else summary.get("title") or "",
        "copy_text": data.get("copy_text") or "",
        "digest": data.get("digest") or "",
        "keyword_reply": data.get("keyword_reply") or "",
        "source_note": data.get("source_note") or "",
        "author_draft": draft,
        "images": images,
        # 真图进了哪几张卡：发布时能对上"卡片3 用的是我传的哪张图"，也方便回溯
        # 合并后从渲染循环的 used_assets（封面 + 各页横带）里数真图，语义不变
        "hero_photos": [p.name for p in shots],
        "photos_in_cards": len({Path(a).name for a in used_assets if a
                                and Path(a).name in {p.name for p in shots}}),
        "cards": cards,
        "model": model,
        "theme": (theme or "").strip(),
        "skin": axes["skin_key"],
        "template": tpl_key,
        "palette": axes["palette_key"],
        "font": axes["font_key"],
        "layout": axes["layout_key"],
        # 合并后与头条 manifest 同源的追溯字段：按卡模板/配色、图标核对、实体实名、用到的素材
        "card_styles": card_styles,
        "icons": icon_report,
        "terms": term_report,
        "assets": [Path(a).name for a in used_assets if a],
        "claims": claims,
        "ref_images": ref_images,
        "canvas": "%dx%d" % (CANVAS_W, CANVAS_H),
        "summary_type": summary.get("summary_type", ""),
        "checklist": list(_CHECKLIST),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if mother:
        manifest.update({"fidelity": fidelity, "source_revision": mother["source_revision"],
                         "mother_revision": mother["mother_revision"]})
    manifest["lint"] = lint_package(manifest, package_root)

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


# ── 公众号成稿（2026-10-26 策略：这一渠道交的是**一篇完整文章**）─────────────
# 为什么不把卡片包换个名字当文章：卡片的产物是 6-8 张图，而策略要求"没有合适的图片，
# 文章也要成立"。成稿单独落 `output/wechat/<整理稿id>/article.md`。
# **骨架不是我们自己定的**：照号主那篇真发布稿（output/文章/魔兽工具实测1-1105发布.md）钉，
# 因为下游两道门只认那几个字面量——`verify_draft.js`（~/Documents/自媒体/_content_factory/）
# 硬要 `## 正文`、`## 公众号摘要（120 字内，后台粘贴）`、`## 发布前自查清单`，
# 本仓库 `draft_audit.audit()` 也靠后两个标记识别"这是公众号发布稿"并豁免三行管线语法。
# 所以这些标记由**程序写死**，模型只填内容：格式漂移不该交给一次采样。
ARTICLE_FIXED_HEAD = "【标题候选】"
ARTICLE_VERSION_HEAD = "【版本口径】"
ARTICLE_BODY_HEAD = "## 正文"
ARTICLE_DIGEST_HEAD = "## 公众号摘要（120 字内，后台粘贴）"
ARTICLE_CHECK_HEAD = "## 发布前自查清单"
ARTICLE_MARKERS = (ARTICLE_FIXED_HEAD, ARTICLE_VERSION_HEAD, ARTICLE_BODY_HEAD,
                   ARTICLE_DIGEST_HEAD, ARTICLE_CHECK_HEAD)
# 整理稿那一层的记号不许进读者眼里（[已核] 是我们 10-26 加的溯源标注，不是文章语言）。
# **注意别把这行写宽**：【标题候选】与【版本口径】是发布稿自己的头部（号主那篇真稿就有），
# 判成泄漏会把每一篇合格成稿都打红——第一版就是这么错的，自检样本当场抓到。
ARTICLE_BRACKET_MARK = re.compile(r"\[(?:已核|他述|本站|待核)\]")
ARTICLE_SECTION_MARK = re.compile(r"【(?:正文|正文骨架|导语|适用范围|核心论点|关键数据|"
                                  r"金句摘录|风险核查|来源)】")
DIGEST_HARD_MAX = 120           # 微信摘要栏硬线（平台字段长度，卡片线同一口径）
ARTICLE_MIN_PARAS = 3           # 正文至少要分成几段（一篇到底不像文章）

# ── 发布边界（2026-10-27 97 号诊断 P0）──
# 「稿子长什么样」和「稿子里哪些话能发出去」是两件事。上一版把标题候选、版本口径、
# 发布状态、自查清单和正文放进同一个 md（这是机器门要求的，动不得），但预览和
# 「复制全文」也一锅端给操作者——97 号实测：预览首屏是【标题候选】，正文中段还留着
# `### 发布前还要回查的三件事`，照着"贴正文到信息来源那段"手动截也会把编辑待办发出去。
# 所以这里加一层**唯一**的拆分实现：谁要"读者看到的这份"，都调 publish_parts()，
# 前端不再自己切一遍（两份实现一定漂）。
ARTICLE_CHOSEN_RE = re.compile(r"^[ \t]*选定[：:]\s*(?:第\s*\d+\s*条[。.、]?\s*)?([^\n]*)$", re.M)
ARTICLE_IMG_TASK_RE = re.compile(r"^[ \t]*配图任务[：:]")
ARTICLE_IMG_SLOT_RE = re.compile(r"^[ \t]*图\s*\d+\s*[：:]")
ARTICLE_CHECK_ITEM_RE = re.compile(r"^[ \t]*-[ \t]*\[[ xX]?\][ \t]")
# 编辑待办的判据：句子在讲"**这篇稿子**发出去之前我们还要查什么"，不是读者视角的限制。
# 只认 `发稿前/发布前/出稿前/发表前`（**要求紧邻"前"**，所以"发布在12月10日前"这种
# 陈述平台时间的句子不算），后面 30 字内跟一个核查类动词。
# 不写宽：`我们还没核实，先别照着做` 是提示词明确允许的读者视角写法（97 号里就有四处），
# 把它当编辑待办拦下会让每一篇合格成稿都被判不能发——第一版判据就是这么错的。
EDITOR_TODO_RE = re.compile(r"(?:发稿|发布|出稿|发表)之?前[^\n]{0,30}?"
                            r"(核实|核过|核对|回查|复核|查清|确认|重算|检查|补)")

_ARTICLE_PROMPT = """你是公众号写手。把下面这份「整理稿」改写成**一篇能直接发的公众号文章**。
读者是准备进入或正在玩《魔兽世界》「无限」的中文玩家，他们要的是能照着做、能回查来源、说明适用版本。

本篇要解决的那一件事：__ASK__

写作纪律（违反任何一条，这篇就算废）：
- **只写上面那一件事**。整理稿里如果有别的主题（例如同时有团本预告、升级地下城、第三方观察），
  不写进这篇，留给另一次成稿。宁可短，不要把三个主题订成一篇合订表——读者带不走一条路线。
- 只用整理稿里的事实。**不许编**：整理稿没有的数字、日期、版本、游戏内体验一律不许出现。
- 整理稿里的溯源标注（[已核] [他述] [本站] [待核]）**必须换成读者能懂的话**，且这些方括号记号本身
  一个字都不许出现在文章里。对照着写：
  [已核]→「官方公告写明」「我们在游戏内查到」；[他述]→「据 XX 转述，我们未验证」；
  [本站]→「我们的判断是」；[待核]→ 要么删掉，要么明确写成"这一条我们还没核实，先别照着做"。
- **待核与转述的东西不许写成能照做的建议**（这是这条产线的硬闸）。
- 不许编亲历：没写过"我实测了三小时"这种话，除非整理稿里真有这么一件事。
- 不要煽情开头、不要"不为人知的故事""就在那一刻"这类套话，不要"首先/其次/总而言之"。
- 段落为手机屏幕写：一段 1-3 句，一段别超过 80 字；小标题用 `### `，至少分 4 个小节。
- 数字要带口径与时间（如"截至 10 月 26 日这份公告"）。表格能用就用 markdown 表格，但表格里的每一格都得是整理稿有的。
- 需要在文末配图的位置，单独一行写 `图N：这张图要展示什么`，紧接一行写 `配图任务：截哪一屏/用哪张图`；
  **没有合适的图就不要写图行**，文章照样要成立。
- **编辑待办不许出现在正文里**：`发稿前还要核实…`、`发布前回查三件事`、`- [ ] …` 这种"我们发出去
  之前还要做什么"的话是审稿区内容，只能写进 checklist；正文里给读者的限制只写读者视角的一句
  （如"这一条我们还没核实，先别照着做"）。
- 正文最后三节固定是：`### 一句我自己的话`（本站立场，一段）、`### 收尾`（下一篇预告或行动提示）、
  `### 信息来源`（逐条列作者/出处/日期，台账里有链接的**必须把链接带上**，让读者能直接点回原文）。

正文目标字数：__RANGE__ 字（不含图行与标记）。

只输出 JSON，不要解释：
{
  "titles": ["候选标题1", "候选标题2", "候选标题3", "候选标题4"],
  "chosen_index": 1,
  "version_note": "这一篇的数核到什么时间、哪一档版本、发前要重算哪几个",
  "body": "## 正文 之后的全部正文 markdown（含小节、图行、最后那三节），不要再写 ## 正文 这个标题",
  "digest": "120 字内的公众号摘要，给微信转发卡片用",
  "checklist": ["发布前必须逐条打勾的事，5-8 条", "..."]
}

素材台账（**类型是权威的**：写来源、写"据谁说的"时照这张表，
表里标"文章"的就绝不能写成"视频"——上一版成稿把两篇暴雪官方公告通篇叫成
"官方视频1/视频2"，读者按视频去找根本找不到出处，这类错机器查不出来）：
__LEDGER__

整理稿（中间产物，只作素材，别照抄它的结构、也别抄它的节名与编号措辞）：
__SOURCE__
"""


def _article_word_range():
    """正文字数区间从**排版机器门**现读，不抄进本仓库（抄一次就漂一次）。

    读不到就返回 None 并让调用方在 lint 里显式写"这一条没查"——
    静默用一个我编的数，比不查更糟。
    """
    path = os.path.expanduser("~/Documents/自媒体/_content_factory/verify_draft.js")
    try:
        with open(path, encoding="utf-8") as fh:
            js = fh.read()
    except Exception:
        return None
    m = re.search(r"range\s*=\s*light\s*\?\s*\[(\d+)\s*,\s*(\d+)\]\s*:\s*\[(\d+)\s*,\s*(\d+)\]", js)
    if not m:
        return None
    lo, hi = int(m.group(3)), int(m.group(4))      # 非轻量口径（完整一篇主稿）
    return (lo, hi)


def _build_article_prompt(summary, ask="", word_range=None, ledger=""):
    """拼成稿提示词。**切口为空直接拒**：上一版这里给了一句万金油默认
    （"把这份整理稿讲的那件事说清楚"），97 号就是空 ask 跑出来的——团本预告、九座地下城、
    九处未核实入口被订成一篇，标题还承诺"一次查清"。默认句等于没切口，不如拦下来。
    拦在这里（不是拦在调用方）：任何一条路径想生成成稿都得先答"这篇解决哪一件事"。"""
    ask = (ask or "").strip()
    if not ask:
        raise gate.GraphicsGateError("这篇要解决哪一件事没写。空着不让生成——"
                                     "没有单篇切口，模型会把整理稿里几个主题订成一篇合订表。")
    rng = ("%d-%d" % word_range) if word_range else "2500-4000（这一条以排版机器门为准）"
    return (_ARTICLE_PROMPT
            .replace("__ASK__", ask[:120])
            .replace("__RANGE__", rng)
            .replace("__LEDGER__", (ledger or "（没有台账：来源类型与日期一律写\"素材未记录\"，不要自己猜）").strip())
            .replace("__SOURCE__", (summary.get("content") or "")[:12000]))


def _llm_article(summary, ai_config, ask="", ledger=""):
    """整篇改写一次。没配 AI 直接拒——成稿没有"本地兜底"这种半成品，
    拿骨架冒充文章是这条线最贵的一次事故（9-30 那五张垃圾卡片）。

    客户端建法与卡片线逐字一致（是 `api_base` 不是 `base_url`、超时 300、重试 1）：
    第一版我自己写了 `ai_config.get("base_url")`，取不到就回落 api.openai.com，
    拿国内端的 key 去打 OpenAI 官方端点 → 401 Incorrect API key，白跑一趟（22:23 实测）。
    **不设 max_tokens**：这一档是思考型，实测一篇整理稿的 completion 要 28k token，
    卡到 8k 会把 JSON 截断成"模型没交回文章"。
    """
    if not ai_config or not ai_config.get("api_key"):
        raise gate.GraphicsGateError("没配 AI，成稿写不出来。整理稿那一版可以人工改写后再发。")
    try:
        import openai

        client = openai.OpenAI(
            api_key=ai_config["api_key"],
            base_url=ai_config.get("api_base") or "https://api.openai.com/v1",
            timeout=300, max_retries=1,
        )
        prompt = _build_article_prompt(summary, ask, _article_word_range(), ledger)
        resp = client.chat.completions.create(
            model=ai_config.get("model") or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            response_format={"type": "json_object"},
        )
        data = _parse_json(resp.choices[0].message.content)
    except gate.GraphicsGateError:
        raise
    except Exception as e:
        raise gate.GraphicsGateError("成稿模型调用失败（模型 %s）：%s"
                                     % (ai_config.get("model") or "?", gate.brief_error(e)))
    if not isinstance(data, dict) or not (data.get("body") or "").strip():
        raise gate.GraphicsGateError("模型没交回文章（body 为空或被截断），已拒绝成稿")
    return data


def assemble_article(data, summary):
    """把模型填的内容装进**程序写死的骨架**里。标记、顺序、前后节名都由这里定。"""
    titles = [str(t).strip() for t in (data.get("titles") or []) if str(t).strip()][:6]
    if not titles:
        titles = [(summary.get("title") or "本篇标题").strip()]
    try:
        chosen = int(data.get("chosen_index") or 1)
    except (TypeError, ValueError):
        chosen = 1
    chosen = min(max(chosen, 1), len(titles))
    body = (data.get("body") or "").strip()
    body = re.sub(r"^##\s*正文\s*", "", body)          # 模型自己写了 ## 正文 就剥掉，标记由程序加
    digest = re.sub(r"\s+", " ", str(data.get("digest") or "")).strip()
    checks = [str(c).strip() for c in (data.get("checklist") or []) if str(c).strip()]
    lines = [ARTICLE_FIXED_HEAD]
    lines += ["%d. %s" % (i, t) for i, t in enumerate(titles, 1)]
    lines += ["", "选定：第 %d 条。%s" % (chosen, titles[chosen - 1]), "",
              ARTICLE_VERSION_HEAD,
              (data.get("version_note") or "版本口径未写，发布前必须补：这一篇的数核到什么时候、哪一档版本。").strip(),
              "", "发布状态：待人工审（成稿由整理稿改写，数字与口径要逐条回原文核过才能改成可发）", "",
              ARTICLE_BODY_HEAD, "", body, "",
              ARTICLE_DIGEST_HEAD, "", (digest or "（摘要没生成，发布前必须补，微信转发卡片要用）"), "",
              ARTICLE_CHECK_HEAD, ""]
    lines += ["- [ ] %s" % c for c in checks] if checks else ["- [ ] 发布前逐条自查（清单没生成，需人工补）"]
    return "\n".join(lines) + "\n"


# 整理稿里这些节是"框架"不是主题，不能当切口候选
# 「发布前/发稿前/回查」也在里面：97 号整理稿有一节就叫「五、发布前必须确认的3个点」，
# 那是给编辑看的待办，不能反过来推荐成"这篇就写待办"。
_TOPIC_SKIP = ("适用范围", "信息来源", "一句我自己的话", "收尾", "导语", "核心论点",
               "关键数据", "金句摘录", "风险核查", "正文骨架", "标题候选", "来源", "版本口径",
               "发布前", "发稿前", "回查", "待办")


def topic_suggestions(summary):
    """从整理稿里**确定性地**给出单篇切口候选（不调模型、不花钱、不改文件）。

    为什么要它：97 号那篇是空 ask 跑出来的，三个主题订成一篇。要让人"确认一句切口"，
    得先把现成的摆在他面前点一下，而不是给他一个空输入框让他自己想措辞。
    取整理稿【正文】的小节标题，一节一条；他自己在输入框里写的算数，这些只是建议。
    """
    content = (summary or {}).get("content") or ""
    tags = _split_tags(content)
    body = tags.get("正文") or tags.get("正文骨架") or ""
    out = []
    for line in body.splitlines():
        s = line.strip()
        # 只取**小节标题行**：第一版没这条，整理稿正文里那句"《魔兽世界》无限服在经典地图…"
        # 也被当成主题，出来一条 40 字不像话的切口（而且它自己就带着 [已核]）。
        if not s.startswith("#"):
            continue
        t = re.sub(r"^#+\s*", "", s)
        t = re.sub(r"\[[^\]]{1,4}\]", "", t)              # 顺手剥掉标题里可能带的溯源标注
        t = re.sub(r"[：:]\s*$", "", t).strip()[:40]
        if not t or any(k in t for k in _TOPIC_SKIP):
            continue
        cand = "这篇只讲「%s」这一件事，整理稿里的其他主题都不写" % t
        if cand not in out:
            out.append(cand)
    if not out:
        title = (summary or {}).get("title") or ""
        if title.strip():
            out.append("这篇只回答一件事：%s" % title.strip()[:60])
    return out[:6]


def _md_section(md, head, *ends):
    """取 head 这一节的内容（切到最先碰到的任何一个 end 之前）；head 不在就返回空串。"""
    if head not in md:
        return ""
    rest = md.split(head, 1)[1]
    stop = len(rest)
    for e in ends:
        i = rest.find(e)
        if i >= 0:
            stop = min(stop, i)
    return rest[:stop].strip("\n")


def editor_todos(body):
    """扫一段正文，列出混在里面的**编辑待办**行。判据只有这一处实现。

    `publish_parts()` 用它，`draft_audit.audit()`（发布前体检）也用它——
    否则就成了"成稿窗说不能发、体检说可以发"，号主拿哪一份当真实？97 号实测就是这样：
    audit_draft.py 判「可以发」，而正文中段挂着 `### 发布前还要回查的三件事`。
    """
    out = []
    for line in (body or "").splitlines():
        t = line.strip()
        if not t:
            continue
        if ARTICLE_CHECK_ITEM_RE.match(t):
            out.append({"kind": "待办清单漏进正文", "text": t})
        elif EDITOR_TODO_RE.search(t):
            out.append({"kind": "编辑待办小节" if t.startswith("#") else "编辑待办句", "text": t})
    return out


def publish_parts(md):
    """把工作底稿切成「能发出去的」和「只能给编辑看的」两份。97 号诊断 P0 的修法。

    返回 {selected_title, body, digest, review, blocked, publishable, missing}：
      body    = `## 正文` 到摘要之间，**剥掉** `配图任务：` 这种给作者的任务说明
                （`图N：` 留着——那是插图位，后台就在这一行换成真图，不是编辑待办）；
      review  = 标题候选／选定／版本口径／发布状态 + 从正文剥出的配图任务 + 发布前自查清单，
                这一份永不进手机预览、永不进「复制正文」；
      blocked = 正文里混进的编辑待办（逐行带原文与类别）。非空即 publishable=False：
                预览与「复制正文」当场拒绝，并把这几行原样列给操作者，让他移进清单后再发。
    纯函数，不读盘、不调模型。前端每次按键 debounce 后调 `/api/wechat/article/parts`
    走的就是它，所以「预览看到的那份」和「复制到剪贴板的那份」必然同一来源——
    这条线不能再出现两份实现各切一遍（上一版的教训是"提示让你手动截、按钮却复制全文"）。
    """
    md = md or ""
    missing = [m for m in (ARTICLE_BODY_HEAD, ARTICLE_DIGEST_HEAD, ARTICLE_CHECK_HEAD)
               if m not in md]
    m = ARTICLE_CHOSEN_RE.search(md)
    title = (m.group(1) or "").strip() if m else ""
    if not title:
        missing.append("选定标题（那一行「选定：第 N 条。」）")

    raw_body = _md_section(md, ARTICLE_BODY_HEAD, ARTICLE_DIGEST_HEAD, ARTICLE_CHECK_HEAD)
    kept, tasks = [], []
    for line in raw_body.splitlines():
        t = line.strip()
        if t and ARTICLE_IMG_TASK_RE.match(line):
            tasks.append(t)                      # 任务说明留在审稿区，不贴给读者
            continue
        kept.append(line)
    body = "\n".join(kept).strip("\n")
    blocked = editor_todos(body)

    digest = _md_section(md, ARTICLE_DIGEST_HEAD, ARTICLE_CHECK_HEAD)
    head = (md.split(ARTICLE_BODY_HEAD, 1)[0] if ARTICLE_BODY_HEAD in md else md).strip()
    review = [head]
    if tasks:
        review.append("【配图任务（已从正文剥出，排版时对着它截屏，别贴进后台）】\n"
                      + "\n".join("- %s" % x for x in tasks))
    if blocked:
        review.append("【被判编辑待办的正文行（移进下面的清单，别留在正文）】\n"
                      + "\n".join("- %s｜%s" % (b["kind"], b["text"]) for b in blocked))
    check = _md_section(md, ARTICLE_CHECK_HEAD)
    review.append("%s\n%s" % (ARTICLE_CHECK_HEAD, check))
    return {
        "selected_title": title,
        "body": body,
        "digest": digest,
        "review": "\n\n".join(x for x in review if x),
        "blocked": blocked,
        "missing": missing,
        # 注意：这里**只判结构**（标记齐不齐、有没有编辑待办、标题正文在不在）。
        # "能不能发"归 article_verdict()——它还要并上 lint_article 的全部 error。
        "structure_ok": bool(title and body and not blocked and not missing),
    }


def article_verdict(md, data=None):
    """一份**最终判定**：拆分 + 全部机检 error → publishable。

    为什么单独有它（Codex 独立复验 P1）：`publish_parts()` 只看结构，在修订稿正文里插一个
    `[待核]`，它照样返回"可发"，而 `lint_article()` 同一刻报的是
    `error: 溯源标注漏进读者眼前`——绿条和机检说两句话，号主就会把带标注的稿子复制出去。
    现在绿条、复制许可、lint 面板读的都是这一份，前端不再自己拼第二个条件。
    调用方向单向：verdict → (publish_parts, lint_article)，`lint_article` 接现成的拆分结果，
    不让 publish_parts 反过来调 lint（那是递归）。
    """
    parts = publish_parts(md)
    issues = lint_article(md, data, parts=parts)
    errors = [i for i in issues if i.startswith("error")]
    return dict(parts, lint=issues, errors=errors,
                publishable=bool(parts["structure_ok"] and not errors))


def lint_article(md, data=None, parts=None):
    """成稿的格式与泄漏检查。前缀 error=不许发，warn=建议处理。

    `parts` 是给 `article_verdict()` 复用的现成拆分结果（它已经算过一遍），
    不传就自己算——单独体检一份 md 时也照样能调。
    """
    issues = []
    md = md or ""
    parts = parts if parts is not None else publish_parts(md)
    missing = [m for m in ARTICLE_MARKERS if m not in md]
    if missing:
        issues.append("error: 缺公众号发布稿的固定标记：%s（排版机器门与审核台都不认这篇）" % "、".join(missing))
    body = md.split(ARTICLE_BODY_HEAD, 1)[1] if ARTICLE_BODY_HEAD in md else ""
    body = body.split(ARTICLE_DIGEST_HEAD, 1)[0]
    # **豁免只给「发布前自查清单」那一节**：清单本来就要写"正文未出现[已核][他述][待核]"
    # 这种话，全文扫会把一条合格稿判成 error（22:41 实测复现：唯一命中就在清单那一行）。
    # 其余各处（标题候选／选定标题／版本口径／正文／摘要）都会进读者眼前，照样全扫。
    reader_visible = md.split(ARTICLE_CHECK_HEAD, 1)[0]
    bracket_leaks = sorted(set(ARTICLE_BRACKET_MARK.findall(reader_visible)))
    if bracket_leaks:
        issues.append("error: 溯源标注漏进读者眼前：%s（要换成「官方公告写明」「据其转述，我们未验证」"
                      "这类文章语言）" % "、".join(bracket_leaks[:8]))
    section_leaks = sorted(set(ARTICLE_SECTION_MARK.findall(md)))
    if section_leaks:
        issues.append("error: 整理稿的节名漏进发布稿：%s（发布稿不用那套【标签】结构）"
                      % "、".join(section_leaks[:8]))
    # 编辑待办拦在这里（不在复制按钮里拦）：这样生成时、保存时、机检时判的是同一条线，
    # 不会出现"lint 说没事、按钮却拒着不给复制"这种两头对不上。
    blocked = parts["blocked"]
    if blocked:
        issues.append("error: 正文里混进 %d 处编辑待办（%s）——公开正文只留读者视角的限制，"
                      "「发稿前我们还要查什么」写进下面的自查清单"
                      % (len(blocked), "、".join(b["text"][:24] for b in blocked[:3])))
    if body.count("### ") < 4:
        issues.append("warn: 正文只有 %d 个小节（手机上一片到底的文章没人读完）" % body.count("### "))
    # "一屏灰墙"查的是**散文段落**：编号来源清单（1. …｜2. …）整块算下来 359 字，
    # 第一版把它也判成超长段落——手机上它是三行各带序号的短块，根本不是灰墙。
    # 判据要按行的形态判，别按块的字数一刀切（错判的 lint 会把人训练成"报警不用看"）。
    long_paras = [p for p in body.split("\n\n")
                  if p.strip() and not re.match(r"^(?:\d+[\.、]|[-*]\s|#|图\d+[：:]|配图任务|\|)", p.strip())
                  and len(re.sub(r"\s", "", p)) > 200]
    if long_paras:
        issues.append("warn: 有 %d 个段落超过 200 字没分段（手机上是一整屏灰墙）" % len(long_paras))
    if "### 信息来源" not in body:
        issues.append("error: 没有「信息来源」那一节——这条线的硬要求：读者要能回查出处")
    for word in ("首先", "其次", "总而言之", "综上所述", "不为人知"):
        if word in body:
            issues.append("warn: 出现套话「%s」" % word)
    m = re.search(re.escape(ARTICLE_DIGEST_HEAD) + r"\s*\n+(.+?)\n\n## ", md, re.S)
    digest = re.sub(r"\s+", "", (m.group(1) if m else ""))
    if digest and len(digest) > DIGEST_HARD_MAX:
        issues.append("error: 摘要 %d 字，超微信摘要栏 %d 字硬线" % (len(digest), DIGEST_HARD_MAX))
    rng = _article_word_range()
    body_text = re.sub(r"^\s*(图\d+：|配图任务：|##|---)", "", body, flags=re.M)
    body_text = re.sub(r"[*#>\-\s|]", "", body_text)
    if rng:
        lo, hi = rng
        if len(body_text) < lo:
            issues.append("warn: 正文 %d 字，低于机器门下限 %d（这篇会被判字数不足）" % (len(body_text), lo))
        elif len(body_text) > hi:
            issues.append("warn: 正文 %d 字，超过机器门上限 %d" % (len(body_text), hi))
    else:
        issues.append("info: 没读到排版机器门的字数区间（verify_draft.js 不在位），**这一条本轮没查**")
    return issues


def article_path(summary_id):
    return OUTPUT_DIR / str(int(summary_id)) / "article.md"


def read_article(summary_id):
    """读回成稿（没有则 None）。列表与详情页都从这里取，不复制第二份状态。"""
    p = article_path(summary_id)
    if not p.exists():
        return None
    meta = {}
    jp = p.parent / "article.json"
    if jp.exists():
        try:
            meta = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            meta = {"error": "article.json 读不出来（成稿正文仍可读）"}
    meta.setdefault("md", p.read_text(encoding="utf-8"))
    meta.setdefault("path", str(p))
    # 拆分**现算**，不存第二份：存进 article.json 的话，人工改了 md 没重存就是旧拆分，
    # 预览又会和实际内容对不上（97 号那个 bug 的另一种长法）。
    meta["publish"] = article_verdict(meta["md"])
    # 顶层 lint 也换成这份现算结果：article.json 里存的是**生成当时**的判定，人工改过稿
    # 或本仓库改过判据之后它就是旧的。上一版顶层给旧的那份、publish 给新的，
    # 同一个成稿窗顶部只显一条旧 warning、红条却报编辑待办 error（Codex R2 新发现 2）。
    meta["lint"] = meta["publish"]["lint"]
    return meta


def generate_article(summary, ai_config, ask="", progress_cb=None, ledger=""):
    """整理稿 → 一篇公众号发布稿（整篇改写，一次模型调用），落盘并返回元数据。

    `ask` 是号主在弹窗里写的"这篇要解决哪一件事"；留空则由提示词按整理稿自行判断。
    `ledger` 是调用方（web/app.py）用 `content_store.source_ledger()` 生成的**素材台账**：
    类型（文章/视频）与发布日由它给，成稿层不再自己猜。没传也照样跑，但提示词会明确要求
    写"素材未记录"而不是编一个日期。
    与卡片线共用同一个门禁 `assert_draft_generatable`：规则版骨架整理稿到这一步照样被拦，
    不许拿骨架改出一篇看着能发的文章。
    """
    gate.assert_draft_generatable(summary, "公众号成稿")
    summary_id = int(summary["id"])
    if progress_cb:
        progress_cb("整篇改写中（一次模型调用，思考型模型通常要几分钟）...")
    data = _llm_article(summary, ai_config, ask=ask, ledger=ledger)
    md = assemble_article(data, summary)
    verdict = article_verdict(md, data)
    issues = verdict["lint"]
    out_dir = OUTPUT_DIR / str(summary_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 旧稿先存档再覆盖：这一篇可能已经被人工改过（97 号那篇真稿就是），
    # 静默覆盖掉别人手改的文字，比多留一个文件贵得多。
    prev_name = ""
    if (out_dir / "article.md").exists():
        prev = out_dir / ("article.prev-%s.md" % datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copyfile(out_dir / "article.md", prev)
        prev_name = prev.name
    (out_dir / "article.md").write_text(md, encoding="utf-8")
    titles = [str(t).strip() for t in (data.get("titles") or []) if str(t).strip()]
    chosen = 1
    try:
        chosen = min(max(int(data.get("chosen_index") or 1), 1), max(len(titles), 1))
    except (TypeError, ValueError):
        pass
    meta = {
        "id": summary_id,
        "title": titles[chosen - 1] if titles else (summary.get("title") or ""),
        "titles": titles,
        "digest": str(data.get("digest") or "").strip(),
        "checklist": [str(c).strip() for c in (data.get("checklist") or []) if str(c).strip()],
        "model": (ai_config or {}).get("model") or "",
        "ask": (ask or "").strip()[:120],
        "lint": issues,
        "publishable": verdict["publishable"],
        "prev_saved": prev_name,
        "chars": len(re.sub(r"\s", "", md)),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_summary_type": summary.get("summary_type", ""),
    }
    (out_dir / "article.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    # 同目录已有卡片包就把成稿标题挂上去，列表页能一眼看出"这篇有成稿"
    mf = out_dir / "manifest.json"
    if mf.exists():
        try:
            man = json.loads(mf.read_text(encoding="utf-8"))
            man["has_article"] = True
            man["article_created_at"] = meta["created_at"]
            mf.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    if progress_cb:
        errs = [i for i in issues if i.startswith("error")]
        progress_cb("✅ 成稿已写好（%d 字）%s" % (meta["chars"],
                    "，⚠️ %d 条 error 要先处理" % len(errs) if errs else ""))
    return meta


# ── manifest 包管理薄封装（绑定公众号 OUTPUT_DIR，形状与头条线一致，Phase 3 接 API）──
def _has_article(summary_id):
    return (OUTPUT_DIR / str(int(summary_id)) / "article.md").exists()


def list_packages():
    """扫描 output/wechat/*/manifest.json，倒序返回列表（每条标有没有成稿）。"""
    items = package.list_packages(OUTPUT_DIR)
    for it in items:
        try:
            it["has_article"] = _has_article(it.get("id") or it.get("summary_id"))
        except (TypeError, ValueError):
            it["has_article"] = False
    return items


def get_package(summary_id):
    """读单个包 manifest；不存在返回 None。成稿单独按文件在不在标，
    不靠 manifest 里那个字段——它是生成成稿时才回填的，先出卡片再写文章时容易漏。"""
    man = package.get_package(summary_id, OUTPUT_DIR)
    if man is not None:
        man["has_article"] = _has_article(summary_id)
    return man


def save_article(summary_id, md):
    """人工改完存回去：**按改后这份重判格式**，不保留旧判定。
    只改文件与元数据里的字数/lint/标题，不动卡片包（那是另一条链的产物）。"""
    md = md or ""
    p = article_path(summary_id)
    if not p.exists():
        raise gate.GraphicsGateError("这一篇还没有成稿（article.md 不在），先点「生成公众号图文」")
    verdict = article_verdict(md)
    issues = verdict["lint"]
    p.write_text(md, encoding="utf-8")
    jp = p.parent / "article.json"
    meta = {}
    if jp.exists():
        try:
            meta = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    m = re.search(r"选定：第\s*(\d+)\s*条。?\s*([^\n]*)", md)
    if m and (m.group(2) or "").strip():
        meta["title"] = m.group(2).strip()
    meta.update({"id": int(summary_id), "lint": issues,
                 "publishable": verdict["publishable"],
                 "chars": len(re.sub(r"\s", "", md)),
                 "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    jp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    meta["md"] = md
    meta["path"] = str(p)
    meta["publish"] = verdict
    return meta


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
