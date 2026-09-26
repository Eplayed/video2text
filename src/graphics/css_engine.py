# -*- coding: utf-8 -*-
"""__TOKEN__ CSS 替换引擎（Phase 1 抽取自 toutiao_graphics.py）。

画布尺寸（canvas_w/canvas_h）与资产目录（assets_dir）由调用方注入，渠道无关。
Python 3.9 兼容。
"""
from .skins import _SKIN_PALETTES, _PALETTE_DEFAULT, _BOLD_ACCENTS


def _tpl_tokens(tpl_key, skin_key):
    """模板级补充色板：minimal 的浅色纸面/墨色，bold 的高饱和强调色。"""
    if tpl_key == "minimal":
        palette = _SKIN_PALETTES.get(skin_key) or _SKIN_PALETTES[_PALETTE_DEFAULT]
        return {"__PAPER__": "#faf7f0", "__INK__": "#221d15", "__MDIM__": "#6b6252",
                "__LINE__": "#e3d9c4", "__ACCENT__": palette["gold_deep"]}
    if tpl_key == "bold":
        return {"__ACCENT__": _BOLD_ACCENTS.get(skin_key, "#ffd23f")}
    return {}


def _build_css(css_template, palette, skin_uri, assets_dir, extra_tokens=None, canvas_w=1080, canvas_h=1920):
    tokens = {"__W__": str(canvas_w), "__H__": str(canvas_h)}
    for k, v in palette.items():
        tokens["__%s__" % k.upper()] = v
    tokens["__BG_NEUTRAL__"] = (assets_dir / "neutral_bg.jpg").as_uri()
    tokens["__BG_ALLIANCE__"] = (assets_dir / "alliance_bg.jpg").as_uri()
    tokens["__BG_HORDE__"] = (assets_dir / "horde_bg.jpg").as_uri()
    tokens["__BG_SKIN__"] = skin_uri or tokens["__BG_NEUTRAL__"]
    if extra_tokens:
        tokens.update(extra_tokens)
    css = css_template
    for k, v in tokens.items():
        css = css.replace(k, v)
    return css
