# Agent Config Snapshot MCP

> Git-style snapshots for AI agent config files — so you can safely let agents modify your settings.

[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

## Why?

AI agents (Claude Code, Hermes, OpenClaw, etc.) modify config files at runtime — switching models, tweaking parameters, writing to memory. One wrong move and your carefully tuned setup is gone.

**agent-config-snapshot-mcp** gives your config files a time machine: snapshot on demand, diff against history, and rollback with one command. Runs as an MCP Server so agents can protect themselves.

> Real pain point: Hermes' `config.yaml` got corrupted by an agent edit. No way back.

## Features

### MCP Server (called by AI agents)

| Tool | Description |
|------|-------------|
| `snapshot` | Take a snapshot of a config file by label |
| `list_snapshots` | List all historical snapshots for a file |
| `diff_snapshot` | Show unified diff between current file and any snapshot |
| `rollback` | Restore a snapshot (auto-saves current version first as a safety net) |

### CLI (manual use)

```bash
agent-snapshot snapshot main-config     # take a snapshot
agent-snapshot list main-config         # view history
agent-snapshot diff main-config 3       # compare with snapshot #3
agent-snapshot rollback main-config 2   # roll back to snapshot #2
agent-snapshot watch                    # start file watcher (foreground)
agent-snapshot watch --daemon           # start file watcher (background)
```

### File Watcher Daemon

Three watch modes built-in:

- **`on_change`** — auto-snapshot on file modification (ideal for `.env`, `config.yaml`)
- **`daily`** — scheduled daily snapshot (ideal for `MEMORY.md`, `USER.md`)
- **`manual`** — only triggered explicitly

The daemon runs `watchdog` for real-time file monitoring and `schedule` for daily tasks. No extra plugins needed — both are bundled dependencies.

### Preset Templates

Ready-to-use presets for popular agents:

```bash
agent-snapshot init --preset hermes      # Hermes Agent
agent-snapshot init --preset openclaw    # OpenClaw
agent-snapshot init --preset claude-code # Claude Code
```

Or run interactive scan to auto-detect installed agent directories and pick what to protect.

### Safety Design

- **Auto-snapshot before rollback**: every rollback saves the current version as a `safe` snapshot first — you never lose data
- **Retention cap**: configurable max snapshots per file; oldest get pruned automatically
- **Collision-proof**: multiple snapshots within the same second get counter suffixes, never overwrite
- **Permission hardening**: snapshot directory is enforced to `700` (owner-only) since it may contain secrets like `.env`

## Snapshot Types (reason tag)

| Tag | Trigger |
|-----|---------|
| `manual` | Manual snapshot command |
| `on_change` | Auto-triggered by file watcher |
| `daily` | Scheduled daily task |
| `safe` | Auto-saved before rollback |
| `baseline` | Initial baseline on watcher startup |

## Installation

```bash
git clone https://github.com/TheIanLi/agent-config-snapshot-mcp.git
cd agent-config-snapshot-mcp
uv sync
```

## Quick Start

### 1. Initialize config

```bash
# Use a preset
agent-snapshot init --preset hermes

# Or interactive scan
agent-snapshot init
```

This creates `snapshot-config.yaml` in the current directory.

### 2. Wire up your agent

Add to your agent's MCP config:

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

### 3. Use it

Your agent can now call the four MCP tools to manage its own config files. You can also use the CLI:

```bash
# Take a snapshot
agent-snapshot snapshot api-keys

# View history
agent-snapshot list api-keys
#   #1  2026-06-02T04:00:00Z  0.5KB  [daily]      .env.snapshot.20260602_040000.daily
#   #2  2026-06-01T15:30:12Z  0.5KB  [on_change]   .env.snapshot.20260601_153012.on_change

# See what changed since 3 days ago
agent-snapshot diff api-keys 2

# Nope, roll it back
agent-snapshot rollback api-keys 2
```

### 4. (Optional) Start the background watcher

```bash
agent-snapshot watch --daemon
```

Config files with `watch: on_change` get auto-snapshotted on every edit. Files with `watch: daily` get archived every day at the configured time (default 04:00).

## Configuration

```yaml
# snapshot-config.yaml
protected_files:
  - path: ~/.hermes_data/.env
    label: api-keys              # short label for CLI/MCP reference
    watch: on_change             # on_change | daily | manual
  - path: ~/.hermes_data/memories/MEMORY.md
    label: agent-memory
    watch: daily

snapshot_dir: ~/.agent-snapshots/    # where snapshots are stored
daily_time: "04:00"                  # daily snapshot schedule
retention:
  max_snapshots_per_file: 50         # max snapshots per file before pruning
```

Snapshot file naming: `{original_name}.snapshot.{timestamp}.{reason}`

## Dependencies

- Python ≥ 3.10
- `mcp[cli]` — MCP protocol
- `pyyaml` — config parsing
- `watchdog` — file system monitoring
- `schedule` — cron-like scheduling

## Project Structure

```
agent-config-snapshot-mcp/
├── snapshot-config.yaml      # your config file
├── pyproject.toml
├── presets/                  # preset templates
│   ├── hermes.yaml
│   ├── openclaw.yaml
│   └── claude-code.yaml
├── src/agent_snapshot/
│   ├── server.py             # MCP Server (4 tools)
│   ├── snapshot.py           # core snapshot logic
│   ├── config.py             # config loading
│   ├── cli.py                # command-line interface
│   ├── watcher.py            # file watcher daemon
│   └── __main__.py           # python -m entry point
└── tests/
    ├── test_snapshot.py
    ├── test_cli.py
    └── test_watcher.py
```

## Use Cases

- Your agent frequently edits configs and you want an undo button
- Multi-agent environments (Hermes + OpenClaw + Claude Code) where configs overlap
- You've had configs corrupted by agent edits before and won't risk it again
- You want a config change history for auditing and troubleshooting

## License

MIT
