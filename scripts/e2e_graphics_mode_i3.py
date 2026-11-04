#!/usr/bin/env python3
"""隔离验证新图文开关与历史包只读边界。"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web import app as wa  # noqa: E402


def main():
    old_env = os.environ.get("V2T_ORIGINAL_GRAPHICS")
    old_dirs = (wa.ai_poster.OUTPUT_DIR, wa.wechat_graphics.OUTPUT_DIR,
                wa.toutiao_channel.OUTPUT_DIR, wa.toutiao_graphics.OUTPUT_DIR)
    with tempfile.TemporaryDirectory(prefix="v2t-mode-i3-") as tmp:
        root = Path(tmp)
        manifests = []
        for kind in ("poster", "wechat", "toutiao"):
            folder = root / kind / "1"
            folder.mkdir(parents=True)
            mf = folder / "manifest.json"
            mf.write_text(json.dumps({"id": 1, "summary_id": 1, "title": "历史包",
                                      "images": [], "created_at": "2026-10-01"},
                                     ensure_ascii=False), encoding="utf-8")
            manifests.append(mf)
        current = root / "wechat" / "2"
        current.mkdir()
        (current / "manifest.json").write_text(json.dumps({
            "id": 2, "summary_id": 2, "title": "新包", "images": [],
            "source_revision": "source", "mother_revision": "mother",
            "fidelity": {"ready": True},
            "created_at": "2026-11-04"}, ensure_ascii=False), encoding="utf-8")
        manifests.append(current / "manifest.json")
        poster_new = root / "poster" / "2"
        poster_new.mkdir()
        (poster_new / "manifest.json").write_text(json.dumps({
            "id": 2, "summary_id": 2, "title": "新海报", "created_at": "2026-11-04",
            "plan": {"source_revision": "source", "mother_revision": "mother",
                     "fidelity": {"ready": True}}}, ensure_ascii=False), encoding="utf-8")
        manifests.append(poster_new / "manifest.json")
        hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in manifests}
        wa.ai_poster.OUTPUT_DIR = root / "poster"
        wa.wechat_graphics.OUTPUT_DIR = root / "wechat"
        wa.toutiao_channel.OUTPUT_DIR = root / "toutiao"
        wa.toutiao_graphics.OUTPUT_DIR = root / "toutiao"
        client = wa.app.test_client()
        try:
            os.environ["V2T_ORIGINAL_GRAPHICS"] = "off"
            status = client.get("/api/graphics/original-mode").get_json()
            assert status["enabled"] is False and status["mode"] == "original_fidelity_v1"
            for route in ("/api/poster/plan", "/api/poster/plan/page", "/api/poster/generate",
                          "/api/wechat/generate", "/api/toutiao/generate"):
                resp = client.post(route, json={"summary_id": 0})
                assert resp.status_code == 409, (route, resp.get_json())
                assert "不会转入旧裁剪流程" in resp.get_json()["error"]
            print("PASS 关闭新图文后五个生成入口在模型调用前拒绝，未回退旧流程")

            for route in ("/api/poster/1", "/api/wechat/1", "/api/toutiao/1"):
                resp = client.get(route)
                assert resp.status_code == 200, (route, resp.get_json())
                data = resp.get_json()
                assert data["generation_mode"] == "legacy"
                assert data["validation_label"] == "旧版，未做新版校验"
            new = client.get("/api/wechat/2").get_json()
            assert new["generation_mode"] == "original_fidelity_v1"
            assert "validation_label" not in new
            assert client.get("/api/poster/2").get_json()["generation_mode"] == "original_fidelity_v1"
            wx_list = client.get("/api/wechat/list").get_json()
            assert wx_list["total"] == 2
            assert {p["generation_mode"] for p in wx_list["items"]} == {
                "legacy", "original_fidelity_v1"}
            poster_list = client.get("/api/poster/list").get_json()
            assert {p["generation_mode"] for p in poster_list["items"]} == {
                "legacy", "original_fidelity_v1"}
            assert hashes == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in manifests}
            print("PASS 关闭时旧包和新包仍可读；旧版标注只在响应中，manifest 哈希不变")

            article = client.post("/api/wechat/article", json={"summary_id": 1})
            assert article.status_code == 400 and "先写" in article.get_json()["error"]
            os.environ["V2T_ORIGINAL_GRAPHICS"] = "on"
            assert client.get("/api/graphics/original-mode").get_json()["enabled"] is True
            assert client.post("/api/poster/plan", json={"summary_id": 0}).status_code == 404
            print("PASS 公众号文章路径不受开关影响；重新打开后新入口恢复前置检查")
        finally:
            if old_env is None:
                os.environ.pop("V2T_ORIGINAL_GRAPHICS", None)
            else:
                os.environ["V2T_ORIGINAL_GRAPHICS"] = old_env
            (wa.ai_poster.OUTPUT_DIR, wa.wechat_graphics.OUTPUT_DIR,
             wa.toutiao_channel.OUTPUT_DIR, wa.toutiao_graphics.OUTPUT_DIR) = old_dirs
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
