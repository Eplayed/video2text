#!/usr/bin/env python3
"""从小程序缓存里批量取原片：扫描 -> 建清单 -> 补缺 -> md5 校验。

原理：小程序每播放一集，播放器会向课程接口要一次播放地址，
      这个响应连同腾讯云 VOD 的 mp4 都会落进 webview 的 Chromium 磁盘缓存。
      缓存条目头部明文存 URL，接口 JSON 里存 name/duration/md5/size。
      所以「点进去 1-2 秒」就足以让一集的地址出现在磁盘上，无需看完。

用法：
    python3 scripts/course_fetch.py              # 只看清单，不下
    python3 scripts/course_fetch.py --download   # 把缺的下齐
    python3 scripts/course_fetch.py --list-only
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "output" / "course" / "raw"
MANIFEST = RAW_DIR / "manifest.json"

WMPF = Path(os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/app_data/radium"))

# 路径部分必须排除 " \ 和控制字符：缓存条目里 URL 常常紧贴着接口 JSON，
# 用宽松的 [\x21-\x7e] 会跨进 JSON 正文，拼出一个不存在的假 URL。
URL_RE = re.compile(rb'https://[A-Za-z0-9.\-]*vod-qcloud\.com/[^"\\\s\x00-\x1f]{5,200}?\.mp4')
JSON_URL_RE = re.compile(r'"url":"(https://[^"\\]+?\.mp4)"')
# fileId 是十六进制串（含字母），不是纯数字——写成 \d 会一个都匹配不到
FILEID_RE = re.compile(r'/([0-9a-f]{20,40})/m\.v\.f\d+\.mp4')
NAME_RE = re.compile(r'"basicInfo":\{"name":"([^"]*)"')
DUR_RE = re.compile(r'"basicInfo":\{.*?"duration":(\d+)')
MD5_RE = re.compile(r'"md5":"([0-9a-f]{32})"')
SIZE_RE = re.compile(r'"size":(\d+),"totalSize"')


def cache_entries():
    """遍历所有 Chromium 磁盘缓存条目文件。"""
    for d in (WMPF / "web" / "profiles").glob("*/Cache/Cache_Data"):
        if not d.is_dir():
            continue
        for fn in sorted(d.iterdir()):
            if fn.is_file():
                yield fn


def harvest():
    """从缓存里收集 fileId -> 元信息。"""
    found = {}
    for p in cache_entries():
        try:
            with open(p, "rb") as fh:
                head = fh.read(16384)      # 只读头部，别把 58MB 原片整个吞进内存
        except Exception:
            continue
        if b"vod-qcloud" not in head:
            continue
        text = head.decode("latin1")
        # 两个来源：原始字节里的 URL，以及接口 JSON 里的 "url" 字段（后者更可靠）
        candidates = [m.group(0).decode("latin1") for m in URL_RE.finditer(head)]
        candidates += JSON_URL_RE.findall(text)

        for url in candidates:
            fm = FILEID_RE.search(url)
            if not fm:
                continue
            fid = fm.group(1)
            rec = found.setdefault(fid, {"fileId": fid, "url": url})
            rec["url"] = url
            # 同一 fileId 可能出现在纯 URL 条目和完整接口 JSON 条目里，取信息更全的那个
            if b"basicInfo" in head:
                nm = NAME_RE.search(text)
                du = DUR_RE.search(text)
                md = MD5_RE.search(text)
                sz = SIZE_RE.search(text)
                if nm and nm.group(1) not in ("", "default"):
                    rec["name"] = nm.group(1)
                if du:
                    rec["duration"] = int(du.group(1))
                if md:
                    rec["md5"] = md.group(1)
                if sz:
                    rec["full_size"] = int(sz.group(1))
    return found


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fmt_dur(sec):
    if not sec:
        return "?"
    return "%02d:%02d" % (sec // 60, sec % 60)


def main():
    ap = argparse.ArgumentParser(description="批量取小程序课程原片")
    ap.add_argument("--download", action="store_true", help="下载缺失的集")
    args = ap.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    found = harvest()
    print("缓存中扫到 %d 个 VOD fileId\n" % len(found))

    # 本地已有文件按 md5 建索引，避免重复下载
    local = {}
    for f in RAW_DIR.glob("*.mp4"):
        if f.stat().st_size < 1 << 20:
            continue
        local[md5_of(f)] = f.name

    manifest = {}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    rows = []
    for fid, rec in sorted(found.items(), key=lambda x: x[1].get("duration", 0)):
        existing = local.get(rec.get("md5", ""))
        if existing:
            rec["local"] = existing
            rec["state"] = "已有"
        else:
            rec["state"] = "缺失"
            if args.download and rec.get("url"):
                dest = RAW_DIR / ("%s_%s.mp4" % (fid[:8], rec.get("name") or "unknown"))
                print("下载 %s (%s) ..." % (rec.get("name") or fid, fmt_dur(rec.get("duration"))))
                rc = subprocess.call(["curl", "-L", "--fail", "--retry", "3",
                                      "-o", str(dest), "-sS", rec["url"]])
                if rc != 0:
                    rec["state"] = "下载失败"
                    dest.unlink(missing_ok=True) if dest.exists() else None
                elif md5_of(dest) != rec.get("md5"):
                    rec["state"] = "md5不符,已删"
                    dest.unlink(missing_ok=True)
                else:
                    rec["local"] = dest.name
                    rec["state"] = "新下载"
                    local[rec["md5"]] = dest.name
        manifest[fid] = {k: v for k, v in rec.items() if k != "fileId"}
        rows.append(rec)

    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    print("%-10s %-8s %-26s %-10s %s" % ("fileId", "时长", "名称", "状态", "本地文件"))
    for r in rows:
        print("%-10s %-8s %-26s %-10s %s" % (
            r["fileId"][:8], fmt_dur(r.get("duration")), (r.get("name") or "?")[:26],
            r["state"], r.get("local", "")))
    print("\n清单已存：%s" % MANIFEST)

    if not args.download:
        miss = [r for r in rows if r["state"] == "缺失"]
        if miss:
            print("提示：加 --download 才会真的下。")


if __name__ == "__main__":
    main()
