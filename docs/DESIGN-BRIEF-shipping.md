# Design Brief — What Ships, In What Order

**Status:** open. Supersedes `DESIGN-BRIEF-premise.md`, which is answered.

This is the last design brief before implementation begins. Answer it as a
build decision, not an exploration.

---

## Read this first: what the operator actually wants

Two previous rounds produced a recurring hedge — *"it depends whether your goal
is permission or diligence quality"*, *"it depends whether you want adoption or
a claim."* Those hedges are now closed off. The goal is stated:

> **Both a working product people install, and publishable research.** Not one
> or the other. An answer that recommends picking one is not responsive to the
> question being asked.

Constraints that shape any acceptable answer:

- **Implementation starts immediately after this brief.** The deliverable is a
  build order, not a further exploration. "More analysis is needed" is only
  acceptable if you name the specific measurement and where it goes in the order.
- **Roughly a 12-week window**, solo maintainer, alongside other work.
- **The maintainer is the tool's own daily user** and will keep running it
  regardless of adoption. Assume one guaranteed install forever.
- **No publishing constraints.** Anything here can go public.
- Prior context the maintainer has rejected: strategy that defers building, and
  advice that trades the product away for the writeup.

---

## What is already settled — assume it, do not re-derive

From two independent frontier reviews and analysis of 809 audit records:

- Verification moves after execution. `PreToolUse` admits candidates; `PostToolUse`
  confirms completion on a **non-error** result (not non-empty — a targeted search
  correctly returning zero rows is diligence).
- The local-read substitute is removed, from the classifier **and** the block message.
- Gate state leaves `/tmp`; today the gated agent can open the gate by writing that
  file, or by deleting it, since a missing file fails open.
- Rules become data. Liveness leaves the hot path. Shell is admitted only by parsing.
- Enforcing on *claims* rather than tool calls was considered and rejected: the
  enforcement point is not reliably reachable, and a claim-judge's own cheapest
  satisfying action is to make no checkable claims.
- The defensible claim is "verifies the required dependency was consulted this
  turn," not "verifies the work was done properly."

Full detail in `ARCHITECTURE.md` (D1–D6) and `INCIDENTS.md` (Incidents 1–4).

---

## The finding this project produced

Measured on the maintainer's own control, in daily use, over 162 days and 809
enforcement decisions:

- 115 of 809 gate satisfactions were a local file read that contacted nothing.
- 84 of those re-read a file the harness **already loads into context**.
- 76 requested eight lines or fewer; 39 requested exactly three.
- During an eight-day dependency outage the substitute absorbed 100% of traffic
  and the control reported success on every turn, with no alert.
- **Root cause:** the message the gate returns on every block lists the
  ineffective local read as method 2 of 3 — co-equal with the two that work, and
  cheapest. The agents were following instructions.
- Every one of the 115 was classified **correctly**. A perfect classifier makes
  the same 115 decisions.

Neither reviewing model found this. The per-decision log did.

---

## The questions

### Q1 — Is `agent-preflight` the right vehicle for this finding?

The maintainer's own doubt, stated plainly: *"I don't think agent-preflight is
the point."*

The finding is about behavioural guardrails in general — every `CLAUDE.md` rule,
every "do X before Y" convention has the same shape. The tool is one instance
that happened to be instrumented.

Is the right move to (a) rebuild `agent-preflight` around the finding, (b) keep
`agent-preflight` as a product and publish the finding separately, or (c) something
else? If the finding deserves a different vehicle, name it and say what happens to
the existing repo.

### Q2 — Observe-only, blocking, or both?

Observe-only classifies and records but never blocks: near-zero adoption friction,
weaker claim, and every install becomes another instrumented control — which
addresses the finding's n=1 weakness. Blocking is the enforcement product.

Does shipping both tiers strengthen the project or dilute it into a tool that does
neither well? If both, which is the default on install, and does the other ship in
the 12-week window or after?

### Q3 — Design the re-surfacing. Do not warn about it.

`ARCHITECTURE.md` D6 now requires that the drift rate be pushed at the operator,
because a log nobody reads for 162 days is the project's own Incident 3. Previous
rounds produced one sentence and a warning about dashboards nobody reads. Neither
is a design.

Specify: what is surfaced, where, on what trigger, at what threshold, and what
makes it survive the maintainer becoming used to it. Note the constraint that the
memory backend is explicitly **not** a notification channel.

### Q4 — What must the telemetry record?

If observe-only ships and N people install it, the difference between a citable
cross-install finding and N noisy local logs is the record format.

Specify the per-decision schema: fields, what must never be captured (these logs
contain user prompts), what makes records comparable across different rules and
different harnesses, and what makes them aggregable without a server. Note that
the existing log was rich enough to overturn a diagnosis — identify which fields
did that work.

### Q5 — Build order.

Given 12 weeks, solo, and the requirement that both the product and the research
ship: what is built first, what is deliberately deferred, and what is the smallest
version that produces *both* an installable artifact and a defensible public claim?

Name the first thing to write on day one.

### Q6 — What should have been asked here and was not?

This is the fourth brief in this project. Each previous one produced a finding
that made the next necessary. If that pattern holds, the most valuable output of
this round may be the question nobody has posed yet.

State the one you would expect to matter next, and whether it can be answered
now from the artifacts supplied rather than after another round.

---

## What a good answer looks like

A decision with reasoning. Not an implementation, not a refactor plan, not code.

For each question: the call, the reasoning, the strongest case against, and what
evidence would change your mind. Where a question is genuinely underspecified,
state the assumption and decide anyway — the maintainer would rather correct a
stated assumption than receive a hedge.

Disagreement with the settled decisions above is welcome if the evidence supports
it. Say so explicitly rather than quietly designing around them.
