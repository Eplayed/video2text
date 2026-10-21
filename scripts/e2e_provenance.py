# -*- coding: utf-8 -*-
"""编辑证据链的定点回归：正向一条 + PRD 点名的五条反例（`PRD-video2text-原创增量改造-2026-10-21.md` §可验证验收 2）。

三条纪律：
· **不碰生产**：库是 `temp/` 下现造的临时库，跑完删；不读 `output/video2text.db`，不生成图；
· **不花钱**：全程纯函数 + SQLite，不调任何模型；
· 反向优先：每条反例都要断言"缺项点名到具体字段/结论/图片"，而不是只断言"有个错"——
  操作者要能在成品预览上照着补，缺一句话就等于没告诉他人该干什么。

用法：env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/e2e_provenance.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = ROOT / "temp" / "_prov_test.db"

fails = []


def ck(name, cond, detail=""):
    detail = "" if detail == "" else str(detail)      # 断言里偶尔传 list/dict，别让它炸在打印上
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + detail) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + detail) if detail else ""))
        fails.append(name)


def fields(gaps):
    return [g["field"] for g in gaps]


def main():
    from src import guide_provenance as gp

    if DB.exists():
        os.remove(str(DB))
    DB.parent.mkdir(exist_ok=True)

    print("== A 正向：三项齐 + 结论有证据 + 材料有依据 → 不挡待发，但只说「待人工评估」 ==")
    full = {"slot_key": "dungeon5", "channel": "toutiao", "form": "guide", "version": 1,
            "question": "13–60 级想按等级顺序刷完九座新五人本，路线怎么排",
            "contribution": "把官方公告里的九座本按等级段排成一条练级路线，标出联盟/部落谁要深入对方领地",
            "contribution_evidence": "官方公告两篇（wow.blizzard.cn/news/24301455、24303009）"
                                     "＋本地核对记录 temp/dungeon_route_check.md",
            "claims": [{"text": "领主大厅等级区间 13–18，入口在铁炉堡地下",
                        "evidence": "https://wow.blizzard.cn/news/24301455/index.html",
                        "evidence_kind": "官方一手页面", "verifier": "张亚军", "vdate": "2026-10-21",
                        "status": "已核"},
                       {"text": "深穴 12 月 10 日开，10 人 8 首领",
                        "evidence": "https://wow.blizzard.cn/news/24303009/index.html",
                        "evidence_kind": "官方一手页面", "verifier": "张亚军", "vdate": "2026-10-21",
                        "status": "已核"},
                       {"text": "黑喉要塞 55–60 适合顺路刷",
                        "evidence": "", "evidence_kind": "无", "verifier": "", "vdate": "",
                        "status": "参考转述"}],
            "materials": [
                {"url": "https://wow.blizzard.cn/news/24301455/index.html", "author": "暴雪官方",
                 "usage": "事实来源", "rights_basis": "官方公告转述，标来源与日期", "review": "可发布"},
                {"ref": "self:route_table", "author": "本站",
                 "usage": "我们自己做的", "rights_basis": "路线表与说明图是我们自己排的",
                 "review": "可发布"}]}
    row = gp.save(str(DB), full)
    got = gp.load(str(DB), "dungeon5", "toutiao", "guide", 1)
    ck("存进去读得回来，claims/materials 结构不丢",
       len(got["claims"]) == 3 and len(got["materials"]) == 2, "claims=%d materials=%d" % (
           len(got["claims"]), len(got["materials"])))
    gaps = gp.compute_gaps(got)
    ck("正向一条待发阻塞都没有", [g for g in gaps if g["blocks"] == "publish"] == [],
       str(fields(gaps)))
    key, label = gp.state(got)
    ck("状态是「有独立贡献，待人工评估」，不是「已获原创」", key == gp.STATE_CONTRIB, label)
    line = gp.summary_line(got)
    ck("摘要里明写这不是平台原创资格认证", "不是平台原创资格认证" in line, line[:60])

    print("== B 反例一：单视频换词改写（没说明新增了什么） ==")
    rewrite = dict(full, contribution="", contribution_evidence="")
    gp.save(str(DB), dict(rewrite, slot_key="rw1"))
    b = gp.load(str(DB), "rw1", "toutiao", "guide", 1)
    bf = fields(gp.compute_gaps(b))
    ck("缺项点名到 contribution 这一栏", "contribution" in bf, str(bf))
    ck("状态是「独立贡献未说明」", gp.state(b)[0] == gp.STATE_UNSET, gp.state(b)[1])
    gp.save(str(DB), dict(rewrite, slot_key="rw1b", contribution="无，本篇就是整理"))
    b2 = gp.load(str(DB), "rw1b", "toutiao", "guide", 1)
    ck("如实写「就是整理」→ 状态转成整理转述，不再算缺项",
       gp.state(b2)[0] == gp.STATE_DIGEST and "contribution" not in fields(gp.compute_gaps(b2)),
       gp.state(b2)[1])

    print("== C 反例二：多视频拼接（用了别人的表达却没写依据） ==")
    stitch = dict(full, slot_key="st1",
                  materials=[{"url": "https://v.douyin.com/aaa", "author": "某博主",
                              "usage": "被引用的表达", "rights_basis": "", "review": "未审"},
                             {"url": "https://v.douyin.com/bbb", "author": "另一个博主",
                              "usage": "被引用的画面", "rights_basis": "", "review": "未审"},
                             {"url": "https://v.douyin.com/ccc", "author": "第三个",
                              "usage": "选题线索", "rights_basis": "", "review": "未审"}])
    gp.save(str(DB), stitch)
    c = gp.load(str(DB), "st1", "toutiao", "guide", 1)
    cf = fields(gp.compute_gaps(c))
    ck("两条借用项各自被点名（不是一句「材料有问题」）",
       "materials[1].rights_basis" in cf and "materials[2].rights_basis" in cf, str(cf))
    ck("选题线索那条不挡（它只是线索，没用人家的表达）",
       "materials[3].rights_basis" not in cf, str(cf))
    ck("未审的项挡待发", any(g["blocks"] == "publish" for g in gp.compute_gaps(c)))

    print("== D 反例三：手填「我测过」但没有可回查记录 ==")
    fake = dict(full, slot_key="fk1",
                claims=[{"text": "我实测过，这条路线两小时刷完",
                         "evidence": "", "evidence_kind": "无", "verifier": "", "vdate": "",
                         "status": "已核"},
                        {"text": "开门任务我实测过一遍，走完就开",
                         "evidence": "本站物品详情页", "evidence_kind": "本站页面查询",
                         "verifier": "张亚军", "vdate": "2026-10-21",
                         "status": "已核"}])
    gp.save(str(DB), fake)
    d = gp.load(str(DB), "fk1", "toutiao", "guide", 1)
    df = gp.compute_gaps(d)
    ck("已核但没证据 → 点名 claims[1].evidence", "claims[1].evidence" in fields(df), str(fields(df)))
    ck("已核但没写谁核哪天 → 点名 claims[1].verifier", "claims[1].verifier" in fields(df))
    ck("站内查询写成游戏内实测 → 单独一条（这两件事必须分开说）",
       "claims[2].evidence_kind" in fields(df),
       [g["what"] for g in df if g["field"] == "claims[2].evidence_kind"])

    print("== E 反例四：借视频帧、字幕裁干净了但没有权利依据 ==")
    frame = dict(full, slot_key="fr1",
                 materials=[{"ref": "F7", "url": "", "author": "某抖音视频",
                             "usage": "被引用的画面", "rights_basis": "", "review": "未审"}])
    gp.save(str(DB), frame)
    e = gp.load(str(DB), "fr1", "toutiao", "guide", 1)
    ef = [g for g in gp.compute_gaps(e) if g["blocks"] == "publish"]
    ck("判帧只证明画面干净，不代替权利审核（这里必须仍然挡）",
       any("使用依据" in g["what"] or "还没人审" in g["what"] for g in ef), str(fields(ef)))

    print("== F 反例五：旧记录缺字段一律「未审」，不默认通过 ==")
    ck("什么都没记 → 状态是「独立贡献未说明」而不是「通过」",
       gp.state({})[0] == gp.STATE_UNSET, gp.state({})[1])
    fg = fields(gp.compute_gaps({}))
    ck("缺项至少包含问题与贡献两栏", "question" in fg and "contribution" in fg, str(fg))
    ck("材料一条没有时，缺项里出现 claims（要人把结论对上出处）",
       "claims" in fg, str(fg))
    bg = gp.blocking_gaps({})
    ck("整条证据链没建过 → 直接算一项待发阻塞（没填 ≠ 通过）",
       len(bg) == 1 and bg[0]["field"] == "provenance", str([g["field"] for g in bg]))
    raw = {"claims": [{"text": "某条结论", "status": "", "evidence": ""}],
           "materials": [{"url": "x", "usage": "", "review": ""}], "question": "q",
           "contribution": "加了分流表", "contribution_evidence": "记录 1"}
    h = gp.compute_gaps(raw)
    ck("状态没填的结论默认算「参考转述」，不会被当成已核",
       "claims[1].evidence" not in fields(h), str(fields(h)))
    ck("用途没填的材料只算选题线索（不擅自升级成事实来源）",
       gp._norm_material({"url": "x"})["usage"] == "选题线索"
       and gp._norm_material({"url": "x"})["review"] == "未审")

    print("== G 补一项不得冲掉其他缺项；版本之间不互相覆盖 ==")
    gp.save(str(DB), dict(fake, slot_key="gg1"))
    g1 = fields([x for x in gp.compute_gaps(gp.load(str(DB), "gg1", "toutiao", "guide", 1))
                 if x["blocks"] == "publish"])
    gp.save(str(DB), dict(fake, slot_key="gg1",
                          claims=[dict(fake["claims"][0], evidence="temp/run_log.md",
                                       verifier="张亚军", vdate="2026-10-21"),
                                  dict(fake["claims"][1])]))
    g2 = fields([x for x in gp.compute_gaps(gp.load(str(DB), "gg1", "toutiao", "guide", 1))
                 if x["blocks"] == "publish"])
    ck("补了证据之后，站内查询冒充实测那条仍然在缺项里",
       "claims[1].evidence" not in g2 and "claims[2].evidence_kind" in g2, "%s → %s" % (g1, g2))
    gp.save(str(DB), dict(full, slot_key="vv1", version=1))
    gp.save(str(DB), dict(full, slot_key="vv1", version=2, question=""))
    v1 = gp.load(str(DB), "vv1", "toutiao", "guide", 1)
    v2 = gp.load(str(DB), "vv1", "toutiao", "guide", 2)
    ck("改第 2 版不动已开工的第 1 版（版本各存一份）",
       v1["question"].startswith("13") and v2["question"] == "", "v1=%s v2=%s" % (
           v1["question"][:8], repr(v2["question"])))

    print("== H 输出里不许出现原创/首发标记 ==")
    blob = gp.summary_line(got) + " " + " ".join(gp.STATE_LABELS.values())
    for bad in ("100%原创", "100% 原创", "已获原创", "可勾原创", "原创资格已确认", "首发"):
        ck("不出现「%s」" % bad, bad not in blob)

    os.remove(str(DB))
    if fails:
        print("\nPROVENANCE_FAIL %d 条：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("PROVENANCE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
