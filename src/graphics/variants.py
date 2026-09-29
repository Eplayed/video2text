# -*- coding: utf-8 -*-
"""图文变体轴预设表（配色 / 字体 / 版式）。

设计原则（参照开源 social-card skill 的「预设闭集」做法）：
1. 只允许从表里挑预设，不接受任意 hex，保证同一模板家族的观感统一；
2. 配色预设 = 皮肤调色板覆盖（与 skins._SKIN_PALETTES 同键名）+ 模板级 token 覆盖，
   下游模板 CSS 结构一行不改即可整体换色；
3. 字体预设 = 对已构建好的 CSS 做「字体栈整体替换」，同样不改模板结构；
4. 版式预设 = 在构建好的 CSS 末尾追加「覆盖片段」（flex order 重排等），
   不改 HTML 结构；default 为空覆盖 ＝ 现状零行为变化。

覆盖范围：配色轴对 classic/tier/quest/guide/wechat/lilac_list/cream_gold 七个模板生效
（resolve_palette 传入 tpl_key 时，未接通模板自动忽略预设覆盖、保持皮原色）；字体轴对全部模板生效，
但机制分两路：有 Songti 衬线栈的模板走字体栈替换，全篇黑体栈的 wechat/minimal/lilac_list/cream_gold
走 FONT_PRESETS[heavy]["tpl_css"] 追加片段——调用方必须把 tpl_key 传给 apply_font，
否则这些模板的字体轴静默失效。
语义装饰色不随配色轴变化：classic 阵营色、tier 梯队红/金/紫、quest 火焰/宝石/盾牌、
guide 荧光绿路线轨、lilac_list 的网格/星标/藏青条（__L_*__）、cream_gold 的金编号/点阵/图标瓦
（__C_*__）——它们是模板的身份色。
公众号信号格（wechat）例外：它的身份色就是信号色 __ACCENT__，允许随配色轴换家族
（微信绿 / 熔火暖橙 / 暮光紫），但必须保持「浅纸面 + 深墨字 + 白卡」的正文可读性；
lilac_list / cream_gold 同理走「浅纸面」路线，共享键（纸面/墨色/线/卡/信号色）随预设换家族。

默认组合（2026-09-28 用户选定「配色 1 + 字体 1」）：鎏金夜蓝 + 衬线金标，
即现状零行为变化；所有 base token 默认值 ＝ token 化之前的硬编码字面量。
Python 3.9 兼容：不用 match / X|Y 语法。
"""
import random
import re

from .skins import _PALETTE_DEFAULT, _SKIN_PALETTES


# ── 模板 CSS 里现有的两套字体栈（替换时整体匹配） ──
_BODY_STACK = '"PingFang SC","Hiragino Sans GB",sans-serif'
_HEAD_RE = re.compile(r'"Songti SC","Noto Serif SC"(?:,"STSong")?,serif')
_BODY_RE = re.compile(r'"PingFang SC","Hiragino Sans GB",sans-serif')

# 重黑字体栈：给「全篇只有 PingFang 黑体栈」的模板用（wechat / minimal）。
# PingFang SC 最粗只到 Semibold，单靠 font-weight:900 在 Chromium 里几乎看不出变化；
# Hiragino Sans GB 自带 W6 重黑（macOS 系统内置，已核对 /System/Library/Fonts 存在），
# 换族才有真实的笔画粗细差异。缺字体时按栈内顺序回落 PingFang SC，不会渲染失败。
_HEAVY_SANS_STACK = '"Hiragino Sans GB","Heiti SC","PingFang SC",sans-serif'

# ── 字体预设：head/body 为 None 表示保持现状 ──
# tpl_css：按模板追加的覆盖片段（机制同版式轴），专治「正则无可替换目标」的静默失效——
# wechat / minimal 全篇没有 Songti 衬线栈，head 替换对它们恒为 no-op，必须靠追加片段生效。
# 片段只动字体族 / 字重 / 字距，不动盒模型尺寸，避免与版式轴的 order 重排打架；
# 标题同时收紧字距（Hiragino W6 比 PingFang 略宽），把换行风险抵回去。
FONT_PRESETS = {
    "serif": {"label": "衬线金标（现状）", "head": None, "body": None},
    "heavy": {
        "label": "硬朗黑体",
        "head": _BODY_STACK,
        "body": None,
        "tpl_css": {
            "wechat": "h1,h2{font-family:%s;font-weight:900;letter-spacing:-.01em}"
                      ".rhead,.ftr,.tile .lb,.flow .step small{font-weight:800}"
                      ".tile .t,.row .t,.flow .fh .ft,.flow .step b{font-weight:900}"
                      ".sub,.sub2,.tile .d,.row .d{font-weight:500}" % _HEAVY_SANS_STACK,
            "minimal": "h1,h2{font-family:%s;font-weight:900;letter-spacing:-.015em}"
                       ".pill,.ck .t,.row .pt,.callout .nt{font-weight:900}"
                       ".tagrow .idx,.lhead .sec .n,.row .no,.ochip{font-weight:900}"
                       ".sub,.ck .d,.row .pd,.callout .nd{font-weight:500}" % _HEAVY_SANS_STACK,
            # lilac_list / cream_gold（2026-09-29 接入）同样是全篇黑体栈、无 Songti 可替换，
            # 只能靠追加片段生效；片段只动字体族/字重，不动盒模型，避免与版式轴 order 打架。
            "lilac_list": "h1,h2{font-family:%s;font-weight:900;letter-spacing:-.01em}"
                          ".badge,.ckick,.cat,.row .no,.note .nt{font-weight:900}"
                          ".row .t,.stat .n,.sum .sd{font-weight:900}"
                          ".sub,.sub2,.stat .lb,.row .d{font-weight:500}" % _HEAVY_SANS_STACK,
            "cream_gold": "h1,h2{font-family:%s;font-weight:900;letter-spacing:-.01em}"
                          ".numrow .no,.kick,.sect .st,.step .sn,.step .sic,.note .nt{font-weight:900}"
                          ".kv .k,.kv .v,.strip .cv,.step .st{font-weight:900}"
                          ".sub,.sub2,.step .sd,.kv .kd{font-weight:500}" % _HEAVY_SANS_STACK,
        },
    },
}
FONT_KEYS = ["serif", "heavy"]
DEFAULT_FONT_KEY = "serif"

# ── 模板级 token 默认值（＝ token 化之前的硬编码字面量） ──
# 放在这里而不是 css_engine._tpl_tokens 里，方便配色预设按同一批键覆盖。
CLASSIC_BASE_TOKENS = {
    "__GLOW__": "rgba(255,205,110,.10)",      # 顶部暖光余晖
    "__GOLD_RGB__": "201,168,76",             # 强调色 RGB 三元组（供 rgba() 拼透明度）
    "__ON_ACCENT__": "#241a10",               # 强调色块上的深色文字
    "__PANEL_RGB__": "10,12,22",              # 半透明面板底色 RGB 三元组
    "__RING_HI__": "#232a40",                 # 圆环图标面板渐变亮部
    "__RING_LO__": "#10131f",                 # 圆环图标面板渐变暗部
}

TIER_BASE_TOKENS = {
    "__T_BASE__": "#0a0e1a",                  # 页面底色（body 背景 + 渐变中段 + 梯队小标签反白文字）
    "__T_BG_HI__": "#101830",                 # 背景渐变亮部
    "__T_BG_LO__": "#070a12",                 # 背景渐变暗部
    "__T_GLOW__": "rgba(90,110,190,.20)",     # 顶部环境光
    "__T_HGLOW__": "rgba(120,150,255,.35)",   # 主标题文字辉光
    "__T_ACCENT__": "#8fa2d8",                # 品牌/来源行次强调色
    "__T_DIM__": "#5a6a99",                   # 页码/脚注弱文字
    "__T_TEXT__": "#f5f7ff",                  # 主标题/条目标题
    "__T_BODYTXT__": "#eef1ff",               # 正文默认文字色
    "__T_MUTE__": "#9aa7cc",                  # 条目描述文字
    "__T_PANEL_RGB__": "15,22,38",            # 条目卡面板 RGB（.9/.94 透明度保留在原处）
    "__T_FADE__": "rgba(10,14,26,.85)",       # 头图底部渐隐
}

QUEST_BASE_TOKENS = {
    "__Q_BASE__": "#160e06",                  # 页面底色（body 背景 + 框架内圈）
    "__Q_GLOW__": "rgba(255,170,80,.10)",     # 顶部环境光
    "__Q_BG_HI__": "#2a1a0a",                 # 背景渐变亮部
    "__Q_BG_MID__": "#1a1008",                # 背景渐变中段
    "__Q_BG_LO__": "#0f0904",                 # 背景渐变暗部
    "__Q_FRAME__": "#4a3018",                 # 金属框架 / 头图边框
    "__Q_TEXT__": "#f0e8d8",                  # 正文文字
    "__Q_STRONG__": "#f5ecd8",                # 条目标题 / 行动链
    "__Q_GOLDTXT__": "#f4d98c",               # 金色小标题（ifact 图标/标题）
    "__Q_MUTE__": "#bfa98a",                  # 描述文字
    "__Q_DIM__": "#8a7355",                   # 页码/备注弱文字
    "__Q_GOLD_RGB__": "201,168,76",           # 金色 rgba 三元组（图标辉光/虚线分隔）
    "__Q_PANEL__": "rgba(30,19,8,.88)",       # ifact 面板底
    "__Q_FLOW__": "rgba(20,12,5,.88)",        # 底部行动链面板
    "__Q_FADE__": "rgba(22,14,6,.45)",        # 头图暗角
    "__Q_H1_HI__": "#ffe9b0",                 # 主标题渐变亮部
    "__Q_H1_LO__": "#93671a",                 # 主标题渐变暗部
    "__Q_BNR_HI__": "#2b1608",                # 顶部横幅渐变亮部
    "__Q_BNR_LO__": "#1a0d05",                # 顶部横幅渐变暗部
    "__Q_BNR_TXT__": "#e8d5b0",               # 顶部横幅文字
    "__Q_ICO_HI__": "#3a220c",                # ifact 图标底径向渐变亮部
    "__Q_ICO_LO__": "#221206",                # ifact 图标底径向渐变暗部
}

GUIDE_BASE_TOKENS = {
    "__G_BASE__": "#171006",                  # 页面底色（body 背景 + 画框内圈）
    "__G_GLOW__": "rgba(212,165,116,.12)",    # 顶部环境光
    "__G_BG_HI__": "#33220f",                 # 背景渐变亮部
    "__G_BG_MID__": "#241708",                # 背景渐变中段
    "__G_BG_LO__": "#1a1005",                 # 背景渐变暗部
    "__G_FRAME__": "#4a3018",                 # 羊皮纸画框
    "__G_TEXT__": "#f0e8d8",                  # 正文文字
    "__G_STRONG__": "#f5ecd8",                # 路线点标题 / 推荐路线
    "__G_GOLDTXT__": "#f4d98c",               # 金色小标题（图角标/tip 强调/主标题渐变亮部）
    "__G_SUBTXT__": "#e8d5b0",                # 副栏文字
    "__G_MUTE__": "#bfa98a",                  # 描述文字
    "__G_TIP__": "#cbb693",                   # 效率提示文字
    "__G_DIM__": "#8a7355",                   # 页码弱文字
    "__G_GOLD_RGB__": "201,168,76",           # 金色 rgba 三元组（图内圈/虚线分隔）
    "__G_GOLD_LO__": "#8b6914",               # 主标题渐变暗部 / 小标签描边
    "__G_CHIP_HI__": "#f0d78c",               # 小标签渐变亮部
    "__G_ON_ACCENT__": "#241a10",             # 金色标签上的深色文字
    "__G_TAG__": "rgba(30,19,8,.9)",          # 图角标底
    "__G_PANEL__": "rgba(20,12,5,.85)",       # 底部指令面板
    "__G_FADE__": "rgba(23,16,6,.5)",         # 攻略图暗角
}

# wechat 公众号信号格：浅纸面 + 深墨字 + 微信绿信号色（默认值＝css_engine._tpl_tokens 里的字面量）。
# ⚠ 键名与 minimal/bold 模板共用（__PAPER__/__INK__/__MDIM__/__LINE__/__ACCENT__），但不串色：
#   1) 预设 tokens 只在 palette_tokens() 里按 tpl_key 取表，minimal/bold 未登记 → 返回 {}，永不注入；
#   2) _build_css 的字面量替换发生在「单个模板自己的 CSS 字符串」内，跨模板物理隔离。
# 公众号正文可读性硬约束：任何预设都必须保持「浅纸面 + 深墨字 + 白卡」，只换信号色家族。
WECHAT_BASE_TOKENS = {
    "__PAPER__": "#f6f8f6",                   # 页面纸面底色（微冷灰绿）
    "__INK__": "#1f2329",                     # 主文字墨色
    "__MDIM__": "#646a73",                    # 次级/描述文字
    "__LINE__": "#e6e9e5",                    # 分隔细线 / 进度点底色
    "__CARD__": "#ffffff",                    # 瓦片 / 条目卡 / 流带底
    "__ACCENT__": "#07c160",                  # 信号色（微信绿）：信号线/末格反白/行动块/左边线
    "__ACCENT_DK__": "#059a4e",               # 信号色深版：眉题/小标签文字
    "__ACCENT_SOFT__": "rgba(7,193,96,.12)",  # 信号色浅底（预留）
    "__ACCENT_SHADOW__": "rgba(7,193,96,.28)",  # 信号色投影（预留）
}

# 浅紫编辑清单（lilac_list，2026-09-29 由公众号参考样本拆解落地）：
# 浅紫网格纸面 + 藏青结论条 + 星标点缀 + 序号行清单。身份色（__L_*__）不随配色轴变，
# 只有共享键（纸面/墨色/线/卡/信号色）随预设换家族——同 wechat 的处理方式。
LILAC_LIST_BASE_TOKENS = {
    "__PAPER__": "#EDEFF7",                   # 页面纸面底色（浅紫灰）
    "__INK__": "#1E2230",                     # 主文字墨色（藏黑）
    "__MDIM__": "#8A8FA0",                    # 次级/描述文字
    "__LINE__": "#E2E5F1",                    # 分隔细线 / 卡片描边
    "__CARD__": "#FCFCFE",                    # 卡片底（近白）
    "__ACCENT__": "#5B4FE0",                  # 信号色（编辑紫）：序号/星标/强调线
    "__ACCENT_DK__": "#4A40C8",               # 信号色深版：眉题文字
    "__L_GRID__": "#E2E5F1",                  # 纸面网格线（身份装饰）
    "__L_STAR__": "#E0A32E",                  # 星标金（身份装饰）
    "__L_PANEL__": "#202A44",                 # 藏青结论条底
    "__L_PANELTXT__": "#EDEFF5",              # 藏青条上的文字
    "__L_NUMBG__": "#E4E4F6",                 # 序号徽章浅紫底
}

# 奶油金插画（cream_gold，2026-09-29 同上）：奶油纸面 + 金色编号 + 右半幅插画位。
# ⚠ 该模板 CSS 里没有 __ACCENT__（金色直接用 __ACCENT_DK__ / __C_NUM__ 表达），不要补键，
#   否则配色预设注入无目标的 token，check 脚本会判「漏定义」。
CREAM_GOLD_BASE_TOKENS = {
    "__PAPER__": "#F4F0E7",                   # 页面纸面底色（奶油）
    "__INK__": "#1A1815",                     # 主文字墨色（近黑）
    "__MDIM__": "#8B8578",                    # 次级/描述文字（暖灰）
    "__LINE__": "#E3DAC6",                    # 分隔细线 / 卡片描边
    "__CARD__": "#FDFCF9",                    # 卡片底（暖白）
    "__ACCENT_DK__": "#A8762A",               # 金棕强调：眉题/键值/描边
    "__C_TILE__": "#EFE9DB",                  # 图标瓦底（身份装饰）
    "__C_DOTS__": "#D9C9A6",                  # 金色点阵装饰（无素材图时的插画位降级）
    "__C_NUM__": "#C89B42",                   # 编号金
    "__C_VAL__": "#A8762A",                   # 键值面板的值色
    "__C_ROW__": "#FDFCF9",                   # 键值行底
}

# 模板 → 基础 token 表；配色轴已接通的模板即此表键集
_TPL_BASE_TOKENS = {
    "classic": CLASSIC_BASE_TOKENS,
    "tier": TIER_BASE_TOKENS,
    "quest": QUEST_BASE_TOKENS,
    "guide": GUIDE_BASE_TOKENS,
    "wechat": WECHAT_BASE_TOKENS,
    "lilac_list": LILAC_LIST_BASE_TOKENS,
    "cream_gold": CREAM_GOLD_BASE_TOKENS,
}
PALETTE_READY_TPLS = set(_TPL_BASE_TOKENS)

# ── 配色预设：palette 覆盖皮肤调色板同键名，tokens 覆盖模板级 token ──
# tokens 是各已接通模板（classic/tier/quest/guide/wechat）键的扁平合集；
# 替换按字面量进行，当前模板 CSS 里没有的键自然 no-op。
# ⚠ 往这里加新键前必须确认：该键名不会出现在「其它已接通模板」的 CSS 里，否则会串色
#   （未接通模板拿不到 tokens，天然安全）。回归核对：scripts/check_variant_tokens.py。
PALETTE_PRESETS = {
    "gold_night": {
        "label": "鎏金夜蓝（现状）",
        "palette": {},
        "tokens": {},
    },
    "ember_forge": {
        "label": "熔火赤金",
        "palette": {"bg_top": "#241009", "bg_mid": "#140904", "bg_bot": "#2c150c",
                    "gold": "#e0913c", "gold_hi": "#ffc98a", "gold_deep": "#8f4a1c",
                    "panel": "#1d100a", "panel_b": "#54301c",
                    "text": "#f4e6d6", "dim": "#b89a7c"},
        "tokens": {
            # classic
            "__GLOW__": "rgba(255,150,70,.12)", "__GOLD_RGB__": "224,145,60",
            "__ON_ACCENT__": "#2a1206", "__PANEL_RGB__": "22,12,7",
            "__RING_HI__": "#3a220c", "__RING_LO__": "#1d0f05",
            # tier
            "__T_BASE__": "#150b05", "__T_BG_HI__": "#241009", "__T_BG_LO__": "#0b0503",
            "__T_GLOW__": "rgba(224,145,60,.16)", "__T_HGLOW__": "rgba(255,150,70,.35)",
            "__T_ACCENT__": "#e0a870", "__T_DIM__": "#99714f",
            "__T_TEXT__": "#fff6ec", "__T_BODYTXT__": "#f9ece0", "__T_MUTE__": "#cca684",
            "__T_PANEL_RGB__": "30,17,9", "__T_FADE__": "rgba(21,11,5,.85)",
            # quest
            "__Q_BASE__": "#170b04", "__Q_GLOW__": "rgba(255,140,60,.12)",
            "__Q_BG_HI__": "#2e1508", "__Q_BG_MID__": "#1d0d05", "__Q_BG_LO__": "#100702",
            "__Q_FRAME__": "#5a2c12",
            "__Q_TEXT__": "#f6e8d8", "__Q_STRONG__": "#f9ead6", "__Q_GOLDTXT__": "#ffc98a",
            "__Q_MUTE__": "#c09a76", "__Q_DIM__": "#8f6a48", "__Q_GOLD_RGB__": "224,145,60",
            "__Q_PANEL__": "rgba(34,17,7,.88)", "__Q_FLOW__": "rgba(23,12,4,.88)",
            "__Q_FADE__": "rgba(23,11,4,.45)",
            "__Q_H1_HI__": "#ffd9a0", "__Q_H1_LO__": "#8f4a1c",
            "__Q_BNR_HI__": "#301707", "__Q_BNR_LO__": "#1d0d04", "__Q_BNR_TXT__": "#f0d0a8",
            "__Q_ICO_HI__": "#42240c", "__Q_ICO_LO__": "#281406",
            # guide
            "__G_BASE__": "#170b04", "__G_GLOW__": "rgba(230,150,80,.12)",
            "__G_BG_HI__": "#33190a", "__G_BG_MID__": "#241006", "__G_BG_LO__": "#1a0b03",
            "__G_FRAME__": "#5a2c12",
            "__G_TEXT__": "#f6e8d8", "__G_STRONG__": "#f9ead6", "__G_GOLDTXT__": "#ffc98a",
            "__G_SUBTXT__": "#f0d0a8", "__G_MUTE__": "#c09a76", "__G_TIP__": "#d0a982",
            "__G_DIM__": "#8f6a48", "__G_GOLD_RGB__": "224,145,60", "__G_GOLD_LO__": "#8f4a1c",
            "__G_CHIP_HI__": "#ffbe78", "__G_ON_ACCENT__": "#2a1206",
            "__G_TAG__": "rgba(34,17,7,.9)", "__G_PANEL__": "rgba(23,12,4,.85)",
            "__G_FADE__": "rgba(23,11,4,.5)",
            # wechat（公众号信号格）：保持浅纸面白卡深墨字，只把信号色换成熔火暖橙
            "__PAPER__": "#fbf6f1", "__INK__": "#231d18", "__MDIM__": "#6d6157",
            "__LINE__": "#ece1d6", "__CARD__": "#ffffff",
            "__ACCENT__": "#d9622b", "__ACCENT_DK__": "#a9451a",
            "__ACCENT_SOFT__": "rgba(217,98,43,.12)", "__ACCENT_SHADOW__": "rgba(217,98,43,.28)",
        },
    },
    "arcane_dusk": {
        "label": "暮光奥术",
        "palette": {"bg_top": "#1c1436", "bg_mid": "#0e0a1c", "bg_bot": "#251a44",
                    "gold": "#a98ce0", "gold_hi": "#e2d4ff", "gold_deep": "#5c47a0",
                    "panel": "#14102a", "panel_b": "#3b3160",
                    "text": "#ece6f8", "dim": "#a79cc0"},
        "tokens": {
            # classic
            "__GLOW__": "rgba(150,120,255,.14)", "__GOLD_RGB__": "169,140,224",
            "__ON_ACCENT__": "#1b1030", "__PANEL_RGB__": "14,10,26",
            "__RING_HI__": "#2a2148", "__RING_LO__": "#131025",
            # tier
            "__T_BASE__": "#120d22", "__T_BG_HI__": "#1e1538", "__T_BG_LO__": "#0a0716",
            "__T_GLOW__": "rgba(140,110,220,.20)", "__T_HGLOW__": "rgba(160,130,255,.35)",
            "__T_ACCENT__": "#b3a2e0", "__T_DIM__": "#7a6a9e",
            "__T_TEXT__": "#f6f2ff", "__T_BODYTXT__": "#ece6f8", "__T_MUTE__": "#b7abd0",
            "__T_PANEL_RGB__": "24,18,44", "__T_FADE__": "rgba(16,11,30,.85)",
            # quest
            "__Q_BASE__": "#130e24", "__Q_GLOW__": "rgba(170,140,255,.10)",
            "__Q_BG_HI__": "#251a44", "__Q_BG_MID__": "#181130", "__Q_BG_LO__": "#0e0a1c",
            "__Q_FRAME__": "#3f3168",
            "__Q_TEXT__": "#ece6f8", "__Q_STRONG__": "#f0eafc", "__Q_GOLDTXT__": "#d8c8f5",
            "__Q_MUTE__": "#a79cc0", "__Q_DIM__": "#8274a4", "__Q_GOLD_RGB__": "169,140,224",
            "__Q_PANEL__": "rgba(26,19,48,.88)", "__Q_FLOW__": "rgba(18,13,34,.88)",
            "__Q_FADE__": "rgba(16,11,30,.45)",
            "__Q_H1_HI__": "#e2d4ff", "__Q_H1_LO__": "#5c47a0",
            "__Q_BNR_HI__": "#241a42", "__Q_BNR_LO__": "#16102c", "__Q_BNR_TXT__": "#d9cdf0",
            "__Q_ICO_HI__": "#2c2250", "__Q_ICO_LO__": "#1a1432",
            # guide
            "__G_BASE__": "#130e24", "__G_GLOW__": "rgba(180,150,240,.12)",
            "__G_BG_HI__": "#2a1e4c", "__G_BG_MID__": "#1d1436", "__G_BG_LO__": "#140e26",
            "__G_FRAME__": "#3f3168",
            "__G_TEXT__": "#ece6f8", "__G_STRONG__": "#f0eafc", "__G_GOLDTXT__": "#d8c8f5",
            "__G_SUBTXT__": "#d9cdf0", "__G_MUTE__": "#a79cc0", "__G_TIP__": "#b4a8d2",
            "__G_DIM__": "#8274a4", "__G_GOLD_RGB__": "169,140,224", "__G_GOLD_LO__": "#5c47a0",
            "__G_CHIP_HI__": "#cbb8f0", "__G_ON_ACCENT__": "#1b1030",
            "__G_TAG__": "rgba(26,19,48,.9)", "__G_PANEL__": "rgba(18,13,34,.85)",
            "__G_FADE__": "rgba(16,11,30,.5)",
            # wechat（公众号信号格）：保持浅纸面白卡深墨字，只把信号色换成暮光奥术紫
            "__PAPER__": "#f5f4fa", "__INK__": "#1e1b2c", "__MDIM__": "#67617d",
            "__LINE__": "#e6e3ef", "__CARD__": "#ffffff",
            "__ACCENT__": "#7b5fd0", "__ACCENT_DK__": "#5b41ab",
            "__ACCENT_SOFT__": "rgba(123,95,208,.12)", "__ACCENT_SHADOW__": "rgba(123,95,208,.28)",
        },
    },
}
PALETTE_KEYS = ["gold_night", "ember_forge", "arcane_dusk"]
DEFAULT_PALETTE_KEY = "gold_night"

# ── 版式预设：CSS 覆盖层，不改 HTML 结构 ──
# 原理：各模板 .wrap-in 均为 flex 纵向容器，子块默认可用 order 重排；
# 覆盖 CSS 在 _build_css 之后追加到样式表末尾，同特异度后来居上。
# 每预设的 css 为 {模板键: 覆盖片段}；未覆盖的模板保持现状（零行为变化）。
# 默认版式 default 为空覆盖 ＝ 现状。
#
# 封面与内页都要变（2026-09-29 修复：此前只写了封面级选择器 .hero/.gmap，
# classic/tier 内页块名是 .band/.panels/.note/.tgrid，选择器落空 → 内页零变化）。
# 内页规则一律用「直接子选择器」或「内页专有类」，避免误伤同名嵌套元素：
#   .wrap-in>.note  —— classic 封面的 .note 嵌在 .cta 里，不加 > 会被连带重排；
#   .tier>.tgrid    —— .tier 只存在于 tier 内页，封面用的是 .tpreview，天然隔离。
#
# 全模板覆盖（2026-09-29 二次修复：此前只覆盖 classic/tier/quest/guide，
# 用户实际最常用的 magazine 拿到空串 → 版式/随机静默失效、界面还无从察觉。
# 现补齐 magazine/minimal/bold，头条下拉里的 7 个模板全部生效）。
# 无主图内页（minimal/bold）的「图先行」语义就近取该页主视觉块（callout/warn）上浮。
#
# 2026-09-29 再补 wechat（公众号信号格，零图片依赖）：封面主视觉＝.tiles 要点瓦片，
# 内页主视觉＝.note 总结条、要点块＝.rows 条目卡；两个预设都要保持语义差异
# （hero_first＝主视觉上浮，summary_first＝要点先给/主视觉沉底）。
# 至此头条 7 模板 + 公众号信号格全部有版式覆盖，不再有静默 no-op 的模板。
LAYOUT_PRESETS = {
    "default": {
        "label": "经典排布（现状）",
        "css": {},
    },
    "hero_first": {
        "label": "图先行（大图置顶）",
        "css": {
            # 封面：报头行钉在最上（order:-2），主图上移到标题之前（order:-1）
            # classic 内页：横带图钉在报头之下并解除 700px 高度上限（图更抢眼），小结压尾
            # tier 内页：无主图，改为放大梯队大徽章（内页的主视觉）
            # quest/guide 内页：.hero.list / .gmap.list 与封面同名，被同一选择器上浮到标题之前
            "classic": ".topline{order:-2}.hero{order:-1}"
                       ".wrap-in>.band{order:-1;max-height:820px}"
                       ".wrap-in>.note{order:2;margin-top:20px}",
            "tier": ".mast{order:-2}.hero{order:-1}"
                    ".tier>.thead{margin-bottom:22px}.tbadge{font-size:104px;width:176px}",
            "quest": ".mast{order:-2}.hero{order:-1}",
            "guide": ".mast{order:-2}.gmap{order:-1}",
            # magazine 封面默认已图先于标题，改为放大主图强化「图先行」；内页横带图同步放大
            "magazine": ".photo{flex-basis:54%}.wrap-in>.lband{flex-basis:30%}",
            # minimal/bold 封面：主图上移到标题之前并放大；内页无主图，主视觉块（callout/warn）上浮
            # （.photo/.callout 分属封面/内页不共存，.tagrow 两页都有、钉顶即可）
            "minimal": ".tagrow{order:-2}.photo{order:-1;flex-basis:38%}"
                       ".wrap-in>.callout{order:-1;margin-top:0;margin-bottom:26px}",
            "bold": ".tagrow{order:-2}.poster{order:-1;flex-basis:34%}"
                    ".wrap-in>.warn{order:-1;margin-top:0;margin-bottom:28px}",
            # wechat 公众号信号格（2026-09-29 接入）：零图片模板，「图先行」就近取该页主视觉块上浮——
            # 封面＝2×2 要点瓦片 .tiles（自带 flex:1，上浮后成为顶部大视觉块）；
            # 内页＝左绿边线总结条 .wrap-in>.note（该页最重的主视觉）。
            # 一律用「负 order 上浮」范式（minimal/bold 先例）：.rhead/.sig/.kick 给更负值钉在原位，
            # .ftr 完全不碰——它靠 DOM 末位 + order:0 沉底，正 order 会把它挤出末尾。
            "wechat": ".rhead{order:-4}.sig{order:-3}.kick{order:-3}"
                      ".tiles{order:-2}"
                      ".wrap-in>.note{order:-2;margin-top:26px}.wrap-in>h2{margin-top:30px}",
            # lilac_list 浅紫编辑清单（2026-09-29 接入）：零图片模板，「图先行」＝该页最重的主视觉块上浮。
            # 封面＝三张统计卡 .stats 抬到标题之前（.sum 的 margin-top:auto 保持不动，
            # 结论条 + 页脚继续沉底，页面不留白）；内页＝星金边提示条 .note 上浮（同 wechat 内页范式）。
            "lilac_list": ".badge{order:-4}.stars{order:-3}.ckick{order:-3}"
                          ".stats{order:-2;margin-top:30px;padding-top:0}h1{margin-top:30px}"
                          ".cat{order:-4}.wrap-in>.note{order:-2;margin-top:26px}"
                          ".wrap-in>h2{margin-top:30px}",
            # cream_gold 奶油金插画（2026-09-29 接入）：右半幅插画位 .illo 是 absolute（不参与 order），
            # 「图先行」取流内主视觉——封面＝点线步骤轨 .steps 上浮，同时解除它的 flex:1
            # （否则它会吞掉整页高度、把标题挤到底），改由 .ftr 的 margin-top:auto 兜住页脚沉底；
            # 内页＝三栏收益条 .strip 上浮到标题之前。
            "cream_gold": ".steps{order:-1;flex:0 0 auto;margin-top:0;margin-bottom:26px}"
                          ".ftr{margin-top:auto}"
                          ".kick{order:-4}.strip{order:-2;margin-top:26px}"
                          ".wrap-in>h2{margin-top:30px}",
        },
    },
    "summary_first": {
        "label": "要点先行（摘要上浮）",
        "css": {
            # 封面：主图沉底（order:1），标题下方的摘要块（cta/tpreview/ifact/panelbox）自然上浮
            # classic 内页：横带图沉到要点之后、小结压尾（标题+分区+要点先给）
            #   ⚠ 必须同时解除 h2 的 margin-top:-84px——那个负边距是为「标题压在横带图下沿」
            #   设计的；band 沉底后标题会顶进报头行，首字被 brand 徽章金底吞掉（2026-09-29
            #   用户报障「有些字被挡住了」，包 56 img2「机」/img4「B」实测复现）。
            # tier 内页：条目网格上浮到大徽章行之前（要点先行）
            # quest/guide 内页：图块随 .hero/.gmap 一起沉底，步骤轨/路线轨上浮
            "classic": ".hero{order:1}"
                       ".wrap-in>.band{order:1}.wrap-in>.note{order:2;margin-top:20px}"
                       ".wrap-in>h2{margin-top:18px}",
            "tier": ".hero{order:1}"
                    ".tier>.tgrid{order:-1;margin-bottom:16px}.tier>.thead{order:1;margin-bottom:0}",
            "quest": ".hero{order:1}",
            "guide": ".gmap{order:1}",
            # magazine 封面：大图沉到标题之后、目录之前；内页横带图沉到条目之后、引言之前
            "magazine": ".photo{order:1}.index{order:2}"
                        ".wrap-in>.lband{order:1}.wrap-in>.note-q{order:2}",
            # minimal/bold 封面：主图沉底，要点清单上浮；内页：提醒块上浮到标题正下方（要点导语位）
            "minimal": ".photo{order:1}"
                       ".tagrow{order:-3}.lhead{order:-2}"
                       ".wrap-in>.callout{order:-1;margin-top:0;margin-bottom:26px}",
            "bold": ".poster{order:1}"
                    ".tagrow{order:-3}.lhead{order:-2}"
                    ".wrap-in>.warn{order:-1;margin-top:0;margin-bottom:28px}",
            # wechat 公众号信号格（2026-09-29 接入）：封面把要点瓦片沉到阅读流带之后（标题+阅读导引先行），
            # .ftr 同时给更大 order(3) 保住页脚末位——这是唯一需要正 order 的场景，必须连带处理页脚；
            # 内页把要点条目卡 .rows 上浮到标题之前＝要点先行，.note/.ftr 顺势留在尾部。
            "wechat": ".rhead{order:-4}.kick{order:-3}"
                      ".rows{order:-2;margin-top:26px}.wrap-in>h2{margin-top:30px}"
                      ".tiles{order:1;align-content:end}.ftr{order:3}",
            # lilac_list（2026-09-29 接入）：封面把藏青结论条 .sum 抬到标题之前＝结论先行，
            # 三张统计卡 .stats 下沉为支撑数据——解除 .sum 的 margin-top:auto、给 .stats 加 flex:1
            # 撑满余高（否则下半页留白），.ftr 给 order:3 保住页脚末位（唯一需要正 order 的场景）；
            # 内页把编号白卡列表 .rows 抬到标题之前＝要点先行（同 wechat 内页范式）。
            "lilac_list": ".badge{order:-4}.stars{order:-3}.ckick{order:-3}"
                          ".sum{order:-2;margin-top:30px}"
                          ".stats{order:1;flex:1;align-content:center;margin-top:34px;padding-top:0}"
                          ".ftr{order:3}"
                          ".cat{order:-4}.rows{order:-2;margin-top:26px}"
                          ".wrap-in>h2{margin-top:30px}",
            # cream_gold（2026-09-29 接入）：封面把副题 .sub 抬到「金编号 + 标题」之前＝摘要先行；
            # 内页把提示条 .note 抬到标题之前＝结论先行（.panel 保持 flex:1 撑满余高，不动盒模型）。
            "cream_gold": ".sub{order:-1;margin-top:0;margin-bottom:22px}"
                          ".kick{order:-4}.wrap-in>.note{order:-2;margin-top:24px}"
                          ".wrap-in>h2{margin-top:28px}",
        },
    },
}
LAYOUT_KEYS = ["default", "hero_first", "summary_first"]
DEFAULT_LAYOUT_KEY = "default"
# 随机版式：只从「非 default」预设里挑，保证每次生成都看得到与经典排布不同的块序；
# 一次生成只抽一次（整包统一版式，不会一张一个样），抽中的真实键写进 manifest.layout。
RANDOM_LAYOUT_KEY = "random"
RANDOM_POOL = ["hero_first", "summary_first"]


def resolve_keys(palette_key, font_key):
    """None/未知值 → 回落到默认组合；返回 (palette_key, font_key)。"""
    p = palette_key if palette_key in PALETTE_PRESETS else DEFAULT_PALETTE_KEY
    f = font_key if font_key in FONT_PRESETS else DEFAULT_FONT_KEY
    return p, f


def resolve_palette(skin_key, palette_key, tpl_key=None):
    """皮调色板 + 配色预设覆盖 → 最终 palette dict（未知预设回落到皮自身配色）。

    tpl_key 传入且该模板未接通配色轴时，忽略预设覆盖（杂志/极简/大字报等保持皮原色）。
    """
    base = dict(_SKIN_PALETTES.get(skin_key) or _SKIN_PALETTES[_PALETTE_DEFAULT])
    if tpl_key is not None and tpl_key not in PALETTE_READY_TPLS:
        return base
    preset = PALETTE_PRESETS.get(palette_key)
    if preset and preset.get("palette"):
        base.update(preset["palette"])
    return base


def palette_tokens(tpl_key, palette_key):
    """模板级 token：先给该模板全套默认值，再叠预设覆盖；未接通模板不介入。"""
    base = _TPL_BASE_TOKENS.get(tpl_key)
    if base is None:
        return {}
    tokens = dict(base)
    preset = PALETTE_PRESETS.get(palette_key)
    if preset and preset.get("tokens"):
        tokens.update(preset["tokens"])
    return tokens


def apply_font(css, font_key, tpl_key=None):
    """按字体预设整体替换 CSS 里的字体栈；预设缺失或未定义时原样返回。

    tpl_key：可选。传入时会追加该预设的「按模板覆盖片段」（FONT_PRESETS[键]["tpl_css"]），
    用于没有衬线栈可替换的模板（wechat / minimal）——否则字体轴对它们恒为 no-op。
    不传（默认）＝ 只做字体栈替换，历史调用点零行为变化。
    """
    preset = FONT_PRESETS.get(font_key)
    if not preset:
        return css
    head, body = preset.get("head"), preset.get("body")
    if head:
        css = _HEAD_RE.sub(lambda m: head, css)
    if body:
        css = _BODY_RE.sub(lambda m: body, css)
    if tpl_key:
        frag = (preset.get("tpl_css") or {}).get(tpl_key)
        if frag:
            css += "\n/* font:%s */\n%s" % (font_key, frag)
    return css


def font_tpl_css(tpl_key, font_key):
    """该模板在该字体预设下的追加片段；默认/未知/未覆盖模板返回空串（零行为变化）。

    单独暴露一个读取口，供回归核对脚本与前端 hint 判定「这个模板的字体轴到底生不生效」。
    """
    preset = FONT_PRESETS.get(font_key)
    if not preset:
        return ""
    return (preset.get("tpl_css") or {}).get(tpl_key, "")


def font_label(font_key):
    preset = FONT_PRESETS.get(font_key) or {}
    return preset.get("label") or font_key


def palette_label(palette_key):
    preset = PALETTE_PRESETS.get(palette_key) or {}
    return preset.get("label") or palette_key


def resolve_layout(layout_key):
    """None/未知值 → 回落到默认版式（现状排布）。"""
    return layout_key if layout_key in LAYOUT_PRESETS else DEFAULT_LAYOUT_KEY


def layout_css(tpl_key, layout_key):
    """该模板在该版式预设下的覆盖 CSS 片段；默认/未知/未覆盖模板返回空串（零行为变化）。"""
    preset = LAYOUT_PRESETS.get(layout_key)
    if not preset:
        return ""
    return (preset.get("css") or {}).get(tpl_key, "")


def layout_label(layout_key):
    preset = LAYOUT_PRESETS.get(layout_key) or {}
    return preset.get("label") or layout_key


def pick_random_layout():
    """随机版式：从非 default 预设池里抽一个真实键返回。

    一次生成只应调用一次（整包统一版式，不会一张一个样），
    抽中的真实键再经 resolve_layout / layout_css 生效，并写进 manifest.layout，
    保证「随机」在产物里可追溯（manifest 落的是 hero_first / summary_first，不是 random）。
    """
    return random.choice(RANDOM_POOL)
