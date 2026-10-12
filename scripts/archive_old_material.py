# -*- coding: utf-8 -*-
"""archive_old_material.py — 把一个月前的老素材与整理稿「先归档、校验、再删」

为什么要这个工具：素材库和整理稿的文本其实很小（1380 条素材 + 72 篇整理稿 = 7.8 MB），
真正被拖慢的是**列表条数**；但号主说以后要做大数据，所以老数据不能一删了之，
得留一份**能直接喂进分析管线的原始档**（JSONL.gz），而不是只留一份人看的摘要。

三条口径是 2026-10-12 号主拍的：
  ① 切割线按发布日期；没有发布日期的按入库时间（updated_at）兜进来；
  ② **只切抖音视频**——微信文章 / B站视频是 DB 直写的取材池，一条不动；
  ③ 素材被删后它已出的图（poster / toutiao / wechat 产物）与帧池**一起打包进归档再删**。

安全设计（删数据这件事不可逆，所以宁可啰嗦）：
  - 默认 **dry-run**，只打印要动什么；必须显式 `--apply` 才真删；
  - 删之前先把整份 video2text.db 复制进归档包（真正的后悔药，7.8 MB）；
  - 归档写完必须**回读校验**：条数对得上、sha256 对得上、tar 能列出目录，
    任何一项不过就直接中止，一条都不删；
  - 删除复用工作台同一条链路：content_store.delete_video（级联删整理稿、清合并稿
    source_video_ids 引用）→ 清 Excel 行（**只清内容不删行，保持行号稳定**）→
    重建 video_index.json 并显式移除已删条目。自己另写一套 DELETE 迟早和界面上
    的删除行为不一致。

用法：
    python3 scripts/archive_old_material.py                    # 看清单（默认切 30 天前）
    python3 scripts/archive_old_material.py --cutoff 2026-09-12
    python3 scripts/archive_old_material.py --cutoff 2026-09-12 --apply
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / "output"
DB_PATH = OUTPUT_DIR / "video2text.db"
EXCEL_PATH = OUTPUT_DIR / "抖音视频信息.xlsx"
INDEX_PATH = ROOT / "video_index.json"
ARCHIVE_DIR = OUTPUT_DIR / "archive"

DOUYIN_SHEET = "抖音视频数据"
EXCEL_COLS = 15          # 清空前 15 列（与 web/app.py 的删除口径一致）
ARTIFACT_BASES = ("poster", "toutiao", "wechat")


def _rows(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def pick_scope(cutoff):
    """算出这次要归档并删除的东西。只认抖音视频那一个 sheet。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=8000")
    try:
        vids = _rows(conn, """
            SELECT * FROM videos
            WHERE COALESCE(source_sheet,'') = ?
              AND ( (COALESCE(published_at,'') <> '' AND published_at < ?)
                 OR (COALESCE(published_at,'') =  '' AND COALESCE(updated_at,'') <> ''
                     AND updated_at < ?) )
            ORDER BY published_at, id""", (DOUYIN_SHEET, cutoff, cutoff))
        vids = [v for v in vids if (v.get("source_row") or 0) > 0]
        idset = set(v["id"] for v in vids)
        sums = _rows(conn, "SELECT * FROM ai_summaries WHERE video_id IS NOT NULL")
        sums = [s for s in sums if s.get("video_id") in idset]
        # 合并稿：成员被删光才跟着删；还剩活成员的只清引用（delete_video 里已经这么干）
        merge_only = _rows(conn, """
            SELECT * FROM ai_summaries
            WHERE COALESCE(source_video_ids,'') LIKE '%,%'""")
        touched = []
        for s in merge_only:
            try:
                ids = json.loads(s.get("source_video_ids") or "[]")
            except (TypeError, ValueError):
                continue
            if any(i in idset for i in ids):
                s["_remain"] = len([i for i in ids if i not in idset])
                touched.append(s)
        awemes = sorted(set(str(v.get("aweme_id") or "").strip() for v in vids
                            if v.get("aweme_id")))
        arts = []
        for base in ARTIFACT_BASES:
            for s in sums:
                p = OUTPUT_DIR / base / str(s["id"])
                if p.is_dir():
                    arts.append(str(p))
        for a in awemes:
            for base in ("assets/keyframes", "assets/frames"):
                p = OUTPUT_DIR / base / a
                if p.is_dir():
                    arts.append(str(p))
        return {"cutoff": cutoff, "videos": vids, "summaries": sums,
                "merge_touched": touched, "awemes": awemes, "artifacts": arts}
    finally:
        conn.close()


def _size(paths):
    tot = 0
    for p in paths:
        for r, _d, fs in os.walk(p):
            for f in fs:
                try:
                    tot += os.path.getsize(os.path.join(r, f))
                except OSError:
                    pass
    return tot


def _sha(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()[:16]


def _write_jsonl_gz(path, rows):
    with gzip.open(str(path), "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    return path


def digest_stats(scope):
    """人看的那份「压缩总结」：删掉之前先把这批数据的形状说清楚。"""
    vids = scope["videos"]
    by_month, by_cat, by_author, tl = {}, {}, {}, []
    for v in vids:
        m = (v.get("published_at") or "无日期")[:7]
        by_month[m] = by_month.get(m, 0) + 1
        by_cat[(v.get("category") or "未分类")] = by_cat.get(v.get("category") or "未分类", 0) + 1
        au = (v.get("author") or "无名")[:18]
        by_author[au] = by_author.get(au, 0) + 1
        tl.append(len(v.get("transcript") or ""))
    lines = ["# 归档摘要（切割线 %s 之前，只抖音视频）" % scope["cutoff"], ""]
    lines.append("- 素材 **%d** 条；整理稿 **%d** 篇；合并稿被清引用 %d 篇" % (
        len(vids), len(scope["summaries"]), len(scope["merge_touched"])))
    lines.append("- 口播文本合计 %.2f MB，平均每条 %d 字；有转写的 %d 条" % (
        sum(tl) / 1048576.0, (sum(tl) // max(1, len(tl))), sum(1 for x in tl if x > 20)))
    lines.append("- 产物与帧池一起打包 %d 个目录，%.1f MB" % (
        len(scope["artifacts"]), _size(scope["artifacts"]) / 1048576.0))
    lines.append("- 视频链接（nwm_url 早过期，只留作溯源）%d 条有值" % sum(
        1 for v in vids if (v.get("video_url") or "").startswith("http")))
    lines += ["", "## 按月分布", "", "| 月份 | 条数 |", "|---|---|"]
    for m in sorted(by_month):
        lines.append("| %s | %d |" % (m, by_month[m]))
    lines += ["", "## 分类分布", "", "| 分类 | 条数 |", "|---|---|"]
    for c, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        lines.append("| %s | %d |" % (c, n))
    lines += ["", "## 作者前 12", "", "| 作者 | 条数 |", "|---|---|"]
    for a, n in sorted(by_author.items(), key=lambda x: -x[1])[:12]:
        lines.append("| %s | %d |" % (a, n))
    lines += ["", "## 整理稿类型", "", "| 类型 | 篇数 |", "|---|---|"]
    ty = {}
    for s in scope["summaries"]:
        ty[s.get("summary_type") or "未标"] = ty.get(s.get("summary_type") or "未标", 0) + 1
    for k, n in sorted(ty.items(), key=lambda x: -x[1]):
        lines.append("| %s | %d |" % (k, n))
    lines += ["", "## 怎么恢复", "",
              "`videos.jsonl.gz` / `summaries.jsonl.gz` 是一行一条的完整原始记录"
              "（含全部列，未做任何字段裁剪），可直接喂 pandas / Spark。",
              "`database/video2text.db` 是删除**之前**的整库副本，"
              "把它覆盖回 output/video2text.db 即恢复全部数据；"
              "`artifacts.tar.gz` 里是产物目录与帧池原文件；`manifest.json` 有逐文件 sha256 与条数。",
              "注意：Excel 里对应的行会被清空内容（行号保留），所以恢复时要一并把 "
              "`workbook/` 里那份 xlsx 放回去。", ""]
    return "\n".join(lines)


def write_archive(scope, out_dir):
    """导出 + 打包 + 校验清单。返回 manifest dict。"""
    os.makedirs(str(out_dir), exist_ok=True)
    mf = {"made_at": time.strftime("%Y-%m-%d %H:%M:%S"), "cutoff": scope["cutoff"],
          "source_sheet": DOUYIN_SHEET,
          "counts": {"videos": len(scope["videos"]), "summaries": len(scope["summaries"]),
                     "merge_touched": len(scope["merge_touched"]),
                     "artifacts": len(scope["artifacts"])},
          "ids": {"video_ids": sorted(v["id"] for v in scope["videos"]),
                  "summary_ids": sorted(s["id"] for s in scope["summaries"]),
                  "aweme_ids": scope["awemes"]},
          "files": {}}
    v_p = _write_jsonl_gz(out_dir / "videos.jsonl.gz", scope["videos"])
    s_p = _write_jsonl_gz(out_dir / "summaries.jsonl.gz", scope["summaries"])
    m_p = _write_jsonl_gz(out_dir / "merge_touched.jsonl.gz", scope["merge_touched"])
    for p, n in ((v_p, len(scope["videos"])), (s_p, len(scope["summaries"])),
                 (m_p, len(scope["merge_touched"]))):
        mf["files"][os.path.basename(str(p))] = {"lines": n, "bytes": os.path.getsize(str(p)),
                                                 "sha256": _sha(p)}
    with open(str(out_dir / "summary.md"), "w", encoding="utf-8") as f:
        f.write(digest_stats(scope))
    # 产物与帧池打包
    existing = [a for a in scope["artifacts"] if os.path.isdir(a)]
    with tarfile.open(str(out_dir / "artifacts.tar.gz"), "w:gz") as tf:
        for a in existing:
            tf.add(a, arcname=os.path.relpath(a, str(ROOT)))
    # 整库副本 + Excel 原件：真正的后悔药
    os.makedirs(str(out_dir / "database"), exist_ok=True)
    shutil.copy2(str(DB_PATH), str(out_dir / "database" / "video2text.db"))
    if EXCEL_PATH.exists():
        os.makedirs(str(out_dir / "workbook"), exist_ok=True)
        shutil.copy2(str(EXCEL_PATH), str(out_dir / "workbook" / EXCEL_PATH.name))
    for p in (out_dir / "database" / "video2text.db",
              out_dir / "artifacts.tar.gz", out_dir / "summary.md"):
        if p.exists():
            mf["files"][str(p.relative_to(out_dir))] = {
                "bytes": os.path.getsize(str(p)), "sha256": _sha(p)}
    with open(str(out_dir / "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(mf, f, ensure_ascii=False, indent=1)
    return mf


def verify_archive(scope, out_dir):
    """删之前必须过这一关：任何一项不成立就中止，一条都不删。"""
    errs = []
    for name, want in (("videos.jsonl.gz", len(scope["videos"])),
                       ("summaries.jsonl.gz", len(scope["summaries"]))):
        p = out_dir / name
        if not p.exists():
            errs.append("%s 不存在" % name)
            continue
        with gzip.open(str(p), "rt", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        if len(lines) != want:
            errs.append("%s 行数 %d ≠ 应有 %d" % (name, len(lines), want))
        for l in lines[:1]:
            if "aweme_id" not in l and name == "videos.jsonl.gz":
                errs.append("%s 第一行缺字段，像是写坏了" % name)
    man = out_dir / "manifest.json"
    if not man.exists():
        errs.append("manifest.json 不存在")
    else:
        d = json.load(open(str(man), encoding="utf-8"))
        for k, v in (d.get("files") or {}).items():
            p = out_dir / k
            if not p.exists():
                errs.append("归档缺文件 %s" % k)
            elif _sha(p) != v.get("sha256"):
                errs.append("%s sha256 对不上" % k)
    tar = out_dir / "artifacts.tar.gz"
    if tar.exists():
        n = len(tarfile.open(str(tar), "r:gz").getnames())
        if n < 1 and scope["artifacts"]:
            errs.append("artifacts.tar.gz 是空的但要打包 %d 个目录" % len(scope["artifacts"]))
    dbb = out_dir / "database" / "video2text.db"
    if not dbb.exists() or os.path.getsize(str(dbb)) < 102400:
        errs.append("整库副本缺失或过小，不能删")
    return errs


def apply_delete(scope):
    """按工作台同一套动作删：DB 级联 → 清 Excel 行 → 重建索引 → 删产物目录。"""
    from src import content_store
    import openpyxl

    keys = set()
    n_db = 0
    for v in scope["videos"]:
        sheet, row = v.get("source_sheet"), int(v.get("source_row") or 0)
        keys.add((sheet, row))
        if content_store.delete_video(DB_PATH, sheet, row):
            n_db += 1
    wb = openpyxl.load_workbook(str(EXCEL_PATH))
    n_xl = 0
    for ws in wb.worksheets:
        if ws.title != DOUYIN_SHEET:
            continue
        for (_s, r) in keys:
            if 2 <= r <= ws.max_row:
                for col in range(1, EXCEL_COLS + 1):
                    ws.cell(r, col).value = None
                n_xl += 1
    wb.save(str(EXCEL_PATH))
    wb.close()
    try:
        import main as collector          # 工作台里也是这么引的：索引重建只有这一份实现
        collector.update_video_index(str(EXCEL_PATH))
    except Exception as e:
        print("  ! 索引重建失败（下面仍会显式移除已删条目，但要人工看一眼）：%s" % e)
    if INDEX_PATH.exists():
        idx = json.load(open(str(INDEX_PATH), encoding="utf-8"))
        before = len(idx.get("videos") or [])
        idx["videos"] = [x for x in (idx.get("videos") or [])
                         if (x.get("sheet"), int(x.get("row") or 0)) not in keys]
        json.dump(idx, open(str(INDEX_PATH), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        n_idx = before - len(idx["videos"])
    else:
        n_idx = 0
    for a in scope["artifacts"]:
        if os.path.isdir(a):
            shutil.rmtree(a, ignore_errors=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    return {"db": n_db, "excel": n_xl, "index": n_idx, "artifacts": len(scope["artifacts"])}


def main():
    ap = argparse.ArgumentParser(description="归档并删除一个月前的老素材（默认 dry-run）")
    ap.add_argument("--cutoff", default=time.strftime("%Y-%m-%d", time.localtime(
        time.time() - 30 * 86400)), help="早于这个日期的才归档（默认 30 天前）")
    ap.add_argument("--apply", action="store_true", help="真的删；不加就只打印清单")
    args = ap.parse_args()

    scope = pick_scope(args.cutoff)
    arts_mb = _size(scope["artifacts"]) / 1048576.0
    print("切割线：%s 之前（只 %s）" % (args.cutoff, DOUYIN_SHEET))
    print("  素材 %d 条 | 整理稿 %d 篇 | 合并稿被清引用 %d 篇" % (
        len(scope["videos"]), len(scope["summaries"]), len(scope["merge_touched"])))
    print("  产物与帧池 %d 个目录，%.1f MB" % (len(scope["artifacts"]), arts_mb))
    if not scope["videos"]:
        print("没有要归档的素材，收工。")
        return 0
    if not args.apply:
        print("\n[dry-run] 一条都没动。加 --apply 才会归档并删除。")
        print("前 5 条将被删：")
        for v in scope["videos"][:5]:
            print("   R%-4s %s  %s | %s" % (v.get("source_row"),
                                            (v.get("published_at") or "无日期")[:10],
                                            (v.get("author") or "")[:10],
                                            (v.get("title") or "")[:34]))
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = ARCHIVE_DIR / ("素材归档-%s" % stamp)
    os.makedirs(str(ARCHIVE_DIR), exist_ok=True)
    print("\n写归档包 %s ..." % out_dir.name)
    write_archive(scope, out_dir)
    errs = verify_archive(scope, out_dir)
    if errs:
        print("!! 归档校验没过，一条都不删：")
        for e in errs:
            print("   -", e)
        return 1
    print("  校验通过（行数 / sha256 / 整库副本 / tar 都可读）")
    r = apply_delete(scope)
    print("删除完成：DB %d 行 | Excel 清空 %d 行（行号保留）| 索引移除 %d 条 | 产物目录 %d 个"
          % (r["db"], r["excel"], r["index"], r["artifacts"]))
    print("归档包：%s" % out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
