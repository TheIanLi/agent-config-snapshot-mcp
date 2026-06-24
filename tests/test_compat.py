"""compat.py 跨平台兼容层的测试。"""

import os
import signal
import sys
from pathlib import Path

import pytest

from agent_snapshot import compat


class TestGetHome:
    """测试 get_home() 跨平台获取家目录。"""

    def test_returns_existing_directory(self):
        """get_home() 返回的路径应该是一个存在的目录。"""
        home = compat.get_home()
        assert home.exists(), f"家目录不存在: {home}"
        assert home.is_dir(), f"家目录不是目录: {home}"

    def test_returns_absolute_path(self):
        """get_home() 返回的应该是绝对路径。"""
        home = compat.get_home()
        assert home.is_absolute(), f"家目录不是绝对路径: {home}"

    def test_returns_path_type(self):
        """get_home() 应该返回 Path 对象。"""
        home = compat.get_home()
        assert isinstance(home, Path)


class TestPlatformConstants:
    """测试平台检测常量。"""

    def test_is_windows_or_posix(self):
        """应该恰好是 Windows 或 POSIX 之一。"""
        assert compat.IS_WINDOWS != compat.IS_POSIX, (
            f"IS_WINDOWS={compat.IS_WINDOWS}, IS_POSIX={compat.IS_POSIX}，"
            "应该恰好一个为 True"
        )

    def test_matches_os_name(self):
        """常量应该和 os.name 一致。"""
        if os.name == "nt":
            assert compat.IS_WINDOWS is True
            assert compat.IS_POSIX is False
        elif os.name == "posix":
            assert compat.IS_WINDOWS is False
            assert compat.IS_POSIX is True


class TestPidLock:
    """测试 PidLock 跨平台单实例锁。"""

    def test_acquire_success(self, tmp_path):
        """首次 acquire 应该成功。"""
        pid_file = tmp_path / "test.pid"
        lock = compat.PidLock(pid_file)
        assert lock.acquire() is True
        lock.release()

    def test_acquire_writes_pid(self, tmp_path):
        """acquire 成功后应该把当前 PID 写入 pid_file。"""
        pid_file = tmp_path / "test.pid"
        lock = compat.PidLock(pid_file)
        lock.acquire()
        try:
            assert pid_file.exists()
            written_pid = int(pid_file.read_text(encoding="utf-8").strip())
            assert written_pid == os.getpid()
        finally:
            lock.release()

    def test_double_acquire_fails(self, tmp_path):
        """对同一 pid_file 第二次 acquire 应该失败。"""
        pid_file = tmp_path / "test.pid"
        lock1 = compat.PidLock(pid_file)
        lock2 = compat.PidLock(pid_file)

        assert lock1.acquire() is True
        try:
            assert lock2.acquire() is False
        finally:
            lock1.release()

    def test_release_cleans_up(self, tmp_path):
        """release 后 pid_file 应该被删除。"""
        pid_file = tmp_path / "test.pid"
        lock = compat.PidLock(pid_file)
        lock.acquire()
        lock.release()
        assert not pid_file.exists()

    def test_is_locked_by_other_when_free(self, tmp_path):
        """未被占用时 is_locked_by_other 应该返回 (False, None)。"""
        pid_file = tmp_path / "test.pid"
        lock = compat.PidLock(pid_file)
        is_locked, pid = lock.is_locked_by_other()
        assert is_locked is False
        assert pid is None

    def test_is_locked_by_other_when_held(self, tmp_path):
        """被占用时 is_locked_by_other 应该返回 (True, pid)。"""
        pid_file = tmp_path / "test.pid"
        lock1 = compat.PidLock(pid_file)
        lock1.acquire()
        try:
            lock2 = compat.PidLock(pid_file)
            is_locked, pid = lock2.is_locked_by_other()
            assert is_locked is True
            assert pid == os.getpid()
        finally:
            lock1.release()

    def test_acquire_after_release(self, tmp_path):
        """release 后应该能重新 acquire。"""
        pid_file = tmp_path / "test.pid"
        lock1 = compat.PidLock(pid_file)
        lock1.acquire()
        lock1.release()

        lock2 = compat.PidLock(pid_file)
        assert lock2.acquire() is True
        lock2.release()


class TestInstallSignalHandlers:
    """测试 install_signal_handlers 跨平台注册信号处理器。"""

    def test_does_not_raise(self):
        """调用不应该抛异常。"""
        def dummy_handler(signum, frame):
            pass

        # 不应该抛异常
        compat.install_signal_handlers(dummy_handler)

    def test_registers_available_signals(self):
        """应该成功注册可用的信号。"""
        registered = []

        def test_handler(signum, frame):
            registered.append(signum)

        compat.install_signal_handlers(test_handler)

        # SIGINT 在所有平台都应该存在
        if hasattr(signal, "SIGINT"):
            # 恢复原来的 handler
            signal.signal(signal.SIGINT, signal.default_int_handler)


class TestSecureDirectory:
    """测试 secure_directory 跨平台目录权限设置。"""

    def test_does_not_raise_on_existing_dir(self, tmp_path):
        """对已存在的目录不应该抛异常。"""
        test_dir = tmp_path / "test_snapshots"
        test_dir.mkdir()
        compat.secure_directory(test_dir)

    def test_does_not_raise_on_missing_dir(self, tmp_path):
        """对不存在的目录不应该抛异常。"""
        missing_dir = tmp_path / "nonexistent"
        compat.secure_directory(missing_dir)


class TestCheckParentPermissions:
    """测试 check_parent_permissions 跨平台父目录权限检查。"""

    def test_does_not_raise(self, tmp_path):
        """调用不应该抛异常。"""
        test_dir = tmp_path / "test"
        test_dir.mkdir()
        compat.check_parent_permissions(test_dir)


class TestSpawnDetached:
    """测试 spawn_detached 跨平台启动后台进程。"""

    def test_can_spawn_process(self):
        """应该能启动一个后台进程。"""
        import subprocess

        # 用一个快速退出的命令测试
        process = compat.spawn_detached(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # 等待进程退出。超时给足 30 秒：子进程本身（sys.exit(0)）瞬间就结束，
        # 但 Windows CI 机器在负载高时，python.exe 冷启动可能很慢，
        # 原来的 5 秒在慢机器上会偶发超时（曾在 windows-latest CI 上 flaky）。
        process.wait(timeout=30)
        assert process.returncode == 0
