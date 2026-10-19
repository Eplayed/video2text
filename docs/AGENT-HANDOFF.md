# 接手这份仓库要认的工具与流程

> 读者：**任何一个** coding agent（Qoder / Trae / WorkBuddy / Cursor / Claude Code / Codex CLI）。
> 这份文件是"工具与流程"的单一入口；**内容口径的来龙去脉在 `CHANGELOG_FOR_AI.md`，角色间的结论在 `output/角色交接/`**。
> 最后更新：2026-10-19。写这份的起因是号主问"换别的 agent 软件，怎么快速接手"。

## 0. 一句话定位

抖音/公众号/B站 素材采集 → ASR 转写 → AI 加工 → SQLite → Flask 工作台（127.0.0.1:15801）→
出图（头条图文卡 / 公众号 / 海报与图文攻略）。**这里只是素材层与产线**；
三仓库架构、权威源地图、换机流程见 media-workbench 仓库 `docs/DEV-SYNC.md`。

**2026-10-19 变了的一件事**：运营层那个仓库（`~/Documents/project/media-workbench`，5180 的
`node server.js`）**号主决定删掉**，5180 从 10-08 起就没在监听、它的数据冻在 9 月下旬。
搬过来的只有台账与发布登记：登记口现在是 **15801 `POST /api/ops/register`**（运营页顶部那一行），
旧台账 100 条已整本导入 `publish_stats`（来源标「旧台账」），原始 `runtime/` 归档在 `output/legacy-5180/`。
**句式黑名单的权威源已改成 `tools/banned_patterns.js`**（2026-10-19 从 `server.js:951-1091` 整段搬来、
逐字未改；生产层 `自媒体/_content_factory/verify_toutiao.js` 与 `verify_draft.js` 现在 require 这份，
**读不到直接判 FAIL**——以前切片失败只打一行 INFO，`src/draft_audit.py` 只认 PASS/WARN/FAIL
会把那行丢掉，机检看着像查过了）。同步义务没变：这份与
`自媒体/_content_factory/dify/custom/05-darkgold-…-v2.yml` 的 02C PATTERNS / 02E SANITIZE 两处要一起改，
`server.js` 里那份从此是**待删的旧副本，别再往那边改**。
`docs/DEV-SYNC.md` 与 `AGENTS.md` 第七节那两份文档（三仓库架构、换机流程、"不进 git 的 6 项数据清单"、
产线双端分工纪律）在删仓库前要先搬进这里或 `~/Documents/自媒体`，**搬之前别删**；
逐条核对表见 `output/角色交接/2026-10-19-dev-删5180前核对表.md`。

## 1. 规则有几份、改哪一份

同一套根现在有 **7 份文字**（这是现状，不是设计缺陷让你重新发明）：

| 位置 | 角色 |
|---|---|
| `.rulesync/rules/10-overview.md` / `20-conventions.md` / `30-python-backend.md` | rulesync 的**源**（`rulesync.jsonc` targets = agentsmd / claudecode / codebuddy / cursor） |
| `AGENTS.md` | 生成物 + 顶部一段 TOON 规则清单；Codex CLI 直接读这份 |
| `CLAUDE.md`、`CODEBUDDY.md`、`.trae/rules/project_rules.md`、`.cursor/rules/10-overview.mdc`、`.cursor/rules/overview.mdc` | 各家工具的副本 |

**改守则 = 一次改全部副本**（本机没装 rulesync CLI，是手工同步的）。只改一处，
下一个 agent 开工读到的就是旧规矩——这件事已经踩过一次（`.agents/memories/40-graphics-variants.md` 里记着）。

## 2. 项目 skill：13 个，只有 4 个在仓库里

`.qoder/skills/` 下 13 个技能，**9 个 `media-*`（内容产线角色）被 `.gitignore` 排除**——
仓库是公开的（`github.com:Eplayed/video2text`），那几份里写了变现与平台口径，不进 git。

| 在仓库里（任何工具都能直接跑） | 干什么 |
|---|---|
| `graphics-verify` | 一条命令跑五条回归线（见第 3 节） |
| `workbench-restart` | 按端口 PID 安全重启 Flask + 语法自检 + PID/接口双确认 |
| `article-audit` | 头条稿发布前体检（禁用句式 / AI 味 / 中间产物泄漏 / 数字出处） |
| `article-humanize` | 把稿子改得像人写的：只改命中句，其余逐字保留 |

被 gitignore 的 9 个角色技能：`media-pm → media-planner → (media-writer | media-material) →
media-ui → media-publish → media-ops → media-retro`，加 `media-dev`。
**别的工具不会自动加载 Qoder 的 skill 格式**：把对应目录里的 `SKILL.md` 当普通文档读，
照它的步骤做就行——里面调用的都是普通 shell / python 脚本，没有私有运行时。

## 3. 门禁：改完必须跑的那一条命令

```bash
bash .qoder/skills/graphics-verify/scripts/run_graphics_checks.sh
```

五条线依次是：`scripts/check_variant_tokens.py`（三轴 token 静态）、`e2e_toutiao_variants.py`（头条 A–R）、
`e2e_wechat_variants.py`（公众号 W1–W14）、`e2e_poster_layouts.py`（海报布局与多页，含攻略页与判帧）、
`e2e_sync_tasks.py`（自动同步调度与并发写）。**末行 `ALL_OK` 才算过**，任何一条非 0 都不算。

跑的时候两条硬规矩：解释器一律 `env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3`；
整条约 13 分钟，期间**别改它要读的文件**。新写断言先单独跑那一个脚本（30 秒级）验证，再进全量门禁。

## 4. 工作台：改完 .py 必须重启，而且不能 pkill

```bash
bash .qoder/skills/workbench-restart/scripts/restart_workbench.sh src/xxx.py web/app.py
```

真实进程命令行是 `.../MacOS/Python app.py`，`pkill -f "web/app.py"` **匹配不到**——
旧进程继续占端口、新进程静默退出，表现就是"改了代码没生效"。脚本按端口 PID 处置，
重启后必须看到 PID 变了 + `/api/stats` 返回 200。前端只改 `web/templates/index.html` 不用重启（每次请求现读）。

## 5. 界面改动收尾：自己走一遍，别等他截图

他的反馈方式基本只有"截图 + 一句话"（他点 UI 不利索）。所以界面类改动收尾前，
用 `scripts/agent/` 里这几个脚本把流程真走一遍（headless Chrome，`confirm()` 打桩，不花钱的那步真跑）：

| 脚本 | 干什么 |
|---|---|
| `ui_walk.py <整合稿id> "主题" --plan` | 按真实点击顺序走海报弹窗，**把每一处用户会读到的文案原样打印**＋截图；`--chain` 连逐页重拆一起跑 |
| `ui_guide_chain.py <id> [--no-plan]` | 专测「整组改成攻略页」那条链跑完的状态（版式/真帧/钱/下拉） |
| `ui_guide_proof.py <id> [--full]` | 单点取证：皮肤轮换、尾注只印末页、攻略页零件没被前端丢掉 |
| `drive_guide.py <id> "主题" 0 guide` | 走接口跑真链路（拆解 → 改攻略页 → 逐页重拆 → 出图），plan 落 `temp/guide_plan_<id>.json`，`reuse` 可 0 元重出 |
| `guide_sheet.py <产物目录> <输出jpg> 1 2 3` | 把多页成品拼成一张对照图 |
| `ui_ops_walk.py [--out 路径]` | 走「运营周报」那张表：表头/每格该不该是「—」/头条转发不许是 0/按钮被挤成多行，末行 `OPS_WALK_OK` |
| `xhs_backfill.py --file rows.json [--dry-run]` | 小红书读屏结果 → 台账。字段对照与 ref_id 规则**只有这一份** |
| `pick_hunks_guide.py <文件> <输出patch> <标记词...>` | 见下一节 |

走查抓到过的典型问题（都是这一类，值得当模板记）：界面上三个"张"混在一句话里、
"3 页都有图"是按"没有版面提示"推出来的、异步名单比页面回填晚到导致下拉停在默认值、
**判据 OR 了上次回传的标记 → 界面写 0 元而后端照样扣钱**（往会花钱那一侧撒谎，最该防的一种）、
右上控件加两个之后 `.header` 不换行把按钮挤成一列一个字（2026-10-14，量按钮高度就能抓）。

**2026-10-15 我自己犯的、必须记下来的一种**：号主问"素材库到整理稿怎么没有关键帧图片了"，
我把取图函数改成读帧池，然后回了句"改完了"。实际他那天打开工作台**一张新图都看不到**——
① 素材库详情前端从来就没渲染那个字段（`.keyframe-row` 那段 CSS 空挂了两个月）；
② 已有的 6 篇攻略整理稿里 5 篇不带配图段，要重新生成才有；
③ 我拍的"渲染证据"是把字符串手工喂给 `renderSummaryContent`，那只证明"markdown 能变成 `<img>`"，
不证明"用户点进去看得见"。**这三条我当时都写进了 CHANGELOG 的"仍未做"，却在对话里报了完成。**
教训两条：**"数据源接上了"≠"功能修好了"**，交付前得从他那一侧点一遍；
**有证据不等于证据在证明你要说的那件事**——断言的范围必须和结论的范围一致，不然"改完了"就是撒谎。
（同一件事的另一面：取帧这个能力在仓库里有 `assets/keyframes/` 和 `assets/frames/` 两本账，
谁读谁不读没人写清楚，于是"图没了"查了两天。加新账的同时就要把老账的读者迁完或写明留到什么时候。）

**每天 09:30 有一个回填定时任务**（Qoder 本地自动化，不在工作台的 `config/sync_tasks.json` 里）：
头条走 `/api/toutiao/perf/sync`（抓完自动灌台账）。
**小红书这一侧整条回填暂停**（2026-10-15 号主：先是被平台检测到自动读屏，随后明确"先暂停"）。
定时任务里已写明：绝不打开/刷新/轮询 creator.xiaohongshu.com，**也不催他发截图、不跑
`scripts/agent/xhs_backfill.py`**，除非他本人说"这次要录"。台账里小红书那几列就空着，不许用 0 或估数顶。
平台两头都只读、只写本地 sqlite。涨粉和钱这两个数**只有号主后台看得到**，任务只能提醒、不许代填。

## 6. 多人并发改这个仓库：提交怎么隔离

同时可能有几个 agent 在写**同一批文件**（`web/app.py`、`web/templates/index.html` 常年如此）。规矩：

1. 动手前先看 `git status`，判断别人是不是正在写你要碰的那两个文件。
2. 只提自己的文件；混着别人改动的文件用 `scripts/agent/pick_hunks_guide.py <文件> <out.patch> <标记词…>`
   只挑含自己标记词的 hunk → `git apply --cached <out.patch>`（**别加 `--recount`**，行数会被它算歪）。
3. 提之前把 staged blob 单独取出来编译一遍：`git show :web/app.py > /tmp/x.py && python3 -m py_compile /tmp/x.py`；
   前端把 `<script>` 整段抽出来 `node --check`（注意 `node --check` 查不出"调了个不存在的函数"）。
4. `git diff --cached` 里 grep 别人的关键词，确认没夹带；夹带了必须当场说明。
5. push 前 `git fetch` + `git merge-base --is-ancestor origin/main HEAD` 确认是快进，**永不 force**；
   push 后用 `git ls-remote origin refs/heads/main` 核对远端 SHA 真等于本地。

## 7. 红线（不可逆，先问再动）

- **仓库公开**：`config/config.env*`（Cookie / API Key）、`*.db`、`output/`、`product/`、`temp/`、
  `video_index.json` 等运行数据一律不进 git；可卖内容也不进。
- **永不自动发布**：发布动作永远是人工那一下（`media-publish` 只出清单和回填）。
- **小红书这一侧先看 `docs/小红书违规面盘点.md` 再动**：10-11、10-15 两篇笔记被判
  「AI 托管发文/互动 / 不可被他人查看」。**触发器本地证据定不了论**：我们装配的文案里确实带着
  「本文由 AI 辅助整理归纳」+「回复 X 领取…」关键词钩子（18 个成品包没一个按小红书档装配过），
  但号主说发布时不贴这两句，而**仓库里没有发布稿快照**可验；让 AI 开登录态后台读屏也违规
  （用户协议禁第三方系统使用平台），可它解释不了 10-11 那次（读屏 10-14 才开始）。
  两条规矩：① 给小红书的东西不许带尾注与关键词回复，且要在**数据层**清掉（不能只在前端隐藏），
  AIGC 声明走小红书自己的勾选位「【笔记内容声明】→【笔记含AI合成内容】」；
  ② 每篇发布前把**实际粘贴的正文**存一份到 `output/发布快照/`——下次再被判，才有一手文本可对。
- 重启 Flask、kill 进程、`git push`、改他自己写的守则、用第三方游戏素材 —— 这些要先点头。
- 写进提交说明/文档的**因果**，先做一次能证伪它的检查；写错了就专门更正并明说。

## 8. 内容口径：这些是定过的，别翻回去

| 口径 | 为什么 |
|---|---|
| 攻略页图里不许出现作者后期压的**字**（字幕条/花字/账号名/水印/贴纸/真人小窗） | 那是纯搬运的铁证，读者一眼认得 |
| 后期画的**圈/红框/箭头不算后期层**，尽量多留可用帧 | 号主 2026-10-13 定："不用管红框，多留可用帧"。这条写进了判帧提示词本身 |
| 图注**不上图**（只留在 `frames.json`/manifest 里核对用） | 那张图就在上面；再解释一遍是给编辑自己看的 |
| 免责脚注整组只印**末页**一句 | 每页一句＝批量搬运号的视觉记号 |
| 攻略页三套皮按整理稿 id 轮换，公式只在 Python 一份 | 十篇一个脸＝平台"套用模板批量生产同质化内容"那条 |
| 上图文字过**过程词闸**（口播/访谈称/据称/素材里说/UP主/评论区…） | 这些是加工痕迹，玩家不需要知道话是谁说的。**尾注不扫**、"素材没提/需自行核对"这类诚实标注**不扫** |
| 卡片小标题别全用四字格 | 三连四字读起来就是模板 |
| 稿内不出现 up主 账号名；不许编"我实测"的经历 | AI 造假那一档处置比限流重 |
| 平台合规两家口径**相反**：公众号要保留转述尾注，小红书要在发布页勾选"笔记含AI合成内容"、正文别写自证句 | 那次限流的复盘记在项目记忆里，可公开的部分就是这条结论 |
| 台账表头由后端 `COLUMN_LABELS` 下发，"这个渠道没有这个指标"画灰「—」且**不给输入框** | 写死一份在 HTML 迟早跟数据对不上；给了框就会有人填 0，0 会被读成结论（2026-10-14） |
| 小红书后台那五个数的顺序是 **浏览·评论·点赞·收藏·分享** | 对着后台图标逐个核过（2026-10-14），别凭截图印象换；换一次整列数据就错位 |
| 头条内容列表接口的 `repin`（转发）**177 条实测全 0** → 台账里头条那侧不写 shares | 那是接口不给数，不是他转发为 0；留给人工 |

## 9. 会话记忆在哪（这份仓库装不下的那部分）

Qoder 在本机还有两层记忆，**不在仓库、含收入数字与账号名，公开仓库不能放**：

- `~/.qoder-cn/memory/` —— 跨项目的用户级（沟通偏好、本机硬件与网络实测、各条产线钱与账号的实况）
- `~/.qoder-cn/projects/-Users-<user>-Documents-project-video2text/memory/` —— 只属于本仓库的
  （判帧口径与实测坑、转写层质量底数、订阅源缺口、AI 出图转向、自动同步定时器、平台流量校准、各条判决）

换工具接手时：让上一台会话把这些导成一份**脱敏摘要**放进 `docs/`，或者直接读上面那两个目录里的
`MEMORY.md` 索引 + 对应条目。别整份复制进仓库。
