# -*- coding: utf-8 -*-
"""asr_fix.py — 转写落库前的两道规范化（改在源头，不改在下游）

为什么要有这一层：实测 471 条转写里 **164 条（35%）出来是繁体**（「迎來超大更新很多兄弟跟我說」），
另有同音错字（装等/官宣/上赛季…）。这两件事都发生在 Whisper **解码**阶段，
下游整理稿的提示词只能补救，补救不了底子——而且海报那一档是照抄整理稿的，
「微望奖励」就是这么一路流到成品图上的。号主 2026-10-12 的要求就一句：上游转写质量提高。

两道活：
  1) `hotwords()`：给 Whisper 一句简体术语开头（initial_prompt）。实测这一招同时压两件事——
     朝这些词解码，同音错字少；提示词是简体的，输出也更倾向简体。
  2) `normalize(segs)`：繁转简（OpenCC t2s）+ 按**已确认**的误写对做确定性替换。
     顺序不能反：「贏火」要先转成「赢火」，才对得上表里的 赢火→营火 那条。
     只改 confirmed_pairs（seen_in 是真素材、没标"待确认"）那一档，猜的映射不静默改。

OpenCC 没装就只做纠错、不转繁（返回的计数里繁体那项是 0），不抛异常——转写本身比规范化重要。
Python 3.9 兼容：不用 match / X|Y 语法。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_cc = {"obj": None, "tried": False}


def _t2s():
    """懒加载 OpenCC 转换器；没装就返回 None（只丢繁转简这一道，不影响别的）"""
    if _cc["tried"]:
        return _cc["obj"]
    _cc["tried"] = True
    try:
        from opencc import OpenCC
        _cc["obj"] = OpenCC("t2s")
    except Exception as e:
        print("[asr_fix] 没装 opencc，跳过繁转简（pip install opencc-python-reimplemented）:", e)
        _cc["obj"] = None
    return _cc["obj"]


def hotwords(max_chars=110):
    try:
        import term_glossary
        return term_glossary.asr_prompt(max_chars=max_chars)
    except Exception as e:
        print("[asr_fix] 术语提示词没拿到（不影响转写）:", e)
        return ""


def normalize_text(text):
    """一段文字走两道规范化，返回 (新文本, 繁体改动字数, 误写替换处数)"""
    s = text or ""
    trad = 0
    cc = _t2s()
    if cc is not None and s:
        out = cc.convert(s)
        if out != s:
            trad = sum(1 for a, b in zip(s, out) if a != b)
            s = out
    pairs = 0
    try:
        import term_glossary
        s, changed = term_glossary.apply_confirmed(s)
        pairs = sum(n for _w, _c, n in changed)
    except Exception as e:
        print("[asr_fix] 纠错表没生效（保留原文）:", e)
    return s, trad, pairs


def normalize(segs):
    """就地改 segs 里每条的 text（带时间戳的那份），返回 (繁体字数, 误写处数)。

    为什么改在 segs 上而不是只改最终字符串：帧池要拿"这一帧前后在说什么"去对图，
    只洗最终文本的话，图注旁边那句口播还是繁体。
    """
    trad = pairs = 0
    for s in segs or []:
        new, t, p = normalize_text(s.get("text") or "")
        if new != s.get("text"):
            s["text"] = new
        trad += t
        pairs += p
    return trad, pairs
