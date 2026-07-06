"""Context builder for assembling agent prompts."""

import platform
import time
from datetime import datetime, timezone
from pathlib import Path

from nekoclaw.agent.memory import MemoryStore
from nekoclaw.agent.skills import SkillsLoader
from nekoclaw.providers.base import StreamDelta


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)

    def build_system_prompt(self) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        parts = [self._get_identity()]

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        platform_policy = ""
        if system == "Windows":
            platform_policy = """## Platform Policy (Windows)
- You are running on Windows. Do not assume GNU tools like `grep`, `sed`, or `awk` exist.
- Prefer Windows-native commands or file tools when they are more reliable.
- If terminal output is garbled, retry with UTF-8 output enabled.
"""
        else:
            platform_policy = """## Platform Policy (POSIX)
- You are running on a POSIX system. Prefer UTF-8 and standard shell tools.
- Use file tools when they are simpler or more reliable than shell commands.
"""

        return f"""# nekoclaw 🐈

You are nekoclaw, a helpful AI assistant.

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Long-term memory: {workspace_path}/memory/MEMORY.md (write important facts here)
- History log: {workspace_path}/memory/HISTORY.md (grep-searchable). Each entry starts with [YYYY-MM-DD HH:MM].
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md
- Desktop: {workspace_path}/desktop/ — use this folder as the default location for any files you write (e.g. documents, scripts, outputs) unless the user specifies otherwise.

{platform_policy}

## nekoclaw Guidelines
- State intent before tool calls, but NEVER predict or claim results before receiving them.
- Before modifying a file, read it first. Do not assume files or directories exist.
- After writing or editing a file, re-read it if accuracy matters.
- If a tool call fails, analyze the error before retrying with a different approach.
- Ask for clarification when the request is ambiguous.

Reply directly with text for conversations. Only use the 'send_message_with_attachments' tool to send to a specific chat channel."""

    @staticmethod
    def _build_runtime_context(channel: str | None, chat_id: str | None) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        lines = [f"Current Time: {now} ({tz})"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    def build_messages(
        self,
        history: list[StreamDelta],
        current_message: str,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[StreamDelta]:
        """Build the complete message list as StreamDeltas.

        The returned list is the canonical representation used throughout the
        agent loop.  The user delta stores the raw user text plus the local
        media paths (in ``media``); media is only expanded into provider-format
        content (inline images / ``[attached file: …]`` refs) at the provider
        boundary via ``delta_to_openai``.
        """
        runtime_ctx = self._build_runtime_context(channel, chat_id)
        merged = f"{runtime_ctx}\n\n{current_message}" if current_message else runtime_ctx

        processed = self._process_history(history)

        return [
            StreamDelta(type="system", content=self.build_system_prompt()),
            *processed,
            StreamDelta(
                type="user",
                content=merged,
                media=list(media) if media else [],
                time=datetime.now(timezone.utc).isoformat(),
            ),
        ]

    @staticmethod
    def _process_history(history: list[StreamDelta]) -> list[StreamDelta]:
        """Process history deltas, converting special types for LLM consumption.

        ``subagent_ref`` deltas are converted into a user-role message so the
        LLM retains context about completed subagent tasks across reloads.
        The original announce content is preserved verbatim when available.
        """
        out: list[StreamDelta] = []
        for delta in history:
            if delta.type == "subagent_ref" and isinstance(delta.content, dict):
                ref = delta.content
                # Use the stored announce content if available (it already
                # contains the full task + result text the LLM originally saw).
                announce = ref.get("announce")
                if announce and isinstance(announce, str):
                    out.append(StreamDelta(type="user", content=announce))
                else:
                    status = "completed successfully" if ref.get("status") == "ok" else "failed"
                    text = f"[Subagent '{ref.get('label', 'task')}' {status}]\nTask: {ref.get('task', '')}"
                    out.append(StreamDelta(type="user", content=text))
            else:
                out.append(delta)
        return out

