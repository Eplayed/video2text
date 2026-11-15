# -*- coding: utf-8 -*-
"""订阅采集的作者归属回归（纯本地，不起浏览器、不碰上游、不调付费模型）。

为什么有这一份：2026-11-01 早上订阅「尤安的魔兽世界」进来 7 条，全是别人的老片子
（荔枝新闻、黑洞科普、2022 年的绿卡访谈…），每条白付一次下载 + ASR，还被盖上尤安的
author_sec_uid 进了素材库。同一件事 2026-10-16 在「笨小孩」上发作过一次，当时只把
DOM 降级成兜底，**兜底那条本身没有关**——于是今天又漏。所以两道闸都要钉：

  闸①（来源）src/browser_fetch：主页 ids 只认页面自取的 aweme/post 那一份，
        DOM 兜底刮到的不算这个号的新视频；图文 notes 是接口带的，不许被牵连。
  闸②（归属）main.process_row：详情回的 author_sec_uid 与订阅 sec_uid 对不上时，
        在 ASR 之前判失败——省的就是这一行最贵的那段。手动粘链接不传这个参数，
        那条路本来就该收任何作者的作品（反面样本第 C 条钉住）。

用法：python3 scripts/e2e_douyin_owner_guard.py      末行 ALL_OK 才算通过
"""
import io
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402

from src import browser_fetch as bf  # noqa: E402
import main as collector  # noqa: E402

FAILS = []
MINE = "MS4wLjABAAAAtczjCHYfy_AASYK0opFPFdubk-3yXqO36YSCQvHrV-LNAb_9qMgXWMGUNNA4FP4h"
FOREIGN = "MS4wLjABAAAA_foreign_author_sec_uid"


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + detail) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + detail) if detail else ""))
        FAILS.append(name)


# ── 闸①：来源判据（纯函数 + 批量解析回路）────────────────────────────────
def test_provenance():
    print("\n== 1. 主页列表只认接口那一份 ==")
    ck("api 那份照收",
       bf.verified_ids({"ids": ["a1", "a2"], "ids_from": "api"}) == ["a1", "a2"], "")
    ck("dom 那份一条都不收（正面：闸门关掉）",
       bf.verified_ids({"ids": ["a1", "a2"], "ids_from": "dom"}) == [], "")
    ck("缺 ids_from 也当不可信（老脚本没带这字段时不许放行）",
       bf.verified_ids({"ids": ["a1"]}) == [], "")
    ck("空列表不该被误报成『只拿到 DOM』（反面样本）",
       bf.ids_from_dom_only({"ids": [], "ids_from": "dom"}) is False, "")
    ck("ids_from=api 时不算 DOM 兜底（反面样本）",
       bf.ids_from_dom_only({"ids": ["a1"], "ids_from": "api"}) is False, "")

    lines = [
        json.dumps({"ok": True, "id": "18", "ids": ["x1", "x2"], "notes": [],
                    "ids_from": "dom"}),
        json.dumps({"ok": True, "id": "19", "ids": ["y1"], "notes": [],
                    "ids_from": "api"}),
        json.dumps({"ok": True, "id": "21", "ids": ["z1"],
                    "notes": [{"aweme_id": "n1", "images": [{"url": "u"}]}],
                    "ids_from": "dom"}),
    ]
    seen = []
    orig_popen = bf.subprocess.Popen
    orig_nodes = bf._NODE_CANDIDATES
    try:
        bf._NODE_CANDIDATES = [sys.executable]
        bf.subprocess.Popen = lambda *a, **k: types.SimpleNamespace(
            stdout=io.StringIO("\n".join(lines)),
            kill=lambda: None, wait=lambda: 0)
        notes_out = {}
        res = bf.browser_fetch_users_batch(
            [{"id": "18", "url": "u"}, {"id": "19", "url": "u"}, {"id": "20", "url": "u"}],
            timeout=1, notes_out=notes_out,
            on_progress=lambda d, t, i, ids, err: seen.append((str(i), ids, err)))
    finally:
        bf.subprocess.Popen = orig_popen
        bf._NODE_CANDIDATES = orig_nodes

    ck("DOM 那一份不进结果（调用方据此退回按 sec_uid 取的旧 API）",
       "18" not in res, str(res))
    ck("api 那一份照常收到", res.get("19") == ["y1"], str(res))
    ck("界面要说清为什么这条没覆盖到（不是静默丢）",
       any(i == "18" and ids is None and "DOM" in str(err) for i, ids, err in seen),
       str(seen))
    ck("只有 DOM 视频但带图文的号：图文保留、视频不算",
       notes_out.get("21") == [{"aweme_id": "n1", "images": [{"url": "u"}]}]
       and res.get("21", ["-"]) == [], "notes=%s res=%s" % (list(notes_out), res))


# ── 闸②：process_row 的归属硬闸（临时工作簿 + 桩）───────────────────────
def _info(sec_uid, aweme="7000000000000000001"):
    return {"aweme_id": aweme, "author": "荔枝新闻", "author_sec_uid": sec_uid,
            "title": "樱花场景", "desc": "樱花场景", "hashtags": "",
            "create_time": "2026-04-02 18:00:34", "duration": 30,
            "like_count": 1, "comment_count": 0, "share_count": 0,
            "collect_count": 0, "cover_url": "http://x/c.jpg",
            "video_url": "http://x/v.mp4", "real_url": "http://x", "error": None}


def test_owner_gate():
    print("\n== 2. 作者对不上就不付转写钱 ==")
    calls = []
    orig = (collector.resolve_url, collector.fetch_video_info, collector.asr_transcribe)
    book = {}

    def run_case(expect, sec_uid, url="https://www.douyin.com/video/7000000000000000001"):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "抖音视频数据"
        for name, idx in collector.COL.items():
            ws.cell(1, idx).value = name
        ws.cell(2, collector.COL["链接"]).value = url
        ws.cell(2, collector.COL["状态"]).value = "未开始"
        book["info"] = _info(sec_uid) if sec_uid is not None else _info(MINE)
        del calls[:]
        ok = collector.process_row(ws, 2, "cookie", "small",
                                   {"method": "skip", "api_key": ""}, None,
                                   expect_sec_uid=expect)
        return ok, ws, list(calls)

    collector.resolve_url = lambda u: u
    collector.fetch_video_info = lambda u, p: book["info"]
    collector.asr_transcribe = lambda *a, **k: (calls.append(a), "转写正文")[1]
    try:
        ok, ws, asr = run_case(MINE, FOREIGN)
        ck("作者不属于本订阅 → 判失败", ok is False, "返回 %s" % ok)
        ck("状态写的是失败而不是已完成",
           ws.cell(2, collector.COL["状态"]).value == collector.STATUS["失败"],
           str(ws.cell(2, collector.COL["状态"]).value))
        ck("备注点名实际作者（人能看到是谁的）",
           "作者不属于本订阅" in str(ws.cell(2, collector.COL["备注"]).value)
           and "荔枝新闻" in str(ws.cell(2, collector.COL["备注"]).value),
           str(ws.cell(2, collector.COL["备注"]).value))
        ck("ASR 一次都没调（钱省下）", len(asr) == 0, "调用 %d 次" % len(asr))

        ok, ws, asr = run_case(MINE, MINE)
        ck("作者对得上照常走（反面样本）", ok is True and len(asr) == 1,
           "ok=%s asr=%d" % (ok, len(asr)))

        ok, ws, asr = run_case("", FOREIGN)
        ck("手动粘链接（不传 expect）不许被拦（反面样本）",
           ok is True and len(asr) == 1, "ok=%s asr=%d" % (ok, len(asr)))

        ok, ws, asr = run_case(MINE, "")
        ck("详情没带回作者时不误杀（风控空响应仍走原路）",
           ok is True and len(asr) == 1, "ok=%s asr=%d" % (ok, len(asr)))
    finally:
        (collector.resolve_url, collector.fetch_video_info,
         collector.asr_transcribe) = orig

    print("\n== 3. 调用点把参数真的传了 ==")
    app_src = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
    n = app_src.count("expect_sec_uid=sub[")
    ck("订阅那条循环传了本订阅 sec_uid", n >= 1, "命中 %d 处" % n)
    ck("只有订阅这一条传参，手动采集两处保持原样（否则会拦掉一切非订阅链接）",
       n == 1 and app_src.count("collector.process_row(ws,") == 3,
       "传参 %d 处 / 调用点共 %d 处" % (n, app_src.count("collector.process_row(ws,")))


if __name__ == "__main__":
    test_provenance()
    test_owner_gate()
    if FAILS:
        print("\nFAILED %d 条：%s" % (len(FAILS), "、".join(FAILS)))
        sys.exit(1)
    print("\nALL_OK")
