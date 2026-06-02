"""加载 snapshot-config.yaml 配置文件，提供类型化的配置访问。"""

import os
import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


def validate_label(label: str) -> str:
    """防路径穿越：将 label 中危险字符替换为下划线。

    应在数据入口处调用（如 load_config），确保所有下游代码收到的都是安全值。
    """
    sanitized = label.replace("..", "_").replace("/", "_").replace("\\", "_")
    sanitized = sanitized.replace("\0", "")
    sanitized = sanitized.strip().strip(".")
    if not sanitized:
        raise ValueError(f"label 不能为空或全为特殊字符: {label!r}")
    if sanitized != label:
        logger.debug("label 已净化: %r -> %r", label, sanitized)
    return sanitized


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
        """按标签查找受保护文件（查找时自动净化输入，保证匹配）。"""
        sanitized = validate_label(label)
        for pf in self.protected_files:
            if pf.label == sanitized:
                return pf
        return None


def _expand_path(raw: str) -> Path:
    """展开 ~ 和环境变量，返回绝对路径。

    使用 pwd 读取 /etc/passwd 获取真实家目录，
    不依赖容易被污染的 $HOME 环境变量。
    """
    if raw.startswith("~"):
        import pwd
        home = pwd.getpwuid(os.getuid()).pw_dir
        raw = home + raw[1:]
    # 展开 $VAR / ${VAR} 环境变量
    expanded = os.path.expandvars(raw)
    return Path(expanded).resolve()


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
        label = validate_label(item["label"])
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
