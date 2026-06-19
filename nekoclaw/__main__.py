"""
Entry point for running nekoclaw as a module: python -m nekoclaw
"""

from __future__ import annotations

import argparse
import asyncio

from rich.console import Console

from nekoclaw import __logo__
from nekoclaw.config.schema import Config
from nekoclaw.startup import (
    configure_logging,
    ensure_exec_tool_python_venv,
    load_runtime_config,
    nekochat_url,
    open_nekochat_browser,
    print_neko_startup_art,
    sync_optional_skills,
)
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

    # ``agents.defaults.model`` is qualified (``providerName/modelId``); resolve
    # it to the owning provider's credentials and the bare model id.
    resolved = config.providers.resolve(config.agents.defaults.model)
    p = resolved.provider

    if not p.api_key:
        console.print(
            "[yellow]还没有配置 OpenAI API Key 喵～ "
            "可以稍后从 NekoChat 配置面板或 ~/.nekoclaw/providers.json 里补上。[/yellow]"
        )

    return OpenAIProvider(
        api_key=p.api_key,
        api_base=p.api_base,
        default_model=resolved.model_id,
        extra_headers=p.extra_headers,
    )


def gateway(
    workspace: str | None = None,
    verbose: bool = False,
    config: str | None = None,
) -> None:
    """Start the nekoclaw gateway."""
    log_path = configure_logging(verbose=verbose)

    print_neko_startup_art(console)
    console.rule(
        f"[bold magenta]{__logo__} 欢迎使用 NekoClaw，主人～现在就由 Neko 来帮你启动吧[/bold magenta]"
    )
    console.print(
        f"  [dim]详细日志会被喵咪悄悄记到 [cyan]{log_path}[/cyan] 里啦～[/dim]"
    )

    # Step 1 wraps everything that needs to happen before we can boot:
    #   - ensure the bundled Python venv is ready (Windows exec tool)
    #   - load (and, if needed, interactively prompt for) the runtime config
    #   - sync optional skills into the workspace
    # ``load_runtime_config`` may print prompts of its own; we run it inside
    # this step so the workspace path is available for ``sync_optional_skills``.
    console.print(
        "[bold cyan][1/2] 准备运行环境喵～（Python venv + 配置 + 可选 skills）[/bold cyan]"
    )
    ensure_exec_tool_python_venv()
    cfg = load_runtime_config(config, workspace)
    sync_optional_skills(cfg.workspace_path)
    console.print("[green]✓[/green] 运行环境已经全部就绪喵～")

    console.rule("[dim]✦ 步骤 1 完成喵 ✦[/dim]")

    console.print("[bold cyan][2/2] 启动猫娘 AI ～[/bold cyan]")
    console.print(f"{__logo__} 正在唤醒 nekoclaw 喵～请稍等一下下")
    from nekoclaw.agent.dispatcher import AgentLoopDispatcher
    from nekoclaw.bus.queue import MessageBus
    from nekoclaw.channels.manager import ChannelManager
    from nekoclaw.config.paths import get_cron_dir
    from nekoclaw.cron.service import CronService
    from nekoclaw.cron.types import CronJob
    from nekoclaw.heartbeat.service import HeartbeatService
    from nekoclaw.providers.base import StreamDelta

    sync_workspace_templates(cfg.workspace_path)
    bus = MessageBus()
    provider = _make_provider(cfg)

    # Resolve the qualified default model once so the dispatcher gets the bare
    # model id. Per-model capability flags are read from config on demand by
    # ``delta_to_openai``.
    resolved_model = cfg.providers.resolve(cfg.agents.defaults.model)

    from nekoclaw.config.manager import set_runtime
    set_runtime(cfg, provider)

    cron_store_path = get_cron_dir() / "jobs.json"
    cron = CronService(cron_store_path)

    agent = AgentLoopDispatcher(
        bus=bus,
        provider=provider,
        workspace=cfg.workspace_path,
        model=resolved_model.model_id,
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

        session_key = f"cron:{job.id}"
        loop = agent.get_or_create_loop(session_key)

        cron_tool = loop.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)
        try:
            response = await agent.process_direct(
                reminder_note,
                session_key=session_key,
                channel=job.payload.channel or "system",
                chat_id=job.payload.to or "direct",
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        message_tool = loop.tools.get("send_message_with_attachments")
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
        console.print(
            f"[green]✓[/green] 已经叫醒了这些频道喵: "
            f"{', '.join(channels.enabled_channels)}"
        )
    else:
        console.print("[yellow]咦？还没有启用任何频道喵，主人快去配置一下吧～[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(
            f"[green]✓[/green] 定时任务: Neko 已经记住 {cron_status['jobs']} 项小日程啦～"
        )

    console.print(
        f"[green]✓[/green] 心跳: Neko 每 {hb_cfg.interval_s} 秒会偷偷看看主人喵～"
    )

    web_url = nekochat_url(cfg)

    def _print_ready_banner() -> None:
        console.rule(
            "[bold magenta]( =ↀωↀ=) 猫娘 AI 启动完成，开始工作～[/bold magenta]"
        )
        if web_url:
            console.print(
                f"  [bold cyan]NekoChat 前端地址：[/bold cyan]"
                f"[link={web_url}]{web_url}[/link]"
            )
        console.print(
            "  [dim]按 [bold]Ctrl+C[/bold] 可以让 Neko 安静地睡觉喵 zzZ[/dim]"
        )
        console.rule("[dim]✦ 祝主人玩得开心喵～ ✦[/dim]")

    async def run() -> None:
        try:
            await cron.start()
            await heartbeat.start()

            async def _post_start_tasks() -> None:
                # Give the channels a moment to bind their sockets so the
                # browser doesn't race ahead and hit a "connection refused".
                await asyncio.sleep(1.2)
                _print_ready_banner()
                open_nekochat_browser(cfg)

            asyncio.create_task(_post_start_tasks())

            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.rule(
                "[bold magenta]收到关机指令，Neko 要去睡觉啦～(=ＴェＴ=)[/bold magenta]"
            )
        finally:
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()

    asyncio.run(run())


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="nekoclaw",
        description=f"{__logo__} nekoclaw - 个人猫娘 AI 助手网关喵～",
    )
    parser.add_argument("--workspace", "-w", default=None, help="工作区目录")
    parser.add_argument("--verbose", "-v", action=None, help="输出详细日志")
    parser.add_argument("--config", "-c", default=None, help="配置文件路径")
    args = parser.parse_args()
    gateway(workspace=args.workspace, verbose=args.verbose, config=args.config)


if __name__ == "__main__":
    main()
