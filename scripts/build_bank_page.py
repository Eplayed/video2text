# -*- coding: utf-8 -*-
"""把答案库做成一张能对外发的卖货页（单文件 HTML，手机优先，零外部依赖）。

为什么用脚本而不是手写页面：题目、主题、样章都从产线数据里取，
下次整理稿变多，重跑一次页面就是新的；手写的页面三个月后就和内容对不上了。

三条硬要求：
① 单文件、不引 CDN、不引字体——公众号里打开、发文件给别人、丢到任何静态空间都要能看；
② 手机优先（读者 99% 在手机微信里），360px 到 1280px 不许出现横向滚动；
③ 没填的信息（价格、联系方式）一律用显眼的占位标出来，并在页顶计数，
   避免出现"占位没填就发出去"这种砸口碑的事。

用法：
    python3 scripts/build_bank_page.py                 # 输出到 仓库根/AI面试答案库-卖货页.html
    python3 scripts/build_bank_page.py --out 路径
    python3 scripts/build_bank_page.py --dry-run       # 只打印将渲染的题数与样章来源
"""
import argparse
import html
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_interview_bank as bib    # noqa: E402  复用同一套筛选/分组/改写，别各写一份

# 文案集中放这里：改话不用动模板。带【】的是必须本人填的占位。
COPY = {
    "kicker": "AI 应用 / 前端 + AI 岗位",
    "h1": "把「面过才知道的题」提前摊开",
    "lede": "76 道 AI 应用落地方向的面试题。每题不只给答案，还给你「答到哪几个点算过」、"
            "「面试官一听就知道你没做过的坑」，以及「你答完第一句之后一定会被追问的那一句」。",
    "适合谁": [
        "投 AI 应用 / 大模型产品 / 前端 + AI 这类岗位，但没系统准备过面试问答的人",
        "做过一点东西、说不成一条链，被「你这个怎么实现的」问住的人",
        "想刷面经但找不到成体系的中文题解的人",
    ],
    "不适合谁": [
        "算法岗：推导、论文、训练细节这里一条都没有",
        "想要「标准答案背一遍就过」的人：数字和项目经历必须换成你自己的，照背最容易问穿",
        "指望这份替代动手做项目的人——它只解决「会做但说不清」",
    ],
    "怎么用": [
        "先按主题扫一遍目录，标出自己答不上来的题——那 10 题就是你这两天的功课；",
        "每题先照「参考答案」开口说一遍（不是看一遍），再对照「答到这几个点就算过」查漏；",
        "把「面试官会追问」当成第二遍自测：追问能接住，这题才算真会；",
        "最后把答案里的数字、工具名、时间全部换成你自己项目里的。",
    ],
    "faq": [
        ("内容哪来的？", "对公开分享（视频 / 文章）做转述整理，题目化和归纳有 AI 辅助，"
                    "再逐条剔除「离开原视频就看不懂」的题、改写题干里的指代。"),
        ("数字和结论能直接引用吗？", "不能。参数量、延迟、成本这类事实请自己再核一遍公开来源，"
                              "这份资料不构成任何官方说法。"),
        ("为什么是 v0.1？", "手撕代码题还没有（这条线必考）、场景题缺「按你自己项目怎么答」的填空模板、"
                        "也没有项目追问清单。这三块会在 v0.2 补，买过的按联系方式补发。"),
        ("多少钱、怎么买？", "见下面的价格与购买方式。【填完再对外发】"),
    ],
}

CSS = """
:root{--ink:#16181d;--ink2:#4a4f5a;--bg:#fbfaf7;--card:#fff;--line:#e3e0d8;
--accent:#4b3bd1;--accent-ink:#fff;--warn-bg:#fff4e0;--warn-line:#e8b96a;--warn-ink:#7a4b06}
@media (prefers-color-scheme:dark){:root{--ink:#eceae4;--ink2:#a6abb5;--bg:#14151a;--card:#1c1e24;
--line:#2e313a;--accent:#a99cff;--accent-ink:#14151a;--warn-bg:#2a2313;--warn-line:#7a5c22;--warn-ink:#f0c67a}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);line-height:1.75;
font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:0 20px}
.skip{position:absolute;left:-9999px;top:0;background:var(--card);color:var(--ink);padding:8px 14px;z-index:9}
.skip:focus{left:8px;top:8px}
a{color:var(--accent)}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
h1{font-size:clamp(26px,7.4vw,38px);line-height:1.25;margin:10px 0 12px;letter-spacing:-.4px}
h2{font-size:clamp(19px,5vw,23px);margin:0 0 6px}
h3{font-size:16px;margin:22px 0 6px}
p{margin:0 0 14px}
.kicker{font-size:13px;font-weight:700;letter-spacing:.12em;color:var(--accent);margin:26px 0 0}
.lede{color:var(--ink2);font-size:16.5px}
.hero{padding-bottom:22px;border-bottom:1px solid var(--line)}
.stats{list-style:none;display:flex;gap:10px;padding:0;margin:18px 0;flex-wrap:wrap}
.stats li{flex:1 1 92px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 10px;text-align:center}
.stats b{display:block;font-size:26px;line-height:1.1}
.stats span{font-size:12.5px;color:var(--ink2)}
.btn{display:inline-block;background:var(--accent);color:var(--accent-ink);text-decoration:none;
padding:13px 20px;border-radius:11px;font-weight:700;font-size:15.5px}
.btn.ghost{background:transparent;color:var(--accent);border:1.5px solid var(--accent);padding:11.5px 18px}
.cta{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0 4px}
.warn{background:var(--warn-bg);border:1px solid var(--warn-line);color:var(--warn-ink);
padding:11px 14px;border-radius:11px;font-size:14px;margin:18px 0}
section{padding:26px 0;border-bottom:1px solid var(--line)}
ul{padding-left:20px;margin:0 0 14px}
li{margin:0 0 8px}
.two{display:grid;gap:18px}
@media (min-width:620px){.two{grid-template-columns:1fr 1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
details{border:1px solid var(--line);border-radius:12px;background:var(--card);margin:0 0 10px}
summary{cursor:pointer;padding:13px 15px;font-weight:700;list-style:none;display:flex;gap:10px;align-items:baseline}
summary::-webkit-details-marker{display:none}
summary::before{content:"＋";color:var(--accent);font-weight:800;flex:0 0 auto}
details[open]summary::before{content:"－"}
summary .n{margin-left:auto;color:var(--ink2);font-size:12.5px;font-weight:400}
ol.q{margin:0;padding:0 15px 14px 42px;font-size:15px;color:var(--ink2)}
ol.q li{margin:0 0 6px}
.qa{border:1px solid var(--line);border-radius:14px;background:var(--card);padding:16px;margin:0 0 14px}
.qa h3{margin:0 0 10px;font-size:16.5px;line-height:1.5}
.tag{display:inline-block;font-size:11.5px;font-weight:700;color:var(--accent);
border:1px solid currentColor;border-radius:999px;padding:1px 8px;margin:0 0 8px}
.dl{margin:0;font-size:15px}
.dl dt{font-weight:700;margin-top:12px}
.dl dd{margin:2px 0 0;color:var(--ink2)}
.price{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:6px 0 14px}
.price b{font-size:38px;line-height:1}
.price s{color:var(--ink2);font-size:16px}
.todo{background:var(--warn-bg);border-bottom:2px dashed var(--warn-line);color:var(--warn-ink);
padding:0 4px;border-radius:4px;font-weight:700}
.faq dt{font-weight:700;margin-top:16px}
.faq dd{margin:4px 0 0;color:var(--ink2)}
footer{padding:22px 0 108px;color:var(--ink2);font-size:13px}
.bar{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);
padding:11px 20px;display:flex;align-items:center;gap:12px;z-index:5}
.bar .p{font-weight:800}
.bar .btn{margin-left:auto;padding:10px 16px;font-size:14.5px}
@media (min-width:760px){.bar{display:none}footer{padding-bottom:40px}}
@media print{.bar,.cta,.warn{display:none}body{background:#fff;color:#000}footer{padding-bottom:0}}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
"""

TPL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<meta name="description" content="%(desc)s">
<style>%(css)s</style>
</head>
<body>
<a class="skip" href="#main">跳到正文</a>
<div class="wrap">
<header class="hero">
  <p class="kicker">%(kicker)s</p>
  <h1>%(h1)s</h1>
  <p class="lede">%(lede)s</p>
  <ul class="stats">
    <li><b>%(nq)s</b><span>道题</span></li>
    <li><b>%(nt)s</b><span>个主题</span></li>
    <li><b>4</b><span>段/题</span></li>
  </ul>
  <p class="cta"><a class="btn" href="#buy">看价格与购买</a><a class="btn ghost" href="#sample">先看样章</a></p>
</header>
%(warn)s
<main id="main">
<section>
  <h2>适合谁，不适合谁</h2>
  <div class="two">
    <div class="card"><h3>适合</h3><ul>%(fits)s</ul></div>
    <div class="card"><h3>不适合（别浪费钱）</h3><ul>%(nofits)s</ul></div>
  </div>
</section>
<section>
  <h2>目录</h2>
  <p class="lede">点开一个主题就能看到全部题干——先扫一遍，标出自己答不上来的。</p>
  %(toc)s
</section>
<section id="sample">
  <h2>免费样章（%(nsm)s 题，完整版）</h2>
  <p class="lede">和付费部分同一套格式，看完就知道自己要不要。</p>
  %(samples)s
</section>
<section>
  <h2>怎么用这份东西</h2>
  <ul>%(howto)s</ul>
</section>
<section id="buy">
  <h2>价格与购买</h2>
  <p class="price"><b>￥%(price)s</b><s>后续加题不另收费</s></p>
  <p>付款方式：<span class="todo">【待填：小报童 / 知识星球链接，或微信号】</span></p>
  <p>买完怎么发货：<span class="todo">【待填：付款后加微信发文件，还是平台自动发货】</span></p>
  <p class="lede">v0.1 是 %(nq)s 题；补上手撕代码题之后会涨到 100 题以上，价格跟着上调，已买的按上面的联系方式补发。</p>
</section>
<section>
  <h2>几个老实问题</h2>
  <dl class="faq">%(faq)s</dl>
</section>
<footer>
  <p>整理日期 %(date)s ｜ 共 %(nq)s 道 ｜ 内容是对公开分享的转述整理，题目化与归纳有 AI 辅助，已逐条复核题干；
  数字与技术结论请在使用前自行核对，本资料不构成任何平台的官方说法。</p>
</footer>
</main>
</div>
<div class="bar"><span class="p">￥%(price)s</span><span class="lede">%(nq)s 题 · 每题四段</span><a class="btn" href="#buy">购买</a></div>
</body>
</html>
"""


def esc(s):
    return html.escape(str(s or ""), quote=True)


def pick_samples(groups, keep, n=3):
    """样章从三个不同主题里各挑一道答案最完整的——只给一个主题会让人以为整份都是那个方向。"""
    want = ["大模型原理与长文本", "前端体验与性能", "系统设计与工程落地"]
    out = []
    for t in want:
        cands = [q for q in groups.get(t) or [] if len((q.get("referenceAnswer") or "").strip()) >= 150]
        if not cands:
            cands = groups.get(t) or []
        if cands:
            out.append(cands[0])
    if len(out) < n:
        for q in keep:
            if q not in out and len((q.get("referenceAnswer") or "").strip()) >= 150:
                out.append(q)
            if len(out) >= n:
                break
    return out


def qa_html(q):
    rows = ["<div class=\"qa\"><span class=\"tag\">%s</span><h3>%d. %s</h3><dl class=\"dl\">"
            % (esc(bib.theme_of(q)), q["_no"], esc(bib.clean(q["_text"])))]
    if q.get("_unresolved"):
        rows.append("<dt>说明</dt><dd>题干里的指代还没换成具体名字，发布前人工补一句。</dd>")
    ref = bib.clean(q.get("referenceAnswer")).replace("口播", "原分享")
    pts = [bib.clean(p, 40) for p in (q.get("answer") or []) if bib.clean(p)][:5]
    ex = bib.clean(q.get("explain")).replace("口播", "原分享")
    dp = bib.clean(q.get("deeper")).replace("口播", "原分享")
    if ref:
        rows.append("<dt>参考答案</dt><dd>%s</dd>" % esc(ref))
    if pts:
        rows.append("<dt>答到这几个点就算过</dt><dd>%s</dd>" % esc("、".join(pts)))
    if ex:
        rows.append("<dt>常见坑</dt><dd>%s</dd>" % esc(ex))
    if dp:
        rows.append("<dt>面试官会追问</dt><dd>%s</dd>" % esc(dp))
    rows.append("</dl></div>")
    return "".join(rows)


def build(groups, keep, samples):
    toc = []
    for t in [x for x, _ in bib.THEMES] + [bib.FALLBACK_THEME]:
        qs = groups.get(t) or []
        if not qs:
            continue
        items = "".join("<li>%s</li>" % esc(bib.clean(q["_text"], 60)) for q in qs)
        toc.append("<details><summary>%s<span class=\"n\">%d 题</span></summary><ol class=\"q\">%s</ol></details>"
                   % (esc(t), len(qs), items))
    faq = "".join("<dt>%s</dt><dd>%s</dd>" % (esc(a), esc(b)) for a, b in COPY["faq"])
    price = "39.9"
    todo_marks = 2                      # 付款方式、发货方式
    todos = todo_marks
    warn = ("<div class=\"warn\">本页还有 <b>%d</b> 处占位没填（付款方式、发货方式），"
            "填完再对外发。</div>" % todo_marks)
    return TPL % {
        "title": "AI 应用方向面试答案库 v0.1（%d 题）" % len(keep),
        "desc": "%d 道 AI 应用落地面试题，每题给参考答案、得分点、常见坑和面试官追问。" % len(keep),
        "css": CSS, "kicker": esc(COPY["kicker"]), "h1": esc(COPY["h1"]), "lede": esc(COPY["lede"]),
        "nq": len(keep), "nt": len([1 for x, _ in bib.THEMES if groups.get(x)] +
                                  [1 for x in [bib.FALLBACK_THEME] if groups.get(x)]),
        "fits": "".join("<li>%s</li>" % esc(x) for x in COPY["适合谁"]),
        "nofits": "".join("<li>%s</li>" % esc(x) for x in COPY["不适合谁"]),
        "howto": "".join("<li>%s</li>" % esc(x) for x in COPY["怎么用"]),
        "toc": "".join(toc), "samples": "".join(qa_html(q) for q in samples), "nsm": len(samples),
        "faq": faq, "price": price, "warn": warn, "date": datetime.now().strftime("%Y-%m-%d"),
    }, todos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(bib.ROOT) / "AI面试答案库-卖货页.html"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw = bib.fetch_questions()
    keep = [q for q in raw if bib.grade(q) in ("A", "B")]
    for q in keep:
        text, unresolved = bib.rewrite(q)
        q["_text"], q["_unresolved"] = text, unresolved
    order = [t for t, _ in bib.THEMES] + [bib.FALLBACK_THEME]
    groups = {}
    for q in keep:
        groups.setdefault(bib.theme_of(q), []).append(q)
    n = 0
    for t in order:                      # 题号按页面出现顺序编，样章里的编号才对得上目录
        for q in groups.get(t) or []:
            n += 1
            q["_no"] = n
    samples = pick_samples(groups, keep)
    counts = {"C": sum(1 for q in raw if bib.grade(q) == "C")}
    htmltext, todos = build(groups, keep, samples)
    print("渲染 %d 题 / %d 个主题 / 样章 %d 题（%s）/ 待填占位 %d 处"
          % (len(keep), len([t for t in order if groups.get(t)]), len(samples),
             "、".join(bib.theme_of(q) for q in samples), todos))
    if args.dry_run:
        return 0
    Path(args.out).write_text(htmltext, encoding="utf-8")
    print("已写出：%s（%d 字）" % (args.out, len(htmltext)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
