#!/usr/bin/env python3
"""Rollback — undo objects created in this session using the audit log.

Reads changelog.jsonl (written by state.py on every create) and suspends the
created campaigns/groups/ads. Suspend, not delete: reversible and safe. Use when
a build went wrong and you want to stop spend immediately.

Examples:
    python rollback.py list                 # show recent create actions
    python rollback.py undo --last 1        # suspend objects from last create
    python rollback.py undo --since 2026-06-13T00:00:00
"""

import argparse
import sys

from direct_api import DirectClient, load_config
from state import State

ACTION_TO_SERVICE = {
    "create_campaign": "campaigns",
    "create_group": "adgroups",
    "create_ads": "ads",
}


def _records(st, last=None, since=None):
    recs = [r for r in st.history(1000) if r["action"] in ACTION_TO_SERVICE]
    if since:
        recs = [r for r in recs if r["ts"] >= since]
    if last:
        recs = recs[-last:]
    return recs


def undo(client, recs):
    results = []
    for r in recs:
        service = ACTION_TO_SERVICE[r["action"]]
        ids = [i for i in (r["response"].get("ids") or []) if i not in (None, "<new>")]
        if not ids:
            continue
        res = client.call(service, "suspend",
                          {"SelectionCriteria": {"Ids": [int(i) for i in ids]}})
        results.append({"action": r["action"], "ids": ids, "result": res})
    return results


def main(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    u = sub.add_parser("undo")
    u.add_argument("--last", type=int)
    u.add_argument("--since", help="ISO timestamp")
    u.add_argument("--apply", action="store_true", help="actually suspend (default dry-run)")
    args = p.parse_args(argv)

    st = State()
    if args.cmd == "list":
        for r in _records(st):
            print(f"{r['ts']}  {r['action']:<18} ids={r['response'].get('ids')}")
        return 0
    if args.cmd == "undo":
        recs = _records(st, last=args.last, since=args.since)
        if not recs:
            print("nothing to roll back")
            return 0
        print(f"would suspend objects from {len(recs)} create action(s):")
        for r in recs:
            print(f"  {r['action']}: {r['response'].get('ids')}")
        if not args.apply:
            print("\n(dry-run — add --apply to suspend)")
            return 0
        client = DirectClient(load_config())
        for res in undo(client, recs):
            print("suspended:", res["action"], res["ids"])
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
