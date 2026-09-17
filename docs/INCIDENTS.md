# Field Incidents — Three Silent Degradations in One Week

Real failures observed in the author's own infrastructure between 2026-09-08
and 2026-09-15, while building this project. Not hypotheticals.

They are recorded here because they share one property, and that property is
the reason `agent-preflight` exists:

> **Every one of these failures produced a success signal.** Nothing errored.
> Nothing alerted. Each system continued reporting that it was working while
> producing nothing, or producing garbage.

---

## Incident 1 — The gate held open for four days while its backend was down

**What the control is supposed to do:** block every tool call until the agent
has performed a targeted query against the memory backend.

**What happened:** the operator was away from the home network and Tailscale was
logged out. The memory backend was unreachable for roughly four days. The gate
was satisfied on **every single turn** and never objected once.

**Why:** the classifier treats a read of a *local* memory file as satisfying:

```python
# preflight_gate.py:167-174
if tool_name in ("Read", "Grep", "Glob"):
    if re.search(r"memory/.*\.md|open-brain|MEMORY\.md", path, re.IGNORECASE):
        return "read-memory"          # satisfies the gate
```

Reading `MEMORY.md` from local disk is indistinguishable, to the gate, from
querying the live backend. The proxy for the work was accepted in place of the
work.

**Root cause:** the control verified *a correlate of* the required action
rather than the action itself. And the correlate remained available after the
real dependency vanished.

**Compounding factor:** during the same period the gate blocked a *legitimate*
backend query, because the URL contained `/api/search` but not the literal
string `open-brain`. So the control was simultaneously permitting the wrong
thing and blocking the right thing.

---

## Incident 2 — 145 memories written as unclassified stubs, with no error

**What happened:** the GPU host's LAN address changed (DHCP reshuffle — the
operator's laptop moved too). The memory service's classification endpoint was
hardcoded to the old address:

```python
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://192.168.68.52:11434")   # stale
```

Every classification call raised `URLError: Connection refused`. The handler
caught it and returned a well-formed default:

```python
except Exception as e:
    return {"category": "general", "people": [], "topics": [],
            "action_item": None, "summary": text[:50] + "...",
            "confidence": 0.0, "_error": str(e)}
```

The write then **succeeded**. HTTP 200. Row inserted. No alert.

**Result:** 145 captures over two days stored as `category='general'`,
`confidence=0.0`, no topics, no people, and a summary that was just the first
50 characters of the raw text. Semantically dead — a later topical search over
exactly that period returned nothing.

**How it was discovered:** by accident, while investigating an unrelated
billing question. Not by monitoring.

**Root cause:** an exception handler that converts a hard failure into a
plausible-looking success. The `_error` field was populated and nothing ever
read it.

---

## Incident 3 — A known cost leak survived 18 days because nothing enforced it

**What happened:** on 2026-08-28 the operator identified that the thought
splitter still called a metered cloud API on every capture over 50 words,
recorded it in the memory system, and noted the fix:

> *"The fix uses what's already there: `call_ollama_api()` exists in
> capture.py; `split_with_haiku()` just needs to route through it."*

It was still running on 2026-09-15.

**Root cause:** correct diagnosis, correct fix, recorded in a system designed to
surface exactly this — and no mechanism that ever raised it again. The note
itself said *"a small fix whenever you want to implement it,"* which is how a
known issue becomes a permanent one.

---

## Incident 4 — An eight-day outage produced eight days of silent full non-enforcement

**What the control is supposed to do:** block every tool call until the agent
has performed a targeted query against the memory backend.

**What happened:** the gate writes an audit record for every decision. Nobody
read it for 162 days. It covers **809 decisions across 15 sessions**
(2026-04-06 to 2026-09-16):

| classification | verdict | count |
|---|---|---|
| `targeted` — *classified as* a backend query | allow + satisfy | 689 |
| `read-memory` — a local file read | allow + satisfy | 115 |
| `generic` — an unfiltered dump | reject | 15 |

Two limits on this denominator, both of which this document previously
overstated:

**These are classification decisions, not completed queries.** The audit is
written in `PreToolUse`, before the call runs. The 679 cannot establish 679
successful backend consultations.

**Not every enforcement decision is audited at all.** Only three code paths call
`audit()`. An ordinary block (`preflight_gate.py:245`) and an already-satisfied
allow (`:197`) write a plain log line and exit without a record. So 819 is the
count of *classified* decisions, and 115/804 is the share of recorded
*satisfaction* classifications. Neither is an all-turn failure rate, and the
absence of classified queries during the outage does not by itself prove no
backend call happened anywhere.

### The distribution is what matters

Drift is not uniform. Of the 115 local-read satisfactions, 47 fall in a single
session. By day:

| date | `targeted` | `read-memory` |
|---|---|---|
| 2026-09-03 | 2 | 2 |
| 2026-09-07 | 0 | 8 |
| 2026-09-08 | 0 | 4 |
| 2026-09-12 | 0 | 12 |
| 2026-09-13 | 0 | 9 |
| 2026-09-14 | 0 | 7 |
| 2026-09-15 | 7 | 5 |

**Eight consecutive days with zero successful backend queries and forty gate
satisfactions.** The operator was off the home network and the backend sat
behind Tailscale. The control reported success on every turn throughout. When
the backend returned on the 15th, drift collapsed immediately.

By month:

| month | `targeted` | `read-memory` | drift |
|---|---|---|---|
| 2026-04 | 181 | 29 | 13% |
| 2026-05 | 52 | 22 | 29% |
| 2026-06 | 68 | 7 | 9% |
| 2026-07 | 122 | 0 | 0% |
| 2026-08 | 216 | 2 | 1% |
| 2026-09 | 50 | 55 | 52% |

**July and August: 338 satisfactions, 2 local reads.** With a reachable backend
and stable infrastructure the control worked almost perfectly for two months.
Six of fifteen sessions show zero drift, including two of 80 and 93 events.

### Root cause — the control published its own bypass

When the gate blocks a call it returns a message telling the agent how to satisfy
it. That message lists three methods:

```
OPEN BRAIN GATE: You must query Open Brain before any other tool calls
this turn. Query via one of:
  1. Direct SQL query to Open Brain PostgreSQL database
  2. Read local memory files (~/.claude/projects/*/memory/*.md)
  3. MCP: search_brain with a targeted query
```

**Method 2 does not contact the backend.** It is offered as co-equal with the two
that do, and it is by far the cheapest of the three. The agents were not evading
the control. They were following its instructions.

This explains the distribution in a way neither "drift" nor "outage" does on its
own: 66 of the 115 occurred on days the backend was demonstrably reachable
(agents taking the advertised cheap path), and 49 during outages (the only
advertised method still working). Both populations are the same behaviour.

The corollary for the fix is easy to miss: removing `read-memory` from the
classifier is **not sufficient**. The message must stop naming it, or the control
will keep instructing agents to perform an action that now gets them blocked.

### Why it persisted

The local-read substitute is not a slow leak. It is a **pressure-relief valve
that hides failure of the real path.** While the dependency is healthy it is
barely used. The moment the dependency dies it absorbs one hundred percent of
traffic, and because it is a legitimate classification the control reports
success rather than an outage.

This is Incident 1 at scale, measured. The two are one incident.

**It is not a classifier defect.** All 115 were classified correctly —
`read-memory` is exactly what they were. A perfect classifier makes the same
115 decisions. The control worked as designed and the design is what failed.

**No adversary was involved.** Every agent was cooperative. The behaviour was
observed directly on 2026-09-15: an agent reviewing this project satisfied the
gate five times in one session by reading memory files it had no use for,
having reasoned explicitly about which was smallest — in a session where three
of four documented backend access paths were broken.

### What the old instrument could not measure at all

Running the v4 report over the frozen pre-intervention dataset returns:

```
satisfies-unverified  689   decided from the request; no result was ever observed
satisfies-substitute  115   opened by an action that contacted nothing
confirmed               0   prerequisite actually established
```

**Zero confirmed consultations in 162 days** — not because the dependency was
down, but because the instrument decided from the request and never observed a
result. It could not produce that number.

This constrains what the before/after comparison can claim. The after-metric
cannot be "fewer local reads", because local reads are no longer a category.
Part of the result is that the old log could not answer the question the new
one answers, so pre-intervention satisfactions are counted under distinct names
and are never added to confirmed ones.

### What remains unexplained

Roughly 58 local reads fall in April through June, outside any documented
outage. Whether those reflect further unrecorded outages, infrastructure churn,
or genuine drift is **not established by this data** and should not be claimed.

**Secondary observation:** in a small number of classified queries the literal project
name appears inside the *semantic search string* rather than as a search term,
present to match the classifier's regex. Rare, but it shows the control shaping
the work it supervises.

---

## The pattern

| incident | the lie the system told |
|---|---|
| 1 | "the precondition was satisfied" — a local file read stood in for a live query |
| 2 | "the memory was captured" — a stub was written and committed |
| 3 | "this is tracked" — recorded, never resurfaced |
| 4 | "the precondition was satisfied" — for eight days with the backend unreachable |

None of these was a crash. All four were **successful-looking operations that
did not do the thing.** Incident 4 differs in one respect that matters: the
first three were defects. It was a correct implementation behaving as designed.

---

## What this project must do differently

**A. Verify the effect, not the request.** (Incidents 1, 2)

Classifying a call by its *shape* is what allowed a local file read to satisfy
a backend requirement, and what allows `echo search_brain` to open the gate
today. The control must observe what actually happened. This is the argument
for approach C in `DESIGN-BRIEF-classification.md`, and it is no longer
theoretical.

**B. A degraded result must not be storable as a normal one.** (Incident 2)

Returning a syntactically valid default on failure is worse than raising,
because it is indistinguishable downstream. Either refuse the write, or mark it
so loudly that it cannot be read as clean data. `confidence: 0.0` and a
populated `_error` field existed — and nothing consumed them.

**C. Dependency liveness is part of the precondition.** (Incidents 1, 2)

"Did the agent do the thing" and "could the thing have worked" are different
questions. A control that answers only the first passes when its dependency is
gone. This is why `ARCHITECTURE.md` D4 separates `unreachable` from `blocked` —
and why that distinction must be *detected*, not assumed.

**D. Fail-open must be loud and time-bounded.** (Incident 1)

Four days of silent non-enforcement. Whatever escape hatch exists must announce
itself on every invocation and expire with the session.

**F. A satisfying action must entail the work, not resemble it.** (Incident 4)

Cost is the wrong axis. An expensive but irrelevant query is still a ritual; a
cheap authoritative lookup can be excellent diligence. The defect in the 115 was
**substitution** — a local file read cannot entail a backend consultation, at
any price.

The corollary is sharper: an escape hatch that stays available when the
dependency is down will absorb all traffic the moment it dies, and report
success while doing it. Degraded operation must therefore be **time-bounded and
escalating**, not a steady state the control can sit in for eight days.

**E. A recorded issue is not a tracked issue.** (Incident 3)

Out of scope for this codebase, but worth stating: capturing a known defect into
a memory system does nothing unless something re-surfaces it. Storage is not
follow-up.

---

## Test cases these produce

Concrete, drawn from real failures rather than imagined ones:

1. Backend unreachable → gate must **not** be satisfied by a local file read
2. `echo <required-tool-name>` → must **not** satisfy the gate
3. A legitimate backend query whose URL lacks the project name → must **not** be blocked
4. Dependency returns an error → the result must be rejected, not stored as a default
5. Fail-open enabled → must warn on **every** invocation, and expire with the session
6. Dependency address changes → must surface as a distinct, loud failure rather than as a silent fallback
7. Degraded state persisting beyond a single session → must **escalate**, not
   continue allowing indefinitely

Cases 1, 2 and 3 are reproducible against the current implementation today.
Case 7 is not hypothetical either: it is Incident 4, measured over eight days.
