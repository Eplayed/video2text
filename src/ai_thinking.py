# -*- coding: utf-8 -*-
"""ai_thinking.py — 会"先思考再回答"的模型，快速档这类活等不起，在 SDK 边界统一关掉。

为什么要有这一层（2026-10-28 实测，同一份 7936 字的海报拆文案提示词）：
  · `qwen3.8-omni-flash` 开着思考：**>230 秒不返回**（生产那层 timeout=300 + 重试一次，等于必挂）；
  · 同一份提示词带 `extra_body={"enable_thinking": False}`：**21 秒**回来、JSON 可直接解析、6721 tokens。
所以"换到这个模型"和"这一档要能用"是两件事，缺了关思考这一步，拆文案只会一直转圈到超时。

为什么包在 SDK 边界而不是在调用点各加一行：`chat.completions.create` 现在有 15 处，散在
ai_poster / content_store / poster_breakdown / frame_pool / graphics.channels / web.app / main 里，
各写一遍必漏（`ai_quota` 的注释里就是同一个理由）。这里只包一层，**只加参数、不改返回值也不改异常**。

哪些模型要关：写在配置 `AI_THINKING_OFF` 里（逗号分隔的模型名前缀），默认空＝行为零变化。
不在代码里写死模型名——这个网关的模型名一年换好几次（见 AI_MODEL_VISION / AI_MODEL_IMAGE 那两条注释）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, "config", "config.env.local")
_INSTALLED: Dict[str, Any] = {"done": False, "error": "", "off_prefixes": []}


def _read_prefixes() -> List[str]:
    """从 config.env.local 读 AI_THINKING_OFF；读不到就什么都不关。"""
    raw = ""
    try:
        with open(ENV_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("AI_THINKING_OFF"):
                    raw = line.split("=", 1)[1].strip().strip('"')
                    break
    except OSError:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def should_off(model: str, prefixes=None) -> bool:
    """模型名命中配置里列的前缀才关。空配置＝一个都不动（默认零变化）。"""
    m = (model or "").strip().lower()
    if not m:
        return False
    for pfx in (prefixes if prefixes is not None else _read_prefixes()):
        if m.startswith(pfx.strip().lower()):
            return True
    return False


def install() -> Dict[str, Any]:
    """给 `Completions.create` 套一层"命中名单就补 enable_thinking=False"。

    调用点已经自己传了 extra_body 的话**不覆盖它**——显式意图优先于这里的默认。
    包不上（SDK 内部结构变了）把原因留在 error 里，让界面能说"这一档没关思考"，
    而不是让人以为参数发出去了。
    """
    if _INSTALLED["done"]:
        return _INSTALLED
    prefixes = _read_prefixes()
    _INSTALLED["off_prefixes"] = prefixes
    if not prefixes:
        _INSTALLED["done"] = True          # 没配名单：什么都不做，但也算挂好了
        return _INSTALLED
    try:
        from openai.resources.chat.completions import Completions
    except Exception as e:                                     # noqa: BLE001
        _INSTALLED["error"] = "openai SDK 结构变了，关思考没挂上：%s" % e
        return _INSTALLED
    if getattr(Completions.create, "_thinking_wrapped", False):
        _INSTALLED["done"] = True
        return _INSTALLED
    orig = Completions.create

    def wrapped(self, *args, **kwargs):                        # noqa: ANN001
        if should_off(kwargs.get("model") or "", prefixes):
            extra = kwargs.get("extra_body")
            if isinstance(extra, dict):
                if "enable_thinking" not in extra:
                    kwargs = dict(kwargs, extra_body=dict(extra, enable_thinking=False))
            else:
                kwargs = dict(kwargs, extra_body={"enable_thinking": False})
        return orig(self, *args, **kwargs)

    wrapped._thinking_wrapped = True
    Completions.create = wrapped
    _INSTALLED["done"] = True
    return _INSTALLED


def installed() -> Dict[str, Any]:
    return dict(_INSTALLED)
