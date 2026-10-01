# -*- coding: utf-8 -*-
"""AI 海报链路 e2e：整理稿 → LLM 拆解 → 出图 → 回读校验 + 视觉评审，全链路跑一条真实素材。

用法：
    python3 scripts/e2e_ai_poster.py [summary_id]              # 默认取最新 wechat_material 整合稿
    python3 scripts/e2e_ai_poster.py [id] plan                 # 只跑拆解，不花钱出图
    python3 scripts/e2e_ai_poster.py [id] model                # 走"模型画字"老路（会错字，对比用）
    python3 scripts/e2e_ai_poster.py [id] typeset [3]          # 默认模式；第三参数=出几张
退出码 0 且末行 ALL_OK 才算通过。会往 output/poster/<id>/ 写产物并保留（供人眼复核）。
"""
import json
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
    fast = (kv.get("AI_MODEL_FAST") or "").strip()
    if method == "deepseek":
        return {"method": method, "api_key": (kv.get("DEEPSEEK_API_KEY") or "").strip(),
                "api_base": (kv.get("DEEPSEEK_API_BASE") or "https://api.deepseek.com").strip(),
                "model": (kv.get("DEEPSEEK_MODEL") or "deepseek-chat").strip(), "fast_model": fast}
    return {"method": method, "api_key": (kv.get("OPENAI_API_KEY") or kv.get("AI_API_KEY") or "").strip(),
            "api_base": (kv.get("AI_API_BASE") or "https://api.openai.com/v1").strip(),
            "model": (kv.get("AI_MODEL") or "gpt-4o-mini").strip(), "fast_model": fast}


def work_config(cfg):
    """对齐生产：拆解这类活走快速档（web/app.py 的 _fast_config 同一套规则）。"""
    fast = (cfg.get("fast_model") or "").strip()
    return dict(cfg, model=fast) if fast else cfg


def pick_summary(conn, sid):
    if sid:
        row = conn.execute("SELECT * FROM ai_summaries WHERE id=?", (int(sid),)).fetchone()
    else:
        row = conn.execute("SELECT * FROM ai_summaries WHERE summary_type='wechat_material' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def main():
    sid = sys.argv[1] if len(sys.argv) > 1 else ""
    arg2 = sys.argv[2] if len(sys.argv) > 2 else ""
    plan_only = arg2 == "plan"
    text_mode = arg2 if arg2 in ai_poster.TEXT_MODES else ai_poster.DEFAULT_TEXT_MODE
    try:
        copies = max(1, min(ai_poster.MAX_COPIES, int(sys.argv[3]))) if len(sys.argv) > 3 else 1
    except ValueError:
        copies = 1
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    summary = pick_summary(conn, sid)
    if not summary:
        print("FAIL 找不到整合稿（%s）" % (sid or "最新 wechat_material"))
        return 1
    ai_config = work_config(load_ai_config())
    print("整合稿 #%s《%s》 拆解模型=%s" % (summary["id"], summary["title"][:30], ai_config["model"]))

    print("排版模式=%s 出图张数=%d" % (text_mode, copies))
    if plan_only:
        plan, publish, bad, notes = ai_poster.build_text_plan(summary, ai_config, text_mode=text_mode)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        print("violations=%s notes=%s visual=%s" % (bad, notes, ai_poster.lint_visual(plan)))
        art = ai_poster.build_art_prompt(plan) if text_mode == "typeset" else ai_poster.build_prompt_text(plan)
        print("---- PROMPT ----\n%s" % art)
        if bad:
            print("FAIL 文案不合格")
            return 1
        print("ALL_OK")
        return 0

    manifest = ai_poster.generate_poster(
        summary, ai_config, progress_cb=lambda m: print("  ·", m),
        text_mode=text_mode, copies=copies, force=("force" in sys.argv))
    print("产物：%s" % (ai_poster.OUTPUT_DIR / str(summary["id"])))
    print("文案计划：%s / %s / %d 张卡片，合计 %d 字"
          % (manifest["plan"]["title"], manifest["plan"]["subtitle"],
             len(manifest["plan"]["cards"]), manifest["text_chars"]))

    errs = []
    if not (ai_poster.OUTPUT_DIR / str(summary["id"]) / "poster.png").exists():
        errs.append("poster.png 未落盘")
    lim = ai_poster.limits_for(manifest.get("text_mode") or text_mode)
    if manifest["text_chars"] > lim["total"]:
        errs.append("字数门禁失效：%d 字 > %d" % (manifest["text_chars"], lim["total"]))
    # 画面计划与风格键：这两个空了就等于退回"一屏文字框"，必须一起断言
    if manifest["plan"].get("style_key") not in ai_poster.STYLE_PRESETS:
        errs.append("style_key 不在预设库里：%r" % manifest["plan"].get("style_key"))
    if not (manifest["plan"].get("scene") or "").strip():
        errs.append("拆解没产出主视觉 scene")
    # 卡片配图只在"模型画字"下是硬要求：那时它决定卡片里画什么图标。
    # 程序排字模式下卡片位置由代码定，v 只当主视觉的道具线索，缺了不影响成品。
    if manifest.get("text_mode") != "typeset" \
            and not [c for c in manifest["plan"]["cards"] if (c.get("v") or "").strip()]:
        errs.append("拆解没给任何卡片配图 v")
    v = manifest.get("verify") or {}
    ts_chk = manifest.get("typeset") or {}
    if manifest.get("text_mode") == "typeset":
        if not ts_chk:
            errs.append("程序排字没跑（manifest.typeset 为空）")
        if ts_chk.get("problems"):
            errs.append("版面放不下：%s" % "；".join(ts_chk["problems"]))
    elif v.get("ok") is not True:
        errs.append("回读校验未通过：missing=%s extra=%s" % (v.get("missing"), v.get("extra")))
    if v.get("skipped") and not v.get("not_needed"):
        print("  ! 回读校验被跳过（%s），本次仅出图，需人眼复核" % v.get("error", "未启用"))
    r = manifest.get("review") or {}
    if r.get("skipped"):
        errs.append("视觉评审没跑成：%s" % (r.get("error") or "已跳过"))
    elif int(r.get("score") or 0) < ai_poster.REVIEW_PASS:
        errs.append("视觉评审 %s/%s 低于可发线 %d（最弱 %s：%s）"
                    % (r.get("score"), r.get("full"), ai_poster.REVIEW_PASS,
                       r.get("worst"), r.get("note")))
    print("版面=%s 回读=%s 评审=%s/%s 画面%s%% 风格=%s"
          % (ts_chk.get("ok") if ts_chk else "-", v.get("ok"), r.get("score"), r.get("full"),
             r.get("visual_pct"), manifest.get("style_label") or manifest.get("style_key")))

    if errs:
        print("FAIL " + "；".join(errs))
        return 1
    print("图面文字与文案计划一致" if manifest.get("text_mode") == "typeset"
          else "图面文字与文案计划逐字一致")
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
