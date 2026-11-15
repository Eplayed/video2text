# -*- coding: utf-8 -*-
"""「答案库与交付」看板取数：把散在 product/ 里的付费库、免费池、交付物、许可判定
汇成一份给工作台用的结构，让人一眼看到"现在能发出去的是什么、干不干净、什么能动"。

为什么要有这个页面（2026-10-09）：答案库这条线的产物已经散成 6 类文件
（.md / 题库 JSON / 三份 PDF / 卖货页 / 知识地图 / 工具包 zip / 免费池 JSON），
而 product/ 整目录被 gitignore——仓库搜索看不到、也没有 git 历史，
只能靠这个页面把"当前实际存在的文件 + 当场跑出来的自检结论"摊开。

三条口径写死在这里，改的时候连产品一起改：
  ① 数字全部现读现算，不缓存也不手抄：题数读 JSON、页数读 PDF 字节、自检跑 audit_gate；
  ② 文件不存在就明说"未生成"，绝不显示 0 或旧数——这页的作用就是发现"忘了重跑"；
  ③ 数据源分级表是决策记录，不是爬取结果：新源要先核清楚"能送到哪一档、允许取什么"再进表，
     没进表的源等于没判过，不许搬。

2026-10-19 换轨：这页原来整页都是 AI 面试答案库线（题库 JSON / 免费池 / PDF / 工具包），
号主拍板 AI 线不做了，改做魔兽资料工具教程。本次只换了 ③ 那张表（SOURCE_LEDGER），
上面的 paid / free / artifacts 三节仍是 AI 线的历史现状，**冻结不删**——它们现在的作用是
"证明那条线停在哪儿"，等号主正式封档再整节清掉。
"""
import json
import os
import re
import sys
import subprocess
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCT = os.path.join(ROOT, "product")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# 魔兽资料站的数据源分级表（判过才记，没记 = 没判过 = 不许搬）
#
# tier 用 wow-forever 仓库自己的口径，不另造术语（`wow-forever/AGENTS.md:54`）：
#   L0 官方已核 / L1 仅官方英文（或仅一家资料站列过）/ L2 待实测 / L3 缺数据。
#   记的是"这个源最高能把字段送到哪一档"，不是它的整体质量分——所以会出现
#   "L0名 · L1值"这种一格两档：客户端解包出来的游戏内中文名（datamine_cn）按
#   AGENTS.md:55 的 2026-10-08 决策直接算 L0，但物品属性与卖价官方没有中文页，
#   天生只能走转述档、到不了 L0 以上（`DATA-SOURCES-MAP-2026-10-16.md` §二末）。
# verdict 记的是"我们现在拿它干什么"，取值见 VERDICT_LABEL。
# checked 记的是那条结论被实测的日期，逐条给，不给全表一个日期——
#   这批源的抓取日分散在 10-11 到 10-18，跨了三个 build，写一个日期等于骗人。
# 事实来源只有两处，改这张表先回去读：
#   `wow-forever-web/docs/DATA-SOURCES-MAP-2026-10-16.md`（下称 MAP）
#   `wow-forever/AGENTS.md`（红线与分级口径）
SOURCE_LEDGER = [
    # ── L0：官方一手源（MAP §二）───────────────────────────────────
    {"source": "wow.blizzard.cn/news/ 国服公告与职业深度解析",
     "tier": "L0", "verdict": "used", "checked": "2026-10-16",
     "note": "职业/天赋/技能的官方中文名、等级区间、机制说明 → abilities.json、races.json、"
             "glossary.json、talents/*.json 的 L0 部分"},
    {"source": "wow.blizzard.cn/24266320/ 在线修正",
     "tier": "L0", "verdict": "lead", "checked": "2026-10-16",
     "note": "逐条修正记录要整页比对才敢引用，只留 docs/ 的比对记录，**未整表入库**"},
    {"source": "暴雪官方图标 CDN（tools/fetch-icons.py 只允许这个域名）",
     "tier": "L0", "verdict": "used", "checked": "2026-10-16",
     "note": "图标字节本体 → src/img/icons/，manifest.json 记来源与抓取日期；"
             "第三方站只给文件名不给字节。已知 83 个图标官方服务器 403，退回自绘块"},
    {"source": "news.blizzard.com / worldofwarcraft.blizzard.com/en-us/forever",
     "tier": "L1", "verdict": "used", "checked": "2026-10-16",
     "note": "英文原名与上线时间 → meta.json 的 launch、changes.json 英文名。"
             "**只有英文所以是 L1 不是 L0**，中文名要另找官方中文页或 datamine_cn"},

    # ── 转述源 A：wclbox（MAP §三）────────────────────────────────
    {"source": "wowforever.wclbox.com · items-detail 物品详情全量",
     "tier": "L0名 · L1值", "verdict": "used", "checked": "2026-10-16",
     "note": "build 1.60.1.70374，21,077 页逐页抓、**0 失败** → items-detail.json 20,528 件。"
             "接口边界决定成本：/_shuju/<发布号>/ 只有 meta / changes / dungeons 三个端点，"
             "物品详情与天赋树没有批量接口，重抓一轮 ≈3.6 小时机器活"},
    {"source": "wclbox · 其余快照（items-index 21,077 / dungeons+cards → 38 座 / "
               "dungeon-quests 396 条 30 座有 / boss-faces 232 首领 146 有脸 / "
               "talent 9 职业 466 节点 / professions 13 类 1,990 配方（新增 889）/ world / legacy）",
     "tier": "L1", "verdict": "used", "checked": "2026-10-11~16",
     "note": "**跨了三个 build**（70338 / 70374 / r-1791702948979）。真正旧的是：名称索引(10-13)、"
             "副本卡片与头像(10-11/10-13)、天赋(10-12)、专业与世界(10-11)。重抓前第一件事是拿"
             "meta.json 的自报数跟站内 scale.json 逐条比——那份自报数是免费的对照尺"},
    {"source": "wclbox · changes.json 天赋+法术改动 965 条",
     "tier": "L1", "verdict": "used", "checked": "2026-10-16",
     "note": "@1.60.1.70338。**这条不是欠账**：MAP §五 更正过，站内 scale.json 的 "
             "965/113/304/79/257 与上游 meta.json 逐条已对上，改动线早被另一台机器刷过"},

    # ── 转述源 B：两份插件（MAP §四，原件不在本机）────────────────
    {"source": "GI 插件 GearInsight_Forever/Data.lua · gear 1,515 / loot 1,875 / "
               "DungeonQuestData 29 座 387 qid",
     "tier": "L1", "verdict": "used", "checked": "2026-10-16",
     "note": "**取值顺序第一优先**（站主 10-16 定：GI > XR > wclbox，反转了 10-15 的评估结论）。"
             "已抽：gear 属性 1,370（成品 1,501 件用它）、loot（**无概率字段，掉落不写百分比的红线不破**）、"
             "DungeonQuestData（补了挖掘场 14、达拉然 15 两座 wclbox 与 wowhead 都空的）。"
             "抽取教训：GI 每行末尾挂 items={{id=...}}，直接对整行取 k=v 会让最后一件掉落物的 n/nEn "
             "盖掉怪自己的名字——修法写在 tools/extract-plugin-tables.py 注释里"},
    {"source": "GI 插件 · 未抽的四张表：spells 1,480 / achievements 211 / "
               "rares 338（带坐标，39 区域）/ talents 9 职业 27 树",
     "tier": "L2", "verdict": "blocked", "checked": "2026-10-16",
     "note": "**不是决定不做，是原料不在手边**：Data.lua 原件在 Windows 那台 D:\\project\\wow\\，"
             "本机 find 实测为空，而两个抽取脚本都要传 --gi 路径参数。拷过来放 "
             "~/Documents/project/.scratch/addon-data/（不提交进仓库）。"
             "talents 的中文名是作者/vmangos 译名，抽到了也**不能升译名等级**"},
    {"source": "GI 插件 · MacroData / KeysRot / DefaultBuildsData（宏 / 循环 / 构筑推荐）",
     "tier": "—", "verdict": "banned", "checked": "2026-10-16",
     "note": "踩《魔兽世界》国服运营团队 2026-09-24《付费插件及其违规商业化行为专项治理公告》敏感区 "
             "+ 强度导向红线，一律不碰（AGENTS.md:51,66：不做循环提示类自动指导、"
             "不做本站自己算的 DPS 或强度评分）"},
    {"source": "XR 插件 Data.lua · items 14,352 件（st/sell）",
     "tier": "L1", "verdict": "fallback", "checked": "2026-10-16",
     "note": "取值顺序第二，→ upstream/xr-items-detail.json 14,331 件；现在属性 1,502 件、"
             "卖价 2,258 件来自它。两源分歧**两个都留**：1,701 件与当前 build 数值签名不同，"
             "全部留档在 items-detail.json 的 meta.conflicts；插件两份都没有的 5,974 件"
             "（含无限新增 1,374 件）由 wclbox 兜住，所以“以插件为准”不是覆盖倒退"},
    {"source": "XR 插件 · Data_Bis（BiS 名单与同部位备选）",
     "tier": "L1", "verdict": "used", "checked": "2026-10-16",
     "note": "**这是红线的一个明写例外**（AGENTS.md:57-65，2026-10-16 站主拍板，仅限配装线）："
             "允许取第三方站的 BiS 名单当配装页数据源，放开的是“哪件装备在哪个部位”这一层。"
             "三条约束：① 每条带 url + 抓取日期，界面“来源”能点开；② 页顶固定写“本站整理自第三方"
             "资料，不是官方结论，也没做模拟计算”；③ 只取职业/专精/模式/部位/物品ID/物品名/品质/"
             "装等/来源标签这些事实字段，“为什么这件好”、属性优先级、循环建议、任何成段中文说明一律不取。"
             "代价：audit-translations.py 核不了它（它核名称不核推荐），所以**整体按 L1 留档，"
             "不许因为“看起来合理”就升 L0**"},
    {"source": "XR 插件 · Data_QuestDB 5,205 条任务（fc 全量，带等级/阵营/经验/坐标）",
     "tier": "—", "verdict": "banned", "checked": "2026-10-14",
     "note": "站主 10-14 拍板**明确暂不立项**：描述/目标/完成文本三类文案不可取"
             "（第三方站只取事实性标识符，不复制其描述文案），剩下的裸表价值打折"},
    {"source": "XR 插件 · Data_DungeonGuides / Data_DungeonRates（攻略文字 / 掉率管线）",
     "tier": "—", "verdict": "banned", "checked": "2026-10-16",
     "note": "强度与攻略正文红线；掉率那张表**作者自己也没填**。"
             "另：XR 的 Data_Skills / Data_Talents（法术书、天赋内嵌 JSON）不在此列，"
             "那是 GAP② 的第一源，等 Data.lua 原件到手后与 GI spells 并评"},

    # ── 其余源（MAP §五、§一点七）─────────────────────────────────
    {"source": "wago.tools/db2 · GroupFinderActivity CSV",
     "tier": "L1", "verdict": "used", "checked": "2026-10-16",
     "note": "副本英文名、排队区间、一次几人 → 快照 db2-group-finder.json @1.60.1.70338，109 条活动。"
             "**非暴雪域名，所以只当 L1**"},
    {"source": "wowhead.com/forever",
     "tier": "—", "verdict": "lead", "checked": "2026-10-16",
     "note": "**人工参照，禁止搬数据**。只有副本任务两处区域能用它补（wowhead-dungeon-quests.json），"
             "其余 9 个区域逐条写明了为什么不能用"},
    {"source": "参考站 · xiyou.json / chengjiu.json 两个批量端点",
     "tier": "L1", "verdict": "lead", "checked": "2026-10-18",
     "note": "xiyou 自报 count:36（22 条有坐标 + 23 条只给名字）、chengjiu 46 条带 text 整句**不落盘**。"
             "⚠️ 别把 36 当稀有上限：站内 world.json 是 **362 条稀有 / 41 区域**，其中 73 条没有 mapFile "
             "所以不标在图上 → **289 个能标点**（有断言钉着“多标 0／应标 289／实标 289”，"
             "见 VERIFY-world-rares-2026-10-18.md）；GI rares 那 338 只与参考站 36 只只重合 12 只，"
             "两边覆盖不同，不是谁漏收"},
    {"source": "foreverchanges.pro / wowclassicforever.info / news.17173.com",
     "tier": "L2", "verdict": "lead", "checked": "2026-10-16",
     "note": "只当线索与译名旁证，**整库复制禁止**。注意 foreverchanges.pro 就是两份插件的共同上游"
             "（fc = Beta 实测掉落/任务），所以它与插件互为旁证，**不是独立的第三方佐证**"},
    {"source": "github.com/cmangos/classic-db（GPL-3.0）",
     "tier": "L2", "verdict": "lead", "checked": "2026-10-09",
     "note": "1.12 中文 locale：**只取译名与结构**。GPL-3.0 有传染性，数据本体不能锁进付费品"
             "（这是从上一版 AI 线许可判定表继承下来的唯一一条结论，仍然成立）"},
    {"source": "网易DD dd.163.com",
     "tier": "—", "verdict": "banned", "checked": "2026-10-09",
     "note": "曾评估后**排除**：只有正式服/怀旧服，没有无限服内容"
             "（wow-forever-web/docs/wow-infinite-sources.md 第十、十二节）"},
]

VERDICT_LABEL = {"used": "已入库", "fallback": "兜底·分歧留档", "lead": "只当线索（不搬数据）",
                 "blocked": "原料不在手边", "banned": "禁搬"}


def _read_json(name):
    path = os.path.join(PRODUCT, name)
    if not os.path.isfile(path):
        return None, path
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), path
    except (ValueError, OSError) as e:
        return {"_error": str(e)}, path


def pdf_pages(path):
    """数页：/Count 不可信（Chrome 分页树，别处也有 /Count），只认 /Type /Page 非 Pages。
    与 scripts/build_bank_pdf.py 同一个判据，那边已实测过。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return 0
    return len(re.findall(rb"/Type\s*/Page[^s]", raw))


def _file_info(path, kind):
    st = os.stat(path)
    info = {"name": os.path.basename(path), "kind": kind,
            "kb": round(st.st_size / 1024.0, 1),
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "url": "/api/bank/file?name=" + os.path.basename(path)}
    if path.endswith(".pdf"):
        info["pages"] = pdf_pages(path)
    return info


def artifacts(version):
    """当前版本该有的交付物，缺哪个就报缺哪个（旧版本不进列表，免得发错文件）。"""
    want = [("AI面试答案库-v%s.md" % version, "库正文"),
            ("题库-v%s.json" % version, "结构化数据"),
            ("AI面试答案库-v%s.pdf" % version, "付费 PDF·A4"),
            ("AI面试答案库-v%s-手机竖版.pdf" % version, "付费 PDF·手机竖版"),
            ("AI面试答案库-样章-v%s.pdf" % version, "免费样章"),
            ("AI面试答案库-卖货页-v%s.html" % version, "卖货页"),
            ("AI面试答案库-知识地图.html", "知识地图 HTML"),
            ("AI面试答案库-知识地图.png", "知识地图长图"),
            ("自媒体AI写稿工具包-v1.zip", "免费引流工具包"),
            ("free_bank.json", "免费题库池"),
            ("repo_bank.json", "外部源解析结果")]
    out, missing = [], []
    for name, label in want:
        p = os.path.join(PRODUCT, name)
        if os.path.isfile(p):
            info = _file_info(p, label)
            out.append(info)
        else:
            missing.append("%s（%s）" % (name, label))
    return out, missing


def artifact_names():
    """给 /api/bank/file 当白名单：只允许打开这页列出来的文件，不开放整个 product/。"""
    names = []
    for info in artifacts(_version())[0]:
        names.append(info["name"])
    return set(names)


def _version():
    try:
        import build_interview_bank as bib
        return bib.VERSION
    except Exception:
        return "0.4"


def hook_policy():
    """钩子口径从两处现读：铁律文件标题行（v2.0 那次拍板）+ 机器门里两组正则的条数。"""
    out = {"rules_version": "", "rules_file": "", "onsite_fail": 0, "hook_info": 0,
           "paid": "付费品直接给链接，不设领取门槛", "free": ""}
    path = os.path.expanduser("~/Documents/自媒体/公众号写作铁律.md")
    out["rules_file"] = path
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                out["rules_version"] = f.readline().strip().lstrip("# ").strip()
        except OSError:
            pass
    try:
        from src import draft_audit as da
        out["onsite_fail"] = len(da.COMPLIANCE_PATTERNS)
        out["hook_info"] = len(da.HOOK_PATTERNS)
    except Exception:
        pass
    out["free"] = "免费交付物：关注后回复关键词领取（%s）" % (out["rules_version"] or "口径未读到")
    return out


def overview():
    version = _version()
    import build_interview_bank as bib
    paid, paid_path = _read_json("题库-v%s.json" % version)
    free, free_path = _read_json("free_bank.json")
    ext, ext_path = _read_json("repo_bank.json")
    files, missing = artifacts(version)
    warns = []
    if paid and paid.get("_error"):
        warns.append("题库 JSON 解析失败：%s" % paid["_error"])
        paid = None
    if free and free.get("_error"):
        warns.append("free_bank.json 解析失败：%s" % free["_error"])
        free = None
    try:
        ok, verdict = bib.audit_gate()
    except Exception as e:
        ok, verdict = False, "自检跑不起来：%s" % e
    out = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "product_dir": PRODUCT,
           "paid": {"version": version, "price": bib.PRICE, "audit_ok": ok, "audit": verdict,
                    "meta": (paid or {}).get("meta") or {},
                    "themes": (paid or {}).get("themes") or [],
                    "items": (paid or {}).get("items") or [],
                    "file": os.path.basename(paid_path)},
           "free": {"meta": (free or {}).get("meta") or {},
                    "per_module": (free or {}).get("per_module") or {},
                    "items": (free or {}).get("items") or [],
                    "file": os.path.basename(free_path)} if free else None,
           "external": {"meta": (ext or {}).get("source") or {},
                        "count": (ext or {}).get("count") or 0} if ext else None,
           "artifacts": files, "missing": missing,
           "samples": {"paid": sample_questions((paid or {}).get("items") or [], 3),
                       "free": sample_questions((free or {}).get("items") or [], 3)},
           "ledger": [{"label": VERDICT_LABEL.get(x["verdict"], x["verdict"]), **x}
                      for x in SOURCE_LEDGER],
           "hook": hook_policy(),
           "warnings": warns}
    return out


def sample_questions(items, n=6):
    """给页面抽几道真题看：优先抽带追问的、来源各来一条，别全是同一模块。"""
    picked, seen_mod = [], set()
    for it in items:
        mod = it.get("module") or it.get("theme") or ""
        if mod in seen_mod:
            continue
        if len((it.get("answer") or "")) < 60:
            continue
        picked.append(it)
        seen_mod.add(mod)
        if len(picked) >= n:
            break
    return picked
