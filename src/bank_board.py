# -*- coding: utf-8 -*-
"""「答案库与交付」看板取数：把散在 product/ 里的付费库、免费池、交付物、许可判定
汇成一份给工作台用的结构，让人一眼看到"现在能发出去的是什么、干不干净、什么能动"。

为什么要有这个页面（2026-10-09）：答案库这条线的产物已经散成 6 类文件
（.md / 题库 JSON / 三份 PDF / 卖货页 / 知识地图 / 工具包 zip / 免费池 JSON），
而 product/ 整目录被 gitignore——仓库搜索看不到、也没有 git 历史，
只能靠这个页面把"当前实际存在的文件 + 当场跑出来的自检结论"摊开。

三条口径写死在这里，改的时候连产品一起改：
  ① 数字全部现读现算，不缓存也不手抄：题数读 JSON、页数读 PDF 字节、自检跑 audit_gate；
  ② 文件不存在就明说"未生成"，绝不显示 0 或旧数——这页的作用就是发现"忘了重跑"；
  ③ 许可判定表是决策记录，不是爬取结果：新仓库要先核 license 字段再进表，
     没进表的仓库等于没判过，不许搬。
"""
import json
import os
import re
import sys
import subprocess
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCT = os.path.join(ROOT, "product")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# 已核过许可证的外部源（判过才记，没记 = 没判过 = 不许搬）
SOURCE_LEDGER = [
    {"repo": "didilili/ai-agents-from-zero", "stars": 5133, "license": "MIT",
     "verdict": "paid", "note": "已搬 89 题进付费库 v0.4，PDF 来源页有署名"},
    {"repo": "bcefghj/ai-agent-interview-guide", "stars": 3056, "license": "MIT",
     "verdict": "free", "note": "330 条只进小程序免费池，付费产线不读"},
    {"repo": "haizlin/fe-interview", "stars": 26265, "license": "MIT",
     "verdict": "ok", "note": "前端 1000+ 题，做前端 SKU 时的首选源（未搬）"},
    {"repo": "Snailclimb/JavaGuide", "stars": 158893, "license": "Apache-2.0",
     "verdict": "ok", "note": "可商用，需保留声明与 NOTICE（未搬）"},
    {"repo": "doocs/advanced-java", "stars": 79143, "license": "CC-BY-SA-4.0",
     "verdict": "no", "note": "SA 传染：衍生作品必须同样开源，不能锁进付费 PDF"},
    {"repo": "jwasham/coding-interview-university", "stars": 362329, "license": "CC-BY-SA-4.0",
     "verdict": "no", "note": "同上，星数最高也不搬"},
    {"repo": "liyupi/codefather", "stars": 8772, "license": "None",
     "verdict": "no", "note": "无 LICENSE 文件、README 无授权 → 默认保留所有权利；"
                              "且它没有 AI 题库（高频面试题只有 Go/Java/前端/后端）"},
]

VERDICT_LABEL = {"paid": "已搬进付费库", "free": "已搬进免费池", "ok": "可搬（未搬）",
                 "no": "不搬"}


def _read_json(name):
    path = os.path.join(PRODUCT, name)
    if not os.path.isfile(path):
        return None, path
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), path
    except (ValueError, OSError) as e:
        return {"_error": str(e)}, path


def pdf_pages(path):
    """数页：/Count 不可信（Chrome 分页树，别处也有 /Count），只认 /Type /Page 非 Pages。
    与 scripts/build_bank_pdf.py 同一个判据，那边已实测过。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return 0
    return len(re.findall(rb"/Type\s*/Page[^s]", raw))


def _file_info(path, kind):
    st = os.stat(path)
    info = {"name": os.path.basename(path), "kind": kind,
            "kb": round(st.st_size / 1024.0, 1),
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "url": "/api/bank/file?name=" + os.path.basename(path)}
    if path.endswith(".pdf"):
        info["pages"] = pdf_pages(path)
    return info


def artifacts(version):
    """当前版本该有的交付物，缺哪个就报缺哪个（旧版本不进列表，免得发错文件）。"""
    want = [("AI面试答案库-v%s.md" % version, "库正文"),
            ("题库-v%s.json" % version, "结构化数据"),
            ("AI面试答案库-v%s.pdf" % version, "付费 PDF·A4"),
            ("AI面试答案库-v%s-手机竖版.pdf" % version, "付费 PDF·手机竖版"),
            ("AI面试答案库-样章-v%s.pdf" % version, "免费样章"),
            ("AI面试答案库-卖货页-v%s.html" % version, "卖货页"),
            ("AI面试答案库-知识地图.html", "知识地图 HTML"),
            ("AI面试答案库-知识地图.png", "知识地图长图"),
            ("自媒体AI写稿工具包-v1.zip", "免费引流工具包"),
            ("free_bank.json", "免费题库池"),
            ("repo_bank.json", "外部源解析结果")]
    out, missing = [], []
    for name, label in want:
        p = os.path.join(PRODUCT, name)
        if os.path.isfile(p):
            info = _file_info(p, label)
            out.append(info)
        else:
            missing.append("%s（%s）" % (name, label))
    return out, missing


def artifact_names():
    """给 /api/bank/file 当白名单：只允许打开这页列出来的文件，不开放整个 product/。"""
    names = []
    for info in artifacts(_version())[0]:
        names.append(info["name"])
    return set(names)


def _version():
    try:
        import build_interview_bank as bib
        return bib.VERSION
    except Exception:
        return "0.4"


def hook_policy():
    """钩子口径从两处现读：铁律文件标题行（v2.0 那次拍板）+ 机器门里两组正则的条数。"""
    out = {"rules_version": "", "rules_file": "", "onsite_fail": 0, "hook_info": 0,
           "paid": "付费品直接给链接，不设领取门槛", "free": ""}
    path = os.path.expanduser("~/Documents/自媒体/公众号写作铁律.md")
    out["rules_file"] = path
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                out["rules_version"] = f.readline().strip().lstrip("# ").strip()
        except OSError:
            pass
    try:
        from src import draft_audit as da
        out["onsite_fail"] = len(da.COMPLIANCE_PATTERNS)
        out["hook_info"] = len(da.HOOK_PATTERNS)
    except Exception:
        pass
    out["free"] = "免费交付物：关注后回复关键词领取（%s）" % (out["rules_version"] or "口径未读到")
    return out


def overview():
    version = _version()
    import build_interview_bank as bib
    paid, paid_path = _read_json("题库-v%s.json" % version)
    free, free_path = _read_json("free_bank.json")
    ext, ext_path = _read_json("repo_bank.json")
    files, missing = artifacts(version)
    warns = []
    if paid and paid.get("_error"):
        warns.append("题库 JSON 解析失败：%s" % paid["_error"])
        paid = None
    if free and free.get("_error"):
        warns.append("free_bank.json 解析失败：%s" % free["_error"])
        free = None
    try:
        ok, verdict = bib.audit_gate()
    except Exception as e:
        ok, verdict = False, "自检跑不起来：%s" % e
    out = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "product_dir": PRODUCT,
           "paid": {"version": version, "price": bib.PRICE, "audit_ok": ok, "audit": verdict,
                    "meta": (paid or {}).get("meta") or {},
                    "themes": (paid or {}).get("themes") or [],
                    "items": (paid or {}).get("items") or [],
                    "file": os.path.basename(paid_path)},
           "free": {"meta": (free or {}).get("meta") or {},
                    "per_module": (free or {}).get("per_module") or {},
                    "items": (free or {}).get("items") or [],
                    "file": os.path.basename(free_path)} if free else None,
           "external": {"meta": (ext or {}).get("source") or {},
                        "count": (ext or {}).get("count") or 0} if ext else None,
           "artifacts": files, "missing": missing,
           "samples": {"paid": sample_questions((paid or {}).get("items") or [], 3),
                       "free": sample_questions((free or {}).get("items") or [], 3)},
           "ledger": [{"checked": "2026-10-09", "label": VERDICT_LABEL.get(x["verdict"], x["verdict"]),
                       **x} for x in SOURCE_LEDGER],
           "hook": hook_policy(),
           "warnings": warns}
    return out


def sample_questions(items, n=6):
    """给页面抽几道真题看：优先抽带追问的、来源各来一条，别全是同一模块。"""
    picked, seen_mod = [], set()
    for it in items:
        mod = it.get("module") or it.get("theme") or ""
        if mod in seen_mod:
            continue
        if len((it.get("answer") or "")) < 60:
            continue
        picked.append(it)
        seen_mod.add(mod)
        if len(picked) >= n:
            break
    return picked
