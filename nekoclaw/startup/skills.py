"""Sync bundled optional skills into the user's workspace.

Skills come from two places:

- Built-in skills live under ``nekoclaw/skills/internal/`` and are loaded
  directly from the package by the agent — they are always available and are
  never copied into the workspace.
- Optional (recommended add-on) skills are downloaded at build time into
  ``resources/skills/skills/`` (see ``resources/skills/build.py``). On every
  launch they are synced into ``<workspace>/skills/<name>/`` so they appear as
  workspace-managed skills: the user can edit them, disable (zip) them, or
  delete them like any other user-installed skill.

Unlike a plain first-run copy, this sync keeps already-installed optional
skills up to date: if the bundled content changes (e.g. a newer app version
ships an improved ``SKILL.md``), the workspace copy is refreshed to match —
whether it's currently an enabled directory or a disabled ``<name>.zip``
archive (which is rebuilt in place, so the disabled state is preserved).
Skills that were removed from the bundle, or that never matched a bundled
name, are left alone.
"""

from __future__ import annotations

import shutil
import zipfile
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


def _bundle_snapshot(entry: Path) -> dict[str, bytes]:
    """Read every file under a bundled skill directory as ``{relpath: content}``."""
    return {
        path.relative_to(entry).as_posix(): path.read_bytes()
        for path in sorted(entry.rglob("*"))
        if path.is_file()
    }


def _dir_snapshot(dest: Path) -> dict[str, bytes]:
    """Read every file under an installed (unzipped) workspace skill directory."""
    return {
        path.relative_to(dest).as_posix(): path.read_bytes()
        for path in sorted(dest.rglob("*"))
        if path.is_file()
    }


def _zip_snapshot(zip_path: Path, name: str) -> dict[str, bytes] | None:
    """Read a disabled skill's ``<name>.zip`` as ``{relpath: content}``.

    Returns ``None`` if the archive can't be read, so callers can treat it as
    "different" and just rebuild it.
    """
    prefix = f"{name}/"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            snapshot: dict[str, bytes] = {}
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                rel = member[len(prefix):] if member.startswith(prefix) else member
                with zf.open(member) as fh:
                    snapshot[rel] = fh.read()
            return snapshot
    except (zipfile.BadZipFile, OSError):
        return None


def _write_zip(zip_path: Path, name: str, files: dict[str, bytes]) -> None:
    """Write ``files`` into ``<name>.zip`` under a top-level ``<name>/`` prefix.

    Writes to a temp file first and swaps it in, so a crash mid-write can't
    leave a half-written archive in place of a working one.
    """
    tmp_zip = zip_path.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel, content in files.items():
                zf.writestr(f"{name}/{rel}", content)
        tmp_zip.replace(zip_path)
    except Exception:
        tmp_zip.unlink(missing_ok=True)
        raise


def _diff_snapshots(
    old: dict[str, bytes], new: dict[str, bytes]
) -> tuple[list[str], list[str], list[str]]:
    """Diff two skill file snapshots into ``(added, removed, modified)`` relpaths."""
    old_keys, new_keys = set(old), set(new)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    modified = sorted(k for k in old_keys & new_keys if old[k] != new[k])
    return added, removed, modified


def _format_diff(added: list[str], removed: list[str], modified: list[str]) -> str:
    """Render a compact, human-readable summary of a skill's file-level diff."""
    parts = []
    if modified:
        parts.append(f"改动 {', '.join(modified)}")
    if added:
        parts.append(f"新增 {', '.join(added)}")
    if removed:
        parts.append(f"删除 {', '.join(removed)}")
    return "；".join(parts)


def sync_optional_skills(workspace: Path, *, silent: bool = False) -> list[str]:
    """Sync bundled ``resources/skills/skills/<name>`` dirs into the workspace.

    For every bundled optional skill:

    - if it's missing from the workspace, it's installed fresh (as an
      enabled directory);
    - if it's already installed (enabled directory or disabled ``.zip``) and
      its content differs from the bundle, it's refreshed in place, keeping
      whichever enabled/disabled state it was already in;
    - if it's already installed and matches the bundle exactly, it's left
      untouched (no unnecessary disk writes or log spam on every launch).

    Unless ``silent``, each install/update is logged, and updates include a
    file-level diff (which files were added/modified/removed) against the
    previously installed copy so it's clear *what* changed, not just *that*
    something changed.

    Args:
        workspace: The active workspace path. The target directory is
            ``<workspace>/skills/`` and is created if it does not exist.
        silent: When ``True``, suppress the per-skill log output.

    Returns:
        Names of skills that were newly installed or updated during this call.
    """
    bundled = _optional_skills_dir()
    if not bundled.is_dir():
        return []

    target_root = workspace / "skills"
    target_root.mkdir(parents=True, exist_ok=True)

    added: list[str] = []
    updated: list[tuple[str, str, bool]] = []  # (name, diff summary, was_disabled)
    unchanged = 0
    for entry in sorted(bundled.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "SKILL.md").is_file():
            continue

        name = entry.name
        dest = target_root / name
        zipped = target_root / f"{name}.zip"

        try:
            bundle_files = _bundle_snapshot(entry)
            if zipped.exists():
                old_files = _zip_snapshot(zipped, name) or {}
                if old_files != bundle_files:
                    _write_zip(zipped, name, bundle_files)
                    diff = _format_diff(*_diff_snapshots(old_files, bundle_files))
                    updated.append((name, diff, True))
                else:
                    unchanged += 1
            elif dest.exists():
                old_files = _dir_snapshot(dest)
                if old_files != bundle_files:
                    shutil.rmtree(dest)
                    shutil.copytree(entry, dest)
                    diff = _format_diff(*_diff_snapshots(old_files, bundle_files))
                    updated.append((name, diff, False))
                else:
                    unchanged += 1
            else:
                shutil.copytree(entry, dest)
                added.append(name)
        except Exception as exc:
            console.print(
                f"[yellow]可选 skill {name} 安装/更新失败了喵: {exc}[/yellow]"
            )
            continue

    if not silent:
        if added or updated:
            for name in added:
                console.print(f"  [dim]✓ 新装好可选 skill [cyan]{name}[/cyan] 喵～[/dim]")
            for name, diff, was_disabled in updated:
                tag = "（已禁用，保持禁用状态）" if was_disabled else ""
                suffix = f"：{diff}" if diff else ""
                console.print(
                    f"  [dim]✓ 可选 skill [cyan]{name}[/cyan]{tag} 已更新到最新版本喵{suffix}[/dim]"
                )
        elif unchanged:
            console.print("  [dim]✓ 可选 skill 都已是最新版本，不需要更新喵～[/dim]")

    return added + [name for name, _, _ in updated]
