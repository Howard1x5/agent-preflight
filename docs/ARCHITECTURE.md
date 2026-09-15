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

**Not yet decided:** what replaces it. Candidate directions include structured
declaration of intent by the caller, parsing rather than matching, and
rule definitions as data rather than code. **This is the next design decision
and it is deliberately left open** rather than settled prematurely.

---

## D6 — Audit log

**Current state:** plain local file.

**Known gap:** competitors emit OpenTelemetry (`mcp-policy-gateway`) or index
sessions (`entire/cli`). Structured, exportable output is required before this
is useful to anyone operating it at scale.

**Deferred**, not dismissed.

---

## Scope limits, stated explicitly

- **Not a defence against a hostile user.** Local configuration can disable it.
  It constrains an agent, not an operator.
- **Not MCP security.** It sees all tool calls; MCP is one of them.
- **Not a policy engine.** Today it enforces one rule. Pluggable rules are the
  intended direction, not a current capability.
