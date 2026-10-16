# 海报链路渠道分叉 + 公众号双形态 实施计划

**日期**：2026-10-13｜**状态**：待号主确认

## Context（为什么做）

号主对头条/小红书的攻略页图文已满意，现在要把公众号线接进同一条海报链路，且三条拍板：

1. **形态 A（图片消息）与形态 B（文章配图）共存；形态 B 仅公众号可用**，其他渠道不出现形态概念。
2. **页面交互更清楚**：渠道、形态在海报弹窗里要有明确控件；发布载荷区按渠道显示对应内容，不再"一套文案走天下"。
3. **渠道分叉落地：小红书档不输出尾注和关键词回复**（2026-10-13 小红书被判"AI 托管"的整改：正文里的 AI 自证尾注句和「回复 XX 获取」是判决旁证）。

**"尾注"的口径**：小红书档两者都不输出——① 图上末页印的免责脚注（弹窗里那个「尾注（只印末页）」框）；② 出图时自动追加到配文末尾的合规尾注句 `COMPLIANCE_NOTE`（「本文由 AI 辅助整理归纳…」）。已核实 [draft_audit.py](file:///Users/zhangyajun/Documents/project/video2text/src/draft_audit.py) 对缺尾注只 WARN 不拦、对「回复 XX 获取」只记 INFO，删掉不撞门禁。

**现状关键事实**（探索已确认）：
- 海报链路本身渠道无关：整理稿详情「🎨 AI 海报」按钮对 wechat_material 整理稿已可用，3:4 比例档已有（`SIZES`）
- publish 载荷四键（copy_text/digest/keyword_reply/source_note）由模型在拆解时生成，`_clip_publish` 裁切；尾注句是**出图时**在 `generate_poster` 里追加的（`src/ai_poster.py` ~L2357），不在图上
- manifest 已有 `"channel": "poster"` 键但无人消费——**保留不动**，新增 `publish_channel` + `form` 两个键，旧 manifest 无此键时视为 toutiao，行为零变化
- 前端发布复制 `pkPublishText()` 从 manifest 顶层字段拼四段；`pkReadPayload` 不带 ratio（ratio 走顶层字段惯例，channel 照抄）

## 改动方案

### 1. src/ai_poster.py（后端核心）

**新增常量与渠道检查清单**（放 CHECKLIST 附近）：

```python
PUBLISH_CHANNELS = {"toutiao": "头条", "xhs": "小红书", "wechat": "公众号"}
WECHAT_FORMS = {"newspic": "图片消息", "article": "文章配图"}
```

把 `CHECKLIST` 从字符串列表重构为带渠道标签的条目（`(适用渠道集合, 文本)`），新增 `checklist_for(publish_channel, form)` 返回过滤后的清单：
- **toutiao**：通用四条（图内文字/视觉评审/数字核对/72h 回填）+ AI 声明勾选 + 配文保留尾注
- **xhs**：通用四条 + 小红书四查（现有【发小红书】前缀条目，过滤后去掉前缀）
- **wechat newspic**：通用四条 + 勾「内容由 AI 生成」+ 图片消息无原创按钮 + 配文保留尾注 + 3:4 尺寸提示
- **wechat article**：通用四条 + 勾「内容由 AI 生成」+ 尾注放正文文末 + 文末钩子与关键词自动回复已配置 + 头图用第 1 页、攻略页组按页顺插

**`generate_poster` 扩参**（现签名 ~L2137）：加 `publish_channel="toutiao"`、`form="newspic"`，非法值校验回落；三处行为分叉：
- 追加尾注条件（~L2357）改为 `publish_channel != "xhs"` 才追加 `COMPLIANCE_NOTE`
- xhs 档在排版前把 plan 顶层与各页的 `footer` 清空（图上末页免责脚注不印）
- manifest 新增 `"publish_channel"`、`"form"`（仅 wechat 存，非 wechat 存 `""`）；`"checklist"` 改用 `checklist_for()`

拆解（`build_text_plan`）**不改**：模型仍生成全部四键（keyword_reply 留在数据里无害），渠道只管装配与展示——换渠道不用重新花一次拆解钱。

### 2. web/app.py（路由接线）

- `/api/poster/plan`（L2495）：读 `publish_channel`（校验回落 toutiao）→ 存进 `_poster_status["plan"]["publish_channel"]`（重开弹窗 `pkHydrate` 能恢复）
- `/api/poster/generate`（L2632）：读 `publish_channel` + `form` → 传给 `generate_poster`
- `/api/poster/list`（L3162）与 `/api/poster/<id>`（L3191）：条目回传 `publish_channel`/`form`（缺省 toutiao/newspic）
- `/api/poster/plan/page`、preview 不动（单页重拆与看版面与渠道无关）

### 3. web/templates/index.html（前端交互）

**弹窗工具条**（`.pk-bar` L1445-1464，比例选择器旁）：
- 渠道 pills：`头条 / 小红书 / 公众号`（`_pkChannel`，默认头条＝现状）
- 公众号选中时出现形态 pills：`图片消息 / 文章配图`（`_pkForm`，默认图片消息）；其他渠道隐藏
- `pkSetChannel(ch)`：切公众号时若比例是 9:16 自动切 3:4；切走时 3:4 自动回 9:16；切换后重渲染发布区

**发布载荷区**（`pkRenderPlan` L3710-3714）按渠道分支：
- 头条：现状四段不动
- 小红书：显示 摘要/配文/来源说明，**隐藏关键词自动回复行**，加一行提示「小红书档：不输出尾注与关键词回复；发布时在发布页勾选『笔记含AI合成内容』」
- 公众号·图片消息：现状四段（尾注+关键词回复保留）+「3:4 图片消息口径」提示
- 公众号·文章配图：四段替换为「文章配图用法」块（头图=第 1 页、正文用整理稿、尾注放正文文末、文末钩子+关键词回复提醒）

**复制拼装**（`pkPublishText` L4802）：加 channel 参数——xhs 去掉【关键词自动回复】行；wechat article 改为文章口径文本。`copyPosterPublish` 与成品页复制按钮分别传 `_pkChannel` / `_pkDetail.publish_channel`。

**请求体**：plan（~L4002）加 `publish_channel`；generate（~L4421）加 `publish_channel` + `form`；`pkHydrate` 从 status 恢复 `_pkChannel/_pkForm`。

**AI 海报列表**（`.pk-cm` 角标 L4876-4883）：非头条渠道加渠道小标签（小红书 / 公众号 / 公众号·文章），旧包无键不显示。

### 4. scripts/e2e_poster_layouts.py（回归防线）

新增一段（O3）：`checklist_for` 三渠道过滤正确（xhs 无微信/AI 尾注条目、wechat 无小红书条目）；`generate_poster` 装配分叉（xhs 的 copy_text 无 COMPLIANCE_NOTE、footer 被清空；toutiao 行为逐字不变）；manifest 新键在位；非法渠道值回落。先单跑此段（30 秒级）再进全量。

## 明确不做（本次范围外）

- **不生成公众号文章正文/HTML 排版**：正文仍走现有「复制整理稿」与 md 权威源交付流程，工作台只管配图包与发布口径
- 老的「💬 生成公众号图文」模板卡链路不动，与海报链路并存
- 模型拆解提示词不改（keyword_reply 照常生成，仅展示层过滤）

## 验证

1. `env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 -m py_compile` 三个改动的 .py
2. 单跑 `scripts/e2e_poster_layouts.py` 新段 → 全绿后跑完整门禁 `bash .qoder/skills/graphics-verify/scripts/run_graphics_checks.sh`（约 13 分钟，期间不改文件），末行 `ALL_OK`
3. 按端口 PID 重启 Flask（`workbench-restart` 脚本），`lsof` 确认 PID 更换 + `/api/stats` 200
4. 界面走查（纪律 5/6）：`python3 scripts/agent/ui_walk.py <整合稿id> "主题" --plan`——核对渠道 pills 文案、小红书档隐藏项、公众号才出现形态 pills；截图给号主
5. 真链路各跑一次：攻略页素材 × 小红书档（0 元）与 × 公众号 3:4 图片消息档，核对产物配文无尾注/无关键词回复、公众号档尾注在位
