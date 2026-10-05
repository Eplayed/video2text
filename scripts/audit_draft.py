#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_draft.py — 发布前统一体检入口（头条长文稿 / 头条图文卡稿两种输入）

为什么要包一层：两个现成质检器各有盲区，实测同一个产物结论互相打架——
  · verify_toutiao.js 报正文 866 字、禁用句式 2 处；
  · ai_score.py 却给 7.9/100「真人味」，因为它把发布元数据和图池当正文一起算了。
机器说没问题、人一眼看出是 AI，缺的就是这一层：先切出真正文，再补三项
两个脚本都不做的判断（中间产物泄漏、有没有叙述者、数字有没有出处）。

用法：
  python3 scripts/audit_draft.py <生成稿.md>                     # 长文稿
  python3 scripts/audit_draft.py --manifest output/toutiao/65    # 图文卡稿（目录或 manifest.json）
  python3 scripts/audit_draft.py <生成稿.md> --sources <整合稿.md>  # 附带数字出处核对

退出码：0=可以发  1=改完再发  2=不能发（或输入有问题）
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACTORY = os.path.expanduser("~/Documents/自媒体/_content_factory")
VERIFY_JS = os.path.join(FACTORY, "verify_toutiao.js")
AI_SCORE = os.path.join(FACTORY, "wechat-publisher", "scripts", "ai_score.py")

# 给机器看的中间产物：这些出现在正文里就是「乱七八糟」的直接来源
LEAK_PATTERNS = [
    (r"图\s*\d+\s*[：:]", "图N：占位说明"),
    (r"画面说明", "画面说明"),
    (r"配图(?:任务|候选|链接池)", "配图任务指令"),
    (r"发布状态[：:]", "流水线自检结论"),
    (r"={3}[A-Z_]+={3}", "内部标记行"),
    (r"━{3,}", "分区标记线"),
    (r"【正文】", "结构标签"),
    (r"视频\s*\d+", "来源视频编号"),
    (r"整合稿", "内部文件名"),
    (r"ASR|转写", "转写口径术语"),
    (r"口播(?:未|中|说)", "口播字样"),
    (r"\*\*增量[：:]", "内部小标题前缀"),
]
# 成品里不该出现的编辑向交代（注意：「信息来源：多位UP主…」是要求写的，不算泄漏，
# 只有「来源：视频1」这种内部编号才算）
EDITORIAL_VOICES = [r"待核实", r"需自行实测", r"人工核对", r"请人工", r"素材(?:未提|缺口)",
                    r"未明确", r"疑似", r"来源[:：]\s*(?:视频|整合稿|\[)"]
NARRATOR = r"我(?:把|去|先|又|试|跑|核|算|花|打|翻|看|测|在)"


def run(cmd):
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             cwd=FACTORY if cmd[0] == "node" else None)
        out, _ = p.communicate(timeout=120)
        return p.returncode, out.decode("utf-8", "replace")
    except Exception as e:
        return 99, "调用失败：%s（%s）" % (cmd[0], e)


def extract_body(md):
    """切出真正文：【正文】之后到下一条分区线为止。"""
    i = md.find("【正文】")
    if i < 0:
        return md
    rest = md[i + len("【正文】"):]
    m = re.search(r"\n\s*━{3,}", rest)
    return (rest[:m.start()] if m else rest).strip()


def audit_prose(text, tag, rows, narrator_text=None, narrator_level="FAIL"):
    for pat, name in LEAK_PATTERNS:
        hits = re.findall(pat, text)
        if hits:
            rows.append(("FAIL", tag + "泄漏", "%s ×%d（%s）" % (name, len(hits), hits[0][:24])))
    for pat in EDITORIAL_VOICES:
        hits = re.findall(pat, text)
        if hits:
            rows.append(("WARN", tag + "编辑向交代",
                         "命中「%s」×%d——成品不能交代素材缺口" % (hits[0][:16], len(hits))))
    probe = narrator_text if narrator_text is not None else text
    placeholders = re.findall(r"\[插图\s*\d+\]", text)
    if placeholders:
        rows.append(("WARN", tag + "插图占位未删",
                     "残留 %s——贴进发布页前要把占位删掉或换成真图" % "、".join(placeholders[:5])))
    if not re.search(NARRATOR, probe) and "我" not in probe:
        rows.append((narrator_level, tag + "叙述者缺席",
                     "没有一个带具体动作的「我」，只剩通稿腔——这就是「一眼 AI 写的」主因"))
    nums = set(re.findall(r"(?<![\d.])\d{2,4}(?![\d.%])", text))
    return nums


def cross_check(nums, sources_path, rows):
    try:
        src = open(sources_path, encoding="utf-8").read()
    except Exception as e:
        rows.append(("WARN", "数字出处", "读不到素材文件：%s" % e))
        return
    src_nums = set(re.findall(r"(?<![\d.])\d{2,4}(?![\d.%])", src))
    ghost = sorted(n for n in nums if n not in src_nums and not (1900 <= int(n) <= 2099))
    if ghost:
        rows.append(("WARN", "数字出处",
                     "正文有 %d 个数字在素材里找不到：%s——要么补出处要么删"
                     % (len(ghost), "、".join(ghost[:8]))))
    else:
        rows.append(("PASS", "数字出处", "正文数字都能在素材里对上"))


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

    rows = []
    prose = ""
    copy_text = ""
    if args.draft:
        md = open(args.draft, encoding="utf-8").read()
        prose = extract_body(md)
        if args.no_verify:
            rows.append(("INFO", "机检", "按 --no-verify 跳过（调度器已单独跑过 verify_toutiao.js）"))
        elif os.path.exists(VERIFY_JS):
            code, out = run(["node", VERIFY_JS, args.draft])
            for line in out.splitlines():
                m = re.search(r"\[(PASS|WARN|FAIL)\]\s*(.+?)\s*—\s*(.*)", line)
                if m:
                    rows.append((m.group(1), "机检·" + m.group(2).strip(), m.group(3).strip()))
                elif "质检结论" in line:
                    rows.append(("INFO", "机检·总结论", line.strip().lstrip("❌✅⚠ ")))
            if code == 99:
                rows.append(("WARN", "机检", out))
        else:
            rows.append(("WARN", "机检", "找不到 verify_toutiao.js（生产层仓库没在预期位置）"))
    else:
        path = args.manifest
        if os.path.isdir(path):
            path = os.path.join(path, "manifest.json")
        data = json.load(open(path, encoding="utf-8"))
        parts = []
        for card in data.get("cards") or []:
            parts.append(card.get("title") or "")
            parts.append(card.get("subtitle") or "")
            for h in card.get("hooks") or []:
                parts.append("%s %s" % (h.get("t", ""), h.get("d", "")))
            for it in card.get("items") or []:
                parts.append("%s %s" % (it.get("name", ""), it.get("desc", "")))
        copy_text = data.get("copy_text") or ""
        prose = "\n".join(parts) + "\n" + copy_text
        rows.append(("INFO", "输入", "图文卡稿 %s：%d 张卡，copy_text %d 字"
                     % (path, len(data.get("cards") or []), len(copy_text))))
        if copy_text and "AI辅助" not in copy_text:
            rows.append(("WARN", "AI 声明", "copy_text 末尾没有「本文图为AI辅助生成」，发布时要手动勾 AI 辅助声明"))
        if copy_text and "信息来源" not in copy_text and "up主" not in copy_text and "UP主" not in copy_text:
            rows.append(("WARN", "来源标注", "copy_text 没有交代信息来源，容易被当搬运"))

    # 卡片是短句清单，「我」只能出现在 copy_text 里，所以卡稿模式下叙述者只查文案、降级为建议
    nums = audit_prose(prose, "正文", rows,
                       narrator_text=copy_text if args.manifest else None,
                       narrator_level="WARN" if args.manifest else "FAIL")
    if args.sources:
        cross_check(nums, args.sources, rows)

    if os.path.exists(AI_SCORE) and len(re.sub(r"\s", "", prose)) >= 200:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        tmp.write(prose)
        tmp.close()
        code, out = run([sys.executable, AI_SCORE, tmp.name])
        os.unlink(tmp.name)
        m = re.search(r"总分[:：]\s*([\d.]+)\s*/\s*100", out)
        if m:
            score = float(m.group(1))
            rows.append((("FAIL" if score >= 45 else "WARN" if score >= 30 else "PASS"),
                         "AI 味评分", "%.1f/100（只算正文，不含元数据；≥45 判 AI 味过重）" % score))
        for line in out.splitlines():
            mm = re.search(r"\[(burstiness|phrases|vocab|structural|punctuation|style)\s*\]\s*分数=\s*([\d.]+).*?(\d+/\d+字|句长均值.*|.*密度.*)", line)
            if mm and float(mm.group(2)) >= 30:
                rows.append(("INFO", "AI 味·弱项", "%s=%.0f %s" % (mm.group(1), float(mm.group(2)), (mm.group(3) or "")[:40])))

    fails = [r for r in rows if r[0] == "FAIL"]
    warns = [r for r in rows if r[0] == "WARN"]
    print("\n━━━━━━ 发布前体检 · %s ━━━━━━" % (args.draft or args.manifest))
    for lvl, name, detail in rows:
        print("  [%-4s] %-22s %s" % (lvl, name, detail))
    print("━━━━━━ 判定：%s ━━━━━━" % ("不能发（%d 项必须改）" % len(fails) if fails
                                    else ("改完再发（%d 项建议）" % len(warns) if warns else "可以发")))
    for lvl, name, detail in rows:
        if lvl == "FAIL":
            print("  必须改 → %s：%s" % (name, detail))
    return 2 if fails else (1 if warns else 0)


if __name__ == "__main__":
    sys.exit(main())
