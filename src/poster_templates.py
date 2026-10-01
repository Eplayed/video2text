# -*- coding: utf-8 -*-
"""用户自己的海报模板库：把一张满意的成品收编成可复用的风格。

为什么要这一层：内置四套风格写死在代码里，加一套就得改代码重启。但真正会被反复用的
模板，多半是自己出过的那几张满意图，而不是别人的参考图——别人的图只能学到七八成，
自己的图是百分百就是。所以模板落成数据存在 config/poster_templates.json（不进 git）。

一条模板有两种价值：
  带底图 —— 把那张已经验收的底图一起收进来。之后换文案、改标题、增减卡片全部复用这张
            底图，出图成本直接归零，而且同一个栏目长出一个样子，这正是系列号要的。
  纯参数 —— 只记画风口 + 配色 + 版式 + 插画占比 + 那次赢了的主视觉方向。出图时现画。

依赖方向：本模块 import poster_typeset 取内置皮肤；poster_typeset 与 ai_poster 反过来
只在函数体里延迟 import 本模块，所以模块加载期没有环。
"""
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE_PATH = ROOT / "config" / "poster_templates.json"
BASE_DIR = ROOT / "config" / "poster_templates"

MAX_TEMPLATES = 40


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read():
    if not STORE_PATH.exists():
        return []
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []      # 手工改坏过 JSON 也不能让整条海报线挂掉，回退成"没有用户模板"
    return data if isinstance(data, list) else []


def _write(items):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def all_templates():
    return _read()


def get(key):
    for it in _read():
        if it.get("key") == key:
            return it
    return None


def preset(key):
    """给 ai_poster 查的画风条目：字段名与 STYLE_PRESETS 对齐。"""
    it = get(key)
    if not it:
        return None
    return {"label": it.get("name") or it["key"], "when": "我的模板 · 来自整合稿 %s" % it.get("source_summary_id"),
            "mood": it.get("mood") or "", "scene": it.get("scene_hint") or "",
            "card_visual": it.get("card_visual") or "", "style": it.get("mood") or "",
            "is_template": True}


def skin(key):
    """给 poster_typeset 查的皮肤：存的是完整一份，字段与 TYPE_SKINS 对齐。"""
    it = get(key)
    return it.get("skin") if it and isinstance(it.get("skin"), dict) else None


def base_image_path(key):
    it = get(key)
    if not it or not it.get("base_image"):
        return None
    p = BASE_DIR / it["base_image"]
    return p if p.exists() else None


def save_from_package(manifest, name="", reuse_base=True):
    """把一条已经满意的海报收编成模板。返回新模板条目。

    只从 manifest 里取"可复用的那部分"：父风格、画风口、配色皮肤、版式、插画占比，
    以及那次赢在哪儿的主视觉描述。文案本身不收——那是每条图文各自要重拆的。
    """
    from . import poster_typeset as ts
    plan = manifest.get("plan") or {}
    parent = plan.get("style_key") or ts.DEFAULT_SKIN
    if parent not in ts.TYPE_SKINS:
        parent = ts.DEFAULT_SKIN
    sid = int(manifest["id"])
    key = "t%d_%s" % (sid, uuid.uuid4().hex[:6])
    entry = {
        "key": key,
        "name": (name or manifest.get("title") or "我的模板").strip()[:24],
        "parent": parent,
        "source_summary_id": sid,
        "mood": _mood_of(parent),
        "scene_hint": (plan.get("scene") or "").strip()[:120],
        "card_visual": _card_visual_of(parent),
        "skin": dict(ts.TYPE_SKINS[parent]),
        "ratio": manifest.get("ratio") or "9:16",
        "base_image": None,
        "created_at": _now(),
    }
    if reuse_base:
        src = poster_dir(sid) / ("base.png" if (poster_dir(sid) / "base.png").exists() else "")
        if src and src.name:
            BASE_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, BASE_DIR / (key + ".png"))
            entry["base_image"] = key + ".png"
    items = _read()
    items.insert(0, entry)
    _write(items[:MAX_TEMPLATES])
    return entry


def save_from_draft(raw, ref_url=""):
    """把一份拆解草稿（人在表单上改过的原始值）存成模板。

    派生规则只有一份：poster_breakdown.normalize()。前端只传表单原值，不在那边重算
    配色和占比，否则深浅翻转那套逻辑迟早两边对不上。
    草稿里没有底图：外部参考图不能当底图用（那是别人的画），所以这类模板出图时
    一律按画风口现画一张，0.5 元/张。参考图只留个地址给人对着看。
    """
    from . import poster_breakdown
    draft = poster_breakdown.normalize(raw)
    key = "d_%s" % uuid.uuid4().hex[:8]
    skin = dict(draft["skin"])
    entry = {
        "key": key,
        "name": (draft.get("name") or "拆出来的模板").strip()[:24],
        "parent": draft.get("parent"),
        "source_summary_id": None,
        "mood": (draft.get("mood") or "").strip()[:200],
        "scene_hint": (draft.get("mood") or "").strip()[:120],
        "card_visual": "",
        "skin": skin,
        "ratio": "9:16",
        "base_image": None,
        "ref_image": ref_url or "",
        "confidence": draft.get("confidence") or "mid",
        "created_at": _now(),
    }
    items = _read()
    items.insert(0, entry)
    _write(items[:MAX_TEMPLATES])
    return entry


def _layout_keys():
    from . import poster_typeset as ts
    return {"stack", "grid2", "list", "hero"}


_LAYOUTS = {"stack", "grid2", "list", "hero"}


def poster_dir(summary_id):
    return ROOT / "output" / "poster" / str(int(summary_id))


def _mood_of(style_key):
    from . import ai_poster
    return (ai_poster.STYLE_PRESETS.get(style_key) or {}).get("mood") or ""


def _card_visual_of(style_key):
    from . import ai_poster
    return (ai_poster.STYLE_PRESETS.get(style_key) or {}).get("card_visual") or ""


def rename(key, name):
    items = _read()
    for it in items:
        if it.get("key") == key:
            it["name"] = (name or "").strip()[:24] or it["name"]
            _write(items)
            return it
    return None


def delete(key):
    """删模板条目，顺带删它收进来的底图——留着就是没人认领的磁盘垃圾。"""
    items = _read()
    kept = [it for it in items if it.get("key") != key]
    if len(kept) == len(items):
        return False
    gone = [it for it in items if it.get("key") == key][0]
    _write(kept)
    if gone.get("base_image"):
        try:
            (BASE_DIR / gone["base_image"]).unlink()
        except OSError:
            pass
    return True
