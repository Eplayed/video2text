"""分页和配图按同一份要点核对。"""
import re

from .term_glossary import WOW_CLASSES, _wow_terms


def _classes(text):
    text = str(text or "")
    found = set()
    for name, key in WOW_CLASSES.items():
        if name in text or re.search(r"\b" + re.escape(key) + r"\b", text, re.I):
            found.add(key)
    if "恶魔猎手" in text and "猎人" not in text:
        found.discard("hunter")
    return found


def _card_text(card):
    return " ".join(str(card.get(key) or "") for key in ("t", "d"))


def _page_class(page):
    title = _classes(page.get("title"))
    if title:
        return title
    return _classes(" ".join(_card_text(card) for card in page.get("cards") or []))


def page_issues(plan, points):
    """单职业页的卡片、原文主体和互动不得指向另一职业。"""
    issues = []
    for index, page in enumerate(plan.get("pages") or [plan], 1):
        owner = _classes(page.get("title"))
        if len(owner) != 1:
            continue
        ask = page.get("ask") or {}
        extras = {"副标题": page.get("subtitle"), "互动": ask.get("q"),
                  "提醒": _card_text(page.get("warn") or {})}
        for label, text in extras.items():
            entities = _classes(text)
            if entities and not entities & owner:
                issues.append({"field": "第%d页·%s" % (index, label), "problem": "内容属于另一职业"})
        for n, card in enumerate(page.get("cards") or [], 1):
            cited = set().union(*(_classes(points[pid].get("topic_group") or points[pid].get("subject"))
                                  for pid in card.get("point_ids") or [] if pid in points))
            entities = _classes(_card_text(card))
            if (cited and not cited & owner) or (entities and not entities & owner):
                issues.append({"field": "第%d页·卡片%d" % (index, n), "problem": "文案或原文要点属于另一职业"})
    return issues


def repair_optional_blocks(plan):
    notes = []
    for index, page in enumerate(plan.get("pages") or [plan], 1):
        owner = _classes(page.get("title"))
        ask = page.get("ask") or {}
        ask_classes = _classes(ask.get("q"))
        if len(owner) == 1 and ask_classes and not ask_classes & owner:
            page.pop("ask", None)
            notes.append("第%d页移除了属于另一职业的互动题" % index)
        kicker = str(page.get("kicker") or "")
        kicker_classes = _classes(kicker)
        if len(kicker) > 16 or (owner and kicker_classes and not kicker_classes & owner):
            page["kicker"] = "职业调整" if kicker_classes else ""
    return notes


def frame_related(page, caption):
    """只用来自画面的描述确认相关性，口播时点不代表画面里一定出现了同一内容。"""
    caption = str(caption or "").strip()
    if not caption:
        return False
    owner = _page_class(page)
    image_owner = _classes(caption)
    if image_owner and owner and not image_owner.issubset(owner):
        return False
    text = " ".join(str(page.get(key) or "") for key in ("title", "subtitle"))
    text += " " + " ".join(_card_text(card) for card in page.get("cards") or [])
    labels = [card.get("t") or "" for card in page.get("cards") or []]
    visual_terms = {term for term in _wow_terms(caption) if len(term) >= 3 and term in caption}
    if visual_terms:
        return any(term in text for term in visual_terms)
    if len(owner) == 1 and image_owner == owner:
        # 只说明职业角色的画面可作同职业配图。技能界面需对上具体名称。
        return bool(re.search(r"角色|职业原画", caption)) and not re.search(r"技能|天赋|面板", caption)
    labels.extend(re.findall(r"[/][a-z][a-z_]+|[\u4e00-\u9fff]{3,}", caption))
    return any(len(label) >= 3 and label in caption and label in text
               for label in labels if label not in ("游戏内", "游戏画面", "操作界面", "游戏界面", "技能面板"))


def attach_frames(plan, items):
    """内容验收后按页选帧，同一帧只上一次。"""
    used = set()
    notes = []
    for index, page in enumerate(plan.get("pages") or [plan], 1):
        page.pop("photos", None)
        for frame in items:
            name = frame.get("file")
            if not name or name in used or not frame_related(page, frame.get("subject")):
                continue
            page["photos"] = [{"path": name, "caption": frame["subject"], "judged": True,
                               "frame": "F%d" % frame["i"], "ts": frame.get("ts") or ""}]
            used.add(name)
            break
        if items and not page.get("photos"):
            notes.append("第%d页无已确认相关画面，保留文字页" % index)
    return notes
