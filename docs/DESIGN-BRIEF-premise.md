# Design Brief — Is a Preventive Gate the Right Instrument?

**Status:** open design decision. Supersedes the question asked in
`DESIGN-BRIEF-classification.md`, which is now considered answered.

This brief asks a different question from the last one. The previous brief asked
*how* to classify tool calls correctly. This one asks whether classifying tool
calls is the right thing to be doing at all.

---

## What the previous round settled

Two frontier models reviewed `DESIGN-BRIEF-classification.md` independently,
from identical inputs, and converged:

- The `satisfied` flag must not be written before execution. Verification
  belongs after the call completes (approach C).
- Narrowing the satisfying surface (approach B) is right as a stance, but its
  literal form contradicts an existing acceptance test.
- Rules-as-data (D) is required and orthogonal.
- A liveness probe (E) does not belong in the hot path of every tool call.

Three further defects were found and confirmed against the code:

- The gate's state is a file in `/tmp` named for the session. The gated agent
  has shell access and knows its own session id, so it can open the gate by
  writing to that file. A missing file fails open, so deleting it works too.
- Structured classification is not "nearly correct" as the previous brief
  claimed: tool identity is substring-matched, `WebFetch` never inspects the
  query at all, and `Glob` satisfies a read requirement without reading
  anything.
- "Did it return data" is the wrong completion test. A targeted search that
  correctly returns zero results is diligence, and must count.

**None of that is in dispute and none of it needs re-deriving.** Assume it.

---

## The new evidence

The gate writes an audit record for every decision. Nobody had read the log.

It covers **808 decisions, 15 sessions, 162 days**. See Incident 4 in
`INCIDENTS.md` for the full breakdown. The short version:

- 115 of 808 gate satisfactions were a local file read, not a backend query.
- 84 of those read `MEMORY.md` — a file the harness **already loads into the
  agent's context** at session start.
- 76 of the 115 requested eight lines or fewer. Thirty-nine requested exactly
  three lines.
- No adversary was present in any of them. Every agent was cooperative and
  trying to comply.

The same behaviour was then observed live: the agent reviewing this project
satisfied the gate five times in one session by reading memory files it had no
use for, having reasoned explicitly about which was smallest.

**Every one of those 115 events was classified correctly.** A perfect
classifier would have made the identical decision each time, because
`read-memory` is exactly what they were.

---

## Why this changes the question

The previous brief's failures were defects — an implementation that could be
fixed. This one is not. The control did what it was designed to do, 115 times,
and the design is what produced the outcome.

The observed property is that **the cheapest action satisfying the control cost
less than the work the control exists to require.** A compliant agent
minimising effort converges on that action. No improvement to classification
accuracy addresses it, because accuracy was never the problem.

Whether that observation generalises — to this control, to guardrails of this
shape, or to agent guardrails as a class — is the question this brief poses.

---

## Candidate directions

Presented without preference. At least one is probably wrong and the reviewer
is expected to say which.

**1. Proceed as planned.** Implement the B+C design from round one plus the
three fixes. Position: Incident 4 describes an acceptable known limit of any
behavioural control, the log already surfaces it, and a better gate is still
better. Changing instruments over this is an overreaction.

**2. Move the check from tool calls to claims.** The project does not actually
care whether a `Bash` call happened. It cares whether the agent asserted a fact
it had not verified. Enforce at the end of the turn, on the assertion, rather
than as a precondition on every tool call. Most turns then need no gate.

**3. Invert the product — the log is the deliverable.** The gate becomes an
instrument for generating compliance evidence rather than the thing being
shipped. What ships is the measurement: detection that a guardrail is being
satisfied without the work being done. Prevention is dropped as a claim.

**4. Remove the adaptive surface.** Make the satisfying action one the agent
cannot perform cheaply or at all — for example, the harness performs the query
itself at turn start and the agent never has the option of substituting
something. The control stops depending on agent choice.

**5. Retire the control and fix the ergonomics instead.** During the session
that produced Incident 4, three of four documented backend access paths were
broken. Agents route around friction, and much of the observed drift may be
that. Inject backend context automatically and the gate becomes unnecessary.

---

## Questions for the reviewer

1. Does Incident 4 invalidate the preventive framing, or is it a known limit
   that any behavioural control carries and that does not justify changing
   instruments?
2. Is "the cheapest satisfying action wins" a general property of agent
   guardrails, or an artifact of this control's particular shape?
3. Can a control exist whose satisfying action is **not** cheaper than the work
   it stands for? If yes, what does it look like. If no, what follows for this
   project.
4. If enforcement moves from tool calls to claims (direction 2), what breaks —
   and is the enforcement point even reachable in a tool-hook architecture?
5. What honest claim can this project make in a README? Specifically: is
   "verifies the work was done" defensible, or is the defensible claim weaker?
6. How much of the 115 is compliance drift and how much is broken ergonomics?
   Does the distinction change the answer?

---

## Constraint

Whatever replaces this must keep the property that makes the project
distinctive: it addresses **whether the work was done properly**, not merely
whether a call was permitted. Collapsing to an allowlist makes this the
eleventh identical project in the space.

Note that an allowlist cannot detect its own rubber-stamping, and neither can a
classifier. If that capability is the distinctive property, say so.

---

## What a good answer looks like

A decision with reasoning. Not an implementation, not a refactor plan.

State which direction you would take and why, the strongest case against it,
and what evidence would change your mind. If you believe the previous round's
design should proceed unchanged and Incident 4 is being overweighted, say that
plainly — it is a live possibility and the operator would rather hear it now
than after a rewrite.

One thing no reviewer can settle from these artifacts: whether the goal is
**permission** or **diligence quality**. Say which one your answer assumes.
