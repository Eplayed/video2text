# -*- coding: utf-8 -*-
"""自动同步任务的配置与到期判断（数据在 config/sync_tasks.json，不进 git）。

为什么要有这一层：订阅同步以前只能人过去点，一天不点就一天没新料；而"什么时候该跑"
这件事完全是本地偏好，不该塞进 subscriptions 表（那张表是采集到的实体，删订阅就该没）。

一个任务 = 一组订阅 + 一个频率 + 一个执行时刻 + 一份执行账。用户勾 6 个号建的是"一个活儿"，
不是 N 个任务；调度也确实是并成一批跑（抖音多条只起一次 Chromium），
所以账本按任务记才对得上界面。
时刻也按任务记：全局那格只是"新建任务的默认时刻"，改它不该把已有三条一起挪走。

任务里只存"该不该跑、上次几点跑的、跑成没跑成"，不存"跑出来几条"：
每条订阅的上次同步时间与新增条数本来就写在 subscriptions 表里
（last_synced_at / last_new_count / last_sync_note，手动同步也会更新它们），
再抄一份进 JSON 就成了两份真相，界面上迟早对不上。

调度本身在 web/app.py 的守护线程里（每分钟看一眼），本模块只做纯判断，
所以想改成系统 cron 调脚本时，到期逻辑一行都不用动。Python 3.9 兼容。
"""
import json
import os
import threading
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


def _norm_task(t: Dict, run_at_fallback: str = "") -> Optional[Dict]:
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
            # 时刻是任务自己的：老数据没有这一格，读的时候按当时那份全局时刻补上（load 里回写一次）
            "run_at": _norm_time(t.get("run_at")) or _norm_time(run_at_fallback) or DEFAULT_RUN_AT,
            "last_run": t.get("last_run") or "",
            "last_status": t.get("last_status") or "",
            "last_note": t.get("last_note") or ""}


def _migrate(raw: List, run_at_fallback: str = "") -> List[Dict]:
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
            n = _norm_task(t, run_at_fallback)
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
                    "interval_days": iv,
                    "run_at": _norm_time(run_at_fallback) or DEFAULT_RUN_AT,
                    "last_run": g["last_run"],
                    "last_status": g["last_status"], "last_note": g["last_note"]})
    return out


def load() -> Dict:
    """读配置。文件坏了一律回退成"没有任务"，绝不让同步链路挂在这里。

    两种就地迁移都在这里回写一次：① 老格式（任务 key 是随机生成的，不回写的话每次读都
    换 key，界面就改不动、删不掉）；② 时刻从全局一份搬进每个任务（不回写的话每次读都
    重新拿当前全局时刻去补，用户刚给某条任务单独设的时间会被全局那格盖掉）。
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
    need_time = any(isinstance(t, dict) and t.get("sub_ids") and not _norm_time(t.get("run_at"))
                    for t in raw)
    cfg = _blank()
    cfg.update(data)
    cfg["enabled"] = bool(cfg.get("enabled"))
    cfg["run_at"] = _norm_time(cfg.get("run_at")) or DEFAULT_RUN_AT
    if legacy or need_time:
        cfg["tasks"] = _migrate(raw, cfg["run_at"])
        save(cfg)
    else:
        cfg["tasks"] = [t for t in (_norm_task(x, cfg["run_at"]) for x in raw) if t]
    return cfg


def save(cfg: Dict) -> Dict:
    cfg = dict(cfg)
    cfg["run_at"] = _norm_time(cfg.get("run_at")) or DEFAULT_RUN_AT
    # 落盘前把每个任务规整一遍：少了一格 run_at 的任务下次读会被全局时刻补上，
    # 等于用户单独设的时间被顶掉，所以宁可在这里就写全
    cfg["tasks"] = [t for t in (_norm_task(x, cfg["run_at"]) for x in (cfg.get("tasks") or [])) if t]
    cfg["updated_at"] = datetime.now().strftime(TIME_FMT)
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再改名：直接 write_text 若正好被中断，会留下半截 JSON，
    # 下次 load() 一律回退成"没有任务"——整个账本凭空没了
    tmp = STORE_PATH.parent / (STORE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STORE_PATH)
    return cfg


# ── 读改写要排队 ──
# 界面保存与调度记账写的都是同一个文件。之前每个改动路由都是"先 load → 查一次数据库
# （可能等几秒）→ 整份 save"，中间只要调度器记了一笔账，就被这份旧快照冲掉：
# 任务看起来还是"到期"，于是同一批订阅紧接着又跑一轮（10-03 的 17:08 与 17:1x 两轮）。
# 现在两件事一起修：① 读改写全程持锁；② 界面只许改"意图"，账本字段以磁盘为准。
_MUTEX = threading.RLock()

# 界面拥有的是意图，调度拥有的是账本。字段按归属分开，谁也别覆盖谁。
INTENT_TOP = ("enabled", "run_at")
LEDGER_TASK = ("last_run", "last_status", "last_note")


def all_member_ids(cfg: Dict) -> List[int]:
    ids: List[int] = []
    for t in cfg.get("tasks") or []:
        for sid in t.get("sub_ids") or []:
            if sid not in ids:
                ids.append(sid)
    return ids


def save_intent(intent: Dict, alive=None) -> Dict:
    """把界面那份"意图"合进磁盘上最新的账本再落盘。

    任务**清单本身以意图为准**（要的就是"改成员/删任务"能落住），
    每个任务只覆盖 name / interval_days / sub_ids 这三样意图字段；
    last_run / last_status / last_note 一律保留磁盘上那份——
    调度器刚记的账不会因为界面点了两下就丢。alive 传集合时顺带清掉已删订阅。
    """
    with _MUTEX:
        cfg = load()
        src = intent or {}
        for k in INTENT_TOP:
            if k in src:
                cfg[k] = src[k]
        ledger = {t.get("key"): t for t in cfg.get("tasks") or [] if t.get("key")}
        tasks = []
        for one in (src.get("tasks") or []):
            t = dict(one or {})
            old = ledger.get(t.get("key"))
            for f in LEDGER_TASK:
                # 账本字段只认磁盘上那份，意图里带了也不看
                t[f] = (old or {}).get(f, "") if old else t.get(f, "")
            if old and not _norm_time(t.get("run_at")):
                # 意图里没带时刻（老前端或只改名的请求）就沿用磁盘上那条自己设的，
                # 别让它掉回全局那一格——那正是"改一条、三条一起挪"的病根
                t["run_at"] = old.get("run_at") or ""
            tasks.append(t)
        cfg["tasks"] = tasks
        if alive is not None:
            gone = [i for i in all_member_ids(cfg) if i not in alive]
            if gone:
                cfg = drop_subs(cfg, gone)
        return save(cfg)


def touch_running(keys: List[str], now: Optional[datetime] = None) -> Dict:
    """锁内读最新账本 → 把这一轮的时间戳占上 → 落盘。开跑前调用。"""
    with _MUTEX:
        cfg = load()
        save(mark_running(cfg, keys, now))
        return cfg


def finish_batch(keys: List[str], ok: bool, note: str,
                 now: Optional[datetime] = None) -> Dict:
    """锁内读最新账本 → 记这一轮的结果 → 落盘。跑完调用。"""
    with _MUTEX:
        cfg = load()
        save(mark_batch(cfg, keys, ok, note, now))
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


def new_task(cfg: Dict, sub_ids, interval_days: int = 1, name: str = "",
             run_at: str = "") -> Optional[Dict]:
    """新建任务（一组订阅一个任务）。任务数满了或没有效订阅返回 None。

    时刻不单独给就用全局那格当这条的初始值——存成任务自己的，之后改全局不会再动它。
    """
    if len(cfg.get("tasks") or []) >= MAX_TASKS:
        return None
    subs = _clean_ids(sub_ids)
    if not subs:
        return None
    t = {"key": _new_key(), "name": str(name or "").strip()[:NAME_MAX] or _default_name(subs),
         "sub_ids": subs, "interval_days": _norm_interval(interval_days),
         "run_at": task_run_at({"run_at": run_at}, cfg.get("run_at") or ""),
         "last_run": "", "last_status": "", "last_note": ""}
    cfg["tasks"].append(t)
    return t


def update_task(cfg: Dict, key: str, name=None, interval_days=None, sub_ids=None,
                run_at=None) -> Optional[Dict]:
    t = get_task(cfg, key)
    if not t:
        return None
    if name is not None:
        t["name"] = str(name).strip()[:NAME_MAX] or t["name"]
    if interval_days is not None:
        t["interval_days"] = _norm_interval(interval_days)
    if run_at is not None:
        n = _norm_time(run_at)
        if n:
            t["run_at"] = n    # 不合法就当没收到这格，界面按原值回显（路由已先挡过一道）
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


def task_run_at(task: Dict, run_at: str = "") -> str:
    """这条任务用哪个时刻：任务自己那格优先，没填才退回全局那格（全局只是新建任务的默认值）。"""
    return (_norm_time((task or {}).get("run_at"))
            or _norm_time(run_at) or DEFAULT_RUN_AT)


def next_due(task: Dict, run_at: str, now: datetime) -> Optional[datetime]:
    """下一次该跑的时间点 = **上次跑的那天 + 间隔**那一天的这个任务的执行时刻。

    原来写成 `max(上次时间 + 间隔, 当天时刻)`，等于"上次几点、下次就几点"：
    16:39 手动跑过一次，往后每天都变 16:39，用户设的时刻永远追不回来
    （用户报的"没按每天执行时刻执行"就是这个）。改成只认设定时刻之后，
    实际几点跑完、跑多久都不影响下一次。

    时刻取任务自己那格（`task_run_at`）：全局只有一份时，改一处三条任务的下次一起挪，
    而用户要的正是"微信 9:30、魔兽世界 10:00、AI 每 2 天 08:00"这样各跑各的。

    从没跑过的任务算"今天这个点"，哪怕现在已过点：刚建好任务，用户希望
    一分钟内就看到它跑一轮（这也是验证配置对不对最快的办法），而不是等到明天。
    """
    last = _parse(task.get("last_run") or "")
    hh, mm = [int(x) for x in task_run_at(task, run_at).split(":")]
    if not last:
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    day = last.date() + timedelta(days=int(task.get("interval_days") or 1))
    return datetime.combine(day, datetime.min.time()).replace(
        hour=hh, minute=mm, second=0, microsecond=0)


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


def mark_running(cfg: Dict, keys: List[str], now: Optional[datetime] = None) -> Dict:
    """开跑前先把 last_run 推到当下、状态记成 running。

    不这么做就有个窗口：一轮要跑几分钟，跑完才记账；这期间只要有一次
    界面保存配置把 JSON 重写掉（或进程被重启），记账就丢，任务看起来还是"到期"，
    于是同一个任务连着跑两轮——10-03 那次「魔兽世界 17:08 跑完紧接着又跑一轮、
    第二条整批都是"无新视频"」就是这么来的，对上游是纯骚扰。
    先占时间戳，最坏情况是这一轮没跑成、明天再跑，而不是今天重复跑。
    """
    now = now or datetime.now()
    stamp = now.strftime(TIME_FMT)
    touched = set(keys or [])
    for t in cfg.get("tasks") or []:
        if t.get("key") in touched:
            t["last_run"] = stamp
            t["last_status"] = "running"
            t["last_note"] = "这一轮正在跑…"
    cfg["updated_at"] = stamp
    return cfg


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
