#!/usr/bin/env python3
"""换皮不重审原意；改字与换源必须重审。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import guide_mother  # noqa: E402
from web import app as wa  # noqa: E402


def main():
    old_out, old_audit = wa.OUTPUT_DIR, guide_mother.audit_output
    with tempfile.TemporaryDirectory(prefix="v2t-fidelity-cache-") as tmp:
        wa.OUTPUT_DIR = Path(tmp)
        calls = []
        mother = {"source_revision": "source-v1", "mother_revision": "mother-v1"}
        plan = {"title": "小号拿1点", "style_key": "game_epic",
                "cards": [{"t": "要点", "d": "小号拿1点", "point_ids": ["p1"]}]}
        pub = {"copy_text": "小号拿1点。"}

        def fake_audit(m, p, text, cfg, source_text=""):
            calls.append((p["title"], source_text))
            revision = guide_mother.audit_revision(m, p, text, source_text)
            return {"ready": True, "source_revision": m["source_revision"],
                    "mother_revision": m["mother_revision"],
                    "coverage": {"status": "pass", "input_revision": revision},
                    "semantic": {"status": "pass", "input_revision": revision}}

        guide_mother.audit_output = fake_audit
        try:
            first, reused = wa._poster_fidelity(1, mother, plan, pub, {}, "原文小号拿1点。")
            assert first["ready"] and not reused and len(calls) == 1
            print("PASS 初次审核落服务端快照")
            styled = dict(plan, style_key="guide_ink")
            _result, reused = wa._poster_fidelity(1, mother, styled, pub, {}, "原文小号拿1点。")
            assert reused and len(calls) == 1
            print("PASS 只换皮复用当前审核，零次模型调用")
            edited = dict(styled, title="小号拿2点")
            _result, reused = wa._poster_fidelity(1, mother, edited, pub, {}, "原文小号拿1点。")
            assert not reused and len(calls) == 2
            print("PASS 改标题重审")
            changed = dict(mother, source_revision="source-v2", mother_revision="mother-v2")
            _result, reused = wa._poster_fidelity(1, changed, edited, pub, {}, "原文小号拿2点。")
            assert not reused and len(calls) == 3
            print("PASS 换源重审")
            forged = wa._poster_load_plan(1)
            forged["fidelity"]["coverage"]["input_revision"] = "forged"
            wa._poster_save_plan(1, forged)
            _result, reused = wa._poster_fidelity(1, changed, edited, pub, {}, "原文小号拿2点。")
            assert not reused and len(calls) == 4
            print("PASS 不信任不匹配的旧审核")
        finally:
            wa.OUTPUT_DIR, guide_mother.audit_output = old_out, old_audit
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
