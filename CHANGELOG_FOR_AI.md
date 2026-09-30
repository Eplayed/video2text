# video2text 改动说明（给其他 AI / Agent）

更新时间：2026-09-30

## 2026-09-30（补十）：海报支持"一次出多张、人工挑一张"

用户诉求：怕单张出图效果不稳。AI 出图天然有方差，**并行出几张挑最好的**比"出一张不满意再重出"更省时间也更可控。

- `generate_poster(..., copies=1..3)`：一次出 N 张存为 `poster_1.png…poster_N.png`，每张**各自跑回读校验**；`pick_best()` 按「校验通过 → 缺字少 → 出图顺序」自动选一张复制成 `poster.png` 作默认，所以列表页、`manifest.url`、发布取图逻辑**全部不用改**。
- 新增 `choose_candidate()` + `POST /api/poster/<id>/pick {index}`：人工改选时把该张复制成 `poster.png`，并**用新那张的校验结果重算 lint**（选到有缺字的图，error 会立刻回到清单里）。
- `manifest` 新增 `candidates`（每张的文件/URL/校验结果/字节数）、`chosen`、`copies`。
- 前端：弹窗加「出 1 张 / 出 3 张挑 1 张」下拉（确认框会按张数报预计花费与耗时）；结果区多张时并排显示缩略图，**点一下即选定**，卡片上直接标「字全对 / 有缺字 / 未校验」。
- 限流提醒：模型 RPM=2，出 3 张会走内置 31s 节流，约 2-3 分钟、1.5 元。
- 验证：打桩跑 3 张（其中第 2 张故意缺字）→ 自动选中第 1 张、`poster.png` 落位、改选第 3 张与越界编号均正确；浏览器用假候选包实测三张缩略图渲染、点击换选后 `poster.png` 与所选文件字节一致。**真实出 3 张未花钱实测**。


## 2026-09-30（补九）：海报拆解"太散"的根因是素材被 110 字硬切，prompt 补结构纪律

用户反馈：素材本来就是别人总结过的，海报拆解反而把它拆得更散、总结得不对。实测定位到**两层叠加**原因。

- **根因 A（主因，在素材层不在海报）**：整合稿 61 的底层 ASR **454 字、标点数为 0**（本地 whisper 输出不带标点）。`content_store._sentences()` 按 `[。！？!?；;\n]` 切不出句，于是走兜底 `cleaned[i:i+110]`——**不看语义每 110 字硬切一刀**，实测切出 `[110,110,110,110,14]` 五段，段与段之间从词中间断开（第1段止于「外加做极换话」、第2段起于「玩具但没有测试资格」；「95块但是」被劈成两半）。规则版骨架的【攻略要点】就是这五片，海报拆解拿到的输入**已经是断的**，只能一片一卡。**本轮未改 `_sentences`**：该文件正被并发编辑，且正解是重跑整合稿而不是给切分器打补丁。
- **根因 B（放大器，在海报 prompt）**：`_PLAN_PROMPT` 原来只管字数与张数，**没有任何一条要求卡片之间构成主线**，也没禁止按原文顺序逐段摘要，模型最省力就是顺素材一段一个标签。
- 修法（B）：prompt 拆成三条铁律，新增**结构纪律**——卡片必须按「结论 → 依据/算账 → 坑或风险 → 怎么选」递进、每张要能回答"所以呢"、禁止逐段摘要与两张同义；并明确"素材是残句时要跨段把一句话说完整"。新增 `spine` 字段（≤24 字，**不画进海报、不计 80 字预算**），在弹窗顶部显示，让人一眼判断这条主线成不成链。另补一条：价格/天数/日期保持阿拉伯数字（实测模型会写成「三八八」「二一八」）。
- **实测对比（同一条 61 号稿）**：改前＝差价账 / 时间坑 / 数据清 / 怎么选（四张并列，"时间坑"和"数据清"其实是同一条坑的两半）；改后＝**结论：本体不用买礼包 → 算账：多花 95 → 两个坑 → 怎么选**，把原来拆成两张的坑合并，腾出的位置放回了原文最核心的判断（此前被埋在碎片里丢了）。73 字未触发压缩。
- 验证：spine 不入图 prompt、不计字数、不参与 publish 预算（离线断言）；`node --check` 与 `py_compile` 过；服务已重启接管。**阿拉伯数字那条未再跑真实拆解复核**（省一次调用），下次点击时留意。


## 2026-09-30（补八）：海报线骨架稿门禁改「默认拦 + 人工复核后放行」，并把检查提到拆解之前

用户在弹窗里点「② 出图并校验」被 `status=draft` 门禁拦下，但**① 拆解早已花掉 3 分钟并产出了一份可用文案**——检查时机与严格度都不对。

- 取证：被拦的是整合稿 61（`toutiao_mix`，今天 11:42 由 `Request timed out.` 退回规则版）。它的底层 ASR 有 454 字、**价格与时间线数字全是真的**（388/218/177/95 元、11月5日开服、10月6—27日、开服清数据），模型从中提出的 67 字文案质量没问题。
- 结论：**海报线与 HTML 卡片线不该共用同一档严格度**。卡片线的 `_fallback_cards` 是机械切片、中间没有 LLM 判断，骨架稿必出垃圾；海报线永远有一次 LLM 提炼在中间，骨架稿只要转写有料就能出好文案。
- 改法：① 拆解**不再拦**，只把 `draft_warning` 随结果回给前端明示风险；② 出图默认仍拦，但被拦时前端给出「仍然出图（我已人工复核文案）」按钮，走 `force=true` 显式放行；③ 放行后 `manifest.draft_override=true` 且 `lint` 首条固定写 warn 提醒核对数字与时间线，事后可追溯。
- **HTML 卡片线（头条/公众号）保持硬拦、无 force 出口**，理由同上；要给它也开放行口需另做决策。
- 验证：离线打桩确认 `force=False` 抛错、`force=True` 放行且 `draft_override`/warn 落位；`py_compile` 与 `node --check` 过；服务已重启接管。真实放行出图那一路**未花钱实测**，留给用户点击验证。


## 2026-09-30（补七）：海报超预算改为确定性自动压缩，不再整单判死

补六的 80 字硬门禁在真实链路上第一次跑就把自己拦住了：`qwen3.8-max` 拆出 **90 字 / 94 字** 是常态，直接 `PosterError` 等于让用户白等一次 3 分钟拆解再赌一把。

- 新增 `fit_plan(plan)`：超预算时**不再调模型**，按价值高低确定性收敛——先把各字段各自裁到单条上限（不依赖调用方已裁过），再**砍末位卡片**（保底 `CARD_MIN`）→ 截最长说明 → 削副标题与尾注；主标题与卡片标题最后动。收敛不了才交回 violations 报错。
- `build_text_plan` 返回值扩为 `(plan, publish, violations, notes)`，`notes` 记录动了哪几处，进 `manifest.auto_notes` 与拆解进度文案，提示「发布前请复核文案是否还完整」。
- **前端手改的文案不自动压缩**：那是用户显式输入，超线就明确报错让他自己删（与「用户显式输入要有代码级强制」同源）。
- 真实端到端复跑 `scripts/e2e_ai_poster.py 58`：模型给 94 字 → 自动压到 **67 字 / 3 张卡片** → 出图 → 回读逐字一致 → `ALL_OK`，耗时 209 秒。发布包字段首次拿到真实产出：配文按「钩子 + 5 条要点」成形并自动追加合规尾注、摘要 118 字、关键词回复为空（模型判断素材无可推词，符合指令）、来源说明带口径日期、checklist 7 条、lint 无告警。
- **已知副作用**：自动压缩只动图面文案，配文与摘要仍是全量的，所以砍卡片后可能出现「配文讲五步、图上只剩三步」。`auto_notes` 就是为这个提示留的，看到它必须复核。
- 同日还修了海报弹窗样式（见补六末段）：`.modal-box` 是按 overlay id 作用域写的，漏了 `#pk-overlay` 导致弹窗裸奔。

**结论更正**：补六里写的「怀疑 6 次出图打光额度」是错的——约 40 分钟后 `qwen3.8-max` 与 `qwen-image-max` 自行恢复 200，那次 403 `AccessDenied.Unpurchased` 是**暂时抖动**。撞 403 先等几分钟重试再下结论。


## 2026-09-30（补六）：海报产线补发布包字段 + 上游去污染 + 实测出限流与额度两个真实约束

**1. 上游不再把错误串写进整合稿正文。** 补五只在下游拦，本轮改源头：`content_store.generate_summary` / `generate_collection_summary` 的 AI 失败分支不再 `content += "[AI生成失败…]"`，改为把原因写进 `structured_data.fallback_error`（正文是素材，不该混日志）。`gate.draft_blockers` 同时升级为**以 `status` 为主判据**（AI 成功落 `ai`、失败或未配置落 `draft`；实测全库 51 个 ai / 3 个 draft，3 个 draft 正好就是全部污染行），正文指纹降为第二层网，专门兜历史污染稿。e2e 的假整合稿不带 status 字段，因此不受影响（已实测）。

**2. 海报补发布包字段。** `build_text_plan` 现在返回 `(plan, publish, violations)`：`plan` 是要画进图里的文字（仍过 80 字硬门禁），`publish` 是发布页用的配文/摘要/关键词回复/来源说明（只做软校验，进 `manifest.lint`）。`manifest` 新增 `publish`、`copy_text`（自动追加合规尾注）、`digest`、`keyword_reply`、`source_note`、`checklist`（7 条平台侧人工动作）、`lint`、`cost_yuan_estimate`。前端弹窗加「发布页文案」区与「复制发布文案」按钮。修了一个自造 bug：`_clip` 会删掉所有空格，英文关键词「回复 Agent 领取」会被粘成「回复Agent领取」，发布文案改用 `_clip_ws` 只折叠不删除。

**3. 实测出两条硬约束（都写进代码了）。**
- **`qwen-image-max` 官方 0.5 元/张、限流 RPM=2**（阿里云百炼模型页）。稳定率探针 `scripts/probe_poster_stability.py` 连发 5 张：4 张逐字全对（累计约 316 字零错字），**第 5 张撞 `Requests rate limit exceeded`**。所以 `generate_image` 加了相邻请求 ≥31s 节流 + 限流退避重试（最多 3 次）。结论：**中文文字渲染稳定率目前是 4/4，真正的风险是限流而不是错字**；批量 20 张要约 10 分钟。
- **会话中途该 key 全线 403 `AccessDenied.Unpurchased`**（`qwen3.8-max` / `qwen-plus` / `deepseek-v4-flash` / `qwen3-vl-flash` / `qwen-image-max` 无一例外，而出图模型 20 分钟前还可用）。聚合站没有余额或账单接口（`/dashboard/billing/*`、`/api/v1/user/balance` 全 404），无法自查剩余额度。**高度怀疑是那 6 次出图（约 3 元）打光了小额试用额度**，也可能是平台侧授权策略变更。因此本轮**第 2 条的端到端真实链路未能验证**，改用离线打桩（stub `generate_image`/`verify_image`）验完 manifest 组装、合规尾注追加、软校验与门禁拒绝；额度恢复后需重跑 `python3 scripts/e2e_ai_poster.py 58` 补一次真实验证。

**验证**：`py_compile` 全过；JS `node --check` 过；`graphics-verify` 三条线 ALL_OK（`gate.py` 改过）；门禁 5 项离线判定（含 status 主判据与指纹兜底各一条负向）全对；发布字段离线打桩验证通过。`content_store.py` 采用 hunk 级暂存提交，未夹带并发的 `classify_videos` 改动。

**补：海报弹窗样式回归（同日用户实测报出）。** 本文件里的弹窗样式是**按 overlay id 作用域**写的（`#tt-gen-overlay .modal-box`、`#wx-gen-overlay .modal-box`），新增 `#pk-overlay` 时漏了对应规则，导致海报弹窗退化成无背景、无内边距、不滚动的裸容器，且「② 出图」只在顶部、编辑完长文案后要滚回顶部找。修复：补 `#pk-overlay .modal-box` 规则（640px / max-height 92vh / overflow auto），`.pk-tag` 标签色从 `--text-tertiary` 提到 `--text-secondary` 并加粗，动作条在文案编辑区末尾再放一份。实测弹窗 computed 样式与两个动作条均已生效。


## 2026-09-30（补五）：出图门禁——LLM 失败不再静默降级出垃圾图

补四里点名「仍未做」的那条，本轮修掉。事故链：DeepSeek 欠费 402 → 整合稿层把「规则版骨架 + `[AI生成失败…Error code: 402]`」写进正文 → 图文层 `_llm_cards` 的 `except Exception: return None` 把同一个 402 吞掉 → `_fallback_cards` 把骨架逐行切片成 5 张读者可见卡片，全程只有 lint 两条 warn。

- 新增 `src/graphics/gate.py`（三条出图产线共用的判据源）：`ai_usable` / `draft_blockers` / `assert_draft_generatable` / `GraphicsGateError` / `brief_error`。
- **修法的关键取舍**：项目纪律「AI 未配置时静默跳过」针对采集主流程，不该套到用户主动点击的出图上。所以**未配置仍走本地兜底**（`scripts/e2e_*_variants.py` 传的就是空 ai_config，依赖这条路），**已配置但调用失败改为抛 `GraphicsGateError`**（欠费/网络/JSON 不合格/卡片数不足全覆盖）。
- 污染稿在消费侧拦：识别规则版骨架指纹（`[AI生成失败`、`规则版草稿未做事实核对` 等 5 条），头条/公众号/海报三条线出图前一律拒绝并提示「先重新生成整合稿」。选择拦在下游而不是只改上游，是因为库里已经存了一批历史污染稿；上游 `content_store` 把错误串写进 content 这件事**本轮没动**（该文件正被并发编辑）。
- 兜底文案不再冒充模型产物：进度与 lint 措辞由「LLM 不可用」改为「未配置 AI，走本地模板兜底（成品需人工核对）」。
- `ir._META_NOTE_WORDS` 补 7 个指纹（`AI生成失败`/`Error code`/`规则版`/`未做事实核对`/`仅提供骨架`/`配置 AI 后重新生成`/`生成失败，已保存`），防任何路径漏网。
- `web/app.py` 头条与公众号两条 generate 线程新增 `GraphicsGateError` 分支：门禁拒绝只回一行原因，不再往 UI 刷 traceback。
- 验证：门禁单元自检 5 项（污染稿被拒、干净稿不误伤、两条渠道都拒、假 key 抛模型错、黑名单清洗）；`graphics-verify` 三条线全绿；重启后 HTTP 实测公众号与海报两条线各返回一行拒绝且无 traceback，且**拒绝发生在落盘之前**，`output/wechat/60` 原有产物未被覆盖。


## 2026-09-30（补四）：图文两条产线——模板地基修复 + 新增「AI 海报」出图产线（含 OCR 回读校验）

用户从「刚生成的公众号图文内容质量很差、写的不对」出发，最终明确**否决 HTML 模板渲染成品**，改要「整张海报交给出图模型画」。本轮两件事。

**A. 公众号图文线的地基（HTML 产线仍在使用，未删）**

- 起因取证：`output/wechat/60` 那组垃圾稿的根因**不是模板也不是 prompt**，是 DeepSeek 欠费（`/user/balance` 返回 `is_available:false`）→ 整合稿与卡片化两层同时掉到本地兜底，`_llm_cards` 的 `except Exception: return None` 把 402 静默吞掉，错误串「[AI生成失败…Error code: 402]」被印进读者可见卡片。
- 修了一处静默失效：前端 `WX_TPL_WHITELIST` 只抄了 4 项、渠道已扩到 6 项，选「浅紫清单/奶油金」会被**前端**回落成 wechat。现改为 `GET /api/wechat/templates` 由后端下发模板与三轴名单（含 `palette_ready`），前端不再同步维护名单。
- 新增 `POST /api/wechat/preview`：用固定样例文案按当前四轴**真实渲染**预览（`render_preview`），替掉原来不随配色/字体/版式变化的离线假样张 JPG；产物按参数哈希缓存在 `output/wechat/_previews/<hash>/`。为此把 `generate_graphics` 的四轴解析与整组渲染抽成 `_resolve_axes` / `_render_cards` 两条入口共用。
- 新增 `POST /api/wechat/ref-upload`（排版参考图 → `output/wechat/_refs/<token>/`，只在 manifest 记 `ref_images`，不进发布包不参与出图），为后续「逆向拆解排版」备料。

**B. 新产线 `src/ai_poster.py`：整理稿 → 整张海报由出图模型画出来**

- **出图后端零新增成本**：现有 `config.env.local` 那个千问聚合 key 就带图像模型（列表含 `qwen-image-max` / `qwen-image-3.0` / `qwen-image-edit-max` / `wan2.7-image` / `z-image-turbo`），**不用租 GPU、不用装 ComfyUI**（本机 Intel 核显 1.5GB 也跑不动能渲染中文的那批模型）。
- 通路三条硬事实（踩出来的）：必须走百炼原生 `POST {root}/api/v1/services/aigc/multimodal-generation/generation` + `X-DashScope-Async: disable`；OpenAI 形状的 `/images/generations` 对这些模型是 404；`wan2.7-image` 异步被拒。**尺寸上限 1664×1664，9:16 只能 936×1664**，传 1024×1792 直接 InvalidParameter。返回的是 OSS 临时链接，必须当场下载。
- 三段式：① LLM 按固定 JSON 拆解文案计划 → ② **字数硬门禁**（合计 ≤80、主标题 ≤12、卡片说明 ≤16、卡片 3-5 张，越界直接 `PosterError` 拒绝出图，不靠 prompt 祈祷）→ ③ 出图后 `qwen3-vl-flash` **回读逐字校验**，比对前剥空白与标点（模型常把主标题排成两行）。这一步是整条链路唯一的质检位；`qwen-vl-ocr` 只回坐标框，不能用。
- 工作台入口：整理稿卡片与详情弹窗都有「🎨 AI 海报」；`/api/poster/plan`（只拆解、可手改文案）、`/api/poster/generate`（带 plan 则不再调 LLM）、`/status`、`/list`、`/<id>`、DELETE。产物 `output/poster/<id>/{poster.png,prompt.txt,manifest.json}`。
- 验证：`scripts/e2e_ai_poster.py 58` → ALL_OK（79 字、4 卡片、回读逐字一致）；浏览器实测弹窗拆解→出图→校验全通；`graphics-verify` 三条线在 A 部分重构后全绿。
- **仍未做**：① 单张实际扣费金额未核（要去聚合站账单看），② 稳定率未做多样本统计（目前 2 张全对，样本太小），③ 手改文案后未做「只重出图不重校验」的开关，④ 海报未接发布检查清单/关键词回复等公众号包级字段，⑤ A 部分那两处「LLM 失败静默降级出垃圾图」的门禁**还没修**，只是这次靠 lint warn 暴露出来。


## 2026-09-30（补三）：微信公众号名回填 + fetch_rss 作者回落（接上一轮「更新缺失」）

用户追问：「localhost:8001 可以拉取数据了吗？我订阅的 5 个公众号，今天的数据就一篇」。取证结论：

- **8001 正常**：4 个 feed 全部 200 可解析，共 34 条、33 条已入库；feed 里 2026-09-30 只有 3 条（重返艾泽拉斯 2 + 魔兽世界情报局 1），且这 3 条**都已入库并打好 ai_tags**——不是本地漏拉，是 WeWe RSS 侧当天就这些（艾泽拉斯快讯另有 1 篇全文未就绪，界面已按上一轮改动明示）。
- **第 5 个公众号不在工作台**：`subscriptions` 里 `platform='wechat'` 只有 41–44 四行；只在 WeWe RSS 加了号、没在「添加微信订阅」贴 feed URL 的话，工作台永远不会同步它。
- **真缺陷（本轮修）**：入库的微信文章 `author` **全部为空**——WeWe RSS 的 `<item>` 不带 `<author>`，公众号名只在 `<channel><title>`。`fetch_rss` 原先只读 item 级 author/dc:creator，取不到就留空，导致素材库看不出是哪个号更的、按作者（topic）筛选永远命中不到，观感等同「有更新但找不到」。修复：RSS 与 Atom 两路都回落到 feed/channel 标题；另按 URL 精确匹配把已入库 33 行的公众号名回填（重返艾泽拉斯 21 / 魔兽世界情报局 6 / 艾泽拉斯前哨 3 / 艾泽拉斯快讯 3），未用订阅备注名做全局兜底以免错挂。
- **验证**：`py_compile` 过；四个 feed 实测署名解析为真实公众号名；`/workbench-restart`（65215→68791）后 `/api/videos` 微信 33 张卡片归属四个号、无空 author，今天 3 篇均带 ai_tags。
- **仍未做**：微信文章的 `author_sec_uid` 仍为空（回填与插入路径都没写），跨表按订阅聚合时要留意；`videos.source` 列对微信行也未赋值。

## 2026-09-30（补二）：修「公众号更新缺失」——微信订阅新文章永不打标 + 全文未就绪被静默吞掉

用户诉求：「排查并修复公众号更新缺失问题，问题修复了一半，继续」。**已完成的一半**：订阅页三维筛选（来源/分类/标签）+ 批量设分类/设标签 + 「同步当前筛选/同步勾选」，以及 `classify_videos` 的写回护栏（增量模式不改写已有 category，只补空 category 与缺失的 ai_tags）。**本轮补完剩下的两处缺口**。

- **取证（先跑真实数据，不猜）**：4 个微信订阅（WeWe RSS 在 `localhost:8001`）中 3 个 `last_sync_note=无新文章`；用 `wechat_fetcher.fetch_rss` 逐个对比 feed 与 `get_wechat_article_links`，实际有 8 条未入库，其中 1 条 `content:encoded` 为空、`description` 只剩 19 字标题。手动触发 `{"ids":[41,42,43,44]}` 同步 → 入库 7 条，但这 7 条 `ai_tags` **全为空**（同批旧行都有标签）。
- **根因①（决定性）**：`web/app.py` 的 `_auto_classify_after_sync()` 挂在 `if douyin_subs:` 分支**内部**（原 2355 行）。只勾微信订阅同步（新「同步当前筛选/同步勾选」正好会走这条路）时该分支整体不进，新入库的微信文章永远不带 ai_tags——选题雷达与渠道策略依赖 ai_tags，下游等于「公众号没有更新」。修复：把该调用**移出两条分支**，放在抖音/微信两分支之后，恢复「任何同步都补打标」的原设计。
- **根因②（静默失效，本项目头号敌人）**：`wechat_fetcher.sync_wechat_feed` 对正文 `<50` 字的条目 `continue` 丢弃且不记链接，调用方只会说「无新文章」——WeWe RSS 全文抓取失败时 `content:encoded` 就是空的，用户看到的是「订阅没更新」而实际是「全文没抓到」。修复：`sync_wechat_feed` 返回 `skipped`（被跳过的标题列表），`/api/subscriptions/sync` 在同步 note 与状态行明示「N 篇全文未就绪（标题…），下次同步自动重试」；不入库不记链接的语义保持不变（本来就是可重试）。
- **顺带堵一处同类隐患**：`/api/videos` 追加微信文章的整块逻辑原先是裸 `except Exception: pass`（app.py:188），一旦列缺失/DB 锁异常，素材库的微信卡片**整块消失且零痕迹**，表现同样是「公众号更新缺失」。改为打印 `[videos] 微信文章追加失败…`，降级行为不变。
- **验证**：`py_compile` 过；`/workbench-restart` 两次（55026→61936→62997，`/api/stats` 200）；只勾微信订阅同步两轮——第一轮 `AI 打标 58/58`、第二轮 `AI 打标 30/30`（清历史欠账），新入库 7 条 `ai_tags` 全部补齐且 `category` 仍逐条保持订阅预设的「游戏攻略」（33/33 未被子节点名覆盖，验证护栏有效）；`/api/videos` sources `wechat=33`、缺标签 0；浏览器实测订阅页 4 行微信订阅正常渲染，第 44 号显示新提示「无新文章；1 篇全文未就绪（魔兽世界无限将延续时光服2小时团），下次同步自动重试」。
- **并发提示（同日上午）**：本轮改动与「Dify 知识库下线」的改动落在**同一个 `web/app.py`** 上（对方 12:20–12:21 重写、12:24:40 自行重启，PID 62997→65215）。已确认这三处在下线后的新版本里仍成立：`_auto_classify_after_sync()` 位于 `if wechat_subs:`(2023) 与 `if douyin_subs:`(2072) **两分支之外**、`_sub_status["result"]`(2230) 之前，`pending_note` 与 `[videos]` 日志均在位；该运行实例自证 `/api/videos` sources `wechat=33`、微信缺 tags 0。**提交必须按关注点拆两笔**（Dify 下线 / 微信订阅打标与未就绪提示），谁后提交谁做 hunk 级 `git add -p`，别把对方未验证的半成品一起 commit。
- **仍未做**：那 1 篇全文未就绪的文章要等 WeWe RSS 侧重新抓取补全才会入库（本地无法强推）；`wechat_fetcher` 无「强制重取全文」入口，如需可加 `?mode=full` 重试；订阅页 WeWe RSS 占位文案仍写 `localhost:4000`（实际部署在 8001，仅提示文字）。



## 2026-09-30：Qoder 接手准备——AI 资产摸底、e2e 收编入仓、两个项目技能、依赖钉版

用户诉求：「查看项目中使用了哪些 AI 技能和插件，我想你来接手这个项目，需要 skill/插件可以自行安装，项目太大就分批拆解，后面我来优化」。本轮只做接手准备，不改业务行为。

- **AI 资产摸底（结论）**：规则源在 `.rulesync/rules/`，分发到 AGENTS.md / CLAUDE.md / CODEBUDDY.md / `.cursor/rules` / `.trae/rules` 五份副本；守则两份在 `.agents/memories/`（`30-python-backend.md` 已进 AGENTS.md 规则块，`40-graphics-variants.md` 15KB **此前无任何入口引用**）；Trae 侧另接 `coding-agent-memory-mcp`（库在 `~/Documents/ai-hub/memory-store`），WorkBuddy 有 19 个技能 + 项目记忆 `.workbuddy/memory/MEMORY.md`（三仓库权威源地图）。Qoder 侧接手前无任何项目相关 skill/plugin。
- **e2e 收编（进 git）**：`scripts/e2e_toutiao_variants.py`（18 用例 A–R）、`scripts/e2e_wechat_variants.py`（14 用例 W1–W14），原只存在于 Trae 私有工作区 `~/.trae-cn/work/6ab65f4c614dcadc9c6e4e41/`（工具一换就丢）。两脚本自包含（绝对 ROOT + `sys.path.insert`，产物写 `output/toutiao/99999`、`output/wechat/99998` 并自清理），迁移零改动。**工作区仍留有未收编的样张脚本**（`gen_new_tpl_previews.py`、`sample_variants.py`、`baseline/` 等），需要时去那儿取。
- **新增 Qoder 项目技能 `.qoder/skills/`**：`workbench-restart`（把纪律 2 脚本化：py_compile 前置 → 按端口 PID TERM/KILL → `cd web && python3 app.py` nohup → PID 变更 + `/api/stats` 200 双确认，失败保留现场不重启）；`graphics-verify`（一条命令串行跑 `check_variant_tokens.py` + 两条 e2e，非 `ALL_OK` 即非 0 退出，并附「manifest → img*.html → 前端」排查顺序与三轴登记守则）。
- **AGENTS.md 两处补入口**：关键纪律新增第 5 条（改 `src/graphics/` 前必读 `40-graphics-variants.md`、改完必跑 `/graphics-verify`）+ 增「Qoder 项目技能」小节。刻意只做 4 行索引、不把 15KB 守则内联进规则块（`rules[]` 会在每次会话整份进上下文）。
- **依赖钉版 `requirements.txt`（进 git）**：按系统 Python 3.9 实测版本钉 Flask 3.1.3 / openpyxl 3.1.5 / requests 2.32.3 / faster-whisper 1.2.1 / openai-whisper 20250625 / playwright 1.40.0 / pillow 11.3.0；文件内注明仍需手动准备的 ffmpeg、playwright 浏览器内核、`vendor/douyin_parse`。**核实结果：`python-docx`、`pandas` 全仓无 import（别误装），`PySide6` 仅 vendor 的 `qt_app.py` GUI 用，主链路不依赖。**
- **验证**：图文回归三条线跑通 `ALL_OK`（收编前后各一次）；`/workbench-restart` 实跑一次，PID 72113 → 23054、`/api/stats` HTTP 200、日志落 `logs/web_restart.log`。
- **批次计划（用户已选 A+B 先行）**：A 环境与可复现（本轮主体）；B 工作台工程债——`web/app.py` 三条采集流水线（约 531/716/1603 行）抽公共函数、清 `generateToutiaoGraphics` 死代码、拆 3683 行 `index.html`；C 图文 `minimal`/`magazine`/`bold` 配色轴接通；D 采集与选题链路加固；E 跨工具规则副本对齐与记忆库统一。

## 2026-09-30（补）：B 批开工前置——死代码清除 + 工程债描述纠偏（取证推翻旧文档）

- **清死代码**：`web/templates/index.html` 删除 `generateToutiaoGraphics`（14 行，CHANGELOG 长期挂「留待顺手删」）。取证：全仓 `web/` 仅此一处匹配、无 onclick、`web/static` 下无 .js；活动路径是同文件 `confirmToutiaoGen` → `/api/toutiao/generate`，后端路由仍在使用**不可删**。
- **旧文档记录过期，已纠**：PROJECT_STATUS 记的三条流水线行号 531/716/1603 实际为 `api_process` 643–741 / `api_fetch_and_process` 840–967 / `api_subscriptions_sync` 2022–2257。
- **推翻一条既有结论（关键）**：文档原写「三条链路的 AI 打标都由 `_auto_classify_after_sync` 兜底」——实测该函数（330–347）**只有 P1/P2 调用，订阅同步 P3 从未调用**（分类靠 2218–2241 裸 SQL 回填）。因此「三条抽成一个公共函数」不是纯重构，会给订阅链路新增打标副作用与 `_db_lock` 争用。差异清单已写进 PROJECT_STATUS 工程债条目（Excel 打开粒度 / 写入列 / 失败回读 / 状态契约与冲突码 / mark_video_source 来源值），作为 B 批改造的硬约束。
- **验证**：按纪律 2 用 `/workbench-restart` 重启（23054 → 24457，`/api/stats` 200）；浏览器实测 index.html——console 零 error/warn，`generateToutiaoGraphics` 已 undefined 而 `confirmToutiaoGen`/`openToutiaoGenDialog`/`pollToutiaoStatus`/`confirmWechatGen` 均为 function，首页正常渲染（素材 1179 / 已有 ASR 1009，侧栏 Tab 完整）。


## 2026-09-29（八）：公众号参考样本拆解 + 模板库新增 lilac_list / cream_gold 两模板（三轴同步登记）

用户诉求：「我看到有些公众号图文排版很好看，你可以给我拆解他的结构，然后丰富我们的模板库吗？」——给了两篇 mp.weixin.qq.com 卡片图文链接，确认「两个都加」。

- **拆解（文档进 git）**：`公众号卡片参考拆解-2026-09-29.md`——抓取两篇参考正文 + 45 张卡片图（`temp/wx_ref/`，不入库），逐图量出可复用参数：样本 A 浅紫编辑清单风（浅紫网格纸面 `#EDEFF7` + 信号紫 `#5B4FE0` + 藏青结论条 `#202A44` + 星金 `#E0A32E` + 编号白卡清单，零背景图依赖）；样本 B 奶油金插画教程风（奶油纸面 `#F4F0E7` + 金超大编号 `#C89B42` + 右半幅通高插画位 + 点线步骤流 + 键值面板 + 三栏收益条）。结论：两样本视觉语言不属于现有 4 套公众号模板任一 → **新增两个模板**而非新版式预设。
- **templates.py**：`_CSS_LILAC_LIST`/`_CSS_CREAM_GOLD` 两套 CSS + 4 个渲染函数（封面/内页各一）+ 注册表 2 条。身份色独立前缀 `__L_*__`（5 键）/`__C_*__`（5 键），可读性 7 键与 wechat 同名同义；cream_gold 右半幅插画位 `.illo` 为 absolute——**有题材图铺图、无图渲染 `.illo.blank` 金色点阵装饰块**（永不破版）。顺带把 cream_gold 内页 strip 第三栏标签硬编码「提醒」改「重点」（用户消息 13 明令成品图移除「提醒」，e2e W13 加回归守护）。
- **variants.py 三轴登记**：① 配色——新增 `LILAC_LIST_BASE_TOKENS`（12 键）/`CREAM_GOLD_BASE_TOKENS`（11 键，**无 `__ACCENT__`**，金色走 `__ACCENT_DK__`/`__C_NUM__`）并登记 `_TPL_BASE_TOKENS`（自动进 `PALETTE_READY_TPLS`，配色轴扩到 7 模板）；**身份键是常量、不进配色预设**，共享键随预设自动级联。② 字体——`FONT_PRESETS["heavy"]["tpl_css"]` 补两键（两模板全篇黑体栈无 Songti 锚点，必须走追加片段路）。③ 版式——两预设 × 两模板片段：lilac 封面 `.stats`/内页 `.note` 上浮，cream 封面 `.steps`/内页 `.strip` 上浮；**两个盒模型坑**：cream `.steps` 上浮必须同时解除 `flex:1`（改 `flex:0 0 auto`，否则吞掉整页高度）并用 `.ftr{margin-top:auto}` 兜页脚沉底；lilac 封面余高把 `.stats` 改 `margin-top:auto` 与 `.sum` 的 auto 边距均分（避免中部大白带，`padding-top:42px` 保底间距），两个版式片段同步 `padding-top:0` 重置。
- **wechat.py 接线**：白名单扩 6 套；hero 三分法——`no_hero_tpls=("wechat","lilac_list")` 恒 None（零图片模板），cream_gold 封面只吃题材图（`cover_img.as_uri()`，无素材→blank 装饰块）、内页恒 None，其余走 `skins._hero_uri`。
- **前端**：index.html 公众号弹窗模板白名单文案 4 套→6 套、新增「浅紫清单·编辑清单风」「奶油金插画·教程步骤风」两个选项；三轴 hint 更新为「配色对信号格/经典卡片/浅紫清单/奶油金生效，字体与版式对 6 套全部生效」。
- **验证**：三文件 `py_compile` 过；`scripts/check_variant_tokens.py` ALL_OK（配色 7 模板接通、版式 10 模板 ×2 预设片段核对、字体 10 模板全生效）；公众号 e2e 扩到 **14 用例** ALL_OK——新增 W11（lilac 默认无 `__L_*__` 残留、零图片无 `<img>`）、W12（lilac 三轴：换色/heavy 指纹 `.sum .sd{font-weight:900}`/封面+内页版式注入）、W13（cream 默认无 `__C_*__` 残留、无图走 `.illo blank` 降级、**成品无「提醒」字样**）、W14（cream 三轴：共享键换色 + 身份金 `#C89B42` 保持常量）；样张脚本生成两套各 4 页进 `web/static/assets/tpl_previews/{lilac_list,cream_gold}/`，目视核对无残留/无裁切并据此修掉 lilac 封面大白带。Flask 按端口 PID 纪律重启（62355→72113），lsof 换 PID + 新文案/样张静态资源 200 双确认。
- **仍未做**：`minimal`/`magazine`/`bold` 配色轴仍未登记；参考原图 45 张在 `temp/wx_ref/` 不入库（`temp/` 已在 .gitignore）。

## 2026-09-29（七）：公众号渠道接入变体三轴 + 字体轴双路机制（修 wechat/minimal 静默失效）

用户诉求：「请为 wechat 模板接入配色、字体和版式三轴」——即抖音素材→整理稿→公众号图文这条链路，也要像头条一样能选配色/字体/版式。取证时发现一个**连带 bug**：字体轴对全篇黑体栈的模板（wechat、minimal）一直是静默 no-op，头条线选 minimal 同样中招。

- **根因①（字体轴只做衬线栈替换）**：`variants.apply_font(css, font_key)` 的实现是「把 CSS 里的 Songti 衬线字体栈正则替换成预设栈」。classic/magazine/bold/guide/tier/quest 六模板有 Songti 锚点所以生效；`wechat`/`minimal` 模板 CSS 全篇是 PingFang SC 黑体栈，**没有可匹配的锚点 → 替换零命中 → 函数原样返回 CSS**，界面上选了「重黑」但成品图毫无变化，且无任何报错。
- **修复①（预设表加按模板追加片段，双路生效）**：`FONT_PRESETS[键]` 新增可选字段 `tpl_css = {模板键: CSS片段}`；`apply_font(css, font_key, tpl_key=None)` 改成两路——先做原有正则字体栈替换，再追加 `tpl_css.get(tpl_key)` 片段（带 `/* font:键 */` 标记，同版式轴范式）。`heavy` 预设补 wechat/minimal 片段，字体族用 `_HEAVY_SANS_STACK = '"Hiragino Sans GB","Heiti SC","PingFang SC",sans-serif'`：**PingFang SC 最粗只到 Semibold，`font-weight:900` 在 Chromium 里视觉无感**，Hiragino Sans GB 的 W6 是真重黑且本机存在（`/System/Library/Fonts/Hiragino Sans GB.ttc`），缺字体时回落 PingFang 不炸。片段只动 `font-family`/`font-weight`/`letter-spacing`，**不动盒模型**；顺带收紧字距（-.01em / -.015em）抵消字宽变大带来的换行风险。
- **调用点必须传 tpl_key**：`channels/toutiao.py`（1 行改动 + 注释）与 `channels/wechat.py` 的 `apply_font` 调用都补上 `tpl_key`。**少传就是静默 no-op**——`scripts/check_variant_tokens.py` 已把字体轴核对从「warn」升级为「失效即报错」（对每个「预设 × 已登记模板」组合实测 CSS 是否真的变化）。
- **根因②（配色轴未登记 wechat）**：`_TPL_BASE_TOKENS` 的键集就是 `PALETTE_READY_TPLS`，此前只登记 classic/tier/quest/guide，未登记模板 `palette_tokens` 恒返回 `{}`。修复：新增 `WECHAT_BASE_TOKENS`（9 键，**默认值与 `css_engine._tpl_tokens("wechat")` 的字面量逐字相等**＝选默认配色时产物零变化的硬保证）并登记；`ember_forge`/`arcane_dusk` 各补一组 wechat tokens（信号色 `#d9622b` / `#7b5fd0`，深色 `#a9451a` / `#5b41ab`，纸面 `#fbf6f1` / `#f5f4fa`）。**公众号可读性硬约束**：任何预设都必须保持「浅纸面 + 深墨字 + 白卡」，只换信号色家族，不得套用头条的深色夜底。
- **版式轴补 wechat 片段**：`LAYOUT_PRESETS` 两个预设各加 wechat 覆盖，**类名与 classic 完全不同，不能复用选择器**（wechat 是 `.rhead`/`.sig`/`.kick`/`.tiles`/`.rows`/`.ftr`）。`hero_first`：封面 `.tiles{order:-2}` 图集上浮、内页 `.wrap-in>.note{order:-2;margin-top:26px}`；`summary_first`：封面 `.rows{order:-2;margin-top:26px}` 要点上浮 + `.tiles{order:1;align-content:end}` 图集沉底 + `.ftr{order:3}` 护脚。注意 wechat 的 `.ftr` 原始规则**没有 order**，所以 hero_first 完全不碰页脚，只有 summary_first 需要显式护脚（否则沉底的图集会把页脚挤上去）。
- **接线（渠道 + API + 前端）**：`wechat.generate_graphics` 签名加 `palette/font/layout`（默认 None＝旧行为），CSS 叠加顺序与头条同范式：`resolve_keys` → 随机版式整包抽一次 → `resolve_palette(skin_key, palette_key, tpl_key)` → `extra_tokens = _tpl_tokens + palette_tokens` → `_build_css` → 追加 `layout_css` → `apply_font(css, font_key, tpl_key)`；manifest 落三轴**真实键**（永不落 "random"）。`web/app.py` `/api/wechat/generate` 读三字段（`str(data.get("x") or "").strip()[:20]` 同款三行）并透传。`web/templates/index.html` 公众号弹窗加三个下拉（`wx-gen-palette`/`wx-gen-font`/`wx-gen-layout`，版式含「每次随机」），`confirmWechatGen` 读取后写进请求体、确认行显示三轴文案；**配色轴未接通的模板如实提示**（弹窗 hint + 确认行 `palNote`：「该模板未接通配色轴，本次仍按默认色出图」）。弹窗打开时不重置下拉，与头条侧 `openToutiaoGenDialog` 行为一致（靠 HTML `selected` 天然默认）。
- **验证**：5 个 .py `py_compile` 过；`scripts/check_variant_tokens.py`（本轮新增，进 git 作持久回归）复跑 `ALL_OK`（配色 5 模板接通、字体轴 wechat/minimal × heavy 均报「生效（追加片段）」、版式 8×2 片段核对）；**公众号 e2e 新脚本 `e2e_wechat_variants.py`（work 区，W1-W10 十用例）`ALL_OK`**——W1 默认组合微信绿 `#07c160` 在位且无 token 残留；W2/W3 两组配色替换信号色、微信绿与 `rgba(7,193,96` 零残留、白卡保持；W4 heavy 封面+内页都吃到；W5/W6 两版式覆盖注入（含 `.ftr{order:3}` 护脚断言）；W7 random 三次均抽中合法键（实测 hero_first / summary_first 都出现过）；**W8 三轴传未知键回落后，封面 HTML 与 W1 默认组合字节全等**（默认零变化硬验证）；W9 minimal+heavy 字距指纹；W10 minimal+ember_forge 不改色（未登记即 no-op 的如实记录）。头条线 e2e 18 用例复跑 `ALL_OK`（字体轴改造未破坏既有渠道）。Flask 按端口 PID 重启并双确认。
- **仍未做**：`minimal`/`magazine`/`bold` 配色轴仍未登记 tokens（前端已如实提示，需要时按 `WECHAT_BASE_TOKENS` 同法补基线表 + 登记 + 各预设补该模板 tokens）；公众号**素材选图仍不随机**（主视觉来自用户提供的真实截图，随机换图无意义，与头条不同）；`guide` 模板不在公众号白名单内。

## 2026-09-29（六）：头条素材选图随机化 + 公众号渠道框架核查

用户两件事：①「头条号图文素材图片也可以是随机的，并不是第一张图片就是第一张图片素材」；②「素材库选抖音素材→整理稿→公众号图文，生成的和现在是一套框架吗」。

- **①根因（选图完全确定性）**：`skins._pick_cover_image(paths)` 永远返回 `_img_ratio`（h/w）最大者＝优先竖构图；`_pick_band_image(paths, idx)` 按比例升序排序后 `(idx-2) % len` 固定轮换。即同一素材库生成一百次，封面永远是同一张、内页横带顺序永远一样——用户看到的「第一张图总是第一张素材」不是 bug 而是旧设计。
- **①修复（`skins.py` 内核参数化，向后兼容）**：两个选图函数各加可选参数——`_pick_cover_image(paths, rng=None)`：`rng` 给定时在**竖构图池（比例 >= 1.0）**里 `rng.choice`，池空回落全池（防横图被拉竖裁切；实测「魔兽世界-正式服图片素材」3 张全是横图，走回落分支）；`_pick_band_image(paths, idx, rng=None, exclude=None)`：`rng` 给定时在「还没用过的素材」里随机抽，`exclude` 接受单张或列表（封面 + 前几页横带），素材耗尽才允许重复。**`rng=None` 逐字保持旧行为**——公众号渠道 `wechat.py` 三处调用点不传 rng，零行为变化。
- **①接线（`channels/toutiao.py`）**：生成主流程每次新建随机源 `rng = _make_rng()`，封面传 rng、横带传 `rng, exclude=used_assets` 并把选中图追加进 `used_assets`（包内不撞封面、不重复）。**guide 模板不走随机**：`<题材>攻略图/` 是用户上传的有序素材（地图/路线/资料图），保持 `theme_imgs[0]` 封面 + `(idx-1) % len` 顺序轮换。**可复现钩子**：模块级 `_RNG_SEED = None`（生产真随机）+ `_make_rng()`，测试/复现某次选图时设成整数即可让选图完全确定。**可追溯**：manifest 新增 `assets` 字段（本包实际用到的素材文件名列表，首个为封面主视觉）——随机后不能再靠目录顺序倒推「这张成品图用的哪张素材」。
- **②核查结论（同一套渲染内核，渠道组装不同）**：抖音素材→整理稿→公众号图文（`channels/wechat.py`）与头条**共用 `src.graphics` 内核**：同一批皮肤/素材函数（`_theme_image_paths`/`_pick_cover_image`/`_pick_band_image`）、同一 `css_engine._build_css`、同一 `templates._TEMPLATES` 注册表、同一 `renderer.render_card_html` 与 `package` manifest 封装。差异在渠道层：公众号默认 `template="wechat"` 信号格模板且 `no_hero`（**零素材图依赖**，杜绝游戏背景图渗进 AI 工具类图文）、白名单只放 wechat/minimal/classic/magazine、有亲测（`author_draft` 非空）/转载双模式与「真实截图 ≥2 张」铁律 lint、截图会被复制进包。**变体三轴（palette/font/layout）目前只接了头条**——`wechat.generate_graphics` 签名无这三参数，CSS 走 `_tpl_tokens` 皮原色；素材随机化本轮也只接头条（公众号主视觉本就来自真实截图，随机换素材图无意义）。
- **验证**：`skins.py`/`toutiao.py`/`wechat.py` `py_compile` 过；e2e 扩到**十八用例**全过 `ALL_OK`——新增 R（`rng=None` 旧行为断言 `cover==paths[0]`/`band==paths[0]`；20 个种子封面出现 3 种取值；横带恒不越池、不与已用素材撞图；素材耗尽回落全池）、R2（真实包 manifest `assets` 非空且列出 4 个文件名）、R3（同种子选图逐字复现、6 个种子出现 6 种素材组合＝随机确实到达渠道层）。**e2e 自身适配**：`main()` 开头钉死 `toutiao._RNG_SEED = 20260929`，否则 D 用例「显式 default 与不传 layout 字节全等」会因两次选到不同素材而误报。
- **仍未做**：公众号渠道未接变体三轴与素材随机（需要时按头条同法接：签名加参数 → `_tpl_tokens` 后叠 `palette_tokens`/`layout_css`/`apply_font` → 选图传 rng）；`minimal`/`bold`/`magazine`/`wechat` 配色轴仍未接通。

## 2026-09-29（五）：summary_first 标题遮挡修复 + 头条图文移除「提醒」块与条目首字徽章

用户报障三件事：①「图片都在布局底部，是不是都随机」；②「有些字被挡住了」；③「图文中移除『提醒』和每个首字」。

- **①定性（非随机，是版式设计行为）**：包 56 manifest `layout=summary_first`——用户自己选的「要点先行」预设，设计即大图沉底；前端版式下拉默认选中 `default`（经典排布，index.html L940 `selected`），`RANDOM_POOL=["hero_first","summary_first"]` 不含 default，只有显式选「每次随机」才抽签。结论：不想要图沉底选「经典排布」或「图先行」即可，代码零改动。
- **②根因（负边距 × order 重排叠加冲突）**：`templates.py` `_CSS_CLASSIC` 的 `h2{margin-top:-84px}` 是为「default 布局下内页标题压在横带图下沿」设计的叠压效果；`summary_first` 把 `.band` 沉底后 h2 失去图片托底，负边距把标题向上拉进 `.topline` 报头行，首字被不透明金色 `.badge`（brand 徽章）盖住（包 56 img2「机」/img4「B」实测复现）。grep 确认全文件仅此一处负边距。**修复**：`variants.py` summary_first 的 classic 覆盖层追加 `.wrap-in>h2{margin-top:18px}` 解除（同特异度后来居上）+ 根因注释。
- **③a 移除条目首字徽章（两处落点，markup+CSS 双删）**：classic 内页 `.panel .ring`（92px 圆环，显示条目 name 首字）与 quest 封面 `.ifc .ico`（54px 圆，显示看点标题首字）——`templates.py` 删两处 CSS 与 `_list_html_classic`/`_cover_html_quest` 的对应 markup。**阵营色补偿**：原联盟/部落色只挂在 `.ring` 上，删除会丢阵营区分 → 改挂 `.panel.f-alliance/.f-horde` 左侧 `border-left:8px solid` 色条。其余模板（magazine/minimal/bold/tier/guide）条目行用数字/步骤序号，无首字徽章，不动。
- **③b 移除「提醒」块（头条渠道三道防线）**：prompt 要求行改「不要输出 note 字段」→ `_build_cards_prompt` schema JSON 示例去 note → 主流程渲染前 `for card in cards: card.pop("note", None)` 代码级剔除（防 LLM 无视 prompt、防本地兜底卡 `_fallback_cards` 第 4 张自带 `note.title='提醒'` 漏出）。模板渲染函数的 `note_html` 分支**保留**（公众号渠道不受影响）；tier `.footnote`、guide `.panelbox`、quest `.flowbox` 属功能性信息块，判定保留。
- **验证**：三文件 `py_compile` 过；e2e 扩到**十七用例**全过 `ALL_OK`——J 用例补 `.wrap-in>h2{margin-top:18px}` 断言，新增 P（兜底卡自带 note，断言全部 `img*.html` 无「提醒」残留）、Q（classic 无 `class="ring"`/CSS 无 `.panel .ring{`、quest 无 `class="ico"`）；另生成目检包 99998（classic+summary_first）肉眼确认标题完整、无圆环、无提醒块。Flask 按端口 PID 重启（50804→52910），lsof 换 PID + HTTP 200 双确认。
- **记录沉淀**：`.agents/memories/40-graphics-variants.md` 新增「负边距 × order 重排 = 遮挡陷阱」「移除视觉元素 = markup+CSS 双删+补偿」两节，并写明「图沉底是 summary_first 设计行为、报障先查 manifest.layout」。

## 2026-09-29（四）：版式轴全模板覆盖（magazine/minimal/bold 补齐）+ 用户标题代码级强制

用户报障「没有随机布局 + 标题改了生成的图文没变」，要求第一性原则修复。**取证（包 56 manifest，12:01 生成）一条记录同时定性两个报障**：`template=magazine`、`layout=summary_first`（随机确实抽中并落盘）、`title="魔兽无限：任务物品智能轮流拾取"`（用户新标题确实传入落盘），但 `cards[0].title="魔兽无限B测：任务物品智能轮流拾取"`（LLM 擅自加「B测」）。即两个报障都不是「参数没传到」，而是「传到了但没生效」。另排查双入口假设：`generateToutiaoGraphics`（简单按钮）grep 仅定义处 1 个匹配、无调用点，属死代码，排除。

- **根因①（版式静默失效，第二次复发）**：`LAYOUT_PRESETS` 每预设的 css 是 `{模板键: 覆盖片段}`，`layout_css(tpl_key, layout_key)` 查不到模板键返回空串。此前只登记 classic/tier/quest/guide 四模板，用户实际生成的 magazine（以及 minimal/bold）拿到空串 → 随机版式抽中落盘但 CSS 零注入、视觉零变化、界面无任何提示。与 09-29（三）的内页选择器缺口同属「静默失效」家族。
- **修复①（`variants.py` 全模板覆盖）**：`hero_first`/`summary_first` 两预设各补 magazine/minimal/bold 规则（依据 `templates.py` 三模板真实块结构与 flex 尺寸设计）——magazine 封面默认 photo 已在 head 之前（天然图先行），`hero_first` 只放大主图（`.photo{flex-basis:54%}`）不重排，`summary_first` 大图沉到标题后目录前（`.photo{order:1}.index{order:2}`）；内页横带 `.wrap-in>.lband`/引言 `.wrap-in>.note-q` 同步重排。minimal/bold 封面主图上移放大（`.photo/.poster{order:-1}` + flex-basis 增大）、`.tagrow` 钉顶；**两模板内页无主图**，`hero_first`/`summary_first` 语义就近取主视觉块上浮（minimal→`.wrap-in>.callout`、bold→`.wrap-in>.warn`，order:-1 + 清零上边距）。选择器不共存说明：`.photo/.poster` 属封面、`.callout/.warn` 属内页，同片段并列写互不误伤。至此头条下拉 7 模板版式轴全部生效；注释块同步写明修复缘由。
- **根因②（标题被 LLM 改写）**：链路传参全程正确，但旧 prompt 措辞「优先采用它，可微调语气与字数但必须保留核心词」给了模型改写空间，实测被加词。软约束违反产品契约（弹窗明示「图文标题决定封面主标题」）。
- **修复②（`channels/toutiao.py` 代码级强制）**：`sanitize_cards` 之后新增——`user_title` 非空时 `cards[0]["title"] = summary.get("title") or user_title[:60]`，用户标题逐字上封面，不依赖模型自觉（`summary["title"]` 已在入口被 title 覆盖，兜底路径 `_fallback_cards` 本就正确）。prompt title_line 同步收紧为「必须逐字使用该标题，不得增删改写任何字词」，保证 subtitle/copy_text 语境一致。
- **前端提示（`index.html`）**：版式 hint「对经典卡片/梯度榜/任务面板/攻略图解生效」→「**版式对全部模板生效**」（配色仍如实标注四模板、字体全部）。配色轴不覆盖 magazine/minimal/bold 属既有设计（`PALETTE_READY_TPLS`），不在本轮范围。
- **验证**：`variants.py`/`toutiao.py` `py_compile` 过；e2e 扩到**十五用例**全过 `ALL_OK`——新增 M（magazine+summary_first：封面吃到 photo/index 重排、img2 吃到 lband/note-q 重排）、N（minimal/bold+hero_first：封面主图上移放大规则注入、bold 内页 warn 上浮）、O（monkeypatch `_llm_cards` 返回改写标题「LLM擅自改写的标题B测」，断言 manifest cards[0].title 与封面 HTML 均为用户标题、改写标题不出现）；Flask 按端口 PID 重启（47588→50804），lsof 换 PID + 首页含「版式对全部模板生效」双确认接管。
- **记录沉淀**：新增 `.agents/memories/40-graphics-variants.md`（变体轴守则：静默失效头号敌人、版式覆盖机制要点、用户输入代码级强制原则、e2e 跑法与「选项不生效」第一性排查顺序 manifest→HTML→前端/进程）。
- 仍未做：`minimal`/`bold`/`magazine`/`wechat` 配色轴未接通（传预设被安全忽略，前端已如实标注）；`generateToutiaoGraphics` 死代码未清理（无害，留待顺手删）。

## 2026-09-29（三）：版式轴内页重排补全（方案一）+ 随机版式选项

- **根因（用户报障「第一张变、后面几张不变」）**：版式轴第三步的 `LAYOUT_PRESETS` 只写了**封面级**选择器（`.hero`/`.gmap`/`.mast`），而 classic 内页图块叫 `.band`、要点容器 `.panels`、小结 `.note`，tier 内页是 `.tier` 包 `.thead`/`.tgrid` 且**无主图**——覆盖片段注入到了内页 HTML（`/* layout:... */` 标记在），但选择器匹配不到任何元素 → 内页零变化。manifest 证据（包 56）：`layout=summary_first` 已生效、四张 HTML 都含标记，唯独 img2-4 无 `.hero` 可排。属实现缺口，非运维问题。
- **修复（方案一：三预设补内页规则，四模板全覆盖）**：`variants.py` 重写 `LAYOUT_PRESETS`，为 `hero_first`/`summary_first` 各补上 classic/tier/quest/guide 的内页重排片段。防嵌套误伤用**直接子选择器/内页专有类**——`.wrap-in>.note`（classic 封面的 `.note` 嵌在 `.cta` 里，不加 `>` 会被连带重排）、`.tier>.tgrid`/`.tier>.thead`（`.tier` 只存在于 tier 内页，封面用 `.tpreview`，天然隔离）。tier 内页无主图：`hero_first` 改为放大梯队大徽章（`.tbadge` 86→104px、宽 150→176px）作主视觉，`summary_first` 让 `.tgrid` 上浮到 `.thead` 之前；classic 内页：`hero_first` 把 `.band` 钉到报头之下并解除 700px 高度上限（→820px，图更抢眼）、`.note` 压尾，`summary_first` 让 `.band` 沉到要点之后、`.note` 压尾。`default` 仍空覆盖＝现状零变化。
- **随机版式（新选项）**：`variants.py` 新增 `RANDOM_LAYOUT_KEY="random"` 与 `RANDOM_POOL=["hero_first","summary_first"]`（只从非 default 抽，保证看得到与经典排布不同的块序）、`pick_random_layout()`（`random.choice(RANDOM_POOL)`）。`channels/toutiao.py` 生成入口分流：`layout=="random"` 时**本次生成只抽一次**（整包统一版式，不会一张一个样），抽中真实键经 `resolve_layout`/`layout_css` 生效并写进 `manifest.layout`（落 hero_first/summary_first，**不落 "random"**，产物可追溯）；其余走原闭集回落。`index.html` 版式下拉加「每次随机（自动换排布）」选项，前端 JS 原样透传 value；`web/app.py` 无需改（`[:20]` 截断与 `layout or None` 对 "random" 均无碍）。
- **验证**：`variants.py`/`toutiao.py` `py_compile` 过；e2e 扩到**十二用例**全过 `ALL_OK`（新增 J：classic 内页 img2 吃到 summary_first 的 `.band` 沉底+`.note` 压尾；K：tier 内页 img2 吃到 `.tgrid` 上浮+`.thead` 下沉；L：`layout="random"` 连抽 8 次均落合法真实预设、注入对应覆盖、manifest  never "random"，seen 覆盖两键）；样张脚本重跑 4 包×3 版式×2 卡＝24 张，内页 c2 HTML 逐张确认注入内页重排规则、default 内页无标记（零注入）；Flask 按端口 PID 重启（45358→47588），`lsof` 确认换 PID + 首页含 `<option value="random">` 双重确认接管。
- 仍未做：`minimal`/`bold`/`magazine`/`wechat` 四模板版式轴 likewise no-op（未接通），需要时按同一套路加覆盖片段并登记进 `LAYOUT_PRESETS`。

## 2026-09-29（二）：修复 guide 封面标题裁切（比例感知满铺整图）

- **根因**：源攻略图（`output/toutiao/<题材>攻略图/4_01.jpg` 等，1078×1918，h/w≈1.78）本身是整张 9:16 信息图——顶部自带金色标题横幅、中部地图、底部路线面板，比例与画布 1080×1920（1.778）几乎一致；旧逻辑把它塞进 `.gmap.cover` 子盒（约 968×1100、比例≈1.14），`object-fit:cover` 居中裁切上下各约 300px，图内自带标题横幅被切半。与版式轴无关，default 版式同样存在。
- **修复（两文件三处）**：`channels/toutiao.py` ctx 注入 `cover_ratio = skins._img_ratio(cover_img)`（h/w，异常返回 None）；`templates.py` guide CSS 新增 `.gfull{position:absolute;inset:0;z-index:0;overflow:hidden}` 与 `.gfull img{width:100%;height:100%;object-fit:cover}`；`_cover_html_guide` 开头加分支——`ctx["cover_ratio"] >= _GUIDE_FULLBLEED_RATIO`（常量 1.6）时输出满铺结构 `div.gfull>img + div.frame + div.wrap-in(空)`，整图满画布（1.779≈1.778 几乎零裁切）；装饰框 `.frame`（无 z-index）与内容层 `.wrap-in`（z-index:1）均在 DOM 更后/更高层，正常压整图之上。ratio < 1.6 或 None（如 wechat 通道未注入）走原结构、零变化；guide 内页（`.gmap.list` 横带）不受影响。
- **验证**：`templates.py`/`toutiao.py` `py_compile` 过；样张脚本补同款 ctx 注入后重跑，guide 三版式封面目检满铺整图、顶部标题横幅与底部路线面板完整、装饰框正常，内页结构不变；e2e 扩到九用例（新增 H：guide 封面含 `div.gfull` 且内页不含；I：monkeypatch `_img_ratio` 返回 1.0 回落旧结构无 `div.gfull`）全过 `ALL_OK`；Flask 重启 200。
- **勘误（重启纪律）**：本节与版式轴节所记「Flask 重启 200」当时均由**旧进程**返回——`pkill -f "web/app.py"` 匹配不到实际命令行（`.../MacOS/Python app.py`，cwd=web），旧进程未被杀、新进程抢端口失败静默退出，15801 端口持续由 09-28 21:31 启动的旧代码占据。用户侧现象（09-29 11:00 包 56）：生成弹窗没有「版式」下拉、配色/字体可选但布局不变；manifest 证据为 `palette=ember_forge` 生效但**无 `layout` 字段**（新代码即使不传也会写 `layout=default`）。已改为按端口 PID 杀（`lsof -nP -i :15801` → `kill`）后重启，并以 `lsof` PID 更换 + 首页含 `tt-gen-layout` 双重确认新进程接管；AGENTS/CLAUDE/CODEBUDDY/规则文档的重启纪律同步改写。

## 2026-09-29：图文变体轴第三步——版式轴接入（CSS 覆盖层重排 + 前端下拉）+ quest 封面 ifact 修复

变体三轴的最后一轴。版式＝同一模板内元素排布的变化，沿用预设闭集原则（只许从表里挑，禁自定义）。技术路线与字体轴同套路：**构建后 CSS 末尾追加覆盖片段**，不改 HTML 结构——各模板 `.wrap-in` 均为 `display:flex; flex-direction:column`，子块用 `order` 重排；覆盖选择器与原模板同特异度、追加在 `<style>` 末尾后来居上。`default` 空覆盖＝现状，零行为变化。

- **顺手修复隐藏 bug（quest 封面 ifact）**：`_CSS_QUEST` 里定义了 `.ifact{display:flex;gap:18px}` 横向容器，但 `_cover_html_quest` 从未输出该 div，4 个 `.ifc` 摘要格直接在列容器里纵向全宽堆叠。修复为 `facts_html = ('<div class="ifact">%s</div>' % facts) if facts else ""` 并同步末尾插值。**注意这是有意的行为变化**：quest 封面 HTML 与旧产物不同（摘要行变横向四格），「默认零变化」口径对 quest 封面不成立，其余模板不受影响。
- **`variants.py`（版式预设表）**：`LAYOUT_PRESETS` 3 组——`default` 经典排布（空覆盖）／`hero_first` 图先行（报头钉最上 order:-2、主图上移到标题前 order:-1）／`summary_first` 要点先行（主图沉底 order:1、标题下摘要块自然上浮）。每组 `css` 为 `{模板键: 覆盖片段}`，只给 classic/tier/quest/guide 四个已接通模板定义（classic 用 `.topline/.hero`、tier/quest 用 `.mast/.hero`、guide 用 `.mast/.gmap`），其余模板 no-op。新增 `LAYOUT_KEYS`/`DEFAULT_LAYOUT_KEY` 与三接口 `resolve_layout`（None/未知回落 default）、`layout_css(tpl,layout)`（默认/未知/未覆盖返回空串）、`layout_label`。模块文档字符串补第 4 条设计原则。
- **生成链路接线（`channels/toutiao.py`）**：`generate_graphics(...)` 新增 `layout=None` 入参；接线顺序 `_build_css → layout_extra=variants.layout_css(...) → if layout_extra: css += "\n/* layout:%s */\n%s" → apply_font`（门控追加，default 为空串即零注入）；manifest 新增 `layout` 字段。
- **API 与前端（`web/app.py` + `web/templates/index.html`）**：`/api/toutiao/generate` 接收并透传 `layout`（截断 20 字符，空串转 None 走默认）；头条生成弹窗在「配色 / 字体」后加「版式」闭集下拉（经典排布/图先行/要点先行，默认经典），确认弹窗文案同步显示所选版式。
- 验证：4 个 `.py` `py_compile` 过；样张脚本渲染 4 包（55/99921/99922/99923）×3 版式×2 卡＝24 张到 `output/_layout_samples/`（索引页 `http://localhost:15801/media/_layout_samples/index.html`），目检无新增溢出/错位——hero_first 主图确实置顶、summary_first 摘要块确实上浮且 quest 摘要行保持横向四格；**默认零变化硬验证**——e2e 用例 D 断言显式 `layout="default"` 与不传 layout 的封面 HTML 字节全等、用例 A 断言默认 HTML 无 `/* layout:` 标记，`git diff` 复核覆盖追加受 `if layout_extra:` 门控；e2e 扩到七用例（A 默认/B tier 熔火黑体/C magazine 保护/D 显式 default 零变化/E hero_first 注入/F 未知键回落/G quest summary_first+ifact 断言）全过 `ALL_OK`；Flask 重启后首页与样张索引均 200。
- 已知既有现象（guide 封面标题裁切，非本轮引入）：已于同日修复，见上方「2026-09-29（二）」节。
- 仍未做：`minimal`/`bold`/`magazine`/`wechat` 四模板配色轴未接通（传预设被安全忽略），版式轴 likewise 对这四个模板 no-op；需要时按同一套路 token 化/加覆盖片段并登记进对应闭集表。

## 2026-09-28（四）：图文变体轴第二步——选定组合接入生成链路 + 四模板配色轴全接通

用户从 24 张样张中选定「**配色 1 + 字体 1**」＝`gold_night` 鎏金夜蓝 + `serif` 衬线金标（均＝现状）为默认组合。因选定组合＝现状，本轮接线的硬约束是**零行为变化**：所有新 token 默认值必须逐字等于 token 化前的硬编码字面量，默认预设的 palette/tokens 覆盖均为空字典。

- **`variants.py`（补齐 + 默认组合）**：`PALETTE_READY_TPLS` 从 `{"classic"}` 扩为 `{classic,tier,quest,guide}`；补第一步遗漏的 token 缺口——tier 加 `__T_BODYTXT__`，quest 加 `__Q_BNR_HI__`/`__Q_BNR_LO__`/`__Q_BNR_TXT__`/`__Q_ICO_HI__`/`__Q_ICO_LO__`（横幅渐变与 ifact 图标底径向渐变），并在 ember_forge/arcane_dusk 两组预设里给出对应风格化覆盖值；新增 `DEFAULT_PALETTE_KEY="gold_night"` / `DEFAULT_FONT_KEY="serif"`，`resolve_keys(palette,font)` 统一做闭集校验与兜底（None/未知键回落默认），`resolve_palette(skin,palette,tpl_key=None)` 传 tpl_key 且不在 `PALETTE_READY_TPLS` 时忽略预设覆盖（未接通模板保护）。
- **`templates.py`（tier/quest/guide 三段 CSS token 化）**：背景三段渐变、glow、边框、标题渐变、面板底、正文/次要文字、chip 文字色等全部换成 token；**身份色一律保持硬编码不 token 化**——tier 梯队红/金/紫（tpreview 四色、tbadge 渐变、tcard 左边条、tchip 背景）、quest 火焰/宝石/盾牌（#ff8c32 系）、guide 荧光绿路线轨（#7cfc00 系），换配色时这些语义装饰色不变。
- **生成链路接线（`channels/toutiao.py`）**：`generate_graphics(...)` 新增 `palette=None, font=None` 两个入参；CSS 构建改为 `resolve_keys → resolve_palette(skin,palette,tpl_key) → extra = {**css_engine._tpl_tokens(...), **variants.palette_tokens(...)} → _build_css → apply_font(css,font_key)`；manifest 新增 `palette`/`font` 两个字段，产出包可追溯用了哪组变体。
- **API 与前端（`web/app.py` + `web/templates/index.html`）**：`/api/toutiao/generate` 接收并透传 `palette`/`font`（各截断 20 字符，空串转 None 走默认）；头条生成弹窗在模板下拉后新增「配色 / 字体」两个闭集下拉（鎏金夜蓝/熔火赤金/暮光奥术 × 衬线金标/硬朗黑体，默认选中现状组合），确认弹窗文案同步显示所选组合。简单入口 `generateToutiaoGraphics`（只发 summary_id）不动，走后端默认。
- 验证：4 个 `.py` 全部 `py_compile` 过；**零行为变化硬验证**——重跑样张脚本，现状组合 8/8 HTML 与改动前**字节全等**（CSS 完全一致），7/8 PNG md5 相同，唯一差异张（tier 封面）经 numpy 像素分析为 2.19% 像素变化、**最大差值仅 3/255** 且全图散布，同时该张 HTML 字节全等 → 定性为 Chromium PNG 编码量化噪声，非行为变化；Flask 重启后 200；端到端离线验证（合成整合稿 + 空 `ai_config` 走 `_fallback_cards` 本地兜底，不消耗 LLM）三用例全过：A 默认 classic 不传参 → manifest `gold_night/serif` 且无 token 残留、B `tier+ember_forge+heavy` → 换色与字体替换均生效、C `magazine+ember_forge` → 未接通模板忽略配色预设保持皮原色。
- 仍未做：**版式轴**（换布局/换元素位置）留待后续；`minimal`/`bold`/`magazine`/`wechat` 四模板的配色轴尚未接通（传预设会被安全忽略），需要时按同一套路 token 化并加进 `PALETTE_READY_TPLS`。

## 2026-09-28（三）：图文变体轴第一步——配色/字体预设表 + classic token 化（样张选型中）

用户诉求：头条号图文「每次生成只有一种布局太呆板」，希望可换颜色/字体/位置但保持模板家族观感。开源调研首推 guizang-social-card-skill（AGPL-3.0，28 版式+10 主题+data-theme 换肤+预设闭集禁自定义 hex），只吸收架构不抄代码。落地拆两步：第一步配色轴+字体轴+样张选型（本轮），第二步版式轴 + tier/quest/guide 的 CSS token 化 + 生成链路接线。

- **新增 `src/graphics/variants.py`（预设闭集表）**：`PALETTE_PRESETS` 3 组（gold_night 现状 / ember_forge 熔火赤金 / arcane_dusk 暮光奥术），每组＝皮肤调色板同键名覆盖（bg_top/bg_mid/bg_bot/gold/gold_hi/gold_deep/panel/panel_b/text/dim）+ 模板级 token 覆盖；`FONT_PRESETS` 2 组（serif 现状 / heavy 标题换黑体），`apply_font(css, key)` 用正则整体替换两套字体栈（`"Songti SC","Noto Serif SC"[,"STSong"],serif` 与 `"PingFang SC","Hiragino Sans GB",sans-serif`），不改模板结构。`resolve_palette(skin,palette)` / `palette_tokens(tpl,palette)` 供调用方合并进 `_build_css` 的 extra_tokens。`PALETTE_READY_TPLS={"classic"}`：配色轴第一步只接通 classic，tier/quest/guide 的 CSS 仍有大量硬编码色值（荧光绿轨/红金紫梯队），第二步再 token 化。
- **`templates.py` `_CSS_CLASSIC` token 化（9 处）**：新增 6 个 token `__GLOW__`/`__GOLD_RGB__`/`__ON_ACCENT__`/`__PANEL_RGB__`/`__RING_HI__`/`__RING_LO__`；默认值放 `variants.CLASSIC_BASE_TOKENS`（＝token 化前的字面量），`css_engine._tpl_tokens` 不动、合并逻辑在调用方，保证零行为变化；阵营色（`.faction.*`/`.panel.f-*`）语义固定不 token 化。
- **样张（选型产物，gitignore 区）**：临时脚本（work 区不进 git）渲染 24 张 PNG + contact sheet 到 `output/_variant_samples/`：classic 3 配色×2 字体、guide/tier/quest 各 2 字体（配色固定现状），每组合封面+内页；索引页 `http://localhost:15801/media/_variant_samples/index.html`。数据源：包 55（classic/魔兽世界-无限）、包 99921-99923（guide/tier/quest/魔兽世界-正式服）。
- 验证：`py_compile` 过；零行为变化硬验证——classic 现状组合样张与包 55 原图 **md5 字节全等**，tier/quest/guide 仅封面报头日期随渲染日变化（模板设计行为）；目检通过（新配色无残留蓝/金块、徽章深色文字对比足够、heavy 标题已转黑体）。
- 待用户选定后（第二步）：`toutiao.py` generate_graphics 增 palette/font 参数 + manifest variant 字段、前端选择器、tier/quest/guide token 化、版式轴。

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
