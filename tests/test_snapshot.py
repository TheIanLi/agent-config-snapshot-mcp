"""测试快照核心逻辑：用临时文件验证 snapshot/list/diff/rollback。"""

import tempfile
from pathlib import Path

import pytest

from agent_snapshot.config import ProtectedFile, SnapshotConfig
from agent_snapshot.snapshot import (
    create_snapshot,
    list_snapshots,
    diff_snapshot,
    rollback,
)


@pytest.fixture
def temp_config_file(tmp_path: Path) -> tuple[ProtectedFile, Path]:
    """创建一个带内容的临时文件作为测试目标。"""
    test_file = tmp_path / "test_config.yaml"
    test_file.write_text("key1: value1\nkey2: value2\nkey3: value3\n")
    pf = ProtectedFile(path=test_file, label="测试配置")
    snapshot_dir = tmp_path / "snapshots"
    return pf, snapshot_dir


def test_create_and_list_snapshot(temp_config_file):
    """测试快照创建和列表。"""
    pf, snap_dir = temp_config_file

    # 创建快照
    snap1 = create_snapshot(pf, snap_dir)
    assert snap1.exists()
    assert "test_config.yaml.snapshot." in snap1.name

    # 再创建一个（修改后）
    pf.path.write_text("key1: modified\nkey2: value2\nkey3: value3\n")
    snap2 = create_snapshot(pf, snap_dir)
    assert snap2.exists()

    # 列表验证：最新在前，序号从 1 开始
    snaps = list_snapshots(pf, snap_dir)
    assert len(snaps) == 2
    assert snaps[0]["index"] == 1
    assert snaps[1]["index"] == 2
    assert snaps[0]["filename"] == snap2.name  # 最新的排第一
    assert snaps[1]["filename"] == snap1.name


def test_diff_snapshot(temp_config_file):
    """测试 diff 输出。"""
    pf, snap_dir = temp_config_file

    original = "key1: value1\nkey2: value2\nkey3: value3\n"
    create_snapshot(pf, snap_dir)

    # 修改文件
    pf.path.write_text("key1: modified\nkey2: value2\nnew_key: new_value\n")

    diff_output = diff_snapshot(pf, snap_dir, 1)
    assert diff_output is not None
    # unified diff 应该包含变化
    assert "-key1: value1" in diff_output or "+key1: modified" in diff_output
    assert "+new_key: new_value" in diff_output


def test_diff_no_difference(temp_config_file):
    """测试无差异时的 diff。"""
    pf, snap_dir = temp_config_file
    create_snapshot(pf, snap_dir)

    diff_output = diff_snapshot(pf, snap_dir, 1)
    assert "无差异" in diff_output


def test_rollback(temp_config_file):
    """测试回滚 + 自动安全快照。"""
    pf, snap_dir = temp_config_file

    original = pf.path.read_text()
    create_snapshot(pf, snap_dir)

    # 修改文件
    pf.path.write_text("completely broken content\n")
    assert pf.path.read_text() == "completely broken content\n"

    # 回滚到快照 #1
    result = rollback(pf, snap_dir, 1)
    assert result["rolled_back_to"] == 1
    assert "safe_snapshot" in result

    # 文件已恢复
    assert pf.path.read_text() == original

    # 安全快照应该存在，且内容是修改后的版本
    safe_path = Path(result["safe_snapshot"])
    assert safe_path.exists()
    assert safe_path.read_text() == "completely broken content\n"

    # 回滚后应该多了一个安全快照
    snaps = list_snapshots(pf, snap_dir)
    assert len(snaps) == 2  # 原始快照 + 回滚前自动快照


def test_list_empty_snapshots(temp_config_file):
    """测试无快照时的列表。"""
    pf, snap_dir = temp_config_file
    snaps = list_snapshots(pf, snap_dir)
    assert snaps == []


def test_source_not_found(tmp_path: Path):
    """测试源文件不存在时拍快照报错。"""
    pf = ProtectedFile(path=tmp_path / "nonexistent.yaml", label="不存在")
    snap_dir = tmp_path / "snapshots"
    with pytest.raises(FileNotFoundError):
        create_snapshot(pf, snap_dir)


def test_rollback_invalid_index(temp_config_file):
    """测试无效序号。"""
    pf, snap_dir = temp_config_file
    create_snapshot(pf, snap_dir)
    with pytest.raises(IndexError):
        rollback(pf, snap_dir, 99)


def test_rollback_source_deleted(temp_config_file):
    """测试源文件被删除后仍可回滚（safe snapshot 跳过不阻断）。"""
    pf, snap_dir = temp_config_file

    original = pf.path.read_text()
    create_snapshot(pf, snap_dir)

    # 删除源文件
    pf.path.unlink()
    assert not pf.path.exists()

    # 回滚应该成功恢复
    result = rollback(pf, snap_dir, 1)
    assert result["rolled_back_to"] == 1
    assert pf.path.exists()
    assert pf.path.read_text() == original
    # 安全快照因源文件不存在而跳过
    assert "未拍安全快照" in result["safe_snapshot"]


def test_label_sanitization():
    """测试 label 净化防止路径穿越。"""
    from agent_snapshot.snapshot import _sanitize_label

    assert _sanitize_label("hermes/config") == "hermes_config"
    assert _sanitize_label("../ssh") == "__ssh"
    assert _sanitize_label("a..b") == "a_b"
    assert _sanitize_label("ok_label") == "ok_label"

    with pytest.raises(ValueError):
        _sanitize_label(".")


def test_binary_diff_detection(temp_config_file):
    """测试二进制文件 diff 返回提示而非乱码。"""
    from agent_snapshot.snapshot import _is_binary

    pf, snap_dir = temp_config_file
    # 创建一个含 null 字节的"二进制"文件
    pf.path.write_bytes(b"text\0binary")
    create_snapshot(pf, snap_dir)

    result = diff_snapshot(pf, snap_dir, 1)
    assert "二进制" in result


def test_prune_old_snapshots(temp_config_file):
    """测试超出上限时自动清理最老快照。"""
    from agent_snapshot.snapshot import list_snapshots

    pf, snap_dir = temp_config_file

    # 创建超过上限的快照（上限=3）
    for i in range(5):
        pf.path.write_text(f"version {i}\n")
        create_snapshot(pf, snap_dir, max_snapshots=3)

    snaps = list_snapshots(pf, snap_dir)
    assert len(snaps) == 3
    # 最老的快照被删，保留的是最新的 3 个
    assert "version 4" in pf.path.read_text()
