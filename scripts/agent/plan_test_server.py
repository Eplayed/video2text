# -*- coding: utf-8 -*-
"""隔离测试服务：真 Flask + 临时库副本 + 装在 openai 客户端边界的模型桩。

为什么要这么一层（Codex 第一单验收 R4/R3 两条点名）：
  · R4：`ui_plan_task_walk.py` 原来直连 15801 与真实库，还会按 slot_key='setup' DELETE 真实选题。
    现在界面证据一律打在**这个进程**上：端口另起、DB_PATH 指到临时副本、脚本只认自己造的 slot，
    而且**解析出的库路径等于生产库就拒绝启动**（不靠"我记得没写"）。
  · R3：桩必须装在真正发请求的那一层。这里替换的是 `sys.modules['openai']`——
    `content_store._generate_with_llm` 里 `import openai; client.chat.completions.create(messages=...)`
    走的是原代码，**提示词由 `_prompt()` 真构造**，我们只截获发出去的内容。
    在 `generate_summary` 那层 mock 会连提示词一起替掉，上下文丢了也测不出来。

模式：
  --mode ok      正常返回（可 --delay 秒拖住，用来拍「整理中」）
  --mode raise    在生成层抛异常（真跑对应模型超时/额度耗尽），用来拍「失败 + 恢复」
  --mode nokey   不抛，但把 api_key 置空 → 走本地模板兜底（证明旧链路仍兼容）

用法（由 walk 脚本自动拉起，也可手跑）：
  python3 scripts/agent/plan_test_server.py --port 15999 --db /tmp/x.db --delay 6 --capture /tmp/prompts.json
"""
import argparse
import json
import os
import shutil
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROD_DB = (ROOT / "output" / "video2text.db").resolve()
CAPTURED = []           # 每次 create() 的完整 messages，供 --capture 落盘与断言


def install_fake_openai(mode, delay):
    """把 `openai` 换成假模块。content_store 里那句 `import openai` 拿到的就是它。"""

    class _Msg(object):
        def __init__(self, content):
            self.content = content

    class _Choice(object):
        def __init__(self, content):
            self.message = _Msg(content)

    class _Resp(object):
        def __init__(self, content):
            self.choices = [_Choice(content)]

    class _Completions(object):
        def create(self, model=None, messages=None, temperature=None, response_format=None, **kw):
            text = ""
            for m in (messages or []):
                if m.get("role") == "user":
                    text = m.get("content") or ""
            CAPTURED.append({"model": model, "prompt": text, "at": time.strftime("%H:%M:%S")})
            if delay:
                time.sleep(delay)
            # 返回形状按 _prompt 里那份 schema 给：title/content/outline/keywords
            body = {
                "title": "桩·整理稿",
                "outline": ["【标题候选】", "【导语】", "【核心论点】"],
                "content": "## 桩小节\n这是模型桩返回的正文，不是真模型产出。",
                "keywords": ["桩"],
            }
            return _Resp(json.dumps(body, ensure_ascii=False))

    class _Chat(object):
        completions = _Completions()

    class _OpenAI(object):
        def __init__(self, *a, **kw):
            self.chat = _Chat()

    mod = types.ModuleType("openai")
    mod.OpenAI = _OpenAI
    sys.modules["openai"] = mod
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=15999)
    ap.add_argument("--db", default="")
    ap.add_argument("--from-db", default=str(PROD_DB))
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--mode", choices=("ok", "raise", "nokey"), default="ok")
    ap.add_argument("--capture", default="")
    a = ap.parse_args()

    db = Path(a.db).resolve() if a.db else Path(tempfile_dir()) / "plan_test.db"
    if db == PROD_DB:
        print("REFUSED: --db 指向生产库 %s" % PROD_DB, file=sys.stderr)
        return 2
    if not db.exists():
        shutil.copyfile(a.from_db, str(db))
    sys.path.insert(0, str(ROOT / "web"))
    sys.path.insert(0, str(ROOT))
    install_fake_openai(a.mode, a.delay)
    import app                                              # noqa: E402
    import src.content_store as cs                          # noqa: E402

    app.DB_PATH = db
    app.OUTPUT_DIR = db.parent / "output"
    app._sync_content_db = lambda: None                     # 不读真 Excel
    app._video_keyframe_markdown = lambda vs: ""            # 不抽帧、不下载视频
    app._fast_config = lambda: {
        "method": "skip" if a.mode == "nokey" else "openai",
        "api_key": "" if a.mode == "nokey" else "stub-key",
        "model": "stub-model", "api_base": "http://127.0.0.1:9/v1", "fast_model": "stub-model",
    }
    if a.mode == "raise":
        def boom(*args, **kw):
            raise RuntimeError("stub: 模型超时（隔离测试服务抛的，不是真模型）")
        cs.generate_summary = boom
        cs.generate_collection_summary = boom

    if a.capture:
        import threading

        def dump():
            while True:
                time.sleep(1.0)
                try:
                    Path(a.capture).write_text(json.dumps(CAPTURED, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
        threading.Thread(target=dump, daemon=True).start()

    print("READY port=%d db=%s mode=%s delay=%s" % (a.port, db, a.mode, a.delay), flush=True)
    app.app.run(host="127.0.0.1", port=a.port, debug=False, use_reloader=False, threaded=True)
    return 0


def tempfile_dir():
    import tempfile
    return tempfile.mkdtemp(prefix="plan_test_srv_")


if __name__ == "__main__":
    sys.exit(main())
