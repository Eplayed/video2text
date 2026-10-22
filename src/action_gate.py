# -*- coding: utf-8 -*-
"""action_gate.py — 每条"可照做的建议"都要能在原 ASR 里定位到原话，定位不到就停在待核清单里。

为什么要这一层（2026-10-22 `PRD-图文攻略模型换档验收与优化-2026-10-22.md`）：
`fact_gate` 只管"上游【风险核查】点名的 token 有没有印到卖点位置上"，
于是出现这种形状——整理稿在末尾【风险核查】老实写了"需回听确认"，
可前面【行动建议】已经把"视野距离拉到 10""低延迟模式关闭"排成一张可照做的表格，
下游海报读的就是这张表。实测两条新稿：83 的九条"需回听/需进游戏"在 `fact_gate` 里
几乎全是 `heavy=false`（旧分级认的是"疑似转写/口误/单位需核实"那批词），
85 表格里的 5%/20% 也是 `heavy=false`——**同一件事，风险节认了，闸口没认**。

这一层不再往同义词表上追加正则（PRD 明说那不是长期方案），改判**证据**：
1. **数值**：建议句里每个独立数字（10、5、20%）都得在 ASR 的独立数字里出现。
   边界很要紧——ASR 那句"对于1080p显示技术"里的 10 不算独立数字，
   所以"视野距离拉到10"判无出处；而"地表景光5就够"里的 5 算，判有出处。
2. **选项名**：句子里出现的设置项名（低延迟模式/次表面光照/地表景观…）要在 ASR 里找得到；
   找不到但 ASR 里有个同尾缀的含混串（刺右光照 ↔ 次表面光照），那是"音没听清"，判**待回听**；
   ASR 里连影子都没有（低延迟模式），那是模型补的，判**待游戏核**。
3. **口播说法**：风险节自己写了"举例/未给官方精确公式/未署名/需实测"的那些 token，
   即使 ASR 里有原话，也只能算"博主那么说"，不许进标题、大字、可照做的指令。
   85 的 5%/20% 就是这一档——ASR 里确实有，但那是举例。

三条口径：
· **只判"能不能照做"，不判游戏事实真假**。判断真假仍然归人，这里只把没依据的挡在玩家动手之前。
· **不能全拦**。ASR 逐字清楚的步骤（SSAO关闭、材质投射一定选择开启）必须放行，
  否则每一篇都发不出去，闸口就会被关掉——那是比没有更糟。
· **未核项留在内部待核清单**（`pending`），不自动进海报、平台标题，也不给 `publishable=true`；
  人工回听/截图之后写进 `confirmations`（确认人、时间、证据）才逐条升档，旧稿不覆盖。

Python 3.9 兼容：不用 match / X|Y 标注。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# 会印给玩家、玩家会照做的三节。【原话摘录】【风险核查】是交代出处的地方，不算指令。
ACTION_SECTIONS = ("行动建议", "对你的影响", "标题候选")
STATUS_OK = "可用"
STATUS_LISTEN = "待回听"          # ASR 里有同尾缀的含混串：音没听清，内容可能对
STATUS_GUESS = "ASR查无"          # ASR 里连影子都没有：模型补的
STATUS_HEDGE = "口播说法未核"      # ASR 有原话，但风险节自己说是举例/未给官方/需实测
# 风险节里"这句话本身就是没核过"的说法。注意：这是**读风险节**用的，
# 不是往 fact_gate 的 heavy 词表上追加——两边判的东西不同（那边判"数可疑"，这边判"话没落地"）。
_UNSETTLED = re.compile(
    r"需回听|回听确认|需进游戏|进游戏核对|需核对|需确认|需实测|建议实测|待核|未核实|核对|"
    r"举例|未给.{0,10}官方|官方.{0,8}公式|未署名|主观判断|口播结论|不承诺|疑似|误转写")
# 独立数字：前面不能贴着数字/小数点，后面不能紧跟 0 以外的数字（挡掉 1080p 里的 10）
_NUM = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(%|成|倍)?(?![\d])")
_ASCII = re.compile(r"[A-Za-z][A-Za-z0-9]{1,9}")
# 设置项/专名候选：以图形设置里常见的尾缀收尾的中文短语
_OPT = re.compile(r"[\u4e00-\u9fa5]{1,5}(?:模式|光照|细节|密度|投射|距离|景观|分辨率|同步|"
                  r"延迟|强度|特效|抗锯齿|视野|采样|各向异?性|质量|档位)")
# 效果承诺这类词：没实测数据时不许写成确定句（与 fact_gate._ABSOLUTE_EFFECT 同一意图，词表各自维护）
_EFFECT = re.compile(r"明显提升|大幅提升|帧数回升|流畅度拉满|不卡|稳了|翻倍|肉眼可见|全消|起飞")
_TAIL = re.compile(r"[\u4e00-\u9fa5]{2}(?:模式|光照|细节|密度|投射|距离|景观|分辨率|同步|延迟|"
                   r"强度|特效|视野|质量)$")
# 这两个表只为消掉**自我误报**，不是游戏术语表，也不是往同义词表上追加：
# 1) `_META_ASCII` 收的是"说明制作过程"的词（稿子里写「ASR 为 VS2」「up 主整理」），
#    它们本来就不会印在游戏设置界面上，拿它们要求 ASR 出处必然查无——
#    实测把「高级：三倍缓冲关闭…（ASR 为 VS2）」和「没升级电脑的玩家：按up主…整理」
#    两条正常建议打成了「查无」，闸口一旦误报就会被当噪音关掉（2026-10-22 实测）。
# 2) `_LEAD`/`_EDIT` 修的是正则自己的贪心：`[\u4e00-\u9fa5]{1,5}(?:档位|景观…)` 会把前面
#    的虚词或编辑动词一起圈进来，于是拿「和地表景观」「需核对档位」去 ASR 里找——
#    那根本不是稿子给的项名，是自己造出来的靶子。
_META_ASCII = {"ASR", "AI", "UP", "PC", "APP", "ID", "PPT", "QA", "UV", "OCR", "TTS"}
_LEAD = re.compile(r"[的和与或把等了这就也还更到为在个要需应可待请疑问]")
_EDIT = re.compile(r"(?:需|要|应|可|待|未|请|核|按|以|疑|建)")


def _opt_name(raw: str) -> str:
    """正则圈到的候选项名先去掉虚词前缀；剩下的仍以编辑动词开头 → 不当项名（返回空）。"""
    n = str(raw or "")
    while n and _LEAD.match(n[0]):
        n = n[1:]
    if len(n) < 2 or _EDIT.match(n[0]):
        return ""
    return n


def _ascii_names(text: str) -> List[str]:
    """句里需要"在转写里找得到"的 ASCII 专名（SSAO／CMAA2／VRS），过程词不算。"""
    return [m.group(0) for m in _ASCII.finditer(str(text or ""))
            if m.group(0).upper() not in _META_ASCII]



def sections(md: str) -> Dict[str, List[str]]:
    """把整理稿切成 {节名: [行]}。节名去掉【】。"""
    out: Dict[str, List[str]] = {}
    cur = "（无节名）"
    for line in str(md or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^#+\s*【?([^】#]{2,12})】?\s*$", s) or re.match(r"^【([^】]{2,12})】\s*$", s)
        if m:
            cur = m.group(1).strip()
            out.setdefault(cur, [])
            continue
        out.setdefault(cur, []).append(s)
    return out


def _asr_numbers(asr: str) -> List[str]:
    """ASR 里**独立**出现的数字（含百分号写法）。1080p 那种贴着的不要。"""
    out = []
    for m in _NUM.finditer(str(asr or "")):
        v = m.group(1)
        if m.group(2) == "%":
            out.append(v + "%")
        out.append(v)
    return out


def _risk_tokens(md: str) -> List[Tuple[str, str]]:
    """风险节里被点名的 (token, 原话)。用 fact_gate 的解析，保证两边认的是同一批条目。"""
    from . import fact_gate
    out = []
    for it in fact_gate.risk_items(md):
        for t in it.get("tokens") or []:
            out.append((re.sub(r"\s+", "", t), it.get("raw") or ""))
    return out


def _flagged(text: str, risk: List[Tuple[str, str]]) -> Optional[str]:
    """这句话里的某个 token 有没有被风险节点名。返回风险原话，没有返回 None。

    token 先过 `fact_gate._usable_token`：风险节里"疑似中或优良"这种**两字档位词**也在 token 里，
    不筛的话"阴影质量：优良即可"会被无关地判成未核（实测就误伤过这一条）。
    """
    from . import fact_gate
    t = re.sub(r"\s+", "", text)
    for tok, raw in risk:
        if len(tok) >= 2 and fact_gate._usable_token(tok) and tok in t:
            return raw
    for tok, raw in risk:                     # 反向：风险条目引的那串就在本句里
        if len(raw) >= 6 and raw[:12] and raw[:12] in t:
            return raw
    return None


def _near_in_asr(name: str, asr: str) -> str:
    """ASR 里找不到这个名字时，找一个**同尾缀**的含混片段（刺右光照 ↔ 次表面光照）。
    只用于区分"音没听清"和"模型凭空补"，不做任何自动改写。"""
    m = _TAIL.search(name)
    if not m:
        return ""
    tail = m.group(0)[-2:]
    for mm in re.finditer(r"[\u4e00-\u9fa5]{0,6}" + tail, asr):
        seg = mm.group(0)
        if seg and seg != name:
            return seg
    return ""


def _rows(sec: List[str]) -> List[str]:
    """一节里的条目：表格行取首列+建议值，普通行整行。"""
    out = []
    for line in sec:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            cells = [c for c in cells if c and set(c) - set(":- ")]
            if cells and cells[0] not in ("设置项", "维度", "项"):
                out.append("：".join(cells[:2]))
        else:
            s = re.sub(r"^[-*•]\s*|^\d+[.、)]\s*", "", line).strip()
            if s:
                out.append(s)
    return out


def check(md: str, transcript: str, material_id: Any = "",
          confirmations: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """逐条判：能不能照做。返回 {rows, pending, hard, ready_for_poster, stats}。

    `confirmations`：人工回听/游戏截图之后的升档记录
    `[{item: 建议原句(可前缀匹配), source: 出处, result: 核出来的结论, by: 谁, at: 时间}]`，
    一条建议要升档必须 source + result 都有——跟 fact_gate.check_override 同一个规矩。
    """
    asr = str(transcript or "")
    nums = _asr_numbers(asr)
    risk = _risk_tokens(md)
    conf = [c for c in (confirmations or []) if isinstance(c, dict)]
    rows: List[Dict[str, Any]] = []
    hard: List[str] = []
    sec = sections(md)
    for name in ACTION_SECTIONS:
        for text in _rows(sec.get(name) or []):
            t = re.sub(r"\s+", "", text)
            if len(t) < 4:
                continue
            problems: List[str] = []
            quote = ""
            # 1) 数值：句里每个独立数字都要在 ASR 的独立数字里
            for m in _NUM.finditer(text):
                v = (m.group(1) + (m.group(2) or "")) if m.group(2) == "%" else m.group(1)
                if v not in nums:
                    problems.append("数值「%s」在转写里找不到独立出处" % v)
            # 1.5) ASCII 专名（SSAO / CMAA2 / VRS / ESC）同样要在 ASR 里出现——
            #      这类词最容易被模型"顺手补一个"，而它没有中文那种同尾缀可对，查无就是查无。
            asr_up = asr.upper()
            for w in _ascii_names(text):
                if w.upper() not in asr_up:
                    problems.append("专名「%s」转写里查无，需进游戏核对" % w)
            # 2) 选项名：ASR 逐字有 → 过；有同尾缀含混串 → 待回听；什么都没有 → 模型补的
            for raw in _OPT.findall(text):
                opt = _opt_name(raw)
                if not opt:
                    continue
                if opt in asr:
                    quote = quote or opt
                    continue
                near = _near_in_asr(opt, asr)
                if near:
                    problems.append("选项名「%s」转写里是含混的「%s」，需回听确认" % (opt, near))
                else:
                    problems.append("选项名「%s」转写里查无此词（疑似模型补的），需进游戏核对" % opt)
            # 3) 风险节点名了的，即使 ASR 有原话也只算口播说法
            flagged = _flagged(text, risk)
            if flagged:
                problems.append("风险节写着：%s" % flagged[:60])
            # 4) 效果承诺：没实测数据时不许写成确定句
            eff = _EFFECT.search(text)
            if eff and not re.search(r"未实测|没有.{0,6}数据|不承诺|以游戏内?为准", text):
                problems.append("写了效果承诺「%s」但没带实测口径" % eff.group(0))
            # 4.5) 行内自己就写了「需核对/需回听/待核/疑似/不承诺」——给建议的人当场承认这条没落地。
            #      PRD 的口径是"不许只在末尾【风险核查】承认有疑问、前面继续教玩家照做"，
            #      行内承认同理：这条不能算可用。要它变可用得把出处+结论记进待核清单
            #      （走下面的 confirmations），而不是把那句承认删掉——删掉承认只会更糟。
            inline = _UNSETTLED.search(text)
            if inline:
                problems.append("这条自己写了「%s」——给建议的同时承认没核完，按未落地处理"
                                % inline.group(0))
            # 5) 人工升档记录（要 source + result 都有才算这条核过）
            # 升档匹配认**这条建议的关键要素**，不认整句：凭据里点名了这个数值/选项名，
            # 并且写了出处与结论，才算这条核过。整句包含那种匹配在这条链上永远对不上
            # （事实闸的凭据条目是"「46亿」进了 主标题…"这种长句，跟建议原句不是一句话）。
            hit, keys = None, row_tokens(text)
            # 句子里没有可点名的数值/专名时（"其他选项：默认"这类），退回认这句本身——
            # 否则空 keys 会让 any(全在) 恒真，一条凭据就把全篇升档了（实测就错在这）。
            if not keys:
                keys = [t[:16]]
            for c in conf:
                blob = re.sub(r"\s+", "", "%s %s %s" % (c.get("item") or "", c.get("source") or "",
                                                         c.get("result") or ""))
                if blob and all(re.sub(r"\s+", "", k) in blob for k in keys):
                    hit = c
                    break
            if hit and str(hit.get("source") or "").strip() and str(hit.get("result") or "").strip():
                problems = []
            status = STATUS_OK if not problems else (
                STATUS_HEDGE if (flagged or any("按未落地处理" in p for p in problems)) else (
                    STATUS_LISTEN if any("需回听" in p for p in problems) else STATUS_GUESS))
            row = {"where": name, "text": text[:120], "material_id": material_id,
                   "quote": quote, "status": status, "problems": problems,
                   "confirmed_by": str((hit or {}).get("by") or ""),
                   "confirmed_at": str((hit or {}).get("at") or "")}
            rows.append(row)
            if problems:
                hard.append("%s：%s｜%s" % (name, text[:46], "；".join(problems[:2])))
    pending = [r for r in rows if r["status"] != STATUS_OK]
    # 标题里的"7 步 / 6 项 / 5 处"要跟实际可照做的条数对得上（2026-10-22 复核点名：
    # pro 新 83 三个标题分别写 7 步/6 项/5 处，行动表却有二十余行——数不对，玩家按标题预期就错）。
    n_action = len([r for r in rows if r["where"] == "行动建议"])
    for r in rows:
        if r["where"] != "标题候选":
            continue
        m = re.search(r"(\d+)\s*(?:步|项|处|条|个)", r["text"])
        if not m:
            continue
        claim = int(m.group(1))
        if abs(claim - n_action) > 1:
            r["problems"].append("标题写「%s」，行动建议实际 %d 条——数字对不上" % (m.group(0), n_action))
            r["status"] = STATUS_GUESS if r["status"] == STATUS_OK else r["status"]
            hard.append("标题候选：%s｜标题写「%s」但行动建议有 %d 条" % (r["text"][:36], m.group(0), n_action))
    return {"rows": rows, "pending": pending, "hard": hard,
            "ready_for_poster": not hard,
            "stats": {"rows": len(rows), "ok": len(rows) - len(pending), "pending": len(pending)},
            "note": "判的是「玩家能不能照做」，不是游戏事实真假；升档要人工回听或游戏截图，"
                    "并把出处与结论记进 confirmations"}


_NUM_UNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:亿|万|%|成|倍|年|级|小时|分钟|秒|点|元|档|步|项|处|条|X)")


def row_tokens(text: str) -> List[str]:
    """一条建议里"要是没落地就不该照做"的关键要素：带单位的数值、ASCII 专名、设置项名。

    刻意**不**收裸数字（"2""40"这种两位以内的纯数）：它会在成品里到处撞上不相干的字，
    实测就把一张干净的稿子误判成越界（"2分40秒"那行的 40 撞进别处）。
    要卡数值就带单位一起卡：46亿 / 20% / 2分40秒 / 10档。
    """
    out = [m.group(0).strip() for m in _NUM_UNIT.finditer(text)]
    out += _ascii_names(text)
    out += [n for n in (_opt_name(r) for r in _OPT.findall(text)) if n]
    seen, uniq = set(), []
    for t in out:
        k = re.sub(r"\s+", "", t)
        if len(k) >= 2 and k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq


def poster_block(rows: List[Dict[str, Any]], plan: Dict[str, Any]) -> List[str]:
    """待核的那几条，有没有真的进到成品（海报字段／发布标题）里。

    为什么不在这里直接判"稿子里有待核项就全拦"：人是编辑。**稿子里留着内部待核清单、
    成品里避开它们**是正常且正确的做法；越界的定义是"没落地的选项名/数值被印到玩家会照做的位置"。
    位置判据复用 `fact_gate._fields/_slot_of`，不另立一份卖点清单。
    """
    from . import fact_gate
    fields = fact_gate._fields(plan or {})
    out: List[str] = []
    for r in rows or []:
        if r.get("status") == STATUS_OK:
            continue
        # 只有"让玩家照做"的两节参与越界匹配。【对你的影响】是叙述句，
        # 实测那样会误伤：一句"帧数杀手集中在阴影、SSAO、泛光…"里的 SSAO，
        # 会把一张合法的「SSAO 关闭」卡片连坐成越界——**碰巧提到 ≠ 教人照做**。
        if r.get("where") not in ("行动建议", "标题候选"):
            continue
        for tok in row_tokens(r.get("text") or ""):
            t = re.sub(r"\s+", "", tok)
            if not fact_gate._usable_token(tok):
                continue
            for label, txt in fields:
                if t and t in re.sub(r"\s+", "", txt) and fact_gate._slot_of(label) == "卖点":
                    out.append("第 %s 节的「%s」还挂着「%s」（%s），却出现在 %s：%s"
                               % (r.get("where"), r.get("text")[:22], r.get("status"),
                                  (r.get("problems") or [""])[0][:40], label, txt[:26]))
                    break
            else:
                continue
            break
    return out


def gate_lines(report: Dict[str, Any], cap: int = 6) -> List[str]:
    """给界面/manifest 用的中文行（跟 fact_gate 的 hard/soft 一个形状）。"""
    out = []
    for x in (report or {}).get("hard") or []:
        out.append(str(x))
    return out[:cap]


def sidecar_path(out_dir: str, summary_id: Any) -> str:
    import os
    return os.path.join(str(out_dir), "action_gate_%s.json" % summary_id)
