# video2text 改动说明（给其他 AI / Agent）

更新时间：2026-09-28

## 2026-09-28（二）：成品图文卡片禁出现编辑向文字（「口播未给」类三层防线）

用户投诉包 55 的 `output/toutiao/55/img4.png` 底部 note 出现「各职业具体数值只在视频图片里，口播未给」——成品图直接发布给读者，素材缺口说明/审核提示绝不能进图。根因三层：① LLM 卡片化文案不受约束时会把缺口写进文案；② 兜底卡片硬编码「本地模板生成，请人工核对」「详见正文」等编辑向文字；③ 整合稿纪律「没有就写无」会把「无/待定」渗进卡片字段。对应三层防线：

- **prompt 层**：头条/公众号两个 `_CARDS_PROMPT` 各加「成品文案纪律」段——卡片文字是发布给读者的成品，严禁「口播未给/素材未提/待核实/请人工核对/本地模板生成/详见正文/无」等字样；信息不足时少写一条或留空字符串，提醒只能写读者视角的话。
- **兜底层**：`toutiao.py`/`wechat.py` 的 `_fallback_cards` 全部编辑向文字改读者视角或空串（如末卡 note 改「数值与机制可能随版本调整，以官方最新公告为准」；hooks 兜底「详见/整合稿正文」改「看点/详见后面几张配图」）。兜底提醒不丢：`wechat.lint_package` 在 `manifest.model=="local-template"` 时追加 warn 进 lint 面板（给编辑看，不进图）。
- **渲染前清洗层（核心）**：`src/graphics/ir.py` 新增 `sanitize_cards(cards)`，两渠道 `generate_graphics` 在取 cards 后、渲染前统一接入。规则：长文本按分句（；。！？\n）剔除命中句、保留其余；短结构字段（title/section/name/tag/icon…）只在整值命中时清空避免破版；占位值集合（无/暂无/待定/n/a…）整值丢弃；条目正文（items[].desc、hooks[].d）被整句清掉则该条目整条丢弃（避免只剩「未公布项」空壳）；note.text 清空后整块移除；内页 items 清空后整卡丢弃（至少保留一张卡，防空白图）。命中判定＝素材缺口组合正则（主语：口播/素材/原文/视频/转写/整合稿/ASR… + 谓语：未给/没提/缺失/没有…）或审核词表（请人工核对/待核实/本地模板/详见正文/占位…）。
- **既有包修复**：包 55 用新清洗器重渲染 4 张图（原包备份在 `output/toutiao/55.bak_presanitize/`，gitignore 区）——img3 的「未公布项」整条消失（4→3 条）、img4 note 只留读者向「数据随时可能调整，别拿测试服数值当结论。」，目检通过，manifest.cards 同步回写。
- 验证：`py_compile` 过；`sanitize_cards` 单测（包55真实数据 + 边界样本 + 极端全空）ALL PASS；Flask 重启后 `http://localhost:15801/media/toutiao/55/img4.png` 返回新图（HTTP 200）。
- 注意：copy_text（配文）不在清洗范围内，本次未动；若后续要求配文也禁缺口说明，需另加开关。

## 2026-09-28：公众号模板视觉升级为「信号格」（A 风格落地，仅 wechat 一套）

用户从三家族样张（信号格/纸页志/瑞士网格，见 `output/wechat/_samples/_contact-sheet.jpg`）中选定 **A 信号格**，并澄清三条管线归属：① 抖音采集→头条图文＝魔兽游戏类（不动）；② **抖音采集→公众号图文＝AI 工具/AI 科普类（信号格只应用在这里）**；③ 公众号采集→头条图文＝魔兽游戏类（不动）。故本轮只改 wechat 模板视觉，头条号两套（classic/guide/tier 等）与管线/字段/接口零改动。

- **设计来源**：pageweave（github.com/Liliane0310/pageweave）「Signal Grid」家族排版语法，走「设计期吸收」路线——只吸收排版设计（规则头/信号线/2×2 瓦片末格反白/阅读流带/左绿边线总结条/进度点页脚），不搬 112MB 字体与模板文件进仓库；自包含 CSS + 系统字体，色板仍由 `css_engine._tpl_tokens("wechat")` 注入（微信绿 #07c160 不变）。
- **`src/graphics/templates.py`**：`_CSS_WECHAT` 整体重写为信号格 CSS；`_cover_html_wechat` / `_list_html_wechat` 重写 markup 匹配新类（cover：`.rhead`+`.sig`+`h1[.long]`+`.sub`+`.tiles`（末格 `.tile.sigcell`）+`.flow`（3 step 第 1 格取卡片 `timeline`「本期看点」+ 固定 `.cta`「开始阅读 →」）+`.ftr`；list：`.rhead`+`.kick`（`SECTION %02d · {icon} {section}`）+`h2[.long]`+`.sub2`+`.rows`（`.row` 眉题用 `%02d / {tag}`）+`.note`（左绿边线 + 右绿 `NOTE →` 块）+`.ftr`）。签名/转义/long 类/`__H__` 替换/dots 惯例全保留；仍零图片依赖（`hero_uri` 入参保留但不再使用，wechat.py `no_hero` 机制不变）。`_TEMPLATES["wechat"]` label 改「公众号信号格」、hint 重写（补 timeline/icon/tag 的版面落点说明）。
- **同步改名**：`css_engine.py` 两处注释、`wechat.py` 白名单注释、`index.html` 模板下拉 label/option/`TT_TPL_PREVIEW.wechat` 三处文案，「公众号清新绿」→「公众号信号格」。
- **预览图**：`web/static/assets/tpl_previews/wechat/img1-4.jpg` 用信号格版式重生成（封面卡补 `timeline` 字段以驱动阅读流带第 1 格）。
- 验证：`py_compile` 过；预览图 4 页目检通过（无溢出/无占位符残留/无豆腐块；内页 3 条目时 `.rows` 垂直居中留呼吸感属预期）。
- 注意：`output/wechat/_samples/` 下三家族样张与 `_contact-sheet.jpg` 为选型产物，在 gitignore 区，不进 git。

## 2026-09-27（三）：公众号图文「转载模式」+ wechat 清新绿模板（去游戏背景）

用户诉求：把抖音上别人总结好的 AI 工具内容直接整理成公众号图文，**不要作者真实底稿和真实截图**；图文模板背景**不要用游戏素材**；头条号与公众号保持两套独立。开源调研结论：doocs/md（13k+ star）仅作公众号排版美学参考，无现成「AI 工具推荐卡片」组件，故自定义 wechat 模板。

- **转载/亲测双模式（`src/graphics/channels/wechat.py`）**：由 `author_draft` 是否为空自动判定 `is_repost`。转载模式＝整合稿为唯一观点源、转述口吻（禁第一人称）、免截图/底稿校验、追加转载合规尾注「本文由 AI 辅助整理归纳…观点与结论归原作者」；亲测模式＝底稿为唯一观点源、实测口吻、铁律截图≥2/底稿≥300 不变。`_CARDS_PROMPT` 用 `__SOURCE_BLOCK__` 占位按模式注入 `_SOURCE_BLOCK_REPOST`/`_SOURCE_BLOCK_TESTED`；`_fallback_cards` 转载时第5-6张改「风险核查/金句摘录」、封面副标题改「AI 辅助整理 · 内容源自公开分享」；`lint_package` 转载跳过截图/底稿校验；manifest 新增 `mode: repost|tested` 字段。
- **去游戏背景（`templates.py` + `css_engine.py`）**：新增 `wechat` 模板（公众号清新绿）——浅纸面 #f6f8f6 + 微信绿 #07c160 + 白色圆角卡片，**纯 CSS 径向渐变装饰、零背景 jpg 依赖**；`css_engine._tpl_tokens` 补 wechat 色板 token。`generate_graphics` 中 `tpl_key=="wechat"` 时强制 `hero=None`，从根上断掉 `skins._hero_uri` 兜底到游戏截图的路径（旧根因：`output/wechat/_assets/` 空时回退 `output/toutiao/_assets/neutral_bg.jpg` 魔兽截图）。
- **模板白名单**：`TEMPLATE_WHITELIST = ["wechat","minimal","classic","magazine"]`，wechat 为默认；越界回落 wechat。`minimal` 渲染同步支持无图（photo 块可选，永不破图）。
- **前端（`web/app.py` + `web/templates/index.html`）**：`/api/wechat/generate` 删除底稿必填 400、默认 template=wechat；弹窗底稿/截图 label 改「可选」+ 双模式说明，计数区留空显示「转载模式」绿字、填写转亲测计数；截图提示按模式切换（转载无需截图）；确认弹窗按模式显示不同校验与文案；模板下拉 4 选项默认 wechat，预览图 `tpl_previews/wechat/img1-4.jpg`（540×720 原生 3:4 缩半）。
- 验证：`py_compile` 全过；离线模板自检 `WECHAT_TEMPLATE_OK`（无 photo 块/无残留 token）；转载逻辑自检 `ALL_REPOST_LOGIC_OK`；重启 Flask 后端到端生成 id=54（转载、无底稿无截图）成功——6 张卡片、`mode=repost`、`lint=[]`、sources 全 template、渲染图目检零游戏背景、copy_text 为转述口吻。

## 2026-09-27（二）：视频库 → 素材库，微信文章并入统一素材池（本轮）

用户反馈「微信 RSS 采集的文章在视频库没有出口」。设计先行（架构/UI/UX/工作流），三项决策：导航改名「素材库」、运行时 DB 合并（不并入索引）、允许抖音+微信混选生成整理稿。

- **架构**：DB（`output/video2text.db` videos 表，`source_sheet='微信文章'`）是微信文章唯一权威源；`/api/videos` 运行时合并「索引抖音行 + DB 微信行」，**不并入 video_index.json**——`update_video_index` 是全重建机制，并入会在每次索引重建时丢微信行。现架构下索引重建/内容库同步永不影响微信文章。
- **后端（`web/app.py`）**：`/api/videos` 支持 `?source=douyin|wechat` 筛选；微信行补齐 transcript_length / cover_url（正文首图兜底 `_first_markdown_image`，入库不存封面）/ create_time / source 字段；`sources` 计数取筛选前合并口径（与卡片显示一致）。详情 API 新增微信分支：走 DB、响应与抖音**同构**（asr=正文、keyframes=[]、video_url=原文链接、source 标识），前端弹窗零特判；整理稿挂载逻辑抽为 `_attach_related_summaries` 公共函数两来源共用。删除 API：微信行 DB 删除即计数（无 Excel 行可清，清行循环遇不到该 sheet 自动跳过）。`_video_keyframe_markdown` 显式跳过微信行——其 aweme_id 是 URL hash，不跳过会误走「在线解析下载 + ffmpeg 抽帧」白费时间。
- **前端（`web/templates/index.html`）**：全局「视频库」→「素材库」；工具栏新增来源药丸（全部/抖音/微信带计数，复用 cat-pill 样式），切换来源时清空勾选（防跨来源残留误生成）；卡片来源徽章（微信绿 #07c160 / 抖音蓝 var(--module-2)），微信无封面时占位版式（book 图标 + 品牌绿渐变），无内容文案区分「无正文」/「无转写」；详情弹窗微信版式（「文章正文」替代 ASR、隐藏 AI 优化文案区、操作改「打开原文/复制正文/删除」），`resetModal` 统一恢复标题与区块显示，抖音/微信互开无残留。
- **生成链路零改动兼容**：素材 key `微信文章:<row>` 由前端原样透传 `/api/content/generate`，线程内 `key.split(":")` → `get_video_by_source` 纯 DB 查询命中微信记录，transcript=正文通过非空校验；`sync_excel_to_db` 只按 Excel 工作表 upsert，不会触碰/复活微信行。
- **孤儿行处置（上轮遗留）**：66 行「未开始」中仅 3 行属今日，已全部补转写成功——R1076 苏丹你好（2900 字）/ R1077 小W玩AI（134 字）/ R1078 码上有面（992 字）；索引重建 962 条、订阅标记、AI 打标 361 条完成。其余 63 行历史存量按用户决定**不补**。
- 验证：`?source=wechat` 返回 2 条微信文章（含 3678 字正文、原文链接）；合并列表 962 条（960 抖音 + 2 微信）；微信详情不再 404；抖音详情（R1078）回归正常；药丸计数与卡片口径一致。

## 2026-09-27：订阅同步提速 + ASR 崩溃修复

用户反馈「数据与同步页同步慢」，排查出三层问题，全部修复：

- **真凶：`src/fetch_user_videos.py` 的 `load_existing_aweme_ids` 是 O(N²)**——read_only 模式下逐格调 `ws.cell(row, col)`，每次调用从流头重新解析 XML，1076 行实测 231 秒；旧同步每个作者调一次（28 订阅 ≈ 100+ 分钟纯浪费）。改为 `iter_rows` 顺序单遍扫描后 **1.0 秒**（231 倍）。教训：openpyxl read_only 模式必须用 iter_rows，禁止随机 cell 访问。
- **浏览器批量拉取**：`tools/douyin_browser_fetch.js` 新增 `users` 批量模式（一次 Chromium 跑完所有作者主页，逐作者流式 NDJSON 输出，单作者失败不断链）；`src/browser_fetch.py` 新增 `browser_fetch_users_batch()`（Popen 流式读取 + 看门狗超时）。旧路径逐作者冷启动 Chromium（5-15s/次，无新视频也要付）。28 订阅浏览器阶段从 ~7-14 分钟降到 ~3.5 分钟。
- **同步流程重构（`web/app.py` 订阅同步线程）**：① 索引重建/内容库同步/来源与分类标记/订阅状态从「逐作者执行」改为「终局统一执行一次」（每次全表重扫 2-5s）；② ASR 循环工作簿只加载一次（每条仍保存防崩溃丢转写）；③ **无新视频不再退回旧 API**——旧逻辑浏览器拉到列表但全是旧视频时仍掉进挂起 30s+ 的旧 API 路径；④ 微信 RSS 路径删掉两次无效全表重扫（Excel 未变，重建索引/内容库同步纯浪费）。
- **环境修复（不在 git 内）**：`numpy 2.0.2 → 1.26.4`。numpy 2.x 与 onnxruntime 1.16.3（faster-whisper VAD 依赖）、torch 2.2.2 不兼容，ASR 一启动即 `AttributeError: _ARRAY_API not found` 并**带崩整个 Flask 进程**。降级后 onnxruntime/torch↔numpy 全部恢复。
- 验证：28 抖音订阅 + 2 微信订阅全流程跑通，49 条新视频转写落库，订阅分类标记/game 字段/sec_uid 回填/索引重建（957 条）全部正常。已知遗留：Excel 有 66 行历史「未开始」孤儿行（63 行为此前多次中断的存量 + 3 行为本次修复前崩溃产生），会被去重逻辑跳过、不再自动转写，需人工在采集中心处理。（已处置：3 行今日孤儿已补转写成功，63 行历史存量按用户决定不补，见上方「素材库」条目）

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
