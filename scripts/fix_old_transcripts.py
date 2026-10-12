# -*- coding: utf-8 -*-
"""fix_old_transcripts.py — 把库里已有的转写按新口径洗一遍（默认只预览，不写库）

为什么需要：10-12 把"繁转简 + 已确认误写回改"接在了 ASR 出口，但那只管**以后**采的素材。
库里 471 条转写是旧口径出来的：实测 164 条（35%）是繁体、还有一批同音错字，
整理稿和海报都从这些字里取文。这一条命令补那一刀。

只做确定性变换，不做任何猜测式改写：
  1) OpenCC t2s 繁→简；
  2) 按 config/term_glossary.json 里 **observed 且没标"待确认"** 的误写对替换。
数值、时间、价格一个字都不碰（替换表里全是词，不含数字）。

用法：
    python3 scripts/fix_old_transcripts.py            # 预览：报会改多少条、举几例
    python3 scripts/fix_old_transcripts.py --apply    # 真改（改前把原值备份到 output/backups/）
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import sqlite3                                                  # noqa: E402
from src import asr_fix, term_glossary                          # noqa: E402

DB = ROOT / "output" / "video2text.db"
TRAD = set("時間說對從會國經進鎖們麼義發變讓單據實後於區東車馬長門開關電話語調談歸賣資費產圖團黨裝壓備優網線級縣醫藥雞")


def trad_count(s):
    return sum(1 for c in (s or "") if c in TRAD)


def wrong_hits(s):
    return sum((s or "").count(w) for w, _c, _d, _s in term_glossary.observed_pairs())


def main():
    ap = argparse.ArgumentParser(description="按新口径洗已有转写（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真写库（不传就只预览）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条，调试用")
    args = ap.parse_args()

    if not DB.exists():
        print("库不在：%s" % DB)
        return 2
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    sql = ("SELECT id, aweme_id, author, length(transcript) n, transcript"
           " FROM videos WHERE transcript IS NOT NULL AND length(transcript) > 0"
           " ORDER BY id DESC")
    rows = conn.execute(sql + (" LIMIT %d" % args.limit if args.limit else "")).fetchall()
    plan, trad_rows, wrong_before = [], 0, 0
    for r in rows:
        raw = r["transcript"] or ""
        wrong_before += wrong_hits(raw)
        new, trad, pairs = asr_fix.normalize_text(raw)
        if new != raw:
            plan.append((r["id"], r["aweme_id"], r["author"], trad, pairs, raw, new))
            trad_rows += 1 if trad else 0
    print("库里转写 %d 条：要改 %d 条（其中含繁体 %d 条）；"
          "改前命中已知误写 %d 处" % (len(rows), len(plan), trad_rows, wrong_before))
    after = sum(wrong_hits(p[6]) for p in plan)
    print("改后这些条命中已知误写 %d 处" % after)
    for p in plan[:5]:
        i, aid, au, trad, pairs, raw, new = p
        print("\n  #%s %s｜繁转简 %d 字、误写回改 %d 处" % (i, (au or "?")[:16], trad, pairs))
        print("    原：…%s…" % raw[:52].replace("\n", " "))
        print("    新：…%s…" % new[:52].replace("\n", " "))
    if not plan:
        print("没有要改的。")
        return 0
    if not args.apply:
        print("\n（这是预览。要真改加 --apply；改前会把原值备份到 output/backups/）")
        return 0

    stamp = __import__("time").strftime("%Y%m%d_%H%M%S")
    bak = ROOT / "output" / "backups" / ("transcripts_before_asr_fix_%s.jsonl" % stamp)
    bak.parent.mkdir(parents=True, exist_ok=True)
    with open(str(bak), "w", encoding="utf-8") as f:
        for i, aid, au, _t, _p, raw, _new in plan:
            f.write(json.dumps({"id": i, "aweme_id": aid, "author": au,
                                "transcript": raw}, ensure_ascii=False) + "\n")
    print("\n原值已备份：%s（%d 条）" % (bak, len(plan)))
    cur = conn.cursor()
    for i, _aid, _au, _t, _p, _raw, new in plan:
        cur.execute("UPDATE videos SET transcript=?, updated_at=updated_at WHERE id=?",
                    (new, i))
    conn.commit()
    conn.close()
    print("已改写 %d 条。注意：整理稿是从转写生成的**旧文本**，不会自动跟着变；"
          "要用新底子请对那条重新生成整理稿。" % len(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
