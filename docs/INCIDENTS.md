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

## The pattern

| incident | the lie the system told |
|---|---|
| 1 | "the precondition was satisfied" — a local file read stood in for a live query |
| 2 | "the memory was captured" — a stub was written and committed |
| 3 | "this is tracked" — recorded, never resurfaced |

None of these was a crash. All three were **successful-looking operations that
did not do the thing.**

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

Cases 1, 2 and 3 are reproducible against the current implementation today.
