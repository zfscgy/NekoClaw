"""Interactive debug harness for :class:`nekoclaw.agent.tools.shell.ExecTool`.

运行方式::

    python -m tests.debug.exec
    # 或
    python tests/debug/exec.py

进入 REPL 后直接输入 shell 命令即可通过 ``ExecTool`` 执行，
命令会在同一个持久化 shell 会话中运行（``cd``、激活的 venv 等状态会保留）。

特殊指令（以 ``:`` 开头，不会传给 shell）：

    :cd <path>     临时切到指定目录再执行（同 ``working_dir`` 参数）
    :cwd <path>    把后续所有命令的默认 working_dir 固定到指定目录
    :reset         关闭当前 shell 会话并新建一个
    :info          打印当前 ExecTool 的 schema / 状态
    :help / ?      显示帮助
    :quit / :exit  退出


测试关键点：
- 能否正常执行命令
- 执行ls，查看中文目录名
- 执行cd 中文目录名
- Python返回/STDERR是否有乱码  
    - python -c "print('你好')"   
    - python -c "你好"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import traceback
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from nekoclaw.agent.tools.shell import ExecTool  # noqa: E402


_HELP_TEXT = """\
Commands:
  <any shell command>   run via ExecTool in a persistent session
  :cd <path>            run the *next* command with working_dir=<path>
  :cwd <path>           set a sticky working_dir for all following commands
  :cwd                  clear the sticky working_dir
  :reset                close the shell and start a fresh one
  :info                 show tool metadata and current state
  :help / ?             show this message
  :quit / :exit / Ctrl-D / Ctrl-C   exit the REPL
"""


async def interactive(working_dir: str | None = None) -> None:
    """Run an interactive REPL bound to a single :class:`ExecTool` instance."""
    tool = ExecTool(working_dir=working_dir)
    sticky_cwd: str | None = None
    one_shot_cwd: str | None = None

    print(f"[exec-debug] ExecTool ready (initial cwd={tool.working_dir or os.getcwd()!r})")
    print("[exec-debug] type ':help' for commands, ':quit' to exit.")

    loop = asyncio.get_running_loop()

    try:
        while True:
            prompt_cwd = one_shot_cwd or sticky_cwd or tool.working_dir or os.getcwd()
            prompt = f"exec [{prompt_cwd}]> "
            try:
                line = await loop.run_in_executor(None, input, prompt)
            except (EOFError, KeyboardInterrupt):
                print()
                break

            command = line.strip()
            if not command:
                continue

            if command in (":quit", ":exit"):
                break
            if command in (":help", "?"):
                print(_HELP_TEXT)
                continue
            if command == ":info":
                print(f"  name:        {tool.name}")
                print(f"  description: {tool.description}")
                print(f"  parameters:  {tool.parameters}")
                print(f"  working_dir: {tool.working_dir}")
                print(f"  sticky cwd:  {sticky_cwd}")
                continue
            if command == ":reset":
                await tool.close()
                tool = ExecTool(working_dir=working_dir)
                sticky_cwd = None
                one_shot_cwd = None
                print("[exec-debug] shell restarted.")
                continue
            if command.startswith(":cwd"):
                _, _, rest = command.partition(" ")
                sticky_cwd = rest.strip() or None
                print(f"[exec-debug] sticky working_dir = {sticky_cwd!r}")
                continue
            if command.startswith(":cd "):
                one_shot_cwd = command[4:].strip() or None
                continue

            effective_cwd = one_shot_cwd or sticky_cwd
            one_shot_cwd = None

            try:
                result = await tool.execute(command=command, working_dir=effective_cwd)
            except Exception:
                traceback.print_exc()
                continue

            if not result.endswith("\n"):
                result += "\n"
            sys.stdout.write(result)
            sys.stdout.flush()
    finally:
        await tool.close()
        print("[exec-debug] bye.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive REPL for ExecTool")
    parser.add_argument(
        "--cwd",
        default=None,
        help="Initial working directory for the persistent shell.",
    )
    args = parser.parse_args()
    asyncio.run(interactive(working_dir=args.cwd))


if __name__ == "__main__":
    main()
