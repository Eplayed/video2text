# -*- coding: utf-8 -*-
"""海报线的隔离测试服务：真 Flask + 临时库副本 + 临时成品目录 + 模型桩 + 独立端口。

为什么要有这一层（2026-10-21 Codex 复核 P1，点名 `ui_guide_gate_walk.py`）：
  那条走查把 BASE 写死成 `http://127.0.0.1:15801`，然后向 `/api/poster/generate` 发真 POST。
  它的安全前提是"闸口会拦下"——**一旦被测功能回归，测试自己就开始生成并覆盖
  `output/poster/87`，甚至发收费请求**。只核 manifest 的 mtime 是事后发现，防不住。
  现在界面证据一律打在这个进程上：
  · 端口另起（默认 15998），UI 脚本只连它；
  · `DB_PATH` 指到生产库的**副本**，成品目录指到临时目录；
  · **解析出的库路径等于生产库、或成品目录落在仓库 `output/` 里 → 直接拒绝启动**（不靠"我记得没写"）；
  · `requests` 在 `ai_poster` 里被换成"一调就抛"的桩：真要出图收费，这里当场报错而不是扣钱；
  · `openai` 换成内存桩，看图/评审那类调用拿到的是固定答案。

模式：
  --gate on    闸口照常（默认）：87 那份带"46亿"的文案必须被拦住
  --gate off   **故意让事实闸失效**（模拟回归）：用来证明"闸口坏了也只会写临时目录，
               生产成品一个字节都不动"——这条反向验证按任务书要求必须在隔离服务上跑

用法（由 walk 脚本自动拉起，也可手跑）：
  python3 scripts/agent/poster_test_server.py --port 15998 --gate off
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROD_DB = (ROOT / "output" / "video2text.db").resolve()
PROD_OUTPUT = (ROOT / "output").resolve()
CAPTURED = []


def _blocked(kind):
    def _call(*a, **kw):
        raise RuntimeError("隔离测试服务：拦住了一次真实%s请求（%s）"
                           % (kind, str(a[0] if a else "")[:70]))
    return _call


def make_no_network():
    """把 `ai_poster.requests` 换成本地桩：出图/下载那一跳一调就抛，绝不真联网。

    桩装在**发请求的那一层**（不是装在 generate_poster 外面），所以提示词、版面预算、
    闸口判据都还是真代码在跑，只有真正掏钱的那一跳被截住。
    `ai_poster` 的 except 里会取 `requests.exceptions.Timeout/ConnectionError`，
    所以这两个名字必须给真的异常类，否则桩本身会把错误搞成 AttributeError。
    """
    exc = types.ModuleType("fake_requests_exceptions")
    exc.Timeout = TimeoutError
    exc.ConnectionError = ConnectionError
    exc.RequestException = Exception
    mod = types.ModuleType("fake_requests")
    mod.post = _blocked("出图收费")
    mod.get = _blocked("下载")
    mod.exceptions = exc
    mod.Timeout = TimeoutError
    mod.ConnectionError = ConnectionError
    return mod


def install_fake_openai():
    """把 `openai` 换成假模块：ai_poster._ask_vision / 视觉评审走的就是它。"""

    class _Msg(object):
        def __init__(self, content):
            self.content = content

    class _Choice(object):
        def __init__(self, content):
            self.message = _Msg(content)

    class _Usage(object):
        total_tokens = 0

    class _Resp(object):
        def __init__(self, content):
            self.choices = [_Choice(content)]
            self.usage = _Usage()

    class _Completions(object):
        def create(self, model=None, messages=None, **kw):
            text = ""
            for m in (messages or []):
                if m.get("role") == "user":
                    c = m.get("content")
                    if isinstance(c, list):
                        text = " ".join(str(x.get("text") or "") for x in c if isinstance(x, dict))
                    else:
                        text = str(c or "")
            CAPTURED.append({"model": model, "prompt": text[:200],
                             "at": time.strftime("%H:%M:%S")})
            if "post_text" in text:                      # 判帧那一句（这里只给形状，不给真判断）
                return _Resp(json.dumps({"post_text": False, "where": "none",
                                         "subject": "桩：隔离服务不作真判断"}, ensure_ascii=False))
            return _Resp(json.dumps({"score": 7, "full": 10, "notes": ["模型桩，不作数"]},
                                    ensure_ascii=False))

    class _Chat(object):
        completions = _Completions()

    class _OpenAI(object):
        def __init__(self, *a, **kw):
            self.chat = _Chat()

    mod = types.ModuleType("openai")
    mod.OpenAI = _OpenAI
    sys.modules["openai"] = mod
    return mod


def guard(db, poster_out):
    """两道硬闸：路径撞上生产就拒绝启动。返回错误串（空串＝可以起）。"""
    if db == PROD_DB:
        return "--db 指向生产库 %s" % PROD_DB
    if PROD_OUTPUT in poster_out.parents or poster_out == PROD_OUTPUT:
        return "--poster-out 落在仓库 output/ 里（%s）：成品目录必须是临时目录" % poster_out
    if str(poster_out).startswith(str(PROD_OUTPUT)):
        return "--poster-out 是生产 output 的子目录（%s）" % poster_out
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=15998)
    ap.add_argument("--db", default="")
    ap.add_argument("--from-db", default=str(PROD_DB))
    ap.add_argument("--poster-out", default="")
    ap.add_argument("--gate", choices=("on", "off"), default="on",
                    help="off＝故意让事实闸失效，用来反向验证「闸坏了也写不到生产」")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="poster_srv_"))
    db = Path(a.db).resolve() if a.db else (tmp / "video2text.db")
    poster_out = Path(a.poster_out).resolve() if a.poster_out else (tmp / "poster")
    bad = guard(db, poster_out)
    if bad:
        print("REFUSED: %s" % bad, file=sys.stderr)
        return 2
    if not db.exists():
        shutil.copyfile(a.from_db, str(db))
    poster_out.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(ROOT / "web"))
    sys.path.insert(0, str(ROOT))
    install_fake_openai()
    import app                                              # noqa: E402
    from src import ai_poster                               # noqa: E402
    from src import fact_gate                               # noqa: E402

    app.DB_PATH = db
    app.OUTPUT_DIR = poster_out.parent            # 预览等临时产物也跟着落到临时根目录
    ai_poster.OUTPUT_DIR = poster_out
    ai_poster.requests = make_no_network()        # 收费那一跳一律拦住
    app._sync_content_db = lambda: None           # 不读真 Excel
    app._video_keyframe_markdown = lambda vs: ""  # 不抽帧、不下载视频
    app._fast_config = lambda: {
        "method": "openai", "api_key": "stub-key-not-real",
        "model": "stub-model", "api_base": "http://127.0.0.1:9/v1", "fast_model": "stub-model",
    }
    if a.gate == "off":
        ai_poster.fact_gate = fact_gate
        fact_gate.check = lambda plan, content, **kw: ([], [])
        fact_gate.copy_is_cut = lambda text: ""
    print("READY port=%d db=%s poster_out=%s gate=%s" % (a.port, db, poster_out, a.gate), flush=True)
    app.app.run(host="127.0.0.1", port=a.port, debug=False, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
