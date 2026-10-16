# -*- coding: utf-8 -*-
"""跑一条素材的「拆解 →（改成攻略页 + 只重拆这一页）→ 出图」，把关键结论打印出来。

为什么写这个：号主要看的是"改完之后长什么样"，得真把这几步跑完拿到 png。
这里走的就是界面上那三个按钮打的同一组接口，参数和他点的一致：
  ① POST /api/poster/plan          —— 整组拆解
  ② 版式选「攻略页」+ POST /api/poster/plan/page —— 逐页改成攻略页并重拆（这才带得上帧清单）
  ③ POST /api/poster/generate      —— 出图（攻略页 0 元）
plan 会落到 temp/guide_plan_<id>.json，改完排版层想重出就直接 reuse，不必再花一次拆解的钱。
"""
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:15801"


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=60).read().decode("utf-8"))


def wait(stage, task_id, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = get("/api/poster/status")
        if int(st.get("task_id") or 0) != int(task_id):
            print("任务串号了：%s 现在是 %s" % (stage, st.get("task_id")))
            return None
        if st.get("error"):
            print("%s 失败：%s" % (stage, st["error"]))
            return None
        if st.get("done") and not st.get("running"):
            return st
        time.sleep(3)
    print("%s 超时" % stage)
    return None


def show(pages):
    for i, p in enumerate(pages, 1):
        ph = p.get("photos") or []
        print("  第%d页 [%s]《%s》 卡标题=%s 脚注=「%s」图注=%s 栏目=%s 大字=%s 警示=%s 提问=%s"
              % (i, p.get("layout_key") or "-", p.get("title") or "",
                 " / ".join([(c.get("t") or "") for c in (p.get("cards") or [])]),
                 p.get("footer") or "", [x.get("caption") for x in ph],
                 p.get("kicker") or "", (p.get("bignum") or {}).get("v") or "",
                 (p.get("warn") or {}).get("t") or "", (p.get("ask") or {}).get("q") or ""))


def rework(sid, theme, pages, idx, spine, style_key, ratio="9:16"):
    body = {"summary_id": sid, "page_index": idx, "theme": theme, "spine": spine,
            "style_key": style_key, "text_mode": "typeset", "ratio": ratio, "pages": pages}
    r = post("/api/poster/plan/page", body)
    if r.get("error"):
        print("  重拆提交失败:", r["error"])
        return None
    st = wait("page", r["task_id"])
    if not st:
        return None
    pl = st.get("plan") or {}
    print("  notes:", pl.get("notes"))
    return pl.get("page")


def render(sid, plan, publish, channel="toutiao", form="newspic", ratio="9:16"):
    dump_plan(sid, plan, publish)
    print("=== %s 号 出图（渠道 %s%s，攻略页 0 元）==="
          % (sid, channel, ("·" + form) if channel == "wechat" else ""))
    g = post("/api/poster/generate", {"summary_id": sid, "plan": plan, "ratio": ratio,
                                      "text_mode": "typeset", "review": False,
                                      "publish_channel": channel, "form": form,
                                      "publish": publish})
    if g.get("error"):
        print("出图提交失败:", g["error"])
        return
    st = wait("generate", g["task_id"])
    if not st:
        return
    mf = st.get("package") or {}
    print("产物:", mf.get("url"), " id:", mf.get("id"), " 上图字数:", mf.get("chars"),
          " 回读校验:", mf.get("verify_ok"))
    d = "output/poster/%s" % mf.get("id")
    print("落盘:", sorted(os.listdir(d)) if os.path.isdir(d) else d + " 不存在")
    mfp = os.path.join(d, "manifest.json")
    if os.path.isfile(mfp):
        with open(mfp, encoding="utf-8") as f:
            m = json.load(f)
        print("渠道键: publish_channel=%s form=%r" % (m.get("publish_channel"), m.get("form")))
        print("配文含AI尾注句:", "本文由 AI 辅助整理归纳" in (m.get("copy_text") or ""))
        print("关键词回复:", repr(m.get("keyword_reply")))
        print("图上脚注(plan/footer):", repr((m.get("plan") or {}).get("footer")))


def dump_plan(sid, plan, publish):
    with open("temp/guide_plan_%s.json" % sid, "w", encoding="utf-8") as f:
        json.dump({"plan": plan, "publish": publish}, f, ensure_ascii=False, indent=1)


def load_plan(sid):
    with open("temp/guide_plan_%s.json" % sid, encoding="utf-8") as f:
        return json.load(f)


def run(sid, theme, page_hint=0, to_guide=False, reuse=False, channel="toutiao", form="newspic"):
    # 渠道决定比例缺省：公众号 3:4（图片消息/文章配图口径），其余 9:16
    ratio = "3:4" if channel == "wechat" else "9:16"
    if reuse:
        saved = load_plan(sid)
        show(saved["plan"].get("pages") or [saved["plan"]])
        render(sid, saved["plan"], saved.get("publish"), channel=channel, form=form, ratio=ratio)
        return
    print("=== %s 号 拆解（theme=%s 渠道=%s）===" % (sid, theme, channel))
    fr = get("/api/poster/frames?summary_id=%s" % sid)
    print("可用画面 %s / 抽了 %s / 已判 %s" % (fr.get("usable"), fr.get("total"), fr.get("judged")))
    r = post("/api/poster/plan", {"summary_id": sid, "theme": theme, "page_hint": page_hint,
                                  "text_mode": "typeset", "ratio": ratio,
                                  "publish_channel": channel, "form": form})
    if r.get("error"):
        print("拆解提交失败:", r["error"])
        return
    st = wait("plan", r["task_id"])
    if not st:
        return
    plan = (st.get("plan") or {}).get("plan") or {}
    publish = (st.get("plan") or {}).get("publish")
    pages = plan.get("pages") or [plan]
    print("拆解回来：style_key=%s 页数=%d guide_only=%s notes=%s"
          % (plan.get("style_key"), len(pages), (st.get("plan") or {}).get("guide_only"),
             (st.get("plan") or {}).get("notes")))
    if to_guide:
        # 界面上就是这两步：版式选「攻略页」→「只重拆这一页」
        skin = get("/api/poster/styles?summary_id=%s" % sid).get("guideStyle") or "guide_ink"
        print("=== 按界面那两步把每页改成攻略页（皮肤 %s）===" % skin)
        plan["style_key"] = skin
        new_pages = []
        for p in pages:
            q = dict(p)
            q["layout_key"] = "guide"
            q["style_key"] = skin
            q["photos"] = []
            new_pages.append(q)
        for i in range(1, len(new_pages) + 1):
            print(" 重拆第 %d 页…" % i)
            got = rework(sid, theme, new_pages, i, plan.get("spine") or "", skin, ratio=ratio)
            if got:
                got["layout_key"] = "guide"
                got["style_key"] = skin
                new_pages[i - 1] = got
        plan["pages"] = new_pages
        # 界面上那一步等价：整组一句尾注，只挂末页（前面几页留空，别一页印一次）
        # 小红书档例外：后端出图时会把 footer 清空，这里不用特殊处理
        foot = plan.get("footer") or ""
        for i, q in enumerate(new_pages):
            q["footer"] = foot if i == len(new_pages) - 1 else ""
    show(plan.get("pages") or pages)
    render(sid, plan, publish, channel=channel, form=form, ratio=ratio)


if __name__ == "__main__":
    sid = int(sys.argv[1])
    theme = sys.argv[2]
    hint = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    flags = sys.argv[4:] if len(sys.argv) > 4 else []
    chan, form = "toutiao", "newspic"
    for i, f in enumerate(flags):
        if f == "--channel" and i + 1 < len(flags):
            chan = flags[i + 1]
        if f == "--form" and i + 1 < len(flags):
            form = flags[i + 1]
    run(sid, theme, hint, to_guide=("guide" in flags), reuse=("reuse" in flags),
        channel=chan, form=form)
