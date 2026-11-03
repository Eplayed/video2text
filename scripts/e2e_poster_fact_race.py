# -*- coding: utf-8 -*-
"""e2e_poster_fact_race.py — 编辑竞态回放（11-02 复验漏口二）：旧响应晚到不许再亮旧绿。

为什么单独一条线（wow-forever-web/docs/VERIFY-AI海报三步事实闸复验-2026-11-02.md）：
复验用页面原样 JS＋假 DOM＋受控 fetch 重放抓到——A 已核 → 改 B 发起重判 → 再改 C，
B 的绿色响应晚到时界面又显示「有官方具体来源」。修法：每次真编辑立即清旧判定＋递增
代次，请求带发出时的完整 plan＋publish 签名，响应只在「代次相同＋当前文本签名一致」
时落界面；接口失败保持「没判」。

这条线把 **index.html 里的原样 JS**（`let _pkFact` 到 `function pkCount` 之间一字不改）
抽出来，接上假 DOM 与受控 fetch 重放四个场景：
  S1 旧绿晚到：B 的响应在改 C 之后才回来 → 必须整份丢弃，等 C 的重判；
  S2 只改配文：plan 没动、publish 变了 → 旧判定同样立即作废，重判请求带新 publish；
  S3 接口 500：失败后界面保持「没判」，不许把旧绿摆回来；
  S4 同签名重放（换页签/重画）：不递增代次、不清判定、不发请求。

全程无网络、无模型；fetch 与定时器都是真的（防抖 700ms 用真实时钟等）。
跑法：unset PYTHONHOME PYTHONPATH && python3 scripts/e2e_poster_fact_race.py
依赖 node（只跑 JS，不发请求）。
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web" / "templates" / "index.html"

HARNESS = r"""
// ── 桩：假 DOM、假表单、受控 fetch（页面 JS 原样跑在这套桩上）──────────────
const __boxes = {};
function __el(id) { if (!__boxes[id]) __boxes[id] = {innerHTML: ''}; return __boxes[id]; }
let __plan = null, __publish = null;
let _pkId = 108;
function esc(s) { return String(s == null ? '' : s); }
function pkReadPayload() { return __plan; }
function pkReadPublish() { return __publish; }
const document = {getElementById: function (id) {
  if (id === 'pk-title') return __plan ? __el('pk-title') : null;   // 没进海报页＝没有表单
  return __el(id);
}};
let __auto = null;                       // {ok, status, body}：自动应答模式（测 500 用）
const __fetches = [];                    // 手动放行模式：请求排队，由驱动逐个 resolve
function fetch(url, opts) {
  const req = {url: url, body: JSON.parse(opts.body), resolve: null};
  const p = new Promise(function (res) { req.resolve = res; });
  __fetches.push(req);
  if (__auto) {
    const a = __auto;
    setTimeout(function () {
      req.resolve({ok: a.ok, status: a.status || 200, json: async function () { return a.body; }});
    }, 10);
  }
  return p;
}
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function __reset() {
  __plan = null; __publish = null; __auto = null;
  __fetches.length = 0;
  for (const k in __boxes) delete __boxes[k];
}
"""

DRIVER = r"""
// ── 测试驱动：四个场景 ────────────────────────────────────────────────
let PASS = 0, FAIL = 0;
function ck(name, cond, detail) {
  if (cond) { PASS++; console.log('  [PASS] ' + name + (detail ? ' — ' + detail : '')); }
  else { FAIL++; console.log('  [FAIL] ' + name + (detail ? ' — ' + detail : '')); }
}
const goodData = {scope: 'in', note: '', fields: [
  {label: '第1页·卡片1', state: 'verified', detail: '对上登记条目，官方出处：https://x'}]};
const badData = {scope: 'in', note: '', fields: [
  {label: '第1页·卡片1', state: 'blocked', detail: '15% 找不到出处'}]};
const planA = {title: 'A 稿', pages: [{cards: [{t: '牛头人', d: '原野疾驰移速2%'}]}]};
const planB = {title: 'B 稿', pages: [{cards: [{t: '牛头人', d: '原野疾驰移速15%'}]}]};
const planC = {title: 'C 稿', pages: [{cards: [{t: '天裔', d: '天穹视界移速15%'}]}]};
const pub0 = {copy_text: '原始配文'};

async function s1() {
  console.log('== S1 旧绿晚到：B 的响应在改 C 之后才回来，必须整份丢弃 ==');
  __reset();
  __plan = planA; __publish = pub0;
  pkFactSet(goodData, pkFactSig(__plan, __publish));
  ck('初始：A 已核绿灯在摆', __boxes['pk-fact'].innerHTML.indexOf('有官方具体来源') >= 0, '');
  __plan = planB; pkFactInvalidate();                          // 编辑 B：立即清旧
  ck('编辑 B：旧判定当场撤下（没判）', _pkFact.data === null
     && __boxes['pk-fact'].innerHTML.indexOf('没有判定') >= 0, '');
  await sleep(800);                                            // 防抖到：B 的请求出发
  ck('B 的重判请求带完整 plan＋publish',
     __fetches.length === 1 && __fetches[0].body.plan.title === 'B 稿'
     && __fetches[0].body.publish.copy_text === '原始配文', JSON.stringify(__fetches[0].body).slice(0, 60));
  __plan = planC; pkFactInvalidate();                          // 编辑 C：代次++，B 在途
  const genAfterC = _pkFact.gen;
  __fetches[0].resolve({ok: true, status: 200, json: async () => goodData});  // B 的旧绿晚到
  await sleep(50);
  ck('B 的旧绿被整份丢弃（没落界面、数据还是空）',
     _pkFact.data === null && __boxes['pk-fact'].innerHTML.indexOf('有官方具体来源') < 0, '');
  await sleep(800);                                            // C 的重判出发
  ck('C 的重判请求带的是 C 的文本（B→C 各递增一次代次）', __fetches.length === 2
     && __fetches[1].body.plan.title === 'C 稿' && genAfterC === 2, 'gen=' + genAfterC);
  __fetches[1].resolve({ok: true, status: 200, json: async () => badData});
  await sleep(50);
  ck('C 的新判定落界面（拦截可见）', _pkFact.data === badData
     && __boxes['pk-fact'].innerHTML.indexOf('拦截') >= 0, '');
}

async function s2() {
  console.log('== S2 只改配文：plan 没动、publish 变了，旧判定同样立即作废 ==');
  __reset();
  __plan = planA; __publish = pub0;
  pkFactSet(goodData, pkFactSig(__plan, __publish));
  __publish = {copy_text: '改过的配文'};                        // 只动配文
  pkFactInvalidate();
  ck('只改配文也当场作废', _pkFact.data === null
     && __boxes['pk-fact'].innerHTML.indexOf('没有判定') >= 0, '');
  __auto = {ok: true, status: 200, body: goodData};
  await sleep(900);
  ck('重判请求带的是新配文（不是旧 plan 拼新 publish 的混合签名）',
     __fetches.length === 1 && __fetches[0].body.publish.copy_text === '改过的配文', '');
  ck('签名一致的重判落回界面', _pkFact.data === goodData, '');
}

async function s3() {
  console.log('== S3 接口 500：失败保持「没判」，不许恢复旧绿 ==');
  __reset();
  __plan = planA; __publish = pub0;
  pkFactSet(goodData, pkFactSig(__plan, __publish));
  __plan = planB; pkFactInvalidate();
  ck('编辑后旧绿已撤', _pkFact.data === null, '');
  __auto = {ok: false, status: 500, body: {error: 'boom'}};
  await sleep(900);
  ck('失败后显示「没判」，不显示旧绿', _pkFact.data && _pkFact.data.scope === 'error'
     && __boxes['pk-fact'].innerHTML.indexOf('没判') >= 0
     && __boxes['pk-fact'].innerHTML.indexOf('有官方具体来源') < 0, '');
}

async function s4() {
  console.log('== S4 同签名重放（换页签/重画调 pkCount）：不动代次、不清判定、不发请求 ==');
  __reset();
  __plan = planA; __publish = pub0;
  pkFactSet(goodData, pkFactSig(__plan, __publish));
  const gen0 = _pkFact.gen;
  pkFactInvalidate();                                          // 同签名：应该直接退出
  ck('同签名不清判定、不递增代次', _pkFact.data === goodData && _pkFact.gen === gen0, '');
  await sleep(850);
  ck('同签名不发重判请求', __fetches.length === 0, '');
}

(async function main() {
  await s1(); await s2(); await s3(); await s4();
  console.log('');
  console.log('RESULT ' + PASS + ' pass / ' + FAIL + ' fail');
  process.exit(FAIL ? 1 : 0);
})().catch(function (e) { console.error('HARNESS_ERROR', e); process.exit(2); });
"""


def main():
    src = HTML.read_text(encoding="utf-8")
    m = re.search(r"let _pkFact = .*?(?=function pkCount\()", src, re.S)
    if not m:
        print("❌ 没从 index.html 抽到事实状态 JS 块（let _pkFact … function pkCount）")
        return 1
    js = m.group(0)
    wd = tempfile.mkdtemp(prefix="fact_race_")
    script = Path(wd) / "race.js"
    script.write_text(HARNESS + js + "\n" + DRIVER, encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=120)
    print(r.stdout, end="")
    if r.stderr:
        print("stderr:", r.stderr[:400])
    if r.returncode != 0:
        print("❌ node 退出码 %d" % r.returncode)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
