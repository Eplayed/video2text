"""首页运营入口。选题沿用排产匹配，稿件和出图沿用各渠道产线。"""
import datetime as dt
import re
import unicodedata

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
    entry = {"video_id": video["id"], "title": video["title"], "author": video["author"],
             "date": (video.get("published_at") or "")[:10], "sheet": video["source_sheet"],
             "row": video["source_row"]}
    if video.get("version_note"):
        entry["version_note"] = video["version_note"]
    return entry


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


_FOREVER_NAME = re.compile(r"魔兽(?:世界)?[:：\s]*无限(?:服)?|(?:wow[:：\s]*)?forever|无限服", re.I)
_TEST_BUILD = re.compile(r"闭测|内测|测试服|[ab]\s*测|ptr|beta", re.I)
_VERSION_HISTORY = re.compile(r"旧版|公测前|开服前瞻|开服前预测")
_EXPIRED_TITLE = re.compile(r"已失效|已过期|不再适用")
_RECENT_QUERY = re.compile(r"最新|近期|最近|当前版本|现版本|本周|今天|今日|现在")
_SEARCH_FRESH_DAYS = 14
_FACTIONS = ("部落", "联盟")
_LEVELING = ("练级", "升级", "冲级")
_LEVEL_RANGE = re.compile(r"(?<!\d)(\d{1,2})\s*级?\s*[-~至到]\s*(\d{1,2})(?!\d)\s*级?")
_LEVEL_POINT = re.compile(r"(?<!\d)(\d{1,2})\s*级")
_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-", "－": "-", "〜": "~"})
_SEARCH_SPLIT = re.compile(
    r"[\s，、；：？！。,:;!?/]+|怎么|怎样|如何|哪里|在哪|去哪|是什么|有哪些|值得")
_QUERY_PREFIX = re.compile(r"^(?:获得|获取|领取|想要|帮我|拿|找|查|领)+")


def _search_text(value):
    return unicodedata.normalize("NFKC", value or "").translate(_DASHES).casefold()


def _level_match(text, bounds):
    """优先同一区间，其次覆盖范围，再其次可作为部分等级的素材。"""
    ranges = [tuple(map(int, match.groups())) for match in _LEVEL_RANGE.finditer(text)
              if "级" in match.group()]
    low, high = bounds
    if bounds in ranges:
        return 3
    if any(a <= low and b >= high for a, b in ranges):
        return 2
    if any(max(a, low) < min(b, high) for a, b in ranges):
        return 1
    if not ranges and any(low <= int(n) < high for n in _LEVEL_POINT.findall(text)):
        return 1
    return 0


def search_topic(db_path, theme, limit=12):
    """从有转写的视频检索自然语言选题，在排序后截取结果。"""
    query = _search_text(theme)
    forever = bool(_FOREVER_NAME.search(query))
    factions = tuple(name for name in _FACTIONS if name in query)
    testing = bool(_TEST_BUILD.search(query))
    prefer_recent = bool(_RECENT_QUERY.search(query))
    today = plan_series.cn_now().date().toordinal()
    leveling = any(word in query for word in _LEVELING)
    interval = next((match for match in _LEVEL_RANGE.finditer(query)
                     if leveling or "级" in match.group()), None)
    bounds = tuple(sorted(map(int, interval.groups()))) if interval else None
    topic = query[:interval.start()] + " " + query[interval.end():] if interval else query
    remaining = _FOREVER_NAME.sub(" ", topic)
    remaining = _RECENT_QUERY.sub(" ", _TEST_BUILD.sub(" ", remaining))
    for word in (*factions, *(_LEVELING if leveling else ())):
        remaining = remaining.replace(word, " ")
    terms = []
    for part in _SEARCH_SPLIT.split(remaining):
        part = _QUERY_PREFIX.sub("", part)
        if leveling:
            part = re.sub(r"(?:攻略|指南|路线|教程)$", "", part)
        if len(part) >= 2 and part not in terms:
            terms.append(part)
    groups = [(word,) for word in terms[:8]]
    if leveling:
        groups.append(_LEVELING)

    conn = content_store.open_readonly(db_path)
    hits = []
    try:
        sheets = ",".join("?" for _ in supplements.VIDEO_SHEETS)
        rows = conn.execute(
            "SELECT id, title, author, published_at, source_sheet, source_row, game, ai_tags, transcript"
            " FROM videos WHERE source_sheet IN (" + sheets + ") AND length(trim(transcript)) > 0",
            supplements.VIDEO_SHEETS)
        for row in rows:
            title = _search_text(row["title"])
            body = _search_text(row["transcript"])
            head = title + "\n" + _search_text(row["ai_tags"])
            text = head + "\n" + body
            exact = query in text
            if forever:
                verdict = plan_series.forever_call(title, row["author"], body, row["source_sheet"])[0]
                if verdict == "off" or (verdict != "yes" and not _FOREVER_NAME.search(row["game"] or "")):
                    continue
            if factions and not all(name in text for name in factions):
                continue
            if len(factions) == 1 and any(name not in factions and name + "专属" in title for name in _FACTIONS):
                continue
            levels = _level_match(text, bounds) if bounds else 0
            if bounds and not levels and not exact:
                continue
            if leveling and not any(word in text for word in _LEVELING):
                continue
            matched = sum(any(word in text for word in group) for group in groups)
            if groups and not matched and not exact:
                continue
            if not (groups or factions or bounds or exact or forever or testing):
                continue
            title_matches = sum(any(word in title for word in group) for group in groups)
            title_level = _level_match(title, bounds) if bounds else 0
            test_material = bool(_TEST_BUILD.search(title))
            expired = bool(_EXPIRED_TITLE.search(title))
            history = bool(_VERSION_HISTORY.search(title))
            published_day = plan_series._day_ordinal(row["published_at"])
            recent = 0 <= today - published_day <= _SEARCH_FRESH_DAYS
            relevance = (query in title, exact, matched, title_matches, title_level, levels,
                         sum(name in title for name in factions))
            priority = (not expired, test_material == testing, not history)
            if prefer_recent:
                fresh_score = (recent, matched, title_matches, bool(title_level), published_day)
                score = priority + fresh_score + relevance + (row["id"],)
            else:
                score = priority + relevance + (published_day, row["id"])
            video = {column: row[column] for column in
                     ("id", "title", "author", "published_at", "source_sheet", "source_row")}
            if expired:
                video["version_note"] = "标题注明失效，核对更新说明"
            elif test_material:
                video["version_note"] = "测试期资料，核对当前版本"
            elif history:
                video["version_note"] = "涉及旧版或开服前信息，核对适用范围"
            elif not published_day:
                video["version_note"] = "发布日期不明"
            elif not recent:
                video["version_note"] = "发布时间较早，核对适用版本"
            hits.append((score, video))
    finally:
        conn.close()
    hits.sort(key=lambda hit: hit[0], reverse=True)
    return [video for _, video in hits[:limit]]


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
        custom = key.startswith("topic-") and slot.get("origin") == "today"
        reason = "自定义选题 · 已选素材合稿" if custom and len(rows) > 1 else (
            "自定义选题" if custom else "继续处理排产表中已采用的素材")
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
            "custom": custom,
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
