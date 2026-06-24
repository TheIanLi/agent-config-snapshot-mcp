[![en](https://img.shields.io/badge/lang-English-red)](README.md)
[![zh](https://img.shields.io/badge/语言-中文-green)](README_zh.md)

# Agent Config Snapshot MCP

> Git-style snapshots for AI agent config files — your safety net when agents go rogue.

[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![CI](https://github.com/TheIanLi/agent-config-snapshot-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/TheIanLi/agent-config-snapshot-mcp/actions/workflows/test.yml)

## Why This Exists

One day, I asked my AI agent to tweak a config. It modified `config.yaml`, rewrote `SOUL.md`, and then—the agent broke.

Not a "hmm, this setting feels off" kind of broke. A full-on personality corruption, startup failure, can't-even-read-the-error-messages kind of broke. I spent half a day manually undoing a dozen scattered changes. That was the moment I realized: **the same agent that helps me manage configs can destroy them in seconds.**

This tool grew out of that disaster. The idea is simple—virtual machine snapshots, but for your agent's config files. Snapshot before every change, roll back when things go wrong. One command, instant recovery.

## What It Does

**agent-config-snapshot-mcp** gives your config files a time machine:

- Take snapshots manually or automatically
- Compare the current version against any historical snapshot
- Roll back to any previous version with a single command (auto-saves current state first)

Works as a standalone CLI or as an MCP Server your AI agent can call directly.

## Demo

[![Demo](https://img.youtube.com/vi/zScgACZecXM/maxresdefault.jpg)](https://youtu.be/zScgACZecXM)

> Full walkthrough: init → snapshot → simulate corruption → auto-snapshot → history → diff → rollback → verify.

## Features

| Feature | CLI | MCP | Description |
|---------|:---:|:---:|-------------|
| Snapshot | ✓ | ✓ | Save the current state of a config file |
| History | ✓ | ✓ | List all snapshots, newest first |
| Diff | ✓ | ✓ | Unified diff between current file and any snapshot |
| Rollback | ✓ | ✓ | Restore any snapshot (auto-saves current version as a safety net) |
| File Watcher | ✓ | — | watchdog-based real-time monitoring, auto-snapshot on change |
| Daily Backup | ✓ | — | Scheduled daily snapshots (e.g. for MEMORY.md) |
| Retention | ✓ | ✓ | Configurable max snapshots per file, auto-prunes oldest |

## Quick Start

```bash
# 1. Clone
git clone https://github.com/TheIanLi/agent-config-snapshot-mcp.git
cd agent-config-snapshot-mcp

# 2. Install dependencies
uv sync

# 3. Initialize with a preset
agent-snapshot init --preset hermes

# 4. Take your first snapshot
agent-snapshot snapshot hermes_data/SOUL

# 5. View history
agent-snapshot list hermes_data/SOUL

# 6. (Optional) Start background watcher
agent-snapshot watch --daemon
```

> **Platform:** Linux / macOS / Windows

## Installation

```bash
git clone https://github.com/TheIanLi/agent-config-snapshot-mcp.git
cd agent-config-snapshot-mcp
uv sync
```

Requires Python ≥ 3.10.

## CLI Reference

### `init` — Initialize Configuration

```bash
# Use a preset (recommended)
agent-snapshot init --preset hermes       # Hermes Agent
agent-snapshot init --preset openclaw     # OpenClaw
agent-snapshot init --preset claude-code  # Claude Code
agent-snapshot init --preset gemini       # Gemini CLI
agent-snapshot init --preset codex        # Codex CLI

# Interactive scan (auto-detect installed agents)
agent-snapshot init

# Scan extra directories — works for ANY agent, even ones not built in
agent-snapshot init --scan-dir ~/.my-agent

# Non-interactive: protect everything detected, no prompts
agent-snapshot init --all
```

**How the scan works:** detection is filename-agnostic — it scans each agent directory and
collects every small config-shaped file (`.yaml` / `.yml` / `.json` / `.md` / `.toml` / `.env`,
under 1 MB), skipping bulky `sessions` / `logs` / `cache` folders. So an agent's config can be
named `config`, `settings.json`, or `config.toml` — it's picked up either way. You select
**by directory** (Enter = select all); `--all` skips the prompt entirely.

Generates `snapshot-config.yaml` in the current directory. Set `SNAPSHOT_CONFIG` env var to use a custom path.

> Each file gets a unique `label`. Duplicate labels are rejected at load time (they would otherwise overwrite each other's snapshots).

### `snapshot` — Take a Manual Snapshot

```bash
agent-snapshot snapshot <label>
# Examples
agent-snapshot snapshot hermes_data/SOUL
agent-snapshot snapshot hermes_data/.env
```

### `list` — View Snapshot History

```bash
agent-snapshot list <label>
# Sample output:
#   #1  2026-06-02T15:27:43+08:00  2.5KB  [on_change]  SOUL.md.snapshot.20260602_072743.on_change
#   #2  2026-06-02T15:22:52+08:00  2.8KB  [on_change]  SOUL.md.snapshot.20260602_072252.on_change
#   #3  2026-06-02T15:07:17+08:00  2.8KB  [on_change]  SOUL.md.snapshot.20260602_070717.on_change
```

Newest first. Indices start at 1 and are used by `diff` and `rollback`.

### `diff` — Compare Changes

```bash
agent-snapshot diff <label> <index>
# Example: compare current SOUL.md against snapshot #3
agent-snapshot diff hermes_data/SOUL 3
```

Outputs a unified diff showing exactly what changed.

### `rollback` — Restore a Snapshot

```bash
agent-snapshot rollback <label> <index>
# Example: restore SOUL.md to snapshot #3
agent-snapshot rollback hermes_data/SOUL 3
```

**Auto-saves a safe snapshot** of the current version before rolling back—you can always undo the undo.

### `watch` — Start File Watcher

```bash
# Foreground (Ctrl+C to stop)
agent-snapshot watch

# Background daemon
agent-snapshot watch --daemon
```

Monitors files based on their `watch` mode in the config:
- `on_change` — auto-snapshot after 5-second debounce on file modification
- `daily` — scheduled snapshot at the configured time (default 04:00)

## Snapshot Types

Each snapshot filename includes a reason tag:

| Tag | Trigger |
|-----|---------|
| `baseline` | Initial snapshot on watcher startup |
| `on_change` | Auto-triggered by file watcher |
| `daily` | Scheduled daily task |
| `manual` | Manual snapshot command |
| `safe` | Auto-saved before rollback (safety net) |

Naming format: `{filename}.snapshot.{timestamp}.{reason}`

## Configuration

```yaml
# snapshot-config.yaml
protected_files:
  - path: ~/.hermes_data/config.yaml
    label: hermes_data/config          # Unique ID for CLI/MCP reference
    watch: on_change                   # on_change | daily | manual
  - path: ~/.hermes_data/SOUL.md
    label: hermes_data/SOUL
    watch: on_change
  - path: ~/.hermes_data/.env
    label: hermes_data/.env
    watch: on_change
  - path: ~/.hermes_data/memories/MEMORY.md
    label: hermes_data/memories/MEMORY
    watch: daily

snapshot_dir: ~/.agent-snapshots/      # Where snapshots are stored
daily_time: "04:00"                    # Daily schedule (HH:MM)
retention:
  max_snapshots_per_file: 50           # Max snapshots before pruning
```

## MCP Integration

Let your AI agent snapshot its own configs before modifying them:

```json
{
  "mcpServers": {
    "agent-config-snapshot": {
      "command": "uv",
      "args": ["run", "agent-snapshot-mcp"],
      "cwd": "/path/to/agent-config-snapshot-mcp"
    }
  }
}
```

Your agent gains 4 MCP tools: `snapshot`, `list_snapshots`, `diff_snapshot`, `rollback`.

> **Heads-up:** MCP is convenient, not essential. If your agent is already broken, its MCP server might not start either. The CLI (`agent-snapshot rollback`) runs independently and is the real lifeline.

## Safety Design

| Mechanism | Detail |
|-----------|--------|
| Pre-rollback snapshot | Every `rollback` saves the current version as a `safe` snapshot first—zero data loss risk |
| Retention cap | Configurable max snapshots per file; oldest pruned automatically |
| Collision-proof | Multiple snapshots within the same second get counter suffixes, never overwrite |
| Permission hardening | Snapshot directory enforced to `700` (owner-only) since it may contain secrets like `.env`. On Windows, uses ACL via `icacls` to restrict access to current user only. |
| Path sanitization | Dangerous characters in labels (`../`, `/`, `\`) are automatically replaced to prevent path traversal |

## Project Structure

```
agent-config-snapshot-mcp/
├── src/agent_snapshot/
│   ├── server.py          # MCP Server (4 tools)
│   ├── snapshot.py        # Core logic: create / list / diff / rollback
│   ├── config.py          # Config types and loading
│   ├── cli.py             # Command-line interface
│   ├── watcher.py         # File watcher daemon
│   ├── compat.py          # Cross-platform compatibility layer (POSIX / Windows)
│   └── __main__.py        # python -m entry point
├── presets/               # Preset templates (hermes / openclaw / claude-code)
├── tests/                 # Test suite
├── snapshot-config.yaml   # Your config (generated by init)
└── pyproject.toml
```

## Dependencies

- Python ≥ 3.10
- [mcp](https://github.com/modelcontextprotocol/python-sdk) — MCP protocol
- [PyYAML](https://pyyaml.org/) — Config file parsing
- [watchdog](https://github.com/gorakhargosh/watchdog) — File system monitoring
- [schedule](https://github.com/dbader/schedule) — Cron-like scheduling
- [filelock](https://github.com/tox-dev/filelock) — Cross-platform file locking (POSIX flock / Windows msvcrt)

## License

MIT License
