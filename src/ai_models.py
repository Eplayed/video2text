# -*- coding: utf-8 -*-
"""ai_models.py — 主档与快速档各一条候选池，按**到期时间长短**自动挑、用尽或到期就换下一个。

为什么要有这一层（2026-10-29）：那把 key 下面挂着一串 1M token 的免费额度，**按模型分开算、
各自有到期日**（号主控制台截图：11-17 到 12-17 都有）。上一轮 `deepseek-v4-pro-0813` 用尽后，
是人工来喊"换模型"的——到期日明摆着写在控制台上，不该每次都靠人盯。他这次把
「免费额度用尽即停」全开了，所以撞墙只会得到一个 403、不会扣钱，正好让产线自己跳到下一个候选。

三条口径，别再改：
  · 排序按**到期日升序**——先到期的先用完。把 11 月就过期的额度留到 12 月是纯浪费。
  · 判"不可用"只认两条看得见的证据：日期已过、或本地账上被 403 盖过章（见 `ai_quota.mark_exhausted`）。
    额度百分比只用来提醒不用来判死——本地账和平台账对不上是常态（`ai_quota` 顶上那段注释）。
  · 配置里只写 `AI_MODEL`/`AI_MODEL_FAST`、没写池子 → 完全走老路，行为零变化。

配置长这样（`config/config.env.local`，逗号分隔，`模型名|YYYY-MM-DD`，日期可省）：
    AI_MODEL_POOL=qwen3.8-max-0902|2026-12-01,qwen3.8-omni-flash|2026-12-17
    AI_MODEL_POOL_FAST=                       # 不填就跟主档共用一条池
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, "config", "config.env.local")

POOL_KEYS = {"main": "AI_MODEL_POOL", "fast": "AI_MODEL_POOL_FAST"}
MANUAL_KEYS = {"main": "AI_MODEL", "fast": "AI_MODEL_FAST"}


def _read_env() -> Dict[str, str]:
    """现读配置，不缓存——号主改一行模型名不该要重启工作台。"""
    out: Dict[str, str] = {}
    try:
        with open(ENV_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return out


def _parse_expiry(raw: str) -> str:
    """只认 YYYY-MM-DD；写错就当没有到期日（排最后），不猜。"""
    s = (raw or "").strip()
    try:
        time.strptime(s, "%Y-%m-%d")
    except ValueError:
        return ""
    return s


def parse_pool(raw: str) -> List[Dict[str, Any]]:
    """`a|2026-12-01, b` -> [{"model": "a", "expiry": "2026-12-01", ...}, ...]，按到期升序。

    没写日期的排最后（它没有"快过期"这回事）；同一天到期保持配置里的先后顺序。
    """
    items: List[Dict[str, Any]] = []
    for n, chunk in enumerate((raw or "").split(",")):
        part = chunk.strip()
        if not part:
            continue
        model, _, exp = part.partition("|")
        items.append({"model": model.strip(), "expiry": _parse_expiry(exp),
                      "ord": n})
    items.sort(key=lambda x: (x["expiry"] or "9999-99-99", x["ord"]))
    return items


def _exhausted(model: str) -> bool:
    """本地账盖过章才算死（403 insufficient_quota 时盖的），百分比高不算。"""
    try:
        from . import ai_quota
        for r in ai_quota.status():
            if r.get("model") == model:
                return bool(r.get("exhausted"))
    except Exception:  # noqa: BLE001
        pass
    return False


def _days_left(expiry: str) -> Optional[int]:
    if not expiry:
        return None
    try:
        end = time.strptime(expiry, "%Y-%m-%d")
    except ValueError:
        return None
    today = time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")
    return int((time.mktime(end) - time.mktime(today)) // 86400)


def annotate(entry: Dict[str, Any]) -> Dict[str, Any]:
    """给界面用：每条候选补上剩几天、是不是过期、是不是被盖章用尽。"""
    days = _days_left(entry.get("expiry") or "")
    state = "ok"
    if days is not None and days < 0:
        state = "expired"
    elif _exhausted(entry["model"]):
        state = "exhausted"
    return dict(entry, days_left=days, state=state)


def pool(tier: str, env: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """这一档的候选池（已按到期升序、已标注状态）。快速档没单配池子就共用主档那条。"""
    env = env if env is not None else _read_env()
    raw = (env.get(POOL_KEYS[tier]) or "").strip()
    if tier == "fast" and not raw:
        raw = (env.get(POOL_KEYS["main"]) or "").strip()
    return [annotate(e) for e in parse_pool(raw)]


def usable(tier: str, skip=(), env: Optional[Dict[str, str]] = None) -> List[str]:
    skip = set(skip or ())
    return [e["model"] for e in pool(tier, env)
            if e["state"] == "ok" and e["model"] not in skip]


def resolve(tier: str, env: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """这一档现在该用哪个模型 + 一句话说明怎么选的（给设置页原样印出来）。

    池子整条不可用时**不退回老模型**：退回去了界面就得说假话，
    而"没有可用模型"这件事本来就该让人当场看见。
    """
    env = env if env is not None else _read_env()
    manual = (env.get(MANUAL_KEYS[tier]) or "").strip()
    rows = pool(tier, env)
    if not rows:
        return manual, "手动指定（没配候选池）" if manual else "没配模型"
    ok = [e for e in rows if e["state"] == "ok"]
    if not ok:
        why = "、".join("%s %s" % (e["model"], "已过期" if e["state"] == "expired" else "额度已用尽")
                        for e in rows)
        return "", "整条池子都不可用：%s" % why
    first = ok[0]
    days = first.get("days_left")
    tail = ("剩 %d 天到期" % days) if days is not None else "未填到期日"
    extra = ""
    if len(ok) > 1:
        extra = "，用尽或到期后自动换 %s" % ok[1]["model"]
    return first["model"], "按到期日自动选（%s，%s）%s" % (first["model"], tail, extra)


def tier_of(model: str, env: Optional[Dict[str, str]] = None) -> str:
    """这个模型名属于哪一档——换档重试时得知道该在同一条池子里找下一个。"""
    env = env if env is not None else _read_env()
    for tier in ("main", "fast"):
        if any(e["model"] == model for e in pool(tier, env)):
            return tier
    return ""


def next_after(tier: str, model: str, skip=(), env: Optional[Dict[str, str]] = None) -> str:
    """池子里排在 `model` 后面、还能用的第一个；没有就返回空串。"""
    env = env if env is not None else _read_env()
    rows = pool(tier, env)
    seen = set(skip or ())
    started = model not in [r["model"] for r in rows]
    for e in rows:
        if not started:
            if e["model"] == model:
                started = True
            continue
        if e["state"] == "ok" and e["model"] not in seen:
            return e["model"]
    return ""
