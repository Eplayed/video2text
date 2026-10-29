# -*- coding: utf-8 -*-
"""plan_tasks.py — 排产任务 ↔ 素材 ↔ 整理稿的关联层（2026-10-20 第一单）。

为什么要有这一层：以前"这条素材有没有整理稿"是**前端猜出来的**——拉前 500 篇稿，
在 JS 里对 `source_video_ids` 那个 JSON **字符串**做 indexOf。id=12 会命中 "[112, 113]"，
第 501 篇以后的老稿永远看不见。而排产表里一条选题点进素材库、搜标题、轮询卡片、
再硬设成 `game_guide` 才生成——公众号的题也落进游戏攻略那一档。

这一层把三件事钉成数据：
  1. **一条排产 = 一篇内容要求**（slot_key + 渠道 + 内容形式），带标题/hook/note/排期；
  2. **本篇采用哪几条素材**（video_ids + 当时的证据快照 sources）——候选是候选、
     采用的是采用的，"重新查素材"只刷候选，不许动已开工版本的证据；
  3. **这一版稿是哪一篇**（summary_id + version）——重新生成开新版本、前稿留着。

三条边界（别扩错方向）：
  · **渠道 ≠ summary_type**：channel 与 form 是两个独立字段，summary_type 只是 form 解析出来的
    整理结构。同一素材可以服务不同选题，所以找稿按任务与精确素材 id，不按类型猜。
  · **本层只存关联**：排产条目本身（11 条静态 DATA）仍在 `web/static/schedule.html`，
    迁到服务端是第二单的事；这里存的 title/hook/note 是**当时的快照**，第二单迁移时以它为准回核。
  · **不推进发布状态**：整理成功不等于成稿质检通过，更不等于已发布。发布登记仍走
    `/api/ops/register`（`src/ops_store.py`），本层一个字的发布事实都不写。

并发防重靠数据库那一行，不靠内存：`claim_running()` 是一条带 `status != 'running'` 条件的
UPDATE，rowcount=0 就是别人已经占了——刷新页面、开第二个标签、两个 agent 同时点，都撞在同一行上。
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
import threading
import time
from typing import Any, Dict, List, Optional

# 渠道名单**不在这里复制一份**：key→label 从 content_store 的权威源借，
# 本单只开头条与公众号两条线（PRD §4：小红书现有产线保留但不在本次入口范围内）。
VALID_CHANNELS = ("toutiao", "wechat")

# 内容形式 → 整理结构（summary_type）。这一份映射只有这里有一个副本，
# 界面与后端都从这里问，别在 HTML 里再抄一遍。
#   toutiao/guide  头条攻略图文      → game_guide（唯一按 ## 小标题切块的那档）
#   toutiao/mix    头条其他形式（资讯整合）→ toutiao_mix
#   wechat/any     公众号素材档案     → wechat_material
FORMS = {
    ("toutiao", "guide"): "game_guide",
    ("toutiao", "mix"): "toutiao_mix",
    ("toutiao", "interview"): "ai_interview",
    ("wechat", "guide"): "wechat_material",
    ("wechat", "mix"): "wechat_material",
}
FORM_LABELS = {"guide": "攻略图文", "mix": "资讯整合", "interview": "面试题"}
DEFAULT_FORM = "guide"

# 任务状态：pending 只表示"选题与素材已确认、还没生成"；running/done/failed 是生成那一步的事实。
# 注意：**点进详情不写状态**（PRD 3.3），只有真的建了任务才有这一行。
STATUSES = ("pending", "running", "done", "failed", "aborted")

_LOCK = threading.Lock()
_READY = set()          # 已建过表的 db_path，省掉每次 CREATE TABLE 的开销


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _j(text, default):
    try:
        val = json.loads(text or "")
    except (TypeError, ValueError):
        return default
    return val if isinstance(val, type(default)) else default


def ensure_table(db_path):
    """建表（幂等）。每个入口先调它一次——不去改 content_store 的 init_db，少碰一份别人的热文件。"""
    key = os.path.abspath(db_path)
    if key in _READY:
        return
    with _LOCK:
        if key in _READY:
            return
        con = sqlite3.connect(db_path)
        try:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS plan_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_key TEXT NOT NULL,
                channel TEXT NOT NULL,
                form TEXT NOT NULL,
                title TEXT DEFAULT '',
                hook TEXT DEFAULT '',
                note TEXT DEFAULT '',
                plan_date TEXT DEFAULT '',
                video_ids TEXT DEFAULT '[]',
                sources TEXT DEFAULT '[]',
                supplement_ids TEXT DEFAULT '[]',
                supplement_sources TEXT DEFAULT '[]',
                summary_id INTEGER,
                version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT DEFAULT '',
                model TEXT DEFAULT '',
                attempt INTEGER NOT NULL DEFAULT 0,
                token TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(slot_key, channel, form, version)
            );
            CREATE INDEX IF NOT EXISTS idx_plan_tasks_summary
                ON plan_tasks(summary_id);
            """)
            con.commit()
            # R5（2026-10-20 Codex 复验点名）：进程被 kill/重启后，DB 里那一行永远停在 running，
            # 于是重试永远 409、界面永远转圈，而**没有任何东西在跑**。
            # 判据不需要租约表：这个函数是"这个库在本进程里第一次被用"，此刻内存里不可能有 job，
            # 所以库里所有 running 一律是上个进程留下的——标 aborted 并写清原因，让人自己决定要不要重试
            # （**不自动重跑**：重试就是再花一次钱，而上一版可能已经出了稿，得先让人核查产物）。
            cols = [r[1] for r in con.execute("PRAGMA table_info(plan_tasks)").fetchall()]
            for add, ddl in (("attempt", "ALTER TABLE plan_tasks ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0"),
                             ("token", "ALTER TABLE plan_tasks ADD COLUMN token TEXT DEFAULT ''"),
                             ("supplement_ids",
                              "ALTER TABLE plan_tasks ADD COLUMN supplement_ids TEXT DEFAULT '[]'"),
                             ("supplement_sources",
                              "ALTER TABLE plan_tasks ADD COLUMN supplement_sources TEXT DEFAULT '[]'")):
                if cols and add not in cols:      # 老库（第一单当天建的那版）补列，幂等
                    con.execute(ddl)
            cur = con.execute(
                "UPDATE plan_tasks SET status='aborted', updated_at=?,"
                " error='上次进程中断时这一版还在跑，结果没有记下来。"
                "先回素材库看有没有多出一篇稿子，再决定要不要重试。'"
                " WHERE status='running'", (_now(),))
            if cur.rowcount:
                print("[plan_tasks] 中断核对：%d 个 running 任务标成 aborted" % cur.rowcount)
            con.commit()
        finally:
            con.close()
        _READY.add(key)


def resolve_summary_type(channel, form):
    """渠道 + 内容形式 → summary_type。认不出来返回 None（调用方必须报错，不许悄悄落到默认档）。

    这里刻意**不给默认值**：以前 prepareGenerate 把什么都写成 game_guide，
    公众号的题就这么进了游戏攻略那一档。宁可报错让人选。
    """
    return FORMS.get((channel, form))


def channel_label(channel):
    try:
        from . import content_store as cs
        return (cs.RADAR_CHANNEL_STRATEGY.get(channel) or {}).get("label") or channel
    except Exception:
        return channel


def form_label(form):
    return FORM_LABELS.get(form) or form


def is_started(task):
    """这一版是不是已经开过工（开过工的证据就是只读的：改走它等于篡改历史稿的依据）。

    failed 要看有没有 summary_id：落了稿的失败版，稿子还在，证据不能跟着改。
    """
    if not task:
        return False
    return task.get("status") in ("running", "done") or (
        task.get("status") == "failed" and task.get("summary_id"))


def _row_to_dict(row):
    d = dict(row)
    d["video_ids"] = _j(d.get("video_ids"), [])
    d["sources"] = _j(d.get("sources"), [])
    d["supplement_ids"] = _j(d.get("supplement_ids"), [])
    d["supplement_sources"] = _j(d.get("supplement_sources"), [])
    return d


def _latest_row(con, slot_key, channel, form):
    """当前版本 = 版本号最大的那条。放在这是因为事务里必须用**同一条连接**读：
    写锁还开着时另起一条连接去读，DELETE 日志模式下会直接 database is locked。"""
    return con.execute(
        "SELECT * FROM plan_tasks WHERE slot_key=? AND channel=? AND form=?"
        " ORDER BY version DESC LIMIT 1", (slot_key, channel, form)).fetchone()


def _task_row(con, task_id):
    return con.execute("SELECT * FROM plan_tasks WHERE id=?", (task_id,)).fetchone()


def find_task(db_path, slot_key, channel, form):
    """这一题这一渠道这一形式的**当前版本**（版本号最大的那条）。没有返回 None。"""
    ensure_table(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = _latest_row(con, slot_key, channel, form)
    finally:
        con.close()
    return _row_to_dict(row) if row else None


def get_task(db_path, task_id):
    ensure_table(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = _task_row(con, task_id)
    finally:
        con.close()
    return _row_to_dict(row) if row else None


def task_of_summary(db_path, summary_id):
    """这篇整理稿挂在哪一格（取 version 最大的那条任务）。没有则 None。

    为什么要有它：2026-10-27 号主把公众号那条流程收成"排产格三步竖线"，
    整理稿页那两颗入口要收掉——但库里 9 篇 wechat_material 全是从素材库那条路生成的、
    **没挂任何排产格**。不看一眼就收按钮，等于把这 9 篇锁死在没法生成图文的地方。
    所以详情接口带这个字段回来：有格 → 界面给「去这一格处理」；没格 → 原入口留着并写明原因。
    """
    if not summary_id:
        return None
    ensure_table(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM plan_tasks WHERE summary_id=? "
            "ORDER BY CAST(version AS INTEGER) DESC, id DESC LIMIT 1",
            (int(summary_id),)).fetchone()
    finally:
        con.close()
    return _row_to_dict(row) if row else None


def _snapshot(video):
    """素材证据快照：把"当时看到的那一行"钉下来。

    为什么要快照：素材行以后可能被改标题、改作者、甚至被删；已开工版本的依据不能跟着变，
    否则回查时指着一条已经不一样的素材说"你当时用的就是它"。
    """
    return {"db_id": int(video["id"]),
            "sheet": video.get("source_sheet") or "",
            "row": video.get("source_row"),
            "title": (video.get("title") or "")[:120],
            "author": (video.get("author") or "")[:60],
            # 字段名按 videos 表实际有的来：这库里是 published_at，没有 publish_date/create_date。
            # 写错不会报错，只会让证据快照里的日期永远是空——回查时看不出一条素材是什么时候的。
            "date": (video.get("published_at") or video.get("publish_date") or "")[:20],
            "has_transcript": bool((video.get("transcript") or "").strip())}


def upsert_task(db_path, slot_key, channel, form, meta, videos, con=None):
    """建任务或更新它采用的素材清单（**只在 version 的当前行上改，不新建版本**）。

    videos 是 content_store 取出来的素材行（按 DB 主键，不是 sheet:row）。
    返回 (task_dict, created_bool)。
    `con` 给了就在调用方的写事务里执行、**不自己 commit/close**（T1：save 与 claim 不许交错）。
    """
    if con is None:
        ensure_table(db_path)
    ids = [int(v["id"]) for v in videos]
    srcs = [_snapshot(v) for v in videos]
    now = _now()
    own = con is None
    if own:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
    try:
        row = _latest_row(con, slot_key, channel, form)
        if row:
            con.execute(
                "UPDATE plan_tasks SET video_ids=?, sources=?, title=?, hook=?, note=?,"
                " plan_date=?, updated_at=? WHERE id=?",
                (json.dumps(ids), json.dumps(srcs, ensure_ascii=False),
                 meta.get("title") or row["title"], meta.get("hook") or row["hook"],
                 meta.get("note") or row["note"], meta.get("date") or row["plan_date"],
                 now, row["id"]))
            if own:
                con.commit()
            tid = row["id"]
            created = False
        else:
            try:
                cur2 = con.execute(
                    "INSERT INTO plan_tasks (slot_key, channel, form, title, hook, note, plan_date,"
                    " video_ids, sources, version, status, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,1,'pending',?,?)",
                    (slot_key, channel, form, meta.get("title") or "", meta.get("hook") or "",
                     meta.get("note") or "", meta.get("date") or "",
                     json.dumps(ids), json.dumps(srcs, ensure_ascii=False), now, now))
                if own:
                    con.commit()
                tid = cur2.lastrowid
                created = True
            except sqlite3.IntegrityError:
                # 两个线程/两个标签同时投同一题：UNIQUE(slot_key,channel,form,version) 撞了。
                # **这不是错误，正是防重要靠的那一下**——改读对方刚插好的那行按"已存在"返回，
                # 谁先抢到 running 谁生成，另一个在 claim_running 那里拿到 False。
                if own:
                    con.rollback()
                row = _latest_row(con, slot_key, channel, form)
                tid = row["id"]
                created = False
        out = _task_row(con, tid)
    finally:
        if own:
            con.close()
    return _row_to_dict(out), created


def _ids_srcs(videos):
    """T3：`videos=None` 表示"继承这一版已存的清单"，`[]` 表示**明确清空**。

    以前两处都写成 `if videos else`，于是"取消最后一条采用"传进来的空列表被当成 falsy，
    直接把旧素材复制回新版本——新版本该是空的，却带着上一版的证据。
    """
    if videos is None:
        return None, None
    return [int(v["id"]) for v in videos], [_snapshot(v) for v in videos]


def save_sources(db_path, slot_key, channel, form, meta, videos):
    """保存"本篇采用哪几条素材"。**已开工的版本是只读的**（R1）。

    规则（一次说清，别在调用点各判一遍）：
      · 没有任务 → 建 version 1（pending），随便写；
      · 最新版还没开工（pending / aborted / failed 且没有 summary_id）→ 就地改，版本号不动；
      · 最新版已开工（running / done，或 failed 但已经落了稿）→ **绝不改它**：
        采用清单变了就开一个新版本当草稿，旧那版的 video_ids/sources 原样冻住。
        否则 v1 的证据会被 v2 的请求悄悄改掉，而 v1.summary_id 还指着素材 A 的稿。
      · 清单没变 → 什么都不写（避免每次点「采用」都涨一个版本）。
    返回 (task_dict, action)，action ∈ created|updated|versioned|unchanged。
    """
    ensure_table(db_path)
    ids = [int(v["id"]) for v in videos]
    # T1（Codex 第二次复验）：**读—判—写必须在同一个写事务里**。以前这里先另起一条连接读
    # 最新版、判完再交给 upsert 写，中间正好能被另一个请求占锁：读到 pending → 对方 claim
    # 成 running → 这里照样把 pending 那行的清单改了，于是"正在生成的那一版"证据被改走。
    # BEGIN IMMEDIATE 让这一段对写者串行，判完当场写，写完才放锁。
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN IMMEDIATE")
        row = _latest_row(con, slot_key, channel, form)
        if row is None:
            task, _created = upsert_task(db_path, slot_key, channel, form, meta, videos, con=con)
            con.commit()
            return task, "created"
        latest = _row_to_dict(row)
        same = list(latest.get("video_ids") or []) == ids
        started = is_started(latest)
        if same and (started or not (meta.get("title") or meta.get("hook"))):
            con.rollback()
            return latest, "unchanged"
        if started:
            new = new_version(db_path, latest["id"], videos, con=con)
            con.commit()
            return new, "versioned"
        task, _created = upsert_task(db_path, slot_key, channel, form, meta, videos, con=con)
        con.commit()
        return task, "updated"
    finally:
        con.close()


def save_supplements(db_path, task_id, videos, why_by_id=None):
    """勾定「补充资料」——挂在已采用视频下面的非视频料（微信文章/暴雪公告/NGA 帖）。

    与 `save_sources` 两处刻意不同：
    1. **开没开工都能改**（号主 2026-10-28 点名取消"已开工就冻结"）。理由：补充料只是给下一次
       生成看的配角上下文，为换一条参考料去开一版、再花一次模型钱不划算；已出的那篇稿不会因此变，
       界面上把"改了要重新生成才会带上"这句说清楚就行。
    2. 没有采用任何视频素材时**不接受**补充料——补充料的定位是"让主素材那份证据更完整"，
       没有主素材它就没有归属，也不该单独成稿。
    `why_by_id`：{素材id: "为什么算相关"}，一起冻进快照，事后能回查当初凭什么勾它。
    返回 (task_dict, action)，action ∈ updated|unchanged|no-main|missing。
    """
    ensure_table(db_path)
    why_by_id = why_by_id or {}
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN IMMEDIATE")
        row = _task_row(con, task_id)
        if row is None:
            con.rollback()
            return None, "missing"
        task = _row_to_dict(row)
        if not (task.get("video_ids") or []):
            con.rollback()
            return task, "no-main"
        ids = [int(v["id"]) for v in videos]
        if list(task.get("supplement_ids") or []) == ids:
            con.rollback()
            return task, "unchanged"
        snaps = []
        for v in videos:
            snap = _snapshot(v)
            snap["role"] = "补充资料"
            snap["why"] = (why_by_id.get(int(v["id"])) or why_by_id.get(str(v["id"])) or "")[:120]
            snaps.append(snap)
        con.execute("UPDATE plan_tasks SET supplement_ids=?, supplement_sources=?, updated_at=?"
                    " WHERE id=?", (json.dumps(ids),
                                    json.dumps(snaps, ensure_ascii=False), _now(), task["id"]))
        con.commit()
        return _row_to_dict(_task_row(con, task["id"])), "updated"
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def new_version(db_path, task_id, videos=None, con=None):
    """显式重新生成才走这里：复制一份 version+1 的新行，**前稿那行原样留着**。

    `con` 给了就在调用方的写事务里执行、不自己 commit/close（与 upsert_task 同一套规矩）。
    """
    own = con is None
    if own:
        ensure_table(db_path)
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
    row = _task_row(con, task_id)
    base = _row_to_dict(row) if row else None
    if not base:
        if own:
            con.close()
        return None
    now = _now()
    ids, srcs = _ids_srcs(videos)
    if ids is None:
        ids, srcs = base["video_ids"], base["sources"]
    want = int(base["version"]) + 1
    try:
        cur = con.execute(
            "INSERT INTO plan_tasks (slot_key, channel, form, title, hook, note, plan_date,"
            " video_ids, sources, version, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?, 'pending', ?, ?)",
            (base["slot_key"], base["channel"], base["form"], base["title"], base["hook"],
             base["note"], base["plan_date"], json.dumps(ids), json.dumps(srcs, ensure_ascii=False),
             want, now, now))
        if own:
            con.commit()
        out = _task_row(con, cur.lastrowid)
    except sqlite3.IntegrityError:
        # 两个人同时点「重新生成」：version+1 撞 UNIQUE。不报错也不重复建版——
        # 回读对方刚建好的那一版，调用方随后在 claim_running 那里分胜负。
        if own:
            con.rollback()
        out = con.execute(
            "SELECT * FROM plan_tasks WHERE slot_key=? AND channel=? AND form=? AND version=?",
            (base["slot_key"], base["channel"], base["form"], want)).fetchone()
    finally:
        if own:
            con.close()
    return _row_to_dict(out) if out else None


def claim_running(db_path, task_id):
    """占锁：抢到返回本次 attempt 的 token，抢不到返回 None。

    T2（Codex 第二次复验）：条件从 `status != 'running'` 收紧成
    **`status IN (pending,failed,aborted) AND summary_id IS NULL`**。
    老条件有个洞：A 读完状态后停在占锁之前，B 占锁并跑完（done），A 再执行时
    `done != running` 仍成立 → 同一个 task 被跑第二次、模型被叫两次。
    加上 `summary_id IS NULL` 后"已经出过稿的那一版"永远抢不到锁。

    token 用来收尾：`finish(token=...)` 只允许同一次尝试改自己的行，
    迟到的旧 worker 不能覆盖新一次的结果。
    这个函数是并发测试的**确定性挂点**（repro 会 monkeypatch 它），别改名别内联。
    """
    ensure_table(db_path)
    token = "%d-%d-%s" % (os.getpid(), int(time.time() * 1000), uuid.uuid4().hex[:8])
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "UPDATE plan_tasks SET status='running', error='', attempt=attempt+1, token=?,"
            " updated_at=? WHERE id=? AND status IN ('pending','failed','aborted')"
            " AND summary_id IS NULL", (token, _now(), task_id))
        con.commit()
        return token if cur.rowcount else None
    finally:
        con.close()


def prepare_run(db_path, slot_key, channel, form, meta, requested_ids,
                regenerate=False, base_task_id=None, base_version=None):
    """T1：在一个写事务里"读当前版 → 判能不能跑 → 定这一版用哪几条素材"。

    返回 dict：`{"decision": ..., "task": ..., "reason": ...}`，decision ∈
      need_claim  拿到 task，调用方随后 claim_running(task.id) 才开跑
      continue    这一版已有稿，直接回去（一次模型都不调）
      running     这一版正在跑
      stale       **请求带的采用清单与库里那一版不一致**，或指向的不是最新版
      blocked     这一版没有可用素材
    为什么要有这个函数：以前生成那一路"先 upsert 再判"，pending 版本上带着一份
    没保存的清单也能直接开跑，于是 task.video_ids 记的是 A、稿子是用 B 生成的
    （复现：存 [12]、生成 [112] → task=[12] 而 summary.source_video_ids=[112]）。
    现在**素材只认库里那一版**，请求带的清单只当校验用，不一致就 409。
    `BEGIN IMMEDIATE` 让"读+判"这一段对写者是串行的，避免 save 与 claim 交错。
    """
    ensure_table(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM plan_tasks WHERE slot_key=? AND channel=? AND form=?"
            " ORDER BY version DESC LIMIT 1", (slot_key, channel, form)).fetchone()
        if row is None:
            if not requested_ids:
                con.rollback()
                return {"decision": "blocked", "task": None, "reason": "还没确认采用任何素材"}
            cur = con.execute(
                "INSERT INTO plan_tasks (slot_key, channel, form, title, hook, note, plan_date,"
                " video_ids, sources, version, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,1,'pending',?,?)",
                (slot_key, channel, form, meta.get("title") or "", meta.get("hook") or "",
                 meta.get("note") or "", meta.get("date") or "", json.dumps(list(requested_ids)),
                 "[]", _now(), _now()))
            con.commit()
            row = con.execute("SELECT * FROM plan_tasks WHERE id=?", (cur.lastrowid,)).fetchone()
            return {"decision": "need_claim", "task": _row_to_dict(row)}
        task = _row_to_dict(row)
        # 迟到的旧请求不许当成"对最新版再重生成一次"：要么它指向的就是当前版，要么拒掉
        if base_task_id and int(task["id"]) != int(base_task_id):
            con.rollback()
            return {"decision": "stale", "task": task,
                    "reason": "这一题已经翻到第 %s 版了，你点的是旧那一版" % task["version"]}
        if base_version is not None and int(task["version"]) != int(base_version):
            con.rollback()
            return {"decision": "stale", "task": task,
                    "reason": "版本对不上（请求 v%s，当前 v%s）" % (base_version, task["version"])}
        if requested_ids is not None and list(task.get("video_ids") or []) != list(requested_ids):
            con.rollback()
            return {"decision": "stale", "task": task,
                    "reason": "请求带的采用清单与这一版存的不一致，先在排产表点「采用」保存"}
        if task["status"] == "running":
            con.rollback()
            return {"decision": "running", "task": task}
        if task.get("summary_id") and task["status"] == "done" and not regenerate:
            con.rollback()
            return {"decision": "continue", "task": task}
        if task.get("summary_id") and regenerate:
            want = int(task["version"]) + 1
            try:
                con.execute(
                    "INSERT INTO plan_tasks (slot_key, channel, form, title, hook, note,"
                    " plan_date, video_ids, sources, version, status, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?, 'pending', ?, ?)",
                    (task["slot_key"], task["channel"], task["form"], task["title"],
                     task["hook"], task["note"], task["plan_date"],
                     json.dumps(task.get("video_ids") or []),
                     json.dumps(task.get("sources") or [], ensure_ascii=False),
                     want, _now(), _now()))
                con.commit()
                row = con.execute(
                    "SELECT * FROM plan_tasks WHERE slot_key=? AND channel=? AND form=?"
                    " AND version=?", (slot_key, channel, form, want)).fetchone()
                return {"decision": "need_claim", "task": _row_to_dict(row)}
            except sqlite3.IntegrityError:
                con.rollback()
                return {"decision": "stale", "task": task,
                        "reason": "已经有人把下一版开出来了（同一版只许开一次）"}
        if not (task.get("video_ids") or []):
            con.rollback()
            return {"decision": "blocked", "task": task, "reason": "这一版没有采用任何素材"}
        con.commit()          # 只读判定也要收尾，别把写锁留在连接上
        return {"decision": "need_claim", "task": task}
    finally:
        con.close()


def finish(db_path, task_id, status, summary_id=None, error="", model="", token=None):
    """收尾。status 只认 STATUSES 里那几个值，写错要当场炸——别把 failed 写成 done。

    给了 `token` 就只允许**同一次尝试**改自己那一行：迟到的旧 worker 跑完时
    行上的 token 已经是新一次的了，rowcount=0，不会把人家的好结果盖成 failed。
    """
    if status not in STATUSES:
        raise ValueError("status 只能是 %s，收到 %r" % ("/".join(STATUSES), status))
    ensure_table(db_path)
    con = sqlite3.connect(db_path)
    try:
        sql = ("UPDATE plan_tasks SET status=?, summary_id=COALESCE(?, summary_id), error=?,"
               " model=?, updated_at=? WHERE id=?")
        args = [status, summary_id, (error or "")[:500], model, _now(), task_id]
        if token:
            sql += " AND token=?"
            args.append(token)
        cur = con.execute(sql, args)
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def tasks_for_video(db_path, video_id):
    """这条素材被哪些排产任务采用过（精确匹配，不用字符串子串）。"""
    ensure_table(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, slot_key, channel, form, version, status, summary_id, plan_date, title"
            " FROM plan_tasks"
            " WHERE json_valid(video_ids)"
            "   AND EXISTS (SELECT 1 FROM json_each(plan_tasks.video_ids) WHERE value = ?)"
            " ORDER BY updated_at DESC", (int(video_id),)).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def tasks_for_slot(db_path, slot_key):
    """这一题在两个渠道各自的任务（同题双渠道要能同时看见，不许互相顶掉）。"""
    ensure_table(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM plan_tasks WHERE slot_key=? ORDER BY channel, version DESC",
            (slot_key,)).fetchall()
    finally:
        con.close()
    out = [_row_to_dict(r) for r in rows]
    # R2：接口直接标出每个 (渠道,形式) 组的**当前版**（version 最大那条）。
    # 以前前端 forEach 覆写 hit，[2,1] 这种顺序最后停在 v1——继续的是旧稿、
    # 新版在跑还是在失败全看不见。判据交给服务端，不靠遍历顺序。
    top = {}
    for r in out:
        k = (r["channel"], r["form"])
        r["is_current"] = top.get(k, -1) < int(r["version"])
        if int(r["version"]) > top.get(k, -1):
            top[k] = int(r["version"])
    return out
