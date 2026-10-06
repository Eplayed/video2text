#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_toolkit_pack.py — 生成《自媒体 AI 写稿工具包 v1》（公众号关注回复的引流品）

产品阶梯（2026-10-05 拍板）：
  免费 · 关注回复【工具包】→ 本包：5 条提示词 + 2 个 skill + 使用说明（目的：涨粉+信任）
  99  · AI 面试答案库 95 题（product/AI面试答案库-v0.2.md）
  199 · 答案库 + 完整工作流（Dify yml + 产线脚本）+ 一次配置辅导（卖给同行）

为什么必须用脚本生成：包里的提示词和体检规则，权威源在本仓库代码里
（content_store._prompt / draft_audit / topic_pack / 头条出稿规范.md）。
手抄一份进产品目录，三个月后两边口径一定漂移——这个项目已经为漂移付过三次学费。
所以这里只做「抽取 + 加使用说明」，规则本体永远从源里读。

用法：
  python3 scripts/build_toolkit_pack.py            # 生成 product/工具包-v1/ 并打 zip
  python3 scripts/build_toolkit_pack.py --no-zip   # 只生成目录，先看内容
"""
import argparse
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "product" / "工具包-v1"
RULES_MD = Path.home() / "Documents" / "自媒体" / "头条出稿规范.md"
V7_MD = Path.home() / "Documents" / "自媒体" / "ARPG去AI味写作规则-v7.md"

sys.path.insert(0, str(ROOT))
from src import content_store, draft_audit, topic_pack  # noqa: E402

GEN_NOTE = "> 本文件由 `scripts/build_toolkit_pack.py` 从权威源抽取生成（%s）。" \
           "要改规则改权威源，别直接改这份副本。\n\n"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def slice_between(text: str, begin: str, end: str) -> str:
    i = text.find(begin)
    if i < 0:
        return ""
    j = text.find(end, i)
    return text[i + len(begin):j if j > 0 else len(text)].strip("\n")


def prompt_of(summary_type: str) -> str:
    """直接调用产线在用的提示词构造函数——包里的提示词和线上跑的是同一份。"""
    video = {"title": "（示例）某游戏版本更新解读", "author": "（示例）某up主",
             "game": "（示例）魔兽世界", "tags": "", "transcript": "（把你的口播/素材正文贴在这里）"}
    return content_store._prompt(video, summary_type)


def build_prompts() -> dict:
    files = {}

    rules = slice_between(read(RULES_MD), "<!-- DIFY_RULES_BEGIN -->", "<!-- DIFY_RULES_END -->")
    files["prompts/01-头条主笔出稿规范.md"] = GEN_NOTE % "头条出稿规范.md 注入区" + (
        "# 头条主笔出稿规范（可直接整段粘给你的 AI）\n\n"
        "用法：新建对话，把下面整块贴进系统提示或第一条消息，再附上你的素材正文。\n"
        "它管的是「写什么、不写什么」：第一人称怎么用、数字怎么来、标题多长、结尾停在哪。\n\n"
        "```\n" + rules + "\n```\n" if rules else
        "# 头条主笔出稿规范\n\n（未找到权威源文件，请检查路径）\n")

    files["prompts/02-头条整合稿提示词.md"] = GEN_NOTE % "content_store._prompt(toutiao_mix)" + (
        "# 头条整合稿提示词（把一条口播压成决策化稿件）\n\n"
        "用法：把提示词整段发给 AI，末尾的「口播 ASR」替换成你的素材正文。\n"
        "产出的【原话摘录】是给人挑词的原料，【风险核查】是发布前必看的清单。\n\n```\n"
        + prompt_of("toutiao_mix") + "\n```\n")

    files["prompts/03-公众号素材档案提示词.md"] = GEN_NOTE % "content_store._prompt(wechat_material)" + (
        "# 公众号素材档案提示词（先存档、后写稿，别一步到位）\n\n"
        "用法：同上。这份档案不是成品文章，是给下一步写稿引擎吃的结构化素材；\n"
        "「先压档案再写稿」比「一步写成文章」更不容易跑偏，也方便多处复用。\n\n```\n"
        + prompt_of("wechat_material") + "\n```\n")

    v7 = read(V7_MD)
    core = slice_between(v7, "## 最重要的原则", "## 标题规则")
    titles = slice_between(v7, "## 标题规则", "## 开头规则")
    opens = slice_between(v7, "## 开头规则", "## “先说结论”必须是真结论")
    files["prompts/04-去AI味写作规则.md"] = GEN_NOTE % "ARPG去AI味写作规则-v7" + (
        "# 去 AI 味写作规则（节选：原则 / 标题 / 开头）\n\n"
        "用法：写完初稿后，把这三节贴给 AI 并要求「逐条对照修改，只改命中的句子」。\n"
        "最重要的一条在最前面：**不要装得比实际更懂**。没有亲历就写整理文，别编。\n\n"
        + core + "\n\n" + titles + "\n\n" + opens + "\n")

    leak = "\n".join("- 出现「%s」即不合格（%s）" % (n, p) for p, n in draft_audit.LEAK_PATTERNS)
    edit = "\n".join("- 正则 `%s`" % p for p in draft_audit.EDITORIAL_VOICES)
    files["prompts/05-发布前自检清单.md"] = GEN_NOTE % "src/draft_audit.py" + (
        "# 发布前自检清单（机器可查的部分，人再补三项）\n\n"
        "用法：发之前逐条过。机器查不到的三项是：有没有一个带具体动作的「我」、\n"
        "正文数字能不能在素材里找到出处、游戏机制本身对不对。\n\n"
        "## 一、中间产物泄漏（命中即不能发）\n" + leak +
        "\n\n## 二、编辑向交代（命中要改）\n" + edit +
        "\n\n## 三、叙述者\n- 正文里必须有一个带具体动作的「我」（我把/我核/我算/我翻…）；"
        "没有就只剩通稿腔。\n\n## 四、AI 味评分\n- 阈值与 ai_score.py 对齐：≥%g 判过重，%g–%g 需人工看。\n"
        % (draft_audit.AI_FAIL, draft_audit.AI_WARN, draft_audit.AI_FAIL))

    files["prompts/06-多源选题聚合口径.md"] = GEN_NOTE % "src/topic_pack.py" + (
        "# 多源选题聚合口径（怎么从一堆素材里找出「独家增量」）\n\n"
        + (topic_pack.__doc__ or "").strip() +
        "\n\n## 关键参数\n- 时间窗：%d 小时\n- 通用词剔除占比：%s\n- 合并重叠阈值：%s\n"
        % (topic_pack.WINDOW_HOURS, topic_pack.STOP_RATIO, topic_pack.MERGE_RATIO)
        + "\n## 一句话用法\n同一个话题被 2 个以上作者在 48 小时内讲过、说法不一致——"
          "那个不一致就是别人抄不到的选题。先对齐口径再动笔，别各写各的。\n")
    return files


SKILL_AUDIT = """---
name: article-audit
description: 发文前体检。用户说"审一下这篇""能不能发""像不像AI写的"时使用。输出三选一结论（可以发/改完再发/不能发）+ 逐条必改清单。
---

# 发布前体检

## 用法

```bash
python3 {HERE}/audit.py <稿子.md>
python3 {HERE}/audit.py --manifest <图文包目录>     # 卡片+文案那种产物
```

退出码 0=可以发 1=改完再发 2=不能发。

## 它查什么、不查什么

查：中间产物泄漏（图N：画面说明、配图任务、内部编号）、编辑向交代、插图占位残留、
有没有带具体动作的「我」、AI 味评分（若本机装了 ai_score.py）。
不查：事实对错、数字准不准、游戏机制——那些要人核，别把机器结论当终审。

## 输出纪律

必须给三选一结论 + 逐条「必须改」。只报分数不给结论等于没审。
"""

SKILL_HUMAN = """---
name: article-humanize
description: 把 AI 稿改得像人写的。用户说"去AI味""不像人写的""定点改写"时使用。只改命中的句子，禁止全篇重写。
---

# 去 AI 味改写

## 五条硬纪律（违反任何一条即算做错）

1. **只改被点名的句子。** 先跑体检拿必改清单，逐条定点改；改完全篇字数变化超过 ±15% 说明你在重写，停下。
2. **人味靠"补具体"，不靠"换词"。** 每处改写必须补进一个来自素材的具体颗粒（坐标、NPC 名、小时数、翻车那一步）。
   把「位置非常隐蔽」改成「藏在洞顶那块突出石头上」是合格；改成「特别难找」是无效改写。
3. **素材里没有的事实一律不许编。** 缺亲历就换文章身份：写成「资料整理文」，
   明写「这篇按 up 主实测和公开公告整理，细节以上线后游戏内为准」。
4. **原话只借词不借句。** 素材里的玩家黑话（≤6 字）可以当用词，整句照抄禁止（跨平台搬运风险）。
5. **署名要具体。** 转述他人实测写「up主『作者名』实测」，不许用「有玩家表示」这种空主语。

## 工作流

体检 → 按清单定点改 → 再体检，前后对照：FAIL 清零、评分下降、字数没大动，三条同时满足才算改完。
"""


def build_skills() -> dict:
    here = "skills/article-audit"
    files = {
        "skills/article-audit/SKILL.md": SKILL_AUDIT.replace("{HERE}", here),
        "skills/article-audit/draft_audit.py": read(ROOT / "src" / "draft_audit.py"),
        "skills/article-audit/audit.py": CLI_SHIM,
        "skills/article-humanize/SKILL.md": SKILL_HUMAN,
    }
    return files


CLI_SHIM = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具包自带的体检命令行（规则在同目录 draft_audit.py，那份是唯一权威）。

用法：
  python3 audit.py <稿子.md>
  python3 audit.py --manifest <图文包目录或 manifest.json>
退出码：0=可以发 1=改完再发 2=不能发
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import draft_audit  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("draft", nargs="?")
    ap.add_argument("--manifest")
    args = ap.parse_args()
    if not args.draft and not args.manifest:
        ap.error("必须给 .md 路径或 --manifest")
    if args.manifest:
        result = draft_audit.audit(manifest=draft_audit.load_manifest(args.manifest))
        target = args.manifest
    else:
        with open(args.draft, encoding="utf-8") as fh:
            md = fh.read()
        result = draft_audit.audit(md_text=md, draft_path=args.draft)
        target = args.draft
    print("\\n━━━━━━ 发布前体检 · %s ━━━━━━" % target)
    for level, name, detail in result["rows"]:
        print("  [%-4s] %-22s %s" % (level, name, detail))
    tail = ("不能发（%d 项必须改）" % result["fails"] if result["verdict"] == "fail"
            else "改完再发（%d 项建议）" % result["warns"] if result["verdict"] == "warn"
            else "可以发")
    print("━━━━━━ 判定：%s ━━━━━━" % tail)
    for level, name, detail in result["rows"]:
        if level == "FAIL":
            print("  必须改 → %s：%s" % (name, detail))
    return {"pass": 0, "warn": 1, "fail": 2}[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
'''


README = """# 自媒体 AI 写稿工具包 v1（免费引流版）

领到这份包，说明你关注了公众号并回复了关键字。三件事先说清楚：

1. **这不是"万能提示词合集"。** 里面每条提示词都来自一条真在跑的产线：
   一个游戏自媒体号用它日更图文，一个技术号用它写面试资料。规则都是被数据改出来的，
   不是拍脑袋写的。
2. **它解决的是"AI 写的东西没人味、还容易编"这两个具体问题**，不解决"怎么涨粉"。
3. 想要完整工作流（自动采集、出图、体检门禁那一整套）和 95 题面试答案库，
   看包内《升级与定价.md》。

## 包里有什么

| 文件 | 干什么用 | 什么时候用 |
|---|---|---|
| prompts/01 头条主笔出稿规范 | 管"写什么不写什么"：第一人称、数字、标题、结尾 | 每次开写前贴给 AI |
| prompts/02 头条整合稿 | 把一条口播压成决策化稿件 | 有素材要成稿时 |
| prompts/03 公众号素材档案 | 先存档后写稿，别一步到位 | 公众号线 |
| prompts/04 去AI味写作规则 | 原则/标题/开头三节 | 初稿写完后 |
| prompts/05 发布前自检清单 | 机器可查的泄漏项+人查的三项 | 发布前 |
| prompts/06 多源选题聚合 | 从一堆素材里找出独家增量 | 选题时 |
| skills/article-audit | 体检脚本（Qoder/Claude 技能包） | 每篇发布前 |
| skills/article-humanize | 定点改写纪律 | 体检不过时 |

## 三步上手

1. 把 `skills/` 两个目录拷进你项目的 `.qoder/skills/`（用 Qoder 的话），或直接看 SKILL.md 里的纪律手工执行。
2. 跑一次体检感受下：`python3 skills/article-audit/audit.py 你的一篇旧稿.md`。
   大概率会报「叙述者缺席」——那就是你稿子"一眼 AI"的原因。
3. 按 prompts/04 + 05 改一遍，再跑体检，对比前后。

## 常见问题

- **体检脚本要联网/要 API key 吗？** 不要。它只做正则与结构检查；AI 味评分那一项
  需要本机有 ai_score.py，没有就自动跳过，不影响结论。
- **我是写小说/写公文的，能用吗？** 04、05 两节通用；01–03 是自媒体口径，按需取用。
"""

UPSELL = """# 升级与定价

| 档 | 内容 | 价格 |
|---|---|---|
| 免费（本包） | 5+1 条提示词、2 个 skill、使用说明 | 0 |
| 进阶 | AI 面试答案库：95 题 / 13 主题 / 19 道代码真跑过，含卖货页同款目录 | __PRICE_BANK__ |
| 完整 | 答案库 + 整套工作流（采集→整理→出图→体检→门禁）+ 一次 1 小时配置辅导 | __PRICE_FULL__ |

怎么买：公众号后台回复「升级」，或邮件 __CONTACT__。
说明：完整档含 Dify 工作流导出文件与产线脚本，适合有技术背景、想自己搭产线的人；
只想要内容的买进阶档就够。
"""

REPLY_COPY = """# 自动回复与引导文案（直接粘到公众号后台）

## 被关注自动回复

终于来了。回复【工具包】领《自媒体 AI 写稿工具包》：6 条在跑产线里真用着的提示词
+ 2 个质检技能包，专治"AI 写的没人味"和"AI 偷偷编数字"。
回复【答案库】看 95 道 AI 面试题整理（19 道代码真跑过）。

## 关键词回复

关键字：工具包
回复内容：
工具包在这：__LINK__
三件事先说：1) 每条提示词都来自真在跑的产线，不是万能咒语；2) 先跑一次体检脚本，
看你旧稿为什么"一眼 AI"；3) 想要完整工作流和答案库，回复【升级】。

关键字：答案库
回复内容：
《AI 面试答案库》95 题 / 13 主题 / 19 道手撕代码真跑过：__LINK_BANK__

关键字：升级
回复内容：
进阶档（答案库）__PRICE_BANK__；完整档（答案库+整套工作流+1小时配置辅导）__PRICE_FULL__。
转账后把截图发我，网盘链接 10 分钟内发你（人工发货，别急）。

## 文章/图文末尾引导句（公众号侧专用，头条不导流）

- 长文结尾：「这套提示词我整理成了工具包，关注公众号回复【工具包】免费领。」
- 图文卡片最后一张的收尾行：「想要同款提示词？关注后回复【工具包】。」
- 注意：只放公众号自己的内容里。头条号规范写明不导流、不带货，别把这句带过去。
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--price-bank", default="99 元")
    ap.add_argument("--price-full", default="199 元")
    ap.add_argument("--link", default="（填网盘链接）")
    ap.add_argument("--link-bank", default="（填网盘链接）")
    ap.add_argument("--contact", default="（填邮箱）")
    args = ap.parse_args()

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    files = build_prompts()
    files.update(build_skills())
    files["README-先看这个.md"] = README
    files["升级与定价.md"] = (UPSELL.replace("__PRICE_BANK__", args.price_bank)
                            .replace("__PRICE_FULL__", args.price_full)
                            .replace("__CONTACT__", args.contact))
    files["自动回复与引导文案.md"] = (REPLY_COPY.replace("__LINK__", args.link)
                                  .replace("__LINK_BANK__", args.link_bank)
                                  .replace("__PRICE_BANK__", args.price_bank)
                                  .replace("__PRICE_FULL__", args.price_full))
    files["版本说明.md"] = ("# 版本说明\n\n- v1 · %s · 首版：6 提示词 + 2 skill + 说明 + 回复文案\n"
                        "- 规则权威源：video2text 仓库 src/draft_audit.py、src/topic_pack.py、"
                        "src/content_store.py；~/Documents/自媒体/头条出稿规范.md、ARPG去AI味写作规则-v7.md\n"
                        % datetime.now().strftime("%Y-%m-%d"))

    for rel, text in files.items():
        target = OUT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print("生成 %d 个文件 → %s" % (len(files), OUT))

    if not args.no_zip:
        zpath = ROOT / "product" / "自媒体AI写稿工具包-v1.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(OUT.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(ROOT / "product").as_posix())
        print("已打包：%s（%.1f KB）" % (zpath, zpath.stat().st_size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
