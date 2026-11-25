"""Join rehearsal decisions to Claude Code transcripts to get a per-TURN view.

The decision log has no turn id, so turn boundaries come from the transcripts:
a turn starts at each real user prompt (not a tool result, not a hook/system
injection). Prompt text is used locally for classification only and is never
written out - output is counts plus prompt LENGTH.

Usage: python3 -I tools/turn_join.py [OUT.jsonl]   (prints summary; optional per-turn counts file)
Rebuilds turns for records written before schema 3 (no turn_seq). Records from
schema 3 on carry turn_seq and do not need this.
"""
import bisect, glob, json, os, sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import records  # noqa: E402

STATE = os.path.expanduser("~/.claude/state/agent-preflight")
OUT = sys.argv[1] if len(sys.argv) > 1 else None


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def real_prompt(o):
    if o.get("type") != "user" or o.get("isMeta") or o.get("isCompactSummary"):
        return None
    c = (o.get("message") or {}).get("content")
    if isinstance(c, list):
        if any(isinstance(p, dict) and p.get("type") == "tool_result" for p in c):
            return None
        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    if not isinstance(c, str) or not c.strip():
        return None
    if c.lstrip().startswith(("<local-command", "<command-name>", "<system-reminder>", "<task-notification", "Caveat:")):
        return None
    return c


secret = records.install_secret()
recs = [json.loads(l) for l in open(os.path.join(STATE, "decisions.jsonl")) if l.strip()]
reh = [r for r in recs if r.get("mode") == "rehearsal"]
by_sh = {}
for r in reh:
    by_sh.setdefault(r["session_hash"], []).append(r)

turns = []
matched = set()
for path in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
    sid = os.path.basename(path)[:-6]
    sh = records.session_hash(sid, secret)
    if sh not in by_sh:
        continue
    matched.add(sh)
    starts = []
    for line in open(path, errors="replace"):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        p = real_prompt(o)
        if p is not None and o.get("timestamp"):
            starts.append((ts(o["timestamp"]), p))
    starts.sort()
    keys = [s[0] for s in starts]
    buckets = {}
    for r in by_sh[sh]:
        i = bisect.bisect_right(keys, ts(r["ts"])) - 1
        if i >= 0:
            buckets.setdefault(i, []).append(r)
    for i, (t0, prompt) in enumerate(starts):
        rs = buckets.get(i, [])
        oc = Counter(r["outcome"] for r in rs if r["phase"] == "pre")
        first = next((r for r in rs if r["phase"] == "pre"), None)
        turns.append({
            "session": sh, "t": t0, "prompt_len": len(prompt),
            "_prompt": prompt,  # in memory only
            "tool_calls": sum(oc.values()),
            "would_block": oc.get("blocked", 0),
            "satisfied": any(r["outcome"] == "satisfies" for r in rs),
            "first_call_blocked": bool(first and first["outcome"] == "blocked"),
            "first_tool": first["tool_kind"] if first else None,
        })

print(f"sessions in log: {len(by_sh)}, matched to transcripts: {len(matched)}")
active = [t for t in turns if t["tool_calls"]]
print(f"turns: {len(turns)} total, {len(active)} with tool calls")
nb = [t for t in active if t["would_block"]]
print(f"turns that would be blocked at least once: {len(nb)} ({100*len(nb)/max(1,len(active)):.0f}% of active turns)")
print(f"turns where the agent satisfied the rule unprompted: {sum(t['satisfied'] for t in active)}")
print(f"would-blocks per blocked turn: median {sorted(t['would_block'] for t in nb)[len(nb)//2] if nb else 0}, max {max((t['would_block'] for t in nb), default=0)}")
print("first tool kind on blocked turns:", Counter(t["first_tool"] for t in nb).most_common())
if OUT:
  with open(OUT, "w") as f:
    for t in turns:
      f.write(json.dumps({k: v for k, v in t.items() if not k.startswith("_")}) + "\n")
  print("wrote", OUT, "(no prompt text)")
