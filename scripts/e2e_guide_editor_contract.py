"""Test clause splitting, mandatory point capacity, and quest guide evidence."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import claim_check, editorial_model, guide_mother


def main():
    known = "回到主城查看邮箱，收到来信之后回到影牙城堡门口，交付任务后可以获得蓝色单手剑。"
    unresolved = "完全没有可以拆开的标点并且每个字都必须保留用于验证单条超长不会让其他清晰的必保点消失的模拟原文"
    points = [{"point_id": key, "quote": value, "priority": "must"}
              for key, value in (("a", "接取任务。"), ("b", known), ("c", unresolved), ("d", "在桌上取道具。"))]
    mother = {"points": points, "status": "complete", "source_revision": "src", "mother_revision": "mom"}
    plan = {"title": "任务流程", "cards": [{"d": "接取任务。", "point_ids": ["a"]}]}
    ready, notes = editorial_model.complete_poster_coverage(
        plan, mother, {"cardD": 20, "total": 200}, 6, 5)
    cards = ready["pages"][1]["cards"]
    assert "".join(card["d"] for card in cards if card["point_ids"] == ["b"]) == known
    assert any(card["point_ids"] == ["d"] for card in cards)
    assert all(card["point_ids"] != ["c"] for card in cards)
    assert any(unresolved in note for note in notes)

    raw = "在视频展示位置输入斜杠Cit，回到对面输入Cit。"
    with patch.object(guide_mother, "_client_json", side_effect=AssertionError("model called for unclear command")):
        result = guide_mother.audit_output(dict(mother, points=points[:1]), plan, {},
                                           {"editorial": True}, source_text=raw)
    assert not result["ready"]
    assert any(x.get("kind") == "source_review" for x in result["coverage"]["issues"])
    assert editorial_model.claim_issues("输入 /Cit", [{"quote": "输入 /sit"}], "card")
    assert not editorial_model.claim_issues("输入 /sit", [{"quote": "输入 /sit"}], "card")
    assert editorial_model.numeric_claims("23.9秒伤；2.46万经验") == {"23.9秒伤", "2.46万经验"}

    config = claim_check.load()
    assert config.get("claims")
    text = "血色修道院的人形怪变为中立不攻击。"
    checked = claim_check.fact_status_fields([("卡片", text)], asr_text="魔兽无限："+text, reg=config)
    assert all(x["state"] != "verified" for x in checked["fields"])
    assert not claim_check.check_fields([("路线","走进左手第一个房间。")], config)["hard"]
    assert claim_check.check_fields([("卖点","全职业第一。")], config)["hard"]
    xp = "按睡袋增益统计，亡灵约1.58万经验"
    got = claim_check.fact_status_fields([("卡片", "亡灵 "+xp)], asr_text="魔兽无限："+xp, reg=config)
    assert got["fields"][0]["state"] != "blocked", got
    assert got["fields"][0]["state"] != "verified", got

    print("ALL_OK literal clause coverage, unresolved point isolation, exact command and fact registry scope")


if __name__ == "__main__":
    main()
