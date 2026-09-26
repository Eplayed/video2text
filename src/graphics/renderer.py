# -*- coding: utf-8 -*-
"""Playwright 渲染内核：量 .wrap 高度 → 等比缩放 → 截图（Phase 1 抽取）。

画布尺寸（canvas_w/canvas_h）由调用方注入，渠道无关。
"""


def render_card_html(page, html_path, png_path, canvas_w, canvas_h):
    page.goto(html_path.resolve().as_uri())
    page.wait_for_timeout(250)
    natural_h = page.evaluate("document.querySelector('.wrap').scrollHeight")
    if natural_h > canvas_h:
        page.add_style_tag(content=".wrap{transform:scale(%.4f);transform-origin:top left;width:%dpx}"
                           % (canvas_h / natural_h, canvas_w))
        page.wait_for_timeout(80)
    page.screenshot(path=str(png_path),
                    clip={"x": 0, "y": 0, "width": canvas_w, "height": canvas_h})
