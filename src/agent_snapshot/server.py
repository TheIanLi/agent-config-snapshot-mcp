"""MCP Server 入口 — 注册 4 个工具：snapshot、list_snapshots、diff_snapshot、rollback。"""

import logging

from mcp.server.fastmcp import FastMCP

from agent_snapshot.config import load_config
from agent_snapshot.snapshot import (
    create_snapshot,
    list_snapshots as _list_snapshots,
    diff_snapshot as _diff_snapshot,
    rollback as _rollback,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastMCP("agent-config-snapshot-mcp")


def get_config():
    cfg = load_config()
    logger.info("配置已加载，保护 %d 个文件", len(cfg.protected_files))
    return cfg


@app.tool()
async def snapshot(label: str) -> str:
    """对指定标签的配置文件拍一份快照。

    Args:
        label: 配置文件标签，如 "主配置"、"API密钥" 等
    """
    cfg = get_config()
    pf = cfg.get_by_label(label)
    if pf is None:
        available = ", ".join(p.label for p in cfg.protected_files)
        return f"未找到标签「{label}」。可用标签: {available}"

    snap_path = create_snapshot(
        pf, cfg.snapshot_dir, cfg.max_snapshots_per_file, reason="manual"
    )
    return f"快照已创建: {snap_path}"


@app.tool()
async def list_snapshots(label: str) -> str:
    """列出指定标签配置文件的所有历史快照。

    Args:
        label: 配置文件标签
    """
    cfg = get_config()
    pf = cfg.get_by_label(label)
    if pf is None:
        available = ", ".join(p.label for p in cfg.protected_files)
        return f"未找到标签「{label}」。可用标签: {available}"

    snaps = _list_snapshots(pf, cfg.snapshot_dir)
    if not snaps:
        return f"「{label}」尚无快照。"

    lines = [f"「{label}」快照列表（最新在前）:"]
    for s in snaps:
        size_kb = s["size"] / 1024
        lines.append(
            f"  #{s['index']}  {s['timestamp']}  {size_kb:.1f}KB  [{s['reason']}]  {s['filename']}"
        )
    return "\n".join(lines)


@app.tool()
async def diff_snapshot(label: str, index: int) -> str:
    """对比当前文件和指定快照的差异。

    Args:
        label: 配置文件标签
        index: 快照序号（从 list_snapshots 获取）
    """
    cfg = get_config()
    pf = cfg.get_by_label(label)
    if pf is None:
        available = ", ".join(p.label for p in cfg.protected_files)
        return f"未找到标签「{label}」。可用标签: {available}"

    return _diff_snapshot(pf, cfg.snapshot_dir, index)


@app.tool()
async def rollback(label: str, index: int) -> str:
    """回滚到指定快照版本。回滚前自动对当前版本拍快照防误操作。

    Args:
        label: 配置文件标签
        index: 目标快照序号（从 list_snapshots 获取）
    """
    cfg = get_config()
    pf = cfg.get_by_label(label)
    if pf is None:
        available = ", ".join(p.label for p in cfg.protected_files)
        return f"未找到标签「{label}」。可用标签: {available}"

    result = _rollback(pf, cfg.snapshot_dir, index, cfg.max_snapshots_per_file)
    return (
        f"已回滚「{label}」到快照 #{result['rolled_back_to']}\n"
        f"快照时间: {result['snapshot_timestamp']}\n"
        f"回滚前自动快照: {result['safe_snapshot']}"
    )


def main():
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
