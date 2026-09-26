# -*- coding: utf-8 -*-
"""头条图文生成 v4：toutiao_mix 整合稿 → 4 张竖版信息图 (1080x1920, 9:16) + 微头条文案。

三层选择架构（正交组合，任意搭配）：
- template 模板：整体版式/字体/装饰 —— classic 经典卡片(鎏金插画) / magazine 杂志大片(编辑排版)
                  / minimal 极简清单(浅色知识卡) / bold 大字报(高对比冲击)
- skin 皮：调色板 + 背景垫图（深色模板直接用调色板；浅色/大字报取其金色系做强调色）
- theme 题材：进 LLM prompt（标题/文案点明游戏）+ 映射 output/toutiao/<题材>图片素材/ 插画

流程：
1. LLM 把整合稿排成卡片 JSON（封面卡 + 3 张列表卡）+ copy_text；失败回退本地模板解析【固定标签】
2. 按模板选 CSS + 渲染函数，playwright 渲染成 PNG（量 .wrap 高度等比缩放进画布，防底部裁切）
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

# ════════════════════════ 模板一：classic 经典卡片（v3 原版） ════════════════════════
_CSS_CLASSIC = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:__W__px; font-family:"PingFang SC","Hiragino Sans GB",sans-serif;
       background:__BG_MID__; color:__TEXT__; }
.wrap { width:__W__px; height:__H__px; padding:56px 60px 52px; display:flex; flex-direction:column;
        position:relative; overflow:hidden;
        background:
          radial-gradient(ellipse at 50% -8%, rgba(255,205,110,.10), transparent 55%),
          linear-gradient(172deg, __BG_TOP__ 0%, __BG_MID__ 46%, __BG_BOT__ 100%); }
.frame { position:absolute; inset:20px; pointer-events:none;
         border:2px solid __GOLD_DEEP__; border-radius:10px;
         box-shadow: inset 0 0 0 4px __BG_MID__, inset 0 0 0 6px __GOLD_DEEP__, 0 0 30px rgba(0,0,0,.55); }
.corner { position:absolute; width:26px; height:26px; background:__GOLD__;
          transform:rotate(45deg); box-shadow:0 0 12px rgba(201,168,76,.5); z-index:3; }
.corner.tl { top:34px; left:34px; } .corner.tr { top:34px; right:34px; }
.corner.bl { bottom:34px; left:34px; } .corner.br { bottom:34px; right:34px; }
.wrap::before { content:""; position:absolute; inset:0; background-position:center;
                background-size:cover; opacity:.30; }
.wrap.bg-neutral::before { background-image:url("__BG_NEUTRAL__"); }
.wrap.bg-alliance::before { background-image:url("__BG_ALLIANCE__"); }
.wrap.bg-horde::before { background-image:url("__BG_HORDE__"); }
.wrap.bg-skin::before { background-image:url("__BG_SKIN__"); }
.wrap-in { position:relative; z-index:1; display:flex; flex-direction:column; flex:1; min-height:0; }
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
     filter:drop-shadow(0 2px 10px rgba(0,0,0,.75)); }
.sub { text-align:center; margin:16px 0 28px; font-size:30px; color:__DIM__; letter-spacing:6px; }
.sub2 { text-align:center; margin:14px 0 24px; font-size:27px; color:__DIM__; letter-spacing:2px; }
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
.cta { margin-top:34px; border:3px double __GOLD_DEEP__; border-radius:12px; padding:26px 36px;
       text-align:center; background:linear-gradient(180deg, rgba(0,0,0,.22), rgba(0,0,0,.42));
       box-shadow:0 6px 18px rgba(0,0,0,.4); }
.cta .label { font-size:24px; letter-spacing:8px; color:__GOLD__; font-weight:700; margin-bottom:12px; }
.cta .big { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:46px; font-weight:900;
            color:__GOLD_HI__; text-shadow:0 2px 10px rgba(0,0,0,.6); }
.cta .note { margin-top:10px; font-size:24px; color:__DIM__; letter-spacing:1px; }
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
.seclabel { display:flex; align-items:center; gap:18px; margin:10px 0 26px; }
.seclabel .ln { flex:1; height:1px;
                background:linear-gradient(90deg, transparent, __GOLD_DEEP__, transparent); }
.seclabel .tx { font-size:27px; letter-spacing:6px; color:__GOLD__; font-weight:800; }
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
.panel.f-alliance { border-color:#2f4f8f; }
.panel.f-alliance .ring { border-color:#6f9fdf; color:#cfe0ff;
                          background:radial-gradient(circle at 35% 30%, #1c2a4a, #101627); }
.panel.f-horde { border-color:#6b1f18; }
.panel.f-horde .ring { border-color:#c25d4f; color:#ffd9c9;
                       background:radial-gradient(circle at 35% 30%, #3a1712, #1d0d0a); }
.note { border:3px double __GOLD_DEEP__; border-radius:12px; padding:22px 30px; margin-top:auto;
        background:linear-gradient(180deg, rgba(0,0,0,.22), rgba(0,0,0,.4)); }
.note .nt { font-size:25px; color:__GOLD__; font-weight:800; margin-bottom:8px; letter-spacing:4px; }
.note .nd { font-size:26px; line-height:1.6; color:__TEXT__; }
.note .nd b { color:__GOLD_HI__; }
"""

# ════════════════════════ 模板二：magazine 杂志大片（暗色编辑排版） ════════════════════════
_CSS_MAGAZINE = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:__W__px; font-family:"PingFang SC","Hiragino Sans GB",sans-serif;
       background:__BG_MID__; color:__TEXT__; }
.wrap { width:__W__px; height:__H__px; position:relative; overflow:hidden; display:flex;
        flex-direction:column;
        background:linear-gradient(180deg, #17130e 0%, __BG_MID__ 58%, #0b0908 100%); }
.wrap::before { content:""; position:absolute; inset:0; background-image:url("__BG_SKIN__");
                background-position:center; background-size:cover; opacity:.14; }
.wrap-in { position:relative; z-index:1; display:flex; flex-direction:column; flex:1; min-height:0; }
/* 报头 */
.mast { display:flex; align-items:center; justify-content:space-between; padding:46px 64px 0; }
.mast .kick { display:flex; align-items:center; gap:16px; }
.mast .kick .sq { width:14px; height:14px; background:__GOLD__; }
.mast .kick .tx { font-size:24px; letter-spacing:6px; color:__GOLD__; font-weight:700; }
.mast .vol { font-size:24px; letter-spacing:4px; color:__DIM__; font-weight:600; }
.mast-rule { margin:20px 64px 0; height:2px;
             background:linear-gradient(90deg, __GOLD__, rgba(201,168,76,.15)); }
/* 封面大图 */
.photo { position:relative; flex:0 0 46%; margin:28px 64px 0; overflow:hidden; border-radius:4px;
         box-shadow:0 20px 56px rgba(0,0,0,.65); }
.photo img { width:100%; height:100%; object-fit:cover; display:block; }
.photo .shade { position:absolute; inset:0;
                background:linear-gradient(180deg, rgba(0,0,0,.06) 40%, rgba(0,0,0,.62) 100%); }
.photo .cap { position:absolute; left:30px; right:30px; bottom:24px; display:flex;
              justify-content:space-between; align-items:flex-end; }
.photo .cap .lab { font-size:22px; letter-spacing:5px; color:#f3e9d2; opacity:.92; font-weight:700; }
.photo .cap .src { font-size:20px; color:#cdb98a; letter-spacing:2px; }
/* 标题 */
.head { padding:42px 64px 0; }
.head .over { display:flex; align-items:center; gap:14px; font-size:25px; letter-spacing:4px;
              color:__GOLD__; font-weight:700; margin-bottom:20px; }
.head .over .dash { width:44px; height:3px; background:__GOLD__; }
h1 { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:88px; line-height:1.22;
     font-weight:900; color:#f5efe1; letter-spacing:1px; text-wrap:balance; word-break:keep-all;
     text-shadow:0 4px 18px rgba(0,0,0,.6); }
h1.long { font-size:70px; }
.head .sub { margin-top:22px; font-size:28px; color:__DIM__; letter-spacing:3px; line-height:1.5; }
/* 目录索引 */
.index { margin:36px 64px 0; flex:1; display:flex; flex-direction:column; justify-content:flex-end;
         padding-bottom:60px; }
.index .cap-tx { font-size:23px; letter-spacing:8px; color:__GOLD__; font-weight:800; margin-bottom:6px; }
.index .it { display:flex; align-items:baseline; gap:26px; padding:22px 4px;
             border-top:1px solid rgba(201,168,76,.30); }
.index .it:last-child { border-bottom:1px solid rgba(201,168,76,.30); }
.index .no { font-family:"Songti SC","Noto Serif SC",serif; font-size:38px; font-weight:900;
             color:__GOLD__; width:62px; flex-shrink:0; }
.index .t { font-size:32px; font-weight:800; color:#f0e8d6; width:310px; flex-shrink:0; }
.index .d { flex:1; font-size:26px; color:__DIM__; line-height:1.5; }
/* 内页：顶部横带 + 章节头 + 编辑行列 */
.lband { position:relative; flex:0 0 24%; margin:26px 64px 0; overflow:hidden; border-radius:4px;
         box-shadow:0 14px 40px rgba(0,0,0,.55); }
.lband img { width:100%; height:100%; object-fit:cover; display:block; }
.lband .shade { position:absolute; inset:0;
                background:linear-gradient(180deg, rgba(0,0,0,.04) 30%, rgba(0,0,0,.45) 100%); }
.lhead { padding:36px 64px 0; display:flex; align-items:flex-end; justify-content:space-between; }
.lhead .lt .sec { display:flex; align-items:center; gap:14px; font-size:24px; letter-spacing:6px;
                  color:__GOLD__; font-weight:800; margin-bottom:16px; }
.lhead .lt .sec .sq { width:12px; height:12px; background:__GOLD__; }
h2 { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:62px; font-weight:900;
     color:#f5efe1; letter-spacing:1px; text-wrap:balance; word-break:keep-all;
     text-shadow:0 3px 14px rgba(0,0,0,.55); }
h2.long { font-size:52px; }
.lhead .lt .sub2 { margin-top:14px; font-size:27px; color:__DIM__; letter-spacing:2px; }
.lhead .pg { font-family:"Songti SC","Noto Serif SC",serif; font-size:30px; color:__GOLD__;
             letter-spacing:3px; font-weight:900; padding-bottom:8px; }
.edrows { flex:1; margin:26px 64px 0; display:flex; flex-direction:column; }
.edrow { flex:1; display:flex; gap:28px; padding:20px 4px; align-items:center;
         border-bottom:1px solid rgba(201,168,76,.22); }
.edrow .no { font-family:"Songti SC","Noto Serif SC",serif; font-size:52px; font-weight:900;
             color:rgba(201,168,76,.60); width:84px; flex-shrink:0; line-height:1; }
.edrow .bd { flex:1; min-width:0; }
.edrow .pt { font-size:33px; font-weight:800; color:#f0e8d6; margin-bottom:8px;
             display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
.edrow .pd { font-size:26px; line-height:1.5; color:__DIM__; }
.mchip { font-size:22px; font-weight:700; color:__GOLD_HI__; letter-spacing:2px;
         border:1px solid __GOLD_DEEP__; border-radius:3px; padding:3px 12px; }
.note-q { margin:26px 64px 58px; border-left:4px solid __GOLD__; padding:8px 0 8px 28px; }
.note-q .nt { font-size:24px; letter-spacing:5px; color:__GOLD__; font-weight:800; margin-bottom:8px; }
.note-q .nd { font-size:26px; line-height:1.6; color:__TEXT__; }
"""

# ════════════════════════ 模板三：minimal 极简清单（浅色知识卡） ════════════════════════
_CSS_MINIMAL = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:__W__px; font-family:"PingFang SC","Hiragino Sans GB",sans-serif;
       background:__PAPER__; color:__INK__; }
.wrap { width:__W__px; height:__H__px; padding:76px 84px 64px; display:flex; flex-direction:column;
        position:relative; overflow:hidden; background:__PAPER__; }
.wrap::after { content:""; position:absolute; top:0; left:0; right:0; height:14px;
               background:linear-gradient(90deg, __ACCENT__ 0%, __ACCENT__ 58%, __INK__ 58.5%, __INK__ 100%); }
.wrap-in { position:relative; z-index:1; display:flex; flex-direction:column; flex:1; min-height:0; }
.tagrow { display:flex; justify-content:space-between; align-items:center; margin-bottom:48px; }
.pill { border:3px solid __INK__; border-radius:999px; padding:10px 30px; font-size:26px;
        font-weight:800; letter-spacing:4px; color:__INK__; }
.tagrow .idx { font-size:26px; color:__MDIM__; letter-spacing:4px; font-weight:700; }
h1 { font-size:96px; line-height:1.18; font-weight:900; color:__INK__; letter-spacing:0;
     text-wrap:balance; word-break:keep-all; }
h1.long { font-size:76px; }
.hl-strip { width:128px; height:14px; background:__ACCENT__; margin:32px 0 26px; }
.sub { font-size:30px; color:__MDIM__; line-height:1.6; letter-spacing:1px; }
.photo { margin-top:44px; border-radius:20px; overflow:hidden; border:1px solid __LINE__;
         flex:0 0 32%; box-shadow:0 26px 64px rgba(34,29,21,.14); }
.photo img { width:100%; height:100%; object-fit:cover; display:block; }
.checklist { margin-top:46px; flex:1; display:flex; flex-direction:column; gap:28px;
             justify-content:center; }
.ck { display:flex; align-items:flex-start; gap:26px; }
.ck .box { flex-shrink:0; width:46px; height:46px; border:3px solid __INK__; border-radius:12px;
           display:flex; align-items:center; justify-content:center; font-size:30px; font-weight:900;
           color:__ACCENT__; margin-top:2px; }
.ck .bd { flex:1; border-bottom:2px solid __LINE__; padding-bottom:22px; }
.ck .t { font-size:34px; font-weight:800; color:__INK__; margin-bottom:8px; }
.ck .d { font-size:27px; color:__MDIM__; line-height:1.5; }
/* 内页 */
.lhead { margin-bottom:30px; }
.lhead .sec { display:flex; align-items:center; gap:20px; margin-bottom:24px; }
.lhead .sec .n { font-size:28px; font-weight:900; color:__ACCENT__; letter-spacing:4px; }
.lhead .sec .ln { flex:1; height:2px; background:__LINE__; }
h2 { font-size:66px; font-weight:900; color:__INK__; letter-spacing:1px;
     text-wrap:balance; word-break:keep-all; }
h2.long { font-size:54px; }
.lhead .sub2 { margin-top:16px; font-size:28px; color:__MDIM__; }
.rows { flex:1; display:flex; flex-direction:column; }
.row { flex:1; display:flex; gap:28px; padding:26px 0; border-bottom:2px solid __LINE__;
       align-items:flex-start; }
.row:last-child { border-bottom:none; }
.row .no { font-size:42px; font-weight:900; color:__ACCENT__; width:80px; flex-shrink:0; line-height:1.05; }
.row .bd { flex:1; min-width:0; }
.row .pt { font-size:34px; font-weight:800; color:__INK__; margin-bottom:8px;
           display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
.row .pd { font-size:27px; color:__MDIM__; line-height:1.55; }
.ochip { font-size:22px; font-weight:700; color:__ACCENT__; border:2px solid __ACCENT__;
         border-radius:999px; padding:2px 16px; letter-spacing:2px; }
.callout { margin-top:26px; background:#ffffff; border:1px solid __LINE__;
           border-left:10px solid __ACCENT__; border-radius:14px; padding:28px 32px;
           box-shadow:0 12px 34px rgba(34,29,21,.07); }
.callout .nt { font-size:25px; font-weight:800; color:__INK__; letter-spacing:4px; margin-bottom:10px; }
.callout .nd { font-size:26px; color:__MDIM__; line-height:1.6; }
.dots { margin-top:34px; display:flex; justify-content:center; gap:14px; }
.dots i { width:14px; height:14px; border-radius:50%; background:__LINE__; }
.dots i.on { background:__ACCENT__; }
"""

# ════════════════════════ 模板四：bold 大字报（高对比冲击） ════════════════════════
_CSS_BOLD = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:__W__px; font-family:"PingFang SC","Hiragino Sans GB",sans-serif;
       background:#0b0b0d; color:#f2f2ee; }
.wrap { width:__W__px; height:__H__px; position:relative; overflow:hidden; display:flex;
        flex-direction:column;
        background:
          radial-gradient(ellipse at 88% 102%, rgba(255,210,63,.08), transparent 52%),
          linear-gradient(180deg, #0b0b0d 0%, #111116 100%); }
.tape { height:26px; flex-shrink:0;
        background:repeating-linear-gradient(-45deg, __ACCENT__ 0 36px, #0b0b0d 36px 72px); }
.tape.bot { position:absolute; bottom:0; left:0; right:0; }
.wrap-in { position:relative; z-index:1; flex:1; min-height:0; display:flex; flex-direction:column;
           padding:52px 64px 88px; }
.tagrow { display:flex; justify-content:space-between; align-items:center; margin-bottom:42px; }
.sticker { background:__ACCENT__; color:#111111; font-weight:900; font-size:26px; letter-spacing:5px;
           padding:12px 26px; border-radius:6px; transform:rotate(-1.5deg);
           box-shadow:7px 7px 0 rgba(0,0,0,.6); }
.tagrow .no2 { font-size:26px; font-weight:800; color:#8a8a92; letter-spacing:4px; }
h1 { font-size:112px; line-height:1.14; font-weight:900; color:#fafafa; letter-spacing:0;
     text-wrap:balance; word-break:keep-all; text-shadow:7px 7px 0 rgba(0,0,0,.85); }
h1.long { font-size:84px; }
.sub { margin-top:28px; font-size:32px; font-weight:700; color:#cfcfd6; letter-spacing:2px; }
.poster { margin-top:40px; flex:0 0 28%; border:6px solid #f2f2ee; box-shadow:16px 16px 0 __ACCENT__;
          overflow:hidden; }
.poster img { width:100%; height:100%; object-fit:cover; display:block; }
.tickets { margin-top:48px; flex:1; display:flex; flex-direction:column; gap:24px; }
.tk { flex:1; display:flex; align-items:center; gap:26px; border:3px dashed #3c3c46;
      border-radius:16px; padding:22px 30px; background:rgba(255,255,255,.025); }
.tk .big { font-family:"Songti SC","Noto Serif SC",serif; font-size:66px; font-weight:900;
           color:__ACCENT__; width:92px; text-align:center; flex-shrink:0; line-height:1; }
.tk .t { font-size:34px; font-weight:900; color:#fafafa; margin-bottom:8px; }
.tk .d { font-size:26px; color:#a9a9b2; line-height:1.5; }
/* 内页 */
.lhead { margin-bottom:30px; }
.lhead .sec { display:inline-block; background:__ACCENT__; color:#111111; font-size:24px;
              font-weight:900; letter-spacing:8px; padding:10px 22px; margin-bottom:26px;
              box-shadow:5px 5px 0 rgba(0,0,0,.6); }
h2 { font-size:78px; font-weight:900; color:#fafafa; line-height:1.2;
     text-wrap:balance; word-break:keep-all; text-shadow:6px 6px 0 rgba(0,0,0,.85); }
h2.long { font-size:62px; }
.lhead .sub2 { margin-top:18px; font-size:28px; color:#a9a9b2; }
.hits { flex:1; display:flex; flex-direction:column; }
.hit { flex:1; display:flex; align-items:center; gap:32px; border-bottom:3px solid #232329; padding:16px 0; }
.hit:last-child { border-bottom:none; }
.hit .no { font-size:76px; font-weight:900; width:112px; flex-shrink:0; line-height:1;
           color:transparent; -webkit-text-stroke:3px __ACCENT__; }
.hit .bd { flex:1; min-width:0; }
.hit .pt { font-size:36px; font-weight:900; color:#fafafa; margin-bottom:8px;
           display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
.hit .pd { font-size:26px; color:#a9a9b2; line-height:1.55; }
.bchip { display:inline-block; background:__ACCENT__; color:#111111; font-size:22px; font-weight:900;
         padding:5px 15px; border-radius:4px; transform:rotate(-2deg); letter-spacing:2px; }
.warn { margin-top:28px; border:4px solid __ACCENT__; border-radius:18px; padding:28px 32px;
        background:rgba(255,210,63,.05); }
.warn .nt { font-size:26px; font-weight:900; color:__ACCENT__; letter-spacing:5px; margin-bottom:12px; }
.warn .nd { font-size:27px; color:#e8e8e0; line-height:1.6; }
"""



# ════════════════════════ 模板五：guide 攻略图解（羊皮纸+攻略图主视觉+路线轨） ════════════════════════
# 版式参照 2026-09-25 抖音参考四：标题区（金书法体+等级区间副标题）+ 攻略图主视觉（暗角羽化）
# + 发光路线编号节点 + 底部指令面板（推荐路线→ + 效率提示）。攻略图来自 output/toutiao/<题材>攻略图/。
_CSS_GUIDE = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:__W__px; font-family:"PingFang SC","Hiragino Sans GB",sans-serif;
       background:#171006; color:#f0e8d8; }
.wrap { width:__W__px; height:__H__px; position:relative; overflow:hidden; display:flex; flex-direction:column;
        background:
          radial-gradient(ellipse at 50% -6%, rgba(212,165,116,.12), transparent 58%),
          linear-gradient(180deg, #33220f 0%, #241708 42%, #1a1005 100%); }
.frame { position:absolute; inset:16px; pointer-events:none; border:4px solid #4a3018; border-radius:8px;
         box-shadow: inset 0 0 0 3px #171006, inset 0 0 0 4px __GOLD__, 0 0 26px rgba(0,0,0,.6); }
.wrap-in { position:relative; z-index:1; flex:1; min-height:0; display:flex; flex-direction:column;
           padding:50px 56px 46px; }
.mast { display:flex; justify-content:space-between; align-items:center; margin-bottom:24px; }
.mast .brand { font-size:24px; letter-spacing:4px; color:__GOLD__; font-weight:800; }
.mast .pg { font-size:24px; color:#8a7355; letter-spacing:3px; }
h1 { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:86px; font-weight:900; text-align:center;
     letter-spacing:4px; line-height:1.28; text-wrap:balance; word-break:keep-all;
     background:linear-gradient(180deg,#f4d98c 0%,__GOLD__ 55%,#8b6914 100%);
     -webkit-background-clip:text; -webkit-text-fill-color:transparent;
     filter:drop-shadow(0 3px 5px rgba(0,0,0,.65)); }
h1.long { font-size:68px; }
h1.list { font-size:62px; }
h1.list.long { font-size:52px; }
.subbar { display:flex; align-items:center; justify-content:center; gap:20px; margin:16px 0 24px; }
.subbar .ln { width:88px; height:2px; background:linear-gradient(90deg, transparent, __GOLD__); }
.subbar .ln.r { background:linear-gradient(270deg, transparent, __GOLD__); }
.subbar .tx { font-size:30px; color:#e8d5b0; letter-spacing:3px; font-weight:700; }
.gmap { position:relative; border-radius:8px; overflow:hidden; border:2px solid __GOLD_DEEP__;
        box-shadow:0 14px 36px rgba(0,0,0,.55), inset 0 0 0 1px rgba(201,168,76,.35); }
.gmap.cover { flex:1; min-height:420px; }
.gmap.list { flex:0 0 38%; min-height:300px; }
.gmap img { width:100%; height:100%; object-fit:cover; display:block; filter:saturate(.94) sepia(.05); }
.gmap .edge { position:absolute; inset:0;
              background:radial-gradient(ellipse at center, transparent 55%, rgba(23,16,6,.5) 100%); }
.gmap .tag { position:absolute; top:16px; right:16px; background:rgba(30,19,8,.9); border:1px solid __GOLD_DEEP__;
             color:#f4d98c; font-size:22px; font-weight:700; padding:6px 16px; border-radius:4px;
             letter-spacing:2px; }
.rail { flex:1; margin:22px 6px 0; position:relative; display:flex; flex-direction:column;
        justify-content:space-evenly; }
.rail .line { position:absolute; left:28px; top:16px; bottom:16px; width:3px; border-radius:2px;
              background:linear-gradient(180deg, rgba(124,252,0,.85), rgba(124,252,0,.30));
              box-shadow:0 0 12px rgba(124,252,0,.55); }
.stop { display:flex; align-items:center; gap:22px; }
.stop .no { width:56px; height:56px; border-radius:50%; flex-shrink:0; position:relative; z-index:1;
            background:radial-gradient(circle at 35% 30%, #14330a, #0a1f06); border:3px solid #7cfc00;
            box-shadow:0 0 14px rgba(124,252,0,.65); display:flex; align-items:center; justify-content:center;
            font-size:27px; font-weight:900; color:#d8ffb0; }
.stop .bd { flex:1; min-width:0; border-bottom:1px dashed rgba(201,168,76,.30); padding-bottom:10px; }
.stop .t { font-size:31px; font-weight:800; color:#f5ecd8; display:flex; align-items:center;
           gap:14px; flex-wrap:wrap; }
.stop .d { font-size:24px; color:#bfa98a; line-height:1.5; margin-top:4px; }
.gchip { font-size:22px; font-weight:700; color:#241a10; background:linear-gradient(180deg,#f0d78c,__GOLD__);
         border-radius:4px; padding:2px 12px; border:1px solid #8b6914; }
.panelbox { margin-top:22px; background:rgba(20,12,5,.85); border:1px solid __GOLD_DEEP__; border-radius:10px;
            padding:22px 30px; box-shadow:inset 0 0 24px rgba(0,0,0,.45); }
.panelbox .rt { font-size:24px; letter-spacing:6px; color:__GOLD__; font-weight:800; margin-bottom:10px; }
.panelbox .chain { font-size:29px; font-weight:700; color:#f5ecd8; line-height:1.6; }
.panelbox .chain b { color:#7cfc00; font-weight:900; margin:0 4px; }
.panelbox .tip { margin-top:10px; font-size:25px; color:#cbb693; line-height:1.55; }
.panelbox .tip b { color:#f4d98c; }
"""

# ════════════════════════ 模板六：tier 梯度榜（梯队大字+条目卡网格） ════════════════════════
# 版式参照 2026-09-25 抖音参考三：暗蓝夜幕底 + 立体渐变梯队大字（红/金/紫）+ 双列条目卡。
_CSS_TIER = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:__W__px; font-family:"PingFang SC","Hiragino Sans GB",sans-serif;
       background:#0a0e1a; color:#eef1ff; }
.wrap { width:__W__px; height:__H__px; position:relative; overflow:hidden; display:flex; flex-direction:column;
        background:
          radial-gradient(ellipse at 50% -10%, rgba(90,110,190,.20), transparent 55%),
          linear-gradient(180deg, #101830 0%, #0a0e1a 55%, #070a12 100%); }
.wrap::before { content:""; position:absolute; inset:0; background-image:url("__BG_SKIN__");
                background-position:center; background-size:cover; opacity:.08; }
.wrap-in { position:relative; z-index:1; flex:1; min-height:0; display:flex; flex-direction:column;
           padding:50px 56px 44px; }
.mast { display:flex; justify-content:space-between; align-items:center; margin-bottom:28px; }
.mast .brand { font-size:24px; letter-spacing:5px; color:#8fa2d8; font-weight:800; }
.mast .pg { font-size:24px; color:#5a6a99; letter-spacing:3px; }
h1 { font-size:90px; font-weight:900; text-align:center; color:#f5f7ff; letter-spacing:2px; line-height:1.25;
     text-wrap:balance; word-break:keep-all;
     text-shadow:0 4px 0 rgba(0,0,0,.5), 0 0 28px rgba(120,150,255,.35); }
h1.long { font-size:70px; }
.srcline { text-align:center; margin:14px 0 24px; font-size:26px; color:#8fa2d8; letter-spacing:2px; }
.hero { position:relative; flex:0 0 32%; border-radius:10px; overflow:hidden;
        border:1px solid rgba(255,255,255,.14); box-shadow:0 16px 40px rgba(0,0,0,.5); margin-bottom:26px; }
.hero img { width:100%; height:100%; object-fit:cover; display:block; }
.hero .fade { position:absolute; inset:0;
              background:linear-gradient(180deg, transparent 55%, rgba(10,14,26,.85) 100%); }
.tpreview { flex:1; display:flex; flex-direction:column; gap:16px; }
.prow { flex:1; display:flex; align-items:center; gap:22px; background:rgba(15,22,38,.9);
        border:1px solid rgba(255,255,255,.10); border-radius:10px; padding:16px 24px; }
.prow .pb { font-size:52px; font-weight:900; width:84px; text-align:center; flex-shrink:0; line-height:1; }
.tpreview .prow:nth-child(1) .pb { color:#ff6b35; text-shadow:0 0 16px rgba(255,107,53,.4); }
.tpreview .prow:nth-child(2) .pb { color:#f4e4c1; text-shadow:0 0 16px rgba(244,228,193,.3); }
.tpreview .prow:nth-child(3) .pb { color:#8b7dd4; text-shadow:0 0 16px rgba(139,125,212,.4); }
.tpreview .prow:nth-child(4) .pb { color:#6fc3e8; text-shadow:0 0 16px rgba(111,195,232,.4); }
.prow .bd2 { flex:1; min-width:0; }
.prow .pt { font-size:30px; font-weight:800; color:#f5f7ff; margin-bottom:4px; }
.prow .pd { font-size:25px; color:#9aa7cc; line-height:1.45; }
.tier { flex:1; display:flex; flex-direction:column; margin-bottom:20px; min-height:0; }
.thead { display:flex; align-items:center; gap:24px; margin-bottom:16px; }
.tbadge { font-family:"Songti SC","Noto Serif SC",serif; font-size:86px; font-weight:900; line-height:1;
          width:150px; text-align:center; flex-shrink:0; transform:skewX(-6deg); }
.tier.a .tbadge { background:linear-gradient(180deg,#ff8a7a,#c41e3a 58%,#7a0c1e);
                  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
                  filter:drop-shadow(0 0 14px rgba(196,30,58,.45)); }
.tier.b .tbadge { background:linear-gradient(180deg,#ffe9a8,#d4a017 58%,#8b6914);
                  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
                  filter:drop-shadow(0 0 14px rgba(212,160,23,.40)); }
.tier.c .tbadge { background:linear-gradient(180deg,#b8a9e8,#4a3f8c 58%,#2d1b69);
                  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
                  filter:drop-shadow(0 0 14px rgba(74,63,140,.45)); }
.tmeta { flex:1; min-width:0; border-bottom:1px solid rgba(255,255,255,.10); padding-bottom:10px; }
.tmeta .tt { font-size:34px; font-weight:900; color:#f5f7ff; }
.tmeta .td { font-size:25px; color:#9aa7cc; margin-top:6px; line-height:1.45; }
.tgrid { flex:1; display:grid; grid-template-columns:1fr 1fr; gap:16px; grid-auto-rows:1fr; }
.tcard { background:rgba(15,22,38,.94); border:1px solid rgba(255,255,255,.10); border-radius:10px;
         padding:20px 22px; box-shadow:0 6px 16px rgba(0,0,0,.4); display:flex; flex-direction:column;
         justify-content:center; }
.tier.a .tcard { border-left:5px solid #c41e3a; }
.tier.b .tcard { border-left:5px solid #d4a017; }
.tier.c .tcard { border-left:5px solid #4a3f8c; }
.tcard .ct { font-size:30px; font-weight:800; color:#f5f7ff; display:flex; align-items:center;
             gap:12px; flex-wrap:wrap; margin-bottom:6px; }
.tcard .cd { font-size:24px; color:#9aa7cc; line-height:1.5; }
.tchip { font-size:21px; font-weight:800; color:#0a0e1a; border-radius:4px; padding:2px 12px; }
.tier.a .tchip { background:#ff6b35; }
.tier.b .tchip { background:#f4e4c1; }
.tier.c .tchip { background:#8b7dd4; color:#fff; }
.footnote { margin-top:18px; text-align:center; font-size:22px; color:#5a6a99; letter-spacing:1px; }
"""


# ════════════════════════ 模板七：quest 任务面板（参考1：魔兽任务UI风） ════════════════════════
# 版式参照 2026-09-25 抖音参考一（睡袋任务系列）：金属框架+四角宝石+金色浮雕大标题
# + 横幅副标题+盾形门槛标签+单字图标徽章摘要行+火焰发光步骤轨+底部操作链面板。
_CSS_QUEST = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:__W__px; font-family:"PingFang SC","Hiragino Sans GB",sans-serif;
       background:#160e06; color:#f0e8d8; }
.wrap { width:__W__px; height:__H__px; position:relative; overflow:hidden; display:flex; flex-direction:column;
        background:
          radial-gradient(ellipse at 50% -6%, rgba(255,170,80,.10), transparent 55%),
          linear-gradient(180deg, #2a1a0a 0%, #1a1008 45%, #0f0904 100%); }
.frame { position:absolute; inset:18px; pointer-events:none; border:5px solid #4a3018; border-radius:6px;
         box-shadow: inset 0 0 0 3px #160e06, inset 0 0 0 5px __GOLD__, 0 0 30px rgba(0,0,0,.6); }
.gem { position:absolute; width:24px; height:24px; transform:rotate(45deg); z-index:3;
       background:radial-gradient(circle at 35% 30%, #ff9a5a, #b03018);
       box-shadow:0 0 14px rgba(255,120,60,.6); border:2px solid #ffd9a0; }
.gem.tl { top:30px; left:30px; } .gem.tr { top:30px; right:30px; }
.gem.bl { bottom:30px; left:30px; } .gem.br { bottom:30px; right:30px; }
.wrap-in { position:relative; z-index:1; flex:1; min-height:0; display:flex; flex-direction:column;
           padding:52px 58px 46px; }
.mast { display:flex; justify-content:space-between; align-items:center; margin-bottom:22px; }
.mast .brand { font-size:24px; letter-spacing:4px; color:__GOLD__; font-weight:800; }
.mast .pg { font-size:24px; color:#8a7355; letter-spacing:3px; }
h1 { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:84px; font-weight:900; text-align:center;
     letter-spacing:3px; line-height:1.25; text-wrap:balance; word-break:keep-all;
     background:linear-gradient(180deg,#ffe9b0 0%,__GOLD__ 50%,#93671a 100%);
     -webkit-background-clip:text; -webkit-text-fill-color:transparent;
     filter:drop-shadow(0 3px 4px rgba(0,0,0,.7)); }
h1.long { font-size:64px; }
h1.list { font-size:58px; }
h1.list.long { font-size:48px; }
.banner { margin:16px auto 0; display:flex; align-items:center; gap:16px; }
.banner .ln { width:70px; height:2px; background:linear-gradient(90deg, transparent, __GOLD__); }
.banner .ln.r { background:linear-gradient(270deg, transparent, __GOLD__); }
.banner .bx { background:linear-gradient(180deg,#2b1608,#1a0d05); border:2px solid __GOLD_DEEP__;
              padding:8px 28px; color:#e8d5b0; font-size:28px; font-weight:700; letter-spacing:2px;
              box-shadow:inset 0 0 12px rgba(0,0,0,.5); }
.shield { margin:20px auto 0; width:fit-content; background:linear-gradient(180deg,#a02818,#701810);
          border:2px solid __GOLD__; color:#ffd9a0; font-size:26px; font-weight:900; letter-spacing:3px;
          padding:10px 36px 24px; border-radius:14px 14px 20px 20px;
          clip-path:polygon(0 0,100% 0,100% 74%,50% 100%,0 74%);
          box-shadow:inset 0 0 10px rgba(0,0,0,.4); }
.hero { position:relative; margin-top:24px; border-radius:8px; overflow:hidden;
        border:3px solid #4a3018; box-shadow:0 12px 32px rgba(0,0,0,.55), inset 0 0 0 2px __GOLD__; }
.hero.cover { flex:1; min-height:380px; }
.hero.list { flex:0 0 28%; min-height:240px; }
.hero img { width:100%; height:100%; object-fit:cover; display:block; }
.hero .fade { position:absolute; inset:0;
              background:radial-gradient(ellipse at center, transparent 58%, rgba(22,14,6,.45) 100%); }
.ifact { display:flex; gap:18px; margin-top:24px; }
.ifc { flex:1; background:rgba(30,19,8,.88); border:1px solid __GOLD_DEEP__; border-radius:8px;
       padding:16px 12px; text-align:center; box-shadow:inset 0 0 14px rgba(0,0,0,.4); }
.ifc .ico { width:54px; height:54px; margin:0 auto 10px; border-radius:50%;
            background:radial-gradient(circle at 35% 30%, #3a220c, #221206); border:2px solid __GOLD__;
            box-shadow:0 0 10px rgba(201,168,76,.35); display:flex; align-items:center; justify-content:center;
            font-size:27px; font-weight:900; color:#f4d98c; }
.ifc .t { font-size:25px; font-weight:800; color:#f4d98c; margin-bottom:4px; }
.ifc .d { font-size:21px; color:#bfa98a; line-height:1.4; }
.tnote { margin-top:14px; text-align:center; font-size:22px; color:#8a7355; }
.steps { flex:1; margin:20px 4px 0; position:relative; display:flex; flex-direction:column;
         justify-content:space-evenly; }
.steps .line { position:absolute; left:26px; top:14px; bottom:14px; width:3px; border-radius:2px;
               background:linear-gradient(180deg, rgba(255,140,50,.9), rgba(255,80,30,.35));
               box-shadow:0 0 12px rgba(255,120,50,.6); }
.step { display:flex; align-items:center; gap:20px; }
.step .no { width:52px; height:52px; border-radius:50%; flex-shrink:0; position:relative; z-index:1;
            background:radial-gradient(circle at 35% 30%, #4a2408, #2a1204); border:3px solid #ff8c32;
            box-shadow:0 0 14px rgba(255,140,50,.65); display:flex; align-items:center; justify-content:center;
            font-size:25px; font-weight:900; color:#ffd9a0; line-height:1; }
.step .bd { flex:1; min-width:0; border-bottom:1px dashed rgba(201,168,76,.30); padding-bottom:10px; }
.step .t { font-size:30px; font-weight:800; color:#f5ecd8; display:flex; align-items:center;
           gap:14px; flex-wrap:wrap; }
.step .d { font-size:23px; color:#bfa98a; line-height:1.45; margin-top:4px; padding-right:10px; }
.qchip { font-size:21px; font-weight:700; color:#241a10; background:linear-gradient(180deg,#ffb066,#e07020);
         border-radius:4px; padding:2px 12px; border:1px solid #93481a; }
.flowbox { margin-top:20px; background:rgba(20,12,5,.88); border:1px solid __GOLD_DEEP__; border-radius:10px;
           padding:20px 28px; box-shadow:inset 0 0 22px rgba(0,0,0,.45); }
.flowbox .rt { font-size:23px; letter-spacing:5px; color:__GOLD__; font-weight:800; margin-bottom:10px; }
.flowbox .chain { font-size:28px; font-weight:700; color:#f5ecd8; line-height:1.6; }
.flowbox .chain b { color:#ff9a4a; font-weight:900; margin:0 4px; }
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


def _tpl_tokens(tpl_key, skin_key):
    """模板级补充色板：minimal 的浅色纸面/墨色，bold 的高饱和强调色。"""
    if tpl_key == "minimal":
        palette = _SKIN_PALETTES.get(skin_key) or _SKIN_PALETTES[_PALETTE_DEFAULT]
        return {"__PAPER__": "#faf7f0", "__INK__": "#221d15", "__MDIM__": "#6b6252",
                "__LINE__": "#e3d9c4", "__ACCENT__": palette["gold_deep"]}
    if tpl_key == "bold":
        return {"__ACCENT__": _BOLD_ACCENTS.get(skin_key, "#ffd23f")}
    return {}


def _build_css(css_template, palette, skin_uri, extra_tokens=None):
    tokens = {"__W__": str(CANVAS_W), "__H__": str(CANVAS_H)}
    for k, v in palette.items():
        tokens["__%s__" % k.upper()] = v
    tokens["__BG_NEUTRAL__"] = (_ASSETS_DIR / "neutral_bg.jpg").as_uri()
    tokens["__BG_ALLIANCE__"] = (_ASSETS_DIR / "alliance_bg.jpg").as_uri()
    tokens["__BG_HORDE__"] = (_ASSETS_DIR / "horde_bg.jpg").as_uri()
    tokens["__BG_SKIN__"] = skin_uri or tokens["__BG_NEUTRAL__"]
    if extra_tokens:
        tokens.update(extra_tokens)
    css = css_template
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



def _guide_image_paths(theme, skin_key):
    """攻略图解模板的素材：优先 output/toutiao/<题材>攻略图/（用户放的地图、截图、资料图），
    没有则回退题材插画素材目录，再由调用方回退皮背景。"""
    t = (theme or "").strip()
    exts = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    if t:
        d = OUTPUT_DIR / (t + "攻略图")
        if d.is_dir():
            files = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts)
            if files:
                return files
    return _theme_image_paths(theme, skin_key)


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


def _pgdate():
    return datetime.now().strftime("%Y.%m.%d")


# ── classic 渲染 ──
def _cover_html_classic(card, css, hero_uri, ctx):
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
        _esc(ctx["brand"] or card.get("badge") or "整合速览"), faction_html,
        "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")), sub_html,
        hero_uri,
        _esc(card.get("timeline_label", "关键时间线")),
        _esc(card.get("timeline", "")), _esc(card.get("timeline_note", "")),
        g_cls, hook_html,
    )


def _list_html_classic(card, css, hero_uri, ctx):
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
    sub_html = ('<div class="sub2">%s</div>' % _esc(card.get("subtitle", ""))) if card.get("subtitle") else ""
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
        _esc(ctx["brand"] or card.get("badge") or "整合速览"), faction_html,
        hero_uri,
        _esc(card.get("title", "")), sub_html,
        _esc(card.get("section", "要点")), "".join(rows),
        note_html,
    )


# ── magazine 渲染 ──
def _cover_html_magazine(card, css, hero_uri, ctx):
    hooks = (card.get("hooks") or [])[:4]
    rows = "".join(
        '<div class="it"><div class="no">%02d</div><div class="t">%s</div><div class="d">%s</div></div>'
        % (i + 1, _esc(h.get("t", "")), _esc(h.get("d", "")))
        for i, h in enumerate(hooks)
    )
    tl = card.get("timeline") or ""
    over = _esc("%s · %s" % (card.get("timeline_label") or "关键时间线", tl) if tl
                else (card.get("timeline_label") or "本期看点"))
    sub = card.get("subtitle") or card.get("timeline_note") or ""
    sub_html = ('<div class="sub">%s</div>' % _esc(sub)) if sub else ""
    return """%s<div class="wrap" style="height:__H__px">
  <div class="wrap-in">
  <div class="mast"><div class="kick"><div class="sq"></div><div class="tx">%s</div></div>
    <div class="vol">%s</div></div>
  <div class="mast-rule"></div>
  <div class="photo"><img src="%s"><div class="shade"></div>
    <div class="cap"><div class="lab">COVER STORY</div><div class="src">%s</div></div></div>
  <div class="head"><div class="over"><div class="dash"></div>%s</div>
    <h1 class="%s">%s</h1>%s</div>
  <div class="index"><div class="cap-tx">IN THIS ISSUE</div>%s</div>
  </div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
        _page_open(css),
        _esc(ctx["brand"] or "整合速览"), _pgdate(),
        hero_uri, _pgdate(),
        over,
        "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")), sub_html,
        rows,
    )


def _list_html_magazine(card, css, hero_uri, ctx):
    items = card.get("items") or []
    rows = "".join(
        '<div class="edrow"><div class="no">%02d</div><div class="bd">'
        '<div class="pt">%s%s</div><div class="pd">%s</div></div></div>'
        % (i + 1, _esc(it.get("name", "")),
           ('<span class="mchip">%s</span>' % _esc(it.get("tag", ""))) if it.get("tag") else "",
           _esc(it.get("desc", "")))
        for i, it in enumerate(items)
    )
    note = card.get("note") or {}
    note_html = (
        '<div class="note-q"><div class="nt">%s</div><div class="nd">%s</div></div>'
        % (_esc(note.get("title", "编辑提醒")), _esc(note.get("text", "")))
    ) if note.get("text") else ""
    sub_html = ('<div class="sub2">%s</div>' % _esc(card.get("subtitle", ""))) if card.get("subtitle") else ""
    return """%s<div class="wrap" style="height:__H__px">
  <div class="wrap-in">
  <div class="mast"><div class="kick"><div class="sq"></div><div class="tx">%s</div></div>
    <div class="vol">%s</div></div>
  <div class="mast-rule"></div>
  <div class="lband"><img src="%s"><div class="shade"></div></div>
  <div class="lhead"><div class="lt"><div class="sec"><div class="sq"></div>%s</div>
    <h2 class="%s">%s</h2>%s</div><div class="pg">%02d / %02d</div></div>
  <div class="edrows">%s</div>
  %s
  </div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
        _page_open(css),
        _esc(ctx["brand"] or "整合速览"), _pgdate(),
        hero_uri,
        _esc(card.get("section", "要点")),
        "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")), sub_html,
        ctx["idx"], ctx["total"],
        rows, note_html,
    )


# ── minimal 渲染 ──
def _cover_html_minimal(card, css, hero_uri, ctx):
    hooks = (card.get("hooks") or [])[:4]
    rows = "".join(
        '<div class="ck"><div class="box">✓</div><div class="bd">'
        '<div class="t">%s</div><div class="d">%s</div></div></div>'
        % (_esc(h.get("t", "")), _esc(h.get("d", "")))
        for h in hooks
    )
    sub = card.get("subtitle") or card.get("timeline_note") or ""
    sub_html = ('<div class="sub">%s</div>' % _esc(sub)) if sub else ""
    return """%s<div class="wrap" style="height:__H__px">
  <div class="wrap-in">
  <div class="tagrow"><div class="pill">%s</div><div class="idx">01 / %02d</div></div>
  <h1 class="%s">%s</h1>
  <div class="hl-strip"></div>
  %s
  <div class="photo"><img src="%s"></div>
  <div class="checklist">%s</div>
  </div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
        _page_open(css),
        _esc(ctx["brand"] or "整合速览"), ctx["total"],
        "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")),
        sub_html, hero_uri, rows,
    )


def _list_html_minimal(card, css, hero_uri, ctx):
    items = card.get("items") or []
    rows = "".join(
        '<div class="row"><div class="no">%02d</div><div class="bd">'
        '<div class="pt">%s%s</div><div class="pd">%s</div></div></div>'
        % (i + 1, _esc(it.get("name", "")),
           ('<span class="ochip">%s</span>' % _esc(it.get("tag", ""))) if it.get("tag") else "",
           _esc(it.get("desc", "")))
        for i, it in enumerate(items)
    )
    note = card.get("note") or {}
    note_html = (
        '<div class="callout"><div class="nt">%s</div><div class="nd">%s</div></div>'
        % (_esc(note.get("title", "提醒")), _esc(note.get("text", "")))
    ) if note.get("text") else ""
    sub_html = ('<div class="sub2">%s</div>' % _esc(card.get("subtitle", ""))) if card.get("subtitle") else ""
    dots = "".join('<i class="%s"></i>' % ("on" if (i + 1) == ctx["idx"] else "")
                   for i in range(ctx["total"]))
    return """%s<div class="wrap" style="height:__H__px">
  <div class="wrap-in">
  <div class="tagrow"><div class="pill">%s</div><div class="idx">%02d / %02d</div></div>
  <div class="lhead"><div class="sec"><div class="n">%s</div><div class="ln"></div></div>
    <h2 class="%s">%s</h2>%s</div>
  <div class="rows">%s</div>
  %s
  <div class="dots">%s</div>
  </div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
        _page_open(css),
        _esc(ctx["brand"] or "整合速览"), ctx["idx"], ctx["total"],
        _esc(card.get("section", "要点")),
        "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")), sub_html,
        rows, note_html, dots,
    )


# ── bold 渲染 ──
def _cover_html_bold(card, css, hero_uri, ctx):
    hooks = (card.get("hooks") or [])[:4]
    rows = "".join(
        '<div class="tk"><div class="big">%d</div><div class="bd">'
        '<div class="t">%s</div><div class="d">%s</div></div></div>'
        % (i + 1, _esc(h.get("t", "")), _esc(h.get("d", "")))
        for i, h in enumerate(hooks)
    )
    sub = card.get("subtitle") or card.get("timeline_note") or ""
    sub_html = ('<div class="sub">%s</div>' % _esc(sub)) if sub else ""
    return """%s<div class="wrap" style="height:__H__px">
  <div class="tape"></div>
  <div class="wrap-in">
  <div class="tagrow"><div class="sticker">%s</div><div class="no2">%s</div></div>
  <h1 class="%s">%s</h1>
  %s
  <div class="poster"><img src="%s"></div>
  <div class="tickets">%s</div>
  </div>
  <div class="tape bot"></div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
        _page_open(css),
        _esc(ctx["brand"] or "整合速览"), _pgdate()[2:],
        "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")),
        sub_html, hero_uri, rows,
    )


def _list_html_bold(card, css, hero_uri, ctx):
    items = card.get("items") or []
    rows = "".join(
        '<div class="hit"><div class="no">%02d</div><div class="bd">'
        '<div class="pt">%s%s</div><div class="pd">%s</div></div></div>'
        % (i + 1, _esc(it.get("name", "")),
           ('<span class="bchip">%s</span>' % _esc(it.get("tag", ""))) if it.get("tag") else "",
           _esc(it.get("desc", "")))
        for i, it in enumerate(items)
    )
    note = card.get("note") or {}
    note_html = (
        '<div class="warn"><div class="nt">⚠ %s</div><div class="nd">%s</div></div>'
        % (_esc(note.get("title", "划重点")), _esc(note.get("text", "")))
    ) if note.get("text") else ""
    sub_html = ('<div class="sub2">%s</div>' % _esc(card.get("subtitle", ""))) if card.get("subtitle") else ""
    return """%s<div class="wrap" style="height:__H__px">
  <div class="tape"></div>
  <div class="wrap-in">
  <div class="tagrow"><div class="sticker">%s</div><div class="no2">%02d / %02d</div></div>
  <div class="lhead"><div class="sec">%s</div>
    <h2 class="%s">%s</h2>%s</div>
  <div class="hits">%s</div>
  %s
  </div>
  <div class="tape bot"></div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
        _page_open(css),
        _esc(ctx["brand"] or "整合速览"), ctx["idx"], ctx["total"],
        _esc(card.get("section", "要点")),
        "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")), sub_html,
        rows, note_html,
    )



# ── guide 渲染（攻略图解：攻略图主视觉 + 路线轨 + 指令面板） ──
def _cover_html_guide(card, css, hero_uri, ctx):
    hooks = (card.get("hooks") or [])[:4]
    chain = " <b>→</b> ".join(_esc(h.get("t", "")) for h in hooks) if hooks else "详见路线图"
    tl = card.get("timeline") or ""
    subbar = ('<div class="subbar"><div class="ln"></div><div class="tx">%s</div><div class="ln r"></div></div>'
              % _esc(tl)) if tl else ""
    tip = card.get("timeline_note") or ""
    tip_html = ('<div class="tip"><b>效率提示：</b>%s</div>' % _esc(tip)) if tip else ""
    return """%s<div class="wrap" style="height:__H__px">
  <div class="frame"></div>
  <div class="wrap-in">
  <div class="mast"><div class="brand">%s</div><div class="pg">%s</div></div>
  <h1 class="%s">%s</h1>
  %s
  <div class="gmap cover"><img src="%s"><div class="edge"></div><div class="tag">%s</div></div>
  <div class="panelbox"><div class="rt">◆ 推荐路线 ◆</div>
    <div class="chain">%s</div>%s</div>
  </div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
    _page_open(css), _esc(ctx["brand"] or "攻略图解"), _pgdate(),
    "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")),
    subbar, hero_uri, _esc(card.get("section") or "攻略图"), chain, tip_html)


def _list_html_guide(card, css, hero_uri, ctx):
    items = card.get("items") or []
    stops = "".join(
        '<div class="stop"><div class="no">%d</div><div class="bd">'
        '<div class="t">%s%s</div><div class="d">%s</div></div></div>'
        % (i + 1, _esc(it.get("name", "")),
           ('<span class="gchip">%s</span>' % _esc(it.get("tag", ""))) if it.get("tag") else "",
           _esc(it.get("desc", "")))
        for i, it in enumerate(items))
    sub = card.get("subtitle") or ""
    subbar = ('<div class="subbar"><div class="ln"></div><div class="tx">%s</div><div class="ln r"></div></div>'
              % _esc(sub)) if sub else ""
    note = card.get("note") or {}
    note_html = ('<div class="panelbox"><div class="rt">◆ %s ◆</div><div class="tip">%s</div></div>'
                 % (_esc(note.get("title", "效率提示")), _esc(note.get("text", "")))) if note.get("text") else ""
    return """%s<div class="wrap" style="height:__H__px">
  <div class="frame"></div>
  <div class="wrap-in">
  <div class="mast"><div class="brand">%s</div><div class="pg">%02d / %02d</div></div>
  <h1 class="list%s">%s</h1>
  %s
  <div class="gmap list"><img src="%s"><div class="edge"></div><div class="tag">%s</div></div>
  <div class="rail"><div class="line"></div>%s</div>
  %s
  </div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
    _page_open(css), _esc(ctx["brand"] or "攻略图解"), ctx["idx"], ctx["total"],
    " long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")),
    subbar, hero_uri, _esc(card.get("section") or "路线"), stops, note_html)


# ── tier 渲染（梯度榜：梯队大字 + 条目卡网格） ──
def _tier_badge(card, ctx):
    m = re.search(r"[A-Za-z]{1,2}\d", str(card.get("section") or ""))
    if m:
        return m.group(0).upper()
    return "%02d" % max(ctx["idx"] - 1, 1)


def _cover_html_tier(card, css, hero_uri, ctx):
    hooks = (card.get("hooks") or [])[:4]
    marks = "①②③④⑤"
    rows = "".join(
        '<div class="prow"><div class="pb">%s</div><div class="bd2">'
        '<div class="pt">%s</div><div class="pd">%s</div></div></div>'
        % (marks[i] if i < len(marks) else str(i + 1), _esc(h.get("t", "")), _esc(h.get("d", "")))
        for i, h in enumerate(hooks))
    src = card.get("subtitle") or ""
    src_html = ('<div class="srcline">%s</div>' % _esc(src)) if src else ""
    foot = card.get("timeline_note") or "梯队仅供参考 · 详见内页"
    return """%s<div class="wrap" style="height:__H__px">
  <div class="wrap-in">
  <div class="mast"><div class="brand">%s</div><div class="pg">%s</div></div>
  <h1 class="%s">%s</h1>
  %s
  <div class="hero"><img src="%s"><div class="fade"></div></div>
  <div class="tpreview">%s</div>
  <div class="footnote">%s</div>
  </div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
    _page_open(css), _esc(ctx["brand"] or "梯度榜"), _pgdate(),
    "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")),
    src_html, hero_uri, rows, _esc(foot))


def _list_html_tier(card, css, hero_uri, ctx):
    items = card.get("items") or []
    cards_html = "".join(
        '<div class="tcard"><div class="ct">%s%s</div><div class="cd">%s</div></div>'
        % (_esc(it.get("name", "")),
           ('<span class="tchip">%s</span>' % _esc(it.get("tag", ""))) if it.get("tag") else "",
           _esc(it.get("desc", "")))
        for it in items)
    note = card.get("note") or {}
    foot = ('<div class="footnote">%s</div>' % _esc(note.get("text", ""))) if note.get("text") else ""
    sub = card.get("subtitle") or ""
    sub_html = ('<div class="td">%s</div>' % _esc(sub)) if sub else ""
    cls = "abc"[(ctx["idx"] - 2) % 3] if ctx["idx"] >= 2 else "a"
    return """%s<div class="wrap" style="height:__H__px">
  <div class="wrap-in">
  <div class="mast"><div class="brand">%s</div><div class="pg">%02d / %02d</div></div>
  <div class="tier %s">
    <div class="thead"><div class="tbadge">%s</div>
      <div class="tmeta"><div class="tt">%s</div>%s</div></div>
    <div class="tgrid">%s</div>
  </div>
  %s
  </div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
    _page_open(css), _esc(ctx["brand"] or "梯度榜"), ctx["idx"], ctx["total"],
    cls, _tier_badge(card, ctx), _esc(card.get("section") or "梯队"), sub_html,
    cards_html, foot)



# ── quest 渲染（任务面板：盾形门槛标签+图标徽章摘要行+火焰步骤轨+操作链） ──
def _chain_html(text):
    parts = [p.strip() for p in str(text or "").split("\u2192") if p.strip()]
    if len(parts) < 2:
        return _esc(text)
    return " <b>\u2192</b> ".join(_esc(p) for p in parts)


def _cover_html_quest(card, css, hero_uri, ctx):
    hooks = (card.get("hooks") or [])[:4]
    facts = "".join(
        '<div class="ifc"><div class="ico">%s</div><div class="t">%s</div><div class="d">%s</div></div>'
        % (_esc((str(h.get("t") or "?"))[:1]), _esc(h.get("t", "")), _esc(h.get("d", "")))
        for h in hooks) if hooks else ""
    sub = card.get("subtitle") or card.get("timeline") or ""
    banner = ('<div class="banner"><div class="ln"></div><div class="bx">%s</div><div class="ln r"></div></div>'
              % _esc(sub)) if sub else ""
    shield = ('<div class="shield">%s</div>' % _esc(card.get("section") or "")) if card.get("section") else ""
    tn = card.get("timeline_note") or ""
    tn_html = ('<div class="tnote">%s</div>' % _esc(tn)) if tn else ""
    return """%s<div class="wrap" style="height:__H__px">
  <div class="frame"></div>
  <div class="gem tl"></div><div class="gem tr"></div><div class="gem bl"></div><div class="gem br"></div>
  <div class="wrap-in">
  <div class="mast"><div class="brand">%s</div><div class="pg">%s</div></div>
  <h1 class="%s">%s</h1>
  %s
  %s
  <div class="hero cover"><img src="%s"><div class="fade"></div></div>
  %s
  %s
  </div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
    _page_open(css), _esc(ctx["brand"] or "任务攻略"), _pgdate(),
    "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")),
    banner, shield, hero_uri, facts, tn_html)


def _list_html_quest(card, css, hero_uri, ctx):
    items = card.get("items") or []
    steps = "".join(
        '<div class="step"><div class="no">%d</div><div class="bd">'
        '<div class="t">%s%s</div><div class="d">%s</div></div></div>'
        % (i + 1, _esc(it.get("name", "")),
           ('<span class="qchip">%s</span>' % _esc(it.get("tag", ""))) if it.get("tag") else "",
           _esc(it.get("desc", "")))
        for i, it in enumerate(items))
    sub = card.get("subtitle") or card.get("section") or ""
    banner = ('<div class="banner"><div class="ln"></div><div class="bx">%s</div><div class="ln r"></div></div>'
              % _esc(sub)) if sub else ""
    note = card.get("note") or {}
    note_html = ('<div class="flowbox"><div class="rt">\u25c6 %s \u25c6</div><div class="chain">%s</div></div>'
                 % (_esc(note.get("title", "操作链")), _chain_html(note.get("text", "")))) if note.get("text") else ""
    return """%s<div class="wrap" style="height:__H__px">
  <div class="frame"></div>
  <div class="gem tl"></div><div class="gem tr"></div><div class="gem bl"></div><div class="gem br"></div>
  <div class="wrap-in">
  <div class="mast"><div class="brand">%s</div><div class="pg">%02d / %02d</div></div>
  <h1 class="list%s">%s</h1>
  %s
  <div class="hero list"><img src="%s"><div class="fade"></div></div>
  <div class="steps"><div class="line"></div>%s</div>
  %s
  </div>
</div></body></html>""".replace("__H__", str(CANVAS_H)) % (
    _page_open(css), _esc(ctx["brand"] or "任务攻略"), ctx["idx"], ctx["total"],
    " long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")),
    banner, hero_uri, steps, note_html)


# ── 模板注册表 ──
_TEMPLATES = {
    "classic": {"label": "经典卡片", "css": _CSS_CLASSIC,
                "cover": _cover_html_classic, "list": _list_html_classic,
                "hint": "视觉模板：经典卡片——深色鎏金、衬线金渐变标题、圆环图标面板，钩子短句。"},
    "magazine": {"label": "杂志大片", "css": _CSS_MAGAZINE,
                 "cover": _cover_html_magazine, "list": _list_html_magazine,
                 "hint": "视觉模板：杂志大片——编辑排版、大图+目录索引，标题要有海报感，hooks 的 t 用名词短语、d 一句讲完。"},
    "minimal": {"label": "极简清单", "css": _CSS_MINIMAL,
                "cover": _cover_html_minimal, "list": _list_html_minimal,
                "hint": "视觉模板：极简清单——浅色纸面、粗黑标题、勾选清单，条目干净利落，desc 控制在 18 字内短句。"},
    "bold": {"label": "大字报", "css": _CSS_BOLD,
             "cover": _cover_html_bold, "list": _list_html_bold,
             "hint": "视觉模板：大字报——高对比、超大字号、冲击力拉满，标题多用数字对比（如「122 对 300」）。"},
    "guide": {"label": "攻略图解", "css": _CSS_GUIDE,
              "cover": _cover_html_guide, "list": _list_html_guide,
              "hint": "视觉模板：攻略图解——图为主角：系统会自动把 output/toutiao/<题材>攻略图/ 文件夹里的"
                      "攻略图/截图/地图嵌为每张卡的主视觉。subtitle 写等级区间或区域（如「30-35级 · 暮色森林」），"
                      "items 为路线/步骤节点（name=地点或步骤名，desc=一句话说明），最后一张 note 写效率提示"
                      "（如「只清门口连片任务，跳过深处精英」）。hooks 的 t 为路线站点名。"
                      "文案围绕图讲「去哪、做什么、注意什么」。"},
    "tier": {"label": "梯度榜", "css": _CSS_TIER,
             "cover": _cover_html_tier, "list": _list_html_tier,
             "hint": "视觉模板：梯度榜——推荐/对比/排行类内容用梯队呈现：每张卡一个梯队，"
                     "section=梯队名（如「T0 强势」「第一梯队」），subtitle=该梯队特点一句话，"
                     "items 为梯队条目（name=条目名，tag=评级标签如「T0」「必练」，desc=一句话点评）。"
                     "hooks 汇总各梯队结论（t=梯队名，d=核心结论）。"},
    "quest": {"label": "任务面板", "css": _CSS_QUEST,
              "cover": _cover_html_quest, "list": _list_html_quest,
              "hint": "视觉模板：任务面板——魔兽任务UI风（金属框架+盾形门槛标签+火焰步骤轨），"
                      "适合步骤/流程/任务/条件类内容：封面 section 写门槛或等级标签（如「14级可接」「新手必看」），"
                      "hooks 为前置条件摘要（t=条件名，首字自动做成图标徽章，d=一句话说明）；"
                      "列表卡 items 为步骤（name=步骤或地点名，tag=坐标或类型标签，desc=一句话动作说明），"
                      "note 写操作链（用 → 连接，如「跳马车 → 点信箱 → 拿睡袋」）。"},
}

# ── LLM 卡片化 ──
_CARDS_PROMPT = """你是微头条竖版信息图排版师。把下面的整合稿排成 4 张竖版卡片（1080x1920 竖屏 9:16），供直接渲染出图。直接输出 JSON，不要思考过程、不要解释。

__THEME_LINE__
__TITLE_LINE__
__TEMPLATE_LINE__

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


def _build_cards_prompt(summary, theme="", template="classic"):
    """构造卡片化 prompt：schema 段含双花括号 JSON 示例，不能走 str.format，逐段拼接。"""
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
    tpl = _TEMPLATES.get(template) or _TEMPLATES["classic"]
    prompt = (_CARDS_PROMPT
              .replace("{schema_placeholder}", schema)
              .replace("{{DATE}}", date_str)
              .replace("__THEME_LINE__", theme_line)
              .replace("__TITLE_LINE__", title_line)
              .replace("__TEMPLATE_LINE__", tpl["hint"]))
    # content 放最后替换，避免整合稿里的花括号被后续 replace 误伤
    prompt = prompt.replace("{content}", (summary.get("content") or "")[:12000])
    return prompt


def _llm_cards(summary, ai_config, theme="", template="classic"):
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
        prompt = _build_cards_prompt(summary, theme, template)
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


def generate_graphics(summary, ai_config, progress_cb=None, theme="", skin="wow", title="", template="classic", guide_images=None):
    """主入口：整合稿 → manifest dict（图片落盘 output/toutiao/<summary_id>/）。

    theme:     str 题材/游戏名，注入 LLM 卡片化 + 选插画素材文件夹
    skin:      str 皮——调色板+背景垫图（wow/d4/poe/poe2/自定义 _assets/<名称>_bg.jpg）
    title:     str 图文标题；非空时覆盖整理稿标题，注入 LLM 封面标题指令
    template:  str 模板——classic 经典卡片 / magazine 杂志大片 / minimal 极简清单 / bold 大字报，
               决定整体版式/字体/装饰（背景、布局、文字大小样式）
    guide_images: list 攻略图解模板的上传攻略图路径（弹窗上传，优先于素材文件夹）
    """
    tpl_key = template if template in _TEMPLATES else "classic"
    tpl = _TEMPLATES[tpl_key]
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
    css = _build_css(tpl["css"], palette, skin_uri, _tpl_tokens(tpl_key, skin_key))
    if tpl_key == "guide":
        if guide_images:
            theme_imgs = [Path(p) for p in guide_images]
        else:
            theme_imgs = _guide_image_paths(theme, skin_key)
        cover_img = theme_imgs[0] if theme_imgs else None
    else:
        theme_imgs = _theme_image_paths(theme, skin_key)
        cover_img = _pick_cover_image(theme_imgs)

    _pg("LLM 卡片化整合稿（模板：%s）..." % tpl["label"])
    data = _llm_cards(summary, ai_config, theme, tpl_key)
    model = "local-template"
    if data:
        model = ai_config.get("model") or "llm"
    else:
        _pg("LLM 不可用，走本地模板兜底...")
        data = _fallback_cards(summary)

    cards = data.get("cards") or []
    if skin_uri:
        for card in cards:
            card["bg"] = "skin"
    total = len(cards)
    images = []
    _pg("渲染 %d 张信息图（模板：%s）..." % (total, tpl["label"]))
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": CANVAS_W, "height": CANVAS_H})
        brand = (theme or "").strip()[:12]
        for idx, card in enumerate(cards, 1):
            kind = card.get("kind") or "list"
            ctx = {"brand": brand, "idx": idx, "total": total, "skin_key": skin_key}
            if kind == "cover" and idx == 1:
                hero = _hero_uri(card, cover_img, skin_uri)
                html = tpl["cover"](card, css, hero, ctx)
            else:
                if tpl_key == "guide":
                    band = theme_imgs[(idx - 1) % len(theme_imgs)] if theme_imgs else None
                else:
                    band = _pick_band_image(theme_imgs, idx)
                hero = _hero_uri(card, band, skin_uri)
                html = tpl["list"](card, css, hero, ctx)
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
        "template": tpl_key,
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
