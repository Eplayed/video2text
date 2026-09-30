# -*- coding: utf-8 -*-
"""AI 海报链路 e2e：整理稿 → LLM 拆解 → 出图 → OCR 回读校验，全链路跑一条真实素材。

用法：
    python3 scripts/e2e_ai_poster.py [summary_id]     # 默认取最新的 wechat_material 整合稿
退出码 0 且末行 ALL_OK 才算通过。会往 output/poster/<id>/ 写产物并保留（供人眼复核）。
"""
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import ai_poster  # noqa: E402

DB_PATH = ROOT / "output" / "video2text.db"
ENV_PATH = ROOT / "config" / "config.env.local"


def load_ai_config():
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    kv = dict(re.findall(r"^([A-Z_]+)\s*=\s*(.*)$", text, re.M))
    method = (kv.get("AI_METHOD") or "skip").strip()
    if method == "deepseek":
        return {"method": method, "api_key": (kv.get("DEEPSEEK_API_KEY") or "").strip(),
                "api_base": (kv.get("DEEPSEEK_API_BASE") or "https://api.deepseek.com").strip(),
                "model": (kv.get("DEEPSEEK_MODEL") or "deepseek-chat").strip()}
    return {"method": method, "api_key": (kv.get("OPENAI_API_KEY") or kv.get("AI_API_KEY") or "").strip(),
            "api_base": (kv.get("AI_API_BASE") or "https://api.openai.com/v1").strip(),
            "model": (kv.get("AI_MODEL") or "gpt-4o-mini").strip()}


def pick_summary(conn, sid):
    if sid:
        row = conn.execute("SELECT * FROM ai_summaries WHERE id=?", (int(sid),)).fetchone()
    else:
        row = conn.execute("SELECT * FROM ai_summaries WHERE summary_type='wechat_material' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def main():
    sid = sys.argv[1] if len(sys.argv) > 1 else ""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    summary = pick_summary(conn, sid)
    if not summary:
        print("FAIL 找不到整合稿（%s）" % (sid or "最新 wechat_material"))
        return 1
    ai_config = load_ai_config()
    print("整合稿 #%s《%s》 模型=%s" % (summary["id"], summary["title"][:30], ai_config["model"]))

    manifest = ai_poster.generate_poster(
        summary, ai_config, progress_cb=lambda m: print("  ·", m))
    print("产物：%s" % (ai_poster.OUTPUT_DIR / str(summary["id"])))
    print("文案计划：%s / %s / %d 张卡片，合计 %d 字"
          % (manifest["plan"]["title"], manifest["plan"]["subtitle"],
             len(manifest["plan"]["cards"]), manifest["text_chars"]))

    errs = []
    if not (ai_poster.OUTPUT_DIR / str(summary["id"]) / "poster.png").exists():
        errs.append("poster.png 未落盘")
    if manifest["text_chars"] > ai_poster.MAX_TOTAL_CHARS:
        errs.append("字数门禁失效：%d 字 > %d" % (manifest["text_chars"], ai_poster.MAX_TOTAL_CHARS))
    v = manifest.get("verify") or {}
    if v.get("ok") is not True:
        errs.append("OCR 回读未通过：missing=%s extra=%s" % (v.get("missing"), v.get("extra")))
    if v.get("skipped"):
        print("  ! OCR 校验被跳过（%s），本次仅出图，需人眼复核" % v.get("error", "未启用"))

    if errs:
        print("FAIL " + "；".join(errs))
        return 1
    print("图面文字与文案计划逐字一致")
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
