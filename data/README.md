# Datasets

## `before-2026-09-16.jsonl`

The pre-intervention audit record for Incident 4: 820 classified enforcement
decisions across 15 sessions, 2026-04-06 to 2026-09-17, from the maintainer's
own daily use of the gate described in `hooks/preflight_gate.py`.

Frozen before the Incident 4 root-cause fix (removing the local-read substitute
from the classifier and from the block message), so it is the "before" arm of a
before/after intervention on one control.

### It contains no content

Derived from the raw log by `tools/freeze_audit.py`. The raw log contains user
prompt text, tool arguments, file paths, hostnames and IP addresses. **None of
that is in this file.** Every string value in all 820 records comes from a
closed enum:

```
ALLOW  REJECT  generic  read-memory  targeted  shell  read
memory-index  memory-note  None
```

Everything else is an integer, a boolean, one ratio, a timestamp, or a salted
session hash. The salt is random per run and never stored, so session
identifiers cannot be recovered.

### Fields

| field | meaning |
|---|---|
| `schema_version` | record format version |
| `ts` | decision timestamp |
| `session_hash` | salted, truncated — groups records, identifies nothing |
| `tool_kind` | `shell` / `read` / `write` / `fetch` / `mcp` / `other` |
| `classification` | what the gate decided the call was |
| `verdict` | `ALLOW` / `REJECT` / `ALLOW-NO-FLIP` |
| `features.read_span_lines` | lines requested, for reads |
| `features.read_offset_present` | whether a partial-read offset was given |
| `features.path_kind` | `memory-index` / `memory-note` / `other` — category, never a path |
| `features.query_len` | characters in the tool input |
| `features.has_filter` | a filtering keyword was present |
| `features.term_count` | distinct content words in the input |
| `features.query_contains_rule_identifier` | the rule's own name appeared in the query text |
| `features.prompt_len` | characters in the turn's prompt |
| `features.prompt_query_term_overlap` | share of prompt terms appearing in the query |

### Known limits

- Records are written in `PreToolUse`, **before execution**. A `targeted`
  classification does not establish that the query ran or returned anything.
- Not every enforcement decision is recorded. Ordinary blocks and
  already-satisfied allows exit without writing. 820 is the count of
  *classified* decisions, not of all turns.
- One control, one operator. This is a case study, not a prevalence estimate.
