# -*- coding: utf-8 -*-
"""头条图文生成 v3：toutiao_mix 整合稿 → 4 张竖版信息图 (1080x1920, 9:16) + 微头条文案。

版式参照 2026-09-24 参考图（WOW FOREVER 风格）：
金渐变大标题 + 插画主视觉（底缘渐隐）+ 圆环图标三面板 + 双金边 CTA 横幅，无页码/来源行。

架构（保持双选择不变）：
- theme 题材：进 LLM prompt（标题/文案点明游戏）+ 映射 output/toutiao/<题材>图片素材/ 插画
- skin 皮：调色板 + 背景垫图；wow=d4/poe/poe2 之外的通用皮，自定义查 _assets/<名称>_bg.jpg

流程：
1. LLM 把整合稿排成卡片 JSON（封面卡 + 3 张列表卡）+ copy_text；失败回退本地模板解析【固定标签】
2. playwright 渲染 HTML 模板成 PNG（量 .wrap 高度等比缩放进画布，防底部裁切）
3. 落盘 output/toutiao/<summary_id>/img*.png + manifest.json，前端经 /media/toutiao/... 取图

Python 3.9 兼容：不用 match / X|Y 语法。
"""
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output" / "toutiao"
CANVAS_W, CANVAS_H = 1080, 1920

_ASSETS_DIR = OUTPUT_DIR / "_assets"

# ── 皮肤（图的皮）→ 背景图资产映射 ──
# "wow"：按卡片自身 bg（neutral/alliance/horde，适配魔兽两版本）
# 预置皮肤：单图铺全部卡片；自定义：查 _assets/<安全文件名>_bg.jpg，无则回退 neutral
PRESET_SKINS = {"wow": None, "d4": "d4_bg.jpg", "poe": "poe_bg.jpg", "poe2": "poe2_bg.jpg"}

# ── 皮 → 调色板（蓝黑+金参照 2026-09-24 参考图；其余皮只换色不动结构） ──
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

# ── 题材 → 插画素材文件夹（output/toutiao/ 下，用户手工整理） ──
_THEME_ASSET_DIRS = {
    "魔兽世界-无限": "魔兽世界-无限图片素材",
    "魔兽世界-正式服": "魔兽世界-正式服图片素材",
    "魔兽世界-怀旧服": "魔兽世界-怀旧服图片素材",
    "暗黑4": "d4图片素材",
}
_THEME_FALLBACK_SKIN_DIRS = {"d4": "d4图片素材"}

_CSS_TEMPLATE = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:__W__px; font-family:"PingFang SC","Hiragino Sans GB",sans-serif;
       background:__BG_MID__; color:__TEXT__; }
.wrap { width:__W__px; height:__H__px; padding:56px 60px 52px; display:flex; flex-direction:column;
        position:relative; overflow:hidden;
        background:
          radial-gradient(ellipse at 50% -8%, rgba(255,205,110,.10), transparent 55%),
          linear-gradient(172deg, __BG_TOP__ 0%, __BG_MID__ 46%, __BG_BOT__ 100%); }
/* 鎏金双线外框 */
.frame { position:absolute; inset:20px; pointer-events:none;
         border:2px solid __GOLD_DEEP__; border-radius:10px;
         box-shadow: inset 0 0 0 4px __BG_MID__, inset 0 0 0 6px __GOLD_DEEP__, 0 0 30px rgba(0,0,0,.55); }
.corner { position:absolute; width:26px; height:26px; background:__GOLD__;
          transform:rotate(45deg); box-shadow:0 0 12px rgba(201,168,76,.5); z-index:3; }
.corner.tl { top:34px; left:34px; } .corner.tr { top:34px; right:34px; }
.corner.bl { bottom:34px; left:34px; } .corner.br { bottom:34px; right:34px; }
/* 全幅氛围垫底图（低透明度，wow 按卡片阵营切图，其他皮统一 skin 图） */
.wrap::before { content:""; position:absolute; inset:0; background-position:center;
                background-size:cover; opacity:.30; }
.wrap.bg-neutral::before { background-image:url("__BG_NEUTRAL__"); }
.wrap.bg-alliance::before { background-image:url("__BG_ALLIANCE__"); }
.wrap.bg-horde::before { background-image:url("__BG_HORDE__"); }
.wrap.bg-skin::before { background-image:url("__BG_SKIN__"); }
.wrap-in { position:relative; z-index:1; display:flex; flex-direction:column; flex:1; min-height:0; }
/* 顶栏：题材徽记 + 阵营标 */
.topline { display:flex; align-items:center; justify-content:space-between; margin-bottom:30px; }
.badge { background:linear-gradient(180deg,__GOLD_HI__,__GOLD__); color:#241a10; font-weight:800;
         font-size:26px; padding:9px 26px; border-radius:5px; letter-spacing:3px;
         border:1px solid __GOLD_DEEP__; box-shadow:0 2px 8px rgba(0,0,0,.5); }
.faction { display:inline-flex; align-items:center; font-size:24px; font-weight:800;
           border-radius:5px; padding:6px 16px; letter-spacing:2px; }
.faction.horde { background:linear-gradient(180deg,#8f2f26,#6b1f18); color:#ffd9c9;
                 border:1px solid #c25d4f; }
.faction.alliance { background:linear-gradient(180deg,#2f4f8f,#1f3566); color:#cfe0ff;
                    border:1px solid #6f9fdf; }
/* 标题：金色渐变 + 衬线 */
h1 { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:84px; line-height:1.3;
     font-weight:900; letter-spacing:2px; text-align:center; text-wrap:balance; word-break:keep-all;
     background:linear-gradient(180deg,__GOLD_HI__ 0%,__GOLD__ 58%,__GOLD_DEEP__ 100%);
     -webkit-background-clip:text; -webkit-text-fill-color:transparent;
     filter:drop-shadow(0 3px 6px rgba(0,0,0,.7)); }
h1.long { font-size:66px; }
h2 { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:62px; font-weight:900;
     letter-spacing:2px; text-align:center; margin-top:-84px; position:relative; z-index:2;
     text-wrap:balance; word-break:keep-all;
     background:linear-gradient(180deg,__GOLD_HI__ 0%,__GOLD__ 58%,__GOLD_DEEP__ 100%);
     -webkit-background-clip:text; -webkit-text-fill-color:transparent;
     filter:drop-shadow(0 3px 6px rgba(0,0,0,.75)); }
.sub { text-align:center; margin:16px 0 28px; font-size:30px; color:__DIM__; letter-spacing:6px; }
.sub2 { text-align:center; margin:14px 0 24px; font-size:27px; color:__DIM__; letter-spacing:2px; }
/* 插画主视觉区（底缘渐隐融入底色） */
.hero { position:relative; flex:1; min-height:480px; border-radius:12px; overflow:hidden;
        border:1px solid __PANEL_B__; box-shadow:0 12px 32px rgba(0,0,0,.55); }
.hero img { width:100%; height:100%; object-fit:cover; display:block; }
.hero .fade { position:absolute; inset:0;
              background:linear-gradient(180deg, rgba(0,0,0,.20) 0%, transparent 24%,
                        transparent 72%, __BG_MID__ 100%); }
.band { position:relative; flex:1; min-height:380px; max-height:700px; border-radius:12px; overflow:hidden;
        border:1px solid __PANEL_B__; box-shadow:0 12px 32px rgba(0,0,0,.5); }
.band img { width:100%; height:100%; object-fit:cover; display:block; }
.band .fade { position:absolute; inset:0;
              background:linear-gradient(180deg, rgba(0,0,0,.18) 0%, transparent 28%,
                        transparent 72%, __BG_MID__ 100%); }
/* CTA 双金边横幅（封面时间线） */
.cta { margin-top:34px; border:3px double __GOLD_DEEP__; border-radius:12px; padding:26px 36px;
       text-align:center; background:linear-gradient(180deg, rgba(0,0,0,.22), rgba(0,0,0,.42));
       box-shadow:0 6px 18px rgba(0,0,0,.4); }
.cta .label { font-size:24px; letter-spacing:8px; color:__GOLD__; font-weight:700; margin-bottom:12px; }
.cta .big { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:46px; font-weight:900;
            color:__GOLD_HI__; text-shadow:0 2px 10px rgba(0,0,0,.6); }
.cta .note { margin-top:10px; font-size:24px; color:__DIM__; letter-spacing:1px; }
/* 封面钩子卡（3 张一行 / 4 张 2x2） */
.hooks { margin-top:28px; display:flex; flex-wrap:wrap; gap:18px; }
.hook { flex:1 1 30%; background:rgba(10,12,22,.82); border:1px solid __PANEL_B__;
        border-radius:10px; padding:22px 24px; box-shadow:0 5px 14px rgba(0,0,0,.35); }
.hooks.g2 .hook { flex-basis:47%; }
.hook .hd { display:flex; align-items:center; gap:12px; margin-bottom:10px; }
.hook .n { width:34px; height:34px; background:__GOLD__; transform:rotate(45deg); flex-shrink:0;
           box-shadow:0 0 10px rgba(201,168,76,.55); position:relative; }
.hook .n span { position:absolute; inset:0; transform:rotate(-45deg); display:flex; align-items:center;
                justify-content:center; font-size:20px; font-weight:800; color:#241a10; }
.hook .t { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:30px; font-weight:800;
           color:__GOLD_HI__; }
.hook .d { font-size:24px; line-height:1.5; color:__TEXT__; }
/* 分组标签（菱形分隔线） */
.seclabel { display:flex; align-items:center; gap:18px; margin:10px 0 26px; }
.seclabel .ln { flex:1; height:1px;
                background:linear-gradient(90deg, transparent, __GOLD_DEEP__, transparent); }
.seclabel .tx { font-size:27px; letter-spacing:6px; color:__GOLD__; font-weight:800; }
/* 三面板：圆环图标 + 金标题 + 白正文 */
.panels { display:flex; flex-direction:column; gap:18px; flex:1; }
.panel { display:flex; align-items:center; gap:26px; background:rgba(10,12,22,.86);
         border:1px solid __PANEL_B__; border-radius:12px; padding:26px 30px; flex:1;
         box-shadow:0 6px 16px rgba(0,0,0,.38); }
.panel .ring { flex-shrink:0; width:92px; height:92px; border-radius:50%;
               background:radial-gradient(circle at 35% 30%, #232a40, #10131f);
               border:2px solid __GOLD__; box-shadow:0 0 0 5px rgba(0,0,0,.35), inset 0 0 14px rgba(0,0,0,.6);
               display:flex; align-items:center; justify-content:center;
               font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:42px;
               font-weight:900; color:__GOLD_HI__; text-shadow:0 2px 6px rgba(0,0,0,.7); }
.panel .bd { flex:1; min-width:0; }
.panel .pt { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:33px; font-weight:800;
             color:__GOLD_HI__; margin-bottom:8px; display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
.panel .pd { font-size:27px; line-height:1.55; color:__TEXT__; }
.chip { font-size:22px; font-weight:700; color:#241a10; letter-spacing:2px;
        background:linear-gradient(180deg,__GOLD_HI__,__GOLD__); border-radius:5px; padding:3px 12px;
        border:1px solid __GOLD_DEEP__; }
/* 阵营强调（面板级） */
.panel.f-alliance { border-color:#2f4f8f; }
.panel.f-alliance .ring { border-color:#6f9fdf; color:#cfe0ff;
                          background:radial-gradient(circle at 35% 30%, #1c2a4a, #101627); }
.panel.f-horde { border-color:#6b1f18; }
.panel.f-horde .ring { border-color:#c25d4f; color:#ffd9c9;
                       background:radial-gradient(circle at 35% 30%, #3a1712, #1d0d0a); }
/* 底部提醒条（双金边） */
.note { border:3px double __GOLD_DEEP__; border-radius:12px; padding:22px 30px; margin-top:auto;
        background:linear-gradient(180deg, rgba(0,0,0,.22), rgba(0,0,0,.4)); }
.note .nt { font-size:25px; color:__GOLD__; font-weight:800; margin-bottom:8px; letter-spacing:4px; }
.note .nd { font-size:26px; line-height:1.6; color:__TEXT__; }
.note .nd b { color:__GOLD_HI__; }
"""


def resolve_skin(skin):
    """解析皮名 → (皮肤key, 背景图URI或None)。返回 None 表示用卡片自带 bg。"""
    skin = (skin or "").strip()
    if not skin or skin in ("wow", "魔兽世界-正式服", "魔兽世界-无限", "魔兽世界-怀旧服"):
        return "wow", None
    asset = PRESET_SKINS.get(skin)
    if asset is None:  # 自定义：安全文件名查资产
        safe = re.sub(r"[^\w-]", "", skin)[:30]
        if safe:
            candidate = _ASSETS_DIR / ("%s_bg.jpg" % safe)
            if candidate.exists():
                return safe, candidate.as_uri()
        return "neutral-only", (_ASSETS_DIR / "neutral_bg.jpg").as_uri()
    path = _ASSETS_DIR / asset
    if path.exists():
        return skin, path.as_uri()
    return "neutral-only", (_ASSETS_DIR / "neutral_bg.jpg").as_uri()


def _build_css(palette, skin_uri):
    tokens = {"__W__": str(CANVAS_W), "__H__": str(CANVAS_H)}
    for k, v in palette.items():
        tokens["__%s__" % k.upper()] = v
    tokens["__BG_NEUTRAL__"] = (_ASSETS_DIR / "neutral_bg.jpg").as_uri()
    tokens["__BG_ALLIANCE__"] = (_ASSETS_DIR / "alliance_bg.jpg").as_uri()
    tokens["__BG_HORDE__"] = (_ASSETS_DIR / "horde_bg.jpg").as_uri()
    tokens["__BG_SKIN__"] = skin_uri or tokens["__BG_NEUTRAL__"]
    css = _CSS_TEMPLATE
    for k, v in tokens.items():
        css = css.replace(k, v)
    return css


def _theme_image_paths(theme, skin_key):
    """题材优先、皮名兜底，找 output/toutiao/<文件夹> 里的插画素材。"""
    folder = _THEME_ASSET_DIRS.get((theme or "").strip())
    if not folder:
        folder = _THEME_FALLBACK_SKIN_DIRS.get(skin_key)
    if not folder:
        return []
    d = OUTPUT_DIR / folder
    if not d.is_dir():
        return []
    exts = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts)


def _img_ratio(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        return h / w if w else 1.0
    except Exception:
        return None


def _pick_cover_image(paths):
    """封面主视觉：优先竖构图（比例最大）。"""
    if not paths:
        return None
    best, best_r = paths[0], -1.0
    for p in paths:
        r = _img_ratio(p)
        if r is not None and r > best_r:
            best, best_r = p, r
    return best


def _pick_band_image(paths, idx):
    """内容页横带：优先横构图（比例最小），按页轮换。idx 从 2 起（1 是封面）。"""
    if not paths:
        return None
    wide = [(p, _img_ratio(p) if _img_ratio(p) is not None else 1.0) for p in paths]
    wide.sort(key=lambda x: x[1])
    return wide[(idx - 2) % len(wide)][0]


def _hero_uri(card, cover_img, skin_uri):
    """卡片主视觉图：题材插画 > 皮背景图 > wow 阵营图。"""
    if cover_img is not None:
        return cover_img.as_uri()
    if skin_uri:
        return skin_uri
    bg = card.get("bg") if card.get("bg") in ("neutral", "alliance", "horde") else "neutral"
    return (_ASSETS_DIR / ("%s_bg.jpg" % bg)).as_uri()


def _esc(text):
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _page_open(css):
    return ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
            '<style>%s</style></head><body>' % css)


def _corners():
    return ('<div class="corner tl"></div><div class="corner tr"></div>'
            '<div class="corner bl"></div><div class="corner br"></div>')


def _cover_html(card, css, hero_uri, total, footer_note, brand=""):
    hooks = card.get("hooks") or []
    shown = hooks[:4]
    g_cls = "hooks g2" if len(shown) >= 4 else "hooks"
    hook_html = "".join(
        '<div class="hook"><div class="hd"><div class="n"><span>%d</span></div>'
        '<div class="t">%s</div></div><div class="d">%s</div></div>'
        % (i + 1, _esc(h.get("t", "")), _esc(h.get("d", "")))
        for i, h in enumerate(shown)
    )
    faction = card.get("faction") or ""
    faction_html = ('<span class="faction %s">%s</span>' % (faction, _esc(card.get("faction_text", "")))) if faction else ""
    bg_cls = ("bg-%s" % card["bg"]) if card.get("bg") in ("neutral", "alliance", "horde", "skin") else ""
    sub_html = ('<div class="sub">%s</div>' % _esc(card.get("subtitle", ""))) if card.get("subtitle") else ""
    return """%s<div class="wrap %s" style="height:__H__px;position:relative">
  <div class="frame"></div>%s
  <div class="wrap-in">
  <div class="topline"><div class="badge">%s</div>%s</div>
  <h1 class="%s">%s</h1>
  %s
  <div class="hero"><img src="%s"><div class="fade"></div></div>
  <div class="cta"><div class="label">◆ %s ◆</div><div class="big">%s</div><div class="note">%s</div></div>
  <div class="%s">%s</div>
  </div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
        _page_open(css), bg_cls, _corners(),
        _esc(brand or card.get("badge") or "整合速览"), faction_html,
        "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")), sub_html,
        hero_uri,
        _esc(card.get("timeline_label", "关键时间线")),
        _esc(card.get("timeline", "")), _esc(card.get("timeline_note", "")),
        g_cls, hook_html,
    )


def _list_html(card, css, hero_uri, idx, total, footer_note, brand=""):
    items = card.get("items") or []
    rows = []
    for it in items:
        f_cls = ("f-%s" % it["faction"]) if it.get("faction") in ("alliance", "horde") else ""
        name = _esc(it.get("name", ""))
        glyph = _esc((it.get("name") or card.get("icon") or "◆")[:1])
        tag = _esc(it.get("tag", ""))
        rows.append(
            '<div class="panel %s"><div class="ring">%s</div><div class="bd">'
            '<div class="pt">%s%s</div><div class="pd">%s</div></div></div>'
            % (f_cls, glyph, name,
               ('<span class="chip">%s</span>' % tag) if tag else "",
               _esc(it.get("desc", "")))
        )
    note = card.get("note") or {}
    note_html = (
        '<div class="note"><div class="nt">◆ %s ◆</div><div class="nd">%s</div></div>'
        % (_esc(note.get("title", "提醒")), _esc(note.get("text", "")))
    ) if note.get("text") else ""
    faction = card.get("faction") or ""
    faction_html = ('<span class="faction %s">%s</span>' % (faction, _esc(card.get("faction_text", "")))) if faction else ""
    bg_cls = ("bg-%s" % card["bg"]) if card.get("bg") in ("neutral", "alliance", "horde", "skin") else ""
    subtitle = card.get("subtitle") or ""
    sub_html = ('<div class="sub2">%s</div>' % _esc(subtitle)) if subtitle else ""
    return """%s<div class="wrap %s" style="height:__H__px;position:relative">
  <div class="frame"></div>%s
  <div class="wrap-in">
  <div class="topline"><div class="badge">%s</div>%s</div>
  <div class="band"><img src="%s"><div class="fade"></div></div>
  <h2>%s</h2>
  %s
  <div class="seclabel"><div class="ln"></div><div class="tx">%s</div><div class="ln"></div></div>
  <div class="panels">%s</div>
  %s
  </div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
        _page_open(css), bg_cls, _corners(),
        _esc(brand or card.get("badge") or "整合速览"), faction_html,
        hero_uri,
        _esc(card.get("title", "")), sub_html,
        _esc(card.get("section", "要点")), "".join(rows),
        note_html,
    )


# ── LLM 卡片化 ──
_CARDS_PROMPT = """你是微头条竖版信息图排版师。把下面的整合稿排成 4 张竖版卡片（1080x1920 竖屏 9:16，深色+金色主题，插画主视觉+文字面板版式），供直接渲染出图。直接输出 JSON，不要思考过程、不要解释。

__THEME_LINE__
__TITLE_LINE__

要求：
- 第 1 张 kind="cover"：title 大标题（≤18字，含数字）；subtitle 一句话（≤20字，可空）；timeline（≤22字，如「11月4日开服 → 12月7日开团本」）；timeline_note（≤28字）；timeline_label（如「关键时间线」）；hooks 3-4 条，每条 t（≤14字）+ d（≤20字）；bg 可选，"neutral"=通用史诗背景
- 第 2-4 张 kind="list"：title（≤14字）；section（条目分组名，≤6字）；icon 单字（如 盾/火/剑）；items 3-5 条，每条 name（≤8字）+ desc（≤30字）+ tag（≤4字，可空，如"最稳""T0"）+ faction（可选，"alliance"或"horde"，仅当该条内容阵营专属时填）；最后一张可带 note 对象，含 title 和 text 两个键
- 阵营主题背景：整卡内容阵营专属时 card 加 bg 字段（"alliance"=联盟蓝金大教堂 / "horde"=部落暗红峡谷）+ faction 字段 + faction_text（如"联盟专属"）；混合阵营内容不要设 bg，用条目级 faction 区分
- 每张卡内容要留呼吸感：条目宁少勿多，desc 一句话讲完，超长会被截断
- 所有文字精简口语化；只能用整合稿里的事实，严禁编造
- copy_text：微头条文案，结构 = 钩子开头（1-2句）+ 📌 要点 4 条（每条一行，冒号+短解释）+ 收尾引导（1句）+ 空行 + 「信息来源：多位UP主公开视频内容整理（口径截至{{DATE}}，可能有调整）」+「本文图为AI辅助生成，发布时勾选AI辅助声明。」，全文 200-320 字

输出 JSON（cards 数组 4 个对象，第一个 kind 为 cover，其余为 list；copy_text 为字符串）：
{schema_placeholder}

整合稿：
{content}"""


def _build_cards_prompt(summary, theme=""):
    """构造卡片化 prompt：schema 段含双花括号 JSON 示例，不能走 str.format，逐段拼接。
    theme：题材/游戏名（如「暗黑4」），注入后卡片标题与 copy_text 点明题材。"""
    schema = ('{{\n  "cards": [\n    {{"kind": "cover", "title": "...", "subtitle": "...", '
              '"timeline_label": "...", "timeline": "...", "timeline_note": "...", '
              '"hooks": [{{"t": "...", "d": "..."}}]}},\n'
              '    {{"kind": "list", "title": "...", "subtitle": "...", "section": "...", "icon": "...", '
              '"items": [{{"name": "...", "desc": "...", "tag": "..."}}], "note": {{"title": "...", "text": "..."}}}}\n  ],\n'
              '  "copy_text": "..."\n}}')
    date_str = datetime.now().strftime("%m月%d日")
    theme_line = ("本篇题材：%s。封面标题、各卡标题和 copy_text 都要点明该题材（游戏名/模式名），让读者一眼知道这是哪个游戏的内容。" % theme) if theme else "本篇题材：未指定，按整合稿内容自行判断题材并在标题点明。"
    title_line = ""
    if (summary.get("title") or "").strip():
        title_line = ("推荐标题（来自整理稿/用户指定）：%s。第 1 张 cover 卡的 title 优先采用它，"
                      "可微调语气与字数但必须保留核心词，不要另起炉灶。" % summary["title"].strip())
    prompt = (_CARDS_PROMPT
              .replace("{schema_placeholder}", schema)
              .replace("{{DATE}}", date_str)
              .replace("__THEME_LINE__", theme_line)
              .replace("__TITLE_LINE__", title_line))
    # content 放最后替换，避免整合稿里的花括号被后续 replace 误伤
    prompt = prompt.replace("{content}", (summary.get("content") or "")[:12000])
    return prompt


def _llm_cards(summary, ai_config, theme=""):
    """调 LLM 把整合稿转卡片 JSON；失败返回 None。"""
    if not ai_config or not ai_config.get("api_key") or ai_config.get("method") == "skip":
        return None
    try:
        import openai

        client = openai.OpenAI(
            api_key=ai_config["api_key"],
            base_url=ai_config.get("api_base") or "https://api.openai.com/v1",
            timeout=300, max_retries=1,
        )
        content = (summary.get("content") or "")[:12000]
        prompt = _build_cards_prompt(summary, theme)
        resp = client.chat.completions.create(
            model=ai_config.get("model") or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content
        data = _parse_json(text)
        cards = data.get("cards") or []
        if len(cards) >= 2 and data.get("copy_text"):
            return data
        return None
    except Exception:
        return None


def _parse_json(text):
    text = str(text or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        return {}
    depth, in_str, escape = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        return {}
    return {}


def _split_tags(content):
    """按【固定标签】切段：{标签名: 文本}。"""
    tags = {}
    parts = re.split(r"【([^】]{2,8})】", content or "")
    for i in range(1, len(parts) - 1, 2):
        tags[parts[i]] = parts[i + 1].strip()
    return tags


def _fallback_cards(summary):
    """本地模板兜底：解析固定标签拼 4 张卡。AI 未配置/调用失败时保底出图。"""
    content = summary.get("content") or ""
    tags = _split_tags(content)
    title = (summary.get("title") or "整合速览")[:18]
    compare = tags.get("变化对比", "")[:400]
    impact = tags.get("对你的影响", "")[:400]
    action = tags.get("行动建议", "")[:400]
    timeline = tags.get("时间线", "").strip().splitlines()
    tl_text = timeline[0][:22] if timeline else "详见正文"

    def _rows(text, limit=5):
        rows = []
        for line in [l.strip() for l in text.splitlines() if l.strip()][:limit]:
            line = re.sub(r"^[-*|]+", "", line).strip(" |")
            seg = line.split("|") if "|" in line else [line[:8], line]
            name = seg[0][:8] if len(seg) > 1 else "要点"
            desc = (seg[1] if len(seg) > 1 else line)[:34]
            rows.append({"name": name, "desc": desc, "tag": ""})
        return rows or [{"name": "详见", "desc": "整理稿正文", "tag": ""}]

    cards = [
        {"kind": "cover", "bg": "neutral", "badge": "整合", "title": title, "subtitle": "多源信息 · 决策化整合",
         "timeline_label": "关键时间线", "timeline": tl_text, "timeline_note": "本地模板生成，请人工核对",
         "hooks": [{"t": "变化对比", "d": "见第 2 张"}, {"t": "对你的影响", "d": "见第 3 张"},
                   {"t": "行动建议", "d": "见第 4 张"}]},
        {"kind": "list", "title": "核心变化对比", "section": "变化", "icon": "变",
         "items": _rows(compare)},
        {"kind": "list", "title": "对你的影响", "section": "影响", "icon": "响",
         "items": _rows(impact)},
        {"kind": "list", "title": "行动建议", "section": "行动", "icon": "行",
         "items": _rows(action), "note": {"title": "提醒", "text": "本地模板兜底生成，发布前请人工核对数值。"}},
    ]
    copy_text = (
        "%s\n\n多源信息决策化整合，4 张卡片看懂变化、影响与行动建议：\n\n📌 变化对比：见配图 2\n"
        "📌 对你的影响：见配图 3\n📌 行动建议：见配图 4，建议收藏\n\n信息来源：多位UP主公开视频内容整理。\n"
        "本文图为AI辅助生成，发布时勾选AI辅助声明。" % title
    )
    return {"cards": cards, "copy_text": copy_text}


# ── playwright 渲染 ──
def _render_card_html(page, html_path, png_path):
    page.goto(html_path.resolve().as_uri())
    page.wait_for_timeout(250)
    natural_h = page.evaluate("document.querySelector('.wrap').scrollHeight")
    if natural_h > CANVAS_H:
        page.add_style_tag(content=".wrap{transform:scale(%.4f);transform-origin:top left;width:%dpx}"
                           % (CANVAS_H / natural_h, CANVAS_W))
        page.wait_for_timeout(80)
    page.screenshot(path=str(png_path),
                    clip={"x": 0, "y": 0, "width": CANVAS_W, "height": CANVAS_H})


def generate_graphics(summary, ai_config, progress_cb=None, theme="", skin="wow", title=""):
    """主入口：整合稿 → manifest dict（图片落盘 output/toutiao/<summary_id>/）。

    theme: str 题材/游戏名，注入 LLM 卡片化（标题/文案点明题材）+ 选插画素材文件夹
    skin:  str 图的皮——"wow"按卡片自带 bg；预置 d4/poe/poe2 单图铺底；自定义查 _assets；
           同时决定整套调色板（wow=蓝黑金参照风，d4=烬红，poe/poe2=青铜/墨玉）
    title: str 用户在弹框里改过的图文标题；非空时覆盖整理稿标题，注入 LLM 封面标题指令
    """
    summary_id = int(summary["id"])
    if (title or "").strip():
        summary = dict(summary)
        summary["title"] = title.strip()[:60]
    out_dir = OUTPUT_DIR / str(summary_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _pg(msg):
        if progress_cb:
            progress_cb(msg)

    skin_key, skin_uri = resolve_skin(skin)
    palette = _SKIN_PALETTES.get(skin_key) or _SKIN_PALETTES[_PALETTE_DEFAULT]
    css = _build_css(palette, skin_uri)
    theme_imgs = _theme_image_paths(theme, skin_key)
    cover_img = _pick_cover_image(theme_imgs)

    _pg("LLM 卡片化整合稿...")
    data = _llm_cards(summary, ai_config, theme)
    model = "local-template"
    if data:
        model = ai_config.get("model") or "llm"
    else:
        _pg("LLM 不可用，走本地模板兜底...")
        data = _fallback_cards(summary)

    cards = data.get("cards") or []
    # 皮肤覆盖：统一皮 → 所有卡片 bg=skin 类
    if skin_uri:
        for card in cards:
            card["bg"] = "skin"
    total = len(cards)
    images = []
    _pg("渲染 %d 张信息图..." % total)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": CANVAS_W, "height": CANVAS_H})
        footer_note = ""
        brand = (theme or "").strip()[:12]
        for idx, card in enumerate(cards, 1):
            kind = card.get("kind") or "list"
            if kind == "cover" and idx == 1:
                hero = _hero_uri(card, cover_img, skin_uri)
                html = _cover_html(card, css, hero, total, footer_note, brand)
            else:
                band = _pick_band_image(theme_imgs, idx)
                hero = _hero_uri(card, band, skin_uri)
                html = _list_html(card, css, hero, idx, total, footer_note, brand)
            html_path = out_dir / ("img%d.html" % idx)
            png_path = out_dir / ("img%d.png" % idx)
            html_path.write_text(html, encoding="utf-8")
            _render_card_html(page, html_path, png_path)
            images.append({"file": png_path.name, "label": "图%d" % idx, "url": "/media/toutiao/%d/%s" % (summary_id, png_path.name)})
        browser.close()

    manifest = {
        "id": summary_id,
        "summary_id": summary_id,
        "title": summary.get("title") or "",
        "copy_text": data.get("copy_text") or "",
        "images": images,
        "cards": cards,
        "model": model,
        "theme": (theme or "").strip(),
        "skin": skin_key,
        "canvas": "%dx%d" % (CANVAS_W, CANVAS_H),
        "summary_type": summary.get("summary_type", ""),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def list_packages():
    """扫描 output/toutiao/*/manifest.json，倒序返回列表。"""
    items = []
    if not OUTPUT_DIR.exists():
        return items
    for mf in OUTPUT_DIR.glob("*/manifest.json"):
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
            items.append(data)
        except (ValueError, OSError):
            continue
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items


def get_package(summary_id):
    mf = OUTPUT_DIR / str(summary_id) / "manifest.json"
    if not mf.exists():
        return None
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except ValueError:
        return None


def delete_package(summary_id):
    """删 manifest + 图片；目录内残留 html 一并清理，目录本身保留（send_from_directory 安全）。"""
    import shutil

    d = OUTPUT_DIR / str(summary_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False
