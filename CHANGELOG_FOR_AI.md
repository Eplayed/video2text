# video2text 改动说明（给其他 AI / Agent）

更新时间：2026-09-24

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
