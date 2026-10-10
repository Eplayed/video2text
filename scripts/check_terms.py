#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_terms.py — 魔兽题材稿子/图文的实体实名核对（对照 wow-forever 名单）

用法：
    python3 scripts/check_terms.py output/文章/xxx.md --sources output/选题包/xxx-证据原文.md
    python3 scripts/check_terms.py output/toutiao/69            # 图文卡稿目录也行
    python3 scripts/check_terms.py --fix output/文章/xxx.md     # 只把改过的旧名换成现名

名单没同步时本项跳过并提示先跑 tools/sync_wow_icons.py——不静默当成"通过"。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import term_check  # noqa: E402


def _read_target(path):
    """长文稿 .md，或图文包目录/manifest.json：拼成一份待查文本。"""
    if os.path.isdir(path):
        path = os.path.join(path, "manifest.json")
    if path.endswith(".json"):
        m = json.load(open(path, encoding="utf-8"))
        parts = [m.get("title") or "", m.get("copy_text") or ""]
        for c in m.get("cards") or []:
            parts += [str(c.get(k) or "") for k in ("title", "subtitle", "section", "timeline")]
            for it in (c.get("items") or []) + (c.get("hooks") or []):
                parts += [str(it.get(k) or "") for k in ("name", "desc", "t", "d")]
        return "\n".join(p for p in parts if p), "图文卡稿"
    return open(path, encoding="utf-8").read(), "长文稿"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?")
    ap.add_argument("--sources", help="素材原文路径，用来查「素材有、稿里没写」的实名")
    ap.add_argument("--fix", action="store_true", help="把名单里标了改名的旧名就地换成现名")
    ap.add_argument("--json", action="store_true", help="输出 JSON（给别的脚本消费）")
    a = ap.parse_args()
    if not a.target:
        ap.print_help()
        return 2

    text, kind = _read_target(a.target)
    if a.fix:
        if not a.target.endswith(".md") or a.target.endswith(".json"):
            print("--fix 只接 .md 文件路径", file=sys.stderr)
            return 2
        new, changed = term_check.apply_names(open(a.target, encoding="utf-8").read())
        if not changed:
            print("没有命中「改过的旧名」，文件未改动。")
            return 0
        open(a.target, "w", encoding="utf-8").write(new)
        print("已换 %d 处：%s" % (len(changed), "、".join("%s→%s" % c for c in changed)))
        return 0

    src = open(a.sources, encoding="utf-8").read() if a.sources else None
    rep = term_check.check(text, source_text=src)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("━━━━━━ 实体实名核对 · %s（%s）━━━━━━" % (a.target, kind))
        if not rep["rows"]:
            print("  [INFO] 名单没命中任何条目——要么稿子不涉及游戏实体，要么名单没同步")
        for lv, nm, dt in rep["rows"]:
            print("  [%-5s] %-16s %s" % (lv, nm, dt))
        bad = sum(1 for lv, _, _ in rep["rows"] if lv in ("FAIL", "WARN"))
        print("━━━━━━ 判定：%s ━━━━━━" % ("有要改的" if bad else "实名这块没问题"))
        # 和发布体检口径不同这件事必须自己说清楚，否则下次看到"CLI 报 3 处、体检却通过"会以为漏检：
        # 这里查全文（含【原话摘录】【风险核查】等编辑节），draft_audit 的实名门只查读者会看到的正文。
        print("说明：本命令查全文；发布门（draft_audit._term_gate）会先剥掉"
              "【原话摘录】【口径分歧】【风险核查】等编辑节，两边计数可以不同。")
    return 1 if any(lv == "FAIL" for lv, _, _ in rep["rows"]) else 0


if __name__ == "__main__":
    sys.exit(main())
