#!/bin/sh
# 无发言自检：环境 + 清单 + 收件箱/闸/配置/状态/登录等 fixture。
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
REAL_HOME="${BILI_SCROLL_HOME:-$HOME/.qoder-bili-scroll}"
PY="$REAL_HOME/.venv/bin/python"
TEST_TMP="$ROOT/.test-tmp"

rm -rf "$TEST_TMP"
mkdir -p "$TEST_TMP"
export TMPDIR="$TEST_TMP"
trap 'rm -rf "$TEST_TMP"' EXIT INT TERM

if [ -x "$REAL_HOME/.venv/bin/python" ]; then
  mkdir -p "$TEST_TMP/selftest-home"
  ln -s "$REAL_HOME/.venv" "$TEST_TMP/selftest-home/.venv"
  BILI_SCROLL_HOME="$TEST_TMP/selftest-home" "$ROOT/bin/bili" selftest
else
  "$ROOT/bin/bili" selftest
fi

"$PY" "$ROOT/tests/test_manifests.py"
"$PY" "$ROOT/tests/test_inbox_fixture.py"
"$PY" "$ROOT/tests/test_inbox_tool.py"
"$PY" "$ROOT/tests/test_reply_wrapper.py"
"$PY" "$ROOT/tests/test_config.py"
"$PY" "$ROOT/tests/test_seen.py"
"$PY" "$ROOT/tests/test_report.py"
"$PY" "$ROOT/tests/test_api_thread.py"
"$PY" "$ROOT/tests/test_prompt.py"
"$PY" "$ROOT/tests/test_round_end.py"
"$PY" "$ROOT/tests/test_logout.py"
"$PY" "$ROOT/tests/test_claim.py"
"$PY" "$ROOT/tests/test_login.py"
"$PY" "$ROOT/tests/test_state.py"
"$PY" "$ROOT/tests/test_cli.py"
"$PY" "$ROOT/tests/test_dm.py"
"$PY" "$ROOT/tests/test_dm_reply.py"
"$PY" "$ROOT/tests/test_notes.py"
"$PY" "$ROOT/tests/test_config_migration.py"
"$PY" "$ROOT/tests/test_round_dm.py"
echo "all tests OK"
