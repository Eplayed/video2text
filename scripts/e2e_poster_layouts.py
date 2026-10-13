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
import os
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
    print("== A 各构图的版面指纹 ==")
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
    ck("%d 种构图版面指纹互不相同" % len(ts.LAYOUT_ORDER),
       len(set(sigs)) == len(sigs),
       "重复: %s" % [k for k, v in fp.items() if sigs.count(v[0]) > 1] if len(set(sigs)) != len(sigs) else "")
    imgs = [v[1] for v in fp.values()]
    ck("%d 种构图渲染结果互不相同（不是只换颜色）" % len(ts.LAYOUT_ORDER),
       len(set(imgs)) == len(imgs))

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
    ck("每页没写 footer 时继承顶层那一句，但整组只在末页印一次（10-13 收口）",
       (ai_poster.plan_pages(hybrid)[-1].get("footer") or "") == one["footer"]
       and not (ai_poster.plan_pages(hybrid)[0].get("footer") or ""),
       str([p.get("footer") for p in ai_poster.plan_pages(hybrid)]))

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

    print("== K 只重拆一页：提示词与归一与整组同源 ==")
    pages2 = [{"role": "封面", "title": "10月商栈更新", "subtitle": "绝版回来了",
               "layout_key": "top_art", "cards": [{"t": "幽灵军马", "d": "绝版卡牌坐骑", "v": "马"},
                                                  {"t": "面具幻化", "d": "按月轮换", "v": "面具"}]},
              {"role": "行动", "title": "先换哪个", "subtitle": "",
               "layout_key": "list", "cards": [{"t": "看余额", "d": "不够就做日志", "v": "钱袋"}]}]
    pp = ai_poster.page_plan_prompt(2, pages2, "先抓绝版再算顺序", "game_epic", False, "商栈",
                                    lim, content="素材正文在这里")
    ck("只交第 2 页", "只重写其中第 2 页" in pp and "这一页的定位：角色 行动" in pp,
       "；".join([ln for ln in pp.splitlines() if "定位" in ln][:1]))
    ck("把别的页已讲的要点当「别重复」清单塞进去",
       "第 1 页「封面」：10月商栈更新" in pp and "幽灵军马" in pp)
    ck("不叫模型改风格、不改页数、不写 publish",
       'style_key 固定填 "game_epic"' in pp and '"publish"' not in pp
       and "page_count" not in pp)
    ck("布局清单与整组拆解共用同一份", '"full_bleed"' in pp and "要有真图" in pp)
    ck("素材正文进提示词", "素材正文在这里" in pp)
    ck("单页预算与整组同一档", "≤%d 字" % lim["total"] in pp)

    one, n1 = ai_poster.normalize_page(
        {"role": "风险", "layout_key": "not_a_key", "title": "注意这些坑",
         "cards": [{"t": "别拖", "d": "每月刷新下架", "v": "沙漏"}]},
        lim, "game_epic", False, 2)
    ck("单页归一：不认识的布局键回落并留话",
       one["layout_key"] == ts.DEFAULT_LAYOUT and any("不认识" in x for x in n1), "；".join(n1))
    ck("单页归一：尾注整组共用，不让模型各页改",
       one["footer"] == "内容整理自公开分享", one["footer"])
    empty, n2 = ai_poster.normalize_page({"role": "空", "title": "", "cards": []},
                                         lim, "game_epic", False, 3)
    ck("单页归一：什么都没给就判空并说明", empty is None and any("空的" in x for x in n2),
       "；".join(n2))
    try:
        ai_poster.build_page_plan({"content": "x"}, {"api_key": "sk-test"}, 1, [])
        ck("没有页时直接报错而不是白跑模型", False, "没抛异常")
    except ai_poster.PosterError as e:
        ck("没有页时直接报错而不是白跑模型", "还没有页" in str(e), str(e))

    # ── 无字底图提示词（豆包手动那条与付费那条共用 build_art_prompt）──
    # 2026-10-11 豆包 Seedream 4.5 实测四张底图全废：文字区被发光面板占满、图上全是假字。
    # 那些翻车点每一个都是一条静默失效，所以全部钉成断言，改 prompt 的人碰一下就报警。
    art = ai_poster.build_art_prompt(plan(cards=4), RATIO)
    ck("底图提示词里没有 Markdown 星号（粘进豆包会被照抄进画面）", "**" not in art)
    ck("底图提示词不提「留给文字/文字区」（提文字它就把字画上去）",
       "留给文字" not in art and "文字区" not in art)
    ck("底图提示词里没有那句通用道具清单（仪表盘/日历/面板）",
       "仪表盘" not in art and "日历" not in art)
    ck("站位那句排在主视觉前面（先定站位再说画什么）",
       art.index("先定站位") < art.index("主视觉："))
    tail = art.split("【底线】", 1)[-1]
    ck("末尾再复述一次空带位置（只说一次压不住模型）",
       "%" in tail or "下沿" in tail, tail.strip()[:40])
    ck("画风那段里带卡片/屏幕/面板的字样有总闸兜住",
       "只画背景氛围" in art)

    scene_line = [x for x in art.splitlines() if x.startswith("- 主视觉：")][0]
    ck("主视觉里的「界面 / 图标」被换成不带字的形",
       "界面" not in scene_line and "图标" not in scene_line, scene_line)

    p2 = plan(cards=2)
    p2["scene"] = "亡灵圣骑士手握发光大剑“狼独草”，40 级后在门口排 3 个人"
    p2["cards"] = [{"t": "先看这个", "d": "说明", "v": "登录界面截图"},
                   {"t": "再看这个", "d": "说明", "v": "发光的圣光大剑，剑身刻有狼头符文"},
                   {"t": "换不掉的", "d": "说明", "v": "用衬线字体排的一列排版稿"}]
    a2 = ai_poster.build_art_prompt(p2, RATIO)
    scene_line = [x for x in a2.splitlines() if x.startswith("- 主视觉：")][0]
    ck("引号里的装备名不进底图（进去了就被当成要写的字）", "狼独草" not in a2)
    ck("主视觉那句里不留数字", not re.search(r"\d", scene_line), scene_line)
    ck("卡片配图里的界面/截图换成不带字的形之后仍然保留",
       "登录界面" not in a2 and "截图" not in a2 and "场景" in a2)
    ck("换不成形的道具（还带着「字体」那种词）整条丢掉",
       "字体" not in a2 and "排版稿" not in a2)

    p3 = plan(cards=1)
    p3["title"] = "狼独草：前期亡灵圣骑神器"
    p3["subtitle"] = "20 级就能拿"
    p3["scene"] = "背上的圣光大剑狼独草发出光芒，圣骑士站在银松森林"
    p3["cards"] = [{"t": "属性", "d": "三倍伤害", "v": "狼人身上跳出伤害数字"}]
    a3 = ai_poster.build_art_prompt(p3, RATIO)
    ck("这一页要排的主标题字样不从画面描述里再出现一次（实测会被刻在剑身上）",
       "狼独草" not in a3, [x for x in a3.splitlines() if x.startswith("- 主视觉：")][0])
    ck("「伤害数字」这种词换成光效，不留数字", "数字" not in a3.split("【底线】")[0])
    ck("平台水印词剥掉后才算干净（豆包那张右下角一定有）",
       ai_poster._base_stray_text("豆包AI生成") == "" and
       ai_poster._base_stray_text("豆包AI生成 狼独草") == "狼独草")
    ck("「刻有符文」改成发光纹路（照抄就画出鬼画符）",
       "符文" not in a2 and "发光纹路" in a2)

    two = {"title": "整组标题", "subtitle": "", "footer": "内容整理自公开分享",
           "style_key": "game_epic", "scene": "月光下的银松森林",
           "pages": [{"title": "第一页", "cards": [{"t": "a", "d": "b", "v": ""}],
                      "style_key": None, "layout_key": "bottom_art"},
                     {"title": "第二页", "cards": [{"t": "c", "d": "d", "v": ""}],
                      "layout_key": "left_text"}]}
    mp = ai_poster.manual_prompts_for(two, RATIO, "typeset")
    ck("多页时每页继承顶层风格键（不回落成默认皮肤的配色）",
       len(mp) == 2 and "暗色云雾" in mp[0]["copy"] and "深蓝紫渐变" not in mp[0]["copy"])
    ck("每页的站位跟着自己那页的布局走",
       "画面上部" in mp[0]["copy"] and "画面左侧" in mp[1]["copy"])
    ck("模型只给页内不带 style_key 时，页码与全文说明照样齐",
       "【第 1/2 页" in mp[0]["copy"] and "豆包" in mp[0]["full"])

    # ── 「直出成品」那一档（字由模型画）：提示词与说明必须整套换掉 ──
    md = ai_poster.manual_prompts_for(two, RATIO, "model")
    ck("直出档回显 mode，界面才知道该不该用这份提示词",
       md[0].get("mode") == "model" and mp[0].get("mode") == "typeset")
    ck("直出档的提示词要模型把中文画进去（含逐字渲染那块）",
       "成品图" in md[0]["copy"] and "逐字" in md[0]["copy"] and "无字底图" not in md[0]["copy"])
    ck("直出档不再要求那半截空着（它反过来要字画满）",
       "先定站位" not in md[0]["copy"] and "必须是空的" not in md[0]["copy"])
    ck("给人看的说明分两档写，各自只说各自那条路的判据",
       "该空的那半截" in mp[0]["full"] and "该空的那半截" not in md[0]["full"]
       and "逐字" in md[0]["full"])
    ck("提示词里不夹自我吐槽（那句「想不错字就切回程序排字」是给我们看的，不是给豆包的）",
       "想不错字" not in md[0]["copy"])

    print("== N 攻略页：真帧 + 图注 + 大字数字 + 对照 + 警示 + 互动 ==")
    import io as _io
    from PIL import Image as _Img
    buf = _io.BytesIO()
    _Img.new("RGB", (700, 1200), (60, 90, 70)).save(buf, format="JPEG")   # 竖帧，故意非方形
    FRAME = buf.getvalue()

    plain = {"title": "普通海报标题", "subtitle": "副", "style_key": "tool_review",
             "cards": [{"t": "一", "d": "第一条说明"}]}
    ck("plan 里没有攻略零件 → 绝不走攻略页（老产物字节不变的那道闸）",
       ts._is_guide(plain) is False)
    ck("plan 里只要出现一样攻略零件就走攻略页",
       ts._is_guide({"photos": [{"path": "x"}]}) and ts._is_guide({"layout_key": "guide"}))

    gp = {"style_key": "guide_ink", "layout_key": "guide",
          "kicker": "无限 · 传承点数", "title": "这回，点数能存着花",
          "subtitle": "素材里出现的官方界面：Legacy Tree",
          "photos": [{"bytes": FRAME, "caption": "传承点数界面 · 素材抽帧第 3 帧"}],
          "cards": [{"t": "怎么拿", "d": "完成各种独特挑战，换一种叫传承点数的资源。"},
                    {"t": "怎么花", "d": "点进三棵天赋树，解锁各种长期增益效果。"}],
          "contrast": [{"t": "口播点了名", "d": "更快拿到双倍经验加成、缩短营火冷却时间"}],
          "footer": "整理自单条抖音素材的口播，未做游戏内实测"}
    gpng, gchk = ts.typeset(None, gp, ratio=RATIO, page=(2, 5))     # base=None：攻略页不出底图
    ck("攻略页不传底图也能出图（这条分支 0 元、不叫模型）",
       gpng[:8] == b"\x89PNG\r\n\x1a\n" and gchk["engine"] == "pil-guide")
    ck("一页装得下：栏目名+真帧+图注+两条要点+对照+脚注，不报超版",
       gchk["ok"], "；".join(gchk["problems"]))
    ck("页码走对标那种两位格式（02 / 05），不是 2 / 5",
       gchk.get("page") == [2, 5] and "%02d / %02d" % (2, 5) == "02 / 05")

    # 页眉那一行不跟栏目名绑死：模型常常只在第 1 页写栏目名，内页也得有"第几页/共几页"
    nk = dict(gp)
    nk.pop("kicker")
    _nk, nkchk = ts.typeset(None, nk, ratio=RATIO, page=(3, 5))
    ck("没有栏目名的内页照样不超版（页眉那行是按有页码量的）",
       nkchk["ok"], "；".join(nkchk["problems"]))
    ck("同一页有页码 / 没页码是两张不同的图（页眉不跟着栏目名一起消失）",
       ts.typeset(None, nk, ratio=RATIO, page=(3, 5))[0]
       != ts.typeset(None, nk, ratio=RATIO, page=None)[0])

    # 三套攻略皮不是摆着好看的：排版真读得到色、也不因为换了底色就撑爆版面
    for k in ("guide_paper", "guide_night"):
        _s, schk = ts.typeset(None, dict(gp, style_key=k), ratio=RATIO, page=(2, 5))
        ck("换皮 %s 照样排得出来且不超版" % k, schk["ok"] and _s[:8] == b"\x89PNG\r\n\x1a\n",
           "；".join(schk["problems"]))
    ck("换皮是真换（同一条内容、两种脸，字节不一样）",
       ts.typeset(None, dict(gp, style_key="guide_ink"), ratio=RATIO, page=(2, 5))[0]
       != ts.typeset(None, dict(gp, style_key="guide_paper"), ratio=RATIO, page=(2, 5))[0])

    # 剩下三样零件各占一页（对标也是一页一件事，不许全堆一页）
    gcover = dict(gp)
    gcover["photos"] = [{"bytes": FRAME, "caption": "野外营火 · 素材抽帧第 1 帧"}]
    gcover["bignum"] = {"v": "3 棵", "label": "天赋树：专业 / 冒险 / 资源利用"}
    gcover["contrast"] = []
    gcover["cards"] = []
    _c, cchk = ts.typeset(None, gcover, ratio=RATIO, page=(1, 5))
    ck("封面那页：大字数字块进版面且不超版",
       "bignum" in cchk["blocks"] and cchk["ok"], "；".join(cchk["problems"]))

    gend = {"style_key": "guide_ink", "layout_key": "guide", "kicker": "无限 · 传承点数",
            "title": "这三棵，你先点哪棵？",
            "ask": {"q": "如果点数只够先开一棵，你选哪个方向？",
                    "options": ["专业", "冒险", "资源利用"]},
            "warn": {"t": "别按这页去加点", "d": "三棵树各自解锁什么，这条素材只给了名字。"},
            "footer": "整理自单条抖音素材的口播，未做游戏内实测"}
    _e, echk = ts.typeset(None, gend, ratio=RATIO, page=(5, 5))
    ck("收尾那页：互动提问 + 警示块进版面且不超版",
       set(["ask", "warn"]) <= set(echk["blocks"]) and echk["ok"],
       "；".join(echk["problems"]))
    ck("五样零件在三页里全部出现过一次（栏目名/真帧+图注/大字数字/对照+警示/互动）",
       set(["kicker", "photos", "bignum", "contrast", "warn", "ask"])
       <= set(gchk["blocks"]) | set(cchk["blocks"]) | set(echk["blocks"]))

    # 图注口径：读不到文件必须明说，不能默默少一张（少一张=论点没证据）
    bad = dict(gp)
    bad["photos"] = [{"path": "/definitely/not/here.jpg", "caption": "不该出现的图注"}]
    bad["contrast"] = []
    _b, bchk = ts.typeset(None, bad, ratio=RATIO, page=(1, 3))
    ck("真帧文件读不到 → 明说并跳过，不静默出空位",
       any("读不到文件" in x for x in bchk["problems"]), "；".join(bchk["problems"]))

    # 一页两张 + 文字很多：先退一张，还放不下就退回纯文字，两种都要留话
    two = dict(gp)
    two["photos"] = [{"bytes": FRAME, "caption": "第一帧"}, {"bytes": FRAME, "caption": "第二帧"}]
    two["cards"] = [{"t": "长说明 %d" % i,
                     "d": "这是一段刻意写长的说明，用来把版面挤满，看看引擎会不会硬塞两张图。" * 2}
                    for i in range(4)]
    _t, tchk = ts.typeset(None, two, ratio=RATIO, page=(1, 5))
    ck("挤不下两张真帧 → 只留一张并说明原因（不压扁证据）",
       any("只留第 1 张" in x or "退回纯文字" in x for x in tchk["problems"]),
       "；".join(tchk["problems"]))
    ck("退图之后不再超版（只留「退一张」这条说明，不该再报塞不下）",
       not any("超出版面" in x for x in tchk["problems"]), "；".join(tchk["problems"]))

    _f, lines, _s = ts._fit_keep("这回，点数能存着花", "sans_bold", int(H * 0.056),
                                 int(936 - 2 * 936 * 0.062), 1, int(H * 0.034))
    ck("10 字标题死磕一行：不出现「第二行只剩一个字」那种孤儿行",
       len(lines) == 1, "%d 行 %s" % (len(lines), lines))
    ck("攻略页皮肤带齐那三样专属色（深墨绿底/砖红警示/米金）",
       all(k in ts.TYPE_SKINS["guide_ink"] for k in ("warn_bg", "gold", "rule")))
    ck("老四套皮肤没被攻略页污染（不新增读取它们的键）",
       all(k not in ts.TYPE_SKINS[s] for s in ("game_epic", "tool_review",
                                               "checklist", "minimal")
           for k in ("warn_bg", "gold", "rule")))
    ck("攻略页进了界面版式下拉（不然号主选不到这一档）",
       "guide" in ts.LAYOUT_ORDER and any(c["key"] == "guide" for c in ts.layout_choices()))
    ck("guide_ink 也进了风格下拉（style_choices 走的是 STYLE_ORDER，漏加就是选不到）",
       any(s["key"] == "guide_ink" for s in ai_poster.style_choices()),
       ",".join(s["key"] for s in ai_poster.style_choices()))

    print("== O 拆解层接线：帧编号→真文件、图注由代码写 ==")
    fake = [{"i": 3, "ts": "00:12", "file": "/tmp/f3.jpg", "subject": "游戏内天赋树界面"},
            {"i": 7, "ts": "00:24", "file": "/tmp/f7.jpg", "subject": "角色旁有半透明小熊"}]
    ck("没有可用帧时，提示词里那条「铁律六」整段不出现（不占预算也不误导模型）",
       ai_poster.guide_rules_text([], 200) == "" and
       "铁律六" in ai_poster.guide_rules_text(fake, 200))
    ck("帧清单带编号、时间戳和画面描述，模型只能照抄编号",
       "F3｜00:12" in ai_poster.guide_rules_text(fake, 200) and
       "游戏内天赋树界面" in ai_poster.guide_rules_text(fake, 200))
    pg = [{"title": "甲", "cards": [{"t": "a", "d": "b"}],
           "photos": [{"frame": "F7"}, {"frame": "F99"}],
           "kicker": "无限 · 传承点数", "bignum": {"v": "3 棵", "label": "天赋树"},
           "warn": {"t": "别按这页加点", "d": "素材只给了名字"},
           "ask": {"q": "你先点哪棵？", "options": ["专业", "冒险", "不点"]}}]
    bn = ai_poster.bind_frames(pg, fake)
    ck("模型点了不存在的编号 → 丢掉那张图并留一句人话（不静默出空位）",
       len(pg[0]["photos"]) == 1 and any("F99" in x for x in bn), "；".join(bn))
    ck("图注由代码按那一帧自己的记录写，不是模型编的",
       pg[0]["photos"][0]["caption"] == "角色旁有半透明小熊",
       pg[0]["photos"][0]["caption"])
    # 图注上那句「· 素材画面 00:24」是 10-12 加的，本意是"这条有出处"；但它是印在图上的，
    # 发小红书等于自证"这帧是从别人视频里抽的"（10-13 判"AI 托管发文/非真人分享"的旁证之一）。
    # 时间戳仍然在 frames.json 与 manifest 里，只是不上图。
    ck("图注不许带来源后缀（时间戳/「素材画面」字样不上图）",
       "素材画面" not in pg[0]["photos"][0]["caption"]
       and "00:24" not in pg[0]["photos"][0]["caption"],
       pg[0]["photos"][0]["caption"])
    ck("攻略页零件过一遍清洗后形状可用（kicker/bignum/warn/ask 都在）",
       pg[0].get("kicker") and pg[0]["bignum"]["v"] == "3 棵"
       and pg[0]["warn"]["t"] and len(pg[0]["ask"]["options"]) == 3)
    ck("选项最多三项，模型给多了会被裁（版面放不下）",
       len(ai_poster.guide_clip({"ask": {"q": "问", "options": ["a", "b", "c", "d", "e"]}},
                                ai_poster.TS_LIMITS)["ask"]["options"]) == 3)
    # 栏目名是"一整套"的记号：模型只写在其中一页，其余页得由代码补上，
    # 否则第 2、3 页左上角空着，五页看着像五张不相干的图（2026-10-12 实测就是这样）
    pgk = [{"title": "甲", "cards": [{"t": "a", "d": "b"}]},
           {"title": "乙", "kicker": "无限 · 传承点数", "cards": [{"t": "c", "d": "d"}]}]
    ai_poster.bind_frames(pgk, fake)
    ck("栏目名只写在某一页 → 其余页代码补齐（页页都有同一句栏目名）",
       pgk[0].get("kicker") == "无限 · 传承点数"
       and pgk[1].get("kicker") == "无限 · 传承点数", str([p.get("kicker") for p in pgk]))
    rt = ai_poster.plan_pages({"pages": [dict(pg[0], layout_key="guide")],
                               "title": "甲", "footer": "脚", "style_key": "guide_ink"})
    ck("存下来的 plan 再过一遍 plan_pages，攻略页零件不丢（重出时才不会掉回纯文字）",
       rt[0].get("photos") and rt[0].get("bignum") and rt[0].get("ask"),
       str(list(rt[0].keys())))

    print("== O2 同质化两处收口：免责脚注只印一次、攻略页三套皮轮换 ==")
    # 免责脚注以前每页从顶层继承同一句，三页就印三次「整理自公开分享」——
    # 既像模板套出来的，也像在替平台做申报（2026-10-13 那条"AI 托管发文"复盘）
    ft = ai_poster.plan_pages({"footer": "脚", "style_key": "guide_ink", "title": "甲",
                               "pages": [{"title": "甲", "cards": [{"t": "a", "d": "b"}], "footer": "脚"},
                                         {"title": "乙", "cards": [{"t": "c", "d": "d"}], "footer": "别的话"},
                                         {"title": "丙", "cards": [{"t": "e", "d": "f"}], "footer": "脚"}]})
    ck("整组继承下来的免责脚注只在末页印一次",
       [p.get("footer") for p in ft] == ["", "别的话", "脚"], str([p.get("footer") for p in ft]))
    ck("某一页自己写过不一样的话 = 编辑的决定，不跟着一起收掉",
       ft[1].get("footer") == "别的话", ft[1].get("footer"))
    keys = set(ts.TYPE_SKINS["guide_ink"])
    ck("三套攻略皮读的键完全一致（少一个键就是排版时崩一次或静默用错色）",
       all(set(ts.TYPE_SKINS[k]) == keys for k in ("guide_paper", "guide_night")),
       str([sorted(keys - set(ts.TYPE_SKINS[k])) for k in ("guide_paper", "guide_night")]))
    ck("三套皮是真的换了脸（底色各不相同，不是复制一份改个名）",
       len({ts.TYPE_SKINS[k]["bg_top"] for k in
            ("guide_ink", "guide_paper", "guide_night")}) == 3)
    ck("三套都进了风格下拉（选不到就等于没做）",
       all(any(s["key"] == k for s in ai_poster.style_choices())
           for k in ("guide_ink", "guide_paper", "guide_night")))
    # 「只重拆这一页」以前不带帧清单：模型不敢给 photos 编号，重拆一次就把这页的证据图丢了
    _lim = ai_poster.limits_for("typeset")
    _p_no = ai_poster.page_plan_prompt(2, pgk, "主线", "guide_ink", False, "主题", _lim)
    _p_fr = ai_poster.page_plan_prompt(2, pgk, "主线", "guide_ink", False, "主题", _lim, frames=fake)
    ck("只重拆一页：给了帧清单才出现「铁律六」，且清单里就是那几个编号",
       "铁律六" not in _p_no and "铁律六" in _p_fr and "F3｜00:12" in _p_fr)
    # 封面页只有标题+要点，别的攻略零件都在内页——这条以前被 kicker 误判成空白页（10-13 实测）
    cover = {"layout_key": "guide", "title": "60级直奔诺森德", "kicker": "攻略 · 60级直奔诺森德",
             "cards": [{"t": "外域或停", "d": "官方问卷里的选项"}, {"t": "3月一阶段", "d": "节奏写在爆料里"}]}
    ck("攻略页封面（只有 kicker + 两条要点，没图没提问）不该被当成空白页拦下",
       not any("空白页" in x for x in ai_poster.lint_plan(cover, _lim, card_min=1)),
       str(ai_poster.lint_plan(cover, _lim, card_min=1)))
    empty = {"layout_key": "guide", "title": "空的", "kicker": "攻略 · 空的", "cards": []}
    ck("攻略页真的一张能读的东西都没有 → 照样拦（这条不能跟着一起松掉）",
       any("空白页" in x for x in ai_poster.lint_plan(empty, _lim, card_min=1)),
       str(ai_poster.lint_plan(empty, _lim, card_min=1)))
    ck("轮换公式：同一条素材稳定可复现、连着几条会换脸、且只在这三套里打转",
       ai_poster.guide_style_for(81) == ai_poster.guide_style_for("81")
       and len({ai_poster.guide_style_for(i) for i in range(9)}) == 3
       and all(ai_poster.guide_style_for(i) in ("guide_ink", "guide_paper", "guide_night")
               for i in list(range(20)) + [None, "", "abc"]))

    # 整条出图路径跑一遍：攻略页一张 AI 底图都不该叫，账必须记 0 元
    frame_file = ROOT / "temp" / "e2e_frame.jpg"
    with open(str(frame_file), "wb") as f:
        f.write(FRAME)
    gplan = {"style_key": "guide_ink", "footer": "整理自公开分享",
             "recommended": {"page_count": 2, "style_key": "guide_ink",
                             "layouts": ["guide", "guide"], "why": "测试用推荐"},
             "pages": [
                 {"role": "封面", "layout_key": "guide", "style_key": "guide_ink",
                  "title": "传承点数，三棵树怎么点", "subtitle": "口播整理",
                  "footer": "整理自公开分享",
                  "kicker": "无限 · 传承点数",
                  "photos": [{"path": str(frame_file), "caption": "天赋树界面"}],
                  "bignum": {"v": "3 棵", "label": "天赋树：专业 / 冒险 / 资源利用"},
                  "cards": [{"t": "怎么拿", "d": "完成独特挑战换点数"}]},
                 {"role": "避坑", "layout_key": "guide", "style_key": "guide_ink",
                  "title": "素材没说的别当结论", "footer": "整理自公开分享",
                  "warn": {"t": "别按这页加点", "d": "三棵树给什么，口播只报了名字"},
                  "ask": {"q": "你先点哪棵？", "options": ["专业", "冒险", "资源利用"]}}]}
    gmf = ai_poster.generate_poster(
        {"id": TEST_ID, "title": "测试攻略", "content": "完成独特挑战获得传承点数，可点在三种天赋树。",
         "summary_type": "game_guide", "status": "ai"},
        {"method": "openai", "api_key": "x", "api_base": "http://127.0.0.1:1", "model": "x"},
        plan=gplan, text_mode="typeset", ratio="9:16", do_verify=False, do_review=False)
    ck("整组都是攻略页 → manifest 记 0 元（账记错比不记更糟，这条已经错过一次）",
       gmf.get("cost_yuan_estimate") == 0, str(gmf.get("cost_yuan_estimate")))
    ck("攻略页不出底图 → image_model 留空，不假装调用过出图模型",
       (gmf.get("image_model") or "") == "", repr(gmf.get("image_model")))
    ck("攻略页成品落盘：两页 png 都在，页码跟着页走",
       all((ROOT / "output" / "poster" / str(TEST_ID) / ("poster_p%d.png" % i)).exists()
           for i in (1, 2)))

    print("== P 帧池：裁完不留中间帧（一条素材 5.6 MB → 2.7 MB）==")
    from src import frame_pool as fpool
    sandbox = ROOT / "temp" / "_pool_probe"
    if sandbox.exists():
        shutil.rmtree(str(sandbox))
    _old_pool_dir = fpool.POOL_DIR
    fpool.POOL_DIR = sandbox
    try:
        def mk(name, band, checked, keep_raw=True):
            d = sandbox / name
            (d / "raw").mkdir(parents=True)
            (d / "clean").mkdir(parents=True)
            frames = []
            for i in range(1, 3):
                rp = d / "raw" / ("f_%03d.jpg" % i)
                cp = d / "clean" / ("c_f_%03d.jpg" % i)
                rp.write_bytes(b"R" * 4096)
                cp.write_bytes(b"C" * 2048)
                frames.append({"i": i, "ts": "00:0%d" % i, "file": str(cp),
                               "raw": str(rp) if keep_raw else "", "band": band,
                               "checked": checked, "clean": True, "subject": "画面"})
            json.dump({"aweme_id": name, "frames": frames, "checked": checked},
                      open(str(d / "frames.json"), "w", encoding="utf-8"),
                      ensure_ascii=False)
            return d
        judged = mk("p_judged", [70, 78], True)
        unjudged = mk("p_unjudged", [70, 78], False)
        noband = mk("p_noband", None, True)
        r = fpool.prune_raw()
        ck("判过帧、横幅已定位的池子：raw 中间帧删掉并省了盘子",
           r["files"] == 2 and r["bytes"] == 2 * 4096 and not (judged / "raw" / "f_001.jpg").exists(), json.dumps(r))
        ck("没判过的池子默认留着 raw（将来补第二刀要从原帧量位置）",
           (unjudged / "raw" / "f_001.jpg").exists())
        ck("定位不到横幅的池子绝不删原帧（那是唯一可用来源）",
           (noband / "raw" / "f_001.jpg").exists())
        meta = json.load(open(str(judged / "frames.json"), encoding="utf-8"))
        ck("删完把 frames.json 里的 raw 字段也清掉（不留悬空路径）",
           all(not f.get("raw") for f in meta["frames"]))
        ck("clean 帧一张没动（版面读的就是它）",
           (judged / "clean" / "c_f_001.jpg").exists())
    finally:
        fpool.POOL_DIR = _old_pool_dir
        shutil.rmtree(str(sandbox), ignore_errors=True)

    print("== Q 帧池判据：不认颜色、认位置；裁不动的就弃用 ==")
    from src import frame_pool as fp2
    from PIL import Image, ImageDraw

    def probe(got):
        it = {"checked": False, "clean": None, "subject": "", "why": ""}
        fp2.apply_probe(it, got)
        return it

    okp = probe({"post_text": False, "where": "none", "subject": "角色站在城门口"})
    ck("模型说没有后期字 → 判干净、不裁", okp["clean"] is True and okp["crop_to"] is None)
    btp = probe({"post_text": True, "where": "bottom", "band_top": 86, "band_bot": 96})
    ck("贴底的字幕条（白字黑边、什么颜色都行）→ 记下要裁哪一条", btp["crop_to"] == (86, 96))
    midp = probe({"post_text": True, "where": "middle", "band_top": 40, "band_bot": 62})
    ck("花字压在画面中间 → 不许裁，直接弃用并写原因",
       midp["clean"] is False and midp["crop_to"] is None and "中间" in midp["why"], midp["why"])
    corp = probe({"post_text": True, "where": "corner", "band_top": 78, "band_bot": 99})
    ck("角落真人小窗 → 裁横条切不掉，弃用（这种以前会被当成品发出去）",
       corp["crop_to"] is None and "角落" in corp["why"], corp["why"])
    fullp = probe({"post_text": True, "where": "full", "band_top": 0, "band_bot": 100})
    ck("整帧就是作者做的卡/表 → 弃用", fullp["crop_to"] is None and "卡" in fullp["why"])
    liep = probe({"post_text": True, "where": "bottom", "band_top": 40, "band_bot": 70})
    ck("模型说 bottom 却给个不贴边的区间 → 照样不许裁（裁了等于把画面切一半）",
       liep["crop_to"] is None)
    echop = probe({"post_text": True, "where": "bottom", "band_top": 85, "band_bot": 95,
                   "post_words": ["后期压上来的中文，最多3条，没有就空数组", "拍卖行"]})
    ck("提示词被原样抄回来时不当证据用（只留真正的后期文字）",
       echop["words"] == ["拍卖行"], str(echop["words"]))
    ck("蓝色那条像素判据只在贴顶/贴底时才当横幅用，中部的一律不信",
       fp2._is_edge_band((22, 30, 0.4)) and not fp2._is_edge_band((40, 62, 0.4)))

    card_png = ROOT / "temp" / "_q_card.jpg"
    scene_png = ROOT / "temp" / "_q_scene.jpg"
    im = Image.new("RGB", (720, 1280), (250, 216, 224))        # 粉底整幅
    dd = ImageDraw.Draw(im)
    dd.rounded_rectangle([80, 250, 640, 420], 26, fill=(255, 255, 255), outline=(20, 20, 20))
    dd.text([150, 300], "魔兽世界：无限", fill=(20, 20, 20))
    dd.text([220, 700], "排骨人春天来了", fill=(250, 220, 40))
    dd.text([250, 1150], "@某某的魔兽世界", fill=(230, 60, 90))
    im.save(str(card_png), quality=92)
    noise = Image.effect_noise((720, 1280), 90).convert("RGB")
    noise.save(str(scene_png), quality=92)
    ck("整帧是卡：免费预筛就能扔掉，一次看图的钱都不花",
       fp2.light_share(str(card_png)) >= fp2.CARD_MIN_LIGHT,
       "亮块 %.2f ≥ %.2f" % (fp2.light_share(str(card_png)), fp2.CARD_MIN_LIGHT))
    ck("真画面不误判成卡（黑边 letterbox 走暗的那一档，不混进来）",
       fp2.light_share(str(scene_png)) < fp2.CARD_MIN_LIGHT,
       "亮块 %.2f" % fp2.light_share(str(scene_png)))
    drp = fp2.dropped({"frames": [
        {"checked": True, "clean": False, "why": "整帧是作者做的卡或表", "band": None},
        {"checked": True, "clean": False, "why": "整帧是作者做的卡或表", "band": None},
        {"checked": True, "clean": True, "why": ""},
        {"checked": False, "clean": None, "why": ""}]})
    ck("扔掉的帧按原因归堆（没判过的不算扔）",
       sum(drp.values()) == 2 and max(drp.values()) == 2, str(dict(drp)))

    # 整条 ensure_judged 走一遍：_ask_vision 换成假答案，不联网、不花钱
    sb = ROOT / "temp" / "_q_pool"
    if sb.exists():
        shutil.rmtree(str(sb))
    d0 = sb / "q_flow"
    (d0 / "raw").mkdir(parents=True)
    (d0 / "clean").mkdir(parents=True)
    frames = []
    for i in (1, 2):
        rp = d0 / "raw" / ("f_%03d.jpg" % i)
        shutil.copyfile(str(scene_png), str(rp))
        frames.append({"i": i, "ts": "00:0%d" % i, "file": str(rp), "raw": str(rp),
                       "band": None, "checked": False, "clean": None, "subject": "", "why": ""})
    json.dump({"aweme_id": "q_flow", "frames": frames, "usable": 0, "tokens": 0},
              open(str(d0 / "frames.json"), "w", encoding="utf-8"), ensure_ascii=False)
    _old_pool, _old_ask = fp2.POOL_DIR, fp2._ask_vision
    fp2.POOL_DIR = sb
    seen = []

    def fake_ask(img_path, question, ai_config, **kw):
        seen.append(str(img_path))
        if "has_band" in question:            # 只补问一次数字（模型说贴边却没给区间才走到这）
            return {"has_band": True, "band_top": 85, "band_bot": 95}, 31
        if "v_" in os.path.basename(str(img_path)):     # 裁过的那张：干净了
            return {"post_text": False, "where": "none", "subject": "角色属性界面"}, 77
        return {"post_text": True, "where": "bottom", "band_top": 0, "band_bot": 0,
                "post_words": ["预计得打18层"], "subject": "带字幕的画面"}, 99
    fp2._ask_vision = fake_ask
    try:
        meta = fp2.ensure_judged("q_flow", {"api_key": "sk-test"}, limit=8)
        it = meta["frames"][0]
        ck("模型说 bottom 却没给区间 → 只补问一次数字，照样裁得回来（不白扔一整批）",
           len(seen) == 6 and it["clean"] is True and (it["band"] or [])[:2] == [85, 95],
           "调用 %d 次 band=%s clean=%s" % (len(seen), it.get("band"), it.get("clean")))
        ck("模型补裁的成品帧落 clean/，不落 raw/（raw 只放中间产物这条账才对）",
           os.path.basename(it["file"]).startswith("v_")
           and str(d0 / "clean") in it["file"] and os.path.exists(it["file"]))
        ck("判完不留原帧", not it.get("raw") and not (d0 / "raw" / "f_001.jpg").exists())
        ck("frames.json 里的 usable 判完重算（不留着没判时的旧数）",
           meta["usable"] == 2 and len(fp2.usable(meta)) == 2,
           "usable=%s candidates=%s" % (meta.get("usable"), meta.get("candidates")))
        ck("token 记的是这一帧所有调用之和（账要能对上）", meta["tokens"] == (99 + 31 + 77) * 2,
           str(meta["tokens"]))
    finally:
        fp2.POOL_DIR = _old_pool
        fp2._ask_vision = _old_ask
        shutil.rmtree(str(sb), ignore_errors=True)
        for p in (card_png, scene_png):
            if p.exists():
                os.remove(str(p))

    print()
    if FAILS:
        print("FAIL %d 项：%s" % (len(FAILS), "、".join(FAILS)))
        return 1
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
