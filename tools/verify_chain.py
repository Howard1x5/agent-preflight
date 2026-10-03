#!/usr/bin/env python3
"""Verify the hash chain over decisions.jsonl.

  python3 tools/verify_chain.py              # the install's own record file
  python3 tools/verify_chain.py --records F
  python3 tools/verify_chain.py --head       # print only the head hash, for anchoring

Each chained record carries the sha256 of the line before it. A mismatch means
a record was edited, inserted or removed at that point. Records written before
chaining existed form an unchained prefix and are reported as unverified, not
as valid.

What this cannot see, stated rather than implied: removal or editing of the
last records, and a whole-file rewrite with every hash recomputed. Both are
detectable only against a head hash stored where the agent cannot rewrite it.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import records  # noqa: E402


def verify(raw_lines):
    """Returns dict(unchained, chained, breaks=[(line_no, reason)], head)."""
    unchained, chained, breaks = 0, 0, []
    started = False
    for i, raw in enumerate(raw_lines):
        try:
            rec = json.loads(raw)
        except ValueError:
            breaks.append((i + 1, "unparsable line"))
            continue
        link = rec.get("chain")
        if link is None:
            if started:
                breaks.append((i + 1, "unchained record after the chain began"))
            else:
                unchained += 1
            continue
        started = True
        chained += 1
        expected = records.line_hash(raw_lines[i - 1]) if i > 0 else records.GENESIS
        if link != expected:
            breaks.append((i + 1, "chain does not match the previous line"))
    head = records.line_hash(raw_lines[-1]) if raw_lines else None
    return {"unchained": unchained, "chained": chained, "breaks": breaks, "head": head}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", help="record file (default: the install's own)")
    ap.add_argument("--head", action="store_true", help="print only the head hash")
    args = ap.parse_args()
    path = Path(args.records) if args.records else records.RECORD_FILE
    try:
        raw = [ln for ln in path.read_bytes().split(b"\n") if ln.strip()]
    except OSError as e:
        print(f"verify_chain: cannot read {path}: {e}", file=sys.stderr)
        return 2
    r = verify(raw)
    if args.head:
        print(r["head"] or "")
        return 0
    print(f"records    {len(raw)}")
    print(f"chained    {r['chained']}")
    if r["unchained"]:
        print(f"unchained  {r['unchained']}   written before chaining; unverified, not valid")
    if r["breaks"]:
        print(f"BREAKS     {len(r['breaks'])}")
        for line_no, why in r["breaks"][:20]:
            print(f"  line {line_no}: {why}")
    else:
        print("breaks     0")
    print(f"head       {r['head']}")
    print("\nNot detectable here: removal or edits of the last records, or a full")
    print("rewrite with recomputed hashes. Compare the head against an anchored copy.")
    return 1 if r["breaks"] else 0


if __name__ == "__main__":
    sys.exit(main())
