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
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TERMS_FILE = ROOT / "output" / "_assets" / "wow_terms.json"

_cache = {"sig": None, "terms": {}, "aliases": {}, "by_len": []}


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
    _cache.update(sig=sig, terms=terms, aliases=aliases, by_len=by_len)
    return terms, aliases, by_len


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
    out = {"ready": bool(terms), "stale": [], "hit": [], "missing": [], "levels": {}, "rows": []}
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

    if source_text:
        in_draft = {n for n, _ in hits}
        for n, r in scan(source_text):
            if n not in in_draft:
                out["missing"].append({"name": n, "kind": r.get("kind"), "level": r.get("level"),
                                       "src": r.get("src")})

    if out["stale"]:
        out["rows"].append(("FAIL", "实体实名·用了改过的旧名",
                            "；".join("%s → 现名 %s" % (s["old"], s["new"]) for s in out["stale"])))
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
    if not out["stale"] and not out["missing"] and out["hit"]:
        out["rows"].append(("PASS", "实体实名", "名字与名单一致，素材里的实名也没漏"))
    return out


def apply_names(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """把稿中的旧名统一成现名（只动 aliases 命中，不做别的替换）。返回 (新文本, 改了哪些)。"""
    _, aliases, _ = load()
    changed = []
    for old, new in aliases.items():
        if old and old in (text or ""):
            text = text.replace(old, new)
            changed.append((old, new))
    return text, changed
