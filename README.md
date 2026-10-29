# video2text

素材采集 + 选题索引 + 内容工作台：抖音视频 / 公众号文章 / B站 / 暴雪官方资讯 进来，
ASR 转写与场景抽帧 → AI 加工成整合稿 → 工作台选题、排产、出图 → **人工发布** → 数据回灌。

CLI（`main.py`）负责"采进来 + 转写 + 建索引"这一截；日常主用是 **Flask 工作台**（下面「工作台」一节）。

**接手先读**：`AGENTS.md`（纪律）→ `docs/AGENT-HANDOFF.md`（工具与流程）→ `PROJECT_STATUS.md`（现状快照）→
`CHANGELOG_FOR_AI.md`（从顶部读，每处改动为什么改）。本文件只讲怎么跑起来。

## 功能

- 批量采集抖音视频元数据（标题、作者、发布时间、封面、标签），风控拦截时用真实 Chromium 兜底抓详情
- ASR 转写口播（faster-whisper，带时间戳）+ **场景抽帧**（帧带时间戳与就近口播，存 `output/assets/frames/`）
- AI 加工：标题/摘要/关键词/分类，以及**整合稿**（一篇稿可由多条素材合成）
- 转写质量兜底：术语表 initial_prompt、繁简与错字对确定性回改
- 处理结果自动写入 Excel（多 Sheet），自动维护 `video_index.json` 供选题筛选
- 工作台：素材库、选题雷达（渠道策略权威源下发）、订阅与自动同步、排产板、出图、质检、发布台账
- 出图两条路：HTML 卡片信息图（模板 + 三轴）与 **AI 海报**（拆解 → 免费看版面 → 出图；攻略页 0 元）

## 快速开始

### 1. 准备 douyin_parse

本项目依赖 `douyin_parse` 解析抖音视频信息。默认读取 `/tmp/douyin_parse`，也可以通过环境变量或命令参数指定：

```bash
export DOUYIN_PARSE_DIR="/path/to/douyin_parse"
# 或运行时使用 --parser-dir "/path/to/douyin_parse"
```

### 2. 配置 Cookie

复制配置文件并填入抖音 `sessionid`：

```bash
cp config/config.env config/config.env.local
# 编辑 config.env.local，填入 DOUYIN_SESSIONID
```

`sessionid` 获取方式：浏览器登录抖音 -> F12 -> Application -> Cookies -> douyin.com -> 找到 `sessionid`

### 3. 安装依赖

依赖清单见 `requirements.txt`（按 Python 3.9 实测版本钉版）：

```bash
python3 -m pip install -r requirements.txt
```

另需手动准备（不在 pip 范围）：`ffmpeg`（ASR 抽音频）、`playwright install chromium`（图文出图 + 抖音风控兜底）、`vendor/douyin_parse` 解析器目录（已 gitignore，换机手动迁移）。

### 4. 运行

```bash
# 处理 Excel 中所有未处理的链接
python main.py --excel "/path/to/抖音视频信息.xlsx" --cookie "sessionid=your_sessionid"

# 只处理指定 Sheet
python main.py --excel "/path/to/抖音视频信息.xlsx" --sheet "AI面试" --cookie "sessionid=your_sessionid"

# 指定行处理
python main.py --excel "/path/to/抖音视频信息.xlsx" --row 3 --cookie "sessionid=your_sessionid"

# 使用 config/config.env.local 中的 DOUYIN_SESSIONID
python main.py --excel "output/抖音视频信息.xlsx"

# 不更新索引
python main.py --excel "output/抖音视频信息.xlsx" --no-update-index

# 查看适合整理成文章的视频候选
python scripts/list_candidates.py --topic "流放2攻略"
```

## 参数说明

| 参数 | 说明 |
|------|------|
| `--excel` | Excel 文件路径 |
| `--sheet` | Sheet 名称，默认 `抖音视频数据` |
| `--config` | 本地配置文件，默认 `config/config.env.local` |
| `--parser-dir` | `douyin_parse` 项目目录，默认读取 `DOUYIN_PARSE_DIR` 或 `/tmp/douyin_parse` |
| `--cookie` | 完整 cookie 字符串或 `sessionid=xxx` |
| `--cookie-file` | 从文件读取 cookie |
| `--asr-model` | Whisper 模型：`tiny`/`base`/`small`/`medium`/`large`，默认 `base` |
| `--ai-method` | AI 优化方式：`skip`/`openai`/`deepseek`，兼容旧写法 `openai_api`/`deepseek_api` |
| `--ai-key` | API Key（也可写在 `config.env.local`） |
| `--ai-base` | OpenAI 兼容接口 `base_url` |
| `--ai-model` | 模型名称 |
| `--update-index` / `--no-update-index` | 是否处理完成后更新 `video_index.json`，默认开启 |

## Excel 格式

每行一个视频，基础列含义：

| 列 | 字段 |
|----|------|
| A | 原始链接 |
| B | 处理状态 |
| C | 视频ID |
| D | 作者 |
| E | 发布时间 |
| F | 标题 |
| G | 视频文案（ASR） |
| H | 标签 |
| I | 封面URL |
| J | 视频链接 |
| K | 口播原文（ASR） |
| L | AI优化文案 |
| M | AI备选标题 |
| N | 关键词摘要 |
| O | 备注 |

为了让索引直接服务文章生产，可以继续追加这些列，脚本会自动识别：

| 字段 | 用途 |
|------|------|
| 选题等级 | A/B/C/已转文章 |
| 适合平台 | 头条号/公众号/双平台/不建议 |
| 文章角度 | 后续二创文章的切入角度 |
| 事实风险 | 需要联网核查或人工确认的问题 |
| Word文档路径 | 已生成文章的本地 Word 路径 |
| 是否已发布 | 发布状态 |

## 输出文件

所有输出文件（处理完成的 Excel、生成的报告 Word 等）统一放在 `output/` 目录，不提交 Git。

## 工作台（日常主用）

```bash
bash run_web.sh                    # 或 cd web && python3 app.py
# 打开 http://127.0.0.1:15801
```

- **无热重载**：改完任何 `.py` 必须重启。按占用端口的 PID 杀旧再起（`lsof -nP -i :15801` 取 PID），
  Qoder 里直接跑 `/workbench-restart`；**禁用 `pkill -f "web/app.py"`**（匹配不到真实命令行，会造成旧进程占端口、新进程静默退出）。
- 主要页面：素材库 / 整理稿（含「AI 海报」弹窗）/ 选题雷达 / 订阅与自动同步 / 排产板（`web/static/schedule.html`）/ 运营台账 / 海报成品页。
- 异步任务一律"启动 + `/api/*/status` 轮询"；详情接口单次约 5 秒（每次整表读 Excel），**慢不等于坏了**。

## 出图两条路

### A. HTML 卡片信息图（`src/graphics/`）

整合稿（`toutiao_mix`）→ 整理稿页「生成头条图文」→ 9:16 竖版卡片 + 微头条文案，产物落 `output/toutiao/<summary_id>/`。
模板库 + **三轴**（配色 / 字体 / 版式）两渠道共用；题材决定文案点明的游戏与插画素材文件夹，皮决定整套配色与背景。
改三轴前必读 `.agents/memories/40-graphics-variants.md`（三轮"选了不生效"的静默失效复盘），改完跑 `/graphics-verify`。

### B. AI 海报（`src/ai_poster.py` + `src/poster_typeset.py`）

弹窗三步：**① 拆文案**（模型定几页、每页讲什么、绑哪张画面）→ **② 看版面 · 免费**（与出图同一个排版函数，花钱前先看）→ **③ 出图**。

- **攻略页那一档 0 元**：画面用素材真帧、底图是这套皮肤的固定渐变、中文由程序写真字体（不存在错字）。
  三套皮（深墨绿 / 米白 / 暗蓝夜色）按整理稿 id 轮换，免得十篇一个脸。
- 硬口径：一页 1 张图、**每页必须有一张关键帧**、整组同一编号只用一次、两页之间差 12 秒以上；
  印之前逐张再核对一次——虚的、带作者字幕的不许上版，被拦下就当场换一张并写明换成了哪张。
- 出图那一档（叫模型画底图，0.5 元/张）**2026-10-29 已撤**：不再自带默认模型名，没底图就明着拒绝并说给出路。
  要模型画的画面走「③b 豆包 · 回传底图」（复制提示词去豆包，回来传上，0 元）；确实要恢复就在
  `config/config.env.local` 加回 `AI_MODEL_IMAGE=模型名`。
- 成品落 `output/poster/<整理稿id>/`（`poster_p1.png…` + `manifest.json` + `plan.json` 文案备份，重启后弹窗还能接回来）。
- 出图前后有四道机器闸：骨架稿、待核数字不许当卖点、建议玩家能不能照做、编辑证据链（问题/新增/依据 + 逐张画面依据）。
  闸的结论都写进成品清单与结果那句话里，**"图出了"和"能发了"是两件事**。

## 项目结构

```text
video2text/
├── main.py                 # CLI：采集 + ASR + 索引更新
├── requirements.txt        # 依赖钉版（Python 3.9）
├── run_web.sh              # 工作台启动脚本（端口 15801，无热重载）
├── AGENTS.md / PROJECT_STATUS.md / CHANGELOG_FOR_AI.md   # 规则 / 现状 / 改动史
├── docs/                   # 带日期的调研与排产文档；docs/归档/ = 已走完的方案（别照着动手）
├── scripts/                # 14 条 e2e 门禁 + agent/（界面走查、选项审计、隔离测试服务）+ 数据脚本
│   ├── list_candidates.py · check_variant_tokens.py · check_douyin_cookie.py
│   ├── e2e_toutiao_variants.py · e2e_wechat_variants.py · e2e_poster_layouts.py · e2e_guide_frames.py
│   └── e2e_guide_gate（事实闸）· e2e_action_gate · e2e_provenance · e2e_frame_judge ·
│       e2e_ai_poster · e2e_plan_brief/series/tasks · e2e_sync_tasks
├── .qoder/skills/          # 13 个：workbench-restart · graphics-verify · article-audit/humanize · media-*（9 角色）
├── src/                    # 38 个模块，分组看：
│   ├── content_store.py    #   SQLite + AI 加工 + 渠道策略权威源 + 选题雷达聚合
│   ├── 采集源              #   fetch_user_videos · browser_fetch · wechat_fetcher · bilibili_fetcher ·
│   │                       #   douyin_notes · blizzard_news · nga_forum
│   ├── frame_pool.py       #   场景抽帧 + 判帧 + 出图前核对（清晰度/裁字幕/内容框裁）
│   ├── graphics/           #   HTML 卡片渲染内核（templates/variants/skins/css_engine/renderer/channels）
│   ├── 海报线              #   ai_poster · poster_typeset · poster_templates · poster_breakdown
│   ├── 四道闸              #   draft_gate(graphics/gate) · fact_gate · action_gate · guide_provenance
│   ├── 排产与台账          #   plan_brief · plan_series · plan_tasks · ops_store · toutiao_stats ·
│   │                       #   review_store · sync_tasks · ai_quota
│   └── 质量与素材          #   draft_audit · humanize · term_check/asr_fix · material_store · topic_pack
├── web/                    # 工作台：app.py（124 路由）+ templates/index.html + static/schedule.html
├── vendor/douyin_parse/    # 内置解析器（已 gitignore）
├── config/                 # 配置模板（config.env / config.env.local 存 Cookie 与 key，永不入库）
└── logs/ output/ temp/     # 运行产物（已 gitignore；成品、帧池、索引都在这里）
```

注：`src/link_resolver.py`、`src/asr.py`、`src/ai_optimizer.py` 已于 2026-09-01 删除，功能由 `main.py` / `content_store.py` 承担；
Dify 知识库同步已于 2026-09-30 整体下线（一条素材都没真发布过，写稿质量不达标）。

## 视频索引

处理完视频后会自动更新 `video_index.json`。索引面向自媒体写稿，除了标题和摘要，还会记录：

- 视频来源、封面、作者、发布时间
- 粗分类：流放2攻略、暗黑4攻略、AI技术教程等
- 选题等级：A/B/C/已转文章
- 平台建议、文章角度、事实风险
- ASR 片段长度和前 800 字
- 已生成 Word 文档路径

后续写文章时，优先从 `video_index.json` 中筛选高价值视频，再联网核查关键事实，最后生成头条号或公众号文章。

## 注意事项

- **这个仓库是公开的**：`config/config.env*`（Cookie / API Key）、`output/`、`temp/`、`*.xlsx`、`*.docx`、
  `*.db`、`video_index.json` 等运行数据与产物一律不入库、不打印值；换机时手动迁移（清单见 media-workbench `docs/DEV-SYNC.md`）
- 抖音 Cookie 有时效性，过期后需重新获取；先跑 `python3 scripts/check_douyin_cookie.py` 自检再判是不是链路坏了
- ASR 走 CPU 推理，Mac 实测 base 够用；`OMP_NUM_THREADS` 不设会明显慢
- 游戏攻略类视频 ASR 容易把专有名词识别错：写稿前关键机制、数值、版本时间必须人工或联网核查
- 平台页面**只读**：不许在工作台里自动点发布/删除/改稿，也不许对平台发写请求；发布永远是人工点
- 多个 AI 会话可能同时在改这个仓库：动手前先看 `git status`，只提自己的文件与 hunk，push 前先 fetch
