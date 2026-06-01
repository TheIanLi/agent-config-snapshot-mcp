"""加载 snapshot-config.yaml 配置文件，提供类型化的配置访问。"""

import os
import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


class ProtectedFile:
    """受保护文件的配置项。"""

    def __init__(self, path: Path, label: str, watch: str = "manual"):
        self.path = path
        self.label = label
        self.watch = watch  # "on_change" | "daily" | "manual"


class SnapshotConfig:
    """快照系统配置，包含所有受保护文件列表和存储设置。"""

    def __init__(
        self,
        protected_files: list[ProtectedFile],
        snapshot_dir: Path,
        max_snapshots_per_file: int,
        daily_time: str = "04:00",
    ):
        self.protected_files = protected_files
        self.snapshot_dir = snapshot_dir
        self.max_snapshots_per_file = max_snapshots_per_file
        self.daily_time = daily_time  # "HH:MM" 格式，daily 模式的执行时间

    def get_by_label(self, label: str) -> Optional[ProtectedFile]:
        """按标签查找受保护文件。"""
        for pf in self.protected_files:
            if pf.label == label:
                return pf
        return None


def _expand_path(raw: str) -> Path:
    """展开 ~ 和环境变量，返回绝对路径。"""
    return Path(os.path.expanduser(raw)).resolve()


def load_config(config_path: Optional[str] = None) -> SnapshotConfig:
    """加载 snapshot-config.yaml。

    查找顺序：
    1. SNAPSHOT_CONFIG 环境变量
    2. config_path 参数
    3. 当前工作目录下的 snapshot-config.yaml
    """
    if config_path is None:
        config_path = os.environ.get("SNAPSHOT_CONFIG", "snapshot-config.yaml")

    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    files = []
    for item in data.get("protected_files", []):
        path = _expand_path(item["path"])
        label = item["label"]
        watch = item.get("watch", "manual")
        files.append(ProtectedFile(path=path, label=label, watch=watch))

    snapshot_dir = _expand_path(data.get("snapshot_dir", "~/.agent-snapshots/"))
    max_snapshots = data.get("retention", {}).get("max_snapshots_per_file", 50)
    daily_time = data.get("daily_time", "04:00")

    logger.info("已加载配置: %d 个受保护文件, 快照目录: %s", len(files), snapshot_dir)
    return SnapshotConfig(
        protected_files=files,
        snapshot_dir=snapshot_dir,
        max_snapshots_per_file=max_snapshots,
        daily_time=daily_time,
    )
