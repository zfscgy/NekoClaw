"""Helpers for opening the NekoChat web UI in the user's default browser.

Kept under :mod:`nekoclaw.startup` so ``__main__`` only has to wire the call
into the post-startup banner, while the URL-resolution and platform-specific
``webbrowser`` quirks live in one place.
"""

from __future__ import annotations

import webbrowser

from rich.console import Console

from nekoclaw.config.schema import Config

console = Console()


def nekochat_url(cfg: Config) -> str | None:
    """Return the user-facing NekoChat URL, or ``None`` when disabled.

    ``0.0.0.0`` / ``::`` bind addresses are rewritten to ``127.0.0.1`` because
    they aren't routable from a browser.
    """
    nc = cfg.channels.nekochat
    if not nc.enabled:
        return None
    host = nc.host
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{nc.port}"


def open_nekochat_browser(cfg: Config) -> None:
    """Best-effort open the NekoChat front-end in the default browser."""
    url = nekochat_url(cfg)
    if not url:
        return
    try:
        if webbrowser.open(url, new=2):
            console.print(
                f"  [dim]喵咪已经帮主人在浏览器里打开 NekoChat 啦～ {url}[/dim]"
            )
        else:
            console.print(
                "  [dim]浏览器没能自动打开喵，主人请手动访问 "
                f"[cyan]{url}[/cyan] ～[/dim]"
            )
    except Exception as exc:  # pragma: no cover - depends on host browser
        console.print(
            f"[yellow]浏览器没能自动打开喵 ({exc})，主人请手动访问 {url}～[/yellow]"
        )
