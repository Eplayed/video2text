# -*- coding: utf-8 -*-
"""图文变体轴预设表（配色 / 字体）——第一步：样张选型。

设计原则（参照开源 social-card skill 的「预设闭集」做法）：
1. 只允许从表里挑预设，不接受任意 hex，保证同一模板家族的观感统一；
2. 配色预设 = 皮肤调色板覆盖（与 skins._SKIN_PALETTES 同键名）+ 少量模板级 token 覆盖，
   下游模板 CSS 结构一行不改即可整体换色；
3. 字体预设 = 对已构建好的 CSS 做「字体栈整体替换」，同样不改模板结构。

当前覆盖范围：配色轴只对 classic 生效（tier/quest/guide 的 CSS 仍有大量硬编码色值，
需要第二步再做 token 化）；字体轴对全部模板生效。
Python 3.9 兼容：不用 match / X|Y 语法。
"""
import re

from .skins import _PALETTE_DEFAULT, _SKIN_PALETTES


# ── 模板 CSS 里现有的两套字体栈（替换时整体匹配） ──
_BODY_STACK = '"PingFang SC","Hiragino Sans GB",sans-serif'
_HEAD_RE = re.compile(r'"Songti SC","Noto Serif SC"(?:,"STSong")?,serif')
_BODY_RE = re.compile(r'"PingFang SC","Hiragino Sans GB",sans-serif')

# ── 字体预设：head/body 为 None 表示保持现状 ──
FONT_PRESETS = {
    "serif": {"label": "衬线金标（现状）", "head": None, "body": None},
    "heavy": {"label": "硬朗黑体", "head": _BODY_STACK, "body": None},
}
FONT_KEYS = ["serif", "heavy"]

# ── classic 模板新增 token 的默认值（＝ token 化之前的硬编码字面量） ──
# 放在这里而不是 _tpl_tokens 里，方便配色预设按同一批键覆盖。
CLASSIC_BASE_TOKENS = {
    "__GLOW__": "rgba(255,205,110,.10)",      # 顶部暖光余晖
    "__GOLD_RGB__": "201,168,76",             # 强调色 RGB 三元组（供 rgba() 拼透明度）
    "__ON_ACCENT__": "#241a10",               # 强调色块上的深色文字
    "__PANEL_RGB__": "10,12,22",              # 半透明面板底色 RGB 三元组
    "__RING_HI__": "#232a40",                 # 圆环图标面板渐变亮部
    "__RING_LO__": "#10131f",                 # 圆环图标面板渐变暗部
}

# ── 配色预设：palette 覆盖皮肤调色板同键名，tokens 覆盖模板级 token ──
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
        "tokens": {"__GLOW__": "rgba(255,150,70,.12)", "__GOLD_RGB__": "224,145,60",
                   "__ON_ACCENT__": "#2a1206", "__PANEL_RGB__": "22,12,7",
                   "__RING_HI__": "#3a220c", "__RING_LO__": "#1d0f05"},
    },
    "arcane_dusk": {
        "label": "暮光奥术",
        "palette": {"bg_top": "#1c1436", "bg_mid": "#0e0a1c", "bg_bot": "#251a44",
                    "gold": "#a98ce0", "gold_hi": "#e2d4ff", "gold_deep": "#5c47a0",
                    "panel": "#14102a", "panel_b": "#3b3160",
                    "text": "#ece6f8", "dim": "#a79cc0"},
        "tokens": {"__GLOW__": "rgba(150,120,255,.14)", "__GOLD_RGB__": "169,140,224",
                   "__ON_ACCENT__": "#1b1030", "__PANEL_RGB__": "14,10,26",
                   "__RING_HI__": "#2a2148", "__RING_LO__": "#131025"},
    },
}
PALETTE_KEYS = ["gold_night", "ember_forge", "arcane_dusk"]

# 配色轴已接通的模板（其余模板 CSS 仍硬编码，第二步再 token 化）
PALETTE_READY_TPLS = {"classic"}


def resolve_palette(skin_key, palette_key):
    """皮调色板 + 配色预设覆盖 → 最终 palette dict（未知预设回落到皮自身配色）。"""
    base = dict(_SKIN_PALETTES.get(skin_key) or _SKIN_PALETTES[_PALETTE_DEFAULT])
    preset = PALETTE_PRESETS.get(palette_key)
    if preset and preset.get("palette"):
        base.update(preset["palette"])
    return base


def palette_tokens(tpl_key, palette_key):
    """模板级 token：classic 给全套默认值再叠预设覆盖，其余模板暂不介入。"""
    if tpl_key not in PALETTE_READY_TPLS:
        return {}
    tokens = dict(CLASSIC_BASE_TOKENS)
    preset = PALETTE_PRESETS.get(palette_key)
    if preset and preset.get("tokens"):
        tokens.update(preset["tokens"])
    return tokens


def apply_font(css, font_key):
    """按字体预设整体替换 CSS 里的字体栈；预设缺失或未定义时原样返回。"""
    preset = FONT_PRESETS.get(font_key)
    if not preset:
        return css
    head, body = preset.get("head"), preset.get("body")
    if head:
        css = _HEAD_RE.sub(lambda m: head, css)
    if body:
        css = _BODY_RE.sub(lambda m: body, css)
    return css


def font_label(font_key):
    preset = FONT_PRESETS.get(font_key) or {}
    return preset.get("label") or font_key


def palette_label(palette_key):
    preset = PALETTE_PRESETS.get(palette_key) or {}
    return preset.get("label") or palette_key
