# video2text 改动说明（给其他 AI / Agent）

更新时间：2026-09-27

## 2026-09-27：订阅同步提速 + ASR 崩溃修复（本轮）

用户反馈「数据与同步页同步慢」，排查出三层问题，全部修复：

- **真凶：`src/fetch_user_videos.py` 的 `load_existing_aweme_ids` 是 O(N²)**——read_only 模式下逐格调 `ws.cell(row, col)`，每次调用从流头重新解析 XML，1076 行实测 231 秒；旧同步每个作者调一次（28 订阅 ≈ 100+ 分钟纯浪费）。改为 `iter_rows` 顺序单遍扫描后 **1.0 秒**（231 倍）。教训：openpyxl read_only 模式必须用 iter_rows，禁止随机 cell 访问。
- **浏览器批量拉取**：`tools/douyin_browser_fetch.js` 新增 `users` 批量模式（一次 Chromium 跑完所有作者主页，逐作者流式 NDJSON 输出，单作者失败不断链）；`src/browser_fetch.py` 新增 `browser_fetch_users_batch()`（Popen 流式读取 + 看门狗超时）。旧路径逐作者冷启动 Chromium（5-15s/次，无新视频也要付）。28 订阅浏览器阶段从 ~7-14 分钟降到 ~3.5 分钟。
- **同步流程重构（`web/app.py` 订阅同步线程）**：① 索引重建/内容库同步/来源与分类标记/订阅状态从「逐作者执行」改为「终局统一执行一次」（每次全表重扫 2-5s）；② ASR 循环工作簿只加载一次（每条仍保存防崩溃丢转写）；③ **无新视频不再退回旧 API**——旧逻辑浏览器拉到列表但全是旧视频时仍掉进挂起 30s+ 的旧 API 路径；④ 微信 RSS 路径删掉两次无效全表重扫（Excel 未变，重建索引/内容库同步纯浪费）。
- **环境修复（不在 git 内）**：`numpy 2.0.2 → 1.26.4`。numpy 2.x 与 onnxruntime 1.16.3（faster-whisper VAD 依赖）、torch 2.2.2 不兼容，ASR 一启动即 `AttributeError: _ARRAY_API not found` 并**带崩整个 Flask 进程**。降级后 onnxruntime/torch↔numpy 全部恢复。
- 验证：28 抖音订阅 + 2 微信订阅全流程跑通，49 条新视频转写落库，订阅分类标记/game 字段/sec_uid 回填/索引重建（957 条）全部正常。已知遗留：Excel 有 66 行历史「未开始」孤儿行（63 行为此前多次中断的存量 + 3 行为本次修复前崩溃产生），会被去重逻辑跳过、不再自动转写，需人工在采集中心处理。

## 2026-09-21~24：头条图文生成链路（本轮）

给后续 Agent 的关键入口：

- `src/content_store.py`：新增 summary_type `toutiao_mix`（单/多视频决策化整合稿，固定标签六节：【标题候选】【时间线】【变化对比】【对你的影响】【行动建议】【风险核查】），出图消费方按标签解析
- `src/toutiao_graphics.py`（v3）：`generate_graphics(summary, ai_config, theme, skin, title)` —— LLM 卡片化 → playwright 渲染 1080×1920 竖版信息图 4 张 + copy_text，manifest 落 `output/toutiao/<summary_id>/manifest.json`。题材映射插画素材文件夹（`output/toutiao/<题材>图片素材/`），皮映射调色板（`_SKIN_PALETTES`）与背景（`output/toutiao/_assets/`）；LLM 失败走 `_fallback_cards` 本地模板兜底
- `web/app.py`：`/api/toutiao/generate|status|list|<id>|<id>/reveal|DELETE`，generate 接收 `summary_id/theme/skin/title`
- `web/templates/index.html`：生成弹框（标题输入框自动代入整理稿标题 + 题材/皮选择）
- `main.py` + `src/browser_fetch.py` + `tools/douyin_browser_fetch.js`：抖音 Argus 风控兜底——parser 拿不到 aweme_id 时用真实 Chromium 抓 detail；视频下载 curl 加 `--fail`/UA

改版式/配色只动 `toutiao_graphics.py` 里的 `_CSS_TEMPLATE` 与 `_SKIN_PALETTES`；加新题材在 `_THEME_ASSET_DIRS` 加一行映射。



## 项目定位变化

这个项目不只是“抖音视频转文案”，现在定位为：

抖音短视频素材采集器 + 自媒体文章选题索引。

后续写文章时，应优先读取 `video_index.json`，从索引中筛选值得二创的视频，再结合联网核查和用户账号策略生成头条号/公众号文章。

## 本次主要改动

### 1. 主程序不再强绑定 `/tmp/douyin_parse`

`main.py` 新增：

- `--parser-dir`
- `DOUYIN_PARSE_DIR` 环境变量支持
- 动态导入 `douyin_video_parser`

默认仍使用 `/tmp/douyin_parse`，但可以通过参数或环境变量改路径。

### 2. 支持读取本地配置文件

`main.py` 新增：

- `--config`
- 默认读取 `config/config.env.local`
- 支持从配置里读取 `DOUYIN_SESSIONID`
- 支持 DeepSeek / OpenAI 兼容配置

AI 参数兼容旧写法：

- `openai_api` -> `openai`
- `deepseek_api` -> `deepseek`

### 3. `--update-index` 改成真正可开关

之前 `--update-index` 默认永远开启，也没有关闭方式。

现在支持：

- `--update-index`
- `--no-update-index`

默认开启。

### 4. `video_index.json` 升级为文章素材索引

索引版本从 `1.0` 升到 `1.1`。

每条视频新增/强化字段：

- `topic`：粗分类，例如 `流放2攻略`、`暗黑4攻略`、`AI技术教程`
- `article_score`：选题等级，可能值如 `A`、`B`、`B-需核查`、`C`、`已转文章`
- `platform_suggestion`：平台建议，默认 `待判断`
- `article_angle`：文章切入角度，可从 Excel 追加列读取
- `fact_risk`：事实风险提示
- `word_doc_path`：已生成文章的 Word 路径
- `published`：发布状态
- `source_url`、`video_url`、`cover_url`
- `keywords`
- `ai_copy_exists`
- `transcript_length`
- `transcript_snippet`

写稿前应重点查看：

- `article_score`
- `fact_risk`
- `transcript_snippet`
- `source_url`
- `video_url`

### 5. 索引会自动识别 Excel 扩展列

Excel 基础列仍是 A-O。

可追加以下列，索引会自动读取：

- `选题等级`
- `适合平台`
- `文章角度`
- `事实风险`
- `Word文档路径`
- `是否已发布`

### 6. 新增候选视频列表脚本

新增：

`scripts/list_candidates.py`

用法：

```bash
python scripts/list_candidates.py --topic "流放2攻略"
python scripts/list_candidates.py --score "A,B" --limit 10
```

用途：

快速列出适合整理成文章的视频候选，不需要人工翻 Excel 或直接读完整 JSON。

### 7. `.gitignore` 补充输出忽略

新增忽略：

- `config/config.env.local`
- `output/`
- `*.docx`
- `*.mp4`
- `*.wav`

避免把视频素材、Excel、Word、Cookie/API Key 相关文件误提交。

## 后续文章生成流程建议

其他 AI / Agent 接手写文章时，建议遵守这个流程：

1. 读取 `video_index.json`
2. 优先筛选 `article_score` 为 `A` 或 `B` 的视频
3. 跳过 `status=已写文稿` 的视频，除非用户明确要求重写
4. 对 `fact_risk` 非空的视频，必须联网核查版本、机制、数值和时间
5. 根据用户账号策略决定平台：
   - 头条号：更偏痛点、争议、开服窗口、实用清单
   - 公众号：更偏沉淀、收藏、长尾搜索、完整攻略
6. 生成 Word 文档后，建议把 Excel 对应行标记为 `已写文稿`，并回填 `Word文档路径`
7. 重新运行索引更新，让 `video_index.json` 反映最新写稿状态

## 注意

ASR 对游戏专有名词容易误识别。任何攻略、BD、刷通货、BUG、收益、版本答案类内容，都不能只依赖口播转写，必须核查一手或高可信资料。
