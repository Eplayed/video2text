# video2text 改动说明（给其他 AI / Agent）

更新时间：2026-09-29

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
