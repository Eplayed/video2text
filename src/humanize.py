# -*- coding: utf-8 -*-
"""humanize.py — 把 article-humanize 的纪律变成工作台能跑的一步（头条线）

为什么这样接：Qoder 的 skill 只在会话里由 agent 加载，Flask 进程调不到。
但 skill 的本事是三样东西——**必改清单、定点替换、不许重写**——这三样都能代码化。
所以这里不复制一份词表（句式黑名单仍以 media-workbench/server.js 为准，
写作原则仍以 ~/Documents/自媒体/ 那两份权威源为准），而是**直接读 SKILL.md 当纪律**：
skill 改了这里就跟着变，两处口径不许漂移。

护栏是代码级的，不靠 prompt 求模型：
  1. 模型只能交「原句 → 改句」的替换对，交整篇的一律不收；
  2. 原句必须在稿子里逐字且唯一出现，否则拒绝（防改错地方）；
  3. 全篇字数漂移超过 ±15% 判定为"在重写"，整批不落盘。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
RULES_FILE = ROOT / ".qoder" / "skills" / "article-humanize" / "SKILL.md"
MAX_LEN_DRIFT = 0.15          # ±15%，超过就是重写，不是改写
MAX_EDITS = 40                # 一次最多接受这么多处，防模型把全篇拆碎重造


def load_rules() -> Tuple[str, str]:
    """读 SKILL.md 里的「五条硬纪律」+「工作流」两段，返回 (纪律文本, 来源路径)。

    读不到必须抛错——宁可按钮报错，也不能悄悄用一份内置副本（那正是本项目反复踩的漂移）。
    """
    if not RULES_FILE.is_file():
        raise RuntimeError("找不到改写纪律文件：%s（skill 没装或路径变了，不能凭内置副本改写）" % RULES_FILE)
    text = RULES_FILE.read_text(encoding="utf-8")
    body = _slice(text, "## 五条硬纪律", "## 禁用清单不在这里维护")
    if not body:
        raise RuntimeError("纪律文件里找不到「五条硬纪律」小节，SKILL.md 可能被改过：%s" % RULES_FILE)
    return body.strip(), str(RULES_FILE)


def _slice(text: str, begin: str, end: str) -> str:
    i = text.find(begin)
    if i < 0:
        return ""
    j = text.find(end, i)
    return text[i:j if j > 0 else len(text)]


def format_rows(rows: List[Tuple[str, str, str]]) -> str:
    """体检结论 → 给模型看的必改清单（只给 FAIL/WARN，PASS 与 INFO 不占篇幅）。"""
    lines = []
    for level, name, detail in rows:
        if level in ("FAIL", "WARN"):
            lines.append("- [%s] %s：%s" % (level, name, detail))
    return "\n".join(lines) or "（机器没报错，只按纪律做定点微调）"


def build_prompt(md: str, rows: List[Tuple[str, str, str]], rules: str,
                 retry: Optional[List[Dict[str, str]]] = None) -> str:
    hint = ""
    if retry:
        lines = ["- 上次交的「%s…」被拒：%s" % (str(e.get("from") or "")[:24], e.get("reason") or "")
                 for e in retry[:10]]
        hint = ("\n【上一轮这几条没被采纳，只处理它们】\n"
                + "\n".join(lines)
                + "\n要么把 from 换成原稿里逐字可见、且只出现一次的更长片段，要么直接放弃这条；"
                  "不许顺手改别的句子。\n")
    return (
        "你是自媒体稿子的定点改写器。下面是一份体检必改清单和原稿，"
        "你要按纪律**只改命中的句子**，其余一字不动。\n\n"
        "【改写纪律（原文照搬自 skill 文件，逐条遵守）】\n" + rules + "\n\n"
        "【体检必改清单】\n" + format_rows(rows) + "\n" + hint + "\n"
        "【输出格式（必须严格遵守）】\n"
        "只输出一个 JSON **对象**（不能是数组，因为接口强制 json_object 模式），形如：\n"
        '{"edits": [{"from": "稿子里逐字出现的一段原文", "to": "改后的这段", "why": "对应清单里哪一条"}]}\n'
        "- 没有可改的就输出 {\"edits\": []}，不要为了凑数硬改。\n"
        "- from 必须是原稿里**逐字、且只出现一次**的片段（含标点），10~120 字为宜；"
        "做不到唯一就换更长的上下文，或者放弃这一条（不输出它）。\n"
        "- 禁止输出整篇稿子、禁止在 JSON 之外写解释。\n"
        "- 素材里没有的事实一律不许编：没有具体颗粒可补的条目，直接放弃，不要硬凑。\n"
        "- 最多 %d 处。\n\n" % MAX_EDITS +
        "【原稿】\n" + md + "\n"
    )


def parse_edits(raw: Any) -> List[Dict[str, str]]:
    """把模型返回的东西抠成替换对。接受三种形态：已解析的 dict、list、原始字符串。

    返回空列表时调用方必须报"没改"，不能静默通过。
    """
    data: Any = raw
    if isinstance(raw, str):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw.strip())
        start, end = text.find("{"), text.rfind("}")
        a, b = text.find("["), text.rfind("]")
        try:
            if 0 <= start < end:
                data = json.loads(text[start:end + 1])
            elif 0 <= a < b:
                data = json.loads(text[a:b + 1])
            else:
                return []
        except ValueError:
            return []
    if isinstance(data, dict):
        data = data.get("edits") or data.get("list") or data.get("result") or []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        frm = str(item.get("from") or "").strip()
        to = str(item.get("to") or "")
        if frm and to and frm != to:
            out.append({"from": frm, "to": to.strip(), "why": str(item.get("why") or "")})
    return out[:MAX_EDITS]


def apply_edits(md: str, edits: List[Dict[str, str]]) -> Tuple[str, List[Dict[str, str]], List[Dict[str, str]]]:
    """逐条定点替换。找不到、出现多次、改后没变化的，全部进 rejected 并说明原因。"""
    cur = md
    applied: List[Dict[str, str]] = []
    rejected: List[Dict[str, str]] = []
    for e in edits:
        frm, to = e["from"], e["to"]
        n = cur.count(frm)
        if n == 0:
            rejected.append(dict(e, reason="原稿里找不到这段（模型改写了原文，不能拿它去替换）"))
        elif n > 1:
            rejected.append(dict(e, reason="这段在原稿里出现 %d 次，定位不到改哪一处" % n))
        else:
            cur = cur.replace(frm, to, 1)
            applied.append(e)
    return cur, applied, rejected


def drift(before: str, after: str) -> float:
    if not before:
        return 0.0
    return (len(after) - len(before)) / float(len(before))


def humanize(md: str, rows: List[Tuple[str, str, str]], ai_config: Dict[str, str],
             llm_chat=None, timeout: int = 420,
             retry: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """跑一次定点改写预览。返回 {ok, new_md, applied, rejected, drift, rules_from, error}。

    ok=False 时 new_md 一定还是原稿——调用方可以直接不写库，稿子不会坏。
    retry 传上一轮被拒的条目，只让它们重做（失败可重试，不整批重来）。
    timeout 默认 420 秒：主模型 qwen3.8-max 是思考型，实测这一步单跑就要 3-4 分钟，
    给 180 秒会被客户端超时掐掉（表现就是"转圈转半天然后报没结果"）。
    """
    from src import content_store            # 延迟导入，避免循环依赖
    chat = llm_chat or content_store._llm_chat

    try:
        rules, rules_from = load_rules()
    except RuntimeError as e:
        return {"ok": False, "error": str(e), "new_md": md, "applied": [], "rejected": [],
                "drift": 0.0, "rules_from": "", "edits_seen": 0}

    resp = chat(ai_config, build_prompt(md, rows, rules, retry), timeout=timeout)
    # 模型明确交 {"edits": []} 是**有效结论**（这篇确实没可改的句子），不能和"格式跑偏/调用失败"混成一种
    valid_empty = isinstance(resp, dict) and "edits" in resp
    edits = parse_edits(resp)
    if not edits:
        if valid_empty:
            return {"ok": True, "error": "", "new_md": md, "applied": [], "rejected": [],
                    "drift": 0.0, "rules_from": rules_from, "edits_seen": 0, "nothing": True}
        shape = type(resp).__name__
        keys = ",".join(list(resp.keys())[:6]) if isinstance(resp, dict) else ""
        empty = (not resp) or (isinstance(resp, dict) and not resp)
        why = ("调用没返回内容（多半是模型报错或超时被掐，详情看工作台日志）"
               if empty else "返回的不是可用的 edits 列表（%s%s），模型没按格式交卷"
                             % (shape, ("：" + keys) if keys else ""))
        return {"ok": False, "error": "模型这一轮没交出可用改法：" + why + "。稿子未改动。",
                "new_md": md, "applied": [], "rejected": [], "drift": 0.0,
                "rules_from": rules_from, "edits_seen": 0}

    new_md, applied, rejected = apply_edits(md, edits)
    d = drift(md, new_md)
    if not applied:
        return {"ok": False, "error": "%d 条替换全被拒（定位不到原文），稿子未改动" % len(edits),
                "new_md": md, "applied": [], "rejected": rejected, "drift": d,
                "rules_from": rules_from, "edits_seen": len(edits)}
    if abs(d) > MAX_LEN_DRIFT:
        return {"ok": False,
                "error": "改完全篇字数变化 %+.0f%%，超过 ±%d%% 红线——这是在重写不是在改写，整批不落盘"
                         % (d * 100, int(MAX_LEN_DRIFT * 100)),
                "new_md": md, "applied": applied, "rejected": rejected, "drift": d,
                "rules_from": rules_from, "edits_seen": len(edits)}
    return {"ok": True, "error": "", "new_md": new_md, "applied": applied, "rejected": rejected,
            "drift": d, "rules_from": rules_from, "edits_seen": len(edits)}
