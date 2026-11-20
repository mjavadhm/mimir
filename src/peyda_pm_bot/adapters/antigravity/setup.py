"""Run the regular Antigravity CLI against a bound project conversation."""

import asyncio
import json
import shutil
from pathlib import Path

from peyda_pm_bot.adapters.antigravity.acp_adapter import AcpAgent
from peyda_pm_bot.runtime.process import ProcessOwner


class AgentSetupError(ValueError):
    """An actionable, local configuration error safe to show on the CLI."""


async def live_agent(
    binary: Path,
    workspace: Path | None,
    conversation_id: str | None,
    *,
    desktop_keyring: bool = False,
    http_mcp: bool = False,
    project_tools: bool = False,
) -> AcpAgent:
    """Reuse the usual login and workspace with per-process tool permissions."""
    if workspace is None or conversation_id is None:
        raise AgentSetupError("Set PEYDA_AGENT_WORKSPACE_PATH and PEYDA_AGENT_CONVERSATION_ID.")
    workspace = await asyncio.to_thread(workspace.resolve)
    if not await asyncio.to_thread(workspace.is_dir):
        raise AgentSetupError("PEYDA_AGENT_WORKSPACE_PATH must be an existing project directory.")
    # Preserve explicit paths while also accepting a command on PATH.
    executable = await asyncio.to_thread(shutil.which, str(binary))
    if executable is None:
        raise AgentSetupError("Install agy-cli on PATH or set PEYDA_ANTIGRAVITY_CLI_PATH.")
    executable = str(await asyncio.to_thread(Path(executable).resolve))
    process = await asyncio.create_subprocess_exec(
        executable,
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(10):
            version, _ = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode != 0 or version.strip() != b"1.5.10":
        raise AgentSetupError("The installed CLI version needs ACP permission validation.")

    # These overrides belong only to the Telegram bridge. Leave the user's CLI
    # settings, default profile, and its authentication store where they are.
    permissions = json.dumps(
        {
            "agentMode": "",
            "toolPermission": "request-review",
            "allowNonWorkspaceAccess": False,
            "permissions": {
                "deny": [
                    "command(*)",
                    "unsandboxed(*)",
                    "write_file(*)",
                    "read_url(*)",
                    "execute_url(*)",
                ],
                "ask": ["read_file(*)", "mcp(*)"],
                "allow": [],
            },
        }
    )
    command = [
        executable,
        f"--settings={permissions}",
        "acp",
        "--disable-telemetry",
        "--include-default-customizations=false",
        "--sandbox",
    ]
    if http_mcp:
        command.append("--process-per-session")
    return AcpAgent(
        ProcessOwner(command, workspace, desktop_keyring=desktop_keyring),
        workspace,
        validated_environment=True,
        conversation_id=conversation_id,
        project_tools=project_tools,
    )
