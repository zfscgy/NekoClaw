"""Packaged NekoClaw application entry point.

PyInstaller and AutoCython both work better when lazy runtime dependencies are
visible from the executable entry script.  Keep these imports explicit even when
the code below only delegates to ``nekoclaw.__main__``.
"""

from __future__ import annotations

import importlib
import os
import sys
import traceback
from pathlib import Path

DEPENDENCY_ANCHORS = (
    "aiohttp",
    "botpy",
    "chardet",
    "croniter",
    "dingtalk_stream",
    "httpx",
    "json_repair",
    "loguru",
    "lxml",
    "markdownify",
    "mcp",
    "msgpack",
    "oauth_cli_kit",
    "openai",
    "playwright",
    "prompt_toolkit",
    "pydantic",
    "pydantic_settings",
    "python_socks",
    "readability",
    "rich",
    "socketio",
    "socksio",
    "telegram",
    "typer",
    "websocket",
    "websockets",
)


def _prepare_bundle_cwd() -> None:
    if not getattr(sys, "frozen", False):
        return

    bundle_dir = Path(sys.executable).resolve().parent
    os.environ.setdefault("NEKOCLAW_BUNDLE_DIR", str(bundle_dir))

    if not (Path.cwd() / "resources").exists() and (bundle_dir / "resources").exists():
        os.chdir(bundle_dir)


def _import_dependency_anchors() -> None:
    """Import lazy dependencies so missing bundled modules fail with a useful log."""
    missing: list[str] = []
    for module in DEPENDENCY_ANCHORS:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name == module:
                missing.append(module)
                continue
            raise
    if missing:
        raise RuntimeError(f"Missing bundled Python modules: {', '.join(missing)}")


def _crash_log_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "NekoClaw-crash.log"
    return Path.cwd() / "NekoClaw-crash.log"


def _show_windows_error(message: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "NekoClaw startup error", 0x10)
    except Exception:
        return


def _run_guarded() -> int:
    try:
        main()
        return 0
    except BaseException:
        tb = traceback.format_exc()
        log_path = _crash_log_path()
        try:
            log_path.write_text(tb, encoding="utf-8")
        except Exception:
            pass

        print(tb, file=sys.stderr)
        _show_windows_error(f"NekoClaw failed to start.\n\nCrash log:\n{log_path}")

        if sys.platform == "win32" and os.environ.get("NEKOCLAW_NO_PAUSE") != "1":
            try:
                input("NekoClaw failed to start. Press Enter to close...")
            except Exception:
                pass
        return 1


def main() -> None:
    _prepare_bundle_cwd()
    _import_dependency_anchors()

    from nekoclaw.__main__ import main as nekoclaw_main

    nekoclaw_main()


if __name__ == "__main__":
    raise SystemExit(_run_guarded())
