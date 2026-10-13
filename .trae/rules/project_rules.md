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
核心链路：采集 → ASR 转写 → AI 加工（标题/摘要/关键词/分类）→ SQLite → Flask 工作台 → 图文出图（头条/公众号渠道）。
（Dify 知识库同步已于 2026-09-30 下线：一条素材都未真正发布过，且 Dify 侧写稿质量不达标）
同时是**选题/渠道策略的唯一权威源**（`src/content_store.py` 的 `RADAR_CHANNEL_STRATEGY` / `CHANNEL_STRATEGY_GLOBAL`）。

## 开始前必读（按顺序）

1. `docs/AGENT-HANDOFF.md` — **接手必读那一份**：规则有几份、skill 在哪、门禁怎么跑、
   界面改动收尾要自己走一遍、并发提交怎么隔离、哪些口径别再翻回去
2. `CHANGELOG_FOR_AI.md`（从顶部往下读）— 每处改动为什么改
3. `output/角色交接/`（按日期那份）— 内容产线各角色的结论与每份文件的最低要求
4. `README.md` — 基础用法
5. `PROJECT_STATUS.md` — 现状快照**停在 2026-09-10**，只当历史背景读，别当现状

## 跨仓库锚（必须认）

- 三仓库架构、权威源地图、换机流程 → **media-workbench 仓库 `docs/DEV-SYNC.md`**（改策略只改权威源一处）
- 自媒体产线双端任务分工、工作纪律 → **media-workbench 仓库 `AGENTS.md` 第七节**

## 关键纪律

1. 改策略：只改 `content_store.py` 权威源，通过 `/api/strategy/channels` 下发，不直接改工作台侧逻辑。
2. 改代码后必须重启 Flask（无热重载）：先按占用端口的 PID 杀旧进程（`lsof -nP -i :15801` 取 PID 后 `kill`），再 `cd web && python3 app.py`；重启后用 `lsof` 确认 PID 已换、新进程真正接管。禁用 `pkill -f "web/app.py"`——它匹配不到实际命令行（`.../MacOS/Python app.py`），会造成旧进程占端口、新进程静默退出。
3. 运行数据不进 git：`config/config.env`（Cookie/API Key）、`video_index.json` 等索引文件、`*.json` 索引——换机时手动迁移。
4. AI 加工未配置时静默跳过、不阻断采集主流程。
5. 界面类改动收尾前**自己把流程走一遍**：`python3 scripts/agent/ui_walk.py <整合稿id> "主题" --plan`（headless 走弹窗、把每处用户会读到的文案原样打印＋截图），再跑门禁。号主只有「截图 + 一句话」这一种反馈方式，等他指出来就已经花掉他一轮。判据要跟后端同源、别拿上次回传的标记当现状——那次抓到的是「界面写 0 元、后端照样扣钱」。

## 角色路由（自媒体内容产线）

内容线的活按角色走（全局 skill `~/.qoder-cn/skills/media-*`），**进会话先定渠道**（头条 / 公众号）：两渠道的素材池、规则权威源、产物目录、门禁 kind 全部分家，一条线的结论不许直接搬到另一条线。

`/media-pm`（需求与验收标准）→ `/media-planner`（选题与角度；**角度门三问没答完禁止写稿**）→ `/media-ui`（版式与样张；必须有截图才算完成）→ `/media-dev`（产线代码；回归 + 按关注点提交）→ `/media-ops`（分发、台账；每周只看涨粉与钱两个数）。

角色之间**用文件交接，不靠会话记忆**：落点 `output/角色交接/<日期>-<渠道>-<环节>.md`（命名与每份最低要求见该目录 README）。被打回时回对应那份文件改结论，不许口头改。

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
