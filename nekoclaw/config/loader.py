"""Configuration loading utilities."""

import json
from pathlib import Path

from nekoclaw.config.schema import Config, ProviderConfig


# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """Get the configuration file path."""
    if _current_config_path:
        return _current_config_path
    return Path.home() / ".nekoclaw" / "config.json"


def _get_providers_path(config_path: Path) -> Path:
    """Return the providers.json path alongside the given config file."""
    return config_path.parent / "providers.json"


def _get_channels_path(config_path: Path) -> Path:
    """Return the channels.json path alongside the given config file."""
    return config_path.parent / "channels.json"


def _get_tools_path(config_path: Path) -> Path:
    """Return the tools.json path alongside the given config file."""
    return config_path.parent / "tools.json"


def _all_config_paths(config_path: Path) -> list[Path]:
    """Return the four files that make up a config bundle, in canonical order."""
    return [
        config_path,
        _get_providers_path(config_path),
        _get_channels_path(config_path),
        _get_tools_path(config_path),
    ]


def create_default_configs(config_path: Path | None = None) -> list[Path]:
    """
    Make sure the full config bundle exists on disk.

    The bundle is composed of ``config.json`` plus the three sidecar files
    (``providers.json`` / ``channels.json`` / ``tools.json``). Any file that is
    missing is materialized from the currently loaded values (which fall back
    to schema defaults when the field is absent), so users never end up with a
    partial install.

    Returns the list of paths that were newly created.
    """
    path = config_path or get_config_path()
    paths = _all_config_paths(path)

    missing = [p for p in paths if not p.exists()]
    if not missing:
        return []

    cfg = load_config(path)
    save_config(cfg, path)
    return missing


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    If a providers.json, channels.json, or tools.json file exists alongside
    config.json, its contents are used as the respective section (taking
    precedence over any matching key already present in config.json).

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data = _migrate_config(data)

            for key, sidecar_path in (
                ("providers", _get_providers_path(path)),
                ("channels", _get_channels_path(path)),
                ("tools", _get_tools_path(path)),
            ):
                if sidecar_path.exists():
                    try:
                        with open(sidecar_path, encoding="utf-8") as f:
                            data[key] = json.load(f)
                    except (json.JSONDecodeError, ValueError) as e:
                        print(f"Warning: Failed to load {key} from {sidecar_path}: {e}")

            return Config.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to disk, always writing the full bundle.

    The four files (``config.json`` plus the ``providers``/``channels``/
    ``tools`` sidecars) are written unconditionally; any sidecar that does
    not yet exist is created. The respective section is dropped from
    ``config.json`` so each value lives in exactly one file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True)

    for key, sidecar_path in (
        ("providers", _get_providers_path(path)),
        ("channels", _get_channels_path(path)),
        ("tools", _get_tools_path(path)),
    ):
        section_data = data.pop(key, {})
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(section_data, f, indent=2, ensure_ascii=False)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def prompt_configs(config_path: Path | None = None) -> Config:
    """
    Interactively prompt the user for the essential configuration keys and
    persist the result to disk.

    Keys collected (in this order):
    - OpenAI API base URL (``providers.openai.api_base``)
    - OpenAI API key      (``providers.openai.api_key``)
    - Default model       (``agents.defaults.model``)
    - Template locale     (``agents.defaults.template_locale`` — ``en`` or ``cn``)

    Current values (if any) are shown as defaults; press Enter to keep them.

    Args:
        config_path: Optional path to the config file. Uses the default path
            (or the path previously set via ``set_config_path``) if omitted.

    Returns:
        The updated, saved configuration.
    """
    from rich.console import Console
    from rich.prompt import Prompt

    console = Console()
    path = config_path or get_config_path()

    create_default_configs(path)
    cfg = load_config(path)

    console.rule("[bold magenta]· NekoClaw 配置时间喵～主人填一下吧 ·[/bold magenta]")
    console.print("[dim]按 Enter 即可保留当前的值喵～[/dim]\n")

    console.print("[bold]OpenAI 服务设置喵[/bold]")
    openai = cfg.providers.openai

    previous_base = openai.api_base
    new_base = Prompt.ask(
        "  openai_base_url",
        default=openai.api_base or "",
        show_default=bool(openai.api_base),
    ).strip()
    openai.api_base = new_base or None

    # If the base URL just changed (or the list was never populated) re-derive
    # the curated suggestions for the new host, then promote the first
    # recommended model id as the default — the schema-level fallback
    # ("gpt-5.4") usually isn't reachable through provider-specific gateways.
    if openai.api_base != previous_base or not openai.recommended_models:
        inferred = ProviderConfig.infer_models(openai.api_base)
        if inferred:
            openai.recommended_models = inferred
            cfg.agents.defaults.model = inferred[0]

    current_key = openai.api_key
    new_key = Prompt.ask(
        "  openai_api_key",
        default=current_key,
        show_default=bool(current_key),
    )
    if new_key and new_key != current_key:
        openai.api_key = new_key.strip()

    new_model = Prompt.ask(
        "  model",
        default=cfg.agents.defaults.model,
    ).strip()
    if new_model:
        cfg.agents.defaults.model = new_model

    console.print("\n[bold]界面语言喵[/bold]")
    new_locale = Prompt.ask(
        "  locale",
        choices=["en", "cn"],
        default=cfg.agents.defaults.template_locale,
    )
    cfg.agents.defaults.template_locale = new_locale  # type: ignore[assignment]

    save_config(cfg, path)
    console.print(f"\n[green]✓[/green] 配置已保存到 [cyan]{path}[/cyan] 喵～")
    console.rule("[dim]· 配置收好啦，喵咪继续启动 ·[/dim]")

    return cfg


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data
