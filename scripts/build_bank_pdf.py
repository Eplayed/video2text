# -*- coding: utf-8 -*-
"""把答案库排成一份能直接发给买家的 PDF（A4 打印版 / 手机竖版两种页面）。

为什么交付用 PDF 而不是网页（2026-10-08 拍板）：
① 这条线没有落地页——卖货页上「付款方式 / 发货方式」两处占位还没填，收款后能给出去的只有一个文件；
② 微信发文件是唯一确定跑得通的交付动作：不依赖服务器、不怕链接过期、不用买家开浏览器；
③ PDF 在手机里能看、能搜、能打印，且版式由我定——网页版式到了买家手机上就不是我说的算了。

「不能修改其中内容」这句话怎么落地——先把边界说清楚，再选手段：
  PDF 不是改不动。任何编辑器都能改文字，权限位（禁止编辑）是几秒钟就能拆掉的装饰，
  拿它当卖点等于承诺一件做不到的事。真正有用的是**每页带买家标记**：
  转发出去的那份带着转发者的名字，所以这份资料按「一人一份」生成——
      --buyer "微信昵称 或 订单号"   → 斜向水印三处 + 页脚一处，每页重复（实测 position:fixed 元素在
      Chrome 打印时确实每页都出现，本机逐页看过）
  --flatten 是第二档：整页转图片再合成，文字选不中、复制不走，代价是体积和"搜不到字"。默认不开。

版式与交互的取舍（为什么这么排，逐条对应交接件 output/角色交接/2026-10-09-wechat-ui.md）：
  · 目录可点：每个题号是内部锚点（Chrome 会写成 PDF /Link，实测 /Annots 有值）；
    不做页码——Chrome 不支持 target-counter，硬凑两遍排版取页码不值，
    改用「PDF 书签 + 题号」导航：generateDocumentOutline 把 h1/h2/h3 变成侧边书签树。
  · 每个主题起新页（break-before），题卡 break-inside:avoid，避免「题干在上一页、答案在下一页」。
  · 手机竖版页面 90×195mm：微信里"适应宽度"打开时正好铺满一屏，字号按页面宽度放大，
    A4 直接缩到手机上是 3.7pt，那是看不懂的——所以两个版本都要出。
  · 交付物里一个占位符都不许留：正文出现「口播 / 视频中 / 【」直接拒绝出文件。

用法：
    python3 scripts/build_bank_pdf.py                          # A4 自用/预览版（无买家标记）
    python3 scripts/build_bank_pdf.py --size phone             # 手机竖版
    python3 scripts/build_bank_pdf.py --buyer "张三 ab12"       # 发给某个买家的版本
    python3 scripts/build_bank_pdf.py --sample                 # 免费样章（全目录 + 3 道完整题）
    python3 scripts/build_bank_pdf.py --flatten                # 图片化（防复制，体积大）
    python3 scripts/build_bank_pdf.py --dry-run                # 只算页面清单，不出文件
"""
import argparse
import base64
import html
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_interview_bank as bib      # noqa: E402  版本/定价/题目：单一数据源
import build_bank_page as bbp           # noqa: E402  题卡渲染：卖货页样章与 PDF 正文必须同一份

# 页面尺寸：mm。phone 那档按 iPhone 屏幕比例取，微信「适应宽度」能铺满一屏。
SIZES = {
    "a4": {"w": 210, "h": 297, "mt": 15, "mb": 14, "mx": 15,
           "base": "10.4pt", "code": "8.4pt", "wm": "26pt", "foot_px": 7.5},
    "phone": {"w": 90, "h": 195, "mt": 6, "mb": 9, "mx": 6,
              "base": "12.2pt", "code": "8.2pt", "wm": "15pt", "foot_px": 6.5},
}
# 交付物里绝对不能出现的字样：出现了就说明这份东西离开原视频还是读不通，或者占位没填。
FORBIDDEN = ["口播", "视频中", "视频里", "根据视频", "结合口播", "【待填", "【填", "【请", "__"]

CSS = """
@page{size:@@W@@mm @@H@@mm;margin:@@MT@@mm @@MX@@mm @@MB@@mm @@MX@@mm}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;color:#16181d;background:#fff;line-height:1.62;
font-family:-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
font-size:@@BASE@@}
h1{font-size:2.05em;line-height:1.25;margin:0 0 .35em;letter-spacing:-.4px}
h2{font-size:1.32em;margin:0 0 .5em;padding-bottom:.28em;border-bottom:2px solid #16181d;
break-after:avoid;break-inside:avoid}
h3{font-size:1.02em;margin:0 0 .45em;line-height:1.5;break-after:avoid}
p{margin:0 0 .62em}
a{color:#16181d;text-decoration:none}
.muted{color:#5c6270}
.small{font-size:.86em}
/* 封面 */
.cover{height:@@CH@@mm;display:flex;flex-direction:column;justify-content:center}
.kicker{font-size:.82em;font-weight:700;letter-spacing:.14em;color:#4b3bd1;margin:0 0 .6em}
.ver{font-size:.98em;color:#4a4f5a;margin:0 0 1.6em}
.hero-rule{height:3px;background:#16181d;margin:0 0 1.2em}
.box{border:1px solid #d9d5cc;border-radius:3mm;padding:2.6mm 3.4mm;margin:0 0 2.4mm}
/* 只把 box 里第一个 <b> 当小标题独占一行；正文里的 <b> 必须保持行内，
   否则「先照参考答案<b>开口说一遍</b>」会被撑成三行（实测踩过） */
.box>b:first-child{display:block;margin-bottom:.3em}
.stamp{display:inline-block;align-self:flex-start;border:1.5px solid #4b3bd1;color:#4b3bd1;
border-radius:99px;padding:.5mm 3mm;font-size:.8em;font-weight:700;letter-spacing:.04em}
/* 目录 */
.toc h2{margin-bottom:.7em}
.part{font-size:.9em;font-weight:700;color:#4b3bd1;border-top:2px solid #4b3bd1;
padding-top:1mm;margin:0 0 1.6mm;break-after:avoid}
.toc .th{margin:0 0 .18em;font-size:1.02em;font-weight:700}
.toc .th span{font-weight:400;color:#5c6270;font-size:.85em}
.toc ol{margin:.1em 0 .9em;padding-left:0;list-style:none;font-size:.9em;color:#3c414c}
.toc li{margin:0 0 .12em;padding-left:.2em}
.toc li a{font-weight:700;color:#4b3bd1;margin-right:.45em}
/* 样章里没答案的题：号做成灰色不可点，一眼看出"这条点不动是因为没收录" */
.toc li .no{font-weight:700;color:#b3b8c2;margin-right:.45em}
/* 题卡：与卖货页样章同一份内容（build_bank_page.qa_html / hc_html） */
.qa{border:1px solid #e3e0d8;border-radius:2.6mm;padding:2.4mm 3mm;margin:0 0 2.2mm;
break-inside:avoid}
.qa h3{margin:0 0 .4em}
.tag{display:inline-block;font-size:.72em;font-weight:700;color:#4b3bd1;
border:1px solid currentColor;border-radius:99px;padding:0 1.6mm;margin:0 0 .5em}
/* 主题标签在 PDF 里是重复信息（上面就是主题大标题），浮到右上角省一行 */
.qa>.tag{float:right;margin:0 0 .6mm .6em}
/* 外部题库的来源与重要度徽标：自家题不出这行 */
.badge{font-size:.8em;color:#4a4f5a;background:#f7f6f2;border-left:2px solid #4b3bd1;
padding:.6mm 1.6mm;margin:0 0 1.2mm}
.dl{margin:0;font-size:.94em}
.dl dt{font-weight:700;margin-top:.5em;break-after:avoid}
.dl dd{margin:.1em 0 0;color:#3c414c}
pre.code{border:1px solid #e3e0d8;border-radius:2mm;padding:1.8mm 2.2mm;margin:0 0 1.4mm;
white-space:pre-wrap;word-break:break-word;font-size:@@CODE@@;line-height:1.42;
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#faf9f6}
section{break-before:page}
.gaps li,.toc-note p{margin-bottom:.3em}
/* 每页水印：fixed 元素在 Chrome 打印时逐页重复（已实测） */
.wm{position:fixed;top:0;left:0;right:0;bottom:0;pointer-events:none;z-index:-1;overflow:hidden}
.wm i{position:absolute;transform:rotate(-32deg);font-style:normal;white-space:nowrap;
font-size:@@WM@@;color:rgba(90,95,105,.13);letter-spacing:.06em}
.wm i:nth-child(1){top:14%;left:-10%}
.wm i:nth-child(2){top:46%;left:-2%}
.wm i:nth-child(3){top:78%;left:-8%}
"""

TPL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>@@TITLE@@</title><style>@@CSS@@</style></head>
<body>@@WM@@
<section class="cover">
  <p class="kicker">AI 应用 / 大模型产品 / 前端 + AI 岗位</p>
  <h1>把「面过才知道的题」<br>提前摊开</h1>
  <p class="ver">AI 应用方向面试答案库 v@@VERSION@@<br>@@SCOPE@@</p>
  <div class="hero-rule"></div>
  <div class="box"><b>怎么用</b>先扫目录，标出自己答不上来的题——那 10 题就是这两天的功课；每题先照「参考答案」<b>开口说一遍</b>（不是看一遍），再对照「答到这几个点就算过」查漏；最后把「面试官会追问」当第二遍自测——追问能接住，这题才算真会。答案里的数字、工具名、时间全部换成你自己项目里的，背模板最容易被问穿。</div>
  <div class="box"><b>这份只收这一条线</b>AI 应用落地（大模型 / RAG / Agent / 多模态 / 前端体验 / 数据与安全 / 系统设计）。算法岗的推导题、论文题一条都没有。</div>
  <p class="stamp">@@STAMP@@</p>
</section>

<section>
  <h2>拿到手先确认三件事</h2>
  <div class="box"><b>① 版本对不对</b>这一版是 v@@VERSION@@（@@DATE@@），共 @@TOTAL@@ 题。后续加题不另收费：在哪个号里付的款，就在那个号里说一声「要新版」，我发新文件。</div>
  <div class="box"><b>② 内容哪来的、可信到什么程度</b>两处来源，分得很清：第一部分 @@NOWN@@ 道是对公开分享（视频 / 文章）的转述整理，题目化与归纳有 AI 辅助（依《人工智能生成合成内容标识办法》在此显式声明），并已逐条剔除「离开原视频就看不懂」的题、改写题干里的指代；第二部分 @@NREPO@@ 道取自 GitHub 仓库 didilili/ai-agents-from-zero（MIT License，Copyright (c) 2026 didilili），题干与答案沿用其整理，每题带来源徽标。上架自检脚本会逐条复检题干指代、答案缺段、题材与小节是否对得上，v@@VERSION@@ 这一版的结果是：@@AUDIT@@。@@NHC@@ 道手撕代码的参考实现全部在 node 里跑过断言，跑不通的不收。</div>
  <div class="box"><b>③ 数字不能直接引用</b>参数量、延迟、成本这类事实请自己再核一遍公开来源，这份资料不构成任何平台的官方说法。</div>
  <p class="muted small">这份 PDF 是@@STAMP2@@。文字可复制、可打印，不做加密——加密拦不住想改的人，所以每页带买家标记：转发的那份带着转发者的名字。</p>
</section>

<section class="toc">
  <h2>目录（@@NTHEME@@ 个主题 / @@TOTAL@@ 题）</h2>
  <p class="muted small">@@TOCNOTE@@</p>
  @@TOC@@
</section>

@@BODY@@

<section>
  <h2>这份还没做到的（下一版要补的）</h2>
  <ul class="gaps">
    <li>场景题没给「按你自己项目怎么答」的填空模板，现在还是通用答案；</li>
    <li>缺一份「我做过什么」的项目追问清单——那才是决定 offer 的部分；</li>
    <li>手撕代码只覆盖接口工程与前端高频，图论 / 动态规划那类算法题不在范围内；</li>
    <li>数字类事实（模型参数量、延迟、成本）没有逐条核到公开来源，引用前自己确认。</li>
  </ul>
</section>

<section>
  <h2>来源与说明</h2>
  <p>第一部分 @@NOWN@@ 道：对公开分享（视频 / 文章）的转述整理，用 AI 辅助做题目化与归纳，
  并剔除离开原文就看不懂的题（本次剔除 @@NC@@ 道、改写主语后保留 @@NB@@ 道）。</p>
  <p>第二部分 @@NREPO@@ 道：取自 GitHub 仓库 <b>didilili/ai-agents-from-zero</b>，
  按 MIT License 使用并在此保留其版权声明：<b>Copyright (c) 2026 didilili</b>。
  这部分的题干与答案沿用该仓库的整理，其「出现次数」「难度」两个字段是原作者统计的，我们未复核；
  这一部分只有参考答案与追问，没有「答到就算过」和「常见坑」。</p>
  <p>手撕代码的参考实现由脚本在 node 里逐条跑断言，跑不通的不进这份资料。</p>
  <p>所有数字与技术结论请在使用前自行核对，本资料不构成任何平台的官方说法。</p>
  <p class="muted small">整理：@@DATE@@ ｜ 版本 v@@VERSION@@ ｜ 这一页之后没有了，
  往下翻就是第一题。</p>
</section>
</body></html>
"""


def esc(s):
    return html.escape(str(s or ""), quote=True)


def fill(tpl, **kw):
    """@@k@@ 占位而不是 % / {}：CSS 里全是 % 和 {}，用它们格式化会咬人。"""
    for k, v in kw.items():
        tpl = tpl.replace("@@%s@@" % k, str(v))
    return tpl


def load_bank():
    """取数全部走 build_interview_bank.load_all()：与 .md、卖货页同一份筛选/改写/编号。
    原来这里自己抄了一遍分组和编号，外部题库一进来就会分错块、号也对不上。"""
    d = bib.load_all()
    return d["raw"], d["keep"], d["groups"], d["order"], d["counts"]


def anchor(inner, el_id, no=None):
    """给卡片挂锚点：不改渲染函数，只在最外层 div 上加 id——
    渲染函数与卖货页共用，改它等于改两处，容易漏。
    no 用来给手撕代码题补题号：卖货页那边是按标题列的、没有号，而 PDF 目录里有号（77–95），
    两边号对不上买家会以为目录漏了题。"""
    if no is not None:
        inner = inner.replace("<h3>", "<h3>%d. " % no, 1)
    return inner.replace("<div class=\"qa\">", "<div class=\"qa\" id=\"%s\">" % el_id, 1)


def theme_ids(order, groups):
    """主题锚点用 th1/th2…，不用主题名：名字里有空格和 &，做成 URL 片段迟早出问题。"""
    m, i = {}, 0
    for t in order:
        if groups.get(t):
            i += 1
            m[t] = "th%d" % i
    return m


def part_div(theme, groups, order):
    """第二部分的分隔说明只在外部题库的第一节前面出现一次（目录和正文各用一次）。"""
    if not bib.is_ext(theme):
        return ""
    first = next((t for t in order if groups.get(t) and bib.is_ext(t)), None)
    if first != theme:
        return ""
    n_ext = len([q for t in order if bib.is_ext(t) for q in (groups.get(t) or [])])
    return ("<p class=\"part\">第二部分 · 外部题库 %d 题：题干与答案沿用 GitHub 仓库整理（MIT 署名见文末来源页），"
            "其「出现次数 / 难度」为原作者统计、我们未复核；这一部分只有参考答案与追问。</p>" % n_ext)


def toc_html(groups, order, handcode, start_hc, tids, only=None, avail=None):
    """avail 给定时（样章）：只有正文里真有这一题的才做成链接。
    样章第一版把 35 条目录全做成链接，实测 PDF 里只剩 6 个真链接——
    Chrome 会静默丢掉指向不存在锚点的链接，点了没反应，比不给链接更糟。"""
    def link(anchor_id, label, text):
        if avail is None or anchor_id in avail:
            return "<li><a href=\"#%s\">%s</a> %s</li>" % (anchor_id, label, text)
        return "<li><span class=\"no\">%s</span> %s</li>" % (label, text)

    out = []
    for t in order:
        qs = groups.get(t) or []
        if not qs:
            continue
        shown = qs if only is None else qs[:only]
        items = "".join(link("q%d" % q["_no"], str(q["_no"]), esc(bib.clean(q["_text"], 44))) for q in shown)
        more = "" if only is None or len(shown) == len(qs) else \
            "<li class=\"muted\">……另有 %d 题</li>" % (len(qs) - len(shown))
        th = ("<a href=\"#%s\">%s</a>" % (tids[t], esc(t))
              if (avail is None or tids[t] in avail) else esc(t))
        out.append("%s<p class=\"th\">%s <span>%d 题（第 %d–%d 题）</span></p>"
                   "<ol>%s%s</ol>" % (part_div(t, groups, order), th, len(qs),
                                      qs[0]["_no"], qs[-1]["_no"], items, more))
    if handcode:
        items = "".join(link("hc%d" % (i + start_hc), str(i + start_hc), esc(bib.clean(it.get("title"), 44)))
                        for i, it in enumerate(handcode if only is None else handcode[:only]))
        more = "" if only is None or only >= len(handcode) else \
            "<li class=\"muted\">……另有 %d 题</li>" % (len(handcode) - only)
        out.append("<p class=\"th\">手撕代码 <span>%d 题（代码全部跑过）</span></p><ol>%s%s</ol>"
                   % (len(handcode), items, more))
    return "\n".join(out)


def body_html(groups, order, handcode, start_hc, tids, sample_nos=None, sample_hc=None):
    """sample_nos 给定时只出这些题的完整答案（免费样章），其余只留目录。
    样章挑哪几题直接复用卖货页的 pick_samples：白看的三题和样章里的三题必须是同一批，
    不然买家拿样章去对照卖货页，发现对不上——那是最容易被差评的一种不一致。"""
    out = []
    for t in order:
        qs = groups.get(t) or []
        if not qs:
            continue
        take = qs if sample_nos is None else [q for q in qs if q["_no"] in sample_nos]
        if not take:
            continue
        cards = "".join(anchor(bbp.qa_html(q), "q%d" % q["_no"]) for q in take)
        note = "" if sample_nos is None else \
            "<p class=\"muted\">这一节共 %d 题，样章只放 %d 题；其余 %d 题在完整版里，格式与上面完全相同。</p>" \
            % (len(qs), len(take), len(qs) - len(take))
        out.append("<section>%s<h2 id=\"%s\">%s<span class=\"muted small\">　%d 题</span></h2>%s%s</section>"
                   % (part_div(t, groups, order), tids[t], esc(t), len(qs), cards, note))
    hc = [it for it in handcode if sample_hc is None or it.get("id") in sample_hc]
    if hc:
        # 题号按**全量列表**的位置算（77–95），只出样章题时也不能从 1 开始编
        cards = "".join(anchor(bbp.hc_html(it), "hc%d" % (start_hc + i), start_hc + i)
                        for i, it in enumerate(handcode)
                        if sample_hc is None or it.get("id") in sample_hc)
        note = "" if sample_hc is None else \
            "<p class=\"muted\">样章只放这 1 道；完整版共 %d 道，全部跑过断言。</p>" % len(handcode)
        out.append("<section><h2>手撕代码题（%d 道，每条参考实现都在 node 里真跑过）</h2>"
                   "<p class=\"muted small\">这一节的代码不是「看起来对」，是执行过的。"
                   "面试官要的不是你背得出，是你写的时候知道边界在哪。</p>%s%s</section>"
                   % (len(handcode), cards, note))
    return "\n".join(out)


def watermark_text(opts):
    if opts["sample"]:
        return "免费样章 · 完整版 %d 题" % opts["total"]
    if opts["buyer"]:
        return "个人版 · %s · %s" % (opts["buyer"], opts["date"])
    return "预览版 · 未分发 · v%s" % bib.VERSION


def build_html(groups, order, keep, counts, handcode, size, opts):
    s = SIZES[size]
    total = len(keep) + len(handcode)
    n_repo = len([q for q in keep if q.get("_src") == "repo"])
    n_own = len(keep) - n_repo
    start_hc = len(keep)
    ntheme = len([t for t in order if groups.get(t)]) + (1 if handcode else 0)
    wm = watermark_text(dict(opts, total=total))
    wm_html = ("<div class=\"wm\"><i>%s</i><i>%s</i><i>%s</i></div>" % (esc(wm), esc(wm), esc(wm))) \
        if opts["watermark"] else ""
    stamp = "免费样章（不含答案）" if opts["sample"] else ("已购买 · 个人版" if opts["buyer"] else "预览版（未分发）")
    stamp2 = "免费样章" if opts["sample"] else ("买家「%s」的专属版本" % opts["buyer"] if opts["buyer"] else "预览版")
    sample_nos = sample_hc = None
    if opts["sample"]:
        # 与卖货页样章同一批题：白看的三题 == 样章里的三题 == 完整版里的同号题
        sample_nos = set(q["_no"] for q in bbp.pick_samples(groups, keep))
        demo = next((x for x in handcode if x.get("id") == "plimit"),
                    handcode[0] if handcode else None)
        sample_hc = set([demo.get("id")]) if demo else set()
    n_scene = len(sample_nos) if sample_nos is not None else len(keep)
    n_code = len(sample_hc) if sample_hc is not None else len(handcode)
    tids = theme_ids(order, groups)
    avail = None
    if opts["sample"]:
        avail = set("q%d" % n for n in sample_nos)
        for i, it in enumerate(handcode):
            if it.get("id") in sample_hc:
                avail.add("hc%d" % (start_hc + i))
        for t in order:
            if any(q["_no"] in sample_nos for q in (groups.get(t) or [])):
                avail.add(tids[t])
    toc_note = ("这份样章只收录 %d 题的完整答案——下面<b>能点开</b>的那几条就是，其余只给题干。"
                % (n_scene + n_code)) if opts["sample"] else \
        ("点题号能跳到那一题；手机和电脑上侧边「书签」里有同一棵树，按主题、按题号都能翻。")
    scope = ("样章含 %d 道完整题 + %d 道手撕代码 ｜ 完整版 %d 道（%d 场景 + %d 代码）｜ 整理日期 %s"
             % (n_scene, n_code, total, len(keep), len(handcode), opts["date"])) \
        if opts["sample"] else ("%d 道（%d 道场景与原理 + %d 道手撕代码）｜ 整理日期 %s"
                                % (total, len(keep), len(handcode), opts["date"]))
    css = fill(CSS, W=s["w"], H=s["h"], MT=s["mt"], MB=s["mb"], MX=s["mx"], BASE=s["base"],
               CODE=s["code"], WM=s["wm"], CH=s["h"] - s["mt"] - s["mb"] - 4)
    body = fill(TPL, TITLE="AI 应用方向面试答案库 v%s（%d 题）" % (bib.VERSION, total),
                CSS=css, WM=wm_html, VERSION=bib.VERSION, DATE=opts["date"], SCOPE=scope,
                TOTAL=total, NSCENE=len(keep), NHC=len(handcode), NTHEME=ntheme,
                NOWN=n_own, NREPO=n_repo, AUDIT=opts.get("audit") or "未跑（自检脚本没找到当前版本的 .md）",
                NC=counts["C"], NB=counts["B"], STAMP=stamp, STAMP2=stamp2,
                TOC=toc_html(groups, order, handcode, start_hc, tids,
                             only=3 if opts["sample"] else None, avail=avail),
                TOCNOTE=toc_note,
                BODY=body_html(groups, order, handcode, start_hc, tids, sample_nos, sample_hc))
    meta = {"total": total, "ntheme": ntheme, "cards": n_scene + n_code, "codes": n_code,
            "links": body.count("<a href=\"#")}
    return body, meta


def check(text, meta):
    """交付前自检：占位符、指代泄漏、题数。PDF 是发出去的东西，一个字没填都不该漏。"""
    bad = []
    for w in FORBIDDEN:
        n = text.count(w)
        if n:
            bad.append("「%s」出现 %d 次" % (w, n))
    n_card = len(re.findall(r"<div class=\"qa\"", text))
    n_code = len(re.findall(r"<pre class=\"code\"", text))
    if n_card != meta["cards"]:
        bad.append("题卡 %d 张 ≠ 应有 %d 张" % (n_card, meta["cards"]))
    if n_code != meta["codes"]:
        bad.append("代码块 %d 段 ≠ 应有 %d 道手撕" % (n_code, meta["codes"]))
    return [b for b in bad if b]


def footer_tpl(size, opts, total):
    s = SIZES[size]
    left = "AI 面试答案库 v%s · %s" % (bib.VERSION, opts["date"])
    if opts["sample"]:
        left = "免费样章 · 完整版 %d 题" % total
    elif opts["buyer"]:
        left = "个人版 · %s · 请勿转发" % opts["buyer"]
    return ('<div style="display:flex;justify-content:space-between;width:100%%;'
            'font-size:%dpx;color:#8a8f98;font-family:-apple-system,PingFang SC,sans-serif;">'
            '<span>%s</span><span>第 <span class="pageNumber"></span> 页 / 共 '
            '<span class="totalPages"></span> 页</span></div>' % (s["foot_px"], left))


def pdf_params(size, opts, total):
    s = SIZES[size]
    mm2in = 25.4
    return {
        "preferCSSPageSize": True,
        "printBackground": True,
        "transferMode": "ReturnAsBase64",
        "generateDocumentOutline": True,          # 侧边书签树：h1/h2/h3 直接变可跳转目录
        "paperWidth": round(s["w"] / mm2in, 4),
        "paperHeight": round(s["h"] / mm2in, 4),
        "marginTop": s["mt"] / mm2in, "marginBottom": s["mb"] / mm2in,
        "marginLeft": s["mx"] / mm2in, "marginRight": s["mx"] / mm2in,
        "displayHeaderFooter": True,
        "headerTemplate": "<div></div>",
        "footerTemplate": footer_tpl(size, opts, total),
    }


def page_count(raw):
    """数页：/Count 不可信——Chrome 会分页树，而且别处也有 /Count 键
    （实测一份 62 页的 PDF 里 /Count 最大能数到 113）。/Type /Page 才是页。"""
    t = raw.decode("latin-1", "ignore")
    return len(re.findall(r"/Type\s*/Page[^s]", t))


def to_pdf(html_text, params, page_ranges=None, src_url=None):
    """一次启动打一遍；pageRanges 只用来抽单页做证据/图片化。
    src_url 给定时用 goto：set_content 的基地址是 about:blank，
    那样 file:// 的图片一律加载不了（实测图片版第一次出来只有 7KB，全页空白）。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        p = b.new_page()
        if src_url:
            p.goto(src_url, wait_until="networkidle")
        else:
            p.set_content(html_text, wait_until="networkidle")
        cdp = p.context.new_cdp_session(p)
        prm = dict(params)
        if page_ranges:
            prm["pageRanges"] = page_ranges
        raw = base64.b64decode(cdp.send("Page.printToPDF", prm)["data"])
        b.close()
    return raw


def print_ranges(html_text, params, ranges):
    """同一趟浏览器打完多页：每页重启一次 Chrome，59 页要三分多钟，不值当。"""
    from playwright.sync_api import sync_playwright
    out = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        p = b.new_page()
        p.set_content(html_text, wait_until="networkidle")
        cdp = p.context.new_cdp_session(p)
        for r in ranges:
            out[r] = base64.b64decode(
                cdp.send("Page.printToPDF", dict(params, pageRanges=r, transferMode="ReturnAsBase64"))["data"])
        b.close()
    return out


def flatten(html_text, params, size, workdir):
    """整页转图片再合成：文字选不中、复制不走。代价是体积和"手机里搜不到字"，
    而且书签和目录跳转一起没了——所以默认不开，只作为"这一份不许改"的加强档。
    光栅化用 qlmanage（QuickLook 走 PDFKit，按矢量重绘，能指定像素宽），
    本机没有 gs / qpdf / pdftotext，为这一档装它们不值当。"""
    n = page_count(to_pdf(html_text, params))
    s = SIZES[size]
    imgs = []
    for i, one in print_ranges(html_text, params, [str(i) for i in range(1, n + 1)]).items():
        f = os.path.join(workdir, "p%s.pdf" % i)
        with open(f, "wb") as fh:
            fh.write(one)
        subprocess.run(["qlmanage", "-t", "-s", "2000", "-o", workdir, f],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        png = f + ".png"
        if not os.path.exists(png):
            raise RuntimeError("第 %s 页光栅化失败（qlmanage 没出图）" % i)
        jpg = os.path.join(workdir, "p%s.jpg" % i)
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "82",
                        png, "--out", jpg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        imgs.append(jpg)
    if any(not os.path.exists(p) or os.path.getsize(p) < 20000 for p in imgs):
        raise RuntimeError("有页面出的图小于 20KB，多半是空白页，别把这种文件发出去")
    # 图片按原页面尺寸铺满，一页一张；宽高写死避免四舍五入多出一张空白页，最后一张不能 break-after
    tags = "".join("<img src=\"%s\"%s>" % (os.path.basename(p), "" if i < len(imgs) - 1 else " class=\"last\"")
                   for i, p in enumerate(imgs))
    flat_css = ("@page{size:%dmm %dmm;margin:0}html,body{margin:0;padding:0}"
                "img{display:block;width:%dmm;height:%dmm;break-after:page}"
                "img.last{break-after:auto}" % (s["w"], s["h"], s["w"], s["h"]))
    doc = ("<!doctype html><html><head><meta charset=\"utf-8\"><style>%s</style>"
           "</head><body>%s</body></html>" % (flat_css, tags))
    doc_path = os.path.join(workdir, "flat.html")
    with open(doc_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    prm = dict(params)
    prm["generateDocumentOutline"] = False
    prm["displayHeaderFooter"] = False
    prm["marginTop"] = prm["marginBottom"] = prm["marginLeft"] = prm["marginRight"] = 0
    raw = to_pdf(None, prm, src_url="file://" + doc_path)
    n_img = len(re.findall(r"/Subtype\s*/Image", raw.decode("latin-1", "ignore")))
    if n_img < n:
        raise RuntimeError("图片版只合进 %d 张图（应 %d 页），图片没加载成功" % (n_img, n))
    return raw, n


def out_name(size, opts, total):
    stem = "AI面试答案库-样章" if opts["sample"] else "AI面试答案库"
    tail = "-%s" % opts["buyer"].replace(" ", "") if opts["buyer"] else ""
    flat = "-图片版" if opts["flatten"] else ""
    tag = "" if size == "a4" else "-手机竖版"
    return "%s-v%s%s%s%s.pdf" % (stem, bib.VERSION, tag, flat, tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(Path(bib.ROOT) / "product"))
    ap.add_argument("--size", choices=sorted(SIZES), default="a4")
    ap.add_argument("--buyer", default="", help="微信昵称或订单号：每页水印 + 页脚，一人一份")
    ap.add_argument("--sample", action="store_true", help="免费样章：全目录只放 3 题答案 + 1 道手撕")
    ap.add_argument("--no-watermark", action="store_true", help="预览用；对外发不允许")
    ap.add_argument("--flatten", action="store_true", help="整页图片化（防复制，体积大、手机搜不到字）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    opts = {"buyer": args.buyer.strip(), "sample": args.sample,
            "watermark": not args.no_watermark, "flatten": args.flatten,
            "date": datetime.now().strftime("%Y-%m-%d")}
    raw, keep, groups, order, counts = load_bank()
    ok, verdict = bib.audit_gate()        # 先过上架自检，再谈出 PDF：见 build_interview_bank.audit_gate
    if not ok:
        print("自检没过，不出 PDF：%s" % verdict)
        print("   （改完库要先跑 python3 scripts/build_interview_bank.py 重出 .md，再回来出 PDF）")
        return 1
    opts["audit"] = verdict
    handcode = bib.load_handcode()
    body, meta = build_html(groups, order, keep, counts, handcode, args.size, opts)
    total, ntheme = meta["total"], meta["ntheme"]
    bad = check(body, meta)
    print("题目 %d（场景 %d + 手撕 %d）/ %d 个主题 / 本次渲染题卡 %d 张 / 页面 %s / 水印「%s」%s"
          % (total, len(keep), len(handcode), ntheme, meta["cards"], args.size,
             watermark_text(dict(opts, total=total)), "（图片化）" if args.flatten else ""))
    if bad:
        print("拒绝出文件，先修这些：")
        for b in bad:
            print("   ✗ %s" % b)
        return 1
    if args.dry_run:
        Path("temp/_bank_preview.html").write_text(body, encoding="utf-8")
        print("dry-run：HTML 已写 temp/_bank_preview.html，未出 PDF")
        return 0

    params = pdf_params(args.size, opts, total)
    if args.flatten:
        with tempfile.TemporaryDirectory() as wd:
            raw_pdf, n = flatten(body, params, args.size, wd)
    else:
        raw_pdf = to_pdf(body, params)
        n = page_count(raw_pdf)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    path = Path(args.out_dir) / out_name(args.size, opts, total)
    path.write_bytes(raw_pdf)
    t = raw_pdf.decode("latin-1", "ignore")
    n_link = len(re.findall(r"/Subtype\s*/Link", t))
    print("已写出：%s ｜ %d 页 ｜ %.1f KB ｜ 侧边书签 %s ｜ 可点链接 %d 处 ｜ 中文字体已嵌入 %s"
          % (path, n, len(raw_pdf) / 1024.0, "有" if "/Outlines" in t else "无",
             n_link, "是" if "/CIDFontType" in t else "否"))
    if not args.flatten and n_link < meta["links"]:
        print("   注意：可点链接 %d 处，少于目录条目 %d 条——目录跳转可能没生成" % (n_link, meta["links"]))
    if n < (5 if args.sample else 20):
        print("   注意：页数偏少（%d），检查是不是分页或内容没渲染出来" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
