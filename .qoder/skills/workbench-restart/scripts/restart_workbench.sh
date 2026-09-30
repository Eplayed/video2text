#!/usr/bin/env bash
# 安全重启 video2text Flask 工作台（15801）。
# 为什么不用 pkill：实际进程命令行是 ".../MacOS/Python app.py"，pkill -f "web/app.py" 匹配不到，
# 旧进程会继续占端口，新进程静默退出（本项目踩过的坑）。故一律按端口 PID 精确处置。
set -uo pipefail

PORT="${PORT:-15801}"
PY="${PYTHON_BIN:-/usr/local/bin/python3}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SELF_DIR/../../../.." && pwd)"
WEB_DIR="$ROOT/web"
LOG="${LOG:-$ROOT/logs/web_restart.log}"
HEALTH_URL="http://127.0.0.1:${PORT}/api/stats"
FAILS=0

say() { printf '%s\n' "$1"; }

# ---- 1) 语法自检：默认查 web/app.py，额外传入的文件一并检查 ----
say "== 1/4 语法自检 =="
CHECK_FILES="$ROOT/web/app.py $*"
for f in $CHECK_FILES; do
  [ -f "$f" ] || continue
  if "$PY" -m py_compile "$f" 2>/dev/null; then
    say "  ok   ${f#$ROOT/}"
  else
    say "  FAIL ${f#$ROOT/}"
    "$PY" -m py_compile "$f" 2>&1 | tail -5 | sed 's/^/       /'
    FAILS=$((FAILS + 1))
  fi
done
if [ "$FAILS" -ne 0 ]; then
  say "语法检查未通过，已中止重启（旧进程保持运行）"
  exit 1
fi

# ---- 2) 按端口 PID 杀旧进程 ----
say "== 2/4 停旧进程 =="
OLD_PID="$(lsof -nP -i ":$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
if [ -z "$OLD_PID" ]; then
  say "  端口 $PORT 无监听进程，跳过"
else
  say "  旧 PID=$OLD_PID（$(ps -p "$OLD_PID" -o command= 2>/dev/null | head -c 80)）"
  kill "$OLD_PID" 2>/dev/null
  WAITED=0
  while [ "$WAITED" -lt 10 ]; do
    kill -0 "$OLD_PID" 2>/dev/null || break
    sleep 1
    WAITED=$((WAITED + 1))
  done
  if kill -0 "$OLD_PID" 2>/dev/null; then
    say "  TERM 10s 未退出，改 KILL"
    kill -9 "$OLD_PID" 2>/dev/null
    sleep 2
  fi
  REMAIN="$(lsof -nP -i ":$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
  if [ -n "$REMAIN" ]; then
    say "  FAIL 端口仍被 PID=$REMAIN 占用，中止（请人工确认该进程身份后再处置）"
    exit 1
  fi
  say "  端口已释放"
fi

# ---- 3) 启动新进程 ----
say "== 3/4 启动 =="
mkdir -p "$ROOT/logs"
cd "$WEB_DIR" || { say "FAIL 无法进入 $WEB_DIR"; exit 1; }
nohup "$PY" app.py >>"$LOG" 2>&1 &
BG_PID=$!

# ---- 4) 双重确认：端口接管 + 接口可用 ----
say "== 4/4 确认接管 =="
NEW_PID=""
WAITED=0
while [ "$WAITED" -lt 30 ]; do
  NEW_PID="$(lsof -nP -i ":$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
  [ -n "$NEW_PID" ] && break
  kill -0 "$BG_PID" 2>/dev/null || break
  sleep 1
  WAITED=$((WAITED + 1))
done

if [ -z "$NEW_PID" ]; then
  say "FAIL 未监听 $PORT，新进程未接管。日志尾部："
  tail -20 "$LOG" 2>/dev/null | sed 's/^/       /'
  exit 1
fi
if [ -n "$OLD_PID" ] && [ "$NEW_PID" = "$OLD_PID" ]; then
  say "FAIL PID 未变化（旧进程仍在），请人工核查"
  exit 1
fi
HTTP="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$HEALTH_URL")"
if [ "$HTTP" != "200" ]; then
  say "WARN 已监听但 $HEALTH_URL 返回 ${HTTP:-无响应}，请查日志"
  tail -10 "$LOG" 2>/dev/null | sed 's/^/       /'
  exit 1
fi
say "  ok  PID ${OLD_PID:-无} -> $NEW_PID  HTTP $HTTP"
say "工作台已重启：http://127.0.0.1:$PORT  （日志 ${LOG#$ROOT/}）"
