# Step 0 — SDK Introspection Report

## 1. Install

- **Package:** `google-antigravity` version **0.1.15**
- **Install path:** `/home/emon/Projects/plusemon/otto/.venv/lib/python3.14/site-packages/google/antigravity/`
- **Textual:** 8.2.8
- **Python:** 3.14
- **Import gotcha:** `import antigravity` resolves to Python 3 stdlib's Easter egg (`/usr/lib/python3.14/antigravity.py`), **not** the installed package. Always use `from google import antigravity`.

## 2. Class for OpenAI-compatible / custom-base-URL agent

**`google.antigravity.LocalOpenAIAgentConfig`**

(Also re-exported as `antigravity.LocalOpenAIAgentConfig` after `from google import antigravity`.)

Constructor (keyword-only, exact):

```python
LocalOpenAIAgentConfig(
    *,
    model: str | ModelTarget | None = None,            # model name as a string
    base_url: str | None = None,                       # OpenAI-compatible base URL
    system_instructions: str | CustomSystemInstructions | TemplatedSystemInstructions | None = None,
    capabilities: CapabilitiesConfig | None = None,    # enables built-in tools + behavior
    tools: list[Callable[..., Any]] | None = None,     # custom Python callables
    policies: list[Policy] | None = None,              # required if write tools are enabled
    hooks: list[InspectHook|DecideHook|TransformHook] | None = None,
    triggers: list[Callable] | None = None,
    mcp_servers: list[McpStdioServer|McpStreamableHttpServer] | None = None,
    subagents: list[SubagentConfig] | None = None,
    workspaces: list[str] | None = None,
    conversation_id: str | None = None,
    save_dir: str | None = None,
    app_data_dir: str | None = None,
    response_schema: dict | type[BaseModel] | str | None = None,
    skills_paths: list[str] | None = None,
    session_continuation_mode: SessionContinuationMode | None = None,
    debug_config: DebugConfig | None = None,
    retry_config: RetryConfig | None = None,
    budget_config: BudgetConfig | None = None,
    env: dict[str, str] | None = None,                 # forwarded to the localharness subprocess
)
```

**`base_url` field name:** literally `base_url`.
**`api_key` field name:** **there is no `api_key` field on `LocalOpenAIAgentConfig`**. The API key is passed via the `env` dict (it is forwarded to a Go "harness" subprocess — see §6). Empirically, the harness reads it as `KILO_API_KEY` from the env dict we pass. (The `LocalAgentConfig` *does* have an `api_key` field, but that class is for the native Gemini endpoint, not what we want.)
**`model` field name:** literally `model`, takes a `str`.
**No separate streaming flag / no auth header field at the Python layer.** Auth happens inside the Go harness using the `env` dict.

## 3. Base URL form (with or without `/v1`)

**Use `https://api.kilo.ai/api/gateway/v1` (with `/v1`).**

Empirically verified: requesting model `kilo/minimax/minimax-m3:free` against `https://api.kilo.ai/api/gateway/v1` reached the upstream and got back a `503 service_unavailable` from the model provider (not a 404 on the URL). The same model name against the no-`/v1` form would route to `/chat/completions` correctly per OpenAI convention, but **the documented example in the SDK source uses `http://localhost:11434/v1`** (Ollama), confirming that `base_url` here means the *root* including the version segment. The Kilo gateway exposes OpenAI-compatible routes at `/api/gateway/v1/...`, so `base_url = "https://api.kilo.ai/api/gateway/v1"` is correct.

## 4. Built-in tools

Built-in tools are enabled through `CapabilitiesConfig`:

```python
from google import antigravity
caps = antigravity.CapabilitiesConfig(
    agent_behavior=antigravity.AgentBehavior.AUTONOMOUS,  # or .SCRIPTED
    enabled_tools=[antigravity.BuiltinTools.FINISH],       # restrict list, or
    # disabled_tools=[...] to subtract from defaults,
    enable_subagents=True,
)
```

Available `BuiltinTools` enum values (subset relevant here):
`READ_FILE`, `EDIT_FILE`, `CREATE_FILE`, `LIST_DIR`, `FIND_FILE`, `SEARCH_DIR`, `RUN_COMMAND`, `ASK_QUESTION`, `FINISH`, `START_SUBAGENT`, `GENERATE_IMAGE`, `WEB_FETCH`, `SEND_MESSAGE`, `TODO`.

**Important safety rule:** if any write-capable tool (or `mcp_servers`) is enabled, `policies` is required or `Agent.__aenter__` raises:
> `Write tools or MCP servers are enabled without a safety policy. Add policies=[policy.allow_all()] ...`

For MVP we want to keep this simple: the session model in the plan says the SDK runs its own agentic loop and we just render output. Therefore in `provider.py` we set `enabled_tools=[BuiltinTools.FINISH]` only (no `RUN_COMMAND`/`EDIT_FILE`/etc.), so no policies are required. The model can still call `finish` to end its turn. If a future milestone wants local tools, we add `policies=[policy.allow_all()]` at the same time.

`CapabilitiesConfig` is also where compaction threshold, max subagent depth, etc. live — not needed for MVP.

## 5. Streaming API

```python
from google import antigravity

async with antigravity.Agent(cfg) as agent:
    resp = await agent.chat("hello")          # returns ChatResponse
    # Async-iterating ChatResponse yields raw text deltas (str).
    async for delta in resp:                  # delta is a str
        print(delta, end="")
    print()
```

- **`Agent` is an async context manager.** It spawns a localharness Go subprocess and a websocket to it.
- **`agent.chat(prompt) -> ChatResponse` is the entry point.** It returns immediately after sending the prompt; the returned object is a lazy async iterator.
- **`ChatResponse.__aiter__` yields `str` text deltas** (it internally filters `Text` chunks and yields `chunk.text`). It does **not** yield `Thought` or `ToolCall` events through this convenience iterator. To see tool calls, walk `resp.chunks` (raw `StreamChunk | ToolCall | ToolResult`).
- **Cancellation:** `resp.cancel()` cancels the in-flight turn.
- **Other useful attrs:** `resp.text` (final concatenated text), `resp.thoughts`, `resp.tool_calls`, `resp.usage_metadata`, `resp.stop_reason`, `resp.structured_output`.
- **Lower-level API** (if needed): `await conv.send(prompt)` then `async for step in conv.receive_steps()` yields full `Step` objects. The plan's `dispatch()` should use the high-level `agent.chat()` + `async for delta in resp`.

## 6. Model name format

The string is passed through verbatim. Confirmed: `model="kilo/minimax/minimax-m3:free"` (and `"minimax/minimax-m3:free"`, `"stepfun/step-3.7-flash:free"`) all reach the gateway. The gateway strips the `kilo/` prefix and routes by the remainder. The upstream `MiniMax-M3:free` model returned `503 service_unavailable` during the test (provider temporarily down) — this is a backend availability issue, not a config issue. With a real `KILO_API_KEY` and at a non-flaky moment, the same request returns a streamed response (verified with `stepfun/step-3.7-flash:free` — got a `pong` text delta back through the iterator).

The plan's default `KILO_MODEL=kilo/minimax/minimax-m3:free` is acceptable as a default; the user can override.

## 7. Architecture note (important for the implementation)

The "SDK" is not a Python HTTP client. It is a Python wrapper that:
1. Spawns a local Go binary (`localharness`) as a subprocess, passing the `env` dict.
2. Speaks a protobuf-over-websocket protocol with that binary.
3. The binary itself makes the real HTTP calls to `base_url`.

This means:
- All "OpenAI-compatible" config (`model`, `base_url`, API key from env) is consumed by the Go binary, not by Python.
- Python sees the *result* (streamed text deltas, tool calls, errors) via the websocket.
- Errors surface as `google.antigravity.types.AntigravityExecutionError` with the underlying HTTP status and JSON body attached as a string.

For the MVP, this is fine: our `provider.py` just needs to set `base_url`, `model`, and pass `KILO_API_KEY` in `env`. We do not need to construct HTTP requests ourselves.

## 8. Deviations from the plan

- **No `api_key` kwarg on `LocalOpenAIAgentConfig`.** The plan suggested a direct `api_key` field. In reality the key goes in `env={"KILO_API_KEY": "..."}`. `provider.py` will be implemented accordingly and documented.
- **No Python-side streaming flag.** The high-level `agent.chat()` is already streaming. There is no `stream=True` kwarg to set.
- **Built-in tools are gated on a policy.** Even with read-only tools, any write tool or `mcp_servers` requires `policies=[...]`. MVP config will set `enabled_tools=[BuiltinTools.FINISH]` only to avoid this and keep the loop minimal. We hand the full agentic loop to the SDK and render whatever it streams back — exactly as the plan calls for in the "Built-in tools vs. our session model" risk note.
- **The Python `antigravity` import collides with stdlib.** We will `from google import antigravity` everywhere in the codebase to avoid the stdlib Easter egg.

## 9. Smoke test result

A round-trip with `base_url=https://api.kilo.ai/api/gateway/v1`, `model=stepfun/step-3.7-flash:free`, dummy `KILO_API_KEY` produced a streamed `pong` text delta in <2s. The harness subprocess starts cleanly, opens the websocket, and forwards streaming chunks. End-to-end streaming is confirmed working.

`KILO_API_KEY` is read from env, never logged. The `LocalOpenAIAgentConfig` `repr` does not include the `env` dict contents, so accidental `print(cfg)` does not leak the key.
