"""Configuration module for nekoclaw."""

from nekoclaw.config.loader import get_config_path
from nekoclaw.config.manager import get_global_config
from nekoclaw.config.paths import (
    get_bridge_install_dir,
    get_cron_dir,
    get_data_dir,
    get_logs_dir,
    get_media_dir,
    get_runtime_subdir,
    get_workspace_path,
)
from nekoclaw.config.schema import Config

__all__ = [
    "Config",
    "get_global_config",
    "get_config_path",
    "get_data_dir",
    "get_runtime_subdir",
    "get_media_dir",
    "get_cron_dir",
    "get_logs_dir",
    "get_workspace_path",
    "get_bridge_install_dir",
]
