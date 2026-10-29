"""video2text Web 管理界面 - Flask Backend"""
import json, os, re, sys, sqlite3, threading, time, traceback, hashlib, uuid, io
from pathlib import Path
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

# ── 添加项目根到 path ──
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as collector  # main.py
from src import content_store, material_store, toutiao_graphics, ai_poster, sync_tasks, topic_pack, supplements
from src import draft_audit, review_store, toutiao_stats, humanize_routes, ops_store
from src import ai_quota
from src import ai_thinking
from src import ai_models        # 主档/快速档按到期时间自动选模型（2026-10-29）
from src import fact_gate, risk_ledger, release_review   # 待核台账 + 发布证据门 + 放行记录（2026-10-29）
from src import bank_board
from src import plan_tasks        # 排产任务 ↔ 素材 ↔ 整理稿的关联层（2026-10-20 第一单）
from src import plan_brief        # 「今天该发哪一题」的唯一判据（日报/颜色/接口共用）
from src import plan_series       # 双系列位置、候选采用、发布真相、夜间匹配（2026-10-22）
from src.plan_series import SLOT_KEY_MAX
from src.graphics.channels import wechat as wechat_graphics
from src.graphics.channels import toutiao as toutiao_channel
from src.graphics import variants as graphics_variants
from src.graphics.gate import GraphicsGateError
from src.path_config import find_parser_dir, ensure_parser_on_path, get_cookie_path

# ── 路径 ──
OUTPUT_DIR    = ROOT / "output"
EXCEL_PATH    = OUTPUT_DIR / "抖音视频信息.xlsx"
INDEX_PATH    = ROOT / "video_index.json"
DB_PATH       = OUTPUT_DIR / "video2text.db"
PARSER_DIR    = Path(find_parser_dir())

# DB 直写、不进 Excel 的素材源：{videos.source_sheet: 前端来源标识}。
# 加一个新采集源就动这一处——素材库追加、来源派生、来源计数、详情与删除的"绕开 Excel"
# 全从这里取，别再在下游写 `== '微信文章'` 的二元判断（历史上就是这么把新源判成抖音的）。
# 「抖音图文」的 key 是 douyin_note 而不是 douyin：同一个订阅一次同步会同时产出
# 视频和图文两种素材，来源栏要能分开看（号主 2026-10-16 拍板单列一档）。
DB_ONLY_SHEETS = {"微信文章": "wechat", "B站视频": "bilibili", "抖音图文": "douyin_note",
                  "暴雪官方": "blizzard"}
# B 站每轮最多翻多少条：首次订阅即"只入最近 20 条"，之后每轮也只拉新增（列表从新往老翻，
# 整页都是已入库的就停）。存量大 UP 主不要一次全量回填——几百条字幕连打会撞风控。
BILI_SYNC_LIMIT = 20
# 抖音图文每轮最多入多少条（和 B 站同口径：只吃最近一屏，不做历史全量回填）。
# 一条图文 3~9 张图、每张实测 100~200KB，一轮 20 条约 20~40MB，落 output/douyin_notes/。
NOTE_SYNC_LIMIT = 20
# 素材库列表里每个 DB 直写源一次最多带多少条。原来是写死的 200，B 站合集一次就入 300 条
# （号主 2026-10-27 要的），200 会把多出来的那 100 条藏起来——表现正是"同步说明明进了
# 300 条，素材库却只有 200"，而数据其实都在库里。加到 600 覆盖这一档，再大就要改成翻页。
DB_ONLY_LIST_LIMIT = 600

app = Flask(__name__, static_folder="static", static_url_path="/static")
# 响应里的中文不再逐字转义成 \uXXXX：这一层几乎全是中文——/api/videos 90 条 160KB 原文
# 被撑到 318KB，/api/content/summaries 46 篇 476KB 撑到 895KB，浏览器还要多解一遍。
# JSON 规范默认就是 UTF-8，浏览器 response.json() 与 Python json.load 都直接吃。
app.json.ensure_ascii = False

# 免费额度记账挂在 openai SDK 的边界上（一次包住全部调用点，理由见 src/ai_quota.py）。
# 挂不上不拦启动，但 /api/ai/quota 会带着 last_error，界面会明说"额度没在记账"。
# seed_from_env() 会连带装上"会思考的模型关掉思考"那一层（src/ai_thinking.py），
# 让 main.py 与 scripts/e2e_ai_poster.py 走的是同一套边界，不是只有工作台这一条路。
_quota_hook = ai_quota.seed_from_env()

# ── 处理状态（内存） ──
_task_status = {"running": False, "progress": "", "done": False, "error": ""}


def _run_progress_cb(status):
    """把产线的 `progress_cb(msg[, done, total])` 接到某一份任务状态上。

    头条卡 / 公众号卡 / 公众号成稿各有一份状态字典，回调以前各写各的（有的只写 progress）。
    步数字段统一在这里落，界面那根进度条只认 `step/steps`：拿不到就画流动条，不编百分比。
    """
    def cb(msg, done=None, total=None):
        status["progress"] = msg
        status["step"] = done
        status["steps"] = total
    return cb
_classify_status = {"running": False, "progress": "", "done": True, "error": "", "result": None}
# 这把锁只管**写**：同步、打标、增删订阅都在它后面串行，避免两条写流程互相踩。
# 读接口一律不拿这把锁（2026-10-14 改）。原来读也排队等锁，而出图前那一轮自动同步
# 会在锁里跑 `classify_videos`（几百条素材、每条一次 LLM，实测十几分钟），
# 于是界面侧的 /api/subscriptions、/api/categories 全在外面干等，浏览器超时后
# 把"还没返回"画成"0 个"——号主看到的"数据全没了"就是这么来的（temp/lock_probe.py 实测：
# 写事务开着时，只读 SELECT 0.00s 就能返回，SQLite 自己不怕读写并发，怕的是这把 Python 锁）。
# 读要拿连接就走 content_store.open_readonly()：mode=ro，不可能顺手改库结构。
_db_lock = threading.RLock()

# ── 工具：cookie 写入与 parser 初始化 ──
def _setup_parser_and_cookie(cookie_str: str):
    """写入 cookie 文件并初始化 parser（自动加上 sessionid= 前缀）。
    不使用 os.chdir（会影响 Flask 工作目录），而是通过 set_cookie() 直接设置。
    """
    # 自动加上 sessionid= 前缀
    if cookie_str and "sessionid=" not in cookie_str:
        cookie_str = f"sessionid={cookie_str}"

    # ── Cookie 合并保护（2026-09-21，2026-10-12 修好）：短 cookie 不覆盖键更多的完整串 ──
    # 抖音 Argus 风控要求完整安全 Cookie（UIF_ID/ttwid/msToken 等），前端 localStorage
    # 里可能仍存着旧的纯 sessionid，直接写入会把完整串冲掉，导致同步全部返回空。
    # 原来这道保护查的是 `"uifid=" in existing`，而抖音真实的键叫 **UIF_ID**（带下划线），
    # 小写比对永远匹配不上 → 它从来没生效过，今天文件里只剩 42 字节就是这么来的。
    # 改成"谁的键多谁赢"，不再依赖某一个键名。
    try:
        cookie_path = get_cookie_path()
        if cookie_str and len(cookie_str) < 200 and cookie_path.exists():
            existing = cookie_path.read_text(encoding="utf-8").strip()
            if len(_cookie_keys(existing)) > len(_cookie_keys(cookie_str)):
                m_new = re.search(r"sessionid=([^;]+)", cookie_str)
                m_old = re.search(r"sessionid=([^;]+)", existing)
                if m_new and m_old and m_new.group(1) != m_old.group(1):
                    # sessionid 变了（重新登录）：只更新完整串里的 sessionid 值
                    existing = re.sub(
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


# ── 抖音 cookie 体检：少哪个键、真打一次过不过风控 ──────────────────────
# 为什么要这个：Argus 的报错是一句 "Uifid Not Found"，界面以前只会说"cookie 无效"，
# 人不知道该补什么。以前那个输入框占位符还写着 `sessionid=xxxx`，等于教人只粘一个键，
# 结果就是今天这样：文件里只剩 42 字节，直链解析全被拦，帧池补画面走不通。
# 键名以浏览器实测为准：抖音写的是 **UIFID**（没有下划线），Argus 的报错却写成 "Uifid"，
# 以前照报错写成 UIF_ID，结果体检永远报"缺 UIF_ID"——两道判据都对不上号。
# 所以这里按别名组比对，sessionid/ttwid 是 httpOnly（document.cookie 抓不到），缺了只提示不拦。
DY_COOKIE_REQUIRED = [("sessionid", ["sessionid"]), ("UIFID", ["uifid", "uif_id"]),
                    ("__ac_nonce", ["__ac_nonce"]), ("__ac_signature", ["__ac_signature"])]
DY_COOKIE_NICE = [("ttwid", ["ttwid"]), ("msToken", ["mstoken"]), ("webid", ["webid"])]


def _cookie_keys(raw):
    out = {}
    for part in (raw or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = len(v.strip())
    return out


def _douyin_cookie_state():
    p = get_cookie_path()
    raw = ""
    try:
        raw = p.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    keys = _cookie_keys(raw)
    missing = [name for name, alts in DY_COOKIE_REQUIRED if not any(a in keys for a in alts)]
    soft = [name for name, alts in DY_COOKIE_NICE if not any(a in keys for a in alts)]
    return {"bytes": len(raw), "nkeys": len(keys), "missing": missing, "missing_soft": soft,
            "complete": not missing, "path": str(p)}


def _douyin_cookie_live(aweme_id):
    """真发一次详情请求。返回 (通不通过, 给人看的一句话)"""
    try:
        ensure_parser_on_path()
        import requests
        from douyin_video_parser import DouyinVideoParser
    except Exception as e:
        return False, "解析器进不去：%s" % repr(e)[:80]
    parser = DouyinVideoParser()
    parser.set_cookie(get_cookie_path().read_text(encoding="utf-8").strip()
                      if get_cookie_path().exists() else "")
    params = dict(parser.BASE_PARAMS)
    params["aweme_id"] = aweme_id
    url = parser._sign_abogus_url("https://www.douyin.com/aweme/v1/web/aweme/detail/", params)
    if not url:
        return False, "签名（a_bogus）没生成，多半是 vendor/douyin_parse 那份解析器坏了"
    try:
        r = requests.get(url, headers=parser._build_headers(
            "https://www.douyin.com/video/%s" % aweme_id), timeout=25)
    except Exception as e:
        return False, "请求没发出去：%s" % repr(e)[:90]
    body = (r.text or "").strip()
    if r.status_code == 200 and '"aweme_detail"' in body:
        return True, "风控放行，能取到视频详情（直链解析可用）"
    return False, "HTTP %s：%s" % (r.status_code, body[:100] or "空响应")


@app.route("/api/douyin/cookie", methods=["GET", "POST"])
def api_douyin_cookie():
    """GET：只报本地文件里有哪些键、缺什么（不打网络）。
    POST：可带 cookie 先落盘，再真发一次详情请求验证能不能过风控。"""
    st = _douyin_cookie_state()
    if request.method == "GET":
        return jsonify(st)
    data = request.get_json(silent=True) or {}
    pasted = str(data.get("cookie") or "").strip()
    if pasted:
        try:
            _setup_parser_and_cookie(pasted)
        except Exception as e:
            return jsonify({"error": "写入失败：%s" % str(e)[:120]}), 400
        st = _douyin_cookie_state()
    row = None
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT aweme_id FROM videos WHERE aweme_id IS NOT NULL"
                               " AND aweme_id GLOB '[0-9]*' ORDER BY updated_at DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        pass
    aid = (row["aweme_id"] or "") if row else ""
    if not aid:
        return jsonify(dict(st, live=None, note="库里没有抖音视频可测"))
    ok, msg = _douyin_cookie_live(aid)
    return jsonify(dict(st, aweme_id=aid, live={"ok": ok, "msg": msg}))


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
    note = ""
    if method == "deepseek":
        api_key = collector.config_get(config, "DEEPSEEK_API_KEY") or collector.config_get(config, "AI_API_KEY")
        api_base = collector.config_get(config, "DEEPSEEK_API_BASE", "https://api.deepseek.com")
        model = collector.config_get(config, "DEEPSEEK_MODEL", "deepseek-v4.1-flash")
    else:
        api_key = collector.config_get(config, "OPENAI_API_KEY") or collector.config_get(config, "AI_API_KEY")
        api_base = collector.config_get(config, "AI_API_BASE", "https://api.openai.com/v1")
        manual = collector.config_get(config, "AI_MODEL", "gpt-4o-mini")
        # 主档模型名：配了候选池就按**到期时间长短**自动选（2026-10-29 号主："根据失效长短自动选择"，
        # 且已把「免费额度用尽即停」全开——撞墙只会 403，不会悄悄按量扣钱，正好让产线自己跳）。
        # 整条池子都不可用时退回写死的 AI_MODEL，但原因留在 model_note 里由界面说出来——
        # 静默换一个模型还不吭声，是这仓库被反复抓到过的那类错。
        model, note = ai_models.resolve("main")
        model = model or manual
    cfg = {"method": method, "api_key": api_key, "api_base": api_base, "model": model,
           "model_note": note,
           "fast_model": collector.config_get(config, "AI_MODEL_FAST", ""),
           # 快速档可以另用一把 key（号主 2026-10-28：这一档换到 qwen3.8-omni-flash，
           # 只给它配新 key，主档/看图那几档还照旧那把）。没配就等于跟主档同一把。
           "fast_api_key": collector.config_get(config, "AI_API_KEY_FAST", ""),
           # 看图那类活（拆参考图做模板、判帧、出图回读）单独一档，默认走 qwen3-vl-plus；
           # 以后想换更强的视觉模型只改配置里的 AI_MODEL_VISION，不用动代码
           "vision_model": collector.config_get(config, "AI_MODEL_VISION", "")}
    return cfg


def _fast_config():
    """拆解与卡片化走「快速模型」：这类活是照抄压缩，不需要主模型深思。

    模型名同样先过候选池（`AI_MODEL_POOL_FAST`，没单配就共用主档那条池）——
    这一档是号主每天点的整理稿/拆文案/图文卡，额度用尽或到期最该自动跳的就是它。

    实测同一条素材（当时跑的是 deepseek-v4-flash，现已换 v4.1-flash，数字未重测）：
    主模型 qwen3.8-max 拆解约 180 秒，deepseek-v4-flash 33 秒且上图
    字数更守规矩（71 字 vs 超线）；qwen-plus 只要 9.7 秒、便宜五倍，但会把上图文字
    写到 98 字（超 80 线，会被自动压缩砍掉一张卡）。
    没配 AI_MODEL_FAST 也没配池子时原样返回主配置，行为零变化。
    """
    cfg = _ai_config()
    fast, note = ai_models.resolve("fast")
    fast = fast or (cfg.get("fast_model") or "").strip()
    if fast:
        cfg = dict(cfg)
        cfg["model"] = fast
        cfg["model_note"] = note or "手动指定（没配候选池）"
        # 这一档另配了 key 就换掉（网关同一个，key 是按模型/额度分开的）
        fk = (cfg.get("fast_api_key") or "").strip()
        if fk:
            cfg["api_key"] = fk
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


# ── video_index.json 进程内缓存（性能：见 2026-10-23 排产表请求风暴那次）──────────
# 这份索引 2.6MB，以前**每一次 /api/videos 都从磁盘重读重解析一遍**（实测 32ms/次）。
# 排产表一次进页面按每个槽位的搜索词各发一发查询（50+ 发），光解析就 1.6 秒，
# 把人点菜单要的那几发请求挤在后面——表现就是"点素材库/整理稿/AI海报都卡一下"。
# 判据用 (mtime, 文件大小)：外部脚本重建索引后下一次就读到新的，不要求调用方记得清缓存。
_INDEX_CACHE = {"key": None, "data": {}}


def _index_raw() -> dict:
    """整份 video_index.json（带缓存）。**返回的是缓存本体，别往上改字段**——
    要改请先 `_index_videos()` 拿拷贝，否则上一次请求补的字段会渗到下一次。"""
    try:
        st = os.stat(INDEX_PATH)
    except OSError:
        _INDEX_CACHE["key"], _INDEX_CACHE["data"] = None, {}
        return {}
    key = (int(st.st_mtime), st.st_size)
    if _INDEX_CACHE["key"] != key:
        try:
            with open(INDEX_PATH, encoding="utf-8") as f:
                _INDEX_CACHE["data"] = json.load(f)
            _INDEX_CACHE["key"] = key
        except (ValueError, OSError):
            return {}
    return _INDEX_CACHE["data"] or {}


def _index_videos() -> list:
    """videos 列表，每条一份浅拷贝（补字段用，实测 874 条拷贝 1ms）。"""
    return [dict(v) for v in (_index_raw().get("videos") or [])]


def _index_invalidate():
    _INDEX_CACHE["key"], _INDEX_CACHE["data"] = None, {}


def _material_rows():
    """素材库列表真正能返回的那批行（Excel 索引 + DB_ONLY_SHEETS 直写源）+ 筛选前的三份计数。

    「全部作者」下拉必须与这里同源。以前它直接对整张 videos 表 group by，把还没接进列表的
    源也列了出来（NGA 板块 1105 行，那列 author 存的是发帖人、不是内容来源），
    表现就是名单里一排"我没订阅的人"，点下去 0 条。
    """
    videos = []
    if INDEX_PATH.exists():
        videos = _index_videos()
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

    # 追加 SQLite 直写的素材（微信文章 / B站视频）。这两类不落 Excel、不进 video_index.json，
    # DB 就是权威源，索引重建不会丢。
    # 原先这里只写死 '微信文章' 一个 sheet：B站入库后素材库看不到，表现就是"同步说新增了 5 条，
    # 页面上却没有"——所以 DB-only 的源一律走这张映射表加，不再新写一块重复代码。
    if DB_PATH.exists():
        for sheet_name, src_key in DB_ONLY_SHEETS.items():
            try:
                conn = content_store.open_readonly(DB_PATH)  # 只读：不建表、不抢写锁
                try:
                    extra_cols = (", COALESCE(view_count,0) as view_count, "
                                  "COALESCE(duration_sec,0) as duration_sec, "
                                  "COALESCE(subs_status,'') as subs_status, "
                                  "COALESCE(note_images,'') as note_images, "
                                  "COALESCE(note_image_count,0) as note_image_count")
                    rows = conn.execute(
                        "SELECT id, source_sheet, source_row, source_url, aweme_id, author, title, "
                        "status, published_at, category, ai_tags, game, remark, "
                        "COALESCE(cover_url,'') as cover, COALESCE(transcript,'') as transcript"
                        + extra_cols +
                        " FROM videos WHERE source_sheet = ? ORDER BY id DESC LIMIT ?",
                        (sheet_name, DB_ONLY_LIST_LIMIT)
                    ).fetchall()
                finally:
                    conn.close()
                for r in rows:
                    body = r["transcript"] or ""
                    cover = r["cover"] or _first_markdown_image(body)
                    item = {
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
                    }
                    if src_key == "bilibili":
                        # B站专属：卡片要显示时长与播放量，写手要知道正文是字幕还是简介
                        item["aweme_id"] = r["aweme_id"] or ""
                        item["view_count"] = r["view_count"] or 0
                        item["duration_sec"] = r["duration_sec"] or 0
                        item["subs_status"] = r["subs_status"] or ""
                    if src_key == "douyin_note":
                        # 图文专属：正文是作者文案（没有转写），图全部在本地。
                        # 封面必须用本地图而不是 cover_url——库里那个是抖音带签名的图链，
                        # x-expires 实测约一个月后失效，届时卡片就是一排裂图。
                        import json as _json
                        try:
                            note_imgs = _json.loads(r["note_images"] or "[]")
                        except ValueError:
                            note_imgs = []
                        item["aweme_id"] = r["aweme_id"] or ""
                        item["images"] = ["/media/%s" % p for p in note_imgs]
                        item["image_count"] = r["note_image_count"] or len(note_imgs)
                        item["note_stats"] = r["remark"] or ""
                        if note_imgs:
                            local_cover = "/media/%s" % note_imgs[0]
                            item["cover"] = local_cover
                            item["cover_url"] = local_cover
                    videos.append(item)
            except Exception as e:
                # 原先是裸 pass：这里一旦抛错（列缺失/DB 锁），素材库的这类内容整块消失且无痕迹，
                # 表现就是「公众号有更新但工作台看不到」。保留降级不阻断抖音列表，但必须留日志。
                print(f"[videos] {sheet_name} 追加失败（素材库将看不到这类内容）: {e}", flush=True)
    # 来源标识统一：抖音（Excel 来的）/ 微信文章 / B站视频，前端卡片徽章与筛选用
    for v in videos:
        v["source"] = DB_ONLY_SHEETS.get(v.get("sheet") or "", "douyin")
    # 药丸计数一律取"筛选前的合并列表"，跟下面真正要显示的那份数据同一个口径。
    # 之前分类药丸走 /api/categories（按 SQLite 整张 videos 表统计，含 174 条还没做转写的行），
    # 列表走索引（只收有 ASR 的行），于是"其他 240"点进去只有 128 条，每个分类都对不上。
    source_counts = {"douyin": 0, "wechat": 0, "bilibili": 0, "douyin_note": 0, "blizzard": 0}
    category_counts = {}
    game_counts = {}
    for v in videos:
        source_counts[v["source"]] = source_counts.get(v["source"], 0) + 1
        cat = (v.get("category") or "").strip() or "未分类"
        category_counts[cat] = category_counts.get(cat, 0) + 1
        if cat == "游戏攻略":            # 游戏药丸只在游戏攻略下有意义（与 get_game_stats 同口径）
            g = (v.get("game") or "").strip() or "未识别游戏"
            game_counts[g] = game_counts.get(g, 0) + 1
    return videos, source_counts, category_counts, game_counts


@app.route("/api/videos")
def api_videos():
    topic = request.args.get("topic", "")
    q     = request.args.get("q", "").lower()
    category = request.args.get("category", "")
    game = request.args.get("game", "")
    source = request.args.get("source", "")  # ''=全部 / douyin / wechat / bilibili
    videos, source_counts, category_counts, game_counts = _material_rows()
    if source == "douyin" or source in DB_ONLY_SHEETS.values():
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
    total = len(videos)
    # 分类/游戏药丸名单：按合并列表实际有的排（数量倒序），再补上权威名单里当前为 0 的分类，
    # 免得以后新加一个分类要在界面里预设它却看不到
    cats_out = [{"category": k, "count": n} for k, n in
                sorted(category_counts.items(), key=lambda x: (-x[1], x[0]))]
    known = {c["category"] for c in cats_out}
    cats_out += [{"category": c, "count": 0} for c in content_store.CATEGORIES if c not in known]
    games_out = [{"game": k, "count": n} for k, n in
                 sorted(game_counts.items(), key=lambda x: (-x[1], x[0]))]
    # 分页：素材库一次塞 1000+ 张卡片进 innerHTML 要 0.9 秒、三万个节点，
    # 而且每敲一个搜索字符都重来一遍。默认只回一页，让「加载更多」按页追加。
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.args.get("page_size") or 90)
    except (TypeError, ValueError):
        page_size = 90
    if page_size < 0:
        page_size = 90
    if page_size == 0:                      # 0＝不分页，全给（脚本侧想要完整列表时用）
        return jsonify({"videos": videos, "total": total, "sources": source_counts,
                        "categories": cats_out, "games": games_out,
                        "page": 1, "page_size": 0, "has_more": False})
    page_size = min(page_size, 500)
    start = (page - 1) * page_size
    page_items = videos[start:start + page_size]
    return jsonify({"videos": page_items, "total": total, "sources": source_counts,
                    "categories": cats_out, "games": games_out,
                    "page": page, "page_size": page_size,
                    "shown": min(start + len(page_items), total),
                    "has_more": start + len(page_items) < total})


def _load_video_extras() -> dict:
    """从 SQLite 取分类/标签与最新整理稿类型，键为 (sheet, row)。"""
    if not DB_PATH.exists():
        return {}
    try:
        conn = content_store.open_readonly(DB_PATH)  # 只读：不建表、不抢写锁
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
    """分类/游戏统计 + 权威分类名单。

    `all_categories` 是给下拉框用的（订阅预设分类、选题雷达筛选）：名单只有 `content_store.CATEGORIES`
    一处权威源。之前这两个下拉在 HTML 里写死了 7 个，而权威名单有 11 个，
    结果是「其他」「职场成长」「生活日常」「汽车资讯」这四类（合计 300 多条素材）选不出来。
    """
    if not DB_PATH.exists():
        return jsonify({"categories": [], "games": [], "all_categories": list(content_store.CATEGORIES)})
    try:
        stats = content_store.get_category_stats(DB_PATH)
        games = content_store.get_game_stats(DB_PATH)
    except Exception as e:
        return jsonify({"categories": [], "games": [], "error": str(e)}), 500
    return jsonify({"categories": stats, "games": games,
                    "all_categories": list(content_store.CATEGORIES)})


# 选题雷达那三个字的接口已于 2026-10-20 删除（路径与雷达页同名）：排产表（/static/schedule.html）
# 直接查素材库 + 人工钉选，热度聚合那一套没人再打开。旧 #radar 链接在前端导向 #schedule。
# 渠道策略接口 /api/strategy/channels **保留**（那是权威源下发口，不只雷达页在用）。

# ── API: 渠道策略配置下发（自媒体工作台策略引擎的单一权威源） ──
@app.route("/api/strategy/channels")
def api_strategy_channels():
    """工作台前端策略引擎（channel-strategy.js）从这里拉取策略口径。"""
    return jsonify(content_store.get_channel_strategy_config())


# ── API: AI 批量分类 ──
def _auto_classify_after_sync(only_ids=None) -> dict:
    """同步入库后的增量自动打标：只补 category/ai_tags 任一为空的视频。

    - AI 未配置（method=skip 或无 key）时静默返回空结果，不阻塞采集主流程；
    - 沿用 classify_videos 的 LLM 优先、失败降级规则分类；
    - 调用方在采集线程里，此函数同步执行（单条链接量小可接受）。
      持锁口径与 /api/videos/classify 一致。
    - **only_ids**：只标这一批。订阅同步收尾必须传，因为不带范围的那句 WHERE 没有上限，
      会把全库 category/ai_tags 为空的存量一起过一遍真实 LLM（缺口有多少条取决于
      上一次打标到现在的间隔，点同步前看不出来）。传空列表表示这轮没新增，直接跳过。
    """
    ids = [int(i) for i in (only_ids or [])]
    if only_ids is not None and not ids:
        return {"total": 0, "classified": 0, "llm_batches": 0, "skipped": "nothing_new"}
    try:
        cfg = _ai_config()
        if cfg.get("method") == "skip" or not cfg.get("api_key"):
            return {"total": 0, "classified": 0, "llm_batches": 0, "skipped": "ai_not_configured"}
        with _db_lock:
            return content_store.classify_videos(DB_PATH, cfg, force=False, only_ids=ids or None)
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
def _video_detail_from_db(video, sheet, row):
    """库里那一行 → 详情响应。与抖音视频那份同构，前端弹窗零特判。

    单独成一个函数是因为有两个入口要用同一份：登记过的 DB 直写源（微信/B站/图文/暴雪），
    以及**库里存着但还没登记成直写源的表**（NGA 板块）。后者以前直接 404——
    排产格「补充资料」里点论坛帖的「看原文」就是一颗死按钮。
    """
    body = video.get("transcript") or ""
    cover = video.get("cover_url") or _first_markdown_image(body)
    return {
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
        "remark": video.get("remark") or "",
        "sheet": sheet,
        "row": row,
        "keyframes": [],
        "source": DB_ONLY_SHEETS.get(sheet, "db"),
    }


@app.route("/api/videos/<sheet>/<int:row>")
def api_video_detail(sheet, row):
    # DB 直写的源（微信文章 / B站视频）：不落 Excel，DB 是权威源；
    # 响应结构与抖音视频同构，前端弹窗零特判
    if sheet in DB_ONLY_SHEETS:
        if not DB_PATH.exists():
            return jsonify({"error": "内容库不存在"}), 404
        video = content_store.get_video_by_source(DB_PATH, sheet, row)
        if not video:
            return jsonify({"error": f"{sheet} R{row} 不存在"}), 404
        detail = _video_detail_from_db(video, sheet, row)
        if DB_ONLY_SHEETS[sheet] == "bilibili":
            detail["id"] = video.get("aweme_id") or ""
            detail["bvid"] = video.get("aweme_id") or ""
            detail["view_count"] = video.get("view_count") or 0
            detail["duration_sec"] = video.get("duration_sec") or 0
            detail["subs_status"] = video.get("subs_status") or ""
        if DB_ONLY_SHEETS[sheet] == "douyin_note":
            # 图文：正文只有作者文案（asr 那份就是），"画面"这一格摆的是本地存下来的图，
            # 不是抽帧——没有视频可抽。封面同理改指本地图，库里那个抖音签名链会过期。
            import json as _json
            try:
                note_imgs = _json.loads(video.get("note_images") or "[]")
            except ValueError:
                note_imgs = []
            detail["id"] = video.get("aweme_id") or ""
            detail["images"] = ["/media/%s" % p for p in note_imgs]
            detail["image_count"] = video.get("note_image_count") or len(note_imgs)
            detail["note_stats"] = video.get("remark") or ""
            detail["remark"] = video.get("remark") or ""
            if note_imgs:
                detail["cover"] = "/media/%s" % note_imgs[0]
        _attach_related_summaries(detail, sheet, row)
        return jsonify(detail)

    if DB_PATH.exists() and sheet not in {(v.get("sheet") or "") for v in _index_videos()}:
        # 库里存着、但既没登记成 DB 直写源、也不在 Excel 索引里的表（NGA 板块就是这状态）：详情别 404。
        # 判据用"索引里有没有这张表"现算，不写死表名——写死就会跟素材库那份账再次对不上。
        # 这颗「看原文」按钮在排产格补充资料里会被点到，死链等于界面说反话。
        hit = content_store.get_video_by_source(DB_PATH, sheet, row)
        if hit:
            detail = _video_detail_from_db(hit, sheet, row)
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
    # 已抽取的关键帧（生成攻略文章时落盘），供自媒体工作台取材配图。
    # 帧池优先：老的 keyframes/ 依赖已废的抖音解析器，9-29 以后采的那一侧全是空目录
    try:
        aweme_id = material_store.extract_aweme_id(detail.get("link") or detail.get("video_url") or "")
        pool_items, pool_judged, pool_present = _pool_frame_items(aweme_id, 6)
        if pool_present:
            detail["keyframes"] = [it["url"] for it in pool_items]
            detail["keyframes_judged"] = pool_judged
        else:
            frame_paths = [p for p in material_store.local_keyframe_paths(aweme_id) if not p.endswith("sheet.jpg")]
            detail["keyframes"] = [f"/media/{p}" for p in frame_paths]
            detail["keyframes_judged"] = False
    except Exception:
        detail["keyframes"] = []
        detail["keyframes_judged"] = False
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
                conn = content_store.open_readonly(DB_PATH)  # 只读：不建表、不抢写锁
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
    """Serve local output assets such as covers and keyframes.

    `?w=320` 这类缩略图请求：列表页以前每张卡都拉原图（海报一张 600-750KB，
    20 张卡一屏就是 13-20MB，AI 海报列表那一眼最明显）。这里按宽度等比缩，
    结果落在 `output/_thumb/` 里按 (文件 mtime, 宽度) 命名，第二次直接送缓存。
    只认 64-1600 的整数宽度，其余一律按原图走——不给人拿这个接口去生成任意尺寸。
    """
    raw = request.args.get("w", "")
    try:
        want = int(raw)
    except (TypeError, ValueError):
        want = 0
    src = (OUTPUT_DIR / filename).resolve()
    # 小于这个头的图不值得缩：本机回环带宽不是瓶颈，浏览器解码也不吃力，
    # 而每次缩放要花 60-250ms 的 CPU——素材库一页 50 张封面，那笔账会算到第一次点进去的人头上。
    BIG_ENOUGH = 180 * 1024
    if want and 64 <= want <= 1600 and str(src).startswith(str(OUTPUT_DIR.resolve())) \
            and src.is_file() and src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") \
            and os.stat(src).st_size > BIG_ENOUGH:
        try:
            st = os.stat(src)
            cache = OUTPUT_DIR / "_thumb" / ("%s-%d-%d-%d.jpg"
                                             % (hashlib.sha1(str(src).encode()).hexdigest()[:16],
                                                want, int(st.st_mtime), st.st_size))
            if not cache.is_file():
                from PIL import Image
                im = Image.open(src).convert("RGB")
                if im.width > want:
                    im = im.resize((want, max(1, int(round(im.height * want / float(im.width))))),
                                   Image.LANCZOS)
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix(".part")
                im.save(tmp, "JPEG", quality=82, optimize=True)
                os.replace(tmp, cache)     # 原子落盘：并发请求同一张也不会读到半个文件
            return send_from_directory(cache.parent, cache.name, mimetype="image/jpeg")
        except Exception as e:
            print("[media] 缩略图没成，回原图：%s" % e, flush=True)
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/api/workbench")
def api_workbench():
    try:
        if not DB_PATH.exists():
            _sync_content_db()
        q = request.args.get("q", "")
        author = request.args.get("author", "")
        status = request.args.get("status", "")
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
        _index_invalidate()
        # 以前是 `len(json.load(...))` —— 数的是顶层键的个数（version/videos 那几样），
        # 报出来的"共 4 条"跟实际条数没关系
        count = len(_index_videos())
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
# 「全部作者」这个下拉的口径，两次翻车各修了一半，得说清到底以谁为准：
# ① 早先是拿 video_index.json 全库 group by，把取消订阅留下的空壳行全列进来了
#    （号主 10-22 的原话："有好几条不是我订阅的"）；
# ② 10-27 改成"从库里现数"，但查的是整张 videos 表——库里那 1105 行 NGA 板块还没接进
#    素材库列表（见 docs/NGA导入接进工作台-任务书），它的 author 存的是发帖人不是来源，
#    于是下拉一口气多出 814 个"我没订阅的人"，点下去还是 0 条。
# 定论：名单只从**素材库列表真正能返回的那批行**（_material_rows）里出，两项硬条件同时成立才列——
# 一是这一行在列表里筛得到，二是这个 author 真是一个**来源**（订阅表里订到的账号，
# 或一手源那种整表就一个账号在发的）。论坛发帖人不算来源。
# 被收起来的**不藏数**，按"为什么被收起"分两堆各自报人报条数，看得见才好决定要不要清。
# 只放"整表就一个账号在发"的一手源。NGA 板块别塞进来——它 1105 行、814 个发帖人，
# author 那一列是署名不是来源，塞进来名单当场涨到 852 项且点下去 0 条（就是上面②那次翻车）。
# 等它按 docs/NGA导入接进工作台-任务书 第 1 件接进素材库列表时，要整表出一个"NGA无限板块"来源项。
FIRST_PARTY_SHEETS = ("暴雪官方",)


def _subscribed_authors():
    """订阅表里订到的账号名（判断"这个人是不是我们订的来源"的权威）。"""
    try:
        with sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True) as con:
            return {r[0].strip() for r in con.execute(
                "SELECT DISTINCT author FROM subscriptions "
                "WHERE author IS NOT NULL AND author<>''")}
    except sqlite3.Error:
        return set()


def _not_in_library(visible):
    """库里存着、但素材库列表返回不了的行，按表报条数（不藏数，也不冒充来源）。

    口径是**行级**的，不是"整张表接没接"：NGA 是整表没接（1105 行），`抖音视频数据`
    接了但仍有漏行的（没进 video_index.json 的空壳/失败行 151 条）。只按表判的话，
    首页会变成"素材 1480 + 另有 1105 ≠ 库里 2736"，那 151 条就凭空消失了——又成两份账。
    """
    buckets = {}
    try:
        with sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True) as con:
            con.row_factory = sqlite3.Row
            for r in con.execute("SELECT source_sheet, source_row, author, status FROM videos"):
                if (str(r["source_sheet"] or ""), str(r["source_row"])) in visible:
                    continue
                key = (r["source_sheet"] or "").strip() or "（没标来源）"
                b = buckets.setdefault(key, {"videos": 0, "signers": set(), "failed": 0})
                b["videos"] += 1
                b["signers"].add((r["author"] or "").strip() or "（没抓到作者）")
                if (r["status"] or "") == "失败":
                    b["failed"] += 1
    except sqlite3.Error:
        return []
    return [{"sheet": k, "videos": b["videos"], "signers": len(b["signers"]), "failed": b["failed"]}
            for k, b in sorted(buckets.items(), key=lambda kv: -kv[1]["videos"])]


def _material_stats(rows=None):
    """首页那行数字的唯一出口——一律按**素材库列表可见集**算，不按整张 videos 表。

    以前这里 `COUNT(*) FROM videos`＝2736，而素材库页头 1480：差的 1256 条是"库里躺着、
    列表根本不接"的行（NGA 1105 + 索引没收到的抖音行 151）。表现就是首页说
    「有转写还没整理稿 867 条，去素材库勾选」，人到了素材库翻不出那么多；
    「转写失败 6 条」那 6 行更是列表里一条都没有——数字和入口必须同源。
    """
    if rows is None:
        rows = _material_rows()[0]
    total = len(rows)
    with_asr = sum(1 for v in rows if (v.get("transcript_length") or 0) > 0)
    visible = {(str(v.get("sheet") or ""), str(v.get("row"))) for v in rows}
    return {
        "rows": rows, "total": total, "with_asr": with_asr,
        "missing_asr": max(0, total - with_asr),
        "asr_rate": round(with_asr * 100 / total, 1) if total else 0,
        "off_library": _not_in_library(visible),
    }


def _author_topics(stats=None):
    """返回 (要列出的来源→条数, 未订阅那堆, 已入库但没接进列表的那堆)。"""
    stats = stats or _material_stats()
    rows = stats["rows"]
    subs = _subscribed_authors()
    topics, off_count = {}, {}
    for v in rows:
        name = (v.get("author") or "").strip()
        sheet = (v.get("sheet") or "").strip()
        label = name or "（没抓到作者）"
        if name in subs or sheet in FIRST_PARTY_SHEETS:
            topics[label] = topics.get(label, 0) + 1
        else:
            off_count[label] = off_count.get(label, 0) + 1
    off = {"authors": len(off_count), "videos": sum(off_count.values())}
    return dict(sorted(topics.items(), key=lambda kv: (-kv[1], kv[0]))), off, stats["off_library"]


@app.route("/api/stats")
def api_stats():
    topics, off, not_in_library = _author_topics()
    total = sum(topics.values()) + off["videos"]
    # 以前这里为了拿一个 version 把 2.6MB 索引整份解析一遍；走缓存
    version = _index_raw().get("version")
    return jsonify({"topics": topics, "total": total,
                    "off_subscription": off, "not_in_library": not_in_library,
                    "version": version})


# ── API: 工作台仪表盘 ──
# ── 首页待办板：一次算完"现在有什么活儿等着"，每项都带跳转 ──
def _packages_pending():
    """扫三渠道成品清单，挑出需要人眼复核的那几张，顺带报各渠道有几组。

    判据跟出图那一步用的是同一套：海报看图面文字回读与视觉评审分数线，
    头条/公众号看 model 是不是 local-template（LLM 没成时静默换的本地兜底稿，
    这种稿会把 ASR 乱码印到读者看得见的卡片上）。
    """
    pending = []
    counts = {}
    for ch, sub in (("海报", "poster"), ("头条图文", "toutiao"), ("公众号图文", "wechat")):
        d = OUTPUT_DIR / sub
        n = 0
        if d.exists():
            for mf in d.glob("*/manifest.json"):
                if mf.parent.name.startswith("_"):
                    continue
                n += 1
                try:
                    m = json.loads(mf.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    continue
                v = (m.get("verify") or {}).get("ok")
                rv = m.get("review") or {}
                why = []
                if v is False:
                    why.append("图面文字与文案不一致")
                if m.get("model") == "local-template" or rv.get("model") == "local-template":
                    why.append("文案是本地兜底稿（AI 没成）")
                if rv.get("score") is not None and int(rv.get("full") or 0) > 0 \
                        and int(rv.get("score")) < ai_poster.REVIEW_PASS:
                    why.append("视觉评审 %s/%s 偏低" % (rv.get("score"), rv.get("full")))
                # 多页那组：每页各自有回读与评审结果，第 2 页分数低也得单独捞出来
                prows = m.get("pages") or []
                if len(prows) > 1:
                    for p in prows:
                        pv = (p.get("verify") or {})
                        pr = p.get("review") or {}
                        tag = "第 %s 页 " % (p.get("page") or "?")
                        if pv.get("ok") is False:
                            why.append(tag + "图面文字与文案不一致")
                        if pr.get("score") is not None and int(pr.get("full") or 0) > 0 \
                                and int(pr.get("score")) < ai_poster.REVIEW_PASS:
                            why.append(tag + "视觉评审 %s/%s 偏低" % (pr.get("score"), pr.get("full")))
                        tp = (p.get("typeset") or {}).get("problems") or []
                        if len(tp) > len((m.get("typeset") or {}).get("problems") or []):
                            why.append(tag + "版面有 %d 条提示" % len(tp))
                if why:
                    pending.append({"channel": ch, "id": m.get("id"),
                                    "title": m.get("title") or "", "why": "；".join(why)})
        counts[sub] = n
    pending.sort(key=lambda x: x["channel"])
    return pending, counts


@app.route("/api/todo")
def api_todo():
    """首页待办板。做成接口而不是在前端拼：判据要读 SQLite 与成品清单，浏览器拿不到。"""
    if not DB_PATH.exists():
        return jsonify({"items": [], "stats": {}, "error": "数据库还没生成，先去采集中心跑一条"})
    now = datetime.now()
    # 素材侧的判定走列表可见集（_material_stats），跟首页那行数字同一个口径：
    # 以前 "有转写还没整理稿 867 条 → 去素材库勾选"，人到了素材库只翻出 768 条，
    # 多出来的那批是库里躺着、列表根本不接的行（NGA 帖子 + 索引没收的抖音行），点进去对不上。
    stats = _material_stats()
    vids = [{"id": v.get("db_id"), "status": v.get("status") or "",
             "tlen": v.get("transcript_length") or 0} for v in stats["rows"]]
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        covered = set()
        for r in conn.execute("SELECT video_id, source_video_ids FROM ai_summaries"):
            if r["video_id"]:
                covered.add(r["video_id"])
            try:
                for v in json.loads(r["source_video_ids"] or "[]"):
                    covered.add(int(v))
            except (ValueError, TypeError):
                continue
        drafts = conn.execute(
            "SELECT COUNT(*) AS c FROM ai_summaries WHERE status='draft' "
            "OR content LIKE '%[AI生成失败%' OR content LIKE '%规则版草稿%'").fetchone()["c"]
        n_sum = conn.execute("SELECT COUNT(*) AS c FROM ai_summaries").fetchone()["c"]
        last_out = conn.execute(
            "SELECT MAX(updated_at) AS t FROM ai_summaries").fetchone()["t"] or ""
        n_subs = conn.execute("SELECT COUNT(*) AS c FROM subscriptions").fetchone()["c"]
    finally:
        conn.close()

    need = [v for v in vids if (v["tlen"] or 0) >= 300 and v["id"] not in covered]
    failed = [v for v in vids if (v["status"] or "") == "失败"]
    pending_pkgs, pkg_counts = _packages_pending()
    cfg = sync_tasks.load()
    subs = content_store.list_subscriptions(DB_PATH) if DB_PATH.exists() else []
    alive = {s["id"] for s in subs}
    due_now = sync_tasks.due_tasks(cfg, now, alive)   # 到点的任务（一组订阅一个任务）
    nxt = None
    for t in cfg.get("tasks") or []:
        d = sync_tasks.next_due(t, cfg.get("run_at"), now)
        if d and (nxt is None or d < nxt):
            nxt = d
    days_since = None
    try:
        days_since = (now - datetime.strptime(last_out, "%Y-%m-%d %H:%M:%S")).days
    except (TypeError, ValueError):
        pass

    items = []
    if drafts:
        items.append({"key": "draft", "tone": "hot", "n": drafts,
                      "label": "整理稿是规则版骨架", "page": "summaries", "btn": "去重跑整理稿",
                      "note": "AI 当时没成，正文是照抄转写的骨架稿——从这里出图会把错字印到读者眼前"})
    if need:
        items.append({"key": "need_summary", "tone": "todo", "n": len(need),
                      "label": "有转写还没整理稿", "page": "videos", "btn": "去素材库勾选",
                      "note": "转写 ≥300 字、还没生成过整理稿的素材；图文与海报都从这一步出"})
    if pending_pkgs:
        first = pending_pkgs[0]
        items.append({"key": "pkg_review", "tone": "hot", "n": len(pending_pkgs),
                      "label": "成品等着人眼复核", "page": "poster", "btn": "去看成品",
                      "note": "%s：%s" % (first["channel"], first["why"])})
    if failed:
        items.append({"key": "asr_fail", "tone": "todo", "n": len(failed),
                      "label": "转写失败", "page": "videos", "btn": "去素材库",
                      "note": "状态标了失败的素材，多为链接失效或下载中断"})
    if cfg.get("enabled"):
        items.append({"key": "autosync", "tone": "ok", "n": len(cfg.get("tasks") or []),
                      "label": "自动同步开着", "page": "materials", "btn": "去配置",
                      "note": "下次 %s 跑%s" % (nxt.strftime("%m-%d %H:%M") if nxt else "（无到期任务）",
                                                "，现在有 %d 个该跑了" % len(due_now) if due_now else "")})
    else:
        items.append({"key": "autosync_off", "tone": "info", "n": 0,
                      "label": "自动同步没开", "page": "materials", "btn": "去开",
                      "note": "不开就得每次人过去点；微信那条很轻，适合交给它"})
    if days_since is None:
        items.append({"key": "no_output", "tone": "info", "n": 0, "label": "还没有整理稿",
                      "page": "videos", "btn": "去生成", "note": "整条链路的第一篇稿子"})
    elif days_since >= 3:
        items.append({"key": "stale_output", "tone": "todo", "n": days_since,
                      "label": "已 %d 天没出新整理稿" % days_since, "page": "summaries",
                      "btn": "去看整理稿", "note": "产线断档比质量差更难发现，先确认是没料还是没做"})

    return jsonify({
        "items": items,
        "stats": {"total": stats["total"], "with_asr": stats["with_asr"],
                  "off_library": stats["off_library"],
                  "summaries": n_sum, "subs": n_subs,
                  "last_output": last_out, "days_since": days_since},
        "packages": pkg_counts,
        "autosync": {"enabled": bool(cfg.get("enabled")), "run_at": cfg.get("run_at"),
                     "tasks": len(cfg.get("tasks") or []),
                     "next_due": nxt.strftime("%Y-%m-%d %H:%M") if nxt else "",
                     "due_now": len(due_now), "last_batch": cfg.get("last_batch")},
        "sync": {"running": bool(_sub_status.get("running")),
                 "progress": _sub_status.get("progress") or ""},
    })


@app.route("/api/dashboard")
def api_dashboard():
    """聚合内容库统计，供首页仪表盘使用。

    素材侧的数（总数/已转写/最近入库/主要作者）一律走 `_material_stats()`——
    跟素材库列表同一批行；整理稿侧仍按 `ai_summaries` 算（那张表就是整理稿的账本）。
    """
    try:
        if not DB_PATH.exists():
            _sync_content_db()
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            summary_rows = conn.execute(
                "SELECT summary_type, COUNT(*) AS c FROM ai_summaries GROUP BY summary_type"
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

        stats = _material_stats()
        total, with_asr = stats["total"], stats["with_asr"]
        # 主要作者取「全部作者」那份同源名单（整表 group by 会把论坛发帖人算成"作者"）
        authors = _author_topics(stats)[0]
        # 最近入库跟列表一样按发布时间排：以前按 updated_at 排，下次导 NGA 快照就会把
        # "最近素材"整片换成论坛帖子，而那些帖子在素材库里根本看不到、点进去也找不到
        recent_videos = sorted(stats["rows"],
                               key=lambda v: (v.get("published_at") or v.get("create_time") or ""),
                               reverse=True)[:6]
        summaries = {row["summary_type"]: row["c"] for row in summary_rows}
        return jsonify({
            "total": total,
            "with_asr": with_asr,
            "missing_asr": stats["missing_asr"],
            "asr_rate": round(with_asr * 100 / total, 1) if total else 0,
            "off_library": stats["off_library"],
            "authors": [{"name": k, "count": n} for k, n in list(authors.items())[:8]],
            "summaries": {
                "game_guide": summaries.get("game_guide", 0),
                "ai_interview": summaries.get("ai_interview", 0),
                "wechat_material": summaries.get("wechat_material", 0),
            },
            "question_count": question_count,
            "recent_videos": [{"title": v.get("title"), "author": v.get("author"),
                               "status": v.get("status"),
                               "updated_at": v.get("published_at") or v.get("create_time") or ""}
                              for v in recent_videos],
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
    asr_model = data.get("asr_model", "small")   # 10-12 从 base 提到 small，理由见 main.py --asr-model 注释

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
        # 收尾必须返回：原来只有 total==0 的几条分支写了 return，翻到视频的正常路径
        # 一路落到函数结尾返回 None，Flask 直接 500
        # 「The view function for 'api_preview_user_videos' did not return a valid response」——
        # 表现就是「预览视频列表」按钮一点就报错，而且只在有结果时报错。
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
    asr_model = data.get("asr_model", "small")   # 10-12 从 base 提到 small，理由见 main.py --asr-model 注释
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
        # 每页最多 50 篇：整理稿列表原先一次全量返回（limit 默认 200），
        # 条数上来之后首屏渲染与搜索框防抖都跟着变肉。面试题那一页要全量，
        # 它自己显式传 page_size=500，不跟着这个默认值走。
        try:
            page = max(1, int(request.args.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            size = min(500, max(1, int(request.args.get("page_size") or 50)))
        except (TypeError, ValueError):
            size = 50
        total = content_store.count_summaries(DB_PATH, summary_type=summary_type, query=q)
        items = content_store.list_summaries(DB_PATH, summary_type=summary_type, query=q,
                                            limit=size, offset=(page - 1) * size)
        # brief=1（整理稿列表页传）：卡片只用正文前 220 字当摘要，正文与结构化数据整段返回
        # 时这两样占了 46 篇里的 433KB。点开来是走 /api/content/summaries/<id> 取全量的，
        # 所以列表这一刀不影响详情；面试题页要整段 structured_data，它不传 brief。
        if request.args.get("brief", "") in ("1", "true"):
            for it in items:
                c = it.get("content") or ""
                if len(c) > 300:
                    it["content"] = c[:300]
                it["structured_data"] = ""
        return jsonify({"items": items, "total": total,
                        "page": page, "page_size": size,
                        "pages": max(1, (total + size - 1) // size)})
    except Exception as e:
        return jsonify({"error": str(e), "items": [], "total": 0}), 500


@app.route("/api/content/summaries/<int:summary_id>")
def api_content_summary_detail(summary_id):
    try:
        item = content_store.get_summary(DB_PATH, summary_id)
        if not item:
            return jsonify({"error": "总结不存在"}), 404
        # 带一句"这篇挂在哪一格"：公众号的生成入口收在排产格那条竖线上，
        # 但没挂格的稿（从素材库那条路生成的 9 篇）不能因此没入口——界面按这个字段分情况给。
        try:
            t = plan_tasks.task_of_summary(DB_PATH, summary_id)
            item["plan_slot"] = {"slot_key": t["slot_key"], "channel": t["channel"],
                                 "form": t["form"], "version": t["version"]} if t else None
        except Exception:
            app.logger.warning("查这篇稿挂哪一格失败（界面会按没挂格处理，入口保留）", exc_info=True)
            item["plan_slot"] = None
        return jsonify(item)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/content/summaries/<int:summary_id>/material-check", methods=["POST"])
def api_content_material_check(summary_id):
    """整理稿体检：只查结构是否塌了、【关键数据】与【金句摘录】能否回指素材。

    为什么不复用成稿的 /api/review/*/audit：素材档案是中间产物、写成清单体，
    禁用句式与字数区间对它没意义；它真正的风险是 LLM 那条路把数字和金句编出来。
    """
    try:
        item = content_store.get_summary(DB_PATH, summary_id)
        if not item:
            return jsonify({"error": "整理稿不存在"}), 404
        sources = content_store.material_source_text(DB_PATH, item)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    result = draft_audit.audit_material(item.get("content") or "", sources,
                                          summary_type=item.get("summary_type") or "")
    return jsonify({"summary_id": summary_id, "summary_type": item.get("summary_type"),
                    "has_source": bool(sources), "verdict": result["verdict"],
                    "label": result["label"], "fails": result["fails"],
                    "warns": result["warns"], "rows": result["rows"],
                    "report": result["report"]})


@app.route("/api/content/summaries/<int:summary_id>/regenerate", methods=["POST"])
def api_content_regenerate(summary_id):
    """重新生成指定整理稿。"""
    try:
        summary = content_store.get_summary(DB_PATH, summary_id)
        if not summary:
            return jsonify({"error": "整理稿不存在"}), 404

        summary_type = request.args.get("type") or summary.get("summary_type", "game_guide")
        # 整理稿走快速档：这一步是"把口播压缩组织成结构化稿子"，不需要思考型模型。
        # 实测 51 篇成功整理稿全是 deepseek-v4-flash 跑的（现为 v4.1-flash，该数字未重测）；
        # 切到 qwen3.8-max 后生成的
        # 2 篇 100% 撞 _generate_with_llm 的超时、静默降级成规则版（＝照抄转写原文），
        # 下游海报拆文案拿到的就是没整理过的生料。
        config = _fast_config()
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


PACK_DIR_NAME = "选题包"


@app.route("/api/topic/pack", methods=["POST"])
def api_topic_pack():
    """选题包：把多条素材打成一份「给 agent 消费的选题材料」，含口径分歧候选。

    为什么不复用 /api/content/generate：那条是「一条素材 → 一篇整合稿」的压缩链路，
    一次只喂 1-2 条，看不见「同一件事被几个 up 主同时讲、说法互相矛盾」——而那才是
    抄不到的独家增量。这里只做捞全 + 对齐，生成动作在 Qoder 会话里按 skill 走。
    取材两种入口：前端勾选的素材；或给关键词按主题自动捞（把 96% 没用过的素材捞出来）。
    """
    data = request.get_json(force=True)
    theme = (data.get("theme") or "").strip()
    keyword = (data.get("keyword") or "").strip()
    game = (data.get("game") or "").strip()
    channel = (data.get("channel") or "toutiao").strip()
    if channel not in topic_pack.PACK_CHANNELS:
        return jsonify({"error": "channel 只能是 %s" % " / ".join(topic_pack.PACK_CHANNELS)}), 400
    keys = data.get("videos") or []

    videos = []
    if keys:
        for key in keys:
            try:
                sheet, row_text = str(key).split(":", 1)
                video = content_store.get_video_by_source(DB_PATH, sheet, int(row_text))
            except (ValueError, TypeError):
                video = None
            if video:
                videos.append(video)
    elif keyword:
        videos = content_store.search_videos(DB_PATH, keyword, game=game,
                                             limit=int(data.get("limit") or 80))
    else:
        return jsonify({"error": "要么勾选素材，要么给一个用来捞素材的关键词"}), 400

    if len(videos) < 2:
        return jsonify({"error": "选题包至少需要 2 条素材（现在 %d 条），交叉核对才有意义" % len(videos)}), 400

    used = content_store.used_video_ids(DB_PATH)
    markdown, stats = topic_pack.build_pack(videos, theme or keyword, used, channel)
    ch = topic_pack.PACK_CHANNELS[channel]

    safe = re.sub(r"[^\w一-鿿-]+", "", (theme or keyword))[:24] or "未命名"
    folder = OUTPUT_DIR / PACK_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    # 文件名**必须带渠道**：以前只有"当天日期+主题"，同一题同一天先出头条再出公众号，
    # 第二份 write_text 直接盖掉第一份，人只看到"生成成功"，丢了那份没人提醒。
    # 同渠道同题同一天也不许静默覆盖 → 撞名就加 -2、-3。
    stem = "%s-%s-%s" % (datetime.now().strftime("%Y-%m-%d"), channel, safe)
    path = folder / ("%s.md" % stem)
    n = 2
    while path.exists():
        path = folder / ("%s-%d.md" % (stem, n))
        n += 1
    path.write_text(markdown, encoding="utf-8")
    app.logger.info("选题包已生成 %s：%s", path, stats)
    return jsonify({
        "path": str(path),
        "name": path.name,
        "stats": stats,
        "command": "按 %s 出%s：走 content-research-writer（先大纲 → 我补 300 字 → 成稿），"
                   "规则按 %s，%s" % (path, ch['label'], ch['rules'], ch['gate']),
    })


@app.route("/api/topic/pack/reveal", methods=["POST"])
def api_topic_pack_reveal():
    """在访达里定位选题包。只认 选题包/ 目录下的文件名，防路径穿越。"""
    import subprocess

    name = os.path.basename((request.get_json(force=True).get("name") or "").strip())
    root = (OUTPUT_DIR / PACK_DIR_NAME).resolve()
    target = (root / name).resolve()
    if not str(target).startswith(str(root) + os.sep) or not target.is_file():
        return jsonify({"error": "文件不存在或不在选题包目录里"}), 404
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        elif os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(root)])
        return jsonify({"success": True, "path": str(target)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


ARTICLE_DIR_NAME = "文章"


def _review_target(kind: str, ref_id: str):
    """解析审核对象 → (正文, 对照素材, 标题, 落盘路径)。找不到就抛错。

    图文包的正文是「卡片文字 + 微头条文案」（这就是要贴出去的东西）；
    对照素材用同一条的整合稿，数字出处能不能核得上就看它。
    """
    if kind in review_store.PACK_KINDS:
        # 头条与公众号图文包共用一套 manifest 口径：正文都是「卡片文字 + 文案」，
        # 差别只在产物目录名（output/toutiao | output/wechat）
        mpath = OUTPUT_DIR / kind / str(ref_id) / "manifest.json"
        if not mpath.is_file():
            raise ValueError("图文包不存在：%s" % mpath)
        data = json.loads(mpath.read_text(encoding="utf-8"))
        prose, _copy = draft_audit.manifest_prose(data)
        sources = ""
        sid = data.get("summary_id") or ref_id
        try:
            summary = content_store.get_summary(DB_PATH, int(sid))
            sources = (summary or {}).get("content") or ""
        except (TypeError, ValueError):
            sources = ""
        label = "头条" if kind == "toutiao" else "公众号"
        return prose, sources, (data.get("title") or "%s图文 #%s" % (label, ref_id)), str(mpath)
    if kind == "article":
        path = OUTPUT_DIR / ARTICLE_DIR_NAME / os.path.basename(str(ref_id))
        if not path.is_file():
            raise ValueError("文章稿不存在：%s" % path)
        text = path.read_text(encoding="utf-8")
        title = next((ln.strip()[2:] for ln in text.splitlines() if ln.startswith("# ")), path.stem)
        return text, "", title, str(path)
    raise ValueError("kind 只能是 %s" % " / ".join(review_store.KINDS))


# ── 整理稿「去 AI 味」两步接口（预览 → 勾选 → 应用）──────────────────
# 实现抽在 src/humanize_routes.py：app.py 常被多个会话同时改，上一轮我的端点与别人的改动
# 相邻、git 合成同一个 hunk 拆不开。撞面从 140 行降到这 2 行。
app.register_blueprint(humanize_routes.make_blueprint(
    DB_PATH, OUTPUT_DIR, _ai_config, _db_lock, app.logger))


@app.route("/api/review/items")
def api_review_items():
    """一次给全审核台：头条图文包 + 公众号图文包 + 文章稿，带各自状态（列表页拿这个合并徽章）。"""
    items = []
    for kind in review_store.PACK_KINDS:      # 头条图文包 + 公众号图文包，同一套 manifest 口径
        root = OUTPUT_DIR / kind
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
            if not (d / "manifest.json").is_file() or not d.name.isdigit():
                continue
            try:
                content, _, title, _ = _review_target(kind, d.name)
            except Exception as e:
                app.logger.info("跳过图文包 %s/%s：%s", kind, d.name, e)
                continue
            items.append({"kind": kind, "kind_label": review_store.KINDS.get(kind, kind),
                          "ref_id": d.name, "title": title,
                          "state": review_store.state(DB_PATH, kind, d.name, content)})
    art_root = OUTPUT_DIR / ARTICLE_DIR_NAME
    if art_root.is_dir():
        for f in sorted(art_root.glob("*.md"), reverse=True):
            content, _, title, _ = _review_target("article", f.name)
            items.append({"kind": "article", "kind_label": review_store.KINDS["article"],
                          "ref_id": f.name, "title": title,
                          "state": review_store.state(DB_PATH, "article", f.name, content)})
    return jsonify({"items": items, "counts": review_store.counts(DB_PATH),
                    # 待处理 = 所有产物里还没过审的（含从没体检过的），侧栏徽章用这个数；
                    # 只数 content_reviews 里的记录会永远是 0，因为没体检的压根没记录
                    "pending": sum(1 for it in items if it["state"]["needs_audit"]),
                    "total": len(items)})


@app.route("/api/review/<kind>/<path:ref_id>")
def api_review_get(kind, ref_id):
    try:
        content, _src, title, path = _review_target(kind, ref_id)
    except (ValueError, json.JSONDecodeError) as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"kind": kind, "ref_id": ref_id, "title": title, "path": path,
                    "state": review_store.state(DB_PATH, kind, ref_id, content)})


@app.route("/api/review/<kind>/<path:ref_id>/audit", methods=["POST"])
def api_review_audit(kind, ref_id):
    """跑体检并落状态。图文包（头条/公众号）走卡稿口径，文章稿走长文稿口径。"""
    try:
        content, sources, _title, path = _review_target(kind, ref_id)
    except (ValueError, json.JSONDecodeError) as e:
        return jsonify({"error": str(e)}), 404
    try:
        if kind in review_store.PACK_KINDS:
            data = json.loads(open(path, encoding="utf-8").read())
            result = draft_audit.audit(manifest=data, sources_text=sources or None)
        else:
            result = draft_audit.audit(md_text=content, sources_text=sources or None,
                                       draft_path=path, use_verify=False)
    except Exception as e:
        return jsonify({"error": "体检跑挂了：%s" % e}), 500
    st = review_store.save_audit(DB_PATH, kind, ref_id, content, result)
    app.logger.info("体检 %s/%s → %s", kind, ref_id, st["status"])
    return jsonify({"state": st})


@app.route("/api/review/<kind>/<path:ref_id>/approve", methods=["POST"])
def api_review_approve(kind, ref_id):
    """人工放行。有 FAIL 时必须写理由（理由落库，不写就 400）。"""
    try:
        content, _s, _t, _p = _review_target(kind, ref_id)
        reason = (request.get_json(silent=True) or {}).get("reason", "")
        st = review_store.approve(DB_PATH, kind, ref_id, content, reason)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"state": st})


@app.route("/api/review/<kind>/<path:ref_id>/publish", methods=["POST"])
def api_review_publish(kind, ref_id):
    """标记已发布。审核没过直接 400——发布本身仍在平台手工完成，这里不碰任何发布通道。"""
    try:
        content, _s, _t, _p = _review_target(kind, ref_id)
        st = review_store.mark_published(DB_PATH, kind, ref_id, content)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"state": st})


# ── 运营数据回流（每周只看两个数：涨粉、钱）───────────────────────────
# 为什么手填：个人主体号没有官方数据接口，内置浏览器打开 mp.weixin.qq.com 是扫码页
# （没有登录态，2026-10-08 实测），同类看板接口 wasm 加密不能 replay——所以这里
# 不写没实测过的爬虫，先做一分钟能填完的录入 + 能自动补的条目。

def _ops_columns():
    """表头由后端下发（口径只有一份）：每列给出键名、三个渠道叫什么、哪些渠道根本没这个指标。
    同一张表混排三个渠道，所以标签取各渠道名字的并集（"点赞/在看"），没有的格子界面画「—」。"""
    out = []
    for key in ("show", "reads", "looks", "comment", "saves", "shares", "net_follow", "revenue"):
        labels = ops_store.COLUMN_LABELS.get(key) or {}
        names = []
        for ch in ("wechat", "toutiao", "xiaohongshu"):
            v = labels.get(ch) or ""
            if v and v != "—" and v not in names:
                names.append(v)
        out.append({"key": key, "label": "/".join(names) or key,
                    "na": [c for c, v in labels.items() if v == "—"]})
    return out


@app.route("/api/ops/report")
def api_ops_report():
    try:
        week = (request.args.get("week") or "").strip()
        return jsonify({"report": ops_store.report(DB_PATH, week or None),
                        "articles": ops_store.list_articles(DB_PATH),
                        "columns": _ops_columns(),
                        "channels": [{"key": k, "label": v}
                                     for k, v in ops_store.CHANNELS.items()],
                        "weeks": ops_store.list_weeks(DB_PATH, limit=12)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ops/article", methods=["POST"])
def api_ops_article():
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "请求体不是合法 JSON，没敢写库"}), 400
    channel = (data.get("channel") or "wechat").strip()
    ref_id = str(data.get("ref_id") or "").strip()
    if channel not in ops_store.CHANNELS:
        return jsonify({"error": "channel 只能是 %s" % " / ".join(ops_store.CHANNELS)}), 400
    if not ref_id:
        return jsonify({"error": "ref_id 不能空"}), 400
    try:
        with _db_lock:
            row = ops_store.upsert_article(DB_PATH, channel, ref_id, data.get("fields") or {})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"row": row})


@app.route("/api/ops/register", methods=["POST"])
def api_ops_register():
    """发布登记：记一条"这篇已经在平台发出去了"。

    2026-10-18 从 5180 搬过来（那边服务已停、数据冻在 09-24）。口径没变过一条：
    **这里只记账，不发车**——发布那一下永远是他在平台后台点的那一下。
    校验都交给 ops_store.register_publish，界面不抄一份判据（抄了迟早跟后端对不上）。
    """
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "请求体不是合法 JSON，没敢写库"}), 400
    try:
        with _db_lock:
            out = ops_store.register_publish(DB_PATH, data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(out)


@app.route("/api/ops/week", methods=["POST"])
def api_ops_week():
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "请求体不是合法 JSON，没敢写库"}), 400
    week = (data.get("week") or "").strip()
    if not week:
        return jsonify({"error": "week 不能空"}), 400
    try:
        with _db_lock:
            row = ops_store.upsert_week(DB_PATH, week, data.get("fields") or {})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"row": row})


@app.route("/api/ops/import-ledger", methods=["POST"])
def api_ops_import_ledger():
    """把旧台账（media-workbench/runtime/publications.json）整本接进来 + 按审核台「已发布」补空行。
    2026-10-18 起是全渠道（原来只导微信 4 条，那边 96 条头条记录没人接）。只补空，不覆盖手填的数。"""
    try:
        with _db_lock:
            out = ops_store.import_ledger(DB_PATH)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(out)


@app.route("/api/bank/overview")
def api_bank_overview():
    """答案库与交付看板：题数、来源、自检结论、交付物清单、数据源分级，全部现读现算。

    2026-10-19 换轨：ledger 那节已从"外部仓库许可判定"换成"魔兽资料站数据源分级"
    （键 source/tier/verdict/checked/note，口径见 src/bank_board.py 表头注释）；
    paid / free / artifacts 三节仍是 AI 线的冻结现状，只读不改。
    """
    try:
        return jsonify(bank_board.overview())
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc().splitlines()[-3:]}), 500


@app.route("/api/bank/file")
def api_bank_file():
    """只开放看板列出来的那几个文件，不把整个 product/ 暴露成可猜的下载口。"""
    name = os.path.basename(request.args.get("name") or "")
    if name not in bank_board.artifact_names():
        return jsonify({"error": "这个文件不在看板清单里，不给打开"}), 404
    return send_from_directory(bank_board.PRODUCT, name)


def _run_generation(video_keys, summary_type, combine, st, brief=None, video_ids=None):
    """生成那一段的真身：把 `sheet:row` 列表整理成整理稿并落库，进度写进 `st` 这个字典。

    为什么抽出来：排产任务那条链（`/api/plan/task/generate`）要跑**同一套**提示词与落库，
    复制一份就等于埋下"素材库那条改了这个入口没改"的双份口径。
    `st` 由调用方给：老路由传全局 `_task_status`（素材库批量那套轮询不动它），
    任务路由传 `_plan_jobs[task_id]`（一题一渠道一份进度，双渠道同时跑互不覆盖）。
    `brief` 是排产那一格的选题要求（标题/hook/补充/排期/渠道/形式），会进真实提示词
    （content_store._brief_block）；**旧素材库那条路不传，行为与抽取前完全一致**。
    `video_ids` 是排产任务**已确认并存在库里的那几条素材主键**（T1）：给了就按 id 取素材，
    不再经 `sheet:row` 绕回——绕回去可能在同步 Excel 之后取到另一条记录，证据就漂移了。
    缺哪条、哪条没正文一律写进 `st["error"]` 明说，不许无声丢掉之后照跑。
    旧素材库那条仍传 `video_keys`（`video_ids=None`），行为一字不变。
    返回 summary_ids；每条素材为什么被跳过写在 `st["error"]` 里（原样保留老文案）。
    """
    try:
        st["progress"] = "同步 Excel 到内容库..."
        _sync_content_db()
        config = _fast_config()   # 同上：整理稿走快速档，理由见 /regenerate 那段注释
        success_count = 0
        summary_ids = []
        selected_videos = []

        if video_ids is not None:
            # T1（排产任务走这条）：按**库里那一版存着的素材主键**取文。
            # 不再经 sheet:row 绕回——上面刚同步过 Excel，绕回去可能取到另一条记录，
            # 任务记的素材与稿子用的素材就会又对不上。缺哪条、哪条没正文都写进 error 明说。
            st["progress"] = "按本篇确认的素材取文..."
            want_ids = [int(x) for x in video_ids]
            found = {int(v["id"]): v for v in content_store.get_videos_by_ids(DB_PATH, want_ids)}
            for vid in want_ids:
                video = found.get(vid)
                if not video:
                    st["error"] += "素材 %d: 内容库里已经没有这条（生成前被删了）\n" % vid
                    continue
                if not (video.get("transcript") or "").strip():
                    st["error"] += "素材 %d: 没有 ASR 文本，跳过\n" % vid
                    continue
                selected_videos.append(video)
            # 真掉了几条就按剩下的重算合并档：三条变一条还按"合并稿"走会产出一篇假合集
            combine = len(selected_videos) > 1
            if not selected_videos:
                st["progress"] = "没有可生成的 ASR 文本"
                st["done"] = True
                return []
        else:
            for i, key in enumerate(video_keys or []):
                try:
                    sheet, row_text = key.split(":", 1)
                    row = int(row_text)
                    st["progress"] = f"读取 [{i+1}/{len(video_keys)}] {sheet} 第{row}行..."
                    video = content_store.get_video_by_source(DB_PATH, sheet, row)
                    if not video:
                        st["error"] += f"{key}: 内容库中找不到视频\n"
                        continue
                    if not (video.get("transcript") or "").strip():
                        st["error"] += f"{key}: 没有 ASR 文本，跳过\n"
                        continue
                    selected_videos.append(video)
                except Exception as e:
                    st["error"] += f"{key}: {e}\n"

        if combine:
            if not selected_videos:
                st["progress"] = "没有可生成的 ASR 文本"
                st["done"] = True
                return []

            st["progress"] = f"合并整理 {len(selected_videos)} 条视频..."
            result, model, status = content_store.generate_collection_summary(
                selected_videos, summary_type, config, brief
            )
            _risk_inherit(result, selected_videos)      # 台账兜底：模型漏写的待核不能消失
            if summary_type in GUIDE_SUMMARY_TYPES:
                # 攻略类：抽关键帧做配图，嵌入整理稿尾部
                st["progress"] = "抽取视频关键帧配图..."
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
            _risk_record(result, selected_videos, summary_id)
            summary_ids.append(summary_id)
            success_count = 1
        else:
            for i, video in enumerate(selected_videos):
                st["progress"] = f"整理 [{i+1}/{len(selected_videos)}] 第{video['source_row']}行..."

                result, model, status = content_store.generate_summary(
                    video, summary_type, config, brief
                )
                _risk_inherit(result, [video])          # 同上：单条素材也要继承
                if summary_type in GUIDE_SUMMARY_TYPES:
                    # 攻略类：抽关键帧做配图，嵌入整理稿尾部
                    st["progress"] = f"抽取关键帧配图 [{i+1}/{len(selected_videos)}] 第{video['source_row']}行..."
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
                _risk_record(result, [video], summary_id)
                summary_ids.append(summary_id)
                success_count += 1

        st["success"] = success_count
        st["summary_ids"] = summary_ids
        label = "合并整理稿" if combine else "整理稿"
        st["progress"] = f"✅ 已生成 {success_count} 条{label}"
        st["done"] = True
        return summary_ids
    except Exception:
        st["error"] = traceback.format_exc()
        st["done"] = True
        return []


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
        _run_generation(videos, summary_type, combine, _task_status)
        _task_status["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started", "total": len(videos), "summary_type": summary_type, "combine": combine})


# ══ 排产任务 → 生成链路（2026-10-20 排产入口第一单）═══════════════════════
# 为什么另开一套端点、不给 /api/content/generate 加几个可选字段：
# 那条是"素材库勾一批 → 出几篇稿"的批量口，**没有"题"的身份**，点两次就 INSERT 两行孤立稿
# （实测 video_id=125681 的 game_guide 已经三行）。排产这一侧要的是
# "这一题·这一渠道·这一版 = 哪篇稿"，幂等判据完全不同，混在一个端点里两边都会迁就对方。
_plan_jobs = {}                    # task_id -> 进度字典（一份任务一格，双渠道同时跑互不覆盖）
_plan_jobs_lock = threading.Lock()


def _plan_job(task_id):
    with _plan_jobs_lock:
        job = _plan_jobs.get(task_id)
        if not job:
            job = {"running": False, "progress": "", "done": False, "error": "",
                   "success": 0, "summary_ids": []}
            _plan_jobs[task_id] = job
        return job


def _short_err(text):
    """把 traceback 压成人读得懂的那一行。

    任务行里的 error 是**给号主看的**（面板直接摆出来），整段 traceback 存进去，
    前 500 字全是文件路径，真正那句"模型超时/额度没了"被截掉了——等于没写原因。
    完整 traceback 仍在内存那份 job 里，排查的人看得到。
    """
    text = str(text or "").strip()
    if not text:
        return ""
    lines = [l for l in text.split("\n") if l.strip()]
    return (lines[-1] if lines else text)[:300]


def _plan_job_reset(job, progress):
    job.update({"running": True, "progress": progress, "done": False, "error": "",
                "success": 0, "summary_ids": []})


def _plan_materials(video_ids):
    """按 **DB 主键**取素材，逐条给"能不能生成"的结论。

    为什么按 id 不按 sheet:row：排产表钉选与实时查回来的就是库里的 id，
    行号会随 Excel 变动，而任务要能五年后还指得回同一条素材。
    每个问题必须带 action——光写"没有正文"等于没提示，人要的是下一步点哪里。
    """
    want = []
    for x in (video_ids or []):
        try:
            vid = int(x)
        except (TypeError, ValueError):
            continue
        if vid and vid not in want:
            want.append(vid)
    if not want:
        return [], [{"code": "no_ids", "msg": "没带任何素材 id",
                     "action": "reselect", "action_label": "回排产表确认采用素材"}]
    try:
        found = {int(v["id"]): v for v in content_store.get_videos_by_ids(DB_PATH, want)}
    except Exception as e:
        # 查库失败**不等于"这些素材不存在"**：当成错误抛出去，让界面报"查不到，重试"，
        # 否则一次数据库抖动就会让人以为"还没有稿"，然后重跑一次收费生成。
        raise RuntimeError("素材查询失败：%s" % e)
    ok, problems = [], []
    for vid in want:
        v = found.get(vid)
        if not v:
            problems.append({"code": "missing", "video_id": vid,
                             "msg": "素材库里已经没有这条（被删了，或 id 写错）",
                             "action": "reselect", "action_label": "回排产表重选素材"})
            continue
        if not (v.get("transcript") or "").strip():
            problems.append({"code": "no_transcript", "video_id": vid,
                             "title": v.get("title") or "",
                             "sheet": v.get("source_sheet"), "row": v.get("source_row"),
                             "msg": "这条没有转写正文，整理稿无米下锅",
                             "action": "transcribe", "action_label": "去素材库开这条补转写"})
            continue
        ok.append(v)
    return ok, problems


def _plan_meta(data):
    return {"title": str(data.get("title") or "")[:120],
            "hook": str(data.get("hook") or "")[:200],
            "note": str(data.get("note") or "")[:500],
            "date": str(data.get("plan_date") or data.get("date") or "")[:20]}


@app.route("/api/plan/brief")
def api_plan_brief():
    """排产日报（只读）：今天该发哪篇、每格现在有什么料。

    判据只有 `src/plan_brief.py` 这一份——命令行日报、排产表的颜色、这个接口三处共用。
    页面要是自己再算一遍"发过没有 / 素材新不新"，过两周就会和日报互相说反话
    （同一件事两份账，本项目反复栽过：frames.json 写 usable=17、界面显示 0 张）。
    """
    from src import plan_brief
    day = str(request.args.get("date") or "").strip()[:10]
    today = None
    if day:
        try:
            today = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "日期要写成 2026-10-21 这样"}), 400
    try:
        return jsonify(plan_brief.build(str(DB_PATH), today))
    except Exception as e:
        # 排产页的 DATA 被改坏时也要说人话，别让页面只剩一片灰
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/plan/provenance", methods=["GET", "POST"])
def api_plan_provenance():
    """编辑证据链的读写（原创增量那一层）。

    GET  按 `slot_key/channel/form/version` 或 `summary_id` 读一份（预览与表单回填用）；
    POST 存一份（允许空着存草稿——缺项由 `guide_provenance` 算，不在这一步拦人）。
    这里**不判原创**：只记"解决什么问题 / 新增了什么 / 依据在哪 / 借来的东西凭什么用"。
    """
    from src import guide_provenance
    if request.method == "GET":
        sid = str(request.args.get("summary_id") or "").strip()
        slot = str(request.args.get("slot_key") or "").strip()
        try:
            if sid:
                row = guide_provenance.load_for_summary(DB_PATH, int(sid))
            elif slot:
                row = guide_provenance.load(
                    DB_PATH, slot, str(request.args.get("channel") or "toutiao"),
                    str(request.args.get("form") or "guide"),
                    int(request.args.get("version") or 1))
            else:
                return jsonify({"error": "要带 summary_id 或 slot_key"}), 400
        except (TypeError, ValueError) as e:
            return jsonify({"error": str(e)[:120]}), 400
        key, label = guide_provenance.state(row)
        return jsonify({"row": row, "recorded": bool(row), "state": key, "label": label,
                        "gaps": guide_provenance.compute_gaps(row),
                        "blocking": guide_provenance.blocking_gaps(row),
                        "line": guide_provenance.summary_line(row)})
    data = request.get_json(force=True, silent=True) or {}
    if not data:
        return jsonify({"error": "请求体不是合法 JSON，没敢写库"}), 400
    try:
        with _db_lock:
            row = guide_provenance.save(DB_PATH, data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    key, label = guide_provenance.state(row)
    return jsonify({"row": row, "state": key, "label": label,
                    "gaps": guide_provenance.compute_gaps(row),
                    "blocking": guide_provenance.blocking_gaps(row),
                    "line": guide_provenance.summary_line(row)})


def _plan_ids_from_keys(keys):
    """把素材库的 `sheet:row` 卡片键现查成 DB 主键；查不到的丢掉（调用方会报"没有可用素材"）。

    行号不是稳定身份（Excel 动一行就错位），所以这里只做"当场换算一次"，
    换算完仍然把 **DB id** 存进任务与证据快照——与排产页那条入口同一份身份。
    """
    out = []
    for k in keys or []:
        s = str(k or "")
        if ":" not in s:
            continue
        sheet, row = s.split(":", 1)
        try:
            con = sqlite3.connect(str(DB_PATH))
            try:
                r = con.execute("SELECT id FROM videos WHERE source_sheet=? AND source_row=?",
                                (sheet, row)).fetchone()
            finally:
                con.close()
        except Exception:
            r = None
        if r and int(r[0]) not in out:
            out.append(int(r[0]))
    return out


# ══ 双系列排产：位置身份、候选采用、发布真相、夜间匹配（2026-10-22 那一单）══════
# 判据与状态全在 `src/plan_series.py` 一份，这里只做参数校验与事务包装。
# 三条硬规矩（任务书 §2–§4）：
#   · 匹配只写候选，采用只写 plan_tasks，发布只写台账 —— 三本账各记各的，谁也不冒充谁；
#   · 已发布的位置不再匹配、也不再采用（要写后续篇就另建槽号）；
#   · 任何一步都不自动出稿、不出图、不发布。

def _plan_slot_or_400(slot):
    slot = str(slot or "").strip()[:SLOT_KEY_MAX]
    if not slot or not plan_series.valid_slot(slot):
        return None, (jsonify({"error": "槽号只能用字母/数字/-/_且不超过 %d 位，收到「%s」"
                               % (SLOT_KEY_MAX, slot)}), 400)
    s = plan_series.get_slot(DB_PATH, slot)
    if not s:
        return None, (jsonify({"error": "查不到这个内容位置（%s）：先确认它已注册，"
                                        "或从素材库直接起稿建一个" % slot}), 404)
    return s, None


@app.route("/api/plan/series")
def api_plan_series_board():
    """两个系列的折叠大纲 + 每个位置现在走到哪一档 + 夜间匹配状态。"""
    try:
        legacy = []
        try:
            legacy = (plan_brief.load_data().get("items") or [])
        except Exception as e:
            print("[排产] 旧题名单读不到（%s），本次看板只列双系列" % str(e)[:80], flush=True)
        plan_series.seed(DB_PATH, legacy)
        return jsonify(plan_series.board(DB_PATH))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


def _plan_adopt(data):
    """采用清单落库的那一段，返回 (payload, http_status)。

    两个入口共用：排产页点「采用勾选项到本篇」，素材库选了某个位置起稿（那等于采用到那一格）。
    写的是 `plan_tasks`（沿用第一单那套版本语义）：已开工的那版绝不改，改清单就开新版；
    候选永远进不了这一格——只有人勾了的 id 会落进 video_ids。
    """
    s, err = _plan_slot_or_400(data.get("slot_key"))
    if err:
        return err
    try:
        # 渠道拼错要当场拒（`norm_channel` 现在不再静默退成头条），否则下面 guard/形式全都算错
        channel = plan_series.norm_channel(data.get("channel") or "toutiao")
    except ValueError as e:
        return (jsonify({"error": str(e)}), 400)
    # 「这一格发过没有」按 (槽号, 渠道) 判：公众号发过不算头条发过，采用清单照样能写
    guard = plan_series.slot_guard(DB_PATH, s["slot_key"], channel=channel)
    if guard["error"]:
        return (jsonify({"error": guard["error"], "slot": s["slot_key"],
                         "state": (guard["slot"] or {}).get("publish_state", {})}), 409)
    form = str(data.get("form") or s.get("form") or plan_tasks.DEFAULT_FORM).strip()[:20]
    if not plan_tasks.resolve_summary_type(channel, form):
        return (jsonify({"error": "渠道「%s」没有内容形式「%s」" % (channel, form)}), 400)
    raw_ids = data.get("video_ids")
    if raw_ids is None:
        raw_ids = _plan_ids_from_keys(data.get("keys") or [])
    if raw_ids is None:
        return (jsonify({"error": "没带 video_ids：要清空请显式传空数组"}), 400)
    try:
        selected, problems = _plan_materials(raw_ids)
    except Exception as e:
        return (jsonify({"error": str(e), "retry": True}), 502)
    if raw_ids and not selected:
        return (jsonify({"error": "没有可用素材，采用清单没存", "problems": problems}), 422)
    meta = _plan_meta(dict(data, title=str(data.get("title") or s.get("title") or "")))
    task, action = plan_tasks.save_sources(DB_PATH, s["slot_key"], channel, form, meta, selected)
    return (jsonify({"task": task, "action": action, "problems": problems,
                     "slot": s["slot_key"], "position": s.get("position") or "",
                     "summary_type": plan_tasks.resolve_summary_type(channel, form)}), 200)


@app.route("/api/plan/series/adopt", methods=["POST"])
def api_plan_series_adopt():
    """把勾选的一条或多条素材存成**本篇已采用**。一次提交多条，生成前可反复增删。"""
    out, code = _plan_adopt(request.get_json(force=True, silent=True) or {})
    return (out, code) if code != 200 else out


@app.route("/api/plan/supplements/<int:task_id>")
def api_plan_supplements(task_id):
    """这一格的「补充资料」提名：拿已采用的视频素材去找强相关的非视频料。

    只提名、不自动采用（号主 10-28 定的规矩：判据再硬也可能错，人勾了才算），
    也不自动进任何生成——要等 POST 存进这一版的 supplement_ids。
    """
    task = plan_tasks.get_task(DB_PATH, task_id)
    if not task:
        return jsonify({"error": "找不到这条排产任务"}), 404
    ids = [int(i) for i in (task.get("video_ids") or [])]
    if not ids:
        return jsonify({"candidates": [], "adopted": [], "scanned": 0, "count_all": 0,
                        "need_main": True,
                        "msg": "这一格还没采用视频素材——补充料是挂在主素材下面的，先采用素材。"})
    try:
        mains = content_store.get_videos_by_ids(DB_PATH, ids)
    except Exception as e:
        return jsonify({"error": str(e), "retry": True}), 502
    res = supplements.candidates(DB_PATH, mains)
    adopted = task.get("supplement_ids") or []
    return jsonify({"candidates": res["list"], "count_all": res["count_all"],
                    "scanned": res["scanned"],
                    "adopted": adopted,
                    # 已勾的那几条也按同一份口径渲染（分级/日期/当初凭什么勾的理由都在里面），
                    # 不在前端再抄一张 sheet→分级 的表
                    "adopted_rows": supplements.rows_by_ids(
                        DB_PATH, adopted, snaps=task.get("supplement_sources")),
                    "started": plan_tasks.is_started(task),
                    "max_items": supplements.MAX_ITEMS})


@app.route("/api/material/supplements")
def api_material_supplements():
    """给**一条视频素材**找强相关的非视频料——排产表那一侧展开子集时才查。

    判据、分级、条数全在 `src/supplements.py`，和工作台排产格那块是同一份实现，不抄第二份。
    懒查是有意的：一页 26 个选题、每个十几条候选，全算要几十秒；点开一条算一条（实测 0.2s）。
    """
    try:
        vid = int(request.args.get("video_id") or 0)
    except (TypeError, ValueError):
        vid = 0
    if not vid or not DB_PATH.exists():
        return jsonify({"error": "要带 video_id（库里那条视频素材的主键）"}), 400
    try:
        mains = content_store.get_videos_by_ids(DB_PATH, [vid])
    except Exception as e:
        return jsonify({"error": str(e), "retry": True}), 502
    if not mains:
        return jsonify({"error": "库里查不到这条素材 id=%s" % vid}), 404
    if (mains[0].get("source_sheet") or "") not in supplements.VIDEO_SHEETS:
        return jsonify({"candidates": [], "scanned": 0, "count_all": 0, "not_video": True,
                        "msg": "这条不是视频素材，下面不挂子集（它可以当别的素材的补充料）"})
    res = supplements.candidates(DB_PATH, mains, limit=8)
    return jsonify({"candidates": res["list"], "scanned": res["scanned"],
                    "count_all": res["count_all"], "max_items": supplements.MAX_ITEMS})


@app.route("/api/plan/supplements", methods=["POST"])
def api_plan_supplements_save():
    """勾定补充料。"凭什么算相关"由服务端现算并冻进快照，不信前端回传的那份。"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        task_id = int(data.get("task_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "没带 task_id"}), 400
    raw = data.get("video_ids")
    if raw is None:
        return jsonify({"error": "没带 video_ids：要清空请显式传空数组"}), 400
    task = plan_tasks.get_task(DB_PATH, task_id)
    if not task:
        return jsonify({"error": "找不到这条排产任务"}), 404
    ids = []
    for x in raw:
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if v and v not in ids:
            ids.append(v)
    if len(ids) > supplements.MAX_ITEMS:
        return jsonify({"error": "补充资料最多 %d 条——它们是配角，给多了挤掉主素材"
                        % supplements.MAX_ITEMS}), 400
    try:
        rows = content_store.get_videos_by_ids(DB_PATH, ids) if ids else []
        mains = content_store.get_videos_by_ids(DB_PATH, task.get("video_ids") or [])
    except Exception as e:
        return jsonify({"error": str(e), "retry": True}), 502
    got = {int(r["id"]) for r in rows}
    missing = [i for i in ids if i not in got]
    if missing:
        return jsonify({"error": "库里查不到这些素材 id：%s（可能刚被删掉），没保存" % missing}), 422
    bad = [r for r in rows if (r.get("source_sheet") or "") not in supplements.NON_VIDEO_SHEETS]
    if bad:
        return jsonify({"error": "这几条不是非视频素材，不能当补充料：%s"
                        % "、".join((r.get("title") or str(r["id"]))[:20] for r in bad)}), 400
    task, action = plan_tasks.save_supplements(DB_PATH, task_id, rows,
                                               supplements.why_map(mains, rows))
    if action == "no-main":
        return jsonify({"error": "这一格还没有采用视频素材，先采用素材再勾补充料。",
                        "task": task}), 409
    return jsonify({"task": task, "action": action})


@app.route("/api/plan/series/open", methods=["POST"])
def api_plan_series_open():
    """起稿入口（素材库直接起、或从 S2 章节线索起一篇）。

    关联系列 = 传 series/chapter；不传就是独立篇。
    **先建独立稿、事后关联系列**走 `/api/plan/series/link`：换标签不换键，任务与稿都不复制。
    """
    data = request.get_json(force=True, silent=True) or {}
    ids = data.get("video_ids") or []
    if not ids:
        # 素材库那边勾的是 `sheet:row`（卡片键就是它），排产页那边勾的是 DB 主键。
        # 两个入口都要能用，所以在服务端把 sheet:row 现查成 DB id——
        # 让前端各自决定传哪种，迟早出现"传了行号被当成 id"那种串号。
        ids = _plan_ids_from_keys(data.get("keys") or [])
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "至少勾一条素材才能起稿（本单不做空稿占位）"}), 400
    series = str(data.get("series") or "free").strip()[:10]
    if series not in ("s1", "s2", "free"):
        return jsonify({"error": "series 只能是 s1 / s2 / free"}), 400
    chapter = str(data.get("chapter") or "").strip()[:6]
    form = str(data.get("form") or "guide").strip()[:20]
    channel = str(data.get("channel") or "toutiao").strip()[:20]
    if not plan_tasks.resolve_summary_type(channel, form):
        return jsonify({"error": "渠道「%s」没有内容形式「%s」" % (channel, form)}), 400
    title = str(data.get("title") or "").strip()[:80]
    if not title:
        return jsonify({"error": "给这一篇起个标题（台账与海报都要用，空着没法复盘）"}), 400
    if str(data.get("slot_key") or "").strip():
        # 选了 S1 的某个位置（或旧题）→ 这就是"采用到那一格"，走 _plan_adopt 那条路。
        # 为什么不能新建槽号：A3 是**内容位置**，不是一篇稿。给它另建一槽，排产表上就会
        # 出现"入门设置 7 个位置"这种假数，还会多出一条没人认领的 free-xxxxx
        # （第一版实测就长出过一条，走查截图里看得见）。
        out, code = _plan_adopt(dict(data, slot_key=str(data["slot_key"]).strip(),
                                     video_ids=ids, channel=channel, form=form))
        return (out, code) if code != 200 else out
    try:
        s = plan_series.new_slot(DB_PATH, title=title, series=series, chapter=chapter,
                                 form=form, link_slot=str(data.get("link_slot") or "")[:40],
                                 keywords=[str(x)[:20] for x in (data.get("keywords") or [])][:6])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    selected, problems = _plan_materials(ids)
    if not selected:
        return jsonify({"error": "素材都不可用，稿没起", "problems": problems,
                        "slot": s["slot_key"]}), 422
    meta = _plan_meta(dict(data, title=title))
    task, action = plan_tasks.save_sources(DB_PATH, s["slot_key"], channel, form, meta, selected)
    # `slot` 一律回**槽号字符串**（与 _plan_adopt 一致），细节放 `slot_obj`：
    # 两个入口的返回形状不一样，前端迟早写出 d.slot.slot_key 或 d.slot 两种取法，
    # 其中一种会在另一个入口上静默变 undefined。
    return jsonify({"slot": s["slot_key"], "slot_obj": s, "task": task,
                    "action": action, "problems": problems})


@app.route("/api/plan/series/link", methods=["POST"])
def api_plan_series_link():
    """独立稿事后**引用**到某个内容位置：只写 parent_slot，稿件自己的身份与槽号都不动。

    为什么不改 series/position：S1 那 26 格是内容位置，把稿件写进 s1 会让看板数出 27 个位置，
    还和地图里真正的那个位置撞成两行（Codex 二次复验第 4 条）。
    """
    data = request.get_json(force=True, silent=True) or {}
    s, err = _plan_slot_or_400(data.get("slot_key"))
    if err:
        return err
    series = str(data.get("series") or "").strip()[:10]
    if series not in ("s1", "s2", "free", "legacy"):
        return jsonify({"error": "series 只能是 s1 / s2 / free / legacy"}), 400
    try:
        out = plan_series.link_to_series(DB_PATH, s["slot_key"], series,
                                         str(data.get("chapter") or "").strip()[:6],
                                         str(data.get("position") or "").strip()[:10],
                                         str(data.get("link_slot") or "").strip()[:40])
    except ValueError as e:
        return jsonify({"error": str(e), "slot": s["slot_key"]}), 400
    return jsonify({"slot": out})


@app.route("/api/plan/match/run", methods=["POST"])
def api_plan_match_run():
    """页面上那颗「现在匹配」。与夜间同一套代码、同一个幂等判据，只是键带时间戳。"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        with _db_lock:
            out = plan_series.run_match(DB_PATH, kind="manual",
                                        actor=str(data.get("by") or "页面"),
                                        only_slots=data.get("only_slots") or None)
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/plan/match/config", methods=["GET", "POST"])
def api_plan_match_config():
    """夜间匹配的开关与执行时刻（默认 22:00 北京时间）。"""
    if request.method == "GET":
        return jsonify({"cfg": plan_series.load_cfg(), "status": plan_series.status_line(DB_PATH)})
    data = request.get_json(force=True, silent=True) or {}
    try:
        cfg = plan_series.save_cfg(bool(data.get("enabled")),
                                   str(data.get("run_at") or plan_series.DEFAULT_RUN_AT))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"cfg": cfg, "status": plan_series.status_line(DB_PATH)})


@app.route("/api/plan/publish", methods=["GET", "POST"])
def api_plan_publish():
    """「已发布」这一勾的服务端真相。

    GET  读这一格发过没有（含撤销后的状态）；
    POST action=register 登记（写台账 + 审计）｜revoke 撤销（只追加审计，不删台账行）｜
         report_local 上报旧浏览器的本地勾选｜resolve_local 人核对完旧勾选。
    这里**不判内容质量**：登记只证明"人工发出去了"，质量门在下游那几道闸里。
    """
    if request.method == "GET":
        s, err = _plan_slot_or_400(request.args.get("slot_key"))
        if err:
            return err
        try:
            return jsonify(plan_series.published_state(
                DB_PATH, s["slot_key"], channel=request.args.get("channel") or ""))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    data = request.get_json(force=True, silent=True) or {}
    s, err = _plan_slot_or_400(data.get("slot_key"))
    if err:
        return err
    action = str(data.get("action") or "").strip()
    # 渠道一律归一后再传：排产页交的是 'toutiao'，读的人可能写「头条」，两个都认，
    # 但判据始终是 (槽号, 渠道) 这一对——公众号发过不能替头条说"这格已发"。
    # 拼错的渠道在这里就拒（`norm_channel` 不再静默退成头条），别拿头条的答案糊过去。
    try:
        channel = plan_series.norm_channel(data.get("channel") or "toutiao")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    try:
        with _db_lock:
            if action == "register":
                out = plan_series.register_slot(DB_PATH, s["slot_key"],
                                                dict(data, channel=channel))
            elif action == "revoke":
                out = plan_series.revoke_slot(DB_PATH, s["slot_key"],
                                              str(data.get("ref_id") or ""),
                                              str(data.get("reason") or ""),
                                              str(data.get("by") or ""), channel=channel)
            elif action == "report_local":
                out = plan_series.report_local(DB_PATH, s["slot_key"],
                                               bool(data.get("local_published")),
                                               str(data.get("note") or "浏览器本地勾选"))
            elif action == "resolve_local":
                out = plan_series.resolve_local(DB_PATH, s["slot_key"],
                                                str(data.get("verdict") or ""),
                                                str(data.get("by") or ""))
            else:
                return jsonify({"error": "action 只能是 register / revoke / report_local /"
                                         " resolve_local，收到「%s」" % action}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e), "retry": True}), 500
    return jsonify({"result": out, "channel": channel,
                    "state": plan_series.published_state(DB_PATH, s["slot_key"],
                                                          channel=channel)})


@app.route("/api/plan/task", methods=["POST"])
def api_plan_task_save():
    """只存任务与"本篇采用哪几条素材"，**不生成**（生成是下一个端点，那一下才花钱）。

    采用清单落库而不是留在浏览器里：PRD 3.1 要求"重新查素材只刷新候选，
    不能悄悄替换已用证据"——候选是每次现查的，采用的是这一行存下来的。
    """
    data = request.get_json(force=True) or {}
    slot = str(data.get("slot_key") or "").strip()[:40]
    channel = str(data.get("channel") or "").strip()[:20]
    form = str(data.get("form") or plan_tasks.DEFAULT_FORM).strip()[:20]
    if not slot:
        return jsonify({"error": "缺 slot_key（排产表里这一题的 id）"}), 400
    if channel not in plan_tasks.VALID_CHANNELS:
        return jsonify({"error": "本单只开头条与公众号两条线，channel 只能是 %s"
                        % "/".join(plan_tasks.VALID_CHANNELS)}), 400
    if not plan_tasks.resolve_summary_type(channel, form):
        return jsonify({"error": "渠道「%s」没有内容形式「%s」" % (channel, form),
                        "forms": [f for (c, f) in plan_tasks.FORMS if c == channel]}), 400
    raw_ids = data.get("video_ids")
    if raw_ids is None:
        return jsonify({"error": "没带 video_ids 字段：要清空请显式传空数组"}), 400
    try:
        selected, problems = _plan_materials(raw_ids)
    except Exception as e:
        return jsonify({"error": str(e), "retry": True}), 502
    # 显式传空数组＝"这一版的素材全部取消采用"，要存得下来（界面那颗「取消采用」点到最后一条靠它）；
    # 给了 id 但一条都不能用＝素材本身有问题，不存空任务，把原因交回界面。
    if raw_ids and not selected:
        return jsonify({"error": "没有可用素材，任务没存", "problems": problems}), 422
    task, action = plan_tasks.save_sources(DB_PATH, slot, channel, form, _plan_meta(data), selected)
    return jsonify({"task": task, "action": action, "created": action == "created",
                    "problems": problems,
                    "summary_type": plan_tasks.resolve_summary_type(channel, form)})


@app.route("/api/plan/task/generate", methods=["POST"])
def api_plan_task_generate():
    """排产主入口那一下：默认**继续已有稿**，显式重新生成才建新版本。

    四条硬规矩（都是这一单要验收的）：
      1 已有 done 的稿且没传 regenerate → 直接回那篇稿，一次模型都不调（不重复花钱）；
      2 正在跑的那版没跑完 → 409，双击/刷新/第二个标签都撞在同一行上（防重靠 DB 那一条
        带条件的 UPDATE，不靠内存标志）；
      3 素材缺正文/已被删 → 422 带具体恢复入口，**不起任务**；
      4 regenerate → version+1 新行，前稿原样留着。
    """
    data = request.get_json(force=True) or {}
    slot = str(data.get("slot_key") or "").strip()[:40]
    channel = str(data.get("channel") or "").strip()[:20]
    form = str(data.get("form") or plan_tasks.DEFAULT_FORM).strip()[:20]
    regenerate = bool(data.get("regenerate"))
    if not slot:
        return jsonify({"error": "缺 slot_key"}), 400
    if channel not in plan_tasks.VALID_CHANNELS:
        return jsonify({"error": "本单只开头条与公众号两条线，channel 只能是 %s"
                        % "/".join(plan_tasks.VALID_CHANNELS)}), 400
    summary_type = plan_tasks.resolve_summary_type(channel, form)
    if not summary_type:
        return jsonify({"error": "渠道「%s」没有内容形式「%s」，不能默认落到游戏攻略那一档"
                        % (channel, form),
                        "forms": [f for (c, f) in plan_tasks.FORMS if c == channel]}), 400
    req_ids = [int(x) for x in (data.get("video_ids") or []) if str(x).strip()]
    base_task_id = data.get("base_task_id")
    base_version = data.get("base_version")
    # T1/T2（Codex 第二次复验）：**素材只认库里那一版**。prepare_run 在一个写事务里把
    # "读当前版 → 判能不能跑 → 定这一版用哪几条"做完；请求带来的 video_ids 只当校验用，
    # 与库里不一致一律 409 stale——以前 pending/failed 上不拦，于是存 [12] 却用 [112] 生成，
    # 任务记的是一回事、稿子的来源是另一回事（复现见 repro-r2.py 第一项）。
    # 先验请求带的素材，再决定要不要落一行：**一条都不能用就不建任务**，
    # 否则库里会留下一行"记着素材 120、其实什么都没生成"的脏版本（第一版就踩过，
    # 后面同一题的请求全部撞上 409，看着像 stale 其实是上一条留下的壳）。
    problems = []
    usable_ids = []
    if req_ids:
        try:
            selected_req, problems = _plan_materials(req_ids)
        except Exception as e:
            return jsonify({"error": str(e), "retry": True}), 502
        if not selected_req:
            return jsonify({"mode": "blocked", "error": "没有可用素材，没起生成任务",
                            "problems": problems}), 422
        # 存进去的必须是"真能用的那几条"：请求带了 3 条、1 条没正文，
        # 若把 3 条原样存下，稿子只用 2 条，任务记的与稿子用的又对不上（T1 同一个洞）
        usable_ids = [int(v["id"]) for v in selected_req]
    d = plan_tasks.prepare_run(DB_PATH, slot, channel, form, _plan_meta(data), usable_ids,
                               regenerate=regenerate, base_task_id=base_task_id,
                               base_version=base_version)
    latest = d.get("task")
    decision = d["decision"]
    if decision == "continue":
        return jsonify({"mode": "continue", "task": latest,
                        "summary_id": latest.get("summary_id"),
                        "note": "这一版已经有稿，默认继续处理；要换一版请显式点「重新生成」"})
    if decision == "running":
        return jsonify({"mode": "running", "error": "这一版正在生成，等它跑完再说",
                        "task": latest, "job": _plan_job(latest["id"])}), 409
    if decision == "blocked":
        return jsonify({"mode": "blocked", "task": latest,
                        "error": d.get("reason") or "这一版没有可用素材",
                        "problems": [{"code": "no_sources", "msg": d.get("reason") or "",
                                      "action": "reselect", "action_label": "回排产表确认采用素材"}]}), 422
    if decision == "stale":
        return jsonify({"mode": "stale", "task": latest,
                        "stored_video_ids": (latest or {}).get("video_ids"),
                        "error": (d.get("reason") or "请求与库里那一版不一致")
                                 + "。已开工版本的证据不许被覆盖：要换素材请先在排产表点「采用」存好"}), 409

    # 占锁前先验一次素材：全不可用就别占锁，那一版留在原状态，人改完可以直接再试
    run_ids = [int(x) for x in (latest.get("video_ids") or [])]
    try:
        selected, run_problems = _plan_materials(run_ids)
    except Exception as e:
        return jsonify({"error": str(e), "retry": True}), 502
    # 请求里被剔掉的那几条（没正文/已删除）要一路带到响应里——界面靠它给恢复入口；
    # 只报"跑起来那几条"的问题会把"你刚才那条 120 我没用"这件事悄悄吞掉。
    problems = list(problems or []) + [p for p in (run_problems or [])
                                       if p not in (problems or [])]
    if not selected:
        return jsonify({"mode": "blocked", "error": "没有可用素材，没起生成任务",
                        "task": latest, "problems": problems}), 422
    token = plan_tasks.claim_running(DB_PATH, latest["id"])
    if not token:
        # T2：抢不到锁要**重读之后分情况回**，不能一律说"正在跑"
        back = plan_tasks.get_task(DB_PATH, latest["id"]) or latest
        if back.get("summary_id") and back.get("status") == "done":
            return jsonify({"mode": "continue", "task": back, "summary_id": back["summary_id"],
                            "note": "这一版刚被别的请求跑完了，直接用它"})
        if back.get("status") == "running":
            return jsonify({"mode": "running", "error": "这一版刚被别人占了（同一题同渠道只跑一个）",
                            "task": back, "job": _plan_job(back["id"])}), 409
        return jsonify({"mode": "stale", "task": back,
                        "error": "这一版的状态刚被改动（现在 %s），请刷新再看"
                                 % back.get("status")}), 409
    attempt_this = (latest.get("attempt") or 0) + 1   # claim 只 +1，重读之后也还是这个数
    # T1 收尾的一刀：prepare_run 与 claim 之间还有一段没上锁的时间，
    # 若这期间有人把采用清单改了（pending 上就地改），我手上那份快照就不是库里那一版了。
    # 占锁成功后**按锁住的这一行重读一次**，生成依据与记录同源；行已经是 running，
    # 之后的 save_sources 只会开新版、改不动这一行，所以这次读数就是最终值。
    after = plan_tasks.get_task(DB_PATH, latest["id"]) or latest
    if [int(x) for x in (after.get("video_ids") or [])] != run_ids:
        run_ids = [int(x) for x in (after.get("video_ids") or [])]
        latest = after
        try:
            selected, run_problems = _plan_materials(run_ids)
        except Exception as e:
            return jsonify({"error": str(e), "retry": True}), 502
        problems = [p for p in (run_problems or []) if p not in (problems or [])] + problems
        if not selected:
            plan_tasks.finish(DB_PATH, latest["id"], "aborted",
                              error=_short_err("占锁后采用清单被改成没有可用素材的那一组，"
                                               "这次没生成；先在排产表重新确认采用再生成"),
                              token=token)
            return jsonify({"mode": "blocked", "error": "这一版的采用清单刚被改过，改完的那几条"
                                                        "现在都不能用，没起生成",
                            "task": after, "problems": problems}), 422
    job = _plan_job(latest["id"])
    _plan_job_reset(job, "准备生成内容...")
    task_id = latest["id"]
    model_name = (_fast_config() or {}).get("model") or ""
    # R3：选题要求进真实提示词。取的是**库里那一版**存的字段（不是请求里带的），
    # 这样"当初用什么要求生成的"永远能从任务行回查，与冻结的证据同源。
    brief = {"title": latest.get("title"), "hook": latest.get("hook"),
             "note": latest.get("note"), "plan_date": latest.get("plan_date"),
             "channel_label": plan_tasks.channel_label(latest.get("channel") or channel),
             "form_label": plan_tasks.form_label(latest.get("form") or form)}
    # 补充资料（非视频料）也取自**任务行里存的那一份**，与选题要求同一套规矩：
    # 当初勾了哪几条、凭什么算相关，事后能从 supplement_sources 回查，不许现编。
    sup_ids = latest.get("supplement_ids") or []
    if sup_ids:
        try:
            brief["supplements"] = supplements.rows_by_ids(
                DB_PATH, sup_ids, snaps=latest.get("supplement_sources"))
        except Exception as e:
            print("[plan] 补充资料读取失败（这次生成不带它们）: %s" % e, flush=True)

    def run():
        # T1：worker 用事务定下来的那几条素材 id，不再经 sheet:row 绕回另一条记录
        ids = _run_generation(None, summary_type, len(run_ids) > 1, job, brief,
                              video_ids=run_ids)
        try:
            if ids:
                plan_tasks.finish(DB_PATH, task_id, "done", summary_id=ids[0],
                                  error=job.get("error") or "", model=model_name, token=token)
            else:
                plan_tasks.finish(DB_PATH, task_id, "failed",
                                  error=_short_err(job.get("error") or job.get("progress")
                                                   or "没生成出稿"),
                                  model=model_name, token=token)
        except Exception:
            app.logger.warning("排产任务收尾失败 task=%s", task_id, exc_info=True)
        finally:
            job["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"mode": "started", "task_id": task_id, "task": latest,
                    "total": len(run_ids), "combine": len(run_ids) > 1,
                    "summary_type": summary_type, "problems": problems,
                    "attempt": attempt_this})


@app.route("/api/plan/task/<int:task_id>")
def api_plan_task_detail(task_id):
    """一个任务的当前状态 + 它自己的进度（**不读全局 _task_status**）。

    为什么单独一条：素材库批量那套轮询的是全局状态，双渠道同时生成时谁后跑谁覆盖，
    两条进度会串。这里按 task_id 各存一份。
    """
    task = plan_tasks.get_task(DB_PATH, task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({"task": task, "job": _plan_job(task_id),
                    "summary": (content_store.get_summary(DB_PATH, task["summary_id"])
                                if task.get("summary_id") else None)})


@app.route("/api/plan/tasks")
def api_plan_tasks_list():
    """按选题（slot_key）或按素材（video_id）查任务。同题双渠道要能同时看见。"""
    slot = str(request.args.get("slot_key") or "").strip()[:40]
    vid = str(request.args.get("video_id") or "").strip()
    try:
        if slot:
            return jsonify({"tasks": plan_tasks.tasks_for_slot(DB_PATH, slot)})
        if vid:
            return jsonify({"tasks": plan_tasks.tasks_for_video(DB_PATH, int(vid))})
    except Exception as e:
        return jsonify({"error": str(e), "tasks": []}), 500
    return jsonify({"error": "要带 slot_key 或 video_id"}), 400


# ══ 整理稿精确关联（修 12/112 子串误配与前 500 篇漏稿）════════════════════
@app.route("/api/content/summaries/by-video/<int:video_id>")
def api_content_summaries_by_video(video_id):
    """这条素材的整理稿，**服务端精确匹配**。

    以前是前端拉 `?page_size=500` 再在 JS 里对 `source_video_ids` 那个 JSON 字符串做
    indexOf：id=12 命中 "[112, 113]"（把无关合集稿算成这条的稿），第 501 篇以后永远看不见。
    现在用 SQLite 的 json_each 精确比，且**没有条数上限**；查不到就明确返回空列表，
    查询本身出错返回 500——前端据此区分"确实没有稿"和"没查到"，不许把后者当前者重跑收费生成。
    """
    try:
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT s.id, s.summary_type, s.title, s.status, s.video_id,"
                " s.source_video_ids, s.model, s.updated_at"
                " FROM ai_summaries s"
                " WHERE s.video_id = ?"
                "    OR (json_valid(s.source_video_ids)"
                "        AND EXISTS (SELECT 1 FROM json_each(s.source_video_ids)"
                "                    WHERE value = ?))"
                " ORDER BY s.updated_at DESC", (int(video_id), int(video_id))).fetchall()
        finally:
            con.close()
        return jsonify({"video_id": int(video_id), "items": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e), "items": []}), 500


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


def _pool_frame_items(aweme_id: str, limit: int = 5):
    """从帧池（output/assets/frames/<aid>）取能展示的帧，返回 (items, judged, has_pool)。

    为什么优先读这一层而不是老的 keyframes/：老路要先解析出抖音无水印直链才能 ffmpeg 抽帧，
    那个 parser 从 9 月底就废了（Argus `Uifid Not Found`），实测 keyframes/ 最后一次有图是
    9-21 23:14，之后建的目录全是空的——于是「素材库→整理稿」一路看图没了，看着像画面丢了。
    其实采集层（main.py 的 frame_pool.build）一直在抽帧并落在 frames/raw，零成本、就在盘上，
    只是以前只有海报弹窗那一个入口读它。

    judged=True 表示取到的是判过且确认没后期字的 clean 帧；False 是还没逐张看图的候选帧
    （判帧花钱，号主定的口径是「只补真要发的那几条」，所以采集侧不判）。后者只当取材线索，
    出图那一档不会自动用它们。
    has_pool 单独返回：帧池存在但一张取不出来 = 这条素材的画面全被判掉了（或原帧已清理），
    caller 不能再退回老路去重下视频——那会把刚被"宁缺毋滥"扔掉的帧又当图配进稿子。
    """
    try:
        from src import frame_pool
    except ImportError:
        import frame_pool
    items: list = []
    meta = frame_pool.load(aweme_id)
    frames = (meta or {}).get("frames") or []
    if not frames or limit <= 0:
        return items, False, bool(frames)
    picked = frame_pool.usable(meta)
    judged = bool(picked)
    if not picked:
        checked_n = len([f for f in frames if f.get("checked")])
        if checked_n:
            # 判过几张、一张都没留下：这条素材已经被"宁缺毋滥"否过一次了，
            # 剩下没判的那几张大概率是同一批（实测 10-12 那条：判 8 弃 8）。
            # 这时候再从没判的帧里凑图，等于自己推翻自己的判词——和海报弹窗那条口径保持一致
            return items, False, True
        cand = [f for f in frames
                if f.get("file") and os.path.exists(str(f.get("file")))]
        if not cand:
            return items, False, True
        # 均匀取样而不是取前 N 张：一条口播素材的头几帧往往是封面卡或钩子，后期字最密
        stride = len(cand) // limit + 1
        picked = cand[::stride][:limit]
    for f in picked[:limit]:
        rel = os.path.relpath(str(f.get("file")), str(OUTPUT_DIR)).replace(os.sep, "/")
        items.append({"url": "/media/" + rel, "ts": f.get("ts") or "",
                      "subject": (f.get("subject") or "").strip()})
    return items, judged, True


def _video_keyframe_markdown(videos: list, max_frames: int = 5, per_video: int = 2) -> str:
    """对攻略类整理稿的视频抽关键帧（ffmpeg，已有帧则直接复用），返回 Markdown 配图段。

    单视频模式取最多 max_frames=5 张；合并模式每视频最多 per_video=2 张、总数截到 max_frames。
    抽帧失败（无视频地址/下载失败）时返回空串，不影响整理稿本身。

    取图顺序（2026-10-15）：帧池优先、老的 keyframes/ 兜底——老路依赖已废的解析器，
    9-29 以后采的素材那一侧全是空目录，而采集层其实一直把帧抽在 frames/ 里。
    """
    single = len(videos) <= 1
    collected: list[str] = []
    unjudged = False      # 配图段里有没有"还没逐张看图"的帧：有的话要在末尾说清楚
    for video in videos:
        if len(collected) >= max_frames:
            break
        if (video.get("source_sheet") or "") in DB_ONLY_SHEETS:
            continue  # DB 直写的源无视频可抽帧（微信 aweme_id 是 URL hash、B站是 bvid），
                      # 不跳过会误走抖音的下载解析路径
        aweme_id = (video.get("aweme_id") or "").strip() or material_store.extract_aweme_id(
            video.get("source_url") or video.get("video_url") or ""
        )
        if not aweme_id:
            continue
        frame_limit = per_video if not single else max_frames
        # ① 帧池：clean 优先，没判过就均匀取候选帧。这一条一分钱不花、也不用下载视频
        pool_items, judged, has_pool = _pool_frame_items(aweme_id, frame_limit)
        if has_pool:
            # 帧池就是这条素材的画面台账：哪怕一张都没取出来（全被判掉了），也不回退去
            # 重下视频——那等于把刚"宁缺毋滥"扔掉的帧再配回稿子
            if pool_items and not judged:
                unjudged = True
            for it in pool_items:
                if frame_limit <= 0 or len(collected) >= max_frames:
                    break
                alt = "关键帧%d" % (len(collected) + 1)
                if it.get("ts"):
                    alt += "·%s" % it["ts"]
                if it.get("subject"):
                    alt += " %s" % it["subject"][:40]
                collected.append("![%s](%s)" % (alt, it["url"]))
                frame_limit -= 1
            if single:
                break
            continue
        # ② 老的 keyframes/：帧池没有（解析器废之前就采完的老素材）才走这条路
        # 已有帧直接复用，跳过解析下载
        if not material_store.local_keyframe_paths(aweme_id):
            video_url = (video.get("video_url") or "").strip()
            nwm_url = _resolve_download_url(video)
            if nwm_url:
                material_store.extract_keyframes(aweme_id, nwm_url, max_frames=max_frames)
            elif video_url:
                material_store.extract_keyframes(aweme_id, video_url, max_frames=max_frames)
        for rel in material_store.local_keyframe_paths(aweme_id):
            if rel.endswith("sheet.jpg"):
                continue
            if len(collected) >= max_frames or frame_limit <= 0:
                break
            collected.append("![关键帧%d](/media/%s)" % (len(collected) + 1, rel))
            frame_limit -= 1
        if single:
            break
    if not collected:
        return ""
    lines = ["", "## 配图素材（视频关键帧）", ""]
    lines.extend(collected)
    if unjudged:
        # 界面不说假话这条：这些帧没判过后期字，直接发出去就是"图上有别人的字幕"。
        # 所以留一句口径，别让它长得像"已经确认能用"
        lines.append("")
        lines.append("（这些帧还没逐张看过图，出图那一档不会自动用它们；"
                     "要进版面先在海报弹窗点「先看图确认」）")
    lines.append("")
    return "\n".join(lines)


# ── 头条图文：整合稿 → 信息图 + 微头条文案（复制发布保持手动，合规红线） ──
# 独立任务状态：不与 /api/content/generate 共用 _task_status，避免两条产线互相打断
_tt_task_status = {"running": False, "done": True, "progress": "", "error": "", "package": None,
                   "step": None, "steps": None}


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
            cb = _run_progress_cb(_tt_task_status)
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
            _tt_task_status["step"] = _tt_task_status["steps"] = None

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started", "summary_id": summary_id})


@app.route("/api/toutiao/status")
def api_toutiao_status():
    return jsonify(_tt_task_status)


@app.route("/api/toutiao/templates")
def api_toutiao_templates():
    """头条弹窗的模板与三轴预设下发（key + 中文标签），与公众号那条同一套构造逻辑。

    这四个下拉此前写死在 HTML 里：模板 7 项、配色 3 项、字体 2 项、版式 4 项，
    连"配色对哪些模板生效"那句话也是手抄的。内核加一套配色或一个模板，头条这边不会跟上，
    就是本项目反复出现的"选了没反应"。名单与生效范围一律以 graphics 内核为准。
    """
    try:
        ready = [k for k in toutiao_channel.TEMPLATE_WHITELIST
                 if k in graphics_variants.PALETTE_READY_TPLS]
        return jsonify({
            "templates": toutiao_channel.template_choices(),
            "palettes": [{"key": k, "label": graphics_variants.palette_label(k)}
                         for k in graphics_variants.PALETTE_KEYS],
            "fonts": [{"key": k, "label": graphics_variants.font_label(k)}
                      for k in graphics_variants.FONT_KEYS],
            "layouts": ([{"key": k, "label": graphics_variants.layout_label(k)}
                         for k in graphics_variants.LAYOUT_KEYS]
                        + [{"key": graphics_variants.RANDOM_LAYOUT_KEY, "label": "每次随机（自动换排布）"}]),
            "palette_ready": ready,
            "defaults": {"template": "classic", "palette": graphics_variants.DEFAULT_PALETTE_KEY,
                         "font": graphics_variants.DEFAULT_FONT_KEY,
                         "layout": graphics_variants.DEFAULT_LAYOUT_KEY},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/toutiao/list")
def api_toutiao_list():
    try:
        items = toutiao_graphics.list_packages()
        rows, meta = toutiao_stats.load_snapshot()
        matched = [p for p in items if (p.get("performance") or {}).get("matched")]
        app.logger.info("图文列表 %d 组，其中已匹配平台数据 %d 组", len(items), len(matched))
        return jsonify({
            "items": items, "total": len(items),
            "perf": {"platform_total": len(rows), "matched": len(matched),
                     "synced_at": next((p["performance"]["synced_at"] for p in matched
                                        if p["performance"].get("synced_at")), ""),
                     "cookie_from": meta.get("cookie_from") or "",
                     "pages": meta.get("pages"), "total_on_page": meta.get("total_on_page")},
        })
    except Exception as e:
        return jsonify({"error": str(e), "items": [], "total": 0}), 500


# ── 头条图文数据回流：抓创作中心内容管理页，按标题匹配回本地图文包 ────────
# 为什么这么绕：5180 台账只登记过文章且停在 09-17，图文从来是盲区（2026-10-06 取证）。
# 用户拍板不做人肉登记，直接问平台网页要数——所以这里只读不写：打开内容页、翻分页、收接口响应。
# 必须起真实浏览器：裸调 mp_provider 拿到的是随机推荐流（每调一次换一批、has_more 永真）。
_perf_status = {"running": False, "progress": "", "error": "", "result": None, "started_at": ""}
_perf_lock = threading.Lock()


@app.route("/api/toutiao/perf/sync", methods=["POST"])
def api_toutiao_perf_sync():
    with _perf_lock:
        if _perf_status["running"]:
            return jsonify({"error": "已经有一次同步在跑，等它结束"}), 409
        _perf_status.update({"running": True, "progress": "准备打开创作中心…",
                             "error": "", "result": None,
                             "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

    def worker():
        try:
            rows, meta = toutiao_stats.collect(
                progress_cb=lambda s: _perf_status.update({"progress": s}))
            res = toutiao_stats.match_packages(rows=rows, meta=meta)
            # 抓到的数顺手灌进运营台账：头条这一侧从此不用手抄（2026-10-14 号主要求每日回填）。
            # 单独 try：台账写失败不能把已经抓回来的同步结果一起报成失败。
            try:
                led = toutiao_stats.backfill_ledger(DB_PATH)
            except Exception as be:
                app.logger.error("头条→台账失败：%s", traceback.format_exc())
                led = {"written": 0, "new": 0, "error": str(be)}
            _perf_status["result"] = {
                "synced_at": res["synced_at"], "platform_total": res["platform_total"],
                "cookie_from": (meta or {}).get("cookie_from"),
                "total_on_page": (meta or {}).get("total_on_page"),
                "errors": (meta or {}).get("errors") or [],
                "matched": len(res["matched"]), "unmatched": len(res["unmatched"]),
                "ledger": led,
                "top": res["platform_top"][:6],
            }
        except Exception as e:
            app.logger.error("头条数据回流失败：%s", traceback.format_exc())
            _perf_status["error"] = str(e)
        finally:
            _perf_status["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"started": True})


@app.route("/api/toutiao/perf/status")
def api_toutiao_perf_status():
    return jsonify(_perf_status)


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
_wx_task_status = {"running": False, "done": True, "progress": "", "error": "", "package": None,
                    "step": None, "steps": None}
# 成稿单独一份进度：卡片在跑与成稿在跑是两件事，混一份状态一定出现"另一条说完成"的假读数
_wx_article_status = {"running": False, "done": True, "progress": "", "error": "", "article": None,
                        "step": None, "steps": None}


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
            cb = _run_progress_cb(_wx_task_status)
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
            _wx_task_status["step"] = _wx_task_status["steps"] = None

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started", "summary_id": summary_id})


@app.route("/api/wechat/status")
def api_wechat_status():
    return jsonify(_wx_task_status)


# ── 公众号成稿（2026-10-26 策略：这一渠道交的是完整文章，卡片只是可选配图）──
# 单独一条路由而不是给 /api/wechat/generate 加 mode：那一路径的产物是 6-8 张卡，
# 成稿的产物是一篇 md，两者的进度、门禁、结果页都不一样，混在一个 status 里
# 一定会出现"卡片在跑、成稿说完成"这种对不上的读数（排产那条线刚踩过同类坑）。
@app.route("/api/wechat/article", methods=["POST"])
def api_wechat_article_generate():
    data = request.get_json(force=True) or {}
    summary_id = data.get("summary_id")
    ask = str(data.get("ask") or "").strip()[:120]     # 这篇要解决哪一件事（进提示词）
    if not summary_id:
        return jsonify({"error": "缺少 summary_id"}), 400
    # 空切口在**花钱之前**拦：97 号那篇就是 ask 空着跑的，团本预告＋九座地下城＋九处未核实入口
    # 订成一篇，标题还承诺"一次查清"。这一趟实测 3.4 万 token、6 分钟，拦晚了白花。
    if not ask:
        return jsonify({"error": "先写「这篇要解决哪一件事」。空着不生成——没有单篇切口，"
                                 "模型会把整理稿里几个主题订成一篇合订表（97 号就是这么来的）"}), 400
    if _wx_article_status.get("running"):
        return jsonify({"error": "已有一篇成稿在写，等它跑完"}), 409
    try:
        summary = content_store.get_summary(DB_PATH, int(summary_id))
    except Exception as e:
        return jsonify({"error": str(e), "retry": True}), 502
    if not summary:
        return jsonify({"error": "整理稿不存在"}), 404
    if summary.get("summary_type") != "wechat_material":
        return jsonify({"error": "只有公众号整理稿（wechat_material）能成稿，请先用「生成：公众号文章」出一份"}), 400
    _wx_article_status.update({"running": True, "done": False, "progress": "准备整篇改写...", "error": "",
                               "article": None})

    def run():
        try:
            cb = _run_progress_cb(_wx_article_status)
            # 素材台账（类型＋发布日）由存储层给：上一版成稿把两篇暴雪官方公告通篇写成
            # "官方视频1/视频2"，就是成稿这一层看不到素材是什么类型、只能照整理稿的编号措猜。
            ledger = ""
            try:
                src_ids = [int(x) for x in json.loads(summary.get("source_video_ids") or "[]")]
                if not src_ids and summary.get("video_id"):
                    src_ids = [int(summary["video_id"])]
                vids = content_store.get_videos_by_ids(DB_PATH, src_ids)
                ledger = content_store.source_ledger(vids)
            except Exception:
                app.logger.warning("成稿台账取不到（来源类型与日期会写成素材未记录）", exc_info=True)
            meta = wechat_graphics.generate_article(summary, _fast_config(), ask=ask,
                                                    progress_cb=cb, ledger=ledger)
            _wx_article_status["article"] = meta
        except GraphicsGateError as e:
            _wx_article_status["error"] = str(e)
            _wx_article_status["progress"] = "成稿被门禁拦下"
        except Exception:
            _wx_article_status["error"] = _short_err(traceback.format_exc())
            _wx_article_status["progress"] = "成稿失败"
        finally:
            _wx_article_status["done"] = True
            _wx_article_status["running"] = False
            _wx_article_status["step"] = _wx_article_status["steps"] = None

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started", "summary_id": int(summary_id)})


@app.route("/api/wechat/article/status")
def api_wechat_article_status():
    return jsonify(_wx_article_status)


@app.route("/api/wechat/article/parts", methods=["POST"])
def api_wechat_article_parts():
    """现算**一份最终判定**（拆分 + 全部机检 error）——绿条、复制许可、lint 面板读同一份。

    前端每次按键 debounce 后把编辑框里这份 md 递过来。为什么不在前端切：编辑框是活的，
    客户端自己切一遍就会和 lint／复制／存档各判各的（上一版"提示让你手动截正文、
    按钮却复制全文"就是两份实现漂出来的）。不落盘、不调模型。
    响应回显 `echo`（请求文本的字数与结尾 24 字）：前端拿它判"这份结果属不属于当前文本"，
    防的是"旧请求晚回来、把新文本解锁"这类竞态（Codex 复验 P0-1）。
    """
    md = str((request.get_json(force=True) or {}).get("md") or "")
    verdict = wechat_graphics.article_verdict(md)
    verdict["echo"] = {"chars": len(md), "tail": md[-24:]}
    return jsonify(verdict)


@app.route("/api/wechat/article/topics/<int:summary_id>")
def api_wechat_article_topics(summary_id):
    """单篇切口建议：从整理稿的小节标题确定性列出来（不调模型、不花钱），供弹窗点选。"""
    try:
        summary = content_store.get_summary(DB_PATH, int(summary_id))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not summary:
        return jsonify({"error": "整理稿不存在"}), 404
    art = wechat_graphics.read_article(summary_id)
    return jsonify({"topics": wechat_graphics.topic_suggestions(summary),
                    "summary_title": summary.get("title") or "",
                    "ask_used": (art or {}).get("ask") or "",
                    "has_article": bool(art)})


@app.route("/api/wechat/article/<int:summary_id>")
def api_wechat_article_get(summary_id):
    art = wechat_graphics.read_article(summary_id)
    if not art:
        return jsonify({"error": "这一篇还没有成稿", "summary_id": summary_id}), 404
    return jsonify(art)


@app.route("/api/wechat/article/<int:summary_id>", methods=["PUT"])
def api_wechat_article_save(summary_id):
    """人工改稿存回去，并**按改后这份重判格式**——沿用旧判定等于"改了字报警还挂着"。"""
    data = request.get_json(force=True) or {}
    md = data.get("md")
    if md is None:
        return jsonify({"error": "没带 md 字段"}), 400
    try:
        return jsonify(wechat_graphics.save_article(summary_id, str(md)))
    except GraphicsGateError as e:
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
                  "stage": "", "package": None, "plan": None,
                  # 海报任务是全局单例，但界面上可以同时开两条整理稿的弹窗：不带归属的
                  # 状态会被后一个任务覆盖，前一个弹窗轮询到的就是别人的结果（表现为
                  # "我给 A 出图，页面上贴出来 B 的图"）。task_id 每次提交自增，
                  # summary_id 记这条任务属于哪张整理稿，两者一起下发让前端能拒收。
                  "summary_id": 0, "task_id": 0}
_poster_task_seq = 0


def _poster_plan_file(sid):
    return Path(str(OUTPUT_DIR)) / "poster" / str(int(sid)) / "plan.json"


def _poster_save_plan(sid, payload):
    """把拆好的文案（连档位、渠道、那份 0 元提示词）落进成品包。

    为什么：`_poster_status` 是进程内的，**重启一次就把刚拆好的文案一起吃掉**——
    那是花过模型钱、等过八九分钟的那一份（号主 10-23 为这个重拆了两回）。
    落盘后重开弹窗、重启服务都接得回来。写失败不影响这次结果，只少一份备份。
    """
    try:
        p = _poster_plan_file(sid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print("[poster] 文案备份没写成（不影响这次结果）:", e)


def _poster_load_plan(sid):
    try:
        p = _poster_plan_file(sid)
        if not p.is_file():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("plan"), dict) else None
    except Exception:
        return None


def _humanize_llm_error(full_tb: str) -> str:
    """把 LLM 端抛回来的堆栈翻成一句人话（完整堆栈已经进日志，不丢）。

    为什么必须翻：2026-10-14 号主点「拆文案」失败，界面上糊了一整屏 Python 堆栈，
    他只能来问"这是什么问题"。真正的原因就一句——**这一档模型的免费额度用完了**。
    认不出来的异常仍然带上堆栈最后两行，别把真 bug 藏成"未知错误"。
    """
    m = re.search(r"model='([^']+)'", full_tb)
    model = m.group(1) if m else ""
    who = model or "这次用的模型"
    if "insufficient_quota" in full_tb or "Free quota exhausted" in full_tb:
        from src import ai_failover
        ev = [e for e in ai_failover.events() if e.get("from") == model]
        jumped = ("；刚才已经自动跳过 %d 个候选（%s）" % (
            len(ev), "、".join("%s→%s" % (e["from"], e["to"]) for e in ev))) if ev else ""
        return ("%s 的免费额度用完了（平台按模型算额度，不是按账号）%s。"
                "现在这条池子里没有可用模型了，三条出路：① 去控制台看哪个模型还有额度，"
                "把 `模型名|到期日` 加进 config/config.env.local 的 AI_MODEL_POOL（改完不用重启）；"
                "② 设置页把这一档写死成还有额度的那个模型；"
                "③ 去控制台关掉「免费额度用尽即停」转按量付费（要花钱）。"
                % (who, jumped))
    if "AuthenticationError" in full_tb or "Error code: 401" in full_tb:
        return "%s 鉴权失败：key 不对或已失效，去设置页重填" % who
    if "PermissionDenied" in full_tb or "Error code: 403" in full_tb:
        return "%s 被端点拒绝（403）：多半是这个模型没给你这个账号开放" % who
    if "RateLimit" in full_tb or "Error code: 429" in full_tb:
        return "%s 被限流（429）：等一会儿再点，别连着重试" % who
    if ("SSLError" in full_tb or "EOF occurred" in full_tb or "ConnectionError" in full_tb
            or "Max retries exceeded" in full_tb):
        # 2026-10-15 22:51 实测：图已经生成好（0.5 元已扣），下载那一跳 TLS 被掐
        return ("这一跳网络断了（对面没报错，是连接被掐断）。等一会儿再点；"
                "如果是出图那一步断的，图可能已经生成并扣过费，先去百炼控制台看一眼用量再重按")
    if "Timeout" in full_tb or "timed out" in full_tb:
        return "%s 这次没响应（超时）：端点慢或断了，可以再点一次" % who
    tail = [l for l in full_tb.strip().splitlines() if l.strip()][-2:]
    return "任务失败：%s" % " ⏎ ".join(tail)


def _poster_start(stage, fn, summary_id=0):
    """海报任务统一壳。拆解与出图都是几十秒到几分钟的活，必须后台跑 + 前端轮询。"""
    global _poster_task_seq
    if _poster_status["running"]:
        return 0
    _poster_task_seq += 1
    _poster_status.update({"running": True, "done": False, "progress": "任务排队中...", "error": "",
                           "stage": stage, "task_id": _poster_task_seq,
                           "summary_id": int(summary_id or 0),
                           "fact_hard": [],        # 上一任务拦下的清单不能留给下一次点按钮
                           "package": None if stage == "plan" else _poster_status["package"],
                           "plan": None if stage == "poster" else _poster_status["plan"]})

    def wrapper():
        try:
            fn()
        except ai_poster.PosterError as e:
            _poster_status["error"] = str(e)        # 可预期失败只给一句话，不刷 traceback
            # 事实闸拦下来的那些条目**逐条**给前端：人要按条填出处与结论，不是一句"我核过了"
            _poster_status["fact_hard"] = list(getattr(e, "fact_hard", None) or [])
            _poster_status["progress"] = "生成失败"
        except Exception:
            full = traceback.format_exc()
            app.logger.error("海报任务失败：\n%s", full)
            _poster_status["error"] = _humanize_llm_error(full)
            _poster_status["progress"] = "生成失败"
        finally:
            _poster_status["done"] = True
            _poster_status["running"] = False

    threading.Thread(target=wrapper, daemon=True).start()
    return _poster_task_seq


def _poster_get_summary(summary_id):
    if not summary_id:
        return None
    return content_store.get_summary(DB_PATH, int(summary_id))


GUIDE_FRAME_TOTAL_CAP = 12     # 整组最多给拆解层几张真帧（一页一张，6 页也用不完这么多）
GUIDE_JUDGE_CAP = 8            # 每条素材最多判几张：判帧要花钱，一条 18 帧全判不值


def _risk_inherit(result, videos):
    """新稿落库前：把这一素材台账里**还开着**的待核并进正文那一节核查清单。

    为什么必须做在落库前（2026-10-29 反例）：同一份素材 309874，10-26 那版列了 11 条待核，
    10-29 重跑只写了 2 条——读者抓的「愈合祷言 40%/200%」「切目标不掉连击点」正好丢了。
    解析器只能读已经写出来的内容，**模型漏写就等于风险静默消失**，所以记忆得存在稿子外面。
    """
    ids = [int(v["id"]) for v in videos if v.get("id")]
    if not ids:
        return 0
    try:
        with _db_lock:
            risk_ledger.ensure(DB_PATH)
            items = risk_ledger.open_items(DB_PATH, ids)
        md, n = risk_ledger.inherit_into(result.get("content") or "", items)
        if n:
            result["content"] = md
            print("[台账] 这一版漏写、从台账继承 %d 条待核（素材 %s）" % (n, ids), flush=True)
        return n
    except Exception as e:                                  # noqa: BLE001
        print("[台账] 继承待核失败（不拦生成）:", e, flush=True)
        return 0


def _risk_record(result, videos, summary_id):
    """新稿写出来的待核入账（含刚继承进去的那些，重复登记不改变判据）。"""
    ids = [int(v["id"]) for v in videos if v.get("id")]
    if not ids:
        return 0
    try:
        with _db_lock:
            risk_ledger.ensure(DB_PATH)
            return risk_ledger.record_draft(DB_PATH, ids, summary_id,
                                            fact_gate.risk_items(result.get("content") or ""))
    except Exception as e:                                  # noqa: BLE001
        print("[台账] 入账失败（不拦生成）:", e, flush=True)
        return 0


def _fact_ctx(summary):
    """出包这一刻的事实上下文：素材来源与日期 + 台账里还开着的待核 + 一手文本。

    `official_blobs` 是"这句官方公告里到底有没有"的对照面——库里那批《"无限"职业深度解析》
    就是现成的一手文本，不花钱、不用问模型。命中就把硬拦降成提示（按一手发）。
    """
    mats = _guide_materials(summary) or []
    ids = [int(m["video_id"]) for m in mats if m.get("video_id")]
    by_id = {}
    if ids:
        try:
            with _db_lock:
                rows = content_store.get_videos_by_ids(DB_PATH, ids)
            by_id = {int(r["id"]): r for r in rows}
        except Exception as e:                              # noqa: BLE001
            print("[事实上下文] 取素材行失败:", e, flush=True)
    materials = []
    for m in mats:
        v = by_id.get(int(m.get("video_id") or 0)) or {}
        sheet = str(v.get("source_sheet") or m.get("sheet") or "")
        materials.append({"author": v.get("author") or "", "sheet": sheet,
                          "published_at": v.get("published_at") or "",
                          "first_party": sheet in FIRST_PARTY_SHEETS,
                          "is_video": supplements.is_video(sheet)})
    open_items, resolved, blobs = [], [], []
    try:
        with _db_lock:
            risk_ledger.ensure(DB_PATH)
            open_items = risk_ledger.open_items(DB_PATH, ids)
            resolved = [r for r in risk_ledger.all_items(DB_PATH, ids)
                        if r.get("status") == "resolved"]
            con = sqlite3.connect(str(DB_PATH))
            con.row_factory = sqlite3.Row
            ph = ",".join("?" for _ in FIRST_PARTY_SHEETS)
            blobs = [r["transcript"] or "" for r in con.execute(
                "SELECT transcript FROM videos WHERE source_sheet IN (%s)"
                " AND COALESCE(transcript,'')<>''" % ph, FIRST_PARTY_SHEETS)]
            con.close()
    except Exception as e:                                  # noqa: BLE001
        print("[事实上下文] 台账/一手文本读取失败:", e, flush=True)
    return {"materials": materials, "open_items": open_items, "resolved": resolved,
            "official_blobs": blobs,
            "open_risks": [str(i.get("raw") or "") for i in open_items],
            "generated_at": datetime.now().strftime("%Y-%m-%d")}


def _guide_materials(summary):
    """这篇稿**实际采用的每一条素材**（主素材排最前），返回 [{"video_id","aweme_id","title"}]。

    为什么不能只看主素材那一条（2026-10-22 真试用抓到的）：`summary["aweme_id"]` 是
    `get_summary` join 出来的**主视频**，而整合稿的主视频常常抽完 ASR 就把原片删了 —— 帧池是空的。
    那次 B2 圣骑士就是这么废掉真帧档的：主素材 222920 没有帧池，同一篇采用的另一条
    302741 有 18 张帧却没人去看，于是提示词里"铁律六·攻略页"整段不出现，
    模型只能给纯卡片，出图还花 1.0 元画了两张插画，玩家看不到一行游戏画面。
    """
    ids = []
    try:
        for x in json.loads((summary or {}).get("source_video_ids") or "[]"):
            ids.append(int(x))
    except (TypeError, ValueError):
        pass
    try:
        if summary.get("video_id"):
            ids.insert(0, int(summary["video_id"]))
    except (TypeError, ValueError, AttributeError):
        pass
    want, seen = [], set()
    for v in ids:
        if v and v not in seen:
            seen.add(v)
            want.append(v)
    if not want:
        return []
    try:
        vids = {int(v["id"]): v for v in content_store.get_videos_by_ids(DB_PATH, want)}
    except Exception as e:
        # 查库失败不等于"这些素材不存在"：报出来，别静默退回"没有真帧"那一档
        print("[guide] 取采用素材失败:", e, flush=True)
        return []
    out = []
    for vid in want:
        v = vids.get(vid) or {}
        sheet = str(v.get("source_sheet") or "").strip()
        # 带来源表出来：论坛帖／微信文章**根本没有视频可抽帧**，
        # 这和"老素材没抽过帧"是两件事，界面以前把两者混成一句"抽帧流程上线前采的"，
        # 还给了一个必然失败的「补画面」按钮（2026-10-29 号主报"看版面没有关键帧"抓到的）。
        out.append({"video_id": vid, "aweme_id": str(v.get("aweme_id") or "").strip(),
                    "title": v.get("title") or "", "sheet": sheet,
                    "is_video": supplements.is_video(sheet)})
    return out


def _guide_frames_for(summary, cfg, progress=None, may_judge=True):
    """这篇稿所有采用素材里"能当攻略证据的干净帧"，合起来给拆解层。

    三条口径：
      · 判帧要花钱 → 每条素材最多判 `GUIDE_JUDGE_CAP` 张、整组最多带 `GUIDE_FRAME_TOTAL_CAP` 张；
      · 合并后**全局重编号**（F1…Fn），模型只许点这个编号；每张留着自己是哪条素材的
        （`video_id`/`aweme_id`/`file` 路径里有），画面使用依据与回读校验都还指得回去；
      · 没有帧池（老素材、原片已删）就跳过那一条，不抛错——不能因为一条没图就把整条产线卡住。

    `may_judge=False`（出图那一步补图用）：只读已经判过的，一张都不新判——
    出图是 0 元那条路，不能在这里悄悄花判帧的钱。
    """
    try:
        from src import frame_pool
    except ImportError:
        import frame_pool
    merged = []
    for m in _guide_materials(summary):
        if len(merged) >= GUIDE_FRAME_TOTAL_CAP:
            break
        aid = m["aweme_id"]
        if not aid:
            continue
        try:
            meta = frame_pool.load(aid)
            if not meta:
                continue
            if not meta.get("checked") and may_judge:
                # 只有拆解那一步允许新判帧（花钱）；出图补图这一路不许顺手花这笔钱
                meta = frame_pool.ensure_judged(aid, cfg, limit=GUIDE_JUDGE_CAP,
                                                progress_cb=progress)
            # 没判过整池 ≠ 一张都没判过：`usable()` 只挑已判且干净的，照用
            take = frame_pool.usable(meta)[:GUIDE_JUDGE_CAP]
        except Exception as e:
            print("[guide] 素材 %s 帧池读取失败（跳过这一条）:" % m["video_id"], e)
            continue
        for it in take:
            if len(merged) >= GUIDE_FRAME_TOTAL_CAP:
                break
            merged.append(dict(it, video_id=m["video_id"], aweme_id=aid))
    for n, it in enumerate(merged, 1):
        it["i"] = n
    if merged:
        print("[guide] 这篇稿可用真帧 %d 张，来自素材 %s"
              % (len(merged), sorted({x["video_id"] for x in merged})), flush=True)
    return merged


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
    # 发布渠道（2026-10-13 渠道分叉）：只管装配与展示，拆解本身渠道无关；
    # 回存进 status 让重开弹窗时 pkHydrate 能恢复到上次的渠道
    publish_channel = str(data.get("publish_channel") or "toutiao").strip()[:10]
    if publish_channel not in ai_poster.PUBLISH_CHANNELS:
        publish_channel = "toutiao"
    pk_form = str(data.get("form") or "newspic").strip()[:10]
    text_mode = str(data.get("text_mode") or ai_poster.DEFAULT_TEXT_MODE).strip()[:10]
    # 有没有真图决定能不能推"满版大图 / 左右分栏"这类没图站不住的布局
    has_photo = bool(str(data.get("photo") or "").strip())
    # 用户已经在页签里定死的页数（点「换一套」时把当前页数带过来，模型才不会再拆回 2 页）
    try:
        page_hint = int(data.get("page_hint") or 0)
    except (TypeError, ValueError):
        page_hint = 0

    def fn():
        cfg = _fast_config()
        frames = _guide_frames_for(summary, cfg)
        _poster_status["progress"] = "LLM 拆解海报文案（模型：%s）..." % (cfg.get("model") or "?")
        plan, publish, bad, notes = ai_poster.build_text_plan(summary, cfg, theme=theme, title=title,
                                                              style_key=style_key, text_mode=text_mode,
                                                              has_photo=has_photo, page_hint=page_hint,
                                                              frames=frames)
        # 骨架稿不在这里拦：拆解本身就能判断这份素材有没有料，拦在这里会让人白等一次拆解。
        # 只把风险随结果一起回给前端，出图那一步再决定是否放行。
        warning = "；".join(ai_poster.draft_gate.draft_blockers(summary))
        _poster_status["plan"] = {"plan": plan, "publish": publish, "violations": bad,
                                  "notes": notes, "chars": ai_poster.plan_chars(plan),
                                  "publish_channel": publish_channel, "form": pk_form,
                                  # 整组都是攻略页时界面不该再摆"去豆包画无字底图"那一块：
                                  # 这一档的画面是素材真帧，传一张底图回来反而把真帧盖掉
                                  "guide_only": ai_poster.guide_only_plan(plan),
                                  "visual": ai_poster.lint_visual(plan),
                                  "model": cfg.get("model") or "",
                                  "draft_warning": warning,
                                  "prompt_text": (ai_poster.build_art_prompt(plan, ratio)
                                                  if text_mode == "typeset"
                                                  else ai_poster.build_prompt_text(plan, ratio)),
                                  # 每页一份、自包含的"拿去豆包手动出图"提示词（0 元那条路）
                                  "manual_prompts": ai_poster.manual_prompts_for(plan, ratio, text_mode)}
        _poster_save_plan(summary["id"], _poster_status["plan"])
        _poster_status["progress"] = ("✅ 文案已拆解（合计 %d 字 · 模型 %s）"
                                      % (ai_poster.plan_chars(plan), cfg.get("model") or "?")
                                      + ("；自动压缩：" + "、".join(notes) if notes else "")
                                      + ("，⚠️ " + "；".join(bad) if bad else ""))

    if not _poster_start("plan", fn, summary["id"]):
        return jsonify({"error": "已有海报任务正在运行"}), 400
    return jsonify({"status": "started", "summary_id": summary["id"],
                    "task_id": _poster_status["task_id"]})


def _merge_page_into_group(group, idx, page):
    """把重拆回来的那一页并回整组文案，返回新的整组（没有整组可并就返回 None）。

    为什么在这儿并：`/api/poster/plan/page` 只交一页回来，前端那一份 `_pkPages` 才是完整结构，
    可前端一关就没了——后台这份状态是「重新打开弹窗还能看见上次拆的文案」唯一的来源。
    """
    if not isinstance(group, dict):
        return None
    merged = dict(group)
    gp = [dict(x) for x in (merged.get("pages") or []) if isinstance(x, dict)]
    if not gp:
        gp = [merged]
    if not (1 <= idx <= len(gp)):
        return merged
    gp[idx - 1] = page
    merged["pages"] = gp
    if idx == 1:
        # 顶层那几个键一直是"第 1 页"的镜像（列表页、发布取图、回读校验读的都是它们），不能留旧值
        merged["title"] = page.get("title") or merged.get("title")
        merged["subtitle"] = page.get("subtitle") or merged.get("subtitle")
        merged["cards"] = page.get("cards") or merged.get("cards")
        merged["scene"] = page.get("scene") or merged.get("scene")
    return merged


@app.route("/api/poster/plan/page", methods=["POST"])
def api_poster_plan_page():
    """只重拆某一页：整组「换一套」会把人手动改过的别的页一起冲掉，这条是"就这页不满意"的出口。

    前端把当前整组结构一起传过来，后端只交第 k 页给模型，并把别的页已讲的要点作为
    "别重复"清单塞进提示词；回来的也只有一页，别的页由前端原样留着。
    """
    data = request.get_json(force=True) or {}
    summary = _poster_get_summary(data.get("summary_id"))
    if not summary:
        return jsonify({"error": "整合稿不存在"}), 404
    pages = [p for p in (data.get("pages") or []) if isinstance(p, dict)]
    if not pages:
        return jsonify({"error": "这一组还没有页，先点「① 拆文案」"}), 400
    try:
        idx = int(data.get("page_index") or 1)
    except (TypeError, ValueError):
        idx = 1
    if idx < 1 or idx > len(pages):
        return jsonify({"error": "页码超出范围（1-%d）" % len(pages)}), 400
    theme = str(data.get("theme") or "").strip()[:30]
    style_key = str(data.get("style_key") or "").strip()[:20]
    text_mode = str(data.get("text_mode") or ai_poster.DEFAULT_TEXT_MODE).strip()[:10]
    has_photo = bool(str(data.get("photo") or "").strip())
    spine = str(data.get("spine") or "").strip()[:40]

    def fn():
        cfg = _fast_config()
        # 这一页重拆也要带着真帧清单：不带给它，模型不敢给 photos 编号，
        # 重拆一次就把原来那页的证据图悄悄丢了（攻略页最伤的一种"字还在图没了"）
        frames = _guide_frames_for(summary, cfg)
        _poster_status["progress"] = "只重拆第 %d 页（模型：%s）..." % (idx, cfg.get("model") or "?")
        page, bad, notes = ai_poster.build_page_plan(
            summary, cfg, idx, pages, spine=spine, theme=theme, style_key=style_key,
            text_mode=text_mode, has_photo=has_photo, frames=frames)
        prev = _poster_status.get("plan") or {}
        merged = _merge_page_into_group(prev.get("plan"), idx, page)
        _poster_status["plan"] = {"page": page, "index": idx, "violations": bad,
                                  "notes": notes, "chars": ai_poster.plan_chars(page),
                                  "model": cfg.get("model") or "",
                                  # 整组那份就地换掉这一页一起留着：只重拆一页不该把"上次拆好的
                                  # 文案"冲成只剩一页，否则关掉弹窗再打开什么都没了（只能重拆花钱）
                                  "plan": merged, "publish": prev.get("publish"),
                                  # 渠道同理：单页重拆不冲掉上次选的发布渠道（重开弹窗要恢复）
                                  "publish_channel": prev.get("publish_channel") or "toutiao",
                                  "form": prev.get("form") or "",
                                  "guide_only": ai_poster.guide_only_plan(merged) if merged else False,
                                  "manual_prompts": prev.get("manual_prompts")}
        _poster_status["progress"] = ("✅ 第 %d 页已重拆（%d 字 · 模型 %s）"
                                      % (idx, ai_poster.plan_chars(page), cfg.get("model") or "?")
                                      + ("；自动压缩：" + "、".join(notes) if notes else "")
                                      + ("，⚠️ " + "；".join(bad) if bad else ""))

    if not _poster_start("page", fn, summary["id"]):
        return jsonify({"error": "已有海报任务正在运行"}), 400
    return jsonify({"status": "started", "summary_id": summary["id"], "page_index": idx,
                    "task_id": _poster_status["task_id"]})


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
    # 发布渠道与公众号形态：渠道决定配文尾注/图上脚注/清单，形态仅公众号有意义
    publish_channel = str(data.get("publish_channel") or "toutiao").strip()[:10]
    if publish_channel not in ai_poster.PUBLISH_CHANNELS:
        publish_channel = "toutiao"
    pk_form = str(data.get("form") or "newspic").strip()[:10]
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else None
    publish = data.get("publish") if isinstance(data.get("publish"), dict) else None
    force = bool(data.get("force"))      # 骨架稿人工复核后的显式放行
    # 事实闸放行必须带**逐条凭据**（2026-10-21 Codex P1）：这里不再只收一个 true。
    fact_evidence = data.get("fact_evidence")
    if not isinstance(fact_evidence, list):
        fact_evidence = []
    fact_checked_by = str(data.get("fact_checked_by") or "").strip()[:40]
    do_review = data.get("review") is not False   # 默认做视觉评审，前端可关掉省十几秒
    text_mode = str(data.get("text_mode") or ai_poster.DEFAULT_TEXT_MODE).strip()[:10]
    try:
        copies = int(data.get("copies") or 1)
    except (TypeError, ValueError):
        copies = 1
    try:
        photo = _photo_read(data.get("photo"))     # 上传图坏了要在提交前就报错，别让人白等一轮
    except ai_poster.PosterError as e:
        return jsonify({"error": str(e)}), 400
    try:
        # 手动底图（在豆包出的无字底图）：一页一张，引用同样只认 _photo 目录里我们发出去的名字
        bases = [b for b in (_photo_read(r) for r in (data.get("bases") or [])[:ai_poster.PAGE_MAX]) if b]
    except ai_poster.PosterError as e:
        return jsonify({"error": str(e)}), 400
    try:
        clicks = int(data.get("preview_clicks") or 0)   # 采纳率要用：平均点几次预览才满意
    except (TypeError, ValueError):
        clicks = 0

    def fn():
        def cb(msg):
            _poster_status["progress"] = msg
        # 编辑证据链按整理稿倒查（表/模块都是我这轮的，函数内 import 少碰别人那片导入区）
        try:
            from src import guide_provenance
            prov = guide_provenance.load_for_summary(DB_PATH, summary["id"])
        except Exception:
            prov = {}
        # 行动依据闸要比对原转写：多源稿不能只看主素材那一条，把所有来源的转写拼起来
        asr_text = ""
        try:
            ids = [int(x) for x in (json.loads(summary.get("source_video_ids") or "[]") or [])]
            rows = content_store.get_videos_by_ids(DB_PATH, ids or [summary["video_id"]])
            asr_text = "\n".join(str(r.get("transcript") or "") for r in rows)
        except Exception:
            asr_text = str(summary.get("transcript") or "")
        # 出图前那道核对会把"太虚/字幕压中间"的那张拦掉，拦完这一页不能就空着
        # （号主 10-23：② 看版面有图、③ 出来第 3 页没图）。这里把可用帧名单带进出图层，
        # 让它当场换一张再核一遍。**只读已判过的，不新判帧——这一档 0 元。**
        frame_items = _guide_frames_for(summary, _fast_config(), may_judge=False)
        manifest = ai_poster.generate_poster(summary, _fast_config(), theme=theme, title=title,
                                             ratio=ratio, progress_cb=cb, plan=plan,
                                             publish=publish, force=force, copies=copies,
                                             do_review=do_review, text_mode=text_mode,
                                             photo=photo, preview_clicks=clicks,
                                             bases=bases or None,
                                             publish_channel=publish_channel, form=pk_form,
                                             fact_evidence=fact_evidence,
                                             fact_checked_by=fact_checked_by,
                                             fact_ctx=_fact_ctx(summary),
                                             provenance=prov, asr_text=asr_text,
                                             frame_items=frame_items)
        v = manifest.get("verify") or {}
        rv = manifest.get("review") or {}
        ts_chk = manifest.get("typeset") or {}
        errs = [i for i in (manifest.get("lint") or []) if i.startswith("error")]
        _poster_status["package"] = {"id": manifest["id"], "title": manifest["title"],
                                     "url": manifest["url"], "chars": manifest["text_chars"],
                                     "copies": manifest.get("copies") or 1,
                                     "verify_ok": v.get("ok"), "review": rv,
                                     "text_mode": manifest.get("text_mode"),
                                     "photo": manifest.get("photo") or "",
                                     "photo_frac": manifest.get("photo_frac"),
                                     "cost": manifest.get("cost_yuan_estimate"),
                                     "typeset_problems": ts_chk.get("problems") or [],
                                     "lint": manifest.get("lint") or [],
                                     # 放行不等于能发：界面按这个字段决定写"已就绪"还是"不可发布"
                                     "publishable": manifest.get("publishable", True),
                                     # 证据链缺登记：单独一个旗子，不再并进 publishable
                                     "needs_evidence": bool(manifest.get("needs_evidence")),
                                     "frames_refilled": manifest.get("frames_refilled") or [],
                                     "fact_gate": manifest.get("fact_gate") or {},
                                     "provenance": manifest.get("provenance") or {},
                                     "action_gate": manifest.get("action_gate") or {},
                                     "photos_unjudged": manifest.get("photos_unjudged") or []}
        _poster_status["plan"] = {"plan": manifest["plan"], "publish": manifest["publish"],
                                  "violations": errs, "chars": manifest["text_chars"],
                                  "prompt_text": manifest["prompt_text"],
                                  "publish_channel": manifest.get("publish_channel") or "toutiao",
                                  "form": manifest.get("form") or ""}
        # 出图后这一份（含换过的那几张画面）才是真印出去的，备份要跟着更新
        _poster_save_plan(summary["id"], _poster_status["plan"])
        score = ("，视觉评审 %s/%s 分" % (rv.get("score"), rv.get("full"))) if rv.get("score") else ""
        # 「图出了」和「能发了」是两件事：待核事实没凭据、或编辑证据链缺项时，
        # 末尾这句必须说清不可发布，否则人看见 ✅ 就以为过了（2026-10-21 Codex P1 + 原创增量单）
        reasons = []
        if not manifest.get("publishable", True):
            fg_hard = len((manifest.get("fact_gate") or {}).get("hard") or [])
            if fg_hard:
                reasons.append("%d 条待核事实没有逐条凭据" % fg_hard)
            act_of = len((manifest.get("action_gate") or {}).get("overflow") or [])
            if act_of:
                reasons.append("%d 条建议的数值/选项名在原转写里定位不到（待回听/待游戏核）" % act_of)
        block = ("，⛔ 不可发布：" + "；".join(reasons)) if reasons else ""
        # 编辑证据链缺登记：号主 10-23 拍板放宽，不再拦在最后一步（图能出、也能发），
        # 但那句话必须留在结果里——放宽不等于抹掉，发之前那三行还是得补。
        prov_gaps = len((manifest.get("provenance") or {}).get("gaps") or [])
        ev = ("，⚠️ 编辑证据链缺 %d 项（%s）：这张能发，但发前把「问题/新增/依据」和"
              "画面使用依据补上" % (prov_gaps, (manifest.get("provenance") or {}).get("label") or "")) \
            if prov_gaps else ""
        # 出图前核对换掉/补不出的那几页要点名，否则"② 看版面有图、③ 出来没图"又成了玄学
        rf = ["第 %d 页→%s" % (r.get("page"), r.get("frame") or "补不出")
              for r in (manifest.get("frames_refilled") or [])]
        rfb = ("，🔁 出图前核对换过画面：" + "、".join(rf)) if rf else ""
        if manifest.get("text_mode") == "typeset":
            warn = ("，⚠️ 版面提示：" + "；".join(ts_chk.get("problems") or [])) if ts_chk.get("problems") else ""
            _poster_status["progress"] = "✅ 海报已生成（%d 字，字由程序排版、不存在错字）%s%s%s%s" % (
                manifest["text_chars"], score, warn, rfb, block + ev)
        else:
            _poster_status["progress"] = "✅ 海报已生成（%d 字）%s%s%s%s" % (
                manifest["text_chars"], score,
                "" if v.get("ok") else "，⚠️ 图面文字校验未通过，发布前人眼复核",
                rfb, block + ev)

    if not _poster_start("poster", fn, summary["id"]):
        return jsonify({"error": "已有海报任务正在运行"}), 400
    return jsonify({"status": "started", "summary_id": summary["id"],
                    "task_id": _poster_status["task_id"]})


@app.route("/api/poster/publish", methods=["GET", "POST"])
def api_poster_publish():
    """海报这一篇的发布状态：在海报里就地登记「已发布」/撤销，**写的还是排产那本台账**。

    为什么要这个入口（号主 10-23：出图、发出去、改状态以前要跑两个页面）：
    登记动作只在一处点，后端按这篇稿挂在哪个槽上决定怎么写——
      · 有排产槽号 → `plan_series.register_slot`：排产表那一格同时变「已发布」，夜间匹配也不再给它出候选；
      · 没槽号（独立稿、老素材直接起的稿）→ 记一行 `slot_key=''` 的台账，键里带稿 id。
    两条都只写 `publish_stats` 这一张表，所以运营页立刻看得到，不另造第二份发布真相。
    校验（标题必填、日期格式、没链接要写原因、撤销要写原因）全在后端那一份里。
    """
    if request.method == "GET":
        try:
            sid = int(request.args.get("summary_id") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "summary_id 要是一个数字"}), 400
        if not sid:
            return jsonify({"error": "缺 summary_id"}), 400
        try:
            out = plan_series.poster_publish_state(DB_PATH, sid,
                                                   channel=request.args.get("channel") or "toutiao")
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify(out)

    data = request.get_json(silent=True) or {}
    try:
        sid = int(data.get("summary_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "summary_id 要是一个数字"}), 400
    if not sid:
        return jsonify({"error": "缺 summary_id"}), 400
    action = str(data.get("action") or "register").strip()
    ch = str(data.get("channel") or "toutiao")
    try:
        with _db_lock:
            if action == "register":
                out = plan_series.poster_publish(DB_PATH, sid, data)
            elif action == "revoke":
                out = plan_series.poster_revoke(DB_PATH, sid, str(data.get("ref_id") or ""),
                                                str(data.get("reason") or ""),
                                                str(data.get("by") or "AI 海报"), channel=ch)
            else:
                return jsonify({"error": "action 只能是 register / revoke，收到「%s」" % action}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e), "retry": True}), 500
    return jsonify(out)


# ── 待核事实台账与最终放行（2026-10-29 小红书反例后加的）──────────────────────
# 三件事各管一段：台账管"上一版标过的别丢"，证据门管"没核过的机制说法别当结论发"，
# 放行记录管"你签的是哪一版"。判据全在 src/risk_ledger.py 与 src/fact_gate.py，这里只做接口。


@app.route("/api/fact/risk")
def api_fact_risk():
    """这一篇的素材上还挂着哪些待核事实：台账里开着的 + 解除过的 + 稿子里写了几条。"""
    try:
        sid = int(request.args.get("summary_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    summary = _poster_get_summary(sid)
    if not summary:
        return jsonify({"error": "整理稿不存在"}), 404
    ctx = _fact_ctx(summary)
    return jsonify({"materials": ctx["materials"], "open": ctx["open_items"],
                    "resolved": ctx["resolved"],
                    "in_draft": len(fact_gate.risk_items(summary.get("content") or "")),
                    "ledger": len(ctx["open_items"])})


@app.route("/api/fact/risk/resolve", methods=["POST"])
def api_fact_risk_resolve():
    """解除一条待核事实。只认可定位证据：官方原句，或写明版本与日期的本人实测。

    证据不合格就**一条都不解除**（不部分生效）——列了核实点不等于完成核实，
    这条规矩跟 `fact_gate.check_override` 一致。
    """
    data = request.get_json(force=True) or {}
    refs = data.get("ids") or []
    if not refs:
        return jsonify({"ok": False, "missing": ["没点名要解除哪一条"]}), 400
    out = risk_ledger.resolve(DB_PATH, refs, data.get("source") or "",
                              data.get("result") or "", data.get("by") or "")
    return jsonify(out), (200 if out.get("ok") else 400)


def _poster_manifest_path(summary_id):
    return OUTPUT_DIR / "poster" / str(int(summary_id)) / "manifest.json"


@app.route("/api/poster/review")
def api_poster_review_status():
    """这一版成品有没有被最终放行过（改稿后旧放行自动作废）。"""
    try:
        sid = int(request.args.get("summary_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    chan = str(request.args.get("channel") or "toutiao")
    fp = _poster_manifest_path(sid)
    if not fp.exists():
        return jsonify({"error": "这一版还没有成品"}), 404
    manifest = json.loads(fp.read_text(encoding="utf-8"))
    h = manifest.get("package_hash") or ""
    st = release_review.status(str(fp.parent), chan, h)
    return jsonify(dict(st, package_hash=h, channel=chan,
                        checklist=release_review.checklist_hint(chan)))


@app.route("/api/poster/review", methods=["POST"])
def api_poster_review_sign():
    """签一次放行：绑的就是刚才看到的那一版哈希。

    只记"谁、什么时候、放行哪一版"，不做逐项打分——号主自己说审稿判断他也没把握，
    那就不拿伪判据冒充审核；能机器判的只有"改过就没有效"。
    """
    data = request.get_json(force=True) or {}
    try:
        sid = int(data.get("summary_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "summary_id 要是一个整数"}), 400
    chan = str(data.get("channel") or "toutiao")
    fp = _poster_manifest_path(sid)
    if not fp.exists():
        return jsonify({"error": "这一版还没有成品，没什么可放行的"}), 404
    manifest = json.loads(fp.read_text(encoding="utf-8"))
    h = manifest.get("package_hash") or ""
    out = release_review.record(str(fp.parent), chan, h, data.get("reviewer") or "",
                                data.get("note") or "")
    if not out.get("ok"):
        return jsonify({"error": out.get("error") or "放行没记上"}), 400
    manifest["release_review"] = dict(
        release_review.status(str(fp.parent), chan, h),
        checklist=release_review.checklist_hint(chan))
    if chan == "xhs" and manifest.get("publishable") is not False:
        pass                                   # 出图时的硬伤仍然算数，这里只补"已放行"这件事
    fp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "status": manifest["release_review"], "package_hash": h})


@app.route("/api/poster/preview", methods=["POST"])
def api_poster_preview():
    """版面预览：占位底图 + 真排版，当场返回，不调任何付费接口。

    故意不做字数拦截——预览的意义就是让人看见"这么写版面会爆"，
    所以越界照样出图，只把版面自检的问题原样回给前端。
    """
    data = request.get_json(force=True) or {}
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else None
    if not plan or not ai_poster.plan_has_text(plan):
        return jsonify({"error": "还没有可预览的文案，先点「① AI 拆解文案」"}), 400
    ratio = str(data.get("ratio") or "9:16").strip()[:5]
    # 草稿预览：派生规则只有 normalize() 一份，前端只传表单原值
    if isinstance(data.get("draft"), dict) and data.get("draft"):
        from src import poster_breakdown
        plan["_skin"] = poster_breakdown.normalize(data["draft"])["skin"]
    try:
        photo = _photo_read(data.get("photo"))
        out_dir = OUTPUT_DIR / "poster" / "_preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        # 多页就整组一起排（一页一张）：换页数、换每页布局都要能当场看到，
        # 这一步全是代码排版，一张都不花钱
        items = []
        for idx, (page_plan, png, chk) in enumerate(
                ai_poster.preview_pages(plan, ratio, photo=photo), 1):
            key = hashlib.sha1(png).hexdigest()[:16]
            (out_dir / (key + ".png")).write_bytes(png)
            items.append({"page": idx, "url": "/media/poster/_preview/%s.png" % key,
                          "check": chk, "chars": ai_poster.plan_chars(page_plan),
                          "layout": chk.get("layout"), "layout_label": chk.get("layout_label"),
                          "role": page_plan.get("role") or "",
                          "cards": len(page_plan.get("cards") or [])})
        _prune_previews(out_dir)
        first = items[0]
        return jsonify({"url": first["url"], "check": first["check"],
                        "chars": ai_poster.plan_chars(plan),
                        "page_count": len(items), "pages": items,
                        "cost_yuan": 0})
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 500


def _prune_previews(out_dir, keep=48):
    """预览图按内容哈希存，改一次文案就多一张；留最近 24 张，其余删掉。
    这些是纯中间产物，攒在 output/ 里没人看也没人清。多页一次点三张，所以留得多些。"""
    try:
        files = sorted(out_dir.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[keep:]:
            f.unlink()
    except Exception:
        pass


# ── 海报配图：用户上传的真实图片当主视觉（坐骑/角色/装备这类"必须长这样"的内容）──
# 这条分支不调出图模型，所以 0 元；排版引擎负责圆角、压暗和让位。
PHOTO_DIR = OUTPUT_DIR / "poster" / "_photo"
PHOTO_MAX_BYTES = 8 * 1024 * 1024
PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_PHOTO_REF = re.compile(r"^[0-9a-f]{10}\.png$")


def _photo_read(ref):
    """把前端回传的配图引用换成图片字节。

    只认 _photo 目录下我们上传时生成的那串名字：不能让前端传什么路径就读什么文件。
    """
    name = os.path.basename(str(ref or "").strip())
    if not name:
        return None
    if not _PHOTO_REF.match(name):
        raise ai_poster.PosterError("配图引用不合法，请重新上传")
    p = PHOTO_DIR / name
    if not p.exists():
        raise ai_poster.PosterError("配图已经不在服务器上了（临时目录只留最近 24 张），重新传一张")
    return p.read_bytes()


@app.route("/api/poster/photo", methods=["POST"])
def api_poster_photo():
    """收一张配图：校验能解码、够清晰，统一转成 PNG 存进临时目录。

    转 PNG 是为了把 gif 取首帧、webp 归一、EXIF 旋转一次性处理掉——排版引擎按像素算版面，
    带旋转信息的 jpg 贴出来会歪。太小的图明确拒绝：贴到 928 宽的海报上会糊成一片。
    """
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "没收到图片"}), 400
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in PHOTO_EXT:
        return jsonify({"error": "只支持 jpg / png / webp / gif"}), 400
    blob = f.read()
    if len(blob) > PHOTO_MAX_BYTES:
        return jsonify({"error": "图片超过 8MB，压一下再传"}), 400
    try:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(blob)))
        w, h = im.size
        if w < 260 or h < 260:
            return jsonify({"error": "图太小（%d×%d），贴到海报上会糊，换一张大一点的" % (w, h)}), 400
        out = io.BytesIO()
        im.convert("RGB").save(out, format="PNG")
        png = out.getvalue()
    except Exception as e:
        return jsonify({"error": "这张图读不出来：%s" % str(e)[:120]}), 400
    name = uuid.uuid4().hex[:10] + ".png"
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    (PHOTO_DIR / name).write_bytes(png)
    _prune_previews(PHOTO_DIR, keep=24)
    return jsonify({"ref": name, "url": "/media/poster/_photo/%s" % name,
                    "w": w, "h": h, "bytes": len(png)})


@app.route("/api/poster/base", methods=["POST"])
def api_poster_base():
    """收一张"在豆包手动生成的无字底图"，回传到这一次出图里用（0 元那条路的回程）。

    和配图的区别：配图是当主视觉贴在一块区域里，底图要铺满整张画布再往上排字，
    所以这里卡两条硬指标——短边够大（排版按像素算，太小一放就糊）、
    长宽比跟目标比例对得上（差太多会被裁掉一大块画面，那种成品不如不出）。
    """
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "没收到图片"}), 400
    ratio = str(request.form.get("ratio") or "9:16").strip()[:5]
    try:
        tw, th = (ai_poster.SIZES.get(ratio) or ai_poster.SIZES["9:16"]).split("*")
        tw, th = int(tw), int(th)
    except (ValueError, AttributeError):
        return jsonify({"error": "比例不认识：%s" % ratio}), 400
    blob = f.read()
    if len(blob) > PHOTO_MAX_BYTES:
        return jsonify({"error": "图片超过 8MB，压一下再传"}), 400
    try:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(blob)))
        w, h = im.size
    except Exception as e:
        return jsonify({"error": "这张图读不出来：%s" % str(e)[:120]}), 400
    if min(w, h) < 700:
        return jsonify({"error": "底图太小（%d×%d），短边至少要 700，"
                                 "豆包导出时选大一点的尺寸" % (w, h)}), 400
    ar, tar = float(w) / h, float(tw) / th
    if abs(ar - tar) / tar > 0.15:
        return jsonify({"error": "比例对不上：这张是 %.2f，海报要 %.2f（%s）。"
                                 "差太多贴上去会裁掉一大块，回豆包按 %s 重新生成一张"
                         % (ar, tar, ratio, ratio)}), 400
    out = io.BytesIO()
    im.convert("RGB").save(out, format="PNG")
    png = out.getvalue()
    name = uuid.uuid4().hex[:10] + ".png"
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    (PHOTO_DIR / name).write_bytes(png)
    _prune_previews(PHOTO_DIR, keep=24)
    return jsonify({"ref": name, "url": "/media/poster/_photo/%s" % name,
                    "w": w, "h": h, "bytes": len(png)})


@app.route("/api/poster/status")
def api_poster_status():
    """进度与上一次结果。带 `summary_id` 时：内存里没有这一条（重启过、或被别的任务顶掉）
    就从成品包里的备份接回来——不然一次重启就把人花过钱等过的拆解吃掉。"""
    st = dict(_poster_status)
    sid = request.args.get("summary_id")
    try:
        sid = int(sid) if sid else 0
    except (TypeError, ValueError):
        sid = 0
    if sid and not st.get("running") and st.get("summary_id") != sid:
        saved = _poster_load_plan(sid)
        if saved:
            st["summary_id"] = sid
            st["plan"] = saved
            st["package"] = None        # 那份成品属于另一条，不许贴到这里（串号那次就是这毛病）
            st["error"] = ""
            st["done"] = True
            st["progress"] = "这条的文案是从成品包里的备份接回来的（服务重启过也能接着改）"
    return jsonify(st)


@app.route("/api/poster/frames")
def api_poster_frames():
    """这条素材有没有"能当攻略证据的干净画面"。只读 frames.json，不判帧、不花钱。

    为什么要单独问一次：攻略页这条 0 元的路以前在界面上完全隐形——他不知道哪条素材
    有可用画面，也就永远不会去选那个版式。这里把数报出来，弹窗才提得出那句建议。
    """
    try:
        sid = int(request.args.get("summary_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    summary = _poster_get_summary(sid)
    if not summary:
        return jsonify({"error": "整合稿不存在"}), 404
    try:
        from src import frame_pool
    except ImportError:
        import frame_pool
    # 逐条采用素材各读一遍自己的帧池（**只读，不判帧、不花钱**）。
    # 以前这里只看主素材那一条：整合稿的主视频常常已经删了原片 → 报"0 张能用"，
    # 而同一篇里另一条明明有 18 张帧——界面上那句"没有可用画面"就是这么骗人的。
    mats, all_items = [], []
    total = judged = usable_n = 0
    drop_kinds = {}
    checked_any = False
    primary_dur = 0
    for k, m in enumerate(_guide_materials(summary)):
        aid = m["aweme_id"]
        meta = frame_pool.load(aid) if aid else {}
        frames = (meta or {}).get("frames") or []
        us = frame_pool.usable(meta)
        drops = frame_pool.dropped(meta)
        for (w, _c), n in drops.items():
            drop_kinds[w] = drop_kinds.get(w, 0) + n
        for it in us:
            p = str(it.get("file") or "")
            nm = os.path.basename(p)
            # URL 一律从**真实路径**推：裁过边的成品落在 raw/ 里（`v_` 前缀）、没裁的就直接是 raw 原帧，
            # 只有极少数落在 clean/。以前这里写死 `frames/<aid>/clean/<文件名>`，
            # 于是画面条整排 404 碎图（2026-10-22 走查抓到：10 张里 0 张打得开）。
            rel = ""
            try:
                rel = os.path.relpath(p, str(OUTPUT_DIR)).replace(os.sep, "/")
            except (TypeError, ValueError):
                rel = ""
            url = "" if rel.startswith("..") else ("/media/" + rel)
            all_items.append({"i": it.get("i"), "ts": it.get("ts") or "",
                              "subject": (it.get("subject") or "").strip(),
                              "video_id": m["video_id"],
                              # 名字带上素材 id：两条素材都可能有 v_f_001.jpg，
                              # 只按文件名对"用在第几页"会张冠李戴（前端按 …/frames/<aid>/<file> 对）
                              "name": "%s/%s" % (aid, nm),
                              "url": url})
        total += len(frames)
        judged += len([f for f in frames if f.get("checked")])
        usable_n += len(us)
        checked_any = checked_any or bool((meta or {}).get("checked"))
        if k == 0:
            primary_dur = (meta or {}).get("duration") or 0
        mats.append({"video_id": m["video_id"], "aweme_id": aid, "title": m["title"][:40],
                     "sheet": m.get("sheet") or "", "is_video": bool(m.get("is_video")),
                     "has_pool": bool(meta), "total": len(frames),
                     "judged": len([f for f in frames if f.get("checked")]),
                     "usable": len(us), "checked": bool((meta or {}).get("checked"))})
    job = _frames_job(sid)
    if job.get("done") and not job.get("running") and not job.get("error"):
        job = {}
    primary = (mats or [{}])[0]
    # 「一条帧都没有」有两种完全不同的原因，界面必须分得开：
    #   A 素材本来就不是视频（NGA 帖／微信文章）→ 没有视频可下载，「补画面」点了必失败；
    #   B 是视频但没抽过帧/原片删了 → 才谈得上"补画面 · 不花钱"。
    # 以前这里只回一个 usable=0，界面统一说成"抽帧流程上线前采的"并给按钮（2026-10-29 抓到）。
    non_video = [m for m in mats if not m["is_video"]]
    return jsonify({"aweme_id": primary.get("aweme_id") or "",
                    "materials": mats,
                    "total": total, "usable": usable_n,
                    "no_video_source": bool(mats) and len(non_video) == len(mats),
                    "non_video_sheets": sorted({m["sheet"] for m in non_video if m["sheet"]}),
                    "items": all_items[:24],
                    "judged": judged,
                    # 扔掉的那些为什么不能用（整帧是卡 / 花字在中间 / 裁完回读还有字）——
                    # 号主看到"18 张只剩 3 张"要能一眼看懂，否则就像程序坏了
                    "dropped": [{"why": w, "n": n} for w, n in
                                sorted(drop_kinds.items(), key=lambda kv: -kv[1])][:3],
                    "checked": checked_any,
                    "duration": primary_dur,
                    "building": bool(job.get("running")),
                    "progress": job.get("progress") or "",
                    "build_error": job.get("error") or ""})


_frames_jobs = {}          # {summary_id: {"running","progress","done","error","usable","total"}}
_frames_lock = threading.Lock()


def _frames_job(sid):
    with _frames_lock:
        return dict(_frames_jobs.get(sid) or {})


def _frames_set(sid, **kw):
    with _frames_lock:
        j = _frames_jobs.setdefault(sid, {"running": False, "progress": "", "done": False,
                                          "error": "", "usable": 0, "total": 0})
        j.update(kw)


def _frames_resolve_url(video, aweme_id, sid):
    """取这条视频的可下载地址：**先读缓存，再走真实浏览器，最后才试解析器**。

    顺序是 2026-10-12 实测出来的，不是随手排的：
      · Python 那条 `douyin_parse` 解析器**已经彻底过不了 Argus**——cookie 补到 68 个键、
        必需键全齐，接口照样回 `Uifid Not Found`；把 `uifid` 当 URL 参数补上，报错就变成
        `Signature Not Found`（下一步要 msToken，那是页面 JS 现算的、几十分钟就换）。
        也就是说这条路不是"cookie 没配好"，是抖音换了规则，补 cookie 补不出来。
      · 真实浏览器那条一路 `browser_fetch_video_detail` 实测能拿到 nwm 直链（今天成过三次），
        它抖不抖取决于机器忙不忙——自动同步批量抓的时候容易等不到 detail 那个 XHR，
        所以这里最多试 5 次、每次报进度，**并且每一发之前先给自动同步排队**，
        而不是一次定生死。
      · 解析器留在最后当"顺手捡一下"：只试一次、不退避，成了省时间，不成也不拖。
    """
    cached = _load_cached_nwm_url(aweme_id)
    if cached:
        _frames_set(sid, progress="用上了缓存里的直链（没走网络）")
        return cached
    try:
        from src.browser_fetch import browser_fetch_video_detail
    except Exception as be:
        print("[frames] 浏览器模块都进不去:", be)
        browser_fetch_video_detail = None
    page = (video.get("video_url") or video.get("source_url") or "").strip() \
        or "https://www.douyin.com/video/%s" % aweme_id
    url = ""
    if browser_fetch_video_detail:
        for attempt in range(1, 6):
            # 自动同步在批量抓主页时，这台机器上会同时开好几个 Chromium，
            # 我们这一发的 detail XHR 就容易被挤到 15 秒超时——所以先排队，别硬抢
            waited = _frames_wait_for_sync(sid)
            _frames_set(sid, progress="真实浏览器取直链（第 %d 次%s）..." % (
                attempt, "，刚等完同步" if waited else ""))
            try:
                info = browser_fetch_video_detail(page) or {}
            except Exception as be:
                print("[frames] 浏览器第 %d 次异常:" % attempt, be)
                info = {}
            url = (info.get("nwm_url") or "").strip()
            if url:
                break
            time.sleep(3 * attempt)      # 3/6/9/12 秒，越往后越给同步让路
    if not url:
        _frames_set(sid, progress="浏览器没拿到，再试一次本地解析器...")
        url = _resolve_download_url(dict(video, aweme_id=aweme_id), retries=1, backoff=1.0)
    if url:
        return url
    _frames_set(sid, running=False, done=True,
                error="取不到下载地址：浏览器那一路试了 5 次都没等到 aweme/detail 的响应。"
                      "本地解析器现在必被抖音风控（缺 msToken，那是页面 JS 现算的），"
                      "不是 cookie 的问题——稍后再点一次通常就成了（一次约 30 秒）")
    return ""


def _frames_wait_for_sync(sid, max_wait=75):
    """自动同步正在批量抓主页时先等它跑完：两边同抢 Chromium 谁都容易超时。
    最多等 max_wait 秒，超时照样放行（不能把补画面无限卡住）。返回等了多久（秒）。"""
    t0 = time.time()
    while _sub_status.get("running") and time.time() - t0 < max_wait:
        _frames_set(sid, progress="自动同步正在抓主页，先让开（已等 %d 秒）..." % int(time.time() - t0))
        time.sleep(5)
    return int(time.time() - t0)


def _frames_judge(sid, aweme_id, cfg, limit=8):
    """判帧这一步单独抽出来：花钱的活只在两种时候干——号主点「先看图确认」，或者真要出图。"""
    from src import frame_pool
    _frames_set(sid, running=True, progress="看图确认 %d 张里哪些还带后期字..." % limit)
    meta = frame_pool.ensure_judged(
        aweme_id, cfg, limit=limit,
        progress_cb=lambda m: _frames_set(sid, progress=m)) or {}
    usable = len(frame_pool.usable(meta))
    total = len(meta.get("frames") or [])
    _frames_set(sid, running=False, done=True, usable=usable, total=total,
                progress="看图完成：%d/%d 张能用（扔掉的 %d 张原因见下面那行）" % (
                    usable, total, total - usable))
    return meta


def _frames_worker(sid, video, aweme_id, cfg, judge=False):
    """下载 → 抽帧 → 逐帧裁掉后期压上去的字。**默认不判帧**（号主 10-12 定的口径：
    判帧只补真要发的那几条，别在"先把画面备着"这一步花钱）。judge=True 才走看图那一问。"""
    import tempfile
    import shutil as _shutil
    from src import frame_pool, material_store
    tmp = Path(tempfile.mkdtemp(prefix="v2t_pool_"))
    mp4 = tmp / "video.mp4"
    try:
        _frames_set(sid, running=True, progress="准备取视频直链...")
        url = _frames_resolve_url(video, aweme_id, sid)
        if not url:
            return                          # 失败原因 _frames_resolve_url 已经写进 job 了
        _frames_set(sid, progress="下载视频...")
        if not material_store.download_video(url, mp4):
            _frames_set(sid, running=False, done=True, error="视频下载失败（链接可能已过期）")
            return
        _frames_set(sid, progress="抽帧并挑掉后期压上去的字...")
        meta = frame_pool.build(mp4, aweme_id, progress_cb=lambda m: _frames_set(sid, progress=m))
        total = len(meta.get("frames") or [])
        if judge:
            _frames_judge(sid, aweme_id, cfg)
        else:
            # 零成本那一档只报"抽了几张"，能用几张要等看图——那一步留给点「先看图确认」或出图
            _frames_set(sid, running=False, done=True, usable=0, total=total,
                        progress="抽了 %d 张（没花钱）。还没逐张看图：出图那一刻会自动看，"
                                 "也可以现在点「先看图确认」" % total)
    except Exception as e:
        traceback.print_exc()
        _frames_set(sid, running=False, done=True, error=str(e)[:200])
    finally:
        _shutil.rmtree(str(tmp), ignore_errors=True)


@app.route("/api/poster/frames/build", methods=["POST"])
def api_poster_frames_build():
    """给一条素材补画面池。以前只有"新采集的素材"才会自动抽帧，
    老素材（转写时还没这套流程、或视频已删）在界面上永远看不到攻略页那句提示——
    这条就是那座桥：点一下重新下载、抽帧、裁掉后期压上去的字。

    两个档，分开收费（号主 10-12 定的口径：判帧只补真要发的那几条）：
      action=build（默认）＝**零成本**，只抽不判；
      action=judge＝只判帧（要已有帧池），这是花钱的那一下，必须号主亲手点。
    """
    data = request.get_json(force=True) or {}
    try:
        sid = int(data.get("summary_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    summary = _poster_get_summary(sid)
    if not summary:
        return jsonify({"error": "整合稿不存在"}), 404
    # 可以点名给"这篇采用的某一条素材"补画面/判帧（2026-10-22 真试用抓到的）：
    # 以前这里硬取 summary["aweme_id"]＝**主素材**那一条，而整合稿的主视频常常抽完 ASR
    # 就把原片删了（没帧池），同一篇里另一条明明有 18 张帧却永远点不到 ——
    # 「补画面 / 先看图确认」全打在没救的那条上，0 元真帧档就此在界面上打不通。
    try:
        want_vid = int(data.get("video_id") or 0)
    except (TypeError, ValueError):
        want_vid = 0
    aweme_id = ""
    if want_vid:
        for m in _guide_materials(summary):
            if m["video_id"] == want_vid:
                aweme_id = m["aweme_id"]
                break
        if not aweme_id:
            return jsonify({"error": "这篇稿没有采用编号 %s 的素材，补不了它的画面" % want_vid}), 400
    else:
        aweme_id = str(summary.get("aweme_id") or "").strip()
    if not aweme_id or not aweme_id.isdigit():
        return jsonify({"error": "这条不是抖音视频（没有 aweme_id），补不了画面"}), 400
    job = _frames_job(sid)
    if job.get("running"):
        return jsonify({"started": False, "reason": "already_running",
                        "progress": job.get("progress") or ""})
    action = str(data.get("action") or "build").strip().lower()
    if action == "judge":
        try:
            from src import frame_pool
        except ImportError:
            import frame_pool
        if not frame_pool.load(aweme_id).get("frames"):
            return jsonify({"error": "这条还没有帧池，先点「补画面」抽帧（不花钱）"}), 400
        _frames_set(sid, running=True, done=False, error="", progress="排队中...")
        threading.Thread(target=lambda: _judge_thread(sid, aweme_id), daemon=True).start()
        return jsonify({"started": True, "action": "judge",
                        "summary_id": sid, "aweme_id": aweme_id})
    # 重下原片要用"那一条素材自己的"记录，不能用整理稿 join 出来的主视频行
    video = {}
    if want_vid:
        try:
            video = {int(v["id"]): v for v in content_store.get_videos_by_ids(DB_PATH, [want_vid])}.get(want_vid) or {}
        except Exception as e:
            print("[guide] 取素材 %s 失败:" % want_vid, e, flush=True)
    if not video:
        video = content_store.get_video_by_source(DB_PATH, summary.get("source_sheet"),
                                                  summary.get("source_row")) or dict(summary)
    _frames_set(sid, running=True, done=False, error="", progress="排队中...")
    threading.Thread(target=_frames_worker, daemon=True,
                     args=(sid, video, aweme_id, _ai_config(),
                           bool(data.get("judge")))).start()
    return jsonify({"started": True, "summary_id": sid, "aweme_id": aweme_id})


def _judge_thread(sid, aweme_id):
    """判帧那条后台线程：出错也要把 running 落下来，否则界面永远转圈"""
    try:
        _frames_judge(sid, aweme_id, _ai_config())
    except Exception as e:
        traceback.print_exc()
        _frames_set(sid, running=False, done=True, error=str(e)[:200])


@app.route("/api/poster/styles")
def api_poster_styles():
    """风格预设名单由后端下发：配方原文只有一份，前端不抄一遍才不会漂移。"""
    sid = request.args.get("summary_id") or 0
    guide = ai_poster.guide_style_for(sid)
    return jsonify({"items": ai_poster.style_choices(),
                    # 布局名单同样后端下发：十种构图、能不能没图、卡片数区间，前端不抄。
                    # 界面这一档只给「攻略页」（号主 10-16：看版面也是看攻略页）
                    "layouts": ai_poster.layout_choices(),
                    "modes": [{"key": "typeset", "label": "程序排字（推荐，字不会错）"},
                              {"key": "model", "label": "模型画字（整张交给模型，风格更统一但会错字）"}],
                    # 两套预算按模式分开下发：程序排字看版面、模型画字看错字率，
                    # 前端自己写死一份迟早会跟后端对不上
                    "limitsByMode": {"typeset": ai_poster.TS_LIMITS, "model": ai_poster.MD_LIMITS},
                    "limits": {"scene": ai_poster.SCENE_MAX, "cardVis": ai_poster.CARD_VIS_MAX,
                               "cardMin": ai_poster.CARD_MIN, "cardMax": ai_poster.CARD_MAX,
                               "spine": ai_poster.SPINE_MAX},
                    "reviewPass": ai_poster.REVIEW_PASS,
                    # 攻略页三套皮肤按素材轮换，轮换公式只有 Python 这一份：
                    # 前端点「改成攻略页」时带着 summary_id 来问该用哪张脸，别自己抄一遍算法，
                    # 否则下拉显示深墨绿、拆解实际出米白，界面就在说假话。
                    # defaultStyle 也用它：打开弹窗默认就该落在这一档这一张脸上。
                    "guideStyles": list(ai_poster.GUIDE_STYLES),
                    "guideStyle": guide, "defaultStyle": guide,
                    # 名单收窄了要说清是收窄不是丢了：全名单还在 STYLE_PRESETS 里，
                    # 只是不进这个下拉（模板管理页看得到）
                    "hiddenStyles": [k for k in ai_poster.STYLE_ORDER
                                     if k not in ai_poster.GUIDE_STYLES]})


@app.route("/api/poster/adoption")
def api_poster_adoption():
    """最近 N 条成品的推荐采纳率：模型推荐的那份 vs 人最后真正出的那份。

    本地工具不落库，直接扫 manifest（跟成品列表同一套枚举，测试位不算样本）。
    这数字是用来收紧规则的：哪个布局老被人改回去，就说明推荐表里那条判据不对。
    """
    try:
        want = max(5, min(int(request.args.get("n") or 20), 100))
    except (TypeError, ValueError):
        want = 20
    rows = []
    try:
        for m in ai_poster.list_packages():
            rec, fin = m.get("recommended") or {}, m.get("final") or {}
            if not rec.get("layouts") or not fin.get("layouts"):
                continue
            rows.append((rec, fin, m))
            if len(rows) >= want:
                break
    except Exception:
        print("[poster/adoption] 扫包失败:\n%s" % traceback.format_exc(), flush=True)
        return jsonify({"error": "读取成品清单失败", "sampled": 0}), 500
    if not rows:
        return jsonify({"sampled": 0,
                        "note": "还没有带推荐记录的成品：拆一次文案（会给出页数与布局推荐）再出图就有了"})
    out = ai_poster.adoption_stats(rows)
    out["why"] = (rows[0][2].get("why") or "")
    return jsonify(out)


@app.route("/api/poster/list")
def api_poster_list():
    try:
        items = []
        for i in ai_poster.list_packages():
            rv = i.get("review") or {}
            items.append({"id": i.get("id"), "summary_id": i.get("summary_id") or i.get("id"),
                          "title": i.get("title"), "url": i.get("url"),
                          "chars": i.get("text_chars"), "ratio": i.get("ratio"),
                          # 旧包没有这两个键，前端按缺省不显示渠道标签
                          "publish_channel": i.get("publish_channel") or "toutiao",
                          "form": i.get("form") or "",
                          "source_title": i.get("source_title") or "",
                          "style_label": i.get("style_label") or "",
                          "text_mode": i.get("text_mode") or "model",
                          "photo": bool(i.get("photo")),
                          "base_reused": bool(i.get("base_reused")),
                          "cost": i.get("cost_yuan_estimate"),
                          "copies": i.get("copies") or 1,
                          # 多页那组在列表里只占一格（封面就是第 1 页），角标显示"3 页"
                          "page_count": i.get("page_count") or 1,
                          "page_labels": [p.get("layout_label") or p.get("layout") or ""
                                          for p in (i.get("pages") or [])],
                          "verify_ok": (i.get("verify") or {}).get("ok"),
                          "review_score": rv.get("score"), "review_full": rv.get("full"),
                          "review_ok": rv.get("ok"),
                          "created_at": i.get("created_at")})
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
    fast_model = collector.config_get(config, "AI_MODEL_FAST", "")
    fast_key = collector.config_get(config, "AI_API_KEY_FAST", "")
    live = _ai_config()
    live_fast = _fast_config()
    return jsonify({
        "method": method,
        "has_key": bool(key),
        "api_key_masked": _mask_key(key),
        "api_base": base,
        "model": model,
        "fast_model": fast_model,
        # 池子在驱动时，输入框里那个 AI_MODEL 只是"池子全不可用时的兜底"，
        # 所以这一栏必须另给实际生效的模型名，否则界面写着一个模型、跑的是另一个。
        "active_model": live.get("model") or "",
        "active_fast_model": live_fast.get("model") or "",
        "main_note": live.get("model_note") or "",
        "fast_note": live_fast.get("model_note") or "",
        "main_pool": ai_models.pool("main"),
        "fast_pool": ai_models.pool("fast"),
        # 快速档另配了一把 key 就得在设置页说出来，不然"API Key"那一栏看着像全局共用的
        "fast_key_own": bool(fast_key),
        "fast_api_key_masked": _mask_key(fast_key) if fast_key else "",
        # 哪些模型被这层关掉了"先思考"（以及没挂上的原因）——设置页要说实话
        "thinking_off": ai_thinking.installed().get("off_prefixes") or [],
        "thinking_error": ai_thinking.installed().get("error") or "",
    })


@app.route("/api/ai/quota")
def api_ai_quota():
    """免费额度还剩多少（本地账）。产线现在用哪几个模型也一并回，界面才知道该盯谁。"""
    from src import ai_failover
    cfg = _ai_config()
    fast = _fast_config()
    return jsonify({"rows": ai_quota.status(),
                    "hook": ai_quota.installed(),
                    # 结构化返回，别让前端拿中文标签当键名去取（改一个字面就静默失效）
                    "in_use": [{"tier": "main", "label": "主档",
                                "does": "采集打标·连通测试", "model": cfg.get("model") or "",
                                "note": cfg.get("model_note") or ""},
                               {"tier": "fast", "label": "快速档",
                                "does": "整理稿·拆解·卡片化·成稿", "model": fast.get("model") or "",
                                "note": fast.get("model_note") or ""},
                               {"tier": "vision", "label": "看图档",
                                "does": "判帧·回读·打分·拆参考图",
                                "model": cfg.get("vision_model") or ai_poster.OCR_MODEL,
                                "note": "固定一档，不进候选池"}],
                    # 出图这一档 2026-10-29 号主撤了（近三周 manifest 的 image_model 全空，
                    # 实际走素材真帧 + 豆包手工出图），这里不再列一行假档位
                    "pool": {"main": ai_models.pool("main"), "fast": ai_models.pool("fast")},
                    "failover": ai_failover.events(),
                    "warn_at": ai_quota.WARN_AT, "danger_at": ai_quota.DANGER_AT})


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
        default_base, default_model = "https://api.deepseek.com", "deepseek-v4.1-flash"
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
            # 不能给 8：这里的模型（主档 qwen3.8-max / 快速档 deepseek-v4.1-flash）都默认开思考，
            # 8 个 token 全被 reasoning 吃掉，content 返回空串——「测试连接」看着像没通但其实通了。
            # 实测该提示词 reasoning 只用 12–71，给 512 是留余量，单次仍在一秒级。
            max_tokens=512,
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
        items = content_store.list_subscriptions(DB_PATH)
    except Exception as e:
        return jsonify({"items": [], "error": str(e)}), 500
    return jsonify({"items": items})


@app.route("/api/bilibili/status")
def api_bilibili_status():
    """B 站采集自检第一步：cookie 配没配。只读本地文件，不打外部接口。"""
    from src.bilibili_fetcher import cookie_status
    try:
        return jsonify(cookie_status())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bilibili/probe", methods=["POST"])
def api_bilibili_probe():
    """真发一次投稿列表请求，验证登录态能不能过风控。只在用户点「测试连通」时走。"""
    from src.bilibili_fetcher import probe, parse_mid
    data = request.get_json(force=True, silent=True) or {}
    mid = parse_mid(data.get("mid") or data.get("url") or "")
    if not mid:
        return jsonify({"ok": False, "error": "请填写 UP 主空间链接或数字空间号"}), 400
    try:
        return jsonify(probe(mid))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════════
# B 站合集整档入库 + 补转写
# 为什么要这两个入口：订阅同步只翻 UP 主投稿列表、每轮 20 条，而「好赛雷切片」这种
# 把内容全塞进合集的号，投稿列表里看不见合集那 997 条（实测订阅它 last_new_count=0）。
# 号主 2026-10-27 要的就是"合集整档"。
# ════════════════════════════════════════════════════════════════

_bili_job = {"kind": "", "running": False, "done": True, "progress": "", "error": "",
             "result": None, "started_at": "", "elapsed": 0}
_bili_job_lock = threading.Lock()


def _job_start(store: dict, lock, kind: str, fn) -> bool:
    """跑一个后台采集任务并把它那份状态写进 store。同一时刻同一档只允许一个：
    合集入库和补转写都要连打外部接口，并行跑既没意义又更容易撞 B 站风控。"""
    with lock:
        if store["running"]:
            return False
        store.update({"kind": kind, "running": True, "done": False,
                      "progress": "排队中…", "error": "", "result": None,
                      "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "elapsed": 0})

    def _run():
        t0 = time.time()
        try:
            res = fn(lambda m: store.__setitem__("progress", str(m)))
            store["result"] = res
            store["progress"] = "✅ " + str((res or {}).get("note") or "完成")
        except Exception as e:
            store["error"] = str(e)[:300]
            store["progress"] = "❌ 失败：" + store["error"]
            print("[job:%s] 失败: %s" % (kind, traceback.format_exc()[-800:]), flush=True)
        finally:
            store["running"] = False
            store["done"] = True
            store["elapsed"] = int(time.time() - t0)

    threading.Thread(target=_run, daemon=True).start()
    return True


def _bili_job_start(kind: str, fn) -> bool:
    return _job_start(_bili_job, _bili_job_lock, kind, fn)


@app.route("/api/bilibili/collection", methods=["POST"])
def api_bilibili_collection():
    """合集链接（合集里任意一条稿件）→ 整档入库。body: {url, limit?, category?, game?}"""
    from src.bilibili_fetcher import sync_bilibili_collection, parse_bvid, load_cookie
    data = request.get_json(force=True, silent=True) or {}
    raw = (data.get("url") or "").strip()
    bvid = parse_bvid(raw)
    if not bvid:
        return jsonify({"ok": False, "error": "贴合集里任意一条视频链接（含 BV 号）就行"}), 400
    limit = int(data.get("limit") or 300)
    category = (data.get("category") or "").strip()
    game = (data.get("game") or "").strip()
    try:
        cookie, _where = load_cookie()
    except Exception:
        cookie = ""

    def _job(progress):
        known = content_store.get_bilibili_bvids(DB_PATH)
        progress("取合集清单（一次接口，实测 997 条约 1.7MB）…")
        res = sync_bilibili_collection(bvid, known_bvids=known, limit=limit, cookie=cookie)
        if res.get("error"):
            return {"ok": False, "note": res["error"]}
        # 分类没在请求里给，就退回这个号订阅时设的分类与游戏（同一口径，不新造规则）
        cat, gm = category, game
        if not cat:
            for s in content_store.list_subscriptions(DB_PATH):
                if s.get("sec_uid") == "bili:%s" % res.get("mid"):
                    cat = s.get("category") or ""
                    gm = gm or (s.get("game") or "")
                    break
        progress("合集共 %d 条，库里已有 %d 条，本轮入 %d 条…"
                 % (res["total"], res["skipped"], len(res["videos"])))
        with _db_lock:
            inserted = content_store.insert_bilibili_videos(
                DB_PATH, res["videos"], author=res.get("author") or "",
                sec_uid="bili:%s" % res.get("mid"), category=cat, game=gm)
        progress("入库完成")
        return {"ok": True, "note": "合集「%s」共 %d 条：本轮入库 %d 条，库里原有 %d 条%s"
                % (res.get("season") or "?", res["total"], len(inserted), res["skipped"],
                   ("，分类「%s」" % cat) if cat else "，未分类（可在素材库里改）")}

    if not _bili_job_start("collection", _job):
        return jsonify({"ok": False, "error": "已有一个 B 站采集任务在跑，等它结束再点"}), 409
    return jsonify({"ok": True, "started": True, "bvid": bvid, "limit": limit})


@app.route("/api/bilibili/asr", methods=["POST"])
def api_bilibili_asr():
    """给正文还空着的 B 站素材补 ASR。body: {limit?, author?, model?}

    只下音频不下整片（一个合集 122.7 小时，整片几十 GB 没意义）。这一步很慢且吃 CPU，
    所以限量、给进度、且同一时刻只跑一个任务。
    """
    from src.bilibili_fetcher import transcribe_bvid, load_cookie
    data = request.get_json(force=True, silent=True) or {}
    limit = max(1, int(data.get("limit") or 20))
    author = (data.get("author") or "").strip()
    model_size = (data.get("model") or "base").strip()
    # 条长上限：这个合集里最长的一条是 4 小时 20 分的直播录像，不截一刀的话"补 20 条"
    # 里随便一条就能吃掉一小时机器时间，而号主看到的只是"卡在进度上没动"
    max_minutes = int(data.get("max_minutes") or 45)
    try:
        cookie, _where = load_cookie()
    except Exception:
        cookie = ""

    def _job(progress):
        rows = content_store.pending_bili_transcripts(
            DB_PATH, limit=limit, season_author=author, max_seconds=max_minutes * 60)
        if not rows:
            return {"ok": True, "note": "没有可转写的 B 站素材（正文都已有，或都超过 %d 分钟）"
                    % max_minutes}
        audio_min = sum(int(r.get("duration_sec") or 0) for r in rows) / 60.0
        # 本机 faster-whisper base 实测约 5.5 倍实时（见记忆/README），先给预计时间再开跑，
        # 别让他自己发现"做不完"
        progress("本轮 %d 条、共 %.1f 小时音频，预计约 %.0f 分钟（跳过超 %d 分钟的长录像）…"
                 % (len(rows), audio_min / 60.0, audio_min / 5.5, max_minutes))
        done = 0
        chars = 0
        failed = []
        work = str(OUTPUT_DIR / "bili_asr_tmp")
        t0 = time.time()
        for i, r in enumerate(rows, 1):
            mins = int(time.time() - t0) // 60
            progress("转写 %d/%d：%s（%d 分钟长，已用 %d 分 %d 秒）"
                     % (i, len(rows), r["aweme_id"], int(r.get("duration_sec") or 0) // 60,
                        mins, int(time.time() - t0) % 60))
            text, err = transcribe_bvid(r["aweme_id"], cookie=cookie,
                                        model_size=model_size, workdir=work)
            if err:
                failed.append("%s: %s" % (r["aweme_id"], err[:60]))
                continue
            with _db_lock:
                ok = content_store.set_video_transcript(DB_PATH, r["id"], text)
            if ok:
                done += 1
                chars += len(text)
        spent = int(time.time() - t0)
        note = ("补转写完成：%d/%d 条有正文（平均 %d 字），用时 %d 分 %d 秒"
                % (done, len(rows), (chars // done) if done else 0, spent // 60, spent % 60))
        if failed:
            note += "；失败 %d 条（%s）" % (len(failed), "；".join(failed[:3]))
        return {"ok": True, "note": note, "done": done, "failed": failed,
                "audio_hours": round(audio_min / 60.0, 2), "seconds": spent}

    if not _bili_job_start("asr", _job):
        return jsonify({"ok": False, "error": "已有一个 B 站采集任务在跑，等它结束再点"}), 409
    return jsonify({"ok": True, "started": True, "limit": limit, "model": model_size})


@app.route("/api/bilibili/job")
def api_bilibili_job():
    """合集入库 / 补转写这两个后台任务共用的进度。"""
    out = dict(_bili_job)
    if out["running"]:
        try:
            out["elapsed"] = int(time.time() - time.mktime(
                time.strptime(out["started_at"], "%Y-%m-%d %H:%M:%S")))
        except Exception:
            pass
    return jsonify(out)


# ── 暴雪官方公告：抓取入口 + 状态（源类定时任务的第一个用户） ──
# 为什么必须有这一格（2026-10-29）：这条源此前**只有 CLI**（python3 -m src.blizzard_news），
# sync_tasks 里没有它的任务，界面上那个「刷新」也只重读本地库——于是上游 10-22/10-24/10-27
# 连发三批公告，库里停在 10-21 整整八天，而页面还写着"抓取 python3 -m src.blizzard_news"。
_blizzard_job = {"kind": "", "running": False, "done": True, "progress": "", "error": "",
                 "result": None, "started_at": "", "elapsed": 0}
_blizzard_job_lock = threading.Lock()
BLIZZARD_STATE_PATH = ROOT / "output" / "blizzard_state.json"
# 默认时刻：新建任务时给一次，之后以文件里那份为准（调度器不认这个常量，别把它当配置）
BLIZZARD_DEFAULT_RUN_AT = "10:10"
BLIZZARD_FETCH_LIMIT = 12


def _blizzard_state() -> dict:
    try:
        with open(BLIZZARD_STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _blizzard_state_write(patch: dict) -> dict:
    """记账。抓一次就写一次，哪怕这轮 0 新增——「上次几点抓的」和「库里最新一条」
    是两件事，只记后者就会把"抓过但没新东西"说成"八天没抓"。"""
    st = _blizzard_state()
    st.update(patch or {})
    try:
        with open(BLIZZARD_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[blizzard] 状态写不进去（不影响抓取结果）: %s" % e, flush=True)
    return st


def _blizzard_task():
    """找到（没有就建）这条源类任务。建的时候给一天一次、10:10，避开 07:30–09:30 那批订阅同步。"""
    cfg = sync_tasks.load()
    for t in cfg.get("tasks") or []:
        if str(t.get("kind") or "") == "source" and t.get("source") == "blizzard":
            return t
    cfg = sync_tasks.load()
    t = sync_tasks.new_source_task(cfg, "blizzard", interval_days=1,
                                   run_at=BLIZZARD_DEFAULT_RUN_AT)
    if t:
        sync_tasks.save(cfg)
        print("[blizzard] 已建源类定时任务 %s（每天 %s）" % (t["key"], t["run_at"]), flush=True)
    return t


def _blizzard_do_refresh(progress=None, task_keys=None):
    """抓一轮官方公告 + 记账。返回 blizzard_news.sync 的统计。"""
    from src import blizzard_news

    def say(msg):
        if progress:
            progress(msg)
    res = blizzard_news.sync(DB_PATH, limit=BLIZZARD_FETCH_LIMIT, progress_cb=say)
    errs = res.get("errors") or []
    ok = not errs
    _blizzard_state_write({
        "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_found": int(res.get("found") or 0),
        "last_new": int(res.get("new") or 0),
        "last_error": "；".join([str(e)[:120] for e in errs[:3]]),
    })
    if task_keys:
        note = "列表 %d 条、新增 %d 条" % (int(res.get("found") or 0), int(res.get("new") or 0))
        sync_tasks.finish_batch(task_keys, ok, note if ok else ("抓取报错：" + note),
                                now=datetime.now())
    return res


@app.route("/api/blizzard/refresh", methods=["POST"])
def api_blizzard_refresh():
    """手动抓一轮暴雪官方公告（排产表那栏的「刷新」就是这个）。"""
    def _job(progress):
        progress("读 wow.blizzard.cn/news/ …")
        # 手动这一跳也要给任务记账：不记的话 last_run 一直空着，定时器下一分钟就认为
        # "这条从没跑过、现在就该跑"，于是刚手动抓完又自动抓一遍（对官方站是纯骚扰）
        t = None
        try:
            t = _blizzard_task()
        except Exception:
            t = None
        res = _blizzard_do_refresh(progress, task_keys=[t["key"]] if t else None)
        errs = res.get("errors") or []
        note = ("新增 %d 条（列表共 %d 条，库里原有的那几条不重抓）"
                % (int(res.get("new") or 0), int(res.get("found") or 0)))
        if errs:
            note += "；有 %d 处报错：%s" % (len(errs), str(errs[0])[:100])
        return {"ok": not errs, "note": note, "result": res}

    if not _job_start(_blizzard_job, _blizzard_job_lock, "blizzard", _job):
        return jsonify({"ok": False, "error": "上一次抓取还没跑完，稍等一下"}), 409
    return jsonify({"ok": True, "started": True})


@app.route("/api/blizzard/status")
def api_blizzard_status():
    """库内条数 / 最新一条 / 上次抓取时间 / 滞后几天 / 任务设置。全从现数，不抄常量。"""
    out = {"ok": True, "rows": 0, "newest": "", "lag_days": None,
           "last_run": "", "last_new": None, "last_error": "", "running": False,
           "progress": _blizzard_job.get("progress", ""), "task": None}
    try:
        conn = content_store.open_readonly(DB_PATH)
        try:
            r = conn.execute("SELECT COUNT(*) AS n, MAX(published_at) AS newest "
                             "FROM videos WHERE source_sheet = ?",
                             (content_store.OFFICIAL_NEWS_SHEET,)).fetchone()
            out["rows"] = int(r["n"] or 0)
            out["newest"] = r["newest"] or ""
        finally:
            conn.close()
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)[:200]
    st = _blizzard_state()
    out["last_run"] = st.get("last_run") or ""
    out["last_new"] = st.get("last_new")
    out["last_error"] = st.get("last_error") or ""
    out["running"] = bool(_blizzard_job.get("running"))
    if out["newest"]:
        try:
            d = datetime.strptime(str(out["newest"])[:10], "%Y-%m-%d")
            out["lag_days"] = (datetime.now() - d).days
        except Exception:
            pass
    try:
        t = _blizzard_task()
        if t:
            nxt = sync_tasks.next_due(t, sync_tasks.load().get("run_at") or "", datetime.now())
            out["task"] = {"key": t["key"], "interval_days": t["interval_days"],
                           "run_at": sync_tasks.task_run_at(t), "last_run": t.get("last_run") or "",
                           "last_status": t.get("last_status") or "",
                           "next": nxt.strftime("%Y-%m-%d %H:%M") if nxt else "",
                           "auto_on": bool(sync_tasks.load().get("enabled"))}
    except Exception as e:
        out["task_error"] = str(e)[:160]
    return jsonify(out)


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

    if platform == "bilibili":
        # B 站：url 是 UP 主空间链接或直接填数字空间号；sec_uid 用可读的 bili:<mid>
        # （微信那份是 md5 合成串，这里不必——mid 本身就是稳定主键）
        from src.bilibili_fetcher import parse_mid, resolve_author
        mid = parse_mid(url)
        if not mid:
            return jsonify({"error": "B 站要填 UP 主空间链接（https://space.bilibili.com/<数字>/video）"
                                     "或直接填数字空间号；视频稿链接认不出作者"}), 400
        sec_uid = "bili:%s" % mid
        user_url = "https://space.bilibili.com/%s/video" % mid
        if not author:
            # card 接口现在会被风控，resolve_author 内部会退到稿件 owner.name
            try:
                author = resolve_author(mid)
            except Exception as e:
                print(f"[subscriptions] B站昵称取回失败（仍可订阅，同步时会再试）: {e}", flush=True)
        try:
            with _db_lock:
                sub = content_store.add_subscription(
                    DB_PATH, sec_uid, user_url, author, category, game, "bilibili")
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"success": True, "subscription": sub, "mid": mid})

    # 抖音：url 是用户主页链接，或者一条作品（视频/图文）的分享链接
    from src.fetch_user_videos import (extract_sec_uid_from_url, first_url,
                                       is_work_url, resolve_short_url)
    url = first_url(url)          # 整段分享文案粘进来是常态，先剥壳再判形状
    if "v.douyin.com" in url or "/share/" in url:
        url = resolve_short_url(url)
    sec_uid = extract_sec_uid_from_url(url)
    if not sec_uid and is_work_url(url):
        # 图文作品的短链展开后是 www.douyin.com/note/<id>，链接里没有 sec_uid，
        # 纯 requests 无解——必须开一次真实浏览器把作品页的作者主页认出来。
        from src.browser_fetch import browser_fetch_author
        info = browser_fetch_author(url)
        sec_uid = info.get("sec_uid") or ""
        if sec_uid:
            if not author:
                author = info.get("nickname") or ""
            c = info.get("counts") or {}
            print(f"[subscriptions] 作品链接认出作者「{author}」主页视频 {c.get('video', '?')} 条、"
                  f"图文 {c.get('note', '?')} 条（首屏计数）", flush=True)
    if not sec_uid:
        return jsonify({"error": "认不出这个抖音号：链接里没有主页 sec_uid，作品页也没对到作者。"
                                 "改贴作者主页链接再试（在抖音里点他头像 → 右上角分享 → 复制链接，"
                                 "展开是 www.douyin.com/user/... 那种）"}), 400
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
    """视频库作者统计（含是否已订阅）——「导入素材库作者为订阅」那颗列表。

    论坛行必须挡掉：NGA 那 1105 行的 author 是发帖人，814 个署名混进这张列表，
    点一下就去解析人家的抖音主页，导进来的全是废订阅（同一列在不同源里语义不同，
    素材库「全部作者」那次翻车就是这个原因，见 _author_topics 上方口径）。
    """
    if not DB_PATH.exists():
        return jsonify({"authors": []})
    try:
        conn = content_store.open_readonly(DB_PATH)  # 只读：不建表、不抢写锁
        try:
            rows = conn.execute(
                "SELECT COALESCE(NULLIF(TRIM(author),''),'未知作者') AS a, COUNT(*) AS n "
                "FROM videos WHERE COALESCE(source,'') <> 'forum' GROUP BY a ORDER BY n DESC"
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
    """取消订阅：**连带删掉这个号采进来的一切**，删了不可恢复。

    2026-10-16 号主拍板的三条口径，别再自作主张改回保守版：
      ① 删到图文包与图片目录这一层（output/{toutiao,wechat,poster}/<稿id>、
         关键帧目录、封面 jpg、raw json）；
      ② 跨号合集稿只要引用到被删素材就整篇删（不剔除单个 id）；
      ③ 不加防误删——不统计条数、不留备份（output/ 不在 git 里，本来也没版本兜底）。
    顺序：整理稿 → 素材行 → 磁盘产物 → Excel 行与索引 → 订阅本身。
    """
    try:
        with _db_lock:
            subs = content_store.list_subscriptions(DB_PATH)
        sub = next((s for s in subs if s["id"] == sub_id), None)
        if not sub:
            return jsonify({"error": "订阅不存在"}), 404
        sec_uid = (sub.get("sec_uid") or "").strip()
        if not sec_uid:
            # 没有 sec_uid 就不敢猜该删哪些行，宁可只删订阅
            with _db_lock:
                content_store.delete_subscription(DB_PATH, sub_id)
            return jsonify({"success": True, "purged": 0,
                            "note": "订阅记录缺 sec_uid，只取消了订阅，没动素材"})

        import shutil
        with _db_lock:
            targets = content_store.subscription_cleanup_targets(
                DB_PATH, sec_uid, sub.get("author") or "")
        videos = targets["videos"]
        sids = targets["summary_ids"]
        purged = {"videos": 0, "summaries": 0, "packages": 0, "frames": 0, "note_dirs": 0}

        with _db_lock:
            # 1. 整理稿（含被判定整篇删掉的合集稿）+ 它们的图文包/海报目录
            for sid in sids:
                for deleter in (toutiao_graphics.delete_package,
                                wechat_graphics.delete_package,
                                ai_poster.delete_package):
                    try:
                        if deleter(sid):
                            purged["packages"] += 1
                    except Exception as e:
                        print(f"[unsubscribe] 删图文包 {sid} 失败（继续）: {e}", flush=True)
                content_store.delete_summary(DB_PATH, sid)
                purged["summaries"] += 1
            # 2. 素材行 + 按 aweme_id 归位的磁盘产物
            for v in videos:
                content_store.delete_video(DB_PATH, v["sheet"], v["row"])
                purged["videos"] += 1
                aweme_id = v.get("aweme_id") or ""
                if not aweme_id:
                    continue
                safe = material_store.safe_id(aweme_id)
                kdir = material_store.KEYFRAMES_DIR / safe
                if kdir.exists():
                    shutil.rmtree(kdir, ignore_errors=True)
                    purged["frames"] += 1
                if v["sheet"] == content_store.NOTE_SHEET:
                    # 图文的产物就是那批本地图（douyin_notes/<aweme_id>/），不删会变成
                    # 孤儿目录：DB 行没了、图还在盘上占地方，也没地方再找到它们
                    from src.douyin_notes import purge_note_images
                    purged["note_dirs"] += purge_note_images(OUTPUT_DIR, [aweme_id])
                for jpg in material_store.COVERS_DIR.glob(safe + ".*"):
                    try:
                        jpg.unlink()
                    except OSError:
                        pass
                raw = material_store.RAW_DIR / (safe + ".json")
                if raw.exists():
                    try:
                        raw.unlink()
                    except OSError:
                        pass
            # 3. Excel 行清空 + 索引摘除 + 重同步（与「删除视频」同一套动作）
            _purge_library_rows([(v["sheet"], v["row"]) for v in videos])
            _sync_content_db()
            content_store.delete_subscription(DB_PATH, sub_id)
        print(f"[unsubscribe] 订阅 {sub_id}（{sub.get('author') or sec_uid}）连带清理：{purged}", flush=True)
        return jsonify({"success": True, "purged": purged})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _do_subscription_sync(sub_id=None, sub_ids=None, cookie=None):
    """订阅同步的实际执行体：手动点「同步」和自动任务共用这一份，不写两遍。

    状态统一写 _sub_status——手动与自动共用同一个"当前同步"槽位，天然互斥，
    不会出现两边同时改 Excel、同时起 Chromium。前端本来就在轮询这个槽位，
    自动任务跑起来时界面也能看到实时进度。
    """
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

        # 分流：抖音需要 cookie + parser；微信不需要；B站需要自己的 B 站 cookie
        douyin_subs = [s for s in subs if s.get("platform", "douyin") == "douyin"]
        wechat_subs = [s for s in subs if s.get("platform") == "wechat"]
        bilibili_subs = [s for s in subs if s.get("platform") == "bilibili"]

        if douyin_subs and not cookie:
            _sub_status["error"] = "抖音订阅需要 Cookie，请先在采集中心完成一次采集"
            return

        summary = []
        pending_marks = []  # 有新视频的抖音作者：终局统一标记（见下方终局块）
        # 本轮真正入库的 (source_sheet, source_row)：收尾只给这批打标，不扫全库
        new_rows = []

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
                    new_rows += [(i.get("source_sheet"), i.get("source_row")) for i in inserted]
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

        # ── B 站订阅同步 ──
        # 和微信一样是「DB 直写、不进 Excel、不跑 ASR」：正文来自 B 站字幕轨
        # （人工 CC 优先，其次 AI 轨）。放在抖音分支之前、不并进任一分支，
        # 否则只勾 B 站订阅时又会重演"新素材永远缺 ai_tags"那个坑。
        if bilibili_subs:
            from src.bilibili_fetcher import (
                sync_bilibili_user, load_cookie as _bili_load_cookie)
            bili_ck = ""
            try:
                bili_ck, _bili_src = _bili_load_cookie()
            except RuntimeError as e:
                # cookie 没配只让 B 站这几条失败，不能挡住抖音/微信那两档
                note = str(e).splitlines()[0][:80]
                for sub in bilibili_subs:
                    with _db_lock:
                        content_store.update_subscription_sync(DB_PATH, sub["id"], 0, note)
                    summary.append(f"{sub['author'] or sub['sec_uid']}: {note}")
            if bili_ck:
                for si, sub in enumerate(bilibili_subs, 1):
                    mid = (sub.get("sec_uid") or "").split(":", 1)[-1]
                    name = sub["author"] or ("UP " + mid)
                    _sub_status["progress"] = f"[B站 {si}/{len(bilibili_subs)}] {name}: 拉投稿列表..."
                    try:
                        with _db_lock:
                            known = content_store.get_bilibili_bvids(DB_PATH)
                        res = sync_bilibili_user(mid, known_bvids=known,
                                                 limit=BILI_SYNC_LIMIT, cookie=bili_ck)
                        if res["error"]:
                            with _db_lock:
                                content_store.update_subscription_sync(
                                    DB_PATH, sub["id"], 0, res["error"])
                            summary.append(f"{name}: {res['error']}")
                            continue
                        new = res["videos"]
                        extra = ("；" + res["note"]) if res.get("note") else ""
                        if not new:
                            with _db_lock:
                                content_store.update_subscription_sync(
                                    DB_PATH, sub["id"], 0, "无新投稿" + extra)
                            summary.append(f"{name}: 无新投稿{extra}")
                            continue
                        with _db_lock:
                            inserted = content_store.insert_bilibili_videos(
                                DB_PATH, new, author=name, sec_uid=sub["sec_uid"],
                                category=sub.get("category") or "",
                                game=sub.get("game") or "")
                        new_rows += [(i.get("source_sheet"), i.get("source_row")) for i in inserted]
                        # 字幕情况直接写进同步备注：用户看一行就知道这批素材能不能直接用
                        n_sub = sum(1 for v in inserted if v.get("subs_status") in ("字幕", "AI字幕"))
                        n_none = len(inserted) - n_sub
                        cat_note = (f"，已按订阅标记分类「{sub['category']}」"
                                    if sub.get("category") and inserted else "")
                        note = (f"新增 {len(inserted)} 条（有字幕 {n_sub} / 无正文 {n_none}）"
                                f"{cat_note}{extra}")
                        shown_author = name
                        if not sub.get("author"):
                            # 添加订阅时昵称没取到（card 接口会被风控），这轮从稿件 owner 补上，
                            # 免得列表长期显示"UP 123456"、按作者筛选也永远命不中
                            shown_author = res.get("author") or name
                        with _db_lock:
                            content_store.update_subscription_sync(
                                DB_PATH, sub["id"], len(inserted), note, author=shown_author)
                        summary.append(f"{shown_author}: {note}")
                        _sub_status["new_items"] += [
                            {"kind": "bilibili", "author": shown_author,
                             "title": (v.get("title") or "").strip(),
                             "published_at": (v.get("published_at") or "")[:10]}
                            for v in inserted]
                    except Exception as e:
                        with _db_lock:
                            content_store.update_subscription_sync(
                                DB_PATH, sub["id"], 0, f"B站同步失败: {e}")
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
                    n = len(browser_notes.get(str(item_id)) or [])
                    if ids is not None:
                        # 纯图文的号 ids 是空列表但 notes 有货，这里不能报成失败
                        mark = "✓ %d 视频" % len(ids) + (" / %d 图文" % n if n else "")
                    else:
                        mark = f"✗ {err[:40]}"
                    _sub_status["progress"] = f"[抖音主页 {done}/{total}] {mark}"

                browser_notes = {}   # {作者id: [图文条目]}，同一次主页加载顺手带回来的
                browser_ids = browser_fetch_users_batch(_items, on_progress=_bp,
                                                        notes_out=browser_notes)
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
                    fetch_empty = False   # 旧 API 原始拉到 0 条＝这次根本没看到主页，别记成"无新视频"
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
                        # total 是去重后剩的、filtered 是去重掉的：相加为 0 ＝ 原始就没拉到
                        # 任何一条，多半是 Argus 风控拦了旧 API——这要如实报，不能说"无新"
                        fetch_empty = ((result.get("total") or 0)
                                       + (result.get("filtered") or 0)) == 0
                    # ── 图文这一侧先走 ──
                    # 位置要紧：纯图文的号在下面 `if not videos` 那里就 continue 了，
                    # 图文同步放后面等于这个号一条也进不来。图和文案是同一次主页加载
                    # 带回来的（见 tools/douyin_browser_fetch.js 的 attachPostCapture），
                    # 不额外付冷启动 Chromium 的成本。
                    note_res: dict = {}
                    notes = browser_notes.get(str(sub["id"])) or []
                    if notes:
                        _sub_status["progress"] = (
                            f"[抖音 {si}/{len(douyin_subs)}] {name}: 图文 {len(notes)} 条取图中...")
                        try:
                            from src.douyin_notes import sync_notes
                            with _db_lock:
                                note_res = sync_notes(
                                    DB_PATH, OUTPUT_DIR, notes,
                                    author=sub["author"] or name, sec_uid=sub["sec_uid"],
                                    category=sub.get("category") or "",
                                    game=sub.get("game") or "", limit=NOTE_SYNC_LIMIT)
                            for p in (note_res.get("rows") or []):
                                new_rows.append(p)
                            if note_res.get("inserted"):
                                summary.append("%s: 图文新增 %d 条 / 存图 %d 张"
                                               % (name, note_res["inserted"], note_res["images"]))
                            if note_res.get("errors"):
                                summary.append("%s: 图文有 %d 张图没取到"
                                               % (name, len(note_res["errors"])))
                        except Exception as ne:
                            summary.append("%s: 图文入库失败 (%s)" % (name, str(ne)[:60]))
                    note_inserted = note_res.get("inserted") or 0

                    new_author = ""
                    if not videos:
                        note = ("取链为空（浏览器没覆盖到、旧 API 疑似被风控拦），别当无新处理"
                                if fetch_empty else "无新视频")
                        if note_inserted:
                            # 号主只看这一行：明明进了 20 条图文却报"无新视频"就是说假话
                            note = "%s；图文另入 %d 条" % (note, note_inserted)
                        with _db_lock:
                            content_store.update_subscription_sync(
                                DB_PATH, sub["id"], note_inserted, note)
                        summary.append(f"{name}: {note}")
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
                        "notes": note_inserted,
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
                new_rows += [("抖音视频数据", r) for r in rows]
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
                n_img = pm.get("notes") or 0
                total_new = pm["count"] + n_img
                # 没有图文时措辞一字不改（别的会话和号主都按这句对账）；有图文就不能再说
                # "N 条并已转写"——图文没有语音可转写，混进去就是把存图说成转写。
                done_note = ("新增 %d 条并已转写" % pm["count"] if not n_img else
                             "新增 %d 条（视频 %d 已转写、图文 %d 已存图）"
                             % (total_new, pm["count"], n_img))
                with _db_lock:
                    content_store.update_subscription_sync(
                        DB_PATH, sub["id"], total_new, f"{done_note}{cat_note}",
                        author=pm["new_author"])
                summary.append(f"{name}: 新增 {total_new} 条{cat_note}")
                _sub_status["new_items"] += [
                    {"kind": "douyin", "author": pm["new_author"] or name, "title": tt,
                     "published_at": ""} for tt in new_titles if tt]

        # 同步入库后补 AI 增量打标：只补缺口（订阅链路新行自带 category 但缺 ai_tags，
        # 选题雷达依赖 ai_tags）。必须放在三条分支之外——原先挂在抖音分支里，
        # 只勾微信订阅同步时新文章一条标都不打，雷达/渠道策略侧就等于「没有更新」。
        # 范围锁在本轮新增的 id 上：不带范围的那句 WHERE 会把全库缺口一起过 LLM。
        # AI 未配置时静默跳过，打标失败不算同步失败
        _sub_status["progress"] = "AI 自动打标..."
        new_ids = []
        if new_rows:
            with _db_lock:
                new_ids = content_store.video_ids_by_rows(DB_PATH, new_rows)
        _cls = _auto_classify_after_sync(only_ids=new_ids)
        if _cls.get("classified"):
            summary.append(f"AI 打标 {_cls['classified']}/{_cls['total']} 条（仅本轮新增）")
        elif new_rows and _cls.get("skipped") == "ai_not_configured":
            summary.append(f"本轮新增 {len(new_rows)} 条未打标（AI 未配置）")

        _sub_status["result"] = summary
        _sub_status["progress"] = "✅ 订阅同步完成：" + "；".join(summary)
    except Exception as e:
        _sub_status["error"] = f"{e}\n{traceback.format_exc()[-500:]}"
    finally:
        _sub_status["running"] = False
        _sub_status["done"] = True

@app.route("/api/subscriptions/sync", methods=["POST"])
def api_subscriptions_sync():
    """同步订阅（全部/单个/多个勾选）。抖音：拉主页新视频→ASR。微信：拉 RSS 新文章。"""
    data = request.get_json(force=True, silent=True) or {}
    sub_id = data.get("id")  # None = 全部
    sub_ids = data.get("ids") or []  # 勾选批量：[1, 3, 7]；空 = 不启用批量过滤
    cookie = (data.get("cookie") or "").strip() or _read_cookie_from_file()
    if not _claim_sync():
        return jsonify({"error": "订阅同步进行中，请稍候"}), 409
    threading.Thread(target=_do_subscription_sync, args=(sub_id, sub_ids, cookie),
                     daemon=True).start()
    return jsonify({"success": True})


@app.route("/api/subscriptions/sync/status")
def api_subscriptions_sync_status():
    return jsonify(_sub_status)


# ════════════════════════════════════════════════════════════════
# 自动同步任务：勾选订阅 + 每天/每几天，工作台开着就会到点自己跑
# ════════════════════════════════════════════════════════════════
# 为什么放进程内而不是系统 cron：这台机器的工作台基本常开，配置改完立刻生效、界面能看
# 下次执行时间，也不用往用户 crontab 里塞东西。代价是工作台没开时不跑——下次打开若已过
# 点会补一轮（到期判断只看上次执行时间，不会因为跳过而丢任务）。
_autosync_state = {"started_at": "", "last_check": "", "note": "", "skipped": 0}
_sync_claim_lock = threading.Lock()


def _claim_sync():
    """原子占用"当前同步"槽位。手动点同步与自动任务都先抢它，抢不到就不跑。

    原先只在线程内置 running=True，两个请求会同时看到 False 一起起跑——
    同时写 Excel、同时起 Chromium，后果是索引与转写互相踩。
    """
    with _sync_claim_lock:
        if _sub_status.get("running"):
            return False
        _sub_status.update(running=True, done=False, error="", progress="准备同步...",
                           result=None, new_items=[])
        return True


def _run_sync_batch(sub_ids, task_keys, who="自动同步"):
    """跑一轮同步，跑完按任务记账。task_keys 是这一轮覆盖到的任务。"""
    ids = [int(i) for i in sub_ids]
    keys = list(task_keys or [])

    def wrap():
        _do_subscription_sync(None, ids, _read_cookie_from_file())
        err = _sub_status.get("error") or ""
        note = err or (_sub_status.get("progress") or "")
        sync_tasks.finish_batch(keys, not err, note)
        _autosync_state["note"] = "%s结束：%s" % (who, note[:160])
        print("[%s] 本轮结束（%d 个订阅 / %d 个任务）：%s"
              % (who, len(ids), len(keys), note[:200]), flush=True)

    threading.Thread(target=wrap, daemon=True).start()


def _run_source_task(task: dict):
    """跑一条源类任务（暴雪官方公告这种"没有订阅记录"的源）。

    和订阅类同一套账：先 touch_running 占时间戳再跑。不占的话一轮要几十秒，
    跑完才记账的窗口里若界面重写配置或进程重启，这条任务看起来还是"到期"，
    紧接着又会再跑一轮——对官方站是纯骚扰（订阅那边 10-03 就这么翻过车）。
    """
    src = str(task.get("source") or "")
    key = task.get("key")
    sync_tasks.touch_running([key])
    if src == "blizzard":
        if _blizzard_job.get("running"):
            _autosync_state["note"] = "暴雪公告上一轮还在抓，本轮顺延（到期时间不重置）"
            return
        def _job(progress):
            res = _blizzard_do_refresh(progress, task_keys=[key])
            errs = res.get("errors") or []
            return {"ok": not errs,
                    "note": "自动抓取：列表 %s 条、新增 %s 条" % (res.get("found"), res.get("new"))}
        _job_start(_blizzard_job, _blizzard_job_lock, "blizzard", _job)
        _autosync_state["note"] = "自动任务：暴雪官方公告开始抓"
        print("[自动任务] 暴雪官方公告 开始抓（key=%s）" % key, flush=True)
        return
    # 认不出的源必须记失败，不许静默跳过：源类清单里有 nga_import，但执行体还没人写。
    # 静默跳过的表现是"任务显示跑过了、数据却一直是旧的"，比报错难查十倍。
    sync_tasks.finish_batch([key], False, "工作台还不认识这个源：%s（没写执行体）" % src,
                            now=datetime.now())
    print("[自动任务] 没有执行体，已记失败: %s" % src, flush=True)


def _autosync_tick(now=None):
    """每分钟一次的检查：有没有到期任务，到期的并成一批跑。

    一批而不是一个任务一轮：抖音多条只起一次 Chromium（browser_fetch_users_batch 是现成的
    批量口），逐任务跑会把启动浏览器的开销付 N 遍。
    """
    now = now or datetime.now()
    _autosync_state["last_check"] = now.strftime("%Y-%m-%d %H:%M:%S")
    cfg = sync_tasks.load()
    if not cfg.get("enabled") or not cfg.get("tasks"):
        return
    subs = content_store.list_subscriptions(DB_PATH)
    alive = {s["id"] for s in subs}
    # 源类任务先走：它不占订阅同步那把闸（抓一次官方公告约十秒，没必要和一轮几十分钟的
    # 抖音同步互相等），也不许被下面 `if not due: return` 挡掉——那正是暴雪公告八天没跑的原因
    for t in sync_tasks.due_tasks(cfg, now, alive, kind="source"):
        try:
            _run_source_task(t)
        except Exception as e:
            print("[自动任务] %s 起不来: %s" % (t.get("source"), e), flush=True)
    due = sync_tasks.due_tasks(cfg, now, alive)
    if not due:
        return
    if _sub_status.get("running"):
        _autosync_state["skipped"] = int(_autosync_state.get("skipped") or 0) + 1
        _autosync_state["note"] = "有同步正在跑，本轮自动任务顺延（到期时间不重置，不会漏）"
        return
    if not _claim_sync():
        return
    ids = sync_tasks.due_sub_ids(cfg, now, alive)
    names = "、".join(t.get("name") or t.get("key") for t in due)
    # 先把这一轮的时间戳占上再跑：一轮要几分钟，跑完才记账的话，中途配置被界面重写
    # 或进程重启就会丢账，同一个任务紧接着再跑一轮（对上游是骚扰）
    sync_tasks.touch_running([t["key"] for t in due])
    _autosync_state["note"] = "自动同步开始：" + names
    print("[自动同步] 到期任务 %d 个（%s），共 %d 个订阅" % (len(due), names, len(ids)), flush=True)
    _run_sync_batch(ids, [t["key"] for t in due], "自动同步")


def _autosync_loop():
    _autosync_state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("[自动同步] 定时器已启动（每 60 秒检查一次到期任务）", flush=True)
    while True:
        time.sleep(60)
        try:
            _autosync_tick()
        except Exception as e:
            # 这一层绝不能抛出去：线程死了自动同步就静默停摆，表现和"没配"一模一样
            print("[自动同步] 本轮检查异常（下一轮继续）: %s" % e, flush=True)


def _start_autosync():
    threading.Thread(target=_autosync_loop, daemon=True, name="autosync").start()


# ── 夜间素材匹配（2026-10-22 那一单：默认 22:00 北京时间，只刷未发布位置的候选）──
_planmatch_state = {"started_at": "", "last_check": "", "note": "", "running": False}


def _planmatch_tick(now=None):
    """每分钟看一眼：今晚该不该跑。到期判据与幂等全在 `plan_series.due_run`／`claim_run`。

    三条不许越界：不采集外站、不调付费模型、不自动采用/生成/出图/发布——
    这一轮只往 `plan_candidates` 里写候选（已发布与待核对的位置连候选都不写）。
    时钟必须由 `cn_now()` 出（Codex 二次复验第 5 条）：原来写 `now or datetime.now()`，
    交进去的是**本机 naive 时间**，而 cn_now 对 naive 一律"就当北京时间"不再换算——
    `TZ=UTC` 下同一瞬间本机 07:31 被当成北京 07:31，22:00 那一档整整偏 8 小时。
    测试要拿固定时钟就传明确的北京时间（naive），这里不认"机器时区"。
    """
    now = plan_series.cn_now(now)
    _planmatch_state["last_check"] = now.strftime("%Y-%m-%d %H:%M:%S")
    if _planmatch_state.get("running"):
        return              # 上一轮还在跑（一轮要扫全库），不叠第二遍
    try:
        due = plan_series.due_run(DB_PATH, now)
    except Exception as e:
        _planmatch_state["note"] = "到期判断失败（下一轮继续）：%s" % str(e)[:120]
        return
    if not due.get("due"):
        return
    _planmatch_state["running"] = True
    _planmatch_state["note"] = "开始匹配：%s" % due.get("why")
    print("[夜间匹配] %s" % due.get("why"), flush=True)

    def wrap():
        try:
            st = plan_series.run_match(DB_PATH, now=now, kind="night",
                                       actor="定时器%s" % ("（补跑）" if due.get("catchup") else ""))
            _planmatch_state["note"] = "匹配结束：新增 %s 条候选、重复命中 %s 条、跳过 %s 格" % (
                st.get("added"), st.get("seen"), st.get("skipped_n"))
            print("[夜间匹配] %s" % _planmatch_state["note"], flush=True)
        except Exception as e:
            # 抛出去会让线程静默停摆，表现和"没配"一模一样——记在状态里，界面上看得见
            _planmatch_state["note"] = "匹配失败：%s" % str(e)[:160]
            print("[夜间匹配] 失败：%s" % e, flush=True)
        finally:
            _planmatch_state["running"] = False

    threading.Thread(target=wrap, daemon=True, name="planmatch").start()


def _planmatch_loop():
    # 这条状态栏与到期判据共用同一把钟（北京时间），否则页面上会并排显示两个时区的时刻
    _planmatch_state["started_at"] = plan_series.cn_now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _db_lock:
            n = plan_series.sweep_stale_runs(DB_PATH)     # 上个进程留下的 running 先收尾
        if n:
            print("[夜间匹配] 中断核对：%d 轮 running 标为 failed" % n, flush=True)
    except Exception as e:
        print("[夜间匹配] 中断核对失败（不影响本轮）：%s" % e, flush=True)
    print("[夜间匹配] 定时器已启动（每 60 秒检查一次，默认 22:00 北京时间）", flush=True)
    while True:
        time.sleep(60)
        try:
            _planmatch_tick()
        except Exception as e:
            print("[夜间匹配] 本轮检查异常（下一轮继续）: %s" % e, flush=True)


def _start_planmatch():
    threading.Thread(target=_planmatch_loop, daemon=True, name="planmatch-loop").start()


def _alive_subs():
    return content_store.list_subscriptions(DB_PATH)


def _task_view(cfg, subs, now):
    """任务列表。成员的上次同步时间与新增条数取自 subscriptions 表——
    那里才是权威值（手动同步也会更新），JSON 里再存一份迟早对不上。"""
    by_id = {s["id"]: s for s in subs}
    run_at = cfg.get("run_at") or sync_tasks.DEFAULT_RUN_AT
    tasks = []
    for t in cfg.get("tasks") or []:
        members = []
        for sid in t.get("sub_ids") or []:
            s = by_id.get(sid)
            if not s:
                continue          # 订阅删了就不显示；_save_tasks 落盘时顺手清掉
            members.append({"id": sid,
                            "author": s.get("author") or s["sec_uid"][:12],
                            "platform": s.get("platform") or "douyin",
                            "category": s.get("category") or "",
                            "synced_at": s.get("last_synced_at") or "",
                            "new_count": s.get("last_new_count"),
                            "sync_note": s.get("last_sync_note") or ""})
        kind = str(t.get("kind") or "subs")
        if kind == "source":
            # 源类任务没有订阅成员。不单独走这一支的话，下面那句「成员空就 continue」
            # 会把它整条藏掉——号主既看不见它、也没法改频率或删掉，
            # 而它每天照常在后台跑（看不见又在动的配置最坑）。
            nxt = sync_tasks.next_due(t, run_at, now)
            tasks.append({"key": t["key"], "name": t.get("name") or "",
                          "kind": "source", "source": t.get("source") or "",
                          "source_label": sync_tasks.SOURCE_LABELS.get(
                              t.get("source") or "", t.get("source") or ""),
                          "interval_days": t.get("interval_days") or 1,
                          "run_at": sync_tasks.task_run_at(t, run_at),
                          "members": [], "count": 0, "platforms": [],
                          "last_run": t.get("last_run") or "",
                          "last_status": t.get("last_status") or "",
                          "last_note": t.get("last_note") or "",
                          "last_new": None,
                          "next_due": nxt.strftime("%Y-%m-%d %H:%M:%S") if nxt else "",
                          "due_now": bool(nxt and now >= nxt)})
            continue
        if not members:
            continue
        nxt = sync_tasks.next_due(t, run_at, now)
        tasks.append({"key": t["key"], "name": t.get("name") or "",
                      "interval_days": t.get("interval_days") or 1,
                      "run_at": sync_tasks.task_run_at(t, run_at),
                      "members": members, "count": len(members),
                      "platforms": sorted({m["platform"] for m in members}),
                      "last_run": t.get("last_run") or "",
                      "last_status": t.get("last_status") or "",
                      "last_note": t.get("last_note") or "",
                      "last_new": sum(int(m["new_count"] or 0) for m in members),
                      "next_due": nxt.strftime("%Y-%m-%d %H:%M:%S") if nxt else "",
                      "due_now": bool(nxt and now >= nxt)})
    tasks.sort(key=lambda x: x["next_due"] or "9999")
    return tasks


@app.route("/api/sync/tasks")
def api_sync_tasks():
    """自动同步任务列表 + 还没进任何任务的订阅候选。"""
    now = datetime.now()
    cfg = sync_tasks.load()
    try:
        subs = _alive_subs()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    picked = set()
    for t in cfg.get("tasks") or []:
        picked.update(t.get("sub_ids") or [])
    return jsonify({
        "enabled": bool(cfg.get("enabled")),
        "run_at": cfg.get("run_at") or sync_tasks.DEFAULT_RUN_AT,
        "intervals": list(sync_tasks.INTERVALS),
        "max_tasks": sync_tasks.MAX_TASKS,
        "tasks": _task_view(cfg, subs, now),
        "candidates": [{"id": s["id"], "author": s.get("author") or s["sec_uid"][:12],
                        "platform": s.get("platform") or "douyin",
                        "category": s.get("category") or ""}
                       for s in subs if s["id"] not in picked],
        "last_batch": cfg.get("last_batch"),
        "autosync": dict(_autosync_state),
        "sync_running": bool(_sub_status.get("running")),
        "sync_progress": _sub_status.get("progress") or "",
    })


def _save_tasks(cfg):
    """落盘界面那份改动：只合"意图"字段，账本以磁盘为准；顺手把已删订阅从成员里清掉。

    以前这里是"整份覆盖写"，而中间还要查一次数据库（同步在跑时会等 _db_lock 好几秒），
    等查完再写，就把这期间调度器记的账一起冲掉了——同一任务紧接着再跑一轮就是这么来的。
    """
    try:
        alive = {s["id"] for s in _alive_subs()}
    except Exception as e:
        # 读不到订阅表就别乱删配置，宁可留着僵尸成员（界面会跳过不显示）
        print("[自动同步] 订阅表读取失败，跳过成员清理: %s" % e, flush=True)
        alive = None
    return sync_tasks.save_intent(cfg, alive)


def _all_member_ids(cfg):
    return sync_tasks.all_member_ids(cfg)


def _valid_ids(sub_ids):
    """过滤成还存在的订阅 id，返回 (有效, 已不存在)。"""
    alive = {s["id"] for s in _alive_subs()}
    want = sync_tasks._clean_ids(sub_ids)
    return [i for i in want if i in alive], [i for i in want if i not in alive]


@app.route("/api/sync/tasks", methods=["POST"])
def api_sync_tasks_new():
    """新建一个任务：一组订阅 + 一个频率（用户勾的 6 个号是一个任务，不是 6 个）。"""
    data = request.get_json(force=True, silent=True) or {}
    ids, missing = _valid_ids(data.get("sub_ids"))
    if not ids:
        return jsonify({"error": "先勾选至少一条订阅"}), 400
    cfg = sync_tasks.load()
    t = sync_tasks.new_task(cfg, ids, data.get("interval_days") or 1,
                            str(data.get("name") or ""),
                            run_at=str(data.get("run_at") or ""))
    if not t:
        return jsonify({"error": "任务数已到 %d 个上限" % sync_tasks.MAX_TASKS}), 400
    _save_tasks(cfg)
    out = {"success": True, "key": t["key"], "name": t["name"], "count": len(t["sub_ids"])}
    if missing:
        out["missing"] = missing
    return jsonify(out)


@app.route("/api/sync/tasks/<key>", methods=["POST"])
def api_sync_tasks_update(key):
    """改任务：改名 / 改频率 / 改这一条的执行时刻 / 整体替换成员。"""
    data = request.get_json(force=True, silent=True) or {}
    cfg = sync_tasks.load()
    t = sync_tasks.get_task(cfg, key)
    if not t:
        return jsonify({"error": "任务不存在（可能刚被删掉）"}), 404
    sub_ids = None
    missing = []
    if data.get("sub_ids") is not None:
        sub_ids, missing = _valid_ids(data.get("sub_ids"))
        if not sub_ids:
            return jsonify({"error": "成员不能全空，要整个删掉请点任务的 ✕"}), 400
    run_at = None
    if data.get("run_at"):
        run_at = sync_tasks._norm_time(data.get("run_at"))
        if not run_at:
            return jsonify({"error": "执行时刻要写成 HH:MM"}), 400
    sync_tasks.update_task(cfg, key, name=data.get("name"),
                           interval_days=data.get("interval_days"), sub_ids=sub_ids,
                           run_at=run_at)
    _save_tasks(cfg)
    cur = sync_tasks.get_task(cfg, key) or {}
    nxt = sync_tasks.next_due(cur, cfg.get("run_at") or "", datetime.now()) if cur else None
    out = {"success": True, "key": key,
           "run_at": sync_tasks.task_run_at(cur, cfg.get("run_at") or ""),
           "next_due": nxt.strftime("%Y-%m-%d %H:%M") if nxt else ""}
    if missing:
        out["missing"] = missing
    return jsonify(out)


@app.route("/api/sync/tasks/<key>/add", methods=["POST"])
def api_sync_tasks_add_members(key):
    """往已有任务里补订阅。"""
    data = request.get_json(force=True, silent=True) or {}
    ids, missing = _valid_ids(data.get("sub_ids"))
    if not ids:
        return jsonify({"error": "先勾选至少一条订阅"}), 400
    cfg = sync_tasks.load()
    t = sync_tasks.add_members(cfg, key, ids)
    if not t:
        return jsonify({"error": "任务不存在（可能刚被删掉）"}), 404
    _save_tasks(cfg)
    out = {"success": True, "count": len(t["sub_ids"])}
    if missing:
        out["missing"] = missing
    return jsonify(out)


@app.route("/api/sync/tasks/<key>", methods=["DELETE"])
def api_sync_tasks_delete(key):
    """删任务：只删这条自动安排，订阅本身还在「我的订阅」里。"""
    cfg = sync_tasks.load()
    if not sync_tasks.get_task(cfg, key):
        return jsonify({"error": "这个任务已经不在了"}), 404
    _save_tasks(sync_tasks.remove_task(cfg, key))
    return jsonify({"success": True})


@app.route("/api/sync/tasks/<key>/subs/<int:sub_id>", methods=["DELETE"])
def api_sync_tasks_del_member(key, sub_id):
    """从任务里移出一个订阅；移出最后一个时任务本身也没了。"""
    cfg = sync_tasks.load()
    t = sync_tasks.remove_member(cfg, key, sub_id)
    _save_tasks(cfg)
    return jsonify({"success": True, "empty": t is None,
                    "count": len(t["sub_ids"]) if t else 0})


@app.route("/api/sync/tasks/settings", methods=["POST"])
def api_sync_tasks_settings():
    """总开关 + 新建任务的默认执行时刻。

    这里改时刻**不会**动已有任务：每条任务的时刻存在任务自己身上（`task["run_at"]`），
    全局这格只是下一条新任务的起点。原来它就是唯一的真相源，改一次三条任务的"下次"一起挪。
    """
    data = request.get_json(force=True, silent=True) or {}
    cfg = sync_tasks.load()
    if "enabled" in data:
        cfg["enabled"] = bool(data.get("enabled"))
    if data.get("run_at"):
        t = sync_tasks._norm_time(data.get("run_at"))
        if not t:
            return jsonify({"error": "执行时刻要写成 HH:MM"}), 400
        cfg["run_at"] = t
    if cfg.get("enabled") and not cfg.get("tasks"):
        return jsonify({"error": "先建至少一个任务，再打开总开关"}), 400
    _save_tasks(cfg)
    return jsonify({"success": True, "enabled": cfg["enabled"], "run_at": cfg["run_at"]})


@app.route("/api/sync/tasks/run", methods=["POST"])
def api_sync_tasks_run():
    """立刻跑一轮验证配置对不对，不用等明天。key＝只跑某个任务，all＝全跑，都不给＝跑到期的。"""
    data = request.get_json(force=True, silent=True) or {}
    cfg = sync_tasks.load()
    if not cfg.get("tasks"):
        return jsonify({"error": "还没有自动同步任务"}), 400
    alive = {s["id"] for s in _alive_subs()}
    if data.get("key"):
        t = sync_tasks.get_task(cfg, str(data.get("key")))
        if not t:
            return jsonify({"error": "任务不存在（可能刚被删掉）"}), 404
        targets = [t]
    elif data.get("all"):
        targets = list(cfg.get("tasks") or [])
    else:
        targets = sync_tasks.due_tasks(cfg, datetime.now(), alive)
    # 订阅类与源类分开走：源类不占订阅同步那把闸（抓一次官方公告约十秒），
    # 也不能混进 ids——混进去就会"任务数算它一个、实际什么都没跑"，
    # 更糟的是 ids 为空时整句直接报"没有到期的任务"，源类任务永远手动跑不动。
    src_targets = [t for t in targets if str(t.get("kind") or "subs") == "source"]
    sub_targets = [t for t in targets if str(t.get("kind") or "subs") != "source"]
    started_sources = []
    for t in src_targets:
        try:
            _run_source_task(t)
            started_sources.append(t.get("name") or t.get("source"))
        except Exception as e:
            return jsonify({"error": "源任务 %s 起不来：%s" % (t.get("source"), e)}), 500
    ids: list = []
    for t in sub_targets:
        for sid in sync_tasks.live_members(t, alive):
            if sid not in ids:
                ids.append(sid)
    if not ids:
        if started_sources:
            return jsonify({"success": True, "ids": [], "sources": started_sources,
                            "note": "这一轮只有源类任务：%s" % "、".join([s or "?" for s in started_sources])})
        return jsonify({"error": "现在没有到期的任务（点「全部跑一遍」可强制跑）"}), 400
    if not _claim_sync():
        return jsonify({"error": "订阅同步进行中，请稍候"}), 409
    _run_sync_batch(ids, [t["key"] for t in sub_targets], "手动触发自动任务")
    return jsonify({"success": True, "ids": ids, "tasks": len(sub_targets),
                    "sources": started_sources})



# ════════════════════════════════════════════════════════════════
# 删除：视频 / 面试题
# ════════════════════════════════════════════════════════════════

def _purge_library_rows(pairs):
    """清 Excel 行（只清内容不删行，行号保持稳定）+ 重建索引并摘掉这些条目。

    抽出来给两处用：素材库的「删除视频」和取消订阅的连带清理。
    原来这段逻辑只写在删除路由里，取消订阅要复用就得抄一遍——抄一份就会漂移。
    pairs: [(source_sheet, source_row), ...]；DB 直写的源（微信/B站）不在 Excel 里，自动跳过。
    """
    import openpyxl

    keyset = {(s, int(r)) for s, r in pairs if s and r}
    if not keyset:
        return
    excel_pairs = [(s, r) for (s, r) in keyset if s not in DB_ONLY_SHEETS]
    if excel_pairs:
        wb = openpyxl.load_workbook(str(EXCEL_PATH))
        target_sheets = {s for s, _ in excel_pairs}
        for ws in wb.worksheets:
            if ws.title not in target_sheets:
                continue
            for (s, r) in excel_pairs:
                if s == ws.title and 2 <= r <= ws.max_row:
                    for col in range(1, 16):
                        ws.cell(r, col).value = None
        wb.save(str(EXCEL_PATH))
        wb.close()
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
        _index_invalidate()      # 文件刚被自己改过，缓存里那份立刻作废


@app.route("/api/videos/delete", methods=["POST"])
def api_videos_delete():
    """删除视频：清 Excel 行（保持行号稳定）→ 删 SQLite 记录 → 重建索引。"""
    data = request.get_json(force=True)
    items = data.get("items") or []
    if not items:
        return jsonify({"error": "请选择要删除的视频"}), 400
    try:
        deleted = 0
        with _db_lock:
            # 0. 图文这一档先把本地图片的归属记下来——图是新引入的大块产物（一条 3~9 张、
            #    实测单张 100~200KB），删行不删图就只增不减。视频那侧的帧池/封面沿用原行为
            #    （这个删除路由本来就不碰它们），要把两类一起清是另一件事。
            note_ids = []
            for it in items:
                if (it.get("sheet") or "") != content_store.NOTE_SHEET:
                    continue
                v = content_store.get_video_by_source(
                    DB_PATH, it.get("sheet"), int(it.get("row") or 0))
                if v and v.get("aweme_id"):
                    note_ids.append(v["aweme_id"])
            # 1. 删 SQLite
            pairs = []
            for it in items:
                sheet, row = it.get("sheet"), int(it.get("row") or 0)
                if sheet and row:
                    content_store.delete_video(DB_PATH, sheet, row)
                    pairs.append((sheet, row))
                    if sheet in DB_ONLY_SHEETS:
                        deleted += 1  # DB 直写的源不落 Excel，删 SQLite 即计数
            # 2-3. 清 Excel 行 + 重建/摘除索引（与取消订阅共用一套动作）
            _purge_library_rows(pairs)
            deleted += sum(1 for s, _ in pairs if s not in DB_ONLY_SHEETS)
            # 4. 重同步内容库
            _sync_content_db()
        if note_ids:
            from src.douyin_notes import purge_note_images
            purged_dirs = purge_note_images(OUTPUT_DIR, note_ids)
            print(f"[delete] 图文图片目录清理 {purged_dirs}/{len(note_ids)} 个", flush=True)
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
    # 启动时幂等回填 author_sec_uid（按订阅作者名匹配存量视频；已填的不动）。
    # 顺带一件必须留着的事：这一句会开一次读写连接、把库结构（建表 + 补列）迁到位。
    # 读接口现在走 content_store.open_readonly()，只读连接不会自己补列，
    # 结构没新就报错——所以别把这句当可有可无的兼容代码删掉。
    try:
        if DB_PATH.exists():
            _n = content_store.backfill_author_sec_uid_from_subscriptions(DB_PATH)
            if _n:
                print(f"   [启动] 回填 author_sec_uid/source：{_n} 条")
    except Exception as _e:
        print(f"   [启动] author_sec_uid 回填失败（不影响启动）：{_e}")
    # 源类定时任务：暴雪官方公告。必须在启动时建好，不能等页面来拉——定时器 60 秒看一眼，
    # 任务不存在就等于"这条源没人管"，正是它八天没抓的原因（上游 10-22/10-24/10-27 连发三批）。
    try:
        _bt = _blizzard_task()
        if _bt:
            print("   [启动] 暴雪官方公告定时抓取：%s｜每 %s 天 %s"
                  % (_bt["key"], _bt.get("interval_days"), _bt.get("run_at")), flush=True)
    except Exception as _e:
        print(f"   [启动] 暴雪公告任务建不出来（手动抓仍可用）：{_e}")
    # 自动同步定时器：放在这里而不是模块级，是为了 import app（e2e 脚本）时不会多出一条线程
    try:
        _start_autosync()
    except Exception as _e:
        print(f"   [启动] 自动同步定时器启动失败（手动同步不受影响）：{_e}")
    # 双系列排产：先把位置注册到位，再开夜间匹配的定时器。
    # 为什么注册要放在启动里而不是等页面来拉：定时器 60 秒看一眼，若此刻 `plan_slots` 还是空的，
    # 它会"成功跑完一轮 0 个位置"并把今晚的账记掉——页面再打开时，当晚就不会再匹配了。
    try:
        if DB_PATH.exists():
            with _db_lock:
                _legacy = []
                try:
                    _legacy = plan_brief.load_data().get("items") or []
                except Exception as _e2:
                    print(f"   [启动] 旧排产名单读不到（{_e2}），只注册双系列位置")
                _seeded = plan_series.seed(DB_PATH, _legacy)
            print("   [启动] 排产位置注册：%d 个在册、新增 %d 个"
                  % (_seeded["registered"], _seeded["added"]))
        _start_planmatch()
    except Exception as _e:
        print(f"   [启动] 夜间匹配启动失败（页面手动匹配不受影响）：{_e}")
    app.run(host="127.0.0.1", port=15801, debug=False)
