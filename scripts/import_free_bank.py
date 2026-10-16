# -*- coding: utf-8 -*-
"""收录**免费题库池**：可商用许可的开源面试题，专门给小程序当免费内容，**不进付费 PDF**。

当前只有一个源：`bcefghj/ai-agent-interview-guide`（MIT，Copyright (c) 2026 bcefghj，3.1K star）
  · docs/01-面试八股文/01–09：9 个模块，每题带「概念 / 原理 / 问答 / 追问 / 代码」
  · docs/06-面试问答集：企业级项目追问 90+ 条（架构设计类 / 技术实现类 / …）
  实际抓到 330 条：205 道八股 Q + 92 条企业级项目问答 + 33 条源里写成「问/答」的追问对
  （追问对按独立条目收——它们本身就是能刷的题，挂到"上一题"反而会挂错）。

为什么单独一个池子（2026-10-09 拍板）：付费库 v0.4 已经 184 题，再并 200 题只会和免费库更同构，
7.9 元的卖点更站不住；而这批题量大、成体系，正适合放小程序当免费题库撑留存与搜索。
**所以它和 `product/repo_bank.json`（付费库的外部来源）是两个池子，付费产线不许读这份。**

许可规矩（三条，改这里就是改产品法务口径）：
  ① 只收有明确宽松许可的源：MIT / Apache-2.0 / BSD / CC0 / CC-BY。
  ② **CC-BY-SA 不收**：SA 要求衍生作品同样 CC-BY-SA 开源，等于不能锁进付费或封闭产品
     （doocs/advanced-java、jwasham/coding-interview-university 都是这一类，星数再高也不搬）。
  ③ 无 LICENSE 的一律不搬（鱼皮 liyupi/codefather 就是这种：license=None、树里没有 LICENSE、
     README 无授权表述 → 默认保留所有权利）。
  署名必须随内容一起显示在界面上，不能只写在文末。

用法：
    python3 scripts/import_free_bank.py                # 抓 + 解析 + 落 product/free_bank.json
    python3 scripts/import_free_bank.py --check        # 只解析本地镜像，不联网
    python3 scripts/import_free_bank.py --mirror-dir product/外部参考/free-repos
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "bcefghj/ai-agent-interview-guide"
COMMIT = "main"
COPYRIGHT = "Copyright (c) 2026 bcefghj · MIT License"
API = "https://api.github.com/repos/%s" % REPO
MIRROR = ROOT / "product" / "外部参考" / "free-repos" / "ai-agent-interview-guide"
OUT = ROOT / "product" / "free_bank.json"

# 题干两种写法：行内加粗 `**Q1：…**`（八股文）和标题 `### Q1: …`（问答集）
Q_INLINE = re.compile(r"^\*\*Q(\d+)\s*[：:]\s*(.+?)\*\*\s*$", re.M)
Q_HEAD = re.compile(r"^#{3,5}\s*Q(\d+)\s*[：:]\s*(.+?)\s*$", re.M)
ANS_LEAD = re.compile(r"^\s*\*\*(?:A|答案|答)\s*[：:]\s*\*\*?\s*", re.M)
CODE = re.compile(r"```(\w*)\n(.*?)```", re.S)


def flatten(s):
    s = re.sub(r"\n\s*[-*]\s*", "；", s or "")
    s = re.sub(r"\s*\n\s*", "", s)
    s = re.sub(r"([。！？；])；", r"\1", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    return re.sub(r"；{2,}", "；", s).strip("； ")


FOLLOW_HEAD = re.compile(r"^[-*]?\s*\*\*\s*(?:追问|问)\s*[：:]\s*(.+?)\s*\*\*\s*$", re.M)
ANS_LABEL = re.compile(r"^\*\*(?:应对|答)\s*[：:]\s*\*\*\s*")


def extract_follows(body):
    """按行抓「追问 / 应对」对，同时把这些行从答案里摘走（否则答案里重复一遍）。

    这份源里至少三种写法：`- **追问：X** ⏎ **应对：** Y`、`**问：X** ⏎ **答：** Y`、
    还有 `**追问应对**` 这种小节标题（没有冒号，不能当追问）。只按一种正则写会漏掉大半
    ——实测 83 处追问只抓到 15 处。
    """
    lines = body.split("\n")
    out, keep, i = [], [], 0
    while i < len(lines):
        t = lines[i].strip()
        m = FOLLOW_HEAD.match(t)
        if not m:
            keep.append(lines[i])
            i += 1
            continue
        ask, buf = m.group(1), []
        i += 1
        while i < len(lines):
            s = lines[i].strip()
            if FOLLOW_HEAD.match(s) or s.startswith("---") or s.startswith("**追问应对**"):
                break
            if s == "" and buf:
                break
            if s == "":
                i += 1
                continue
            buf.append(ANS_LABEL.sub("", s))
            i += 1
        out.append({"ask": flatten(ask), "answer": flatten(" ".join(buf))})
    return flatten("\n".join(keep)), out


def split_block(body):
    """一个 Q 块里拆出：答案正文、追问列表、代码块。代码摘走，避免小程序正文里塞长代码。"""
    codes = [{"lang": m.group(1) or "text", "code": m.group(2).strip()} for m in CODE.finditer(body)]
    body = CODE.sub("", body)
    body, follows = extract_follows(body)
    body = ANS_LEAD.sub("", body)
    body = flatten(body)
    # 「- **问：X** ⏎ **应对：** Y」这种对被当成独立条目后，答案开头会留个"应对："标签，
    # 刷题界面上一眼就是残缺，这里统一摘掉
    body = re.sub(r"^[*\s]*(?:应对|答案|答)\s*[：:]\s*", "", body)
    return body, follows, codes


def parse_module(name, text, module_no, prefix):
    """八股文：`**Qn：题干**` 紧跟 `**A：** 答案`；问答集：`### Qn: 题干` 到下一个 Q 之间。

    id 用「文件号 + 出现顺序」而不是源里的 Q 编号：问答集每换一类就从 Q1 重新数，
    拿编号当 key 会撞（第一类和第二类都叫 Q1–Q18）。源编号仍留在 qno 里给人看。
    """
    marks = [(m.start(), m.end(), m.group(1), m.group(2), "inline") for m in Q_INLINE.finditer(text)]
    marks += [(m.start(), m.end(), m.group(1), m.group(2), "head") for m in Q_HEAD.finditer(text)]
    # 「- **问：X** / **答：** Y」这种对**本身就是一道题**：源里它写在「概念/原理」段里，
    # 位置经常在第一个 Q 之前（297 题那批里 83 处追问有 68 处落在 Q 块外面），
    # 硬挂到"上一题"会挂错内容，所以按位置当独立条目收，一条不丢。
    marks += [(m.start(), m.end(), None, m.group(1), "ask") for m in FOLLOW_HEAD.finditer(text)]
    marks.sort(key=lambda x: x[0])
    groups = [(m.start(), m.group(1).strip()) for m in re.finditer(r"^##\s+(.+)$", text, re.M)]
    items = []
    for i, (start, end, qno, qtext, kind) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        group = ""
        for gs, gt in groups:
            if gs < start:
                group = gt
        answer, follows, codes = split_block(text[end:stop])
        items.append({"id": "f%s.%d" % (prefix, i + 1), "module": name, "module_no": module_no,
                      "group": group, "qno": (int(qno) if qno else None),
                      "question": flatten(qtext), "answer": answer,
                      "followup": follows, "code": codes, "kind": kind})
    return items


def http_get(url, raw=False):
    """本机 Python 3.9 没装 CA 证书，urllib 走 HTTPS 必挂（CERTIFICATE_VERIFY_FAILED），
    所以统一用 curl——这是本机事实，不是风格偏好。raw=True 时按原文取（配合 Accept 头）。"""
    cmd = ["curl", "-s", "--max-time", "120", "-H", "User-Agent: v2t-free"]
    if raw:
        cmd += ["-H", "Accept: application/vnd.github.raw"]
    cmd.append(url)
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError("curl 取不到：%s" % url)
    return r.stdout


def fetch_tree():
    return json.loads(http_get(API + "/git/trees/%s?recursive=1" % COMMIT).decode("utf-8"))


def fetch_blob(sha):
    return http_get("%s/git/blobs/%s" % (API, sha), raw=True).decode("utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只用本地镜像，不联网")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    targets = []
    if args.check:
        for p in sorted(MIRROR.rglob("*.md")):
            targets.append((str(p.relative_to(MIRROR)), p.read_text(encoding="utf-8")))
        if not targets:
            print("本地镜像是空的，先不带 --check 跑一次")
            return 1
    else:
        tree = fetch_tree()
        for t in tree.get("tree") or []:
            p = t["path"]
            if t["type"] != "blob" or not p.endswith(".md"):
                continue
            if p.startswith("docs/01-面试八股文/") and not p.endswith("README.md"):
                targets.append((p, None))
            elif p == "docs/06-面试问答集/README.md":
                targets.append((p, None))
        for i, (p, _) in enumerate(targets):
            sha = [x["sha"] for x in tree["tree"] if x["path"] == p][0]
            text = fetch_blob(sha)
            fp = MIRROR / p
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(text, encoding="utf-8")
            targets[i] = (p, text)

    items, per = [], {}
    for idx, (p, text) in enumerate(targets, 1):
        f = Path(p)
        name = f.stem
        # 问答集那份文件名叫 README.md，直接拿 stem 会全叫 "README"——用上级目录名当模块名
        name = f.parent.name if name == "README" else name
        module_no = re.sub(r"\D", "", name)[:2] or "99"
        label = re.sub(r"^\d+[-·\s]*", "", name)
        got = parse_module(label, text, module_no, "%02d" % idx)
        per[label] = len(got)
        items += got
    for i, it in enumerate(items, 1):
        it["no"] = i
        it["source"] = {"repo": REPO, "license": "MIT", "copyright": COPYRIGHT,
                        "file": next((x for x in per if x == it["module"]), it["module"])}
        it["theme"] = it["module"]

    # 这份源里本来就有一节"一句话速答"（21 条答案只有一行），那不是解析失败；
    # 真解析失败是答案空或短到 12 字以内
    empty = [it["id"] for it in items if len(it["answer"] or "") < 12]
    oneline = len([it for it in items if 12 <= len(it["answer"] or "") < 40])
    doc = {"meta": {"pool": "free", "schema": "freebank.v1", "built": datetime.now().strftime("%Y-%m-%d"),
                    "count": len(items),
                    "sources": [{"repo": REPO, "license": "MIT", "copyright": COPYRIGHT,
                                 "commit": COMMIT, "note": "题目与答案为该仓库整理，未经我们复核；"
                                         "界面上必须随题显示来源署名"}],
                    "note": "免费池：给小程序用，**不进付费 PDF / 卖货页 / 知识地图**"},
           "per_module": per, "items": items}
    bad = []
    if empty:
        bad.append("%d 题答案短于 30 字：%s" % (len(empty), empty[:6]))
    ids = [x["id"] for x in items]
    if len(ids) != len(set(ids)):
        bad.append("id 有重复")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print("解析 %d 题 ｜ 模块 %d 个 ｜ 其中一句话速答 %d 条 ｜ 镜像 %s"
          % (len(items), len(per), oneline, MIRROR))
    for k, v in per.items():
        print("   %-22s %d 题" % (k, v))
    print("已写出：%s（%.1f KB）" % (args.out, Path(args.out).stat().st_size / 1024.0))
    if bad:
        print("要注意：")
        for b in bad:
            print("   ! %s" % b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
