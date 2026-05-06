"""Sync bundled optional skills into the user's workspace.

The package ships skills under ``nekoclaw/skills/`` split into two groups:

- ``internal/`` — always available, loaded directly from the package as
  built-in skills.
- ``optional/`` — recommended add-ons. On first launch they are copied into
  ``<workspace>/skills/<name>/`` so they appear as workspace-managed skills:
  the user can edit them, disable (zip) them, or delete them like any other
  user-installed skill.

Skills that already exist in the workspace (either as a directory or a
zipped, disabled archive) are left untouched, so user customizations and
disabled states are preserved across upgrades.
"""

from __future__ import annotations

from importlib.resources import files as pkg_files
from pathlib import Path

from rich.console import Console

console = Console()


def sync_optional_skills(workspace: Path, *, silent: bool = False) -> list[str]:
    """Copy bundled ``skills/optional/<name>`` directories into the workspace.

    Args:
        workspace: The active workspace path. The target directory is
            ``<workspace>/skills/`` and is created if it does not exist.
        silent: When ``True``, suppress the per-skill log output.

    Returns:
        Names of skills that were newly installed during this call.
    """
    try:
        bundled = pkg_files("nekoclaw") / "skills" / "optional"
    except Exception:
        return []
    if not bundled.is_dir():
        return []

    target_root = workspace / "skills"
    target_root.mkdir(parents=True, exist_ok=True)

    added: list[str] = []
    for entry in bundled.iterdir():
        if not entry.is_dir():
            continue
        if not (entry / "SKILL.md").is_file():
            continue

        name = entry.name
        dest = target_root / name
        zipped = target_root / f"{name}.zip"
        if dest.exists() or zipped.exists():
            continue

        try:
            _copy_traversable(entry, dest)
        except Exception as exc:
            console.print(
                f"[yellow]Warning: failed to install optional skill {name}: {exc}[/yellow]"
            )
            continue

        added.append(name)

    if added and not silent:
        for name in added:
            console.print(f"  [dim]Installed optional skill {name}[/dim]")

    return added


def _copy_traversable(src, dest: Path) -> None:
    """Recursively copy an :mod:`importlib.resources` Traversable to ``dest``.

    Works for both real filesystem paths (development / PyInstaller bundles)
    and resources nested inside zip-style loaders.
    """
    if src.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with src.open("rb") as fh:
            dest.write_bytes(fh.read())
        return

    if src.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            _copy_traversable(child, dest / child.name)
