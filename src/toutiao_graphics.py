# -*- coding: utf-8 -*-
"""头条图文生成 v4 —— 薄壳（Phase 1 内核抽取后）。

真实实现已迁出：
- 渠道无关内核 `src/graphics/`：skins（皮肤/调色板/素材） · css_engine（__TOKEN__ 替换）
  · templates（7 套 CSS + 14 渲染函数 + 注册表） · renderer（Playwright 量高缩放截图）
  · package（manifest 包管理） · ir（卡片 schema）
- 头条渠道适配器 `src/graphics/channels/toutiao.py`：目录/画布常量、卡片化 prompt、
  LLM 调用、本地兜底、generate_graphics 主流程、ToutiaoChannel

本文件只做符号转发，保持既有引用面零改动（全仓仅 web/app.py 用 5 个公开符号：
generate_graphics / list_packages / get_package / delete_package / OUTPUT_DIR）。
内核把目录与画布改成了参数注入，签名变过的函数在此包一层，对外仍是原签名。

Python 3.9 兼容：不用 match / X|Y 语法。
"""
from .graphics import css_engine, renderer, skins, templates
from .graphics.channels import toutiao as _impl

# ── 目录 / 画布常量（与适配器同一对象，取值不变）──
ROOT = _impl.ROOT
OUTPUT_DIR = _impl.OUTPUT_DIR
CANVAS_W = _impl.CANVAS_W
CANVAS_H = _impl.CANVAS_H
_ASSETS_DIR = _impl._ASSETS_DIR

# ── 皮肤 / 调色板 / 题材素材映射 ──
PRESET_SKINS = skins.PRESET_SKINS
_PALETTE_DEFAULT = skins._PALETTE_DEFAULT
_SKIN_PALETTES = skins._SKIN_PALETTES
_BOLD_ACCENTS = skins._BOLD_ACCENTS
_THEME_ASSET_DIRS = skins._THEME_ASSET_DIRS
_THEME_FALLBACK_SKIN_DIRS = skins._THEME_FALLBACK_SKIN_DIRS

# ── 7 套模板 CSS + 注册表 ──
_CSS_CLASSIC = templates._CSS_CLASSIC
_CSS_MAGAZINE = templates._CSS_MAGAZINE
_CSS_MINIMAL = templates._CSS_MINIMAL
_CSS_BOLD = templates._CSS_BOLD
_CSS_GUIDE = templates._CSS_GUIDE
_CSS_TIER = templates._CSS_TIER
_CSS_QUEST = templates._CSS_QUEST
_TEMPLATES = templates._TEMPLATES

# ── 渲染辅助与 14 个渲染函数（签名未变，直接转发）──
_esc = templates._esc
_page_open = templates._page_open
_corners = templates._corners
_pgdate = templates._pgdate
_cover_html_classic = templates._cover_html_classic
_list_html_classic = templates._list_html_classic
_cover_html_magazine = templates._cover_html_magazine
_list_html_magazine = templates._list_html_magazine
_cover_html_minimal = templates._cover_html_minimal
_list_html_minimal = templates._list_html_minimal
_cover_html_bold = templates._cover_html_bold
_list_html_bold = templates._list_html_bold
_cover_html_guide = templates._cover_html_guide
_list_html_guide = templates._list_html_guide
_tier_badge = templates._tier_badge
_cover_html_tier = templates._cover_html_tier
_list_html_tier = templates._list_html_tier
_chain_html = templates._chain_html
_cover_html_quest = templates._cover_html_quest
_list_html_quest = templates._list_html_quest

# ── 选图逻辑（签名未变，直接转发）──
_img_ratio = skins._img_ratio
_pick_cover_image = skins._pick_cover_image
_pick_band_image = skins._pick_band_image
_tpl_tokens = css_engine._tpl_tokens

# ── 头条业务：prompt / LLM 卡片化 / 兜底 / 主流程 / 包管理 ──
_CARDS_PROMPT = _impl._CARDS_PROMPT
_build_cards_prompt = _impl._build_cards_prompt
_llm_cards = _impl._llm_cards
_parse_json = _impl._parse_json
_split_tags = _impl._split_tags
_fallback_cards = _impl._fallback_cards
generate_graphics = _impl.generate_graphics
list_packages = _impl.list_packages
get_package = _impl.get_package
delete_package = _impl.delete_package
ToutiaoChannel = _impl.ToutiaoChannel


# ── 签名兼容包装：内核改为目录/画布参数注入，此处补回头条默认值 ──
def resolve_skin(skin):
    """皮名 → (skin_key, 背景图 URI)；资产目录固定为头条 _assets。"""
    return skins.resolve_skin(skin, _ASSETS_DIR)


def _build_css(css_template, palette, skin_uri, extra_tokens=None):
    """模板 CSS + 调色板 + 背景 URI → 成品 CSS（画布固定 1080x1920）。"""
    return css_engine._build_css(css_template, palette, skin_uri, _ASSETS_DIR,
                                 extra_tokens, CANVAS_W, CANVAS_H)


def _theme_image_paths(theme, skin_key):
    """题材优先、皮名兜底，找 output/toutiao/<文件夹> 里的插画素材。"""
    return skins._theme_image_paths(theme, skin_key, OUTPUT_DIR)


def _guide_image_paths(theme, skin_key):
    """攻略图解模板素材：<题材>攻略图/ 优先，回退题材插画目录。"""
    return skins._guide_image_paths(theme, skin_key, OUTPUT_DIR)


def _hero_uri(card, cover_img, skin_uri):
    """卡片主视觉图 URI：题材插画 > 皮背景图 > wow 阵营图。"""
    return skins._hero_uri(card, cover_img, skin_uri, _ASSETS_DIR)


def _render_card_html(page, html_path, png_path):
    """Playwright 渲染单页：量高 → 等比缩放 → 截图 1080x1920。"""
    return renderer.render_card_html(page, html_path, png_path, CANVAS_W, CANVAS_H)
