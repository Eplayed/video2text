# -*- coding: utf-8 -*-
"""渠道无关皮肤 / 调色板 / 题材素材解析内核（Phase 1 抽取自 toutiao_graphics.py）。

资产目录（assets_dir）与输出目录（output_dir）由调用方注入，本模块不绑定任何渠道。
Python 3.9 兼容：不用 match / X|Y 语法。
"""
import random
import re


# ── 皮肤（图的皮）→ 背景图资产映射 ──
PRESET_SKINS = {"wow": None, "d4": "d4_bg.jpg", "poe": "poe_bg.jpg", "poe2": "poe2_bg.jpg"}

# ── 皮 → 调色板（深色系：classic/magazine 直接使用） ──
_PALETTE_DEFAULT = "wow"
_SKIN_PALETTES = {
    "wow": {"bg_top": "#131b30", "bg_mid": "#0a0e17", "bg_bot": "#182138",
            "gold": "#c9a84c", "gold_hi": "#f0d78c", "gold_deep": "#8b734b",
            "panel": "#12182a", "panel_b": "#3a3450", "text": "#e8e0d0", "dim": "#a09888"},
    "d4": {"bg_top": "#1e0f0b", "bg_mid": "#130a08", "bg_bot": "#26140e",
           "gold": "#d4914c", "gold_hi": "#ffc98a", "gold_deep": "#8f5a2c",
           "panel": "#1d120c", "panel_b": "#4a2c1c", "text": "#f0e0d0", "dim": "#a89078"},
    "poe": {"bg_top": "#191309", "bg_mid": "#100c06", "bg_bot": "#201910",
            "gold": "#b08d57", "gold_hi": "#e8cf9a", "gold_deep": "#71583a",
            "panel": "#161009", "panel_b": "#3d3020", "text": "#e8e0cc", "dim": "#9c8f78"},
    "poe2": {"bg_top": "#0d1712", "bg_mid": "#08100c", "bg_bot": "#12211a",
             "gold": "#9fb87a", "gold_hi": "#d8e8b0", "gold_deep": "#5f7a48",
             "panel": "#0e1812", "panel_b": "#2c3f2e", "text": "#e2e8d8", "dim": "#93a088"},
}

# ── 大字报模板的强调色（按皮取，高饱和） ──
_BOLD_ACCENTS = {"wow": "#ffd23f", "d4": "#ff7043", "poe": "#e0b96b", "poe2": "#7ed07e"}

# ── 题材 → 插画素材文件夹（output/toutiao/ 下，用户手工整理） ──
_THEME_ASSET_DIRS = {
    "魔兽世界-无限": "魔兽世界-无限图片素材",
    "魔兽世界-正式服": "魔兽世界-正式服图片素材",
    "魔兽世界-怀旧服": "魔兽世界-怀旧服图片素材",
    "暗黑4": "d4图片素材",
}
_THEME_FALLBACK_SKIN_DIRS = {"d4": "d4图片素材"}


def resolve_skin(skin, assets_dir):
    """解析皮名 → (皮肤key, 背景图URI或None)。返回 None 表示用卡片自带 bg。"""
    skin = (skin or "").strip()
    if not skin or skin in ("wow", "魔兽世界-正式服", "魔兽世界-无限", "魔兽世界-怀旧服"):
        return "wow", None
    asset = PRESET_SKINS.get(skin)
    if asset is None:  # 自定义：安全文件名查资产
        safe = re.sub(r"[^\w-]", "", skin)[:30]
        if safe:
            candidate = assets_dir / ("%s_bg.jpg" % safe)
            if candidate.exists():
                return safe, candidate.as_uri()
        return "neutral-only", (assets_dir / "neutral_bg.jpg").as_uri()
    path = assets_dir / asset
    if path.exists():
        return skin, path.as_uri()
    return "neutral-only", (assets_dir / "neutral_bg.jpg").as_uri()


def _theme_image_paths(theme, skin_key, output_dir):
    """题材优先、皮名兜底，找 output/toutiao/<文件夹> 里的插画素材。"""
    folder = _THEME_ASSET_DIRS.get((theme or "").strip())
    if not folder:
        folder = _THEME_FALLBACK_SKIN_DIRS.get(skin_key)
    if not folder:
        return []
    d = output_dir / folder
    if not d.is_dir():
        return []
    exts = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts)


def _guide_image_paths(theme, skin_key, output_dir):
    """攻略图解模板的素材：优先 output/toutiao/<题材>攻略图/（用户放的地图、截图、资料图），
    没有则回退题材插画素材目录，再由调用方回退皮背景。"""
    t = (theme or "").strip()
    exts = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    if t:
        d = output_dir / (t + "攻略图")
        if d.is_dir():
            files = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts)
            if files:
                return files
    return _theme_image_paths(theme, skin_key, output_dir)


def _img_ratio(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        return h / w if w else 1.0
    except Exception:
        return None


def _pick_cover_image(paths, rng=None):
    """封面主视觉。rng=None 保持旧行为（优先竖构图/比例最大，确定性）；
    rng 给定（random.Random 实例）则随机抽——2026-09-29 用户要求「素材图片也可以是随机的，
    并不是第一张图片就是第一张图片素材」：同一素材库多次生成应换图，不再永远同一张上封面。
    随机时在竖构图池（比例>=1.0）里抽，池空回落全池，避免横图被拉竖裁切。"""
    if not paths:
        return None
    if rng is not None:
        pool = [p for p in paths if (_img_ratio(p) or 1.0) >= 1.0] or list(paths)
        return rng.choice(pool)
    best, best_r = paths[0], -1.0
    for p in paths:
        r = _img_ratio(p)
        if r is not None and r > best_r:
            best, best_r = p, r
    return best


def _pick_band_image(paths, idx, rng=None, exclude=None):
    """内容页横带。idx 从 2 起（1 是封面）。
    rng=None 保持旧行为（优先横构图、按页固定轮换）；rng 给定则随机抽：
    exclude 可为单张图或已用图列表（封面 + 前几页横带），随机时优先在「还没用过的图」里抽，
    素材不够时才允许重复——保证同一包内各页尽量互不重复、且不与封面撞图，跨包顺序随机。"""
    if not paths:
        return None
    if rng is not None:
        used = set()
        if exclude is not None:
            if isinstance(exclude, (list, tuple, set)):
                used = set(exclude)
            else:
                used = {exclude}
        fresh = [p for p in paths if p not in used]
        return rng.choice(fresh or list(paths))
    wide = [(p, _img_ratio(p) if _img_ratio(p) is not None else 1.0) for p in paths]
    wide.sort(key=lambda x: x[1])
    return wide[(idx - 2) % len(wide)][0]


def _hero_uri(card, cover_img, skin_uri, assets_dir):
    """卡片主视觉图：题材插画 > 皮背景图 > wow 阵营图。"""
    if cover_img is not None:
        return cover_img.as_uri()
    if skin_uri:
        return skin_uri
    bg = card.get("bg") if card.get("bg") in ("neutral", "alliance", "horde") else "neutral"
    return (assets_dir / ("%s_bg.jpg" % bg)).as_uri()
