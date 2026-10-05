#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_draft.py — 发布前体检的命令行壳。

规则与判定全在 `src/draft_audit.py`（那份是唯一权威，工作台的发布门禁也调它），
这里只解析参数和打印。自媒体自动线 dify_auto_publish.py 调的就是本文件，
输出格式与退出码是它的契约，别改。

为什么要包一层：两个现成质检器各有盲区，实测同一份产物结论互相打架——
  · verify_toutiao.js 报正文 866 字、禁用句式 2 处；
  · ai_score.py 却给 7.9/100「真人味」，因为它把发布元数据和图池当正文一起算了。
机器说没问题、人一眼看出是 AI，缺的就是这一层：先切出真正文，再补三项
两个脚本都不做的判断（中间产物泄漏、有没有叙述者、数字有没有出处）。

用法：
  python3 scripts/audit_draft.py <生成稿.md>                        # 长文稿
  python3 scripts/audit_draft.py --manifest output/toutiao/65       # 图文卡稿（目录或 manifest.json）
  python3 scripts/audit_draft.py <稿.md> --sources <整合稿.md>      # 附带数字出处核对
  python3 scripts/audit_draft.py <稿.md> --no-verify                # 调度器已单独跑过机检时用

退出码：0=可以发  1=改完再发  2=不能发（或输入读不到）
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import draft_audit  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("draft", nargs="?", help="长文稿 .md 路径")
    ap.add_argument("--manifest", help="图文卡稿目录或 manifest.json（output/toutiao/65）")
    ap.add_argument("--sources", help="整合稿/素材原文路径，用于数字出处核对")
    ap.add_argument("--no-verify", action="store_true",
                    help="跳过 verify_toutiao.js（调度器已经跑过它，这里只补它查不到的三项）")
    args = ap.parse_args()
    if not args.draft and not args.manifest:
        ap.error("必须给 .md 路径或 --manifest")

    sources_text = None
    if args.sources:
        try:
            with open(args.sources, encoding="utf-8") as fh:
                sources_text = fh.read()
        except OSError as e:
            print("读不到素材文件：%s" % e)
            return 2

    if args.manifest:
        target = args.manifest
        result = draft_audit.audit(manifest=draft_audit.load_manifest(args.manifest),
                                   sources_text=sources_text)
    else:
        target = args.draft
        with open(args.draft, encoding="utf-8") as fh:
            md = fh.read()
        result = draft_audit.audit(md_text=md, sources_text=sources_text, draft_path=args.draft,
                                   use_verify=not args.no_verify)
        if args.no_verify:
            result["rows"].insert(0, ("INFO", "机检",
                                      "按 --no-verify 跳过（调度器已单独跑过 verify_toutiao.js）"))

    verdict, fails, warns = result["verdict"], result["fails"], result["warns"]
    tail = "不能发（%d 项必须改）" % fails if verdict == "fail" \
        else "改完再发（%d 项建议）" % warns if verdict == "warn" else "可以发"
    print("\n━━━━━━ 发布前体检 · %s ━━━━━━" % target)
    for level, name, detail in result["rows"]:
        print("  [%-4s] %-22s %s" % (level, name, detail))
    print("━━━━━━ 判定：%s ━━━━━━" % tail)
    for level, name, detail in result["rows"]:
        if level == "FAIL":
            print("  必须改 → %s：%s" % (name, detail))
    return {"pass": 0, "warn": 1, "fail": 2}[verdict]


if __name__ == "__main__":
    sys.exit(main())
