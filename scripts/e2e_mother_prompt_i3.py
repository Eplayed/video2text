#!/usr/bin/env python3
"""隔离验证原意母稿模式真实发送的拆稿提示词不带旧强制结构。"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import ai_poster  # noqa: E402


class Captured(Exception):
    pass


def main():
    calls = []

    def create(**kwargs):
        calls.append(kwargs["messages"][0]["content"])
        raise Captured()

    fake = types.SimpleNamespace(OpenAI=lambda **_kw: types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))))
    old = sys.modules.get("openai")
    old_names = ai_poster._names_hint
    roster_inputs = []
    ai_poster._names_hint = lambda summary, source_text="": (
        roster_inputs.append(summary), "只用原文专名")[1]
    sys.modules["openai"] = fake
    mother = {"status": "complete", "source_revision": "s", "mother_revision": "m",
              "core_answer": "小号能得到奖励", "points": [{
                  "point_id": "p1", "quote": "小号能得到奖励", "display_text": "小号能得到奖励",
                  "priority": "must", "subject": "小号", "conditions": [], "modality": "fact"}]}
    summary = {"id": 1, "title": "旧整理稿标题", "content": "旧整理稿不作为事实源"}
    cfg = {"api_key": "stub", "model": "stub"}
    try:
        try:
            ai_poster.build_text_plan(summary, cfg, mother=mother)
        except Captured:
            pass
        assert len(calls) == 1
        plan = calls.pop()
        assert "小号能得到奖励" in plan and "point_id" in plan
        for bad in ("每页**卡片 3-5 张", "砍掉单纯罗列", "团本大概率推迟 2 到 3 个 CD",
                    "这一条**该画成什么**", "结构是否成链", "熔岩城墙头"):
            assert bad not in plan, bad
        assert "卡片数量由内容决定" in plan and "不能删必保点" in plan
        assert "不能为了去掉过程词而把传闻写成确定事实" in plan
        assert "卡片建议 2-3 条" not in plan
        frame_rules = ai_poster.guide_rules_text(
            [{"i": 1, "ts": "00:01", "subject": "任务面板", "near": "先领取任务"}],
            180, ai_poster.MOTHER_META_RULE)
        assert "卡片数量由必保点和可用空间决定" in frame_rules
        assert "卡片建议 2-3 条" not in frame_rules
        assert roster_inputs == [{"content": ""}]
        print("PASS 整组母稿提示词保留并列和必保点，无旧卡数强迫或虚构示例")

        pages = [{"title": "小号奖励", "role": "清单", "cards": [
            {"t": "小号", "d": "能得到奖励", "point_ids": ["p1"]}]}]
        try:
            ai_poster.build_page_plan(summary, cfg, 1, pages, mother=mother)
        except Captured:
            pass
        assert len(calls) == 1
        page = calls.pop()
        for bad in ("这一页**卡片 3-5 张", "卡片之间要构成递进", "两个字，如 收藏",
                    "团本大概率推迟 2 到 3 个 CD"):
            assert bad not in page, bad
        assert "清单保留并列" in page and "必保点不可删" in page
        print("PASS 单页母稿提示词允许并列清单和按内容定卡数")

        legacy = ai_poster.page_plan_prompt(1, pages, "主线", "guide_ink", False, "主题",
                                            ai_poster.limits_for(ai_poster.DEFAULT_TEXT_MODE))
        assert "卡片 3-5 张" in legacy
        assert "素材没说的写在这儿" not in ai_poster._GUIDE_RULES
        print("PASS 旧模式结构不被本次改动重写；提醒示例不再诱导编造")
    finally:
        ai_poster._names_hint = old_names
        if old is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = old
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
