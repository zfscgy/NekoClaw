"""Download the optional skill pack into ``resources/skills/skills``.

Optional (recommended add-on) skills are no longer vendored in the repository.
They live in a separate upstream project and are fetched at build time by this
script. The packaging step (``build/install.py``) then bundles whatever is in
``resources/skills/skills`` into the app, and on first launch the gateway copies
those skills into the user's workspace (see
``nekoclaw.startup.sync_optional_skills``).

Usage::

    python resources/skills/build.py            # download default ref (main)
    python resources/skills/build.py --ref main # download a specific branch/tag
    python resources/skills/build.py --keep      # merge into existing dir

The skills are sourced from https://github.com/zfscgy/ZhSkills — every
top-level directory in that repository that contains a ``SKILL.md`` file is
treated as a skill and copied into ``resources/skills/skills/<name>``.
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = "zfscgy/ZhSkills"
DEFAULT_REF = "main"
HERE = Path(__file__).resolve().parent
TARGET_DIR = HERE / "skills"


def _archive_url(ref: str) -> str:
    return f"https://github.com/{REPO}/archive/refs/heads/{ref}.zip"


def _download(url: str) -> bytes:
    print(f"+ downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "NekoClaw-skill-builder"})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - trusted GitHub URL
        return resp.read()


def _extract_skills(archive: bytes, dest: Path) -> list[str]:
    """Extract every ``<skill>/SKILL.md`` directory from the archive into ``dest``."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            zf.extractall(tmp_path)

        roots = [p for p in tmp_path.iterdir() if p.is_dir()]
        if not roots:
            raise RuntimeError("Downloaded archive contained no directories")
        repo_root = roots[0]

        installed: list[str] = []
        for entry in sorted(repo_root.iterdir()):
            if not entry.is_dir():
                continue
            if not (entry / "SKILL.md").is_file():
                continue
            skill_dest = dest / entry.name
            if skill_dest.exists():
                shutil.rmtree(skill_dest)
            shutil.copytree(entry, skill_dest)
            installed.append(entry.name)
        return installed


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the optional skill pack.")
    parser.add_argument(
        "--ref",
        default=DEFAULT_REF,
        help=f"Branch or tag of {REPO} to download (default: {DEFAULT_REF}).",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep existing skills in the target directory instead of wiping it first.",
    )
    args = parser.parse_args()

    if TARGET_DIR.exists() and not args.keep:
        shutil.rmtree(TARGET_DIR)
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    try:
        archive = _download(_archive_url(args.ref))
    except Exception as exc:  # noqa: BLE001 - surface a clear build error
        print(f"ERROR: failed to download {REPO}@{args.ref}: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        installed = _extract_skills(archive, TARGET_DIR)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: failed to extract skills: {exc}", file=sys.stderr)
        sys.exit(1)

    if not installed:
        print(f"WARNING: no skills found in {REPO}@{args.ref}", file=sys.stderr)
        return

    print(f"Installed {len(installed)} optional skill(s) into {TARGET_DIR}:")
    for name in installed:
        print(f"  - {name}")


if __name__ == "__main__":
    main()
