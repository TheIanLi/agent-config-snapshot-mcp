#说明书
"""加载 snapshot-config.yaml 配置文件，提供类型化的配置访问。"""

#拿取os这个工具，主要用来读取环境变量等等
import os
#拿取logging这个工具，主要用来输出日志
import logging
#在pathlib这个工具中，有一个Path小工具，主要用来处理文件路径，更方便
from pathlib import Path
#拿取typing这个工具里面的Optional工具，用来提示
from typing import Optional

#拿取yaml，可以更方便的读取文件
import yaml

# 跨平台兼容层：集中处理平台差异
from . import compat

#拿出logging这个工具，用来进行定位输出，有点像高级版的print
logger = logging.getLogger(__name__)

_VALID_WATCH_MODES = {"on_change", "daily", "manual"}

#def造机器，机器名叫validate_label，机器唯一的入口是label并且只能塞进来字符串，吐出字符串
def validate_label(label: str) -> str:
    """防路径穿越：将 label 中危险字符替换为下划线。

    应在数据入口处调用（如 load_config），确保所有下游代码收到的都是安全值。
    """
    #把下面的东西清洗干净，replace：替换。例如将..替换成_....
    sanitized = label.replace("..", "_").replace("/", "_").replace("\\", "_")
    #把\0变成空值
    sanitized = sanitized.replace("\0", "")
    #把空格和.都去掉，观感好
    sanitized = sanitized.strip().strip(".")
    #如果sanitized为空，则抛出错误
    if not sanitized:
        raise ValueError(f"label 不能为空或全为特殊字符: {label!r}")
    #如果sanitized不等于label，则输出label已净化
    if sanitized != label:
        logger.debug("label 已净化: %r -> %r", label, sanitized)
    #输出的意思
    return sanitized


#制定ProtectedFile模板，受保护的文件项，里面有path、label、watch三个参数
class ProtectedFile:
    """受保护文件的配置项。"""

    
    def __init__(self, path: Path, label: str, watch: str = "manual"):
        self.path = path
        self.label = label
        self.watch = watch  # "on_change" | "daily" | "manual"


#制定SnapshotConfig模板，快照系统配置，包含所有受保护文件列表和存储设置。
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

    #定义一个机器，机器名叫get_by_label，机器唯一的入口是label并且只能塞进来字符串，吐出Optional[ProtectedFile]
    def get_by_label(self, label: str) -> Optional[ProtectedFile]:
        """按标签查找受保护文件（查找时自动净化输入，保证匹配）。"""
        sanitized = validate_label(label)
        for pf in self.protected_files:
            if pf.label == sanitized:
                return pf
        return None


def _expand_path(raw: str) -> Path:
    """展开 ~ 和环境变量，返回绝对路径。

    使用 compat.get_home() 跨平台获取家目录：
    - POSIX: pwd.getpwuid 读 /etc/passwd（不依赖容易被污染的 $HOME）
    - Windows: os.path.expanduser 解析 %USERPROFILE%
    """
    if raw.startswith("~"):
        home = str(compat.get_home())
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
        if watch not in _VALID_WATCH_MODES:
            raise ValueError(
                f"文件 '{item['path']}' 的 watch 模式无效: '{watch}'，可选: {_VALID_WATCH_MODES}"
            )
        files.append(ProtectedFile(path=path, label=label, watch=watch))

    # 检查重复 label：相同 label 会让快照互相覆盖、且 get_by_label 只能取到第一个，
    # 导致后面的文件静默失效。这里提前报错，让用户改掉冲突的 label。
    seen_labels: dict[str, str] = {}
    for pf in files:
        if pf.label in seen_labels:
            raise ValueError(
                f"配置中存在重复的 label「{pf.label}」："
                f"{seen_labels[pf.label]} 与 {pf.path}。请为其中一个改用不同的 label。"
            )
        seen_labels[pf.label] = str(pf.path)

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
