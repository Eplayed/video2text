#!/usr/bin/env python3
"""用合成稿和隔离 Flask 运行项目规定的 ui_walk.py --plan，不发模型请求。"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from flask import jsonify
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src import content_store  # noqa: E402
from web import app as wa  # noqa: E402


def main():
    old_db = wa.DB_PATH
    old_plan = wa.app.view_functions["api_poster_plan"]
    old_status = wa.app.view_functions["api_poster_status"]
    old_preview = wa.app.view_functions["api_poster_preview"]
    with tempfile.TemporaryDirectory(prefix="v2t-ui-walk-t16-") as tmp:
        db = Path(tmp) / "test.db"
        content_store.connect(db).close()
        wa.DB_PATH = db
        plan = {"title": "两个小号的奖励条件", "subtitle": "先看原文限制",
                "style_key": "guide_ink", "layout_key": "guide", "role": "清单",
                "cards": [{"t": "奖励归属", "d": "仅两个小号符合条件", "point_ids": ["p1"]}],
                "pages": [{"title": "两个小号的奖励条件", "subtitle": "先看原文限制",
                           "style_key": "guide_ink", "layout_key": "guide", "role": "清单",
                           "cards": [{"t": "奖励归属", "d": "仅两个小号符合条件",
                                      "point_ids": ["p1"]}]}]}
        usage = {"run_id": "b" * 32, "status": "complete", "request_count": 1,
                 "tokens_known": 18, "tokens_unknown_count": 0, "cost_yuan": None,
                 "cost_status": "unknown", "calls": [{"requested_model": "unit-model"}]}
        state = {"running": False, "done": True, "task_id": 1, "summary_id": 1,
                 "plan": {"plan": plan, "publish": {"copy_text": "两个小号的奖励条件"},
                          "fidelity": {"ready": False, "problems": []}},
                 "model_usage": usage, "progress": "合成稿已拆解", "error": ""}
        wa.app.view_functions["api_poster_plan"] = lambda: jsonify({"task_id": 1,
                                                                       "status": "started"})
        wa.app.view_functions["api_poster_status"] = lambda: jsonify(state)
        wa.app.view_functions["api_poster_preview"] = lambda: jsonify({
            "url": "/static/fixture.png", "chars": 29,
            "check": {"cards": 1, "problems": [], "layout_label": "攻略页"},
            "pages": [{"url": "/static/fixture.png", "page": 1, "chars": 29,
                       "check": {"cards": 1, "problems": [], "layout_label": "攻略页"}}]})
        server = make_server("127.0.0.1", 0, wa.app, threaded=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            env = dict(os.environ, V2T_WALK_BASE="http://127.0.0.1:%d" % server.server_port)
            result = subprocess.run([sys.executable, "scripts/agent/ui_walk.py", "1",
                                     "隔离原意稿", "--plan"], cwd=str(ROOT), env=env,
                                    capture_output=True, text=True, timeout=90)
            print(result.stdout)
            if result.returncode or "弹窗里没有文案" in result.stdout or "JS 报错:" in result.stdout:
                print(result.stderr)
                raise AssertionError("ui_walk.py --plan 隔离走查失败")
            assert "── 预览结果区 ──" in result.stdout
            assert "没有判合格的素材画面，按纯文字攻略页出图" in result.stdout
            assert "画面全用素材里挑出来的真帧" not in result.stdout
            print("PASS 项目 ui_walk.py --plan 用合成稿完整走过弹窗，未发真实模型请求")
        finally:
            server.shutdown()
            worker.join(timeout=5)
            wa.app.view_functions["api_poster_plan"] = old_plan
            wa.app.view_functions["api_poster_status"] = old_status
            wa.app.view_functions["api_poster_preview"] = old_preview
            wa.DB_PATH = old_db
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
