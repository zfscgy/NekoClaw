import shutil
from unittest.mock import patch

import pytest

import nekoclaw.tools.terminal as terminal
from nekoclaw.config.schema import Config, ExecToolConfig, ToolsConfig


def _shell_test_config() -> Config:
    return Config(
        tools=ToolsConfig(
            restrict_to_workspace=False,
            exec=ExecToolConfig(profile_files=[]),
        )
    )


@pytest.mark.asyncio
async def test_persistent_shell_uses_powershell_on_windows_and_executes():
    if shutil.which("powershell") is None:
        pytest.skip("powershell is not available on this machine")

    with patch.object(terminal, "load_config", return_value=_shell_test_config()):
        shell = terminal.PersistentShell()
        args = shell._shell_args()
        result = await shell.execute('Write-Output "hello"')
        await shell.close()

    assert args == ["powershell", "-NoLogo", "-NoExit", "-ExecutionPolicy", "Bypass"]
    assert shell._shell_kind == "powershell"
    assert "hello" in result


@pytest.mark.asyncio
async def test_persistent_shell_uses_bash_on_non_windows_and_executes():
    if shutil.which("bash") is None:
        pytest.skip("bash is not available on this machine")

    with patch.object(terminal, "load_config", return_value=_shell_test_config()):
        shell = terminal.PersistentShell()
        args = shell._shell_args()
        result = await shell.execute("echo hello")
        await shell.close()

    assert args == ["bash", "--norc", "--noprofile"]
    assert shell._shell_kind == "bash"
    assert "hello" in result


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_persistent_shell_uses_powershell_on_windows_and_executes())
