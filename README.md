# agent-preflight

**Enforce that your AI coding agent did the required thing — before it acts.**

`CLAUDE.md` is an instruction. This is a control.

Instructions are advisory. A model weighs them against everything else in its
context, and sometimes they lose. A `PreToolUse` hook exits non-zero and the
tool call **does not happen** — no interpretation, no context pressure, no
negotiation.

> **Status: early.** Extracted from a working system in daily personal use
> since early 2026. Being generalised. Not yet stable for other people.

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
This answers *"did the agent actually do it properly."*

---

## Three outcomes, not two

Most policy tools are allow / deny, or allow / deny / ask. This has a third
state that matters in practice:

| outcome | meaning | result |
|---|---|---|
| **satisfies** | the required action was performed properly | allow, gate opens |
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
| backend unreachable | **degraded: warn loudly, log, allow** | the control plane is down; blocking every call bricks the session |
| dev mode explicitly enabled | **fail open, noisily** | opt-in, session-scoped, warns on every invocation |

**Dev mode is deliberately hard to leave on.** Silent degradation is the
failure this project exists to prevent — a gate that quietly stopped enforcing
while everything looked fine is worse than no gate, because you stop checking.

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
