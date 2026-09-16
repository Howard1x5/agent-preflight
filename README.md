# agent-preflight

**Require your AI coding agent to do the prerequisite work — and measure
whether it actually did.**

`CLAUDE.md` is an instruction. This is a control.

Instructions are advisory. A model weighs them against everything else in its
context, and sometimes they lose. A `PreToolUse` hook exits non-zero and the
tool call **does not happen** — no interpretation, no context pressure, no
negotiation.

> **Status: early.** Extracted from a working system in daily personal use
> since early 2026. Being generalised. Not yet stable for other people.
>
> The core design decision was settled on 2026-09-16 and is being implemented.
> See `docs/ARCHITECTURE.md` D5. Behaviour described below is the target, not
> all of it is shipped.

---

## What it does

Sits on your agent harness's lifecycle events and refuses to let the agent act
until a required precondition is satisfied.

```
agent wants to call a tool
        |
        v
  [ PreToolUse gate ]
        |
   +----+----+--------------------+
   |         |                    |
SATISFIES  ALLOWED           BLOCKED
 the gate  but does not      exit 2, reason
           satisfy it        returned to the agent
```

Examples of preconditions it can enforce:

- query the memory store before answering — **the original use case**
- read the file before editing it
- run the tests before claiming success
- check the schema before writing a migration

---

## Why it is not an allowlist

Almost every tool in this space enforces **permission**: may this agent call
this tool with these arguments? Allowlists, deny rules, name matching.

This enforces **diligence**: did the agent actually do the required work, and
do it *properly*?

The distinction is concrete. The original gate does not check that a memory
query happened — it inspects whether the query was **targeted**, by testing the
SQL for real filtering (`WHERE` / `ILIKE`) rather than accepting a bare
`ORDER BY ... LIMIT` dump that returns whatever happens to be recent.

A permission layer answers *"was the agent allowed to do this."*
This answers *"did the agent actually do the prerequisite work."*

Those are different questions, and the second one splits in two — which matters,
because only one half can honestly be enforced:

| | what it covers | how it behaves |
|---|---|---|
| **gated** | a live round-trip to the required dependency completed this turn | blocks until satisfied |
| **measured** | whether that contact was substantive rather than a formality | reported, never blocked on |

Quality is measured and not gated on purpose. Any mechanical test for "was this
query good enough" becomes a new thing to satisfy cheaply — which is precisely
the failure documented as Incident 4.

---

## What it does not claim

This project exists because success signals lie, so it should not produce one.

- **It does not verify that the work was done *properly*.** It verifies the
  prerequisite operation completed. Whether the agent understood the result, or
  acted on it, is outside what any hook can establish.
- **It does not certify the agent's conclusions.** A completed lookup can
  return plausible garbage — see Incident 2.
- **It is not a defence against a hostile operator.** It runs in-process and
  local configuration can disable it. It constrains an agent, not a person.
- **It did not catch its own biggest failure.** Incident 4 was found by reading
  the audit log, not by the gate. That is the argument for the log, and an
  honest statement of the gate's limits.

---

## Three outcomes, not two

Most policy tools are allow / deny, or allow / deny / ask. This has a third
state that matters in practice:

| outcome | meaning | result |
|---|---|---|
| **satisfies** | the required action ran and completed without error | allow, gate opens |
| **permitted, non-qualifying** | allowed, but does not count toward the gate | allow, gate stays shut |
| **blocked** | precondition not met | exit 2, actionable reason returned |

The middle state exists because some calls should be permitted without
satisfying the requirement. A write to the memory store is allowed, but writing
is not reading — it must not open a gate that requires a *query*.

---

## Failure posture

This is a **security control, and it fails closed by default.**

If the gate cannot confirm the precondition was met, the tool call is blocked.

But "not satisfied" and "control plane unreachable" are different failures and
must not be conflated:

| condition | posture | rationale |
|---|---|---|
| precondition not met | **block** | the agent skipped a required step |
| backend unreachable | **degraded: warn loudly, log, allow — time-bounded** | the control plane is down; blocking every call bricks the session. Bounded because eight unbounded days is Incident 4 |
| dev mode explicitly enabled | **fail open, noisily** | opt-in, session-scoped, warns on every invocation |

**Dev mode is deliberately hard to leave on.** Silent degradation is the
failure this project exists to prevent — a gate that quietly stopped enforcing
while everything looked fine is worse than no gate, because you stop checking.

**Degraded is not a state to live in.** Measured: eight consecutive days
unreachable, forty gate satisfactions, success reported every turn. Degraded
operation escalates after N consecutive verified failures rather than
continuing indefinitely.

---

## Requirements

An agent harness that exposes lifecycle hooks. Claude Code does
(`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`). Support for other
harnesses is intended but not present.

Python 3.10+. No runtime dependencies.

---

## Not MCP-specific

Several comparable projects are MCP gateways — they see MCP traffic and nothing
else. An agent that shells out to `curl`, or reads `~/.ssh/id_rsa` directly,
is invisible to them.

This hooks the harness lifecycle, so it sees **every** tool call: bash, file
reads, web fetches, MCP invocations, all of it. MCP is one of the things it
catches, not the boundary it operates on.

---

## License

TBD
