# -*- coding: utf-8 -*-
"""自动同步任务的调度与并发写回归（纯本地，不碰真配置、不碰上游）。

改了什么必须跑它：`next_due` 的时刻口径（时刻存在任务自己身上，全局那格只是新任务默认值）、
界面保存与调度记账的并发写。
这两处坏掉的表象都是"没按设定时间跑"或"同一批订阅连着跑两轮"，
而后者会去打上游（微信侧尤其敏感），所以不能只靠肉眼看界面。

用法：python3 scripts/e2e_sync_tasks.py      末行 ALL_OK 才算通过
"""
import copy
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import sync_tasks as st  # noqa: E402

NOW = datetime(2026, 10, 4, 8, 31, 0)
FAILS = []


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + detail) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + detail) if detail else ""))
        FAILS.append(name)


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


def task(key="t1", name="每天", interval=1, last="2026-10-03 16:39:00", subs=(1,),
         run_at=None):
    t = {"key": key, "name": name, "sub_ids": list(subs), "interval_days": interval,
         "last_run": last, "last_status": "ok" if last else "", "last_note": ""}
    if run_at:
        t["run_at"] = run_at
    return t


def main():
    tmp = tempfile.mkdtemp(prefix="sync_tasks_e2e_")
    real = st.STORE_PATH
    st.STORE_PATH = Path(tmp) / "sync_tasks.json"
    try:
        print("== A 时刻口径：任务没自己那格时退回全局（I 段测的是各存一份）==")
        t = task()
        for run_at, want in (("08:30", "2026-10-04 08:30"), ("21:00", "2026-10-04 21:00"),
                             ("06:00", "2026-10-04 06:00"), ("09:00", "2026-10-04 09:00")):
            got = fmt(st.next_due(t, run_at, NOW))
            ck("run_at=%s → 下次 %s" % (run_at, want), got == want, "实得 " + got)
        ck("设成 06:00（已过点）算到期，会补跑", st.is_due(t, "06:00", NOW) is True)
        ck("设成 09:00（还没到）不算到期", st.is_due(t, "09:00", NOW) is False)

        print("== B 改了「频率」立刻生效 ==")
        base = task(last="2026-10-04 08:56:00")
        for iv, want in ((1, "2026-10-05 08:30"), (2, "2026-10-06 08:30"),
                         (3, "2026-10-07 08:30"), (7, "2026-10-11 08:30")):
            got = fmt(st.next_due(dict(base, interval_days=iv), "08:30", NOW))
            ck("每 %d 天 → 下次 %s" % (iv, want), got == want, "实得 " + got)
        old = task(last="2026-10-03 17:02:00", interval=2)
        ck("每 2 天改成每天：今天 08:30 这档就该跑", st.is_due(dict(old, interval_days=1), "08:30", NOW))
        ck("维持每 2 天时不该跑（下次是 10-05 08:30）",
           not st.is_due(old, "08:30", NOW)
           and fmt(st.next_due(old, "08:30", NOW)) == "2026-10-05 08:30")
        ck("非法频率值回落每天", st._norm_interval(5) == 1 and st._norm_interval("x") == 1)

        print("== C 实际跑完的时间不影响下一次 ==")
        late = task(last="2026-10-03 23:58:11")
        ck("上次 23:58 跑完，下次仍是 08:30",
           fmt(st.next_due(late, "08:30", NOW)) == "2026-10-04 08:30")
        never = task(last="")
        ck("从没跑过的算今天这个点（建好就能看见它跑一轮）",
           fmt(st.next_due(never, "08:30", NOW)) == "2026-10-04 08:30"
           and st.is_due(never, "08:30", NOW))

        print("== D 占账：同一轮不会被连着触发两次 ==")
        cfg = {"enabled": True, "run_at": "08:30", "tasks": [task("t1"), task("t2", "每2天", 2,
                                                               "2026-10-02 17:02:00")]}
        st.save(cfg)
        due = st.due_tasks(st.load(), NOW)
        ck("08:31 两个任务都到期", sorted(t["key"] for t in due) == ["t1", "t2"],
           str([t["key"] for t in due]))
        st.touch_running([t["key"] for t in due])
        after = st.due_tasks(st.load(), datetime(2026, 10, 4, 8, 32, 0))
        ck("占账后下一分钟不再到期（这就是 10-03 连着跑两轮的病根）", after == [], str(after))
        got = st.load()
        ck("占账写的是 running 状态", all(t["last_status"] == "running" for t in got["tasks"]))
        st.finish_batch(["t1", "t2"], True, "✅ 同步完成")
        done = st.load()
        ck("跑完记账覆盖成 ok", all(t["last_status"] == "ok" for t in done["tasks"]))
        ck("当天剩下的时间都不再到期",
           all(not st.due_tasks(st.load(), datetime(2026, 10, 4, h, 0), None) for h in (9, 16, 23)))
        nxt = sorted(t["key"] for t in st.due_tasks(st.load(), datetime(2026, 10, 5, 8, 31), None))
        ck("次日 08:31 每天那条又到期、每 2 天那条还没", nxt == ["t1"], str(nxt))

        print("== E 界面保存不会把调度记的账冲掉 ==")
        stale = st.load()                      # 模拟路由：先读一份
        stale["tasks"][0]["name"] = "改个名字"   # 界面上改了名
        st.finish_batch(["t1"], True, "调度器刚记的一笔")   # 期间调度器落了账
        st.save_intent(stale, alive=None)      # 界面这份旧快照后落盘
        now_cfg = st.load()
        t1 = st.get_task(now_cfg, "t1")
        ck("账没被旧快照冲掉（last_note 是调度器那份）",
           t1.get("last_note") == "调度器刚记的一笔", repr(t1.get("last_note")))
        ck("界面改的名字落住了", t1.get("name") == "改个名字")
        ck("意图带着的旧账字段不会反向覆盖磁盘",
           t1.get("last_status") == "ok")

        print("== F 清单以意图为准：删得掉、加得进、成员改得动 ==")
        cfg = st.load()
        two = copy.deepcopy(cfg)
        two["tasks"] = [t for t in two["tasks"] if t["key"] != "t2"]
        st.save_intent(two)
        ck("删掉的任务真的没了", st.get_task(st.load(), "t2") is None
           and len(st.load()["tasks"]) == 1)
        add = copy.deepcopy(st.load())
        add["tasks"].append(task("t9", "新任务", 1, ""))
        st.save_intent(add)
        t9 = st.get_task(st.load(), "t9")
        ck("新建任务带进来且没有假账", t9 is not None and not t9.get("last_run"))
        mem = copy.deepcopy(st.load())
        for x in mem["tasks"]:
            if x["key"] == "t9":
                x["sub_ids"] = [7, 8]
        st.save_intent(mem)
        ck("成员改动能落住", st.get_task(st.load(), "t9")["sub_ids"] == [7, 8])
        st.save_intent(st.load(), alive={1, 2, 3})     # 7、8 这两个订阅已被删
        ck("已删订阅从成员里清掉、空任务一并删除",
           st.get_task(st.load(), "t9") is None)

        print("== G 坏文件不毁账本 ==")
        keep = st.load()
        st.STORE_PATH.write_text("{半截 J", encoding="utf-8")
        ck("JSON 坏了回退成空配置而不是抛错", st.load().get("tasks") == [])
        st.save(keep)
        ck("重新落盘能恢复", len(st.load()["tasks"]) == len(keep["tasks"]))
        st.save_intent(st.load())
        ck("原子写不留 .tmp 文件", not list(Path(tmp).glob("*.tmp")))

        print("== H 老格式迁移（每条订阅一个任务 → 一组一个任务）==")
        legacy = {"enabled": True, "run_at": "09:30", "tasks": [
            {"key": "a", "sub_id": 11, "interval_days": 1, "last_run": "2026-10-01 09:30:00",
             "last_status": "ok", "last_note": "x"},
            {"key": "b", "sub_id": 12, "interval_days": 1},
            {"key": "c", "sub_id": 13, "interval_days": 2}]}
        st.STORE_PATH.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        migrated = st.load()
        again = st.load()
        ck("按频率并成两个任务", [t["interval_days"] for t in migrated["tasks"]] == [1, 2]
           and sorted(migrated["tasks"][0]["sub_ids"]) == [11, 12])
        ck("迁移结果回写落盘（两次读出来的 key 一致）",
           [t["key"] for t in migrated["tasks"]] == [t["key"] for t in again["tasks"]])
        print("== I 时刻按任务各存一份（改一条不挪动别条）==")
        three = {"enabled": True, "run_at": "09:30", "tasks": [
            task("wa", "微信 5 个号", 1, "2026-10-03 09:30:00", (1,), "09:30"),
            task("wb", "魔兽世界", 1, "2026-10-03 09:30:00", (2,), "10:00"),
            task("wc", "AI", 2, "2026-10-02 09:30:00", (3,), "08:00")]}
        st.save(three)
        cfg = st.load()

        def nxt(key):
            return fmt(st.next_due(st.get_task(cfg, key), cfg["run_at"], NOW))

        ck("三条各自时刻 → 下次分别是 09:30 / 10:00 / 08:00",
           [nxt("wa"), nxt("wb"), nxt("wc")] == ["2026-10-04 09:30", "2026-10-04 10:00",
                                                 "2026-10-04 08:00"],
           "%s / %s / %s" % (nxt("wa"), nxt("wb"), nxt("wc")))
        # 顶上那格现在只是"新任务默认时刻"：改它不该把已有三条一起带走
        moved = copy.deepcopy(cfg)
        moved["run_at"] = "21:00"
        st.save_intent(moved, alive=None)
        cfg2 = st.load()
        ck("改全局默认时刻，已有三条的下次一个都不动",
           [st.get_task(cfg2, k)["run_at"] for k in ("wa", "wb", "wc")]
           == ["09:30", "10:00", "08:00"],
           str([t["run_at"] for t in cfg2["tasks"]]))
        ck("全局那格落盘成了 21:00（下一条新任务用它）", cfg2["run_at"] == "21:00")
        one = copy.deepcopy(cfg2)
        st.update_task(one, "wb", run_at="11:15")
        st.save_intent(one, alive=None)
        cfg3 = st.load()
        ck("只改「魔兽世界」到 11:15，另外两条不变",
           [st.get_task(cfg3, k)["run_at"] for k in ("wa", "wb", "wc")]
           == ["09:30", "11:15", "08:00"],
           str([t["run_at"] for t in cfg3["tasks"]]))
        ck("改时刻非法（25:99）不动原值",
           st.update_task(cfg3, "wa", run_at="25:99")["run_at"] == "09:30")
        at931 = datetime(2026, 10, 4, 9, 31, 0)
        due = sorted(t["key"] for t in st.due_tasks(st.load(), at931, {1, 2, 3}))
        ck("09:31 只有 09:30 与 08:00 那两条到期，11:15 那条还没",
           due == ["wa", "wc"], str(due))
        new_t = st.new_task(st.load(), [9], 1, "新建", run_at="18:40")
        ck("新建任务带时刻就用它自己的", new_t["run_at"] == "18:40")
        new_t2 = st.new_task(st.load(), [10], 1, "新建2")
        ck("新建任务不给时刻 → 拿全局那格当自己的默认", new_t2["run_at"] == "21:00")
        # 意图里没带 run_at（老前端 / 只改名的请求）不能把磁盘上单独设的时刻掉回全局
        partial = st.load()
        for x in partial["tasks"]:
            x.pop("run_at", None)
        st.save_intent(partial, alive=None)
        ck("意图没带时刻时沿用磁盘那份，不会退回全局",
           [st.get_task(st.load(), k)["run_at"] for k in ("wa", "wb", "wc")]
           == ["09:30", "11:15", "08:00"],
           str([t["run_at"] for t in st.load()["tasks"]]))

        print("== J 老配置（时刻只有全局一份）读一次就补齐并回写 ==")
        old = {"enabled": True, "run_at": "07:20", "tasks": [
            {"key": "o1", "name": "老任务", "sub_ids": [5], "interval_days": 1,
             "last_run": "", "last_status": "", "last_note": ""}]}
        st.STORE_PATH.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        first = st.load()
        ck("补齐成任务自己那格", first["tasks"][0]["run_at"] == "07:20",
           repr(first["tasks"][0].get("run_at")))
        # 补过一次后，再改全局不该把这条的时刻带走（迁移只在缺那格时发生一次）
        first["run_at"] = "13:00"
        st.save(first)
        ck("迁移已回写：之后改全局，这条仍是 07:20",
           st.load()["tasks"][0]["run_at"] == "07:20",
           repr(st.load()["tasks"][0].get("run_at")))
        raw = json.loads(st.STORE_PATH.read_text(encoding="utf-8"))
        ck("回写真的落了盘（文件里能看到 run_at）", raw["tasks"][0].get("run_at") == "07:20")

    finally:
        st.STORE_PATH = real
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print("FAIL %d 项：%s" % (len(FAILS), "、".join(FAILS)))
        return 1
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
