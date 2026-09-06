# otto

Terminal-native coding agent MVP. A Textual TUI that holds multiple isolated
chat sessions, each backed by its own `google-antigravity` Agent talking to the
[Kilo](https://kilo.ai) OpenAI-compatible gateway. Streaming text deltas appear
in the output pane; slash commands manage sessions.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configure

```bash
export KILO_API_KEY=sk-...                # required
export KILO_MODEL=kilo/minimax/minimax-m3:free   # optional, this is the default
export KILO_BASE_URL=https://api.kilo.ai/api/gateway/v1   # optional
```

A copyable template lives in `.env.example`.

## Run

```bash
otto
```

## Slash commands

| command                        | effect                                                  |
|--------------------------------|---------------------------------------------------------|
| `/session new`                 | create a new session and make it active                 |
| `/session list`                | show all sessions (id, created, #msgs, last active)     |
| `/session switch <id-prefix>`  | make the session whose id starts with `<id-prefix>` active |
| `/quit` (or `:q`, or `Ctrl-D`) | exit                                                    |

Anything else is sent as a user message to the active session.

## Multi-session model

Each session owns its own conversation history with the model and is fully
isolated from the others. Switching sessions is just a UI redirect — the
underlying Agents and websocket connections stay alive. Messages typed while a
session is mid-turn are queued and processed in FIFO order.

## Out of scope (this milestone)

- Plan/Build mode toggle, permission/policy system
- Disk persistence of sessions
- Custom tools (test runner, linter, git helpers)
- Config file loading beyond env vars
- Diff previews, rich tool-call rendering, multi-pane layouts
- MCP, LSP, plugins

## See also

`otto/STEP0_REPORT.md` — the SDK introspection report that justified the
config shape and deviated from the original plan (no `api_key` kwarg on
`LocalOpenAIAgentConfig`; the key is passed via `env={"KILO_API_KEY": ...}`).
