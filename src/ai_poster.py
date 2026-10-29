# -*- coding: utf-8 -*-
"""AI 海报出图链路：整理稿 → 整张海报由出图模型画出来（含中文文字）。

与 src/graphics 的 HTML 模板渲染是两条独立产线：这里不出网页、不排 CSS，
文字由出图模型直接画进像素。三段式，缺一不可：

  1) LLM 拆解：把整理稿压成「主标题 + 副标题 + N 张卡片 + 尾注」文案计划，
     并过字数硬门禁。总字数是实测出来的安全线，不是拍脑袋：带完整画面计划时
     62 字连出 2 张全对、79 字那张直接丢掉主标题，所以线钉在 64，不靠 prompt 祈祷。
     拆解同时产出**画面计划**（主视觉 + 每条卡片该画什么）与**风格预设键**：
     只有文字没有画面的计划，模型只能画空框，图面占比实测接近 0。
  2) 出图：百炼原生 multimodal-generation 端点。prompt 把「要逐字渲染的文字」和
     「只用来构图的画面描述」分成两块写，否则模型会把画面描述也当文字画上去。
     返回的是 OSS 临时链接，必须当场下载，否则过期拿不到图。
  3) 质检两问，各司其职：OCR 回读只答「字对不对」，视觉评审只答「好不好看」。
     少任何一个都发现不了问题：字全对但一张空文字框的图照样能发出去。

Python 3.9 兼容：不用 match / X|Y 语法。
"""
import base64
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

import requests

from . import fact_gate
from .graphics import gate as draft_gate
from .graphics import package as pkg_store
from .graphics.channels.toutiao import _parse_json

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "poster"

# 出图这一档 2026-10-29 号主撤了。依据：近三周 21 份海报 manifest 的 `image_model` 全为空，
# 实际流程一直是「素材真帧 / 豆包手工出图 + 程序排真字」，API 出图一次没跑过。
# 原来这里写死一个默认模型名——档位撤掉后那个默认值还留着，等于任何一次"没有底图"的出图
# 都会拿一个没人认领的模型去扣 0.5 元/张（而且它走 requests 直连百炼，不在额度记账那一层里）。
# 所以现在没配模型就是不出图，并且要抛一句说得清出路的话（见 generate_image 开头）。


def _image_model(ai_config):
    """出图模型名：只认配置里显式写的 `AI_MODEL_IMAGE`，没写就是不出图（不再自带默认值）。"""
    return ((ai_config or {}).get("image_model") or "").strip()


# 转录型视觉模型（qwen-vl-ocr 只回坐标框，不能用）。
# 2026-10-11 实测：配置里那个中转端点的 models.list 有 175 个模型，但已经没有
# qwen3-vl-flash 了，调用直接 404「Model not exist」——而这三道看图检查（回读校验、
# 视觉评审、底图体检）都把异常吞成 skipped，界面上只写着「跳过」，从 10-09 晚上换端点
# 起就一直在静默失败。换成端点上确实在的 qwen3-vl-plus；以后端点再改名，
# 配置里加一行 AI_MODEL_VISION 就能顶掉这里，不用改代码。
OCR_MODEL = "qwen3-vl-plus"

# 端点尺寸上限 1664x1664：传 1024*1792 会直接 InvalidParameter
SIZES = {"9:16": "936*1664", "3:4": "1248*1664", "1:1": "1664*1664"}
RATIO_LABEL = {"9:16": "竖版 9:16", "3:4": "竖版 3:4", "1:1": "方形 1:1"}

# 字数纪律（超限即拒绝出图，不是警告）
# 80 字是"prompt 里几乎没有画面要求"时测出来的线。加了 scene / 卡片配图之后重测：
# 62 字连出 2 张逐字全对（评审 23/25、画面占比 65%），79 字那一张直接把主标题和三条
# 说明整段丢掉。模型要分算力去画画面，字数安全线只能跟着往下挪。
MAX_TOTAL_CHARS = 64
TITLE_MAX, SUBTITLE_MAX, FOOTER_MAX = 12, 10, 14
# 单条说明的长度比总字数更容易出错：实测 4 张卡片、说明 7-8 字时连出 2 张逐字全对；
# 同样 64 字总量但说明写到 11-13 字，那三行直接退化成"伪汉字"乱码。所以把单条压到 10 字。
CARD_TITLE_MAX, CARD_DESC_MAX, CARD_MIN, CARD_MAX = 6, 10, 3, 5
SPINE_MAX = 24              # 主线一句话：只在 UI 里给人看，不进图、不计字数

# 两种排版模式。typeset＝模型只画没有字的底图、中文由程序用真字体排上去（默认）；
# model＝整张交给模型画字，留着做对比用。
# 为什么默认换成前者：模型同时画插画和写中文时两件事抢同一份算力，实测 63 字那张
# 插画最好但把"比上赛季"多写一个"个"、"10月8开启"改写成"10月8日"、赛字笔画画错、
# 连提示词里的「」框都画上去了。字交给程序排，这五类问题一次性消失，字数上限也
# 从"正确性问题"降级成"版面放得下"，信息量才能做回竞品那种密度。
TEXT_MODES = ("typeset", "model")
DEFAULT_TEXT_MODE = "typeset"
# 程序排字这套预算只受版面约束：200 字在 9:16 上实测放得下（5 张卡片、说明 40 字）
TS_LIMITS = {"total": 200, "title": 16, "subtitle": 22, "cardT": 8, "cardD": 40, "footer": 24}
MD_LIMITS = {"total": MAX_TOTAL_CHARS, "title": TITLE_MAX, "subtitle": SUBTITLE_MAX,
             "cardT": CARD_TITLE_MAX, "cardD": CARD_DESC_MAX, "footer": FOOTER_MAX}


def limits_for(text_mode):
    """按"出法"给字数预算。

    ⚠ 这里以前写的是 `TS_LIMITS if text_mode == "typeset" else MD_LIMITS`——
    任何拼错的值（2026-10-14 实测：传了个 'guide' 进去）都会**悄悄落到"直出成品"那一档**，
    卡片标题从 8 字变 6 字、说明从 40 字变 10 字，拆出来的东西薄一半，
    而调用方完全看不出来。改成反过来：只有明确 'model' 才用 MD，其余一律按默认的排版档走。
    """
    return dict(MD_LIMITS if text_mode == "model" else TS_LIMITS)


# ── 上图文字的"过程词"闸 ────────────────────────────────────────────────
# 号主 2026-10-13 点名：「访谈称」「口播」这类字眼不是要给玩家看的东西，
# 而且他说清楚了一件事——别只改这两个词，**同一类**都得挡掉。所以我们加工素材
# 留下的痕迹（谁说的、从哪来的、内部文件名）一律不许印到图上。
# 比 `draft_audit.LEAK_PATTERNS` 窄：那边管正文别漏流水线标记，这里只管"会印在图上的那几行"；
# 也**故意不收**「未核实／素材没提／疑似」——攻略页的警示块靠这些说清边界，那是诚实标注，不是过程词。
META_WORDS = [
    (r"口播", "口播"),
    (r"访谈(?:称|中|说|提到)", "访谈称／访谈中"),
    (r"据称|据报道|爆料称", "据称／爆料称"),
    (r"素材(?:里|中|说|讲|提到|出现)", "素材里说"),
    (r"(?:原)?视频(?:里|中)(?:说|提到|讲|看)", "视频里提到"),
    (r"转写|ASR|整合稿|整理稿", "转写／整合稿"),
    (r"[Uu][Pp]主|主播|博主", "UP主／主播／博主"),
    (r"评论区|弹幕", "评论区／弹幕"),
    (r"画面(?:里|中)看?到", "画面里看到"),
    (r"整理(?:自|归纳)", "整理自"),
]
META_NAMES = "／".join(n for _p, n in META_WORDS)
# 提示词里那一条由这份名单生成——名单与提示词各写一份，迟早对不上
META_RULE = ("- **会印到图上的那几行字里不许出现过程词**（%s）：这些是我们加工素材留下的痕迹，"
             "玩家不需要知道这句话是谁说的、从哪儿来的。要写成玩家视角的事实句——"
             "「访谈称平均满级耗时约175小时」→「平均满级约 175 小时」；"
             "「口播说团本推迟」→「团本大概率推迟 2 到 3 个 CD」。"
             "出处与口径写进 publish.source_note（发布页那份给人看的），别印到图上。" % META_NAMES)


def ink_texts(page):
    """这一页**会印到图上**的文字（尾注除外：那句是合规声明，本来就得写「整理自…」）。"""
    out = [("主标题", page.get("title")), ("副标题", page.get("subtitle")),
           ("栏目名", page.get("kicker"))]
    for i, c in enumerate(page.get("cards") or [], 1):
        out += [("卡片%d标题" % i, c.get("t")), ("卡片%d说明" % i, c.get("d"))]
    bn = page.get("bignum")
    if isinstance(bn, dict):
        out += [("大字数字", bn.get("v")), ("大字标签", bn.get("label")), ("大字注释", bn.get("note"))]
    for i, c in enumerate(page.get("contrast") or [], 1):
        out += [("对照%d" % i, c.get("t")), ("对照%d" % i, c.get("d"))]
    wn = page.get("warn")
    if isinstance(wn, dict):
        out += [("警示标题", wn.get("t")), ("警示正文", wn.get("d"))]
    ak = page.get("ask")
    if isinstance(ak, dict):
        out += [("互动提问", ak.get("q"))]
        out += [("互动选项%d" % i, o) for i, o in enumerate(ak.get("options") or [], 1)]
    return [(k, str(v or "").strip()) for k, v in out if str(v or "").strip()]


def meta_hits(page):
    """这一页的上图文字里有哪些过程词，返回给人看的句子列表（不抛错、不改字）。"""
    hits = []
    for label, txt in ink_texts(page):
        for pat, name in META_WORDS:
            if re.search(pat, txt):
                hits.append("%s里有「%s」：%s" % (label, name, txt[:36]))
                break
    return hits
# 画面计划（scene / 每条卡片的 v）与 spine 同理：只喂给出图模型，不落成图面文字，
# 所以不占合计字数预算。没有这一栏，模型只会画一排空文字框——那是我们的图"土"的第一原因。
SCENE_MAX, CARD_VIS_MAX = 48, 30
VISUAL_MIN = 2              # 至少几条卡片给了画面描述，少于这个数提示人工补

# 一次出多张让人挑：AI 出图有方差，并行采样比反复重出省时间也省钱
MAX_COPIES = 3
COPIES_NOTE = "模型限流每分钟 2 张，出 3 张约需 2-3 分钟"

# 发布文案（不进图、只给发布页用）的独立字数档：不受图面合计字数预算约束
COPY_MIN, COPY_MAX = 120, 260
DIGEST_MAX = 120
KEYWORD_MAX, SOURCE_MAX = 30, 50

# ── 发布标题（2026-10-16 号主点名）──────────────────────────────────────
# 他要的是"拆文案那一次就把头条标题+配文、小红书标题+配文一起给我"，理由是
# **标题和配文直接决定点击率**——图面主标题（title，≤16 字）是给卡片看的，
# 从来不是平台标题：manifest.title 一直拿的是图面主标题，所以这一档以前是空的。
# 字数线抄自权威源（都在本仓库外，改口径先改那两份、再回来改这里）：
#   头条：~/Documents/自媒体/头条出稿规范.md:49-53「每条含标点 ≤30 字（信息流显示上限，
#         超出截断丢关键信息）」「双标题 A/B 位必须不同题型」「标题里的日期、数字、
#         时间线必须与正文完全一致」；media-workbench/server.js:411 另给一条取向
#         「18-25 字、数字+具体+利益点、不堆惊叹号」→ 写进提示词当偏好，不做硬闸。
#   小红书：media-workbench 的 06-xiaohongshu-integrated-publishing-v1.yml:801
#         「5 个 ≤20 字标题」「话题标签 5-8 个」→ 这里取 3 条候选（号主一个人发，
#         5 条挑不过来）+ 标签 5-8 个。
TT_TITLE_MAX, TT_TITLE_N = 30, 2
XHS_TITLE_MAX, XHS_TITLE_N = 20, 3
XHS_COPY_MAX = 300          # 小红书正文比头条配文长一点：要点用文字带全 + 一句"我"
XHS_TAG_MIN, XHS_TAG_MAX = 5, 8
# 小红书正文/标题的自证禁词——只挡"看着像机器在代发"这一类，**故意不收**全部过程词
# （头条配文与 source_note 本来就要写「整理自」，那是合规声明不是自证）。
# 与 META_WORDS 是两份名单、口径不同，别合并。
XHS_BAN = [(r"回复\s*[^\s，。]{1,8}\s*(?:获取|领取|领|拿|要)", "回复 XX 获取…（自动回复引导＝托管特征）"),
           (r"本文由\s*AI|AI\s*辅助整理|由\s*AI\s*(?:生成|整理|归纳)", "本文由 AI…（正文自证）"),
           (r"侵权|免责声明", "尾注免责句（该走发布页的【笔记内容声明】勾选）")]


# 出图后视觉评审的五项 rubric（详见 review_image）：回读管"字对不对"，这里管"好不好看"
REVIEW_ITEMS = [("overflow", "文字完整"), ("hierarchy", "层级清晰"), ("visual", "画面占比"),
                ("contrast", "可读性"), ("polish", "精致度")]
REVIEW_PASS = 18            # 五项满分 25，低于这个分不建议直接发

# 合规尾注：图是 AI 画的、文字是 AI 写的，转述声明必须随配文一起发
COMPLIANCE_NOTE = "本文由 AI 辅助整理归纳，内容源自公开分享，观点与结论归原作者，如有侵权请联系删除。"

# 海报版发布检查清单：平台侧动作 AI 替不了，逐条人工过。
# 每条带适用渠道标签（"all"＝三渠道都要；"wechat:article"＝公众号·文章配图形态独有），
# 由 checklist_for() 过滤——一次发布只该看到那条线要过的门（2026-10-13 渠道分叉落地）。
_CHECKLIST_ITEMS = [
    ("all", "图内文字已核对：程序排字模式由代码落字、不会错，只需看版面提示有没有截断；"
            "模型画字模式必须 manifest.verify.ok = true，未通过别发"),
    ("all", "视觉评审已过（manifest.review）：低于 %d 分或画面占比低于 40%% 时先换候选/换风格重出，别硬发" % REVIEW_PASS),
    ("all", "数字/版本/价格类信息人工核对一手来源（ASR 与模型都可能出错）"),
    ("all", "尺寸与平台匹配：9:16 小红书/抖音图文、3:4 公众号图片消息、1:1 通用封面"),
    ("all", "发布后 72 小时回填数据，便于回看哪种风格预设、哪种画面占比有效"),
    (("toutiao", "wechat"), "「文章设置」勾选「内容由 AI 生成」（2025-09-01 起强制，平台不自动标注）"),
    (("wechat:newspic",), "图片消息无「声明原创」入口（平台未开放图片原创），勿找该按钮、勿群发后补标"),
    (("toutiao", "wechat:newspic"), "配文保留文末合规尾注，勿删；正文禁外链"),
    (("toutiao",), "头条后台两个标题位各填一条（A/B 不同题型，每条含标点 ≤%d 字）；"
                   "别拿图面主标题当发布标题，那条是给卡片看的、只有 %d 字" % (TT_TITLE_MAX, TS_LIMITS["title"])),
    (("xhs",), "小红书标题从三条候选里挑一条（≤%d 字），正文用「小红书那份」——"
               "把头条配文连尾注一起粘过去，就是 10-10 那次被判托管的样子" % XHS_TITLE_MAX),
    # ── 小红书单独四查（2026-10-13 复盘：10-10 那条《魔兽无限服国服金团随便开》
    #    10-11 23:20 被判「笔记存在利用AI托管进行发文/互动的内容」，处置=不可被他人查看）。
    #    平台 2026-03-10《打击AI托管运营账号治理公告》+ 04-27《AI内容治理规则》：
    #    禁"模拟真人发文/互动"、禁"套模板批量生产同质化内容"，AI 参与要走**发布时的官方声明勾选**。
    #    我们那篇正好同时踩了两句自己写的字：正文末尾「本文由 AI 辅助整理归纳」＝自证，
    #    「回复 金团 获取政策要点」＝引导关键词自动回复（就是"托管互动"的字面形态）。
    #    渠道分叉落地后这四条只在 xhs 档出现，原文的【发小红书】前缀随之去掉。
    ("xhs", "正文**不要**写「本文由 AI 辅助整理归纳」那句尾注——那是自证托管（这档配文本来就不带尾注）；"
            "改用发布页【笔记内容声明】→勾选「笔记含AI合成内容」（公众号那条才保留尾注，两家口径相反）"),
    ("xhs", "删掉「回复 XX 获取…」这类引导自动回复；评论/私信也别挂机器人（这档发布载荷已不带关键词回复）"),
    ("xhs", "至少一张自己游戏内的真截图 + 一句你本人真发生过的事"
            "（不许编：伪造经历与截图是「AI 造假」那一档，处置比限流重）"),
    ("xhs", "别拿同一套模板批量发——「套用模板批量生产同质化内容」是平台明写的红线"),
    # ── 公众号「文章配图」形态独有（2026-10-13 拍板：形态 B 仅公众号可用）
    (("wechat:article",), "头图用第 1 页，攻略页组按页顺插文内；正文用整理稿，别照抄图上的字"),
    (("wechat:article",), "合规尾注不放配文、放正文文末，勿删；正文禁外链"),
    (("wechat:article",), "文末钩子与关键词自动回复已配置（回复关键词见发布载荷）"),
]

PUBLISH_CHANNELS = {"toutiao": "头条", "xhs": "小红书", "wechat": "公众号"}
WECHAT_FORMS = {"newspic": "图片消息", "article": "文章配图"}


def compose_source_note(ctx, fallback=""):
    """来源那一行由**代码**拼，不让模型写日期。

    2026-10-29 反例：提示词里的 `{{DATE}}` 取的是生成当天，素材是 10-24 的第三方口播，
    成品却写「数据口径截至10月29日」——读者一句"你这啥时候的信息"就问穿了。
    三个日期各说各的：**素材发布日**（连来源号与一手/第三方一起）、**本稿生成日**、
    **事实核验情况**（没核就明写没核，不许写成"已核到某日"）。
    """
    mats = (ctx or {}).get("materials") or []
    if not mats:
        return fallback                      # 没接台账的老路径：不自己编日期，也不假装核过
    bits = []
    for m in mats[:3]:
        who = str(m.get("author") or "未知作者")
        when = str(m.get("published_at") or "")[:10] or "日期未知"
        level = "官方一手公告" if m.get("first_party") else (
            "第三方口播" if m.get("is_video") else "第三方转述")
        bits.append("%s·%s·%s" % (who, level, when))
    if len(mats) > 3:
        bits.append("等 %d 条" % len(mats))
    line = "来源：" + "；".join(bits)
    open_r = (ctx or {}).get("open_risks") or []
    resolved = (ctx or {}).get("resolved") or []
    if open_r:
        line += "｜未逐项核验 %d 条（%s）" % (
            len(open_r), "、".join(str(r)[:14] for r in open_r[:2]))
    if resolved:
        line += "｜已核 %d 条：%s" % (
            len(resolved), "；".join("%s（出处：%s）" % (str(r.get("raw"))[:14],
                                                  str(r.get("source"))[:28]) for r in resolved[:2]))
    else:
        line += "｜事实核验：无（不得写成已核到某日）"
    return line + "｜本稿生成于 %s" % ((ctx or {}).get("generated_at") or "日期未知")


def checklist_for(publish_channel, form=""):
    """按渠道（与公众号形态）过滤发布检查清单，返回该次发布要逐条人工过的文本列表。

    条目标签三种写法："all"＝三渠道都要；渠道名字面（"toutiao"/"xhs"/"wechat"）＝该渠道
    两种形态都要；"wechat:newspic"/"wechat:article"＝仅公众号那一个形态。"""
    ch = publish_channel if publish_channel in PUBLISH_CHANNELS else "toutiao"
    if ch == "wechat" and form not in WECHAT_FORMS:
        form = "newspic"
    formtag = ("wechat:%s" % form) if ch == "wechat" else ""
    items = []
    for chans, text in _CHECKLIST_ITEMS:
        if "all" in chans or ch in chans or (formtag and formtag in chans):
            items.append(text)
    return items


# ── 风格预设库 ──
# 原来只让模型自由发挥一句"深色影院质感"，所以十次出图十个样、每次都像临时拼的。
# 这里把风格钉成四套固定配方：每套都写死画风词 + 配色 + 卡片材质 + 主视觉该往哪走，
# 拆解时让模型从里挑一个（可人工改），出图 prompt 直接取用配方原文。
# key 会落进 manifest.plan.style_key，改名等于改历史数据，新增可以、删除要三思。
STYLE_PRESETS = {
    "game_epic": {
        "label": "游戏史诗风（写实插画 · 暗色氛围）",
        "when": "游戏攻略、版本解读、装备/数值向",
        "scene": "占画面五成以上的写实游戏主视觉：一个角色或一处场景在单一强光源下，背景压暗只做氛围",
        "mood": "写实厚涂游戏原画风，电影级布光，深灰蓝夜色背景 #161b26 配单一暖金强光源 #c8963c，整体像魔兽资料集封面，严禁扁平卡通矢量风、严禁纯白底、严禁左右对称的商务模板感",
        "card_visual": "每张卡片左端配一个道具特写小图（武器/徽章/地图/药水瓶），不许留空框",
        "style": ("写实厚涂游戏原画风中文信息图，电影级布光，深灰蓝夜色背景 #161b26 配单一暖金强光源 #c8963c，"
                  "画面上半部是一整幅占版面五成的游戏场景或角色插画，卡片做成带烧蚀边缘的黑铁石板，"
                  "石板之间用金色雕花细线分隔，主标题用厚重中文黑体配金色描边但笔画必须清晰可读，"
                  "严禁扁平卡通矢量风、严禁纯白底、严禁左右对称的商务模板感"),
    },
    "tool_review": {
        "label": "工具测评风（界面感 · 冷色科技）",
        "when": "AI 工具实测、软件对比、效率清单",
        "scene": "占画面五成的主视觉：一台斜视角屏幕里跑出界面光效，周围浮着几个发光的接口节点",
        "mood": "高级科技感插画，深蓝紫渐变底 #0e1230 到 #1a1040，点缀青蓝高光 #4fd1ff 与紫色 #8b5cf6，斜视角悬浮屏幕、发光数据流、玻璃质感体块，整体像高端发布会主视觉，严禁廉价蓝白渐变、严禁真人照片拼贴",
        "card_visual": "每张卡片配一枚线性发光图标（终端/齿轮/闪电/盾牌），卡片做成悬浮的毛玻璃面板",
        "style": ("高级科技感中文信息图，深蓝紫渐变底 #0e1230 到 #1a1040，点缀青蓝高光 #4fd1ff 与紫色 #8b5cf6，"
                  "画面上半部是一幅占版面五成的产品界面氛围插画（斜视角悬浮屏幕、发光数据流、玻璃质感面板），"
                  "卡片做成半透明毛玻璃圆角面板带细霓虹描边和内发光，主标题是粗中文无衬线字体带冷光渐变，"
                  "整体像高端发布会主视觉，严禁廉价蓝白渐变、严禁真人照片拼贴、严禁纯文字排版"),
    },
    "checklist": {
        "label": "清单笔记风（纸质手账 · 暖色亲和）",
        "when": "经验总结、避坑清单、教程要点",
        "scene": "占画面五成的主视觉：一张摊开的笔记本或桌面俯拍插画，笔、贴纸、咖啡杯散在其间",
        "mood": "精致手账插画风，暖米色纸纹背景 #f5efe3，主色墨黑 #2b2b2b 配砖红 #d9694a 与苔绿 #6b8e5a，俯拍桌面、纸胶带与手撕边质感，点缀手绘下划线，严禁冷色科技风、严禁纯黑底",
        "card_visual": "每条卡片前面画一个手绘勾选框或小标记，配一枚相关物件插画（便签/尺子/警示牌）",
        "style": ("精致手账风中文信息图，暖米色纸纹背景 #f5efe3，主色墨黑 #2b2b2b 配砖红 #d9694a 与苔绿 #6b8e5a，"
                  "画面上半部是一幅占版面五成的俯拍桌面插画（摊开的笔记本、笔、贴纸、咖啡杯），"
                  "卡片做成贴在纸上的小便签卡带轻微投影和手撕边，标题用清晰规整的中文粗黑体（严禁手写体），"
                  "点缀手绘下划线和小图标，严禁冷色科技风、严禁玻璃质感、严禁纯黑底"),
    },
    "minimal": {
        "label": "极简杂志风（大留白 · 强对比）",
        "when": "观点短文、单点结论、封面级标题党",
        "scene": "占画面五成的主视觉：一个被高度风格化的单一主体（一扇门/一枚棋子/一台机器），大面积纯色包围",
        "mood": "高端杂志插画版式，大面积留白配单一强调色（正红 #d92b2b 或深墨绿），高度风格化的单主体构图，几何感强、栅格严整，严禁装饰堆砌、严禁渐变、严禁花哨边框",
        "card_visual": "每张卡片只放一个大号几何符号或单色剪影，宁少勿多，靠体量不靠细节",
        "style": ("高端杂志版式中文信息图，大面积留白配单一强调色（正红 #d92b2b 或深墨绿），"
                  "画面上半部是一幅占版面五成的高度风格化单主体插画（剪影/几何构成/强轮廓），"
                  "卡片是极细分割线隔开的纯排版区块，靠字号体量差拉层级（主标题极大、说明极小），"
                  "主标题用超粗中文无衬线，留白充足、栅格严整，严禁装饰堆砌、严禁渐变、严禁花哨边框"),
    },
    # 攻略页专用：画面是素材真帧，不是模型插画，所以这套配方描述的是"版面底色与字色"，
    # 只有当有人拿它去出插画海报时 mood/style 才真正喂给画图模型。
    "guide_ink": {
        "label": "攻略页（深墨绿 · 真帧当证据）",
        "when": "多页图文攻略：一页一个论点，配素材真帧 + 大字数字（图注不上图，只留档）",
        "scene": "不画主视觉插画：版面靠真帧截图当证据，底色是安静的深墨绿渐变",
        "mood": "深墨绿底 #182721 到 #0a1410 的安静渐变，米金标题 #f0d9a0，砖红警示块 #5f2a24，"
                "正文米白 #e6e6df，图注与脚注灰绿 #93a497；版面像一本摊开的攻略手册，"
                "严禁炫光科技感的界面面板、严禁花哨边框、严禁把画面塞满装饰",
        "card_visual": "每条论点前面是米金色小标题，正文一行行排下来，不画图标也不画面板",
        "style": ("深墨绿底中文攻略页，米金色超粗中文黑体标题配砖红警示块，正文米白、图注灰绿，"
                  "版面是一页一个论点的攻略手册结构，靠字号体量差拉层级，"
                  "严禁科技风玻璃面板、严禁花哨边框、严禁把画面塞满装饰"),
    },
    "guide_paper": {
        "label": "攻略页（米白纸感 · 真帧当证据）",
        "when": "和「攻略页」同一套结构，换浅底深字；连着做几篇时轮换着用，别十个号一个脸",
        "scene": "不画主视觉插画：版面靠真帧截图当证据，底色是安静的米白纸感渐变",
        "mood": "米白底 #f1e9d8 到 #ddcfb4 的纸感渐变，深墨标题 #2f2517，暗金强调 #8a6a2f，"
                "正文深褐 #33291a，图注与脚注灰褐 #7c705a，警示块浅砖红 #f2d8cd；"
                "版面像一本摊开的攻略手册，严禁炫光科技感、严禁花哨边框、严禁把画面塞满装饰",
        "card_visual": "每条论点前面是暗金色小标题，正文一行行排下来，不画图标也不画面板",
        "style": ("米白纸感中文攻略页，深墨超粗中文黑体标题配暗金强调与浅砖红警示块，"
                  "正文深褐、图注灰褐，版面是一页一个论点的攻略手册结构，靠字号体量差拉层级，"
                  "严禁科技风玻璃面板、严禁花哨边框、严禁把画面塞满装饰"),
    },
    "guide_night": {
        "label": "攻略页（暗蓝夜色 · 真帧当证据）",
        "when": "同一套结构的冷色版；副本/团本/夜间题材用它，跟米白那套错开",
        "scene": "不画主视觉插画：版面靠真帧截图当证据，底色是安静的暗蓝夜色渐变",
        "mood": "暗蓝底 #16202f 到 #080d16 的夜色渐变，浅蓝白标题 #dfe9f5，冷蓝强调 #9fd0f0，"
                "正文浅灰蓝 #d5dde8，图注与脚注蓝灰 #8494a8，警示块暗酒红 #5a2f33；"
                "版面像一本摊开的攻略手册，严禁霓虹赛博朋克、严禁花哨边框、严禁把画面塞满装饰",
        "card_visual": "每条论点前面是冷蓝色小标题，正文一行行排下来，不画图标也不画面板",
        "style": ("暗蓝夜色中文攻略页，浅蓝白超粗中文黑体标题配冷蓝强调与暗酒红警示块，"
                  "正文浅灰蓝、图注蓝灰，版面是一页一个论点的攻略手册结构，靠字号体量差拉层级，"
                  "严禁霓虹赛博朋克、严禁花哨边框、严禁把画面塞满装饰"),
    },
    "auto": {
        "label": "自由发挥（沿用拆解写的那句风格）",
        "when": "上面四套都不合题材时",
        "scene": "",
        "card_visual": "",
        "style": "",
    },
}
# 攻略页那一档排在四套插画风格之后：它不出 AI 底图，选它等于选"用素材真帧当证据"。
# 三套攻略页皮肤同一套结构、不同脸——跨素材轮换用，免得十篇长一个样
STYLE_ORDER = ["game_epic", "tool_review", "checklist", "minimal",
               "guide_ink", "guide_paper", "guide_night", "auto"]
GUIDE_STYLES = ("guide_ink", "guide_paper", "guide_night")


def guide_style_for(seed):
    """攻略页三套皮肤按素材轮换：同一套结构、换脸。

    为什么：默认全给 `guide_ink` 的话，做十篇就是十个一模一样的深墨绿，
    平台那条红线「套用模板批量生产同质化内容」看着说的就是这个（2026-10-13 复盘）。
    seed 用整理稿 id——稳定可复现，同一条重出还是那张脸，不会一次一个样让人对不上账。
    """
    try:
        n = int(seed or 0)
    except (TypeError, ValueError):
        n = 0
    return GUIDE_STYLES[abs(n) % len(GUIDE_STYLES)]


DEFAULT_STYLE_KEY = "tool_review"
# 字体这条不写进各套配方、单独挂：风格词里全是"质感/雕花/氛围"，模型很容易顺手把中文
# 写成手写体或艺术字，而实测手写体的逐字准确率明显更低（主标题整块消失过一次）。
TYPE_RULE = ("图上的中文一律用清晰规整的中文黑体或无衬线字体，字号够大、笔画干净，"
             "禁用手写体、草书、艺术字变形与笔画粘连")


def style_choices(guide_only=True):
    """给前端下拉用：默认**只出攻略页那三套皮肤**。

    为什么收窄（号主 2026-10-16 原话）：「这里的选项只需要攻略页的几个，之前老的模板都不要了，
    而且默认进来就是选择攻略页」。攻略页这一档用素材真帧、出图 0 元，四套插画风格那一路
    每张要 0.5 元，摆在同一个下拉里就是给他一个会花钱的错选项。
    **没有删任何东西**：`STYLE_PRESETS` 里四套原样留着、他自己收编的 ★ 模板还在
    `config/poster_templates.json` 里，只是不再进这个下拉；要全名单传 `guide_only=False`
    （模板管理页/回归脚本走那条）。
    """
    out = []
    keys = list(GUIDE_STYLES) if guide_only else list(STYLE_ORDER)
    for k in keys:
        p = STYLE_PRESETS.get(k)
        if not p:
            continue
        out.append({"key": k, "label": p["label"], "when": p["when"],
                    "preset": (p["style"] or "（沿用手写的风格描述）"),
                    "scene": p["scene"] or "", "builtin": True, "has_base": False})
    parents = _user_preset_parents()
    for k, p in _user_presets().items():
        if guide_only and parents.get(k) not in GUIDE_STYLES:
            # 收编自插画风格的 ★ 模板不进这个下拉（号主 10-16：老的模板都不要了）。
            # 判据是它的父风格，不是它自己长什么样——不然前端那个 chip 点了以后
            # 下拉里根本没有这一项，界面显示"攻略页"、计划里却是老模板，就是说假话。
            continue
        out.append({"key": k, "label": "★ " + p["label"], "when": p["when"],
                    "preset": p["mood"] or "（沿用父风格的画风口）",
                    "scene": p["scene"], "builtin": False, "has_base": _has_base(k)})
    return out


def _user_preset_parents():
    """{模板键: 父风格键}。读一次 poster_templates，读不到就空（不影响内置那几套）。"""
    try:
        from . import poster_templates
        return {it["key"]: (it.get("parent") or "") for it in poster_templates.all_templates()
                if it.get("key")}
    except Exception:
        return {}


def _has_base(key):
    try:
        from . import poster_templates
        return bool(poster_templates.base_image_path(key))
    except Exception:
        return False


def _user_presets():
    """用户模板（收编过的成品）。整条链路挂掉也不能影响内置四套，所以吞异常。"""
    try:
        from . import poster_templates
        return {it["key"]: poster_templates.preset(it["key"])
                for it in poster_templates.all_templates() if it.get("key")}
    except Exception:
        return {}


def _preset(plan):
    k = (plan or {}).get("style_key") or ""
    if k in STYLE_PRESETS:
        return STYLE_PRESETS[k]
    return _user_presets().get(k) or {}


def resolve_style_text(plan):
    """出图用的风格原文：命中预设就用配方原文（钉死、不许漂移），auto/未知键才回落到自由填写。

    两条路都要补一句字体硬约束。实测手账那版把标题写成"手写感中文粗体"后，主标题整块
    消失、卡片标题被复制成两个"重点"——出图模型画规整黑体的逐字准确率明显高于手写体。
    """
    p = _preset(plan)
    base = p.get("style") or (plan or {}).get("style") or "深色质感背景，金色细边框，信息卡片纵向排列"
    return base + "。" + TYPE_RULE


def style_plan_prompt(frames=None):
    """拼进拆解 prompt 的预设清单：让模型自己挑风格键，而不是自己编一句风格。

    给了真帧清单（`frames` 非空）就只列攻略页那三套皮：界面上的风格下拉本来就只给攻略页
    （任务 #93 收窄过），提示词却把插画档全列出来，模型会挑 `game_epic` 这种要 AI 画底图的风格
    ——2026-10-22 实测就是这么把"0 元真帧"又变回"2 元插画"的：同一份提示词里前后打架。
    """
    if frames:
        lines = ["可选风格（这一档画面用素材真帧，只许从这三套攻略页皮里选）："]
        for k in GUIDE_STYLES:
            p = STYLE_PRESETS[k]
            lines.append('  - "%s"：%s（适用：%s）' % (k, p["label"], p["when"]))
        lines.append("不许选插画类风格（game_epic / tool_review / checklist / minimal 等），"
                     "那些要 AI 画底图，会把真帧盖掉。")
        return "\n".join(lines)
    lines = []
    for k in STYLE_ORDER:
        p = STYLE_PRESETS[k]
        lines.append('  - "%s"：%s（适用：%s）' % (k, p["label"], p["when"]))
    for k, p in _user_presets().items():
        lines.append('  - "%s"：%s（适用：%s；这是用户自己收编的模板，选它就用它记下的主视觉方向）'
                     % (k, p["label"], p["when"]))
    return "\n".join(lines)


# ── 页数与布局：推荐规则做成数据 ──
# 为什么不写死在提示词的散文里：① 加一类题材（数码、汽车）只改这张表，不碰代码；
# ② 后端回落判据和喂给模型的规则必须是同一份，否则模型按 A 说、代码按 B 改，
#    用户在界面上看到的推荐就跟实际排出来的对不上。
PAGE_RULES = [
    {"max_points": 5, "pages": 1,
     "why": "5 条以内一页读完就够，拆两页反而没人翻第二页"},
    {"max_points": 9, "pages": 2,
     "why": "6-9 条同类要点：首页抓眼，次页一口气说完"},
    {"max_points": 13, "pages": 3,
     "why": "10 条以上，或「有什么 + 怎么办」两类都有：封面 / 要点 / 收尾"},
    {"max_points": 19, "pages": 4,
     "why": "14-19 条按每页 4 条切：封面 / 两页要点 / 收尾"},
    {"max_points": 999, "pages": 5,
     "why": "20 条以上就一页一个论点，别往一页里塞。多一页不加钱——"
             "画面用素材真帧（出图档 2026-10-29 撤了，模型画底图那条 0.5 元/页已停用）"},
]

# 题材信号 → 风格 + 布局（无图 / 有图两套）+ 页数倾向。加新题材往这里追加一行就够。
THEME_RULES = [
    {"signals": "游戏 / 版本 / 装备 / 坐骑 / 数值 / 攻略", "style": "game_epic",
     "no_photo": "top_art", "photo": "full_bleed 或 left_text",
     "pages": "坐骑、装备逐个列，6 条以上就 2-3 页"},
    {"signals": "AI 工具 / 技术 / 实测 / 软件对比", "style": "tool_review",
     "no_photo": "compare", "photo": "left_text（截图放右边）",
     "pages": "「怎么用 + 值不值」两类都有 → 2 页"},
    {"signals": "清单 / 避坑 / 教程要点 / 经验", "style": "checklist",
     "no_photo": "list", "photo": "top_art",
     "pages": "5 条内 1 页；8 条以上按每页 4 条切"},
    {"signals": "单观点 / 结论先行 / 态度", "style": "minimal",
     "no_photo": "big_type", "photo": "full_bleed",
     "pages": "就 1 页，拆了力量就散了"},
]


def suggest_pages(n_points):
    """按要点数给一个建议页数 + 一句话理由（回落和提示词共用这一份规则）。"""
    for r in PAGE_RULES:
        if n_points <= r["max_points"]:
            return r["pages"], r["why"]
    return PAGE_MAX, PAGE_RULES[-1]["why"]


def layout_choices_prompt(has_photo):
    """那十种构图的一段说明（整组拆解与单页重拆共用，别各写一份）。"""
    from . import poster_typeset as ts
    lines = ["可选布局（只能填键名；标了「要有真图」的，没图就别选）："]
    for k in ts.LAYOUT_ORDER:
        v = ts.LAYOUTS[k]
        lines.append('  - "%s"：%s，放 %d-%d 条%s。适用：%s'
                     % (k, v["label"], v["min_cards"], v["max_cards"],
                        "（要有真图）" if v.get("needs_photo") else "", v.get("when", "")))
    lines.append("")
    return lines


GUIDE_LAYOUT_LOCK = (
    '**这一组走攻略页那一档：每页的 layout_key 一律填 "guide"**，'
    "画面用「铁律六」列出的素材真帧，不叫 AI 画底图。"
    "那十种构图是给没有真帧的稿子准备的，这次不用挑，也别按"
    "「有没有上传配图」判断这一组能不能用图——**这些真帧就算真图**。")


def layout_plan_prompt(has_photo, page_hint=0, has_frames=False):
    """拼进拆解 prompt 的那段"拆几页 + 每页什么布局"规则。

    里面的 __TOTAL__ 交给调用方那串 replace 一起替换，跟主模板用同一个预算数。

    page_hint 是用户在界面上已经定了的页数（他嫌 2 页太挤、手动加到 3 页，再点「换一套」
    就是想要 3 页的结构）。这时候页数不再由模型判断，剩下的只让它决定怎么分配与选布局。

    has_frames（有可用真帧）时**不再列那十种构图**：以前列了，模型就按"无真图"去推插画布局，
    `why` 里写"无真图先用 top_art 起幅"，photos 一张都不给（2026-10-22 实测两次都是这样）。
    这一档的布局只有一个答案，直接说死，省得它替我们做决定。
    """
    lines = []
    if has_frames:
        lines.append(GUIDE_LAYOUT_LOCK)
        lines.append("")
    else:
        lines = layout_choices_prompt(has_photo)
    if page_hint and page_hint > 0:
        lines.append("**页数已由用户定死：这一组必须正好 %d 页**（page_count 填 %d，"
                     "pages 数组给满 %d 项，每页都要有自己的 role、主标题和至少 1 条要点）。"
                     "不要再自行改成别的页数；要点不够也要按内容重心分成 %d 个角度，"
                     "每页合计 ≤__TOTAL__ 字。" % (page_hint, page_hint, page_hint, page_hint))
        lines.append("")
    else:
        lines.append("页数规则（一页最多 5 条、每页合计 ≤__TOTAL__ 字；**装不下就翻页，不许为了塞进一页砍要点**）：")
        for r in PAGE_RULES:
            lines.append("  - %s → %d 页：%s"
                         % ("≤%d 条" % r["max_points"] if r["max_points"] < 999 else "更多",
                            r["pages"], r["why"]))
        lines.append("  - 每页给一个 role（两个字：封面 / 要点 / 收藏 / 行动 / 收尾…），"
                     "第 1 页负责抓眼，末页负责结论或入口。")
        lines.append("")
    lines.append("题材 → 风格 + 布局 + 页数倾向（本篇贴哪一行就照那行来；用户定了页数就以用户为准）：")
    for r in THEME_RULES:
        lines.append("  - %s → 风格 %s；%s；页数：%s"
                     % (r["signals"], r["style"],
                        ("有真图首选 " + r["photo"]) if has_photo else ("无真图首选 " + r["no_photo"]),
                        r["pages"]))
    lines.append("")
    lines.append("本篇%s真图。" % ("已经上传了" if has_photo else "没有上传"))
    return "\n".join(lines)


def _page_layout(layout_key, style_key, has_photo, idx):
    """这一页到底用哪个布局：模型给的键合法就用；非法、或"没图却选了要图的"就回落并留话。"""
    from . import poster_typeset as ts
    raw = str(layout_key or "").strip()
    key = ts.LAYOUT_ALIAS.get(raw, raw)
    if key not in ts.LAYOUTS:
        dft = ts.resolve_layout(None, ts.skin_for(style_key))
        note = ("第 %d 页：模型给的布局「%s」不认识，已按这套风格默认「%s」"
                % (idx, raw, ts.LAYOUTS[dft]["label"])) if raw else ""
        return dft, note
    if style_key in GUIDE_STYLES and key != "guide":
        # 号主 2026-10-16：AI 海报这一档只留攻略页。皮肤既然是攻略页三套之一，版面就必须是
        # 攻略页那一套结构——模型回了别的键（或旧计划里带回来的）一律收回。
        # 不这么做就是"界面选着攻略页、后端按插画档叫 AI 画底图"，0 元变 0.5 元/张。
        return "guide", ("第 %d 页：这套皮肤是攻略页，版面已收回到「攻略页」"
                         "（原来给的是「%s」）" % (idx, ts.LAYOUTS[key]["label"]))
    if ts.LAYOUTS[key].get("needs_photo") and not has_photo:
        return ts.DEFAULT_LAYOUT, ("第 %d 页：「%s」要有图才站得住，这篇没图，已改用「%s」"
                                   % (idx, ts.LAYOUTS[key]["label"],
                                      ts.LAYOUTS[ts.DEFAULT_LAYOUT]["label"]))
    return key, ""


class PosterError(Exception):
    """链路可预期失败（AI 未配置 / 字数越界 / 出图端点报错），消息可直接展示给用户。"""


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _api_root(api_base):
    """百炼兼容模式与原生服务端点同域不同路径，这里从 api_base 还原出根。"""
    b = (api_base or "").rstrip("/")
    for suffix in ("/compatible-mode/v1", "/compatible-mode"):
        if b.endswith(suffix):
            return b[:-len(suffix)]
    return b


def _require_ai(ai_config):
    if not ai_config or not ai_config.get("api_key") or ai_config.get("method") == "skip":
        raise PosterError("未配置 AI 接口，无法出图（工作台 AI 设置里填 API Key）")
    return ai_config["api_key"], (ai_config.get("api_base") or ""), (ai_config.get("model") or "")


# ── 1. LLM 拆解：整理稿 → 文案计划 ──
_PLAN_PROMPT = """你是自媒体图文编辑。下面这份素材多半**已经是别人总结过的内容**（口播稿或攻略要点
——这一句只是说素材的来历，这种词不许出现在图上的文字里），
你的任务不是把它切得更碎，而是挑出一条最有价值的主线，并把推理链留在图上。

本篇主题：__THEME__

铁律一·内容纪律（转载整理模式）：
- 只能用素材里出现过的事实与数字，严禁编造、严禁夸大；素材没写的不要写。
- 全程转述口吻，禁用第一人称（我实测 / 我用过）。
__META__
- 素材可能是从中间断开的残句（缺标点、句子被截断）。遇到这种情况要**跨段把一句话说完整**
  再落卡片，绝不允许一张卡片只承接半句话。

铁律二·结构纪律（这条决定海报是"一串散点"还是"一个论证"）：
- 卡片之间必须构成一条递进主线，按「结论 → 依据/算账 → 坑或风险 → 怎么选」这类顺序排；
  每张卡片都要能回答"所以呢"，删掉任意一张主线就断。
- 严禁按素材原文顺序逐段摘要；严禁两张卡片讲同一件事。
- 优先保留**带推理链的信息**（为什么值、怎么算出来的、什么条件才成立），
  砍掉单纯罗列的名词、重复修饰和与主题无关的边角料。

铁律三·篇幅纪律：
主标题 ≤__TITLE__ 字；副标题 ≤__SUB__ 字；每张卡片标题 ≤__CT__ 字、说明 ≤__CD__ 字；尾注 ≤__FOOT__ 字；
**每页**卡片 3-5 张；**每页**全部文字合计 ≤__TOTAL__ 字。
__DENSITY__
中文标点计入字数；不要出现 markdown 符号；
**卡片小标题别全是四字格**（"装备取舍""路线规划""收益结算"这样三连排开）：长短混着写，
该说满的就说满。换三条素材还是那四个字，读者一眼认出模板——平台判"批量同质化"看的也是这个。
**价格、天数、日期一律保持素材里的阿拉伯数字**（写 388 不写三八八），中文数字反而更占字数也更难读。

铁律四·画面纪律（这一条决定海报是"一张插画"还是"一屏文字框"，与字数同样重要）：
- scene：先想一个能代表本篇的**主视觉画面**（≤48 字），要求是一个看得见的场景或物件，
  例如"一个法师在熔岩城墙头举起发光的卷轴"，不要写"科技感""高级感"这类形容词。
- 每张卡片必须给 v（≤24 字）：这一条**该画成什么**——具体角色、道具、动作或场景，
  要能被画出来。"重要""高效""方便"这种没有形体的词一律不合格；讲数值就画仪表/天平/钱，
  讲风险就画断裂/警示，讲选择就画岔路/对比。
- scene 与 v 只喂给画图模型看，不会被写成图上的字，所以不占上面那条合计预算，但同样要贴着素材事实。
- 从下面这几套风格预设里挑**一个最贴本篇题材的**填进 style_key（只能填键名，不要自己编风格描述）：
__STYLES__
__STYLE_FIXED__

铁律五·分页与构图纪律（这条决定"要点装不下时怎么办"——翻页，不是砍掉）：
**先数一遍素材里独立成立的要点有几条**：超过 5 条就必须拆成多页（每页 3-5 条），
严禁只交 5 条然后把剩下的丢掉；被分到后面几页的要点仍然要出现在图上，也要在配文里带上。
__LAYOUT__
输出 JSON（键固定，值为字符串或对象数组）：
{
  "title": "主标题",
  "subtitle": "副标题",
  "cards": [{"t": "卡片标题", "d": "卡片说明", "v": "这一条画什么"}],
  "footer": "合规转述声明，例：内容整理自公开分享",
  "style_key": "上面风格预设之一的键名",
  "style": "仅当 style_key 为 auto 时填写一句自由风格描述，否则留空字符串",
  "scene": "主视觉画面描述",
  "spine": "一句话说清这条主线（≤24字，不画进海报，只用来检查结构是否成链）",
  "page_count": 1,
  "why": "一句话说明为什么拆这么多页、每页为什么用这个布局（≤60字，给人看的）",
  "pages": [
    {"role": "封面", "layout_key": "上面布局键名之一", "title": "这页主标题",
     "subtitle": "这页副标题，可留空", "scene": "这页要画成什么场景",
     "cards": [{"t": "卡片标题", "d": "卡片说明", "v": "这一条画什么"}]__GUIDE_SHAPE__}
  ],
  "publish": {
    "copy_text": "发布页配文，120-260 字纯文本：钩子开头 1-2 句 + 要点 3-5 条（每条一行，用「· 」开头）+ 收尾引导 1 句。不要 markdown 符号。",
    "digest": "摘要，≤120 字，单独成段能读懂，不写「见图」这类字样",
    "keyword_reply": "关注后自动回复引导语，≤30 字（例：回复 清单 领取完整列表）；素材里没有可推的关键词就留空字符串",
    "source_note": "来源说明一句话，≤50 字，只写内容从哪来（谁/哪类来源），**不要写日期**——素材发布日与核验状态由系统在出包时补，写在这里会被覆盖",
    "tt_titles": ["头条标题 A 位", "头条标题 B 位"],
    "xhs_titles": ["小红书标题一", "小红书标题二", "小红书标题三"],
    "xhs_copy": "小红书正文：第一句给结论，然后 3-5 条要点，最后一句收尾",
    "xhs_tags": ["#游戏名", "#版本名"]
  }
}

关于 pages：按上面页数规则判定要拆几页后，**把每页写进 pages[]**（一页一个对象，形状同上），
同时顶层的 title / subtitle / cards / scene 仍然填**第 1 页**的内容（老代码读这几个键）。
只拆一页时 pages 可以省略。footer 整组共用一句，只填顶层就行。
配文（publish.copy_text）要把被分到后面几页的要点也用文字带上，读者不看第二页也不漏信息。

关于 publish 里的标题与小红书正文（**都不画进海报，但直接决定人家点不点进来**，值得给字数）：
- `tt_titles` 两条头条标题：每条含标点 **≤30 字**（信息流显示上限，超出被截断就丢关键信息）；
  两条必须是**不同题型**（一条给结论、一条给疑问或对比可以，同一句式换个词不行）；
  取向是 18-25 字、带一个具体数字或时间、不堆感叹号。
- `xhs_titles` 三条小红书标题：每条 **≤20 字**，说人话，直接给玩家能拿到的那个东西。
- **标题里的数字、日期、版本名必须在卡片文案或配文里也出现**。素材里没有的数不许只出现在标题里，
  那是标题党；标题写「175 小时」，正文里就得有「175 小时」。
- `xhs_copy` 小红书正文：第一句就给结论，要点 3-5 条（每条一行、用「· 」开头），收尾一句。
  可以写"我"，但**只能用在整理动作上**（"这份清单按公告逐条对过"），
  **不许编游戏内亲测**（"我昨天打了团本"——素材里没有你发生过的事就不写）。
  不许出现「回复 XX 获取」「本文由 AI 辅助整理」「侵权／免责」这类句子：
  那正是 2026-10-10 那篇被判「AI 托管发文」的旁证，AI 参与走发布页的官方声明勾选。
- `xhs_tags` 5-8 个话题标签，**只用这一篇讲的那个游戏**的名字／版本名／团本名；
  素材里顺带提到的别的游戏（拿来对比的）不许当标签——那会把笔记推给不相干的人，别造词。

spine、scene、每张卡片的 v、style_key 与 publish 里的文字都不会画进海报，所以不占合计字数预算；
但同样只能用素材里的事实。

__RISKS__
素材：
__CONTENT__"""


def risks_prompt_block(summary):
    """把整理稿【风险核查】列成"这些说法不许当卖点"塞进拆解提示词。

    以前这一节只是随素材原文一起进去、没有任何一句提到它，于是模型照常用它当事实
    （87 的"46亿"整理稿明明标了疑似口误，成品照样拿它当第二页卖点）。
    这里只做一件事：**把上游已经标过的疑点显式列出来、说不许升格**。
    判断真假仍然归人，不是叫模型自己再评一遍。
    """
    items = fact_gate.risk_items(summary.get("content") or "")
    if not items:
        return ("整理稿没有【风险核查】这一节。凡是没有出处的数字、版本号和效果承诺，"
                "一律不要写成确定语气；确实要提就写清「以游戏内实际为准」。\n\n")
    lines = "\n".join("- %s" % it["raw"] for it in items[:12])
    return ("【整理稿已经标为待核的说法——这些不许当卖点、不许进标题、不许放大字】\n"
            "%s\n"
            "写法要求：① 上面点到的数字/名词，要么整条不写，要么只作为「作者称…」的转述，"
            "并明确写「需按游戏内实际核实」；② 不许把它们变成行动指令（"
            "「按 5 年不变值准备」这种）；③ 不许在配文里说「已核对 / 逐项核对过」——"
            "列出核实点不等于完成核实；④ 上游写了「没有实测数据 / 不承诺」的，"
            "成品里就不许出现「毛边全消」「一定提升」这类效果保证。\n\n"
            % lines)


def _clip(text, limit):
    return re.sub(r"\s+", "", str(text or ""))[:limit]


def _clip_ws(text, limit):
    """发布文案用的裁切：折叠空白但保留单空格——图面字段可以删空格，
    「回复 Agent 领取」这类关键词删了空格就废了。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _title_list(raw, limit_n):
    """标题候选：去空、去重、只截条数，**不截长度**。

    为什么不裁长度：裁到 30 字会留下一句断头话，看着像合格的、实际发不出去。
    超长交给 lint_publish 报出来让人自己改——和「过程词闸只报不自动改写」同一条口径。
    """
    out = []
    for x in (raw if isinstance(raw, list) else []):
        t = _clip_ws(x, 60)
        if t and t not in out:
            out.append(t)
    return out[:limit_n]


def _tag_list(raw):
    out = []
    for x in (raw if isinstance(raw, list) else []):
        t = re.sub(r"\s+", "", str(x or "")).lstrip("#")[:12]
        if t and "#" + t not in out:
            out.append("#" + t)
    return out[:XHS_TAG_MAX]


def _clip_publish(raw, plan=None):
    """发布文案单独裁切：它不进图，所以不受图面合计字数预算约束。

    plan 传进来是为了让 lint_publish 能查"标题里的数字正文里有没有"这一条；
    校验结果挂在 publish.warnings 里随结果回前端——**拆解那一步就要看得见**，
    等出图完再看已经晚了（app.py 因此一行不用改，它原样把 publish 存进 status）。
    """
    raw = raw if isinstance(raw, dict) else {}
    # 配文/小红书正文**不再按长度硬切**（2026-10-21）：以前这里是 `[:COPY_MAX]`，
    # 实测 83/85/80 三条成品配文都正好 260 字并断在句中——83 停在「帧数提升幅」，
    # 被切掉的半句恰好是【风险核查】第 9 条"帧数提升幅度未给具体数值"，
    # 切完再接合规尾注，读者看见的就是一句半截话。超线交给 lint_publish 报出来让人自己删，
    # 与 `_title_list` 那句"裁到 30 字会留下一句断头话"是同一条口径。
    pub = {"copy_text": re.sub(r"[ \t]+", " ", str(raw.get("copy_text") or "")).strip(),
           "digest": _clip_ws(raw.get("digest"), DIGEST_MAX),
           "keyword_reply": _clip_ws(raw.get("keyword_reply"), KEYWORD_MAX),
           "source_note": _clip_ws(raw.get("source_note"), SOURCE_MAX),
           "tt_titles": _title_list(raw.get("tt_titles"), TT_TITLE_N),
           "xhs_titles": _title_list(raw.get("xhs_titles"), XHS_TITLE_N),
           "xhs_copy": re.sub(r"[ \t]+", " ", str(raw.get("xhs_copy") or "")).strip(),
           "xhs_tags": _tag_list(raw.get("xhs_tags"))}
    bad = lint_publish(pub, plan)
    cut = fact_gate.copy_is_cut(pub["copy_text"])
    if cut:
        bad.append("配文%s" % cut)
    cut_x = fact_gate.copy_is_cut(pub["xhs_copy"])
    if cut_x:
        bad.append("小红书正文%s" % cut_x)
    pub["warnings"] = bad
    return pub


def _publish_view(plan, pub):
    """把**实际要发出去**的发布字段并进一个只读视图给事实闸查（不改原 plan）。

    2026-10-21 Codex P2：闸口原来只读 `plan.publish`，而海报线的发布文案是独立字段 `pub`，
    调用时只额外塞了 `copy_text` 一项——待核的数写进 `tt_titles`／`xhs_copy`／`digest`
    这些同样会发出去的位置时，这一道根本查不到。合并视图让同一份判据覆盖全字段。
    """
    view = dict(plan or {})
    view["publish"] = pub or {}
    return view


def _body_pool(pub, plan):
    """标题里那些数字该去哪儿找：图面文案 + 两份配文 + 摘要，全算正文。"""
    bits = [pub.get("copy_text") or "", pub.get("xhs_copy") or "", pub.get("digest") or ""]
    if isinstance(plan, dict):
        for p in plan_pages(plan):
            for _k, v in ink_texts(p):
                bits.append(v or "")
            bits.append(p.get("footer") or "")
    return re.sub(r"\s+", "", " ".join(bits))


def lint_publish(pub, plan=None):
    """发布文案侧软校验：不阻断出图，只进 manifest.lint 提示人工补。

    标题这几条是**机器真能查的**，所以不靠模型自觉：字数线、A/B 重不重、
    标题里的数字正文有没有（《头条出稿规范》那句「标题里的日期、数字必须与正文完全一致」
    的可执行版本）、小红书自证禁词。
    """
    bad = []
    n = len(pub.get("copy_text") or "")
    if n and (n < COPY_MIN or n > COPY_MAX):
        bad.append("配文 %d 字，建议 %d-%d 字" % (n, COPY_MIN, COPY_MAX))
    if len(pub.get("digest") or "") > DIGEST_MAX:
        bad.append("摘要超 %d 字" % DIGEST_MAX)
    if len(pub.get("keyword_reply") or "") > KEYWORD_MAX:
        bad.append("关键词回复超 %d 字" % KEYWORD_MAX)
    tts = pub.get("tt_titles") or []
    if tts and len(tts) < TT_TITLE_N:
        # 两条一模一样在 _title_list 里就被并成一条了，所以这条同时管"只给一条"和"给两条但同题"
        bad.append("头条标题只有 %d 条（两条写成同一个题型也会被并掉），后台有 A/B 两个位、"
                   "要 %d 条不同题型" % (len(tts), TT_TITLE_N))
    pool = _body_pool(pub, plan)
    for label, items, lim in (("头条标题", tts, TT_TITLE_MAX),
                              ("小红书标题", pub.get("xhs_titles") or [], XHS_TITLE_MAX)):
        for i, t in enumerate(items, 1):
            if len(t) > lim:
                bad.append("%s%d %d 字，超 %d 字会被截断" % (label, i, len(t), lim))
            for num in set(re.findall(r"\d+(?:\.\d+)?", t)):
                if len(num.replace(".", "")) >= 2 and num not in pool:
                    bad.append("%s%d 里的「%s」正文里没有——标题不许出现正文没有的数" % (label, i, num))
    xcopy = pub.get("xhs_copy") or ""
    if xcopy and len(xcopy) < 40:
        bad.append("小红书正文只有 %d 字，太薄会被当随手发" % len(xcopy))
    for field, text in (("小红书标题", " ".join(pub.get("xhs_titles") or [])),
                        ("小红书正文", xcopy),
                        ("话题标签", " ".join(pub.get("xhs_tags") or []))):
        for pat, why in XHS_BAN:
            if re.search(pat, text or ""):
                bad.append("%s撞了自证句：%s" % (field, why))
    tags = pub.get("xhs_tags") or []
    if tags and len(tags) < XHS_TAG_MIN:
        bad.append("话题标签 %d 个，建议 %d-%d 个" % (len(tags), XHS_TAG_MIN, XHS_TAG_MAX))
    # 空值单独说，而且只说一句：加这个功能之前拆的老文案全都会落到这里，
    # 分开报会变成三行"没生成"，看着像坏了。
    if not (tts or pub.get("xhs_titles") or xcopy):
        bad.append("发布标题／小红书正文是空的：这一组是加这一档之前拆的文案，"
                   "点「① 拆文案」重拆一次就有（图面主标题不算发布标题，那是画进卡片的）")
    return bad


# 两种模式对"写多少"的取向完全相反，所以铁律三的后半句分开写：
# 模型画字时多写会错，程序排字时少写是浪费版面。
_DENSITY = {
    "typeset": ("字是程序用真字体排到图上的，不会写错也不会漏，所以**别为了省字把信息砍掉**："
                "把素材里的具体数值、条件、时间、坑都留在图上，一张卡片讲清一件事就够了。"
                "宁可写满也不要写成口号；但合计超过 __TOTAL__ 字版面会放不下并被截断。"),
    "model": ("字由出图模型画进像素，**字数越多错字率越高**，所以宁可少一张卡片把主线讲完整，"
              "也不要多一张卡片讲半句。"),
}


def _density_rule(text_mode):
    return _DENSITY.get(text_mode) or _DENSITY["model"]


def style_fixed_prompt(style_key):
    """用户已在下拉里锁定风格时，把这套配法的画面方向塞进拆解 prompt。

    不塞的话模型会按自己那套联想写 scene/v：实测选"清单笔记风"却写出"能量核心脉动"，
    风格与配图对不上，画出来自然别扭。
    """
    p = STYLE_PRESETS.get(style_key or "") or {}
    if not p.get("label") or style_key == "auto":
        return ""
    return ("\n本篇风格已由人工指定为 \"%s\"（%s），style_key 必须填这个键，"
            "scene 与各卡片的 v 都要按这个方向的画面来想：%s。"
            % (style_key, p["label"], p.get("scene") or ""))


def _resplit(pages, want, lim):
    """按 want 页重新分配卡片：用户在界面上定的页数与模型给的不一致时兜一次。

    只动卡片归属——每页的标题/副标题/布局/场景沿用模型给的那一页，多出来的新页留空
    （界面会提示"主标题为空"，人补一句就行）。要点一条都不许丢，这是铁律三的下半句。
    """
    n = max(1, min(want, PAGE_MAX))
    flat = []
    for p in pages:
        flat.extend(p.get("cards") or [])
    if n == len(pages) or not flat:
        return pages, ""
    short = ""
    if n > len(flat):
        # 要点比页数还少：硬摊成 N 页必然出现空页（界面会报"这页是空的，出图时会被丢掉"），
        # 不如老实按能摊开的页数给，并说清楚为什么
        short = "、这篇只有 %d 条要点，最多拆 %d 页" % (len(flat), len(flat))
        n = max(1, len(flat))
    base, extra = divmod(len(flat), n)
    out, i = [], 0
    for k in range(n):
        cnt = base + (1 if k < extra else 0)
        chunk = flat[i:i + cnt]
        i += cnt
        src = pages[k] if k < len(pages) else {}
        out.append({"title": src.get("title") or "", "subtitle": src.get("subtitle") or "",
                    "cards": chunk, "layout_key": src.get("layout_key") or "",
                    "style_key": src.get("style_key") or pages[0].get("style_key") or DEFAULT_STYLE_KEY,
                    "role": src.get("role") or ("第%d页" % (k + 1)),
                    "footer": src.get("footer") or pages[0].get("footer") or "内容整理自公开分享",
                    "scene": src.get("scene") or ""})
    if i < len(flat):                      # 均分不该剩，真剩了就并进末页，绝不丢要点
        out[-1]["cards"] = out[-1]["cards"] + flat[i:]
    out = [fit_plan(p, lim["total"], lim)[0] for p in out]
    return out, ("你要 %d 页、模型给了 %d 页，已把 %d 条要点重排成 %d 页（新页标题请补一句）%s"
                 % (want, len(pages), len(flat), n, short))


# ── 攻略页（真帧当证据那一档）与拆解层的接线 ─────────────────────
# 这一档和"插画海报"的根本差别：画面不靠模型画，靠素材里那一帧真画面当证据。
# 于是多了两件必须代码管住的事：
#   ① 模型只许从"已判过、确认没被 up主 压过字"的帧里挑编号，不许凭空点名一张图；
#   ② caption 由代码按那一帧自己的记录写（画面里是什么），**不让模型写**——它没看过那张图，
#      写出来的就是照着文案倒推的假图注。这份 caption **不印到图上**（号主 10-13 点名），
#      只留在 plan/manifest 里给我们核对"图和这句话对不对得上"用。
GUIDE_KEYS = ("kicker", "photos", "bignum", "contrast", "warn", "ask")

_GUIDE_RULES = """
铁律六·攻略页（这一档不靠模型画插画，画面全部来自素材真帧）：
可用画面只有下面这些，编号是 F+数字，**一张都不许多编，也不许说画面里有它没说的东西**：
__FRAMES__
每页按内容需要给这几样，用不上就别给，别硬凑__SHAPE__：
- "photos": [{"frame": "F7"}] —— 这一页的证据图，**一页只给 1 张**（号主 2026-10-22 定死：
  硬上限 1 张/页，要多放画面就加页数，不许在一页里堆两张）。只能填上面的编号。
  **一个编号整组只许用一次，两页之间也要差 12 秒以上**——同一场景贴两遍比没图更难看。
  **每一页都必须给 1 张图，一页都不许空着**（号主 2026-10-22 定死："必须有图片"——
  读者就是来看游戏画面的，空页等于这一页没做完）。漏了后端会自动补一张，但那是兜底：
  补的不一定跟这一页讲的事对得上，你自己挑才挑得准。
  **图注不上图**（那一帧的说明由程序写进档案，只给我们自己核对），所以**你不要写 caption**。挑图只有一条标准：
  这一页在讲的那件事，画面里真的看得见；看不见就不给图。
- "kicker": "栏目名 · 本篇主题"（≤14 字，整组同一句，只填在第一页）
- "bignum": {"v": "约 +20%", "label": "这个数字说的是什么"} —— v 里的数字必须在素材原文里出现过
- "contrast": [{"t": "已改", "d": "…"}, {"t": "没改", "d": "…"}] —— 两行讲清改了/没改
- "warn": {"t": "一句提醒", "d": "限制是什么、为什么"} —— 素材没说的写在这儿，别编成结论
- "ask": {"q": "一个能直接回答的问题", "options": ["不超过三项"]} —— 只给最后一页
这一档每页正文仍守 ≤__TOTAL2__ 字，但卡片建议 2-3 条，把地方留给图。
**页与页不许同构**（号主 2026-10-22 点名：两张排版一模一样，看多了无聊）：
- 封面页：`bignum`（或一句结论式大标题）+ 1 张图 + ≤2 条卡片；
- 中间页：每页只讲一件事，1 张图 + 2-3 条卡片，**整组至少一页用 `contrast`（改了/没改）**、
  **至少一页用 `warn`（限制与风险）**，这两块不许挤在同一页；
- 最后一页：`ask` 互动 + 脚注，不再放新论点；
- 相邻两页的"零件组合"不许完全相同：都是「1 张图 + 3 条卡片」就是同构，
  把其中一页改成大字数字开头、或改成对照块开头。
卡片小标题长短混着写：三条全是四个字（"装备取舍""路线规划""收益结算"）看着就像模板，
换一条素材还是那四个字，等于把十篇做成一篇。
**图注不用你写、也不会印到图上**——那一帧的说明只留在档案里给我们自己核对，
所以挑图只有一条标准：这一页讲的事，画面里真的看得见。
__META__
"""


def guide_rules_text(items, total):
    """帧清单进提示词；没有可用帧就返回空串（这一档整段不出现，不占预算）"""
    if not items:
        return ""
    try:
        from . import frame_pool
    except ImportError:
        import frame_pool
    return (_GUIDE_RULES
            .replace("__FRAMES__", frame_pool.index_text(items))
            .replace("__TOTAL2__", str(total))
            .replace("__SHAPE__", "（这些键就写在 pages[] 每一页的对象里，示例已经带上；"
                                  "哪一页用不上某个零件，就把那**整个键删掉**）")
            .replace("__META__", META_RULE))


def guide_shape_prompt(items):
    """pages[] / 单页那个 JSON **示例**里要不要带上攻略零件（有真帧才带）。

    为什么不只在提示词末尾写"铁律六"就够了：2026-10-22 真实试用实测——帧清单 6 张、
    铁律六整段都在提示词里，模型仍然照 `pages[]` 示例的形状交了一份纯 cards
    （示例里没有的键，它当这个键不存在），于是 `guide_only=false`、出图花 2 元画插画、
    玩家一行游戏画面都看不到。**示例才是模型真正照着填的那张表**，规则文字管不住它。
    这一段只在有帧时插入，没帧时留空＝形状和以前逐字一致（默认零变化）。
    """
    if not items:
        return ""
    return (', "kicker": "栏目名 · 本篇主题（整组同一句，只填第一页）",'
            ' "photos": [{"frame": "F1"}],'
            ' "bignum": {"v": "素材原文里出现过的数", "label": "这个数字说的是什么"},'
            ' "contrast": [{"t": "已改", "d": "一句话"}, {"t": "没改", "d": "一句话"}],'
            ' "warn": {"t": "一句提醒", "d": "限制是什么、为什么"},'
            ' "ask": {"q": "结尾问一句能直接回答的问题", "options": ["选项一", "选项二"]}')


def _as_list(v):
    """把模型该交数组却交成对象/字符串的那一项洗成列表。

    为什么不只在提示词里要求它交数组：2026-10-23 那次「① 拆文案」整单任务失败，
    报 `TypeError: unhashable type: 'slice'`——模型把 `contrast` 交成了一个对象，
    代码里 `page.get("contrast")[:3]` 就是给 dict 切片段。钱已经花在那一次调用上了，
    解析层再炸等于白花钱，所以这里兜住，内容尽量留而不是丢掉。
    """
    if v is None or isinstance(v, bool) or isinstance(v, (int, float)):
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, dict):
        if any(k in v for k in ("t", "d", "v", "q", "label", "note", "frame", "path")):
            return [v]                       # 一条记录被当成对象交了
        return [{"t": str(k), "d": str(x)} for k, x in v.items()]   # 「已改/没改」那种标签→文字
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    return [v]


def _opt_text(o):
    """`ask.options` 里的一条：字符串直接用；被交成对象时取那句人话。

    模型可能交 `{"已改": "…", "没改": "…"}`（标签在键上），也可能交
    `{"1": "手动开", "2": "宏自动"}`（标签在值上）——键是纯序号/一两个字符就当占位键，取值。
    """
    if not isinstance(o, dict):
        return str(o or "")
    k = str(o.get("t") or "").strip()
    v = str(o.get("d") or "").strip()
    if k and not (k.isdigit() or (len(k) <= 2 and k.isascii())):
        return k
    return v or k


def guide_clip(page, lim):
    """把模型给的攻略页零件洗成排版能直接用的形状（裁字数、丢空项）"""
    out = {}
    k = (page.get("kicker") or "").strip()
    if k:
        out["kicker"] = _clip_ws(k, 16)
    bn = page.get("bignum")
    if isinstance(bn, dict) and str(bn.get("v") or "").strip():
        out["bignum"] = {"v": _clip_ws(bn.get("v"), 12),
                         "label": _clip_ws(bn.get("label"), 20),
                         "note": _clip_ws(bn.get("note"), 30)}
    con = []
    for c in _as_list(page.get("contrast"))[:3]:
        if isinstance(c, dict) and (c.get("t") or c.get("d")):
            con.append({"t": _clip_ws(c.get("t"), 8),
                        "d": _clip(c.get("d"), lim["cardD"])})
    if con:
        out["contrast"] = con
    wn = page.get("warn")
    if isinstance(wn, dict) and (wn.get("t") or wn.get("d")):
        out["warn"] = {"t": _clip_ws(wn.get("t"), 12),
                       "d": _clip(wn.get("d"), lim["cardD"] * 2)}
    ak = page.get("ask")
    if isinstance(ak, dict) and str(ak.get("q") or "").strip():
        opts = []
        for o in _as_list(ak.get("options"))[:3]:
            s = _opt_text(o)
            if s.strip() and s not in opts:
                opts.append(_clip_ws(s, 8))
        out["ask"] = {"q": _clip(ak.get("q"), lim["title"]), "options": opts}
    return out


GUIDE_FRAME_MIN_GAP = 12    # 秒：两张帧差这么点时间就是同一个场景，放同一页等于白占半屏
GUIDE_PHOTOS_PER_PAGE = 1   # 一页几张证据图：号主 2026-10-22 定死 1 张（硬上限，要多加页数）


def _ts_seconds(ts):
    """'00:39' / '1:02:03' → 秒；解析不了返回 None（不拦，交给原逻辑）。"""
    s = str(ts or "").strip()
    if not s or ":" not in s:
        return None
    parts = s.split(":")
    if len(parts) > 3:
        return None
    try:
        nums = [int(float(p)) for p in parts]
    except (TypeError, ValueError):
        return None
    out = 0
    for n in nums:
        out = out * 60 + n
    return out


def bind_frames(pages, items, lim=None):
    """模型给的 {"frame":"F7"} → 真文件 + 程序写的图注；编号对不上就丢掉并留一句人话。

    返回说明列表（给人看，不抛错）：哪一页点了不存在的编号、哪一页因此没图，
    都得在界面上看得见——否则"这页为什么空着"又变成要猜的事。
    """
    try:
        from . import frame_pool
    except ImportError:
        import frame_pool
    by = {}
    for it in items or []:
        try:
            by["F%d" % int(it.get("i") or 0)] = it
        except (TypeError, ValueError):
            continue
    notes = []
    used_ts = []        # [(秒, 第几页)]：整组用过的帧时刻，用来挡"同一场景连着截两张"
    lim = lim or dict(TS_LIMITS)
    # 栏目名是"这一套"的记号，模型只肯写在第 1 页 → 往下补一遍。
    # 对标那五页每页左上角都有同一句栏目名，缺了就像五张不相干的图。
    series_kicker = ""
    for p in pages or []:
        if isinstance(p, dict) and str(p.get("kicker") or "").strip():
            series_kicker = str(p["kicker"]).strip()
            break
    if series_kicker:
        for p in pages or []:
            if isinstance(p, dict) and not str(p.get("kicker") or "").strip():
                p["kicker"] = series_kicker
    for pi, p in enumerate(pages or [], 1):
        if not isinstance(p, dict):
            continue
        p.update(guide_clip(p, lim))
        raw = p.get("photos")
        if not isinstance(raw, list) or not raw:
            continue
        kept = []
        if len(raw) > GUIDE_PHOTOS_PER_PAGE:
            # 光在提示词里说"一页只给 1 张"不算数（模型照样会给两张），代码级再切一刀：
            # 号主 2026-10-22 定的是**硬上限**，要多放画面就加页数。
            notes.append("第 %d 页给了 %d 张图，这一档一页只放 %d 张（号主定的硬上限），"
                         "多的已去掉——要放更多画面就再加一页"
                         % (pi, len(raw), GUIDE_PHOTOS_PER_PAGE))
        for ph in raw[:GUIDE_PHOTOS_PER_PAGE]:
            fid = ""
            if isinstance(ph, str):
                fid = ph.strip().upper()
            elif isinstance(ph, dict):
                fid = str(ph.get("frame") or ph.get("id") or "").strip().upper()
                if not fid and ph.get("path"):
                    # 人手工在界面上选过图：路径直给，图注也保留人写的那句。
                    # 2026-10-21 Codex P0 点名：这条路**绕过了判帧**——文件是谁都行，
                    # 程序无从知道画面上有没有别人压的字。所以标 judged=False 并留一句人话，
                    # 让它进最终审核清单（"未判，需人眼看"），不许因为"是人选的"就当作已核。
                    kept.append({"path": str(ph.get("path")),
                                 "caption": _clip_ws(ph.get("caption"), 40),
                                 "judged": False})
                    notes.append("第 %d 页这张是手工选的路径（没走判帧，%s）——"
                                 "发布前人眼确认画面里没有 up 主压的字幕条、花字、账号名"
                                 % (pi, str(ph.get("path")).rsplit("/", 1)[-1][:24]))
                    continue
            it = by.get(fid)
            if not it:
                notes.append("第 %d 页点了 %s，可用画面里没有这个编号，这张图已丢掉"
                             % (pi, fid or "空编号"))
                continue
            secs = _ts_seconds(it.get("ts"))
            if secs is not None:
                near = next((k for k in used_ts if abs(k[0] - secs) < GUIDE_FRAME_MIN_GAP), None)
                if near:
                    notes.append("第 %d 页的 %s 与第 %d 页那张只差 %d 秒，是同一个画面，"
                                 "已丢掉（一页一张、整组同一个编号只用一次；要放更多画面就加一页，"
                                 "别挑挨着几秒的那张）"
                                 % (pi, fid, near[1], abs(near[0] - secs)))
                    continue
                used_ts.append((secs, pi))
            kept.append({"path": it.get("file"), "frame": fid, "judged": True,
                         "caption": frame_pool.caption_for(it),
                         "ts": it.get("ts") or ""})
        if kept:
            p["photos"] = kept
        else:
            p.pop("photos", None)

    # ── 每一页都必须有一张关键帧（号主 2026-10-23 定死："必须有图片"）──────────
    # 光在提示词里写"每页给一张"不算数：模型会漏，12 秒同场景闸还会把挨着的那几张挡掉，
    # 结果就是四页里两页空着。这里代码兜底：从没用过的帧里挑一张**最清楚、
    # 且跟已经用上的画面离得最远**的补进去，补了什么为什么补，全部写成一句人话。
    used_ids = {ph.get("frame") for p in pages if isinstance(p, dict)
                for ph in (p.get("photos") or []) if ph.get("frame")}
    pool_items = [it for it in (items or []) if it.get("file")]

    def _far(it):
        """跟已用画面的最小时间距离（越大越是另一个场景）。"""
        s = _ts_seconds(it.get("ts"))
        if s is None or not used_ts:
            return 9999
        return min(abs(s - k[0]) for k in used_ts)

    def _clear(it):
        try:
            return frame_pool.sharpness(it.get("file")) >= frame_pool.HARDEN_MIN_SHARP
        except Exception:
            return True

    for pi, p in enumerate(pages, 1):
        if not isinstance(p, dict) or p.get("photos"):
            continue
        left = [it for it in pool_items if ("F%d" % int(it.get("i") or 0)) not in used_ids]
        if not left:
            notes.append("第 %d 页补不出图了：这条素材能用的画面一共 %d 张，已被别的页占完——"
                         "减一页、或你自己在「＋ 用你的图」传一张截图" % (pi, len(pool_items)))
            continue
        far = [it for it in left if _far(it) >= GUIDE_FRAME_MIN_GAP]
        pick_pool = far or left          # 宁可重复场景，也不能让这一页没图（号主要的是"必须有"）
        pick = sorted(pick_pool, key=lambda it: (not _clear(it), -_far(it)))[0]
        fid = "F%d" % int(pick.get("i") or 0)
        p["photos"] = [{"path": pick.get("file"), "frame": fid, "judged": True,
                        "caption": frame_pool.caption_for(pick),
                        "ts": pick.get("ts") or "", "auto": True}]
        used_ids.add(fid)
        secs = _ts_seconds(pick.get("ts"))
        if secs is not None:
            used_ts.append((secs, pi))
        notes.append("第 %d 页模型没配图，已自动补 %s（%s）" % (
            pi, fid, "画面清楚、跟已用的不是同一时刻" if far else "剩下的都跟已用画面挨得近，先保证有图"))
    return notes


def borrowed_frame_count(plan):
    """版面里真的绑上了几张**素材帧**（`bind_frames` 从帧池取的那些）。

    为什么单独数这个：`guide_provenance` 光看"人登记了几条材料"会留一个洞——一条都不登记
    照样没缺项。而 `frame` 这个键只有从这条素材的帧池取图时才写得上（手工给 path 的没有），
    所以它说的是"这张借来的画面确实进了成品"，不是"库里有这么条素材"。
    判帧证明的是画面干净，**不是能用**；用了就得有一条「被引用的画面」记着凭什么用。
    """
    n = 0
    for pg in plan_pages(plan) or []:
        for ph in (pg.get("photos") or []):
            if isinstance(ph, dict) and str(ph.get("frame") or "").strip():
                n += 1
    return n


def unjudged_photos(plan):
    """版面里**没走判帧**的那些配图（人手工给的路径 / 旧 plan 带回来的不明文件）。

    判帧那条链（frame_pool）只覆盖"按 F 编号从帧池取图"这一条路。手工给 path 的、
    或者从旧成品复制回来的，程序无从知道画面上有没有 up 主压的字——2026-10-21 Codex
    P0 点名的就是这个缺口。这里不拦出图（人手选的图大概率就是他看过的那张），
    但必须在成品清单上写"未判，需人眼看"，不许因为出图成功就当它过了检。
    """
    out = []
    multi = len(plan.get("pages") or []) > 1
    for n, pg in enumerate(plan_pages(plan) or [], 1):
        for ph in (pg.get("photos") or []):
            if not isinstance(ph, dict):
                continue
            if ph.get("judged") is True or str(ph.get("frame") or "").strip():
                continue
            out.append("第%d页·%s" % (n, str(ph.get("path") or "").rsplit("/", 1)[-1])
                       if multi else str(ph.get("path") or "").rsplit("/", 1)[-1])
    return out


def normalize_page(item, lim, style_key, has_photo, idx, first_title="",
                   group_footer="", group_scene=""):
    """把模型给的一页洗成能排版的那一页：裁字数、认布局键、没图就换掉"要图"的构图。

    整组拆解与「只重拆这一页」共用这一处——两条路出来的页必须形状一致，
    否则同一份排版引擎要伺候两种输入，早晚在其中一条上静默出错。
    返回 (页 或 None, 给人看的说明列表)。None 表示这页是空的，该丢掉。
    """
    notes = []
    cards = []
    for c in _as_list(item.get("cards"))[:CARD_MAX]:
        if not isinstance(c, dict):
            continue
        t, d = _clip(c.get("t"), lim["cardT"]), _clip(c.get("d"), lim["cardD"])
        if t or d:
            cards.append({"t": t, "d": d, "v": _clip_ws(c.get("v"), CARD_VIS_MAX)})
    title = _clip(item.get("title") or (first_title if idx == 1 else ""), lim["title"])
    lay, note = _page_layout(item.get("layout_key"), style_key, has_photo, idx)
    if note:
        notes.append(note)
    if not title and not cards:
        return None, ["第 %d 页是空的（没标题也没卡片），已丢掉" % idx]
    p = {"title": title, "subtitle": _clip(item.get("subtitle"), lim["subtitle"]),
         "cards": cards, "layout_key": lay, "style_key": style_key,
         "role": _clip_ws(item.get("role"), 8),
         "footer": _clip(item.get("footer") or group_footer or "内容整理自公开分享", lim["footer"]),
         "scene": _clip_ws(item.get("scene") or (group_scene if idx == 1 else ""), SCENE_MAX)}
    # 攻略页那几样（栏目名/真帧/大字数字/对照/警示/互动）原样带下去；清洗与
    # "编号对不对得上真帧"统一在 bind_frames 一处做，别在这儿再洗一遍
    for _k in GUIDE_KEYS:
        if item.get(_k):
            p[_k] = item.get(_k)
    p, pn = fit_plan(p, lim["total"], lim)
    notes.extend(["第 %d 页：%s" % (idx, x) for x in pn])
    return p, notes


def _pages_from_data(data, lim, style_key, has_photo, first_title="", page_hint=0):
    """把模型给的 pages[] 洗成能排版的页列表；模型不写 pages 就返回 None（按单页走）。

    模型不守格式是常态：漏页、给不认识的布局键、把"要图"的布局分给没图的页、
    单页超预算——都在这里一次修完并留下一句人话说明，不抛错、不整条挂掉。
    page_hint 是用户在界面上定死的页数，与模型给的不一致时按用户的重排。
    返回 (pages 或 None, why, notes)。
    """
    notes = []
    why = _clip_ws(data.get("why"), 120)
    raw = data.get("pages")
    if not isinstance(raw, list):
        return None, why, notes
    picked = [p for p in raw if isinstance(p, dict)]
    if not picked:
        return None, why, notes
    try:
        want = int(data.get("page_count") or len(picked))
    except (TypeError, ValueError):
        want = len(picked)
    if want < 1 or want > PAGE_MAX:
        notes.append("模型给的页数「%s」不在 1-%d，按 %d 页处理"
                     % (data.get("page_count"), PAGE_MAX, max(1, min(want, PAGE_MAX))))
        want = max(1, min(want, PAGE_MAX))
    if len(picked) > want:
        notes.append("模型给了 %d 页，按 page_count=%d 只留前 %d 页" % (len(picked), want, want))
        picked = picked[:want]
    pages = []
    for idx, item in enumerate(picked, 1):
        p, pn = normalize_page(item, lim, style_key, has_photo, idx,
                               first_title=first_title, group_footer=data.get("footer") or "",
                               group_scene=data.get("scene") or "")
        notes.extend(pn)
        if not p:
            continue
        pages.append(p)
        if len(pages) >= PAGE_MAX:
            break
    if page_hint and pages and len(pages) != page_hint:
        pages, rn = _resplit(pages, page_hint, lim)
        if rn:
            notes.insert(0, rn)
    return (pages or None), why, notes


_PAGE_PROMPT = """你是自媒体图文编辑。这一组海报已经拆好了，现在**只重写其中第 __IDX__ 页**，
其他页一个字都不动，也不要输出其他页。

本篇主题：__THEME__
整组主线：__SPINE__

其他几页已经在讲这些（**严禁重复**，这一页要讲素材里还没被用掉、又能自成一块的内容）：
__OTHERS__

这一页的定位：角色 __ROLE__（可换，两个字，如 收藏 / 行动 / 风险 / 收尾）；
页数不变、风格不变（style_key 固定填 "__STYLE__"，整组要统一）。

铁律（与整组拆解同一套，只是范围只剩这一页）：
- 只能用素材里出现过的事实与数字，严禁编造；全程转述口吻，禁用第一人称；
  素材是断开的残句时要把一句话说完整再落卡片。
__META__
- 这一页的卡片之间要构成递进，每张都能回答"所以呢"；两张卡不许讲同一件事。
- 主标题 ≤__TITLE__ 字；副标题 ≤__SUB__ 字；每张卡片标题 ≤__CT__ 字、说明 ≤__CD__ 字；
  **这一页**卡片 3-5 张、全部文字合计 ≤__TOTAL__ 字；中文标点计入字数；不要 markdown 符号；
  价格、天数、日期保持素材里的阿拉伯数字。
  卡片小标题长短混着写，别和前面几页一样全是四字格——三连四字读起来就是模板。
- scene（≤48 字）写这一页要画成什么场景，要看得见；每张卡给 v（≤24 字）写这一条画什么，
  要有形体（讲数值画仪表/钱，讲风险画断裂/警示，讲选择画岔路）。scene 与 v 不占字数预算。
__LAYOUT__
输出 JSON（只此一个对象，不要 pages 数组、不要 publish）：
{"role": "两个字", "layout_key": "上面布局键名之一", "title": "这一页主标题",
 "subtitle": "这一页副标题，可留空", "scene": "这一页的画面",
 "cards": [{"t": "卡片标题", "d": "卡片说明", "v": "这一条画什么"}]__GUIDE_SHAPE__}

素材（可能已经是别人总结过的内容）：
__CONTENT__
"""


def page_plan_prompt(page_index, pages, spine, style_key, has_photo, theme, lim, content="",
                     frames=None):
    """把"只重写第 k 页"这段话拼出来（纯函数，离线可测）。

    其他页的标题与要点会作为"别重复"的清单塞进去——不给这份清单，模型最容易出现的就是
    第二页把第一页讲过的话再说一遍。

    frames 跟整组拆解同一个意思：给了才有"铁律六·攻略页"，模型才敢往这一页填 photos 编号。
    以前这一条路不带帧清单，于是界面上「只重拆这一页」会把原来那页的真帧和图注悄悄丢掉——
    攻略页这一档最容易踩的就是"字还在、图没了"。
    """
    others = []
    for i, p in enumerate(pages, 1):
        if i == page_index:
            continue
        pts = "、".join([((c.get("t") or c.get("d") or "")[:12]) for c in _as_list(p.get("cards"))[:5]
                         if isinstance(c, dict) and (c.get("t") or c.get("d"))])
        others.append('  - 第 %d 页「%s」：%s%s' % (i, p.get("role") or "",
                                                   p.get("title") or "（没标题）",
                                                   "；要点：" + pts if pts else ""))
    cur = pages[page_index - 1] if 1 <= page_index <= len(pages) else {}
    used = []
    for i, q in enumerate(pages, 1):
        if i == page_index or not isinstance(q, dict):
            continue
        for ph in (q.get("photos") or []):
            f = str((ph or {}).get("frame") or "").strip().upper()
            if f:
                used.append("%s(%s)" % (f, (ph or {}).get("ts") or "时刻未知"))
    txt = (_PAGE_PROMPT
           .replace("__IDX__", str(page_index))
           .replace("__THEME__", (theme or "未指定，按素材自判").strip())
           .replace("__SPINE__", (spine or "（未填）"))
           .replace("__OTHERS__", "\n".join(others) or "  - （这是唯一一页，没有别页）")
           .replace("__ROLE__", cur.get("role") or "（未定）")
           .replace("__STYLE__", style_key or DEFAULT_STYLE_KEY)
           .replace("__LAYOUT__", GUIDE_LAYOUT_LOCK if frames
                    else "\n".join(layout_choices_prompt(has_photo)))
           .replace("__TITLE__", str(lim["title"])).replace("__SUB__", str(lim["subtitle"]))
           .replace("__CT__", str(lim["cardT"])).replace("__CD__", str(lim["cardD"]))
           .replace("__TOTAL__", str(lim["total"]))
           .replace("__GUIDE_SHAPE__", guide_shape_prompt(frames))
           .replace("__CONTENT__", (content or "")[:8000])
           .replace("__META__", META_RULE)
           + guide_rules_text(frames, lim["total"]))
    if frames and used:
        # 逐页填这条路必须告诉它"哪些编号已被别的页占了"：不说的话模型会挑同一场景里
        # 挨着的那几张，后端 12 秒同场景闸一挡，这一页就变成没图
        # （2026-10-22 线上实测第 3 页就是这么空的：F6 与第 1 页的 F3 只差 9 秒）。
        txt += ("\n**这些编号已经被别的页用掉了**：%s。这一页要配图就挑跟它们**相差 %d 秒以上**的另一张；"
                "挑不出来就这一页不放图——同一场景贴两遍比没图更难看。"
                % ("、".join(used), GUIDE_FRAME_MIN_GAP))
    return txt


def build_page_plan(summary, ai_config, page_index, pages, spine="", theme="",
                    style_key="", text_mode=DEFAULT_TEXT_MODE, has_photo=False, frames=None):
    """只重拆一页，返回 (page, violations, notes)。其他页由调用方原样留着。

    整组「换一套」会把人手动改过的别的页一起冲掉，这条就是给"这一页不满意"准备的出口。
    frames 是这条素材的可用真帧清单：不传进来，这一页就拿不到「铁律六」，模型不会给 photos 编号，
    重拆一次等于把原来那页的证据图悄悄丢了（2026-10-13 复盘发现的洞）。
    """
    api_key, api_base, model = _require_ai(ai_config)
    import openai

    lim = limits_for(text_mode)
    try:
        page_index = int(page_index)
    except (TypeError, ValueError):
        page_index = 1
    if page_index < 1:
        page_index = 1
    if not pages:
        raise PosterError("这一组还没有页可拆，先点「① 拆文案」")
    if page_index > len(pages):
        page_index = len(pages)
    key = style_key if style_key in (set(STYLE_PRESETS) | set(_user_presets())) else DEFAULT_STYLE_KEY
    prompt = page_plan_prompt(page_index, pages, spine, key, has_photo, theme, lim,
                              content=summary.get("content") or "", frames=frames)
    client = openai.OpenAI(api_key=api_key, base_url=api_base or "https://api.openai.com/v1",
                           timeout=300, max_retries=1)
    resp = client.chat.completions.create(
        model=model or "qwen-plus",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5, response_format={"type": "json_object"})
    data = _parse_json(resp.choices[0].message.content)
    page, notes = normalize_page(data, lim, key, has_photo, page_index,
                                 first_title=(pages[0].get("title") if page_index == 1 else ""),
                                 # 尾注跟着这一组走：重拆一页不该把整组那句合规声明换成默认句，
                                 # 换了就跟别的页对不上，plan_pages 那句"只留末页"也收不掉
                                 group_footer=((pages[page_index - 1] or {}).get("footer") or ""))
    if not page:
        raise PosterError("模型这一页什么也没给（没标题也没要点），换一套没换成")
    if frames:
        # 编号→真文件、程序写图注、整组栏目名对齐，全都走 bind_frames 这一条（跟整组拆解同源）。
        # 只传这一页会怎样：别的页有栏目名而这一页没有 → 五页看着像五张不相干的图。
        group = list(pages)
        group[page_index - 1] = page
        notes = list(notes) + bind_frames(group, frames, lim)
    one = {"pages": [page]}
    return page, lint_plan(one, lim), notes


def build_text_plan(summary, ai_config, theme="", title="", style_key="", text_mode=DEFAULT_TEXT_MODE,
                    has_photo=False, page_hint=0, frames=None):
    """调 LLM 产出文案计划 + 发布文案，并逐字段裁到字数线内。

    返回 (plan, publish, violations, notes)：plan 是要画进图里的文字（超预算先经 fit_plan
    自动收敛，violations 非空才拒绝出图），publish 是发布页用的配文/摘要/关键词回复/
    来源说明（只软校验），notes 记录自动压缩动了哪几处。

    style_key 显式传入且合法时直接盖掉模型选的风格：用户在弹窗里点过下拉，
    就不该再被模型自由发挥改回去。
    has_photo 只影响推荐：有真图才允许推满版大图/左右分栏那几种"没图站不住"的布局。
    frames 是这条素材的**可用真帧清单**（frame_pool 判过、确认没被 up主 压过字的那些）：
    给了它，提示词里才多一条"铁律六·攻略页"，模型才允许往 pages 里填 photos/bignum/… 。
    page_hint 同理用在页数上：人已经在页签里加到 3 页，再点「换一套」就是想要 3 页的结构，
    这时候页数不再由模型判断（模型给成 2 页的话，后端把要点重排成 3 页并留一句说明）。
    """
    api_key, api_base, model = _require_ai(ai_config)
    import openai

    try:
        page_hint = int(page_hint or 0)
    except (TypeError, ValueError):
        page_hint = 0
    page_hint = max(0, min(page_hint, PAGE_MAX))
    lim = limits_for(text_mode)
    client = openai.OpenAI(api_key=api_key, base_url=api_base or "https://api.openai.com/v1",
                           timeout=300, max_retries=1)
    prompt = (_PLAN_PROMPT
              .replace("__THEME__", (theme or "未指定，按素材自判").strip())
              .replace("__STYLES__", style_plan_prompt(frames))
              .replace("__DENSITY__", _density_rule(text_mode))
              .replace("__TOTAL__", str(lim["total"])).replace("__TITLE__", str(lim["title"]))
              .replace("__SUB__", str(lim["subtitle"])).replace("__CT__", str(lim["cardT"]))
              .replace("__CD__", str(lim["cardD"])).replace("__FOOT__", str(lim["footer"]))
              .replace("__STYLE_FIXED__", style_fixed_prompt(style_key))
              .replace("__LAYOUT__", layout_plan_prompt(has_photo, page_hint, bool(frames)))
              .replace("__GUIDE_SHAPE__", guide_shape_prompt(frames))
              .replace("__RISKS__", risks_prompt_block(summary))
              .replace("__CONTENT__", (summary.get("content") or "")[:8000])
              .replace("__META__", META_RULE)
              + guide_rules_text(frames, lim["total"]))
    resp = client.chat.completions.create(
        model=model or "qwen-plus",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3, response_format={"type": "json_object"})
    data = _parse_json(resp.choices[0].message.content)

    cards = []
    for c in _as_list(data.get("cards"))[:CARD_MAX]:
        if not isinstance(c, dict):
            continue
        t, d = _clip(c.get("t"), lim["cardT"]), _clip(c.get("d"), lim["cardD"])
        if t or d:
            cards.append({"t": t, "d": d, "v": _clip_ws(c.get("v"), CARD_VIS_MAX)})
    valid = set(STYLE_PRESETS) | set(_user_presets())
    key = str(style_key if style_key in valid else data.get("style_key") or "").strip()
    if key not in valid:
        key = DEFAULT_STYLE_KEY
    plan = {"title": _clip(title or data.get("title") or summary.get("title"), lim["title"]),
            "subtitle": _clip(data.get("subtitle"), lim["subtitle"]),
            "cards": cards,
            "footer": _clip(data.get("footer") or "内容整理自公开分享", lim["footer"]),
            # spine 只用来让人一眼判断「这条主线成不成链」，不画进海报、不计入字数预算
            "spine": _clip_ws(data.get("spine"), SPINE_MAX),
            # scene 与卡片的 v 同理：只喂给出图模型，不当成图面文字，所以也不占字数预算
            "scene": _clip_ws(data.get("scene"), SCENE_MAX),
            "style_key": key,
            "style": str(data.get("style") or "").strip()[:120]}
    plan, notes = fit_plan(plan, lim["total"], lim)
    pages, why, pnotes = _pages_from_data(data, lim, key, has_photo, plan.get("title") or "",
                                          page_hint)
    if not pages and page_hint > 1:
        # 模型只交了平铺的一份（没写 pages[]），但用户已经定了页数：按用户的页数把要点切开
        one = {"title": plan.get("title") or "", "subtitle": plan.get("subtitle") or "",
               "cards": plan.get("cards") or [], "layout_key": plan.get("layout_key") or "",
               "style_key": key, "role": "封面", "footer": plan.get("footer") or "",
               "scene": plan.get("scene") or ""}
        pages, rn = _resplit([one], page_hint, lim)
        if rn:
            pnotes = [rn] + pnotes
    # 攻略页那一档默认配攻略皮：画面是素材真帧，插画风那几套配色压不住游戏截图。
    # 只在人没显式选风格时生效——人点过下拉就以人为准，模型和默认都不许改回去。
    # 三套攻略皮按整理稿 id 轮换，别十篇一个脸（见 guide_style_for）。
    if pages and not style_key:
        gc = sum(1 for p in pages if p.get("layout_key") == "guide")
        if gc and gc * 2 >= len(pages):
            key = guide_style_for((summary or {}).get("id"))
            for p in pages:
                p["style_key"] = key
    if pages:
        # 顶层那几个键仍然填第 1 页：列表页、发布取图、回读校验读的都是它们，不能断
        first = pages[0]
        plan.update({"title": first["title"], "subtitle": first["subtitle"],
                     "cards": first["cards"], "scene": first["scene"], "footer": first["footer"],
                     "style_key": key})
        if len(pages) > 1:
            plan["pages"] = pages
        if why:
            plan["why"] = why
        notes = pnotes + notes
    # 攻略页：模型只给了帧编号，这里换成真文件 + 程序写的图注。
    # 编号对不上就丢图并留一句人话——放一张不存在的图进版面，比不配图严重。
    if frames:
        bnotes = bind_frames(pages if pages else [plan], frames, lim)
        if bnotes:
            notes = bnotes + notes
    # 推荐快照：M4 算采纳率靠它跟最后真正出的那份对比（人改过什么一目了然）
    plan["recommended"] = {
        "page_count": len(pages) if pages else 1,
        "style_key": key,
        "layouts": [p["layout_key"] for p in pages] if pages
                   else [_page_layout(plan.get("layout_key"), key, has_photo, 1)[0]],
        "cards": [len(p["cards"]) for p in pages] if pages else [len(plan.get("cards") or [])],
        "why": why}
    return plan, _clip_publish(data.get("publish"), plan), lint_plan(plan, lim), notes


def adoption_stats(rows):
    """rows = [(recommended, final, manifest)] → 推荐采纳率。

    分母只算"有推荐记录的那些"：这套埋点上线前的历史包没有 recommended，
    混进分母会算出一个假的低采纳率，看着像推荐不准，其实是样本没埋点。
    哪个布局老被人改回去，就说明 §题材映射 里那条判据不对——这数字是拿来收紧规则的。
    """
    from collections import Counter
    from . import poster_typeset as ts
    labels = {k: v["label"] for k, v in ts.LAYOUTS.items()}
    page_ok = style_ok = lay_hit = lay_all = 0
    clicks, swaps = [], Counter()
    for rec, fin, m in rows:
        if int(rec.get("page_count") or 1) == int(fin.get("page_count") or 1):
            page_ok += 1
        if (rec.get("style_key") or "") == (fin.get("style_key") or ""):
            style_ok += 1
        rl, fl = rec.get("layouts") or [], fin.get("layouts") or []
        for i in range(max(len(rl), len(fl))):
            a = rl[i] if i < len(rl) else "（无此页）"
            b = fl[i] if i < len(fl) else "（无此页）"
            lay_all += 1
            if a == b:
                lay_hit += 1
            else:
                swaps["%s → %s" % (labels.get(a, a), labels.get(b, b))] += 1
        try:
            if m.get("preview_clicks") is not None:
                clicks.append(int(m.get("preview_clicks")))
        except (TypeError, ValueError):
            pass
    n = max(1, len(rows))
    return {"sampled": len(rows),
            "page": {"ok": page_ok, "n": len(rows), "rate": int(round(page_ok * 100.0 / n))},
            "style": {"ok": style_ok, "n": len(rows), "rate": int(round(style_ok * 100.0 / n))},
            "layout": {"ok": lay_hit, "n": lay_all,
                       "rate": int(round(lay_hit * 100.0 / max(1, lay_all)))},
            "avg_preview_clicks": round(sum(clicks) / float(len(clicks)), 1) if clicks else None,
            "top_swaps": [{"pair": k, "n": v} for k, v in swaps.most_common(3)]}


def _plan_chars_one(plan):
    n = len(plan.get("title") or "") + len(plan.get("subtitle") or "") + len(plan.get("footer") or "")
    for c in plan.get("cards") or []:
        n += len(c.get("t") or "") + len(c.get("d") or "")
    return n


# 一页装不下就翻页：页数上限 6。
# 以前定 4 的理由是"AI 底图按页数翻倍花钱"，但攻略页那一档画面用素材真帧、出图 0 元，
# 号主 2026-10-22 点名两页一个样太呆板、信息多就该摊到 3-4 页甚至更多 ——
# 所以上限放开到 6，钱那一头由界面在「出图」按钮上按页标出来（插画档 0.5 元/页，攻略档 0 元）。
PAGE_MAX = 6


def plan_pages(plan):
    """把文案计划归一成"页列表"。没有 pages 字段＝单页，老素材与老逻辑完全不变。

    每页缺的字段从顶层继承（style_key、footer、首页的 scene），空页和超上限的页
    在这里一次丢掉——后面排版、出图、落盘都可以无脑信任返回值的形状。
    """
    if not isinstance(plan, dict):
        return []
    raw = plan.get("pages")
    if not isinstance(raw, list) or not raw:
        return [plan]
    pages = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cards = [c for c in (item.get("cards") or []) if isinstance(c, dict)
                 and ((c.get("t") or "").strip() or (c.get("d") or "").strip())][:CARD_MAX]
        if not (item.get("title") or "").strip() and not cards:
            continue
        pages.append({
            "title": (item.get("title") or plan.get("title") or "").strip(),
            "subtitle": (item.get("subtitle") or "").strip(),
            "footer": (item.get("footer") or plan.get("footer") or "").strip(),
            "cards": cards,
            "scene": (item.get("scene") or (plan.get("scene") if not pages else "") or "").strip(),
            "style_key": (item.get("style_key") or plan.get("style_key") or "").strip(),
            "layout_key": (item.get("layout_key") or "").strip(),
            "role": (item.get("role") or "").strip(),
        })
        for _k in GUIDE_KEYS:
            if item.get(_k):
                pages[-1][_k] = item.get(_k)
        if len(pages) >= PAGE_MAX:
            break
    # 免责脚注**只留末页一句**：以前每页都从顶层继承同一句，一页一张图就印一次，
    # 跨素材还一字不差——在小红书那就是"批量搬运号"的视觉记号（2026-10-13 复盘）。
    # 只收"从顶层继承来的同一句"；哪一页自己写过不一样的话，那是编辑的决定，不动。
    if len(pages) > 1:
        group = (plan.get("footer") or "").strip()
        if group:
            for p in pages[:-1]:
                if (p.get("footer") or "").strip() == group:
                    p["footer"] = ""
    return pages or [plan]


def is_multi_page(plan):
    return len(plan_pages(plan)) > 1


def plan_has_text(plan):
    """有没有可预览的文案：单页看主标题；多页只要有一页有标题或有卡片就算有。"""
    if not isinstance(plan, dict):
        return False
    if (plan.get("title") or "").strip():
        return True
    for p in plan_pages(plan):
        if (p.get("title") or "").strip() or (p.get("cards") or []):
            return True
    return False


def plan_chars(plan):
    """海报上要渲染的总字数（多页时是整组的合计）。

    风格描述、主视觉与卡片配图（style / scene / cards[].v）都不算：它们只告诉模型
    "该画什么"，不会被落成图上的字，所以不占字数安全预算。
    """
    pages = plan_pages(plan)
    if len(pages) > 1:
        return sum(_plan_chars_one(p) for p in pages)
    return _plan_chars_one(plan or {})


def lint_visual(plan):
    """画面侧软校验：不阻断出图，只提醒"这张图大概又是空文字框"。"""
    bad = []
    cards = plan.get("cards") or []
    with_vis = [c for c in cards if (c.get("v") or "").strip()]
    if not (plan.get("scene") or "").strip():
        bad.append("没写主视觉（scene），模型只会画一排文字框")
    if cards and len(with_vis) < min(VISUAL_MIN, len(cards)):
        bad.append("%d 张卡片里只有 %d 张配了画面，建议每条都能画出一个具体物件"
                   % (len(cards), len(with_vis)))
    return bad


def plan_is_guide(plan):
    """整份 plan 是不是"攻略页"体裁（判据与 lint_plan 里那一处同源，别写第二份口径）。"""
    if (plan.get("layout_key") or "") == "guide":
        return True
    for pg in plan_pages(plan):
        if (pg.get("layout_key") or "") == "guide" or any(
                pg.get(k) for k in GUIDE_KEYS if k != "kicker"):
            return True
    return False


def lint_plan(plan, lim=None, card_min=None):
    """字数与结构门禁：越界就返回问题清单，调用方据此拒绝出图。

    lim 按排版模式给：程序排字看的是"版面放得下"，模型画字看的是"错字率安全线"。
    多页时逐页各查一遍（每页一份自己的预算，这正是"装不下就翻页"的意义）；
    页内卡片下限放宽到 1——末页常常只有一条结论，不该被判成不合格。
    """
    lim = lim or dict(MD_LIMITS)
    pages = plan_pages(plan)
    if len(pages) > 1:
        bad = []
        for i, p in enumerate(pages, 1):
            for msg in lint_plan(p, lim, card_min=1):
                bad.append("第 %d 页：%s" % (i, msg))
        if len(plan.get("pages") or []) > PAGE_MAX:
            bad.append("页数 %d 超过上限 %d 页" % (len(plan.get("pages") or []), PAGE_MAX))
        return bad
    if len(pages) == 1 and isinstance(plan.get("pages"), list):
        # pages 只有一页时也按那一页查：顶层可能只是空壳（脚本或模型只填了 pages）
        plan = pages[0]
    bad = []
    if not (plan.get("title") or "").strip():
        bad.append("主标题为空")
    cards = plan.get("cards") or []
    floor = CARD_MIN if card_min is None else card_min
    # 攻略页的收尾那一页常常没有"要点卡片"：一句提问 + 三个选项就是整页内容
    # （对标那套 05/05 就这样），封面那一页则常常只有标题 + 三四条要点、没有别的零件。
    # 所以带攻略页零件或干脆是攻略版式的，卡片下限放到 0，但仍要求这一页至少有一样能读的东西。
    # 判据别写 `any(GUIDE_KEYS)`：栏目名 kicker 也在 GUIDE_KEYS 里，而它是代码补到每一页的，
    # 于是"只有卡片的封面页"会被当成空白页拦下（2026-10-13 实测就拦在这）。
    guide_page = ((plan.get("layout_key") or "") == "guide"
                  or any(plan.get(k) for k in GUIDE_KEYS if k != "kicker"))
    if guide_page:
        if not (cards or plan.get("ask") or plan.get("warn") or plan.get("bignum")
                or plan.get("contrast") or plan.get("photos")):
            bad.append("攻略页这一张卡片、提问、警示、图都没有，是空白页")
        floor = 0
    if len(cards) < floor:
        bad.append("卡片只有 %d 张，少于 %d 张" % (len(cards), floor))
    if len(cards) > CARD_MAX:
        bad.append("卡片 %d 张，超过 %d 张" % (len(cards), CARD_MAX))
    for i, c in enumerate(cards, 1):
        if len(c.get("d") or "") > lim["cardD"]:
            bad.append("第 %d 张卡片说明超 %d 字" % (i, lim["cardD"]))
    total = plan_chars(plan)
    if total > lim["total"]:
        why = "版面会放不下并被截断" if lim is TS_LIMITS else "字数越多错字率越高，请删条目而不是缩字号"
        bad.append("合计 %d 字，超过上限 %d 字（%s）" % (total, lim["total"], why))
    # 过程词闸（2026-10-13 号主点名）：「访谈称」「口播」这类痕迹印在图上，玩家不需要看见。
    # 只报不自动改写：换词会连着改数字与口径，那种事得由人（或重拆）来定。
    for h in meta_hits(plan):
        bad.append("上图文字有过程词 —— " + h)
    return bad


def fit_plan(plan, limit=None, lim=None):
    """超预算时按价值高低确定性收敛，返回 (plan, notes)。

    模型实测经常多写十几个字（90 字 vs 80 线），直接判死等于让用户白等一次
    3 分钟的拆解。压缩顺序＝先砍末位卡片 → 再截最长说明 → 再削副标题与尾注，
    主标题与卡片标题最后动；全程不再调模型。收敛不了才交回 violations 报错。
    """
    lim = lim or dict(MD_LIMITS)
    limit = lim["total"] if limit is None else limit
    notes = []
    start = plan_chars(plan)
    # 第 0 步：先把每个字段各自裁到单条上限，让本函数不依赖调用方已做过裁剪
    plan["title"] = _clip(plan.get("title"), lim["title"])
    plan["subtitle"] = _clip(plan.get("subtitle"), lim["subtitle"])
    plan["footer"] = _clip(plan.get("footer"), lim["footer"])
    plan["scene"] = _clip_ws(plan.get("scene"), SCENE_MAX)
    if not plan.get("style_key"):
        plan["style_key"] = DEFAULT_STYLE_KEY
    for c in plan.get("cards") or []:
        c["t"], c["d"] = _clip(c.get("t"), lim["cardT"]), _clip(c.get("d"), lim["cardD"])
        c["v"] = _clip_ws(c.get("v"), CARD_VIS_MAX)
    if start <= limit:
        return plan, notes
    while plan_chars(plan) > limit and len(plan["cards"]) > CARD_MIN:
        dropped = plan["cards"].pop()
        notes.append("删掉末位卡片「%s」以压字数" % (dropped.get("t") or dropped.get("d")))
    for _ in range(24):
        over = plan_chars(plan) - limit
        if over <= 0:
            break
        cands = [c for c in plan["cards"] if len(c.get("d") or "") > 10]
        if not cands:
            break
        c = max(cands, key=lambda x: len(x["d"]))
        c["d"] = c["d"][:max(10, len(c["d"]) - over)]
        notes.append("截短卡片「%s」的说明" % c.get("t"))
    for key, label in (("subtitle", "副标题"), ("footer", "尾注")):
        over = plan_chars(plan) - limit
        if over > 0 and len(plan.get(key) or "") > 2:
            plan[key] = (plan.get(key) or "")[:max(2, len(plan[key]) - over)]
            notes.append("截短%s" % label)
    left = plan_chars(plan) - limit
    if left > 0:
        notes.append("仍超 %d 字，需人工删条目" % left)
    elif notes:
        notes.append("已从 %d 字压到 %d 字，发布前请复核文案是否还完整" % (start, plan_chars(plan)))
    return plan, notes


def build_prompt_text(plan, ratio="9:16"):
    """组装出图 prompt：画面要求与要渲染的文字**分成两块**写。

    分块是必须的：早期版本把两者混在一起说"渲染以下内容"，模型会把画面描述
    也当文案画上去，或者反过来因为文字太多而放弃画插画（实测图面占比接近 0）。
    所以这里明确标注哪些字要逐字画、哪些只是构图说明、并给出画面占比下限。
    """
    preset = _preset(plan)
    # 要渲染的字一律用「」框住：实测直接写"卡片1标题：X 说明：Y"，模型会把"说明："
    # 这两个字也当成文案画到图上。框起来 + 明令只画框内的字，才分得清哪是指令哪是内容。
    text_lines = ["主标题区：「%s」" % plan["title"]]
    if plan.get("subtitle"):
        text_lines.append("副标题区：「%s」" % plan["subtitle"])
    for i, c in enumerate(plan.get("cards") or [], 1):
        text_lines.append("卡片%d标题区：「%s」 说明区：「%s」"
                          % (i, c.get("t") or "", c.get("d") or ""))
    if plan.get("footer"):
        text_lines.append("尾注区：「%s」" % plan["footer"])
    lines = ["一张%s中文信息图海报。" % RATIO_LABEL.get(ratio, "竖版 9:16"),
             "",
             "【画风与配色 · 严格执行】",
             resolve_style_text(plan)]
    scene = (plan.get("scene") or "").strip() or (preset.get("scene") or "").strip()
    card_visual = (preset.get("card_visual") or "").strip()
    visuals = []
    if scene:
        visuals.append("主视觉：%s" % scene)
    for i, c in enumerate(plan.get("cards") or [], 1):
        v = (c.get("v") or "").strip()
        if v:
            visuals.append("卡片%d配图：%s" % (i, v))
    if visuals:
        lines += ["",
                  "【画面构成 · 下面这些只是要画出来的图像，一个字都不许写成文字放到图上】",
                  "\n".join("- " + x for x in visuals)]
        if card_visual:
            lines.append("- 配图样式：%s" % card_visual)
    lines += ["- 插画与图标合计要占住整张图 50%% 以上的面积，%s"
              % ("上半幅留给主视觉插画，文字卡片压在下半幅" if ratio in ("9:16", "3:4")
                 else "左侧留给主视觉插画，文字卡片排在右侧"),
              "- 严禁画成一整页纯文字排版，严禁只有空边框没有图像",
              # 实测两次翻车方式：插画里的简历/信封被顺手写上字；三张卡片标题全被复制成主标题
              "- 插画里的纸张、屏幕、招牌、信封一律用色块和线条表示，不许出现任何文字或字母",
              "- 每张卡片的标题与说明都各不相同，卡片标题严禁重复主标题里的字样"]
    lines += ["",
              "【必须逐字渲染的简体中文 · 只画「」里面的字，「」本身和「主标题区/说明区」这类位置名一律不要画"
              " · 不得增删改任何字符 · 除此之外不得出现任何其它文字】",
              "\n".join(text_lines),
              "",
              "【版面骨架 · 上面每一行都要占住自己那块位置，一条不许少、一条不许重复】",
              "- 最上方留出主标题区（字号最大）与副标题区（次之），两块都不能省",
              "- 中部是卡片区，每张卡片只出现一次自己的标题和说明，严禁两张卡片写同一句",
              "- 最下方留出尾注小灰字区，字号最小但必须存在"]
    return "\n".join(lines)


def _canvas_px(ratio):
    w, h = (SIZES.get(ratio) or SIZES["9:16"]).split("*")
    return int(w), int(h)


# ── 无字底图专用：把「画面线索」洗成能安全交给出图模型的话 ──
# 这些词一进 prompt，模型就真会把带字的东西画进来。2026-10-11 拿豆包 Seedream 4.5
# 实测：scene 里那句「一个角色在门前进度条」「三种材料图标」「剑身刻有狼头符文」
# 直接对应四张底图里三张画出发光 UI 面板 + 鬼画符，白扔了 4 次生成。
# 「与 NPC 对话」也一样——对话必带气泡，气泡里必带字。
# 判定：这类线索对底图是负资产，宁可丢掉那条装饰，也不留着它勾出字。
_ART_DROP_RE = re.compile(
    r"(进度条|血条|蓝条|读条|界面|屏幕|显示器|窗口|面板|仪表盘|表格|图表|曲线|清单|列表|"
    r"截图|二维码|文字|字样|字体|标签|贴纸|印章|签名|水印|logo|LOGO|Logo|标题栏|按钮|"
    r"图标|排行榜|榜单|评分|打分|箭头|对话|聊天|说话|气泡|对话框|"
    r"数字|数值|写着|标注|铭牌|牌匾|名字|标题|等级显示)")
# 只画外形就够的：把"刻着字"换成"带光纹"，主体不丢
_ART_FIX = [("刻有", "带着"), ("刻着", "带着"), ("符文", "发光纹路"), ("铭文", "花纹"),
             ("刻字", "花纹"), ("碑文", "石碑"), ("招牌", "木牌"), ("书页", "空白书页")]
# 引号里的装备名／人名：模型会照着把「狼独草」仨字画在剑身上，一律摘掉
_QUOTE_RE = re.compile(u"[“\"『「][^”\"』」]{1,24}[”\"』」]")
# 数字同理：底图上的数字是插画里的鬼画符，真实数字由程序排上去。
# 只换数字、单位留着（"40 级后"→"若干级后"）：连单位一起吃掉会写成"若干后"这种断句
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_PUNCT_DUP_RE = re.compile(r"[，、]{2,}")


def _art_safe_text(text):
    """洗一段画面描述：去引号内的实名、把数字换成"若干"、把"刻字"改成"光纹"、清标点。

    洗完还剩东西才回给调用方；什么都不剩回空串，让上层把这一行整条删掉，
    免得留下「主视觉：」后面跟个空的怪句子。

    数字换成「若干」而不是直接删：实测直接删会得到「手握发光大剑，后在门口排 人」
    这种断句，模型看得懂但画面线索也丢了；「若干级后在门口排若干个人」既不带数字
    又不残句。
    """
    s = _QUOTE_RE.sub("", str(text or ""))
    for a, b in _ART_FIX:
        s = s.replace(a, b)
    s = re.sub(r"若干(?:\s*若干)+", "若干", _NUM_RE.sub("若干", s))
    # "排 3 个人"换完是"排 若干个人"，中文里那格空格是残渣，只清「若干」前后的
    s = re.sub(r"\s+(?=若干)|(?<=若干)\s+", "", s)
    s = _PUNCT_DUP_RE.sub("，", s).strip("，、 ：:；;")
    return s


# 主视觉那句不能整条丢（它就是这张图的主角），所以改用"换掉诱饵词"而不是"丢掉这条"：
# 界面/屏幕/图标/进度条这些东西在真实海报里由程序画，底图只要留个发光的形就够了。
# 实测豆包拿到"商栈界面打开，坐骑图标闪烁"就画出一块带假字的 UI 面板。
_ART_SCENE_FIX = [("界面截图", "场景全景"), ("登录界面", "走进场景"), ("伤害数字", "一团爆发光效"),
                  ("等级显示", "头顶一圈光环"), ("数字", "光效"), ("数值", "光带"),
                  ("进度条", "发光的能量条"), ("血条", "发光的能量条"), ("读条", "一段光"),
                  ("列表", "一列色块"), ("清单", "一列色块"), ("表格", "色块阵列"),
                  ("图表", "发光曲线"), ("屏幕", "发光箱体"), ("显示器", "发光箱体"),
                  ("写着", "带着"), ("标注", "描边"), ("窗口", "光框"), ("面板", "石板"),
                  ("排行榜", "一列光条"), ("图标", "小图形"), ("按钮", "发光方块"),
                  ("二维码", "光纹"), ("标签", "色块"), ("文字", "纹理"), ("字样", "纹样"),
                  ("水印", "斑痕"), ("对话", "并肩而立"), ("气泡", "光晕"), ("箭头", "一道光束"),
                  ("界面", "场景")]

# 主题词切分：标题「狼独草：前期亡灵圣骑神器」→ ["狼独草", "前期亡灵圣骑神器"]
_ART_SPLIT_RE = re.compile(r"[：:，,、。/·—\-\s！？!?（）()《》]+")


def _art_topic_words(plan):
    """这篇要排出去的主题词（主标题/副标题里的词段），底图里一个都不许再出现。

    实测第 4 页模型把「背上的圣光大剑狼独草发出光芒」照字画成了剑身上的三个汉字——
    而「狼独草」本来就是程序要排上去的主标题。同一句话被画两遍，既丑又容易错字。
    """
    out = []
    for key in ("title", "subtitle"):
        for seg in _ART_SPLIT_RE.split(str((plan or {}).get(key) or "")):
            seg = seg.strip()
            if len(seg) >= 2 and seg not in out:
                out.append(seg)
    return out[:8]


def _art_scene_text(text, topic_words=()):
    """主视觉与道具那条线专用：换掉诱饵词、摘掉主题实名，只留能被画成"形"的描述。"""
    s = _art_safe_text(text)
    for a, b in _ART_SCENE_FIX:
        s = s.replace(a, b)
    for wd in topic_words:
        s = s.replace(wd, "")
    s = re.sub(r"[，、]{2,}", "，", s)
    return s.strip("，、 ：:；;")


def build_art_prompt(plan, ratio="9:16", extra_topics=None):
    """程序排字模式用的出图 prompt：只要一张**没有字的底图**。

    分区比例由 poster_typeset 按卡片内容算出来，出图和排版共用同一个函数，
    所以模型让出来的地方正好是程序要落字的地方；卡片从 3 张变 5 张，这块自己伸缩。

    2026-10-11 按豆包实测重排过顺序，三条都是被翻车教出来的：
    ① 站位写在最前面（原来「主视觉」那条抢在第一句，主体就被画到画面正中，
       该空的那半截塞满了发光面板）；末尾再复述一次，只说一次压不住。
    ② 通篇不出现 Markdown 星号，也不出现"文字区/留给文字"这种说法——
       提"文字"它就去画字，改成描述那块**是什么**（一片渐变、一层雾）。
    ③ 大段"禁止…"清单换成一句底线 + 让给人填负面提示栏：扩散模型读不懂否定，
       把汉字/数字/屏幕/卷轴列一串，等于把这些东西挨个念给它听（原第 3 条还硬
       写了"仪表盘、日历、面板"，一张魔兽海报的底图就这么被勾成了 UI 截图）。
    """
    from . import poster_typeset as ts
    preset = _preset(plan)
    zone = ts.art_zone(plan, _canvas_px(ratio), (plan or {}).get("_skin"))
    skin = ts.skin_for(plan.get("style_key"))
    art_line, trans_line, rest_short = ts.art_zone_hint(zone, skin)
    # 这一页自己要排出去的字（页标题/副标题里的词段）先从画面描述里摘掉：
    # 同一句话不该被画两遍，模型照着画还会错字。整组的主标题由调用方从 extra_topics 递进来
    topic = _art_topic_words(plan)
    for wd in (extra_topics or []):
        if wd not in topic:
            topic.append(wd)
    scene = _art_scene_text((plan.get("scene") or "").strip() or (preset.get("scene") or "").strip(),
                            topic)
    # 卡片配图栏在"模型画字"那条路上是给卡片画小图用的；底图模式下它只是主视觉的道具线索。
    # 先把诱饵词换成不带字的形，换完仍然读不出画面（还带数字/字体那类词）的就丢掉，宁缺毋滥
    props = [_art_scene_text(c.get("v"), topic) for c in (plan.get("cards") or [])]
    props = [x for x in props if x and not _ART_DROP_RE.search(x)][:4]
    # 底图只要画风与配色那段：卡片面板和字体那两样在程序排字模式下由代码负责，
    # 让模型去画反而会跟我们排的字打架
    style_only = preset.get("mood") or resolve_style_text(plan).split("。图上的中文")[0]
    lines = ["一张%s的信息图底图。这张图上现在没有任何字，中文之后由程序排上去。"
             % RATIO_LABEL.get(ratio, "竖版 9:16"),
             "",
             "【第一步：先定站位 · 这张图最容易做错的就是这句】",
             "- %s" % art_line,
             "- %s" % trans_line,
             "",
             "【画风与配色 · 严格执行】",
             style_only,
             # 四套预设的画风原文是给"模型画整张海"写的，里面带着"悬浮屏幕""毛玻璃面板"
             # "手绘下划线""信息卡片纵向排列"这类词——那条路上卡片真该由模型画。底图这条路
             # 卡片和面板由代码画，这些词留着就是勾着它画 UI。改一句总闸，比逐套预设改写稳。
             "- 上面这段里凡是提到卡片、边框、面板、界面、屏幕、勾选框、下划线的，"
             "一律按「只画背景氛围」理解：那些东西之后由程序画上去，这张底图里不要有。",
             "",
             "【画面内容 · 只画外形、光影和材质，表面什么都不许写】",
             "- 主视觉：%s" % (scene or "按上面画风自行构图"),
             ("- 画面里可以顺带出现的道具：%s" % "；".join(props)) if props else None,
             "",
             "【底线】",
             "- 整幅画面里不许出现汉字、字母、数字、标点、符号、logo、印章；"
             "纸张、屏幕、木牌、石碑、书本一律只画外形和色块，表面空白。",
             "- 最后核对一遍：%s 那一块，除了底色和极淡的底纹必须是空的，"
             "主体和任何道具都不许越进去。" % rest_short]
    return "\n".join([l for l in lines if l is not None])


# 给人看的操作说明，分两档：默认那档（程序排版）与「直出成品」那档（模型把字画好）。
# 为什么分开写：两档对"这张算不算能用"的判断完全相反——排版档要那半截空着、有字就废；
# 直出档反过来要模型把字画全，检查的是字对不对。合成一段只能写成"看情况"，那是废话。
_MANUAL_COMMON = """──────────────── 下面是给人看的操作说明，不要粘进豆包 ────────────────
1. 只把上面从「一张…」到最后一个【】块那一段复制进豆包，生成。
   豆包要是有「不想要 / 负面提示」这一栏，往里填：
   文字、汉字、字母、数字、标点、logo、水印、UI 界面、进度条、卡片边框、表格、截图
   没有这一栏就跳过，正文里已经把同样的话写过了。
2. 比例选 9:16，尺寸按上面那个像素数；选不到就让它按这个长宽比出。
3. 豆包一次给你 4 张，这是它的默认行为，不是出错了。一页只用一张，另外 3 张不用管，
   更别一起传上来——工作台一页只吃一张。
4. 导出用「下载 / 另存为」，不要截图。截图掉清晰度，字边缘会发糊。
   右下角「豆包 AI 生成」是平台后盖的水印，提示词拦不住，它在角落不用管。
5. 一组几页就要几页的图，少一张工作台当场拒收。走这条路 0 元
   （不扣 qwen-image 那 0.5 元/张），不满意就回豆包重出再传。
"""

MANUAL_FOOT = _MANUAL_COMMON + """【排版档 · 这张要当底图用】
6. 挑那张「该空的那半截真的空着、画面上读不出任何字」的。中文是工作台排上去的，
   豆包画了字就等于跟我们要排的字打架。
7. 四张都挑不出来：把正文【第一步】里说空带那句抄到提示词最前面再说一遍；
   还不行就让它只画纯背景底纹，回来照样能排字。
8. 传上来工作台会自己体检那一块空不空、图上有没有字，不合格会在结果第一行点名第几页。
"""

MANUAL_FOOT_DIRECT = _MANUAL_COMMON + """【直出档 · 这张就是成品】
6. 这一档中文由豆包画，所以挑那张**字最全、最清楚**的：把图上的字跟我们列的文案逐字对一遍，
   多字、少字、数字被改写、笔画画错都算废。
7. 实测这一档做不到每张都对（模型会把"10月8开启"写成"10月8日"、把数字加一位），
   而自动回读用的也是视觉模型、它会把错字读成对的——**所以这一档必须你本人逐字看**。
   字多（>64 字）就别用这档，切回「排版」，那档字不会错。
8. 传上来工作台会跑一次回读校验，缺字漏字会在结果第一行点名，但"通过"不等于字对。
"""


def _ts_is_guide(page):
    """这一页是不是攻略页。poster_typeset 拿不到（极端导入环境）就当不是，别整条挂掉。"""
    try:
        from . import poster_typeset as _t
    except ImportError:
        try:
            import poster_typeset as _t
        except ImportError:
            return False
    try:
        return bool(_t._is_guide(page))
    except Exception:
        return False


def guide_only_plan(plan):
    """整组是不是都走攻略页（画面=素材真帧）。界面据此决定要不要摆"去豆包传底图"那一块。

    攻略页压根不需要底图：给它一份"画一张无字底图"的提示词是自相矛盾的指令——
    他真去传一张回来，反而把真帧的位置盖掉。
    """
    pages = plan_pages(plan) or [plan or {}]
    return bool(pages) and all(_ts_is_guide(p) for p in pages)


def manual_prompts_for(plan, ratio="9:16", text_mode=None):
    """整组（每页一份）手动出图材料。plan 里没有 pages 就是单页。

    每页回两个字段：`copy` 是要粘进豆包的那段（含尺寸与要求，不含操作说明），
    `full` 是给人看的完整版。分开的理由很直接：把「回工作台点上传」那句粘进豆包，
    模型可能真给你画一个上传按钮出来。界面上一律只显示 copy，说明走「复制全文」。
    """
    # 必须走 plan_pages：模型交的 pages[] 里每页常常不带 style_key，直接拿来用会
    # 让 _preset 落空、皮肤回落到默认的 tool_review——实测第 1/4 页那条提示词因此
    # 既写了「深蓝紫渐变」又写了「信息卡片纵向排列」，风格串了、还勾着模型画卡片框。
    # plan_pages 会把顶层的 style_key/footer/scene 继承到每一页，和花钱出图那条同一份算法。
    pages = plan_pages(plan) or [plan or {}]
    if pages and all(_ts_is_guide(p) for p in pages):
        # 攻略页的画面是素材真帧，压根不叫出图模型——这里再给一份"去豆包画无字底图"
        # 就是自相矛盾的指令：他真传一张回来，反而把真帧的位置盖掉。
        return []
    # 整组的主标题也要从每一页的画面描述里摘掉：第 1 页的插画最容易顺手把主题名画上去
    group_topics = _art_topic_words(plan)
    mode = text_mode or DEFAULT_TEXT_MODE
    direct = mode != "typeset"
    tag = "成品图（中文也画）" if direct else "无字底图"
    foot = MANUAL_FOOT_DIRECT if direct else MANUAL_FOOT
    out = []
    for i, p in enumerate(pages, 1):
        w, h = _canvas_px(ratio)
        body = (build_prompt_text(p, ratio) if direct
                else build_art_prompt(p, ratio, extra_topics=group_topics))
        head = ["【第 %d/%d 页 · %s】" % (i, len(pages), tag) if len(pages) > 1
                else "【%s】" % tag,
                "目标尺寸：%d×%d（%s）。选不到这个比例就按该长宽比出图。"
                % (w, h, RATIO_LABEL.get(ratio, "竖版 9:16"))]
        copy = "\n".join(head) + "\n\n" + body
        out.append({"page": i, "total": len(pages), "mode": mode, "copy": copy,
                    "full": copy + foot})
    return out


def check_base_numbers(base_png, plan, ai_config):
    """复用底图时专设的一道提醒：底图是上一期那张，插画里可能写着上一期的数字。

    我们排上去的字由代码保证不会错，但模型在插画里顺手写的数字管不着——实测这张
    388 的底图里就烙着 3600 / 10.8 / 10.22 / 5168。换一期内容时那些数很可能已经变了，
    而版面自检和回读都查不出它（那些字不在文案里，本来就不该一致）。
    所以这里只比数字：底图里出现、整份文案里却找不到的，列出来提醒人看一眼。
    """
    try:
        v = verify_image(base_png, plan, ai_config)
    except Exception:
        return []
    # 只扫 transcript，别扫 extra：extra 是把命中的文案从整段里抠掉后剩下的残渣，
    # 中间没有分隔符（"20018层360010.8..."），拿它比数字只会报出一串没人看得懂的乱码。
    blob = v.get("transcript") or ""
    want = json.dumps(plan, ensure_ascii=False)
    return sorted({n for n in re.findall(r"\d{2,}", blob) if n not in want})


# 回传底图的体检：问视觉模型「程序要排字的那一块里有没有东西」。
# 为什么只问这一块：出图模型对「上部 56% 留给文字」这种比例指令经常直接无视
# （2026-10-11 豆包 Seedream 4.5 四张全把发光面板画进了要排字的区域），而人对着
# 缩略图也量不出边界在哪。判据取自排版层同一个 art_zone，问的位置就是排字的位置。
_BAND_CHECK_PROMPT = """这张图是一张还没有排字的海报底图：程序之后要往「%s」这一块排上中文，所以这一块现在应该只有背景。
只看这一块回答前两个问题，别管其余区域；第三个问题看整张图：
1. 这一块里有没有字（汉字、字母、数字、标点，糊成一团的假字也算）
2. 这一块里有没有具体物体（卡片、边框、面板、屏幕、图标、人物、道具都算；纯渐变、雾气、纸纹、光点不算）
3. 把整张图里能读到的字逐字抄下来，一个都别漏，没有就留空
只输出 JSON，不要解释：
{"band_text":true/false,"band_object":true/false,"all_text":"抄下来的字","note":"不超过 30 个中文字，说这一块里画了什么"}"""

# 平台在生成之后盖上去的水印：提示词拦不住，位置也在角落，程序排字会盖住，所以不算不合格
_WATERMARK_WORDS = ("豆包", "AI生成", "AI 生成", "即梦", "可灵", "智谱", "通义", "千问",
                    "AIGC", "Generated", "generated", "水印")


def _base_stray_text(all_text):
    """剥掉平台水印词，看这张底图上还剩多少"不该存在的字"。空串才算干净。"""
    s = str(all_text or "")
    for wd in _WATERMARK_WORDS:
        s = s.replace(wd, "")
    return re.sub(r"[\s，。、·．.]+", "", s)[:40]


def check_base_band(base_png, page_plan, ai_config, ratio="9:16", timeout=120):
    """回传底图专项体检：该空的那块被画上字/物体了没有，顺带把整张图上的字抄下来比一遍。

    只回警告用，不拦路——那块被占了人还可以照常用（程序排的字带半透明底板，压得住），
    但必须让他知道是哪一页、占成什么样，否则下次还是白抽四张。
    模型调用失败时回 checked=False，绝不能因为体检跑不通就把人的图判死。
    """
    from . import poster_typeset as ts
    out = {"band": "", "band_text": False, "band_object": False, "note": "", "why": "",
           "stray": "", "numbers": [], "bad": False, "checked": False}
    try:
        w, h = _canvas_px(ratio)
        zone = ts.art_zone(page_plan, (w, h), (page_plan or {}).get("_skin"))
        skin = ts.skin_for((page_plan or {}).get("style_key"))
        band = ts.art_zone_hint(zone, skin)[2]
        out["band"] = band
        api_key, api_base, _ = _require_ai(ai_config)
        import openai

        client = openai.OpenAI(api_key=api_key, base_url=api_base or "https://api.openai.com/v1",
                               timeout=timeout, max_retries=1)
        b64 = base64.b64encode(base_png).decode()
        resp = client.chat.completions.create(
            model=_ocr_model(ai_config),
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
                {"type": "text", "text": _BAND_CHECK_PROMPT % band}]}],
            max_tokens=600)
        data = _parse_json(resp.choices[0].message.content or "") or {}
    except Exception as e:
        out["note"] = "体检没跑成（%s）" % str(e)[:60]
        return out
    all_text = str(data.get("all_text") or "")
    want = json.dumps(page_plan, ensure_ascii=False)
    # 整张图上有字就算不合格，光查那块空带不够：实测模型会把装备名（狼独草）直接刻在剑身上，
    # 而剑身在插画区。平台水印已经在上一步剥掉了。
    stray = _base_stray_text(all_text)
    out["stray"] = stray
    out.update({"band_text": bool(data.get("band_text")), "band_object": bool(data.get("band_object")),
                "note": str(data.get("note") or "")[:60], "checked": True,
                "numbers": sorted({n for n in re.findall(r"\d{2,}", all_text) if n not in want})})
    out["bad"] = bool(out["band_text"] or out["band_object"] or out["stray"])
    why = []
    if out["band_text"]:
        why.append("要排字那块上有字")
    if out["band_object"]:
        why.append("要排字那块被画了具体物体")
    if out["stray"]:
        why.append("画面上有字：%s" % out["stray"])
    out["why"] = "、".join(why)
    return out


def template_base(style_key):
    """模板带来的底图字节。有它就跳过出图，换文案复用同一张底图，成本 0 元。"""
    if not style_key or style_key in STYLE_PRESETS:
        return None
    try:
        from . import poster_templates
        path = poster_templates.base_image_path(style_key)
        return path.read_bytes() if path else None
    except Exception:
        return None


def layout_choices(guide_only=True):
    """布局名单下发给界面：唯一权威源在排版层，前端抄一份迟早对不上。

    `guide_only=True`（界面那一档默认）：只给「攻略页」这一种构图，理由同 `style_choices`——
    号主说"看版面也是看攻略页"。十种构图本身一套都没删，排版层与拆解提示词还在用全名单。
    """
    from . import poster_typeset as ts
    items = ts.layout_choices()
    if guide_only:
        items = [x for x in items if x["key"] == "guide"]
    return items


def typeset_poster(png_bytes, plan, ratio="9:16", photo=None, page=None):
    """把中文用真字体排到无字底图上。返回 (成品字节, 版面自检 dict)。

    photo 是用户上传的真实图片字节：非空时它会顶掉整块插画区，标题压在图上。
    走哪个布局看 plan["layout_key"]，没给就用这套风格的默认布局。
    page=(第几页, 总页数) 时右上角画一颗「1 / 3」，多页组靠它告诉读者后面还有。
    """
    from . import poster_typeset as ts
    return ts.typeset(png_bytes, plan, ratio, photo=photo, page=page)


def preview_typeset(plan, ratio="9:16", photo=None, page=None, base=None):
    """不花钱的版面预览：占位底图 + 真排版函数，尺寸与配色都和成品一致。

    为什么值得单独做一个入口：换轨之后"版面"是代码算的，跟出图没关系，
    所以折行、字号、卡片放不放得下、配色对不对，全都能在花钱之前看到。
    看不到的一件事是插画本身画得好不好——那只能出图。
    传了 photo 就连真图一起排进预览，这块也不再是"看不到"的东西。
    base 非空时直接用那张底图（复用模板底图那条路），不再画占位灰框。
    返回 (png 字节, 版面自检 dict)。
    """
    from . import poster_typeset as ts
    if ratio not in SIZES:
        ratio = "9:16"
    w, h = _canvas_px(ratio)
    skin = plan.get("_skin") if isinstance(plan.get("_skin"), dict) else None
    if base is None:
        zone = ts.art_zone(plan, (w, h), skin)
        base = ts.placeholder_base(plan.get("style_key"), w, h, zone["frac"], skin,
                                   art_hint=not photo, plan=plan, zone=zone)
    return ts.typeset(base, plan, ratio, skin, photo=photo, page=page)


def preview_pages(plan, ratio="9:16", photo=None):
    """整组免费预览：一页一张，全部 0 元（占位底图或模板底图 + 真排版）。

    真图只给第一页当主视觉——同一张图在第 2、3 页再出现一次很难看。后面的页
    优先复用这套风格已经验收过的模板底图（还是 0 元），没有模板才留灰框，
    并在自检里说明这一页出图时要花那 5 毛。
    """
    pages = plan_pages(plan)
    total = len(pages)
    out = []
    for i, p in enumerate(pages, 1):
        ph = photo if (i == 1 or not photo) else None
        base = None
        if photo and not ph:
            base = template_base(p.get("style_key"))
        pg = (i, total) if total > 1 else None
        png, chk = preview_typeset(p, ratio, photo=ph, page=pg, base=base)
        if total > 1 and photo and not ph:
            chk["base_note"] = ("模板底图（0 元）" if base
                                else "这页没有可复用的底图：出图档已撤（不再自动叫模型画），"
                                     "换一套带底图的风格，或用「＋ 用你的图」回传一张")
        out.append((p, png, chk))
    return out


# ── 2. 出图 ──
# 官方口径（阿里云百炼 qwen-image-max）：0.5 元/张、限流 RPM=2。
# 实测连发 5 张时第 5 张必撞 "Requests rate limit exceeded"，所以这里既退避也节流。
PRICE_PER_IMAGE_YUAN = 0.5
RATE_LIMIT_WAIT_SEC = 31      # RPM=2 → 相邻两次请求至少隔 30s，留 1s 余量
MAX_ATTEMPTS = 3
_last_call_at = [0.0]


def _throttle():
    """把相邻出图请求拉开到 RPM=2 允许的间隔，批量跑时不需要调用方自己 sleep。"""
    wait = RATE_LIMIT_WAIT_SEC - (time.time() - _last_call_at[0])
    if wait > 0:
        time.sleep(wait)
    _last_call_at[0] = time.time()


def _rate_limited(status_code, text):
    return status_code == 429 or "rate limit" in text.lower() or "throttl" in text.lower()


def _get_image_bytes(img_url, tries=3, timeout=180):
    """把对面那张已生成的 PNG 取回来。**这一跳不花钱，所以断了必须重试。**

    实测 2026-10-15 22:51 号主点出图：生成那一步成了（返回了 OSS 链接、0.5 元已经扣），
    下载这一跳 TLS 直接 EOF（`EOF occurred in violation of protocol`），整单被判失败。
    链接上带 `Expires`，24 小时内还有效——也就是说图在对面躺着，我们只是没拿回来。
    以前这里是一发裸 `requests.get`：网络抖一下就把已经花掉的钱和另外几页一起废掉。
    """
    last = ""
    for n in range(1, tries + 1):
        try:
            r = requests.get(img_url, timeout=timeout)
            r.raise_for_status()
            if len(r.content) > 4096:
                return r.content
            last = "只拿回 %d 字节，不像一张图" % len(r.content)
        except Exception as e:
            last = str(e)[:160]
        if n < tries:
            time.sleep(2 * n)          # 退避着再拿，别对着一个刚抖完的链路连打
    raise PosterError("图已经生成了，但下载不下来（重试 %d 次）：%s。"
                      "这个链接 24 小时内还有效，再点一次出图会重新生成、再扣一次钱；"
                      "先别急，隔一会儿网络稳了再点。" % (tries, last))


def generate_image(prompt_text, size, ai_config, timeout=300):
    """调百炼原生 multimodal-generation 出图，返回 (png 字节, usage)。

    注意三件实测事实：/images/generations（OpenAI 形状）对这些模型是 404；异步
    text2image 会被拒「current user api does not support asynchronous calls」；
    模型限流 RPM=2，撞限流要退避重试而不是直接失败。返回的 OSS 链接会过期，当场下载。

    2026-10-29 起这一档默认没模型：出图档已被号主撤掉，没配 `AI_MODEL_IMAGE` 就直接拒绝，
    免得哪一次"没有底图"悄悄拿一个写死的模型去扣钱。
    """
    model = _image_model(ai_config)
    if not model:
        raise PosterError(
            "出图这一档已经撤掉（不再调模型画底图）。要画面有两条现成的路："
            "① 用素材真帧出攻略页（0 元）；② 复制提示词去豆包出图再回传。"
            "确实要恢复自动出图：在 config/config.env.local 加一行 AI_MODEL_IMAGE=模型名，不用重启。")
    api_key, api_base, _ = _require_ai(ai_config)
    url = _api_root(api_base) + "/api/v1/services/aigc/multimodal-generation/generation"
    payload = {"model": model,
               "input": {"messages": [{"role": "user", "content": [{"text": prompt_text}]}]},
               "parameters": {"size": size}}
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
               "X-DashScope-Async": "disable"}
    last = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _throttle()
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            # 连接级失败和"限流"不是一回事：对面很可能已经画完并扣了钱，只是回包没到本机
            # （实测 2026-10-15 22:49 那次是 300 秒读超时）。仍然重试，但最后一次必须把
            # "可能已经扣过钱"这句说出来，别让号主以为白等了一场、可以放心连点三次。
            last = "连不上/等不到回包：%s" % str(e)[:140]
            if attempt < MAX_ATTEMPTS:
                continue
            raise PosterError("出图这一跳没通（试了 %d 次）：%s。"
                              "超时那几次对面可能已经出图并扣费，先去百炼控制台看用量再决定要不要再点。"
                              % (MAX_ATTEMPTS, last))
        try:
            data = r.json()
        except ValueError:
            raise PosterError("出图接口返回非 JSON（HTTP %s）：%s" % (r.status_code, r.text[:200]))
        if "output" in data:
            content = ((data.get("output") or {}).get("choices") or [{}])[0] \
                .get("message", {}).get("content") or []
            img_url = ""
            for item in content:
                if isinstance(item, dict) and item.get("image"):
                    img_url = item["image"]
                    break
            if not img_url:
                raise PosterError("出图接口没返回图片地址：%s" % str(data)[:200])
            # OSS 链接会过期，当场下载；断了要重试（这一跳不花钱，见 _get_image_bytes）
            return _get_image_bytes(img_url), (data.get("usage") or {})
        last = str(data.get("message") or data.get("code") or
                   ("HTTP %s %s" % (r.status_code, str(data)[:200])))
        if _rate_limited(r.status_code, last) and attempt < MAX_ATTEMPTS:
            time.sleep(RATE_LIMIT_WAIT_SEC)
            continue
        raise PosterError("出图失败：%s" % last[:300])
    raise PosterError("出图失败（重试 %d 次仍被限流）：%s" % (MAX_ATTEMPTS, last[:200]))


# ── 3. OCR 回读校验 ──
_PUNCT_RE = re.compile(r"[\s，。、！？：；「」『』“”\"'（）()《》·—…\-]+")


def _norm(text):
    return _PUNCT_RE.sub("", str(text or ""))


def _ocr_model(ai_config):
    """看图那档模型名：配置给了 AI_MODEL_VISION 就用它，否则用这里的默认值。

    写死一个名字的代价已经付过一次（见 OCR_MODEL 那段），所以三道看图检查统一走这里。
    """
    return ((ai_config or {}).get("vision_model") or "").strip() or OCR_MODEL


def verify_image(png_bytes, plan, ai_config, timeout=120):
    """转录图面文字并与文案计划比对，返回 {ok, missing, extra, transcript}。

    比对前先剥空白与标点：模型常把主标题排成两行，逐行比对会误报缺失。
    """
    api_key, api_base, _ = _require_ai(ai_config)
    import openai

    client = openai.OpenAI(api_key=api_key, base_url=api_base or "https://api.openai.com/v1",
                           timeout=timeout, max_retries=1)
    b64 = base64.b64encode(png_bytes).decode()
    resp = client.chat.completions.create(
        model=_ocr_model(ai_config),
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
            {"type": "text", "text": "逐字转录图中所有简体中文文字，按从上到下顺序每行一条，只输出文字本身"}]}],
        max_tokens=800)
    transcript = resp.choices[0].message.content or ""
    blob = _norm(transcript)
    expected = [plan.get("title"), plan.get("subtitle"), plan.get("footer")]
    for c in plan.get("cards") or []:
        expected.extend([c.get("t"), c.get("d")])
    missing, rest = [], blob
    for e in expected:
        ne = _norm(e)
        if not ne:
            continue
        if ne not in blob:
            missing.append(str(e).strip())
        rest = rest.replace(ne, "", 1)
    return {"ok": not missing, "missing": missing, "extra": rest[:80],
            "transcript": transcript.strip()}


# ── 4. 出图后视觉评审：只答"好不好看"，与回读校验互补 ──
# 回读校验能查出"字画错了"，查不出"字全对但一张空文字框"。竞品图文的差距几乎全在
# 后者：画面占比、层级、对比度。所以再挂一道视觉模型打分，把主观差距变成可比较的数字，
# 也让人在多张候选里挑得有依据，而不是凭一眼感觉。
_REVIEW_PROMPT = """你在评审一张由 AI 画出来的中文信息图海报（图上的中文文字是画进像素的，不是排版的）。
按下面五项逐条打分，只能给 1-5 的整数，1 分很差、5 分很好：
- overflow 文字完整：有没有字被裁掉、糊成一团、超出边框、出现乱码或假字
- hierarchy 层级清晰：能不能一眼看出先看哪里；主标题/卡片/尾注是否分明
- visual 画面占比：除文字外有没有真正的插画或图标（不是空边框），估算它占整张图的百分比
- contrast 可读性：文字与背景对比够不够，有没有浅字压浅底、细字压花纹
- polish 精致度：有没有明显 AI 味、廉价拼贴感、元素歪斜或比例失调
再判断 publishable：作为自媒体图文封面，这张图现在能不能直接发出去。
worst 填这五项里最差那一项的英文键名。note 用不超过 40 个中文字说最该改什么。
只输出 JSON，不要任何解释：
{"overflow":n,"hierarchy":n,"visual":n,"contrast":n,"polish":n,"visual_pct":n,"worst":"键名","note":"一句话","publishable":true}"""


def _review_json(text):
    """评审要的是 JSON，但视觉模型常包一层 ```json，交给 _parse_json 兜住。"""
    return _parse_json(text)


def _pct(v):
    """模型给的画面占比：只取数字，越界或给不上就当 0（0 表示"没估出来"）。"""
    m = re.search(r"\d+", str(v if v is not None else ""))
    if not m:
        return 0
    return max(0, min(100, int(m.group())))


def review_image(png_bytes, ai_config, timeout=120):
    """视觉模型按固定 rubric 打分，返回 {ok, score, items, visual_pct, worst, note}。

    ok 直接采用模型给的 publishable，score 用总分兜底（模型漏给 publishable 时
    按 REVIEW_PASS 判），这样前端只认一个字段。
    """
    api_key, api_base, _ = _require_ai(ai_config)
    import openai

    client = openai.OpenAI(api_key=api_key, base_url=api_base or "https://api.openai.com/v1",
                           timeout=timeout, max_retries=1)
    b64 = base64.b64encode(png_bytes).decode()
    resp = client.chat.completions.create(
        model=_ocr_model(ai_config),
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
            {"type": "text", "text": _REVIEW_PROMPT}]}],
        max_tokens=500)
    data = _review_json(resp.choices[0].message.content or "")

    def _n(v):
        try:
            return max(1, min(5, int(v)))
        except (TypeError, ValueError):
            return 0

    items = [{"key": k, "label": lb, "score": _n(data.get(k))} for k, lb in REVIEW_ITEMS]
    scored = [i for i in items if i["score"]]
    if not scored:
        # 一项都没给分＝模型没按格式回，不能当成"这张图 0 分"，那样会污染候选排序
        raise PosterError("视觉评审没拿到有效分数（模型没按 JSON 回话）")
    total = sum(i["score"] for i in items)
    worst = sorted(scored, key=lambda x: x["score"])[:1]
    pub = data.get("publishable")
    ok = bool(pub) if isinstance(pub, bool) else (total >= REVIEW_PASS and len(scored) == len(items))
    return {"ok": ok, "score": total, "full": len(items) * 5,
            "items": items,
            "visual_pct": _pct(data.get("visual_pct")),
            "worst": (worst[0]["key"] if worst else ""),
            "note": _clip_ws(data.get("note"), 60),
            "model": _ocr_model(ai_config)}


# ── 编排 ──
def pick_best(candidates):
    """多张里挑最好的一张：字先要对，再看视觉评审分，最后按出图顺序。

    顺序不能反：回读没过=内容错，再好看也不能发；回读都过了才轮到"好不好看"决定。
    """
    def _rank(c):
        v = c.get("verify") or {}
        r = c.get("review") or {}
        return (0 if v.get("ok") else (1 if v.get("ok") is None else 2),
                len(v.get("missing") or []),
                -(int(r.get("score") or 0)),
                0 if r.get("ok") else 1,
                c.get("index") or 99)
    return sorted(candidates, key=_rank)[0]


def build_lint(pub, check, overrode, review=None, typeset=None):
    """发布包告警：error 代表这张图别发，warn 代表要人补一手。"""
    # 拆解时那份告警是**带着 plan 算的**（标题里的数字有没有落在正文里），优先复用它；
    # 老 manifest 没有 warnings 才就地重算一遍（少了数字那条，但总比没有强）。
    issues = list(pub.get("warnings") or lint_publish(pub))
    if not pub.get("copy_text"):
        issues.append("warn: 配文为空，发布页需手写")
    if not pub.get("digest"):
        issues.append("warn: 摘要为空（公众号发布页要 ≤%d 字）" % DIGEST_MAX)
    if check.get("ok") is False:
        issues.insert(0, "error: 图面文字回读未通过，缺：" + "、".join(check.get("missing") or []))
    elif check.get("ok") is None:
        issues.append("warn: 本次未做回读校验，发布前必须人眼核对图面文字")
    if typeset is not None:
        # 程序排字：字对不对由构造保证，这里只报版面问题（放不下会被截断）
        if typeset.get("problems"):
            issues.append("warn: 版面放不下——" + "；".join(typeset["problems"]))
    review = review or {}
    if review.get("photo_mode"):
        # 真图主视觉不做画面评审是设计决定（那五项打的是 AI 插画好不好），
        # 报成 warn 会让人以为漏了一步要补
        issues.append("info: 主视觉用的是上传的真图，不做画面评审（那五项打的是 AI 插画好坏）")
    elif review.get("skipped"):
        issues.append("warn: 本次未做视觉评审（好不好看没人判过），错误原因：%s"
                      % (review.get("error") or "已跳过"))
    elif review:
        head = "视觉评审 %s/%s 分" % (review.get("score"), review.get("full"))
        if not review.get("ok"):
            issues.append("warn: %s 偏低，建议换一张候选或重出——%s" % (head, review.get("note") or ""))
        else:
            issues.append("info: %s 可发；最弱项 %s，%s"
                          % (head, review.get("worst") or "?", review.get("note") or ""))
    if overrode:
        issues.insert(0, "warn: 整合稿是规则版骨架，本次由人工显式放行出图，发布前务必核对数字与时间线")
    return issues


def choose_candidate(summary_id, index):
    """人工在多张候选里改选一张：把它复制成 poster.png（发布取图用的就是这张）。"""
    m = get_package(summary_id)
    if not m:
        raise PosterError("海报不存在，无法改选")
    cands = m.get("candidates") or []
    picked = [c for c in cands if str(c.get("index")) == str(index)]
    if not picked:
        raise PosterError("没有这张候选（可选编号：%s）"
                          % "、".join(str(c.get("index")) for c in cands))
    src = OUTPUT_DIR / str(summary_id) / picked[0]["file"]
    if not src.exists():
        raise PosterError("候选图片文件已丢失：%s" % picked[0]["file"])
    shutil.copyfile(src, OUTPUT_DIR / str(summary_id) / "poster.png")
    m["chosen"] = picked[0]["index"]
    m["verify"] = picked[0]["verify"]
    m["review"] = picked[0].get("review") or {}
    m["typeset"] = picked[0].get("typeset")
    m["lint"] = build_lint(m.get("publish") or {}, picked[0]["verify"],
                           bool(m.get("draft_override")), m["review"], m["typeset"])
    (OUTPUT_DIR / str(summary_id) / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return m


def generate_poster(summary, ai_config, theme="", title="", ratio="9:16",
                    progress_cb=None, do_verify=True, plan=None, publish=None, force=False,
                    copies=1, do_review=True, text_mode=DEFAULT_TEXT_MODE, photo=None,
                    preview_clicks=0, bases=None, publish_channel="toutiao", form="newspic",
                    fact_evidence=None, fact_checked_by="", provenance=None, asr_text=None,
                    frame_items=None, fact_ctx=None):
    """整理稿 → 海报一张。返回 manifest dict（已落盘 output/poster/<summary_id>/）。

    plan / publish 非空时跳过 LLM 拆解（前端审改过文案再出图走这条）：plan 是要画进图的
    文字仍过字数硬门禁，publish 是发布页文案只做软校验。两者都不落模型自由发挥——
    模型只负责画字与排版，写什么由拆解那一步定、由人改。

    photo 非空（图片字节）时走"真图主视觉"这条：用户上传的那张图顶掉整块插画区，
    标题压在图上、卡片往下让。这条**根本不调出图模型**，所以 0 元、几秒钟出，
    而且坐骑/角色这类"必须长这样"的内容不会被模型画跑形——这是它比 AI 插画更值钱的地方。

    force=True 放行 status=draft 的骨架稿。海报线与 HTML 卡片线不同：这里永远有一次 LLM
    提炼在中间，骨架稿只要底层转写有料就能出好文案（实测 61 号那篇超时稿，价格与时间线
    数字全对），所以门禁做成「默认拦、人工复核后可显式放行」，而不是一刀切。

    fact_evidence / fact_checked_by（2026-10-21 Codex P1）：事实闸拦下来后走 force 放行，
    必须**逐条**给「出处 + 核验结论」（`[{"item":…, "source":…, "result":…}]`）。
    缺任何一条，图照样出（人是最终判断者），但 `publishable=False`：
    界面不许显示"已核实"，也不许当待发成品用——以前只有一个 `overridden: true`，
    等于给没核过的数盖了"已核实"的章。

    copies=2/3 时一次出多张候选（poster_1.png…），自动把回读最干净的一张复制成 poster.png
    当默认，人工可在前端改选。AI 出图有方差，并行挑比反复重出省时间。
    """
    _require_ai(ai_config)
    if ratio not in SIZES:
        ratio = "9:16"
    if photo:
        # 真图模式下字一定是程序排的（图都占了一半画面，再让模型写字就是糊图）
        text_mode = "typeset"
    if plan is not None and text_mode != "typeset" and guide_only_plan(plan):
        # 攻略页压根不出 AI 底图（画面是素材真帧），所以"让模型把字画进图里"这一档在这儿
        # 只会得到一张没字的渐变底。2026-10-23 选项走查抓到的：界面选了直出，成品零文字。
        text_mode = "typeset"
    if text_mode not in TEXT_MODES:
        text_mode = DEFAULT_TEXT_MODE
    lim = limits_for(text_mode)
    # 判据与头条/公众号共用 gate；海报线允许人工复核后 force 放行
    blockers = draft_gate.draft_blockers(summary)
    overrode = bool(force and blockers)
    if blockers and not force:
        raise PosterError("整合稿不合格：" + "；".join(blockers)
                          + "。若你已复核过拆解出的文案，可点「仍然出图」放行。")
    summary_id = int(summary["id"])
    out_dir = OUTPUT_DIR / str(summary_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _pg(msg):
        if progress_cb:
            progress_cb(msg)

    started = datetime.now()
    if plan is None:
        _pg("LLM 拆解海报文案...")
        plan, llm_publish, bad, notes = build_text_plan(summary, ai_config, theme=theme,
                                                        title=title, text_mode=text_mode)
    else:
        # 手改过的文案不做自动压缩：那是用户显式输入，超线就明确报错让他自己删
        llm_publish, bad, notes = None, lint_plan(plan, lim), []
    if bad:
        raise PosterError("文案不合格：" + "；".join(bad))
    pub = _clip_publish(publish or llm_publish or {}, plan)
    # ── 事实闸（2026-10-21 号主抽查 87/85/83 之后加的）────────────────────────
    # 拦的是"上游已经标过待核的东西被升格成卖点"这一件确定性的事：整理稿【风险核查】点名的
    # 数字/名词，原样出现在图上任何一处文字或配文里 → 不出图。
    # 判据只有"标过没有 + 出没出现"两条，不叫模型再打一次分（那等于用编造审核编造）。
    # 语义类问题（同一条里既说别拉满又说推荐拉满、缺操作步骤）只列进 soft，交人看，不夸称全能抓。
    pub["source_note"] = compose_source_note(fact_ctx, pub.get("source_note") or "")
    # 台账里那些"上一版标过、这一版没写"的条目必须一起进判据；一手文本用来区分
    # "只有口播撑着"和"公告里确实有这句"（2026-10-29 小红书反例）
    risk_all = fact_gate.merge_items(fact_gate.risk_items(summary.get("content") or ""),
                                     (fact_ctx or {}).get("open_items") or [])
    fact_hard, fact_soft = fact_gate.check(
        _publish_view(plan, pub), summary.get("content") or "",
        extra_items=(fact_ctx or {}).get("open_items"),
        official_blobs=(fact_ctx or {}).get("official_blobs"))
    fact_gaps = fact_gate.guide_gaps(plan) if plan_is_guide(plan) else []
    if fact_gaps:
        fact_soft.append("按攻略发就得让人照做，这几段现在没有明确内容：%s；"
                         "补不齐就把标题改成「介绍/观察」，别逼模型编步骤"
                         % "、".join(fact_gaps))
    fact_gate_state = {"risk_items": [it["raw"] for it in risk_all],
                       "inherited": [it["raw"] for it in risk_all
                                     if str(it.get("raw") or "").startswith("【继承")
                                     or it.get("status") == "open"],
                       "hard": fact_hard, "soft": fact_soft, "guide_gaps": fact_gaps,
                       "overridden": bool(force and fact_hard)}
    fact_override = None
    if fact_hard and not force:
        err = PosterError("有 %d 处待核事实进了成品：%s。"
                          "先把这些卖点改掉再出图；确实核过了，就逐条填「出处 + 核验结论」再放行"
                          "（凭据会记进成品，缺记录这张不算已核实、不许标待发布）。"
                          % (len(fact_hard), "；".join(fact_hard[:3])))
        err.fact_hard = list(fact_hard)      # 前端按条开输入框，不能只给一句话
        raise err
    if fact_hard:
        fact_override = fact_gate.check_override(fact_hard, fact_evidence, fact_checked_by)
        fact_gate_state["override"] = fact_override
    # 「有图」≠「能发」：放行没有逐条凭据时，这张只能当草稿看（界面读 publishable）
    fact_blocks = bool(fact_hard) and not (fact_override or {}).get("complete")
    # ── 编辑证据链（2026-10-21 原创增量改造，只管 头条 + 攻略页 这一条）──────────────
    # 事实闸管的是"没核过的数别当卖点"，判帧管的是"画面上有没有别人压的字"——
    # 两件都不等于"这篇有没有独立价值、借来的文字和画面能不能这么用"。那一层由
    # `guide_provenance` 记，这里只做两件事：缺项并进 publishable、状态写进 manifest 给预览看。
    # **不勾原创、不输出原创百分比、不判首发**（PRD 明写工具不证明原创资格）；
    # 缺项也不拦出图——内部审稿的草稿照样要能生成，让人看得见要补什么。
    prov_state, prov_gaps = {}, []
    if publish_channel == "toutiao" and plan_is_guide(plan):
        from . import guide_provenance
        used_frames = borrowed_frame_count(plan)
        key, label = guide_provenance.state(provenance or {})
        prov_state = {"recorded": bool(provenance), "state": key, "label": label,
                      "question": (provenance or {}).get("question") or "",
                      "contribution": (provenance or {}).get("contribution") or "",
                      "contribution_evidence": (provenance or {}).get("contribution_evidence") or "",
                      "claims": (provenance or {}).get("claims") or [],
                      "materials": (provenance or {}).get("materials") or [],
                      # 版面里真绑上的素材帧张数：只有出图这一刻才知道得准，界面读的时候没有
                      "used_frames": used_frames,
                      "note": "内部提示，不是平台原创资格认证；勾不勾原创由人按当时平台规则逐篇判"}
        prov_gaps = guide_provenance.blocking_gaps(provenance or {}, used_frames=used_frames)
        prov_state["gaps"] = prov_gaps
    # ── 行动依据闸（2026-10-22 模型换档验收那一单）─────────────────────────────
    # 判的是"这条建议玩家能不能照做"：句里的数值与选项名要能在**原 ASR** 定位，
    # 定位不到就是"待回听/ASR查无/口播说法未核"。`fact_gate` 只认"风险节点名的 token 有没有印到卖点"，
    # 而新稿的形状是"末尾老实写需回听、前面已经排成可照做的表格"——同一件事它判 heavy=false，
    # 所以这一层补的是**证据**，不是往同义词表上加正则。
    # 只挡"越界进成品"的那几条：稿子里留内部待核清单、成品里避开它们，是编辑的正常动作（人是编辑）。
    act_state, act_hard = {}, []
    if publish_channel == "toutiao" and plan_is_guide(plan):
        from . import action_gate
        asr = str(asr_text or summary.get("transcript") or "")
        # 人工凭据只有一份：事实闸那条逐条凭据（fact_evidence）就是"这条核过了"的记录，
        # 这里把它当升档依据喂给行动依据闸——**不另立一本账**，否则同一件事两处记，
        # 迟早出现"事实闸说核过、依据闸说没核过"互相说反话（本项目栽过多次）。
        conf = [{"item": (e or {}).get("item") or "", "source": (e or {}).get("source") or "",
                 "result": (e or {}).get("result") or "", "by": fact_checked_by}
                for e in (fact_evidence or []) if isinstance(e, dict)]
        rep = action_gate.check(summary.get("content") or "", asr,
                                material_id=summary.get("id"), confirmations=conf)
        act_hard = action_gate.poster_block(rep["rows"], plan)
        act_state = {"ready_for_poster": bool(rep["ready_for_poster"] and not act_hard),
                     "stats": rep["stats"], "asr_chars": len(asr),
                     "pending": rep["pending"][:24], "overflow": act_hard[:12],
                     "note": rep["note"]}
    chars = plan_chars(plan)

    # 渠道分叉（2026-10-13 拍板）：小红书档不输出尾注与关键词回复。尾注有两处——
    # 图上末页印的免责脚注（plan 顶层与各页的 footer）和出图后追加到配文的合规尾注句
    # （见下方 copy_text 组装），这里把前者清空、后者按渠道跳过。keyword_reply 数据保留
    # 在 publish 里，只在发布载荷展示层过滤——渠道只管装配与展示，换渠道不用重花拆解钱。
    if publish_channel not in PUBLISH_CHANNELS:
        publish_channel = "toutiao"
    if publish_channel == "wechat":
        if form not in WECHAT_FORMS:
            form = "newspic"
    else:
        form = ""
    if publish_channel == "xhs":
        plan["footer"] = ""
        for _ch_page in (plan.get("pages") or []):
            if isinstance(_ch_page, dict):
                _ch_page["footer"] = ""

    pages = plan_pages(plan)
    multi = len(pages) > 1
    from . import poster_typeset as _ts

    def _page_prompt(p):
        """一页一份出图提示词：每页的版面（图在哪一块）和主视觉都不一样。"""
        if photo:
            lab = _ts.LAYOUTS[_ts.resolve_layout(p, _ts.skin_for(p.get("style_key")))]["label"]
            return ("（真图主视觉：主视觉用用户上传的图片、按「%s」排版，"
                    "不出 AI 底图，因此没有出图提示词）" % lab)
        return (build_art_prompt(p, ratio, extra_topics=_art_topic_words(plan))
                if text_mode == "typeset" else build_prompt_text(p, ratio))

    prompts = [_page_prompt(p) for p in pages]
    prompt_text = prompts[0]
    # 程序排字模式先按版面算出"图占哪一块、文字占哪一块"，再把这个比例写进出图 prompt，
    # 让模型在文字那块只画底纹。两边共用 poster_typeset.art_zone，所以不会错位。
    if multi:
        (out_dir / "prompt.txt").write_text(
            "\n\n".join("【第 %d 页】\n%s" % (i, t) for i, t in enumerate(prompts, 1)) + "\n",
            encoding="utf-8")
    else:
        (out_dir / "prompt.txt").write_text(prompt_text + "\n", encoding="utf-8")
    try:
        n = int(copies)
    except (TypeError, ValueError):
        n = 1
    n = max(1, min(n, MAX_COPIES))

    # 手动底图（在豆包等外部生成的无字底图，一页一张回传）。给了就必须每页都给：
    # 一半豆包一半 qwen 会打架，而且人会以为整组都 0 元——宁可当场拒。
    manual = [b for b in (bases or []) if b]
    if manual and len(manual) != len(pages):
        raise PosterError("手动底图要每页一张：这组共 %d 页，只收到 %d 张。"
                          "补齐再出，或者全部让 AI 出图。" % (len(pages), len(manual)))
    # 带底图的模板：换文案时复用那张已经验收的底图，不再出图。同一张底图多出几张
    # 只会得到完全一样的成品，所以这里强制单张。真图模式同理（不随机，多张必重复）。
    # 多页时也不套用：那张底图的插画位置是按单页版面算的，翻页会跟文字打架。
    tpl_base = None if (photo or multi or manual) else (template_base(plan.get("style_key"))
                                                        if text_mode == "typeset" else None)
    if photo:
        n = 1
        rest = ""
        if multi:
            # 攻略页其余那几页的画面是素材真帧，不是纯色底——话得按这一档真实的排法说
            rest = ("，其余 %d 页用素材真帧" % (len(pages) - 1) if guide_only_plan(plan)
                    else "，其余 %d 页用纯色底" % (len(pages) - 1))
        _pg("用你上传的图排主视觉（0 元，不出 AI 底图）%s..." % rest)
    elif manual:
        n = 1
        _pg("用你回传的 %d 张底图排版（0 元，不出 AI 底图）..." % len(manual))
    elif multi:
        n = 1
        _pg("多页模式：一页一张，不再抽卡（张数 × 页数会把花费翻倍）")
    elif guide_only_plan(plan):
        # 攻略页的底图是这套皮肤的固定渐变、画面是从素材里挑定的那一张真帧，
        # 抽几次得到的是一模一样的副本，还要白跑几次视觉评审（2026-10-23 选项走查抓到）。
        if n > 1:
            _pg("攻略页：画面是定好的素材真帧、底图是固定渐变，选 %d 张也只会出同一张图，"
                "按 1 张出（评审那几步也不再重复跑）..." % n)
        n = 1
    stale_nums = []
    # 每张回传底图的体检结果（{page, band, text, object, note, checked, bad}），落进 manifest
    base_rows = []
    if tpl_base:
        _pg("复用模板底图（0 元，不再出图）...")
        if do_verify:
            _pg("核对底图里有没有上一期留下的数字...")
            stale_nums = check_base_numbers(tpl_base, plan, ai_config)
    if manual and do_verify and text_mode == "typeset":
        # 只有排版档才查"那块空不空"：直出档那块本来就该被字占满，问它是答错的
        _pg("体检回传的底图：要排字的那一块空着没有（一页查一次，约 5-10 秒）...")
        for pi, b in enumerate(manual, 1):
            r = check_base_band(b, pages[pi - 1], ai_config, ratio)
            stale_nums += r.get("numbers") or []
            base_rows.append({"page": pi, "band": r.get("band") or "", "text": bool(r["band_text"]),
                              "object": bool(r["band_object"]), "note": r["note"],
                              "why": r.get("why") or "", "stray": r.get("stray") or "",
                              "checked": bool(r["checked"]), "bad": bool(r["bad"])})
            if r["bad"]:
                _pg("第 %d 页那张不合格：%s" % (pi, r.get("why") or r["note"]))

    # ── 印之前把**真要用上版的那几张帧**再收拾一遍 ──────────────────────────
    # 号主 2026-10-22 看图点名两件事：① p3/p4 那两张底部带着作者压的字幕；② p2/p3 发虚、
    # "没有精准截取到帧"（中间一块界面、四周一大片虚化背景，整幅放大印出来当然糊）。
    # 判帧那一次问的是七个字段的宽问题，实测会答 where=none 漏掉底部字幕——
    # **问题越窄答得越准**，所以这里只问"最下面三分之一有没有一行字幕 + 该保留哪块矩形"。
    # 只往成品包里写文件，生产帧池（frames.json / raw / clean）一个字节都不动。
    hardened = []
    used_photos = [(pi, ph) for pi, p in enumerate(pages, 1)
                   for ph in (p.get("photos") or [])
                   if (ph.get("path") and os.path.exists(str(ph.get("path"))))]
    if used_photos and ai_config and ai_config.get("api_key"):
        try:
            from . import frame_pool
        except ImportError:
            import frame_pool
        _pg("核对要印上版的 %d 张画面：底部有没有字幕、四周虚化的边裁不裁" % len(used_photos))
        for pi, ph in used_photos:
            new, rec = frame_pool.harden_frame(
                ph["path"], ai_config, out_dir / "_frames",
                name="p%d_%s" % (pi, os.path.basename(str(ph["path"]))), progress=_pg)
            rec["page"] = pi
            rec["frame"] = ph.get("frame") or ""
            hardened.append(rec)
            if rec.get("still_text") or rec.get("too_soft"):
                # 后期字裁不掉 / 这张本身就是虚的：不许上版。宁可这页没图，
                # 也不能把别人的字幕或一张糊图当成我们给的证据
                ph["_drop"] = rec.get("why") or "这一帧没通过出图前核对"
            elif rec.get("ok"):
                ph["path"] = new
    for p in pages:
        kept = [ph for ph in (p.get("photos") or []) if not ph.get("_drop")]
        dropped = [ph for ph in (p.get("photos") or []) if ph.get("_drop")]
        if dropped:
            _pg("第 %d 页有 %d 张画面被拦下：%s" % (
                (dropped[0].get("page") or 0), len(dropped),
                "；".join(str(d.get("_drop")) for d in dropped)))
        p["photos"] = kept

    # ── 拦完当场换一张再核一遍：这条硬要求必须落在最后一步之前 ─────────────────
    # 号主 10-23 定死"每一页都要有一张关键帧"，可兜底以前只跑在拆解那一步（bind_frames）。
    # 出图前这道核对把某页那张判死（太虚／字幕压中间）之后没人再补，就成了
    # "② 看版面有图、③ 出来那页没图"。补进来的那张走同一道核对，核不过就再换，最多试 3 张。
    refilled = []
    need_fill = any(isinstance(p, dict) and not (p.get("photos") or [])
                    and _ts and _ts._is_guide(p) for p in pages)
    if need_fill:
        try:
            from . import frame_pool
        except ImportError:
            import frame_pool
        bad_ids = {str(h.get("frame") or "") for h in hardened
                   if h.get("still_text") or h.get("too_soft")}
        used_ids = {str(ph.get("frame") or "") for p in pages
                    for ph in (p.get("photos") or []) if ph.get("frame")}
        used_ts = []
        for _pi, _p in enumerate(pages, 1):
            for ph in (_p.get("photos") or []):
                _s = _ts_seconds(ph.get("ts"))
                if _s is not None:
                    used_ts.append((_s, _pi))

        def _cands():
            """候选帧按"先清楚、再跟已用画面隔得远"排；已占用的、刚被判死的都剔掉。"""
            out = []
            for it in (frame_items or []):
                fid = "F%d" % int(it.get("i") or 0)
                f = it.get("file")
                if not f or fid in used_ids or fid in bad_ids or not os.path.exists(str(f)):
                    continue
                s = _ts_seconds(it.get("ts"))
                far = min([abs(s - k[0]) for k in used_ts], default=9999) if s is not None else 9999
                try:
                    clear = frame_pool.sharpness(f) >= frame_pool.HARDEN_MIN_SHARP
                except Exception:
                    clear = True
                out.append(((not clear, -far), fid, it))
            return [x[2] for x in sorted(out, key=lambda x: x[0])]

        for pi, p in enumerate(pages, 1):
            if not isinstance(p, dict) or (p.get("photos") or []) or not _ts._is_guide(p):
                continue
            for it in _cands()[:3]:
                fid = "F%d" % int(it.get("i") or 0)
                new, rec = frame_pool.harden_frame(
                    it["file"], ai_config, out_dir / "_frames",
                    name="p%d_补_%s" % (pi, os.path.basename(str(it["file"]))), progress=_pg)
                rec["page"] = pi
                rec["frame"] = fid
                hardened.append(rec)
                if rec.get("still_text") or rec.get("too_soft"):
                    bad_ids.add(fid)
                    _pg("第 %d 页换的 %s 也没过出图前核对（%s），再换一张"
                        % (pi, fid, rec.get("why") or ""))
                    continue
                p["photos"] = [{"path": new, "frame": fid, "judged": True,
                                "caption": frame_pool.caption_for(it),
                                "ts": it.get("ts") or "", "auto": True, "refilled": True}]
                used_ids.add(fid)
                _s = _ts_seconds(it.get("ts"))
                if _s is not None:
                    used_ts.append((_s, pi))
                refilled.append({"page": pi, "frame": fid})
                _pg("第 %d 页那张被拦下后已换成 %s，并重新核对通过" % (pi, fid))
                break
            if not (p.get("photos") or []):
                refilled.append({"page": pi, "frame": ""})
                _pg("第 %d 页补不出第二张：能用的画面要么已被别的页占完、要么都没过出图前核对——"
                    "这一页只排文字，或在「＋ 用你的图」传一张截图进来")

    if multi:
        # 回写：`plan_pages()` 给的是副本，拦掉的那张、换上的那张都改在副本上。
        # 不回写就成了"成品清单里记着 F4、纸上印的是 F3"——画面使用依据也照着它数，会数错。
        plan["pages"] = pages

    candidates, usage, page_rows = [], {}, []
    photo_name = ""
    if photo:
        # 原图存一份进包里：改文案重出时不用再传一次
        photo_name = "photo_src.png"
        (out_dir / photo_name).write_bytes(photo)
    w, h = _canvas_px(ratio)
    paid = 0
    saved = []        # 已经落盘的页名：某一页断了要能说出"前面几页还在"，否则他只会整单重按一次
    for pi, page_plan in enumerate(pages, 1):
        # 真图只当第一页的主视觉：同一张图在第 2 页再出现一次很难看。
        # 真图模式下整组都不出 AI 底图（其余页就是这套皮肤的纯色渐变），所以仍然 0 元。
        pphoto = photo if pi == 1 else None
        # 攻略页：画面是素材真帧，本来就不该叫模型画底图——整页 0 元。
        # 「＋ 用你的图」在这一档读的是**那一块画面位**（排版层用它顶掉这一页那张帧，
        # 一页一张是号主定的硬上限），不是插画区。以前这里写着 `if guide: pphoto = None`，
        # 于是界面确认框说"第 1 页用你上传的真图"、成品上一张都没印（2026-10-23 选项走查抓到）。
        guide = bool(_ts and _ts._is_guide(page_plan))
        for i in range(1, n + 1):
            if guide:
                raw = _ts.placeholder_base(page_plan.get("style_key"), w, h, 0.6,
                                           art_hint=False)
            elif photo:
                raw = _ts.placeholder_base(page_plan.get("style_key"), w, h, 0.6,
                                           art_hint=False, plan=page_plan)
            elif manual:
                raw = manual[pi - 1]
            elif tpl_base:
                raw = tpl_base
            else:
                _pg(("出第 %d/%d 页的底图（%s）..." % (pi, len(pages),
                                                     COPIES_NOTE if n > 1 else "约 20-60 秒"))
                    if multi else "出图 %d/%d（%s）..." % (i, n, COPIES_NOTE if n > 1 else "约 20-60 秒"))
                try:
                    raw, usage = generate_image(prompts[pi - 1], SIZES[ratio], ai_config)
                except PosterError as e:
                    # 哪一页死的要说得出来：多页时"出图失败"四个字等于让人整单重按，
                    # 而前面那几页其实已经在盘上（钱已经花在那儿了）
                    where = ("第 %d/%d 页" % (pi, len(pages))) if multi else ("第 %d/%d 张" % (i, n))
                    raise PosterError("%s 出图失败：%s%s" % (
                        where, e, ("；已经落盘的页：" + "、".join(saved)) if saved else ""))
                paid += 1
            suffix = ("_p%d" % pi) if multi else ""
            fname = ("poster%s.png" % suffix) if (n == 1 or multi) else ("poster_%d.png" % i)
            v = {"skipped": True, "ok": None, "missing": [], "extra": "", "transcript": ""}
            ts_chk = None
            # 页码用 pi（第几页），不是 i（同一页抽的第几张）——写反过，三页全标 1/3
            pg_tag = (pi, len(pages)) if multi else None
            if text_mode == "typeset":
                (out_dir / ("base%s.png" % suffix if n == 1 else "base_%d.png" % i)).write_bytes(raw)
                _pg("排版第 %d 张（真字体，字不会画错）..." % (pi if multi else i))
                png, ts_chk = typeset_poster(raw, page_plan, ratio, photo=pphoto, page=pg_tag)
                # 回读校验在这条路上是负资产：字是程序画的、不可能错，而那个视觉模型
                # 实测会把画错的赛字读成对的、还会自己脑补出多出来的字，只会误报。
                v = {"skipped": True, "ok": True, "not_needed": True,
                     "missing": [], "extra": "", "transcript": ""}
            else:
                png = raw
                if do_verify:
                    _pg("回读校验第 %d 张的图面文字..." % i)
                    try:
                        v = verify_image(png, page_plan, ai_config)
                    except Exception as e:
                        v = {"skipped": True, "ok": None, "error": str(e)[:200],
                             "missing": [], "extra": "", "transcript": ""}
            (out_dir / fname).write_bytes(png)
            saved.append(fname)
            r = {"skipped": True, "ok": None}
            if do_review and not photo:
                _pg("视觉评审第 %d 张好不好看..." % (pi if multi else i))
                try:
                    r = review_image(png, ai_config)
                except Exception as e:
                    r = {"skipped": True, "ok": None, "error": str(e)[:200]}
            elif photo:
                # 评审那五项打的是"AI 插画好不好看"，主视觉换成用户真图后这套分没意义，
                # 还会因为"画面不是手绘的"乱扣，所以这条明确不做，而不是给个 0 分。
                r = {"skipped": True, "ok": None, "photo_mode": True}
            row = {"index": i, "file": fname,
                   "url": "/media/poster/%d/%s" % (summary_id, fname),
                   "verify": v, "review": r, "typeset": ts_chk, "bytes": len(png)}
            if multi:
                row["page"] = pi
                row["layout"] = (ts_chk or {}).get("layout") or ""
                row["layout_label"] = (ts_chk or {}).get("layout_label") or ""
                row["chars"] = _plan_chars_one(page_plan)
                row["cards"] = len(page_plan.get("cards") or [])
                row["role"] = page_plan.get("role") or ""
                row["base_note"] = ("真图主视觉" if pphoto else
                                    ("素材真帧（0 元）" if guide else
                                     ("纯色底（0 元）" if photo else
                                      "无底图（出图档已撤，见 generate_image）")))
                page_rows.append(row)
            candidates.append(row)

    # 多张时自动挑一张当默认（poster.png 永远是"当前选中的那张"，列表页与发布取图不用改）
    best = page_rows[0] if multi else pick_best(candidates)
    if multi:
        shutil.copyfile(out_dir / best["file"], out_dir / "poster.png")
    elif n > 1:
        shutil.copyfile(out_dir / best["file"], out_dir / "poster.png")
    check = best["verify"]
    review = best.get("review") or {}
    ts_chk = best.get("typeset")

    copy_text = pub.get("copy_text") or ""
    # 渠道分叉（2026-10-16 加了分渠道正文之后改成"按渠道取那份"）：
    # 小红书用 xhs_copy + 话题标签，那份里没有尾注、没有「回复 XX」；
    # 小红书档**永远不追加尾注**——正文里的 AI 自证句正是那次「AI 托管」判决的旁证之一（见四查清单）。
    # 老 plan 没有 xhs_copy 时退回 copy_text（至少不追加尾注），并在 warnings 里已经报过。
    if publish_channel == "xhs":
        if (pub.get("xhs_copy") or "").strip():
            copy_text = pub["xhs_copy"]
            if pub.get("xhs_tags"):
                copy_text = copy_text.rstrip() + "\n\n" + " ".join(pub["xhs_tags"])
    elif copy_text and COMPLIANCE_NOTE[:10] not in copy_text:
        copy_text = copy_text.rstrip() + "\n\n" + COMPLIANCE_NOTE

    issues = build_lint(pub, check, overrode, review, ts_chk)
    if not photo:                     # 真图模式下"主视觉/配图写没写"这条提醒没意义
        # 多页时只查首页：scene 与各页配图是给模型看的，第 2 页起本来就不要求再写一遍
        for w in lint_visual(pages[0]):
            issues.append("warn: " + w)
    if multi:
        issues.append("info: 这组 %d 页，发布时按 img 顺序一次带走（第 1 页也复制成了 poster.png）"
                      % len(pages))
    if stale_nums:
        issues.append("warn: 这张底图里带着文案里没有的数字 %s——是上一期烙进插画的，"
                      "换内容时确认它们还对不对；不对就换一套不带底图的风格重出一张"
                      % "、".join(stale_nums[:6]))
    bad_bands = ["第 %d 页：%s" % (x["page"], x.get("why") or x["note"] or "要排字那块被占了")
                 for x in base_rows if x.get("bad")]
    if bad_bands:
        # 只提醒不拦：卡片有半透明底板，压得住一点杂；但必须让他知道是哪一页、占成什么样
        issues.append("warn: 这几张回传的底图不合格 —— %s。回豆包重出这一页时，把提示词"
                      "【第一步】里那句空带描述抄到最前面再说一遍；赶时间也能直接用，"
                      "卡片有半透明底板，只是没这么干净" % "；".join(bad_bands))

    # 整组都是攻略页＝一张 AI 底图都没叫（画面全部来自素材真帧），这一档必须记 0 元。
    # 判据用 _is_guide 而不是 layout_key：手工往 plan 里塞 photos 也算攻略页。
    guide_only = bool(pages) and bool(_ts) and all(_ts._is_guide(p) for p in pages)

    manifest = {
        "id": summary_id,
        "summary_id": summary_id,
        "channel": "poster",
        # 发布渠道与公众号形态（2026-10-13 渠道分叉）。channel:"poster" 是产物管线标识，别动；
        # 旧 manifest 没有这两个键，读取端一律按 toutiao/newspic 缺省。
        "publish_channel": publish_channel,
        "form": form,
        "title": plan.get("title") or "",
        "plan": plan,
        "publish": pub,
        "copy_text": copy_text,
        "digest": pub.get("digest") or "",
        "keyword_reply": pub.get("keyword_reply") or "",
        "source_note": pub.get("source_note") or "",
        "prompt_text": prompt_text,
        "ratio": ratio,
        "size": SIZES[ratio],
        "text_chars": chars,
        "style_key": plan.get("style_key") or "",
        "style_label": _preset(plan).get("label", ""),
        "scene": plan.get("scene") or "",
        "image": "poster.png",
        "photo": photo_name,
        "photo_frac": ((ts_chk or {}).get("photo") or {}).get("frac"),
        "url": "/media/poster/%d/poster.png" % summary_id,
        "candidates": candidates,
        "chosen": best["index"],
        "copies": n,
        "page_count": len(pages),
        "pages": page_rows or candidates[:1],
        # 采纳率的原料：模型推荐的那份 vs 人最后真正出的那份
        "recommended": (plan or {}).get("recommended") or {},
        "final": {"page_count": len(pages), "style_key": plan.get("style_key") or "",
                  "layouts": [_ts.resolve_layout(p, _ts.skin_for(p.get("style_key"))) for p in pages],
                  "cards": [len(p.get("cards") or []) for p in pages]},
        "why": (plan or {}).get("why") or "",
        "preview_clicks": int(preview_clicks or 0),
        "text_mode": text_mode,
        "typeset": ts_chk,
        "image_model": "" if (photo or guide_only) else _image_model(ai_config),
        "ocr_model": _ocr_model(ai_config),
        "usage": usage,
        # 一分钱没花的四种情况都要写 0：复用模板底图、用上传的真图、用豆包回传的图、
        # 整组都是攻略页（画面是素材真帧，压根不叫画图模型）。
        # 原来漏了 manual 这一支，实测回传成品那张 manifest 写着 0.5 元——人就是冲着 0 元
        # 走这条路的，账记错比不记更糟。攻略页第一次端到端跑出来也误记成 0.5 元，同一类错。
        "cost_yuan_estimate": (0 if (tpl_base or photo or manual or guide_only)
                               else PRICE_PER_IMAGE_YUAN * max(1, paid or 1)),
        "base_reused": bool(tpl_base),
        "base_stale_numbers": stale_nums,
        # 豆包等外部底图的体检结果：一页一条，bad 的那些已经变成上面的 warn
        "base_check": base_rows,
        "auto_notes": notes,
        "draft_override": overrode,
        # 事实闸的结果必须落盘：放行过哪几条、当时看到的是什么，事后要能查（不许只活在会话里）
        "fact_gate": fact_gate_state,
        # 「有图」≠「能发」：待核卖点没有逐条凭据时这张只是草稿（2026-10-21 Codex P1）
        "publishable": not fact_blocks,
        # 编辑证据链（原创增量）：状态、逐条缺项、以及"这不是原创认证"那句原话都要落盘
        "provenance": prov_state,
        # 行动依据闸：内部待核清单 + 越界进成品的那几条（2026-10-22 那一单）
        "action_gate": act_state,
        "verify": check,
        "review": review,
        "source_title": summary.get("title") or "",
        "theme": (theme or "").strip(),
        "checklist": checklist_for(publish_channel, form),
        "created_at": _now(),
        "elapsed_sec": int((datetime.now() - started).total_seconds()),
    }
    manifest["lint"] = issues
    # 事实闸的条目并进同一份告警清单：前端只有一处给人看的地方，另开一处迟早没人看
    if fact_hard and fact_blocks:
        for msg in fact_hard:
            issues.append("error: 待核事实进了成品 —— " + msg)
        miss = (fact_override or {}).get("missing") or [str(x)[:40] for x in fact_hard]
        issues.append("error: 图已出但 %d 条待核事实没有完整逐条凭据（缺：%s）——"
                      "这张不算已核实，不许当待发成品用"
                      % (len(miss), "；".join(m[:36] for m in miss[:3])))
    else:
        for msg in fact_gate_state.get("hard") or []:
            issues.append("warn: 待核事实曾进了卖点（已逐条留凭据）—— " + msg)
        ov = fact_gate_state.get("override") or {}
        if ov.get("complete"):
            issues.append("warn: 人工放行 %d 条待核事实，凭据已逐条记录（核验人：%s）"
                          % (len(ov.get("rows") or []), ov.get("checked_by") or "未填"))
    for msg in fact_gate_state.get("soft") or []:
        issues.append("warn: " + msg)
    # 没判过帧的配图：出图照出，但清单上必须写"未判，需人眼看"（2026-10-21 Codex P0）
    unjudged = unjudged_photos(plan)
    manifest["photos_unjudged"] = unjudged
    # 印之前那一道核对留痕：裁没裁字幕条、按没按内容框裁、哪张被拦下——没有这份记录，
    # "这张图到底核过没有"又得靠猜（号主只有截图＋一句话这一种反馈方式）
    manifest["frames_hardened"] = [
        {k: v for k, v in rec.items() if k in ("page", "frame", "strip", "box", "still_text", "too_soft",
                                              "sharp", "subject", "ok", "why")}
        for rec in hardened]
    # 被拦下后当场换的那几张（page + 换成的编号；frame 空＝这页补不出来）：
    # 没有这条记录，"② 有图 ③ 没图"就永远只能靠人回忆
    manifest["frames_refilled"] = refilled
    for name in unjudged:
        issues.append("warn: %s 这张图没走判帧（手工给的路径），"
                      "发布前人眼确认画面里没有字幕条／花字／账号名" % name)
    # 编辑证据链的缺项：一条条点到字段（哪条结论没证据、哪张图没依据），
    # 并明确写"不是原创资格认证"——省得有人把它读成"补完就能勾原创"。
    # 号主 10-23 拍板放宽这一档：它是**内部台账缺登记**，不是内容错，
    # 不该把整条产线钉死在最后一步（前面拆解、看版面都过了，③ 也出图了，只是发不出去）。
    # 所以改成 warn + `needs_evidence`，不再翻 `publishable`；
    # 真会发错东西的那两道（事实闸的待核卖点、行动依据闸的照做不了）照旧硬拦。
    manifest["needs_evidence"] = bool(prov_gaps)
    for g in prov_gaps[:6]:
        issues.append("warn: 编辑证据链缺项 —— %s（%s）｜怎么补：%s"
                      % (g["what"], g["field"], g["hint"]))
    if prov_gaps:
        issues.append("warn: 编辑状态「%s」——图能出、也能发，但这篇没建证据链，"
                      "被问「这条画面你凭什么用」时答不上（内部提示，不是平台原创资格认证）"
                      % prov_state.get("label", ""))
    # 行动依据没落地却进了成品位置：一条条点名（哪一节、哪句话、缺什么、印在哪个位置）
    for x in act_hard[:6]:
        issues.append("error: 行动依据未落地 —— %s" % x)
    if act_hard:
        issues.append("error: 这篇有 %d 条建议的数值/选项名在原转写里定位不到，"
                      "已挡在待发之前；要放行请先回听或进游戏核对，"
                      "把出处与结论记进待核清单（内部清单还有 %d 条）"
                      % (len(act_hard), (act_state.get("stats") or {}).get("pending", 0)))
    if manifest["publishable"] and any(i.startswith("error") for i in issues):
        manifest["publishable"] = False      # 别的硬伤（字数/骨架稿）也一样不能标待发
    # ── 最终人工放行（2026-10-29 最小版）：只记"你放行的是哪一版" ──────────────
    # 不做逐项打分表——号主自己说"最终人工审稿我也不知道正不正确"，那就不拿伪判据冒充审核。
    # 这条只解决一件事：成品一改（文字或任一页图），哈希变、旧放行作废。
    from . import release_review
    pkg_hash = release_review.package_hash(str(out_dir), manifest)
    rr = release_review.status(str(out_dir), publish_channel, pkg_hash)
    manifest["package_hash"] = pkg_hash
    manifest["release_review"] = dict(rr, checklist=release_review.checklist_hint(publish_channel))
    if release_review.blocks_publish(publish_channel, rr):
        manifest["publishable"] = False
        issues.append("info: 小红书这一档要最终人工放行才算待发；"
                      "现在没有匹配这一版的放行记录（改稿即失效），去发布区点「签发放行」")

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    errs = [i for i in issues if i.startswith("error")]
    score = ("，评审 %s/%s 分" % (review.get("score"), review.get("full"))) if review.get("score") else ""
    _pg(("✅ 海报已生成（用的是你上传的真图，没花出图钱）" if photo
         else ("✅ 海报已生成（复用模板底图，这次没花出图钱）" if tpl_base
               else "✅ 海报已生成" + ("" if errs else "（发布文案与清单已就绪）"))) + score
        + ("，共 %d 张，点图换选" % n if n > 1 else ""))
    return manifest


def reveal_folder(summary_id=0):
    """在系统文件管理器里打开海报所在目录（有 poster.png 就选中它）。

    这个接口只可能跑在本机：工作台监听 127.0.0.1，路径由整数 id 拼出来，
    没有用户可控的字符串进来，所以不存在路径穿越。
    """
    import subprocess
    import sys as _sys
    d = OUTPUT_DIR / str(int(summary_id)) if summary_id else OUTPUT_DIR
    if not d.exists():
        raise PosterError("还没有这个目录：先出一次图再点")
    target = (d / "poster.png") if (summary_id and (d / "poster.png").exists()) else d
    path = str(target)
    if _sys.platform == "darwin":
        cmd = ["open", "-R", path] if target.is_file() else ["open", path]
    elif _sys.platform.startswith("win"):
        cmd = ["explorer", "/select," + path] if target.is_file() else ["explorer", path]
    else:
        cmd = ["xdg-open", str(target.parent if target.is_file() else target)]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(d)


def list_packages():
    return pkg_store.list_packages(OUTPUT_DIR)


def get_package(summary_id):
    return pkg_store.get_package(summary_id, OUTPUT_DIR)


def delete_package(summary_id):
    return pkg_store.delete_package(summary_id, OUTPUT_DIR)
