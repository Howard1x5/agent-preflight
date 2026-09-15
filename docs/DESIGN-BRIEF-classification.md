# Design Brief — Replacing Pattern-Based Classification

**Status:** open design decision (D5 in `ARCHITECTURE.md`). Not yet settled.

This brief exists to be handed to a reviewer. It states the problem, the
evidence, the candidate approaches and their trade-offs. It deliberately does
**not** pick a winner.

---

## The job classification does

Given a single tool call, decide which of four things it is:

| classification | effect on the gate |
|---|---|
| `targeted` | satisfies — real query with real filtering |
| `generic` | blocked — a query that returns whatever is recent |
| `capture-only` | permitted, does not satisfy — a write, not a read |
| `read-memory` | satisfies — local memory file read |
| `None` | not relevant to the gate |

Current implementation: regex matching against the raw command string, URL, or
tool name (`preflight_gate.py:124-176`).

---

## Two demonstrated failures

### 1. The gate is spoofable by a command that does nothing

```python
# preflight_gate.py:136-146
if tool_name == "Bash":
    cmd = tool_input.get("command", "")
    if not OB_REGEX.search(cmd):
        return None
    if FILTER_REGEX.search(cmd):
        return "targeted"
    if "search_brain" in cmd:      # <-- substring match on the command text
        return "targeted"
```

`echo search_brain` satisfies the gate.

So does `echo "open-brain WHERE"`. No query runs, nothing is retrieved, no
backend is contacted. The control verifies **the shape of a command string**,
not that any work occurred.

A gate that can be opened by printing the right words is not a control. It is a
convention with extra steps.

### 2. The gate has been satisfied for days while the backend was unreachable

```python
# preflight_gate.py:167-174
if tool_name in ("Read", "Grep", "Glob"):
    if re.search(r"memory/.*\.md|open-brain|MEMORY\.md", path, re.IGNORECASE):
        return "read-memory"          # satisfies the gate
```

Observed directly over a multi-day session: the memory backend sits behind
Tailscale and was unreachable the entire time (host logged out, network
changed). **Every turn's gate was satisfied by reading a local `MEMORY.md`
file.** The backend was never consulted and the gate never once objected.

This is precisely the silent-degradation failure the project exists to prevent,
present in the current implementation, and it went unnoticed for days.

### The common root cause

Both failures share a cause: **classification inspects the request, never the
result.** It is pure pre-execution pattern matching with no verification that
the work actually happened or returned anything.

---

## The structural reframe

Tool calls arrive in two forms, and they are not the same problem:

**Structured** — MCP invocations, `Read`, `Grep`, `Glob`. Arrive as typed
fields (`tool_name` plus a `tool_input` dict). Classification here is field
inspection. It is already nearly correct and is not the hard part.

**Unstructured** — `Bash`. Arrives as an opaque string that may contain
pipelines, subshells, heredocs, SSH invocations wrapping remote Python wrapping
SQL. This is where every failure above lives.

Treating both with one regex soup is the actual design error.

---

## Candidate approaches

### A. Parse instead of match

Use a real shell parser (`bashlex`) for commands, and a real SQL parser
(`sqlglot`) for embedded SQL. Inspect the AST for genuine filter predicates
rather than string-matching `WHERE`.

- **Fixes:** `echo "WHERE"` no longer matches, because `echo` is not a query
- **Does not fix:** nothing is verified to have *run*
- **Cost:** dependencies, and shell is genuinely hard to parse
- **Risk:** SSH-wrapping-python-wrapping-SQL defeats any single parser

### B. Narrow the satisfying surface

Raw `Bash` can never satisfy the gate. Qualifying actions must occur through a
structured, identifiable channel — an MCP tool, or a purpose-built wrapper
command that the gate can recognise unambiguously.

- **Fixes:** spoofing, entirely. There is nothing to spoof.
- **Cost:** convenience. Ad-hoc shell queries stop counting.
- **Observation:** this is the security-correct answer. A control that attempts
  to understand arbitrary shell is fighting an unbounded problem it cannot win.

### C. Verify the result, not the request

Move (or duplicate) the decision to `PostToolUse`: did the call return data?
Did it contact the backend? Correlate outcome with the pre-call classification.

- **Fixes:** both failures — a command that does nothing produces nothing
- **Cost:** cannot block *before* the action, so it becomes detective rather
  than preventive control. May need both.

### D. Rules as data, not code

Move patterns into a config file; ship rule packs. Orthogonal to A–C — it fixes
**portability**, not correctness. Probably required regardless, since hardcoded
host patterns are why this is not usable by anyone else.

### E. Liveness check on the dependency

Independently confirm the backend is reachable before treating any call as
satisfying. Directly addresses failure 2.

- **Interacts with D4:** unreachable must trigger the degraded state, not a
  block.

---

## Questions for the reviewer

1. **Is B the right answer** — should a control refuse to interpret arbitrary
   shell and instead require qualifying actions through a narrow channel? What
   is actually lost?
2. If not B, can A be made robust enough to matter, given SSH-wrapped remote
   execution defeats local parsing?
3. Is preventive control achievable at all here, or is the honest answer a
   **preventive gate on structured calls plus detective verification on
   everything else** (B + C)?
4. Does E belong in the gate, or is a liveness probe a separate concern that
   should not sit in the hot path of every tool call?
5. What breaks first when this generalises past one memory backend to
   read-before-edit or test-before-claiming-success?

---

## Constraint

Whatever replaces this must keep the property that makes the project
distinctive: it enforces **whether the work was done properly**, not merely
whether a call was permitted. Collapsing to an allowlist would make this the
eleventh identical project in the space.
