---
name: workbench-restart
description: 安全重启 video2text Flask 工作台（127.0.0.1:15801）并验证接管。改完任何 .py（web/app.py、src/*）后必须走这套流程，禁止用 pkill 杀进程。当需要重启服务、代码改完不生效、端口被占、接口无响应或页面还是旧行为时使用。
---

# 工作台重启与自检

## Overview

Flask 无热重载，且真实进程命令行是 `.../MacOS/Python app.py`，`pkill -f "web/app.py"` 匹配不到旧进程——旧进程继续占端口、新进程静默退出，表现为"改了代码没生效"。本技能用端口 PID 精确处置，并在重启后做 PID + 接口双重确认。

## 用法

```bash
bash .qoder/skills/workbench-restart/scripts/restart_workbench.sh
# 改动过多个文件时，把路径作为参数传入，重启前一并 py_compile
bash .qoder/skills/workbench-restart/scripts/restart_workbench.sh src/content_store.py src/graphics/variants.py
```

脚本按 4 步执行：语法自检 → 按端口 PID 杀旧（TERM 10s 未退再 KILL）→ `cd web && python3 app.py` 后台启动（日志 `logs/web_restart.log`）→ 确认 PID 已变且 `/api/stats` 返回 200。任一步失败即中止并保留现场，不会留下半死状态。

## 关键约束

- 语法检查未通过时脚本**不重启**，旧进程继续服务；先修语法再来。
- `web/app.py` 里端口硬编码为 15801，所以 `PORT` 只影响检测目标，改端口要先改代码。
- 端口被非本项目进程占用时脚本只报错退出，不要手工 `kill -9` 别人——先 `ps -p <PID> -o command=` 看清身份。
- 重启成功不等于功能生效。前端相关改动要再走一次浏览器验证（`browser-use` MCP 打开 http://127.0.0.1:15801 截图 + 看 console），页面还是旧行为通常是缓存或改动点没命中，不是"再重启一次"能解决。
- 涉及 `src/graphics/*` 的改动，重启后跑 `graphics-verify` 技能。

## Resources

- `scripts/restart_workbench.sh`：幂等重启脚本，可用环境变量覆盖 `PORT`、`PYTHON_BIN`、`LOG`。
