# -*- coding: utf-8 -*-
"""渠道无关图文生成内核（Phase 1 抽取自 toutiao_graphics.py）。

子模块：
  ir          Card IR schema 文档与轻量助手
  skins       皮肤 / 调色板 / 题材素材解析（目录注入）
  css_engine  __TOKEN__ CSS 替换引擎（画布尺寸、资产目录参数化）
  templates   7 套模板 CSS + 14 渲染函数 + 注册表
  renderer    Playwright 渲染内核：量高 → 缩放 → 截图（画布参数化）
  package     manifest 包管理（输出目录参数化）
  channels    渠道适配器（base 抽象 + toutiao 头条）

设计纪律：内核不绑定任何渠道目录/画布/URL 前缀，全部由 channels/* 适配器注入。
"""
