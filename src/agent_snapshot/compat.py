"""跨平台兼容层 — 集中处理 POSIX / Windows 的平台差异。

本模块是所有平台专用代码的唯一入口，其它模块不允许直接 import pwd / fcntl 等平台专用库。
审查时只需看本文件就能理解所有平台分支逻辑。

设计原则：
1. POSIX 分支必须和改造前的逻辑完全等价（零行为变更）
2. Windows 分支尽力而为，失败时 log warning 而非抛异常
3. 注释风格和项目其它文件保持一致（面向编程初学者的详细中文注释）
"""

import logging
import os
import signal
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# ---- 平台检测常量 ----
# os.name 在 CPython 文档中只有三个值："posix"、"nt"、"java"
# Linux 和 macOS 都是 "posix"，Windows 是 "nt"
IS_WINDOWS = os.name == "nt"
IS_POSIX = os.name == "posix"


def get_home() -> Path:
    """跨平台获取用户家目录，返回绝对路径。

    POSIX 分支：
      故意不用 $HOME 环境变量，因为 $HOME 容易被 sudo / env -i 等场景污染。
      改用 pwd.getpwuid 从 /etc/passwd 读取真实家目录，这是更可靠的做法。

    Windows 分支：
      用 os.path.expanduser("~")，它会解析 %USERPROFILE% 环境变量。
      Windows 上没有 /etc/passwd 的概念，%USERPROFILE% 是标准做法。
    """
    if IS_POSIX:
        import pwd
        home = pwd.getpwuid(os.getuid()).pw_dir
        return Path(home)
    else:
        # Windows: expanduser 会解析 %USERPROFILE%
        return Path(os.path.expanduser("~")).resolve()


def secure_directory(path: Path) -> None:
    """把快照目录权限收紧到"仅当前用户可访问"。

    快照目录里存放 .env / auth.json 等敏感文件的明文副本，
    必须确保只有 owner/当前用户能读取。

    POSIX 分支：
      检查目录权限是否为 0o700（owner rwx，group/other 无权限），
      如果不是就 os.chmod 修正，并 log warning 提醒用户。

    Windows 分支：
      Windows 没有 Unix 风格的权限位，改用 icacls 命令设置 ACL：
      1. 关闭继承（/inheritance:r），移除从父目录继承的权限
      2. 只授予当前用户完全控制权限（/grant:r "<用户名>:(OI)(CI)F"）
      任何一步失败都不抛异常，改为 log warning 提示用户手动检查。
    """
    if not path.exists():
        return

    if IS_POSIX:
        try:
            mode = path.stat().st_mode & 0o777
            if mode != 0o700:
                os.chmod(path, 0o700)
                logger.warning("快照目录权限已从 %o 修正为 700: %s", mode, path)
        except OSError as e:
            logger.error("无法检查/修正快照目录权限: %s", e)
    else:
        # Windows: 用 icacls 设置 ACL
        try:
            username = os.environ.get("USERNAME", "")
            if not username:
                logger.warning(
                    "无法获取 USERNAME 环境变量，跳过 ACL 设置。"
                    "快照目录含明文敏感文件，请手动确认该目录仅自己可访问: %s",
                    path,
                )
                return

            # 第一步：关闭继承，移除继承的权限
            result = subprocess.run(
                ["icacls", str(path), "/inheritance:r"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.warning(
                    "icacls /inheritance:r 失败 (exit=%d): %s。"
                    "快照目录含明文敏感文件，请手动确认该目录仅自己可访问: %s",
                    result.returncode,
                    result.stderr.strip(),
                    path,
                )
                return

            # 第二步：授予当前用户完全控制权限
            # (OI)(CI) 表示对象继承 + 容器继承，F 表示完全控制
            grant = f"{username}:(OI)(CI)F"
            result = subprocess.run(
                ["icacls", str(path), "/grant:r", grant],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.warning(
                    "icacls /grant:r 失败 (exit=%d): %s。"
                    "快照目录含明文敏感文件，请手动确认该目录仅自己可访问: %s",
                    result.returncode,
                    result.stderr.strip(),
                    path,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.warning(
                "无法执行 icacls 设置 ACL: %s。"
                "快照目录含明文敏感文件，请手动确认该目录仅自己可访问: %s",
                e,
                path,
            )


def check_parent_permissions(path: Path) -> None:
    """检查父目录链是否对 group/other 开放写权限。

    POSIX 分支：
      从 path 的父目录开始向上遍历，检查每个目录的权限位。
      如果发现 group 或 other 有写权限（mask 0o022），log warning 提醒用户。
      这是一个安全提醒，不会阻止程序运行。

    Windows 分支：
      Windows 没有 Unix 风格的权限位（rwx），POSIX 权限检查无意义。
      直接返回，不做任何检查。
    """
    if IS_WINDOWS:
        return

    # POSIX: 遍历父目录链检查权限
    parent = path.parent
    while parent != parent.parent:
        try:
            p_mode = parent.stat().st_mode & 0o777
            if p_mode & 0o022:  # group 或 other 有写权限
                logger.warning(
                    "父目录权限不安全 (%o): %s —— 建议用 chmod 755 或 700 修正",
                    p_mode,
                    parent,
                )
        except OSError:
            pass
        parent = parent.parent


def install_signal_handlers(handler) -> None:
    """跨平台注册退出信号处理器。

    遍历 SIGTERM 和 SIGINT，用 getattr 安全获取信号常量（某些平台可能没有）。
    注册时用 try/except 包住，因为：
    - 某些平台/环境不支持特定信号
    - 在非主线程中注册信号会抛 ValueError

    注意：Windows 上 SIGINT 通过 Ctrl+C 触发 KeyboardInterrupt，
    这条路径是 Python 内置支持的，不需要额外处理。
    """
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            # 该平台没有这个信号（理论上 SIGINT 所有平台都有，但防御性编程）
            continue
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            # 在非主线程中注册信号会抛 ValueError
            # 某些嵌入式环境可能抛 OSError
            logger.debug("无法注册信号处理器 %s（可能在非主线程中）", sig_name)


def spawn_detached(cmd: list[str], **kwargs) -> subprocess.Popen:
    """跨平台启动脱离终端的后台子进程。

    POSIX 分支：
      使用 start_new_session=True 让子进程脱离当前终端会话，
      等价于传统的 nohup 效果。子进程不会收到终端关闭的 SIGHUP。

    Windows 分支：
      使用 DETACHED_PROCESS 让子进程彻底脱离父进程的控制台
      （这样关掉终端窗口也不会杀掉守护进程，这才是后台 daemon 想要的），
      再叠加 CREATE_NEW_PROCESS_GROUP 让子进程自成进程组，
      不会收到发给父进程组的 Ctrl+C / Ctrl+Break。
    """
    if IS_POSIX:
        return subprocess.Popen(cmd, start_new_session=True, **kwargs)
    else:
        # Windows: DETACHED_PROCESS(0x08) 脱离控制台 + CREATE_NEW_PROCESS_GROUP(0x200) 独立进程组
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(cmd, creationflags=creationflags, **kwargs)


class PidLock:
    """跨平台单实例锁，使用 filelock 库实现。

    设计说明：
    - 锁文件 = <pid_file>.lock（交给 filelock 管理底层锁机制）
    - PID 文件 = pid_file（纯文本，存当前 PID 供人/父进程读取）

    为什么用 filelock 而不是手写 fcntl + msvcrt：
    - filelock 已经处理好 POSIX (fcntl.flock) 和 Windows (msvcrt.locking) 两套底层锁
    - 比手写更可靠、更好审查、更容易维护
    - 文件锁在进程退出时自动释放（由内核/OS 保证）

    使用方式：
    - 创建 PidLock 对象后调用 acquire() 获取锁
    - 持有锁的对象要在进程生命周期内一直存活（别被 GC 回收）
    - 进程退出时调用 release() 释放锁并清理 PID 文件
    """

    def __init__(self, pid_file: Path) -> None:
        """初始化 PID 锁。

        Args:
            pid_file: PID 文件路径，锁文件会在同目录下创建为 <pid_file>.lock
        """
        from filelock import FileLock

        self._pid_file = pid_file
        # 锁文件和 PID 文件在同一目录，后缀加 .lock
        self._lock = FileLock(str(pid_file) + ".lock")

    def acquire(self) -> bool:
        """尝试获取锁（非阻塞）。

        成功时：
        - 把当前 PID 写入 pid_file
        - 返回 True

        失败时（已被其它进程持有）：
        - 返回 False
        """
        from filelock import Timeout

        try:
            # timeout=0 表示非阻塞，拿不到就立即抛 Timeout
            self._lock.acquire(timeout=0)
        except Timeout:
            # 锁被其它进程占用（正常的竞争失败）
            return False
        except OSError as e:
            # 真正的故障（锁目录不存在 / 不可写等），记录真实原因再返回失败，
            # 否则调用方只会看到"watcher 可能已在运行"的误导信息。
            logger.error("无法获取 PID 锁文件 %s: %s", self._pid_file, e)
            return False

        # 锁已获取，写入当前 PID
        try:
            self._pid_file.write_text(str(os.getpid()), encoding="utf-8")
        except OSError as e:
            logger.warning("无法写入 PID 文件 %s: %s", self._pid_file, e)
        return True

    def release(self) -> None:
        """释放锁并删除 PID 文件。

        注意：即使释放失败也不会抛异常（best-effort）。
        """
        try:
            self._lock.release()
        except Exception:
            pass
        try:
            self._pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    def is_locked_by_other(self) -> tuple[bool, int | None]:
        """探测锁是否被别的进程占用。

        返回值：
        - (False, None)：锁未被占用（当前进程可以获取）
        - (True, old_pid)：锁已被占用，old_pid 是占用进程的 PID（读不到则为 None）

        注意：这个方法会短暂获取锁再释放，用于探测。
        探测结果是瞬时的，不保证后续 acquire 一定成功（TOCTOU），
        但对于"检查 watcher 是否已在运行"这个场景足够用了。
        """
        try:
            # 尝试获取锁
            self._lock.acquire(timeout=0)
        except Exception:
            # 获取失败，说明锁被占用
            old_pid = self._read_pid()
            return (True, old_pid)

        # 获取成功，说明锁未被占用，立即释放
        try:
            self._lock.release()
        except Exception:
            pass
        return (False, None)

    def _read_pid(self) -> int | None:
        """从 PID 文件读取进程号，读不到返回 None。"""
        try:
            text = self._pid_file.read_text(encoding="utf-8").strip()
            return int(text)
        except (OSError, ValueError):
            return None
