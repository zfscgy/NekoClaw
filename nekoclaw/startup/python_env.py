"""Bootstrap the bundled Python environment used by the exec tool.

On Windows we ship a standalone Python build under ``resources/packpy/win64``
together with offline wheels for the dependencies the ``exec`` tool needs.
This module ensures the venv exists, has the required packages installed, and
prepends its ``Scripts/`` directory to ``PATH`` so child processes pick up the
bundled interpreter.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console

console = Console()


def _repo_root() -> Path:
    """Return the source tree root when running from the unpacked project.

    This module lives at ``nekoclaw/startup/python_env.py``, so the project
    root is two parents up.
    """
    return Path(__file__).resolve().parents[2]


def _find_packaged_python(packpy_dir: Path) -> Path | None:
    """Find the standalone python.exe bundled for the offline venv install."""
    runtime_dir = packpy_dir / "python-build-standalone"
    if not runtime_dir.exists():
        return None

    for path in runtime_dir.rglob("python.exe"):
        lowered_parts = {part.lower() for part in path.parts}
        if "venv" in lowered_parts or ".venv" in lowered_parts or ".venvs" in lowered_parts:
            continue
        return path
    return None


def _run_checked(command: list[str], *, cwd: Path | None = None, error: str) -> None:
    """Run an install command, streaming output to the current terminal."""
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as exc:
        console.print(f"[red]Error: {error}: {exc}[/red]")
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]Error: {error} (exit {exc.returncode})[/red]")
        sys.exit(exc.returncode or 1)


def _requirement_package_names(requirements: Path) -> list[str]:
    """Extract distribution names from a simple requirements.txt file."""
    names: list[str] = []
    for raw_line in requirements.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        for marker in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", ";"):
            line = line.split(marker, 1)[0]
        name = line.strip()
        if name:
            names.append(name)
    return names


def _missing_venv_packages(python: Path, requirements: Path) -> list[str]:
    """Return requirement package names not installed in the target venv."""
    packages = _requirement_package_names(requirements)
    if not packages:
        return []

    code = (
        "import importlib.metadata as metadata, sys\n"
        "missing = []\n"
        "for package in sys.argv[1:]:\n"
        "    try:\n"
        "        metadata.distribution(package)\n"
        "    except metadata.PackageNotFoundError:\n"
        "        missing.append(package)\n"
        "print('\\n'.join(missing))\n"
    )
    try:
        result = subprocess.run(
            [str(python), "-c", code, *packages],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        console.print(f"[red]Error: could not check exec-tool dependencies: {exc}[/red]")
        sys.exit(1)

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _install_exec_tool_dependencies(
    dev_python: Path,
    packpy_dir: Path,
    requirements: Path,
    wheels_dir: Path,
) -> None:
    """Install exec-tool dependencies from the bundled offline wheel cache."""
    console.print("  [dim]->[/dim] Installing exec-tool dependencies from offline wheels")
    _run_checked(
        [
            str(dev_python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheels_dir),
            "-r",
            str(requirements),
        ],
        cwd=packpy_dir,
        error="failed to install exec-tool dependencies",
    )


def ensure_exec_tool_python_venv() -> None:
    """Ensure the packaged Python venv exists and is first on PATH for exec."""
    if sys.platform != "win32":
        console.print("[dim]Packaged win64 exec-tool venv is only required on Windows; skipping.[/dim]")
        return

    packpy_dir = _repo_root() / "resources" / "packpy" / "win64"
    dev_venv = packpy_dir / ".venvs" / "dev"
    dev_python = dev_venv / "Scripts" / "python.exe"
    scripts_dir = dev_venv / "Scripts"
    requirements = packpy_dir / "requirements.txt"
    wheels_dir = packpy_dir / "wheels"

    console.print(f"  [dim]Target:[/dim] {dev_venv}")
    if not dev_python.exists():
        packaged_python = _find_packaged_python(packpy_dir)

        if packaged_python is None:
            console.print(
                "[red]Error: python.exe not found under "
                f"{packpy_dir / 'python-build-standalone'}[/red]"
            )
            sys.exit(1)
        if not requirements.exists():
            console.print(f"[red]Error: requirements file not found: {requirements}[/red]")
            sys.exit(1)
        if not wheels_dir.exists():
            console.print(f"[red]Error: offline wheels directory not found: {wheels_dir}[/red]")
            sys.exit(1)

        console.print(f"  [dim]->[/dim] Creating venv with {packaged_python}")
        _run_checked(
            [str(packaged_python), "-m", "venv", str(dev_venv)],
            error="failed to create exec-tool venv",
        )
        console.print("  [green]✓[/green] Exec-tool venv created")

        _install_exec_tool_dependencies(dev_python, packpy_dir, requirements, wheels_dir)
    else:
        console.print("  [green]✓[/green] Exec-tool venv already exists")
        if not requirements.exists():
            console.print(f"[red]Error: requirements file not found: {requirements}[/red]")
            sys.exit(1)
        if not wheels_dir.exists():
            console.print(f"[red]Error: offline wheels directory not found: {wheels_dir}[/red]")
            sys.exit(1)

        console.print("  [dim]->[/dim] Checking exec-tool dependencies")
        missing_packages = _missing_venv_packages(dev_python, requirements)
        if missing_packages:
            console.print(
                "[yellow]Missing exec-tool packages: "
                f"{', '.join(missing_packages)}[/yellow]"
            )
            _install_exec_tool_dependencies(dev_python, packpy_dir, requirements, wheels_dir)
        else:
            console.print("  [green]✓[/green] Exec-tool dependencies ready")

    console.print("  [dim]->[/dim] Linking source tree into exec-tool venv")
    try:
        site_packages = subprocess.check_output(
            [
                str(dev_python),
                "-c",
                "import sysconfig; print(sysconfig.get_paths()['purelib'])",
            ],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        console.print(f"[red]Error: could not locate exec-tool site-packages: {exc}[/red]")
        sys.exit(1)

    if not site_packages:
        console.print(f"[red]Error: could not locate exec-tool site-packages for {dev_python}[/red]")
        sys.exit(1)

    pth_file = Path(site_packages) / "nekoclaw.pth"
    pth_file.write_text(str(_repo_root()), encoding="utf-8")

    scripts_path = str(scripts_dir)
    current_path = os.environ.get("PATH", "")
    current_parts = [part for part in current_path.split(os.pathsep) if part]
    if scripts_path.casefold() not in {part.casefold() for part in current_parts}:
        os.environ["PATH"] = scripts_path + os.pathsep + current_path
    os.environ["VIRTUAL_ENV"] = str(dev_venv)
    os.environ.pop("PYTHONHOME", None)

    console.print(f"  [green]✓[/green] Exec-tool Python ready: {dev_python}")
