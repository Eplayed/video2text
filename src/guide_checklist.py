"""可核对的攻略清单与确定性分页。引用的原句和修订号由服务端管理。"""
import json
import re
from copy import deepcopy

from . import ai_poster, content_store, guide_mother
from .editorial_model import _literal_card_fragments, claim_issues
from .term_glossary import WOW_CLASSES


STORE_KEY = "poster_fact_sheet_v1"
KINDS = {"equipment": "装备清单", "class": "职业改动", "steps": "任务步骤",
         "ranking": "梯度榜单", "digest": "汇总盘点"}
COMPACT_VERSION = 3
RANK = re.compile(r"^\s*(?:最后是[^。！？]{0,45}?)?第\s*([0-9]+|[一二三四五六七八九十]+)\s*名")
RANK_MENTION = re.compile(r"第\s*([0-9]+|[一二三四五六七八九十]+)\s*名")
TIER = re.compile(r"([SABCD]\s*(?:级|G|[T梯][队隊])|T[0-5](?!\d))", re.I)
RANKING = re.compile(r"排名|排行|强弱|梯队|梯度|模拟榜|专精榜|[SAB]\s*级.{0,8}对比|T[0-5](?:\s*[/、和]|档|梯队|级|强度|对比)", re.I)
DIGEST = re.compile(r"避坑|合集|汇总|盘点|赚钱|赚金|商业玩法|开服清单|总结")
ALIASES = {
    "暗幕": "暗影牧师", "暗牧": "暗影牧师",
    "恶魔术": "恶魔学识术士", "恶魔学识术士": "恶魔学识术士",
    "惩戒骑": "惩戒圣骑士", "狂暴战": "狂暴战士", "武器战": "武器战士",
    "刺杀贼": "刺杀盗贼", "敏锐贼": "敏锐盗贼", "战斗贼": "战斗盗贼",
    "兽王猎": "野兽控制猎人", "鸟德": "平衡德鲁伊", "猫德": "野性德鲁伊",
    "奥法": "奥术法师", "冰法": "冰霜法师", "火法": "火焰法师",
}
DIGEST_GROUPS = (
    (r"避坑|风险|注意|失败|别做", "风险与避坑"),
    (r"赚钱|赚金|金币|拍卖|材料|成本", "材料与收益"),
    (r"改动|修正|热修|调整", "变更要点"),
    (r"开服|前置|准备|解锁", "开服与准备"),
)
FOOTER = "内容整理自公开分享"


class ChecklistError(ValueError):
    pass


def _text(value, label, limit):
    value = value.strip() if isinstance(value, str) else ""
    if not value or len(value) > limit:
        raise ChecklistError("%s请填写 1 至 %d 字" % (label, limit))
    return value


def suggest_kind(text, mother=None):
    points = (mother or {}).get("points") or []
    sample = " ".join((point.get("subject") or "") + " " + (point.get("quote") or "")
                      for point in points)
    if RANKING.search(text) or sum(bool(RANK.match(point.get("quote") or "")) for point in points) >= 2:
        return "ranking"
    if DIGEST.search(text):
        # 「装备盘点」仍用专用版，显式的收益或跨题综述走盘点。
        if not re.search(r"装备|掉落|配装|戒指", text) or re.search(r"避坑|汇总|合集|赚钱|赚金", text):
            return "digest"
    if re.search(r"热修|修正|职业调整|蓝贴", text):
        return "class"
    if re.search(r"装备|掉落|配装|戒指", text):
        return "equipment"
    if RANKING.search(sample) and TIER.search(sample):
        return "ranking"
    if DIGEST.search(sample) and any(point.get("topic_group") for point in points):
        return "digest"
    return "steps"


def _subject(point):
    name = (point.get("subject") or "").strip()
    return ALIASES.get(name, name)


def _tiers(text):
    for match in TIER.findall(text or ""):
        value = re.sub(r"\s+", "", match).upper()
        yield value if value.startswith("T") else value[0] + "级"


def _tier(text):
    return next(reversed(list(_tiers(text))), "")


def _group(point, kind):
    group = point.get("topic_group") or ""
    if kind == "ranking":
        return _tier(group) or _tier(point.get("tier")) or "总览"
    if kind == "digest":
        return group or next((label for pattern, label in DIGEST_GROUPS
                              if re.search(pattern, _subject(point) + point["quote"])), "要点")
    if kind == "class":
        if group:
            return next((name for name, key in WOW_CLASSES.items() if key == group.lower()), group)
        classes = [name for name in WOW_CLASSES if name in _subject(point)]
        if len(classes) == 1:
            return classes[0]
    if kind == "equipment":
        match = re.match(r"(?:在)?([一-鿿]{1,8}?(?:洞穴|矿井|监狱|神殿|要塞|之城|瑞根))(?:[，、：的]|\s)",
                         point["quote"])
        if match:
            return match[1]
    return group or KINDS[kind]


def _card_parts(points, limit):
    """贪心拼接逐字短句，保留放不下的原句供编辑核对。"""
    cards = []
    for point in points:
        text = point["display_text"].strip()
        parts = _literal_card_fragments(text, limit)
        if not parts or any(claim_issues(part, [point], "card") for part in parts):
            parts = [text]
        for part in parts:
            if any(part == existing or part in existing for existing in cards):
                continue
            joined = cards[-1] + "；" + part if cards else ""
            if cards and len(joined) <= limit and not claim_issues(joined, points, "card"):
                cards[-1] = joined
            else:
                cards.append(part)
    return cards


def _rank_entities(points):
    """段落标题决定梯队，场景排名只保留在同专精的原文证据里。"""
    leads = {}
    tier = "总览"
    for point in points:
        quote = point["quote"].strip()
        name = _subject(point)
        rank = RANK.match(quote)
        source_tier = _tier(point.get("topic_group")) or _tier(point.get("tier"))
        if source_tier:
            tier = source_tier
        if rank:
            header = _tier(quote[:rank.start(1)]) if not quote.startswith("第") else ""
            tier = header or tier
            number = _rank_number(rank.group(1))
            label = str(number) if number != 10000 else rank.group(1)
            leads.setdefault(name, (tier, "第%s名" % label, point["point_id"]))
        elif len(quote) <= 65 and re.match(r"先来|盘完|接下来|最后|下面", quote):
            tier = _tier(quote) or tier
    return leads


def _tier_refs(points):
    refs = {}
    for point in points:
        quote = point["quote"].strip()
        if not re.match(r"先来|盘完|接下来|最后|下面", quote):
            continue
        for tier in _tiers(quote):
            # A literal heading takes precedence over a mistranscribed tier.
            if tier in quote or tier not in refs:
                refs[tier] = point["point_id"]
    return refs


def _rank_number(label):
    units = "一二三四五六七八九"
    if label.isdecimal():
        return int(label)
    if "十" in label:
        tens, _, ones = label.partition("十")
        if (not tens or tens in units and len(tens) == 1) and (not ones or ones in units and len(ones) == 1):
            return (units.index(tens) + 1 if tens else 1) * 10 + (units.index(ones) + 1 if ones else 0)
    return units.index(label) + 1 if len(label) == 1 and label in units else 10000


def from_mother(mother, kind, title):
    if kind not in KINDS:
        raise ChecklistError("请选择有效的攻略模板")
    points = [point for point in mother["points"] if point["priority"] != "optional"]
    limit = ai_poster.limits_for("typeset")["cardD"]
    leads = _rank_entities(points) if kind == "ranking" else {}
    items = {}
    previous = None
    for point in points:
        name = _subject(point)
        if kind == "ranking" and leads and name not in leads:
            continue
        if kind == "digest" and point["priority"] != "must" and re.fullmatch(
                r"(先来|最后|接下来|下面)[^。！？]{0,45}", point["quote"].strip()):
            continue
        section = (leads[name][0] if name in leads else _group(point, kind))[:16] or KINDS[kind]
        entity = name if name and not re.fullmatch(r"要点\d*|作者|观点|总结|说明", name) else point["point_id"]
        identity = (entity, (point.get("source_ref") or {}).get("video_id"))
        if kind in ("equipment", "steps") and section == KINDS[kind] and previous and previous[0] == identity:
            section = previous[1]
        previous = (identity, section)
        # 待核片段保留在同一主体的证据区，但非榜单不合并跨核验状态的可见正文。
        held = bool(point.get("review_reason")) if name not in leads else False
        items.setdefault((section, entity, held), []).append(point)
    rows = []
    if leads:
        items = dict(sorted(items.items(),
                            key=lambda entry: _rank_number(leads[_subject(entry[1][0])][1][1:-1])))
    for (section, _entity, _held), evidence in items.items():
        first = evidence[0]
        name = _subject(first)
        name = name if name and len(name) <= 8 else "要点%d" % (len(rows) + 1)
        parts = [leads[name][1]] if name in leads else _card_parts(evidence, limit)
        # 清单表单最多五张卡。超过预算的原句全文在证据区，存为待拆正文，保持所有引用。
        if len(parts) > ai_poster.CARD_MAX:
            parts = ["；".join(parts)]
        rows.append({"id": first["point_id"], "group": section, "name": name,
                     "cards": [{"t": name if i == 0 else "补充%d" % i, "d": part}
                               for i, part in enumerate(parts)],
                     "point_ids": [point["point_id"] for point in evidence], "included": False, "note": ""})
    if not rows:
        raise ChecklistError("这份母稿没有可核对的攻略条目")
    if leads and not RANKING.search(title or ""):
        title = "无限服职业模拟排名"
    return {"kind": kind, "title": str(title or KINDS[kind])[:30], "rows": rows, "compact_version": COMPACT_VERSION}


def view(conn, summary_id):
    row = conn.execute("SELECT structured_data FROM ai_summaries WHERE id=?", (summary_id,)).fetchone()
    if not row:
        raise ChecklistError("整理稿不存在")
    data = guide_mother._json_dict(row["structured_data"])
    return data.get(STORE_KEY) or {}, data


def load(db_path, summary_id):
    conn = content_store.open_readonly(db_path)
    try:
        return view(conn, summary_id)[0]
    finally:
        conn.close()


def edit(sheet, body):
    """浏览器只编辑文字与确认状态。引用、行身份和来源版本沿用原记录。"""
    submitted = body.get("rows")
    if not isinstance(submitted, list) or len(submitted) != len(sheet["rows"]):
        raise ChecklistError("清单行已变化，请刷新后再编辑")
    new = deepcopy(sheet)
    new["title"] = _text(body.get("title"), "攻略标题", 30)
    kind = body.get("kind")
    if kind not in KINDS:
        raise ChecklistError("请选择有效的攻略模板")
    new["kind"] = kind
    for item, row in zip(submitted, new["rows"]):
        if not isinstance(item, dict) or item.get("id") != row["id"]:
            raise ChecklistError("清单顺序已变化，请刷新后再编辑")
        row["group"] = _text(item.get("group"), "分组", 16)
        row["name"] = _text(item.get("name"), "要点名称", 8)
        row["note"] = str(item.get("note") or "").strip()[:200]
        row["included"] = item.get("included") is True
        cards = item.get("cards")
        if not isinstance(cards, list) or not 1 <= len(cards) <= ai_poster.CARD_MAX:
            raise ChecklistError("每条清单请填写 1 至 %d 张卡片" % ai_poster.CARD_MAX)
        if any(not isinstance(card, dict) for card in cards):
            raise ChecklistError("卡片格式有误")
        limit = ai_poster.limits_for("typeset")
        text_limit = limit["cardD"] if row["included"] else guide_mother.SEGMENT_CHARS
        row["cards"] = [{"t": _text(card.get("t"), "卡片标题", limit["cardT"]),
                         "d": _text(card.get("d"), "卡片正文", text_limit)} for card in cards]
        row["cards"][0]["t"] = row["name"]
    return new


def save(db_path, summary_id, sheet, source_revision, mother_revision, expected):
    conn = content_store.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        old, data = view(conn, summary_id)
        if (old.get("revision") or "") != expected:
            raise ChecklistError("清单已有新版本，请刷新后再保存")
        if guide_mother.current_revisions(conn, summary_id) != (source_revision, mother_revision):
            raise ChecklistError("原文或母稿已更新，请重新准备清单")
        stored = dict(sheet, source_revision=source_revision, mother_revision=mother_revision)
        stored.pop("revision", None)
        stored["revision"] = guide_mother._digest(stored)
        data[STORE_KEY] = stored
        conn.execute("UPDATE ai_summaries SET structured_data=? WHERE id=?",
                     (json.dumps(data, ensure_ascii=False), summary_id))
        conn.commit()
        return stored
    finally:
        conn.close()


def compiled(sheet, mother, style="guide_ink"):
    """仅排入已确认条目。每个分组稳定分页，单条内的多张卡一起移动。"""
    if sheet.get("mother_revision") != mother.get("mother_revision") or mother.get("status") != "complete":
        raise ChecklistError("原文或母稿已更新，请重新准备清单")
    points = {point["point_id"]: point for point in mother["points"]}
    selected = [row for row in sheet["rows"] if row["included"]]
    if not selected:
        raise ChecklistError("请至少确认一条事实后再出图")
    groups = {}
    included_points = set()
    limits = ai_poster.limits_for("typeset")
    ranked = _rank_entities(mother["points"]) if sheet["kind"] == "ranking" else {}
    for row in selected:
        refs = row["point_ids"]
        if not refs or any(ref not in points for ref in refs):
            raise ChecklistError("「%s」的引用已失效" % row["name"])
        evidence = [points[ref] for ref in refs]
        if any(point.get("review_reason") for point in evidence):
            raise ChecklistError("「%s」的原文待核，请先核对来源或从本次清单移除" % row["name"])
        allowed = None
        if sheet["kind"] == "ranking":
            names = {_subject(point) for point in evidence}
            name = _subject({"subject": row["name"]})
            allowed = ranked.get(name)
            if name not in names or (allowed and row["group"] != allowed[0]):
                raise ChecklistError("「%s」的主体或梯队与原句不一致" % row["name"])
            claimed = [_rank_number(rank) for card in row["cards"]
                       for rank in RANK_MENTION.findall(card["t"] + " " + card["d"])]
            verified = {_rank_number(rank) for point in evidence for rank in RANK_MENTION.findall(point["quote"])}
            if (any(rank not in verified for rank in claimed)
                    or (allowed and claimed != [_rank_number(allowed[1][1:-1])])):
                raise ChecklistError("「%s」的名次与对应原句不一致" % row["name"])
        cards = []
        for card in row["cards"]:
            findings = claim_issues(card["t"] + " " + card["d"], evidence, "card")
            if findings:
                raise ChecklistError("「%s」%s" % (card["t"], findings[0]["problem"]))
            # The composite row keeps all references. A verbatim rank has one primary source.
            visible_refs = [allowed[2]] if (allowed and allowed[2] in refs
                                           and card["t"] == row["name"] and card["d"] == allowed[1]) else refs
            cards.append(dict(card, point_ids=visible_refs))
            included_points.update(visible_refs)
        groups.setdefault(row["group"], []).append(cards)

    pages, paragraphs = [], []
    if sheet["kind"] == "ranking":
        groups = dict(sorted(groups.items(), key=lambda entry: (
            ("总览", "S级", "A级", "B级", "C级").index(entry[0])
            if entry[0] in ("总览", "S级", "A级", "B级", "C级") else 10, entry[0])))
    for group, entries in groups.items():
        blocks = []
        capacity = ai_poster.CARD_MAX
        if sheet["kind"] == "ranking" and all(len(cards) == 1 for cards in entries):
            batches = (len(entries) + capacity - 1) // capacity
            capacity = (len(entries) + batches - 1) // batches
        for cards in entries:
            page = {"title": group, "style_key": style, "layout_key": "guide", "footer": FOOTER}
            if sheet["kind"] == "ranking":
                page["kicker"] = "第三方模拟整理"
                page["footer"] = "第三方模拟整理 · 仅供参考"
            if not pages or pages[-1]["title"] != group:
                pages.append(dict(page, cards=[]))
            merged = dict(page, cards=pages[-1]["cards"] + cards)
            if pages[-1]["cards"] and (len(merged["cards"]) > capacity or ai_poster.lint_plan(merged, limits)):
                pages.append(dict(page, cards=cards))
            else:
                pages[-1] = merged
            bad = ai_poster.lint_plan(pages[-1], limits)
            if bad:
                raise ChecklistError("「%s」%s" % (cards[0]["t"], bad[0]))
            for card in cards:
                body = re.sub(r"[。！？；;\n]+", "，", card["d"].rstrip("。！？；;"))
                blocks.append("%s｜%s。" % (card["t"], body))
        paragraphs.append(group + "\n" + "\n".join(blocks))
    if len(pages) > ai_poster.PAGE_MAX:
        raise ChecklistError("本次确认内容需要 %d 页，请拆为两篇（单篇最多 %d 页）" %
                             (len(pages), ai_poster.PAGE_MAX))
    plan = dict(pages[0], pages=pages, title=sheet["title"], source_revision=sheet["source_revision"],
                mother_revision=sheet["mother_revision"], checklist_revision=sheet["revision"],
                checklist_kind=sheet["kind"])
    text = "\n\n".join(paragraphs)
    publish = {"copy_text": text, "xhs_copy": text, "digest": "、".join(groups),
               "tt_titles": [sheet["title"]], "xhs_titles": [sheet["title"]]}
    tier_refs = {group: ref for group, ref in _tier_refs(mother["points"]).items() if group in groups}
    if ranked and tier_refs:
        plan["checklist_tier_refs"] = tier_refs
    return plan, publish, scope(mother, included_points, set(tier_refs.values()) if ranked else set())


def recommendations(sheet, mother):
    """按相同分页和事实校验选择能成稿的条目，保持勾选由编辑确认。"""
    trial = dict(sheet, rows=[dict(row, included=False) for row in sheet["rows"]])
    ids, pages = [], 0
    for row in trial["rows"]:
        row["included"] = True
        try:
            plan, _, _ = compiled(trial, mother)
        except ChecklistError:
            row["included"] = False
            continue
        count = len(plan["pages"]) + 1
        if count > 6:
            row["included"] = False
            continue
        ids.append(row["id"])
        pages = count
        if sheet["kind"] != "ranking" and len(ids) >= 18:
            break
    return {"row_ids": ids, "pages": pages}


def with_cover(plan):
    """文案校验后添加封面。封面只复用已经校验的标题与已有配图。"""
    pages = plan["pages"]
    chosen = next(((i, page["photos"][0]) for i, page in enumerate(pages, 1)
                   if page.get("photos") and not page["photos"][0].get("scene_only")), None)
    cover = {"title": plan["title"], "style_key": plan["style_key"], "layout_key": "guide",
             "kicker": KINDS[plan["checklist_kind"]], "role": "封面", "checklist_cover": True,
             "cards": []}
    if plan.get("checklist_kind") == "ranking":
        cover.update(footer="第三方模拟整理 · 仅供参考", visual_mode="text")
    if chosen:
        cover["visual_mode"] = "frame"
        cover["cover_source_page"] = chosen[0]
        cover["photos"] = [dict(chosen[1])]
    return dict(plan, pages=[cover] + pages)


def scope(mother, included_points, supporting_points=()):
    """范围跟随经确认的引用。独立语义核验仍会收到完整来源。"""
    return dict(mother, points=[
        dict(point, priority="must" if point["point_id"] in included_points else "support")
        for point in mother["points"] if point["point_id"] in included_points or point["point_id"] in supporting_points
    ])
