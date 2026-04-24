"""Persistent terminal session and command execution."""

import asyncio
import os
import platform
import time
import uuid
from pathlib import Path

from nekoclaw.config.loader import load_config
from nekoclaw.security.exec_checker import check

_IS_WINDOWS = platform.system() == "Windows"
_SENTINEL_PREFIX = "__SHELL_DONE_"


def _new_sentinel() -> str:
    return f"{_SENTINEL_PREFIX}{uuid.uuid4().hex}"


class PersistentShell:
    """Long-lived shell session with guards and profile bootstrap."""

    def __init__(self, cwd: str | None = None) -> None:
        cfg = load_config()
        exec_cfg = cfg.tools.exec
        self.timeout = float(exec_cfg.timeout)
        self.restrict_to_workspace = cfg.tools.restrict_to_workspace

        self.profile_files = list(exec_cfg.profile_files)
        self.profile_commands = list(exec_cfg.profile_commands)

        self._initial_cwd = cwd or os.getcwd()
        self._current_cwd = self._initial_cwd
        env = os.environ.copy()
        if exec_cfg.path_append:
            env["PATH"] = env.get("PATH", "") + os.pathsep + exec_cfg.path_append
        self._env = env
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._stderr_buf: list[str] = []
        self._stderr_task: asyncio.Task | None = None
        self._shell_kind = "powershell" if _IS_WINDOWS else "bash"
        self._startup_errors: list[str] = []
        self._last_activity: float = 0.0

    async def start(self) -> None:
        """Start the underlying shell process if it isn't already running."""
        if self.is_alive:
            return

        shell_args = self._shell_args()

        self._process = await asyncio.create_subprocess_exec(
            *shell_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._initial_cwd,
            env=self._env,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        await self._run_profile_startup()

    async def close(self) -> None:
        """Terminate the shell and release all resources."""
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stderr_task = None

        if self._process:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                self._process.kill()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception:
                pass
            self._process = None

    async def reset(self) -> str:
        """Kill the current shell and start a fresh one with the same profile commands.

        Returns a human-readable status string. Safe to call even if the shell
        is not currently running.
        """
        async with self._lock:
            await self._hard_reset()
            self._startup_errors.clear()
            try:
                await self.start()
            except Exception as e:
                return f"Shell was terminated, but failed to restart: {e}"

            notes = list(self._startup_errors)
            self._startup_errors.clear()
            if notes:
                return (
                    "Shell session restarted with a fresh profile.\n"
                    "Startup notes:\n" + "\n".join(notes)
                )
            return "Shell session restarted with a fresh profile."

    async def execute(
        self,
        command: str,
        timeout: float | None = None,
        working_dir: str | None = None,
        output_limit: int = 10_000,
    ) -> str:
        """Run command with guard checks and formatted output.

        The special literal ``:reset`` terminates the underlying shell and
        starts a fresh one, re-running all configured profile commands.
        """
        if isinstance(command, str) and command.strip() == ":reset":
            return await self.reset()

        cwd_hint = working_dir or self._current_cwd
        guard_error = check(
            command,
            cwd_hint,
            restrict_to_workspace=self.restrict_to_workspace,
        )
        if guard_error:
            return guard_error

        full_command = self._with_working_dir(command, working_dir) if working_dir else command
        effective_timeout = self.timeout if timeout is None else timeout

        try:
            stdout, stderr, returncode = await self.run(full_command, timeout=effective_timeout)
        except Exception as e:
            return f"Error executing command: {e}"

        output_parts: list[str] = []
        if self._startup_errors:
            output_parts.append(
                "Startup warnings:\n" + "\n".join(self._startup_errors) + "\n"
            )
            self._startup_errors.clear()

        if stdout.strip():
            output_parts.append(stdout)
        if stderr.strip():
            output_parts.append(f"STDERR:\n{stderr}")
        if returncode is not None and returncode != 0:
            output_parts.append(f"\nExit code: {returncode}")

        result = "\n".join(output_parts) if output_parts else "(no output)"
        if len(result) > output_limit:
            result = result[:output_limit] + f"\n... (truncated, {len(result) - output_limit} more chars)"
        return result

    async def run(self, command: str, timeout: float = 30.0) -> tuple[str, str, int | None]:
        """Execute raw command in the persistent shell."""
        async with self._lock:
            if not self.is_alive:
                await self.start()
            return await self._run_no_lock(command, timeout)

    @property
    def is_alive(self) -> bool:
        """True while the shell subprocess is still running."""
        return self._process is not None and self._process.returncode is None

    def _shell_args(self) -> list[str]:
        if _IS_WINDOWS:
            self._shell_kind = "powershell"
            return [
                "powershell",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "-",
            ]
        self._shell_kind = "bash"
        return ["bash", "--norc", "--noprofile"]

    async def _run_profile_startup(self) -> None:
        commands = self._collect_profile_commands()
        for cmd in commands:
            stdout, stderr, returncode = await self._run_no_lock(cmd, timeout=30.0)
            if returncode not in (0, None):
                message = f"Profile command failed: {cmd}"
                if stderr.strip():
                    message += f" ({stderr.strip()})"
                self._startup_errors.append(message)
            elif stderr.strip():
                self._startup_errors.append(f"Profile warning ({cmd}): {stderr.strip()}")
            if stdout.strip():
                self._startup_errors.append(f"Profile output ({cmd}): {stdout.strip()}")

    def _collect_profile_commands(self) -> list[str]:
        """
        First run the profile files, then the profile commands
        """
        commands: list[str] = []
        for path in self.profile_files:
            path = Path(path)
            if not path.exists() or not path.is_file():
                continue
            try:
                for raw_line in path.read_text(encoding="utf-8").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    commands.append(line)
            except Exception as e:
                self._startup_errors.append(f"Unable to read profile file {path}: {e}")
        commands.extend([c for c in self.profile_commands if c.strip()])
        return commands

    def _with_working_dir(self, command: str, working_dir: str) -> str:
        self._current_cwd = working_dir
        if self._shell_kind == "powershell":
            return f'Set-Location -LiteralPath "{working_dir}"; {command}'
        return f'cd "{working_dir}" && {command}'

    def _build_payload(self, command: str, sentinel: str) -> str:
        if self._shell_kind == "powershell":
            return (
                f"{command}\n"
                "$__nb_rc = if ($?) { if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 } } "
                "else { if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 1 } }\n"
                f'Write-Output "{sentinel} $__nb_rc"\n'
            )
        return f"{command}\necho {sentinel} $?\n"

    async def _write(self, text: str) -> None:
        if self._process and self._process.stdin:
            self._process.stdin.write(text.encode())
            await self._process.stdin.drain()

    async def _run_no_lock(self, command: str, timeout: float) -> tuple[str, str, int | None]:
        self._stderr_buf.clear()
        sentinel = _new_sentinel()
        payload = self._build_payload(command, sentinel)
        self._last_activity = time.monotonic()
        await self._write(payload)

        output_lines: list[str] = []
        returncode: int | None = None
        timed_out = False

        assert self._process is not None and self._process.stdout is not None
        stdout = self._process.stdout

        while True:
            remaining = timeout - (time.monotonic() - self._last_activity)
            if remaining <= 0:
                timed_out = True
                break
            try:
                raw = await asyncio.wait_for(stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                # No stdout within the window; if stderr drainer bumped activity
                # while we were waiting, give the command another grace period.
                if time.monotonic() - self._last_activity < timeout:
                    continue
                timed_out = True
                break

            if not raw:
                break

            self._last_activity = time.monotonic()
            line = raw.decode("utf-8", errors="replace")
            if sentinel in line:
                parts = line.strip().split()
                if len(parts) >= 2:
                    try:
                        returncode = int(parts[-1])
                    except ValueError:
                        pass
                break
            output_lines.append(line)

        if timed_out:
            partial_stdout = "".join(output_lines)
            partial_stderr = "".join(self._stderr_buf)
            await self._hard_reset()
            message = (
                f"Error: Command produced no output for {timeout} seconds. "
                "It likely spawned an interactive process (e.g. python/ipython REPL) "
                "that consumed stdin, or is hung. The shell session has been restarted; "
                "non-persistent state such as variables and cwd were lost."
            )
            combined_stderr = (partial_stderr + "\n" if partial_stderr else "") + message
            return partial_stdout, combined_stderr, None

        return "".join(output_lines), "".join(self._stderr_buf), returncode

    async def _hard_reset(self) -> None:
        """Forcefully terminate the shell so the next run() starts a fresh session."""
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stderr_task = None

        if self._process:
            try:
                if self._process.stdin:
                    try:
                        self._process.stdin.close()
                    except Exception:
                        pass
                self._process.kill()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception:
                pass
            self._process = None

        self._stderr_buf.clear()
        self._current_cwd = self._initial_cwd

    async def _drain_stderr(self) -> None:
        try:
            assert self._process is not None and self._process.stderr is not None
            async for raw in self._process.stderr:
                self._last_activity = time.monotonic()
                self._stderr_buf.append(raw.decode("utf-8", errors="replace"))
        except Exception:
            pass
