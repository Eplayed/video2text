"""Evidence admission, card capacity, and immutable coverage/accuracy gates."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import editorial_model, guide_mother


def must(point_id, quote, conditions=()):
    return {"point_id": point_id, "priority": "must", "quote": quote, "conditions": conditions}


def main():
    exact = "本次维护后，仅限测试服普通难度地下城，小怪经验上调50%。"
    video = {"id": 23, "transcript": "先说今天的维护公告。现在回顾此次更新。" + exact}
    proof = {"ready": True, "topic": "小怪经验", "quote": exact, "video_id": "23"}
    assert editorial_model.review_topic("速刷满级", [video], {}, lambda _: proof)["title"] == "小怪经验"
    for forged in (dict(proof, quote=exact.replace("50%", "80%")), dict(proof, video_id=99)):
        try:
            editorial_model.review_topic("速刷满级", [video], {}, lambda _: forged)
        except ValueError as exc:
            assert "无法在主视频正文定位" in str(exc)
        else:
            raise AssertionError("fake topic evidence accepted")

    second = {"id": 42, "transcript": "第二条资料提到只在固定队伍条件下考虑副本任务。"
                                       "需要先核对当前版本，再安排经验分配和路线。"}
    collection = editorial_model.review_topic(
        "部落1–20练级攻略", [video, second], {}, lambda _: {
            "ready": True, "topic": "低等级副本练级条件", "refs": ["23:0", "42:0"]})
    raw = {v["id"]: v["transcript"] for v in (video, second)}
    assert collection["title"] == "低等级副本练级条件"
    assert all(collection["title"] not in t for t in raw.values())
    assert set(editorial_model.collection_spans(collection, raw)) == {23, 42}
    for refs in (["23:0"], ["23:0", "99:0"]):
        try:
            editorial_model.review_topic("部落1–20练级攻略", [video, second], {}, lambda _: {
                "ready": True, "topic": "低等级副本练级条件", "refs": refs})
        except ValueError as exc:
            assert "每条已选视频" in str(exc)
        else:
            raise AssertionError("multi-source evidence did not check every selected source")
    try:
        editorial_model.collection_spans(collection, {**raw, 42: "整段已替换"})
    except ValueError as exc:
        assert "原文片段已变化" in str(exc)
    else:
        raise AssertionError("changed source accepted")
    scoped = [{"video_id": 23, "text": video["transcript"] + "超出主题的后续内容",
               "topic_spans": [(0, len(video["transcript"]))], "topic": collection["title"]},
              {"video_id": 42, "text": second["transcript"],
               "topic_spans": [(0, len(second["transcript"]))], "topic": collection["title"]}]
    segments = guide_mother.split_sources(scoped, "source")
    assert [seg["text"] for seg in segments] == list(raw.values())
    assert "超出主题" not in guide_mother.audit_source_text(scoped)
    assert editorial_model.collection_point_outside("部落1-20级练级攻略", "30级开始去中立地图。")
    assert editorial_model.collection_point_outside("部落1-20级练级攻略", "联盟可以去泰达希尔。")
    assert not editorial_model.collection_point_outside("部落1-20级练级攻略", "10到20级走新手村旁边的地图。")
    quote = "以部落为例怒焰峡谷一共经验是2.64万经验。"
    old = {"video_id": 42, "start": 0, "text": quote, "segment_id": "old",
           "topic": "部落1-20级练级攻略", "published_at": "2025-01-01"}
    points = guide_mother._points_from_result(
        old, {"points": [{"quote": quote, "subject": "怒焰峡谷", "priority": "must"}]},
        "fixture-source", editorial=True)
    assert points[0]["review_reason"].startswith("早期素材")
    assert editorial_model.collection_point_note("部落1-20级练级攻略", "可以直接升到19级", "2025-01-01")
    assert editorial_model.collection_point_note("部落1-20级练级攻略", "部落和联盟的起始地图", "2025-01-01")

    primary = must("p1", exact, ["仅限测试服普通难度地下城"])
    p2 = must("p2", "两点致命偷袭。")
    p3 = must("p3", "三点无情。")
    mother = {"status":"complete", "mother_revision":"m", "source_revision":"s", "points":[primary,p2,p3]}
    plan = {"title": "地下城经验", "layout_key": "guide",
            "cards": [{"t":"适用范围","d":exact,"point_ids":["p1"]}]}
    limits = {"cardD": 40, "total": 200}
    completed, notes = editorial_model.complete_poster_coverage(plan, mother, limits, 6, 5)
    assert notes == ["从原文补齐 2 个必保点"]
    assert completed["pages"][1]["cards"] == [
        {"t":"原文要点","d":p["quote"],"point_ids":[p["point_id"]]} for p in (p2,p3)]
    assert "pages" not in plan
    assert editorial_model.complete_poster_coverage(plan, mother, limits, 1, 5)[0] is plan

    original = {"title":"天赋加点","cards":[{"t":"切割","d":"三点切割。","point_ids":["cut"]}]}
    unclear = dict(mother, points=[must("cut","点上5.213点的切割")])
    with patch.object(guide_mother, "_client_json", side_effect=AssertionError("called model for unclear source")):
        blocked = guide_mother.audit_output(unclear, original, {}, {"editorial": True},
                                           source_text="天赋加点：点上5.213点的切割")
    assert not blocked["ready"] and blocked["coverage"]["issues"][0]["kind"] == "source_review"
    # A model cannot bypass the source check by leaving the garbled number out of the mother draft.
    incomplete_mother = dict(mother, points=[p2])
    plan_with_clear_card = {"title":"加点","cards":[{"d":p2["quote"],"point_ids":["p2"]}]}
    held = guide_mother.audit_output(incomplete_mother, plan_with_clear_card, {}, {"editorial":True},
                                    source_text="天赋加点：点上5.213点的切割，两点致命偷袭。")
    assert any(item.get("kind") == "source_review" for item in held["coverage"]["issues"])
    assert not editorial_model.unclear_talent_rank_spans("天赋使技能额外造成2.5点伤害。")

    original = {"title":"地下城经验","cards":[{"t":"适用范围","d":exact,"point_ids":["p1"]}]}
    single = dict(mother, points=[primary])
    drift = dict(original, bignum={"v":"80%","label":"经验加成"}, ask={"q":"需要排队吗？","options":["两小时"]})
    assert guide_mother.audit_revision(single, original, {}, exact) != guide_mother.audit_revision(single, drift, {}, exact)

    def unchecked_auditor(_m, output, _c, _s):
        assert "80%" in output and "两小时" in output and "回复领取脚本" in output
        return {"issues": [], "field_sources": {
            field: {"point_ids":["p1"]} for field in guide_mother._field_texts(drift, pub)}}

    pub = {"keyword_reply":"回复领取脚本"}
    failed = guide_mother.audit_output(single, drift, pub, {"editorial":True},
                                      checker=unchecked_auditor, source_text=exact)
    assert not failed["ready"]
    assert any("80%" in item["problem"] for item in failed["semantic"]["issues"])
    assert any("2小时" in item["problem"] for item in failed["semantic"]["issues"])
    condition = dict(original, cards=[{"t":"经验","d":"小怪经验上调50%。","point_ids":["p1"]}])
    failed = guide_mother.audit_output(single, condition, {}, {"editorial":True},
                                      checker=lambda *_: {"issues":[]}, source_text=exact)
    assert not failed["ready"] and any("遗漏原文条件" in item["problem"] for item in failed["semantic"]["issues"])
    assert editorial_model.numeric_claims("两点、百分之五十、5.213点") == {"2点","50%","5.213点"}
    print("ALL_OK source admission, deterministic pages, unclear ASR, visible fields, numeric and scope checks")


if __name__ == "__main__":
    main()
