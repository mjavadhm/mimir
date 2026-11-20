"""Project text files exposed to the agent as a typed create-only operation."""

import asyncio
import logging
import os
import secrets
from pathlib import Path, PurePosixPath
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from peyda_pm_bot.application.agent.turns import authorize_turn
from peyda_pm_bot.application.ports import UowFactory

logger = logging.getLogger(__name__)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def create_text_file(root: Path, path: str, content: str) -> str:
    relative = PurePosixPath(path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part.startswith(".") for part in relative.parts)
    ):
        raise ValueError("Expected a visible path relative to the project root")
    data = content.encode("utf-8")
    if len(data) > 128_000 or "\0" in path or "\0" in content:
        raise ValueError("Invalid text file content or path")

    directory = os.open(root, DIRECTORY_FLAGS)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=directory)
            os.close(directory)
            directory = child
        name = relative.name
        temporary = f".peyda-new-{secrets.token_hex(16)}"
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=directory
        )
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            os.unlink(temporary, dir_fd=directory)
    finally:
        os.close(directory)
    return relative.as_posix()


def register_project_files(server: FastMCP, uow: UowFactory, project_id: str, root: Path) -> None:
    @server.tool()
    async def project_create_file(
        turn_token: str,
        path: Annotated[str, Field(min_length=1, max_length=512)],
        content: Annotated[str, Field(max_length=32_000)],
    ) -> dict[str, str]:
        """Create a new UTF-8 text file under an existing project directory. Turbo must be on."""
        async with uow() as unit:
            turn = await authorize_turn(unit, project_id, turn_token)
            if not (await unit.queue.state()).turbo_effective:
                raise ValueError("Project file tools require Turbo")
        try:
            created = await asyncio.to_thread(create_text_file, root, path, content)
        except FileExistsError:
            logger.info(
                "project file creation skipped job_id=%s reason=already_exists", turn.job_id
            )
            return {"status": "already_exists"}
        except (OSError, ValueError):
            logger.info("project file creation rejected job_id=%s", turn.job_id)
            return {"status": "rejected"}
        logger.info("project file created job_id=%s", turn.job_id)
        return {"status": "created", "path": created}
