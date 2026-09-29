# 图文变体轴（配色/字体/版式）守则

以下约束来自 2026-09-29 三轮「选项选了不生效」报障/复盘，触碰 `src/graphics/variants.py`、`src/graphics/channels/*.py`、`src/graphics/templates.py` 时必须遵守：

## 静默失效是头号敌人（已复发三次）

- 第一次：版式预设只写封面选择器，内页图块类名不同（classic 内页是 `.band` 不是 `.hero`）→ 覆盖注入了但匹配不到元素，内页零变化。
- 第二次：`LAYOUT_PRESETS` 只登记 classic/tier/quest/guide 四模板，用户实际生成的 magazine 拿到空串 → `layout_css` 静默返回 ""，版式/随机全无效且界面无从察觉。
- 第三次：**字体轴对 wechat/minimal 全程 no-op**——`apply_font` 靠「Songti 衬线栈正则替换」实现，这两个模板 CSS 全篇黑体栈，无锚点可匹配 → 原样返回 CSS，选「重黑」毫无变化（头条线选 minimal 同样中招）。教训：**基于「文本替换/选择器匹配」生效的机制，必须先确认目标模板里真的有可匹配的锚点**，不能假设所有模板同构。
- **规则：任何用户可选的轴（配色/字体/版式），要么对下拉里全部模板生效，要么在前端提示文案里如实标注生效范围**（index.html 弹窗 hint + 确认行文案，如公众号侧 `palNote`）。两者都不做＝静默失效，禁止。
- 新增模板进 `templates._TEMPLATES` 时，必须同步检查 `PALETTE_READY_TPLS`（＝`_TPL_BASE_TOKENS` 键集）、`LAYOUT_PRESETS` 各预设的 `{模板键: 片段}`、`FONT_PRESETS` 各预设的 `tpl_css`（仅全篇黑体栈的模板需要）是否登记；未登记就是 no-op，要在 CHANGELOG「仍未做」里写明。
- **防线已代码化**：`scripts/check_variant_tokens.py`（进 git）对三轴逐组合实测「CSS 是否真的变化 / token 是否有残留」，**字体轴与配色轴失效即报错**（不再只 warn）。改 variants/templates/css_engine 后必跑。

## 字体轴双路机制（2026-09-29 起）

- 两路并存，一路都不能少：① **正则字体栈替换**——把模板 CSS 里的 Songti 衬线栈换成预设栈（对 classic/magazine/bold/guide/tier/quest 生效）；② **按模板追加片段** `FONT_PRESETS[键]["tpl_css"][tpl_key]`——对无衬线锚点的模板（wechat/minimal）在 CSS 末尾追加 `/* font:键 */` 片段。
- **调用方必须传 `tpl_key`**：`apply_font(css, font_key, tpl_key)`，两个渠道适配器（toutiao/wechat）都要传，少传即静默 no-op。
- 追加片段的写法约束：只动 `font-family`/`font-weight`/`letter-spacing`，**绝不动盒模型**（宽高/padding/margin 一动就重排版式，与版式轴打架）；字重变大后字宽也变大，顺手收紧字距（-.01em ~ -.015em）抵换行风险。
- 重黑字体选型踩坑记录：**PingFang SC 最粗只到 Semibold，写 `font-weight:900` 在 Chromium 里视觉无感**；本机 `/System/Library/Fonts/Hiragino Sans GB.ttc` 的 W6 是真重黑。栈写 `'"Hiragino Sans GB","Heiti SC","PingFang SC",sans-serif'`，缺字体时回落 PingFang 不炸。

## 版式覆盖机制（改规则前先读）

- 机制＝构建后 CSS 末尾追加 `"\n/* layout:%s */\n%s"`（`default` 空串零注入），不改 HTML 结构；各模板 `.wrap-in` 是纵向 flex，子块用 `order` 重排（未指定 order 的元素按 0 即源码顺序）。
- 覆盖片段同时注入封面与内页 HTML，写规则前必须分别确认两页的真实块结构（读 `templates.py` 对应 cover/list 函数）：
  - 内页规则一律用直接子选择器 `.wrap-in>.xxx` 防嵌套误伤（如 classic 封面 `.note` 嵌在 `.cta` 里）。
  - 内页无主图的模板，「图先行」语义就近取该页主视觉块上浮（tier→`.tbadge` 放大、minimal→`.callout`、bold→`.warn`）。
  - magazine 封面默认 photo 已在 head 之前（天然图先行），`hero_first` 只放大不重排。
- 随机版式：`RANDOM_LAYOUT_KEY="random"` + `RANDOM_POOL`（只含非 default 键），整包只抽一次，抽中真实键写 `manifest.layout`，manifest 永不落 "random"。前端默认选中 `default`（经典排布），「图沉底/图上移」是 `summary_first`/`hero_first` 的**设计行为**，不是随机——用户报「图都在底部」先查 manifest.layout 再答。

## 版式片段的盒模型连锁坑（2026-09-29 新增 lilac_list/cream_gold 时踩实）

- **上浮块必须解除 `flex:1`**：cream_gold 内页 `.steps` 基础规则是 `flex:1`（吃掉纵向余高）；`hero_first` 片段把它 order 上浮时若不写 `flex:0 0 auto`，上浮块仍占满剩余高度、把后面内容顶出画布。凡对「基础规则含 flex:1 的块」做 order 重排，覆盖片段里同步改 flex 基准。
- **沉底兜底靠页脚 `margin-top:auto`**：上浮打乱源码顺序后，页面底部留白失控；cream_gold 用 `.ftr{margin-top:auto}` 把页脚钉在画布底，写「图先行」类片段时必须带上。
- **多个 `margin-top:auto` 均分余高**：lilac_list 封面初版只有 `.sum{margin-top:auto}` 独占余高 → 中部出现大白带（样张目视才发现，token 检查全绿也测不出）。修法：基础规则改 `.stats{margin-top:auto;padding-top:42px}`，让 `.stats` 与 `.sum` 两个 auto 边距均分余高、padding 保底间距。**版式覆盖片段里的 `.stats` 规则要显式 `padding-top:0` 重置**——片段只写 order/margin 时基础 padding 会残留，叠加出额外空白。
- **教训推广**：版式覆盖片段不是「只写 order 就完事」，必须把被重排块的 flex 基准、auto 边距、padding 一并核对；这类盒模型连锁问题 check 脚本测不出，**必须跑样张目视**（`gen_*_previews.py` 出图后人眼看留白是否均衡）。

## 模板身份键 = 常量，不进配色预设（2026-09-29 起）

- `LILAC_LIST_BASE_TOKENS` 的 `__L_GRID__/__L_STAR__/__L_PANEL__/__L_PANELTXT__/__L_NUMBG__` 与 `CREAM_GOLD_BASE_TOKENS` 的 `__C_TILE__/__C_DOTS__/__C_NUM__/__C_VAL__/__C_ROW__` 是**模板身份装饰键**：只存 base 表（值＝默认 CSS 字面量，保「默认零变化」），**任何配色预设都不覆盖它们**——浅紫的星格底纹、奶油金的点阵/编号金是模板辨识度本身，随预设换色就不成其为「浅紫清单/奶油金」了。
- 随预设级联的只有共享键：`__PAPER__/__INK__/__MDIM__/__LINE__/__CARD__/__ACCENT_DK__`（lilac 另有 `__ACCENT__`）。**cream_gold 没有 `__ACCENT__` 键（CSS 里就不存在，不要补，补了 check 脚本判「漏定义」）**，e2e 换色断言落 `__ACCENT_DK__`＋`__PAPER__`；反向断言身份金 `#C89B42`（`__C_NUM__`）在换色后仍逐字在位。
- 新模板接配色轴时先分家：哪些键是「皮肤语义」（进预设级联）、哪些是「模板身份」（锁死常量），e2e 两个方向都要断言。

## 负边距 × order 重排 = 遮挡陷阱（2026-09-29 报障「有些字被挡住了」）

- classic 内页 `h2{margin-top:-84px}` 是为「default 布局下标题压在横带图下沿」设计的叠压效果；`summary_first` 把 `.band` 沉底后 h2 失去图片托底，负边距把它拉进 `.topline` 报头行，首字被不透明金色 `.badge`（brand 徽章）盖住。
- **规则：给某模板写 order 重排规则时，必须检查该模板 CSS 里所有负 margin / 绝对定位叠压（grep `margin-top:-`），凡依赖被重排块做「托底/叠压参照」的，覆盖层里同步解除**（同特异度后来居上，如 `.wrap-in>h2{margin-top:18px}`）。e2e J 用例已含此断言。

## 移除视觉元素 = markup + CSS 双删 + 补偿

- 删徽章/色块这类元素时三件事一起做：① 渲染函数里的 markup 删掉；② 对应 CSS 规则删掉（留死样式会误导后人）；③ 若该元素承载了语义色（阵营色/状态色），把语义转移到仍在的元素上（2026-09-29 删 `.panel .ring` 后阵营色改挂 `.panel.f-alliance/.f-horde` 左侧 8px 色条）。
- 渠道级下线某内容块（如头条「提醒」）用三道防线：prompt 禁止生成 → schema 示例去掉该字段 → 主流程渲染前 `card.pop(...)` 代码级剔除（防 LLM 不听话 + 防本地兜底卡漏出）；模板渲染函数的分支保留，其他渠道不受影响。

## 素材选图随机化（2026-09-29 起）

- `skins._pick_cover_image(paths, rng=None)` / `_pick_band_image(paths, idx, rng=None, exclude=None)` 的 `rng=None` 分支是**旧确定行为**（封面取比例最大竖图、横带按比例升序固定轮换），公众号等既有调用点依赖它，**不得删除或改语义**；随机走 `rng` 分支（封面在竖构图池 >=1.0 里抽、池空回落全池；横带在 `exclude` 之外的未用素材里抽，耗尽才允许重复）。
- 随机源必须每次生成新建一个（`toutiao._make_rng()`），并在渲染循环里累积 `used_assets`（封面 + 各页横带）当排除集——否则同包多页会反复抽到同一张图。
- **guide 模板永不走随机**：`<题材>攻略图/` 是用户上传的有序素材（地图/路线/资料图），顺序即语义。
- 随机化必须配「可复现 + 可追溯」两件套：模块级 `_RNG_SEED`（生产 None，测试设整数）+ manifest `assets` 字段（本包用到的素材文件名，首个为封面）。**跑 e2e 前必须钉死种子**，否则「两次生成字节全等」类断言（D 用例）会误报。
- 报障「每次都是同一张图」先查：① 素材目录里有几张图（`output/toutiao/<题材>图片素材/`）；② manifest `assets`；③ 是不是 guide 模板（设计如此）。

## 用户显式输入是契约，代码级强制

- 用户在弹窗填的「图文标题」必须逐字上封面。链路：弹窗 title → app.py → `generate_graphics(title=...)` 覆盖 `summary["title"]` → prompt + 渲染。
- **禁止只在 prompt 里说「优先采用」**——实测 LLM（deepseek-v4-flash）会自行加词（「魔兽无限：…」→「魔兽无限B测：…」）。`channels/toutiao.py` 在 `sanitize_cards` 之后有代码级强制 `cards[0]["title"] = summary.get("title") or user_title[:60]`，不得移除；prompt 措辞保持「必须逐字使用，不得增删改写」。
- 推广原则：凡是「界面承诺了 X 决定 Y」的传参，落点必须有代码级保证，模型自觉只能当锦上添花。

## 渠道分工（头条 vs 公众号，2026-09-29 核查）

- **共用同一套渲染内核**：`src.graphics` 的 skins / css_engine / templates（同一 `_TEMPLATES` 注册表）/ renderer / package，两个渠道适配器 `channels/toutiao.py`、`channels/wechat.py` 只在渠道层组装差异。用户问「是不是同一套框架」＝是，内核同源。
- **头条**：7 模板全开、变体三轴（palette/font/layout）已接、素材选图已随机、题材插画作主视觉（`<题材>图片素材/`）。
- **公众号**：白名单 6 套 wechat/minimal/classic/magazine/lilac_list/cream_gold（后两套为 2026-09-29 参考样本拆解新增，越界回落 wechat）；默认 `template="wechat"` 信号格；主视觉来自用户提供的 `real_screenshots`（≥2 张铁律 lint + 10MB 上限，截图会复制进包）；`author_draft` 非空＝亲测模式、空＝转载模式；**变体三轴已接（2026-09-29）**，素材随机**有意不接**（主视觉是用户真实截图，随机换图无意义）。
- **公众号 hero 三分法**（`channels/wechat.py`，改选图逻辑先读）：① `no_hero_tpls=("wechat","lilac_list")` 封面内页恒 `hero=None`（零素材图依赖，纯 CSS 装饰，防游戏背景渗入）；② `cream_gold` 封面只吃题材插画图 `cover_img`（无素材则 None → 模板降级 `.illo.blank` 金色点阵装饰块），内页恒 None（满幅不留插画位）；③ 其余模板走 `skins._hero_uri` / `_pick_band_image` 旧路径。
- 公众号三轴落地要点（改这块前先读）：① 配色靠 `WECHAT_BASE_TOKENS`（9 键，默认值与 `css_engine._tpl_tokens("wechat")` 字面量**逐字相等**＝默认零变化）+ `_TPL_BASE_TOKENS["wechat"]` 登记，各预设再补 wechat tokens；lilac_list/cream_gold 同法各有 base 表（身份键锁常量，见「模板身份键」节）；**可读性硬约束：任何预设都保持「浅纸面 + 深墨字 + 白卡」，只换信号色家族，禁止套用头条深色夜底**。② 版式片段用各模板自己的类名（wechat 是 `.rhead`/`.sig`/`.kick`/`.tiles`/`.rows`/`.ftr`；lilac_list 内页主视觉是 `.note`；cream_gold 是 `.steps`/`.numrow`/`.sub`），**不能跨模板复用选择器**；「图沉底」类预设需给 `.ftr` 补 order/margin 护脚。③ 字体走 `tpl_css` 追加片段（见「字体轴双路机制」节），lilac/cream 全篇黑体栈、正则路恒 no-op，只能靠追加路。④ 前端两处如实提示：弹窗 hint + `confirmWechatGen` 确认行 `palNote`（选了非默认配色但模板未接通配色轴时明示「仍按默认色出图」）。
- 未接通配色轴的模板：`minimal`/`magazine`/`bold`（前端已如实提示；配色轴现共接通 7 模板＝classic/tier/quest/guide/wechat/lilac_list/cream_gold）。要接按 `WECHAT_BASE_TOKENS` 同法：补基线 token 表 → 登记 `_TPL_BASE_TOKENS` → 各预设补该模板 tokens → 跑 `scripts/check_variant_tokens.py`。

## 验证与排查

- e2e 脚本在 work 区（不进 git）：头条线 `e2e_variants.py`（18 用例 A-R）、公众号线 `e2e_wechat_variants.py`（14 用例 W1-W14；W11-W14 覆盖 lilac_list/cream_gold 的默认零变化＋三轴组合，断言 token 零残留、身份色在位/常量不随预设变、封面内页双页零 `<img`、「提醒」零回归），跑法 `cd 项目根 && env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 <脚本>`，末行必须 `ALL_OK`。改动 variants/templates/css_engine 后**两条线都要跑**（同一内核）；改 toutiao/skins 跑头条线，改 wechat 跑公众号线。头条脚本 `main()` 开头会钉死 `toutiao._RNG_SEED`（素材随机化的前置条件）；公众号渠道选图未随机，无种子依赖。
- 两条线都必含的两类硬断言：① **默认零变化**——三轴传未知键/默认键时，产物 HTML 与基线用例**字节全等**（依赖各模板 `*_BASE_TOKENS` 与 `css_engine._tpl_tokens` 字面量逐字相等，改任一侧都要同步）；② **信号色替换彻底**——旧色十六进制与 `rgba(` 形式都零残留。
- 新增覆盖规则必配断言用例：封面 HTML 断言 `/* layout:键 */` 标记 + 具体片段字符串逐字匹配；内页断言读 `img2.html`；标题类断言用 monkeypatch `toutiao._llm_cards` 模拟 LLM 改写（finally 恢复）。
- 排查「选项不生效」第一性原则顺序：① 读产物 `output/toutiao/<id>/manifest.json`（公众号是 `output/wechat/<id>/manifest.json`）看参数是否落盘 → ② 读 `img*.html`（覆盖是否注入、选择器是否匹配）→ ③ 才怀疑前端/进程。manifest 落了参数但 HTML 无效果＝覆盖字典缺口（或字体轴漏传 `tpl_key`）；manifest 没落参数＝链路/进程问题（先查旧进程占端口）。
- Flask 重启纪律见 `30-python-backend.md` 与 AGENTS.md（按端口 PID 杀，禁 pkill 失配模式，重启后 lsof 换 PID + 页面特征双确认）。
