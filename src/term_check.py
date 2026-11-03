# -*- coding: utf-8 -*-
"""实体实名核对：稿子/卡片里的魔兽名字，按 wow-forever 的名单对一遍。

名单来自 `tools/sync_wow_icons.py`，落在 `output/_assets/wow_terms.json`（3070 条，
每条带类别 / 分级 / 归属 / 图标）。口径是 2026-10-10 用户拍板的那条：
**库名只用来统一写法和补图标，不用来引入新实体**——所以这里只做三件事，不做改名强制：

1. `stale`：稿里出现了上游标了「改名」的旧名（如「强化雄鹰守护」→ 现名「致命守护」）——
   这一条是真错误，数量少且确定，可以当硬门。
2. `hit`：稿里命中的库内实名，带分级。L0 是官方用词/客户端实名，L2 是两个挖掘源对齐、
   名字没跟官方核对过——**分级只记在报告里，绝不上图**（图上出现「L2」是编辑向泄漏）。
3. `missing`：素材原文里命中、稿子里却没有的实名——这就是用户说的「漏关键信息」里
   名字那一类，能机器查出来的部分。

不做的事：不做模糊/形近匹配。中文实体名做编辑距离误报会很多（"帐篷"能命中一串），
误报的机检没人信，宁可漏报。库里没有的名字（混调干果、鱼缸这类）也不判——
上游自己就没拆这些数据，不能拿"库里没有"当"我们写错了"。

**一个例外（11-02 号主拍的）**：种族与职业这两类，官网是**一次列全的完整枚举**
（`wow_terms.json` 里 `official_enum=true` 那 18 条，带公告页 URL），所以"枚举里没有"
在這一类上就是硬证据。当天那张图文攻略卡片写的「德莱尼主动技能…」正是这么露馅的：
口播 0 次、补充资料 0 次、官网种族名单 0 次——名字是模型安的，评论第一句就是"哪来的德莱尼"。
判据：成品里出现的种族/职业名，必须能在**官方枚举**或**本篇素材原文**里找到，两处都没有才拦。
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TERMS_FILE = ROOT / "output" / "_assets" / "wow_terms.json"
NAMES_FILE = ROOT / "config" / "wow_names.json"

_cache = {"sig": None, "terms": {}, "aliases": {}, "by_len": [], "blobs": []}
_names_cache = {"sig": None, "races": [], "classes": []}


def _detector():
    """种族/职业检测表（config/wow_names.json）。读不到就交空表 → 这道闸自动不判。

    检测表只负责"认出这是个种族/职业名"，**不负责它在这个服存不存在**——后者看官方枚举。
    """
    try:
        sig = NAMES_FILE.stat().st_mtime
    except OSError:
        return [], []
    if _names_cache["sig"] == sig:
        return _names_cache["races"], _names_cache["classes"]
    try:
        doc = json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return [], []
    _names_cache.update(sig=sig, races=list(doc.get("races") or []),
                        classes=list(doc.get("classes") or []))
    return _names_cache["races"], _names_cache["classes"]


def enum_names(terms=None):
    """官方完整枚举的种族/职业名（唯一有资格当硬判据的那一小撮）。"""
    t = terms if terms is not None else load()[0]
    return {n for n, r in (t or {}).items() if r.get("official_enum")}


def roster_hint(source_text: str = "") -> str:
    """给拆解提示词的那句"这些名字才能用"：官方枚举 + 素材里出现过的种族/职业名。

    为什么事前要给，不能只靠事后拦：模型编一个种族名时它自己不知道在编（素材里
    一次没出现，它就顺着"人类/兽人/牛头人"的句式补一个），事后拦等于白跑一次模型钱。
    名单读不到时返回空串——提示词里那句会退成"只用素材里出现过的写法"，不假装有成文名单。
    """
    races, classes = _detector()
    enum = enum_names()
    ok = set(enum) | {n for n in list(races) + list(classes) if n in (source_text or "")}
    return "、".join(sorted(ok))


def roster_check(draft_text: str, source_text: Optional[str] = None,
                 terms=None) -> Dict[str, object]:
    """成品里的种族/职业名，逐个问一遍：官方枚举或本篇素材里有吗。

    三种"没判"必须说出来而不是当成通过：检测表读不到、名单里没有官方枚举条目（没同步）、
    没交素材原文。第三种尤其重要——只拿官方枚举比，素材里说的新名字会被误杀。
    """
    races, classes = _detector()
    out = {"suspect": [], "ok": [], "skipped": ""}
    if not (races or classes):
        out["skipped"] = "检测表读不到（config/wow_names.json），这一项没判"
        return out
    enum = enum_names(terms)
    if not enum:
        out["skipped"] = ("名单里没有官方枚举条目（跑一次 tools/sync_wow_icons.py --terms-only），"
                          "这一项没判")
        return out
    if not source_text:
        out["skipped"] = "没交素材原文，这一项没判（只比官方枚举会把素材里的名字误杀）"
        return out
    # 先把"允许的名字"整段挖掉再扫，否则「萨满祭司」会被短名「萨满」切碎误报（长名优先）
    allowed = set(enum) | {n for n in list(races) + list(classes) if n in (source_text or "")}
    rest = draft_text or ""
    for name in sorted(allowed, key=len, reverse=True):
        if name:
            rest = rest.replace(name, "◎")
    for name in sorted(set(races) | set(classes), key=len, reverse=True):
        if name and name in rest:
            out["suspect"].append({"name": name,
                                   "kind": "种族" if name in races else "职业"})
    out["ok"] = sorted(n for n in set(races) | set(classes) if n in enum and n in (draft_text or ""))
    return out



def load():
    if not TERMS_FILE.exists():
        return {}, {}, []
    sig = TERMS_FILE.stat().st_mtime
    if _cache["sig"] == sig:
        return _cache["terms"], _cache["aliases"], _cache["by_len"]
    doc = json.loads(TERMS_FILE.read_text(encoding="utf-8"))
    terms = doc.get("terms") or {}
    aliases = doc.get("aliases") or {}
    # 最长优先：「营地帐篷」要盖过「帐篷」，否则一条名字会被短名切碎重复计
    by_len = sorted([n for n in terms if len(n) >= 2], key=len, reverse=True)
    _cache.update(sig=sig, terms=terms, aliases=aliases, by_len=by_len,
                  blobs=list(doc.get("blobs") or []))
    return terms, aliases, by_len


def official_blobs() -> List[str]:
    """资料库里**官网原文那一段段话**（种族介绍整段、技能名出处的原句）。

    事实闸判的是"这句官方里到底有没有"（`fact_gate.mechanism_gate` 的 `official_blobs`），
    只有词表的名单答不了整句——所以同步脚本把 provenance 里的原句一并带回来。
    11-02 那天补充资料里其实写着「天穹视界：移动速度提高15%（原为10%），持续15秒」，
    问题是这份一手文本没进对照面，不是官方没说。
    """
    load()
    return list(_cache.get("blobs") or [])


def scan(text: str) -> List[Tuple[str, dict]]:
    """按名单正向扫，返回去重后的 [(名字, 记录)]，按出现先后排。"""
    terms, _, by_len = load()
    if not text or not by_len:
        return []
    found = {}
    for name in by_len:
        pos = text.find(name)
        while pos >= 0:
            found.setdefault(name, pos)
            pos = text.find(name, pos + len(name))
    return [(n, terms[n]) for n, _ in sorted(found.items(), key=lambda kv: kv[1]) if n in terms]


def check(draft_text: str, source_text: Optional[str] = None) -> Dict[str, object]:
    """出一份核对报告。rows 是给体检打印用的 (级别, 名称, 说明) 三元组。"""
    terms, aliases, _ = load()
    out = {"ready": bool(terms), "stale": [], "alias": [], "hit": [], "missing": [],
           "roster": {}, "levels": {}, "rows": []}
    if not terms:
        out["rows"].append(("INFO", "实体实名", "名单没同步（先跑 tools/sync_wow_icons.py），本项跳过"))
        return out

    hits = scan(draft_text or "")
    out["hit"] = [{"name": n, "kind": r.get("kind"), "level": r.get("level"), "src": r.get("src")}
                  for n, r in hits]
    for _, r in hits:
        lv = r.get("level") or "?"
        out["levels"][lv] = out["levels"].get(lv, 0) + 1

    for old, new in aliases.items():
        if old and old in (draft_text or ""):
            out["stale"].append({"old": old, "new": new})
    # 手工别名表：名单里有实名、稿里却用描述性写法（制皮帐篷→营地帐篷）。
    # 和"上游改名"同一档处理——都是**确定的**错，不是猜出来的，所以判 FAIL。
    for wrong, right, _note in _alias_pairs():
        n = (draft_text or "").count(wrong)
        if n and wrong != right:
            out["alias"].append({"wrong": wrong, "right": right, "count": n})

    if source_text:
        in_draft = {n for n, _ in hits}
        for n, r in scan(source_text):
            if n not in in_draft:
                out["missing"].append({"name": n, "kind": r.get("kind"), "level": r.get("level"),
                                       "src": r.get("src")})

    if out["stale"]:
        out["rows"].append(("FAIL", "实体实名·用了改过的旧名",
                            "；".join("%s → 现名 %s" % (s["old"], s["new"]) for s in out["stale"])))
    if out["alias"]:
        out["rows"].append(("FAIL", "实体实名·名单里有实名",
                            "；".join("%s → %s（%d 处）" % (a["wrong"], a["right"], a["count"])
                                  for a in out["alias"])))
    ros = roster_check(draft_text, source_text, terms=terms)
    out["roster"] = ros
    if ros["suspect"]:
        out["rows"].append((
            "FAIL", "实体实名·素材与官方名单都没有的种族/职业名",
            "%s 在本篇素材里一次都没出现，官网种族/职业枚举里也没有——这是模型安的名字，"
            "要么删掉、要么换成素材/官方名单里真有的那个（官方枚举里现有：%s）"
            % ("、".join("%s「%s」" % (s["kind"], s["name"]) for s in ros["suspect"]),
               "、".join(sorted(enum_names(terms))[:12]))))
    elif ros["ok"]:
        out["rows"].append(("PASS", "实体实名·种族与职业",
                            "用的都在官方枚举或素材里：%s" % "、".join(ros["ok"][:10])))
    elif ros["skipped"]:
        out["rows"].append(("INFO", "实体实名·种族与职业", ros["skipped"]))

    if out["hit"]:
        lv = "、".join("%s %d 条" % (k, v) for k, v in sorted(out["levels"].items()))
        out["rows"].append(("INFO", "实体实名", "命中名单 %d 条（%s）：%s"
                            % (len(out["hit"]), lv,
                               "、".join(h["name"] for h in out["hit"][:12]))))
    if out["missing"]:
        # 只给 INFO 不给 WARN：素材是「一个选题包五条来源」时，它覆盖的主题远宽于本篇
        # （实测 69 号那份素材带整张 12 专业营地设施表，本篇只讲四条经验线，
        # 按 WARN 报会一次甩 25 条噪音）。误报的机检没人信，宁可当线索不当错误。
        out["rows"].append(("INFO", "实体实名·素材有稿里没写",
                            "%d 个（素材覆盖多主题时这条会偏多，只当线索）：%s"
                            % (len(out["missing"]),
                               "、".join(m["name"] for m in out["missing"][:12]))))
    if (not out["stale"] and not out["alias"] and not out["missing"] and out["hit"]
            and not ros["suspect"]):
        out["rows"].append(("PASS", "实体实名", "名字与名单一致，素材里的实名也没漏"))
    return out


def _alias_pairs():
    """手工写法对照表（config/wow_aliases.json）。取不到就只跑名单，不影响其余检查。"""
    try:
        from . import wow_reference
        return wow_reference.alias_pairs()[0]
    except Exception:
        return []


def apply_names(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """把稿里的旧名与"名单已有实名的描述性写法"统一成实名。返回 (新文本, 改了哪些)。

    只动两张表里显式登记的条目，不做任何模糊匹配。
    """
    _, aliases, _ = load()
    changed = []
    for old, new in aliases.items():
        if old and old in (text or ""):
            text = text.replace(old, new)
            changed.append((old, new))
    for wrong, right, _note in _alias_pairs():
        if wrong and wrong != right and wrong in (text or ""):
            text = text.replace(wrong, right)
            changed.append((wrong, right))
    return text, changed
