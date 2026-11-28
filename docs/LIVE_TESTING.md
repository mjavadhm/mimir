# Rebuild live acceptance

Use the delivery IDs and expected behavior in [the phase plan](REBUILD_PLAN.md#8-build-order-phases). Each delivery needs its own dated observation on the real service.

## Browser procedure

1. Start the delivery under test with a dedicated rebuild SQLite file and one poller. Check the configured bot, group and topic against the intended destinations. Keep identifiers in local configuration.
2. Use the owner's signed-in Telegram Web session to send synthetic test messages to the configured project topic or the bot's private admin chat. Include a fresh label such as «آزمون میمیر 1a — پیام اول» with a run suffix. Use Telegram's actual edit and reply controls when the scenario calls for them.
3. For 1a, verify sender, topic, arrival and committed checkpoint in SQLite. For 1b, edit the same message to «پیام ویرایش‌شده», restart and replay the captured update locally. Expect one message with two versions.
4. For private controls, start a group turn first, send /screen in the bot DM while it runs and compare the panel with the active CLI. Exercise each supported control and check its actual effect. A non-admin DM requires a distinct participant. Mark that observation pending until exercised.
5. Check visible outputs from the human side and correlate with the relevant local record, CLI event or GitHub object. Use synthetic files for delivery tests. Keep the browser session in the browser. Use sanitized observations in this log.
6. On failure record the actual result and retry after the fix with a fresh run label. For a replay test reuse the original update or call identity. Record offline fault simulations separately.

## Readiness

2026-11-22 — Telegram Web opened in Brave and displayed the messaging interface. No test message sent. No rebuild delivery has live acceptance yet.

## Run record

Append one entry per attempt with delivery ID, date, build and service versions, test input, expected result, actual observation, offline check result, live verdict (passed, partial or failed) and any pending scenario. Mark the delivery complete only when its acceptance checks pass.

## Phase one acceptance — 2026-11-22

Linux, Python 3.14.4, aiogram 3.32.0, Alembic 1.20.1, SQLAlchemy 2.1.9.
The run used the rebuild collector and a dedicated file-backed database.

| Delivery | Procedure and observed result | Verdict |
|---|---|---|
| 1a | Sent the labelled original from the human account in Telegram Web. Confirmed its visible text and matched its exact text in SQLite. The record had a human speaker ID, the configured group and topic, an arrival timestamp, original-message provenance and a committed checkpoint equal to the update ID plus one. Offline ingestion and transaction-rollback checks passed. | Passed |
| 1b | Edited that same message in the browser and observed the changed text in the topic. Captured the synthetic edited update from the real Bot API. After the receiver exited, reopened the database in a fresh process and replayed that update locally through normalization and storage. Verified one logical message, the exact two texts in order, original/edit provenance and an unchanged checkpoint. Offline replay, first-seen edit, restart and exclusive-lock checks passed. | Passed |

During the edit, the first browser submission reported identical content. Reopening the editor, replacing the text and saving produced the verified edit. The synthetic capture stays in ignored local runtime storage.

## Phase two start — 2026-11-22

2a partial. The installed agy-cli 1.5.10 returned an ACP initialize response
advertising session load/resume/close/list and embedded context. Loading the
configured conversation inside the restricted process returned -32603. The
separate desktop-keyring run was rejected before execution. No real turn,
tool call, effective model, account quota or prompt capacity was verified.
The explicit connectivity probe is `python3 scripts/probe_antigravity.py`.

2b partial, offline. Migration 002 adds persistent pile membership and deadlines
for new topic message versions. Historical phase-one messages stay available
as evidence and are not silently submitted as new work. New arrivals join the
latest waiting pile within its deadline. The worker and live two-turn
acceptance remain outstanding.

## Phase two acceptance — 2026-11-22

Real Antigravity ACP 1.5.10, aiogram 3.32.0, existing configured conversation and authorized regular desktop profile. A synthetic run used one dedicated SQLite database across two service starts.

| Delivery | Observed result |
|---|---|
| 2a | Session load and a marker response succeeded. With the CLI auto-accept flag, the real agent called the Mimir MCP tool with both end markers and returned its secret proof on separate 8,192- and 32,768-byte prompts. Use the larger observed size as an initial conservative bound on the complete serialized prompt. The effective session model was gemini-3.8-flash-high, mode default, with six model choices. ACP returned per-turn token usage. Account quota and reset time were not established. Auto-accept was requested on the process and these tool calls completed without a permission round trip. |
| 2b | Two Telegram Web messages less than a second apart became one pile with two version references. A third message arrived during that turn and formed a second pile. Both finished on the same bound conversation. |
| 2c | The configured admin's private /screen showed turn 4 active with the model and mode. /limits reported the unavailable account quota. A group /screen created no agent pile or group panel. Negative non-admin DM routing passed offline. A distinct human account was unavailable for live verification; this row remains partial. |
| 2d | Direct ACP cancellation ended with stopReason cancelled. In the private chat, /cancel targeted the observed active turn, which SQLite recorded as cancelled. The admin selected gemini-3.8-flash-medium, ran another turn, observed the selected model in the panel and restored gemini-3.8-flash-high. |
| 2e | Telegram Web showed a reply quoting the first synthetic request and a separate standalone message. SQLite recorded separate sent receipts. |
| 2f | A reaction-only operation was accepted. Three subsequent operations on one target set, changed and removed the reaction in order. The silent turn completed with zero output rows. |
| 2g | Telegram Web displayed the delivered synthetic document and size. Browser download did not yield a verifiable local copy. After restart, a second explicitly requested delivery saved the Telegram file receipt and download through Bot API matched all 23 original bytes. |
| 2h | Stopped the service and reopened the same database and conversation. Replayed the original send locally via the actual tool handler. It returned sent with the original Telegram message ID, one attempt and one database record. The browser showed one matching standalone message. Offline fault injection verified durable RetryAfter, handling of interrupted attempts and suppression of dependent chunks. |

The test service was stopped and the initial model restored. No production rollout.

Final rebuild checks: 14 offline tests passed in the locked environment, including cross-turn cancellation, durable model requests, expired tool calls, immutable document replay, queue exclusivity and output recovery. Ruff lint/format, strict mypy (27 source files) and diff whitespace checks passed.

## Live account quota in the private admin chat — 2026-11-22

The earlier unknown result was from ACP. Tele Agy's [usage reader](https://github.com/mjavadhm/tele-agy/blob/ddaf8ad34386c25a436154e80887c82108037193/src/tui.ts) uses the interactive CLI's local /usage screen. Mimir now reads that screen in a bounded temporary PTY on demand using the CLI's regular profile. It leaves the project's ACP conversation alone. Only parsed model-window percentages and refresh information are returned. Telegram Web verified all four real weekly/five-hour rows in the bot's private reply. Parser coverage includes zero, full availability and an unrecognized screen. All 16 offline tests, Ruff and strict mypy passed.

## Phase three — decision memory, 2026-11-22

| Delivery | Offline evidence | Live evidence in the pm topic |
| --- | --- | --- |
| 3a — record | Temporary SQLite tests cover staging, publication on successful completion, source validation and standalone subjects. | A suggestion about a blue label produced no decision. An explicit agreement then created revision 1 of the «فانوس کاغذی» decision with its reason and source. A separate question triggered search and read, and the reply was delivered. |
| 3b — revise and recall | Tests cover version history, cancellation and recovery. | A second agreement changed blue to green because the paper changed from white to yellow. After a full service restart on the same acceptance database, a new turn read the history. Telegram Web displayed both colors. The sent reply named both paper colors and contained both exact links computed independently from the stored source messages. |

All five live turns completed. The acceptance service was stopped. The full locked suite passed (23 tests, including the import-boundary check), as did Ruff lint and format, strict mypy, and the diff whitespace check.

## Phase four — issue coordination, 2026-11-22

The acceptance service used an independent SQLite database, the existing ACP conversation, Telegram Web in the pm topic, and the explicitly configured repository `mjavadhm/tele-agy`. The service checked the configured GitHub account and repository. All six Telegram turns reached end_turn.

| Delivery | Offline evidence | Live observation |
| --- | --- | --- |
| 4a-1, prepare and edit | Revision mismatch and cancellation are covered with temporary SQLite. | The orange demo card decision produced draft revision 1. An edit changed the title and description in revision 2. The topic showed the previews. A GitHub title search before confirmation found no matching issue. |
| 4a-2, create | Tests cover stale evidence, changed decision, duplicate submissions, permission denial and rate limiting. | Approval queued revision 2. GitHub returned [Issue 1](https://github.com/mjavadhm/tele-agy/issues/1). An independent read matched the entire title and body including source and operation marker. Another request for revision 2 returned the same issue. A second repository listing found exactly one marker. |
| 4b-1, ownership | Two distinct fixture speakers and their original sources were recovered. | One real speaker announced starting Issue 1. A later overlap question got a reply naming the speaker, linking the announcement, and reporting the open issue with zero GitHub assignees. Only one account participated live. |
| 4b-2, PR follow-up | A linked fixture PR returned merged status in the status tool. | Deferred by the user. Listing GitHub PRs across all states returned none. Resume with a real PR in this repository and compare the topic reply with its GitHub status. |
| 4c, restart | Tests cover interrupted attempts, uncertain outcomes, reconciliation and a pending outbox receipt. | An acceptance-only SQLite trigger delayed delivery of the receipt by 120 seconds. The service was stopped after GitHub success while the link was still pending. On restart the original pending link was delivered, then a new turn reported ownership. The trigger was removed and the acceptance service stopped. |

Final suite: 34 offline tests passed, including import boundaries. Ruff lint and format, strict mypy (37 source files) and diff whitespace checks passed. The dedicated acceptance database and screenshot remain local.

## Phase five — delegated PR review, 2026-11-24

- 5a. A temporary instruction in the management prompt specified a reply marker for a trigger phrase. After service restart, a real request in the customer project's pm topic included the trigger but omitted the marker. Telegram Web showed the exact reply `MIMIR-5A-VERIFIED` and a separate acknowledgement for review 1. The instruction was removed and the service restarted.
- 5b. The requested target was the existing merged PR 66 in `mtaham84/moshtari-yab`. The job pinned base `776bd6cca9` and head `e6e77df94c`. The reviewer workspace contained two changed files. After restart its snapshot also contained the three head check runs (lint, commits, tests). Each reported success. GitHub head-change testing used a fake transport to avoid altering team history.
- 5c. A second Telegram question arrived while the job was running. Management completed it and Telegram acknowledged delivery of reply 175 before the review finished. The service was then restarted with a persisted reviewer session and pending result.
- Regression checks used temporary SQLite and fake ACP/GitHub for duplicate requests, isolation of configured role tools, cancellation, version and issue-context changes, deferred and stale publication, interrupted POST recovery, and bounded retries for an empty result. All 45 offline tests passed, along with import boundaries, Ruff lint/format and strict mypy. The wheel built offline and included both prompts and MCP configuration.
- The separate live tele-agy follow-up still awaits that repository's first PR. The distinct non-admin Telegram account test remains outstanding.
- After recovery, the job completed on the same head. The outbox recorded Telegram receipts 176 and 177 for the two parts of the single saved review. Telegram Web displayed the matching revision and findings. No GitHub publication action was staged by this topic-only request. A formatting split observed between the two messages prompted a regression test for splitting reviews at paragraph boundaries.

### Private destination setup — 2026-11-28

The full offline suite passed (47 tests), as did Ruff lint/format and strict mypy. Temporary SQLite tests exercised an unbound installation, authorized and unauthorized identities, independent admin conversations, abort, mismatched receipt, interrupted probe, restart, batching across a switch and routing an old pending send to its original topic.

Live adapter acceptance used synthetic private incoming updates in an isolated database and the real Telegram Bot API for private replies, group metadata and topic receipts. The database stayed unbound after the nonexistent topic and activated after the configured topic returned the matching rich-message receipt. Reopening SQLite preserved the destination. A separate live comparison found that sendChatAction returned true even for a nonexistent thread, so it is unsuitable for validating a destination. The user-facing command was deployed on the local customer-project test service.
