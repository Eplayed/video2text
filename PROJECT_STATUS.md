# video2text 项目现状（给 AI / Agent 的快照）

更新时间：2026-10-23
这是 10-23 的完整架构快照；当前接手状态先看 `NOW.md`，再核实际工作树和本题最新交接。
阅读顺序：`AGENTS.md`（纪律）→ `NOW.md`（当前接手入口）→ **本文件**（10-23 架构快照）；按本题需要再读 `output/角色交接/` 的最新原件、`CHANGELOG_FOR_AI.md` 的相关节或 `docs/AGENT-HANDOFF.md` 的工具章节。
双机协作/换机流程见 media-workbench 仓库 `docs/DEV-SYNC.md`。

> 本次（10-23）文档清理：根目录 5 篇一次性方案/历史快照移进 **`docs/归档/`**（迁移方案、Phase0/Phase1、
> 头条号与公众号交接梳理、海报布局 PRD），另删掉一份重复的旧 Cursor 规则 `.cursor/rules/overview.mdc`
> （与 `10-overview.mdc` 同体，只少一句中文尾注）。归档原因逐条写在 `docs/归档/README.md`。
> 同时把 `.rulesync/rules/10-overview.md`（规则**源**）补齐到与 `AGENTS.md` 一致——
> 以前源落后，下次 `rulesync generate` 会把新规则冲掉。

## 项目定位

抖音短视频 / 公众号文章 / B站 / 暴雪官方资讯 的**素材采集器 + 选题索引 + 内容工作台**，
往下接两条出图产线，再往接发布与台账。核心链路：

采集 → ASR 转写（带时间戳）+ 场景抽帧 → AI 加工（标题/摘要/关键词/分类/整合稿）→ SQLite
→ 工作台（选题、排产、生成、质检）→ **图文出图两条路** → 人工发布 → 数据回灌。

三条不可让的口径：
1. **选题/渠道策略唯一权威源** = `src/content_store.py` 的 `RADAR_CHANNEL_STRATEGY` / `CHANNEL_STRATEGY_GLOBAL`，通过 `/api/strategy/channels` 下发，工作台侧不另写一份。
2. **永不自动发布**：所有触到平台页面的动作都是"生成可粘贴产物 + 人工点发布 + 回来登记"。
3. **机器闸只挡确定性错误**，语义判断交人；闸的结论一律落进成品清单，不许只活在会话里。

## 文档地图（谁是什么）

| 文件 | 作用 | 状态 |
| --- | --- | --- |
| `AGENTS.md` / `CLAUDE.md` / `CODEBUDDY.md` / `.cursor/rules/*.mdc` | 各工具的项目规则 | 全部由 `.rulesync/rules/*` 生成，**四份 10-23 已对齐**；改规则改源，别只改一份 |
| `.rulesync/rules/10-overview.md`、`20-conventions.md`、`30-python-backend.md` | 规则**源** | 权威 |
| `.agents/memories/40-graphics-variants.md` | 三轴静默失效的复盘守则 | 改 `src/graphics/` 前必读 |
| `docs/AGENT-HANDOFF.md` | 换 agent 接手必读：规则几份、skill 在哪、门禁怎么跑、并发怎么隔离 | 10-19 |
| `CHANGELOG_FOR_AI.md` | 改动史（顶部最新，97 节） | 活跃 |
| `output/角色交接/*.md`（49 份） | 内容产线各角色的结论与每份文件最低要求 | 活跃 |
| `docs/*.md`（8 份带日期） | 一次性调研/盘点/排产表，仍被 `web/static/schedule.html` 等引用 | 保留 |
| `docs/归档/` | 已走完的方案与历史快照 | **别照着动手** |
| `公众号内容审计与工作流诊断-2026-09-23.md`、`公众号卡片参考拆解-2026-09-29.md` | 仍被 skill / `src/graphics/templates.py` 引用的方法论与模板依据 | 保留在根目录 |

## 当前架构

```
main.py                  CLI 入口（采集/ASR/索引更新；whisper 双引擎，ffmpeg 抽音频）
web/app.py               Flask 工作台后端，124 个路由（15801，无热重载）
web/templates/index.html 单页工作台（hash 路由 + 海报弹窗 + 排产板 + 素材库 + 选题雷达 + 运营台账）
web/static/schedule.html 排产板页（读 /api/plan/*，节点决定排产）
src/
  content_store.py       SQLite 内容库 + AI 加工 + 策略权威源 + 选题雷达聚合 + 转写规范化出口
  采集源                 fetch_user_videos / browser_fetch(+tools/douyin_browser_fetch.js) /
                         wechat_fetcher(WeWe RSS) / bilibili_fetcher / douyin_notes(小红书笔记) /
                         blizzard_news(暴雪官方) / nga_forum(NGA 无限板块，直写 videos，UI 入口待接)
  frame_pool.py          场景抽帧 + 判帧 + **出图前核对**（清晰度闸、裁后期字幕、按内容框裁；池在 output/assets/frames/<aid>/{raw,clean}）
  整合稿/加工            draft_audit · humanize(+humanize_routes) · term_check/term_glossary/asr_fix（转写质量）· topic_pack（选题包）
  出图·HTML 卡片线       toutiao_graphics + graphics/{templates,variants,skins,css_engine,renderer,package,ir,channels/{toutiao,wechat}}
  出图·AI 海报线         ai_poster（拆解/分页/风格/提示词）· poster_typeset（真字体排版，攻略页流式排）·
                         poster_templates（★ 收编模板）· poster_breakdown（参考图拆解）
  四道闸                 fact_gate（待核数字不许当卖点）· action_gate（建议玩家能不能照做）·
                         guide_provenance（编辑证据链：问题/新增/依据 + 逐张画面使用依据）· draft_gate（骨架稿）
  排产与台账             plan_brief（选题卡）· plan_series（双系列看板+槽位）· plan_tasks（任务）·
                         ops_store（发布台账）· toutiao_stats（数据回流）· review_store（质检记录）·
                         sync_tasks（自动同步定时器，配置在 config/sync_tasks.json）· ai_quota（额度按模型算）
  其他                   material_store（导出）· bank_board/ai_*（答案库线）· wow_reference · path_config · ffmpeg_probe
scripts/                 14 条 e2e/门禁（见下）+ agent/（界面走查、审计探针、隔离测试服务）+ build_*/import_* 数据脚本
.qoder/skills/           13 个：workbench-restart · graphics-verify · article-audit · article-humanize · media-*（9 个角色）
vendor/douyin_parse      内置解析器（免依赖 /tmp）
```

## 内容产线（头条线现在长什么样）

九角色按序走（skill 在项目内、`.gitignore` 掉，公开仓库不出现）：
`/media-pm` → `/media-planner`（角度门三问没答完禁止写稿）→ `/media-writer`（选题包→整合稿，交稿即过机器门）
或 `/media-material`（单条素材→素材档案）→ `/media-ui`（版式与样张，**必须有截图才算完成**）
→ `/media-publish`（发布清单、人工发、登记回填）→ `/media-ops`（台账，每周只看涨粉与钱）→ `/media-retro`。

出图两条路：
- **HTML 卡片线**（`src/graphics/`）：模板 + 三轴（配色/字体/版式）→ Playwright 截图。改前三轴守则、改完跑 `/graphics-verify`。
- **AI 海报线**（`src/ai_poster.py`）：弹窗三步 ① 拆文案 → ② 看版面（免费，与出图同一个排版函数）→ ③ 出图。
  攻略页那一档**画面用素材真帧、渐变底、字由程序排 → 0 元**；硬口径：一页 1 张图、**每页必须有图**、
  整组同一编号只用一次、两页之间差 12 秒以上、印之前逐张再核对一遍（虚的/带字幕的不许上版，拦下就当场换一张）。

## 数据与配置（10-23 实测）

- SQLite `output/video2text.db`，14 张表：`videos`(1286 行) / `ai_summaries`(45) / `subscriptions` /
  `publish_stats` / `week_stats` / `content_reviews` / `guide_provenance` /
  `plan_slots` `plan_tasks` `plan_candidates` `plan_slot_flags` `plan_publish_audit` `plan_match_runs` / `sqlite_sequence`
- 帧池 493 条素材有目录；海报成品包 25 个（`output/poster/<整理稿id>/`，含 `manifest.json` 与 `plan.json` 文案备份）
- `video_index.json` 等运行索引**全部 gitignore**，换机手动迁移；Excel `output/抖音视频信息.xlsx` 仍是采集真相源（A-O 基础列 + 扩展列）
- 敏感配置 `config/config.env` / `config.env.local`（Cookie、AI key）——**仓库是公开的，永不入库、永不打印值**；改前先备份
- 模型：拆解走 `deepseek-v4-pro-0813`（快速档，300s 超时 + 1 次重试），判帧/核对走 `qwen3-vl-plus`（免费额度）；**额度按模型分开算**
- 依赖钉版见 `requirements.txt`（系统 Python 3.9；无 venv；ffmpeg / playwright 内核 / 解析器换机手动准备）

## 运营现状（决定优先级，别拿旧口径判新事）

- 头条实测单价量级是零钱（0.1785 元/千），**决定量的是平台开不开池子**（节点脉冲），不是写法；
  决策型题材比告知型高一个量级。细则与算式见 `docs/你的量是怎么来的-2026-10-16.md`、`docs/涨粉与流量的卡点-2026-10-16.md`。
- 钱主要看**按篇入口**（贴吧/营地/大神暗黑圈），不是分成——见 `docs/图文奖池盘点-2026-10-16.md`。
- 11-05 无限服上线 / 12-11 是两个节点，排产按节点分班（`docs/11-05排产表-2026-10-16.md` + 工作台排产板）。
- 小红书：无图文分成，且 10-10 因"尾注自证 + 回复 XX 获取"被判 **AI 托管**限流——合规口径见 `docs/小红书违规面盘点.md`。
- 公众号线现状与接手清单在仓库外：`~/Documents/自媒体/公众号交接文档.md`。

## 门禁与验证（改完要跑哪些）

| 改动位置 | 跑什么 |
| --- | --- |
| 任意 `.py` | `python3 -m py_compile <file>`，然后按 `/workbench-restart` 重启（**禁 pkill**） |
| `src/graphics/` 或海报排版 | `/graphics-verify` 三线 + `scripts/e2e_poster_layouts.py`、`e2e_guide_frames.py`、`e2e_ai_poster.py` |
| 闸口 | `e2e_guide_gate.py`（事实闸，拿三篇已发错成品当夹具）、`e2e_action_gate.py`、`e2e_provenance.py`、`e2e_frame_judge.py` |
| 排产/台账/自动同步 | `e2e_plan_series.py`(127 条) `e2e_plan_tasks.py` `e2e_plan_brief.py` `e2e_sync_tasks.py` |
| 界面 | `scripts/agent/ui_walk.py` / `ui_poster_*_walk.py`（headless 走真页面 + 截图，**判据与后端同源**） |
| 选项是否真生效 | `scripts/agent/audit_poster_options.py`（0 元、纯红靶图当判据，末行 `ALL_OK`） |

## 已知注意事项

- 异步任务一律"启动 + `/api/*/status` 轮询"，改契约要连前端一起改。
- 详情接口单次约 5 秒（每次整表读 Excel），**别把慢当 bug**；前端等待给到 15 秒。
- 三条采集流水线（`api_process` / `api_fetch_and_process` / `api_subscriptions_sync`）历史上是逐行复制结构，
  差异（Excel 打开粒度、写入列、失败处理、状态契约、`mark_video_source` 取值）都还在，抽公共层前必须逐条保住；
  **行号会漂，别照旧文档的行号动手，用函数名定位**。
- 转写出口只做确定性两道（繁简、错字对），语义纠错不承诺；ASR 对游戏专有名词易错，数值/BD 类不能只靠口播。
- **多个 AI 会话并发改本仓库**：动手前 `git status` 比对，只提自己的文件/hunk，push 前 fetch，禁 `git add .`。
- 界面上任何"点了不会发生任何事"的控件必须置灰并写明原因，不许静默改掉用户点过的选择
  （10-23 一轮抓到 11 个选项里 5 个白放，复盘见 `CHANGELOG_FOR_AI.md` 顶部两节）。
- **列表页取图一律走 `thumbOf(url, w)` → `/media/<路径>?w=`**（10-24 首屏提速）：别退回直接塞原图，
  也别把"点开看大图"改回 `window.open(this.src)`（那会打开糊的那张）；**源文件 <180KB 服务端刻意不缩**，
  因为缩放一次要 130-240ms CPU，而本机带宽不是瓶颈。
- `video_index.json` 有**进程内缓存**（`_index_raw/_index_videos`）：任何绕过 `api_rebuild_index` 直接改这个文件的写入点，
  都必须调 `_index_invalidate()`，否则界面读到旧索引。
- 整理稿列表接口默认 `brief=1`（`content` 只给 300 字、`structured_data` 不给）：要全文走 `/api/content/summaries/<id>`；
  **面试题页不传 brief**，它要整段 `structured_data` 汇全量表——别把这一刀推广到所有调用方。
- 量性能用 `scripts/agent/perf_pages_probe.py`（读浏览器 Resource Timing）与 `scripts/agent/perf_thumb_ab.py`
  （同一页把 `thumbOf` 换成恒等函数当"改前"）。**别拿 Playwright 事件时间相减当接口耗时**，
  上一版就是这么把 110ms 量成 1528ms、差点照着假影子去优化接口。
