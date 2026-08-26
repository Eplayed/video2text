# video2text 项目现状（给 AI / Agent 的快照）

更新时间：2026-08-26
阅读顺序：本文件（现状快照）→ `README.md`（基础用法）→ `CHANGELOG_FOR_AI.md`（2026-06 历史改动与写稿流程约定）。

## 项目定位

抖音短视频素材采集器 + 自媒体文章选题索引 + 内容工作台。
核心链路：视频/文章采集 → ASR 转写 → AI 加工（标题/摘要/关键词/分类）→ SQLite 存储 → Flask 工作台管理 → Dify 知识库同步。

2026-06 之后定位进一步扩展：除抖音视频外，还接入微信公众号文章（WeWe RSS）作为素材源；工作台从"查看器"升级为"采集 + 打标 + 生成 + 发布"的一体化面板。

## 当前架构

```
main.py                  CLI 入口（采集/处理/索引更新，支持 --config、--parser-dir）
src/
  link_resolver.py       抖音链接解析（含 v.douyin.com 短链/主页分享链接）
  video_extractor.py     视频下载
  asr.py                 ASR 双引擎（本地 whisper / Whisper API），ffmpeg 抽音频
  ai_optimizer.py        AI 优化：结构化 prompt → 标题/摘要/关键词/标签 JSON
  content_store.py       SQLite 内容库（videos / ai_summaries / subscriptions）
  fetch_user_videos.py   批量获取用户视频
  wechat_fetcher.py      公众号文章抓取（WeWe RSS → 解析 → 入 videos 表，transcript=正文）【未提交】
  material_store.py      素材工作台导出（Excel/SQLite/JSONL/图片同步）【未提交】
  dify_client.py         Dify 知识库 API 客户端（文档级增量同步）【未提交】
  path_config.py         douyin_parse 解析器路径解析（env → vendor/ → /tmp 兼容）【未提交】
web/app.py               Flask 工作台后端（44 个路由，见下）
vendor/douyin_parse      内置解析器（免依赖 /tmp）
```

## 工作台（web/app.py）功能分组

- 视频管理：`/api/videos`（列表+筛选）、`/api/videos/<sheet>/<row>`（详情）、`/api/videos/delete`
- 采集：`/api/fetch_user_videos`、`/api/preview_user_videos`、`/api/fetch_and_process`（获取+写入+ASR 一键流水线）
- AI：`/api/videos/classify`（异步批量分类，`/api/classify/status` 轮询进度）、`/api/videos/category`、`/api/ai/config|test`
- 内容生成：`/api/content/generate|sync|summaries`（生成/重生成/删除 AI 摘要）
- Dify：`/api/dify/config|datasets|publish`（知识库配置、创建、发布，带 status 轮询）
- 订阅：`/api/subscriptions`（CRUD、导入、按作者同步，`/sync/status` 轮询）
- 看板：`/api/dashboard`、`/api/stats`、`/api/topics/radar`、`/api/workbench`

## 最新进展（git 时间线）

- 2026-06-12：批量采集一键完成（获取+写入+ASR 全流水线）；Cookie 记忆到 localStorage；批量获取自动去重
- 2026-06-12：分类按作者分组；视频选择 + AI 批量优化；修复 AI 优化 undefined、标题重复
- 2026-06-13：整理稿 Tab 交互增强；修复视频重复数据
- 2026-08-26：**采集入库后自动增量 AI 打标**（补齐"入库 ≠ 打标"断链，最新提交 a90ada1）

### 未提交的工作区变更（重要）

`main.py`、`src/content_store.py`、`src/fetch_user_videos.py`、`src/video_extractor.py`、`web/templates/index.html` 有修改；`src/dify_client.py`、`src/material_store.py`、`src/path_config.py`、`src/wechat_fetcher.py`、`vendor/` 为新增未跟踪。这些是公众号素材接入 + Dify 同步 + 路径内置化的一条完整功能线，接手时先确认这些文件的完成度再动代码。

## 数据与配置

- SQLite 表：`videos`（含 transcript、ai_copy、keywords、分类、Dify 同步状态）、`ai_summaries`、`subscriptions`
- `video_index.json`：文章素材索引（v1.1，含 topic / article_score / fact_risk / platform_suggestion 等选题字段）
- 敏感配置在 `config/config.env.local`（已 gitignore）：DOUYIN_SESSIONID、AI key、Dify key
- 写稿筛选约定见 `CHANGELOG_FOR_AI.md`：优先 article_score A/B、fact_risk 非空必须联网核查

## 已知注意事项

- ASR 对游戏专有名词易误识别，攻略/BD/数值类内容不能只依赖口播转写
- 异步任务（分类、Dify 发布、订阅同步）均走"启动 + status 轮询"模式，改动时保持该契约
- Excel 基础列 A-O，扩展列（选题等级/适合平台等）由索引自动识别
