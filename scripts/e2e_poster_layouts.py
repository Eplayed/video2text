# -*- coding: utf-8 -*-
"""海报「布局轴 + 多页」回归：十种构图、页数归一、回落判据、成组落盘、采纳率算式。

为什么单独一条线：布局与页数都是**代码算版面**，不像图文线那样能从 CSS 差异里看出来，
静默失效的表现是"换了布局图却没变"或"多页只出一页"。这里全部按几何与结构断言，
不比对 PNG 字节（那会依赖本机字体），也不调任何付费接口（真图模式 0 元）。

用法：python3 scripts/e2e_poster_layouts.py
末行 ALL_OK 才算通过；产物写 output/poster/99996/ 并在结束时删除。
"""
import io
import json
import re
import shutil
import sqlite3
import sys
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import ai_poster, poster_typeset as ts  # noqa: E402

W, H = 936, 1664
RATIO = "9:16"
FAILS = []
TEST_ID = 99996
ENV = ROOT / "config" / "config.env.local"
DB = ROOT / "output" / "video2text.db"
PHOTO = ROOT / "output" / "poster" / "63" / "photo_src.png"


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + detail) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + detail) if detail else ""))
        FAILS.append(name)


def plan(cards=4, style="game_epic", layout=None, long_desc=False):
    descs = ["幽灵军马骸骨（绝版）、怒缚乌祖尔（换色）、无头骑士军马（节日补票）",
             "绘纹系列多款面具，刺客/骑士等套装风格",
             "每月刷新后可能绝版，建议优先兑换限时坐骑",
             "打开商栈界面（Shift+J），完成旅行者日志任务",
             "整套幻化按喜好挑，面具不限量随时能补"]
    return {"title": "魔兽10月商栈：绝版坐骑幻化", "subtitle": "限时兑换，面具套装同步",
            "footer": "内容整理自公开分享", "style_key": style, "layout_key": layout or "",
            "scene": "商栈界面打开，坐骑图标闪烁光芒",
            "cards": [{"t": ["三只坐骑回归", "头部幻化面具", "优先兑换坐骑", "获取商栈币", "套装幻化"][i % 5],
                       "d": descs[i % 5] if long_desc else descs[i % 5][:14],
                       "v": "道具特写"} for i in range(cards)]}


def render(p, photo=None):
    zone = ts.art_zone(p, (W, H))
    base = ts.placeholder_base(p.get("style_key"), W, H, zone["frac"],
                               art_hint=False, plan=p, zone=zone)
    png, chk = ts.typeset(base, p, ratio=RATIO, photo=photo)
    return png, chk, zone


def main():
    print("== A 十种构图的版面指纹 ==")
    fp = {}
    for key in ts.LAYOUT_ORDER:
        p = plan(layout=key)
        if key == "full_bleed":
            p["cards"] = p["cards"][:2]
        if key in ("big_type", "bottom_art", "left_text", "right_text"):
            p["cards"] = p["cards"][:3]
        png, chk, zone = render(p)
        boxes, top = ts._card_boxes(p["cards"], W, H, int(W * 0.062), W - 2 * int(W * 0.062),
                                    int(W * ts.skin_for(p["style_key"])["gap"]),
                                    H - int(H * 0.024) - int(H * 0.042),
                                    ts.skin_for(p["style_key"]), layout=key,
                                    text_x0=zone.get("text_x0") or int(W * 0.062),
                                    top_limit=int(H * 0.14))
        sig = (chk["layout"], zone["slot"], top, boxes[-1]["y1"],
               tuple(sorted({(b["x0"], b["x1"]) for b in boxes})))
        fp[key] = (sig, hash(png))
        ck("%s 的 art 槽位=%s" % (key, zone["slot"]),
           zone["slot"] == ts.LAYOUTS[key]["art"], "卡片区 %d..%d" % (top, boxes[-1]["y1"]))
    sigs = [v[0] for v in fp.values()]
    ck("十种构图版面指纹互不相同", len(set(sigs)) == len(sigs),
       "重复: %s" % [k for k, v in fp.items() if sigs.count(v[0]) > 1] if len(set(sigs)) != len(sigs) else "")
    imgs = [v[1] for v in fp.values()]
    ck("十种构图渲染结果互不相同（不是只换颜色）", len(set(imgs)) == len(imgs))

    print("== B 默认路径零变化（皮肤自带布局仍然是它自己） ==")
    expect = {"game_epic": "top_art", "tool_review": "grid2",
              "checklist": "list", "minimal": "hero"}
    for skin, want in expect.items():
        got = ts.resolve_layout(None, ts.TYPE_SKINS[skin])
        ck("%s 默认布局仍是 %s" % (skin, want), got == want, "实得 %s" % got)
    ck("历史键 stack 仍然认（用户模板 JSON 里就是它）",
       ts.resolve_layout({"layout_key": "stack"}, None) == "top_art")

    print("== C 卡片数超出该布局承载要说明白 ==")
    _png, chk, _z = render(plan(cards=4, layout="full_bleed"))
    ck("满版大图塞 4 条 → 提示最多 2 条并指路",
       any("最多放 2 条" in x and "拆到下一页" in x for x in chk["problems"]),
       "；".join(chk["problems"]))
    _png, chk, _z = render(plan(cards=1, layout="compare"))
    ck("双列对比只给 1 条 → 提示至少 2 条", any("至少要 2 条" in x for x in chk["problems"]),
       "；".join(chk["problems"]))

    print("== D 布局回落判据 ==")
    lay, note = ai_poster._page_layout("full_bleed", "game_epic", False, 1)
    ck("没真图时满版大图回落到图上文下", lay == "top_art" and "要有图" in note, note)
    lay, note = ai_poster._page_layout("不存在的键", "checklist", False, 2)
    ck("非法布局键回落到该风格默认", lay == "list", "实得 %s / %s" % (lay, note))
    lay, _n = ai_poster._page_layout("left_text", "game_epic", True, 1)
    ck("有真图时左右分栏照用", lay == "left_text")

    print("== E 页数归一 ==")
    one = plan()
    ck("没有 pages 字段＝单页", len(ai_poster.plan_pages(one)) == 1)
    hybrid = dict(one, pages=[{"title": "A", "cards": one["cards"][:2]},
                              {"title": "", "cards": []},
                              {"title": "C", "cards": one["cards"][:2]}])
    ck("空页丢掉、其余保留", len(ai_poster.plan_pages(hybrid)) == 2)
    many = dict(one, pages=[{"title": "P%d" % i, "cards": one["cards"][:2]} for i in range(6)])
    ck("超过 4 页被截断", len(ai_poster.plan_pages(many)) == ai_poster.PAGE_MAX)
    ck("每页没写 footer 时继承顶层那一句",
       (ai_poster.plan_pages(hybrid)[0].get("footer") or "") == one["footer"])

    print("== F 门禁跟着页走 ==")
    lim = ai_poster.limits_for("typeset")
    two = {"style_key": "game_epic", "pages": [
        {"title": "第一页", "cards": [{"t": "甲", "d": "说明"}]},
        {"title": "第二页", "cards": [{"t": "乙", "d": "说明"}, {"t": "丙", "d": "说明"},
                                       {"t": "丁", "d": "说明"}]}]}
    ck("末页只有 1 条卡片不算不合格", ai_poster.lint_plan(two, lim) == [],
       str(ai_poster.lint_plan(two, lim)))
    over = {"style_key": "game_epic", "pages": [
        {"title": "正常的一页", "cards": [{"t": "甲", "d": "说明甲"}, {"t": "乙", "d": "说明乙"},
                                          {"t": "丙", "d": "说明丙"}]},
        {"title": "超预算的一页", "cards": [{"t": "标题标题", "d": "说明" * 20} for _ in range(5)]}]}
    ck("单页超 200 字仍然被拦并指明是哪一页",
       any("第 2 页" in x and "超过上限" in x for x in ai_poster.lint_plan(over, lim)),
       "；".join(ai_poster.lint_plan(over, lim)))
    only1 = {"pages": [{"title": "只有一页", "cards": [{"t": "甲", "d": "乙"}, {"t": "丙", "d": "丁"},
                                                       {"t": "戊", "d": "己"}]}]}
    ck("pages 只有一页时按那一页查内容（顶层空壳不误报）",
       ai_poster.lint_plan(only1, lim) == [], str(ai_poster.lint_plan(only1, lim)))
    ck("整组字数＝各页之和",
       ai_poster.plan_chars(two) == sum(ai_poster._plan_chars_one(p) for p in two["pages"]))

    print("== G 页码角标 ==")
    p1 = plan(cards=2, layout="top_art")
    # 同一页内容，只换页码：图必须不同（角标画的是 1/2 还是 2/2），版面必须一致
    base = ts.placeholder_base(p1["style_key"], W, H, 0.6, art_hint=False, plan=p1)
    i1, k1 = ts.typeset(base, p1, RATIO, page=(1, 2))
    i2, k2 = ts.typeset(base, p1, RATIO, page=(2, 2))
    ck("页码进了自检", k1.get("page") == [1, 2] and k2.get("page") == [2, 2])
    ck("只换页码则图不同、版面相同",
       i1 != i2 and [x for x in [k1["cards"], k1["layout"]]] == [x for x in [k2["cards"], k2["layout"]]])
    ck("单页时不画页码", "page" not in ts.typeset(base, p1, RATIO)[1])

    print("== H 三页成组落盘（真图模式，0 元） ==")
    if not PHOTO.exists():
        print("  [SKIP] 没有测试用真图，跳过多页出图")
    else:
        conn = sqlite3.connect(str(DB))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM ai_summaries WHERE id=63").fetchone()
        conn.close()
        if not row:
            print("  [SKIP] 库里没有整合稿 63")
        else:
            summary = dict(row)
            summary["id"] = TEST_ID
            pages = {"style_key": "game_epic", "footer": "内容整理自公开分享",
                     "recommended": {"page_count": 3, "style_key": "game_epic",
                                     "layouts": ["full_bleed", "top_art", "list"]},
                     "why": "测试用推荐",
                     "pages": [
                         {"role": "封面", "layout_key": "full_bleed", "title": "10月商栈",
                          "subtitle": "绝版坐骑回来了",
                          "cards": [{"t": "幽灵军马骸骨", "d": "卡牌坐骑之前绝版"},
                                    {"t": "怒缚乌祖尔", "d": "换色版这次再上架"}]},
                         {"role": "收藏", "layout_key": "top_art", "title": "能收的三样",
                          "cards": [{"t": "无头骑士军马", "d": "万圣节的错过能补票"},
                                    {"t": "绘纹面具多款", "d": "头部幻化按月轮换"},
                                    {"t": "骑士刺客套装", "d": "整套幻化按喜好挑"}]},
                         {"role": "行动", "layout_key": "list", "title": "先换哪个",
                          "cards": [{"t": "先看余额", "d": "不够就做旅行者日志"},
                                    {"t": "绝版优先", "d": "每月刷新后可能下架"},
                                    {"t": "面具随意", "d": "不限量回头再收也行"}]}]}
            out = ROOT / "output" / "poster" / str(TEST_ID)
            if out.exists():
                shutil.rmtree(out)
            m = ai_poster.generate_poster(summary, {"method": "openai", "api_key": "x",
                                                    "api_base": "http://127.0.0.1:1"},
                                          plan=pages, ratio=RATIO, copies=3,
                                          do_review=True, text_mode="typeset",
                                          photo=PHOTO.read_bytes(), preview_clicks=2)
            ck("三页都落盘", all((out / ("poster_p%d.png" % i)).exists() for i in (1, 2, 3)))
            ck("第 1 页同时复制成 poster.png",
               (out / "poster.png").read_bytes() == (out / "poster_p1.png").read_bytes())
            ck("多页时强制一页一张（不再抽卡，免得花费翻倍）",
               m["copies"] == 1 and m["page_count"] == 3)
            ck("整组 0 元（真图模式不出 AI 底图）", m["cost_yuan_estimate"] == 0)
            ck("每页各自记版面与字数",
               [p.get("layout") for p in m["pages"]] == ["full_bleed", "top_art", "list"]
               and all(p.get("chars") for p in m["pages"]),
               str([(p["page"], p["layout"], p["chars"]) for p in m["pages"]]))
            ck("整理稿里的 8 个要点一条不丢",
               sum(p["cards"] for p in m["pages"]) == 8)
            ck("采纳率原料落盘（recommended / final / preview_clicks）",
               (m.get("recommended") or {}).get("page_count") == 3
               and (m.get("final") or {}).get("page_count") == 3
               and m.get("preview_clicks") == 2,
               "final=%s" % m.get("final"))
            st = ai_poster.adoption_stats([(m["recommended"], m["final"], m)])
            ck("采纳率算式：全照用 = 100%",
               st["page"]["rate"] == 100 and st["layout"]["rate"] == 100
               and st["avg_preview_clicks"] == 2.0, json.dumps(st, ensure_ascii=False)[:120])
            st2 = ai_poster.adoption_stats([({"page_count": 3, "style_key": "game_epic",
                                             "layouts": ["full_bleed", "list"]},
                                            {"page_count": 2, "style_key": "game_epic",
                                             "layouts": ["top_art", "list"]},
                                            {"preview_clicks": 4})])
            ck("采纳率算式：改页数/改布局能被算出来",
               st2["page"]["rate"] == 0 and st2["layout"]["rate"] == 50
               and st2["top_swaps"][0]["pair"].startswith("满版大图"),
               json.dumps(st2, ensure_ascii=False)[:160])
            shutil.rmtree(out)
            ck("测试目录已清理", not out.exists())

    print("== I 拆解提示词里确实带了页数与布局规则 ==")
    blk = ts and ai_poster.layout_plan_prompt(False)
    ck("提示词含布局清单", '"full_bleed"' in blk and "要有真图" in blk)
    ck("提示词含页数规则", "装不下就翻页" in blk and "1 页" in blk)
    ck("提示词按有没有真图换话", "没有上传" in blk and "已经上传了" in ai_poster.layout_plan_prompt(True))
    ck("题材映射是数据不是散文", len(ai_poster.THEME_RULES) >= 4
       and all({"signals", "style", "no_photo", "photo", "pages"} <= set(r) for r in ai_poster.THEME_RULES))
    ck("建议页数与规则表一致",
       [ai_poster.suggest_pages(n)[0] for n in (3, 7, 11, 30)] == [1, 2, 3, 4])

    print("== J 用户定了页数时，页数不再由模型判断 ==")
    lim = ai_poster.limits_for(ai_poster.DEFAULT_TEXT_MODE)
    hint3 = ai_poster.layout_plan_prompt(False, 3)
    ck("定了 3 页 → 提示词写死必须正好 3 页",
       "必须正好 3 页" in hint3 and "page_count 填 3" in hint3)
    ck("定了页数就不再念自动页数规则（免得两套话打架）", "装不下就翻页" not in hint3)
    ck("没定页数时提示词照旧走自动规则",
       "装不下就翻页" in blk and "必须正好" not in blk)

    def _pg(title, cards):
        return {"title": title, "subtitle": "", "layout_key": "list", "style_key": "game_epic",
                "role": title, "footer": "内容整理自公开分享", "scene": "",
                "cards": [{"t": "t%d" % i, "d": "说明%d" % i, "v": ""} for i in range(cards)]}

    two = [_pg("第一页", 3), _pg("第二页", 3)]
    got, note = ai_poster._resplit(two, 3, lim)
    ck("2 页 6 条重排成 3 页", len(got) == 3 and note, note)
    ck("重排后要点一条不丢", sum(len(p["cards"]) for p in got) == 6,
       str([len(p["cards"]) for p in got]))
    ck("每页分到的条数尽量均匀（2/2/2）",
       [len(p["cards"]) for p in got] == [2, 2, 2], str([len(p["cards"]) for p in got]))
    ck("新页有角色名，标题留空等人补",
       got[2]["role"] and got[2]["title"] == "", repr(got[2]["role"]))
    back, _n2 = ai_poster._resplit(got, 1, lim)
    ck("3 页并回 1 页也不丢点",
       sum(len(p["cards"]) for p in back) == 6 and len(back) == 1,
       str([len(p["cards"]) for p in back]))
    same, note3 = ai_poster._resplit(two, 2, lim)
    ck("页数本来就对得上就不动结构", same is two and not note3)

    data = {"page_count": 2, "why": "两条线各一页", "cards": [], "footer": "内容整理自公开分享",
            "pages": [{"title": "A 页", "cards": [{"t": "甲", "d": "说明甲"}]},
                      {"title": "B 页", "cards": [{"t": "乙", "d": "说明乙"}]}]}
    p2h, _w, n2h = ai_poster._pages_from_data(data, lim, "game_epic", False, "A 页", 3)
    ck("要点比页数少时不硬摊空页：只给 2 页并说清楚",
       len(p2h) == 2 and any("最多拆 2 页" in x for x in n2h), "；".join(n2h))
    p_no, _wn, n_no = ai_poster._pages_from_data(data, lim, "game_epic", False, "A 页")
    ck("不给 hint 时保持模型给的两页（默认零变化）",
       len(p_no) == 2 and not any("重排" in x for x in n_no), "；".join(n_no))

    rich = {"page_count": 2, "why": "两条线各一页", "cards": [], "footer": "内容整理自公开分享",
            "pages": [{"title": "A 页", "cards": [{"t": "甲%d" % i, "d": "说明%d" % i} for i in range(3)]},
                      {"title": "B 页", "cards": [{"t": "乙%d" % i, "d": "说明%d" % i} for i in range(3)]}]}
    p3, _w, n3 = ai_poster._pages_from_data(rich, lim, "game_epic", False, "A 页", 3)
    ck("模型只给 2 页、用户要 3 页 → 落 3 页并留一句说明",
       len(p3) == 3 and any("重排" in x for x in n3), "；".join(n3))
    def _page_problems(pg):
        # 多页时门禁本来就是按 card_min=1 逐页查的（lint_plan 内部就是这么调的）
        return [m for m in ai_poster.lint_plan({"pages": [pg]}, lim, card_min=1)
                if m != "主标题为空"]
    ck("重排后每页仍能通过字数与卡片数门禁",
       all(not _page_problems(x) for x in p3),
       str([_page_problems(x) for x in p3]))
    ck("新摊出来的那页标题留空、由界面提示人补（不是门禁问题）",
       "主标题为空" in ai_poster.lint_plan({"pages": [p3[2]]}, lim, card_min=1))

    print()
    if FAILS:
        print("FAIL %d 项：%s" % (len(FAILS), "、".join(FAILS)))
        return 1
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
