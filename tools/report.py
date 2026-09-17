#!/usr/bin/env python3
"""Summarise preflight decision records, and emit a shareable export bundle.

Two outputs, deliberately separate:

  --summary  what happened here, for the operator
  --export   counts only, for combining with other installs

The export is the answer to "what makes N installs a finding rather than N
noisy logs". Records are already content-free, but an export goes further and
ships no per-decision rows at all -- only counts grouped by the dimensions that
make installs comparable: rule contract, mode, message version and outcome.
Two installs whose `rule_config_hash` differs are not measuring the same thing
and must not be pooled, which is why that hash is a grouping key rather than
metadata.

Sharing is manual. Nothing here uploads anything.

  python3 tools/report.py --summary
  python3 tools/report.py --export bundle.json
  python3 tools/report.py --summary --since 2026-09-17
"""

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import records  # noqa: E402

BUNDLE_VERSION = 1

# Outcomes that mean the prerequisite was actually established.
CONFIRMING = {"satisfies"}
# Outcomes that mean it was not, while work proceeded anyway.
UNENFORCED = {"degraded-allow"}

# v1 (pre-intervention) records carry classification+verdict and no outcome,
# because that version decided from the request and never observed a result.
# They are normalised to DISTINCT outcome names, never to v2's "satisfies".
#
# This is the point, not a nuisance: v1's satisfactions were unverified by
# construction, so folding them into a confirmed-consultation count would
# manufacture a comparison the data cannot support. The honest before/after
# result includes "the old instrument could not produce this number at all".
V1_OUTCOME = {
    ("targeted", "ALLOW"): "satisfies-unverified",
    ("read-memory", "ALLOW"): "satisfies-substitute",
    ("capture-only", "ALLOW-NO-FLIP"): "permitted-non-qualifying",
    ("generic", "REJECT"): "blocked",
}


def load(path=None, since=None):
    src = Path(path) if path else records.RECORD_FILE
    rows, unparsable = [], 0
    try:
        text = src.read_text()
    except OSError:
        return [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            unparsable += 1
            continue
        if since and (r.get("ts") or "") < since:
            continue
        rows.append(normalise(r))
    return rows, unparsable


def normalise(r):
    """Give a v1 record a v2-shaped outcome, without claiming v2 semantics."""
    if r.get("schema_version", 1) >= 2 or r.get("outcome"):
        return r
    r = dict(r)
    key = (r.get("classification"), r.get("verdict"))
    r["outcome"] = V1_OUTCOME.get(key, "unclassified")
    r.setdefault("mode", "enforce")
    r.setdefault("rule_id", "consult-backend")
    r.setdefault("rule_kind", "consult-backend")
    r.setdefault("rule_config_hash", "v1-unknown")
    r.setdefault("message_version", "v1-unknown")
    return r


def summarise(rows, unparsable):
    out = collections.Counter(r.get("outcome") for r in rows)
    confirmed = sum(out[o] for o in CONFIRMING)
    unenforced = sum(out[o] for o in UNENFORCED)
    unconfirmed = out.get("unconfirmed", 0)
    admitted = out.get("candidate-admitted", 0)
    incomplete = sum(1 for r in rows if r.get("integrity"))

    # An admitted candidate with no confirmation is a turn that proceeded on a
    # request rather than a result -- the v2 failure mode, still visible.
    dangling = max(admitted - confirmed - unconfirmed, 0)

    schemas = collections.Counter(r.get("schema_version", 1) for r in rows)
    return {
        "schemas": dict(schemas),
        "substitutions": out.get("satisfies-substitute", 0),
        "unverified": out.get("satisfies-unverified", 0),
        "records": len(rows),
        "sessions": len({r.get("session_hash") for r in rows}),
        "by_outcome": dict(out),
        "confirmed": confirmed,
        "unconfirmed": unconfirmed,
        "admitted": admitted,
        "dangling_candidates": dangling,
        "unenforced_allows": unenforced,
        "incomplete_records": incomplete,
        "unparsable_lines": unparsable,
        "modes": dict(collections.Counter(r.get("mode") for r in rows)),
        "rules": dict(collections.Counter(r.get("rule_id") for r in rows)),
        "message_versions": dict(collections.Counter(r.get("message_version") for r in rows)),
    }


def print_summary(s):
    if not s["records"]:
        print("no records found")
        return
    print(f"records            {s['records']}   across {s['sessions']} session(s)")
    if s["unverified"] or s["substitutions"]:
        print(f"[pre-intervention records present — schema {sorted(s['schemas'])}]")
        print(f"satisfies-unverified {s['unverified']}   decided from the request; "
              f"no result was ever observed")
        print(f"satisfies-substitute {s['substitutions']}   opened by an action that "
              f"contacted nothing")
    print(f"confirmed          {s['confirmed']}   prerequisite actually established")
    print(f"admitted           {s['admitted']}   allowed to run pending confirmation")
    print(f"unconfirmed        {s['unconfirmed']}   ran, did not establish the work")
    if s["dangling_candidates"]:
        print(f"DANGLING           {s['dangling_candidates']}   admitted, never resolved either way")
    if s["unenforced_allows"]:
        print(f"UNENFORCED ALLOWS  {s['unenforced_allows']}   proceeded while degraded")
    if s["incomplete_records"]:
        print(f"INCOMPLETE         {s['incomplete_records']}   records flagged as gaps")
    if s["unparsable_lines"]:
        print(f"UNPARSABLE         {s['unparsable_lines']}   lines could not be read")
    print()
    for label, key in (("mode", "modes"), ("rule", "rules"),
                       ("message", "message_versions")):
        vals = ", ".join(f"{k}={v}" for k, v in sorted(s[key].items(), key=str))
        print(f"  {label:8} {vals}")
    print()
    print("  by outcome:")
    for k, v in sorted(s["by_outcome"].items(), key=lambda kv: -kv[1]):
        print(f"    {str(k):24} {v}")
    if s["dangling_candidates"] or s["unenforced_allows"] or s["incomplete_records"]:
        print()
        print("  Attention: a dangling candidate is a turn that proceeded on an")
        print("  inspected request rather than a confirmed result. That is the")
        print("  failure this version exists to remove, so it should trend to 0.")
    if len(s["schemas"]) > 1:
        print()
        print("  NOTE: this window mixes schema versions. Pre-intervention")
        print("  satisfactions were never verified against a result, so they are")
        print("  counted separately and must not be added to confirmed.")


def export_bundle(rows, unparsable):
    """Counts only. No per-decision rows leave the machine.

    Grouped by (rule_config_hash, mode, message_version, outcome) so that
    installs running different rule contracts or different message text are
    never silently pooled -- message text is what caused Incident 4, so
    behaviour under different text is not the same measurement.
    """
    grouped = collections.Counter()
    for r in rows:
        grouped[(r.get("rule_kind"), r.get("rule_config_hash"), r.get("mode"),
                 r.get("message_version"), r.get("outcome"))] += 1
    return {
        "bundle_version": BUNDLE_VERSION,
        "schema_version": records.SCHEMA_VERSION,
        "install_sessions": len({r.get("session_hash") for r in rows}),
        "window": {"first": min((r.get("ts") for r in rows), default=None),
                   "last": max((r.get("ts") for r in rows), default=None)},
        "completeness": {"records": len(rows), "unparsable_lines": unparsable,
                         "incomplete_records": sum(1 for r in rows if r.get("integrity"))},
        "counts": [
            {"rule_kind": k[0], "rule_config_hash": k[1], "mode": k[2],
             "message_version": k[3], "outcome": k[4], "n": v}
            for k, v in sorted(grouped.items(), key=str)
        ],
        "note": ("Counts only; no per-decision rows. Do not pool groups whose "
                 "rule_config_hash or message_version differ -- they are not "
                 "measuring the same thing."),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", help="record file (default: the install's own)")
    ap.add_argument("--since", help="ISO instant; earlier records are ignored")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--export", metavar="PATH", help="write a shareable counts-only bundle")
    args = ap.parse_args()

    rows, unparsable = load(args.records, args.since)

    if args.export:
        bundle = export_bundle(rows, unparsable)
        Path(args.export).write_text(json.dumps(bundle, indent=2, sort_keys=True))
        print(f"wrote {args.export} — {len(bundle['counts'])} count rows, "
              f"no per-decision data")
        return

    print_summary(summarise(rows, unparsable))


if __name__ == "__main__":
    main()
