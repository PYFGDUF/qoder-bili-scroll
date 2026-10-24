#!/usr/bin/env python3
"""插件清单、命令 TOML、SKILL frontmatter 与 ZIP 结构校验。"""
from __future__ import annotations

import json
import re
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    plugin = json.loads((ROOT / ".qoder-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "bili-scroll"
    assert plugin.get("version")
    assert (ROOT / plugin["skills"]).is_dir()
    for path in plugin.get("agents") or []:
        assert (ROOT / path).is_file(), path
    assert (ROOT / plugin["mcpServers"]).is_file()
    assert (ROOT / plugin["logo"]).is_file()

    mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    server = mcp["mcpServers"]["bili-scroll"]
    assert "${QODER_PLUGIN_ROOT}/bin/bili-mcp" in server["command"]
    assert int(server["timeout"]) >= 60000

    commands = sorted((ROOT / "commands").glob("*.toml"))
    assert len(commands) >= 7
    for path in commands:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert data.get("description") and data.get("prompt"), path

    skill = (ROOT / "skills/bili-scroll/SKILL.md").read_text(encoding="utf-8")
    front = re.match(r"^---\n(.*?)\n---\n", skill, re.S)
    assert front, "SKILL.md frontmatter missing"
    body = front.group(1)
    assert re.search(r"^name:\s*bili-scroll$", body, re.M)
    desc = re.search(r"^description:\s*(.+)$", body, re.M)
    assert desc and desc.group(1).startswith('"') and desc.group(1).endswith('"'), desc.group(0) if desc else ""
    for ref in re.findall(r"\]\((references/[^)]+)\)", skill):
        assert (ROOT / "skills/bili-scroll" / ref).is_file(), ref

    agent = (ROOT / "agents/bili-duty.md").read_text(encoding="utf-8")
    assert agent.startswith("---\n") and "skills:" in agent and "bili-scroll" in agent

    zips = sorted((ROOT / "dist").glob("bili-scroll-*.zip"))
    if zips:
        with zipfile.ZipFile(zips[-1]) as archive:
            names = set(archive.namelist())
        for required in (
            ".qoder-plugin/plugin.json",
            ".mcp.json",
            "bin/bili-mcp",
            "bin/bootstrap.sh",
            "bin/lib/dm.py",
            "bin/lib/uid.py",
            "bin/lib/notes.py",
            "commands/bili-dm.toml",
            "commands/bili-watched.toml",
            "skills/bili-scroll/SKILL.md",
            "vendor/bilibili-mcp/mcp_server.py",
        ):
            assert required in names, required
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)

    print("manifest/frontmatter/zip OK")


if __name__ == "__main__":
    main()
