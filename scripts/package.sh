#!/bin/sh
# 打包成 Qoder 插件 ZIP（ZIP 根目录直接是 .qoder-plugin/ 等）。
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(python3 -c "import json; print(json.load(open('$ROOT/.qoder-plugin/plugin.json'))['version'])")
OUT="$ROOT/dist/bili-scroll-$VERSION.zip"

mkdir -p "$ROOT/dist"
rm -f "$OUT"
cd "$ROOT"

zip -qr "$OUT" \
  .qoder-plugin \
  .mcp.json \
  skills \
  commands \
  agents \
  bin \
  vendor \
  assets \
  README.md \
  LICENSE \
  NOTICE \
  -x "*/__pycache__/*" "*.pyc" "*.DS_Store" "*/__pycache__"

python3 - "$OUT" "$ROOT" <<'PY'
import pathlib
import sys
import zipfile

out = sys.argv[1]
root = pathlib.Path(sys.argv[2])
required = [
    ".qoder-plugin/plugin.json",
    ".mcp.json",
    "skills/bili-scroll/SKILL.md",
    "skills/bili-scroll/references/charter.md",
    "bin/lib/mcp_server_safe.py",
    "bin/lib/gate.py",
    "bin/lib/inbox.py",
    "bin/lib/dm.py",
    "bin/lib/uid.py",
    "bin/lib/notes.py",
    "bin/bili-mcp",
    "commands/bili-run.toml",
    "commands/bili-dm.toml",
    "commands/bili-watched.toml",
]
with zipfile.ZipFile(out) as archive:
    for rel in required:
        assert archive.read(rel) == (root / rel).read_bytes(), f"stale zip entry: {rel}"
print("zip current")
PY

echo "$OUT"
