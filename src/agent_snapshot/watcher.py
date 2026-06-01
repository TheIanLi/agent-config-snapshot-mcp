"""文件监听守护进程 — 基于 watchdog + schedule 自动拍快照。"""

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import schedule
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import ProtectedFile, SnapshotConfig
from .snapshot import create_snapshot

logger = logging.getLogger("agent-snapshot.watcher")

# 防抖窗口（秒）
_DEBOUNCE_SECONDS = 5


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

    def __init__(self, config: SnapshotConfig) -> None:
        self._config = config
        self._on_change_files: list[ProtectedFile] = []
        self._daily_files: list[ProtectedFile] = []
        self._debounce_timers: dict[str, threading.Timer] = {}
        self._debounce_lock = threading.Lock()
        self._observer: Optional[Observer] = None

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
            datefmt="%Y-%m-%dT%H:%M:%S",
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
        """确保快照目录权限为 700（包含敏感文件如 .env）。"""
        snap_dir = self._config.snapshot_dir
        if not snap_dir.exists():
            return
        try:
            mode = snap_dir.stat().st_mode & 0o777
            if mode != 0o700:
                os.chmod(snap_dir, 0o700)
                logger.warning("快照目录权限已从 %o 修正为 700: %s", mode, snap_dir)
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
                    reason="baseline",
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

    def _on_file_event(self, event_path: str, reason: str) -> None:
        """文件变动事件入口，经防抖后拍快照。"""
        pf = self._match_file(event_path)
        if pf is None:
            return
        self._debounce_snapshot(pf, reason)

    def _debounce_snapshot(self, pf: ProtectedFile, reason: str) -> None:
        """per-file 防抖：5 秒内同一文件多次变动只拍最后一次。"""
        with self._debounce_lock:
            # 取消已有的 timer
            existing = self._debounce_timers.pop(pf.label, None)
            if existing is not None:
                existing.cancel()

            # 设置新的 timer
            timer = threading.Timer(
                _DEBOUNCE_SECONDS,
                self._do_snapshot,
                args=[pf, reason],
            )
            self._debounce_timers[pf.label] = timer
            timer.start()

    def _do_snapshot(self, pf: ProtectedFile, reason: str) -> None:
        """实际执行快照（由 debounce timer 触发）。"""
        with self._debounce_lock:
            self._debounce_timers.pop(pf.label, None)

        try:
            if not pf.path.exists():
                logger.warning("文件不存在，跳过快照: %s", pf.path)
                return
            snap_path = create_snapshot(
                pf,
                self._config.snapshot_dir,
                self._config.max_snapshots_per_file,
                reason="on_change",
            )
            logger.info(
                "快照触发 [%s] 原因=%s 文件=%s -> %s",
                datetime.now().strftime("%H:%M:%S"),
                reason,
                pf.path,
                snap_path,
            )
        except Exception as e:
            logger.error("快照失败 [%s] 原因=%s: %s", pf.label, reason, e)

    def _run_daily_snapshots(self) -> None:
        """执行所有 daily 文件的每日快照。"""
        now = datetime.now().strftime("%H:%M:%S")
        for pf in self._daily_files:
            try:
                snap_path = create_snapshot(
                    pf,
                    self._config.snapshot_dir,
                    self._config.max_snapshots_per_file,
                    reason="daily",
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
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)

    def _shutdown(self, signum: int, frame: object) -> None:
        """优雅退出：取消所有 pending timer，停止 observer。"""
        logger.info("收到信号 %d，正在退出...", signum)

        with self._debounce_lock:
            for label, timer in list(self._debounce_timers.items()):
                timer.cancel()
                logger.info("已取消 pending timer: %s", label)
            self._debounce_timers.clear()

        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)

        logger.info("watcher 已退出。")
        sys.exit(0)

    def run(self) -> None:
        """前台运行 watcher。"""
        self._setup_signal_handlers()

        logger.info("===== watcher 启动 =====")
        logger.info("启动时间: %s", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
        logger.info(
            "监听文件: on_change=%d, daily=%d",
            len(self._on_change_files),
            len(self._daily_files),
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
        """后台运行 watcher（nohup）。"""
        # 使用自身模块启动
        cmd = [
            sys.executable, "-m", "agent_snapshot", "watch",
        ]
        log_file = self._config.snapshot_dir / "watcher.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n===== daemon 启动 {datetime.now()} =====\n")
        with open(os.devnull, "r") as devnull:
            process = subprocess.Popen(
                cmd,
                stdin=devnull,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        print(f"watcher 已在后台启动 (pid={process.pid})")
        print(f"日志文件: {log_file}")
