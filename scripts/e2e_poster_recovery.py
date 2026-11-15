"""出图失败后保留已拆文案；缺文案不得暗中重拆或花模型额度。"""
import copy
import json
import tempfile
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web import app as wa
from src import ai_poster, fact_gate


def check(ok, label):
    if not ok:
        raise AssertionError(label)
    print("[PASS] " + label)


try:
    ai_poster.generate_poster({"id": 111, "content": "测试"}, {"api_key": "sk-no-network"})
except ai_poster.PosterError as e:
    check("拆文案" in str(e), "底层出图函数也拒绝漏传 plan，不再有隐式收费拆解分支")
else:
    raise AssertionError("底层漏传 plan 没被拒绝")


old_out = wa.OUTPUT_DIR
old_status = copy.deepcopy(wa._poster_status)
old_summary = wa._poster_get_summary
try:
    with tempfile.TemporaryDirectory(prefix="poster-recovery-") as tmp:
        wa.OUTPUT_DIR = Path(tmp)
        wa._poster_status.update({"running": False, "done": True, "summary_id": 0,
                                  "plan": None, "error": "", "package": None})
        wa._poster_get_summary = lambda sid: {"id": 111, "title": "隔离测试"}
        client = wa.app.test_client()
        res = client.post("/api/poster/generate", json={"summary_id": 111})
        check(res.status_code == 400 and "拆文案" in (res.get_json() or {}).get("error", ""),
              "无 plan 当场拒绝，不能现场重拆")
        check(not wa._poster_status["running"], "无 plan 不启动任务")

        snapshot = {"plan": {"title": "人工改过的标题", "cards": [{"t": "已改", "d": "具体文案"}]},
                    "publish": {"copy_text": "改过的配文"}, "publish_channel": "toutiao"}

        def blocked():
            raise wa.ai_poster.PosterError("待核事实进了成品")

        task = wa._poster_start("poster", blocked, 111, plan_snapshot=snapshot)
        check(task, "失败任务已启动")
        for _ in range(100):
            if not wa._poster_status["running"]:
                break
            time.sleep(0.01)
        st = client.get("/api/poster/status?summary_id=111").get_json()
        check(st["error"] and st["plan"]["plan"] == snapshot["plan"],
              "失败状态仍可编辑本次提交的文案")
        saved = json.loads((Path(tmp) / "poster" / "111" / "plan.json").read_text())
        check(saved["plan"] == snapshot["plan"], "失败后重启也能从临时备份恢复最新文案")
        wa._poster_status["plan"] = None  # 兼容修复前已卡住的同一 summary 失败状态
        recovered = client.get("/api/poster/status?summary_id=111").get_json()
        check(recovered["plan"]["plan"] == snapshot["plan"] and recovered["error"],
              "旧失败状态缺 plan 时也从备份恢复，保留逐条错误")
finally:
    wa.OUTPUT_DIR = old_out
    wa._poster_get_summary = old_summary
    wa._poster_status.clear()
    wa._poster_status.update(old_status)

number_plan = {"title": "技能加速15分钟", "cards": [{"t": "提高60%", "d": "先看说明"}],
               "publish": {"tt_titles": ["技能加速15分钟"]}}
missing = fact_gate.unsourced_numbers(number_plan, "素材只说技能加速，未给数值")
check(len(missing) == 3 and any("15分钟" in s for s in missing)
      and any("60%" in s for s in missing), "模型新增的带单位数值逐处拦下")
check(fact_gate.unsourced_numbers(number_plan, "加速15分钟，提高60%") == [],
      "原素材确有同一数值时不误拦")
check(fact_gate.unsourced_numbers(number_plan, "只提加速", ["加速15分钟，提高60%"] ) == [],
      "已留存官方原文也可作为数字依据")
check(len(fact_gate.unsourced_numbers(number_plan, "")) == 3,
      "素材原文缺席时不把新数值判成已核")
check(fact_gate.unsourced_numbers({"title": "先做3步", "cards": [],
                                   "publish": {"source_note": "核验于2026年"}}, "素材只有步骤") == [],
      "步骤数与来源说明日期不当游戏数值拦")
old_poster_out = ai_poster.OUTPUT_DIR
try:
    with tempfile.TemporaryDirectory(prefix="poster-number-gate-") as tmp:
        ai_poster.OUTPUT_DIR = Path(tmp)
        plan = {"title": "加速15分钟", "cards": [
            {"t": "先看说明", "d": "别猜数值"}, {"t": "再核出处", "d": "以原文为准"},
            {"t": "最后决定", "d": "待核不发"}]}
        summary = {"id": 998001, "status": "final", "title": "测试稿",
                   "content": "【正文】技能加速，具体时长未给。", "transcript": "技能加速，具体时长未给。"}
        try:
            ai_poster.generate_poster(summary, {"api_key": "sk-no-network", "api_base": "http://127.0.0.1:9/v1"},
                                      plan=plan, publish={}, asr_text=summary["transcript"],
                                      do_review=False)
        except ai_poster.PosterError as e:
            check(any("15分钟" in x for x in getattr(e, "fact_hard", [])),
                  "③ 真出图路径在任何画图请求前拦下新编数字")
        else:
            raise AssertionError("新编数字越过出图闸")
finally:
    ai_poster.OUTPUT_DIR = old_poster_out
print("POSTER_RECOVERY_OK")
