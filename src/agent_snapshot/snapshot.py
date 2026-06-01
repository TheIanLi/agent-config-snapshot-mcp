"""快照核心逻辑：创建、列出、对比、回滚。"""

import shutil
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import ProtectedFile

logger = logging.getLogger(__name__)

# 快照文件命名：{原文件名}.snapshot.{UTC时间戳}.{reason}
_TIMESTAMP_FMT = "%Y%m%d_%H%M%S"
_TIMESTAMP_LEN = 15  # YYYYMMDD_HHMMSS 固定长度


def _ensure_snapshot_dir(snapshot_dir: Path) -> None:
    """确保快照存储目录存在。"""
    snapshot_dir.mkdir(parents=True, exist_ok=True)


def _file_snapshot_dir(snapshot_dir: Path, pf: ProtectedFile) -> Path:
    """返回某个受保护文件的快照存放目录。"""
    # 用原文件名的 hash 做子目录名，避免同名字符串冲突
    sub = pf.label
    return snapshot_dir / sub


def _snapshot_name(pf: ProtectedFile, timestamp: datetime, reason: str) -> str:
    """生成快照文件名。"""
    ts = timestamp.strftime(_TIMESTAMP_FMT)
    return f"{pf.path.name}.snapshot.{ts}.{reason}"


def _parse_timestamp(filename: str) -> datetime:
    """从快照文件名中解析时间戳。

    兼容三种格式：
    - 旧：{name}.snapshot.{timestamp}
    - 旧+计数器：{name}.snapshot.{timestamp}_{counter}
    - 新：{name}.snapshot.{timestamp}.{reason}
    - 新+计数器：{name}.snapshot.{timestamp}.{reason}_{counter}
    """
    after = filename.rsplit(".snapshot.", 1)[-1]
    ts_part = after[:_TIMESTAMP_LEN]
    return datetime.strptime(ts_part, _TIMESTAMP_FMT).replace(tzinfo=timezone.utc)


def _parse_reason(filename: str) -> str:
    """从快照文件名中提取 reason 标签。旧格式无 reason 时默认 'manual'。"""
    after = filename.rsplit(".snapshot.", 1)[-1]
    suffix = after[_TIMESTAMP_LEN:]  # 时间戳之后的部分
    if suffix.startswith("."):
        # 新格式：.{reason} 或 .{reason}_{counter}
        reason_part = suffix[1:]
        if "_" in reason_part:
            rest, counter = reason_part.rsplit("_", 1)
            if counter.isdigit():
                return rest
        return reason_part
    # 旧格式：没有 reason，或只有 _{counter}
    return "manual"


def create_snapshot(
    pf: ProtectedFile,
    snapshot_dir: Path,
    max_snapshots: int = 0,
    reason: str = "manual",
) -> Path:
    """对受保护文件拍一份快照，返回快照文件路径。

    max_snapshots: 保留上限，拍完后超出则删除最老的快照。0 表示不限制。
    reason: 触发原因标签（manual / on_change / daily / baseline / safe）。
    """
    if not pf.path.exists():
        raise FileNotFoundError(f"源文件不存在，无法拍快照: {pf.path}")

    _ensure_snapshot_dir(snapshot_dir)
    dest_dir = _file_snapshot_dir(snapshot_dir, pf)
    _ensure_snapshot_dir(dest_dir)

    now = datetime.now(timezone.utc)
    base_name = _snapshot_name(pf, now, reason)
    snapshot_file = dest_dir / base_name

    # 防止同一秒内多次快照覆盖：已存在则加计数器后缀
    counter = 1
    while snapshot_file.exists():
        name = pf.path.name
        ts = now.strftime(_TIMESTAMP_FMT)
        snapshot_file = dest_dir / f"{name}.snapshot.{ts}.{reason}_{counter}"
        counter += 1

    shutil.copy2(pf.path, snapshot_file)
    logger.info("快照已创建 [%s]: %s -> %s", reason, pf.path, snapshot_file)

    # 按保留策略清理最老的快照
    if max_snapshots > 0:
        _prune_snapshots(dest_dir, max_snapshots)

    return snapshot_file


def _prune_snapshots(dest_dir: Path, max_snapshots: int) -> None:
    """删除最老的快照，使快照总数不超过 max_snapshots。按时间从老到新排序，删最老的。"""
    snapshots = sorted(
        dest_dir.glob("*.snapshot.*"),
        key=lambda p: _parse_timestamp(p.name),
    )
    overflow = len(snapshots) - max_snapshots
    if overflow > 0:
        for old in snapshots[:overflow]:
            old.unlink()
            logger.info("已清理过期快照: %s", old)


def list_snapshots(pf: ProtectedFile, snapshot_dir: Path) -> list[dict]:
    """列出某个受保护文件的所有快照，最新的排前面。"""
    dest_dir = _file_snapshot_dir(snapshot_dir, pf)
    if not dest_dir.exists():
        return []

    snapshots = sorted(dest_dir.glob(f"{pf.path.name}.snapshot.*"), reverse=True)
    result = []
    for idx, snap in enumerate(snapshots, start=1):
        stat = snap.stat()
        ts = _parse_timestamp(snap.name)
        result.append({
            "index": idx,
            "filename": snap.name,
            "path": str(snap),
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size": stat.st_size,
            "reason": _parse_reason(snap.name),
        })
    return result


def get_snapshot_by_index(
    pf: ProtectedFile, snapshot_dir: Path, index: int
) -> Path:
    """按序号获取指定快照的文件路径。序号从 1 开始，最新的为 1。"""
    dest_dir = _file_snapshot_dir(snapshot_dir, pf)
    if not dest_dir.exists():
        raise IndexError(f"没有找到 {pf.label} 的快照")

    snapshots = sorted(dest_dir.glob(f"{pf.path.name}.snapshot.*"), reverse=True)
    if index < 1 or index > len(snapshots):
        raise IndexError(f"快照序号 {index} 超出范围 (1-{len(snapshots)})")

    return snapshots[index - 1]


def diff_snapshot(pf: ProtectedFile, snapshot_dir: Path, index: int) -> str:
    """对比当前文件和指定快照的差异，返回 unified diff 文本。"""
    from difflib import unified_diff

    snapshot_path = get_snapshot_by_index(pf, snapshot_dir, index)

    if not pf.path.exists():
        raise FileNotFoundError(f"当前文件不存在: {pf.path}")

    with open(snapshot_path, "r", encoding="utf-8", errors="replace") as f:
        old_lines = f.readlines()

    with open(pf.path, "r", encoding="utf-8", errors="replace") as f:
        new_lines = f.readlines()

    diff = unified_diff(
        old_lines,
        new_lines,
        fromfile=f"{pf.path.name} (快照 #{index})",
        tofile=f"{pf.path.name} (当前)",
    )
    result = "".join(diff)
    return result if result else "（无差异）"


def rollback(
    pf: ProtectedFile, snapshot_dir: Path, index: int, max_snapshots: int = 0
) -> dict:
    """回滚到指定快照。回滚前自动对当前版本拍一张快照，防止误操作。"""
    snapshot_path = get_snapshot_by_index(pf, snapshot_dir, index)

    # 回滚前先拍一张安全快照
    safe_snapshot = create_snapshot(pf, snapshot_dir, max_snapshots, reason="safe")

    shutil.copy2(snapshot_path, pf.path)
    timestamp = _parse_timestamp(snapshot_path.name)
    logger.info(
        "已回滚 %s 到快照 #%d (%s)，安全快照: %s",
        pf.label,
        index,
        timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        safe_snapshot,
    )

    return {
        "rolled_back_to": index,
        "snapshot_timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "safe_snapshot": str(safe_snapshot),
    }
