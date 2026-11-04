#!/usr/bin/env python3
"""I3 首段：发布字段逐项映射与公众号母稿提示词边界。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import guide_mother  # noqa: E402
from src.graphics.channels import wechat  # noqa: E402


def check(label, ok):
    if not ok:
        raise AssertionError(label)
    print("PASS " + label)


def main():
    mother = {"status": "complete", "source_revision": "source-v1", "mother_revision": "mother-v1",
              "core_answer": "两步拿到点数", "points": [
                  {"point_id": "p1", "quote": "大号先拿1点", "display_text": "大号先拿1点",
                   "priority": "must", "subject": "大号", "conditions": [], "modality": "fact"},
                  {"point_id": "p2", "quote": "小号随后拿1点", "display_text": "小号随后拿1点",
                   "priority": "must", "subject": "小号", "conditions": [], "modality": "fact"}]}
    cards = [{"kind": "cover", "title": "两步拿2点", "point_ids": ["p1"]},
             {"kind": "text", "title": "小号继续拿点", "point_ids": ["p2"],
              "steps": [{"h": "随后", "p": "小号拿1点"}]}]
    plan = guide_mother.wechat_plan(cards, mother)
    fields = {"page_1.title": {"point_ids": ["p1", "p2"]},
              "page_2.title": {"point_ids": ["p2"]},
              "publish.copy_text.1": {"point_ids": ["p1"]},
              "publish.copy_text.2": {"point_ids": ["p2"]},
              "publish.digest.1": {"point_ids": ["p1", "p2"]}}
    pub = {"copy_text": "大号先拿1点。小号随后拿1点。", "digest": "两步拿2点"}
    good = guide_mother.audit_output(mother, plan, pub, {},
                                      checker=lambda *_: {"issues": [], "field_sources": fields})
    check("公众号两张卡共同覆盖两条必保点", good["ready"])
    check("公众号标题和配文的映射单独保留", set(good["coverage"]["field_sources"]) == set(fields))
    dropped = dict(fields)
    dropped.pop("publish.copy_text.2")
    split_bad = guide_mother.audit_output(mother, plan, pub, {},
                                           checker=lambda *_: {"issues": [], "field_sources": dropped})
    check("配文第二句漏来源不能由第一句蒙混", not split_bad["ready"]
          and any(x.get("field") == "publish.copy_text.2" for x in split_bad["coverage"]["issues"]))
    wrong = dict(fields, **{"publish.digest.1": {"point_ids": ["fake"]}})
    bad = guide_mother.audit_output(mother, plan, pub, {},
                                     checker=lambda *_: {"issues": [], "field_sources": wrong})
    check("公众号摘要引用虚构点数时阻断", not bad["ready"]
          and any(x.get("field") == "publish.digest.1" for x in bad["coverage"]["issues"]))
    prompt = wechat._build_cards_prompt({"title": "旧标题", "content": "旧整理稿"}, "", mother=mother)
    check("母稿提示词不带旧整理稿事实", "旧整理稿" not in prompt and "旧标题" not in prompt)
    check("母稿提示词不注入无关免责示例", "工具功能与价格" not in prompt
          and "AI 辅助整理归纳" not in prompt and "网友总结" not in prompt)
    check("公众号保留逐点引用格式", "point_ids" in prompt and "原意母稿" in prompt)
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
