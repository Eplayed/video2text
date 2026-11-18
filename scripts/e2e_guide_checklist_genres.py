#!/usr/bin/env python3
"""攻略体裁、同主体证据、榜单口径和缺帧降级。"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import ai_poster, checklist_frames as frames, guide_checklist as checklist, guide_mother
from src.graphics import icons


def point(key, name, quote, priority="must"):
    return {"point_id": key, "subject": name, "quote": quote, "display_text": quote,
            "conditions": [], "priority": priority}


def mother(points):
    return {"points": points, "status": "complete", "source_revision": "source", "mother_revision": "mother"}


def ready(source, kind, title):
    sheet = dict(checklist.from_mother(source, kind, title), source_revision="source",
                 mother_revision="mother", revision="checklist")
    recommended = checklist.recommendations(sheet, source)
    for row in sheet["rows"]:
        row["included"] = row["id"] in recommended["row_ids"]
    return sheet, recommended["pages"]


def rejects(operation, phrase):
    try:
        operation()
    except checklist.ChecklistError as exc:
        assert phrase in str(exc), str(exc)
    else:
        raise AssertionError("应拦截：" + phrase)


def test_ranking():
    source = mother([
        point("a", "B级梯队", "先来看B级。", "support"),
        point("b", "暗幕", "第3名 暗幕单目标场景仍有优势。"),
        point("c", "暗影牧师", "暗影牧师短战位居第1名。"),
        point("d", "A级梯队", "盘完B级接下来是A级。", "support"),
        point("e", "恶魔学识术士", "第2名 恶魔学识术士。"),
        point("f", "恶魔术", "恶魔术在2分钟单体场景排在第3名。"),
        point("g", "猎人", "最后是S级梯队第1名 猎人。"),
    ])
    assert checklist.suggest_kind("今日要点", source) == "ranking"
    sheet, pages = ready(source, "ranking", "职业模拟排名")
    assert len(sheet["rows"]) == 3 and pages == 4
    assert [(row["name"], row["point_ids"]) for row in sheet["rows"]] == [
        ("猎人", ["g"]), ("恶魔学识术士", ["e", "f"]), ("暗影牧师", ["b", "c"]),
    ]
    plan, publish, scope = checklist.compiled(sheet, source)
    assert [page["title"] for page in plan["pages"]] == ["S级", "A级", "B级"]
    assert plan["pages"][2]["cards"][0]["point_ids"] == ["b"]
    assert plan["checklist_tier_refs"]["B级"] == "d"
    assert dict((p["point_id"], p["priority"]) for p in scope["points"])["d"] == "support"
    assert "2分钟" not in publish["copy_text"]
    altered = deepcopy(sheet)
    altered["rows"][2]["cards"][0]["d"] = "第1名"
    rejects(lambda: checklist.compiled(altered, source), "名次")
    altered["rows"][2]["cards"][0]["d"] = "第3名"
    altered["rows"][2]["group"] = "S级"
    rejects(lambda: checklist.compiled(altered, source), "梯队")
    return plan, sheet


def test_same_entity():
    source = mother([
        point("a", "腰带", "哀嚎洞穴，皮萨斯掉落腰带。"),
        point("b", "腰带", "使用可解除定身，冷却30分钟。"),
    ])
    sheet, _ = ready(source, "equipment", "装备清单")
    plan, _, scope = checklist.compiled(sheet, source)
    assert len(sheet["rows"]) == 1 and len(scope["points"]) == 2
    assert sheet["rows"][0]["group"] == "哀嚎洞穴"
    card = plan["pages"][0]["cards"][0]
    assert card["point_ids"] == ["a", "b"]
    assert "皮萨斯" in card["d"] and "30分钟" in card["d"]
    sheet["rows"][0]["cards"][0]["d"] = card["d"].replace("30分钟", "99分钟")
    rejects(lambda: checklist.compiled(sheet, source), "99分钟")


def test_editorial_field_refs():
    source = mother([point("a", "梯队", "盘完B级接下来是A级。")])
    fields = {
        "page_1.title": {"text": "B级", "point_ids": ["a"]},
        "publish.xhs_copy.1": {"text": "B级", "point_ids": ["a"]},
    }
    response = {"checks": {"0": {"ok": True, "refs": [1]}}}
    with patch.object(guide_mother, "_client_json", return_value=response) as mock:
        proof = guide_mother._editorial_compare(source, "原文", [], fields, {})
    assert mock.call_count == 1 and not proof["issues"]
    assert set(proof["field_sources"]) == set(fields)
    assert proof["field_sources"]["publish.xhs_copy.1"]["point_ids"] == ["a"]


def test_visuals(plan, sheet):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "icons").mkdir()
        icon = root / "icons" / "class.png"
        Image.new("RGB", (64, 64), "#ff8844").save(icon)
        (root / "wow_cards.json").write_text(json.dumps({"cards": {"猎人": {"icon": "class.png"}}}), encoding="utf-8")
        scene = root / "scene.png"
        Image.new("RGB", (320, 240), "#1188dd").save(scene)
        frame = {"id": "test/1", "digest": sha256(scene.read_bytes()).hexdigest(),
                 "file": str(scene), "subject": "远景", "time": "00:42", "source": "视频",
                 "source_url": "", "url": "/test.png", "author": ""}
        candidates = {frame["id"]: frame}
        keys = [frames.page_key(page) for page in plan["pages"]]
        with patch.object(icons, "MAP_FILE", root / "icons_cn.json"):
            desc = frames.describe(plan, sheet, {})
            assert [page["visual"] for page in desc["pages"]] == ["asset", "text", "text"]
            sample = deepcopy(plan)
            frames.attach(sample, sheet, {})
            previews = ai_poster.preview_pages(checklist.with_cover(sample), ratio="3:4")
            reports = [item[2] for item in previews]
            assert [item["icons"] if "icons" in item else 0 for item in reports] == [0, 1, 0, 0]
            assert all(item["ok"] for item in reports)
            for _, png, _ in previews:
                with Image.open(BytesIO(png)) as rendered:
                    rendered.verify()
            text = frames.edit(plan, sheet, candidates, {"page": keys[0], "visual": "text"})
            assert frames.describe(plan, text, candidates)["pages"][0]["visual"] == "text"
            requests = [dict(page=key, id=frame["id"], digest=frame["digest"],
                             anchor=page["cards"][0]["t"], usage="scene",
                             crop=[index * 1000, 0, 10000, 10000])
                        for index, (key, page) in enumerate(zip(keys, plan["pages"]))]
            chosen = frames.edit(plan, sheet, candidates, requests[0])
            chosen = frames.edit(plan, chosen, candidates, requests[1])
            rejects(lambda: frames.edit(plan, chosen, candidates, requests[2]), "最多用于两页")
            reused = deepcopy(plan)
            frames.attach(reused, chosen, candidates)
            frames.freeze(reused, root / "frozen")
            assert all(page["photos"][0]["reusable_scene"] for page in reused["pages"][:2])
            assert not ai_poster.lint_plan(reused, ai_poster.limits_for("typeset"))
            first, second = [Image.open(page["photos"][0]["path"]).size for page in reused["pages"][:2]]
            assert first == (320, 240) and second == (288, 240)


if __name__ == "__main__":
    titles = {"地下城装备盘点": "equipment", "今日在线热修蓝贴": "class",
              "接取任务的路线": "steps", "全专精T0/T1模拟榜": "ranking", "开服避坑指南": "digest"}
    assert {title: checklist.suggest_kind(title) for title in titles} == titles
    test_same_entity()
    test_visuals(*test_ranking())
    test_editorial_field_refs()
    print("PASS 5 种体裁、实体聚合、名次与数值拦截、标题回指、四级配图与有限复用")
