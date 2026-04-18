"""Runtime configuration management.

Exposes helpers for inspecting and mutating the active
:class:`~nekoclaw.config.schema.Config` — persisting changes to disk *and*
applying them to the live :class:`~nekoclaw.providers.base.LLMProvider`
so no restart is required.
"""

from __future__ import annotations

from loguru import logger

from nekoclaw.config.loader import load_config, save_config
from nekoclaw.config.schema import ProviderConfig
from nekoclaw.manager.runtime import get_config, get_provider


def get_openai_provider_config() -> dict:
    """Return the current OpenAI provider config as a plain dict.

    Falls back to reading from disk if no runtime config is registered.
    """
    cfg = get_config() or load_config()
    p = cfg.providers.openai
    return {
        "api_key": p.api_key,
        "api_base": p.api_base or "",
        "extra_headers": p.extra_headers or {},
    }


def set_openai_provider_config(
    api_key: str | None = None,
    api_base: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict:
    """Update the OpenAI provider config in-memory, on disk, and on the live provider.

    Only non-``None`` arguments are applied; pass ``""`` for ``api_base`` to
    clear it, or an empty dict for ``extra_headers`` to drop custom headers.

    Returns the resulting provider config as a dict.
    """
    cfg = get_config()
    if cfg is None:
        cfg = load_config()

    current = cfg.providers.openai
    new_key = current.api_key if api_key is None else api_key
    if api_base is None:
        new_base = current.api_base
    else:
        new_base = api_base or None
    if extra_headers is None:
        new_headers = current.extra_headers
    else:
        new_headers = extra_headers or None

    cfg.providers.openai = ProviderConfig(
        api_key=new_key,
        api_base=new_base,
        extra_headers=new_headers,
    )

    save_config(cfg)
    logger.info("OpenAI provider config updated (api_base={})", new_base)

    provider = get_provider()
    if provider is not None:
        reconfigure = getattr(provider, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(
                    api_key=new_key,
                    api_base=new_base,
                    extra_headers=new_headers or {},
                )
                logger.info("Live provider reconfigured")
            except Exception as exc:
                logger.warning("Failed to reconfigure live provider: {}", exc)

    return {
        "api_key": new_key,
        "api_base": new_base or "",
        "extra_headers": new_headers or {},
    }
