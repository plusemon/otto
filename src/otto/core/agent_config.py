"""Provider config builder for the Kilo OpenAI-compatible gateway via google-antigravity.

Reads KILO_API_KEY (required) and optional KILO_MODEL / KILO_BASE_URL from the
environment and returns a configured google.antigravity.LocalOpenAIAgentConfig.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from google import antigravity

from .tools import ALL_TOOLS

logger = logging.getLogger("otto.provider")


DEFAULT_BASE_URL = "https://api.kilo.ai/api/gateway/v1"
DEFAULT_MODEL = "kilo-auto/free"
DEFAULT_API_KEY = "sk-dummy-free-model-key"

_MASK = "***"


class ProviderConfigError(RuntimeError):
    """Raised when the provider cannot be configured (missing/invalid env)."""


@dataclass(frozen=True)
class ProviderSettings:
    base_url: str
    model: str
    api_key: str

    def masked(self) -> ProviderSettings:
        return ProviderSettings(self.base_url, self.model, _MASK)


def _require_key() -> str:
    key = os.environ.get("KILO_API_KEY", "").strip()
    if not key:
        logger.debug("KILO_API_KEY not set, using default key")
        return DEFAULT_API_KEY
    logger.debug("KILO_API_KEY found")
    return key


def load_settings() -> ProviderSettings:
    """Read env and return a ProviderSettings (does not construct SDK objects)."""
    base_url = os.environ.get("KILO_BASE_URL", "").strip() or DEFAULT_BASE_URL
    model = os.environ.get("KILO_MODEL", "").strip() or DEFAULT_MODEL
    api_key = _require_key()
    logger.debug("Settings loaded: base_url=%s, model=%s", base_url, model)
    return ProviderSettings(base_url=base_url, model=model, api_key=api_key)


def build_provider_config(
    model_override: str | None = None,
) -> antigravity.LocalOpenAIAgentConfig:
    """Build a ready-to-use LocalOpenAIAgentConfig for the Kilo gateway.

    Exposes read, write, and shell tools to the model. Policy gating
    (allow/deny/ask_user) is applied per-session in session.py — the
    provider only controls which tools the model can *see*.

    If *model_override* is given it replaces the model from env/defaults.
    """
    try:
        settings = load_settings()
        model = model_override or settings.model
        caps = antigravity.CapabilitiesConfig(
            agent_behavior=antigravity.AgentBehavior.AUTONOMOUS,
            enabled_tools=[
                # Read-only tools
                antigravity.BuiltinTools.VIEW_FILE,
                antigravity.BuiltinTools.LIST_DIR,
                antigravity.BuiltinTools.FIND_FILE,
                antigravity.BuiltinTools.SEARCH_DIR,
                # Write tools
                antigravity.BuiltinTools.CREATE_FILE,
                antigravity.BuiltinTools.EDIT_FILE,
                # Shell execution
                antigravity.BuiltinTools.RUN_COMMAND,
                # Finish
                antigravity.BuiltinTools.FINISH,
            ],
        )
        config = antigravity.LocalOpenAIAgentConfig(
            model=model,
            base_url=settings.base_url,
            capabilities=caps,
            tools=ALL_TOOLS,
            env={"KILO_API_KEY": settings.api_key},
        )
        logger.debug("Provider config built successfully")
        return config
    except Exception as e:
        logger.error("Failed to build provider config: %s", e)
        raise


def describe() -> dict[str, Any]:
    """Return a non-secret summary of the active settings, for logging/UI."""
    s = load_settings().masked()
    return {"base_url": s.base_url, "model": s.model, "api_key": s.api_key}


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    is_free: bool


def fetch_models() -> list[ModelInfo]:
    """Fetch available models from the Kilo gateway /models endpoint."""
    settings = load_settings()
    try:
        r = httpx.get(
            f"{settings.base_url}/models",
            headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=10,
        )
        r.raise_for_status()
        models = [
            ModelInfo(id=m["id"], name=m.get("name", m["id"]), is_free=m.get("isFree", False))
            for m in r.json().get("data", [])
        ]
        return sorted(models, key=lambda m: (not m.is_free, m.name.lower()))
    except (httpx.RequestError, ValueError) as e:
        logger.warning("Failed to fetch models: %s", e)
        return []


if __name__ == "__main__":
    # Smoke test: build the config without making a network call.
    if "KILO_API_KEY" not in os.environ:
        os.environ["KILO_API_KEY"] = "sk-dummy-smoke-test-key"
    cfg = build_provider_config()
    print("config built OK")
    print("model:    ", cfg.model)
    print("base_url: ", cfg.base_url)
    print("capabilities.enabled_tools:", cfg.capabilities.enabled_tools)
