# -*- coding: utf-8 -*-
"""从答案库里抽题，生成公众号引流稿（3 篇）+ 关键词自动回复文案。

为什么要脚本：引流稿里给的"完整答案"必须和付费资料里的一字不差，手抄一定会走样；
而且题目、字数、日期这些数字要跟着产线数据变。

每篇的结构是固定的：一个能被搜到的标题 → 三句话说清这篇解决什么 → 2-3 道完整题
（参考答案 / 得分点 / 常见坑 / 追问）→ 一句"完整版在哪"的钩子 → 来源与 AI 声明。

用法：
    python3 scripts/build_lead_articles.py          # 输出到 product/引流/
    python3 scripts/build_lead_articles.py --dry-run
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_interview_bank as bib    # noqa: E402  同一套筛选/改写，别各写一份

ROOT = bib.ROOT
OUT_DIR = ROOT / "product" / "引流"

# 三篇的选题：都按"面试官会怎么问 + 微信里会被搜的词"来定，不按我们自己想讲什么
PLAN = [
    {
        "file": "01-大模型应用面试-3道高频题.md",
        "title": "大模型应用面试：这 3 道题答不全，基本就到此为止了",
        "hook": ["大模型应用岗的面试，问来问去就那几件事：链路怎么走、幻觉怎么压、长文本怎么不前后矛盾。",
                 "这三题我按「面试官原话 → 参考答案 → 他会追问什么」写成一份资料，先把最常被卡住的三问摊开。",
                 "看的时候别只点头，试着每道先自己说一遍，再对照答案——说得出和看得出是两回事。"],
        "pick": {"themes": ["大模型原理与长文本", "RAG 与知识库", "系统设计与工程落地"], "n": 3,
                 "must": [r"长线记忆|长文本", r"幻觉|前后矛盾", r"RAG|切片|检索"]},
    },
    {
        "file": "02-前端面试-虚拟列表与CLS.md",
        "title": "前端面试：虚拟列表和 CLS 这两题，答不上说明你真没写过",
        "hook": ["一屏只渲染 20 条、滚动位置怎么算、图片高度服务端只给宽高比时 CLS 怎么做到 0——"
                 "这三问是「做过列表优化」和「背过列表优化」的分水岭。",
                 "答案里我特意保留了「面试官会追问」那一句，因为追问才是真正判分的地方。",
                 "如果你答得很顺，可以跳过这篇；如果有一两处心虚，那就是准备方向。"],
        "pick": {"themes": ["前端体验与性能", "前端框架与语言"], "n": 3,
                 "must": [r"虚拟滚动|虚拟列表", r"CLS"]},
    },
    {
        "file": "03-手写并发限制器.md",
        "title": "手写并发限制器：200 条素材调大模型接口，面试官要的就是这段",
        "hook": ["AI 应用岗的手撕代码，十有八九绕不开「同时只能跑 N 个」：调模型接口要限并发，"
                 "上传要分片，重试要退避。看着简单，写对的人不多。",
                 "下面两段代码都在 node 里跑过断言（含并发峰值、失败后名额释放这些边界），"
                 "你可以直接复制去跑，跑不通算我输。",
                 "常见写法是用 for + await 串起来——那不算并发控制，那是排队。"],
        "pick": {"handcode": ["plimit", "retry", "sse"], "n": 3},
    },
]

REPLY = {
    "file": "自动回复文案.md",
    "body": """# 关键词自动回复（公众号后台直接粘）

## 触发词
`面试`（也可同时配 `AI面试`、`题库`）

## 回复正文
在的。这份是 AI 应用 / 前端+AI 方向的面试题库，共 %(total)s 道：
%(themes)s

每道题四段：参考答案、答到哪些点算过、常见坑、面试官会追问。
其中 %(hc)s 道手撕代码的参考实现都在 node 里跑过断言，不是"看起来对"。

完整目录和样章在这里看：【填你的页面链接】
需要的话直接回我一句「题库」，我把购买方式发你。

## 说明（为什么这么写）
- 开头不说"感谢关注"，直接给内容——读者是来找题的，不是来寒暄的；
- 把主题清单列出来，是为了让他在微信里就能判断"有没有我要的那块"，比一句广告语转化高；
- 结尾留一个"再回一次"的动作，是为了把只想要免费部分的人和愿意付钱的人分开，后者才值得跟进。
"""
}


def load_bank():
    raw = bib.fetch_questions()
    keep = [q for q in raw if bib.grade(q) in ("A", "B")]
    for q in keep:
        text, unresolved = bib.rewrite(q)
        q["_text"], q["_unresolved"] = text, unresolved
    groups = {}
    for q in keep:
        groups.setdefault(bib.theme_of(q), []).append(q)
    return raw, keep, groups


def pick(groups, handcode, spec):
    """按主题顺序挑够 n 道，优先挑答案写得够长的（短答案放进引流稿显得单薄）。

    must 是给"标题已经承诺了具体题"的篇用的：引流稿的钩子说讲虚拟列表，
    正文就必须有虚拟列表那道，不能因为别的题答案长就跑题。
    """
    out = []
    want = (spec.get("pick") or {}).get("handcode")
    if want:
        by = {x["id"]: x for x in handcode}
        return [by[i] for i in want if i in by]
    pool = []
    for t in spec["pick"]["themes"]:
        pool += sorted(groups.get(t) or [], key=lambda q: -len(q.get("referenceAnswer") or ""))
    used = set()

    def take(q):
        if id(q) in used:
            return False
        used.add(id(q))
        out.append(q)
        return True

    for pat in spec["pick"].get("must") or []:
        for q in pool:
            if re.search(pat, bib.clean(q["_text"])):
                take(q)
                break
    for q in pool:
        if len(out) >= spec["pick"]["n"]:
            break
        take(q)
    return out


def qa_md(q, hc=False):
    if hc:
        lines = ["### %s" % q.get("title"), "", "**面试官会这么说**：%s" % q.get("ask", ""), "",
                 "```js", "\n".join(q.get("code") or []), "```", ""]
        if q.get("points"):
            lines += ["**考点**：" + "；".join(q["points"]), ""]
        if q.get("traps"):
            lines += ["**常见错**：" + "；".join(q["traps"]), ""]
        if q.get("followup"):
            lines += ["**追问**：" + q["followup"], ""]
        return lines
    lines = ["### %s" % bib.clean(q["_text"]), ""]
    ref = bib.clean(q.get("referenceAnswer")).replace("口播", "原分享")
    if ref:
        lines += ["**参考答案**：" + ref, ""]
    pts = [bib.clean(p, 40) for p in (q.get("answer") or []) if bib.clean(p)][:5]
    if pts:
        lines += ["**答到这几个点就算过**：" + "、".join(pts), ""]
    ex = bib.clean(q.get("explain")).replace("口播", "原分享")
    if ex:
        lines += ["**常见坑**：" + ex, ""]
    dp = bib.clean(q.get("deeper")).replace("口播", "原分享")
    if dp:
        lines += ["**他会追问**：" + dp, ""]
    return lines


def article(spec, qs, total, hc_total, date):
    lines = ["# %s" % spec["title"], ""]
    lines += ["> 全文 %d 字左右，读完约 4 分钟。%s" % (sum(len(bib.clean(q.get("referenceAnswer") or "")) for q in qs) + 600, date), ""]
    for h in spec["hook"]:
        lines += [h, ""]
    lines += ["---", ""]
    for q in qs:
        lines += qa_md(q, hc=("code" in q)) + ["", "---", ""]
    lines += ["", "## 完整版在这里", ""]
    lines += ["上面是 %d 道。整份资料共 %d 道（其中 %d 道手撕代码，代码全部在 node 里跑过断言），"
              "按主题分好了，可以只翻自己要补的那块。" % (len(qs), total, hc_total), ""]
    lines += ["目录和样章（免费看）：【填你的页面链接】", ""]
    lines += ["## 来源与声明", ""]
    lines += ["内容是对公开分享（视频 / 文章）的转述整理，题目化与归纳有 AI 辅助，"
              "已剔除离开原文就读不通的题；数字与技术结论请自行核对，不构成任何平台的官方说法。", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw, keep, groups = load_bank()
    handcode = bib.load_handcode()
    total = len(keep) + len(handcode)
    date = datetime.now().strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    made = []
    for spec in PLAN:
        qs = pick(groups, handcode, spec)
        md = article(spec, qs, total, len(handcode), date)
        (OUT_DIR / spec["file"]).write_text(md, encoding="utf-8")
        made.append((spec["file"], len(qs), len(md)))
        print("  %-34s %d 道完整题，%d 字" % (spec["file"], len(qs), len(md)))
    themes = "\n".join("- %s：%d 道" % (t, len(groups.get(t) or []))
                       for t in [x for x, _ in bib.THEMES] + [bib.FALLBACK_THEME] if groups.get(t))
    reply = REPLY["body"] % {"total": total, "themes": themes, "hc": len(handcode)}
    (OUT_DIR / REPLY["file"]).write_text(reply, encoding="utf-8")
    print("  %-34s %d 字" % (REPLY["file"], len(reply)))
    print("合计 %d 个文件 → %s" % (len(made) + 1, OUT_DIR))
    return 0


if __name__ == "__main__":
    sys.exit(main())
