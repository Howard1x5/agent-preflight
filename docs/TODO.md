# Deferred Work

Parked deliberately. Not started. Recorded so they are not rediscovered later
as surprises.

---

## T1 — Measure token cost of enforcement

The hook itself is a Python subprocess and burns no model tokens. But
enforcement is not free at the model layer:

- **Every turn is forced to perform a qualifying query** before any tool call.
  That query and its results enter the context window.
- **Every block returns a reason string to the model** (`exit 2` + stderr),
  which is additional input tokens, plus whatever retry the model then attempts.
- A gate that blocks repeatedly in one turn multiplies this.

Rough observed shape: a satisfying memory read runs a few hundred tokens per
turn. Over a long session that compounds into a real, unmeasured cost.

**To do:** instrument it. Log the token footprint of the satisfying call and of
block messages, and report cost per session. Enforcement that is too expensive
will simply be switched off, which is the same outcome as having no control.

**Now more relevant, not less.** D5 was decided on 2026-09-16 in favour of a
narrowed satisfying surface plus post-execution verification. That removes the
local-read escape hatch, so a turn that previously satisfied the gate with a
3-line file read will now perform a real backend query. Enforcement cost goes
**up**, and it has never been measured. A control that is too expensive gets
switched off, which is the same outcome as having no control.

---

## T2 — Support harnesses beyond Claude Code

Currently bound to Claude Code's hook contract (`UserPromptSubmit`,
`PreToolUse`, `PostToolUse`, `Stop`, and the `exit 2` blocking convention).

Other agent harnesses expose different lifecycle mechanisms, and some expose
none. Supporting them means separating the **policy core** (classify, decide,
audit) from the **harness adapter** (how events arrive, how a block is
signalled).

**To do:** that separation, before harness-specific logic spreads through the
codebase and makes it expensive.

Not urgent. Claude Code is the current user and the only one with a mature hook
contract. But the architecture decision is easier now than later.

---

## T3 — Agent-facing feedback in the post phase

`PostToolUse` currently reports an unconfirmed query to the **operator** via
`systemMessage`. The agent is not told, so it has no cue to retry.

Feeding text back to the model from the post phase is a different channel
(`hookSpecificOutput.additionalContext`) and has not been verified against the
harness. Until it is, the retry hint reaches a human who is not the one able to
act on it in that moment.

**To do:** verify the channel exists and carries text to the model, then route
the retry message there and keep only degradation on the operator channel.

---

## T4 — Receipt cost grows with the record file

`receipt()` reads the whole of `decisions.jsonl` and filters by session on every
session end. That is fine at hundreds of records and wrong at hundreds of
thousands.

**To do:** roll records per day (the export format already assumes one file per
day) and read only the current day, or keep a per-session counter file updated
as decisions are made.
