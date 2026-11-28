"""GitHub REST adapter for one configured repository."""

import re
import time
from typing import Any

import aiohttp


class GitHubError(ValueError):
    def __init__(self, state: str, message: str, due: float = 0) -> None:
        super().__init__(message)
        self.state, self.due = state, due


class GitHub:
    def __init__(self, repo: str, token: str, login: str) -> None:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not login or not token:
            raise ValueError("Configure GitHub repository, token and account")
        self.repo, self.login = repo.lower(), login.lower()
        self.headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.base = "/repos/" + self.repo

    async def request(self, path: str, data: dict[str, str] | None = None) -> Any:
        uncertain = "unknown" if data is not None else "pending"
        try:
            async with aiohttp.ClientSession(
                headers=self.headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as http:
                async with http.request(
                    "POST" if data is not None else "GET",
                    "https://api.github.com" + path,
                    json=data,
                    allow_redirects=False,
                ) as r:
                    if r.status in (403, 429) and (
                        r.status == 429
                        or "Retry-After" in r.headers
                        or r.headers.get("X-RateLimit-Remaining") == "0"
                    ):
                        raise GitHubError(
                            "pending",
                            "GitHub rate limit",
                            max(
                                time.time() + 60,
                                time.time() + int(r.headers.get("Retry-After", "0")),
                                float(r.headers.get("X-RateLimit-Reset", "0")),
                            ),
                        )
                    if r.status >= 500:
                        raise GitHubError(uncertain, f"GitHub HTTP {r.status}", time.time() + 60)
                    if not 200 <= r.status < 300:
                        raise GitHubError("failed", f"GitHub HTTP {r.status}")
                    return await r.json()
        except (TimeoutError, aiohttp.ClientError, UnicodeError, ValueError) as exc:
            if isinstance(exc, GitHubError):
                raise
            raise GitHubError(uncertain, "GitHub response unavailable", time.time() + 60) from exc

    async def verify(self) -> None:
        user = await self.request("/user")
        repo = await self.request(self.base)
        if (
            user.get("login", "").lower() != self.login
            or repo.get("full_name", "").lower() != self.repo
            or not repo.get("has_issues")
        ):
            raise GitHubError("failed", "GitHub identity or repository mismatch")

    def number(self, item: Any, kind: str) -> int:
        n = item.get("number") if isinstance(item, dict) else None
        if (
            type(n) is not int
            or n <= 0
            or item.get("html_url", "").lower() != (f"https://github.com/{self.repo}/{kind}/{n}")
        ):
            raise GitHubError("unknown", "Unexpected GitHub resource")
        return n

    async def create(self, title: str, body: str) -> int:
        return self.number(
            await self.request(self.base + "/issues", {"title": title, "body": body}), "issues"
        )

    async def reconcile(self, marker: str) -> int | None:
        matches = []
        for page in range(1, 6):
            rows = await self.request(f"{self.base}/issues?state=all&per_page=100&page={page}")
            for row in rows:
                if (
                    "pull_request" not in row
                    and marker in (row.get("body") or "")
                    and row["user"]["login"].lower() == self.login
                ):
                    matches.append(self.number(row, "issues"))
            if len(rows) < 100:
                return matches[0] if len(matches) == 1 else None
        return None

    async def metadata(self, number: int, pull: bool = False) -> dict[str, object]:
        kind = "pulls" if pull else "issues"
        row = await self.request(f"{self.base}/{kind}/{number}")
        if self.number(row, "pull" if pull else "issues") != number:
            raise ValueError("Unexpected resource")
        return {
            "url": row["html_url"],
            "state": "merged" if row.get("merged") else row["state"],
            "assignees": [person["login"] for person in row["assignees"]],
        }

    async def pull_version(self, pull: int) -> tuple[str, str, str, str]:
        row = await self.request(f"{self.base}/pulls/{pull}")
        if self.number(row, "pull") != pull:
            raise ValueError("Unexpected PR")
        base, head = row["base"]["sha"], row["head"]["sha"]
        if not all(isinstance(s, str) and re.fullmatch("[0-9a-f]{40}", s) for s in (base, head)):
            raise ValueError("Invalid PR version")
        return base, head, str(row["title"]), str(row.get("body") or "")

    async def patches(self, pull: int) -> tuple[list[dict[str, object]], bool]:
        files: list[dict[str, object]] = []
        used = 0
        for page in range(1, 31):
            rows = await self.request(f"{self.base}/pulls/{pull}/files?per_page=100&page={page}")
            for row in rows:
                patch = row.get("patch")
                used += len(str(patch or "").encode())
                if used > 4_000_000:
                    return files, False
                files.append(
                    {k: row.get(k) for k in ("filename", "previous_filename", "status", "patch")}
                )
            if len(rows) < 100:
                return files, True
        return files, False

    async def checks(self, head: str) -> dict[str, object]:
        try:
            data = await self.request(f"{self.base}/commits/{head}/check-runs?per_page=100")
        except GitHubError as exc:
            if exc.state != "failed":
                raise
            return {"available": False}
        checks = [
            {k: check.get(k) for k in ("name", "status", "conclusion")}
            for check in data["check_runs"]
            if check.get("head_sha") == head
        ]
        return {"available": True, "runs": checks, "complete": len(checks) == data["total_count"]}

    async def post_review_comment(self, pull: int, body: str) -> str:
        row = await self.request(f"{self.base}/issues/{pull}/comments", {"body": body})
        return self.comment_url(row, pull)

    def comment_url(self, row: dict[str, Any], pull: int) -> str:
        url = row.get("html_url")
        if not isinstance(url, str) or not re.fullmatch(
            re.escape(f"https://github.com/{self.repo}/pull/{pull}#issuecomment-") + r"[0-9]+",
            url,
            re.I,
        ):
            raise GitHubError("unknown", "Unexpected comment receipt")
        return url

    async def find_review_comment(self, pull: int, marker: str) -> str | None:
        matches = []
        for page in range(1, 6):
            rows = await self.request(
                f"{self.base}/issues/{pull}/comments?per_page=100&page={page}"
            )
            for row in rows:
                if (row.get("body") or "").endswith(marker) and row["user"][
                    "login"
                ].lower() == self.login:
                    matches.append(self.comment_url(row, pull))
            if len(rows) < 100:
                return matches[0] if len(matches) == 1 else None
        return None
