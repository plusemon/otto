# AGENTS.md

## Project

otto — terminal-native coding agent MVP. Textual TUI over Kilo OpenAI-compatible gateway via `google-antigravity`. Single Python package, no monorepo.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run

```bash
otto
```

Requires `KILO_API_KEY` env var (or it falls back to a dummy key). See `.env.example`.

## Slash commands

| command | effect |
|---|---|
| `/session new` | create a new session and make it active |
| `/session list` | show all sessions |
| `/session switch <id>` | switch to session by ID prefix |
| `/session resume <id>` | resume a session from disk |
| `/model` | show current model |
| `/model <name>` | set model for new sessions |
| `/name <id> <name>` | name a session for easier identification |
| `/quit` / `:q` / `Ctrl-D` | exit |

## Critical import rule

**Always use `from google import antigravity`** — never `import antigravity` (that's a Python stdlib Easter egg). This applies everywhere: `agent_config.py`, `session.py`, any new files.

## SDK gotcha: no `api_key` kwarg

`LocalOpenAIAgentConfig` has no `api_key` field. Pass the key via `env={"KILO_API_KEY": ...}` — it's forwarded to the Go harness subprocess. See `otto/STEP0_REPORT.md` for full SDK introspection.

## `base_url` must include `/v1`

Correct: `https://api.kilo.ai/api/gateway/v1`
Wrong: `https://api.kilo.ai/api/gateway`

## Built-in tools and policies

Current config enables read/write built-in tools (`VIEW_FILE`, `LIST_DIR`, `FIND_FILE`, `SEARCH_DIR`, `CREATE_FILE`, `EDIT_FILE`, `RUN_COMMAND`) plus `FINISH`, and custom Python tools (see below). Policies are applied per-session in `policy.py` — see the policy tables there for the full allow/ask_user/deny matrix.

Actual `BuiltinTools` enum names (v0.1.15): `LIST_DIR`, `SEARCH_DIR`, `FIND_FILE`, `VIEW_FILE`, `CREATE_FILE`, `EDIT_FILE`, `RUN_COMMAND`, `ASK_QUESTION`, `START_SUBAGENT`, `GENERATE_IMAGE`, `SEARCH_WEB`, `READ_URL_CONTENT`, `FINISH`. Note: `READ_FILE` does not exist — it's `VIEW_FILE`.

## Custom tools

otto registers custom Python callables via the `tools=` config param on `LocalOpenAIAgentConfig`. The SDK auto-generates JSON Schema from type annotations and docstrings, serializes to protobuf, and the Go harness dispatches calls back to `ToolRunner` which invokes the functions. Policies match on tool name strings — no special registration needed.

| Tool | Description | Build mode | Plan mode |
|------|-------------|------------|-----------|
| `run_tests(scope?)` | Run project test suite | allow | allow |
| `run_linter()` | Run project linter | allow | allow |
| `git_diff(path?)` | Show working tree diff | allow | allow |
| `git_status()` | Show working tree status | allow | allow |
| `git_log(n?)` | Show recent commits | allow | allow |
| `git_branch(name?)` | List/create branches | allow | deny |
| `git_commit(message)` | Stage all + commit | allow | deny |
| `git_push()` | Push to origin | ask_user | deny |
| `open_pull_request(title,body,branch?)` | Create PR via gh CLI | ask_user | deny |

Test/lint commands are auto-detected from project files (pytest.ini, pyproject.toml, package.json, etc.) and can be overridden via `.otto/config.json`.

## `.otto/config.json`

Optional per-project overrides:

```json
{
    "test_command": "pytest -v --tb=short",
    "lint_command": "ruff check . --output-format=concise"
}
```

Auto-detection falls back to sensible defaults when no config file exists.

## Env vars

| Var | Required | Default |
|-----|----------|---------|
| `KILO_API_KEY` | no (falls back to dummy) | `sk-dummy-free-model-key` |
| `KILO_MODEL` | no | `kilo-auto/free` |
| `KILO_BASE_URL` | no | `https://api.kilo.ai/api/gateway/v1` |
| `OTTO_LOG_LEVEL` | no | `WARNING` |

## Architecture

- `agent_config.py` — builds `LocalOpenAIAgentConfig` from env vars
- `session.py` — `SessionManager` owns sessions; each session runs an `Agent` via async context manager with a consumer task draining an input queue
- `ui.py` — Textual `App` with output pane, input bar, command palette
- `main.py` — entry point, wires `SessionManager` → `AgentCliApp`
- `settings.py` — auto-detects test/lint commands from project files; loads `.otto/config.json` overrides
- `tools/` — custom Python callables (test runner, linter, git operations) registered as SDK tools
- `policy.py` — Build/Plan mode policy tables; `ask_user` handler bridging SDK to asyncio

The `google-antigravity` SDK spawns a Go binary (`localharness`) as a subprocess and speaks protobuf-over-websocket. Python never makes HTTP calls directly.

## Testing

6 test files cover the core modules:

| File | Coverage |
|------|----------|
| `tests/unit/test_tools.py` | All 9 custom tools (mocked subprocess) |
| `tests/unit/test_settings.py` | Test/lint auto-detection, `.otto/config.json` overrides |
| `tests/unit/test_session_index.py` | Index CRUD, persistence, naming, corrupt-file handling |
| `tests/unit/test_messages.py` | Message save/load round-trip, timestamp preservation |
| `tests/unit/test_policy.py` | Build/Plan policy tables, `make_ask_handler` |
| `tests/integration/test_cli_smoke.py` | CLI imports, app instantiation, slash commands |

```bash
pytest          # run all tests
ruff check src tests  # linting
```

`if __name__ == "__main__"` smoke tests also exist in `agent_config.py` and `session.py` (require `KILO_API_KEY`).

## Linting / formatting / typecheck

- **`ruff`**: Configured as dev dependency (`ruff>=0.6`), lints via `ruff check src tests`
- **`pytest`**: Configured via `[tool.pytest.ini_options]` in `pyproject.toml`
- **CI**: GitHub Actions workflow at `.github/workflows/ci.yml` runs tests and linting on Python 3.10–3.12
- **`mypy`**: Not configured
- **`tox`**, **`Makefile`**: Not configured

## Python version

Developed on Python 3.14. `requires-python = ">=3.10"` in `pyproject.toml`.
