"""video2text Web 管理界面 - Flask Backend"""
import json, os, sys, threading, traceback
from pathlib import Path
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

# ── 添加项目根到 path ──
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as collector  # main.py

# ── 路径 ──
EXCEL_PATH    = ROOT / "output" / "抖音视频信息.xlsx"
INDEX_PATH    = ROOT / "video_index.json"
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
        videos = [v for v in videos if v.get("topic", "") == topic]
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
        t = v.get("topic", "未分类")
        topics[t] = topics.get(t, 0) + 1
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
        )
        # 如果 total=0，给出提示
        if result.get('total', 0) == 0 and not result.get('error'):
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
            result = fetch_user_videos(url=url, cookie=cookie, max_pages=max_pages, mode=mode)

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

            _task_status["progress"] = f"✅ 全部 {len(videos)} 条处理完成！"
            _task_status["done"] = True

        except Exception as e:
            _task_status["error"] = traceback.format_exc()
            _task_status["done"] = True
        finally:
            _task_status["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/")
def index():
    return send_from_directory(str(ROOT / "web" / "templates"), "index.html")

if __name__ == "__main__":
    print(f"🎬 video2text Web 面板")
    print(f"   Excel: {EXCEL_PATH}")
    print(f"   启动: http://127.0.0.1:15801")
    app.run(host="127.0.0.1", port=15801, debug=False)
