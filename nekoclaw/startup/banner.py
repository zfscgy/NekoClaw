"""Curated Rich startup banner pieces."""

from __future__ import annotations

from rich.console import Console


NEKO_STARTUP_ART = r"""
.__   __.  _______  __  ___   ______     ______  __          ___   ____    __    ____ 
|  \ |  | |   ____||  |/  /  /  __  \   /      ||  |        /   \  \   \  /  \  /   / 
|   \|  | |  |__   |  '  /  |  |  |  | |  ,----'|  |       /  ^  \  \   \/    \/   /  
|  . `  | |   __|  |    <   |  |  |  | |  |     |  |      /  /_\  \  \            /   
|  |\   | |  |____ |  .  \  |  `--'  | |  `----.|  `----./  _____  \  \    /\    /    
|__| \__| |_______||__|\__\  \______/   \______||_______/__/     \__\  \__/  \__/                                         
"""


def print_neko_startup_art(console: Console) -> None:
    """Print the Neko cat++girl ASCII art used during gateway startup."""
    console.print(NEKO_STARTUP_ART, style="bold magenta")
