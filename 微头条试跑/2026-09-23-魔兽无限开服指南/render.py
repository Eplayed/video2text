#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微头条信息图渲染脚本：量高度 → 等比缩放进 1080x1440 (3:4) → 截图 PNG
用法: python3 render.py <html文件或目录>
"""
import os
import sys
import glob

from playwright.sync_api import sync_playwright

CANVAS_W, CANVAS_H = 1080, 1440


def render_one(page, html_path):
    abs_path = os.path.abspath(html_path)
    png_path = abs_path.replace('.html', '.png')
    page.goto('file://' + abs_path)
    page.wait_for_timeout(300)
    natural_h = page.evaluate("document.querySelector('.wrap').scrollHeight")
    if natural_h > CANVAS_H:
        scale = CANVAS_H / natural_h
        page.add_style_tag(
            content=".wrap{transform:scale(%s);transform-origin:top left;width:%dpx}"
                    % (scale, CANVAS_W)
        )
        page.wait_for_timeout(100)
        print("%s 高度 %d → 缩放 %.3f" % (os.path.basename(html_path), natural_h, scale))
    else:
        print("%s 高度 %d OK" % (os.path.basename(html_path), natural_h))
    page.screenshot(path=png_path, clip={'x': 0, 'y': 0, 'width': CANVAS_W, 'height': CANVAS_H})
    print("  → %s" % png_path)


def main():
    targets = sys.argv[1:]
    if not targets:
        print("用法: python3 render.py <html文件或目录>")
        sys.exit(1)
    files = []
    for t in targets:
        if os.path.isdir(t):
            files.extend(sorted(glob.glob(os.path.join(t, '图*.html'))))
        else:
            files.append(t)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': CANVAS_W, 'height': CANVAS_H})
        for f in files:
            render_one(page, f)
        browser.close()


if __name__ == '__main__':
    main()
