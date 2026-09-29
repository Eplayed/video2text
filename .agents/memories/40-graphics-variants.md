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
- 随机版式：`RANDOM_LAYOUT_KEY="random"` + `RANDOM_POOL`（只含非 default 键），整包只抽一次，抽中真实键写 `manifest.layout`，manifest 永不落 "random"。

## 用户显式输入是契约，代码级强制

- 用户在弹窗填的「图文标题」必须逐字上封面。链路：弹窗 title → app.py → `generate_graphics(title=...)` 覆盖 `summary["title"]` → prompt + 渲染。
- **禁止只在 prompt 里说「优先采用」**——实测 LLM（deepseek-v4-flash）会自行加词（「魔兽无限：…」→「魔兽无限B测：…」）。`channels/toutiao.py` 在 `sanitize_cards` 之后有代码级强制 `cards[0]["title"] = summary.get("title") or user_title[:60]`，不得移除；prompt 措辞保持「必须逐字使用，不得增删改写」。
- 推广原则：凡是「界面承诺了 X 决定 Y」的传参，落点必须有代码级保证，模型自觉只能当锦上添花。

## 验证与排查

- e2e 脚本在 work 区（不进 git）：`e2e_variants.py`，15 用例（A-O），跑法 `cd 项目根 && env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 <脚本>`，末行必须 `ALL_OK`。改动 variants/toutiao 后必跑。
- 新增覆盖规则必配断言用例：封面 HTML 断言 `/* layout:键 */` 标记 + 具体片段字符串逐字匹配；内页断言读 `img2.html`；标题类断言用 monkeypatch `toutiao._llm_cards` 模拟 LLM 改写（finally 恢复）。
- 排查「选项不生效」第一性原则顺序：① 读产物 `output/toutiao/<id>/manifest.json`（参数是否落盘）→ ② 读 `img*.html`（覆盖是否注入、选择器是否匹配）→ ③ 才怀疑前端/进程。manifest 落了参数但 HTML 无效果＝覆盖字典缺口；manifest 没落参数＝链路/进程问题（先查旧进程占端口）。
- Flask 重启纪律见 `30-python-backend.md` 与 AGENTS.md（按端口 PID 杀，禁 pkill 失配模式，重启后 lsof 换 PID + 页面特征双确认）。
