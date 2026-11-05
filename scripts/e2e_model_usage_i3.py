#!/usr/bin/env python3
"""隔离验证新图文逐请求账、费用未知、失败恢复和超时不重复出图。"""
import copy
import json
import sys
import tempfile
import time
from pathlib import Path

import httpx
import openai
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import ai_models, ai_poster, ai_quota, model_usage  # noqa: E402
from web import app as wa  # noqa: E402


def main():
    old_dir, old_quota, old_output = model_usage.RUN_DIR, ai_quota.PATH, wa.OUTPUT_DIR
    old_status = copy.deepcopy(wa._poster_status)
    old_wechat_status = copy.deepcopy(wa._wx_task_status)
    old_image_model, old_throttle, old_post = ai_poster._image_model, ai_poster._throttle, requests.post
    old_tier, old_next = ai_models.tier_of, ai_models.next_after
    with tempfile.TemporaryDirectory(prefix="v2t-model-usage-") as tmp:
        root = Path(tmp)
        model_usage.RUN_DIR = root / "runs"
        ai_quota.PATH = str(root / "quota.json")
        wa.OUTPUT_DIR = root / "output"
        hits = []

        def handler(request):
            hits.append(request.url.path)
            if request.url.path.startswith("/fail/"):
                return httpx.Response(404, json={"error": {"message": "Model not exist"}})
            return httpx.Response(200, json={
                "id": "chat-test", "object": "chat.completion", "created": 1,
                "model": "unit-model", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "{}"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}})

        client = openai.OpenAI(api_key="sk-test", base_url="https://unit.invalid/v1",
                               max_retries=0,
                               http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        try:
            assert ai_quota.installed()["done"]
            with model_usage.track("poster", "plan", 101) as run:
                client.chat.completions.create(model="unit-model",
                    messages=[{"role": "user", "content": "测试"}])
                run.set_revisions("source-r1", "mother-r1")
                run.close("complete")
            report = model_usage.load(run.run_id)
            assert report["request_count"] == 1 and report["tokens_known"] == 18
            assert report["cost_yuan"] is None and report["cost_status"] == "unknown"
            assert report["calls"][0]["response_model"] == "unit-model"
            assert report["source_revision"] == "source-r1"
            assert "sk-test" not in json.dumps(report) and "测试" not in json.dumps(report)
            print("PASS SDK 边界记录实际调用与 token，费用未知且不落提示词/密钥")

            ai_models.tier_of = lambda *_a, **_kw: "fast"
            ai_models.next_after = lambda *_a, **_kw: (_ for _ in ()).throw(
                AssertionError("新图文不得隐式切换模型"))
            client_fail = openai.OpenAI(api_key="sk-test", base_url="https://unit.invalid/fail",
                                        max_retries=0,
                                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
            before = len(hits)
            with model_usage.track("wechat", "generate", 102) as failed:
                try:
                    client_fail.chat.completions.create(model="unit-model",
                        messages=[{"role": "user", "content": "测试"}])
                except openai.APIStatusError:
                    pass
                else:
                    raise AssertionError("404 应传回调用方")
                failed.close("failed")
            assert len(hits) == before + 1
            assert model_usage.load(failed.run_id)["calls"][0]["status"] == "failed"
            wa._wx_task_status.update({"running": False, "done": True, "summary_id": 0,
                                       "model_usage": {}, "error": "", "package": None})
            restored_wechat = wa.app.test_client().get("/api/wechat/status?summary_id=102").get_json()
            assert restored_wechat["model_usage"]["request_count"] == 1
            assert restored_wechat["model_usage"]["run_id"] == failed.run_id
            print("PASS 新图文模型失败只发一次，不隐式切到候选模型")

            ai_poster._image_model = lambda _cfg: "image-test"
            ai_poster._throttle = lambda: None
            image_hits = []
            def image_timeout(*_a, **_kw):
                image_hits.append(1)
                raise requests.exceptions.Timeout("隔离超时")
            requests.post = image_timeout
            with model_usage.track("poster", "poster", 103) as image_run:
                try:
                    ai_poster.generate_image("只用于测试", "1024*1536",
                                             {"api_key": "sk-test", "api_base": "https://unit.invalid"})
                except ai_poster.PosterError as exc:
                    assert "没有自动重试" in str(exc)
                else:
                    raise AssertionError("超时应阻断出图")
                image_run.close("failed")
            assert len(image_hits) == 1
            assert model_usage.load(image_run.run_id)["calls"][0]["status"] == "unknown"
            print("PASS 出图超时只发一次生成请求，费用状态保留未知")

            wa._poster_status.update({"running": False, "done": True, "summary_id": 0,
                                      "plan": None, "error": "", "package": None})
            plan = {"plan": {"title": "人工改过的标题", "cards": []}, "publish": {"copy_text": "原意"}}
            def blocked():
                client.chat.completions.create(model="unit-model",
                    messages=[{"role": "user", "content": "测试"}])
                raise ai_poster.PosterError("内容闸拦下")
            task = wa._poster_start("poster", blocked, 104, plan_snapshot=plan)
            assert task
            for _ in range(100):
                if not wa._poster_status["running"]:
                    break
                time.sleep(0.02)
            assert not wa._poster_status["running"]
            status = wa.app.test_client().get("/api/poster/status?summary_id=104").get_json()
            assert status["model_usage"]["request_count"] == 1
            saved = json.loads((wa.OUTPUT_DIR / "poster" / "104" / "plan.json").read_text())
            assert saved["plan"] == plan["plan"] and saved["model_usage"]["request_count"] == 1
            wa._poster_status.update({"running": False, "done": True, "summary_id": 0,
                                      "plan": None, "error": "", "package": None,
                                      "model_usage": {}})
            recovered = wa.app.test_client().get("/api/poster/status?summary_id=104").get_json()
            assert recovered["plan"]["plan"] == plan["plan"]
            assert recovered["model_usage"]["request_count"] == 1
            print("PASS 被拦草稿与真实调用账重启后均可恢复")

            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"source_revision": "source-r1",
                                            "mother_revision": "mother-r1",
                                            "fidelity": {"ready": True}}))
            wa._attach_model_run(manifest, run.run_id)
            view = wa._graphics_manifest_view(json.loads(manifest.read_text()),
                                              include_model_usage=True)
            assert view["model_usage"]["run_id"] == run.run_id
            assert view["generation_mode"] == "original_fidelity_v1"
            with model_usage.track("wechat", "generate", 105) as empty:
                empty.close("complete")
            no_call = model_usage.load(empty.run_id)
            assert no_call["request_count"] == 0 and no_call["cost_yuan"] == 0
            print("PASS 成品通过账本 ID 读取实际模型调用，旧版判断不受影响")
        finally:
            model_usage.RUN_DIR, ai_quota.PATH, wa.OUTPUT_DIR = old_dir, old_quota, old_output
            ai_poster._image_model, ai_poster._throttle, requests.post = old_image_model, old_throttle, old_post
            ai_models.tier_of, ai_models.next_after = old_tier, old_next
            wa._poster_status.clear()
            wa._poster_status.update(old_status)
            wa._wx_task_status.clear()
            wa._wx_task_status.update(old_wechat_status)
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
