# Agent Config Snapshot MCP

> Git 式快照，给你的 AI agent 配置文件加一层保险。

[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

## 为什么你需要这个

有一天，我让我的 AI agent 帮我调个配置。它改了 `config.yaml`，改了 `SOUL.md`，然后——agent 崩了。

不是"配置不太对"那种崩。是整个 agent 人格错乱、启动失败、连报错都看不懂的那种崩。我花了大半天才把十几处改动逐一还原。那一刻我意识到：**agent 帮我管理配置的同时，也在随时可能毁掉它们。**

这个工具就是从那场灾难里长出来的。灵感来自虚拟机快照——改配置之前拍一张，改坏了就回滚，一秒恢复。

## 它做什么

**agent-config-snapshot-mcp** 给你指定的配置文件加了一个时间机器：

- 手动或自动拍快照
- 随时对比当前版本和任意历史版本
- 一键回滚（回滚前自动保存当前版本，防止二次灾难）

既可以作为 CLI 独立使用，也可以作为 MCP Server 接入你的 AI agent——让 agent 在改配置前自己拍快照。

## 功能一览

| 功能 | CLI | MCP Server | 说明 |
|------|:---:|:---:|------|
| 拍快照 | ✓ | ✓ | 手动或自动保存配置文件的当前版本 |
| 列历史 | ✓ | ✓ | 按时间倒序列出所有快照 |
| 对比差异 | ✓ | ✓ | unified diff 格式，直观看到改了什么 |
| 回滚 | ✓ | ✓ | 一键恢复到任意历史版本，回滚前自动存 safe 快照 |
| 文件监听 | ✓ | — | watchdog 实时监听，文件变动自动拍照 |
| 定时快照 | ✓ | — | 每日定时存档（适合 MEMORY.md 等持续变化的文件） |
| 保留策略 | ✓ | ✓ | 每个文件可配置快照数量上限，自动清理最老快照 |

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/TheIanLi/agent-config-snapshot-mcp.git
cd agent-config-snapshot-mcp

# 2. 安装依赖
uv sync

# 3. 初始化配置（预设模板，一键搞定）
agent-snapshot init --preset hermes

# 4. 拍第一张快照
agent-snapshot snapshot hermes_data/SOUL

# 5. 看一眼历史
agent-snapshot list hermes_data/SOUL

# 6. 启动后台监听（自动拍照，可不做）
agent-snapshot watch --daemon
```

## 安装

```bash
git clone https://github.com/TheIanLi/agent-config-snapshot-mcp.git
cd agent-config-snapshot-mcp
uv sync
```

依赖：Python ≥ 3.10

## CLI 命令详解

### `init` — 初始化配置文件

```bash
# 使用预设模板（推荐）
agent-snapshot init --preset hermes       # Hermes Agent
agent-snapshot init --preset openclaw     # OpenClaw
agent-snapshot init --preset claude-code  # Claude Code

# 交互式扫描（自动探测已安装的 agent 目录）
agent-snapshot init
```

默认在当前目录生成 `snapshot-config.yaml`。也可以通过 `SNAPSHOT_CONFIG` 环境变量指定位置。

### `snapshot` — 手动拍快照

```bash
agent-snapshot snapshot <label>
# 例如
agent-snapshot snapshot hermes_data/SOUL
agent-snapshot snapshot hermes_data/.env
```

### `list` — 查看历史快照

```bash
agent-snapshot list <label>
# 输出示例：
#   #1  2026-06-02T15:27:43+08:00  2.5KB  [on_change]  SOUL.md.snapshot.20260602_072743.on_change
#   #2  2026-06-02T15:22:52+08:00  2.8KB  [on_change]  SOUL.md.snapshot.20260602_072252.on_change
#   #3  2026-06-02T15:07:17+08:00  2.8KB  [on_change]  SOUL.md.snapshot.20260602_070717.on_change
```

最新在前。序号从 1 开始，用于 `diff` 和 `rollback`。

### `diff` — 对比差异

```bash
agent-snapshot diff <label> <index>
# 例如：对比当前 SOUL.md 和快照 #3
agent-snapshot diff hermes_data/SOUL 3
```

输出 unified diff 格式，一目了然。

### `rollback` — 回滚到指定版本

```bash
agent-snapshot rollback <label> <index>
# 例如：把 SOUL.md 恢复到快照 #3
agent-snapshot rollback hermes_data/SOUL 3
```

**回滚前自动拍 safe 快照**保护当前版本，回滚错了还能回滚回来。

### `watch` — 启动文件监听

```bash
# 前台运行（Ctrl+C 退出）
agent-snapshot watch

# 后台运行
agent-snapshot watch --daemon
```

根据配置文件中的 `watch` 字段自动拍照：
- `on_change` — 文件每次被修改后 5 秒自动拍快照
- `daily` — 每天固定时间自动拍快照（默认凌晨 4:00）

## 快照类型

快照文件名中包含触发原因标签：

| 标签 | 含义 |
|------|------|
| `baseline` | watcher 启动时的初始快照 |
| `on_change` | 文件监听自动触发 |
| `daily` | 每日定时任务 |
| `manual` | 手动执行 snapshot 命令 |
| `safe` | 回滚前自动保存的保护快照 |

命名格式：`{原文件名}.snapshot.{时间戳}.{原因}`

## 配置文件

```yaml
# snapshot-config.yaml
protected_files:
  - path: ~/.hermes_data/config.yaml
    label: hermes_data/config          # 唯一标识，用于 CLI/MCP 引用
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

snapshot_dir: ~/.agent-snapshots/      # 快照存储目录
daily_time: "04:00"                    # daily 模式的执行时间
retention:
  max_snapshots_per_file: 50           # 每个文件最多保留的快照数
```

## 接入你的 AI Agent（MCP）

让 agent 在修改配置前能自己拍快照：

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

Agent 将获得 4 个 MCP 工具：`snapshot`、`list_snapshots`、`diff_snapshot`、`rollback`。

> **注意：** MCP 是锦上添花，不是救命稻草。如果 agent 本身已经崩了，MCP Server 可能也起不来。真正救命的永远是 CLI——终端里敲 `agent-snapshot rollback`，不依赖任何 agent 子系统。

## 安全设计

| 机制 | 说明 |
|------|------|
| 回滚前自动快照 | 每次 `rollback` 先把当前版本存为 `safe` 快照，杜绝误操作 |
| 保留上限 | 可配置每文件最大快照数，最老的自动清理，不会撑爆磁盘 |
| 同秒防覆盖 | 同一秒内多次快照自动加计数器后缀，永不覆盖 |
| 权限硬化 | 快照目录强制 `700`（仅 owner 可读写），因为可能包含 `.env` 等密钥文件 |
| 路径净化 | label 中的 `../`、`/`、`\` 等危险字符自动替换，防止路径穿越 |

## 项目结构

```
agent-config-snapshot-mcp/
├── src/agent_snapshot/
│   ├── server.py          # MCP Server（4 个工具）
│   ├── snapshot.py        # 核心逻辑：创建/列表/对比/回滚
│   ├── config.py          # 配置类型定义与加载
│   ├── cli.py             # 命令行界面
│   ├── watcher.py         # 文件监听守护进程
│   └── __main__.py        # python -m 入口
├── presets/               # 预设模板（hermes / openclaw / claude-code）
├── tests/                 # 测试（30 个用例）
├── snapshot-config.yaml   # 你的配置（由 init 生成）
└── pyproject.toml
```

## 依赖

- Python ≥ 3.10
- [mcp](https://github.com/modelcontextprotocol/python-sdk) — MCP 协议
- [PyYAML](https://pyyaml.org/) — 配置文件解析
- [watchdog](https://github.com/gorakhargosh/watchdog) — 文件系统监听
- [schedule](https://github.com/dbader/schedule) — 定时任务

## License

MIT
