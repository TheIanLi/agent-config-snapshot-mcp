"""测试 init CLI 命令：扫描、预设加载、配置生成。"""

import tempfile
from pathlib import Path
from unittest import mock

import pytest

from agent_snapshot.cli import (
    _scan_directory,
    _detect_agents,
    _list_presets,
    _load_preset,
    _generate_config,
)


def test_scan_directory_empty(tmp_path):
    """空目录扫描结果为空。"""
    assert _scan_directory(tmp_path) == []


def test_scan_directory_finds_config_files(tmp_path):
    """扫描能找到 yaml、json、.env 等配置文件。"""
    (tmp_path / "config.yaml").write_text("key: value")
    (tmp_path / ".env").write_text("TOKEN=secret")
    (tmp_path / "SOUL.md").write_text("# Soul")

    result = _scan_directory(tmp_path)
    names = {p.name for p in result}
    assert "config.yaml" in names
    assert ".env" in names
    assert "SOUL.md" in names


def test_scan_directory_skips_subdirs(tmp_path):
    """跳过 sessions、logs 等无关子目录。"""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "data.json").write_text("big")

    assert _scan_directory(tmp_path) == []


def test_scan_directory_scans_memories(tmp_path):
    """扫描 memories 子目录。"""
    mem = tmp_path / "memories"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("# Memory")
    (mem / "USER.md").write_text("# User")

    result = _scan_directory(tmp_path)
    names = {p.name for p in result}
    assert "MEMORY.md" in names
    assert "USER.md" in names


def test_scan_directory_skips_large_files(tmp_path):
    """跳过 >1MB 的 JSON 文件。"""
    (tmp_path / "config.yaml").write_text("ok")
    (tmp_path / "huge.json").write_bytes(b"x" * (1024 * 1024 + 1))

    result = _scan_directory(tmp_path)
    names = {p.name for p in result}
    assert "config.yaml" in names
    assert "huge.json" not in names


def test_list_presets():
    """列出可用预设模板。"""
    presets = _list_presets()
    assert "hermes" in presets
    assert "openclaw" in presets
    assert "claude-code" in presets


def test_load_preset_hermes():
    """加载 hermes 预设模板。"""
    data = _load_preset("hermes")
    assert "protected_files" in data
    assert data["snapshot_dir"] == "~/.agent-snapshots/"
    assert len(data["protected_files"]) >= 5


def test_load_preset_not_found():
    """加载不存在的预设报错。"""
    with pytest.raises(FileNotFoundError):
        _load_preset("nonexistent-agent")


def test_generate_config(tmp_path):
    """测试生成配置文件。"""
    data = {
        "protected_files": [
            {"path": "~/.hermes_data/config.yaml", "label": "测试"},
        ],
        "snapshot_dir": "~/.snaps/",
        "retention": {"max_snapshots_per_file": 10},
    }
    out = tmp_path / "out.yaml"
    result = _generate_config(data, out)
    assert result == out
    assert out.exists()

    content = out.read_text()
    assert "测试" in content
    assert "~/.snaps/" in content


def test_generate_config_overwrites(tmp_path):
    """测试覆盖已存在的配置。"""
    out = tmp_path / "out.yaml"
    out.write_text("old")
    _generate_config({"protected_files": []}, out)
    # 不抛异常，文件已覆盖
    content = out.read_text()
    assert "protected_files" in content
    assert "old" not in content


def test_detect_agents():
    """测试 agent 目录检测。"""
    detected = _detect_agents()
    # 用户环境应该至少有一个
    assert len(detected) >= 1
    # ~/.hermes_data 应该存在
    home = str(Path.home())
    assert any(home in d for d in detected)
