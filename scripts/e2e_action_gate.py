# -*- coding: utf-8 -*-
"""action_gate 的离线回归：四个反例必须挡、两条正常操作不许误拦、升档要有凭据。

零收费：全程读 `temp/model_swap/` 里已存的四份稿和库里的原 ASR（只读），
不调模型、不写库、不出图。任务书：
`wow-forever-web/docs/PRD-图文攻略模型换档验收与优化-2026-10-22.md` 下一单第 1、2 条。

断言里**不写死档位中文名**（状态词改了就成堆假红），一律比 `action_gate.STATUS_*`。

用法：env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/e2e_action_gate.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = ROOT / "output" / "video2text.db"
SWAP = ROOT / "temp" / "model_swap"
fails = []


def ck(name, cond, detail=""):
    detail = "" if detail == "" else str(detail)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, (" — " + detail) if detail else ""))
    if not cond:
        fails.append(name)


def find(rep, needle):
    """按子串找某条建议的判定结果（找不到返回 None，让断言自己红）。"""
    for r in rep["rows"]:
        if needle in r["text"]:
            return r
    return None


def blocked(rep, needle, why=""):
    r = find(rep, needle)
    ck("%s 被挡在行动/标题前" % needle, r is not None and r["status"] != "可用",
       "" if r else "稿子里没找到这句（夹具变了？）")
    if r is not None and why:
        ck("%s 的阻断理由里写着「%s」" % (needle, why),
           any(why in p for p in r["problems"]), str(r["problems"])[:120])


def main():
    from src import action_gate as ag
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)

    def asr_of(sid):
        row = con.execute("SELECT transcript FROM videos "
                          "WHERE id=(SELECT video_id FROM ai_summaries WHERE id=?)", (sid,)).fetchone()
        return (row[0] or "") if row else ""

    md83 = (SWAP / "new_83.md").read_text(encoding="utf-8")
    md85 = (SWAP / "new_85.md").read_text(encoding="utf-8")
    old83 = (SWAP / "old_83.md").read_text(encoding="utf-8")
    old85 = (SWAP / "old_85.md").read_text(encoding="utf-8")
    a83, a85 = asr_of("83"), asr_of("85")

    print("== A 反例：83 新稿里那些「音没听清/模型补的」选项与数值 ==")
    r83 = ag.check(md83, a83, material_id=83)
    ck("整篇 ready_for_poster=False（未核项没清完就不许进海报）", r83["ready_for_poster"] is False,
       "待核 %d 条" % r83["stats"]["pending"])
    blocked(r83, "低延迟模式", "转写里")           # ASR 只有"第一言是模式关闭"
    blocked(r83, "视野距离", "数值「10」")          # ASR 是"事业距离我们来到时"，10 是猜的
    blocked(r83, "地表景观", "转写里查无")          # ASR 写"地表景光5"
    blocked(r83, "垂直同步", "含混")               # ASR 写"把锤值同步关闭"
    blocked(r83, "次表面光照", "含混")             # ASR 写"刺右光照"
    blocked(r83, "材质投射", "含混")               # ASR 写"才至投射"，指令清楚但名称没落地
    ck("83 标题里的「7步/6项」与行动表条数对不上也要点名",
       any("标题写" in p for r in r83["rows"] for p in r["problems"]),
       str([r["text"] for r in r83["rows"] if r["where"] == "标题候选"])[:90])

    print("== B 正例：ASR 逐字清楚的步骤不许误拦（不能全拦，全拦闸口就会被关掉） ==")
    # 正例是 ASR 里逐字在的：SSAO关闭、ESC→设置→图形（ASR 确实有 ESC 三个字母）。
    # 「抗锯齿…CMAA2」故意不放进来——ASR 写的是 CMA2，专名对不上，这一条**该挡**（见下面单独断言）。
    for ok in ("SSAO：关闭", "ESC→设置→图形", "三重缓冲：关闭"):
        row = find(r83, ok)
        ck("「%s」判为可用、没被误伤" % ok, row is not None and row["status"] == ag.STATUS_OK,
           "" if row else "没找到这条")
    ck("可用条数不是 0（说明判据不是「一律拦下」）", r83["stats"]["ok"] > 0, "可用 %d 条" % r83["stats"]["ok"])
    blocked(r83, "CMAA2", "专名「CMAA2」转写里查无")
    ck("ASR 写 CMA2、稿子写 CMAA2 → 挡（专名不许由模型替游戏补标准写法）",
       any("CMAA2" in p for p in (find(r83, "CMAA2") or {}).get("problems", [])))

    print("== C 反例：85 的 5%/20%——ASR 里确实有，但风险节自己说是举例 ==")
    r85 = ag.check(md85, a85, material_id=85)
    ck("85 整篇 ready_for_poster=False", r85["ready_for_poster"] is False,
       "待核 %d 条" % r85["stats"]["pending"])
    # 拦的理由是"风险节自己说这是口播例子/未给官方口径"，不是"ASR 里没有"——
    # 措辞随稿子变，所以断言认状态与"风险节写着"这个前缀，不认某个词。
    for needle in ("只差5%", "增伤20%"):
        rr = find(r85, needle)
        blocked(r85, needle, "风险节写着")
        ck("%s 判的是「口播说法未核」这一档" % needle,
           rr is not None and rr["status"] == ag.STATUS_HEDGE,
           "" if rr else "没找到这条")
    ck("数值 5%/20% 在 ASR 里查得到（所以拦它的理由不能是「ASR 没有」）",
       "5%" in ag._asr_numbers(a85) and "20%" in ag._asr_numbers(a85))
    ck("拦的是「口播说法未核」这一档（风险节点名了它）",
       any(r["status"] == ag.STATUS_HEDGE for r in r85["rows"]),
       str(sorted({r["status"] for r in r85["rows"]})))
    blocked(r85, "翻倍", "效果承诺")               # "伤害输出翻倍" 是绝对化效果句

    print("== D 新格式风险节必须能读（旧版是编号列表，pro 新稿是破折号加中文引号、还有表格行） ==")
    toks = [t for t, _ in ag._risk_tokens(md83)]
    ck("新格式的风险条目解析出了 token", len(toks) >= 6, "%d 个：%s" % (len(toks), toks[:6]))
    ck("风险节里「视野距离拉到10」这条被认出来", any("视野距离" in t for t in toks), str(toks[:10]))
    ck("85 的表格型风险节（| 20%增伤例子 | …）也被认出来",
       any("20%" in t for t in [x for x, _ in ag._risk_tokens(md85)]))

    print("== E 升档：人工回听/截图之后要留出处与结论，缺一样就不算核过 ==")
    one = {"item": "垂直同步：关闭", "source": "回听原片 00:31–00:36，选项名确为垂直同步",
           "result": "确为「垂直同步=关闭」", "by": "张亚军", "at": "2026-10-22"}
    up = ag.check(md83, a83, material_id=83, confirmations=[one])
    ck("补了出处+结论的那条转成可用", (find(up, "垂直同步") or {}).get("status") == ag.STATUS_OK,
       str((find(up, "垂直同步") or {}).get("status")))
    half = dict(one)
    half.pop("result")
    up2 = ag.check(md83, a83, material_id=83, confirmations=[half])
    ck("只写出处不写结论 → 仍然挡（不许一句话给没核过的数盖「已核实」的章）",
       (find(up2, "垂直同步") or {}).get("status") != ag.STATUS_OK)
    ck("升档一条不冲掉别条（其余待核还在）",
       up["stats"]["pending"] >= r83["stats"]["pending"] - 1,
       "%d → %d" % (r83["stats"]["pending"], up["stats"]["pending"]))

    print("== F 旧稿不当真值：v4.1-flash 那两篇同样有未核项 ==")
    o83 = ag.check(old83, a83, material_id=83)
    o85 = ag.check(old85, a85, material_id=85)
    ck("旧 83 也有硬阻断（说明这是产线问题，不是某一档模型的锅）", o83["ready_for_poster"] is False,
       "待核 %d 条" % o83["stats"]["pending"])
    ck("旧 85 也有硬阻断", o85["ready_for_poster"] is False, "待核 %d 条" % o85["stats"]["pending"])

    print("== G 边界：没有转写、没有风险节、空稿子，一律不许「看着像通过」==")
    ck("ASR 传空串 → 整篇不可用（没原文可定位，等于全条待核）",
       ag.check(md83, "", material_id=83)["ready_for_poster"] is False)
    empty = ag.check("【行动建议】\n- 随便一条建议\n", "完全无关的转写文本", material_id=1)
    ck("稿子里没有【风险核查】时不报错，按现有判据给结论", isinstance(empty["hard"], list))
    ck("空输入不炸", ag.check("", "", "")["stats"]["rows"] == 0)

    print("== I 两级判定：稿子里留待核不等于越界；越界指的是没落地的东西印到玩家照做的位置 ==")
    row_gz = find(r83, "视野距离")
    bad_plan = {"pages": [{"title": "视野距离：拉到10", "layout_key": "guide",
                           "cards": [{"t": "视野距离拉到10", "d": "按这个数设"}],
                           "bignum": {"v": "10", "label": "视野距离"}}],
                "publish": {"tt_titles": ["无限服视野距离拉到10才顺"], "copy_text": "照做即可"}}
    over = ag.poster_block([row_gz], bad_plan)
    ck("待核那条被印成卡片标题/大字/发布标题 → 判越界并点名", len(over) >= 1, str(over)[:110])
    good_plan = {"pages": [{"title": "先关这两项", "layout_key": "guide",
                            "cards": [{"t": "SSAO 关闭", "d": "省帧数，转写里逐字有这句"}],
                            "bignum": {}}],
                 "publish": {"tt_titles": ["老电脑先关这两项"], "copy_text": "转写清楚的两步"}}
    row_ok = find(r83, "SSAO")
    ck("可用那条印到成品上不算越界（不误伤正常操作）",
       ag.poster_block([row_ok], good_plan) == [], str(ag.poster_block([row_ok], good_plan))[:100])
    ck("稿子里还有待核、成品避开了 → 这一层不拦出图（人是编辑，但内部清单照样列着）",
       r83["ready_for_poster"] is False and ag.poster_block(r83["rows"], good_plan) == [],
       "pending=%d" % r83["stats"]["pending"])
    noasr = ag.check(md83, "", 83)["rows"]
    ck("没拿到转写时，凡带数值或选项名的行一律不判可用（没原文可定位＝没依据）",
       all(r["status"] != ag.STATUS_OK for r in noasr if ag.row_tokens(r["text"])),
       str([r["text"][:20] for r in noasr if ag.row_tokens(r["text"])
            and r["status"] == ag.STATUS_OK])[:90])

    print("== H 输出话术：不许出现自证「已通过质量门/已核实」这类字样 ==")
    blob = " ".join(r83["hard"] + r85["hard"]) + " " + " ".join(
        " ".join(p) for r in r83["rows"] for p in [r["problems"]])
    for bad in ("已通过质量门", "可以发布", "已核实无误"):
        ck("阻断文案里不出现「%s」" % bad, bad not in blob)
    ck("文案里写明这是「能不能照做」的判断", "能不能照做" in ag.check.__doc__ or
       "能不能照做" in (r83.get("note") or ""), str(r83.get("note"))[:60])

    print("== J 判据自己不许造靶子：过程词、虚词前缀、行内承认 ==")
    # 这三条都是 2026-10-22 实测出来的**自我误报**：闸口拿稿子里的元词（ASR/up 主）当游戏专名，
    # 拿正则贪心圈进来的虚词（「和地表景观」「需核对档位」）当项名，逼着人回听一个不存在的选项。
    # 误报的代价不是"多提示一条"，是号主把整个闸口当噪音关掉——那比没有更糟。
    ck("项名去掉虚词前缀：「和地表景观」→「地表景观」",
       ag._opt_name("和地表景观") == "地表景观", ag._opt_name("和地表景观"))
    ck("编辑动词开头的不是项名：「需核对档位」不当选项查（避免自造靶子）",
       ag._opt_name("需核对档位") == "", ag._opt_name("需核对档位"))
    ck("真项名不被误伤：「低延迟模式」「垂直同步」原样保留",
       ag._opt_name("低延迟模式") == "低延迟模式" and ag._opt_name("垂直同步") == "垂直同步",
       "%s/%s" % (ag._opt_name("低延迟模式"), ag._opt_name("垂直同步")))
    ck("过程词不当游戏专名：ASR/up 不要求转写出处，VS2 仍要求",
       ag._ascii_names("（ASR 为 VS2）按 up 主整理") == ["VS2"],
       str(ag._ascii_names("（ASR 为 VS2）按 up 主整理")))
    md_inline = ("【行动建议】\n"
                 "- 法术密度：设置成“重要”（原文，需核对档位）。\n"
                 "- 分辨率：保持最高（这句 ASR 里逐字有）。\n"
                 "- SSAO：关闭。\n"
                 "【风险核查】\n- 本节无补充\n")
    rows = ag.check(md_inline, a83, material_id=83)["rows"]
    ck("行内自己写「需核对」的那条不能算可用（给建议的同时承认没核完）",
       (find({"rows": rows}, "法术密度") or {}).get("status") != ag.STATUS_OK,
       str([(r["text"][:16], r["status"]) for r in rows if "法术密度" in r["text"]]))
    ck("同一条夹具里 ASR 逐字有、又没自我承认的（分辨率/SSAO）仍判可用——不是见词就拦",
       all((find({"rows": rows}, k) or {}).get("status") == ag.STATUS_OK for k in
           ("分辨率：保持最高", "SSAO：关闭")),
       str([(r["text"][:16], r["status"]) for r in rows]))
    ck("带「ASR」这种过程词的可用行不因元词被误拦",
       not any("ASR" in p for r in rows for p in r["problems"]),
       str([p for r in rows for p in r["problems"]])[:90])

    if fails:
        print("\nACTION_GATE_FAIL %d 条：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("ACTION_GATE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
