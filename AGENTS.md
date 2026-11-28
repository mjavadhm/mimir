# Mimir PM Bot development guide

## Current contract and state

The owner's rebuild contract is [docs/REBUILD_PLAN.md](docs/REBUILD_PLAN.md).
Read it before planning or implementing a change. The team's problems are
recorded in [docs/PROBLEMS.md](docs/PROBLEMS.md).

On 2026-11-22 the owner requested removal of the previous implementation and
confirmed the rebuild contract. The old source, tests, migrations, scripts,
dependency manifests and CI configuration have been removed. Phase one of the rebuild is implemented and live-tested. Local credentials, runtime data, installed
tools and Git history have been retained.

The documents in `docs/legacy/` describe the removed implementation. Existing
API references and the previous detailed PR-review proposal are background,
not evidence that the rebuild implements or supports those capabilities.
Do not carry old restrictions or completed-phase claims into the rebuild.

## Product and agent behavior

- Build a Telegram project-management assistant for three peers. Do not invent
  a product-manager role. The team's group and its `pm` topic are the primary
  interface.
- Use one real Antigravity agent and one persistent conversation for the whole
  managed project. Connect through ACP and reopen the explicitly configured
  conversation with `session/load`; a loading failure must not silently create
  a replacement. Users, topics and ordinary management tasks share this session.
  In phase five, Mimir delegates PR analysis through a review tool that starts
  a separate Antigravity session with a fixed prompt and PR-specific input.
- Keep management turns sequential, including across process restarts. Receiving
  input concurrently never authorizes a second active prompt.
- Run `agy-cli` with its ordinary user-login profile, including DBus/keyring
  access as required by that profile, and Turbo auto-accept. This is a terminal
  agent, not an API-key model service. Never replace the existing `agy` GUI
  launcher.
- Do not add bot restrictions, approval gates or wrappers around the agent's
  native file, terminal or Git tools, and do not restrict Turbo. The ACP client
  must not advertise filesystem or terminal capabilities; Antigravity handles
  these operations through its own native tools. Bot tools are additions.
- The agent chooses whether to send messages, replies, reactions, files, any
  combination, or nothing. A successful turn may be silent. Never automatically
  publish ordinary session text to the group; posting requires an explicit
  Telegram tool call.
- Team confirmation and consensus rules are standing agent instructions. Do
  not implement vote counting, sensitivity classification or social approval
  gates. Technical validation of targets, turns, revisions and deduplication
  remains mandatory for the bot's tools.
- GitHub is the source of truth for issues and PRs. Local storage holds a
  reconciled projection, conversation evidence and durable subject memory.
- Deliver the management bot first. Add PR review in the second milestone
  through the delegated review workflow in `docs/PR_REVIEW_PLAN.md`.

## Telegram tools and queue

- Provide separate tools to send a group/topic message, reply to a specific
  message/version, set/change/remove reactions in stable order, and send a
  readable file as a document. The contract's 50 MB note is informational,
  never a bot-side refusal rule; report the actual service result.
- Tool results describe what happened and what data is available. Validate
  turn/generation, target binding, deduplication and message versions; do not
  add social validation.
- Persist incoming messages before advancing Telegram's offset. Preserve
  speaker identity, edits, replies, topic links and the arrival timestamp.
- Group nearby messages into a pile. Defaults are a four-second silence gap
  and a sixty-second maximum wait from the first line, both read from settings.
  A pile produces one prompt in the same persistent session.
- Input arriving during an active turn is durably queued for the following
  pile. Silence and reaction-only responses remain valid.
- Measure real Antigravity prompt capacity before selecting the backlog cap.
  Record an explicit overflow decision; never silently truncate or split input.
- Store each outgoing attempt before network submission. Replaying the same
  tool-call identity must not resend it; distinct deliberate sends are allowed.
  Pending sends may outlive a successful turn. Record unknown outcomes and
  reconcile them before retrying. Never blindly replay a send after a crash
  between submission and result recording, or claim exactly-once delivery.
- Persist Telegram RetryAfter rescheduling and chunk long text. Keep retries
  bounded and distinguish definite rejection, retryable failure and unknown
  outcome.
- `/screen` belongs only in the technical admin's private chat. It shows live
  session/turn/tool/text/model/mode/Turbo state, application state and queue.
  Route admin queries and supported controls directly in the private chat.
  Follow section 6 of the rebuild contract for quota reporting and controls.

## Architecture

- Use Python, aiogram 3, SQLite with WAL and Alembic migrations.
- Keep rules and models in `domain`, use cases and small I/O contracts in
  `application`, integration details in `adapters`, lifecycle and scheduling in
  `runtime`, and concrete dependency wiring in `bootstrap`.
- Domain has no framework or I/O imports. Application does not import concrete
  adapters. Map external models into typed boundary data; do not pass
  `dict[str, Any]` between layers.
- Group ownership by `conversations`, `memory`, `work` and `actions`. Add
  `reviews` only in the second milestone. Cross-capability changes go through
  public application contracts, never another capability's repository internals.
- Route the bot's effectful tools through the owning action path. This path
  does not wrap or replace Antigravity's native file/terminal/Git tools.
- Keep SQL in storage adapters and ACP/HTTP details in their adapters. Use
  small Protocols, functions, dataclasses, composition and manual injection.
  Avoid generic base repositories, service locators and speculative scaffolding.
- Use short use-case transactions and a lightweight Unit of Work for related
  writes. Repositories do not commit independently. Never hold a transaction
  while awaiting the agent, a network request or a human. Never share a database
  session between concurrent tasks. Every schema change needs an Alembic migration.
- Before a coding change, identify its owner, existing path, public contract,
  failure cases and acceptance criterion. Deliver a small end-to-end behavior.
  Add executable import-boundary checks when the source tree is created.
- Record substantive decisions with reasons and keep the rebuild contract and
  active delivery documentation consistent. Summaries never replace evidence
  or versioned decision history.

## Delivery and verification

- Follow the five phases in the rebuild contract: durable Telegram storage;
  the real single-session loop and tools; subject memory; issues and
  coordination; then PR review and daily-use preparation.
- Phase two starts with live Antigravity probes for session lifecycle, eager
  tools, Turbo and prompt capacity. Installation, connection checks and real
  testing are authorized; do not defer every live check to the final phase.
- Each delivery row in section 8 requires offline checks and live acceptance against its real
  service. Record current results separately in `docs/LIVE_TESTING.md` when
  checks begin. Historical evidence does not establish rebuild compatibility.
- Verify official Telegram method/changelog documentation and the matching
  installed/locked aiogram APIs before adopting a feature. Check current stable
  dependencies when establishing the environment and lock chosen versions.
  Dated API references do not prove what is currently installed or supported.
- Keep routine tests offline, isolated from `.env` and process credentials.
  Live checks must be explicit commands, never import or routine pytest effects.
- Use real temporary SQLite files for persistence/recovery tests and fakes at
  external boundaries. Test durable ACK, edits and duplicates, one active turn,
  crash recovery, stale tool calls, cancellation, unknown outcomes and durable
  retry scheduling. PR-version checks belong to the review milestone.
- Once the development environment exists, run `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy src/mimir_pm_bot` and relevant
  `uv run pytest` checks. Report actual results; fake success is not live proof.
- Use the installed `python-design-patterns`, `python-code-style`,
  `async-python-patterns` and `python-testing-patterns` skills when applicable.
  Keep interfaces typed, async I/O nonblocking, background work bounded and
  shutdown explicit. Propagate cancellation after cleanup; do not swallow errors.
- Keep operational numbers configurable. Keep credentials, private exports,
  databases, logs and auth state in ignored local locations such as `.env`,
  `.runtime/` and `.tools/`; never commit them or include them in fixtures.
