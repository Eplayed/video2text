# -*- coding: utf-8 -*-
"""游戏图标解析：条目上写的名字 → 本地真图标（file:// 就绪的 icon_uri）。

数据从哪来：`tools/sync_wow_icons.py` 从 wow-forever 单向同步过来（图标字节来自暴雪官方 CDN，
那边 fetch-icons.py 限死了域名）。落点在 `output/_assets/` 下——video2text 是 GitHub 公开仓库，
暴雪图标一张都不许进 git，所以映射表和图都待在 gitignore 覆盖的地方。

三条硬规矩（都是踩过才知道要写的）：
1. **只认条目上显式写的 `icon`，不从 `name` 猜**。同一个字在天赋里是另一回事：
   实测按裸名查「复仇」会命中德鲁伊那条，配错图印出去就是错攻略，比空着严重。
2. **查不到就不画图**，绝不拿相近的图凑数——和 wow-forever 那份 `icon-gaps.json` 同一套纪律。
   缺了什么名字，`attach()` 的返回里逐条列出来，不许静默。
3. **键带职业前缀优先**：卡片给 `class`（如「圣骑士」）就先查 `圣骑士|定罪`；
   没给职业时只接受全库唯一的名字，重名一律不画。
"""
import json
import os
from pathlib import Path

# 本文件在 src/graphics/ 下，比 src/*.py 深一层，parents[2] 才是仓库根（写成 parent.parent
# 会指到 src/ 去，映射表判空、图标全查不到——路径按目录层级算，这类坑本项目踩过不止一次）。
ROOT = Path(__file__).resolve().parents[2]
MAP_FILE = ROOT / "output" / "_assets" / "icons_cn.json"
ICON_DIR = ROOT / "output" / "_assets" / "icons"

_cache = {"sig": None, "exact": {}, "uniq": {}}


def _load():
    """读映射，带 mtime 缓存；文件不在就返回空表——渲染照常出，只是全部无图（会报缺）。"""
    if not MAP_FILE.exists():
        return {}, {}
    sig = MAP_FILE.stat().st_mtime
    if _cache["sig"] == sig:
        return _cache["exact"], _cache["uniq"]
    doc = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    exact = {k: v for k, v in (doc.get("map") or {}).items() if (ICON_DIR / v).exists()}
    by_name = {}
    for k, v in exact.items():
        name = k.split("|", 1)[-1]
        by_name.setdefault(name, set()).add(v)
    uniq = {n: next(iter(s)) for n, s in by_name.items() if len(s) == 1}
    _cache.update(sig=sig, exact=exact, uniq=uniq)
    return exact, uniq


def attach(cards):
    """给卡片条目补 icon_uri，返回 {"attached": n, "missing": [名字…], "map_ready": bool}。

    条目字段：`icon` 写游戏内真名（天赋/技能/道具），可选 `icon_class` 或卡级 `class` 指定职业。
    封面 hooks 也支持，方便「四条线」那种摘要行配图。
    """
    exact, uniq = _load()
    rep = {"attached": 0, "missing": [], "map_ready": bool(exact)}
    if not isinstance(cards, list):
        return rep
    for card in cards:
        if not isinstance(card, dict):
            continue
        cls = str(card.get("class") or "").strip()
        groups = [card.get("items") or [], card.get("hooks") or []]
        for group in groups:
            for it in group:
                if not isinstance(it, dict):
                    continue
                want = str(it.get("icon") or "").strip()
                if not want:
                    continue
                it.pop("icon_uri", None)
                use_cls = str(it.get("icon_class") or cls).strip()
                f = exact.get("%s|%s" % (use_cls, want)) if use_cls else None
                if not f:
                    f = uniq.get(want)
                if f and (ICON_DIR / f).exists():
                    it["icon_uri"] = (ICON_DIR / f).as_uri()
                    rep["attached"] += 1
                else:
                    rep["missing"].append(want)
    return rep
