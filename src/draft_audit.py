"""发布前体检引擎——规则的唯一一份。

谁调它：
  · `scripts/audit_draft.py`（命令行，自媒体自动线 dify_auto_publish 也走这条）
  · `web/app.py` 的发布门禁（工作台里图文包 / 文章稿过审才能标记发布）
再抄一份规则表就是等着两处口径漂移，本项目在图文三轴上已经栽过三次。

判定分三档：可以发（零 FAIL 零 WARN）/ 改完再发（只有 WARN）/ 不能发（有 FAIL）。
机器只判形：句式黑名单仍由 verify_toutiao.js 动态读 media-workbench/server.js，
本文件不复制那份表；游戏机制对不对、数字准不准，仍要人按整合稿【风险核查】核。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

FACTORY = os.path.expanduser("~/Documents/自媒体/_content_factory")
VERIFY_JS = os.path.join(FACTORY, "verify_toutiao.js")
AI_SCORE = os.path.join(FACTORY, "wechat-publisher", "scripts", "ai_score.py")
AI_FAIL, AI_WARN = 45.0, 30.0     # 阈值与 ai_score.py 的 DEFAULT_THRESHOLD 对齐

# 给机器看的中间产物：这些出现在正文里就是「乱七八糟」的直接来源
LEAK_PATTERNS = [
    (r"图\s*\d+\s*[：:]", "图N：占位说明"),
    (r"画面说明", "画面说明"),
    (r"配图(?:任务|候选|链接池)", "配图任务指令"),
    (r"发布状态[：:]", "流水线自检结论"),
    (r"={3}[A-Z_]+={3}", "内部标记行"),
    (r"━{3,}", "分区标记线"),
    (r"【正文】", "结构标签"),
    (r"视频\s*\d+", "来源视频编号"),
    (r"整合稿", "内部文件名"),
    (r"ASR|转写", "转写口径术语"),
    (r"口播(?:未|中|说)", "口播字样"),
    (r"\*\*增量[：:]", "内部小标题前缀"),
]
# 成品里不该出现的编辑向交代（「信息来源：多位UP主…」是要求写的，不算泄漏，
# 只有「来源：视频1」这种内部编号才算）
EDITORIAL_VOICES = [r"待核实", r"需自行实测", r"人工核对", r"请人工", r"素材(?:未提|缺口)",
                    r"未明确", r"疑似", r"来源[:：]\s*(?:视频|整合稿|\[)"]
NARRATOR = r"我(?:把|去|先|又|试|跑|核|算|花|打|翻|看|测|在)"
NUMBER = r"(?<![\d.])\d{2,4}(?![\d.%])"

VERDICT_LABEL = {"pass": "可以发", "warn": "改完再发", "fail": "不能发"}


def _run(cmd: Sequence[str]) -> Tuple[int, str]:
    try:
        proc = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                cwd=FACTORY if cmd[0] == "node" else None)
        out, _ = proc.communicate(timeout=120)
        return proc.returncode, out.decode("utf-8", "replace")
    except Exception as e:
        return 99, "调用失败：%s（%s）" % (cmd[0], e)


def extract_body(md: str) -> str:
    """切出真正文：【正文】之后到下一条分区线为止。

    不切就会被 ai_score.py 误判——它曾把发布元数据和图池当正文一起算，
    给一份「不能发」的稿子打出 7.9/100 的真人味。
    """
    i = md.find("【正文】")
    if i < 0:
        return md
    rest = md[i + len("【正文】"):]
    m = re.search(r"\n\s*━{3,}", rest)
    return (rest[:m.start()] if m else rest).strip()


def manifest_prose(data: Dict[str, Any]) -> Tuple[str, str]:
    """图文包 → (卡片文字+文案, copy_text)。叙述者只查文案：卡片是短句清单。"""
    parts: List[str] = []
    for card in data.get("cards") or []:
        parts.append(card.get("title") or "")
        parts.append(card.get("subtitle") or "")
        for hook in card.get("hooks") or []:
            parts.append("%s %s" % (hook.get("t", ""), hook.get("d", "")))
        for item in card.get("items") or []:
            parts.append("%s %s" % (item.get("name", ""), item.get("desc", "")))
    copy_text = data.get("copy_text") or ""
    return "\n".join(parts) + "\n" + copy_text, copy_text


def _audit_prose(text: str, rows: List[Tuple[str, str, str]], tag: str = "正文",
                 narrator_text: Optional[str] = None,
                 narrator_level: str = "FAIL") -> set:
    for pat, name in LEAK_PATTERNS:
        hits = re.findall(pat, text)
        if hits:
            rows.append(("FAIL", tag + "泄漏", "%s ×%d（%s）" % (name, len(hits), hits[0][:24])))
    for pat in EDITORIAL_VOICES:
        hits = re.findall(pat, text)
        if hits:
            rows.append(("WARN", tag + "编辑向交代",
                         "命中「%s」×%d——成品不能交代素材缺口" % (hits[0][:16], len(hits))))
    placeholders = re.findall(r"\[插图\s*\d+\]", text)
    if placeholders:
        rows.append(("WARN", tag + "插图占位未删",
                     "残留 %s——贴进发布页前要把占位删掉或换成真图" % "、".join(placeholders[:5])))
    probe = text if narrator_text is None else narrator_text
    if not re.search(NARRATOR, probe) and "我" not in probe:
        rows.append((narrator_level, tag + "叙述者缺席",
                     "没有一个带具体动作的「我」，只剩通稿腔——这就是「一眼 AI 写的」主因"))
    return set(re.findall(NUMBER, text))


def _cross_check(nums: set, sources_text: str, rows: List[Tuple[str, str, str]]) -> None:
    src_nums = set(re.findall(NUMBER, sources_text))
    ghost = sorted(n for n in nums if n not in src_nums and not (1900 <= int(n) <= 2099))
    if ghost:
        rows.append(("WARN", "数字出处",
                     "正文有 %d 个数字在素材里找不到：%s——要么补出处要么删"
                     % (len(ghost), "、".join(ghost[:8]))))
    else:
        rows.append(("PASS", "数字出处", "正文数字都能在素材里对上"))


def _ai_score(prose: str, rows: List[Tuple[str, str, str]]) -> Optional[float]:
    if not os.path.exists(AI_SCORE) or len(re.sub(r"\s", "", prose)) < 200:
        return None
    tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    tmp.write(prose)
    tmp.close()
    try:
        _, out = _run([sys.executable or "python3", AI_SCORE, tmp.name])
    finally:
        os.unlink(tmp.name)
    m = re.search(r"总分[:：]\s*([\d.]+)\s*/\s*100", out)
    for line in out.splitlines():
        mm = re.search(r"\[(burstiness|phrases|vocab|structural|punctuation|style)\s*\]\s*分数=\s*([\d.]+)"
                       r".*?(\d+/\d+字|句长均值.*|.*密度.*)", line)
        if mm and float(mm.group(2)) >= 30:
            rows.append(("INFO", "AI 味·弱项", "%s=%.0f %s"
                         % (mm.group(1), float(mm.group(2)), (mm.group(3) or "")[:40])))
    return float(m.group(1)) if m else None


def _verify_js(draft_path: str, rows: List[Tuple[str, str, str]]) -> None:
    if not os.path.exists(VERIFY_JS):
        rows.append(("WARN", "机检", "找不到 verify_toutiao.js（生产层仓库没在预期位置）"))
        return
    code, out = _run(["node", VERIFY_JS, draft_path])
    for line in out.splitlines():
        m = re.search(r"\[(PASS|WARN|FAIL)\]\s*(.+?)\s*—\s*(.*)", line)
        if m:
            rows.append((m.group(1), "机检·" + m.group(2).strip(), m.group(3).strip()))
        elif "质检结论" in line:
            rows.append(("INFO", "机检·总结论", line.strip().lstrip("❌✅ ")))
    if code == 99:
        rows.append(("WARN", "机检", out))


def audit(md_text: Optional[str] = None, manifest: Optional[Dict[str, Any]] = None,
          sources_text: Optional[str] = None, draft_path: Optional[str] = None,
          use_verify: bool = True) -> Dict[str, Any]:
    """体检一份稿子。md_text 走长文稿口径，manifest 走图文卡稿口径。

    use_verify 只对 Dify 那种带「复制区」的成稿有意义；工作台里的图文包/文章稿
    没有那个结构，硬跑只会误报。
    """
    rows: List[Tuple[str, str, str]] = []
    if manifest is not None:
        prose, copy_text = manifest_prose(manifest)
        cards = len(manifest.get("cards") or [])
        rows.append(("INFO", "输入", "图文卡稿：%d 张卡，copy_text %d 字" % (cards, len(copy_text))))
        if copy_text and "AI辅助" not in copy_text:
            rows.append(("WARN", "AI 声明",
                         "copy_text 末尾没有「本文图为AI辅助生成」，发布时要手动勾 AI 辅助声明"))
        if copy_text and "信息来源" not in copy_text and "up主" not in copy_text \
                and "UP主" not in copy_text:
            rows.append(("WARN", "来源标注", "copy_text 没有交代信息来源，容易被当搬运"))
        # 卡片是短句清单，「我」只能出现在 copy_text 里，所以叙述者只查文案、降级为建议
        nums = _audit_prose(prose, rows, narrator_text=copy_text, narrator_level="WARN")
    else:
        prose = extract_body(md_text or "")
        if use_verify and draft_path and "复制区" in (md_text or ""):
            _verify_js(draft_path, rows)
        nums = _audit_prose(prose, rows)
    if sources_text:
        _cross_check(nums, sources_text, rows)

    score = _ai_score(prose, rows)
    if score is not None:
        level = "FAIL" if score >= AI_FAIL else "WARN" if score >= AI_WARN else "PASS"
        rows.append((level, "AI 味评分",
                     "%.1f/100（只算正文，不含元数据；≥%g 判 AI 味过重）" % (score, AI_FAIL)))

    fails = [r for r in rows if r[0] == "FAIL"]
    warns = [r for r in rows if r[0] == "WARN"]
    verdict = "fail" if fails else "warn" if warns else "pass"
    return {"rows": rows, "verdict": verdict, "label": VERDICT_LABEL[verdict],
            "fails": len(fails), "warns": len(warns), "score": score,
            "prose_chars": len(re.sub(r"\s", "", prose)),
            "report": render(rows, verdict, warns, fails)}


def render(rows: List[Tuple[str, str, str]], verdict: str,
           warns: List[Tuple[str, str, str]], fails: List[Tuple[str, str, str]]) -> str:
    lines = ["  [%-4s] %-22s %s" % (lvl, name, detail) for lvl, name, detail in rows]
    tail = ""
    if fails:
        tail = "\n" + "\n".join("  必须改 → %s：%s" % (n, d) for lvl, n, d in fails)
    head = "━━━━━━ 发布前体检 ━━━━━━\n" if lines else ""
    return head + "\n".join(lines) + "\n判定：%s（FAIL %d / WARN %d）%s" % (
        VERDICT_LABEL[verdict], len(fails), len(warns), tail)


def load_manifest(path: str) -> Dict[str, Any]:
    if os.path.isdir(path):
        path = os.path.join(path, "manifest.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
