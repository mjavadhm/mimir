# Problems Peyda must solve

Peyda is a project-management assistant on Telegram for a three-person peer
team. The bot is a member of the team's group (topic `pm`), reads what members
write, joins discussions with knowledge of the code and the history of
decisions, records decisions, turns them into issues, and follows the work
through to a PR.

This document records the concrete team problems the bot exists to solve. The
rebuild contract that answers them is `docs/REBUILD_PLAN.md`; the previous
implementation's docs are archived under `docs/legacy/` for reference only.

1. **Work starts without coordination.** A member picks up a task and starts
   before anyone has agreed who does what.

2. **Pushing straight to `main`.** Changes land on the main branch without
   going through a reviewed PR.

3. **Parallel implementations of one responsibility.** The same
   responsibility gets built twice because nobody sees the duplication
   forming.

4. **Overlapping work.** Members collide on the same or neighbouring work
   without noticing.

5. **Decisions get lost in group chatter.** What was agreed, and why, drowns
   in the message flow and cannot be reliably recovered later.

6. **An unclear relationship between PRs and issues.** Nobody can tell which
   PR belongs to which issue, or whether an issue has any PR at all.

7. **Forgotten blockers and PRs waiting for review.** Anything that needs
   someone else's action — an unblocked task, a PR awaiting review — silently
   stalls and is forgotten.
