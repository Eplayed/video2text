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
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

import requests

from .graphics import gate as draft_gate
from .graphics import package as pkg_store
from .graphics.channels.toutiao import _parse_json

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "poster"

# 中文文字渲染主力。2026-10-11 实测：这个中转端点把 qwen-image-max 下线了
# （models.list 里只剩 qwen-image-2.x / 3.0 / 2.1-pro / wan2.7-image / z-image-turbo），
# 调用直接 404「Model not exist」——出图那一下是**会报错**的，不像看图那三处被吞成 skipped，
# 所以这条是当场能发现的。换到 qwen-image-3.0-pro 实测一张 936×1664 出图正常（1.69MB PNG）。
# ⚠ 没核实的两件事：① 这个档在聚合站的单价（原来 max 是 0.5 元/张、账单核对过；
#    新档大概率更贵，界面按钮上写的"0.5 元/张"要以账单为准，别当已核实）；
# ② 它的中文逐字稳定率没重测（0.5 元那张的 63 字翻车记录是 max 的，见 CHANGELOG 10-01）。
#    要重测跑 `python3 scripts/probe_poster_stability.py 5 9:16`（5 张 ≈ 2.5 元）。
IMAGE_MODEL = "qwen-image-3.0-pro"


def _image_model(ai_config):
    """出图模型名：配置里给了 AI_MODEL_IMAGE 就用它，理由同 _ocr_model——别再写死。"""
    return ((ai_config or {}).get("image_model") or "").strip() or IMAGE_MODEL
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
    return dict(TS_LIMITS if text_mode == "typeset" else MD_LIMITS)
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

# 出图后视觉评审的五项 rubric（详见 review_image）：回读管"字对不对"，这里管"好不好看"
REVIEW_ITEMS = [("overflow", "文字完整"), ("hierarchy", "层级清晰"), ("visual", "画面占比"),
                ("contrast", "可读性"), ("polish", "精致度")]
REVIEW_PASS = 18            # 五项满分 25，低于这个分不建议直接发

# 合规尾注：图是 AI 画的、文字是 AI 写的，转述声明必须随配文一起发
COMPLIANCE_NOTE = "本文由 AI 辅助整理归纳，内容源自公开分享，观点与结论归原作者，如有侵权请联系删除。"

# 海报版发布检查清单：平台侧动作 AI 替不了，逐条人工过
CHECKLIST = [
    "图内文字已核对：程序排字模式由代码落字、不会错，只需看版面提示有没有截断；"
    "模型画字模式必须 manifest.verify.ok = true，未通过别发",
    "视觉评审已过（manifest.review）：低于 %d 分或画面占比低于 40%% 时先换候选/换风格重出，别硬发" % REVIEW_PASS,
    "数字/版本/价格类信息人工核对一手来源（ASR 与模型都可能出错）",
    "「文章设置」勾选「内容由 AI 生成」（2025-09-01 起强制，平台不自动标注）",
    "图片消息无「声明原创」入口（平台未开放图片原创），勿找该按钮、勿群发后补标",
    "配文保留文末合规尾注，勿删；正文禁外链",
    "尺寸与平台匹配：9:16 小红书/抖音图文、3:4 公众号图片消息、1:1 通用封面",
    # ── 小红书单独四查（2026-10-13 复盘：10-10 那条《魔兽无限服国服金团随便开》
    #    10-11 23:20 被判「笔记存在利用AI托管进行发文/互动的内容」，处置=不可被他人查看）。
    #    平台 2026-03-10《打击AI托管运营账号治理公告》+ 04-27《AI内容治理规则》：
    #    禁"模拟真人发文/互动"、禁"套模板批量生产同质化内容"，AI 参与要走**发布时的官方声明勾选**。
    #    我们那篇正好同时踩了两句自己写的字：正文末尾「本文由 AI 辅助整理归纳」＝自证，
    #    「回复 金团 获取政策要点」＝引导关键词自动回复（就是"托管互动"的字面形态）。
    "【发小红书】正文**不要**写「本文由 AI 辅助整理归纳」那句尾注——那是自证托管；"
    "改用发布页【笔记内容声明】→勾选「笔记含AI合成内容」（公众号那条才保留尾注，两家口径相反）",
    "【发小红书】删掉「回复 XX 获取…」这类引导自动回复；评论/私信也别挂机器人",
    "【发小红书】至少一张自己游戏内的真截图 + 一句你本人真发生过的事"
    "（不许编：伪造经历与截图是「AI 造假」那一档，处置比限流重）",
    "【发小红书】别拿同一套模板批量发——「套用模板批量生产同质化内容」是平台明写的红线",
    "发布后 72 小时回填数据，便于回看哪种风格预设、哪种画面占比有效",
]


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
        "when": "多页图文攻略：一页一个论点，配素材真帧 + 图注 + 大字数字",
        "scene": "不画主视觉插画：版面靠真帧截图当证据，底色是安静的深墨绿渐变",
        "mood": "深墨绿底 #182721 到 #0a1410 的安静渐变，米金标题 #f0d9a0，砖红警示块 #5f2a24，"
                "正文米白 #e6e6df，图注与脚注灰绿 #93a497；版面像一本摊开的攻略手册，"
                "严禁炫光科技感的界面面板、严禁花哨边框、严禁把画面塞满装饰",
        "card_visual": "每条论点前面是米金色小标题，正文一行行排下来，不画图标也不画面板",
        "style": ("深墨绿底中文攻略页，米金色超粗中文黑体标题配砖红警示块，正文米白、图注灰绿，"
                  "版面是一页一个论点的攻略手册结构，靠字号体量差拉层级，"
                  "严禁科技风玻璃面板、严禁花哨边框、严禁把画面塞满装饰"),
    },
    "auto": {
        "label": "自由发挥（沿用拆解写的那句风格）",
        "when": "上面四套都不合题材时",
        "scene": "",
        "card_visual": "",
        "style": "",
    },
}
# 攻略页那一档排在四套插画风格之后：它不出 AI 底图，选它等于选"用素材真帧当证据"
STYLE_ORDER = ["game_epic", "tool_review", "checklist", "minimal", "guide_ink", "auto"]
DEFAULT_STYLE_KEY = "tool_review"
# 字体这条不写进各套配方、单独挂：风格词里全是"质感/雕花/氛围"，模型很容易顺手把中文
# 写成手写体或艺术字，而实测手写体的逐字准确率明显更低（主标题整块消失过一次）。
TYPE_RULE = ("图上的中文一律用清晰规整的中文黑体或无衬线字体，字号够大、笔画干净，"
             "禁用手写体、草书、艺术字变形与笔画粘连")


def style_choices():
    """给前端下拉用：内置四套 + 用户自己收编的模板，走同一份名单。"""
    out = []
    for k in STYLE_ORDER:
        p = STYLE_PRESETS[k]
        out.append({"key": k, "label": p["label"], "when": p["when"],
                    "preset": (p["style"] or "（沿用手写的风格描述）"),
                    "scene": p["scene"] or "", "builtin": True, "has_base": False})
    for k, p in _user_presets().items():
        out.append({"key": k, "label": "★ " + p["label"], "when": p["when"],
                    "preset": p["mood"] or "（沿用父风格的画风口）",
                    "scene": p["scene"], "builtin": False, "has_base": _has_base(k)})
    return out


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


def style_plan_prompt():
    """拼进拆解 prompt 的预设清单：让模型自己挑风格键，而不是自己编一句风格。"""
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
    {"max_points": 999, "pages": 4,
     "why": "14 条以上按每页 4 条切；再往上没人看完，成本也按页数翻倍"},
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


def layout_plan_prompt(has_photo, page_hint=0):
    """拼进拆解 prompt 的那段"拆几页 + 每页什么布局"规则。

    里面的 __TOTAL__ 交给调用方那串 replace 一起替换，跟主模板用同一个预算数。

    page_hint 是用户在界面上已经定了的页数（他嫌 2 页太挤、手动加到 3 页，再点「换一套」
    就是想要 3 页的结构）。这时候页数不再由模型判断，剩下的只让它决定怎么分配与选布局。
    """
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
_PLAN_PROMPT = """你是自媒体图文编辑。下面这份素材多半**已经是别人总结过的内容**（口播稿或攻略要点），
你的任务不是把它切得更碎，而是挑出一条最有价值的主线，并把推理链留在图上。

本篇主题：__THEME__

铁律一·内容纪律（转载整理模式）：
- 只能用素材里出现过的事实与数字，严禁编造、严禁夸大；素材没写的不要写。
- 全程转述口吻，禁用第一人称（我实测 / 我用过）。
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
     "cards": [{"t": "卡片标题", "d": "卡片说明", "v": "这一条画什么"}]}
  ],
  "publish": {
    "copy_text": "发布页配文，120-260 字纯文本：钩子开头 1-2 句 + 要点 3-5 条（每条一行，用「· 」开头）+ 收尾引导 1 句。不要 markdown 符号。",
    "digest": "摘要，≤120 字，单独成段能读懂，不写「见图」这类字样",
    "keyword_reply": "关注后自动回复引导语，≤30 字（例：回复 清单 领取完整列表）；素材里没有可推的关键词就留空字符串",
    "source_note": "来源说明一句话，≤50 字，含数据口径截至{{DATE}}"
  }
}

关于 pages：按上面页数规则判定要拆几页后，**把每页写进 pages[]**（一页一个对象，形状同上），
同时顶层的 title / subtitle / cards / scene 仍然填**第 1 页**的内容（老代码读这几个键）。
只拆一页时 pages 可以省略。footer 整组共用一句，只填顶层就行。
配文（publish.copy_text）要把被分到后面几页的要点也用文字带上，读者不看第二页也不漏信息。

spine、scene、每张卡片的 v、style_key 与 publish 里的文字都不会画进海报，所以不占合计字数预算；
但同样只能用素材里的事实。

素材：
__CONTENT__"""


def _clip(text, limit):
    return re.sub(r"\s+", "", str(text or ""))[:limit]


def _clip_ws(text, limit):
    """发布文案用的裁切：折叠空白但保留单空格——图面字段可以删空格，
    「回复 Agent 领取」这类关键词删了空格就废了。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _clip_publish(raw):
    """发布文案单独裁切：它不进图，所以不受图面合计字数预算约束。"""
    raw = raw if isinstance(raw, dict) else {}
    return {"copy_text": re.sub(r"[ \t]+", " ", str(raw.get("copy_text") or "")).strip()[:COPY_MAX],
            "digest": _clip_ws(raw.get("digest"), DIGEST_MAX),
            "keyword_reply": _clip_ws(raw.get("keyword_reply"), KEYWORD_MAX),
            "source_note": _clip_ws(raw.get("source_note"), SOURCE_MAX)}


def lint_publish(pub):
    """发布文案侧软校验：不阻断出图，只进 manifest.lint 提示人工补。"""
    bad = []
    n = len(pub.get("copy_text") or "")
    if n and (n < COPY_MIN or n > COPY_MAX):
        bad.append("配文 %d 字，建议 %d-%d 字" % (n, COPY_MIN, COPY_MAX))
    if len(pub.get("digest") or "") > DIGEST_MAX:
        bad.append("摘要超 %d 字" % DIGEST_MAX)
    if len(pub.get("keyword_reply") or "") > KEYWORD_MAX:
        bad.append("关键词回复超 %d 字" % KEYWORD_MAX)
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
#   ② 图注由代码按那一帧自己的记录写（画面里是什么 + 第几秒），**不让模型写 caption**——
#      它没看过那张图，写出来的就是照着文案倒推的假图注。
GUIDE_KEYS = ("kicker", "photos", "bignum", "contrast", "warn", "ask")

_GUIDE_RULES = """
铁律六·攻略页（这一档不靠模型画插画，画面全部来自素材真帧）：
可用画面只有下面这些，编号是 F+数字，**一张都不许多编，也不许说画面里有它没说的东西**：
__FRAMES__
每页按内容需要给这几样，用不上就别给，别硬凑：
- "photos": [{"frame": "F7"}] —— 这一页的证据图，最多 2 张，只能填上面的编号。
  图注由程序按那一帧自己的记录写，**你不要写 caption**。挑图只有一条标准：
  这一页在讲的那件事，画面里真的看得见；看不见就不给图。
- "kicker": "栏目名 · 本篇主题"（≤14 字，整组同一句，只填在第一页）
- "bignum": {"v": "约 +20%", "label": "这个数字说的是什么"} —— v 里的数字必须在素材原文里出现过
- "contrast": [{"t": "已改", "d": "…"}, {"t": "没改", "d": "…"}] —— 两行讲清改了/没改
- "warn": {"t": "一句提醒", "d": "限制是什么、为什么"} —— 素材没说的写在这儿，别编成结论
- "ask": {"q": "一个能直接回答的问题", "options": ["不超过三项"]} —— 只给最后一页
这一档每页正文仍守 ≤__TOTAL2__ 字，但卡片建议 2-3 条，把地方留给图。
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
            .replace("__TOTAL2__", str(total)))


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
    for c in (page.get("contrast") or [])[:3]:
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
        opts = [_clip_ws(o, 8) for o in (ak.get("options") or [])[:3] if str(o).strip()]
        out["ask"] = {"q": _clip(ak.get("q"), lim["title"]), "options": opts}
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
        for ph in raw[:2]:
            fid = ""
            if isinstance(ph, str):
                fid = ph.strip().upper()
            elif isinstance(ph, dict):
                fid = str(ph.get("frame") or ph.get("id") or "").strip().upper()
                if not fid and ph.get("path"):
                    # 人手工在界面上选过图：路径直给，图注也保留人写的那句
                    kept.append({"path": str(ph.get("path")),
                                 "caption": _clip_ws(ph.get("caption"), 40)})
                    continue
            it = by.get(fid)
            if not it:
                notes.append("第 %d 页点了 %s，可用画面里没有这个编号，这张图已丢掉"
                             % (pi, fid or "空编号"))
                continue
            kept.append({"path": it.get("file"), "frame": fid,
                         "caption": frame_pool.caption_for(it),
                         "ts": it.get("ts") or ""})
        if kept:
            p["photos"] = kept
        else:
            p.pop("photos", None)
            notes.append("第 %d 页没有可用画面（编号全对不上），这一页只排文字" % pi)
    return notes


def normalize_page(item, lim, style_key, has_photo, idx, first_title="",
                   group_footer="", group_scene=""):
    """把模型给的一页洗成能排版的那一页：裁字数、认布局键、没图就换掉"要图"的构图。

    整组拆解与「只重拆这一页」共用这一处——两条路出来的页必须形状一致，
    否则同一份排版引擎要伺候两种输入，早晚在其中一条上静默出错。
    返回 (页 或 None, 给人看的说明列表)。None 表示这页是空的，该丢掉。
    """
    notes = []
    cards = []
    for c in (item.get("cards") or [])[:CARD_MAX]:
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
- 这一页的卡片之间要构成递进，每张都能回答"所以呢"；两张卡不许讲同一件事。
- 主标题 ≤__TITLE__ 字；副标题 ≤__SUB__ 字；每张卡片标题 ≤__CT__ 字、说明 ≤__CD__ 字；
  **这一页**卡片 3-5 张、全部文字合计 ≤__TOTAL__ 字；中文标点计入字数；不要 markdown 符号；
  价格、天数、日期保持素材里的阿拉伯数字。
- scene（≤48 字）写这一页要画成什么场景，要看得见；每张卡给 v（≤24 字）写这一条画什么，
  要有形体（讲数值画仪表/钱，讲风险画断裂/警示，讲选择画岔路）。scene 与 v 不占字数预算。
__LAYOUT__
输出 JSON（只此一个对象，不要 pages 数组、不要 publish）：
{"role": "两个字", "layout_key": "上面布局键名之一", "title": "这一页主标题",
 "subtitle": "这一页副标题，可留空", "scene": "这一页的画面",
 "cards": [{"t": "卡片标题", "d": "卡片说明", "v": "这一条画什么"}]}

素材（可能已经是别人总结过的内容）：
__CONTENT__
"""


def page_plan_prompt(page_index, pages, spine, style_key, has_photo, theme, lim, content=""):
    """把"只重写第 k 页"这段话拼出来（纯函数，离线可测）。

    其他页的标题与要点会作为"别重复"的清单塞进去——不给这份清单，模型最容易出现的就是
    第二页把第一页讲过的话再说一遍。
    """
    others = []
    for i, p in enumerate(pages, 1):
        if i == page_index:
            continue
        pts = "、".join([(c.get("t") or c.get("d") or "")[:12] for c in (p.get("cards") or [])[:5]
                         if (c.get("t") or c.get("d"))])
        others.append('  - 第 %d 页「%s」：%s%s' % (i, p.get("role") or "",
                                                   p.get("title") or "（没标题）",
                                                   "；要点：" + pts if pts else ""))
    cur = pages[page_index - 1] if 1 <= page_index <= len(pages) else {}
    txt = (_PAGE_PROMPT
           .replace("__IDX__", str(page_index))
           .replace("__THEME__", (theme or "未指定，按素材自判").strip())
           .replace("__SPINE__", (spine or "（未填）"))
           .replace("__OTHERS__", "\n".join(others) or "  - （这是唯一一页，没有别页）")
           .replace("__ROLE__", cur.get("role") or "（未定）")
           .replace("__STYLE__", style_key or DEFAULT_STYLE_KEY)
           .replace("__LAYOUT__", "\n".join(layout_choices_prompt(has_photo)))
           .replace("__TITLE__", str(lim["title"])).replace("__SUB__", str(lim["subtitle"]))
           .replace("__CT__", str(lim["cardT"])).replace("__CD__", str(lim["cardD"]))
           .replace("__TOTAL__", str(lim["total"]))
           .replace("__CONTENT__", (content or "")[:8000]))
    return txt


def build_page_plan(summary, ai_config, page_index, pages, spine="", theme="",
                    style_key="", text_mode=DEFAULT_TEXT_MODE, has_photo=False):
    """只重拆一页，返回 (page, violations, notes)。其他页由调用方原样留着。

    整组「换一套」会把人手动改过的别的页一起冲掉，这条就是给"这一页不满意"准备的出口。
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
                              content=summary.get("content") or "")
    client = openai.OpenAI(api_key=api_key, base_url=api_base or "https://api.openai.com/v1",
                           timeout=300, max_retries=1)
    resp = client.chat.completions.create(
        model=model or "qwen-plus",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5, response_format={"type": "json_object"})
    data = _parse_json(resp.choices[0].message.content)
    page, notes = normalize_page(data, lim, key, has_photo, page_index,
                                 first_title=(pages[0].get("title") if page_index == 1 else ""))
    if not page:
        raise PosterError("模型这一页什么也没给（没标题也没要点），换一套没换成")
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
              .replace("__STYLES__", style_plan_prompt())
              .replace("__DENSITY__", _density_rule(text_mode))
              .replace("__TOTAL__", str(lim["total"])).replace("__TITLE__", str(lim["title"]))
              .replace("__SUB__", str(lim["subtitle"])).replace("__CT__", str(lim["cardT"]))
              .replace("__CD__", str(lim["cardD"])).replace("__FOOT__", str(lim["footer"]))
              .replace("__STYLE_FIXED__", style_fixed_prompt(style_key))
              .replace("__LAYOUT__", layout_plan_prompt(has_photo, page_hint))
              .replace("{{DATE}}", datetime.now().strftime("%m月%d日"))
              .replace("__CONTENT__", (summary.get("content") or "")[:8000])
              + guide_rules_text(frames, lim["total"]))
    resp = client.chat.completions.create(
        model=model or "qwen-plus",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3, response_format={"type": "json_object"})
    data = _parse_json(resp.choices[0].message.content)

    cards = []
    for c in (data.get("cards") or [])[:CARD_MAX]:
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
    # 攻略页那一档默认配深墨绿皮：画面是素材真帧，插画风那几套配色压不住游戏截图。
    # 只在人没显式选风格时生效——人点过下拉就以人为准，模型和默认都不许改回去。
    if pages and not style_key:
        gc = sum(1 for p in pages if p.get("layout_key") == "guide")
        if gc and gc * 2 >= len(pages):
            key = "guide_ink"
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
    return plan, _clip_publish(data.get("publish")), lint_plan(plan, lim), notes


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


# 一页装不下就翻页：页数硬上限 4（再多没人翻，而且 AI 底图按页数翻倍花钱）
PAGE_MAX = 4


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
    # （对标那套 05/05 就这样）。所以只要带了攻略页零件，卡片下限放到 0，
    # 但仍然要求这一页至少有一样能读的东西，别放一页空白出去。
    if floor and any(plan.get(k) for k in GUIDE_KEYS):
        if not (plan.get("ask") or plan.get("warn") or plan.get("bignum")
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


def layout_choices():
    """布局名单下发给界面：唯一权威源在排版层，前端抄一份迟早对不上。"""
    from . import poster_typeset as ts
    return ts.layout_choices()


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
                                else "这页没有可复用的底图：出图时要 AI 现画（0.5 元）或换一套带底图的风格")
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


def generate_image(prompt_text, size, ai_config, timeout=300):
    """调百炼原生 multimodal-generation 出图，返回 (png 字节, usage)。

    注意三件实测事实：/images/generations（OpenAI 形状）对这些模型是 404；异步
    text2image 会被拒「current user api does not support asynchronous calls」；
    模型限流 RPM=2，撞限流要退避重试而不是直接失败。返回的 OSS 链接会过期，当场下载。
    """
    api_key, api_base, _ = _require_ai(ai_config)
    url = _api_root(api_base) + "/api/v1/services/aigc/multimodal-generation/generation"
    payload = {"model": _image_model(ai_config),
               "input": {"messages": [{"role": "user", "content": [{"text": prompt_text}]}]},
               "parameters": {"size": size}}
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
               "X-DashScope-Async": "disable"}
    last = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _throttle()
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
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
            png = requests.get(img_url, timeout=180)   # OSS 链接会过期，当场下载
            png.raise_for_status()
            return png.content, (data.get("usage") or {})
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
    issues = lint_publish(pub)
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
                    preview_clicks=0, bases=None):
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

    copies=2/3 时一次出多张候选（poster_1.png…），自动把回读最干净的一张复制成 poster.png
    当默认，人工可在前端改选。AI 出图有方差，并行挑比反复重出省时间。
    """
    _require_ai(ai_config)
    if ratio not in SIZES:
        ratio = "9:16"
    if photo:
        # 真图模式下字一定是程序排的（图都占了一半画面，再让模型写字就是糊图）
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
    pub = _clip_publish(publish or llm_publish or {})
    chars = plan_chars(plan)

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
        _pg("用你上传的图排主视觉（0 元，不出 AI 底图）"
            + ("，其余 %d 页用纯色底" % (len(pages) - 1) if multi else "") + "...")
    elif manual:
        n = 1
        _pg("用你回传的 %d 张底图排版（0 元，不出 AI 底图）..." % len(manual))
    elif multi:
        n = 1
        _pg("多页模式：一页一张，不再抽卡（张数 × 页数会把花费翻倍）")
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

    candidates, usage, page_rows = [], {}, []
    photo_name = ""
    if photo:
        # 原图存一份进包里：改文案重出时不用再传一次
        photo_name = "photo_src.png"
        (out_dir / photo_name).write_bytes(photo)
    w, h = _canvas_px(ratio)
    paid = 0
    for pi, page_plan in enumerate(pages, 1):
        # 真图只当第一页的主视觉：同一张图在第 2 页再出现一次很难看。
        # 真图模式下整组都不出 AI 底图（其余页就是这套皮肤的纯色渐变），所以仍然 0 元。
        pphoto = photo if pi == 1 else None
        # 攻略页：画面是素材真帧，本来就不该叫模型画底图——整页 0 元。
        guide = bool(_ts and _ts._is_guide(page_plan))
        if guide:
            pphoto = None
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
                raw, usage = generate_image(prompts[pi - 1], SIZES[ratio], ai_config)
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
                                    ("纯色底（0 元）" if photo else "AI 现画底图 0.5 元"))
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
    if copy_text and COMPLIANCE_NOTE[:10] not in copy_text:
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
        "verify": check,
        "review": review,
        "source_title": summary.get("title") or "",
        "theme": (theme or "").strip(),
        "checklist": list(CHECKLIST),
        "created_at": _now(),
        "elapsed_sec": int((datetime.now() - started).total_seconds()),
    }
    manifest["lint"] = issues
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
