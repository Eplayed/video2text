"""video2text Web 管理界面 - Flask Backend"""
import json, os, re, sys, sqlite3, threading, time, traceback, hashlib, uuid
from pathlib import Path
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

# ── 添加项目根到 path ──
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as collector  # main.py
from src import content_store, material_store, toutiao_graphics, ai_poster
from src.graphics.channels import wechat as wechat_graphics
from src.graphics import variants as graphics_variants
from src.graphics.gate import GraphicsGateError
from src.path_config import find_parser_dir, ensure_parser_on_path, get_cookie_path

# ── 路径 ──
OUTPUT_DIR    = ROOT / "output"
EXCEL_PATH    = OUTPUT_DIR / "抖音视频信息.xlsx"
INDEX_PATH    = ROOT / "video_index.json"
DB_PATH       = OUTPUT_DIR / "video2text.db"
PARSER_DIR    = Path(find_parser_dir())

app = Flask(__name__, static_folder="static", static_url_path="/static")

# ── 处理状态（内存） ──
_task_status = {"running": False, "progress": "", "done": False, "error": ""}
_classify_status = {"running": False, "progress": "", "done": True, "error": "", "result": None}
_db_lock = threading.RLock()

# ── 工具：cookie 写入与 parser 初始化 ──
def _setup_parser_and_cookie(cookie_str: str):
    """写入 cookie 文件并初始化 parser（自动加上 sessionid= 前缀）。
    不使用 os.chdir（会影响 Flask 工作目录），而是通过 set_cookie() 直接设置。
    """
    # 自动加上 sessionid= 前缀
    if cookie_str and "sessionid=" not in cookie_str:
        cookie_str = f"sessionid={cookie_str}"

    # ── Cookie 合并保护（2026-09-21）：短 cookie（纯 sessionid）不覆盖完整 Cookie ──
    # 抖音 Argus 风控要求完整安全 Cookie（UIFID/ttwid/msToken 等），前端 localStorage
    # 里可能仍存着旧的纯 sessionid，直接写入会把完整串冲掉，导致同步全部返回空。
    try:
        cookie_path = get_cookie_path()
        if cookie_str and len(cookie_str) < 200 and cookie_path.exists():
            existing = cookie_path.read_text(encoding="utf-8").strip()
            if len(existing) > len(cookie_str) and "uifid=" in existing.lower():
                import re as _re
                m_new = _re.search(r"sessionid=([^;]+)", cookie_str)
                m_old = _re.search(r"sessionid=([^;]+)", existing)
                if m_new and m_old and m_new.group(1) != m_old.group(1):
                    # sessionid 变了（重新登录）：只更新完整串里的 sessionid 值
                    existing = _re.sub(
                        r"sessionid=[^;]*", "sessionid=" + m_new.group(1), existing, count=1
                    )
                cookie_str = existing
    except Exception:
        pass

    parser_dir = str(PARSER_DIR)
    if not os.path.isdir(parser_dir):
        raise RuntimeError(
            f"douyin_parse 目录不存在: {parser_dir}\n"
            f"请执行: git clone https://github.com/DLWangSan/douyin_parse.git {parser_dir}"
        )

    # 写入 cookie 文件（部分内部逻辑仍可能读取文件，保持兼容）
    cookie_path = get_cookie_path()
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cookie_path, "w") as f:
        f.write(cookie_str)

    # 确保 parser 目录在 sys.path 中并创建实例
    ensure_parser_on_path()
    parser = collector.get_douyin_parser(parser_dir)
    # 直接设置 cookie，避免依赖 os.chdir
    parser.set_cookie(cookie_str)
    return parser


def _sync_content_db():
    """把 Excel 当前内容同步到 SQLite 内容库。"""
    if not EXCEL_PATH.exists():
        raise RuntimeError(f"Excel 不存在: {EXCEL_PATH}")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db_lock:
        return content_store.sync_excel_to_db(EXCEL_PATH, DB_PATH)


def _sync_material_workspace():
    """同步 SQLite + AI JSONL 素材层，轻量操作，不下载媒体。"""
    with _db_lock:
        content_store.sync_excel_to_db(EXCEL_PATH, DB_PATH)
        return material_store.export_workspace(EXCEL_PATH, DB_PATH)


def _ai_config():
    config = collector.load_env_file(str(ROOT / "config" / "config.env.local"))
    method = collector.normalize_ai_method(collector.config_get(config, "AI_METHOD", "skip"))
    if method == "deepseek":
        api_key = collector.config_get(config, "DEEPSEEK_API_KEY") or collector.config_get(config, "AI_API_KEY")
        api_base = collector.config_get(config, "DEEPSEEK_API_BASE", "https://api.deepseek.com")
        model = collector.config_get(config, "DEEPSEEK_MODEL", "deepseek-chat")
    else:
        api_key = collector.config_get(config, "OPENAI_API_KEY") or collector.config_get(config, "AI_API_KEY")
        api_base = collector.config_get(config, "AI_API_BASE", "https://api.openai.com/v1")
        model = collector.config_get(config, "AI_MODEL", "gpt-4o-mini")
    cfg = {"method": method, "api_key": api_key, "api_base": api_base, "model": model,
           "fast_model": collector.config_get(config, "AI_MODEL_FAST", ""),
           # 看图那类活（拆参考图做模板）单独一档，默认走 qwen3-vl-flash；
           # 以后想换更强的视觉模型只改配置里的 AI_MODEL_VISION，不用动代码
           "vision_model": collector.config_get(config, "AI_MODEL_VISION", "")}
    return cfg


def _fast_config():
    """拆解与卡片化走「快速模型」：这类活是照抄压缩，不需要主模型深思。

    实测同一条素材：主模型 qwen3.8-max 拆解约 180 秒，deepseek-v4-flash 33 秒且上图
    字数更守规矩（71 字 vs 超线）；qwen-plus 只要 9.7 秒、便宜五倍，但会把上图文字
    写到 98 字（超 80 线，会被自动压缩砍掉一张卡）。
    没配 AI_MODEL_FAST 时原样返回主配置，行为零变化。
    """
    cfg = _ai_config()
    fast = (cfg.get("fast_model") or "").strip()
    if fast:
        cfg = dict(cfg)
        cfg["model"] = fast
    return cfg

# ── API: 视频列表 ──
def _first_markdown_image(text: str) -> str:
    """从 Markdown 正文提取第一张图片 URL（微信文章封面兜底：入库时不存 cover_url，正文首图即头图）。"""
    if not text:
        return ""
    m = re.search(r"!\[[^\]]*\]\((https?://[^)\s]+)", text)
    if m:
        return m.group(1)
    m = re.search(r'<img[^>]+src=["\'](https?://[^"\']+)', text)
    return m.group(1) if m else ""


@app.route("/api/videos")
def api_videos():
    topic = request.args.get("topic", "")
    q     = request.args.get("q", "").lower()
    category = request.args.get("category", "")
    game = request.args.get("game", "")
    source = request.args.get("source", "")  # ''=全部 / douyin / wechat
    videos = []
    if INDEX_PATH.exists():
        with open(INDEX_PATH, encoding="utf-8") as f:
            data = json.load(f)
        videos = data.get("videos", [])
        # 合并 SQLite 中的分类/标签/最新整理稿类型（按 sheet+row 关联）
        db_info = _load_video_extras()
        for v in videos:
            info = db_info.get((v.get("sheet"), v.get("row")))
            if info:
                v["db_id"] = info["id"]
                v["category"] = info["category"]
                v["ai_tags"] = info["ai_tags"]
                v["game"] = info["game"]
                v["summary_type"] = info.get("summary_type", "")
            else:
                v.setdefault("category", "")
                v.setdefault("ai_tags", "")
                v.setdefault("game", "")
                v.setdefault("summary_type", "")

    # 追加 SQLite 中的微信文章（DB 是微信文章权威源，不经过 video_index.json，索引重建不会丢）
    if DB_PATH.exists():
        try:
            with _db_lock:
                conn = content_store.connect(DB_PATH)
                try:
                    wx_rows = conn.execute(
                        "SELECT id, source_sheet, source_row, source_url, author, title, "
                        "status, published_at, category, ai_tags, game, "
                        "COALESCE(cover_url,'') as cover, COALESCE(transcript,'') as transcript "
                        "FROM videos WHERE source_sheet = '微信文章' ORDER BY id DESC LIMIT 200"
                    ).fetchall()
                finally:
                    conn.close()
            for r in wx_rows:
                body = r["transcript"] or ""
                cover = r["cover"] or _first_markdown_image(body)
                videos.append({
                    "id": r["id"], "db_id": r["id"],
                    "sheet": r["source_sheet"], "row": r["source_row"],
                    "url": r["source_url"] or "",
                    "aweme_id": "", "author": r["author"] or "",
                    "title": r["title"] or "", "status": r["status"] or "",
                    "published_at": r["published_at"] or "",
                    "create_time": (r["published_at"] or "")[:10],
                    "cover": cover, "cover_url": cover,
                    "transcript_length": len(body),
                    "category": r["category"] or "",
                    "ai_tags": r["ai_tags"] or "",
                    "game": r["game"] or "",
                    "summary_type": "",
                })
        except Exception as e:
            # 原先是裸 pass：这里一旦抛错（列缺失/DB 锁），素材库的微信文章整块消失且无痕迹，
            # 表现就是「公众号有更新但工作台看不到」。保留降级不阻断抖音列表，但必须留日志。
            print(f"[videos] 微信文章追加失败（素材库将看不到微信内容）: {e}", flush=True)
    # 来源标识统一：微信文章 vs 抖音，前端卡片徽章/筛选用
    for v in videos:
        v["source"] = "wechat" if v.get("sheet") == "微信文章" else "douyin"
    # 来源计数取筛选前的合并列表，保证药丸数字与卡片口径一致
    source_counts = {"douyin": 0, "wechat": 0}
    for v in videos:
        source_counts[v["source"]] = source_counts.get(v["source"], 0) + 1
    if source in ("douyin", "wechat"):
        videos = [v for v in videos if v["source"] == source]
    if topic:
        videos = [v for v in videos if v.get("author", "") == topic]
    if category:
        want = category if category != "未分类" else ""
        videos = [v for v in videos if (v.get("category") or "") == want]
    if game:
        want_game = "" if game == "未识别游戏" else game
        videos = [v for v in videos if (v.get("game") or "") == want_game]
    if q:
        # 按空白分词、逐词 AND 匹配（标题/作者/描述/分类/标签/游戏/工作表/行号）。
        # 整串关键词或粘贴整行卡片（如"…送英雄装 游戏攻略 泰莉亚子 抖音视频数据 R404"）都能命中；
        # 纯符号/emoji 词直接忽略，R404 / 404 均可命中行号。
        import re as _re
        tokens = [t.lower() for t in q.split() if _re.search(r"\w", t)]
        if tokens:
            def _haystack(v):
                return " ".join([
                    v.get("title", ""), v.get("author", ""), v.get("description", ""),
                    v.get("category", ""), v.get("game", ""), v.get("ai_tags", ""),
                    v.get("sheet", ""), f"R{v.get('row', '')}", str(v.get("row", "")),
                ]).lower()
            videos = [v for v in videos if all(t in _haystack(v) for t in tokens)]
    # 按时间最新在前（发布时间优先，其次创建时间；空值垫底）
    videos.sort(
        key=lambda v: (v.get("published_at") or v.get("create_time") or v.get("pub_time") or ""),
        reverse=True,
    )
    return jsonify({"videos": videos, "total": len(videos), "sources": source_counts})


def _load_video_extras() -> dict:
    """从 SQLite 取分类/标签与最新整理稿类型，键为 (sheet, row)。"""
    if not DB_PATH.exists():
        return {}
    try:
        with _db_lock:
            conn = content_store.connect(DB_PATH)
            try:
                rows = conn.execute(
                    "SELECT id, source_sheet, source_row, category, ai_tags, game FROM videos"
                ).fetchall()
                # 每条整理稿的类型（含合集稿的全部源视频）：id 倒序第一个出现的即最新，供列表标注「已整理」
                summary_rows = conn.execute(
                    "SELECT video_id, source_video_ids, summary_type FROM ai_summaries "
                    "WHERE summary_type IN ('wechat_material', 'game_guide', 'ai_interview', 'guide_article', 'toutiao_mix') "
                    "ORDER BY id DESC"
                ).fetchall()
                involved_ids = set()
                parsed = []
                for r in summary_rows:
                    ids = {int(r["video_id"])}
                    try:
                        ids.update(int(x) for x in json.loads(r["source_video_ids"] or "[]"))
                    except (ValueError, TypeError):
                        pass
                    parsed.append((ids, r["summary_type"]))
                    involved_ids.update(ids)
                id_key_map = {}
                if involved_ids:
                    placeholders = ",".join("?" * len(involved_ids))
                    id_rows = conn.execute(
                        f"SELECT id, source_sheet, source_row FROM videos WHERE id IN ({placeholders})",
                        tuple(involved_ids),
                    ).fetchall()
                    id_key_map = {r["id"]: (r["source_sheet"], r["source_row"]) for r in id_rows}
            finally:
                conn.close()
        latest_summary = {}
        for ids, summary_type in parsed:
            for vid in ids:  # 合集稿的每个源视频都标注（否则第二篇视频显示无整理稿，取材时踩空）
                key = id_key_map.get(vid)
                if key and key not in latest_summary:
                    latest_summary[key] = summary_type
        return {
            (r["source_sheet"], r["source_row"]): {
                "id": r["id"],
                "category": r["category"] or "",
                "ai_tags": r["ai_tags"] or "",
                "game": r["game"] or "",
                "summary_type": latest_summary.get((r["source_sheet"], r["source_row"]), ""),
            }
            for r in rows
        }
    except Exception:
        return {}


# ── API: 分类统计 ──
@app.route("/api/categories")
def api_categories():
    if not DB_PATH.exists():
        return jsonify({"categories": [], "games": []})
    try:
        with _db_lock:
            stats = content_store.get_category_stats(DB_PATH)
            games = content_store.get_game_stats(DB_PATH)
    except Exception as e:
        return jsonify({"categories": [], "games": [], "error": str(e)}), 500
    return jsonify({"categories": stats, "games": games})


# ── API: 选题雷达 ──
@app.route("/api/topics/radar")
def api_topic_radar():
    """选题雷达：标签热度聚合。category/game/author 可选筛选，channel 切换渠道策略。"""
    category = (request.args.get("category") or "").strip()
    game = (request.args.get("game") or "").strip()
    author = (request.args.get("author") or "").strip()
    channel = (request.args.get("channel") or "").strip()
    if channel and channel not in content_store.RADAR_CHANNEL_STRATEGY:
        channel = ""
    if not DB_PATH.exists():
        return jsonify({"topics": [], "total_videos": 0, "window_days": 90})
    try:
        with _db_lock:
            data = content_store.get_topic_radar(
                DB_PATH, category, game, author, channel=channel
            )
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── API: 渠道策略配置下发（自媒体工作台策略引擎的单一权威源） ──
@app.route("/api/strategy/channels")
def api_strategy_channels():
    """工作台前端策略引擎（channel-strategy.js）从这里拉取策略口径。"""
    return jsonify(content_store.get_channel_strategy_config())


# ── API: AI 批量分类 ──
def _auto_classify_after_sync() -> dict:
    """同步入库后的增量自动打标：只补 category/ai_tags 任一为空的视频。

    - AI 未配置（method=skip 或无 key）时静默返回空结果，不阻塞采集主流程；
    - 沿用 classify_videos 的 LLM 优先、失败降级规则分类；
    - 调用方在采集线程里，此函数同步执行（单条链接量小可接受）。
      持锁口径与 /api/videos/classify 一致。
    """
    try:
        cfg = _ai_config()
        if cfg.get("method") == "skip" or not cfg.get("api_key"):
            return {"total": 0, "classified": 0, "llm_batches": 0, "skipped": "ai_not_configured"}
        with _db_lock:
            return content_store.classify_videos(DB_PATH, cfg, force=False)
    except Exception as e:
        # 打标失败不算采集失败：记日志，返回空结果让主流程继续
        print(f"[auto-classify] 增量打标失败（不影响采集）: {e}", flush=True)
        return {"total": 0, "classified": 0, "llm_batches": 0, "error": str(e)}


@app.route("/api/videos/classify", methods=["POST"])
def api_classify_videos():
    if _classify_status.get("running"):
        return jsonify({"error": "分类任务进行中，请稍候"}), 409
    force = bool((request.get_json(force=True, silent=True) or {}).get("force"))

    def run():
        _classify_status.update(running=True, done=False, error="", progress="开始分类...", result=None)
        try:
            def cb(done, total, note):
                _classify_status["progress"] = note
            with _db_lock:
                result = content_store.classify_videos(DB_PATH, _ai_config(), force=force, progress_cb=cb)
            _classify_status["result"] = result
            _classify_status["progress"] = f"完成：{result['classified']}/{result['total']} 条已分类"
        except Exception as e:
            _classify_status["error"] = f"{e}\n{traceback.format_exc()[-500:]}"
        finally:
            _classify_status["running"] = False
            _classify_status["done"] = True

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"success": True})


@app.route("/api/classify/status")
def api_classify_status():
    return jsonify(_classify_status)


# ── API: 手动修改分类 ──
@app.route("/api/videos/category", methods=["POST"])
def api_update_video_category():
    data = request.get_json(force=True)
    sheet = (data.get("sheet") or "").strip()
    row = data.get("row")
    category = (data.get("category") or "").strip()
    game = data.get("game")
    if not sheet or not row:
        return jsonify({"error": "缺少 sheet/row"}), 400
    try:
        with _db_lock:
            conn = content_store.connect(DB_PATH)
            try:
                r = conn.execute(
                    "SELECT id FROM videos WHERE source_sheet = ? AND source_row = ?", (sheet, row)
                ).fetchone()
            finally:
                conn.close()
        if not r:
            return jsonify({"error": "视频不存在于内容库，请先同步内容库"}), 404
        content_store.update_video_category(DB_PATH, r["id"], category, game=game)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── API: 视频详情 ──
@app.route("/api/videos/<sheet>/<int:row>")
def api_video_detail(sheet, row):
    # 微信文章分支：不落 Excel，DB 是权威源；响应结构与抖音视频同构，前端弹窗零特判
    if sheet == "微信文章":
        if not DB_PATH.exists():
            return jsonify({"error": "内容库不存在"}), 404
        video = content_store.get_video_by_source(DB_PATH, sheet, row)
        if not video:
            return jsonify({"error": f"微信文章 R{row} 不存在"}), 404
        body = video.get("transcript") or ""
        cover = video.get("cover_url") or _first_markdown_image(body)
        detail = {
            "link": video.get("source_url") or "",
            "video_url": video.get("source_url") or "",
            "status": video.get("status") or "",
            "id": "",
            "author": video.get("author") or "",
            "pub_time": video.get("published_at") or "",
            "title": video.get("title") or "",
            "asr": body,
            "raw_transcript": "",
            "tags": video.get("ai_tags") or "",
            "cover": cover,
            "ai_copy": "",
            "ai_title": "",
            "keywords": "",
            "remark": "",
            "sheet": sheet,
            "row": row,
            "keyframes": [],
            "source": "wechat",
        }
        _attach_related_summaries(detail, sheet, row)
        return jsonify(detail)

    import openpyxl
    if not EXCEL_PATH.exists():
        return jsonify({"error": "Excel 不存在"}), 404
    wb = openpyxl.load_workbook(str(EXCEL_PATH), read_only=True)
    try:
        ws = wb[sheet]
    except KeyError:
        return jsonify({"error": f"Sheet '{sheet}' 不存在"}), 404
    if row < 2 or row > ws.max_row:
        return jsonify({"error": f"行号 {row} 超出范围"}), 404
    cols = {"link":1,"status":2,"id":3,"author":4,"pub_time":5,"title":6,
            "asr":7,"tags":8,"cover":9,"video_url":10,"raw_transcript":11,
            "ai_copy":12,"ai_title":13,"keywords":14,"remark":15}
    detail = {k: str(ws.cell(row, c).value or "") for k, c in cols.items()}
    detail["sheet"] = sheet
    detail["row"] = row
    detail["source"] = "douyin"
    wb.close()
    # 已抽取的关键帧（生成攻略文章时落盘），供自媒体工作台取材配图
    try:
        aweme_id = material_store.extract_aweme_id(detail.get("link") or detail.get("video_url") or "")
        frame_paths = [p for p in material_store.local_keyframe_paths(aweme_id) if not p.endswith("sheet.jpg")]
        detail["keyframes"] = [f"/media/{p}" for p in frame_paths]
    except Exception:
        detail["keyframes"] = []
    # 该视频相关的最新整理稿（wechat_material/game_guide/ai_interview），供工作台取材时优先于原始转写。
    # 含两种挂载：本视频直接挂的稿 + 本视频作为源视频之一的合集稿（source_video_ids）。
    # 整理稿是素材底稿不是成品文章：工作台取材后仍需生成+人工润色才能发布
    _attach_related_summaries(detail, sheet, row)
    return jsonify(detail)


def _attach_related_summaries(detail: dict, sheet: str, row: int) -> None:
    """把与该素材相关的最新整理稿按类型挂到 detail 上（抖音视频与微信文章共用）。"""
    try:
        if DB_PATH.exists():
            video = content_store.get_video_by_source(DB_PATH, sheet, row)
            if video:
                with _db_lock:
                    conn = content_store.connect(DB_PATH)
                    summary_rows = conn.execute(
                        "SELECT video_id, source_video_ids, summary_type, title, content "
                        "FROM ai_summaries "
                        "WHERE summary_type IN ('wechat_material', 'game_guide', 'ai_interview', 'guide_article', 'toutiao_mix') "
                        "ORDER BY id DESC"
                    ).fetchall()
                    conn.close()
                vid = video["id"]
                related = []
                for summary_row in summary_rows:
                    if summary_row["video_id"] == vid:
                        related.append(summary_row)
                        continue
                    try:
                        source_ids = json.loads(summary_row["source_video_ids"] or "[]")
                    except (ValueError, TypeError):
                        source_ids = []
                    if vid in source_ids:  # 合集稿的源视频之一 → 同样可用该合集稿取材
                        related.append(summary_row)
                latest = {}
                for summary_row in related:  # id 倒序 → 每类只留最新一条
                    if summary_row["summary_type"] not in latest:
                        latest[summary_row["summary_type"]] = summary_row
                for summary_type in ("wechat_material", "game_guide", "ai_interview", "toutiao_mix"):
                    summary_row = latest.get(summary_type)
                    if summary_row:
                        detail[summary_type] = summary_row["content"] or ""
                        detail[summary_type + "_title"] = summary_row["title"] or ""
                # summary_type/summary_title：按取材优先级（素材档案 > 攻略整理 > 面试题 > 头条整合）给首选整理稿
                for summary_type in ("wechat_material", "game_guide", "ai_interview", "toutiao_mix"):
                    if detail.get(summary_type):
                        detail["summary_type"] = summary_type
                        detail["summary_title"] = detail[summary_type + "_title"]
                        break
    except Exception:
        detail["wechat_material"] = ""


@app.route("/media/<path:filename>")
def api_media(filename):
    """Serve local output assets such as covers and keyframes."""
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/api/workbench")
def api_workbench():
    try:
        if not DB_PATH.exists():
            _sync_content_db()
        q = request.args.get("q", "")
        author = request.args.get("author", "")
        status = request.args.get("status", "")
        with _db_lock:
            items = material_store.list_workbench(DB_PATH, query=q, author=author, status=status)
        return jsonify({"items": items, "total": len(items)})
    except Exception as e:
        return jsonify({"items": [], "total": 0, "error": str(e)}), 500


@app.route("/api/rebuild-index", methods=["POST"])
def api_rebuild_index():
    """手动重建 video_index.json（读取 Excel + SQLite 全量数据）"""
    try:
        collector.update_video_index(str(EXCEL_PATH))
        _sync_content_db()
        count = len(json.load(open(INDEX_PATH, encoding="utf-8"))) if INDEX_PATH.exists() else 0
        return jsonify({"success": True, "message": f"索引已重建，共 {count} 条", "count": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/workbench/sync", methods=["POST"])
def api_workbench_sync():
    try:
        result = _sync_material_workspace()
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── API: 处理状态 ──
@app.route("/api/status")
def api_status():
    return jsonify(_task_status)

# ── API: 统计 ──
@app.route("/api/stats")
def api_stats():
    if not INDEX_PATH.exists():
        return jsonify({"topics": {}, "total": 0})
    with open(INDEX_PATH, encoding="utf-8") as f:
        data = json.load(f)
    topics = {}
    for v in data.get("videos", []):
        author = v.get("author", "未知作者") or "未知作者"
        topics[author] = topics.get(author, 0) + 1
    return jsonify({"topics": topics, "total": data.get("total", 0),
                     "version": data.get("version")})


# ── API: 工作台仪表盘 ──
@app.route("/api/dashboard")
def api_dashboard():
    """聚合 SQLite 内容库统计，供首页仪表盘使用。"""
    try:
        if not DB_PATH.exists():
            _sync_content_db()
        with _db_lock:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            try:
                total = conn.execute("SELECT COUNT(*) AS c FROM videos").fetchone()["c"]
                with_asr = conn.execute(
                    "SELECT COUNT(*) AS c FROM videos WHERE transcript IS NOT NULL AND transcript != ''"
                ).fetchone()["c"]
                authors = conn.execute(
                    "SELECT author AS name, COUNT(*) AS c FROM videos "
                    "GROUP BY author ORDER BY c DESC LIMIT 8"
                ).fetchall()
                summary_rows = conn.execute(
                    "SELECT summary_type, COUNT(*) AS c FROM ai_summaries GROUP BY summary_type"
                ).fetchall()
                recent_videos = conn.execute(
                    "SELECT title, author, status, updated_at FROM videos "
                    "ORDER BY updated_at DESC, id DESC LIMIT 6"
                ).fetchall()
                recent_summaries = conn.execute(
                    "SELECT id, title, summary_type, status, updated_at FROM ai_summaries "
                    "ORDER BY updated_at DESC, id DESC LIMIT 6"
                ).fetchall()
                question_count = 0
                for row in conn.execute(
                    "SELECT structured_data FROM ai_summaries WHERE summary_type = 'ai_interview'"
                ).fetchall():
                    try:
                        payload = json.loads(row["structured_data"] or "{}")
                        question_count += len(payload.get("questions") or [])
                    except Exception:
                        pass
            finally:
                conn.close()

        summaries = {row["summary_type"]: row["c"] for row in summary_rows}
        return jsonify({
            "total": total,
            "with_asr": with_asr,
            "missing_asr": max(0, total - with_asr),
            "asr_rate": round(with_asr * 100 / total, 1) if total else 0,
            "authors": [{"name": r["name"] or "未知作者", "count": r["c"]} for r in authors],
            "summaries": {
                "game_guide": summaries.get("game_guide", 0),
                "ai_interview": summaries.get("ai_interview", 0),
                "wechat_material": summaries.get("wechat_material", 0),
            },
            "question_count": question_count,
            "recent_videos": [dict(r) for r in recent_videos],
            "recent_summaries": [dict(r) for r in recent_summaries],
            "running": _task_status.get("running", False),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── API: 提交处理 ──
@app.route("/api/process", methods=["POST"])
def api_process():
    global _task_status
    if _task_status["running"]:
        return jsonify({"error": "已有任务正在运行"}), 400

    data = request.get_json(force=True)
    links_text = data.get("links", "").strip()
    cookie = data.get("cookie", "").strip()
    asr_model = data.get("asr_model", "base")

    if not links_text:
        return jsonify({"error": "请输入抖音链接"}), 400
    if not cookie:
        return jsonify({"error": "请输入 Cookie"}), 400

    _task_status = {"running": True, "progress": "准备中...", "done": False, "error": ""}

    def run():
        global _task_status
        import openpyxl
        try:
            # 1. 初始化 parser
            _task_status["progress"] = "初始化解析器..."
            parser = _setup_parser_and_cookie(cookie)

            # 2. 写入链接到 Excel
            _task_status["progress"] = "写入链接到 Excel..."
            wb = openpyxl.load_workbook(str(EXCEL_PATH))
            ws = wb["抖音视频数据"]
            next_row = ws.max_row + 1
            # 找第一个空行
            for r in range(2, ws.max_row + 1):
                if not ws.cell(r, 1).value:
                    next_row = r
                    break
            link_list = [l.strip() for l in links_text.split("\n") if l.strip()]
            for i, link in enumerate(link_list):
                ws.cell(int(next_row) + i, 1).value = link
                ws.cell(int(next_row) + i, 2).value = "未开始"
            wb.save(str(EXCEL_PATH))
            wb.close()

            # 3. 处理每条链接（重新打开 Excel 让 process_row 操作）
            ai_config = {"method": "skip", "api_key": "", "api_base": "", "model": ""}

            for i, link in enumerate(link_list):
                row = int(next_row) + i
                _task_status["progress"] = f"处理 [{i+1}/{len(link_list)}] 第 {row} 行..."

                wb = openpyxl.load_workbook(str(EXCEL_PATH))
                ws = wb["抖音视频数据"]
                ok = collector.process_row(ws, row, cookie, asr_model, ai_config, parser)
                wb.save(str(EXCEL_PATH))
                wb.close()

                if not ok:
                    # 失败原因写在 Excel 备注列，带回前端（不再只报“处理失败”）
                    reason = ""
                    try:
                        wb2 = openpyxl.load_workbook(str(EXCEL_PATH), read_only=True)
                        reason = (wb2["抖音视频数据"].cell(row, 15).value or "").strip()
                        wb2.close()
                    except Exception:
                        pass
                    _task_status["error"] += f"Row {row}: 处理失败（{reason or '无备注'}）\n"

                if i < len(link_list) - 1:
                    import time
                    time.sleep(5)

            # 4. 更新索引 + 增量自动打标
            _task_status["progress"] = "更新索引..."
            collector.update_video_index(str(EXCEL_PATH))
            _sync_content_db()
            # 5. 标记来源：采集中心单链接入库的行（source=source_single_link）
            with _db_lock:
                content_store.mark_video_source(
                    DB_PATH, "抖音视频数据",
                    list(range(int(next_row), int(next_row) + len(link_list))),
                    "single_link",
                )

            _task_status["progress"] = "AI 自动打标..."
            _cls = _auto_classify_after_sync()
            if _cls.get("classified"):
                _task_status["progress"] = f"AI 打标 {_cls['classified']}/{_cls['total']} 条完成"

            _task_status["progress"] = "全部处理完成 ✅"
            _task_status["done"] = True

        except Exception as e:
            _task_status["error"] = traceback.format_exc()
            _task_status["done"] = True
        finally:
            _task_status["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})

# ── 前端页面 ──
# ── API: 批量获取用户视频列表 ──────────────────────────────────────
@app.route("/api/fetch_user_videos", methods=["POST"])
def api_fetch_user_videos():
    data = request.get_json(force=True)
    url = (data.get("user_url") or data.get("video_url") or "").strip()
    cookie = data.get("cookie", "").strip()
    max_pages = int(data.get("max_pages", 10))
    max_videos = int(data.get("max_videos") or 0)

    if not url:
        return jsonify({"error": "请输入用户主页链接或视频链接"}), 400
    if not cookie:
        return jsonify({"error": "请输入 Cookie"}), 400

    try:
        from src.fetch_user_videos import fetch_user_videos
        mode = "user_url" if "/user/" in url else "video_url"
        result = fetch_user_videos(
            url=url, cookie=cookie,
            max_pages=max_pages, max_videos=max_videos, mode=mode,
            exclude_excel=str(EXCEL_PATH),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500


# ── API: 预览用户信息（不写入Excel，只获取列表） ────────────────────
@app.route("/api/preview_user_videos", methods=["POST"])
def api_preview_user_videos():
    """获取用户视频列表预览，不写入 Excel"""
    data = request.get_json(force=True)
    url = (data.get("user_url") or data.get("video_url") or "").strip()
    cookie = data.get("cookie", "").strip()
    max_videos = int(data.get("max_videos") or 0)
    max_pages = int(data.get("max_pages", 1))
    if max_videos <= 0:
        max_pages = min(max_pages, 5)

    # DEBUG LOG
    import json as _j
    print(f"[DEBUG] preview_user_videos: url={url[:50]}... cookie={cookie[:20]}... max_pages={max_pages} max_videos={max_videos}", flush=True)

    if not url:
        return jsonify({"error": "请输入链接"}), 400
    if not cookie:
        return jsonify({"error": "请输入 Cookie"}), 400

    try:
        from src.fetch_user_videos import fetch_user_videos
        mode = "user_url" if "/user/" in url else "video_url"
        result = fetch_user_videos(
            url=url, cookie=cookie,
            max_pages=max_pages, max_videos=max_videos, mode=mode,
            exclude_excel=str(EXCEL_PATH),
        )
        # 如果 total=0，给出提示（但如果是去重导致的，不提示）
        if result.get('total', 0) == 0 and not result.get('error'):
            # 检查是否是去重导致的
            if result.get('filtered'):
                result['warning'] = f"所有视频已存在（去重 {result.get('filtered')} 条）"
            elif mode == "user_url":
                # ── 浏览器兜底（2026-09-21）：抖音 Argus 拦截纯 API 签名，
                # 旧路径拉空时改用真实 Chromium 打开主页抓取 ──
                try:
                    from src.browser_fetch import browser_fetch_user_videos
                    from src.fetch_user_videos import load_existing_aweme_ids
                    ids = browser_fetch_user_videos(url, max_videos=max_videos or 20)
                    if ids:
                        existing = load_existing_aweme_ids(str(EXCEL_PATH))
                        vids = [i for i in ids if i not in existing]
                        result['videos'] = [
                            {"aweme_id": i, "url": "https://www.douyin.com/video/" + i}
                            for i in vids
                        ]
                        result['total'] = len(vids)
                        result['user_url'] = url
                        result['success'] = True
                        if not vids:
                            result['warning'] = f"浏览器拉到 {len(ids)} 条，但全部已存在（去重过滤）"
                    else:
                        result['warning'] = "视频数为0（浏览器兜底也未拉到），请检查链接或稍后重试"
                except Exception:
                    result['warning'] = "视频数为0，请检查Cookie是否有效或已过期"
                if not result.get('videos'):
                    return jsonify(result)
            else:
                result['warning'] = "视频数为0，请检查Cookie是否有效或已过期"
                return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e), "success": False}), 500


# ── API: 批量获取 + 写入 Excel + 自动处理（全自动流水线） ─────────
@app.route("/api/fetch_and_process", methods=["POST"])
def api_fetch_and_process():
    """
    完整流水线：
    1. 获取用户主页视频列表
    2. 写入 Excel（状态=未开始）
    3. 后台逐条 ASR 处理
    4. 更新索引
    """
    global _task_status
    if _task_status["running"]:
        return jsonify({"error": "已有任务正在运行，请等待完成"}), 400

    data = request.get_json(force=True)
    url = (data.get("user_url") or data.get("video_url") or "").strip()
    cookie = data.get("cookie", "").strip()
    max_pages = int(data.get("max_pages", 10))
    max_videos = int(data.get("max_videos") or 0)
    asr_model = data.get("asr_model", "base")
    # 前端预览阶段已抓取的视频列表（可选，传入则跳过重复抓取）
    preset_videos = data.get("videos") or []

    if not url and not preset_videos:
        return jsonify({"error": "请输入用户主页链接或视频链接"}), 400
    if not cookie:
        return jsonify({"error": "请输入 Cookie"}), 400

    _task_status = {"running": True, "progress": "准备中...", "done": False, "error": "", "total": 0, "current": 0}

    def run():
        global _task_status
        import openpyxl, time
        try:
            # 1. 获取视频列表（优先使用前端预览结果）
            if preset_videos:
                videos = preset_videos
                _task_status["progress"] = f"使用预览列表 {len(videos)} 条..."
            else:
                _task_status["progress"] = "获取视频列表..."
                from src.fetch_user_videos import fetch_user_videos
                mode = "user_url" if "/user/" in url else "video_url"
                result = fetch_user_videos(url=url, cookie=cookie, max_pages=max_pages, max_videos=max_videos, mode=mode,
                                   exclude_excel=str(EXCEL_PATH))
                if not result.get("success") or result.get("total", 0) == 0:
                    _task_status["error"] = f"获取视频列表失败: {result.get('error', '0条视频')}"
                    _task_status["done"] = True
                    _task_status["running"] = False
                    return
                videos = result["videos"]
            _task_status["total"] = len(videos)
            _task_status["progress"] = f"写入 {len(videos)} 条视频到 Excel..."

            # 2. 写入 Excel
            wb = openpyxl.load_workbook(str(EXCEL_PATH))
            ws = wb["抖音视频数据"]
            next_row = ws.max_row + 1
            for r in range(2, ws.max_row + 1):
                if not ws.cell(r, 1).value:
                    next_row = r
                    break

            for i, v in enumerate(videos):
                ws.cell(next_row + i, 1).value = v["url"]
                ws.cell(next_row + i, 2).value = "未开始"
                ws.cell(next_row + i, 3).value = v.get("aweme_id", "")

            wb.save(str(EXCEL_PATH))
            wb.close()

            # 3. 初始化 parser
            _task_status["progress"] = "初始化解析器..."
            parser = _setup_parser_and_cookie(cookie)
            ai_config = {"method": "skip", "api_key": "", "api_base": "", "model": ""}

            # 4. 逐条 ASR 处理
            for i, v in enumerate(videos):
                row = next_row + i
                _task_status["current"] = i + 1
                _task_status["progress"] = f"处理 [{i+1}/{len(videos)}] {v.get('aweme_id','')}..."

                wb = openpyxl.load_workbook(str(EXCEL_PATH))
                ws = wb["抖音视频数据"]
                ok = collector.process_row(ws, row, cookie, asr_model, ai_config, parser)
                wb.save(str(EXCEL_PATH))
                wb.close()

                if not ok:
                    # 失败原因写在 Excel 备注列，带回前端（不再只报“处理失败”）
                    reason = ""
                    try:
                        wb2 = openpyxl.load_workbook(str(EXCEL_PATH), read_only=True)
                        reason = (wb2["抖音视频数据"].cell(row, 15).value or "").strip()
                        wb2.close()
                    except Exception:
                        pass
                    _task_status["error"] += f"Row {row}: 处理失败（{reason or '无备注'}）\n"

                if i < len(videos) - 1:
                    time.sleep(5)

            # 5. 更新索引 + 增量自动打标
            _task_status["progress"] = "更新索引..."
            collector.update_video_index(str(EXCEL_PATH))
            _sync_content_db()
            # 6. 标记来源：采集中心主页批量抓取（手动入口）
            with _db_lock:
                content_store.mark_video_source(
                    DB_PATH, "抖音视频数据",
                    list(range(int(next_row), int(next_row) + len(videos))),
                    "single_link",
                )

            _task_status["progress"] = "AI 自动打标..."
            _cls = _auto_classify_after_sync()
            if _cls.get("classified"):
                _task_status["progress"] = f"AI 打标 {_cls['classified']}/{_cls['total']} 条完成"

            _task_status["progress"] = f"✅ 全部 {len(videos)} 条处理完成！"
            _task_status["done"] = True

        except Exception as e:
            _task_status["error"] = traceback.format_exc()
            _task_status["done"] = True
        finally:
            _task_status["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})


# ── API: SQLite 内容库 ────────────────────────────────────────────
@app.route("/api/content/sync", methods=["POST"])
def api_content_sync():
    try:
        result = _sync_content_db()
        return jsonify({"success": True, **result, "db_path": str(DB_PATH)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/content/summaries")
def api_content_summaries():
    try:
        if not DB_PATH.exists():
            _sync_content_db()
        summary_type = request.args.get("type", "")
        q = request.args.get("q", "")
        items = content_store.list_summaries(DB_PATH, summary_type=summary_type, query=q)
        return jsonify({"items": items, "total": len(items)})
    except Exception as e:
        return jsonify({"error": str(e), "items": [], "total": 0}), 500


@app.route("/api/content/summaries/<int:summary_id>")
def api_content_summary_detail(summary_id):
    try:
        item = content_store.get_summary(DB_PATH, summary_id)
        if not item:
            return jsonify({"error": "总结不存在"}), 404
        return jsonify(item)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/content/summaries/<int:summary_id>/regenerate", methods=["POST"])
def api_content_regenerate(summary_id):
    """重新生成指定整理稿。"""
    try:
        summary = content_store.get_summary(DB_PATH, summary_id)
        if not summary:
            return jsonify({"error": "整理稿不存在"}), 404

        summary_type = request.args.get("type") or summary.get("summary_type", "game_guide")
        config = _ai_config()
        source_ids = json.loads(summary.get("source_video_ids") or "[]")
        if len(source_ids) > 1:
            videos = content_store.get_videos_by_ids(DB_PATH, [int(v) for v in source_ids])
            if not videos:
                return jsonify({"error": "关联视频不存在"}), 404
            result, model, status = content_store.generate_collection_summary(
                videos, summary_type, config
            )
            content_store.update_summary(
                DB_PATH, summary_id, result, model, status, source_video_ids=[int(v["id"]) for v in videos]
            )
        else:
            video = content_store.get_video_by_source(DB_PATH, summary["source_sheet"], summary["source_row"])
            if not video:
                return jsonify({"error": "关联视频不存在"}), 404
            result, model, status = content_store.generate_summary(video, summary_type, config)
            content_store.update_summary(
                DB_PATH, summary_id, result, model, status, source_video_ids=[int(video["id"])]
            )

        updated = content_store.get_summary(DB_PATH, summary_id)
        return jsonify({"success": True, "summary": updated})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500



@app.route("/api/content/summaries/<int:summary_id>", methods=["DELETE"])
def api_content_delete(summary_id):
    """删除指定整理稿"""
    try:
        content_store.delete_summary(DB_PATH, summary_id)
        return jsonify({"success": True, "deleted": summary_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/content/generate", methods=["POST"])
def api_content_generate():
    """把选中的视频整理成游戏攻略或 AI 面试题，并保存到 SQLite。"""
    global _task_status
    if _task_status.get("running"):
        return jsonify({"error": "已有任务正在运行"}), 400

    data = request.get_json(force=True)
    videos = data.get("videos", [])
    summary_type = data.get("summary_type", "game_guide")
    combine = bool(data.get("combine", False))
    if summary_type not in ("game_guide", "ai_interview", "wechat_material", "toutiao_mix"):
        return jsonify({"error": "summary_type 只能是 game_guide / ai_interview / wechat_material / toutiao_mix（攻略成品文章走自媒体工作台生成）"}), 400
    if not videos:
        return jsonify({"error": "请选择要整理的视频"}), 400

    _task_status = {
        "running": True,
        "progress": "准备生成内容...",
        "done": False,
        "error": "",
        "success": 0,
        "summary_ids": [],
    }

    def run():
        global _task_status
        try:
            _task_status["progress"] = "同步 Excel 到内容库..."
            _sync_content_db()
            config = _ai_config()
            success_count = 0
            summary_ids = []
            selected_videos = []

            for i, key in enumerate(videos):
                try:
                    sheet, row_text = key.split(":", 1)
                    row = int(row_text)
                    _task_status["progress"] = f"读取 [{i+1}/{len(videos)}] {sheet} 第{row}行..."
                    with _db_lock:
                        video = content_store.get_video_by_source(DB_PATH, sheet, row)
                    if not video:
                        _task_status["error"] += f"{key}: 内容库中找不到视频\n"
                        continue
                    if not (video.get("transcript") or "").strip():
                        _task_status["error"] += f"{key}: 没有 ASR 文本，跳过\n"
                        continue
                    selected_videos.append(video)
                except Exception as e:
                    _task_status["error"] += f"{key}: {e}\n"

            if combine:
                if not selected_videos:
                    _task_status["progress"] = "没有可生成的 ASR 文本"
                    _task_status["done"] = True
                    return

                _task_status["progress"] = f"合并整理 {len(selected_videos)} 条视频..."
                result, model, status = content_store.generate_collection_summary(
                    selected_videos, summary_type, config
                )
                if summary_type in GUIDE_SUMMARY_TYPES:
                    # 攻略类：抽关键帧做配图，嵌入整理稿尾部
                    _task_status["progress"] = "抽取视频关键帧配图..."
                    result["content"] = (result.get("content") or "") + _video_keyframe_markdown(selected_videos)
                source_ids = [int(v["id"]) for v in selected_videos]
                with _db_lock:
                    summary_id = content_store.save_summary(
                        DB_PATH,
                        video_id=source_ids[0],
                        summary_type=summary_type,
                        result=result,
                        model=model,
                        status=status,
                        source_video_ids=source_ids,
                    )
                summary_ids.append(summary_id)
                success_count = 1
            else:
                for i, video in enumerate(selected_videos):
                    _task_status["progress"] = f"整理 [{i+1}/{len(selected_videos)}] 第{video['source_row']}行..."

                    result, model, status = content_store.generate_summary(
                        video, summary_type, config
                    )
                    if summary_type in GUIDE_SUMMARY_TYPES:
                        # 攻略类：抽关键帧做配图，嵌入整理稿尾部
                        _task_status["progress"] = f"抽取关键帧配图 [{i+1}/{len(selected_videos)}] 第{video['source_row']}行..."
                        result["content"] = (result.get("content") or "") + _video_keyframe_markdown([video])
                    with _db_lock:
                        summary_id = content_store.save_summary(
                            DB_PATH,
                            video_id=video["id"],
                            summary_type=summary_type,
                            result=result,
                            model=model,
                            status=status,
                            source_video_ids=[int(video["id"])],
                        )
                    summary_ids.append(summary_id)
                    success_count += 1

            _task_status["success"] = success_count
            _task_status["summary_ids"] = summary_ids
            label = "合并整理稿" if combine else "整理稿"
            _task_status["progress"] = f"✅ 已生成 {success_count} 条{label}"
            _task_status["done"] = True
        except Exception:
            _task_status["error"] = traceback.format_exc()
            _task_status["done"] = True
        finally:
            _task_status["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started", "total": len(videos), "summary_type": summary_type, "combine": combine})


# ── 攻略整理稿配图：抽取视频关键帧并生成 Markdown 图片段 ──
# 成品文章在自媒体工作台生成；这里只给资料整理稿（game_guide / wechat_material 公众号素材档案）配图，供工作台取材时引用
GUIDE_SUMMARY_TYPES = ("game_guide", "wechat_material")

_extractor = None
_extractor_lock = threading.Lock()


def _load_cached_nwm_url(aweme_id: str) -> str:
    """读采集时缓存的原始解析结果（output/raw/douyin/<id>.json），免网络请求。"""
    import json as _json
    path = material_store.RAW_DIR / f"{material_store.safe_id(aweme_id)}.json"
    try:
        if path.exists():
            raw = _json.loads(path.read_text(encoding="utf-8"))
            return (raw.get("nwm_url") or raw.get("download_url") or "").strip()
    except Exception:
        pass
    return ""


def _resolve_download_url(video: dict, retries: int = 4, backoff: float = 15.0) -> str:
    """解析视频真实无水印直链：本地 raw 缓存优先，其次 douyin_video_parser 在线解析。

    Excel 里的 video_url 是抖音网页地址，curl 下载只会拿到 HTML；必须解析出
    nwm 直链 ffmpeg 才能抽帧。注意：
    1) parser 构造时按相对路径读 douyin_cookie.txt（依赖 cwd），需显式 set_cookie 注入
    2) 抖音接口有间歇性反爬限流，失败需带退避重试
    解析失败返回空串，由调用方兜底原地址。
    """
    aweme_id = (video.get("aweme_id") or "").strip()
    if not aweme_id:
        return ""
    cached = _load_cached_nwm_url(aweme_id)
    if cached:
        return cached
    global _extractor
    with _extractor_lock:
        if _extractor is None:
            try:
                from src.video_extractor import VideoExtractor
                _extractor = VideoExtractor()
                cookie = get_cookie_path().read_text(encoding="utf-8").strip()
                if cookie:
                    _extractor.parser.set_cookie(cookie)
            except Exception:
                traceback.print_exc()
                return ""
        for attempt in range(retries):
            try:
                # parse_video 的 get_video_id 只认 URL 形态，裸 aweme_id 会直接返回 None
                url = (video.get("source_url") or "").strip() or (video.get("video_url") or "").strip()
                if "douyin.com" not in url:
                    url = f"https://www.douyin.com/video/{aweme_id}"
                info = _extractor.parser.parse_video(url)
                nwm = (info.get("nwm_url") or "").strip() if info else ""
                if nwm:
                    # 解析成功即落 raw 缓存：抖音接口有短窗限流，缓存后不再走网络
                    try:
                        material_store.save_raw(aweme_id, {"nwm_url": nwm})
                    except Exception:
                        pass
                    return nwm
            except Exception:
                pass
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    return ""


def _video_keyframe_markdown(videos: list, max_frames: int = 5, per_video: int = 2) -> str:
    """对攻略类整理稿的视频抽关键帧（ffmpeg，已有帧则直接复用），返回 Markdown 配图段。

    单视频模式取最多 max_frames=5 张；合并模式每视频最多 per_video=2 张、总数截到 max_frames。
    抽帧失败（无视频地址/下载失败）时返回空串，不影响整理稿本身。
    """
    single = len(videos) <= 1
    collected: list[str] = []
    for video in videos:
        if len(collected) >= max_frames:
            break
        if (video.get("source_sheet") or "") == "微信文章":
            continue  # 微信文章无视频可抽帧；其 aweme_id 是 URL hash，不跳过会误走下载解析
        aweme_id = (video.get("aweme_id") or "").strip() or material_store.extract_aweme_id(
            video.get("source_url") or video.get("video_url") or ""
        )
        if not aweme_id:
            continue
        # 已有帧直接复用，跳过解析下载
        if not material_store.local_keyframe_paths(aweme_id):
            video_url = (video.get("video_url") or "").strip()
            nwm_url = _resolve_download_url(video)
            if nwm_url:
                material_store.extract_keyframes(aweme_id, nwm_url, max_frames=max_frames)
            elif video_url:
                material_store.extract_keyframes(aweme_id, video_url, max_frames=max_frames)
        frame_limit = per_video if not single else max_frames
        for rel in material_store.local_keyframe_paths(aweme_id):
            if rel.endswith("sheet.jpg"):
                continue
            if len(collected) >= max_frames or frame_limit <= 0:
                break
            collected.append(rel)
            frame_limit -= 1
        if single:
            break
    if not collected:
        return ""
    lines = ["", "## 配图素材（视频关键帧）", ""]
    for idx, rel in enumerate(collected, 1):
        lines.append(f"![关键帧{idx}](/media/{rel})")
    lines.append("")
    return "\n".join(lines)


# ── 头条图文：整合稿 → 信息图 + 微头条文案（复制发布保持手动，合规红线） ──
# 独立任务状态：不与 /api/content/generate 共用 _task_status，避免两条产线互相打断
_tt_task_status = {"running": False, "done": True, "progress": "", "error": "", "package": None}


@app.route("/api/toutiao/guide-upload", methods=["POST"])
def api_toutiao_guide_upload():
    """攻略图解模板：上传攻略图（最多 10 张），存 _uploads/<token>/，返回 token 供生成时引用。"""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "未选择图片"}), 400
    if len(files) > 10:
        return jsonify({"error": "一次最多上传 10 张攻略图"}), 400
    ok_ext = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in ok_ext:
            return jsonify({"error": "仅支持 jpg/png/webp/avif：%s" % (f.filename or "?")}), 400
    token = uuid.uuid4().hex[:12]
    d = OUTPUT_DIR / "toutiao" / "_uploads" / token
    d.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(files, 1):
        f.save(d / ("%02d%s" % (i, os.path.splitext(f.filename)[1].lower())))
    return jsonify({"token": token, "count": len(files)})


@app.route("/api/toutiao/generate", methods=["POST"])
def api_toutiao_generate():
    """从 toutiao_mix 整理稿生成头条图文（4 张信息图 + 微头条文案）。"""
    data = request.get_json(force=True)
    summary_id = data.get("summary_id")
    theme = str(data.get("theme") or "").strip()[:30]   # 图文卡片化题材（进 LLM prompt）
    skin = str(data.get("skin") or "wow").strip()[:30]  # 图的皮（背景资产）
    title = str(data.get("title") or "").strip()[:60]  # 图文标题（弹框可改，空则用整理稿标题）
    template = str(data.get("template") or "classic").strip()[:20]  # 图文模板（版式/字体风格）
    palette = str(data.get("palette") or "").strip()[:20]  # 配色预设键（闭集，空/未知回落默认 gold_night）
    font = str(data.get("font") or "").strip()[:20]        # 字体预设键（闭集，空/未知回落默认 serif）
    layout = str(data.get("layout") or "").strip()[:20]    # 版式预设键（闭集，空/未知回落默认 default＝现状）
    guide_imgs = []  # 攻略图解模板：弹窗上传的攻略图（优先于素材文件夹）
    guide_token = str(data.get("guide_token") or "").strip()
    if guide_token and re.fullmatch(r"[0-9a-f]{12}", guide_token):
        up = OUTPUT_DIR / "toutiao" / "_uploads" / guide_token
        if up.is_dir():
            exts = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
            guide_imgs = sorted(p for p in up.iterdir() if p.is_file() and p.suffix.lower() in exts)
    if not summary_id:
        return jsonify({"error": "缺少 summary_id"}), 400
    if _tt_task_status.get("running"):
        return jsonify({"error": "已有头条图文生成任务正在运行"}), 400

    with _db_lock:
        summary = content_store.get_summary(DB_PATH, int(summary_id))
    if not summary:
        return jsonify({"error": "整理稿不存在"}), 404
    if summary.get("summary_type") != "toutiao_mix":
        return jsonify({"error": "只支持头条整合稿（toutiao_mix）生成图文，请先生成整合稿"}), 400

    _tt_task_status.update({
        "running": True, "done": False, "progress": "准备生成头条图文...", "error": "", "package": None,
    })

    def run():
        try:
            def cb(msg):
                _tt_task_status["progress"] = msg
            _tt_task_status["progress"] = "LLM 卡片化整合稿..."
            package = toutiao_graphics.generate_graphics(summary, _fast_config(), progress_cb=cb,
                                                          theme=theme, skin=skin, title=title,
                                                          template=template, guide_images=guide_imgs,
                                                          palette=palette or None, font=font or None,
                                                          layout=layout or None)
            _tt_task_status["package"] = {"id": package["id"], "title": package["title"], "images": len(package["images"])}
            _tt_task_status["progress"] = "✅ 已生成 %d 张信息图，可复制文案发布" % len(package["images"])
        except GraphicsGateError as e:
            _tt_task_status["error"] = str(e)          # 门禁拒绝：一行说清，不刷 traceback
            _tt_task_status["progress"] = "生成失败"
        except Exception:
            _tt_task_status["error"] = traceback.format_exc()
            _tt_task_status["progress"] = "生成失败"
        finally:
            _tt_task_status["done"] = True
            _tt_task_status["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started", "summary_id": summary_id})


@app.route("/api/toutiao/status")
def api_toutiao_status():
    return jsonify(_tt_task_status)


@app.route("/api/toutiao/list")
def api_toutiao_list():
    try:
        items = toutiao_graphics.list_packages()
        return jsonify({"items": items, "total": len(items)})
    except Exception as e:
        return jsonify({"error": str(e), "items": [], "total": 0}), 500


@app.route("/api/toutiao/<int:summary_id>")
def api_toutiao_detail(summary_id):
    package = toutiao_graphics.get_package(summary_id)
    if not package:
        return jsonify({"error": "头条图文不存在"}), 404
    return jsonify(package)


@app.route("/api/toutiao/<int:summary_id>/reveal", methods=["POST"])
def api_toutiao_reveal(summary_id):
    """在 Finder 中打开该组信息图所在文件夹（macOS open / Win explorer）。"""
    import subprocess

    folder = toutiao_graphics.OUTPUT_DIR / str(summary_id)
    if not folder.exists():
        return jsonify({"error": "文件夹不存在"}), 404
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        elif os.name == "nt":
            subprocess.Popen(["explorer", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return jsonify({"success": True, "folder": str(folder)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/toutiao/<int:summary_id>", methods=["DELETE"])
def api_toutiao_delete(summary_id):
    try:
        ok = toutiao_graphics.delete_package(summary_id)
        return jsonify({"success": bool(ok)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 公众号图文：wechat_material 整合稿 + 作者真实底稿 → 3:4 卡片 + 配文/摘要/关键词回复 ──
# 独立任务状态：与 _task_status / _tt_task_status 三者刻意分离，产线互不打断（迁移方案 §5.5）
_wx_task_status = {"running": False, "done": True, "progress": "", "error": "", "package": None}


@app.route("/api/wechat/shot-upload", methods=["POST"])
def api_wechat_shot_upload():
    """真实截图上传（铁律 ≥2 张，lint 核验），存 output/wechat/_uploads/<token>/，返回 token。"""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "未选择图片"}), 400
    if len(files) > 10:
        return jsonify({"error": "一次最多上传 10 张截图"}), 400
    ok_ext = {".jpg", ".jpeg", ".png", ".webp"}
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in ok_ext:
            return jsonify({"error": "仅支持 jpg/png/webp：%s" % (f.filename or "?")}), 400
    token = uuid.uuid4().hex[:12]
    d = OUTPUT_DIR / "wechat" / "_uploads" / token
    d.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(files, 1):
        f.save(d / ("%02d%s" % (i, os.path.splitext(f.filename)[1].lower())))
    return jsonify({"token": token, "count": len(files)})


@app.route("/api/wechat/templates")
def api_wechat_templates():
    """模板与三轴预设的权威下发（key + 中文标签）。

    前端历史上自己抄了一份 4 项模板白名单，而渠道已扩到 6 项——选「浅紫清单」「奶油金」
    会被前端静默回落成 wechat。名单一律以后端 graphics 内核为准，前端不再同步维护。
    """
    try:
        return jsonify({
            "templates": wechat_graphics.template_choices(),
            "palettes": [{"key": k, "label": graphics_variants.palette_label(k)}
                         for k in graphics_variants.PALETTE_KEYS],
            "fonts": [{"key": k, "label": graphics_variants.font_label(k)}
                      for k in graphics_variants.FONT_KEYS],
            "layouts": ([{"key": k, "label": graphics_variants.layout_label(k)}
                         for k in graphics_variants.LAYOUT_KEYS]
                        + [{"key": graphics_variants.RANDOM_LAYOUT_KEY, "label": "随机（整包统一）"}]),
            # 配色轴就绪的模板：未接通的模板选了配色会 no-op，前端据此如实提示而非猜
            "palette_ready": [k for k in wechat_graphics.TEMPLATE_WHITELIST
                              if k in graphics_variants.PALETTE_READY_TPLS],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/wechat/preview", methods=["POST"])
def api_wechat_preview():
    """样式预览：按当前「模板 + 配色 + 字体 + 版式」真实渲染固定样例卡。

    不调 LLM、不进发布包，结果按参数哈希缓存在 output/wechat/_previews/<hash>/。
    旧版预览是离线假样张 JPG，换三轴毫无变化，「选了没效果」只能等成品才知道。
    """
    data = request.get_json(force=True) or {}
    try:
        out = wechat_graphics.render_preview(
            template=str(data.get("template") or "wechat"),
            palette=(data.get("palette") or None),
            font=(data.get("font") or None),
            layout=(data.get("layout") or None),
            pages=data.get("pages") or 1)
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/wechat/ref-upload", methods=["POST"])
def api_wechat_ref_upload():
    """排版参考图上传（后续「逆向拆解排版」的输入），存 output/wechat/_refs/<token>/。
    与真实截图分目录：参考图不进发布包、不参与 lint，只在 manifest 记 ref_images。"""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "未选择图片"}), 400
    if len(files) > 20:
        return jsonify({"error": "一次最多上传 20 张参考图"}), 400
    ok_ext = {".jpg", ".jpeg", ".png", ".webp"}
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in ok_ext:
            return jsonify({"error": "仅支持 jpg/png/webp：%s" % (f.filename or "?")}), 400
    token = uuid.uuid4().hex[:12]
    d = OUTPUT_DIR / "wechat" / "_refs" / token
    d.mkdir(parents=True, exist_ok=True)
    urls = []
    for i, f in enumerate(files, 1):
        name = "%02d%s" % (i, os.path.splitext(f.filename)[1].lower())
        f.save(d / name)
        urls.append("/media/wechat/_refs/%s/%s" % (token, name))
    return jsonify({"token": token, "count": len(files), "urls": urls})


@app.route("/api/wechat/generate", methods=["POST"])
def api_wechat_generate():
    """从 wechat_material 整合稿生成公众号图片消息卡片包。
    底稿可选：填了走亲测模式（实测口吻），留空走转载模式（把他人已总结内容整理成图文）。"""
    data = request.get_json(force=True)
    summary_id = data.get("summary_id")
    author_draft = str(data.get("author_draft") or "").strip()
    theme = str(data.get("theme") or "").strip()[:30]    # 题材（进 LLM prompt，如「AI工具实测」）
    title = str(data.get("title") or "").strip()[:60]    # 图文标题（空则用整合稿标题）
    template = str(data.get("template") or "wechat").strip()[:20]  # 限白名单，越界适配器内回落 wechat
    # 变体三轴（闭集，与头条线同一套预设；空/未知一律回落默认＝现状零行为变化）
    palette = str(data.get("palette") or "").strip()[:20]  # 配色预设键（默认 gold_night＝微信绿信号格）
    font = str(data.get("font") or "").strip()[:20]        # 字体预设键（默认 serif）
    layout = str(data.get("layout") or "").strip()[:20]    # 版式预设键（默认 default；可传 random）
    shots = []  # 真实截图（弹窗上传，复制进包并标 source=real_screenshot）
    shot_token = str(data.get("shot_token") or "").strip()
    if shot_token and re.fullmatch(r"[0-9a-f]{12}", shot_token):
        up = OUTPUT_DIR / "wechat" / "_uploads" / shot_token
        if up.is_dir():
            exts = {".jpg", ".jpeg", ".png", ".webp"}
            shots = sorted(p for p in up.iterdir() if p.is_file() and p.suffix.lower() in exts)
    ref_token = str(data.get("ref_token") or "").strip()  # 排版参考图令牌：只在 manifest 记路径
    if not summary_id:
        return jsonify({"error": "缺少 summary_id"}), 400
    if _wx_task_status.get("running"):
        return jsonify({"error": "已有公众号图文生成任务正在运行"}), 400

    with _db_lock:
        summary = content_store.get_summary(DB_PATH, int(summary_id))
    if not summary:
        return jsonify({"error": "整合稿不存在"}), 404
    if summary.get("summary_type") != "wechat_material":
        return jsonify({"error": "只支持公众号整合稿（wechat_material）生成图文，请先生成整合稿"}), 400

    _wx_task_status.update({
        "running": True, "done": False, "progress": "准备生成公众号图文...", "error": "", "package": None,
    })

    def run():
        try:
            def cb(msg):
                _wx_task_status["progress"] = msg
            package = wechat_graphics.generate_graphics(summary, _fast_config(), author_draft=author_draft,
                                                        progress_cb=cb, theme=theme, title=title,
                                                        template=template, real_screenshots=shots,
                                                        palette=palette or None, font=font or None,
                                                        layout=layout or None, ref_token=ref_token)
            _wx_task_status["package"] = {"id": package["id"], "title": package["title"], "images": len(package["images"])}
            errs = [i for i in (package.get("lint") or []) if i.startswith("error")]
            suffix = "，⚠️ lint 有 %d 条 error 需处理" % len(errs) if errs else ""
            _wx_task_status["progress"] = "✅ 已生成 %d 张卡片，可复制文案人工发布%s" % (len(package["images"]), suffix)
        except GraphicsGateError as e:
            _wx_task_status["error"] = str(e)          # 门禁拒绝：一行说清，不刷 traceback
            _wx_task_status["progress"] = "生成失败"
        except Exception:
            _wx_task_status["error"] = traceback.format_exc()
            _wx_task_status["progress"] = "生成失败"
        finally:
            _wx_task_status["done"] = True
            _wx_task_status["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started", "summary_id": summary_id})


@app.route("/api/wechat/status")
def api_wechat_status():
    return jsonify(_wx_task_status)


@app.route("/api/wechat/list")
def api_wechat_list():
    try:
        items = wechat_graphics.list_packages()
        return jsonify({"items": items, "total": len(items)})
    except Exception as e:
        return jsonify({"error": str(e), "items": [], "total": 0}), 500


@app.route("/api/wechat/<int:summary_id>")
def api_wechat_detail(summary_id):
    package = wechat_graphics.get_package(summary_id)
    if not package:
        return jsonify({"error": "公众号图文不存在"}), 404
    return jsonify(package)


@app.route("/api/wechat/<int:summary_id>/reveal", methods=["POST"])
def api_wechat_reveal(summary_id):
    """在 Finder 中打开该组卡片所在文件夹（人工上传微信素材库时直接取图）。"""
    import subprocess

    folder = wechat_graphics.OUTPUT_DIR / str(summary_id)
    if not folder.exists():
        return jsonify({"error": "文件夹不存在"}), 404
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        elif os.name == "nt":
            subprocess.Popen(["explorer", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return jsonify({"success": True, "folder": str(folder)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/wechat/<int:summary_id>", methods=["DELETE"])
def api_wechat_delete(summary_id):
    try:
        ok = wechat_graphics.delete_package(summary_id)
        return jsonify({"success": bool(ok)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── AI 海报：整理稿 → 出图模型直接画整张海报（含中文文字），出图后 OCR 回读逐字校验 ──
# 与上面两条 HTML 模板产线互不相干：这里不渲染网页，文字是画进像素的。
_poster_status = {"running": False, "done": True, "progress": "", "error": "",
                  "stage": "", "package": None, "plan": None}


def _poster_start(stage, fn):
    """海报任务统一壳。拆解与出图都是几十秒到几分钟的活，必须后台跑 + 前端轮询。"""
    if _poster_status["running"]:
        return False
    _poster_status.update({"running": True, "done": False, "progress": "任务排队中...", "error": "",
                           "stage": stage,
                           "package": None if stage == "plan" else _poster_status["package"],
                           "plan": None if stage == "poster" else _poster_status["plan"]})

    def wrapper():
        try:
            fn()
        except ai_poster.PosterError as e:
            _poster_status["error"] = str(e)        # 可预期失败只给一句话，不刷 traceback
            _poster_status["progress"] = "生成失败"
        except Exception:
            _poster_status["error"] = traceback.format_exc()
            _poster_status["progress"] = "生成失败"
        finally:
            _poster_status["done"] = True
            _poster_status["running"] = False

    threading.Thread(target=wrapper, daemon=True).start()
    return True


def _poster_get_summary(summary_id):
    if not summary_id:
        return None
    with _db_lock:
        return content_store.get_summary(DB_PATH, int(summary_id))


@app.route("/api/poster/plan", methods=["POST"])
def api_poster_plan():
    """只拆解不出图：产出可编辑的文案计划，先审文案再花钱出图。"""
    data = request.get_json(force=True) or {}
    summary = _poster_get_summary(data.get("summary_id"))
    if not summary:
        return jsonify({"error": "整合稿不存在"}), 404
    theme = str(data.get("theme") or "").strip()[:30]
    title = str(data.get("title") or "").strip()[:60]
    # 用户在下拉里点过风格就别让模型再改回去：显式选择优先于自由发挥
    style_key = str(data.get("style_key") or "").strip()[:20]
    ratio = str(data.get("ratio") or "9:16").strip()[:5]   # 只影响预览 prompt 的版式描述
    text_mode = str(data.get("text_mode") or ai_poster.DEFAULT_TEXT_MODE).strip()[:10]

    def fn():
        cfg = _fast_config()
        _poster_status["progress"] = "LLM 拆解海报文案（模型：%s）..." % (cfg.get("model") or "?")
        plan, publish, bad, notes = ai_poster.build_text_plan(summary, cfg, theme=theme, title=title,
                                                              style_key=style_key, text_mode=text_mode)
        # 骨架稿不在这里拦：拆解本身就能判断这份素材有没有料，拦在这里会让人白等一次拆解。
        # 只把风险随结果一起回给前端，出图那一步再决定是否放行。
        warning = "；".join(ai_poster.draft_gate.draft_blockers(summary))
        _poster_status["plan"] = {"plan": plan, "publish": publish, "violations": bad,
                                  "notes": notes, "chars": ai_poster.plan_chars(plan),
                                  "visual": ai_poster.lint_visual(plan),
                                  "model": cfg.get("model") or "",
                                  "draft_warning": warning,
                                  "prompt_text": (ai_poster.build_art_prompt(plan, ratio)
                                                  if text_mode == "typeset"
                                                  else ai_poster.build_prompt_text(plan, ratio))}
        _poster_status["progress"] = ("✅ 文案已拆解（合计 %d 字 · 模型 %s）"
                                      % (ai_poster.plan_chars(plan), cfg.get("model") or "?")
                                      + ("；自动压缩：" + "、".join(notes) if notes else "")
                                      + ("，⚠️ " + "；".join(bad) if bad else ""))

    if not _poster_start("plan", fn):
        return jsonify({"error": "已有海报任务正在运行"}), 400
    return jsonify({"status": "started", "summary_id": summary["id"]})


@app.route("/api/poster/generate", methods=["POST"])
def api_poster_generate():
    """出图。plan 非空＝用前端审改过的文案（不再调 LLM），留空＝现场拆解。"""
    data = request.get_json(force=True) or {}
    summary = _poster_get_summary(data.get("summary_id"))
    if not summary:
        return jsonify({"error": "整合稿不存在"}), 404
    theme = str(data.get("theme") or "").strip()[:30]
    title = str(data.get("title") or "").strip()[:60]
    ratio = str(data.get("ratio") or "9:16").strip()[:5]
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else None
    publish = data.get("publish") if isinstance(data.get("publish"), dict) else None
    force = bool(data.get("force"))      # 骨架稿人工复核后的显式放行
    do_review = data.get("review") is not False   # 默认做视觉评审，前端可关掉省十几秒
    text_mode = str(data.get("text_mode") or ai_poster.DEFAULT_TEXT_MODE).strip()[:10]
    try:
        copies = int(data.get("copies") or 1)
    except (TypeError, ValueError):
        copies = 1

    def fn():
        def cb(msg):
            _poster_status["progress"] = msg
        manifest = ai_poster.generate_poster(summary, _fast_config(), theme=theme, title=title,
                                             ratio=ratio, progress_cb=cb, plan=plan,
                                             publish=publish, force=force, copies=copies,
                                             do_review=do_review, text_mode=text_mode)
        v = manifest.get("verify") or {}
        rv = manifest.get("review") or {}
        ts_chk = manifest.get("typeset") or {}
        errs = [i for i in (manifest.get("lint") or []) if i.startswith("error")]
        _poster_status["package"] = {"id": manifest["id"], "title": manifest["title"],
                                     "url": manifest["url"], "chars": manifest["text_chars"],
                                     "copies": manifest.get("copies") or 1,
                                     "verify_ok": v.get("ok"), "review": rv,
                                     "text_mode": manifest.get("text_mode"),
                                     "typeset_problems": ts_chk.get("problems") or [],
                                     "lint": manifest.get("lint") or []}
        _poster_status["plan"] = {"plan": manifest["plan"], "publish": manifest["publish"],
                                  "violations": errs, "chars": manifest["text_chars"],
                                  "prompt_text": manifest["prompt_text"]}
        score = ("，视觉评审 %s/%s 分" % (rv.get("score"), rv.get("full"))) if rv.get("score") else ""
        if manifest.get("text_mode") == "typeset":
            warn = ("，⚠️ 版面提示：" + "；".join(ts_chk.get("problems") or [])) if ts_chk.get("problems") else ""
            _poster_status["progress"] = "✅ 海报已生成（%d 字，字由程序排版、不存在错字）%s%s" % (
                manifest["text_chars"], score, warn)
        else:
            _poster_status["progress"] = "✅ 海报已生成（%d 字）%s%s" % (
                manifest["text_chars"], score,
                "" if v.get("ok") else "，⚠️ 图面文字校验未通过，发布前人眼复核")

    if not _poster_start("poster", fn):
        return jsonify({"error": "已有海报任务正在运行"}), 400
    return jsonify({"status": "started", "summary_id": summary["id"]})


@app.route("/api/poster/preview", methods=["POST"])
def api_poster_preview():
    """版面预览：占位底图 + 真排版，当场返回，不调任何付费接口。

    故意不做字数拦截——预览的意义就是让人看见"这么写版面会爆"，
    所以越界照样出图，只把版面自检的问题原样回给前端。
    """
    data = request.get_json(force=True) or {}
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else None
    if not plan or not (plan.get("title") or "").strip():
        return jsonify({"error": "还没有可预览的文案，先点「① AI 拆解文案」"}), 400
    ratio = str(data.get("ratio") or "9:16").strip()[:5]
    # 草稿预览：派生规则只有 normalize() 一份，前端只传表单原值
    if isinstance(data.get("draft"), dict) and data.get("draft"):
        from src import poster_breakdown
        plan["_skin"] = poster_breakdown.normalize(data["draft"])["skin"]
    try:
        png, chk = ai_poster.preview_typeset(plan, ratio)
        key = hashlib.sha1(png).hexdigest()[:16]
        out_dir = OUTPUT_DIR / "poster" / "_preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / (key + ".png")).write_bytes(png)
        _prune_previews(out_dir)
        return jsonify({"url": "/media/poster/_preview/%s.png" % key,
                        "check": chk, "chars": ai_poster.plan_chars(plan),
                        "cost_yuan": 0})
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 500


def _prune_previews(out_dir, keep=24):
    """预览图按内容哈希存，改一次文案就多一张；留最近 24 张，其余删掉。
    这些是纯中间产物，攒在 output/ 里没人看也没人清。"""
    try:
        files = sorted(out_dir.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[keep:]:
            f.unlink()
    except Exception:
        pass


@app.route("/api/poster/status")
def api_poster_status():
    return jsonify(_poster_status)


@app.route("/api/poster/styles")
def api_poster_styles():
    """风格预设名单由后端下发：配方原文只有一份，前端不抄一遍才不会漂移。"""
    return jsonify({"items": ai_poster.style_choices(),
                    "modes": [{"key": "typeset", "label": "程序排字（推荐，字不会错）"},
                              {"key": "model", "label": "模型画字（整张交给模型，风格更统一但会错字）"}],
                    # 两套预算按模式分开下发：程序排字看版面、模型画字看错字率，
                    # 前端自己写死一份迟早会跟后端对不上
                    "limitsByMode": {"typeset": ai_poster.TS_LIMITS, "model": ai_poster.MD_LIMITS},
                    "limits": {"scene": ai_poster.SCENE_MAX, "cardVis": ai_poster.CARD_VIS_MAX,
                               "cardMin": ai_poster.CARD_MIN, "cardMax": ai_poster.CARD_MAX,
                               "spine": ai_poster.SPINE_MAX},
                    "reviewPass": ai_poster.REVIEW_PASS})


@app.route("/api/poster/list")
def api_poster_list():
    try:
        items = [{"id": i.get("id"), "title": i.get("title"), "url": i.get("url"),
                  "chars": i.get("text_chars"), "ratio": i.get("ratio"),
                  "verify_ok": (i.get("verify") or {}).get("ok"),
                  "created_at": i.get("created_at")}
                 for i in ai_poster.list_packages()]
        return jsonify({"items": items, "total": len(items)})
    except Exception as e:
        return jsonify({"error": str(e), "items": [], "total": 0}), 500


@app.route("/api/poster/<int:summary_id>")
def api_poster_detail(summary_id):
    manifest = ai_poster.get_package(summary_id)
    if not manifest:
        return jsonify({"error": "海报不存在"}), 404
    return jsonify(manifest)


@app.route("/api/poster/<int:summary_id>/pick", methods=["POST"])
def api_poster_pick(summary_id):
    """多张候选里人工改选一张当要发布的那张（复制成 poster.png，发布清单随之重算）。"""
    data = request.get_json(force=True) or {}
    try:
        m = ai_poster.choose_candidate(summary_id, data.get("index"))
        return jsonify({"success": True, "chosen": m.get("chosen"), "url": m.get("url"),
                        "verify_ok": (m.get("verify") or {}).get("ok"),
                        "lint": m.get("lint") or []})
    except ai_poster.PosterError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 我的模板：把满意的成品收编成可复用的风格（数据在 config/poster_templates.json）──
@app.route("/api/poster/templates")
def api_poster_templates():
    try:
        from src import poster_templates as pt
        items = [{"key": it.get("key"), "name": it.get("name"), "parent": it.get("parent"),
                  "source_summary_id": it.get("source_summary_id"),
                  "has_base": bool(it.get("base_image")), "created_at": it.get("created_at")}
                 for it in pt.all_templates()]
        return jsonify({"items": items, "total": len(items)})
    except Exception as e:
        return jsonify({"error": str(e), "items": [], "total": 0}), 500


@app.route("/api/poster/breakdown", methods=["POST"])
def api_poster_breakdown():
    """拆一张外部参考图文 → 模板草稿。看图必须用视觉模型，deepseek 那档看不见图。

    草稿不直接落盘：模型给的配色、占比、版式都会错，先回给前端让人改，
    改完点免费预览立刻看到，确认没问题再调 save-draft 存。
    """
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "没收到图片"}), 400
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        return jsonify({"error": "只支持 jpg/png/webp"}), 400
    blob = f.read()
    if len(blob) > 8 * 1024 * 1024:
        return jsonify({"error": "图片超过 8MB，压一下再传"}), 400
    token = uuid.uuid4().hex[:10]
    d = OUTPUT_DIR / "poster" / "_refs" / token
    d.mkdir(parents=True, exist_ok=True)
    name = "ref%s" % (ext if ext != ".jpeg" else ".jpg")
    (d / name).write_bytes(blob)
    ref_url = "/media/poster/_refs/%s/%s" % (token, name)
    try:
        from src import poster_breakdown
        cfg = _ai_config()          # 拆解用主档模型名，视觉档单独可配
        model = (cfg.get("vision_model") or poster_breakdown.DEFAULT_VISION_MODEL)
        raw = poster_breakdown.breakdown(blob, cfg, model=model)
        return jsonify({"draft": raw, "ref_url": ref_url, "model": model,
                        "layouts": poster_breakdown.LAYOUTS,
                        "panels": poster_breakdown.PANEL_STYLES,
                        "faces": poster_breakdown.TITLE_FACES})
    except Exception as e:
        return jsonify({"error": "拆解失败：%s" % str(e)[:200], "ref_url": ref_url}), 500


@app.route("/api/poster/save-draft", methods=["POST"])
def api_poster_save_draft():
    """把改过的草稿存成模板。"""
    data = request.get_json(force=True) or {}
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else None
    if not draft:
        return jsonify({"error": "没有草稿"}), 400
    try:
        from src import poster_templates as pt
        it = pt.save_from_draft(draft, str(data.get("ref_url") or ""))
        return jsonify({"success": True, "key": it["key"], "name": it["name"],
                        "has_base": False})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/poster/<int:summary_id>/save-template", methods=["POST"])
def api_poster_save_template(summary_id):
    """收编当前这张海报为模板。reuse_base=True 时连底图一起存，之后换文案出图 0 元。"""
    data = request.get_json(force=True) or {}
    m = ai_poster.get_package(summary_id)
    if not m:
        return jsonify({"error": "这条整合稿还没出过图，先出一次再收编"}), 400
    try:
        from src import poster_templates as pt
        it = pt.save_from_package(m, name=str(data.get("name") or ""),
                                 reuse_base=data.get("reuse_base") is not False)
        return jsonify({"success": True, "key": it["key"], "name": it["name"],
                        "has_base": bool(it["base_image"])})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/poster/templates/<key>/rename", methods=["POST"])
def api_poster_template_rename(key):
    data = request.get_json(force=True) or {}
    from src import poster_templates as pt
    it = pt.rename(key, str(data.get("name") or ""))
    return jsonify({"success": bool(it), "name": (it or {}).get("name")})


@app.route("/api/poster/templates/<key>", methods=["DELETE"])
def api_poster_template_delete(key):
    from src import poster_templates as pt
    return jsonify({"success": bool(pt.delete(key))})


@app.route("/api/poster/<int:summary_id>/reveal", methods=["POST"])
def api_poster_reveal(summary_id):
    """出图之后直接在访达里定位到那张图，省掉"图存哪了"这一问。"""
    try:
        return jsonify({"success": True, "path": ai_poster.reveal_folder(summary_id)})
    except ai_poster.PosterError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/poster/<int:summary_id>", methods=["DELETE"])
def api_poster_delete(summary_id):
    try:
        return jsonify({"success": bool(ai_poster.delete_package(summary_id))})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── AI 配置管理 ──
ENV_PATH = ROOT / "config" / "config.env.local"


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "***"
    return f"{key[:5]}...{key[-4:]}"


def _update_env_file(path: Path, updates: dict):
    """按行更新 KEY=VALUE，保留原有注释与其他键；新键追加到末尾。"""
    lines = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for key, val in remaining.items():
        out.append(f"{key}={val}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


@app.route("/api/ai/config", methods=["GET"])
def api_ai_config_get():
    config = collector.load_env_file(str(ENV_PATH))
    method = collector.normalize_ai_method(collector.config_get(config, "AI_METHOD", "skip"))
    if method == "deepseek":
        key = collector.config_get(config, "DEEPSEEK_API_KEY") or collector.config_get(config, "AI_API_KEY")
        base = collector.config_get(config, "DEEPSEEK_API_BASE", "")
        model = collector.config_get(config, "DEEPSEEK_MODEL", "")
    else:
        key = collector.config_get(config, "OPENAI_API_KEY") or collector.config_get(config, "AI_API_KEY")
        base = collector.config_get(config, "AI_API_BASE", "")
        model = collector.config_get(config, "AI_MODEL", "")
    return jsonify({
        "method": method,
        "has_key": bool(key),
        "api_key_masked": _mask_key(key),
        "api_base": base,
        "model": model,
        "fast_model": collector.config_get(config, "AI_MODEL_FAST", ""),
    })


@app.route("/api/ai/config", methods=["POST"])
def api_ai_config_save():
    data = request.get_json(force=True)
    method = collector.normalize_ai_method(data.get("method", "skip"))
    if method not in ("skip", "deepseek", "openai"):
        return jsonify({"error": "AI 服务类型无效"}), 400

    config = collector.load_env_file(str(ENV_PATH))
    updates = {"AI_METHOD": method}
    if method != "skip":
        api_key = (data.get("api_key") or "").strip()
        if not api_key:
            # 表单留空：沿用已保存的 Key
            api_key = config.get("DEEPSEEK_API_KEY" if method == "deepseek" else "OPENAI_API_KEY", "")
        if not api_key:
            return jsonify({"error": "请填写 API Key"}), 400
        base = (data.get("api_base") or "").strip()
        model = (data.get("model") or "").strip()
        # 快速模型：拆解与卡片化用；留空＝跟主模型一样。必须是同一个接口下调得到的模型名
        updates["AI_MODEL_FAST"] = (data.get("fast_model") or "").strip()
        if method == "deepseek":
            updates["DEEPSEEK_API_KEY"] = api_key
            updates["DEEPSEEK_API_BASE"] = base
            updates["DEEPSEEK_MODEL"] = model
        else:
            updates["OPENAI_API_KEY"] = api_key
            updates["AI_API_BASE"] = base
            updates["AI_MODEL"] = model
    _update_env_file(ENV_PATH, updates)
    return jsonify({"success": True})


def _ai_config_from_payload(data: dict) -> dict:
    """从请求体构建测试用配置；空字段回退到已保存配置或默认值。"""
    saved = _ai_config()
    method = collector.normalize_ai_method((data.get("method") or "").strip() or saved["method"])
    api_key = (data.get("api_key") or "").strip() or saved.get("api_key") or ""
    if method == "deepseek":
        default_base, default_model = "https://api.deepseek.com", "deepseek-chat"
    else:
        default_base, default_model = "https://api.openai.com/v1", "gpt-4o-mini"
    same_method = saved.get("method") == method
    api_base = (data.get("api_base") or "").strip() or (saved.get("api_base") if same_method else "") or default_base
    model = (data.get("model") or "").strip() or (saved.get("model") if same_method else "") or default_model
    return {"method": method, "api_key": api_key, "api_base": api_base, "model": model}


@app.route("/api/ai/test", methods=["POST"])
def api_ai_test():
    data = request.get_json(force=True) or {}
    cfg = _ai_config_from_payload(data)
    if cfg["method"] == "skip":
        return jsonify({"success": False, "error": "当前选择「不使用 AI」，请先选择 DeepSeek 或 OpenAI 接口"})
    if not cfg["api_key"]:
        return jsonify({"success": False, "error": "请填写 API Key"})
    try:
        import openai
    except ImportError:
        return jsonify({"success": False, "error": "服务端缺少 openai 库，请执行: pip3 install openai"})
    try:
        client = openai.OpenAI(api_key=cfg["api_key"], base_url=cfg["api_base"], timeout=30, max_retries=0)
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": "连接测试，请只回复两个字母：OK"}],
            max_tokens=8,
        )
        reply = (resp.choices[0].message.content or "").strip()
        return jsonify({"success": True, "reply": reply, "model": cfg["model"]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ════════════════════════════════════════════════════════════════
# 订阅（作者主页增量同步）
# ════════════════════════════════════════════════════════════════

# new_items：这轮真正进了素材库的条目（标题+作者+来源）。同步完只显示"新增 N 篇"的话，
# 用户只能去上千条列表里自己翻，翻不到就以为没同步——这次报障就是这么来的。
_sub_status = {"running": False, "progress": "", "done": True, "error": "", "result": None,
               "new_items": []}


def _read_cookie_from_file() -> str:
    """读本地 cookie 文件（采集中心每次采集都会写入）。"""
    try:
        p = get_cookie_path()
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _sub_ids(data: dict) -> list[int]:
    """请求体 ids -> 正整数订阅 id 列表（非数字项丢弃）。"""
    out = []
    for raw in data.get("ids") or []:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.append(n)
    return out


@app.route("/api/subscriptions")
def api_subscriptions_list():
    if not DB_PATH.exists():
        return jsonify({"items": []})
    try:
        with _db_lock:
            items = content_store.list_subscriptions(DB_PATH)
    except Exception as e:
        return jsonify({"items": [], "error": str(e)}), 500
    return jsonify({"items": items})


@app.route("/api/subscriptions", methods=["POST"])
def api_subscriptions_add():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    author = (data.get("author") or "").strip()
    category = (data.get("category") or "").strip()
    game = (data.get("game") or "").strip()
    platform = (data.get("platform") or "douyin").strip()
    if not url:
        return jsonify({"error": "请输入链接"}), 400

    if platform == "wechat":
        # 微信：url 是 WeWe RSS 的 feed URL
        if not url.startswith("http"):
            return jsonify({"error": "RSS 链接需以 http 开头"}), 400
        sec_uid = f"wechat:{hashlib.md5(url.encode()).hexdigest()[:12]}"
        try:
            with _db_lock:
                sub = content_store.add_subscription(
                    DB_PATH, sec_uid, url, author, category, game, "wechat")
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"success": True, "subscription": sub})

    # 抖音：url 是用户主页链接
    from src.fetch_user_videos import extract_sec_uid_from_url, resolve_short_url
    if "v.douyin.com" in url or "/share/" in url:
        url = resolve_short_url(url)
    sec_uid = extract_sec_uid_from_url(url)
    if not sec_uid:
        return jsonify({"error": "无法从链接提取 sec_uid，请使用 www.douyin.com/user/... 格式"}), 400
    user_url = f"https://www.douyin.com/user/{sec_uid}"
    try:
        with _db_lock:
            sub = content_store.add_subscription(
                DB_PATH, sec_uid, user_url, author, category, game, "douyin")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"success": True, "subscription": sub})


@app.route("/api/authors")
def api_authors():
    """视频库作者统计（含是否已订阅）。"""
    if not DB_PATH.exists():
        return jsonify({"authors": []})
    try:
        with _db_lock:
            conn = content_store.connect(DB_PATH)
            try:
                rows = conn.execute(
                    "SELECT COALESCE(NULLIF(TRIM(author),''),'未知作者') AS a, COUNT(*) AS n "
                    "FROM videos GROUP BY a ORDER BY n DESC"
                ).fetchall()
                subs = {s["author"] for s in content_store.list_subscriptions(DB_PATH)}
            finally:
                conn.close()
        authors = [
            {"author": r["a"], "count": r["n"], "subscribed": r["a"] in subs}
            for r in rows
        ]
        return jsonify({"authors": authors})
    except Exception as e:
        return jsonify({"authors": [], "error": str(e)}), 500


@app.route("/api/subscriptions/import", methods=["POST"])
def api_subscriptions_import():
    """从视频库作者导入订阅：优先 SQLite 直查 sec_uid（videos.author_sec_uid，
    2026-09-14 起回填），查不到再走视频链接反查主页的旧路径。"""
    data = request.get_json(force=True)
    author = (data.get("author") or "").strip()
    if not author:
        return jsonify({"error": "缺少作者名"}), 400

    # 路径 1：SQLite 直查（快且不依赖 cookie/链接有效性）
    try:
        with _db_lock:
            sec_uid = content_store.get_author_sec_uid(DB_PATH, author)
    except Exception:
        sec_uid = ""
    if sec_uid:
        home = f"https://www.douyin.com/user/{sec_uid}"
        with _db_lock:
            sub = content_store.add_subscription(DB_PATH, sec_uid, home, author)
        return jsonify({"success": True, "subscription": sub})

    # 路径 2（兜底）：拿该作者一条已完成视频的链接反查主页
    cookie = _read_cookie_from_file()
    if not cookie:
        return jsonify({"error": "未找到 Cookie，请先在采集中心完成一次采集"}), 400

    # 找该作者一条已完成视频的链接
    try:
        with _db_lock:
            conn = content_store.connect(DB_PATH)
            try:
                row = conn.execute(
                    "SELECT source_url FROM videos "
                    "WHERE author = ? AND COALESCE(source_url,'') != '' "
                    "ORDER BY id DESC LIMIT 1", (author,),
                ).fetchone()
            finally:
                conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not row or not row["source_url"]:
        return jsonify({"error": f"视频库中没有「{author}」的视频链接"}), 404

    try:
        parser = _setup_parser_and_cookie(cookie)
        home = parser.get_user_home_from_video_url(row["source_url"])
        if not home:
            return jsonify({"error": "无法从视频链接解析作者主页，链接可能已失效"}), 502
        from src.fetch_user_videos import extract_sec_uid_from_url
        sec_uid = extract_sec_uid_from_url(home)
        if not sec_uid:
            return jsonify({"error": "解析主页失败：无法提取 sec_uid"}), 502
        with _db_lock:
            sub = content_store.add_subscription(DB_PATH, sec_uid, home, author)
        return jsonify({"success": True, "subscription": sub})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/subscriptions/<int:sub_id>/author", methods=["POST"])
def api_subscriptions_author(sub_id):
    """修改订阅作者显示名。"""
    data = request.get_json(force=True)
    author = (data.get("author") or "").strip()
    if not author:
        return jsonify({"error": "作者名不能为空"}), 400
    try:
        with _db_lock:
            ok = content_store.update_subscription_author(DB_PATH, sub_id, author)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not ok:
        return jsonify({"error": "订阅不存在"}), 404
    return jsonify({"success": True})


@app.route("/api/subscriptions/category", methods=["POST"])
def api_subscriptions_category():
    """批量设置订阅分类（同步的新内容会自动打上）。ids 单个/多个同一路径。"""
    data = request.get_json(force=True)
    ids = _sub_ids(data)
    category = (data.get("category") or "").strip()
    game = (data.get("game") or "").strip()
    if not ids:
        return jsonify({"error": "未选择订阅"}), 400
    if category not in content_store.CATEGORIES:
        return jsonify({"error": "无效分类"}), 400
    try:
        with _db_lock:
            n = content_store.update_subscriptions_category(DB_PATH, ids, category, game)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not n:
        return jsonify({"error": "订阅不存在"}), 404
    return jsonify({"success": True, "updated": n})


@app.route("/api/subscriptions/tags", methods=["POST"])
def api_subscriptions_tags():
    """批量设置订阅自定义标签（本地分组用，不参与 AI 分类与渠道策略）。"""
    data = request.get_json(force=True)
    ids = _sub_ids(data)
    if not ids:
        return jsonify({"error": "未选择订阅"}), 400
    tags = content_store.normalize_tags(data.get("tags") or "")
    try:
        with _db_lock:
            n = content_store.set_subscription_tags(DB_PATH, ids, tags)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not n:
        return jsonify({"error": "订阅不存在"}), 404
    return jsonify({"success": True, "updated": n, "tags": tags})


@app.route("/api/subscriptions/<int:sub_id>", methods=["DELETE"])
def api_subscriptions_delete(sub_id):
    try:
        with _db_lock:
            ok = content_store.delete_subscription(DB_PATH, sub_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not ok:
        return jsonify({"error": "订阅不存在"}), 404
    return jsonify({"success": True})


@app.route("/api/subscriptions/sync", methods=["POST"])
def api_subscriptions_sync():
    """同步订阅（全部/单个/多个勾选）。抖音：拉主页新视频→ASR。微信：拉 RSS 新文章。"""
    if _sub_status.get("running"):
        return jsonify({"error": "订阅同步进行中，请稍候"}), 409
    data = request.get_json(force=True, silent=True) or {}
    sub_id = data.get("id")  # None = 全部
    sub_ids = data.get("ids") or []  # 勾选批量：[1, 3, 7]；空 = 不启用批量过滤
    cookie = (data.get("cookie") or "").strip() or _read_cookie_from_file()

    def run():
        import time
        _sub_status.update(running=True, done=False, error="", progress="准备同步...",
                           result=None, new_items=[])
        try:
            with _db_lock:
                subs = content_store.list_subscriptions(DB_PATH)
            if sub_id:
                subs = [s for s in subs if s["id"] == sub_id]
            elif sub_ids:
                wanted = {int(i) for i in sub_ids if str(i).isdigit() or isinstance(i, int)}
                subs = [s for s in subs if s["id"] in wanted]
            if not subs:
                _sub_status["error"] = "没有可同步的订阅"
                return

            # 分流：抖音需要 cookie + parser；微信不需要
            douyin_subs = [s for s in subs if s.get("platform", "douyin") == "douyin"]
            wechat_subs = [s for s in subs if s.get("platform") == "wechat"]

            if douyin_subs and not cookie:
                _sub_status["error"] = "抖音订阅需要 Cookie，请先在采集中心完成一次采集"
                return

            summary = []
            pending_marks = []  # 有新视频的抖音作者：终局统一标记（见下方终局块）

            # ── 微信订阅同步 ──
            if wechat_subs:
                from src.wechat_fetcher import sync_wechat_feed
                for si, sub in enumerate(wechat_subs, 1):
                    name = sub["author"] or sub["sec_uid"][:16]
                    _sub_status["progress"] = f"[微信 {si}/{len(wechat_subs)}] {name}: 拉取 RSS..."
                    try:
                        existing = content_store.get_wechat_article_links(DB_PATH)
                        result = sync_wechat_feed(sub["user_url"], existing)
                        if result["error"]:
                            with _db_lock:
                                content_store.update_subscription_sync(
                                    DB_PATH, sub["id"], 0, result["error"])
                            summary.append(f"{name}: {result['error']}")
                            continue
                        new_articles = result["articles"]
                        # 全文未就绪的条目不入库、下次同步重试。必须回显，否则界面显示
                        # 「无新文章」，用户以为订阅没更新（本次排查的实际报障）。
                        pending = result.get("skipped") or []
                        pending_note = ""
                        if pending:
                            shown = "、".join((t or "无标题")[:16] for t in pending[:2])
                            pending_note = "；%d 篇全文未就绪（%s%s），下次同步自动重试" % (
                                len(pending), shown, "…" if len(pending) > 2 else "")
                        if not new_articles:
                            with _db_lock:
                                content_store.update_subscription_sync(
                                    DB_PATH, sub["id"], 0, "无新文章" + pending_note)
                            summary.append(f"{name}: 无新文章{pending_note}")
                            continue
                        with _db_lock:
                            inserted = content_store.insert_wechat_articles(
                                DB_PATH, new_articles,
                                category=sub.get("category") or "",
                                game=sub.get("game") or "",
                            )
                        cat_note = f"，已按订阅标记分类「{sub['category']}」{len(inserted)} 条" if sub.get("category") and inserted else ""
                        with _db_lock:
                            content_store.update_subscription_sync(
                                DB_PATH, sub["id"], len(inserted),
                                f"新增 {len(inserted)} 篇文章{cat_note}{pending_note}",
                                author=sub.get("author") or "")
                        summary.append(f"{name}: 新增 {len(inserted)} 篇文章{cat_note}{pending_note}")
                        _sub_status["new_items"] += [
                            {"kind": "wechat", "author": name,
                             "title": (a.get("title") or "").strip(),
                             "published_at": (a.get("published_at") or "")[:10]}
                            for a in inserted]
                    except Exception as e:
                        with _db_lock:
                            content_store.update_subscription_sync(
                                DB_PATH, sub["id"], 0, f"同步失败: {e}")
                        summary.append(f"{name}: 失败 ({e})")

            # ── 抖音订阅同步 ──
            if douyin_subs:
                parser = _setup_parser_and_cookie(cookie)
                ai_config = {"method": "skip", "api_key": "", "api_base": "", "model": ""}
                # 批量拉取（2026-09-27 提速）：一次 Chromium 跑完所有作者主页，
                # 替代旧路径逐作者冷启动浏览器（每次 5-15 秒，无新视频也要付）
                browser_ids: dict = {}
                try:
                    from src.browser_fetch import browser_fetch_users_batch
                    _items = [{"id": str(s["id"]), "url": s["user_url"]} for s in douyin_subs]

                    def _bp(done, total, item_id, ids, err):
                        mark = f"✓ {len(ids)} 条" if ids else f"✗ {err[:40]}"
                        _sub_status["progress"] = f"[抖音主页 {done}/{total}] {mark}"

                    browser_ids = browser_fetch_users_batch(_items, on_progress=_bp)
                except Exception as be:
                    summary.append(f"浏览器批量拉取异常 ({be})，逐作者退回旧 API")
                from src.fetch_user_videos import load_existing_aweme_ids as _load_ids
                existing_ids = _load_ids(str(EXCEL_PATH))
                for si, sub in enumerate(douyin_subs, 1):
                    name = sub["author"] or sub["sec_uid"][:12]
                    _sub_status["progress"] = f"[抖音 {si}/{len(douyin_subs)}] {name}: 拉取主页..."
                    try:
                        from src.fetch_user_videos import fetch_user_videos
                        # ── 浏览器批量结果查表（2026-09-21 起浏览器优先绕 Argus 风控，
                        #    2026-09-27 起批量一次拉完，不再逐作者冷启动）──
                        videos = []
                        ids = browser_ids.get(str(sub["id"])) or []
                        if ids:
                            # 浏览器成功拿到列表：去重后无新就是无新，
                            # 不再退回旧 API（旧路径无新视频也会挂起 30s+ 白等）
                            videos = [
                                {"aweme_id": i, "url": "https://www.douyin.com/video/" + i}
                                for i in ids if i not in existing_ids
                            ]
                        else:
                            # 浏览器没覆盖到该作者（批量异常/该作者页失败）→ 退回旧 API
                            result = fetch_user_videos(
                                url=sub["user_url"], cookie=cookie,
                                max_pages=1, max_videos=20, mode="user_url",
                                exclude_excel=str(EXCEL_PATH),
                            )
                            videos = result.get("videos") or []
                        new_author = ""
                        if not videos:
                            with _db_lock:
                                content_store.update_subscription_sync(
                                    DB_PATH, sub["id"], 0, "无新视频")
                            summary.append(f"{name}: 无新视频")
                            continue

                        # 写入 Excel
                        import openpyxl
                        wb = openpyxl.load_workbook(str(EXCEL_PATH))
                        ws = wb["抖音视频数据"]
                        next_row = ws.max_row + 1
                        for r in range(2, ws.max_row + 1):
                            if not ws.cell(r, 1).value:
                                next_row = r
                                break
                        for i, v in enumerate(videos):
                            ws.cell(next_row + i, 1).value = v["url"]
                            ws.cell(next_row + i, 2).value = "未开始"
                            ws.cell(next_row + i, 3).value = v.get("aweme_id", "")
                        wb.save(str(EXCEL_PATH))
                        wb.close()

                        # 逐条 ASR（提速：工作簿只加载一次，每条保存一次防崩溃丢转写）
                        wb = openpyxl.load_workbook(str(EXCEL_PATH))
                        try:
                            ws = wb["抖音视频数据"]
                            for i, v in enumerate(videos):
                                row = next_row + i
                                _sub_status["progress"] = (
                                    f"[抖音 {si}/{len(douyin_subs)}] {name}: 处理 {i+1}/{len(videos)} "
                                    f"{v.get('aweme_id','')}..."
                                )
                                ok = collector.process_row(ws, row, cookie, "base", ai_config, parser)
                                if not new_author:
                                    new_author = str(ws.cell(row, 4).value or "").strip()
                                wb.save(str(EXCEL_PATH))
                                if i < len(videos) - 1:
                                    time.sleep(5)
                        finally:
                            wb.close()

                        # 提速：索引重建/内容库同步/来源与分类标记/订阅状态，
                        # 从逐作者执行改为收集后终局统一执行（每次全表重扫 2-5 秒）
                        existing_ids.update(v["aweme_id"] for v in videos)
                        pending_marks.append({
                            "sub": sub,
                            "next_row": next_row,
                            "count": len(videos),
                            "new_author": new_author,
                        })
                    except Exception as e:
                        with _db_lock:
                            content_store.update_subscription_sync(
                                DB_PATH, sub["id"], 0, f"同步失败: {e}")
                        summary.append(f"{name}: 失败 ({e})")

            # ── 终局：一次重建索引 + 一次内容库同步 + 补齐订阅标记 ──
            if pending_marks:
                _sub_status["progress"] = "重建索引与同步内容库..."
                collector.update_video_index(str(EXCEL_PATH))
                _sync_content_db()
                for pm in pending_marks:
                    sub = pm["sub"]
                    name = sub["author"] or sub["sec_uid"][:12]
                    rows = [pm["next_row"] + i for i in range(pm["count"])]
                    # 标记来源 + 回填作者 sec_uid（订阅入口，按行号精确标记）
                    with _db_lock:
                        content_store.mark_video_source(
                            DB_PATH, "抖音视频数据", rows, "subscription",
                            author_sec_uid=sub["sec_uid"],
                        )
                    # 订阅设了分类 → 新视频自动打上。AI 增量分类不会改写已有 category
                    # （写回口径见 content_store.classify_videos 的 force=False 分支）
                    cat_note = ""
                    new_titles = []
                    if sub.get("category"):
                        with _db_lock:
                            conn = content_store.connect(DB_PATH)
                            try:
                                marked = 0
                                for r0 in rows:
                                    r = conn.execute(
                                        "SELECT id, title FROM videos WHERE source_sheet = ? AND source_row = ?",
                                        ("抖音视频数据", r0),
                                    ).fetchone()
                                    if r:
                                        new_titles.append((r["title"] or "").strip())
                                        # 非游戏攻略分类不带 game
                                        g = sub.get("game") or ""
                                        if sub["category"] != "游戏攻略":
                                            g = ""
                                        conn.execute(
                                            "UPDATE videos SET category = ?, game = ? WHERE id = ?",
                                            (sub["category"], g, r["id"]),
                                        )
                                        marked += 1
                                conn.commit()
                            finally:
                                conn.close()
                        cat_note = f"，已按订阅标记分类「{sub['category']}」{marked} 条"
                    with _db_lock:
                        content_store.update_subscription_sync(
                            DB_PATH, sub["id"], pm["count"],
                            f"新增 {pm['count']} 条并已转写{cat_note}", author=pm["new_author"])
                    summary.append(f"{name}: 新增 {pm['count']} 条{cat_note}")
                    _sub_status["new_items"] += [
                        {"kind": "douyin", "author": pm["new_author"] or name, "title": tt,
                         "published_at": ""} for tt in new_titles if tt]

            # 同步入库后补 AI 增量打标：只补缺口（订阅链路新行自带 category 但缺 ai_tags，
            # 选题雷达依赖 ai_tags）。必须放在两条分支之外——原先挂在抖音分支里，
            # 只勾微信订阅同步时新文章一条标都不打，雷达/渠道策略侧就等于「没有更新」。
            # AI 未配置时静默跳过，打标失败不算同步失败
            _sub_status["progress"] = "AI 自动打标..."
            _cls = _auto_classify_after_sync()
            if _cls.get("classified"):
                summary.append(f"AI 打标 {_cls['classified']}/{_cls['total']} 条")

            _sub_status["result"] = summary
            _sub_status["progress"] = "✅ 订阅同步完成：" + "；".join(summary)
        except Exception as e:
            _sub_status["error"] = f"{e}\n{traceback.format_exc()[-500:]}"
        finally:
            _sub_status["running"] = False
            _sub_status["done"] = True

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"success": True})


@app.route("/api/subscriptions/sync/status")
def api_subscriptions_sync_status():
    return jsonify(_sub_status)


# ════════════════════════════════════════════════════════════════
# 删除：视频 / 面试题
# ════════════════════════════════════════════════════════════════

@app.route("/api/videos/delete", methods=["POST"])
def api_videos_delete():
    """删除视频：清 Excel 行（保持行号稳定）→ 删 SQLite 记录 → 重建索引。"""
    data = request.get_json(force=True)
    items = data.get("items") or []
    if not items:
        return jsonify({"error": "请选择要删除的视频"}), 400
    try:
        import openpyxl

        deleted = 0
        with _db_lock:
            # 1. 删 SQLite
            for it in items:
                sheet, row = it.get("sheet"), int(it.get("row") or 0)
                if sheet and row:
                    content_store.delete_video(DB_PATH, sheet, row)
                    if sheet == "微信文章":
                        deleted += 1  # 微信文章不落 Excel，DB 删除即计数
            # 2. 清 Excel 行（所有 sheet 中匹配行清空 1-15 列，行号保持不变）
            wb = openpyxl.load_workbook(str(EXCEL_PATH))
            target_sheets = {it.get("sheet") for it in items if it.get("sheet")}
            keyset = {(it.get("sheet"), int(it.get("row") or 0)) for it in items}
            for ws in wb.worksheets:
                if ws.title not in target_sheets:
                    continue
                for (s, r) in keyset:
                    if s == ws.title and 2 <= r <= ws.max_row:
                        for col in range(1, 16):
                            ws.cell(r, col).value = None
                        deleted += 1
            wb.save(str(EXCEL_PATH))
            wb.close()
            # 3. 重建索引（追加分）+ 显式移除已删条目（索引是合并模式，不会自动删）
            collector.update_video_index(str(EXCEL_PATH))
            if INDEX_PATH.exists():
                with open(INDEX_PATH, encoding="utf-8") as f:
                    idx = json.load(f)
                idx["videos"] = [
                    v for v in idx.get("videos", [])
                    if (v.get("sheet"), int(v.get("row") or 0)) not in keyset
                ]
                with open(INDEX_PATH, "w", encoding="utf-8") as f:
                    json.dump(idx, f, ensure_ascii=False, indent=2)
            # 4. 重同步内容库
            _sync_content_db()
        return jsonify({"success": True, "deleted": deleted})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/content/questions/delete", methods=["POST"])
def api_content_question_delete():
    """删除面试题库中的单题（改写 structured_data.questions）。"""
    data = request.get_json(force=True)
    summary_id = int(data.get("summary_id") or 0)
    q_index = int(data.get("q_index") or -1)
    if not summary_id or q_index < 0:
        return jsonify({"error": "参数错误"}), 400
    try:
        with _db_lock:
            conn = content_store.connect(DB_PATH)
            try:
                row = conn.execute(
                    "SELECT structured_data FROM ai_summaries WHERE id = ? AND summary_type = 'ai_interview'",
                    (summary_id,),
                ).fetchone()
                if not row:
                    return jsonify({"error": "面试题整理稿不存在"}), 404
                structured = json.loads(row["structured_data"] or "{}")
                questions = structured.get("questions") or []
                if q_index >= len(questions):
                    return jsonify({"error": "题目索引越界"}), 400
                questions.pop(q_index)
                structured["questions"] = questions
                conn.execute(
                    "UPDATE ai_summaries SET structured_data = ? WHERE id = ?",
                    (json.dumps(structured, ensure_ascii=False), summary_id),
                )
                conn.commit()
                remaining = len(questions)
            finally:
                conn.close()
        # 全删完则连整理稿一起删
        if remaining == 0:
            content_store.delete_summary(DB_PATH, summary_id)
        return jsonify({"success": True, "remaining": remaining})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/")
def index():
    return send_from_directory(str(ROOT / "web" / "templates"), "index.html")

if __name__ == "__main__":
    print(f"🎬 video2text Web 面板")
    print(f"   Excel: {EXCEL_PATH}")
    print(f"   启动: http://127.0.0.1:15801")
    # 启动时幂等回填 author_sec_uid（按订阅作者名匹配存量视频；已填的不动）
    try:
        if DB_PATH.exists():
            _n = content_store.backfill_author_sec_uid_from_subscriptions(DB_PATH)
            if _n:
                print(f"   [启动] 回填 author_sec_uid/source：{_n} 条")
    except Exception as _e:
        print(f"   [启动] author_sec_uid 回填失败（不影响启动）：{_e}")
    app.run(host="127.0.0.1", port=15801, debug=False)
