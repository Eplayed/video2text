# -*- coding: utf-8 -*-
"""海报出图稳定率探针：同一份文案计划连出 N 张，逐张跑回读校验 + 视觉评审。

为什么需要它：图面文字是否逐字正确是这条产线唯一的发布门槛，必须有一眼能看出通过率
的测量，而不是凭感觉。同时把视觉评审分一起打出来——「字全对但一张空文字框」和
「画面好看但缺字」是两种完全不同的失败，只有两个数一起看才知道该调哪一头。

注意字数安全线会随画面计划变化：早先 80 字那条线是在"几乎没有插画"的 prompt 上测的。
加了 scene / 卡片配图之后，模型要分算力去画画面，同样的字数更容易丢字，所以这里
PLAN 刻意带完整画面计划，测出来的才是产线现在真实会遇到的通过率。

用法：
    python3 scripts/probe_poster_stability.py [N] [ratio]     # 默认 3 张、9:16
产物落在 output/poster/_stability/<序号>/，可人眼复核。
末行 PASS_RATE 形如 3/3；低于全对时会列出失败样本缺失的文案条目。
"""
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src import ai_poster                                    # noqa: E402
from e2e_ai_poster import load_ai_config                     # noqa: E402

OUT = ROOT / "output" / "poster" / "_stability"
REVIEW_FULL = 5 * len(ai_poster.REVIEW_ITEMS)     # 五项各 1-5 分

# 一份贴近真实转载场景的固定计划：带完整画面计划，只测出图模型的稳定性，不掺 LLM 拆解的方差
PLAN = {
    "title": "追剧党搜了就能看",
    "subtitle": "一个站点搞定",
    "cards": [
        {"t": "直接搜", "d": "想看什么搜什么", "v": "一个放大镜压在片单列表上"},
        {"t": "免登录", "d": "不用注册不用翻墙", "v": "一把被剪断的锁和登录框"},
        {"t": "片源全", "d": "国产日韩动漫都有", "v": "三张电影票叠成一摞"},
        {"t": "提醒", "d": "以官方为准", "v": "一个感叹号立在警示牌上"},
    ],
    "footer": "内容整理自公开分享",
    "scene": "一间家庭影音室，大屏上正在播放影片，前排座椅和爆米花桶清晰可见",
    "style_key": "tool_review",
    "style": "",
}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    ratio = sys.argv[2] if len(sys.argv) > 2 else "9:16"
    ai_config = load_ai_config()
    bad = ai_poster.lint_plan(PLAN)
    if bad:
        print("FAIL 计划本身不合格：" + "；".join(bad))
        return 1
    print("画面侧提示：%s" % (ai_poster.lint_visual(PLAN) or "无"))
    prompt = ai_poster.build_prompt_text(PLAN, ratio)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    ok_n = 0
    scores = []
    print("模型=%s 尺寸=%s 字数=%d 共 %d 次\n" % (ai_poster.IMAGE_MODEL, ai_poster.SIZES[ratio],
                                              ai_poster.plan_chars(PLAN), n))
    for i in range(1, n + 1):
        t0 = time.time()
        try:
            png, usage = ai_poster.generate_image(prompt, ai_poster.SIZES[ratio], ai_config)
        except Exception as e:
            print("  #%d 出图失败 %.0fs  %s" % (i, time.time() - t0, str(e)[:120]))
            continue
        d = OUT / str(i)
        d.mkdir(parents=True, exist_ok=True)
        (d / "poster.png").write_bytes(png)
        (d / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
        t1 = time.time()
        try:
            v = ai_poster.verify_image(png, PLAN, ai_config)
        except Exception as e:
            print("  #%d 校验异常 %.0fs  %s" % (i, time.time() - t1, str(e)[:120]))
            continue
        try:
            r = ai_poster.review_image(png, ai_config)
            rv = "评审 %d/%d 画面%d%% 最弱%s" % (r["score"], r["full"], r["visual_pct"], r["worst"])
            scores.append(r["score"])
        except Exception as e:
            rv = "评审失败 %s" % str(e)[:60]
        if v["ok"]:
            ok_n += 1
            print("  #%d 通过  出图%.0fs 校验%.0fs %dB  %s"
                  % (i, t1 - t0, time.time() - t1, len(png), rv))
        else:
            print("  #%d 缺字  出图%.0fs 校验%.0fs  %s  missing=%s extra=%r"
                  % (i, t1 - t0, time.time() - t1, rv, v["missing"], v["extra"][:40]))
    print("\n开始时间 %s" % datetime.now().strftime("%H:%M:%S"))
    if scores:
        print("评审均分 %.1f/%d" % (sum(scores) / float(len(scores)), REVIEW_FULL))
    print("PASS_RATE %d/%d" % (ok_n, n))
    return 0 if ok_n == n else 1


if __name__ == "__main__":
    sys.exit(main())
