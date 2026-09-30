# video2text 项目现状（给 AI / Agent 的快照）

更新时间：2026-09-30
阅读顺序：本文件（现状快照）→ `README.md`（基础用法）→ `CHANGELOG_FOR_AI.md`（2026-06 历史改动与写稿流程约定）。
双机协作/换机流程见 media-workbench 仓库 `docs/DEV-SYNC.md`（跨三仓库的权威文档）。

## 项目定位

抖音短视频素材采集器 + 自媒体文章选题索引 + 内容工作台。
核心链路：视频/文章采集 → ASR 转写 → AI 加工（标题/摘要/关键词/分类）→ SQLite 存储 → Flask 工作台管理 → 图文出图（头条/公众号渠道）。

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
  path_config.py         douyin_parse 解析器路径解析（env → vendor/ → /tmp 兼容）
web/app.py               Flask 工作台后端（64 个路由，见下）
src/graphics/            图文渲染内核（Phase 1 抽出）：templates（模板 CSS+渲染）· variants（配色/字体/版式三轴）· skins（皮肤/题材素材）· css_engine（__TOKEN__ 替换）· renderer（Playwright 截图）· package（manifest）· ir（卡片 schema）· channels/{toutiao,wechat}.py（渠道适配器）
scripts/                 list_candidates（候选查询）· check_variant_tokens（三轴静态检查）· e2e_toutiao_variants / e2e_wechat_variants（图文 e2e，2026-09-30 从 Trae 工作区收编入仓）
.qoder/skills/           Qoder 项目技能：workbench-restart（安全重启+双确认）· graphics-verify（三线回归）
vendor/douyin_parse      内置解析器（免依赖 /tmp）
```

注：真实 ASR 在 `main.py`（whisper 双引擎，ffmpeg 抽音频），链接解析在 `main.py` + vendor 解析器；2026-09-01 已删除零引用的历史遗留模块 `src/asr.py`、`src/ai_optimizer.py`、`src/link_resolver.py`（功能早已由 main.py/content_store.py 接管）。

## 工作台（web/app.py）功能分组

- 视频管理：`/api/videos`（列表+筛选+搜索）、`/api/videos/<sheet>/<row>`（详情）、`/api/videos/delete`
- 采集：`/api/fetch_user_videos`、`/api/preview_user_videos`、`/api/fetch_and_process`（获取+写入+ASR 一键流水线，完成后自动增量 AI 打标）
- AI：`/api/videos/classify`（异步批量分类，`/api/classify/status` 轮询；增量=只补 category/ai_tags 任一为空）、`/api/videos/category`、`/api/ai/config|test`
- 内容生成：`/api/content/generate|sync|summaries`（生成/重生成/删除 AI 摘要；类型含 game_guide/wechat_material/ai_interview）
- 策略下发：`/api/strategy/channels`（渠道策略配置，media-workbench 拉取）
- 订阅：`/api/subscriptions`（CRUD、导入、按 ids 批量同步，`/sync/status` 轮询；分类与标签支持批量设置）
- 看板：`/api/dashboard`、`/api/stats`、`/api/topics/radar`（支持 channel 渠道策略参数）、`/api/workbench`

## 最新进展（git 时间线）

- 2026-06-12~13：批量采集一键流水线；Cookie 记忆；分类分组；整理稿 Tab 交互；视频去重修复
- 2026-08-26：采集入库后自动增量 AI 打标（a90ada1）
- 2026-08-31（86cb727~f8100f5）：公众号素材接入（wechat_material 生成 + 关键帧配图）、Dify 知识库同步、素材导出、订阅按 ids 批量同步 + 分类筛选、选题雷达 Top10/展开收起/时间排序、**渠道策略权威源**（RADAR_CHANNEL_STRATEGY + /api/strategy/channels 下发）、AI 批量分类筛选修复（category 或 ai_tags 任一为空即补）
- 2026-09-01（9fa078d~faa7be7）：修复链接采集 NameError（_auto_classify_after_sync 函数本体补齐：AI 未配置静默跳过、失败不阻塞采集）；索引安全加固（update_video_index 备份 .bak + 原子写入）；删除废文件；删除零引用死代码三件套；本文档同步真实化
- 2026-09-02（def1e7c 等）：头条策略排除分类补「汽车资讯」（10→11 类）+ 策略下发版本号 v2；「数据与同步」页重构——移除 grid-materials 卡片网格，改为 订阅管理 → 同步按钮 → 5 统计卡片（总素材/有ASR/本地封面/关键帧/已生成内容），loadWorkbench 精简
- 2026-09-10：项目整体接入周一体检自动化（media-workbench 定时任务，只读检查本仓库 15801 端口服务与 git 状态）
- 2026-09-14：**SQLite 权威源第一步（实体标识）**——videos 表新增 `author_sec_uid`/`source` 列（Excel 重同步不覆盖）：订阅同步按行号回写 sec_uid，启动时按订阅作者名幂等回填存量（737 条）；订阅导入改 SQLite 直查 sec_uid（URL 反查降为兜底）。**索引实体键重构**——video_index 以 aweme_id 为实体键（缺 id 旧条目退回 sheet:row），重建只保留本次扫描到的实体，Excel 清行（删除视频）自动出索引，published/performance 按实体键继承。**采集链修复**——resolve_url 由 HEAD 改 GET(stream)（抖音 CDN 对 HEAD 返回 404/超时导致短链解析失败，Row 834 实测），parser 自带重定向解析作双保险；采集失败原因从 Excel 备注列带回前端

- 2026-09-21~24：**头条图文生成链路**——①采集兜底：抖音 Argus 风控拦截纯 API 签名，parser 解析不到 aweme_id 时改用真实 Chromium 打开视频页抓 detail（src/browser_fetch.py + tools/douyin_browser_fetch.js），视频下载 curl 加 --fail/UA 防空文件；②整合稿：content_store 新增 toutiao_mix 类型（单视频/多视频决策化整合，【标题候选/时间线/变化对比/对你的影响/行动建议/风险核查】固定标签六节结构）；③出图：src/toutiao_graphics.py v3——LLM 卡片化整合稿 → playwright 渲染 9:16（1080×1920）竖版信息图 4 张 + 微头条文案，版式参照 2026-09-24 参考图（金渐变衬线大标题/插画主视觉底缘渐隐/圆环图标三面板/双金边横幅，无页码无来源行）；题材+皮双选择：题材决定 LLM 文案点明的游戏与插画素材文件夹（output/toutiao/<题材>图片素材/，更新素材下次生成即生效），皮决定整套调色板与背景（wow 蓝黑金/d4 烬红/poe 青铜/poe2 墨玉绿/自定义 _assets/<名称>_bg.jpg），插画封面取竖构图、内容页取横构图按页轮换，LLM 不可用走本地模板兜底；④前端：生成弹框新增标题输入框（自动代入整理稿标题，可改可留空），超 12 字标题自动降字号防截断
- 2026-09-25~29：**Phase 1 内核抽取 + 图文三轴**——`src/graphics/` 从 `toutiao_graphics.py` 抽出渠道无关内核（templates/variants/skins/css_engine/renderer/package/ir），`channels/{toutiao,wechat}.py` 两渠道共用同一渲染内核；公众号模板库扩到 6 套（新增 lilac_list 浅紫清单 / cream_gold 奶油金插画，含参考样本拆解文档）；**变体三轴（配色/字体/版式）两渠道全部接线**，manifest 落真实键；头条素材选图支持随机（rng/exclude + 可复现种子 + manifest.assets 追溯）；`scripts/check_variant_tokens.py` 代码化防线（字体/配色轴失效即报错，因三轮「选了不生效」静默失效复盘而生，守则见 `.agents/memories/40-graphics-variants.md`）
- 2026-09-30：**Qoder 接手准备**——两条图文 e2e（18+14 用例）从 Trae 私有工作区收编进 `scripts/`；新增 `.qoder/skills/workbench-restart`（按端口 PID 安全重启 + py_compile 前置 + PID/接口双确认）与 `.qoder/skills/graphics-verify`（三线回归一条命令）；AGENTS.md 补纪律第 5 条（改 graphics 先读 40 守则、改完跑验证）；`requirements.txt` 依赖钉版；README 项目结构纠偏（已删模块不再列）。业务行为零改动，三线回归 `ALL_OK`，重启实跑 72113→23054
- 2026-09-30（同日二、三轮）：**订阅页三维分组**——`subscriptions` 新增自定义 `tags` 列（与固定 11 类 `category` 正交：category 喂选题雷达/渠道策略，tags 只做本地分组），来源/分类/标签三维筛选 + 勾选批量设分类/设标签 + 「同步当前筛选」一键整组同步；分类端点由单 id 改批量 `{ids}`。**Dify 知识库同步整体下线**——删 `src/dify_client.py`、6 个 `/api/dify/*` 路由、设置页 RAG 面板与「发布到知识库」按钮（取证：1220 行素材 `dify_document_id` 全为 NULL，一条都没发布过，且 Dify 侧写稿质量不达标）；`videos` 两列按约定保留不删。图文回归三条线 `ALL_OK`，`wechat_material` 固定【标签】分节经实测仍被本地兜底模板正常消费

## 运营协作现状（2026-09）

本仓库是三仓库自媒体产线的**素材层**，生产层见 `自媒体/_content_factory`，运营层见 `media-workbench`。当前公众号走「老张码上记」AI 科技赛道（周二更：周一科普/周四战地日记），头条处于修复期（3天×2篇），选题雷达支持 channel 参数按渠道出题。

## 数据与配置

- SQLite 表：`videos`（含 transcript、ai_copy、keywords、分类 category/ai_tags、实体标识 author_sec_uid/source；另有 `dify_document_id`/`dify_synced_at` 两列随 Dify 下线保留但不再读写，全表均为 NULL）、`ai_summaries`、`subscriptions`（含自定义分组 tags）
- `video_index.json`：文章素材索引（v1.1，含 topic/article_score/fact_risk/summary/published/performance/resultScore）。**实体键口径（2026-09-14 起）**：aweme_id 为主键（缺 id 旧条目退回 sheet:row），重建只保留本次扫描到的实体，Excel 清行自动出索引。**已被 gitignore（`*.json`），换机迁移见 DEV-SYNC.md 数据清单**；update_video_index 每次写入前自动备份 `.bak`
- Excel（output/抖音视频信息.xlsx）是采集数据的真相源，含"是否已发布/处理状态"列供回写；`author_sec_uid`/`source` 为 SQLite 专属字段，Excel 重同步不覆盖
- 敏感配置在 `config/config.env.local`（已 gitignore）：DOUYIN_SESSIONID、AI key（`DIFY_*` 键随 Dify 下线已不再写入，本机配置里本就没有）
- 写稿筛选约定见 `CHANGELOG_FOR_AI.md`：优先 article_score A/B、fact_risk 非空必须联网核查

## 已知注意事项

- ASR 对游戏专有名词易误识别，攻略/BD/数值类内容不能只依赖口播转写
- 异步任务（分类、订阅同步）均走"启动 + status 轮询"模式，改动时保持该契约
- Excel 基础列 A-O，扩展列（选题等级/适合平台等）由索引自动识别
- 运行中的 web 进程无热重载，改完代码必须重启（15801 端口）
- 遗留工程债（不阻塞，改采集代码时留意）：web/app.py 三条采集流水线为逐行复制结构，实际位置（2026-09-30 复核，旧文档记的 531/716/1603 已过期）——链接采集 `api_process` 641–741、批量获取 `api_fetch_and_process` 838–967、订阅同步 `api_subscriptions_sync` 2117–2369。三者**不是简单同构**，抽公共层前必须保住这些差异：Excel 打开粒度（P1/P2 每行 load+save，P3 单工作簿循环内多次 save + finally close）、写入列（P1 只写 col1/2，P2/P3 还写 col3 aweme_id，P3 另读 col4 作者回填）、失败处理（P1/P2 回读第 15 列备注累加 error，P3 不看 `ok` 只写订阅 summary）、状态契约（P1/P2 共用 `_task_status`+`/api/status`，P2 多 total/current 且前端依赖；P3 用 `_sub_status`+`/api/subscriptions/sync/status`；冲突码 400 vs 409、返回结构也不同）、`mark_video_source` 来源值（single_link vs subscription）。**_auto_classify_after_sync（327–344）现三条流水线都在流程末尾调用**（P1 724 / P2 950 / P3 2356；P3 原先只挂在抖音分支内，只勾微信订阅同步时新文章永不打标，2026-09-30 已移到两条分支之外）。P3 另有 2318–2344 的裸 SQL 按订阅预设回填 category/game，与增量打标并存（谁优先由 `content_store.classify_videos` 的 `force=False` 护栏决定：已有 category 不改写，只补缺）。传给 process_row 的 ai_config 三条都硬编码 skip。
- 依赖钉版见 `requirements.txt`（2026-09-30 新增，按系统 Python 3.9 实测版本；无 venv，ffmpeg / playwright 浏览器内核 / vendor 解析器仍需换机手动准备）
