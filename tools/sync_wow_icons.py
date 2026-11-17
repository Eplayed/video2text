# -*- coding: utf-8 -*-
"""tools/sync_wow_icons.py — 把 wow-forever 的图标、原画与「实名名单 + 结构化卡片真源」同步进图文产线

产出三份，都在 output/_assets/ 下（已被 .gitignore 覆盖，不进公开仓库）：
  icons_cn.json   名字 → 图标文件，渲染时查它补图
  wow_terms.json  名字 → {类别, 分级, 归属, 图标键, 旧名}，机检与提示词查它统一写法
  wow_cards.json  名字 → {kind, icon, quality, meta, stats, src, banner, map_img, portrait}，3:4 阅读版与海报排版查它补高清组件与准确属性
"""
import json
import os
import re
import shutil
import sys
from datetime import datetime

SRC_REPO = os.path.expanduser("~/Documents/project/wow-forever/wow-forever-web")


def _upstream(*parts):
    for root in ("app", "src"):
        p = os.path.join(SRC_REPO, root, *parts)
        if os.path.exists(p):
            return p
    return os.path.join(SRC_REPO, "app", *parts)


ICONS_SRC = _upstream("img", "icons")
ART_SRC = _upstream("img", "art")
DATA_SRC = _upstream("data")
TALENTS_SRC = os.path.join(DATA_SRC, "talents")
MAP_SRC = os.path.join(DATA_SRC, "icons.json")
GAPS_SRC = os.path.join(DATA_SRC, "icon-gaps.json")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST_DIR = os.path.join(ROOT, "output", "_assets", "icons")
DST_ART = os.path.join(ROOT, "output", "_assets", "art")
DST_MAP = os.path.join(ROOT, "output", "_assets", "icons_cn.json")
DST_TERMS = os.path.join(ROOT, "output", "_assets", "wow_terms.json")
DST_CARDS = os.path.join(ROOT, "output", "_assets", "wow_cards.json")


def _first_url(doc):
    for src in ((doc.get("meta") or {}).get("sources") or []):
        u = str(src.get("url") or "").strip()
        if u:
            return u
    return ""


def _load(rel):
    p = os.path.join(DATA_SRC, rel)
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else None


def sync_art_files():
    """把职业原画、副本头图、副本底图、区域图与首领头像同步到 output/_assets/art/。"""
    if not os.path.isdir(ART_SRC):
        return 0
    copied = 0
    for sub in ("classes", "classbanners", "dungeons", "dungeonmaps", "bosses", "bosses-m2", "maps", "scenes"):
        sdir = os.path.join(ART_SRC, sub)
        if not os.path.isdir(sdir):
            continue
        ddir = os.path.join(DST_ART, sub)
        os.makedirs(ddir, exist_ok=True)
        for fname in os.listdir(sdir):
            if not fname.lower().endswith((".webp", ".png", ".jpg")):
                continue
            sp = os.path.join(sdir, fname)
            dp = os.path.join(ddir, fname)
            if not os.path.exists(dp) or os.path.getsize(sp) != os.path.getsize(dp):
                shutil.copy2(sp, dp)
                copied += 1
    return copied


def collect_terms_and_cards(have):
    terms, imap, aliases = {}, {}, {}
    cards = {}

    def add(name, kind, src, level, icon, extra=None):
        if not name:
            return
        icon_file = None
        if icon:
            base = icon[:-4] if icon.endswith(".jpg") else icon
            if base in have:
                icon_file = base + ".jpg"
        rec = terms.get(name)
        if not (rec and rec.get("level", "L9") <= level):
            terms[name] = {"kind": kind, "src": src, "level": level, "icon": icon_file}
            if extra:
                terms[name].update(extra)
        elif icon_file and not rec.get("icon"):
            rec["icon"] = icon_file
        if icon_file:
            imap["%s|%s" % (src, name)] = icon_file
            imap.setdefault("通用|%s" % name, icon_file)
        return icon_file

    # 1. 专业与配方
    prof = _load("professions.json") or {}
    for p in prof.get("professions") or []:
        pname = p.get("nameCn") or p.get("slug") or "?"
        for r in p.get("recipes") or []:
            ic = add(r.get("nameCn"), "配方", pname, r.get("level") or "L0", r.get("iconKey"),
                     {"item_id": r.get("itemId"), "quality": r.get("quality"), "is_new": r.get("isNew")})
            if r.get("nameCn"):
                cards.setdefault(r["nameCn"], {
                    "kind": "recipe", "icon": ic, "quality": r.get("quality") or 2,
                    "meta": "%s · 技能 %s" % (pname, r.get("skill") or ""),
                    "src": r.get("source") or "",
                })
            for m in r.get("mats") or []:
                add(m.get("nameCn"), "材料", pname, r.get("level") or "L0", m.get("iconKey"))

    # 2. 九职业天赋
    for f in sorted(os.listdir(TALENTS_SRC)):
        if not f.endswith(".json") or f.startswith("_"):
            continue
        d = json.load(open(os.path.join(TALENTS_SRC, f), encoding="utf-8"))
        cname = d.get("classNameCn") or d.get("classId")
        for tree in d.get("trees") or []:
            for n in tree.get("nodes") or []:
                ic = add(n.get("nameCn"), "天赋", cname, n.get("level") or "L2", n.get("iconKey"),
                         {"max_ranks": n.get("maxRanks"), "tree": tree.get("nameCn"),
                          "name_verified": n.get("nameVerified")})
                if n.get("nameCn"):
                    cards.setdefault(n["nameCn"], {
                        "kind": "talent", "icon": ic, "quality": 3,
                        "meta": "%s · %s天赋" % (cname, tree.get("nameCn") or ""),
                    })

    # 3. 技能书 spells.json
    sp_doc = _load("spells.json") or {}
    cls_cn_map = {
        "warrior": "战士", "paladin": "圣骑士", "hunter": "猎人", "rogue": "潜行者",
        "priest": "牧师", "shaman": "萨满祭司", "mage": "法师", "warlock": "术士", "druid": "德鲁伊",
    }
    for sp in sp_doc.get("spells") or []:
        sname = sp.get("nameCn") or sp.get("name")
        cname = cls_cn_map.get(sp.get("classId"), sp.get("classId") or "通用")
        ic = add(sname, "技能", cname, sp.get("level") or "L0", sp.get("iconKey") or sp.get("icon"))
        if sname:
            cards.setdefault(sname, {
                "kind": "spell", "icon": ic, "quality": 3,
                "meta": "%s · %s" % (cname, sp.get("tree") or "技能"),
            })

    # 4. 术语与技能四态
    gl = _load("glossary.json") or {}
    for g in gl.get("items") or []:
        kind = {"talent": "天赋", "racial": "种族特长", "item": "物品"}.get(g.get("kind"), "技能")
        add(g.get("cn"), kind, g.get("classId") or "通用", g.get("level") or "L0", None)

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
                aliases[old] = a["name"]
        add(a.get("name"), "技能", a.get("classId") or "通用", a.get("level") or "L0", None,
            {"state": a.get("state"), "official_tag": a.get("officialTag")})

    # 5. 种族与职业（含职业原画）
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
        add_enum("天裔", "种族", race_url)
    cb = _load("classes.json") or {}
    cls_url = _first_url(cb)
    for c in cb.get("classes") or []:
        cname = c.get("cn")
        cid = c.get("id") or ""
        add_enum(cname, "职业", cls_url)
        ic = add(cname, "职业", "官方枚举", "L0", "classicon_" + cid if cid else None)
        banner = "classbanners/%s.webp" % cid if cid else ""
        if cname:
            cards[cname] = {
                "kind": "class", "icon": ic, "quality": 4,
                "meta": "%s · %s" % (cname, c.get("role") or "九大职业"),
                "banner": banner if os.path.isfile(os.path.join(DST_ART, banner)) else "",
            }
    # 常见专精+职业组合别名（如「增强萨满」「猎人宠物」）
    spec_aliases = {
        "增强萨满": ("萨满祭司", "spell_nature_lightningshield.jpg", "萨满祭司 · 增强专精"),
        "元素萨满": ("萨满祭司", "spell_nature_lightning.jpg", "萨满祭司 · 元素专精"),
        "恢复萨满": ("萨满祭司", "spell_nature_magicimmunity.jpg", "萨满祭司 · 恢复专精"),
        "猎人宠物": ("猎人", "ability_hunter_beasttaming.jpg", "猎人 · 野兽控制"),
        "武器战士": ("战士", "ability_rogue_eviscerate.jpg", "战士 · 武器专精"),
        "狂暴战士": ("战士", "ability_warrior_innerrage.jpg", "战士 · 狂怒专精"),
        "防战": ("战士", "ability_warrior_defensivestance.jpg", "战士 · 防护专精"),
        "野德": ("德鲁伊", "ability_racial_bearform.jpg", "德鲁伊 · 野性战斗"),
        "暗牧": ("牧师", "spell_shadow_shadowwordpain.jpg", "牧师 · 暗影专精"),
        "惩戒骑": ("圣骑士", "spell_holy_auraoflight.jpg", "圣骑士 · 惩戒专精"),
    }
    for k, (base_cls, sp_ic, sp_meta) in spec_aliases.items():
        base_card = cards.get(base_cls) or {}
        cards[k] = {
            "kind": "class",
            "icon": sp_ic if sp_ic[:-4] in have else base_card.get("icon"),
            "quality": 4,
            "meta": sp_meta,
            "banner": base_card.get("banner") or "",
        }

    # 5b. 世界区域地图 (world.json)
    wd_doc = _load("world.json") or {}
    for z in wd_doc.get("zones") or []:
        zname = z.get("nameCn") or ""
        mid = str(z.get("mapId") or "")
        if not zname:
            continue
        lr = z.get("levelRange") or []
        lr_s = f"Lv.{lr[0]}-{lr[1]}" if len(lr) == 2 else ""
        fac = z.get("faction") or ""
        mrel = f"maps/{mid}.webp" if mid and os.path.isfile(os.path.join(DST_ART, f"maps/{mid}.webp")) else ""
        cards[zname] = {
            "kind": "zone",
            "icon": "inv_misc_map_01.jpg" if "inv_misc_map_01" in have else None,
            "quality": 3,
            "meta": " · ".join(x for x in [lr_s, fac] if x),
            "map_img": mrel,
            "banner": mrel,
        }

    # 6. 副本、首领、副本任务与坐标（dungeons.json + dungeon-quests.json + dungeon-geo.json）
    dg_doc = _load("dungeons.json") or {}
    dq_doc = _load("dungeon-quests.json") or {}
    raw_dq = dq_doc.get("dungeons") or {}
    dq_by_id = raw_dq if isinstance(raw_dq, dict) else {d.get("id"): d for d in raw_dq if isinstance(d, dict) and d.get("id")}
    for d in (dg_doc.get("newDungeons") or []) + (dg_doc.get("classicDungeons") or []) + (dg_doc.get("raids") or []):
        did = d.get("id") or ""
        dname = d.get("nameCn") or ""
        if not dname:
            continue
        lvl_str = d.get("levelRange") or ""
        zone_str = d.get("zoneCn") or d.get("locationCn") or ""
        add(dname, "副本", zone_str or "地下城", d.get("level") or "L0", d.get("iconKey"))
        # 副本底图与头图
        map_rel = ""
        for cand in ("dungeonmaps/%s.webp" % did, "dungeons/%s.webp" % did):
            if os.path.isfile(os.path.join(DST_ART, cand)):
                map_rel = cand
                break
        banner_rel = "dungeons/%s.webp" % did if os.path.isfile(os.path.join(DST_ART, "dungeons/%s.webp" % did)) else map_rel
        q_info = dq_by_id.get(did) or {}
        q_rows = q_info.get("quests") or []
        cards[dname] = {
            "kind": "dungeon",
            "icon": None,
            "quality": 4,
            "meta": " · ".join(x for x in [f"Lv.{lvl_str}" if lvl_str else "", zone_str, f"{len(q_rows)}条关联任务" if q_rows else ""] if x),
            "banner": banner_rel,
            "map_img": map_rel,
        }
        for b in d.get("bosses") or []:
            bname = b.get("nameCn") or b.get("name")
            if not bname:
                continue
            add(bname, "首领", dname, b.get("level") or "L0", None)
            port = str(b.get("portrait") or "").replace("img/art/", "")
            if port and not os.path.isfile(os.path.join(DST_ART, port)):
                port = ""
            cards.setdefault(bname, {
                "kind": "boss", "icon": None, "quality": 4,
                "meta": "%s · 首领" % dname,
                "portrait": port,
            })
        for q in q_rows:
            qname = q.get("nameCn") or ""
            if not qname:
                continue
            npc = q.get("pickupNpc") or ""
            pzone = q.get("pickupZone") or ""
            add(qname, "副本任务", dname, q.get("level") or "L1", None)
            cards.setdefault(qname, {
                "kind": "quest", "icon": "inv_misc_note_01.jpg" if "inv_misc_note_01" in have else None,
                "quality": 2,
                "meta": " · ".join(x for x in [dname, f"Lv.{q.get('questLevel')}" if q.get("questLevel") else "", q.get("side") or ""] if x),
                "src": "接取：%s" % (" · ".join(x for x in [pzone, npc] if x)) if (npc or pzone) else "",
            })

    # 7. 物品名录 + 物品详情 (items.json + items-detail.json) + 套装表 (_flb_item_sets.json)
    it_doc = _load("items.json") or {}
    det_doc = (_load("items-detail.json") or {}).get("items") or {}
    item_by_id = {}
    for it in it_doc.get("items") or []:
        iid = it.get("id")
        iname = it.get("name") or it.get("nameCn")
        if not iname or not iid:
            continue
        item_by_id[int(iid)] = it
        det = det_doc.get(str(iid)) or {}
        st_lines = [str(x).strip() for x in (det.get("st") or []) if str(x).strip()]
        # 过滤掉纯说明长句，只留核心属性行（护甲/属性/速度/绿字）
        base_stats = []
        green_stats = []
        for ln in st_lines:
            if ln in ("装备唯一", "拾取后绑定", "装备后绑定", "唯一") or len(ln) <= 2:
                continue
            if ln.startswith(("装备：", "使用：", "击中时可能：", "（", "(", "套装：")) or "套装" in ln:
                green_stats.append(ln)
            elif any(k in ln for k in ("护甲", "耐力", "力量", "敏捷", "智力", "精神", "伤害", "速度", "抗性")):
                base_stats.append(ln)
        src_name = det.get("srcName") or ""
        slot_type = it.get("type") or it.get("slot") or ""
        rlvl = it.get("rlvl") or 0
        ilvl = it.get("ilvl") or 0
        meta_parts = [slot_type]
        if rlvl:
            meta_parts.append("需%d级" % rlvl)
        elif ilvl:
            meta_parts.append("装等%d" % ilvl)
        if src_name:
            meta_parts.append(src_name)
        ic_file = add(iname, "物品", src_name.split("·")[0].strip() if "·" in src_name else "物品",
                      "L0" if it.get("status") == "same" else "L1",
                      it.get("iconKey") or it.get("icon"),
                      {"item_id": iid, "quality": it.get("quality")})
        # 优先存有详情属性或有出处的同名物品
        existing = cards.get(iname)
        score = len(base_stats) * 2 + len(green_stats) * 3 + (2 if src_name else 0)
        if not existing or score > existing.get("_score", -1):
            cards[iname] = {
                "kind": "item",
                "id": iid,
                "icon": ic_file,
                "quality": it.get("quality") or 2,
                "meta": " · ".join(x for x in meta_parts if x),
                "stats": " ｜ ".join(base_stats[:4]),
                "effect": " ".join(green_stats[:2]),
                "src": src_name,
                "_score": score,
            }

    # 8. 套装表 (_flb_item_sets.json) + 常见套装简称（如「尖牙套装」「戒指套装特效」）
    sets_doc = _load("_flb_item_sets.json") or {}
    for s in sets_doc.get("sets") or []:
        sname = s.get("nameCn") or ""
        if not sname:
            continue
        piece_ids = s.get("items") or []
        first_it = item_by_id.get(piece_ids[0]) if piece_ids else None
        ic_file = add(sname, "套装", "装备套装", "L1", (first_it or {}).get("iconKey"))
        piece_names = [item_by_id[pid]["name"] for pid in piece_ids if pid in item_by_id and item_by_id[pid].get("name")]
        cards[sname] = {
            "kind": "set",
            "icon": ic_file or ("inv_chest_chain_03.jpg" if "inv_chest_chain_03" in have else None),
            "quality": 3,
            "meta": "Lv.%s · %d件套" % (s.get("level") or "?", len(piece_ids)),
            "stats": "包含：" + " / ".join(piece_names[:5]) if piece_names else "",
        }
    # 补充常见口播套装别名
    if "毒蛇的拥抱" in cards:
        cards["尖牙套装"] = dict(cards["毒蛇的拥抱"], meta="哀嚎洞穴 · 5件套 (毒蛇的拥抱)")
        terms["尖牙套装"] = dict(terms.get("毒蛇的拥抱") or {"kind": "套装", "src": "哀嚎洞穴", "level": "L0", "icon": cards["毒蛇的拥抱"].get("icon")})
    if "同谋佳偶" in cards:
        cards["戒指套装特效"] = {
            "kind": "set",
            "icon": "inv_jewelry_ring_54.jpg" if "inv_jewelry_ring_54" in have else cards["同谋佳偶"].get("icon"),
            "quality": 3,
            "meta": "同谋佳偶 (2件套) · 大副的戒指 + 良配之戒",
            "stats": "良配之戒：+10 耐力 ｜ +18 力量 ｜ +12 精准",
        }

    # 清理内部评分字段
    for v in cards.values():
        v.pop("_score", None)

    for key, icon in (_load("icons.json") or {}).get("map", {}).items():
        if "|" in key and icon + ".jpg" in have:
            imap.setdefault(key, icon + ".jpg")
    return terms, imap, aliases, cards


def _trait_lines(items):
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
    terms_only = "--terms-only" in sys.argv
    if not os.path.isdir(DATA_SRC):
        raise SystemExit("找不到 wow-forever 的资料目录 %s" % DATA_SRC)
    os.makedirs(DST_DIR, exist_ok=True)
    os.makedirs(DST_ART, exist_ok=True)

    have = set(os.path.splitext(f)[0] for f in os.listdir(DST_DIR) if f.endswith(".jpg"))
    new = set()
    if not terms_only and os.path.isdir(ICONS_SRC):
        src_keys = set(os.path.splitext(f)[0] for f in os.listdir(ICONS_SRC) if f.endswith(".jpg"))
        new = src_keys - have
        for k in new:
            shutil.copy2(os.path.join(ICONS_SRC, k + ".jpg"), os.path.join(DST_DIR, k + ".jpg"))
        have |= new

    art_copied = 0 if terms_only else sync_art_files()
    terms, imap, aliases, cards = collect_terms_and_cards(have)

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
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DST_TERMS, "w", encoding="utf-8") as fp:
        json.dump({"meta": {"generated_at": now_str,
                            "source": os.path.relpath(DATA_SRC, SRC_REPO),
                            "upstream_root": SRC_REPO,
                            "terms": len(terms), "levels": levels, "kinds": kinds,
                            "official_enum": sum(1 for t in terms.values() if t.get("official_enum")),
                            "blobs": len(blobs),
                            "aliases": len(aliases), "icon_files": len(have),
                            "upstream_gaps": gaps, "rule": rule},
                   "blobs": blobs,
                   "terms": dict(sorted(terms.items())),
                   "aliases": dict(sorted(aliases.items()))},
                  fp, ensure_ascii=False, indent=1)
    with open(DST_MAP, "w", encoding="utf-8") as fp:
        json.dump({"meta": {"generated_at": now_str,
                            "icon_files": len(have), "mapped": len(imap), "rule": rule},
                   "map": dict(sorted(imap.items()))},
                  fp, ensure_ascii=False, indent=1)
    with open(DST_CARDS, "w", encoding="utf-8") as fp:
        json.dump({"meta": {"generated_at": now_str, "cards": len(cards)},
                   "cards": cards},
                  fp, ensure_ascii=False, indent=1)

    print("同步完成：新拷图标 %d 张（共 %d 张），新拷原画/地图 %d 张" % (len(new), len(have), art_copied))
    print("实名名单 %d 条；带图标映射 %d 条；结构化卡片真源 %d 条" % (len(terms), len(imap), len(cards)))
    print("类别分布:", kinds)


if __name__ == "__main__":
    main()
