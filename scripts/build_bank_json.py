# -*- coding: utf-8 -*-
"""把答案库出一份**结构化 JSON**（`product/题库-v<版本>.json`），给小程序题库当数据地基，
PDF / 卖货页 / .md 也认这份字段——一份数据多处用，别再各抄一遍。

为什么先做这个再动手搭小程序：小程序那三块（prompt / skill / 题库）里，只有题库是有现成数据的；
而题库能不能上小程序，卡的是"数据长什么样、有多少条、个人主体能放什么"，不是界面。
先把 schema 定死，小程序端拿着 JSON 就能开工，改库也不用回头问"这个字段哪来的"。

字段口径（小程序端直接照这个表写死即可）
  meta.version / meta.built / meta.price      版本、构建日期、当前售价（都来自 build_interview_bank）
  meta.counts                                 {total, scene, code, themes}
  meta.sources[]                              两处来源各一条：key=own / repo，repo 那条带 MIT 署名与
                                              "出现次数为原作者统计"的免责说明——**界面上要能显示出来**，
                                              署名不能只藏在文末
  meta.audit                                  上架自检的当场结论（跑不出来就为空，空说明没跑）
  themes[].id                                 th1、th2… 稳定 id（改名不影响引用）
  themes[].source                             own / repo，前端可据此分组显示"第一部分 / 第二部分"
  items[].id                                  q<题号> / c<序号>，全局唯一，小程序收藏与进度用它当 key
  items[].no                                全书连续题号（1–165 场景与外部，手撕另起一段）
  items[].type                              scene（问答题）/ code（手撕代码题）
  items[].question / answer                 题干与参考答案（都有，非空）
  items[].points[]                          「答到这几个点就算过」，外部题与代码题为空数组
  items[].traps                             「常见坑」，可为空
  items[].followup                          「面试官会追问」，可为空
  items[].ask / items[].code[]              仅 type=code：面试官原话 + 代码行数组（跑过断言）
  items[].freq / freq_n / difficulty        仅外部题有：出现次数与难度，**是原作者统计的**
  items[].source                            own / repo；repo 的题界面上要带"来源：GitHub 仓库（MIT）"
  items[].tag                               考点短标签（自家题来自素材库 knowledgeTag），搜索用

用法：
    python3 scripts/build_bank_json.py                 # 出 JSON
    python3 scripts/build_bank_json.py --check         # 只校验，不写文件
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_interview_bank as bib    # noqa: E402

SCHEMA = "bank.v1"


def build():
    d = bib.load_all()
    keep, groups, order, handcode = d["keep"], d["groups"], d["order"], d["handcode"]
    ok, verdict = bib.audit_gate()
    themes, items = [], []
    for t in order:
        qs = groups.get(t) or []
        if not qs:
            continue
        tid = "th%d" % (len(themes) + 1)
        themes.append({"id": tid, "name": t, "count": len(qs),
                       "source": "repo" if bib.is_ext(t) else "own"})
        for q in qs:
            items.append({
                "id": "q%d" % q["_no"], "no": q["_no"], "type": "scene",
                "theme_id": tid, "theme": t, "source": q.get("_src") or "own",
                "question": bib.clean(q["_text"]),
                "answer": bib.afix(bib.clean(q.get("referenceAnswer"))).replace("口播", "原分享"),
                "points": [bib.clean(p, 40) for p in (q.get("answer") or []) if bib.clean(p)][:5],
                "traps": bib.afix(bib.clean(q.get("explain"))).replace("口播", "原分享"),
                "followup": bib.afix(bib.clean(q.get("deeper"))).replace("口播", "原分享"),
                "freq": q.get("freq") or "", "freq_n": int(q.get("freq_n") or 0),
                "difficulty": q.get("difficulty") or "",
                "tag": (q.get("knowledgeTag") or "").strip(),
            })
    n = len(keep)
    for i, it in enumerate(handcode):
        n += 1
        items.append({
            "id": "c%d" % (i + 1), "no": n, "type": "code",
            "theme_id": "code", "theme": it.get("theme") or "手撕代码", "source": "own",
            "question": (it.get("title") or "").strip(), "ask": it.get("ask") or "",
            "code": it.get("code") or [], "points": it.get("points") or [],
            "traps": "；".join(it.get("traps") or []), "followup": it.get("followup") or "",
            "answer": "", "freq": "", "freq_n": 0, "difficulty": "", "tag": "",
        })
    themes.append({"id": "code", "name": "手撕代码（每条真跑过）", "count": len(handcode), "source": "own"})
    doc = {
        "meta": {
            "product": "AI 应用方向面试答案库", "schema": SCHEMA, "version": bib.VERSION,
            "built": datetime.now().strftime("%Y-%m-%d"), "price": bib.PRICE,
            "counts": {"total": len(items), "scene": len(keep), "code": len(handcode),
                       "themes": len(themes)},
            "sources": [
                {"key": "own", "label": "自家整理", "count": d["n_own"],
                 "note": "对公开分享（视频/文章）的转述整理，题目化与归纳有 AI 辅助，已逐条复核题干"},
                {"key": "repo", "label": "外部题库", "count": d["n_repo"],
                 "repo": bib.REPO_NAME, "license": "MIT", "copyright": bib.REPO_COPYRIGHT,
                 "note": "题干与答案沿用该仓库整理；其「出现次数/难度」为原作者统计，我们未复核。"
                         "界面上必须能显示来源，署名不能只藏在文末"},
            ],
            "audit": verdict if ok else "",
            "audit_ok": ok,
        },
        "themes": themes, "items": items,
    }
    return doc, d


def check(doc):
    bad = []
    ids = [x["id"] for x in doc["items"]]
    if len(ids) != len(set(ids)):
        bad.append("id 有重复")
    tids = set(t["id"] for t in doc["themes"])
    miss = sorted(set(x["theme_id"] for x in doc["items"]) - tids)
    if miss:
        bad.append("有 item 引用了不存在的 theme_id：%s" % miss)
    for x in doc["items"]:
        if not x["question"]:
            bad.append("%s 没有题干" % x["id"])
        if x["type"] == "scene" and not x["answer"]:
            bad.append("%s 问答题没有参考答案" % x["id"])
        if x["type"] == "code" and not x["code"]:
            bad.append("%s 代码题没有代码" % x["id"])
    n_repo = len([x for x in doc["items"] if x["source"] == "repo"])
    if n_repo != doc["meta"]["sources"][1]["count"]:
        bad.append("外部题计数对不上：items 里 %d，meta 写 %d" % (n_repo, doc["meta"]["sources"][1]["count"]))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(bib.ROOT) / "product" / ("题库-v%s.json" % bib.VERSION)))
    ap.add_argument("--check", action="store_true", help="只校验不写文件")
    args = ap.parse_args()
    doc, d = build()
    bad = check(doc)
    c = doc["meta"]["counts"]
    print("schema %s ｜ 共 %d 条（问答 %d + 代码 %d）｜ 主题 %d 个 ｜ 外部题 %d ｜ 自检：%s"
          % (SCHEMA, c["total"], c["scene"], c["code"], c["themes"],
             doc["meta"]["sources"][1]["count"], doc["meta"]["audit"] or "没过"))
    if bad:
        print("校验没过：")
        for b in bad[:12]:
            print("   ✗ %s" % b)
        return 1
    if args.check:
        print("校验通过（--check 未写文件）")
        return 0
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写出：%s（%.1f KB）" % (args.out, Path(args.out).stat().st_size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
