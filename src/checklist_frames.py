"""已审关键帧的逐页确认、快照校验和排版输入。"""
import hashlib
from pathlib import Path
from urllib.parse import quote

from . import frame_pool, guide_mother, poster_content
from .guide_checklist import ChecklistError


def page_key(page):
    return guide_mother._digest([page["title"], page["cards"]])[:24]


def candidates(materials, output_dir):
    output = Path(output_dir).resolve()
    out = {}
    for material in materials:
        aid = material["aweme_id"]
        if not aid:
            continue
        for frame in frame_pool.usable(frame_pool.load(aid)):
            if not frame.get("checked"):
                continue
            path = Path(frame["file"]).resolve()
            try:
                rel = path.relative_to(output / "assets" / "frames" / aid)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, ValueError):
                continue
            key = "%s/%s" % (aid, frame["i"])
            out[key] = {
                "id": key, "file": str(path), "digest": digest,
                "url": "/media/assets/frames/" + quote(aid + "/" + rel.as_posix()) + "?v=" + digest[:16],
                "time": frame.get("ts") or "", "subject": frame.get("subject") or "",
                "video_id": material["video_id"], "source": material["title"],
                "source_url": material.get("url") or "", "author": material.get("author") or "",
            }
    return out


def _allowed(frame, page, pages):
    caption = frame["subject"]
    owner = poster_content._page_class(page)
    image_owner = poster_content._classes(caption)
    if owner and image_owner and not image_owner.issubset(owner):
        return False
    if poster_content.frame_related(page, caption):
        return True
    # 模型看不清装备名时允许操作员明确核对条目。已识别到其他页主体时直接拦住。
    return not any(other is not page and poster_content.frame_related(other, caption)
                   for other in pages)


def describe(plan, sheet, frames):
    pages = plan["pages"]
    saved = sheet.get("frames") or {}
    valid_keys = {page_key(page) for page in pages}
    response = {"revision": sheet["revision"], "outdated": len(set(saved) - valid_keys), "pages": []}
    for order, page in enumerate(pages, 1):
        key = page_key(page)
        selected = saved.get(key)
        choices = []
        for frame in frames.values():
            if not _allowed(frame, page, pages):
                continue
            labels = [card["t"] for card in page["cards"] if card["t"] in frame["subject"]]
            choices.append({k: frame[k] for k in ("id", "url", "time", "subject", "source", "source_url", "digest")})
            choices[-1]["suggested"] = poster_content.frame_related(page, frame["subject"])
            choices[-1]["anchor"] = labels[0] if len(labels) == 1 else ""
        choices.sort(key=lambda frame: not frame["suggested"])
        available = frames.get(selected["id"]) if selected else None
        stale = bool(selected and (not available or available["digest"] != selected["digest"]
                                   or not any(c["id"] == selected["id"] for c in choices)))
        response["pages"].append({
            "key": key, "page": order, "title": page["title"],
            "labels": list(dict.fromkeys(card["t"] for card in page["cards"])),
            "selected": selected, "stale": stale, "candidates": choices,
        })
    return response


def edit(plan, sheet, frames, body):
    pages = plan["pages"]
    keyed = {page_key(page): page for page in pages}
    bindings = {key: value for key, value in (sheet.get("frames") or {}).items() if key in keyed}
    if body.get("discard_stale") is True:
        return dict(sheet, frames=bindings)
    key = body.get("page")
    page = keyed.get(key) if isinstance(key, str) else None
    if not page:
        raise ChecklistError("页面内容已更新，请重新打开逐页配图")
    frame_id = body.get("id")
    if frame_id in ("", None):
        bindings.pop(key, None)
        return dict(sheet, frames=bindings)
    frame = frames.get(frame_id) if isinstance(frame_id, str) else None
    if not frame or not _allowed(frame, page, pages):
        raise ChecklistError("这张帧尚未审核通过，或画面主体属于另一页")
    if body.get("digest") != frame["digest"]:
        raise ChecklistError("这张图刚更新，请重新打开预览后再确认")
    anchor = body.get("anchor")
    if not isinstance(anchor, str) or anchor not in {card["t"] for card in page["cards"]}:
        raise ChecklistError("请选择这张图展示的本页条目")
    if any(other.get("id") == frame_id for k, other in bindings.items() if k != key):
        raise ChecklistError("这张关键帧已用于另一页，请选择其他帧")
    basis = body.get("basis") or ""
    if not isinstance(basis, str) or len(basis.strip()) > 200:
        raise ChecklistError("画面使用依据最多 200 字")
    bindings[key] = {"id": frame_id, "anchor": anchor, "basis": basis.strip(), "digest": frame["digest"]}
    return dict(sheet, frames=bindings)


def attach(plan, sheet, frames, require_basis=False):
    saved = sheet.get("frames") or {}
    pages = plan["pages"]
    if set(saved) - {page_key(page) for page in pages}:
        raise ChecklistError("部分配图对应的页面已改动，请到逐页配图重新确认")
    materials = []
    for index, page in enumerate(pages, 1):
        selected = saved.get(page_key(page))
        if not selected:
            continue
        frame = frames.get(selected["id"])
        if (not frame or selected["digest"] != frame["digest"] or not _allowed(frame, page, pages)
                or selected["anchor"] not in {card["t"] for card in page["cards"]}):
            raise ChecklistError("第 %d 页的关键帧已变化，请重新确认" % index)
        if require_basis and not selected["basis"]:
            raise ChecklistError("第 %d 页缺少画面使用依据，请补填或设为文字页" % index)
        page["photos"] = [{
            "path": frame["file"], "frame": frame["id"], "caption": selected["anchor"],
            "ts": frame["time"], "judged": True, "sha256": frame["digest"],
        }]
        materials.append({"ref": "%s %s" % (frame["id"], frame["time"]), "url": frame["source_url"],
                          "author": frame["author"], "usage": "被引用的画面",
                          "rights_basis": selected["basis"], "review": "可发布"})
    return materials


def freeze(plan, folder):
    """逐页保存经过预览的同一文件，避免帧池刷新影响出图或历史成品。"""
    for index, page in enumerate(plan["pages"], 1):
        for photo in page.get("photos") or []:
            source = Path(photo["path"])
            try:
                data = source.read_bytes()
            except OSError:
                raise ChecklistError("第 %d 页的关键帧已丢失，请重新选择" % index)
            if hashlib.sha256(data).hexdigest() != photo["sha256"]:
                raise ChecklistError("第 %d 页的关键帧已更新，请重新预览" % index)
            target = folder / "_frames" / ("page-%d%s" % (index, source.suffix))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            photo["path"] = str(target)
