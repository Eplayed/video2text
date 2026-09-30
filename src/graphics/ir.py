# -*- coding: utf-8 -*-
"""Card IR（中间表示）schema 文档与轻量助手。

Phase 1 仅落 schema 常量、extract_cards() 与 sanitize_cards()（渲染前清洗编辑向元语言），
不改造卡片结构。
「通用骨架 + channel_extra」分层留待 Phase 2（见迁移方案 7.1：本期不动）。

Card dict 约定字段（渠道无关骨架）：
  kind:       str   "cover" | "list"
  title:      str   卡片标题
  subtitle:   str   副标题（可空）
  section:    str   条目分组名（list 卡）
  icon:       str   单字图标（list 卡）
  items:      list  [{name, desc, tag, faction}]（list 卡）
  hooks:      list  [{t, d}]（cover 卡）
  note:       dict  {title, text}（可空）
  bg:         str   "neutral" | "alliance" | "horde" | "skin"（背景）
  timeline / timeline_label / timeline_note / badge / faction / faction_text:
              cover 卡可选字段
渠道专属扩展字段统一收入 channel_extra（Phase 2 启用）。
"""
import re

KIND_COVER = "cover"
KIND_LIST = "list"

# ── 成品文案清洗：剔除「写给编辑看」的元语言 ──
# 卡片渲染出来就是直接发布给读者的成品图，素材缺口说明（口播未给/整合稿没提）与
# 审核提示（请人工核对/本地模板兜底）只该出现在 lint 结果或 copy 文本里，绝不能进图。
# 素材缺口类：主语（口播/素材/原文…）+ 谓语（未给/没提/缺失…）的组合一律命中
_META_GAP_RE = re.compile(
    r"(口播|素材|原文|原视频|视频|录音|转写|转录|文稿|底稿|整合稿|整理稿|ASR|asr)"
    r"\s*(里|中|内|图片里)?\s*"
    r"(未给|没给|没有给|未提|没提|未讲|没讲|未出现|没出现|未提供|没提供|缺失|为空|里没|中没|没有)"
)
# 审核提示类：整词命中即视为编辑向
_META_NOTE_WORDS = (
    "请人工核对", "人工核对", "需人工", "待核实", "待确认", "需核实", "需确认",
    "本地模板", "模板兜底", "兜底生成", "整合稿正文", "整理稿正文", "详见正文",
    "无法确认", "暂无数据", "数据缺失", "未提供", "未给出", "待补充", "占位",
    # 2026-09-30：AI 失败时的规则版骨架说明会随整合稿正文渗进卡片，实测被印到读者可见图面上
    "AI生成失败", "生成失败，已保存", "Error code", "规则版", "未做事实核对",
    "仅提供骨架", "配置 AI 后重新生成",
)
# 纯占位值：整字段等于这些值时视为空，直接丢弃（整合稿纪律要求「没有就写无」，会渗进卡片）
_PLACEHOLDER_VALUES = {"", "无", "无。", "暂无", "暂无。", "n/a", "na", "none", "null",
                       "-", "—", "/", "待定", "tbd", "未知"}
# 短结构字段（标题/栏目名/标签等）：不做分句剔除，只在整值命中时清空，避免破版
_SHORT_KEYS = {"kind", "bg", "title", "section", "icon", "name", "tag", "t",
               "badge", "timeline_label", "faction"}
# 分句边界：保留分隔符，逐句判断
_CLAUSE_RE = re.compile(r"(?<=[；;。！!？?\n])")
# 条目型列表字段 → 其正文键：正文被整句清掉时，条目只剩空壳（如「未公布项」），整条丢弃
_LIST_TEXT_KEY = {"items": "desc", "hooks": "d"}


def _is_meta_clause(clause):
    """单句是否属编辑向元语言。"""
    text = str(clause or "")
    if not text.strip():
        return False
    if _META_GAP_RE.search(text):
        return True
    return any(w in text for w in _META_NOTE_WORDS)


def _clean_text(value):
    """长文本字段：按分句剔除命中句，保留其余内容；全命中则返回空串。"""
    text = str(value or "")
    if not text.strip():
        return ""
    if text.strip().lower() in _PLACEHOLDER_VALUES:
        return ""
    if not _is_meta_clause(text):
        return text
    kept = [c for c in _CLAUSE_RE.split(text) if c.strip() and not _is_meta_clause(c)]
    out = "".join(kept).strip()
    return out


def _clean_short(value):
    """短结构字段：整值为占位词或整值命中元语言时清空，否则原样返回。"""
    text = str(value or "")
    if text.strip().lower() in _PLACEHOLDER_VALUES:
        return ""
    if _is_meta_clause(text):
        return ""
    return text


def _clean_value(key, value):
    """递归清洗：dict 按键名分派，list 逐项清洗并丢弃空值。"""
    if isinstance(value, str):
        return _clean_short(value) if key in _SHORT_KEYS else _clean_text(value)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            cv = _clean_value(k, v)
            if cv not in ("", None, [], {}):
                out[k] = cv
        return out
    if isinstance(value, list):
        out = []
        for v in value:
            cv = _clean_value(key, v)
            if cv not in ("", None, [], {}):
                out.append(cv)
        return out
    return value


def _clean_entries(entries, text_key):
    """清洗条目列表：正文被整句清掉的条目直接丢弃（剩下的名称/标签对读者无意义）。"""
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        had_text = bool(str(entry.get(text_key) or "").strip())
        cleaned = _clean_value(None, entry)
        if had_text and not str(cleaned.get(text_key) or "").strip():
            continue
        if cleaned:
            out.append(cleaned)
    return out


def sanitize_cards(cards):
    """渲染前清洗卡片文案，返回同一个 list（元素被替换为清洗后的 dict）。

    - 长文本（subtitle/timeline/timeline_note/desc/text/d/hooks.d…）按分句剔除编辑向句子；
    - 短字段（title/section/name/tag/icon…）只在整值命中时清空，避免版面破洞；
    - 条目正文（items[].desc / hooks[].d）被整句清掉时，该条目整体丢弃；
    - note 的 text 被清空后整块移除（各模板对空 note 均不渲染）；
    - 内页卡 items 被清空后整卡丢弃（至少保留一张卡，避免出空白图）；
    - 空字符串/空列表字段直接丢弃，模板侧统一走 card.get(...) 容错。
    """
    if not isinstance(cards, list):
        return cards
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            continue
        rest = {}
        entries = {}
        for key, value in card.items():
            text_key = _LIST_TEXT_KEY.get(key)
            if text_key and isinstance(value, list):
                cleaned_list = _clean_entries(value, text_key)
                if cleaned_list:
                    entries[key] = cleaned_list
            else:
                rest[key] = value
        cleaned = _clean_value(None, rest)
        cleaned.update(entries)
        note = cleaned.get("note")
        if isinstance(note, dict) and not str(note.get("text") or "").strip():
            cleaned.pop("note", None)
        cards[i] = cleaned
    # 内页条目被清空后整卡丢弃，避免渲染出一张近乎空白的图（至少保留一张卡）
    kept = [c for c in cards
            if isinstance(c, dict) and (c.get("kind") == "cover" or c.get("items"))]
    if kept and len(kept) != len(cards):
        cards[:] = kept
    return cards


def extract_cards(data):
    """从 LLM / 兜底返回的 dict 中取出 cards 列表（容错，非 list 返回空）。"""
    if not isinstance(data, dict):
        return []
    cards = data.get("cards") or []
    return cards if isinstance(cards, list) else []
