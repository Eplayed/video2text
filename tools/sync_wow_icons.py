# -*- coding: utf-8 -*-
"""tools/sync_wow_icons.py — 把 wow-forever 的图标库与「中文名 → 图标键」映射同步进图文产线

为什么单向同步而不是直接引用那个仓库：
1. video2text 是 GitHub 公开库，暴雪图标一张都不许进 git——落点必须在 output/ 下（已 gitignore）；
2. 跨仓库读绝对路径，换机或对方挪目录就静默取不到图，本项目最恨静默失效；
3. 图标数据的权威源在 wow-forever（那边有 provenance、缺口清单和回归脚本），这边只做消费方副本。

映射必须带职业前缀：同一个名字在不同职业是两回事。实测按裸名字查「复仇」会命中德鲁伊那条，
配上圣骑士的图标，印到图上就是错攻略。
"""
import json
import os
import shutil
import sys
from datetime import datetime

SRC_REPO = os.path.expanduser("~/Documents/project/wow-forever/wow-forever-web")
ICONS_SRC = os.path.join(SRC_REPO, "src", "img", "icons")
TALENTS_SRC = os.path.join(SRC_REPO, "src", "data", "talents")
MAP_SRC = os.path.join(SRC_REPO, "src", "data", "icons.json")
GAPS_SRC = os.path.join(SRC_REPO, "src", "data", "icon-gaps.json")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST_DIR = os.path.join(ROOT, "output", "_assets", "icons")
DST_MAP = os.path.join(ROOT, "output", "_assets", "icons_cn.json")


def main():
    for p in (ICONS_SRC, TALENTS_SRC, MAP_SRC):
        if not os.path.isdir(p) and not os.path.isfile(p):
            raise SystemExit("找不到 wow-forever 的 %s——先确认那个仓库在不在原处" % p)
    os.makedirs(DST_DIR, exist_ok=True)

    have = set(os.path.splitext(f)[0] for f in os.listdir(DST_DIR) if f.endswith(".jpg"))
    src_keys = set(os.path.splitext(f)[0] for f in os.listdir(ICONS_SRC) if f.endswith(".jpg"))
    new = src_keys - have
    for k in new:
        shutil.copy2(os.path.join(ICONS_SRC, k + ".jpg"), os.path.join(DST_DIR, k + ".jpg"))
    have |= new

    m = json.load(open(MAP_SRC, encoding="utf-8")).get("map") or {}
    out = {}
    for key, icon in m.items():
        if icon in have:
            out[key] = icon + ".jpg"
    n_map = len(out)

    # 天赋表是图标最全的一路：talents/*.json 每条都有 nameCn + iconKey + 职业中文名
    n_tal = 0
    for f in sorted(os.listdir(TALENTS_SRC)):
        if not f.endswith(".json") or f.startswith("_"):
            continue
        d = json.load(open(os.path.join(TALENTS_SRC, f), encoding="utf-8"))
        cname = d.get("classNameCn") or d.get("classId")
        for tree in d.get("trees") or []:
            for node in tree.get("nodes") or []:
                name, icon = node.get("nameCn"), node.get("iconKey")
                if name and icon and icon in have:
                    out["%s|%s" % (cname, name)] = icon + ".jpg"
                    n_tal += 1

    gaps = {}
    if os.path.isfile(GAPS_SRC):
        g = json.load(open(GAPS_SRC, encoding="utf-8"))
        gaps = {"count": (g.get("meta") or {}).get("count"), "note": (g.get("meta") or {}).get("note")}

    doc = {"meta": {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "wow-forever/wow-forever-web（图标字节来自暴雪官方 CDN，那边 fetch-icons.py 限了域名）",
        "icon_files": len(have), "mapped": len(out), "from_icons_json": n_map, "from_talents": n_tal,
        "upstream_gaps": gaps,
        "rule": "查不到就不画图，绝不拿相近的图凑数；键一律带职业前缀，裸名会串图",
    }}
    doc["map"] = dict(sorted(out.items()))
    with open(DST_MAP, "w", encoding="utf-8") as fp:
        json.dump(doc, fp, ensure_ascii=False, indent=1)
    print("同步完成：新拷 %d 张，本地共 %d 张；映射 %d 条（icons.json %d + 天赋 %d）→ %s"
          % (len(new), len(have), len(out), n_map, n_tal, DST_MAP))
    print("提醒：output/ 已被 .gitignore 覆盖，图标与映射都不进公开仓库。")


if __name__ == "__main__":
    main()
