# -*- coding: utf-8 -*-
"""ai_failover.py — 池子里这个模型撞墙了，就自动换下一个再试一次。

为什么包在 SDK 边界（和 `ai_quota`/`ai_thinking` 同一个理由）：`chat.completions.create` 有 15 处，
散在六个文件里，各写一遍 try 必漏。装在这里，一次包住全部调用点。

什么算"这个模型不能用了"，只认这三种回包（2026-10-29 实测的形状）：
  · `insufficient_quota` / `Free quota exhausted` —— 额度没了（号主把「免费额度用尽即停」全开了，
    所以撞墙只会得到 403，不会悄悄按量扣钱）；
  · `Error code: 403` 且不是额度 —— 这个模型没给这个账号开放，换一个是合理处置；
  · `Model not exist` / 404 —— 端点又改模型名了（这一年发生过好几次）。

**不算**的：429 限流（换模型等于绕限流，且下一个也一样撞）、400 参数错
（那是我们自己的请求写错了，换个模型还是错——要真遇到说明池子里塞了不兼容的模型，
先量再放：实测 `kimi-k3` 拒 `temperature=0.3`、`glm-5.3` 拒 `enable_thinking=False`）。

换档必须留痕：换过谁、为什么、换成谁，记在 `events()` 里给设置页印出来。
不留痕的话，"这个模型今天怎么这么慢/怎么答案变了"就又变成一次人肉排查。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

_INSTALLED: Dict[str, Any] = {"done": False, "error": ""}
_LOCK = threading.Lock()
_EVENTS: List[Dict[str, str]] = []
MAX_EVENTS = 20


def _dead_reason(exc: Exception) -> str:
    msg = str(exc)
    if "insufficient_quota" in msg or "Free quota exhausted" in msg:
        return "免费额度用尽"
    if "Model not exist" in msg or "model_not_found" in msg or "Error code: 404" in msg:
        return "端点说没有这个模型"
    if "Error code: 403" in msg or "PermissionDenied" in msg:
        return "端点拒绝(403)"
    return ""


def events() -> List[Dict[str, str]]:
    with _LOCK:
        return list(_EVENTS)


def _remember(model: str, reason: str, to: str) -> None:
    row = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "from": model,
           "reason": reason, "to": to or "（池子里没有下一个了）"}
    with _LOCK:
        _EVENTS.append(row)
        del _EVENTS[:-MAX_EVENTS]


def install() -> Dict[str, Any]:
    """给 `Completions.create` 套一层"这个模型死了就换池子里下一个重试"。

    装在 `ai_quota`、`ai_thinking` 的**外面**（后装的在外层），这样换完模型名之后，
    关思考那层看到的是新模型、记账那层记的也是新模型。
    """
    if _INSTALLED["done"]:
        return _INSTALLED
    try:
        from openai.resources.chat.completions import Completions
    except Exception as e:                                     # noqa: BLE001
        _INSTALLED["error"] = "openai SDK 结构变了，自动换档没挂上：%s" % e
        return _INSTALLED
    if getattr(Completions.create, "_failover_wrapped", False):
        _INSTALLED["done"] = True
        return _INSTALLED
    orig = Completions.create

    def wrapped(self, *args, **kwargs):                        # noqa: ANN001
        from . import ai_models
        model = str(kwargs.get("model") or "")
        tier = ai_models.tier_of(model)
        if not tier:
            return orig(self, *args, **kwargs)     # 不在池子里（看图那档、手动指定的）：原样走
        tried = [model]
        while True:
            try:
                return orig(self, *args, **kwargs)
            except Exception as e:                             # noqa: BLE001
                reason = _dead_reason(e)
                if not reason:
                    raise
                nxt = ai_models.next_after(tier, str(kwargs.get("model") or ""), skip=tried)
                _remember(str(kwargs.get("model") or ""), reason, nxt)
                print("[ai-failover] %s %s → %s" % (
                    kwargs.get("model"), reason, nxt or "池子里没有下一个可用模型"), flush=True)
                if not nxt:
                    raise
                tried.append(nxt)
                kwargs = dict(kwargs, model=nxt)

    wrapped._failover_wrapped = True
    Completions.create = wrapped
    _INSTALLED["done"] = True
    return _INSTALLED


def installed() -> Dict[str, Any]:
    return dict(_INSTALLED)
