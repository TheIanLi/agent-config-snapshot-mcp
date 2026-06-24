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
    _known_agent_dirs,
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


def test_scan_directory_finds_toml(tmp_path):
    """扫描能找到 .toml 配置文件（Gemini CLI、Codex CLI 等使用）。"""
    (tmp_path / "config.toml").write_text('[tool]\nkey = "value"')

    result = _scan_directory(tmp_path)
    names = {p.name for p in result}
    assert "config.toml" in names


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
    assert "gemini" in presets
    assert "codex" in presets


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

    content = out.read_text(encoding="utf-8")
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


def test_detect_agents(tmp_path):
    """测试 agent 目录检测：使用 mock 目录而非依赖真实环境。"""
    from agent_snapshot import cli

    # 构造模拟 agent 目录
    agent_dir = tmp_path / "mock_agent"
    agent_dir.mkdir()
    (agent_dir / "config.yaml").write_text("key: value")
    (agent_dir / ".env").write_text("TOKEN=secret")

    # Mock _known_agent_dirs 指向临时目录
    with mock.patch.object(cli, "_known_agent_dirs", return_value=[agent_dir]):
        detected = _detect_agents()
        assert str(agent_dir) in detected
        assert len(detected[str(agent_dir)]) == 2


# ---- 新增测试：_known_agent_dirs ----

def test_known_agent_dirs_returns_paths():
    """_known_agent_dirs() 返回的每个元素都应该是 Path 对象。"""
    dirs = _known_agent_dirs()
    for d in dirs:
        assert isinstance(d, Path), f"不是 Path 对象: {d}"


def test_known_agent_dirs_contains_expected_agents():
    """_known_agent_dirs() 应包含常见 agent 目录。"""
    dirs = _known_agent_dirs()
    dir_names = {d.name for d in dirs}
    # 基础 agent（全平台，家目录下以 . 开头）
    assert ".claude" in dir_names
    assert ".hermes_data" in dir_names
    assert ".hermes" in dir_names
    assert ".openclaw" in dir_names
    assert ".gemini" in dir_names
    assert ".codex" in dir_names
    assert ".aider" in dir_names
    assert ".cursor" in dir_names
    assert ".continue" in dir_names
    assert ".copilot" in dir_names
    assert ".qwen" in dir_names
    assert ".opencode" in dir_names


def test_known_agent_dirs_uses_absolute_paths():
    """_known_agent_dirs() 返回的路径应该是绝对路径（因为 compat.get_home() 返回绝对路径）。"""
    dirs = _known_agent_dirs()
    for d in dirs:
        assert d.is_absolute(), f"不是绝对路径: {d}"


# ---- 新增测试：模拟新 agent 目录扫描 ----

def test_scan_directory_gemini_agent(tmp_path):
    """模拟 Gemini agent 目录，验证能扫到 settings.json 和 .env。"""
    (tmp_path / "settings.json").write_text('{"theme": "dark"}')
    (tmp_path / ".env").write_text("GEMINI_API_KEY=xxx")

    result = _scan_directory(tmp_path)
    names = {p.name for p in result}
    assert "settings.json" in names
    assert ".env" in names


def test_scan_directory_codex_agent(tmp_path):
    """模拟 Codex agent 目录，验证能扫到 config.toml。"""
    (tmp_path / "config.toml").write_text('[model]\nname = "o3"')
    (tmp_path / ".env").write_text("OPENAI_API_KEY=xxx")

    result = _scan_directory(tmp_path)
    names = {p.name for p in result}
    assert "config.toml" in names
    assert ".env" in names


def test_detect_agents_with_gemini_dir(tmp_path):
    """模拟检测 Gemini agent 目录。"""
    from agent_snapshot import cli

    gemini_dir = tmp_path / ".gemini"
    gemini_dir.mkdir()
    (gemini_dir / "settings.json").write_text('{"key": "value"}')
    (gemini_dir / ".env").write_text("API_KEY=xxx")

    with mock.patch.object(cli, "_known_agent_dirs", return_value=[gemini_dir]):
        detected = _detect_agents()
        assert str(gemini_dir) in detected
        assert len(detected[str(gemini_dir)]) == 2


def test_detect_agents_extra_dirs(tmp_path):
    """--scan-dir 指定的额外目录应被扫描（支持内置列表之外的任意 agent）。"""
    from agent_snapshot import cli

    custom = tmp_path / "my-custom-agent"
    custom.mkdir()
    (custom / "config.yaml").write_text("key: value")

    # 即使内置列表为空，只靠 extra_dirs 也能扫到
    with mock.patch.object(cli, "_known_agent_dirs", return_value=[]):
        detected = _detect_agents(extra_dirs=[custom])
        assert str(custom) in detected
        names = {p.name for p in detected[str(custom)]}
        assert "config.yaml" in names


def test_build_protected_files_unique_labels_across_dirs(tmp_path):
    """同名 agent 出现在两个目录候选（如 ~/.opencode 和 ~/.config/opencode），
    且各含同名文件时，生成的 label 必须仍然唯一，否则 load_config 会报重复 label。"""
    from agent_snapshot.cli import _build_protected_files

    d1 = tmp_path / ".opencode"
    d2 = tmp_path / ".config" / "opencode"
    d1.mkdir()
    d2.mkdir(parents=True)
    (d1 / "config.json").write_text("{}")
    (d2 / "config.json").write_text("{}")

    detected = {str(d1): [d1 / "config.json"], str(d2): [d2 / "config.json"]}
    files = _build_protected_files(detected, [str(d1), str(d2)])

    labels = [f["label"] for f in files]
    assert len(labels) == 2
    assert len(set(labels)) == 2, f"label 应唯一，实际: {labels}"


# ---- 新增测试：preset 完整性 ----

def _check_preset_structure(data: dict) -> None:
    """检查 preset 数据结构是否完整。"""
    assert "protected_files" in data, "缺少 protected_files 字段"
    assert "snapshot_dir" in data, "缺少 snapshot_dir 字段"
    assert isinstance(data["protected_files"], list), "protected_files 应为列表"
    assert len(data["protected_files"]) > 0, "protected_files 不应为空"
    for item in data["protected_files"]:
        assert "path" in item, f"条目缺少 path: {item}"
        assert "label" in item, f"条目缺少 label: {item}"
        assert "watch" in item, f"条目缺少 watch: {item}"


@pytest.mark.parametrize("preset_name", ["hermes", "openclaw", "claude-code", "gemini", "codex"])
def test_preset_structure(preset_name):
    """每个 preset 都应有完整的 protected_files / snapshot_dir 结构。"""
    data = _load_preset(preset_name)
    _check_preset_structure(data)


def test_preset_gemini_loadable():
    """gemini preset 可加载且包含预期配置。"""
    data = _load_preset("gemini")
    _check_preset_structure(data)
    labels = [f["label"] for f in data["protected_files"]]
    assert "gemini/settings" in labels


def test_preset_codex_loadable():
    """codex preset 可加载且包含预期配置。"""
    data = _load_preset("codex")
    _check_preset_structure(data)
    labels = [f["label"] for f in data["protected_files"]]
    assert "codex/config" in labels


def test_preset_codex_contains_toml():
    """codex preset 应包含 .toml 配置文件。"""
    data = _load_preset("codex")
    paths = [f["path"] for f in data["protected_files"]]
    assert any(".toml" in p for p in paths), f"codex preset 应包含 .toml 文件，实际: {paths}"
