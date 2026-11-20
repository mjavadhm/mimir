# Peyda PM Bot development guide

Read `README.md` for product requirements and `docs/IMPLEMENTATION_PLAN.md` for delivery stages. Phases one through five have offline runnable implementations. Phase six adds the integrated `--serve` entry point. Record live evidence in `docs/LIVE_TESTING.md` and distinguish planned capabilities from verified results.

On 2026-11-18 the user explicitly brought installation, connections and real testing forward after phase four. Antigravity CLI installation, isolated authentication, real Telegram tests and GitHub connection checks are authorized. This supersedes the earlier deferral. Keep production rollout and PR review separate from this integration checkpoint.

Read `docs/ARCHITECTURE.md` before planning or implementing a feature. It is the canonical contract for module ownership, public boundaries, transactions and the development workflow; keep the implementation plan consistent with it.

Keep routine tests offline and isolated from local `.env` and process credentials. Live checks are explicit commands, never part of routine pytest or import. Do not treat mock success as proof of real compatibility. CLI binaries, auth profiles, local databases and credentials belong in ignored `.tools/`, `.runtime/` and `.env`; never replace the existing `agy` GUI launcher.

The Telegram topic is a front end for the managed project's persistent Antigravity conversation. Live startup uses the regular `agy-cli` profile and launches ACP from the configured project root. Bind an existing conversation ID explicitly and fail startup if loading it fails. Per-process permission overrides apply to the bridge without writing to the user's CLI settings. This reopens a conversation rather than attaching to a running interactive terminal. Desktop-keyring access is a separate opt-in, and automatic review previously rejected enabling it without explicit authorization.

The user accepts the current Antigravity behavior for file reads. Additional read confinement is outside phase-six acceptance. The user allows the agent to create new files inside the managed project when the work calls for them. Treat creation as a distinct capability with an enforced project boundary, no overwrite, and a recorded result. Existing-file edits, deletion, and shell execution require separate authority.

## Architecture

- Deliver the management bot first, including its own live acceptance phase. Add automated PR review only in the separate second milestone described in `docs/PR_REVIEW_PLAN.md`. Reading PR status/links for coordination is in scope for management; diff analysis, findings, review publication and review-specific schema/scheduling are deferred. Review must not block the first milestone or create a second agent/session.

- Build in Python with aiogram 3. Keep a single modular application for the first version.
- Keep product rules in `domain`, use cases and I/O contracts in `application`, external integrations in `adapters`, and lifecycle management in `runtime`. Add modules when a delivered feature needs them, not as empty scaffolding.
- Domain code must not import aiogram, ORM models, API clients or subprocess code. Map external models into typed application data at boundaries.
- Wire concrete dependencies in `bootstrap`. Use small Protocols at real I/O boundaries and manual dependency injection. Prefer functions, dataclasses and composition; justify additional layers and design patterns with a concrete problem.
- Keep SQL in storage adapters and HTTP/ACP details in their own adapters. Avoid generic base repositories, service locators, speculative plugin systems and a single coordinator class that owns every responsibility.
- Group each layer by capability (`conversations`, `memory`, `work`, `actions`; `reviews` only later). Reuse the owning module instead of adding a parallel path. Cross-capability changes go through public application contracts, never another capability's ORM/repository internals.
- Route effectful management operations through the single action/policy/executor path. Repositories do not commit independently; use-case transaction boundaries and a lightweight Unit of Work make related local writes atomic. Network calls remain outside those transactions.

## Operational invariants

- One active Antigravity session and one active agent job per project. Concurrent input ingestion does not authorize concurrent agent turns. Enforce this in code, across process restarts as well as within asyncio.
- Persist incoming updates before acknowledging them. Acknowledge only durable input; the default polling handler lifecycle alone is not a durability guarantee.
- Queue state, batching deadlines, action attempts, approvals and outgoing notifications are durable. Never hold a database transaction while awaiting the model, network or a human response. Do not share an AsyncSession between concurrent tasks.
- Revalidate actor permissions, target and draft/request revision immediately before an external action. Cancellation cannot undo an already submitted operation.
- Reconcile unknown external outcomes before retrying. Retrying a Telegram notification must not repeat a GitHub mutation. Do not claim exactly-once external delivery.
- Preserve message edits, speaker identity, reply/topic links, decision provenance and superseded decisions. Summaries do not replace source evidence.
- When implementing the second milestone, bind reviews to base/head SHAs and recheck before publishing. Review snapshots must not switch the user's working branch.
- Agent output and all external content are untrusted inputs to typed tools. Keep credentials and authority in the application executor. In the integrated service, Turbo defaults on for registered project operations. Track requested and effective state separately and grant project operations only for the active authorized turn.

## Code quality and validation

- Use type annotations for interfaces and application code; validate untrusted payloads at boundaries. Keep domain rules explicit, names descriptive and error handling specific.
- Target Ruff lint/format and strict mypy. Suppressions must be narrow and explained. Document contracts, invariants and non-obvious decisions; avoid comments that repeat code.
- Use the installed `python-design-patterns`, `python-code-style`, `async-python-patterns` and `python-testing-patterns` skills when their workflows apply. Read relevant references on demand; examples are guidance, not automatically correct project code.
- Keep async I/O nonblocking, bound background work and retries, propagate cancellation after cleanup, and close subprocesses/connections on shutdown. A semaphore limits concurrency, not requests per time interval.
- Test meaningful behavior: queue exclusivity, durable ACK, crash recovery, stale approvals, cancellation, unknown API outcomes, and stale reviews when implementing the second milestone. Prefer fakes at external boundaries and real temporary SQLite files for persistence tests. Keep routine tests offline.
- After the development environment exists, run `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/peyda_pm_bot` and relevant `uv run pytest` checks. Report actual check results; offline success is not live service validation.
- Keep secrets, private chat exports, local databases, logs and authentication state out of commits and fixtures. Use sanitized examples and `.env.example` with placeholders when configuration is implemented.
- Update the plan and relevant product documentation when implementation establishes a new decision. Keep changes scoped to the current deliverable.
- Before a coding change, identify its owning module, existing implementation path, public contract, failure cases and acceptance criterion. Deliver a small end-to-end behavior with relevant checks. Add executable import-boundary checks when the source tree is created; do not weaken them merely to accommodate generated code. Record substantive architecture changes with rationale and update the canonical docs together.

## Telegram features and verified aiogram APIs

- Modern Telegram Bot API features may be used when they improve the current management-bot flow. Start with `docs/TELEGRAM_BOT_API_REFERENCE.md`; new features do not expand product scope or move PR review into the first milestone.
- Before implementing or changing a Telegram feature, check the official Telegram changelog/method documentation and the matching aiogram documentation for the installed/locked version. Confirm the actual Python method, model fields, required permissions and supported chat type. Do not translate a camelCase API name into an assumed Python API or infer availability from another SDK.
- The recorded baseline is aiogram 3.32.0 with Bot API 10.4 support, checked on 2026-11-17. It is a dated reference, not a perpetual claim about the latest release. Check PyPI for the current stable version when establishing/upgrading dependencies, then lock the chosen compatible version in `uv.lock`. Do not call it installed before installation or use moving `/latest/` documentation as proof about an older locked package.
- For each adopted new feature, update the reference with the Telegram and aiogram links, checked versions/date, exact method/fields and restrictions. If sources conflict or cannot be retrieved, state what remains unverified; do not invent a signature or silently assume support. Inspect the installed package or the official versioned distribution when needed.
- Distinguish Bot API from MTProto, guest/managed bots from this group's member bot, private-chat drafts/widgets from group-topic messages, and business checklists from ordinary bot messages. A Telegram tag, Premium status or a visual button state does not grant an application role or approve an action.
- Keep optional UI enhancements behind a verified capability decision and a simple supported fallback. In the `pm` group topic, use persisted messages and bounded edits when private-chat-only streaming is unavailable; never report a temporary draft or ephemeral message as a durable team notification.
- Test new payloads and policy behavior offline during development. The user has authorized early live checks; distinguish each verified adapter/tool from the remaining final integration acceptance.

## Memory policy agreed with the user

The managed team consists of three peers; do not invent a product-manager role. Asking about ambiguous decisions, confirming removal, notifying recorded changes and seeking at least two confirmations for sensitive decisions are agent prompt instructions. Do not add vote-counting, sensitivity classification or approval gates in code for these rules. Technical project/source/revision/turn validation remains mandatory. Memory tools only stage changes; the active worker atomically commits them with the successful job and notifications. Preserve history and evidence. Draft architecture text stays in the bot database; no writes to the managed repository are authorized by phase three.

## GitHub policy agreed with the user

Consensus and sensitive-operation confirmation remain agent prompt rules for GitHub too; do not add approval buttons, vote counting or a mandatory human-approval table. Prepare and submit are distinct tool operations; submission becomes runnable only after a successful turn. Validate repository binding, source freshness, draft revision and allowed operation types in code. Keep credentials in the main-process adapter, never in agent arguments/environment or MCP configuration. Unknown outcomes are reconciled, never blindly resent. Preserve external race and incomplete-pagination limitations in reports.
