# -*- coding: utf-8 -*-
"""语音转写同音字纠正表：给整理稿那一步当"可以放心改"的依据。

为什么要有这个文件：整理稿和海报拆文案的提示词都写着「只基于输入材料，不要编造事实」，
这条纪律挡住了瞎编，也**同时挡住了纠错**——模型没有依据判断"装灯"是"装等"的误写，
改了就算编造。所以实测里 ASR 的同音错字一路原样传到海报上。

有了这张表，"按表改"就不算编造，是有依据的规范化。修在整理稿这一层是因为三条产线
（头条图文、公众号图文、AI 海报）都从整理稿取文，一处生效三处受益；修在海报那层没用，
它拿到的已经是带错的字。

三档可信度要分清，别一把梭：
  observed     真实素材里已经见过的误写对 —— 可以让模型直接改
  terms        标准写法清单 —— 遇到读音相同的写法才改，本身不是替换规则
  needs_review 拿不准的 —— 不参与提示词，只在诊断里提示人去看
"""
import json
import os
from functools import lru_cache
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GLOSSARY_PATH = ROOT / "config" / "term_glossary.json"

_cache = {"mtime": None, "data": None}


def load():
    """读表，按文件修改时间缓存。词表是给人手工编辑的，所以每次改完不用重启服务。"""
    try:
        m = GLOSSARY_PATH.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] == m and _cache["data"] is not None:
        return _cache["data"]
    try:
        data = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    _cache["mtime"], _cache["data"] = m, data
    return data


def observed_pairs():
    out = []
    for g in load().get("groups") or []:
        for it in g.get("observed") or []:
            w, w2 = (it.get("wrong") or "").strip(), (it.get("correct") or "").strip()
            if w and w2:
                out.append((w, w2, g.get("domain") or "", it.get("seen_in") or ""))
    return out


def confirmed_pairs():
    """observed 里**在真实素材里见过**的那些（seen_in 没写"待确认"的）。

    为什么要分这一档：表里有三条是"推断，待确认"（陛下宝→首领宝宝 这种），那是猜的。
    拿猜的映射去静默改转写，等于把一种错字换成另一种，还查不出来。
    这一档只做确定性替换；待确认的仍然只进提示词，由模型带着上下文判断。
    """
    out = []
    for w, c, d, seen in observed_pairs():
        if "待确认" in (seen or ""):
            continue
        out.append((w, c, d))
    return out


def apply_confirmed(text):
    """按已确认的误写对做确定性替换。返回 (新文本, [(错写, 正写, 次数)])。

    长的先替换：不然"上赛径"会被"赛径→赛季"那条切成"上赛季"之外的半截。
    """
    out = text or ""
    changed = []
    for w, c, _d in sorted(confirmed_pairs(), key=lambda x: -len(x[0])):
        n = out.count(w)
        if n and w != c:
            out = out.replace(w, c)
            changed.append((w, c, n))
    return out, changed


WOW_CLASSES = {
    "死亡骑士": "deathknight", "恶魔猎手": "demonhunter", "德鲁伊": "druid",
    "猎人": "hunter", "法师": "mage", "圣骑士": "paladin", "牧师": "priest",
    "潜行者": "rogue", "盗贼": "rogue", "萨满": "shaman", "术士": "warlock", "战士": "warrior",
}
_WOW_SCOPE = re.compile(r"魔兽|无限服|\bWoW(?::?\s*Forever)?\b|暴雪", re.I)
# 仅替换已核过且含明确技能锚点的同音误写。原文中的数字和机制不动。
_WOW_PAIRS = (
    ("风怒图腾和副魔", "风怒图腾和附魔"),
    ("小鬼的火焰剑", "小鬼的火焰箭"),
    ("小鬼前四级的火焰剑", "小鬼前四级的火焰箭"),
    ("小鬼火焰剑", "小鬼火焰箭"),
    ("杜龙塔尔", "杜隆塔尔"),
    ("刺济山", "赤脊山"),
    ("岸叶经领", "暗夜精灵"),
    ("道队黑暗", "到黑海岸"),
    ("西破荒野", "西部荒野"),
    ("平级之地", "贫瘠之地"),
    ("王岭", "亡灵"),
    ("当歌主持人", "当前主城"),
    ("联盟还有部落房", "联盟还有部落方"),
    ("小程区", "小程序"),
    ("把任务接权", "把任务接全"),
)
_WOW_HOTWORDS = {
    "hunter": ("奥术射击", "召唤雄鹰"),
    "shaman": ("风怒武器", "风怒图腾", "风暴打击", "漩涡武器"),
    "warlock": ("小鬼", "火焰箭", "折磨", "持续伤害"),
}


def is_wow(context):
    return bool(_WOW_SCOPE.search(str(context or "")))


def normalize_wow(text, context):
    if not text or (not is_wow(context) and not is_wow(text)):
        return text
    for old, new in _WOW_PAIRS:
        text = text.replace(old, new)
    return text


@lru_cache(maxsize=12)
def _read_verified_terms(path, mtime_ns):
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(row.get("classId"), row.get("cn") or row.get("name"))
            for row in data.get("items", data.get("spells", []))
            if row.get("level") == "L0" and (row.get("cn") or row.get("name"))]


def _wow_terms(context):
    base = Path(os.environ.get("V2T_FOREVER_DATA") or
                ROOT.parent / "wow-forever" / "wow-forever-web" / "app" / "data")
    verified = []
    for name in ("glossary.json", "abilities.json", "spells.json"):
        path = base / name
        try:
            verified.extend(_read_verified_terms(path, path.stat().st_mtime_ns))
        except (OSError, ValueError):
            continue
    classes = {key for title, key in WOW_CLASSES.items() if title in context}
    explicit = [term for _key, term in verified if len(term) >= 2 and term in context]
    owned = [term for key, term in verified if key in classes]
    preferred = [term for key, terms in _WOW_HOTWORDS.items() if key in classes for term in terms]
    return list(dict.fromkeys(["魔兽世界无限"] + explicit + preferred + owned
                              + ["副本", "专精", "天赋", "附魔", "装备等级"]))


def asr_prompt(max_chars=110, context=""):
    """给 Whisper 的 initial_prompt：只塞标准写法，让它朝这些词解码。

    为什么要塞：同音错字是**解码阶段**选错了词，下游再怎么提示模型都只是补救。
    实测这条产线上的高频错字（装等/官宣/上赛季/晋升）全部来自领域专名，
    给一句术语开头，解码时这些词的权重就上来了。
    为什么不塞长：Whisper 的提示词吃的是那 224 token 的上下文，给长了会挤掉音频、
    还会让模型开始复读提示词。所以先放"已经错过的那批正确写法"，再补标准词表。
    """
    if is_wow(context):
        parts = []
        for term in _wow_terms(context):
            if len("、".join(parts + [term])) <= max_chars:
                parts.append(term)
        return "、".join(parts)
    need = []
    for _w, c, _d in confirmed_pairs():
        if c and c not in need:
            need.append(c)
    for g in load().get("groups") or []:
        for t in (g.get("terms") or []):
            if t and t not in need:
                need.append(t)
    parts = []
    for t in need:
        if len("、".join(parts + [t])) > max_chars:
            break
        parts.append(t)
    return "、".join(parts)


def render_prompt(max_chars=1400):
    """拼成塞进整理稿提示词的一段。超预算就只保留已确认的误写对，标准写法清单往后放。"""
    data = load()
    groups = data.get("groups") or []
    if not groups:
        return ""
    lines = ["【术语标准写法 · 用于纠正语音转写的同音错字】",
             "下面的词是本领域标准写法。转写里出现读音相同或极相近、明显读不通的写法时，"
             "按标准写法改；除此之外一个字都不许动，"
             "**数值、时间、日期、价格一律保持原样，不许猜、不许补齐**。"]
    for g in groups:
        terms = "、".join(g.get("terms") or [])
        if terms:
            lines.append("%s标准写法：%s" % (g.get("domain") or "", terms))
    pairs = ["%s→%s" % (w, c) for w, c, _d in confirmed_pairs()]
    if pairs:
        lines.append("已确认的误写对照（照这个改）：" + "；".join(pairs))
    text = "\n".join(lines)
    if len(text) > max_chars:
        keep = ["【术语标准写法 · 用于纠正语音转写的同音错字】",
                "转写里出现读音相同或极相近、明显读不通的写法时按下列对照改；"
                "除此之外一个字都不许动，数值、时间、日期、价格一律保持原样。"]
        if pairs:
            keep.append("已确认的误写对照：" + "；".join(pairs))
        text = "\n".join(keep)
    return text


def scan(text):
    """诊断用：这段转写里命中了多少处已知误写、多少处待确认片段。

    只报数不替换——真正改字的是整理稿那一步的模型，这里的作用是让人在选素材时
    就知道"这条底子脏"，决定要不要用。
    """
    t = str(text or "")
    hits = [{"wrong": w, "correct": c, "domain": d} for w, c, d, _s in observed_pairs() if w in t]
    review = [it for it in (load().get("needs_review") or []) if (it.get("raw") or "") in t]
    return {"hits": hits, "needs_review": review,
            "hit_count": len(hits), "review_count": len(review)}


def stats():
    data = load()
    groups = data.get("groups") or []
    return {
        "terms": sum(len(g.get("terms") or []) for g in groups),
        "observed": len(observed_pairs()),
        "needs_review": len(data.get("needs_review") or []),
        "domains": [g.get("domain") for g in groups],
        "path": str(GLOSSARY_PATH),
    }
