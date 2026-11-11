"""Reproduce a multi-page coverage failure and a truncated DeepSeek audit."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import guide_mother


def main():
    mother = {
        "status": "complete",
        "source_revision": "src",
        "mother_revision": "mother",
        "points": [{"point_id": "p1", "priority": "must", "quote": "两点致命偷袭。"}],
    }
    plan = {"pages": [
        {"title": "加点要点", "cards": [{"t": "天赋", "d": "两点致命偷袭。", "point_ids": []}]}
    ]}
    publish = {"copy_text": "\n".join("核对项目 %d。" % index for index in range(30))}
    model = {"editorial": True}
    with patch.object(guide_mother, "_client_json", side_effect=AssertionError("premature model call")):
        missing = guide_mother.audit_output(mother, plan, publish, model)
    assert missing["semantic"]["deferred"]
    assert missing["coverage"]["issues"][0]["quote"] == "两点致命偷袭。"

    plan["pages"][0]["cards"][0]["point_ids"] = ["p1"]
    calls = []

    def fake_call(_cfg, prompt, *, max_tokens):
        payload = json.JSONDecoder().raw_decode(prompt[prompt.index('{"source"'):])[0]
        items = payload["items"]
        assert 0 < len(items) <= 14 and max_tokens == 4096
        calls.append([item["text"] for item in items.values()])
        if len(calls) == 1:
            raise guide_mother.MotherError("模型输出达到长度上限，JSON 被截断")
        return {"checks": {i: {"ok": True, "refs": [1]} for i in items}}

    with patch.object(guide_mother, "_client_json", side_effect=fake_call):
        repaired = guide_mother.audit_output(mother, plan, publish, model)
    assert repaired["ready"] and len(calls) == 4 and calls[0] == calls[1]
    assert len(repaired["coverage"]["field_sources"]) == 31

    def partial(_cfg, prompt, *, max_tokens):
        return {"checks": {}}

    with patch.object(guide_mother, "_client_json", side_effect=partial):
        incomplete = guide_mother.audit_output(mother, plan, publish, model)
    assert not incomplete["ready"] and incomplete["semantic"]["status"] == "unknown"
    print("ALL_OK coverage before model call, batch JSON retry, incomplete response fails closed")


if __name__ == "__main__":
    main()
