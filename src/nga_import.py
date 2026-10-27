# -*- coding: utf-8 -*-
"""nga_import.py — 把 wow-forever 那条 launchd 采集的 NGA 快照导进本仓库素材库。

为什么加这一层（2026-10-27 号主拍板）：NGA 原先有两套采集器各自为政——
wow-forever 的 launchd 每天 08:40/20:40 抓整块板块落 `~/.nga/data/`（累计 1104 个 tid），
而本仓库的 `nga_forum.py` 自己连 bbs.nga.cn 抓、没接界面也没定时（库里只有 10 条）。
同一个板块两处抓会撞 PHPWind 的登录态风控，且两边的数对不上。现在定成一条线：
**采集只有 launchd 那一份，本文件只负责把它的产物读进来**，`nga_forum.py` 退回手动救急。

不重造写入口径：去重键、字段形态、remark 的用途标注全部复用 `nga_forum`
（`post_id()` 算的 `nf`+md5(url)[:16]、`insert()` 的列与「正文空记未开始」），
实测两条线写出的 url 形态完全一致（`https://bbs.nga.cn/read.php?tid=…`），
库里原有 10 条有 9 条就在快照里，所以导入不会分裂成两批同帖不同键的行。

状态文件 `output/nga_import_state.json` 只记「上次导到哪个快照」，不是数据副本：
判重永远以库里的 aweme_id 为准，所以重复跑是安全的（连跑两次新增 0）。

Python 3.9 兼容：不用 match / X|Y 语法。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nga_forum  # 复用 post_id / _clean / insert / SHEET，别处改口径这边跟着走

DATA_DIR_DEFAULT = os.path.expanduser("~/.nga/data")
BOARD_GLOB = "board-*.json"
STATE_NAME = "nga_import_state.json"
LOG_DIR_NAME = "logs"
LOG_NAME = "nga_import.log"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def board_files(data_dir: str) -> List[str]:
    """快照按文件名里的日期排序；文件名就是采集日的口径，不另造时间源。"""
    return sorted(str(p) for p in Path(data_dir).glob(BOARD_GLOB))


def snapshot_date(path: str) -> str:
    base = os.path.basename(path)
    return base[len("board-"):-len(".json")] if base.startswith("board-") else ""


def read_board(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        print("[NGA导入] 读不了 %s：%s" % (os.path.basename(path), e), flush=True)
        return []
    rows = d.get("threads") or []
    return [r for r in rows if isinstance(r, dict) and r.get("tid") and r.get("url")]


def read_body(data_dir: str, tid: Any) -> Dict[str, Any]:
    """正文只从 threads/<tid>.json 取；取不到就留空，不许拿标题或导航文案凑数。"""
    p = os.path.join(data_dir, "threads", "%s.json" % tid)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def state_path(root: Path) -> str:
    return str(root / "output" / STATE_NAME)


def load_state(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(path: str, st: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def append_log(root: Path, text: str) -> str:
    d = root / "output" / LOG_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    p = str(d / LOG_NAME)
    with open(p, "a", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n")
    return p


def collect(data_dir: str, files: List[str]) -> Dict[str, Any]:
    """把若干快照摊平成「每个 tid 一条」，同名 tid 取后面（更新的那份）的记录。"""
    uniq: Dict[Any, Dict[str, Any]] = {}
    per_file = {}
    for p in files:
        rows = read_board(p)
        per_file[os.path.basename(p)] = len(rows)
        for r in rows:
            uniq[r["tid"]] = r
    return {"rows": list(uniq.values()), "per_file": per_file}


def build_items(data_dir: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        body = read_body(data_dir, r["tid"])
        text = nga_forum._clean(body.get("body") or "")
        out.append({
            "aweme_id": nga_forum.post_id(r["url"]),
            "url": r["url"],
            # 标题与作者以正文档为准（列表页可能被截断或带前缀），缺了才退回快照行
            "title": (body.get("subject") or r.get("subject") or "").strip()[:200],
            "author": (body.get("author") or body.get("listAuthor")
                       or r.get("author") or "").strip(),
            "published_at": (body.get("firstPostDate") or r.get("postdateCst") or "").strip(),
            "transcript": text,
            "tid": r["tid"],
            "replies": r.get("replies"),
        })
    return out


def repo_root() -> Path:
    """状态与日志的位置跟着代码仓库走，不跟着 --db 走：
    否则拿 output/backups/ 里的库副本试跑，状态文件会写到 output/output/ 去。"""
    return Path(__file__).resolve().parent.parent


def sync(db_path: str, data_dir: str = DATA_DIR_DEFAULT, backfill: bool = False,
         force: bool = False, dry_run: bool = False, max_rows: int = 0,
         quiet: bool = False) -> Dict[str, Any]:
    root = repo_root()
    files = board_files(data_dir)
    if not files:
        return {"error": "找不到任何 %s/%s，采集那侧没跑或路径不对" % (data_dir, BOARD_GLOB),
                "found": 0, "new": 0}

    st = load_state(state_path(root))
    last = st.get("lastSnapshot") or ""
    if backfill:
        picked = files
    else:
        picked = [f for f in files if snapshot_date(f) > last]
        if not picked and force:
            picked = files[-1:]
        if not picked:
            return {"error": "", "skipped": "今天没有比 %s 更新的快照，本轮没动库" % (last or "（无）"),
                   "found": 0, "new": 0, "lastSnapshot": last}

    flat = collect(data_dir, picked)
    rows = flat["rows"]
    if max_rows > 0:
        rows = rows[:max_rows]
    items = build_items(data_dir, rows)

    have = set() if dry_run else nga_forum.known_ids(db_path)
    todo = [it for it in items if it["aweme_id"] not in have]
    no_body = sum(1 for it in todo if not it["transcript"])
    res = {"found": len(items), "alreadyInDb": len(items) - len(todo), "todo": len(todo),
           "noBody": no_body, "files": flat["per_file"], "dry_run": dry_run,
           "from": snapshot_date(picked[0]), "to": snapshot_date(picked[-1])}

    if dry_run:
        res["new"] = 0
        res["sample"] = [{"title": x["title"][:26], "chars": len(x["transcript"]),
                          "url": x["url"]} for x in todo[:3]]
    else:
        ins = nga_forum.insert(db_path, todo)
        res["new"] = len(ins)
        if ins:
            st["lastSnapshot"] = max(last, res["to"])
            st["lastRun"] = _now()
            st["lastNew"] = len(ins)
            save_state(state_path(root), st)

    if not quiet:
        line = ("[%s][NGA导入] 读 %d 份快照（%s→%s）摊平 %d 条｜库里已有 %d｜本轮要导 %d（无正文 %d）｜新增入库 %d%s"
                % (_now(), len(picked), res["from"], res["to"], res["found"],
                   res["alreadyInDb"], res["todo"], res["noBody"], res["new"],
                   "｜dry-run 没动库" if dry_run else ""))
        print(line, flush=True)
        res["logFile"] = append_log(root, line)
    return res


def check(db_path: str, data_dir: str = DATA_DIR_DEFAULT) -> Dict[str, Any]:
    """只读体检：不写库、不写状态，给界面和人都能看一眼的数字。"""
    files = board_files(data_dir)
    flat = collect(data_dir, files)
    items = build_items(data_dir, flat["rows"])
    have = nga_forum.known_ids(db_path)
    st = load_state(state_path(repo_root()))
    dates = [it["published_at"] for it in items if it["published_at"]]
    return {
        "dataDir": data_dir, "snapshots": len(files),
        "tidTotal": len(items), "withBody": sum(1 for it in items if it["transcript"]),
        "inDb": len(have), "pending": sum(1 for it in items if it["aweme_id"] not in have),
        "latestPost": max(dates) if dates else "",
        "lastRun": st.get("lastRun", ""), "lastSnapshot": st.get("lastSnapshot", ""),
        "lastNew": st.get("lastNew", ""),
    }


def main(argv=None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(
        description="导入 wow-forever launchd 抓的 NGA 板块快照（选题素材，不是内容源）")
    ap.add_argument("--db", default=str(root / "output" / "video2text.db"))
    ap.add_argument("--data-dir", default=os.environ.get("NGA_DATA_DIR", DATA_DIR_DEFAULT),
                    help="launchd 快照目录（默认 ~/.nga/data，与采集侧同一个环境变量）")
    ap.add_argument("--backfill", action="store_true", help="导全部历史快照，不止比上次导入新的")
    ap.add_argument("--force", action="store_true", help="没有更新快照也重跑最新一份")
    ap.add_argument("--dry-run", action="store_true", help="只算不写库")
    ap.add_argument("--check", action="store_true", help="只读体检，不写任何东西")
    ap.add_argument("--max-rows", type=int, default=0, help="本轮最多导几条（0＝不限）")
    a = ap.parse_args(argv)

    if a.check:
        c = check(a.db, a.data_dir)
        print(json.dumps(c, ensure_ascii=False, indent=1))
        return 0 if not c.get("snapshots") or c["pending"] >= 0 else 1
    r = sync(a.db, a.data_dir, backfill=a.backfill, force=a.force,
             dry_run=a.dry_run, max_rows=a.max_rows)
    print(json.dumps(r, ensure_ascii=False, indent=1)[:1200])
    return 1 if r.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
