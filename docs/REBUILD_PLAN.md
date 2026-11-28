# Rebuilding Mimir PM Bot from zero

This document is self-contained: it explains what the bot is, what it does,
which decisions were made, and in what order to build it — everything needed
to recreate the project from scratch. It contains no code and no tests, only
product description and its contracts. It is taken from the owner's rebuild
document, confirmed by the owner on 2026-11-22. This is the current product
and architecture contract; [AGENTS.md](../AGENTS.md) applies it to development.
For background on the previous implementation, see
`docs/legacy/README.md` (product), `docs/legacy/ARCHITECTURE.md`
(architecture) and `docs/legacy/IMPLEMENTATION_PLAN.md` (delivery plan); the
problems this rebuild answers are listed in `docs/PROBLEMS.md`.

Current state: the owner requested removal of the previous implementation on
2026-11-22. Its source, tests, migrations, scripts, dependency manifests and
CI configuration have been removed. Mimir phase one has passed offline and live acceptance;
historical test results are not acceptance evidence for the rebuild.

## 1. What the bot is

Mimir is a project-management assistant on Telegram for a three-person peer
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
- Project management uses one persistent Antigravity conversation across users,
  tasks and topics. For PR review, Mimir calls a tool that opens a separate
  review session with a fixed reviewer prompt and PR-specific input.
- Durable memory lives in a local SQLite database, independent of session
  lifetime.
- Each subject has a "file" that the agent reads while thinking. The Telegram
  topic, the subject file and the agent session are three different concepts.
- GitHub is the official source of truth for issues/PRs; the bot's database
  is a projection and a memory of discussions, reconciled with GitHub.
- Management turns run sequentially. Each review job tracks its own session and progress.
- Implementation is Python with aiogram 3; the database is SQLite with WAL;
  migrations use Alembic.
- Delivery has two stages: first the PM bot alone; then the PR-review
  capability on the same bot via the [review tool](PR_REVIEW_PLAN.md).
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

## 6. Private admin chat

The technical admin manages Antigravity by messaging the bot directly. Route
admin commands outside the project conversation queue, including while the agent
is busy. Check both the private chat type and configured admin identity on
every request. Send panel updates only to that private chat.

`/screen` shows the active turn, latest tool steps, in-progress session text,
queue, connection, effective model, mode and Turbo state. A limits query shows
the provider-reported quota scope, amount used or remaining, reset time and
observation time wherever available. Report an observed rate-limit event and its
retry time separately. Distinguish Telegram delivery limits from Antigravity
limits. Show unavailable fields explicitly. A token count or a rate-limit error
alone cannot establish the percentage of account quota consumed.

Candidate management commands are cancelling the current turn and selecting a
model supported by the bound session. Verify each operation with the installed
CLI before exposing it. Report the resulting turn state or effective model.
Admin requests use the existing session.

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
- **Phase 5 — requested PR reviews.** Management loads `prompts/mimir.md`.
  A Telegram request calls `review_pr` and starts a separate session with
  `prompts/reviewer.md`, the current PR snapshot and role-specific MCP servers
  from `config/mcp.toml`. Results go to the topic. Explicit requests can
  publish a saved result as a GitHub comment. See the [phase-five plan](PR_REVIEW_PLAN.md).

Each row below is a separately tested delivery. Run relevant offline checks
and at least one real acceptance scenario per row before counting it as
delivered. Record the date, versions, expected and observed result and any
unavailable capability in [the live test log](LIVE_TESTING.md). Use its browser
procedure for group and admin tests. Run rows in the listed order. Phase one has passed. Phase two has integrated live evidence for 2a–2h; 2c still requires a distinct non-admin account for its live negative test. Phase three passed offline and live acceptance for 3a and 3b, including recall after a service restart. Phase four is implemented. Drafting, issue creation, overlap follow-up and restart delivery passed live on tele-agy. Its live PR-link test awaits the first real PR in that repository. Phase five implements Telegram-requested delegated review, version checks, durable recovery and optional publication as a PR conversation comment. See LIVE_TESTING.md for exercised paths.

| Delivery | Live acceptance scenario |
|---|---|
| 1a. Receive and store | Send a message in the real topic and verify its speaker, topic and committed checkpoint in SQLite. |
| 1b. Edit and recover | Edit the message, restart the collector and replay the captured update. Verify both versions survive and neither is duplicated. |
| 2a. Probe Antigravity | Load the configured conversation, run a real prompt and tool, inspect model, mode, Turbo, quota and available controls. Measure prompt capacity and record the backlog decision. |
| 2b. Batch turns | Send several lines in the topic, then another during an active turn. Verify speaker/version references and two sequential prompts in the same session. |
| 2c. Observe from private chat | During a real turn, open the panel and query limits from the admin DM. Compare with CLI observations. Check that a group request and a non-admin DM disclose no panel data. |
| 2d. Control from private chat | Exercise each supported control from the admin DM. Check cancellation against the actual turn and model selection against the next turn in the same conversation. Record unsupported controls. |
| 2e. Send and reply | Trigger a standalone message and a reply through the real agent. Check both in Telegram Web, including the reply target. |
| 2f. React or stay silent | Ask for a reaction, change it and remove it. Verify order and target in the topic. Run a silent turn and verify successful completion without a new post. |
| 2g. Send a document | Send a synthetic text file through the agent tool and open the received document in Telegram Web to compare its content. |
| 2h. Recover an outgoing attempt | Restart after a real send and replay the same tool call. Verify one delivered message. Exercise unknown outcomes and RetryAfter deterministically offline. |
| 3a. Record memory | Make a test decision in the topic and inspect the subject record and source messages. |
| 3b. Revise and recall | Supersede that decision, restart, then ask what changed and why. Verify current and historical evidence. |
| 4a. Create an issue | From an agreed test decision in Telegram create an issue in the designated GitHub repository and verify its link and contents. |
| 4b. Coordinate work | Announce overlapping work and link a test PR. Ask in the topic who is working on it and compare the answer with team evidence and GitHub. |
| 4c. Accept the management bot | Run the discussion-to-issue-to-follow-up flow, restart during pending work and verify recovery and notifications before starting PR review. |
| 5a. Configure prompts and MCP | Verify both role profiles offline, then change the management prompt in a test setup, restart between turns and observe the change in a real Telegram reply. |
| 5b. Review a PR | Have Mimir delegate a test PR to a distinct review session, change its head during review and verify published findings refer to the current revision. |
| 5c. Exercise daily operation | Interleave a team discussion with review work and restart the service. Verify both resume sequentially and no completed external action is repeated. |

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

The private admin panel can bind the existing project conversation to a forum group and topic.
Authorized numeric user IDs come from MIMIR_ADMIN_IDS. The initial environment destination
is optional for --serve. An accepted topic probe saves the destination in SQLite. Each
pile and its deliveries retain their originating route when an admin changes the destination.
