#!/usr/bin/env python3
"""变体三轴登记完整性核对（配色 / 字体 / 版式）。

用途：新模板接入变体轴、或改动 PALETTE_PRESETS / LAYOUT_PRESETS 之后跑一遍，
防止已经复发过两次的「静默失效」再犯：
  1. 配色轴漏登记：模板不在 _TPL_BASE_TOKENS 里 → resolve_palette / palette_tokens 静默 no-op，
     用户选了配色但图没变，界面无从察觉；
  2. 配色串色：预设 tokens 是「所有已接通模板键的扁平合集」，若某键名同时出现在
     别的已接通模板 CSS 里，那个模板会被连带换色；
  3. 版式轴漏覆盖：LAYOUT_PRESETS 的 css 字典缺该模板键 → 版式/随机版式静默 no-op；
  4. 版式选择器落空：覆盖片段里的类名在该模板 CSS 中不存在（例如只写封面块名，
     内页块名不同）→ 片段注入了但一半页面零变化；
  5. token 漏定义：模板 CSS 用到的 __XXX__ 既不在自己的 base 表、也不在皮肤调色板里
     → 渲染产物残留占位符字面量。
  6. 字体轴静默失效：该模板既没有可被正则命中的字体栈、又没有按模板追加片段
     → 用户选了字体但图没变（无衬线栈模板必须走 tpl_css 片段）。

跑法（项目根目录）：
    env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/check_variant_tokens.py
末行输出 ALL_OK 表示全部通过；否则输出 FAIL=n。Python 3.9 兼容（不用 match / X|Y）。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.graphics import css_engine, skins, templates, variants  # noqa: E402

TOKEN_RE = re.compile(r"__[A-Z0-9_]+__")
CLASS_RE = re.compile(r"\.([A-Za-z][\w-]*)")

# 皮肤调色板键 → 模板 CSS 里的 token 形式（_build_css 的转换规则）
_SKIN_TOKENS = set()
for _pal in list(skins._SKIN_PALETTES.values()):
    for _k in _pal:
        _SKIN_TOKENS.add("__%s__" % str(_k).upper())
# _build_css 另外注入的固定 token：画布尺寸 + 背景资产 URI（ neutral/alliance/horde/skin ）
_FIXED_TOKENS = {"__W__", "__H__", "__BG_TOP__", "__BG_MID__", "__BG_BOT__",
                 "__BG_HERO__", "__BG_FULL__", "__BG_SKIN__", "__BG_NEUTRAL__",
                 "__BG_ALLIANCE__", "__BG_HORDE__"}


def check_palette(bad):
    """配色轴：串色 / 漏登记 / 默认零变化 / token 漏定义。"""
    css_tokens = {k: set(TOKEN_RE.findall(v["css"])) for k, v in templates._TEMPLATES.items()}
    ready = sorted(variants.PALETTE_READY_TPLS)
    print("[配色] 已接通模板：%s" % ready)

    # 1+2) 预设 tokens 命中已接通模板 CSS 的键，必须都属于该模板自己的 base 表
    for pk in variants.PALETTE_KEYS:
        preset_tokens = set((variants.PALETTE_PRESETS[pk].get("tokens") or {}).keys())
        for tpl in ready:
            own = set(variants._TPL_BASE_TOKENS[tpl].keys())
            foreign = (preset_tokens & css_tokens[tpl]) - own
            if foreign:
                bad += 1
                print("  [串色] 预设 %s 命中 %s 模板 CSS 的外来键：%s" % (pk, tpl, sorted(foreign)))

    # 3) 未接通模板必须拿不到任何预设 token
    for tpl in sorted(set(templates._TEMPLATES) - set(ready)):
        for pk in variants.PALETTE_KEYS:
            got = variants.palette_tokens(tpl, pk)
            if got:
                bad += 1
                print("  [异常] 未登记模板 %s 在预设 %s 下拿到 %d 个 token" % (tpl, pk, len(got)))
        print("  [未登记] %s：配色预设 no-op（保持皮原色）" % tpl)

    # 4) 默认预设必须等于 base 表（现状零行为变化）
    for tpl in ready:
        base = variants._TPL_BASE_TOKENS[tpl]
        got = variants.palette_tokens(tpl, variants.DEFAULT_PALETTE_KEY)
        diff = {k: (base[k], got.get(k)) for k in base if got.get(k) != base[k]}
        if diff:
            bad += 1
            print("  [默认漂移] %s 在 %s 下与 base 不一致：%s"
                  % (tpl, variants.DEFAULT_PALETTE_KEY, diff))

    # 5) 模板 CSS 用到的 token 必须有来源（自己的 base 表 / 皮肤调色板 / 固定注入）
    for tpl, used in sorted(css_tokens.items()):
        base = set(variants._TPL_BASE_TOKENS.get(tpl, {}).keys())
        # 未登记模板走 css_engine._tpl_tokens 兜底，一并计入合法来源
        for skin_key in list(skins._SKIN_PALETTES):
            base |= set((css_engine._tpl_tokens(tpl, skin_key) or {}).keys())
        orphan = used - base - _SKIN_TOKENS - _FIXED_TOKENS
        if orphan:
            bad += 1
            print("  [漏定义] %s 模板 CSS 用到但无来源的 token：%s" % (tpl, sorted(orphan)))

    # wechat 专项：三预设的信号色必须互不相同，且纸面保持浅色（公众号正文可读性）
    accents = {}
    for pk in variants.PALETTE_KEYS:
        got = variants.palette_tokens("wechat", pk)
        accents[pk] = got.get("__ACCENT__")
        paper = str(got.get("__PAPER__") or "")
        if not paper.startswith("#f"):
            bad += 1
            print("  [可读性] wechat 预设 %s 纸面底色不是浅色：%s" % (pk, paper))
    if len(set(accents.values())) != len(accents):
        bad += 1
        print("  [雷同] wechat 三预设信号色未拉开差异：%s" % accents)
    print("  [wechat] 三预设信号色：%s" % accents)
    return bad


def check_layout(bad):
    """版式轴：每个模板都要有非空片段，且片段里的类名在该模板 CSS 中存在。"""
    for lk in variants.LAYOUT_KEYS:
        if lk == variants.DEFAULT_LAYOUT_KEY:
            continue
        for tpl in sorted(templates._TEMPLATES):
            frag = variants.layout_css(tpl, lk)
            if not frag.strip():
                bad += 1
                print("  [漏覆盖] 版式 %s 缺模板 %s 的片段（会静默 no-op）" % (lk, tpl))
                continue
            css_text = templates._TEMPLATES[tpl]["css"]
            missing = sorted(c for c in set(CLASS_RE.findall(frag))
                             if ("." + c) not in css_text)
            if missing:
                bad += 1
                print("  [选择器落空] 版式 %s / 模板 %s 的类名不在其 CSS 里：%s"
                      % (lk, tpl, missing))
        print("[版式] %s：%d 个模板片段已核对" % (lk, len(templates._TEMPLATES)))
    return bad


def check_font(bad):
    """字体轴：默认预设必须零变化；非默认预设对每个模板都必须「真的有变化」。

    变化来源有两条，命中任意一条即算生效：
      a) 字体栈替换（模板 CSS 里有 Songti 衬线栈 / PingFang 黑体栈可被正则命中）；
      b) 按模板追加片段（FONT_PRESETS[键]["tpl_css"]，给全篇黑体栈的 wechat / minimal 用）。
    两条都没有 ＝ 用户选了字体但图没变（2026-09-29 wechat 接入时实测命中过），必须报错。
    """
    for tpl in sorted(templates._TEMPLATES):
        css_text = templates._TEMPLATES[tpl]["css"]
        if variants.apply_font(css_text, variants.DEFAULT_FONT_KEY, tpl) != css_text:
            bad += 1
            print("  [默认漂移] 字体 %s / %s 不是零行为变化" % (tpl, variants.DEFAULT_FONT_KEY))
        for fk in variants.FONT_KEYS:
            if fk == variants.DEFAULT_FONT_KEY:
                continue
            out = variants.apply_font(css_text, fk, tpl)
            if out == css_text:
                bad += 1
                print("  [静默失效] 字体 %s / %s：无变化（无字体栈可替换，也无追加片段）" % (tpl, fk))
                continue
            frag = variants.font_tpl_css(tpl, fk)
            missing = sorted(c for c in set(CLASS_RE.findall(frag))
                             if ("." + c) not in css_text)
            if missing:
                bad += 1
                print("  [选择器落空] 字体 %s / %s 片段的类名不在其 CSS 里：%s" % (fk, tpl, missing))
            modes = []
            if variants.apply_font(css_text, fk) != css_text:
                modes.append("字体栈替换")
            if frag:
                modes.append("追加片段")
            print("[字体] %s / %s：生效（%s）" % (tpl, fk, "+".join(modes) or "未识别"))
    return bad


def main():
    bad = 0
    bad = check_palette(bad)
    bad = check_layout(bad)
    bad = check_font(bad)
    print("ALL_OK" if bad == 0 else "FAIL=%d" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
