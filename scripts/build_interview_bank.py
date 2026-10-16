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
HANDCODE = ROOT / "product" / "handcode.json"
REPO_BANK = ROOT / "product" / "repo_bank.json"
VERSION = "0.4"
# 外部题库的署名（MIT 要求保留版权声明，2026-10-09 从仓库 LICENSE 原文抄）：
REPO_NAME = "didilili/ai-agents-from-zero"
REPO_COPYRIGHT = "Copyright (c) 2026 didilili · MIT License"
# 定价 2026-10-08 拍板：7.9。依据是当天闲鱼实测——同类聚合货（1400 页 PDF）标 1–12 元、
# "验证过的真题"类 35–50 元。7.9 是"名片价"不是"收入价"：靠它到 2000 元/月要 253 单，
# 所以钱的大头押在服务上，这里只求"有人愿意为信息付一次钱"这件事被验证。
PRICE = "7.9"


def load_handcode():
    """手撕代码题的原料。跑不通的一律不进这份资料（先过 scripts/verify_handcode.py）。"""
    if not HANDCODE.exists():
        return []
    try:
        return (json.loads(HANDCODE.read_text(encoding="utf-8")).get("items")) or []
    except (ValueError, OSError):
        print("   手撕代码题文件读失败，这一节先不出")
        return []


def _flat(s):
    """外部题库的答案是分小节的 markdown，拼成一段才和我们自家题的「参考答案」同一形态。
    项目符号换成「；」，段落之间的换行直接接上（中文不需要空格）。
    接缝要清一遍：原文"…三层展开。"后面紧跟列表，直接拼会出来"。；"这种两个标点连着的样子。"""
    s = re.sub(r"\n\s*[-*]\s*", "；", s or "")
    s = re.sub(r"\s*\n\s*", "", s)
    s = re.sub(r"([。！？；])；", r"\1", s)
    return re.sub(r"；{2,}", "；", s).strip("； ")


def load_repo_bank():
    """第二数据源：GitHub 仓库整理好的 89 题（`scripts/import_repo_bank.py` 落的 JSON）。

    2026-10-09 拍板搬进付费库（MIT，可商用，须留版权声明）。三条硬规矩写在这里：
    ① 只搬**题干 + 答案 + 它自己的追问**，不编我们没写的「得分点」「常见坑」——
       渲染器对空段是不输出的，所以这 89 题看着比自家题短一截，那是事实不是漏了；
    ② 每题带来源徽标（_src / _freq / _diff / _repo_id），.md 与 PDF 的来源页必须出现署名；
    ③ 它的「出现次数：N 次」是**原作者统计的**，徽标一律写「据原仓库」，不能冒充我们测的。
    """
    if not REPO_BANK.exists():
        print("   没有 product/repo_bank.json，这一版不并外部题（先跑 scripts/import_repo_bank.py）")
        return []
    try:
        doc = json.loads(REPO_BANK.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        print("   repo_bank.json 读失败，这一版不并外部题")
        return []
    repo = ((doc.get("source") or {}).get("repo")) or REPO_NAME
    out = []
    for it in doc.get("items") or []:
        q = dict(it)
        q["text"] = (it.get("question") or "").strip()
        q["referenceAnswer"] = _flat(it.get("answer"))
        q["answer"] = []
        q["explain"] = ""
        # 它的追问每条自带问号，用空格并列就行；用"；"会拼出"？；"这种连着两个标点
        q["deeper"] = " ".join(x for x in (it.get("followup") or []) if x)
        q["knowledgeTag"] = ""          # 别拿它的节名去喂关键词判据，见 theme_order 的注释
        q["_theme"] = EXT_PREFIX + (it.get("section") or "未分节")
        m = re.match(r"Q(\d+)-", it.get("id") or "")
        q["_sec_no"] = int(m.group(1)) if m else 99
        q["_src"] = "repo"
        q["_repo"] = repo
        q["_sid"] = "repo:%s" % it.get("id")
        out.append(q)
    return out


def prepare(q):
    """统一入口：自家题要过改写（去掉"该工具/视频中"），外部仓库题题干本来就自足，
    再进一遍 STRIP/OVERRIDE 只会把干净的题干改坏。"""
    if q.get("_src") == "repo":
        q["_text"] = (q.get("text") or "").strip()
        q["_unresolved"] = False
        return q
    q["_text"], q["_unresolved"] = rewrite(q)
    return q


def src_badge(q):
    """外部题的来源徽标；自家题返回空串（没测过出现次数，不假装有）。"""
    if q.get("_src") != "repo":
        return ""
    bits = []
    if q.get("freq"):
        bits.append("%s（出现 %d 次，据原仓库）" % (q["freq"], int(q.get("freq_n") or 0)))
    if q.get("difficulty"):
        bits.append("难度 %s" % q["difficulty"])
    bits.append("来源：%s" % (q.get("_repo") or REPO_NAME))
    return " · ".join(bits)


def render_handcode(items, start_no):
    """按"面试官原话 → 参考实现 → 考点 → 常见坑 → 追问"排，代码块用围栏，能直接复制去跑。"""
    if not items:
        return [], start_no
    n = start_no
    lines = ["", "## 手撕代码题（%d 道，每条参考实现都在 node 里真跑过）" % len(items), ""]
    lines += ["这一节的代码不是「看起来对」，是执行过的：`python3 scripts/verify_handcode.py` 会逐条跑断言。"
              "面试官要的不是你背得出，是你写的时候知道边界在哪。", ""]
    for it in items:
        n += 1
        lines += ["**%d. %s**（%s）" % (n, it.get("title") or "", it.get("theme") or ""), ""]
        if it.get("ask"):
            lines += ["面试官会这么说：%s" % it["ask"], ""]
        lines += ["```js", "\n".join(it.get("code") or []), "```", ""]
        if it.get("points"):
            lines += ["- 考点：" + "；".join(it["points"]), ""]
        if it.get("traps"):
            lines += ["- 常见错：" + "；".join(it["traps"]), ""]
        if it.get("followup"):
            lines += ["- 追问：" + it["followup"], ""]
        lines += ["", "---", ""]
    return lines, n
BASE = "http://127.0.0.1:15801/media/ai_summaries"

# 主题桶：按顺序命中第一个（越靠前越具体）。判据是"面试官会怎么分类问"，不是素材来源。
THEMES = [
    ("多模态与语音", r"语音|ASR|转写|识别.*声音|图像|摄像头|视频理解|OCR|多模态|画面|TTS|配音|字幕"),
    ("前端体验与性能", r"CLS|瀑布流|虚拟列表|首屏|渲染|预加载|带宽|进度条|低端|动画|性能|兼容|移动端|H5"
                  r"|WebRTC|WebSocket|半路请求"),
    ("前端框架与语言", r"Vue|React|Webpack|Vite|HMR|响应式|构建|路由|Router|JavaScript|TypeScript|"
                  r"闭包|作用域|组件|浏览器|接口|超时|重试|异常"),
    ("Agent 与工具调用", r"[Aa]gent|工具调用|function[ _]?call|MCP|编排|工作流|自主|多轮|任务分解|LLM工具|自动化报告"),
    ("RAG 与知识库", r"检索|向量|embedding|召回|知识库|切片|rerank|引用|文档问答|RAG"),
    ("大模型原理与长文本", r"幻觉|量化|微调|LoRA|上下文|token|注意力|预训练|对齐|蒸馏|推理|显存|采样|提示词|prompt"
                     r"|长线记忆|长文本|长篇|长文|前后矛盾|分章|信息密度|节奏|上下文窗口"),
    ("AI 面试与对话产品", r"面试者|面试官|多角色|对话|语义分析|专业程度|评估.*回答|追问"),
    ("数据采集与文档处理", r"爬虫|反爬|抓取|页面结构|采集|解析|排版|PDF|格式转换|文档处理"),
    ("数据与安全", r"数据一致|差异检测|脱敏|隐私|权限|加密|审计|合规|数据安全|校验|XSS|CORS|防御|攻击"),
    ("系统设计与工程落地", r"架构|链路|高并发|缓存|队列|幂等|灰度|监控|部署|成本|吞吐|可用性|全链路|扩展|集成"
                      r"|设计.*方案|实现方案|匹配算法|测试用例|质量保障|上传"),
    ("AI 工具选型与工程边界", r"开源|选型|评估.*维度|采用|局限|无法替代|替代|边界|风险|效率"),
    ("产品判断与需求", r"用户|留存|粘性|场景|需求|指标|转化|体验|商业|定价|运营|闭环|学习路径"),
]
FALLBACK_THEME = "AI 应用落地综合"
EXT_PREFIX = "外部 · "        # 外部题库单独成一块：不混进我们那 13 个主题里


def theme_order(groups):
    """渲染顺序：自家 13 个主题（含兜底）在前，外部题库按它自己的节序在后。
    为什么不按关键词把外部题打散并进我们的主题：两类题的段数不一样（我们四段、它们两段），
    混在一节里买家会以为漏写了；而且打散会把自家 76 题的编号挪掉——引流稿、知识地图、
    工具包里引用的题号会全部失效（2026-10-09 拍板分块）。"""
    own = [t for t, _ in THEMES] + [FALLBACK_THEME]
    ext = sorted([t for t in groups if t not in own],
                 key=lambda t: groups[t][0].get("_sec_no") or 99)
    return own + ext


def is_ext(theme):
    return (theme or "").startswith(EXT_PREFIX)

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
        rep = FIX.get(w, w)
        s = s.replace(w, rep)
        if rep and "\u4e00" <= rep[0] <= "\u9fff":
            s = s.replace(" " + rep, rep)
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
    if q.get("_theme"):
        return q["_theme"]        # 外部题的归属是人映射的（见 REPO_THEME），不交给正则猜
    # 按**改写后**的题干分主题：改写前题干里全是"该工具/公文/网文"这类来源词，
    # 拿原文分类会把公文写作题一路送进 RAG 桶（v0.2 的错位就是这么来的）。
    hay = "%s %s" % (q.get("knowledgeTag") or "", q.get("_text") or q.get("text") or "")
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
    if q.get("_src") == "repo":
        return "A"        # 外部仓库的题是按"能独立问"编的，别再拿"离不离得开视频"这套判据过
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


# v0.3 人工改写表。为什么不按题号而按题干片段匹配：题号是渲染时按主题顺序现排的，
# 加一道题、改一次主题归属就会整体错位；按原文片段匹配，重跑多少次结果都一样。
# 每条都在 product/AI面试答案库-v0.3.md 上由 scripts/audit_answer_bank.py 验过（FAIL 0 / WARN 0）。
OVERRIDE_Q = [
    ("你认为这种全球摄像头观看平台",
     "全球公开摄像头直播平台（用户可实时查看各地公共机位画面）适合哪些应用场景？"
     "这些场景对 AI 能力分别提出什么要求？"),
    ("这个项目如何做到“全程不用改代码”",
     "动态加载内容（如 JavaScript 渲染的页面）对爬虫有什么挑战？"
     "如何设计一套不用为每个站点改代码、能自动适配页面结构变化的抓取方案？"),
    ("如何通过规划技巧控制长篇网文的节奏密度",
     "生成长篇内容（长文档、长剧本、连续报告）时，如何用规划技巧控制信息密度与节奏，"
     "避免中段塌陷、前后失衡？"),
    ("如何设计一个AI写作助手，使其能够根据用户输入的标题，自动生成结构完整、包含逻辑、数据和案例的千字公文",
     "如何设计一个 AI 写作助手，根据用户输入的标题自动生成结构完整、包含逻辑、数据和案例的长文？"),
    ("工具支持一键排版和PDF转换、拆分等免费功能",
     "一个办公工具支持一键排版、PDF 转换与拆分。从工程角度，如何实现这些文档处理功能？"),
    ("公文写作工具将多种AI功能",
     "一个 AI 产品把多种能力（写作、会议记录、排版、格式转换）整合在同一个站点里。"
     "请从产品和技术角度讨论：如何有效集成多种 AI 能力并保证用户体验？"),
    ("如何在长篇网文创作中解决大模型的长线记忆问题",
     "如何在长篇内容生成中解决大模型的长线记忆问题（前后设定不一致）？"),
    ("一个开源网文写作助手是如何解决长篇生成中的前后矛盾问题",
     "长文本生成如何保证跨章节的设定一致（人物、数值、术语不冲突）？请给出可验证的工程方案。"),
    ("并说明这个项目声称“自动适配反爬手段”",
     "网站常见的反爬手段有哪些？请列举至少三种，并说明"
     "「自动适配反爬手段」这类说法在工程上可能对应哪些策略？"),
    ("这个项目声称“开源爬虫项目，让你成为爬虫大师”",
     "一个开源爬虫项目宣称「让你成为爬虫大师」。你认为它作为学习材料有哪些局限性？"
     "工程实践中又该如何规避这类局限？"),
    ("请结合的开源工具，阐述AI如何自动补全需求",
     "结合一个你实际用过的开源 AI 研发工具，阐述 AI 如何自动补全需求、生成代码并完成测试，"
     "让一个人具备完整研发团队的能力？"),
    ("但视频中也隐含了“一个人开公司”的挑战",
     "尽管 AI 工具能大幅提高开发效率，但「一个人开一家公司」的说法隐含了工程上的挑战。"
     "你认为 AI 工具在软件工程中有哪些无法替代的环节？"),
    ("的工具是开源的，如果你是个人开发者",
     "评估一个开源 AI 工具是否值得采用，你会从哪些维度判断？"
     "（许可证、可维护性、二次开发接口、社区活跃度…）"),
    ("分层记忆在AI写作中是如何应用的",
     "分层记忆机制在长文本生成中是如何应用的？"),
]
# 答案正文里的同类残留（题干改通用了、答案还在讲网文/公文，比不改更刺眼——买家读的就是答案）
OVERRIDE_A = [
    ("网文需要张弛有度的节奏，否则读者容易疲劳或无聊。核心机制：该项目提供整套写作规划技巧，"
     "包括章节目标设定、冲突密度安排、伏笔与爆发点的布局，从而精准把控节奏。例如在关键情节提高事件密度，"
     "在过渡章节降低冲突强度。",
     "长篇内容（连续报告、剧本、系列文档）同样需要张弛有度，否则读者疲劳或失去主线。"
     "核心机制：靠一套规划技巧把控节奏——章节目标设定、信息密度安排、伏笔与爆发点的布局。"
     "例如在关键节点提高信息密度，在过渡段落降低强度。"),
    ("统计每章的冲突密度和情节推进速度", "统计每章的信息密度与推进速度"),
    ("视频中的工具仅需输入标题即可生成千字公文，说明其背后需要强大的自然语言生成能力。",
     "输入标题就能生成千字长文的工具，背后需要强大的自然语言生成能力。"),
    ("结合公文领域微调，同时构建包含公文逻辑结构（如背景、意义、措施、总结）的模板库",
     "结合目标领域语料微调，同时构建包含常见长文逻辑结构（背景—论据—措施—总结）的模板库"),
    ("通过人工评估生成公文的逻辑连贯性", "通过人工评估生成内容的逻辑连贯性"),
    ("如果用户要求生成特定领域的公文（如金融、医疗）",
     "如果用户要求生成特定领域的长文（如金融研报、医疗科普）"),
    ("以及排版是否符合党政机关公文格式国家标准",
     "以及排版是否符合目标格式规范（论文模板、企业内部模板、行业标准文档）"),
    ("在超长文本生成中容易遗忘早期情节", "在超长文本生成中容易遗忘早期设定"),
    ("该项目采用分层记忆机制", "可行做法是分层记忆机制"),
    ("该项目提供的整套规划", "可行的做法是一套规划"),
    ("将人物设定、主线目标、副线等核心信息分层存储，并结合完整的写作规划技巧",
     "将实体设定、主线目标、支线信息等核心内容分层存储，并结合完整的大纲规划"),
    ("通过生成超长章节后人工或自动比对关键设定", "通过生成超长内容后人工或自动比对关键设定"),
    ("AI写作长篇小说时，既需要保持核心人物和主线的稳定性，又需要灵活生成不同场景的细节。",
     "AI 生成长篇内容时，既需要保持核心设定与主线的稳定性，又需要灵活生成不同片段的细节。"),
    ("分层记忆将信息分为长期稳定的核心层（如人物性格、主线目标）和短期动态的细节层"
     "（如当前场景的具体状态）。创作时核心层始终作为约束条件，细节层则随进度更新",
     "分层记忆将信息分为长期稳定的核心层（如实体设定、主线目标）和短期动态的细节层"
     "（如当前片段的具体状态）。生成时核心层始终作为约束条件，细节层则随进度更新"),
    ("但需基于视频中的‘实时景色’和‘覆盖多国’来合理推断",
     "但需基于「实时景色」「覆盖多国」这两个产品特征来合理推断"),
    ("此题考察对视频内容的总结归纳能力，注意不要遗漏任何一个功能点。",
     "此题考察对产品功能拆解的归纳能力，注意不要遗漏关键功能点。"),
    ("问题背景：视频介绍了一款开源工具，它整合了", "问题背景：有一类开源工具整合了"),
    ("核心机制：视频中未给出标准答案，但结合前文提到的PPR和边缘渲染可以推导",
     "核心机制：没有现成的标准答案，但可以顺着 PPR 与边缘渲染推导"),
    ("视频中未给出具体算法，但这是可行的通用思路",
     "这里给的是通用思路，实际算法要按业务数据调"),
]


def afix(s):
    """答案正文的精确替换。宁可一条都不同改，也不要模糊匹配把别处改坏。"""
    for old, new in OVERRIDE_A:
        s = s.replace(old, new)
    return s


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
    # 人工改写放在流水线的**最后**：表里的片段是从渲染出来的题干抄的，
    # 而 STRIP（去掉"根据视频内容"）和 SUBJECT（把"该开源项目"换成具体名字）会先改一遍，
    # 放在最前面会一条都匹配不上（2026-10-08 实测踩过）。
    for frag, new in OVERRIDE_Q:
        if frag and frag in text:
            return new, False
    return text, (has_noun and not named)


def load_all():
    """一条口取数：自家题 + 外部题库 + 手撕代码，分好组、编好号。
    .md / 卖货页 / PDF 三个出口都调这个——之前各自抄一遍筛选与编号，
    改一处漏两处（v0.3 免费样章漏 afix、以及"95"散在 13 处对不上，都是这么来的）。"""
    raw = fetch_questions()
    counts = Counter(grade(q) for q in raw)
    repo = load_repo_bank()
    keep = [q for q in raw if grade(q) in ("A", "B")] + repo
    for q in keep:
        prepare(q)
    groups = {}
    for q in keep:
        groups.setdefault(theme_of(q), []).append(q)
    order = theme_order(groups)
    n = 0
    for t in order:                      # 题号按出现顺序编，三个出口的编号才一致
        for q in groups.get(t) or []:
            n += 1
            q["_no"] = n
    return {"raw": raw, "keep": keep, "groups": groups, "order": order, "counts": counts,
            "handcode": load_handcode(), "n_repo": len(repo), "n_own": len(keep) - len(repo)}


def audit_gate():
    """出付费产物（.md / PDF / 卖货页）之前先跑一遍上架自检，返回 (能不能发, 一句话结论)。

    为什么让构建脚本自己去跑，而不是在文案里写死「FAIL 0 / WARN 0」：
    那句话是要卖给买家看的，库一改、加一题，话就变成假的——v0.2 翻在"承诺了没做到"上，
    不能再让同一个承诺靠记性维持。
    """
    import audit_answer_bank as aab       # noqa: E402  audit 反过来要用 VERSION，放函数里避免绕圈
    path = ROOT / "product" / ("AI面试答案库-v%s.md" % VERSION)
    if not path.is_file():
        return False, "没找到 %s（先跑 scripts/build_interview_bank.py）" % path
    rows, scene, code = aab.audit(str(path))
    fails = len([r for r in rows if r[0] == "FAIL"])
    warns = len([r for r in rows if r[0] == "WARN"])
    return fails == 0, "必须改 %d 条 / 观察项 %d 条（共 %d 题）" % (fails, warns, scene + code)


def render(questions, counts, handcode=()):
    """按主题分组拼 markdown；手撕代码题单独一大节，编号接着往下排。"""
    groups = {}
    for q in questions:
        prepare(q)
        groups.setdefault(theme_of(q), []).append(q)
    order = theme_order(groups)
    today = datetime.now().strftime("%Y-%m-%d")
    total = len(questions) + len(handcode)
    n_repo = len([q for q in questions if q.get("_src") == "repo"])
    n_own = len(questions) - n_repo

    lines = ["# AI 应用方向面试答案库 v%s" % VERSION, ""]
    lines += ["> 整理日期 %s ｜ 共 %d 道（%d 道场景与原理题 + %d 道手撕代码）" % (today, total, len(questions), len(handcode)), ""]
    lines += ["> 题目来源两处：**%d 道**是对公开分享（视频 / 文章）的转述整理（AI 辅助加工，已逐条复核题干）；"
              "**%d 道**取自 GitHub 仓库 %s（%s），题干与答案沿用其整理，其「出现次数 / 难度」为原作者统计、我们未复核。"
              "带来源徽标的那几道一眼能分出来。代码题全部在本机执行过。" % (n_own, n_repo, REPO_NAME, REPO_COPYRIGHT), ""]
    lines += ["**怎么用**：每道题最多四段——「参考答案」是能开口说的完整版，先照它复述一遍；"
              "「答到这几个点就算过」是查漏清单；「常见坑」是面试官一听就知道你没做过的点；"
              "「追问」是答完第一句之后一定会被问的，提前把答案想好。"
              "外部仓库那部分只给了参考答案与追问，没编得分点和坑——那是下一版的活。"
              "数字、时间、公司名一律换成你自己项目里的，背模板最容易被问穿。", ""]
    lines += ["**范围**：只收 AI 应用落地这条线（大模型 / RAG / Agent / MCP / LangGraph / 多模态 / 前端与体验 / 数据与安全 / 系统设计），"
              "不收算法岗推导题。", ""]

    lines += ["## 目录", ""]
    n = 0
    div = False
    for t in order:
        qs = groups.get(t) or []
        if not qs:
            continue
        if is_ext(t) and not div:
            # 用粗体行而不是 ### 分块：### 是自检脚本切小节的标记，别拿它当分隔线
            lines += ["**—— 第二部分：外部题库 %d 题（MIT 署名，段数与第一部分不同，见文末来源页）——**" % n_repo, ""]
            div = True
        lines += ["**%s（%d 题）**" % (t, len(qs)), ""]
        for q in qs:
            n += 1
            q["_no"] = n
            lines += ["%d. %s" % (n, clean(q["_text"], 46))]
        lines += [""]
    if handcode:
        lines += ["**手撕代码（%d 题，代码跑过）**" % len(handcode), ""]
        for i, it in enumerate(handcode, 1):
            lines += ["%d. %s" % (n + i, clean(it.get("title"), 46))]
        lines += [""]
    lines += ["", "## 题目", ""]
    div = False
    for t in order:
        qs = groups.get(t) or []
        if not qs:
            continue
        if is_ext(t) and not div:
            lines += ["", "**—— 第二部分：外部题库（题干与答案沿用该仓库整理，其「出现次数/难度」为原作者统计）——**", ""]
            div = True
        lines += ["", "### %s" % t, ""]
        for q in qs:
            blob = " ".join([str(q.get(k) or "") for k in ("text", "referenceAnswer", "explain", "deeper")]
                            + [str(x) for x in (q.get("answer") or [])])
            _, bad = sanitize(blob)
            lines += ["**%d. %s**" % (q["_no"], clean(q["_text"])), ""]
            badge = src_badge(q)
            if badge:
                lines += ["> %s" % badge, ""]
            if bad:
                lines += ["> 这条答案里有模型吐错的乱码词（%s），发布前必须改掉。"
                          % "、".join(sorted(set(bad))), ""]
            if q["_unresolved"]:
                lines += ["> 题干里的指代还没换成具体名字（来源整理稿 #%s），发布前人工补一句。"
                          % q.get("_sid"), ""]
            ref = afix(clean(q.get("referenceAnswer"))).replace("口播", "原分享")
            if ref:
                lines += ["- 参考答案：%s" % ref, ""]
            pts = [p for p in (q.get("answer") or []) if clean(p)]
            if pts:
                lines += ["- 答到这几个点就算过：%s" % "、".join(clean(p, 40) for p in pts[:5]), ""]
            ex = afix(clean(q.get("explain"))).replace("口播", "原分享")
            if ex:
                lines += ["- 常见坑：%s" % ex, ""]
            dp = afix(clean(q.get("deeper"))).replace("口播", "原分享")
            if dp:
                lines += ["- 面试官会追问：%s" % dp, ""]
            lines += ["", "---", ""]

    hc_lines, n = render_handcode(handcode, n)
    lines += hc_lines

    lines += ["", "## 这份还没做到的（下一版要补的）", ""]
    lines += ["- 外部仓库那 %d 道只有参考答案和追问，没有「答到就算过」和「常见坑」——补这两段要逐题重写，"
              "不是套模板能套出来的；" % n_repo,
              "- 场景题没给「按你自己项目怎么答」的填空模板，现在还是通用答案；",
              "- 缺一份「我做过什么」的项目追问清单——那才是决定 offer 的部分；",
              "- 手撕代码只覆盖了接口工程与前端高频，图论/动态规划那类算法题不在范围内；",
              "- 数字类事实（模型参数量、延迟、成本）没有逐条核到公开来源，引用前自己确认。", ""]
    lines += ["## 来源与说明", ""]
    lines += ["自家整理的 %d 道：对公开分享（视频/文章）的转述整理，用 AI 辅助做题目化与归纳，"
              "已剔除离开原文就看不懂的题（本次剔除 %d 道、改写主语后保留 %d 道）。"
              % (n_own, counts.get("C", 0), counts.get("B", 0)),
              "外部题库的 %d 道：取自 GitHub 仓库 %s（%s）。MIT 许可允许商用与再分发，条件是保留版权声明，"
              "署名在此；其「出现次数」「难度」两个字段是原作者统计的，我们没复核，看的时候当参考不当事实。"
              % (n_repo, REPO_NAME, REPO_COPYRIGHT),
              "手撕代码题的参考实现由 `scripts/verify_handcode.py` 在 node 里逐条跑断言，跑不通的不进这份资料。",
              "所有数字与技术结论请在使用前自行核对，本资料不构成任何平台的官方说法。", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "product" / ("AI面试答案库-v%s.md" % VERSION)))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw = fetch_questions()
    repo = load_repo_bank()
    handcode = load_handcode()
    counts = Counter(grade(q) for q in raw)
    print("摊平题目 %d 道 ｜ A 直接能用 %d ｜ B 补主语能用 %d ｜ C 离开原视频看不懂（丢） %d ｜ 手撕代码 %d 道 ｜ 外部题库 %d 道"
          % (len(raw), counts["A"], counts["B"], counts["C"], len(handcode), len(repo)))
    keep = [q for q in raw if grade(q) in ("A", "B")] + repo
    dist = Counter(theme_of(prepare(q)) for q in keep)
    for t, n in dist.most_common():
        print("   %-14s %d 题" % (t, n))
    garbled = [q for q in keep if sanitize(" ".join(
        [str(q.get(k) or "") for k in ("text", "referenceAnswer", "explain", "deeper")]
        + [str(x) for x in (q.get("answer") or [])]))[1]]
    unresolved = [q for q in keep if q["_unresolved"]]
    print("发布前要人工过一遍：乱码词 %d 道、指代没名字 %d 道" % (len(garbled), len(unresolved)))
    if args.dry_run:
        return 0
    md = render(keep, counts, handcode)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(md, encoding="utf-8")
    print("已写出：%s（%d 题：自家 %d + 外部 %d + 手撕 %d，%d 字）"
          % (args.out, len(keep) + len(handcode), len(keep) - len(repo), len(repo), len(handcode), len(md)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
