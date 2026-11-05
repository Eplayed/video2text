# -*- coding: utf-8 -*-
"""新图文任务的逐请求用量账：只记模型和数字，不存提示词、原文或密钥。"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "output" / "model_runs"
SCHEMA = 1
_ACTIVE = ContextVar("v2t_model_run", default=None)
_LIVE = set()
_LIVE_LOCK = threading.Lock()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _tokens(usage, *names):
    for name in names:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        if value is not None:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                pass
    return None


class ModelRun:
    def __init__(self, channel, stage, summary_id, allow_failover=False):
        self.run_id = uuid.uuid4().hex
        self.channel = str(channel)
        self.stage = str(stage)
        self.summary_id = int(summary_id)
        self.allow_failover = bool(allow_failover)
        self.status = "running"
        self.started_at = _now()
        self.started_ns = time.time_ns()
        self.finished_at = ""
        self.source_revision = ""
        self.mother_revision = ""
        self.calls = []
        self._save()

    def _save(self):
        """每次发请求前落盘；服务中断后 pending 记录仍在，不把未知记成 0。"""
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        path = RUN_DIR / (self.run_id + ".json")
        tmp = RUN_DIR / (self.run_id + ".tmp")
        tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))

    def set_revisions(self, source_revision, mother_revision):
        self.source_revision = str(source_revision or "")
        self.mother_revision = str(mother_revision or "")
        self._save()

    def begin(self, kind, model):
        call_id = uuid.uuid4().hex[:12]
        self.calls.append({"call_id": call_id, "kind": str(kind),
                           "requested_model": str(model or ""), "response_model": "",
                           "status": "pending", "started_at": _now(), "finished_at": "",
                           "prompt_tokens": None, "completion_tokens": None,
                           "total_tokens": None})
        self._save()
        return call_id

    def end(self, call_id, status, usage=None, response_model=""):
        for call in self.calls:
            if call["call_id"] == call_id:
                call["status"] = str(status)
                call["finished_at"] = _now()
                call["response_model"] = str(response_model or "")
                if usage is not None:
                    call["prompt_tokens"] = _tokens(usage, "prompt_tokens", "input_tokens")
                    call["completion_tokens"] = _tokens(usage, "completion_tokens", "output_tokens")
                    call["total_tokens"] = _tokens(usage, "total_tokens")
                self._save()
                return
        raise ValueError("模型请求记录不存在")

    def close(self, status):
        self.status = str(status)
        self.finished_at = _now()
        self._save()

    def snapshot(self):
        calls = [dict(item) for item in self.calls]
        known = [item["total_tokens"] for item in calls if item["total_tokens"] is not None]
        return {"schema": SCHEMA, "run_id": self.run_id, "channel": self.channel,
                "stage": self.stage, "summary_id": self.summary_id,
                "status": self.status, "started_at": self.started_at,
                "started_ns": self.started_ns,
                "finished_at": self.finished_at,
                "source_revision": self.source_revision,
                "mother_revision": self.mother_revision,
                "allow_failover": self.allow_failover,
                "request_count": len(calls), "tokens_known": sum(known),
                "tokens_unknown_count": len(calls) - len(known),
                "cost_yuan": None if calls else 0,
                "cost_status": "unknown" if calls else "no_model_requests",
                "calls": calls}


@contextmanager
def track(channel, stage, summary_id, allow_failover=False):
    run = ModelRun(channel, stage, summary_id, allow_failover=allow_failover)
    with _LIVE_LOCK:
        _LIVE.add(run.run_id)
    token = _ACTIVE.set(run)
    try:
        yield run
    finally:
        try:
            if run.status == "running":
                run.close("interrupted")
        finally:
            with _LIVE_LOCK:
                _LIVE.discard(run.run_id)
            _ACTIVE.reset(token)


def current():
    return _ACTIVE.get()


def load(run_id):
    if not re.fullmatch(r"[0-9a-f]{32}", str(run_id or "")):
        return {}
    try:
        data = json.loads((RUN_DIR / (run_id + ".json")).read_text(encoding="utf-8"))
        if data.get("status") == "running":
            with _LIVE_LOCK:
                if run_id not in _LIVE:
                    data["status"] = "interrupted"
        return data
    except (OSError, ValueError):
        return {}


def latest(summary_id, channel):
    """重启后按素材与渠道找最近一次账；读不到只返回空，不伪造 0 次。"""
    if not RUN_DIR.is_dir():
        return {}
    target_id = int(summary_id)
    found = []
    for path in RUN_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("summary_id") == target_id and data.get("channel") == channel:
                found.append(data)
        except (OSError, ValueError):
            continue
    if not found:
        return {}
    data = max(found, key=lambda item: int(item.get("started_ns") or 0))
    if data.get("status") == "running":
        with _LIVE_LOCK:
            if data.get("run_id") not in _LIVE:
                data["status"] = "interrupted"
    return data
