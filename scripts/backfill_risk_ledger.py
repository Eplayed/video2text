# -*- coding: utf-8 -*-
"""backfill_risk_ledger.py — 把历史整理稿里已经写出来的待核条目灌进台账（幂等、只新增）。

为什么要有这一步：台账是 2026-10-29 新建的，**空台账等于旧稿里那些待核没人继承**——
下一次重跑同一份素材，模型还是漏写，事故照旧。所以先把存量稿里写过的条目按素材入账一次，
之后每版新稿都往上添、只能凭证据往下减。

只写 `risk_ledger` 这一张新表，不动 `ai_summaries`、不动成品包、不发模型请求。
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import fact_gate, risk_ledger          # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="待核事实台账回填（只新增，不改任何旧数据）")
    ap.add_argument("--db", default=str(ROOT / "output" / "video2text.db"))
    ap.add_argument("--apply", action="store_true", help="真写库（默认只干跑报数）")
    a = ap.parse_args()
    db = Path(a.db)
    if not db.exists():
        print("库不存在：%s" % db)
        return 1
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id, video_id, source_video_ids, summary_type, content "
                       "FROM ai_summaries ORDER BY id").fetchall()
    con.close()
    if a.apply:
        risk_ledger.ensure(str(db))
    stats = {"稿": 0, "有条目的稿": 0, "条目": 0, "素材": set()}
    for r in rows:
        stats["稿"] += 1
        ids = []
        try:
            ids = [int(x) for x in json.loads(r["source_video_ids"] or "[]")]
        except (TypeError, ValueError):
            pass
        if r["video_id"]:
            ids.insert(0, int(r["video_id"]))
        ids = sorted({i for i in ids if i})
        items = fact_gate.risk_items(r["content"] or "")
        if not items or not ids:
            continue
        stats["有条目的稿"] += 1
        stats["条目"] += len(items)
        stats["素材"].update(ids)
        if a.apply:
            risk_ledger.record_draft(str(db), ids, r["id"], items)
    print("%s：扫 %d 篇稿，%d 篇带待核，共 %d 条，覆盖 %d 条素材"
          % ("已写入" if a.apply else "干跑", stats["稿"], stats["有条目的稿"],
             stats["条目"], len(stats["素材"])))
    if a.apply:
        con = sqlite3.connect(str(db))
        n, m = con.execute("SELECT COUNT(*), COUNT(DISTINCT material_id) FROM risk_ledger").fetchone()
        con.close()
        print("台账现状：%d 行、覆盖 %d 条素材（全部 status=open，等人工凭证据解除）" % (n, m))
    else:
        print("（干跑，没写库。确认数字对得上再加 --apply）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
