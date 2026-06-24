"""测试 watcher 核心逻辑：分类、防抖、触发、基线快照、on_moved 匹配。"""

import time
from pathlib import Path

import pytest

from agent_snapshot.config import ProtectedFile, SnapshotConfig
from agent_snapshot.snapshot import list_snapshots
from agent_snapshot.watcher import FileWatcher


@pytest.fixture
def watcher_config(tmp_path: Path) -> SnapshotConfig:
    """构造一个含 on_change / daily / manual 三类文件的配置。"""
    on_change_file = tmp_path / "on_change_config.yaml"
    on_change_file.write_text("key: value")
    daily_file = tmp_path / "MEMORY.md"
    daily_file.write_text("# Memory")
    manual_file = tmp_path / "manual.json"
    manual_file.write_text('{"x": 1}')

    snapshot_dir = tmp_path / "snapshots"

    pf1 = ProtectedFile(
        path=on_change_file, label="test/on_change_config", watch="on_change"
    )
    pf2 = ProtectedFile(
        path=daily_file, label="test/MEMORY", watch="daily"
    )
    pf3 = ProtectedFile(
        path=manual_file, label="test/manual", watch="manual"
    )

    return SnapshotConfig(
        protected_files=[pf1, pf2, pf3],
        snapshot_dir=snapshot_dir,
        max_snapshots_per_file=50,
        daily_time="04:00",
    )


class TestFileClassification:
    """测试文件按 watch 字段分类。"""

    def test_classify_files(self, watcher_config: SnapshotConfig) -> None:
        w = FileWatcher(watcher_config, debounce_seconds=0.1)
        assert len(w._on_change_files) == 1
        assert w._on_change_files[0].label == "test/on_change_config"
        assert len(w._daily_files) == 1
        assert w._daily_files[0].label == "test/MEMORY"

    def test_manual_not_classified(self, watcher_config: SnapshotConfig) -> None:
        """manual 模式的文件不进入 on_change 或 daily。"""
        w = FileWatcher(watcher_config, debounce_seconds=0.1)
        all_watched = {pf.label for pf in w._on_change_files + w._daily_files}
        assert "test/manual" not in all_watched


class TestDebounce:
    """测试 per-file 防抖机制。"""

    def test_debounce_single_snapshot(self, watcher_config: SnapshotConfig) -> None:
        """5 秒内同一文件多次变动只产生一次快照。

        防抖窗口（0.5s）必须明显大于事件之间的间隔（0.05s），否则在较慢的 CI
        机器上，第一个防抖定时器可能在第三次事件取消它之前就先触发，导致拍出 2 张
        快照（曾在 macOS CI 上偶发失败）。窗口放大后留足余量，消除这种时序竞争。
        """
        w = FileWatcher(watcher_config, debounce_seconds=0.5)
        pf = watcher_config.protected_files[0]  # on_change file

        # 直接触发多次事件（绕过 watchdog），间隔远小于防抖窗口
        w._on_file_event(str(pf.path), "modified")
        time.sleep(0.05)
        w._on_file_event(str(pf.path), "modified")
        time.sleep(0.05)
        w._on_file_event(str(pf.path), "modified")

        # 等待防抖窗口结束 + 快照写入（留足余量）
        time.sleep(0.8)

        snaps = list_snapshots(pf, watcher_config.snapshot_dir)
        # 应该只有 1 个快照（多次触发被防抖合并）
        assert len(snaps) == 1

    def test_independent_files_debounce(
        self, tmp_path: Path, watcher_config: SnapshotConfig
    ) -> None:
        """不同文件的 debounce timer 互不干扰。"""
        # 追加第二个 on_change 文件
        f2 = tmp_path / "second.yaml"
        f2.write_text("data: yes")
        pf2 = ProtectedFile(path=f2, label="test/second", watch="on_change")
        watcher_config.protected_files.append(pf2)

        w = FileWatcher(watcher_config, debounce_seconds=0.1)
        pf1 = watcher_config.protected_files[0]

        # 分别触发两个文件
        w._on_file_event(str(pf1.path), "modified")
        w._on_file_event(str(pf2.path), "modified")

        time.sleep(0.3)

        snaps1 = list_snapshots(pf1, watcher_config.snapshot_dir)
        snaps2 = list_snapshots(pf2, watcher_config.snapshot_dir)
        assert len(snaps1) == 1
        assert len(snaps2) == 1


class TestOnChangeTrigger:
    """测试 on_change 文件修改后触发快照。"""

    def test_on_change_creates_snapshot(
        self, watcher_config: SnapshotConfig
    ) -> None:
        w = FileWatcher(watcher_config, debounce_seconds=0.1)
        pf = watcher_config.protected_files[0]

        # 修改文件后触发
        pf.path.write_text("key: modified")
        w._on_file_event(str(pf.path), "modified")

        time.sleep(0.3)

        snaps = list_snapshots(pf, watcher_config.snapshot_dir)
        assert len(snaps) == 1


class TestDailyIsolation:
    """测试 daily 文件不被实时监听触发。"""

    def test_daily_not_triggered_on_change(
        self, watcher_config: SnapshotConfig
    ) -> None:
        w = FileWatcher(watcher_config, debounce_seconds=0.1)
        daily_pf = watcher_config.protected_files[1]  # daily

        daily_pf.path.write_text("# Updated Memory")
        # 即使有文件事件传入，daily 文件在 _match_file 中不会被匹配
        # 因为 _match_file 只查 _on_change_files
        result = w._match_file(str(daily_pf.path))
        assert result is None


class TestBaselineSnapshots:
    """测试启动基线快照。"""

    def test_baseline_snapshot_taken(
        self, watcher_config: SnapshotConfig
    ) -> None:
        w = FileWatcher(watcher_config, debounce_seconds=0.1)
        w._take_baseline_snapshots()

        # on_change 和 daily 文件都应该有基线快照
        snaps1 = list_snapshots(
            watcher_config.protected_files[0], watcher_config.snapshot_dir
        )
        snaps2 = list_snapshots(
            watcher_config.protected_files[1], watcher_config.snapshot_dir
        )
        assert len(snaps1) == 1
        assert len(snaps2) == 1


class TestOnMovedDestPath:
    """测试原子写入时 on_moved 用 dest_path 匹配目标文件。"""

    def test_on_moved_matches_dest_path(
        self, tmp_path: Path, watcher_config: SnapshotConfig
    ) -> None:
        """模拟原子写入：temp → target，验证匹配的是 dest_path。"""
        w = FileWatcher(watcher_config, debounce_seconds=0.1)
        pf = watcher_config.protected_files[0]  # on_change file

        # 模拟 on_moved：temp 路径是 src，目标路径是 watched file
        temp_path = str(tmp_path / ".tmp_write_abc123")
        result = w._match_file(temp_path)
        assert result is None  # 临时文件不应匹配

        result = w._match_file(str(pf.path))
        assert result is pf  # 目标文件应该匹配
