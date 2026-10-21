# -*- coding: utf-8 -*-
"""编辑证据链的端到端走查：接口存得进、缺项算得准、成品预览看得见，且**不覆盖生产样稿**。

覆盖 PRD 的验收 1（正向流）与验收 2（反向流）里能机器判的那部分，全程只连隔离服务：
· `scripts/agent/poster_test_server.py` —— 临时库副本 + 临时成品目录 + 模型桩 + 独立端口；
· 生产 `output/poster/83/manifest.json` 前后 sha256 必须一致（不重生成、不覆盖已发成品）；
· 证据链写的是那份**临时库**，生产库一个字节不动。

三条反向断言单独列出来，因为它们最容易在改版时被人悄悄删掉：
没建证据链的稿子不许算通过；补一项不得冲掉别的缺项；
界面上不许出现"已获原创/100% 原创/可勾原创"这类字样（工具不证明原创资格）。

用法：env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/agent/ui_provenance_walk.py
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "agent"))
from ui_schedule_brief_walk import start_server                  # noqa: E402  同一套隔离服务
from ui_guide_gate_walk import stop_server                       # noqa: E402

PROD_MANIFEST = ROOT / "output" / "poster" / "83" / "manifest.json"
fails = []


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + detail) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + detail) if detail else ""))
        fails.append(name)


def sha(p):
    if not os.path.isfile(str(p)):
        return ""
    with open(str(p), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def api(port, path, body=None, timeout=120):
    url = "http://127.0.0.1:%d%s" % (port, path)
    req = urllib.request.Request(url, data=(json.dumps(body).encode("utf-8") if body is not None else None),
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_done(port, tries=60):
    st = {}
    for _ in range(tries):
        time.sleep(1)
        st = api(port, "/api/poster/status")
        if not st.get("running"):
            return st
    return st


PLAN = {"title": "五人本按等级怎么排", "subtitle": "13 到 60 级一条路线",
        "style_key": "guide_night", "footer": "内容整理自公开分享，以游戏内为准。",
        "pages": [{"kicker": "无限服 · 练级", "title": "五人本按等级怎么排",
                   "layout_key": "guide", "style_key": "guide_night",
                   "cards": [{"t": "先排等级", "d": "按等级段串起来，省得来回跑。"},
                             {"t": "两边都能进", "d": "部落要深入联盟领地，反过来也一样。"}]},
                  {"kicker": "无限服 · 练级", "title": "哪几座顺路",
                   "layout_key": "guide", "style_key": "guide_night",
                   "cards": [{"t": "先核等级", "d": "以游戏内显示的等级区间为准。"},
                             {"t": "没测过就不写", "d": "耗时与效率没有实测数据，不猜。"}]}]}
PUB = {"copy_text": "九座新五人本按等级段排一条路线，联盟与部落两边谁要深入对方领地都写清楚了。"
                    "等级区间以游戏内显示为准，耗时没有实测数据，不猜。",
       "digest": "五人本练级路线，等级区间以游戏内为准。", "source_note": "整理自暴雪官方公告。"}


def main():
    if not os.path.isfile(str(PROD_MANIFEST)):
        print("生产 83 的 manifest 不在，这条走查没法证明「不覆盖线上成品」")
        return 2
    before = sha(PROD_MANIFEST)
    proc, port, srv = start_server()
    try:
        print("── 1. 没建证据链时不许算通过 ──")
        d0 = api(port, "/api/plan/provenance?summary_id=83")
        ck("读一份不存在的证据链：recorded=False，不报错", d0.get("recorded") is False,
           str(d0.get("error") or "")[:60])
        api(port, "/api/poster/generate", {"summary_id": 83, "plan": PLAN, "publish": PUB,
                                          "text_mode": "typeset", "review": False, "copies": 1})
        st1 = wait_done(port)
        pkg1 = st1.get("package") or {}
        ck("没建链就出图：publishable=False（缺项挡待发，但不拦出图）",
           pkg1.get("publishable") is False, "publishable=%s err=%s" % (
               pkg1.get("publishable"), str(st1.get("error") or "")[:60]))
        prov1 = pkg1.get("provenance") or {}
        ck("预览数据里带出「独立贡献未说明」这一档",
           prov1.get("label") == "独立贡献未说明", str(prov1.get("label")))
        ck("预览数据把「绑了几张素材帧」一起带回来（这张夹具没绑帧，所以是 0；界面据此显示）",
           prov1.get("used_frames") == 0, str(prov1.get("used_frames")))
        ck("缺项点名到具体字段（provenance / question）",
           any(g.get("field") in ("provenance", "question") for g in (prov1.get("gaps") or [])),
           str([g.get("field") for g in (prov1.get("gaps") or [])]))
        mf1 = os.path.join(srv["poster_out"], "83", "manifest.json")
        ck("成品落在临时目录，不是生产 output/poster", os.path.isfile(mf1), mf1)
        m1 = json.load(open(mf1, encoding="utf-8"))
        ck("manifest 里写明这不是平台原创资格认证",
           "不是平台原创资格认证" in json.dumps(m1.get("provenance"), ensure_ascii=False),
           str((m1.get("provenance") or {}).get("note"))[:50])
        ck("lint 里有「编辑证据链缺项」那条（人在结果区看得见）",
           any("编辑证据链缺项" in x for x in (m1.get("lint") or [])),
           str([x for x in (m1.get("lint") or []) if "证据链" in x][:1])[:110])

        print("── 2. 正向：三项齐 + 结论有证据 + 材料有依据 → 允许待发 ──")
        good = {"slot_key": "summary:83", "channel": "toutiao", "form": "guide", "version": 1,
                "summary_id": 83,
                "question": "13–60 级想按等级顺序刷完九座新五人本，路线怎么排",
                "contribution": "把官方公告的九座本按等级段排成一条路线，标出两边谁要深入对方领地",
                "contribution_evidence": "wow.blizzard.cn/news/24301455、24303009 两篇公告"
                                         "＋本地核对记录 temp/dungeon_route_check.md",
                "claims": [{"text": "领主大厅 13–18，入口在铁炉堡地下",
                            "evidence": "https://wow.blizzard.cn/news/24301455/index.html",
                            "evidence_kind": "官方一手页面", "verifier": "张亚军",
                            "vdate": "2026-10-21", "status": "已核"}],
                "materials": [{"url": "https://wow.blizzard.cn/news/24301455/index.html",
                               "author": "暴雪官方", "usage": "事实来源",
                               "rights_basis": "官方公告转述，标来源与日期", "review": "可发布"},
                              {"url": "self:route_table", "author": "本站",
                               "usage": "我们自己做的", "rights_basis": "路线表与说明图是我们自己排的",
                               "review": "可发布"}]}
        r2 = api(port, "/api/plan/provenance", good)
        ck("存进去再读回来 blocking=0（正向一条都不挡）", r2.get("blocking") == [],
           str([g.get("field") for g in (r2.get("gaps") or [])]))
        ck("状态升到「有独立贡献，待人工评估」", r2.get("label") == "有独立贡献，待人工评估",
           str(r2.get("label")))
        api(port, "/api/poster/generate", {"summary_id": 83, "plan": PLAN, "publish": PUB,
                                          "text_mode": "typeset", "review": False, "copies": 1})
        st2 = wait_done(port)
        pkg2 = st2.get("package") or {}
        ck("补齐后 publishable=True", pkg2.get("publishable") is True,
           "publishable=%s err=%s" % (pkg2.get("publishable"), str(st2.get("error") or "")[:70]))
        ck("重新打开还读得到（持久化，不是内存里糊一份）",
           api(port, "/api/plan/provenance?summary_id=83").get("recorded") is True)

        print("── 3. 反向：借用的画面没有依据 → 仍然挡，且不被其他已填项冲掉 ──")
        bad = dict(good, version=2,
                   materials=good["materials"] + [
                       {"url": "https://v.douyin.com/xxx", "author": "某博主",
                        "usage": "被引用的画面", "rights_basis": "", "review": "未审"}])
        r3 = api(port, "/api/plan/provenance", bad)
        f3 = [g.get("field") for g in (r3.get("gaps") or [])]
        ck("新加的那条借用画面被单独点名（rights_basis + review 两条）",
           "materials[3].rights_basis" in f3 and "materials[3].review" in f3, str(f3))
        v1 = api(port, "/api/plan/provenance?slot_key=summary:83&version=1").get("row") or {}
        ck("版本 2 存进去后，按版本 1 读回来还是第 1 版（已开工那版不被覆盖）",
           v1.get("version") == 1 and v1.get("contribution", "").startswith("把官方公告"),
           "v=%s %s" % (v1.get("version"), (v1.get("contribution") or "")[:14]))

        print("── 4. 界面：折叠栏里填得动、存得进、结果区看得见 ──")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(channel="chrome", headless=True)
            pg = br.new_page(viewport={"width": 1500, "height": 1100})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/" % port, wait_until="domcontentloaded")
            pg.wait_for_timeout(2500)
            pg.evaluate("(id) => openPosterDialog(id)", 83)
            pg.evaluate("() => { document.getElementById('pk-prov').open = true; }")
            for _ in range(20):
                time.sleep(1)
                if "玩家要解决的问题" in pg.evaluate(
                        "() => (document.getElementById('pk-prov-body')||{}).textContent || ''"):
                    break
            body = pg.evaluate("() => (document.getElementById('pk-prov-body')||{}).textContent || ''")
            ck("证据链那一栏真的渲染出来了（不是空壳）", "玩家要解决的问题" in body, body[:48].replace("\n", " "))
            ck("回填了已存的三项（重新打开看得见旧内容）",
               "13" in pg.evaluate("() => document.getElementById('pr-question').value"),
               pg.evaluate("() => document.getElementById('pr-question').value")[:24])
            pg.fill("#pr-question", "老电脑怎么在五人本路线里少跑回头路")
            n_claim = pg.evaluate("() => _prov.claims.length")
            pg.evaluate("() => provAdd('claims')")
            idx = pg.evaluate("() => _prov.claims.length - 1")
            pg.fill("[data-k='claims'][data-i='%d'][data-f='text']" % idx, "跑完一趟要两小时")
            # 状态选「已核」而不给证据——这才是"手填我测过但没记录"那一档，必须当场被点名；
            # 留「参考转述」不算缺项（如实标转述是允许的，别把老实标注也拦下来）
            pg.select_option("[data-k='claims'][data-i='%d'][data-f='status']" % idx, "已核")
            pg.evaluate("() => provSave()")
            pg.wait_for_timeout(2500)
            gaps = pg.evaluate("() => (document.getElementById('pr-gaps')||{}).textContent || ''")
            ck("新加的那条「已核无证据」当场被点名（claims[%d]）" % (idx + 1),
               ("claims[%d].evidence" % (idx + 1)) in gaps, gaps[:120].replace("\n", " "))
            ck("原来那条借用画面没依据的缺项还在（补一项不冲掉别的）",
               "materials[" in gaps and "使用依据" in gaps, gaps[120:240].replace("\n", " "))
            pg.evaluate("() => posterGenerate(false)")
            txt = ""
            for _ in range(45):
                time.sleep(1)
                txt = pg.evaluate("() => (document.getElementById('pk-result')||{}).innerText || ''")
                if "编辑证据链" in txt:
                    break
            ck("出图结果区里看得见编辑状态与那句「不是原创资格认证」",
               "编辑证据链" in txt and "不是平台原创资格认证" in txt,
               [ln for ln in txt.split("\n") if "编辑证据链" in ln][:1][0][:80] if txt else "")
            pg.eval_on_selector("#pk-result", "e=>e.scrollIntoView({block:'center'})")
            pg.wait_for_timeout(300)
            pg.query_selector("#pk-result").screenshot(path=str(ROOT / "temp" / "prov_result.png"))
            pg.eval_on_selector("#pk-prov", "e=>e.scrollIntoView({block:'center'})")
            pg.wait_for_timeout(300)
            pg.query_selector("#pk-prov").screenshot(path=str(ROOT / "temp" / "prov_panel.png"))
            whole = pg.evaluate("() => document.body.innerText")
            for bad_word in ("100%原创", "100% 原创", "已获原创", "可勾原创", "原创资格已确认"):
                ck("界面上不出现「%s」" % bad_word, bad_word not in whole)
            ck("页面没有 JS 报错", not errs, "; ".join(errs[:2])[:120])
            br.close()
    finally:
        stop_server(proc)

    after = sha(PROD_MANIFEST)
    ck("生产 83 的样稿没被覆盖（sha 前后一致）", before == after, "%s → %s" % (before, after))
    print("截图：temp/prov_result.png / temp/prov_panel.png")
    if fails:
        print("\nPROVENANCE_WALK_FAIL %d 条：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("PROVENANCE_WALK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
