# -*- coding: utf-8 -*-
"""e2e_xhs_fact_gate.py — 小红书图文的事实发布闸验收（2026-10-29 那一单）。

固定反例（号主点名要用这四样，别换成编的）：
  素材 309874 = 抖音 @一起玩魔兽(一条大菜狗) 2026-10-24 口播「每个职业最强专精盘点」
  旧稿 98     = 同一素材 10-26 的整理稿，列了 11 条待核（含读者抓的那两条）
  新稿 103    = 10-29 重跑的整理稿，**漏写**了那两条
  成品 103    = `output/poster/103/plan.json`，已发的小红书图文（两条都写成了确定结论）

三条验收对应三组断言：
  ① 原 103 成品在**没有新证据**时被拦；
  ② 新稿**省略**待核清单仍被拦（靠台账继承，不靠模型自觉）；
  ③ 给出**可定位证据**、或**删改争议断言**后能通过。
另测：一手文本里有同一句 → 只提示不拦；日期三件事分开写；放行记录绑哈希、改稿即失效。

**全程本地**：临时库副本、不调任何模型、不发布。
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import ai_poster, fact_gate, risk_ledger, release_review   # noqa: E402

DB = ROOT / "output" / "video2text.db"
PLAN103 = ROOT / "output" / "poster" / "103" / "plan.json"
MATERIAL_ID = 309874
OFFICIAL_URL = "https://wow.blizzard.cn/news/24301514"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    print("  [%s] %s%s" % ("OK " if cond else "XX ", name, ("  " + detail) if detail else ""))


def load_fixtures():
    """反例必须齐；缺一样就是环境不对，不许静默跳过。"""
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    con.row_factory = sqlite3.Row
    old = con.execute("SELECT content FROM ai_summaries WHERE id=98").fetchone()
    new = con.execute("SELECT content FROM ai_summaries WHERE id=103").fetchone()
    mat = con.execute("SELECT id, author, source_sheet, published_at FROM videos WHERE id=?",
                      (MATERIAL_ID,)).fetchone()
    con.close()
    for label, row in (("旧稿 98", old), ("新稿 103", new), ("素材 309874", mat)):
        if not row:
            raise SystemExit("反例缺 %s —— 这套验收不许在缺料的情况下报通过" % label)
    plan = json.loads(PLAN103.read_text(encoding="utf-8"))
    view = dict(plan["plan"])
    view["publish"] = plan.get("publish") or {}
    return old["content"], new["content"], dict(mat), view


def main():
    tmp = Path(tempfile.mkdtemp(prefix="xhs-gate-"))
    db = tmp / "video2text.db"
    shutil.copyfile(DB, db)
    content98, content103, material, view = load_fixtures()
    pub = view["publish"]

    print("\n== 0. 反例本身长什么样（钉住事实，别后面改了都不知道）==")
    r98 = fact_gate.risk_items(content98)
    r103 = fact_gate.risk_items(content103)
    check("旧稿 98 列了 11 条待核，含读者抓的两条",
          len(r98) == 11 and any("愈合祷言" in i["raw"] for i in r98)
          and any("连击点" in i["raw"] for i in r98), "读到 %d 条" % len(r98))
    check("新稿 103 漏写了那两条（这就是事故现场）",
          not any("愈合祷言" in i["raw"] for i in r103)
          and not any("连击点" in i["raw"] for i in r103), "读到 %d 条" % len(r103))
    check("成品 103 的小红书正文把两条写成了确定结论",
          "200%" in (pub.get("xhs_copy") or "") and "切目标不掉连击点" in (pub.get("xhs_copy") or ""),
          (pub.get("xhs_copy") or "")[:46])

    print("\n== 1. 台账：入账、继承、幂等 ==")
    risk_ledger.ensure(str(db))
    n = risk_ledger.record_draft(str(db), [MATERIAL_ID], 98, r98)
    open_items = risk_ledger.open_items(str(db), [MATERIAL_ID])
    check("旧稿的 11 条进了台账（按素材记，不按稿记）", n >= 11 and len(open_items) >= 11,
          "入账 %d / open %d" % (n, len(open_items)))
    kinds = set(i["kind"] for i in open_items)
    check("机制数值与二元机制分档（发布闸只对这两档硬拦）",
          "机制数值" in kinds and "二元机制" in kinds, str(sorted(kinds)))
    md, added = risk_ledger.inherit_into(content103, open_items)
    check("新稿漏写的条目被并进正文（漏写不等于风险消失）",
          added >= 2 and "愈合祷言" in md and "连击点" in md, "并回 %d 条" % added)
    check("并进的是同一节清单，解析器读得到（写在文末等于没写）",
          len(fact_gate.risk_items(md)) > len(r103),
          "%d → %d 条" % (len(r103), len(fact_gate.risk_items(md))))
    md2, added2 = risk_ledger.inherit_into(md, open_items)
    check("重复继承不长重复条目（幂等）", added2 == 0 and md2 == md, "第二次并回 %d 条" % added2)

    print("\n== 2. 验收①：原 103 成品，没有新证据 → 必须拦 ==")
    hard, soft = fact_gate.check(view, content103, extra_items=open_items)
    joined = "；".join(hard)
    check("硬拦不为空（这篇再过一遍闸就发不出去）", len(hard) >= 2, "hard=%d" % len(hard))
    check("点名愈合祷言那组数值", "200%" in joined or "愈合祷言" in joined, joined[:80])
    check("点名「切目标不掉连击点」这种二元机制结论（没数字也拦得住）",
          "连击点" in joined, [h[:36] for h in hard if "连击点" in h][:1])
    check("报的位置覆盖小红书正文与图上文字",
          "xhs_copy" in joined or "第" in joined, joined[80:170])
    check("话说清了解除路径（补原句／带版本日期的实测／删改）",
          all(("官方" in h or "实测" in h) and "删" in h for h in hard), "")

    print("\n== 3. 验收②：不接台账（只有新稿自己那份漏写的清单）→ 洞在哪 ==")
    h0, s0 = fact_gate.check(view, content103)
    check("不接台账时一条都拦不住（所以继承必须做在稿子外面，不靠模型自觉）",
          h0 == [] and s0 == [], "hard=%d soft=%d" % (len(h0), len(s0)))
    h1, s1 = fact_gate.check(view, md)
    check("继承写回正文后，调用方不传台账也照样拦", len(h1) >= 2, "hard=%d" % len(h1))

    print("\n== 4. 一手文本反查：公告里有同一句 → 只提示，不拦 ==")
    blob = ("牧师天赋改动：神牧的愈合祷言现在每次弹跳递增40%、吃满200%法强收益，"
            "战斗贼切目标不掉连击点，毒药正常吃属性加成。")
    hard2, soft2 = fact_gate.check(view, content103, extra_items=open_items, official_blobs=[blob])
    check("一手文本里有这两句时不再硬拦（降级成按一手发的提示）",
          len(hard2) < len(hard) and any("一手文本" in x for x in soft2),
          "hard %d → %d" % (len(hard), len(hard2)))
    fake = ["愈合祷言的法力值消耗降低；治疗祷言会治疗小队成员。"]
    check("库里真实公告那种（提到技能但没有这两组数值）不能误放行",
          len(fact_gate.check(view, content103, extra_items=open_items,
                              official_blobs=fake)[0]) >= 2, "")

    print("\n== 5. 验收③：可定位证据解除 / 删改争议断言 ==")
    ids = [i["id"] for i in open_items if "愈合祷言" in i["raw"] or "连击点" in i["raw"]]
    bad = risk_ledger.resolve(str(db), ids, "我看过了", "没问题")
    check("含糊证据（没出处、没版本日期）解除不了，且一条都不生效",
          not bad["ok"] and bad["resolved"] == 0 and bad["missing"],
          json.dumps(bad, ensure_ascii=False)[:100])
    good = risk_ledger.resolve(str(db), ids,
                               "官方公告《无限职业深度解析——牧师与战士》" + OFFICIAL_URL,
                               "公告原文为每跳递增 40%，与口播一致（2026-10-29 核对）")
    check("给到可定位一手出处 + 结论 → 解除成功", good["ok"] and good["resolved"] == len(ids),
          json.dumps(good, ensure_ascii=False)[:80])
    left = risk_ledger.open_items(str(db), [MATERIAL_ID])
    hard3, _ = fact_gate.check(view, content103, extra_items=left)
    check("解除之后那两条不再拦（其余待核仍在账上）",
          "愈合祷言" not in "；".join(hard3) and len(left) < len(open_items),
          "open %d → %d, hard=%d" % (len(open_items), len(left), len(hard3)))
    view_clean = json.loads(json.dumps(view))
    view_clean["publish"]["xhs_copy"] = "神牧和战斗贼这版手感有变化，具体数值以游戏内实测为准。"
    view_clean["publish"]["xhs_titles"] = ["无限服九职业怎么选"]
    for pg in (view_clean.get("pages") or []):
        for c in (pg.get("cards") or []):
            c["d"] = "作者称有调整，进游戏核对后再练号"
    hard4, _ = fact_gate.check(view_clean, content103, extra_items=open_items)
    check("只删小红书那份仍拦：头条配文里留着同一句，两份配文是同一条链",
          len(hard4) >= 1 and any("copy_text" in h for h in hard4),
          "hard=%d %s" % (len(hard4), (hard4 or [""])[0][:60]))
    view_clean["publish"]["copy_text"] = "九职业各自的手感有变化，数值以游戏内为准。"
    view_clean["publish"]["digest"] = "九职业专精选择，数值待游戏内核对。"
    view_clean["publish"]["tt_titles"] = ["无限服九职业怎么选"]
    hard5, _ = fact_gate.check(view_clean, content103, extra_items=open_items)
    check("删改争议断言后能通过（不必先有证据，删掉就是合规）",
          hard5 == [], "hard=%d %s" % (len(hard5), (hard5 or [""])[0][:70]))

    print("\n== 6. 不做全局关键词封杀 ==")
    view_other = {"title": "无限服9职业最强专精", "pages": [
        {"title": "九个专精一次看完", "cards": [{"t": "猎人", "d": "生存猎重做，急速射击给 40% 急速"}]}],
        "publish": {"xhs_copy": "这篇按职业拆开讲，共 9 个专精", "xhs_titles": ["9 职业一次看完"]}}
    hh, _ss = fact_gate.check(view_other, content103, extra_items=[])
    check("上游没标过的数字出现在标题/正文里不拦（闸的入口是标过没有）",
          hh == [], "hard=%d" % len(hh))

    print("\n== 7. 来源那一行：三个日期各说各的 ==")
    ctx = {"materials": [{"author": material["author"], "sheet": material["source_sheet"],
                          "published_at": material["published_at"], "first_party": False,
                          "is_video": True}],
           "open_risks": ["神牧愈合祷言每次弹跳递增40%"], "resolved": [],
           "generated_at": "2026-10-29"}
    note = ai_poster.compose_source_note(ctx)
    check("点名来源号 + 第三方口播 + 素材发布日（不是生成日）",
          material["author"] in note and "第三方口播" in note and "2026-10-24" in note, note[:76])
    check("未核验时明写没核，绝不出现「截至生成日」那种话",
          "事实核验：无" in note and "截至" not in note, note[-80:])
    check("生成日单独标，不与素材日混为一谈", "本稿生成于 2026-10-29" in note, "")
    note2 = ai_poster.compose_source_note(
        dict(ctx, resolved=[{"raw": "愈合祷言", "source": "官方公告 24301514"}], open_risks=[]))
    check("核过之后才写已核，并带出处", "已核 1 条" in note2 and "24301514" in note2, note2[:100])
    note3 = ai_poster.compose_source_note({}, "整理自公开分享")
    check("没接台账的老路径不自己编日期（原样交回，不假装）", note3 == "整理自公开分享", note3)

    print("\n== 8. 放行记录：绑哈希，改稿即失效 ==")
    pkg = tmp / "poster" / "103"
    pkg.mkdir(parents=True)
    (pkg / "poster_p1.png").write_bytes(b"\x89PNG fake-1")
    man = {"publish": pub, "pages": [{"file": "poster_p1.png", "chars": 120}]}
    h_a = release_review.package_hash(str(pkg), man)
    st0 = release_review.status(str(pkg), "xhs", h_a)
    check("没签过时只有小红书这一档被挡（头条不加戏）",
          release_review.blocks_publish("xhs", st0) and not release_review.blocks_publish("toutiao", st0),
          json.dumps(st0, ensure_ascii=False)[:56])
    no_sign = release_review.record(str(pkg), "xhs", h_a, "", "顺手过一下")
    check("署名空着不算放行（没有谁签的记录没法追）", not no_sign["ok"], no_sign.get("error", ""))
    release_review.record(str(pkg), "xhs", h_a, "号主", "逐页读过，两处待核已删")
    st1 = release_review.status(str(pkg), "xhs", h_a)
    check("签完之后这一版算放行", st1["reviewed"] and st1["hash_matches"] and not st1["stale"], "")
    man2 = json.loads(json.dumps(man))
    man2["publish"]["xhs_copy"] = "改了一个字"
    h_b = release_review.package_hash(str(pkg), man2)
    st2 = release_review.status(str(pkg), "xhs", h_b)
    check("改稿后哈希变、旧放行作废",
          h_b != h_a and st2["reviewed"] and not st2["hash_matches"] and st2["stale"],
          "%s → %s" % (h_a, h_b))
    (pkg / "poster_p1.png").write_bytes(b"\x89PNG fake-2")
    check("只换图也算改稿（图也是读者看到的东西）",
          release_review.package_hash(str(pkg), man) != h_a, "")

    print("\n━━ 小红书事实发布闸：%d 项通过 / %d 项失败 ━━" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败：%s" % FAIL)
        return 1
    print("ALL_OK 无证据拦得住、漏写清单也拦得住、给证据或删改能过，日期与放行各自留痕")
    return 0


if __name__ == "__main__":
    sys.exit(main())
