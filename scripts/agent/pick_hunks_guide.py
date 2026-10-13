# -*- coding: utf-8 -*-
"""scripts/agent/pick_hunks_guide.py — 从 git diff 里只挑出我这一轮的 hunk（别人正在改同两个文件）

用法：python3 scripts/agent/pick_hunks_guide.py <文件> <输出 patch> <标记词...>
只保留**包含任一标记词**的 hunk；一条 hunk 里混进了别人的改动会整条被跳过并打印出来，
由人看——绝不做"整文件 add"。
"""
import io
import subprocess
import sys


def main():
    path, out = sys.argv[1], sys.argv[2]
    marks = sys.argv[3:]
    diff = subprocess.run(["git", "diff", "-U3", "--", path],
                          capture_output=True, text=True, check=True).stdout
    lines = diff.splitlines(True)
    head = []
    hunks, cur = [], None
    for ln in lines:
        if ln.startswith("@@"):
            if cur:
                hunks.append(cur)
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
        else:
            head.append(ln)
    if cur:
        hunks.append(cur)
    keep, skip = [], []
    for h in hunks:
        body = "".join(h)
        (keep if any(m in body for m in marks) else skip).append(body)
    with io.open(out, "w", encoding="utf-8") as f:
        f.write("".join(head))
        f.write("".join(keep))
    print("%s：留 %d 个 hunk，跳过 %d 个" % (path, len(keep), len(skip)))
    for s in skip:
        first = s.splitlines()[0]
        added = [l for l in s.splitlines() if l.startswith("+") and not l.startswith("+++")][:3]
        print("  跳过 %s %s" % (first[:46], " / ".join(x.strip()[:40] for x in added)))


if __name__ == "__main__":
    main()
