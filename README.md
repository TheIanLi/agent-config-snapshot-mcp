# Agent Config Snapshot MCP

> AI agent 核心配置文件快照备份与回滚工具 —— 让 agent 改配置不再恐惧。

[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

## 为什么需要它？

AI agent（Claude Code、Hermes、OpenClaw 等）在运行时会修改配置文件——改模型、调参数、写记忆。一次误操作就可能把精心调好的配置毁掉。

**agent-config-snapshot-mcp** 给你的配置文件加上"时间机器"：随时快照、对比差异、一键回滚。作为 MCP Server 运行，agent 可以直接调用它来保护自己。

> 真实痛点：Hermes 的 `config.yaml` 被 agent 改坏过，回不来。

## 功能

### MCP Server（供 AI agent 调用）

| 工具 | 功能 |
|------|------|
| `snapshot` | 对指定标签的配置文件拍快照 |
| `list_snapshots` | 列出某配置文件的所有历史快照 |
| `diff_snapshot` | 对比当前文件与任意历史快照的差异（unified diff） |
| `rollback` | 回滚到指定快照（回滚前自动拍一张安全快照防误操作） |

### CLI 模式（手动操作）

```bash
agent-snapshot snapshot "主配置"     # 拍快照
agent-snapshot list "主配置"         # 看历史
agent-snapshot diff "主配置" 3       # 对比差异
agent-snapshot rollback "主配置" 2   # 回滚
agent-snapshot watch                 # 后台自动监听
```

### 文件监听守护进程

支持三种监听模式：
- **`on_change`** — 文件被修改时自动拍快照（适合 .env、config.yaml）
- **`daily`** — 每天定时拍快照（适合 MEMORY.md、USER.md）
- **`manual`** — 仅手动触发

### 预设模板

开箱即用的预设：

```bash
agent-snapshot init --preset hermes      # Hermes Agent
agent-snapshot init --preset openclaw    # OpenClaw
agent-snapshot init --preset claude-code # Claude Code
```

也可以交互式扫描自动检测已安装的 agent 目录。

### 安全设计

- **回滚前自动快照**：`rollback` 执行前会对当前版本拍一张 `safe` 快照，绝不丢数据
- **保留上限**：可配置每文件最大快照数，超出自动清理最老的
- **防覆盖**：同一秒内多次快照自动加计数器后缀

## 安装

```bash
git clone https://github.com/TheIanLi/agent-config-snapshot-mcp.git
cd agent-config-snapshot-mcp
uv sync
```

## 快速开始

### 1. 初始化配置

```bash
# 使用预设模板
agent-snapshot init --preset hermes

# 或者交互扫描
agent-snapshot init
```

会在当前目录生成 `snapshot-config.yaml`。

### 2. 配置 Claude Code / Hermes / OpenClaw

在你的 agent 的 MCP 配置中添加：

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

### 3. 开始使用

Agent 可以自动调用四个工具来管理自己的配置文件。你也可以手动操作：

```bash
# 拍一张快照
agent-snapshot snapshot "API密钥"

# 查看历史
agent-snapshot list "API密钥"
#   #1  2026-06-02T04:00:00Z  0.5KB  [daily]    .env.snapshot.20260602_040000.daily
#   #2  2026-06-01T15:30:12Z  0.5KB  [on_change] .env.snapshot.20260601_153012.on_change

# 看看和三天前有什么区别
agent-snapshot diff "API密钥" 2

# 不对，回滚
agent-snapshot rollback "API密钥" 2
```

### 4. （可选）后台自动监听

```bash
agent-snapshot watch --daemon
```

配置文件被改时自动拍快照，记忆文件每天凌晨 4 点自动存档。

## 配置文件

```yaml
# snapshot-config.yaml
protected_files:
  - path: ~/.hermes_data/.env
    label: "API密钥"          # 简短标签，用于命令引用
    watch: on_change          # on_change | daily | manual
  - path: ~/.hermes_data/memories/MEMORY.md
    label: "记忆笔记"
    watch: daily

snapshot_dir: ~/.agent-snapshots/   # 快照存储目录
daily_time: "04:00"                 # daily 模式执行时间
retention:
  max_snapshots_per_file: 50        # 每文件保留上限
```

快照文件命名格式：`{原文件名}.snapshot.{时间戳}.{reason}`

## 快照类型（reason 标签）

| 标签 | 触发方式 |
|------|----------|
| `manual` | 手动调用 snapshot |
| `on_change` | 文件监听自动触发 |
| `daily` | 定时任务 |
| `safe` | 回滚前的自动保护快照 |
| `baseline` | 初始基线（预留） |

## 依赖

- Python ≥ 3.10
- `mcp[cli]` — MCP 协议
- `pyyaml` — 配置解析
- `watchdog` — 文件监听
- `schedule` — 定时任务

## 目录结构

```
agent-config-snapshot-mcp/
├── snapshot-config.yaml      # 配置文件
├── pyproject.toml
├── presets/                  # 预设模板
│   ├── hermes.yaml
│   ├── openclaw.yaml
│   └── claude-code.yaml
├── src/agent_snapshot/
│   ├── server.py             # MCP Server（4 个工具）
│   ├── snapshot.py           # 快照核心逻辑
│   ├── config.py             # 配置加载
│   ├── cli.py                # 命令行界面
│   ├── watcher.py            # 文件监听守护进程
│   └── __main__.py           # python -m 入口
└── tests/
    ├── test_snapshot.py
    ├── test_cli.py
    └── test_watcher.py
```

## 适用场景

- 你的 agent 频繁修改配置，你想有"撤销"能力
- 多 agent 共存的复杂环境（Hermes + OpenClaw + Claude Code）
- 配置文件被 agent 改坏过，不想再来一次
- 想要配置变更历史，方便审计和排查

## License

MIT
