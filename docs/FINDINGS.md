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

**First result after the response (2026-10-03):** the first live enforce
session under the root-owned, dedicated-uid install (message version
`8ed0b22c`), with a fresh agent that had no part in building the control.
The session receipt read `1 confirmed consultation, 4 blocked, mode=enforce`.
From the operator-supplied transcript:

| # | agent's call | gate |
|---|---|---|
| 1 | `ssh ... python` querying the database directly | refused: compound — message names `preflight-query` |
| 2 | `preflight-query --help` | refused: no filtering |
| 3 | `preflight-query "open brain gate hook blocking"` | refused: no filtering |
| 4 | read the hook source to find the syntax | refused |
| 5 | `preflight-query --query "..."` | admitted, confirmed |

Refused calls before the first admitted query: **4**, against 39 and 18 under
`f9e4ed8f`. The agent's first attempt was still the habitual compound query,
so the finding stands: it did not discover the wrapper on its own. But it
switched to the wrapper immediately after one message that named it.

Calls 2–4 were not a discovery failure but a syntax failure: the agent was
already using the wrapper and passed its terms positionally, which the
wrapper rejected and the generic "no filtering" message did not explain.
Response: the wrapper now accepts positional terms as the query, and a wrapper
call without terms gets its own message naming both forms.

Limits: n = 1 session. The figures come from the session receipt and the
transcript; the records themselves are in the service's directory.

**Second result (2026-10-03, after the positional-terms fix, message version
`4c34cd73`):** same install, a new fresh agent, the identical question.

| # | agent's call | gate |
|---|---|---|
| 1 | `grep ... \| ls ... \| tail` inspecting settings | refused: compound |
| 2 | read a settings file | refused: not yet consulted |
| 3 | `preflight-query --query "..."` | admitted, confirmed |

Refused calls before the first admitted query: **2** (4 in the first session,
39 and 18 before the message change). No wrapper call was wasted: the first
`preflight-query` call qualified. Neither refusal was a failed query; both were
the agent orienting itself before it had tried to query at all, which is the
cost of a turn that starts closed rather than a discovery failure.

Not exercised: this agent used `--query` on its first attempt -- plausibly
because the block message names it -- so the positional-terms path is covered
only by tests and an operator-run check, not by a fresh agent. n = 2 live
sessions, one operator, one model.

**How this will be tested:** the report keeps message versions apart, so
refused-before-first-admission under `8ed0b22c` can be compared directly with
`f9e4ed8f` once enforce mode has run for long enough. If naming the wrapper does
not move that number, the message is not the lever and the finding stands
without a fix.
