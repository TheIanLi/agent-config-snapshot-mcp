"""文件监听守护进程 — 基于 watchdog + schedule 自动拍快照。"""

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import schedule
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import compat
from .config import ProtectedFile, SnapshotConfig
from .snapshot import SnapshotReason, create_snapshot

logger = logging.getLogger("agent-snapshot.watcher")

# 防抖窗口（秒）：同一文件连续变动在此时间内合并为一次快照
_DEBOUNCE_SECONDS = 5

# watchdog observer 停止等待超时（秒）
_OBSERVER_STOP_TIMEOUT = 5

# daemon 模式：子进程启动后验证等待时间（秒）
_DAEMON_STARTUP_WAIT = 1.5


@dataclass
class _PendingSnapshot:
    """存放待执行快照的上下文，避免依赖 threading.Timer.args 的内部结构。"""
    pf: ProtectedFile
    trigger: str


class _SnapshotHandler(FileSystemEventHandler):
    """watchdog 事件处理器：转发 on_modified/on_created/on_moved 给 FileWatcher。"""

    def __init__(self, watcher: "FileWatcher") -> None:
        super().__init__()
        self._watcher = watcher

    def on_modified(self, event: object) -> None:
        self._watcher._on_file_event(getattr(event, "src_path", ""), "modified")

    def on_created(self, event: object) -> None:
        self._watcher._on_file_event(getattr(event, "src_path", ""), "created")

    def on_moved(self, event: object) -> None:
        # 原子写入是 temp → target，用 dest_path 匹配目标文件
        self._watcher._on_file_event(getattr(event, "dest_path", ""), "moved")


class FileWatcher:
    """文件监听器：on_change 用 watchdog 实时监听，daily 用 schedule 定时拍快照。"""

    def __init__(self, config: SnapshotConfig, debounce_seconds: float = _DEBOUNCE_SECONDS) -> None:
        self._config = config
        self._on_change_files: list[ProtectedFile] = []
        self._daily_files: list[ProtectedFile] = []
        self._debounce_timers: dict[str, tuple[threading.Timer, _PendingSnapshot]] = {}
        self._debounce_lock = threading.Lock()
        self._observer: Optional[Observer] = None
        self._pid_lock: Optional[compat.PidLock] = None
        self._debounce_seconds = debounce_seconds

        self._setup_logging()
        self._classify_files()
        self._check_dir_permissions()

    def _setup_logging(self) -> None:
        """日志同时输出到终端和 watcher.log。"""
        log_dir = self._config.snapshot_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "watcher.log"

        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S+08:00",
        )
        logger.setLevel(logging.INFO)

        # 避免重复添加 handler
        if not logger.handlers:
            fh = logging.FileHandler(str(log_file), encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)

            ch = logging.StreamHandler(sys.stderr)
            ch.setFormatter(fmt)
            logger.addHandler(ch)

    def _classify_files(self) -> None:
        """按 watch 字段分类文件。"""
        for pf in self._config.protected_files:
            if pf.watch == "on_change":
                self._on_change_files.append(pf)
            elif pf.watch == "daily":
                self._daily_files.append(pf)

    def _check_dir_permissions(self) -> None:
        """确保快照目录权限安全，并验证父目录权限链。

        快照包含敏感文件明文副本（.env / auth.json / credentials.json），
        整个目录树应限制为仅 owner/当前用户可访问。
        """
        snap_dir = self._config.snapshot_dir
        if not snap_dir.exists():
            return
        try:
            # 使用 compat 层处理平台差异
            # POSIX: 修正目录权限为 0o700
            # Windows: 用 icacls 设置 ACL
            compat.secure_directory(snap_dir)
            # 检查父目录链：确保父目录不开放 group/other 写权限
            # Windows: 直接 return（POSIX 权限位无意义）
            compat.check_parent_permissions(snap_dir)
        except OSError as e:
            logger.error("无法检查/修正快照目录权限: %s", e)

    def _take_baseline_snapshots(self) -> None:
        """启动时对所有 watched 文件拍一次基线快照。"""
        all_watched = self._on_change_files + self._daily_files
        if not all_watched:
            return
        logger.info("正在创建基线快照 (%d 个文件)...", len(all_watched))
        for pf in all_watched:
            try:
                snap_path = create_snapshot(
                    pf,
                    self._config.snapshot_dir,
                    self._config.max_snapshots_per_file,
                    reason=SnapshotReason.BASELINE,
                )
                logger.info("基线快照: %s -> %s", pf.label, snap_path)
            except Exception as e:
                logger.error("基线快照失败 [%s]: %s", pf.label, e)

    def _match_file(self, event_path: str) -> Optional[ProtectedFile]:
        """将事件路径匹配到受保护的 on_change 文件。"""
        if not event_path:
            return None
        try:
            p = Path(event_path).resolve()
        except (OSError, ValueError):
            return None
        for pf in self._on_change_files:
            if pf.path == p:
                return pf
        return None

    def _on_file_event(self, event_path: str, trigger: str) -> None:
        """文件变动事件入口，经防抖后拍快照。"""
        pf = self._match_file(event_path)
        if pf is None:
            return
        self._debounce_snapshot(pf, trigger)

    def _debounce_snapshot(self, pf: ProtectedFile, trigger: str) -> None:
        """per-file 防抖：5 秒内同一文件多次变动只拍最后一次。"""
        with self._debounce_lock:
            # 取消已有的 timer
            existing = self._debounce_timers.pop(pf.label, None)
            if existing is not None:
                existing[0].cancel()

            # 设置新的 timer
            pending = _PendingSnapshot(pf=pf, trigger=trigger)
            timer = threading.Timer(
                self._debounce_seconds,
                self._do_snapshot,
                args=[pending],
            )
            self._debounce_timers[pf.label] = (timer, pending)
            timer.start()

    def _do_snapshot(self, pending: _PendingSnapshot) -> None:
        """实际执行快照（由 debounce timer 触发）。

        接收 _PendingSnapshot 而非裸 timer.args，消除对 Thread 内部结构的依赖。
        """
        with self._debounce_lock:
            self._debounce_timers.pop(pending.pf.label, None)

        try:
            if not pending.pf.path.exists():
                logger.warning("文件不存在，跳过快照: %s", pending.pf.path)
                return
            snap_path = create_snapshot(
                pending.pf,
                self._config.snapshot_dir,
                self._config.max_snapshots_per_file,
                reason=SnapshotReason.ON_CHANGE,
            )
            logger.info(
                "快照触发 [%s] trigger=%s reason=%s 文件=%s -> %s",
                datetime.now().strftime("%H:%M:%S"),
                pending.trigger,
                SnapshotReason.ON_CHANGE.value,
                pending.pf.path,
                snap_path,
            )
        except Exception as e:
            logger.error(
                "快照失败 [%s] trigger=%s: %s", pending.pf.label, pending.trigger, e
            )

    def _run_daily_snapshots(self) -> None:
        """执行所有 daily 文件的每日快照。"""
        now = datetime.now().strftime("%H:%M:%S")
        for pf in self._daily_files:
            try:
                snap_path = create_snapshot(
                    pf,
                    self._config.snapshot_dir,
                    self._config.max_snapshots_per_file,
                    reason=SnapshotReason.DAILY,
                )
                logger.info("每日快照 [%s]: %s -> %s", now, pf.label, snap_path)
            except Exception as e:
                logger.error("每日快照失败 [%s]: %s", pf.label, e)

    def _start_observer(self) -> None:
        """启动 watchdog observer，按目录去重监听。"""
        if not self._on_change_files:
            return

        # 按父目录分组，去重
        watch_dirs: set[Path] = set()
        for pf in self._on_change_files:
            parent = pf.path.parent
            if parent.exists():
                watch_dirs.add(parent)
            else:
                logger.warning("文件目录不存在，跳过监听: %s", parent)

        if not watch_dirs:
            logger.warning("没有可监听的目录。")
            return

        self._observer = Observer()
        handler = _SnapshotHandler(self)
        for d in sorted(watch_dirs):
            self._observer.schedule(handler, str(d), recursive=False)
            logger.info("监听目录: %s", d)

        self._observer.start()
        logger.info("watchdog 已启动，监听 %d 个目录。", len(watch_dirs))

    def _setup_signal_handlers(self) -> None:
        """注册 SIGTERM/SIGINT 做优雅退出。"""
        compat.install_signal_handlers(self._shutdown)

    def _pid_file_path(self) -> Path:
        """PID 文件路径，存在快照目录下。"""
        return self._config.snapshot_dir / "watcher.pid"

    def _acquire_pid_lock(self) -> bool:
        """获取 PID 锁，防止重复启动。返回 True 表示获取成功。

        使用 filelock 库实现跨平台文件锁：
        - POSIX: 底层用 fcntl.flock
        - Windows: 底层用 msvcrt.locking
        由内核/OS 保证原子性，进程退出时自动释放。
        """
        self._pid_lock = compat.PidLock(self._pid_file_path())
        if not self._pid_lock.acquire():
            logger.error("无法获取 PID 锁 (watcher 可能已在运行)")
            self._pid_lock = None
            return False
        return True

    def _release_pid_lock(self) -> None:
        """释放 PID 锁并删除 PID 文件。"""
        if self._pid_lock is not None:
            self._pid_lock.release()
            self._pid_lock = None

    def _shutdown(self, signum: int, frame: object) -> None:
        """优雅退出：flush 所有 pending timer，停止 observer。"""
        logger.info("收到信号 %d，正在退出...", signum)

        # 收集所有 pending 快照上下文
        with self._debounce_lock:
            pending = list(self._debounce_timers.items())
            self._debounce_timers.clear()
        for label, (timer, pending_snap) in pending:
            timer.cancel()
            logger.info("flush pending timer: %s", label)
        # 执行 pending 快照（锁外执行，避免死锁）
        for label, (timer, pending_snap) in pending:
            try:
                self._do_snapshot(pending_snap)
            except Exception as e:
                logger.error("flush 快照失败 [%s]: %s", label, e)

        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=_OBSERVER_STOP_TIMEOUT)

        self._release_pid_lock()
        logger.info("watcher 已退出。")
        sys.exit(0)

    def run(self) -> None:
        """前台运行 watcher。"""
        self._setup_signal_handlers()

        if not self._acquire_pid_lock():
            print("错误: watcher 已在运行。如需重启请先停止旧进程。", file=sys.stderr)
            sys.exit(1)

        logger.info("===== watcher 启动 =====")
        logger.info("启动时间: %s", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
        logger.info("PID: %d, PID 文件: %s", os.getpid(), self._pid_file_path())
        logger.info(
            "监听文件: on_change=%d, daily=%d",
            len(self._on_change_files),
            len(self._daily_files),
        )
        logger.warning(
            "安全提醒: 快照以明文形式存储于 %s，包含 .env / auth.json 等敏感文件。"
            "请确保该目录仅 owner 可读 (chmod 700)。",
            self._config.snapshot_dir,
        )
        for pf in self._on_change_files:
            logger.info("  [on_change] %s (%s)", pf.label, pf.path)
        for pf in self._daily_files:
            logger.info("  [daily]     %s (%s)", pf.label, pf.path)

        # 启动基线快照
        self._take_baseline_snapshots()

        # 启动 watchdog
        self._start_observer()

        # 设置每日定时任务
        if self._daily_files:
            schedule.every().day.at(self._config.daily_time).do(
                self._run_daily_snapshots
            )
            logger.info("每日快照时间: %s", self._config.daily_time)

        # 主循环
        logger.info("watcher 运行中，按 Ctrl+C 退出。")
        try:
            while True:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            self._shutdown(signal.SIGINT, None)

    def run_daemon(self) -> None:
        """后台运行 watcher（nohup），含 PID 锁和启动验证。"""
        # 用 PidLock 检查是否已有 daemon 在跑
        pid_file = self._pid_file_path()
        probe_lock = compat.PidLock(pid_file)
        is_locked, old_pid = probe_lock.is_locked_by_other()
        if is_locked:
            if old_pid:
                print(f"错误: watcher 已在后台运行 (pid={old_pid})", file=sys.stderr)
            else:
                print("错误: watcher 已在后台运行 (PID 文件被锁定)", file=sys.stderr)
            sys.exit(1)

        cmd = [
            sys.executable, "-m", "agent_snapshot", "watch",
        ]
        log_file = self._config.snapshot_dir / "watcher.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n===== daemon 启动 {datetime.now()} =====\n")
        with open(os.devnull, "r") as devnull:
            process = compat.spawn_detached(
                cmd,
                stdin=devnull,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        # 验证子进程启动
        time.sleep(_DAEMON_STARTUP_WAIT)
        # 用 poll() 检查子进程是否存活（跨平台）
        # poll() 返回 None = 还活着；返回非 None = 已退出
        if process.poll() is not None:
            print(
                f"错误: watcher 子进程未能启动 (pid={process.pid} 已退出)。"
                f"查看日志: {log_file}",
                file=sys.stderr,
            )
            sys.exit(1)

        # 二次确认 PID 文件已被子进程写入
        if not pid_file.exists():
            print(
                f"警告: watcher 子进程已启动 (pid={process.pid})，但 PID 文件未出现。"
                f"查看日志: {log_file}",
                file=sys.stderr,
            )
        else:
            try:
                child_pid = pid_file.read_text(encoding="utf-8").strip()
                print(f"watcher 已在后台启动 (pid={child_pid})")
            except OSError:
                print(f"watcher 已在后台启动 (pid={process.pid})")
        print(f"日志文件: {log_file}")
