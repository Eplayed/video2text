# -*- coding: utf-8 -*-
"""图文攻略事实闸口的反向回归：拿三篇**已经发出去的错成品**当夹具，证明闸口真能拦住它们。

为什么用真成品而不是造样本（2026-10-21）：号主抽查 87/85/83 后确认这些缺陷已经进了线上文字
（`output/toutiao/_platform_items.json` 的快照能证），拿它们当夹具是唯一能证明
"下一次不会再犯"的办法。只跑旧门禁全绿不代表这些新问题被修好——旧门禁压根没读过【风险核查】。

三条纪律：
· **只读**：库用 mode=ro，成品读磁盘上的 manifest.json，不写生产库、不改任何稿件；
· **不花钱**：全程不调模型（闸口是纯函数），`_clip_publish` 用内存里的假 publish 验；
· 失败要能分清是"内容真有问题"还是"夹具签名不匹配"（读不到 manifest / 库里没这条 → 单独报 FIXTURE）。

用法：env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/e2e_guide_gate.py
"""
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "output", "video2text.db")
POSTER = os.path.join(ROOT, "output", "poster")

fails = []
FIX = []


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + detail) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + detail) if detail else ""))
        fails.append(name)


def load(sid):
    """夹具：manifest + 整合稿原文。读不到就记 FIXTURE，别混进内容判据里。"""
    mf = os.path.join(POSTER, str(sid), "manifest.json")
    if not os.path.isfile(mf):
        FIX.append("整合稿 %s 的 manifest 读不到（%s）" % (sid, mf))
        return None, None
    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT content, title FROM ai_summaries WHERE id=?", (sid,)).fetchone()
    finally:
        conn.close()
    if not row:
        FIX.append("库里没有整合稿 %s" % sid)
        return None, None
    m = json.load(open(mf, encoding="utf-8"))
    plan = dict(m.get("plan") or {})
    plan["publish"] = m.get("publish") or {}
    return {"plan": plan, "pub": m.get("publish") or {}, "manifest": m}, row["content"]


def main():
    from src import ai_poster as ai
    from src import fact_gate

    # 预检：本脚本要的是「ai_poster 已经接上闸口」的那一版。接线（提示词 __RISKS__、
    # generate_poster 阻断、_clip_publish 不再硬切）和另一条会话 10-16 那份未提交的
    # 「发布标题/小红书分叉」长在同一个函数上，拆不出干净的一刀，所以可能先只有模块进了仓库。
    # 少了预检，干净检出上跑出来的三条 FAIL 会被误读成"内容判据退化了"。
    missing = [n for n in ("risks_prompt_block", "plan_is_guide", "_publish_view")
               if not hasattr(ai, n)]
    missing += ["check_override"] if not hasattr(fact_gate, "check_override") else []
    if missing:
        print("!! 前置缺失：%s 还没有 %s —— 闸口的接线没在这份检出里，"
              "下面的 E/F/H/I 四段必然红，那不是内容判据退化，是接线未提交。"
              % ("src/ai_poster.py 或 src/fact_gate.py", "、".join(missing)))

    print("── A. 三篇错成品必须被拦（87 的 46亿 / 85 的 +20% / 83 的毛边全消）──")
    for sid, needle, why in (
            (87, "46亿", "整理稿写着疑似转写或口误，成品却拿它当卡片标题"),
            (85, "20%", "整理稿写明只是口播举例，成品把它印成副标题与大字"),
            (83, "毛边全消", "整理稿第 8/9 条说没有实测数据、不承诺")):
        pack, content = load(sid)
        if not pack:
            continue
        hard, soft = fact_gate.check(pack["plan"], content)
        hit = [h for h in hard if needle in h]
        ck("%d 待核事实被拦" % sid, bool(hit),
           "%s｜命中：%s" % (why, (hit[0][:70] if hit else "无（闸口漏了）")))

    print("── B. 配文断头句必须被抓（83/80 都是代码 [:260] 切的）──")
    for sid, tail in ((83, "帧数提升幅"), (80, "再决定怎")):
        pack, content = load(sid)
        if not pack:
            continue
        cut = fact_gate.copy_is_cut(pack["pub"].get("copy_text") or "")
        ck("%d 配文截断被抓" % sid, bool(cut), cut or "判成完整（漏了）")
        ck("%d 截断点就是当年那半句" % sid, tail in cut, "尾巴：%s" % tail)

    print("── C. 83 那条内部矛盾要进人工清单（标题「别一次拉满」vs 说明「推荐拉满」）──")
    pack, content = load(83)
    if pack:
        hard, soft = fact_gate.check(pack["plan"], content)
        contra = [s for s in soft if "相反口径" in s]
        ck("83 视野矛盾进人工清单", bool(contra), contra[0][:78] if contra else "没抓到")
        gaps = fact_gate.guide_gaps(pack["plan"])
        ck("83 攻略五段结构有判定结果", isinstance(gaps, list),
           "缺：%s" % ("、".join(gaps) or "无"))

    print("── D. 健康样本不许被一律拦（放行侧也要有断言）──")
    # 66 从这一组里挪走了：2026-10-29 加了机制证据门之后它**该被拦**——
    # 它的清单写着「需确认"后台系统"具体指代的产品…口播原文为"字节或者月之暗面在服务器上的程序"」，
    # 而成品把「后台系统」印进了卡片2说明、卡片3标题和配文。那句"可保留"是编辑的判断，
    # 不是可定位证据；按号主这轮定的口径（风险只能凭证据解除），这就是要停下来的样子。
    # 放行侧的断言仍然留着（74/63），闸写宽到全拦会被这两条抓到。
    for sid in (74, 63):
        pack, content = load(sid)
        if not pack:
            continue
        hard, _soft = fact_gate.check(pack["plan"], content)
        cut = fact_gate.copy_is_cut(pack["pub"].get("copy_text") or "")
        ck("%d 健康样本放行" % sid, (not hard) and (not cut),
           "hard=%d cut=%s" % (len(hard), cut or "无"))

    pack66, content66 = load(66)
    if pack66:
        h66, _s66 = fact_gate.check(pack66["plan"], content66)
        ck("66 含疑转写被印进成品 → 机制证据门要停（上游那句「可保留」不算证据）",
           bool(h66) and "后台系统" in "；".join(h66), (h66 or [""])[0][:70])

    print("── E. 工程 bug 本身：配文不再被长度硬切，改成报出来 ──")
    from src import ai_poster
    long_text = "开头一句。" * 40          # 200 字整，够不到 260 线
    longer = "这是一句会被旧代码切掉的长话。" * 30
    p1 = ai_poster._clip_publish({"copy_text": long_text, "digest": "", "source_note": ""}, {})
    ck("260 字内的配文原样保留", p1["copy_text"] == long_text,
       "%d 字" % len(p1["copy_text"]))
    p2 = ai_poster._clip_publish({"copy_text": longer, "digest": "", "source_note": ""}, {})
    ck("超长不再被切成断头句", len(p2["copy_text"]) == len(longer),
       "原 %d 字 → 存 %d 字" % (len(longer), len(p2["copy_text"])))
    ck("超长改为报出来", any("配文" in w and "字" in w for w in p2["warnings"]),
       "；".join(p2["warnings"])[:70])

    print("── F. 拆解提示词里要真的带上待核清单（不靠模型自觉）──")
    pack87, content87 = load(87)
    if pack87:
        conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
        row = conn.execute("SELECT content FROM ai_summaries WHERE id=87").fetchone()
        conn.close()
        block = ai_poster.risks_prompt_block({"content": row[0]})
        ck("提示词带上了 46亿 那条", "46亿" in block and "不许当卖点" in block,
           "块长 %d 字" % len(block))
        empty = ai_poster.risks_prompt_block({"content": "【正文】没有任何风险节"})
        ck("没有风险节时也有一句兜底", "没有【风险核查】" in empty, empty[:40])

    # 2026-10-29：号主发出去的「9职业最强专精」被读者抓到两处（愈合祷言 40%/200%、切目标不掉连击点）。
    # 查下来整理稿**列了**待核，是机器一条都没读到——这两条夹具就是那两个形状，别换成当天的真稿。
    md_style = ("## 六、事实核查清单\n"
                "- [ ] 神牧愈合祷言“每次弹跳递增40%、吃满200%法强收益”的具体数值。\n"
                "- [ ] 战斗贼平缓持续回能、毒药吃属性、切目标不掉连击点。\n"
                "## 配图素材（视频关键帧）\n![关键帧1·00:03](/media/a.jpg)\n")
    got = fact_gate.risk_items(md_style)
    ck("标题写成「事实核查清单」也认（以前只认字面「风险核查」，整节读不到）",
       len(got) == 2, str([g["raw"][:18] for g in got]))
    ck("下一节「## 配图素材」的图片行不许被当风险项抓走",
       not any("jpg" in g["raw"] or "关键帧" in g["raw"] for g in got),
       str([g["raw"][:20] for g in got]))
    ck("待核里的数字抽成 token（成品上出现 40% 才比得中）",
       any("40%" in g["tokens"] for g in got), str(got[0]["tokens"] if got else ""))
    inline = "【风险核查】8000 金币那条待核，不许写成建议\n\n【来源】某号"
    ck("标记和内容写在同一行也取得到（以前整行被当标题丢掉）",
       [i["raw"] for i in fact_gate.risk_items(inline)] == ["8000 金币那条待核，不许写成建议"],
       str([i["raw"] for i in fact_gate.risk_items(inline)]))
    done = ("## 事实核查清单\n"
            "- [ ] 实名核对：三个技能名均与上游数据库一致，已保留标准写法。\n"
            "- [ ] 40% 这个数值需要游戏内核对后再做投入。\n")
    ck("自己声明「已核过」的记录不进待核清单（噪音大了闸会被关掉）",
       len(fact_gate.risk_items(done)) == 1 and "40%" in fact_gate.risk_items(done)[0]["raw"],
       str([i["raw"][:22] for i in fact_gate.risk_items(done)]))
    blk = ai_poster.risks_prompt_block({"content": md_style})
    ck("这两条真的进了拆解提示词的「不许当卖点」那一段",
       "愈合祷言" in blk and "连击点" in blk and "不许当卖点" in blk, "块长 %d 字" % len(blk))

    print("── G. 老成品只列清单、绝不改写 ──")
    rows = []
    for sid in (87, 85, 83, 80):
        pack, content = load(sid)
        if pack:
            rows.append({"id": sid, "title": "整合稿%d" % sid,
                         "plan": pack["plan"], "content": content, "pub": pack["pub"]})
    rep = fact_gate.legacy_report(rows)
    ck("四篇老成品列出待人工处理清单", len(rep) >= 3, "%d 条" % len(rep))
    for line in rep:
        print("       · " + line[:96])

    print("── H. 实际会发出去的字段也要查（挪进发布标题/小红书正文不能绕过闸口）──")
    if hasattr(ai, "_publish_view"):
        conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
        row87 = conn.execute("SELECT content FROM ai_summaries WHERE id=87").fetchone()
        conn.close()
        c87 = (row87[0] if row87 else "") or ""
        if "46亿" in c87:
            # 图面一个字都没用那颗待核数，风险数只写在**发布标题**里（Codex P2 点名的形状）
            clean_plan = {"title": "传承点数怎么花",
                          "cards": [{"t": "先看三个池子", "d": "各池独立结算，以游戏内为准"}]}
            pub_title = {"copy_text": "三个池子分开结算，先看清楚再花。",
                         "tt_titles": ["传承点数还能卖46亿？"], "xhs_copy": "", "digest": ""}
            h, s = fact_gate.check(ai._publish_view(clean_plan, pub_title), c87)
            ck("待核数只写在发布标题 → 仍算卖点、拦住", any("46亿" in x for x in h),
               (h[0][:70] if h else "没拦（闸口只读图面字段）"))
            ck("报的位置点名是哪条字段（人知道改哪儿）",
               any("tt_titles" in x for x in h), "；".join(h)[:70])
            pub_narr = {"copy_text": "三个池子分开结算，先看清楚再花。",
                        "tt_titles": [], "xhs_copy": "顺带一提那个 46亿 的数还要核。", "digest": ""}
            h2, s2 = fact_gate.check(ai._publish_view(clean_plan, pub_narr), c87)
            ck("同一颗数只写在正文叙述里 → 不拦，但进「要人看一眼」",
               not h2 and any("46亿" in x for x in s2),
               "hard=%d soft=%s" % (len(h2), (s2[0][:50] if s2 else "无")))
            ck("合并视图不改原 plan（不把人改过的文案悄悄加字段）",
               "publish" not in clean_plan, str(list(clean_plan.keys())))
        else:
            FIX.append("整合稿 87 的原文里没有 46亿（内容变了？），H 段没跑")
    print("── I. 人工放行必须逐条留凭据（一个 force=True 不等于已核实）──")
    if hasattr(fact_gate, "check_override"):
        hard_i = ["「46亿」进了 主标题（还能卖46亿），可整理稿【风险核查】写着：疑似转写或口误"]
        ov0 = fact_gate.check_override(hard_i, None)
        ck("没给凭据 → 不算核过，并且点名缺哪条",
           ov0["complete"] is False and len(ov0["missing"]) == 1,
           "missing=%d" % len(ov0["missing"]))
        ov1 = fact_gate.check_override(hard_i, [{"item": hard_i[0], "source": "官方公告 2026-09-30",
                                                 "result": "实际口径是 4.6 亿/周"}], "号主")
        ck("逐条给了出处 + 结论 → 算核过，凭据留在结果里",
           ov1["complete"] is True and ov1["rows"][0]["ok"] and ov1["checked_by"] == "号主",
           json.dumps(ov1["rows"][0], ensure_ascii=False)[:80])
        ov2 = fact_gate.check_override(hard_i, [{"item": hard_i[0], "source": "官方公告",
                                                 "result": "  "}])
        ck("只有出处没有结论 → 仍算没核过", ov2["complete"] is False,
           "missing=%s" % (ov2["missing"][0][:30] if ov2["missing"] else "无"))
        ov3 = fact_gate.check_override(hard_i, [{"item": "另一条不相干的", "source": "a",
                                                 "result": "b"}])
        ck("凭据对不上号的那条不算数（不许拿别的条目凑齐）", ov3["complete"] is False,
           "missing=%d" % len(ov3["missing"]))
        two = hard_i + ["「20%」进了 大字数字，整理稿写明只是口播举例"]
        ov4 = fact_gate.check_override(two, [{"item": hard_i[0], "source": "a", "result": "b"}])
        ck("拦下两条只填一条 → 仍然 incomplete（放行要逐条，不是一句总括）",
           ov4["complete"] is False and len(ov4["missing"]) == 1,
           "missing=%d" % len(ov4["missing"]))

    if FIX:
        print("\n夹具问题（不算内容判据失败）：\n  - " + "\n  - ".join(FIX))
        return 2
    if fails:
        print("\n发现 %d 处问题：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("\nGUIDE_GATE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
