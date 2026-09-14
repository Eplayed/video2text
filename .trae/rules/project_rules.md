Please also reference the following rules as needed. The list below is provided in TOON format, and `@` stands for the project root directory.

rules[1]:
  - path: @.agents/memories/30-python-backend.md
    description: Python/Flask 后端改动守则(仅当 AI 触碰 .py 文件时加载)
    applyTo[1]: **/*.py

# Additional Conventions Beyond the Built-in Functions

As this project's AI coding tool, you must follow the additional conventions below, in addition to the built-in functions.

# AGENTS.md — video2text（素材层）

> 本文件是双端共享上下文（Trae 与 WorkBuddy 都会读）。每次开工前先读本文件。

## 项目定位

抖音短视频/公众号文章 素材采集器 + 自媒体选题索引 + 内容工作台。
核心链路：采集 → ASR 转写 → AI 加工（标题/摘要/关键词/分类）→ SQLite → Flask 工作台 → Dify 知识库同步。
同时是**选题/渠道策略的唯一权威源**（`src/content_store.py` 的 `RADAR_CHANNEL_STRATEGY` / `CHANNEL_STRATEGY_GLOBAL`）。

## 开始前必读（按顺序）

1. `PROJECT_STATUS.md` — 现状快照（更新到 2026-09-10，优先看它）
2. `README.md` — 基础用法
3. `CHANGELOG_FOR_AI.md` — 历史改动与写稿流程约定

## 跨仓库锚（必须认）

- 三仓库架构、权威源地图、换机流程 → **media-workbench 仓库 `docs/DEV-SYNC.md`**（改策略只改权威源一处）
- 自媒体产线双端任务分工、工作纪律 → **media-workbench 仓库 `AGENTS.md` 第七节**

## 关键纪律

1. 改策略：只改 `content_store.py` 权威源，通过 `/api/strategy/channels` 下发，不直接改工作台侧逻辑。
2. 改代码后必须重启 Flask（无热重载）：`cd web && python3 app.py`（端口 15801）。
3. 运行数据不进 git：`config/config.env`（Cookie/API Key）、`video_index.json` 等索引文件、`*.json` 索引——换机时手动迁移。
4. AI 加工未配置时静默跳过、不阻断采集主流程。

# 通用工作纪律(所有 AI 工具生效)

## 沟通与输出

- 沟通、注释、文档一律简体中文;代码标识符用英文。
- 输出结构化数据(JSON)时必须带字段名和类型说明。
- 方案设计前先调研成熟方案(高星 GitHub 项目),只参考架构不抄代码,并给调研设时间盒。

## 编码兼容性(硬约束)

- Python 兼容 3.9:禁用 `match` 语句、`X | Y` 类型标注等 3.10+ 语法。
- Node.js:避免可选链 `?.` 与空值合并 `??` 写法。

## Git 纪律

- commit 按关注点拆分,一次提交只做一件事。
- 运行数据与密钥不入库(Cookie / API Key / 运行索引),保持被 .gitignore 排除。

## 改代码风格

- 偏好最小改动,不做整体重写;重构不改变外部行为。

---

You must always answer in Simplified Chinese. On the other hand, reasoning (thinking) should be in English to improve token efficiency.
