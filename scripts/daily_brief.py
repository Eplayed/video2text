# -*- coding: utf-8 -*-
"""daily_brief.py — 命令行看今天该发哪篇（判据全在 `src/plan_brief.py`，这里只是壳）。

为什么判据不在这里（2026-10-21）：号主当时要求排产表按颜色区分"哪篇一定要发 / 哪篇可以暂缓"，
颜色跟日报必须是同一套判据——判据写两份迟早互相说反话（本项目反复踩过：
frames.json 写着 usable=17 而界面显示 0 张、界面写"0 元"后端照样扣钱）。
所以逻辑搬到 `src/plan_brief.py`：CLI、`/api/plan/brief`、页面 `schedule-brief.js` 三处共用一份。
档位名字后来按复核 R1 改过：红色不叫"今天就发"了（那读起来像已经过发布审核），现在叫"今日优先处理"。

只读、零成本、不联网、不调模型：库走 `mode=ro`，不写任何文件。

用法：
  env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/daily_brief.py
  ... --date 2026-11-05 --json        # 指定日期 / 出机器可读那份（跟接口同一结构）
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import plan_brief          # noqa: E402  判据唯一来源


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="按哪天算（默认今天），格式 YYYY-MM-DD")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", default="", help="换库路径（默认 output/video2text.db）")
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    b = plan_brief.build(a.db, today)
    print(json.dumps(b, ensure_ascii=False, indent=1) if a.json else plan_brief.render(b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
