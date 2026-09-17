# Architecture and Design Decisions

This document records **why** each decision was made, at the time it was made.
The point is that every choice here can be defended later without relying on
memory of the session it came from.

Origin: extracted from the Open Brain memory system's hook layer, in daily
personal use since early 2026.

---

## D1 — Hook, not proxy

**Decision:** enforce at the agent harness's lifecycle events, in-process.
Not as a network proxy or gateway.

**Alternatives considered:** most of the field builds out-of-process gateways
(`hoophq/hoop`, `portcullis`, `mcp-policy-gateway`, `arbitus`) that intercept
traffic between agent and tool.

**Why hook wins for this problem:**
- Sees the agent's actual intent and session context, not just wire traffic
- Covers **all** tool calls — bash, local file reads, web fetches — not only
  MCP traffic. A proxy watching MCP cannot see `curl` or a direct read of
  `~/.ssh/id_rsa`.
- Deployment is a settings entry, not an infrastructure component
- No TLS interception, no traffic redirection, no certificate handling

**Accepted costs:**
- Only works with harnesses exposing hooks (Claude Code today)
- In-process, so a user with local write access can disable it. This is a
  **guardrail against agent behaviour, not a defence against a hostile
  operator** — an important scope limit, stated rather than implied.

---

## D2 — Diligence, not permission

**Decision:** enforce whether the required work was done *properly*, rather
than whether a call was permitted.

**Why:** permission is a solved and crowded problem — allowlists, deny rules,
argument matching. Every surveyed competitor does it. None checks quality of
execution.

**Concrete form:** the gate does not accept "a memory query occurred." It
inspects whether the query was targeted — real filtering (`WHERE`, `ILIKE`)
versus a bare `ORDER BY ... LIMIT` returning whatever is recent. A generic dump
technically satisfies "did you query" while defeating the purpose.

**Generalises to:** read-before-edit, test-before-claiming-success,
schema-check-before-migration. The rule differs; the shape does not.

**Amended 2026-09-16 — the claim was too strong.** Incident 4 measured what
this control can actually establish. "Enforces whether the work was done
properly" is not defensible: targetedness is a quality judgment, and any
mechanical test for it becomes a new proxy to game. What is defensible after
D5 is narrower and split in two:

| | claim |
|---|---|
| **gated** | a live backend round-trip occurred this turn, established from the response |
| **measured** | whether that contact was substantive — reported, never blocked on |

The distinction from an allowlist survives this, and in fact sharpens: an
allowlist cannot detect its own rubber-stamping. The record produced here can,
and did.

---

## D3 — Three outcomes

**Decision:** `satisfies` / `permitted-non-qualifying` / `blocked`.

**Why the middle state is necessary:** some calls must be allowed without
counting toward the requirement. Writing to the memory store is legitimate, but
a write is not a read — permitting it must not open a gate that requires a
*query*. Two-state allow/deny forces a wrong answer either way: block a
legitimate write, or let a write satisfy a read requirement.

---

## D4 — Fail closed, with an explicit unreachable state

**Decision:** this is a security control. Default posture is **fail closed**.

**Superseded:** the original implementation failed open — a missing gate file
meant allow, so sessions would not brick. Correct for a personal productivity
tool; disqualifying for a control.

**But "not satisfied" and "backend down" are different failures:**

| condition | posture |
|---|---|
| precondition not met | block |
| backend unreachable | degraded — warn loudly, log as degraded, allow |
| dev mode enabled | fail open, noisily, session-scoped |

**Why unreachable cannot simply block:** observed directly. The memory backend
sits behind Tailscale; during several days of working off the home network it
was unreachable. Strict fail-closed would have blocked every tool call for the
entire period. Conflating "the agent skipped a step" with "the control plane is
down" makes the tool unusable in exactly the conditions where it needs to
degrade gracefully.

**Amended 2026-09-16 — degraded cannot be a steady state.** The table above
says "warn loudly, log as degraded, allow." Incident 4 measured what happens
when the warning is absent and the allowance is unbounded: eight consecutive
days, zero successful backend queries, forty gate satisfactions, success
reported every turn. Degraded operation must be **time-bounded and escalating**
— N consecutive verified failures move the session to a state that blocks or
demands acknowledgement, rather than one it can sit in for a week.

**Why dev mode must be loud and session-scoped:** the original failure this
project exists to prevent is *silent* degradation — the gate holding open while
the backend was disconnected, discovered only by accident. A persistent config
flag recreates that trap. It must warn on every invocation and expire with the
session, so it cannot quietly become permanent.

---

## D5 — Classification is the known weakness

**Current state:** intent is inferred by regex pattern-matching against bash
command strings, URLs and tool names.

**Why this is the priority problem:**
- Every new query shape needs a new pattern
- Legitimate calls get blocked when they do not match — the main source of
  day-to-day friction
- Patterns are hardcoded to one environment (host names, endpoints), so the
  tool is not portable without editing source

**Decided 2026-09-16.** Settled by two independent frontier-model reviews from
identical inputs, plus analysis of 809 audit records (`INCIDENTS.md`,
Incident 4). Recorded in `DESIGN-BRIEF-classification.md` and
`DESIGN-BRIEF-premise.md`.

1. **Verification moves after execution.** `PreToolUse` admits candidates and
   blocks non-candidates; `PostToolUse` flips satisfaction, and only on a
   **non-error** result. Non-error, not non-empty — a targeted search that
   correctly returns zero rows is diligence, not a failure.
2. **The local-read substitute is removed — from the classifier *and* from the
   block message.** No local file read satisfies a live-backend requirement, and
   the message the gate returns must stop listing it as an approved method. The
   message is the root cause of Incident 4: it advertised the bypass on every
   block. Fixing `classify()` alone leaves the control instructing agents to do
   something that now gets them blocked. With no substitute available, an unreachable
   backend blocks, and the block is the outage alert. This single change
   addresses Incidents 1 and 4 together.
3. **Gate state leaves `/tmp`.** The current file is named for the session and
   writable by the gated agent, which has shell access and knows its own
   session id. It can open the gate by writing to that file, or by deleting it,
   since a missing file fails open. Satisfaction must be recorded where the
   gated agent cannot write.
4. **Rules become data.** Per-requirement configured clients and endpoints,
   not patterns hardcoded in source.
5. **Liveness leaves the hot path.** Inferred from `PostToolUse` outcomes — N
   consecutive verified failures move the session to the D4 degraded state —
   not probed before every tool call.
6. **Shell is admitted only by parsing.** A single simple command whose
   `argv[0]` is a configured client, established with a real parse rather than
   by denylisting metacharacters. A denylist over raw command text is the same
   unsound control this decision exists to remove. Everything else goes through
   a shipped wrapper that is itself a recognised client.

**Rejected — enforcing on claims rather than tool calls.** Appealing, because
the real concern is an unverified assertion rather than a tool call. Rejected on
two independent grounds: the enforcement point is not reliably reachable in a
pre-tool hook contract, and "asserted a fact it had not verified" requires an
LLM judge whose own cheapest satisfying action is to make no checkable claims —
a worse outcome than the problem.

**Portability note:** step 1 requires `PostToolUse` to carry the tool result.
Confirmed present in Claude Code's hook payload (`tool_response`). Unverified
elsewhere — see `TODO.md` T2.

---

## D6 — The audit log is the product, not a byproduct

**Superseded 2026-09-16.** This was previously recorded as a deferred gap — a
plain local file, with structured export listed as future work.

**Decision:** the per-decision record is the project's distinctive output, and
enforcement is the instrument that generates it.

**Why:** Incident 4 was not found by the gate. It was found by reading the
gate's log, which held enough per-decision detail to establish what satisfied
the control, how large each satisfying read was, and how that distribution moved
against backend availability. The finding overturned the maintainer's own
stated diagnosis. Neither an allowlist nor a classifier can produce that; both
are blind to their own rubber-stamping.

**Condition — Incident 3 is the specification.** This log existed for 162 days
and nobody read it. "Ship the log" without re-surfacing reproduces exactly the
failure this project documents as Incident 3. A per-session drift rate must be
pushed at the operator — at session end and in the block message — and a
threshold must turn the metric into an alert. Storage is not follow-up.

**Still deferred:** structured export (OpenTelemetry, session indexing).

---

## Scope limits, stated explicitly

- **Not a defence against a hostile user.** Local configuration can disable it.
  It constrains an agent, not an operator.
- **Not MCP security.** It sees all tool calls; MCP is one of them.
- **Not a policy engine.** Today it enforces one rule. Pluggable rules are the
  intended direction, not a current capability.
