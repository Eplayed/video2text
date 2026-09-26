# -*- coding: utf-8 -*-
"""Card IR（中间表示）schema 文档与轻量助手。

Phase 1 仅落 schema 常量与 extract_cards()，不改造卡片结构。
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

KIND_COVER = "cover"
KIND_LIST = "list"


def extract_cards(data):
    """从 LLM / 兜底返回的 dict 中取出 cards 列表（容错，非 list 返回空）。"""
    if not isinstance(data, dict):
        return []
    cards = data.get("cards") or []
    return cards if isinstance(cards, list) else []
