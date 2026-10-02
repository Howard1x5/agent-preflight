# Findings

Measured results from running agent-preflight on real traffic. Each entry
states what the records can and cannot establish.

## F1 — Agents do not discover the wrapper on their own

**Claim:** an agent that needs to query the backend reaches for the shell tools
it already knows, writes the query as a compound command, and does not use the
shipped wrapper unless something puts the wrapper in front of it. A correct
wrapper that the agent never runs does not change behaviour.

**Setting:** observe mode, the maintainer's own Claude Code sessions,
2026-09-24, immediately after the legacy gate was retired. Rule
`consult-backend`, message version `f9e4ed8f`. The wrapper existed, was
documented in the README, and was a configured client. It was not on `PATH`,
and the block messages referred to "the configured clients" and "the shipped
wrapper" without naming either.

**What the records show:**

| session | shell calls refused before the first admitted query | first admitted query |
|---|---|---|
| `3f7fc03b0dbb` | 39 | wrapper (how it was invoked is not recorded) |
| `f3a2fbbce766` | 18 | wrapper, invoked by full repo path |

In both sessions the first query the gate could admit was the wrapper, and it
confirmed on the first attempt. In `f3a2fbbce766` it was invoked by its full
path inside the repository.

**What the transcript adds for `f3a2fbbce766`:** three of the 18 refused shell
calls were real backend queries that returned real results — a piped `curl`, a
`curl` chained with `ssh`, and a loop of four searches. The agent obtained the
information it needed every time. The gate could interpret none of it.

**Limits:**

- Records carry no command text by design, so they cannot say how many refused
  shell calls were backend queries. The 3-of-18 figure comes from the session
  transcript, for one session only. For `3f7fc03b0dbb` only the count of 39 is
  established.
- n = 2 sessions, one operator, one agent model. This is an observation, not a
  rate.
- In `f3a2fbbce766` the agent used the wrapper because the session's plan named
  it, not because a gate message did. Why it was used in `3f7fc03b0dbb` is not
  established.

**Response (2026-10-01 – 10-02):**

1. `bcbdbd6` — the installer links `preflight-query` into `~/.local/bin`, so it
   runs by name. Verified: a bare-name query was admitted and confirmed.
2. `3b1140f` — block messages name the configured clients, the wrapper and its
   required `--query`, read from the rule config. Still no worked example, still
   no endpoints, and only actions that perform a query are named (Incident 4).
   Message version `8ed0b22c`.

**How this will be tested:** the report keeps message versions apart, so
refused-before-first-admission under `8ed0b22c` can be compared directly with
`f9e4ed8f` once enforce mode has run for long enough. If naming the wrapper does
not move that number, the message is not the lever and the finding stands
without a fix.
