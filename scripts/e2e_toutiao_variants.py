# -*- coding: utf-8 -*-
"""端到端验证：generate_graphics 的 palette/font/layout 接线（临时脚本，不进 git）。

用例：
  A. 默认（不传 palette/font/layout）→ manifest 应为 gold_night/serif/default，CSS 无 token 残留、无版式标记
  B. tier + ember_forge + heavy → manifest 记录正确，HTML 应含熔火色 #241009
  C. magazine 未接通配色轴保护
  D. 显式 layout=default 与不传 layout 的封面 HTML 字节全等（默认零行为变化）
  E. classic + hero_first → manifest 记录正确，HTML 含版式标记与 order 覆盖
  F. 未知 layout 键 → 回落 default，无版式标记
  G. quest + summary_first → 含版式标记；且封面含 div.ifact 横向容器（ifact 修复断言）
  H. guide 封面（源攻略图比例 1.78 >= 1.6）→ 封面含 div.gfull 满铺整图；内页不含
  I. guide 封面比例 < 1.6（monkeypatch _img_ratio 返回 1.0）→ 回落旧结构，无 div.gfull
  J. classic 内页（img2）也吃到 summary_first 重排（.band 沉底 + .note 压尾）——内页缺口修复断言
  K. tier 内页（img2）吃到 summary_first 重排（tgrid 上浮 + thead 下沉）
  L. layout="random" → 抽中合法真实预设、注入对应覆盖、manifest 不落 "random"（多次抽样）
  M. magazine + summary_first → 封面吃到 photo/index 重排、内页吃到 lband/note-q 重排（全模板覆盖修复断言）
  N. minimal/bold + hero_first → 主图上移放大规则注入
  O. 显式 title → 封面卡 title 被代码级强制为用户标题（monkeypatch LLM 返回改写标题，仍应被覆盖）
  P. 头条渠道成品图不再渲染「提醒」小结块（兜底卡自带 note，渲染前代码级剔除）
  Q. 条目行不再显示「首字」徽章（classic 圆环 .ring / quest 圆徽 .ico，markup+CSS 双清）
  R. 素材选图随机内核（rng=None 旧行为不变；随机封面多样化、横带避开已用素材）+ manifest.assets 可追溯
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path("/Users/zhangyajun/Documents/project/video2text")
sys.path.insert(0, str(ROOT))

from src.graphics.channels import toutiao  # noqa: E402

SUMMARY = {
    "id": 99999,
    "title": "接线验证：变体轴端到端测试",
    "summary_type": "toutiao_mix",
    "content": "变化：测试内容一。\n影响：测试内容二。\n行动：测试内容三。",
}

OUT = ROOT / "output" / "toutiao" / "99999"


def run_case(name, **kw):
    if OUT.exists():
        shutil.rmtree(OUT)
    m = toutiao.generate_graphics(SUMMARY, {}, theme="魔兽世界-正式服", **kw)
    print("[%s] manifest palette=%s font=%s layout=%s template=%s imgs=%d"
          % (name, m.get("palette"), m.get("font"), m.get("layout"),
             m.get("template"), len(m.get("images") or [])))
    html1 = (OUT / "img1.html").read_text(encoding="utf-8")
    return m, html1


def main():
    ok = True

    # 素材选图自 2026-09-29 起随机化：跑用例前把种子钉死，保证 D 等「字节全等」断言可复现。
    # 随机本身是否真的到达渠道层，由 R3 用例单独验证（临时换种子比对 assets）。
    toutiao._RNG_SEED = 20260929

    m, html = run_case("A 默认classic", skin="wow", template="classic")
    if m.get("palette") != "gold_night" or m.get("font") != "serif" or m.get("layout") != "default":
        print("  ✗ manifest 默认组合不对"); ok = False
    if "__T_" in html or "__Q_" in html or "__G_" in html or "__GLOW__" in html:
        print("  ✗ classic HTML 残留未替换 token"); ok = False
    elif "/* layout:" in html:
        print("  ✗ 默认版式不应注入版式覆盖标记"); ok = False
    else:
        print("  ✓ 默认组合 gold_night/serif/default，无 token 残留、无版式标记")
    html_default_implicit = html

    m, html = run_case("B tier熔火黑体", skin="wow", template="tier",
                       palette="ember_forge", font="heavy")
    if m.get("palette") != "ember_forge" or m.get("font") != "heavy":
        print("  ✗ manifest 变体字段不对"); ok = False
    if "#241009" not in html:
        print("  ✗ tier HTML 未吃到熔火背景色 #241009"); ok = False
    elif '"Songti SC"' in html:
        print("  ✗ heavy 字体未替换（仍有宋体栈）"); ok = False
    else:
        print("  ✓ ember_forge 换色生效 + heavy 字体替换生效")
    for tok in ("__T_BASE__", "__T_PANEL_RGB__", "__T_BODYTXT__"):
        if tok in html:
            print("  ✗ tier HTML 残留 %s" % tok); ok = False

    m, html = run_case("C 未接通模板保护", skin="wow", template="magazine",
                       palette="ember_forge", font="serif")
    if "#241009" in html:
        print("  ✗ magazine 不应被预设改色"); ok = False
    else:
        print("  ✓ magazine 忽略配色预设，保持皮原色")

    m, html = run_case("D 显式default零变化", skin="wow", template="classic", layout="default")
    if html != html_default_implicit:
        print("  ✗ 显式 layout=default 与不传 layout 的封面 HTML 不一致"); ok = False
    elif "/* layout:" in html:
        print("  ✗ default 不应注入版式标记"); ok = False
    else:
        print("  ✓ 显式 default 与不传 layout 字节全等（默认零行为变化）")

    m, html = run_case("E classic图先行", skin="wow", template="classic", layout="hero_first")
    if m.get("layout") != "hero_first":
        print("  ✗ manifest 版式字段不对"); ok = False
    if "/* layout:hero_first */" not in html or ".topline{order:-2}.hero{order:-1}" not in html:
        print("  ✗ classic 未吃到 hero_first 覆盖 CSS"); ok = False
    else:
        print("  ✓ hero_first 标记与 order 覆盖注入生效")

    m, html = run_case("F 未知版式回落", skin="wow", template="classic", layout="no_such_layout")
    if m.get("layout") != "default" or "/* layout:" in html:
        print("  ✗ 未知版式键未回落 default"); ok = False
    else:
        print("  ✓ 未知版式键回落 default，无覆盖注入")

    m, html = run_case("G quest要点先行+ifact", skin="wow", template="quest", layout="summary_first")
    if "/* layout:summary_first */" not in html or ".hero{order:1}" not in html:
        print("  ✗ quest 未吃到 summary_first 覆盖 CSS"); ok = False
    elif '<div class="ifact">' not in html:
        print("  ✗ quest 封面缺少 div.ifact 横向容器（ifact 修复未生效）"); ok = False
    else:
        print("  ✓ quest summary_first 生效，且摘要行已包进 div.ifact")

    m, html = run_case("H guide满铺整图", skin="wow", template="guide")
    html2 = (OUT / "img2.html").read_text(encoding="utf-8")
    if '<div class="gfull">' not in html:
        print("  ✗ guide 封面未走满铺整图分支（缺 div.gfull）"); ok = False
    elif '<div class="gfull">' in html2:
        print("  ✗ guide 内页不应满铺整图"); ok = False
    else:
        print("  ✓ guide 封面满铺整图生效，内页保持原结构")

    from src.graphics import skins as _skins
    _orig_ratio = _skins._img_ratio
    _skins._img_ratio = lambda path: 1.0
    try:
        m, html = run_case("I guide比例回落", skin="wow", template="guide")
    finally:
        _skins._img_ratio = _orig_ratio
    if '<div class="gfull">' in html:
        print("  ✗ 比例 < 1.6 时应回落旧结构，不应出现 div.gfull"); ok = False
    else:
        print("  ✓ 比例不足 1.6 时 guide 封面回落旧结构")

    # J. classic 内页也吃到版式覆盖（2026-09-29 内页缺口修复断言）
    m, html = run_case("J classic内页重排", skin="wow", template="classic", layout="summary_first")
    html2 = (OUT / "img2.html").read_text(encoding="utf-8")
    if "/* layout:summary_first */" not in html2:
        print("  ✗ classic 内页未注入版式覆盖标记"); ok = False
    elif ".wrap-in>.band{order:1}" not in html2 or ".wrap-in>.note{order:2" not in html2:
        print("  ✗ classic 内页缺少 .band/.note 重排规则（内页缺口未补）"); ok = False
    elif ".wrap-in>h2{margin-top:18px}" not in html2:
        # 2026-09-29 报障「有些字被挡住了」：h2 自带 margin-top:-84px（为压在横带图下沿设计），
        # band 沉底后负边距把标题顶进报头行，首字被 brand 徽章金底盖住 → 覆盖层必须解除
        print("  ✗ classic 内页未解除 h2 负边距（band 沉底后标题会被 brand 徽章遮挡）"); ok = False
    else:
        print("  ✓ classic 内页吃到 summary_first 重排（.band 沉底 + .note 压尾 + h2 负边距解除）")

    # K. tier 内页重排（tgrid 上浮到 thead 之前）
    m, html = run_case("K tier内页重排", skin="wow", template="tier", layout="summary_first")
    html2 = (OUT / "img2.html").read_text(encoding="utf-8")
    if ".tier>.tgrid{order:-1" not in html2 or ".tier>.thead{order:1" not in html2:
        print("  ✗ tier 内页缺少 tgrid/thead 重排规则"); ok = False
    else:
        print("  ✓ tier 内页吃到 summary_first 重排（tgrid 上浮 + thead 下沉）")

    # L. 随机版式：抽中真实预设、整包统一、manifest 不落 random
    from src.graphics import variants as _variants
    seen = set()
    for _ in range(8):
        m, html = run_case("L 随机版式", skin="wow", template="classic", layout="random")
        lk = m.get("layout")
        if lk not in _variants.RANDOM_POOL:
            print("  ✗ 随机版式抽中非法键：%r" % lk); ok = False; break
        if "/* layout:%s */" % lk not in html:
            print("  ✗ 随机版式 HTML 未注入抽中预设的覆盖标记"); ok = False; break
        seen.add(lk)
    else:
        if len(seen) < 2:
            print("  ⚠ 8 次随机只抽到 %s（样本偏少，非致命）" % sorted(seen))
        print("  ✓ 随机版式 8 次均抽中合法预设并注入覆盖，manifest 落真实键（seen=%s）" % sorted(seen))

    # M. magazine 吃到版式覆盖（2026-09-29 报障①修复断言：
    #    此前 LAYOUT_PRESETS 缺 magazine 规则 → layout_css 返回空串 → 随机版式静默零注入）
    m, html = run_case("M magazine要点先行", skin="wow", template="magazine", layout="summary_first")
    html2 = (OUT / "img2.html").read_text(encoding="utf-8")
    if "/* layout:summary_first */" not in html or ".photo{order:1}" not in html or ".index{order:2}" not in html:
        print("  ✗ magazine 封面未吃到 summary_first 重排（photo/index 规则缺失）"); ok = False
    elif ".wrap-in>.lband{order:1}" not in html2 or ".wrap-in>.note-q{order:2}" not in html2:
        print("  ✗ magazine 内页未吃到 summary_first 重排（lband/note-q 规则缺失）"); ok = False
    else:
        print("  ✓ magazine 封面+内页均吃到 summary_first 重排（全模板覆盖修复生效）")

    # N. minimal/bold 吃到 hero_first 覆盖（minimal 封面主图上移放大；bold 内页 warn 上浮）
    m, html = run_case("N minimal图先行", skin="wow", template="minimal", layout="hero_first")
    if "/* layout:hero_first */" not in html or ".photo{order:-1;flex-basis:38%}" not in html:
        print("  ✗ minimal 未吃到 hero_first 覆盖（photo 上移放大规则缺失）"); ok = False
    else:
        m, html = run_case("N bold图先行", skin="wow", template="bold", layout="hero_first")
        html2 = (OUT / "img2.html").read_text(encoding="utf-8")
        if "/* layout:hero_first */" not in html or ".poster{order:-1;flex-basis:34%}" not in html:
            print("  ✗ bold 未吃到 hero_first 覆盖（poster 上移放大规则缺失）"); ok = False
        elif ".wrap-in>.warn{order:-1" not in html2:
            print("  ✗ bold 内页未吃到 warn 上浮规则"); ok = False
        else:
            print("  ✓ minimal/bold hero_first 封面+内页规则均注入")

    # O. 用户显式标题代码级强制（2026-09-29 报障②修复断言：
    #    monkeypatch _llm_cards 模拟 LLM 擅自改写封面标题，仍应被强制覆盖为用户标题）
    _orig_llm = toutiao._llm_cards

    def _fake_llm(summary_, ai_config_, theme_="", template_="classic"):
        data = toutiao._fallback_cards(summary_)
        data["cards"][0]["title"] = "LLM擅自改写的标题B测"
        return data

    toutiao._llm_cards = _fake_llm
    try:
        m, html = run_case("O 标题强制", skin="wow", template="classic",
                           title="用户指定标题必须逐字上封面")
    finally:
        toutiao._llm_cards = _orig_llm
    cover_title = ((m.get("cards") or [{}])[0]).get("title")
    if cover_title != "用户指定标题必须逐字上封面":
        print("  ✗ 封面卡 title 未被强制为用户标题：%r" % cover_title); ok = False
    elif "LLM擅自改写的标题" in html:
        print("  ✗ 封面 HTML 仍含 LLM 改写标题"); ok = False
    elif "用户指定标题必须逐字上封面" not in html:
        print("  ✗ 封面 HTML 缺少用户标题"); ok = False
    else:
        print("  ✓ LLM 改写标题被代码级强制覆盖，封面逐字呈现用户标题")

    # P. 头条渠道成品图不再出现「提醒」小结块（2026-09-29 用户要求「图文中移除提醒」）
    #    兜底卡 _fallback_cards 第 4 张自带 note.title='提醒'——渲染前必须被代码级剔除
    m, html = run_case("P 提醒块剔除", skin="wow", template="classic")
    leaked = [f.name for f in sorted(OUT.glob("img*.html"))
              if "提醒" in f.read_text(encoding="utf-8")]
    if leaked:
        print("  ✗ 以下成品图 HTML 仍含「提醒」块：%s" % leaked); ok = False
    else:
        print("  ✓ 兜底卡的「提醒」块被代码级剔除，全部成品图无残留")

    # Q. 条目行不再显示「首字」徽章（classic 圆环 ring / quest 圆形 ico）
    m, html = run_case("Q classic去首字", skin="wow", template="classic")
    ring_leak = [f.name for f in sorted(OUT.glob("img*.html"))
                 if 'class="ring"' in f.read_text(encoding="utf-8")]
    if ring_leak:
        print("  ✗ classic 仍渲染条目名首字圆环：%s" % ring_leak); ok = False
    elif ".panel .ring{" in html:
        print("  ✗ classic CSS 仍保留 .panel .ring 死样式"); ok = False
    else:
        m, html = run_case("Q quest去首字", skin="wow", template="quest")
        ico_leak = [f.name for f in sorted(OUT.glob("img*.html"))
                    if 'class="ico"' in f.read_text(encoding="utf-8")]
        if ico_leak:
            print("  ✗ quest 仍渲染看点标题首字圆徽：%s" % ico_leak); ok = False
        else:
            print("  ✓ classic 圆环 / quest 圆徽均已移除（markup + CSS 双清）")

    # R. 素材选图随机内核（2026-09-29 用户要求「素材图片也可以是随机的，并不是第一张图片
    #    就是第一张图片素材」）。直接测 skins 两个选图函数：
    #    rng=None 必须逐字保持旧行为（wechat 等既有调用点向后兼容），
    #    rng 给定时封面取值应多样化、横带应在「还没用过的素材」里抽（包内不撞图）。
    import random as _random
    from src.graphics import skins as _skins
    pool = [Path("/tmp/zz_fake_a.jpg"), Path("/tmp/zz_fake_b.jpg"), Path("/tmp/zz_fake_c.jpg")]
    cover_old = _skins._pick_cover_image(pool)
    band_old = _skins._pick_band_image(pool, 2)
    if cover_old != pool[0] or band_old != pool[0]:
        print("  ✗ rng=None 旧行为被破坏（cover=%s band=%s）" % (cover_old, band_old)); ok = False
    else:
        covers = set()
        bad = []
        for s in range(20):
            rng2 = _random.Random(s)
            cov = _skins._pick_cover_image(pool, rng2)
            covers.add(cov)
            used = [cov]
            for i in (2, 3):
                b = _skins._pick_band_image(pool, i, rng2, exclude=list(used))
                if b not in pool or b in used:
                    bad.append((s, i, str(b)))
                used.append(b)
        exhausted = _skins._pick_band_image(pool, 4, _random.Random(0), exclude=list(pool))
        if exhausted not in pool:
            bad.append(("素材耗尽回落", 4, str(exhausted)))
        if len(covers) < 2:
            print("  ✗ 封面随机未生效（20 个种子只出现 %d 种取值）" % len(covers)); ok = False
        elif bad:
            print("  ✗ 横带越池或与已用素材撞图：%s" % bad[:3]); ok = False
        else:
            print("  ✓ rng=None 旧行为不变；随机封面 %d 种取值，横带避开已用素材、耗尽时回落全池"
                  % len(covers))

        # R2. 头条包 manifest 记录实际用到的素材文件名（随机后必须可追溯「这张图用的哪张素材」）
        m, html = run_case("R 素材可追溯", skin="wow", template="classic")
        assets = m.get("assets")
        if not isinstance(assets, list) or not assets:
            print("  ✗ manifest.assets 缺失或为空：%r" % (assets,)); ok = False
        else:
            print("  ✓ manifest.assets 记录 %d 个素材文件：%s" % (len(assets), assets))

            # R3. 随机确实到达渠道层：同种子选图必须完全复现，换种子后 assets 应出现变化
            again = run_case("R3 同种子复现", skin="wow", template="classic")[0].get("assets")
            seen = set()
            for s in (1, 2, 3, 4, 5, 6):
                toutiao._RNG_SEED = s
                try:
                    a = run_case("R3 种子%d" % s, skin="wow", template="classic")[0].get("assets") or []
                finally:
                    toutiao._RNG_SEED = 20260929
                seen.add(tuple(a))
            if again != assets:
                print("  ✗ 同种子选图不可复现：%s vs %s" % (assets, again)); ok = False
            elif len(seen) < 2:
                print("  ✗ 换 6 个种子选图毫无变化（随机未接到渠道层）：%s" % seen); ok = False
            else:
                print("  ✓ 同种子选图可复现；6 个种子出现 %d 种素材组合，随机已接到渠道层" % len(seen))

    shutil.rmtree(OUT, ignore_errors=True)
    print("\n%s" % ("ALL_OK" if ok else "HAS_FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
