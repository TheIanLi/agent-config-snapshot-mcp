"""MCP Server 入口 — 注册 4 个工具：snapshot、list_snapshots、diff_snapshot、rollback。"""

import asyncio
import os
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from agent_snapshot.config import load_config, SnapshotConfig
from agent_snapshot.snapshot import (
    SnapshotReason,
    create_snapshot,
    list_snapshots as _list_snapshots,
    diff_snapshot as _diff_snapshot,
    rollback as _rollback,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastMCP("agent-config-snapshot-mcp")

# 配置缓存：仅在配置文件 mtime 变更时才重新加载
_cached_config: SnapshotConfig | None = None
_config_mtime: float = 0.0


def _config_path() -> Path:
    """查找配置文件：CWD → ~/.agent-snapshots/ → 报错。"""
    env_path = os.environ.get("SNAPSHOT_CONFIG")
    if env_path:
        return Path(env_path)

    cwd_path = Path("snapshot-config.yaml")
    if cwd_path.exists():
        return cwd_path

    import pwd
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    fallback = home / ".agent-snapshots" / "snapshot-config.yaml"
    if fallback.exists():
        logger.info("使用 fallback 配置: %s", fallback)
        return fallback

    raise FileNotFoundError(
        f"未找到 snapshot-config.yaml。已搜索:\n"
        f"  - {cwd_path.resolve()}\n"
        f"  - {fallback}\n"
        f"请设置 SNAPSHOT_CONFIG 环境变量或运行 agent-snapshot init"
    )


def get_config() -> SnapshotConfig:
    """获取配置，若文件未修改则复用缓存，否则重新加载。"""
    global _cached_config, _config_mtime
    path = _config_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _cached_config is not None and mtime == _config_mtime:
        return _cached_config
    _cached_config = load_config(str(path))
    _config_mtime = mtime
    logger.info("配置已加载（mtime=%.0f），保护 %d 个文件", mtime, len(_cached_config.protected_files))
    return _cached_config


@app.tool()
async def snapshot(label: str) -> str:
    """对指定标签的配置文件拍一份快照。

    Args:
        label: 配置文件标签，如 "hermes_data/SOUL"、"hermes_data/.env" 等
    """
    return await asyncio.to_thread(_snapshot_sync, label)


@app.tool()
async def list_snapshots(label: str) -> str:
    """列出指定标签配置文件的所有历史快照。

    Args:
        label: 配置文件标签
    """
    return await asyncio.to_thread(_list_sync, label)


@app.tool()
async def diff_snapshot(label: str, index: int) -> str:
    """对比当前文件和指定快照的差异。

    Args:
        label: 配置文件标签
        index: 快照序号（从 list_snapshots 获取）
    """
    return await asyncio.to_thread(_diff_sync, label, index)


@app.tool()
async def rollback(label: str, index: int) -> str:
    """回滚到指定快照版本。回滚前自动对当前版本拍快照防误操作。

    Args:
        label: 配置文件标签
        index: 目标快照序号（从 list_snapshots 获取）
    """
    return await asyncio.to_thread(_rollback_sync, label, index)


# ---- 同步实现（在线程池中执行，不阻塞事件循环） ----

def _snapshot_sync(label: str) -> str:
    cfg = get_config()
    pf = cfg.get_by_label(label)
    if pf is None:
        available = ", ".join(p.label for p in cfg.protected_files)
        return f"未找到标签「{label}」。可用标签: {available}"

    snap_path = create_snapshot(
        pf, cfg.snapshot_dir, cfg.max_snapshots_per_file, reason=SnapshotReason.MANUAL
    )
    return f"快照已创建: {snap_path}"


def _list_sync(label: str) -> str:
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


def _diff_sync(label: str, index: int) -> str:
    cfg = get_config()
    pf = cfg.get_by_label(label)
    if pf is None:
        available = ", ".join(p.label for p in cfg.protected_files)
        return f"未找到标签「{label}」。可用标签: {available}"

    return _diff_snapshot(pf, cfg.snapshot_dir, index)


def _rollback_sync(label: str, index: int) -> str:
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
