"""init CLI 命令 — 首次安装时生成 snapshot-config.yaml。"""

import argparse
import logging
import os
import shutil
import sys
from importlib.resources import files as _files
from pathlib import Path

import yaml

from . import compat
from .config import load_config
from .snapshot import (
    SnapshotReason,
    create_snapshot,
    list_snapshots,
    diff_snapshot,
    rollback,
)

logger = logging.getLogger(__name__)

_PRESETS_DIR = Path(str(_files("agent_snapshot"))) / "presets"


def _known_agent_dirs() -> list[Path]:
    """返回所有已知 AI agent 的配置目录候选列表。

    跨平台设计：
    - POSIX（Linux/macOS）：统一用 compat.get_home() 获取家目录（不依赖 $HOME）
    - Windows：家目录下的配置 + %APPDATA% 下的配置

    注意：这里只列出候选目录，_detect_agents() 扫描时会自动跳过不存在的目录，
    所以可以放心多列，宁缺毋滥。
    """
    home = compat.get_home()
    dirs = []

    # ---- 家目录下的 agent 配置（全平台通用） ----

    # Claude Code（Anthropic）
    dirs.append(home / ".claude")
    # Hermes Agent
    dirs.append(home / ".hermes_data")
    dirs.append(home / ".hermes")
    # OpenClaw
    dirs.append(home / ".openclaw")
    # Google Gemini CLI
    dirs.append(home / ".gemini")
    # OpenAI Codex CLI
    dirs.append(home / ".codex")
    # Aider（AI pair programming）
    dirs.append(home / ".aider")
    # Cursor（AI 代码编辑器）
    dirs.append(home / ".cursor")
    # Continue（VS Code AI 插件）
    dirs.append(home / ".continue")
    # GitHub Copilot CLI
    dirs.append(home / ".copilot")
    # Qwen Code（通义千问代码助手）
    dirs.append(home / ".qwen")
    # OpenCode
    dirs.append(home / ".opencode")

    # ---- XDG 风格路径（仅 POSIX，Windows 不适用） ----
    if compat.IS_POSIX:
        # GitHub Copilot CLI（XDG 风格路径）
        dirs.append(home / ".config" / "github-copilot")
        # OpenCode（XDG 风格路径）
        dirs.append(home / ".config" / "opencode")

    # ---- Windows 特有路径：%APPDATA% ----
    if compat.IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        if appdata:
            appdata_path = Path(appdata)
            # Cursor 在 Windows 上的配置目录
            dirs.append(appdata_path / "Cursor")
            # Continue 在 Windows 上的配置目录
            dirs.append(appdata_path / "Continue")

    return dirs


# 扫描时要跳过的子目录（日志、会话、缓存等大量文件）
_SKIP_SUBDIRS = {
    "sessions", "logs", "cache", "sandboxes", "workspace",
    "audio_cache", "image_cache", "cron", "plans", "tasks",
    "session-env", "shell-snapshots", "file-history", "backups",
    "downloads", "paste-cache", "bin", "home", "skills", "skins",
    "platforms", "pairing", "projects",
    # Gemini CLI 的会话/缓存目录（"sessions" 已在上面列出，集合自动去重）
    "conversations", "tmp",
}

# 只在特定子目录中递归扫描（仅这些目录有价值的小配置文件）
_SCAN_SUBDIRS = {"memories", "plugins", "hooks"}

# watch 模式自动分配规则：根据文件名决定监听策略
_WATCH_RULES = {
    ".env":                "on_change",
    "config.yaml":         "on_change",
    "SOUL.md":             "on_change",
    "auth.json":           "on_change",
    "channel_directory.json": "on_change",
    ".credentials.json":   "on_change",
    "settings.json":       "on_change",
    "MEMORY.md":           "daily",
    "USER.md":             "daily",
    # Gemini / Codex 等 agent 的配置文件
    "config.toml":         "on_change",
    "config.json":         "on_change",
}

# 单文件大小上限（1MB），超过此大小的文件不纳入保护
_MAX_FILE_SIZE = 1 * 1024 * 1024

# 扫描时排除的文件名（不需要保护的临时/运行时数据）
_EXCLUDE_FILES = {
    "gateway_state.json",
    "gateway_voice_mode.json",
    "processes.json",
    ".restart_last_processed.json",
    ".skills_prompt_snapshot.json",
    "state.db",
    "installed_plugins.json",
    "known_marketplaces.json",
    "plugin-catalog-cache.json",
}


def _is_config_file(p: Path) -> bool:
    """过滤器：检查文件是否为应保护的小型配置文件。

    顶层扫描由 caller 额外处理大小限制和特殊文件名排除。
    支持的扩展名：.yaml / .yml / .json / .md / .toml / .env
    （.toml 是 Gemini CLI、Rust 工具链等常用的配置格式）
    """
    if not p.is_file():
        return False
    if p.name in _EXCLUDE_FILES:
        return False
    # 特殊文件名（无标准扩展名但属于配置文件）
    if p.name in (".env",):
        return p.stat().st_size < _MAX_FILE_SIZE
    ext = p.suffix.lower()
    if ext not in (".yaml", ".yml", ".json", ".md", ".toml"):
        return False
    # 大小上限对所有受支持扩展名生效（之前只检查 .json，导致大 .toml/.md/.yaml
    # 在子目录扫描时漏过——子目录扫描只走本函数，没有顶层那层额外的大小兜底）。
    if p.stat().st_size >= _MAX_FILE_SIZE:
        return False
    return True


def _scan_directory(dir_path: Path) -> list[Path]:
    """扫描目录下适合保护的小型配置文件（.yaml/.json/.md/.env 等）。"""
    if not dir_path.exists():
        return []

    found = []

    # 顶层文件：匹配小配置文件，排除大文件和特殊数据库文件
    _TOP_LARGE_FILES = {"models_dev_cache.json", "state.db", "kanban.db"}
    top_patterns = ["*.yaml", "*.yml", "*.json", "*.md", ".env", "SOUL.md", "*.toml"]
    for pattern in top_patterns:
        for p in dir_path.glob(pattern):
            if p.name in _TOP_LARGE_FILES:
                continue
            if not _is_config_file(p):
                continue
            if p.stat().st_size >= _MAX_FILE_SIZE:
                continue
            found.append(p)

    # 仅扫描特定有价值的子目录（不含其 cache 等子目录）
    for sub_name in _SCAN_SUBDIRS:
        sub = dir_path / sub_name
        if not sub.is_dir():
            continue
        for p in sub.glob("*"):
            if _is_config_file(p):
                found.append(p)
        # 递归进入非 cache 的子目录
        for child in sub.iterdir():
            if child.is_dir() and child.name not in _SKIP_SUBDIRS:
                for p in child.glob("*"):
                    if _is_config_file(p):
                        found.append(p)

    # 去重并排序
    seen = set()
    result = []
    for p in sorted(found):
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            result.append(p)
    return result


def _detect_agents(extra_dirs: list[Path] | None = None) -> dict[str, list[Path]]:
    """扫描已知 agent 目录（外加用户用 --scan-dir 指定的目录），返回检测结果。

    extra_dirs: 用户额外指定要扫描的目录。这让本工具能支持任意 agent——
    哪怕它的目录不在内置已知列表里，也能用 --scan-dir 指过去。
    """
    dirs = list(_known_agent_dirs())
    if extra_dirs:
        dirs.extend(extra_dirs)

    detected = {}
    # 去重：同一目录可能既在已知列表又被 --scan-dir 指定
    seen: set[Path] = set()
    for d in dirs:
        try:
            key = d.resolve()
        except OSError:
            key = d
        if key in seen:
            continue
        seen.add(key)
        files = _scan_directory(d)
        if files:
            detected[str(d)] = files
    return detected


def _list_presets() -> list[str]:
    """列出可用的预设模板名称。"""
    if not _PRESETS_DIR.exists():
        return []
    return sorted(
        p.stem for p in _PRESETS_DIR.glob("*.yaml")
    )


def _load_preset(name: str) -> dict:
    """加载指定预设模板。"""
    preset_file = _PRESETS_DIR / f"{name}.yaml"
    if not preset_file.exists():
        available = ", ".join(_list_presets())
        raise FileNotFoundError(
            f"预设模板不存在: {name}\n可用预设: {available}"
        )
    with open(preset_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _generate_config(preset_data: dict, output_path: Path) -> Path:
    """将配置写入文件。已存在则提示。"""
    if output_path.exists():
        logger.warning("配置文件已存在: %s，将覆盖", output_path)

    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(
            preset_data,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
    return output_path


def _interactive_select(detected: dict[str, list[Path]]) -> list[dict]:
    """交互式选择要保护的文件。"""
    home = compat.get_home()
    print("\n检测到以下 agent 目录及其可保护文件:\n")

    dirs = list(detected.keys())
    for i, d in enumerate(dirs, 1):
        print(f"  [{i}] {d}")
        for f in detected[d]:
            try:
                rel = f.relative_to(home)
                print(f"      - ~/{rel}")
            except ValueError:
                # Windows 上 %APPDATA% 路径无法 relative_to 家目录
                print(f"      - {f}")
        print()

    print("选择要保护的目录（输入序号，多选用逗号分隔，回车=全选）:")
    choice = input("> ").strip()

    if not choice:
        selected_dirs = dirs
    else:
        try:
            indices = [int(c.strip()) for c in choice.split(",")]
            selected_dirs = [dirs[i - 1] for i in indices if 1 <= i <= len(dirs)]
        except (ValueError, IndexError):
            print("无效输入，跳过。")
            return []

    return _build_protected_files(detected, selected_dirs)


def _build_protected_files(
    detected: dict[str, list[Path]], selected_dirs: list[str]
) -> list[dict]:
    """把选中目录里的文件转成 protected_files 配置条目。

    被交互选择和 --all（全选）两条路径共用，避免逻辑重复。
    """
    home = compat.get_home()

    # 先算出每个文件“去掉扩展名”的基础 label，并统计每个基础 label 出现几次。
    # 同一目录下若存在同名不同扩展（如 config.toml + config.json），去掉扩展名后
    # 会得到相同 label，进而触发 load_config 的重复 label 校验把工具卡死。
    entries = []
    base_label_counts: dict[str, int] = {}
    for d in selected_dirs:
        agent_name = Path(d).name.lstrip(".")
        for f in detected[d]:
            rel_to_agent = f.relative_to(Path(d))
            # as_posix() 保证跨平台一致（Windows 上不会出现反斜杠）。
            base_label = f"{agent_name}/{rel_to_agent.with_suffix('').as_posix()}"
            base_label_counts[base_label] = base_label_counts.get(base_label, 0) + 1
            entries.append((f, rel_to_agent, agent_name, base_label))

    files = []
    used_labels: set[str] = set()
    for f, rel_to_agent, agent_name, base_label in entries:
        # 基础 label 唯一就直接用；同名不同扩展（config.toml + config.json）则带扩展名区分。
        if base_label_counts[base_label] > 1:
            label = f"{agent_name}/{rel_to_agent.as_posix()}"
        else:
            label = base_label
        # 最终唯一性兜底：同一 agent 可能出现在两个目录候选里
        # （如 ~/.opencode 和 ~/.config/opencode 都含 config.json），
        # 上面的扩展名区分仍可能撞车。这里统一补 -2/-3 后缀，确保 label 全局唯一，
        # 否则会触发 load_config 的重复 label 校验把工具卡死。
        if label in used_labels:
            n = 2
            while f"{label}-{n}" in used_labels:
                n += 1
            label = f"{label}-{n}"
        used_labels.add(label)
        watch = _WATCH_RULES.get(f.name, "manual")
        # 优先用 ~ 相对家目录的写法（跨机器更通用）；
        # 若文件不在家目录下（如 Windows 上被重定向的 %APPDATA%），退回绝对路径。
        try:
            rel = f.relative_to(home)
            path_str = f"~/{rel.as_posix()}"
        except ValueError:
            path_str = str(f)
        files.append({
            "path": path_str,
            "label": label,
            "watch": watch,
        })

    return files


def run_init(args: argparse.Namespace) -> None:
    """执行 init 命令。"""
    output = Path(args.output) if args.output else Path("snapshot-config.yaml")

    # 已存在配置时，默认不覆盖：这份 YAML 往往是用户精心挑选的受保护文件清单，
    # 静默覆盖会让保护范围悄悄丢失。要覆盖必须显式传 --force。
    if output.exists() and not args.force:
        print(
            f"配置文件已存在: {output}\n"
            f"如需重新生成并覆盖，请加 --force；否则请手动编辑现有文件。"
        )
        return

    if args.preset:
        # 预设模式：直接加载模板
        data = _load_preset(args.preset)
        path = _generate_config(data, output)
        print(f"已从预设「{args.preset}」生成配置文件: {path}")
        return

    # 扫描模式（支持 --scan-dir 指定额外目录，让任意 agent 都能被扫到）
    extra_dirs = [Path(d).expanduser() for d in (args.scan_dir or [])]
    detected = _detect_agents(extra_dirs=extra_dirs)
    if not detected:
        presets = _list_presets()
        print("未检测到任何已知 agent 目录。")
        if presets:
            print(f"可用的预设模板: {', '.join(presets)}")
            print(f"用法: python -m agent_snapshot init --preset <名称>")
        else:
            print("请手动编辑 snapshot-config.yaml 配置文件。")
        return

    if args.all:
        # 非交互：直接保护检测到的全部文件，跳过逐项询问
        files = _build_protected_files(detected, list(detected.keys()))
        print(f"已自动选择检测到的全部 {len(files)} 个文件（--all）。")
    else:
        # 交互选择（按目录选，回车 = 全选所有目录）
        files = _interactive_select(detected)
    if not files:
        print("未选择任何文件，退出。")
        return

    data = {
        "protected_files": files,
        "snapshot_dir": "~/.agent-snapshots/",
        "daily_time": "04:00",
        "retention": {"max_snapshots_per_file": 50},
    }
    path = _generate_config(data, output)
    print(f"配置文件已生成: {path}")
    print(f"共保护 {len(files)} 个文件。")


def _resolve_label(label: str):
    """根据 label 查找 ProtectedFile。"""
    cfg = load_config()
    pf = cfg.get_by_label(label)
    if pf is None:
        available = ", ".join(p.label for p in cfg.protected_files)
        print(f"未找到标签「{label}」。可用标签: {available}")
        return None, cfg
    return pf, cfg


def run_snapshot(args: argparse.Namespace) -> None:
    pf, cfg = _resolve_label(args.label)
    if pf is None:
        return
    snap_path = create_snapshot(
        pf, cfg.snapshot_dir, cfg.max_snapshots_per_file, reason=SnapshotReason.MANUAL
    )
    print(f"快照已创建: {snap_path}")


def run_list(args: argparse.Namespace) -> None:
    pf, cfg = _resolve_label(args.label)
    if pf is None:
        return
    snaps = list_snapshots(pf, cfg.snapshot_dir)
    if not snaps:
        print(f"「{args.label}」尚无快照。")
        return
    print(f"「{args.label}」快照列表（最新在前）:")
    for s in snaps:
        size_kb = s["size"] / 1024
        print(f"  #{s['index']}  {s['timestamp']}  {size_kb:.1f}KB  [{s['reason']}]  {s['filename']}")


def run_diff(args: argparse.Namespace) -> None:
    pf, cfg = _resolve_label(args.label)
    if pf is None:
        return
    result = diff_snapshot(pf, cfg.snapshot_dir, args.index)
    print(result)


def run_rollback(args: argparse.Namespace) -> None:
    pf, cfg = _resolve_label(args.label)
    if pf is None:
        return
    result = rollback(pf, cfg.snapshot_dir, args.index, cfg.max_snapshots_per_file)
    print(
        f"已回滚「{args.label}」到快照 #{result['rolled_back_to']}\n"
        f"快照时间: {result['snapshot_timestamp']}\n"
        f"回滚前自动快照: {result['safe_snapshot']}"
    )


def run_watch(args: argparse.Namespace) -> None:
    """启动文件监听守护进程。"""
    from .watcher import FileWatcher

    cfg = load_config()
    on_change = [pf for pf in cfg.protected_files if pf.watch == "on_change"]
    daily = [pf for pf in cfg.protected_files if pf.watch == "daily"]
    if not on_change and not daily:
        print("没有需要监听的文件（所有 watch 字段均为 manual），退出。")
        return

    print(f"监听启动: on_change={len(on_change)} 个, daily={len(daily)} 个")
    watcher = FileWatcher(cfg)
    if args.daemon:
        watcher.run_daemon()
    else:
        watcher.run()


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="agent-snapshot",
        description="AI agent 配置文件快照管理工具",
    )
    sub = parser.add_subparsers(dest="command")

    # init 子命令
    init_parser = sub.add_parser("init", help="初始化配置文件")
    init_parser.add_argument(
        "--preset",
        choices=_list_presets() or None,
        help="直接使用预设模板，跳过交互扫描",
    )
    init_parser.add_argument(
        "--output", "-o",
        default="snapshot-config.yaml",
        help="输出路径 (默认: snapshot-config.yaml)",
    )
    init_parser.add_argument(
        "--scan-dir",
        action="append",
        metavar="DIR",
        help="额外扫描的目录（可多次指定），用于支持内置列表之外的任意 agent，"
             "例如 --scan-dir ~/.my-agent",
    )
    init_parser.add_argument(
        "--all", "-y",
        action="store_true",
        help="非交互模式：直接保护检测到的全部文件，跳过逐项询问",
    )
    init_parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="覆盖已存在的配置文件（默认不覆盖，避免误删现有保护清单）",
    )
    init_parser.set_defaults(func=run_init)

    # snapshot 子命令
    snap_parser = sub.add_parser("snapshot", help="对指定标签的配置文件拍一份快照")
    snap_parser.add_argument("label", help="配置文件标签")
    snap_parser.set_defaults(func=run_snapshot)

    # list 子命令
    list_parser = sub.add_parser("list", help="列出指定标签的所有历史快照")
    list_parser.add_argument("label", help="配置文件标签")
    list_parser.set_defaults(func=run_list)

    # diff 子命令
    diff_parser = sub.add_parser("diff", help="对比当前文件和指定快照的差异")
    diff_parser.add_argument("label", help="配置文件标签")
    diff_parser.add_argument("index", type=int, help="快照序号（从 list 获取）")
    diff_parser.set_defaults(func=run_diff)

    # rollback 子命令
    rollback_parser = sub.add_parser("rollback", help="回滚到指定快照版本")
    rollback_parser.add_argument("label", help="配置文件标签")
    rollback_parser.add_argument("index", type=int, help="目标快照序号（从 list 获取）")
    rollback_parser.set_defaults(func=run_rollback)

    # watch 子命令
    watch_parser = sub.add_parser("watch", help="启动文件监听守护进程，自动拍快照")
    watch_parser.add_argument("--daemon", action="store_true", help="后台运行")
    watch_parser.set_defaults(func=run_watch)

    return parser


def main():
    """CLI 入口。"""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    try:
        args.func(args)
    except FileNotFoundError as e:
        print(f"错误: {e}", file=sys.stderr)
        if "snapshot-config.yaml" in str(e) or "SNAPSHOT_CONFIG" not in os.environ:
            print("\n提示: 请先运行 agent-snapshot init 初始化配置文件，"
                  "或设置 SNAPSHOT_CONFIG 环境变量指向已有的配置文件。",
                  file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        # 配置内容非法（重复 label / watch 模式错误 / daily_time 格式错误等）。
        # 这类错误对初学者而言，原始 traceback 毫无帮助，给一句可读的提示即可。
        print(f"错误: 配置文件有问题 —— {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
