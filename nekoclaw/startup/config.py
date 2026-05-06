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
    """Return the names of required LLM settings that have not been configured."""
    missing: list[str] = []
    p = config.providers.openai

    if not config.agents.defaults.model.strip():
        missing.append("model")
    if not (p.api_base or "").strip():
        missing.append("openai_base_url")
    if not p.api_key.strip():
        missing.append("openai_api_key")

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
            console.print(f"[red]Error: Config file not found: {config_path}[/red]")
            sys.exit(1)
        set_config_path(config_path)
    else:
        created = create_default_configs()
        if created:
            console.print(f"[dim]Created default config files in {created[0].parent}[/dim]")

    loaded = config.get_global_config()
    missing = missing_gateway_config_keys(loaded)
    if missing:
        console.print(
            "[yellow]Missing required gateway config: "
            f"{', '.join(missing)}. Please set them now.[/yellow]"
        )
        loaded = prompt_configs(config_path)
        missing = missing_gateway_config_keys(loaded)
        if missing:
            console.print(
                "[red]Error: gateway requires "
                f"{', '.join(missing)} before it can start.[/red]"
            )
            sys.exit(1)

    if workspace:
        loaded.agents.defaults.workspace = workspace
    return loaded
