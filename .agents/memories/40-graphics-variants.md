# 图文变体轴（配色/字体/版式）守则

以下约束来自 2026-09-29 两轮「选项选了不生效」报障的复盘，触碰 `src/graphics/variants.py`、`src/graphics/channels/*.py`、`src/graphics/templates.py` 时必须遵守：

## 静默失效是头号敌人（已复发两次）

- 第一次：版式预设只写封面选择器，内页图块类名不同（classic 内页是 `.band` 不是 `.hero`）→ 覆盖注入了但匹配不到元素，内页零变化。
- 第二次：`LAYOUT_PRESETS` 只登记 classic/tier/quest/guide 四模板，用户实际生成的 magazine 拿到空串 → `layout_css` 静默返回 ""，版式/随机全无效且界面无从察觉。
- **规则：任何用户可选的轴（配色/字体/版式），要么对下拉里全部模板生效，要么在前端提示文案里如实标注生效范围**（index.html 弹窗 hint）。两者都不做＝静默失效，禁止。
- 新增模板进 `templates._TEMPLATES` 时，必须同步检查 `PALETTE_READY_TPLS`、`LAYOUT_PRESETS` 各预设的 `{模板键: 片段}` 是否登记；未登记就是 no-op，要在 CHANGELOG「仍未做」里写明。

## 版式覆盖机制（改规则前先读）

- 机制＝构建后 CSS 末尾追加 `"\n/* layout:%s */\n%s"`（`default` 空串零注入），不改 HTML 结构；各模板 `.wrap-in` 是纵向 flex，子块用 `order` 重排（未指定 order 的元素按 0 即源码顺序）。
- 覆盖片段同时注入封面与内页 HTML，写规则前必须分别确认两页的真实块结构（读 `templates.py` 对应 cover/list 函数）：
  - 内页规则一律用直接子选择器 `.wrap-in>.xxx` 防嵌套误伤（如 classic 封面 `.note` 嵌在 `.cta` 里）。
  - 内页无主图的模板，「图先行」语义就近取该页主视觉块上浮（tier→`.tbadge` 放大、minimal→`.callout`、bold→`.warn`）。
  - magazine 封面默认 photo 已在 head 之前（天然图先行），`hero_first` 只放大不重排。
- 随机版式：`RANDOM_LAYOUT_KEY="random"` + `RANDOM_POOL`（只含非 default 键），整包只抽一次，抽中真实键写 `manifest.layout`，manifest 永不落 "random"。前端默认选中 `default`（经典排布），「图沉底/图上移」是 `summary_first`/`hero_first` 的**设计行为**，不是随机——用户报「图都在底部」先查 manifest.layout 再答。

## 负边距 × order 重排 = 遮挡陷阱（2026-09-29 报障「有些字被挡住了」）

- classic 内页 `h2{margin-top:-84px}` 是为「default 布局下标题压在横带图下沿」设计的叠压效果；`summary_first` 把 `.band` 沉底后 h2 失去图片托底，负边距把它拉进 `.topline` 报头行，首字被不透明金色 `.badge`（brand 徽章）盖住。
- **规则：给某模板写 order 重排规则时，必须检查该模板 CSS 里所有负 margin / 绝对定位叠压（grep `margin-top:-`），凡依赖被重排块做「托底/叠压参照」的，覆盖层里同步解除**（同特异度后来居上，如 `.wrap-in>h2{margin-top:18px}`）。e2e J 用例已含此断言。

## 移除视觉元素 = markup + CSS 双删 + 补偿

- 删徽章/色块这类元素时三件事一起做：① 渲染函数里的 markup 删掉；② 对应 CSS 规则删掉（留死样式会误导后人）；③ 若该元素承载了语义色（阵营色/状态色），把语义转移到仍在的元素上（2026-09-29 删 `.panel .ring` 后阵营色改挂 `.panel.f-alliance/.f-horde` 左侧 8px 色条）。
- 渠道级下线某内容块（如头条「提醒」）用三道防线：prompt 禁止生成 → schema 示例去掉该字段 → 主流程渲染前 `card.pop(...)` 代码级剔除（防 LLM 不听话 + 防本地兜底卡漏出）；模板渲染函数的分支保留，其他渠道不受影响。

## 用户显式输入是契约，代码级强制

- 用户在弹窗填的「图文标题」必须逐字上封面。链路：弹窗 title → app.py → `generate_graphics(title=...)` 覆盖 `summary["title"]` → prompt + 渲染。
- **禁止只在 prompt 里说「优先采用」**——实测 LLM（deepseek-v4-flash）会自行加词（「魔兽无限：…」→「魔兽无限B测：…」）。`channels/toutiao.py` 在 `sanitize_cards` 之后有代码级强制 `cards[0]["title"] = summary.get("title") or user_title[:60]`，不得移除；prompt 措辞保持「必须逐字使用，不得增删改写」。
- 推广原则：凡是「界面承诺了 X 决定 Y」的传参，落点必须有代码级保证，模型自觉只能当锦上添花。

## 验证与排查

- e2e 脚本在 work 区（不进 git）：`e2e_variants.py`，17 用例（A-Q），跑法 `cd 项目根 && env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 <脚本>`，末行必须 `ALL_OK`。改动 variants/toutiao/templates 后必跑。
- 新增覆盖规则必配断言用例：封面 HTML 断言 `/* layout:键 */` 标记 + 具体片段字符串逐字匹配；内页断言读 `img2.html`；标题类断言用 monkeypatch `toutiao._llm_cards` 模拟 LLM 改写（finally 恢复）。
- 排查「选项不生效」第一性原则顺序：① 读产物 `output/toutiao/<id>/manifest.json`（参数是否落盘）→ ② 读 `img*.html`（覆盖是否注入、选择器是否匹配）→ ③ 才怀疑前端/进程。manifest 落了参数但 HTML 无效果＝覆盖字典缺口；manifest 没落参数＝链路/进程问题（先查旧进程占端口）。
- Flask 重启纪律见 `30-python-backend.md` 与 AGENTS.md（按端口 PID 杀，禁 pkill 失配模式，重启后 lsof 换 PID + 页面特征双确认）。
