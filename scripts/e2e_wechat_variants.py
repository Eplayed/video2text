# -*- coding: utf-8 -*-
"""端到端验证：公众号渠道 generate_graphics 的 palette/font/layout 接线（临时脚本，不进 git）。

与头条线共用同一套 variants 内核，本脚本只验公众号渠道特有的部分：
  W1. 默认（不传三轴）→ manifest gold_night/serif/default，无版式/字体标记，信号色仍是微信绿，无 token 残留
  W2. ember_forge → 信号色换 #d9622b，微信绿 #07c160 彻底消失
  W3. arcane_dusk → 信号色换 #7b5fd0，微信绿消失
  W4. font=heavy → 追加片段生效（wechat 全篇黑体栈，没有 Songti 可替换，靠 tpl_css 才生效）
  W5. layout=hero_first → 封面瓦片上浮 + 内页总结条上浮（封面/内页都吃到）
  W6. layout=summary_first → 要点条上浮 + 瓦片沉底 + .ftr{order:3} 护脚（页脚不被挤出末尾）
  W7. layout=random → 抽中合法真实预设并注入对应覆盖，manifest 不落 "random"
  W8. 三轴都传未知键 → 全部回落默认，且封面 HTML 与 W1 字节全等（默认零行为变化）
  W9. minimal + heavy → minimal 专属片段生效（letter-spacing:-.015em 是 minimal 片段的指纹）
  W10. minimal + ember_forge → 未接通配色轴的模板不改色（前端 hint 如实性的代码级验证）
  W11. lilac_list 默认 → 身份键 __L_*__ 无残留、零图片（无 <img>）、基色 #5B4FE0 在位
  W12. lilac_list 三轴 → ember 换信号色、heavy 吃 .sum .sd 指纹、hero_first 封面/内页都注入
  W13. cream_gold 默认 → 身份键 __C_*__ 无残留、无题材图走 .illo blank 降级、成品无「提醒」字样
  W14. cream_gold 三轴 → ember 换 __ACCENT_DK__（身份金 #C89B42 保持常量）、heavy/summary_first 生效
"""
import shutil
import sys
from pathlib import Path

ROOT = Path("/Users/zhangyajun/Documents/project/video2text")
sys.path.insert(0, str(ROOT))

from src.graphics.channels import wechat as wx  # noqa: E402

SUMMARY = {
    "id": 99998,
    "title": "公众号三轴接线验证",
    "summary_type": "wechat_material",
    "content": ("【导语】这是一条用于接线验证的整合稿导语。\n"
                "【核心论点】论点一：配色轴换信号色。\n论点二：字体轴换重黑。\n论点三：版式轴换块序。\n"
                "【关键数据】数据一：3 组配色。\n数据二：2 组字体。\n"
                "【正文骨架】骨架一：默认零变化。\n骨架二：未知键回落。\n"
                "【金句摘录】金句一：静默失效是最贵的 bug。\n"
                "【风险核查】风险一：未接通模板选配色应保持原色。"),
}

OUT = ROOT / "output" / "wechat" / "99998"

# wechat 模板专属指纹（来自 variants.FONT_PRESETS / LAYOUT_PRESETS 的实际片段）
HEAVY_WECHAT_MARK = 'font-family:"Hiragino Sans GB","Heiti SC"'
HEAVY_MINIMAL_MARK = "letter-spacing:-.015em"


def run_case(name, **kw):
    if OUT.exists():
        shutil.rmtree(OUT)
    m = wx.generate_graphics(SUMMARY, {}, theme="AI工具实测", **kw)
    print("[%s] manifest palette=%s font=%s layout=%s template=%s imgs=%d"
          % (name, m.get("palette"), m.get("font"), m.get("layout"),
             m.get("template"), len(m.get("images") or [])))
    html1 = (OUT / "img1.html").read_text(encoding="utf-8")
    html2 = (OUT / "img2.html").read_text(encoding="utf-8")
    return m, html1, html2


def main():
    ok = True

    # W1 默认组合
    m, html, html2 = run_case("W1 默认wechat")
    if (m.get("palette"), m.get("font"), m.get("layout")) != ("gold_night", "serif", "default"):
        print("  ✗ manifest 默认组合不对"); ok = False
    elif "/* layout:" in html or "/* font:" in html:
        print("  ✗ 默认组合不应注入版式/字体标记"); ok = False
    elif "#07c160" not in html:
        print("  ✗ 默认信号色微信绿 #07c160 丢失"); ok = False
    elif "__PAPER__" in html or "__ACCENT__" in html or "__MDIM__" in html:
        print("  ✗ wechat HTML 残留未替换 token"); ok = False
    else:
        print("  ✓ 默认 gold_night/serif/default，微信绿在位、无标记、无 token 残留")
    html_default = html

    # W2/W3 配色轴
    for key, accent, name in (("ember_forge", "#d9622b", "W2 熔火暖橙"),
                              ("arcane_dusk", "#7b5fd0", "W3 暮光紫")):
        m, html, html2 = run_case(name, palette=key)
        if m.get("palette") != key:
            print("  ✗ manifest palette 字段不对"); ok = False
        elif accent not in html:
            print("  ✗ 未吃到 %s 信号色 %s" % (key, accent)); ok = False
        elif "#07c160" in html or "rgba(7,193,96" in html:
            print("  ✗ 微信绿残留（信号色家族没换干净）"); ok = False
        elif "#ffffff" not in html:
            print("  ✗ 白卡底色丢失（纸面应保持浅底白卡）"); ok = False
        else:
            print("  ✓ %s 换信号色生效，微信绿无残留，浅纸面白卡保持" % key)

    # W4 字体轴（wechat 模板）
    m, html, html2 = run_case("W4 硬朗黑体", font="heavy")
    if m.get("font") != "heavy":
        print("  ✗ manifest font 字段不对"); ok = False
    elif "/* font:heavy */" not in html:
        print("  ✗ 未注入字体追加片段标记"); ok = False
    elif HEAVY_WECHAT_MARK not in html:
        print("  ✗ wechat 未吃到重黑字体族（tpl_css 片段静默失效）"); ok = False
    elif "font-weight:900" not in html:
        print("  ✗ 重黑字重未生效"); ok = False
    elif "/* font:heavy */" not in html2:
        print("  ✗ 内页未吃到字体片段"); ok = False
    else:
        print("  ✓ heavy 对 wechat 真实生效（追加片段：重黑字体族 + 900 字重），封面内页都吃到")

    # W5 hero_first
    m, html, html2 = run_case("W5 主视觉先行", layout="hero_first")
    if m.get("layout") != "hero_first" or "/* layout:hero_first */" not in html:
        print("  ✗ hero_first 未记录/未注入"); ok = False
    elif ".tiles{order:-2}" not in html:
        print("  ✗ 封面瓦片未上浮（.tiles{order:-2} 缺失）"); ok = False
    elif ".wrap-in>.note{order:-2" not in html2:
        print("  ✗ 内页总结条未上浮（内页缺口）"); ok = False
    elif ".ftr{order:" in html:
        print("  ✗ hero_first 不应给页脚正 order（会把页脚挤出末尾）"); ok = False
    else:
        print("  ✓ hero_first 封面瓦片上浮 + 内页总结条上浮，页脚未被干扰")

    # W6 summary_first（含护脚断言）
    m, html, html2 = run_case("W6 要点先行", layout="summary_first")
    if "/* layout:summary_first */" not in html:
        print("  ✗ summary_first 未注入"); ok = False
    elif ".rows{order:-2" not in html2:
        print("  ✗ 内页要点条未上浮（.rows{order:-2} 缺失）"); ok = False
    elif ".tiles{order:1;align-content:end}" not in html:
        print("  ✗ 封面瓦片未沉底"); ok = False
    elif ".ftr{order:3}" not in html:
        print("  ✗ 页脚护脚规则缺失（瓦片沉底会把页脚挤出末尾）"); ok = False
    else:
        print("  ✓ summary_first 要点上浮 + 瓦片沉底 + 页脚 order:3 护脚")

    # W7 random
    from src.graphics import variants as _v
    seen = set()
    for i in range(3):
        m, html, html2 = run_case("W7 随机版式%d" % (i + 1), layout="random")
        lk = m.get("layout")
        if lk not in _v.RANDOM_POOL:
            print("  ✗ 随机抽中非法键：%r" % lk); ok = False; break
        if "/* layout:%s */" % lk not in html:
            print("  ✗ 随机抽中 %s 但未注入对应覆盖" % lk); ok = False; break
        seen.add(lk)
    else:
        print("  ✓ random 3 次均抽中合法预设并注入覆盖，manifest 落真实键（seen=%s）" % sorted(seen))

    # W8 未知键回落（与 W1 字节全等＝默认零行为变化）
    m, html, html2 = run_case("W8 未知键回落", palette="no_such", font="no_such", layout="no_such")
    if (m.get("palette"), m.get("font"), m.get("layout")) != ("gold_night", "serif", "default"):
        print("  ✗ 未知键未回落默认组合"); ok = False
    elif html != html_default:
        print("  ✗ 未知键封面 HTML 与默认组合不一致（回落不彻底）"); ok = False
    else:
        print("  ✓ 三轴未知键全部回落默认，封面 HTML 与默认组合字节全等")

    # W9 minimal + heavy（字体轴对全黑体栈模板的另一半验证）
    m, html, html2 = run_case("W9 minimal硬朗黑体", template="minimal", font="heavy")
    if "/* font:heavy */" not in html or HEAVY_MINIMAL_MARK not in html:
        print("  ✗ minimal 未吃到 heavy 专属片段（静默失效）"); ok = False
    else:
        print("  ✓ heavy 对 minimal 真实生效（minimal 专属片段指纹在位）")

    # W10 minimal 未接通配色轴（hint 如实性）
    m, html, html2 = run_case("W10 minimal配色保护", template="minimal", palette="ember_forge")
    if "#d9622b" in html:
        print("  ✗ minimal 未接通配色轴却被改色（会串色）"); ok = False
    else:
        print("  ✓ minimal 忽略配色预设保持原色，与前端 hint 一致")

    # W11 lilac_list 默认（身份键无残留 + 零图片模板）
    m, html, html2 = run_case("W11 lilac默认", template="lilac_list")
    resid = [t for t in ("__PAPER__", "__ACCENT__", "__L_GRID__", "__L_STAR__",
                         "__L_PANEL__", "__L_PANELTXT__", "__L_NUMBG__")
             if t in html or t in html2]
    if m.get("template") != "lilac_list":
        print("  ✗ manifest template 字段不对"); ok = False
    elif resid:
        print("  ✗ lilac_list 残留未替换 token：%s" % resid); ok = False
    elif "#5B4FE0" not in html:
        print("  ✗ lilac 基色信号紫 #5B4FE0 丢失"); ok = False
    elif "<img" in html or "<img" in html2:
        print("  ✗ lilac_list 是零图片模板，不应出现 <img>"); ok = False
    else:
        print("  ✓ lilac_list 默认无 token 残留、零图片、基色在位")

    # W12 lilac_list 三轴（ember + heavy + hero_first）
    m, html, html2 = run_case("W12 lilac三轴", template="lilac_list",
                              palette="ember_forge", font="heavy", layout="hero_first")
    if "#d9622b" not in html or "#5B4FE0" in html:
        print("  ✗ lilac 未吃到 ember 信号色（或基色残留）"); ok = False
    elif ".sum .sd{font-weight:900}" not in html:
        print("  ✗ lilac 未吃到 heavy 专属片段（.sum .sd 指纹缺失＝静默失效）"); ok = False
    elif ".stats{order:-2;margin-top:30px;padding-top:0}" not in html:
        print("  ✗ lilac 封面 hero_first 片段未注入"); ok = False
    elif ".wrap-in>.note{order:-2" not in html2:
        print("  ✗ lilac 内页 hero_first 片段未注入（内页缺口）"); ok = False
    else:
        print("  ✓ lilac_list 三轴全生效（换色/重黑指纹/封面内页版式注入）")

    # W13 cream_gold 默认（身份键无残留 + 无图降级 + 「提醒」回归守护）
    m, html, html2 = run_case("W13 cream默认", template="cream_gold")
    resid = [t for t in ("__PAPER__", "__ACCENT_DK__", "__C_TILE__", "__C_DOTS__",
                         "__C_NUM__", "__C_VAL__", "__C_ROW__")
             if t in html or t in html2]
    if m.get("template") != "cream_gold":
        print("  ✗ manifest template 字段不对"); ok = False
    elif resid:
        print("  ✗ cream_gold 残留未替换 token：%s" % resid); ok = False
    elif "#C89B42" not in html or "#A8762A" not in html:
        print("  ✗ cream 身份金/金棕值丢失"); ok = False
    elif "illo blank" not in html:
        print("  ✗ 无题材图未走 .illo blank 金色装饰降级"); ok = False
    elif "提醒" in html or "提醒" in html2:
        print("  ✗ 成品出现「提醒」字样（用户明令移除）"); ok = False
    else:
        print("  ✓ cream_gold 默认无 token 残留、无图降级正常、无「提醒」字样")

    # W14 cream_gold 三轴（ember + heavy + summary_first；身份金保持常量）
    m, html, html2 = run_case("W14 cream三轴", template="cream_gold",
                              palette="ember_forge", font="heavy", layout="summary_first")
    if "#a9451a" not in html or "#fbf6f1" not in html:
        print("  ✗ cream 未吃到 ember 共享键覆盖（__ACCENT_DK__/__PAPER__）"); ok = False
    elif "#C89B42" not in html:
        print("  ✗ 身份金 __C_NUM__ 应保持常量，不随配色预设变化"); ok = False
    elif ".numrow .no," not in html or "/* font:heavy */" not in html:
        print("  ✗ cream 未吃到 heavy 专属片段（.numrow .no 指纹缺失）"); ok = False
    elif ".sub{order:-1;margin-top:0;margin-bottom:22px}" not in html:
        print("  ✗ cream 封面 summary_first 片段未注入"); ok = False
    elif ".wrap-in>.note{order:-2;margin-top:24px}" not in html2:
        print("  ✗ cream 内页 summary_first 片段未注入（内页缺口）"); ok = False
    else:
        print("  ✓ cream_gold 三轴全生效（共享键换色/身份金常量/重黑指纹/版式注入）")

    shutil.rmtree(OUT, ignore_errors=True)
    print("\n%s" % ("ALL_OK" if ok else "HAS_FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
