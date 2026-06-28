"""快照核心逻辑：创建、列出、对比、回滚。"""

import shutil
import logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path

from . import compat
from .config import ProtectedFile

logger = logging.getLogger(__name__)

# 上海时区 UTC+8
SHA_TZ = timezone(timedelta(hours=8))


class SnapshotReason(str, Enum):
    """快照触发原因枚举，限定合法取值。"""
    ON_CHANGE = "on_change"
    DAILY = "daily"
    BASELINE = "baseline"
    MANUAL = "manual"
    SAFE = "safe"

# 快照文件命名：{原文件名}.snapshot.{UTC时间戳}.{reason}
_TIMESTAMP_FMT = "%Y%m%d_%H%M%S"
_TIMESTAMP_LEN = 15  # YYYYMMDD_HHMMSS 固定长度


def _ensure_snapshot_dir(snapshot_dir: Path) -> bool:
    """确保快照存储目录存在。返回本次是否新建了该目录。"""
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        return True
    except FileExistsError:
        return False


def _file_snapshot_dir(snapshot_dir: Path, pf: ProtectedFile) -> Path:
    """返回某个受保护文件的快照存放目录。"""
    from .config import validate_label

    sub = validate_label(pf.label)
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
    reason: SnapshotReason | str = SnapshotReason.MANUAL,
) -> Path:
    """对受保护文件拍一份快照，返回快照文件路径。

    max_snapshots: 保留上限，拍完后超出则删除最老的快照。0 表示不限制。
    reason: 触发原因标签，优先使用 SnapshotReason 枚举值。
    """
    # 归一化 reason：str → SnapshotReason
    if isinstance(reason, str):
        try:
            reason = SnapshotReason(reason)
        except ValueError:
            logger.warning("未知 reason=%r，回退为 MANUAL", reason)
            reason = SnapshotReason.MANUAL

    if not pf.path.exists():
        raise FileNotFoundError(f"源文件不存在，无法拍快照: {pf.path}")

    # 仅在快照根目录"本次新建"时加固权限（POSIX 收紧到 0o700 / Windows 设 ACL）。
    # 放在这里而非仅靠 watcher，是为了覆盖 CLI 直接拍快照的路径，并消除
    # "首跑时目录尚不存在、watcher 提前加固扑空" 的时序缺口。根目录 0o700
    # 即可阻止其它用户进入，是敏感快照的安全边界。只在新建时加固可避免
    # Windows 上每次快照都重复 spawn icacls 子进程。
    if _ensure_snapshot_dir(snapshot_dir):
        compat.secure_directory(snapshot_dir)
    dest_dir = _file_snapshot_dir(snapshot_dir, pf)
    _ensure_snapshot_dir(dest_dir)

    now = datetime.now(timezone.utc)
    base_name = _snapshot_name(pf, now, reason.value)
    snapshot_file = dest_dir / base_name

    # 防止同一秒内多次快照覆盖：已存在则加计数器后缀
    counter = 1
    while snapshot_file.exists():
        name = pf.path.name
        ts = now.strftime(_TIMESTAMP_FMT)
        snapshot_file = dest_dir / f"{name}.snapshot.{ts}.{reason.value}_{counter}"
        counter += 1

    shutil.copy2(pf.path, snapshot_file)
    logger.info("快照已创建 [%s]: %s -> %s", reason.value, pf.path, snapshot_file)

    # 按保留策略清理最老的快照
    if max_snapshots > 0:
        _prune_snapshots(dest_dir, pf.path.name, max_snapshots)

    return snapshot_file


def _prune_snapshots(dest_dir: Path, source_name: str, max_snapshots: int) -> None:
    """删除最老的快照，使快照总数不超过 max_snapshots。按时间从老到新排序，删最老的。

    glob 用 ``{source_name}.snapshot.*`` 与 list/get 保持一致：每个 label 独占
    子目录、当前只放一个源文件的快照，但限定文件名能防止将来该目录混入其它文件时
    误删（避免和 list 出现"删到的和列出的不是同一批"的不一致）。
    """
    snapshots = sorted(
        dest_dir.glob(f"{source_name}.snapshot.*"),
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

    snapshots = sorted(
        dest_dir.glob(f"{pf.path.name}.snapshot.*"),
        key=lambda p: _parse_timestamp(p.name),
        reverse=True,
    )
    result = []
    for idx, snap in enumerate(snapshots, start=1):
        stat = snap.stat()
        ts_utc = _parse_timestamp(snap.name)
        ts_sha = ts_utc.astimezone(SHA_TZ)
        result.append({
            "index": idx,
            "filename": snap.name,
            "path": str(snap),
            "timestamp": ts_sha.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "size": stat.st_size,
            "reason": _parse_reason(snap.name),
        })
    return result


def _is_binary(filepath: Path) -> bool:
    """检测文件是否为二进制（读前 1024 字节，含 null 则为二进制）。"""
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(1024)
        return b"\0" in chunk
    except OSError:
        return False


def get_snapshot_by_index(
    pf: ProtectedFile, snapshot_dir: Path, index: int
) -> Path:
    """按序号获取指定快照的文件路径。序号从 1 开始，最新的为 1。"""
    dest_dir = _file_snapshot_dir(snapshot_dir, pf)
    if not dest_dir.exists():
        raise IndexError(f"没有找到 {pf.label} 的快照目录: {dest_dir}")

    snapshots = sorted(dest_dir.glob(f"{pf.path.name}.snapshot.*"),
                       key=lambda p: _parse_timestamp(p.name), reverse=True)
    if not snapshots:
        raise IndexError(f"「{pf.label}」尚无快照")
    if index < 1 or index > len(snapshots):
        raise IndexError(f"快照序号 {index} 超出范围 (1-{len(snapshots)})")

    snap_path = snapshots[index - 1]
    if not snap_path.exists():
        raise FileNotFoundError(f"快照文件已被外部删除: {snap_path}")
    return snap_path


def diff_snapshot(pf: ProtectedFile, snapshot_dir: Path, index: int) -> str:
    """对比当前文件和指定快照的差异，返回 unified diff 文本。"""
    from difflib import unified_diff

    snapshot_path = get_snapshot_by_index(pf, snapshot_dir, index)

    if not pf.path.exists():
        raise FileNotFoundError(f"当前文件不存在: {pf.path}")

    # 二进制文件检测
    if _is_binary(pf.path) or _is_binary(snapshot_path):
        return "（无法对比：文件为二进制格式）"

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

    # 回滚前先拍一张安全快照（源文件不存在时跳过，不阻断回滚）
    safe_snapshot = None
    if pf.path.exists():
        safe_snapshot = create_snapshot(pf, snapshot_dir, max_snapshots, reason=SnapshotReason.SAFE)
    else:
        logger.warning("源文件不存在，跳过安全快照，直接回滚: %s", pf.path)

    shutil.copy2(snapshot_path, pf.path)
    timestamp_utc = _parse_timestamp(snapshot_path.name)
    timestamp_sha = timestamp_utc.astimezone(SHA_TZ)
    logger.info(
        "已回滚 %s 到快照 #%d (%s)，安全快照: %s",
        pf.label,
        index,
        timestamp_sha.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        safe_snapshot or "（跳过）",
    )

    return {
        "rolled_back_to": index,
        "snapshot_timestamp": timestamp_sha.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "safe_snapshot": str(safe_snapshot) if safe_snapshot else "（源文件在回滚前不存在，未拍安全快照）",
    }
