# -*- coding: utf-8 -*-
"""check_douyin_cookie.py — 抖音 cookie 到底缺什么，一条命令说清楚

为什么要有这个：直链解析被风控时报的是 `403 Blocked by ArgusSecurityPlugin Uifid Not Found`，
这句人话翻译过来就是"你给的 cookie 里没有 UIF_ID"。但文件里现在只剩一个 `sessionid`（42 字节），
谁都不知道缺哪几键、补完算不算补对——所以这里做三件事：

1. 拆开看有哪些键，对照必需清单报缺项（**不打印任何值**，这是公开仓库）；
2. 拿一条真实 aweme_id 真打一次接口，把 HTTP 状态与拦截原因原样报出来；
3. 打印补齐步骤与落点路径。

用法：
    python3 scripts/check_douyin_cookie.py                # 用默认那条测试 id
    python3 scripts/check_douyin_cookie.py 7691087995755187506
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor" / "douyin_parse"))

from src import path_config                                    # noqa: E402

# 必需键：少任何一个都会被 Argus 挡在门外（实测缺 UIF_ID 的报错就是 "Uifid Not Found"）
REQUIRED = [
    ("sessionid", "登录态。只有它不够，别以为有 sessionid 就算配好了"),
    ("ttwid", "抖音的访客标识，第一次访问网页时由站点下发"),
    ("UIF_ID", "风控要的用户指纹——现在缺的就是它，报错原话是 Uifid Not Found"),
    ("__ac_nonce", "反爬一次性串，和 __ac_signature 成对出现"),
    ("__ac_signature", "同上，缺一即被拦"),
]
NICE = [("msToken", "多数请求要带，浏览器会自己续"), ("webid", "设备/浏览器 id")]
TEST_AWEME = "7691087995755187506"


def parse_keys(raw):
    keys = {}
    for part in (raw or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            keys[k.strip()] = len(v.strip())
    return keys


def main():
    p = path_config.get_cookie_path()
    raw = ""
    if p.exists():
        raw = p.read_text(encoding="utf-8").strip()
    keys = parse_keys(raw)
    print("cookie 文件：%s（%d 字节，%d 个键）" % (p, len(raw), len(keys)))
    if not raw:
        print("  文件是空的。")
    missing = [(k, why) for k, why in REQUIRED if k.lower() not in {x.lower() for x in keys}]
    print("  现有键：%s" % ("、".join(sorted(keys)) or "无"))
    if missing:
        print("  缺必需键 %d 个：" % len(missing))
        for k, why in missing:
            print("     - %-14s %s" % (k, why))
    else:
        print("  必需键齐了。")
    for k, why in NICE:
        if k.lower() not in {x.lower() for x in keys}:
            print("  建议补：%s（%s）" % (k, why))

    print("\n真打一次接口（拿一条真实视频，看风控放不放行）…")
    try:
        from douyin_video_parser import DouyinVideoParser
    except Exception as e:
        print("  解析器进不去：%s" % e)
        return 2
    parser = DouyinVideoParser()
    parser.set_cookie(raw)
    aid = sys.argv[1] if len(sys.argv) > 1 else TEST_AWEME
    import requests
    params = dict(parser.BASE_PARAMS)
    params["aweme_id"] = aid
    url = parser._sign_abogus_url("https://www.douyin.com/aweme/v1/web/aweme/detail/", params)
    try:
        r = requests.get(url, headers=parser._build_headers(
            "https://www.douyin.com/video/%s" % aid), timeout=25)
        body = (r.text or "").strip()
        print("  aweme_id=%s → HTTP %s，响应 %d 字" % (aid, r.status_code, len(body)))
        if r.status_code == 200 and '"aweme_detail"' in body:
            try:
                j = json.loads(body)
                d = (j.get("aweme_detail") or {}).get("video") or {}
                print("  ✅ 通了。能拿到 play_addr：%s" % ("有" if d.get("play_addr") else "没有"))
            except ValueError:
                print("  ✅ 状态 200（返回体没解析，但风控放行）")
            return 0
        print("  ❌ 还是被拦。原话：%s" % body[:120])
        return 1
    except Exception as e:
        print("  请求本身失败：%s" % repr(e)[:160])
        return 1


if __name__ == "__main__":
    print(__doc__.split("\n\n")[0])
    rc = main()
    print("""
补齐步骤（一次拷整串，别只拷 sessionid——今天就是只留了 sessionid 才全被拦）：
  1. 用 Chrome 打开 https://www.douyin.com/ 并确认是登录状态（右上角能看到自己头像）；
  2. F12 → Network 网络 → 在地址栏回车任意一条视频页 → 找到名字带 aweme/detail 的那条请求；
  3. 点开它 → Headers 请求头 → 把 **Cookie 那一整行** 全选复制（很长，通常 1500-3000 字）；
  4. 粘进 %s 覆盖原内容，保存（这一行就是一整串 k=v; k=v;…，不要换行、不要加引号）；
  5. 重跑本脚本，看到 ✅ 就通了；界面「采集中心」的抖音 cookie 状态也会跟着变。
  注意：这个文件在 .gitignore 里，不会被提交（公开仓库，绝不能进）。
""" % path_config.get_cookie_path())
    sys.exit(rc)
