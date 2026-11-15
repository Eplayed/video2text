"""发布前体检引擎——规则的唯一一份。

谁调它：
  · `scripts/audit_draft.py`（命令行，自媒体自动线 dify_auto_publish 也走这条）
  · `web/app.py` 的发布门禁（工作台里图文包 / 文章稿过审才能标记发布）
再抄一份规则表就是等着两处口径漂移，本项目在图文三轴上已经栽过三次。

判定分三档：可以发（零 FAIL 零 WARN）/ 改完再发（只有 WARN）/ 不能发（有 FAIL）。
机器只判形：句式黑名单在 `tools/banned_patterns.js`（2026-10-19 从 media-workbench/server.js 搬来，
由 verify_toutiao.js require），本文件不复制那份表；游戏机制对不对、数字准不准，仍要人按整合稿【风险核查】核。
「机检没跑成」一律判 FAIL（见 `_verify_js`）：没检查 ≠ 检查通过，WARN 点一下就过、不落理由，
FAIL 才能逼出一条 override_reason 让事后翻得出"这篇是在质检门失效时发的"。
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

# 合规红线里"机器判得了形"的那部分：《公众号写作铁律》v1.2 废止、v1.9（2026-10-08）
# 结案维持——「关注后回复关键词领取」属公众号运营规范 3.3.2 诱导关注典型违规，
# 轻则单篇取消推荐/封分享，重则限制关注。LLM 写卡片时爱自己加这种"获取方式"卡
# （output/wechat/60 的 img6 就是它自己写的），所以判 FAIL 不判 WARN；
# 确实被误伤的（比如正文在讲公众号运营本身）走审核台「人工放行」写理由留痕。
COMPLIANCE_PATTERNS = [
    (r"(?:私信|加我?微信|扫码)[^，。；\n]{0,8}(?:领|获取|发我)", "私下领取/站外导流"),
]
# 「关注 + 回复 + 领取」这一档 2026-10-09 由本人拍板恢复（铁律 v2.0），所以**不再拦**，
# 但继续检测并记一条 INFO：平台《运营规范 3.3.2》把"关注后才能领"算诱导关注，
# 真被限流或处罚时，这条记录能说明"是我们知情后选的打法"，而不是漏检。
# 想改回随文给：把下面两条挪回 COMPLIANCE_PATTERNS 即可，别在调用处加 if。
HOOK_PATTERNS = [
    # 「关注 + 回/回复 + 领取动词」才判。"回"要收进来：文案里"关注后回「工具包」"和
    # "关注后回复「工具包」"是同一件事，只认后者会漏掉一半（2026-10-09 拿自家生成的文案试出来）。
    # 单独出现"关注""回复"不算——"值得关注的是模型回复很慢"是叙述。误报在这里不花钱：只记不拦。
    (r"关注[^，。；\n]{0,6}回(?:复)?[^，。；\n]{0,14}(?:领取|领|获取|入口|查看)", "关注后回复领取"),
    (r"回(?:复)?[「『【][^」』】]{1,12}[」』】][^，。；\n]{0,12}(?:领|取|获得|查看|拿)", "回复关键词领取"),
]

VERDICT_LABEL = {"pass": "可以发", "warn": "改完再发", "fail": "不能发"}


# 公众号发布稿的三行"管线语法"：verify_draft.js 要求它们必须在稿里
# （图N：→ 排版时渲染成截图位卡片；配图任务：→ 告诉作者截哪一屏；发布状态：→ 质检门读它），
# 而上面的 LEAK_PATTERNS 是按头条「复制区」口径写的，把这三行一律判成"中间产物泄漏"。
# 两道门互斥的直接后果：公众号长稿在这套产线下**永远拿不到 audit_draft.py 退出码 0**
# （2026-10-19 实测复现，见 docs 交接档）。号主当天拍板：给 draft_audit 加窄豁免——
# 只豁免这三类行、只在识别为公众号发布稿时豁免、豁免了什么必须在报告里显形。
# 头条线一份字节没改：那条线的稿子里没有下面这两个标记。
WECHAT_MARKERS = ("## 公众号摘要", "## 发布前自查清单")
WECHAT_PIPELINE_RE = re.compile(
    r"^[ \t]*(?:图\s*\d+\s*[：:][^\n]*|配图任务[：:][^\n]*|发布状态[：:][^\n]*)[ \t]*$", re.M)


def _is_wechat_draft(md: str) -> bool:
    return all(m in (md or "") for m in WECHAT_MARKERS)


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

    公众号发布稿没有【正文】这个标记，它用的是「## 正文」这一节，后面跟着摘要段和
    发布前自查清单（2026-10-19 第一篇教程稿实测：整份文件当正文算，自查清单里
    「bis-01-rail-doll.png」这种文件名被切成 01 当数字去查出处）。所以这里补一条
    公众号口径：切到 ## 正文 到 摘要/自查清单 之间，与 verify_draft.js 算字数用的是
    同一段，两道门判的正文才是同一份文字。
    """
    i = md.find("【正文】")
    if i < 0:
        j = md.find("## 正文")
        if j >= 0 and _is_wechat_draft(md):
            rest = md[j + len("## 正文"):]
            cuts = [k for k in (rest.find("## 公众号摘要"), rest.find("## 发布前自查清单")) if k > 0]
            return (rest[:min(cuts)] if cuts else rest).strip()
        return md
    rest = md[i + len("【正文】"):]
    m = re.search(r"\n\s*━{3,}", rest)
    return (rest[:m.start()] if m else rest).strip()


def _num_tokens(text: str) -> set:
    """抽正文里要核的数。两步归一，都是为了让"格式"别冒充"查无出处"：
    ① 去掉数字之间的千分位逗号——「6,626」在原正则下会被切成「626」，而素材里写 6626；
    ② 整段去掉 ISO 日期（2026-10-16 这种）——日期不是数据声明，它的出处是决策记录，
       留在反查池里只会让每篇带日期的教程稿白吃一条 WARN（年份本来就被 1900-2099 滤掉，
       月日没滤是同一个漏洞的另一半）。等级区间那种「29-38」不在此列，照查。"""
    t = re.sub(r"\d{4}-\d{1,2}-\d{1,2}", " ", text or "")
    t = re.sub(r"(?<=\d),(?=\d)", "", t)
    return set(re.findall(NUMBER, t))


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
    return _num_tokens(text)


def _cross_check(nums: set, sources_text: str, rows: List[Tuple[str, str, str]]) -> None:
    src_nums = _num_tokens(sources_text)
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
    """跑生产层机检。三条"没跑成"的分支一律判 FAIL，不判 WARN（2026-10-19）。

    为什么：WARN 在审核台点一下就过、不落理由；FAIL 必须写放行理由进 override_reason
    （review_store.approve）。"机检根本没跑"和"查出个小问题"是两件事——前者若只判 WARN，
    事后翻不出这篇是在质检门失效状态下发的。本项目已经栽过同类：界面写 0 元、后端照样扣钱。
    触发条件不是假设：VERIFY_JS 在 ~/Documents/自媒体/_content_factory 下，那是个仓库外的
    目录，搬家/换机/清理都会让它消失，而消失了稿子照样能发出去。
    """
    if not os.path.exists(VERIFY_JS):
        rows.append(("FAIL", "机检未跑",
                     "找不到 verify_toutiao.js（预期位置 %s）——本次**没有机检**，"
                     "禁用句式/字数/AI 味全都没查。要么恢复生产层仓库，"
                     "要么在审核台写明理由人工放行" % VERIFY_JS))
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
        rows.append(("FAIL", "机检未跑", "调用失败，本次**没有机检**：%s" % out))
    elif got == 0:
        rows.append(("FAIL", "机检未跑",
                     "verify_toutiao.js 没有任何可解析输出（退出码 %s，输出开头：%s）——"
                     "多半是稿子里没有「复制区」标记，或 node 调用失败；"
                     "这次**等于没机检**，别当已通过" % (code, out.strip()[:90])))


def _compliance(text: str, rows: List[Tuple[str, str, str]]) -> None:
    """机器判得了形的合规红线：站外导流命中即 FAIL；关注回复钩子只记录不拦（v2.0 拍板）。"""
    hits = []
    for pat, label in COMPLIANCE_PATTERNS:
        m = re.search(pat, text or "")
        if m:
            hits.append("%s（%s）" % (label, re.sub(r"\s+", " ", m.group(0))[:36]))
    if hits:
        rows.append(("FAIL", "站外导流/私下领取",
                     "；".join(hits) + " —— 交付物只能在站内拿（铁律 v2.0）。"
                     "确属误伤（比如正文在讲公众号运营本身）"
                     "走审核台「人工放行」写理由留痕"))
    hooks = []
    for pat, label in HOOK_PATTERNS:
        m = re.search(pat, text or "")
        if m:
            hooks.append("%s（%s）" % (label, re.sub(r"\s+", " ", m.group(0))[:36]))
    if hooks:
        rows.append(("INFO", "关注回复钩子",
                     "；".join(hooks) + " —— 2026-10-09 拍板恢复的打法（铁律 v2.0），不拦。"
                     "留意：《腾讯运营规范 3.3.2》把「关注后才能领」算诱导关注，"
                     "真出问题这条记录就是「知情后选择」的依据"))


# 这几节是"给编辑看的"，本来就该保留素材原话与不确定项，实名门不看它们：
# 【原话摘录】要留 up 主说法、【口径分歧】【风险核查】要写"素材叫 A、名单叫 B，待人工确认"。
# 拿实名规则去卡这几节，等于逼模型把引用改成通顺的话，那是造假不是纠错。
_EDITORIAL_SECTIONS = ("原话摘录", "口径分歧", "风险核查", "来源", "待办")


def _reader_facing(text: str) -> str:
    """去掉编辑节，只留读者会看到的正文。"""
    lines, skip = [], False
    for ln in (text or "").splitlines():
        head = re.match(r"^\s*[【\[](.{2,8})[】\]]", ln)
        if head:
            skip = head.group(1) in _EDITORIAL_SECTIONS
        if not skip:
            lines.append(ln)
    return "\n".join(lines)


def _term_gate(text: str, rows: List[Tuple[str, str, str]]) -> None:
    """实体实名门：名单里有实名、正文却用描述性写法或改过的旧名 → FAIL。

    口径 2026-10-10 由用户升级：「名单里有实名就一律统一」，不再要求"读不通才改"。
    规则本身在 config/wow_aliases.json（人工维护，目标实名必须在同步下来的名单里存在）；
    这里只负责拦，不改——替换走 scripts/check_terms.py --fix，由人决定要不要落盘。
    名单没同步时整项跳过并留 INFO，不静默放行。
    """
    try:
        from . import term_check
        rep = term_check.check(_reader_facing(text))
    except Exception as exc:                      # 名单缺失/表损坏都不该让体检崩掉
        rows.append(("INFO", "实体实名", "本项跳过：%s" % str(exc)[:60]))
        return
    for level, name, detail in rep.get("rows") or []:
        if name.startswith("实体实名"):
            rows.append((level, name, detail))


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
        scan_text = prose
        if _is_wechat_draft(md_text or ""):
            hits = WECHAT_PIPELINE_RE.findall(prose)
            scan_text = WECHAT_PIPELINE_RE.sub("", prose)
            rows.append(("INFO", "泄漏扫描",
                         "公众号发布稿：%d 行管线行（图N：／配图任务：／发布状态：）不计泄漏，"
                         "其余照查——这三行是 verify_draft.js 要求在稿里的，两门互斥已按 2026-10-19 拍板处理"
                         % len(hits)))
            # 两道门不能说两种话。2026-10-27 实测：97 号在成稿窗被拦下（正文中段挂着
            # 「### 发布前还要回查的三件事」），这份体检却判「可以发」——号主拿后一份当真实，
            # 就会把编辑待办发出去。判据调 graphics 那一处实现，不在这里抄第二份。
            # 没跑成按上面 10-19 那条同口径判 FAIL（"等于没查"不能混成已通过）。
            try:
                from .graphics.channels.wechat import editor_todos
                todos = editor_todos(prose)
            except Exception as e:
                rows.append(("FAIL", "正文混没混编辑待办",
                             "这道查没跑成（%s）——本次**没有查这一项**，别当已通过" % str(e)[:70]))
            else:
                if todos:
                    rows.append(("FAIL", "正文混没混编辑待办",
                                 "%d 处：%s。公开正文只留读者视角的限制，"
                                 "「发稿前我们还要查什么」写进文末「发布前自查清单」"
                                 % (len(todos), "；".join(x["text"][:26] for x in todos[:3]))))
                else:
                    rows.append(("PASS", "正文混没混编辑待办",
                                 "正文里没有「发稿前还要核实」这类编辑向的话"))
        # 要核的数只从"读者会看到的那段"里取：管线行（图N 说明／配图任务／发布状态）
        # 发布前会被真图替换掉，里面「bis-01-rail-doll.png」「1,440 宽」这种不是数据声明
        nums = _audit_prose(scan_text, rows)
    # 图文包的卡片文案（copy_text）也在扫描范围内：违规"获取方式"卡就是 LLM 自己加的
    _compliance(prose if manifest is None else prose + "\n" + copy_text, rows)
    if sources_text:
        _cross_check(nums, sources_text, rows)
    # 实名门放在 AI 味之前：名字错了属于事实层问题，比"像不像 AI 写的"更该先挡住
    _term_gate(prose if manifest is None else prose + "\n" + copy_text, rows)

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


# ── 整理稿（公众号文章档）体检 ────────────────────────────────────
# 为什么不复用 audit()：整理稿写成清单体，禁用句式、字数区间、AI 味评分对它没意义。
# 它真正的风险有两个：① LLM 把【关键数据】【金句摘录】写成原文里没有的东西；
# ② 2026-10-26 起这一档的成品定义变了——按《公众号当前策略》交的是**一篇完整文章**，
#    不再是"给下游写稿引擎的骨架"。所以这里除了"结构塌没塌、有没有编造"，
#    还要查**正文到底写没写**：只有小节标题、或还留着"待写"占位，就是没成稿，必须退回整合稿。
#    （号主 10-26 原话口径：不合格就退回整合稿，不许带着骨架去发。）

MATERIAL_SECTIONS = ["【标题候选】", "【导语】", "【适用范围】", "【核心论点】", "【正文】",
                     "【关键数据】", "【金句摘录】", "【风险核查】", "【来源】"]
# 旧形态（素材档案只有骨架）就靠这两枚标记认：读得懂老稿，但不放它过闸
LEGACY_BODY_TAG = "【正文骨架】"
# 占位词：规则版骨架与被截断的正文都会留这种字样，出现就是没写完
BODY_PLACEHOLDER = re.compile(r"待写|待补充|只给小节|仅提供骨架|只给骨架")
SOURCING_MARKS = ("[已核]", "[他述]", "[本站]", "[待核]")
_NUM_TOKEN = re.compile(r"\d+(?:\.\d+)?%?")
# 素材档案阶段不查这几条：它就是转写产物，【金句摘录】里写「口播说」是正当词汇。
# 直接套成稿的 LEAK_PATTERNS 会把 9/10 篇判成「不能发」，全是假阳性。
# 2026-10-26 再放两条，因为是这一档的成品定义变了之后**新出现的假阳性**（不是我挑出来的旧账）：
#   结构标签——【正文】现在就是这一档的正式节名（文章主体），成稿才禁止它出现；
#   来源视频编号——策略要求整合稿"能回查来源"，多源稿必须标「视频1/视频3」说清谁说的，
#     这条在旧版提示词里也要求过，只是以前没写死在结构里，撞上的概率低。
MATERIAL_LEAK_SKIP = ("转写口径术语", "口播字样", "结构标签", "来源视频编号")
MATERIAL_LEAK_PATTERNS = [p for p in LEAK_PATTERNS if p[1] not in MATERIAL_LEAK_SKIP]
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


def _article_body(md_text: str, rows: List[Tuple[str, str, str]]) -> None:
    """查【正文】到底写没写：小节数、有没有段落、占位词、四类标注在不在。

    判据只认结构，不抄字数线——号主没给公众号成稿字数口径，这里写死一个数就变成
    "机器拿我编的数判他能不能发"。
    """
    body = _section_body(md_text, "【正文】")
    if not body.strip():
        return                      # 整节没了由「分节完整」判，这里不重复报一条
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    heads = [l for l in lines if l.startswith("##")]
    prose = [l for l in lines if not l.startswith("##")]
    hit = BODY_PLACEHOLDER.search(body)
    if hit:
        rows.append(("FAIL", "正文写没写",
                     "【正文】还留着「%s」这种占位，等于没成稿——配 AI 重新生成或自己写完再体检"
                     % hit.group(0)))
    if len(heads) < 2:
        rows.append(("FAIL", "正文分节",
                     "【正文】只有 %d 个小节（要 3-5 个 `## ` 小节，每节给能照做的步骤或明确的判断依据）"
                     % len(heads)))
    elif not prose:
        rows.append(("FAIL", "正文有内容", "【正文】全是小节标题，一行内容都没有"))
    elif max(len(p) for p in prose) < 40:
        rows.append(("FAIL", "正文有内容",
                     "【正文】每节都只有一句话（最长 %d 字），读者照不做起不来"
                     % max(len(p) for p in prose)))
    else:
        rows.append(("PASS", "正文有内容", "【正文】%d 个小节、%d 行正文" % (len(heads), len(prose))))
    marked = sum(body.count(m) for m in SOURCING_MARKS)
    if not marked:
        rows.append(("FAIL", "来源类型标注",
                     "【正文】一处标注都没有：策略要求每条结论标 [已核]/[他述]/[本站]/[待核]，"
                     "不标就分不清哪些是本人核实过的、哪些只是转述别人"))
    else:
        rows.append(("PASS", "来源类型标注", "【正文】里 %d 处标注" % marked))
        pending = body.count("[待核]") + body.count("[他述]")
        if pending:
            rows.append(("INFO", "待核与转述",
                         "【正文】有 %d 处 [待核]/[他述]：这些不许在文章里写成能照做的建议" % pending))


# 每一档的**结构契约**只在这里登记一份，且必须与 `content_store` 那条提示词写的需求一致
# （e2e W28 有一条"提示词里找不到这个标签就算漂移"的守卫）。
# 没登记在这张表里的形态：只查通用项，**不判分节**——
# 拿一套没核对过的结构去拦稿子，就是本轮在修的那个错（公众号素材那九节拦了 41 篇）。
FORM_SECTIONS = {
    # 头条决策化整合稿：提示词要求"标签原样输出、顺序不变"，只有【变化对比】可省
    "toutiao_mix": {"required": ["【标题候选】", "【时间线】", "【对你的影响】", "【行动建议】",
                                 "【原话摘录】", "【风险核查】"],
                    "optional": ["【变化对比】"]},
}


def _finish_material(rows, md):
    fails = [r for r in rows if r[0] == "FAIL"]
    warns = [r for r in rows if r[0] == "WARN"]
    verdict = "fail" if fails else "warn" if warns else "pass"
    return {"rows": rows, "verdict": verdict, "label": VERDICT_LABEL[verdict],
            "fails": len(fails), "warns": len(warns), "score": None,
            "prose_chars": len(_norm(md)), "report": render(rows, verdict, warns, fails)}


# 攻略档（game_guide / toutiao_mix …）按 `## 小标题` 成篇，供程序化排版切块
GUIDE_MIN_SECTIONS = 3


def _md_sections(md):
    """按**二级** `## ` 切节，返回 [(标题, 正文)]。

    两个都测出来的坑：
      · 只写 `s.startswith("##")` 会把 `### 小标题` 也当分节，于是
        "## 大标题"紧跟"### 子标题"的稿子被切成一个空节 → 凭空报「有标题没内容」
        （库里 稿 44、稿 92 就是这么被误判的）；`###` 及以下归给父节。
      · 正文开头的引子不算小节：稿子一上来就是 `## ` 时，把空引子当小节也会凭空报空节。
    """
    out, title, buf = [], None, []
    for line in (md or "").splitlines():
        if line.startswith("## ") or line.strip() == "##":
            if title is not None:
                out.append((title, "\n".join(buf).strip()))
            title = line.lstrip("#").strip() or "(无题)"
            buf = []
        elif title is not None:
            buf.append(line)
    if title is not None:
        out.append((title, "\n".join(buf).strip()))
    return out


# 全文数字回指在这一档只能当提示，不能当闸：没有【关键数据】那种锚点，
# 正文里混着 aweme id（19 位）、链接里的数字、`001.` 这种条目编号——
# 实测库里 4 篇被判「数字找不到」的，理由全是这类噪音（01、7685199393477135662、001…）。
# 所以：先剥链接与条目编号、丢掉 ≥8 位的 id，剩下的对不上也只报 WARN 让人看一眼。
_LINK_RE = re.compile(r"https?://\S+|//\S*\.\S+")
_LIST_HEAD_RE = re.compile(r"^\s*(?:[-*]\s+)?[（(]?\d{1,3}[)）.、]", re.M)
# （条目常写成「- 001. xxx」，只认行首数字会漏掉带 - 的那种，库里实测漏掉过 001）


def _prose_numbers(md):
    txt = _LIST_HEAD_RE.sub("", _LINK_RE.sub(" ", md or ""))
    return sorted({t for t in _NUM_TOKEN.findall(txt)
                   if 1 < len(re.sub(r"\D", "", t)) < 8})


def _audit_other_form(md, source_text, summary_type):
    """**不是**「公众号文章素材」那一档的整理稿，该查什么。

    为什么单独一条（2026-10-27 号主撞出来的）：`audit_material` 原来不分形态，
    拿公众号素材那九节去量所有整理稿——库里 13 篇 game_guide + 27 篇 toutiao_mix
    一律判「缺 9 节、不能发」。他刚生成的那篇攻略稿五节齐
    （适用场景/步骤/机制解释/避坑提醒/事实核查清单，正是提示词要求的那五块），
    体检照样报不能发。**判据用错档就是假阳性**，代价是号主以为刚做的稿子废了。
    这一档不查【关键数据】/【金句摘录】/四类溯源标注（那是公众号素材的口径），
    但查：成没成篇、小节够不够、节里有没有内容、占位截断、中间产物泄漏、合规、
    数字能不能回指素材。
    """
    rows = []
    rows.append(("INFO", "体检口径",
                 "这一档是「%s」，不查公众号素材那九节与 [已核]/[他述]/[本站]/[待核] 标注；"
                 "查成篇、结构契约、空节、占位、泄漏、合规、数字回指" % (summary_type or "未标注形态")))
    chars = len(_norm(md))
    if chars < 200:
        rows.append(("FAIL", "成没成篇", "全文 %d 字，太短不像一篇稿（重新生成或自己写完再体检）" % chars))
    else:
        rows.append(("PASS", "成没成篇", "%d 字" % chars))
    empty = []
    spec = FORM_SECTIONS.get(summary_type)
    if spec:
        miss = [t for t in spec["required"] if t not in md]
        if miss:
            rows.append(("FAIL", "分节", "缺 %d 个固定标签：%s（提示词要求原样输出、顺序不变，"
                         "缺节下游按节抽取会拿到空）" % (len(miss), "、".join(miss))))
        else:
            rows.append(("PASS", "分节", "%d 个固定标签齐%s"
                         % (len(spec["required"]),
                            "（可选节 %s 在）" % "、".join(spec["optional"])
                            if any(o in md for o in spec["optional"]) else "（可选节没出，允许）")))
        for tag in spec["required"] + spec["optional"]:
            if tag in md and len(_norm(_section_body(md, tag))) < 10:
                empty.append(tag)
    elif summary_type == "game_guide":
        secs = _md_sections(md)
        if len(secs) < GUIDE_MIN_SECTIONS:
            rows.append(("FAIL", "分节", "只有 %d 个 `## ` 小节（这一档要按小节切块给排版用，"
                         "少于 %d 节拼不出页）" % (len(secs), GUIDE_MIN_SECTIONS)))
        else:
            rows.append(("PASS", "分节", "%d 个小节：%s"
                         % (len(secs), "、".join(t for t, _ in secs[:6]))))
        empty = [t for t, b in secs if len(_norm(b)) < 10]
    else:
        rows.append(("INFO", "分节", "这一档没登记过结构契约（FORM_SECTIONS 里没有 %s），"
                     "本轮**不判分节**——要判先去提示词里把它的固定标签登记进来"
                     % (summary_type or "未标类型")))
    if empty:
        rows.append(("FAIL", "空节", "有标题没内容：%s" % "、".join(empty)))
    hit = BODY_PLACEHOLDER.search(md)
    if hit:
        rows.append(("FAIL", "占位没填", "留着「%s」这种占位，等于没写完" % hit.group(0)))
    for pat, label in MATERIAL_LEAK_PATTERNS:
        if re.search(pat, md):
            rows.append(("FAIL", "中间产物泄漏", "%s 命中" % label))
    _compliance(md, rows)
    if source_text:
        src = _norm(source_text)
        tokens = _prose_numbers(md)
        bad = [t for t in tokens if not _number_found(t, src)]
        if bad:
            rows.append(("WARN", "数据可回指",
                         "%d/%d 个数字在素材与标题里找不到：%s——这一档没有【关键数据】那种锚点，"
                         "剥掉链接与条目编号后仍对不上的，人工回原文看一眼（不拦发布）"
                         % (len(bad), len(tokens), "、".join(bad[:8]))))
        elif tokens:
            rows.append(("PASS", "数据可回指", "%d 个数字全部能在素材里找到" % len(tokens)))
        else:
            rows.append(("INFO", "数据可回指", "正文里没有可核对的 ≥2 位数字（链接与条目编号已剥掉）"))
    else:
        rows.append(("INFO", "回指校验", "没传原素材：本轮只查了结构与泄漏，数字**没有回指**"))
    return _finish_material(rows, md)


def audit_material(md_text: str, source_text: str = "", summary_type: str = "wechat_material") -> Dict[str, Any]:
    """整理稿体检：成稿形态 + 结构完整性 + 正文写没写 + 数字与金句能否回指原素材。

    source_text 要一起传标题与标签——很多数字（如「53K星」）来自标题，只比对口播
    会把它们误判成编造。没传源文本时只查结构，并如实写明"没做回指"，
    不许让人以为"没列出来就是都过了"。
    `summary_type` 决定用哪套判据：那九节只属于 wechat_material，
    其余形态（game_guide / toutiao_mix …）走 `_audit_other_form`。
    """
    rows: List[Tuple[str, str, str]] = []
    md = md_text or ""
    # 九节口径只属于 wechat_material；不分形态硬套 = 40 篇假阳性（见 _audit_other_form）
    if summary_type and summary_type != "wechat_material":
        return _audit_other_form(md, source_text, summary_type)
    if LEGACY_BODY_TAG in md and "【正文】" not in md:
        # 存量 8 篇就是这一形态。判 FAIL 不是刁难：策略把公众号成品改成完整文章之后，
        # 骨架稿发出去就是一堆空话，得让人知道这一步没做完。
        rows.append(("FAIL", "成稿形态",
                     "还是旧的素材档案形态（只有【正文骨架】、没有【正文】）。"
                     "公众号交的是完整文章——重新生成这一档，或由本人把正文写出来再体检"))
    else:
        missing = [s for s in MATERIAL_SECTIONS if s not in md]
        if missing:
            rows.append(("FAIL", "分节完整",
                         "缺 %d 节：%s（【正文】是主体成品，其余短节给出图与登记用，缺节会拼出空卡）"
                         % (len(missing), "、".join(missing))))
        else:
            rows.append(("PASS", "分节完整", "%d 节齐" % len(MATERIAL_SECTIONS)))
        _article_body(md, rows)
    for name in MATERIAL_SECTIONS:
        if name in md and len(_section_body(md, name)) < 10:
            rows.append(("FAIL", "空节", "%s 有标题没内容" % name))
    for pat, label in MATERIAL_LEAK_PATTERNS:
        if re.search(pat, md):
            rows.append(("FAIL", "中间产物泄漏", "%s 命中" % label))
    _compliance(md, rows)   # 素材档案里就写着"关注回复领取"，出图会照抄进卡片
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
    return _finish_material(rows, md)
