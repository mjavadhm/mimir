"""Run delegated review sessions independently from management turns."""

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mimir_pm_bot.adapters.acp import ACPError, Agent
from mimir_pm_bot.adapters.github import GitHub, GitHubError
from mimir_pm_bot.adapters.review_store import ReviewStore


class ReviewWorker:
    def __init__(
        self,
        store: ReviewStore,
        github: GitHub,
        directory: Path,
        agent: Callable[[Path, str], Agent],
        mcp: list[dict[str, Any]],
        context: Callable[[int], list[dict[str, Any]]],
    ) -> None:
        self.store, self.github, self.directory = store, github, directory
        self.agent, self.mcp, self.context = agent, mcp, context

    async def context_matches(self, row: sqlite3.Row) -> bool:
        ident = row["review"] if "review" in row.keys() else row["id"]
        snapshot = self.directory / str(ident) / (row["base"] + "-" + row["head"]) / "snapshot.json"

        def matches() -> bool:
            return bool(
                json.loads(snapshot.read_text(encoding="utf-8"))["linked_issues"]
                == self.context(row["pull"])
            )

        return await asyncio.to_thread(matches)

    async def jobs(self, interval: float) -> None:
        while True:
            row = await asyncio.to_thread(self.store.next_job)
            if row:
                try:
                    await self.review(row)
                except (ACPError, OSError, ValueError, KeyError, TypeError) as exc:
                    await asyncio.to_thread(
                        self.store.retry,
                        row,
                        type(exc).__name__,
                        max(time.time() + 15, getattr(exc, "due", 0)),
                        permanent=isinstance(exc, GitHubError) and exc.state == "failed",
                    )
            await asyncio.sleep(interval)

    async def review(self, row: sqlite3.Row) -> None:
        ident, pull = row["id"], row["pull"]
        base, head, title, description = await self.github.pull_version(pull)
        if row["base"] and (base, head) != (row["base"], row["head"]):
            await asyncio.to_thread(self.store.retry, row, "PR version changed", 0, reset=True)
            return
        if row["state"] == "ready":
            if not await self.context_matches(row):
                await asyncio.to_thread(
                    self.store.retry, row, "Issue context changed", 0, reset=True
                )
                return
            await asyncio.to_thread(self.store.complete, row)
            return
        files, complete = await self.github.patches(pull)
        if (await self.github.pull_version(pull))[:2] != (base, head):
            await asyncio.to_thread(
                self.store.retry, row, "PR changed during snapshot", 0, reset=True
            )
            return
        directory = self.directory / str(ident) / (base + "-" + head)
        evidence = dict(
            repository=self.github.repo,
            pull=pull,
            base=base,
            head=head,
            title=title,
            description=description,
            request=json.loads(row["request"]),
            linked_issues=await asyncio.to_thread(self.context, pull),
            files=files,
            complete_file_list=complete,
            ci_evidence=await self.github.checks(head),
        )

        def snapshot() -> None:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            temp = directory / "snapshot.tmp"
            temp.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
            temp.replace(directory / "snapshot.json")

        await asyncio.to_thread(snapshot)
        await asyncio.to_thread(self.store.checkpoint, ident, base, head, row["session"])
        agent = self.agent(directory, row["session"] or "")
        agent.review_output = True
        try:
            await agent.start(mcp=self.mcp)
            await asyncio.to_thread(self.store.checkpoint, ident, base, head, agent.session)
            if not await asyncio.to_thread(self.store.running, ident):
                return
            prompt = (
                row["prompt"]
                + "\n"
                + json.dumps(
                    {
                        k: v
                        for k, v in evidence.items()
                        if k not in ("description", "files", "linked_issues", "ci_evidence")
                    },
                    ensure_ascii=False,
                )
            )
            task = asyncio.create_task(agent.prompt(prompt))
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=1)
                    if not await asyncio.to_thread(self.store.running, ident):
                        await agent.cancel()
                        return
                if await task != "end_turn" or agent.overflow or not agent.text.strip():
                    raise ACPError("Reviewer returned an incomplete response")
                await asyncio.to_thread(self.store.save, ident, agent.text.strip())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            await agent.close()

    async def publications(self, interval: float) -> None:
        while True:
            row = await asyncio.to_thread(self.store.next_action)
            if row:
                try:
                    await self.publish(row)
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    # A lost POST receipt can only be resolved through a read.
                    state = (
                        "unknown" if row["state"] == "unknown" else getattr(exc, "state", "unknown")
                    )
                    await asyncio.to_thread(
                        self.store.settle,
                        row,
                        state,
                        "GitHub comment delivery needs verification."
                        if state in ("unknown", "failed")
                        else "",
                        due=max(time.time() + 60, getattr(exc, "due", 0)),
                    )
            await asyncio.sleep(interval)

    async def publish(self, row: sqlite3.Row) -> None:
        async def settle(state: str, message: str, url: str | None = None, due: float = 0) -> None:
            await asyncio.to_thread(self.store.settle, row, state, message, url, due)

        pull = row["pull"]
        if row["state"] == "unknown":
            url = await self.github.find_review_comment(pull, row["marker"])
            if url:
                await settle("posted", url, url)
            else:
                await settle("unknown", "", due=time.time() + 300)
            return
        if row["review_state"] != "done" or (await self.github.pull_version(pull))[:2] != (
            row["base"],
            row["head"],
        ):
            await settle("stale", "This review cannot be published. Please request a fresh review.")
            return
        if not await self.context_matches(row):
            await settle("stale", "Issue context changed. Please request a fresh review.")
            return
        outcome = await asyncio.to_thread(self.store.begin_post, row)
        if outcome == "send":
            body = f"Review of `{row['head']}`\n\n{row['result']}\n\n{row['marker']}"
            url = await self.github.post_review_comment(pull, body)
            await settle("posted", url, url)
        elif outcome.startswith("https://"):
            await settle("posted", outcome, outcome)
        else:
            await settle("blocked", "Publication deferred: " + outcome)
