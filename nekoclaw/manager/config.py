"""Runtime configuration management.

Exposes generic :func:`get` / :func:`set` helpers that read and mutate the
active :class:`~nekoclaw.config.schema.Config` via dot-path keys
(e.g. ``providers.openai.api_key``), persist changes to disk *and* apply them
to the live runtime where possible so no restart is required.

The frontend config panel is driven entirely by the Pydantic schema: calling
:func:`get` returns the full config dict plus the resolved JSON schema, so
any new field added to :mod:`nekoclaw.config.schema` appears automatically.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from nekoclaw.config.loader import load_config, save_config
from nekoclaw.config.schema import Config
from nekoclaw.manager.runtime import get_agent, get_heartbeat
from nekoclaw.manager.runtime import get_config as _get_runtime_config
from nekoclaw.manager.runtime import get_provider, set_runtime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_global_config() -> Config:
    """Return the live runtime config, falling back to disk."""
    return _get_runtime_config() or load_config()


def _camel_to_snake(name: str) -> str:
    """Convert ``camelCase`` segments to ``snake_case`` for path traversal."""
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _normalize_parts(key: str) -> list[str]:
    """Split a dotted key path and normalize each segment to snake_case."""
    if not key:
        raise ValueError("config key must not be empty")
    return [_camel_to_snake(p) for p in key.split(".") if p]


SENSITIVE_TOKENS = ("key", "secret", "token", "password")


def _redact(key: str, value: Any) -> Any:
    """Mask sensitive values for log output."""
    lowered = key.lower()
    for tok in SENSITIVE_TOKENS:
        if tok in lowered and isinstance(value, str) and value:
            return "***"
    return value


def _resolve_schema() -> dict[str, Any]:
    """Return the Pydantic JSON schema with ``$ref`` entries inlined.

    Inlining simplifies the frontend: it can render any branch of the tree
    without having to track ``$defs`` separately.
    """
    schema = Config.model_json_schema(mode="serialization", by_alias=False)
    defs = schema.pop("$defs", {}) or schema.pop("definitions", {}) or {}

    def _inline(node: Any, seen: frozenset[str]) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/"):
                name = ref.rsplit("/", 1)[-1]
                if name in seen:
                    # Break recursion: leave as opaque object.
                    return {"type": "object"}
                target = defs.get(name)
                if target is None:
                    return {}
                return _inline(target, seen | {name})
            return {k: _inline(v, seen) for k, v in node.items() if k != "$ref"}
        if isinstance(node, list):
            return [_inline(v, seen) for v in node]
        return node

    return _inline(schema, frozenset())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def to_dict(key: str | None = None) -> Any:
    """Return the config as a dict, or the value at ``key`` (dot-path).

    Path segments accept both ``snake_case`` and ``camelCase``.  Pass ``None``
    (or an empty string) to retrieve the full configuration.
    """
    cfg = get_global_config()
    data: Any = cfg.model_dump(by_alias=False)
    if not key:
        return data
    for part in _normalize_parts(key):
        if isinstance(data, dict) and part in data:
            data = data[part]
        else:
            raise KeyError(f"Unknown config key: {key!r}")
    return data


def set_key(key: str, value: Any) -> Any:  # noqa: A001 - module-level API name
    """Set the config value at ``key`` (dot-path) and apply it live.

    The mutation is validated against :class:`Config`, persisted to disk via
    :func:`save_config`, registered as the new runtime config, and pushed to
    live runtime components where supported.

    Returns the value that was written.
    """
    parts = _normalize_parts(key)
    cfg = get_global_config()
    data = cfg.model_dump(by_alias=False)

    node = data
    for p in parts[:-1]:
        if not isinstance(node, dict) or p not in node:
            raise KeyError(f"Unknown config key: {key!r}")
        node = node[p]

    if not isinstance(node, dict):
        raise KeyError(f"Cannot assign to non-object parent for {key!r}")

    node[parts[-1]] = value

    try:
        new_cfg = Config.model_validate(data)
    except Exception as exc:
        raise ValueError(f"Invalid value for {key!r}: {exc}") from exc

    save_config(new_cfg)
    logger.info("Config updated: {} = {!r}", key, _redact(key, value))

    set_runtime(new_cfg, get_provider())

    _apply_runtime_live(new_cfg)

    return value


def schema() -> dict[str, Any]:
    """Return the resolved JSON schema for :class:`Config`.

    Useful to render dynamic UIs that should reflect schema changes without
    manual updates.
    """
    return _resolve_schema()


# ---------------------------------------------------------------------------
# Live runtime propagation
# ---------------------------------------------------------------------------


def _apply_runtime_live(new_cfg: Config) -> None:
    """Push config changes to live runtime components, if registered."""
    _apply_provider_live(new_cfg)
    _apply_agent_live(new_cfg)
    _apply_heartbeat_live(new_cfg)


def _apply_provider_live(new_cfg: Config) -> None:
    """Push OpenAI provider settings to the running provider, if any."""
    provider = get_provider()
    if provider is None:
        return
    reconfigure = getattr(provider, "reconfigure", None)
    if not callable(reconfigure):
        return
    p = new_cfg.providers.openai
    try:
        reconfigure(
            api_key=p.api_key,
            api_base=p.api_base,
            extra_headers=p.extra_headers or {},
            default_model=new_cfg.agents.defaults.model,
        )
        logger.info("Live provider reconfigured")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to reconfigure live provider: {}", exc)


def _apply_agent_live(new_cfg: Config) -> None:
    """Apply agent defaults to the running agent loop and subagent manager."""
    agent = get_agent()
    if agent is None:
        return

    d = new_cfg.agents.defaults
    try:
        agent.model = d.model
        agent.temperature = d.temperature
        agent.max_tokens = d.max_tokens
        agent.max_iterations = d.max_tool_iterations
        agent.memory_window = d.memory_window
        agent.reasoning_effort = d.reasoning_effort

        subagents = getattr(agent, "subagents", None)
        if subagents is not None:
            subagents.model = d.model
            subagents.temperature = d.temperature
            subagents.max_tokens = d.max_tokens
            subagents.reasoning_effort = d.reasoning_effort

        logger.info("Live agent defaults reconfigured")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to reconfigure live agent defaults: {}", exc)


def _apply_heartbeat_live(new_cfg: Config) -> None:
    """Apply heartbeat settings to the running heartbeat service."""
    heartbeat = get_heartbeat()
    if heartbeat is None:
        return

    hb_cfg = new_cfg.gateway.heartbeat
    try:
        heartbeat.model = new_cfg.agents.defaults.model
        heartbeat.interval_s = hb_cfg.interval_s
        heartbeat.enabled = hb_cfg.enabled
        logger.info("Live heartbeat settings reconfigured")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to reconfigure live heartbeat settings: {}", exc)


# ---------------------------------------------------------------------------
# Backward-compatible OpenAI shortcuts (thin wrappers over get/set)
# ---------------------------------------------------------------------------


def get_openai_provider_config() -> dict:
    """Return the current OpenAI provider config as a plain dict."""
    data = to_dict("providers.openai")
    return {
        "api_key": data.get("api_key") or "",
        "api_base": data.get("api_base") or "",
        "extra_headers": data.get("extra_headers") or {},
    }


def set_openai_provider_config(
    api_key: str | None = None,
    api_base: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict:
    """Update the OpenAI provider config.  Only non-``None`` args are applied."""
    if api_key is not None:
        set_key("providers.openai.api_key", api_key)
    if api_base is not None:
        set_key("providers.openai.api_base", api_base or None)
    if extra_headers is not None:
        set_key("providers.openai.extra_headers", extra_headers or None)
    return get_openai_provider_config()
