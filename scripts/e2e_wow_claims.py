# -*- coding: utf-8 -*-
"""机制断言闸（claim_check）的回归：主体＋技能＋数值＋条件＋带日期来源，缺一样都拦。

为什么单独一条线（wow-forever-web/docs/VERIFY-牛头人直接起飞修复-2026-11-02.md 交接）：
108 号海报把天裔的急速写成亡灵、把牛头人的原野疾驰写成"矮人接手移速"、把官方的
15 秒写成"魔网读取延到 15 分钟"、还替读者下了"兽人实为削弱/全职业都在亏"的结论。
专名闸只能证明"这个种族名存在"，老事实闸只认"上游标过待核的 token"——这四句的
token 本身没被标过，于是全都漏了。判据是**配对**：名字和数字各自存在不算核过，
同一句里"谁的技能、改成多少、什么条件"必须能在登记表里对上。

样本全部固定在仓库夹具（scripts/fixtures/），不指向会被重出覆盖的 output 路径，
不调用任何收费模型。

用法：python3 scripts/e2e_wow_claims.py    末行 ALL_OK 才算通过（全离线、0 次模型调用）
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import claim_check  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + str(detail)[:130]) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + str(detail)[:130]) if detail else ""))
        FAILS.append(name)


def bad_fixture():
    return json.loads((ROOT / "scripts" / "fixtures" / "wow_claims_108_bad.json")
                      .read_text(encoding="utf-8"))


def good_fixture():
    return json.loads((ROOT / "scripts" / "fixtures" / "wow_claims_108_good.json")
                      .read_text(encoding="utf-8"))


def run(fields):
    return claim_check.check_fields([(str(f.get("label") or "配文·copy_text"),
                                      str(f.get("text") or "")) for f in fields])


def main():
    reg = claim_check.load()

    print("== A 登记表自己合格：每条都带主体/技能/日期/原句，原句能在快照里逐字找到 ==")
    ck("登记表读得到且有条目", len(reg.get("claims") or []) >= 10,
       "%d 条" % len(reg.get("claims") or []))
    probs = claim_check.registry_problems(reg)
    ck("登记表自检无问题（状态、日期、原句逐字对得上快照）", not probs, probs[:2])
    ck("每条登记的来源都带日期", all("2026-" in str(e.get("source_date") or "")
                                     for e in reg["claims"]), "")
    ck("通用新闻首页不在任何一条登记里（逐条凭据要到具体帖）",
       not any(re_url.rstrip("/") == "https://wow.blizzard.cn/news"
               for re_url in [str(e.get("source_url") or "") for e in reg["claims"]]), "")

    print("== B 现行 108 的四类错必须拦（固定坏样本）==")
    bad = bad_fixture()
    for f in bad["must_block"]:
        r = run([f])
        ck("拦「%s」" % f["name"], bool(r["hard"]),
           r["hard"][0][:100] if r["hard"] else "没拦")
    # 整篇坏文本一起判：hard 覆盖四类，一条都不能少
    whole = run([{"label": f.get("label"), "text": f.get("text")}
                 for f in bad["must_block"]])
    joined = "、".join(whole["hard"])
    ck("整篇回放四类错都有命中点名", all(k in joined for k in
       ("不属于 矮人", "不属于 亡灵", "15分钟", "全职业")), "%d 条 hard" % len(whole["hard"]))

    print("== C 正确底稿必须全绿（含纠错句）==")
    good = good_fixture()
    gfields = claim_check.claim_fields(good["plan"], good["publish"])
    g = claim_check.check_fields(gfields)
    ck("正确底稿 0 条硬拦", not g["hard"], g["hard"][:2])
    ck("底稿里确实判到了机制断言（不是空转通过）", g["checked"] >= 15, "checked=%d" % g["checked"])
    for f in bad["corrections_must_pass"]:
        r = run([f])
        ck("纠错句不拦「%s」" % f["name"], not r["hard"], r["hard"][:1])

    print("== D 名字和数字各自存在 ≠ 核过（配对才有效）==")
    r = run([{"label": "配文·digest",
              "text": "矮人新增1命中，另有职业移速有叠加机制（改的是哪个种族没写清）"}])
    ck("矮人+移速同句（互相都存在）照样按主体错配拦", bool(r["hard"]), r["hard"][:1])
    r2 = run([{"label": "配文·digest", "text": "亡灵的内置冷却改了"}])
    ck("亡灵只提冷却不提数，走登记（墓穴之触在亡灵身上）不误拦", not r2["hard"], r2["hard"][:1])

    print("== E 人工放行的凭据：通用新闻首页不算逐条核验 ==")
    entry = [e for e in reg["claims"] if e["id"] == "skyborne_windblessed"][0]
    url = str(entry.get("source_url") or (reg.get("meta") or {}).get("source_url") or "")
    ok, miss = claim_check.evidence_ok(entry, "https://wow.blizzard.cn/news",
                                       "确认 3%→2%，2026-11-02 核")
    ck("通用新闻首页拒", not ok, (miss or [""])[0])
    ok, miss = claim_check.evidence_ok(entry, "官网看过", "确认改成2%")
    ck("「官网看过」这类没链接的自述拒", not ok, "")
    ok, miss = claim_check.evidence_ok(entry, "https://www.bilibili.com/video/BV1x",
                                       "口播说的，2026-10-27")
    ck("第三方转述拒（来源对不上登记）", not ok, "")
    ok, miss = claim_check.evidence_ok(entry, url,
                                       "官方原句 Wind-Blessed Haste 2%（was 3%），2026-10-26 核")
    ck("登记自己的具体帖＋日期＋结论过", ok, miss)
    ok, miss = claim_check.evidence_ok(entry, url, "改小了")
    ck("有链接但没写结论与日期拒", not ok, miss[:1])

    print("== F 接线：出包真走这道闸；表坏了要说「没判」，不演成通过 ==")
    ap = (ROOT / "src" / "ai_poster.py").read_text(encoding="utf-8")
    ck("generate_poster 调 claim_check.check_fields 并把 hard 并进 fact_hard",
       "claim_check.check_fields(" in ap and "claims.get(\"hard\")" in ap, "")
    ck("scope 外的稿子（PoE）明确说「没判」，不等于查过没问题",
       ("in_scope(scope, reg)" in ap or "in_scope(scope)" in ap) and "不等于查过没问题" in ap, "")
    ck("放行走加严判据（出处必须是登记自己的具体帖＋日期）",
       "evidence_validator" in ap and "extra=_cc.evidence_validator()" in ap, "")
    ck("登记表自身的问题也露到成品记录（registry_problems）",
       "registry_problems" in ap, "")
    broken = dict(reg)
    broken["claims"] = [dict(reg["claims"][0], status="没核")]
    r = claim_check.check_fields([("配文·copy_text", "牛头人原野疾驰每层2%，上限60%")], reg=broken)
    ck("登记表不合格 → 整项按缺席判（skipped 说出口）",
       bool(r.get("skipped")) and "不合格" in r["skipped"], r.get("skipped"))
    ck("scope 判定：魔兽稿在内、PoE 稿不在",
       claim_check.in_scope("魔兽无限服种族改动") and not claim_check.in_scope("POE 圣物加成"), "")

    print("== G 原子断言：数值只跟同一小句的技能对账，整句并集放不走交换反例 ==")
    # 2026-11-02 复验阻塞③：旧实现整句取 allowed 并集，「牛头人 15%；天裔 60%」
    # 每个数都"存在"就掩护了"数字挂错技能"。现在按小句（逗号/顿号/分号）分别核。
    r = run([{"label": "图卡", "text": "牛头人原野疾驰移速提高到15%；天裔天穹视界移速提高到60%。"}])
    ck("固定反例（数值交换）拦", bool(r["hard"]), r["hard"][:1])
    ck("拦的话点名了无出处的数", "15%、60%" in "、".join(r["hard"]), "")
    r = run([{"label": "图卡", "text": "天裔原野疾驰移速提高到2%；牛头人天穹视界移速提高到15%。"}])
    ck("交换主体反例拦（技能挂错种族）", bool(r["hard"]), r["hard"][:1])
    r = run([{"label": "图卡", "text": "牛头人原野疾驰移速提高到2%；天裔天穹视界移速提高到15%。"}])
    ck("同构正例（数值各归各）不拦", not r["hard"], r["hard"][:1])
    r = run([{"label": "图卡", "text": "原野疾驰每层2%，上限60%，骑乘不受益"}])
    ck("继承链：小句没重复技能名时沿用上一小句的锚点", not r["hard"], r["hard"][:1])

    print()
    if FAILS:
        print("FAILED %d 条：%s" % (len(FAILS), "、".join(FAILS)))
        return 1
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
