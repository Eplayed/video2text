"""新图文的原意母稿：原转写逐段提取、精确定位、续跑与忠实性检查。"""
import hashlib
import json
import re
from datetime import datetime

from . import content_store, source_corrections


STORE_KEY = "guide_mother_v1"
SCHEMA = 1
SEGMENT_CHARS = 1800


class MotherError(ValueError):
    pass


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json_dict(raw):
    try:
        value = json.loads(raw or "{}") if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _source_rows(conn, summary_id):
    row = conn.execute("SELECT video_id, source_video_ids FROM ai_summaries WHERE id=?",
                       (int(summary_id),)).fetchone()
    if not row:
        raise MotherError("整理稿不存在")
    ids = [int(row["video_id"])]
    try:
        ids.extend(int(x) for x in json.loads(row["source_video_ids"] or "[]"))
    except (TypeError, ValueError):
        pass
    ids = list(dict.fromkeys(ids))
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute("SELECT id, title, transcript FROM videos WHERE id IN (%s)" % placeholders,
                        ids).fetchall()
    by_id = {int(v["id"]): v for v in rows}
    sources = []
    for i in ids:
        if i not in by_id or not str(by_id[i]["transcript"] or "").strip():
            continue
        raw = str(by_id[i]["transcript"])
        try:
            source_text = source_corrections.effective_text(conn, i, raw)
        except source_corrections.CorrectionError as exc:
            raise MotherError(str(exc))
        sources.append({"video_id": i, "title": str(by_id[i]["title"] or ""),
                        "text": source_text, "corrected": source_text != raw,
                        "raw_text": raw if source_text != raw else ""})
    if not sources:
        raise MotherError("缺少原始转写：新图文不能用旧整理稿充当原视频来源")
    if len(sources) != len(ids):
        raise MotherError("部分来源视频缺少转写，原意母稿不能宣称完整")
    return sources


def source_snapshot(db_path, summary_id):
    conn = content_store.open_readonly(db_path)
    try:
        sources = _source_rows(conn, summary_id)
        revision = _digest([{k: s[k] for k in ("video_id", "title", "text")}
                            for s in sources])
        return sources, revision
    finally:
        conn.close()


def current_revisions(conn, summary_id):
    """在调用方持有的事务中核对原文和母稿，用于包落盘前最后一道门。"""
    sources = _source_rows(conn, summary_id)
    source_revision = _digest([{k: s[k] for k in ("video_id", "title", "text")}
                               for s in sources])
    row = conn.execute("SELECT structured_data FROM ai_summaries WHERE id=?",
                       (int(summary_id),)).fetchone()
    mother = _json_dict(row["structured_data"]).get(STORE_KEY) or {}
    return source_revision, mother.get("mother_revision") or ""


def split_sources(sources, source_revision, max_chars=SEGMENT_CHARS):
    """按原文字符偏移切段；没有标点时也前进，不漏尾段。"""
    out = []
    for source in sources:
        raw = source["text"]
        start = 0
        while start < len(raw):
            end = min(len(raw), start + max_chars)
            if end < len(raw):
                cuts = [m.end() for m in re.finditer(r"[。！？!?；;\n]", raw[start:end])
                        if m.end() >= max_chars // 2]
                if cuts:
                    end = start + cuts[-1]
            if end <= start:
                end = min(len(raw), start + max_chars)
            seg = {"video_id": source["video_id"], "start": start, "end": end,
                   "text": raw[start:end]}
            seg["segment_id"] = _digest([source_revision, seg["video_id"], start, end])[:20]
            out.append(seg)
            start = end
    return out


def load_mother(db_path, summary_id):
    conn = content_store.open_readonly(db_path)
    try:
        row = conn.execute("SELECT structured_data FROM ai_summaries WHERE id=?",
                           (int(summary_id),)).fetchone()
        if not row:
            raise MotherError("整理稿不存在")
        return _json_dict(row["structured_data"]).get(STORE_KEY) or {}
    finally:
        conn.close()


def _save_mother(db_path, summary_id, mother):
    """源修订在持锁事务内重查；较旧任务不能覆盖较新的源。"""
    conn = content_store.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        sources = _source_rows(conn, summary_id)
        current = _digest([{k: s[k] for k in ("video_id", "title", "text")}
                           for s in sources])
        if current != mother["source_revision"]:
            raise MotherError("原始转写已更新，本次旧母稿已作废；请重新提取")
        row = conn.execute("SELECT structured_data FROM ai_summaries WHERE id=?",
                           (int(summary_id),)).fetchone()
        structured = _json_dict(row["structured_data"])
        old = structured.get(STORE_KEY) or {}
        if old.get("source_revision") == current and old.get("status") == "complete":
            # 两个请求同时提取时，不让迟到的旧结果盖住已完成母稿。
            return old
        structured[STORE_KEY] = mother
        conn.execute("UPDATE ai_summaries SET structured_data=? WHERE id=?",
                     (json.dumps(structured, ensure_ascii=False), int(summary_id)))
        conn.commit()
        return mother
    finally:
        conn.close()


def _client_json(ai_config, prompt):
    if not (ai_config or {}).get("api_key"):
        raise MotherError("未配置 AI，原意母稿未生成；采集素材不受影响")
    import openai
    client = openai.OpenAI(api_key=ai_config["api_key"],
                           base_url=ai_config.get("api_base") or "https://api.openai.com/v1",
                           timeout=300, max_retries=1)
    resp = client.chat.completions.create(
        model=ai_config.get("model") or "qwen-plus",
        messages=[{"role": "user", "content": prompt}], temperature=0,
        response_format={"type": "json_object"})
    try:
        return json.loads(resp.choices[0].message.content or "{}")
    except (TypeError, ValueError):
        raise MotherError("模型没有返回可解析的母稿 JSON")


def _extract_segment(segment, source_title, ai_config):
    prompt = """你只做原文事实摘录，不写文章。视频标题仅供识别题材，不能当事实来源。
从【原始转写段】逐条抽取可影响攻略结论的事实、步骤、适用条件、否定词、主体数量与观点。
quote 必须逐字复制原始转写段里的连续文本，不能改标点、合并跨句或补字；display_text 可压缩但不得扩大范围。
即使本段是全文最后一段也不能省略。长段要列全，不要只取开头。不能输出原文没有的结论。
输出 JSON：{"points":[{"quote":"原句连续片段","display_text":"忠实短句","priority":"must/support/optional","subject":"原句主体","conditions":["适用条件"],"modality":"fact/opinion"}]}。
标题：%s
原始转写段：
%s""" % (source_title, segment["text"])
    return _client_json(ai_config, prompt)


def _points_from_result(segment, result, source_revision):
    raw = result.get("points") if isinstance(result, dict) else None
    if not isinstance(raw, list) or (not raw and segment["text"].strip()):
        raise MotherError("第 %s 段没有可定位的要点，不能当作已完整处理" % segment["segment_id"])
    points = []
    for item in raw:
        if not isinstance(item, dict):
            raise MotherError("母稿要点格式无效")
        quote = str(item.get("quote") or "").strip()
        at = segment["text"].find(quote) if quote else -1
        if at < 0:
            raise MotherError("第 %s 段的引文无法逐字定位：%s" %
                              (segment["segment_id"], quote[:40]))
        start = segment["start"] + at
        end = start + len(quote)
        point_id = _digest([source_revision, segment["video_id"], start, end, quote])[:20]
        priority = str(item.get("priority") or "support")
        if priority not in ("must", "support", "optional"):
            raise MotherError("要点 %s 的 priority 无效" % point_id)
        conditions = item.get("conditions") or []
        if not isinstance(conditions, list):
            raise MotherError("要点 %s 的 conditions 应为数组" % point_id)
        points.append({"point_id": point_id, "quote": quote,
                       "display_text": str(item.get("display_text") or quote).strip(),
                       "priority": priority, "subject": str(item.get("subject") or "").strip(),
                       "conditions": [str(x).strip() for x in conditions if str(x).strip()],
                       "modality": "opinion" if item.get("modality") == "opinion" else "fact",
                       "source_ref": {"video_id": segment["video_id"],
                                      "start": start, "end": end}})
    return points


def _finish(mother):
    points = mother["points"]
    if not any(p["priority"] == "must" for p in points):
        raise MotherError("母稿缺少必保点；不能把全部信息标成可省略")
    first = next(p for p in points if p["priority"] == "must")
    mother["core_question"] = "这条素材的核心结论和适用条件是什么？"
    mother["core_answer"] = first["display_text"]
    mother["mother_revision"] = _digest([mother["source_revision"], points])
    mother["status"] = "complete"
    return mother


def ensure_mother(db_path, summary_id, ai_config, extractor=None, progress=None):
    """成功段逐段落库，失败后只重跑未成功段；不写任何旧图文包。"""
    sources, revision = source_snapshot(db_path, summary_id)
    segments = split_sources(sources, revision)
    old = load_mother(db_path, summary_id)
    if old.get("source_revision") == revision and old.get("status") == "complete":
        return old
    done = {x["segment_id"]: x for x in old.get("segments") or []
            if old.get("source_revision") == revision and x.get("status") == "done"}
    old_points = {p["point_id"]: p for p in old.get("points") or []
                  if old.get("source_revision") == revision}
    mother = {"schema": SCHEMA, "source_revision": revision, "mother_revision": "",
              "status": "partial", "segments": [], "points": [], "core_question": "",
              "core_answer": "", "updated_at": datetime.now().isoformat(timespec="seconds")}
    by_video = {s["video_id"]: s for s in sources}
    for i, seg in enumerate(segments, 1):
        sid = seg["segment_id"]
        if progress:
            progress("提取原意母稿：第 %d/%d 段" % (i, len(segments)))
        if sid in done and all(pid in old_points for pid in done[sid].get("point_ids") or []):
            record = done[sid]
            points = [old_points[pid] for pid in record["point_ids"]]
        else:
            try:
                result = (extractor or _extract_segment)(seg, by_video[seg["video_id"]]["title"], ai_config)
                points = _points_from_result(seg, result, revision)
            except Exception as exc:
                mother["segments"].append({"segment_id": sid, "video_id": seg["video_id"],
                                           "start": seg["start"], "end": seg["end"],
                                           "status": "failed", "point_ids": [],
                                           "error": str(exc)[:300]})
                mother["status"] = "failed"
                _save_mother(db_path, summary_id, mother)
                raise MotherError("原意母稿第 %d/%d 段失败：%s；已成功段可续跑" %
                                  (i, len(segments), exc))
            record = {"segment_id": sid, "video_id": seg["video_id"],
                      "start": seg["start"], "end": seg["end"],
                      "status": "done", "point_ids": [p["point_id"] for p in points], "error": ""}
        mother["segments"].append(record)
        known = {p["point_id"] for p in mother["points"]}
        mother["points"].extend(p for p in points if p["point_id"] not in known)
        _save_mother(db_path, summary_id, mother)
    try:
        _finish(mother)
    except MotherError:
        mother["status"] = "failed"
        _save_mother(db_path, summary_id, mother)
        raise
    return _save_mother(db_path, summary_id, mother)


def mother_prompt(mother):
    if mother.get("status") != "complete":
        raise MotherError("原意母稿尚未完整提取")
    return json.dumps({"core_answer": mother.get("core_answer") or "",
                       "points": [{"point_id": p["point_id"], "quote": p["quote"],
                                   "display_text": p["display_text"], "priority": p["priority"],
                                   "subject": p["subject"], "conditions": p["conditions"],
                                   "modality": p["modality"]} for p in mother["points"]]},
                      ensure_ascii=False)


def _output_text(plan, publish):
    parts = []
    pages = plan.get("pages") or [plan]
    for pg in pages:
        parts.extend(str(pg.get(k) or "") for k in ("title", "subtitle"))
        for card in pg.get("cards") or []:
            parts.extend(str(card.get(k) or "") for k in ("t", "d"))
    parts.extend(str((publish or {}).get(k) or "") for k in ("copy_text", "digest", "xhs_copy"))
    for key in ("tt_titles", "xhs_titles"):
        parts.extend(str(x) for x in (publish or {}).get(key) or [] if x)
    return "\n".join(x for x in parts if x)


def _field_texts(plan, publish):
    """每个读者可见的标题/配文单独列项，审核结果可精确定位到字段。"""
    fields = {}
    for index, pg in enumerate(plan.get("pages") or [plan], 1):
        for key in ("title", "subtitle"):
            value = str(pg.get(key) or "").strip()
            if value:
                fields["page_%d.%s" % (index, key)] = value
    for key in ("copy_text", "digest", "xhs_copy"):
        value = str((publish or {}).get(key) or "").strip()
        if value:
            sentences = [part.strip() for part in re.split(r"(?<=[。！？!?；;])|\n+", value)
                         if part.strip()]
            for index, sentence in enumerate(sentences, 1):
                fields["publish.%s.%d" % (key, index)] = sentence
    for key in ("tt_titles", "xhs_titles"):
        for index, value in enumerate((publish or {}).get(key) or [], 1):
            if str(value).strip():
                fields["publish.%s_%d" % (key, index)] = str(value).strip()
    return fields


def _model_compare(mother, output, ai_config, source_text="", mappings=None, fields=None):
    prompt = """你是独立校对员。逐句比较【源文引文】与【待发布文案】，专查主体数量、谁做了什么、条件、先后、概率、否定词。
源文引文逐字来自原始转写；不得引用或相信待发布文案自己声称的出处。不要补游戏常识。
若有任何变义、无源断言，或卡片挂的 point_id 实际不支持卡片文字，issues 逐条列待发布文案的原句、问题、对应引文；不得用免责声明抵消错误。
对【逐项字段】中的每一项给出支撑它的 point_id 列表。纯导航标签可填 navigation=true，但包含数字、主体、条件或结论的字段不得标为导航。只列真实支持该句的点，不能为凑齐而乱挂 ID。
输出 JSON：{"issues":[{"text":"待发布文案原句","problem":"具体变义","source_quote":"对应源文原句"}],"field_sources":{"page_1.title":{"point_ids":["现有ID"],"navigation":false}}}。field_sources 必须覆盖逐项字段全部键；没有问题才返回空 issues。
【原始转写】%s
【已定位要点】%s
【卡片与来源映射】%s
【逐项字段】%s
【待发布文案】%s""" % (source_text or "（仅有已定位要点）", mother_prompt(mother),
                   json.dumps(mappings or [], ensure_ascii=False),
                   json.dumps(fields or {}, ensure_ascii=False), output)
    return _client_json(ai_config, prompt)


def _local_semantic_issues(output, source_text):
    """只拦非常具体的无源数量/否定断言；其余语义交独立校对。"""
    source = re.sub(r"\s+", "", source_text or "")
    if not source:
        return []
    issues = []
    quantity = r"[一二三四五六七八九十两1-9]个(?:小号|角色)"
    for claim in set(re.findall(quantity, output)):
        if claim not in source:
            issues.append({"text": claim, "problem": "原始转写找不到同一主体数量，需核对范围",
                           "source_quote": ""})
    # 同一原文里可能同时出现“两小号拿到 11 点”和“三小号补最后 5 点”。
    # 只查名词/数字各自出现会把两条路线错接成一个标题；要核对它们在原文的先后邻接。
    pair = r"([一二三四五六七八九十两1-9]个小号)[^。；\n]{0,12}?(\d+点)"
    for match in re.finditer(pair, output):
        actor, outcome = match.group(1), match.group(2)
        starts = [m.start() for m in re.finditer(re.escape(actor), source)]
        if not any(outcome in source[at:at + 180] for at in starts):
            issues.append({"text": match.group(0),
                           "problem": "主体数量与点数在原始转写中不是同一阶段的结论",
                           "source_quote": ""})
    negative = r"(?:不|没有|无法|不能)[^，。；\n]{0,10}(?:点数|传承点)"
    for claim in set(re.findall(negative, output)):
        if claim not in source:
            issues.append({"text": claim, "problem": "否定结论在原始转写中找不到逐字依据",
                           "source_quote": ""})
    return issues


def audit_output(mother, plan, publish, ai_config, checker=None, source_text=""):
    """覆盖与语义分列；未知不能当 PASS。生成前须对当前编辑后的文案重新调用。"""
    if mother.get("status") != "complete":
        raise MotherError("原意母稿未完成")
    points = {p["point_id"]: p for p in mother["points"]}
    must = {pid for pid, p in points.items() if p["priority"] == "must"}
    pages = plan.get("pages") or [plan]
    used = set()
    mappings = []
    for pg in pages:
        for card in pg.get("cards") or []:
            used.update(str(x) for x in (card.get("point_ids") or []))
            mappings.append({"title": card.get("t") or "", "text": card.get("d") or "",
                             "point_ids": card.get("point_ids") or []})
    missing = sorted(must - used)
    unknown = sorted(used - set(points))
    coverage_issues = ([{"point_id": pid, "problem": "必保点未映射到图上卡片"} for pid in missing]
                       + [{"point_id": pid, "problem": "引用了不存在的母稿点"} for pid in unknown])
    output = _output_text(plan, publish)
    fields = _field_texts(plan, publish)
    revision = audit_revision(mother, plan, publish, source_text)
    semantic = {"status": "unknown", "issues": [], "input_revision": revision}
    try:
        result = (checker(mother, output, ai_config, source_text) if checker
                  else _model_compare(mother, output, ai_config, source_text, mappings, fields))
        issues = result.get("issues") if isinstance(result, dict) else None
        if not isinstance(issues, list):
            raise MotherError("忠实性校对未返回 issues 数组")
        field_sources = result.get("field_sources") if isinstance(result, dict) else None
        if checker is None or field_sources is not None:
            if not isinstance(field_sources, dict):
                raise MotherError("校对未返回标题与配文来源映射")
            for field, value in fields.items():
                entry = field_sources.get(field)
                if not isinstance(entry, dict):
                    coverage_issues.append({"field": field, "problem": "标题或配文缺来源映射"})
                    continue
                refs = entry.get("point_ids") or []
                if not isinstance(refs, list):
                    refs = []
                invalid = [str(pid) for pid in refs if str(pid) not in points]
                if invalid:
                    coverage_issues.append({"field": field, "problem": "引用了不存在的母稿点：%s" % ",".join(invalid)})
                if not refs and not (entry.get("navigation") and not re.search(r"\d|[一二三四五六七八九十两]个", value)):
                    coverage_issues.append({"field": field, "problem": "实质标题或配文没有对应母稿点"})
            semantic["field_sources"] = {k: field_sources.get(k) for k in fields}
        semantic["issues"] = _local_semantic_issues(output, source_text) + issues
        semantic["status"] = "fail" if semantic["issues"] else "pass"
    except Exception as exc:
        semantic["issues"] = [{"problem": "忠实性校对未完成：%s" % str(exc)[:200]}]
    coverage = {"status": "fail" if coverage_issues else "pass",
                "issues": coverage_issues, "input_revision": revision}
    if semantic.get("field_sources") is not None:
        coverage["field_sources"] = semantic["field_sources"]
    return {"coverage": coverage, "semantic": semantic,
            "source_revision": mother["source_revision"],
            "mother_revision": mother["mother_revision"],
            "ready": coverage["status"] == "pass" and semantic["status"] == "pass"}


def audit_revision(mother, plan, publish, source_text=""):
    """审核输入只包括读者可见的事实文字、映射 ID、原文版本；换皮不改变它。"""
    used = set()
    for page in plan.get("pages") or [plan]:
        for card in page.get("cards") or []:
            used.update(str(x) for x in (card.get("point_ids") or []))
    return _digest([mother["mother_revision"], _output_text(plan, publish),
                    sorted(used), _digest(source_text)])


def audit_problems(check):
    problems = []
    for item in (check.get("coverage") or {}).get("issues") or []:
        problems.append("原意覆盖：%s（%s）" % (item.get("field") or item.get("point_id") or "?", item.get("problem") or ""))
    for item in (check.get("semantic") or {}).get("issues") or []:
        problems.append("原意忠实性：%s%s" % (item.get("text") or "",
                                         item.get("problem") or "未判"))
    return problems


def wechat_plan(cards, mother):
    """公众号卡片适配同一忠实性闸；页面比例和渲染仍由渠道自己决定。"""
    pages = []
    for card in cards:
        bits = [str(card.get(k) or "") for k in
                ("subtitle", "timeline_label", "timeline", "timeline_note", "precondition",
                 "section", "footer", "badge", "faction_text")]
        for field, keys in (("hooks", ("t", "d")), ("items", ("name", "desc")),
                            ("steps", ("h", "p"))):
            for item in card.get(field) or []:
                bits.extend(str(item.get(k) or "") for k in keys)
                if field == "items":
                    bits.append(str(item.get("tag") or ""))
        note = card.get("note") or {}
        if isinstance(note, dict) and note.get("text"):
            bits.extend(str(note.get(k) or "") for k in ("title", "text"))
        pages.append({"title": card.get("title") or "",
                      "cards": [{"t": card.get("title") or "", "d": "；".join(x for x in bits if x),
                                 "point_ids": card.get("point_ids") or []}]})
    return {"pages": pages, "source_revision": mother["source_revision"],
            "mother_revision": mother["mother_revision"]}
