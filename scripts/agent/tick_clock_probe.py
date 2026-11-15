# -*- coding: utf-8 -*-
"""夜间匹配那颗钟到底取的哪个时间——单独起进程测，别拿 15801 试。

复现 Codex 二次复验第 5 条（P2）：`web/app.py` 的 `_planmatch_tick(now=None)` 原来写
`now = now or datetime.now()`，也就是**本机 naive 时间**，而 `plan_series.cn_now()`
把 naive 当北京时间处理。这台机器在东八区时看不出问题；一旦 `TZ=UTC`（Codex 就是这么复现的），
同一瞬间本机 07:09 被当成北京 07:09，22:00 那一档整整偏 8 小时——夜间匹配要么提前跑要么不跑。

判据要落在"定时器交给到期判断的那参数"上，所以这里：
· 在 **TZ=UTC** 的子进程里 `import web/app.py`（只 import，不起服务、不起线程——
  启动块在 `__main__` 里）；
· 把 `plan_series.due_run` 换成**只记录参数**的桩，返回"不到期"，
  所以这一趟一条候选都不写、一个模型都不调；
· 打印三个小时数：桩收到的、真北京此刻的、本机 naive 的。修复后前两者必须相等、
  且与第三者差 8 小时（差 0 只说明"这台机器恰好就是东八区"，测不出东西）。

用法（由 e2e_plan_series.py 自动拉起）：
  env TZ=UTC python3 scripts/agent/tick_clock_probe.py --db /tmp/probe.db
库路径解析出来等于生产库就直接拒绝跑。
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROD_DB = (ROOT / "output" / "video2text.db").resolve()


def install_fake_openai():
    """把 openai 换成"一调就抛"的桩：这一趟真花不出钱（与 poster_test_server 同一招）。"""

    class _Boom(object):
        def __init__(self, *a, **kw):
            raise RuntimeError("探针不允许调模型")

    mod = types.ModuleType("openai")
    mod.OpenAI = _Boom
    sys.modules["openai"] = mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="", help="临时库副本；等于生产库就拒绝启动")
    a = ap.parse_args()
    db = Path(a.db).resolve() if a.db else Path(tempfile.mkdtemp(prefix="tick_probe_")) / "p.db"
    if db == PROD_DB:
        print("REFUSED: --db 指向生产库 %s" % PROD_DB, file=sys.stderr)
        return 2
    if not db.exists():
        db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(PROD_DB), str(db))

    sys.path.insert(0, str(ROOT / "web"))
    sys.path.insert(0, str(ROOT))
    install_fake_openai()
    import app                                       # noqa: E402  只 import，不跑启动块
    from src import plan_series as ps                # noqa: E402

    app.DB_PATH = db
    app.plan_series = ps
    seen = {}
    real_due = ps.due_run

    def stub_due(db_path, now=None, *args, **kw):
        seen["now"] = now
        seen["db"] = str(db_path)
        return {"due": False, "why": "探针桩：一律判不到期", "catchup": []}

    ps.due_run = stub_due
    try:
        app._planmatch_tick()
    finally:
        ps.due_run = real_due

    tick = seen.get("now")
    cn = dt.datetime.now(ps.TZ_CN)
    out = {
        "tz_env": os.environ.get("TZ", ""),
        "tick_hour": tick.hour if tick is not None else -1,
        "tick_min": tick.minute if tick is not None else -1,
        "cn_hour": cn.hour,
        "utc_hour": dt.datetime.utcnow().hour,     # TZ=UTC 时就是"本机 naive"那个值
        "tick_tzinfo": str(tick.tzinfo) if tick is not None and tick.tzinfo else "",
        "stubbed": bool(seen),
        "used_db": seen.get("db", ""),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
