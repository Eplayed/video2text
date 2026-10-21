#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从已生成的头条攻略海报制作人工发布包；缺审稿记录时只给草稿清单。"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src import fact_gate


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _page_files(manifest):
    pages = manifest.get("pages") or []
    if not isinstance(pages, list):
        return []
    return [p.get("file") for p in pages if isinstance(p, dict)]


def template(manifest):
    risks = (manifest.get("fact_gate") or {}).get("risk_items") or []
    return {
        "reviewer": "",
        "reviewed_at": datetime.now().strftime("%Y-%m-%d"),
        "player_question": "",
        "own_contribution": "",
        "text_checked": False,
        "copy_checked": False,
        "source_and_version_checked": False,
        "risk_decisions": [{"risk": str(r), "decision": "", "evidence_or_change": ""}
                           for r in risks],
        "soft_decisions": [{"warning": str(w), "resolution": ""}
                           for w in ((manifest.get("fact_gate") or {}).get("soft") or [])],
        "lint_decisions": [{"warning": str(w), "resolution": ""}
                           for w in (manifest.get("lint") or [])
                           if str(w).startswith("warn:")],
        "pages": [{"file": name, "text_readable": False,
                   "image_explains": "", "image_rights_basis": ""}
                  for name in _page_files(manifest)],
        "platform": {"channel": "toutiao", "format": "", "ai_label_checked": False,
                     "originality_checked_by_human": False},
        "approved_for_manual_publish": False,
    }


def validate(manifest, review, folder):
    problems = []
    if manifest.get("publish_channel") != "toutiao":
        problems.append("仅支持头条发布包；请重新选择正确渠道生成")
    if manifest.get("publishable") is not True:
        problems.append("原成品 publishable 不是 true；先修原产线列出的阻塞项")
    gate = manifest.get("fact_gate") or {}
    if gate.get("hard"):
        problems.append("事实闸仍有硬阻断；先改稿或在原产线逐条核查")
    if gate.get("guide_gaps"):
        problems.append("攻略五段结构有缺项；先补文字稿或降为资料整理")
    if (manifest.get("photos_unjudged") or []):
        problems.append("有未判关键帧；先在原产线核查或撤图")
    provenance = manifest.get("provenance")
    if isinstance(provenance, dict) and provenance.get("gaps"):
        problems.append("编辑证据链有缺项；先在原产线补齐")
    if not (manifest.get("copy_text") or "").strip():
        problems.append("没有可复制的发布配文")
    elif fact_gate.copy_is_cut(manifest["copy_text"]):
        problems.append("发布配文疑似截断；回到文字稿补全")
    if not review.get("reviewer") or not review.get("reviewed_at"):
        problems.append("缺人工核稿人或日期")
    if not review.get("player_question") or not review.get("own_contribution"):
        problems.append("缺玩家问题或本站独立贡献")
    for field, label in [("text_checked", "完整文字稿"),
                         ("copy_checked", "最终配文"),
                         ("source_and_version_checked", "来源与适用版本")]:
        if review.get(field) is not True:
            problems.append("未人工核对%s" % label)
    expected = (gate.get("risk_items") or [])
    decisions = {x.get("risk"): x for x in review.get("risk_decisions") or []
                 if isinstance(x, dict)}
    for risk in expected:
        item = decisions.get(str(risk)) or {}
        if item.get("decision") not in ("已删除", "已核实", "已限定表述"):
            problems.append("风险未处理：%s" % str(risk)[:80])
        if not item.get("evidence_or_change"):
            problems.append("风险处理缺证据或改动说明：%s" % str(risk)[:80])
    soft_decisions = {x.get("warning"): x for x in review.get("soft_decisions") or []
                      if isinstance(x, dict)}
    for warning in gate.get("soft") or []:
        if not (soft_decisions.get(str(warning)) or {}).get("resolution"):
            problems.append("人工提示未处理：%s" % str(warning)[:80])
    lint_decisions = {x.get("warning"): x for x in review.get("lint_decisions") or []
                      if isinstance(x, dict)}
    for warning in manifest.get("lint") or []:
        if str(warning).startswith("warn:") and not (lint_decisions.get(str(warning)) or {}).get("resolution"):
            problems.append("成品警告未处理：%s" % str(warning)[:80])
    expected_pages = _page_files(manifest)
    if not expected_pages or any(not x or Path(x).name != x for x in expected_pages):
        problems.append("成品页文件名无效或为空")
    page_reviews = {x.get("file"): x for x in review.get("pages") or []
                    if isinstance(x, dict)}
    for name in expected_pages:
        if not name or not (folder / name).is_file():
            problems.append("缺成品页：%s" % name)
            continue
        try:
            with Image.open(folder / name) as image:
                image.verify()
        except Exception:
            problems.append("成品页无法作为图片打开：%s" % name)
        page = page_reviews.get(name) or {}
        if page.get("text_readable") is not True:
            problems.append("未目视核对文字可读：%s" % name)
        if not page.get("image_explains") or not page.get("image_rights_basis"):
            problems.append("未说明画面解释作用或使用依据：%s" % name)
    platform = review.get("platform") or {}
    if platform.get("channel") != "toutiao" or platform.get("format") not in ("文章", "微头条"):
        problems.append("未确认头条体裁（文章/微头条）")
    if platform.get("ai_label_checked") is not True:
        problems.append("未按账号当日发布页确认 AI 内容声明")
    if platform.get("originality_checked_by_human") is not True:
        problems.append("未人工确认原创/首发声明资格；工具不代判")
    if review.get("approved_for_manual_publish") is not True:
        problems.append("尚未人工签字放行")
    return problems


def run(manifest_path, review_path, out_dir):
    folder = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not review_path.exists():
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text(json.dumps(template(manifest), ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print("已创建人工审稿表：%s" % review_path)
        print("未出发布包；先核稿、填表，再运行同一命令。")
        return 1
    review = json.loads(review_path.read_text(encoding="utf-8"))
    problems = validate(manifest, review, folder)
    if problems:
        print("仍是草稿，未出发布包：")
        for item in problems:
            print("- " + item)
        return 2
    if out_dir.exists() and any(out_dir.iterdir()):
        print("发布包目录已有文件，请换一个新目录，避免覆盖旧版：%s" % out_dir)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "copy_text.txt").write_text(manifest["copy_text"], encoding="utf-8")
    for name in _page_files(manifest):
        shutil.copy2(folder / name, out_dir / name)
    receipt = {
        "source_manifest": str(manifest_path.resolve()),
        "manifest_sha256": _hash(manifest_path),
        "review_sha256": _hash(review_path),
        "reviewer": review["reviewer"],
        "reviewed_at": review["reviewed_at"],
        "platform": review["platform"],
        "files": {p.name: _hash(p) for p in sorted(out_dir.iterdir()) if p.is_file()},
    }
    (out_dir / "release_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("人工待发布包：%s" % out_dir)
    print("请按页码上传 PNG、复制 copy_text.txt；平台发布仍由本人手动完成。")
    return 0


def main():
    parser = argparse.ArgumentParser(description="制作头条攻略人工待发布包")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if not args.manifest.is_file():
        parser.error("找不到 manifest.json")
    return run(args.manifest, args.review, args.out)


if __name__ == "__main__":
    sys.exit(main())
