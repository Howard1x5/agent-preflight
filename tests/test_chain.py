"""The record chain: editing, inserting or deleting a record breaks it, and
parallel writers must not fork it."""

import json
import multiprocessing
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "tools"))
import records  # noqa: E402
import verify_chain  # noqa: E402


def lines(p):
    return [ln for ln in p.read_bytes().split(b"\n") if ln.strip()]


def write_n(p, n, tag="r"):
    for i in range(n):
        assert records.write({"outcome": "blocked", "i": f"{tag}{i}"}, path=p)


def test_an_untouched_chain_verifies(tmp_path):
    p = tmp_path / "d.jsonl"
    write_n(p, 5)
    r = verify_chain.verify(lines(p))
    assert r["chained"] == 5 and not r["breaks"]
    assert json.loads(lines(p)[0])["chain"] == records.GENESIS


def test_editing_a_record_breaks_the_chain_at_the_next_one(tmp_path):
    p = tmp_path / "d.jsonl"
    write_n(p, 5)
    ls = lines(p)
    ls[2] = ls[2].replace(b'"blocked"', b'"satisfies"')
    r = verify_chain.verify(ls)
    assert [b[0] for b in r["breaks"]] == [4]


def test_deleting_a_middle_record_is_detected(tmp_path):
    p = tmp_path / "d.jsonl"
    write_n(p, 5)
    ls = lines(p)
    del ls[2]
    assert verify_chain.verify(ls)["breaks"]


def test_inserting_a_forged_record_is_detected(tmp_path):
    p = tmp_path / "d.jsonl"
    write_n(p, 4)
    ls = lines(p)
    forged = json.dumps({"outcome": "satisfies", "chain": records.line_hash(ls[1])}).encode()
    ls.insert(2, forged)
    assert verify_chain.verify(ls)["breaks"], "the record after the forgery must break"


def test_pre_chain_records_are_reported_unverified_not_valid(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text(json.dumps({"outcome": "blocked"}) + "\n" + json.dumps({"outcome": "blocked"}) + "\n")
    write_n(p, 3)
    r = verify_chain.verify(lines(p))
    assert r["unchained"] == 2 and r["chained"] == 3 and not r["breaks"]


def test_unchained_record_after_the_chain_began_is_a_break(tmp_path):
    p = tmp_path / "d.jsonl"
    write_n(p, 3)
    with p.open("a") as f:
        f.write(json.dumps({"outcome": "satisfies"}) + "\n")
    assert verify_chain.verify(lines(p))["breaks"]


def test_truncating_the_tail_is_not_detectable_without_an_anchor(tmp_path):
    """Pinned as a known limit, so nobody later believes the chain alone covers it."""
    p = tmp_path / "d.jsonl"
    write_n(p, 5)
    full = verify_chain.verify(lines(p))
    cut = verify_chain.verify(lines(p)[:3])
    assert not cut["breaks"] and cut["head"] != full["head"]


def _writer(args):
    p, tag = args
    write_n(Path(p), 25, tag)


def test_parallel_writers_do_not_fork_the_chain(tmp_path):
    p = tmp_path / "d.jsonl"
    with multiprocessing.Pool(6) as pool:
        pool.map(_writer, [(str(p), f"w{k}-") for k in range(6)])
    r = verify_chain.verify(lines(p))
    assert r["chained"] == 150 and not r["breaks"]
