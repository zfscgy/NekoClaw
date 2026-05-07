"""Runtime config bootstrap.

Resolves the active config file path, creates default sidecar files
(``providers.json`` / ``channels.json`` / ``tools.json``) on first launch, and
prompts the user for any required gateway settings (model, API base, API key)
that have not been provided yet.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console

from nekoclaw.config.schema import Config

console = Console()


def missing_gateway_config_keys(config: Config) -> list[str]:
    """Return the names of required LLM settings that have not been configured.

    Order is kept consistent with :func:`prompt_configs` so users see the same
    sequence both in the warning and in the interactive prompt.
    """
    missing: list[str] = []
    p = config.providers.openai

    if not (p.api_base or "").strip():
        missing.append("openai_base_url")
    if not p.api_key.strip():
        missing.append("openai_api_key")
    if not config.agents.defaults.model.strip():
        missing.append("model")

    return missing


def load_runtime_config(
    config_path_str: str | None = None,
    workspace: str | None = None,
) -> Config:
    """Load config and optionally override the active workspace.

    If ``config_path_str`` is provided, that file must exist; otherwise the
    default config files are created (when missing) under
    ``~/.nekoclaw/``. Any required keys that are still empty trigger an
    interactive prompt and the gateway aborts if the user fails to fill them
    in.
    """
    from nekoclaw.config.loader import (
        create_default_configs,
        prompt_configs,
        set_config_path,
    )
    from nekoclaw.manager import config

    config_path: Path | None = None
    if config_path_str:
        config_path = Path(config_path_str).expanduser().resolve()
        if not config_path.exists():
            console.print(f"[red]找不到配置文件喵: {config_path}[/red]")
            sys.exit(1)
        set_config_path(config_path)
    else:
        created = create_default_configs()
        if created:
            console.print(
                f"  [dim]喵咪已经在 {created[0].parent} 帮主人摆好默认配置啦～[/dim]"
            )

    loaded = config.get_global_config()
    missing = missing_gateway_config_keys(loaded)
    if missing:
        console.print(
            "[yellow]还有几项必填配置没填好喵: "
            f"{', '.join(missing)}，主人现在就来补上吧～[/yellow]"
        )
        loaded = prompt_configs(config_path)
        missing = missing_gateway_config_keys(loaded)
        if missing:
            console.print(
                "[red]喵咪开不了网关呜呜～必须先填好这些项目喵: "
                f"{', '.join(missing)}[/red]"
            )
            sys.exit(1)

    if workspace:
        loaded.agents.defaults.workspace = workspace
    return loaded
