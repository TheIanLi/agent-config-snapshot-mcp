# Changelog

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
