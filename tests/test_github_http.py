"""Actual HTTP adapter through MockTransport, never a GitHub account."""

import json

import httpx
import pytest

from peyda_pm_bot.adapters.github.http import HttpGithub
from peyda_pm_bot.application.actions.errors import GithubPermanent, GithubTemporary, GithubUnknown
from peyda_pm_bot.domain.actions.models import ActionDraft, ActionSpec, marker
from peyda_pm_bot.domain.work.models import STATUSES

LABELS = {state: f"pm:{state}" for state in STATUSES}


def issue(body="body", **changes):
    value = {
        "number": 1,
        "title": "Title",
        "body": body,
        "state": "open",
        "labels": [],
        "assignees": [],
        "updated_at": "2026-11-18T00:00:00Z",
        "html_url": "https://github.com/example/project/issues/1",
    }
    return value | changes


async def test_headers_scope_and_ambiguous_post():
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == "Bearer FAKE_TEST_TOKEN"
        assert request.headers["x-github-api-version"] == "2026-03-10"
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "pm-bot"})
        if request.method == "POST":
            raise httpx.ReadTimeout("response lost")
        return httpx.Response(200, json=issue())

    github = HttpGithub(
        "example/project",
        "FAKE_TEST_TOKEN",
        "pm-bot",
        LABELS,
        transport=httpx.MockTransport(handle),
        write_spacing=0,
    )
    try:
        assert (await github.issue(1)).number == 1
        with pytest.raises(GithubUnknown):
            await github.mutate(
                ActionDraft("id", 1, ActionSpec("create_issue", (10,), title="Title", body="body"))
            )
        assert requests[-1].url.path == "/repos/example/project/issues"
        assert marker("id") in json.loads(requests[-1].content)["body"]
    finally:
        await github.close()


@pytest.mark.parametrize(
    "status,headers,error",
    [
        (403, {}, GithubPermanent),
        (429, {"retry-after": "7"}, GithubTemporary),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"}, GithubTemporary),
        (500, {}, GithubUnknown),
    ],
)
async def test_error_classification(status, headers, error):
    def handle(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "pm-bot"})
        return httpx.Response(status, headers=headers, json={"message": "error"})

    github = HttpGithub(
        "example/project",
        "FAKE",
        "pm-bot",
        LABELS,
        transport=httpx.MockTransport(handle),
        write_spacing=0,
    )
    try:
        with pytest.raises(error):
            await github.mutate(
                ActionDraft("id", 1, ActionSpec("create_issue", (10,), title="T", body="B"))
            )
    finally:
        await github.close()


async def test_get_etag_and_incomplete_pagination():
    requests = []

    def handle(request):
        requests.append(request)
        if "/issues/1" in request.url.path:
            if request.headers.get("if-none-match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(200, headers={"etag": '"v1"'}, json=issue())
        return httpx.Response(200, json=[issue(number=index) for index in range(100)])

    github = HttpGithub(
        "example/project", "FAKE", "pm-bot", LABELS, transport=httpx.MockTransport(handle)
    )
    try:
        assert await github.issue(1) == await github.issue(1)
        assert requests[-1].headers["if-none-match"] == '"v1"'
        items, complete = await github.issues()
        assert len(items) == 500 and not complete
    finally:
        await github.close()


async def test_auth_identity_mismatch_prevents_writes():
    writes = []

    def handle(request):
        if request.method != "GET":
            writes.append(request)
        return httpx.Response(200, json={"login": "somebody-else"})

    github = HttpGithub(
        "example/project", "FAKE", "pm-bot", LABELS, transport=httpx.MockTransport(handle)
    )
    try:
        with pytest.raises(GithubPermanent, match="identity"):
            await github.mutate(
                ActionDraft("id", 1, ActionSpec("create_issue", (10,), title="T", body="B"))
            )
        assert not writes
    finally:
        await github.close()


@pytest.mark.parametrize("repository", ["../evil", "owner/..", "owner/repo?x=1", "other/repo/path"])
def test_repository_path_cannot_escape(repository):
    with pytest.raises(ValueError):
        HttpGithub(repository, "FAKE", "pm-bot", LABELS)


async def test_code_file_paging_and_symlink_denial():
    import base64

    sha = "a" * 40

    def handle(request):
        if "/git/trees/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {
                            "path": "src/main.py",
                            "sha": "b" * 40,
                            "mode": "100644",
                            "type": "blob",
                            "size": 20,
                        },
                        {
                            "path": "link",
                            "sha": "c" * 40,
                            "mode": "120000",
                            "type": "blob",
                            "size": 10,
                        },
                    ],
                    "truncated": False,
                },
            )
        return httpx.Response(
            200, json={"content": base64.b64encode(b"0123456789").decode(), "encoding": "base64"}
        )

    github = HttpGithub(
        "example/project", "FAKE", "pm-bot", LABELS, transport=httpx.MockTransport(handle)
    )
    try:
        page = json.loads(
            await github.reads("file", path="src/main.py", ref=sha, offset=3, limit=4)
        )
        assert page["text"] == "3456" and page["next_offset"] == 7
        with pytest.raises(ValueError):
            await github.reads("file", path="link", ref=sha)
        with pytest.raises(ValueError):
            await github.reads("file", path="../secret", ref=sha)
        with pytest.raises(ValueError):
            await github.reads("file", path=".env", ref=sha)
    finally:
        await github.close()


async def test_rate_deadline_survives_client_recreation(database):
    from peyda_pm_bot.adapters.github.rate_limits import PersistentLimits

    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "60"}, json={"message": "rate limited"})

    for _attempt in range(2):
        github = HttpGithub(
            "example/project",
            "FAKE",
            "pm-bot",
            LABELS,
            transport=httpx.MockTransport(handle),
            rate_limits=PersistentLimits(database.unit),
        )
        try:
            with pytest.raises(GithubTemporary):
                await github.issue(1)
        finally:
            await github.close()
    assert len(calls) == 1
