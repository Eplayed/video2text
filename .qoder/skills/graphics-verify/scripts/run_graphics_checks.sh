#!/usr/bin/env bash
# 出图与调度回归：三轴 token 静态检查 + 头条/公众号两条 e2e + 海报布局与多页一条
# + 自动同步调度与并发写一条（后两条各管一个「静默失效」高发区）。
# 全部通过时末行输出 ALL_OK；任一失败输出 FAIL 行并以非 0 退出。
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SELF_DIR/../../../.." && pwd)"
PY="${PYTHON_BIN:-/usr/local/bin/python3}"
RUNNER=(env -u PYTHONHOME -u PYTHONPATH "$PY")
OVERALL=0

run_one() {
  local label="$1" script="$2"
  printf '\n== %s (%s)\n' "$label" "${script#$ROOT/}"
  if [ ! -f "$script" ]; then
    printf '  FAIL 脚本缺失\n'; OVERALL=1; return
  fi
  local out rc
  out="$(cd "$ROOT" && "${RUNNER[@]}" "$script" 2>&1)"; rc=$?
  printf '%s\n' "$out" | grep -E "^(✗|✓|FAIL|WARN|ALL_OK|==|结果|.*不通过|.*未接通)" | tail -25
  if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "ALL_OK"; then
    printf '  -> PASS\n'
  else
    printf '  -> FAIL (exit=%s)\n' "$rc"
    printf '%s\n' "$out" | tail -15 | sed 's/^/     /'
    OVERALL=1
  fi
}

run_one "三轴 token 静态检查" "$ROOT/scripts/check_variant_tokens.py"
run_one "头条 e2e（A-R）" "$ROOT/scripts/e2e_toutiao_variants.py"
run_one "公众号 e2e（W1-W14）" "$ROOT/scripts/e2e_wechat_variants.py"
run_one "海报布局与多页 e2e（A-K）" "$ROOT/scripts/e2e_poster_layouts.py"
run_one "自动同步调度与并发写 e2e（A-J）" "$ROOT/scripts/e2e_sync_tasks.py"

printf '\n'
if [ "$OVERALL" -eq 0 ]; then
  echo "ALL_OK 出图与调度回归五条线全绿"
else
  echo "FAIL 出图与调度回归存在失败项，按上面 ✗/FAIL 行定位"
fi
exit "$OVERALL"
