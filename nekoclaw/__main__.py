"""
Entry point for running nekoclaw as a module: python -m nekoclaw
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from nekoclaw import __logo__
from nekoclaw.config.schema import Config
from nekoclaw.utils.helpers import sync_workspace_templates

console = Console()


def _repo_root() -> Path:
    """Return the source tree root when running from the unpacked project."""
    return Path(__file__).resolve().parents[1]


def _missing_gateway_config_keys(config: Config) -> list[str]:
    """Return the required LLM settings that are not configured."""
    missing: list[str] = []
    p = config.providers.openai

    if not config.agents.defaults.model.strip():
        missing.append("model")
    if not (p.api_base or "").strip():
        missing.append("openai_base_url")
    if not p.api_key.strip():
        missing.append("openai_api_key")

    return missing


def _make_provider(config: Config):
    """Create the OpenAI provider from config.

    If no API key is configured yet, we still build a placeholder provider so
    the user can start the service and set credentials via the NekoChat UI
    (``/api/manager/config``); the provider will be reconfigured live at that
    point. Requests made before a key is set will fail with a provider error.
    """
    from nekoclaw.providers.openai_provider import OpenAIProvider

    model = config.agents.defaults.model
    p = config.providers.openai

    if not p.api_key:
        console.print(
            "[yellow]Warning: No OpenAI API key configured — "
            "set it from the NekoChat config panel or ~/.nekoclaw/providers.json.[/yellow]"
        )

    return OpenAIProvider(
        api_key=p.api_key,
        api_base=p.api_base,
        default_model=model,
        extra_headers=p.extra_headers,
    )


def _load_runtime_config(config_path_str: str | None = None, workspace: str | None = None) -> Config:
    """Load config and optionally override the active workspace."""
    from nekoclaw.config.loader import create_default_configs, prompt_configs, set_config_path
    from nekoclaw.manager import config

    config_path = None
    if config_path_str:
        config_path = Path(config_path_str).expanduser().resolve()
        if not config_path.exists():
            console.print(f"[red]Error: Config file not found: {config_path}[/red]")
            sys.exit(1)
        set_config_path(config_path)
    else:
        created = create_default_configs()
        if created:
            console.print(f"[dim]Created default config files in {created[0].parent}[/dim]")

    loaded = config.get_global_config()
    missing = _missing_gateway_config_keys(loaded)
    if missing:
        console.print(
            "[yellow]Missing required gateway config: "
            f"{', '.join(missing)}. Please set them now.[/yellow]"
        )
        loaded = prompt_configs(config_path)
        missing = _missing_gateway_config_keys(loaded)
        if missing:
            console.print(
                "[red]Error: gateway requires "
                f"{', '.join(missing)} before it can start.[/red]"
            )
            sys.exit(1)

    if workspace:
        loaded.agents.defaults.workspace = workspace
    return loaded


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


def _install_exec_tool_dependencies(dev_python: Path, packpy_dir: Path, requirements: Path, wheels_dir: Path) -> None:
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


def _ensure_exec_tool_python_venv() -> None:
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


def gateway(
    port: int = 18790,
    workspace: str | None = None,
    verbose: bool = False,
    config: str | None = None,
) -> None:
    """Start the nekoclaw gateway."""
    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    console.print("[bold cyan][1/3] Checking config[/bold cyan]")
    cfg = _load_runtime_config(config, workspace)
    console.print("[green]✓[/green] Config ready")

    console.print("[bold cyan][2/3] Checking Python venv for exec tool[/bold cyan]")
    _ensure_exec_tool_python_venv()

    console.print("[bold cyan][3/3] Starting gateway[/bold cyan]")
    console.print(f"{__logo__} Starting nekoclaw gateway on port {port}...")
    from nekoclaw.agent.loop import AgentLoop
    from nekoclaw.bus.queue import MessageBus
    from nekoclaw.channels.manager import ChannelManager
    from nekoclaw.config.paths import get_cron_dir
    from nekoclaw.cron.service import CronService
    from nekoclaw.cron.types import CronJob
    from nekoclaw.heartbeat.service import HeartbeatService
    from nekoclaw.providers.base import StreamDelta
    from nekoclaw.session.manager import SessionManager

    sync_workspace_templates(
        cfg.workspace_path, template_locale=cfg.agents.defaults.template_locale
    )
    bus = MessageBus()
    provider = _make_provider(cfg)

    from nekoclaw.manager.runtime import set_runtime
    set_runtime(cfg, provider)

    cron_store_path = get_cron_dir() / "jobs.json"
    cron = CronService(cron_store_path)

    agent = AgentLoop(
        session=SessionManager(cfg.workspace_path).get_or_create("gateway:default"),
        bus=bus,
        provider=provider,
        workspace=cfg.workspace_path,
        model=cfg.agents.defaults.model,
        temperature=cfg.agents.defaults.temperature,
        max_tokens=cfg.agents.defaults.max_tokens,
        max_iterations=cfg.agents.defaults.max_tool_iterations,
        memory_window=cfg.agents.defaults.memory_window,
        reasoning_effort=cfg.agents.defaults.reasoning_effort,
        cron_service=cron,
        restrict_to_workspace=cfg.tools.restrict_to_workspace,
    )

    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        from nekoclaw.agent.tools.cron import CronTool
        from nekoclaw.agent.tools.message import MessageTool

        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)
        try:
            response = await agent.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "system",
                chat_id=job.payload.to or "direct",
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        message_tool = agent.tools.get("send_message_with_attachments")
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        if job.payload.deliver and job.payload.to and response:
            from nekoclaw.bus.events import OutboundMessage

            await bus.publish_outbound(OutboundMessage(
                channel=job.payload.channel or "system",
                chat_id=job.payload.to,
                msg=StreamDelta(type="content", content=response),
            ))
        return response

    cron.on_job = on_cron_job

    channels = ChannelManager(cfg, bus)

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        enabled = set(channels.enabled_channels)
        for item in agent.sessions.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        return "system", "direct"

    async def on_heartbeat_execute(tasks: str) -> str:
        """Execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()
        return await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
        )

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from nekoclaw.bus.events import OutboundMessage

        channel, chat_id = _pick_heartbeat_target()
        if channel == "system":
            return
        await bus.publish_outbound(OutboundMessage(
            channel=channel, chat_id=chat_id,
            msg=StreamDelta(type="content", content=response),
        ))

    hb_cfg = cfg.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=cfg.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
    )
    set_runtime(cfg, provider, agent=agent, heartbeat=heartbeat)

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")

    async def run() -> None:
        try:
            await cron.start()
            await heartbeat.start()
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()

    asyncio.run(run())


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="nekoclaw",
        description=f"{__logo__} nekoclaw - Personal AI Assistant gateway",
    )
    parser.add_argument("--port", "-p", type=int, default=18790, help="Gateway port (default: 18790)")
    parser.add_argument("--workspace", "-w", default=None, help="Workspace directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--config", "-c", default=None, help="Path to config file")
    args = parser.parse_args()
    gateway(port=args.port, workspace=args.workspace, verbose=args.verbose, config=args.config)


if __name__ == "__main__":
    main()
