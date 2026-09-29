# -*- coding: utf-8 -*-
"""7 套模板 CSS + 14 个渲染函数 + 模板注册表（Phase 1 逐字搬移自 toutiao_graphics.py）。

唯一改动：渲染函数内画布高度由 ctx["canvas_h"] 注入（原 str(CANVAS_H)，恰好 14 处），
其余逐字不变，保证同一输入产出 HTML 字节一致。Python 3.9 兼容。
"""
import re
from datetime import datetime


# ════════════════════════ 模板一：classic 经典卡片（v3 原版） ════════════════════════
_CSS_CLASSIC = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:__W__px; font-family:"PingFang SC","Hiragino Sans GB",sans-serif;
       background:__BG_MID__; color:__TEXT__; }
.wrap { width:__W__px; height:__H__px; padding:56px 60px 52px; display:flex; flex-direction:column;
        position:relative; overflow:hidden;
        background:
          radial-gradient(ellipse at 50% -8%, __GLOW__, transparent 55%),
          linear-gradient(172deg, __BG_TOP__ 0%, __BG_MID__ 46%, __BG_BOT__ 100%); }
.frame { position:absolute; inset:20px; pointer-events:none;
         border:2px solid __GOLD_DEEP__; border-radius:10px;
         box-shadow: inset 0 0 0 4px __BG_MID__, inset 0 0 0 6px __GOLD_DEEP__, 0 0 30px rgba(0,0,0,.55); }
.corner { position:absolute; width:26px; height:26px; background:__GOLD__;
          transform:rotate(45deg); box-shadow:0 0 12px rgba(__GOLD_RGB__,.5); z-index:3; }
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
.badge { background:linear-gradient(180deg,__GOLD_HI__,__GOLD__); color:__ON_ACCENT__; font-weight:800;
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
.hook { flex:1 1 30%; background:rgba(__PANEL_RGB__,.82); border:1px solid __PANEL_B__;
        border-radius:10px; padding:22px 24px; box-shadow:0 5px 14px rgba(0,0,0,.35); }
.hooks.g2 .hook { flex-basis:47%; }
.hook .hd { display:flex; align-items:center; gap:12px; margin-bottom:10px; }
.hook .n { width:34px; height:34px; background:__GOLD__; transform:rotate(45deg); flex-shrink:0;
           box-shadow:0 0 10px rgba(__GOLD_RGB__,.55); position:relative; }
.hook .n span { position:absolute; inset:0; transform:rotate(-45deg); display:flex; align-items:center;
                justify-content:center; font-size:20px; font-weight:800; color:__ON_ACCENT__; }
.hook .t { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:30px; font-weight:800;
           color:__GOLD_HI__; }
.hook .d { font-size:24px; line-height:1.5; color:__TEXT__; }
.seclabel { display:flex; align-items:center; gap:18px; margin:10px 0 26px; }
.seclabel .ln { flex:1; height:1px;
                background:linear-gradient(90deg, transparent, __GOLD_DEEP__, transparent); }
.seclabel .tx { font-size:27px; letter-spacing:6px; color:__GOLD__; font-weight:800; }
.panels { display:flex; flex-direction:column; gap:18px; flex:1; }
.panel { display:flex; align-items:center; gap:26px; background:rgba(__PANEL_RGB__,.86);
         border:1px solid __PANEL_B__; border-radius:12px; padding:26px 30px; flex:1;
         box-shadow:0 6px 16px rgba(0,0,0,.38); }
.panel .ring { flex-shrink:0; width:92px; height:92px; border-radius:50%;
               background:radial-gradient(circle at 35% 30%, __RING_HI__, __RING_LO__);
               border:2px solid __GOLD__; box-shadow:0 0 0 5px rgba(0,0,0,.35), inset 0 0 14px rgba(0,0,0,.6);
               display:flex; align-items:center; justify-content:center;
               font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:42px;
               font-weight:900; color:__GOLD_HI__; text-shadow:0 2px 6px rgba(0,0,0,.7); }
.panel .bd { flex:1; min-width:0; }
.panel .pt { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:33px; font-weight:800;
             color:__GOLD_HI__; margin-bottom:8px; display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
.panel .pd { font-size:27px; line-height:1.55; color:__TEXT__; }
.chip { font-size:22px; font-weight:700; color:__ON_ACCENT__; letter-spacing:2px;
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

# ════════════════════════ 模板三·五：wechat 公众号信号格（结构化教程流 · 零图片依赖） ════════════════════════
# 设计期吸收自 pageweave「Signal Grid」家族排版语法（规则头/信号线/2×2 瓦片/阅读流带/总结条），
# 仅吸收设计不搬文件：自包含 CSS + 系统字体（PingFang SC），微信绿 #07c160 色板由 css_engine 注入。
# 纯 CSS 装饰，不依赖任何背景 jpg——公众号渠道去游戏资产的专用模板。
_CSS_WECHAT = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:__W__px; font-family:"PingFang SC","Hiragino Sans GB",sans-serif;
       background:__PAPER__; color:__INK__; }
.wrap { width:__W__px; height:__H__px; padding:80px 78px 56px; display:flex; flex-direction:column;
        position:relative; overflow:hidden; background:__PAPER__; }
.wrap-in { position:relative; z-index:1; display:flex; flex-direction:column; flex:1; min-height:0; }
/* 规则头：细线 + 小字距双栏眉题 */
.rhead { display:flex; justify-content:space-between; align-items:baseline;
         font-size:22px; letter-spacing:.18em; color:__MDIM__; font-weight:600;
         padding-bottom:20px; border-bottom:1px solid __LINE__; }
.rhead b { color:__INK__; font-weight:700; }
.sig { width:64px; height:10px; background:__ACCENT__; margin-top:30px; }
h1 { margin-top:28px; font-size:88px; line-height:1.18; font-weight:800; color:__INK__;
     letter-spacing:.01em; text-wrap:balance; word-break:keep-all; }
h1.long { font-size:68px; }
.sub { margin-top:22px; font-size:30px; color:__MDIM__; line-height:1.6; letter-spacing:.02em; }
/* 2×2 要点瓦片：末格信号绿反白 */
.tiles { margin-top:40px; flex:1; min-height:0; display:grid; grid-template-columns:1fr 1fr;
         grid-auto-rows:1fr; gap:20px; }
.tile { background:__CARD__; padding:28px 30px 30px; border-radius:4px; }
.tile .lb { font-size:20px; letter-spacing:.16em; color:__MDIM__; font-weight:700; }
.tile .t { margin:12px 0 8px; font-size:33px; font-weight:800; color:__INK__; }
.tile .d { font-size:24px; line-height:1.55; color:__MDIM__; }
.tile.sigcell { background:__ACCENT__; }
.tile.sigcell .lb { color:rgba(255,255,255,.82); }
.tile.sigcell .t { color:#ffffff; }
.tile.sigcell .d { color:rgba(255,255,255,.92); }
/* 阅读流带：三步骤 + 绿色行动格 */
.flow { margin-top:36px; padding-top:26px; border-top:1px solid __LINE__; }
.flow .fh { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:18px; }
.flow .fh .ft { font-size:30px; font-weight:800; color:__INK__; }
.flow .fh .fr { font-size:19px; letter-spacing:.14em; color:__ACCENT_DK__; font-weight:700; }
.flow .track { display:grid; grid-template-columns:1fr 1fr 1fr 200px;
               border:1px solid __LINE__; background:__CARD__; }
.flow .step { padding:20px 22px; border-right:1px solid __LINE__; }
.flow .step small { display:block; font-size:18px; letter-spacing:.1em; color:__MDIM__; font-weight:700; }
.flow .step b { display:block; margin-top:14px; font-size:23px; line-height:1.4;
                font-weight:600; color:__INK__; }
.flow .cta { background:__ACCENT__; color:#ffffff; display:flex; align-items:center;
             justify-content:center; font-size:24px; font-weight:800; letter-spacing:.06em; }
/* 页脚：品牌 / 进度点 / 页码 */
.ftr { margin-top:30px; display:flex; justify-content:space-between; align-items:center;
       font-size:20px; letter-spacing:.14em; color:__MDIM__; font-weight:600; }
.dots { display:flex; gap:10px; align-items:center; }
.dots i { width:12px; height:12px; border-radius:50%; background:__LINE__; display:block; }
.dots i.on { background:__ACCENT__; }
/* 内页 */
.kick { margin-top:34px; font-size:21px; letter-spacing:.16em; color:__ACCENT_DK__; font-weight:800; }
h2 { margin-top:14px; font-size:66px; line-height:1.16; font-weight:800; color:__INK__;
     text-wrap:balance; word-break:keep-all; }
h2.long { font-size:52px; }
.sub2 { margin-top:14px; font-size:28px; color:__MDIM__; }
.rows { margin-top:34px; flex:1; min-height:0; display:flex; flex-direction:column;
        gap:18px; justify-content:center; }
.row { background:__CARD__; border-radius:4px; padding:26px 30px; }
.row .lb { font-size:20px; letter-spacing:.14em; color:__ACCENT_DK__; font-weight:800; }
.row .t { margin:10px 0 6px; font-size:33px; font-weight:800; color:__INK__; }
.row .d { font-size:25px; line-height:1.55; color:__MDIM__; }
.note { margin-top:24px; display:flex; gap:22px; align-items:stretch; }
.note .bd { flex:1; border-left:6px solid __ACCENT__; padding:6px 0 6px 26px; }
.note .nt { font-size:21px; letter-spacing:.14em; color:__ACCENT_DK__; font-weight:800; margin-bottom:10px; }
.note .nd { font-size:26px; line-height:1.6; color:__INK__; }
.note .cta { flex-shrink:0; width:190px; background:__ACCENT__; color:#ffffff; border-radius:4px;
             display:flex; align-items:center; justify-content:center; font-size:24px; font-weight:800; }
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
       background:__G_BASE__; color:__G_TEXT__; }
.wrap { width:__W__px; height:__H__px; position:relative; overflow:hidden; display:flex; flex-direction:column;
        background:
          radial-gradient(ellipse at 50% -6%, __G_GLOW__, transparent 58%),
          linear-gradient(180deg, __G_BG_HI__ 0%, __G_BG_MID__ 42%, __G_BG_LO__ 100%); }
.frame { position:absolute; inset:16px; pointer-events:none; border:4px solid __G_FRAME__; border-radius:8px;
         box-shadow: inset 0 0 0 3px __G_BASE__, inset 0 0 0 4px __GOLD__, 0 0 26px rgba(0,0,0,.6); }
.wrap-in { position:relative; z-index:1; flex:1; min-height:0; display:flex; flex-direction:column;
           padding:50px 56px 46px; }
.mast { display:flex; justify-content:space-between; align-items:center; margin-bottom:24px; }
.mast .brand { font-size:24px; letter-spacing:4px; color:__GOLD__; font-weight:800; }
.mast .pg { font-size:24px; color:__G_DIM__; letter-spacing:3px; }
h1 { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:86px; font-weight:900; text-align:center;
     letter-spacing:4px; line-height:1.28; text-wrap:balance; word-break:keep-all;
     background:linear-gradient(180deg,__G_GOLDTXT__ 0%,__GOLD__ 55%,__G_GOLD_LO__ 100%);
     -webkit-background-clip:text; -webkit-text-fill-color:transparent;
     filter:drop-shadow(0 3px 5px rgba(0,0,0,.65)); }
h1.long { font-size:68px; }
h1.list { font-size:62px; }
h1.list.long { font-size:52px; }
.subbar { display:flex; align-items:center; justify-content:center; gap:20px; margin:16px 0 24px; }
.subbar .ln { width:88px; height:2px; background:linear-gradient(90deg, transparent, __GOLD__); }
.subbar .ln.r { background:linear-gradient(270deg, transparent, __GOLD__); }
.subbar .tx { font-size:30px; color:__G_SUBTXT__; letter-spacing:3px; font-weight:700; }
.gmap { position:relative; border-radius:8px; overflow:hidden; border:2px solid __GOLD_DEEP__;
        box-shadow:0 14px 36px rgba(0,0,0,.55), inset 0 0 0 1px rgba(__G_GOLD_RGB__,.35); }
.gmap.cover { flex:1; min-height:420px; }
.gmap.list { flex:0 0 38%; min-height:300px; }
.gmap img { width:100%; height:100%; object-fit:cover; display:block; filter:saturate(.94) sepia(.05); }
.gmap .edge { position:absolute; inset:0;
              background:radial-gradient(ellipse at center, transparent 55%, __G_FADE__ 100%); }
.gmap .tag { position:absolute; top:16px; right:16px; background:__G_TAG__; border:1px solid __GOLD_DEEP__;
             color:__G_GOLDTXT__; font-size:22px; font-weight:700; padding:6px 16px; border-radius:4px;
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
.stop .bd { flex:1; min-width:0; border-bottom:1px dashed rgba(__G_GOLD_RGB__,.30); padding-bottom:10px; }
.stop .t { font-size:31px; font-weight:800; color:__G_STRONG__; display:flex; align-items:center;
           gap:14px; flex-wrap:wrap; }
.stop .d { font-size:24px; color:__G_MUTE__; line-height:1.5; margin-top:4px; }
.gchip { font-size:22px; font-weight:700; color:__G_ON_ACCENT__; background:linear-gradient(180deg,__G_CHIP_HI__,__GOLD__);
         border-radius:4px; padding:2px 12px; border:1px solid __G_GOLD_LO__; }
.panelbox { margin-top:22px; background:__G_PANEL__; border:1px solid __GOLD_DEEP__; border-radius:10px;
            padding:22px 30px; box-shadow:inset 0 0 24px rgba(0,0,0,.45); }
.panelbox .rt { font-size:24px; letter-spacing:6px; color:__GOLD__; font-weight:800; margin-bottom:10px; }
.panelbox .chain { font-size:29px; font-weight:700; color:__G_STRONG__; line-height:1.6; }
.panelbox .chain b { color:#7cfc00; font-weight:900; margin:0 4px; }
.panelbox .tip { margin-top:10px; font-size:25px; color:__G_TIP__; line-height:1.55; }
.panelbox .tip b { color:__G_GOLDTXT__; }
"""

# ════════════════════════ 模板六：tier 梯度榜（梯队大字+条目卡网格） ════════════════════════
# 版式参照 2026-09-25 抖音参考三：暗蓝夜幕底 + 立体渐变梯队大字（红/金/紫）+ 双列条目卡。
_CSS_TIER = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:__W__px; font-family:"PingFang SC","Hiragino Sans GB",sans-serif;
       background:__T_BASE__; color:__T_BODYTXT__; }
.wrap { width:__W__px; height:__H__px; position:relative; overflow:hidden; display:flex; flex-direction:column;
        background:
          radial-gradient(ellipse at 50% -10%, __T_GLOW__, transparent 55%),
          linear-gradient(180deg, __T_BG_HI__ 0%, __T_BASE__ 55%, __T_BG_LO__ 100%); }
.wrap::before { content:""; position:absolute; inset:0; background-image:url("__BG_SKIN__");
                background-position:center; background-size:cover; opacity:.08; }
.wrap-in { position:relative; z-index:1; flex:1; min-height:0; display:flex; flex-direction:column;
           padding:50px 56px 44px; }
.mast { display:flex; justify-content:space-between; align-items:center; margin-bottom:28px; }
.mast .brand { font-size:24px; letter-spacing:5px; color:__T_ACCENT__; font-weight:800; }
.mast .pg { font-size:24px; color:__T_DIM__; letter-spacing:3px; }
h1 { font-size:90px; font-weight:900; text-align:center; color:__T_TEXT__; letter-spacing:2px; line-height:1.25;
     text-wrap:balance; word-break:keep-all;
     text-shadow:0 4px 0 rgba(0,0,0,.5), 0 0 28px __T_HGLOW__; }
h1.long { font-size:70px; }
.srcline { text-align:center; margin:14px 0 24px; font-size:26px; color:__T_ACCENT__; letter-spacing:2px; }
.hero { position:relative; flex:0 0 32%; border-radius:10px; overflow:hidden;
        border:1px solid rgba(255,255,255,.14); box-shadow:0 16px 40px rgba(0,0,0,.5); margin-bottom:26px; }
.hero img { width:100%; height:100%; object-fit:cover; display:block; }
.hero .fade { position:absolute; inset:0;
              background:linear-gradient(180deg, transparent 55%, __T_FADE__ 100%); }
.tpreview { flex:1; display:flex; flex-direction:column; gap:16px; }
.prow { flex:1; display:flex; align-items:center; gap:22px; background:rgba(__T_PANEL_RGB__,.9);
        border:1px solid rgba(255,255,255,.10); border-radius:10px; padding:16px 24px; }
.prow .pb { font-size:52px; font-weight:900; width:84px; text-align:center; flex-shrink:0; line-height:1; }
.tpreview .prow:nth-child(1) .pb { color:#ff6b35; text-shadow:0 0 16px rgba(255,107,53,.4); }
.tpreview .prow:nth-child(2) .pb { color:#f4e4c1; text-shadow:0 0 16px rgba(244,228,193,.3); }
.tpreview .prow:nth-child(3) .pb { color:#8b7dd4; text-shadow:0 0 16px rgba(139,125,212,.4); }
.tpreview .prow:nth-child(4) .pb { color:#6fc3e8; text-shadow:0 0 16px rgba(111,195,232,.4); }
.prow .bd2 { flex:1; min-width:0; }
.prow .pt { font-size:30px; font-weight:800; color:__T_TEXT__; margin-bottom:4px; }
.prow .pd { font-size:25px; color:__T_MUTE__; line-height:1.45; }
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
.tmeta .tt { font-size:34px; font-weight:900; color:__T_TEXT__; }
.tmeta .td { font-size:25px; color:__T_MUTE__; margin-top:6px; line-height:1.45; }
.tgrid { flex:1; display:grid; grid-template-columns:1fr 1fr; gap:16px; grid-auto-rows:1fr; }
.tcard { background:rgba(__T_PANEL_RGB__,.94); border:1px solid rgba(255,255,255,.10); border-radius:10px;
         padding:20px 22px; box-shadow:0 6px 16px rgba(0,0,0,.4); display:flex; flex-direction:column;
         justify-content:center; }
.tier.a .tcard { border-left:5px solid #c41e3a; }
.tier.b .tcard { border-left:5px solid #d4a017; }
.tier.c .tcard { border-left:5px solid #4a3f8c; }
.tcard .ct { font-size:30px; font-weight:800; color:__T_TEXT__; display:flex; align-items:center;
             gap:12px; flex-wrap:wrap; margin-bottom:6px; }
.tcard .cd { font-size:24px; color:__T_MUTE__; line-height:1.5; }
.tchip { font-size:21px; font-weight:800; color:__T_BASE__; border-radius:4px; padding:2px 12px; }
.tier.a .tchip { background:#ff6b35; }
.tier.b .tchip { background:#f4e4c1; }
.tier.c .tchip { background:#8b7dd4; color:#fff; }
.footnote { margin-top:18px; text-align:center; font-size:22px; color:__T_DIM__; letter-spacing:1px; }
"""


# ════════════════════════ 模板七：quest 任务面板（参考1：魔兽任务UI风） ════════════════════════
# 版式参照 2026-09-25 抖音参考一（睡袋任务系列）：金属框架+四角宝石+金色浮雕大标题
# + 横幅副标题+盾形门槛标签+单字图标徽章摘要行+火焰发光步骤轨+底部操作链面板。
_CSS_QUEST = """
* { margin:0; padding:0; box-sizing:border-box; }
body { width:__W__px; font-family:"PingFang SC","Hiragino Sans GB",sans-serif;
       background:__Q_BASE__; color:__Q_TEXT__; }
.wrap { width:__W__px; height:__H__px; position:relative; overflow:hidden; display:flex; flex-direction:column;
        background:
          radial-gradient(ellipse at 50% -6%, __Q_GLOW__, transparent 55%),
          linear-gradient(180deg, __Q_BG_HI__ 0%, __Q_BG_MID__ 45%, __Q_BG_LO__ 100%); }
.frame { position:absolute; inset:18px; pointer-events:none; border:5px solid __Q_FRAME__; border-radius:6px;
         box-shadow: inset 0 0 0 3px __Q_BASE__, inset 0 0 0 5px __GOLD__, 0 0 30px rgba(0,0,0,.6); }
.gem { position:absolute; width:24px; height:24px; transform:rotate(45deg); z-index:3;
       background:radial-gradient(circle at 35% 30%, #ff9a5a, #b03018);
       box-shadow:0 0 14px rgba(255,120,60,.6); border:2px solid #ffd9a0; }
.gem.tl { top:30px; left:30px; } .gem.tr { top:30px; right:30px; }
.gem.bl { bottom:30px; left:30px; } .gem.br { bottom:30px; right:30px; }
.wrap-in { position:relative; z-index:1; flex:1; min-height:0; display:flex; flex-direction:column;
           padding:52px 58px 46px; }
.mast { display:flex; justify-content:space-between; align-items:center; margin-bottom:22px; }
.mast .brand { font-size:24px; letter-spacing:4px; color:__GOLD__; font-weight:800; }
.mast .pg { font-size:24px; color:__Q_DIM__; letter-spacing:3px; }
h1 { font-family:"Songti SC","Noto Serif SC","STSong",serif; font-size:84px; font-weight:900; text-align:center;
     letter-spacing:3px; line-height:1.25; text-wrap:balance; word-break:keep-all;
     background:linear-gradient(180deg,__Q_H1_HI__ 0%,__GOLD__ 50%,__Q_H1_LO__ 100%);
     -webkit-background-clip:text; -webkit-text-fill-color:transparent;
     filter:drop-shadow(0 3px 4px rgba(0,0,0,.7)); }
h1.long { font-size:64px; }
h1.list { font-size:58px; }
h1.list.long { font-size:48px; }
.banner { margin:16px auto 0; display:flex; align-items:center; gap:16px; }
.banner .ln { width:70px; height:2px; background:linear-gradient(90deg, transparent, __GOLD__); }
.banner .ln.r { background:linear-gradient(270deg, transparent, __GOLD__); }
.banner .bx { background:linear-gradient(180deg,__Q_BNR_HI__,__Q_BNR_LO__); border:2px solid __GOLD_DEEP__;
              padding:8px 28px; color:__Q_BNR_TXT__; font-size:28px; font-weight:700; letter-spacing:2px;
              box-shadow:inset 0 0 12px rgba(0,0,0,.5); }
.shield { margin:20px auto 0; width:fit-content; background:linear-gradient(180deg,#a02818,#701810);
          border:2px solid __GOLD__; color:#ffd9a0; font-size:26px; font-weight:900; letter-spacing:3px;
          padding:10px 36px 24px; border-radius:14px 14px 20px 20px;
          clip-path:polygon(0 0,100% 0,100% 74%,50% 100%,0 74%);
          box-shadow:inset 0 0 10px rgba(0,0,0,.4); }
.hero { position:relative; margin-top:24px; border-radius:8px; overflow:hidden;
        border:3px solid __Q_FRAME__; box-shadow:0 12px 32px rgba(0,0,0,.55), inset 0 0 0 2px __GOLD__; }
.hero.cover { flex:1; min-height:380px; }
.hero.list { flex:0 0 28%; min-height:240px; }
.hero img { width:100%; height:100%; object-fit:cover; display:block; }
.hero .fade { position:absolute; inset:0;
              background:radial-gradient(ellipse at center, transparent 58%, __Q_FADE__ 100%); }
.ifact { display:flex; gap:18px; margin-top:24px; }
.ifc { flex:1; background:__Q_PANEL__; border:1px solid __GOLD_DEEP__; border-radius:8px;
       padding:16px 12px; text-align:center; box-shadow:inset 0 0 14px rgba(0,0,0,.4); }
.ifc .ico { width:54px; height:54px; margin:0 auto 10px; border-radius:50%;
            background:radial-gradient(circle at 35% 30%, __Q_ICO_HI__, __Q_ICO_LO__); border:2px solid __GOLD__;
            box-shadow:0 0 10px rgba(__Q_GOLD_RGB__,.35); display:flex; align-items:center; justify-content:center;
            font-size:27px; font-weight:900; color:__Q_GOLDTXT__; }
.ifc .t { font-size:25px; font-weight:800; color:__Q_GOLDTXT__; margin-bottom:4px; }
.ifc .d { font-size:21px; color:__Q_MUTE__; line-height:1.4; }
.tnote { margin-top:14px; text-align:center; font-size:22px; color:__Q_DIM__; }
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
.step .bd { flex:1; min-width:0; border-bottom:1px dashed rgba(__Q_GOLD_RGB__,.30); padding-bottom:10px; }
.step .t { font-size:30px; font-weight:800; color:__Q_STRONG__; display:flex; align-items:center;
           gap:14px; flex-wrap:wrap; }
.step .d { font-size:23px; color:__Q_MUTE__; line-height:1.45; margin-top:4px; padding-right:10px; }
.qchip { font-size:21px; font-weight:700; color:#241a10; background:linear-gradient(180deg,#ffb066,#e07020);
         border-radius:4px; padding:2px 12px; border:1px solid #93481a; }
.flowbox { margin-top:20px; background:__Q_FLOW__; border:1px solid __GOLD_DEEP__; border-radius:10px;
           padding:20px 28px; box-shadow:inset 0 0 22px rgba(0,0,0,.45); }
.flowbox .rt { font-size:23px; letter-spacing:5px; color:__GOLD__; font-weight:800; margin-bottom:10px; }
.flowbox .chain { font-size:28px; font-weight:700; color:__Q_STRONG__; line-height:1.6; }
.flowbox .chain b { color:#ff9a4a; font-weight:900; margin:0 4px; }
"""


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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
        _page_open(css),
        _esc(ctx["brand"] or "整合速览"), ctx["idx"], ctx["total"],
        _esc(card.get("section", "要点")),
        "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")), sub_html,
        rows, note_html, dots,
    )


# ── wechat 公众号信号格 渲染（纯 CSS 结构化版面，零图片依赖，永不破图） ──
# 封面：规则头 → 信号线 → 大标题 → 2×2 要点瓦片（末格绿反白）→ 阅读流带 → 页脚
def _cover_html_wechat(card, css, hero_uri, ctx):
    hooks = (card.get("hooks") or [])[:4]
    tiles = "".join(
        '<div class="tile%s"><div class="lb">%02d / POINT</div>'
        '<div class="t">%s</div><div class="d">%s</div></div>'
        % (" sigcell" if i == len(hooks) - 1 else "", i + 1,
           _esc(h.get("t", "")), _esc(h.get("d", "")))
        for i, h in enumerate(hooks)
    )
    # 阅读流带：第 1 格用卡片自带的「本期看点」一句话，后两格为固定阅读引导
    claim = str(card.get("timeline") or "")[:14] or "先看结论"
    steps = "".join(
        '<div class="step"><small>%s</small><b>%s</b></div>' % (a, _esc(b))
        for a, b in (("01 / 看点", claim),
                     ("02 / 拆解", "逐页看工具与实操"),
                     ("03 / 上手", "照步骤直接复现"))
    )
    sub = card.get("subtitle") or card.get("timeline_note") or ""
    sub_html = ('<div class="sub">%s</div>' % _esc(sub)) if sub else ""
    dots = "".join('<i class="%s"></i>' % ("on" if (i + 1) == ctx["idx"] else "")
                   for i in range(ctx["total"]))
    return """%s<div class="wrap" style="height:__H__px">
  <div class="wrap-in">
  <div class="rhead"><b>%s</b><span>COVER · 信号格 SIGNAL GRID</span></div>
  <div class="sig"></div>
  <h1 class="%s">%s</h1>
  %s
  <div class="tiles">%s</div>
  <div class="flow">
    <div class="fh"><div class="ft">这一辑怎么读</div><div class="fr">READING FLOW</div></div>
    <div class="track">%s<div class="cta">开始阅读 →</div></div>
  </div>
  <div class="ftr"><span>CLAIM · PROOF · ACTION</span><div class="dots">%s</div>
    <span>01 / %02d</span></div>
  </div>
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
        _page_open(css),
        _esc(ctx["brand"] or "整合速览"),
        "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")),
        sub_html, tiles, steps, dots, ctx["total"],
    )


# 内页：规则头 → 栏目眉题 → 大标题 → 条目卡 → 总结条（左绿边线 + 右绿行动块）→ 页脚
def _list_html_wechat(card, css, hero_uri, ctx):
    items = card.get("items") or []
    rows = "".join(
        '<div class="row"><div class="lb">%02d / %s</div>'
        '<div class="t">%s</div><div class="d">%s</div></div>'
        % (i + 1, _esc(it.get("tag") or "要点"),
           _esc(it.get("name", "")), _esc(it.get("desc", "")))
        for i, it in enumerate(items)
    )
    note = card.get("note") or {}
    note_html = (
        '<div class="note"><div class="bd"><div class="nt">%s</div>'
        '<div class="nd">%s</div></div><div class="cta">NOTE →</div></div>'
        % (_esc(note.get("title", "提醒")), _esc(note.get("text", "")))
    ) if note.get("text") else ""
    sub_html = ('<div class="sub2">%s</div>' % _esc(card.get("subtitle", ""))) if card.get("subtitle") else ""
    icon = str(card.get("icon") or "").strip()[:1]
    section = _esc(card.get("section", "要点"))
    kick = "SECTION %02d · %s%s" % (ctx["idx"], (icon + " ") if icon else "", section)
    dots = "".join('<i class="%s"></i>' % ("on" if (i + 1) == ctx["idx"] else "")
                   for i in range(ctx["total"]))
    return """%s<div class="wrap" style="height:__H__px">
  <div class="wrap-in">
  <div class="rhead"><b>%s</b><span>%s · %02d / %02d</span></div>
  <div class="kick">%s</div>
  <h2 class="%s">%s</h2>%s
  <div class="rows">%s</div>
  %s
  <div class="ftr"><span>CLAIM · PROOF · ACTION</span><div class="dots">%s</div>
    <span>%02d / %02d</span></div>
  </div>
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
        _page_open(css),
        _esc(ctx["brand"] or "整合速览"), section, ctx["idx"], ctx["total"],
        kick,
        "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")), sub_html,
        rows, note_html, dots, ctx["idx"], ctx["total"],
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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
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
    # 修复：摘要行必须包进 .ifact 横向容器（此前漏包导致 4 个 .ifc 在列容器里纵向全宽堆叠）
    facts_html = ('<div class="ifact">%s</div>' % facts) if facts else ""
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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
    _page_open(css), _esc(ctx["brand"] or "任务攻略"), _pgdate(),
    "long" if len(str(card.get("title") or "")) > 12 else "", _esc(card.get("title", "")),
    banner, shield, hero_uri, facts_html, tn_html)


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
</div></body></html>""".replace("__H__", str(ctx["canvas_h"])) % (
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
    "wechat": {"label": "公众号信号格", "css": _CSS_WECHAT,
               "cover": _cover_html_wechat, "list": _list_html_wechat,
               "hint": "视觉模板：公众号信号格——浅纸面 + 微信绿信号线 + 2×2 要点瓦片（末格绿反白）"
                       "+ 阅读流带 + 左绿边线总结条，纯 CSS 装饰零背景图依赖，"
                       "专为「抖音采集的 AI 工具/AI 科普内容 → 公众号图文」设计（无需真实截图）。"
                       "封面 hooks 3-4 条：t 为要点名词短语（≤14字）、d 一句讲完（≤20字），"
                       "timeline 填本期看点一句话；列表卡 items 的 name=工具或要点名，"
                       "tag=可选标签（如「免费」「强推」，会显示在条目眉题），desc=一句话点评，"
                       "icon 单字（如 测/坑/省/比，显示在栏目前），note 写使用提醒。"},
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
