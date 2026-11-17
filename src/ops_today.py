"""首页运营入口。选题沿用排产匹配，稿件和出图沿用各渠道产线。"""
import datetime as dt
import re

from . import content_store, frame_pool, plan_brief, plan_series, supplements


_CONTEXT_TERMS = re.compile(
    "魔兽世界|魔兽|无限服|测试服|国服|正式服|攻略|实测|猎人|术士|法师|牧师|"
    "战士|盗贼|圣骑士|萨满|德鲁伊"
)


def topic_overlap(main_title, supplement_title):
    """游戏、服务器或职业相同，还要有具体机制、事件或操作的交集。"""
    return any(len(_CONTEXT_TERMS.sub("", part)) >= 3
               for part in supplements.strong_fragments(main_title, supplement_title))


def material(video):
    return {"video_id": video["id"], "title": video["title"], "author": video["author"],
            "date": (video.get("published_at") or "")[:10], "sheet": video["source_sheet"],
            "row": video["source_row"]}


def standalone_topic(key, title, videos, reason, *, custom=False, summary_id=None):
    return {"slot_key": key, "title": title, "form": "guide", "angle": "", "boundary": "",
            "reason": reason, "stage": "已有整合稿" if summary_id else "有候选",
            "summary_id": summary_id, "has_package": False, "task_error": "", "task_error_at": "",
            "materials": [material(v) for v in videos], "candidate_id": None,
            "standalone": True, "custom": custom}


def recent_videos(db_path, today, excluded, limit):
    if limit <= 0:
        return []
    start = (dt.date.fromisoformat(today) - dt.timedelta(days=plan_brief.FRESH_DAYS)).isoformat()
    conn = content_store.open_readonly(db_path)
    try:
        owner = plan_series.douyin_owner_map(conn)
        rows = conn.execute(
            "SELECT id, source_sheet, source_row, title, author, published_at, category, game,"
            " source, author_sec_uid, substr(transcript, 1, 1200) AS tr FROM videos"
            " WHERE published_at >= ? AND published_at < ? AND length(trim(transcript)) >= ?"
            " ORDER BY published_at DESC, id DESC",
            (start, (dt.date.fromisoformat(today) + dt.timedelta(days=1)).isoformat(), plan_series.MIN_TL))
        results = []
        for entry in rows:
            v = dict(entry)
            if not v["title"] or v["id"] in excluded:
                continue
            sheet = v["source_sheet"] or ""
            verdict, _ = plan_series.forever_call(v["title"], v["author"], v["tr"], sheet)
            if verdict == "off":
                continue
            if "正式服" in v["title"] and plan_series.forever_call(v["title"], "", "", sheet)[0] != "yes":
                continue
            if plan_series.level1_ok(sheet, v["category"], verdict, v["game"], owner=owner, row=v)[0]:
                results.append(v)
                if len(results) == limit:
                    break
        return results
    finally:
        conn.close()


def search_topic(db_path, theme, limit=12):
    # 允许输入自然语言。完整短语优先，空格分词或常见提问词可召回分开的关键词。
    terms = []
    for part in re.split(r"[\s，、；：？！。,:;!?/]+|怎么|怎样|如何|哪里|在哪|去哪|是什么|有哪些|值得",
                         theme):
        part = re.sub(r"^(?:获得|获取|领取|想要|帮我|拿|找|查|领)+", "", part)
        if len(part) >= 2 and part not in {"魔兽", "魔兽无限", "攻略"} and part not in terms:
            terms.append(part)
    terms = terms[:8]
    hits = {}
    for term in dict.fromkeys([theme] + terms):
        for v in content_store.search_videos(db_path, term, limit=120):
            if supplements.is_video(v["source_sheet"]) and (v.get("transcript") or "").strip():
                hits[v["id"]] = v
    rank = lambda v: (theme.casefold() in v["title"].casefold(),
                      sum(term.casefold() in v["title"].casefold() for term in terms),
                      sum(term.casefold() in (v["transcript"] or "").casefold() for term in terms),
                      v["published_at"] or "", v["id"])
    return sorted(hits.values(), key=rank, reverse=True)[:limit]


def recommend(board, package_for, used=()):
    """只推荐已有明确素材或在制稿件的可采用题位。"""
    today = board["today"]
    channel = board["channel"]
    day = dt.date.fromisoformat(today).toordinal()
    cutoff = day - plan_brief.FRESH_DAYS
    legacy_dates = {item["id"]: item["date"] for item in plan_brief.load_data()["items"]}
    frames_available = {}
    items = []
    for slot in board["slots"]:
        if slot["published"] or slot["pending_review"] or not slot["adoptable"]:
            continue
        key = slot["slot_key"]
        scheduled_date = legacy_dates.get(key)
        task = slot["task"] or {}
        form = task.get("form") or slot["form"]
        if form not in ("guide", "mix") or (scheduled_date and scheduled_date > today):
            continue
        sid = task.get("summary_id")
        package = package_for(sid, channel) if sid else None
        rows = slot["adopted_rows"]
        priority = 2
        reason = "继续处理排产表中已采用的素材"
        if package:
            priority, reason = 1, "已有图文包，先查看成品与审核结果"
        elif not rows:
            if task:
                # 人清空过采用清单，交回排产表重新选。
                continue
            fresh = []
            for row in slot["candidates"]:
                material_day = plan_series._day_ordinal(row["date"])
                if row["video_id"] in used or not cutoff <= material_day <= day:
                    continue
                hits = plan_series._match_terms(slot["keywords"], row["title"], "", "")
                aid = str(row.get("aweme_id") or "").strip()
                if not hits or not aid:
                    continue
                if aid not in frames_available:
                    frames_available[aid] = bool((frame_pool.load(aid) or {}).get("frames"))
                if not frames_available[aid]:
                    continue
                # 优先具体的标题命中，避免转写顺带一提就被当作成稿的主要依据。
                score = (sum(len(word.replace(" ", "")) for word in hits),
                         material_day, int(row.get("nk") or 0))
                fresh.append((score, row, hits))
            if not fresh:
                continue
            _, row, hits = max(fresh, key=lambda match: match[0])
            rows = [row]
            priority = 0
            reason = "近期素材标题命中「%s」" % "、".join(hits)
        stage = "待审图文" if package else slot["stage_label"]
        if sid and not package:
            stage = "已有整合稿"
        task_error = ""
        if not sid and task.get("status") in ("failed", "aborted"):
            task_error = task.get("error") or "上次整合稿生成未完成"
            stage = "整合稿中断" if task["status"] == "aborted" else "整合稿失败"
        item = {
            "slot_key": key,
            "title": (rows[0]["title"] if priority == 0 else task.get("title") or slot["title"]),
            "form": form, "angle": "" if priority == 0 else slot.get("angle") or "",
            "boundary": slot.get("boundary") or "", "reason": reason,
            "stage": stage, "summary_id": sid, "has_package": bool(package),
            "task_error": task_error,
            "task_error_at": (task.get("updated_at") or "") if task_error else "",
            "materials": [{k: r.get(k) for k in ("video_id", "title", "author", "date",
                                                 "sheet", "row")} for r in rows],
            "candidate_id": rows[0]["video_id"] if priority == 0 else None,
            "custom": key.startswith("topic-") and slot.get("origin") == "today",
        }
        date = max((plan_series._day_ordinal(r["date"]) for r in rows), default=0)
        items.append(((priority, bool(priority == 0 and not scheduled_date),
                       -date, key), item))
    ordered = []
    seen = set()
    for _, item in sorted(items, key=lambda pair: pair[0]):
        candidate = item["candidate_id"]
        if candidate and candidate in seen:
            continue
        if candidate:
            seen.add(candidate)
        ordered.append(item)
    return ordered


def source_overview(tasks):
    """同一个账号可能属于多个定时任务，成员回执只显示一次。"""
    labels = {"douyin": "抖音", "bilibili": "B 站", "wechat": "公众号 RSS"}
    by_platform = {}
    sources = []
    for task in tasks:
        if task.get("kind") == "source":
            sources.append({"label": task.get("source_label") or task["name"],
                            "at": task["last_run"], "status": task["last_status"],
                            "note": task["last_note"], "members": []})
        for member in task["members"]:
            by_platform.setdefault(member["platform"], {})[member["id"]] = member
    for platform, group in by_platform.items():
        members = sorted(group.values(), key=lambda m: m["synced_at"], reverse=True)
        failed = [m for m in members if any(token in m["sync_note"]
                  for token in ("失败", "异常", "拒绝", "未配置", "过期"))]
        at = members[0]["synced_at"]
        if len(failed) == len(members):
            status, note = "error", "%d 个订阅最近一次拉取失败" % len(failed)
        elif failed:
            status, note = "error", "%d/%d 个订阅最近一次拉取失败" % (len(failed), len(members))
        elif at:
            status, note = "logged", "%d 个订阅有同步记录" % sum(bool(m["synced_at"]) for m in members)
        else:
            status, note = "", "暂无同步记录"
        sources.append({"label": labels.get(platform, platform), "at": at,
                        "status": status, "note": note, "members": members})
    return sources
