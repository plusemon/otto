# otto

Terminal-native coding agent MVP. A Textual TUI that manages multiple isolated chat sessions, each backed by its own AI agent communicating through the [Kilo](https://kilo.ai) OpenAI-compatible gateway. Switch between Plan (read-only) and Build (read-write) modes to control tool access.

[![tests](https://github.com/plusemon/otto/workflows/tests/badge.svg)](https://github.com/plusemon/otto/actions)

## Features

- **Multi-session**: Open multiple chat sessions simultaneously, each with isolated state
- **Plan/Build modes**: Toggle between read-only exploration and read-write execution
- **Streaming UI**: Real-time token streaming with syntax-highlighted output
- **Permission controls**: Request approval for tool calls (git, file edits, shell commands)
- **Auto-detected tools**: Test and lint commands auto-detected from project files
- **Session persistence**: Sessions survive app restarts via disk storage

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configuration

Otto requires a Kilo API key to connect to the gateway:

```bash
export KILO_API_KEY=sk-...
```

Optional configuration:

```bash
export KILO_MODEL=kilo/minimax/minimax-m3:free  # default model
export KILO_BASE_URL=https://api.kilo.ai/api/gateway/v1
```

A template is available in `.env.example`.

## Usage

```bash
otto
```

### Slash Commands

| Command | Description |
|---------|-------------|
| `/session new` | Create a new chat session |
| `/session list` | List all active and resumable sessions |
| `/session switch <id>` | Switch to a session by ID prefix |
| `/session resume <id>` | Resume a previous session from disk |
| `/plan` | Switch active session to read-only mode |
| `/build` | Switch active session to read-write mode |
| `/model` | Select a model for new sessions |
| `/quit` | Exit otto |

### Key Bindings

| Key | Action |
|-----|--------|
| `Ctrl+B` | Toggle sidebar visibility |
| `Ctrl+C` | Quit |

## Architecture

Each session:
- Owns a unique ID (8 hex chars) and creation timestamp
- Runs its own `google.antigravity.Agent` instance
- Maintains isolated conversation history
- Supports Plan/Build mode switching via agent replacement
- Persists state via `.otto/sessions/<id>/` directory

## Development

Run tests:

```bash
pytest
```

Run linting:

```bash
ruff check .
```

## Status

This is an MVP (Minimum Viable Product). Out of scope:
- Disk persistence of sessions (partial via index only)
- Custom tools beyond auto-detected test/lint
- Diff previews, multi-pane layouts
- MCP, LSP, plugins

## See Also

- `.otto/STEP0_REPORT.md` — SDK introspection report detailing config design decisions
- [Kilo documentation](https://kilo.ai/docs) — Gateway and agent API reference