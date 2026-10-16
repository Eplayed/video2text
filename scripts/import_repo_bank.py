# -*- coding: utf-8 -*-
"""把 GitHub 仓库 didilili/ai-agents-from-zero 的《AI 智能体与大模型应用开发面试题库》
拆成结构化 JSON，并和自家 95 题做重复度对照。

为什么要有这一步：那份题库是 MIT 许可、5.1K star、12 节 90 题，带「出现次数 / 难度 / 考察点 /
对应教程章节」四个我们没有的字段——它既是我们最大的缺口对照，也是**最直接的竞品**：
免费、成体系、就在 GitHub 上。搬之前必须先量清楚"重合多少、我们缺多少"，
否则就是把同一份东西卖两遍。

许可与口径（MIT，2026-10-09 核过仓库 license 字段）：
  · 可以改、可以用、可以商用，**必须保留版权声明**；
  · 它的「出现次数：N 次」是**它自己统计的**，我们没验过——搬进付费资料时必须标来源，
    不能写成我们测的（卖货页 FAQ 里"数字请自己再核"那条同样管这里）；
  · 它带教程内链（对应章节），那是它的地基，我们搬题不搬教程，所以引用一律去掉相对路径、
    只留"来自哪个仓库哪一节"的说明。

用法：
    python3 scripts/import_repo_bank.py                  # 下载+解析+落 product/repo_bank.json
    python3 scripts/import_repo_bank.py --diff           # 再打一份与自家 95 题的对照
    python3 scripts/import_repo_bank.py --from temp/_repo_bank.md   # 用本地已下载的原文（不联网）
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_interview_bank as bib    # noqa: E402  主题判据只有一份

REPO = "didilili/ai-agents-from-zero"
FILE = "AI智能体与大模型应用开发面试题库.md"
RAW = ("https://raw.githubusercontent.com/%s/main/%s"
       % (REPO, urllib.parse.quote(FILE)))
BLOB_SHA = "879ba947f07f3098d961f9efae5d94ed0f0cad3a"      # 2026-10-09 抓取时的 blob sha
COMMIT = "7165a729e3a2"                                     # 同日 main
OUT = ROOT / "product" / "repo_bank.json"

Q_RE = re.compile(r"^### (Q\d+-\d+)\.\s*(.+?)\s*$", re.M)
SEC_RE = re.compile(r"^## (\d+)、(.+?)\s*$", re.M)
ASK_RE = re.compile(r"\*\*常见追问：\*\*\s*(.*?)(?=\n\*\*|\Z)", re.S)
META_RE = re.compile(r"\*\*重要度：\*\*\s*`?([^`|（]+)`?（出现次数：(\d+)次）\s*\|\s*\*\*难度：\*\*\s*`?([^`\n]+)`?")
FOCUS_RE = re.compile(r"\*\*考察点：\*\*\s*(.+?)(?=\n\n|\n\*\*|\Z)", re.S)
REF_RE = re.compile(r"\*\*对应章节：\*\*\s*(.+?)(?=\n\n|\Z)", re.S)
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def clean_md(s):
    """去掉 markdown 装饰，留纯文本——题库后面要进 PDF 也要进小程序，先别混排版语法。"""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s or "")
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"[ \t]+\n", "\n", s).strip()
    return s


def parse(md):
    marks = list(SEC_RE.finditer(md))
    qmarks = list(Q_RE.finditer(md))
    if not qmarks:
        return []

    def section_at(pos):
        cur = ""
        for m in marks:
            if m.start() < pos:
                cur = m.group(2)
            else:
                break
        return cur

    items = []
    for i, m in enumerate(qmarks):
        start = m.end()
        end = qmarks[i + 1].start() if i + 1 < len(qmarks) else len(md)
        block = md[start:end]
        # 答案 = 到第一个 **xxx：** 标签之前的全部正文
        cut = re.search(r"\n\*\*(?:常见追问|重要度|考察点|对应章节)", block)
        answer = clean_md(block[:cut.start()] if cut else block)
        ask = ASK_RE.search(block)
        followup = [clean_md(x) for x in re.findall(r"^\s*[-*]\s+(.+)$", ask.group(1), re.M)] if ask else []
        meta = META_RE.search(block)
        focus = FOCUS_RE.search(block)
        refs = REF_RE.search(block)
        items.append({
            "id": m.group(1),
            "section": section_at(m.start()),
            "question": clean_md(m.group(2)),
            "answer": answer,
            "followup": [f for f in followup if f],
            "freq": (meta.group(1).strip() if meta else ""),
            "freq_n": int(meta.group(2)) if meta else 0,
            "difficulty": (meta.group(3).strip() if meta else ""),
            "focus": clean_md(focus.group(1)) if focus else "",
            "refs": [LINK_RE.sub(r"\1", clean_md(refs.group(1))) if refs else ""],
        })
    return items


def ours():
    """自家 95 题（走同一条产线：筛选 + 改写 + 编号），保证对照用的是发出去的那份文字。"""
    import build_bank_pdf as bpdf      # noqa: E402  编号逻辑只有一份，别再抄第三遍
    raw, keep, groups, order, counts = bpdf.load_bank()
    return keep, bib.load_handcode()


# 方向覆盖：题干里有没有这个词。Jaccard 那种"整句像不像"判不准（同一件事两种问法分数很低），
# 所以判缺口按**方向关键词**数，数出来的结论也才能拿去跟人说。
COVERAGE = [
    ("MCP / 协议", r"MCP|模型上下文协议|A2A"),
    ("LangChain / LangGraph", r"LangChain|LangGraph|LCEL|Checkpoint"),
    ("微调 / 训练", r"微调|LoRA|SFT|续训|显存|蒸馏"),
    ("Coze / Dify / 平台", r"Coze|Dify|RAGFlow|工作流平台"),
    ("向量库 / Embedding", r"向量|Embedding|embedding|图谱|GraphRAG"),
    ("切块 / 检索优化", r"切块|chunk|召回|重排|rerank|混合检索|查询改写|Lost in the Middle"),
    ("幻觉与忠实度", r"幻觉|拒答|引用|不忠实"),
    ("安全与护栏", r"Injection|注入|护栏|沙箱|越权|脱敏|权限"),
    ("评测 / 可观测 / 成本", r"评测|评估|指标|可观测|监控|badcase|成本|耗时|性能瓶颈|延迟"),
    ("模型路由 / 选型", r"路由|选型|降级|多家模型"),
    ("场景设计题", r"NL2SQL|客服|知识库问答|Deep Research|Code Agent|浏览器自动化|代码解释器|Manus"),
    ("Agent 编排", r"ReAct|Function Calling|工具调用|多智能体|终止|规划|反思"),
    ("记忆", r"记忆|上下文窗口|短期|长期"),
    ("前端 / 客户端", r"前端|React|Vue|CSS|渲染|浏览器|H5|小程序|虚拟列表|CLS"),
    ("项目表达", r"介绍.*项目|项目.*讲|怎么回答|表达"),
]


def coverage(items, keep):
    rows = []
    for name, pat in COVERAGE:
        theirs = sum(1 for it in items if re.search(pat, it["question"] + " " + it["section"], re.I))
        ours_n = sum(1 for q in keep if re.search(pat, q["_text"], re.I))
        ours_blob = sum(1 for q in keep
                        if re.search(pat, " ".join([q["_text"], str(q.get("referenceAnswer") or "")] +
                                                   [str(x) for x in (q.get("answer") or [])]), re.I))
        rows.append((name, theirs, ours_n, ours_blob))
    return rows


STOP = set("什么的为什么如何怎么进行可以用需要在哪个哪些是不是有一般通常工程上应该怎么做设计一个如果".replace(" ", ""))


def tokens(s):
    """中文按二元组 + 英文数字按词，够粗但够用来判"这两题是不是同一回事"。"""
    s = (s or "").lower()
    lat = re.findall(r"[a-z]{2,}\d*|[\u4e00-\u9fff]", s)
    han = re.findall(r"[\u4e00-\u9fff]", s)
    big = set("".join(han[i:i + 2]) for i in range(len(han) - 1))
    words = set(t for t in lat if not t.isdigit() and len(t) >= 2)
    return big | words


def diff(items, keep):
    ours_t = [(q, tokens(q["_text"])) for q in keep]
    rows = []
    for it in items:
        t = tokens(it["question"])
        best, score = None, 0.0
        for q, ot in ours_t:
            if not t or not ot:
                continue
            j = len(t & ot) / float(len(t | ot))
            if j > score:
                best, score = q, j
        rows.append((it, best, score))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default="", help="用本地 md（不联网）")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--diff", action="store_true", help="再和自家题库做对照（要工作台在线）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.src:
        md = Path(args.src).read_text(encoding="utf-8")
    else:
        md = urllib.request.urlopen(RAW, timeout=90).read().decode("utf-8")
    items = parse(md)
    n_q = len(Q_RE.findall(md))
    if len(items) != n_q:
        print("解析数 %d ≠ 原文题数 %d，有题没吃到" % (len(items), n_q))
        return 1
    for it in items:
        it["theme"] = bib.theme_of({"_text": it["question"], "knowledgeTag": it["section"]})
    empty = [it["id"] for it in items if len(it["answer"]) < 40]
    doc = {
        "source": {"repo": REPO, "file": FILE, "license": "MIT", "blob_sha": BLOB_SHA,
                   "commit": COMMIT, "fetched": datetime.now().strftime("%Y-%m-%d"),
                   "note": "题目与答案为该仓库整理，出现次数/难度为原作者统计，未经我们复核"},
        "count": len(items), "items": items,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.quiet:
        print("解析 %d 题 ｜ 答案空/过短 %d ｜ 字数中位 %d ｜ 落 %s"
              % (len(items), len(empty), sorted(len(x["answer"]) for x in items)[len(items) // 2], args.out))
        print("重要度分布：", dict(Counter(x["freq"] for x in items)))
        print("难度分布：  ", dict(Counter(x["difficulty"] for x in items)))
        print("它的分节：  ", dict(Counter(x["section"] for x in items)))
        print("按我们主题判据归类：", dict(Counter(x["theme"] for x in items)))

    if args.diff:
        keep, handcode = ours()
        rows = diff(items, keep)
        hi = [r for r in rows if r[2] >= 0.30]
        mid = [r for r in rows if 0.18 <= r[2] < 0.30]
        print("\n【方向覆盖】它的 89 题 vs 我们 76 道场景题（题干命中；括号内含答案正文）")
        print("%-22s %5s %8s" % ("方向", "它", "我们"))
        for name, theirs, ours_n, ours_blob in coverage(items, keep):
            flag = "  ← 我们几乎没有" if ours_n == 0 and theirs >= 4 else ("  ← 偏薄" if ours_n * 3 < theirs else "")
            print("%-22s %4d  %3d (%3d)%s" % (name, theirs, ours_n, ours_blob, flag))
        print("\n【整句重合度】高度重合 %d ｜ 相近 %d ｜ 其余 %d（这栏只能当线索，别当结论：同一件事两种问法分数就低）"
              % (len(hi), len(mid), len(rows) - len(hi) - len(mid)))
        for it, q, sc in sorted(hi + mid, key=lambda r: -r[2])[:12]:
            print("   %.2f  %s %s" % (sc, it["id"], it["question"][:30]))
            print("        ≈ %d. %s" % (q["_no"], bib.clean(q["_text"], 40)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
