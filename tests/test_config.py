"""测试配置加载：格式错误、缺失字段、边界情况。"""

import tempfile
from pathlib import Path

import pytest
import yaml

from agent_snapshot.config import load_config, validate_label


def test_malformed_yaml(tmp_path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("protected_files: [this: is: broken: yaml")
    with pytest.raises(yaml.YAMLError):
        load_config(str(cfg))


def test_missing_protected_files(tmp_path):
    cfg = tmp_path / "no_files.yaml"
    cfg.write_text("snapshot_dir: ~/.snaps/\n")
    config = load_config(str(cfg))
    assert config.protected_files == []


def test_empty_protected_files(tmp_path):
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("protected_files: []\nsnapshot_dir: ~/.snaps/\n")
    config = load_config(str(cfg))
    assert config.protected_files == []
    assert config.max_snapshots_per_file == 50  # 默认值


def test_duplicate_label_raises(tmp_path):
    """配置里有重复 label 应该报错，避免快照互相覆盖、文件静默失效。"""
    cfg = tmp_path / "dup.yaml"
    cfg.write_text(
        "protected_files:\n"
        "  - path: ~/.a/config.json\n"
        "    label: agent/config\n"
        "  - path: ~/.b/config.json\n"
        "    label: agent/config\n"
        "snapshot_dir: ~/.snaps/\n"
    )
    with pytest.raises(ValueError, match="重复的 label"):
        load_config(str(cfg))


def test_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.yaml")


def test_missing_snapshot_dir(tmp_path):
    cfg = tmp_path / "no_dir.yaml"
    cfg.write_text(
        "protected_files:\n"
        "  - path: ~/test.yaml\n"
        "    label: test\n"
    )
    config = load_config(str(cfg))
    assert config.snapshot_dir is not None
    assert ".agent-snapshots" in str(config.snapshot_dir)


def test_missing_retention(tmp_path):
    cfg = tmp_path / "no_retention.yaml"
    cfg.write_text(
        "protected_files:\n"
        "  - path: ~/test.yaml\n"
        "    label: test\n"
        "snapshot_dir: ~/.snaps/\n"
    )
    config = load_config(str(cfg))
    assert config.max_snapshots_per_file == 50  # 默认值


def test_default_watch_mode(tmp_path):
    cfg = tmp_path / "default_watch.yaml"
    cfg.write_text(
        "protected_files:\n"
        "  - path: ~/test.yaml\n"
        "    label: test\n"
        "snapshot_dir: ~/.snaps/\n"
    )
    config = load_config(str(cfg))
    assert config.protected_files[0].watch == "manual"


def test_label_empty_raises(tmp_path):
    cfg = tmp_path / "empty_label.yaml"
    cfg.write_text(
        "protected_files:\n"
        "  - path: ~/test.yaml\n"
        "    label: .\n"
        "snapshot_dir: ~/.snaps/\n"
    )
    with pytest.raises(ValueError, match="label 不能为空"):
        load_config(str(cfg))


def test_label_traversal_sanitized(tmp_path):
    cfg = tmp_path / "traversal.yaml"
    cfg.write_text(
        "protected_files:\n"
        "  - path: ~/test.yaml\n"
        "    label: ../etc/passwd\n"
        "snapshot_dir: ~/.snaps/\n"
    )
    config = load_config(str(cfg))
    assert "/" not in config.protected_files[0].label
    assert ".." not in config.protected_files[0].label


def test_expand_path_with_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_HOME", str(tmp_path))
    cfg = tmp_path / "env.yaml"
    cfg.write_text(
        "protected_files:\n"
        "  - path: $TEST_HOME/app.yaml\n"
        "    label: env_test\n"
        "snapshot_dir: ~/.snaps/\n"
    )
    config = load_config(str(cfg))
    assert config.protected_files[0].path == tmp_path / "app.yaml"


def test_daily_time_default(tmp_path):
    cfg = tmp_path / "daily.yaml"
    cfg.write_text(
        "protected_files: []\n"
        "snapshot_dir: ~/.snaps/\n"
    )
    config = load_config(str(cfg))
    assert config.daily_time == "04:00"


def test_daily_time_custom(tmp_path):
    cfg = tmp_path / "daily_custom.yaml"
    cfg.write_text(
        "protected_files: []\n"
        "snapshot_dir: ~/.snaps/\n"
        "daily_time: '06:30'\n"
    )
    config = load_config(str(cfg))
    assert config.daily_time == "06:30"


def test_invalid_watch_mode(tmp_path):
    cfg = tmp_path / "invalid_watch.yaml"
    cfg.write_text(
        "protected_files:\n"
        "  - path: ~/test.yaml\n"
        "    label: test\n"
        "    watch: onchange\n"
        "snapshot_dir: ~/.snaps/\n"
    )
    with pytest.raises(ValueError, match="watch 模式无效"):
        load_config(str(cfg))


def test_valid_watch_modes(tmp_path):
    for mode in ("on_change", "daily", "manual"):
        cfg = tmp_path / f"valid_{mode}.yaml"
        cfg.write_text(
            "protected_files:\n"
            "  - path: ~/test.yaml\n"
            "    label: test\n"
            f"    watch: {mode}\n"
            "snapshot_dir: ~/.snaps/\n"
        )
        config = load_config(str(cfg))
        assert config.protected_files[0].watch == mode
