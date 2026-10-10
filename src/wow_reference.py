# -*- coding: utf-8 -*-
"""魔兽题材的「实名参考」注入：整理稿那一步从 wow-forever 数据库取名单当依据。

为什么必须注入名单（而不是在提示词里写一句"请修正错字"）：整理稿的纪律是
「只基于输入材料，不要编造事实」，这条纪律同时挡住了瞎编**也挡住了纠错**——
模型没有依据判断「税代」就是「舒适的睡袋」，改了就算编造。所以 ASR 的同音错字
会一路原样传到图文卡片上（实测：装等→装灯、饰品→视频、睡袋→税代、鱼缸→魚鋼）。
有了名单，"按名单改"属于有依据的规范化。名单本身来自 `tools/sync_wow_icons.py`
同步的 `output/_assets/wow_terms.json`，权威源在 wow-forever 那个仓库。

口径（2026-10-10 用户拍板，选 A）：**库名只用来统一写法和补图标，不用来引入新实体**。
所以这段块反复强调两件事：素材确实在讲这个东西才用它的实名；名单里没有的
（混调干果、鱼缸这类上游自己都没拆的）保留素材原话，不许换个名字硬凑。

名单有 3070 条，全塞进提示词会挤掉素材本身。所以按相关性挑：
改名对照 → 素材提到的职业的全部天赋 → 与素材字面重合的配方与材料 → 素材里已经写对的实名。
总量按字数截断，超了就砍后面的。
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
TERMS_FILE = ROOT / "output" / "_assets" / "wow_terms.json"

# 认题材：game 字段最权威，标题与标签兜底（订阅没填 game 的素材也得能命中）
WOW_HINT = re.compile(r"(魔兽|魔兽世界|艾泽拉斯|wow|正式服|怀旧服|无限服)", re.I)
CLASSES = ["圣骑士", "德鲁伊", "猎人", "法师", "牧师", "潜行者", "萨满祭司", "术士", "战士"]
PROFS = ["炼金术", "锻造", "附魔", "工程学", "制皮", "裁缝", "采矿", "草药学",
         "剥皮", "烹饪", "钓鱼", "急救", "野营"]

BUDGET = 1500          # 整块（含纪律说明）的字符预算，超了就砍后面的组
MAX_TALENTS = 60       # 单个职业天赋上限（实测最多 56 条，等于整树全给）
MAX_RECIPES = 18       # 单个专业给的配方/材料条数
MAX_PROFS = 4          # 最多带几个专业：实测 3 个会把「制皮」挤掉（它只有 2 条命中，
                       # 而它恰恰是营地帐篷那条最需要的），4 个仍在预算内


def _load_terms():
    if not TERMS_FILE.exists():
        return None
    try:
        return json.loads(TERMS_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def is_wow(video: Dict[str, Any]) -> bool:
    blob = " ".join(str(video.get(k) or "") for k in ("game", "title", "tags", "category"))
    return bool(WOW_HINT.search(blob))


def _bigrams(text: str) -> set:
    t = re.sub(r"\s+", "", text or "")
    return set(t[i:i + 2] for i in range(len(t) - 1))


def build_block(videos: List[Dict[str, Any]]) -> str:
    """返回要塞进整理稿提示词的一段；非魔兽题材、或名单没同步 → 返回空串。"""
    vids = [v for v in (videos or []) if isinstance(v, dict)]
    wow = [v for v in vids if is_wow(v)]
    if not wow:
        return ""
    doc = _load_terms()
    if not doc:
        return ("\n【魔兽实名参考】名单还没同步（跑一次 tools/sync_wow_icons.py），"
                "本轮没有实名依据：转写里的可疑同音错字一律**原样保留并写进【风险核查】**，不许自己猜。\n")
    terms = doc.get("terms") or {}
    aliases = doc.get("aliases") or {}
    text = " ".join(str(v.get("title") or "") + " " + str(v.get("tags") or "") + " " +
                    str(v.get("transcript") or "")[:6000] for v in wow)

    lines = []
    head = [
        "【魔兽实名参考 · 来自 wow-forever 数据库（客户端解包与官方公告，非第三方攻略）】",
        "用法纪律，三条都必守：",
        "① 只用来**统一写法和纠正同音错字**：素材确实在讲这个东西、而转写写法明显读不通时，按名单改"
        "（如「税代」→舒适的睡袋、「魚鋼」→鱼缸、「彭韵」→烹饪）。",
        "② **不许拿名单引入新实体**：素材没讲的东西，名单里有也不写进去；名单里没有的名字保留素材原话，"
        "并写进【风险核查】提示人工核对，绝不换个相近的词凑上。",
        "③ 名单里带「未跟官方核对」分级的条目（多为天赋名），写法可以用，"
        "但**不许在稿子里称其为官方名称**；数值、点数上限、等级门槛一律以素材为准，名单只给名字。",
    ]
    # 纪律说明本身要先从预算里扣掉——早先没扣，八组名单一路放到 1788 字才停
    used = sum(len(x) for x in head)

    def emit(s):
        nonlocal used
        if used + len(s) > BUDGET:
            return False
        used += len(s)
        lines.append(s)
        return True

    # ① 改名对照：这是真错误，优先级最高
    if aliases:
        emit("已改名（素材若用旧名，一律换成现名）：" +
             "；".join("%s→%s" % (o, n) for o, n in sorted(aliases.items())[:12]))

    # ② 素材点到的职业：把整棵天赋树的名字给它（天赋名最容易在口播里被念错）。
    #    判定要严：只在标题里出现、或正文提到 ≥3 次才算"这篇在讲这个职业"。
    #    实测放宽到"正文出现过"就会把法师/术士/战士三整棵树（700 多字）塞进来，
    #    而那几条素材只是顺带提了一句——预算被顺带提的占掉，真正要的反而进不来。
    titles = " ".join(str(v.get("title") or "") for v in wow)
    def about(word):
        return (word in titles) or (text.count(word) >= 3)

    picked = 0
    for cls in [c for c in CLASSES if about(c)]:
        names = sorted(n for n, r in terms.items() if r.get("kind") == "天赋" and r.get("src") == cls)
        if names and not emit("%s天赋实名（%d 条）：%s" % (cls, len(names), "、".join(names[:MAX_TALENTS]))):
            break
        picked += 1
        if picked >= 2:          # 一棵树 50 多条，两棵就到这块的天花板了
            break

    # ③ 素材点到的专业：只给字面有重合的配方与材料（511 条制皮配方全塞会挤掉素材）。
    #    两条判定并用：标题/正文提到这个专业，**或者**素材里已经命中了它的两条以上条目——
    #    后者是为了救 ASR：实测「制皮」在转写里被写成「质疲」，只认关键词就整个专业都漏了，
    #    但「制革架」「营地帐篷」这些名字是对的，能从它们反推回制皮。
    #    候选按「与素材重合的二字组数量」排序取前 N，不按字面截断：
    #    按字面截会把「卫兵的链甲衬衫」这种只靠一个「的」字重合的捞进来，挤掉真正相关的。
    bg = _bigrams(text)

    def group_names(group):
        cands = []
        for n, r in terms.items():
            if r.get("src") != group:
                continue
            shared = _bigrams(n) & bg
            if shared:
                cands.append((len(shared), len(n), n))
        cands.sort(key=lambda x: (-x[0], x[1], x[2]))
        return [c[2] for c in cands]

    hit_names = {n for n in terms if len(n) >= 3 and n in text}
    # 按「素材里已命中该专业几条」排序，只带最相关的前 3 个：
    # 实测素材横跨 8 个专业时全带会撑到 1700 多字，把真正有用的制皮/烹饪挤到预算外
    qualified = []
    for prof in PROFS:
        mine = [n for n in hit_names if terms[n].get("src") == prof]
        if about(prof) or len(mine) >= 2:
            qualified.append((len(mine), prof))
    qualified.sort(key=lambda x: (-x[0], x[1]))
    for _n, prof in qualified[:MAX_PROFS]:
        names = group_names(prof)
        if names and not emit("%s配方/材料实名：%s" % (prof, "、".join(names[:MAX_RECIPES]))):
            break

    # ④ 素材里已经写对的实名（顺带告诉模型这些可信，不用再改）
    hit = sorted(hit_names)
    if hit:
        emit("素材中已出现且与名单一致的实名：%s" % "、".join(hit[:24]))

    return "\n" + "\n".join(head + lines) + "\n"


def stats() -> Dict[str, Any]:
    doc = _load_terms() or {}
    meta = doc.get("meta") or {}
    return {"ready": bool(doc), "terms": meta.get("terms"), "levels": meta.get("levels"),
            "aliases": meta.get("aliases"), "generated_at": meta.get("generated_at")}
