#!/usr/bin/env python3
"""I2 母稿：尾段、精确来源、续跑、源版本与语义/覆盖分闸。"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import content_store, guide_mother  # noqa: E402


def check(label, value):
    if not value:
        raise AssertionError(label)
    print("PASS " + label)


def fixture(db, transcript):
    conn = content_store.connect(db)
    try:
        conn.execute("INSERT INTO videos (id,source_sheet,source_row,title,transcript,updated_at) "
                     "VALUES (1,'test',1,'测试标题',?, '2026-11-04')", (transcript,))
        conn.execute("INSERT INTO ai_summaries "
                     "(id,video_id,summary_type,title,content,structured_data,status,source_video_ids,created_at,updated_at) "
                     "VALUES (1,1,'guide_article','旧整理稿','这里可能有错误',?, 'draft','[1]','2026-11-04','2026-11-04')",
                     (json.dumps({"other_key": {"keep": True}}),))
        conn.commit()
    finally:
        conn.close()


def main():
    transcript = "第一步保留主体与条件。" * 200 + "最后一步：两个小号依次冲专业；第一个小号后续也能取得点数。"
    with tempfile.TemporaryDirectory(prefix="v2t-mother-i2-") as tmp:
        db = str(Path(tmp) / "case.db")
        fixture(db, transcript)
        source, revision = guide_mother.source_snapshot(db, 1)
        segs = guide_mother.split_sources(source, revision)
        check("分段从 0 到原文尾连续覆盖", segs[0]["start"] == 0
              and segs[-1]["end"] == len(transcript)
              and all(a["end"] == b["start"] for a, b in zip(segs, segs[1:])))

        calls = []
        failed = {segs[1]["segment_id"]}

        def extractor(seg, _title, _cfg):
            calls.append(seg["segment_id"])
            if seg["segment_id"] in failed:
                failed.remove(seg["segment_id"])
                raise RuntimeError("模拟模型超时")
            quote = seg["text"][-min(30, len(seg["text"])):].strip()
            return {"points": [{"quote": quote, "display_text": quote,
                                "priority": "must", "subject": "小号",
                                "conditions": [], "modality": "fact"}]}

        try:
            guide_mother.ensure_mother(db, 1, {"api_key": "fake"}, extractor=extractor)
        except guide_mother.MotherError as exc:
            check("失败段可定位且保留已完成段", "第 2/" in str(exc)
                  and guide_mother.load_mother(db, 1)["segments"][0]["status"] == "done")
        else:
            raise AssertionError("失败段未阻断")
        first_calls = list(calls)
        mother = guide_mother.ensure_mother(db, 1, {"api_key": "fake"}, extractor=extractor)
        check("续跑不重复收费处理成功段", calls.count(segs[0]["segment_id"]) == 1
              and len(calls) == len(segs) + 1)
        check("原文尾段进入母稿且引文可逐字读回", mother["status"] == "complete"
              and any("第一个小号后续也能取得点数" in p["quote"] for p in mother["points"])
              and all(source[0]["text"][p["source_ref"]["start"]:p["source_ref"]["end"]]
                      == p["quote"] for p in mother["points"]))
        conn = sqlite3.connect(db)
        structured = json.loads(conn.execute("SELECT structured_data FROM ai_summaries WHERE id=1").fetchone()[0])
        check("structured_data 其他键未被覆盖", structured["other_key"]["keep"])
        pid = mother["points"][0]["point_id"]
        plan = {"title": "两个小号", "cards": [{"t": "第一步", "d": "条件保留", "point_ids": [pid]}],
                "mother_revision": mother["mother_revision"]}
        bad = guide_mother.audit_output(mother, plan, {}, {"api_key": "fake"},
                                        checker=lambda *_: {"issues": [{"text": "两个小号",
                                                                         "problem": "主体数量变形"}]})
        check("覆盖与语义独立：ID 命中仍可拦变义", bad["coverage"]["status"] == "fail"
              and bad["semantic"]["status"] == "fail" and not bad["ready"])
        plan["cards"] = [{"t": "要点", "d": "完整", "point_ids": [p["point_id"]]}
                         for p in mother["points"]]
        ok = guide_mother.audit_output(mother, plan, {}, {"api_key": "fake"},
                                       checker=lambda *_: {"issues": []})
        check("必保点齐且语义复核完成才就绪", ok["ready"])
        publish = {"copy_text": "保留条件", "tt_titles": ["两个小号拿点数"]}
        fields = {"page_1.title": {"point_ids": [pid], "navigation": False},
                  "publish.copy_text.1": {"point_ids": [pid], "navigation": False},
                  "publish.tt_titles_1": {"point_ids": [pid], "navigation": False}}
        mapped = guide_mother.audit_output(
            mother, plan, publish, {"api_key": "fake"},
            checker=lambda *_: {"issues": [], "field_sources": fields})
        check("标题和配文逐项有来源映射才放行", mapped["ready"]
              and mapped["semantic"]["field_sources"]["publish.tt_titles_1"]["point_ids"] == [pid])
        absent = guide_mother.audit_output(
            mother, plan, publish, {"api_key": "fake"},
            checker=lambda *_: {"issues": [], "field_sources": {"page_1.title": fields["page_1.title"]}})
        check("标题候选或配文漏映射单独阻断", not absent["ready"]
              and any(x.get("field") == "publish.tt_titles_1" for x in absent["coverage"]["issues"]))
        check("改标题后审核输入版本变化", guide_mother.audit_output(
            mother, plan, dict(publish, tt_titles=["三个小号拿点数"]), {"api_key": "fake"},
            checker=lambda *_: {"issues": [], "field_sources": fields})["coverage"]["input_revision"]
            != mapped["coverage"]["input_revision"])
        sample_source = ("两个小号升专业后拿到11点。后续有两条路线，省钱路线再建第三个小号，"
                         "三个小号补最后5点。第一个小号先当材料仓库，随后也拿到点数。")
        sample = {"title": "三个小号凑齐11点", "cards": [
            {"t": "小号定位", "d": "第一个小号不产出点数", "point_ids": [p["point_id"]]}
            for p in mother["points"]]}
        caught = guide_mother.audit_output(mother, sample, {}, {"api_key": "fake"},
                                           checker=lambda *_: {"issues": []},
                                           source_text=sample_source)
        check("114 号两类变义固定反例：跨阶段错接与无源否定", not caught["ready"]
              and any("同一阶段" in x["problem"] for x in caught["semantic"]["issues"])
              and any("否定结论" in x["problem"] for x in caught["semantic"]["issues"]))
        conn.execute("UPDATE videos SET transcript=? WHERE id=1", (transcript + "新条件。",))
        conn.commit()
        conn.close()
        check("原文更新使旧母稿修订失效", guide_mother.source_snapshot(db, 1)[1]
              != mother["source_revision"])
        try:
            guide_mother._save_mother(db, 1, mother)
        except guide_mother.MotherError:
            print("PASS 旧后台任务不能回写新原文")
        else:
            raise AssertionError("旧后台任务盖过新原文")
        check("新提取会换母稿版本", guide_mother.ensure_mother(
            db, 1, {"api_key": "fake"}, extractor=extractor)["mother_revision"]
            != mother["mother_revision"])
        conn = sqlite3.connect(db)
        conn.execute("UPDATE videos SET transcript='' WHERE id=1")
        conn.commit()
        conn.close()
        try:
            guide_mother.ensure_mother(db, 1, {"api_key": "fake"}, extractor=extractor)
        except guide_mother.MotherError as exc:
            check("缺原始转写不拿整理稿自证", "缺少原始转写" in str(exc))
        else:
            raise AssertionError("缺源被旧整理稿放行")
    with tempfile.TemporaryDirectory(prefix="v2t-mother-no-ai-") as tmp:
        db = str(Path(tmp) / "no_ai.db")
        fixture(db, "需要原意提取的素材。")
        try:
            guide_mother.ensure_mother(db, 1, {})
        except guide_mother.MotherError as exc:
            check("无 AI 时只停新图文、素材仍在库", "未配置 AI" in str(exc)
                  and content_store.get_summary(db, 1) is not None)
        else:
            raise AssertionError("无 AI 冒充母稿完成")
    segment = {"segment_id": "belt", "video_id": 384746, "start": 0,
               "text": "皮萨斯它掉落滑行腰索，装备后可使用，冷却30分钟。"}
    result = {"points": [{"quote": segment["text"], "priority": "must",
                          "conditions": ["皮萨斯掉落", "装备后", "冷却15分钟"]}]}
    point = guide_mother._points_from_result(segment, result, "rev", editorial=True)[0]
    check("有效原句不因模型条件缩写停稿，未经证实的条件不进入母稿",
          point["quote"] == segment["text"] and point["conditions"] == ["装备后"])
    from src.editorial_model import claim_issues
    check("核实的原文条件和数值仍约束图文",
          bool(claim_issues("冷却30分钟", [point], "card"))
          and bool(claim_issues("装备后冷却15分钟", [point], "card"))
          and not claim_issues("装备后冷却30分钟", [point], "card"))
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
