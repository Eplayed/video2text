# -*- coding: utf-8 -*-
"""渠道适配器抽象基类（迁移方案 5.2）。

定义图文渠道统一契约：画布、卡片数、模板集、prompt、字数限制、
合规检查(lint)、发布清单(checklist)、导出(export)。

Phase 1 仅头条适配器落地已有能力；lint / checklist / export 为最小实现，
Phase 2（公众号渠道）再补全。本基类不引入任何渠道目录/画布常量。
"""
from abc import ABC, abstractmethod


class ChannelAdapter(ABC):
    name = ""
    canvas = (0, 0)
    card_count = 4

    @abstractmethod
    def templates(self):
        """返回该渠道可用模板 key 列表。"""

    @abstractmethod
    def prompt(self, summary, theme="", template=""):
        """构造卡片化 prompt（字符串）。"""

    def limits(self):
        """字数 / 字段限制，供前端提示与 lint 使用。默认空。"""
        return {}

    def lint(self, cards):
        """合规 / 质量检查，返回问题列表（空 = 通过）。Phase 2 补全。"""
        return []

    def checklist(self):
        """发布前人工检查清单。Phase 2 补全。"""
        return []

    def export(self, summary, ai_config, **kwargs):
        """生成并落盘，返回 manifest dict。默认未实现。"""
        raise NotImplementedError
