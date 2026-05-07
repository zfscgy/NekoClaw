"""Runtime skill management.

Skills live under ``<workspace>/skills/<name>/SKILL.md``. This module lets the
user add new skills from a directory (moved into the skills directory) and
toggle individual skills on/off without deleting them — a disabled skill is
packed into ``<name>.zip`` alongside the skills directory, and re-enabling it
decompresses the archive back into a directory.

Builtin skills (shipped with the nekoclaw package) are listed for visibility
but cannot be modified here.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from typing import Literal

from loguru import logger

from nekoclaw.agent.skills import BUILTIN_SKILLS_DIR, SkillsLoader
from nekoclaw.config.manager import get_global_config


SkillStatus = Literal["enabled", "disabled"]
SkillSource = Literal["workspace", "builtin"]

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def _resolve_workspace() -> Path:
    """Return the active workspace path."""
    return get_global_config().workspace_path


def _skills_dir() -> Path:
    """Return (and create) the user-managed skills directory."""
    path = _resolve_workspace() / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    """Validate and return the skill name, rejecting path traversal attempts."""
    name = name.strip()
    if not name or not _SAFE_NAME.match(name) or name in {".", ".."}:
        raise ValueError(f"Invalid skill name: {name!r}")
    return name


def _describe(entry_dir: Path | None, name: str, source: SkillSource) -> dict:
    """Build a skill descriptor dict, reading metadata from SKILL.md if present."""
    description = ""
    if entry_dir is not None:
        skill_file = entry_dir / "SKILL.md"
        if skill_file.exists():
            try:
                text = skill_file.read_text(encoding="utf-8", errors="ignore")
                m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
                if m:
                    for line in m.group(1).splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            if k.strip().lower() == "description":
                                description = v.strip().strip('"\'')
                                break
            except OSError:
                pass
    return {"name": name, "source": source, "description": description}


def list_skills() -> list[dict]:
    """Return every installed skill with its current status.

    Each entry is a dict: ``{name, source, status, description}``. Workspace
    skills can be ``enabled`` or ``disabled``; builtin skills are always
    ``enabled`` and read-only.
    """
    skills_dir = _skills_dir()
    result: list[dict] = []
    seen: set[str] = set()

    for entry in sorted(skills_dir.iterdir()):
        if entry.is_dir():
            name = entry.name
            info = _describe(entry, name, "workspace")
            info["status"] = "enabled"
            info["editable"] = True
            result.append(info)
            seen.add(name)
        elif entry.is_file() and entry.suffix.lower() == ".zip":
            name = entry.stem
            if name in seen:
                continue
            description = _read_zip_description(entry)
            result.append({
                "name": name,
                "source": "workspace",
                "status": "disabled",
                "description": description,
                "editable": True,
            })
            seen.add(name)

    if BUILTIN_SKILLS_DIR.exists():
        for entry in sorted(BUILTIN_SKILLS_DIR.iterdir()):
            if entry.is_dir() and entry.name not in seen:
                info = _describe(entry, entry.name, "builtin")
                info["status"] = "enabled"
                info["editable"] = False
                result.append(info)
                seen.add(entry.name)

    return result


def _read_zip_description(zip_path: Path) -> str:
    """Extract the ``description`` frontmatter field from a zipped skill, if any."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                if member.endswith("SKILL.md"):
                    with zf.open(member) as fh:
                        head = fh.read(4096).decode("utf-8", errors="ignore")
                    m = re.match(r"^---\n(.*?)\n---", head, re.DOTALL)
                    if m:
                        for line in m.group(1).splitlines():
                            if ":" in line:
                                k, v = line.split(":", 1)
                                if k.strip().lower() == "description":
                                    return v.strip().strip('"\'')
                    break
    except (zipfile.BadZipFile, OSError):
        pass
    return ""


def add_skill_from_directory(source: Path | str, name: str | None = None) -> dict:
    """Install a new skill by moving a local directory into the skills dir.

    Args:
        source: Path to a directory containing at minimum a ``SKILL.md`` file.
        name: Optional name override. Defaults to ``source.name``.

    Raises:
        FileNotFoundError: If the source directory (or its ``SKILL.md``) is missing.
        FileExistsError: If a skill with this name already exists.
    """
    src = Path(source).expanduser().resolve()
    if not src.exists() or not src.is_dir():
        raise FileNotFoundError(f"Source is not a directory: {src}")
    if not (src / "SKILL.md").exists():
        raise FileNotFoundError(f"Source directory missing SKILL.md: {src}")

    skill_name = _safe_name(name or src.name)
    dest_dir = _skills_dir() / skill_name
    dest_zip = _skills_dir() / f"{skill_name}.zip"
    if dest_dir.exists() or dest_zip.exists():
        raise FileExistsError(f"Skill already exists: {skill_name}")

    shutil.move(str(src), str(dest_dir))
    logger.info("Installed skill {} from {}", skill_name, src)
    info = _describe(dest_dir, skill_name, "workspace")
    info["status"] = "enabled"
    info["editable"] = True
    return info


def add_skill_from_zip(zip_path: Path | str, name: str | None = None) -> dict:
    """Install a new skill from a zip archive.

    Accepts archives whose top level is either ``<name>/SKILL.md`` or a
    bare ``SKILL.md`` plus siblings. Returns the installed skill descriptor.
    """
    src = Path(zip_path).expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"Zip not found: {src}")

    with zipfile.ZipFile(src) as zf:
        members = zf.namelist()
        if not members:
            raise ValueError("Zip archive is empty")

        top_levels = {m.split("/", 1)[0] for m in members if m.strip("/")}
        has_top_dir = (
            len(top_levels) == 1
            and next(iter(top_levels)) + "/" in members
        )
        skill_root = next(iter(top_levels)) if has_top_dir else None

        if not any(m.endswith("SKILL.md") for m in members):
            raise ValueError("Zip archive does not contain SKILL.md")

        skill_name = _safe_name(name or skill_root or src.stem)
        dest_dir = _skills_dir() / skill_name
        dest_zip = _skills_dir() / f"{skill_name}.zip"
        if dest_dir.exists() or dest_zip.exists():
            raise FileExistsError(f"Skill already exists: {skill_name}")

        dest_dir.mkdir(parents=True)
        try:
            for m in members:
                if m.endswith("/"):
                    continue
                rel = m
                if has_top_dir and skill_root:
                    prefix = f"{skill_root}/"
                    if m.startswith(prefix):
                        rel = m[len(prefix):]
                    else:
                        continue
                if not rel:
                    continue
                target = dest_dir / rel
                if not str(target.resolve()).startswith(str(dest_dir.resolve())):
                    raise ValueError(f"Zip slip detected: {m}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(m) as fh, open(target, "wb") as out:
                    shutil.copyfileobj(fh, out)
        except Exception:
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise

    logger.info("Installed skill {} from zip {}", skill_name, src)
    info = _describe(dest_dir, skill_name, "workspace")
    info["status"] = "enabled"
    info["editable"] = True
    return info


def disable_skill(name: str) -> dict:
    """Disable a workspace skill by compressing its directory into ``<name>.zip``.

    Builtin skills cannot be disabled.
    """
    skill_name = _safe_name(name)
    skills_dir = _skills_dir()
    target = skills_dir / skill_name
    if not target.exists() or not target.is_dir():
        raise FileNotFoundError(f"Enabled workspace skill not found: {skill_name}")

    zip_path = skills_dir / f"{skill_name}.zip"
    tmp_zip = skills_dir / f"{skill_name}.zip.tmp"
    try:
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file in target.rglob("*"):
                if file.is_file():
                    zf.write(file, arcname=f"{skill_name}/{file.relative_to(target).as_posix()}")
        if zip_path.exists():
            zip_path.unlink()
        tmp_zip.replace(zip_path)
    except Exception:
        tmp_zip.unlink(missing_ok=True)
        raise

    shutil.rmtree(target)
    logger.info("Disabled skill {}", skill_name)
    return {
        "name": skill_name,
        "source": "workspace",
        "status": "disabled",
        "description": _read_zip_description(zip_path),
        "editable": True,
    }


def enable_skill(name: str) -> dict:
    """Re-enable a disabled workspace skill by unzipping ``<name>.zip``."""
    skill_name = _safe_name(name)
    skills_dir = _skills_dir()
    zip_path = skills_dir / f"{skill_name}.zip"
    if not zip_path.exists():
        raise FileNotFoundError(f"Disabled skill not found: {skill_name}")

    dest_dir = skills_dir / skill_name
    if dest_dir.exists():
        raise FileExistsError(f"Skill already enabled: {skill_name}")

    dest_dir.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.namelist()
            top_levels = {m.split("/", 1)[0] for m in members if m.strip("/")}
            has_top_dir = len(top_levels) == 1 and (next(iter(top_levels)) == skill_name)

            for m in members:
                if m.endswith("/"):
                    continue
                rel = m
                if has_top_dir:
                    prefix = f"{skill_name}/"
                    if m.startswith(prefix):
                        rel = m[len(prefix):]
                    else:
                        continue
                if not rel:
                    continue
                target = dest_dir / rel
                if not str(target.resolve()).startswith(str(dest_dir.resolve())):
                    raise ValueError(f"Zip slip detected: {m}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(m) as fh, open(target, "wb") as out:
                    shutil.copyfileobj(fh, out)
    except Exception:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise

    zip_path.unlink()
    logger.info("Enabled skill {}", skill_name)

    info = _describe(dest_dir, skill_name, "workspace")
    info["status"] = "enabled"
    info["editable"] = True
    return info


def get_loader() -> SkillsLoader:
    """Return a SkillsLoader bound to the active workspace."""
    return SkillsLoader(_resolve_workspace())
