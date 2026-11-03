# -*- coding: utf-8 -*-
"""tools/sync_wow_icons.py — 把 wow-forever 的图标与「实名名单」同步进图文产线

产出两份，都在 output/_assets/ 下（已被 .gitignore 覆盖，暴雪图标一张都不进这个公开仓库）：
  icons_cn.json   名字 → 图标文件，渲染时查它补图
  wow_terms.json  名字 → {类别, 分级, 归属, 图标键, 旧名}，机检与提示词查它统一写法

为什么单向同步而不是直接引用那个仓库：
1. video2text 是 GitHub 公开库，图标与整库数据都不该进 git；
2. 跨仓库读绝对路径，换机或对方挪目录就静默取不到图，本项目最恨静默失效；
3. 数据权威源在 wow-forever（那边有 provenance、缺口清单和发布前检查），这边只做消费方副本。

分级口径照抄上游，不自造（method.json 实测）：**L0 最高**（官方公告用词或客户端实名），
L1/L2 越低（两个挖掘源按坐标对齐、名字没跟官方核对过）。上游纪律是「译名只认官方中文，
官方没发过的职业不填中文名——不机翻、不拿第三方自己翻的叫法冒充官方定名」。
我们消费的口径（2026-10-10 用户拍板）：库名只用来**统一写法和补图标**，不用来引入新实体；
库里查不到的（如混调干果、鱼缸）保留素材原话并标「以游戏内为准」。

键必须带归属前缀：同一个名字在不同职业是两回事，实测裸名查「复仇」会命中德鲁伊那条。
"""
import json
import os
import shutil
import sys
from datetime import datetime

SRC_REPO = os.path.expanduser("~/Documents/project/wow-forever/wow-forever-web")


def _upstream(*parts):
    """上游仓库把 `src/` 改名成了 `app/`，两种布局都认。

    只认一种的代价是实测到的：脚本一启动就 SystemExit，名单冻在改目录那天
    （`output/_assets/wow_terms.json` 停在 10-10，23 天没更新，里面连"天裔"都没有），
    而下游机检看起来"有名单"，谁都不知道它旧了。
    """
    for root in ("app", "src"):
        p = os.path.join(SRC_REPO, root, *parts)
        if os.path.exists(p):
            return p
    return os.path.join(SRC_REPO, "app", *parts)


ICONS_SRC = _upstream("img", "icons")
DATA_SRC = _upstream("data")
TALENTS_SRC = os.path.join(DATA_SRC, "talents")
MAP_SRC = os.path.join(DATA_SRC, "icons.json")
GAPS_SRC = os.path.join(DATA_SRC, "icon-gaps.json")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST_DIR = os.path.join(ROOT, "output", "_assets", "icons")
DST_MAP = os.path.join(ROOT, "output", "_assets", "icons_cn.json")
DST_TERMS = os.path.join(ROOT, "output", "_assets", "wow_terms.json")


def _first_url(doc):
    """资料文件自己的 provenance 里第一个链接（官方公告页），没有就空。"""
    for src in ((doc.get("meta") or {}).get("sources") or []):
        u = str(src.get("url") or "").strip()
        if u:
            return u
    return ""


def _load(rel):
    p = os.path.join(DATA_SRC, rel)
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else None


def collect_terms(have):
    """从上游四类数据里收「实名 + 图标键 + 分级 + 归属」，返回 (terms, icon_map, aliases)。

    terms 的键是裸名字（机检要在稿子里正向扫名字），值里带归属；
    icon_map 的键带归属前缀（渲染补图要按 职业|名字 查，避免同名串图）。
    """
    terms, imap, aliases = {}, {}, {}

    def add(name, kind, src, level, icon, extra=None):
        if not name:
            return
        rec = terms.get(name)
        if rec and rec.get("level", "L9") <= level:
            return                      # 已有更高（或同级）来源，不覆盖
        f = icon + ".jpg" if icon and icon in have else None
        terms[name] = {"kind": kind, "src": src, "level": level, "icon": f}
        if extra:
            terms[name].update(extra)
        if f:
            imap["%s|%s" % (src, name)] = f

    prof = _load("professions.json") or {}
    for p in prof.get("professions") or []:
        pname = p.get("nameCn") or p.get("slug") or "?"
        for r in p.get("recipes") or []:
            add(r.get("nameCn"), "配方", pname, r.get("level") or "L0", r.get("iconKey"),
                {"item_id": r.get("itemId"), "quality": r.get("quality"), "is_new": r.get("isNew")})
            for m in r.get("mats") or []:
                add(m.get("nameCn"), "材料", pname, r.get("level") or "L0", m.get("iconKey"))

    for f in sorted(os.listdir(TALENTS_SRC)):
        if not f.endswith(".json") or f.startswith("_"):
            continue
        d = json.load(open(os.path.join(TALENTS_SRC, f), encoding="utf-8"))
        cname = d.get("classNameCn") or d.get("classId")
        for tree in d.get("trees") or []:
            for n in tree.get("nodes") or []:
                add(n.get("nameCn"), "天赋", cname, n.get("level") or "L2", n.get("iconKey"),
                    {"max_ranks": n.get("maxRanks"), "tree": tree.get("nameCn"),
                     "name_verified": n.get("nameVerified")})

    gl = _load("glossary.json") or {}
    for g in gl.get("items") or []:
        kind = {"talent": "天赋", "racial": "种族特长", "item": "物品"}.get(g.get("kind"), "技能")
        add(g.get("cn"), kind, g.get("classId") or "通用", g.get("level") or "L0", None)

    # search.json 是唯一带「睡袋与营地」这类交互物实名与归属的地方，别漏
    sr = _load("search.json") or {}
    kind_cn = {"camp": "营地与睡袋", "zone": "区域", "rare": "稀有精英", "bok": "书籍",
               "rec": "配方", "gnd": "采集点", "tra": "种族特长", "lpg": "传承专长"}
    for row in sr.get("items") or []:
        if not isinstance(row, list) or len(row) < 5:
            continue
        k = kind_cn.get(row[0])
        if k:
            add(row[1], k, row[2] or "通用", row[4] or "L2", None, {"note": row[2]})

    ab = _load("abilities.json") or {}
    for a in ab.get("items") or []:
        if a.get("state") == "renamed" and a.get("name"):
            tag = str(a.get("officialTag") or "")
            old = tag.replace("原", "").replace("。", "").strip()
            if old and old != a["name"]:
                aliases[old] = a["name"]     # 旧名 → 官方新名，机检拿它判「用了改过的旧名」
        add(a.get("name"), "技能", a.get("classId") or "通用", a.get("level") or "L0", None,
            {"state": a.get("state"), "official_tag": a.get("officialTag")})

    # ── 官方**完整枚举**的两类：种族、职业 ─────────────────────────────
    # 为什么单独标 `official_enum`：官方公告把可选种族/职业一次列全，所以这一类
    # "名单里没有"就是"这个服没有"——11-02 那张卡片写的「德莱尼」正是这么露的馅
    # （口播 0 次、补充资料 0 次、官网 races.json 也 0 次，模型自己安的种族名）。
    # 其余类别（配方/材料/挖掘来的天赋名）是残表，沿用老口径：库里没有 ≠ 写错了。
    def add_enum(name, kind, url, extra=None):
        if not name:
            return
        rec = terms.get(name)
        if rec:
            rec["official_enum"] = True
            rec["enum_kind"] = kind
            if url and not rec.get("url"):
                rec["url"] = url
            if extra:
                rec.update({k: v for k, v in extra.items() if not rec.get(k)})
            return
        add(name, kind, "官方枚举", "L0", None,
            dict({"official_enum": True, "url": url}, **(extra or {})))

    rc = _load("races.json") or {}
    race_url = _first_url(rc)
    for r in rc.get("races") or []:
        add_enum(r.get("nameCn"), "种族", race_url, {"faction": r.get("faction")})
    sk = rc.get("skyborneNote") or {}
    if isinstance(sk, dict):
        add_enum("天裔", "种族", race_url)          # 新种族在 races 里也有，这一句只兜底
    cb = _load("classes.json") or {}
    cls_url = _first_url(cb)
    for c in cb.get("classes") or []:
        add_enum(c.get("cn"), "职业", cls_url)

    for key, icon in (_load("icons.json") or {}).get("map", {}).items():
        if "|" in key and icon + ".jpg" in have:
            imap.setdefault(key, icon + ".jpg")
    return terms, imap, aliases


def _trait_lines(items):
    """把特长/天赋条目拼成人能读的句子（名字：效果。外加官方原句）。

    为什么不用 json.dumps 整坨塞进去：事实闸判的是"这句官方里有没有"，
    它拿整句去比对、中间不能夹 `","` 这类结构符号——JSON 形状会把"天穹视界：移动速度…"切断。
    """
    out = []
    for t in items or []:
        if isinstance(t, dict):
            name = str(t.get("name") or t.get("nameCn") or "").strip()
            eff = str(t.get("effect") or t.get("desc") or t.get("note") or "").strip()
            line = ("%s：%s" % (name, eff)).strip("：")
            if line:
                out.append(line)
            for key in ("quote", "officialQuote"):
                q = str(t.get(key) or "").strip()
                if q:
                    out.append(q)
        elif t:
            out.append(str(t))
    return out


def _prov_lines(rows):
    out = []
    for pr in rows or []:
        if not isinstance(pr, dict):
            continue
        for key in ("quote", "note"):
            v = str(pr.get(key) or "").strip()
            if v:
                out.append("%s（%s）" % (v, pr.get("url") or ""))
    return out


def collect_blobs():
    """把官网资料里的**整句原文**收成一坨坨文本，给事实闸当一手对照面。

    为什么不只带名字：事实闸问的是"这句官方里有没有"（比如「天穹视界：移动速度提高15%
    （原为10%），持续15秒」），词表答不了这种问题。11-02 那篇的补充资料里其实写着这条，
    缺的是"没进对照面"，不是"官方没说"。
    """
    blobs = []
    rc = _load("races.json") or {}
    race_url = _first_url(rc)
    for r in rc.get("races") or []:
        bits = [str(r.get("nameCn") or ""), str(r.get("lore") or "")]
        bits += _trait_lines(r.get("traits"))
        sg = r.get("subgroup") or {}
        if isinstance(sg, dict):
            bits += [str(sg.get("nameCn") or ""), str(sg.get("lore") or "")]
            bits += _trait_lines(sg.get("traits"))
        bits += _prov_lines(r.get("provenance"))
        txt = "\n".join(b for b in bits if b)
        if txt.strip():
            blobs.append("【种族·官方原文 %s】\n%s" % (race_url, txt))
    for row in rc.get("newCombos") or []:
        for pr in (row.get("provenance") or []):
            q = str(pr.get("quote") or "").strip()
            if q:
                blobs.append("【组合·官方原文 %s】\n%s" % (pr.get("url") or race_url, q))
    sk = rc.get("skyborneNote") or {}
    if isinstance(sk, dict) and sk:
        blobs.append("【天裔·官方原文】\n%s" % json.dumps(sk, ensure_ascii=False)[:4000])
    lines = []
    for g in (_load("glossary.json") or {}).get("items") or []:
        for pr in g.get("provenance") or []:
            q = str(pr.get("quote") or "").strip()
            if q and g.get("cn"):
                lines.append("%s：%s（%s）" % (g.get("cn"), q[:160], pr.get("url") or ""))
    if lines:
        blobs.append("【技能与天赋名·官方原句】\n" + "\n".join(lines))
    return blobs


def main():
    # `--terms-only`：只重出名单与映射，不拷图标。图标一次全量要拷上千张，
    # 而"名单旧了"这件事不该被图标拷贝的开销挡着（11-02 就是为了让名单追上官方才跑的）
    terms_only = "--terms-only" in sys.argv
    if not os.path.isdir(DATA_SRC):
        raise SystemExit("找不到 wow-forever 的资料目录 %s——先确认那个仓库在不在原处" % DATA_SRC)
    for p in ((ICONS_SRC, TALENTS_SRC, MAP_SRC) if not terms_only else (TALENTS_SRC, MAP_SRC)):
        if not os.path.isdir(p) and not os.path.isfile(p):
            raise SystemExit("找不到 wow-forever 的 %s——先确认那个仓库在不在原处" % p)
    os.makedirs(DST_DIR, exist_ok=True)

    have = set(os.path.splitext(f)[0] for f in os.listdir(DST_DIR) if f.endswith(".jpg"))
    new = set()
    if not terms_only and os.path.isdir(ICONS_SRC):
        src_keys = set(os.path.splitext(f)[0] for f in os.listdir(ICONS_SRC) if f.endswith(".jpg"))
        new = src_keys - have
        for k in new:
            shutil.copy2(os.path.join(ICONS_SRC, k + ".jpg"), os.path.join(DST_DIR, k + ".jpg"))
        have |= new

    terms, imap, aliases = collect_terms(have)

    gaps = {}
    if os.path.isfile(GAPS_SRC):
        g = json.load(open(GAPS_SRC, encoding="utf-8"))
        gaps = {"count": (g.get("meta") or {}).get("count"), "note": (g.get("meta") or {}).get("note")}

    levels = {}
    kinds = {}
    for t in terms.values():
        levels[t["level"]] = levels.get(t["level"], 0) + 1
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    rule = ("库名只用来统一写法和补图标，不用来引入新实体；查不到就保留素材原话并标「以游戏内为准」；"
            "分级 L0 最高（官方用词/客户端实名）、L2 是两挖掘源对齐未跟官方核对；图上不出现分级标记")
    blobs = collect_blobs()
    with open(DST_TERMS, "w", encoding="utf-8") as fp:
        json.dump({"meta": {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            # 实际读的是哪个目录就写哪个（上游 10 月把 src/ 改名 app/，
                            # 这里写死 "src/data" 会让下一个人以为读的是老布局）
                            "source": os.path.relpath(DATA_SRC, SRC_REPO),
                            "upstream_root": SRC_REPO,
                            "terms": len(terms), "levels": levels, "kinds": kinds,
                            "official_enum": sum(1 for t in terms.values()
                                                 if t.get("official_enum")),
                            "blobs": len(blobs),
                            "aliases": len(aliases), "icon_files": len(have),
                            "upstream_gaps": gaps, "rule": rule},
                   "blobs": blobs,
                   "terms": dict(sorted(terms.items())),
                   "aliases": dict(sorted(aliases.items()))},
                  fp, ensure_ascii=False, indent=1)
    # icons_cn.json 保持原结构（渲染侧在读它），映射换成从 terms 派生，两处不会再各算一遍
    with open(DST_MAP, "w", encoding="utf-8") as fp:
        json.dump({"meta": {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "icon_files": len(have), "mapped": len(imap), "rule": rule},
                   "map": dict(sorted(imap.items()))},
                  fp, ensure_ascii=False, indent=1)
    print("同步完成：%s新拷 %d 张，本地共 %d 张"
          % (("(只重出名单，没动图标)" if terms_only else ""), len(new), len(have)))
    print("实名名单 %d 条（分级 %s）；带图标的映射 %d 条；改名对照 %d 条"
          % (len(terms), levels, len(imap), len(aliases)))
    print("类别分布:", kinds)
    print("产物：", DST_TERMS, "与", DST_MAP, "（output/ 已 gitignore，不进公开仓库）")


if __name__ == "__main__":
    main()

