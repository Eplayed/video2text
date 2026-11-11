"""首页运营入口。选题沿用排产匹配，稿件和出图沿用各渠道产线。"""
import datetime as dt

from . import frame_pool, plan_brief, plan_series


def recommend(board, package_for):
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
                if not cutoff <= material_day <= day:
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
            "slot_key": key, "title": task.get("title") or slot["title"],
            "form": form, "angle": slot.get("angle") or "",
            "boundary": slot.get("boundary") or "", "reason": reason,
            "stage": stage, "summary_id": sid, "has_package": bool(package),
            "task_error": task_error,
            "task_error_at": (task.get("updated_at") or "") if task_error else "",
            "materials": [{k: r.get(k) for k in ("video_id", "title", "author", "date",
                                                 "sheet", "row")} for r in rows],
            "candidate_id": rows[0]["video_id"] if priority == 0 else None,
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
