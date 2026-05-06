"""Build the Windows NekoClaw executable with PyInstaller.

The resulting one-folder app contains:
  - the NekoClaw executable built from the AutoCython staging tree,
  - the compiled NekoChat frontend,
  - resources/packpy with the packed Python environments,
  - resources/chrome with portable Chromium/Chrome.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build"
WIN_BUILD_DIR = BUILD_DIR / "win"
CYTHON_STAGE = WIN_BUILD_DIR / "cython-src"
DIST_DIR = WIN_BUILD_DIR / "dist"
PYINSTALLER_WORK_DIR = WIN_BUILD_DIR / "pyinstaller"
SPEC_DIR = WIN_BUILD_DIR / "spec"
RESOURCE_STAGE = WIN_BUILD_DIR / "resources"
ENTRY_SCRIPT = BUILD_DIR / "main.py"
FRONTEND_DIR = ROOT / "nekochat" / "nekochat_frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
RESOURCES_DIR = ROOT / "resources"
PACKPY_DIR = RESOURCES_DIR / "packpy"
PACKPY_WIN64_DIR = PACKPY_DIR / "win64"
PACKPY_WHEELS_DIR = PACKPY_WIN64_DIR / "wheels"
CHROME_DIR = RESOURCES_DIR / "chrome"
APP_NAME = "NekoClaw"
PACKAGES = ("nekoclaw", "lightsear")
SKIP_MARKER = "# AutoCython No Compile"
CYTHON_SKIP_FILES = {
    Path("nekoclaw/bus/queue.py"),
    Path("nekoclaw/config/schema.py"),
    Path("nekoclaw/cron/types.py"),
}
# Subtrees inside packages that ship as data, not as Python modules. They
# are excluded from the AutoCython staging tree (so AutoCython does not try
# to compile any helper scripts they contain) and bundled directly from the
# source tree via PyInstaller's --add-data.
PACKAGE_RESOURCE_DIRS: dict[str, tuple[str, ...]] = {
    "nekoclaw": ("skills",),
}
# Third-party packages whose submodules must be bundled. PyInstaller cannot
# introspect AutoCython-compiled .pyd files for import statements, so every
# dependency that the runtime imports through `from pkg.sub import ...` has to
# be enumerated here and collected as a whole package tree.
COLLECT_SUBMODULES = (
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

# Packages that additionally ship binaries, data files, or distribution metadata
# that must be copied alongside the Python sources. `--collect-all` is a
# superset of `--collect-submodules` so these must NOT also appear above.
COLLECT_ALL = (
    # Playwright bundles a Node.js driver under playwright/driver/ that is
    # required even when connecting to an external Chromium over CDP.
    "playwright",
)


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(f'"{part}"' if " " in part else part for part in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def _pyinstaller_data(src: Path, dest: str) -> str:
    return f"{src}{os.pathsep}{dest}"


def _find_executable(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def build_frontend(*, skip_install: bool) -> None:
    if not FRONTEND_DIR.exists():
        raise FileNotFoundError(f"Missing frontend directory: {FRONTEND_DIR}")

    npm = _find_executable("npm.cmd", "npm")
    if not npm:
        raise RuntimeError("npm was not found on PATH; install Node.js or pass --skip-frontend-build.")

    if not skip_install:
        if (FRONTEND_DIR / "package-lock.json").exists():
            _run([npm, "ci"], cwd=FRONTEND_DIR)
        else:
            _run([npm, "install"], cwd=FRONTEND_DIR)

    _run([npm, "run", "build"], cwd=FRONTEND_DIR)

    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        raise FileNotFoundError(f"Frontend build did not produce {index}")


def validate_resources(*, strict: bool) -> None:
    required = [
        (PACKPY_DIR, "packed Python resource directory"),
        (PACKPY_WIN64_DIR, "Windows packed Python directory"),
        (PACKPY_WHEELS_DIR, "offline wheel cache"),
        (CHROME_DIR, "Chrome resource directory"),
        (CHROME_DIR / "chrome-win64", "portable Chrome directory"),
        (FRONTEND_DIST / "index.html", "compiled NekoChat frontend"),
    ]
    missing = [f"{label}: {path}" for path, label in required if not path.exists()]
    if PACKPY_WHEELS_DIR.exists() and not any(PACKPY_WHEELS_DIR.glob("*.whl")):
        missing.append(f"offline wheel files: {PACKPY_WHEELS_DIR / '*.whl'}")
    if not missing:
        return

    message = "Missing bundle resources:\n" + "\n".join(f"  - {item}" for item in missing)
    if strict:
        raise FileNotFoundError(message)
    print(f"WARNING: {message}", file=sys.stderr)


def _find_autocython() -> list[str]:
    for name in ("AutoCython", "autocython"):
        executable = shutil.which(name)
        if executable:
            return [executable]

    for module in ("AutoCython", "autocython"):
        probe = subprocess.run(
            [sys.executable, "-m", module, "--help"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0:
            return [sys.executable, "-m", module]

    raise RuntimeError(
        "AutoCython was not found. Install it in the active build environment, "
        "for example: python -m pip install AutoCython-jianjun"
    )


def _ignore_runtime_noise(_: str, names: list[str]) -> set[str]:
    ignored = {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
    return {name for name in names if name in ignored or name.endswith((".pyc", ".pyo", ".c"))}


def _ignore_resource_noise(_: str, names: list[str]) -> set[str]:
    ignored_dirs = {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
    ignored_suffixes = (".pyc", ".pyo")
    return {name for name in names if name in ignored_dirs or name.endswith(ignored_suffixes)}


def stage_resources(*, clean: bool) -> Path:
    """Copy resources into a PyInstaller-friendly staging tree."""
    if clean and RESOURCE_STAGE.exists():
        shutil.rmtree(RESOURCE_STAGE)
    if RESOURCE_STAGE.exists():
        shutil.rmtree(RESOURCE_STAGE)
    shutil.copytree(RESOURCES_DIR, RESOURCE_STAGE, ignore=_ignore_resource_noise)
    staged_wheels = RESOURCE_STAGE / "packpy" / "win64" / "wheels"
    wheel_count = sum(1 for _ in staged_wheels.glob("*.whl")) if staged_wheels.exists() else 0
    if wheel_count == 0:
        raise FileNotFoundError(f"No offline wheels were staged from {PACKPY_WHEELS_DIR}")
    print(f"Staged {wheel_count} offline wheels into {staged_wheels}")
    return RESOURCE_STAGE


def _mark_package_files_to_keep(package_dir: Path) -> None:
    """Keep package markers as Python files for predictable import discovery."""
    for path in package_dir.rglob("*.py"):
        rel_path = path.relative_to(CYTHON_STAGE)
        if path.name not in {"__init__.py", "__main__.py"} and rel_path not in CYTHON_SKIP_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if text.startswith(SKIP_MARKER):
            continue
        path.write_text(f"{SKIP_MARKER}\n{text}", encoding="utf-8", newline="\n")


def stage_sources(*, clean: bool) -> list[Path]:
    if clean and CYTHON_STAGE.exists():
        shutil.rmtree(CYTHON_STAGE)
    CYTHON_STAGE.mkdir(parents=True, exist_ok=True)

    staged: list[Path] = []
    for package in PACKAGES:
        src = ROOT / package
        dst = CYTHON_STAGE / package
        if not src.exists():
            raise FileNotFoundError(f"Missing package directory: {src}")
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=_ignore_runtime_noise)
        for resource_subdir in PACKAGE_RESOURCE_DIRS.get(package, ()):
            staged_resource = dst / resource_subdir
            if staged_resource.exists():
                shutil.rmtree(staged_resource)
        _mark_package_files_to_keep(dst)
        staged.append(dst)

    return staged


def _compile_package(
    autocython: list[str],
    package_dir: Path,
    *,
    delete_source: bool,
    workers: int | None,
) -> None:
    cmd = [*autocython, "-p", str(package_dir)]
    if delete_source:
        cmd.extend(["-d", "True"])
    if workers is not None:
        cmd.extend(["-c", str(workers)])
    _run(cmd, cwd=CYTHON_STAGE)


def compile_with_autocython(
    *,
    clean: bool,
    keep_source: bool,
    workers: int | None,
) -> None:
    staged_packages = stage_sources(clean=clean)
    autocython = _find_autocython()
    for package_dir in staged_packages:
        _compile_package(
            autocython,
            package_dir,
            delete_source=not keep_source,
            workers=workers,
        )
    print(f"\nCompiled staging tree ready: {CYTHON_STAGE}")


def ensure_pyinstaller() -> None:
    probe = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            "PyInstaller was not found in the active Python environment. "
            "Install it with: python -m pip install pyinstaller"
        )


def build_exe(*, clean: bool, windowed: bool, resources: Path) -> None:
    ensure_pyinstaller()
    if not ENTRY_SCRIPT.exists():
        raise FileNotFoundError(f"Missing executable entry script: {ENTRY_SCRIPT}")

    if clean:
        for path in (DIST_DIR, PYINSTALLER_WORK_DIR, SPEC_DIR):
            if path.exists():
                shutil.rmtree(path)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        APP_NAME,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(PYINSTALLER_WORK_DIR),
        "--specpath",
        str(SPEC_DIR),
        "--paths",
        str(CYTHON_STAGE),
        "--contents-directory",
        ".",
        "--collect-submodules",
        "nekoclaw",
        "--collect-submodules",
        "lightsear",
        "--add-data",
        _pyinstaller_data(CYTHON_STAGE / "nekoclaw" / "templates", "nekoclaw/templates"),
        "--add-data",
        _pyinstaller_data(ROOT / "nekoclaw" / "skills", "nekoclaw/skills"),
        "--add-data",
        _pyinstaller_data(FRONTEND_DIST, "nekochat/nekochat_frontend/dist"),
        "--add-data",
        _pyinstaller_data(resources, "resources"),
    ]
    for module in COLLECT_SUBMODULES:
        cmd.extend(["--collect-submodules", module])
    for module in COLLECT_ALL:
        cmd.extend(["--collect-all", module])
    if windowed:
        cmd.append("--windowed")
    else:
        cmd.append("--console")
    cmd.append(str(ENTRY_SCRIPT))

    _run(cmd, cwd=ROOT)
    print(f"\nExecutable ready: {DIST_DIR / APP_NAME / (APP_NAME + '.exe')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the packaged NekoClaw Windows executable.")
    parser.add_argument("--clean", action="store_true", help="Clean build outputs before packaging.")
    parser.add_argument(
        "--skip-cython",
        action="store_true",
        help="Use the existing build/win/cython-src staging tree.",
    )
    parser.add_argument(
        "--keep-source",
        action="store_true",
        help="Keep .py files in the AutoCython staging tree.",
    )
    parser.add_argument(
        "--autocython-workers",
        type=int,
        default=None,
        help="Worker count passed to AutoCython's -c option. Omit for maximum compatibility.",
    )
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="Use the existing nekochat/nekochat_frontend/dist output.",
    )
    parser.add_argument(
        "--skip-npm-install",
        action="store_true",
        help="Run npm build without npm ci/npm install first.",
    )
    parser.add_argument(
        "--strict-resources",
        action="store_true",
        help="Fail when packpy/chrome resources are incomplete instead of warning.",
    )
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="Build a windowed executable. By default the exe keeps a console for startup diagnostics.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    WIN_BUILD_DIR.mkdir(parents=True, exist_ok=True)

    if not args.skip_frontend_build:
        build_frontend(skip_install=args.skip_npm_install)

    validate_resources(strict=args.strict_resources)

    if not args.skip_cython:
        compile_with_autocython(
            clean=args.clean,
            keep_source=args.keep_source,
            workers=args.autocython_workers,
        )
    elif not CYTHON_STAGE.exists():
        raise FileNotFoundError(f"Missing AutoCython staging tree: {CYTHON_STAGE}")

    resources = stage_resources(clean=args.clean)
    build_exe(clean=args.clean, windowed=args.windowed, resources=resources)


if __name__ == "__main__":
    main()
