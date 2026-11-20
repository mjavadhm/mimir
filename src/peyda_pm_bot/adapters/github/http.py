"""Fixed-repository REST adapter; only this process sees the credential."""

import asyncio
import base64
import json
import re
import time
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field

from peyda_pm_bot.application.actions.errors import GithubPermanent, GithubTemporary, GithubUnknown
from peyda_pm_bot.application.actions.rate_ports import RateLimits
from peyda_pm_bot.domain.actions.models import ActionDraft, RemoteComment, RemoteIssue, marker
from peyda_pm_bot.domain.work.models import BranchProtection, PullSummary, desired_fields


class User(BaseModel):
    login: str


class Label(BaseModel):
    name: str


class IssueWire(BaseModel):
    model_config = ConfigDict(extra="ignore")
    number: int
    title: str
    body: str | None = None
    state: str
    labels: list[Label] = Field(default_factory=list)
    assignees: list[User] = Field(default_factory=list)
    updated_at: str
    html_url: str
    pull_request: dict[str, object] | None = None
    user: User | None = None

    def domain(self) -> RemoteIssue:
        return RemoteIssue(
            self.number,
            self.title,
            self.body or "",
            self.state,
            tuple(sorted(label.name for label in self.labels)),
            tuple(sorted(user.login for user in self.assignees)),
            self.updated_at,
            self.html_url,
            self.pull_request is not None,
            self.user.login if self.user else "",
        )


class CommentWire(BaseModel):
    id: int
    body: str
    user: User
    updated_at: str
    html_url: str
    issue_url: str

    def domain(self, number: int) -> RemoteComment:
        if self.issue_url.rsplit("/", 1)[-1] != str(number):
            raise GithubPermanent("Comment belongs to another issue")
        return RemoteComment(
            self.id, number, self.body, self.user.login, self.updated_at, self.html_url
        )


class PullSummaryWire(BaseModel):
    number: int
    title: str
    html_url: str
    state: str
    draft: bool = False
    updated_at: str
    merged_at: str | None = None

    def domain(self) -> PullSummary:
        state = "merged" if self.merged_at is not None else self.state
        return PullSummary(
            self.number, self.title, self.html_url, state, self.draft, self.updated_at
        )


class BranchWire(BaseModel):
    protected: bool


class BranchRule(BaseModel):
    type: str


class LegacyProtection(BaseModel):
    required_pull_request_reviews: dict[str, object] | None = None
    required_status_checks: dict[str, object] | None = None


class HttpGithub:
    def __init__(
        self,
        repository: str,
        token: str,
        bot_login: str,
        status_labels: dict[str, str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        write_spacing: float = 1,
        rate_limits: RateLimits | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Expected a fixed owner/repository")
        if repository.split("/")[1] in {".", ".."}:
            raise ValueError("Invalid repository name")
        self.rate_limits = rate_limits
        self.repository = repository
        self.bot_login = bot_login
        self.status_labels = status_labels
        self.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            timeout=30,
            follow_redirects=False,
            transport=transport,
        )
        self.lock = asyncio.Lock()
        self.not_before = 0.0
        self.next_write = 0.0
        self.write_spacing = write_spacing
        self.verified = False
        self.cache: dict[str, tuple[str, Any]] = {}

    def path(self, suffix: str) -> str:
        return f"/repos/{self.repository}/{suffix}"

    async def _request(
        self, method: str, path: str, payload: dict[str, object] | None = None
    ) -> Any:
        async with self.lock:
            if self.rate_limits:
                deadline, next_write = await self.rate_limits.get()
                self.not_before = max(self.not_before, deadline)
                self.next_write = max(self.next_write, next_write)
            now = time.time()
            if now < self.not_before:
                raise GithubTemporary(self.not_before - now)
            if method != "GET" and now < self.next_write:
                await asyncio.sleep(self.next_write - now)
            headers = {}
            if method == "GET" and path in self.cache:
                headers["If-None-Match"] = self.cache[path][0]
            if method != "GET":
                self.next_write = time.time() + self.write_spacing
                if self.rate_limits:
                    await self.rate_limits.save(self.not_before, self.next_write)
            try:
                response = await self.client.request(method, path, json=payload, headers=headers)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                raise GithubTemporary(1) from None
            except httpx.TransportError:
                if method == "GET":
                    raise GithubTemporary(1) from None
                raise GithubUnknown("GitHub write outcome uncertain") from None
            if response.status_code == 304 and path in self.cache:
                return self.cache[path][1]
            if response.status_code in {403, 429} and (
                response.headers.get("retry-after")
                or response.headers.get("x-ratelimit-remaining") == "0"
                or response.status_code == 429
                or "secondary rate limit" in response.text.lower()
            ):
                wait = max(
                    float(response.headers.get("retry-after", "60")),
                    float(response.headers.get("x-ratelimit-reset", "0")) - time.time(),
                )
                self.not_before = time.time() + max(wait, 1)
                if self.rate_limits:
                    await self.rate_limits.save(self.not_before, self.next_write)
                raise GithubTemporary(max(wait, 1))
            if response.status_code >= 500:
                if method == "GET":
                    raise GithubTemporary(1)
                raise GithubUnknown("GitHub server write outcome uncertain")
            if response.is_error or response.is_redirect:
                raise GithubPermanent(f"GitHub rejected request: HTTP {response.status_code}")
            try:
                value = response.json()
            except ValueError:
                if method != "GET":
                    raise GithubUnknown("Invalid GitHub write response") from None
                raise GithubPermanent("Invalid GitHub response") from None
            if method == "GET" and response.headers.get("etag"):
                self.cache[path] = (response.headers["etag"], value)
            elif method != "GET":
                self.cache.clear()
            return value

    async def _pages(self, suffix: str) -> tuple[list[Any], bool]:
        items = []
        separator = "&" if "?" in suffix else "?"
        for page in range(1, 6):
            result = await self._request(
                "GET", self.path(f"{suffix}{separator}per_page=100&page={page}")
            )
            if not isinstance(result, list):
                raise GithubPermanent("Expected paginated collection")
            items.extend(result)
            if len(result) < 100:
                return items, True
        return items, False

    async def issue(self, number: int) -> RemoteIssue:
        if number <= 0:
            raise ValueError("Invalid issue number")
        return IssueWire.model_validate(
            await self._request("GET", self.path(f"issues/{number}"))
        ).domain()

    async def comment(self, issue_number: int, comment_id: int) -> RemoteComment:
        return CommentWire.model_validate(
            await self._request("GET", self.path(f"issues/comments/{comment_id}"))
        ).domain(issue_number)

    async def issues(self, query: str = "") -> tuple[list[RemoteIssue], bool]:
        values, complete = await self._pages("issues?state=all&sort=updated&direction=desc")
        issues = [IssueWire.model_validate(value).domain() for value in values]
        return [
            issue
            for issue in issues
            if query.casefold() in (issue.title + " " + issue.body).casefold()
        ], complete

    async def comments(self, issue_number: int) -> tuple[list[RemoteComment], bool]:
        values, complete = await self._pages(f"issues/{issue_number}/comments")
        return [
            CommentWire.model_validate(value).domain(issue_number) for value in values
        ], complete

    async def labels(self) -> tuple[list[str], bool]:
        values, complete = await self._pages("labels")
        return [Label.model_validate(value).name for value in values], complete

    async def assignees(self) -> tuple[list[str], bool]:
        values, complete = await self._pages("assignees")
        return [User.model_validate(value).login for value in values], complete

    async def pull_summaries(self) -> tuple[list[PullSummary], bool]:
        values, complete = await self._pages("pulls?state=all&sort=updated&direction=desc")
        return [PullSummaryWire.model_validate(value).domain() for value in values], complete

    async def branch_protection(self) -> BranchProtection:
        branch = BranchWire.model_validate(await self._request("GET", self.path("branches/main")))
        if not branch.protected:
            return BranchProtection(False, False, False)
        rules, complete = await self._branch_rules()
        try:
            legacy = LegacyProtection.model_validate(
                await self._request("GET", self.path("branches/main/protection"))
            )
        except GithubPermanent:
            # A missing or unreadable legacy endpoint cannot establish a negative rule result.
            legacy = None
        details_known = complete and legacy is not None
        requires_pull = "pull_request" in rules or bool(
            legacy and legacy.required_pull_request_reviews is not None
        )
        requires_checks = "required_status_checks" in rules or bool(
            legacy and legacy.required_status_checks is not None
        )
        return BranchProtection(
            True,
            requires_pull if requires_pull or details_known else None,
            requires_checks if requires_checks or details_known else None,
        )

    async def _branch_rules(self) -> tuple[set[str], bool]:
        try:
            values, complete = await self._pages("rules/branches/main")
        except GithubPermanent:
            return set(), False
        return {BranchRule.model_validate(value).type for value in values}, complete

    async def mutate(self, draft: ActionDraft) -> RemoteIssue | RemoteComment:
        if not self.verified:
            user = User.model_validate(await self._request("GET", "/user"))
            if user.login.casefold() != self.bot_login.casefold():
                raise GithubPermanent("Authenticated identity does not match configured bot author")
            self.verified = True
        spec = draft.spec
        tag = marker(draft.id)
        if spec.kind == "create_issue":
            empty = RemoteIssue(0, "", "", "open", (), (), "", "")
            fields = desired_fields(spec, empty, self.status_labels)
            fields["body"] = (spec.body or "") + "\n\n" + tag
            result = await self._request("POST", self.path("issues"), fields)
            try:
                return IssueWire.model_validate(result).domain()
            except ValueError:
                raise GithubUnknown("Invalid issue creation response") from None
        assert spec.issue_number is not None
        if spec.kind == "update_issue":
            if not isinstance(draft.base, RemoteIssue):
                raise GithubPermanent("Missing issue base")
            fields = desired_fields(spec, draft.base, self.status_labels)
            fields["body"] = str(fields.get("body", draft.base.body)) + "\n\n" + tag
            result = await self._request("PATCH", self.path(f"issues/{spec.issue_number}"), fields)
            try:
                return IssueWire.model_validate(result).domain()
            except ValueError:
                raise GithubUnknown("Invalid issue update response") from None
        body = (spec.body or "") + "\n\n" + tag
        if spec.kind == "add_comment":
            result = await self._request(
                "POST", self.path(f"issues/{spec.issue_number}/comments"), {"body": body}
            )
        else:
            result = await self._request(
                "PATCH", self.path(f"issues/comments/{spec.comment_id}"), {"body": body}
            )
        try:
            return CommentWire.model_validate(result).domain(spec.issue_number)
        except (ValueError, GithubPermanent):
            raise GithubUnknown("Invalid comment write response") from None

    async def reads(
        self,
        resource: str,
        number: int = 0,
        path: str = "",
        ref: str = "",
        offset: int = 0,
        limit: int = 8000,
    ) -> str:
        if offset < 0 or not 1 <= limit <= 10000:
            raise ValueError("Invalid read window")
        if resource == "pulls":
            values, complete = await self._pages("pulls?state=all")
            fields = (
                "number",
                "title",
                "body",
                "state",
                "html_url",
                "updated_at",
                "draft",
                "head",
                "base",
            )
            # Return metadata, never review findings or patches.
            return json.dumps(
                {
                    "items": [
                        {key: value.get(key) for key in fields if key not in {"head", "base"}}
                        | {
                            "head_sha": value.get("head", {}).get("sha"),
                            "base_sha": value.get("base", {}).get("sha"),
                        }
                        for value in values
                    ],
                    "complete": complete,
                }
            )
        if resource == "pull":
            value = await self._request("GET", self.path(f"pulls/{number}"))
            return json.dumps(
                {
                    key: value.get(key)
                    for key in (
                        "number",
                        "title",
                        "body",
                        "state",
                        "html_url",
                        "updated_at",
                        "merged",
                    )
                }
            )
        if resource == "pull_paths":
            values, complete = await self._pages(f"pulls/{number}/files")
            return json.dumps(
                {
                    "items": [
                        {"path": value["filename"], "status": value["status"]} for value in values
                    ],
                    "complete": complete,
                }
            )
        if resource not in {"tree", "file"}:
            raise ValueError("Unknown read resource")
        if not ref:
            repo = await self._request("GET", f"/repos/{self.repository}")
            commit = await self._request(
                "GET", self.path("commits/" + quote(repo["default_branch"], safe=""))
            )
            ref = commit["sha"]
        if not re.fullmatch("[0-9a-fA-F]{40}", ref):
            raise ValueError("A full commit SHA is required")
        if resource == "tree":
            value = await self._request("GET", self.path(f"git/trees/{ref}?recursive=1"))
            paths = [
                item["path"]
                for item in value["tree"]
                if item["type"] == "blob" and self._allowed_path(item["path"])
            ]
            return json.dumps(
                {
                    "ref": ref,
                    "paths": paths[offset : offset + min(limit, 100)],
                    "next_offset": offset + min(limit, 100)
                    if offset + min(limit, 100) < len(paths)
                    else None,
                    "complete": not value.get("truncated", False),
                    "tree_truncated": bool(value.get("truncated", False)),
                }
            )
        if not self._allowed_path(path):
            raise ValueError("Code path not allowed")
        # Resolve the tree entry first: Contents API may otherwise dereference a symlink.
        tree = await self._request("GET", self.path(f"git/trees/{ref}?recursive=1"))
        entry = next((item for item in tree["tree"] if item["path"] == path), None)
        if (
            entry is None
            or entry.get("mode") in {"120000", "160000"}
            or entry.get("type") != "blob"
        ):
            raise ValueError("Missing, symlinked or submodule file; tree may be incomplete")
        if entry.get("size", 0) > 100000:
            raise ValueError("File exceeds bounded code-read size")
        value = await self._request("GET", self.path(f"git/blobs/{entry['sha']}"))
        content = base64.b64decode(value["content"], validate=False)
        if len(content) > 100000:
            raise ValueError("File exceeds limit")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Only UTF-8 text files are supported") from None
        return json.dumps(
            {
                "ref": ref,
                "path": path,
                "text": text[offset : offset + limit],
                "next_offset": offset + limit if offset + limit < len(text) else None,
                "complete": offset + limit >= len(text),
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _allowed_path(path: str) -> bool:
        value = PurePosixPath(path)
        return (
            bool(path)
            and not value.is_absolute()
            and ".." not in value.parts
            and not any(
                part.startswith(".env")
                or part in {".git", ".ssh", ".aws", ".codex", ".agents"}
                or part.endswith((".pem", ".key", ".db", ".sqlite"))
                for part in value.parts
            )
        )

    async def close(self) -> None:
        await self.client.aclose()
