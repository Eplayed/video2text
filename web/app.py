"""video2text Web 管理界面 - Flask Backend"""
import json, os, sys, threading, traceback
from pathlib import Path
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

# ── 添加项目根到 path ──
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as collector  # main.py
from src import content_store

# ── 路径 ──
EXCEL_PATH    = ROOT / "output" / "抖音视频信息.xlsx"
INDEX_PATH    = ROOT / "video_index.json"
DB_PATH       = ROOT / "output" / "video2text.db"
PARSER_DIR    = Path(os.environ.get("DOUYIN_PARSE_DIR", "/tmp/douyin_parse"))

app = Flask(__name__, static_folder="static", static_url_path="/static")

# ── 处理状态（内存） ──
_task_status = {"running": False, "progress": "", "done": False, "error": ""}

# ── 工具：cookie 写入与 parser 初始化 ──
def _setup_parser_and_cookie(cookie_str: str):
    """写入 cookie 文件并初始化 parser（自动加上 sessionid= 前缀）"""
    # 自动加上 sessionid= 前缀
    if cookie_str and "sessionid=" not in cookie_str:
        cookie_str = f"sessionid={cookie_str}"
    if not PARSER_DIR.exists():
        raise RuntimeError(f"douyin_parse 目录不存在: {PARSER_DIR}")
    cookie_path = PARSER_DIR / "douyin_cookie.txt"
    with open(cookie_path, "w") as f:
        f.write(cookie_str)
    os.chdir(str(PARSER_DIR))
    return collector.get_douyin_parser(str(PARSER_DIR))


def _sync_content_db():
    """把 Excel 当前内容同步到 SQLite 内容库。"""
    if not EXCEL_PATH.exists():
        raise RuntimeError(f"Excel 不存在: {EXCEL_PATH}")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return content_store.sync_excel_to_db(EXCEL_PATH, DB_PATH)


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
    return {"method": method, "api_key": api_key, "api_base": api_base, "model": model}

# ── API: 视频列表 ──
@app.route("/api/videos")
def api_videos():
    topic = request.args.get("topic", "")
    q     = request.args.get("q", "").lower()
    if not INDEX_PATH.exists():
        return jsonify({"videos": [], "total": 0})
    with open(INDEX_PATH, encoding="utf-8") as f:
        data = json.load(f)
    videos = data.get("videos", [])
    if topic:
        videos = [v for v in videos if v.get("author", "") == topic]
    if q:
        videos = [v for v in videos if q in v.get("title", "").lower()
                   or q in v.get("author", "").lower()
                   or q in v.get("description", "").lower()]
    return jsonify({"videos": videos, "total": len(videos)})

# ── API: 视频详情 ──
@app.route("/api/videos/<sheet>/<int:row>")
def api_video_detail(sheet, row):
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
    wb.close()
    return jsonify(detail)

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
                    _task_status["error"] += f"Row {row}: 处理失败\n"

                if i < len(link_list) - 1:
                    import time
                    time.sleep(5)

            # 4. 更新索引
            _task_status["progress"] = "更新索引..."
            collector.update_video_index(str(EXCEL_PATH))
            _sync_content_db()

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

    if not url:
        return jsonify({"error": "请输入用户主页链接或视频链接"}), 400
    if not cookie:
        return jsonify({"error": "请输入 Cookie"}), 400

    try:
        from src.fetch_user_videos import fetch_user_videos
        mode = "user_url" if "/user/" in url else "video_url"
        result = fetch_user_videos(
            url=url, cookie=cookie,
            max_pages=max_pages, mode=mode,
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
    max_pages = min(int(data.get("max_pages", 1)), 5)

    # DEBUG LOG
    import json as _j
    print(f"[DEBUG] preview_user_videos: url={url[:50]}... cookie={cookie[:20]}... max_pages={max_pages}", flush=True)

    if not url:
        return jsonify({"error": "请输入链接"}), 400
    if not cookie:
        return jsonify({"error": "请输入 Cookie"}), 400

    try:
        from src.fetch_user_videos import fetch_user_videos
        mode = "user_url" if "/user/" in url else "video_url"
        result = fetch_user_videos(
            url=url, cookie=cookie,
            max_pages=max_pages, mode=mode,
            exclude_excel=str(EXCEL_PATH),
        )
        # 如果 total=0，给出提示（但如果是去重导致的，不提示）
        if result.get('total', 0) == 0 and not result.get('error'):
            # 检查是否是去重导致的
            if result.get('filtered'):
                result['warning'] = f"所有视频已存在（去重 {result.get('filtered')} 条）"
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
    asr_model = data.get("asr_model", "base")

    if not url:
        return jsonify({"error": "请输入用户主页链接或视频链接"}), 400
    if not cookie:
        return jsonify({"error": "请输入 Cookie"}), 400

    _task_status = {"running": True, "progress": "准备中...", "done": False, "error": "", "total": 0, "current": 0}

    def run():
        global _task_status
        import openpyxl, time
        try:
            # 1. 获取视频列表
            _task_status["progress"] = "获取视频列表..."
            from src.fetch_user_videos import fetch_user_videos
            mode = "user_url" if "/user/" in url else "video_url"
            result = fetch_user_videos(url=url, cookie=cookie, max_pages=max_pages, mode=mode,
                               exclude_excel=str(EXCEL_PATH))

            if not result.get("success") or result.get("total", 0) == 0:
                _task_status["error"] = f"获取视频列表失败: {result.get('error', '0条视频')}"
                _task_status["done"] = True
                _task_status["running"] = False
                return

            videos = result["videos"]
            user_url = result.get("user_url", "")
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
                    _task_status["error"] += f"Row {row}: 处理失败\n"

                if i < len(videos) - 1:
                    time.sleep(5)

            # 5. 更新索引
            _task_status["progress"] = "更新索引..."
            collector.update_video_index(str(EXCEL_PATH))
            _sync_content_db()

            _task_status["progress"] = f"✅ 全部 {len(videos)} 条处理完成！"
            _task_status["done"] = True

        except Exception as e:
            _task_status["error"] = traceback.format_exc()
            _task_status["done"] = True
        finally:
            _task_status["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})


# ── API: 批量AI优化选中视频 ───────────────────────────────────────────────
@app.route("/api/optimize_batch", methods=["POST"])
def api_optimize_batch():
    """对选中的视频进行AI文案优化"""
    global _task_status
    if _task_status.get("running"):
        return jsonify({"error": "已有任务正在运行"}), 400

    data = request.get_json(force=True)
    videos = data.get("videos", [])

    if not videos:
        return jsonify({"error": "请选择要优化的视频"}), 400

    _task_status = {"running": True, "progress": "准备中...", "done": False, "error": "", "success": 0}

    def run():
        global _task_status
        import openpyxl, time
        try:
            wb = openpyxl.load_workbook(str(EXCEL_PATH))
            ws = wb["抖音视频数据"]
            
            success_count = 0
            for i, key in enumerate(videos):
                try:
                    sheet, row = key.split(":")
                    row = int(row)
                    _task_status["progress"] = f"优化 [{i+1}/{len(videos)}] 第{row}行..."
                    
                    # 读取ASR文本
                    asr_text = ws.cell(row, 7).value or ""
                    if not asr_text.strip():
                        continue
                    
                    # 调用AI优化（简化版：直接标记为已处理）
                    # TODO: 接入真实LLM API
                    ws.cell(row, 12).value = f"[AI优化] {asr_text[:100]}..." 
                    ws.cell(row, 2).value = "已优化"
                    success_count += 1
                    
                except Exception as e:
                    _task_status["error"] += f"{key}: {e}\n"
            
            wb.save(str(EXCEL_PATH))
            wb.close()
            
            # 更新索引
            collector.update_video_index(str(EXCEL_PATH))
            _sync_content_db()
            
            _task_status["progress"] = f"✅ 完成 {success_count}/{len(videos)} 个优化"
            _task_status["success"] = success_count
            _task_status["done"] = True

        except Exception as e:
            _task_status["error"] = traceback.format_exc()
            _task_status["done"] = True
        finally:
            _task_status["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started", "total": len(videos)})


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
    """"重新生成指定整理稿（基于本地模板）"""
    try:
        video = content_store.get_video_by_source(DB_PATH, None, None,
                                                  video_id=summary_id)
        # 通过 summary 找到对应的 video
        summary = content_store.get_summary(DB_PATH, summary_id)
        if not summary:
            return jsonify({"error": "整理稿不存在"}), 404
        video = content_store.get_video_by_source(DB_PATH, summary["source_sheet"], summary["source_row"])
        if not video:
            return jsonify({"error": "关联视频不存在"}), 404

        summary_type = request.args.get("type") or summary.get("summary_type", "game_guide")
        config = _ai_config()
        result, model, status = content_store.generate_summary(video, summary_type, config)

        # 更新已有记录
        content_store.update_summary(DB_PATH, summary_id, result, model, status)
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
    if summary_type not in ("game_guide", "ai_interview"):
        return jsonify({"error": "summary_type 只能是 game_guide 或 ai_interview"}), 400
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

            for i, key in enumerate(videos):
                try:
                    sheet, row_text = key.split(":", 1)
                    row = int(row_text)
                    _task_status["progress"] = f"整理 [{i+1}/{len(videos)}] {sheet} 第{row}行..."
                    video = content_store.get_video_by_source(DB_PATH, sheet, row)
                    if not video:
                        _task_status["error"] += f"{key}: 内容库中找不到视频\n"
                        continue
                    if not (video.get("transcript") or "").strip():
                        _task_status["error"] += f"{key}: 没有 ASR 文本，跳过\n"
                        continue

                    result, model, status = content_store.generate_summary(
                        video, summary_type, config
                    )
                    summary_id = content_store.save_summary(
                        DB_PATH,
                        video_id=video["id"],
                        summary_type=summary_type,
                        result=result,
                        model=model,
                        status=status,
                    )
                    summary_ids.append(summary_id)
                    success_count += 1
                except Exception as e:
                    _task_status["error"] += f"{key}: {e}\n"

            _task_status["success"] = success_count
            _task_status["summary_ids"] = summary_ids
            _task_status["progress"] = f"✅ 已生成 {success_count}/{len(videos)} 条整理稿"
            _task_status["done"] = True
        except Exception:
            _task_status["error"] = traceback.format_exc()
            _task_status["done"] = True
        finally:
            _task_status["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started", "total": len(videos), "summary_type": summary_type})


@app.route("/")
def index():
    return send_from_directory(str(ROOT / "web" / "templates"), "index.html")

if __name__ == "__main__":
    print(f"🎬 video2text Web 面板")
    print(f"   Excel: {EXCEL_PATH}")
    print(f"   启动: http://127.0.0.1:15801")
    app.run(host="127.0.0.1", port=15801, debug=False)
