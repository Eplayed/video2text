"""可核对的攻略清单与确定性分页。引用的原句和修订号由服务端管理。"""
import json
import re
from copy import deepcopy

from . import ai_poster, content_store, guide_mother
from .editorial_model import _literal_card_fragments, claim_issues
from .term_glossary import WOW_CLASSES


STORE_KEY = "poster_fact_sheet_v1"
KINDS = {"equipment": "装备清单", "class": "职业改动", "steps": "任务步骤"}
FOOTER = "内容整理自公开分享"


class ChecklistError(ValueError):
    pass


def _text(value, label, limit):
    value = value.strip() if isinstance(value, str) else ""
    if not value or len(value) > limit:
        raise ChecklistError("%s请填写 1 至 %d 字" % (label, limit))
    return value


def suggest_kind(text):
    if re.search(r"装备|掉落|配装|戒指", text):
        return "equipment"
    if re.search(r"热修|修正|职业调整|蓝贴", text):
        return "class"
    return "steps"


def _group(point, kind):
    group = point.get("topic_group")
    if kind == "class":
        if group:
            return next((name for name, key in WOW_CLASSES.items() if key == group.lower()), group)
        classes = [name for name in WOW_CLASSES if name in point["subject"]]
        if len(classes) == 1:
            return classes[0]
    if kind == "equipment":
        match = re.match(r"(?:在)?([一-鿿]{1,8}?(?:洞穴|矿井|监狱|神殿|要塞|之城|瑞根))(?:[，、：的]|\s)",
                         point["quote"])
        if match:
            return match[1]
    return group or KINDS[kind]


def from_mother(mother, kind, title):
    if kind not in KINDS:
        raise ChecklistError("请选择有效的攻略模板")
    group = KINDS[kind]
    rows = []
    for point in mother["points"]:
        if point["priority"] == "optional":
            continue
        label = point["subject"]
        name = label if label and len(label) <= 8 else "要点%d" % (len(rows) + 1)
        section = _group(point, kind)
        if len(section) > 16:
            section = group
        parts = _literal_card_fragments(point["display_text"], ai_poster.limits_for("typeset")["cardD"])
        if (not parts or len(parts) > ai_poster.CARD_MAX
                or any(claim_issues(part, [point], "card") for part in parts)):
            parts = [point["display_text"]]
        cards = [{"t": name if i == 0 else "补充%d" % i, "d": part}
                 for i, part in enumerate(parts)]
        rows.append({"id": point["point_id"], "group": section, "name": name, "cards": cards,
                     "point_ids": [point["point_id"]], "included": False, "note": ""})
    if not rows:
        raise ChecklistError("这份母稿没有可核对的攻略条目")
    return {"kind": kind, "title": str(title or group)[:30], "rows": rows}


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
    for row in selected:
        refs = row["point_ids"]
        if not refs or any(ref not in points for ref in refs):
            raise ChecklistError("「%s」的引用已失效" % row["name"])
        evidence = [points[ref] for ref in refs]
        if any(point.get("review_reason") for point in evidence):
            raise ChecklistError("「%s」的原文待核，请先核对来源或从本次清单移除" % row["name"])
        included_points.update(refs)
        cards = [dict(card, point_ids=refs) for card in row["cards"]]
        for card in cards:
            findings = claim_issues(card["t"] + " " + card["d"], evidence, "card")
            if findings:
                raise ChecklistError("「%s」%s" % (card["t"], findings[0]["problem"]))
        groups.setdefault(row["group"], []).append(cards)

    pages, paragraphs = [], []
    for group, entries in groups.items():
        blocks = []
        for cards in entries:
            page = {"title": group, "style_key": style, "layout_key": "guide", "footer": FOOTER}
            if not pages or pages[-1]["title"] != group:
                pages.append(dict(page, cards=[]))
            merged = dict(page, cards=pages[-1]["cards"] + cards)
            if pages[-1]["cards"] and ai_poster.lint_plan(merged, limits):
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
                mother_revision=sheet["mother_revision"], checklist_revision=sheet["revision"])
    text = "\n\n".join(paragraphs)
    publish = {"copy_text": text, "xhs_copy": text, "digest": "、".join(groups),
               "tt_titles": [sheet["title"]], "xhs_titles": [sheet["title"]]}
    return plan, publish, scope(mother, included_points)


def scope(mother, included_points):
    """范围跟随经确认的引用。独立语义核验仍会收到完整来源。"""
    return dict(mother, points=[dict(point, priority="must") for point in mother["points"]
                                if point["point_id"] in included_points])
