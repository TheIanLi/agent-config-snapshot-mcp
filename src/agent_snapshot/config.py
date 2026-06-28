#说明书
"""加载 snapshot-config.yaml 配置文件，提供类型化的配置访问。"""

#拿取os这个工具，主要用来读取环境变量等等
import os
#拿取re这个工具，用来做正则匹配（白名单净化、时间格式校验）
import re
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

# label 白名单：只允许 Unicode 字词字符（含中文等非 ASCII 字母）、下划线、点、连字符。
# 其它字符（包括路径分隔符 / \ 和 Windows 非法字符 : * ? " < > |，以及空字节、控制字符）
# 一律替换为下划线。Python 3 的 str 正则中 \w 默认是 Unicode 感知的，所以 CJK 等
# 字母会被保留，而路径分隔符/保留字符仍是非字词字符会被净化。
# 用白名单而非黑名单，这样将来出现没想到的危险字符也会被默认挡掉。
_LABEL_DISALLOWED = re.compile(r"[^\w.-]")

# Windows 保留设备名：这些名字不能直接当文件/目录名（即使带扩展名，如 CON.txt 也保留）。
# 在 Windows 上用它们建目录会直接报错，所以净化时要给它们加前缀避开。
_WINDOWS_RESERVED = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# daily_time 合法格式：HH:MM 或 HH:MM:SS（24 小时制）。
# 小时必须是两位（"04:00" 而非 "4:00"）——下游 schedule.at 只认两位小时，
# 这里的正则必须和 schedule 的约定保持一致，否则会放过让守护进程崩溃的值。
_DAILY_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")


#def造机器，机器名叫validate_label，机器唯一的入口是label并且只能塞进来字符串，吐出字符串
def validate_label(label: str) -> str:
    """白名单净化 label：只保留安全字符，并避开 Windows 保留设备名。

    应在数据入口处调用（如 load_config），确保所有下游代码收到的都是安全值。
    label 会被当作快照子目录名，所以必须是单个、跨平台合法的路径片段。
    """
    #先去掉首尾空白，再把白名单之外的字符统一替换成下划线
    sanitized = _LABEL_DISALLOWED.sub("_", label.strip())
    #把连续 2 个以上的点收成一个下划线（保留 config.json 这种单点，
    # 但清除 ".." 之类的父目录引用片段）
    sanitized = re.sub(r"\.{2,}", "_", sanitized)
    #去掉首尾的点（".config" → "config"，"." → 空）
    sanitized = sanitized.strip(".")
    #如果sanitized为空，则抛出错误
    if not sanitized:
        raise ValueError(f"label 不能为空或全为特殊字符: {label!r}")
    #避开 Windows 保留设备名：取第一个点之前的部分判断，命中就加下划线前缀
    if sanitized.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        sanitized = "_" + sanitized
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

    # 校验 daily_time 格式：非法值（如 "not-a-time"）若不在此拦下，会等到守护进程
    # 调用 schedule 时才崩溃，导致后台进程静默挂掉。提前在加载时报错更安全。
    daily_time = data.get("daily_time", "04:00")
    if not _DAILY_TIME_RE.match(str(daily_time)):
        raise ValueError(
            f"daily_time 格式无效: {daily_time!r}，应为 24 小时制的 HH:MM 或 HH:MM:SS（如 '04:00'）"
        )

    logger.info("已加载配置: %d 个受保护文件, 快照目录: %s", len(files), snapshot_dir)
    return SnapshotConfig(
        protected_files=files,
        snapshot_dir=snapshot_dir,
        max_snapshots_per_file=max_snapshots,
        daily_time=daily_time,
    )
