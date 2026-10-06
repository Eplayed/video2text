#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_knowledge_map.py — 从答案库生成《前端面试 95 题知识地图》竖版长图（HTML → 截图）

为什么要脚本而不是手画：图上的 95 这个数字、13 个主题、每题的短标签，全部来自
`product/AI面试答案库-v0.2.md` 与素材库里的 structured_data.knowledgeTag。手抄一版，
下一版题库更新后图就对不上了，而这张图的作用是让人「存下来照着复习」。

短标签取法（按优先级）：
  1. 素材库里这道题的 knowledgeTag（模型抽过的考点词，如「SSE流式通信」）；
  2. 答案里「答到这几个点就算过」的第一个关键词；
  3. 题干第一个分句截断。

用法：
  python3 scripts/build_knowledge_map.py                 # 生成 HTML + PNG
  python3 scripts/build_knowledge_map.py --no-shot       # 只出 HTML，先看版式
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "product" / "AI面试答案库-v0.2.md"
DB = ROOT / "output" / "video2text.db"
OUT_HTML = ROOT / "product" / "前端面试95题知识地图.html"
OUT_PNG = ROOT / "product" / "前端面试95题知识地图.png"

THEME_RE = re.compile(r"^\*\*(.+?)（(\d+) 题）\*\*$", re.M)
# 主题在正文里是 `### 主题名`，目录里的 `**主题（N 题）**` 只用来对数
SEC_RE = re.compile(r"^### (.+)$", re.M)
# 一道题 = 「**N. 题干**」到下一个题头/主题头之前；考点关键词在块内
Q_BLOCK_RE = re.compile(r"\*\*(\d+)\. (.+?)\*\*(.*?)(?=\n\*\*\d+\. |\n### |\Z)", re.S)
KEY_RE = re.compile(r"^- 答到这几个点就算过：(.+)$", re.M)
CODE_RE = re.compile(r"^## 手撕代码题（(\d+) 道", re.M)


def load_tags():
    """题面 → 考点短标签。素材库是这些题的出处，标签是当初模型抽好的。"""
    tags = {}
    if not DB.is_file():
        return tags
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT structured_data FROM ai_summaries "
                            "WHERE summary_type='ai_interview'").fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            data = json.loads(row["structured_data"] or "{}")
        except (ValueError, TypeError):
            continue
        for q in data.get("questions") or []:
            text = re.sub(r"\s+", "", q.get("text") or "")
            tag = (q.get("knowledgeTag") or "").strip()
            if text and tag and text not in tags:
                tags[text] = tag
    return tags


def parse_bank(md):
    """正文（`## 题目` 到 `## 手撕代码题` 之间）按 `### 主题` 切，题数按实际解析到的算。

    返回 (themes, declared)：declared 是目录里写的题数，用来对账——目录和正文对不上
    说明答案库本身有问题，图不能悄悄画错。
    """
    start = md.find("## 题目")
    end = md.find("## 手撕代码题")
    body = md[start:end if end > start else len(md)]
    marks = [(m.group(1).strip(), m.start()) for m in SEC_RE.finditer(body)]
    declared = {m.group(1): int(m.group(2)) for m in THEME_RE.finditer(md)}
    out = []
    for i, (name, pos) in enumerate(marks):
        stop = marks[i + 1][1] if i + 1 < len(marks) else len(body)
        qs = []
        for m in Q_BLOCK_RE.finditer(body[pos:stop]):
            hit = KEY_RE.search(m.group(3))
            keys = [k.strip() for k in re.split(r"[、，,]", hit.group(1)) if k.strip()] if hit else []
            qs.append((int(m.group(1)), m.group(2).strip(), keys))
        if qs:
            out.append((name, len(qs), qs))
    # 手撕代码题也是题库的一部分（95 = 76 问答 + 19 代码），目录里没有它，单独并进来
    code_end = md.find("\n## ", end + 1) if end > 0 else -1
    code = md[end:(code_end if code_end > 0 else len(md))]
    cqs = []
    for m in Q_BLOCK_RE.finditer(code):
        # 代码题的考点写在题干后面：「**77. 手写 debounce…**（工具函数）」
        cat = re.match(r"\s*（(.+?)）", m.group(3) or "")
        cqs.append((int(m.group(1)), m.group(2).strip(), [cat.group(1)] if cat else []))
    if cqs:
        out.append(("手撕代码（每条真跑过）", len(cqs), cqs))
    return out, declared


def short_label(title, keys):
    """一行的短标签：优先素材库抽过的考点词，其次答案里的得分点，最后截题干。"""
    flat = re.sub(r"\s+", "", title)
    for text, tag in TAGS.items():
        if text.startswith(flat[:18]) or flat.startswith(text[:18]):
            return tag[:14]
    if keys:
        return keys[0][:14]
    cut = re.split(r"[，。？：；,?]", title)[0]
    return (cut[:13] + "…") if len(cut) > 13 else cut


def build_html(themes, total, n_code):
    hue_step = 360.0 / max(len(themes), 1)
    blocks = []
    for idx, (name, count, qs) in enumerate(themes):
        hue = int(idx * hue_step)
        is_code = name.startswith("手撕代码")
        rows = "".join(
            '<li><span class="no">%02d</span><span class="tag">%s</span>'
            '<span class="q" title="%s">%s</span></li>'
            # 代码题的类别就是它的考点词，别再拿题干去凑一遍
            % (no, html_escape(keys[0] if is_code and keys else short_label(title, keys)),
               html_escape(title), html_escape(title[:46]))
            for no, title, keys in qs)
        blocks.append(
            '<section class="theme" style="--h:%d">'
            '<h2><i>%s</i>%s<span class="n">%d 题</span></h2><ul>%s</ul></section>'
            % (hue, "%02d" % (idx + 1), html_escape(name), count, rows))
    version = re.search(r"v[\d.]+", BANK.read_text(encoding="utf-8")[:80])
    n_themes = len([t for t in themes if not t[0].startswith("手撕代码")])
    return TEMPLATE.replace("__BLOCKS__", "".join(blocks)) \
        .replace("__TOTAL__", str(total)) \
        .replace("__THEMES__", str(n_themes)) \
        .replace("__CODE__", str(n_code)) \
        .replace("__DATE__", datetime.now().strftime("%Y-%m-%d")) \
        .replace("__VERSION__", version.group(0) if version else "v0.2")


def html_escape(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>前端面试 95 题知识地图</title>
<style>
:root{--ink:#2b2621;--sub:#7a6f63;--paper:#f6f1e7;--card:#fffdf8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:#ded7c9;font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
 color:var(--ink);-webkit-font-smoothing:antialiased}
.sheet{width:1080px;margin:0 auto;padding:46px 44px 34px;background:
 radial-gradient(circle at 12% 6%,#fffaf0 0%,var(--paper) 55%);position:relative}
.sheet:before,.sheet:after{content:"";position:absolute;left:0;right:0;height:8px;
 background:repeating-linear-gradient(90deg,#e8ddc7 0 22px,transparent 22px 44px)}
.sheet:before{top:0}.sheet:after{bottom:0}
header{text-align:center;margin-bottom:26px}
h1{font-size:56px;font-weight:900;letter-spacing:2px;line-height:1.15}
h1 em{font-style:normal;color:#c2703c}
.slogan{margin-top:10px;font-size:23px;color:var(--sub)}
.stats{display:flex;justify-content:center;gap:12px;margin-top:18px;flex-wrap:wrap}
.stat{background:var(--card);border:2px solid #e6dac2;border-radius:999px;
 padding:7px 18px;font-size:20px;font-weight:700}
.stat b{color:#c2703c;font-size:24px}
.grid{column-count:2;column-gap:20px}
.theme{break-inside:avoid;background:var(--card);border:2px solid #ecdfc8;border-radius:16px;
 padding:14px 16px 12px;margin-bottom:18px;box-shadow:0 3px 0 #e4d7bd}
.theme h2{display:flex;align-items:center;gap:9px;font-size:25px;font-weight:900;
 padding-bottom:8px;border-bottom:2px dashed hsl(var(--h) 52% 72%)}
.theme h2 i{font-style:normal;font-size:16px;font-weight:800;color:#fff;background:hsl(var(--h) 46% 52%);
 border-radius:7px;padding:2px 7px}
.theme h2 .n{margin-left:auto;font-size:17px;color:var(--sub);font-weight:700}
li{display:flex;align-items:baseline;gap:8px;font-size:19px;line-height:1.55;margin-top:7px}
.no{font-size:14px;color:#b3a694;font-variant-numeric:tabular-nums;min-width:20px}
.tag{font-weight:800;color:hsl(var(--h) 52% 32%);white-space:nowrap}
.q{color:var(--sub);font-size:17px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
footer{margin-top:20px;background:#2f2a24;color:#f6efe2;border-radius:14px;padding:16px 20px;
 display:flex;align-items:center;gap:14px}
footer .big{font-size:24px;font-weight:900}
footer .small{font-size:17px;color:#c9bda9;margin-left:auto;text-align:right;line-height:1.5}
@media print{body{background:#fff}.sheet{margin:0}}
</style></head><body>
<div class="sheet">
<header>
  <h1>前端面试 <em>__TOTAL__</em> 题，无非就这些</h1>
  <div class="slogan">__THEMES__ 个主题 · 每题一个考点词 · 存下来照着复习</div>
  <div class="stats"><span class="stat">题目 <b>__TOTAL__</b></span><span class="stat">主题 <b>__THEMES__</b></span>
  <span class="stat">手撕代码 <b>__CODE__</b> 道跑过</span><span class="stat">版本 __VERSION__</span></div>
</header>
<div class="grid">__BLOCKS__</div>
<footer>
  <span class="big">要 PDF 高清原图？</span>
  <span class="small">公众号后台回复「地图」<br>整理于 __DATE__ · 题目与答案同库，答不到的点不算过</span>
</footer>
</div></body></html>"""

TAGS = {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-shot", action="store_true", help="只出 HTML，不截图")
    args = ap.parse_args()
    if not BANK.is_file():
        print("找不到答案库：%s" % BANK)
        return 1
    md = BANK.read_text(encoding="utf-8")
    global TAGS
    TAGS = load_tags()
    themes, declared = parse_bank(md)
    total = sum(len(qs) for _, _, qs in themes)
    mismatch = [(n, declared.get(n), c) for n, c, _ in themes
                if not n.startswith("手撕代码") and declared.get(n) != c]
    if mismatch:
        print("⚠️ 目录题数与正文对不上（主题, 目录, 正文）：%s" % mismatch)
    if total != 95:
        print("⚠️ 这道具现在数出 %d 题，但文件名写的是 95 题——先确认答案库有没有增删" % total)
    code = CODE_RE.search(md)
    n_code = int(code.group(1)) if code else 0
    html = build_html(themes, total, n_code)
    OUT_HTML.write_text(html, encoding="utf-8")
    print("主题 %d 个 / 题目 %d 道 / 手撕代码 %d 道 / 用到考点标签 %d 个"
          % (len(themes), total, n_code, len(TAGS)))
    print("已写出：%s（%d 字）" % (OUT_HTML, len(html)))
    if args.no_shot:
        return 0
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page(viewport={"width": 1120, "height": 900}, device_scale_factor=2)
        pg.goto(OUT_HTML.as_uri())
        pg.wait_for_timeout(700)
        h = pg.evaluate("document.querySelector('.sheet').getBoundingClientRect().height")
        pg.locator(".sheet").screenshot(path=str(OUT_PNG))
        b.close()
    print("已截图：%s（1080 × %d，长宽比 1:%.1f）" % (OUT_PNG, h, h / 1080.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
