# Changelog

## v0.3.0

- Cross-platform support: Windows / macOS / Linux (new `compat.py` compatibility layer)
- Cross-platform PID lock and file watching via `filelock` (POSIX flock / Windows msvcrt)
- Windows snapshot-directory hardening via ACL (`icacls`), best-effort
- Add `gemini` and `codex` presets; expand auto-detected agent directories
- Add `init --scan-dir` to scan arbitrary directories and `init --all`/`-y` for non-interactive setup
- Reject duplicate labels at config load time
- Whitelist-based label sanitization (only `A-Za-z0-9_.-` kept) with Windows reserved-name avoidance
- Validate `daily_time` format (24-hour `HH:MM` / `HH:MM:SS`) at config load
- Pin dependency minimum versions to guard against breaking upstream releases
- Run CI across Ubuntu / macOS / Windows

## v0.2.0

- Fix snapshot list ordering by explicit timestamp parsing
- Use importlib.resources for presets directory lookup
- Add test suite (server + config tests) and CI pipeline
- Add `snapshot-config.example.yaml` and clean tracked artifacts
- Add LICENSE (MIT)
- Add platform compatibility note to README

## v0.1.0

- Initial release
- CLI + MCP Server for agent config file snapshots
- Snapshot creation, listing, diff, and rollback
- File watcher with on_change and daily trigger modes
- Interactive init with presets for popular agent frameworks
