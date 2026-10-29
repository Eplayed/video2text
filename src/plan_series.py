# -*- coding: utf-8 -*-
"""plan_series.py — 双系列排产的"身份与状态"层（2026-10-22 那一单的第一块）。

任务书：`wow-forever-web/docs/PRD-video2text-双系列排产与夜间匹配-2026-10-22.md`。
这一层只管四件事，全部落服务端：

1. **稳定身份**：S1「从新手到高手」A1–D4 共 26 个内容位置、S2「官方信息观察室」O1/O2/O3 三个章节模板、
   以及从素材库直接起的独立篇，各自有一个**永不变**的 `slot_key`。
   为什么键不能变：发布台账（`publish_stats.slot_key`）、编辑证据链（`guide_provenance.slot_key`）、
   排产任务（`plan_tasks.slot_key`）三本账都靠它对上号。
   所以「先做独立稿、事后关联系列」只写一个 `parent_slot` 引用，**不改稿件自己的身份、
   不换键、不复制任务**（任务书 §3 明令：应建立引用关系，不复制任务或把多篇并成一篇；
   把稿件的 series 改成 s1 会让看板的 26 个内容位置数成 27，那是计数口径被写坏了）。
2. **状态只由服务端事实算**：八档（未匹配／有候选／已采用／整合稿中／待拆文案／看版面／待人工发布／已发布）
   每一档都指向一条查得到的事实，界面不许自己推。
   特别是 `plan_tasks.status='done'` **不等于已发布**——那是 10-21 复核点名的老毛病（生成完成冒充发布事实）。
3. **发布真相**：读 `publish_stats`（槽号命中才算确认），撤销走**追加式审计**，
   不删台账行、也不允许"前端取消勾选而服务端仍算已发"。
4. **夜间候选**：只写 `plan_candidates` 这一张表。匹配**从不**碰 `plan_tasks.video_ids`、
   不碰整理稿、不碰海报产物——"候选不污染已采用快照"是任务书 §3 的硬线。

三条边界（别扩错方向）：
  · 位置是**阅读次序与内容位置**，不是 26 篇交稿配额；空位可以长期空着，匹配到素材也不自动成稿。
  · 匹配是素材库的**只读检索 + 可解释排序**：不调模型、不采外站、不自动采用、不出图、不发布。
  · 旧排产页 DATA 里那 11 条静态选题（odur/ban/…）不迁不删，以 `origin='legacy'` 注册进来，
    老任务与老稿照旧按原 slot_key 查得到。

Python 3.9 兼容：不用 match / `X | Y` 标注。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
# 配置单独一份、不进 git（与 config/sync_tasks.json 同一套纪律：运行数据不入库）
MATCH_STORE = Path(os.environ.get("V2T_PLAN_MATCH_STORE") or
                   (ROOT / "config" / "plan_match.json"))
# 任务书 §4：默认 22:00 北京时间。这台机器就在东八区，但**别拿本机时区当准**——
# 补跑判据算的是"北京时间的那一个自然日跑过没有"，用固定偏移写死，换机器/换时区不会偷偷改口径。
# Asia/Shanghai 自 1991 年起不再实行夏令时，固定 +08:00 与 zoneinfo 结果一致，且不需要 tzdata。
TZ_OFFSET = dt.timedelta(hours=8)
TZ_CN = dt.timezone(TZ_OFFSET)
DEFAULT_RUN_AT = "22:00"
SLOT_KEY_MAX = 24          # 与 ops_store.register_publish 的槽号约束一致（字母数字/-/_，≤24）
SLOT_RE = re.compile(r"^[0-9A-Za-z_\-]{1,%d}$" % SLOT_KEY_MAX)
MIN_TL = 200               # 正文短于这个数只算线索，不占候选位（与 plan_brief 同值）
CAND_CAP = 6               # 每个位置最多列几条候选：排产表是给人挑的，不是搜索结果的转储
# 排产这一单只管头条那条线。发布状态一律按 **(槽号, 渠道)** 判：
# 只按 slot_key 查台账的话，公众号登记一篇，头条那格就跟着显示"已发布"并停止夜间匹配
# （Codex 二次复验第 2 条，与前一笔「同题双渠道稿件与进度分开」的口径冲突）。
DEFAULT_CHANNEL = "toutiao"
_LOCK = threading.Lock()
_READY = set()

# ── 八档状态：标签与"这一档凭什么"各只有一份，页面与接口都从这里读 ──────────────
STAGES = [
    ("unmatched",  "未匹配",   "还没跑过匹配，或匹配到零条 —— 零就显示零，不拿历史素材填位"),
    ("candidates", "有候选",   "plan_candidates 里有这条位置的材料，等人勾"),
    ("adopted",    "已采用",   "plan_tasks 当前版存了人勾的素材清单，还没开始整理"),
    ("drafting",   "整合稿中", "plan_tasks 当前版 status=running（一次只允许一版在跑）"),
    ("draft_done", "待拆文案", "整理稿已落库（summary_id），还没有海报产物"),
    ("layout",     "看版面",   "已出图文包，但质量门没全过（manifest.publishable=false）"),
    ("ready",      "待人工发布", "图文包 publishable=true，等人去平台发"),
    ("published",  "已发布",   "台账 publish_stats 里有这条槽号的登记（撤销后不算）"),
]
# 解包时别拿 `_v` 去绑然后写 `v`——推导式作用域里会直接 NameError（冒烟实测撞过一次）。
STAGE_LABEL = {k: lab for k, lab, _why in STAGES}
STAGE_WHY = {k: why for k, _lab, why in STAGES}
STAGE_ORDER = {k: i for i, (k, _lab, _why) in enumerate(STAGES)}

SERIES_LABEL = {"s1": "从新手到高手", "s2": "官方信息观察室", "free": "独立篇",
                "legacy": "旧排产题（保留可查）"}

# ── S1 内容地图（26 个位置）：标题/角度/检索词/边界，抄自 wow-planner 那份 PLAN ──
# 检索词只是"去哪儿捞线索"，不是事实；每个位置匹配到零条也照样显示零。
_CHAPTERS = {
    "A": ("入门设置", "需要对应客户端与插件版本实测；没有真实操作记录或图片使用依据就不出操作稿"),
    "B": ("选职业", "具体专精与 PvE/PvP 表现须逐条回查官方资料与当前客户端；证据不足只写选择画像，不补强度结论"),
    "C": ("成长与资源", "路线和收益不能从第三方攻略照搬；掉落不写概率，缺值写待实测"),
    "D": ("进阶判断", "无本人或明确来源的对照证据时只给检查方法；不自算 DPS／强度评分"),
}
# (位置号, 标题, 角度, 检索词, 默认稿型, 额外边界)
_S1: List[Tuple[str, str, str, List[str], str, str]] = [
    ("A1", "先确认你进的是哪个服", "Forever、时光服、正式服三者的辨认与当前版本核对",
     ["Forever", "无限服 版本", "测试服"], "guide", "截图或日期与实际入口不符则重核"),
    ("A2", "进游戏先存下原始设置", "改画面/按键之前，哪些原始配置该先备份",
     ["设置 备份", "配置文件", "画面 设置"], "guide", "本站要自己走一遍备份与还原"),
    ("A3", "插件怎么装、怎么备份、怎么卸", "从原渠道安装、留备份、出问题卸载回退",
     ["插件", "AddOn", "界面 插件"], "guide", "没有真实安装／回退记录或图片使用依据则不出操作稿"),
    ("A4", "界面太乱先调这三块", "动作条、姓名板、小地图这三块区域的整理顺序",
     ["界面 整理", "姓名板", "UI"], "guide", ""),
    ("A5", "DD 分享码怎么导入并核对版本", "Details 分享码导入后如何核对适用版本",
     ["DD 分享", "分享码", "Details"], "guide",
     "露露分享码 144659 目前只是内部线索：作者授权与实际内容未核，未核实前停在内部研究"),
    ("A6", "宏与 WA 的免费导入、验证、回退", "不花钱拿到、装完怎么验证、装坏了怎么退",
     ["宏", "WeakAuras", "WA"], "guide", "本站网页专题；小程序不承载字符串，不分发插件本体"),
    ("B0", "先按玩法偏好挑职责", "用操作负担与组队职责缩小职业候选，不给强度排名",
     ["职业 选择", "新手 职业", "选职业"], "guide", "把偏好匹配写成强度排行则退回"),
    ("B1", "战士：前排与资源节奏", "适合谁、PvE 承担什么、PvP 有什么工具与限制",
     ["战士"], "guide", "无证据不写强度"),
    ("B2", "圣骑士：输出承伤与辅助之间", "三职责切换的代价与选择",
     ["圣骑士"], "guide", "无证据不写强度"),
    ("B3", "猎人：宠物与距离管理", "宠物取舍、距离控制与操作成本",
     ["猎人"], "guide", "无证据不写强度"),
    ("B4", "潜行者：潜行、控制与近身", "控制链与近身操作负担",
     ["潜行者"], "guide", "无证据不写强度"),
    ("B5", "牧师：治疗、护盾与暗影", "三条路线的适用场景",
     ["牧师"], "guide", "无证据不写强度"),
    ("B6", "萨满祭司：图腾与混合职责", "图腾管理与增强/恢复/元素的选择",
     ["萨满"], "guide", "无证据不写强度"),
    ("B7", "法师：施法控制与法术流派", "控制技与流派选择",
     ["法师"], "guide", "无证据不写强度"),
    ("B8", "术士：宠物、持续伤害与资源", "宠物取舍与碎片管理",
     ["术士"], "guide", "无证据不写强度"),
    ("B9", "德鲁伊：多形态多职责的切换成本", "形态切换与职责覆盖的代价",
     ["德鲁伊"], "guide", "无证据不写强度"),
    ("C1", "1–60 每阶段要核什么", "等级阶段的前置条件与节奏",
     ["练级", "升级", "满级"], "guide", ""),
    ("C2", "第一次进副本前查什么", "任务、首领与掉落本站查询路径示范",
     ["副本 准备", "五人本", "副本 攻略"], "guide", "所选副本关键字段缺失则换本，不补编"),
    ("C3", "一件装备从哪来", "看装备名反查获取路径的方法",
     ["装备 获取", "掉落", "装备 来源"], "guide", "掉落不写概率"),
    ("C4", "专业怎么选、配方怎么查", "专业投入与配方来源核对",
     ["专业", "配方", "制造业"], "guide", "价格、冲级路线或收益未经本人核实则剔除"),
    ("C5", "地精商人的游戏内账本", "成本、材料、手续费、时间四项怎么记",
     ["金币", "商人", "成本"], "mix", "只用游戏内价格与本人记录；不碰人民币兑金币、漏洞、多开、异常带练"),
    ("C6", "第一次团本前的准备清单", "合剂、修理、站位、替补这些准备项",
     ["团本 准备", "开荒", "团队 副本"], "guide", ""),
    ("D1", "同一职业换 PvE／PvP 要重核什么", "场景变了哪些条件要重看",
     ["PvP", "PvE", "天赋 切换"], "guide", "无对照证据只给检查方法"),
    ("D2", "团队分工与辅助价值怎么比", "不靠 DPS 数字比较贡献的口径",
     ["团队 分工", "辅助", "职责"], "guide", "不自算强度评分"),
    ("D3", "在真实战斗里精简插件与警报", "哪些警报在战斗中真的会看",
     ["警报", "插件 精简", "战斗 界面"], "guide", "无本人记录只给方法"),
    ("D4", "专业、换装与金币的取舍", "投入产出与回本条件的决策表",
     ["换装", "金币", "专业 投入"], "mix", "不给稳赚路线"),
]
# ── S2 三个章节模板：位置不预置稿件，文章由真实公告创建 ──
_S2 = {
    "O1": ("官方事实", "国服官方公告、蓝贴、职业解析或补丁说明的时间、版本、生效范围及原文",
           ["公告", "蓝贴", "职业 深度解析", "补丁"], "mix",
           "只复述核实后的变更；不把「未来 Beta build」写成「现已上线」；先确认这条真属 Forever"),
    "O2": ("条件预测", "以 O1 一手事实为前提写影响路径、成立条件、反例与待验证点",
           ["预测", "影响", "改动"], "mix",
           "必须关联前序 O1；显式标「本站推测」；不编职业名次、DPS、胜率"),
    "O3": ("上线后回看", "按新公告或真实可归因实测回填上一条预测",
           ["回看", "上线 后", "实测"], "mix",
           "必须关联被回看的那篇；保留旧预测原文、日期与证据，不静默覆盖"),
}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def cn_now(now: Optional[dt.datetime] = None) -> dt.datetime:
    """一律返回 **naive 的北京时间**（不带 tzinfo）。

    为什么把 tzinfo 抹掉：这一模块里所有比较（`now >= _at(run_at, day)`、补跑回看）拿的都是
    naive 的 `_at()` 结果，混进一个 aware 时间就会抛"can't compare offset-naive and offset-aware"。
    传进来的 naive 时间按"就是北京时间"处理，不做二次换算（测试用固定时钟就是这么传的）。
    """
    if now is None:
        return dt.datetime.now(TZ_CN).replace(tzinfo=None)
    if now.tzinfo is None:
        return now
    return now.astimezone(TZ_CN).replace(tzinfo=None)


def valid_slot(key: str) -> bool:
    return bool(SLOT_RE.match(str(key or "")))


def norm_channel(raw) -> str:
    """渠道键归一：界面可能交 `toutiao`，也可能交中文名「头条」，库里只存键。
    名单取 `ops_store.CHANNELS` 那一份，不在这里抄第二份（抄了迟早对不上）。

    **空值才用默认渠道；写了但不认识的一律报错**（Codex 二次复验点名）：
    原来 `norm_channel('wechta')` 会静默退成 `toutiao`，等于把一篇公众号的登记
    记到头条头上——拼错的渠道比没填危险得多。
    """
    from . import ops_store
    s = str(raw or "").strip()
    if not s:
        return DEFAULT_CHANNEL
    if s in ops_store.CHANNELS:
        return s
    for k, v in ops_store.CHANNELS.items():
        if v == s:
            return k
    raise ValueError("渠道只能是 %s 之一（收到「%s」）"
                     %(" / ".join(ops_store.CHANNELS.values()), str(raw)[:20]))


# ── S2 那条链的判据（服务端一份）：O2 挂在一篇 O1 上，O3 挂在被回看的那篇上 ──────
_LINK_NEEDS = {"O2": ("O1",), "O3": ("O1", "O2")}


def check_link(con, series: str, chapter: str, link_slot: str) -> str:
    """返回空串 = 通过；否则是不能建这一篇的原因。

    为什么必须写在服务端（Codex 二次复验第 3 条）：前端按钮只渲染「起一篇 O1 稿」，
    点 O2/O3 也照样提交 `chapter:'O1'`，而服务端**根本不查前序**——绕过前端直接 POST
    就能建出一篇没有前序的"条件预测"。那种稿正是任务书明令不许出现的（拿推测当事实）。
    """
    key = str(link_slot or "").strip()
    if str(series or "") != "s2":
        if key and not con.execute("SELECT 1 FROM plan_slots WHERE slot_key=?",
                                   (key,)).fetchone():
            return "关联的槽号查不到（%s）" % key
        return ""
    ch = str(chapter or "").upper()
    need = _LINK_NEEDS.get(ch)
    if not need:
        return ""                                   # O1 由真实公告建出来，没有前序
    if not key:
        return ("「%s」这一章必须先关联前序那一篇（%s），空着建出来的稿没有依据"
                % (ch, " 或 ".join(need)))
    row = con.execute("SELECT series, chapter FROM plan_slots WHERE slot_key=?",
                      (key,)).fetchone()
    if row is None:
        return "关联的前序稿件查不到（槽号 %s）—— 先在 %s 那一篇起稿，再回来写这一篇" % (
            key, " 或 ".join(need))
    if str(row[0] or "") != "s2" or str(row[1] or "").upper() not in need:
        return ("「%s」只能关联 S2 的 %s 那几篇，%s 是 %s/%s，不是前序"
                % (ch, " 或 ".join(need), key, row[0], row[1] or "-"))
    return ""


# ── 建表与种子 ────────────────────────────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS plan_slots (
    slot_key TEXT PRIMARY KEY,
    series TEXT NOT NULL,
    chapter TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL DEFAULT '',
    title TEXT DEFAULT '',
    angle TEXT DEFAULT '',
    keywords TEXT DEFAULT '[]',
    form TEXT DEFAULT 'guide',
    boundary TEXT DEFAULT '',
    link_slot TEXT DEFAULT '',
    parent_slot TEXT DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'map',
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS plan_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_key TEXT NOT NULL,
    video_id INTEGER NOT NULL,
    forever TEXT NOT NULL DEFAULT 'unknown',
    reason TEXT DEFAULT '',
    run_date TEXT DEFAULT '',
    added_at TEXT, last_seen TEXT,
    UNIQUE(slot_key, video_id)
);
CREATE TABLE IF NOT EXISTS plan_match_runs (
    run_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'night',
    run_date TEXT NOT NULL,
    started_at TEXT, finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    note TEXT DEFAULT '',
    positions INTEGER DEFAULT 0, added INTEGER DEFAULT 0,
    skipped INTEGER DEFAULT 0, seen INTEGER DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0, catchup_of TEXT DEFAULT '',
    claimed_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS plan_publish_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_key TEXT NOT NULL, channel TEXT DEFAULT '', ref_id TEXT DEFAULT '',
    action TEXT NOT NULL, title TEXT DEFAULT '', pub_date TEXT DEFAULT '',
    url TEXT DEFAULT '', note TEXT DEFAULT '', at TEXT
);
CREATE TABLE IF NOT EXISTS plan_slot_flags (
    slot_key TEXT PRIMARY KEY,
    local_published INTEGER NOT NULL DEFAULT 0,
    note TEXT DEFAULT '', seen_at TEXT, resolved_at TEXT, resolved_by TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_plan_cand_slot ON plan_candidates(slot_key);
"""


def ensure(db_path) -> None:
    key = os.path.abspath(str(db_path))
    # 缓存认"路径 + inode"，不只认路径：测试里同一个文件名会被整份替换成另一份库，
    # 只认路径就会拿着"表建过了"的旧账不建表，报错报成"no such table"（实测撞过一次）。
    try:
        stamp = (key, os.stat(key).st_ino)
    except OSError:
        stamp = (key, None)
    if stamp in _READY:
        return
    with _LOCK:
        if key in _READY:
            return
        con = sqlite3.connect(key)
        try:
            con.executescript(_DDL)
            _migrate(con)
            con.commit()
        finally:
            con.close()
        _READY.add(stamp)


def _migrate(con) -> None:
    """老库升级：补 `parent_slot` 这一列，并把"老式关联"改回引用关系。

    为什么要有这一段：第一版把「独立稿事后归进 S1」做成了**改标签**（series 直接写 s1、
    position 写 C2），于是地图上的 C2 变成两行、看板把 S1 数成 27 个位置
    （Codex 二次复验第 4 条）。现在关联只写 `parent_slot`，稿件自己的身份不动。
    迁移只碰 `origin='user'` 且确实能对上某个地图位置的行，**槽号与 plan_tasks 的关联一律不变**。
    """
    cols = {c[1] for c in con.execute("PRAGMA table_info(plan_slots)")}
    if "parent_slot" not in cols:
        con.execute("ALTER TABLE plan_slots ADD COLUMN parent_slot TEXT DEFAULT ''")
    if "series" not in cols:
        return
    rows = con.execute("SELECT slot_key, position FROM plan_slots"
                       " WHERE origin='user' AND series='s1'").fetchall()
    for slot_key, pos in rows:
        target = "s1%s" % str(pos or "").lower()
        hit = con.execute("SELECT 1 FROM plan_slots WHERE slot_key=? AND origin='map'",
                          (target,)).fetchone()
        if not hit:
            continue          # 对不上地图位置就不动，宁可留原样也别凭空造个关联
        con.execute("UPDATE plan_slots SET series='free', chapter='', position=?,"
                   " parent_slot=?, updated_at=? WHERE slot_key=?",
                   (slot_key, target, _now(), slot_key))


def seed(db_path, legacy_items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    """把 26 个位置 + S2 三章 + 旧排产 11 题注册进 plan_slots（幂等，只补缺、不改人已编辑的格）。

    为什么"只补缺"：位置标题/检索词允许在界面上改（比如 B6 要写"萨满祭司"还是"萨满"），
    每次启动拿代码里的表覆盖回去，等于把人的修改吃掉——这类静默回滚本项目栽过多次。
    """
    ensure(db_path)
    rows = list(_s1_rows()) + list(_s2_rows())
    for it in legacy_items or []:
        sid = str(it.get("id") or "").strip()
        if sid and valid_slot(sid):
            rows.append({"slot_key": sid, "series": "legacy", "chapter": "",
                         "position": sid, "title": it.get("title") or "",
                         "angle": it.get("decision") or "",
                         "keywords": it.get("search") or [], "form": "guide",
                         "boundary": "", "origin": "legacy"})
    con = sqlite3.connect(str(db_path))
    added = 0
    try:
        for r in rows:
            cur = con.execute(
                "INSERT INTO plan_slots (slot_key, series, chapter, position, title, angle,"
                " keywords, form, boundary, link_slot, origin, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,'',?,?,?)"
                " ON CONFLICT(slot_key) DO NOTHING",
                (r["slot_key"], r["series"], r["chapter"], r["position"], r["title"],
                 r.get("angle") or "", json.dumps(r.get("keywords") or [], ensure_ascii=False),
                 r.get("form") or "guide", r.get("boundary") or "", r.get("origin") or "map",
                 _now(), _now()))
            added += cur.rowcount
        con.commit()
    finally:
        con.close()
    return {"registered": len(rows), "added": added}


def _s1_rows():
    for pos, title, angle, kws, form, extra in _S1:
        ch = pos[0]
        label, gate = _CHAPTERS.get(ch, (ch, ""))
        yield {"slot_key": "s1%s" % pos.lower(), "series": "s1", "chapter": ch,
               "position": pos, "title": title, "angle": angle, "keywords": kws,
               "form": form, "boundary": "；".join(x for x in (gate, extra) if x)}


def _s2_rows():
    for pos, (label, angle, kws, form, gate) in _S2.items():
        yield {"slot_key": "s2%s" % pos.lower(), "series": "s2", "chapter": pos,
               "position": pos, "title": label, "angle": angle, "keywords": kws,
               "form": form, "boundary": gate}


def slots(db_path, series: str = "") -> List[Dict[str, Any]]:
    ensure(db_path)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        sql = "SELECT * FROM plan_slots"
        args: Tuple[Any, ...] = ()
        if series:
            sql += " WHERE series=?"
            args = (series,)
        sql += " ORDER BY series, position"
        rows = [_slot_dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()
    return rows


def _slot_dict(r) -> Dict[str, Any]:
    d = dict(r)
    try:
        d["keywords"] = json.loads(d.get("keywords") or "[]")
    except ValueError:
        d["keywords"] = []
    return d


def get_slot(db_path, slot_key: str) -> Optional[Dict[str, Any]]:
    for s in slots(db_path):
        if s["slot_key"] == slot_key:
            return s
    return None


def check_creation(series: str, chapter: str) -> str:
    """这一篇**能不能新建**（Codex 二次复验的边界条）。空串 = 可以。

    S1 那 26 格是**地图注册的内容位置**，不是谁都能新建的稿件：
    绕过页面 POST 一个 `series='s1'` 就会多出一行 `origin='user'` 的 S1，
    看板又数回 27 格（上一轮修的是"关联不改标签"，这一条修的是"新建不许冒充系列位"）。
    要归位只能走 `link_to_series` 写 `parent_slot`。
    S2 只允许 O1/O2/O3 三个真章节，`chapter='O9'` 这种造出来的第四张模板没人认。
    """
    s = str(series or "free").strip()
    if s not in ("free", "s2"):
        if s == "s1":
            return ("S1 的 26 个位置是内容地图，不能新建稿件冒充："
                    "要采用请到那一格点「采用勾选项到本篇」，要归位请用关联（parent_slot）")
        return "新建只支持独立篇（free）或 S2 的文章（s2），收到「%s」" % s
    if s == "s2" and str(chapter or "").strip().upper() not in _S2:
        return "S2 只有 %s 三个章节，没有「%s」这一章" % (" / ".join(_S2.keys()), chapter)
    return ""


def new_slot(db_path, title: str, series: str = "free", chapter: str = "",
             position: str = "", form: str = "guide", link_slot: str = "",
             keywords: Optional[List[str]] = None, origin: str = "user") -> Dict[str, Any]:
    """建一篇"由真实消息/人工起稿"的位置：S2 的文章、素材库直接起的独立篇。

    键怎么给：`s2o1-3` / `free-7a3f1c`。都是**服务端算出来的短键**，
    因为 ops_store 对槽号是"清洗后与原值不一样就报错"，界面拼的中文名进不去。
    O2/O3 的前序、以及"能不能新建这一篇"都在**这里**判（不在界面判），
    所以既建不出没有依据的预测稿，也建不出冒充 S1 位置的第 27 行。
    """
    ensure(db_path)
    err = check_creation(series, chapter)
    if err:
        raise ValueError(err)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("BEGIN IMMEDIATE")
        err = check_link(con, series, chapter, link_slot)
        if err:
            con.rollback()
            raise ValueError(err)
        key = _alloc_key(con, series, chapter or position or "o1")
        now = _now()
        con.execute(
            "INSERT INTO plan_slots (slot_key, series, chapter, position, title, angle,"
            " keywords, form, boundary, link_slot, origin, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, series, chapter, position or key, (title or "")[:80], "",
             json.dumps(keywords or [], ensure_ascii=False), form, "", link_slot, origin,
             now, now))
        con.commit()
    finally:
        con.close()
    out = get_slot(db_path, key)
    return out or {}


def _alloc_key(con, series: str, chapter: str) -> str:
    """s2 按章节往后排号；free 用 6 位随机串（撞了就重取，键空间足够大）。"""
    if series == "s2":
        ch = (chapter or "o1").lower()
        n = 1
        while con.execute("SELECT 1 FROM plan_slots WHERE slot_key=?",
                          ("s2%s-%d" % (ch, n),)).fetchone():
            n += 1
        return "s2%s-%d" % (ch, n)
    import uuid
    for _ in range(20):
        key = "free-%s" % uuid.uuid4().hex[:6]
        if not con.execute("SELECT 1 FROM plan_slots WHERE slot_key=?", (key,)).fetchone():
            return key
    raise ValueError("独立篇键分配失败（重试 20 次都撞上已有槽号）")


def slot_guard(db_path, slot_key: str, channel: str = DEFAULT_CHANNEL) -> Dict[str, Any]:
    """采用/起稿之前那道判断。**判据与 board() 同一份**，不在路由里再推一遍。

    为什么单独有这个函数：路由里当时直接写 `s["adoptable"]`、`s["published"]`，
    而这两个键是 `board()` 算状态时才加上去的——`get_slot()` 返回的那份没有，
    一点「采用」就 500（KeyError）。凡是"两个地方各自推一遍同一个事实"，迟早一边有一边没有。
    返回 {"slot": 带 adoptable/published 的那份, "error": 不能采用的原因（可空）}。
    """
    s = get_slot(db_path, slot_key)
    if not s:
        return {"slot": None, "error": "查不到这个内容位置（%s）" % slot_key}
    s["adoptable"] = not (s["series"] == "s2" and s["origin"] == "map")
    pub = published_state(db_path, slot_key, channel=channel)
    s["published"] = pub["state"] == "confirmed"
    s["publish_state"] = pub
    err = ""
    if not s["adoptable"]:
        err = ("「%s」是章节模板，只当线索池：先用这条公告建一篇稿子，采用落在那一篇上"
               % (s.get("title") or slot_key))
    elif s["published"]:
        err = "这一篇已登记发布，不再改它的采用清单。版本有新变化请另建后续篇（不能悄悄重开旧稿）"
    return {"slot": s, "error": err}


def link_to_series(db_path, slot_key: str, series: str = "", chapter: str = "",
                   position: str = "", link_slot: str = "") -> Dict[str, Any]:
    """把一篇稿**引用**到某个内容位置上：只写 `parent_slot`，它自己的身份一律不动。

    为什么不改 series/position（Codex 二次复验第 4 条）：S1 那 26 格是**内容位置**，
    不是稿件数。第一版把独立稿的 series 改成 s1、position 写成 C2，于是
    「第一次进副本前查什么」在库里出现两行、看板把 S1 数成 27 个位置，
    读起来像"多了一个要写的位置"。任务书 §3 要的是引用关系，不复制也不新增位置。
    传 `series='free'` 且不给目标 = 取消关联。
    """
    ensure(db_path)
    series = str(series or "").strip()
    if series not in ("s1", "s2", "legacy", "free"):
        raise ValueError("series 只能是 s1 / s2 / free / legacy，收到「%s」" % series)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute("SELECT slot_key, origin, series FROM plan_slots WHERE slot_key=?",
                          (slot_key,)).fetchone()
        if cur is None:
            raise ValueError("查不到这一篇（槽号 %s）" % slot_key)
        if cur[1] != "user":
            raise ValueError("%s 不是稿件（origin=%s），关联只能指人自己起的那一篇"
                             % (slot_key, cur[1]))
        target = _parent_target(con, series, chapter, position, link_slot)
        if series == "s2":
            err = check_link(con, "s2", chapter, link_slot)
            if err:
                raise ValueError(err)
        con.execute("UPDATE plan_slots SET parent_slot=?,"
                    " link_slot=CASE WHEN ?='' THEN link_slot ELSE ? END,"
                    " updated_at=? WHERE slot_key=?",
                    (target, link_slot, link_slot, _now(), slot_key))
        con.commit()
    finally:
        con.close()
    return get_slot(db_path, slot_key) or {}


def _parent_target(con, series: str, chapter: str, position: str, link_slot: str) -> str:
    """关联目标：S1 给位置号（C2 → s1c2）、S2 给章节（O1 → s2o1）、legacy 给原 id。
    目标必须**确实是一张地图位**，查不到就报错——悄悄存一个对不上的 parent_slot，
    比拒绝更难查（看板的"这一格下的关联稿"就是靠它对上的）。"""
    key = str(link_slot or "").strip()
    if series == "s1" and str(position or "").strip():
        key = "s1%s" % str(position).strip().lower()
    elif series == "s2" and str(chapter or "").strip():
        key = "s2%s" % str(chapter).strip().lower()
    elif series == "legacy" and str(position or "").strip():
        key = str(position).strip()
    if series == "free" and not key:
        return ""
    if not key:
        raise ValueError("要关联到哪个位置没给（S1 给位置号如 C2，S2 给章节如 O1）")
    row = con.execute("SELECT origin FROM plan_slots WHERE slot_key=?", (key,)).fetchone()
    if row is None:
        raise ValueError("查不到要关联的内容位置（%s）—— 位置号请照排产表上的写，如 C2" % key)
    if row[0] not in ("map", "legacy"):
        raise ValueError("%s 是 %s 那张位置，不是内容位置，不能当关联目标" % (key, row[0]))
    return key


# ── 夜间匹配的配置（config/plan_match.json，不进 git）───────────────────────
def load_cfg(write_if_missing: bool = True) -> Dict[str, Any]:
    """读配置。文件坏了不抛：整份退回默认值并说明原因（排产页不能因为一格 JSON 打不开就白屏）。

    `since` 是"这套匹配从哪天起存在"。为什么要有：补跑判据问的是"昨晚跑过没有"，
    而**刚装好的那天历史上一行都没有**——不记这一格，启动瞬间就会"补跑"一次，
    把一个本该晚上跑的全库扫描挪到任意时刻。文件不存在时顺手落一份（原子写）。
    """
    out = {"enabled": True, "run_at": DEFAULT_RUN_AT, "updated_at": "",
           "since": cn_now().date().isoformat(), "note": ""}
    try:
        raw = json.loads(MATCH_STORE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if write_if_missing:
            try:
                save_cfg(True, DEFAULT_RUN_AT)
            except OSError:
                pass
        return out
    except ValueError as e:
        out["note"] = "配置文件读不懂（%s），这一轮按默认值走：开关开、时刻 %s" % (str(e)[:60], DEFAULT_RUN_AT)
        return out
    if isinstance(raw, dict):
        out["enabled"] = bool(raw.get("enabled", True))
        out["run_at"] = norm_time(raw.get("run_at")) or DEFAULT_RUN_AT
        out["updated_at"] = raw.get("updated_at") or ""
        out["since"] = str(raw.get("since") or out["since"])[:10]
    return out


def save_cfg(enabled: bool, run_at: str, since: str = "") -> Dict[str, Any]:
    t = norm_time(run_at)
    if not t:
        raise ValueError("执行时刻要写成 22:00 这样，收到的是「%s」" % str(run_at)[:20])
    old = load_cfg(write_if_missing=False)
    cfg = {"enabled": bool(enabled), "run_at": t, "updated_at": _now(), "note": "",
           "since": (since or old.get("since") or cn_now().date().isoformat())[:10]}
    MATCH_STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(MATCH_STORE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, str(MATCH_STORE))     # 原子替换：写一半被 kill 不会留下半个 JSON
    return cfg


def norm_time(raw) -> str:
    s = str(raw or "").strip()[:5]
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", s)
    if not m:
        return ""
    return "%02d:%02d" % (int(m.group(1)), int(m.group(2)))


def _at(run_at: str, day: dt.date) -> dt.datetime:
    hh, mm = (run_at or DEFAULT_RUN_AT).split(":")
    return dt.datetime(day.year, day.month, day.day, int(hh), int(mm))


# ── 发布真相：台账 + 撤销审计 + 旧本地勾选 ─────────────────────────────────
def published_state(db_path, slot_key: str, channel: str = DEFAULT_CHANNEL) -> Dict[str, Any]:
    """这一格在**这一个渠道**发过没有。三档说清，不含混：

    · `confirmed` 台账里有 (槽号, 渠道) 命中的行（撤销过就不算）；
    · `pending_review` 只有浏览器本地勾过、服务端查无登记 → 界面标「待核对旧勾选」，
      夜间匹配**保守跳过**（任务书 §4：不得静默清空，也不得悄悄当成已正式登记）；
    · `none` 服务端与本地都没有事实。

    渠道必须进判据（Codex 二次复验第 2 条）：原来只按 slot_key 查，公众号登记一条
    `wechat/s1a1`，头条看板上这一格就变成"已发布"、夜间匹配跟着不再给它出候选——
    同一张位置在两个渠道是两篇稿、两条进度，不能互相代表。
    """
    ensure(db_path)
    ch = norm_channel(channel)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT channel, ref_id, title, pub_date, url, slot_key FROM publish_stats "
            "WHERE slot_key=? AND channel=? ORDER BY COALESCE(pub_date,'') DESC",
            (slot_key, ch)).fetchall()]
        audits = [dict(r) for r in con.execute(
            "SELECT action, ref_id, channel, note, at FROM plan_publish_audit "
            "WHERE slot_key=? ORDER BY id", (slot_key,)).fetchall()]
        flag = con.execute("SELECT * FROM plan_slot_flags WHERE slot_key=?",
                           (slot_key,)).fetchone()
    except sqlite3.OperationalError:
        # 老库还没有 publish_stats（第一次跑测试库时会这样）——按"没发过"处理，不整个报错
        rows, audits, flag = [], [], None
    finally:
        con.close()
    # 撤销是按 (槽号, 键) 记的；审计里渠道为空的老行按"就是这一渠道"处理，别把它当没撤销
    revoked = {a["ref_id"] for a in audits if a["action"] == "revoke"
               and (a.get("channel") or ch) == ch}
    live = [r for r in rows if r["ref_id"] not in revoked]
    out = {"state": "none", "rows": live, "revoked": len(rows) - len(live), "note": "",
           "channel": ch, "flag": None}
    if live:
        r = live[0]
        out["state"] = "confirmed"
        out["note"] = "已登记 %s（%s《%s》）" % (r.get("pub_date") or "-", r.get("channel") or "-",
                                                (r.get("title") or "")[:22])
    # 判"待核对"用的是 IFNULL：老库里 resolved_at 可能是 NULL 而不是空串，
    # 拿 `resolved_at IS ''` 去比会永远不成立，那条旧勾选就再也解不掉了。
    # **旧浏览器的本地勾选只属于头条那一路**（`plan_slot_flags` 没有渠道键，页面也只有
    # 头条的 schedule 在上报）。让公众号也读这张表，就会出现"头条勾过 → 公众号显示待核对、
    # 且跟着不匹配"这种串渠道（Codex 二次复验的边界条）。将来两渠道都要迁旧勾选，
    # 再给这张表补渠道键，而不是现在拿它当通用事实。
    if (ch == DEFAULT_CHANNEL and flag is not None and not flag["resolved_at"]
            and out["state"] != "confirmed"):
        out["flag"] = dict(flag)
        out["state"] = "pending_review"
        out["note"] = "这台浏览器勾过「已发布」，但服务端查无登记 —— 请你确认：真发过就去登记，没发过就取消勾选"
    return out


def _live_ref_id(con, channel: str, slot_key: str, title: str, pub_date: str) -> str:
    """同槽、同渠道、同标题、同日期且**没被撤销过**的那一行 → 复用它的键。

    只在这里查"复用"，不查"别的槽有没有同题同日"：ops_store 那侧的复用判据是
    渠道+标题+日期（那是它自己的历史业务语义，别的调用方要靠它），而排产这一格要的是
    **槽号级的独立**——不然给 s1a1、s1a2 登记同一篇同名同日的稿，第二笔会把第一笔那行
    原地改成自己的槽号，第一格反而变回"没发过"（Codex 二次复验第 1 条实测到的就是这个）。
    """
    revoked = {r[0] for r in con.execute(
        "SELECT ref_id FROM plan_publish_audit WHERE action='revoke' AND slot_key=?"
        " AND (IFNULL(channel,'')=? OR IFNULL(channel,'')='')", (slot_key, channel))}
    for r in con.execute("SELECT ref_id FROM publish_stats WHERE channel=? AND slot_key=?"
                         " AND title=? AND pub_date=?", (channel, slot_key, title, pub_date)):
        if r[0] not in revoked:
            return r[0]
    return ""


def _new_ref_id(con, channel: str, slot_key: str, stamp: str) -> str:
    """键里带槽号：同槽双击撞秒也不会互相吞行，不同槽天生不同键。"""
    base = "plan:%s:%s" % (slot_key, stamp)
    taken = {r[0] for r in con.execute("SELECT ref_id FROM publish_stats WHERE channel=?",
                                       (channel,))}
    ref, n = base, 1
    while ref in taken:
        n += 1
        ref = "%s-%d" % (base, n)
    return ref


def register_slot(db_path, slot_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """把「已发布」这一勾落到台账上（复用 ops_store.register_publish，不另造一份发布真相）。

    `by` 只进审计，不进台账行——台账那一行是发布事实，谁点的属于操作记录。
    与通用登记口的两点差别（都在 e2e 的 M1 里有反例）：
      · **ref_id 由这里算**，判据是 (渠道, 槽号, 标题, 日期)，别的槽抢不走这一行；
      · 撤销之后拿相同资料重登，走的是**新键**（撤销的那条审计还指着老键），
        所以这一格能干净地回到"已发布"，而不是显示成"撤销过"。
    """
    from . import ops_store
    if not valid_slot(slot_key):
        raise ValueError("槽号只能用字母/数字/-/_且不超过 %d 位，收到「%s」" % (SLOT_KEY_MAX, slot_key))
    body = dict(payload or {})
    channel = norm_channel(body.get("channel"))
    body["channel"] = channel
    body["slot_key"] = slot_key
    title = str(body.get("title") or "").strip()
    if not title:
        raise ValueError("标题不能空——台账里没有标题的行没法复盘")
    # 发布日期没填按**北京时间**今天算，不让本机时区当准（跟夜间那颗钟同一口径）
    pub_date = str(body.get("pub_date") or "").strip()[:10] or cn_now().date().isoformat()
    body["pub_date"] = pub_date
    if not str(body.get("url") or "").strip():
        why = str(body.get("url_missing_reason") or "").strip()
        body["note"] = ("链接待补：%s" % why)[:200] if why else "链接待补：登记时没填链接，也没写原因"
    ops_store.ensure(str(db_path))
    ensure(db_path)
    con = sqlite3.connect(str(db_path))
    try:
        ref = _live_ref_id(con, channel, slot_key, title, pub_date)
        if not ref:
            ref = _new_ref_id(con, channel, slot_key,
                              cn_now().strftime("%Y%m%d%H%M%S"))
    finally:
        con.close()
    body["ref_id"] = ref
    out = ops_store.register_publish(str(db_path), body)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO plan_publish_audit (slot_key, channel, ref_id, action, title,"
            " pub_date, url, note, at) VALUES (?,?,?,?,?,?,?,?,?)",
            (slot_key, out["row"].get("channel") or channel, out.get("ref_id") or "",
             "register", out["row"].get("title") or "", out["row"].get("pub_date") or "",
             out["row"].get("url") or "", str(body.get("by") or "")[:60], _now()))
        # 登记成功即等于"这条旧本地勾选已经核对完"，顺手解掉，省得下次还标待核对
        con.execute("UPDATE plan_slot_flags SET resolved_at=?, resolved_by=? WHERE slot_key=?"
                    " AND IFNULL(resolved_at,'')=''", (_now(), "登记核对", slot_key))
        con.commit()
    finally:
        con.close()
    return out


def revoke_slot(db_path, slot_key: str, ref_id: str, reason: str, by: str = "",
                channel: str = DEFAULT_CHANNEL) -> Dict[str, Any]:
    """撤销误登记：**只追加一条审计**，台账那行原样留着（删了就没人能回查当初登记了什么）。

    撤销之后 `published_state` 立刻回到未发布，夜间匹配重新覆盖这一格——
    这是任务书要的"不允许只取消前端勾选而服务端仍算已发"的反向那一半。
    键在两个渠道间可重复（`publish_stats` 的主键是 (渠道, ref_id)），所以查行也要带渠道。
    """
    ensure(db_path)
    ch = norm_channel(channel)
    if not str(reason or "").strip():
        raise ValueError("撤销要写原因：这一条为什么不算已发布（否则下次没人敢判断）")
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT channel, title, pub_date, url FROM publish_stats "
                          "WHERE slot_key=? AND ref_id=? AND channel=?",
                          (slot_key, ref_id, ch)).fetchone()
        if row is None:
            raise ValueError("台账里查不到这条登记（%s / 槽号 %s / 键 %s），没敢撤销"
                             % (ch, slot_key, ref_id))
        con.execute(
            "INSERT INTO plan_publish_audit (slot_key, channel, ref_id, action, title,"
            " pub_date, url, note, at) VALUES (?,?,?,?,?,?,?,?,?)",
            (slot_key, row["channel"] or ch, ref_id, "revoke", row["title"] or "",
             row["pub_date"] or "", row["url"] or "",
             ("原因：%s｜影响：这一格重新算未发布，夜间匹配会再给它出候选；由 %s 操作"
              % (str(reason)[:120], by or "未署名"))[:300], _now()))
        con.commit()
    finally:
        con.close()
    return published_state(db_path, slot_key, channel=ch)


def slot_of_summary(db_path, summary_id) -> str:
    """这篇整理稿挂在哪个排产槽上（`plan_tasks` 里最新那一版）。查不到就是独立稿，回 ''。

    为什么海报那边要先问这一句：登记必须写进**同一本**台账，不能因为入口不同长出第二份发布真相。
    有槽号就交给 `register_slot`（排产表那一格会同步变已发布、夜间匹配也不再给它出候选），
    没槽号才记成独立稿行。
    """
    try:
        sid = int(summary_id or 0)
    except (TypeError, ValueError):
        return ""
    if not sid:
        return ""
    ensure(db_path)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        if not _has_table(con, "plan_tasks"):
            return ""
        row = con.execute("SELECT slot_key FROM plan_tasks WHERE summary_id=?"
                          " AND IFNULL(slot_key,'')<>''"
                          " ORDER BY version DESC, id DESC LIMIT 1", (sid,)).fetchone()
        return (dict(row or {}).get("slot_key") or "")
    except sqlite3.Error:
        return ""
    finally:
        con.close()


def _sum_ref_id(con, summary_id: int, channel: str) -> str:
    """独立稿的台账键：`sum:<稿id>:<渠道>:<时间戳>`。

    为什么不复用 `_new_ref_id`：那把键固定以 `plan:` 开头（排产槽用的），
    而这边要靠 `sum:<稿id>:` 这个前缀把"这一篇自己的行"读回来——前缀混了就读不到，
    更糟的是可能读到别人的行。
    """
    taken = {r[0] for r in con.execute("SELECT ref_id FROM publish_stats WHERE channel=?",
                                       (channel,))}
    n = 0
    while True:
        ref = "sum:%d:%s:%s%s" % (int(summary_id), channel,
                                  cn_now().strftime("%Y%m%d%H%M%S"),
                                  ("-%d" % n) if n else "")
        if ref not in taken:
            return ref
        n += 1


def poster_publish_state(db_path, summary_id, channel: str = DEFAULT_CHANNEL) -> Dict[str, Any]:
    """这篇稿在**这一个渠道**算不算已发布（海报弹窗与成品页读这一份，不自己判）。"""
    slot = slot_of_summary(db_path, summary_id)
    if slot:
        st = published_state(db_path, slot, channel=channel)
        st["slot_key"] = slot
        return st
    ch = norm_channel(channel)
    ensure(db_path)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT channel, ref_id, title, pub_date, url, IFNULL(slot_key,'') AS slot_key "
            "FROM publish_stats WHERE channel=? AND IFNULL(slot_key,'')='' AND ref_id LIKE ? ORDER BY pub_date DESC",
            (ch, "sum:%d:%%" % int(summary_id or 0))).fetchall()]
        revoked = {r["ref_id"] for r in con.execute(
            "SELECT ref_id FROM plan_publish_audit WHERE action='revoke'"
            " AND channel=? AND ref_id LIKE ?", (ch, "sum:%d:%%" % int(summary_id or 0))).fetchall()}
        # 撤销过的键要认"那一次登记已作废"：同一条 ref_id 被 revoke 指着就不算已发布
        live = [r for r in rows if r["ref_id"] not in revoked]
    except sqlite3.Error:
        rows, live = [], []
    finally:
        con.close()
    # 返回形状与 `published_state` 一致（rows/note/state/channel），前端只认一份
    out = {"slot_key": "", "state": "none", "rows": live, "revoked": len(rows) - len(live),
           "note": "", "channel": ch, "flag": None}
    if live:
        r = live[0]
        out["state"] = "confirmed"
        out["note"] = "已登记 %s（%s《%s》）" % (r.get("pub_date") or "-", r.get("channel") or "-",
                                                (r.get("title") or "")[:22])
    return out


def poster_publish(db_path, summary_id, payload: Dict[str, Any]) -> Dict[str, Any]:
    """海报这边点「登记已发布」：写的是排产那本台账，**不另造一份发布真相**。

    有槽号 → 直接交给 `register_slot`，于是排产表那一格同时变「已发布」（一条流程不用两边跑）；
    没槽号（独立稿、老素材直接起的稿）→ 记一行 `slot_key=''` 的台账，键里带稿 id，
    运营页读同一张表所以立刻看得到，撤销也只追加审计、不删行。
    校验（标题必填、日期格式、没链接要写原因）全在 `ops_store.register_publish` 那一份里，这里不抄。
    """
    from . import ops_store
    ch = norm_channel((payload or {}).get("channel") or DEFAULT_CHANNEL)
    slot = slot_of_summary(db_path, summary_id)
    body = dict(payload or {})
    body["channel"] = ch
    body["by"] = str(body.get("by") or "AI 海报")
    if slot:
        out = register_slot(db_path, slot, body)
        out["slot_key"] = slot
        out["state"] = poster_publish_state(db_path, summary_id, channel=ch)
        return out
    title = str(body.get("title") or "").strip()
    if not title:
        raise ValueError("标题不能空——台账里没有标题的行没法复盘")
    pub_date = str(body.get("pub_date") or "").strip()[:10] or cn_now().date().isoformat()
    body["pub_date"] = pub_date
    if not str(body.get("url") or "").strip():
        why = str(body.get("url_missing_reason") or "").strip()
        body["note"] = ("链接待补：%s" % why)[:200] if why else "链接待补：海报里登记时没填链接"
    ops_store.ensure(str(db_path))
    ensure(db_path)
    con = sqlite3.connect(str(db_path))
    try:
        body["ref_id"] = _sum_ref_id(con, int(summary_id), ch)
    finally:
        con.close()
    body["slot_key"] = ""
    out = ops_store.register_publish(str(db_path), body)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("INSERT INTO plan_publish_audit (slot_key, channel, ref_id, action, title,"
                    " pub_date, url, note, at) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("", ch, out.get("ref_id") or "", "register",
                     (out.get("row") or {}).get("title") or "",
                     (out.get("row") or {}).get("pub_date") or "",
                     (out.get("row") or {}).get("url") or "",
                     "整理稿 %s｜由 %s 操作" % (int(summary_id or 0), body["by"])[:300], _now()))
        con.commit()
    finally:
        con.close()
    out["slot_key"] = ""
    out["state"] = poster_publish_state(db_path, summary_id, channel=ch)
    return out


def poster_revoke(db_path, summary_id, ref_id: str, reason: str, by: str = "",
                  channel: str = DEFAULT_CHANNEL) -> Dict[str, Any]:
    """撤销这条登记：**只追加审计，台账那行留着**（删了没人能回查当初登记了什么）。"""
    ch = norm_channel(channel)
    slot = slot_of_summary(db_path, summary_id)
    if slot:
        return revoke_slot(db_path, slot, ref_id, reason, by, channel=ch)
    if not str(reason or "").strip():
        raise ValueError("撤销要写原因：这一条为什么不算已发布（否则下次没人敢判断）")
    ensure(db_path)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT channel, title, pub_date, url FROM publish_stats "
                          "WHERE ref_id=? AND channel=? AND IFNULL(slot_key,'')=''",
                          (ref_id, ch)).fetchone()
        if row is None:
            raise ValueError("台账里查不到这条登记（渠道 %s / 键 %s），没敢撤销" % (ch, ref_id))
        con.execute("INSERT INTO plan_publish_audit (slot_key, channel, ref_id, action, title,"
                    " pub_date, url, note, at) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("", row["channel"] or ch, ref_id, "revoke", row["title"] or "",
                     row["pub_date"] or "", row["url"] or "",
                     ("原因：%s｜影响：这篇重新算未发布；由 %s 操作"
                      % (str(reason)[:120], by or "未署名"))[:300], _now()))
        con.commit()
    finally:
        con.close()
    return poster_publish_state(db_path, summary_id, channel=ch)


def report_local(db_path, slot_key: str, local_published: bool, note: str = "") -> Dict[str, Any]:
    """页面把它那份旧的 localStorage 勾选上报一次（只上报，不覆盖服务端事实）。"""
    ensure(db_path)
    if not valid_slot(slot_key):
        return {"ok": False, "reason": "槽号不合法"}
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.execute("SELECT * FROM plan_slot_flags WHERE slot_key=?", (slot_key,))
        if cur.fetchone():
            con.execute("UPDATE plan_slot_flags SET local_published=?, note=?, seen_at=?"
                        " WHERE slot_key=?", (1 if local_published else 0, note[:200], _now(), slot_key))
        else:
            con.execute("INSERT INTO plan_slot_flags (slot_key, local_published, note, seen_at)"
                        " VALUES (?,?,?,?)", (slot_key, 1 if local_published else 0, note[:200], _now()))
        con.commit()
    finally:
        con.close()
    return {"ok": True}


def resolve_local(db_path, slot_key: str, verdict: str, by: str = "") -> Dict[str, Any]:
    """人核对完旧勾选：`published`（其实发过，但没登记）或 `not_published`（勾错了）。

    两种都**不自动写台账**——发过就得由人去运营页补登记，那才是发布事实的来源。
    """
    ensure(db_path)
    if verdict not in ("published", "not_published"):
        raise ValueError("verdict 只能是 published / not_published")
    con = sqlite3.connect(str(db_path))
    # 结论先拼成变量再进参数元组：以前直接写在 `% (…)` 里，把后面的 SQL 参数一起吃掉了
    # （报"not enough arguments for format string"，实测撞在 resolve_local 上）。
    verdict_txt = ("确已发布但没登记，请去运营页补登记" if verdict == "published"
                   else "没发过，取消旧勾选，恢复夜间匹配")
    note = "｜核对结论：%s（%s）" % (verdict_txt, str(by or "")[:20])
    try:
        n = con.execute(
            "UPDATE plan_slot_flags SET resolved_at=?, resolved_by=?, note=note||? WHERE slot_key=?"
            " AND IFNULL(resolved_at,'')=''",
            (_now(), by or "人工核对", note, slot_key)).rowcount
        con.commit()
    finally:
        con.close()
    return {"resolved": n, "slot_key": slot_key}


def open_flags(db_path) -> List[Dict[str, Any]]:
    ensure(db_path)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM plan_slot_flags WHERE IFNULL(resolved_at,'')=''"
            " ORDER BY seen_at").fetchall()]
    finally:
        con.close()


# ── 版面/图文包状态：只读 manifest，不写任何东西 ─────────────────────────────
def poster_dir():
    """成品目录取 `ai_poster.OUTPUT_DIR` 那一份，别在这里再拼一次路径。"""
    try:
        from . import ai_poster
        return Path(str(ai_poster.OUTPUT_DIR))
    except Exception:
        return ROOT / "output" / "poster"


def poster_state(summary_id, base=None) -> Dict[str, Any]:
    """这篇稿出过图文包没有、过没过质量门。读不到就是没出，不猜。"""
    if not summary_id:
        return {"exists": False, "publishable": False, "pages": 0, "reasons": []}
    d = (Path(str(base)) if base else poster_dir()) / str(int(summary_id))
    mf = d / "manifest.json"
    if not mf.is_file():
        return {"exists": False, "publishable": False, "pages": 0, "reasons": []}
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        return {"exists": True, "publishable": False, "pages": 0,
                "reasons": ["成品清单读不懂：%s" % str(e)[:60]]}
    errs = [x for x in (m.get("lint") or []) if str(x).startswith("error")]
    return {"exists": True, "publishable": bool(m.get("publishable")),
            # 编辑证据链缺登记：10-23 起单独一旗，不再并进 publishable
            "needs_evidence": bool(m.get("needs_evidence")),
            "pages": len([x for x in d.glob("poster_p*.png")]) or (1 if (d / "poster.png").exists() else 0),
            "reasons": errs[:4], "dir": str(d)}


# ── 看板：每个位置现在走到哪一档 ───────────────────────────────────────────
def board(db_path, today=None, base=None, channel: str = DEFAULT_CHANNEL) -> Dict[str, Any]:
    """两个系列 + 独立篇 + 旧题的整块看板。判据全在这里，页面只上色。

    `channel` 决定"发过没有"读哪一路台账：这块看板默认是**头条线**，
    公众号那边登记的同一张位置不算它已发（第 2 条反例）。
    """
    ensure(db_path)
    ch = norm_channel(channel)
    now0 = cn_now(today) if isinstance(today, dt.datetime) else cn_now()
    today = now0.date() if isinstance(today, dt.datetime) else (today or now0.date())
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        slots = [_slot_dict(r) for r in con.execute(
            "SELECT * FROM plan_slots ORDER BY series, position").fetchall()]
        tasks = {}
        for r in con.execute("SELECT * FROM plan_tasks ORDER BY version DESC").fetchall() \
                if _has_table(con, "plan_tasks") else []:
            d = dict(r)
            # 同一槽号可能双渠道各一版；这块看板只管交来的那一路渠道（另一路不混）
            if d["channel"] != ch:
                continue
            ids = _j(d.get("video_ids"), [])
            d["video_ids"] = ids
            # 子集里勾的那几条同样是 JSON 文本：不解析的话下发到页面是字符串 '[]'，
            # 页面拿它 forEach 会当场炸（看板整块渲染不出来）
            d["supplement_ids"] = _j(d.get("supplement_ids"), [])
            tasks.setdefault(d["slot_key"], d)      # version DESC，第一条就是当前版
        cands = {}
        if _has_table(con, "plan_candidates"):
            for r in con.execute("SELECT * FROM plan_candidates ORDER BY run_date DESC,"
                                 " last_seen DESC, id DESC").fetchall():
                cands.setdefault(r["slot_key"], []).append(dict(r))
        flags = {}
        if _has_table(con, "plan_slot_flags"):
            for r in con.execute("SELECT * FROM plan_slot_flags").fetchall():
                if not r["resolved_at"]:
                    flags[r["slot_key"]] = dict(r)
        pub_rows = {}
        if _has_table(con, "publish_stats"):
            cols = {c[1] for c in con.execute("PRAGMA table_info(publish_stats)")}
            if "slot_key" in cols:
                for r in con.execute("SELECT channel, ref_id, title, pub_date, url, slot_key"
                                     " FROM publish_stats WHERE COALESCE(slot_key,'')<>''"
                                     " AND channel=? ORDER BY COALESCE(pub_date,'') DESC",
                                     (ch,)).fetchall():
                    pub_rows.setdefault(r["slot_key"], []).append(dict(r))
        revoked = set()
        if _has_table(con, "plan_publish_audit"):
            for r in con.execute("SELECT slot_key, ref_id FROM plan_publish_audit"
                                 " WHERE action='revoke' AND (IFNULL(channel,'')=?"
                                 " OR IFNULL(channel,'')='')", (ch,)).fetchall():
                revoked.add((r["slot_key"], r["ref_id"]))
        # 候选行本身只存了 video_id 与理由，界面上要念得出"这是哪一条"——
        # 不补标题，排产页那一格就是一片「（无标题）」，人根本没法挑（接上数据源 ≠ 看得见）。
        vinfo = {}
        want_ids = sorted({int(c["video_id"]) for k in cands for c in cands[k]}
                          # 已采用那几条也要念得出标题：排产页把它们当"父行"渲染，
                          # 下面的「相关非视频」子集得跟着它们走（采用之后还能继续勾补充料）
                          | {int(x) for t in tasks.values() for x in (t.get("video_ids") or [])})
        if want_ids and _has_table(con, "videos"):
            for i in range(0, len(want_ids), 400):     # 分块：一次几千个 IN 参数会顶到 SQLite 上限
                chunk = want_ids[i:i + 400]
                for r in con.execute(
                        "SELECT id, title, author, published_at, source_sheet, source_row"
                        " FROM videos WHERE id IN (%s)" % ",".join("?" * len(chunk)),
                        chunk).fetchall():
                    vinfo[int(r["id"])] = dict(r)
    finally:
        con.close()

    out = []
    for s in slots:
        key = s["slot_key"]
        task = tasks.get(key) or {}
        adopted = [int(x) for x in (task.get("video_ids") or [])]
        all_c = cands.get(key) or []
        cand = []
        for c in all_c:
            if int(c["video_id"]) in adopted:
                continue
            info = vinfo.get(int(c["video_id"])) or {}
            c = dict(c)
            c["title"] = info.get("title") or ""
            c["author"] = info.get("author") or ""
            c["date"] = (info.get("published_at") or "")[:10]
            c["sheet"] = info.get("source_sheet") or ""
            c["row"] = str(info.get("source_row") or "")
            cand.append(c)
        live_pub = [p for p in (pub_rows.get(key) or [])
                    if (key, p["ref_id"]) not in revoked]
        flag = flags.get(key)
        stage, extra = _stage(task, cand, live_pub, base)
        s.update({"stage": stage, "stage_label": STAGE_LABEL[stage], "stage_why": extra or
                  STAGE_WHY[stage], "task": {k: task.get(k) for k in
                                             ("id", "status", "version", "summary_id", "form",
                                              "title", "error", "video_ids",
                                              # 子集里勾的那几条要回读得到，否则刷新一次界面就说瞎话
                                              "supplement_ids")} if task else None,
                  # S2 那三行是**章节模板**（origin='map'）：它只当线索池，不能直接采用成稿——
                  # 一篇 O1 得由真实公告建出来（`s2o1-1`），采用落在那一篇上。
                  # 判据写在这里一份，界面与接口都不许再各推一遍。
                  "adoptable": not (s["series"] == "s2" and s["origin"] == "map"),
                  "adopted_ids": adopted, "candidates": cand, "candidate_total": len(cand),
                  # 已采用那几条的行数据：排产页拿它把"父行"画出来（含 sheet/row 才能跳详情、
                  # 才能按表判"这条是视频素材、下面挂子集"）
                  "adopted_rows": [{"video_id": i,
                                    "title": (vinfo.get(i) or {}).get("title") or "",
                                    "author": (vinfo.get(i) or {}).get("author") or "",
                                    "date": ((vinfo.get(i) or {}).get("published_at") or "")[:10],
                                    "sheet": (vinfo.get(i) or {}).get("source_sheet") or "",
                                    "row": str((vinfo.get(i) or {}).get("source_row") or "")}
                                   for i in adopted],
                  "published": bool(live_pub),
                  # 撤销要指到具体那一行，所以把 ref_id/日期/链接一起给（只读，不在这改台账）
                  "publish_rows": [{"ref_id": r["ref_id"], "channel": r.get("channel") or "",
                                    "title": r.get("title") or "", "pub_date": r.get("pub_date") or "",
                                    "url": r.get("url") or ""} for r in live_pub],
                  "publish_note": ("已登记 %s《%s》" % (live_pub[0].get("pub_date") or "-",
                                                       (live_pub[0].get("title") or "")[:20])
                                   if live_pub else ""),
                  "pending_review": bool(flag and not live_pub),
                  "review_note": (flag or {}).get("note") or "",
                  # 关联稿自己的身份不动，这里只列出"挂在我这个位置下的稿件"（第 4 条反例）
                  "parent_slot": s.get("parent_slot") or "", "linked_slots": []})
        out.append(s)
    # 关联关系在算完状态之后再挂：子稿的档位/发布情况要用上面那份结果，不能拿原始行糊上去
    by_key = {s["slot_key"]: s for s in out}
    kids = {}
    for s in out:
        pv = s.get("parent_slot") or ""
        if pv and pv != s["slot_key"] and pv in by_key:
            kids.setdefault(pv, []).append({
                "slot_key": s["slot_key"], "title": s.get("title") or s["slot_key"],
                "series": s["series"], "stage": s["stage"],
                "stage_label": s["stage_label"], "published": s["published"],
                "has_task": bool(s.get("task"))})
    for s in out:
        s["linked_slots"] = kids.get(s["slot_key"], [])
    runs = _runs(db_path, limit=5)
    return {"today": today.isoformat(), "generated_at": _now(), "slots": out,
            "channel": ch,
            "cfg": load_cfg(), "runs": runs,
            # 状态栏（下次几点、上次成没成、今晚该不该跑）跟着看板一次给完：
            # 页面分两个请求拼一份状态，迟早出现"徽章是新的、时刻是旧的"
            "status": status_line(db_path, today=now0), 
            "chapter_labels": {k: v[0] for k, v in _CHAPTERS.items()},
            "stages": [{"key": k, "label": v, "why": w} for k, v, w in STAGES],
            "series_labels": SERIES_LABEL,
            "counts": {k: len([s for s in out if s["stage"] == k]) for k, _v, _w in STAGES}}


def _stage(task, cand, live_pub, base=None) -> Tuple[str, str]:
    """取**最远**那一档。整理完成 `done` 绝不冒充已发布——那正是这一单要拆开的两件事。"""
    if live_pub:
        return "published", ""
    if task.get("status") == "running":
        return "drafting", ""
    sid = task.get("summary_id")
    if sid:
        ps = poster_state(sid, base=base)
        if not ps["exists"]:
            return "draft_done", ""
        if ps["publishable"]:
            if ps.get("needs_evidence"):
                # 号主 10-23 拍板：证据链缺登记不再拦发布，但这句必须跟着走，
                # 否则看板等于替他忘了"这三行还没写"
                return "ready", ("图文包能发（编辑证据链缺登记不拦发布），但那三行与逐张画面依据"
                                 "还没写，发前自己补")
            return "ready", "图文包质量门全过，等人去平台发（发完记得登记）"
        # 整条 error 原样带一句（含"编辑证据链/行动依据"这些关键词），别为了短把判据截没了
        why = "；".join(x[len("error: "):][:70] for x in ps["reasons"][:2])
        return "layout", "版面已出，质量门没过：%s" % (why or "见成品清单")
    if task.get("video_ids"):
        return "adopted", ""
    if cand:
        return "candidates", ""
    return "unmatched", ""


def _has_table(con, name) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (name,)).fetchone())


def _j(text, default):
    try:
        v = json.loads(text or "")
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def _day_ordinal(raw) -> int:
    """日期串 → 可比较的整数（越大越新）。读不懂的按 0 处理，也就是排在最后——
    没日期的素材可以当线索，但不许挤掉有日期的新料。"""
    try:
        return dt.date.fromisoformat(str(raw or "")[:10]).toordinal()
    except ValueError:
        return 0


# ── 夜间匹配：素材库只读检索 + 可解释排序，永不写别的表 ────────────────────
# 判 Forever 归属的三条线（任务书 §4 点名：来源只是 blizzard、或标题只含"无限"，都不足以判）：
#   强 = 标题/正文里出现「无限服」或「Forever」 → yes
#   弱 = 只有"无限/时光服/怀旧"这类词，或只因为来源是暴雪官方 → unknown（标"待人工判定"）
#   否 = 命中别的游戏名单 → 直接不进候选（跨服假阳性是这一单的反例项）
_STRONG = re.compile(r"无限服|Forever", re.I)
_WEAK = re.compile(r"无限|时光服|怀旧|艾泽拉斯|暴雪")
_OFFGAME = re.compile(r"流放|POE|暗黑|Diablo|蜘蛛侠|原神|王者|火影|明日方舟|蛋仔|苹果|iPhone|华为", re.I)


def forever_call(title: str, author: str, body: str, sheet: str) -> Tuple[str, str]:
    hay = "%s %s" % (title or "", body or "")
    if _OFFGAME.search("%s %s" % (title or "", author or "")):
        return "off", "标题/作者命中其他游戏名单（跨服或跨游戏，不进候选）"
    m = _STRONG.search(hay)
    if m:
        return "yes", "正文/标题出现「%s」" % m.group(0)
    if sheet in ("blizzard", "暴雪官方"):
        # 库里存的值是表名「暴雪官方」，"blizzard" 是配置/采集侧那个源名——两个口径都得认，
        # 否则这一支永远不命中，暴雪公告的理由会被写成泛泛的「只有『暴雪』这类弱线索」。
        return "unknown", "来源是暴雪官方公告，但正文没写清是不是无限服 → 待人工判定"
    m2 = _WEAK.search(hay)
    if m2:
        return "unknown", "只有「%s」这类弱线索，判不了 Forever 归属 → 待人工判定" % m2.group(0)
    return "unknown", "正文没有可判归属的字样 → 待人工判定"


def _match_terms(kws: List[str], hay_title: str, hay_author: str, hay_body: str) -> List[str]:
    hit = []
    for k in kws or []:
        s = str(k).strip()
        if not s:
            continue
        # 多词检索（"画面 设置"）按"每个词都在"判，比整串 LIKE 更接近人的搜索意图
        parts = [p for p in s.split() if p]
        def _in(part, blob):
            return part.lower() in blob.lower()
        ok = all((_in(p, hay_title) or _in(p, hay_author) or _in(p, hay_body)) for p in parts)
        if ok:
            hit.append(s)
    return hit


def match_positions(db_path, now=None, only_slots=None, cap: int = CAND_CAP,
                    channel: str = DEFAULT_CHANNEL) -> Dict[str, Any]:
    """给**未发布**的位置刷候选。返回 {added, skipped, positions, detail}。

    三条硬规矩（都在 e2e 里有反例）：
      · 已发布、以及"待核对旧勾选"的那一格 → 跳过，不刷；"已发布"按 **(槽号, 渠道)** 判，
        公众号发过不算头条发过（第 2 条反例）；
      · 只写 plan_candidates，且 `ON CONFLICT DO UPDATE` 只更新 last_seen → 重复跑不长重复候选；
      · 一条都不自动采用：`plan_tasks` 的 video_ids/sources 一个字节都不动。
    """
    ensure(db_path)
    now = cn_now(now)
    ch = norm_channel(channel)
    day = now.date().isoformat()
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    stats = {"added": 0, "seen": 0, "skipped": [], "positions": 0, "empty": 0, "detail": []}
    try:
        slots = [_slot_dict(r) for r in con.execute(
            "SELECT * FROM plan_slots ORDER BY position").fetchall()]
        if only_slots:
            want = set(only_slots)
            slots = [s for s in slots if s["slot_key"] in want]
        published = {}
        if _has_table(con, "publish_stats"):
            cols = {c[1] for c in con.execute("PRAGMA table_info(publish_stats)")}
            if "slot_key" in cols:
                for r in con.execute("SELECT slot_key, ref_id FROM publish_stats "
                                     "WHERE COALESCE(slot_key,'')<>'' AND channel=?",
                                     (ch,)).fetchall():
                    published.setdefault(r["slot_key"], set()).add(r["ref_id"])
        if _has_table(con, "plan_publish_audit"):
            for r in con.execute("SELECT slot_key, ref_id FROM plan_publish_audit"
                                 " WHERE action='revoke' AND (IFNULL(channel,'')=?"
                                 " OR IFNULL(channel,'')='')", (ch,)).fetchall():
                # 撤销过的那一条不再算已发：摘掉（判据与 published_state 同一份，别两处各写一遍）
                published.get(r["slot_key"], set()).discard(r["ref_id"])
        pending = {r["slot_key"] for r in con.execute(
            "SELECT slot_key FROM plan_slot_flags WHERE IFNULL(resolved_at,'')=''").fetchall()} \
            if (_has_table(con, "plan_slot_flags") and ch == DEFAULT_CHANNEL) else set()
        adopted = {}
        if _has_table(con, "plan_tasks"):
            for r in con.execute("SELECT slot_key, video_ids FROM plan_tasks WHERE channel=?"
                                 " ORDER BY version DESC", (ch,)).fetchall():
                adopted.setdefault(r["slot_key"], set(int(x) for x in _j(r["video_ids"], [])))
        videos = [dict(r) for r in con.execute(
            "SELECT id, source_sheet, source_row, title, author, published_at,"
            " COALESCE(transcript,'') AS tr, category FROM videos").fetchall()]
        for s in slots:
            key = s["slot_key"]
            if published.get(key):
                stats["skipped"].append({"slot_key": key, "why": "已发布，不再匹配"})
                continue
            if key in pending:
                stats["skipped"].append({"slot_key": key, "why": "有旧勾选待核对，保守跳过"})
                continue
            stats["positions"] += 1
            got = []
            for v in videos:
                title = v.get("title") or ""
                author = v.get("author") or ""
                body = (v.get("tr") or "")[:1200]
                kws = _match_terms(s.get("keywords") or [], title, author, body)
                if not kws:
                    continue
                verdict, basis = forever_call(title, author, body, v.get("source_sheet") or "")
                if verdict == "off":
                    continue
                if len(body) < MIN_TL:
                    continue          # 太短的只算线索，不占候选位
                got.append({"video_id": int(v["id"]), "verdict": verdict, "basis": basis,
                            "kws": kws, "date": (v.get("published_at") or "")[:10],
                            "tl": len(body), "title": title[:60],
                            "sheet": v.get("source_sheet") or "", "row": str(v.get("source_row") or "")})
            # 可解释排序：先能确定属 Forever 的、再按日期**新→旧**、再按正文有料。
            # 这一行原来写的是 `(tier, x["date"], x["tl"])` + `reverse=False`：注释说"按日期新"，
            # 实际是**升序**，于是候选超过上限 6 时，留下的正是最旧那几条、新料被挤掉
            # （Codex 二次复验第 5 条后半，e2e 的 M5b 用 8 条不同日期的夹具钉住它）。
            got.sort(key=lambda x: (0 if x["verdict"] == "yes" else 1,
                                   -_day_ordinal(x["date"]), -x["tl"]))
            got = got[:cap]
            if not got:
                stats["empty"] += 1
            for g in got:
                reason = "命中「%s」｜%s｜%s｜正文 %d 字｜%s" % (
                    "、".join(g["kws"])[:30], g["sheet"] or "-", g["date"] or "无日期",
                    g["tl"], g["basis"])
                # 新建还是撞了唯一键，SQLite 两种情况 rowcount 都是 1，拿它计数会让
                # "重复运行不产生重复候选"这条断言永远看着像过（其实数在瞎涨）。
                # 候选量级很小，先查一条换判据干净；唯一键仍然兜住并发。
                have = con.execute("SELECT 1 FROM plan_candidates WHERE slot_key=? AND video_id=?",
                                   (key, g["video_id"])).fetchone()
                if have:
                    con.execute("UPDATE plan_candidates SET last_seen=?, reason=?,"
                                " forever=? WHERE slot_key=? AND video_id=?",
                                (_now(), reason, g["verdict"], key, g["video_id"]))
                    stats["seen"] += 1
                else:
                    con.execute(
                        "INSERT INTO plan_candidates (slot_key, video_id, forever, reason,"
                        " run_date, added_at, last_seen) VALUES (?,?,?,?,?,?,?)",
                        (key, g["video_id"], g["verdict"], reason, day, _now(), _now()))
                    stats["added"] += 1
            stats["detail"].append({"slot_key": key, "n": len(got)})
        con.commit()
    finally:
        con.close()
    stats["skipped_n"] = len(stats["skipped"])
    return stats


def _runs(db_path, limit: int = 5) -> List[Dict[str, Any]]:
    ensure(db_path)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM plan_match_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        con.close()


# ── 运行账：一天一行、抢不到就不跑、失败可重试（幂等就靠这张表）────────────
NIGHT_ATTEMPTS = 3          # 当晚自动重试上限，超过就只留记录等人手动，不再半夜反复敲库
CATCHUP_LOOKBACK = 1        # 补跑只往前看**昨晚**：错过三晚也只补一次，
                            # 第二天早上再看时"昨晚"已经有账了，不会连着几天各补一轮


def night_key(day) -> str:
    """当晚那一行的键。传 date 或 'YYYY-MM-DD' 都行——这两个形状在库里的判据是同一个。"""
    s = day.isoformat() if hasattr(day, "isoformat") else str(day or "")[:10]
    return "night:%s" % s


def claim_run(db_path, run_key: str, kind: str, run_date: str, who: str = "") -> bool:
    """抢这一轮的账。抢不到返回 False——重复触发、重启、接口重试都撞在这一行上。

    两条路径：
      · 新的一天：`INSERT ... ON CONFLICT DO NOTHING`，rowcount=1 才算抢到；
      · 当晚那行已经 failed 且次数没用完：带条件的 UPDATE 抢（`WHERE status='failed'`），
        别人同时抢只有一个 rowcount=1。
    **running 状态的行永远抢不到**：进程被 kill 留下的 running 由 `sweep_stale_runs` 收尾，
    不靠这里放行——放行了就是两个匹配同时写候选。
    """
    ensure(db_path)
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.execute(
            "INSERT INTO plan_match_runs (run_key, kind, run_date, started_at, status,"
            " attempts, claimed_by) VALUES (?,?,?,?, 'running', 1,?)"
            " ON CONFLICT(run_key) DO NOTHING",
            (run_key, kind, run_date, _now(), who[:60]))
        if cur.rowcount:
            con.commit()
            return True
        cur = con.execute(
            "UPDATE plan_match_runs SET status='running', started_at=?, attempts=attempts+1,"
            " claimed_by=? WHERE run_key=? AND status='failed' AND attempts<?",
            (_now(), who[:60], run_key, NIGHT_ATTEMPTS))
        con.commit()
        return bool(cur.rowcount)
    finally:
        con.close()


def finish_run(db_path, run_key: str, ok: bool, stats: Optional[Dict[str, Any]] = None,
               note: str = "") -> None:
    ensure(db_path)
    s = stats or {}
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "UPDATE plan_match_runs SET status=?, finished_at=?, note=?, positions=?, added=?,"
            " skipped=?, seen=? WHERE run_key=?",
            ("ok" if ok else "failed", _now(), note[:400], int(s.get("positions") or 0),
             int(s.get("added") or 0), int(s.get("skipped_n") or 0), int(s.get("seen") or 0),
             run_key))
        con.commit()
    finally:
        con.close()


def sweep_stale_runs(db_path) -> int:
    """进程被 kill 后，账上会永远留一行 running（下一次再也不会跑，界面永远转圈）。

    与 `plan_tasks.ensure_table` 同一套判据：本函数只在"这个库在本进程里第一次被用"时调，
    此刻内存里不可能有匹配线程，所以库里所有 running 都是上个进程留下的 → 标 failed 并写清原因。
    **不自动重跑**：重跑要人看一眼上一次的候选有没有被采纳过。
    """
    ensure(db_path)
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.execute(
            "UPDATE plan_match_runs SET status='failed', finished_at=?, note=? WHERE status='running'",
            (_now(), "上次进程中断时这一轮还在跑，结果没记下来；先看看候选有没有被采用，再手动匹配"))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def due_run(db_path, now=None) -> Dict[str, Any]:
    """今晚该不该跑。返回 {"due","run_key","kind","catchup","why"}。

    补跑只补**一次**：错过三个自然日后，下次启动写的是"今天"那一行（note 里写明覆盖了哪几天），
    所以不会为每个错过的日子连跑多轮——任务书 §4 点名的就是这条。
    """
    cfg = load_cfg()
    now = cn_now(now)
    day = now.date()
    key = night_key(day)
    if not cfg.get("enabled"):
        return {"due": False, "run_key": key, "kind": "night", "catchup": [],
                "why": "总开关关着（配置：%s）" % MATCH_STORE.name}
    ensure(db_path)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM plan_match_runs WHERE run_key=?", (key,)).fetchone()
    finally:
        con.close()
    if row:
        if row["status"] in ("running", "ok"):
            return {"due": False, "run_key": key, "kind": "night", "catchup": [],
                    "why": "今天这一轮%s（%s）" % ("正在跑" if row["status"] == "running" else "已经跑过",
                                                  row["status"])}
        if row["status"] == "failed" and int(row["attempts"]) < NIGHT_ATTEMPTS:
            return {"due": True, "run_key": key, "kind": "night", "catchup": [],
                    "why": "今晚这一轮失败过（已试 %s 次），自动再试一次" % row["attempts"]}
        return {"due": False, "run_key": key, "kind": "night", "catchup": [],
                "why": "今晚失败且自动重试已用满 %d 次 —— 页面上点「现在匹配」手动跑" % NIGHT_ATTEMPTS}
    if now >= _at(cfg["run_at"], day):
        return {"due": True, "run_key": key, "kind": "night", "catchup": [],
                "why": "已过计划时刻 %s" % cfg["run_at"]}
    missed = []
    since = str(cfg.get("since") or "")[:10]
    con = sqlite3.connect(str(db_path))
    try:
        for back in range(1, CATCHUP_LOOKBACK + 1):
            d = day - dt.timedelta(days=back)
            if since and d.isoformat() < since:
                continue          # 这套匹配那天还不存在，谈不上"错过"
            if _at(cfg["run_at"], d) > now:
                continue          # 那天还没到计划时刻，谈不上"错过"（原来拿"同一钟点的那天"比，
                                  # 早上开机时**每一天**都被判成没到点，补跑永远不触发）
            hit = con.execute("SELECT 1 FROM plan_match_runs WHERE run_key=?",
                              (night_key(d),)).fetchone()
            if not hit:
                missed.append(d.isoformat())
    finally:
        con.close()
    if missed:
        return {"due": True, "run_key": key, "kind": "night", "catchup": missed,
                "why": "错过过 %d 个计划时刻（%s…），下次启动补这一次" % (
                    len(missed), missed[0])}
    return {"due": False, "run_key": key, "kind": "night", "catchup": [],
            "why": "没到今晚 %s，也没有错过的账要补" % cfg["run_at"]}


def run_match(db_path, now=None, kind: str = "night", actor: str = "",
              only_slots=None, channel: str = DEFAULT_CHANNEL) -> Dict[str, Any]:
    """跑一轮匹配（含抢账与记账）。抢不到返回 {"skipped":true,...}，不抛。"""
    now = cn_now(now)
    day = now.date().isoformat()
    if kind == "manual":
        key = "manual:%s:%s" % (day, now.strftime("%H%M%S"))
    else:
        key = night_key(day)
    who = "%s/%s" % (kind, actor or "system")
    if not claim_run(db_path, key, kind, day, who):
        return {"skipped": True, "run_key": key,
                "reason": "这一轮已经有人在跑（或今晚已跑过），本轮不重复触发"}
    try:
        st = match_positions(db_path, now, only_slots=only_slots, channel=channel)
        finish_run(db_path, key, True, st, "手动" if kind == "manual" else "定时")
        st.update({"run_key": key, "skipped": st.get("skipped") or []})
        return st
    except Exception as e:
        finish_run(db_path, key, False, {}, "异常：%s" % str(e)[:200])
        raise


def next_run_at(now=None) -> str:
    cfg = load_cfg()
    now = cn_now(now)
    t = _at(cfg["run_at"], now.date())
    if now >= t:
        t = t + dt.timedelta(days=1)
    return t.strftime("%Y-%m-%d %H:%M")


def status_line(db_path, now=None, today=None) -> Dict[str, Any]:
    """给界面那一条状态栏：开关、下次计划、上次结果、这一轮的增减与跳过。

    `today=` 只给 board() 内部复用（看板已经算过一次时间，别再取第二次系统时钟）。
    """
    now = cn_now(now) if now is not None else (today or cn_now())
    cfg = load_cfg()
    runs = _runs(db_path, limit=8)
    last_ok = next((r for r in runs if r["status"] == "ok"), None)
    last_bad = next((r for r in runs if r["status"] == "failed"), None)
    return {"enabled": bool(cfg.get("enabled")), "run_at": cfg.get("run_at"),
            "tz": "Asia/Shanghai（固定 +08:00）", "next": next_run_at(now),
            "last_ok": last_ok, "last_failed": last_bad, "runs": runs,
            "due": due_run(db_path, now), "store": str(MATCH_STORE.name)}
