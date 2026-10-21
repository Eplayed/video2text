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
    from src import fact_gate

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
    for sid in (74, 63, 66):
        pack, content = load(sid)
        if not pack:
            continue
        hard, _soft = fact_gate.check(pack["plan"], content)
        cut = fact_gate.copy_is_cut(pack["pub"].get("copy_text") or "")
        ck("%d 健康样本放行" % sid, (not hard) and (not cut),
           "hard=%d cut=%s" % (len(hard), cut or "无"))

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
