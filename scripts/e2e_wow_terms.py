# -*- coding: utf-8 -*-
"""实体专名闸的回归：成品里的种族/职业名，素材与官方枚举都没有就拦。

为什么单独一条线（号主 2026-11-02 抓的错）：图文攻略卡片写了「德莱尼主动技能持续30秒变15秒」，
而口播 0 次、补充资料 0 次、官网种族名单 0 次——名字是模型安的，读者第一句就问"哪来的德莱尼"。
此前海报这条线**一次都没跑过实名核对**（只有头条图文卡线跑），而名单本身还冻在 10-10
（上游把 `src/` 改名 `app/` 之后同步脚本直接起不来，里面连"天裔"都没有）。

三条口径都要钉住，尤其反面：
1. 只有**官方完整枚举**的类别（种族/职业）能当硬判据；配方/天赋那类是残表，库里没有 ≠ 写错；
2. 名单没同步、检测表读不到、**没交素材原文** → 这道闸一律"没判"，不许把"没判"演成"通过"，
   更不许拿它拦好稿（11-01 那条老教训：降级成兜底不等于关掉那条路，兜底也要有来源判据）；
3. 长名优先挖掉再扫：「萨满祭司」不许被短名「萨满」切出误报。

用法：python3 scripts/e2e_wow_terms.py     末行 ALL_OK 才算通过（全离线、0 次模型调用）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import term_check  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + str(detail)[:150]) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + str(detail)[:150]) if detail else ""))
        FAILS.append(name)


def fixture(enum_races=("人类", "兽人", "牛头人", "矮人", "天裔"),
            enum_classes=("战士", "法师", "牧师", "潜行者", "猎人", "圣骑士", "术士",
                          "德鲁伊", "萨满祭司"),
            with_enum=True, detector=True):
    """造一份临时名单 + 临时检测表，并把模块的两个路径指过去（测完还原）。"""
    wd = tempfile.mkdtemp(prefix="wow_terms_")
    terms = {}
    for n in enum_races:
        terms[n] = {"kind": "种族", "level": "L0", "official_enum": True, "src": "官方枚举"}
    for n in enum_classes:
        terms[n] = {"kind": "职业", "level": "L0", "official_enum": True, "src": "官方枚举"}
    terms["瞄准射击"] = {"kind": "天赋", "level": "L0", "src": "hunter"}   # 非枚举类：不参与硬判
    if not with_enum:
        terms = {"瞄准射击": {"kind": "天赋", "level": "L0"}}
    tf = os.path.join(wd, "wow_terms.json")
    with open(tf, "w", encoding="utf-8") as f:
        json.dump({"meta": {"terms": len(terms)}, "terms": terms, "aliases": {}}, f,
                  ensure_ascii=False)
    nf = os.path.join(wd, "wow_names.json")
    if detector:
        with open(nf, "w", encoding="utf-8") as f:
            json.dump({"races": list(term_check._detector()[0]) or
                       ["人类", "兽人", "牛头人", "矮人", "天裔", "德莱尼", "血精灵", "亡灵"],
                       "classes": ["战士", "法师", "牧师", "潜行者", "猎人", "圣骑士", "术士",
                                   "德鲁伊", "萨满祭司", "萨满", "盗贼"]}, f, ensure_ascii=False)
    old = (term_check.TERMS_FILE, term_check.NAMES_FILE)
    term_check.TERMS_FILE = Path(tf)
    term_check.NAMES_FILE = Path(nf if detector else os.path.join(wd, "missing.json"))
    term_check._cache["sig"] = None                      # 换文件必须让缓存失效，否则读的还是旧的
    term_check._names_cache["sig"] = None
    return old


def restore(old):
    term_check.TERMS_FILE, term_check.NAMES_FILE = old
    term_check._cache["sig"] = None
    term_check._names_cache["sig"] = None


def suspects(draft, source):
    return [s["name"] for s in term_check.roster_check(draft, source).get("suspect") or []]


def main():
    print("== A 抓到真错：素材与官方名单都没有的种族名 ==")
    old = fixture()
    try:
        src = "牛头人这次加强，人类被砍，矮人新增命中，天裔的急速降到 2%。"
        ck("「德莱尼」这种素材没提、官方名单也没有的名字 → 判为可疑",
           suspects("德莱尼主动技能持续30秒变15秒。", src) == ["德莱尼"], "")
        ck("素材与名单里都有的名字一律放行（不许顺手多拦）",
           suspects("牛头人、天裔、人类这次都有改动。", src) == [], "")
        r = term_check.roster_check("德莱尼主动技能持续30秒变15秒。", src)
        ck("报告里点名它是种族（人要能一眼看出错在哪一类）",
           r["suspect"][0]["kind"] == "种族", r["suspect"])
        ck("同时给出官方名单里现有什么（改稿的人不用再去查）",
           "天裔" in (r.get("ok") or []) or "天裔" in "、".join(term_check.enum_names()), "")
    finally:
        restore(old)

    print("== B 长名优先：短名不许切碎长名 ==")
    old = fixture()
    try:
        ck("「萨满祭司」在官方名单里，就不该被短名「萨满」误报",
           suspects("萨满祭司这次改了图腾。", "萨满祭司的图腾改动") == [], "")
        ck("反面：素材与名单都只有「萨满祭司」，稿里写「萨满」也算对（同一名写法不拦）",
           suspects("萨满的图腾改了。", "萨满祭司的图腾改动") == [], "")
        ck("真没提过的职业名照样抓得出来",
           suspects("盗贼这次很强。", "战士与法师的改动") == ["盗贼"], "")
    finally:
        restore(old)

    print("== C 三种「没判」必须说出口，不许演成通过、更不许拦好稿 ==")
    old = fixture()
    try:
        r = term_check.roster_check("德莱尼主动技能。", None)
        ck("没交素材原文 → 不判（只比名单会把素材里的新名字误杀）",
           r["suspect"] == [] and "素材原文" in r["skipped"], r["skipped"])
    finally:
        restore(old)
    old = fixture(with_enum=False)
    try:
        r = term_check.roster_check("德莱尼主动技能。", "牛头人加强")
        ck("名单里没有官方枚举条目（没同步）→ 不判并说清要跑哪条命令",
           r["suspect"] == [] and "sync_wow_icons" in r["skipped"], r["skipped"])
    finally:
        restore(old)
    old = fixture(detector=False)
    try:
        r = term_check.roster_check("德莱尼主动技能。", "牛头人加强")
        ck("检测表读不到 → 不判（缺文件不许变成「全拦」或「全放」）",
           r["suspect"] == [] and "wow_names" in r["skipped"], r["skipped"])
    finally:
        restore(old)

    print("== D 只有官方完整枚举那两类能当硬判据，残表类一律不判 ==")
    old = fixture()
    try:
        ck("残表类名字（混调干果这类）出现在成品里不拦——沿用 10-10 那条口径",
           suspects("奖励里有混调干果和鱼缸。", "任务奖励包括这些") == [], "")
        rep = term_check.check("德莱尼主动技能持续30秒变15秒。\n瞄准射击加强。",
                               "牛头人这次加强，瞄准射击的改动最大。")
        fails = [w for lv, n, w in rep["rows"] if lv == "FAIL"]
        ck("check() 里这一条判 FAIL（不是 WARN），并且点名官方名单现有什么",
           any("德莱尼" in w for w in fails) and any("天裔" in w for w in fails), fails)
        ck("PASS 那句不许在同一次核对里跟着 FAIL 一起出现（自相矛盾的报告没人信）",
           not any(lv == "PASS" and "种族" in n for lv, n, _w in rep["rows"]),
           [n for lv, n, _ in rep["rows"] if lv == "PASS"])
    finally:
        restore(old)

    print("== E 海报这条线真的走了这道闸（以前一次都没跑）==")
    poster = (ROOT / "src" / "ai_poster.py").read_text(encoding="utf-8")
    ck("generate_poster 里调 roster_check，并把可疑并进 fact_hard（走既有的拦与放行）",
       "term_check.roster_check(" in poster
       and poster.index("term_check.roster_check(") < poster.index("if fact_hard and not force:"),
       "")
    ck("闸「没判」也要落到成品记录里，不许长得像「查过没问题」",
       '"skipped": ros.get("skipped")' in poster, "")
    sync = (ROOT / "tools" / "sync_wow_icons.py").read_text(encoding="utf-8")
    ck("同步脚本收官方枚举的种族与职业，并打 official_enum 标记",
       "official_enum" in sync and '"races.json"' in sync and '"classes.json"' in sync, "")
    ck("上游目录 src→app 两种布局都认（只认一种时脚本一启动就退出，名单冻在 10-10）",
       'for root in ("app", "src")' in sync, "")

    print("== E2 事前也给一份名单：提示词里那份「能用的种族/职业名」==")
    old = fixture()
    try:
        hint = term_check.roster_hint("血精灵这次也改了。")
        ck("名单 = 官方枚举 + 素材里出现过的名字（血精灵不在枚举，但素材提过就能用）",
           "血精灵" in hint and "天裔" in hint and "德莱尼" not in hint, hint[:80])
        ck("名单读不到时 roster_hint 交空串（提示词那句会退成纪律，不假装有成文名单）",
           term_check.roster_hint("") != "", "")
    finally:
        restore(old)
    ap = (ROOT / "src" / "ai_poster.py").read_text(encoding="utf-8")
    ck("拆解提示词里真有这条纪律与占位符（只在事后拦，等于让人白等一次模型钱）",
       "__NAMES__" in ap and "不许自己安一个名字" in ap and "_names_hint" in ap, "")
    web_src = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
    ck("出图与拆解两头都带素材原文（白名单必须是素材说过什么，不是成品自己）",
       "source_text=asr_text" in ap and "asr_text = _material_text(summary)" in web_src, "")

    print("== E3 官网整段原文进了一手对照面 ==")
    blobs = term_check.official_blobs()
    ck("同步脚本把种族介绍与特长原句收成 blobs（名单只有词答不了整句）",
       len(blobs) > 0 and any("天穹视界" in x for x in blobs),
       "%d 块 / %d 字" % (len(blobs), sum(len(x) for x in blobs)))
    ck("官方那句写的是 10%，与蓝贴的 15% 分得开（校对时要能指出差在哪一版）",
       any("移动速度提高10%" in x for x in blobs), "")
    app_src = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
    ck("_fact_ctx 把资料库原文并进 official_blobs（事实闸判「官方里有没有」就用这一份）",
       "_tc.official_blobs()" in app_src, "")
    ck("反面：资料库读不到时不静默——并进去失败要打印并继续按缺席判",
       "官网资料库没并进一手文本" in app_src, "")

    print("== E4 扫描范围：只看会发出去的正文，不罚诚实标注 ==")
    from src import ai_poster
    old = fixture()
    try:
        plan = {"title": "种族天赋算账", "pages": [{"title": "天裔改了什么", "layout_key": "guide",
                                                 "cards": [{"t": "天穹视界", "d": "移速 10% 变 15%"}]}]}
        pub = {"copy_text": "天裔的天穹视界改了。", "source_note":
               "来源：某 UP 主｜未逐项核验：「德莱尼」→ 天裔（术语修正记录）"}
        src = "天裔的天穹视界这次改了，移速提高10%。"
        r = term_check.roster_check(ai_poster.roster_scan_text(plan, pub), src)
        ck("来源说明/术语修正里引用错名去纠错，不算成品用了它（罚诚实标注=第三次踩同一类）",
           r["suspect"] == [], r["suspect"])
        bad = json.loads(json.dumps(plan))
        bad["pages"][0]["cards"][0]["t"] = "德莱尼缩时"
        r2 = term_check.roster_check(ai_poster.roster_scan_text(bad, pub), src)
        ck("反面：错名真写进图上卡片，照样抓得住",
           [x["name"] for x in r2["suspect"]] == ["德莱尼"], r2["suspect"])
    finally:
        restore(old)

    print("== F 真稿回放：11-02 那篇「德莱尼」必须被抓到 ==")
    # 固定回放已发时的错误快照；现行 108 会被重出覆盖，不能拿它期待旧错。
    frozen = json.loads((ROOT / "scripts" / "fixtures" / "wow_terms_108_bad.json")
                        .read_text(encoding="utf-8"))
    r0 = term_check.roster_check(frozen["draft"], frozen["source"])
    ck("固定旧错样本始终拦德莱尼（换机没有 output 备份也不能 SKIP）",
       [s["name"] for s in r0["suspect"]] == ["德莱尼"], r0["suspect"])
    plan_path = ROOT / "output" / "poster" / "108.bak-1102-published" / "plan.json"
    if plan_path.exists():
        import sqlite3
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        texts = []

        def walk(o):
            if isinstance(o, dict):
                for v in o.values():
                    (texts.append(v) if isinstance(v, str) else walk(v))
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        walk(plan.get("plan") if isinstance(plan, dict) and "plan" in plan else plan)
        draft = "\n".join(texts)
        con = sqlite3.connect("file:%s?mode=ro" % (ROOT / "output" / "video2text.db"), uri=True)
        src = "\n".join(str(r[0] or "") for r in con.execute(
            "SELECT transcript FROM videos WHERE id IN (332100, 321114)"))
        con.close()
        r = term_check.roster_check(draft, src)
        ck("拿当天那份真成品与真素材回放，抓出的就是「德莱尼」",
           [s["name"] for s in r["suspect"]] == ["德莱尼"], r["suspect"])
        ck("真名单里确实没有它（不是检测表瞎猜）",
           "德莱尼" not in term_check.enum_names(), "")
    else:
        print("  [SKIP] 没有 output/poster/108/plan.json，跳过真稿回放")

    print()
    if FAILS:
        print("FAILED %d 条：%s" % (len(FAILS), "、".join(FAILS)))
        return 1
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
