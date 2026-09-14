# video2text / 自媒体体系长期备忘

## 三仓库自媒体体系架构（权威源地图，见 media-workbench/docs/DEV-SYNC.md）

- **video2text**（~/Documents/project/video2text，Flask，端口 15801）：抖音/公众号素材采集 + ASR 转写 + AI 加工 + 选题雷达。**选题层权威源**：src/content_store.py 的 RADAR_CHANNEL_STRATEGY（7:2:1 配比、止损线 CTR<1%、排除词），经 /api/strategy/channels 下发给工作台。
- **media-workbench**（~/Documents/project/media-workbench，Node，端口 5180）：自媒体工作台（发文流程/草稿三态待审已审已发/头条数据回填/对标爆文库/标题公式检测 AI 腔防同质化/策略速览/待办/周报 G1G2G3 阶段门）。写作层权威源 server.js DIFY_CHANNEL_PROFILES；样本层 runtime/toutiao-benchmarks.json；检测层 detectTitleFormula；运营层 runtime/*.json。无 package.json 依赖，纯内置模块。改代码必须重启（无热重载）。
- **自媒体内容工厂**（~/Documents/自媒体/_content_factory，非 project 目录）：Dify 可视化工作流 + dify/scheduler/dify_auto_publish.py（cron 自动发文）+ Karakeep(素材 :3000) + Whisper(转写 :9000) + Activepieces(:8080) + wechat-publisher。DSL 05/07 检测节点与 server.js 正则口径需同步。用户口中的"self-media-factory"即此目录（project/ 下无此名）。
- **media-pivot-plan**（~/Documents/project/media-pivot-plan）：三平台转型方案 HTML + brand-guideline.md（2026-08-21 暗金品牌手册：暗金车库/暗金造物/暗金装备库，改名有 7 天冷却期，"暗金观察"曾失败）。
- 双机同步：三仓库 git + runtime 数据手动迁移（media-migrate.tar.gz 六项清单）；周一体检自动化（TRAE Schedule 7ae8a336，非 crontab）。

## 运营现状（2026-09-10 快照）

- 头条号修复期（至 2026-10-01，G1 阶段门）：排期 3天×2篇（周日/一/三 8:00），周五不发；带 #文章首发挑战赛 + 头条首发；8 周无起色（中位展现三位数）→ 降为辅渠道。
- 公众号养流量期（G2 门槛 500 粉+收藏率>5%，目标 5000 粉开流量主）：周 2 更——周一 AI 赛道文 / 周四战地日记。
- 变现路线（2026-09 定调）：近期代运营服务 → 中期付费专栏/DSL 模板/返佣清单 → 远期开源 content-review-workbench。私域钩子：文末回复「工具」领 AI 提效工具清单（WECHAT_FOOTER_MD）。
- IP 方案：ip-war-diary/战地日记-IP内容方案-v1.md——「一人+AI 工作流」人设，差异化契约=真实数字/承认失败/不卖课，7 个结构模板轮换（禁连续同结构），10 篇立信期开篇，工具试用日月更。素材池 28 条。
- 账号名漂移待对齐：6 月「最后的暗金」→ 8 月计划改「暗金车库/造物」→ 实际当前 头条「唯金观察局」/公众号「老张码上记」；ip-war-diary 中公众号写「一人 AI 工作流（已定稿）」与实际名不一致。
- 今日交付：output/ 内容策略报告 + content_calendar_30d.json + strategy_adjustments.json（公众号渠道 AI 加权与实际粉丝画像错位的修正建议）。注意：公众号 AI 线是战地日记 IP 战略的有意布局，非误判；策略报告的交叉内容支柱（dev_x_game）与工具试用日栏目天然契合。
