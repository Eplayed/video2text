# -*- coding: utf-8 -*-
"""跑一遍手撕代码题：每条题目的参考实现都要在 node 里真的跑通，跑不通的不许进答案库。

这是这份资料和别的面经最实在的差别——别人是"AI 写的答案"，这里是"执行过的代码"。
每条单独一个临时文件跑，一条语法错不会带崩全量；每条带超时，写挂了的会被判失败而不是卡住脚本。

用法：
    python3 scripts/verify_handcode.py            # 全跑，末行 ALL_OK / FAIL
    python3 scripts/verify_handcode.py --only plimit,retry
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "product" / "handcode.json"
TIMEOUT = 20

PRELUDE = """
let __fail = null;
const assert = (c, m) => { if (!c) throw new Error(m || '断言失败'); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
"""
POSTLUDE = """
})().then(() => console.log('__OK__'), e => { console.log('__ERR__ ' + ((e && e.stack) || e)); process.exitCode = 1; });
"""


def js_of(item, code_map):
    """依赖（如 pLimit、retry）先塞进作用域，再放本题实现，最后放测试表达式。"""
    lines = []
    for dep in item.get("deps") or []:
        if dep not in code_map:
            raise KeyError("依赖 %s 不存在" % dep)
        lines += code_map[dep]
    lines += list(item.get("code") or [])
    lines += list(item.get("tests") or [])
    return PRELUDE + "\n".join(lines) + POSTLUDE


def run_one(name, js):
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
        f.write(js)
        path = f.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "超时（>%ds）没跑完" % TIMEOUT
    finally:
        Path(path).unlink(missing_ok=True)
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"__ERR__ (.*)", out)
    if m:
        return False, m.group(1).strip().splitlines()[0][:160]
    if "__OK__" not in out:
        return False, (out.strip().splitlines() or ["没输出"]) [0][:160]
    return True, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(DATA))
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    items = data.get("items") or []
    code_map = {x["id"]: (x.get("code") or []) for x in items}
    want = set(re.split(r"[,\s]+", args.only.strip())) if args.only else None
    bad = []
    for it in items:
        if want and it["id"] not in want:
            continue
        if not (it.get("code") and it.get("tests")):
            bad.append((it["id"], "缺 code 或 tests"))
            print("  [FAIL] %-12s %s — 缺 code 或 tests" % (it["id"], it.get("title", "")))
            continue
        ok, msg = run_one(it["id"], js_of(it, code_map))
        print("  [%s] %-12s %s%s" % ("PASS" if ok else "FAIL", it["id"],
                                     re.sub(r"\s+", " ", it.get("title", ""))[:38],
                                     "" if ok else " — " + msg))
        if not ok:
            bad.append((it["id"], msg))
    print()
    print("手撕代码题 %d 道，跑通 %d 道" % (len(items), len(items) - len(bad)))
    if bad:
        print("FAIL %d 道：%s" % (len(bad), "、".join(x[0] for x in bad)))
        return 1
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
