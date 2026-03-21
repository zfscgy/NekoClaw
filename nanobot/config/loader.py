"""Configuration loading utilities."""

import json
from pathlib import Path

from nanobot.config.schema import Config


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
    return Path.home() / ".nanobot" / "config.json"


def _get_providers_path(config_path: Path) -> Path:
    """Return the providers.json path alongside the given config file."""
    return config_path.parent / "providers.json"


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    If a providers.json file exists alongside config.json, its contents are
    used as the providers section (taking precedence over any providers key
    already present in config.json).

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

            providers_path = _get_providers_path(path)
            if providers_path.exists():
                try:
                    with open(providers_path, encoding="utf-8") as f:
                        data["providers"] = json.load(f)
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"Warning: Failed to load providers from {providers_path}: {e}")

            return Config.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    If a providers.json file already exists alongside config.json, providers
    are saved there and omitted from config.json. Otherwise everything is
    saved to config.json as before.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True)

    providers_path = _get_providers_path(path)
    if providers_path.exists():
        providers_data = data.pop("providers", {})
        with open(providers_path, "w", encoding="utf-8") as f:
            json.dump(providers_data, f, indent=2, ensure_ascii=False)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data
