# video2text 项目现状（给 AI / Agent 的快照）

更新时间：2026-09-01
阅读顺序：本文件（现状快照）→ `README.md`（基础用法）→ `CHANGELOG_FOR_AI.md`（2026-06 历史改动与写稿流程约定）。
双机协作/换机流程见 media-workbench 仓库 `docs/DEV-SYNC.md`（跨三仓库的权威文档）。

## 项目定位

抖音短视频素材采集器 + 自媒体文章选题索引 + 内容工作台。
核心链路：视频/文章采集 → ASR 转写 → AI 加工（标题/摘要/关键词/分类）→ SQLite 存储 → Flask 工作台管理 → Dify 知识库同步。

2026-06 之后定位进一步扩展：除抖音视频外，还接入微信公众号文章（WeWe RSS）作为素材源；工作台从"查看器"升级为"采集 + 打标 + 生成 + 发布"的一体化面板。

2026-08 之后新增职责：**选题/渠道策略的唯一权威源**——`content_store.RADAR_CHANNEL_STRATEGY` 集中管理头条号/公众号/小红书的选题策略（7:2:1 配比、止损线、排除词），通过 `/api/strategy/channels` 下发给 media-workbench 前端策略引擎（channel-strategy.js），单一权威源、两处消费。

## 当前架构

```
main.py                  CLI 入口（采集/处理/索引更新，支持 --config、--parser-dir；update_video_index 带备份+原子写入）
src/
  video_extractor.py     视频下载
  content_store.py       SQLite 内容库（videos/ai_summaries/subscriptions）+ AI 加工（_llm_chat）+ 策略权威源（RADAR_CHANNEL_STRATEGY/CHANNEL_STRATEGY_GLOBAL）+ 选题雷达聚合 + 分类
  fetch_user_videos.py   批量获取用户视频
  wechat_fetcher.py      公众号文章抓取（WeWe RSS → 解析 → 入 videos 表，transcript=正文）
  material_store.py      素材工作台导出（Excel/SQLite/JSONL/图片同步）+ 关键帧路径
  dify_client.py         Dify 知识库 API 客户端（文档级增量同步）
  path_config.py         douyin_parse 解析器路径解析（env → vendor/ → /tmp 兼容）
web/app.py               Flask 工作台后端（45 个路由，见下）
vendor/douyin_parse      内置解析器（免依赖 /tmp）
```

注：真实 ASR 在 `main.py`（whisper 双引擎，ffmpeg 抽音频），链接解析在 `main.py` + vendor 解析器；2026-09-01 已删除零引用的历史遗留模块 `src/asr.py`、`src/ai_optimizer.py`、`src/link_resolver.py`（功能早已由 main.py/content_store.py 接管）。

## 工作台（web/app.py）功能分组

- 视频管理：`/api/videos`（列表+筛选+搜索）、`/api/videos/<sheet>/<row>`（详情）、`/api/videos/delete`
- 采集：`/api/fetch_user_videos`、`/api/preview_user_videos`、`/api/fetch_and_process`（获取+写入+ASR 一键流水线，完成后自动增量 AI 打标）
- AI：`/api/videos/classify`（异步批量分类，`/api/classify/status` 轮询；增量=只补 category/ai_tags 任一为空）、`/api/videos/category`、`/api/ai/config|test`
- 内容生成：`/api/content/generate|sync|summaries`（生成/重生成/删除 AI 摘要；类型含 game_guide/wechat_material/ai_interview）
- 策略下发：`/api/strategy/channels`（渠道策略配置，media-workbench 拉取）
- Dify：`/api/dify/config|datasets|publish`（知识库配置、创建、发布，带 status 轮询）
- 订阅：`/api/subscriptions`（CRUD、导入、按 ids 批量同步，`/sync/status` 轮询）
- 看板：`/api/dashboard`、`/api/stats`、`/api/topics/radar`（支持 channel 渠道策略参数）、`/api/workbench`

## 最新进展（git 时间线）

- 2026-06-12~13：批量采集一键流水线；Cookie 记忆；分类分组；整理稿 Tab 交互；视频去重修复
- 2026-08-26：采集入库后自动增量 AI 打标（a90ada1）
- 2026-08-31（86cb727~f8100f5）：公众号素材接入（wechat_material 生成 + 关键帧配图）、Dify 知识库同步、素材导出、订阅按 ids 批量同步 + 分类筛选、选题雷达 Top10/展开收起/时间排序、**渠道策略权威源**（RADAR_CHANNEL_STRATEGY + /api/strategy/channels 下发）、AI 批量分类筛选修复（category 或 ai_tags 任一为空即补）
- 2026-09-01（9fa078d~faa7be7）：修复链接采集 NameError（_auto_classify_after_sync 函数本体补齐：AI 未配置静默跳过、失败不阻塞采集）；索引安全加固（update_video_index 备份 .bak + 原子写入）；删除废文件；删除零引用死代码三件套；本文档同步真实化

## 数据与配置

- SQLite 表：`videos`（含 transcript、ai_copy、keywords、分类 category/ai_tags、Dify 同步状态）、`ai_summaries`、`subscriptions`
- `video_index.json`：文章素材索引（v1.1，含 topic/article_score/fact_risk/summary/published/performance/resultScore）。**已被 gitignore（`*.json`），换机迁移见 DEV-SYNC.md 数据清单**；update_video_index 每次写入前自动备份 `.bak`
- Excel（output/抖音视频信息.xlsx）是采集数据的真相源，含"是否已发布/处理状态"列供回写
- 敏感配置在 `config/config.env.local`（已 gitignore）：DOUYIN_SESSIONID、AI key、Dify key
- 写稿筛选约定见 `CHANGELOG_FOR_AI.md`：优先 article_score A/B、fact_risk 非空必须联网核查

## 已知注意事项

- ASR 对游戏专有名词易误识别，攻略/BD/数值类内容不能只依赖口播转写
- 异步任务（分类、Dify 发布、订阅同步）均走"启动 + status 轮询"模式，改动时保持该契约
- Excel 基础列 A-O，扩展列（选题等级/适合平台等）由索引自动识别
- 运行中的 web 进程无热重载，改完代码必须重启（15801 端口）
- 遗留工程债（不阻塞，改采集代码时留意）：web/app.py 三处采集流水线（链接采集/批量获取/订阅同步 ≈531/716/1603 行附近）为逐行复制结构，且传给 process_row 的 ai_config 硬编码 skip（AI 打标实际由流程末尾的 _auto_classify_after_sync 兜底）——重构需抽公共函数，动前先验证三条链路
- 无 requirements.txt / venv：依赖装在系统 Python 3.9，换机需手动装 flask/openpyxl 等
