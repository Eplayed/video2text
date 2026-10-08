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


def _verify_js(draft_path: str, rows: List[Tuple[str, str, str]],
               sources_path: Optional[str] = None) -> None:
    if not os.path.exists(VERIFY_JS):
        rows.append(("WARN", "机检", "找不到 verify_toutiao.js（生产层仓库没在预期位置）"))
        return
    # 必须传绝对路径：_run 把 cwd 切到生产层仓库目录，相对路径在那边找不到稿子，
    # verify_toutiao.js 只打一行用法说明就退 2 —— 机检行一条都解析不到，
    # 报告看着像"通过"。这就是本项目反复踩的静默失效，2026-10-07 实测复现过一次。
    cmd = ["node", VERIFY_JS, os.path.abspath(draft_path)]
    if sources_path and os.path.exists(sources_path):
        cmd += ["--sources", os.path.abspath(sources_path)]
    code, out = _run(cmd)
    got = 0
    for line in out.splitlines():
        # 结论行有两种形态：带说明的「[PASS] 名称 — 实际 3 条」和不带说明的「[PASS] 无占位标记」。
        # 早先只匹配带 — 的那种，等于把三项静默丢掉（禁用句式就在里面），报告看着像没查。
        m = re.search(r"\[(PASS|WARN|FAIL)\]\s*(.+?)(?:\s+—\s+(.*))?$", line)
        if m:
            got += 1
            rows.append((m.group(1), "机检·" + m.group(2).strip(), (m.group(3) or "").strip()))
        elif "质检结论" in line:
            got += 1
            rows.append(("INFO", "机检·总结论", line.strip().lstrip("❌✅ ")))
    if code == 99:
        rows.append(("WARN", "机检", out))
    elif got == 0:
        rows.append(("WARN", "机检",
                     "verify_toutiao.js 没有任何可解析输出（退出码 %s，输出开头：%s）——"
                     "多半是稿子里没有「复制区」标记，或 node 调用失败；这次等于没机检，别当已通过"
                     % (code, out.strip()[:90])))


def audit(md_text: Optional[str] = None, manifest: Optional[Dict[str, Any]] = None,
          sources_text: Optional[str] = None, draft_path: Optional[str] = None,
          use_verify: bool = True, sources_path: Optional[str] = None) -> Dict[str, Any]:
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
        if use_verify and draft_path:
            if "复制区" in (md_text or ""):
                _verify_js(draft_path, rows, sources_path)
            else:
                # 跳过也要留痕：没有复制区标记就整段不跑机检，报告只剩本层三项，
                # 不写这一行就会让人以为"没列出来 = 都过了"。
                rows.append(("INFO", "机检",
                             "跳过：稿子里没有「头条号 · 复制区」标记，verify_toutiao.js 不认这种结构，"
                             "本次只查了泄漏/叙述者/AI 味"))
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


# ── 整理稿（素材档案）体检 ────────────────────────────────────────
# 为什么不复用 audit()：素材档案是中间产物，禁用句式、字数区间、AI 味评分对它没意义
# （它本来就该写成清单）。它真正的风险是 LLM 那条路此前零校验——【关键数据】【金句摘录】
# 可能被写成原文里不存在的东西，而这些一旦进了正文就是编造。所以这里只查两件事：
# 结构塌没塌、有没有编造。

MATERIAL_SECTIONS = ["【标题候选】", "【导语】", "【核心论点】", "【关键数据】",
                     "【正文骨架】", "【金句摘录】", "【风险核查】"]
_NUM_TOKEN = re.compile(r"\d+(?:\.\d+)?%?")
# 素材档案阶段不查这两条：它就是转写产物，【金句摘录】里写「口播说」是正当词汇。
# 直接套成稿的 LEAK_PATTERNS 会把 9/10 篇判成「不能发」，全是假阳性。
MATERIAL_LEAK_PATTERNS = [p for p in LEAK_PATTERNS
                          if p[1] not in ("转写口径术语", "口播字样")]
_QUOTE_NOTE = re.compile(r"[（(](?:原话|原文|原|口播)[）)]|——.*$|—{2,}.*$")
_LIST_HEAD = re.compile(r"^\s*(?:[-*·]|\d+[.、)])\s*")
_CN_DIGIT = {"0": "零", "1": "一", "2": "二", "3": "三", "4": "四",
             "5": "五", "6": "六", "7": "七", "8": "八", "9": "九"}


def _cn_variants(run: str) -> set:
    """一个阿拉伯数字段可能对应的中文写法集合。

    口播里两位以上常用位值读法（53 = 五十三、15 = 十五），逐位读只对年份这类成立
    （2026 = 二零二六）。只按逐位比会把"五十三K"判成编造——实测就误报过稿54。
    """
    out = {"".join(_CN_DIGIT.get(ch, "") for ch in run)}
    if len(run) == 2 and run[1] != "0":
        body = "十" + _CN_DIGIT.get(run[1], "")
        out.add(_CN_DIGIT.get(run[0], "") + body)
        if run[0] == "1":
            out.add(body)                       # 15 常说成"十五"而不是"一十五"
    return {v for v in out if v}


def _number_found(token: str, src: str) -> bool:
    """一个数字算不算"素材里有"。

    口播转写里数字绝大多数是中文写法（"五十三K的 Star"、"大概分为四步"、"五分之一"），
    档案把它换成阿拉伯数字是正确加工，不是编造——所以两种写法都要认。
    按数字段逐个比对（"1/5" 拆成 1 和 5），比整串匹配更能容忍"五分之一"这类顺序写法；
    代价是理论上偏松，真编造一个素材里从没出现过的数字仍然会被抓到。
    """
    for run in re.findall(r"\d+", token or ""):
        if run in src:
            continue
        forms = _cn_variants(run)
        if len(run) == 1:
            forms.add("两")                      # 单个 2 常说成"两"（两个、两遍）
        if not any(f in src for f in forms):
            return False
    return bool(re.search(r"\d", token or ""))


def _norm(text: str) -> str:
    """只留字母数字与汉字：口播转写与整理稿的标点/空格差异不该影响回指比对。"""
    return re.sub(r"[^0-9a-zA-Z一-鿿]+", "", text or "").lower()


def _bigrams(text: str) -> set:
    s = _norm(text)
    return set(s[i:i + 2] for i in range(len(s) - 1))


def _echo_ratio(quote: str, src_grams: set) -> float:
    """金句与原文的词串重合度。整句精确匹配太脆（繁简、标点、轻度改写都会判不中），
    重合度才能区分「换了说法」和「凭空编了一句」。"""
    g = _bigrams(quote)
    return (len(g & src_grams) / len(g)) if g else 1.0


def _section_body(md: str, name: str) -> str:
    """取某个【标签】节的正文，到下一个【标签】或文末。"""
    m = re.search(re.escape(name) + r"(.*?)(?=\n【|$)", md or "", re.S)
    return (m.group(1) if m else "").strip()


def _claim_text(section: str) -> str:
    """只取【关键数据】里"被断言"的那部分文本。

    档案的表格是三列「项目 | 数值 | 口径」，第三列写的是来源与怀疑（"ASR原文，疑为2026年，
    需核对"）。把口径也当数据比对，会把"怀疑某个数"误判成"编造了某个数"——实测稿57 就这么误报过。
    """
    out = []
    for line in (section or "").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            out.append(s)
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 3 and not re.fullmatch(r"[-: ]*", cells[1] or "-"):
            out.append(cells[1])                 # 只取「数值」列
        elif cells:
            out.append(cells[0])
    return "\n".join(out)


def audit_material(md_text: str, source_text: str = "") -> Dict[str, Any]:
    """整理稿体检：结构完整性 + 数字与金句能否回指原素材。

    source_text 要一起传标题与标签——很多数字（如「53K星」）来自标题，只比对口播
    会把它们误判成编造。没传源文本时只查结构，并如实写明"没做回指"，
    不许让人以为"没列出来就是都过了"。
    """
    rows: List[Tuple[str, str, str]] = []
    md = md_text or ""
    missing = [s for s in MATERIAL_SECTIONS if s not in md]
    if missing:
        rows.append(("FAIL", "分节完整",
                     "缺 %d 节：%s（下游出图按节解析，缺节会拼出空卡）" % (len(missing), "、".join(missing))))
    else:
        rows.append(("PASS", "分节完整", "7 节齐"))
    for name in MATERIAL_SECTIONS:
        if name in md and len(_section_body(md, name)) < 10:
            rows.append(("FAIL", "空节", "%s 有标题没内容" % name))
    for pat, label in MATERIAL_LEAK_PATTERNS:
        if re.search(pat, md):
            rows.append(("FAIL", "中间产物泄漏", "%s 命中" % label))
    if source_text:
        src = _norm(source_text)
        src_grams = _bigrams(source_text)
        data = _claim_text(_section_body(md, "【关键数据】"))
        tokens = []
        for line in data.splitlines():          # 行首序号（1、 2. -）是排版不是数据
            tokens += _NUM_TOKEN.findall(_LIST_HEAD.sub("", line))
        # 一位数不判：口播里到处是 1~9，"素材里出现过 6"不代表"6 这个结论有出处"。
        # 实测把 60 改成 80 能抓到，把 6 改成 1 抓不到——所以宁可标注测不准，
        # 也不许让"可以发"覆盖掉一个其实没验的数。
        weak = [t for t in tokens if len(re.sub(r"\D", "", t)) == 1]
        assessable = [t for t in tokens if len(re.sub(r"\D", "", t)) > 1]
        bad = sorted({t for t in assessable if not _number_found(t, src)})
        if bad:
            rows.append(("FAIL", "数据可回指",
                         "【关键数据】%d/%d 个数字在素材与标题里都找不到：%s——回原文核"
                         % (len(bad), len(assessable), "、".join(bad[:8]))))
        elif assessable:
            rows.append(("PASS", "数据可回指", "【关键数据】%d 个数字全部能在素材里找到" % len(assessable)))
        elif not tokens:
            rows.append(("WARN", "数据可回指", "【关键数据】一个数字都没有——素材档案没数据就是空转"))
        else:
            rows.append(("INFO", "数据可回指",
                         "【关键数据】只有 %d 个一位数，本轮不判（见下）" % len(weak)))
        if weak:
            rows.append(("INFO", "回指覆盖",
                         "另有 %d 个一位数（%s）未判：素材里到处是 1~9，裸数字包含说明不了有出处，"
                         "这几个要人工看一眼" % (len(weak), "、".join(weak[:6]))))
        quotes = [q.strip() for q in re.split(r"\n+", _section_body(md, "【金句摘录】"))
                  if len(_norm(_QUOTE_NOTE.sub("", q))) >= 12]
        weak = [q for q in quotes if _echo_ratio(_QUOTE_NOTE.sub("", q), src_grams) < 0.5]
        if len(weak) == len(quotes) and quotes:
            rows.append(("WARN", "金句是否原文",
                         "%d 条金句与原文重合度都低于 50%%，整节可能是改写或编造，引用前逐条回原文对：%s"
                         % (len(weak), " / ".join(_QUOTE_NOTE.sub("", q)[:20] for q in weak[:3]))))
        elif weak:
            rows.append(("WARN", "金句是否原文",
                         "%d/%d 条金句与原文重合度低于 50%%（改了说法或凭空写）：%s"
                         % (len(weak), len(quotes),
                            " / ".join(_QUOTE_NOTE.sub("", q)[:20] for q in weak[:3]))))
        elif quotes:
            rows.append(("PASS", "金句是否原文", "%d 条金句与原文重合度都在 50%% 以上" % len(quotes)))
    else:
        rows.append(("INFO", "回指校验", "没传原素材：本轮只查了结构与泄漏，数字与金句**没有回指**"))
    fails = [r for r in rows if r[0] == "FAIL"]
    warns = [r for r in rows if r[0] == "WARN"]
    verdict = "fail" if fails else "warn" if warns else "pass"
    return {"rows": rows, "verdict": verdict, "label": VERDICT_LABEL[verdict],
            "fails": len(fails), "warns": len(warns), "score": None,
            "prose_chars": len(_norm(md)), "report": render(rows, verdict, warns, fails)}
