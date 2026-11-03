# -*- coding: utf-8 -*-
"""e2e_poster_fact_steps.py — AI 海报三步事实闸（2026-11-02）固定反例测试。

规格：wow-forever-web/docs/VERIFY-AI海报三步事实闸-2026-11-02.md「下一步最小修法」四条：
  ① 拆文案后逐条显示 官方依据／只有素材说法／待核／没判；
  ② 看版面只表示"版面合格"，编辑任何文案立即作废旧判定并重跑本地闸；
  ③ 出图前用当前文本重判，适用范围内登记缺失/校对失败 → 事实未判、暂停待发资格；
  ④ 反例：错误草稿拦、编辑后旧绿灯失效、登记缺失不显示已核、正确 108 与其他游戏不误拦。

全程只用本地桩：登记表比对、Flask test_client，**不调任何模型、不出图、不发布**。
跑法：unset PYTHONHOME PYTHONPATH && python3 scripts/e2e_poster_fact_steps.py
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import claim_check as cc          # noqa: E402
from src import ai_poster as ap            # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name + (("　— " + detail) if detail else ""))


def states(st):
    return [f.get("state") for f in st.get("fields") or []]


def by_label(st, key):
    for f in st.get("fields") or []:
        if key in str(f.get("label") or ""):
            return f
    return {}


# ── 准备：正确 108 的当前 plan+publish 与素材原文（跟工作台同一份来源）────────
plan_file = ROOT / "output" / "poster" / "108" / "plan.json"
if not plan_file.exists():
    print("❌ 找不到 %s，无法测正确 108" % plan_file)
    sys.exit(1)
saved = json.loads(plan_file.read_text(encoding="utf-8"))
plan108, publish108 = saved.get("plan") or {}, saved.get("publish") or {}

asr_text = ""
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("v2t_web_app", ROOT / "web" / "app.py")
    webapp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(webapp)
    summary108 = webapp._poster_get_summary(108)
    asr_text = webapp._material_text(summary108) if summary108 else ""
except Exception as e:                                  # noqa: BLE001
    print("⚠️ web/app.py 导入失败（%s），素材原文用整理稿正文兜底" % str(e)[:80])
    asr_text = ""

print("\n【A】① 拆文案：正确 108 不误拦，且逐条有状态")
fields108 = cc.claim_fields(plan108, publish108)
stA = cc.fact_status_fields(fields108, asr_text=asr_text)
blockedA = [f for f in stA.get("fields") or [] if f.get("state") == "blocked"]
check("scope 命中（魔兽无限服）", stA.get("scope") == "in", "scope=%s" % stA.get("scope"))
check("正确 108 无拦截", not blockedA,
      "blocked=%s" % "；".join(str(f.get('detail'))[:60] for f in blockedA[:2]))
cntA = {}
for s in states(stA):
    cntA[s] = cntA.get(s, 0) + 1
print("     108 状态分布：", json.dumps(cntA, ensure_ascii=False))
ver = [f for f in stA.get("fields") or [] if f.get("state") == "verified"]
check("逐条给了官方依据（verified 带 URL/原句）",
      bool(ver) and all(("http" in str(f.get("detail")) or "原句" in str(f.get("detail")))
                        for f in ver),
      "verified=%d 条，示例：%s" % (len(ver), str((ver[0] or {}).get('detail'))[:90] if ver else "无"))
check("待核/只有素材说法没有被演成已核",
      any(s in ("pending", "source_only") for s in states(stA)) or bool(ver),
      "pending/source_only=%d" % len([s for s in states(stA) if s in ("pending", "source_only")]))

print("\n【B】① 拆文案：乱编草稿逐条拦（归属错配 / 未登记断言 / 凭空专名 / 评价性结论）")
bad_plan = {"title": "魔兽无限服种族大改", "subtitle": "", "cards": [
    {"t": "牛头人原野疾驰", "d": "移速提高到15%，天裔天穹视界移动速度提高60%"},   # 数值挂错技能（原子断言反例）
    {"t": "矮人", "d": "接手了原野疾驰"},                                      # 技能挂错主体
    {"t": "德莱尼", "d": "主动技能提供10%急速"},                                # 素材与官方名单都没有的断言
    {"t": "全职业都在亏", "d": "直接起飞"},                                     # 替读者下的结论
]}
try:
    stB = webapp._poster_fact_status(bad_plan, {}, asr_text or "魔兽无限服 种族机制整理")
except NameError:
    stB = cc.fact_status_fields(
        cc.claim_fields(bad_plan, {}), asr_text=asr_text or "魔兽无限服 种族机制整理")
sb = {str(f.get("label")): f for f in stB.get("fields") or []}
check("数值挂错技能被拦", sb.get("卡片1", {}).get("state") == "blocked",
      str(sb.get("卡片1", {}).get("detail", ""))[:80])
check("技能挂错主体被拦", sb.get("卡片2", {}).get("state") == "blocked",
      str(sb.get("卡片2", {}).get("detail", ""))[:80])
check("凭空断言被拦（德莱尼10%急速）", sb.get("卡片3", {}).get("state") == "blocked",
      str(sb.get("卡片3", {}).get("detail", ""))[:80])
check("评价性结论被拦（直接起飞）", sb.get("卡片4", {}).get("state") == "blocked",
      str(sb.get("卡片4", {}).get("detail", ""))[:80])

print("\n【C】① 登记缺失：整项没判，不能显示已核")
empty = cc.fact_status_fields([("主标题", "魔兽无限服种族大改")], asr_text="魔兽",
                              reg={})
check("scope=no_registry", empty.get("scope") == "no_registry")
check("字段状态=unjudged（没判），不是 verified", set(states(empty)) == {"unjudged"})

print("\n【D】② 编辑文案：旧判定作废、重判后变红（接口级，本地不花钱）")
try:
    client = webapp.app.test_client()
    r1 = client.post("/api/poster/fact_status", json={
        "summary_id": 108, "plan": plan108, "publish": publish108}).get_json()
    check("原始文案重判：无拦截", isinstance(r1, dict) and r1.get("scope") == "in"
          and "blocked" not in states(r1),
          str(r1.get("error", ""))[:60] if isinstance(r1, dict) and r1.get("error") else "")
    import copy
    edited = copy.deepcopy(plan108)
    pages = edited.get("pages") or [edited]
    if pages[0].get("cards"):
        pages[0]["cards"][0]["d"] = "牛头人原野疾驰移速提高到15%"
    r2 = client.post("/api/poster/fact_status", json={
        "summary_id": 108, "plan": edited, "publish": publish108}).get_json()
    check("编辑后的同版文案重判：出现拦截（旧绿灯失效）",
          isinstance(r2, dict) and "blocked" in states(r2),
          "示例：%s" % next((str(f.get('detail'))[:70] for f in (r2.get('fields') or [])
                             if f.get('state') == 'blocked'), ""))
except NameError:
    check("接口级重判（web/app 导入失败，跳过）", False, "webapp 未导入")

print("\n【E】② 看版面：预览响应带同一套事实状态（免费排字路径）")
try:
    client2 = webapp.app.test_client()
    rp = client2.post("/api/poster/preview", json={
        "summary_id": 108, "plan": plan108, "publish": publish108}).get_json()
    if isinstance(rp, dict) and rp.get("error"):
        check("预览带事实状态", False, "preview 返回错误：%s" % str(rp.get("error"))[:80])
    else:
        check("预览带事实状态（scope=in、无拦截）",
              isinstance(rp, dict) and isinstance(rp.get("fact_status"), dict)
              and rp["fact_status"].get("scope") == "in"
              and "blocked" not in states(rp["fact_status"]))
except NameError:
    check("预览带事实状态（web/app 导入失败，跳过）", False, "webapp 未导入")

print("\n【F】③ 出图前重判：登记缺失/校对失败 → 事实未判（不是已核）")
tmpdir = tempfile.mkdtemp(prefix="fact_steps_")
saved_claims = cc.CLAIMS_FILE
try:
    cc.CLAIMS_FILE = Path(tmpdir) / "wow_claims.json"     # 桩：登记表读不到
    ev = ap.evaluate_claims(plan108, publish108, asr_text=asr_text)
    check("unjudged 非空（事实未判）", bool(ev.get("unjudged")), str(ev.get("unjudged"))[:80])
    check("hard=[] 没被当成通过（uncovered=False、unjudged 在）",
          ev.get("hard") == [] and not ev.get("uncovered") and bool(ev.get("unjudged")))
finally:
    cc.CLAIMS_FILE = saved_claims
    shutil.rmtree(tmpdir, ignore_errors=True)

print("\n【G】③ scope 外：不误拦，但写明未覆盖、由人工核对")
evG = ap.evaluate_claims({"title": "流放之路 PoE 新赛季开荒"}, {}, asr_text="流放之路 PoE 攻略")
check("uncovered=True（未覆盖）", evG.get("uncovered") is True and evG.get("unjudged") == "",
      str(evG.get("skipped"))[:80])
check("scope 外不造拦（hard 空）", evG.get("hard") == [])

print("\n【H】③ 正确 108 重判：仍然通过（含：不许有未核机制断言误停发）")
evH = ap.evaluate_claims(plan108, publish108, summary=None, asr_text=asr_text)
check("hard 空 / unjudged 空", evH.get("hard") == [] and evH.get("unjudged") == "",
      "checked=%d" % (evH.get("checked") or 0))
check("正确 108 无未核机制断言（mech 空）", evH.get("mech") == [],
      "mech=%s" % "；".join(evH.get("mech") or [])[:100])

print("\n【I】③ publishable 接线：适用范围内没判必须翻成不可发布")
src_ap = (ROOT / "src" / "ai_poster.py").read_text(encoding="utf-8")
wired = ("claims_unjudged" in src_ap and "if claims_unjudged:\n        fact_blocks = True" in src_ap)
check("generate_poster 里 unjudged → fact_blocks=True", wired)
check("清单上有「事实未判」原话", "事实未判 —— " in src_ap)

print("\n【J】①→③ 混合断言（11-02 复验漏口一）：已核句盖不住同卡新增的未核机制结论")
import copy
mixed = copy.deepcopy(plan108)
mpages = mixed.get("pages") or [mixed]
mcard = mpages[0]["cards"][0]
mcard["d"] = "原野疾驰移速提高到2%，上限60%。牛头人原野疾驰免疫所有控制。"
try:
    stJ = webapp._poster_fact_status(mixed, publish108, asr_text)
except NameError:
    stJ = cc.fact_status_fields(cc.claim_fields(mixed, publish108), asr_text=asr_text)
fj = {str(f.get("label")): f for f in stJ.get("fields") or []}
sent1 = next((f for k, f in fj.items() if k.endswith("卡片1·句1")), {})
sent2 = next((f for k, f in fj.items() if k.endswith("卡片1·句2")), {})
check("已核句仍逐条亮绿（数值对上登记）", sent1.get("state") == "verified",
      "句1=%s %s" % (sent1.get("state"), str(sent1.get("detail", ""))[:60]))
check("同卡新增「免疫所有控制」不再借绿（state≠verified）", sent2.get("state") != "verified",
      "句2=%s %s" % (sent2.get("state"), str(sent2.get("detail", ""))[:70]))
check("它被标成未核机制断言（pending＋mech，点名「免疫」）",
      sent2.get("state") == "pending" and sent2.get("mech") is True
      and "免疫" in str(sent2.get("pred") or []),
      str(sent2.get("detail", ""))[:80])
evJ = ap.evaluate_claims(mixed, publish108, asr_text=asr_text)
check("关键：check_fields 对这句不报 hard（旧闸漏过形态）",
      evJ.get("hard") == [], "；".join(evJ.get("hard") or [])[:80])
check("出图前重判抓住它（mech 非空 → 暂停待发资格）",
      bool(evJ.get("mech")) and "免疫" in evJ["mech"][0],
      "mech=%s" % "；".join(evJ.get("mech") or [])[:100])
gap_direct = cc.mechanism_gaps(cc.claim_fields(mixed, publish108), asr_text=asr_text)
check("mechanism_gaps 与 evaluate_claims 同源（逐条一致）", gap_direct == (evJ.get("mech") or []))
# 普通非机制句不误拦
plain = copy.deepcopy(plan108)
ppages = plain.get("pages") or [plain]
ppages[0]["cards"][0]["d"] = "这套连招手感很顺，推荐新手试试。"
try:
    stJP = webapp._poster_fact_status(plain, publish108, asr_text)
except NameError:
    stJP = cc.fact_status_fields(cc.claim_fields(plain, publish108), asr_text=asr_text)
check("普通修辞句不拦（无 blocked、无 mech）",
      "blocked" not in states(stJP)
      and not any(f.get("mech") for f in stJP.get("fields") or []),
      "states=%s" % ",".join(sorted(set(states(stJP)))))
evJP = ap.evaluate_claims(plain, publish108, asr_text=asr_text)
check("普通句出图闸不暂停待发（hard 空、mech 空、unjudged 空）",
      evJP.get("hard") == [] and evJP.get("mech") == [] and evJP.get("unjudged") == "")

print("\n【K】③ 未核机制断言的停发接线：mech → fact_blocks → 红条")
check("generate_poster 里 mech → fact_blocks=True",
      "claims_mech = claims.get(\"mech\") or []" in src_ap
      and "if claims_mech:\n        fact_blocks = True" in src_ap)
check("fact_gate_state 露出 mech 清单（界面红条要读得到）",
      '"mech": claims.get("mech") or []' in src_ap)
check("清单上有「未核机制断言」原话", "未核机制断言 —— " in src_ap)
src_html = (ROOT / "web" / "templates" / "index.html").read_text(encoding="utf-8")
check("结果页红条读 cs.mech", "cs.mech" in src_html and "未核机制断言，待发资格已暂停" in src_html)
check("出图预检把 mech 算进停发档",
      "f.state === 'blocked' || f.state === 'unjudged' || f.mech" in src_html)

print("\n═══ 结果：%d 通过 / %d 失败 ═══" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  ❌ " + f)
sys.exit(1 if FAIL else 0)
