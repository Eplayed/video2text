"""fact_gate.py — 把整理稿【风险核查】当成机器可读的信号，拦住"没核过的数变成卖点"。

为什么要这一层（2026-10-21，号主抽查 87/85/83 三篇已发成品后的整改任务书）：
整理稿阶段模型**已经**把疑点写进【风险核查】了——87 那条明写"「46亿」疑似转写或口误，
单位需核实"。可到了成品，同一个数被放大字小标题"还能卖46亿"，配文还写"这份清单逐项核对过"。
原因不是模型忘了，而是**全仓没有任何代码读这一节**：拆解提示词只说"只能用素材里出现过的事实与数字"，
而待核数本来就写在素材里，于是自我一致即算通过；`draft_audit._cross_check` 拿的"素材池"
就是整合稿自己（`web/app.py` 里 sources=summary.content），`_reader_facing()` 还把这节剥掉了。
公众号线反而读了（`graphics/channels/wechat.py` 拿它做一张卡）。

四条口径写在这里，接手别翻回去：
1. **机器只判"上游标过没标 + 出现在哪个位置"，不判断游戏事实真假。** 不许再叫一个模型打分来代替人工核对。
2. **只有"卖点位"才阻断。** 同一颗待核 token 印在主标题/副标题/栏目名/卡片标题/大字 = 把它当卖点 → 拦；
   只出现在卡片说明、警示、配文里 = 叙述 → 进"要人看一眼"清单。第一版就是这么定的，
   因为 87 的"还能卖46亿"是卡片标题、85 的"+20%"是大字，而"2分40秒不能当固定标准"那种
   全拦会让每一篇都发不出去——闸口一旦被嫌烦就会被关掉，那比没有更糟。
3. **风险条本身分轻重。** 「疑似转写/口误/单位需核实/未给数值/不承诺」是重（可以拦）；
   「是否官方定名/写法需核对/不能当固定标准」是轻（只提示）。分级看原文关键词，不看模型心情。
4. **旧稿不偷偷改写。** 已经发出去的内容一律不动，这里只拦新的出图，并把老成品列成待人工处理清单。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

RISK_HEADING = "风险核查"
# 整理稿那一节的**标题写法模型不统一**（2026-10-29 实测，同一份素材两版稿）：
#   10-26 那版写 `## 六、事实核查清单`，11 条 `- [ ]` 全在里面；
#   10-29 那版把 `**风险核查**：…` 写在一条 `- [ ]` 里，标题位置根本没有那一节。
# 以前只认字面「风险核查」→ 前一版一条都抓不到，等于上游写了清单没人读
# （号主发出去那篇小红书的「愈合祷言 40%/200%法强」「切目标不掉连击点」正是这么漏的）。
RISK_HEADINGS = ("风险核查", "事实核查", "核查清单", "发布前自查", "自查清单")
# 小节结束：既认老式 `【…】` 分节，也认 markdown 的 `## 标题`（配图素材那节以前被整段吞进风险清单）
_MD_HEAD = re.compile(r"^#{1,6}\s")
# 条目行前缀 `- [ ]` / `- [x]` / `* ` / `1. `
_ITEM_PREFIX = re.compile(r"^(?:[-*+]\s*\[[ xX]?\]\s*|[-*+]\s*|\d+[.、)]\s*)")
# 图片行与纯链接行不是"说法"，是排版素材
_MEDIA_LINE = re.compile(r"^!?\[[^\]]*\]\(")
# 老式分节标题 `【关键数据】`：遇到它就认为核查那一节结束了
_BRACKET_HEAD = re.compile(r"^\s*【[^】]{2,12}】")
# 条目自己声明"已经核过"的，不是待核项。核查那一节里混着「与上游数据库一致，已保留标准写法」
# 这类记录，把它们当"待核"列进闸口＝噪音，闸口一旦被嫌烦就会被关掉（这层第一版就吃过这个教训）。
_DONE_MARK = re.compile(r"一致[，。]|已保留标准写法|无需核对|本节无补充|修正[，。]")
# 一条风险项里"被点名的东西"通常用中文引号或书名号圈出来；圈出来的就当 token 用
_QUOTED = re.compile(r"[“\"『「]([^”\"』」]{1,24})[”\"』」]")
# 没被引号圈住时，退而抓"数字+单位"这种一眼可辨的量词组（46亿 / 5% / 2分40秒 / 28点法伤）
_NUM_UNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:亿|万|千)?[+%]?|\d+\s*(?:年|级|小时|分钟|秒|点|元|成)")
# 上游说"没数值/不承诺"时，成品里就不许出现这类绝对化效果句
_ABSOLUTE_EFFECT = re.compile(
    r"毛边全消|全消|彻底消除|完全消除|一定(?:提升|不卡|流畅)|必不卡|稳了|秒变|翻倍|"
    r"肉眼可见(?:提升|变多)?|流畅度拉满|帧数起飞")
# "已核对"这类措辞必须有可定位的核验记录，不能靠一句自述
_CLAIM_VERIFIED = re.compile(r"逐项核对|已核对|核对过|都核过|核实过|确认无误")
# 重级风险（可以拦）：数字/单位本身可疑
_HEAVY = re.compile(r"疑似转写|转写错|口误|单位需|需核实|未给.{0,8}数值|不承诺|举例|"
                    r"未说明是否|未给出|主观判断|不能确定|无法确定|仍需")
# 轻级风险（只提示）：名称写法、单次样本不能当标准这类
_LIGHT = re.compile(r"官方定名|定名|写法|不能当固定标准|单次|受.{0,8}影响")
# 卖点位：这些位置出现待核 token 就是"把它当卖点"
# 「大字注释」也在内——它紧贴大字数字，读者会当成数字的说明读（2026-10-29 复核点名的漏项之一）
_SELLER_SLOTS = ("主标题", "副标题", "栏目名", "大字数字", "大字标签", "大字注释", "警示标题")
# 机制类待核说法的两档（判据与分级同源，`risk_ledger` 也读这一份）
_MECH_NUM = re.compile(r"\d")
_MECH_WORD = re.compile(r"伤害|治疗|法强|急速|暴击|命中|冷却|CD|公共冷却|点数|秒|级|收益|递减|递增|"
                        r"返还|阈值|概率|几率|数值|层|点天赋")
_BINARY = re.compile(r"不再|不掉|不会掉|不消耗|无冷却|必定|一定能|免疫|保留|不受|不吃|直接触发")
# 同一条卡片的标题与说明之间，只收最稳的几对反向操作口径（跨字段比，不在单字段里找）
_CONTRADICTION = [("别拉满", "拉满"), ("不要拉满", "拉满"), ("别一次拉满", "推荐拉满"),
                  ("别调高", "调高"), ("别降", "调低"), ("不要开", "开启")]
# 尾注与话题标签不算正文句子（83 的截断就藏在尾注前面）
_TAIL_NOTE = re.compile(r"\n*(?:本文由\s*AI|内容源自公开|侵权必究|如有侵权请联系删除)[^\n]*")
_TAGS = re.compile(r"(?:\s*#[^\s#]+)+\s*$")


def _inline_item(line: str, idx: int, head: str) -> str:
    """标记和内容写在**同一行**时把内容取出来。

    两种真实形状（以前按物理行切，整行被当标题丢掉，于是内容永远读不到）：
      · `【风险核查】8000 金币那条待核，不许写成建议`
      · `- [ ] **风险核查**：口播中提到的"着热之痛"疑似语音识别错误…`
    而 `### 风险核查（发布前必看）` 这种只是标题的注解，不能当条目。
    """
    rest = line[idx + len(head):].lstrip("】 ").strip()
    m = re.match(r"^[：:]\s*(.+)$", rest)
    if m:
        rest = m.group(1).strip()
    if not rest or rest.startswith(("（", "(")):
        return ""
    return rest if len(rest) >= 6 else ""


def risk_section(content: str) -> Dict[str, Any]:
    """定位整理稿里"那一节核查清单"：返回 {start, end, inline}，没有就返回 {}。

    单独拿出来是因为**两处要用同一个位置**：`risk_items` 读它，
    `risk_ledger` 把上一版没核完的条目并回去也要用它（另起一节写在文末，
    解析器只会读到第一处，继承就等于没发生）。
    """
    lines = str(content or "").splitlines()
    start, head, inline = -1, "", ""
    for n, ln in enumerate(lines):
        for h in RISK_HEADINGS:
            i = ln.find(h)
            if i < 0:
                continue
            start, head, inline = n, h, _inline_item(ln, i, h)
            break
        if start >= 0:
            break
    if start < 0:
        return {}
    end = len(lines)
    for n in range(start + 1, len(lines)):
        s0 = lines[n].strip()
        # 小节边界：老式 `【…】` 分节、markdown 的 `## 标题` 都算结束。
        # 以前只认前者，于是 `## 配图素材（视频关键帧）` 后面整段图片 markdown 被当风险项抓走
        # （2026-10-29 实测抓到 6 条，前 5 条是关键帧链接行——真该挡的那条反倒不在里面）。
        if _MD_HEAD.match(s0) or _BRACKET_HEAD.match(s0):
            end = n
            break
    return {"start": start, "end": end, "inline": inline}


def risk_items(content: str) -> List[Dict[str, Any]]:
    """把整理稿那一节核查清单解析成结构化条目：[{raw, tokens, heavy}]。

    自由文本实测有三种形状：破折号列表（87）、数字列表（83/85）、整段没有引号只描述。
    token 先取引号里的，取不到再退到"数字+单位"。**匹配不上 token 的条目照样保留**（raw），
    它进不了阻断判据，但要出现在人工复核清单里——不能因为机器认不出就当这条不存在。
    """
    lines = str(content or "").splitlines()
    sec = risk_section(content)
    if not sec:
        return []
    start, end, inline = sec["start"], sec["end"], sec["inline"]
    raws: List[str] = [inline] if inline else []
    for ln in lines[start + 1:end]:
        s0 = ln.strip()
        if not s0 or _MEDIA_LINE.match(s0):
            continue
        s = _ITEM_PREFIX.sub("", s0).replace("**", "").strip()
        if len(s) < 4:
            continue
        raws.append(s)
    raws = [r for r in raws if not _DONE_MARK.search(r)]
    out: List[Dict[str, Any]] = []
    for s in raws:
        # 引号里的整串 + 串内/串外的"数字+单位"都要收。85 那条引号圈的是
        # 「熔火之心内伤害提升20%」整句，成品里写的是 bignum「+20%」——只拿整句去匹配永远对不上，
        # 于是那一处真卖点漏拦（2026-10-21 第一版实测）。数字单独抽出来才匹配得上。
        toks = [t.strip() for t in _QUOTED.findall(s) if t.strip()]
        toks += [m.group(0).strip() for m in _NUM_UNIT.finditer(s) if len(m.group(0).strip()) >= 2]
        seen_t, uniq = set(), []
        for t in toks:
            k = re.sub(r"\s+", "", t)
            if k and k not in seen_t:
                seen_t.add(k)
                uniq.append(t)
        out.append({"raw": s, "tokens": uniq[:8], "heavy": bool(_HEAVY.search(s))})
    return out


def _usable_token(tok: str) -> bool:
    """这颗 token 值不值得拿去做阻断判据。

    「重要」「名称」这种两字常用词会被引号圈出来（83 那条"法术密度设置成重要"），
    拿去匹配成品文字等于到处命中——第一版就在这误拦了三处。所以：
    含数字、或含字母（CMAA2/VRS 这类专名）、或 ≥4 字，才允许参与阻断；其余只进提示。
    """
    t = re.sub(r"\s+", "", tok)
    if len(t) < 2:
        return False
    if re.search(r"\d", t) or re.search(r"[A-Za-z]", t):
        return True
    return len(t) >= 4


def claim_kind(raw: str) -> str:
    """这条待核说法是哪一类机制断言。

    只给**上游已经标过待核**的条目分级（判据在台账/清单里，不拿它去全文找茬——
    那会变成号主明确否掉的"全局关键词封杀"）。两档会触发发布前证据门：
      · 机制数值：带量词的机制改动（"弹跳递增40%、吃满200%法强"）；
      · 二元机制：非此即彼的机制结论（"切目标不掉连击点"）——这类最坏，
        因为它没有数字，读者一眼看不出是待核说法，而玩家会照着练号。
    """
    s = str(raw or "")
    if _MECH_NUM.search(s) and _MECH_WORD.search(s):
        return "机制数值"
    if _BINARY.search(s):
        return "二元机制"
    return "其他"


# 二元机制条目的"可匹配签名"：圈住二元词前后一小段（`切目标不掉连击点`）。
# 为什么非要单独抽：这类句子常常一个数字都没有、也没引号，`tokens` 是空的，
# 老判据直接看不见它（2026-10-29 Codex 复核点名的正是这条：切目标那条没有可匹配 token）。
_BIN_WINDOW = re.compile(
    r"[\w\u4e00-\u9fff]{0,10}(?:不再|不掉|不会掉|不消耗|无冷却|必定|一定能|免疫|保留|不受|不吃)"
    r"[\w\u4e00-\u9fff]{0,8}")


def claim_signature(raw: str) -> List[str]:
    out = [m.group(0) for m in _BIN_WINDOW.finditer(str(raw or ""))]
    return [o for o in out if len(re.sub(r"\s+", "", o)) >= 5]


def official_support(phrase: str, blobs: List[str]) -> str:
    """这条说法有没有**一手文本**撑着（官方公告/蓝贴正文里能找到同一句）。

    只认长度 ≥6 的短语（裸"40%"这种谁都能撞上，拿它当证据等于自欺）。
    命中就返回出处的序号，调用方据此把硬拦降级成"一手公告里有这句"的提示。
    """
    key = re.sub(r"\s+", "", str(phrase or ""))
    if len(key) < 6:
        return ""
    for n, blob in enumerate(blobs or [], 1):
        if key in re.sub(r"\s+", "", str(blob or "")):
            return "官方一手文本第 %d 篇" % n
    return ""


def _fields(plan: Dict[str, Any]) -> List[Tuple[str, str]]:
    """plan 里所有**会印到图上**的文字位置（含配文），带位置标签。"""
    from . import ai_poster       # 延迟导入：ai_poster 反过来也用本模块
    out: List[Tuple[str, str]] = []
    pages = ai_poster.plan_pages(plan) or []
    multi = len(plan.get("pages") or []) > 1
    for n, pg in enumerate(pages, 1):
        for label, txt in ai_poster.ink_texts(pg):
            out.append(("第%d页·%s" % (n, label) if multi else label, txt))
    pub = plan.get("publish") or {}
    for k in ("copy_text", "digest", "source_note", "keyword_reply", "xhs_copy"):
        if pub.get(k):
            out.append(("配文·" + k, str(pub.get(k))))
    for k in ("tt_titles", "xhs_titles"):
        for j, t in enumerate(pub.get(k) or [], 1):
            out.append(("配文·%s%d" % (k, j), str(t)))
    # 话题标签也是会发出去的字段：#46亿 这种待核数挂在标签里同样算卖点
    tags = [str(t) for t in (pub.get("xhs_tags") or []) if str(t).strip()]
    if tags:
        out.append(("配文·xhs_tags", " ".join(tags)))
    return out


def _slot_of(label: str) -> str:
    """位置标签 → 卖点/叙述。带"卡片N标题"算卖点，"卡片N说明"算叙述。"""
    base = label.split("·")[-1]
    if base in _SELLER_SLOTS or re.match(r"卡片\d+标题", base) or re.match(r"对照\d+$", base):
        return "卖点"
    # 发布标题（头条候选 / 小红书候选）也是卖点位：待核的数写进标题和写进图上大字没区别。
    # 2026-10-21 Codex P2 点名这一档——原来只查 plan 里那些会印上图的位置，
    # 而"实际发出去"的标题/正文是另一份字段，风险数挪到那儿就绕过了闸口。
    if re.match(r"(?:tt|xhs)_titles\d+$", base):
        return "卖点"
    return "叙述"


def merge_items(parsed, ledger):
    """稿子里读到的 + 台账里还开着的，合成一份（同一条说法只留一条）。

    为什么要合：模型这一版**漏写**不等于这条事实核过了。台账是外置记忆，漏写时它兜底；
    写全时只是重复登记一次，不改变判据。
    """
    out, seen = [], set()
    for it in list(ledger or []) + list(parsed or []):
        raw = str(it.get("raw") or "").strip()
        if not raw:
            continue
        key = re.sub(r"\W+", "", raw)[:40].lower()
        if key in seen:
            continue
        seen.add(key)
        merged = dict(it)
        merged.setdefault("tokens", list(merged.get("tokens") or []))
        merged.setdefault("kind", claim_kind(raw))
        # 台账条目是外置记忆，不带稿内那份字段；缺了老判据会 KeyError
        merged.setdefault("heavy", bool(_HEAVY.search(raw)))
        out.append(merged)
    return out


def mechanism_gate(items, fields, official_blobs):
    """机制类待核说法的证据门：只有第三方转述撑着，就不许以肯定语气发出去。

    与老判据的分工（别混）：老那条只管"重级措辞 + 出现在卖点位"，很窄、只拦最像的；
    这一条管**机制数值与二元机制结论**，扫所有会发出去的字段（图上文字、两份配文、
    两份标题、摘要、话题标签）——号主挨的那两句正是"数值进卡片说明 + 机制结论进小红书标题"。
    仍然不是关键词封杀：入口是"上游/台账把这条标成了待核"，没标过的一律不看。
    解除只有两条路：一手文本里有同一句（降级成提示），或台账里凭可定位证据解除。
    """
    hard, soft = [], []
    flat = [(label, re.sub(r"\s+", "", txt)) for label, txt in fields]
    for it in items:
        if str(it.get("status") or "") == "resolved":
            continue
        kind = it.get("kind") or claim_kind(str(it.get("raw") or ""))
        if kind not in ("机制数值", "二元机制"):
            continue
        phrases = [str(t) for t in (it.get("tokens") or [])] + claim_signature(str(it.get("raw") or ""))
        hit = None
        for ph in phrases:
            key = re.sub(r"\s+", "", ph)
            if len(key) < 4:
                continue                       # 裸"40%"谁都能撞上，不算命中
            where = [lb for lb, txt in flat if key and key in txt]
            if not where:
                continue
            sup = official_support(ph, official_blobs or [])
            if sup:
                hit = ("支持", ph, sup)
                break
            if hit is None:
                hit = ("缺", ph, where)
        if not hit:
            continue
        if hit[0] == "支持":
            soft.append("「%s」上游标过待核，但一手文本里有同一句（%s）——按一手发，别再叠口播的说法"
                        % (hit[1], hit[2]))
            continue
        hard.append("%s「%s」出现在 %s，支撑它的只有第三方转述、没有可定位证据（上游标注：%s）；"
                    "要么补官方原句、要么写明版本和日期的本人实测，要么删掉/改成不误导的说法"
                    % (kind, hit[1], "、".join(hit[2][:3]), str(it.get("raw"))[:60]))
    return hard, soft



def check(plan: Dict[str, Any], content: str,
          copy_extra: str = "", extra_items=None,
          official_blobs=None) -> Tuple[List[str], List[str]]:
    """返回 (必须拦的, 要人看一眼的)。

    `extra_items`：台账里**这一版稿没写出来、但还没解除**的待核条目（`risk_ledger.open_items`），
    与稿内清单合并后再判——模型漏写不能让风险静默消失（2026-10-29 小红书反例）。
    `official_blobs`：一手文本（官方公告/蓝贴正文）。用来把"只有第三方口播撑着"的机制说法
    与"一手公告里确实有这句"分开：后者不拦，只提示按一手发。
    """
    items = merge_items(risk_items(content), extra_items or [])
    fields = _fields(plan)
    if copy_extra:
        fields.append(("配文·copy_text", copy_extra))
    hard: List[str] = []
    soft: List[str] = []
    if not items:
        soft.append("整合稿里没有【风险核查】这一节，机器无从判断哪个数是待核的；"
                    "关键数字、版本号、效果承诺请人工核一手来源再发。")
    for it in items:
        for tok in it["tokens"]:
            t = re.sub(r"\s+", "", tok)
            hits = [(label, txt) for label, txt in fields
                    if t and t in re.sub(r"\s+", "", txt)]
            if not hits:
                continue
            sellers = [(l, x) for l, x in hits if _slot_of(l) == "卖点"]
            if it["heavy"] and sellers and _usable_token(tok):
                for label, txt in sellers[:2]:
                    hard.append("「%s」进了 %s（%s），可整理稿【风险核查】写着：%s"
                                % (tok, label, txt[:28], it["raw"][:70]))
            else:
                soft.append("「%s」出现在 %s，上游标注：%s"
                            % (tok, "、".join(l for l, _ in hits[:2]), it["raw"][:60]))
    mech_hard, mech_soft = mechanism_gate(items, fields, official_blobs or [])
    hard.extend(mech_hard)
    soft.extend(mech_soft)

    hedge = [it["raw"] for it in items
             if re.search(r"不承诺|未给.{0,8}数值|没有.{0,8}实测|主观判断|无.{0,4}数据", it["raw"])]
    if hedge:
        for label, txt in fields:
            m = _ABSOLUTE_EFFECT.search(txt)
            if m:
                hard.append("%s 写了「%s」，但整理稿已说明没有实测数据（%s）"
                            % (label, m.group(0), hedge[0][:48]))
    for label, txt in fields:
        if _CLAIM_VERIFIED.search(txt):
            soft.append("%s 写了「已核对」一类话：除非每条都有可定位的核验记录，否则这句得删——"
                        "列出核实点不等于完成核实。" % label)
    soft.extend(_contradictions(plan))
    seen, h, s = set(), [], []
    for x in hard:
        if x not in seen:
            seen.add(x)
            h.append(x)
    seen = set()
    for x in soft:
        if x not in seen:
            seen.add(x)
            s.append(x)
    return h, s


def _contradictions(plan: Dict[str, Any]) -> List[str]:
    """同一条卡片的**标题与说明**给了相反的操作口径（83 那处：标题"视野别一次拉满"、
    说明"镜头视野推荐拉满"）。只在同一张卡片内部比，跨段比会把"光线追踪别开极佳"
    这种正常句子误判成矛盾（第一版就误报了两次）。"""
    from . import ai_poster
    out = []
    for n, pg in enumerate(ai_poster.plan_pages(plan) or [], 1):
        for i, c in enumerate(pg.get("cards") or [], 1):
            t = re.sub(r"\s+", "", c.get("t") or "")
            d = re.sub(r"\s+", "", c.get("d") or "")
            if not t or not d:
                continue
            for neg, pos in _CONTRADICTION:
                if neg in t and pos in d:
                    out.append("第%d页·卡片%d：标题说「%s」，说明却写「%s」——"
                               "同一件事给了相反口径，得由人定一个"
                               % (n, i, t[:20], d[:26]))
                    break
    return out


def guide_gaps(plan: Dict[str, Any]) -> List[str]:
    """攻略体裁的五段最小结构缺哪几段（只对"选为攻略"的产物查）。

    判据是"有没有一段文字在干这件事"，不是查字数表：找不到就报**具体缺什么**，
    并明确允许把标题改成"介绍/观察"——不许逼模型去补它没有的事实（那正是编步骤的来源）。
    """
    blob = "\n".join(t for _, t in _fields(plan))
    if not blob.strip():
        return ["成品文字是空的，先出文案再谈结构"]
    need = [
        ("适用版本/适用对象", r"无限服|正式服|经典|版本|适用|老电脑|低配|高配|猎人|法师|牧师|萌新|\d+\s*级"),
        ("操作前提", r"前提|需要|先有|已开|装备|等级|声望|权限|配置|显存|GTX|RTX|内存"),
        ("按顺序做什么", r"先|再|然后|接着|步骤|第[一二三四1-4]|打开|进入|勾选|设置成|调到"),
        ("做完应看到什么", r"会看到|应看到|显示|变成|生效|结果|效果|掉落?出?|获得|不再"),
        ("失败或不适用时怎么办", r"如果|不行|没生效|卡顿?|失败|不适用|再降|重开|换个|以游戏内?为准|按配置"),
    ]
    return [name for name, pat in need if not re.search(pat, blob)]


def copy_is_cut(text: str) -> str:
    """配文尾句是不是断头话。返回空串表示没问题。

    83/85/80 三条成品配文都正好 260 字并断在句中（83「…帧数提升幅」），那是
    `_clip_publish` 里 `[:COPY_MAX]` 切的——**判断前必须先把合规尾注和话题标签剥掉**，
    否则结尾永远是尾注的"…必究。"，永远看不出正文被切过（第一版就在这漏判）。
    """
    t = str(text or "")
    t = _TAIL_NOTE.sub("", t)
    t = _TAGS.sub("", t)
    t = re.sub(r"\s+", "", t)
    if not t:
        return ""
    if t[-1] in "。！？…”』」】)":
        return ""
    if re.search(r"(?:的|和|与|或|、|，|；|是|在|把|向|为|比|约|第|地|得|而|且)$", t):
        return "结尾停在「…%s」，句子没写完" % t[-12:]
    if len(t) >= 240:
        return "结尾「…%s」没有句末标点且已经 %d 字，像被长度上限切过" % (t[-12:], len(t))
    return ""


def check_override(hard, evidence, checked_by=""):
    """人工放行要留**逐条凭据**：每一条待核都得有出处和结论，缺哪条就点名哪条。

    为什么要这一层（2026-10-21 Codex 复核 P1）：界面上那颗按钮写着"这些数我已人工核实"，
    后端收到的却只有一个 `force=True`，manifest 里只留 `overridden: true` ——
    一句话没记、一个出处没填，等于**给没核过的数盖了"已核实"的章**。
    现在：`evidence = [{"item": 闸口原话, "source": 出处/链接/实测位置, "result": 核出来的结论}]`，
    每条都要 source + result 都非空才算这条核过；`complete` 为假时成品仍然出（人是最终判断者），
    但 `publishable` 必须是 False，界面上也不许出现"已核实"字样。
    """
    def _n(s):
        return re.sub(r"\s+", "", str(s or ""))

    rows = []
    pool = [e for e in (evidence or []) if isinstance(e, dict)]
    for h in hard or []:
        key = _n(h)
        hit = None
        for e in pool:
            ei = _n(e.get("item"))
            if ei and (ei in key or key in ei):
                hit = e
                break
        src = str((hit or {}).get("source") or "").strip()[:300]
        res = str((hit or {}).get("result") or "").strip()[:300]
        rows.append({"item": str(h)[:300], "source": src, "result": res,
                     "ok": bool(src and res)})
    missing = [r["item"] for r in rows if not r["ok"]]
    return {"rows": rows, "missing": missing,
            "complete": bool(rows) and not missing,
            "checked_by": str(checked_by or "").strip()[:40]}


def legacy_report(rows: List[Dict[str, Any]]) -> List[str]:
    """把已经发出去的老成品列成待人工处理清单（只读，绝不改写它们）。"""
    out = []
    for r in rows:
        hard, soft = check(r.get("plan") or {}, r.get("content") or "")
        cut = copy_is_cut((r.get("pub") or {}).get("copy_text") or "")
        bits = []
        if hard:
            bits.append("待核事实进卖点 %d 处" % len(hard))
        if cut:
            bits.append("配文截断")
        if bits:
            out.append("整合稿 %s《%s》：%s" % (r.get("id"), (r.get("title") or "")[:22],
                                               "、".join(bits)))
    return out
