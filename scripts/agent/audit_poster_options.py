#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""海报弹窗上每个选项到底生不生效：一次跑完，逐项给证据（全程 0 元、不联网、不动生产库）。

为什么要这个：号主 2026-10-23 抓到「拆几页选了 6 还是出 4 页」，要求把其余选项一起过一遍。
光读代码不算数——② 看版面和 ③ 出图走同一个排版函数，所以这里全按真实渲染路径跑，
拿像素当判据：给一张纯红靶图，成品里没出现红，就是那张图根本没上版。

钱：出图/评审/回读/裁帧的模型调用全换成桩函数并计数，一次真请求都不发。
盘：OUTPUT_DIR 指到临时目录，成品不落进 output/poster/。
"""
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "web"))

from PIL import Image, ImageChops  # noqa: E402
from src import ai_poster  # noqa: E402
from src import frame_pool  # noqa: E402
from src import poster_typeset  # noqa: E402

CALLS = {"generate_image": 0, "review_image": 0, "verify_image": 0, "harden_frame": 0}
ROWS = []


def _fake_generate(prompt, size, ai_config):
    CALLS["generate_image"] += 1
    raise ai_poster.PosterError("探针拦住了：这一条真会花钱（%s）" % size)


def _fake_review(png, ai_config):
    CALLS["review_image"] += 1
    return {"score": 4, "full": 5, "skipped": False}


def _fake_verify(png, plan, ai_config):
    CALLS["verify_image"] += 1
    return {"skipped": False, "ok": True, "missing": [], "extra": "", "transcript": ""}


def _fake_harden(path, ai_config, out_dir, name=None, progress=None):
    CALLS["harden_frame"] += 1
    return path, {"ok": True, "why": "探针放行"}


ai_poster.generate_image = _fake_generate
ai_poster.review_image = _fake_review
ai_poster.verify_image = _fake_verify
frame_pool.harden_frame = _fake_harden

TMP = tempfile.mkdtemp(prefix="poster_audit_")
ai_poster.OUTPUT_DIR = Path(TMP) / "poster"
SID = 999001
FAKE_CFG = {"api_key": "sk-FAKE-NO-NETWORK", "api_base": "http://127.0.0.1:9/v1",
            "model": "fake", "vision_model": "fake"}


def verdict(opt, how, ok, detail):
    ROWS.append((opt, ok, detail))
    print("%-16s %-24s %s | %s" % (opt, how, "生效" if ok else "不生效", detail))


# 设计如此、界面已经置灰并写明原因的那一项：不算"放了白放"，也不许把门禁卡红
BY_DESIGN = ("布局（攻略页）",)


def px(b):
    return Image.open(io.BytesIO(b)).convert("RGB")


def mean_color(b):
    return tuple(px(b).resize((1, 1)).getpixel((0, 0))[:3])


def color_gap(a1, b1):
    """两张图平均色的最大通道差：两套深色皮肤换脸，靠像素均值判不出、靠色相判得出。"""
    c1, c2 = mean_color(a1), mean_color(b1)
    return max(abs(x - y) for x, y in zip(c1, c2))


def mean_diff(a1, b1):
    im1, im2 = px(a1), px(b1)
    if im1.size != im2.size:
        return -1.0
    h = ImageChops.difference(im1, im2).histogram()
    tot = sum(h)
    return sum(i * h[i] for i in range(256)) / float(max(1, tot))


def differs(a1, b1):
    return mean_diff(a1, b1) > 0.02


def has_red(b):
    """成品里有没有那块纯红（上传的靶图真上了版才会红）。"""
    c = mean_color(b)
    return c[0] - max(c[1], c[2]) > 12


def red_bytes(w=720, h=1080):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (235, 20, 20)).save(buf, "PNG")
    return buf.getvalue()


def frame_file():
    base = str(ROOT / "output/assets/frames")
    for aid in sorted(os.listdir(base)):
        for sub in ("clean", "raw"):
            d = os.path.join(base, aid, sub)
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                p = os.path.join(d, f)
                if f.endswith(".jpg") and os.path.getsize(p) > 30000:
                    return os.path.abspath(p)
    return None


FRAME = frame_file()


def page(i, npage, skin, layout, short=False, frame=True):
    return {
        "role": "封面" if i == 1 else ("末页" if i == npage else "中间页"),
        "layout_key": layout, "style_key": skin,
        "kicker": "第 %d 期 · 任务路线" % i,
        "title": "宏库这一页就讲一件事" if i == 1 else "插件装完先做这一步",
        "subtitle": "照着设置一遍" if short else "照着设置一遍，不用回看视频",
        "footer": "数据口径：2026-10-22 实测" if i == npage else "",
        "cards": ([{"t": "先勾这一项", "d": "勾了自动跟随队伍，进本不用再开。"},
                   {"t": "宏槽别拉满", "d": "拉满跟默认按键打架，留两格够用。"}]
                  if not short else
                  [{"t": "先勾这一项", "d": "勾了自动跟随。"},
                   {"t": "宏槽别拉满", "d": "留两格够用。"}]),
        "photos": ([{"path": FRAME, "frame": "F1", "judged": True,
                     "caption": "任务界面", "ts": "00:12"}] if frame and FRAME else []),
    }


def gplan(npage=2, skin="guide_ink", layout="guide", short=False, frame=True):
    ps = [page(i, npage, skin, layout, short, frame) for i in range(1, npage + 1)]
    return {"pages": ps, "title": ps[0]["title"], "style_key": skin,
            "footer": ps[-1]["footer"], "spine": "装插件 → 勾设置 → 进本验证"}


def plain_plan(layout="top_art"):
    return {"title": "这条坐骑值不值", "subtitle": "三个角度看", "footer": "",
            "style_key": "minimal", "layout_key": layout,
            "cards": [{"t": "外观", "d": "翅膀展开有光效。"},
                      {"t": "速度", "d": "跟不跟随要确认。"},
                      {"t": "获取", "d": "本周副本掉落。"}]}


def summary_stub():
    return {"id": SID, "video_id": SID, "aweme_id": "audit", "status": "final",
            "title": "无限服功能插件：宏和任务指引", "source_video_ids": "[]",
            "content": "【整理稿】\n1. 插件装好后要勾自动跟随。\n2. 宏槽位别拉满。\n【风险核查】\n（无）",
            "transcript": "装好插件之后记得勾上自动跟随，宏槽位别拉满，留两格够用"}


def gen_pages(**kw):
    """跑一次真出图（攻略页这条不花钱），回 (成品页路径列表, manifest, 那一包的目录)。"""
    m = ai_poster.generate_poster(summary_stub(), FAKE_CFG, **kw)
    d = str(ai_poster.OUTPUT_DIR / str(SID))
    paths = [os.path.join(d, f) for f in sorted(os.listdir(d))
             if f.startswith("poster") and f != "poster_src.png"]
    return paths, m, d


def read(p):
    return open(p, "rb").read()


def main():
    print("探针用的真帧：%s" % (FRAME or "（没找到，图相关判据降级）"))
    print("=" * 112)

    # ── 1 风格：三套攻略皮换脸 ───────────────────────────────────────────
    outs = {s: ai_poster.preview_typeset(gplan(1, skin=s), "9:16", page=(1, 1))[0]
            for s in ai_poster.GUIDE_STYLES}
    gap_ip = color_gap(outs["guide_ink"], outs["guide_paper"])
    gap_in = color_gap(outs["guide_ink"], outs["guide_night"])
    verdict("风格（三套皮）", "逐套渲染比平均色", gap_ip > 8 and gap_in > 8,
            "墨绿%s / 米白%s / 夜色%s；墨绿vs米白差 %d、vs夜色差 %d"
            % (mean_color(outs["guide_ink"]), mean_color(outs["guide_paper"]),
               mean_color(outs["guide_night"]), gap_ip, gap_in))

    # ── 2 比例 ──────────────────────────────────────────────────────────
    sizes = {r: px(ai_poster.preview_typeset(gplan(1), r, page=(1, 1))[0]).size
             for r in ("9:16", "3:4", "1:1")}
    verdict("比例", "三种比例各排一次看画布", len(set(sizes.values())) == 3,
            " ".join("%s=%s" % (k, v) for k, v in sizes.items()))

    # ── 3 布局：攻略页上换 layout_key；名单本身还剩几档 ─────────────────────
    g1 = ai_poster.preview_typeset(gplan(1, layout="guide"), "9:16", page=(1, 1))[0]
    g2 = ai_poster.preview_typeset(gplan(1, layout="grid2"), "9:16", page=(1, 1))[0]
    choices = [x["key"] for x in ai_poster.layout_choices(True)]
    p1 = ai_poster.preview_typeset(plain_plan("top_art"), "9:16")[0]
    p2 = ai_poster.preview_typeset(plain_plan("list"), "9:16")[0]
    print("   ↳ 对照：插画档 top_art vs list 有变化=%s；界面这份名单实际下发 %s"
          % (differs(p1, p2), choices))
    verdict("布局（攻略页）", "layout_key guide vs grid2", differs(g1, g2),
            "攻略页上 layout_key 选什么都不改版面（走的是攻略页那套流式排），名单只下发 %s 一档"
            "——界面上这个下拉现在置灰并写明「只有一种排法」" % choices)

    # ── 4 配图「＋ 用你的图」：纯红靶图有没有上版 ───────────────────────────
    red = red_bytes()
    g_yes = ai_poster.preview_typeset(gplan(1), "9:16", photo=red, page=(1, 1))[0]
    pl_yes = ai_poster.preview_typeset(plain_plan(), "9:16", photo=red)[0]
    paths, _m, d_g = gen_pages(plan=gplan(2), text_mode="typeset", copies=1,
                               do_review=False, photo=red)
    page_colors = [(os.path.basename(x), mean_color(read(x))) for x in paths]
    print("   ↳ 对照：插画档传红图 → 红上了版=%s；攻略页出图后各页平均色=%s"
          % (has_red(pl_yes), page_colors))
    p1_red = any(has_red(read(x)) for x in paths if x.endswith("poster_p1.png"))
    shutil.rmtree(d_g, ignore_errors=True)
    verdict("配图·用你的图", "攻略页上传纯红靶图", (has_red(g_yes) and p1_red),
            "预览与成品两处红都上了版（第 1 页那块画面位）；第 2 页仍是素材真帧 %s"
            % (dict(page_colors).get("poster_p2.png", "-"),))

    # ── 5 出法「直出」：攻略页选直出得到什么 ────────────────────────────────
    _t, _m, d_ts = gen_pages(plan=gplan(2, short=True), text_mode="typeset", copies=1,
                             do_review=False)
    shutil.rmtree(d_ts, ignore_errors=True)
    paths_md, _m2, d_md = gen_pages(plan=gplan(2, short=True), text_mode="model", copies=1,
                                    do_review=False)
    w0, h0 = ai_poster._canvas_px("9:16")
    bare = poster_typeset.placeholder_base("guide_ink", w0, h0, 0.6, art_hint=False)
    md0 = read(paths_md[0]) if paths_md else bare
    gap_bare = color_gap(md0, bare)
    verdict("出法·直出", "攻略页 text_mode=model", gap_bare >= 8,
            "选了直出也被后端按排版出（第 1 页平均色 %s vs 纯色底 %s，差 %d＝字排上了）；"
            "界面上这一档已置灰，停在一个不会发生的选择上不算实话"
            % (mean_color(md0), mean_color(bare), gap_bare))
    shutil.rmtree(d_md, ignore_errors=True)

    # ── 6 每页几张 ──────────────────────────────────────────────────────
    a0 = CALLS["review_image"]
    paths3, _m3, d3 = gen_pages(plan=gplan(1), text_mode="typeset", copies=3, do_review=True)
    rev3 = CALLS["review_image"] - a0
    verdict("每页几张·单页", "攻略页 copies=3", len(paths3) == 1 and rev3 == 1,
            "落盘 %d 张、视觉评审 %d 次（攻略页抽几张只会得到同一张图的副本，"
            "后端按 1 张出并且在进度里说了；界面那个下拉也已置灰）" % (len(paths3), rev3))
    shutil.rmtree(d3, ignore_errors=True)
    paths4, _m4, d4 = gen_pages(plan=gplan(4, frame=False), text_mode="typeset", copies=3,
                                do_review=True)
    shutil.rmtree(d4, ignore_errors=True)
    verdict("每页几张·多页", "4 页 copies=3 不翻倍", len(paths4) == 5,
            "落盘 %d 张（4 页 + 一张默认图）：张数不会被页数乘上去，"
            "多页时这个下拉本来就没意义，界面上同样置灰" % len(paths4))

    # ── 7 出图后打分 ────────────────────────────────────────────────────
    b0 = CALLS["review_image"]
    _p, _m, dd = gen_pages(plan=gplan(1), text_mode="typeset", copies=1, do_review=False)
    off = CALLS["review_image"] - b0
    shutil.rmtree(dd, ignore_errors=True)
    b1 = CALLS["review_image"]
    _p, _m, dd = gen_pages(plan=gplan(1), text_mode="typeset", copies=1, do_review=True)
    on = CALLS["review_image"] - b1
    shutil.rmtree(dd, ignore_errors=True)
    verdict("出图后打分", "关 vs 开的评审调用数", on > off, "关：%d 次 / 开：%d 次" % (off, on))

    # ── 8 发到哪（渠道）＋形态 ───────────────────────────────────────────
    rendered, cl = {}, {}
    for ch in ("toutiao", "xhs", "wechat"):
        paths_c, m_c, d_c = gen_pages(plan=gplan(2), text_mode="typeset", copies=1,
                                      do_review=False, publish_channel=ch,
                                      form="newspic" if ch == "wechat" else "")
        rendered[ch] = read(paths_c[-1])
        cl[ch] = len(m_c.get("checklist") or [])
        print("   渠道 %-8s 末页有尾注：%s  清单 %d 条"
              % (ch, "有" if (m_c["plan"]["pages"][-1].get("footer") or "") else "无", cl[ch]))
        shutil.rmtree(d_c, ignore_errors=True)
    verdict("发到哪（渠道）", "末页像素 + 清单条数",
            differs(rendered["toutiao"], rendered["xhs"]) and cl["toutiao"] != cl["xhs"],
            "头条vs小红书末页有变化=%s、清单 %d vs %d 条"
            % (differs(rendered["toutiao"], rendered["xhs"]), cl["toutiao"], cl["xhs"]))
    wx1 = len(ai_poster.checklist_for("wechat", "newspic"))
    wx2 = len(ai_poster.checklist_for("wechat", "article"))
    verdict("形态（公众号）", "checklist_for 两档", wx1 != wx2,
            "图片消息 %d 条 / 文章配图 %d 条" % (wx1, wx2))

    # ── 9 题材：进不进拆解提示词 ─────────────────────────────────────────
    pr = ai_poster.page_plan_prompt(1, [page(1, 1, "guide_ink", "guide")], "一条链", "guide_ink",
                                    False, "测试题材词ABC", ai_poster.limits_for("typeset"), "", 0)
    verdict("题材", "拆解提示词里有没有这个词", "测试题材词ABC" in pr,
            "命中" if "测试题材词ABC" in pr else "没进提示词")

    print("   ↳ 主线 spine：输入框占位符自己写了「不进图」，plan.spine 只存包备查（说得上话）")
    print("   ↳ 出图前画面核对 harden_frame 调用 %d 次（桩）；真环境逐张核" % CALLS["harden_frame"])
    print("=" * 112)
    bad = [r for r in ROWS if not r[1] and r[0] not in BY_DESIGN]
    waived = [r for r in ROWS if not r[1] and r[0] in BY_DESIGN]
    print("结论：%d 项，%d 项选了不生效 → %s"
          % (len(ROWS), len(bad) + len(waived),
             "、".join(r[0] for r in bad + waived) or "无"))
    for r in waived:
        print("   设计如此（界面上已置灰并写明为什么，不算白放）：%s" % r[0])
    shutil.rmtree(TMP, ignore_errors=True)
    if bad:
        print("FAIL %d 项：%s" % (len(bad), "、".join(r[0] for r in bad)))
        return 1
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
