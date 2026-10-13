# -*- coding: utf-8 -*-
"""scripts/agent/pick_hunks.py — 按关键词从某个文件的 diff 里挑出属于某一个关注点的 hunk

用法：python3 scripts/agent/pick_hunks.py <diff 文件> <输出 patch> <关键词…>
没命中的 hunk 就留给下一个提交。混着两边关键词的 hunk 会报出来，不自动收。
"""
import io
import sys


def split(path):
    lines = io.open(path, encoding="utf-8").read().split("\n")
    i = 0
    while i < len(lines) and not lines[i].startswith("@@ "):
        i += 1
    header = lines[:i]
    hunks, cur = [], None
    for ln in lines[i:]:
        if ln.startswith("@@ "):
            if cur:
                hunks.append(cur)
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
    if cur:
        hunks.append(cur)
    return header, hunks


def main():
    src, dst = sys.argv[1], sys.argv[2]
    keys = sys.argv[3:]
    header, hunks = split(src)
    take, rest, both = [], [], []
    for k, hk in enumerate(hunks):
        body = "\n".join(l for l in hk[1:] if l.startswith("+") or l.startswith("-"))
        hit = [t for t in keys if t in body]
        if hit:
            take.append(k)
        else:
            rest.append(k)
    print("hunk %d 个：命中 %s 未命中 %s" % (len(hunks), take, rest))
    for k in take:
        print("   取 %d %s" % (k, hunks[k][0][:70]))
    if both:
        print("   ⚠ 混着的：", both)
    out = "\n".join(header) + "\n" + "\n".join("\n".join(hunks[k]) for k in take) + "\n"
    io.open(dst, "w", encoding="utf-8").write(out)
    print("写出", dst)


if __name__ == "__main__":
    main()
