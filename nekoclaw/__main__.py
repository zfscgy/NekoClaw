"""
Entry point for running nekoclaw as a module: python -m nekoclaw
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console

from nekoclaw import __logo__
from nekoclaw.config.schema import Config
from nekoclaw.utils.helpers import sync_workspace_templates

console = Console()


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
    from nekoclaw.config.loader import create_default_configs, load_config, set_config_path
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
    if workspace:
        loaded.agents.defaults.workspace = workspace
    return loaded


def gateway(
    port: int = 18790,
    workspace: str | None = None,
    verbose: bool = False,
    config: str | None = None,
) -> None:
    """Start the nekoclaw gateway."""
    from nekoclaw.agent.loop import AgentLoop
    from nekoclaw.bus.queue import MessageBus
    from nekoclaw.channels.manager import ChannelManager
    from nekoclaw.config.paths import get_cron_dir
    from nekoclaw.cron.service import CronService
    from nekoclaw.cron.types import CronJob
    from nekoclaw.heartbeat.service import HeartbeatService
    from nekoclaw.providers.base import StreamDelta
    from nekoclaw.session.manager import SessionManager

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    cfg = _load_runtime_config(config, workspace)

    console.print(f"{__logo__} Starting nekoclaw gateway on port {port}...")
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
