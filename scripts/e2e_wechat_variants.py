# -*- coding: utf-8 -*-
"""端到端验证：公众号渠道并回头条渲染循环后的 generate_graphics（2026-11-03 合并版，不进 git）。

合并方案（站主拍板）：公众号图片消息与头条共用 toutiao.render_cards，模板白名单同源、
默认 classic 暗金，画布仍 3:4（1080x1440）；文字攻略页（kind="text"）为新页型。
本脚本只验公众号渠道在合并后的行为：
  W1. 默认 → manifest classic/gold_night/serif/wow、画布 1080x1440、PNG 实际 3:4、URL 前缀 /media/wechat、无标记无 token 残留
  W2. ember_forge → classic 全部被覆盖的配色 token 换新值且默认值零残留（动态比对，不抄色值）
  W3. font=heavy → 追加片段标记 + 重黑字体族 + 900 字重，封面内页都吃到
  W4. layout=hero_first → classic 覆盖片段注入封面与内页
  W5. layout=random → 抽中合法真实预设并注入覆盖，manifest 不落 "random"
  W6. 三轴都传未知键 → 全部回落默认，封面 HTML 与 W1 字节全等（默认零行为变化）
  W7. minimal+heavy → 专属片段指纹生效；minimal+ember → 未接通配色轴不改色（前端 hint 如实性）
  W8. 3:4 排版 lint：list 卡 6 条 → error；text 页 5 步 → warn（样稿实测口径落成 lint）
  W9. 文字攻略页渲染：kind="text" 出整页文字卡（kicker/编号步骤/页脚在），claims 记录跳过原因
  W10. 事实闸：在范围内的未登记数值 → GraphicsGateError 拒绝出图；已核口径 → 放行
  W11. 真实截图 → 复制进包、标 source=real_screenshot、排进主视觉（photos_in_cards>0）
  W12. render_preview → 走同一合并管线，默认 classic，URL 前缀 /media/wechat/_previews
  W13. 提示词（单源+多源）要求【适用范围】【正文】【来源】与四类标注，且不再写"不写正文内容"
  W14. 规则版骨架（没配 AI 那条路）按新九节落库，但体检必须判「不能发」——骨架不许冒充成稿
  W15. 新形态【正文】拼配图卡取小节标题不切碎句；真写了正文的文章过结构关；短句/无标注判 FAIL
  W16. 存量旧【正文骨架】稿仍要能出 6 张卡（库里 8 篇），同时体检给一句可执行的「成稿形态」不合格
  W17. 渠道策略权威源口径跟着改（primary_product=article、graphics_optional、版本 +1），头条那份不带
  W18. 素材台账：编号对应「文章」还是「视频」＋发布日真给进提示词；头条两档仍走原样（串线判据）
"""
import shutil
import sys
from pathlib import Path

ROOT = Path("/Users/zhangyajun/Documents/project/video2text")
# 成品目录默认不再留中间 HTML（2026-10-08 用户要求），但本脚本要靠 HTML 断言「三轴到底生效没有」，
# 所以显式打开保留开关。渠道侧的判断在 channels/{toutiao,wechat}.py。
import os

os.environ["V2T_KEEP_RENDER_HTML"] = "1"

sys.path.insert(0, str(ROOT))

from src.graphics.channels import wechat as wx  # noqa: E402
from src.graphics import variants as _v        # noqa: E402
from src.graphics import css_engine as _ce     # noqa: E402
from src.graphics import gate as _gate         # noqa: E402

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

# 字体轴指纹（来自 variants.FONT_PRESETS 的实际片段；classic 有 Songti 衬线栈走正则路，
# minimal 全篇黑体栈走 tpl_css 追加路——两路各验一边）
HEAVY_STACK_MARK = 'font-family:"Hiragino Sans GB","Heiti SC"'
HEAVY_MINIMAL_MARK = "letter-spacing:-.015em"


def png_size(p):
    """读 PNG IHDR 的宽高（字节 16-24，大端），验「画布真是 3:4」不看 manifest 自说自话。"""
    with open(p, "rb") as fh:
        head = fh.read(24)
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def palette_swap_problems(html, tpl, new_key, html_default):
    """配色轴动态比对：**classic 真正用到的**被覆盖 token，新值必须在位、皮基线旧值必须零残留。
    预设会带其他模板的 token（如 tier 的 __T_BASE__），classic 的 CSS 根本不消费它们，
    不在断言里抄死色值——色值改一处这里就漂一处，check_variant_tokens 已经锁过全组合。"""
    base = _ce._tpl_tokens(tpl, "wow")
    new = _v.palette_tokens(tpl, new_key)
    bad = []
    for k, nv in new.items():
        ov = base.get(k)
        if not ov or ov == nv:
            continue
        if ov in html_default:
            if nv not in html:
                bad.append("%s 新值 %s 未生效" % (k, nv))
            if ov in html:
                bad.append("%s 旧值 %s 残留" % (k, ov))
    return bad


def run_case(name, summary=None, summary_id=99998, ai_config=None, **kw):
    out = ROOT / "output" / "wechat" / str(summary_id)
    if out.exists():
        shutil.rmtree(out)
    m = wx.generate_graphics(summary or SUMMARY, ai_config or {}, theme="AI工具实测", **kw)
    print("[%s] manifest template=%s palette=%s font=%s layout=%s skin=%s imgs=%d canvas=%s"
          % (name, m.get("template"), m.get("palette"), m.get("font"), m.get("layout"),
             m.get("skin"), len(m.get("images") or []), m.get("canvas")))
    html1 = (out / "img1.html").read_text(encoding="utf-8")
    html2 = (out / "img2.html").read_text(encoding="utf-8")
    return m, html1, html2, out


def main():
    ok = True

    # W1 默认组合（合并后＝头条同款 classic 暗金 3:4）
    m, html, html2, out = run_case("W1 默认classic")
    if (m.get("template"), m.get("palette"), m.get("font"), m.get("layout"), m.get("skin")) != \
            ("classic", "gold_night", "serif", "default", "wow"):
        print("  ✗ manifest 默认组合不对"); ok = False
    elif m.get("canvas") != "1080x1440":
        print("  ✗ manifest 画布非 3:4"); ok = False
    elif any(png_size(out / im["file"]) != (1080, 1440) for im in m["images"]):
        print("  ✗ PNG 实际尺寸非 1080x1440"); ok = False
    elif any(not im["url"].startswith("/media/wechat/99998/") for im in m["images"]):
        print("  ✗ 图片 URL 前缀不是 /media/wechat/<id>"); ok = False
    elif "/* layout:" in html or "/* font:" in html:
        print("  ✗ 默认组合不应注入版式/字体标记"); ok = False
    elif "__PAPER__" in html or "__ACCENT_DK__" in html or "__MDIM__" in html:
        print("  ✗ classic HTML 残留未替换 token"); ok = False
    elif "neutral_bg.jpg" not in html:
        print("  ✗ wow 皮垫图未进 CSS（合并后默认 skin=wow）"); ok = False
    else:
        print("  ✓ 默认 classic/gold_night/serif/wow、1080x1440、URL 前缀正确、无标记无残留")
    html_default = html

    # W2/W3 配色轴（动态比对：覆盖 token 新值在位、旧值零残留）
    for key, name in (("ember_forge", "W2 熔火暖橙"), ("arcane_dusk", "W3 暮光紫")):
        m, html, html2, out = run_case(name, palette=key)
        if m.get("palette") != key:
            print("  ✗ manifest palette 字段不对"); ok = False
            continue
        bad = (palette_swap_problems(html, "classic", key, html_default)
               + palette_swap_problems(html2, "classic", key, html_default))
        if bad:
            print("  ✗ %s：%s" % (key, "；".join(bad[:4]))); ok = False
        else:
            print("  ✓ %s 配色覆盖 token 全部换新、皮基线旧值零残留（封面内页）" % key)

    # W4 字体轴（classic 标题是 Songti 衬线栈，走正则替换路：head 换成黑体栈、旧衬线零残留；
    # body 本来就是黑体栈保持现状，也不注入 tpl_css 追加片段——classic 有可替换锚点，追加路不启用）
    m, html, html2, out = run_case("W4 硬朗黑体", font="heavy")
    if m.get("font") != "heavy":
        print("  ✗ manifest font 字段不对"); ok = False
    elif '"Songti SC"' not in html_default:
        print("  ✗ 基线里没有衬线栈锚点（断言前提不成立）"); ok = False
    elif '"Songti SC"' in html or '"Songti SC"' in html2:
        print("  ✗ 旧衬线字体栈残留（正则没换干净＝静默失效）"); ok = False
    elif '"PingFang SC","Hiragino Sans GB",sans-serif' not in html:
        print("  ✗ 标题未换上黑体栈"); ok = False
    else:
        print("  ✓ heavy 对 classic 真实生效（正则路换 head 栈，封面内页），旧衬线栈零残留")

    # W5 hero_first（classic 覆盖片段，封面内页都注入）
    m, html, html2, out = run_case("W5 主视觉先行", layout="hero_first")
    frag = _v.LAYOUT_PRESETS["hero_first"]["css"].get("classic", "")
    if m.get("layout") != "hero_first" or "/* layout:hero_first */" not in html:
        print("  ✗ hero_first 未记录/未注入"); ok = False
    elif not frag or frag not in html or frag not in html2:
        print("  ✗ classic hero_first 覆盖片段未注入（封面或内页缺口）"); ok = False
    else:
        print("  ✓ hero_first classic 片段注入封面与内页")

    # W6 random
    seen = set()
    for i in range(3):
        m, html, html2, out = run_case("W6 随机版式%d" % (i + 1), layout="random")
        lk = m.get("layout")
        if lk not in _v.RANDOM_POOL:
            print("  ✗ 随机抽中非法键：%r" % lk); ok = False; break
        if "/* layout:%s */" % lk not in html:
            print("  ✗ 随机抽中 %s 但未注入对应覆盖" % lk); ok = False; break
        seen.add(lk)
    else:
        print("  ✓ random 3 次均抽中合法预设并注入覆盖，manifest 落真实键（seen=%s）" % sorted(seen))

    # W7 未知键回落（与 W1 字节全等＝默认零行为变化）
    m, html, html2, out = run_case("W7 未知键回落", palette="no_such", font="no_such", layout="no_such")
    if (m.get("template"), m.get("palette"), m.get("font"), m.get("layout")) != \
            ("classic", "gold_night", "serif", "default"):
        print("  ✗ 未知键未回落默认组合"); ok = False
    elif html != html_default:
        print("  ✗ 未知键封面 HTML 与默认组合不一致（回落不彻底）"); ok = False
    else:
        print("  ✓ 三轴未知键全部回落默认，封面 HTML 与默认组合字节全等")

    # W8 minimal：字体追加路生效 + 未接通配色轴不改色（hint 如实性）
    m, html, html2, out = run_case("W8 minimal硬朗黑体", template="minimal", font="heavy")
    if m.get("template") != "minimal":
        print("  ✗ manifest template 字段不对"); ok = False
    elif "/* font:heavy */" not in html or HEAVY_MINIMAL_MARK not in html:
        print("  ✗ minimal 未吃到 heavy 专属片段（静默失效）"); ok = False
    else:
        print("  ✓ heavy 对 minimal 真实生效（minimal 专属片段指纹在位）")
    m, html, html2, out = run_case("W8b minimal配色保护", template="minimal", palette="ember_forge")
    bad = palette_swap_problems(html, "minimal", "ember_forge", html_default)
    if m.get("template") != "minimal" or bad:
        print("  ✗ minimal 未接通配色轴却被改色：%s（会串色，与前端 hint 不符）" % bad); ok = False
    else:
        print("  ✓ minimal 忽略配色预设保持原色，与前端 hint 一致")

    # W9 3:4 排版 lint（样稿实测口径：list 卡 6 条溢出＝error；text 页 5 步＝warn）
    lint_m = {"summary_id": 0, "canvas": "1080x1440", "images": [], "digest": "x",
              "author_draft": "", "model": "llm",
              "cards": [
                  {"kind": "list", "title": "超载卡", "items": [{"name": "n%d" % i, "desc": "d"} for i in range(6)]},
                  {"kind": "text", "title": "步骤超载", "steps": [{"h": "s%d" % i, "p": "p"} for i in range(5)]},
              ]}
    lint_issues = wx.lint_package(lint_m, ROOT / "output" / "wechat")
    has_item_err = any(i.startswith("error") and "上限 5 条" in i for i in lint_issues)
    has_step_warn = any(i.startswith("warn") and "≤4 条" in i for i in lint_issues)
    if has_item_err and has_step_warn:
        print("  ✓ 3:4 排版 lint 生效：6 条 list → error、5 步 text → warn")
    else:
        print("  ✗ 3:4 排版 lint 缺口：%s" % lint_issues); ok = False

    # W10 文字攻略页渲染 + claims 跳过记录（本题材不在登记表范围）
    TEXT_CARDS = {
        "cards": [
            {"kind": "cover", "bg": "neutral", "badge": "看点", "title": "文字页页型验证",
             "subtitle": "合并后新页型", "timeline_label": "本期看点", "timeline": "整页文字卡",
             "timeline_note": "", "hooks": [{"t": "一页一事", "d": "编号步骤"}]},
            {"kind": "list", "title": "要点", "section": "要点", "icon": "要",
             "items": [{"name": "页型", "desc": "整页有格式文字卡", "tag": "新"}]},
            {"kind": "text", "title": "上线前自查两步",
             "subtitle": "一页讲清一个动作",
             "precondition": "先找自己的条目，再往下核。",
             "steps": [{"h": "对号入座", "p": "先找到自己的条目再往下核。"},
                       {"h": "以游戏内为准", "p": "官方说明只是快照，进服看游戏内文本。"}],
             "footer": "出处：整合稿"},
        ],
        "copy_text": "文字页页型验证配文。" + "补充说明。" * 20,
        "digest": "文字页页型验证摘要", "keyword_reply": "", "source_note": "验证",
    }
    real_llm = wx._llm_cards
    try:
        wx._llm_cards = lambda *a, **k: dict(TEXT_CARDS)
        m, html3, html2b, out = run_case("W10 文字攻略页", summary_id=99997,
                                         summary={"id": 99997, "title": "文字页页型验证",
                                                  "summary_type": "wechat_material",
                                                  "content": "【导语】验证文字攻略页。"},
                                         ai_config={"api_key": "stub", "model": "stub"})
        text_idx = [i for i, c in enumerate(TEXT_CARDS["cards"], 1) if c["kind"] == "text"][0]
        html_text = (out / ("img%d.html" % text_idx)).read_text(encoding="utf-8")
        claims = m.get("claims") or {}
        if not (out / ("img%d.png" % text_idx)).exists():
            print("  ✗ 文字攻略页没出 PNG"); ok = False
        elif "文字攻略 · 一页一个动作" not in html_text or "上线前自查两步" not in html_text \
                or "出处：整合稿" not in html_text:
            print("  ✗ 文字页 HTML 缺 kicker/标题/页脚出处"); ok = False
        elif not claims.get("skipped") or claims.get("hard"):
            print("  ✗ claims 记录不对（应记跳过原因、无 hard）：%s" % claims); ok = False
        else:
            print("  ✓ 文字攻略页整页出图（kicker/步骤/页脚出处），claims 如实记录：%s" % claims["skipped"][:30])
    finally:
        wx._llm_cards = real_llm
        p97 = ROOT / "output" / "wechat" / "99997"
        if p97.exists():
            shutil.rmtree(p97)

    # W11 事实闸：范围内未登记数值 → 拒绝出图；已核口径 → 放行
    WOW_SUMMARY = {"id": 99996, "title": "魔兽无限种族调整自查",
                   "summary_type": "wechat_material",
                   "content": "【导语】魔兽无限服 10 月官方种族调整，上线前自查。"}
    HARD_PAGE = {"cards": [
        {"kind": "cover", "bg": "neutral", "badge": "官方快照", "title": "种族调整自查",
         "subtitle": "", "timeline_label": "本期看点", "timeline": "上线前自查",
         "timeline_note": "", "hooks": [{"t": "牛头人", "d": "原野疾驰"}]},
        {"kind": "text", "title": "自查一步",
         "subtitle": "", "precondition": "进服以游戏内文本为准。",
         "steps": [{"h": "核移速", "p": "牛头人的原野疾驰上限是 55%。"}],
         "footer": "出处：官方蓝贴"},
    ], "copy_text": "种族调整自查配文。" + "补充说明。" * 20, "digest": "种族调整自查",
       "keyword_reply": "", "source_note": "验证"}
    PASS_PAGE = {"cards": [
        {"kind": "cover", "bg": "neutral", "badge": "官方快照", "title": "种族调整自查",
         "subtitle": "", "timeline_label": "本期看点", "timeline": "上线前自查",
         "timeline_note": "", "hooks": [{"t": "牛头人", "d": "原野疾驰"}]},
        {"kind": "text", "title": "自查一步",
         "subtitle": "", "precondition": "进服以游戏内文本为准。",
         "steps": [{"h": "核移速", "p": "牛头人的原野疾驰每层 2%、上限 60%。"},
                   {"h": "以游戏内为准", "p": "数值对的是官方蓝贴，进服看游戏内文本。"}],
         "footer": "数值出处：暴雪官方开发说明 2026-10-26"},
    ], "copy_text": "种族调整自查配文。" + "补充说明。" * 20, "digest": "种族调整自查",
       "keyword_reply": "", "source_note": "验证"}
    real_llm = wx._llm_cards
    stub = {"api_key": "stub", "model": "stub"}
    try:
        wx._llm_cards = lambda *a, **k: dict(HARD_PAGE)
        raised = ""
        try:
            wx.generate_graphics(dict(WOW_SUMMARY), stub, theme="魔兽攻略")
        except _gate.GraphicsGateError as e:
            raised = str(e)
        if "事实闸" in raised:
            print("  ✓ 未登记数值被事实闸拦下（hard→拒绝出图）：%s" % raised[:50])
        else:
            print("  ✗ 未登记数值没被拦（raised=%r）" % raised[:80]); ok = False
        wx._llm_cards = lambda *a, **k: dict(PASS_PAGE)
        out96 = ROOT / "output" / "wechat" / "99996"
        if out96.exists():
            shutil.rmtree(out96)
        m = wx.generate_graphics(dict(WOW_SUMMARY), stub, theme="魔兽攻略")
        claims = m.get("claims") or {}
        if claims.get("hard") or not claims.get("checked"):
            print("  ✗ 已核口径应放行且有判定数：%s" % claims); ok = False
        else:
            print("  ✓ 已核口径放行（checked=%d、hard=0）" % claims.get("checked", 0))
    finally:
        wx._llm_cards = real_llm
        p96 = ROOT / "output" / "wechat" / "99996"
        if p96.exists():
            shutil.rmtree(p96)

    # W12 真实截图：复制进包、标 source、排进主视觉
    shots_src = ROOT / "output" / "toutiao" / "_assets"
    tmp_shots = []
    shot_dir = ROOT / "output" / "wechat" / "_e2e_shots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    for i in (1, 2):
        src = shots_src / "neutral_bg.jpg"
        if src.exists():
            dst = shot_dir / ("fake_shot%d.jpg" % i)
            shutil.copyfile(src, dst)
            tmp_shots.append(dst)
    if tmp_shots:
        m, html, html2, out = run_case("W12 真实截图", real_screenshots=tmp_shots)
        pkg_shots = sorted(p.name for p in out.glob("shot_*"))
        src_entries = [im for im in m["images"] if im.get("source") == "real_screenshot"]
        if len(pkg_shots) != 2 or len(src_entries) != 2:
            print("  ✗ 真图没复制进包/没标 source：%s / %d" % (pkg_shots, len(src_entries))); ok = False
        elif not (m.get("photos_in_cards") and m.get("assets")):
            print("  ✗ 真图没排进主视觉：photos_in_cards=%s assets=%s"
                  % (m.get("photos_in_cards"), m.get("assets"))); ok = False
        else:
            print("  ✓ 真图复制进包并排进主视觉（photos_in_cards=%d、assets=%s）"
                  % (m["photos_in_cards"], m["assets"][:3]))
    else:
        print("  ! W12 跳过：找不到可复用的示例图（output/toutiao/_assets/neutral_bg.jpg 不在）")

    # W13 render_preview 走同一合并管线
    pv = wx.render_preview()
    if pv["params"]["template"] != "classic" or not pv["images"][0].startswith("/media/wechat/_previews/"):
        print("  ✗ 预览参数/URL 前缀不对：%s %s" % (pv["params"], pv["images"][0])); ok = False
    else:
        pv2 = wx.render_preview()
        print("  ✓ render_preview 默认 classic、URL 前缀正确、缓存命中=%s" % pv2["cached"])

    # ── W14–W19：2026-10-26 策略改动（这一档交的是**完整文章**，配图降级为可选工具）──
    # 依据 wow-forever-web `docs/OPS-公众号当前策略-2026-10-25.md`。这五组钉的是：
    # 提示词真的要求文章主体、规则版骨架过不了体检、新形态仍能拼出配图卡、存量旧稿还能出图。
    from src import content_store as cs   # noqa: E402
    from src import draft_audit as da     # noqa: E402

    VIDEO = {"id": 99998, "title": "魔兽无限服怎么查一件装备的获取条件", "author": "测试号",
             "tags": "游戏攻略,魔兽", "game": "魔兽世界", "published_at": "2026-10-24",
             "transcript": ("我昨天在无限服实测了一次。打开物品页点获取条件，能看到 5 个副本名字。"
                            "材料槽要 126 个布片。有人提到需要 8000 金币，我没验证过。")}

    def check(name, cond, detail=""):
        print("  %s %s%s" % ("✓" if cond else "✗", name, (" — " + str(detail)) if detail else ""))
        if not cond:
            return False
        return True

    print("\n[W14 提示词要求文章主体，不再只要求骨架]")
    pr = cs._prompt(VIDEO, "wechat_material")
    pc = cs._collection_prompt([VIDEO], "wechat_material")
    need = ("【适用范围】", "【正文】", "【来源】", "[已核]", "[他述]", "[本站]", "[待核]")
    ok = check("单源与多源提示词都要求适用范围/正文/来源与四类标注",
               all(k in pr and k in pc for k in need),
               "两份都含这 7 项；缺的：%s" % ("、".join(k for k in need if k not in pr or k not in pc) or "无")) and ok
    ok = check("提示词里那句「不写正文内容，只给骨架」已经去掉（留着就是白改）",
              "不写正文内容" not in pr and "不是成品文章" not in pr,
              "旧措辞残留：%s" % ("、".join(x for x in ("不写正文内容", "不是成品文章")
                                        if x in pr) or "已删除")) and ok
    ok = check("头条整合稿的提示词一个字没动（不许串线）",
              "决策化整合稿" in cs._prompt(VIDEO, "toutiao_mix")
              and "【正文】是真写的分节正文" not in cs._prompt(VIDEO, "toutiao_mix")) and ok

    print("\n[W15 规则版骨架不能冒充成稿（没配 AI 时它照样落库，但体检必须拦住）]")
    loc = cs._generate_local(VIDEO, "wechat_material")["content"]
    loc_c = cs._generate_collection_local([VIDEO, VIDEO], "wechat_material")["content"]
    miss = [sec for sec in da.MATERIAL_SECTIONS if sec not in loc]
    ok = check("规则版单源与多源都按新九节产出", not miss and all(sec in loc_c for sec in da.MATERIAL_SECTIONS),
               "单源缺：%s" % "、".join(miss)) and ok
    aud = da.audit_material(loc, VIDEO["transcript"])
    kinds = [r[1] for r in aud["rows"] if r[0] == "FAIL"]
    ok = check("体检把规则版判成不能发，理由落在正文上",
               aud["verdict"] == "fail" and any("正文" in k for k in kinds),
               "%s / FAIL 项：%s" % (aud["verdict"], "、".join(kinds))) and ok
    ok = check("规则版没把没核实的东西写成建议（待核项那一行在）",
               "不能直接发" in loc and "[待核]" in loc) and ok

    print("\n[W16 新形态的【正文】仍能拼配图卡：取小节标题，不把整段正文切成碎句]")
    ARTICLE = ("【标题候选】\n1. 查装备获取条件三步\n2. 无限服物品页藏着什么\n3. 126 个布片怎么来\n\n"
               "【导语】一件装备能不能做，先看获取条件那一页 [本站]\n\n"
               "【适用范围】魔兽世界「无限」国服，2026-10 版本；怀旧服界面不同，不适用 [已核]\n\n"
               "【核心论点】获取条件页是唯一入口\n材料数看的是全服口径\n金币门槛未验证\n\n"
               "【正文】\n## 打开物品页看获取条件\n"
               "在游戏内按 K 打开法术书旁边的物品页，点「获取条件」页签，会列出这套装备需要的 5 个副本名字与材料数。"
               "看到副本列表才算这一页真的加载出来，只看到图标不算。 [已核]\n"
               "## 材料数怎么读\n"
               "物品页标的 126 个布片是**全服口径**，不是单人一次能刷出来的量；"
               "要判断自己差多少，得先看你已有多少，再按每周实际产出折算。 [已核]\n"
               "## 金币门槛这条先别照做\n"
               "有 UP 主提到还需要 8000 金币启动，实测没有验证过，本站也无法从公开数据推出，"
               "所以这一段只作为线索记录，**不要当成操作步骤**。 [待核]\n\n"
               "【关键数据】\n| 项目 | 数值 | 口径 | 标注 |\n|---|---|---|---|\n| 布片 | 126 | 全服 | [已核] |\n\n"
               "【金句摘录】材料槽要 126 个布片（原话）\n\n"
               "【风险核查】8000 金币那条待核，不许写成建议\n\n"
               "【来源】测试号《魔兽无限服怎么查一件装备的获取条件》，2026-10-24（素材库 id=99998）")
    cards = wx._fallback_cards({"title": "查装备获取条件三步", "content": ARTICLE}, "")["cards"]
    sec_card = [c for c in cards if c.get("title") == "正文要点"][0]
    got_names = "、".join(i["name"] for i in sec_card["items"])
    ok = check("「正文要点」那张卡拿到的是三个小节，不是段落碎句",
               len(sec_card["items"]) == 3 and "打开物品页" in got_names
               and "所以这一段只作为线索" not in got_names, got_names) and ok
    aud_ok = da.audit_material(ARTICLE, "")
    passed = [r[1] for r in aud_ok["rows"] if r[0] == "PASS"]
    ok = check("一篇真写了正文的文章能过结构关（分节齐/正文有内容/标注在位）",
               {"分节完整", "正文有内容", "来源类型标注"} <= set(passed),
               "PASS：%s；FAIL：%s" % ("、".join(passed),
                                        [r[2][:40] for r in aud_ok["rows"] if r[0] == "FAIL"])) and ok
    # 这条才是真正拦假阳性的：结构关过了却整篇被判「不能发」，等于把人往回赶
    ok = check("这样一篇文章不该被判「不能发」", aud_ok["verdict"] != "fail",
               "%s / %s" % (aud_ok["verdict"],
                            "、".join(r[1] for r in aud_ok["rows"] if r[0] == "FAIL"))) and ok
    def with_body(new_body):
        """只换【正文】那一节，其余节保持原样——这样测出来的是正文这一关，不是别处塌了。"""
        head, rest = ARTICLE.split("【正文】\n", 1)
        _old, tail = rest.split("\n\n【关键数据】", 1)
        return head + "【正文】\n" + new_body + "\n\n【关键数据】" + tail

    thin = with_body("## 打开物品页\n很短的一句话\n## 材料数\n也很短\n## 金币门槛\n还是短")
    ok = check("正文每节只有一句话的，判 FAIL（读者照不做起不来）",
               da.audit_material(thin, "")["verdict"] == "fail"
               and any(r[1] == "正文有内容" for r in da.audit_material(thin, "")["rows"] if r[0] == "FAIL"),
               [r[1] for r in da.audit_material(thin, "")["rows"] if r[0] == "FAIL"]) and ok
    heads_only = with_body("## 打开物品页\n## 材料数怎么读\n## 金币门槛")
    ok = check("只有小节标题、一行内容都没有的，判 FAIL",
               da.audit_material(heads_only, "")["verdict"] == "fail") and ok
    no_mark = with_body(ARTICLE.split("【正文】\n", 1)[1].split("\n\n【关键数据】", 1)[0]
                        .replace(" [已核]", "").replace(" [待核]", ""))
    ok = check("一条标注都没有的文章判 FAIL（分不清本人核实还是转述别人）",
               any(r[1] == "来源类型标注" for r in da.audit_material(no_mark, "")["rows"]
                   if r[0] == "FAIL")) and ok

    print("\n[W17 存量旧骨架稿（库里 8 篇）仍要能出图，不许改成一动就崩]")
    legacy = wx._fallback_cards(SUMMARY, "")["cards"]
    lk = [c for c in legacy if c.get("title") == "正文要点"][0]
    ok = check("旧【正文骨架】稿读得到、6 张卡齐", len(legacy) == 6 and len(lk["items"]) >= 2,
               "%d 张 / 要点 %d 条" % (len(legacy), len(lk["items"]))) and ok
    ok = check("旧形态进体检会被判「成稿形态」不合格（读得懂，但不放过）",
               any(r[1] == "成稿形态" for r in da.audit_material(SUMMARY["content"], "")["rows"])) and ok

    print("\n[W18 渠道策略权威源口径跟着改（下发的是判据，不是注释）]")
    cfg = cs.RADAR_CHANNEL_STRATEGY["wechat"]
    ok = check("wechat 策略写明主体成品是文章、配图可选",
               cfg.get("primary_product") == "article" and cfg.get("graphics_optional") is True
               and "完整富文本文章" in cfg["desc"], "%s" % cfg.get("primary_product")) and ok
    ok = check("头条策略那一份没被这次改动带进去（toutiao 键里不许出现文章口径）",
               "primary_product" not in cs.RADAR_CHANNEL_STRATEGY["toutiao"]) and ok
    ok = check("版本号 +1（工作台靠它发现口径漂了）",
               cs.CHANNEL_STRATEGY_VERSION == 5, cs.CHANNEL_STRATEGY_VERSION) and ok

    print("\n[W19 素材台账：编号对应的是「文章」还是「视频」、发布日真给进去]")
    # 第一次真生成抓到两个缺陷：官方公告被写成「视频1」，【来源】的发布日只能写"素材未给"。
    ART1 = dict(VIDEO, id=1, source_sheet="暴雪官方", published_at="2026-10-21")
    ART2 = dict(VIDEO, id=2, source_sheet="微信文章", published_at="2026-10-26")
    CLIP = dict(VIDEO, id=3, source_sheet="抖音视频数据", published_at="")
    pc2 = cs._collection_prompt([ART1, ART2, CLIP], "wechat_material")
    ok = check("台账逐条给出类型（文章/视频）与发布日，缺日期才写素材未给",
               "素材1｜文章（来源表：暴雪官方）" in pc2 and "发布日：2026-10-21" in pc2
               and "素材3｜视频" in pc2 and "发布日：" in pc2, pc2[pc2.find("素材台账"):][:150]) and ok
    ok = check("来源那几节的措辞不再把人往「视频」上带",
               "来源视频编号" not in pc2 and "来源编号" in pc2 and "别把文章写成视频" in pc2) and ok
    ok = check("素材块标题对公众号改成「输入素材」",
               "输入素材（编号与文末台账一一对应）" in pc2) and ok
    ok = check("单源档也带台账",
               "素材台账" in cs._prompt(ART1, "wechat_material")) and ok
    ok = check("头条那两档一个字没变（仍是「输入视频」、没有台账）",
               "输入视频" in cs._collection_prompt([ART1, ART2], "toutiao_mix")
               and "素材台账" not in cs._collection_prompt([ART1, ART2], "toutiao_mix")
               and "素材台账" not in cs._prompt(ART1, "game_guide")) and ok

    print("\n[W21 成稿这条链：格式标记由程序写死、泄漏判据不误杀、落盘与改稿回读]")
    import tempfile
    from src.graphics import gate as _g
    gate_err_cls = _g.GraphicsGateError
    good_body = ("引子第一段，讲清这篇要解决什么。[不该有标注]\n\n### 一、第一小节\n"
                 + "这一步在游戏里怎么点，写完一段。\n" * 3 + "图1：这一屏要拍到什么\n配图任务：截那一屏\n\n")
    good_body += "\n".join("### %s、小节%d\n%s" % ("二三四五", i, "内容句子。" * 30) for i in range(1, 6))
    good_body += ("\n\n### 一句我自己的话\n我们的判断是这样，边界也写清楚。"
                  "\n\n### 收尾\n下一篇写另一件事。"
                  "\n\n### 信息来源\n1. 魔兽世界运营团队公告，2026-10-21。")
    good = {"titles": ["候选甲", "候选乙", "候选丙"], "chosen_index": 2,
            "version_note": "数核到 10-26，发前要重算 38/175 这两个。",
            "body": "## 正文\n" + good_body.replace("[不该有标注]", ""),
            "digest": "摘要一句话，控制在 120 字内。", "checklist": ["勾 AI 辅助", "375px 预览"]}
    md_ok = wx.assemble_article(good, {"id": 97, "title": "T", "content": "x"})
    ok = check("五个固定标记由程序写死（模型自己写 ## 正文 也只留一份）",
               all(m in md_ok for m in wx.ARTICLE_MARKERS)
               and md_ok.count("## 正文") == 1, md_ok.count("## 正文")) and ok
    ok = check("合格成稿不该被判泄漏（【标题候选】【版本口径】是发布稿自己的头部）",
               not [i for i in wx.lint_article(md_ok) if i.startswith("error")],
               [i[:52] for i in wx.lint_article(md_ok)]) and ok
    leak = md_ok.replace("候选乙", "[已核] 候选乙").replace("### 信息来源", "### 来源")
    lk = [i for i in wx.lint_article(leak) if i.startswith("error")]
    ok = check("整理稿记号漏进正文 → error；「信息来源」被改名 → error",
               any("溯源标注" in i for i in lk) and any("信息来源" in i for i in lk),
               "、".join(i[:34] for i in lk)) and ok
    ok = check("自查清单里提到「[已核][他述][待核]」不误判成泄漏（豁免只给这一节）",
               not [i for i in wx.lint_article(
                   md_ok.rstrip() + "\n- [ ] 正文未出现[已核][他述][待核]等方括号记号。\n")
                    if i.startswith("error") and "溯源标注" in i]) and ok
    ok = check("标注漏在标题候选/正文这类读者会看到的地方，照样判 error",
               any(i.startswith("error") and "溯源标注" in i for i in
                   wx.lint_article(md_ok.replace("2. 候选乙", "2. [已核] 候选乙")))) and ok
    ok = check("但正文真留下标注，照样判 error",
               any(i.startswith("error") and "溯源标注" in i for i in wx.lint_article(
                   md_ok.replace("### 收尾", "### 收尾\n这句是编的。[已核]\n")))) and ok
    ok = check("整理稿节名出现在发布稿任何一处都判 error",
               any(i.startswith("error") and "节名" in i for i in wx.lint_article(
                   md_ok.replace("### 收尾", "### 收尾\n【关键数据】\n")))) and ok
    ok = check("摘要超 120 字硬线 → error",
               any(i.startswith("error") and "摘要" in i for i in
                   wx.lint_article(md_ok.replace("摘要一句话，控制在 120 字内。", "长" * 121)))) and ok
    ok = check("字数区间是从排版机器门现读的，不是抄在仓库里的数",
               wx._article_word_range() == (2500, 4000) or wx._article_word_range() is None,
               wx._article_word_range()) and ok
    led = cs.source_ledger([{"id": 1, "source_sheet": "暴雪官方", "author": "魔兽世界运营团队",
                             "published_at": "2026-10-21"},
                            {"id": 2, "source_sheet": "微信文章", "author": "魔兽世界情报局",
                             "published_at": "2026-10-26"}])
    pr_art = wx._build_article_prompt({"content": "素材正文"}, "拿到表先查哪四样",
                                      wx._article_word_range(), led)
    ok = check("成稿提示词把四条硬规矩写进去了（只用整理稿事实/标注换人话/待核不许当建议/必须有信息来源）",
               all(k in pr_art for k in ("不许编", "换成读者能懂的话", "不许写成能照做的建议",
                                         "### 信息来源", "没有合适的图就不要写图行")),
               "缺项检查通过" ) and ok
    old_out = wx.OUTPUT_DIR
    try:
        wx.OUTPUT_DIR = Path(tempfile.mkdtemp(prefix="wxart_"))
        meta = {"titles": good["titles"], "chosen_index": 2, "version_note": good["version_note"],
                "body": good["body"], "digest": good["digest"], "checklist": good["checklist"]}
        (wx.OUTPUT_DIR / "97").mkdir(parents=True)
        (wx.OUTPUT_DIR / "97" / "article.md").write_text(md_ok, encoding="utf-8")
        got = wx.read_article(97)
        ok = check("成稿能按整理稿 id 读回（详情页与列表都从这里取，不复制第二份状态）",
                   bool(got) and "## 正文" in got["md"], bool(got)) and ok
        saved = wx.save_article(97, md_ok.replace("候选乙", "候选乙改定了"))
        ok = check("人工改稿存回去会**按改后这份重判**（标题跟着选定那行走）",
                   saved["title"] == "候选乙改定了" and saved.get("updated_at"),
                   saved.get("title")) and ok
        ok = check("改坏的稿子存回去立刻报 error，不留着旧判定骗人",
                   any(i.startswith("error") for i in
                       wx.save_article(97, md_ok.replace("### 信息来源", "### 别的"))["lint"])) and ok
        ok = check("没有成稿时读它是 404 语义（None），不是空文件冒充",
                   wx.read_article(424242) is None) and ok
        try:
            wx.save_article(424242, "x")
            ok = check("没有成稿时改稿被拒", False, "居然存进去了") and ok
        except gate_err_cls as e:
            ok = check("没有成稿时改稿被拒（不新建一份空文章）", "还没有成稿" in str(e), str(e)[:40]) and ok
    finally:
        wx.OUTPUT_DIR = old_out
    tt_src = (ROOT / "src" / "graphics" / "channels" / "toutiao.py").read_text(encoding="utf-8")
    ok = check("成稿提示词带素材台账，并明写「标文章的绝不能叫视频」",
               "素材台账" in pr_art and "素材1｜文章" in pr_art
               and '绝不能写成"视频"' in pr_art,
               pr_art[pr_art.find("素材台账"):][:60] if "素材台账" in pr_art else "没有台账") and ok
    ok = check("没台账时要求写「素材未记录」而不是自己编日期",
               "素材未记录" in wx._build_article_prompt({"content": "x"}, "这篇只讲怎么排本")) and ok
    ok = check("路由真的把台账取出来传进去了（不是留个空串静默降级）",
               "source_ledger" in (ROOT / "web" / "app.py").read_text(encoding="utf-8")) and ok
    ok = check("头条渠道模块里没有被塞进公众号发布稿的标记（串线判据）",
               "## 公众号摘要" not in tt_src and "ARTICLE_MARKERS" not in tt_src) and ok

    print("\n[W22 发布边界：能发的那一份与只能自己看的那一份分开（2026-10-27 97 号诊断 P0）]")
    p_ok = wx.article_verdict(md_ok)
    ok = check("拆分给出选定标题／正文／摘要／审稿区，干净稿判可发",
               p_ok["selected_title"] == "候选乙" and p_ok["publishable"]
               and "### 一、第一小节" in p_ok["body"] and "摘要一句话" in p_ok["digest"],
               p_ok["selected_title"][:30]) and ok
    FORBID = ("【标题候选】", "【版本口径】", "发布状态", "## 发布前自查清单", "候选甲")
    ok = check("「复制正文」那一份里没有标题候选／版本口径／发布状态／自查清单／别的候选标题",
               not [f for f in FORBID if f in p_ok["body"]],
               [f for f in FORBID if f in p_ok["body"]]) and ok
    ok = check("审稿区把这几块收回去（信息不丢，只是不发给读者）",
               all(f in p_ok["review"] for f in FORBID[:4])) and ok
    ok = check("配图任务从正文剥进审稿区，图行留在正文（那是插图位不是待办）",
               "配图任务" not in p_ok["body"] and "配图任务" in p_ok["review"]
               and "图1：" in p_ok["body"]) and ok
    todo_md = md_ok.replace("### 收尾", "### 发布前还要回查的三件事\n\n- 版本阶段还没确认。\n")
    tp = wx.article_verdict(todo_md)
    ok = check("正文里冒出「发布前还要回查」这类编辑待办 → blocked、判不可发、lint 出 error",
               len(tp["blocked"]) >= 1 and not tp["publishable"]
               and any(i.startswith("error") and "编辑待办" in i for i in wx.lint_article(todo_md)),
               str(tp["blocked"][:1])[:70]) and ok
    ok = check("编辑待办那几行进审稿区（拦住的同时要看得见是哪几行）",
               "发布前还要回查" in tp["review"]) and ok
    ok = check("提示词允许过的读者视角写法「这一条我们还没核实，先别照着做」不误拦",
               not wx.publish_parts(md_ok.replace(
                   "### 收尾", "### 收尾\n这一条我们还没核实，先别照着做。\n"))["blocked"]) and ok
    ok = check("「这篇发布在12月10日之前」这种陈述平台时间的句子不误拦（判据要挨着「前」字）",
               not wx.publish_parts(md_ok.replace(
                   "### 收尾", "### 收尾\n这篇发布在12月10日之前时，团本那一段按预告理解。\n"))["blocked"]) and ok
    ok = check("自查清单整块漏进正文（- [ ] 那些行）也判 blocked",
               bool(wx.publish_parts(md_ok.replace(
                   "### 收尾", "### 收尾\n- [ ] 到客户端确认名称\n"))["blocked"])) and ok
    broken = md_ok.replace("## 正文", "## 内文")
    bp = wx.article_verdict(broken)
    ok = check("缺固定标记时不硬拆：missing 写清缺哪块，publishable=False",
               not bp["publishable"] and bool(bp["missing"])
               and any("正文" in x for x in bp["missing"]), str(bp["missing"])[:60]) and ok
    ok = check("「信息来源」那种编号清单不被误判成一屏灰墙（长段判据按行形态，不按块字数）",
               not [i for i in wx.lint_article(md_ok) if "超过 200 字" in i],
               [i[:40] for i in wx.lint_article(md_ok)]) and ok

    print("\n[W23 单篇切口：空 ask 拦在花钱之前]")
    try:
        wx._build_article_prompt({"content": "x"}, "")
        ok = check("空切口被拒", False, "居然拼出提示词了") and ok
    except gate_err_cls as e:
        ok = check("空切口被拒（不再拿万金油默认句把几个主题订成一篇）",
                   "切口" in str(e), str(e)[:44]) and ok
    pr_s = wx._build_article_prompt({"content": "x"}, "这篇只讲九座地下城按等级怎么排")
    ok = check("操作者写的那句原话进提示词，并明写「只写这一件事，其他主题不写」",
               "这篇只讲九座地下城按等级怎么排" in pr_s and "只写上面那一件事" in pr_s) and ok
    ok = check("提示词明写编辑待办不许进正文、来源要有链接",
               "编辑待办不许出现在正文里" in pr_s and "必须把链接带上" in pr_s) and ok
    led_u = cs.source_ledger([{"id": 1, "source_sheet": "暴雪官方", "author": "魔兽世界运营团队",
                               "published_at": "2026-10-21",
                               "source_url": "https://wow.blizzard.cn/news/24303009/index.html"}])
    ok = check("台账带出 source_url（宣称可回查就得给链接，库里有就不该写素材未给）",
               "https://wow.blizzard.cn/news/24303009/index.html" in led_u, led_u[-70:]) and ok
    ok = check("库里真没链接时写「素材未记录」，不拼一个假地址",
               "链接：素材未记录" in cs.source_ledger([{"id": 2, "source_sheet": "微信文章"}])) and ok

    print("\n[W24 切口建议从整理稿现算、旧成稿先存档再覆盖]")
    topic_src = ("【标题候选】\n- 一个合订标题\n\n【正文】\n"
                 "## 一、三个团本怎么选\n普通正文一行，不该被当成主题。\n"
                 "## 二、九座地下城等级与位置 [已核]\n## 五、发布前必须确认的3个点\n"
                 "## 信息来源\n")
    tops = wx.topic_suggestions({"content": topic_src, "title": "T"})
    ok = check("切口建议只取小节标题，跳过正文行与「发布前」那种待办节",
               len(tops) == 2 and any("一、三个团本" in t for t in tops)
               and not any("发布前" in t or "信息来源" in t or "普通正文" in t for t in tops),
               str(tops)) and ok
    ok = check("建议里不带 [已核] 这类整理稿记号（不能把中间产物递给操作者）",
               not any("[" in t for t in tops), str(tops)) and ok
    ok = check("整理稿没有小节标题时退回标题那句，不给空列表",
               bool(wx.topic_suggestions({"content": "【正文】\n没有小标题的一行\n",
                                          "title": "只有一个主题"}))) and ok
    old_out2 = wx.OUTPUT_DIR
    real_llm = wx._llm_article
    try:
        wx.OUTPUT_DIR = Path(tempfile.mkdtemp(prefix="wxart_prev_"))
        (wx.OUTPUT_DIR / "98").mkdir(parents=True)
        (wx.OUTPUT_DIR / "98" / "article.md").write_text("旧的一版，里面有人工改过的字\n", encoding="utf-8")
        wx._llm_article = lambda *a, **k: good            # 桩：不发收费请求
        meta98 = wx.generate_article({"id": 98, "title": "T", "content": "素材正文"},
                                     {"api_key": "sk-test", "model": "stub"},
                                     ask="这篇只讲九座地下城按等级怎么排")
        prevs = sorted(x.name for x in (wx.OUTPUT_DIR / "98").glob("article.prev-*.md"))
        ok = check("已有成稿时先存 article.prev-时间戳.md 再覆盖（人工改过的字不静默丢）",
                   len(prevs) == 1 and "人工改过的字" in (wx.OUTPUT_DIR / "98" / prevs[0]).read_text(encoding="utf-8")
                   and meta98.get("prev_saved") == prevs[0],
                   str(prevs)) and ok
        ok = check("覆盖后 article.md 是新那一版，读回来直接带拆分结果",
                   wx.read_article(98)["publish"]["selected_title"] == "候选乙",
                   wx.read_article(98)["publish"]["selected_title"][:30]) and ok
    finally:
        wx._llm_article = real_llm
        wx.OUTPUT_DIR = old_out2

    print("\n[W25 两道门一句判据：发布前体检与成稿窗不许说反话]")
    def _row(res, name):
        got = [r for r in res["rows"] if r[1] == name]
        return got[0] if got else None

    r_todo = _row(da.audit(md_text=todo_md), "正文混没混编辑待办")
    ok = check("体检也把「发布前还要回查」判 FAIL（97 号实测它当时判「可以发」，两道门互相矛盾）",
               bool(r_todo) and r_todo[0] == "FAIL", r_todo[2][:60] if r_todo else "(这一项没跑)") and ok
    r_clean = _row(da.audit(md_text=md_ok), "正文混没混编辑待办")
    ok = check("干净稿在体检里判 PASS（不是新加一条谁都会红的门）",
               bool(r_clean) and r_clean[0] == "PASS", r_clean[0] if r_clean else "(这一项没跑)") and ok
    r_mat = _row(da.audit(md_text=ARTICLE), "正文混没混编辑待办")
    ok = check("整理稿（不是公众号发布稿）不受这条影响（头条那条线零改动）",
               r_mat is None, r_mat[1] if r_mat else "无此行") and ok

    print("\n[W26 一份最终判定：机检 error 也算进「能不能复制」]")
    pp = wx.publish_parts(md_ok)
    ok = check("publish_parts 不再返回 publishable（判定只有一个出口，不许两处各判一次）",
               "publishable" not in pp and "structure_ok" in pp, sorted(pp.keys())) and ok
    mark = md_ok.replace("### 收尾", "### 收尾\n这一句带着整理稿记号。[待核]\n")
    mv = wx.article_verdict(mark)
    ok = check("正文插一个 [待核]：lint 报 error 且最终判定翻成不可发（Codex 构造的那条，旧实现还给绿条）",
               any(i.startswith("error") and "溯源标注" in i for i in mv["lint"])
               and mv["publishable"] is False and mv["structure_ok"] is True,
               [e[:34] for e in mv["errors"]]) and ok
    dg = wx.article_verdict(md_ok.replace("摘要一句话，控制在 120 字内。", "字" * 121))
    ok = check("摘要超硬线这类非结构 error 也拦得住复制（不是只管编辑待办一种）",
               dg["publishable"] is False and any("摘要" in e for e in dg["errors"]),
               [e[:30] for e in dg["errors"]]) and ok
    ok = check("干净稿在最终判定里仍是可发（没有把门无限收紧）",
               wx.article_verdict(md_ok)["publishable"] is True) and ok
    old_out3 = wx.OUTPUT_DIR
    import json as _json
    try:
        wx.OUTPUT_DIR = Path(tempfile.mkdtemp(prefix="wxart_lint_"))
        (wx.OUTPUT_DIR / "97").mkdir(parents=True)
        (wx.OUTPUT_DIR / "97" / "article.md").write_text(md_ok, encoding="utf-8")
        # article.json 里存一份**过时的**判定（模拟"生成当时没错、判据后来改了"或人工改过稿）
        (wx.OUTPUT_DIR / "97" / "article.json").write_text(
            _json.dumps({"lint": ["warn: 这是生成当时存的旧判定"], "chars": 1}), encoding="utf-8")
        rd = wx.read_article(97)
        ok = check("读回时的顶层 lint 就是现算那份（不再把 article.json 里存的旧判定递给界面）",
                   rd["lint"] == rd["publish"]["lint"]
                   and not any("旧判定" in x for x in rd["lint"]),
                   rd["lint"][:1]) and ok
        (wx.OUTPUT_DIR / "97" / "article.md").write_text(todo_md, encoding="utf-8")
        rd2 = wx.read_article(97)
        ok = check("稿子本身有 error 时，读回的顶层 lint 里就有那条 error",
                   any(i.startswith("error") and "编辑待办" in i for i in rd2["lint"])
                   and rd2["publish"]["publishable"] is False,
                   [i[:28] for i in rd2["lint"] if i.startswith("error")]) and ok
    finally:
        wx.OUTPUT_DIR = old_out3

    print("\n[W27 进度条要真实步数：每出一张卡回调一次，没步数的阶段报 None]")
    seen = []
    m27 = wx.generate_graphics(SUMMARY, {}, theme="进度探针",
                               progress_cb=lambda msg, d=None, t=None: seen.append((msg, d, t)))
    n27 = len(m27["cards"])
    steps = [(d, t) for (_msg, d, t) in seen if d]
    ok = check("渲染第 k/N 张逐步报出（k 连号、N 恒等于卡数），不是估的百分比",
               len(steps) == n27 and [d for d, _t in steps] == list(range(1, n27 + 1))
               and all(t == n27 for _d, t in steps), steps) and ok
    ok = check("拿不到步数的阶段（LLM 卡片化/打包）报 None → 界面画流动条而不是假进度",
               any(d is None for (_msg, d, _t) in seen),
               [x[0][:14] for x in seen if x[1] is None][:2]) and ok

    print("\n[W28 体检判据要分档：攻略档不许按公众号素材那九节判]")
    GUIDE = ("# 无限服PvE前瞻\n\n这是引子，说明本篇是播客前瞻整理，不是已实装的版本更新说明，"
             "适合准备回归或开荒的玩家提前做信息准备。\n\n"
             "## 适用场景\n- 关注无限服团本与地下城终局内容的玩家，想提前知道该等什么。\n"
             "- 公会管理人员要为熔火之心这类经典团本做人员规划。\n\n"
             "## 步骤：现在可以做什么\n1. 先把本篇当作播客前瞻情报，不要当作已经实装的版本补丁。\n"
             "2. 跟踪官方是否进一步确认团本乱序开放的具体名单与时间，素材标题说确认但正文没展开。\n\n"
             "## 机制解释\n- 乱序开放：素材标题这么说，正文只提到熔火之心不会消失、会在未来登场。\n\n"
             "## 避坑提醒\n- 公会人员规划不要过早按固定 40 人锁团，人数规模尚未公布。\n\n"
             "## 事实核查清单\n- 2027 年夏季经典团本重置这条目前只是玩家猜测，暴雪尚未确认。\n")
    g_res = da.audit_material(GUIDE, "熔火之心 2027 年夏季 40 人", summary_type="game_guide")
    ok = check("五节齐的攻略稿不再被判「不能发」（号主那篇 #99 实测被误判）",
               g_res["verdict"] != "fail",
               "%s / %s" % (g_res["verdict"], [r[1] for r in g_res["rows"] if r[0] == "FAIL"])) and ok
    ok = check("不再报「缺 9 节」这类公众号素材口径",
               not any("分节完整" == r[1] for r in g_res["rows"]),
               [r[1] for r in g_res["rows"]]) and ok
    ok = check("第一行就说清这一档用的是哪套判据（不许让人以为没列出来就是都过了）",
               g_res["rows"][0][0] == "INFO" and "game_guide" in g_res["rows"][0][2], g_res["rows"][0][2][:40]) and ok
    bad = GUIDE.replace("- 公会人员规划不要过早按固定 40 人锁团，人数规模尚未公布。",
                        "- 待补充").replace("2027 年夏季", "2035 年夏季")
    b_res = da.audit_material(bad, "熔火之心 2027 年夏季 40 人", summary_type="game_guide")
    ok = check("攻略稿真有问题照样拦：空节与占位是 FAIL，对不上号的数字是 WARN（看得见但不拦发布）",
               b_res["verdict"] == "fail"
               and {"空节", "占位没填"} <= {r[1] for r in b_res["rows"] if r[0] == "FAIL"}
               and any(r[1] == "数据可回指" and r[0] == "WARN" for r in b_res["rows"]),
               [(r[0], r[1]) for r in b_res["rows"] if r[0] in ("FAIL", "WARN")]) and ok
    no_intro = GUIDE[GUIDE.index("## 适用场景"):]
    n_res = da.audit_material(no_intro, "熔火之心 2027 年夏季 40 人", summary_type="game_guide")
    ok = check("稿子一上来就是小节（没有引子段）时，不许凭空报一条「空节」",
               not [r for r in n_res["rows"] if r[1] == "空节" and "引子" in r[2]],
               [r[2][:30] for r in n_res["rows"] if r[1] == "空节"]) and ok
    w_res = da.audit_material(ARTICLE, "", summary_type="wechat_material")
    ok = check("公众号素材那一档的九节判据没被这次改动带偏（默认口径不变）",
               any(r[1] == "分节完整" for r in w_res["rows"]),
               [r[1] for r in w_res["rows"]]) and ok
    MIX = ("【标题候选】\n1. 无限服专业抄作业：1制造1采集，别五个全开\n"
           "2. 亡灵圣骑新手任务怎么起手，30 分钟能跑完\n"
           "3. 初级配方强度起飞：绿装蓝装逼近副本装备\n\n"
           "【时间线】\n| 时间 | 事件 |\n|---|---|\n"
           "| 1-2级 | 系统把专业引导任务塞进任务列表，新手村就地学采集 |\n"
           "| 1-75点 | 初级配方强度起飞，绿装蓝装属性逼近甚至部分超过副本装备 |\n\n"
           "【对你的影响】\n- 专业重要性大大提高，练满一个比多开五个划算。\n"
           "- 前期制造装不用再进团本就换掉，过渡期能穿更久。\n\n"
           "【行动建议】\n- 先定一个制造专业加一个采集专业，别五个全开摊平。\n"
           "- 升到 25 级后回头点传承相关的通用天赋，别提前囤材料。\n\n"
           "【原话摘录】\n原话：抄作业｜意思：直接照别人验证过的配方案走，自己别试错\n"
           "原话：起飞｜意思：初级配方强度明显高于旧版本预期\n\n"
           "【风险核查】\n- 数值以正式服实装为准，测试服结论别当承诺看。\n"
           "- 装备属性对比只到\"逼近\"，作者没给具体数值，要自己再核。\n")
    m_ok = da.audit_material(MIX, "无限服 75 点 1-2级", summary_type="toutiao_mix")
    ok = check("头条整合稿按它自己的六个固定标签判（齐了就过）",
               m_ok["verdict"] != "fail" and any(r[1] == "分节" and "齐" in r[2] for r in m_ok["rows"]),
               [r[2][:30] for r in m_ok["rows"] if r[1] == "分节"]) and ok
    m_miss = da.audit_material(MIX[:MIX.index("【时间线】")] + MIX[MIX.index("【对你的影响】"):],
                               "无限服 75 点", summary_type="toutiao_mix")
    ok = check("真缺节照样 FAIL（缺【时间线】，下游按节抽取会拿到空）",
               m_miss["verdict"] == "fail"
               and any(r[1] == "分节" and "时间线" in r[2] for r in m_miss["rows"]),
               [r[2][:40] for r in m_miss["rows"] if r[0] == "FAIL"]) and ok
    u_res = da.audit_material("随便一段没有小标题的文字。" * 30, "", summary_type="ai_interview")
    ok = check("没登记过契约的形态**不判分节**（只报 INFO 说没查）——不再拿一套没核对的结构拦稿子",
               not any(r[0] == "FAIL" and r[1] == "分节" for r in u_res["rows"])
               and any(r[1] == "分节" and "不判分节" in r[2] for r in u_res["rows"]),
               [r[2][:34] for r in u_res["rows"] if r[1] == "分节"]) and ok
    # 噪音样本：链接里的 19 位 id、"- 001." 这种条目编号都不该当数据；8121 是真编出来的数
    noise = (MIX + "- 见 https://v.douyin.com/7685199393477135662/ 这条\n"
             "- 001. 第一条编号\n- 8121 小时这个数素材里没有。\n")
    n_res = da.audit_material(noise, "无限服 75 点 1-2级 25 级 30 分钟", summary_type="toutiao_mix")
    wr = [r for r in n_res["rows"] if r[1] == "数据可回指"]
    ok = check("数字回指在这一档只 WARN 不拦，且链接里的 19 位 id、`001.` 条目编号不算数字",
               wr and wr[0][0] == "WARN" and "7685199393477135662" not in wr[0][2]
               and "001" not in wr[0][2] and "8121" in wr[0][2],
               wr[0][2][:70] if wr else "没跑这一项") and ok
    cs_src = (ROOT / "src" / "content_store.py").read_text(encoding="utf-8")
    drift = [t for spec in da.FORM_SECTIONS.values()
             for t in spec["required"] + spec["optional"] if t not in cs_src]
    ok = check("结构契约不漂移：FORM_SECTIONS 里每个标签都必须在提示词里出现（改了提示词这条就红）",
               not drift, drift) and ok

    shutil.rmtree(OUT, ignore_errors=True)
    print("\n%s" % ("ALL_OK" if ok else "HAS_FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
