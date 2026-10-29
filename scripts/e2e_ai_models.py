# -*- coding: utf-8 -*-
"""候选池与自动换档的离线回归（2026-10-29：主档/快速档按到期时间自动选模型）。

钉的是边界行为，不打网络：
  · 排序按**到期日近的先用**，判"不可用"只认两条证据（日期已过 / 403 盖过章）；
  · 撞额度错才换下一个，限流和参数错不许换（换了也一样撞，还会绕限流）；
  · 不在池子里的模型（看图那一档、手动写死的）一律不动；
  · 出图档撤掉后，没有底图就是**明着拒绝**，不许拿写死的默认模型去扣钱。
真跑数据在 temp/probe_model_pool.log（同一提示词逐模型实测：四个 qwen3.8 吃
enable_thinking=False、glm-5.3 拒这个参数、kimi-k3 连 temperature=0.3 都拒）。
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import ai_models, ai_failover            # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK " if cond else "XX ", name, ("  " + detail) if detail else ""))


ENV = {"AI_MODEL_POOL": "b-new|2027-01-01, a-soon|2026-12-01, no-date",
       "AI_MODEL": "hand-picked", "AI_MODEL_FAST": "fast-hand"}


def main():
    print("\n== 1. 池子解析与排序 ==")
    rows = ai_models.parse_pool("b|2027-01-01, a|2026-12-01, c")
    check("按到期日升序（先到期的先消耗）", [r["model"] for r in rows] == ["a", "b", "c"],
          str([r["model"] for r in rows]))
    check("没写到期日的排最后（它谈不上快过期）", rows[-1]["model"] == "c" and rows[-1]["expiry"] == "")
    check("写错的日期当没日期，不猜", ai_models.parse_pool("x|2026-13-99")[0]["expiry"] == "")
    check("空串不炸", ai_models.parse_pool("") == [])
    check("同一天到期保持配置里的先后",
          [r["model"] for r in ai_models.parse_pool("p|2026-12-01,q|2026-12-01")] == ["p", "q"])

    print("\n== 2. 判不可用只认两条证据 ==")
    dead = ai_models.annotate({"model": "gone", "expiry": "2020-01-01", "ord": 0})
    check("日期已过 → expired", dead["state"] == "expired", str(dead))
    live = ai_models.annotate({"model": "alive", "expiry": "2027-01-01", "ord": 0})
    check("没到期也没盖章 → ok（额度百分比高不算死）", live["state"] == "ok", str(live))
    real = ai_models._exhausted
    ai_models._exhausted = lambda m: m == "stamped"
    try:
        st = ai_models.annotate({"model": "stamped", "expiry": "2027-01-01", "ord": 0})
        check("本地账盖过章（403 时盖的）→ exhausted", st["state"] == "exhausted", str(st))
        noexp = ai_models.annotate({"model": "ok2", "expiry": "", "ord": 0})
        check("没到期日且没盖章 → 照用", noexp["state"] == "ok")
    finally:
        ai_models._exhausted = real

    print("\n== 3. resolve：怎么选、怎么说自己是怎么选的 ==")
    model, note = ai_models.resolve("main", ENV)
    check("取到期最近的那个", model == "a-soon", model)
    check("说明里写清怎么选的和下一个是谁", "按到期日自动选" in note and "b-new" in note, note)
    m2, n2 = ai_models.resolve("fast", ENV)
    check("快速档没单配池子就共用主档那条", (m2, n2) == (model, note), m2)
    m3, n3 = ai_models.resolve("fast", {"AI_MODEL_FAST": "fast-hand"})
    check("没配池子 → 用写死值并说明是手动指定", m3 == "fast-hand" and "手动指定" in n3, "%s|%s" % (m3, n3))
    m4, n4 = ai_models.resolve("main", {})
    check("没配池子也没写死 → 空模型名 + 说没配", m4 == "" and "没配" in n4, "%s|%s" % (m4, n4))
    env_all_dead = {"AI_MODEL_POOL": "old|2020-01-01, older|2019-01-01", "AI_MODEL": "hand-picked"}
    m5, n5 = ai_models.resolve("main", env_all_dead)
    check("整条池子都不可用 → 不假装选出来了", m5 == "" and "整条池子都不可用" in n5, "%s|%s" % (m5, n5))
    check("这时候界面拿到的兜底是写死值（_ai_config 里 or 的那一下），不是静默换档",
          (m5 or env_all_dead["AI_MODEL"]) == "hand-picked")

    print("\n== 4. 换档找下一个 ==")
    check("池子里的模型认得出属于哪一档", ai_models.tier_of("a-soon", ENV) == "main")
    check("不在池子里的（看图那档）返回空＝不动它", ai_models.tier_of("qwen3-vl-plus", ENV) == "")
    check("a-soon 死了换 b-new", ai_models.next_after("main", "a-soon", env=ENV) == "b-new")
    check("没写到期日那个排在最后当兜底（有日期的先用完）",
          ai_models.next_after("main", "b-new", env=ENV) == "no-date")
    two = dict(ENV, AI_MODEL_POOL="b-new|2027-01-01, a-soon|2026-12-01")
    check("池子到底了返回空（不回头重试第一个，免得原地打转）",
          ai_models.next_after("main", "b-new", env=two) == "")
    check("已经试过的不再试（skip）",
          ai_models.next_after("main", "a-soon", skip=["b-new", "no-date"], env=ENV) == "")

    print("\n== 5. SDK 边界：什么错才换、换几次 ==")
    calls = []

    class Boom(Exception):
        pass

    class FakeCompletions(object):
        def create(self, *args, **kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] == "a-soon":
                raise Boom(ENV["__err__"])
            return {"ok": True, "model": kwargs["model"]}

    mod = types.ModuleType("openai.resources.chat.completions")
    mod.Completions = FakeCompletions
    pkg = types.ModuleType("openai.resources.chat")
    pkg.completions = mod
    root = sys.modules.setdefault("openai", types.ModuleType("openai"))
    root.resources = types.ModuleType("openai.resources")
    root.resources.chat = pkg
    sys.modules["openai.resources.chat"] = pkg
    sys.modules["openai.resources.chat.completions"] = mod

    real_read = ai_models._read_env
    ai_models._read_env = lambda: ENV
    orig_create = FakeCompletions.create
    try:
        ai_failover._INSTALLED["done"] = False
        info = ai_failover.install()
        check("钩子挂上了", info["done"] and getattr(mod.Completions.create,
                                                    "_failover_wrapped", False), str(info))
        c = mod.Completions()
        ENV["__err__"] = "Error code: 403 - insufficient_quota / Free quota exhausted"
        r = c.create(model="a-soon", messages=[])
        check("额度用尽 → 自动换池里下一个并成功", r["model"] == "b-new" and calls == ["a-soon", "b-new"],
              str(calls))
        check("换过谁、为什么留了痕（界面要印出来）",
              ai_failover.events()[-1]["from"] == "a-soon"
              and ai_failover.events()[-1]["to"] == "b-new"
              and "额度" in ai_failover.events()[-1]["reason"], str(ai_failover.events()[-1]))
        calls[:] = []
        ENV["__err__"] = "Error code: 429 - RateLimit reached"
        try:
            c.create(model="a-soon", messages=[])
        except Boom:
            pass
        check("限流不换档（换模型等于绕限流，且下一个一样撞）", calls == ["a-soon"], str(calls))
        calls[:] = []
        ENV["__err__"] = "Error code: 400 - invalid_parameter_error: temperature"
        try:
            c.create(model="a-soon", messages=[])
        except Boom:
            pass
        check("参数错不换档（那是我们自己写错了）", calls == ["a-soon"], str(calls))
        calls[:] = []
        try:
            c.create(model="qwen3-vl-plus", messages=[])
        except Boom:
            pass
        check("不在池子里的模型一律不动（看图那档自己那一条路）", calls == ["qwen3-vl-plus"], str(calls))
        calls[:] = []
        ENV["__err__"] = "insufficient_quota"
        only = dict(ENV, AI_MODEL_POOL="a-soon|2026-12-01")
        ai_models._read_env = lambda: only
        try:
            c.create(model="a-soon", messages=[])
            check("池子里没有下一个时把原错误抛出去，不吞", False, "没抛错")
        except Boom:
            check("池子里没有下一个时把原错误抛出去，不吞", calls == ["a-soon"], str(calls))
        n_before = mod.Completions.create
        ai_failover.install()
        check("重复 install 不套第二层", mod.Completions.create is n_before)
    finally:
        ai_models._read_env = real_read
        mod.Completions.create = orig_create
        ai_failover._INSTALLED["done"] = False

    print("\n== 6. 出图档撤掉的契约 ==")
    from src import ai_poster
    check("不再有写死的出图默认模型（撤档后留着就等于没人认领也照扣钱）",
          not hasattr(ai_poster, "IMAGE_MODEL"))
    check("没配 AI_MODEL_IMAGE → 模型名为空", ai_poster._image_model({"image_model": ""}) == "")
    check("配置里显式给了就还能用（撤档不是删功能）",
          ai_poster._image_model({"image_model": "qwen-image-3.0-pro"}) == "qwen-image-3.0-pro")
    try:
        ai_poster.generate_image("x", "936*1664", {"api_key": "k", "api_base": "https://x/v1"})
        check("没有模型时明着拒绝出图", False, "没抛错")
    except ai_poster.PosterError as e:
        msg = str(e)
        check("拒绝的话说得出路（真帧 / 豆包回传 / 加回配置）",
              "撤掉" in msg and "豆包" in msg and "AI_MODEL_IMAGE" in msg, msg[:90])

    print("\n== 7. 现读配置：改池子不用重启 ==")
    env = ai_models._read_env()
    check("config.env.local 里配了候选池", bool(env.get("AI_MODEL_POOL")), env.get("AI_MODEL_POOL", ""))
    check("池子里每个候选都带到期日（自动选择靠它排序）",
          all(r["expiry"] for r in ai_models.pool("main")), str(ai_models.pool("main")))
    check("主档与快速档现在都解析到同一个「先到期」的模型",
          ai_models.resolve("main")[0] == ai_models.resolve("fast")[0],
          "%s / %s" % (ai_models.resolve("main")[0], ai_models.resolve("fast")[0]))
    from src import ai_thinking
    pfx = ai_thinking._read_prefixes()
    check("关思考那条前缀覆盖池子里每一个模型（开着会一路等到超时）",
          all(ai_thinking.should_off(r["model"], pfx) for r in ai_models.pool("main")),
          "前缀=%s 池=%s" % (pfx, [r["model"] for r in ai_models.pool("main")]))
    check("出图档不再从配置里读（AI_MODEL_IMAGE 已停用）", "image_model" not in _app_cfg_keys(),
          str(sorted(_app_cfg_keys())))

    print("\n━━ 候选池与自动换档：%d 项通过 / %d 项失败 ━━" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败：%s" % FAIL)
        return 1
    print("ALL_OK 到期近的先用、撞额度错才换下一个、不在池子里的一律不动")
    return 0


def _app_cfg_keys():
    """_ai_config() 返回的键名（第 7 节要证明出图那一档真的从配置层没了）。"""
    sys.path.insert(0, str(ROOT / "web"))
    import app                                          # noqa: F401
    return set(app._ai_config().keys())


if __name__ == "__main__":
    sys.exit(main())
