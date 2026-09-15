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

Relevant to the D5 decision: approach B (narrow the satisfying surface) is
likely cheaper than approach A (parse everything), because a structured
call is smaller than an ad-hoc query and its result set.

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
