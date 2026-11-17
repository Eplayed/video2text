#!/usr/bin/env python3
"""攻略清单的固定分页、编辑范围、来源绑定和版本并发。"""
from copy import deepcopy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import checklist_frames, content_store, guide_checklist, guide_mother


def main():
    phrases = [
        "在哀嚎洞穴，皮萨斯掉落滑行腰索。使用可解除定身，冷却30分钟。",
        "在死亡矿井，曲奇掉落搅汤棒。装备后烹饪速度提高25%。",
        "在诺莫瑞根，水藤的来源待核，输入 abc。",
    ]
    with tempfile.TemporaryDirectory(prefix="v2t-checklist-") as tmp:
        db = Path(tmp) / "fixture.db"
        conn = content_store.connect(db)
        conn.execute("INSERT INTO videos(id,source_sheet,source_row,title,transcript,updated_at) "
                     "VALUES(1,'test',1,'魔兽无限装备清单',?,'2026-11-16')", ("\n".join(phrases),))
        conn.execute("INSERT INTO ai_summaries(id,video_id,summary_type,title,content,structured_data,"
                     "status,source_video_ids,created_at,updated_at) "
                     "VALUES(1,1,'game_guide','地下城装备清单','内容','{}','draft','[1]',"
                     "'2026-11-16','2026-11-16')")
        conn.commit()
        conn.close()
        points = [{"quote": quote, "display_text": quote, "priority": "must", "subject": name,
                   "conditions": conditions, "review_reason": reason}
                  for quote, name, conditions, reason in zip(
                      phrases, ["滑行腰索", "搅汤棒", "水藤"], [["使用"], ["装备后"], []],
                      ["", "", "来源待核"])]
        mother = guide_mother.ensure_mother(db, 1, {"editorial": True},
                                           extractor=lambda *_: {"points": points})
        draft = guide_checklist.from_mother(mother, "equipment", "地下城装备清单")
        assert [row["group"] for row in draft["rows"]] == ["哀嚎洞穴", "死亡矿井", "诺莫瑞根"]
        assert not any(row["included"] for row in draft["rows"])
        first = guide_checklist.save(db, 1, draft, mother["source_revision"],
                                     mother["mother_revision"], "")
        edit = deepcopy(first)
        edit["rows"][0]["included"] = True
        edit["rows"][1]["included"] = True
        edit["rows"][0]["point_ids"] = ["客户端伪造引用"]
        revised = guide_checklist.edit(first, edit)
        assert revised["rows"][0]["point_ids"] == first["rows"][0]["point_ids"]
        current = guide_checklist.save(db, 1, revised, mother["source_revision"],
                                       mother["mother_revision"], first["revision"])
        plan, publish, scope = guide_checklist.compiled(current, mother)
        assert (plan, publish, scope) == guide_checklist.compiled(current, mother)
        assert [p["title"] for p in plan["pages"]] == ["哀嚎洞穴", "死亡矿井"]
        assert len(scope["points"]) == 2 and "诺莫瑞根" not in publish["copy_text"]
        assert publish["copy_text"] == publish["xhs_copy"]
        from src import ai_poster
        covered = guide_checklist.with_cover(plan)
        assert len(covered["pages"]) == len(plan["pages"]) + 1
        assert covered["pages"][0]["title"] == plan["title"]
        assert covered["pages"][0]["role"] == "封面" and covered["pages"][1:] == plan["pages"]
        assert not ai_poster.lint_plan(covered, ai_poster.limits_for("typeset"))
        assert ai_poster.page_marker(ai_poster.plan_pages(covered), 2) == (1, 2)
        cover_long = dict(plan, title="魔兽世界无限地下城掉落装备用途和获取方法攻略清单")
        assert not ai_poster.lint_plan(guide_checklist.with_cover(cover_long), ai_poster.limits_for("typeset"))
        _, _, preview_check = ai_poster.preview_pages(guide_checklist.with_cover(cover_long))[0]
        assert preview_check["ok"] and preview_check["layout_label"] == "封面" and preview_check["photos"] == 0
        audit = guide_mother.audit_output(scope, plan, publish, {"editorial": True},
                                          checker=lambda *_: {"issues": []}, source_text="\n".join(phrases))
        assert not any(issue.get("kind") == "source_review" for issue in audit["coverage"]["issues"])
        assert guide_checklist.suggest_kind("11月在线热修蓝贴") == "class"
        assert guide_checklist.suggest_kind("接取任务攻略") == "steps"
        row = deepcopy(current)
        row["rows"][0]["cards"][0]["d"] = "皮萨斯掉落。使用可解除定身，冷却30分钟。"
        changed, changed_publish, _ = guide_checklist.compiled(row, mother)
        assert changed["pages"][0] != plan["pages"][0]
        assert changed["pages"][1] == plan["pages"][1] and changed_publish != publish
        row["rows"][0]["cards"][0]["d"] = "皮萨斯掉落。使用可解除定身，冷却15分钟。"
        try:
            guide_checklist.compiled(row, mother)
        except guide_checklist.ChecklistError as exc:
            assert "15分钟" in str(exc)
        else:
            raise AssertionError("篡改数字未拦截")
        row["rows"][0]["cards"][0]["d"] = current["rows"][0]["cards"][0]["d"]
        row["rows"][2]["included"] = True
        try:
            guide_checklist.compiled(row, mother)
        except guide_checklist.ChecklistError as exc:
            assert "原文待核" in str(exc)
        else:
            raise AssertionError("待核来源进入成品")
        try:
            guide_checklist.save(db, 1, revised, mother["source_revision"],
                                 mother["mother_revision"], first["revision"])
        except guide_checklist.ChecklistError as exc:
            assert "已有新版本" in str(exc)
        else:
            raise AssertionError("旧版本覆盖新清单")
        assert guide_checklist.load(db, 1) == current

        from hashlib import sha256
        local = Path(tmp) / "frame.jpg"
        local.write_bytes(b"first approved frame")
        frame = {"id": "video/6", "digest": sha256(local.read_bytes()).hexdigest(),
                 "time": "00:42", "subject": "滑行腰索", "source": "装备清单",
                 "source_url": "", "url": "/media/frame.jpg", "file": str(local), "author": ""}
        frames = {frame["id"]: frame}
        bound = checklist_frames.edit(plan, current, frames, {
            "page": checklist_frames.page_key(plan["pages"][0]), "id": frame["id"],
            "digest": frame["digest"], "anchor": "滑行腰索",
        })
        assert checklist_frames.describe(plan, bound, frames)["pages"][0]["selected"]["id"] == frame["id"]
        assert not checklist_frames.describe(plan, bound, frames)["pages"][1]["candidates"]
        preview = deepcopy(plan)
        proof = checklist_frames.attach(preview, bound, frames)
        assert proof[0]["review"] == "未审" and proof[0]["rights_basis"] == ""
        checklist_frames.freeze(preview, Path(tmp) / "preview")
        assert Path(preview["pages"][0]["photos"][0]["path"]).read_bytes() == local.read_bytes()
        local.write_bytes(b"updated frame")
        try:
            checklist_frames.freeze(preview, Path(tmp) / "unused")
        except checklist_frames.ChecklistError:
            raise AssertionError("已冻结的预览文件不应受源文件变化影响")
        attached = deepcopy(plan)
        checklist_frames.attach(attached, bound, frames)
        try:
            checklist_frames.freeze(attached, Path(tmp) / "final")
        except checklist_frames.ChecklistError:
            pass
        else:
            raise AssertionError("更新过的帧未经重新确认进入出图")
        assert checklist_frames.describe(changed, bound, frames)["outdated"] == 1

        from PIL import Image
        fixture = Image.new("RGB", (80, 60), "#cc1122")
        fixture.paste("#117733", (40, 0, 80, 60))
        fixture.save(local, format="PNG")
        frame["digest"] = sha256(local.read_bytes()).hexdigest()
        chosen = dict(page=checklist_frames.page_key(plan["pages"][0]), id=frame["id"],
                      digest=frame["digest"], anchor="滑行腰索", crop=[5000, 1000, 10000, 9000])
        cropped = checklist_frames.edit(plan, current, frames, chosen)
        cropped_plan = deepcopy(plan)
        checklist_frames.attach(cropped_plan, cropped, frames)
        checklist_frames.freeze(cropped_plan, Path(tmp) / "cropped")
        with Image.open(cropped_plan["pages"][0]["photos"][0]["path"]) as result:
            assert result.size == (40, 48) and result.getpixel((1, 1)) == (17, 119, 51)
        restored = checklist_frames.edit(plan, cropped, frames, dict(chosen, crop=[0, 0, 10000, 10000]))
        assert "crop" not in restored["frames"][chosen["page"]]
        for bad in ([0, 0, 10001, 10000], [9000, 0, 8000, 10000], [0, 0, 1, 1]):
            try:
                checklist_frames.edit(plan, cropped, frames, dict(chosen, crop=bad))
            except checklist_frames.ChecklistError:
                continue
            raise AssertionError("无效裁剪没有被拒绝")
        reading = dict(deepcopy(plan), checklist_reading=True)
        checklist_frames.attach(reading, cropped, frames)
        assert not reading["pages"][0]["photos"][0]["scene_only"]
        assert reading["pages"][0]["photos"][0]["caption"] == "滑行腰索"
        reading = guide_checklist.with_cover(reading)
        checklist_frames.freeze(reading, Path(tmp) / "reading")
        previews = ai_poster.preview_pages(reading, "3:4")
        assert all(check["ok"] for _, _, check in previews)
        assert previews[0][2]["layout_label"] == "阅读版封面"
        assert previews[1][2]["photos"] == 1 and previews[2][2]["photos"] == 0

        scene = checklist_frames.edit(plan, cropped, frames, dict(chosen, usage="scene"))
        reading = dict(deepcopy(plan), checklist_reading=True)
        checklist_frames.attach(reading, scene, frames)
        assert reading["pages"][0]["photos"][0]["scene_only"]
        assert not guide_checklist.with_cover(reading)["pages"][0].get("photos")
        updated = checklist_frames.edit(plan, scene, frames, dict(chosen, id=""))
        reading = dict(deepcopy(plan), checklist_reading=True)
        assert not checklist_frames.attach(reading, updated, frames)
        assert not reading["pages"][0].get("photos")

        extra = "从入口往左走。拿到钥匙后才能打开上层门。"
        conn = content_store.connect(db)
        _, data = guide_checklist.view(conn, 1)
        data["editorial_supplements"] = [{"id": 9999, "title": "补充资料夹具",
                                          "evidence_status": "official", "body": extra}]
        conn.execute("UPDATE ai_summaries SET structured_data=? WHERE id=1",
                     (json.dumps(data, ensure_ascii=False),))
        conn.commit()
        conn.close()

        def extract(segment, *_args):
            if segment.get("role"):
                return {"points": [{"quote": extra, "display_text": extra, "priority": "must",
                                   "subject": "上层门", "conditions": ["拿到钥匙后"]}]}
            return {"points": points}

        mother_with_extra = guide_mother.ensure_mother(db, 1, {"editorial": True}, extractor=extract)
        sheet_with_extra = guide_checklist.from_mother(mother_with_extra, "steps", "路线核对")
        ref = next(p for p in mother_with_extra["points"] if p["source_ref"].get("role"))
        for entry in sheet_with_extra["rows"]:
            entry["included"] = ref["point_id"] in entry["point_ids"]
        sheet_with_extra.update(mother_revision=mother_with_extra["mother_revision"],
                                source_revision=mother_with_extra["source_revision"], revision="fixture")
        extra_plan, extra_publish, extra_scope = guide_checklist.compiled(sheet_with_extra, mother_with_extra)
        extra_plan["checklist_reading"] = True
        assert "拿到钥匙后" in extra_publish["copy_text"]
        assert extra_scope["points"] == [ref]
        assert all(check["ok"] for _, _, check in
                   ai_poster.preview_pages(guide_checklist.with_cover(extra_plan), "3:4"))
    print("POSTER_CHECKLIST_OK")


if __name__ == "__main__":
    main()
