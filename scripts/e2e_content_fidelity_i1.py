#!/usr/bin/env python3
"""图文攻略 I1 离线回归：原文保留、容量阻断和配图不乱补。"""
import sys
import types
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import ai_poster, poster_typeset  # noqa: E402
from src.graphics.channels import wechat  # noqa: E402
from src.graphics.gate import GraphicsGateError  # noqa: E402


def check(name, ok):
    if not ok:
        raise AssertionError(name)
    print("PASS %s" % name)


def main():
    lim = ai_poster.limits_for("typeset")
    condition = "只有猎人携带宠物且宠物参与输出时才可能提高收益；该说法只适用于升级阶段，副本环境仍须看队伍配置；未带宠物时不成立。"
    original = {"title": "猎人升级", "subtitle": "单刷路线", "footer": "来源：公开整理",
                "cards": [{"t": "适用条件", "d": condition},
                          {"t": "例外", "d": "副本组队需要另看队伍配置。"}]}
    fitted, notes = ai_poster.fit_plan(original, limit=30, lim=lim)
    check("超预算不删末尾条件", len(fitted["cards"]) == 2
          and fitted["cards"][0]["d"] == condition
          and any("拆页" in n for n in notes))

    page, _ = ai_poster.normalize_page(
        {"title": "猎人升级", "cards": [{"t": "适用条件", "d": condition}]},
        lim, "guide_ink", False, 1)
    check("单字段超限保留原句并报具体位置",
          page["cards"][0]["d"] == condition
          and any("第 1 张卡片说明" in n for n in ai_poster.lint_plan(page, lim)))

    cards = [{"t": "要点%d" % i, "d": "条件%d" % i} for i in range(ai_poster.CARD_MAX + 1)]
    one = {"title": "完整清单", "cards": cards}
    check("单页卡片超限不截尾", len(ai_poster.plan_pages(one)[0]["cards"]) == len(cards)
          and any("卡片" in n and "超过" in n for n in ai_poster.lint_plan(one, lim)))
    many = {"pages": [{"title": "第%d页" % i, "cards": cards[:1]}
                      for i in range(ai_poster.PAGE_MAX + 1)]}
    check("整组页数超限不截末页", len(ai_poster.plan_pages(many)) == ai_poster.PAGE_MAX + 1
          and any("页数" in n for n in ai_poster.lint_plan(many, lim)))

    fake = types.ModuleType("src.frame_pool")
    fake.caption_for = lambda item: item.get("subject") or ""
    prior = sys.modules.get("src.frame_pool")
    sys.modules["src.frame_pool"] = fake
    try:
        pages = [{"title": "第一步", "photos": [{"frame": "F1"}]},
                 {"title": "第二步", "photos": [{"frame": "F2"}]},
                 {"title": "没有相关画面的说明"}]
        frames = [{"i": 1, "ts": "00:03", "file": "/tmp/one.png", "subject": "打开菜单"},
                  {"i": 2, "ts": "00:06", "file": "/tmp/two.png", "subject": "点击按钮"},
                  {"i": 3, "ts": "00:30", "file": "/tmp/other.png", "subject": "无关画面"}]
        ai_poster.bind_frames(pages, frames, lim)
        check("相邻时刻不同操作帧保留", pages[0]["photos"][0]["frame"] == "F1"
              and pages[1]["photos"][0]["frame"] == "F2")
        check("无相关图保留文字页", not pages[2].get("photos"))
    finally:
        if prior is None:
            sys.modules.pop("src.frame_pool", None)
        else:
            sys.modules["src.frame_pool"] = prior

    _, render_check = poster_typeset.typeset_guide(
        None, {"title": "排版检测", "cards": [{"t": "一条", "d": "条件" * 180}],
               "style_key": "guide_ink"})
    check("渲染截字反馈失败", not render_check["ok"]
          and any("未完整上版" in n or "超出版面" in n for n in render_check["problems"]))
    prior_placeholder = poster_typeset._art_placeholder
    try:
        poster_typeset._art_placeholder = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("文字攻略页不应出现 AI 插画占位"))
        _, preview_check = ai_poster.preview_typeset(
            {"title": "无图步骤", "layout_key": "guide", "style_key": "guide_ink",
             "cards": [{"t": "起手", "d": "先完成任务。"}]})
        check("无图攻略预览不渲染 AI 插画占位", preview_check["ok"])
    finally:
        poster_typeset._art_placeholder = prior_placeholder

    old_output, old_cards = wechat.OUTPUT_DIR, wechat._llm_cards
    with tempfile.TemporaryDirectory(prefix="v2t_i1_wechat_") as root:
        try:
            wechat.OUTPUT_DIR = Path(root)
            wechat._llm_cards = lambda *args, **kwargs: {
                "cards": [{"kind": "list", "title": "第%d页" % i,
                           "items": [{"name": "信息", "desc": "条件完整"}]} for i in range(21)],
                "copy_text": "核心信息与适用条件"}
            try:
                wechat.generate_graphics(
                    {"id": 123456, "title": "容量测试", "status": "ai", "content": "完整内容"},
                    {"model": "test"})
            except GraphicsGateError as exc:
                check("公众号超过 20 张明确失败且不落成品", "21 张" in str(exc)
                      and not (Path(root) / "123456" / "manifest.json").exists())
            else:
                raise AssertionError("公众号超量未阻断")
        finally:
            wechat.OUTPUT_DIR, wechat._llm_cards = old_output, old_cards
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
