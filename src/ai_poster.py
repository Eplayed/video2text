# -*- coding: utf-8 -*-
"""AI 海报出图链路：整理稿 → 整张海报由出图模型画出来（含中文文字）。

与 src/graphics 的 HTML 模板渲染是两条独立产线：这里不出网页、不排 CSS，
文字由出图模型直接画进像素。三段式，缺一不可：

  1) LLM 拆解：把整理稿压成「主标题 + 副标题 + N 张卡片 + 尾注」文案计划，
     并过字数硬门禁。总字数是实测出来的安全线（60 字全对、超 80 字开始出错），
     所以钉在代码里，不靠 prompt 祈祷。
  2) 出图：百炼原生 multimodal-generation 端点，prompt 逐字点名要渲染的文本。
     返回的是 OSS 临时链接，必须当场下载，否则过期拿不到图。
  3) OCR 回读：视觉模型转录图面文字，与文案计划逐字比对。
     没有这一步，「图面写错字」只能靠人眼发现——而这是整条链路唯一的质检位。

Python 3.9 兼容：不用 match / X|Y 语法。
"""
import base64
import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests

from .graphics import gate as draft_gate
from .graphics import package as pkg_store
from .graphics.channels.toutiao import _parse_json

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "poster"

IMAGE_MODEL = "qwen-image-max"      # 中文文字渲染主力；列表里还有 qwen-image-3.0
OCR_MODEL = "qwen3-vl-flash"        # 转录型视觉模型（qwen-vl-ocr 只回坐标框，不能用）

# 端点尺寸上限 1664x1664：传 1024*1792 会直接 InvalidParameter
SIZES = {"9:16": "936*1664", "3:4": "1248*1664", "1:1": "1664*1664"}
RATIO_LABEL = {"9:16": "竖版 9:16", "3:4": "竖版 3:4", "1:1": "方形 1:1"}

# 字数纪律（超限即拒绝出图，不是警告）
MAX_TOTAL_CHARS = 80
TITLE_MAX, SUBTITLE_MAX, FOOTER_MAX = 12, 10, 14
CARD_TITLE_MAX, CARD_DESC_MAX, CARD_MIN, CARD_MAX = 6, 16, 3, 5

# 发布文案（不进图、只给发布页用）的独立字数档：不受图面 80 字预算约束
COPY_MIN, COPY_MAX = 120, 260
DIGEST_MAX = 120
KEYWORD_MAX, SOURCE_MAX = 30, 50

# 合规尾注：图是 AI 画的、文字是 AI 写的，转述声明必须随配文一起发
COMPLIANCE_NOTE = "本文由 AI 辅助整理归纳，内容源自公开分享，观点与结论归原作者，如有侵权请联系删除。"

# 海报版发布检查清单：平台侧动作 AI 替不了，逐条人工过
CHECKLIST = [
    "图内文字已过 OCR 回读校验（manifest.verify.ok = true），未通过别发",
    "数字/版本/价格类信息人工核对一手来源（ASR 与模型都可能出错）",
    "「文章设置」勾选「内容由 AI 生成」（2025-09-01 起强制，平台不自动标注）",
    "图片消息无「声明原创」入口（平台未开放图片原创），勿找该按钮、勿群发后补标",
    "配文保留文末合规尾注，勿删；正文禁外链",
    "尺寸与平台匹配：9:16 小红书/抖音图文、3:4 公众号图片消息、1:1 通用封面",
    "发布后 72 小时回填数据，便于回看哪种风格有效",
]


class PosterError(Exception):
    """链路可预期失败（AI 未配置 / 字数越界 / 出图端点报错），消息可直接展示给用户。"""


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _api_root(api_base):
    """百炼兼容模式与原生服务端点同域不同路径，这里从 api_base 还原出根。"""
    b = (api_base or "").rstrip("/")
    for suffix in ("/compatible-mode/v1", "/compatible-mode"):
        if b.endswith(suffix):
            return b[:-len(suffix)]
    return b


def _require_ai(ai_config):
    if not ai_config or not ai_config.get("api_key") or ai_config.get("method") == "skip":
        raise PosterError("未配置 AI 接口，无法出图（工作台 AI 设置里填 API Key）")
    return ai_config["api_key"], (ai_config.get("api_base") or ""), (ai_config.get("model") or "")


# ── 1. LLM 拆解：整理稿 → 文案计划 ──
_PLAN_PROMPT = """你是自媒体图文编辑。把下面的整合稿压缩成一张信息长图海报的文案计划。

本篇主题：__THEME__

铁律（转载整理模式）：
- 只能用整合稿里出现过的事实与数字，严禁编造、严禁夸大；整合稿没写的不要写。
- 全程转述口吻，禁用第一人称（我实测 / 我用过）。
- 字数是硬约束，多一个字都不合格：主标题 ≤12 字；副标题 ≤10 字；
  每张卡片标题 ≤6 字、说明 ≤16 字；尾注 ≤14 字；卡片 3-5 张；全部文字合计 ≤80 字。
- 中文标点计入字数；不要出现英文、数字编号前缀、markdown 符号。

输出 JSON（键固定，值为字符串或对象数组）：
{
  "title": "主标题",
  "subtitle": "副标题",
  "cards": [{"t": "卡片标题", "d": "卡片说明"}],
  "footer": "合规转述声明，例：内容整理自公开分享",
  "style": "画面风格一句话，描述配色/材质/装饰，例：深色影院质感背景，金色雕花边框，卡片做成票根造型",
  "publish": {
    "copy_text": "发布页配文，120-260 字纯文本：钩子开头 1-2 句 + 要点 3-5 条（每条一行，用「· 」开头）+ 收尾引导 1 句。不要 markdown 符号。",
    "digest": "摘要，≤120 字，单独成段能读懂，不写「见图」这类字样",
    "keyword_reply": "关注后自动回复引导语，≤30 字（例：回复 清单 领取完整列表）；素材里没有可推的关键词就留空字符串",
    "source_note": "来源说明一句话，≤50 字，含数据口径截至{{DATE}}"
  }
}

publish 里的文字不会画进海报，是给发布页用的，所以不占上面 80 字的预算；但同样只能用整合稿里的事实。

整合稿：
__CONTENT__"""


def _clip(text, limit):
    return re.sub(r"\s+", "", str(text or ""))[:limit]


def _clip_ws(text, limit):
    """发布文案用的裁切：折叠空白但保留单空格——图面字段可以删空格，
    「回复 Agent 领取」这类关键词删了空格就废了。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _clip_publish(raw):
    """发布文案单独裁切：它不进图，所以不受图面 80 字预算约束。"""
    raw = raw if isinstance(raw, dict) else {}
    return {"copy_text": re.sub(r"[ \t]+", " ", str(raw.get("copy_text") or "")).strip()[:COPY_MAX],
            "digest": _clip_ws(raw.get("digest"), DIGEST_MAX),
            "keyword_reply": _clip_ws(raw.get("keyword_reply"), KEYWORD_MAX),
            "source_note": _clip_ws(raw.get("source_note"), SOURCE_MAX)}


def lint_publish(pub):
    """发布文案侧软校验：不阻断出图，只进 manifest.lint 提示人工补。"""
    bad = []
    n = len(pub.get("copy_text") or "")
    if n and (n < COPY_MIN or n > COPY_MAX):
        bad.append("配文 %d 字，建议 %d-%d 字" % (n, COPY_MIN, COPY_MAX))
    if len(pub.get("digest") or "") > DIGEST_MAX:
        bad.append("摘要超 %d 字" % DIGEST_MAX)
    if len(pub.get("keyword_reply") or "") > KEYWORD_MAX:
        bad.append("关键词回复超 %d 字" % KEYWORD_MAX)
    return bad


def build_text_plan(summary, ai_config, theme="", title=""):
    """调 LLM 产出文案计划 + 发布文案，并逐字段裁到字数线内。

    返回 (plan, publish, violations, notes)：plan 是要画进图里的文字（超预算先经 fit_plan
    自动收敛，violations 非空才拒绝出图），publish 是发布页用的配文/摘要/关键词回复/
    来源说明（只软校验），notes 记录自动压缩动了哪几处。
    """
    api_key, api_base, model = _require_ai(ai_config)
    import openai

    client = openai.OpenAI(api_key=api_key, base_url=api_base or "https://api.openai.com/v1",
                           timeout=300, max_retries=1)
    prompt = (_PLAN_PROMPT
              .replace("__THEME__", (theme or "未指定，按素材自判").strip())
              .replace("{{DATE}}", datetime.now().strftime("%m月%d日"))
              .replace("__CONTENT__", (summary.get("content") or "")[:8000]))
    resp = client.chat.completions.create(
        model=model or "qwen-plus",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3, response_format={"type": "json_object"})
    data = _parse_json(resp.choices[0].message.content)

    cards = []
    for c in (data.get("cards") or [])[:CARD_MAX]:
        if not isinstance(c, dict):
            continue
        t, d = _clip(c.get("t"), CARD_TITLE_MAX), _clip(c.get("d"), CARD_DESC_MAX)
        if t or d:
            cards.append({"t": t, "d": d})
    plan = {"title": _clip(title or data.get("title") or summary.get("title"), TITLE_MAX),
            "subtitle": _clip(data.get("subtitle"), SUBTITLE_MAX),
            "cards": cards,
            "footer": _clip(data.get("footer") or "内容整理自公开分享", FOOTER_MAX),
            "style": str(data.get("style") or "深色质感背景，金色细边框，信息卡片纵向排列").strip()[:120]}
    plan, notes = fit_plan(plan)
    return plan, _clip_publish(data.get("publish")), lint_plan(plan), notes


def plan_chars(plan):
    """海报上要渲染的总字数（不含风格描述——那部分模型不落成文字）。"""
    n = len(plan.get("title") or "") + len(plan.get("subtitle") or "") + len(plan.get("footer") or "")
    for c in plan.get("cards") or []:
        n += len(c.get("t") or "") + len(c.get("d") or "")
    return n


def lint_plan(plan):
    """字数与结构门禁：越界就返回问题清单，调用方据此拒绝出图。"""
    bad = []
    if not (plan.get("title") or "").strip():
        bad.append("主标题为空")
    cards = plan.get("cards") or []
    if len(cards) < CARD_MIN:
        bad.append("卡片只有 %d 张，少于 %d 张" % (len(cards), CARD_MIN))
    if len(cards) > CARD_MAX:
        bad.append("卡片 %d 张，超过 %d 张" % (len(cards), CARD_MAX))
    for i, c in enumerate(cards, 1):
        if len(c.get("d") or "") > CARD_DESC_MAX:
            bad.append("第 %d 张卡片说明超 %d 字" % (i, CARD_DESC_MAX))
    total = plan_chars(plan)
    if total > MAX_TOTAL_CHARS:
        bad.append("合计 %d 字，超过安全线 %d 字（字数越多错字率越高，请删条目而不是缩字号）"
                   % (total, MAX_TOTAL_CHARS))
    return bad


def fit_plan(plan, limit=MAX_TOTAL_CHARS):
    """超预算时按价值高低确定性收敛，返回 (plan, notes)。

    模型实测经常多写十几个字（90 字 vs 80 线），直接判死等于让用户白等一次
    3 分钟的拆解。压缩顺序＝先砍末位卡片 → 再截最长说明 → 再削副标题与尾注，
    主标题与卡片标题最后动；全程不再调模型。收敛不了才交回 violations 报错。
    """
    notes = []
    start = plan_chars(plan)
    # 第 0 步：先把每个字段各自裁到单条上限，让本函数不依赖调用方已做过裁剪
    plan["title"] = _clip(plan.get("title"), TITLE_MAX)
    plan["subtitle"] = _clip(plan.get("subtitle"), SUBTITLE_MAX)
    plan["footer"] = _clip(plan.get("footer"), FOOTER_MAX)
    for c in plan.get("cards") or []:
        c["t"], c["d"] = _clip(c.get("t"), CARD_TITLE_MAX), _clip(c.get("d"), CARD_DESC_MAX)
    if start <= limit:
        return plan, notes
    while plan_chars(plan) > limit and len(plan["cards"]) > CARD_MIN:
        dropped = plan["cards"].pop()
        notes.append("删掉末位卡片「%s」以压字数" % (dropped.get("t") or dropped.get("d")))
    for _ in range(24):
        over = plan_chars(plan) - limit
        if over <= 0:
            break
        cands = [c for c in plan["cards"] if len(c.get("d") or "") > 6]
        if not cands:
            break
        c = max(cands, key=lambda x: len(x["d"]))
        c["d"] = c["d"][:max(6, len(c["d"]) - over)]
        notes.append("截短卡片「%s」的说明" % c.get("t"))
    for key, label in (("subtitle", "副标题"), ("footer", "尾注")):
        over = plan_chars(plan) - limit
        if over > 0 and len(plan.get(key) or "") > 2:
            plan[key] = (plan.get(key) or "")[:max(2, len(plan[key]) - over)]
            notes.append("截短%s" % label)
    left = plan_chars(plan) - limit
    if left > 0:
        notes.append("仍超 %d 字，需人工删条目" % left)
    elif notes:
        notes.append("已从 %d 字压到 %d 字，发布前请复核文案是否还完整" % (start, plan_chars(plan)))
    return plan, notes


def build_prompt_text(plan, ratio="9:16"):
    """组装出图 prompt：要渲染的文字逐条点名 + 明令不得增删，模型才不会自己编文案。"""
    lines = ["一张%s中文信息长图海报，%s。" % (RATIO_LABEL.get(ratio, "竖版 9:16"), plan["style"]),
             "海报必须逐字渲染以下简体中文，不得增删改任何字符，不要出现其它文字：",
             "主标题：%s" % plan["title"]]
    if plan.get("subtitle"):
        lines.append("副标题：%s" % plan["subtitle"])
    for i, c in enumerate(plan.get("cards") or [], 1):
        lines.append("卡片%d标题：%s 说明：%s" % (i, c.get("t") or "", c.get("d") or ""))
    if plan.get("footer"):
        lines.append("尾注小灰字：%s" % plan["footer"])
    return "\n".join(lines)


# ── 2. 出图 ──
# 官方口径（阿里云百炼 qwen-image-max）：0.5 元/张、限流 RPM=2。
# 实测连发 5 张时第 5 张必撞 "Requests rate limit exceeded"，所以这里既退避也节流。
PRICE_PER_IMAGE_YUAN = 0.5
RATE_LIMIT_WAIT_SEC = 31      # RPM=2 → 相邻两次请求至少隔 30s，留 1s 余量
MAX_ATTEMPTS = 3
_last_call_at = [0.0]


def _throttle():
    """把相邻出图请求拉开到 RPM=2 允许的间隔，批量跑时不需要调用方自己 sleep。"""
    wait = RATE_LIMIT_WAIT_SEC - (time.time() - _last_call_at[0])
    if wait > 0:
        time.sleep(wait)
    _last_call_at[0] = time.time()


def _rate_limited(status_code, text):
    return status_code == 429 or "rate limit" in text.lower() or "throttl" in text.lower()


def generate_image(prompt_text, size, ai_config, timeout=300):
    """调百炼原生 multimodal-generation 出图，返回 (png 字节, usage)。

    注意三件实测事实：/images/generations（OpenAI 形状）对这些模型是 404；异步
    text2image 会被拒「current user api does not support asynchronous calls」；
    模型限流 RPM=2，撞限流要退避重试而不是直接失败。返回的 OSS 链接会过期，当场下载。
    """
    api_key, api_base, _ = _require_ai(ai_config)
    url = _api_root(api_base) + "/api/v1/services/aigc/multimodal-generation/generation"
    payload = {"model": IMAGE_MODEL,
               "input": {"messages": [{"role": "user", "content": [{"text": prompt_text}]}]},
               "parameters": {"size": size}}
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
               "X-DashScope-Async": "disable"}
    last = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _throttle()
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        try:
            data = r.json()
        except ValueError:
            raise PosterError("出图接口返回非 JSON（HTTP %s）：%s" % (r.status_code, r.text[:200]))
        if "output" in data:
            content = ((data.get("output") or {}).get("choices") or [{}])[0] \
                .get("message", {}).get("content") or []
            img_url = ""
            for item in content:
                if isinstance(item, dict) and item.get("image"):
                    img_url = item["image"]
                    break
            if not img_url:
                raise PosterError("出图接口没返回图片地址：%s" % str(data)[:200])
            png = requests.get(img_url, timeout=180)   # OSS 链接会过期，当场下载
            png.raise_for_status()
            return png.content, (data.get("usage") or {})
        last = str(data.get("message") or data.get("code") or
                   ("HTTP %s %s" % (r.status_code, str(data)[:200])))
        if _rate_limited(r.status_code, last) and attempt < MAX_ATTEMPTS:
            time.sleep(RATE_LIMIT_WAIT_SEC)
            continue
        raise PosterError("出图失败：%s" % last[:300])
    raise PosterError("出图失败（重试 %d 次仍被限流）：%s" % (MAX_ATTEMPTS, last[:200]))


# ── 3. OCR 回读校验 ──
_PUNCT_RE = re.compile(r"[\s，。、！？：；「」『』“”\"'（）()《》·—…\-]+")


def _norm(text):
    return _PUNCT_RE.sub("", str(text or ""))


def verify_image(png_bytes, plan, ai_config, timeout=120):
    """转录图面文字并与文案计划比对，返回 {ok, missing, extra, transcript}。

    比对前先剥空白与标点：模型常把主标题排成两行，逐行比对会误报缺失。
    """
    api_key, api_base, _ = _require_ai(ai_config)
    import openai

    client = openai.OpenAI(api_key=api_key, base_url=api_base or "https://api.openai.com/v1",
                           timeout=timeout, max_retries=1)
    b64 = base64.b64encode(png_bytes).decode()
    resp = client.chat.completions.create(
        model=OCR_MODEL,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
            {"type": "text", "text": "逐字转录图中所有简体中文文字，按从上到下顺序每行一条，只输出文字本身"}]}],
        max_tokens=800)
    transcript = resp.choices[0].message.content or ""
    blob = _norm(transcript)
    expected = [plan.get("title"), plan.get("subtitle"), plan.get("footer")]
    for c in plan.get("cards") or []:
        expected.extend([c.get("t"), c.get("d")])
    missing, rest = [], blob
    for e in expected:
        ne = _norm(e)
        if not ne:
            continue
        if ne not in blob:
            missing.append(str(e).strip())
        rest = rest.replace(ne, "", 1)
    return {"ok": not missing, "missing": missing, "extra": rest[:80],
            "transcript": transcript.strip()}


# ── 编排 ──
def generate_poster(summary, ai_config, theme="", title="", ratio="9:16",
                    progress_cb=None, do_verify=True, plan=None, publish=None, force=False):
    """整理稿 → 海报一张。返回 manifest dict（已落盘 output/poster/<summary_id>/）。

    plan / publish 非空时跳过 LLM 拆解（前端审改过文案再出图走这条）：plan 是要画进图的
    文字仍过字数硬门禁，publish 是发布页文案只做软校验。两者都不落模型自由发挥——
    模型只负责画字与排版，写什么由拆解那一步定、由人改。

    force=True 放行 status=draft 的骨架稿。海报线与 HTML 卡片线不同：这里永远有一次 LLM
    提炼在中间，骨架稿只要底层转写有料就能出好文案（实测 61 号那篇超时稿，价格与时间线
    数字全对），所以门禁做成「默认拦、人工复核后可显式放行」，而不是一刀切。
    """
    _require_ai(ai_config)
    if ratio not in SIZES:
        ratio = "9:16"
    # 判据与头条/公众号共用 gate；海报线允许人工复核后 force 放行
    blockers = draft_gate.draft_blockers(summary)
    overrode = bool(force and blockers)
    if blockers and not force:
        raise PosterError("整合稿不合格：" + "；".join(blockers)
                          + "。若你已复核过拆解出的文案，可点「仍然出图」放行。")
    summary_id = int(summary["id"])
    out_dir = OUTPUT_DIR / str(summary_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _pg(msg):
        if progress_cb:
            progress_cb(msg)

    started = datetime.now()
    if plan is None:
        _pg("LLM 拆解海报文案...")
        plan, llm_publish, bad, notes = build_text_plan(summary, ai_config, theme=theme, title=title)
    else:
        # 手改过的文案不做自动压缩：那是用户显式输入，超线就明确报错让他自己删
        llm_publish, bad, notes = None, lint_plan(plan), []
    if bad:
        raise PosterError("文案不合格：" + "；".join(bad))
    pub = _clip_publish(publish or llm_publish or {})
    chars = plan_chars(plan)

    prompt_text = build_prompt_text(plan, ratio)
    _pg("出图模型绘制海报（%s，约 20-60 秒）..." % SIZES[ratio])
    png, usage = generate_image(prompt_text, SIZES[ratio], ai_config)
    (out_dir / "poster.png").write_bytes(png)
    (out_dir / "prompt.txt").write_text(prompt_text + "\n", encoding="utf-8")

    check = {"skipped": True, "ok": None, "missing": [], "extra": "", "transcript": ""}
    if do_verify:
        _pg("回读校验图面文字...")
        try:
            check = verify_image(png, plan, ai_config)
        except Exception as e:
            check = {"skipped": True, "ok": None, "error": str(e)[:200],
                     "missing": [], "extra": "", "transcript": ""}

    copy_text = pub.get("copy_text") or ""
    if copy_text and COMPLIANCE_NOTE[:10] not in copy_text:
        copy_text = copy_text.rstrip() + "\n\n" + COMPLIANCE_NOTE

    issues = lint_publish(pub)
    if not pub.get("copy_text"):
        issues.append("warn: 配文为空，发布页需手写")
    if not pub.get("digest"):
        issues.append("warn: 摘要为空（公众号发布页要 ≤%d 字）" % DIGEST_MAX)
    if check.get("ok") is False:
        issues.insert(0, "error: 图面文字回读未通过，缺：" + "、".join(check.get("missing") or []))
    elif check.get("ok") is None:
        issues.append("warn: 本次未做回读校验，发布前必须人眼核对图面文字")
    if overrode:
        issues.insert(0, "warn: 整合稿是规则版骨架，本次由人工显式放行出图，发布前务必核对数字与时间线")

    manifest = {
        "id": summary_id,
        "summary_id": summary_id,
        "channel": "poster",
        "title": plan.get("title") or "",
        "plan": plan,
        "publish": pub,
        "copy_text": copy_text,
        "digest": pub.get("digest") or "",
        "keyword_reply": pub.get("keyword_reply") or "",
        "source_note": pub.get("source_note") or "",
        "prompt_text": prompt_text,
        "ratio": ratio,
        "size": SIZES[ratio],
        "text_chars": chars,
        "image": "poster.png",
        "url": "/media/poster/%d/poster.png" % summary_id,
        "image_model": IMAGE_MODEL,
        "ocr_model": OCR_MODEL,
        "usage": usage,
        "cost_yuan_estimate": PRICE_PER_IMAGE_YUAN,
        "auto_notes": notes,
        "draft_override": overrode,
        "verify": check,
        "source_title": summary.get("title") or "",
        "theme": (theme or "").strip(),
        "checklist": list(CHECKLIST),
        "created_at": _now(),
        "elapsed_sec": int((datetime.now() - started).total_seconds()),
    }
    manifest["lint"] = issues
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    errs = [i for i in issues if i.startswith("error")]
    _pg("✅ 海报已生成" + ("" if errs else "（发布文案与清单已就绪）"))
    return manifest


def list_packages():
    return pkg_store.list_packages(OUTPUT_DIR)


def get_package(summary_id):
    return pkg_store.get_package(summary_id, OUTPUT_DIR)


def delete_package(summary_id):
    return pkg_store.delete_package(summary_id, OUTPUT_DIR)
