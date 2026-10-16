#!/usr/bin/env python3
"""把各集素材编译成单文件 HTML 学习合集（图片 base64 内嵌，离线可看）。

输入：output/course/<集名>/ 下的 episode.md、timeline.json、frames/，
      以及（若已写好）总结.md —— 有则作为正文，无则退化为分镜时间轴。
输出：output/course/合集.html

用法：
    python3 scripts/course_build_html.py
    python3 scripts/course_build_html.py --frames-per-episode 16 --out /tmp/x.html
"""

import argparse
import base64
import html
import json
import re
from io import BytesIO
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COURSE_DIR = PROJECT_ROOT / "output" / "course"

# 排序：让 导学课 / 单元号 / DLC 各归其位
UNIT_ORDER = {"序": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "DLC": 9}


def classify(name):
    """从目录名推断 (单元键, 排序号, 展示名)。"""
    n = name.strip()
    if n.startswith("导学"):
        return "序", 0, n
    if n.upper().startswith("DLC"):
        m = re.search(r"DLC\s*(\d+)", n.upper())
        return "DLC", int(m.group(1)) if m else 99, n
    m = re.match(r"(\d+)-(\d+)", n)
    if m:
        return m.group(1), int(m.group(2)), n
    m = re.match(r"(\d+)", n)
    if m:
        return m.group(1), 99, n
    return "其他", 99, n


def img_b64(path, max_w, quality):
    """缩图 + 转 JPEG base64，控制合集体积。"""
    try:
        im = Image.open(path)
        if im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, int(im.height * max_w / im.width)), Image.LANCZOS)
        buf = BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def mmss(sec):
    return "%02d:%02d" % (int(sec) // 60, int(sec) % 60)


# ---------- 极简 Markdown 渲染（覆盖本项目笔记用到的语法） ----------

def inline(s):
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def render_md(md):
    out, i, lines = [], 0, md.split("\n")
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()

        if not s:
            i += 1
            continue
        if s in ("---", "***"):
            out.append("<hr>")
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, inline(m.group(2)), lvl))
            i += 1
            continue
        if s.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>%s</blockquote>" % inline(" ".join(buf)))
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            head = [c.strip() for c in s.strip("|").split("|")]
            i += 2
            body = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t = ["<table><thead><tr>"] + ["<th>%s</th>" % inline(c) for c in head] + ["</tr></thead><tbody>"]
            for row in body:
                t.append("<tr>" + "".join("<td>%s</td>" % inline(c) for c in row) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue
        if re.match(r"^[-*]\s+", s) or re.match(r"^\d+\.\s+", s):
            ordered = bool(re.match(r"^\d+\.\s+", s))
            items = []
            while i < len(lines):
                cs = lines[i].strip()
                m2 = re.match(r"^(?:[-*]|\d+\.)\s+(.*)$", cs)
                if not m2:
                    break
                items.append(m2.group(1))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append("<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % inline(x) for x in items), tag))
            continue
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4}\s|>|\||---|\*\*\*|[-*]\s|\d+\.\s)", lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append("<p>%s</p>" % inline(" ".join(buf)))
    return "\n".join(out)


# ---------- 素材装载 ----------

def pick_frames(timeline, want):
    """从时间轴里均匀挑带画面的条目，优先口播非空的（信息密度高）。"""
    shots = [r for r in timeline if r.get("frame")]
    if not shots or want <= 0:
        return []
    step = max(1, len(shots) // want)
    picked = shots[::step]
    return picked[:want]


def pretty(name):
    """目录名 -> 展示名：去下划线，导学课补分隔符。"""
    s = name.replace("_", " ").strip()
    s = re.sub(r"^导学课\s+", "导学课 · ", s)
    return s


def strip_first_heading(md):
    """episode.md / 总结.md 自带一级标题，和页面生成的 h2 重复，去掉。"""
    lines = md.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^#{1,2}\s", lines[0].strip()):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines)


def load_episode(ep_dir, args):
    tl_path = ep_dir / "timeline.json"
    if not tl_path.exists():
        return None
    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    summary = ep_dir / "总结.md"
    body_md = summary.read_text(encoding="utf-8") if summary.exists() else \
        (ep_dir / "episode.md").read_text(encoding="utf-8")
    body_md = strip_first_heading(body_md)

    gallery = []
    for row in pick_frames(tl.get("frames", []), args.frames_per_episode):
        fp = ep_dir / "frames" / row["frame"]
        if not fp.exists():
            continue
        gallery.append({
            "t": mmss(row["t"]),
            "src": img_b64(fp, args.img_width, args.img_quality),
            "caption": (row.get("speech") or "").strip(),
        })
    return {
        "name": ep_dir.name,
        "display": pretty(ep_dir.name),
        "duration": tl.get("duration", 0),
        "has_summary": summary.exists(),
        "body_html": render_md(body_md),
        "gallery": gallery,
        "n_frames": len([r for r in tl.get("frames", []) if r.get("frame")]),
        "n_segs": len(re.findall(r"^\[\d\d:\d\d\]", (ep_dir / "transcript.txt").read_text(encoding="utf-8"), re.M))
        if (ep_dir / "transcript.txt").exists() else 0,
    }


CSS = """
:root{--bg:#f7f6f3;--fg:#1c1b1a;--mut:#6b6862;--line:#e0ddd6;--acc:#b4441f;--card:#fff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:16px/1.8 -apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
#nav{position:fixed;left:0;top:0;bottom:0;width:250px;overflow:auto;padding:22px 16px;
 background:#eceae4;border-right:1px solid var(--line);font-size:13px}
#nav h2{font-size:13px;letter-spacing:.1em;color:var(--mut);margin:0 0 14px;font-weight:600}
#nav a{display:block;color:var(--fg);text-decoration:none;padding:5px 9px;border-radius:6px;margin-bottom:1px}
#nav a:hover{background:#dfddd5}
#nav .u{margin:14px 0 4px;color:var(--acc);font-weight:700;font-size:12px;letter-spacing:.06em}
main{margin-left:250px;padding:44px 56px 120px;max-width:940px}
h1{font-size:31px;line-height:1.35;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:23px;margin:44px 0 12px;padding-bottom:8px;border-bottom:2px solid var(--acc)}
h3{font-size:18px;margin:30px 0 8px}
h4{font-size:16px;margin:22px 0 6px;color:var(--mut)}
p{margin:11px 0}
hr{border:0;border-top:1px solid var(--line);margin:30px 0}
code{background:#e9e6df;padding:1.5px 5px;border-radius:4px;font-size:.88em;
 font-family:ui-monospace,Menlo,monospace}
blockquote{margin:16px 0;padding:12px 18px;background:#efece5;border-left:3px solid var(--acc);
 color:#4a4741;border-radius:0 8px 8px 0}
blockquote p{margin:0}
table{border-collapse:collapse;width:100%;margin:18px 0;font-size:14.5px;background:var(--card)}
th,td{border:1px solid var(--line);padding:9px 12px;text-align:left;vertical-align:top}
th{background:#efece5;font-weight:600}
ul,ol{padding-left:24px;margin:12px 0}
li{margin:5px 0}
a{color:var(--acc)}
.ep{background:var(--card);border:1px solid var(--line);border-radius:14px;
 padding:10px 34px 30px;margin-bottom:46px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.meta{color:var(--mut);font-size:13px;margin:0 0 4px}
.warn{display:inline-block;background:#fdf1d6;color:#8a6100;border:1px solid #f0d9a0;
 padding:1px 8px;border-radius:20px;font-size:12px;margin-left:8px;vertical-align:2px}
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin:22px 0 6px}
.shot{border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fbfaf8}
.shot img{display:block;width:100%}
.shot .cap{padding:8px 11px;font-size:12.5px;line-height:1.6;color:var(--mut)}
.shot .cap b{color:var(--acc);font-variant-numeric:tabular-nums;margin-right:6px}
.galhead{font-size:13px;letter-spacing:.08em;color:var(--mut);margin:34px 0 0;
 padding-top:20px;border-top:1px dashed var(--line);font-weight:600}
.chapter{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--acc);
 border-radius:14px;padding:8px 34px 30px;margin:0 0 26px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.ep.inchapter{padding:6px 26px 22px;margin-bottom:22px;background:#fbfaf8}
.ep.inchapter h3{margin:6px 0 2px;font-size:17px}
.warnbox{background:#fdf1d6;border:1px solid #f0d9a0;color:#8a6100;padding:10px 14px;border-radius:8px}
@media(max-width:900px){#nav{display:none}main{margin:0;padding:26px 18px}}
"""

JS = """
const secs=[...document.querySelectorAll('.ep')];
const links=[...document.querySelectorAll('#nav a')];
addEventListener('scroll',()=>{let cur=secs[0]?.id;
 for(const s of secs){if(s.getBoundingClientRect().top<120)cur=s.id}
 links.forEach(a=>a.style.background=a.getAttribute('href')==='#'+cur?'#dfddd5':'')},{passive:true});
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(COURSE_DIR / "合集.html"))
    ap.add_argument("--frames-per-episode", type=int, default=12)
    ap.add_argument("--img-width", type=int, default=880)
    ap.add_argument("--img-quality", type=int, default=72)
    args = ap.parse_args()

    dirs = [d for d in COURSE_DIR.iterdir() if d.is_dir() and (d / "timeline.json").exists()]

    cat = {}
    tpath = COURSE_DIR / "titles.json"
    if tpath.exists():
        cat = json.loads(tpath.read_text(encoding="utf-8"))
    units_lbl = cat.get("units", {})
    episodes = cat.get("episodes", {})
    unit_docs = cat.get("unit_docs", {})

    def key_of(d):
        meta = episodes.get(d.name)
        if meta:
            return (UNIT_ORDER.get(meta["unit"], 8), meta.get("order", 99))
        u, o, _ = classify(d.name)
        return (UNIT_ORDER.get(u, 8), o)

    eps = []
    for d in sorted(dirs, key=key_of):
        meta = episodes.get(d.name)
        e = load_episode(d, args)
        if not e:
            continue
        if meta:
            e["unit"] = meta["unit"]
            e["display"] = meta["title"]
            e["catalog_dur"] = meta.get("dur", "")
            e["ai"] = meta.get("ai", "")
        else:
            e["unit"] = classify(d.name)[0]
        eps.append(e)

    if not eps:
        raise SystemExit("output/course 下没有可用素材，先跑 course_analyze.py")

    nav, body = [], []
    last_unit = None
    for e in eps:
        anchor = "ep_%s" % re.sub(r"\W", "_", e["name"])
        if e["unit"] != last_unit:
            label = units_lbl.get(e["unit"]) or {
                "序": "序 · 导学", "DLC": "DLC 加餐"}.get(e["unit"], "第 %s 单元" % e["unit"])
            nav.append('<div class="u">%s</div>' % html.escape(label))
            body.append('<h1 class="uhead">%s</h1>' % html.escape(label))
            doc = unit_docs.get(e["unit"])
            if doc:
                dp = COURSE_DIR / doc
                if dp.exists():
                    body.append('<section class="chapter">%s</section>'
                                % render_md(strip_first_heading(dp.read_text(encoding="utf-8"))))
                else:
                    body.append('<p class="warnbox">⚠ 单元章文件缺失：%s</p>' % html.escape(doc))
            last_unit = e["unit"]
        nav.append('<a href="#%s">%s</a>' % (anchor, html.escape(e["display"])))

        # 有单元合并章的单元，各集只保留图集，正文由章统一承载
        in_chapter = bool(unit_docs.get(e["unit"]))

        gal = ""
        if e["gallery"]:
            cards = "".join(
                '<figure class="shot"><img loading="lazy" src="%s" alt="%s">'
                '<figcaption class="cap"><b>%s</b>%s</figcaption></figure>'
                % (s["src"], html.escape(e["display"]), s["t"], html.escape(s["caption"][:110]) or "（无口播）")
                for s in e["gallery"])
            gal = ('<p class="galhead">关键画面 · 摘取 %d/%d 帧</p><div class="gal">%s</div>'
                   % (len(e["gallery"]), e["n_frames"], cards))

        if in_chapter:
            head = '<h3>%s</h3>' % html.escape(e["display"])
            main_body = ""
        else:
            badge = "" if e["has_summary"] else '<span class="warn">仅分镜，未写总结</span>'
            head = '<h2>%s%s</h2>' % (html.escape(e["display"]), badge)
            main_body = e["body_html"]

        body.append(
            '<section class="ep%s" id="%s">%s'
            '<p class="meta">时长 %s · 转写 %d 段 · 关键帧 %d 张</p>%s%s</section>'
            % (" inchapter" if in_chapter else "", anchor, head, mmss(e["duration"]),
               e["n_segs"], e["n_frames"], main_body, gal))

    doc = ("<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
           "<meta name=viewport content='width=device-width,initial-scale=1'>"
           "<title>影视飓风 AI 实战课 · 学习合集</title><style>%s</style></head><body>"
           "<nav id=nav><h2>目 录</h2>%s</nav><main>%s</main><script>%s</script></body></html>"
           % (CSS, "".join(nav), "".join(body), JS))

    Path(args.out).write_text(doc, encoding="utf-8")
    size = Path(args.out).stat().st_size / 1e6
    print("已生成 %s" % args.out)
    print("  %d 集 · %.1f MB · 内嵌 %d 张图" % (len(eps), size, sum(len(e["gallery"]) for e in eps)))
    for e in eps:
        print("   [%-4s] %-30s %s" % (e["unit"], e["name"], "有总结" if e["has_summary"] else "仅分镜"))


if __name__ == "__main__":
    main()
