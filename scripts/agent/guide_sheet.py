# -*- coding: utf-8 -*-
"""把几页攻略页拼成一张对照图，号主一眼能看出版面差别。

用法：python3 scripts/agent/guide_sheet.py <产物目录> <输出jpg> [页码...]
比如：python3 scripts/agent/guide_sheet.py output/poster/80 temp/guide_样张/80_两页.jpg 1 2
"""
import os
import sys

# 拼样张常被临时脚本调用（走查、e2e 之后随手拼一张），把本目录加进 sys.path，
# 免得调用方每次都要自己 sys.path.insert(0, "scripts/agent")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image

THUMB_H = 1000          # 单页缩到 1000 高，拼一张横向对照图
GAP = 24


def sheet(paths, out):
    ims = [Image.open(p).convert("RGB") for p in paths]
    w = max(im.width for im in ims)
    scale = float(THUMB_H) / max(im.height for im in ims)
    ims = [im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
           for im in ims]
    total_w = sum(im.width for im in ims) + GAP * (len(ims) + 1)
    canvas = Image.new("RGB", (total_w, THUMB_H + GAP * 2), (20, 20, 22))
    x = GAP
    for im in ims:
        canvas.paste(im, (x, GAP))
        x += im.width + GAP
    d = os.path.dirname(out)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    canvas.save(out, quality=88)
    print(out, canvas.size)


if __name__ == "__main__":
    folder, out = sys.argv[1], sys.argv[2]
    pages = sys.argv[3:] or ["1", "2", "3"]
    sheet([os.path.join(folder, "poster_p%s.png" % p) for p in pages
           if os.path.exists(os.path.join(folder, "poster_p%s.png" % p))], out)
