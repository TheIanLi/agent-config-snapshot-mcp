"""测试 MCP Server 工具函数：用 mock get_config 验证各同步实现。"""

import tempfile
from pathlib import Path
from unittest import mock

import pytest

from agent_snapshot.config import ProtectedFile, SnapshotConfig
from agent_snapshot.server import (
    _config_path,
    _snapshot_sync,
    _list_sync,
    _diff_sync,
    _rollback_sync,
)


def _make_config(protected_files=None, snapshot_dir=None, max_snapshots=50):
    if protected_files is None:
        protected_files = []
    if snapshot_dir is None:
        snapshot_dir = Path(tempfile.mkdtemp())
    return SnapshotConfig(
        protected_files=protected_files,
        snapshot_dir=snapshot_dir,
        max_snapshots_per_file=max_snapshots,
    )


def _make_pf(path=None, label="test_label", watch="manual"):
    if path is None:
        path = Path(tempfile.mkstemp(suffix=".yaml")[1])
        path.write_text("key: value\n")
    return ProtectedFile(path=path, label=label, watch=watch)


class TestConfigPath:
    """_config_path 查找逻辑测试。"""

    def test_env_var(self, tmp_path, monkeypatch):
        cfg = tmp_path / "custom.yaml"
        cfg.write_text("protected_files: []")
        monkeypatch.setenv("SNAPSHOT_CONFIG", str(cfg))
        assert _config_path() == cfg

    def test_cwd_found(self, monkeypatch):
        monkeypatch.delenv("SNAPSHOT_CONFIG", raising=False)
        cwd_cfg = Path("snapshot-config.yaml")
        if cwd_cfg.exists():
            # 使用实际项目中的配置文件
            path = _config_path()
            assert path.name == "snapshot-config.yaml"
        else:
            pytest.skip("cwd 无 snapshot-config.yaml")

    def test_not_found(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SNAPSHOT_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError):
            _config_path()


class TestGetConfigFallback:
    """get_config 在只有 fallback 路径存在时的测试。"""

    def test_fallback_config_loaded(self, tmp_path, monkeypatch):
        import agent_snapshot.server as server_module
        from agent_snapshot import compat

        # 清除模块级缓存
        server_module._cached_config = None
        server_module._config_mtime = 0.0

        # 切换到空目录
        empty_cwd = tmp_path / "empty"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)
        monkeypatch.delenv("SNAPSHOT_CONFIG", raising=False)

        # 创建 fallback 配置文件
        fallback_dir = tmp_path / ".agent-snapshots"
        fallback_dir.mkdir()
        fallback_cfg = fallback_dir / "snapshot-config.yaml"
        fallback_cfg.write_text(
            "protected_files: []\nsnapshot_dir: ~/.snaps/\n", encoding="utf-8"
        )

        # Mock 家目录为 tmp_path。直接替换 compat.get_home（server 现在就调它），
        # 不碰平台专用的 pwd 模块——这样测试在 Windows 上也能跑（Windows 没有 pwd）。
        monkeypatch.setattr(compat, "get_home", lambda: tmp_path)

        config = server_module.get_config()
        assert config is not None
        assert config.protected_files == []


class TestSnapshotSync:
    """_snapshot_sync 测试。"""

    def test_label_not_found(self):
        cfg = _make_config()
        with mock.patch("agent_snapshot.server.get_config", return_value=cfg):
            result = _snapshot_sync("nonexistent")
            assert "未找到标签" in result
            assert "可用标签:" in result

    def test_snapshot_success(self, tmp_path):
        src = tmp_path / "config.yaml"
        src.write_text("key: value\n")
        pf = _make_pf(path=src)
        snap_dir = tmp_path / "snapshots"
        cfg = _make_config(protected_files=[pf], snapshot_dir=snap_dir)

        with mock.patch("agent_snapshot.server.get_config", return_value=cfg):
            result = _snapshot_sync("test_label")
            assert "快照已创建" in result
            assert len(list((snap_dir / "test_label").glob("*.snapshot.*"))) == 1


class TestListSync:
    """_list_sync 测试。"""

    def test_label_not_found(self):
        cfg = _make_config()
        with mock.patch("agent_snapshot.server.get_config", return_value=cfg):
            result = _list_sync("nonexistent")
            assert "未找到标签" in result

    def test_no_snapshots(self, tmp_path):
        src = tmp_path / "empty.yaml"
        src.write_text("data\n")
        pf = _make_pf(path=src)
        snap_dir = tmp_path / "snapshots"
        cfg = _make_config(protected_files=[pf], snapshot_dir=snap_dir)

        with mock.patch("agent_snapshot.server.get_config", return_value=cfg):
            result = _list_sync("test_label")
            assert "尚无快照" in result


class TestDiffSync:
    """_diff_sync 测试。"""

    def test_label_not_found(self):
        cfg = _make_config()
        with mock.patch("agent_snapshot.server.get_config", return_value=cfg):
            result = _diff_sync("nonexistent", 1)
            assert "未找到标签" in result


class TestRollbackSync:
    """_rollback_sync 测试。"""

    def test_label_not_found(self):
        cfg = _make_config()
        with mock.patch("agent_snapshot.server.get_config", return_value=cfg):
            result = _rollback_sync("nonexistent", 1)
            assert "未找到标签" in result

    def test_rollback_success(self, tmp_path):
        from agent_snapshot.snapshot import create_snapshot

        src = tmp_path / "target.yaml"
        src.write_text("version 1\n")
        pf = _make_pf(path=src)
        snap_dir = tmp_path / "snapshots"
        cfg = _make_config(protected_files=[pf], snapshot_dir=snap_dir)

        # 先手动创建快照
        create_snapshot(pf, snap_dir)
        # 修改文件
        src.write_text("version 2\n")

        with mock.patch("agent_snapshot.server.get_config", return_value=cfg):
            result = _rollback_sync("test_label", 1)
            assert "已回滚" in result
            assert "test_label" in result
            # 文件应恢复到 version 1
            assert src.read_text() == "version 1\n"
