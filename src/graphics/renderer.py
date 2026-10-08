# -*- coding: utf-8 -*-
"""Playwright 渲染内核：量 .wrap 高度 → 等比缩放 → 截图（Phase 1 抽取）。

画布尺寸（canvas_w/canvas_h）由调用方注入，渠道无关。
"""
import shutil
import tempfile
from pathlib import Path


def render_html(page, html, png_path, canvas_w, canvas_h):
    """把一段 HTML 渲染成 PNG。

    中间 HTML 写在系统临时目录、渲染完就删——成品目录只该留图。
    以前直接落 img*.html 到成品目录，用户看到一堆「不知道能不能删」的中间产物
    （2026-10-08 明确要求不再产出）。必须走文件而不能 set_content：
    CSS 与配图全是 file:// 绝对 URI，about:blank 下加载不到。
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="v2t_render_"))
    try:
        html_path = tmp_dir / (Path(png_path).name + ".html")
        html_path.write_text(html, encoding="utf-8")
        render_card_html(page, html_path, png_path, canvas_w, canvas_h)
    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


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
