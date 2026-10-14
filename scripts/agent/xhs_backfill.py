# -*- coding: utf-8 -*-
"""把小红书创作后台「笔记管理」那一屏的数字灌进运营台账（channel=xiaohongshu）。

为什么要这个脚本：小红书没有能 replay 的取数接口，登录态只在浏览器里，
所以每天这一步是「谁坐在电脑前谁读屏」——读屏的人只负责抄数，
**字段对照和 ref_id 规则只留在这一个文件里**，免得每次回填都凭记忆重推一遍。

抄下来的顺序（2026-10-14 对着后台截图逐个图标核过，别再凭印象改）：
    浏览 · 评论 · 点赞 · 收藏 · 分享
后台每篇给的是**累计数**，不是当日数，所以每天回填是把同一行的数往上刷。

用法（两种都行）：
    python3 scripts/agent/xhs_backfill.py --file rows.json
    echo '[{"title":"...","time":"2026-10-13 17:15","views":352,"comments":0,
            "likes":5,"saves":12,"shares":4}]' | python3 scripts/agent/xhs_backfill.py
加 --dry-run 只打印要写的行，不落库。
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:15801"

# 抄数时的列顺序 → 台账字段。多写几个别名，读屏的人不用记字段名。
_ALIASES = {
    "title": ("title", "标题"),
    "time": ("time", "发布时间", "datetime", "date"),
    "views": ("views", "浏览", "reads"),
    "comments": ("comments", "评论", "comment"),
    "likes": ("likes", "点赞", "looks"),
    "saves": ("saves", "收藏"),
    "shares": ("shares", "分享", "转发"),
}


def _pick(row, keys):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return None


def _num(v):
    """后台会写 1.2万 这种缩写；认不出来就返回 None（空着比填错强）。"""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    mult = 1
    if s.endswith("万"):
        s, mult = s[:-1], 10000
    elif s.endswith("亿"):
        s, mult = s[:-1], 100000000
    try:
        return int(round(float(s) * mult))
    except ValueError:
        return None


def normalize(row):
    """一条读屏记录 → 台账 fields。ref_id 用发布时间到分钟，标题他改了我们也不丢行。"""
    day = str(_pick(row, _ALIASES["time"]) or "").strip()
    title = str(_pick(row, _ALIASES["title"]) or "").strip()
    if not day:
        raise ValueError("缺发布时间：这一条没法当一行（%s）" % json.dumps(row, ensure_ascii=False)[:80])
    ref = "xhs:" + day.replace(" ", "T").replace(":", "")[:16]
    fields = {"title": title, "pub_date": day[:10], "source": "小红书后台"}
    for src, dst in (("views", "reads"), ("comments", "comment"), ("likes", "looks"),
                     ("saves", "saves"), ("shares", "shares")):
        n = _num(_pick(row, _ALIASES[src]))
        if n is not None:
            fields[dst] = n
    return ref, fields


def post(base, ref, fields):
    body = json.dumps({"channel": "xiaohongshu", "ref_id": ref, "fields": fields})
    req = urllib.request.Request(base + "/api/ops/article", data=body.encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s %s" % (e.code, e.read().decode("utf-8", "ignore")[:200])}
    except urllib.error.URLError as e:
        return {"error": "工作台没在跑？%s" % e.reason}
    try:
        return json.loads(raw)
    except ValueError:
        return {"error": "返回不是 JSON：%s" % raw[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="读屏结果 JSON（数组）；不给就从 stdin 读")
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
    try:
        rows = json.loads(raw)
    except ValueError as e:
        print("输入不是合法 JSON：%s" % e)
        return 1
    if isinstance(rows, dict):
        rows = [rows]

    ok, bad = 0, []
    for row in rows:
        try:
            ref, fields = normalize(row)
        except ValueError as e:
            bad.append(str(e))
            continue
        if args.dry_run:
            print("会写 %s → %s" % (ref, json.dumps(fields, ensure_ascii=False)))
            ok += 1
            continue
        d = post(args.base, ref, fields)
        if d.get("error"):
            bad.append("%s：%s" % (ref, d["error"]))
        else:
            ok += 1
            r = d.get("row") or {}
            print("已写 %s｜%s｜浏览%s 评论%s 点赞%s 收藏%s 分享%s" % (
                ref, (r.get("title") or "")[:20], r.get("reads"), r.get("comment"),
                r.get("looks"), r.get("saves"), r.get("shares")))
    print("写入 %d 条%s" % (ok, "（dry-run，没落库）" if args.dry_run else ""))
    for b in bad:
        print("跳过：" + b)
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
