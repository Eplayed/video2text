# -*- coding: utf-8 -*-
"""claim_check.py — 机制改动逐条对：主体＋技能＋数值＋条件＋带日期的具体来源，缺一样都不算核过。

为什么要这一层（2026-11-02，wow-forever 复核《牛头人直接起飞》之后交接）：
108 那张海报上「德莱尼」改对了之后，仍然写着**矮人接手移速**、**亡灵急速 3%→2%**、
**魔网读取延到 15 分钟**、**兽人宠物天赋实为削弱**。这四句没有一句是"名字编错"，
专名闸（`term_check.roster_check`）逐条查过种族名，全都在官方枚举里——**它证明的是
"这个种族存在"，证明不了"这个技能属于这个种族"**。老事实闸同理：它认的是
"上游标过待核的那颗 token 有没有印到卖点上"，而这几句是**归属错了**，token 本身没被标过。

四条判据（写在这里，接手别翻回去）：
1. **默认不给过。** 机制断言必须能在登记表 `config/wow_claims.json` 里找到对应条目，
   条目要带 主体/技能/新旧数值/适用条件/来源 URL/发布日期/官方原句，原句还要能在
   `config/wow_official_20261026.txt` 那份一手快照里逐字找到。找不到就是没核。
2. **名字和数字各自出现过 ≠ 核过。** 判的是同一句里"谁的技能、改成多少、什么条件下"，
   所以：技能挂到别的种族 = 主体错配；句里的数不在该条目登记的数里 = 数值无出处；
   条目要求条件而句里没写 = 条件缺失。
3. **纠错句不算断言。** 「矮人未获得原野疾驰」「急速那条不属于亡灵」是在纠正常见误解，
   否定词必须**紧贴**被点名的技能/属性（前 8 字内）才按澄清处理；隔着一整句的"不"不管用，
   否则「亡灵并没有把急速降下来，移速提高了 5%」这种句子就绕过去了。
4. **通用新闻首页不算凭据。** 人工放行一条断言，出处必须就是那条登记自己的来源 URL
   （或同一条快照里的其它具体帖），并且要写核验人与日期；只写"官网看过"、给个 /news 首页，
   或者引一句没有日期的转述，都不通过。

明确**不做**的：不拿关键词去全文找茬决定"哪句可疑"（那是号主否过的全局封杀），
登记表面向的是"这一版素材正好改到的那些种族机制"；表里没有的种族机制断言一律按未核处理，
要发就先补登记（补的人负责把原句和日期抄进去）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
CLAIMS_FILE = ROOT / "config" / "wow_claims.json"

# 日期形状先整段挖掉再抽数：10 月 26 日、2026-10-26、10-26 都不是机制数值
_DATE = re.compile(r"20\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?|\d{1,2}月\d{1,2}日|\d{1,2}\s*[-/]\s*\d{1,2}")
_NUM = re.compile(r"\d+(?:\.\d+)?\s*(?:%|秒|分钟|小时|层|点|级|次|人)?")
# 机制词：出现即认为这句在说"改了什么"，不是闲聊
_MECH = re.compile(r"提高|提升|降到|提到|缩到|延长|减少|移除|新增|接手|翻倍|缩短|加成|上限|"
                   r"持续|回调|削弱|加强|不再|改为|改成|回归|获得|触发")
_ATTRS = ("移速", "移动速度", "骑乘", "急速", "暴击", "命中", "冷却", "精神",
          "伤害", "治疗", "回复", "回蓝", "回血", "生命", "血量", "宠物", "持续")
# 否定式澄清（判"类"不判样本词：任何写明"没有/不是/别当"的说法都算，见判据 3）
_NEG = re.compile(r"没有|没拿|没得|并未|并不是|不是|不属于|而非|并不|未获|未拿|不获得|无这"
                  r"|别当|别把|不要|不能|不可|并非|没有获得")
# 算例标记：明说"假设/估算/算例/仅算"的句子，句里多出来的数按算例看，不当官方数值
_ASSUME = re.compile(r"假设|估算|算例|仅算|只算|按.{0,10}算|举例")
# 评价性断言：这类句子登记不了、也不是官方说法，出现在成品里就是"替读者下结论"
_HYPE = re.compile(r"全职业|所有职业|都亏|必亏|实为削弱|最强|最弱|第一|T0|白给|血亏|稳赚|"
                   r"实测|亲测|体感|直接起飞")
# 强机制谓语（11-02 复验漏口一）：「免疫所有控制」这类**不落数值**的机制断言，
# check_fields 对它没有数可对账、也没有现成属性锚点，整字段判就借同卡已核句亮了绿灯。
# 这类词出现＝这句在主张一个可证伪的机制效果，登记原句与素材原文里都找不到就按"未核机制
# 断言"处理：界面标待核，出图前暂停该结论的待发资格。保守取控制/效果类强谓语，
# 普通修辞（很强/顺手/推荐）不在内，不把闲聊拦成机制错误。
_PRED = re.compile(r"免疫|无视|不受|打断|驱散|沉默|眩晕|冻结|恐惧|变形|反弹|吸血|穿透|"
                   r"斩杀|处决|复活|重生|闪避|格挡|招架|缴械|定身|控制|护盾|隐身|潜行|"
                   r"必暴|必定|无视地形|免疫伤害")
# 只按**句末**切：分号与逗号都不切（卡片说明习惯用「；」串三个要点，
# 切了就变成"主体在前半句、数值在后半句"，「裂隙圈内读取能延到15分钟」就是这么溜过去的）。
_SENT_SPLIT = re.compile(r"[。！？\n]")
# 原子断言边界（句内再切）：逗号/顿号/分号。数值只跟**同一小句（或其继承链）**里的
# 主体/技能对账——11-02 复验反例：「牛头人原野疾驰…15%；天裔天穹视界…60%」整句取
# 并集，"每个数字都存在"就掩护了"数字挂错技能"。
_SPLIT2 = re.compile(r"[，、；,;]")
_QUOTE_MAP = {u"’": "'", u"‘": "'", u"“": '"', u"”": '"'}


def _norm(text: str) -> str:
    """比对用的归一化：去空白、弯引号折成直引号。"""
    s = re.sub(r"\s+", "", str(text or ""))
    for a, b in _QUOTE_MAP.items():
        s = s.replace(a, b)
    return s


def load(path: Optional[Path] = None) -> Dict[str, Any]:
    """读登记表。读不到就返回空表，由调用方把"这一项没判"说出口（不许演成通过）。"""
    p = Path(path) if path else CLAIMS_FILE
    try:
        with open(str(p), "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except Exception:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        return {}
    return data


def snapshot_text(reg: Dict[str, Any]) -> str:
    """登记表指定的一手快照原文（文件读不到返回空串）。"""
    rel = str(((reg or {}).get("meta") or {}).get("snapshot_file") or "").strip()
    if not rel:
        return ""
    try:
        return (ROOT / rel).read_text(encoding="utf-8")
    except Exception:
        return ""


def registry_problems(reg: Dict[str, Any]) -> List[str]:
    """登记表自己合不合格——它不合格时闸必须说"按缺席判"，不能默默放行。

    一条登记要凑齐：主体、技能（中或英）、来源 URL、发布日期、官方原句，
    且原句能在快照里逐字找到，状态还得是"已核一手"。
    """
    out: List[str] = []
    snap = _norm(snapshot_text(reg))
    if not snap:
        return ["一手快照读不到（meta.snapshot_file），登记表里每条原句都没法对照——这一项按缺席判"]
    seen = set()
    meta = (reg or {}).get("meta") or {}
    for e in (reg or {}).get("claims") or []:
        eid = str(e.get("id") or "?")
        if eid in seen:
            out.append("登记 id 重复：%s" % eid)
        seen.add(eid)
        miss = []
        if not str(e.get("race") or "").strip():
            miss.append("主体")
        if not (str(e.get("skill") or "").strip() or str(e.get("skill_en") or "").strip()):
            miss.append("技能")
        if not str(e.get("source_url") or meta.get("source_url") or "").strip():
            miss.append("来源 URL")
        if not re.search(r"20\d{2}-\d{1,2}-\d{1,2}", str(e.get("source_date") or "")):
            miss.append("发布日期")
        if not str(e.get("quote") or "").strip():
            miss.append("官方原句")
        if str(e.get("status") or "") != "已核一手":
            miss.append("核验状态不是「已核一手」（现在是「%s」）" % (e.get("status") or "空"))
        if miss:
            out.append("登记 %s 缺：%s" % (eid, "、".join(miss)))
            continue
        if _norm(e["quote"]) not in snap:
            out.append("登记 %s 的官方原句在快照里逐字找不到：%s" % (eid, str(e["quote"])[:60]))
    return out


def _numbers(text: str) -> List[str]:
    """句里的机制数值 token（日期先挖掉，"+1%" 与 "1%" 算同一个）。"""
    s = _DATE.sub(" ", str(text or ""))
    out = []
    for m in _NUM.finditer(s):
        t = re.sub(r"\s+", "", m.group(0)).lstrip("+")
        if t:
            out.append(t)
    return out


def _numbers_pos(text: str) -> List[Tuple[str, int, int]]:
    """带位置的机制数值 token：(token, 起点, 终点)。

    日期用**等长空格**补位而不是删掉——位置必须跟原句对齐，否则否定区间
    （按原句坐标算）套不到数上。
    """
    s = _DATE.sub(lambda m: " " * len(m.group(0)), str(text or ""))
    out = []
    for m in _NUM.finditer(s):
        t = re.sub(r"\s+", "", m.group(0)).lstrip("+")
        if t:
            out.append((t, m.start(), m.end()))
    return out


def _index(reg: Dict[str, Any]) -> Dict[str, Any]:
    """技能别名 → 条目；属性词 → 允许拥有它的种族集合；条件词 → 带这条件的条目。"""
    by_skill: Dict[str, List[Dict[str, Any]]] = {}
    by_race: Dict[str, List[Dict[str, Any]]] = {}
    by_attr: Dict[str, set] = {}
    by_cond: Dict[str, List[Dict[str, Any]]] = {}
    for e in (reg or {}).get("claims") or []:
        for n in [_norm(e.get("skill")), _norm(e.get("skill_en"))] + \
                  [_norm(a) for a in (e.get("aliases") or [])]:
            if n:
                by_skill.setdefault(n, []).append(e)
        by_race.setdefault(_norm(e.get("race")), []).append(e)
        for a in list(e.get("attrs") or []):
            by_attr.setdefault(_norm(a), set()).add(str(e.get("race") or ""))
        # 条件词也是锚：11-02 那句「魔网裂隙读取能延到15分钟」整句没有种族名、
        # 也没点名「阅读魔网」这个技能别名，只有条件词「魔网/裂隙」——不拿条件当锚，
        # 句里的数就没人去对，15 分钟就是这么漏的
        for c in list(e.get("condition") or []):
            n = _norm(c)
            if n and len(n) >= 2:
                by_cond.setdefault(n, []).append(e)
    return {"skill": by_skill, "race": by_race, "attr": by_attr, "cond": by_cond}


def _hits(needles: Dict[str, Any], text: str) -> List[Tuple[str, int]]:
    """命中键 + 位置。同一个别名只取最靠前的位置，长名优先由调用方排序保证。"""
    out = []
    for key in sorted(needles or {}, key=len, reverse=True):
        i = text.find(key) if key else -1
        if i >= 0:
            out.append((key, i))
    return out


# 职业只是「命令」的适用条件。职业名独立出现不能定位到旧种族条目。
_GENERIC_CONDITIONS = {"攻击", "法术", "术士", "猎人"}
_XP_RESULT = r"(?:非?亡灵)(?:约)?\d+(?:\.\d+)?万?经验"
_QUEST_XP_OBSERVATION = re.compile(
    r"(?:(?:非?亡灵(?:经验)?|经验(?:统计|收益|记录))[:：]?)?(?:按睡袋(?:增益|buff)(?:统计)?[，,：:]?)?"
    + _XP_RESULT + r"(?:[，,]" + _XP_RESULT + r")?[。]?$", re.I)
_ROUTE_ORDINAL = re.compile(r"第一(?=(?:个|扇)?(?:房间|路口|箱子|门)|间|处站位|步)")


def _condition_hits(idx, text):
    named_entries = {id(entry) for name, _ in _hits(idx["skill"], text) for entry in idx["skill"][name]}
    for name, pos in _hits(idx["cond"], text):
        if name not in _GENERIC_CONDITIONS or any(
                id(entry) in named_entries or (_norm(entry.get("race")) and _norm(entry.get("race")) in text)
                for entry in idx["cond"][name]):
            yield name, pos


_CLAUSE_BREAK = re.compile(r"[。！？；，、\s]")


def _neg_near(text: str, pos: int, before: int = 8, after: int = 8) -> bool:
    """被点名的那个词**附近**有没有否定词——有就是纠错句，不是断言。

    两头都要看：「未获得原野疾驰」的否定在前面，「急速那条不属于亡灵」的否定在后面。
    窗口遇到标点就截断，免得「急速提高了，这条不属于亡灵」把后半句的"不"借过来用。
    """
    head = text[max(0, pos - before):pos]
    m = _CLAUSE_BREAK.search(head[::-1])
    if m:
        head = head[len(head) - m.start():]      # 只留最近一个标点之后那截
    if _NEG.search(head):
        return True
    tail = text[pos:pos + after]
    cut = len(tail)
    for i, ch in enumerate(tail):
        if _CLAUSE_BREAK.match(ch):
            cut = i
            break
    return bool(_NEG.search(tail[:cut]))


def _neg_span(text: str, pos: int, before: int = 8, after: int = 8) -> Tuple[int, int]:
    """被否定词罩住的区间（起点，终点）：否定词所在小句里、被点名词前后各一截。

    「不是急速3%→2%」里的 3%、2% 是在**引用那个错误说法**，不是本句的断言数——
    不把这段挖掉，纠错句就会因为"数字没登记"被拦（等于罚诚实标注）。
    """
    start = max(0, pos - before)
    m = _CLAUSE_BREAK.search(text[start:pos][::-1])
    if m:
        start = start + (len(text[start:pos]) - m.start())
    end = min(len(text), pos + after)
    for i in range(pos, end):
        if _CLAUSE_BREAK.match(text[i]):
            end = i
            break
    return start, end


def check_fields(fields: List[Tuple[str, str]], reg: Optional[Dict[str, Any]] = None
                 ) -> Dict[str, Any]:
    """逐句核对。`fields` 是 [(位置标签, 文本)]，标签里带「视觉提示」的只查归属、不查登记。

    返回 {"hard": 必须拦的（点名哪句、官方怎么说）, "soft": 要人看一眼的,
          "checked": 判了几句机制断言, "skipped": 为什么整项没判}。
    """
    reg = reg if reg is not None else load()
    if not (reg or {}).get("claims"):
        return {"hard": [], "soft": [], "checked": 0, "entry_problems": [],
                "skipped": "机制断言登记表读不到（config/wow_claims.json），这一项没判"}
    problems = registry_problems(reg)
    idx = _index(reg)
    races = sorted({str(e.get("race") or "") for e in reg["claims"] if e.get("race")},
                   key=len, reverse=True)
    hard: List[str] = []
    soft: List[str] = []
    checked = 0
    matched_all: List[Dict[str, Any]] = []
    for label, text in fields or []:
        visual = "视觉提示" in str(label)
        for sent in _SENT_SPLIT.split(str(text or "")):
            s = _norm(sent)
            if len(s) < 4:
                continue
            in_races = [r for r in races if r and r in s]
            skill_hits = _hits(idx["skill"], s)
            attr_hits = [(a, s.find(a)) for a in _ATTRS if a in s]
            cond_hits = list(_condition_hits(idx, s))
            nums = _numbers_pos(s)
            # 只认**肯定式**点到的技能与属性（「未获得原野疾驰」不当断言，也不当错配）
            pos_skills = [k for k, i in skill_hits if not _neg_near(s, i)]
            pos_attrs = [a for a, i in attr_hits if not _neg_near(s, i)]
            pos_conds = [c for c, i in cond_hits if not _neg_near(s, i)]
            # 被否定词罩住的区间：这些范围里的数是在**引用错误说法**，不是本句断言数
            neg_spans = [_neg_span(s, i) for _k, i in skill_hits if _neg_near(s, i)]
            neg_spans += [_neg_span(s, i) for _a, i in attr_hits if _neg_near(s, i)]
            neg_spans += [_neg_span(s, i) for _c, i in cond_hits if _neg_near(s, i)]
            asserted_nums = [t for t, a, b in nums
                             if not any(a < e and b > st for st, e in neg_spans)]
            matched: List[Dict[str, Any]] = []
            for k in pos_skills:
                for e in idx["skill"][k]:
                    if e not in matched:
                        matched.append(e)
            for c in pos_conds:
                for e in idx["cond"][c]:
                    if e not in matched:
                        matched.append(e)
            if not pos_skills and not pos_conds:
                for r in in_races:
                    for e in idx["race"].get(_norm(r), []):
                        if {_norm(a) for a in (e.get("attrs") or [])} & {_norm(a) for a in pos_attrs}:
                            if e not in matched:
                                matched.append(e)
            for e in matched:
                if e not in matched_all:
                    matched_all.append(e)
            # ① 归属：技能/属性挂到别的种族（视觉提示也查——提示写错，模型就照错的下画）
            owners = sorted({str(e.get("race")) for e in matched})
            if matched and owners and in_races and not (set(in_races) & set(owners)):
                right = sorted({str(e.get("skill") or e.get("skill_en"))
                                for e in idx["race"].get(_norm(in_races[0]), [])})
                hard.append("%s「%s」把技能挂错了主体：%s 登记在「%s」身上，%s 本次官方改的是%s"
                            % (label, sent.strip()[:34],
                               "、".join(pos_skills) or "、".join(pos_attrs),
                               "、".join(owners), "、".join(in_races),
                               "、".join(right) or "没有这一项"))
            for a in pos_attrs:
                own = sorted(idx["attr"].get(_norm(a)) or set())
                if own and in_races and not (set(in_races) & set(own)):
                    hard.append("%s「%s」说的「%s」不属于 %s：登记表里这项在「%s」身上"
                                % (label, sent.strip()[:34], a, "、".join(in_races), "、".join(own)))
            # ② 登记：机制断言要能在表里对上（视觉提示只描述画面，不当公开断言查这条）
            if visual:
                continue
            # 评价性结论**先于**锚点门查：这类句子（「全职业都在亏」「实为削弱」）常常
            # 独立成句、没有种族名也没有数——「兽人1%暴击换宠物5%伤害？算完账发现全职业都在亏」
            # 按句末切开后，后半句什么都不剩，老位置（trigger 门之后）永远轮不到它
            hype = next((match for match in _HYPE.finditer(s)
                         if not (match.group() == "第一" and _ROUTE_ORDINAL.match(s, match.start()))), None)
            if hype and not _neg_near(s, hype.start(), 6, 6):
                hard.append("%s「%s」写了「%s」：这是替读者下的结论，登记表与官方原句里都没有这种说法，"
                            "改成客观列变动（谁、什么技能、从多少到多少）"
                            % (label, sent.strip()[:34], hype.group(0)))
            if not pos_skills and not pos_conds and _QUEST_XP_OBSERVATION.fullmatch(s):
                # A scoped quest-run XP observation is not a racial skill mechanic.
                continue
            trigger = bool(nums or pos_skills or pos_conds or (in_races and _MECH.search(s)))
            # 整句点到的技能/属性/条件**全是否定式**（「矮人没有接手移速」）：这句在纠错，
            # 没有可核对的断言，别把它判成"未登记"——那等于罚诚实标注（同一条坑踩过三次）
            only_correction = (skill_hits or attr_hits or cond_hits) \
                and not pos_skills and not pos_attrs and not pos_conds
            if not (trigger and (in_races or pos_skills or pos_conds)) or only_correction:
                continue
            checked += 1
            if not matched:
                hard.append("%s「%s」是机制断言，但登记表里没有对应条目（哪个种族的哪个技能、"
                            "从多少改到多少、什么条件、哪天的哪份原句）——没登记就是没核，"
                            "要么补登记，要么这句别发" % (label, sent.strip()[:34]))
                continue
            # ── 原子断言：数值只跟**同一小句（或其继承链）**里的锚点条目对账 ──────
            # 不能把整句所有登记值取并集——「牛头人原野疾驰…15%；天裔天穹视界…60%」
            # 两个数各自存在（在**别的**技能名下登记过），整句并集就把交换数值放过去了。
            bounds = [m.start() for m in _SPLIT2.finditer(s)]

            def _clause_of(pos: int) -> int:
                ci = 0
                for b in bounds:
                    if pos >= b:
                        ci += 1
                    else:
                        break
                return ci

            n_cl = len(bounds) + 1
            own_entries: List[List[Dict[str, Any]]] = [[] for _ in range(n_cl)]
            own_anchor = [False] * n_cl
            cl_races: List[List[str]] = [[] for _ in range(n_cl)]
            for k, i in skill_hits:
                if _neg_near(s, i):
                    continue
                ci = _clause_of(i)
                own_anchor[ci] = True
                for e in idx["skill"][k]:
                    if e not in own_entries[ci]:
                        own_entries[ci].append(e)
            for c, i in cond_hits:
                if _neg_near(s, i):
                    continue
                ci = _clause_of(i)
                own_anchor[ci] = True
                for e in idx["cond"][c]:
                    if e not in own_entries[ci]:
                        own_entries[ci].append(e)
            for r in in_races:
                i = s.find(r)
                if i >= 0:
                    cl_races[_clause_of(i)].append(r)
            # 继承链：没有自己锚点的小句沿用上一小句的（「上限60%，骑乘不受益」的 60%
            # 还是上一小句原野疾驰的数）；全句都没有锚点时由小句为空回落到整句 matched。
            eff: List[Optional[List[Dict[str, Any]]]] = []
            for ci in range(n_cl):
                eff.append(own_entries[ci] or (eff[-1] if eff else None))
            # ①b 小句级归属：技能锚点与**同小句**的种族名必须对得上（同句两个主体
            # 各挂各的技能、整句看有交集的老判法放过了交换主体）
            for ci in range(n_cl):
                if not cl_races[ci] or not own_anchor[ci] or not own_entries[ci]:
                    continue
                owners_c = sorted({str(e.get("race")) for e in own_entries[ci]})
                if not (set(cl_races[ci]) & set(owners_c)):
                    right = sorted({str(e.get("skill") or e.get("skill_en"))
                                    for e in idx["race"].get(_norm(cl_races[ci][0]), [])})
                    hard.append("%s「%s」把「%s」挂给了%s：%s 登记在「%s」身上，%s 本次官方改的是%s"
                                % (label, sent.strip()[:34],
                                   "、".join(str(e.get("skill")) for e in own_entries[ci]),
                                   "、".join(cl_races[ci]),
                                   "、".join(str(e.get("skill")) for e in own_entries[ci]),
                                   "、".join(owners_c), "、".join(cl_races[ci]),
                                   "、".join(right) or "没有这一项"))
            # ③ 数值逐小句对账：无出处＝这个数不在**就近锚点**登记的数里
            uncovered: List[str] = []
            for t, a, b in nums:
                if any(a < e and b > st for st, e in neg_spans):
                    continue                      # 否定区间里的是引用错误说法，不是断言数
                tn = _norm(t)
                allowed_c = set()
                for e in (eff[_clause_of(a)] or matched):
                    allowed_c |= {_norm(x) for x in (e.get("numbers") or [])}
                if tn not in allowed_c and not (
                        re.fullmatch(r"\d+(?:\.\d+)?", tn) and (tn + "%") in allowed_c):
                    uncovered.append(t)
            if uncovered:
                cite = "；".join("%s：%s" % (e.get("skill") or e.get("skill_en"), e.get("quote"))
                                 for e in matched[:2])
                if _ASSUME.search(s):
                    soft.append("%s「%s」里的 %s 不在官方数值里，句里已写明是假设算例——"
                                "发布时保留这个标注，别让它读起来像官方口径（对照：%s）"
                                % (label, sent.strip()[:34], "、".join(uncovered), cite[:80]))
                else:
                    hard.append("%s「%s」的 %s 在登记表里找不到出处（官方原句只写到：%s）——"
                                "改数、删句，或明说是假设算例"
                                % (label, sent.strip()[:34], "、".join(uncovered), cite[:110]))
            seller = not str(label).startswith("配文")
            for e in matched:
                need = [str(c) for c in (e.get("condition") or []) if str(c).strip()]
                own = {_norm(x) for x in (e.get("numbers") or [])}
                if e.get("condition_required") and need and own & {_norm(x) for x in asserted_nums} \
                        and not any(c in sent for c in need):
                    msg = ("%s「%s」写了「%s」的效果但没写适用条件（%s）——"
                           "条件缺了就是把人往坑里带"
                           % (label, sent.strip()[:34], e.get("skill") or e.get("skill_en"),
                              "、".join(need)))
                    (hard if seller else soft).append(msg)
    seen, h = set(), []
    for x in hard:
        if x not in seen:
            seen.add(x)
            h.append(x)
    seen, st = set(), []
    for x in soft:
        if x not in seen:
            seen.add(x)
            st.append(x)
    return {"hard": h, "soft": st, "checked": checked, "entry_problems": problems,
            "matched_entries": matched_all,
            "skipped": ("登记表自身不合格：%s" % "；".join(problems[:3])) if problems else ""}


def claim_fields(plan: Dict[str, Any], pub: Dict[str, Any]) -> List[Tuple[str, str]]:
    """把成品里所有会发出去的文字拼成 (位置, 文本)：图上按卡片成组，配文按字段。

    卡片标题与说明**必须并成一句**再判：兽人那条的种族名在标题里、数值在说明里，
    分开扫就变成"说明里出现了没主体的数"，判据会指错地方。
    """
    from . import ai_poster
    out: List[Tuple[str, str]] = []
    pages = ai_poster.plan_pages(plan) or []
    multi = len(plan.get("pages") or []) > 1
    for n, pg in enumerate(pages, 1):
        pre = ("第%d页·" % n) if multi else ""
        for label, txt in ai_poster.ink_texts(pg):
            if str(label).startswith("卡片") and (str(label).endswith("说明")
                                                  or str(label).endswith("标题")):
                continue                      # 卡片单独在下面成对拼
            out.append((pre + label, txt))
        for i, c in enumerate(pg.get("cards") or [], 1):
            one = " ".join([str(c.get("t") or ""), str(c.get("d") or "")]).strip()
            if one:
                out.append((pre + "卡片%d" % i, one))
        if pg.get("scene"):
            out.append((pre + "视觉提示·scene", str(pg.get("scene"))))
        for i, c in enumerate(pg.get("cards") or [], 1):
            if c.get("v"):
                out.append((pre + "卡片%d·视觉提示" % i, str(c.get("v"))))
    if plan.get("scene"):
        out.append(("视觉提示·scene", str(plan.get("scene"))))
    for k in ("copy_text", "digest", "keyword_reply", "xhs_copy"):
        if (pub or {}).get(k):
            out.append(("配文·" + k, str(pub.get(k))))
    for k in ("tt_titles", "xhs_titles"):
        for j, t in enumerate((pub or {}).get(k) or [], 1):
            out.append(("配文·%s%d" % (k, j), str(t)))
    for i, t in enumerate((pub or {}).get("xhs_tags") or [], 1):
        if str(t).strip():
            out.append(("配文·xhs_tags%d" % i, str(t)))
    return out


def fact_status_fields(fields: List[Tuple[str, str]], asr_text: str = "",
                       reg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """逐**断言**给事实状态：拆文案（①）与看版面（②）跟出图（③）共用同一套判据。

    11-02 复验漏口一的修法：字段（整张卡片）先切成可独立判断的断言（按句末切开），
    **已核的部分只能为自身亮绿**——同卡另一句的出处盖不住新增的未核机制结论。
    判据全部复用 `check_fields`（同一份登记表、同一套归属/数值/条件规则），
    这里只做"每条断言落在哪一档"的分类，不另立第二套标准：
      verified    有官方具体来源——数值对上登记条目，或（无数的）说法能在登记原句/条目里找到
      blocked     拦截——check_fields 判出 hard（归属错/数值无出处/缺条件/评价性断言）
      source_only 只有素材说法——锚点与说法在素材原文里找得到，登记表没有对应条目
      pending     待核——素材原文与登记表都没有；其中带强机制谓语的标 mech=True
                  （「免疫所有控制」类未核机制断言，出图前要暂停待发资格，见 mechanism_gaps）
      unjudged    没判——登记表读不到/不合格（适用范围内不能当成已核）
      uncovered   未覆盖——scope 外（由人工逐项核对来源）
      visual      画面提示（只查归属，不查登记）
      general     非机制句——没有可核的断言锚点（普通修辞不拦也不算待核）
    每条断言带 text（原句）与 mech（是否未核机制断言），调用方按需取用。
    """
    reg = reg if reg is not None else load()
    fields = [(str(l), str(t or "")) for l, t in (fields or []) if str(t or "").strip()]
    asr_n = _norm(asr_text)
    if not (reg or {}).get("claims"):
        note = "机制断言登记表读不到（config/wow_claims.json），这一项没判——适用范围内不能当成已核"
        return {"scope": "no_registry", "note": note,
                "fields": [{"label": l, "state": "unjudged", "detail": note} for l, _t in fields]}
    problems = registry_problems(reg)
    if problems:
        note = "登记表自身不合格：%s——适用范围内这一项按缺席判（事实未判）" % "；".join(problems[:2])
        return {"scope": "in", "note": note,
                "fields": [{"label": l, "state": "unjudged", "detail": note} for l, _t in fields]}
    idx = _index(reg)
    races = sorted({str(e.get("race") or "") for e in reg["claims"] if e.get("race")},
                   key=len, reverse=True)
    marks = [_norm(str(m)) for m in ((reg.get("meta") or {}).get("scope_markers") or []) if str(m)]
    joined = _norm(" ".join([asr_text] + [t for _l, t in fields]))
    if marks and not any(m and m in joined for m in marks):
        note = ("登记表只覆盖魔兽无限服的种族机制，这篇未覆盖——"
                "机制类说法由人工逐项核对官方来源，不等于查过没问题")
        return {"scope": "out", "note": note,
                "fields": [{"label": l, "state": "uncovered", "detail": note} for l, _t in fields]}
    meta_url = str(((reg.get("meta") or {}).get("source_url")) or "")

    def _anchors(s: str) -> List[str]:
        """这句里的可核锚点（技能别名/条件词/种族名/属性词/数值）：一个都没有＝非机制句。"""
        out = [k for k, _i in _hits(idx["skill"], s)]
        out += [c for c, _i in _condition_hits(idx, s)]
        if races:
            out += [r for r, _i in _hits({r: 1 for r in races}, s)]
        out += [a for a in _ATTRS if a in s]
        out += [t for t, _a, _b in _numbers_pos(s)]
        return out

    def _entries(s: str) -> List[Dict[str, Any]]:
        """肯定式点到的登记条目（给「有官方具体来源」那条用）。"""
        got: List[Dict[str, Any]] = []
        for k, i in _hits(idx["skill"], s):
            if _neg_near(s, i):
                continue
            for e in idx["skill"][k]:
                if e not in got:
                    got.append(e)
        for c, i in _condition_hits(idx, s):
            if _neg_near(s, i):
                continue
            for e in idx["cond"][c]:
                if e not in got:
                    got.append(e)
        return got

    def _support(es: List[Dict[str, Any]]) -> str:
        """登记条目能背书的内容（原句＋数值＋条件＋属性＋技能名），归一化后供谓语比对。"""
        blob = []
        for e in es:
            blob += [str(e.get(k) or "") for k in ("quote", "skill", "skill_en", "condition")]
            blob += [str(x) for x in (e.get("numbers") or [])]
            blob += [str(x) for x in (e.get("attrs") or [])]
        return _norm(" ".join(blob))

    def _verified_item(label: str, sent: str, s: str, es: List[Dict[str, Any]]) -> Dict[str, Any]:
        src = meta_url or "、".join(str(e.get("source_url") or "") for e in es[:1])
        return {"label": label, "state": "verified", "text": sent,
                "detail": "对上登记条目（%s），官方出处：%s；原句：%s" % (
                    "、".join(str(e.get("skill") or e.get("skill_en")) for e in es[:3]) or "?",
                    src[:90],
                    "；".join(str(e.get("quote") or "")[:60] for e in es[:1]))}

    out: List[Dict[str, Any]] = []
    for label, text in fields:
        sents = [x.strip() for x in _SENT_SPLIT.split(str(text or "")) if len(_norm(x)) >= 4]
        multi = len(sents) > 1
        for j, sent in enumerate(sents, 1):
            alabel = ("%s·句%d" % (label, j)) if multi else label
            s = _norm(sent)
            res = check_fields([(label, sent)], reg)
            hard = res.get("hard") or []
            if hard:
                out.append({"label": alabel, "state": "blocked", "text": sent,
                            "detail": "；".join(hard[:3])})
                continue
            if "视觉提示" in label:
                out.append({"label": alabel, "state": "visual", "text": sent,
                            "detail": "画面描述只查归属，不作公开断言核"})
                continue
            nums = _numbers_pos(s)
            es = _entries(s)
            if res.get("checked") and not es:
                # check_fields 还会经「种族名＋属性」对上条目，这里把那份也并进来当出处
                for e in (res.get("matched_entries") or []):
                    if e not in es:
                        es.append(e)
            if res.get("checked") and nums:
                # 有数的机制断言：check_fields 无 hard ＝ 数值全部对上账——数值就是可核核心
                out.append(_verified_item(alabel, sent, s, es))
                continue
            if res.get("checked"):
                # 无数的机制断言（11-02 漏口一）：主张全在谓语词上，必须能在登记条目
                # 背书内容或素材原文里找到，否则就是未核机制断言——不得借同卡别句亮绿
                mech_words = [m.group(0) for m in _PRED.finditer(s)]
                mech_words += [m.group(0) for m in _MECH.finditer(s)]
                support = _support(es)
                miss_sup = [w for w in mech_words if _norm(w) not in support]
                if es and not miss_sup:
                    out.append(_verified_item(alabel, sent, s, es))
                elif mech_words and all(_norm(w) in asr_n for w in mech_words) and not es:
                    out.append({"label": alabel, "state": "source_only", "text": sent,
                                "detail": "只有素材说法（登记表没有对应条目）"})
                else:
                    miss_asr = [w for w in miss_sup if _norm(w) not in asr_n]
                    pred_miss = sorted({w for w in miss_asr if _PRED.search(w)})
                    item = {"label": alabel, "state": "pending", "text": sent,
                            "detail": "机制断言对不上登记（%s）：说法在登记原句与素材原文里都找不到"
                                      "——待核" % ("、".join(mech_words[:4]) or "无数值可对账")}
                    if pred_miss:
                        item["mech"] = True
                        item["pred"] = pred_miss
                        item["detail"] = ("未核机制断言：「%s」在登记原句与素材原文里都找不到——"
                                          "待核，出图前会暂停这条结论的待发资格"
                                          % "、".join(pred_miss[:3]))
                    out.append(item)
                continue
            anchors = _anchors(s)
            if not anchors:
                out.append({"label": alabel, "state": "general", "text": sent,
                            "detail": ""})          # 非机制句，没有可核的断言
            else:
                miss = [a for a in anchors if _norm(a) not in asr_n]
                if miss:
                    out.append({"label": alabel, "state": "pending", "text": sent,
                                "detail": "素材原文里找不到：%s——待核" % "、".join(miss[:4])})
                else:
                    out.append({"label": alabel, "state": "source_only", "text": sent,
                                "detail": "只有素材说法（登记表没有对应条目）"})
    return {"scope": "in", "note": "", "fields": out}


def mechanism_gaps(fields: List[Tuple[str, str]], asr_text: str = "",
                   reg: Optional[Dict[str, Any]] = None) -> List[str]:
    """出图前那道「未核机制断言」闸（11-02 复验漏口一的后半截）。

    「免疫所有控制」这类不落数值的机制断言，check_fields 没有数可对账、也不会报 hard，
    整字段判就借同卡已核句混过了出图闸。这里把每条断言独立判：强机制谓语在登记条目
    背书内容与素材原文里都找不到的，逐条点名返回——调用方据此暂停待发资格。
    scope 外／登记缺失返回空（那两档有各自的 unjudged/uncovered 旗子，不在这里重复拦）。
    """
    st = fact_status_fields(fields, asr_text=asr_text, reg=reg)
    if st.get("scope") != "in":
        return []
    out: List[str] = []
    for f in st.get("fields") or []:
        if f.get("mech"):
            out.append("%s「%s」的机制说法（%s）在登记原句与素材原文里都找不到——"
                       "这是没核过的机制断言：补登记（带官方原句与日期），或把这句删掉"
                       % (f.get("label"), str(f.get("text") or "")[:34],
                          "、".join(f.get("pred") or []) or "待核"))
    return out


def in_scope(text: str, reg: Optional[Dict[str, Any]] = None) -> bool:
    """这篇稿子归不归这张登记表管（只覆盖魔兽无限服的种族机制）。

    登记表里没有 PoE、没有别的游戏，拿它去卡一篇 PoE 攻略等于凭空造拦——
    所以调用方先问一句：正文里连"魔兽/无限服"这类标记都没有，这一项就该报**不适用**，
    而不是"查过没问题"。
    """
    reg = reg if reg is not None else load()
    marks = [str(m) for m in ((reg.get("meta") or {}).get("scope_markers") or []) if str(m)]
    s = str(text or "")
    return any(m and m in s for m in marks)


def evidence_validator(reg: Optional[Dict[str, Any]] = None):
    """给 `fact_gate.check_override` 用的加严判据：机制断言那条类，出处要能对上登记。"""
    reg = reg if reg is not None else load()
    claims = (reg or {}).get("claims") or []

    def _validator(item: str, source: str, result: str) -> Tuple[bool, List[str]]:
        key = _norm(item)
        hit = [e for e in claims
               if _norm(e.get("skill")) and _norm(e.get("skill")) in key]
        entry = hit[0] if len(hit) == 1 else {}
        return evidence_ok(entry, source, result)

    return _validator


def evidence_ok(entry: Dict[str, Any], source: str, result: str) -> Tuple[bool, List[str]]:
    """人工放行一条机制断言：出处必须是**这条登记自己的来源**，且要写结论与日期。

    通用新闻首页（`.../news/` 这种没有条目 id 的）、只写"官网看过"、引第三方转述，都不算。
    """
    missing: List[str] = []
    s = str(source or "").strip()
    r = str(result or "").strip()
    meta = ((load().get("meta") or {}))
    want = str((entry or {}).get("source_url") or meta.get("source_url") or "").strip()
    if len(r) < 6:
        missing.append("没写核出来的结论（是、不是、还是改成多少）")
    if not re.search(r"https?://", s, re.I):
        missing.append("出处必须是能点开的一手链接（写「官网看过」不算）")
    else:
        path = re.sub(r"^https?://", "", s).strip("/")
        seg = [x for x in path.split("/") if x]
        if len(seg) <= 1 or re.search(r"/news/?$|/news/?\?", path):
            missing.append("出处是通用首页或新闻列表，不构成逐条凭据——要点到具体那一页（带条目 id 或帖号）")
        if want:
            key = _norm(want).split("//")[-1]
            got = _norm(s).split("//")[-1]
            tail = re.sub(r"^\d+", "", key.split("/")[-1]) or key
            if tail[:24] not in got:
                missing.append("出处与登记表里那条的来源对不上（登记表写的是：%s）" % want[:70])
    if not re.search(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}月\d{1,2}日", s + " " + r):
        missing.append("出处或结论里没写日期（哪天核的哪个版本，换一版就没法对）")
    return (not missing), missing
