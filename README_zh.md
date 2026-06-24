[![en](https://img.shields.io/badge/lang-English-red)](README.md)
[![zh](https://img.shields.io/badge/语言-中文-green)](README_zh.md)

# Agent Config Snapshot MCP

> Git 风格的 AI  agent 配置文件快照——agents 翻车时的安全网。

[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![CI](https://github.com/TheIanLi/agent-config-snapshot-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/TheIanLi/agent-config-snapshot-mcp/actions/workflows/test.yml)

## 为什么会有这个项目

有一次我让 AI agent 调个配置。它改了 `config.yaml`，重写了 `SOUL.md`——然后 agent 炸了。

不是"嗯这个设置有点怪"那种炸。是人格损坏、启动失败、连报错都读不了的那种炸。我花了半天时间手工回滚了十几个散落的改动。那一刻我意识到：**帮我管理配置的同一个 agent，可以在几秒内毁掉它们。**

这个工具就是从那次灾难里长出来的。思路很简单——把虚拟机的快照概念搬到 agent 配置文件上。每次修改前拍个快照，出事了就回滚。一条命令，瞬间恢复。

## 能做什么

**agent-config-snapshot-mcp** 给你的配置文件装了一台时光机：

- 手动或自动拍摄快照
- 对比当前版本和历史快照的差异
- 一条命令回滚到任意历史版本（回滚前自动保存当前状态）

既是独立 CLI 工具，也可以作为 MCP Server 让你的 AI agent 直接调用。

## 演示

[![Demo](https://img.youtube.com/vi/zScgACZecXM/maxresdefault.jpg)](https://youtu.be/zScgACZecXM)

> 完整流程：init → snapshot → 模拟损坏 → auto-snapshot → history → diff → rollback → 验证恢复。

## 功能

| 功能 | CLI | MCP | 说明 |
|---------|:---:|:---:|-------------|
| 快照 | ✓ | ✓ | 保存配置文件的当前状态 |
| 历史 | ✓ | ✓ | 列出所有快照，最新在前 |
| 对比 | ✓ | ✓ | 当前文件与任意快照的 unified diff |
| 回滚 | ✓ | ✓ | 恢复到任意快照（自动先把当前版本存为安全快照） |
| 文件监控 | ✓ | — | 基于 watchdog 的实时监控，文件变动自动快照 |
| 每日备份 | ✓ | — | 定时每日快照（如 MEMORY.md） |
| 保留策略 | ✓ | ✓ | 每个文件可配置最大快照数，自动清理旧快照 |

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/TheIanLi/agent-config-snapshot-mcp.git
cd agent-config-snapshot-mcp

# 2. 安装依赖
uv sync

# 3. 用预设模板初始化
agent-snapshot init --preset hermes

# 4. 拍第一张快照
agent-snapshot snapshot hermes_data/SOUL

# 5. 查看历史
agent-snapshot list hermes_data/SOUL

# 6. （可选）启动后台监控
agent-snapshot watch --daemon
```

> **平台：** Linux / macOS / Windows

## 安装

```bash
git clone https://github.com/TheIanLi/agent-config-snapshot-mcp.git
cd agent-config-snapshot-mcp
uv sync
```

需要 Python ≥ 3.10。

## CLI 参考

### `init` — 初始化配置

```bash
# 使用预设模板（推荐）
agent-snapshot init --preset hermes       # Hermes Agent
agent-snapshot init --preset openclaw     # OpenClaw
agent-snapshot init --preset claude-code  # Claude Code
agent-snapshot init --preset gemini       # Gemini CLI
agent-snapshot init --preset codex        # Codex CLI

# 交互式扫描（自动检测已安装的 agents）
agent-snapshot init

# 扫描额外目录 —— 支持任意 agent，哪怕不在内置列表里
agent-snapshot init --scan-dir ~/.my-agent

# 非交互：直接保护检测到的全部文件，不再逐项询问
agent-snapshot init --all
```

**扫描原理（重要）：** 检测**不认文件名**，而是扫描每个 agent 目录、把所有"长得像配置的小文件"
（`.yaml` / `.yml` / `.json` / `.md` / `.toml` / `.env`，且 < 1MB）都收集起来，
自动跳过 `sessions` / `logs` / `cache` 等大目录。所以某个 agent 的配置叫 `config`、
`settings.json` 还是 `config.toml` 都能被扫到。选择时是**按目录选**（回车 = 全选所有目录），
`--all` 则完全跳过询问、一把全保护。

在当前目录生成 `snapshot-config.yaml`。可通过环境变量 `SNAPSHOT_CONFIG` 指定自定义路径。

> 每个文件有唯一的 `label`。重复的 label 会在加载配置时直接报错（否则它们的快照会互相覆盖）。

### `snapshot` — 手动快照

```bash
agent-snapshot snapshot <label>
# 示例
agent-snapshot snapshot hermes_data/SOUL
agent-snapshot snapshot hermes_data/.env
```

### `list` — 查看快照历史

```bash
agent-snapshot list <label>
# 输出示例：
#   #1  2026-06-02T15:27:43+08:00  2.5KB  [on_change]  SOUL.md.snapshot.20260602_072743.on_change
#   #2  2026-06-02T15:22:52+08:00  2.8KB  [on_change]  SOUL.md.snapshot.20260602_072252.on_change
#   #3  2026-06-02T15:07:17+08:00  2.8KB  [on_change]  SOUL.md.snapshot.20260602_070717.on_change
```

最新在前。编号从 1 开始，供 `diff` 和 `rollback` 使用。

### `diff` — 对比变更

```bash
agent-snapshot diff <label> <index>
# 示例：对比当前 SOUL.md 与第 3 号快照
agent-snapshot diff hermes_data/SOUL 3
```

输出 unified diff 格式，清晰展示所有变化。

### `rollback` — 恢复快照

```bash
agent-snapshot rollback <label> <index>
# 示例：将 SOUL.md 恢复到第 3 号快照
agent-snapshot rollback hermes_data/SOUL 3
```

**回滚前会自动保存当前版本**为安全快照——你随时可以撤销这次回滚。

### `watch` — 启动文件监控

```bash
# 前台运行（Ctrl+C 停止）
agent-snapshot watch

# 后台守护进程
agent-snapshot watch --daemon
```

根据配置中的 `watch` 模式监控文件：
- `on_change` — 文件变动后防抖 5 秒自动快照
- `daily` — 定时快照（默认凌晨 4:00）

## 快照类型

每个快照文件名包含触发原因标签：

| 标签 | 触发方式 |
|-----|---------|
| `baseline` | 监控启动时的初始快照 |
| `on_change` | 文件监控自动触发 |
| `daily` | 定时每日任务 |
| `manual` | 手动快照命令 |
| `safe` | 回滚前自动保存的安全快照 |

命名格式：`{文件名}.snapshot.{时间戳}.{原因}`

## 配置

```yaml
# snapshot-config.yaml
protected_files:
  - path: ~/.hermes_data/config.yaml
    label: hermes_data/config          # 唯一标识，供 CLI/MCP 引用
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

snapshot_dir: ~/.agent-snapshots/      # 快照存储路径
daily_time: "04:00"                    # 每日快照时间（HH:MM）
retention:
  max_snapshots_per_file: 50           # 每个文件最大快照数，超出自动清理
```

## MCP 集成

让你的 AI agent 在修改配置前先自己拍个快照：

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

你的 agent 将获得 4 个 MCP 工具：`snapshot`、`list_snapshots`、`diff_snapshot`、`rollback`。

> **注意：** MCP 是锦上添花，不是救命稻草。如果 agent 已经崩了，它的 MCP server 大概率也起不来。CLI（`agent-snapshot rollback`）是独立运行的，这才是真正的救生索。

> **Windows 用户注意：** Windows 上使用 ACL（icacls）尽力收紧快照目录权限，但建议用户自行确认该目录仅自己可访问，因为快照里含 .env / auth.json 等明文敏感文件。

## 安全设计

| 机制 | 细节 |
|-----------|--------|
| 回滚前快照 | 每次 `rollback` 先把当前版本存为 `safe` 快照——零数据丢失风险 |
| 保留上限 | 每个文件可配置最大快照数，超出自动清理最早的 |
| 防碰撞 | 同一秒内多次快照自动加计数器后缀，绝不覆盖 |
| 权限加固 | 快照目录强制设为 `700`（仅属主可访问），因为可能包含 `.env` 等机密文件。Windows 上使用 ACL（icacls）限制仅当前用户可访问。 |
| 路径消毒 | 标签中的危险字符（`../`、`/`、`\`）自动替换，防止路径穿越 |

## 项目结构

```
agent-config-snapshot-mcp/
├── src/agent_snapshot/
│   ├── server.py          # MCP Server（4 个工具）
│   ├── snapshot.py        # 核心逻辑：创建 / 列表 / 对比 / 回滚
│   ├── config.py          # 配置类型与加载
│   ├── cli.py             # 命令行接口
│   ├── watcher.py         # 文件监控守护进程
│   ├── compat.py          # 跨平台兼容层（POSIX / Windows）
│   └── __main__.py        # python -m 入口
├── presets/               # 预设模板（hermes / openclaw / claude-code）
├── tests/                 # 测试套件
├── snapshot-config.yaml   # 你的配置文件（init 生成）
└── pyproject.toml
```

## 依赖

- Python ≥ 3.10
- [mcp](https://github.com/modelcontextprotocol/python-sdk) — MCP 协议
- [PyYAML](https://pyyaml.org/) — 配置文件解析
- [watchdog](https://github.com/gorakhargosh/watchdog) — 文件系统监控
- [schedule](https://github.com/dbader/schedule) — Cron 风格定时调度
- [filelock](https://github.com/tox-dev/filelock) — 跨平台文件锁（POSIX flock / Windows msvcrt）

## 许可证

MIT License
