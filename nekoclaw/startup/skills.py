"""Sync bundled optional skills into the user's workspace.

Skills come from two places:

- Built-in skills live under ``nekoclaw/skills/internal/`` and are loaded
  directly from the package — they are always available.
- Optional (recommended add-on) skills are downloaded at build time into
  ``resources/skills/skills/`` (see ``resources/skills/build.py``). On first
  launch they are copied into ``<workspace>/skills/<name>/`` so they appear as
  workspace-managed skills: the user can edit them, disable (zip) them, or
  delete them like any other user-installed skill.

Skills that already exist in the workspace (either as a directory or a
zipped, disabled archive) are left untouched, so user customizations and
disabled states are preserved across upgrades.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console

console = Console()


def _optional_skills_dir() -> Path:
    """Return the bundled optional-skill source directory.

    Mirrors the resource resolution used elsewhere in ``nekoclaw.startup``:
    this module lives at ``nekoclaw/startup/skills.py``, so ``parents[2]`` is
    the project / bundle root, where ``resources/`` is shipped.
    """
    return Path(__file__).resolve().parents[2] / "resources" / "skills" / "skills"


def sync_optional_skills(workspace: Path, *, silent: bool = False) -> list[str]:
    """Copy bundled ``resources/skills/skills/<name>`` dirs into the workspace.

    Args:
        workspace: The active workspace path. The target directory is
            ``<workspace>/skills/`` and is created if it does not exist.
        silent: When ``True``, suppress the per-skill log output.

    Returns:
        Names of skills that were newly installed during this call.
    """
    bundled = _optional_skills_dir()
    if not bundled.is_dir():
        return []

    target_root = workspace / "skills"
    target_root.mkdir(parents=True, exist_ok=True)

    added: list[str] = []
    for entry in sorted(bundled.iterdir()):
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
            shutil.copytree(entry, dest)
        except Exception as exc:
            console.print(
                f"[yellow]可选 skill {name} 安装失败了喵: {exc}[/yellow]"
            )
            continue

        added.append(name)

    if added and not silent:
        for name in added:
            console.print(f"  [dim]新装好可选 skill {name} 喵～[/dim]")

    return added
