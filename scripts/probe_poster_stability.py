# -*- coding: utf-8 -*-
"""海报出图稳定率探针：同一份文案计划连出 N 张，逐张跑 OCR 回读校验。

为什么需要它：目前只有 2 个样本全对，不足以判断「每次都能发」。图面文字是否逐字
正确是这条产线唯一的发布门槛，必须有一眼能看出通过率的测量，而不是凭感觉。

用法：
    python3 scripts/probe_poster_stability.py [N] [ratio]     # 默认 5 张、9:16
产物落在 output/poster/_stability/<序号>/，可人眼复核。
末行 PASS_RATE 形如 5/5；低于全对时会列出失败样本缺失的文案条目。
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

# 一份贴近真实转载场景的固定计划（75 字），只测出图模型的稳定性，不掺 LLM 拆解的方差
PLAN = {
    "title": "追剧党搜了就能看",
    "subtitle": "一个站点搞定",
    "cards": [
        {"t": "直接搜", "d": "想看什么搜什么，点一下就出"},
        {"t": "免登录", "d": "据称不用注册不用翻墙"},
        {"t": "片源全", "d": "国产日韩动漫都有"},
        {"t": "提醒", "d": "功能价格可能调整，以官方为准"},
    ],
    "footer": "内容整理自公开分享",
    "style": "深色影院质感背景，金色雕花边框，信息卡片做成放映票根造型",
}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    ratio = sys.argv[2] if len(sys.argv) > 2 else "9:16"
    ai_config = load_ai_config()
    bad = ai_poster.lint_plan(PLAN)
    if bad:
        print("FAIL 计划本身不合格：" + "；".join(bad))
        return 1
    prompt = ai_poster.build_prompt_text(PLAN, ratio)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    ok_n = 0
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
        t1 = time.time()
        try:
            v = ai_poster.verify_image(png, PLAN, ai_config)
        except Exception as e:
            print("  #%d 校验异常 %.0fs  %s" % (i, time.time() - t1, str(e)[:120]))
            continue
        if v["ok"]:
            ok_n += 1
            print("  #%d 通过  出图%.0fs 校验%.0fs %dB usage=%s"
                  % (i, t1 - t0, time.time() - t1, len(png), usage))
        else:
            print("  #%d 缺字  出图%.0fs 校验%.0fs  missing=%s extra=%r"
                  % (i, t1 - t0, time.time() - t1, v["missing"], v["extra"][:40]))
    print("\n开始时间 %s" % datetime.now().strftime("%H:%M:%S"))
    print("PASS_RATE %d/%d" % (ok_n, n))
    return 0 if ok_n == n else 1


if __name__ == "__main__":
    sys.exit(main())
