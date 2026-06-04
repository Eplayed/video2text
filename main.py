#!/usr/bin/env python3
"""
抖音视频采集器 — 主程序
用法：
  python main.py --excel /path/to/抖音视频信息.xlsx --cookie "sessionid=xxx"
  python main.py --excel /path/to/抖音视频信息.xlsx --cookie-file /path/to/cookie.txt
  python main.py --url "https://v.douyin.com/xxx" --cookie "sessionid=xxx"
"""

import argparse
import os
import sys
import re
import time
import json
import tempfile
import subprocess
from datetime import datetime

# 第三方库
try:
    import openpyxl
except ImportError:
    print("需要 openpyxl: pip install openpyxl")
    sys.exit(1)

# 项目模块
sys.path.insert(0, "/tmp/douyin_parse")
from douyin_video_parser import DouyinVideoParser

# faster-whisper（需 KMP_DUPLICATE_LIB_OK=TRUE）
try:
    from faster_whisper import WhisperModel
    HAS_FASTER_WHISPER = True
except ImportError:
    HAS_FASTER_WHISPER = False
    import whisper

# ── Excel 列索引（1-based）───────────────────────────────────────
COL = {
    "链接": 1, "状态": 2, "视频ID": 3, "作者": 4, "发布时间": 5,
    "标题": 6, "视频文案ASR": 7, "标签": 8, "封面URL": 9,
    "视频链接": 10, "口播原文": 11, "AI优化文案": 12,
    "AI备选标题": 13, "关键词摘要": 14, "备注": 15,
}

STATUS = {"未开始": "未开始", "处理中": "处理中",
          "已完成": "已完成", "失败": "失败"}


# ── 链接解析 ─────────────────────────────────────────────────────
def resolve_url(url: str) -> str:
    """跟随重定向，返回真实视频URL"""
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    if not url:
        return ""
    try:
        import requests
        r = requests.head(url, timeout=10, allow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})
        return r.url
    except Exception:
        return url


def extract_aweme_id(url: str) -> str:
    """从URL中提取 aweme_id"""
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    return ""


# ── 视频元数据提取 ───────────────────────────────────────────────
def fetch_video_info(url_or_id: str, parser: DouyinVideoParser) -> dict:
    """调用 douyin_parse，返回结构化 dict"""
    try:
        raw = parser.parse_video(url_or_id)
        desc = raw.get("desc", "")
        hashtags = []
        if "#" in desc:
            for part in desc.split("#")[1:]:
                tag = part.strip().split()[0]
                if tag:
                    hashtags.append(tag)

        ts = raw.get("create_time", 0)
        create_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""

        video_obj = raw.get("video", {})
        statistics = raw.get("statistics", {})

        return {
            "aweme_id": raw.get("aweme_id", ""),
            "author": raw.get("author_nickname", ""),
            "author_sec_uid": raw.get("author_sec_uid", ""),
            "title": desc.split("#")[0].strip(),
            "desc": desc,
            "hashtags": " ".join(hashtags),
            "create_time": create_str,
            "duration": video_obj.get("duration"),
            "like_count": statistics.get("digg_count"),
            "comment_count": statistics.get("comment_count"),
            "share_count": statistics.get("share_count"),
            "collect_count": statistics.get("collect_count"),
            "cover_url": raw.get("cover_url", ""),
            "video_url": raw.get("nwm_url", ""),
            "real_url": f"https://www.douyin.com/video/{raw.get('aweme_id', '')}",
            "error": None,
        }
    except Exception as e:
        return {"error": str(e)}


# ── ASR 转写 ─────────────────────────────────────────────────────
def asr_transcribe(video_url: str, model_size: str = "base",
                    language: str = "zh") -> str:
    """下载视频 → 提取音频 → faster-whisper 转写"""
    tmp_dir = tempfile.mkdtemp(prefix="dy_")
    video_path = os.path.join(tmp_dir, "video.mp4")
    audio_path = os.path.join(tmp_dir, "audio.wav")

    # 下载视频
    try:
        subprocess.run(
            ["curl", "-L", "-o", video_path,
             "-H", "Referer: https://www.douyin.com/",
             video_url],
            capture_output=True, check=True, timeout=120
        )
    except Exception as e:
        return f"[下载失败] {e}"

    if not os.path.exists(video_path) or os.path.getsize(video_path) < 1024:
        return "[下载失败] 文件无效"

    # 提取音频
    try:
        subprocess.run(
            ["ffmpeg", "-i", video_path,
             "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1",
             "-y", audio_path],
            capture_output=True, check=True, timeout=60
        )
    except Exception as e:
        return f"[音频提取失败] {e}"

    if not os.path.exists(audio_path):
        return "[音频提取失败] 文件不存在"

    # 转写
    try:
        if HAS_FASTER_WHISPER:
            env = os.environ.copy()
            env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments, info = model.transcribe(
                audio_path, language=language,
                vad_filter=True, beam_size=1
            )
            text = "".join(s.text for s in segments)
        else:
            model = whisper.load_model(model_size)
            result = model.transcribe(audio_path, language=language)
            text = result["text"].strip()
    except Exception as e:
        return f"[转写失败] {e}"
    finally:
        # 清理临时文件
        for f in [video_path, audio_path]:
            if os.path.exists(f):
                os.remove(f)
        if os.path.exists(tmp_dir):
            os.rmdir(tmp_dir)

    return text


# ── AI 优化文案 ──────────────────────────────────────────────────
def ai_optimize(title: str, desc: str, author: str,
                transcript: str, config: dict) -> dict:
    """调用 LLM API 优化文案"""
    if config.get("method") == "skip" or not config.get("api_key"):
        return {}

    prompt = f"""你是短视频内容运营专家。请基于以下抖音视频信息，整理并优化文案。

标题：{title}
描述：{desc}
作者：{author}
口播识别文本：{transcript}

输出JSON（不要输出多余文字）：
{{
  "clean_transcript": "清洗后口播文案",
  "optimized_copy": "优化完整文案",
  "short_copy": "精简版100字内",
  "title_options": ["标题1","标题2","标题3","标题4","标题5"],
  "summary": "摘要50字内",
  "keywords": ["关键词1","关键词2"],
  "risk_words": ["敏感词"],
  "recommended_tags": ["标签1","标签2"]
}}"""

    try:
        import openai
        client = openai.OpenAI(
            api_key=config["api_key"],
            base_url=config.get("api_base", "https://api.openai.com/v1")
        )
        resp = client.chat.completions.create(
            model=config.get("model", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        return {"error": str(e)}


# ── 写 Excel ─────────────────────────────────────────────────────
def set_cell(ws, row: int, col_name: str, value):
    ws.cell(row, COL[col_name], value)


def process_row(ws, row_idx: int, cookie: str,
                asr_model: str, ai_config: dict,
                parser: DouyinVideoParser) -> bool:
    """处理单行，返回是否成功"""
    url = (ws.cell(row_idx, COL["链接"]).value or "").strip()
    if not url:
        return False

    # 标记处理中
    ws.cell(row_idx, COL["状态"], STATUS["处理中"])
    print(f"[Row {row_idx}] 处理中: {url[:60]}")

    # 1. 解析真实URL
    real_url = resolve_url(url)
    aweme_id = extract_aweme_id(real_url)
    if not aweme_id:
        ws.cell(row_idx, COL["状态"], STATUS["失败"])
        ws.cell(row_idx, COL["备注"], f"无法提取视频ID: {real_url}")
        return False

    # 2. 获取元数据（传完整URL而不是纯ID）
    info = fetch_video_info(real_url, parser)
    if info.get("error"):
        ws.cell(row_idx, COL["状态"], STATUS["失败"])
        ws.cell(row_idx, COL["备注"], info["error"])
        return False

    # 写入元数据列
    set_cell(ws, row_idx, "视频ID", info["aweme_id"])
    set_cell(ws, row_idx, "作者", info["author"])
    set_cell(ws, row_idx, "发布时间", info["create_time"])
    set_cell(ws, row_idx, "标题", info["title"])
    set_cell(ws, row_idx, "标签", info["hashtags"])
    set_cell(ws, row_idx, "封面URL", info["cover_url"])
    set_cell(ws, row_idx, "视频链接", info["real_url"])

    # 3. ASR 转写（如果还没有文案）
    existing_asr = (ws.cell(row_idx, COL["口播原文"]).value or "").strip()
    if not existing_asr:
        print(f"[Row {row_idx}] 开始ASR转写...")
        transcript = asr_transcribe(info["video_url"], model_size=asr_model)
        if transcript and not transcript.startswith("[") and not transcript.startswith("下载失败"):
            set_cell(ws, row_idx, "视频文案ASR", transcript)
            set_cell(ws, row_idx, "口播原文", transcript)
        else:
            ws.cell(row_idx, COL["备注"], transcript)
            print(f"[Row {row_idx}] ASR失败: {transcript}")

    # 4. AI 优化（如果配置了 API）
    if ai_config.get("api_key") and not ai_config.get("method") == "skip":
        existing = (ws.cell(row_idx, COL["AI优化文案"]).value or "").strip()
        if not existing:
            transcript = ws.cell(row_idx, COL["口播原文"]).value or ""
            result = ai_optimize(info["title"], info["desc"],
                                  info["author"], transcript, ai_config)
            if "error" not in result:
                set_cell(ws, row_idx, "AI优化文案", result.get("optimized_copy", ""))
                set_cell(ws, row_idx, "AI备选标题", " ".join(result.get("title_options", [])))
                set_cell(ws, row_idx, "关键词摘要", " ".join(result.get("keywords", [])))
            else:
                ws.cell(row_idx, COL["备注"], f"AI错误: {result.get('error')}")

    # 标记完成
    ws.cell(row_idx, COL["状态"], STATUS["已完成"])
    print(f"[Row {row_idx}] ✅ 完成")
    return True


# ── 主流程 ───────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="抖音视频采集器")
    parser.add_argument("--sheet", default="抖音视频数据", help="Excel sheet名称")
    parser.add_argument("--excel", default="/Users/zhangyajun/Desktop/抖音视频信息.xlsx")
    parser.add_argument("--cookie", help="完整 cookie 字符串，或 sessionid=xxx")
    parser.add_argument("--cookie-file", help="从文件读取 cookie")
    parser.add_argument("--row", type=int, help="只处理指定行号")
    parser.add_argument("--asr-model", default="base",
                        choices=["tiny","base","small","medium","large"])
    parser.add_argument("--ai-method", default="skip",
                        choices=["skip","openai","deepseek"])
    parser.add_argument("--ai-key", help="API Key")
    parser.add_argument("--ai-base", default="https://api.deepseek.com/v1")
    parser.add_argument("--ai-model", default="deepseek-chat")
    parser.add_argument("--interval", type=int, default=5,
                        help="每个视频间隔秒数")
    parser.add_argument("--update-index", action="store_true", default=True,
                        help="处理完成后自动更新 video_index.json")
    args = parser.parse_args()

    # 读取 Cookie
    if args.cookie_file:
        cookie = open(args.cookie_file).read().strip()
    elif args.cookie:
        cookie = args.cookie
    else:
        print("错误：需要 --cookie 或 --cookie-file")
        sys.exit(1)

    # 写入 cookie 文件（供 douyin_parse 使用）
    cookie_path = "/tmp/douyin_parse/douyin_cookie.txt"
    with open(cookie_path, "w") as f:
        f.write(cookie)
    print(f"Cookie 已写入 {cookie_path}")

    # 切换到 douyin_parse 目录，让 parser 能找到 cookie 文件
    import os
    os.chdir("/tmp/douyin_parse")
    # 初始化 parser
    parser_dy = DouyinVideoParser()

    # AI 配置
    ai_config = {
        "method": args.ai_method,
        "api_key": args.ai_key or os.environ.get("OPENAI_API_KEY", ""),
        "api_base": args.ai_base,
        "model": args.ai_model,
    }

    # 打开 Excel
    if not os.path.exists(args.excel):
        print(f"Excel 文件不存在: {args.excel}")
        sys.exit(1)

    wb = openpyxl.load_workbook(args.excel)
    ws = wb[args.sheet] if args.sheet in wb.sheetnames else wb.active
    print(f"已打开 Excel：{args.excel}，共 {ws.max_row} 行")

    # 确定要处理的行
    if args.row:
        rows_to_process = [args.row]
    else:
        rows_to_process = [
            r for r in range(2, ws.max_row + 1)
            if (ws.cell(r, COL["状态"]).value or "未开始") in
               ("未开始", "失败")
            and (ws.cell(r, COL["链接"]).value or "").strip()
        ]

    if not rows_to_process:
        print("没有需要处理的链接（全部已完成或无链接）")
        sys.exit(0)

    print(f"共 {len(rows_to_process)} 条待处理")
    for idx, row in enumerate(rows_to_process):
        print(f"\n── 处理进度 {idx+1}/{len(rows_to_process)} ──")
        ok = process_row(ws, row, cookie, args.asr_model,
                         ai_config, parser_dy)
        # 保存进度（每条都保存）
        wb.save(args.excel)
        if idx < len(rows_to_process) - 1:
            print(f"等待 {args.interval} 秒...")
            time.sleep(args.interval)

    print(f"\n✅ 全部处理完成！Excel：{args.excel}")



    # ── 更新视频索引 ──────────────────────────────────────────────
    if args.update_index:
        update_video_index(args.excel)


def update_video_index(excel_path: str):
    """
    读取 Excel 中所有已完成（含 ASR 文案）的视频，
    生成描述并写入 video_index.json。
    追加模式：新视频追加，已有的（相同 sheet+row）则覆盖。
    """
    import re, json
    from pathlib import Path

    INDEX_PATH = Path(__file__).parent / "video_index.json"  # 随仓库存放

    # 读取现有索引（用于去重）
    existing = {}
    if INDEX_PATH.exists():
        with open(INDEX_PATH, encoding="utf-8") as f:
            old = json.load(f)
        for v in old.get("videos", []):
            key = f"{v.get('sheet')}:{v.get('row')}"
            existing[key] = v

    wb = openpyxl.load_workbook(excel_path)
    updated_count = 0

    for ws in wb.worksheets:
        for r in range(2, ws.max_row + 1):
            status = ws.cell(r, COL.get("状态", 2)).value or ""
            if status not in ("已完成", "已写文稿"):
                continue
            asr = (ws.cell(r, COL.get("视频文案ASR", 7)).value or
                   ws.cell(r, COL.get("口播原文", 11)).value or "").strip()
            if not asr:
                continue

            title   = ws.cell(r, COL.get("标题", 6)).value or ""
            author  = ws.cell(r, COL.get("作者", 4)).value or ""
            tags    = ws.cell(r, COL.get("标签", 8)).value or ""
            vid     = ws.cell(r, COL.get("视频ID", 3)).value or ""
            ctime  = ws.cell(r, COL.get("发布时间", 5)).value or ""

            key = f"{ws.title}:{r}"
            existing[key] = {
                "id": str(vid),
                "sheet": ws.title,
                "row": r,
                "author": author,
                "title": title[:100],
                "tags": tags,
                "create_time": str(ctime)[:10] if ctime else "",
                "asr_snippet": asr[:400],   # 暂存，AI 下次提炼描述
                "status": status,
            }
            updated_count += 1

    # 启发式描述生成（LLM 提炼前先用规则顶一下）
    def _make_desc(v: dict) -> str:
        title  = v.get("title", "")
        author = v.get("author", "")
        tags   = v.get("tags", "")
        asr    = v.get("asr_snippet", "")[:400]
        combined = title + tags + asr

        if "鹏宇" in author or any(k in title for k in ["RAG", "提示词", "微调", "AI"]):
            cat = "AI技术教程"
        elif any(k in combined for k in ["流放", "POE", "BD", "天赋", "通货", "异界", "开荒"]):
            cat = "流放2攻略"
        else:
            cat = "视频内容"

        # 提取中文句子作为摘要
        sents = re.findall(r'[一-鿿][^。！？.\n]{4,50}[。！？.\n]?', asr)
        snippet = sents[0][:70].strip() if sents else asr[:70].strip()
        return f"【{cat}】{snippet}"

    videos = list(existing.values())
    for v in videos:
        v["description"] = _make_desc(v)
        v.pop("asr_snippet", None)

    new_index = {
        "name": "抖音视频内容库",
        "description": "抖音视频文案的轻量索引，包含标题和一句话描述，用于快速检索视频内容。",
        "version": "1.0",
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(videos),
        "videos": sorted(videos, key=lambda x: (x.get("sheet", ""), x.get("row", 0))),
    }

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(new_index, f, ensure_ascii=False, indent=2)

    print(f"\n📋 索引已更新：{updated_count} 条视频 → {INDEX_PATH}")
    print(f"   共 {len(videos)} 条。描述为初版，后续对话中可让 AI 优化。")


if __name__ == "__main__":
    main()
