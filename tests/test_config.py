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


def test_label_windows_invalid_chars_sanitized():
    """Windows 非法文件名字符 : * ? | < > " 应被净化成下划线（白名单）。"""
    assert validate_label('a:b*c?d|e<f>g"h') == "a_b_c_d_e_f_g_h"


def test_label_windows_reserved_name_escaped():
    """Windows 保留设备名（CON/NUL/...）不能原样当目录名，需转义。"""
    result = validate_label("CON")
    assert result.upper() != "CON"


def test_label_windows_reserved_name_with_ext_escaped():
    """带扩展名的保留名（CON.md）同样要转义，Windows 上 CON.md 仍是保留名。"""
    result = validate_label("nul.txt")
    assert result.split(".")[0].upper() != "NUL"


def test_label_cjk_preserved():
    """非 ASCII（中文）label 应被保留，不能整段被替换成下划线。"""
    assert validate_label("我的配置") == "我的配置"


def test_label_distinct_cjk_stay_distinct():
    """两个不同的中文 label 净化后仍要保持不同，避免误判重复。"""
    assert validate_label("配置一") != validate_label("配置二")


def test_invalid_daily_time_raises(tmp_path):
    """daily_time 格式非法应在加载时报错，而不是等守护进程运行时才崩。"""
    cfg = tmp_path / "bad_time.yaml"
    cfg.write_text(
        "protected_files: []\n"
        "snapshot_dir: ~/.snaps/\n"
        "daily_time: not-a-time\n"
    )
    with pytest.raises(ValueError, match="daily_time"):
        load_config(str(cfg))


def test_single_digit_hour_daily_time_raises(tmp_path):
    """单位数小时（如 "4:00"）虽是常见写法，但 schedule.at 只认两位小时，
    必须在加载时就拦下，否则会等到守护进程注册定时任务时才崩。"""
    cfg = tmp_path / "single_digit.yaml"
    cfg.write_text(
        "protected_files: []\n"
        "snapshot_dir: ~/.snaps/\n"
        "daily_time: '4:00'\n"
    )
    with pytest.raises(ValueError, match="daily_time"):
        load_config(str(cfg))


def test_validated_daily_time_accepted_by_schedule(tmp_path):
    """凡是能通过 load_config 校验的 daily_time，schedule.at 都必须能接受。
    这条测试把两边的格式约定钉死，防止校验放过会让守护进程崩溃的值。"""
    import schedule

    for value in ("04:00", "00:00", "23:59", "06:30:15"):
        cfg = tmp_path / f"time_{value.replace(':', '')}.yaml"
        cfg.write_text(
            "protected_files: []\n"
            "snapshot_dir: ~/.snaps/\n"
            f"daily_time: '{value}'\n"
        )
        config = load_config(str(cfg))
        # 不抛异常即为通过
        schedule.every().day.at(config.daily_time).do(lambda: None)
        schedule.clear()


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
