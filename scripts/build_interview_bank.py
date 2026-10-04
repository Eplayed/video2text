# -*- coding: utf-8 -*-
"""把工作台里已经生成的 AI 面试题整理成一份能拿去卖的答案库（v0.1）。

为什么要这个脚本：产线一次生成一篇整理稿，题目散在 17 篇里、按"视频"分组、
还混着一批"这个视频讲了什么"的阅读理解题。买家是按主题翻的，所以这里做三件事：
① 按能不能直接拿去面试现场用做分级；② 按主题重新分组；③ 一份带目录的 markdown。

全程不调模型、0 元——答案本来就是产线生成好的，这一步只做筛选和拼装。

用法：
    python3 scripts/build_interview_bank.py                 # 输出到 仓库根/AI面试答案库-v0.1.md
    python3 scripts/build_interview_bank.py --out 路径      # 换输出位置
    python3 scripts/build_interview_bank.py --dry-run       # 只打印分级统计，不写文件
"""
import argparse
import json
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:15801"
BASE = "http://127.0.0.1:15801/media/ai_summaries"

# 主题桶：按顺序命中第一个（越靠前越具体）。判据是"面试官会怎么分类问"，不是素材来源。
THEMES = [
    ("多模态与语音", r"语音|ASR|转写|识别.*声音|图像|摄像头|视频理解|OCR|多模态|画面|TTS|配音|字幕"),
    ("前端体验与性能", r"CLS|瀑布流|虚拟列表|首屏|渲染|预加载|带宽|进度条|低端|动画|性能|兼容|移动端|H5"
                  r"|WebRTC|WebSocket|半路请求"),
    ("前端框架与语言", r"Vue|React|Webpack|Vite|HMR|响应式|构建|路由|Router|JavaScript|TypeScript|"
                  r"闭包|作用域|组件|浏览器|接口|超时|重试|异常"),
    ("Agent 与工具调用", r"[Aa]gent|工具调用|function[ _]?call|MCP|编排|工作流|规划|自主|多轮|任务分解|LLM工具|自动化报告"),
    ("RAG 与知识库", r"检索|向量|embedding|召回|知识库|切片|rerank|引用|文档问答|RAG|公文|格式转换|排版|PDF|表格"),
    ("大模型原理与长文本", r"幻觉|量化|微调|LoRA|上下文|token|注意力|预训练|对齐|蒸馏|推理|显存|采样|提示词|prompt"
                     r"|长线记忆|长文本|记忆|前后矛盾|一致性|分章|网文"),
    ("AI 面试与对话产品", r"面试者|面试官|多角色|对话|语义分析|专业程度|评估.*回答|追问"),
    ("数据采集与反爬", r"爬虫|反爬|抓取|页面结构|采集|解析"),
    ("数据与安全", r"数据一致|校验|清洗|脱敏|隐私|权限|加密|安全|审计|合规|差异检测"),
    ("系统设计与工程落地", r"架构|链路|高并发|缓存|队列|幂等|灰度|监控|部署|成本|吞吐|可用性|全链路|扩展"
                     r"|设计.*方案|实现方案|匹配算法|测试用例|质量保障|上传"),
    ("AI 工具选型与工程边界", r"开源|选型|评估.*维度|采用|局限|无法替代|替代|边界|风险|效率"),
    ("产品判断与需求", r"用户|留存|粘性|场景|需求|指标|转化|体验|商业|定价|运营|闭环|学习路径"),
]
FALLBACK_THEME = "AI 应用落地综合"

# 题干里只写"该工具/该平台"的，要换成能说出口的名字。名字从来源整理稿标题人工核过一遍——
# 自动从标题里截短语不靠谱（"AI生成的React组件能直接上线吗"会截成"AI生成"），
# 所以这里只放确认过的；不在表里的宁可留一句"需人工补主语"，也不编一个名字出去砸口碑。
SUBJECT = {
    13: "一个开源网文写作助手",
    14: "一个开源爬虫项目",
    15: "一个开源 AI 研发工具",
    16: "一个追剧平台",
    17: "一个 AI 模拟面试系统",
    18: "一个跨表数据自动核对与报告生成工具",
    19: "一个 AI 公文写作工具",
    20: "一个基于简历的智能求职助手（开源项目）",
    21: "一个全球实时摄像头网站",
}
# 去掉"离开原视频就读不通"的从句（这些短语去掉后句子仍然完整）
STRIP = [r"请结合口播内容说明。?", r"请结合口播信息，?", r"根据视频口播内容，?", r"根据视频内容，?",
         r"根据视频口播，?", r"结合视频内容，?", r"结合口播，?", r"视频中提到", r"在视频中，?",
         r"该视频", r"口播中提到的", r"口播中"]
NOUN = re.compile(r"该(开源)?(视频)?(工具|项目|平台|网站|系统)")
# 模型偶尔会把西里尔字母吐进中文答案（实测 #18 的"表结构 адаптация"）。
# 能确定原意的先换回来，对不上的必须标出来——这份是要卖出去的，留一个乱码词就是差评。
CYR = re.compile(r"[Ѐ-ӿ]{3,}")
FIX = {"адаптация": "适配"}


def sanitize(s):
    """清掉答案里的乱码词，返回 (文本, 处理不了的残留列表)。"""
    s = s or ""
    left = []
    for w in CYR.findall(s):
        s = s.replace(w, FIX.get(w, w))
        if w not in FIX:
            left.append(w)
    return s, left

# 离开原视频就看不懂的信号：主语是"这个/该/它"，或者题干直接要求"根据视频/口播"复述。
BARE_SUBJECT = re.compile(r"^(这个|这些|该|它|视频中|根据视频|结合口播|口播中|在视频中|上面|刚才)")
NEEDS_SOURCE = re.compile(r"根据视频|结合口播|口播中|视频中(提到|说|讲)|这段视频|该视频")
# 真正的技术/场景名词：有它说明题目自带上下文，最多是把"该工具"换成具体名字。
NAMED = re.compile(
    r"[A-Za-z]{2,}|模型|接口|算法|架构|数据库|缓存|向量|语音|图像|面试|简历|客服|报告|会议|公文|表格|摄像头"
    r"|反爬|爬虫|预加载|带宽|请求|上传|闭包|作用域|响应式|构建|路由|超时|重试|异常|记忆|一致性|测试用例"
    r"|开源|排版|转换|设计|实现|原理|机制|方案|维度|局限|风险|权限|隐私|加密|校验")


def fetch_questions():
    """从工作台读全部 ai_interview 整理稿，摊平成题目列表（带来源编号）。"""
    url = API + "/api/content/summaries?type=ai_interview"
    d = json.loads(urllib.request.urlopen(url, timeout=60).read().decode())
    out = []
    for it in d.get("items") or []:
        sd = {}
        try:
            sd = json.loads(it.get("structured_data") or "{}")
        except (TypeError, ValueError):
            print("  跳过 #%s：structured_data 不是合法 JSON" % it.get("id"))
            continue
        for q in sd.get("questions") or []:
            if not isinstance(q, dict):
                continue
            q = dict(q)
            q["_sid"] = it.get("id")
            q["_stitle"] = (it.get("title") or "").strip()
            out.append(q)
    return out


def theme_of(q):
    hay = "%s %s" % (q.get("knowledgeTag") or "", q.get("text") or "")
    for name, pat in THEMES:
        if re.search(pat, hay):
            return name
    return FALLBACK_THEME


def grade(q):
    """三级：A 直接能用 / B 把"该工具"换成具体名字就能用 / C 就是问视频本身，丢掉。

    判据只有一条：不看原视频，这题还成不成立。
    句中出现"这些/它"不算问题（"这些半路请求如何取消"是完全自足的好题），
    真正不能要的是主语就是"这个网站"、或者要求"根据口播复述"的那几道。
    """
    text = (q.get("text") or "").strip()
    ans = (q.get("referenceAnswer") or "").strip()
    if not text:
        return "C"
    needs_source = bool(NEEDS_SOURCE.search(text))
    bare = bool(BARE_SUBJECT.search(text))
    named = bool(NAMED.search(re.sub(r"^(这个|这些|该|它)", "", text)))
    if needs_source and not named:
        return "C"
    if bare and not named:
        return "C"
    if needs_source or bare:
        return "B" if len(ans) >= 60 else "C"
    return "A"


def clean(s, limit=1200):
    s = re.sub(r"\s*\n\s*", " ", sanitize(s or "")[0].strip())
    return s if len(s) <= limit else s[:limit].rstrip() + "……"


def rewrite(q):
    """把"该工具/视频中提到"这类指代改成能独立读通的问法（只动题干与答案里的口头禅）。

    返回 (新题干, 是否还需要人工补主语)。名字只认 SUBJECT 里核过的，
    表里没有的来源不硬编名字，标出来让人自己补。
    """
    text = (q.get("text") or "").strip()
    src = q.get("_sid")
    named = SUBJECT.get(src)
    for pat in STRIP:
        text = re.sub(pat, "", text)
    has_noun = bool(NOUN.search(text))
    if named:
        text = NOUN.sub(named, text)
    text = re.sub(r"[，、：;；]\s*(?=？|\?)", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ，、；。")
    if text and not text.endswith(("？", "?", "。")) and re.search(
            r"(如何|怎样|为什么|哪些|是什么|吗|能否|怎么|谈谈|讨论|请|设计|分析|说明|解释)", text):
        text += "？"
    return text, (has_noun and not named)


def render(questions, counts):
    """按主题分组拼 markdown。"""
    groups = {}
    for q in questions:
        text, unresolved = rewrite(q)
        q["_text"], q["_unresolved"] = text, unresolved
        groups.setdefault(theme_of(q), []).append(q)
    order = [t for t, _ in THEMES] + [FALLBACK_THEME]
    today = datetime.now().strftime("%Y-%m-%d")

    lines = ["# AI 应用方向面试答案库 v0.1", ""]
    lines += ["> 整理日期 %s ｜ 共 %d 道可直接准备的题 ｜ 来源：公开分享内容的转述整理（AI 辅助加工，已逐条复核题干）"
              % (today, len(questions)), ""]
    lines += ["**怎么用**：每道题三段——「参考答案」是能开口说的完整版，先照它复述一遍；"
              "「常见坑」是面试官一听就知道你没做过的点；「追问」是答完第一句之后一定会被问的，"
              "提前把答案想好。数字、时间、公司名一律换成你自己项目里的，背模板最容易被问穿。", ""]
    lines += ["**范围**：只收 AI 应用落地这条线（大模型 / RAG / Agent / 多模态 / 前端与体验 / 数据与安全 / 系统设计），"
              "不收算法岗推导题。", ""]

    lines += ["## 目录", ""]
    n = 0
    for t in order:
        qs = groups.get(t) or []
        if not qs:
            continue
        lines += ["**%s（%d 题）**" % (t, len(qs)), ""]
        for q in qs:
            n += 1
            q["_no"] = n
            lines += ["%d. %s" % (n, clean(q["_text"], 46))]
        lines += [""]
    lines += ["", "## 题目", ""]
    for t in order:
        qs = groups.get(t) or []
        if not qs:
            continue
        lines += ["", "### %s" % t, ""]
        for q in qs:
            blob = " ".join([str(q.get(k) or "") for k in ("text", "referenceAnswer", "explain", "deeper")]
                            + [str(x) for x in (q.get("answer") or [])])
            _, bad = sanitize(blob)
            lines += ["**%d. %s**" % (q["_no"], clean(q["_text"])), ""]
            if bad:
                lines += ["> 这条答案里有模型吐错的乱码词（%s），发布前必须改掉。"
                          % "、".join(sorted(set(bad))), ""]
            if q["_unresolved"]:
                lines += ["> 题干里的指代还没换成具体名字（来源整理稿 #%s），发布前人工补一句。"
                          % q.get("_sid"), ""]
            ref = clean(q.get("referenceAnswer")).replace("口播", "原分享")
            if ref:
                lines += ["- 参考答案：%s" % ref, ""]
            pts = [p for p in (q.get("answer") or []) if clean(p)]
            if pts:
                lines += ["- 答到这几个点就算过：%s" % "、".join(clean(p, 40) for p in pts[:5]), ""]
            ex = clean(q.get("explain")).replace("口播", "原分享")
            if ex:
                lines += ["- 常见坑：%s" % ex, ""]
            dp = clean(q.get("deeper")).replace("口播", "原分享")
            if dp:
                lines += ["- 面试官会追问：%s" % dp, ""]
            lines += ["", "---", ""]

    lines += ["", "## 这份还没做到的（v0.2 要补的）", ""]
    lines += ["- 手撕代码题（快排、LRU、并发限制、防抖节流）一条没有，这条线面试必考；",
              "- 场景题没给「按你自己项目怎么答」的填空模板，现在还是通用答案；",
              "- 缺一份「我做过什么」的项目追问清单——那才是决定 offer 的部分；",
              "- 数字类事实（模型参数量、延迟、成本）没有逐条核到公开来源，引用前自己确认。", ""]
    lines += ["## 来源与说明", ""]
    lines += ["内容是对公开分享（视频/文章）的转述整理，用 AI 辅助做题目化与归纳，"
              "已剔除离开原文就看不懂的题（本次剔除 %d 道、改写主语后保留 %d 道）。"
              % (counts.get("C", 0), counts.get("B", 0)),
              "所有数字与技术结论请在使用前自行核对，本资料不构成任何平台的官方说法。", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "AI面试答案库-v0.1.md"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw = fetch_questions()
    counts = Counter(grade(q) for q in raw)
    print("摊平题目 %d 道 ｜ A 直接能用 %d ｜ B 补主语能用 %d ｜ C 离开原视频看不懂（丢） %d"
          % (len(raw), counts["A"], counts["B"], counts["C"]))
    keep = [q for q in raw if grade(q) in ("A", "B")]
    dist = Counter(theme_of(q) for q in keep)
    for t, n in dist.most_common():
        print("   %-14s %d 题" % (t, n))
    garbled = [q for q in keep if sanitize(" ".join(
        [str(q.get(k) or "") for k in ("text", "referenceAnswer", "explain", "deeper")]
        + [str(x) for x in (q.get("answer") or [])]))[1]]
    unresolved = [q for q in keep if rewrite(q)[1]]
    print("发布前要人工过一遍：乱码词 %d 道、指代没名字 %d 道" % (len(garbled), len(unresolved)))
    if args.dry_run:
        return 0
    md = render(keep, counts)
    Path(args.out).write_text(md, encoding="utf-8")
    print("已写出：%s（%d 题，%d 字）" % (args.out, len(keep), len(md)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
