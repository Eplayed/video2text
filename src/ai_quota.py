# -*- coding: utf-8 -*-
"""ai_quota.py — 记每个模型用掉多少免费额度，快没了要提前说，不是等 403 才知道。

为什么要这一层：2026-10-14 号主在千问控制台看到 qwen3.8-max 还剩 23.84%，界面却报
"Free quota exhausted"。两边都没说谎——**额度是按模型算的**，拆解走的是「快速模型」档
（AI_MODEL_FAST），那一档的额度早就空了。控制台只有一个网页能看，产线里没人盯着，
所以用完之前必须自己记账、自己提醒。

口径说清楚（别把它当准数）：
  · 记的是**我们自己发出去的请求**回包里 usage 的 token 数；控制台那个"已消耗"是平台侧总数，
    包含别的会话/别的机器花掉的。所以这里默认拿控制台的数当**种子**（seed），之后只加自己看到的量。
  · 上限按模型存，默认 100 万 token（千问这一批免费额度都是 1M）；不在表里的模型 limit=None，
    只记用量不报警——**没有依据就不猜**。
  · 阈值：用到 80% 提示（跟控制台"已消耗 80% 以上"同一档），95% 标红。

为什么用 install() 包一层 SDK，而不是在 12 个调用点各加一行：
  那 12 处散在 ai_poster / content_store / frame_pool / poster_breakdown / main / web.app 六个文件里，
  而这几个文件常年有多个 agent 同时在改，改一处就多一次互相覆盖的机会。
  包在 SDK 边界上只需要一次，且**包不上就明确报错**，不会假装记到了。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "output", "ai_quota.json")
DEFAULT_LIMIT = 1_000_000          # 千问这一批免费额度都是 1M tokens
# 阈值定在 75% 而不是控制台那个 80%：号主 2026-10-14 说这句话的时候，qwen3.8-max 是
# 剩 23.84%（＝已用 76.16%），按 80% 那条线今天就不会报——他要的是"快没了提前说"，
# 所以"剩不到四分之一"就该出声；92% 再升一级标红。
WARN_AT = 0.75
DANGER_AT = 0.92
_LOCK = threading.Lock()


def _load() -> Dict[str, Any]:
    if not os.path.isfile(PATH):
        return {"models": {}, "note": "本地记账：种子来自控制台，之后只加本仓库自己发出的请求"}
    try:
        with open(PATH, encoding="utf-8") as fh:
            d = json.load(fh)
    except ValueError:
        d = {"models": {}}
    d.setdefault("models", {})
    return d


def _save(d: Dict[str, Any]) -> None:
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


def _entry(d: Dict[str, Any], model: str) -> Dict[str, Any]:
    return d["models"].setdefault(model, {"used": 0, "limit": DEFAULT_LIMIT, "calls": 0})


def note(model: str, tokens: int, prompt: int = 0, completion: int = 0) -> None:
    """记一次调用花掉的 token。model 为空或 tokens<=0 就什么都不做（不编数）。"""
    model = (model or "").strip()
    if not model or tokens <= 0:
        return
    with _LOCK:
        d = _load()
        e = _entry(d, model)
        e["used"] = int(e.get("used") or 0) + int(tokens)
        e["calls"] = int(e.get("calls") or 0) + 1
        e["last_prompt"] = int(prompt or 0)
        e["last_completion"] = int(completion or 0)
        e["last_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save(d)


def mark_exhausted(model: str) -> None:
    """平台回了 insufficient_quota 就盖章：这个模型已经没有免费额度了。"""
    model = (model or "").strip()
    if not model:
        return
    with _LOCK:
        d = _load()
        e = _entry(d, model)
        e["exhausted"] = True
        e["exhausted_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        e["limit"] = e.get("limit") or DEFAULT_LIMIT
        _save(d)


def seed(model: str, used: int, limit: int = DEFAULT_LIMIT) -> None:
    """把控制台看到的"已消耗"写进来当起点（会覆盖该模型的 used，不动 calls 之外的历史）。"""
    with _LOCK:
        d = _load()
        e = _entry(d, model)
        e["used"] = int(used)
        e["limit"] = int(limit) or DEFAULT_LIMIT
        e["seeded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save(d)


def status() -> List[Dict[str, Any]]:
    """给界面的那份表：每个模型剩多少、该不该提示。"""
    d = _load()
    out = []
    for model, e in sorted((d.get("models") or {}).items()):
        used = int(e.get("used") or 0)
        limit = e.get("limit")
        pct = round(used / float(limit), 4) if limit else None
        out.append({"model": model, "used": used, "limit": limit,
                    "left": (int(limit) - used) if limit else None,
                    "pct": pct, "calls": int(e.get("calls") or 0),
                    "exhausted": bool(e.get("exhausted")),
                    "last_at": e.get("last_at") or "",
                    "level": ("exhausted" if e.get("exhausted") else
                              "danger" if pct is not None and pct >= DANGER_AT else
                              "warn" if pct is not None and pct >= WARN_AT else "ok")})
    out.sort(key=lambda r: (-(r["pct"] if r["pct"] is not None else -1), r["model"]))
    return out


def summary_line(models: List[str]) -> str:
    """给状态栏用的一句话：只提该提醒的那一个，别把三个模型都念一遍。"""
    rows = {r["model"]: r for r in status()}
    for name in models:
        r = rows.get(name)
        if not r:
            continue
        if r["level"] == "exhausted":
            return "%s 免费额度已用尽" % name
        if r["pct"] is None:
            continue
        if r["level"] in ("warn", "danger"):
            return "%s 免费额度只剩 %.0f%%（约 %s tokens）" % (
                name, (1 - r["pct"]) * 100, "{:,}".format(r["left"]))
    return ""


def _usage_tokens(resp: Any) -> int:
    u = getattr(resp, "usage", None)
    return int(getattr(u, "total_tokens", 0) or 0)


_INSTALLED = {"done": False, "error": ""}


def install() -> Dict[str, Any]:
    """在 openai 的 chat.completions.create 外面套一层记账 + 额度耗尽盖章。

    只包成功路径的 usage 和 403 的标记，**不改返回值也不改异常类型**：
    调用方原来怎么拿 resp 现在还怎么拿，原来抛什么错还是抛什么错。
    包不上（SDK 换了内部结构）就把原因留在 last_error 里，让界面能显示"额度没在记账"，
    而不是悄悄不记。
    """
    if _INSTALLED["done"]:
        return _INSTALLED
    try:
        from openai.resources.chat.completions import Completions
    except Exception as e:                                     # noqa: BLE001
        _INSTALLED["error"] = "openai SDK 结构变了，额度记账没挂上：%s" % e
        return _INSTALLED
    if getattr(Completions.create, "_quota_wrapped", False):
        _INSTALLED["done"] = True
        return _INSTALLED
    orig = Completions.create

    def wrapped(self, *args, **kwargs):                        # noqa: ANN001
        model = str(kwargs.get("model") or "")
        try:
            resp = orig(self, *args, **kwargs)
        except Exception as e:                                 # noqa: BLE001
            msg = str(e)
            if "insufficient_quota" in msg or "Free quota exhausted" in msg:
                mark_exhausted(model)
            raise
        note(model, _usage_tokens(resp),
             getattr(getattr(resp, "usage", None), "prompt_tokens", 0) or 0,
             getattr(getattr(resp, "usage", None), "completion_tokens", 0) or 0)
        return resp

    wrapped._quota_wrapped = True
    Completions.create = wrapped
    _INSTALLED["done"] = True
    return _INSTALLED


def installed() -> Dict[str, Any]:
    return dict(_INSTALLED)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="免费额度记账（本地账，不是平台账）")
    ap.add_argument("cmd", choices=("show", "seed", "clear"))
    ap.add_argument("model", nargs="?", default="")
    ap.add_argument("used", nargs="?", type=int, default=0)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    a = ap.parse_args()
    if a.cmd == "seed":
        seed(a.model, a.used, a.limit)
        print("已把 %s 的已消耗记为 %s" % (a.model, "{:,}".format(a.used)))
    elif a.cmd == "clear":
        _save({"models": {}, "note": "手动清空"})
        print("账已清（这只影响本地记账，平台侧额度不会变）")
    else:
        for r in status():
            print("%-24s 已用 %9s / %-9s %6s  %s" % (
                r["model"], "{:,}".format(r["used"]),
                "{:,}".format(r["limit"]) if r["limit"] else "未知",
                ("%.1f%%" % (r["pct"] * 100)) if r["pct"] is not None else "—",
                {"ok": "", "warn": "← 该提示了", "danger": "← 快没了",
                 "exhausted": "← 已用尽（平台会 403）"}[r["level"]]))
