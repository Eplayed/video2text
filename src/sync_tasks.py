# -*- coding: utf-8 -*-
"""自动同步任务的配置与到期判断（数据在 config/sync_tasks.json，不进 git）。

为什么要有这一层：订阅同步以前只能人过去点，一天不点就一天没新料；而"什么时候该跑"
这件事完全是本地偏好，不该塞进 subscriptions 表（那张表是采集到的实体，删订阅就该没）。

这里只存"该不该跑、上次几点跑的、跑成没跑成"，不存"跑出来几条"：
每条订阅的上次同步时间与新增条数本来就写在 subscriptions 表里
（last_synced_at / last_new_count / last_sync_note，手动同步也会更新它们），
再抄一份进 JSON 就成了两份真相，界面上迟早对不上。

调度本身在 web/app.py 的守护线程里（每分钟看一眼），本模块只做纯判断，
所以想改成系统 cron 调脚本时，到期逻辑一行都不用动。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
STORE_PATH = ROOT / "config" / "sync_tasks.json"

# 频率只给"天"这一档：用户要的是每天或每两天，小时级留给手动
INTERVALS = (1, 2, 3, 7)
DEFAULT_RUN_AT = "09:30"
TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _blank() -> Dict:
    return {"enabled": False, "run_at": DEFAULT_RUN_AT, "tasks": [],
            "updated_at": "", "last_batch": None}


def load() -> Dict:
    """读配置。文件坏了一律回退成"没有任务"，绝不让同步链路挂在这里。"""
    if not STORE_PATH.exists():
        return _blank()
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return _blank()
    if not isinstance(data, dict):
        return _blank()
    cfg = _blank()
    cfg.update(data)
    cfg["enabled"] = bool(cfg.get("enabled"))
    cfg["run_at"] = _norm_time(cfg.get("run_at")) or DEFAULT_RUN_AT
    tasks = []
    for t in cfg.get("tasks") or []:
        if not isinstance(t, dict):
            continue
        try:
            sid = int(t.get("sub_id"))
        except (TypeError, ValueError):
            continue
        tasks.append({"sub_id": sid,
                      "interval_days": _norm_interval(t.get("interval_days")),
                      "last_run": t.get("last_run") or "",
                      "last_status": t.get("last_status") or "",
                      "last_note": t.get("last_note") or ""})
    cfg["tasks"] = tasks
    return cfg


def save(cfg: Dict) -> Dict:
    cfg = dict(cfg)
    cfg["run_at"] = _norm_time(cfg.get("run_at")) or DEFAULT_RUN_AT
    cfg["updated_at"] = datetime.now().strftime(TIME_FMT)
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def _norm_time(v) -> str:
    """'9:5' -> '09:05'；不合法返回空串，由调用方回落默认值。"""
    s = str(v or "").strip()
    parts = s.split(":")
    if len(parts) != 2:
        return ""
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return ""
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return ""
    return "%02d:%02d" % (h, m)


def _norm_interval(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 1
    return n if n in INTERVALS else 1


def _parse(ts: str) -> Optional[datetime]:
    try:
        return datetime.strptime(ts, TIME_FMT)
    except (TypeError, ValueError):
        return None


def upsert(cfg: Dict, sub_id: int, interval_days: int) -> Dict:
    """加一条任务；已存在就只改频率，保留它的执行账（last_run）。"""
    sub_id = int(sub_id)
    interval_days = _norm_interval(interval_days)
    for t in cfg["tasks"]:
        if t["sub_id"] == sub_id:
            t["interval_days"] = interval_days
            break
    else:
        cfg["tasks"].append({"sub_id": sub_id, "interval_days": interval_days,
                             "last_run": "", "last_status": "", "last_note": ""})
    return cfg


def remove(cfg: Dict, sub_id: int) -> Dict:
    sub_id = int(sub_id)
    cfg["tasks"] = [t for t in cfg["tasks"] if t["sub_id"] != sub_id]
    return cfg


def next_due(task: Dict, run_at: str, now: datetime) -> Optional[datetime]:
    """下一次该跑的时间点。频率按整天数走，但不会早于当天设定的时刻。

    从没跑过的任务算"今天这个点"，哪怕现在已过点：刚勾上一条订阅，用户希望
    一分钟内就看到它跑一轮（这也是验证配置对不对最快的办法），而不是等到明天。
    """
    last = _parse(task.get("last_run") or "")
    hh, mm = [int(x) for x in (run_at or DEFAULT_RUN_AT).split(":")]
    if not last:
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    due = last + timedelta(days=int(task.get("interval_days") or 1))
    floor = due.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return max(due, floor)


def is_due(task: Dict, run_at: str, now: datetime) -> bool:
    nxt = next_due(task, run_at, now)
    return bool(nxt and now >= nxt)


def due_task_ids(cfg: Dict, now: datetime, valid_sub_ids=None) -> List[int]:
    """到期的任务对应的订阅 id。valid_sub_ids 用来剔掉已经被删掉的订阅。"""
    ids = []
    for t in cfg.get("tasks") or []:
        if valid_sub_ids is not None and t["sub_id"] not in valid_sub_ids:
            continue
        if is_due(t, cfg.get("run_at") or DEFAULT_RUN_AT, now):
            ids.append(t["sub_id"])
    return ids


def mark_batch(cfg: Dict, sub_ids: List[int], ok: bool, note: str,
               now: Optional[datetime] = None) -> Dict:
    """一轮跑完记账。到期判断只看 last_run，所以失败也记时间——
    否则一条拉不到的订阅会被每分钟重试一次，把上游打成骚扰。"""
    now = now or datetime.now()
    stamp = now.strftime(TIME_FMT)
    touched = set(int(i) for i in sub_ids)
    for t in cfg["tasks"]:
        if t["sub_id"] in touched:
            t["last_run"] = stamp
            t["last_status"] = "ok" if ok else "fail"
            t["last_note"] = (note or "")[:200]
    cfg["last_batch"] = {"at": stamp, "subs": sorted(touched), "ok": bool(ok),
                         "note": (note or "")[:300]}
    return cfg
