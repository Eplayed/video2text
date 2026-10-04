# -*- coding: utf-8 -*-
"""把卖货页拍成一张「目录长图」（含手撕代码那 19 道的标题）——发朋友圈/群里/文章里最容易被看完的形式。

为什么从页面截图而不是用海报产线出图：目录是 95 条文字，海报产线一页 200 字上限，
拆成十几页反而没人翻；而卖货页本身就是按手机阅读排的，截出来就是能直接发的图。
另外占位提示（"还有 2 处没填"）和底部价格条在对外图里要去掉，所以拍之前先注入样式藏掉。

用法：
    python3 scripts/build_toc_image.py            # product/目录长图.png
    python3 scripts/build_toc_image.py --width 430
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "product" / "AI面试答案库-卖货页-v0.2.html"

HIDE = """
.warn, .bar, section:not(#toc):not(#handcode), .qa, footer p { display:none !important; }
html { background:#fff !important; }
body { background:#fff !important; }
.wrap { max-width:none !important; }
.cta { display:none !important; }
.hero { border-bottom:none !important; }
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=430)
    ap.add_argument("--out", default=str(ROOT / "product" / "目录长图.png"))
    ap.add_argument("--split", type=int, default=2400,
                    help="每段高度（CSS 像素）。整张 7000+ 像素长，微信里会被压得看不清，"
                         "默认切成几张能连着发的。设 0 出一整张。")
    args = ap.parse_args()
    if not PAGE.exists():
        print("先跑 scripts/build_bank_page.py 生成卖货页：%s 不存在" % PAGE)
        return 1

    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page(viewport={"width": args.width, "height": 900}, device_scale_factor=2)
        pg.goto(PAGE.as_uri(), wait_until="load")
        pg.add_style_tag(content=HIDE)
        pg.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        pg.wait_for_timeout(600)
        total = pg.evaluate("() => Math.ceil(document.body.getBoundingClientRect().height)")
        out = Path(args.out)
        if not args.split or total <= args.split:
            pg.screenshot(path=str(out), full_page=True)
            print("已写出：%s（%dx%d，%.1f MB）"
                  % (out, args.width * 2, total * 2, out.stat().st_size / 1048576))
        else:
            n = 0
            y = 0
            while y < total:
                n += 1
                h = min(args.split, total - y)
                stem = out.stem
                part = out.with_name("%s-%d.png" % (stem, n))
                pg.screenshot(path=str(part), full_page=True,
                              clip={"x": 0, "y": y, "width": args.width, "height": h})
                y += h
            print("已写出 %d 张：%s-1.png … %s-%d.png（整页高 %d CSS 像素）"
                  % (n, out.stem, out.stem, n, total))
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
