# -*- coding: utf-8 -*-
"""自动同步任务的配置与到期判断（数据在 config/sync_tasks.json，不进 git）。

为什么要有这一层：订阅同步以前只能人过去点，一天不点就一天没新料；而"什么时候该跑"
这件事完全是本地偏好，不该塞进 subscriptions 表（那张表是采集到的实体，删订阅就该没）。

一个任务 = 一组订阅 + 一个频率 + 一份执行账。用户勾一批号建的是"一个活儿"，
不是 N 个任务；调度也确实是并成一批跑（抖音多条只起一次 Chromium），
所以账本按任务记才对得上界面。

任务里只存"该不该跑、上次几点跑的、跑成没跑成"，不存"跑出来几条"：
每条订阅的上次同步时间与新增条数本来就写在 subscriptions 表里
（last_synced_at / last_new_count / last_sync_note，手动同步也会更新它们），
再抄一份进 JSON 就成了两份真相，界面上迟早对不上。

调度本身在 web/app.py 的守护线程里（每分钟看一眼），本模块只做纯判断，
所以想改成系统 cron 调脚本时，到期逻辑一行都不用动。Python 3.9 兼容。
"""
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
STORE_PATH = ROOT / "config" / "sync_tasks.json"

# 频率只给"天"这一档：用户要的是每天或每两天，小时级留给手动
INTERVALS = (1, 2, 3, 7)
DEFAULT_RUN_AT = "09:30"
TIME_FMT = "%Y-%m-%d %H:%M:%S"
MAX_TASKS = 20
NAME_MAX = 20


def _blank() -> Dict:
    return {"enabled": False, "run_at": DEFAULT_RUN_AT, "tasks": [],
            "updated_at": "", "last_batch": None}


def _new_key() -> str:
    return "t" + uuid.uuid4().hex[:6]


def _clean_ids(raw) -> List[int]:
    """订阅 id 列表：去重、保序、非法项丢掉。"""
    out: List[int] = []
    for v in raw or []:
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in out:
            out.append(n)
    return out


def _default_name(sub_ids: List[int]) -> str:
    return "%d 个订阅" % len(sub_ids)


def _norm_task(t: Dict) -> Optional[Dict]:
    if not isinstance(t, dict):
        return None
    subs = _clean_ids(t.get("sub_ids"))
    if not subs:
        return None
    name = str(t.get("name") or "").strip()[:NAME_MAX]
    return {"key": str(t.get("key") or _new_key()),
            "name": name or _default_name(subs),
            "sub_ids": subs,
            "interval_days": _norm_interval(t.get("interval_days")),
            "last_run": t.get("last_run") or "",
            "last_status": t.get("last_status") or "",
            "last_note": t.get("last_note") or ""}


def _migrate(raw: List) -> List[Dict]:
    """老格式（一条订阅一个任务，只有 sub_id）并成"同一频率一个任务"。

    不迁移的话，用户按老界面勾出来的 6 个号会在界面上摊成 6 行，跟"这是一个任务"的
    直觉对不上。合并时执行账取最近跑过的那条，别把已经跑过的记录弄丢。
    """
    out: List[Dict] = []
    legacy: List[Dict] = []
    for t in raw or []:
        if not isinstance(t, dict):
            continue
        if t.get("sub_ids"):
            n = _norm_task(t)
            if n:
                out.append(n)
        elif t.get("sub_id"):
            legacy.append(t)
    by_iv: Dict[int, Dict] = {}
    for t in legacy:
        subs = _clean_ids([t.get("sub_id")])
        if not subs:
            continue
        iv = _norm_interval(t.get("interval_days"))
        g = by_iv.setdefault(iv, {"subs": [], "last_run": "", "last_status": "", "last_note": ""})
        if subs[0] not in g["subs"]:
            g["subs"].append(subs[0])
        if str(t.get("last_run") or "") > g["last_run"]:
            g["last_run"] = t.get("last_run") or ""
            g["last_status"] = t.get("last_status") or ""
            g["last_note"] = t.get("last_note") or ""
    for iv in sorted(by_iv):
        g = by_iv[iv]
        out.append({"key": _new_key(), "name": _default_name(g["subs"]), "sub_ids": g["subs"],
                    "interval_days": iv, "last_run": g["last_run"],
                    "last_status": g["last_status"], "last_note": g["last_note"]})
    return out


def load() -> Dict:
    """读配置。文件坏了一律回退成"没有任务"，绝不让同步链路挂在这里。

    老格式在这里就地迁移并回写一次：任务 key 是随机生成的，不回写的话每次读都换 key，
    界面就改不动、删不掉。
    """
    if not STORE_PATH.exists():
        return _blank()
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return _blank()
    if not isinstance(data, dict):
        return _blank()
    raw = data.get("tasks") or []
    legacy = any(isinstance(t, dict) and not t.get("sub_ids") and t.get("sub_id") for t in raw)
    cfg = _blank()
    cfg.update(data)
    cfg["enabled"] = bool(cfg.get("enabled"))
    cfg["run_at"] = _norm_time(cfg.get("run_at")) or DEFAULT_RUN_AT
    if legacy:
        cfg["tasks"] = _migrate(raw)
        save(cfg)
    else:
        cfg["tasks"] = [t for t in (_norm_task(x) for x in raw) if t]
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


def get_task(cfg: Dict, key: str) -> Optional[Dict]:
    for t in cfg.get("tasks") or []:
        if t.get("key") == key:
            return t
    return None


def new_task(cfg: Dict, sub_ids, interval_days: int = 1, name: str = "") -> Optional[Dict]:
    """新建任务（一组订阅一个任务）。任务数满了或没有效订阅返回 None。"""
    if len(cfg.get("tasks") or []) >= MAX_TASKS:
        return None
    subs = _clean_ids(sub_ids)
    if not subs:
        return None
    t = {"key": _new_key(), "name": str(name or "").strip()[:NAME_MAX] or _default_name(subs),
         "sub_ids": subs, "interval_days": _norm_interval(interval_days),
         "last_run": "", "last_status": "", "last_note": ""}
    cfg["tasks"].append(t)
    return t


def update_task(cfg: Dict, key: str, name=None, interval_days=None, sub_ids=None) -> Optional[Dict]:
    t = get_task(cfg, key)
    if not t:
        return None
    if name is not None:
        t["name"] = str(name).strip()[:NAME_MAX] or t["name"]
    if interval_days is not None:
        t["interval_days"] = _norm_interval(interval_days)
    if sub_ids is not None:
        subs = _clean_ids(sub_ids)
        # 成员被清空时不动任务本身：删除任务是另一个动作，别让用户以为改频率删了任务
        if subs:
            t["sub_ids"] = subs
            if str(t["name"]).endswith("个订阅"):
                t["name"] = _default_name(subs)
    return t


def add_members(cfg: Dict, key: str, sub_ids) -> Optional[Dict]:
    t = get_task(cfg, key)
    if not t:
        return None
    for n in _clean_ids(sub_ids):
        if n not in t["sub_ids"]:
            t["sub_ids"].append(n)
    if str(t["name"]).endswith("个订阅"):
        t["name"] = _default_name(t["sub_ids"])
    return t


def remove_member(cfg: Dict, key: str, sub_id: int) -> Optional[Dict]:
    """从任务里移出一个订阅；最后一个移走时把空任务一起删掉（留着它只会一直报"没有成员"）。"""
    t = get_task(cfg, key)
    if not t:
        return None
    t["sub_ids"] = [i for i in t["sub_ids"] if i != int(sub_id)]
    if not t["sub_ids"]:
        remove_task(cfg, key)
        return None
    if str(t["name"]).endswith("个订阅"):
        t["name"] = _default_name(t["sub_ids"])
    return t


def remove_task(cfg: Dict, key: str) -> Dict:
    cfg["tasks"] = [t for t in cfg.get("tasks") or [] if t.get("key") != key]
    return cfg


def drop_subs(cfg: Dict, sub_ids) -> Dict:
    """订阅被删掉时清理成员列表，成员空了的任务一并删。"""
    gone = set(_clean_ids(sub_ids))
    if not gone:
        return cfg
    kept = []
    for t in cfg.get("tasks") or []:
        t["sub_ids"] = [i for i in t.get("sub_ids") or [] if i not in gone]
        if t["sub_ids"]:
            kept.append(t)
    cfg["tasks"] = kept
    return cfg


def next_due(task: Dict, run_at: str, now: datetime) -> Optional[datetime]:
    """下一次该跑的时间点。频率按整天数走，但不会早于当天设定的时刻。

    从没跑过的任务算"今天这个点"，哪怕现在已过点：刚建好任务，用户希望
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


def live_members(task: Dict, alive) -> List[int]:
    """任务里还活着的订阅（订阅被删了就不该再算进这一轮）。"""
    subs = task.get("sub_ids") or []
    return subs if alive is None else [i for i in subs if i in alive]


def due_tasks(cfg: Dict, now: datetime, alive=None) -> List[Dict]:
    out = []
    for t in cfg.get("tasks") or []:
        if not live_members(t, alive):
            continue
        if is_due(t, cfg.get("run_at") or DEFAULT_RUN_AT, now):
            out.append(t)
    return out


def due_sub_ids(cfg: Dict, now: datetime, alive=None) -> List[int]:
    """这批到期任务实际要同步哪些订阅（跨任务去重，先出现的先跑）。"""
    ids: List[int] = []
    for t in due_tasks(cfg, now, alive):
        for sid in live_members(t, alive):
            if sid not in ids:
                ids.append(sid)
    return ids


def mark_batch(cfg: Dict, keys: List[str], ok: bool, note: str,
               now: Optional[datetime] = None) -> Dict:
    """一轮跑完按任务记账。到期判断只看 last_run，所以失败也记时间——
    否则一个拉不到的订阅会被每分钟重试一次，把上游打成骚扰。"""
    now = now or datetime.now()
    stamp = now.strftime(TIME_FMT)
    touched = set(keys or [])
    for t in cfg.get("tasks") or []:
        if t.get("key") in touched:
            t["last_run"] = stamp
            t["last_status"] = "ok" if ok else "fail"
            t["last_note"] = (note or "")[:200]
    cfg["last_batch"] = {"at": stamp, "keys": sorted(touched), "ok": bool(ok),
                         "note": (note or "")[:300]}
    return cfg
