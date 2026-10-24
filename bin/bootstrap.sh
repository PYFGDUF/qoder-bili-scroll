#!/bin/sh
# 确保运行时 venv 存在并返回其 python 路径（stdout 只输出路径）。
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PLUGIN_ROOT=$(dirname "$SCRIPT_DIR")
HOME_DIR="${BILI_SCROLL_HOME:-$HOME/.qoder-bili-scroll}"

mkdir -p "$HOME_DIR"
chmod 700 "$HOME_DIR" 2>/dev/null || true

PY="$HOME_DIR/.venv/bin/python"
LOG="$HOME_DIR/setup.log"

deps_ok() {
  [ -x "$PY" ] || return 1
  "$PY" -c "import bilibili_api, mcp, qrcode" >/dev/null 2>&1
}

find_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

find_python() {
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        command -v "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

if ! deps_ok; then
  UV=$(find_uv || true)
  PYBIN=$(find_python || true)
  if [ -z "$UV" ] && [ -z "$PYBIN" ]; then
    echo "bili-scroll: 需要 uv 或 Python 3.10+ 来安装依赖。" >&2
    echo "bili-scroll: 请先安装 uv（推荐）或 Python 3.10+；安装日志会写到 $LOG" >&2
    exit 3
  fi

  LOCK="$HOME_DIR/.install.lock"
  i=0
  while ! mkdir "$LOCK" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -gt 150 ]; then
      echo "bili-scroll: 另一个安装进程长时间未结束（$LOCK）。" >&2
      exit 4
    fi
    sleep 2
  done
  trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT INT TERM

  # 拿到锁后重新检查，避免重复安装
  if ! deps_ok; then
    echo "bili-scroll: 首次运行，正在创建 Python 环境并安装依赖（日志：$LOG）..." >&2
    {
      echo
      echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) bootstrap ==="
    } >>"$LOG"
    if [ -n "$UV" ]; then
      if ! "$UV" venv --python 3.12 "$HOME_DIR/.venv" >>"$LOG" 2>&1; then
        echo "bili-scroll: 创建 venv 失败。日志尾部：" >&2
        tail -20 "$LOG" >&2 || true
        exit 5
      fi
      if ! "$UV" pip install --python "$PY" -r "$PLUGIN_ROOT/vendor/bilibili-mcp/requirements.txt" >>"$LOG" 2>&1; then
        echo "bili-scroll: 安装依赖失败。日志尾部：" >&2
        tail -20 "$LOG" >&2 || true
        exit 6
      fi
    else
      if ! "$PYBIN" -m venv "$HOME_DIR/.venv" >>"$LOG" 2>&1; then
        echo "bili-scroll: 创建 venv 失败（$PYBIN）。日志尾部：" >&2
        tail -20 "$LOG" >&2 || true
        exit 5
      fi
      "$PY" -m ensurepip --upgrade >>"$LOG" 2>&1 || true
      if ! "$PY" -m pip install -r "$PLUGIN_ROOT/vendor/bilibili-mcp/requirements.txt" >>"$LOG" 2>&1; then
        echo "bili-scroll: 安装依赖失败（$PYBIN）。日志尾部：" >&2
        tail -20 "$LOG" >&2 || true
        exit 6
      fi
    fi
    if ! deps_ok; then
      echo "bili-scroll: 依赖安装后仍无法导入，请查看 $LOG" >&2
      exit 7
    fi
    echo "bili-scroll: 依赖安装完成。" >&2
  fi
fi

echo "$PY"
