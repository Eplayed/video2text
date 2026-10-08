#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_answer_bank.py — 《AI 应用方向面试答案库》自检（改库必跑）

为什么要有这个脚本：卖货页 FAQ 白纸黑字承诺「已逐条剔除『离开原视频就看不懂的题』、
改写题干里的指代」，而 v0.2 实测仍有指代残留与题材错位（2026-10-08 逐条数过）。
**承诺了没做到比不承诺更伤**——买家会拿这句话当验收标准。所以把承诺变成一条能跑的脚本，
以后每次改库、每次加题都跑，别靠记性和肉眼。

查四件事（都是机器判得了的形）：
  1 题干指代残留：「这个项目 / 该工具 / 视频中 / 上面提到」——离开原视频就读不通；
  2 题干残缺：缺主语（以「的」字结构开头）、短到不像题、没有问号也没"请…"；
  3 题材与小节错配：小节叫「RAG 与知识库」，题面却是公文写作——按关键词表判；
  4 计数一致：目录、正文、代码题、页眉「共 N 道」四处必须相等（改库最容易漏同步）。

用法：
  python3 scripts/audit_answer_bank.py product/AI面试答案库-v0.2.md
  python3 scripts/audit_answer_bank.py product/AI面试答案库-v0.3.md --json

退出码：0 干净 / 1 有 WARN / 2 有 FAIL。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 题干里出现这些词 = 依赖原文上下文，买家单独看题面读不懂
PRONOUN_PATTERNS = [
    (r"这个项目", "指代「这个项目」"),
    (r"该工具|这个工具", "指代「该工具」"),
    (r"视频中|视频里|视频中也|本视频", "引用原视频"),
    (r"上面提到|文中提到|刚才", "上下文指代"),
    (r"这种|这样的", "指示词无先行对象"),
]
# 答案正文里的指代：只查"引用原视频"和"指示代词"两类（"这种"在正常表述里太常见，
# 拿它报错只会造出假 WARN，反而让人开始忽略这个脚本）
ANSWER_PRONOUNS = [
    (r"视频(?:中|里|内容|介绍)|本视频|刚才|上面提到|前面提到", "引用原视频"),
    (r"该项目|这个项目|该工具|这个工具", "指示代词"),
]
# 每题应有的四段（缺一段就是答案没写完）
ANSWER_PARTS = [("参考答案", "缺「参考答案」"),
                ("答到这几个点就算过", "缺「答到就算过」"),
                ("常见坑", "缺「常见坑」"),
                ("面试官会追问", "缺「追问」")]
# 小节 → 允许出现的题材关键词；命中不了任何一条 = 疑似放错小节。
# 宁可漏判也不误杀：这里只列"整节都不该出现"的硬题材词。
SECTION_TOPIC_WORDS = {
    "RAG 与知识库": ["rag", "检索", "向量", "切片", "embedding", "知识库", "召回", "rerank"],
    "Agent 与工具调用": ["agent", "工具调用", "函数调用", "工作流", "流水线", "代理", "规划"],
    "大模型原理与长文本": ["长文本", "长篇", "长文", "上下文", "记忆", "窗口", "幻觉", "一致性", "前后矛盾"],
    "数据与安全": ["安全", "隐私", "加密", "xss", "cors", "权限", "合规", "数据"],
    "数据采集与文档处理": ["爬虫", "反爬", "抓取", "解析", "指纹", "文档", "排版", "PDF", "消息队列"],
    "AI 面试与对话产品": ["面试", "对话", "语音", "回答", "评价", "报告"],
}
# 与"AI 应用开发面试"这个卖点明显不属于同一叙事的题材词（WARN，不 FAIL：
# 网文/公文本身也是 AI 应用，但混在 95 题里会让买家怀疑"这是不是某个视频的笔记"）
OFF_TOPIC_WORDS = ["网文", "小说", "党政机关", "公文写作"]


def parse(text):
    """返回 (sections, code_items, header_count, toc_count)。sections=[(小节,[{no,q,body}])]"""
    head = text[:text.index("## 题目")] if "## 题目" in text else text[:2000]
    m = re.search(r"共\s*(\d+)\s*道", head)
    header_count = int(m.group(1)) if m else None
    m2 = re.search(r"(\d+)\s*道场景与原理题\s*\+\s*(\d+)\s*道手撕代码", text)
    toc_count = (int(m2.group(1)), int(m2.group(2))) if m2 else None

    if "## 手撕代码题" in text:
        scene = text[text.index("## 题目"):text.index("## 手撕代码题")]
        code_txt = text[text.index("## 手撕代码题"):]
    else:
        scene, code_txt = text[text.index("## 题目"):], ""
    sections = []
    parts = re.split(r"^### (.+)$", scene, flags=re.M)
    for i in range(1, len(parts), 2):
        name = parts[i].strip()
        body = parts[i + 1]
        items = []
        # 每题 = **N. 题干** 到下一个 **N. 或 ### 之前
        marks = list(re.finditer(r"^\*\*(\d+)\.\s*(.+?)\*\*\s*$", body, flags=re.M))
        for j, mk in enumerate(marks):
            end = marks[j + 1].start() if j + 1 < len(marks) else len(body)
            items.append({"no": int(mk.group(1)), "q": mk.group(2).strip(),
                          "ans": body[mk.end():end]})
        sections.append((name, items))
    codes = [int(x) for x in re.findall(r"^\*\*(\d+)\.", code_txt, flags=re.M)]
    return sections, codes, header_count, toc_count


def audit(path):
    text = io.open(path, encoding="utf-8").read()
    rows = []
    sections, codes, header_count, toc_count = parse(text)
    all_items = [(sec, it) for sec, items in sections for it in items]

    for sec, it in all_items:
        q = it["q"]
        for pat, label in PRONOUN_PATTERNS:
            if re.search(pat, q):
                rows.append(("FAIL", "题干指代残留", "%d [%s] %s —— %s"
                             % (it["no"], sec, q[:52], label)))
        if q.startswith("的") or re.match(r"^.{0,3}的工具", q):
            rows.append(("FAIL", "题干残缺", "%d [%s] 缺主语：%s" % (it["no"], sec, q[:52])))
        if len(re.sub(r"\s", "", q)) < 12:
            rows.append(("WARN", "题干过短", "%d [%s] %s" % (it["no"], sec, q[:52])))
        if ("？" not in q) and ("?" not in q) and not re.search(r"请|如何|怎样|为什么|什么是|是否", q):
            rows.append(("WARN", "题干不像问题", "%d [%s] %s" % (it["no"], sec, q[:52])))
        for name, miss in ANSWER_PARTS:
            if name not in it["ans"]:
                rows.append(("FAIL", "答案缺段", "%d [%s] %s" % (it["no"], sec, miss)))
        low = q.lower()
        for w in OFF_TOPIC_WORDS:
            if w in low:
                rows.append(("WARN", "题材与卖点不符",
                             "%d [%s] 出现「%s」：这类外壳词会让买家怀疑"
                             "「这是不是某个视频的笔记」" % (it["no"], sec, w)))
        # 题干改成通用说法了、答案还在讲网文/公文——比不改更刺眼，买家读的就是答案。
        # 这条是 v0.3 第一遍踩出来的坑（改了 12 道题干，漏了答案正文），所以进脚本。
        ans = it["ans"]
        stray = [w for w in OFF_TOPIC_WORDS if w in ans and w not in q]
        if stray:
            rows.append(("FAIL", "题干与答案题材不一致",
                         "%d [%s] 答案里还有%s，题干已是通用说法"
                         % (it["no"], sec, "、".join("「%s」" % w for w in stray))))
        for pat, label in ANSWER_PRONOUNS:
            m = re.search(pat, ans)
            if not m:
                continue
            # 「该工具/该项目」在题干已经立了先行对象时是正常指代，不算残留；
            # 但「视频中」这种引用原文的说法，题干提没提过都不该出现在卖出去的货里。
            if label != "引用原视频" and any(
                    k in q for k in ("工具", "项目", "平台", "系统", "助手")):
                continue
            rows.append(("WARN", "答案指代残留",
                         "%d [%s] %s（%s）" % (it["no"], sec, label, m.group(0))))
        words = SECTION_TOPIC_WORDS.get(sec)
        if words and not any(w in low for w in words):
            rows.append(("WARN", "疑似放错小节",
                         "%d [%s] 题面不含本节任何关键词（%s）：%s"
                         % (it["no"], sec, "/".join(words[:4]), q[:44])))

    scene_n = len(all_items)
    nums = sorted(it["no"] for _, it in all_items)
    if nums and (nums[0] != 1 or nums != list(range(1, len(nums) + 1))):
        gap = sorted(set(range(1, nums[-1] + 1)) - set(nums))
        rows.append(("FAIL", "编号连续", "场景题编号不连续，缺：%s" % gap[:12]))
    if header_count is not None and header_count != scene_n + len(codes):
        rows.append(("FAIL", "计数一致",
                     "页眉写「共 %d 道」，实际场景 %d + 代码 %d = %d"
                     % (header_count, scene_n, len(codes), scene_n + len(codes))))
    if toc_count and toc_count != (scene_n, len(codes)):
        rows.append(("FAIL", "计数一致",
                     "开头写「%d 场景 + %d 代码」，实际 %d + %d"
                     % (toc_count[0], toc_count[1], scene_n, len(codes))))
    dup = [n for n in set(nums) if nums.count(n) > 1]
    if dup:
        rows.append(("FAIL", "编号重复", str(sorted(dup)[:10])))
    rows.append(("INFO", "规模", "场景题 %d 道 / 代码题 %d 道 / 小节 %d 个；总计 %d"
                 % (scene_n, len(codes), len(sections), scene_n + len(codes))))
    per = {}
    for sec, items in sections:
        per[sec] = len(items)
    rows.append(("INFO", "各小节题数", "、".join("%s %d" % (k, v) for k, v in per.items())))
    return rows, scene_n, len(codes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=os.path.join(
        ROOT, "product", "AI面试答案库-v0.2.md"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if not os.path.isfile(args.path):
        print("找不到文件：%s" % args.path)
        return 2
    rows, scene_n, code_n = audit(args.path)
    fails = [r for r in rows if r[0] == "FAIL"]
    warns = [r for r in rows if r[0] == "WARN"]
    if args.json:
        print(json.dumps({"file": os.path.basename(args.path), "scene": scene_n,
                          "code": code_n, "fails": len(fails), "warns": len(warns),
                          "rows": rows}, ensure_ascii=False, indent=1))
        return 2 if fails else (1 if warns else 0)
    print("━━━━━━ 答案库自检 · %s ━━━━━━" % os.path.basename(args.path))
    for lvl, name, detail in rows:
        print("  [%-4s] %-12s %s" % (lvl, name, detail))
    print("━━━━━━ 判定：%s（FAIL %d / WARN %d）━━━━━━" % (
        "可以上架" if not fails and not warns else
        "有硬伤，别上架" if fails else "可上架但有观察项", len(fails), len(warns)))
    for lvl, name, detail in fails:
        if lvl == "FAIL":
            print("  必须改 → %s：%s" % (name, detail))
    return 2 if fails else (1 if warns else 0)


if __name__ == "__main__":
    sys.exit(main())
