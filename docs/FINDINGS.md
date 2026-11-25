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

## F2 — An advisory message on every call was heeded on 14% of turns

**Claim:** in rehearsal mode the gate showed the agent the full block message
on every tool call that would have been refused, and refused nothing. The
agent satisfied the rule in about one turn in seven. Being told, every call,
was not enough to change what it did. This is the project's premise measured
on the project itself: an instruction is advisory; only the block is a control.

**Setting:** rehearsal mode, the maintainer's own Claude Code sessions,
2026-10-02 to 2026-11-25. Rule `consult-backend` (claude-mem backend), message
versions `3240e7d0` (1,890 records), `4c34cd73` (147), `8ed0b22c` (55).
2,070 records across 6 sessions. The operator's own instruction file also asked
for a memory search before big decisions, so the agent had both the standing
instruction and the per-call message.

**What the records show** (joined to transcripts; see Limits):

| measure | value |
|---|---|
| turns with at least one tool call | 233 |
| turns that would have been refused at least once | 221 (95%) |
| turns in which the agent satisfied the rule anyway | 32 (14%) |
| would-be refusals per refused turn | median 4, max 79 |
| first tool on refused turns | shell 171, other 17, write 16, mcp 10, read 6, fetch 1 |

The median of 4 is a rehearsal artifact: nothing was refused, so nothing
forced the query, and the count kept climbing through the turn. Under
enforcement the expected cost is about one refusal and one query per turn.

**Scoping does not rescue it for this operator.** Replaying the same period's
turns (328 with tool calls) through the proposed "big decisions only" scope:

| scope | share of turns it fires on |
|---|---|
| every turn (current) | 100% |
| action triggers (ssh to own hosts, git push/publish, delete, config/memory writes) | 64% |
| prompt triggers (planning, past-work references, project names, money/career, first turn, after compaction) | 50% |
| either | 80% |
| only state-changing commands on own hosts | roughly 40% (looser turn split; indicative) |

773 of the 838 action-trigger calls were ssh to the operator's own machines.
When the work *is* infrastructure, "big decision" is most turns.

**Limits:**

- The record schema had no turn id. Turns were rebuilt by joining records to
  harness transcripts by session hash and timestamp (`tools/turn_join.py`);
  4 of 6 sessions joined. Schema 3 adds `turn_seq` so this join is no longer
  needed.
- n = 1 operator, one agent model, one rule. The 14% includes turns where the
  standing instruction, not the message, may have been the cause; the records
  cannot separate the two.
- Trigger matching was done with regular expressions over prompts and tool
  inputs, locally. The prompt text is not in this repository and was never
  written to disk.

**Response (2026-11-25):**

1. Enforce mode switched on for the operator's own sessions.
2. Schema 3: every record carries `turn_seq`, the nth user prompt in the
   session, counted by the turn-start hook alongside the carried-over failure
   state (Incident 1). No content is recorded.
3. `tools/turn_join.py` published so the rehearsal figures can be reproduced
   from pre-schema-3 logs.

**How this will be tested:** under enforcement, per turn: refusals before the
first admitted query, share of turns that reach a confirmed query, and whether
the operator switches back to rehearsal (the friction signal that matters most,
because a control that gets switched off is no control). Compare against the
rehearsal table above once there are as many enforced turns as rehearsed ones.
