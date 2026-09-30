---
name: graphics-verify
description: 跑头条/公众号图文生成链路的回归验证（三轴 token 静态检查 + 两条 e2e 线）。改动 src/graphics/ 下任何文件（variants.py、templates.py、css_engine.py、skins.py、channels/*.py、renderer.py、package.py）后必须执行；用户报"配色/字体/版式选了没效果""每次都是同一张图""标题被挡住"时也用它取证。
---

# 图文变体回归验证

## Overview

`src.graphics` 是头条与公众号共用的渲染内核，变体三轴（配色 palette / 字体 font / 版式 layout）靠 token 表与 CSS 覆盖片段生效，**极易静默失效**（已复发三次：选择器不匹配、模板未登记、字体栈无锚点）。本技能把三条验证线一次跑完，任何一条非 `ALL_OK` 都不算通过。

## 用法

```bash
bash .qoder/skills/graphics-verify/scripts/run_graphics_checks.sh
```

依次跑：

1. `scripts/check_variant_tokens.py` — 三轴 × 全模板逐组合实测「CSS 是否真的变了 / token 是否残留」，字体轴与配色轴失效即报错（不是 warn）。
2. `scripts/e2e_toutiao_variants.py` — 头条线 18 用例（A–R），含「默认三轴产物字节全等」硬断言。
3. `scripts/e2e_wechat_variants.py` — 公众号线 14 用例（W1–W14），含 lilac_list/cream_gold 身份色常量断言。

两条 e2e 会往 `output/toutiao/99999`、`output/wechat/99998` 写产物并自行清理；脚本内部用 `env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3`，别用别的解释器。改了 `main.py`/`content_store` 的取稿逻辑也要跑（e2e 依赖整合稿结构）。

## 失败排查顺序（第一性原则）

1. 读 `output/toutiao/<id>/manifest.json`（公众号 `output/wechat/<id>/manifest.json`）看参数是否落盘。
2. 参数落盘但 HTML 无效果 = 覆盖字典缺口：读 `img1.html`/`img2.html` 找 `/* layout:键 */`、`/* font:键 */` 标记，核对选择器是否命中真实类名。
3. manifest 没落参数 = 链路或进程问题，先按 `workbench-restart` 确认新进程真的接管。

## 改动守则（勿违反）

- 新模板进 `templates._TEMPLATES` 时，同步登记 `PALETTE_READY_TPLS`（= `_TPL_BASE_TOKENS` 键集）、`LAYOUT_PRESETS` 各预设的模板片段、`FONT_PRESETS` 的 `tpl_css`；未登记就是 no-op，必须在 CHANGELOG「仍未做」写明，或在前端 hint 里如实标注生效范围。两者都不做＝静默失效，禁止。
- 任一预设必须保持「默认零变化」：基线 token 表与 `css_engine._tpl_tokens` 字面量逐字相等，改一侧要同步另一侧。
- 版式覆盖片段要连带核对被重排块的 `flex` 基准、`margin-top:auto`、`padding` 与负 `margin` 叠压；这类盒模型连锁问题脚本测不出，须跑样张目视（生成 `img*.jpg` 后看留白是否均衡、有无遮挡）。
- 用户显式输入（弹窗标题）必须有代码级强制，不能只写进 prompt。

## Resources

- `scripts/run_graphics_checks.sh`：三条线串行执行，全绿末行 `ALL_OK`，否则非 0 退出并打印失败上下文尾部。
