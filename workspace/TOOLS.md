# Tools Reference

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## Quick Reference

- Use `task_create` / `task_update` / `task_list` for multi-step work.
- Use `TodoWrite` for short, single-session checklists.
- Use the `task` tool to delegate work to a sub-agent.
- Use `load_skill` to load specialised knowledge before tackling a domain.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace
