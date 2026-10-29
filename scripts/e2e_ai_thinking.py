# -*- coding: utf-8 -*-
"""ai_thinking 的离线回归：关思考那一层到底有没有把参数带上，以及会不会误伤别的模型。

不打网络（真跑一次的对照数据已经写在 src/ai_thinking.py 的文档里：
同一份 7936 字提示词，开着思考 >230 秒不返回、关掉 21 秒出可用 JSON）。
这里钉的是**边界行为**：命中名单才动、调用点自己传过就不覆盖、没配名单就零变化。
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import ai_thinking                      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK " if cond else "XX ", name, ("  " + detail) if detail else ""))


class FakeCompletions(object):
    """假 SDK：只记录最后一次 create 收到什么参数。"""
    last = {}

    def create(self, *args, **kwargs):
        FakeCompletions.last = dict(kwargs)
        return {"ok": True}


def install_fake_sdk():
    mod = types.ModuleType("openai.resources.chat.completions")
    mod.Completions = FakeCompletions
    pkg = types.ModuleType("openai.resources.chat")
    pkg.completions = mod
    root = sys.modules.setdefault("openai", types.ModuleType("openai"))
    root.resources = types.ModuleType("openai.resources")
    root.resources.chat = pkg
    sys.modules["openai.resources.chat"] = pkg
    sys.modules["openai.resources.chat.completions"] = mod
    return mod


def main():
    print("\n== 1. 名单判据 ==")
    check("命中前缀才关：qwen3.8-omni-flash", ai_thinking.should_off("qwen3.8-omni-flash", ["qwen3.8-omni"]))
    check("不误伤别的模型：deepseek-v4-pro-0813",
          not ai_thinking.should_off("deepseek-v4-pro-0813", ["qwen3.8-omni"]))
    check("不误伤同族但没列的：qwen3.8-max", not ai_thinking.should_off("qwen3.8-max", ["qwen3.8-omni"]))
    check("大小写不敏感", ai_thinking.should_off("Qwen3.8-OMNI-Flash", ["qwen3.8-omni"]))
    check("空模型名不报错", not ai_thinking.should_off("", ["qwen3.8-omni"]))
    check("名单为空＝一个都不关", not ai_thinking.should_off("qwen3.8-omni-flash", []))

    print("\n== 2. 挂在 SDK 边界上的那一层 ==")
    mod = install_fake_sdk()
    ai_thinking._INSTALLED.update({"done": False, "error": "", "off_prefixes": []})
    orig_create = FakeCompletions.create

    def fake_read():
        return ["qwen3.8-omni"]
    real = ai_thinking._read_prefixes
    ai_thinking._read_prefixes = fake_read
    try:
        info = ai_thinking.install()
        check("挂上了且记录了名单", info["done"] and info["off_prefixes"] == ["qwen3.8-omni"], str(info))
        check("包不上时不会假装挂好（marker 在函数上）",
              getattr(mod.Completions.create, "_thinking_wrapped", False))
        c = mod.Completions()
        c.create(model="qwen3.8-omni-flash", messages=[{"role": "user", "content": "x"}])
        check("命中名单的请求自动带上 enable_thinking=False",
              FakeCompletions.last.get("extra_body") == {"enable_thinking": False},
              str(FakeCompletions.last.get("extra_body")))
        c.create(model="deepseek-v4-pro-0813", messages=[{"role": "user", "content": "x"}])
        check("没命中的请求参数原样不动", "extra_body" not in FakeCompletions.last,
              str(FakeCompletions.last)[:80])
        c.create(model="qwen3.8-omni-flash", extra_body={"foo": 1}, messages=[])
        check("调用点自己传了 extra_body 会被补上、不丢它原来的键",
              FakeCompletions.last.get("extra_body") == {"foo": 1, "enable_thinking": False},
              str(FakeCompletions.last.get("extra_body")))
        c.create(model="qwen3.8-omni-flash",
                 extra_body={"enable_thinking": True}, messages=[])
        check("调用点显式要开思考，就不覆盖它（显式意图优先）",
              FakeCompletions.last.get("extra_body") == {"enable_thinking": True},
              str(FakeCompletions.last.get("extra_body")))
        # 幂等：装两次不该套两层
        n_before = mod.Completions.create
        ai_thinking.install()
        check("重复 install 不套第二层", mod.Completions.create is n_before)
    finally:
        ai_thinking._read_prefixes = real
        mod.Completions.create = orig_create
        ai_thinking._INSTALLED.update({"done": False, "error": "", "off_prefixes": []})

    print("\n== 3. 配置读的是那份 gitignore 的文件 ==")
    prefixes = ai_thinking._read_prefixes()
    # 判据跟着候选池走，不抄模型名：2026-10-29 起前缀放宽成整个 qwen3.8 家族，
    # 因为池子里每一个都实测吃这个参数（数据在 temp/probe_model_pool.log）。
    from src import ai_models
    pool = [r["model"] for r in ai_models.pool("main")] or [r for r in (ai_models.resolve("main")[0],) if r]
    check("池子里每一个模型都被这条前缀覆盖（漏一个就是慢到超时）",
          bool(pool) and all(ai_thinking.should_off(m, prefixes) for m in pool),
          "前缀=%s 池=%s" % (prefixes, pool))

    print("\n━━ ai_thinking 契约：%d 项通过 / %d 项失败 ━━" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败：%s" % FAIL)
        return 1
    print("ALL_OK 关思考那一层只动名单里的模型，且从不覆盖显式意图")
    return 0


if __name__ == "__main__":
    sys.exit(main())
