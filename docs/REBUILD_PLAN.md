# Rebuilding Peyda PM Bot from zero

This document is self-contained: it explains what the bot is, what it does,
which decisions were made, and in what order to build it — everything needed
to recreate the project from scratch. It contains no code and no tests, only
product description and its contracts. It is taken from the owner's rebuild
document (November 2026). For background on the previous implementation, see
`docs/legacy/README.md` (product), `docs/legacy/ARCHITECTURE.md`
(architecture) and `docs/legacy/IMPLEMENTATION_PLAN.md` (delivery plan); the
problems this rebuild answers are listed in `docs/PROBLEMS.md`.

## 1. What the bot is

Peyda is a project-management assistant on Telegram for a three-person peer
team. The bot is a member of the team's Telegram group (topic `pm`), reads
what members write, joins discussions with knowledge of the code and the
history of decisions, records decisions in a "subject file", turns a decision
into an issue, follows the work through to a PR, and later also reviews PRs.

Problems it solves: starting work without coordination, pushing straight to
main, parallel implementations of one responsibility, overlapping work,
decisions getting lost in group chatter, an unclear relationship between PRs
and issues, and forgotten blockers and PRs waiting for review.

## 2. Foundational decisions (never change without a deliberate revisit)

- The team's primary interface is the Telegram group; topics live inside that
  same group.
- The bot's brain is a real coding agent, Google's Antigravity (the `agy`
  terminal command, run via `agy-cli`), connected through the ACP protocol —
  an agent with access to files, terminal and git, not a bare language model.
- For the whole project there is exactly **one agent and one session** of
  Antigravity; a separate session is never created per user/task/topic/PR.
- Durable memory lives in a local SQLite database, independent of session
  lifetime.
- Each subject has a "file" that the agent reads while thinking. The Telegram
  topic, the subject file and the agent session are three different concepts.
- GitHub is the official source of truth for issues/PRs; the bot's database
  is a projection and a memory of discussions, reconciled with GitHub.
- Running agent work is sequential: at any moment at most one turn and one
  active session/prompt.
- Implementation is Python with aiogram 3; the database is SQLite with WAL;
  migrations use Alembic.
- Delivery has two stages: first the PM bot alone; then the PR-review
  capability on the same bot and the same session.
- The Telegram topic is the interface to the same persistent Antigravity
  conversation; the bot reopens the conversation by its fixed ID with
  `session/load`.

## 3. The "open hands" agent philosophy

- `agy` is a terminal agent with a user login, not an API service; it has no
  API key, its authentication comes from its own ordinary profile
  (DBus/keyring), and it runs in turbo mode (auto-accept).
- The bot adds **no restrictions, no approval gates and no wrappers** around
  file/terminal/git, and never restricts turbo. The bot's tools are added to
  the agy environment.
- The bot's ACP client does not advertise filesystem/terminal capability, so
  those jobs are delegated to agy's own native tools.
- The shape of the agent's presence in the group is up to the agent itself: a
  message, a reply, a reaction only, any combination, or silence. **No turn
  is ever required to produce a text message.** Ordinary session text is
  never auto-posted to the group; posting happens only through an explicit
  Telegram tool call.

## 4. The added Telegram tools (contract)

Four tools, freely combinable:

1. Send a message to the group/topic.
2. Reply to a specific message/version of a message.
3. Reactions: set, change or remove one; with stable ordering.
4. Send a file: any file it can read, as a Telegram document; the 50 MB limit
   is only reported informationally in the tool's answer, never used to
   refuse.

Rules for these tools:

- A tool's answer is informational only: it states what happened and what
  data is available, and may add an unverified caution. **No social validation
  exists in code** (for example "how many people confirmed?"). Team
  agreements such as "two people must confirm" are standing instructions to
  the agent, not code logic.
- Code-side validation is purely technical: turn/generation validity, target
  binding, dedup, message versioning.
- Sends are durable: the attempt is stored before the network submit; a crash
  between send and record never replays the message; replaying the same tool
  call does not send twice (idempotency key); several deliberate sends are
  allowed; a pending message can outlive a successful turn; an unknown
  outcome is recorded and never blindly resent; Telegram's RetryAfter
  persists the reschedule durably; long messages are chunked.

## 5. Queue and turns (conversation behavior)

- Every incoming line is stored immediately and durably (the storage
  timestamp is the moment of arrival).
- Messages close in time form a **pile** that closes after a short silence;
  one pile = one session/prompt carrying speaker identities and
  message/version references. Default silence gap is 4 seconds, maximum wait
  from the first line is 60 seconds — every number is read from settings;
  nothing is hardcoded.
- A message during an active turn does not create a second turn; it is
  stored and joins the following pile with later lines.
- A reaction-only turn or silence is a valid turn and never forces a text
  message.
- The pile's backlog cap comes from a real measurement of agy's prompt
  capacity; crossing the cap is never a raw truncate or split (an explicit
  recorded decision defines what happens).

## 6. Status panel (`/screen`)

Only in the bot's technical admin's private chat: a live view of the
Antigravity session (current turn, tool steps, in-progress text, model, mode,
turbo status), the application's state and the Telegram queue. The group
never sees this panel.

## 7. Architecture (lightweight Ports & Adapters)

Layers and dependency direction:

- `domain`: models and rules; no framework, no I/O.
- `application`: use cases and small I/O contracts (ports); knows nothing
  about concrete adapters.
- `adapters`: converts outside data (Telegram, database, ACP) and implements
  the ports.
- `runtime`: the run cycle, scheduling, worker, lifecycle. Makes no product
  decisions.
- `bootstrap`: the only place where real implementations are wired together.

Feature ownership: `conversations` (message identity, versions, replies,
topics), `memory` (subject file, decisions, summaries), `work` (tasks,
dependencies, issues/PRs), `actions` (the single path for tool effects),
`runtime` (inbox/outbox, checkpoints, worker), `reviews` (second stage).

Core rules: every feature exposes a small public API in `application`;
another feature changes its data only through that path. Importing another
feature's repository directly is forbidden. Boundary DTOs are typed;
`dict[str, Any]` never crosses a boundary. Every database change goes through
an Alembic migration.

## 8. Build order (phases)

- **Phase 1 — skeleton and durable Telegram storage:** receive and durably
  store messages with an offset checkpoint; storage only after commit; record
  edit versions; survive restarts and duplicate updates.
- **Phase 2 — the single-session loop:** first live probes against a real
  agy (session lifecycle, eager tools, turbo, prompt capacity), then settings,
  piles/turns, the agent worker, the Telegram tools, durable sends and
  `/screen`.
- **Phase 3 — subject file and memory:** the subject file, search, versioned
  decision records.
- **Phase 4 — issues and coordination:** issue creation with confirmation,
  start announcements, overlap checks, PR follow-up.
- **Phase 5 — PR review and preparation for daily use.**

Every phase has two deliverables: offline (the tests) and live against the
real service; live acceptance criteria are recorded in the implementation
plan.

## 9. Process principles

- Skills and `AGENTS.md` live in the root of the PM project; the agent's
  standing instructions are there.
- Every decision is recorded with its reason in a document; a document is not
  proof of implementation — live results are recorded separately
  (`LIVE_TESTING.md`).
- Numbers and contracts are configurable, never hardcoded; sensitive data
  (tokens, identifiers) appears in no document and no log.
- On failures: an unknown outcome is investigated before retrying; a failed
  send is separated into firm refusal / retryable / unknown; `except: pass`
  is never acceptable.
